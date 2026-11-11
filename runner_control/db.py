from __future__ import annotations

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
CREATE INDEX IF NOT EXISTS idx_commands_status_dispatched ON commands(status, dispatched_at);
CREATE INDEX IF NOT EXISTS idx_commands_status_created ON commands(status, created_at);
CREATE INDEX IF NOT EXISTS idx_command_output_command ON command_output(command_id, id);
CREATE INDEX IF NOT EXISTS idx_store_releases_package ON store_releases(package_id, published_at);
CREATE INDEX IF NOT EXISTS idx_store_events_at ON store_events(at);
CREATE INDEX IF NOT EXISTS idx_package_inventory_agent ON package_inventory(agent_id, manager);
CREATE INDEX IF NOT EXISTS idx_package_rules_agent ON package_rules(agent_id, manager);
CREATE INDEX IF NOT EXISTS idx_agent_profiles_slug ON agent_profiles(slug);
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
            },
            "collection": {
                "service_inventory_seconds": 21600,
                "package_inventory_seconds": 86400,
                "package_full_seconds": 604800,
            },
            "actions": {
                "allow_shell": True,
                "allow_power": True,
                "allow_service": True,
                "allow_packages": True,
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
            },
            "collection": {
                "service_inventory_seconds": 3600,
                "package_inventory_seconds": 21600,
                "package_full_seconds": 604800,
            },
            "actions": {
                "allow_shell": True,
                "allow_power": True,
                "allow_service": True,
                "allow_packages": True,
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
            conn.executescript(INDEXES)
            conn.execute("PRAGMA user_version=6")
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

        state_columns = columns("package_inventory_state")
        if state_columns and "revision" not in state_columns:
            conn.execute("ALTER TABLE package_inventory_state ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")

        remote_columns = columns("remote_install_jobs")
        if remote_columns and "watch_services" not in remote_columns:
            conn.execute("ALTER TABLE remote_install_jobs ADD COLUMN watch_services TEXT NOT NULL DEFAULT '[]'")

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

    def telemetry_history(self, agent_id: str, hours: int, points: int) -> list[sqlite3.Row]:
        """Return a time-bucketed series bounded by ``points`` rows.

        Buckets are computed in SQL so an agent with years of samples never
        materialises an unbounded result set in Python.
        """
        span = max(1, int(hours)) * 3600
        start = utcnow() - timedelta(seconds=span)
        start_epoch = int(start.timestamp())
        cutoff = start.isoformat(timespec="seconds").replace("+00:00", "Z")
        points = max(10, min(1000, int(points)))
        with self.connection() as conn:
            try:
                rows = conn.execute(
                    """SELECT
                           CAST(((CAST(strftime('%s', at) AS INTEGER) - ?) * ?) / ? AS INTEGER) AS bucket,
                           MIN(at) AS at,
                           AVG(cpu_pct) AS cpu_pct,
                           AVG(mem_pct) AS mem_pct,
                           AVG(disk_max_pct) AS disk_max_pct,
                           AVG(gpu_max_util) AS gpu_max_util,
                           AVG(gpu_max_temp) AS gpu_max_temp,
                           AVG(service_down) AS service_down
                       FROM telemetry_samples
                       WHERE agent_id = ? AND at >= ?
                       GROUP BY bucket
                       ORDER BY bucket ASC
                       LIMIT ?""",
                    (start_epoch, points, span, agent_id, cutoff, points),
                ).fetchall()
            except sqlite3.OperationalError:
                # An older database may not have the table yet.
                return []
            return list(rows)

    def prune_telemetry(self, retention_days: int = 7) -> int:
        days = max(1, min(365, int(retention_days)))
        cutoff = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.connection() as conn:
            try:
                cur = conn.execute("DELETE FROM telemetry_samples WHERE at < ?", (cutoff,))
            except sqlite3.OperationalError:
                return 0
            return int(cur.rowcount)

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

    def set_alert_ack(
        self,
        agent_id: str,
        alert_key: str,
        actor: str,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        sql = """INSERT INTO alert_states (agent_id, alert_key, acknowledged_at, acknowledged_by)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(agent_id, alert_key) DO UPDATE SET
                   acknowledged_at = excluded.acknowledged_at,
                   acknowledged_by = excluded.acknowledged_by"""
        params = (agent_id, alert_key, utcnow_iso(), actor)
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
        snoozed_until = (
            utcnow() + timedelta(minutes=int(minutes))
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        sql = """INSERT INTO alert_states (agent_id, alert_key, snoozed_until)
                 VALUES (?, ?, ?)
                 ON CONFLICT(agent_id, alert_key) DO UPDATE SET
                   snoozed_until = excluded.snoozed_until"""
        params = (agent_id, alert_key, snoozed_until)
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
        sql = "DELETE FROM alert_states WHERE agent_id = ? AND alert_key = ?"
        params = (agent_id, alert_key)
        if conn is not None:
            return conn.execute(sql, params).rowcount == 1
        with self.connection() as own:
            return own.execute(sql, params).rowcount == 1

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
            cur = conn.execute("UPDATE agents SET profile_id = ? WHERE id = ?", (profile_id, agent_id))
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
            for row in conn.execute("SELECT id, tags FROM agents").fetchall():
                tags = loads(row["tags"], [])
                if not isinstance(tags, list):
                    continue
                if any(str(item).casefold() == tag.casefold() for item in tags):
                    conn.execute("UPDATE agents SET profile_id = ? WHERE id = ?", (profile_id, row["id"]))
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
