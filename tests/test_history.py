from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from sextant_control.db import Database, parse_iso
from sextant_control.server import create_app
from tests.helpers import enroll_agent, headers, heartbeat, login, sample_metrics


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_sample(database: Database, agent_id: str, at: str, **values) -> None:
    with database.transaction() as conn:
        database.insert_telemetry_sample(conn, agent_id, at, **values)


def test_heartbeat_records_derived_telemetry_sample(client, admin, app):
    agent = enroll_agent(admin, watch_services=["svc-a"])
    payload = sample_metrics(
        disks=[
            {"mount": "/", "total": 100, "used": 40, "free": 60, "percent": 40, "fs": "ext4"},
            {"mount": "/data", "total": 100, "used": 95, "free": 5, "percent": 95, "fs": "ext4"},
        ],
        gpus=[
            {"name": "GPU A", "util_pct": 20.0, "mem_used": 1, "mem_total": 2, "temp_c": 50.0},
            {"name": "GPU B", "util_pct": 97.0, "mem_used": 1, "mem_total": 2, "temp_c": 71.0},
        ],
        services=[
            {"name": "svc-a", "display_name": "A", "status": "stopped", "startup": "enabled", "watched": True},
            {"name": "svc-b", "display_name": "B", "status": "running", "startup": "enabled", "watched": True},
            {"name": "svc-c", "display_name": "C", "status": "stopped", "startup": "disabled", "watched": False},
        ],
    )
    heartbeat(admin, agent, payload)

    with app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM telemetry_samples WHERE agent_id = ?",
            (agent["agent_id"],),
        ).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["cpu_pct"] == 22.5
    assert row["mem_pct"] == 37.5
    assert row["mem_used"] == 6 * 1024**3
    assert row["mem_total"] == 16 * 1024**3
    assert row["uptime_seconds"] == 7200
    assert row["disk_max_pct"] == 95.0
    assert row["gpu_max_util"] == 97.0
    assert row["gpu_max_temp"] == 71.0
    assert row["service_down"] == 1


def test_history_endpoint_returns_ascending_series(client, admin, app):
    agent = enroll_agent(admin)
    now = datetime.now(timezone.utc)
    for hours_ago, cpu in ((3, 10.0), (2, 20.0), (1, 30.0)):
        _insert_sample(
            app.state.db,
            agent["agent_id"],
            _iso(now - timedelta(hours=hours_ago)),
            cpu_pct=cpu,
            mem_pct=15.0,
            disk_max_pct=25.0,
            gpu_max_util=5.0,
            gpu_max_temp=35.0,
            service_down=0,
        )

    response = client.get(f"/api/agents/{agent['agent_id']}/history?hours=24&points=200")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["agent_id"] == agent["agent_id"]
    assert data["hours"] == 24
    assert len(data["points"]) == 200
    samples = [point for point in data["points"] if point["cpu_pct"] is not None]
    assert [point["cpu_pct"] for point in samples] == [10.0, 20.0, 30.0]
    assert [point["at"] for point in data["points"]] == sorted(point["at"] for point in data["points"])
    assert set(data["points"][0]) == {
        "at",
        "cpu_pct",
        "mem_pct",
        "disk_max_pct",
        "gpu_max_util",
        "gpu_max_temp",
        "service_down",
    }


def test_history_returns_null_buckets_for_offline_interval(client, admin, app):
    agent = enroll_agent(admin)
    now = datetime.now(timezone.utc)
    _insert_sample(
        app.state.db,
        agent["agent_id"],
        _iso(now - timedelta(hours=5)),
        cpu_pct=10.0,
        mem_pct=20.0,
    )
    _insert_sample(
        app.state.db,
        agent["agent_id"],
        _iso(now - timedelta(hours=1)),
        cpu_pct=30.0,
        mem_pct=40.0,
    )

    data = client.get(
        f"/api/agents/{agent['agent_id']}/history?hours=6&points=60"
    ).json()
    points = data["points"]
    assert len(points) == 60
    timestamps = [parse_iso(point["at"]) for point in points]
    assert all(value is not None for value in timestamps)
    assert timestamps == sorted(timestamps)
    assert any(point["cpu_pct"] is None for point in points)
    gap = next(
        point
        for point in points
        if point["cpu_pct"] is None
        and parse_iso(point["at"]) > now - timedelta(hours=4)
        and parse_iso(point["at"]) < now - timedelta(hours=2)
    )
    assert gap["mem_pct"] is None
    assert gap["service_down"] is None
    assert [point["cpu_pct"] for point in points if point["cpu_pct"] is not None] == [10.0, 30.0]


def test_history_requires_auth_and_allows_viewer(client, admin, app):
    agent = enroll_agent(admin)
    _insert_sample(app.state.db, agent["agent_id"], _iso(datetime.now(timezone.utc)), cpu_pct=1.0)
    path = f"/api/agents/{agent['agent_id']}/history"

    with TestClient(app) as anonymous_client:
        assert anonymous_client.get(path).status_code == 401

    created = client.post(
        "/api/users",
        json={
            "username": "historyviewer",
            "display_name": "History Viewer",
            "role": "viewer",
            "password": "viewer password 123",
        },
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text

    with TestClient(app) as viewer_client:
        login(viewer_client, "historyviewer", "viewer password 123")
        response = viewer_client.get(path)
        assert response.status_code == 200, response.text
        points = response.json()["points"]
        assert len(points) == 200
        assert any(point["cpu_pct"] == 1.0 for point in points)


def test_history_rejects_unknown_agent_and_out_of_range(client, admin):
    response = client.get("/api/agents/agt_missing/history")
    assert response.status_code == 404

    for query in ("hours=0", "hours=721", "points=9", "points=1001"):
        response = client.get(f"/api/agents/agt_missing/history?{query}")
        assert response.status_code == 400, (query, response.text)


def test_history_downsampling_respects_points(client, admin, app):
    agent = enroll_agent(admin)
    now = datetime.now(timezone.utc)
    total = 100
    start = now - timedelta(hours=23)
    step = timedelta(hours=23) / (total - 1)
    for index in range(total):
        _insert_sample(
            app.state.db,
            agent["agent_id"],
            _iso(start + step * index),
            cpu_pct=float(index),
            mem_pct=50.0,
            disk_max_pct=50.0,
            gpu_max_util=10.0,
            gpu_max_temp=40.0,
            service_down=0,
        )

    response = client.get(f"/api/agents/{agent['agent_id']}/history?hours=24&points=10")
    assert response.status_code == 200, response.text
    points = response.json()["points"]
    assert 1 <= len(points) <= 10
    assert len(points) == 10
    assert [point["at"] for point in points] == sorted(point["at"] for point in points)


def test_telemetry_retention_prunes_old_rows(client, admin, app):
    agent = enroll_agent(admin)
    now = datetime.now(timezone.utc)
    old_at = _iso(now - timedelta(days=10))
    recent_at = _iso(now - timedelta(hours=1))
    _insert_sample(app.state.db, agent["agent_id"], old_at, cpu_pct=1.0)
    _insert_sample(app.state.db, agent["agent_id"], recent_at, cpu_pct=2.0)

    removed = app.state.db.prune_telemetry(7)
    assert removed == 1

    with app.state.db.connection() as conn:
        remaining = conn.execute(
            "SELECT at FROM telemetry_samples WHERE agent_id = ? ORDER BY at",
            (agent["agent_id"],),
        ).fetchall()
    assert [row["at"] for row in remaining] == [recent_at]


def test_history_disabled_skips_inserts(settings, tmp_path):
    disabled = replace(settings, telemetry_history=False, data_dir=tmp_path / "history-disabled")
    app = create_app(disabled)
    with TestClient(app) as client:
        admin = login(client)
        agent = enroll_agent(admin)
        heartbeat(admin, agent)
        with app.state.db.connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM telemetry_samples").fetchone()["n"]
    assert count == 0


def test_old_database_without_history_table_migrates(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            hostname TEXT NOT NULL,
            os TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            enrolled_at TEXT NOT NULL,
            last_seen TEXT
        );
        INSERT INTO agents (id, name, hostname, os, token_hash, enrolled_at)
        VALUES ('agt_legacy', 'legacy', 'legacy-box', 'linux', 'hash', '2026-01-01T00:00:00Z');
        PRAGMA user_version=2;
        """
    )
    conn.commit()
    conn.close()

    database = Database(path)
    database.initialize()
    database.initialize()

    with database.connection() as migrated:
        table = migrated.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'telemetry_samples'"
        ).fetchone()
        index = migrated.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_telemetry_agent_at'"
        ).fetchone()
        version = migrated.execute("PRAGMA user_version").fetchone()[0]

    assert table is not None
    assert index is not None
    assert version == 16


def test_history_without_table_returns_empty_list(client, admin, app):
    agent = enroll_agent(admin)
    with app.state.db.connection() as conn:
        conn.execute("DROP TABLE telemetry_samples")

    response = client.get(f"/api/agents/{agent['agent_id']}/history")
    assert response.status_code == 200, response.text
    assert response.json()["points"] == []
