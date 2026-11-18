from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from sextant_control import selfupdate, updates
from sextant_control.db import loads, utcnow_iso
from sextant_control.server import _rollout_sweep, advance_rollout, create_app
from tests.helpers import headers, enroll_agent, heartbeat, login, sample_metrics


def _feed_release(version: str) -> dict:
    data = b"wheel"
    return {
        "version": version,
        "url": f"https://example.test/releases/{version}",
        "notes": "",
        "published_at": "",
        "assets": [
            {
                "name": f"sextant-{version}-py3-none-any.whl",
                "url": f"https://example.test/sextant-{version}-py3-none-any.whl",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        ],
    }


def _feed_app(settings, monkeypatch, versions=("0.9.1",)):
    feed = [_feed_release(v) for v in versions]
    monkeypatch.setattr(updates, "list_releases", lambda feed_url, limit=30: feed)

    def fake_download(url, destination, **kwargs):
        data = b"wheel"
        Path(destination).write_bytes(data)
        return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    monkeypatch.setattr(selfupdate, "download_artifact", fake_download)
    app = create_app(replace(settings, update_feed="github:owner/repo"))
    with TestClient(app) as client:
        admin = login(client)
        client.post("/api/agent-updates/prepare", headers=headers(admin))
        return client, admin


def _enroll(client, admin, count=1, profile="workstation"):
    agents = []
    for i in range(1, count + 1):
        agent = enroll_agent(admin, hostname=f"host-{i}.example", profile=profile)
        agents.append(agent)
    return agents


def test_agent_update_profiles_endpoint(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    response = client.get("/api/agent-updates/profiles", headers=headers(admin))
    assert response.status_code == 200, response.text
    profiles = response.json()["profiles"]
    assert any(p["profile_id"] == "prof_workstation" for p in profiles)
    for profile in profiles:
        assert profile["agent_count"] == 0
        assert profile["deployed_version"] == ""


def test_create_rollout_assigns_agents_and_directs_first_batch(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    _enroll(client, admin, count=3)

    rollout = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "0.9.1", "batch_size": 2},
        headers=headers(admin),
    )
    assert rollout.status_code == 200, rollout.text
    body = rollout.json()
    assert body["status"] == "active"
    assert body["batch_size"] == 2

    detail = client.get(f"/api/agent-updates/rollouts/{body['id']}", headers=headers(admin))
    assert detail.status_code == 200, detail.text
    agents = detail.json()["agents"]
    assert len(agents) == 3
    summary = detail.json()["summary"]
    assert summary["total"] == 3
    by_pos = {a["position"]: a for a in agents}
    assert by_pos[0]["status"] == "directed"
    assert by_pos[1]["status"] == "directed"
    assert by_pos[2]["status"] == "pending"
    assert summary["directed"] == 2
    assert summary["pending"] == 1


def test_get_rollout_requires_admin(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "0.9.1"},
        headers=headers(admin),
    )
    client.post(
        "/api/users",
        json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    vsession = login(client, "viewer1", "viewer password 123")
    rollouts = client.get("/api/agent-updates/rollouts", headers=headers(vsession))
    assert rollouts.status_code == 403


def test_report_success_updates_version_and_advances_rollout(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    agent = _enroll(client, admin, count=1)[0]

    rollout = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "0.9.1"},
        headers=headers(admin),
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = rollout.json()["id"]

    result = client.post(
        f"/api/agent/{agent['agent_id']}/update/result",
        json={"status": "success", "version": "0.9.1"},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "success"

    agent_row = client.get(f"/api/agents/{agent['agent_id']}", headers=headers(admin))
    assert agent_row.status_code == 200, agent_row.text
    assert agent_row.json()["agent"]["agent_version"] == "0.9.1"

    detail = client.get(f"/api/agent-updates/rollouts/{rollout_id}", headers=headers(admin))
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "completed"
    assert detail.json()["summary"]["succeeded"] == 1


def test_heartbeat_from_target_version_completes_directed_rollout(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    agent = _enroll(client, admin, count=1)[0]

    rollout = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "0.9.1"},
        headers=headers(admin),
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = rollout.json()["id"]

    # The directed agent is still on the old version, so it keeps receiving
    # the directed update payload.
    old_beat = heartbeat(admin, agent, sample_metrics(agent_version="0.9.0"))
    assert old_beat["agent_update"]["directed"] is True

    # Self-update restarts the agent before it can post /update/result. The
    # first heartbeat from the target version should still settle the rollout.
    beat = heartbeat(admin, agent, sample_metrics(agent_version="0.9.1"))
    assert beat["agent_update"]["available"] is False

    detail = client.get(f"/api/agent-updates/rollouts/{rollout_id}", headers=headers(admin))
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "completed"
    assert detail.json()["summary"]["succeeded"] == 1
    assert detail.json()["agents"][0]["status"] == "succeeded"


def test_report_failure_marks_agent_failed(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    agent = _enroll(client, admin, count=1)[0]

    rollout = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "0.9.1"},
        headers=headers(admin),
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = rollout.json()["id"]

    result = client.post(
        f"/api/agent/{agent['agent_id']}/update/result",
        json={"status": "failure", "error": "download failed"},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["error"] == "download failed"

    detail = client.get(f"/api/agent-updates/rollouts/{rollout_id}", headers=headers(admin))
    assert detail.status_code == 200, detail.text
    assert detail.json()["summary"]["failed"] == 1
    agent_row = detail.json()["agents"][0]
    assert agent_row["status"] == "failed"
    assert agent_row["last_error"] == "download failed"


def test_retry_failed_agents_resets_to_pending(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    agents = _enroll(client, admin, count=2)

    rollout = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "0.9.1", "batch_size": 2},
        headers=headers(admin),
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = rollout.json()["id"]

    # Mark one agent as failed directly in the DB (the other stays directed,
    # so the rollout remains active rather than auto-completing).
    with client.app.state.db.transaction() as conn:
        conn.execute(
            "UPDATE agent_rollout_agents SET status = 'failed', completed_at = ?, last_error = 'boom' WHERE rollout_id = ? AND agent_id = ?",
            (utcnow_iso(), rollout_id, agents[0]["agent_id"]),
        )

    retry = client.post(
        f"/api/agent-updates/rollouts/{rollout_id}/retry", headers=headers(admin)
    )
    assert retry.status_code == 200, retry.text

    detail = client.get(f"/api/agent-updates/rollouts/{rollout_id}", headers=headers(admin))
    assert detail.status_code == 200, detail.text
    by_id = {a["agent_id"]: a for a in detail.json()["agents"]}
    # Retry resets failed agents to pending; the other agent (still directed)
    # keeps the batch unsettled so the reset agent is not re-directed.
    assert by_id[agents[0]["agent_id"]]["status"] == "pending"
    assert by_id[agents[0]["agent_id"]]["last_error"] == ""
    assert by_id[agents[1]["agent_id"]]["status"] == "directed"


def test_pause_and_complete_rollout(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    _enroll(client, admin, count=1)
    rollout = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "0.9.1"},
        headers=headers(admin),
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = rollout.json()["id"]

    paused = client.post(
        f"/api/agent-updates/rollouts/{rollout_id}/status",
        json={"status": "paused"},
        headers=headers(admin),
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    completed = client.post(
        f"/api/agent-updates/rollouts/{rollout_id}/status",
        json={"status": "completed"},
        headers=headers(admin),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    denied = client.post(
        f"/api/agent-updates/rollouts/{rollout_id}/status",
        json={"status": "paused"},
        headers=headers(admin),
    )
    assert denied.status_code == 409


def test_rollback_creates_command(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    agent = _enroll(client, admin, count=1)[0]

    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE agents SET agent_version = '0.9.1' WHERE id = ?", (agent["agent_id"],))

    rollback = client.post(
        f"/api/agent-updates/rollback/{agent['agent_id']}", headers=headers(admin)
    )
    assert rollback.status_code == 200, rollback.text
    command_id = rollback.json()["command_id"]

    with client.app.state.db.connection() as conn:
        cmd = conn.execute("SELECT kind, args FROM commands WHERE id = ?", (command_id,)).fetchone()
    assert cmd is not None
    assert cmd["kind"] == "rollback"
    assert loads(cmd["args"], {})["reason"] == "manual rollback"


def test_rollback_requires_admin(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    client.post(
        "/api/users",
        json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    vsession = login(client, "viewer1", "viewer password 123")
    denied = client.post("/api/agent-updates/rollback/agent-x", headers=headers(vsession))
    assert denied.status_code == 403


def test_create_rollout_requires_available_version(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    missing = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof_workstation", "target_version": "9.9.9"},
        headers=headers(admin),
    )
    assert missing.status_code == 404


def test_create_rollout_requires_profile(settings, monkeypatch):
    client, admin = _feed_app(settings, monkeypatch)
    missing = client.post(
        "/api/agent-updates/rollout",
        json={"profile_id": "prof-does-not-exist", "target_version": "0.9.1"},
        headers=headers(admin),
    )
    assert missing.status_code == 404


def test_advance_rollout_completes_when_all_settled(settings, db):
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents (id, name, hostname, machine_id, os, token_hash, agent_version, profile_id, enrolled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("agent-e", "agent e", "host-e.example", "machine-e", "linux", "hash", "0.8.0", "prof_workstation", now),
        )
        conn.execute(
            """INSERT INTO agent_rollouts (id, profile_id, target_version, batch_size, validation, validation_seconds, status, created_at, updated_at)
               VALUES ('rollout-test', 'prof_workstation', '0.9.1', 1, 0, 300, 'active', ?, ?)""",
            (now, now),
        )
        conn.execute(
            "INSERT INTO agent_rollout_agents (rollout_id, agent_id, position, status) VALUES ('rollout-test', 'agent-e', 0, 'pending')",
        )
        conn.execute(
            "UPDATE agent_rollout_agents SET status = 'succeeded', completed_at = ? WHERE agent_id = 'agent-e'",
            (now,),
        )

    advance_rollout("rollout-test", db, settings)
    rollout = db.get_agent_rollout("rollout-test")
    assert rollout["status"] == "completed"


def test_advance_rollout_directs_next_batch(settings, db):
    now = utcnow_iso()
    with db.transaction() as conn:
        for i in range(1, 5):
            conn.execute(
                "INSERT INTO agents (id, name, hostname, machine_id, os, token_hash, agent_version, profile_id, enrolled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"agent-f{i}",
                    f"agent f{i}",
                    f"host-f{i}.example",
                    f"machine-f{i}",
                    "linux",
                    "hash",
                    "0.8.0",
                    "prof_workstation",
                    now,
                ),
            )
        conn.execute(
            """INSERT INTO agent_rollouts (id, profile_id, target_version, batch_size, validation, validation_seconds, status, created_at, updated_at)
               VALUES ('rollout-f', 'prof_workstation', '0.9.1', 2, 0, 300, 'active', ?, ?)""",
            (now, now),
        )
        for i in range(1, 5):
            conn.execute(
                "INSERT INTO agent_rollout_agents (rollout_id, agent_id, position, status) VALUES ('rollout-f', ?, ?, 'pending')",
                (f"agent-f{i}", i - 1),
            )
        conn.execute(
            "UPDATE agent_rollout_agents SET status = 'succeeded', completed_at = ? WHERE agent_id = 'agent-f1'",
            (now,),
        )

    advance_rollout("rollout-f", db, settings)
    agents = db.get_rollout_agents("rollout-f")
    by_id = {a["agent_id"]: a for a in agents}
    assert by_id["agent-f1"]["status"] == "succeeded"
    assert by_id["agent-f2"]["status"] == "directed"
    assert by_id["agent-f3"]["status"] == "pending"
    assert by_id["agent-f4"]["status"] == "pending"


def test_advance_rollout_respects_validation_window(settings, db):
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents (id, name, hostname, machine_id, os, token_hash, agent_version, profile_id, enrolled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("agent-g", "agent g", "host-g.example", "machine-g", "linux", "hash", "0.8.0", "prof_workstation", now),
        )
        conn.execute(
            """INSERT INTO agent_rollouts (id, profile_id, target_version, batch_size, validation, validation_seconds, status, created_at, updated_at)
               VALUES ('rollout-g', 'prof_workstation', '0.9.1', 1, 1, 3600, 'active', ?, ?)""",
            (now, now),
        )
        conn.execute(
            "INSERT INTO agent_rollout_agents (rollout_id, agent_id, position, status) VALUES ('rollout-g', 'agent-g', 0, 'pending')",
        )
        conn.execute(
            "UPDATE agent_rollout_agents SET status = 'succeeded', completed_at = ? WHERE agent_id = 'agent-g'",
            (now,),
        )
        conn.execute(
            "UPDATE agent_rollouts SET last_batch_settled_at = ? WHERE id = 'rollout-g'",
            (now,),
        )

    advance_rollout("rollout-g", db, settings)
    rollout = db.get_agent_rollout("rollout-g")
    assert rollout["status"] == "active"


def test_rollout_sweep_marks_stale_directed_failed(settings, db):
    now = utcnow_iso()
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents (id, name, hostname, machine_id, os, token_hash, agent_version, profile_id, enrolled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("agent-h", "agent h", "host-h.example", "machine-h", "linux", "hash", "0.8.0", "prof_workstation", now),
        )
        conn.execute(
            """INSERT INTO agent_rollouts (id, profile_id, target_version, batch_size, validation, validation_seconds, status, created_at, updated_at)
               VALUES ('rollout-h', 'prof_workstation', '0.9.1', 1, 0, 300, 'active', ?, ?)""",
            (now, now),
        )
        conn.execute(
            "INSERT INTO agent_rollout_agents (rollout_id, agent_id, position, status, directed_at) "
            "VALUES ('rollout-h', 'agent-h', 0, 'directed', ?)",
            (stale,),
        )

    _rollout_sweep(db, settings)
    agent = db.get_rollout_agent_state("rollout-h", "agent-h")
    assert agent["status"] == "failed"
    assert "timeout" in agent["last_error"].lower()


def test_rollout_sweep_advances_active_rollouts(settings, db):
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents (id, name, hostname, machine_id, os, token_hash, agent_version, profile_id, enrolled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("agent-i", "agent i", "host-i.example", "machine-i", "linux", "hash", "0.8.0", "prof_workstation", now),
        )
        conn.execute(
            """INSERT INTO agent_rollouts (id, profile_id, target_version, batch_size, validation, validation_seconds, status, created_at, updated_at)
               VALUES ('rollout-i', 'prof_workstation', '0.9.1', 1, 0, 300, 'active', ?, ?)""",
            (now, now),
        )
        conn.execute(
            "INSERT INTO agent_rollout_agents (rollout_id, agent_id, position, status) VALUES ('rollout-i', 'agent-i', 0, 'pending')",
        )
        conn.execute(
            "UPDATE agent_rollout_agents SET status = 'succeeded', completed_at = ? WHERE agent_id = 'agent-i'",
            (now,),
        )

    _rollout_sweep(db, settings)
    rollout = db.get_agent_rollout("rollout-i")
    assert rollout["status"] == "completed"
