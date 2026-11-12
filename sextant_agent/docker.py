"""Read-only Docker discovery and inventory for Linux agents.

The agent talks to the local Docker CLI only. It never opens a network listener,
never calls the Docker Engine API directly, and never collects container
environment variables.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import threading
import time
from pathlib import Path
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
_CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{12,64}$")
_IMAGE_ID_RE = re.compile(r"^(?:sha256:)?[a-f0-9]{12,64}$")
_NETWORK_ID_RE = re.compile(r"^[a-f0-9]{12,64}$")
_VOLUME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_RENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_WORKDIR_RE = re.compile(r"^(?:/[^\x00-\x1f]{0,255})?$")
_RESTART_POLICY_RE = re.compile(r"^(?:no|always|unless-stopped|on-failure(?::[0-9]{1,5})?)$")
_MEMORY_RE = re.compile(r"^[0-9]{1,12}[bkmg]?$")
_CPUS_RE = re.compile(r"^[0-9]{1,4}(?:\.[0-9]{1,4})?$")
_RECLAIM_RE = re.compile(r"Total reclaimed space:\s*([0-9.]+)\s*([A-Za-z]+)", re.IGNORECASE)
_REMOVED_RE = re.compile(r"Removed\s+([0-9]+)\s+stopped container", re.IGNORECASE)

_CONTAINER_LIFECYCLE_ACTIONS = {
    "container.start",
    "container.stop",
    "container.restart",
    "container.pause",
    "container.unpause",
    "container.kill",
}
_COMPOSE_ACTIONS = {"compose.up", "compose.down", "compose.restart", "compose.pull"}
_MUTATION_ACTIONS = _CONTAINER_LIFECYCLE_ACTIONS | {
    "container.rename",
    "container.remove",
    "container.update",
    "container.exec_one_shot",
    "container.clone",
    "image.remove",
    "volume.remove",
    "network.remove",
    "cleanup",
} | _COMPOSE_ACTIONS
_CLEANUP_SCOPES = {
    "stopped_containers",
    "dangling_images",
    "unused_images",
    "unused_volumes",
    "unused_networks",
    "build_cache",
}
DOCKER_MUTATION_ACTIONS = _MUTATION_ACTIONS
DOCKER_CLEANUP_SCOPES = _CLEANUP_SCOPES


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
        "memory_limit": 0,
        "nano_cpus": 0,
        "pids_limit": None,
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


_AGENT_COMPOSE_NAMES = {"compose.yaml", "compose.yml"}
_AGENT_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_AGENT_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_AGENT_BUILD_RE = re.compile(r"(?m)^\s*build\s*:")


def _compose_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        data = files[name]
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\x00")
        digest.update(data)
    return digest.hexdigest()


def _validate_compose_files(files: dict[str, bytes]) -> None:
    names = sorted(files)
    if len([name for name in names if name in _AGENT_COMPOSE_NAMES]) != 1:
        raise ValueError("bundle must contain exactly one compose.yaml or compose.yml")
    for name in names:
        if not _AGENT_FILE_RE.match(name) or name in {".", ".."}:
            raise ValueError("bundle contains an invalid file name")
        if name in _AGENT_COMPOSE_NAMES or name == ".env":
            continue
        if name.startswith(".") or not name.endswith((".yaml", ".yml")):
            raise ValueError("bundle contains a non-Compose file")
        if _AGENT_BUILD_RE.search(files[name].decode("utf-8", errors="replace")):
            raise ValueError("v1 supports image-based Compose only; build: services are not supported.")
    for name in names:
        if name in _AGENT_COMPOSE_NAMES or name.endswith((".yaml", ".yml")):
            if _AGENT_BUILD_RE.search(files[name].decode("utf-8", errors="replace")):
                raise ValueError("v1 supports image-based Compose only; build: services are not supported.")


def _apply_inventory_usage(
    containers: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    networks: list[dict[str, Any]],
) -> None:
    mount_names: set[str] = set()
    for container in containers:
        for mount in container.get("mounts") or []:
            text = str(mount)
            if "/volumes/" in text:
                mount_names.add(text.split("/volumes/", 1)[1].split("/", 1)[0])
            elif ":" in text:
                source = text.split(":", 1)[0].strip()
                if source and "/" not in source:
                    mount_names.add(source)
    for volume in volumes:
        volume["in_use"] = str(volume.get("name", "")) in mount_names
    for network in networks:
        name = str(network.get("name", ""))
        network["container_count"] = sum(
            1 for container in containers if name and name in (container.get("networks") or [])
        )


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
        self._mutation_lock = threading.Lock()

    # --- Profile / policy -------------------------------------------------
    def set_profile(self, profile: dict[str, Any]) -> None:
        if isinstance(profile, dict) and profile != self.profile:
            self.profile = profile
            self._status_at = 0.0

    def set_policy(self, policy: dict[str, Any]) -> None:
        if isinstance(policy, dict) and policy != self.policy:
            self.policy = policy
            self._status_at = 0.0

    def _server_collection(self) -> bool:
        # Explicit server-side agent setting. It is allowed to override the
        # local config default and the profile module so an admin can turn on
        # Docker collection from the console.
        return bool(self.policy.get("docker_enabled", False))

    def _module_enabled(self) -> bool:
        modules = self.profile.get("modules") if isinstance(self.profile.get("modules"), dict) else {}
        # A missing module key means an older profile; the local ceiling still applies.
        return bool(modules.get("docker", True))

    def _policy_enabled(self) -> bool:
        return bool(self.policy.get("allow_docker", True))

    def enabled(self) -> bool:
        if self._server_collection():
            return True
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
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        return run_capture(
            [command, *args],
            max(5, min(int(timeout or self.config.docker_timeout), 300)),
            max_output=max_output,
            env=env,
            cwd=cwd,
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
        self._apply_container_inspect(command, env, containers)
        _apply_inventory_usage(containers, volumes, networks)

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

    def _apply_container_inspect(
        self,
        command: str,
        env: dict[str, str],
        containers: list[dict[str, Any]],
    ) -> None:
        if not containers:
            return
        template = (
            "{{.Id}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|{{.State.ExitCode}}|"
            "{{.HostConfig.RestartPolicy.Name}}|{{.HostConfig.RestartPolicy.MaximumRetryCount}}|"
            "{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.PidsLimit}}"
        )
        by_id = {str(item.get("id", "")): item for item in containers}
        for start in range(0, len(containers), 100):
            chunk = [str(item.get("id", "")) for item in containers[start : start + 100]]
            result = self._run(
                command,
                ["inspect", "--format", template, *chunk],
                env=env,
                max_output=MAX_COMMAND_OUTPUT,
            )
            if result["status"] != "succeeded":
                continue
            for line in str(result.get("stdout", "")).splitlines():
                fields = line.split("|", 8)
                if len(fields) != 9:
                    continue
                container_id = fields[0].strip()
                item = by_id.get(container_id)
                if item is None:
                    continue
                item["started_at"] = _text(fields[1], 64)
                item["finished_at"] = _text(fields[2], 64)
                try:
                    exit_code = int(fields[3])
                except ValueError:
                    exit_code = None
                if exit_code is not None and exit_code >= 0:
                    item["exit_code"] = exit_code
                restart_name = _text(fields[4], 64)
                try:
                    retry_count = int(fields[5])
                except ValueError:
                    retry_count = 0
                if restart_name == "on-failure" and retry_count > 0:
                    item["restart_policy"] = f"on-failure:{retry_count}"[:64]
                else:
                    item["restart_policy"] = restart_name
                try:
                    item["memory_limit"] = max(0, int(fields[6]))
                    item["nano_cpus"] = max(0, int(fields[7]))
                except ValueError:
                    pass
                pids_text = fields[8].strip()
                try:
                    item["pids_limit"] = int(pids_text) if pids_text else None
                except ValueError:
                    item["pids_limit"] = None

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

    # --- Mutating actions ------------------------------------------------
    def _policy_flag(self, name: str) -> bool:
        return bool(self.policy.get(name, True))

    def exec_enabled(self) -> bool:
        return (
            self.enabled()
            and (bool(self.config.allow_docker_exec) or self._server_collection())
            and bool(self.policy.get("allow_docker_exec", False))
        )

    def prune_enabled(self) -> bool:
        return (
            self.enabled()
            and (bool(self.config.allow_docker_prune) or self._server_collection())
            and bool(self.policy.get("allow_docker_prune", False))
        )

    def compose_enabled(self) -> bool:
        return (
            self.enabled()
            and (bool(self.config.allow_docker_compose) or self._server_collection())
            and bool(self.policy.get("allow_docker_compose", False))
        )

    @staticmethod
    def _action_result(
        status: str,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        return {
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout[:MAX_COMMAND_OUTPUT],
            "stderr": stderr[:MAX_COMMAND_OUTPUT],
            "error": error[:4000],
        }

    def _resolve_target(
        self,
        command: str,
        env: dict[str, str],
        args: list[str],
        pattern: re.Pattern[str],
    ) -> tuple[str | None, dict[str, Any]]:
        result = self._run(command, args, env=env, max_output=16384)
        value = str(result.get("stdout", "")).strip()
        if result["status"] == "succeeded" and pattern.match(value):
            return value, result
        return None, result

    def execute_action(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one allowlisted Docker action with argv only."""
        if platform.system() != "Linux":
            return self._action_result("failed", error="Docker actions are Linux-only.")
        if not self.enabled():
            return self._action_result(
                "failed",
                error="Docker is disabled by local config, profile, or server policy.",
            )
        command = self._docker_binary()
        if not command:
            return self._action_result("failed", error="Docker CLI is not installed.")
        env = self._docker_env()

        if action == "container.exec_one_shot" and not self.exec_enabled():
            return self._action_result("failed", error="Docker exec is disabled by local or server policy.")
        if action == "cleanup" and not self.prune_enabled():
            return self._action_result("failed", error="Docker cleanup is disabled by local or server policy.")
        if action not in _MUTATION_ACTIONS and action not in {
            "container.logs",
            "container.inspect",
        }:
            return self._action_result("failed", error="Unsupported Docker action.")

        locked = False
        if action in _MUTATION_ACTIONS:
            locked = self._mutation_lock.acquire(blocking=False)
            if not locked:
                return self._action_result(
                    "failed",
                    error="Another Docker mutation is already running on this host.",
                )
        try:
            if action in _CONTAINER_LIFECYCLE_ACTIONS:
                return self._container_lifecycle(command, env, action, args)
            if action == "container.rename":
                return self._container_rename(command, env, args)
            if action == "container.remove":
                return self._container_remove(command, env, args)
            if action == "container.update":
                return self._container_update(command, env, args)
            if action == "container.logs":
                return self._container_logs(command, env, args)
            if action == "container.inspect":
                return self._container_inspect(command, env, args)
            if action == "container.exec_one_shot":
                return self._container_exec(command, env, args)
            if action == "container.clone":
                return self._container_clone(command, env, args)
            if action == "image.remove":
                return self._image_remove(command, env, args)
            if action == "volume.remove":
                return self._volume_remove(command, env, args)
            if action == "network.remove":
                return self._network_remove(command, env, args)
            if action == "cleanup":
                return self._cleanup(command, env, args)
            return self._action_result("failed", error="Unsupported Docker action.")
        except Exception as exc:  # defensive: never let an action kill the agent loop
            return self._action_result("failed", error=f"Docker action error: {type(exc).__name__}")
        finally:
            if locked:
                self._mutation_lock.release()

    def resolve_container(self, target: str) -> tuple[str | None, str]:
        if platform.system() != "Linux" or not self.enabled():
            return None, "Docker is unavailable."
        command = self._docker_binary()
        if not command:
            return None, "Docker CLI is not installed."
        container_id, result = self._target_container(
            command,
            self._docker_env(),
            {"container_id": target},
        )
        if container_id is None:
            return None, str(result.get("stderr") or "Container not found.")
        return container_id, ""

    def _target_container(self, command: str, env: dict[str, str], args: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        target = str(args.get("container_id", "") or "").strip()
        if not _CONTAINER_ID_RE.match(target):
            return None, self._action_result("failed", error="Invalid container ID.")
        return self._resolve_target(
            command,
            env,
            ["inspect", "--format", "{{.Id}}", target],
            _CONTAINER_ID_RE,
        )

    def _container_lifecycle(self, command: str, env: dict[str, str], action: str, args: dict[str, Any]) -> dict[str, Any]:
        container_id, resolved = self._target_container(command, env, args)
        if container_id is None:
            return self._action_result(
                "failed",
                stderr=str(resolved.get("stderr", "")),
                error="Container not found in the current Docker inventory.",
            )
        verb = action.split(".", 1)[1]
        return self._run(command, [verb, container_id], env=env, max_output=MAX_COMMAND_OUTPUT)

    def _container_rename(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        container_id, resolved = self._target_container(command, env, args)
        if container_id is None:
            return self._action_result("failed", error="Container not found.")
        new_name = str(args.get("new_name", "") or "").strip()
        if not _RENAME_RE.match(new_name):
            return self._action_result("failed", error="Invalid container name.")
        return self._run(command, ["rename", container_id, new_name], env=env, max_output=MAX_COMMAND_OUTPUT)

    def _container_remove(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        container_id, resolved = self._target_container(command, env, args)
        if container_id is None:
            return self._action_result("failed", error="Container not found.")
        argv = ["rm"]
        if bool(args.get("force", False)):
            argv.append("-f")
        if bool(args.get("remove_volumes", False)):
            argv.append("-v")
        argv.append(container_id)
        return self._run(command, argv, env=env, max_output=MAX_COMMAND_OUTPUT)

    def _container_update(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        container_id, resolved = self._target_container(command, env, args)
        if container_id is None:
            return self._action_result("failed", error="Container not found.")
        argv = ["update"]
        restart_policy = str(args.get("restart_policy", "") or "").strip()
        memory = str(args.get("memory", "") or "").strip().lower()
        cpus = str(args.get("cpus", "") or "").strip()
        pids_limit = args.get("pids_limit")
        if restart_policy:
            if not _RESTART_POLICY_RE.match(restart_policy):
                return self._action_result("failed", error="Invalid restart policy.")
            argv.extend(["--restart", restart_policy])
        if memory:
            if not _MEMORY_RE.match(memory):
                return self._action_result("failed", error="Invalid memory limit.")
            argv.extend(["--memory", memory])
        if cpus:
            if not _CPUS_RE.match(cpus):
                return self._action_result("failed", error="Invalid CPU limit.")
            argv.extend(["--cpus", cpus])
        if pids_limit is not None:
            try:
                pids = int(pids_limit)
            except (TypeError, ValueError):
                return self._action_result("failed", error="Invalid pids limit.")
            if not 0 <= pids <= 1_000_000:
                return self._action_result("failed", error="Invalid pids limit.")
            argv.extend(["--pids-limit", str(pids)])
        if len(argv) == 1:
            return self._action_result("failed", error="No update limits supplied.")
        argv.append(container_id)
        return self._run(command, argv, env=env, max_output=MAX_COMMAND_OUTPUT)

    def _container_logs(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        container_id, resolved = self._target_container(command, env, args)
        if container_id is None:
            return self._action_result("failed", error="Container not found.")
        try:
            tail = int(args.get("tail", 500))
        except (TypeError, ValueError):
            return self._action_result("failed", error="Invalid log tail.")
        if tail not in {100, 500, 1000}:
            return self._action_result("failed", error="Invalid log tail.")
        argv = ["logs", "--tail", str(tail)]
        if bool(args.get("timestamps", False)):
            argv.append("--timestamps")
        argv.append(container_id)
        return self._run(command, argv, env=env, max_output=MAX_COMMAND_OUTPUT)

    def _container_inspect(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        container_id, resolved = self._target_container(command, env, args)
        if container_id is None:
            return self._action_result("failed", error="Container not found.")
        result = self._run(command, ["inspect", container_id], env=env, max_output=MAX_COMMAND_OUTPUT)
        if result["status"] != "succeeded":
            return result
        try:
            parsed = json.loads(result.get("stdout", ""))
        except (TypeError, ValueError):
            return self._action_result("failed", error="Docker inspect returned invalid JSON.")
        result["stdout"] = json.dumps(self._mask_env(parsed), indent=2, ensure_ascii=False)[:MAX_COMMAND_OUTPUT]
        return result

    @classmethod
    def _mask_env(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ["<masked>"] if str(key).lower() == "env" else cls._mask_env(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._mask_env(item) for item in value]
        return value

    def _container_exec(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        container_id, resolved = self._target_container(command, env, args)
        if container_id is None:
            return self._action_result("failed", error="Container not found.")
        shell = str(args.get("shell", "sh") or "").strip().lower()
        if shell not in {"sh", "bash", "ash"}:
            return self._action_result("failed", error="Unsupported exec shell.")
        user = str(args.get("user", "") or "").strip()
        if user and not _USER_RE.match(user):
            return self._action_result("failed", error="Invalid exec user.")
        workdir = str(args.get("workdir", "") or "").strip()
        if workdir and not _WORKDIR_RE.match(workdir):
            return self._action_result("failed", error="Invalid exec workdir.")
        script = str(args.get("command", "") or "")
        if not script.strip() or len(script) > 8000 or "\x00" in script:
            return self._action_result("failed", error="Invalid exec command.")
        try:
            timeout = max(5, min(int(args.get("timeout", 60)), 300))
        except (TypeError, ValueError):
            return self._action_result("failed", error="Invalid exec timeout.")
        argv = ["exec", "-i"]
        if bool(args.get("tty", False)):
            argv.append("-t")
        if user:
            argv.extend(["-u", user])
        if workdir:
            argv.extend(["-w", workdir])
        argv.extend(["-e", "TERM=xterm-256color", container_id, shell, "-lc", script])
        return self._run(command, argv, env=env, max_output=MAX_COMMAND_OUTPUT, timeout=timeout)

    def _container_clone(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        source_id, source_result = self._target_container(command, env, args)
        if source_id is None:
            return self._action_result("failed", error="Source container not found.")
        new_name = str(args.get("new_name", "") or "").strip()
        if not _RENAME_RE.match(new_name):
            return self._action_result("failed", error="Invalid clone container name.")
        image_target = str(args.get("image_id", "") or "").strip()
        if not _IMAGE_ID_RE.match(image_target):
            return self._action_result("failed", error="Invalid clone image ID.")
        image_id, image_result = self._resolve_target(
            command, env, ["image", "inspect", "--format", "{{.Id}}", image_target], _IMAGE_ID_RE
        )
        if image_id is None:
            return self._action_result("failed", error="Clone image not found.")
        network_ids: list[str] = []
        for network_target in args.get("network_ids") or []:
            network_id, network_result = self._resolve_target(
                command,
                env,
                ["network", "inspect", "--format", "{{.Id}}", str(network_target)],
                _NETWORK_ID_RE,
            )
            if network_id is None:
                return self._action_result("failed", error="Clone network not found.")
            network_ids.append(network_id)
        argv = ["create", "--name", new_name]
        restart_policy = str(args.get("restart_policy", "") or "").strip()
        memory = str(args.get("memory", "") or "").strip().lower()
        cpus = str(args.get("cpus", "") or "").strip()
        pids_limit = args.get("pids_limit")
        if restart_policy:
            if not _RESTART_POLICY_RE.match(restart_policy):
                return self._action_result("failed", error="Invalid clone restart policy.")
            argv.extend(["--restart", restart_policy])
        if memory:
            if not _MEMORY_RE.match(memory):
                return self._action_result("failed", error="Invalid clone memory limit.")
            argv.extend(["--memory", memory])
        if cpus:
            if not _CPUS_RE.match(cpus):
                return self._action_result("failed", error="Invalid clone CPU limit.")
            argv.extend(["--cpus", cpus])
        if pids_limit is not None:
            try:
                pids = int(pids_limit)
            except (TypeError, ValueError):
                return self._action_result("failed", error="Invalid clone pids limit.")
            if not 0 <= pids <= 1_000_000:
                return self._action_result("failed", error="Invalid clone pids limit.")
            argv.extend(["--pids-limit", str(pids)])
        if network_ids:
            argv.extend(["--network", network_ids[0]])
        argv.append(image_id)
        command_args = args.get("command") or []
        if not isinstance(command_args, list) or len(command_args) > 64:
            return self._action_result("failed", error="Invalid clone command.")
        for item in command_args:
            value = str(item)
            if not value or len(value) > 2048 or "\x00" in value:
                return self._action_result("failed", error="Invalid clone command argument.")
            argv.append(value)
        created = self._run(command, argv, env=env, max_output=MAX_COMMAND_OUTPUT)
        if created["status"] != "succeeded":
            return created
        connected: list[dict[str, Any]] = []
        for network_id in network_ids[1:]:
            result = self._run(
                command,
                ["network", "connect", network_id, new_name],
                env=env,
                max_output=16384,
            )
            connected.append(result)
            if result["status"] != "succeeded":
                return self._compose_aggregate(
                    "failed",
                    created,
                    *connected,
                    error="Clone was created but an additional network attachment failed.",
                )
        return self._compose_aggregate("succeeded", created, *connected)

    def _image_remove(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("image_id", "") or "").strip()
        if not _IMAGE_ID_RE.match(target):
            return self._action_result("failed", error="Invalid image ID.")
        image_id, resolved = self._resolve_target(
            command, env, ["image", "inspect", "--format", "{{.Id}}", target], _IMAGE_ID_RE
        )
        if image_id is None:
            return self._action_result("failed", error="Image not found.")
        return self._run(command, ["image", "rm", image_id], env=env, max_output=MAX_COMMAND_OUTPUT)

    def _volume_remove(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("volume_name", "") or "").strip()
        if not _VOLUME_NAME_RE.match(target):
            return self._action_result("failed", error="Invalid volume name.")
        name, resolved = self._resolve_target(
            command, env, ["volume", "inspect", "--format", "{{.Name}}", target], _VOLUME_NAME_RE
        )
        if name is None:
            return self._action_result("failed", error="Volume not found.")
        return self._run(command, ["volume", "rm", name], env=env, max_output=MAX_COMMAND_OUTPUT)

    def _network_remove(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("network_id", "") or "").strip()
        if not _NETWORK_ID_RE.match(target):
            return self._action_result("failed", error="Invalid network ID.")
        network_id, resolved = self._resolve_target(
            command, env, ["network", "inspect", "--format", "{{.Id}}", target], _NETWORK_ID_RE
        )
        if network_id is None:
            return self._action_result("failed", error="Network not found.")
        return self._run(command, ["network", "rm", network_id], env=env, max_output=MAX_COMMAND_OUTPUT)

    def _cleanup(self, command: str, env: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
        scope = str(args.get("scope", "") or "").strip().lower()
        if scope not in _CLEANUP_SCOPES:
            return self._action_result("failed", error="Unsupported cleanup scope.")
        if scope == "stopped_containers":
            return self._cleanup_stopped_containers(command, env, bool(args.get("include_compose", False)))
        argv = {
            "dangling_images": ["image", "prune", "-f"],
            "unused_images": ["image", "prune", "-a", "-f"],
            "unused_volumes": ["volume", "prune", "-f"],
            "unused_networks": ["network", "prune", "-f"],
            "build_cache": ["builder", "prune", "-f"],
        }[scope]
        return self._run(command, argv, env=env, max_output=MAX_COMMAND_OUTPUT)

    def _cleanup_stopped_containers(
        self,
        command: str,
        env: dict[str, str],
        include_compose: bool,
    ) -> dict[str, Any]:
        listing = self._run(
            command,
            [
                "ps",
                "-a",
                "--no-trunc",
                "--filter",
                "status=exited",
                "--filter",
                "status=created",
                "--format",
                "{{json .}}",
            ],
            env=env,
            max_output=MAX_COMMAND_OUTPUT,
        )
        if listing["status"] != "succeeded":
            return listing
        candidates: list[tuple[str, str]] = []
        for raw in _json_lines(listing.get("stdout", "")):
            container_id = str(raw.get("ID", "")).strip()
            name = str(raw.get("Names", "")).split(",")[0].strip()
            state = str(raw.get("State", "")).strip().lower()
            labels = _parse_labels(raw.get("Labels"))
            if not _CONTAINER_ID_RE.match(container_id) or state not in {"exited", "created", "dead"}:
                continue
            if not include_compose and labels.get("com.docker.compose.project"):
                continue
            candidates.append((container_id, name))
            if len(candidates) >= 200:
                break
        if not candidates:
            return self._action_result(
                "succeeded",
                exit_code=0,
                stdout="Removed 0 stopped containers.",
            )
        lines: list[str] = []
        errors: list[str] = []
        removed = 0
        for container_id, name in candidates:
            result = self._run(command, ["rm", container_id], env=env, max_output=16384)
            if result["status"] == "succeeded":
                removed += 1
                lines.append(f"Removed {name or container_id} ({container_id}).")
            else:
                errors.append(f"{name or container_id}: {result.get('error') or result.get('stderr') or 'remove failed'}")
        summary = f"Removed {removed} stopped container{'s' if removed != 1 else ''}."
        stdout = "\n".join([summary, *lines])
        if errors:
            return self._action_result(
                "failed",
                exit_code=1,
                stdout=stdout,
                stderr="\n".join(errors)[:MAX_COMMAND_OUTPUT],
                error=f"{len(errors)} container removal(s) failed.",
            )
        return self._action_result("succeeded", exit_code=0, stdout=stdout)

    # --- Compose deployment ---------------------------------------------
    def execute_compose(
        self,
        action: str,
        args: dict[str, Any],
        bundle: bytes,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        if platform.system() != "Linux":
            return self._action_result("failed", error="Compose deployment is Linux-only.")
        if action not in _COMPOSE_ACTIONS:
            return self._action_result("failed", error="Unsupported Compose action.")
        if not self.compose_enabled():
            return self._action_result(
                "failed",
                error="Compose deployment is disabled by local config, profile, or server policy.",
            )
        command = self._docker_binary()
        if not command:
            return self._action_result("failed", error="Docker CLI is not installed.")
        revision_hash = str(args.get("revision_hash", "") or "")
        project = str(args.get("project_name", "") or "")
        deployment_id = str(args.get("deployment_id", "") or "")
        if not re.match(r"^[a-f0-9]{64}$", revision_hash):
            return self._action_result("failed", error="Invalid Compose revision hash.")
        if not _AGENT_PROJECT_RE.match(project):
            return self._action_result("failed", error="Invalid Compose project name.")
        if not re.match(r"^dep_[A-Za-z0-9]+$", deployment_id):
            return self._action_result("failed", error="Invalid deployment ID.")
        expected_bundle_hash = headers.get("x-bundle-sha256", "")
        if not expected_bundle_hash or hashlib.sha256(bundle).hexdigest() != expected_bundle_hash:
            return self._action_result("failed", error="Compose bundle SHA-256 verification failed.")
        expected_size = headers.get("content-length", "")
        try:
            if expected_size and int(expected_size) != len(bundle):
                return self._action_result("failed", error="Compose bundle size verification failed.")
        except ValueError:
            return self._action_result("failed", error="Compose bundle size header is invalid.")

        locked = self._mutation_lock.acquire(blocking=False)
        if not locked:
            return self._action_result("failed", error="Another Docker mutation is already running on this host.")
        try:
            try:
                files = self._extract_compose_bundle(bundle, revision_hash)
            except (ValueError, tarfile.TarError, OSError) as exc:
                return self._action_result("failed", error=f"Compose bundle rejected: {exc}")
            root = Path(self.config.docker_compose_dir or "/var/lib/sextant-agent/docker/stacks").expanduser()
            if not root.is_absolute():
                return self._action_result("failed", error="docker_compose_dir must be absolute.")
            root = root.resolve(strict=False)
            if root == Path("/"):
                return self._action_result("failed", error="docker_compose_dir cannot be /.")
            try:
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                root.chmod(0o700)
            except OSError as exc:
                return self._action_result("failed", error=f"Could not create Compose directory: {exc}")
            project_dir = root / project
            final_dir = project_dir / revision_hash
            try:
                project_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                project_dir.chmod(0o700)
            except OSError as exc:
                return self._action_result("failed", error=f"Could not create project directory: {exc}")
            if not final_dir.exists():
                temp_dir = Path(tempfile.mkdtemp(prefix=".rev-", dir=root))
                try:
                    for name, data in files.items():
                        target = temp_dir / name
                        with target.open("xb") as handle:
                            handle.write(data)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.chmod(target, 0o600)
                    os.chmod(temp_dir, 0o700)
                    os.replace(temp_dir, final_dir)
                finally:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
            compose_file = "compose.yaml" if "compose.yaml" in files else "compose.yml"
            base = [command, "compose", "-p", project, "-f", compose_file]
            env = self._docker_env()
            config = self._compose_run(base + ["config", "--quiet"], env, final_dir, 60)
            if config["status"] != "succeeded":
                return self._compose_aggregate("failed", config, error="Compose config validation failed.")
            if action == "compose.down":
                down_args = base + ["down"]
                if headers.get("x-compose-remove-volumes") == "1":
                    down_args.append("--volumes")
                return self._compose_run(down_args, env, final_dir)
            if action == "compose.up":
                parts = []
                if headers.get("x-compose-pull") == "1":
                    pull = self._compose_run(base + ["pull"], env, final_dir)
                    parts.append(pull)
                    if pull["status"] != "succeeded":
                        return self._compose_aggregate("failed", *parts)
                up = self._compose_run(base + ["up", "-d", "--remove-orphans"], env, final_dir)
                parts.append(up)
                return self._compose_aggregate(up["status"], *parts)
            if action == "compose.restart":
                restart = self._compose_run(base + ["restart"], env, final_dir)
                return restart
            pull = self._compose_run(base + ["pull"], env, final_dir)
            return pull
        finally:
            self._mutation_lock.release()

    def _extract_compose_bundle(self, bundle: bytes, revision_hash: str) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total = 0
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 16:
                raise ValueError("too many bundle entries")
            for member in members:
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("bundle may contain regular files only")
                name = member.name
                if not _AGENT_FILE_RE.match(name) or name in {".", ".."} or "/" in name or "\\" in name:
                    raise ValueError("bundle contains a path traversal or invalid name")
                if member.size > 262144:
                    raise ValueError("bundle entry exceeds the file size limit")
                total += member.size
                if total > 1_048_576:
                    raise ValueError("bundle exceeds the total size limit")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError("bundle entry could not be read")
                data = extracted.read(member.size + 1)
                if len(data) != member.size:
                    raise ValueError("bundle entry size mismatch")
                files[name] = data
        _validate_compose_files(files)
        if _compose_hash(files) != revision_hash:
            raise ValueError("bundle revision hash mismatch")
        return files

    def _compose_run(self, args: list[str], env: dict[str, str], cwd: Path, timeout: int = 300) -> dict[str, Any]:
        return self._run(
            args[0],
            args[1:],
            env=env,
            max_output=MAX_COMMAND_OUTPUT,
            timeout=timeout,
            cwd=str(cwd),
        )

    @staticmethod
    def _compose_aggregate(status: str, *parts: dict[str, Any], error: str = "") -> dict[str, Any]:
        stdout = "\n".join(str(part.get("stdout", "")) for part in parts if part.get("stdout"))
        stderr = "\n".join(str(part.get("stderr", "")) for part in parts if part.get("stderr"))
        exit_code = next((part.get("exit_code") for part in reversed(parts) if part.get("exit_code") is not None), None)
        errors = [str(part.get("error", "")) for part in parts if part.get("error")]
        if error:
            errors.append(error)
        return {
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout[:MAX_COMMAND_OUTPUT],
            "stderr": stderr[:MAX_COMMAND_OUTPUT],
            "error": "\n".join(errors)[:4000],
        }

    def reset(self) -> None:
        self._status = None
        self._status_at = 0.0
        self._requested = True
