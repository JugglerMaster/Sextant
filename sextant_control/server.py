from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import csv
import hashlib
import hmac
import io
import ipaddress
import logging
import re
import secrets
import shlex
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from . import remote_install
from . import selfupdate
from . import updates
from . import windows_remote
from .config import Settings
from .db import Database, DEFAULT_PROFILES, dumps, loads, parse_iso, utcnow, utcnow_iso
from .discovery import (
    ACKNOWLEDGEMENT,
    DeviceObservation,
    DiscoveryBusy,
    DiscoveryError,
    DiscoveryManager,
    DiscoveryRateLimited,
    detect_identity_conflicts,
    expand_targets,
    merge_observations,
    parse_scope,
    segment_for_ip,
    validate_allowlist,
    validate_excludes,
    validate_methods,
    validate_network,
    verify_reachability,
)
from .discovery_imports import (
    MAX_IMPORT_DEVICES,
    DiscoveryImportError,
    parse_import,
)
from .discovery_integrations import (
    INTEGRATION_KINDS,
    IntegrationError,
    IntegrationSecretStore,
    adapter_descriptor,
    fetch_adapter_devices,
    secret_fields,
    test_adapter,
    validate_integration_config,
)
from .docker_console import ConsoleBroker, ConsoleError
from .docker_rules import DOCKER_MUTATION_ACTIONS
from .docker_store import ComposeLibrary, DockerStoreError
from .ldap_auth import authenticate_ldap, probe_ldap
from .notifications import NotificationError, NotificationService
from .system_settings import (
    apply_overrides as apply_system_overrides,
    build_ldap_settings,
    normalize_patch as normalize_system_patch,
    serialize as serialize_system_settings,
    ssh_key_info,
    write_bind_password,
    write_ssh_private_key,
)
from .models import (
    AdminPasswordResetIn,
    AgentEnrollIn,
    AgentProfileIn,
    AgentProfilePatchIn,
    AgentUpdateIn,
    AgentWakeIn,
    AlertSnoozeIn,
    BulkActionIn,
    CommandIn,
    CommandOutputIn,
    CommandResultIn,
    DockerActionIn,
    DockerCleanupIn,
    DockerConsoleClosedIn,
    DockerConsoleInputIn,
    DockerConsoleOpenIn,
    DockerConsoleOutputIn,
    DockerConsoleResizeIn,
    DockerDeployIn,
    DockerExecIn,
    DockerInventoryIn,
    DockerSettingsIn,
    DiscoveryDevicePatchIn,
    DiscoveryImportIn,
    DiscoveryIntegrationIn,
    DiscoveryLinkAgentIn,
    DiscoveryPromoteIn,
    DiscoveryReportIn,
    DiscoveryScanIn,
    DiscoverySettingsIn,
    DiscoveryWakeTargetIn,
    DockerStackIn,
    DockerStackPatchIn,
    EnrollmentTokenIn,
    HeartbeatIn,
    LoginIn,
    PackageRuleIn,
    PasswordChangeIn,
    ProfileTagIn,
    RemoteInstallIn,
    SavedViewIn,
    StorePackageIn,
    UpdateApplyIn,
    UserCreateIn,
    UserUpdateIn,
    WakeTargetIn,
    WakeTargetWakeIn,
)
from .snmp import (
    SNMPError,
    redact_snmp_error,
    snmp_device,
    snmp_get,
    snmp_status,
    target_ips,
)
from .security import (
    LoginRateLimiter,
    client_ip,
    decode_session,
    hash_password,
    hash_token,
    make_session_payload,
    new_token,
    sign_session,
    token_matches,
    validate_password,
    verify_password,
    verify_password_dummy,
)
from .store import ArtifactStore, StoreError
from .views import (
    _agent_alerts,
    agent_event_to_dict,
    agent_to_dict,
    bounded_json,
    command_to_dict,
    docker_container_to_dict,
    docker_deployment_to_dict,
    docker_host_to_dict,
    docker_image_to_dict,
    docker_network_to_dict,
    docker_stack_revision_to_dict,
    docker_stack_to_dict,
    docker_volume_to_dict,
    discovered_device_to_dict,
    discovery_event_to_dict,
    discovery_scan_to_dict,
    role_at_least,
    session_to_dict,
    store_package_to_dict,
    store_release_to_dict,
    telemetry_sample_to_dict,
    user_to_dict,
    validate_command_args,
    wake_target_to_dict,
)
from .wol import normalize_mac, send_magic_packet

logger = logging.getLogger("sextant_control")

SSE_HEARTBEAT_SECONDS = 15
DASHBOARD_SAFETY_REFRESH_SECONDS = 10
DASHBOARD_AUTH_RECHECK_SECONDS = 5
DASHBOARD_CACHE_MAX_AGE_SECONDS = 9
COMMAND_STREAM_SECONDS = 1
COMMAND_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
COMMAND_HISTORY_STATUSES = {"queued", "dispatched", "running", "succeeded", "failed", "cancelled"}
COMMAND_HISTORY_DEFAULT_LIMIT = 50
COMMAND_HISTORY_MAX_LIMIT = 200
COMMAND_OUTPUT_CHUNK_BYTES = 64 * 1024
COMMAND_OUTPUT_REPLAY_LIMIT = 2000
COMMAND_OUTPUT_POLL_LIMIT = 500
BULK_ACTION_MAX_AGENTS = 200
BULK_ACTIONS = {
    "refresh",
    "wake",
    "reboot",
    "shutdown",
    "service_start",
    "service_stop",
    "service_restart",
    "profile_apply",
}
SAVED_VIEW_STATUSES = {"all", "online", "offline", "alerts"}


def _timestamp_filter(value: str, field_name: str) -> str | None:
    """Normalize an optional ISO timestamp to the format stored in SQLite."""
    value = (value or "").strip()
    if not value:
        return None
    parsed = parse_iso(value)
    if parsed is None:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a valid ISO 8601 timestamp.",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _command_history_filters(
    limit: int | None,
    offset: int,
    status: str,
    query: str,
    since: str,
    until: str,
) -> tuple[int | None, int, str | None, str | None, str | None, str | None]:
    if limit is not None and not 1 <= int(limit) <= COMMAND_HISTORY_MAX_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be between 1 and {COMMAND_HISTORY_MAX_LIMIT}.",
        )
    if int(offset) < 0:
        raise HTTPException(status_code=400, detail="offset must be zero or greater.")
    normalized_status = (status or "").strip().lower()
    if normalized_status == "all":
        normalized_status = ""
    if normalized_status and normalized_status not in COMMAND_HISTORY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="status must be one of " + ", ".join(sorted(COMMAND_HISTORY_STATUSES)) + ".",
        )
    normalized_query = (query or "").strip() or None
    since_value = _timestamp_filter(since, "since")
    until_value = _timestamp_filter(until, "until")
    if since_value and until_value and since_value > until_value:
        raise HTTPException(status_code=400, detail="since must not be after until.")
    return (
        None if limit is None else int(limit),
        int(offset),
        normalized_status or None,
        normalized_query,
        since_value,
        until_value,
    )


def _command_csv_description(row: sqlite3.Row) -> str:
    args = loads(row["args"], {})
    if not isinstance(args, dict):
        args = {}
    kind = str(row["kind"])
    if kind == "shell":
        return f"{args.get('shell', 'shell')}: {args.get('command', '')}"
    if kind == "power":
        return f"power: {args.get('action', '?')}"
    if kind == "service":
        return f"service: {args.get('action', '?')} {args.get('name', '')}".strip()
    if kind == "refresh":
        return "refresh telemetry"
    if kind == "docker":
        target = args.get("container_id") or args.get("image_id") or args.get("volume_name") or args.get("network_id") or args.get("scope") or ""
        return f"docker: {args.get('action', '?')}{f' {target}' if target else ''}"
    return kind


def _sse_event(event: str, payload: Any) -> str:
    """Format one Server-Sent Event. JSON never contains a raw newline."""
    return f"event: {event}\ndata: {dumps(payload)}\n\n"


def _sse_comment(text: str = "keep-alive") -> str:
    return f": {text}\n\n"


class _DashboardState:
    """In-process revision counter and shared snapshot cache.

    ``notify`` is safe to call from FastAPI threadpool workers. The cache and
    build lock are shared by every SSE client in this process; concurrent
    clients at the same revision wait for one dashboard build instead of
    rebuilding independently. This is deliberately single-node state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0
        self._cached_revision: int | None = None
        self._cached_payload: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._listeners: dict[int, tuple[asyncio.Event, asyncio.AbstractEventLoop]] = {}
        self._next_listener = 0
        self._build_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def notify(self) -> None:
        """Mark dashboard state dirty and wake every waiting stream."""
        with self._lock:
            self._revision += 1
            listeners = list(self._listeners.values())
        for event, loop in listeners:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # The stream's event loop has already closed.
                pass

    def subscribe(self) -> tuple[int, asyncio.Event]:
        event = asyncio.Event()
        loop = asyncio.get_running_loop()
        with self._lock:
            key = self._next_listener
            self._next_listener += 1
            self._listeners[key] = (event, loop)
        return key, event

    def unsubscribe(self, key: int) -> None:
        with self._lock:
            self._listeners.pop(key, None)

    def _build_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._lock:
            lock = self._build_locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._build_locks[loop] = lock
            return lock

    async def payload(
        self,
        revision: int,
        build: Any,
        *,
        max_age: float | None = None,
    ) -> dict[str, Any]:
        """Return a cached or freshly built payload for ``revision``.

        ``max_age`` limits how old a cached payload may be. Change-driven
        events pass ``None`` (any payload for that revision is valid); the
        periodic safety refresh passes the cache age limit so time-derived
        fields are still rebuilt without every client forcing a build.
        """
        with self._lock:
            cached_revision = self._cached_revision
            cached_payload = self._cached_payload
            cached_at = self._cached_at
        if cached_revision == revision and cached_payload is not None:
            if max_age is None or time.monotonic() - cached_at <= max_age:
                return cached_payload
        async with self._build_lock():
            with self._lock:
                cached_revision = self._cached_revision
                cached_payload = self._cached_payload
                cached_at = self._cached_at
            if cached_revision == revision and cached_payload is not None:
                if max_age is None or time.monotonic() - cached_at <= max_age:
                    return cached_payload
            payload = await asyncio.to_thread(build)
            with self._lock:
                self._cached_revision = revision
                self._cached_payload = payload
                self._cached_at = time.monotonic()
            return payload


def _ensure_bootstrap_admin(settings: Settings, db: Database) -> None:
    if db.count_users() > 0:
        return
    password = settings.admin_password
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(18)
    else:
        problem = validate_password(password)
        if problem:
            raise RuntimeError(
                f"SEXTANT_ADMIN_PASSWORD is not acceptable: {problem} "
                "Remove it to auto-generate a strong bootstrap password instead."
            )
    assert password is not None
    db.create_user(
        username=settings.admin_user,
        role="admin",
        password_hash=hash_password(password),
        display_name=settings.admin_user,
        source="local",
    )
    if generated:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        file = settings.bootstrap_file
        file.write_text(
            "Sextant bootstrap administrator\n"
            f"Username: {settings.admin_user}\n"
            f"Password: {password}\n\n"
            "Sign in and change this password immediately, then delete this file.\n",
            encoding="utf-8",
        )
        try:
            file.chmod(0o600)
        except OSError:
            pass
        logger.warning("Created bootstrap admin at %s. Read the password from that file.", file)
    else:
        logger.warning("Created administrator %r from SEXTANT_ADMIN_PASSWORD.", settings.admin_user)


def _public_settings(settings: Settings) -> dict[str, Any]:
    return {
        "ldap_enabled": settings.ldap is not None,
        "local_auth": True,
        "offline_after_seconds": settings.offline_after_seconds,
        "offline_command_policy": settings.offline_command_policy,
        "max_queued_commands_per_agent": settings.max_queued_commands_per_agent,
        "ssh_install_enabled": settings.ssh_install_enabled,
        "windows_install_enabled": settings.windows_install_enabled,
        "version": __version__,
        "public_url": settings.public_url,
        "update": {
            "check_interval_hours": settings.update_check_hours,
            "feed_configured": bool(settings.update_feed),
        },
        "notifications": {
            "enabled": settings.notify_enabled,
            "telegram_enabled": settings.telegram_enabled,
            "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
            "telegram_events": list(settings.telegram_events),
            "telegram_min_severity": settings.telegram_min_severity,
            "telegram_recovery": settings.telegram_recovery,
            "interval_seconds": settings.notify_interval_seconds,
        },
        "docker": {
            "enabled": settings.docker_enabled,
            "inventory_stale_seconds": settings.docker_inventory_stale_seconds,
            "inventory_max_bytes": settings.docker_inventory_max_bytes,
        },
    }


def _powershell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _discovery_install_command(
    base_url: str, token: str, os_name: str, name: str, address: str
) -> str:
    """Build a one-time install command. The caller must never log the result."""
    display_name = name or address or "discovered-device"
    if os_name == "windows":
        return (
            f"# Run in an elevated PowerShell on {address or 'the target host'}.\n"
            f"& 'C:\\ProgramData\\SextantAgent\\current\\Scripts\\sextant-agent.exe' enroll "
            f"--config 'C:\\ProgramData\\SextantAgent\\config.json' "
            f"--server {_powershell_quote(base_url)} --token {_powershell_quote(token)} "
            f"--name {_powershell_quote(display_name)} --profile workstation"
        )
    return (
        f"# Run on {address or 'the target host'} as root.\n"
        f"sudo /opt/sextant-agent/.venv/bin/sextant-agent enroll "
        f"--config /etc/sextant-agent/config.json "
        f"--server {shlex.quote(base_url)} --token {shlex.quote(token)} "
        f"--name {shlex.quote(display_name)} --profile workstation"
    )


def _session_cookie(response: Response, settings: Settings, payload: dict[str, Any]) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=sign_session(payload, settings.secret_key),
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _new_session_id() -> str:
    return "ses_" + secrets.token_hex(16)


def _session_expiry(settings: Settings) -> str:
    expires = utcnow() + timedelta(seconds=settings.session_hours * 3600)
    return expires.isoformat(timespec="seconds").replace("+00:00", "Z")


def _request_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


def _validate_broadcast(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ValueError as exc:
        raise ValueError("broadcast must be an IPv4 address") from exc


def _find_agent_alert(
    row: sqlite3.Row,
    settings: Settings,
    alert_key: str,
    alert_states: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for alert in agent_to_dict(row, settings, alert_states=alert_states)["alerts"]:
        if alert["key"] == alert_key:
            return alert
    return None


def _alert_map(row: Any) -> dict[str, dict[str, Any]]:
    disks = loads(row["disks"], [])
    gpus = loads(row["gpus"], [])
    services = loads(row["services"], [])
    if not isinstance(disks, list):
        disks = []
    if not isinstance(gpus, list):
        gpus = []
    if not isinstance(services, list):
        services = []
    alerts = _agent_alerts(disks, gpus, services, float(row["mem_pct"] or 0))
    return {str(alert["key"]): alert for alert in alerts}


def _reconcile_agent_alerts(
    database: Database,
    row: sqlite3.Row,
    config: Settings,
    now: datetime,
    conn: sqlite3.Connection,
    at: str | None = None,
) -> dict[str, sqlite3.Row]:
    """Reconcile the current telemetry-derived alerts with incident state."""
    allow_clear = bool(row["last_seen"])
    return database.reconcile_alert_states(
        row["id"],
        _alert_map(row),
        now=now,
        at=at,
        ack_ttl_hours=config.alert_ack_ttl_hours,
        conn=conn,
        allow_clear=allow_clear,
    )


def _service_map(services: Any) -> dict[str, tuple[str, str, bool]]:
    result: dict[str, tuple[str, str, bool]] = {}
    if not isinstance(services, list):
        return result
    for service in services:
        if not isinstance(service, dict):
            continue
        name = str(service.get("name") or "").strip()
        if not name:
            continue
        result[name.casefold()] = (
            name,
            str(service.get("status") or "unknown"),
            bool(service.get("watched")),
        )
    return result


def _package_snapshot(rows: list[sqlite3.Row]) -> dict[tuple[str, str], str]:
    snapshot: dict[tuple[str, str], str] = {}
    for row in rows:
        package = str(row["package"] or "")
        if not package or package == "__manager__":
            continue
        snapshot[(str(row["manager"] or ""), package)] = str(row["version"] or "")
    return snapshot


def _package_label(key: tuple[str, str], version: str) -> str:
    manager, package = key
    label = f"{manager}:{package}" if manager else package
    return f"{label} {version}".strip()


def _package_change_events(
    before: dict[tuple[str, str], str], after: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    added = sorted(key for key in after if key not in before)
    removed = sorted(key for key in before if key not in after)
    changed = sorted(
        key for key in before.keys() & after.keys() if before[key] != after[key]
    )
    if added:
        labels = [_package_label(key, after[key]) for key in added[:10]]
        title = (
            f"Package added: {labels[0]}"
            if len(added) == 1
            else f"{len(added)} packages added"
        )
        events.append(
            {
                "kind": "package",
                "severity": "info",
                "title": title,
                "detail": {"action": "added", "count": len(added), "packages": labels, "truncated": len(added) > 10},
            }
        )
    if removed:
        labels = [_package_label(key, before[key]) for key in removed[:10]]
        title = (
            f"Package removed: {labels[0]}"
            if len(removed) == 1
            else f"{len(removed)} packages removed"
        )
        events.append(
            {
                "kind": "package",
                "severity": "info",
                "title": title,
                "detail": {"action": "removed", "count": len(removed), "packages": labels, "truncated": len(removed) > 10},
            }
        )
    if changed:
        labels = [_package_label(key, after[key]) for key in changed[:10]]
        title = (
            f"Package version changed: {labels[0]}"
            if len(changed) == 1
            else f"{len(changed)} package versions changed"
        )
        detail = [
            {
                "manager": key[0],
                "package": key[1],
                "from": before[key],
                "to": after[key],
            }
            for key in changed[:10]
        ]
        events.append(
            {
                "kind": "package",
                "severity": "info",
                "title": title,
                "detail": {"action": "version_changed", "count": len(changed), "changes": detail, "truncated": len(changed) > 10},
            }
        )
    return events


def _service_change_events(
    old_services: Any,
    new_services: Any,
    watched_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    before = _service_map(old_services)
    after = _service_map(new_services)
    watched_names = watched_names or set()
    events: list[dict[str, Any]] = []
    for key, (name, status, watched) in after.items():
        if not watched and key not in watched_names:
            continue
        if key not in before:
            continue
        previous = before[key][1]
        if previous.casefold() == status.casefold():
            continue
        healthy = status.casefold() in {"running", "active"}
        events.append(
            {
                "kind": "service",
                "severity": "success" if healthy else "critical",
                "title": f"Service {name} {status}",
                "detail": {"service": name, "from": previous, "to": status},
            }
        )
        if len(events) >= 20:
            break
    return events


def _record_agent_events(database: Database, events: list[dict[str, Any]]) -> None:
    for event in events:
        try:
            database.record_agent_event(**event)
        except Exception:  # best-effort: never break the heartbeat/command path
            logger.debug("agent event recording failed", exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    base_settings = settings
    db = Database(settings.database_path)
    db.initialize()
    system_row = db.get_system_settings()
    system_values = loads(system_row["values_json"], {}) if system_row is not None else {}
    if not isinstance(system_values, dict):
        system_values = {}
    settings = apply_system_overrides(base_settings, system_values)
    if settings.command_retention_days:
        db.prune_commands(settings.command_retention_days)
        db.prune_agent_events(settings.command_retention_days)
    if settings.audit_retention_days:
        db.prune_audit(settings.audit_retention_days)
    _ensure_bootstrap_admin(settings, db)
    if settings.telemetry_history:
        db.prune_telemetry(settings.telemetry_retention_days)
    db.prune_discovery(settings.discovery.retention_days)

    notification_service = NotificationService(db, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        await notification_service.start()
        _app.state.discovery_manager.start()
        try:
            yield
        finally:
            _app.state.discovery_manager.stop()
            await notification_service.stop()

    app = FastAPI(
        title="Sextant",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.base_settings = base_settings
    app.state.system_settings_values = system_values
    app.state.db = db
    app.state.notifications = notification_service
    app.state.limiter = LoginRateLimiter()
    artifact_store = ArtifactStore(
        settings.store_dir,
        max_blob_bytes=settings.store_max_blob_mb * 1024 * 1024,
        max_total_bytes=settings.store_max_total_gb * 1024**3,
    )
    artifact_store.ensure()
    artifact_store.cleanup_temp()
    app.state.store = artifact_store
    stored_compose_dir = db.get_docker_compose_setting()
    if settings.docker_compose_dir_from_env or not stored_compose_dir:
        compose_root = settings.docker_compose_root
        db.set_docker_compose_setting(str(compose_root))
    else:
        compose_root = Path(stored_compose_dir)
    compose_store = ComposeLibrary(
        compose_root,
        max_file_bytes=settings.docker_max_file_bytes,
        max_files=settings.docker_max_files,
        max_total_bytes=settings.docker_max_total_bytes,
        max_bundle_bytes=settings.docker_max_bundle_bytes,
    )
    compose_store.ensure()
    compose_store.cleanup_temp()
    app.state.compose_store = compose_store
    db.close_open_console_sessions(status="failed", error="Server restarted.")
    app.state.console_broker = ConsoleBroker(
        max_per_agent=settings.docker_console_max_sessions,
        max_input_bytes=settings.docker_console_max_input_bytes,
        max_output_bytes=settings.docker_console_max_output_bytes,
    )
    db.prune_docker_deployments(settings.docker_deployment_retention_days)
    for stack_row in db.list_docker_stacks():
        revisions = db.list_docker_stack_revisions(stack_row["id"])
        keep = {str(row["revision_hash"]) for row in revisions[:11]}
        keep.add(str(stack_row["current_revision_hash"]))
        keep |= db.docker_deployment_revision_hashes(stack_row["id"])
        compose_store.prune_revisions(str(stack_row["slug"]), keep)
    app.state.update_cache = {"at": 0.0, "data": None}
    dashboard_state = _DashboardState()
    app.state.dashboard_state = dashboard_state

    def notify_dashboard() -> None:
        dashboard_state.notify()

    discovery_manager = DiscoveryManager(
        db,
        settings.discovery,
        on_change=notify_dashboard,
    )
    app.state.discovery_manager = discovery_manager
    integration_secrets = IntegrationSecretStore()
    app.state.integration_secrets = integration_secrets

    def _security_headers(response: Response, path: str) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'"
        )
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def _error(status_code: int, detail: str, path: str, **kwargs: Any) -> JSONResponse:
        return _security_headers(JSONResponse({"detail": detail}, status_code=status_code, **kwargs), path)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Use the ASGI path, not request.url.path: the old Starlette rebuilt the
        # latter from the untrusted Host header, which could skip these checks.
        path = request.scope["path"]
        if (
            path.startswith("/api/")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not path.startswith("/api/agent/")
            and path != "/api/auth/login"
        ):
            payload = decode_session(request.cookies.get(settings.cookie_name), settings.secret_key)
            if payload is None:
                return _error(401, "Authentication required.", path)
            supplied = request.headers.get("x-csrf-token")
            if not supplied or not hmac.compare_digest(supplied, payload["csrf"]):
                return _error(403, "Invalid CSRF token.", path)

        if path.startswith("/api/"):
            length = request.headers.get("content-length")
            body_limit = settings.max_body_bytes
            if path.startswith("/api/store/") and request.method in {"PUT", "POST"}:
                body_limit = max(body_limit, settings.store_max_blob_mb * 1024 * 1024)
            if (
                settings.docker_enabled
                and path.startswith("/api/agent/")
                and path.endswith("/docker/inventory")
                and request.method == "POST"
            ):
                body_limit = max(body_limit, settings.docker_inventory_max_bytes)
            if length:
                try:
                    if int(length) > body_limit:
                        return _error(413, "Request body too large.", path)
                except ValueError:
                    return _error(400, "Invalid Content-Length.", path)
            elif request.method not in {"GET", "HEAD", "OPTIONS"}:
                transfer = request.headers.get("transfer-encoding", "").casefold()
                if "chunked" in transfer:
                    return _error(411, "Content-Length is required for API requests.", path)

        response = await call_next(request)
        return _security_headers(response, path)

    # Only install host validation when an explicit allowlist is configured;
    # the default public URL derives one. "*" disables the check.
    if settings.allowed_hosts and "*" not in settings.allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=list(settings.allowed_hosts),
            www_redirect=False,
        )

    def get_db(request: Request) -> Database:
        return request.app.state.db

    def get_settings(request: Request) -> Settings:
        return request.app.state.settings

    def get_store(request: Request) -> ArtifactStore:
        return request.app.state.store

    def get_compose_store(request: Request) -> ComposeLibrary:
        return request.app.state.compose_store

    def get_console_broker(request: Request) -> ConsoleBroker:
        return request.app.state.console_broker

    def get_notifications(request: Request) -> NotificationService:
        return request.app.state.notifications

    def get_integration_secrets(request: Request) -> IntegrationSecretStore:
        return request.app.state.integration_secrets

    def issue_session(
        user_row: sqlite3.Row,
        request: Request,
        database: Database,
        config: Settings,
    ) -> dict[str, Any]:
        session_id = _new_session_id()
        payload = make_session_payload(
            user_row["id"],
            user_row["session_version"],
            config.session_hours * 3600,
            session_id,
        )
        database.cleanup_sessions(config.session_cleanup_days)
        database.create_session(
            session_id=session_id,
            user_id=user_row["id"],
            expires_at=_session_expiry(config),
            ip=client_ip(request, config.trust_proxy),
            user_agent=_request_user_agent(request),
        )
        return payload

    def authenticated_user(
        request: Request,
        database: Database,
        config: Settings,
    ) -> dict[str, Any]:
        """Validate the signed cookie against the session and user rows."""
        payload = decode_session(request.cookies.get(config.cookie_name), config.secret_key)
        if payload is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        session_row = database.get_session(payload["sid"])
        expires = parse_iso(session_row["expires_at"]) if session_row is not None else None
        if session_row is None or session_row["revoked_at"] or expires is None or expires <= utcnow():
            if session_row is not None and not session_row["revoked_at"]:
                database.revoke_session(payload["sid"])
            raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
        if int(session_row["user_id"]) != int(payload["uid"]):
            # The cookie is signed, but bind it to the session row as defense in
            # depth if the signing secret is ever weak or leaked.
            database.revoke_session(payload["sid"])
            raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
        row = database.get_user_by_id(payload["uid"])
        if (
            row is None
            or not row["is_active"]
            or row["session_version"] != payload["sv"]
        ):
            database.revoke_session(payload["sid"])
            raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
        database.touch_session(payload["sid"])
        user = user_to_dict(row)
        user["csrf"] = payload["csrf"]
        user["sid"] = payload["sid"]
        return user

    def current_user(
        request: Request,
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return authenticated_user(request, database, config)

    def require_operator(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not role_at_least(user, "operator"):
            raise HTTPException(status_code=403, detail="Operator access required.")
        return user

    def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not role_at_least(user, "admin"):
            raise HTTPException(status_code=403, detail="Administrator access required.")
        return user

    def agent_auth(
        agent_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token or not agent_id.startswith("agt_"):
            raise HTTPException(status_code=401, detail="Invalid agent credentials.")
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None or not token_matches(token, row["token_hash"]):
            raise HTTPException(status_code=401, detail="Invalid agent credentials.")
        return dict(row)

    def sweep_commands(database: Database, config: Settings) -> int:
        return database.sweep_stale_commands(
            dispatched_grace_seconds=config.dispatched_grace_seconds,
            queued_ttl_hours=config.queued_command_ttl_hours,
        )

    def dashboard_payload(database: Database, config: Settings) -> dict[str, Any]:
        """Single source of truth for GET /api/dashboard and the SSE stream."""
        sweep_commands(database, config)
        now = datetime.now(timezone.utc)
        alert_states: dict[str, dict[str, sqlite3.Row]] = {}
        with database.transaction() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name COLLATE NOCASE").fetchall()
            queued = conn.execute(
                "SELECT COUNT(*) AS n FROM commands WHERE status IN ('queued', 'dispatched', 'running')"
            ).fetchone()
            for row in rows:
                alert_states[row["id"]] = _reconcile_agent_alerts(
                    database, row, config, now, conn
                )
        agents = [
            agent_to_dict(row, config, now, alert_states.get(row["id"], {}))
            for row in rows
        ]
        agents.sort(key=lambda item: (not item["online"], item["name"].casefold()))
        online = sum(1 for item in agents if item["online"])
        return {
            "server_time": utcnow_iso(),
            "stats": {
                "total": len(agents),
                "online": online,
                "offline": len(agents) - online,
                "alerts": sum(item["alert_count"] for item in agents),
                "active_commands": int(queued["n"]),
            },
            "agents": agents,
        }

    def queue_command(
        agent_id: str,
        kind: str,
        args: dict[str, Any],
        user: dict[str, Any],
        request: Request,
        database: Database,
        config: Settings,
        *,
        audit_action: str = "command.created",
        extra_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            normalized = validate_command_args(kind, args)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        with database.transaction() as conn:
            agent = conn.execute(
                "SELECT id, name, last_seen, policy, profile_id, os FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
            if agent is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            agent_policy = loads(agent["policy"], {})
            if not isinstance(agent_policy, dict):
                agent_policy = {}
            profile_row = conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ?",
                (agent["profile_id"] or "prof_workstation",),
            ).fetchone()
            profile_actions = _policy_from_settings(_profile_settings(profile_row))
            policy_flag = {
                "shell": "allow_shell",
                "power": "allow_power",
                "service": "allow_service",
                "package": "allow_packages",
            }.get(kind)
            if policy_flag and (
                agent_policy.get(policy_flag) is False or not profile_actions.get(policy_flag, False)
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"{kind.capitalize()} commands are blocked by this agent's profile or server policy.",
                )
            if kind == "docker":
                docker_action = str(normalized.get("action", "")).lower()
                docker_gate = "allow_docker"
                if docker_action in {"container.exec_one_shot", "console.open"}:
                    docker_gate = "allow_docker_exec"
                elif docker_action == "cleanup":
                    docker_gate = "allow_docker_prune"
                elif docker_action.startswith("compose."):
                    docker_gate = "allow_docker_compose"
                if (
                    str(agent["os"]).lower() != "linux"
                    or not _docker_module_enabled(profile_row)
                    or agent_policy.get(docker_gate, True) is False
                    or not profile_actions.get(docker_gate, False)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Docker commands are blocked by this agent's profile or server policy.",
                    )
                if docker_action in DOCKER_MUTATION_ACTIONS:
                    active = conn.execute(
                        """SELECT args FROM commands
                           WHERE agent_id = ? AND kind = 'docker'
                             AND status IN ('queued', 'dispatched', 'running')""",
                        (agent_id,),
                    ).fetchall()
                    for row in active:
                        active_args = loads(row["args"], {})
                        if (
                            isinstance(active_args, dict)
                            and str(active_args.get("action", "")).lower() in DOCKER_MUTATION_ACTIONS
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail="Another Docker mutation is already active for this host.",
                            )
            if kind == "discovery":
                if (
                    not config.discovery.enabled
                    or not config.discovery.agent_relay
                    or str(agent["os"]).lower() != "linux"
                    or not _discovery_module_enabled(profile_row)
                    or agent_policy.get("allow_discovery", False) is not True
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Agent discovery is disabled by the server relay switch, "
                            "agent profile, or server policy."
                        ),
                    )
                active_discovery = conn.execute(
                    """SELECT 1 FROM commands
                       WHERE agent_id = ? AND kind = 'discovery'
                         AND status IN ('queued', 'dispatched', 'running')
                       LIMIT 1""",
                    (agent_id,),
                ).fetchone()
                if active_discovery is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="Another discovery scan is already active for this agent.",
                    )
            last_seen = parse_iso(agent["last_seen"])
            online = bool(
                last_seen and (utcnow() - last_seen).total_seconds() <= config.offline_after_seconds
            )
            if not online and config.offline_command_policy == "reject":
                raise HTTPException(
                    status_code=409,
                    detail="Agent is offline and the server policy rejects new commands.",
                )
            queued = conn.execute(
                "SELECT COUNT(*) AS n FROM commands WHERE agent_id = ? AND status = 'queued'",
                (agent_id,),
            ).fetchone()
            if int(queued["n"]) >= config.max_queued_commands_per_agent:
                raise HTTPException(
                    status_code=409,
                    detail=f"Agent already has {queued['n']} queued commands (limit {config.max_queued_commands_per_agent}).",
                )
            command_id = "cmd_" + secrets.token_hex(12)
            now = utcnow_iso()
            conn.execute(
                """INSERT INTO commands (id, agent_id, kind, args, status, created_by, created_at)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
                (command_id, agent_id, kind, dumps(normalized), user["username"], now),
            )
            detail = {
                "command_id": command_id,
                "kind": kind,
                "agent": agent["name"],
                **(extra_detail or {}),
            }
            database.audit(
                actor=user["username"],
                action=audit_action,
                target=agent_id,
                detail=detail,
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        notify_dashboard()
        return command_to_dict(row)

    def resolve_agent_profile(conn: sqlite3.Connection, value: str) -> sqlite3.Row:
        profile_row = conn.execute(
            "SELECT * FROM agent_profiles WHERE id = ? OR slug = ? COLLATE NOCASE",
            (value, value),
        ).fetchone()
        if profile_row is None:
            raise HTTPException(status_code=400, detail="Unknown agent profile.")
        return profile_row

    def apply_agent_profile(
        agent_id: str,
        profile_value: str,
        user: dict[str, Any],
        request: Request,
        database: Database,
        config: Settings,
        *,
        extra_detail: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            previous_profile_id = row["profile_id"]
            profile_row = resolve_agent_profile(conn, profile_value)
            conn.execute(
                "UPDATE agents SET profile_id = ? WHERE id = ?",
                (profile_row["id"], agent_id),
            )
            database.audit(
                actor=user["username"],
                action="agent.updated",
                target=agent_id,
                detail={
                    "name": row["name"],
                    "profile_id": profile_row["id"],
                    **(extra_detail or {}),
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if previous_profile_id != profile_row["id"]:
            _record_agent_events(
                database,
                [
                    {
                        "agent_id": agent_id,
                        "kind": "profile",
                        "severity": "info",
                        "title": "Agent profile changed",
                        "detail": {
                            "from": previous_profile_id,
                            "to": profile_row["id"],
                            "actor": user["username"],
                        },
                    }
                ],
            )
        return updated

    def send_agent_wake(
        agent_id: str,
        payload: AgentWakeIn,
        user: dict[str, Any],
        request: Request,
        database: Database,
        config: Settings,
        *,
        extra_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        if not row["mac"]:
            raise HTTPException(status_code=400, detail="Agent has not reported a MAC address.")
        try:
            mac = normalize_mac(row["mac"])
            broadcast = _validate_broadcast(payload.broadcast)
            packets = send_magic_packet(mac, broadcast, payload.port, payload.packets)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            logger.warning("WoL send failed: %s", exc)
            raise HTTPException(status_code=502, detail="Could not send the wake packet.")
        database.audit(
            actor=user["username"],
            action="wol.sent",
            target=agent_id,
            detail={
                "mac": mac,
                "broadcast": broadcast,
                "port": payload.port,
                "packets": packets,
                **(extra_detail or {}),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        return {"sent": packets, "mac": mac, "broadcast": broadcast, "port": payload.port}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        index_file = settings.static_dir / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=500, detail="Dashboard assets are missing.")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    if settings.static_dir.exists():
        app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "server_time": utcnow_iso()}

    @app.get("/api/auth/config")
    def auth_config(config: Settings = Depends(get_settings)) -> dict[str, Any]:
        return {
            "ldap_enabled": config.ldap is not None,
            "local_auth": True,
            "version": __version__,
            "public_url": config.public_url,
        }

    @app.post("/api/auth/login")
    def login(
        payload: LoginIn,
        request: Request,
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> JSONResponse:
        ip = client_ip(request, config.trust_proxy)
        key = f"{ip}:{payload.username.casefold()}"

        def fail(reason: str, detail: str = "Invalid username or password.") -> None:
            app.state.limiter.record_failure(key)
            database.audit(
                actor=payload.username,
                action="auth.login_failed",
                target=payload.username,
                detail={"mode": payload.mode, "reason": reason},
                ip=ip,
            )
            raise HTTPException(status_code=401, detail=detail)

        retry = app.state.limiter.retry_after(key)
        if retry:
            database.audit(
                actor=payload.username,
                action="auth.login_rate_limited",
                target=payload.username,
                detail={"mode": payload.mode, "retry_after": retry},
                ip=ip,
            )
            return JSONResponse(
                {"detail": "Too many failed sign-in attempts. Try again later."},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )

        row = database.get_user_by_name(payload.username)
        if row is None or row["source"] != "local":
            # Spend the same scrypt work as a real local account so response
            # timing does not reveal whether a username exists.
            verify_password_dummy(payload.password)
        local_ok = bool(
            row is not None
            and row["source"] == "local"
            and row["is_active"]
            and verify_password(payload.password, row["password_hash"])
        )

        selected = None
        if payload.mode in {"auto", "local"} and row is not None and row["source"] == "local":
            if not row["is_active"]:
                fail("account_disabled")
            if local_ok:
                selected = row
            else:
                fail("bad_password")
        elif payload.mode == "local":
            fail("unknown_user")

        if selected is None and payload.mode in {"auto", "ad"} and config.ldap is not None:
            if row is not None and row["source"] == "local":
                fail("local_account_mismatch")
            try:
                identity = authenticate_ldap(payload.username, payload.password, config.ldap)
            except RuntimeError:
                logger.exception("LDAP authentication failed for %r", payload.username)
                database.audit(
                    actor=payload.username,
                    action="auth.ldap_unavailable",
                    target=payload.username,
                    detail={"mode": payload.mode},
                    ip=ip,
                )
                raise HTTPException(status_code=503, detail="Directory authentication is unavailable.")
            if identity is None:
                fail("bad_credentials")
            selected = database.upsert_ad_user(
                username=payload.username,
                role=identity.role,
                display_name=identity.display_name,
                email=identity.email,
            )
        elif selected is None:
            fail("unknown_user")

        if selected is None:
            fail("unknown_user")
        if not selected["is_active"]:
            fail("account_disabled")

        # Refresh last_login and use the current role from the database.
        now = utcnow_iso()
        with database.connection() as conn:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, selected["id"]))
        selected = database.get_user_by_id(selected["id"])
        assert selected is not None

        app.state.limiter.record_success(key)
        session = issue_session(selected, request, database, config)
        public_user = user_to_dict(selected)
        result = JSONResponse(
            {
                "user": public_user,
                "csrf": session["csrf"],
                "settings": _public_settings(config),
                "permissions": _permissions(public_user),
            }
        )
        _session_cookie(result, config, session)
        database.audit(
            actor=selected["username"],
            action="auth.login",
            target=selected["username"],
            detail={"source": selected["source"]},
            ip=ip,
        )
        return result

    @app.post("/api/auth/logout")
    def logout(
        request: Request,
        response: Response,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> Response:
        database.revoke_session(user["sid"])
        broker.close_for_user(user["username"], status="closed", error="Operator session ended.")
        database.close_console_sessions_for_user(user["username"], status="closed")
        _clear_session_cookie(response, config)
        response.status_code = 204
        database.audit(
            actor=user["username"],
            action="auth.logout",
            target=user["username"],
            ip=client_ip(request, config.trust_proxy),
        )
        return response

    @app.get("/api/me")
    def me(
        user: dict[str, Any] = Depends(current_user),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        public_user = {key: value for key, value in user.items() if key != "sid"}
        return {
            "user": public_user,
            "settings": _public_settings(config),
            "permissions": _permissions(user),
        }

    @app.get("/api/settings")
    def settings_view(
        config: Settings = Depends(get_settings),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        return {
            **_public_settings(config),
            "server_time": utcnow_iso(),
            "permissions": _permissions(user),
        }

    def _system_values(database: Database) -> dict[str, Any]:
        row = database.get_system_settings()
        values = loads(row["values_json"], {}) if row is not None else {}
        return values if isinstance(values, dict) else {}

    def _system_payload(request: Request, database: Database) -> dict[str, Any]:
        row = database.get_system_settings()
        return serialize_system_settings(
            request.app.state.base_settings,
            _system_values(database),
            updated_at=str(row["updated_at"]) if row is not None else "",
            updated_by=str(row["updated_by"]) if row is not None else "",
        )

    @app.get("/api/system/settings")
    def system_settings_view(
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        return _system_payload(request, database)

    @app.patch("/api/system/settings")
    def update_system_settings(
        payload: dict[str, Any],
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        current = _system_values(database)
        try:
            merged, secrets = normalize_system_patch(
                request.app.state.base_settings, current, payload
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "bind_password" in secrets:
            write_bind_password(request.app.state.base_settings, secrets["bind_password"])
        elif (
            merged.get("domain", {}).get("enabled")
            and request.app.state.base_settings.ldap is not None
            and request.app.state.base_settings.ldap.bind_password
        ):
            # Persist an environment-provided bind password the first time the
            # domain is saved, so UI-managed configuration survives env changes.
            if not (request.app.state.base_settings.data_dir / "secrets" / "ldap_bind_password").exists():
                write_bind_password(
                    request.app.state.base_settings,
                    request.app.state.base_settings.ldap.bind_password,
                )
        database.set_system_settings(merged, user["username"])
        request.app.state.system_settings_values = merged
        request.app.state.settings = apply_system_overrides(
            request.app.state.base_settings, merged
        )
        if "ssh_private_key" in secrets:
            write_ssh_private_key(
                request.app.state.settings, secrets["ssh_private_key"]
            )
        database.audit(
            actor=user["username"],
            action="system.settings_updated",
            target="system",
            detail={
                "sections": sorted(key for key in payload if key in {"server", "domain", "ssh"}),
                "domain_enabled": bool(merged.get("domain", {}).get("enabled")),
                "ssh_source": merged.get("ssh", {}).get("source", ""),
                "restart_required": sorted(
                    key
                    for key in ("allowed_hosts", "cookie_secure")
                    if key in payload.get("server", {})
                ),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        result = _system_payload(request, database)
        result["meta"]["restart_required"] = sorted(
            key
            for key in ("allowed_hosts", "cookie_secure")
            if key in (payload.get("server") or {})
        )
        return result

    @app.patch("/api/me")
    def update_me(
        payload: dict[str, Any],
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if user.get("source") == "ad":
            raise HTTPException(
                status_code=400,
                detail="Directory accounts are updated from the domain, not here.",
            )
        display_name = payload.get("display_name")
        email = payload.get("email")
        if display_name is None and email is None:
            raise HTTPException(status_code=400, detail="No profile changes were supplied.")
        if display_name is not None:
            display_name = str(display_name).strip()
            if not display_name:
                raise HTTPException(status_code=400, detail="Display name cannot be empty.")
            if len(display_name) > 255:
                raise HTTPException(status_code=400, detail="Display name is too long.")
        if email is not None:
            email = str(email).strip()
            if email and (
                len(email) > 255
                or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email)
            ):
                raise HTTPException(status_code=400, detail="Enter a valid email address.")
        row = database.update_user_profile(
            int(user["id"]), display_name=display_name, email=email
        )
        if row is None:
            raise HTTPException(status_code=404, detail="User not found.")
        database.audit(
            actor=user["username"],
            action="auth.profile_updated",
            target=user["username"],
            detail={"display_name": display_name is not None, "email": email is not None},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"user": user_to_dict(row)}

    @app.post("/api/system/ldap/test")
    def test_system_ldap(
        payload: dict[str, Any],
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        current = _system_values(database)
        if "domain" in payload:
            patch = {"domain": payload["domain"]}
        else:
            domain = current.get("domain") or serialize_system_settings(
                request.app.state.base_settings, current
            ).get("domain", {})
            patch = {"domain": domain}
        try:
            merged, secrets = normalize_system_patch(
                request.app.state.base_settings, current, patch
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        domain = merged.get("domain") or {}
        if not domain.get("enabled"):
            raise HTTPException(status_code=400, detail="Directory login is disabled.")
        ldap = build_ldap_settings(
            request.app.state.base_settings,
            domain,
            secrets.get("bind_password"),
        )
        if ldap is None:
            raise HTTPException(status_code=400, detail="Directory login is not configured.")
        try:
            result = probe_ldap(ldap, str(payload.get("username") or "").strip())
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        database.audit(
            actor=user["username"],
            action="system.ldap_tested",
            target=ldap.server,
            detail={"username": bool(payload.get("username")), "entries": result.get("entries", 0)},
            ip=client_ip(request, config.trust_proxy),
        )
        return result

    @app.post("/api/system/ssh/test")
    def test_system_ssh(
        payload: dict[str, Any],
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        current = _system_values(database)
        patch = {"ssh": payload.get("ssh")} if "ssh" in payload else {}
        try:
            merged, _secrets = normalize_system_patch(
                request.app.state.base_settings, current, patch
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        effective = apply_system_overrides(request.app.state.base_settings, merged)
        info = ssh_key_info(effective.ssh_key_path)
        return {
            **info,
            "source": effective.ssh_key_source,
            "system_user": effective.ssh_system_user,
            "effective_key_path": effective.ssh_key_path,
            "effective_config_path": effective.ssh_config_path,
            "config_present": bool(
                effective.ssh_config_path and Path(effective.ssh_config_path).exists()
            ),
        }

    @app.post("/api/system/restart")
    def restart_for_settings(
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        try:
            trigger = selfupdate.request_restart(
                config.update_restart_path,
                version=__version__,
                action="settings",
            )
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail="The restart handoff is not available."
            ) from exc
        database.audit(
            actor=user["username"],
            action="system.restart_requested",
            target="sextant",
            detail={"reason": "settings", "trigger": str(trigger)},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"restarting": True, "restart_pending": True}

    @app.get("/api/notifications/status")
    def notification_status(
        user: dict[str, Any] = Depends(require_admin),
        notifications: NotificationService = Depends(get_notifications),
    ) -> dict[str, Any]:
        del user
        return notifications.status()

    @app.get("/api/notifications/log")
    def notification_log(
        limit: int = Query(default=50, ge=1, le=200),
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        rows = database.list_notifications(limit)
        return {"notifications": [_notification_to_dict(row) for row in rows]}

    @app.post("/api/notifications/test")
    def notification_test(
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        notifications: NotificationService = Depends(get_notifications),
    ) -> dict[str, Any]:
        if not notifications.configured:
            raise HTTPException(status_code=400, detail="Telegram notifications are not configured.")
        try:
            notifications.send_test()
        except NotificationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        database.audit(
            actor=user["username"],
            action="notification.test",
            target="telegram",
            ip=client_ip(request, config.trust_proxy),
        )
        return {"sent": True, "at": utcnow_iso()}

    @app.post("/api/notifications/retry")
    def notification_retry(
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        retried = database.retry_failed_notifications()
        database.audit(
            actor=user["username"],
            action="notification.retry",
            target="telegram",
            detail={"count": retried},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"retried": retried}

    @app.get("/api/saved-views")
    def list_saved_views(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        rows = database.list_saved_views(int(user["id"]))
        return {"views": [_saved_view_to_dict(row) for row in rows]}

    @app.post("/api/saved-views")
    def create_saved_view(
        payload: SavedViewIn,
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Saved view needs a name.")
        filters = _normalize_saved_view_filters(payload.filters)
        try:
            row = database.create_saved_view(int(user["id"]), name, filters)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="A saved view with that name already exists.")
        database.audit(
            actor=user["username"],
            action="saved_view.created",
            target=row["id"],
            detail={"name": name, "filters": filters},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"view": _saved_view_to_dict(row)}

    @app.delete("/api/saved-views/{view_id}")
    def delete_saved_view(
        view_id: str,
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        row = database.get_saved_view(view_id, int(user["id"]))
        if row is None:
            raise HTTPException(status_code=404, detail="Saved view not found.")
        if not database.delete_saved_view(view_id, int(user["id"])):
            raise HTTPException(status_code=404, detail="Saved view not found.")
        database.audit(
            actor=user["username"],
            action="saved_view.deleted",
            target=view_id,
            detail={"name": row["name"]},
            ip=client_ip(request, config.trust_proxy),
        )
        return Response(status_code=204)

    @app.post("/api/me/password")
    def change_password(
        payload: PasswordChangeIn,
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> JSONResponse:
        row = database.get_user_by_id(user["id"])
        if row is None or row["source"] != "local":
            raise HTTPException(status_code=400, detail="Only local accounts can change a password here.")
        if not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        problem = validate_password(payload.new_password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if payload.current_password == payload.new_password:
            raise HTTPException(status_code=400, detail="Choose a new password.")
        database.revoke_user_sessions(row["id"])
        with database.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
                (hash_password(payload.new_password), row["id"]),
            )
        refreshed = database.get_user_by_id(row["id"])
        assert refreshed is not None
        session = issue_session(refreshed, request, database, config)
        result = JSONResponse({"user": user_to_dict(refreshed), "csrf": session["csrf"]})
        _session_cookie(result, config, session)
        database.audit(
            actor=user["username"],
            action="auth.password_changed",
            target=user["username"],
            ip=client_ip(request, config.trust_proxy),
        )
        if user["username"].casefold() == config.admin_user.casefold():
            try:
                config.bootstrap_file.unlink(missing_ok=True)
            except OSError:
                pass
        return result

    @app.get("/api/sessions")
    def my_sessions(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        rows = database.list_sessions(user["id"])
        return {"sessions": [session_to_dict(row, user["sid"]) for row in rows]}

    @app.get("/api/users/{user_id}/sessions")
    def user_sessions(
        user_id: int,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        target = database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        rows = database.list_sessions(user_id)
        return {
            "user": user_to_dict(target),
            "sessions": [session_to_dict(row, user["sid"]) for row in rows],
        }

    @app.delete("/api/sessions/{session_id}")
    def revoke_one_session(
        session_id: str,
        request: Request,
        response: Response,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        target = database.get_session(session_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        if target["user_id"] != user["id"] and not role_at_least(user, "admin"):
            raise HTTPException(status_code=403, detail="Administrator access required.")
        database.revoke_session(session_id)
        database.audit(
            actor=user["username"],
            action="session.revoked",
            target=session_id,
            detail={"user_id": target["user_id"]},
            ip=client_ip(request, config.trust_proxy),
        )
        if session_id == user["sid"]:
            _clear_session_cookie(response, config)
            response.status_code = 204
            return response
        response.status_code = 204
        return response

    @app.post("/api/users/{user_id}/revoke-sessions")
    def revoke_user_sessions(
        user_id: int,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> JSONResponse:
        target = database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        revoked = database.revoke_user_sessions(user_id)
        broker.close_for_user(target["username"], status="closed", error="Operator session revoked.")
        database.close_console_sessions_for_user(target["username"], status="closed")
        with database.connection() as conn:
            conn.execute(
                "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                (user_id,),
            )
        database.audit(
            actor=user["username"],
            action="sessions.revoked",
            target=target["username"],
            detail={"count": revoked, "self": user_id == user["id"]},
            ip=client_ip(request, config.trust_proxy),
        )
        result: dict[str, Any] = {"revoked": revoked}
        if user_id == user["id"]:
            refreshed = database.get_user_by_id(user_id)
            assert refreshed is not None
            session = issue_session(refreshed, request, database, config)
            result["csrf"] = session["csrf"]
            result["user"] = user_to_dict(refreshed)
            response = JSONResponse(result)
            _session_cookie(response, config, session)
            return response
        return JSONResponse(result)

    @app.post("/api/users/{user_id}/reset-password")
    def admin_reset_password(
        user_id: int,
        payload: AdminPasswordResetIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> JSONResponse:
        target = database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if target["source"] != "local":
            raise HTTPException(status_code=400, detail="Only local accounts can have a local password reset.")
        problem = validate_password(payload.new_password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        database.revoke_user_sessions(user_id)
        with database.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
                (hash_password(payload.new_password), user_id),
            )
        database.audit(
            actor=user["username"],
            action="user.password_reset",
            target=target["username"],
            detail={"self": user_id == user["id"]},
            ip=client_ip(request, config.trust_proxy),
        )
        if target["username"].casefold() == config.admin_user.casefold():
            try:
                config.bootstrap_file.unlink(missing_ok=True)
            except OSError:
                pass
        result: dict[str, Any] = {"user_id": user_id, "username": target["username"]}
        if user_id == user["id"]:
            refreshed = database.get_user_by_id(user_id)
            assert refreshed is not None
            session = issue_session(refreshed, request, database, config)
            result["csrf"] = session["csrf"]
            result["user"] = user_to_dict(refreshed)
            response = JSONResponse(result)
            _session_cookie(response, config, session)
            return response
        return JSONResponse(result)

    @app.get("/api/dashboard")
    def dashboard(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        return dashboard_payload(database, config)

    @app.get("/api/docker/hosts")
    def list_docker_hosts(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        rows = database.list_docker_hosts()
        now = utcnow()
        return {
            "server_time": utcnow_iso(),
            "stale_seconds": config.docker_inventory_stale_seconds,
            "hosts": [docker_host_to_dict(row, config, now) for row in rows],
        }

    def _docker_context(
        database: Database,
        agent_id: str,
        config: Settings,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, dict[str, bool]]:
        host_row = database.get_docker_host(agent_id)
        if host_row is None:
            raise HTTPException(status_code=404, detail="Docker host not found.")
        with database.connection() as conn:
            agent_row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent_row is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        profile_row = database.get_profile_by_id(
            agent_row["profile_id"]
        ) or database.get_profile_by_slug("workstation")
        return host_row, agent_row, profile_row, _docker_capabilities(agent_row, profile_row, config)

    @app.get("/api/docker/hosts/{agent_id}")
    def get_docker_host(
        agent_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        row, _agent_row, _profile_row, capabilities = _docker_context(database, agent_id, config)
        host_payload = docker_host_to_dict(row, config)
        host_payload["capabilities"] = capabilities
        return {
            "server_time": utcnow_iso(),
            "stale_seconds": config.docker_inventory_stale_seconds,
            "host": host_payload,
            "containers": [
                docker_container_to_dict(item)
                for item in database.list_docker_containers(agent_id)
            ],
            "images": [
                docker_image_to_dict(item) for item in database.list_docker_images(agent_id)
            ],
            "volumes": [
                docker_volume_to_dict(item) for item in database.list_docker_volumes(agent_id)
            ],
            "networks": [
                docker_network_to_dict(item) for item in database.list_docker_networks(agent_id)
            ],
            "deployments": [
                _deployment_payload(database, item)
                for item in database.list_docker_deployments(agent_id=agent_id, limit=100)
            ],
        }

    @app.post("/api/docker/hosts/{agent_id}/refresh")
    def refresh_docker_host(
        agent_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        host_row = database.get_docker_host(agent_id)
        if host_row is None:
            raise HTTPException(status_code=404, detail="Docker host not found.")
        with database.connection() as conn:
            agent_row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent_row is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        profile_row = database.get_profile_by_id(
            agent_row["profile_id"]
        ) or database.get_profile_by_slug("workstation")
        if not _docker_effective_allowed(agent_row, profile_row, config):
            raise HTTPException(
                status_code=409,
                detail="Docker is disabled by the agent profile or server policy.",
            )
        if not host_row["available"]:
            raise HTTPException(status_code=409, detail="Docker is not currently reachable on this host.")
        command = queue_command(
            agent_id,
            "docker",
            {"action": "inventory.refresh"},
            user,
            request,
            database,
            config,
            audit_action="docker.inventory_refresh",
            extra_detail={"host": host_row["agent_name"]},
        )
        command["queued"] = True
        return command

    @app.post("/api/docker/hosts/{agent_id}/actions")
    def docker_action(
        agent_id: str,
        payload: DockerActionIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        host_row, _agent_row, _profile_row, capabilities = _docker_context(
            database, agent_id, config
        )
        if not host_row["available"]:
            raise HTTPException(status_code=409, detail="Docker is not currently reachable on this host.")
        if not capabilities["mutate"]:
            raise HTTPException(
                status_code=409,
                detail="Docker actions are disabled by the agent profile or server policy.",
            )
        action_model = payload.root
        action = action_model.action
        args = action_model.model_dump(exclude_none=True)
        target = ""
        detail: dict[str, Any] = {
            "host": host_row["agent_name"],
            "docker_action": action,
        }
        if action.startswith("container."):
            container = database.find_docker_container(agent_id, str(args["container_id"]))
            if container is None:
                raise HTTPException(status_code=404, detail="Container not found in the latest inventory.")
            target = str(container["container_id"])
            args["container_id"] = target
            detail["target"] = target
            detail["container_name"] = container["name"]
            if action == "container.clone":
                image = database.find_docker_image(agent_id, str(args["image_id"]))
                if image is None:
                    raise HTTPException(status_code=404, detail="Clone image not found in the latest inventory.")
                args["image_id"] = str(image["image_id"])
                detail["image_id"] = args["image_id"]
                for network_id in args.get("network_ids") or []:
                    network = database.find_docker_network(agent_id, network_id)
                    if network is None:
                        raise HTTPException(status_code=404, detail="Clone network not found in the latest inventory.")
                if container["compose_project"] and not bool(args.get("acknowledge_drift", False)):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Clone is blocked for a Compose-managed container. "
                            "Retry with acknowledge_drift=true; docker compose up may revert the clone."
                        ),
                    )
                for existing in database.list_docker_containers(agent_id):
                    if existing["name"] == args.get("new_name"):
                        raise HTTPException(status_code=409, detail="A container with that name already exists.")
                detail["new_name"] = args.get("new_name")
                detail["network_ids"] = args.get("network_ids") or []
                detail["acknowledge_drift"] = bool(args.get("acknowledge_drift", False))
        elif action == "image.remove":
            image = database.find_docker_image(agent_id, str(args["image_id"]))
            if image is None:
                raise HTTPException(status_code=404, detail="Image not found in the latest inventory.")
            if int(image["container_count"]) > 0:
                raise HTTPException(status_code=409, detail="Image is used by a container and cannot be removed.")
            target = str(image["image_id"])
            args["image_id"] = target
            detail["target"] = target
        elif action == "volume.remove":
            volume = database.find_docker_volume(agent_id, str(args["volume_name"]))
            if volume is None:
                raise HTTPException(status_code=404, detail="Volume not found in the latest inventory.")
            if bool(volume["in_use"]):
                raise HTTPException(status_code=409, detail="Volume is in use and cannot be removed.")
            target = str(volume["name"])
            args["volume_name"] = target
            detail["target"] = target
        elif action == "network.remove":
            network = database.find_docker_network(agent_id, str(args["network_id"]))
            if network is None:
                raise HTTPException(status_code=404, detail="Network not found in the latest inventory.")
            if str(network["name"]).lower() in {"bridge", "host", "none"}:
                raise HTTPException(status_code=409, detail="Default Docker networks cannot be removed.")
            if int(network["container_count"]) > 0:
                raise HTTPException(status_code=409, detail="Network has attached containers and cannot be removed.")
            target = str(network["network_id"])
            args["network_id"] = target
            detail["target"] = target
        if action == "container.rename":
            detail["new_name"] = args.get("new_name", "")
        elif action == "container.update":
            detail.update(
                {
                    "restart_policy": args.get("restart_policy", ""),
                    "memory": args.get("memory", ""),
                    "cpus": args.get("cpus", ""),
                    "pids_limit": args.get("pids_limit"),
                }
            )
        elif action == "container.remove":
            detail.update(
                {"force": bool(args.get("force")), "remove_volumes": bool(args.get("remove_volumes"))}
            )
        elif action == "container.logs":
            detail.update({"tail": args.get("tail"), "timestamps": bool(args.get("timestamps"))})
        command = queue_command(
            agent_id,
            "docker",
            args,
            user,
            request,
            database,
            config,
            audit_action="docker.action_requested",
            extra_detail=detail,
        )
        command["queued"] = True
        return command

    @app.post("/api/docker/hosts/{agent_id}/containers/{container_id}/exec")
    def docker_container_exec(
        agent_id: str,
        container_id: str,
        payload: DockerExecIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        host_row, _agent_row, _profile_row, capabilities = _docker_context(
            database, agent_id, config
        )
        if not host_row["available"]:
            raise HTTPException(status_code=409, detail="Docker is not currently reachable on this host.")
        if not capabilities["exec"]:
            raise HTTPException(
                status_code=409,
                detail="Docker exec is disabled by the agent profile or server policy.",
            )
        container = database.find_docker_container(agent_id, container_id)
        if container is None:
            raise HTTPException(status_code=404, detail="Container not found in the latest inventory.")
        args = {
            "action": "container.exec_one_shot",
            "container_id": container["container_id"],
            "shell": payload.shell,
            "user": payload.user,
            "workdir": payload.workdir,
            "command": payload.command,
            "timeout": payload.timeout,
            "tty": payload.tty,
        }
        command = queue_command(
            agent_id,
            "docker",
            args,
            user,
            request,
            database,
            config,
            audit_action="docker.action_requested",
            extra_detail={
                "host": host_row["agent_name"],
                "docker_action": "container.exec_one_shot",
                "target": container["container_id"],
                "container_name": container["name"],
                "shell": payload.shell,
                "timeout": payload.timeout,
            },
        )
        command["queued"] = True
        return command

    @app.get("/api/docker/hosts/{agent_id}/cleanup")
    def docker_cleanup_preview(
        agent_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        host_row, _agent_row, _profile_row, capabilities = _docker_context(
            database, agent_id, config
        )
        return {
            "server_time": utcnow_iso(),
            "host": docker_host_to_dict(host_row, config),
            "can_prune": capabilities["prune"],
            "scopes": _docker_cleanup_preview(database, agent_id),
        }

    @app.post("/api/docker/hosts/{agent_id}/cleanup")
    def docker_cleanup(
        agent_id: str,
        payload: DockerCleanupIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        host_row, _agent_row, _profile_row, capabilities = _docker_context(
            database, agent_id, config
        )
        if not host_row["available"]:
            raise HTTPException(status_code=409, detail="Docker is not currently reachable on this host.")
        if not capabilities["prune"]:
            raise HTTPException(
                status_code=409,
                detail="Docker cleanup is disabled by the agent profile or server policy.",
            )
        command = queue_command(
            agent_id,
            "docker",
            {
                "action": "cleanup",
                "scope": payload.scope,
                "include_compose": payload.include_compose,
            },
            user,
            request,
            database,
            config,
            audit_action="docker.action_requested",
            extra_detail={
                "host": host_row["agent_name"],
                "docker_action": "cleanup",
                "scope": payload.scope,
                "include_compose": payload.include_compose,
            },
        )
        command["queued"] = True
        return command

    def _prune_stack_revisions(
        database: Database,
        compose_store: ComposeLibrary,
        stack_row: sqlite3.Row,
    ) -> None:
        revisions = database.list_docker_stack_revisions(stack_row["id"])
        keep = {str(row["revision_hash"]) for row in revisions[:11]}
        keep.add(str(stack_row["current_revision_hash"]))
        keep |= database.docker_deployment_revision_hashes(stack_row["id"])
        try:
            compose_store.prune_revisions(str(stack_row["slug"]), keep)
        except DockerStoreError:
            logger.warning("Compose revision pruning failed for %s", stack_row["slug"], exc_info=True)

    def _stack_detail(
        database: Database,
        compose_store: ComposeLibrary,
        stack_row: sqlite3.Row,
        *,
        include_contents: bool,
    ) -> dict[str, Any]:
        files = compose_store.read_current(str(stack_row["slug"]))
        payload = docker_stack_to_dict(stack_row)
        payload["files"] = [
            {"name": name, "size": len(data)} for name, data in sorted(files.items())
        ]
        payload["contents"] = (
            {name: data.decode("utf-8") for name, data in sorted(files.items())}
            if include_contents
            else None
        )
        payload["revisions"] = [
            docker_stack_revision_to_dict(row, include_files=True)
            for row in database.list_docker_stack_revisions(stack_row["id"])
        ]
        return payload

    def _default_project(slug: str) -> str:
        return f"sextant_{slug}"[:63]

    def _deployment_payload(database: Database, row: sqlite3.Row) -> dict[str, Any]:
        payload = docker_deployment_to_dict(row)
        config_drift = bool(
            payload["active"]
            and payload["action"] == "up"
            and database.docker_deployment_config_drift(row)
        )
        payload["config_drift"] = config_drift
        payload["drift"] = bool(payload.get("drift") or config_drift)
        return payload

    @app.get("/api/docker/stacks")
    def list_docker_stacks(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        return {"stacks": [docker_stack_to_dict(row) for row in database.list_docker_stacks()]}

    @app.get("/api/docker/stacks/{stack_id}")
    def get_docker_stack(
        stack_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        compose_store: ComposeLibrary = Depends(get_compose_store),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        stack = database.get_docker_stack(stack_id)
        if stack is None:
            raise HTTPException(status_code=404, detail="Compose stack not found.")
        return {
            "stack": _stack_detail(
                database,
                compose_store,
                stack,
                include_contents=role_at_least(user, "admin"),
            )
        }

    @app.post("/api/docker/stacks")
    def create_docker_stack(
        payload: DockerStackIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        compose_store: ComposeLibrary = Depends(get_compose_store),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        if database.get_docker_stack(payload.slug) is not None:
            raise HTTPException(status_code=409, detail="A Compose stack with that slug already exists.")
        try:
            revision_hash, manifest = compose_store.save_revision(payload.slug, payload.files)
            stack = database.create_docker_stack(
                payload.slug,
                payload.name,
                payload.description,
                revision_hash,
                user["username"],
            )
            database.add_docker_stack_revision(
                stack["id"],
                revision_hash,
                manifest,
                payload.message,
                user["username"],
            )
        except DockerStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        database.audit(
            actor=user["username"],
            action="docker.stack_created",
            target=stack["id"],
            detail={
                "slug": payload.slug,
                "name": payload.name,
                "revision_hash": revision_hash,
                "files": sorted(payload.files),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        _prune_stack_revisions(database, compose_store, stack)
        return {"stack": _stack_detail(database, compose_store, stack, include_contents=True)}

    @app.put("/api/docker/stacks/{stack_id}")
    def update_docker_stack(
        stack_id: str,
        payload: DockerStackPatchIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        compose_store: ComposeLibrary = Depends(get_compose_store),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        stack = database.get_docker_stack(stack_id)
        if stack is None:
            raise HTTPException(status_code=404, detail="Compose stack not found.")
        try:
            if payload.files is not None:
                revision_hash, manifest = compose_store.save_revision(str(stack["slug"]), payload.files)
            else:
                revision_hash, manifest = compose_store.save_revision(
                    str(stack["slug"]), compose_store.read_current(str(stack["slug"]))
                )
        except DockerStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        name = payload.name if payload.name is not None else str(stack["name"])
        description = (
            payload.description if payload.description is not None else str(stack["description"])
        )
        database.update_docker_stack(
            stack["id"],
            name=name,
            description=description,
            current_revision_hash=revision_hash,
        )
        database.add_docker_stack_revision(
            stack["id"],
            revision_hash,
            manifest,
            payload.message,
            user["username"],
        )
        database.audit(
            actor=user["username"],
            action="docker.stack_updated",
            target=stack["id"],
            detail={
                "slug": stack["slug"],
                "revision_hash": revision_hash,
                "files": sorted(payload.files or {}),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        updated = database.get_docker_stack(stack["id"])
        assert updated is not None
        _prune_stack_revisions(database, compose_store, updated)
        return {"stack": _stack_detail(database, compose_store, updated, include_contents=True)}

    @app.delete("/api/docker/stacks/{stack_id}")
    def delete_docker_stack(
        stack_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        compose_store: ComposeLibrary = Depends(get_compose_store),
    ) -> Response:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        stack = database.get_docker_stack(stack_id)
        if stack is None:
            raise HTTPException(status_code=404, detail="Compose stack not found.")
        if database.docker_stack_has_active_deployments(stack["id"]):
            raise HTTPException(status_code=409, detail="Bring the stack down before deleting it.")
        try:
            compose_store.delete_stack(str(stack["slug"]))
        except DockerStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        database.delete_docker_stack(stack["id"])
        database.audit(
            actor=user["username"],
            action="docker.stack_deleted",
            target=stack["id"],
            detail={"slug": stack["slug"]},
            ip=client_ip(request, config.trust_proxy),
        )
        return Response(status_code=204)

    @app.post("/api/docker/stacks/{stack_id}/deploy")
    def deploy_docker_stack(
        stack_id: str,
        payload: DockerDeployIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        stack = database.get_docker_stack(stack_id)
        if stack is None:
            raise HTTPException(status_code=404, detail="Compose stack not found.")
        targets = payload.agent_ids or ([payload.agent_id] if payload.agent_id else [])
        if not targets:
            raise HTTPException(status_code=400, detail="Select at least one Docker host.")
        if len(targets) > 20:
            raise HTTPException(status_code=400, detail="At most 20 Docker hosts can be deployed at once.")

        def deploy_one(agent_id: str) -> dict[str, Any]:
            host_row, _agent_row, _profile_row, capabilities = _docker_context(
                database, agent_id, config
            )
            if not host_row["available"]:
                raise HTTPException(status_code=409, detail="Docker is not currently reachable on this host.")
            if not capabilities["compose"]:
                raise HTTPException(
                    status_code=409,
                    detail="Compose deployment is disabled by the agent profile or server policy.",
                )
            action = payload.action
            active = database.active_docker_deployment(
                agent_id=agent_id,
                project_name=payload.project_name,
                stack_id=stack["id"],
            )
            if action == "up":
                revision_hash = payload.revision_hash or str(stack["current_revision_hash"])
                project_name = payload.project_name or _default_project(str(stack["slug"]))
                pull_before_up = bool(payload.pull_before_up)
                remove_volumes = False
            else:
                if active is None:
                    raise HTTPException(
                        status_code=409,
                        detail="No active deployment for this stack on the selected host.",
                    )
                revision_hash = str(active["revision_hash"])
                project_name = payload.project_name or str(active["project_name"])
                pull_before_up = False
                remove_volumes = bool(payload.remove_volumes) if action == "down" else False
            if not re.match(r"^[a-z0-9][a-z0-9_-]{0,62}$", project_name):
                raise HTTPException(status_code=400, detail="Invalid Compose project name.")
            revision = database.get_docker_stack_revision(stack["id"], revision_hash)
            if revision is None:
                raise HTTPException(status_code=404, detail="Stack revision not found.")
            if action == "up":
                collision = database.docker_project_in_use(
                    agent_id=agent_id, project_name=project_name
                )
                if collision is not None and collision["stack_id"] != stack["id"]:
                    raise HTTPException(status_code=409, detail="Compose project name is already active on this host.")
                for container in database.list_docker_containers(agent_id):
                    if (
                        container["compose_project"] == project_name
                        and (active is None or active["project_name"] != project_name)
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail="Compose project name collides with an external project on this host.",
                        )
            deployment = database.create_docker_deployment(
                stack_id=stack["id"],
                agent_id=agent_id,
                revision_hash=revision_hash,
                project_name=project_name,
                action=action,
                command_id="",
                actor=user["username"],
                pull_before_up=pull_before_up,
                remove_volumes=remove_volumes,
            )
            try:
                command = queue_command(
                    agent_id,
                    "docker",
                    {
                        "action": f"compose.{action}",
                        "deployment_id": deployment["id"],
                        "stack_id": stack["id"],
                        "revision_hash": revision_hash,
                        "project_name": project_name,
                    },
                    user,
                    request,
                    database,
                    config,
                    audit_action="docker.deploy_requested",
                    extra_detail={
                        "deployment_id": deployment["id"],
                        "stack_id": stack["id"],
                        "slug": stack["slug"],
                        "revision_hash": revision_hash,
                        "project_name": project_name,
                        "action": action,
                        "agent_name": host_row["agent_name"],
                    },
                )
            except Exception:
                database.delete_docker_deployment(deployment["id"])
                raise
            database.set_docker_deployment_command(deployment["id"], command["id"])
            deployment = database.get_docker_deployment(deployment["id"])
            command["queued"] = True
            return {
                "agent_id": agent_id,
                "agent_name": host_row["agent_name"],
                "status": "queued",
                "deployment": _deployment_payload(database, deployment),
                "command": command,
            }

        results: list[dict[str, Any]] = []
        for agent_id in targets:
            try:
                results.append(deploy_one(agent_id))
            except HTTPException as exc:
                results.append(
                    {
                        "agent_id": agent_id,
                        "status": "failed",
                        "error": str(exc.detail),
                        "status_code": exc.status_code,
                    }
                )
            except Exception:
                logger.exception("docker multi-host deploy failed for %s", agent_id)
                results.append(
                    {
                        "agent_id": agent_id,
                        "status": "failed",
                        "error": "Deployment failed on this host.",
                        "status_code": 500,
                    }
                )
        summary = {
            "total": len(results),
            "queued": sum(1 for item in results if item["status"] == "queued"),
            "failed": sum(1 for item in results if item["status"] == "failed"),
        }
        if not payload.agent_ids:
            first = results[0]
            if first["status"] == "failed":
                raise HTTPException(
                    status_code=first.get("status_code", 409),
                    detail=first.get("error", "Deployment failed."),
                )
            return {
                "deployment": first["deployment"],
                "command": first["command"],
                "summary": summary,
            }
        return {"results": results, "summary": summary}

    @app.get("/api/docker/deployments")
    def list_docker_deployments(
        agent_id: str = "",
        stack_id: str = "",
        limit: int = 100,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        rows = database.list_docker_deployments(
            agent_id=agent_id[:64],
            stack_id=stack_id[:64],
            limit=max(1, min(int(limit), 500)),
        )
        return {"deployments": [_deployment_payload(database, row) for row in rows]}

    @app.get("/api/docker/deployments/{deployment_id}")
    def get_docker_deployment(
        deployment_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        deployment = database.get_docker_deployment(deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Docker deployment not found.")
        return {"deployment": _deployment_payload(database, deployment)}

    @app.get("/api/docker/settings")
    def get_docker_settings(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        compose_store: ComposeLibrary = Depends(get_compose_store),
    ) -> dict[str, Any]:
        del user, database
        return {
            "compose_dir": str(compose_store.root),
            "source": "env" if config.docker_compose_dir_from_env else "database",
            "editable": not config.docker_compose_dir_from_env,
            "max_file_bytes": config.docker_max_file_bytes,
            "max_files": config.docker_max_files,
            "max_total_bytes": config.docker_max_total_bytes,
            "max_bundle_bytes": config.docker_max_bundle_bytes,
            "deployment_retention_days": config.docker_deployment_retention_days,
            "counts": compose_store.counts(),
        }

    @app.patch("/api/docker/settings")
    def update_docker_settings(
        payload: DockerSettingsIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        compose_store: ComposeLibrary = Depends(get_compose_store),
    ) -> dict[str, Any]:
        if config.docker_compose_dir_from_env:
            raise HTTPException(status_code=409, detail="Compose directory is fixed by SEXTANT_DOCKER_COMPOSE_DIR.")
        try:
            counts = compose_store.migrate_to(Path(payload.compose_dir))
        except DockerStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        database.set_docker_compose_setting(str(compose_store.root))
        database.audit(
            actor=user["username"],
            action="docker.compose_dir_updated",
            target=str(compose_store.root),
            detail=counts,
            ip=client_ip(request, config.trust_proxy),
        )
        return {
            "compose_dir": str(compose_store.root),
            "source": "database",
            "editable": True,
            "counts": compose_store.counts(),
        }

    @app.get("/api/agent/{agent_id}/docker/deployments/{deployment_id}/bundle")
    def download_docker_deployment_bundle(
        agent_id: str,
        deployment_id: str,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        compose_store: ComposeLibrary = Depends(get_compose_store),
    ) -> Response:
        del agent
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        deployment = database.get_docker_deployment(deployment_id)
        if deployment is None or deployment["agent_id"] != agent_id:
            raise HTTPException(status_code=404, detail="Docker deployment not found.")
        if deployment["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Docker deployment was cancelled.")
        stack = database.get_docker_stack(str(deployment["stack_id"]))
        if stack is None:
            raise HTTPException(status_code=404, detail="Compose stack not found.")
        try:
            data = compose_store.build_bundle(str(stack["slug"]), str(deployment["revision_hash"]))
        except DockerStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        headers = {
            "X-Bundle-Sha256": hashlib.sha256(data).hexdigest(),
            "X-Compose-Revision": str(deployment["revision_hash"]),
            "X-Compose-Pull": "1" if deployment["pull_before_up"] else "0",
            "X-Compose-Remove-Volumes": "1" if deployment["remove_volumes"] else "0",
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store",
        }
        return Response(content=data, media_type="application/gzip", headers=headers)

    def _prune_console_sessions(
        database: Database,
        broker: ConsoleBroker,
        config: Settings,
    ) -> None:
        for session_id, status, error in broker.prune(
            idle_seconds=config.docker_console_idle_seconds,
            poll_seconds=config.docker_console_poll_seconds,
        ):
            database.close_console_session(session_id, status=status, error=error)

    def _console_for_user(
        database: Database,
        session_id: str,
        user: dict[str, Any],
    ) -> sqlite3.Row:
        row = database.get_console_session(session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Console session not found.")
        if row["created_by"] != user["username"]:
            raise HTTPException(status_code=403, detail="This console belongs to another operator.")
        return row

    @app.post("/api/docker/hosts/{agent_id}/containers/{container_id}/console")
    def open_docker_console(
        agent_id: str,
        container_id: str,
        payload: DockerConsoleOpenIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        host_row, _agent_row, _profile_row, capabilities = _docker_context(
            database, agent_id, config
        )
        if not host_row["available"]:
            raise HTTPException(status_code=409, detail="Docker is not currently reachable on this host.")
        if not capabilities["exec"]:
            raise HTTPException(
                status_code=409,
                detail="Docker exec is disabled by the agent profile or server policy.",
            )
        container = database.find_docker_container(agent_id, container_id)
        if container is None:
            raise HTTPException(status_code=404, detail="Container not found in the latest inventory.")
        _prune_console_sessions(database, broker, config)
        session_id = "con_" + secrets.token_hex(12)
        try:
            broker.create(
                session_id,
                agent_id=agent_id,
                created_by=user["username"],
                rows=payload.rows,
                cols=payload.cols,
            )
        except ConsoleError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        database.create_console_session(
            session_id,
            agent_id=agent_id,
            container_id=str(container["container_id"]),
            shell=payload.shell,
            user=payload.user,
            tty=True,
            created_by=user["username"],
            client_ip=client_ip(request, config.trust_proxy),
        )
        try:
            command = queue_command(
                agent_id,
                "docker",
                {
                    "action": "console.open",
                    "session_id": session_id,
                    "container_id": container["container_id"],
                    "shell": payload.shell,
                    "user": payload.user,
                    "workdir": payload.workdir,
                    "rows": payload.rows,
                    "cols": payload.cols,
                },
                user,
                request,
                database,
                config,
                audit_action="docker.console_opened",
                extra_detail={
                    "session_id": session_id,
                    "container_id": container["container_id"],
                    "container_name": container["name"],
                    "shell": payload.shell,
                    "user": payload.user,
                },
            )
        except Exception as exc:
            broker.finish(session_id, status="failed", exit_code=None, error=str(exc))
            database.close_console_session(session_id, status="failed", error=str(exc))
            raise
        return {
            "session_id": session_id,
            "status": "opening",
            "idle_seconds": config.docker_console_idle_seconds,
            "poll_seconds": config.docker_console_poll_seconds,
            "rows": payload.rows,
            "cols": payload.cols,
            "shell": payload.shell,
            "user": payload.user,
            "container_id": container["container_id"],
            "command_id": command["id"],
        }

    @app.get("/api/docker/console/{session_id}/events")
    async def docker_console_events(
        request: Request,
        session_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> StreamingResponse:
        row = _console_for_user(database, session_id, user)
        del row

        async def stream() -> Any:
            loop = asyncio.get_running_loop()
            last_seq = 0
            last_status = ""
            next_auth = loop.time() + 5
            next_heartbeat = loop.time() + 15
            while True:
                if loop.time() >= next_auth:
                    next_auth = loop.time() + 5
                    try:
                        authenticated_user(request, database, config)
                    except HTTPException:
                        broker.request_close(session_id, status="failed", error="Operator session expired.")
                        database.close_console_session(
                            session_id, status="failed", error="Operator session expired."
                        )
                        yield _sse_event("auth", {"authenticated": False})
                        return
                _prune_console_sessions(database, broker, config)
                current = database.get_console_session(session_id)
                if current is None:
                    yield _sse_event("auth", {"authenticated": False})
                    return
                state = broker.status(session_id)
                status = state["status"] if state else str(current["status"])
                if status != last_status:
                    last_status = status
                    yield _sse_event(
                        "status",
                        {
                            "session_id": session_id,
                            "status": status,
                            "exit_code": state["exit_code"] if state else current["exit_code"],
                            "error": state["error"] if state else current["error"],
                            "idle_seconds": config.docker_console_idle_seconds,
                            "poll_seconds": config.docker_console_poll_seconds,
                        },
                    )
                for seq, data in broker.outputs_since(session_id, last_seq):
                    last_seq = seq
                    yield _sse_event(
                        "output",
                        {"session_id": session_id, "seq": seq, "data": base64.b64encode(data).decode("ascii")},
                    )
                if status in {"closed", "failed", "cancelled", "exited"} or current["closed_at"]:
                    return
                if loop.time() >= next_heartbeat:
                    next_heartbeat = loop.time() + 15
                    yield _sse_comment()
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/docker/console/{session_id}/input")
    def docker_console_input(
        session_id: str,
        payload: DockerConsoleInputIn,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> Response:
        row = _console_for_user(database, session_id, user)
        if str(row["status"]) not in {"opening", "open"}:
            raise HTTPException(status_code=409, detail="Console is not open.")
        try:
            data = base64.b64decode(payload.data, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid console input encoding.")
        if len(data) > config.docker_console_max_input_bytes:
            raise HTTPException(status_code=413, detail="Console input chunk is too large.")
        result = broker.push_input(session_id, data)
        if result == "rate":
            raise HTTPException(status_code=429, detail="Console input rate exceeded.")
        if result == "full":
            raise HTTPException(status_code=413, detail="Console input buffer is full.")
        if result == "closed":
            raise HTTPException(status_code=409, detail="Console is not open.")
        database.mark_console_seen(session_id)
        return Response(status_code=204)

    @app.post("/api/docker/console/{session_id}/resize")
    def docker_console_resize(
        session_id: str,
        payload: DockerConsoleResizeIn,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> Response:
        row = _console_for_user(database, session_id, user)
        if str(row["status"]) not in {"opening", "open"}:
            raise HTTPException(status_code=409, detail="Console is not open.")
        if broker.push_resize(session_id, payload.rows, payload.cols) == "closed":
            raise HTTPException(status_code=409, detail="Console is not open.")
        database.mark_console_seen(session_id)
        return Response(status_code=204)

    @app.delete("/api/docker/console/{session_id}")
    def docker_console_close(
        session_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> Response:
        row = _console_for_user(database, session_id, user)
        broker.request_close(session_id, status="closing")
        database.mark_console_seen(session_id, status="closing")
        database.audit(
            actor=user["username"],
            action="docker.console_close_requested",
            target=session_id,
            detail={"session_id": session_id, "container_id": row["container_id"]},
            ip=client_ip(request, config.trust_proxy),
        )
        return Response(status_code=204)

    @app.post("/api/agent/{agent_id}/docker/console/{session_id}/poll")
    def agent_docker_console_poll(
        agent_id: str,
        session_id: str,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> Any:
        del agent
        row = database.get_console_session(session_id)
        if row is None or row["agent_id"] != agent_id:
            raise HTTPException(status_code=404, detail="Console session not found.")
        if row["status"] not in {"opening", "open", "closing"}:
            return {"type": "close"}
        broker.mark_poll(session_id)
        if row["status"] == "opening":
            broker.mark_open(session_id)
            database.mark_console_seen(session_id, status="open")
        else:
            database.mark_console_seen(session_id)
        result = broker.poll(session_id, config.docker_console_poll_seconds)
        if result is None:
            return Response(status_code=204)
        return result

    @app.post("/api/agent/{agent_id}/docker/console/{session_id}/output")
    def agent_docker_console_output(
        agent_id: str,
        session_id: str,
        payload: DockerConsoleOutputIn,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> Response:
        del agent
        row = database.get_console_session(session_id)
        if row is None or row["agent_id"] != agent_id:
            raise HTTPException(status_code=404, detail="Console session not found.")
        if row["status"] not in {"opening", "open", "closing"}:
            raise HTTPException(status_code=409, detail="Console is not open.")
        try:
            data = base64.b64decode(payload.data, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid console output encoding.")
        if len(data) > config.docker_console_max_output_bytes:
            raise HTTPException(status_code=413, detail="Console output chunk is too large.")
        if not broker.push_output(session_id, data):
            raise HTTPException(status_code=409, detail="Console is not open.")
        database.mark_console_seen(session_id)
        return Response(status_code=204)

    @app.post("/api/agent/{agent_id}/docker/console/{session_id}/closed")
    def agent_docker_console_closed(
        agent_id: str,
        session_id: str,
        payload: DockerConsoleClosedIn,
        request: Request,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> Response:
        del agent
        row = database.get_console_session(session_id)
        if row is None or row["agent_id"] != agent_id:
            raise HTTPException(status_code=404, detail="Console session not found.")
        status = (
            "closed"
            if str(row["status"]) == "closing"
            else "exited"
            if payload.exit_code == 0
            else "failed"
        )
        broker.finish(session_id, status=status, exit_code=payload.exit_code, error=payload.error)
        database.close_console_session(
            session_id,
            status=status,
            exit_code=payload.exit_code,
            error=payload.error,
        )
        database.audit(
            actor="agent",
            action="docker.console_closed",
            target=session_id,
            detail={
                "session_id": session_id,
                "container_id": row["container_id"],
                "status": status,
                "exit_code": payload.exit_code,
                "error": payload.error[:1000],
            },
            ip=client_ip(request, config.trust_proxy),
        )
        return Response(status_code=204)

    @app.get("/api/events")
    async def dashboard_events(
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> StreamingResponse:
        """Authenticated SSE stream of dashboard payloads.

        The first event is sent immediately. After that the stream wakes on a
        dashboard revision change (an agent heartbeat, command change, alert
        action, and so on), with a bounded periodic refresh for time-derived
        fields such as online/offline. Concurrent clients share one cached
        build per revision. The session is revalidated while the stream is
        open, and the stream closes if the session is revoked or invalid.
        """
        del user

        async def stream() -> Any:
            loop = asyncio.get_running_loop()
            listener, changed = dashboard_state.subscribe()
            last_revision = -1
            next_refresh = loop.time() + DASHBOARD_SAFETY_REFRESH_SECONDS
            next_heartbeat = loop.time() + SSE_HEARTBEAT_SECONDS
            next_auth = loop.time() + DASHBOARD_AUTH_RECHECK_SECONDS
            try:
                while True:
                    now = loop.time()
                    revision = dashboard_state.revision()
                    refresh_due = now >= next_refresh
                    if last_revision < 0 or revision != last_revision or refresh_due:
                        failed = False
                        try:
                            payload = await dashboard_state.payload(
                                revision,
                                lambda: dashboard_payload(database, config),
                                max_age=(
                                    DASHBOARD_CACHE_MAX_AGE_SECONDS if refresh_due else None
                                ),
                            )
                        except Exception:  # keep the stream alive across transient DB errors
                            failed = True
                            logger.exception("Dashboard SSE refresh failed")
                        else:
                            yield _sse_event("dashboard", payload)
                            last_revision = revision
                        if refresh_due:
                            next_refresh = loop.time() + (
                                1.0 if failed else DASHBOARD_SAFETY_REFRESH_SECONDS
                            )
                        elif failed and last_revision < 0:
                            # Do not retry a failing initial build in a tight loop.
                            last_revision = revision

                    now = loop.time()
                    if now >= next_auth:
                        try:
                            authenticated_user(request, database, config)
                        except HTTPException:
                            yield _sse_event("auth", {"authenticated": False})
                            return
                        next_auth = loop.time() + DASHBOARD_AUTH_RECHECK_SECONDS

                    now = loop.time()
                    if now >= next_heartbeat:
                        yield _sse_comment()
                        next_heartbeat = loop.time() + SSE_HEARTBEAT_SECONDS

                    # Clear before checking the revision so a notify between the
                    # check and the wait cannot be lost.
                    changed.clear()
                    if dashboard_state.revision() != last_revision:
                        continue
                    now = loop.time()
                    timeout = min(next_refresh - now, next_heartbeat - now, next_auth - now)
                    if timeout > 0:
                        try:
                            await asyncio.wait_for(changed.wait(), timeout)
                        except asyncio.TimeoutError:
                            pass
            finally:
                dashboard_state.unsubscribe(listener)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/agents/bulk")
    def bulk_agents(
        payload: BulkActionIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        args = payload.args if isinstance(payload.args, dict) else {}
        action = payload.action
        if action == "refresh_telemetry":
            action = "refresh"
        elif action == "profile":
            action = "profile_apply"
        elif action in {"service", "power"}:
            requested = str(args.get("action", "")).lower()
            if action == "service" and requested in {"start", "stop", "restart"}:
                action = f"service_{requested}"
            elif action == "power" and requested in {"reboot", "shutdown"}:
                action = requested
            else:
                raise HTTPException(status_code=400, detail=f"Invalid {payload.action} action arguments.")
        if action not in BULK_ACTIONS:
            raise HTTPException(status_code=400, detail="Unsupported bulk action.")
        if action == "profile_apply" and not role_at_least(user, "admin"):
            raise HTTPException(status_code=403, detail="Administrator access required for profile apply.")
        agent_ids = list(dict.fromkeys(str(item).strip() for item in payload.agent_ids if str(item).strip()))
        if not agent_ids:
            raise HTTPException(status_code=400, detail="At least one agent_id is required.")
        if len(agent_ids) > BULK_ACTION_MAX_AGENTS:
            raise HTTPException(
                status_code=400,
                detail=f"At most {BULK_ACTION_MAX_AGENTS} agents may be targeted per bulk request.",
            )
        command_kind: str | None = None
        command_args: dict[str, Any] = {}
        wake_payload = AgentWakeIn()
        profile_row: sqlite3.Row | None = None
        try:
            if action == "refresh":
                command_kind = "refresh"
                command_args = validate_command_args("refresh", {})
            elif action in {"reboot", "shutdown"}:
                command_kind = "power"
                command_args = validate_command_args(
                    "power",
                    {"action": action, "delay": args.get("delay", 30)},
                )
            elif action.startswith("service_"):
                command_kind = "service"
                command_args = validate_command_args(
                    "service",
                    {"action": action.split("_", 1)[1], "name": args.get("name", "")},
                )
            elif action == "wake":
                wake_payload = AgentWakeIn(
                    broadcast=str(args.get("broadcast", "255.255.255.255")),
                    port=int(args.get("port", 9)),
                    packets=int(args.get("packets", 3)),
                )
            elif action == "profile_apply":
                profile_value = str(args.get("profile", "")).strip()
                if not profile_value:
                    raise ValueError("profile is required for profile apply")
                profile_row = database.get_profile_by_id(profile_value) or database.get_profile_by_slug(profile_value)
                if profile_row is None:
                    raise ValueError("unknown agent profile")
        except (TypeError, ValueError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        bulk_id = "blk_" + secrets.token_hex(6)
        results: list[dict[str, Any]] = []
        counts = {"queued": 0, "sent": 0, "updated": 0, "failed": 0}
        for agent_id in agent_ids:
            with database.connection() as conn:
                name_row = conn.execute("SELECT name FROM agents WHERE id = ?", (agent_id,)).fetchone()
            agent_name = name_row["name"] if name_row is not None else agent_id
            try:
                if action == "profile_apply":
                    assert profile_row is not None
                    updated = apply_agent_profile(
                        agent_id,
                        profile_row["slug"],
                        user,
                        request,
                        database,
                        config,
                        extra_detail={"bulk_id": bulk_id, "bulk_action": action},
                    )
                    agent_name = updated["name"]
                    status = "updated"
                    message = f"Profile set to {profile_row['name']}."
                elif action == "wake":
                    result = send_agent_wake(
                        agent_id,
                        wake_payload,
                        user,
                        request,
                        database,
                        config,
                        extra_detail={"bulk_id": bulk_id, "bulk_action": action},
                    )
                    status = "sent"
                    message = f"Wake packet sent ({result['sent']} packets)."
                else:
                    assert command_kind is not None
                    command = queue_command(
                        agent_id,
                        command_kind,
                        command_args,
                        user,
                        request,
                        database,
                        config,
                        extra_detail={"bulk_id": bulk_id, "bulk_action": action},
                    )
                    status = "queued"
                    if command_kind == "refresh":
                        message = "Telemetry refresh queued."
                    elif command_kind == "power":
                        message = f"{action.capitalize()} queued with a {command_args['delay']}s delay."
                    else:
                        message = f"Service {command_args['action']} queued for {command_args['name']}."
                    results.append(
                        {
                            "agent_id": agent_id,
                            "name": agent_name,
                            "status": status,
                            "message": message,
                            "command_id": command["id"],
                        }
                    )
                    counts[status] += 1
                    continue
                results.append(
                    {
                        "agent_id": agent_id,
                        "name": agent_name,
                        "status": status,
                        "message": message,
                    }
                )
                counts[status] += 1
            except HTTPException as exc:
                results.append(
                    {
                        "agent_id": agent_id,
                        "name": agent_name,
                        "status": "failed",
                        "message": str(exc.detail),
                    }
                )
                counts["failed"] += 1
            except Exception:  # noqa: BLE001 - one agent must not abort the batch
                logger.exception("Bulk action %s failed for %s", action, agent_id)
                results.append(
                    {
                        "agent_id": agent_id,
                        "name": agent_name,
                        "status": "failed",
                        "message": "Unexpected error while applying the action.",
                    }
                )
                counts["failed"] += 1

        summary = {"total": len(agent_ids), **counts}
        database.audit(
            actor=user["username"],
            action="agents.bulk",
            target=f"bulk:{bulk_id}",
            detail={
                "action": action,
                "bulk_id": bulk_id,
                "agent_ids": agent_ids,
                "args": command_args or (wake_payload.model_dump() if action == "wake" else args),
                "summary": summary,
            },
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {"action": action, "bulk_id": bulk_id, "results": results, "summary": summary}

    @app.get("/api/agents/{agent_id}")
    def agent_detail(
        agent_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        now = utcnow()
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            commands = conn.execute(
                "SELECT * FROM commands WHERE agent_id = ? ORDER BY created_at DESC LIMIT 50",
                (agent_id,),
            ).fetchall()
            states = _reconcile_agent_alerts(database, row, config, now, conn)
        return {
            "agent": agent_to_dict(row, config, now=now, alert_states=states),
            "commands": [command_to_dict(command, full_output=False) for command in commands],
        }

    @app.post("/api/agents/{agent_id}/alerts/{alert_key}/ack")
    def acknowledge_agent_alert(
        agent_id: str,
        alert_key: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            prior_state = database.get_alert_states(agent_id, conn=conn).get(alert_key)
            states = _reconcile_agent_alerts(database, row, config, utcnow(), conn)
            alert = _find_agent_alert(row, config, alert_key, states)
            if alert is None:
                raise HTTPException(status_code=404, detail="Alert not found.")
            database.set_alert_ack(agent_id, alert_key, user["username"], conn=conn)
            reacknowledged = bool(
                prior_state and (prior_state["acknowledged_at"] or prior_state["cleared_at"])
            )
            action = "reacknowledged" if reacknowledged else "acknowledged"
            database.audit(
                actor=user["username"],
                action=f"alert.{action}",
                target=agent_id,
                detail={
                    "alert_key": alert_key,
                    "incident_id": alert["incident_id"],
                    "level": alert["level"],
                    "message": alert["message"],
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            database.record_agent_event(
                agent_id,
                "alert",
                alert["level"],
                f"Alert {'re-acknowledged' if reacknowledged else 'acknowledged'}: {alert['message']}",
                {
                    "action": action,
                    "alert_key": alert_key,
                    "incident_id": alert["incident_id"],
                    "level": alert["level"],
                    "message": alert["message"],
                    "actor": user["username"],
                },
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            states = database.get_alert_states(agent_id, conn=conn)
            decorated = agent_to_dict(updated, config, alert_states=states)["alerts"]
        notify_dashboard()
        return {"alert": next(item for item in decorated if item["key"] == alert_key)}

    @app.post("/api/agents/{agent_id}/alerts/{alert_key}/snooze")
    def snooze_agent_alert(
        agent_id: str,
        alert_key: str,
        payload: AlertSnoozeIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            states = _reconcile_agent_alerts(database, row, config, utcnow(), conn)
            alert = _find_agent_alert(row, config, alert_key, states)
            if alert is None:
                raise HTTPException(status_code=404, detail="Alert not found.")
            snoozed_until = database.set_alert_snooze(agent_id, alert_key, payload.minutes, conn=conn)
            database.audit(
                actor=user["username"],
                action="alert.snoozed",
                target=agent_id,
                detail={
                    "alert_key": alert_key,
                    "incident_id": alert["incident_id"],
                    "level": alert["level"],
                    "message": alert["message"],
                    "minutes": payload.minutes,
                    "snoozed_until": snoozed_until,
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            database.record_agent_event(
                agent_id,
                "alert",
                alert["level"],
                f"Alert snoozed: {alert['message']}",
                {
                    "action": "snoozed",
                    "alert_key": alert_key,
                    "incident_id": alert["incident_id"],
                    "level": alert["level"],
                    "message": alert["message"],
                    "minutes": payload.minutes,
                    "snoozed_until": snoozed_until,
                    "actor": user["username"],
                },
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            states = database.get_alert_states(agent_id, conn=conn)
            decorated = agent_to_dict(updated, config, alert_states=states)["alerts"]
        notify_dashboard()
        return {"alert": next(item for item in decorated if item["key"] == alert_key)}

    @app.delete("/api/agents/{agent_id}/alerts/{alert_key}")
    def clear_agent_alert(
        agent_id: str,
        alert_key: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            states = _reconcile_agent_alerts(database, row, config, utcnow(), conn)
            alert = _find_agent_alert(row, config, alert_key, states)
            if alert is None:
                raise HTTPException(status_code=404, detail="Alert not found.")
            database.clear_alert_state(agent_id, alert_key, conn=conn)
            database.audit(
                actor=user["username"],
                action="alert.cleared",
                target=agent_id,
                detail={
                    "alert_key": alert_key,
                    "incident_id": alert["incident_id"],
                    "level": alert["level"],
                    "message": alert["message"],
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            database.record_agent_event(
                agent_id,
                "alert",
                "info",
                f"Alert cleared: {alert['message']}",
                {
                    "action": "cleared",
                    "alert_key": alert_key,
                    "incident_id": alert["incident_id"],
                    "level": alert["level"],
                    "message": alert["message"],
                    "actor": user["username"],
                },
                conn=conn,
            )
        notify_dashboard()
        return Response(status_code=204)

    @app.get("/api/agents/{agent_id}/history")
    def agent_history(
        agent_id: str,
        hours: int = Query(default=24, description="Lookback window in hours (1-720)"),
        points: int = Query(default=200, description="Maximum number of time buckets (10-1000)"),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        if not 1 <= hours <= 720:
            raise HTTPException(status_code=400, detail="hours must be between 1 and 720.")
        if not 10 <= points <= 1000:
            raise HTTPException(status_code=400, detail="points must be between 10 and 1000.")
        with database.connection() as conn:
            exists = conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        rows = database.telemetry_history(agent_id, hours, points)
        return {
            "agent_id": agent_id,
            "hours": hours,
            "points": [telemetry_sample_to_dict(row) for row in rows],
        }

    @app.get("/api/agents/{agent_id}/events")
    def agent_events(
        agent_id: str,
        hours: int = Query(default=24, description="Lookback window in hours (1-720)"),
        limit: int = Query(default=100, description="Maximum events to return (1-500)"),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        if not 1 <= hours <= 720:
            raise HTTPException(status_code=400, detail="hours must be between 1 and 720.")
        if not 1 <= limit <= 500:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 500.")
        with database.connection() as conn:
            exists = conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        rows = database.list_agent_events(agent_id, hours, limit)
        return {
            "agent_id": agent_id,
            "hours": hours,
            "events": [agent_event_to_dict(row) for row in rows],
        }

    @app.patch("/api/agents/{agent_id}")
    def update_agent(
        agent_id: str,
        payload: AgentUpdateIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if all(
            value is None
            for value in (
                payload.name,
                payload.tags,
                payload.notes,
                payload.watch_services,
                payload.profile,
                payload.allow_shell,
                payload.allow_power,
                payload.allow_service,
                payload.allow_packages,
                payload.allow_docker,
                payload.allow_docker_exec,
                payload.allow_docker_compose,
                payload.allow_docker_prune,
                payload.allow_discovery,
            )
        ):
            raise HTTPException(status_code=400, detail="No agent changes supplied.")
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            previous_profile_id = row["profile_id"]
            tags = loads(row["tags"], [])
            watch_services = loads(row["watch_services"], [])
            policy = loads(row["policy"], {})
            if not isinstance(tags, list):
                tags = []
            if not isinstance(watch_services, list):
                watch_services = []
            if not isinstance(policy, dict):
                policy = {}
            if payload.name is not None:
                row_name = payload.name.strip()
            else:
                row_name = row["name"]
            if payload.tags is not None:
                tags = [str(tag).strip()[:64] for tag in payload.tags if str(tag).strip()][:32]
            if payload.watch_services is not None:
                watch_services = [
                    str(service).strip()[:255]
                    for service in payload.watch_services
                    if str(service).strip()
                ][:64]
            profile_id = row["profile_id"]
            if payload.profile is not None:
                profile_row = resolve_agent_profile(conn, payload.profile)
                profile_id = profile_row["id"]
            elif payload.tags is not None:
                tag_profile_id = database.profile_for_tags(tags)
                if tag_profile_id:
                    profile_id = tag_profile_id
            for flag in (
                "allow_shell",
                "allow_power",
                "allow_service",
                "allow_packages",
                "allow_docker",
                "allow_docker_exec",
                "allow_docker_compose",
                "allow_docker_prune",
                "allow_discovery",
            ):
                value = getattr(payload, flag)
                if value is not None:
                    policy[flag] = bool(value)
            conn.execute(
                """UPDATE agents SET name = ?, tags = ?, notes = ?, watch_services = ?,
                   profile_id = ?, policy = ? WHERE id = ?""",
                (
                    row_name,
                    dumps(tags),
                    payload.notes if payload.notes is not None else row["notes"],
                    dumps(watch_services),
                    profile_id,
                    dumps(
                        {
                            "allow_shell": bool(policy.get("allow_shell", True)),
                            "allow_power": bool(policy.get("allow_power", True)),
                            "allow_service": bool(policy.get("allow_service", True)),
                            "allow_packages": bool(policy.get("allow_packages", True)),
                            "allow_docker": bool(policy.get("allow_docker", False)),
                            "allow_docker_exec": bool(policy.get("allow_docker_exec", False)),
                            "allow_docker_compose": bool(policy.get("allow_docker_compose", False)),
                            "allow_docker_prune": bool(policy.get("allow_docker_prune", False)),
                            "allow_discovery": bool(policy.get("allow_discovery", False)),
                        }
                    ),
                    agent_id,
                ),
            )
            database.audit(
                actor=user["username"],
                action="agent.updated",
                target=agent_id,
                detail={
                    "name": row_name,
                    "tags": tags,
                    "watch_services": watch_services,
                    "profile_id": profile_id,
                    "allow_shell": bool(policy.get("allow_shell", True)),
                    "allow_power": bool(policy.get("allow_power", True)),
                    "allow_service": bool(policy.get("allow_service", True)),
                    "allow_packages": bool(policy.get("allow_packages", True)),
                    "allow_docker": bool(policy.get("allow_docker", False)),
                    "allow_docker_exec": bool(policy.get("allow_docker_exec", False)),
                    "allow_docker_compose": bool(policy.get("allow_docker_compose", False)),
                    "allow_docker_prune": bool(policy.get("allow_docker_prune", False)),
                    "allow_discovery": bool(policy.get("allow_discovery", False)),
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if previous_profile_id != profile_id:
            _record_agent_events(
                database,
                [
                    {
                        "agent_id": agent_id,
                        "kind": "profile",
                        "severity": "info",
                        "title": "Agent profile changed",
                        "detail": {
                            "from": previous_profile_id,
                            "to": profile_id,
                            "actor": user["username"],
                        },
                    }
                ],
            )
        notify_dashboard()
        return agent_to_dict(updated, config, alert_states=database.get_alert_states(agent_id))

    @app.post("/api/agents/{agent_id}/commands")
    def create_command(
        agent_id: str,
        payload: CommandIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return queue_command(agent_id, payload.kind, payload.args, user, request, database, config)

    @app.post("/api/commands/{command_id}/cancel")
    def cancel_command(
        command_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Command not found.")
            if row["status"] != "queued":
                raise HTTPException(
                    status_code=409,
                    detail="Only commands still queued for an agent can be cancelled.",
                )
            conn.execute(
                """UPDATE commands SET status = 'cancelled', completed_at = ?, error = ?
                   WHERE id = ? AND status = 'queued'""",
                (utcnow_iso(), "Cancelled by operator before dispatch.", command_id),
            )
            if row["kind"] == "docker":
                args = loads(row["args"], {})
                if isinstance(args, dict) and str(args.get("action", "")).startswith("compose."):
                    conn.execute(
                        """UPDATE docker_deployments
                           SET status = 'cancelled', finished_at = ?, error = ?
                           WHERE command_id = ? AND status IN ('queued', 'running')""",
                        (
                            utcnow_iso(),
                            "Cancelled by operator before dispatch.",
                            command_id,
                        ),
                    )
            database.audit(
                actor=user["username"],
                action="command.cancelled",
                target=row["agent_id"],
                detail={"command_id": command_id},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        notify_dashboard()
        return command_to_dict(updated)

    @app.post("/api/commands/{command_id}/retry")
    def retry_command(
        command_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            original = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        if original is None:
            raise HTTPException(status_code=404, detail="Command not found.")
        if original["status"] not in {"failed", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail="Only failed or cancelled commands can be retried.",
            )
        return queue_command(
            original["agent_id"],
            original["kind"],
            loads(original["args"], {}),
            user,
            request,
            database,
            config,
            audit_action="command.retried",
            extra_detail={"original_command_id": command_id},
        )

    @app.get("/api/agents/{agent_id}/commands")
    def list_commands(
        agent_id: str,
        limit: int = COMMAND_HISTORY_DEFAULT_LIMIT,
        offset: int = 0,
        status: str = "",
        q: str = "",
        since: str = "",
        until: str = "",
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        limit, offset, status, q, since, until = _command_history_filters(
            limit, offset, status, q, since, until
        )
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        rows, total = database.command_history(
            agent_id,
            limit=limit,
            offset=offset,
            status=status,
            query=q,
            since=since,
            until=until,
        )
        return {
            "commands": [command_to_dict(row, full_output=False) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/commands/{command_id}")
    def get_command(
        command_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Command not found.")
        return {"command": command_to_dict(row)}

    @app.get("/api/commands/{command_id}/events")
    async def command_events(
        command_id: str,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
    ) -> StreamingResponse:
        """Live status, result, and incremental output delivery for one command.

        The first event is the current command snapshot. Stored output chunks
        are then replayed (bounded) so a reconnecting browser catches up. While
        the command is active this polls once per second, emitting new output
        chunks as ``output`` events and row changes as ``command`` events. It
        closes after the terminal command event and any final output. Operators
        only, matching the command-execution workflow in the UI.
        """
        del user
        with database.connection() as conn:
            if conn.execute("SELECT id FROM commands WHERE id = ?", (command_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Command not found.")

        async def stream() -> Any:
            loop = asyncio.get_running_loop()
            last_heartbeat = loop.time()
            last_signature: str | None = None
            last_output_id = 0
            first = True
            while True:
                with database.connection() as conn:
                    row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
                if row is None:
                    return
                payload = {"command": command_to_dict(row)}
                signature = dumps(payload)
                terminal = payload["command"]["status"] in COMMAND_TERMINAL_STATUSES
                if first:
                    yield _sse_event("command", payload)
                    last_signature = signature
                    chunks = await asyncio.to_thread(
                        database.replay_command_output,
                        command_id,
                        COMMAND_OUTPUT_REPLAY_LIMIT,
                    )
                    first = False
                else:
                    chunks = await asyncio.to_thread(
                        database.command_output_since,
                        command_id,
                        last_output_id,
                        COMMAND_OUTPUT_POLL_LIMIT,
                    )
                for chunk in chunks:
                    last_output_id = int(chunk["id"])
                    yield _sse_event(
                        "output",
                        {
                            "command_id": command_id,
                            "stream": chunk["stream"],
                            "seq": int(chunk["seq"]),
                            "text": chunk["text"],
                        },
                    )
                if not first and signature != last_signature:
                    # New output above is sent before the status update so a
                    # live UI can append it before rendering the canonical result.
                    yield _sse_event("command", payload)
                    last_signature = signature
                if terminal:
                    return
                if loop.time() - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                    yield _sse_comment()
                    last_heartbeat = loop.time()
                await asyncio.sleep(COMMAND_STREAM_SECONDS)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/agents/{agent_id}/wake")
    def wake_agent(
        agent_id: str,
        payload: AgentWakeIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return send_agent_wake(agent_id, payload, user, request, database, config)

    @app.get("/api/wake-targets")
    def list_wake_targets(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM wake_targets ORDER BY name COLLATE NOCASE").fetchall()
        return {"targets": [wake_target_to_dict(row) for row in rows]}

    @app.post("/api/wake-targets")
    def create_wake_target(
        payload: WakeTargetIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        try:
            mac = normalize_mac(payload.mac)
            broadcast = _validate_broadcast(payload.broadcast)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        target_id = "wol_" + secrets.token_hex(8)
        with database.connection() as conn:
            conn.execute(
                """INSERT INTO wake_targets (id, name, mac, ip, broadcast, port, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, payload.name, mac, payload.ip, broadcast, payload.port, user["username"], utcnow_iso()),
            )
            database.audit(
                actor=user["username"],
                action="wol.target_created",
                target=target_id,
                detail={"name": payload.name, "mac": mac},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            row = conn.execute("SELECT * FROM wake_targets WHERE id = ?", (target_id,)).fetchone()
        return wake_target_to_dict(row)

    @app.delete("/api/wake-targets/{target_id}")
    def delete_wake_target(
        target_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM wake_targets WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Wake target not found.")
            conn.execute("DELETE FROM wake_targets WHERE id = ?", (target_id,))
            database.audit(
                actor=user["username"],
                action="wol.target_deleted",
                target=target_id,
                detail={"name": row["name"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        return Response(status_code=204)

    @app.post("/api/wake-targets/{target_id}/wake")
    def wake_target(
        target_id: str,
        payload: WakeTargetWakeIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM wake_targets WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Wake target not found.")
            try:
                broadcast = _validate_broadcast(payload.broadcast or row["broadcast"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            port = payload.port or int(row["port"])
            try:
                packets = send_magic_packet(row["mac"], broadcast, port, payload.packets)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except OSError:
                raise HTTPException(status_code=502, detail="Could not send the wake packet.")
            now = utcnow_iso()
            conn.execute("UPDATE wake_targets SET last_woken_at = ? WHERE id = ?", (now, target_id))
            database.audit(
                actor=user["username"],
                action="wol.sent",
                target=target_id,
                detail={"mac": row["mac"], "broadcast": broadcast, "port": port},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            discovered = conn.execute(
                "SELECT * FROM discovered_devices WHERE wake_target_id = ?",
                (target_id,),
            ).fetchall()
            for device in discovered:
                database.update_discovered_device(
                    device["id"],
                    wake_status="sent",
                    wake_verified_at=now,
                    conn=conn,
                )
                database.record_discovery_event(
                    device["id"],
                    "wake_verified",
                    {"status": "sent", "method": "wake_packet", "target_id": target_id},
                    conn=conn,
                )
                database.audit(
                    actor=user["username"],
                    action="discovery.wake_verified",
                    target=device["id"],
                    detail={"status": "sent", "method": "wake_packet", "target_id": target_id},
                    ip=client_ip(request, config.trust_proxy),
                    conn=conn,
                )
                if device["agent_id"]:
                    database.record_agent_event(
                        str(device["agent_id"]),
                        "discovery",
                        "info",
                        f"Wake packet sent for {device['hostname'] or device['ip']}",
                        {"device_id": device["id"], "status": "sent"},
                        conn=conn,
                    )
        return {"sent": packets, "mac": row["mac"], "broadcast": broadcast, "port": port}

    @app.post("/api/enrollment-tokens")
    def create_enrollment_token(
        payload: EnrollmentTokenIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        token = new_token("sextant_enroll_")
        token_id = "enr_" + secrets.token_hex(8)
        now = utcnow()
        minutes = payload.expires_minutes if payload.expires_minutes is not None else config.enrollment_ttl_minutes
        expires = now + timedelta(minutes=minutes)
        with database.connection() as conn:
            conn.execute(
                """INSERT INTO enrollment_tokens
                   (id, token_hash, label, created_by, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    token_id,
                    hash_token(token),
                    payload.label,
                    user["username"],
                    now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
                ),
            )
            database.audit(
                actor=user["username"],
                action="enrollment.created",
                target=token_id,
                detail={"label": payload.label, "expires_at": expires.isoformat()},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        base = config.public_url or str(request.base_url).rstrip("/")
        return {
            "id": token_id,
            "token": token,
            "label": payload.label,
            "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "install_command": f"sextant-agent enroll --server {base} --token {token}",
        }

    @app.get("/api/enrollment-tokens")
    def list_enrollment_tokens(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        now = utcnow()
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM enrollment_tokens ORDER BY created_at DESC LIMIT 100").fetchall()
        tokens = []
        for row in rows:
            expires = parse_iso(row["expires_at"])
            tokens.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                    "used_at": row["used_at"],
                    "used_by_agent": row["used_by_agent"],
                    "state": "used" if row["used_at"] else ("expired" if expires and expires < now else "active"),
                }
            )
        return {"tokens": tokens}

    @app.delete("/api/enrollment-tokens/{token_id}")
    def delete_enrollment_token(
        token_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM enrollment_tokens WHERE id = ?", (token_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Enrollment token not found.")
            if row["used_at"]:
                raise HTTPException(status_code=409, detail="Enrollment token has already been used.")
            conn.execute("DELETE FROM enrollment_tokens WHERE id = ?", (token_id,))
            database.audit(
                actor=user["username"],
                action="enrollment.revoked",
                target=token_id,
                detail={"label": row["label"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        return Response(status_code=204)

    @app.get("/api/users")
    def list_users(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return {
            "users": [
                user_to_dict(row, session_count=database.count_sessions(row["id"]))
                for row in rows
            ]
        }

    @app.post("/api/users")
    def create_user(
        payload: UserCreateIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        problem = validate_password(payload.password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if database.get_user_by_name(payload.username) is not None:
            raise HTTPException(status_code=409, detail="That username already exists.")
        try:
            row = database.create_user(
                username=payload.username,
                role=payload.role,
                password_hash=hash_password(payload.password),
                display_name=payload.display_name,
                email=payload.email,
                source="local",
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="That username already exists.")
        database.audit(
            actor=user["username"],
            action="user.created",
            target=payload.username,
            detail={"role": payload.role},
            ip=client_ip(request, config.trust_proxy),
        )
        return user_to_dict(row)

    @app.patch("/api/users/{user_id}")
    def update_user(
        user_id: int,
        payload: UserUpdateIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> dict[str, Any]:
        if payload.role is None and payload.active is None:
            raise HTTPException(status_code=400, detail="Provide role and/or active.")
        if user_id == user["id"] and payload.active is False:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="User not found.")
            new_role = payload.role if payload.role is not None else row["role"]
            new_active = payload.active if payload.active is not None else bool(row["is_active"])
            if row["role"] == "admin" and row["is_active"] and (new_role != "admin" or not new_active):
                admins = conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
                ).fetchone()["n"]
                if int(admins) <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot demote or deactivate the last active administrator.",
                    )
            conn.execute(
                "UPDATE users SET role = ?, is_active = ? WHERE id = ?",
                (new_role, 1 if new_active else 0, user_id),
            )
            if row["is_active"] and not new_active:
                conn.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                    (utcnow_iso(), user_id),
                )
                conn.execute(
                    "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                    (user_id,),
                )
            database.audit(
                actor=user["username"],
                action="user.updated",
                target=row["username"],
                detail={
                    "from_role": row["role"],
                    "to_role": new_role,
                    "from_active": bool(row["is_active"]),
                    "to_active": new_active,
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row["is_active"] and not new_active:
            broker.close_for_user(row["username"], status="closed", error="Operator account disabled.")
            database.close_console_sessions_for_user(row["username"], status="closed")
        return user_to_dict(updated)

    @app.get("/api/audit")
    def audit_log(
        limit: int = 100,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        limit = max(1, min(int(limit), 500))
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {
            "events": [
                {
                    "id": row["id"],
                    "at": row["at"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "target": row["target"],
                    "detail": loads(row["detail"], {}),
                    "ip": row["ip"],
                }
                for row in rows
            ]
        }

    # ---- Discovery (D1-D5) -------------------------------------------

    def _discovery_device_or_404(device_id: str, database: Database) -> sqlite3.Row:
        row = database.get_discovered_device(device_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Discovered device not found.")
        return row

    def _promote_wake_target_row(
        device: sqlite3.Row,
        payload: DiscoveryWakeTargetIn | DiscoveryPromoteIn,
        user: dict[str, Any],
        request: Request,
        database: Database,
        config: Settings,
    ) -> dict[str, Any]:
        mac = normalize_mac(device["mac"])
        if not mac:
            raise HTTPException(
                status_code=400,
                detail="This device has no observed MAC address; a wake target cannot be created.",
            )
        try:
            broadcast = _validate_broadcast(payload.broadcast)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        name = (payload.name or device["hostname"] or device["ip"] or mac)[:120]
        with database.transaction() as conn:
            existing = conn.execute(
                """SELECT * FROM wake_targets
                   WHERE mac = ? COLLATE NOCASE AND mac != '' LIMIT 1""",
                (mac,),
            ).fetchone()
            duplicate = existing is not None
            if existing is None:
                target_id = "wol_" + secrets.token_hex(8)
                conn.execute(
                    """INSERT INTO wake_targets
                       (id, name, mac, ip, broadcast, port, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        target_id,
                        name,
                        mac,
                        device["ip"] or "",
                        broadcast,
                        payload.port,
                        user["username"],
                        utcnow_iso(),
                    ),
                )
                database.audit(
                    actor=user["username"],
                    action="wol.target_created",
                    target=target_id,
                    detail={"name": name, "mac": mac, "source": "discovery"},
                    ip=client_ip(request, config.trust_proxy),
                    conn=conn,
                )
            else:
                target_id = str(existing["id"])
            database.update_discovered_device(
                device["id"],
                state="promoted",
                wake_target_id=target_id,
                conn=conn,
            )
            database.record_discovery_event(
                device["id"],
                "promoted",
                {"kind": "wake_target", "wake_target_id": target_id},
                conn=conn,
            )
            database.audit(
                actor=user["username"],
                action="discovery.device_promoted",
                target=device["id"],
                detail={
                    "kind": "wake_target",
                    "wake_target_id": target_id,
                    "duplicate": duplicate,
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            if device["agent_id"]:
                database.record_agent_event(
                    str(device["agent_id"]),
                    "discovery",
                    "info",
                    f"Discovered device {device['hostname'] or device['ip']} promoted to a wake target",
                    {"device_id": device["id"], "wake_target_id": target_id},
                    conn=conn,
                )
            target = conn.execute(
                "SELECT * FROM wake_targets WHERE id = ?", (target_id,)
            ).fetchone()
        return {
            "device": discovered_device_to_dict(
                _discovery_device_or_404(device["id"], database)
            ),
            "wake_target": wake_target_to_dict(target),
            "duplicate": duplicate,
        }

    def _promote_link_agent_row(
        device: sqlite3.Row,
        agent_id: str,
        user: dict[str, Any],
        request: Request,
        database: Database,
        config: Settings,
    ) -> dict[str, Any]:
        with database.connection() as conn:
            agent_row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        if agent_row is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        device_mac = normalize_mac(device["mac"])
        agent_mac = normalize_mac(agent_row["mac"])
        if not device_mac or not agent_mac:
            raise HTTPException(
                status_code=409,
                detail="Both the discovered device and the enrolled agent need a MAC address to link.",
            )
        if device_mac != agent_mac:
            raise HTTPException(
                status_code=409,
                detail="The discovered MAC does not match the enrolled agent MAC.",
            )
        if device["agent_id"] and str(device["agent_id"]) != str(agent_id):
            raise HTTPException(
                status_code=409,
                detail="This discovered device is already linked to another agent.",
            )
        with database.transaction() as conn:
            database.update_discovered_device(
                device["id"], state="promoted", agent_id=agent_id, conn=conn
            )
            database.record_discovery_event(
                device["id"],
                "promoted",
                {"kind": "existing_agent", "agent_id": agent_id},
                conn=conn,
            )
            database.audit(
                actor=user["username"],
                action="discovery.device_promoted",
                target=device["id"],
                detail={"kind": "existing_agent", "agent_id": agent_id},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            database.record_agent_event(
                agent_id,
                "discovery",
                "info",
                f"Discovered device {device['hostname'] or device['ip']} linked to this agent",
                {"device_id": device["id"]},
                conn=conn,
            )
        return {
            "device": discovered_device_to_dict(
                _discovery_device_or_404(device["id"], database)
            ),
            "agent": agent_to_dict(agent_row, config),
        }

    def _promote_install_row(
        device: sqlite3.Row,
        os_name: str,
        payload: DiscoveryPromoteIn,
        user: dict[str, Any],
        request: Request,
        database: Database,
        config: Settings,
    ) -> dict[str, Any]:
        token = new_token("sextant_enroll_")
        token_id = "enr_" + secrets.token_hex(8)
        now = utcnow()
        minutes = payload.expires_minutes or config.enrollment_ttl_minutes
        expires = now + timedelta(minutes=max(5, min(1440, int(minutes))))
        expires_at = expires.isoformat(timespec="seconds").replace("+00:00", "Z")
        name = (payload.name or device["hostname"] or device["ip"] or "discovered device")[:255]
        address = device["ip"] or ""
        base = config.public_url or str(request.base_url).rstrip("/")
        install_command = _discovery_install_command(base, token, os_name, name, address)
        with database.transaction() as conn:
            conn.execute(
                """INSERT INTO enrollment_tokens
                   (id, token_hash, label, created_by, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    token_id,
                    hash_token(token),
                    f"discovery:{device['id']}",
                    user["username"],
                    now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    expires_at,
                ),
            )
            database.audit(
                actor=user["username"],
                action="enrollment.created",
                target=token_id,
                detail={
                    "label": f"discovery:{device['id']}",
                    "purpose": f"discovery_promote_{os_name}",
                    "expires_at": expires_at,
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            database.update_discovered_device(
                device["id"], state="promoted", conn=conn
            )
            database.record_discovery_event(
                device["id"],
                "promoted",
                {
                    "kind": f"{os_name}_agent",
                    "enrollment_id": token_id,
                    "expires_at": expires_at,
                },
                conn=conn,
            )
            database.audit(
                actor=user["username"],
                action="discovery.device_promoted",
                target=device["id"],
                detail={
                    "kind": f"{os_name}_agent",
                    "enrollment_id": token_id,
                    "expires_at": expires_at,
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            if device["agent_id"]:
                database.record_agent_event(
                    str(device["agent_id"]),
                    "discovery",
                    "info",
                    f"Onboarding started for {device['hostname'] or device['ip']}",
                    {"device_id": device["id"], "os": os_name},
                    conn=conn,
                )
        prerequisites = (
            [
                "Run in an elevated PowerShell on the target host.",
                "Python 3.11+ and the Sextant agent package must already be installed.",
                "The host must reach the console over HTTPS.",
                "Manual Windows install only; remote Windows install is not implemented.",
                f"Token expires at {expires_at}.",
            ]
            if os_name == "windows"
            else [
                "Run as root on the target host.",
                "Install the Sextant agent package and service first.",
                "The host must reach the console over HTTPS.",
                f"Token expires at {expires_at}.",
            ]
        )
        return {
            "device": discovered_device_to_dict(
                _discovery_device_or_404(device["id"], database)
            ),
            "enrollment": {
                "id": token_id,
                "token": token,
                "label": f"discovery:{device['id']}",
                "os": os_name,
                "name": name,
                "address": address,
                "expires_at": expires_at,
                "install_command": install_command,
                "prerequisites": prerequisites,
                "windows_plan": (
                    "https://github.com/JugglerMaster/Sextant/blob/main/docs/WINDOWS_REMOTE_INSTALL.md"
                    if os_name == "windows"
                    else ""
                ),
            },
        }

    def _integration_defaults(kind: str) -> dict[str, Any]:
        if kind == "snmp":
            return {
                "targets": [],
                "use_discovered": True,
                "timeout_ms": 600,
                "retries": 1,
                "max_targets": 256,
                "verify_tls": True,
            }
        return {
            "base_url": "",
            "verify_tls": True,
            "timeout_ms": 5000,
            "max_response_bytes": 1_000_000,
            **({"site": "default"} if kind == "unifi" else {}),
            **({"auth_method": "token"} if kind == "proxmox" else {}),
            **({"fixture_only": True} if kind == "esxi" else {}),
        }

    def _integration_row(kind: str) -> sqlite3.Row | None:
        return app.state.db.get_discovery_integration(kind)

    def _integration_config(kind: str, row: sqlite3.Row | None = None) -> dict[str, Any]:
        defaults = _integration_defaults(kind)
        if row is None:
            row = _integration_row(kind)
        if row is not None:
            stored = loads(row["config"], {})
            if isinstance(stored, dict):
                defaults.update(stored)
        return defaults

    def _integration_descriptor(kind: str, row: sqlite3.Row | None = None) -> dict[str, Any]:
        row = row if row is not None else _integration_row(kind)
        if kind == "snmp":
            label = "SNMP v2c"
            live_supported = True
            verified = False
        else:
            meta = adapter_descriptor(kind)
            label = meta["label"]
            live_supported = meta["live_supported"]
            verified = meta["verified"]
        fields = list(secret_fields(kind))
        return {
            "kind": kind,
            "label": label,
            "enabled": bool(row["enabled"]) if row is not None else False,
            "config": _integration_config(kind, row),
            "secret_fields": fields,
            "secret_configured": integration_secrets.configured(kind, tuple(fields)),
            "live_supported": live_supported,
            "verified": verified,
            "fixture_only": kind == "esxi",
            "last_error": row["last_error"] if row is not None else "",
            "last_checked_at": row["last_checked_at"] if row is not None else None,
            "updated_at": row["updated_at"] if row is not None else None,
        }

    def _redact_integration_error(kind: str, message: Any) -> str:
        text = redact_snmp_error(message) if kind == "snmp" else str(message)[:2000]
        for secret in integration_secrets.get(kind).values():
            if secret:
                text = text.replace(secret, "***")
        return text

    def _integration_error(kind: str, message: str) -> str:
        text = _redact_integration_error(kind, message)
        app.state.db.set_discovery_integration_result(kind, last_error=text)
        return text

    def _observations_from_devices(
        devices: list[dict[str, Any]],
        *,
        source: str,
    ) -> list[DeviceObservation]:
        try:
            allowlist = validate_allowlist(app.state.settings.discovery.cidrs)
        except DiscoveryError:
            allowlist = []
        observations: list[DeviceObservation] = []
        for device in devices[:MAX_IMPORT_DEVICES]:
            ip = str(device.get("ip") or "")
            if not ip:
                continue
            observations.append(
                DeviceObservation(
                    ip=ip,
                    mac=str(device.get("mac") or ""),
                    hostname=str(device.get("hostname") or ""),
                    vendor=str(device.get("vendor") or ""),
                    source=source,
                    segment=segment_for_ip(ip, allowlist),
                    open_ports=list(device.get("open_ports") or []),
                    services=device.get("services") if isinstance(device.get("services"), dict) else {},
                    mdns=device.get("mdns") if isinstance(device.get("mdns"), dict) else {},
                    ssdp=device.get("ssdp") if isinstance(device.get("ssdp"), dict) else {},
                    evidence=device.get("evidence") if isinstance(device.get("evidence"), dict) else {},
                )
            )
        return observations

    def _preview_counts(observations: list[DeviceObservation]) -> dict[str, int]:
        with app.state.db.connection() as conn:
            rows = conn.execute(
                "SELECT id, mac, ip, hostname FROM discovered_devices"
            ).fetchall()
        by_mac = {str(row["mac"]).lower(): row for row in rows if row["mac"]}
        by_ip = {str(row["ip"]): row for row in rows if row["ip"]}
        by_hostname = {
            str(row["hostname"]).lower(): row for row in rows if row["hostname"]
        }
        new = 0
        existing = 0
        seen: set[str] = set()
        for observation in observations:
            mac = str(observation.mac or "").lower()
            match = by_mac.get(mac) if mac else None
            if match is None and observation.ip:
                match = by_ip.get(str(observation.ip))
            if match is None and observation.hostname:
                match = by_hostname.get(str(observation.hostname).lower())
            key = str(match["id"]) if match is not None else f"new:{observation.ip}:{mac}"
            if key in seen:
                continue
            seen.add(key)
            if match is None:
                new += 1
            else:
                existing += 1
        return {"new": new, "existing": existing}

    def _ingest_discovery_observations(
        observations: list[DeviceObservation],
        *,
        source: str,
        methods: list[str],
        user: dict[str, Any],
        request: Request,
        audit_action: str,
        audit_detail: dict[str, Any],
        failure_action: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        segments = sorted({item.segment for item in observations if item.segment})
        scan = app.state.db.create_discovery_scan(
            segments, methods, user["username"], source=source
        )
        try:
            merged = merge_observations(
                app.state.db, observations, scan_id=scan["id"]
            )
        except Exception as exc:  # noqa: BLE001 - surface the failure safely
            app.state.db.update_discovery_scan(
                scan["id"],
                status="failed",
                finished_at=utcnow_iso(),
                error=f"Ingest failed: {type(exc).__name__}",
            )
            app.state.db.audit(
                actor=user["username"],
                action=failure_action or f"{audit_action}_failed",
                target=str(scan["id"]),
                detail={**audit_detail, "error": type(exc).__name__},
                ip=client_ip(request, app.state.settings.trust_proxy),
            )
            raise HTTPException(
                status_code=400, detail="Discovery data could not be merged."
            ) from None
        app.state.db.update_discovery_scan(
            scan["id"],
            status="succeeded",
            finished_at=utcnow_iso(),
            hosts_found=len({item["id"] for item in merged}),
            hosts_scanned=len(observations),
            progress=100,
            error="",
        )
        app.state.db.audit(
            actor=user["username"],
            action=audit_action,
            target=str(scan["id"]),
            detail={
                **audit_detail,
                "devices": len(merged),
                "hosts_scanned": len(observations),
                "source": source,
            },
            ip=client_ip(request, app.state.settings.trust_proxy),
        )
        notify_dashboard()
        updated = app.state.db.get_discovery_scan(scan["id"])
        return (
            discovery_scan_to_dict(updated) if updated is not None else {},
            merged,
        )

    def discovery_settings_payload(
        database: Database, config: Settings
    ) -> dict[str, Any]:
        allowed: list[str] = []
        cidr_errors: list[str] = []
        for value in config.discovery.cidrs:
            try:
                allowed.append(str(validate_network(value)))
            except DiscoveryError as exc:
                cidr_errors.append(str(exc))
        acknowledgement = database.get_discovery_settings()
        acknowledged = bool(
            acknowledgement is not None and acknowledgement["acknowledged_at"]
        )
        reasons: list[str] = []
        if not config.discovery.enabled:
            reasons.append("SEXTANT_DISCOVERY_ENABLED is 0.")
        if not acknowledged:
            reasons.append("The authorization acknowledgement has not been stored.")
        if not allowed:
            reasons.append("SEXTANT_DISCOVERY_CIDRS has no valid private ranges.")
        return {
            "enabled": config.discovery.enabled,
            "acknowledged": acknowledged,
            "acknowledged_at": acknowledgement["acknowledged_at"]
            if acknowledgement is not None
            else None,
            "acknowledged_by": acknowledgement["acknowledged_by"]
            if acknowledgement is not None
            else "",
            "acknowledgement_text": ACKNOWLEDGEMENT,
            "cidrs": allowed,
            "cidr_errors": cidr_errors,
            "exclude": list(config.discovery.exclude),
            "methods": list(config.discovery.methods),
            "probe_ports": list(config.discovery.probe_ports),
            "active_probes": config.discovery.active_probes,
            "interval_hours": config.discovery.interval_hours,
            "max_hosts": config.discovery.max_hosts,
            "concurrency": config.discovery.concurrency,
            "timeout_ms": config.discovery.timeout_ms,
            "retention_days": config.discovery.retention_days,
            "agent_relay": config.discovery.agent_relay,
            "export_limit": config.discovery.export_limit,
            "disabled_reasons": reasons,
        }

    @app.get("/api/discovery/settings")
    def get_discovery_settings(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        return discovery_settings_payload(database, config)

    @app.patch("/api/discovery/settings")
    def update_discovery_settings(
        payload: DiscoverySettingsIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if payload.acknowledged:
            if payload.acknowledgement.strip() != ACKNOWLEDGEMENT:
                raise HTTPException(
                    status_code=400,
                    detail=f'Acknowledgement text must be exactly: "{ACKNOWLEDGEMENT}"',
                )
            database.set_discovery_acknowledgement(
                user["username"], ACKNOWLEDGEMENT
            )
            action = "discovery.acknowledged"
        else:
            database.clear_discovery_acknowledgement()
            action = "discovery.acknowledgement_cleared"
        database.audit(
            actor=user["username"],
            action=action,
            detail={"acknowledgement": ACKNOWLEDGEMENT},
            ip=client_ip(request, config.trust_proxy),
        )
        return discovery_settings_payload(database, config)

    @app.get("/api/discovery/devices")
    def list_discovery_devices(
        state: str = "",
        segment: str = "",
        q: str = "",
        type: str = "",
        vendor: str = "",
        no_agent: bool = False,
        admin_ports: bool = False,
        exposure: bool = False,
        conflict: bool = False,
        mac: str = "",
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        normalized_state = state.strip().lower()
        if normalized_state and normalized_state not in {
            "new",
            "known",
            "ignored",
            "promoted",
            "changed",
            "conflict",
        }:
            raise HTTPException(status_code=400, detail="Invalid device state.")
        normalized_segment = segment.strip()
        if normalized_segment == "unsegmented":
            normalized_segment = ""
        rows, total = database.list_discovered_devices(
            state=normalized_state or None,
            segment=normalized_segment or None,
            query=q.strip() or None,
            device_type=type.strip() or None,
            vendor=vendor.strip() or None,
            no_agent=no_agent,
            admin_ports=admin_ports,
            exposure=exposure,
            conflict=conflict,
            mac=mac.strip() or None,
            limit=limit,
            offset=offset,
        )
        summary = database.discovery_summary()
        conflict_ids = set(summary.get("conflict_device_ids") or [])
        devices = []
        for row in rows:
            device = discovered_device_to_dict(row)
            device["identity_conflict"] = device["id"] in conflict_ids
            device["conflict"] = bool(device["conflict"]) or device["identity_conflict"]
            devices.append(device)
        return {
            "devices": devices,
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": summary,
        }

    @app.get("/api/discovery/devices/{device_id}")
    def get_discovery_device(
        device_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        row = database.get_discovered_device(device_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Discovered device not found.")
        device = discovered_device_to_dict(row)
        events = [
            discovery_event_to_dict(event)
            for event in database.discovery_device_events(device_id)
        ]
        agent = None
        if device["agent_id"]:
            with database.connection() as conn:
                agent_row = conn.execute(
                    "SELECT * FROM agents WHERE id = ?", (device["agent_id"],)
                ).fetchone()
            if agent_row is not None:
                agent = agent_to_dict(agent_row, config)
        wake_target = None
        if device["wake_target_id"]:
            target_row = database.get_wake_target(device["wake_target_id"])
            if target_row is not None:
                wake_target = wake_target_to_dict(target_row)
        conflicts = detect_identity_conflicts(database, device_id)
        device["identity_conflict"] = bool(conflicts)
        device["conflict"] = bool(device["conflict"]) or bool(conflicts)
        candidates: list[dict[str, Any]] = []
        if device["mac"] and not device["agent_id"]:
            with database.connection() as conn:
                candidate_rows = conn.execute(
                    "SELECT * FROM agents WHERE mac = ? COLLATE NOCASE LIMIT 10",
                    (device["mac"],),
                ).fetchall()
            candidates = [agent_to_dict(item, config) for item in candidate_rows]
        return {
            "device": device,
            "events": events,
            "agent": agent,
            "wake_target": wake_target,
            "conflicts": conflicts,
            "agent_candidates": candidates,
        }

    @app.patch("/api/discovery/devices/{device_id}")
    def update_discovery_device(
        device_id: str,
        payload: DiscoveryDevicePatchIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        existing = database.get_discovered_device(device_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Discovered device not found.")
        if payload.tags is None and payload.notes is None and payload.state is None:
            raise HTTPException(status_code=400, detail="No device changes were supplied.")
        row = database.update_discovered_device(
            device_id,
            tags=payload.tags,
            notes=payload.notes,
            state=payload.state,
        )
        database.audit(
            actor=user["username"],
            action="discovery.device_updated",
            target=device_id,
            detail={
                "from_state": existing["state"],
                "to_state": row["state"] if row is not None else existing["state"],
                "tags": payload.tags is not None,
                "notes": payload.notes is not None,
            },
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {
            "device": discovered_device_to_dict(row) if row is not None else None,
            "events": [
                discovery_event_to_dict(event)
                for event in database.discovery_device_events(device_id)
            ],
        }

    @app.delete("/api/discovery/devices/{device_id}")
    def delete_discovery_device(
        device_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        existing = database.get_discovered_device(device_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Discovered device not found.")
        database.delete_discovered_device(device_id)
        database.audit(
            actor=user["username"],
            action="discovery.device_deleted",
            target=device_id,
            detail={
                "ip": existing["ip"],
                "mac": existing["mac"],
                "hostname": existing["hostname"],
            },
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return Response(status_code=204)

    @app.get("/api/discovery/conflicts")
    def list_discovery_conflicts(
        device_id: str = "",
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        conflicts = detect_identity_conflicts(database, device_id.strip() or None)
        return {
            "conflicts": conflicts,
            "conflict_device_ids": sorted(set(database.discovery_conflict_ids())),
            "total": len(conflicts),
        }

    @app.get("/api/discovery/export")
    def export_discovery_devices(
        request: Request,
        format: str = "csv",
        state: str = "",
        segment: str = "",
        q: str = "",
        type: str = "",
        vendor: str = "",
        no_agent: bool = False,
        admin_ports: bool = False,
        exposure: bool = False,
        conflict: bool = False,
        mac: str = "",
        limit: int = Query(default=0, ge=0, le=100000),
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        export_format = str(format or "csv").strip().lower()
        if export_format not in {"csv", "json"}:
            raise HTTPException(status_code=400, detail="format must be csv or json.")
        normalized_state = state.strip().lower()
        if normalized_state and normalized_state not in {
            "new",
            "known",
            "ignored",
            "promoted",
            "changed",
            "conflict",
        }:
            raise HTTPException(status_code=400, detail="Invalid device state.")
        normalized_segment = segment.strip()
        if normalized_segment == "unsegmented":
            normalized_segment = ""
        cap = max(1, min(100_000, int(config.discovery.export_limit)))
        take = cap if not limit else min(cap, max(1, int(limit)))
        rows, total = database.list_discovered_devices(
            state=normalized_state or None,
            segment=normalized_segment or None,
            query=q.strip() or None,
            device_type=type.strip() or None,
            vendor=vendor.strip() or None,
            no_agent=no_agent,
            admin_ports=admin_ports,
            exposure=exposure,
            conflict=conflict,
            mac=mac.strip() or None,
            limit=take,
            offset=0,
        )
        conflict_ids = database.discovery_conflict_ids()
        devices = []
        for row in rows:
            device = discovered_device_to_dict(row)
            device["identity_conflict"] = device["id"] in conflict_ids
            device["conflict"] = bool(device["conflict"]) or device["identity_conflict"]
            devices.append(device)
        truncated = total > len(devices)
        database.audit(
            actor=user["username"],
            action="discovery.exported",
            detail={
                "format": export_format,
                "rows": len(devices),
                "total": total,
                "truncated": truncated,
                "cap": cap,
                "filters": {
                    "state": normalized_state,
                    "segment": normalized_segment,
                    "q": q.strip()[:255],
                    "type": type.strip(),
                    "vendor": vendor.strip(),
                    "no_agent": no_agent,
                    "admin_ports": admin_ports,
                    "exposure": exposure,
                    "conflict": conflict,
                    "mac": mac.strip(),
                },
            },
            ip=client_ip(request, config.trust_proxy),
        )
        headers = {
            "Content-Disposition": (
                f'attachment; filename="sextant-discovery.{export_format}"'
            ),
            "X-Discovery-Truncated": "true" if truncated else "false",
            "X-Discovery-Rows": str(len(devices)),
        }
        if export_format == "json":
            return Response(
                dumps(
                    {
                        "generated_at": utcnow_iso(),
                        "total": total,
                        "returned": len(devices),
                        "truncated": truncated,
                        "devices": devices,
                    }
                ),
                media_type="application/json",
                headers=headers,
            )
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "state",
                "ip",
                "mac",
                "vendor",
                "hostname",
                "device_type",
                "os_guess",
                "confidence",
                "segment",
                "open_ports",
                "services",
                "agent_id",
                "wake_target_id",
                "wake_status",
                "wake_verified_at",
                "tags",
                "notes",
                "first_seen",
                "last_seen",
                "mac_randomized",
                "identity_conflict",
            ]
        )
        for device in devices:
            writer.writerow(
                [
                    device["id"],
                    device["state"],
                    device["ip"],
                    device["mac"],
                    device["vendor"],
                    device["hostname"],
                    device["device_type"],
                    device["os_guess"],
                    device["confidence"],
                    device["segment"],
                    dumps(device["open_ports"]),
                    dumps(device["services"]),
                    device["agent_id"] or "",
                    device["wake_target_id"] or "",
                    device["wake_status"],
                    device["wake_verified_at"] or "",
                    dumps(device["tags"]),
                    device["notes"],
                    device["first_seen"],
                    device["last_seen"],
                    "1" if device["mac_randomized"] else "0",
                    "1" if device["identity_conflict"] else "0",
                ]
            )
        return Response(output.getvalue(), media_type="text/csv", headers=headers)

    @app.post("/api/discovery/devices/{device_id}/wake-target")
    def discovery_wake_target(
        device_id: str,
        payload: DiscoveryWakeTargetIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        device = _discovery_device_or_404(device_id, database)
        result = _promote_wake_target_row(
            device, payload, user, request, database, config
        )
        notify_dashboard()
        return result

    @app.post("/api/discovery/devices/{device_id}/link-agent")
    def discovery_link_agent(
        device_id: str,
        payload: DiscoveryLinkAgentIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        device = _discovery_device_or_404(device_id, database)
        result = _promote_link_agent_row(
            device, payload.agent_id, user, request, database, config
        )
        notify_dashboard()
        return result

    @app.post("/api/discovery/devices/{device_id}/promote")
    def promote_discovery_device(
        device_id: str,
        payload: DiscoveryPromoteIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        device = _discovery_device_or_404(device_id, database)
        if payload.target == "wake_target":
            result = _promote_wake_target_row(
                device, payload, user, request, database, config
            )
        elif payload.target == "existing_agent":
            if not payload.agent_id:
                raise HTTPException(status_code=400, detail="agent_id is required.")
            result = _promote_link_agent_row(
                device, payload.agent_id, user, request, database, config
            )
        else:
            os_name = "windows" if payload.target == "windows_agent" else "linux"
            result = _promote_install_row(
                device, os_name, payload, user, request, database, config
            )
        notify_dashboard()
        return result

    @app.post("/api/discovery/devices/{device_id}/verify")
    def verify_discovery_device(
        device_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.discovery.enabled:
            raise HTTPException(
                status_code=403,
                detail="Discovery is disabled. Set SEXTANT_DISCOVERY_ENABLED=1 first.",
            )
        acknowledgement = database.get_discovery_settings()
        if acknowledgement is None or not acknowledgement["acknowledged_at"]:
            raise HTTPException(
                status_code=403,
                detail="Discovery authorization acknowledgement is required.",
            )
        device = _discovery_device_or_404(device_id, database)
        if not device["ip"]:
            raise HTTPException(
                status_code=400, detail="This device has no IP address to verify."
            )
        ports = list(config.discovery.probe_ports) if config.discovery.active_probes else []
        try:
            verification = verify_reachability(
                device["ip"],
                ports,
                mac=device["mac"],
                timeout_ms=config.discovery.timeout_ms,
                concurrency=config.discovery.concurrency,
            )
        except DiscoveryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
        target = (
            database.get_wake_target(device["wake_target_id"])
            if device["wake_target_id"]
            else None
        )
        if verification["reachable"]:
            wake_status = "woke"
        elif target is not None and target["last_woken_at"]:
            wake_status = "sent"
        else:
            wake_status = "failed"
        now = utcnow_iso()
        with database.transaction() as conn:
            database.update_discovered_device(
                device_id,
                wake_status=wake_status,
                wake_verified_at=now,
                last_seen=now if wake_status == "woke" else None,
                conn=conn,
            )
            database.record_discovery_event(
                device_id,
                "wake_verified",
                {
                    "status": wake_status,
                    "method": verification["method"],
                    "open_ports": verification["open_ports"],
                },
                conn=conn,
            )
            database.audit(
                actor=user["username"],
                action="discovery.wake_verified",
                target=device_id,
                detail={
                    "status": wake_status,
                    "method": verification["method"],
                    "open_ports": verification["open_ports"],
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            if device["agent_id"]:
                database.record_agent_event(
                    str(device["agent_id"]),
                    "discovery",
                    "info",
                    f"Wake verification for {device['hostname'] or device['ip']}: {wake_status}",
                    {"device_id": device_id, "status": wake_status},
                    conn=conn,
                )
        notify_dashboard()
        updated = database.get_discovered_device(device_id)
        return {
            "device": discovered_device_to_dict(updated) if updated is not None else None,
            "verification": {**verification, "status": wake_status},
        }

    @app.post("/api/discovery/import")
    def import_discovery_data(
        payload: DiscoveryImportIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.discovery.enabled:
            raise HTTPException(
                status_code=403,
                detail="Discovery is disabled. Set SEXTANT_DISCOVERY_ENABLED=1 first.",
            )
        acknowledgement = database.get_discovery_settings()
        if acknowledgement is None or not acknowledgement["acknowledged_at"]:
            raise HTTPException(
                status_code=403,
                detail="Discovery authorization acknowledgement is required.",
            )
        filename = payload.filename.replace("\\", "/").split("/")[-1][:255]
        content_bytes = len(payload.content.encode("utf-8"))
        try:
            parsed = parse_import(payload.format, payload.content)
        except DiscoveryImportError as exc:
            database.audit(
                actor=user["username"],
                action="discovery.import_failed",
                detail={
                    "format": payload.format[:32],
                    "filename": filename,
                    "bytes": content_bytes,
                    "error": str(exc)[:500],
                },
                ip=client_ip(request, config.trust_proxy),
            )
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
        observations = _observations_from_devices(
            parsed.devices, source=f"import:{parsed.format}"
        )
        if payload.dry_run:
            counts = _preview_counts(observations)
            return {
                "dry_run": True,
                "format": parsed.format,
                "devices": len(observations),
                "new": counts["new"],
                "existing": counts["existing"],
                "skipped": parsed.skipped,
                "truncated": parsed.truncated,
                "metadata": parsed.metadata,
                "sample": [
                    {
                        "ip": item["ip"],
                        "mac": item["mac"],
                        "hostname": item["hostname"],
                        "vendor": item["vendor"],
                        "open_ports": item["open_ports"],
                    }
                    for item in parsed.devices[:10]
                ],
            }
        scan, merged = _ingest_discovery_observations(
            observations,
            source=f"import:{parsed.format}",
            methods=["import"],
            user=user,
            request=request,
            audit_action="discovery.imported",
            failure_action="discovery.import_failed",
            audit_detail={
                "format": parsed.format,
                "filename": filename,
                "bytes": content_bytes,
                "skipped": parsed.skipped,
                "truncated": parsed.truncated,
            },
        )
        return {
            "dry_run": False,
            "format": parsed.format,
            "scan": scan,
            "devices_merged": len(merged),
            "skipped": parsed.skipped,
            "truncated": parsed.truncated,
        }

    @app.get("/api/discovery/integrations")
    def list_discovery_integrations(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        secrets: IntegrationSecretStore = Depends(get_integration_secrets),
    ) -> dict[str, Any]:
        del user
        rows = {row["kind"]: row for row in database.list_discovery_integrations()}
        return {
            "integrations": [
                _integration_descriptor(kind, rows.get(kind))
                for kind in INTEGRATION_KINDS
            ],
            "snmp": snmp_status(),
        }

    @app.patch("/api/discovery/integrations/{kind}")
    def update_discovery_integration(
        kind: str,
        payload: DiscoveryIntegrationIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        secrets: IntegrationSecretStore = Depends(get_integration_secrets),
    ) -> dict[str, Any]:
        normalized_kind = str(kind).lower()
        if normalized_kind not in INTEGRATION_KINDS:
            raise HTTPException(status_code=404, detail="Unknown discovery integration.")
        row = database.get_discovery_integration(normalized_kind)
        merged_config = _integration_config(normalized_kind, row)
        if payload.config is not None:
            merged_config.update(payload.config)
        try:
            normalized_config = validate_integration_config(
                normalized_kind, merged_config
            )
        except (IntegrationError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        enabled = bool(row["enabled"]) if row is not None else False
        if payload.enabled is not None:
            enabled = bool(payload.enabled)
        if payload.clear_secrets:
            secrets.clear(normalized_kind)
        provided_secret_fields: list[str] = []
        if payload.secrets:
            allowed = set(secret_fields(normalized_kind))
            unknown = sorted(set(payload.secrets) - allowed)
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown secret field(s): {', '.join(unknown)}",
                )
            secrets.set(normalized_kind, payload.secrets)
            provided_secret_fields = sorted(payload.secrets)
        updated = database.upsert_discovery_integration(
            normalized_kind,
            enabled=enabled,
            config=normalized_config,
            actor=user["username"],
        )
        database.audit(
            actor=user["username"],
            action="discovery.integration_updated",
            target=normalized_kind,
            detail={
                "enabled": enabled,
                "config_fields": sorted(normalized_config),
                "secret_fields": provided_secret_fields,
                "cleared_secrets": bool(payload.clear_secrets),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        return _integration_descriptor(normalized_kind, updated)

    @app.post("/api/discovery/integrations/{kind}/test")
    def test_discovery_integration(
        kind: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        secrets: IntegrationSecretStore = Depends(get_integration_secrets),
    ) -> dict[str, Any]:
        normalized_kind = str(kind).lower()
        if normalized_kind not in INTEGRATION_KINDS:
            raise HTTPException(status_code=404, detail="Unknown discovery integration.")
        if not config.discovery.enabled:
            raise HTTPException(
                status_code=403, detail="Discovery is disabled. Set SEXTANT_DISCOVERY_ENABLED=1 first."
            )
        acknowledgement = database.get_discovery_settings()
        if acknowledgement is None or not acknowledgement["acknowledged_at"]:
            raise HTTPException(
                status_code=403, detail="Discovery authorization acknowledgement is required."
            )
        row = database.get_discovery_integration(normalized_kind)
        integration_config = _integration_config(normalized_kind, row)
        secret_values = secrets.get(normalized_kind)
        ok = False
        detail = ""
        try:
            if normalized_kind == "snmp":
                community = secret_values.get("community", "")
                if not community:
                    raise IntegrationError("SNMP community is not configured.")
                targets = target_ips(
                    integration_config.get("targets") or [],
                    integration_config.get("max_targets", 256),
                )
                if not targets:
                    discovered, _total = database.list_discovered_devices(
                        limit=integration_config.get("max_targets", 256)
                    )
                    targets = [str(row["ip"]) for row in discovered if row["ip"]]
                if not targets:
                    raise IntegrationError("No SNMP targets are configured or discovered.")
                result = snmp_get(
                    targets[0],
                    community,
                    timeout_ms=integration_config.get("timeout_ms", 600),
                    retries=integration_config.get("retries", 1),
                )
                name = result["values"].get("1.3.6.1.2.1.1.5.0")
                detail = f"SNMP GET succeeded for {targets[0]}"
                if name:
                    detail += f" (sysName {str(name)[:80]})"
                ok = True
            else:
                result = test_adapter(
                    normalized_kind, integration_config, secret_values
                )
                detail = result.detail or "Adapter request succeeded."
                ok = True
        except (IntegrationError, SNMPError) as exc:
            detail = _redact_integration_error(normalized_kind, exc)
            _integration_error(normalized_kind, detail)
        except Exception as exc:  # noqa: BLE001 - never leak a credential in the error
            detail = _redact_integration_error(normalized_kind, type(exc).__name__)
            _integration_error(normalized_kind, detail)
        if ok:
            database.set_discovery_integration_result(
                normalized_kind, last_error="", last_checked_at=utcnow_iso()
            )
        database.audit(
            actor=user["username"],
            action="discovery.integration_tested",
            target=normalized_kind,
            detail={"ok": ok, "detail": detail[:500]},
            ip=client_ip(request, config.trust_proxy),
        )
        if not ok:
            raise HTTPException(status_code=502, detail=detail)
        return {"ok": True, "kind": normalized_kind, "detail": detail}

    @app.post("/api/discovery/integrations/{kind}/poll")
    def poll_discovery_integration(
        kind: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        secrets: IntegrationSecretStore = Depends(get_integration_secrets),
    ) -> dict[str, Any]:
        normalized_kind = str(kind).lower()
        if normalized_kind not in INTEGRATION_KINDS:
            raise HTTPException(status_code=404, detail="Unknown discovery integration.")
        if not config.discovery.enabled:
            raise HTTPException(
                status_code=403, detail="Discovery is disabled. Set SEXTANT_DISCOVERY_ENABLED=1 first."
            )
        acknowledgement = database.get_discovery_settings()
        if acknowledgement is None or not acknowledgement["acknowledged_at"]:
            raise HTTPException(
                status_code=403, detail="Discovery authorization acknowledgement is required."
            )
        row = database.get_discovery_integration(normalized_kind)
        if row is None or not row["enabled"]:
            raise HTTPException(
                status_code=409, detail="Enable this integration before polling."
            )
        integration_config = _integration_config(normalized_kind, row)
        secret_values = secrets.get(normalized_kind)
        errors: list[str] = []
        devices: list[dict[str, Any]] = []
        adapter_detail = ""
        source = normalized_kind if normalized_kind == "snmp" else f"adapter:{normalized_kind}"
        methods = ["snmp"] if normalized_kind == "snmp" else ["adapter"]
        try:
            if normalized_kind == "snmp":
                community = secret_values.get("community", "")
                if not community:
                    raise IntegrationError("SNMP community is not configured.")
                max_targets = int(integration_config.get("max_targets", 256))
                targets = target_ips(
                    integration_config.get("targets") or [], max_targets
                )
                if integration_config.get("use_discovered", True):
                    discovered, _total = database.list_discovered_devices(
                        limit=max_targets
                    )
                    for device in discovered:
                        if device["ip"] and device["ip"] not in targets:
                            targets.append(str(device["ip"]))
                        if len(targets) >= max_targets:
                            break
                targets = list(dict.fromkeys(targets))[:max_targets]
                if not targets:
                    raise IntegrationError("No SNMP targets are configured or discovered.")
                workers = max(1, min(8, len(targets)))

                def poll_one(ip: str) -> dict[str, Any] | None:
                    try:
                        result = snmp_get(
                            ip,
                            community,
                            timeout_ms=integration_config.get("timeout_ms", 600),
                            retries=integration_config.get("retries", 1),
                        )
                        return snmp_device(ip, result["values"])
                    except SNMPError as exc:
                        errors.append(redact_snmp_error(exc))
                        return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    for device in pool.map(poll_one, targets):
                        if device is not None:
                            devices.append(device)
            else:
                result = fetch_adapter_devices(
                    normalized_kind, integration_config, secret_values
                )
                devices = result.devices
                adapter_detail = result.detail
        except (IntegrationError, SNMPError) as exc:
            detail = _redact_integration_error(normalized_kind, exc)
            _integration_error(normalized_kind, detail)
            database.audit(
                actor=user["username"],
                action="discovery.integration_poll_failed",
                target=normalized_kind,
                detail={"error": detail[:500]},
                ip=client_ip(request, config.trust_proxy),
            )
            raise HTTPException(status_code=502, detail=detail) from None
        observations = _observations_from_devices(devices, source=source)
        try:
            scan, merged = _ingest_discovery_observations(
                observations,
                source=source,
                methods=methods,
                user=user,
                request=request,
                audit_action="discovery.integration_polled",
                failure_action="discovery.integration_poll_failed",
                audit_detail={
                    "integration": normalized_kind,
                    "targets": len(devices),
                    "errors": errors[:10],
                },
            )
        except HTTPException:
            _integration_error(normalized_kind, "Poll merge failed.")
            raise
        database.set_discovery_integration_result(
            normalized_kind,
            last_error=errors[0][:2000] if errors else "",
            last_checked_at=utcnow_iso(),
        )
        return {
            "scan": scan,
            "devices_merged": len(merged),
            "targets": len(devices),
            "errors": errors[:10],
            "detail": adapter_detail,
        }

    @app.get("/api/discovery/scans")
    def list_discovery_scans(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        rows, total = database.list_discovery_scans(limit=limit, offset=offset)
        active = database.active_discovery_scan()
        return {
            "scans": [discovery_scan_to_dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "active_scan_id": active["id"] if active is not None else None,
        }

    @app.get("/api/discovery/scans/{scan_id}")
    def get_discovery_scan(
        scan_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        row = database.get_discovery_scan(scan_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Discovery scan not found.")
        return {"scan": discovery_scan_to_dict(row)}

    @app.post("/api/discovery/scan")
    def start_discovery_scan(
        payload: DiscoveryScanIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.discovery.enabled:
            raise HTTPException(
                status_code=403,
                detail="Discovery is disabled. Set SEXTANT_DISCOVERY_ENABLED=1 first.",
            )
        acknowledgement = database.get_discovery_settings()
        if acknowledgement is None or not acknowledgement["acknowledged_at"]:
            raise HTTPException(
                status_code=403,
                detail="Discovery authorization acknowledgement is required.",
            )
        try:
            allowlist = validate_allowlist(config.discovery.cidrs)
            excludes = validate_excludes(config.discovery.exclude)
            scope = parse_scope(payload.scope, allowlist)
            methods = validate_methods(config.discovery, payload.methods)
            targets = expand_targets(scope, excludes, config.discovery.max_hosts)
        except DiscoveryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
        if not targets:
            raise HTTPException(
                status_code=400, detail="The selected scope has no scan targets."
            )
        scan = database.create_discovery_scan(
            [str(network) for network in scope], methods, user["username"]
        )
        manager: DiscoveryManager = request.app.state.discovery_manager
        try:
            manager.submit(str(scan["id"]))
        except (DiscoveryBusy, DiscoveryRateLimited) as exc:
            database.update_discovery_scan(
                str(scan["id"]),
                status="failed",
                finished_at=utcnow_iso(),
                error=str(exc),
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
                headers={"Retry-After": "5"},
            ) from None
        database.audit(
            actor=user["username"],
            action="discovery.scan_started",
            target=str(scan["id"]),
            detail={
                "scope": [str(network) for network in scope],
                "methods": methods,
                "targets": len(targets),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        return {"scan": discovery_scan_to_dict(scan)}

    @app.post("/api/discovery/scans/{scan_id}/cancel")
    def cancel_discovery_scan(
        scan_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        row = database.get_discovery_scan(scan_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Discovery scan not found.")
        if row["status"] not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="The scan is already finished.")
        manager: DiscoveryManager = request.app.state.discovery_manager
        manager.cancel(scan_id)
        cancelled_now = False
        if row["agent_id"]:
            with database.transaction() as conn:
                if row["command_id"]:
                    command_row = conn.execute(
                        "SELECT * FROM commands WHERE id = ? AND agent_id = ?",
                        (row["command_id"], row["agent_id"]),
                    ).fetchone()
                    if command_row is not None and command_row["status"] == "queued":
                        conn.execute(
                            """UPDATE commands SET status = 'cancelled', completed_at = ?, error = ?
                               WHERE id = ? AND status = 'queued'""",
                            (utcnow_iso(), "Cancelled by the operator.", row["command_id"]),
                        )
                        if row["status"] == "queued":
                            database.update_discovery_scan(
                                scan_id,
                                status="cancelled",
                                finished_at=utcnow_iso(),
                                error="Cancelled before dispatch.",
                                conn=conn,
                            )
                            cancelled_now = True
        database.audit(
            actor=user["username"],
            action="discovery.scan_cancel_requested",
            target=scan_id,
            detail={
                "status": row["status"],
                "agent_id": row["agent_id"],
                "cancelled_now": cancelled_now,
            },
            ip=client_ip(request, config.trust_proxy),
        )
        if cancelled_now:
            database.audit(
                actor=user["username"],
                action="discovery.scan_cancelled",
                target=scan_id,
                detail={"source": row["source"], "agent_id": row["agent_id"]},
                ip=client_ip(request, config.trust_proxy),
            )
        updated = database.get_discovery_scan(scan_id)
        return {"scan": discovery_scan_to_dict(updated) if updated is not None else None}

    @app.get("/api/discovery/agents")
    def list_discovery_agents(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        try:
            allowlist = validate_allowlist(config.discovery.cidrs)
        except DiscoveryError:
            allowlist = []
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name COLLATE NOCASE").fetchall()
        agents: list[dict[str, Any]] = []
        now = utcnow()
        for row in rows:
            profile_row = database.get_profile_by_id(
                row["profile_id"]
            ) or database.get_profile_by_slug("workstation")
            policy = loads(row["policy"], {})
            if not isinstance(policy, dict):
                policy = {}
            agent_ip = str(row["ip"] or "")
            segment = ""
            try:
                address = ipaddress.ip_address(agent_ip)
            except ValueError:
                address = None
            if address is not None:
                containing = [network for network in allowlist if address in network]
                if containing:
                    segment = str(max(containing, key=lambda item: item.prefixlen))
            reasons: list[str] = []
            if not config.discovery.agent_relay:
                reasons.append("SEXTANT_DISCOVERY_AGENT_RELAY is 0.")
            if str(row["os"]).lower() != "linux":
                reasons.append("Agent-relayed discovery is Linux only.")
            if not _discovery_module_enabled(profile_row):
                reasons.append("The agent profile does not enable the discovery module.")
            if policy.get("allow_discovery", False) is not True:
                reasons.append("The per-agent policy does not allow discovery.")
            if address is None:
                reasons.append("The agent has not reported an IP address.")
            elif not segment:
                reasons.append("The agent IP is not inside SEXTANT_DISCOVERY_CIDRS.")
            last_seen = parse_iso(row["last_seen"])
            agents.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "hostname": row["hostname"],
                    "os": row["os"],
                    "ip": agent_ip,
                    "segment": segment,
                    "online": bool(
                        last_seen
                        and (now - last_seen).total_seconds() <= config.offline_after_seconds
                    ),
                    "eligible": not reasons,
                    "reasons": reasons,
                    "discovery_module": _discovery_module_enabled(profile_row),
                    "allow_discovery": policy.get("allow_discovery", False) is True,
                }
            )
        return {
            "relay_enabled": config.discovery.agent_relay,
            "agents": agents,
            "local_flag_required": True,
        }

    @app.post("/api/agents/{agent_id}/discover")
    def start_agent_discovery(
        agent_id: str,
        payload: DiscoveryScanIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.discovery.enabled:
            raise HTTPException(
                status_code=403,
                detail="Discovery is disabled. Set SEXTANT_DISCOVERY_ENABLED=1 first.",
            )
        if not config.discovery.agent_relay:
            raise HTTPException(
                status_code=403,
                detail="Agent-relayed discovery is disabled (SEXTANT_DISCOVERY_AGENT_RELAY=0).",
            )
        acknowledgement = database.get_discovery_settings()
        if acknowledgement is None or not acknowledgement["acknowledged_at"]:
            raise HTTPException(
                status_code=403,
                detail="Discovery authorization acknowledgement is required.",
            )
        with database.connection() as conn:
            agent_row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        if agent_row is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        profile_row = database.get_profile_by_id(
            agent_row["profile_id"]
        ) or database.get_profile_by_slug("workstation")
        if not _discovery_effective_allowed(agent_row, profile_row, config):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Agent discovery is blocked by the relay switch, platform, "
                    "agent profile, or per-agent policy."
                ),
            )
        try:
            allowlist = validate_allowlist(config.discovery.cidrs)
            excludes = validate_excludes(config.discovery.exclude)
            if payload.scope:
                scope = parse_scope(payload.scope, allowlist)
            else:
                scope = []
                try:
                    agent_address = ipaddress.ip_address(str(agent_row["ip"] or ""))
                except ValueError:
                    raise DiscoveryError(
                        "The agent has no reported IP address; choose an allowlisted scope explicitly."
                    ) from None
                containing = [network for network in allowlist if agent_address in network]
                if not containing:
                    raise DiscoveryError(
                        "The agent IP is outside the configured discovery allowlist."
                    )
                scope = [max(containing, key=lambda item: item.prefixlen)]
            if str(agent_row["ip"] or "").strip():
                try:
                    agent_address = ipaddress.ip_address(str(agent_row["ip"]))
                except ValueError:
                    agent_address = None
                if agent_address is not None and not any(
                    agent_address in network for network in scope
                ):
                    raise DiscoveryError(
                        "The selected scope does not contain the agent's reported IP address."
                    )
            methods = validate_methods(config.discovery, payload.methods)
            targets = expand_targets(scope, excludes, config.discovery.max_hosts)
            if not targets:
                raise DiscoveryError("The selected scope has no scan targets.")
        except DiscoveryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
        if database.active_agent_discovery_scan(agent_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="Another discovery scan is already active for this agent.",
            )
        scan = database.create_discovery_scan(
            [str(network) for network in scope],
            methods,
            user["username"],
            agent_id=agent_id,
            source=f"agent:{agent_id}",
        )
        args = {
            "action": "discovery.scan",
            "scan_id": scan["id"],
            "scope": [str(network) for network in scope],
            "methods": methods,
            "ports": list(config.discovery.probe_ports),
            "timeout_ms": config.discovery.timeout_ms,
            "concurrency": min(32, config.discovery.concurrency),
            "max_hosts": config.discovery.max_hosts,
            "budget_seconds": 120,
            "exclude": [str(network) for network in excludes],
        }
        try:
            command = queue_command(
                agent_id,
                "discovery",
                args,
                user,
                request,
                database,
                config,
                audit_action="discovery.scan_started",
                extra_detail={
                    "scan_id": scan["id"],
                    "source": f"agent:{agent_id}",
                    "scope": [str(network) for network in scope],
                    "methods": methods,
                    "targets": len(targets),
                },
            )
        except HTTPException:
            database.update_discovery_scan(
                scan["id"],
                status="failed",
                finished_at=utcnow_iso(),
                error="The discovery command could not be queued.",
            )
            raise
        database.update_discovery_scan(scan["id"], command_id=command["id"])
        updated = database.get_discovery_scan(scan["id"])
        return {
            "scan": discovery_scan_to_dict(updated) if updated is not None else None,
            "command": command,
        }

    @app.post("/api/agent/{agent_id}/discovery/report")
    def agent_discovery_report(
        agent_id: str,
        payload: DiscoveryReportIn,
        request: Request,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del agent
        if not config.discovery.enabled or not config.discovery.agent_relay:
            raise HTTPException(status_code=404, detail="Agent discovery is disabled.")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 1_000_000:
                    raise HTTPException(
                        status_code=413, detail="Discovery report exceeds the 1 MB limit."
                    )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length.") from None
        scan = database.get_discovery_scan(payload.scan_id)
        if scan is None or str(scan["agent_id"] or "") != agent_id:
            raise HTTPException(status_code=404, detail="Discovery scan not found for this agent.")
        if scan["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Discovery scan was cancelled.")
        if scan["status"] in {"succeeded", "failed"}:
            return {
                "scan": discovery_scan_to_dict(scan),
                "devices_merged": 0,
                "idempotent": True,
            }
        if bool(scan["cancel_requested"]):
            database.update_discovery_scan(
                scan["id"],
                status="cancelled",
                finished_at=utcnow_iso(),
                error="Cancelled before the agent report arrived.",
            )
            database.audit(
                actor=scan["initiated_by"],
                action="discovery.scan_cancelled",
                target=scan["id"],
                detail={"source": scan["source"], "agent_id": agent_id},
            )
            raise HTTPException(status_code=409, detail="Discovery scan was cancelled.")
        def fail_report(message: str, status_code: int = 400) -> None:
            database.update_discovery_scan(
                scan["id"],
                status="failed",
                finished_at=utcnow_iso(),
                error=message[:4000],
            )
            database.audit(
                actor=scan["initiated_by"],
                action="discovery.scan_finished",
                target=scan["id"],
                detail={
                    "status": "failed",
                    "source": scan["source"],
                    "agent_id": agent_id,
                    "error": message[:500],
                },
            )
            notify_dashboard()
            raise HTTPException(status_code=status_code, detail=message)

        if payload.error:
            database.update_discovery_scan(
                scan["id"],
                status="failed",
                finished_at=utcnow_iso(),
                error=payload.error[:4000],
            )
            database.audit(
                actor=scan["initiated_by"],
                action="discovery.scan_finished",
                target=scan["id"],
                detail={
                    "status": "failed",
                    "source": scan["source"],
                    "agent_id": agent_id,
                    "error": payload.error[:500],
                },
            )
            notify_dashboard()
            return {"scan": discovery_scan_to_dict(database.get_discovery_scan(scan["id"])), "devices_merged": 0}

        try:
            scope = [validate_network(value) for value in loads(scan["scope"], [])]
            excludes = validate_excludes(config.discovery.exclude)
        except DiscoveryError as exc:
            fail_report(str(exc))
        observations: list[DeviceObservation] = []
        for device in payload.devices:
            address = ipaddress.ip_address(device.ip)
            if not any(address in network for network in scope):
                fail_report(f"Reported device {device.ip} is outside the scan scope.")
            if any(address in network for network in excludes):
                fail_report(f"Reported device {device.ip} is excluded from scanning.")
            segment = segment_for_ip(device.ip, scope) or (
                payload.segment[:128] if payload.segment in {str(item) for item in scope} else ""
            )
            evidence = dict(device.evidence or {})
            evidence.update(
                {
                    "agent_id": agent_id,
                    "reported_segment": payload.segment[:128],
                    "reported_source": payload.source[:128],
                    "collected_at": payload.collected_at[:64],
                    "agent_relay": True,
                }
            )
            observations.append(
                DeviceObservation(
                    ip=device.ip,
                    mac=device.mac,
                    hostname=device.hostname,
                    vendor=device.vendor,
                    source=f"agent:{agent_id}",
                    segment=segment,
                    open_ports=device.open_ports,
                    services=device.services,
                    mdns=device.mdns,
                    ssdp=device.ssdp,
                    evidence=evidence,
                )
            )
        stamp = payload.collected_at if parse_iso(payload.collected_at) else utcnow_iso()
        try:
            merged = merge_observations(
                database, observations, scan_id=scan["id"], now=stamp
            )
        except Exception as exc:  # noqa: BLE001 - report failures must be visible
            database.update_discovery_scan(
                scan["id"],
                status="failed",
                finished_at=utcnow_iso(),
                error=f"Report merge failed: {type(exc).__name__}",
            )
            database.audit(
                actor=scan["initiated_by"],
                action="discovery.scan_finished",
                target=scan["id"],
                detail={
                    "status": "failed",
                    "source": scan["source"],
                    "agent_id": agent_id,
                    "error": type(exc).__name__,
                },
            )
            raise HTTPException(status_code=400, detail="Discovery report could not be merged.") from None
        database.update_discovery_scan(
            scan["id"],
            status="succeeded",
            finished_at=utcnow_iso(),
            hosts_found=len(merged),
            hosts_scanned=len(observations),
            progress=100,
            error="",
        )
        database.audit(
            actor=scan["initiated_by"],
            action="discovery.scan_finished",
            target=scan["id"],
            detail={
                "status": "succeeded",
                "source": scan["source"],
                "agent_id": agent_id,
                "hosts_found": len(merged),
                "truncated": bool(payload.truncated),
            },
        )
        notify_dashboard()
        updated = database.get_discovery_scan(scan["id"])
        return {
            "scan": discovery_scan_to_dict(updated) if updated is not None else None,
            "devices_merged": len(merged),
            "truncated": bool(payload.truncated),
        }

    @app.post("/api/agents/{agent_id}/rotate-token")
    def rotate_agent_token(
        agent_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        token = new_token("sextant_agent_")
        with database.connection() as conn:
            row = conn.execute("SELECT id, name FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            conn.execute("UPDATE agents SET token_hash = ? WHERE id = ?", (hash_token(token), agent_id))
            database.audit(
                actor=user["username"],
                action="agent.token_rotated",
                target=agent_id,
                detail={"agent": row["name"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        notify_dashboard()
        return {"agent_id": agent_id, "agent_token": token}

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(
        agent_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        with database.connection() as conn:
            row = conn.execute("SELECT id, name FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            database.audit(
                actor=user["username"],
                action="agent.deleted",
                target=agent_id,
                detail={"agent": row["name"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        notify_dashboard()
        return Response(status_code=204)

    # ---- Agent-facing API -------------------------------------------------

    @app.post("/api/agent/enroll")
    def enroll_agent(
        payload: AgentEnrollIn,
        request: Request,
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        now = utcnow()
        token_hash = hash_token(payload.enrollment_token)
        with database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM enrollment_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            expires = parse_iso(row["expires_at"]) if row else None
            if row is None or row["used_at"] or expires is None or expires < now:
                raise HTTPException(status_code=401, detail="Invalid or expired enrollment token.")
            machine_id = payload.machine_id.strip()[:128]
            tags = [str(tag)[:64] for tag in payload.tags[:32]]
            watch_services = [str(service)[:255] for service in payload.watch_services[:64]]
            existing = None
            if machine_id:
                existing = conn.execute(
                    "SELECT * FROM agents WHERE machine_id = ? AND machine_id != '' ORDER BY enrolled_at LIMIT 1",
                    (machine_id,),
                ).fetchone()
            reenrolled = existing is not None
            profile_row = None
            if payload.profile:
                profile_row = conn.execute(
                    "SELECT * FROM agent_profiles WHERE slug = ? COLLATE NOCASE", (payload.profile,)
                ).fetchone()
                if profile_row is None:
                    raise HTTPException(status_code=400, detail="Unknown agent profile.")
            if profile_row is None and existing is not None:
                profile_row = conn.execute(
                    "SELECT * FROM agent_profiles WHERE id = ?",
                    (existing["profile_id"] or "prof_workstation",),
                ).fetchone()
            if profile_row is None:
                profile_row = conn.execute(
                    "SELECT * FROM agent_profiles WHERE id = 'prof_workstation'"
                ).fetchone()
            profile_id = profile_row["id"] if profile_row is not None else "prof_workstation"
            tag_normalized = {str(tag).casefold() for tag in tags}
            for assignment in conn.execute(
                "SELECT tag, profile_id FROM profile_tag_assignments ORDER BY tag"
            ).fetchall():
                if str(assignment["tag"]).casefold() in tag_normalized:
                    profile_row = conn.execute(
                        "SELECT * FROM agent_profiles WHERE id = ?", (assignment["profile_id"],)
                    ).fetchone()
                    if profile_row is not None:
                        profile_id = profile_row["id"]
                    break
            profile_settings = loads(profile_row["settings"], {}) if profile_row is not None else {}
            default_policy = dumps(
                _policy_from_settings(profile_settings)
                if profile_settings
                else {
                    "allow_shell": True,
                    "allow_power": True,
                    "allow_service": True,
                    "allow_packages": False,
                }
            )
            # Per-agent policy is an additional restriction layer, not a copy of
            # the profile. Keep Docker gates open by default so enabling Docker
            # in a profile later is not silently blocked by an enrollment-time
            # snapshot. The profile still enforces its own Docker gates.
            policy_defaults = loads(default_policy, {})
            if not isinstance(policy_defaults, dict):
                policy_defaults = {}
            for docker_gate in (
                "allow_docker",
                "allow_docker_exec",
                "allow_docker_compose",
                "allow_docker_prune",
            ):
                policy_defaults[docker_gate] = True
            policy_defaults.setdefault("allow_discovery", False)
            default_policy = dumps(policy_defaults)
            agent_id = existing["id"] if existing else "agt_" + secrets.token_hex(12)
            agent_token = new_token("sextant_agent_")
            update = conn.execute(
                "UPDATE enrollment_tokens SET used_at = ?, used_by_agent = ? WHERE id = ? AND used_at IS NULL",
                (utcnow_iso(), agent_id, row["id"]),
            )
            if update.rowcount != 1:
                raise HTTPException(status_code=401, detail="Invalid or expired enrollment token.")
            if existing:
                conn.execute(
                    """UPDATE agents SET
                       name = ?, hostname = ?, machine_id = ?, os = ?, os_version = ?, arch = ?,
                       ip = ?, mac = ?, token_hash = ?, agent_version = ?, profile_id = ?, policy = ?, enrolled_at = ?, last_seen = NULL
                       WHERE id = ?""",
                    (
                        (payload.name or payload.hostname)[:255],
                        payload.hostname,
                        machine_id,
                        payload.os,
                        payload.os_version,
                        payload.arch,
                        payload.ip,
                        payload.mac,
                        hash_token(agent_token),
                        payload.agent_version,
                        profile_id,
                        default_policy,
                        utcnow_iso(),
                        agent_id,
                    ),
                )
                database.audit(
                    actor="enrollment",
                    action="agent.reenrolled",
                    target=agent_id,
                    detail={"hostname": payload.hostname, "label": row["label"]},
                    ip=client_ip(request, config.trust_proxy),
                    conn=conn,
                )
            else:
                conn.execute(
                    """INSERT INTO agents
                       (id, name, hostname, machine_id, os, os_version, arch, ip, mac, token_hash,
                        agent_version, tags, notes, watch_services, policy, profile_id, enrolled_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        agent_id,
                        (payload.name or payload.hostname)[:255],
                        payload.hostname,
                        machine_id,
                        payload.os,
                        payload.os_version,
                        payload.arch,
                        payload.ip,
                        payload.mac,
                        hash_token(agent_token),
                        payload.agent_version,
                        dumps(tags),
                        payload.notes,
                        dumps(watch_services),
                        default_policy,
                        profile_id,
                        utcnow_iso(),
                    ),
                )
                database.audit(
                    actor="enrollment",
                    action="agent.enrolled",
                    target=agent_id,
                    detail={"hostname": payload.hostname, "label": row["label"]},
                    ip=client_ip(request, config.trust_proxy),
                    conn=conn,
                )
        notify_dashboard()
        return {
            "agent_id": agent_id,
            "agent_token": agent_token,
            "reenrolled": reenrolled,
            "server_time": utcnow_iso(),
            "heartbeat_interval": int(profile_settings.get("heartbeat_seconds", 30)),
            "profile": profile_row["slug"] if profile_row is not None else "workstation",
            "version": __version__,
        }

    @app.post("/api/agent/{agent_id}/heartbeat")
    def heartbeat(
        agent_id: str,
        payload: HeartbeatIn,
        request: Request,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del request
        sweep_commands(database, config)
        now = utcnow()
        disks = []
        for disk in payload.disks:
            percent = float(disk.percent)
            if disk.total > 0:
                percent = min(100.0, max(0.0, disk.used / disk.total * 100.0))
            disks.append(
                {
                    "mount": disk.mount,
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": round(percent, 1),
                    "fs": disk.fs,
                }
            )
        gpus = [gpu.model_dump() for gpu in payload.gpus]
        services = [
            {
                "name": service.name,
                "display_name": service.display_name,
                "status": service.status,
                "startup": service.startup,
                "watched": service.watched,
            }
            for service in payload.services
        ]
        startup = [entry.model_dump() for entry in payload.startup]
        mem_pct = payload.mem_pct
        if payload.mem_total > 0:
            mem_pct = min(100.0, max(0.0, payload.mem_used / payload.mem_total * 100.0))
        disk_max_pct = max((float(disk["percent"]) for disk in disks), default=None)
        gpu_max_util = max((float(gpu["util_pct"]) for gpu in gpus), default=None)
        gpu_max_temp = max((float(gpu["temp_c"]) for gpu in gpus), default=None)
        service_down = sum(
            1
            for service in services
            if service["watched"] and str(service["status"]).lower() not in {"running", "active"}
        )
        stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        event_at = stamp
        previous_services = loads(agent["services"], [])
        watched_names = {
            str(name).strip().casefold()
            for name in loads(agent["watch_services"], [])
            if str(name).strip()
        }
        previous_packages = _package_snapshot(database.list_package_inventory(agent_id))
        previous_version = str(agent["agent_version"] or "")
        old_last_seen = parse_iso(agent["last_seen"] if "last_seen" in agent else None)
        was_online = bool(
            old_last_seen
            and (now - old_last_seen).total_seconds() <= config.offline_after_seconds
        )
        profile_row_for_docker = database.get_profile_by_id(
            agent["profile_id"]
        ) or database.get_profile_by_slug("workstation")
        docker_allowed = _docker_effective_allowed(agent, profile_row_for_docker, config)

        with database.transaction() as conn:
            conn.execute(
                """UPDATE agents SET
                   hostname = ?, machine_id = ?, os = ?, os_version = ?, arch = ?, ip = ?, mac = ?,
                   agent_version = ?, last_seen = ?, service_inventory_at = ?, cpu_pct = ?, mem_pct = ?, mem_total = ?, mem_used = ?,
                   uptime_seconds = ?, disks = ?, gpus = ?, services = ?, startup = ?, extra = ?
                   WHERE id = ?""",
                (
                    (payload.hostname or agent["hostname"])[:255],
                    (agent["machine_id"] or payload.machine_id)[:128],
                    (payload.os or agent["os"])[:64],
                    (payload.os_version or agent["os_version"])[:255],
                    (payload.arch or agent["arch"])[:64],
                    (payload.ip or agent["ip"])[:128],
                    (payload.mac or agent["mac"])[:64],
                    (payload.agent_version or agent["agent_version"])[:64],
                    stamp,
                    payload.service_inventory_collected_at or agent["service_inventory_at"],
                    round(float(payload.cpu_pct), 1),
                    round(float(mem_pct), 1),
                    payload.mem_total,
                    payload.mem_used,
                    payload.uptime_seconds,
                    dumps(disks),
                    dumps(gpus),
                    dumps(services),
                    dumps(startup),
                    bounded_json(payload.extra, 50_000),
                    agent_id,
                ),
            )
            if payload.docker is not None:
                if docker_allowed:
                    database.record_docker_summary(
                        conn,
                        agent_id,
                        payload.docker.model_dump(),
                        at=stamp,
                    )
                else:
                    conn.execute("DELETE FROM docker_hosts WHERE agent_id = ?", (agent_id,))
            if config.telemetry_history:
                database.insert_telemetry_sample(
                    conn,
                    agent_id,
                    stamp,
                    cpu_pct=round(float(payload.cpu_pct), 1),
                    mem_pct=round(float(mem_pct), 1),
                    mem_used=payload.mem_used,
                    mem_total=payload.mem_total,
                    uptime_seconds=payload.uptime_seconds,
                    disk_max_pct=disk_max_pct,
                    gpu_max_util=gpu_max_util,
                    gpu_max_temp=gpu_max_temp,
                    service_down=service_down,
                )
            inventory_state = database.get_package_inventory_state(agent_id)
            current_revision = int(inventory_state["revision"]) if inventory_state is not None else 0
            received_hash = payload.package_inventory_hash
            has_changes = bool(payload.packages) or bool(payload.package_inventory_deletes)
            request_inventory_full = ""
            new_revision = current_revision
            mode = payload.package_inventory_mode
            if mode == "delta":
                if inventory_state is None or payload.package_inventory_base_revision != current_revision:
                    request_inventory_full = "full"
                elif has_changes:
                    database.apply_package_inventory_delta(
                        conn,
                        agent_id,
                        [item.model_dump() for item in payload.packages],
                        payload.package_inventory_deletes,
                    )
                    new_revision = current_revision + 1
                    database.set_package_inventory_state(
                        conn,
                        agent_id,
                        hash_value=received_hash,
                        revision=new_revision,
                        full=False,
                    )
            else:
                if payload.package_inventory_full or (
                    has_changes and (inventory_state is None or inventory_state["hash"] != received_hash)
                ):
                    database.replace_package_inventory(
                        conn,
                        agent_id,
                        [item.model_dump() for item in payload.packages],
                        [item.model_dump() for item in payload.package_managers],
                    )
                    new_revision = current_revision + 1
                    database.set_package_inventory_state(
                        conn,
                        agent_id,
                        hash_value=received_hash,
                        revision=new_revision,
                        full=True,
                    )
                elif received_hash and inventory_state is not None and inventory_state["hash"] != received_hash and not has_changes:
                    request_inventory_full = "full"
            rows = conn.execute(
                """SELECT * FROM commands
                   WHERE agent_id = ? AND status = 'queued'
                   ORDER BY created_at ASC LIMIT 1""",
                (agent_id,),
            ).fetchall()
            dispatch: list[dict[str, Any]] = []
            stamp = utcnow_iso()
            for row in rows:
                updated = conn.execute(
                    "UPDATE commands SET status = 'dispatched', dispatched_at = ? WHERE id = ? AND status = 'queued'",
                    (stamp, row["id"]),
                )
                if updated.rowcount == 1:
                    row = conn.execute("SELECT * FROM commands WHERE id = ?", (row["id"],)).fetchone()
                    dispatch.append(
                        {
                            "id": row["id"],
                            "kind": row["kind"],
                            "args": loads(row["args"], {}),
                            "created_at": row["created_at"],
                        }
                    )
                    if row["kind"] == "docker":
                        args = loads(row["args"], {})
                        if isinstance(args, dict) and str(args.get("action", "")).startswith("compose."):
                            conn.execute(
                                """UPDATE docker_deployments
                                   SET status = 'running', started_at = COALESCE(started_at, ?)
                                   WHERE command_id = ? AND status IN ('queued', 'dispatched')""",
                                (stamp, row["id"]),
                            )
            current_agent = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            _reconcile_agent_alerts(
                database, current_agent, config, now, conn, at=event_at
            )
        current_packages = _package_snapshot(database.list_package_inventory(agent_id))
        current_version = str((payload.agent_version or agent["agent_version"] or "0"))[:64]
        state_events: list[dict[str, Any]] = []
        if old_last_seen is not None and not was_online:
            state_events.append(
                {
                    "agent_id": agent_id,
                    "at": event_at,
                    "kind": "online",
                    "severity": "success",
                    "title": "Agent back online",
                    "detail": {"last_seen": agent["last_seen"]},
                }
            )
        if previous_version and current_version and previous_version != current_version:
            state_events.append(
                {
                    "agent_id": agent_id,
                    "at": event_at,
                    "kind": "version",
                    "severity": "info",
                    "title": f"Agent version changed to {current_version}",
                    "detail": {"from": previous_version, "to": current_version},
                }
            )
        for event in _service_change_events(previous_services, services, watched_names):
            state_events.append({"agent_id": agent_id, "at": event_at, **event})
        for event in _package_change_events(previous_packages, current_packages):
            state_events.append({"agent_id": agent_id, "at": event_at, **event})
        _record_agent_events(database, state_events)
        notify_dashboard()
        if secrets.randbelow(100) == 0:
            if config.telemetry_history:
                database.prune_telemetry(config.telemetry_retention_days)
            database.prune_commands(config.command_retention_days)
            database.prune_agent_events(config.command_retention_days)
            database.prune_audit(config.audit_retention_days)
        policy = loads(agent["policy"], {})
        if not isinstance(policy, dict):
            policy = {}
        profile_row = profile_row_for_docker
        profile_settings = _profile_settings(profile_row)
        profile_actions = _policy_from_settings(profile_settings)
        profile_modules = (
            profile_settings.get("modules")
            if isinstance(profile_settings.get("modules"), dict)
            else {}
        )
        watch_services = loads(agent["watch_services"], [])
        if not isinstance(watch_services, list):
            watch_services = []
        policy = {
            "allow_shell": bool(policy.get("allow_shell", True)) and profile_actions["allow_shell"],
            "allow_power": bool(policy.get("allow_power", True)) and profile_actions["allow_power"],
            "allow_service": bool(policy.get("allow_service", True)) and profile_actions["allow_service"],
            "allow_packages": bool(policy.get("allow_packages", True)) and profile_actions["allow_packages"],
            "allow_docker": bool(policy.get("allow_docker", True)) and profile_actions["allow_docker"],
            "allow_docker_exec": bool(policy.get("allow_docker_exec", True))
            and profile_actions["allow_docker_exec"],
            "allow_docker_compose": bool(policy.get("allow_docker_compose", True))
            and profile_actions["allow_docker_compose"],
            "allow_docker_prune": bool(policy.get("allow_docker_prune", True))
            and profile_actions["allow_docker_prune"],
            "allow_discovery": bool(policy.get("allow_discovery", False))
            and bool(profile_modules.get("discovery", False)),
            "watch_services": watch_services,
        }
        request_docker_inventory = ""
        if docker_allowed and payload.docker is not None and payload.docker.available:
            docker_row = database.get_docker_host(agent_id)
            if (
                docker_row is None
                or not docker_row["inventory_at"]
                or (
                    payload.docker.inventory_hash
                    and docker_row["inventory_hash"] != payload.docker.inventory_hash
                )
            ):
                request_docker_inventory = "full"
        agent_update_info: dict[str, Any] = {"available": False, "current_version": current_version}
        release = _latest_agent_release(database)
        if release is not None:
            agent_update_info["latest_version"] = release["version"]
            if updates.parse_version(release["version"]) > updates.parse_version(current_version):
                agent_update_info = {
                    "available": True,
                    "current_version": current_version,
                    "version": release["version"],
                    "release_id": release["id"],
                    "sha256": release["sha256"],
                    "size": release["size"],
                    "filename": release["filename"],
                    "channel": release["channel"],
                    "notes_url": release["notes_url"],
                    "download_url": f"/api/agent/{agent_id}/store/releases/{release['id']}/download",
                }
        return {
            "server_time": utcnow_iso(),
            "offline_after_seconds": config.offline_after_seconds,
            "policy": policy,
            "profile": _profile_payload(profile_row),
            "request_package_inventory": request_inventory_full,
            "package_inventory_revision": new_revision,
            "request_docker_inventory": request_docker_inventory,
            "commands": dispatch,
            "agent_update": agent_update_info,
        }

    @app.post("/api/agent/{agent_id}/docker/inventory")
    def agent_docker_inventory(
        agent_id: str,
        payload: DockerInventoryIn,
        request: Request,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.docker_enabled:
            raise HTTPException(status_code=404, detail="Docker management is disabled.")
        if str(agent.get("os", "")).lower() != "linux":
            raise HTTPException(status_code=409, detail="Docker inventory is Linux-only.")
        profile_row = database.get_profile_by_id(
            agent["profile_id"]
        ) or database.get_profile_by_slug("workstation")
        if not _docker_effective_allowed(agent, profile_row, config):
            raise HTTPException(
                status_code=409,
                detail="Docker inventory is disabled by the agent profile or server policy.",
            )
        if not payload.summary.available:
            raise HTTPException(status_code=409, detail="The agent did not report a usable Docker engine.")
        if payload.hash != payload.summary.inventory_hash:
            raise HTTPException(status_code=400, detail="Docker inventory hash does not match the summary.")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > config.docker_inventory_max_bytes:
                    raise HTTPException(status_code=413, detail="Docker inventory payload is too large.")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length.")
        if len(payload.model_dump_json().encode("utf-8")) > config.docker_inventory_max_bytes:
            raise HTTPException(status_code=413, detail="Docker inventory payload is too large.")

        previous_health = {
            str(row["container_id"]): {
                "name": row["name"],
                "health": str(row["health"] or ""),
            }
            for row in database.list_docker_containers(agent_id)
        }
        summary = payload.summary.model_dump()
        with database.transaction() as conn:
            database.replace_docker_inventory(
                conn,
                agent_id,
                summary,
                [item.model_dump() for item in payload.containers],
                [item.model_dump() for item in payload.images],
                [item.model_dump() for item in payload.volumes],
                [item.model_dump() for item in payload.networks],
                collected_at=payload.collected_at,
                inventory_hash=payload.hash,
                revision=payload.revision,
            )
            database.reconcile_docker_deployment_hashes(agent_id, conn=conn)
        health_events = []
        for item in payload.containers:
            container_id = item.id
            health = str(item.health or "").lower()
            previous = previous_health.get(container_id, {})
            old_health = str(previous.get("health", "")).lower()
            if health == old_health:
                continue
            if health == "unhealthy":
                health_events.append(
                    {
                        "agent_id": agent_id,
                        "kind": "docker.health",
                        "severity": "warning",
                        "title": f"Container {item.name or container_id} became unhealthy",
                        "detail": {
                            "host": agent.get("name", ""),
                            "container_id": container_id,
                            "container_name": item.name,
                            "from": old_health,
                            "to": health,
                        },
                    }
                )
            elif old_health == "unhealthy" and health == "healthy":
                health_events.append(
                    {
                        "agent_id": agent_id,
                        "kind": "docker.health",
                        "severity": "success",
                        "title": f"Container {item.name or container_id} recovered",
                        "detail": {
                            "host": agent.get("name", ""),
                            "container_id": container_id,
                            "container_name": item.name,
                            "from": old_health,
                            "to": health,
                        },
                    }
                )
        _record_agent_events(database, health_events)
        notify_dashboard()
        return {
            "revision": payload.revision,
            "containers": len(payload.containers),
            "images": len(payload.images),
            "volumes": len(payload.volumes),
            "networks": len(payload.networks),
        }

    @app.post("/api/agent/{agent_id}/commands/{command_id}/output")
    def agent_command_output(
        agent_id: str,
        command_id: str,
        payload: CommandOutputIn,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del agent
        encoded = payload.text.encode("utf-8")
        if len(encoded) > COMMAND_OUTPUT_CHUNK_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Output chunk exceeds the {COMMAND_OUTPUT_CHUNK_BYTES}-byte limit.",
            )
        with database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM commands WHERE id = ? AND agent_id = ?",
                (command_id, agent_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Command not found.")
            if row["status"] in COMMAND_TERMINAL_STATUSES:
                raise HTTPException(status_code=409, detail="Command has already finished.")
            if row["status"] == "queued":
                raise HTTPException(status_code=409, detail="Command has not been dispatched yet.")
            result = database.add_command_output(
                command_id,
                payload.stream,
                payload.seq,
                payload.text,
                config.command_output_limit,
                conn=conn,
            )
            state = result["state"]
            if state == "conflict":
                raise HTTPException(
                    status_code=409,
                    detail="That output sequence already exists with different text.",
                )
            if state == "out_of_order":
                raise HTTPException(
                    status_code=409,
                    detail=f"Output sequence is behind the last accepted sequence ({result['last_seq']}).",
                )
            if state == "over_limit":
                raise HTTPException(
                    status_code=413,
                    detail=f"Command output exceeds the {result['limit']}-character limit.",
                )
            chunk = result["chunk"]
        return {
            "command_id": command_id,
            "stream": chunk["stream"],
            "seq": int(chunk["seq"]),
            "bytes": len(encoded),
            "idempotent": state == "duplicate",
        }

    @app.post("/api/agent/{agent_id}/commands/{command_id}/result")
    def agent_command_result(
        agent_id: str,
        command_id: str,
        payload: CommandResultIn,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        broker: ConsoleBroker = Depends(get_console_broker),
    ) -> dict[str, Any]:
        del agent
        limit = config.command_output_limit
        stdout = payload.stdout[:limit]
        stderr = payload.stderr[:limit]
        final_status = payload.status
        if payload.exit_code not in (None, 0) and final_status == "succeeded":
            final_status = "failed"
        with database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM commands WHERE id = ? AND agent_id = ?",
                (command_id, agent_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Command not found.")
            if row["status"] in {"succeeded", "failed", "cancelled"}:
                return {"command": command_to_dict(row), "idempotent": True}
            if row["status"] == "queued":
                raise HTTPException(status_code=409, detail="Command has not been dispatched yet.")
            updated = conn.execute(
                """UPDATE commands SET status = ?, completed_at = ?, exit_code = ?, stdout = ?, stderr = ?, error = ?
                   WHERE id = ? AND agent_id = ? AND status IN ('dispatched', 'running')""",
                (
                    final_status,
                    utcnow_iso(),
                    payload.exit_code,
                    stdout,
                    stderr,
                    payload.error[:4000],
                    command_id,
                    agent_id,
                ),
            )
            if updated.rowcount != 1:
                latest = conn.execute(
                    "SELECT * FROM commands WHERE id = ? AND agent_id = ?",
                    (command_id, agent_id),
                ).fetchone()
                if latest is not None:
                    return {"command": command_to_dict(latest), "idempotent": True}
                raise HTTPException(status_code=404, detail="Command not found.")
            conn.execute("UPDATE agents SET last_seen = ? WHERE id = ?", (utcnow_iso(), agent_id))
            updated = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if row["kind"] == "discovery":
                scan_row = conn.execute(
                    "SELECT * FROM discovery_scans WHERE command_id = ? LIMIT 1",
                    (command_id,),
                ).fetchone()
                if scan_row is not None and scan_row["status"] in {"queued", "running"}:
                    scan_status = final_status
                    scan_error = payload.error[:4000]
                    if final_status == "succeeded":
                        scan_status = "failed"
                        scan_error = "Agent finished without a discovery report."
                    if bool(scan_row["cancel_requested"]):
                        scan_status = "cancelled"
                        scan_error = "Cancelled by the operator."
                    database.update_discovery_scan(
                        scan_row["id"],
                        status=scan_status,
                        finished_at=utcnow_iso(),
                        error=scan_error,
                        conn=conn,
                    )
                    database.audit(
                        actor=row["created_by"],
                        action="discovery.scan_finished",
                        target=scan_row["id"],
                        detail={
                            "status": scan_status,
                            "source": scan_row["source"],
                            "agent_id": agent_id,
                            "error": scan_error[:500],
                        },
                        conn=conn,
                    )
            if row["kind"] == "docker":
                docker_args = loads(row["args"], {})
                if not isinstance(docker_args, dict):
                    docker_args = {}
                docker_action = str(docker_args.get("action", "")).lower()
                combined = f"{stdout}\n{stderr}"
                result_detail = {
                    "docker_action": docker_action,
                    "status": final_status,
                    "exit_code": payload.exit_code,
                    "reclaimed_bytes": _docker_reclaimed_bytes(combined),
                    "removed_containers": _docker_removed_containers(combined),
                }
                if docker_args.get("scope"):
                    result_detail["scope"] = docker_args["scope"]
                if docker_args.get("container_id"):
                    result_detail["target"] = docker_args["container_id"]
                elif docker_args.get("image_id"):
                    result_detail["target"] = docker_args["image_id"]
                elif docker_args.get("volume_name"):
                    result_detail["target"] = docker_args["volume_name"]
                elif docker_args.get("network_id"):
                    result_detail["target"] = docker_args["network_id"]
                if docker_args.get("deployment_id"):
                    result_detail["deployment_id"] = docker_args["deployment_id"]
                if docker_args.get("project_name"):
                    result_detail["project_name"] = docker_args["project_name"]
                database.audit(
                    actor=row["created_by"],
                    action="docker.action_result",
                    target=agent_id,
                    detail=result_detail,
                    conn=conn,
                )
                if docker_action == "console.open" and final_status != "succeeded":
                    session_id = str(docker_args.get("session_id", ""))
                    console_error = payload.error or stderr or "Console failed to open."
                    broker.finish(
                        session_id,
                        status="failed",
                        exit_code=payload.exit_code,
                        error=console_error,
                    )
                    database.close_console_session(
                        session_id,
                        status="failed",
                        exit_code=payload.exit_code,
                        error=console_error,
                    )
                    database.audit(
                        actor=row["created_by"],
                        action="docker.console_failed",
                        target=session_id,
                        detail={
                            "session_id": session_id,
                            "container_id": docker_args.get("container_id", ""),
                            "error": console_error[:1000],
                        },
                        conn=conn,
                    )
                if docker_action in DOCKER_MUTATION_ACTIONS:
                    database.record_agent_event(
                        agent_id,
                        "docker.action",
                        "success" if final_status == "succeeded" else "warning",
                        f"Docker {docker_action} {final_status}",
                        result_detail,
                        conn=conn,
                    )
        if row["kind"] == "docker":
            docker_args = loads(row["args"], {})
            if isinstance(docker_args, dict) and str(docker_args.get("action", "")).startswith("compose."):
                deployment = database.get_docker_deployment_by_command(command_id)
                if deployment is not None:
                    action = str(deployment["action"])
                    active: bool | None = None
                    if final_status == "succeeded" and action == "up":
                        database.deactivate_docker_deployments(
                            agent_id=deployment["agent_id"],
                            stack_id=deployment["stack_id"],
                            except_id=deployment["id"],
                        )
                        active = True
                    elif final_status == "succeeded" and action == "down":
                        database.deactivate_docker_deployments(
                            agent_id=deployment["agent_id"],
                            stack_id=deployment["stack_id"],
                            project_name=deployment["project_name"],
                        )
                        active = False
                    database.update_docker_deployment(
                        deployment["id"],
                        status=final_status,
                        log=f"{stdout}\n{stderr}".strip(),
                        error=payload.error,
                        finished=True,
                        active=active,
                    )
                    deployment_detail = {
                        "deployment_id": deployment["id"],
                        "stack_id": deployment["stack_id"],
                        "agent_id": deployment["agent_id"],
                        "revision_hash": deployment["revision_hash"],
                        "project_name": deployment["project_name"],
                        "action": action,
                        "status": final_status,
                    }
                    database.audit(
                        actor=row["created_by"],
                        action="docker.deployment_result",
                        target=deployment["id"],
                        detail=deployment_detail,
                    )
                    database.record_agent_event(
                        agent_id,
                        "docker.deployment",
                        "success" if final_status == "succeeded" else "warning",
                        f"Compose {action} {final_status}: {deployment['project_name']}",
                        deployment_detail,
                    )
        notify_dashboard()
        return {"command": command_to_dict(updated)}

    # ---- Package store (Tier 0) ---------------------------------------

    STORE_ARTIFACT_TYPES = {"agent-wheel", "deb", "rpm", "msi", "msix", "zip", "exe"}
    VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

    @app.get("/api/store/packages")
    def list_store_packages(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        return {"packages": [store_package_to_dict(row) for row in database.list_store_packages()]}

    @app.get("/api/store/packages/{slug}")
    def get_store_package(
        slug: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        package = database.get_store_package(slug)
        if package is None:
            raise HTTPException(status_code=404, detail="Store package not found.")
        releases = [store_release_to_dict(row) for row in database.list_store_releases(package["id"])]
        return {"package": store_package_to_dict(package), "releases": releases}

    @app.post("/api/store/packages")
    def create_store_package(
        payload: StorePackageIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.store_enabled:
            raise HTTPException(status_code=404, detail="The package store is disabled.")
        try:
            row = database.create_store_package(
                slug=payload.slug,
                name=payload.name,
                vendor=payload.vendor,
                description=payload.description,
                kind=payload.kind,
                platforms=payload.platforms,
                actor=user["username"],
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="A package with that slug already exists.")
        database.audit(
            actor=user["username"],
            action="store.package_created",
            target=row["slug"],
            detail={"name": payload.name, "kind": payload.kind},
            ip=client_ip(request, config.trust_proxy),
        )
        return store_package_to_dict(row)

    @app.put("/api/store/packages/{slug}/releases/{version}")
    async def upload_store_release(
        slug: str,
        version: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        store: ArtifactStore = Depends(get_store),
    ) -> dict[str, Any]:
        if not config.store_enabled:
            raise HTTPException(status_code=404, detail="The package store is disabled.")
        if not VERSION_RE.match(version):
            raise HTTPException(status_code=400, detail="Invalid version.")
        package = database.get_store_package(slug)
        if package is None:
            raise HTTPException(status_code=404, detail="Store package not found.")
        artifact_type = request.headers.get("x-artifact-type", "").strip().lower()
        if artifact_type not in STORE_ARTIFACT_TYPES:
            raise HTTPException(status_code=400, detail="Unsupported artifact type.")
        filename = Path(request.headers.get("x-filename", f"{slug}-{version}.bin")).name[:255]
        expected_sha = request.headers.get("x-sha256", "").strip().lower()
        channel = request.headers.get("x-channel", "stable").strip().lower() or "stable"
        if channel not in {"stable", "beta"}:
            raise HTTPException(status_code=400, detail="Channel must be stable or beta.")
        min_version = request.headers.get("x-min-version", "").strip()[:64]
        notes_url = request.headers.get("x-notes-url", "").strip()[:500]
        mandatory = request.headers.get("x-mandatory", "").strip().lower() in {"1", "true", "yes"}

        temp_path = store.new_temp_path()
        digest = hashlib.sha256()
        size = 0
        try:
            with temp_path.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > store.max_blob_bytes:
                        raise HTTPException(status_code=413, detail="Artifact exceeds the per-file limit.")
                    digest.update(chunk)
                    handle.write(chunk)
            sha256 = digest.hexdigest()
            if expected_sha and expected_sha != sha256:
                raise HTTPException(status_code=400, detail="SHA-256 does not match the uploaded bytes.")
            try:
                store.commit(temp_path, sha256, size)
            except StoreError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            manifest = {
                "schema": 1,
                "slug": slug,
                "version": version,
                "artifact_type": artifact_type,
                "filename": filename,
                "sha256": sha256,
                "size": size,
                "channel": channel,
                "min_version": min_version,
                "mandatory": mandatory,
                "notes_url": notes_url,
                "signature": None,
                "key_id": None,
            }
            try:
                row = database.create_store_release(
                    package_id=package["id"],
                    version=version,
                    artifact_type=artifact_type,
                    filename=filename,
                    size=size,
                    sha256=sha256,
                    manifest=manifest,
                    actor=user["username"],
                    channel=channel,
                    min_version=min_version,
                    mandatory=mandatory,
                    notes_url=notes_url,
                )
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=409, detail="That version already exists.")
        finally:
            temp_path.unlink(missing_ok=True)

        database.store_event(
            "published",
            package_id=package["id"],
            release_id=row["id"],
            detail={"version": version, "sha256": sha256, "size": size},
            actor=user["username"],
        )
        database.audit(
            actor=user["username"],
            action="store.release_published",
            target=row["id"],
            detail={"slug": slug, "version": version, "sha256": sha256},
            ip=client_ip(request, config.trust_proxy),
        )
        return store_release_to_dict(row)

    @app.post("/api/store/releases/{release_id}/yank")
    def yank_store_release(
        release_id: str,
        request: Request,
        reason: str = Query(default="", max_length=500),
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        release = database.get_store_release(release_id)
        if release is None:
            raise HTTPException(status_code=404, detail="Release not found.")
        database.yank_store_release(release_id, reason)
        database.store_event("yanked", package_id=release["package_id"], release_id=release_id, detail={"reason": reason}, actor=user["username"])
        database.audit(
            actor=user["username"],
            action="store.release_yanked",
            target=release_id,
            detail={"reason": reason},
            ip=client_ip(request, config.trust_proxy),
        )
        updated = database.get_store_release(release_id)
        return store_release_to_dict(updated)

    @app.get("/api/agent/{agent_id}/store/releases/{release_id}/download")
    def download_store_release(
        agent_id: str,
        release_id: str,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        store: ArtifactStore = Depends(get_store),
    ) -> FileResponse:
        release = database.get_store_release(release_id)
        if release is None or release["yanked_at"]:
            raise HTTPException(status_code=404, detail="Release not found.")
        path = store.blob_path(release["sha256"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact is missing from storage.")
        database.store_event(
            "downloaded",
            package_id=release["package_id"],
            release_id=release_id,
            agent_id=agent["id"],
            detail={"version": release["version"]},
        )
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=release["filename"],
            headers={"X-Checksum-Sha256": release["sha256"]},
        )

    @app.get("/api/store/events")
    def list_store_events(
        limit: int = 100,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        limit = max(1, min(int(limit), 500))
        with database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM store_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return {"events": [dict(row) for row in rows]}

    @app.get("/api/store/stats")
    def store_stats(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        store: ArtifactStore = Depends(get_store),
    ) -> dict[str, Any]:
        del user
        return {**database.store_stats(), "bytes": store.total_bytes(), "max_bytes": store.max_total_bytes}

    def _update_status_payload(data: dict[str, Any], config: Settings) -> dict[str, Any]:
        return updates.decorate_status(
            data,
            check_interval_hours=config.update_check_hours,
            state=selfupdate.read_state(config.update_state_path),
            restart_pending=selfupdate.restart_pending(config.update_restart_path),
        )

    def _check_updates(
        force: bool,
        *,
        user: dict[str, Any],
        request: Request,
        config: Settings,
        database: Database,
        action: str,
    ) -> tuple[dict[str, Any], bool]:
        if not config.update_feed:
            return updates.check_update(""), False
        cache = app.state.update_cache
        now = time.monotonic()
        ttl = config.update_check_hours * 3600
        if force or cache["data"] is None or now - cache["at"] > ttl:
            data = updates.check_update(config.update_feed)
            cache["data"] = data
            cache["at"] = now
            database.audit(
                user["username"],
                action,
                updates.sanitize_feed(config.update_feed),
                {
                    "latest_version": data.get("latest_version", ""),
                    "update_available": bool(data.get("update_available")),
                    "feed_state": data.get("feed_state", ""),
                    "error": updates.sanitize_error(data.get("error", "")),
                },
                ip=client_ip(request, config.trust_proxy),
            )
            return data, True
        return cache["data"], False

    @app.get("/api/update/status")
    def update_status(
        request: Request,
        force: bool = False,
        user: dict[str, Any] = Depends(require_admin),
        config: Settings = Depends(get_settings),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        data, _checked = _check_updates(
            force,
            user=user,
            request=request,
            config=config,
            database=database,
            action="update.check",
        )
        return _update_status_payload(data, config)

    @app.post("/api/update/check")
    def update_check_now(
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        config: Settings = Depends(get_settings),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        data, _checked = _check_updates(
            True,
            user=user,
            request=request,
            config=config,
            database=database,
            action="update.check_now",
        )
        return {**_update_status_payload(data, config), "checked_now": True}

    @app.post("/api/update/apply")
    def update_apply(
        payload: UpdateApplyIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        config: Settings = Depends(get_settings),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        target = str(payload.version or "").strip().lstrip("vV")

        def reject(status_code: int, detail: str) -> None:
            database.audit(
                user["username"],
                "update.apply",
                target,
                {
                    "status": "rejected",
                    "error": updates.sanitize_error(detail),
                },
                ip=client_ip(request, config.trust_proxy),
            )
            raise HTTPException(status_code=status_code, detail=detail)

        if not config.update_feed:
            reject(409, "No update feed is configured.")
        data, _checked = _check_updates(
            True,
            user=user,
            request=request,
            config=config,
            database=database,
            action="update.check",
        )
        decorated = _update_status_payload(data, config)
        if not decorated.get("update_available"):
            reject(409, "No newer release is available.")
        latest = str(decorated.get("latest_version") or "")
        if updates.parse_version(target) != updates.parse_version(latest):
            reject(
                409,
                f"Target version {target or '(empty)'} is not the latest available release ({latest}).",
            )
        if updates.parse_version(target) <= updates.parse_version(__version__):
            reject(409, "That version is already installed.")
        if not decorated.get("apply_available"):
            reject(
                409,
                str(decorated.get("apply_disabled_reason") or "The update cannot be applied."),
            )
        try:
            result = selfupdate.apply_update(
                decorated,
                version=target,
                release_root=config.update_release_path,
                current_link=config.update_current_path,
                trigger_path=config.update_restart_path,
                state_path=config.update_state_path,
                max_bytes=config.update_download_max_mb * 1024 * 1024,
            )
        except selfupdate.UpdateError as exc:
            database.audit(
                user["username"],
                "update.apply",
                target,
                {
                    "status": "failed",
                    "error": updates.sanitize_error(exc),
                },
                ip=client_ip(request, config.trust_proxy),
            )
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        database.audit(
            user["username"],
            "update.apply",
            target,
            {
                "status": result.get("status", ""),
                "previous_version": result.get("previous_version", ""),
                "sha256": result.get("sha256", ""),
                "size": result.get("size", 0),
                "restart_requested": bool(result.get("restart_requested")),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        return {**result, "update": _update_status_payload(data, config)}

    @app.post("/api/update/rollback")
    def update_rollback(
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        config: Settings = Depends(get_settings),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            result = selfupdate.rollback_update(
                release_root=config.update_release_path,
                current_link=config.update_current_path,
                trigger_path=config.update_restart_path,
                state_path=config.update_state_path,
            )
        except selfupdate.UpdateError as exc:
            database.audit(
                user["username"],
                "update.rollback",
                "",
                {"status": "failed", "error": updates.sanitize_error(exc)},
                ip=client_ip(request, config.trust_proxy),
            )
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        database.audit(
            user["username"],
            "update.rollback",
            str(result.get("version") or ""),
            {
                "status": result.get("status", ""),
                "rolled_back_from": result.get("rolled_back_from", ""),
                "restart_requested": bool(result.get("restart_requested")),
            },
            ip=client_ip(request, config.trust_proxy),
        )
        cached = app.state.update_cache.get("data")
        data = cached if isinstance(cached, dict) else updates.check_update("")
        return {**result, "update": _update_status_payload(data, config)}

    @app.get("/api/agent/{agent_id}/update")
    def agent_update(
        agent_id: str,
        current_version: str = Query(default=""),
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del agent_id
        current = current_version or str(agent.get("agent_version") or "0")
        release = _latest_agent_release(database)
        if release is None:
            return {"available": False, "current_version": current, "reason": "no agent release in the store"}
        if updates.parse_version(release["version"]) <= updates.parse_version(current):
            return {
                "available": False,
                "current_version": current,
                "latest_version": release["version"],
            }
        return {
            "available": True,
            "current_version": current,
            "version": release["version"],
            "release_id": release["id"],
            "sha256": release["sha256"],
            "size": release["size"],
            "filename": release["filename"],
            "channel": release["channel"],
            "min_version": release["min_version"],
            "notes_url": release["notes_url"],
            "download_url": f"/api/agent/{agent['id']}/store/releases/{release['id']}/download",
        }

    @app.get("/api/agents/{agent_id}/packages")
    def get_agent_packages(
        agent_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        inventory = database.list_package_inventory(agent_id)
        rules = database.list_package_rules(agent_id)
        managers = [
            {"name": row["manager"], "version": row["version"]}
            for row in inventory
            if row["package"] == "__manager__"
        ]
        packages = [
            {key: row[key] for key in ("manager", "package", "version", "arch", "source", "last_seen")}
            for row in inventory
            if row["package"] != "__manager__"
        ]
        return {
            "inventory": packages,
            "managers": managers,
            "rules": [
                {
                    "manager": row["manager"],
                    "package": row["package"],
                    "desired_state": row["desired_state"],
                    "version_spec": row["version_spec"],
                    "enabled": bool(row["enabled"]),
                }
                for row in rules
            ],
            "drift": _package_actions(packages, rules),
        }

    @app.put("/api/agents/{agent_id}/packages")
    def set_agent_packages(
        agent_id: str,
        payload: list[PackageRuleIn],
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        rules: list[dict[str, Any]] = []
        for item in payload:
            if item.desired_state == "version" and not item.version_spec:
                raise HTTPException(status_code=400, detail="version state requires version_spec.")
            rules.append(item.model_dump())
        database.replace_package_rules(agent_id, rules, user["username"])
        database.audit(
            actor=user["username"],
            action="packages.rules_updated",
            target=agent_id,
            detail={"rules": rules},
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {"rules": rules}

    @app.post("/api/agents/{agent_id}/packages/sync")
    def sync_agent_packages(
        agent_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        inventory = database.list_package_inventory(agent_id)
        packages = [
            {key: row[key] for key in ("manager", "package", "version")}
            for row in inventory
            if row["package"] != "__manager__"
        ]
        actions = _package_actions(packages, database.list_package_rules(agent_id))
        if not actions:
            return {"queued": False, "actions": [], "message": "All managed packages are in the desired state."}
        command = queue_command(
            agent_id,
            "package",
            {"actions": actions},
            user,
            request,
            database,
            config,
            audit_action="packages.sync_queued",
        )
        return {"queued": True, "command": command, "actions": actions}

    @app.get("/api/profiles")
    def list_agent_profiles(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        profiles = []
        for row in database.list_profiles():
            profiles.append(
                {
                    "id": row["id"],
                    "slug": row["slug"],
                    "name": row["name"],
                    "description": row["description"],
                    "is_system": bool(row["is_system"]),
                    "revision": int(row["revision"]),
                    "settings": _profile_settings(row),
                    "agent_count": database.count_profile_agents(row["id"]),
                }
            )
        return {"profiles": profiles}

    @app.post("/api/profiles")
    def create_agent_profile(
        payload: AgentProfileIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        settings = payload.settings.model_dump()
        try:
            row = database.create_profile(
                slug=payload.slug,
                name=payload.name,
                description=payload.description,
                settings=settings,
                actor=user["username"],
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="A profile with that slug already exists.")
        database.audit(
            actor=user["username"],
            action="profile.created",
            target=row["id"],
            detail={"slug": payload.slug, "settings": settings},
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "is_system": bool(row["is_system"]),
            "revision": int(row["revision"]),
            "settings": settings,
        }

    @app.patch("/api/profiles/{profile_id}")
    def update_agent_profile(
        profile_id: str,
        payload: AgentProfilePatchIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        existing = database.get_profile_by_id(profile_id) or database.get_profile_by_slug(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        settings = payload.settings.model_dump() if payload.settings is not None else None
        row = database.update_profile(
            existing["id"],
            name=payload.name,
            description=payload.description,
            settings=settings,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        with database.connection() as conn:
            affected = conn.execute(
                "SELECT id FROM agents WHERE profile_id = ?", (row["id"],)
            ).fetchall()
        _record_agent_events(
            database,
            [
                {
                    "agent_id": agent_row["id"],
                    "kind": "profile",
                    "severity": "info",
                    "title": f"Profile updated: {row['name']}",
                    "detail": {
                        "profile_id": row["id"],
                        "slug": row["slug"],
                        "revision": int(row["revision"]),
                        "actor": user["username"],
                    },
                }
                for agent_row in affected
            ],
        )
        database.audit(
            actor=user["username"],
            action="profile.updated",
            target=row["id"],
            detail={"slug": row["slug"], "settings": settings},
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "is_system": bool(row["is_system"]),
            "revision": int(row["revision"]),
            "settings": _profile_settings(row),
        }

    @app.delete("/api/profiles/{profile_id}")
    def delete_agent_profile(
        profile_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        existing = database.get_profile_by_id(profile_id) or database.get_profile_by_slug(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        try:
            deleted = database.delete_profile(existing["id"])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if not deleted:
            raise HTTPException(status_code=400, detail="System profiles cannot be deleted.")
        database.audit(
            actor=user["username"],
            action="profile.deleted",
            target=existing["id"],
            detail={"slug": existing["slug"]},
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {"deleted": True, "id": existing["id"]}

    @app.post("/api/profiles/{profile_id}/reset")
    def reset_agent_profile(
        profile_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        existing = database.get_profile_by_id(profile_id) or database.get_profile_by_slug(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        if not existing["is_system"]:
            raise HTTPException(status_code=400, detail="Only built-in profiles can be reset.")
        default = next((profile for profile in DEFAULT_PROFILES if profile["id"] == existing["id"]), None)
        if default is None:
            raise HTTPException(status_code=400, detail="No built-in defaults for this profile.")
        row = database.update_profile(
            existing["id"],
            name=default["name"],
            description=default["description"],
            settings=default["settings"],
        )
        with database.connection() as conn:
            affected = conn.execute(
                "SELECT id FROM agents WHERE profile_id = ?", (existing["id"],)
            ).fetchall()
        _record_agent_events(
            database,
            [
                {
                    "agent_id": agent_row["id"],
                    "kind": "profile",
                    "severity": "info",
                    "title": f"Profile reset: {default['name']}",
                    "detail": {
                        "profile_id": existing["id"],
                        "slug": existing["slug"],
                        "revision": int(row["revision"]) if row else 0,
                        "actor": user["username"],
                    },
                }
                for agent_row in affected
            ],
        )
        database.audit(
            actor=user["username"],
            action="profile.reset",
            target=existing["id"],
            detail={"slug": existing["slug"]},
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {"reset": True, "id": existing["id"], "revision": int(row["revision"]) if row else 0}

    @app.get("/api/profile-tags")
    def list_profile_tags(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        return {
            "assignments": [
                {"tag": row["tag"], "profile_id": row["profile_id"], "updated_at": row["updated_at"]}
                for row in database.list_profile_tags()
            ]
        }

    @app.put("/api/profile-tags/{tag}")
    def set_profile_tag(
        tag: str,
        payload: ProfileTagIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        tag = tag.strip()[:64]
        if not tag:
            raise HTTPException(status_code=400, detail="Tag is required.")
        profile = database.get_profile_by_slug(payload.profile) or database.get_profile_by_id(payload.profile)
        if profile is None:
            raise HTTPException(status_code=400, detail="Unknown agent profile.")
        changed = database.set_profile_tag(tag, profile["id"])
        database.audit(
            actor=user["username"],
            action="profile.tag_assigned",
            target=tag,
            detail={"profile_id": profile["id"], "agents": changed},
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {"tag": tag, "profile_id": profile["id"], "agents": changed}

    @app.delete("/api/profile-tags/{tag}")
    def delete_profile_tag(
        tag: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        deleted = database.delete_profile_tag(tag)
        database.audit(
            actor=user["username"],
            action="profile.tag_unassigned",
            target=tag,
            detail={"deleted": deleted},
            ip=client_ip(request, config.trust_proxy),
        )
        notify_dashboard()
        return {"deleted": deleted, "tag": tag}

    @app.post("/api/remote-install/test")
    def test_remote_install(
        payload: RemoteInstallIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if payload.os == "windows":
            if not config.windows_install_enabled:
                raise HTTPException(status_code=404, detail="Windows remote install is disabled.")
            if not payload.password:
                raise HTTPException(status_code=400, detail="A Windows password is required.")
            try:
                output = windows_remote.test_connection(
                    host=payload.host,
                    port=payload.port,
                    user=payload.user,
                    password=payload.password,
                    ca_file=config.windows_install_ca_file,
                    timeout=min(120, config.windows_install_timeout_seconds),
                )
            except windows_remote.WindowsRemoteError as exc:
                database.audit(
                    actor=user["username"],
                    action="remote_install.test_failed",
                    detail={"host": payload.host, "os": "windows", "error": str(exc)[:500]},
                    ip=client_ip(request, config.trust_proxy),
                )
                raise HTTPException(status_code=400, detail=str(exc)) from None
        else:
            if not config.ssh_install_enabled:
                raise HTTPException(status_code=404, detail="Remote install is disabled.")
            try:
                output = remote_install.test_connection(
                    key_path=config.ssh_key_path,
                    host=payload.host,
                    port=payload.port,
                    user=payload.user,
                    config_path=config.ssh_config_path,
                )
            except remote_install.RemoteInstallError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        database.audit(
            actor=user["username"],
            action="remote_install.tested",
            detail={"host": payload.host, "os": payload.os, "transport": payload.transport},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"ok": True, "output": output}

    @app.post("/api/remote-install")
    def create_remote_install(
        payload: RemoteInstallIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        store: ArtifactStore = Depends(get_store),
    ) -> dict[str, Any]:
        is_windows = payload.os == "windows"
        if is_windows:
            if not config.windows_install_enabled:
                raise HTTPException(status_code=404, detail="Windows remote install is disabled.")
            if not payload.password:
                raise HTTPException(status_code=400, detail="A Windows password is required.")
            if payload.transport != "winrm":
                raise HTTPException(status_code=400, detail="Windows remote install uses the WinRM transport.")
        elif not config.ssh_install_enabled:
            raise HTTPException(status_code=404, detail="Remote install is disabled.")
        profile = database.get_profile_by_slug(payload.profile) or database.get_profile_by_id(payload.profile)
        if profile is None:
            raise HTTPException(status_code=400, detail="Unknown agent profile.")
        release = _latest_agent_release(database)
        if release is None:
            raise HTTPException(status_code=400, detail="Publish an agent-wheel release under 'sextant-agent' first.")
        wheel_path = store.blob_path(release["sha256"])
        if not wheel_path.is_file():
            raise HTTPException(status_code=400, detail="The agent wheel blob is missing from the store.")
        if not is_windows:
            try:
                remote_install._key_check(config.ssh_key_path)
            except remote_install.RemoteInstallError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        if database.running_remote_install_jobs() > 0:
            raise HTTPException(status_code=409, detail="Another remote install is already running. Wait for it to finish.")
        name = (payload.name or payload.host).strip()[:255]
        job = database.create_remote_install_job(
            host=payload.host,
            port=payload.port,
            ssh_user=payload.user,
            profile=profile["slug"],
            name=name,
            watch_services=[str(item)[:255] for item in payload.watch_services if str(item).strip()][:64],
            actor=user["username"],
            os=payload.os,
            transport="winrm" if is_windows else "ssh",
        )
        token = _issue_remote_enrollment_token(database, user["username"], f"remote-install:{payload.host}")
        server_url = config.public_url or str(request.base_url).rstrip("/")
        database.audit(
            actor=user["username"],
            action="remote_install.created",
            target=job["id"],
            detail={
                "host": payload.host,
                "user": payload.user,
                "profile": profile["slug"],
                "os": payload.os,
                "transport": "winrm" if is_windows else "ssh",
            },
            ip=client_ip(request, config.trust_proxy),
        )
        threading.Thread(
            target=_run_remote_install_job,
            args=(
                config,
                job["id"],
                token,
                str(wheel_path),
                server_url,
                payload.password if is_windows else "",
            ),
            daemon=True,
        ).start()
        return _remote_job_dict(job)

    @app.get("/api/remote-install")
    def list_remote_install(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        return {"jobs": [_remote_job_dict(row) for row in database.list_remote_install_jobs()]}

    @app.get("/api/remote-install/{job_id}")
    def get_remote_install(
        job_id: str,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        job = database.get_remote_install_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Remote install job not found.")
        return _remote_job_dict(job)

    @app.get("/api/export/agents.csv")
    def export_agents_csv(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        del user
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["id", "name", "hostname", "os", "os_version", "arch", "ip", "mac", "agent_version", "online", "profile_id", "tags", "last_seen", "service_inventory_at"]
        )
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name COLLATE NOCASE").fetchall()
        for row in rows:
            agent = agent_to_dict(row, config)
            writer.writerow(
                [
                    agent["id"],
                    agent["name"],
                    agent["hostname"],
                    agent["os"],
                    agent["os_version"],
                    agent["arch"],
                    agent["ip"],
                    agent["mac"],
                    agent["agent_version"],
                    "yes" if agent["online"] else "no",
                    agent["profile_id"],
                    ",".join(agent["tags"]),
                    agent["last_seen"] or "",
                    agent["service_inventory_at"] or "",
                ]
            )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="sextant-agents.csv"'},
        )

    @app.get("/api/export/packages.csv")
    def export_packages_csv(
        agent_id: str = Query(default=""),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> Response:
        del user
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["agent_id", "agent_name", "manager", "package", "version", "arch", "source", "last_seen"])
        with database.connection() as conn:
            if agent_id:
                rows = conn.execute(
                    """SELECT p.*, a.name AS agent_name FROM package_inventory p
                       JOIN agents a ON a.id = p.agent_id
                       WHERE p.agent_id = ? AND p.package != '__manager__'
                       ORDER BY p.manager, p.package""",
                    (agent_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT p.*, a.name AS agent_name FROM package_inventory p
                       JOIN agents a ON a.id = p.agent_id
                       WHERE p.package != '__manager__'
                       ORDER BY a.name, p.manager, p.package"""
                ).fetchall()
        for row in rows:
            writer.writerow(
                [
                    row["agent_id"],
                    row["agent_name"],
                    row["manager"],
                    row["package"],
                    row["version"],
                    row["arch"],
                    row["source"],
                    row["last_seen"],
                ]
            )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="sextant-packages.csv"'},
        )

    @app.get("/api/export/commands.csv")
    def export_commands_csv(
        agent_id: str = Query(default=""),
        status: str = Query(default=""),
        q: str = Query(default=""),
        since: str = Query(default=""),
        until: str = Query(default=""),
        limit: int | None = Query(default=None),
        offset: int = Query(default=0),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> Response:
        del user
        limit, offset, status, q, since, until = _command_history_filters(
            limit, offset, status, q, since, until
        )
        rows, _total = database.command_history(
            agent_id or None,
            limit=limit,
            offset=offset,
            status=status,
            query=q,
            since=since,
            until=until,
        )
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "created_at",
                "completed_at",
                "kind",
                "status",
                "exit_code",
                "description",
                "args",
                "stdout",
                "stderr",
                "error",
                "agent_id",
                "id",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["created_at"] or "",
                    row["completed_at"] or "",
                    row["kind"],
                    row["status"],
                    "" if row["exit_code"] is None else row["exit_code"],
                    _command_csv_description(row),
                    row["args"],
                    row["stdout"],
                    row["stderr"],
                    row["error"],
                    row["agent_id"],
                    row["id"],
                ]
            )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="sextant-commands.csv"'},
        )

    return app



def _notification_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "at": row["at"],
        "event_key": row["event_key"],
        "kind": row["kind"],
        "severity": row["severity"],
        "agent_id": row["agent_id"],
        "title": row["title"],
        "body": row["body"],
        "url": row["url"],
        "status": row["status"],
        "attempts": int(row["attempts"] or 0),
        "next_attempt_at": row["next_attempt_at"],
        "sent_at": row["sent_at"],
        "error": row["error"],
    }


def _saved_view_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    filters = loads(row["filters"], {})
    if not isinstance(filters, dict):
        filters = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "filters": filters,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _normalize_saved_view_filters(filters: dict[str, Any]) -> dict[str, str]:
    if not isinstance(filters, dict):
        raise HTTPException(status_code=400, detail="filters must be a JSON object.")
    allowed = {"status", "profile", "tag", "search"}
    unknown = sorted(set(filters) - allowed)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported saved-view filter(s): {', '.join(unknown)}.",
        )
    status = str(filters.get("status") or "all")
    if status not in SAVED_VIEW_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="status must be all, online, offline, or alerts.",
        )
    normalized = {"status": status}
    for key, limit in (("profile", 64), ("tag", 64), ("search", 255)):
        value = filters.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{key} must be a string.")
        normalized[key] = value.strip()[:limit]
    return normalized


def _latest_agent_release(database: Database) -> Any:
    package = database.get_store_package("sextant-agent")
    if package is None:
        return None
    releases = [
        row
        for row in database.list_store_releases(package["id"])
        if row["artifact_type"] == "agent-wheel" and not row["yanked_at"]
    ]
    if not releases:
        return None
    return max(releases, key=lambda row: updates.parse_version(row["version"]))


def _policy_from_settings(settings: dict[str, Any]) -> dict[str, bool]:
    actions = settings.get("actions") if isinstance(settings.get("actions"), dict) else {}
    return {
        "allow_shell": bool(actions.get("allow_shell", True)),
        "allow_power": bool(actions.get("allow_power", True)),
        "allow_service": bool(actions.get("allow_service", True)),
        "allow_packages": bool(actions.get("allow_packages", True)),
        "allow_docker": bool(actions.get("allow_docker", False)),
        "allow_docker_exec": bool(actions.get("allow_docker_exec", False)),
        "allow_docker_compose": bool(actions.get("allow_docker_compose", False)),
        "allow_docker_prune": bool(actions.get("allow_docker_prune", False)),
    }


def _docker_module_enabled(profile_row: Any) -> bool:
    settings = _profile_settings(profile_row)
    modules = settings.get("modules") if isinstance(settings.get("modules"), dict) else {}
    return bool(modules.get("docker", False))


def _discovery_module_enabled(profile_row: Any) -> bool:
    settings = _profile_settings(profile_row)
    modules = settings.get("modules") if isinstance(settings.get("modules"), dict) else {}
    return bool(modules.get("discovery", False))


def _discovery_effective_allowed(agent_row: Any, profile_row: Any, config: Settings) -> bool:
    if not config.discovery.enabled or not config.discovery.agent_relay:
        return False
    os_name = str(agent_row["os"] if "os" in agent_row.keys() else agent_row.get("os", "")).lower()
    if os_name != "linux":
        return False
    if not _discovery_module_enabled(profile_row):
        return False
    policy = loads(agent_row["policy"] if "policy" in agent_row.keys() else agent_row.get("policy"), {})
    if not isinstance(policy, dict):
        policy = {}
    return bool(policy.get("allow_discovery", False))


def _docker_effective_allowed(agent_row: Any, profile_row: Any, config: Settings) -> bool:
    if not config.docker_enabled:
        return False
    if str(agent_row["os"] if "os" in agent_row.keys() else agent_row.get("os", "")).lower() != "linux":
        return False
    if not _docker_module_enabled(profile_row):
        return False
    actions = _policy_from_settings(_profile_settings(profile_row))
    policy = loads(agent_row["policy"] if "policy" in agent_row.keys() else agent_row.get("policy"), {})
    if not isinstance(policy, dict):
        policy = {}
    return bool(policy.get("allow_docker", True)) and actions["allow_docker"]


def _docker_capabilities(agent_row: Any, profile_row: Any, config: Settings) -> dict[str, bool]:
    allowed = _docker_effective_allowed(agent_row, profile_row, config)
    actions = _policy_from_settings(_profile_settings(profile_row))
    policy = loads(agent_row["policy"] if "policy" in agent_row.keys() else agent_row.get("policy"), {})
    if not isinstance(policy, dict):
        policy = {}
    return {
        "mutate": bool(allowed),
        "exec": bool(
            allowed
            and policy.get("allow_docker_exec", True)
            and actions["allow_docker_exec"]
        ),
        "prune": bool(
            allowed
            and policy.get("allow_docker_prune", True)
            and actions["allow_docker_prune"]
        ),
        "compose": bool(
            allowed
            and policy.get("allow_docker_compose", True)
            and actions["allow_docker_compose"]
        ),
    }


_DOCKER_RECLAIM_RE = re.compile(r"Total reclaimed space:\s*([0-9.]+)\s*([kmgtpe]?i?b?)", re.IGNORECASE)
_DOCKER_REMOVED_RE = re.compile(r"Removed\s+([0-9]+)\s+stopped container", re.IGNORECASE)


def _docker_reclaimed_bytes(text: str) -> int:
    match = _DOCKER_RECLAIM_RE.search(text or "")
    if match is None:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).lower()
    factor = 1
    if unit.startswith("k"):
        factor = 1024
    elif unit.startswith("m"):
        factor = 1024**2
    elif unit.startswith("g"):
        factor = 1024**3
    elif unit.startswith("t"):
        factor = 1024**4
    elif unit.startswith("p"):
        factor = 1024**5
    return max(0, int(amount * factor))


def _docker_removed_containers(text: str) -> int:
    match = _DOCKER_REMOVED_RE.search(text or "")
    return int(match.group(1)) if match else 0


def _docker_cleanup_preview(database: Database, agent_id: str) -> list[dict[str, Any]]:
    containers = database.list_docker_containers(agent_id)
    images = database.list_docker_images(agent_id)
    volumes = database.list_docker_volumes(agent_id)
    networks = database.list_docker_networks(agent_id)

    stopped = [
        row
        for row in containers
        if str(row["state"]).lower() in {"exited", "created", "dead"}
        and not str(row["compose_project"] or "").strip()
    ]
    dangling = [row for row in images if bool(row["dangling"])]
    used_images: set[str] = set()
    for row in containers:
        if row["image"]:
            used_images.add(str(row["image"]))
        if row["image_id"]:
            used_images.add(str(row["image_id"]))
    unused_images = []
    for row in images:
        if bool(row["dangling"]):
            continue
        tags = loads(row["repo_tags"], [])
        if int(row["container_count"]) > 0:
            continue
        if row["image_id"] in used_images or any(str(tag) in used_images for tag in tags):
            continue
        unused_images.append(row)
    unused_volumes = [row for row in volumes if not bool(row["in_use"])]
    unused_networks = [
        row
        for row in networks
        if int(row["container_count"]) == 0
        and str(row["name"]).lower() not in {"bridge", "host", "none"}
    ]

    def entry(
        scope: str,
        label: str,
        rows: list[Any],
        *,
        estimated_bytes: int = 0,
        estimated: bool = True,
        warning: str = "",
    ) -> dict[str, Any]:
        candidates = []
        for row in rows[:20]:
            if scope == "stopped_containers":
                candidates.append({"id": row["container_id"], "name": row["name"]})
            elif scope in {"dangling_images", "unused_images"}:
                candidates.append({"id": row["image_id"], "tags": loads(row["repo_tags"], [])})
            elif scope == "unused_volumes":
                candidates.append({"name": row["name"]})
            elif scope == "unused_networks":
                candidates.append({"id": row["network_id"], "name": row["name"]})
        return {
            "scope": scope,
            "label": label,
            "count": len(rows),
            "estimated_bytes": estimated_bytes,
            "estimated": estimated,
            "candidates": candidates,
            "warning": warning,
        }

    return [
        entry(
            "stopped_containers",
            "Stopped containers",
            stopped,
            warning="Compose-managed stopped containers are excluded by default.",
        ),
        entry(
            "dangling_images",
            "Dangling images",
            dangling,
            estimated_bytes=sum(int(row["size"]) for row in dangling),
            warning="Untagged images only.",
        ),
        entry(
            "unused_images",
            "Unused images",
            unused_images,
            estimated_bytes=sum(int(row["size"]) for row in unused_images),
            warning="Destructive: removes tagged images not used by a cached container.",
        ),
        entry(
            "unused_volumes",
            "Unused volumes",
            unused_volumes,
            estimated=False,
            warning="Destructive and irreversible. Docker only removes volumes not in use.",
        ),
        entry(
            "unused_networks",
            "Unused networks",
            unused_networks,
            warning="Default bridge/host/none networks are excluded.",
        ),
        entry(
            "build_cache",
            "Build cache",
            [],
            estimated=False,
            warning="Docker reports reclaimed size after the prune runs.",
        ),
    ]


def _profile_settings(row: Any) -> dict[str, Any]:
    settings = loads(row["settings"], {}) if row is not None else {}
    return settings if isinstance(settings, dict) else {}


def _profile_payload(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    settings = _profile_settings(row)
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "revision": int(row["revision"]),
        "is_system": bool(row["is_system"]),
        **settings,
    }


def _remote_job_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "host": row["host"],
        "port": row["port"],
        "user": row["ssh_user"],
        "os": row["os"],
        "transport": row["transport"] if "transport" in row.keys() else ("winrm" if row["os"] == "windows" else "ssh"),
        "profile": row["profile"],
        "name": row["name"],
        "status": row["status"],
        "log": row["log"],
        "error": row["error"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _issue_remote_enrollment_token(database: Database, actor: str, label: str) -> str:
    token = new_token("sextant_enroll_")
    token_id = "enr_" + secrets.token_hex(8)
    now = utcnow()
    expires = now + timedelta(minutes=30)
    with database.connection() as conn:
        conn.execute(
            """INSERT INTO enrollment_tokens
               (id, token_hash, label, created_by, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                hash_token(token),
                label,
                actor,
                now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            ),
        )
        database.audit(
            actor=actor,
            action="enrollment.created",
            target=token_id,
            detail={"label": label, "purpose": "remote_install"},
            conn=conn,
        )
    return token


def _redact_remote_text(text: Any, secrets: list[str]) -> str:
    value = str(text or "")
    for secret in secrets:
        if secret:
            value = value.replace(secret, "***")
    return value[:20_000]


def _wait_for_remote_agent_heartbeat(
    database: Database, *, host: str, name: str, timeout_seconds: int = 60
) -> bool:
    deadline = time.monotonic() + max(5, min(300, int(timeout_seconds)))
    while time.monotonic() < deadline:
        with database.connection() as conn:
            row = conn.execute(
                """SELECT last_seen FROM agents
                   WHERE last_seen IS NOT NULL
                     AND (hostname = ? COLLATE NOCASE OR name = ? COLLATE NOCASE OR ip = ?)
                   ORDER BY last_seen DESC LIMIT 1""",
                (name, name, host),
            ).fetchone()
        if row is not None and row["last_seen"]:
            return True
        time.sleep(2)
    return False


def _run_remote_install_job(
    settings: Settings,
    job_id: str,
    token: str,
    wheel_path: str,
    server_url: str,
    password: str = "",
) -> None:
    database = Database(settings.database_path)
    row = database.get_remote_install_job(job_id)
    if row is None:
        return
    secrets_to_redact = [token, password]

    def log_line(line: str) -> None:
        database.update_remote_install_job(
            job_id, log_append=_redact_remote_text(line, secrets_to_redact)
        )

    database.update_remote_install_job(
        job_id,
        status="running",
        started=True,
        log_append=_redact_remote_text(
            f"Starting {row['os']} remote install on {row['ssh_user']}@{row['host']}:{row['port']}",
            secrets_to_redact,
        ),
    )
    try:
        if row["os"] == "windows":
            windows_remote.run_install(
                host=row["host"],
                port=row["port"],
                user=row["ssh_user"],
                password=password,
                server_url=server_url,
                token=token,
                name=row["name"],
                profile=row["profile"],
                watch_services=loads(row["watch_services"], []),
                wheel_path=wheel_path,
                ca_file=settings.windows_install_ca_file,
                timeout=settings.windows_install_timeout_seconds,
                log=log_line,
            )
            if not _wait_for_remote_agent_heartbeat(
                database,
                host=row["host"],
                name=row["name"],
                timeout_seconds=min(120, settings.windows_install_timeout_seconds),
            ):
                raise windows_remote.WindowsRemoteError(
                    "Windows install finished but no authenticated agent heartbeat was observed. "
                    "Check the target's SextantAgent task and agent.log."
                )
        else:
            remote_install.run_install(
                key_path=settings.ssh_key_path,
                host=row["host"],
                port=row["port"],
                user=row["ssh_user"],
                server_url=server_url,
                token=token,
                name=row["name"],
                profile=row["profile"],
                watch_services=loads(row["watch_services"], []),
                wheel_path=wheel_path,
                log=log_line,
                config_path=settings.ssh_config_path,
            )
    except Exception as exc:  # job boundary: always record the failure
        error = _redact_remote_text(exc, secrets_to_redact)
        database.update_remote_install_job(
            job_id,
            status="failed",
            error=error[:4000],
            log_append=f"Remote install failed: {error}",
            finished=True,
        )
        database.audit("system", "remote_install.failed", job_id, {"error": error[:1000]})
        return
    database.update_remote_install_job(
        job_id,
        status="succeeded",
        log_append="Remote install succeeded.",
        finished=True,
    )
    database.audit(
        "system",
        "remote_install.completed",
        job_id,
        {"os": row["os"], "transport": row["transport"]},
    )


def _package_actions(packages: list[dict[str, Any]], rules: list[Any]) -> list[dict[str, str]]:
    installed = {(row["manager"], row["package"]): row.get("version", "") for row in packages}
    actions: list[dict[str, str]] = []
    for rule in rules:
        if not bool(rule["enabled"]):
            continue
        manager = str(rule["manager"])
        package = str(rule["package"])
        state = str(rule["desired_state"])
        version = str(rule["version_spec"] or "")
        current = installed.get((manager, package))
        action = ""
        if state == "absent":
            if current:
                action = "remove"
        elif state == "version":
            if current != version:
                action = "install"
        elif state == "latest":
            action = "upgrade" if current else "install"
        elif state == "present":
            if not current:
                action = "install"
            elif version and current != version:
                action = "install"
        if not action:
            continue
        item = {"manager": manager, "action": action, "package": package}
        if version and action == "install":
            item["version"] = version
        actions.append(item)
        if len(actions) >= 25:
            break
    return actions


def _permissions(user: dict[str, Any]) -> dict[str, bool]:
    return {
        "operate": role_at_least(user, "operator"),
        "admin": role_at_least(user, "admin"),
        "viewer": True,
    }
