from __future__ import annotations

import logging
import queue
import random
import signal
import threading
import time
from typing import Any

from .client import AgentClient, AgentHTTPError
from .collectors import MetricsCollector
from .config import AgentConfig
from .executor import execute_command

logger = logging.getLogger("runner_agent")

OUTPUT_BATCH_SECONDS = 0.15
OUTPUT_BATCH_CHARS = 16 * 1024


class _OutputUploader:
    """Batch command output chunks onto a background upload thread.

    Streaming is best-effort: a failed upload is logged and dropped so it can
    never prevent the final result from being posted.
    """

    def __init__(self, client: AgentClient, command_id: str):
        self.client = client
        self.command_id = command_id
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def push(self, stream: str, text: str) -> None:
        if text:
            self._queue.put((stream, text))

    def close(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._queue.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        buffers = {"stdout": [], "stderr": []}
        sizes = {"stdout": 0, "stderr": 0}
        seqs = {"stdout": 0, "stderr": 0}
        last_flush = time.monotonic()

        def flush(stream: str | None = None) -> None:
            nonlocal last_flush
            streams = [stream] if stream else ["stdout", "stderr"]
            for name in streams:
                text = "".join(buffers[name])
                if not text:
                    continue
                buffers[name].clear()
                sizes[name] = 0
                seqs[name] += 1
                try:
                    self.client.send_output(self.command_id, name, seqs[name], text)
                except Exception as exc:
                    logger.warning(
                        "Dropping command %s %s output chunk %s: %s",
                        self.command_id,
                        name,
                        seqs[name],
                        exc,
                    )
            last_flush = time.monotonic()

        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if time.monotonic() - last_flush >= OUTPUT_BATCH_SECONDS:
                    flush()
                if self._stop.is_set() and self._queue.empty():
                    flush()
                    return
                continue
            if item is None:
                flush()
                return
            stream, text = item
            buffers[stream].append(text)
            sizes[stream] += len(text)
            if sizes[stream] >= OUTPUT_BATCH_CHARS:
                flush(stream)
            elif time.monotonic() - last_flush >= OUTPUT_BATCH_SECONDS:
                flush()
        # Any chunks still buffered are flushed by the sentinel or idleness path.


class AgentRunner:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.client = AgentClient(config)
        self.collector = MetricsCollector(config)
        self.remote_policy: dict[str, Any] = {}
        self.stop_event = threading.Event()
        self._installed_signals = False
        self._update_notice: str | None = None
        self._local_max_output_bytes = config.max_output_bytes
        self._local_allow_self_update = config.allow_self_update

    def install_signal_handlers(self) -> None:
        if self._installed_signals:
            return
        self._installed_signals = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):
                pass

    def _handle_signal(self, signum, _frame) -> None:
        logger.info("Received signal %s, stopping after the current operation.", signum)
        self.stop_event.set()

    def run(self, once: bool = False) -> int:
        self.install_signal_handlers()
        logger.info(
            "Agent %s starting against %s (interval=%ss, shell=%s, power=%s, service=%s)",
            self.config.agent_id,
            self.config.server_url,
            self.config.interval,
            self.config.allow_shell,
            self.config.allow_power,
            self.config.allow_service,
        )
        failures = 0
        while not self.stop_event.is_set():
            try:
                snapshot = self.collector.snapshot()
                response = self.client.heartbeat(snapshot)
                self.collector.ack_package_payload(response.get("package_inventory_revision"))
                failures = 0
                self._apply_policy(response.get("policy"))
                self._apply_profile(response.get("profile"))
                if response.get("request_package_inventory") == "full":
                    self.collector.request_package_full()
                self._note_update(response.get("agent_update"))
                commands = response.get("commands") or []
                for command in commands:
                    if self.stop_event.is_set():
                        break
                    self._execute(command)
                if once:
                    return 0
                if commands:
                    # Only one command is dispatched per heartbeat; poll again
                    # immediately so a backlog drains without waiting a full
                    # interval between commands.
                    if self.stop_event.wait(0.5):
                        break
                    continue
            except AgentHTTPError as exc:
                if exc.status in {401, 403}:
                    logger.error("Agent credentials rejected (%s). Update the agent config token.", exc)
                    return 2
                logger.error("Heartbeat rejected: %s", exc)
                failures += 1
            except Exception as exc:
                failures += 1
                logger.warning("Heartbeat failed: %s", exc)

            if once:
                return 1
            wait = self.config.interval
            if failures:
                wait = min(wait, 15)
            wait += random.uniform(0, min(5, wait / 4))
            if self.stop_event.wait(wait):
                break
        return 0

    def _execute(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id", ""))
        kind = str(command.get("kind", ""))
        logger.info("Executing command %s (%s)", command_id, kind)
        if kind == "refresh":
            self.collector.request_inventory_refresh()
        uploader = _OutputUploader(self.client, command_id) if command_id else None

        def on_chunk(stream: str, text: str) -> None:
            if uploader is not None:
                uploader.push(stream, text)

        started = time.time()
        try:
            result = execute_command(command, self.config, self.remote_policy, on_chunk=on_chunk)
        except Exception as exc:  # defensive: never let one command kill the agent loop
            logger.exception("Command %s crashed the executor", command_id)
            result = {
                "status": "failed",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "error": f"Agent executor error: {type(exc).__name__}",
            }
        finally:
            # Flush accepted output before posting the result so the server can
            # emit the final chunk before the terminal command event.
            if uploader is not None:
                uploader.close()
        result.setdefault("started_at", "")
        result["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
        result["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        logger.info(
            "Command %s finished: %s exit=%s in %.1fs",
            command_id,
            result.get("status"),
            result.get("exit_code"),
            time.time() - started,
        )

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self.client.send_result(command_id, result)
                return
            except Exception as exc:
                last_error = exc
                logger.warning("Could not post result for %s (attempt %s/3): %s", command_id, attempt, exc)
                if self.stop_event.wait(2 * attempt):
                    return
        logger.error("Giving up posting result for %s: %s", command_id, last_error)

    def _note_update(self, info: Any) -> None:
        if not isinstance(info, dict) or not info.get("available"):
            return
        if not self.config.allow_self_update:
            logger.warning("Agent update %s is available but allow_self_update is false.", info.get("version"))
            return
        version = str(info.get("version") or "")
        if version and version == self._update_notice:
            return
        self._update_notice = version
        logger.warning(
            "Agent update %s is available (current %s). Run: runner-agent update --apply --restart",
            info.get("version"),
            info.get("current_version"),
        )

    def _apply_profile(self, profile: Any) -> None:
        if not isinstance(profile, dict):
            return
        collection = profile.get("collection") if isinstance(profile.get("collection"), dict) else {}
        modules = profile.get("modules") if isinstance(profile.get("modules"), dict) else {}
        update = profile.get("update") if isinstance(profile.get("update"), dict) else {}
        self.config.interval = max(10, min(int(profile.get("heartbeat_seconds", self.config.interval)), 3600))
        self.config.service_inventory_interval = max(
            60, min(int(collection.get("service_inventory_seconds", 21600)), 86400)
        )
        self.config.package_inventory_interval = max(
            300, min(int(collection.get("package_inventory_seconds", 86400)), 604800)
        )
        self.config.package_full_interval = max(
            3600, min(int(collection.get("package_full_seconds", 604800)), 2592000)
        )
        self.config.max_output_bytes = min(
            self._local_max_output_bytes, max(1024, int(profile.get("max_output_bytes", 200000)))
        )
        self.config.allow_self_update = self._local_allow_self_update and bool(
            update.get("allow_self_update", True)
        ) and bool(modules.get("self_update", True))
        self.remote_policy["allow_shell"] = bool(self.remote_policy.get("allow_shell", True)) and bool(
            modules.get("shell", True)
        )
        self.remote_policy["allow_power"] = bool(self.remote_policy.get("allow_power", True)) and bool(
            modules.get("power", True)
        )
        self.remote_policy["allow_service"] = bool(self.remote_policy.get("allow_service", True)) and bool(
            modules.get("service_control", True)
        )
        self.remote_policy["allow_packages"] = bool(self.remote_policy.get("allow_packages", False)) and bool(
            modules.get("package_management", False)
        )
        self.collector.set_profile(profile)

    def _apply_policy(self, policy: Any) -> None:
        if not isinstance(policy, dict):
            return
        watches = policy.get("watch_services")
        if isinstance(watches, list):
            self.collector.set_watch_services([str(item) for item in watches])
        self.remote_policy = {
            "allow_shell": bool(policy.get("allow_shell", True)),
            "allow_power": bool(policy.get("allow_power", True)),
            "allow_service": bool(policy.get("allow_service", True)),
            "allow_packages": bool(policy.get("allow_packages", False)),
        }

    def health(self) -> dict[str, Any]:
        return self.client.health()
