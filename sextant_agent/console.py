"""Interactive Docker console using a local PTY.

Linux-only. Runs ``docker exec -it`` in a background PTY so the agent heartbeat
loop keeps running. Input/output are streamed to the server; nothing is written
to disk by this module.
"""

from __future__ import annotations

import base64
import os
import re
import select
import signal
import subprocess
import threading
import time
from typing import Any

from .config import AgentConfig

try:  # Linux-only imports; the module must still import on Windows.
    import fcntl
    import pty
    import struct
    import termios
except ImportError:  # pragma: no cover - Windows
    fcntl = None
    pty = None
    struct = None
    termios = None

_PTY_AVAILABLE = all(item is not None for item in (fcntl, pty, struct, termios))
_SHELLS = {"sh", "bash", "ash"}
_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_WORKDIR_RE = re.compile(r"^(?:/[^\x00-\x1f]{0,255})?$")


class ConsoleError(ValueError):
    pass


class ConsoleManager:
    def __init__(self, config: AgentConfig, client: Any, stop_event: threading.Event):
        self.config = config
        self.client = client
        self.stop_event = stop_event
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def build_argv(container_id: str, args: dict[str, Any]) -> list[str]:
        shell = str(args.get("shell", "sh") or "").strip().lower()
        if shell not in _SHELLS:
            raise ConsoleError("shell must be sh, bash, or ash")
        user = str(args.get("user", "") or "").strip()
        if user and not _USER_RE.match(user):
            raise ConsoleError("invalid console user")
        workdir = str(args.get("workdir", "") or "").strip()
        if workdir and not _WORKDIR_RE.match(workdir):
            raise ConsoleError("console workdir must be an absolute path")
        if not re.match(r"^[a-f0-9]{12,64}$", container_id):
            raise ConsoleError("invalid container ID")
        argv = ["exec", "-it"]
        if user:
            argv.extend(["-u", user])
        if workdir:
            argv.extend(["-w", workdir])
        argv.extend(["-e", "TERM=xterm-256color", container_id, shell, "-l"])
        return argv

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def open(
        self,
        session_id: str,
        container_id: str,
        args: dict[str, Any],
        docker_env: dict[str, str],
    ) -> None:
        if not _PTY_AVAILABLE:
            raise ConsoleError("Interactive console is Linux-only.")
        if self.stop_event.is_set():
            raise ConsoleError("Agent is stopping.")
        with self._lock:
            if len(self._sessions) >= self.config.docker_console_max_sessions:
                raise ConsoleError("Maximum concurrent consoles reached on this agent.")
            if session_id in self._sessions:
                raise ConsoleError("Console session already exists.")
        argv = self.build_argv(container_id, args)
        rows = max(10, min(int(args.get("rows", 32)), 200))
        cols = max(20, min(int(args.get("cols", 100)), 300))
        command = self.config.docker_command or "docker"
        try:
            master, slave = pty.openpty()
            self._set_winsize(master, rows, cols)
            process = subprocess.Popen(
                [command, *argv],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=docker_env,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave)
        except OSError as exc:
            raise ConsoleError(f"Could not start console process: {exc}") from exc
        session = {
            "session_id": session_id,
            "container_id": container_id,
            "process": process,
            "master": master,
            "stop": threading.Event(),
            "closed_sent": False,
            "lock": threading.Lock(),
            "last_activity": time.monotonic(),
            "exit_code": None,
            "error": "",
        }
        with self._lock:
            self._sessions[session_id] = session
        reader = threading.Thread(target=self._read_loop, args=(session,), daemon=True)
        poller = threading.Thread(target=self._poll_loop, args=(session,), daemon=True)
        session["reader"] = reader
        session["poller"] = poller
        reader.start()
        poller.start()

    @staticmethod
    def _set_winsize(master: int, rows: int, cols: int) -> None:
        if not _PTY_AVAILABLE:
            return
        try:
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def _read_loop(self, session: dict[str, Any]) -> None:
        master = session["master"]
        process: subprocess.Popen = session["process"]
        seq = 0
        try:
            while not session["stop"].is_set():
                try:
                    readable, _, _ = select.select([master], [], [], 0.5)
                except (OSError, ValueError):
                    break
                if not readable:
                    continue
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                session["last_activity"] = time.monotonic()
                seq += 1
                try:
                    self.client.send_console_output(session["session_id"], data)
                except Exception:
                    session["stop"].set()
                    break
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._terminate(session)
            exit_code = process.poll()
            self._finish(session, exit_code, session.get("error", ""))

    def _poll_loop(self, session: dict[str, Any]) -> None:
        failures = 0
        try:
            while not session["stop"].is_set() and not self.stop_event.is_set():
                if time.monotonic() - session["last_activity"] > self.config.docker_console_idle_seconds:
                    session["error"] = "Console idle timeout."
                    self._terminate(session)
                    return
                try:
                    response = self.client.console_poll(session["session_id"], timeout=30)
                    failures = 0
                except Exception:
                    failures += 1
                    if failures >= 3:
                        session["error"] = "Server console poll failed."
                        self._terminate(session)
                        return
                    time.sleep(1)
                    continue
                if not isinstance(response, dict) or not response:
                    continue
                kind = response.get("type")
                if kind == "close":
                    self._terminate(session)
                    return
                if kind == "input":
                    try:
                        data = base64.b64decode(str(response.get("data", "")))
                        os.write(session["master"], data)
                        session["last_activity"] = time.monotonic()
                    except (OSError, ValueError):
                        self._terminate(session)
                        return
                elif kind == "resize":
                    rows = max(10, min(int(response.get("rows", 32)), 200))
                    cols = max(20, min(int(response.get("cols", 100)), 300))
                    self._set_winsize(session["master"], rows, cols)
        finally:
            if session["stop"].is_set():
                self._finish_if_needed(session)

    def _finish(self, session: dict[str, Any], exit_code: int | None, error: str) -> None:
        with session["lock"]:
            if session["closed_sent"]:
                return
            session["closed_sent"] = True
        with self._lock:
            self._sessions.pop(session["session_id"], None)
        try:
            self.client.send_console_closed(session["session_id"], exit_code, error)
        except Exception:
            pass

    def _finish_if_needed(self, session: dict[str, Any]) -> None:
        if session["closed_sent"]:
            return
        if session["process"].poll() is None:
            self._terminate(session)
        try:
            session["process"].wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        self._finish(session, session["process"].poll(), session.get("error", ""))

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return
        self._terminate(session)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self._terminate(session)

    @staticmethod
    def _terminate(session: dict[str, Any]) -> None:
        session["stop"].set()
        process: subprocess.Popen = session["process"]
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
        try:
            os.close(session["master"])
        except OSError:
            pass
