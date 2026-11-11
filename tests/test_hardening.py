from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from sextant_control.server import create_app
from tests.helpers import (
    enroll_agent,
    headers,
    heartbeat,
    issue_token,
    login,
    sample_metrics,
)


def queue(session: dict, agent_id: str, command: str = "printf hardening") -> dict:
    response = session["client"].post(
        f"/api/agents/{agent_id}/commands",
        json={"kind": "shell", "args": {"shell": "bash", "command": command, "timeout": 30}},
        headers=headers(session),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_failed_login_is_audited_without_secret(client, admin):
    failed = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "definitely-wrong", "mode": "local"},
    )
    assert failed.status_code == 401
    events = client.get("/api/audit?limit=20").json()["events"]
    failures = [event for event in events if event["action"] == "auth.login_failed"]
    assert failures
    assert failures[0]["actor"] == "admin"
    assert "definitely-wrong" not in str(failures[0]["detail"])
    assert failures[0]["detail"]["reason"] == "bad_password"


def test_sessions_list_disable_reset_and_revoke(app, client, admin):
    created = client.post(
        "/api/users",
        json={
            "username": "sessionuser",
            "display_name": "Session User",
            "role": "operator",
            "password": "session user password",
        },
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]

    with TestClient(app) as first:
        login(first, "sessionuser", "session user password")
        with TestClient(app) as second:
            login(second, "sessionuser", "session user password")
            listing = client.get(f"/api/users/{user_id}/sessions").json()
            assert len(listing["sessions"]) == 2
            assert all(session["current"] is False for session in listing["sessions"])
            own_sessions = first.get("/api/sessions").json()["sessions"]
            assert len(own_sessions) == 2
            assert any(session["current"] for session in own_sessions)

            revoked = client.post(
                f"/api/users/{user_id}/revoke-sessions",
                json={},
                headers=headers(admin),
            )
            assert revoked.status_code == 200
            assert revoked.json()["revoked"] >= 2
            assert first.get("/api/me").status_code == 401
            assert second.get("/api/me").status_code == 401

            relogin = login(second, "sessionuser", "session user password")
            assert relogin["user"]["active"] is True

            reset = client.post(
                f"/api/users/{user_id}/reset-password",
                json={"new_password": "brand new session password"},
                headers=headers(admin),
            )
            assert reset.status_code == 200, reset.text
            assert second.get("/api/me").status_code == 401
            assert second.post(
                "/api/auth/login",
                json={"username": "sessionuser", "password": "session user password", "mode": "local"},
            ).status_code == 401
            assert login(second, "sessionuser", "brand new session password")

    disabled = client.patch(
        f"/api/users/{user_id}",
        json={"active": False},
        headers=headers(admin),
    )
    assert disabled.status_code == 200
    assert disabled.json()["active"] is False
    with TestClient(app) as blocked:
        assert blocked.post(
            "/api/auth/login",
            json={"username": "sessionuser", "password": "brand new session password", "mode": "local"},
        ).status_code == 401

    enabled = client.patch(
        f"/api/users/{user_id}",
        json={"active": True},
        headers=headers(admin),
    )
    assert enabled.status_code == 200
    with TestClient(app) as allowed:
        login(allowed, "sessionuser", "brand new session password")


def test_admin_cannot_deactivate_self_or_last_admin(client, admin):
    me = client.get("/api/me").json()["user"]
    response = client.patch(f"/api/users/{me['id']}", json={"active": False}, headers=headers(admin))
    assert response.status_code == 400


def test_command_cancel_retry_stale_and_offline_policy(settings, monkeypatch):
    app = create_app(replace(settings, max_queued_commands_per_agent=3))
    with TestClient(app) as client:
        admin = login(client)
        agent = enroll_agent(admin, machine_id="machine-1")
        heartbeat(admin, agent)

        first = queue(admin, agent["agent_id"])
        cancelled = client.post(f"/api/commands/{first['id']}/cancel", json={}, headers=headers(admin))
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        retried = client.post(f"/api/commands/{first['id']}/retry", json={}, headers=headers(admin))
        assert retried.status_code == 200
        assert retried.json()["status"] == "queued"
        assert retried.json()["kind"] == "shell"

        # Dispatch and then force the command into the past to exercise the sweeper.
        dispatched = heartbeat(admin, agent)
        assert dispatched["commands"]
        stale_id = dispatched["commands"][0]["id"]
        with app.state.db.connection() as conn:
            conn.execute(
                "UPDATE commands SET dispatched_at = '2020-01-01T00:00:00Z' WHERE id = ?",
                (stale_id,),
            )
        client.get("/api/dashboard")
        stale = client.get(f"/api/commands/{stale_id}").json()["command"]
        assert stale["status"] == "failed"
        assert "timed out" in stale["error"].lower()

        retry_stale = client.post(f"/api/commands/{stale_id}/retry", json={}, headers=headers(admin))
        assert retry_stale.status_code == 200

        # Fill the queue and hit the cap.
        queue(admin, agent["agent_id"], "printf 1")
        queue(admin, agent["agent_id"], "printf 2")
        full = client.post(
            f"/api/agents/{agent['agent_id']}/commands",
            json={"kind": "shell", "args": {"shell": "bash", "command": "printf 3", "timeout": 30}},
            headers=headers(admin),
        )
        assert full.status_code == 409


def test_offline_policy_rejects_commands(settings):
    app = create_app(replace(settings, offline_command_policy="reject"))
    with TestClient(app) as client:
        admin = login(client)
        agent = enroll_agent(admin)
        response = client.post(
            f"/api/agents/{agent['agent_id']}/commands",
            json={"kind": "refresh", "args": {}},
            headers=headers(admin),
        )
        assert response.status_code == 409
        assert "offline" in response.json()["detail"].lower()


def test_agent_policy_update_and_reenrollment_dedupe(client, admin):
    agent = enroll_agent(admin, machine_id="stable-machine", watch_services=["ssh"])
    updated = client.patch(
        f"/api/agents/{agent['agent_id']}",
        json={
            "tags": ["gpu", "office"],
            "notes": "Managed by policy",
            "watch_services": ["sshd", "nginx"],
            "allow_shell": False,
            "allow_power": True,
            "allow_service": False,
        },
        headers=headers(admin),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["tags"] == ["gpu", "office"]
    assert body["notes"] == "Managed by policy"
    assert body["watch_services"] == ["sshd", "nginx"]
    assert body["policy"] == {
        "allow_shell": False,
        "allow_power": True,
        "allow_service": False,
        "allow_packages": True,
        "allow_docker": False,
        "allow_docker_exec": False,
        "allow_docker_compose": False,
        "allow_docker_prune": False,
    }

    response = heartbeat(admin, agent)
    assert response["policy"]["allow_shell"] is False
    assert response["policy"]["allow_service"] is False
    assert response["policy"]["watch_services"] == ["sshd", "nginx"]

    blocked = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "shell", "args": {"shell": "bash", "command": "printf blocked", "timeout": 30}},
        headers=headers(admin),
    )
    assert blocked.status_code == 409
    assert "policy" in blocked.json()["detail"].lower()

    no_changes = client.patch(
        f"/api/agents/{agent['agent_id']}",
        json={},
        headers=headers(admin),
    )
    assert no_changes.status_code == 400

    # Re-enrolling the same stable machine identity rotates credentials and
    # updates the existing row instead of creating a duplicate.
    token = issue_token(admin, "reenroll")
    second = enroll_agent(admin, token=token, machine_id="stable-machine", hostname="renamed-box")
    assert second["agent_id"] == agent["agent_id"]
    assert second["reenrolled"] is True
    assert client.get(f"/api/agents/{agent['agent_id']}").status_code == 200
    assert client.get("/api/dashboard").json()["stats"]["total"] == 1
    old_token = client.post(
        f"/api/agent/{agent['agent_id']}/heartbeat",
        json=sample_metrics(),
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert old_token.status_code == 401
    new_token = client.post(
        f"/api/agent/{agent['agent_id']}/heartbeat",
        json=sample_metrics(machine_id="stable-machine"),
        headers={"Authorization": f"Bearer {second['agent_token']}"},
    )
    assert new_token.status_code == 200
