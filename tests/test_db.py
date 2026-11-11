from __future__ import annotations

import sqlite3

from runner_control.db import Database


OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    role TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'local',
    session_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    os TEXT NOT NULL,
    os_version TEXT NOT NULL DEFAULT '',
    arch TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    mac TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL,
    agent_version TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    enrolled_at TEXT NOT NULL,
    last_seen TEXT,
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
INSERT INTO users (username, role, created_at) VALUES ('legacyadmin', 'admin', '2026-01-01T00:00:00Z');
INSERT INTO agents (id, name, hostname, os, token_hash, enrolled_at)
VALUES ('agt_legacy', 'legacy', 'legacy-box', 'linux', 'hash', '2026-01-01T00:00:00Z');
"""


def test_old_schema_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    database = Database(path)
    database.initialize()

    with database.connection() as migrated:
        user = migrated.execute("SELECT * FROM users WHERE username = 'legacyadmin'").fetchone()
        agent = migrated.execute("SELECT * FROM agents WHERE id = 'agt_legacy'").fetchone()
        user_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(users)")}
        agent_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(agents)")}
        session_table = migrated.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
        ).fetchone()
        telemetry_table = migrated.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'telemetry_samples'"
        ).fetchone()
        store_table = migrated.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'store_releases'"
        ).fetchone()
        version = migrated.execute("PRAGMA user_version").fetchone()[0]

    assert user is not None and user["is_active"] == 1
    assert agent is not None
    assert agent["machine_id"] == ""
    assert agent["watch_services"] == "[]"
    assert "allow_shell" in agent["policy"]
    assert "is_active" in user_columns
    assert {"machine_id", "watch_services", "policy"} <= agent_columns
    assert session_table is not None
    assert telemetry_table is not None
    assert store_table is not None
    assert version == 6
