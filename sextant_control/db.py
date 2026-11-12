from __future__ import annotations

import ipaddress
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
    source TEXT NOT NULL DEFAULT 'local' CHECK (source IN ('local', 'ad')),
    session_version INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    machine_id TEXT NOT NULL DEFAULT '',
    os TEXT NOT NULL,
    os_version TEXT NOT NULL DEFAULT '',
    arch TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    mac TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL,
    agent_version TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    watch_services TEXT NOT NULL DEFAULT '[]',
    policy TEXT NOT NULL DEFAULT '{"allow_shell":true,"allow_power":true,"allow_service":true,"allow_packages":false}',
    profile_id TEXT NOT NULL DEFAULT 'prof_workstation',
    enrolled_at TEXT NOT NULL,
    last_seen TEXT,
    service_inventory_at TEXT,
    cpu_pct REAL,
    mem_pct REAL,
    mem_total INTEGER,
    mem_used INTEGER,
    uptime_seconds INTEGER,
    disks TEXT NOT NULL DEFAULT '[]',
    gpus TEXT NOT NULL DEFAULT '[]',
    services TEXT NOT NULL DEFAULT '[]',
    startup TEXT NOT NULL DEFAULT '[]',
    extra TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS enrollment_tokens (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    used_by_agent TEXT
);

CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'dispatched', 'running', 'succeeded', 'failed', 'cancelled')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    dispatched_at TEXT,
    completed_at TEXT,
    exit_code INTEGER,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS command_output (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    stream TEXT NOT NULL CHECK (stream IN ('stdout', 'stderr')),
    seq INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(command_id, stream, seq)
);

CREATE TABLE IF NOT EXISTS alert_states (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    alert_key TEXT NOT NULL,
    incident_id TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT,
    last_seen_at TEXT,
    cleared_at TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT NOT NULL DEFAULT '',
    snoozed_until TEXT,
    PRIMARY KEY (agent_id, alert_key)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    ip TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS wake_targets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mac TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    broadcast TEXT NOT NULL DEFAULT '255.255.255.255',
    port INTEGER NOT NULL DEFAULT 9,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_woken_at TEXT
);

CREATE TABLE IF NOT EXISTS telemetry_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    cpu_pct REAL,
    mem_pct REAL,
    mem_used INTEGER,
    mem_total INTEGER,
    uptime_seconds INTEGER,
    disk_max_pct REAL,
    gpu_max_util REAL,
    gpu_max_temp REAL,
    service_down INTEGER
);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS notification_availability (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    offline_incident_id TEXT NOT NULL DEFAULT '',
    offline_since TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    agent_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    sent_at TEXT,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS store_packages (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    vendor TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'app' CHECK (kind IN ('app', 'agent', 'driver')),
    platforms TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS store_releases (
    id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES store_packages(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    manifest TEXT NOT NULL DEFAULT '{}',
    signature TEXT,
    key_id TEXT,
    channel TEXT NOT NULL DEFAULT 'stable',
    min_version TEXT NOT NULL DEFAULT '',
    mandatory INTEGER NOT NULL DEFAULT 0,
    notes_url TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL,
    published_by TEXT NOT NULL,
    yanked_at TEXT,
    yanked_reason TEXT NOT NULL DEFAULT '',
    UNIQUE(package_id, version)
);

CREATE TABLE IF NOT EXISTS store_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    package_id TEXT NOT NULL DEFAULT '',
    release_id TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS package_inventory (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    manager TEXT NOT NULL,
    package TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    arch TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL,
    PRIMARY KEY (agent_id, manager, package)
);

CREATE TABLE IF NOT EXISTS package_rules (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    manager TEXT NOT NULL,
    package TEXT NOT NULL,
    desired_state TEXT NOT NULL CHECK (desired_state IN ('present', 'absent', 'latest', 'version')),
    version_spec TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_id, manager, package)
);

CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    settings TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS package_inventory_state (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    hash TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    last_full_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_tag_assignments (
    tag TEXT PRIMARY KEY COLLATE NOCASE,
    profile_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_install_jobs (
    id TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    ssh_user TEXT NOT NULL,
    os TEXT NOT NULL DEFAULT 'linux',
    profile TEXT NOT NULL DEFAULT 'workstation',
    name TEXT NOT NULL DEFAULT '',
    watch_services TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    log TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS saved_views (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    filters TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS docker_hosts (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    available INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    engine_version TEXT NOT NULL DEFAULT '',
    api_version TEXT NOT NULL DEFAULT '',
    compose_version TEXT NOT NULL DEFAULT '',
    compose_available INTEGER NOT NULL DEFAULT 0,
    rootless INTEGER NOT NULL DEFAULT 0,
    storage_driver TEXT NOT NULL DEFAULT '',
    cgroup_driver TEXT NOT NULL DEFAULT '',
    docker_root_dir TEXT NOT NULL DEFAULT '',
    container_count INTEGER NOT NULL DEFAULT 0,
    running_count INTEGER NOT NULL DEFAULT 0,
    paused_count INTEGER NOT NULL DEFAULT 0,
    stopped_count INTEGER NOT NULL DEFAULT 0,
    unhealthy_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    volume_count INTEGER NOT NULL DEFAULT 0,
    network_count INTEGER NOT NULL DEFAULT 0,
    inventory_hash TEXT NOT NULL DEFAULT '',
    inventory_revision INTEGER NOT NULL DEFAULT 0,
    inventory_at TEXT,
    collected_at TEXT,
    heartbeat_at TEXT,
    last_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS docker_containers (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    container_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    image_id TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    health TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    restart_policy TEXT NOT NULL DEFAULT '',
    memory_limit INTEGER NOT NULL DEFAULT 0,
    nano_cpus INTEGER NOT NULL DEFAULT 0,
    pids_limit INTEGER,
    ports TEXT NOT NULL DEFAULT '[]',
    labels TEXT NOT NULL DEFAULT '{}',
    mounts TEXT NOT NULL DEFAULT '[]',
    networks TEXT NOT NULL DEFAULT '[]',
    compose_project TEXT NOT NULL DEFAULT '',
    compose_service TEXT NOT NULL DEFAULT '',
    compose_config_hash TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, container_id)
);

CREATE TABLE IF NOT EXISTS docker_images (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    image_id TEXT NOT NULL,
    repo_tags TEXT NOT NULL DEFAULT '[]',
    repo_digests TEXT NOT NULL DEFAULT '[]',
    size INTEGER NOT NULL DEFAULT 0,
    shared_size INTEGER NOT NULL DEFAULT 0,
    container_count INTEGER NOT NULL DEFAULT 0,
    dangling INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, image_id)
);

CREATE TABLE IF NOT EXISTS docker_volumes (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    driver TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    mountpoint TEXT NOT NULL DEFAULT '',
    labels TEXT NOT NULL DEFAULT '{}',
    in_use INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, name)
);

CREATE TABLE IF NOT EXISTS docker_networks (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    network_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    internal INTEGER NOT NULL DEFAULT 0,
    attachable INTEGER NOT NULL DEFAULT 0,
    labels TEXT NOT NULL DEFAULT '{}',
    container_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, network_id)
);

CREATE TABLE IF NOT EXISTS docker_settings (
    id TEXT PRIMARY KEY CHECK (id = 'docker'),
    compose_dir TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS docker_stacks (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    current_revision_hash TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS docker_stack_revisions (
    id TEXT PRIMARY KEY,
    stack_id TEXT NOT NULL REFERENCES docker_stacks(id) ON DELETE CASCADE,
    revision_hash TEXT NOT NULL,
    files TEXT NOT NULL DEFAULT '[]',
    message TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(stack_id, revision_hash)
);

CREATE TABLE IF NOT EXISTS docker_console_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    container_id TEXT NOT NULL,
    shell TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT '',
    tty INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'opening',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    closed_at TEXT,
    exit_code INTEGER,
    error TEXT NOT NULL DEFAULT '',
    client_ip TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS docker_deployments (
    id TEXT PRIMARY KEY,
    stack_id TEXT NOT NULL REFERENCES docker_stacks(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    revision_hash TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    compose_config_hash TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT 'up',
    status TEXT NOT NULL DEFAULT 'queued',
    command_id TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 0,
    pull_before_up INTEGER NOT NULL DEFAULT 0,
    remove_volumes INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    log TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS discovery_settings (
    id TEXT PRIMARY KEY CHECK (id = 'discovery'),
    acknowledged_at TEXT,
    acknowledged_by TEXT NOT NULL DEFAULT '',
    acknowledgement TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered_devices (
    id TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    mac TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    hostname TEXT NOT NULL DEFAULT '',
    vendor TEXT NOT NULL DEFAULT '',
    device_type TEXT NOT NULL DEFAULT 'unknown',
    os_guess TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'server',
    segment TEXT NOT NULL DEFAULT '',
    open_ports TEXT NOT NULL DEFAULT '[]',
    services TEXT NOT NULL DEFAULT '[]',
    mdns TEXT NOT NULL DEFAULT '{}',
    ssdp TEXT NOT NULL DEFAULT '{}',
    evidence TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'new'
        CHECK (state IN ('new', 'known', 'ignored', 'promoted')),
    agent_id TEXT,
    wake_target_id TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    mac_randomized INTEGER NOT NULL DEFAULT 0,
    wake_status TEXT NOT NULL DEFAULT '',
    wake_verified_at TEXT
);

CREATE TABLE IF NOT EXISTS discovery_scans (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    scope TEXT NOT NULL DEFAULT '[]',
    methods TEXT NOT NULL DEFAULT '[]',
    initiated_by TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'server',
    command_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    hosts_found INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    progress INTEGER NOT NULL DEFAULT 0,
    hosts_scanned INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS discovery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    device_id TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('new_device', 'new_port', 'service_changed',
                        'identity_conflict', 'went_dark', 'promoted',
                        'wake_verified')),
    detail TEXT NOT NULL DEFAULT '{}'
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents(last_seen);
CREATE INDEX IF NOT EXISTS idx_agents_machine_id ON agents(machine_id);
CREATE INDEX IF NOT EXISTS idx_commands_agent_status ON commands(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);
CREATE INDEX IF NOT EXISTS idx_commands_created ON commands(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_enrollment_expiry ON enrollment_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_agent_at ON telemetry_samples(agent_id, at);
CREATE INDEX IF NOT EXISTS idx_telemetry_at ON telemetry_samples(at);
CREATE INDEX IF NOT EXISTS idx_agent_events_agent_at ON agent_events(agent_id, at);
CREATE INDEX IF NOT EXISTS idx_agent_events_at ON agent_events(at);
CREATE INDEX IF NOT EXISTS idx_notification_log_at ON notification_log(at);
CREATE INDEX IF NOT EXISTS idx_notification_log_status ON notification_log(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_notification_log_agent ON notification_log(agent_id, at);
CREATE INDEX IF NOT EXISTS idx_commands_status_dispatched ON commands(status, dispatched_at);
CREATE INDEX IF NOT EXISTS idx_commands_status_created ON commands(status, created_at);
CREATE INDEX IF NOT EXISTS idx_command_output_command ON command_output(command_id, id);
CREATE INDEX IF NOT EXISTS idx_store_releases_package ON store_releases(package_id, published_at);
CREATE INDEX IF NOT EXISTS idx_store_events_at ON store_events(at);
CREATE INDEX IF NOT EXISTS idx_package_inventory_agent ON package_inventory(agent_id, manager);
CREATE INDEX IF NOT EXISTS idx_package_rules_agent ON package_rules(agent_id, manager);
CREATE INDEX IF NOT EXISTS idx_agent_profiles_slug ON agent_profiles(slug);
CREATE INDEX IF NOT EXISTS idx_saved_views_user ON saved_views(user_id);
CREATE INDEX IF NOT EXISTS idx_docker_hosts_available ON docker_hosts(available, reason);
CREATE INDEX IF NOT EXISTS idx_docker_containers_agent ON docker_containers(agent_id, state);
CREATE INDEX IF NOT EXISTS idx_docker_images_agent ON docker_images(agent_id);
CREATE INDEX IF NOT EXISTS idx_docker_volumes_agent ON docker_volumes(agent_id);
CREATE INDEX IF NOT EXISTS idx_docker_networks_agent ON docker_networks(agent_id);
CREATE INDEX IF NOT EXISTS idx_docker_stacks_slug ON docker_stacks(slug);
CREATE INDEX IF NOT EXISTS idx_docker_revisions_stack ON docker_stack_revisions(stack_id, created_at);
CREATE INDEX IF NOT EXISTS idx_docker_deployments_agent ON docker_deployments(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_docker_deployments_stack ON docker_deployments(stack_id, created_at);
CREATE INDEX IF NOT EXISTS idx_docker_deployments_active ON docker_deployments(agent_id, project_name, active);
CREATE INDEX IF NOT EXISTS idx_docker_deployments_command ON docker_deployments(command_id);
CREATE INDEX IF NOT EXISTS idx_docker_console_agent ON docker_console_sessions(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_docker_console_last_seen ON docker_console_sessions(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_mac ON discovered_devices(mac);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_ip ON discovered_devices(ip);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_hostname ON discovered_devices(hostname);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_state ON discovered_devices(state);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_segment ON discovered_devices(segment);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_last_seen ON discovered_devices(last_seen);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_type ON discovered_devices(device_type);
CREATE INDEX IF NOT EXISTS idx_discovery_scans_started ON discovery_scans(started_at);
CREATE INDEX IF NOT EXISTS idx_discovery_scans_status ON discovery_scans(status);
CREATE INDEX IF NOT EXISTS idx_discovery_scans_agent ON discovery_scans(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_discovery_scans_command ON discovery_scans(command_id);
CREATE INDEX IF NOT EXISTS idx_discovery_events_at ON discovery_events(at);
CREATE INDEX IF NOT EXISTS idx_discovery_events_device_at ON discovery_events(device_id, at);
CREATE INDEX IF NOT EXISTS idx_discovery_events_kind ON discovery_events(kind, at);
"""

DEFAULT_PROFILES = [
    {
        "id": "prof_workstation",
        "slug": "workstation",
        "name": "Workstation",
        "description": "Relaxed cadence for desktops and laptops.",
        "is_system": 1,
        "settings": {
            "heartbeat_seconds": 60,
            "modules": {
                "telemetry": True,
                "services": True,
                "startup": True,
                "gpu": True,
                "packages": True,
                "shell": True,
                "power": True,
                "service_control": True,
                "package_management": True,
                "self_update": True,
                "docker": False,
                "discovery": False,
            },
            "collection": {
                "service_inventory_seconds": 21600,
                "package_inventory_seconds": 86400,
                "package_full_seconds": 604800,
                "docker_inventory_seconds": 300,
            },
            "actions": {
                "allow_shell": True,
                "allow_power": True,
                "allow_service": True,
                "allow_packages": True,
                "allow_docker": False,
                "allow_docker_exec": False,
                "allow_docker_compose": False,
                "allow_docker_prune": False,
            },
            "update": {"allow_self_update": True, "channel": "stable"},
            "max_output_bytes": 200000,
        },
    },
    {
        "id": "prof_server",
        "slug": "server",
        "name": "Server",
        "description": "Tighter service and package monitoring for servers.",
        "is_system": 1,
        "settings": {
            "heartbeat_seconds": 30,
            "modules": {
                "telemetry": True,
                "services": True,
                "startup": True,
                "gpu": True,
                "packages": True,
                "shell": True,
                "power": True,
                "service_control": True,
                "package_management": True,
                "self_update": True,
                "docker": False,
                "discovery": False,
            },
            "collection": {
                "service_inventory_seconds": 3600,
                "package_inventory_seconds": 21600,
                "package_full_seconds": 604800,
                "docker_inventory_seconds": 300,
            },
            "actions": {
                "allow_shell": True,
                "allow_power": True,
                "allow_service": True,
                "allow_packages": True,
                "allow_docker": False,
                "allow_docker_exec": False,
                "allow_docker_compose": False,
                "allow_docker_prune": False,
            },
            "update": {"allow_self_update": True, "channel": "stable"},
            "max_output_bytes": 200000,
        },
    },
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        with self.connection() as conn:
            old_version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.executescript(SCHEMA)
            self._migrate(conn)
            self._seed_profiles(conn)
            if old_version < 6:
                self._enable_profile_packages(conn)
            if old_version < 7:
                self._ensure_docker_profile_defaults(conn)
            if old_version < 13:
                self._ensure_discovery_profile_defaults(conn)
            conn.executescript(INDEXES)
            conn.execute("PRAGMA user_version=14")
            conn.execute("UPDATE agents SET profile_id = 'prof_workstation' WHERE profile_id = ''")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        def columns(table: str) -> set[str]:
            return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}

        user_columns = columns("users")
        if "is_active" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

        agent_columns = columns("agents")
        if "machine_id" not in agent_columns:
            conn.execute("ALTER TABLE agents ADD COLUMN machine_id TEXT NOT NULL DEFAULT ''")
        if "watch_services" not in agent_columns:
            conn.execute("ALTER TABLE agents ADD COLUMN watch_services TEXT NOT NULL DEFAULT '[]'")
        if "policy" not in agent_columns:
            conn.execute(
                """ALTER TABLE agents ADD COLUMN policy TEXT NOT NULL
                   DEFAULT '{"allow_shell":true,"allow_power":true,"allow_service":true,"allow_packages":false}'"""
            )
        if "profile_id" not in agent_columns:
            conn.execute("ALTER TABLE agents ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'prof_workstation'")
        if "service_inventory_at" not in agent_columns:
            conn.execute("ALTER TABLE agents ADD COLUMN service_inventory_at TEXT")

        deployment_columns = columns("docker_deployments")
        if deployment_columns and "compose_config_hash" not in deployment_columns:
            conn.execute(
                "ALTER TABLE docker_deployments ADD COLUMN compose_config_hash TEXT NOT NULL DEFAULT ''"
            )

        docker_container_columns = columns("docker_containers")
        if docker_container_columns:
            if "memory_limit" not in docker_container_columns:
                conn.execute(
                    "ALTER TABLE docker_containers ADD COLUMN memory_limit INTEGER NOT NULL DEFAULT 0"
                )
            if "nano_cpus" not in docker_container_columns:
                conn.execute(
                    "ALTER TABLE docker_containers ADD COLUMN nano_cpus INTEGER NOT NULL DEFAULT 0"
                )
            if "pids_limit" not in docker_container_columns:
                conn.execute("ALTER TABLE docker_containers ADD COLUMN pids_limit INTEGER")

        state_columns = columns("package_inventory_state")
        if state_columns and "revision" not in state_columns:
            conn.execute("ALTER TABLE package_inventory_state ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")

        alert_columns = columns("alert_states")
        if alert_columns:
            if "incident_id" not in alert_columns:
                conn.execute("ALTER TABLE alert_states ADD COLUMN incident_id TEXT NOT NULL DEFAULT ''")
            if "first_seen_at" not in alert_columns:
                conn.execute("ALTER TABLE alert_states ADD COLUMN first_seen_at TEXT")
            if "last_seen_at" not in alert_columns:
                conn.execute("ALTER TABLE alert_states ADD COLUMN last_seen_at TEXT")
            if "cleared_at" not in alert_columns:
                conn.execute("ALTER TABLE alert_states ADD COLUMN cleared_at TEXT")
            now = utcnow_iso()
            conn.execute(
                """UPDATE alert_states SET
                     incident_id = CASE WHEN incident_id = '' THEN 'inc_' || lower(hex(randomblob(8))) ELSE incident_id END,
                     first_seen_at = COALESCE(first_seen_at, acknowledged_at, ?),
                     last_seen_at = COALESCE(last_seen_at, acknowledged_at, ?)
                   WHERE incident_id = '' OR first_seen_at IS NULL OR last_seen_at IS NULL""",
                (now, now),
            )

        remote_columns = columns("remote_install_jobs")
        if remote_columns and "watch_services" not in remote_columns:
            conn.execute("ALTER TABLE remote_install_jobs ADD COLUMN watch_services TEXT NOT NULL DEFAULT '[]'")

        discovery_scan_columns = columns("discovery_scans")
        if discovery_scan_columns:
            if "agent_id" not in discovery_scan_columns:
                conn.execute("ALTER TABLE discovery_scans ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''")
            if "source" not in discovery_scan_columns:
                conn.execute("ALTER TABLE discovery_scans ADD COLUMN source TEXT NOT NULL DEFAULT 'server'")
            if "command_id" not in discovery_scan_columns:
                conn.execute("ALTER TABLE discovery_scans ADD COLUMN command_id TEXT NOT NULL DEFAULT ''")

        device_columns = columns("discovered_devices")
        if device_columns:
            if "wake_status" not in device_columns:
                conn.execute(
                    "ALTER TABLE discovered_devices ADD COLUMN wake_status TEXT NOT NULL DEFAULT ''"
                )
            if "wake_verified_at" not in device_columns:
                conn.execute("ALTER TABLE discovered_devices ADD COLUMN wake_verified_at TEXT")

        event_table = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'discovery_events'"
        ).fetchone()
        if event_table is not None and "wake_verified" not in str(event_table["sql"] or ""):
            # SQLite cannot alter a CHECK constraint, so rebuild the small event
            # table. Indexes are recreated by INDEXES after this migration.
            conn.execute("ALTER TABLE discovery_events RENAME TO discovery_events_old")
            conn.execute(
                """CREATE TABLE discovery_events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       at TEXT NOT NULL,
                       device_id TEXT NOT NULL,
                       kind TEXT NOT NULL
                           CHECK (kind IN ('new_device', 'new_port', 'service_changed',
                                           'identity_conflict', 'went_dark', 'promoted',
                                           'wake_verified')),
                       detail TEXT NOT NULL DEFAULT '{}'
                   )"""
            )
            conn.execute(
                """INSERT INTO discovery_events (id, at, device_id, kind, detail)
                   SELECT id, at, device_id, kind, detail FROM discovery_events_old"""
            )
            conn.execute("DROP TABLE discovery_events_old")

    @staticmethod
    def _seed_profiles(conn: sqlite3.Connection) -> None:
        now = utcnow_iso()
        for profile in DEFAULT_PROFILES:
            conn.execute(
                """INSERT OR IGNORE INTO agent_profiles
                   (id, slug, name, description, is_system, revision, settings, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    profile["id"],
                    profile["slug"],
                    profile["name"],
                    profile["description"],
                    profile["is_system"],
                    dumps(profile["settings"]),
                    now,
                    now,
                ),
            )

    @staticmethod
    def _ensure_docker_profile_defaults(conn: sqlite3.Connection) -> None:
        """Add Docker-off defaults to profiles created before schema version 7."""
        for row in conn.execute("SELECT id, settings, revision FROM agent_profiles").fetchall():
            settings = loads(row["settings"], {})
            if not isinstance(settings, dict):
                continue
            modules = settings.get("modules")
            if not isinstance(modules, dict):
                modules = {}
                settings["modules"] = modules
            collection = settings.get("collection")
            if not isinstance(collection, dict):
                collection = {}
                settings["collection"] = collection
            actions = settings.get("actions")
            if not isinstance(actions, dict):
                actions = {}
                settings["actions"] = actions
            changed = False
            for key, value in (
                ("docker", False),
                ("allow_docker", False),
                ("allow_docker_exec", False),
                ("allow_docker_compose", False),
                ("allow_docker_prune", False),
            ):
                target = modules if key == "docker" else actions
                if key not in target:
                    target[key] = value
                    changed = True
            if "docker_inventory_seconds" not in collection:
                collection["docker_inventory_seconds"] = 300
                changed = True
            if changed:
                conn.execute(
                    "UPDATE agent_profiles SET settings = ?, revision = revision + 1 WHERE id = ?",
                    (dumps(settings), row["id"]),
                )

    @staticmethod
    def _ensure_discovery_profile_defaults(conn: sqlite3.Connection) -> None:
        """Add discovery-off defaults to profiles and agents from schema v12."""
        for row in conn.execute("SELECT id, settings, revision FROM agent_profiles").fetchall():
            settings = loads(row["settings"], {})
            if not isinstance(settings, dict):
                continue
            modules = settings.get("modules")
            if not isinstance(modules, dict):
                modules = {}
                settings["modules"] = modules
            changed = False
            if "discovery" not in modules:
                modules["discovery"] = False
                changed = True
            if changed:
                conn.execute(
                    "UPDATE agent_profiles SET settings = ?, revision = revision + 1 WHERE id = ?",
                    (dumps(settings), row["id"]),
                )
        for agent in conn.execute("SELECT id, policy FROM agents").fetchall():
            policy = loads(agent["policy"], {})
            if not isinstance(policy, dict):
                policy = {}
            if "allow_discovery" not in policy:
                policy["allow_discovery"] = False
                conn.execute("UPDATE agents SET policy = ? WHERE id = ?", (dumps(policy), agent["id"]))

    @staticmethod
    def _enable_profile_packages(conn: sqlite3.Connection) -> None:
        """Enable package management by default for built-in profiles (v6)."""
        rows = conn.execute(
            "SELECT id, settings FROM agent_profiles WHERE is_system = 1"
        ).fetchall()
        for row in rows:
            settings = loads(row["settings"], {})
            if not isinstance(settings, dict):
                continue
            modules = settings.get("modules")
            if not isinstance(modules, dict):
                modules = {}
                settings["modules"] = modules
            actions = settings.get("actions")
            if not isinstance(actions, dict):
                actions = {}
                settings["actions"] = actions
            modules["package_management"] = True
            actions["allow_packages"] = True
            conn.execute(
                "UPDATE agent_profiles SET settings = ?, revision = revision + 1 WHERE id = ?",
                (dumps(settings), row["id"]),
            )
        for agent in conn.execute("SELECT id, policy FROM agents").fetchall():
            policy = loads(agent["policy"], {})
            if not isinstance(policy, dict):
                policy = {}
            policy["allow_packages"] = True
            conn.execute("UPDATE agents SET policy = ? WHERE id = ?", (dumps(policy), agent["id"]))

    def get_user_by_id(self, user_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def get_user_by_name(self, username: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def count_users(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
            return int(row["n"])

    def count_active_admins(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()
            return int(row["n"])

    def create_user(
        self,
        username: str,
        role: str = "admin",
        password_hash: str | None = None,
        display_name: str = "",
        email: str = "",
        source: str = "local",
    ) -> sqlite3.Row:
        now = utcnow_iso()
        with self.connection() as conn:
            cur = conn.execute(
                """INSERT INTO users (username, display_name, email, password_hash, role, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (username, display_name or username, email, password_hash, role, source, now),
            )
            return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()

    def upsert_ad_user(
        self,
        username: str,
        role: str,
        display_name: str = "",
        email: str = "",
    ) -> sqlite3.Row:
        now = utcnow_iso()
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                cur = conn.execute(
                    """INSERT INTO users (username, display_name, email, password_hash, role, source, created_at, last_login_at)
                       VALUES (?, ?, ?, NULL, ?, 'ad', ?, ?)""",
                    (username, display_name or username, email, role, now, now),
                )
                return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
            if row["source"] == "local":
                # Never let directory login silently take over a local account.
                return row
            conn.execute(
                """UPDATE users SET display_name = ?, email = ?, role = ?, last_login_at = ?
                   WHERE id = ?""",
                (display_name or row["display_name"], email or row["email"], role, now, row["id"]),
            )
            return conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()

    # ---- Sessions -------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        user_id: int,
        expires_at: str,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        now = utcnow_iso()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, user_id, created_at, last_seen_at, expires_at, ip, user_agent)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, user_id, now, now, expires_at, ip[:128], user_agent[:512]),
            )

    def get_session(self, session_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def touch_session(self, session_id: str) -> None:
        row = self.get_session(session_id)
        if row is None:
            return
        last = parse_iso(row["last_seen_at"])
        if last is not None and (utcnow() - last).total_seconds() < 60:
            return
        with self.connection() as conn:
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (utcnow_iso(), session_id))

    def revoke_session(self, session_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (utcnow_iso(), session_id),
            )

    def revoke_user_sessions(self, user_id: int, except_session: str | None = None) -> int:
        now = utcnow_iso()
        with self.connection() as conn:
            if except_session:
                cur = conn.execute(
                    """UPDATE sessions SET revoked_at = ?
                       WHERE user_id = ? AND revoked_at IS NULL AND id != ?""",
                    (now, user_id, except_session),
                )
            else:
                cur = conn.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                    (now, user_id),
                )
            return int(cur.rowcount)

    def list_sessions(self, user_id: int) -> list[sqlite3.Row]:
        now = utcnow_iso()
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM sessions
                       WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
                       ORDER BY last_seen_at DESC""",
                    (user_id, now),
                ).fetchall()
            )

    def count_sessions(self, user_id: int) -> int:
        return len(self.list_sessions(user_id))

    def cleanup_sessions(self, retention_days: int = 7) -> int:
        cutoff = (utcnow() - timedelta(days=retention_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connection() as conn:
            cur = conn.execute(
                """DELETE FROM sessions
                   WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)""",
                (cutoff, cutoff),
            )
            return int(cur.rowcount)

    # ---- Telemetry history ---------------------------------------------

    def insert_telemetry_sample(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        at: str,
        *,
        cpu_pct: float | None = None,
        mem_pct: float | None = None,
        mem_used: int | None = None,
        mem_total: int | None = None,
        uptime_seconds: int | None = None,
        disk_max_pct: float | None = None,
        gpu_max_util: float | None = None,
        gpu_max_temp: float | None = None,
        service_down: int = 0,
    ) -> None:
        conn.execute(
            """INSERT INTO telemetry_samples
               (agent_id, at, cpu_pct, mem_pct, mem_used, mem_total, uptime_seconds,
                disk_max_pct, gpu_max_util, gpu_max_temp, service_down)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_id,
                at,
                cpu_pct,
                mem_pct,
                mem_used,
                mem_total,
                uptime_seconds,
                disk_max_pct,
                gpu_max_util,
                gpu_max_temp,
                service_down,
            ),
        )

    def telemetry_history(self, agent_id: str, hours: int, points: int) -> list[dict[str, Any]]:
        """Return a fixed-size, gap-aware time-bucketed series.

        Every bucket in the requested window is returned, even when it has no
        samples. Missing samples are explicit ``None`` values so the UI can
        break a line across an offline interval instead of connecting across it.
        Aggregation stays in SQL, so a years-long sample table never
        materialises an unbounded result set in Python.
        """
        span = max(1, int(hours)) * 3600
        points = max(10, min(1000, int(points)))
        end = utcnow()
        start = end - timedelta(seconds=span)
        start_epoch = int(start.timestamp())
        cutoff = start.isoformat(timespec="seconds").replace("+00:00", "Z")
        cutoff_end = end.isoformat(timespec="seconds").replace("+00:00", "Z")
        bucket_seconds = span / points
        with self.connection() as conn:
            try:
                rows = conn.execute(
                    """SELECT
                           MIN(?, MAX(0, CAST(((CAST(strftime('%s', at) AS INTEGER) - ?) * ?) / ? AS INTEGER))) AS bucket,
                           AVG(cpu_pct) AS cpu_pct,
                           AVG(mem_pct) AS mem_pct,
                           AVG(disk_max_pct) AS disk_max_pct,
                           AVG(gpu_max_util) AS gpu_max_util,
                           AVG(gpu_max_temp) AS gpu_max_temp,
                           AVG(service_down) AS service_down
                       FROM telemetry_samples
                       WHERE agent_id = ? AND at >= ? AND at <= ?
                       GROUP BY bucket
                       ORDER BY bucket ASC""",
                    (
                        points - 1,
                        start_epoch,
                        points,
                        span,
                        agent_id,
                        cutoff,
                        cutoff_end,
                    ),
                ).fetchall()
            except sqlite3.OperationalError:
                # An older database may not have the table yet.
                return []
        by_bucket = {int(row["bucket"]): row for row in rows}
        series: list[dict[str, Any]] = []
        for index in range(points):
            row = by_bucket.get(index)
            at = start + timedelta(seconds=index * bucket_seconds)
            series.append(
                {
                    "at": at.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "cpu_pct": row["cpu_pct"] if row is not None else None,
                    "mem_pct": row["mem_pct"] if row is not None else None,
                    "disk_max_pct": row["disk_max_pct"] if row is not None else None,
                    "gpu_max_util": row["gpu_max_util"] if row is not None else None,
                    "gpu_max_temp": row["gpu_max_temp"] if row is not None else None,
                    "service_down": row["service_down"] if row is not None else None,
                }
            )
        return series

    def prune_telemetry(self, retention_days: int = 7) -> int:
        days = max(1, min(365, int(retention_days)))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connection() as conn:
            try:
                cur = conn.execute("DELETE FROM telemetry_samples WHERE at < ?", (cutoff,))
            except sqlite3.OperationalError:
                return 0
            return int(cur.rowcount)

    # ---- Persistent agent state-change events ---------------------------

    def record_agent_event(
        self,
        agent_id: str,
        kind: str,
        severity: str,
        title: str,
        detail: Any = None,
        *,
        at: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int | None:
        """Store one bounded state-change event, best-effort.

        Event recording must never break a heartbeat or command path, so write
        failures are swallowed. Callers still keep their concise title and
        bounded detail structure.
        """
        encoded = dumps(detail or {})
        if len(encoded) > 4000:
            encoded = dumps({"truncated": True})
        params = (
            agent_id,
            at or utcnow_iso(),
            str(kind)[:32],
            str(severity or "info")[:16],
            str(title)[:240],
            encoded,
        )
        sql = """INSERT INTO agent_events (agent_id, at, kind, severity, title, detail)
                 VALUES (?, ?, ?, ?, ?, ?)"""
        try:
            if conn is not None:
                cur = conn.execute(sql, params)
                return int(cur.lastrowid) if cur.lastrowid is not None else None
            with self.connection() as own:
                cur = own.execute(sql, params)
                return int(cur.lastrowid) if cur.lastrowid is not None else None
        except sqlite3.Error:
            return None

    def list_agent_events(self, agent_id: str, hours: int, limit: int) -> list[sqlite3.Row]:
        hours = max(1, min(720, int(hours)))
        limit = max(1, min(500, int(limit)))
        cutoff = (utcnow() - timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connection() as conn:
            try:
                return list(
                    conn.execute(
                        """SELECT * FROM agent_events
                           WHERE agent_id = ? AND at >= ?
                           ORDER BY at DESC, id DESC LIMIT ?""",
                        (agent_id, cutoff, limit),
                    ).fetchall()
                )
            except sqlite3.OperationalError:
                return []

    def prune_agent_events(self, retention_days: int = 90) -> int:
        days = max(1, min(3650, int(retention_days)))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connection() as conn:
            try:
                cur = conn.execute("DELETE FROM agent_events WHERE at < ?", (cutoff,))
            except sqlite3.OperationalError:
                return 0
            return int(cur.rowcount)

    def command_history(
        self,
        agent_id: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
        status: str | None = None,
        query: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> tuple[list[sqlite3.Row], int]:
        """Return filtered command rows plus the unpaginated match count.

        Every user-supplied value is bound as a SQL parameter. ``limit=None``
        returns all matches, which the CSV export uses.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            pattern = f"%{query}%"
            clauses.append(
                """(kind LIKE ? OR status LIKE ? OR stdout LIKE ? OR stderr LIKE ?
                    OR error LIKE ? OR args LIKE ?)"""
            )
            params.extend([pattern] * 6)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if until:
            clauses.append("created_at <= ?")
            params.append(until)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM commands{where}", params
                ).fetchone()["n"]
            )
            sql = f"SELECT * FROM commands{where} ORDER BY created_at DESC, rowid DESC"
            row_params = list(params)
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                row_params.extend([max(1, int(limit)), max(0, int(offset))])
            elif offset:
                sql += " LIMIT -1 OFFSET ?"
                row_params.append(max(0, int(offset)))
            rows = conn.execute(sql, row_params).fetchall()
        return list(rows), total

    def add_command_output(
        self,
        command_id: str,
        stream: str,
        seq: int,
        text: str,
        limit: int,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Persist one ordered output chunk, enforcing idempotency and the cap."""
        if conn is not None:
            return self._add_command_output(conn, command_id, stream, seq, text, limit)
        with self.transaction() as own:
            return self._add_command_output(own, command_id, stream, seq, text, limit)

    def _add_command_output(
        self,
        conn: sqlite3.Connection,
        command_id: str,
        stream: str,
        seq: int,
        text: str,
        limit: int,
    ) -> dict[str, Any]:
        existing = conn.execute(
            """SELECT * FROM command_output
               WHERE command_id = ? AND stream = ? AND seq = ?""",
            (command_id, stream, seq),
        ).fetchone()
        if existing is not None:
            if existing["text"] == text:
                return {"state": "duplicate", "chunk": existing}
            return {"state": "conflict", "chunk": existing}
        last = conn.execute(
            """SELECT MAX(seq) AS seq FROM command_output
               WHERE command_id = ? AND stream = ?""",
            (command_id, stream),
        ).fetchone()
        if last is not None and last["seq"] is not None and seq <= int(last["seq"]):
            return {"state": "out_of_order", "last_seq": int(last["seq"])}
        total = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(text)), 0) AS n FROM command_output WHERE command_id = ?",
            (command_id,),
        ).fetchone()["n"]
        if int(total) + len(text) > limit:
            return {"state": "over_limit", "total": int(total), "limit": int(limit)}
        cursor = conn.execute(
            """INSERT INTO command_output (command_id, stream, seq, text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (command_id, stream, seq, text, utcnow_iso()),
        )
        chunk = conn.execute(
            "SELECT * FROM command_output WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return {"state": "accepted", "chunk": chunk}

    def replay_command_output(self, command_id: str, limit: int) -> list[sqlite3.Row]:
        """Return the newest bounded window of chunks in insertion order."""
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM (
                           SELECT * FROM command_output
                           WHERE command_id = ? ORDER BY id DESC LIMIT ?
                       ) ORDER BY id ASC""",
                    (command_id, max(1, int(limit))),
                ).fetchall()
            )

    def command_output_since(
        self, command_id: str, after_id: int, limit: int = 500
    ) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM command_output
                       WHERE command_id = ? AND id > ? ORDER BY id ASC LIMIT ?""",
                    (command_id, max(0, int(after_id)), max(1, int(limit))),
                ).fetchall()
            )

    def prune_commands(self, retention_days: int = 90) -> int:
        """Delete terminal command rows and their streamed chunks older than retention."""
        days = max(1, int(retention_days))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.transaction() as conn:
            conn.execute(
                """DELETE FROM command_output WHERE command_id IN (
                       SELECT id FROM commands
                       WHERE status IN ('succeeded', 'failed', 'cancelled') AND created_at < ?
                   )""",
                (cutoff,),
            )
            cur = conn.execute(
                """DELETE FROM commands
                   WHERE status IN ('succeeded', 'failed', 'cancelled') AND created_at < ?""",
                (cutoff,),
            )
            # Agent state-change events share the command retention policy.
            try:
                conn.execute("DELETE FROM agent_events WHERE at < ?", (cutoff,))
            except sqlite3.OperationalError:
                pass
            return int(cur.rowcount)

    def prune_audit(self, retention_days: int = 365) -> int:
        """Delete audit rows older than the retention window."""
        days = max(1, int(retention_days))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM audit_log WHERE at < ?", (cutoff,))
            return int(cur.rowcount)

    # ---- Alert acknowledgement / snooze -------------------------------

    @staticmethod
    def _alert_states(conn: sqlite3.Connection, agent_id: str | None = None) -> Any:
        if agent_id is None:
            rows = conn.execute("SELECT * FROM alert_states").fetchall()
            grouped: dict[str, dict[str, sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(row["agent_id"], {})[row["alert_key"]] = row
            return grouped
        rows = conn.execute("SELECT * FROM alert_states WHERE agent_id = ?", (agent_id,)).fetchall()
        return {row["alert_key"]: row for row in rows}

    def get_alert_states(
        self,
        agent_id: str,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, sqlite3.Row]:
        if conn is not None:
            return self._alert_states(conn, agent_id)
        with self.connection() as own:
            return self._alert_states(own, agent_id)

    def get_all_alert_states(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, dict[str, sqlite3.Row]]:
        if conn is not None:
            return self._alert_states(conn)
        with self.connection() as own:
            return self._alert_states(own)

    @staticmethod
    def _new_incident_id() -> str:
        return "inc_" + secrets.token_hex(8)

    def _reconcile_alert_states(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        alerts: dict[str, dict[str, Any]] | list[dict[str, Any]],
        *,
        now: datetime,
        at: str,
        ack_ttl_hours: int,
        allow_clear: bool,
    ) -> dict[str, sqlite3.Row]:
        if isinstance(alerts, dict):
            incoming = {str(key): alert for key, alert in alerts.items()}
        else:
            incoming = {str(alert["key"]): alert for alert in alerts}
        rows = self._alert_states(conn, agent_id)
        ttl = timedelta(hours=max(1, min(8760, int(ack_ttl_hours))))

        for key, alert in incoming.items():
            row = rows.get(key)
            level = str(alert.get("level") or "warning")
            message = str(alert.get("message") or key)
            if row is None or row["cleared_at"] or not row["incident_id"]:
                previous_incident = row["incident_id"] if row is not None else ""
                incident_id = self._new_incident_id()
                if row is None:
                    conn.execute(
                        """INSERT INTO alert_states
                           (agent_id, alert_key, incident_id, first_seen_at, last_seen_at,
                            cleared_at, acknowledged_at, acknowledged_by, snoozed_until)
                           VALUES (?, ?, ?, ?, ?, NULL, NULL, '', NULL)""",
                        (agent_id, key, incident_id, at, at),
                    )
                else:
                    conn.execute(
                        """UPDATE alert_states SET
                             incident_id = ?, first_seen_at = ?, last_seen_at = ?,
                             cleared_at = NULL, acknowledged_at = NULL,
                             acknowledged_by = '', snoozed_until = NULL
                           WHERE agent_id = ? AND alert_key = ?""",
                        (incident_id, at, at, agent_id, key),
                    )
                detail = {
                    "action": "raised",
                    "alert_key": key,
                    "incident_id": incident_id,
                    "level": level,
                    "message": message,
                }
                if previous_incident:
                    detail["previous_incident_id"] = previous_incident
                    detail["recurrence"] = True
                self.record_agent_event(
                    agent_id,
                    "alert",
                    level,
                    f"Alert raised: {message}",
                    detail,
                    at=at,
                    conn=conn,
                )
                continue

            acknowledged_at = parse_iso(row["acknowledged_at"])
            ack_expired = bool(acknowledged_at and now - acknowledged_at >= ttl)
            if ack_expired:
                conn.execute(
                    """UPDATE alert_states SET
                         last_seen_at = ?, acknowledged_at = NULL, acknowledged_by = ''
                       WHERE agent_id = ? AND alert_key = ?""",
                    (at, agent_id, key),
                )
                self.record_agent_event(
                    agent_id,
                    "alert",
                    "info",
                    f"Alert acknowledgement expired: {message}",
                    {
                        "action": "acknowledgement_expired",
                        "alert_key": key,
                        "incident_id": row["incident_id"],
                        "level": level,
                        "message": message,
                    },
                    at=at,
                    conn=conn,
                )
            else:
                conn.execute(
                    "UPDATE alert_states SET last_seen_at = ? WHERE agent_id = ? AND alert_key = ?",
                    (at, agent_id, key),
                )

        if allow_clear:
            for key, row in rows.items():
                if key in incoming or row["cleared_at"]:
                    continue
                conn.execute(
                    """UPDATE alert_states SET
                         cleared_at = ?, acknowledged_at = NULL, acknowledged_by = '',
                         snoozed_until = NULL
                       WHERE agent_id = ? AND alert_key = ?""",
                    (at, agent_id, key),
                )
                self.record_agent_event(
                    agent_id,
                    "alert",
                    "info",
                    f"Alert cleared: {key}",
                    {
                        "action": "cleared",
                        "alert_key": key,
                        "incident_id": row["incident_id"],
                        "level": "",
                        "message": "",
                    },
                    at=at,
                    conn=conn,
                )

        return self._alert_states(conn, agent_id)

    def reconcile_alert_states(
        self,
        agent_id: str,
        alerts: dict[str, dict[str, Any]] | list[dict[str, Any]],
        *,
        now: datetime | None = None,
        at: str | None = None,
        ack_ttl_hours: int = 168,
        conn: sqlite3.Connection | None = None,
        allow_clear: bool = True,
    ) -> dict[str, sqlite3.Row]:
        """Materialize the current alert conditions as incident state.

        A new condition gets a stable incident id and first/last-seen stamps. A
        still-present condition only advances last-seen, preserving an
        acknowledgement until its TTL expires. A condition that is no longer
        reported is marked cleared and loses its acknowledgement and snooze. If
        the same key reappears after being cleared it starts a new incident.
        """
        now_value = now or utcnow()
        at_value = at or now_value.isoformat(timespec="seconds").replace("+00:00", "Z")
        if conn is not None:
            return self._reconcile_alert_states(
                conn,
                agent_id,
                alerts,
                now=now_value,
                at=at_value,
                ack_ttl_hours=ack_ttl_hours,
                allow_clear=allow_clear,
            )
        with self.transaction() as own:
            return self._reconcile_alert_states(
                own,
                agent_id,
                alerts,
                now=now_value,
                at=at_value,
                ack_ttl_hours=ack_ttl_hours,
                allow_clear=allow_clear,
            )

    def set_alert_ack(
        self,
        agent_id: str,
        alert_key: str,
        actor: str,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        now = utcnow_iso()
        sql = """INSERT INTO alert_states
                 (agent_id, alert_key, incident_id, first_seen_at, last_seen_at, acknowledged_at, acknowledged_by)
                 VALUES (?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(agent_id, alert_key) DO UPDATE SET
                   acknowledged_at = excluded.acknowledged_at,
                   acknowledged_by = excluded.acknowledged_by"""
        params = (agent_id, alert_key, self._new_incident_id(), now, now, now, actor)
        if conn is not None:
            conn.execute(sql, params)
            return
        with self.connection() as own:
            own.execute(sql, params)

    def set_alert_snooze(
        self,
        agent_id: str,
        alert_key: str,
        minutes: int,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        now = utcnow_iso()
        snoozed_until = (
            utcnow() + timedelta(minutes=int(minutes))
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        sql = """INSERT INTO alert_states
                 (agent_id, alert_key, incident_id, first_seen_at, last_seen_at, snoozed_until)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT(agent_id, alert_key) DO UPDATE SET
                   snoozed_until = excluded.snoozed_until"""
        params = (agent_id, alert_key, self._new_incident_id(), now, now, snoozed_until)
        if conn is not None:
            conn.execute(sql, params)
        else:
            with self.connection() as own:
                own.execute(sql, params)
        return snoozed_until

    def clear_alert_state(
        self,
        agent_id: str,
        alert_key: str,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """Clear the acknowledgement/snooze on the current incident."""
        sql = """UPDATE alert_states SET
                   acknowledged_at = NULL, acknowledged_by = '', snoozed_until = NULL
                 WHERE agent_id = ? AND alert_key = ?"""
        params = (agent_id, alert_key)
        if conn is not None:
            return conn.execute(sql, params).rowcount == 1
        with self.connection() as own:
            return own.execute(sql, params).rowcount == 1

    # ---- Notification outbox and offline availability ------------------

    def get_availability(self, agent_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM notification_availability WHERE agent_id = ?", (agent_id,)
            ).fetchone()

    def set_offline_availability(self, agent_id: str, incident_id: str, since: str) -> None:
        now = utcnow_iso()
        sql = """INSERT INTO notification_availability
                   (agent_id, offline_incident_id, offline_since, updated_at)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(agent_id) DO UPDATE SET
                   offline_incident_id = excluded.offline_incident_id,
                   offline_since = excluded.offline_since,
                   updated_at = excluded.updated_at"""
        with self.connection() as conn:
            conn.execute(sql, (agent_id, incident_id, since, now))

    def clear_offline_availability(self, agent_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM notification_availability WHERE agent_id = ?", (agent_id,))

    def notification_exists(self, event_key: str) -> bool:
        with self.connection() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM notification_log WHERE event_key = ? LIMIT 1", (event_key,)
                ).fetchone()
                is not None
            )

    def get_notification(self, event_key: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM notification_log WHERE event_key = ?", (event_key,)
            ).fetchone()

    def enqueue_notification(
        self,
        event_key: str,
        kind: str,
        severity: str,
        agent_id: str | None,
        title: str,
        body: str = "",
        url: str = "",
    ) -> bool:
        """Insert one outbox row. Returns False when the event is already queued."""
        now = utcnow_iso()
        sql = """INSERT OR IGNORE INTO notification_log
                   (at, event_key, kind, severity, agent_id, title, body, url,
                    status, attempts, next_attempt_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)"""
        with self.connection() as conn:
            cur = conn.execute(
                sql,
                (
                    now,
                    event_key,
                    str(kind)[:32],
                    str(severity)[:16],
                    agent_id,
                    str(title)[:500],
                    str(body)[:4000],
                    str(url)[:1000],
                    now,
                ),
            )
            return cur.rowcount == 1

    def due_notifications(self, now: str, limit: int = 20) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM notification_log
                       WHERE status = 'pending'
                         AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                       ORDER BY CASE severity
                                  WHEN 'critical' THEN 0
                                  WHEN 'warning' THEN 1
                                  ELSE 2
                                END,
                                id ASC
                       LIMIT ?""",
                    (now, max(1, int(limit))),
                ).fetchall()
            )

    def mark_notification_sent(self, notification_id: int) -> None:
        with self.connection() as conn:
            conn.execute(
                """UPDATE notification_log SET
                     status = 'sent', attempts = attempts + 1, sent_at = ?,
                     next_attempt_at = NULL, error = ''
                   WHERE id = ?""",
                (utcnow_iso(), notification_id),
            )

    def mark_notification_failed(
        self,
        notification_id: int,
        error: str,
        next_attempt_at: str | None,
    ) -> None:
        status = "pending" if next_attempt_at else "failed"
        with self.connection() as conn:
            conn.execute(
                """UPDATE notification_log SET
                     status = ?, attempts = attempts + 1,
                     next_attempt_at = ?, error = ?
                   WHERE id = ?""",
                (status, next_attempt_at, str(error)[:1000], notification_id),
            )

    def list_notifications(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM notification_log ORDER BY id DESC LIMIT ?",
                    (max(1, min(200, int(limit))),),
                ).fetchall()
            )

    def notification_counts(self) -> dict[str, int]:
        counts = {"pending": 0, "sent": 0, "failed": 0}
        with self.connection() as conn:
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM notification_log GROUP BY status"
            ).fetchall():
                counts[str(row["status"])] = int(row["n"])
        return counts

    def retry_failed_notifications(self) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """UPDATE notification_log SET
                     status = 'pending', attempts = 0, next_attempt_at = ?, error = ''
                   WHERE status = 'failed'""",
                (utcnow_iso(),),
            )
            return int(cur.rowcount or 0)

    def prune_notifications(self, retention_days: int = 90) -> int:
        days = max(1, min(3650, int(retention_days)))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connection() as conn:
            cur = conn.execute(
                "DELETE FROM notification_log WHERE at < ? AND status IN ('sent', 'failed')",
                (cutoff,),
            )
            return int(cur.rowcount or 0)


    # ---- Agent / command housekeeping ----------------------------------

    def set_agent_watch_services_and_policy(
        self,
        agent_id: str,
        watch_services: list[str],
        policy: dict[str, Any],
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE agents SET watch_services = ?, policy = ? WHERE id = ?",
                (dumps(watch_services), dumps(policy), agent_id),
            )

    def sweep_stale_commands(self, dispatched_grace_seconds: int = 120, queued_ttl_hours: int = 24) -> int:
        """Fail commands that can no longer produce a useful result.

        A dispatched command gets the shell timeout plus a grace period. A queued
        command expires after the configured TTL so an offline endpoint does not
        receive week-old work when it finally reconnects.
        """
        now = utcnow()
        grace = max(10, int(dispatched_grace_seconds))
        queued_ttl = timedelta(hours=max(1, int(queued_ttl_hours)))
        changed = 0
        with self.transaction() as conn:
            dispatched = conn.execute(
                "SELECT id, args, dispatched_at, created_at FROM commands WHERE status = 'dispatched'"
            ).fetchall()
            for row in dispatched:
                args = loads(row["args"], {})
                try:
                    timeout = int(args.get("timeout", 300))
                except (TypeError, ValueError):
                    timeout = 300
                timeout = max(5, min(timeout, 300))
                started = parse_iso(row["dispatched_at"] or row["created_at"])
                if started is None:
                    continue
                if (now - started).total_seconds() <= timeout + grace:
                    continue
                conn.execute(
                    """UPDATE commands SET status = 'failed', completed_at = ?,
                       error = 'Command timed out before the agent returned a result.' WHERE id = ?""",
                    (utcnow_iso(), row["id"]),
                )
                self.audit(
                    "system",
                    "command.expired",
                    row["id"],
                    {"reason": "dispatched_timeout"},
                    conn=conn,
                )
                changed += 1

            queued = conn.execute(
                "SELECT id, created_at FROM commands WHERE status = 'queued'"
            ).fetchall()
            for row in queued:
                created = parse_iso(row["created_at"])
                if created is None or (now - created) <= queued_ttl:
                    continue
                conn.execute(
                    """UPDATE commands SET status = 'failed', completed_at = ?,
                       error = 'Command expired while the agent was offline.' WHERE id = ?""",
                    (utcnow_iso(), row["id"]),
                )
                self.audit(
                    "system",
                    "command.expired",
                    row["id"],
                    {"reason": "queued_ttl"},
                    conn=conn,
                )
                changed += 1
        return changed

    # ---- Store ---------------------------------------------------------

    def create_store_package(
        self,
        *,
        slug: str,
        name: str,
        vendor: str = "",
        description: str = "",
        kind: str = "app",
        platforms: list[str] | None = None,
        actor: str = "",
    ) -> sqlite3.Row:
        package_id = "pkg_" + secrets.token_hex(8)
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO store_packages
                   (id, slug, name, vendor, description, kind, platforms, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    package_id,
                    slug,
                    name,
                    vendor,
                    description,
                    kind,
                    dumps(platforms or []),
                    actor,
                    utcnow_iso(),
                ),
            )
            row = conn.execute("SELECT * FROM store_packages WHERE id = ?", (package_id,)).fetchone()
        return row

    def get_store_package(self, slug: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM store_packages WHERE slug = ? COLLATE NOCASE", (slug,)
            ).fetchone()

    def list_store_packages(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM store_packages WHERE archived_at IS NULL ORDER BY name COLLATE NOCASE"
                ).fetchall()
            )

    def create_store_release(
        self,
        *,
        package_id: str,
        version: str,
        artifact_type: str,
        filename: str,
        size: int,
        sha256: str,
        manifest: dict[str, Any],
        actor: str,
        channel: str = "stable",
        min_version: str = "",
        mandatory: bool = False,
        notes_url: str = "",
        signature: str | None = None,
        key_id: str | None = None,
    ) -> sqlite3.Row:
        release_id = "rel_" + secrets.token_hex(8)
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO store_releases
                   (id, package_id, version, artifact_type, filename, size, sha256, manifest,
                    signature, key_id, channel, min_version, mandatory, notes_url,
                    published_at, published_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    release_id,
                    package_id,
                    version,
                    artifact_type,
                    filename,
                    size,
                    sha256,
                    dumps(manifest),
                    signature,
                    key_id,
                    channel,
                    min_version,
                    1 if mandatory else 0,
                    notes_url,
                    utcnow_iso(),
                    actor,
                ),
            )
            row = conn.execute("SELECT * FROM store_releases WHERE id = ?", (release_id,)).fetchone()
        return row

    def get_store_release(self, release_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM store_releases WHERE id = ?", (release_id,)).fetchone()

    def list_store_releases(self, package_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM store_releases WHERE package_id = ? ORDER BY published_at DESC",
                    (package_id,),
                ).fetchall()
            )

    def latest_store_release(self, slug: str, artifact_type: str | None = None) -> sqlite3.Row | None:
        with self.connection() as conn:
            query = """SELECT r.* FROM store_releases r
                       JOIN store_packages p ON p.id = r.package_id
                       WHERE p.slug = ? COLLATE NOCASE AND r.yanked_at IS NULL"""
            params: list[Any] = [slug]
            if artifact_type:
                query += " AND r.artifact_type = ?"
                params.append(artifact_type)
            query += " ORDER BY r.published_at DESC LIMIT 1"
            return conn.execute(query, params).fetchone()

    def yank_store_release(self, release_id: str, reason: str = "") -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE store_releases SET yanked_at = ?, yanked_reason = ? WHERE id = ? AND yanked_at IS NULL",
                (utcnow_iso(), reason[:500], release_id),
            )
            return cur.rowcount == 1

    def store_event(
        self,
        action: str,
        *,
        package_id: str = "",
        release_id: str = "",
        agent_id: str = "",
        detail: Any = None,
        actor: str = "",
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO store_events (at, package_id, release_id, agent_id, action, detail, actor)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (utcnow_iso(), package_id, release_id, agent_id, action, dumps(detail or {}), actor),
            )

    def store_stats(self) -> dict[str, Any]:
        with self.connection() as conn:
            packages = conn.execute("SELECT COUNT(*) AS n FROM store_packages").fetchone()["n"]
            releases = conn.execute("SELECT COUNT(*) AS n FROM store_releases").fetchone()["n"]
            yanked = conn.execute("SELECT COUNT(*) AS n FROM store_releases WHERE yanked_at IS NOT NULL").fetchone()["n"]
        return {"packages": packages, "releases": releases, "yanked": yanked}

    # ---- Package inventory and desired state ---------------------------

    def replace_package_inventory(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        entries: list[dict[str, Any]],
        managers: list[dict[str, Any]],
    ) -> None:
        stamp = utcnow_iso()
        conn.execute("DELETE FROM package_inventory WHERE agent_id = ?", (agent_id,))
        for entry in entries[:5000]:
            conn.execute(
                """INSERT OR REPLACE INTO package_inventory
                   (agent_id, manager, package, version, arch, source, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    str(entry.get("manager", ""))[:32],
                    str(entry.get("package", ""))[:128],
                    str(entry.get("version", ""))[:128],
                    str(entry.get("arch", ""))[:32],
                    str(entry.get("source", ""))[:128],
                    stamp,
                ),
            )
        # Managers are stored as a synthetic row per manager for discovery.
        for manager in managers[:10]:
            name = str(manager.get("name", ""))[:32]
            if not name:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO package_inventory
                   (agent_id, manager, package, version, arch, source, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, name, "__manager__", str(manager.get("version", ""))[:128], "", "", stamp),
            )

    def list_package_inventory(self, agent_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM package_inventory WHERE agent_id = ? ORDER BY manager, package",
                    (agent_id,),
                ).fetchall()
            )

    def replace_package_rules(self, agent_id: str, rules: list[dict[str, Any]], actor: str) -> list[sqlite3.Row]:
        now = utcnow_iso()
        with self.transaction() as conn:
            conn.execute("DELETE FROM package_rules WHERE agent_id = ?", (agent_id,))
            for rule in rules[:200]:
                conn.execute(
                    """INSERT INTO package_rules
                       (id, agent_id, manager, package, desired_state, version_spec,
                        enabled, created_by, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (
                        "rule_" + secrets.token_hex(8),
                        agent_id,
                        str(rule["manager"]),
                        str(rule["package"]),
                        str(rule["desired_state"]),
                        str(rule.get("version_spec", ""))[:128],
                        actor,
                        now,
                        now,
                    ),
                )
            return list(
                conn.execute(
                    "SELECT * FROM package_rules WHERE agent_id = ? ORDER BY manager, package",
                    (agent_id,),
                ).fetchall()
            )

    def list_package_rules(self, agent_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM package_rules WHERE agent_id = ? ORDER BY manager, package",
                    (agent_id,),
                ).fetchall()
            )

    # ---- Agent profiles -------------------------------------------------

    def get_profile_by_id(self, profile_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()

    def get_profile_by_slug(self, slug: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM agent_profiles WHERE slug = ? COLLATE NOCASE", (slug,)).fetchone()

    def list_profiles(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(conn.execute("SELECT * FROM agent_profiles ORDER BY is_system DESC, name").fetchall())

    def create_profile(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        settings: dict[str, Any],
        actor: str,
    ) -> sqlite3.Row:
        profile_id = "prof_" + secrets.token_hex(8)
        now = utcnow_iso()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO agent_profiles
                   (id, slug, name, description, is_system, revision, settings, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, 1, ?, ?, ?)""",
                (profile_id, slug, name, description, dumps(settings), now, now),
            )
            row = conn.execute("SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()
        return row

    def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> sqlite3.Row | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE agent_profiles
                   SET name = ?, description = ?, settings = ?, revision = revision + 1, updated_at = ?
                   WHERE id = ?""",
                (
                    name if name is not None else row["name"],
                    description if description is not None else row["description"],
                    dumps(settings) if settings is not None else row["settings"],
                    utcnow_iso(),
                    profile_id,
                ),
            )
            return conn.execute("SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()

    def delete_profile(self, profile_id: str) -> bool:
        with self.transaction() as conn:
            row = conn.execute("SELECT is_system FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None or row["is_system"]:
                return False
            assigned = conn.execute(
                "SELECT COUNT(*) AS n FROM agents WHERE profile_id = ?", (profile_id,)
            ).fetchone()["n"]
            if assigned:
                raise ValueError("profile is assigned to agents")
            conn.execute("DELETE FROM agent_profiles WHERE id = ?", (profile_id,))
            return True

    def count_profile_agents(self, profile_id: str) -> int:
        with self.connection() as conn:
            return int(
                conn.execute("SELECT COUNT(*) AS n FROM agents WHERE profile_id = ?", (profile_id,)).fetchone()["n"]
            )

    def set_agent_profile(self, agent_id: str, profile_id: str) -> bool:
        with self.transaction() as conn:
            row = conn.execute("SELECT profile_id FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                return False
            cur = conn.execute("UPDATE agents SET profile_id = ? WHERE id = ?", (profile_id, agent_id))
            if row["profile_id"] != profile_id:
                self.record_agent_event(
                    agent_id,
                    "profile",
                    "info",
                    "Agent profile changed",
                    {"from": row["profile_id"], "to": profile_id},
                    conn=conn,
                )
            return cur.rowcount == 1

    def list_profile_tags(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(conn.execute("SELECT * FROM profile_tag_assignments ORDER BY tag").fetchall())

    def profile_for_tags(self, tags: list[str]) -> str | None:
        if not tags:
            return None
        normalized = {str(tag).casefold() for tag in tags}
        with self.connection() as conn:
            for row in conn.execute("SELECT tag, profile_id FROM profile_tag_assignments").fetchall():
                if str(row["tag"]).casefold() in normalized:
                    return row["profile_id"]
        return None

    def set_profile_tag(self, tag: str, profile_id: str) -> int:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO profile_tag_assignments (tag, profile_id, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tag) DO UPDATE SET profile_id = excluded.profile_id, updated_at = excluded.updated_at""",
                (tag, profile_id, utcnow_iso()),
            )
            changed = 0
            for row in conn.execute("SELECT id, tags, profile_id FROM agents").fetchall():
                tags = loads(row["tags"], [])
                if not isinstance(tags, list):
                    continue
                if any(str(item).casefold() == tag.casefold() for item in tags):
                    conn.execute("UPDATE agents SET profile_id = ? WHERE id = ?", (profile_id, row["id"]))
                    if row["profile_id"] != profile_id:
                        self.record_agent_event(
                            row["id"],
                            "profile",
                            "info",
                            "Agent profile changed",
                            {"from": row["profile_id"], "to": profile_id, "tag": tag},
                            conn=conn,
                        )
                    changed += 1
            return changed

    def delete_profile_tag(self, tag: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM profile_tag_assignments WHERE tag = ? COLLATE NOCASE", (tag,))
            return cur.rowcount == 1

    def apply_profile_policy(self, profile_id: str, policy: dict[str, Any]) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE agents SET policy = ? WHERE profile_id = ?",
                (dumps(policy), profile_id),
            )
            return int(cur.rowcount)

    # ---- Package inventory state ---------------------------------------

    def get_package_inventory_state(self, agent_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM package_inventory_state WHERE agent_id = ?", (agent_id,)
            ).fetchone()

    def set_package_inventory_state(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        *,
        hash_value: str,
        revision: int,
        full: bool = False,
    ) -> None:
        now = utcnow_iso()
        conn.execute(
            """INSERT INTO package_inventory_state (agent_id, hash, revision, last_full_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 hash = excluded.hash,
                 revision = excluded.revision,
                 last_full_at = COALESCE(excluded.last_full_at, package_inventory_state.last_full_at),
                 updated_at = excluded.updated_at""",
            (agent_id, hash_value, int(revision), now if full else None, now),
        )

    def apply_package_inventory_delta(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        upserts: list[dict[str, Any]],
        deletes: list[list[str]],
    ) -> None:
        now = utcnow_iso()
        for item in deletes[:5000]:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            conn.execute(
                "DELETE FROM package_inventory WHERE agent_id = ? AND manager = ? AND package = ?",
                (agent_id, str(item[0])[:32], str(item[1])[:128]),
            )
        for entry in upserts[:5000]:
            conn.execute(
                """INSERT OR REPLACE INTO package_inventory
                   (agent_id, manager, package, version, arch, source, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    str(entry.get("manager", ""))[:32],
                    str(entry.get("package", ""))[:128],
                    str(entry.get("version", ""))[:128],
                    str(entry.get("arch", ""))[:32],
                    str(entry.get("source", ""))[:128],
                    now,
                ),
            )

    # ---- Remote install jobs -------------------------------------------

    def create_remote_install_job(
        self,
        *,
        host: str,
        port: int,
        ssh_user: str,
        profile: str,
        name: str,
        watch_services: list[str],
        actor: str,
    ) -> sqlite3.Row:
        job_id = "ri_" + secrets.token_hex(8)
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO remote_install_jobs
                   (id, host, port, ssh_user, os, profile, name, watch_services, status, log, error, created_by, created_at)
                   VALUES (?, ?, ?, ?, 'linux', ?, ?, ?, 'queued', '', '', ?, ?)""",
                (
                    job_id,
                    host,
                    int(port),
                    ssh_user,
                    profile,
                    name,
                    dumps(watch_services),
                    actor,
                    utcnow_iso(),
                ),
            )
            return conn.execute("SELECT * FROM remote_install_jobs WHERE id = ?", (job_id,)).fetchone()

    def get_remote_install_job(self, job_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM remote_install_jobs WHERE id = ?", (job_id,)).fetchone()

    def list_remote_install_jobs(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM remote_install_jobs ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(int(limit), 200)),),
                ).fetchall()
            )

    def update_remote_install_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        log_append: str = "",
        error: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        with self.transaction() as conn:
            row = conn.execute("SELECT log, status FROM remote_install_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return
            log = str(row["log"] or "")
            if log_append:
                log = (log + ("\n" if log else "") + log_append)[-200_000:]
            now = utcnow_iso()
            conn.execute(
                """UPDATE remote_install_jobs
                   SET status = ?, log = ?, error = ?,
                       started_at = COALESCE(?, started_at),
                       finished_at = COALESCE(?, finished_at)
                   WHERE id = ?""",
                (
                    status if status is not None else row["status"],
                    log,
                    error if error is not None else "",
                    now if started else None,
                    now if finished else None,
                    job_id,
                ),
            )

    def running_remote_install_jobs(self) -> int:
        with self.connection() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM remote_install_jobs WHERE status IN ('queued', 'running')"
                ).fetchone()["n"]
            )

    # ---- Saved fleet views ---------------------------------------------

    def list_saved_views(self, user_id: int) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM saved_views WHERE user_id = ?
                       ORDER BY name COLLATE NOCASE""",
                    (user_id,),
                ).fetchall()
            )

    def get_saved_view(self, view_id: str, user_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM saved_views WHERE id = ? AND user_id = ?",
                (view_id, user_id),
            ).fetchone()

    def create_saved_view(self, user_id: int, name: str, filters: dict[str, Any]) -> sqlite3.Row:
        now = utcnow_iso()
        view_id = "sv_" + secrets.token_hex(8)
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO saved_views (id, user_id, name, filters, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (view_id, user_id, name, dumps(filters), now, now),
            )
            return conn.execute("SELECT * FROM saved_views WHERE id = ?", (view_id,)).fetchone()

    def delete_saved_view(self, view_id: str, user_id: int) -> bool:
        with self.connection() as conn:
            cur = conn.execute(
                "DELETE FROM saved_views WHERE id = ? AND user_id = ?",
                (view_id, user_id),
            )
            return bool(cur.rowcount)

    # ---- Docker inventory ----------------------------------------------

    @staticmethod
    def _docker_counts_from_rows(
        containers: list[dict[str, Any]],
        images: list[dict[str, Any]],
        volumes: list[dict[str, Any]],
        networks: list[dict[str, Any]],
    ) -> dict[str, int]:
        running = sum(1 for item in containers if str(item.get("state", "")).lower() == "running")
        paused = sum(1 for item in containers if str(item.get("state", "")).lower() == "paused")
        unhealthy = sum(1 for item in containers if str(item.get("health", "")).lower() == "unhealthy")
        return {
            "containers": len(containers),
            "running": running,
            "paused": paused,
            "stopped": max(0, len(containers) - running - paused),
            "unhealthy": unhealthy,
            "images": len(images),
            "volumes": len(volumes),
            "networks": len(networks),
        }

    def record_docker_summary(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        summary: dict[str, Any],
        *,
        at: str | None = None,
    ) -> None:
        """Apply the compact heartbeat summary without touching inventory rows."""
        heartbeat_at = at or utcnow_iso()
        reason = str(summary.get("reason") or "")
        available = bool(summary.get("available"))
        if reason in {"disabled", "unsupported_os"}:
            conn.execute("DELETE FROM docker_hosts WHERE agent_id = ?", (agent_id,))
            return
        existing = conn.execute(
            "SELECT agent_id FROM docker_hosts WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
        if existing is None:
            if not available:
                return
            conn.execute(
                """INSERT INTO docker_hosts
                   (agent_id, available, reason, engine_version, api_version, compose_version,
                    compose_available, rootless, storage_driver, cgroup_driver, docker_root_dir,
                    container_count, running_count, paused_count, stopped_count, unhealthy_count,
                    image_count, volume_count, network_count, inventory_hash, inventory_revision,
                    collected_at, heartbeat_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    int(available),
                    reason[:64],
                    str(summary.get("engine_version") or "")[:64],
                    str(summary.get("api_version") or "")[:64],
                    str(summary.get("compose_version") or "")[:64],
                    int(bool(summary.get("compose_available"))),
                    int(bool(summary.get("rootless"))),
                    str(summary.get("storage_driver") or "")[:64],
                    str(summary.get("cgroup_driver") or "")[:64],
                    str(summary.get("docker_root_dir") or "")[:255],
                    int(counts.get("containers") or 0),
                    int(counts.get("running") or 0),
                    int(counts.get("paused") or 0),
                    int(counts.get("stopped") or 0),
                    int(counts.get("unhealthy") or 0),
                    int(counts.get("images") or 0),
                    int(counts.get("volumes") or 0),
                    int(counts.get("networks") or 0),
                    str(summary.get("inventory_hash") or "")[:64],
                    int(summary.get("inventory_revision") or 0),
                    str(summary.get("collected_at") or "")[:64] or None,
                    heartbeat_at,
                ),
            )
            return
        if available:
            conn.execute(
                """UPDATE docker_hosts SET
                     available = 1, reason = ?, engine_version = ?, api_version = ?,
                     compose_version = ?, compose_available = ?, rootless = ?,
                     storage_driver = ?, cgroup_driver = ?, docker_root_dir = ?,
                     container_count = ?, running_count = ?, paused_count = ?,
                     stopped_count = ?, unhealthy_count = ?, image_count = ?,
                     volume_count = ?, network_count = ?, inventory_hash = ?,
                     inventory_revision = ?, collected_at = COALESCE(?, collected_at),
                     heartbeat_at = ?, last_error = ''
                   WHERE agent_id = ?""",
                (
                    reason[:64],
                    str(summary.get("engine_version") or "")[:64],
                    str(summary.get("api_version") or "")[:64],
                    str(summary.get("compose_version") or "")[:64],
                    int(bool(summary.get("compose_available"))),
                    int(bool(summary.get("rootless"))),
                    str(summary.get("storage_driver") or "")[:64],
                    str(summary.get("cgroup_driver") or "")[:64],
                    str(summary.get("docker_root_dir") or "")[:255],
                    int(counts.get("containers") or 0),
                    int(counts.get("running") or 0),
                    int(counts.get("paused") or 0),
                    int(counts.get("stopped") or 0),
                    int(counts.get("unhealthy") or 0),
                    int(counts.get("images") or 0),
                    int(counts.get("volumes") or 0),
                    int(counts.get("networks") or 0),
                    str(summary.get("inventory_hash") or "")[:64],
                    int(summary.get("inventory_revision") or 0),
                    str(summary.get("collected_at") or "")[:64] or None,
                    heartbeat_at,
                    agent_id,
                ),
            )
        else:
            conn.execute(
                """UPDATE docker_hosts
                   SET available = 0, reason = ?, heartbeat_at = ?, last_error = ''
                   WHERE agent_id = ?""",
                (reason[:64], heartbeat_at, agent_id),
            )

    def replace_docker_inventory(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        summary: dict[str, Any],
        containers: list[dict[str, Any]],
        images: list[dict[str, Any]],
        volumes: list[dict[str, Any]],
        networks: list[dict[str, Any]],
        *,
        collected_at: str,
        inventory_hash: str,
        revision: int,
        at: str | None = None,
    ) -> None:
        """Replace one host's current inventory atomically in the caller's transaction."""
        now = at or utcnow_iso()
        counts = self._docker_counts_from_rows(containers, images, volumes, networks)
        summary = dict(summary)
        summary.update(
            {
                "available": True,
                "counts": counts,
                "inventory_hash": inventory_hash,
                "inventory_revision": revision,
                "collected_at": collected_at,
            }
        )
        self.record_docker_summary(conn, agent_id, summary, at=now)
        conn.execute(
            """UPDATE docker_hosts SET
                 available = 1, container_count = ?, running_count = ?, paused_count = ?,
                 stopped_count = ?, unhealthy_count = ?, image_count = ?, volume_count = ?,
                 network_count = ?, inventory_hash = ?, inventory_revision = ?,
                 inventory_at = ?, collected_at = ?, heartbeat_at = ?, last_error = ''
               WHERE agent_id = ?""",
            (
                counts["containers"],
                counts["running"],
                counts["paused"],
                counts["stopped"],
                counts["unhealthy"],
                counts["images"],
                counts["volumes"],
                counts["networks"],
                inventory_hash[:64],
                int(revision),
                now,
                collected_at[:64],
                now,
                agent_id,
            ),
        )
        conn.execute("DELETE FROM docker_containers WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM docker_images WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM docker_volumes WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM docker_networks WHERE agent_id = ?", (agent_id,))
        if containers:
            conn.executemany(
                """INSERT INTO docker_containers
                   (agent_id, container_id, name, image, image_id, command, state, status,
                    health, created_at, started_at, finished_at, exit_code, restart_policy,
                    memory_limit, nano_cpus, pids_limit, ports, labels, mounts, networks,
                    compose_project, compose_service, compose_config_hash, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        agent_id,
                        str(item.get("id", ""))[:64],
                        str(item.get("name", ""))[:255],
                        str(item.get("image", ""))[:512],
                        str(item.get("image_id", ""))[:128],
                        str(item.get("command", ""))[:2048],
                        str(item.get("state", ""))[:32],
                        str(item.get("status", ""))[:255],
                        str(item.get("health", ""))[:32],
                        str(item.get("created_at", ""))[:64],
                        str(item.get("started_at", ""))[:64],
                        str(item.get("finished_at", ""))[:64],
                        item.get("exit_code"),
                        str(item.get("restart_policy", ""))[:64],
                        int(item.get("memory_limit") or 0),
                        int(item.get("nano_cpus") or 0),
                        item.get("pids_limit"),
                        dumps(item.get("ports") or []),
                        dumps(item.get("labels") or {}),
                        dumps(item.get("mounts") or []),
                        dumps(item.get("networks") or []),
                        str(item.get("compose_project", ""))[:255],
                        str(item.get("compose_service", ""))[:255],
                        str(item.get("compose_config_hash", ""))[:128],
                        now,
                    )
                    for item in containers
                ],
            )
        if images:
            conn.executemany(
                """INSERT INTO docker_images
                   (agent_id, image_id, repo_tags, repo_digests, size, shared_size,
                    container_count, dangling, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        agent_id,
                        str(item.get("id", ""))[:128],
                        dumps(item.get("repo_tags") or []),
                        dumps(item.get("repo_digests") or []),
                        int(item.get("size") or 0),
                        int(item.get("shared_size") or 0),
                        int(item.get("containers") or 0),
                        int(bool(item.get("dangling"))),
                        str(item.get("created_at", ""))[:64],
                        now,
                    )
                    for item in images
                ],
            )
        if volumes:
            conn.executemany(
                """INSERT INTO docker_volumes
                   (agent_id, name, driver, scope, mountpoint, labels, in_use, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        agent_id,
                        str(item.get("name", ""))[:255],
                        str(item.get("driver", ""))[:64],
                        str(item.get("scope", ""))[:32],
                        str(item.get("mountpoint", ""))[:1024],
                        dumps(item.get("labels") or {}),
                        int(bool(item.get("in_use"))),
                        now,
                    )
                    for item in volumes
                ],
            )
        if networks:
            conn.executemany(
                """INSERT INTO docker_networks
                   (agent_id, network_id, name, driver, scope, internal, attachable,
                    labels, container_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        agent_id,
                        str(item.get("id", ""))[:128],
                        str(item.get("name", ""))[:255],
                        str(item.get("driver", ""))[:64],
                        str(item.get("scope", ""))[:32],
                        int(bool(item.get("internal"))),
                        int(bool(item.get("attachable"))),
                        dumps(item.get("labels") or {}),
                        int(item.get("container_count") or 0),
                        now,
                    )
                    for item in networks
                ],
            )

    @staticmethod
    def _docker_host_select() -> str:
        return """SELECT d.*, a.name AS agent_name, a.hostname AS agent_hostname,
                         a.os AS agent_os, a.last_seen AS agent_last_seen
                  FROM docker_hosts d
                  JOIN agents a ON a.id = d.agent_id
                  WHERE d.reason NOT IN ('disabled', 'unsupported_os')
                    AND (d.available = 1 OR d.inventory_at IS NOT NULL)"""

    def list_docker_hosts(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    self._docker_host_select() + " ORDER BY a.name COLLATE NOCASE"
                ).fetchall()
            )

    def get_docker_host(self, agent_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                self._docker_host_select() + " AND d.agent_id = ?",
                (agent_id,),
            ).fetchone()

    def find_docker_container(self, agent_id: str, target: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM docker_containers WHERE agent_id = ? AND (container_id = ? OR container_id LIKE ?)",
                (agent_id, target, f"{target}%"),
            ).fetchall()
            return rows[0] if len(rows) == 1 else None

    def find_docker_image(self, agent_id: str, target: str) -> sqlite3.Row | None:
        prefix = target[len("sha256:") :] if target.startswith("sha256:") else target
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM docker_images
                   WHERE agent_id = ? AND (image_id = ? OR image_id = ? OR image_id LIKE ?)""",
                (agent_id, target, prefix, f"{prefix}%"),
            ).fetchall()
            return rows[0] if len(rows) == 1 else None

    def find_docker_volume(self, agent_id: str, name: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM docker_volumes WHERE agent_id = ? AND name = ?",
                (agent_id, name),
            ).fetchone()

    def find_docker_network(self, agent_id: str, target: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM docker_networks
                   WHERE agent_id = ? AND (network_id = ? OR network_id LIKE ?)""",
                (agent_id, target, f"{target}%"),
            ).fetchall()
            return rows[0] if len(rows) == 1 else None

    def list_docker_containers(self, agent_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM docker_containers WHERE agent_id = ? ORDER BY name COLLATE NOCASE",
                    (agent_id,),
                ).fetchall()
            )

    def list_docker_images(self, agent_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM docker_images WHERE agent_id = ? ORDER BY image_id",
                    (agent_id,),
                ).fetchall()
            )

    def list_docker_volumes(self, agent_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM docker_volumes WHERE agent_id = ? ORDER BY name COLLATE NOCASE",
                    (agent_id,),
                ).fetchall()
            )

    def list_docker_networks(self, agent_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM docker_networks WHERE agent_id = ? ORDER BY name COLLATE NOCASE",
                    (agent_id,),
                ).fetchall()
            )

    # ---- Docker Compose library ----------------------------------------

    def get_docker_compose_setting(self) -> str:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT compose_dir FROM docker_settings WHERE id = 'docker'"
            ).fetchone()
            return str(row["compose_dir"]) if row is not None else ""

    def set_docker_compose_setting(self, compose_dir: str, *, conn: sqlite3.Connection | None = None) -> None:
        sql = """INSERT INTO docker_settings (id, compose_dir, updated_at)
                 VALUES ('docker', ?, ?)
                 ON CONFLICT(id) DO UPDATE SET compose_dir = excluded.compose_dir,
                   updated_at = excluded.updated_at"""
        params = (compose_dir, utcnow_iso())
        if conn is not None:
            conn.execute(sql, params)
            return
        with self.connection() as own:
            own.execute(sql, params)

    def create_docker_stack(
        self,
        slug: str,
        name: str,
        description: str,
        current_revision_hash: str,
        actor: str,
    ) -> sqlite3.Row:
        now = utcnow_iso()
        stack_id = "stk_" + secrets.token_hex(10)
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO docker_stacks
                   (id, slug, name, description, current_revision_hash, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (stack_id, slug, name, description, current_revision_hash, actor, now, now),
            )
            return conn.execute("SELECT * FROM docker_stacks WHERE id = ?", (stack_id,)).fetchone()

    def get_docker_stack(self, value: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM docker_stacks WHERE id = ? OR slug = ? COLLATE NOCASE",
                (value, value),
            ).fetchone()

    def list_docker_stacks(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT s.*,
                         (SELECT COUNT(DISTINCT d.agent_id) FROM docker_deployments d
                          WHERE d.stack_id = s.id AND d.active = 1) AS deployed_host_count,
                         (SELECT COUNT(*) FROM docker_stack_revisions r
                          WHERE r.stack_id = s.id) AS revision_count
                       FROM docker_stacks s
                       WHERE s.archived_at IS NULL
                       ORDER BY s.name COLLATE NOCASE"""
                ).fetchall()
            )

    def update_docker_stack(
        self,
        stack_id: str,
        *,
        name: str,
        description: str,
        current_revision_hash: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """UPDATE docker_stacks
                   SET name = ?, description = ?, current_revision_hash = ?, updated_at = ?
                   WHERE id = ?""",
                (name, description, current_revision_hash, utcnow_iso(), stack_id),
            )

    def delete_docker_stack(self, stack_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM docker_stacks WHERE id = ?", (stack_id,))

    def add_docker_stack_revision(
        self,
        stack_id: str,
        revision_hash: str,
        files: list[dict[str, Any]],
        message: str,
        actor: str,
    ) -> sqlite3.Row:
        now = utcnow_iso()
        revision_id = "rev_" + secrets.token_hex(10)
        with self.connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO docker_stack_revisions
                   (id, stack_id, revision_hash, files, message, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (revision_id, stack_id, revision_hash, dumps(files), message[:2000], actor, now),
            )
            return conn.execute(
                "SELECT * FROM docker_stack_revisions WHERE stack_id = ? AND revision_hash = ?",
                (stack_id, revision_hash),
            ).fetchone()

    def list_docker_stack_revisions(self, stack_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM docker_stack_revisions
                       WHERE stack_id = ? ORDER BY created_at DESC, id DESC""",
                    (stack_id,),
                ).fetchall()
            )

    def get_docker_stack_revision(self, stack_id: str, revision_hash: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """SELECT * FROM docker_stack_revisions
                   WHERE stack_id = ? AND revision_hash = ?""",
                (stack_id, revision_hash),
            ).fetchone()

    def create_docker_deployment(
        self,
        *,
        stack_id: str,
        agent_id: str,
        revision_hash: str,
        project_name: str,
        action: str,
        command_id: str,
        actor: str,
        pull_before_up: bool = False,
        remove_volumes: bool = False,
        status: str = "queued",
    ) -> sqlite3.Row:
        now = utcnow_iso()
        deployment_id = "dep_" + secrets.token_hex(10)
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO docker_deployments
                   (id, stack_id, agent_id, revision_hash, project_name, action, status,
                    command_id, active, pull_before_up, remove_volumes, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                (
                    deployment_id,
                    stack_id,
                    agent_id,
                    revision_hash,
                    project_name,
                    action,
                    status,
                    command_id,
                    int(pull_before_up),
                    int(remove_volumes),
                    actor,
                    now,
                ),
            )
            return conn.execute(
                "SELECT * FROM docker_deployments WHERE id = ?", (deployment_id,)
            ).fetchone()

    def get_docker_deployment(self, deployment_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM docker_deployments WHERE id = ?", (deployment_id,)
            ).fetchone()

    def get_docker_deployment_by_command(self, command_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM docker_deployments WHERE command_id = ?", (command_id,)
            ).fetchone()

    def delete_docker_deployment(self, deployment_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM docker_deployments WHERE id = ?", (deployment_id,))

    def docker_project_in_use(
        self,
        *,
        agent_id: str,
        project_name: str,
        exclude_id: str = "",
    ) -> sqlite3.Row | None:
        params: list[Any] = [agent_id, project_name]
        sql = """SELECT * FROM docker_deployments
                 WHERE agent_id = ? AND project_name = ? AND action = 'up'
                   AND (active = 1 OR status IN ('queued', 'dispatched', 'running'))"""
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self.connection() as conn:
            return conn.execute(sql, tuple(params)).fetchone()

    def docker_stack_has_active_deployments(self, stack_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT 1 FROM docker_deployments
                   WHERE stack_id = ?
                     AND (active = 1 OR status IN ('queued', 'dispatched', 'running'))
                   LIMIT 1""",
                (stack_id,),
            ).fetchone()
            return row is not None

    def set_docker_deployment_command(self, deployment_id: str, command_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE docker_deployments SET command_id = ? WHERE id = ?",
                (command_id, deployment_id),
            )

    def list_docker_deployments(
        self,
        *,
        agent_id: str = "",
        stack_id: str = "",
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id:
            clauses.append("d.agent_id = ?")
            params.append(agent_id)
        if stack_id:
            clauses.append("d.stack_id = ?")
            params.append(stack_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 500))
        with self.connection() as conn:
            return list(
                conn.execute(
                    f"""SELECT d.*, s.name AS stack_name, s.slug AS stack_slug,
                               s.current_revision_hash AS stack_current_revision_hash,
                               a.name AS agent_name
                        FROM docker_deployments d
                        JOIN docker_stacks s ON s.id = d.stack_id
                        JOIN agents a ON a.id = d.agent_id
                        {where}
                        ORDER BY d.created_at DESC, d.id DESC LIMIT ?""",
                    (*params, limit),
                ).fetchall()
            )

    def update_docker_deployment(
        self,
        deployment_id: str,
        *,
        status: str,
        log: str = "",
        error: str = "",
        started: bool = False,
        finished: bool = False,
        active: bool | None = None,
    ) -> sqlite3.Row | None:
        now = utcnow_iso()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM docker_deployments WHERE id = ?", (deployment_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE docker_deployments SET
                     status = ?, log = ?, error = ?,
                     started_at = COALESCE(?, started_at),
                     finished_at = COALESCE(?, finished_at),
                     active = COALESCE(?, active)
                   WHERE id = ?""",
                (
                    status,
                    log[:200_000],
                    error[:4000],
                    now if started else None,
                    now if finished else None,
                    None if active is None else int(active),
                    deployment_id,
                ),
            )
            return conn.execute(
                "SELECT * FROM docker_deployments WHERE id = ?", (deployment_id,)
            ).fetchone()

    def active_docker_deployment(
        self,
        *,
        agent_id: str,
        project_name: str = "",
        stack_id: str = "",
    ) -> sqlite3.Row | None:
        clauses = ["agent_id = ?", "active = 1"]
        params: list[Any] = [agent_id]
        if project_name:
            clauses.append("project_name = ?")
            params.append(project_name)
        if stack_id:
            clauses.append("stack_id = ?")
            params.append(stack_id)
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM docker_deployments WHERE " + " AND ".join(clauses) +
                " ORDER BY created_at DESC, id DESC LIMIT 1",
                tuple(params),
            ).fetchone()

    def deactivate_docker_deployments(
        self,
        *,
        agent_id: str,
        stack_id: str = "",
        project_name: str = "",
        except_id: str = "",
    ) -> int:
        clauses = ["agent_id = ?", "active = 1"]
        params: list[Any] = [agent_id]
        if stack_id:
            clauses.append("stack_id = ?")
            params.append(stack_id)
        if project_name:
            clauses.append("project_name = ?")
            params.append(project_name)
        if except_id:
            clauses.append("id != ?")
            params.append(except_id)
        with self.connection() as conn:
            cur = conn.execute(
                "UPDATE docker_deployments SET active = 0 WHERE " + " AND ".join(clauses),
                tuple(params),
            )
            return int(cur.rowcount)

    def prune_docker_deployments(self, retention_days: int = 90) -> int:
        days = max(1, min(3650, int(retention_days)))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connection() as conn:
            cur = conn.execute(
                """DELETE FROM docker_deployments
                   WHERE active = 0
                     AND status IN ('succeeded', 'failed', 'cancelled')
                     AND COALESCE(finished_at, created_at) < ?""",
                (cutoff,),
            )
            return int(cur.rowcount)

    def reconcile_docker_deployment_hashes(
        self,
        agent_id: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if conn is None:
            with self.connection() as own:
                self.reconcile_docker_deployment_hashes(agent_id, conn=own)
            return
        deployments = conn.execute(
            """SELECT * FROM docker_deployments
               WHERE agent_id = ? AND active = 1 AND action = 'up'""",
            (agent_id,),
        ).fetchall()
        for deployment in deployments:
            rows = conn.execute(
                """SELECT DISTINCT compose_config_hash FROM docker_containers
                   WHERE agent_id = ? AND compose_project = ?
                     AND compose_config_hash != ''""",
                (agent_id, deployment["project_name"]),
            ).fetchall()
            hashes = {str(row["compose_config_hash"]) for row in rows}
            if not deployment["compose_config_hash"] and len(hashes) == 1:
                conn.execute(
                    "UPDATE docker_deployments SET compose_config_hash = ? WHERE id = ?",
                    (next(iter(hashes)), deployment["id"]),
                )

    def docker_deployment_config_drift(self, row: sqlite3.Row) -> bool:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT compose_config_hash FROM docker_containers
                   WHERE agent_id = ? AND compose_project = ?
                     AND compose_config_hash != ''""",
                (row["agent_id"], row["project_name"]),
            ).fetchall()
        hashes = {str(item["compose_config_hash"]) for item in rows}
        expected = str(row["compose_config_hash"] or "")
        if expected:
            return any(value != expected for value in hashes)
        return len(hashes) > 1

    def docker_deployment_revision_hashes(self, stack_id: str) -> set[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT revision_hash FROM docker_deployments WHERE stack_id = ?",
                (stack_id,),
            ).fetchall()
            return {str(row["revision_hash"]) for row in rows if row["revision_hash"]}

    # ---- Docker console metadata ---------------------------------------

    def create_console_session(
        self,
        session_id: str,
        *,
        agent_id: str,
        container_id: str,
        shell: str,
        user: str,
        tty: bool,
        created_by: str,
        client_ip: str = "",
    ) -> sqlite3.Row:
        now = utcnow_iso()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO docker_console_sessions
                   (id, agent_id, container_id, shell, user, tty, status, created_by,
                    created_at, last_seen_at, client_ip)
                   VALUES (?, ?, ?, ?, ?, ?, 'opening', ?, ?, ?, ?)""",
                (
                    session_id,
                    agent_id,
                    container_id,
                    shell,
                    user,
                    int(tty),
                    created_by,
                    now,
                    now,
                    client_ip[:128],
                ),
            )
            return conn.execute(
                "SELECT * FROM docker_console_sessions WHERE id = ?", (session_id,)
            ).fetchone()

    def get_console_session(self, session_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM docker_console_sessions WHERE id = ?", (session_id,)
            ).fetchone()

    def mark_console_seen(self, session_id: str, status: str | None = None) -> None:
        now = utcnow_iso()
        with self.connection() as conn:
            if status:
                conn.execute(
                    "UPDATE docker_console_sessions SET last_seen_at = ?, status = ? WHERE id = ?",
                    (now, status, session_id),
                )
            else:
                conn.execute(
                    "UPDATE docker_console_sessions SET last_seen_at = ? WHERE id = ?",
                    (now, session_id),
                )

    def close_console_session(
        self,
        session_id: str,
        *,
        status: str,
        exit_code: int | None = None,
        error: str = "",
    ) -> None:
        now = utcnow_iso()
        with self.connection() as conn:
            conn.execute(
                """UPDATE docker_console_sessions
                   SET status = ?, closed_at = ?, last_seen_at = ?, exit_code = ?, error = ?
                   WHERE id = ?""",
                (status, now, now, exit_code, error[:4000], session_id),
            )

    def close_open_console_sessions(self, *, status: str, error: str = "") -> int:
        now = utcnow_iso()
        with self.connection() as conn:
            cur = conn.execute(
                """UPDATE docker_console_sessions
                   SET status = ?, closed_at = ?, last_seen_at = ?, error = ?
                   WHERE status IN ('opening', 'open', 'closing')""",
                (status, now, now, error[:4000]),
            )
            return int(cur.rowcount)

    def close_console_sessions_for_user(self, username: str, *, status: str = "closed") -> int:
        now = utcnow_iso()
        with self.connection() as conn:
            cur = conn.execute(
                """UPDATE docker_console_sessions
                   SET status = ?, closed_at = ?, last_seen_at = ?, error = ?
                   WHERE created_by = ? AND status IN ('opening', 'open', 'closing')""",
                (status, now, now, "Operator session invalidated.", username),
            )
            return int(cur.rowcount)

    def list_open_console_sessions(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM docker_console_sessions
                       WHERE status IN ('opening', 'open', 'closing')
                       ORDER BY created_at"""
                ).fetchall()
            )

    def audit(
        self,
        actor: str,
        action: str,
        target: str = "",
        detail: Any = None,
        ip: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> None:
        sql = "INSERT INTO audit_log (at, actor, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)"
        params = (utcnow_iso(), actor, action, target, dumps(detail or {}), ip)
        if conn is not None:
            conn.execute(sql, params)
            return
        with self.connection() as own:
            own.execute(sql, params)

    # ---- Discovery ------------------------------------------------------

    def get_discovery_settings(self) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM discovery_settings WHERE id = 'discovery'"
            ).fetchone()

    def set_discovery_acknowledgement(
        self,
        actor: str,
        acknowledgement: str,
        *,
        at: str | None = None,
    ) -> sqlite3.Row:
        now = at or utcnow_iso()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO discovery_settings
                   (id, acknowledged_at, acknowledged_by, acknowledgement, updated_at)
                   VALUES ('discovery', ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     acknowledged_at = excluded.acknowledged_at,
                     acknowledged_by = excluded.acknowledged_by,
                     acknowledgement = excluded.acknowledgement,
                     updated_at = excluded.updated_at""",
                (now, actor[:255], acknowledgement[:500], now),
            )
            return conn.execute(
                "SELECT * FROM discovery_settings WHERE id = 'discovery'"
            ).fetchone()

    def clear_discovery_acknowledgement(self) -> None:
        with self.connection() as conn:
            conn.execute(
                """UPDATE discovery_settings
                   SET acknowledged_at = NULL, acknowledged_by = '',
                       acknowledgement = '', updated_at = ?
                   WHERE id = 'discovery'""",
                (utcnow_iso(),),
            )

    def create_discovery_scan(
        self,
        scope: list[str],
        methods: list[str],
        initiated_by: str,
        *,
        status: str = "queued",
        agent_id: str = "",
        source: str = "server",
        command_id: str = "",
    ) -> sqlite3.Row:
        scan_id = "dsc_" + secrets.token_hex(8)
        now = utcnow_iso()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO discovery_scans
                   (id, started_at, scope, methods, initiated_by, agent_id, source, command_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    now,
                    dumps(scope),
                    dumps(methods),
                    initiated_by[:255],
                    agent_id[:64],
                    source[:128],
                    command_id[:64],
                    status,
                ),
            )
            return conn.execute(
                "SELECT * FROM discovery_scans WHERE id = ?", (scan_id,)
            ).fetchone()

    def get_discovery_scan(self, scan_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM discovery_scans WHERE id = ?", (scan_id,)
            ).fetchone()

    def get_discovery_scan_by_command(self, command_id: str) -> sqlite3.Row | None:
        if not command_id:
            return None
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM discovery_scans WHERE command_id = ? LIMIT 1",
                (command_id,),
            ).fetchone()

    def active_agent_discovery_scan(self, agent_id: str) -> sqlite3.Row | None:
        if not agent_id:
            return None
        with self.connection() as conn:
            return conn.execute(
                """SELECT * FROM discovery_scans
                   WHERE agent_id = ? AND status IN ('queued', 'running')
                   ORDER BY started_at DESC LIMIT 1""",
                (agent_id,),
            ).fetchone()

    def list_discovery_scans(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[list[sqlite3.Row], int]:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        with self.connection() as conn:
            total = int(
                conn.execute("SELECT COUNT(*) AS n FROM discovery_scans").fetchone()["n"]
            )
            rows = conn.execute(
                """SELECT * FROM discovery_scans
                   ORDER BY started_at DESC, rowid DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return list(rows), total

    def active_discovery_scan(self) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """SELECT * FROM discovery_scans
                   WHERE status IN ('queued', 'running')
                   ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()

    def update_discovery_scan(
        self,
        scan_id: str,
        *,
        status: str | None = None,
        finished_at: str | None = None,
        hosts_found: int | None = None,
        error: str | None = None,
        progress: int | None = None,
        hosts_scanned: int | None = None,
        cancel_requested: bool | None = None,
        command_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        fields: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("status", status),
            ("finished_at", finished_at),
            ("hosts_found", hosts_found),
            ("error", error),
            ("progress", progress),
            ("hosts_scanned", hosts_scanned),
            ("cancel_requested", None if cancel_requested is None else int(cancel_requested)),
            ("command_id", None if command_id is None else command_id[:64]),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        if not fields:
            return
        params.append(scan_id)

        def apply(connection: sqlite3.Connection) -> None:
            connection.execute(
                f"UPDATE discovery_scans SET {', '.join(fields)} WHERE id = ?",
                params,
            )

        if conn is not None:
            apply(conn)
            return
        with self.connection() as own:
            apply(own)

    def request_discovery_scan_cancel(self, scan_id: str) -> bool:
        with self.connection() as conn:
            cur = conn.execute(
                """UPDATE discovery_scans SET cancel_requested = 1
                   WHERE id = ? AND status IN ('queued', 'running')""",
                (scan_id,),
            )
            return bool(cur.rowcount)

    def list_discovered_devices(
        self,
        *,
        state: str | None = None,
        segment: str | None = None,
        query: str | None = None,
        device_type: str | None = None,
        vendor: str | None = None,
        no_agent: bool = False,
        admin_ports: bool = False,
        exposure: bool = False,
        conflict: bool = False,
        mac: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if segment:
            clauses.append("segment = ?")
            params.append(segment)
        if device_type:
            clauses.append("device_type = ?")
            params.append(device_type)
        if vendor:
            clauses.append("vendor = ?")
            params.append(vendor)
        if mac:
            clauses.append("mac = ? COLLATE NOCASE")
            params.append(mac)
        if no_agent or exposure:
            clauses.append("(agent_id IS NULL OR agent_id = '')")
        if admin_ports or exposure:
            ports = ("22", "3389", "5900", "5985", "5986", "8006")
            patterns: list[str] = []
            for port in ports:
                patterns.extend(
                    [
                        f'%"{port}"%',
                        f"%[{port},%",
                        f"%,{port},%",
                        f"%,{port}]%",
                        f"%[{port}]%",
                    ]
                )
            clauses.append(
                "(" + " OR ".join("open_ports LIKE ?" for _ in patterns) + ")"
            )
            params.extend(patterns)
        if conflict:
            clauses.append(
                """(
                    (mac != '' AND mac IN (
                        SELECT mac FROM discovered_devices
                        WHERE mac != '' GROUP BY mac HAVING COUNT(DISTINCT ip) > 1
                    ))
                    OR (ip != '' AND ip IN (
                        SELECT ip FROM discovered_devices
                        WHERE ip != '' GROUP BY ip HAVING COUNT(DISTINCT mac) > 1
                    ))
                    OR (hostname != '' AND hostname IN (
                        SELECT hostname FROM discovered_devices
                        WHERE hostname != '' GROUP BY hostname
                        HAVING COUNT(DISTINCT mac) > 1
                    ))
                    OR id IN (
                        SELECT device_id FROM discovery_events
                        WHERE kind = 'identity_conflict'
                    )
                )"""
            )
        if query:
            pattern = f"%{query}%"
            clauses.append(
                """(ip LIKE ? OR mac LIKE ? OR hostname LIKE ? OR vendor LIKE ?
                    OR tags LIKE ? OR notes LIKE ?)"""
            )
            params.extend([pattern] * 6)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(100_000, int(limit)))
        offset = max(0, int(offset))
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM discovered_devices{where}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""SELECT * FROM discovered_devices{where}
                    ORDER BY last_seen DESC, first_seen DESC
                    LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        return list(rows), total

    def get_discovered_device(self, device_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM discovered_devices WHERE id = ?", (device_id,)
            ).fetchone()

    def get_discovered_device_by_mac(self, mac: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """SELECT * FROM discovered_devices
                   WHERE mac = ? COLLATE NOCASE AND mac != ''
                   ORDER BY last_seen DESC LIMIT 1""",
                (mac,),
            ).fetchone()

    @staticmethod
    def _discovery_conflict_ids(conn: sqlite3.Connection) -> set[str]:
        ids: set[str] = set()
        for row in conn.execute(
            """SELECT id FROM discovered_devices WHERE mac != '' AND mac IN (
                   SELECT mac FROM discovered_devices
                   WHERE mac != '' GROUP BY mac HAVING COUNT(DISTINCT ip) > 1
               )"""
        ).fetchall():
            ids.add(str(row["id"]))
        for row in conn.execute(
            """SELECT id FROM discovered_devices WHERE ip != '' AND ip IN (
                   SELECT ip FROM discovered_devices
                   WHERE ip != '' GROUP BY ip HAVING COUNT(DISTINCT mac) > 1
               )"""
        ).fetchall():
            ids.add(str(row["id"]))
        for row in conn.execute(
            """SELECT id FROM discovered_devices WHERE hostname != '' AND hostname IN (
                   SELECT hostname FROM discovered_devices
                   WHERE hostname != '' GROUP BY hostname
                   HAVING COUNT(DISTINCT mac) > 1
               )"""
        ).fetchall():
            ids.add(str(row["id"]))
        for row in conn.execute(
            """SELECT DISTINCT device_id FROM discovery_events
               WHERE kind = 'identity_conflict'"""
        ).fetchall():
            ids.add(str(row["device_id"]))
        return ids

    def discovery_conflict_ids(self) -> set[str]:
        with self.connection() as conn:
            return self._discovery_conflict_ids(conn)

    def discovery_summary(self) -> dict[str, Any]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT id, segment, ip, state, agent_id, open_ports,
                          device_type, vendor, wake_status
                   FROM discovered_devices"""
            ).fetchall()
            states = {
                str(row["state"]): int(row["n"])
                for row in conn.execute(
                    "SELECT state, COUNT(*) AS n FROM discovered_devices GROUP BY state"
                ).fetchall()
            }
            types = [
                {"type": row["device_type"], "count": int(row["n"])}
                for row in conn.execute(
                    """SELECT device_type, COUNT(*) AS n
                       FROM discovered_devices GROUP BY device_type
                       ORDER BY n DESC, device_type"""
                ).fetchall()
            ]
            vendors = [
                {"vendor": row["vendor"], "count": int(row["n"])}
                for row in conn.execute(
                    """SELECT vendor, COUNT(*) AS n FROM discovered_devices
                       WHERE vendor != '' GROUP BY vendor ORDER BY n DESC, vendor LIMIT 50"""
                ).fetchall()
            ]
            conflict_ids = self._discovery_conflict_ids(conn)
        total = len(rows)
        admin_ports = {"22", "3389", "5900", "5985", "5986", "8006"}
        segment_map: dict[str, dict[str, Any]] = {}
        exposure_count = 0
        managed_count = 0
        new_count = 0
        for row in rows:
            ports = set(str(port) for port in loads(row["open_ports"], []))
            has_admin = bool(ports & admin_ports)
            has_agent = bool(row["agent_id"])
            exposed = has_admin and not has_agent
            if has_agent:
                managed_count += 1
            if row["state"] == "new":
                new_count += 1
            if exposed:
                exposure_count += 1
            segment = str(row["segment"] or "")
            entry = segment_map.setdefault(
                segment,
                {
                    "segment": segment,
                    "count": 0,
                    "new": 0,
                    "managed": 0,
                    "unmanaged": 0,
                    "exposure": 0,
                    "gateway_hint": "",
                    "gateway_source": "",
                },
            )
            entry["count"] += 1
            if row["state"] == "new":
                entry["new"] += 1
            if has_agent:
                entry["managed"] += 1
            else:
                entry["unmanaged"] += 1
            if exposed:
                entry["exposure"] += 1
            if row["device_type"] == "router/switch" and not entry["gateway_hint"]:
                entry["gateway_hint"] = str(row["ip"] or "")
                entry["gateway_source"] = "router/switch device"
            if str(row["ip"] or "").endswith(".1") and not entry["gateway_hint"]:
                entry["gateway_hint"] = str(row["ip"] or "")
                entry["gateway_source"] = "lowest host"
        for entry in segment_map.values():
            if not entry["gateway_hint"] and entry["segment"]:
                try:
                    network = ipaddress.ip_network(entry["segment"], strict=False)
                    entry["gateway_hint"] = str(next(network.hosts()))
                    entry["gateway_source"] = "network first host (guess)"
                except (ValueError, StopIteration):
                    pass
        segments = sorted(segment_map.values(), key=lambda item: item["segment"])
        return {
            "total": total,
            "new": states.get("new", 0),
            "new_count": new_count,
            "states": {name: states.get(name, 0) for name in ("new", "known", "ignored", "promoted")},
            "types": types,
            "segments": segments,
            "vendors": vendors,
            "no_agent": total - managed_count,
            "managed": managed_count,
            "unmanaged": total - managed_count,
            "exposure": exposure_count,
            "conflicts": len(conflict_ids),
            "conflict_device_ids": sorted(conflict_ids)[:5000],
        }

    def update_discovered_device(
        self,
        device_id: str,
        *,
        tags: list[str] | None = None,
        notes: str | None = None,
        state: str | None = None,
        agent_id: str | None = None,
        wake_target_id: str | None = None,
        wake_status: str | None = None,
        wake_verified_at: str | None = None,
        last_seen: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> sqlite3.Row | None:
        fields: list[str] = []
        params: list[Any] = []
        if tags is not None:
            fields.append("tags = ?")
            params.append(dumps(list(dict.fromkeys(str(tag)[:64] for tag in tags))[:32]))
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes[:2000])
        if state is not None:
            fields.append("state = ?")
            params.append(state)
        if agent_id is not None:
            fields.append("agent_id = ?")
            params.append(agent_id[:64] or None)
        if wake_target_id is not None:
            fields.append("wake_target_id = ?")
            params.append(wake_target_id[:64] or None)
        if wake_status is not None:
            fields.append("wake_status = ?")
            params.append(wake_status[:32])
        if wake_verified_at is not None:
            fields.append("wake_verified_at = ?")
            params.append(wake_verified_at[:64] or None)
        if last_seen is not None:
            fields.append("last_seen = ?")
            params.append(last_seen[:64])
        if not fields:
            return self.get_discovered_device(device_id)
        params.append(device_id)

        def apply(connection: sqlite3.Connection) -> sqlite3.Row | None:
            connection.execute(
                f"UPDATE discovered_devices SET {', '.join(fields)} WHERE id = ?", params
            )
            return connection.execute(
                "SELECT * FROM discovered_devices WHERE id = ?", (device_id,)
            ).fetchone()

        if conn is not None:
            return apply(conn)
        with self.connection() as own:
            return apply(own)

    def delete_discovered_device(self, device_id: str) -> bool:
        with self.transaction() as conn:
            conn.execute("DELETE FROM discovery_events WHERE device_id = ?", (device_id,))
            cur = conn.execute("DELETE FROM discovered_devices WHERE id = ?", (device_id,))
            return bool(cur.rowcount)

    def discovery_device_events(self, device_id: str, limit: int = 200) -> list[sqlite3.Row]:
        limit = max(1, min(500, int(limit)))
        with self.connection() as conn:
            return list(
                conn.execute(
                    """SELECT * FROM discovery_events WHERE device_id = ?
                       ORDER BY at DESC, id DESC LIMIT ?""",
                    (device_id, limit),
                ).fetchall()
            )

    def record_discovery_event(
        self,
        device_id: str,
        kind: str,
        detail: Any = None,
        *,
        at: str | None = None,
        conn: sqlite3.Connection | None = None,
        dedupe: bool = True,
    ) -> int | None:
        encoded = dumps(detail or {})
        stamp = at or utcnow_iso()

        def apply(connection: sqlite3.Connection) -> int | None:
            if dedupe:
                existing = connection.execute(
                    """SELECT id FROM discovery_events
                       WHERE device_id = ? AND kind = ? AND detail = ?
                       ORDER BY id DESC LIMIT 1""",
                    (device_id, kind, encoded),
                ).fetchone()
                if existing is not None:
                    return None
            cur = connection.execute(
                """INSERT INTO discovery_events (at, device_id, kind, detail)
                   VALUES (?, ?, ?, ?)""",
                (stamp, device_id, kind, encoded),
            )
            return int(cur.lastrowid) if cur.lastrowid is not None else None

        if conn is not None:
            return apply(conn)
        with self.connection() as own:
            return apply(own)

    def record_discovery_event_once(
        self,
        device_id: str,
        kind: str,
        detail: Any = None,
        *,
        at: str | None = None,
    ) -> bool:
        return self.record_discovery_event(device_id, kind, detail, at=at) is not None

    def get_agent_by_mac(self, mac: str) -> sqlite3.Row | None:
        if not mac:
            return None
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM agents WHERE mac = ? COLLATE NOCASE LIMIT 1", (mac,)
            ).fetchone()

    def get_wake_target_by_mac(self, mac: str) -> sqlite3.Row | None:
        if not mac:
            return None
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM wake_targets WHERE mac = ? COLLATE NOCASE LIMIT 1", (mac,)
            ).fetchone()

    def get_wake_target(self, target_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM wake_targets WHERE id = ?", (target_id,)
            ).fetchone()

    def devices_for_wake_target(self, target_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM discovered_devices WHERE wake_target_id = ?",
                    (target_id,),
                ).fetchall()
            )

    def prune_discovery(self, retention_days: int = 90) -> tuple[int, int]:
        days = max(1, min(3650, int(retention_days)))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.transaction() as conn:
            scans = conn.execute(
                "DELETE FROM discovery_scans WHERE started_at < ?", (cutoff,)
            )
            events = conn.execute(
                "DELETE FROM discovery_events WHERE at < ?", (cutoff,)
            )
            return int(scans.rowcount), int(events.rowcount)
