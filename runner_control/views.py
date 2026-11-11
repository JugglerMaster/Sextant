from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any
from urllib.parse import quote

from .config import Settings
from .db import dumps, loads, parse_iso

SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@:+][A-Za-z0-9_.@:+ -]{0,254}$")
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:@-]{0,127}$")
PACKAGE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:~-]{0,127}$")
PACKAGE_MANAGERS = {"apt", "dnf", "winget", "choco"}
PACKAGE_ACTIONS = {"install", "remove", "upgrade"}
SHELL_VALUES = {"bash", "powershell", "cmd"}
POWER_ACTIONS = {"shutdown", "reboot", "cancel"}
SERVICE_ACTIONS = {"start", "stop", "restart", "enable", "disable", "status"}
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


def role_at_least(user: dict[str, Any], role: str) -> bool:
    return ROLE_RANK.get(user.get("role", ""), 0) >= ROLE_RANK.get(role, 99)


def validate_command_args(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    if kind == "shell":
        shell = str(args.get("shell", "bash")).lower()
        command = str(args.get("command", ""))
        if shell not in SHELL_VALUES:
            raise ValueError("shell must be bash, powershell, or cmd")
        if not command.strip():
            raise ValueError("command is required")
        if len(command) > 8000:
            raise ValueError("command is too long (max 8000 characters)")
        timeout = int(args.get("timeout", 60))
        if not 5 <= timeout <= 300:
            raise ValueError("timeout must be between 5 and 300 seconds")
        return {"shell": shell, "command": command, "timeout": timeout}

    if kind == "power":
        action = str(args.get("action", "")).lower()
        if action not in POWER_ACTIONS:
            raise ValueError("power action must be shutdown, reboot, or cancel")
        delay = int(args.get("delay", 30))
        if not 0 <= delay <= 600:
            raise ValueError("delay must be between 0 and 600 seconds")
        return {"action": action, "delay": delay}

    if kind == "service":
        action = str(args.get("action", "")).lower()
        name = str(args.get("name", "")).strip()
        if action not in SERVICE_ACTIONS:
            raise ValueError("service action must be start, stop, restart, enable, disable, or status")
        if not SERVICE_NAME_RE.match(name):
            raise ValueError("invalid service name")
        return {"action": action, "name": name}

    if kind == "refresh":
        return {}

    if kind == "package":
        actions = args.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("package actions are required")
        if len(actions) > 25:
            raise ValueError("at most 25 package actions are allowed per job")
        normalized: list[dict[str, str]] = []
        for item in actions:
            if not isinstance(item, dict):
                raise ValueError("each package action must be an object")
            manager = str(item.get("manager", "")).lower()
            action = str(item.get("action", "")).lower()
            package = str(item.get("package", "")).strip()
            version = str(item.get("version") or "").strip()
            if manager not in PACKAGE_MANAGERS:
                raise ValueError("unsupported package manager")
            if action not in PACKAGE_ACTIONS:
                raise ValueError("package action must be install, remove, or upgrade")
            if not PACKAGE_NAME_RE.match(package):
                raise ValueError("invalid package name")
            if version and not PACKAGE_VERSION_RE.match(version):
                raise ValueError("invalid package version")
            normalized.append({"manager": manager, "action": action, "package": package, "version": version})
        return {"actions": normalized}

    raise ValueError("unsupported command kind")


def _alert_key_part(value: Any) -> str:
    """Encode a value so an alert key is safe inside a single URL path segment."""
    return quote(str(value), safe="").replace("%", "!")


def _agent_alerts(disks: list[dict], gpus: list[dict], services: list[dict], mem_pct: float) -> list[dict]:
    alerts: list[dict] = []
    for disk in disks:
        mount = disk.get("mount") or "?"
        pct = float(disk.get("percent") or 0)
        key = f"disk:{_alert_key_part(mount)}"
        if pct >= 90:
            alerts.append({"key": key, "level": "critical", "message": f"Disk {mount} is {pct:.0f}% full"})
        elif pct >= 80:
            alerts.append({"key": key, "level": "warning", "message": f"Disk {mount} is {pct:.0f}% full"})
    if mem_pct >= 90:
        alerts.append({"key": "memory", "level": "critical", "message": f"Memory is {mem_pct:.0f}% used"})
    elif mem_pct >= 80:
        alerts.append({"key": "memory", "level": "warning", "message": f"Memory is {mem_pct:.0f}% used"})
    for index, gpu in enumerate(gpus):
        temp = float(gpu.get("temp_c") or 0)
        if temp >= 85:
            alerts.append(
                {
                    "key": f"gpu:{index}",
                    "level": "warning",
                    "message": f"GPU {gpu.get('name') or ''} is {temp:.0f} C",
                }
            )
    for service in services:
        if service.get("watched") and str(service.get("status", "")).lower() not in {"running", "active"}:
            name = service.get("name") or "?"
            alerts.append(
                {
                    "key": f"service:{_alert_key_part(name)}",
                    "level": "critical",
                    "message": f"Monitored service {name} is not running",
                }
            )
    return alerts


def _decorate_alerts(
    alerts: list[dict],
    states: dict[str, Any] | None,
    now: datetime,
) -> list[dict]:
    states = states or {}
    decorated: list[dict] = []
    for alert in alerts:
        state = states.get(alert["key"])
        acknowledged = bool(state and state["acknowledged_at"])
        snoozed_until = state["snoozed_until"] if state is not None else None
        until = parse_iso(snoozed_until)
        snoozed = bool(until is not None and until > now)
        decorated.append(
            {
                **alert,
                "acknowledged": acknowledged,
                "snoozed": snoozed,
                "snoozed_until": snoozed_until if snoozed else None,
            }
        )
    return decorated


def agent_to_dict(
    row: sqlite3.Row,
    settings: Settings,
    now: datetime | None = None,
    alert_states: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    last_seen = parse_iso(row["last_seen"])
    online = bool(last_seen and (now - last_seen).total_seconds() <= settings.offline_after_seconds)
    disks = loads(row["disks"], [])
    gpus = loads(row["gpus"], [])
    services = loads(row["services"], [])
    startup = loads(row["startup"], [])
    mem_pct = float(row["mem_pct"] or 0)
    alerts = _decorate_alerts(_agent_alerts(disks, gpus, services, mem_pct), alert_states, now)
    active_alerts = [alert for alert in alerts if not alert["acknowledged"] and not alert["snoozed"]]
    watch_services = loads(row["watch_services"], [])
    policy = loads(row["policy"], {})
    if not isinstance(watch_services, list):
        watch_services = []
    if not isinstance(policy, dict):
        policy = {}
    policy = {
        "allow_shell": bool(policy.get("allow_shell", True)),
        "allow_power": bool(policy.get("allow_power", True)),
        "allow_service": bool(policy.get("allow_service", True)),
        "allow_packages": bool(policy.get("allow_packages", True)),
    }
    return {
        "id": row["id"],
        "name": row["name"],
        "hostname": row["hostname"],
        "machine_id": row["machine_id"],
        "os": row["os"],
        "os_version": row["os_version"],
        "arch": row["arch"],
        "ip": row["ip"],
        "mac": row["mac"],
        "agent_version": row["agent_version"],
        "tags": loads(row["tags"], []),
        "notes": row["notes"],
        "watch_services": watch_services,
        "policy": policy,
        "profile_id": row["profile_id"],
        "enrolled_at": row["enrolled_at"],
        "last_seen": row["last_seen"],
        "service_inventory_at": row["service_inventory_at"],
        "online": online,
        "cpu_pct": round(float(row["cpu_pct"] or 0), 1),
        "mem_pct": round(mem_pct, 1),
        "mem_total": int(row["mem_total"] or 0),
        "mem_used": int(row["mem_used"] or 0),
        "uptime_seconds": int(row["uptime_seconds"] or 0),
        "disks": disks,
        "gpus": gpus,
        "services": services,
        "startup": startup,
        "extra": loads(row["extra"], {}),
        "alert_count": len(active_alerts),
        "alerts": alerts,
    }


def telemetry_sample_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    def optional_float(key: str) -> float | None:
        value = row[key]
        return None if value is None else round(float(value), 1)

    return {
        "at": row["at"],
        "cpu_pct": optional_float("cpu_pct"),
        "mem_pct": optional_float("mem_pct"),
        "disk_max_pct": optional_float("disk_max_pct"),
        "gpu_max_util": optional_float("gpu_max_util"),
        "gpu_max_temp": optional_float("gpu_max_temp"),
        "service_down": int(row["service_down"] or 0),
    }


def command_to_dict(row: sqlite3.Row, full_output: bool = True) -> dict[str, Any]:
    stdout = row["stdout"] or ""
    stderr = row["stderr"] or ""
    truncated = False
    if not full_output:
        preview_limit = 4000
        if len(stdout) > preview_limit:
            stdout = stdout[:preview_limit] + "\n…[history preview; fetch the command for full output]"
            truncated = True
        if len(stderr) > preview_limit:
            stderr = stderr[:preview_limit] + "\n…[history preview; fetch the command for full output]"
            truncated = True
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "kind": row["kind"],
        "args": loads(row["args"], {}),
        "status": row["status"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "dispatched_at": row["dispatched_at"],
        "completed_at": row["completed_at"],
        "exit_code": row["exit_code"],
        "stdout": stdout,
        "stderr": stderr,
        "error": row["error"],
        "output_truncated": truncated,
    }


def user_to_dict(row: sqlite3.Row, session_count: int | None = None) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row["email"],
        "role": row["role"],
        "source": row["source"],
        "active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }
    if session_count is not None:
        result["session_count"] = session_count
    return result


def session_to_dict(row: sqlite3.Row, current_session: str | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
        "expires_at": row["expires_at"],
        "ip": row["ip"],
        "user_agent": row["user_agent"],
        "current": bool(current_session and row["id"] == current_session),
    }


def wake_target_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "mac": row["mac"],
        "ip": row["ip"],
        "broadcast": row["broadcast"],
        "port": row["port"],
        "created_at": row["created_at"],
        "last_woken_at": row["last_woken_at"],
    }


def store_package_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "vendor": row["vendor"],
        "description": row["description"],
        "kind": row["kind"],
        "platforms": loads(row["platforms"], []),
        "created_at": row["created_at"],
        "archived_at": row["archived_at"],
    }


def store_release_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "package_id": row["package_id"],
        "version": row["version"],
        "artifact_type": row["artifact_type"],
        "filename": row["filename"],
        "size": row["size"],
        "sha256": row["sha256"],
        "channel": row["channel"],
        "min_version": row["min_version"],
        "mandatory": bool(row["mandatory"]),
        "notes_url": row["notes_url"],
        "published_at": row["published_at"],
        "published_by": row["published_by"],
        "yanked_at": row["yanked_at"],
        "yanked_reason": row["yanked_reason"],
    }


def bounded_json(value: Any, limit: int = 10_000) -> str:
    encoded = dumps(value)
    if len(encoded) <= limit:
        return encoded
    return dumps({"truncated": True})
