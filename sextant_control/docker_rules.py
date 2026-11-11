"""Validation rules shared by Docker API models and command normalization."""

from __future__ import annotations

import re
from typing import Any

STACK_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"
PROJECT_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
REVISION_HASH_PATTERN = r"^[a-f0-9]{64}$"
CONTAINER_ID_PATTERN = r"^[a-f0-9]{12,64}$"
IMAGE_ID_PATTERN = r"^(?:sha256:)?[a-f0-9]{12,64}$"
NETWORK_ID_PATTERN = r"^[a-f0-9]{12,64}$"
VOLUME_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$"
RENAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
USER_PATTERN = r"^[A-Za-z0-9_.-]{1,32}$"
WORKDIR_PATTERN = r"^(?:/[^\x00-\x1f]{0,255})?$"
RESTART_POLICY_PATTERN = r"^(?:no|always|unless-stopped|on-failure(?::[0-9]{1,5})?)$"
MEMORY_PATTERN = r"^[0-9]{1,12}[bkmg]?$"
CPUS_PATTERN = r"^[0-9]{1,4}(?:\.[0-9]{1,4})?$"

STACK_SLUG_RE = re.compile(STACK_SLUG_PATTERN)
PROJECT_NAME_RE = re.compile(PROJECT_NAME_PATTERN)
REVISION_HASH_RE = re.compile(REVISION_HASH_PATTERN)
CONTAINER_ID_RE = re.compile(CONTAINER_ID_PATTERN)
IMAGE_ID_RE = re.compile(IMAGE_ID_PATTERN)
NETWORK_ID_RE = re.compile(NETWORK_ID_PATTERN)
VOLUME_NAME_RE = re.compile(VOLUME_NAME_PATTERN)
RENAME_RE = re.compile(RENAME_PATTERN)
USER_RE = re.compile(USER_PATTERN)
WORKDIR_RE = re.compile(WORKDIR_PATTERN)
RESTART_POLICY_RE = re.compile(RESTART_POLICY_PATTERN)
MEMORY_RE = re.compile(MEMORY_PATTERN)
CPUS_RE = re.compile(CPUS_PATTERN)

EXEC_SHELLS = {"sh", "bash", "ash"}
CONTAINER_LIFECYCLE_ACTIONS = {
    "container.start",
    "container.stop",
    "container.restart",
    "container.pause",
    "container.unpause",
    "container.kill",
}
CONTAINER_ACTIONS = CONTAINER_LIFECYCLE_ACTIONS | {
    "container.rename",
    "container.remove",
    "container.update",
    "container.logs",
    "container.inspect",
    "container.exec_one_shot",
}
IMAGE_ACTIONS = {"image.remove"}
VOLUME_ACTIONS = {"volume.remove"}
NETWORK_ACTIONS = {"network.remove"}
CLEANUP_ACTIONS = {"cleanup"}
COMPOSE_ACTIONS = {"compose.up", "compose.down", "compose.restart", "compose.pull"}
DOCKER_ACTIONS = (
    CONTAINER_ACTIONS
    | IMAGE_ACTIONS
    | VOLUME_ACTIONS
    | NETWORK_ACTIONS
    | CLEANUP_ACTIONS
    | COMPOSE_ACTIONS
    | {"inventory.refresh"}
)
DOCKER_MUTATION_ACTIONS = (
    CONTAINER_LIFECYCLE_ACTIONS
    | {"container.rename", "container.remove", "container.update", "container.exec_one_shot"}
    | IMAGE_ACTIONS
    | VOLUME_ACTIONS
    | NETWORK_ACTIONS
    | CLEANUP_ACTIONS
    | COMPOSE_ACTIONS
)
DOCKER_CLEANUP_SCOPES = {
    "stopped_containers",
    "dangling_images",
    "unused_images",
    "unused_volumes",
    "unused_networks",
    "build_cache",
}
DOCKER_LOG_TAILS = {100, 500, 1000}


def _text(args: dict[str, Any], key: str, limit: int = 255) -> str:
    value = str(args.get(key, "") or "").strip()
    return value[:limit]


def _check_keys(args: dict[str, Any], allowed: set[str]) -> None:
    extra = set(args) - allowed
    if extra:
        raise ValueError(f"unsupported Docker action field: {sorted(extra)[0]}")


def _required_container_id(args: dict[str, Any], key: str = "container_id") -> str:
    value = _text(args, key, 64)
    if not CONTAINER_ID_RE.match(value):
        raise ValueError(f"{key} must be a 12-64 character hex Docker container ID")
    return value


def normalize_docker_command_args(args: dict[str, Any]) -> dict[str, Any]:
    """Normalize direct `docker` command args and reject unsafe fields.

    Browser action models validate separately. This function is the second gate
    used by ``POST /api/agents/{id}/commands`` so callers cannot bypass the
    action-specific request models with a hand-written command payload.
    """
    if not isinstance(args, dict):
        raise ValueError("Docker command args must be an object")
    action = str(args.get("action", "")).lower()
    if action == "inventory.refresh":
        _check_keys(args, {"action"})
        return {"action": action}
    if action in CONTAINER_LIFECYCLE_ACTIONS:
        _check_keys(args, {"action", "container_id"})
        return {"action": action, "container_id": _required_container_id(args)}
    if action == "container.rename":
        _check_keys(args, {"action", "container_id", "new_name"})
        new_name = _text(args, "new_name", 128)
        if not RENAME_RE.match(new_name):
            raise ValueError("new_name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
        return {
            "action": action,
            "container_id": _required_container_id(args),
            "new_name": new_name,
        }
    if action == "container.remove":
        _check_keys(args, {"action", "container_id", "force", "remove_volumes"})
        return {
            "action": action,
            "container_id": _required_container_id(args),
            "force": bool(args.get("force", False)),
            "remove_volumes": bool(args.get("remove_volumes", False)),
        }
    if action == "container.update":
        _check_keys(
            args,
            {"action", "container_id", "restart_policy", "memory", "cpus", "pids_limit"},
        )
        restart_policy = _text(args, "restart_policy", 64)
        memory = _text(args, "memory", 32).lower()
        cpus = _text(args, "cpus", 16)
        pids_limit = args.get("pids_limit")
        if restart_policy and not RESTART_POLICY_RE.match(restart_policy):
            raise ValueError("invalid restart policy")
        if memory and not MEMORY_RE.match(memory):
            raise ValueError("memory must be a Docker memory value such as 512m or 1g")
        if cpus and not CPUS_RE.match(cpus):
            raise ValueError("cpus must be a positive number such as 0.5 or 2")
        if pids_limit is not None:
            try:
                pids_limit = int(pids_limit)
            except (TypeError, ValueError) as exc:
                raise ValueError("pids_limit must be an integer") from exc
            if not 0 <= pids_limit <= 1_000_000:
                raise ValueError("pids_limit must be between 0 and 1000000")
        if not restart_policy and not memory and not cpus and pids_limit is None:
            raise ValueError("container.update requires at least one limit")
        return {
            "action": action,
            "container_id": _required_container_id(args),
            "restart_policy": restart_policy,
            "memory": memory,
            "cpus": cpus,
            "pids_limit": pids_limit,
        }
    if action == "container.logs":
        _check_keys(args, {"action", "container_id", "tail", "timestamps"})
        try:
            tail = int(args.get("tail", 500))
        except (TypeError, ValueError) as exc:
            raise ValueError("tail must be 100, 500, or 1000") from exc
        if tail not in DOCKER_LOG_TAILS:
            raise ValueError("tail must be 100, 500, or 1000")
        return {
            "action": action,
            "container_id": _required_container_id(args),
            "tail": tail,
            "timestamps": bool(args.get("timestamps", False)),
        }
    if action == "container.inspect":
        _check_keys(args, {"action", "container_id"})
        return {"action": action, "container_id": _required_container_id(args)}
    if action == "container.exec_one_shot":
        _check_keys(
            args,
            {"action", "container_id", "shell", "user", "workdir", "command", "timeout", "tty"},
        )
        shell = _text(args, "shell", 16).lower()
        if shell not in EXEC_SHELLS:
            raise ValueError("shell must be sh, bash, or ash")
        user = _text(args, "user", 32)
        if user and not USER_RE.match(user):
            raise ValueError("invalid exec user")
        workdir = _text(args, "workdir", 256)
        if workdir and not WORKDIR_RE.match(workdir):
            raise ValueError("workdir must be an absolute path")
        command = str(args.get("command", "") or "")
        if not command.strip() or len(command) > 8000 or "\x00" in command:
            raise ValueError("command is required and must be at most 8000 characters")
        try:
            timeout = int(args.get("timeout", 60))
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout must be between 5 and 300 seconds") from exc
        if not 5 <= timeout <= 300:
            raise ValueError("timeout must be between 5 and 300 seconds")
        return {
            "action": action,
            "container_id": _required_container_id(args),
            "shell": shell,
            "user": user,
            "workdir": workdir,
            "command": command,
            "timeout": timeout,
            "tty": bool(args.get("tty", False)),
        }
    if action == "image.remove":
        _check_keys(args, {"action", "image_id"})
        image_id = _text(args, "image_id", 128)
        if not IMAGE_ID_RE.match(image_id):
            raise ValueError("invalid Docker image ID")
        return {"action": action, "image_id": image_id}
    if action == "volume.remove":
        _check_keys(args, {"action", "volume_name"})
        volume_name = _text(args, "volume_name", 255)
        if not VOLUME_NAME_RE.match(volume_name):
            raise ValueError("invalid Docker volume name")
        return {"action": action, "volume_name": volume_name}
    if action == "network.remove":
        _check_keys(args, {"action", "network_id"})
        network_id = _text(args, "network_id", 64)
        if not NETWORK_ID_RE.match(network_id):
            raise ValueError("invalid Docker network ID")
        return {"action": action, "network_id": network_id}
    if action in COMPOSE_ACTIONS:
        _check_keys(
            args,
            {"action", "deployment_id", "stack_id", "revision_hash", "project_name"},
        )
        deployment_id = _text(args, "deployment_id", 64)
        stack_id = _text(args, "stack_id", 64)
        revision_hash = _text(args, "revision_hash", 64)
        project_name = _text(args, "project_name", 63)
        if not re.match(r"^dep_[A-Za-z0-9]+$", deployment_id):
            raise ValueError("invalid deployment ID")
        if not re.match(r"^stk_[A-Za-z0-9]+$", stack_id):
            raise ValueError("invalid stack ID")
        if not REVISION_HASH_RE.match(revision_hash):
            raise ValueError("invalid revision hash")
        if not PROJECT_NAME_RE.match(project_name):
            raise ValueError("invalid Compose project name")
        return {
            "action": action,
            "deployment_id": deployment_id,
            "stack_id": stack_id,
            "revision_hash": revision_hash,
            "project_name": project_name,
        }
    if action == "cleanup":
        _check_keys(args, {"action", "scope", "include_compose"})
        scope = _text(args, "scope", 64).lower()
        if scope not in DOCKER_CLEANUP_SCOPES:
            raise ValueError("unsupported cleanup scope")
        return {
            "action": action,
            "scope": scope,
            "include_compose": bool(args.get("include_compose", False)),
        }
    raise ValueError("unsupported Docker action")
