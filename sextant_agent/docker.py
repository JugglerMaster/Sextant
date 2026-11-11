"""Read-only Docker discovery and inventory for Linux agents.

The agent talks to the local Docker CLI only. It never opens a network listener,
never calls the Docker Engine API directly, and never collects container
environment variables.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import time
from typing import Any

from .config import AgentConfig
from .executor import run_capture

MAX_CONTAINERS = 1000
MAX_IMAGES = 2000
MAX_VOLUMES = 1000
MAX_NETWORKS = 500
MAX_INVENTORY_BYTES = 2_000_000
MAX_COMMAND_OUTPUT = 1_500_000
PROBE_CACHE_SECONDS = 30
DEFAULT_INVENTORY_SECONDS = 300

REASONS = {
    "disabled",
    "unsupported_os",
    "not_installed",
    "socket_missing",
    "permission_denied",
    "daemon_unreachable",
    "compose_missing",
    "probe_error",
}

_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?b?)\s*$", re.IGNORECASE)
_LABEL_SPLIT_RE = re.compile(r",(?=[A-Za-z0-9_.-]+=)")


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "")[:limit]


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_size(value: Any) -> int:
    text = str(value or "").strip()
    match = _SIZE_RE.match(text)
    if not match:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"", "b"}:
        factor = 1
    elif unit.startswith("k"):
        factor = 1024
    elif unit.startswith("m"):
        factor = 1024**2
    elif unit.startswith("g"):
        factor = 1024**3
    elif unit.startswith("t"):
        factor = 1024**4
    elif unit.startswith("p"):
        factor = 1024**5
    elif unit.startswith("e"):
        factor = 1024**6
    else:
        factor = 1
    return max(0, int(amount * factor))


def _parse_labels(value: Any) -> dict[str, str]:
    labels: dict[str, str] = {}
    for part in _LABEL_SPLIT_RE.split(str(value or "")):
        if not part or "=" not in part:
            continue
        key, label_value = part.split("=", 1)
        key = key.strip()[:128]
        if key:
            labels[key] = label_value.strip()[:512]
        if len(labels) >= 64:
            break
    return labels


def _json_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _split_compact(value: Any, limit: int, item_limit: int = 255) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) == 1 and parts[0].lower() in {"", "<none>", "none"}:
        return []
    return [part[:item_limit] for part in parts[:limit]]


def _health_from_status(status: str) -> str:
    lowered = status.lower()
    if "(healthy)" in lowered:
        return "healthy"
    if "(unhealthy)" in lowered:
        return "unhealthy"
    if "(health: starting)" in lowered:
        return "starting"
    return ""


def _exit_code_from_status(status: str) -> int | None:
    match = re.search(r"exited\s*\((\d+)\)", status, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _container_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    container_id = _text(raw.get("ID"), 64).strip()
    if not container_id:
        return None
    labels = _parse_labels(raw.get("Labels"))
    status = _text(raw.get("Status"), 255)
    name = _text(raw.get("Names"), 255).split(",")[0].strip()
    return {
        "id": container_id,
        "name": name,
        "image": _text(raw.get("Image"), 512),
        "image_id": "",
        "command": _text(raw.get("Command"), 2048),
        "state": _text(raw.get("State"), 32).lower(),
        "status": status,
        "health": _health_from_status(status),
        "created_at": _text(raw.get("CreatedAt"), 64),
        "started_at": "",
        "finished_at": "",
        "exit_code": _exit_code_from_status(status),
        "restart_policy": "",
        "ports": _split_compact(raw.get("Ports"), 128),
        "labels": labels,
        "mounts": _split_compact(raw.get("Mounts"), 128, 1024),
        "networks": _split_compact(raw.get("Networks"), 64),
        "compose_project": labels.get("com.docker.compose.project", "")[:255],
        "compose_service": labels.get("com.docker.compose.service", "")[:255],
        "compose_config_hash": labels.get("com.docker.compose.config-hash", "")[:128],
    }


def _image_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        image_id = _text(raw.get("ID"), 128).strip()
        if not image_id:
            continue
        repository = _text(raw.get("Repository"), 255).strip()
        tag = _text(raw.get("Tag"), 128).strip()
        digest = _text(raw.get("Digest"), 512).strip()
        item = merged.setdefault(
            image_id,
            {
                "id": image_id,
                "repo_tags": [],
                "repo_digests": [],
                "size": 0,
                "shared_size": 0,
                "containers": 0,
                "dangling": True,
                "created_at": _text(raw.get("CreatedAt"), 64),
            },
        )
        if repository and repository != "<none>" and tag and tag != "<none>":
            tag_value = f"{repository}:{tag}"[:512]
            if tag_value not in item["repo_tags"] and len(item["repo_tags"]) < 64:
                item["repo_tags"].append(tag_value)
            item["dangling"] = False
        if repository and repository != "<none>" and digest and digest != "<none>":
            digest_value = f"{repository}@{digest}"[:1024]
            if digest_value not in item["repo_digests"] and len(item["repo_digests"]) < 64:
                item["repo_digests"].append(digest_value)
        item["size"] = max(int(item["size"]), _parse_size(raw.get("Size")))
        item["shared_size"] = max(int(item["shared_size"]), _parse_size(raw.get("SharedSize")))
        item["containers"] = max(int(item["containers"]), _int(raw.get("Containers")))
    return list(merged.values())[:MAX_IMAGES]


def _volume_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = _text(raw.get("Name"), 255).strip()
    if not name:
        return None
    return {
        "name": name,
        "driver": _text(raw.get("Driver"), 64),
        "scope": _text(raw.get("Scope"), 32),
        "mountpoint": _text(raw.get("Mountpoint"), 1024),
        "labels": _parse_labels(raw.get("Labels")),
        "in_use": False,
    }


def _network_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    network_id = _text(raw.get("ID"), 128).strip()
    if not network_id:
        return None
    return {
        "id": network_id,
        "name": _text(raw.get("Name"), 255),
        "driver": _text(raw.get("Driver"), 64),
        "scope": _text(raw.get("Scope"), 32),
        "internal": _bool(raw.get("Internal")),
        "attachable": _bool(raw.get("Attachable")),
        "labels": _parse_labels(raw.get("Labels")),
        "container_count": _int(raw.get("Containers")),
    }


def _counts(
    containers: list[dict[str, Any]],
    images: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    networks: list[dict[str, Any]],
) -> dict[str, int]:
    running = sum(1 for item in containers if item.get("state") == "running")
    paused = sum(1 for item in containers if item.get("state") == "paused")
    unhealthy = sum(1 for item in containers if item.get("health") == "unhealthy")
    stopped = max(0, len(containers) - running - paused)
    return {
        "containers": len(containers),
        "running": running,
        "paused": paused,
        "stopped": stopped,
        "unhealthy": unhealthy,
        "images": len(images),
        "volumes": len(volumes),
        "networks": len(networks),
    }


def _empty_summary(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason if reason in REASONS else "probe_error",
        "engine_version": "",
        "api_version": "",
        "compose_version": "",
        "compose_available": False,
        "rootless": False,
        "storage_driver": "",
        "cgroup_driver": "",
        "docker_root_dir": "",
        "counts": _counts([], [], [], []),
        "inventory_hash": "",
        "inventory_revision": 0,
        "collected_at": "",
    }


def _failure_reason(result: dict[str, Any]) -> str:
    text = f"{result.get('stderr', '')}\n{result.get('error', '')}".lower()
    if "permission denied" in text:
        return "permission_denied"
    if any(
        marker in text
        for marker in (
            "cannot connect",
            "is the docker daemon running",
            "connection refused",
            "no such host",
            "timeout",
        )
    ):
        return "daemon_unreachable"
    return "probe_error"


class DockerCollector:
    """Detect a local Docker engine and produce bounded inventory snapshots."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.profile: dict[str, Any] = {}
        self.policy: dict[str, Any] = {}
        self._status: dict[str, Any] | None = None
        self._status_at = 0.0
        self._requested = True
        self._last_inventory_at = 0.0
        self._last_hash = ""
        self._last_collected_at = ""
        self._revision = 0
        self._counts: dict[str, int] = _counts([], [], [], [])

    # --- Profile / policy -------------------------------------------------
    def set_profile(self, profile: dict[str, Any]) -> None:
        if isinstance(profile, dict) and profile != self.profile:
            self.profile = profile
            self._status_at = 0.0

    def set_policy(self, policy: dict[str, Any]) -> None:
        if isinstance(policy, dict) and policy != self.policy:
            self.policy = policy
            self._status_at = 0.0

    def _module_enabled(self) -> bool:
        modules = self.profile.get("modules") if isinstance(self.profile.get("modules"), dict) else {}
        # A missing module key means an older profile; the local ceiling still applies.
        return bool(modules.get("docker", True))

    def _policy_enabled(self) -> bool:
        return bool(self.policy.get("allow_docker", True))

    def enabled(self) -> bool:
        return bool(self.config.allow_docker) and self._module_enabled() and self._policy_enabled()

    def _interval(self) -> int:
        collection = self.profile.get("collection") if isinstance(self.profile.get("collection"), dict) else {}
        try:
            value = int(collection.get("docker_inventory_seconds", self.config.docker_inventory_interval))
        except (TypeError, ValueError):
            value = self.config.docker_inventory_interval
        return max(60, min(value, 86400))

    def request_inventory(self) -> None:
        self._requested = True
        self._status_at = 0.0

    def inventory_due(self) -> bool:
        if not self.enabled():
            return False
        status = self._probe()
        if not status.get("available"):
            return False
        return (
            self._requested
            or self._last_inventory_at == 0
            or (time.monotonic() - self._last_inventory_at) >= self._interval()
        )

    # --- Detection --------------------------------------------------------
    def _probe(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._status is not None and (now - self._status_at) < PROBE_CACHE_SECONDS:
            return dict(self._status)
        status = self._probe_uncached()
        self._status = status
        self._status_at = now
        return dict(status)

    def _probe_uncached(self) -> dict[str, Any]:
        if platform.system() != "Linux":
            return _empty_summary("unsupported_os")
        if not self.enabled():
            return _empty_summary("disabled")

        command = self._docker_binary()
        if not command:
            return _empty_summary("not_installed")

        socket_error = self._socket_error()
        if socket_error:
            return _empty_summary(socket_error)

        env = self._docker_env()
        version_result = self._run(
            command,
            ["version", "--format", "{{json .Server}}"],
            env=env,
            max_output=131072,
        )
        if version_result["status"] != "succeeded":
            return _empty_summary(_failure_reason(version_result))
        server = self._first_json(version_result.get("stdout", ""))
        if server is None:
            return _empty_summary("probe_error")

        info_result = self._run(command, ["info", "--format", "{{json .}}"], env=env, max_output=262144)
        info = self._first_json(info_result.get("stdout", "")) if info_result["status"] == "succeeded" else {}

        compose_result = self._run(command, ["compose", "version", "--short"], env=env, max_output=16384)
        compose_version = _text(compose_result.get("stdout", ""), 64).strip()
        compose_available = compose_result["status"] == "succeeded" and bool(compose_version)

        summary = {
            "available": True,
            "reason": "" if compose_available else "compose_missing",
            "engine_version": _text(server.get("Version"), 64),
            "api_version": _text(server.get("ApiVersion"), 64),
            "compose_version": compose_version if compose_available else "",
            "compose_available": compose_available,
            "rootless": self._rootless(info),
            "storage_driver": _text(info.get("Driver"), 64),
            "cgroup_driver": _text(info.get("CgroupDriver"), 64),
            "docker_root_dir": _text(info.get("DockerRootDir"), 255),
            "counts": {
                "containers": _int(info.get("Containers")),
                "running": _int(info.get("ContainersRunning")),
                "paused": _int(info.get("ContainersPaused")),
                "stopped": _int(info.get("ContainersStopped")),
                "unhealthy": 0,
                "images": _int(info.get("Images")),
                "volumes": 0,
                "networks": 0,
            },
            "inventory_hash": self._last_hash,
            "inventory_revision": self._revision,
            "collected_at": self._last_collected_at,
        }
        # Once an inventory has been collected, its bounded rows are the source
        # of truth for the counts shown on the host card.
        if self._last_inventory_at:
            summary["counts"] = dict(self._counts)
        return summary

    @staticmethod
    def _rootless(info: dict[str, Any]) -> bool:
        if _bool(info.get("Rootless")):
            return True
        options = info.get("SecurityOptions")
        if isinstance(options, list):
            return any("rootless" in str(option).lower() for option in options)
        return "rootless" in str(options or "").lower()

    def _docker_binary(self) -> str:
        configured = (self.config.docker_command or "docker").strip()
        if not configured:
            return ""
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
        return ""

    def _socket_error(self) -> str:
        socket = (self.config.docker_socket or "").strip()
        if not socket or socket.startswith("tcp://") or socket.startswith("http://") or socket.startswith("https://"):
            return ""
        path = socket
        if path.startswith("unix://"):
            path = path[len("unix://") :]
        if not path:
            return ""
        if not os.path.exists(path):
            return "socket_missing"
        if not os.access(path, os.R_OK | os.W_OK):
            return "permission_denied"
        return ""

    def _docker_env(self) -> dict[str, str]:
        env = os.environ.copy()
        socket = (self.config.docker_socket or "").strip()
        if socket:
            env["DOCKER_HOST"] = socket if "://" in socket else f"unix://{socket}"
        env.setdefault("DOCKER_CLI_HINTS", "false")
        return env

    def _run(
        self,
        command: str,
        args: list[str],
        *,
        env: dict[str, str],
        max_output: int,
    ) -> dict[str, Any]:
        return run_capture(
            [command, *args],
            max(5, int(self.config.docker_timeout)),
            max_output=max_output,
            env=env,
        )

    @staticmethod
    def _first_json(text: str) -> dict[str, Any] | None:
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                return value
        return None

    # --- Heartbeat / inventory -------------------------------------------
    def summary(self) -> dict[str, Any]:
        return self._probe()

    def collect_inventory(self, force: bool = False) -> dict[str, Any] | None:
        if not force and not self.inventory_due():
            return None
        status = self._probe(force=True)
        if not status.get("available"):
            return None
        command = self._docker_binary()
        if not command:
            return None
        env = self._docker_env()

        containers_raw = self._run(command, ["ps", "-a", "--no-trunc", "--format", "{{json .}}"], env=env, max_output=MAX_COMMAND_OUTPUT)
        images_raw = self._run(
            command,
            ["image", "ls", "--no-trunc", "--digests", "--format", "{{json .}}"],
            env=env,
            max_output=MAX_COMMAND_OUTPUT,
        )
        volumes_raw = self._run(
            command,
            ["volume", "ls", "--format", "{{json .}}"],
            env=env,
            max_output=MAX_COMMAND_OUTPUT,
        )
        networks_raw = self._run(
            command,
            ["network", "ls", "--format", "{{json .}}"],
            env=env,
            max_output=MAX_COMMAND_OUTPUT,
        )
        if containers_raw["status"] != "succeeded":
            self._requested = True
            return None

        containers: list[dict[str, Any]] = []
        for raw in _json_lines(containers_raw.get("stdout", "")):
            row = _container_row(raw)
            if row is not None:
                containers.append(row)
            if len(containers) >= MAX_CONTAINERS:
                break
        images = _image_rows(_json_lines(images_raw.get("stdout", ""))) if images_raw["status"] == "succeeded" else []
        volumes = [
            row
            for row in (_volume_row(raw) for raw in _json_lines(volumes_raw.get("stdout", "")))
            if row is not None
        ][:MAX_VOLUMES]
        networks = [
            row
            for row in (_network_row(raw) for raw in _json_lines(networks_raw.get("stdout", "")))
            if row is not None
        ][:MAX_NETWORKS]

        collected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        containers.sort(key=lambda item: (item.get("name", ""), item.get("id", "")))
        images.sort(key=lambda item: item.get("id", ""))
        volumes.sort(key=lambda item: item.get("name", ""))
        networks.sort(key=lambda item: (item.get("name", ""), item.get("id", "")))
        counts = _counts(containers, images, volumes, networks)
        revision = self._revision + 1
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                {"containers": containers, "images": images, "volumes": volumes, "networks": networks},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        inventory_hash = digest.hexdigest()

        summary = dict(status)
        summary.update(
            {
                "reason": "" if status.get("compose_available") else "compose_missing",
                "counts": counts,
                "inventory_hash": inventory_hash,
                "inventory_revision": revision,
                "collected_at": collected_at,
            }
        )
        payload: dict[str, Any] = {
            "summary": summary,
            "containers": containers,
            "images": images,
            "volumes": volumes,
            "networks": networks,
            "collected_at": collected_at,
            "hash": inventory_hash,
            "revision": revision,
        }
        payload = self._fit_payload(payload)

        self._last_inventory_at = time.monotonic()
        self._last_hash = str(payload["hash"])
        self._last_collected_at = str(payload["collected_at"])
        self._revision = int(payload["revision"])
        self._counts = _counts(payload["containers"], payload["images"], payload["volumes"], payload["networks"])
        self._requested = False
        # Force the next heartbeat summary to pick up the new hash/revision.
        self._status = None
        self._status_at = 0.0
        return payload

    @staticmethod
    def _fit_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Drop trailing rows until the serialized inventory is under the cap."""
        limit = MAX_INVENTORY_BYTES
        lists = ("containers", "images", "volumes", "networks")
        while len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > limit:
            largest = max(lists, key=lambda key: len(payload.get(key) or []))
            rows = payload.get(largest) or []
            if not rows:
                break
            rows.pop()
            # Recompute the hash and revision after trimming so the payload stays
            # internally consistent and the server can replace it atomically.
            digest = hashlib.sha256()
            digest.update(
                json.dumps(
                    {
                        "containers": payload["containers"],
                        "images": payload["images"],
                        "volumes": payload["volumes"],
                        "networks": payload["networks"],
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            payload["hash"] = digest.hexdigest()
            payload["revision"] = int(payload.get("revision", 0)) + 1
            payload["summary"]["inventory_hash"] = payload["hash"]
            payload["summary"]["inventory_revision"] = payload["revision"]
            payload["summary"]["counts"] = _counts(
                payload["containers"], payload["images"], payload["volumes"], payload["networks"]
            )
        return payload

    def reset(self) -> None:
        self._status = None
        self._status_at = 0.0
        self._requested = True
