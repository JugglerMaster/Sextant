from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import (
    ADMIN_PASSWORD,
    enroll_agent,
    headers,
    heartbeat,
    issue_token,
    login,
    sample_metrics,
)


def test_public_health_and_security_headers(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]

    index = client.get("/")
    assert index.status_code == 200
    assert "Runner Control" in index.text
    assert "/static/app.js" in index.text
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_dashboard_requires_authentication(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 401


def test_login_me_logout(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong", "mode": "auto"}).status_code == 401
    session = login(client)
    assert session["user"]["role"] == "admin"
    assert session["permissions"] == {"operate": True, "admin": True, "viewer": True}

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["permissions"] == {"operate": True, "admin": True, "viewer": True}

    logout = client.post("/api/auth/logout", json={}, headers=headers(session))
    assert logout.status_code == 204
    assert client.get("/api/me").status_code == 401


def test_csrf_is_required(client):
    login(client)
    response = client.post(
        "/api/wake-targets",
        json={"name": "office", "mac": "AA:BB:CC:DD:EE:FF", "broadcast": "255.255.255.255", "port": 9},
    )
    assert response.status_code == 403


def test_login_rate_limit(app):
    with TestClient(app) as client:
        for _ in range(5):
            response = client.post("/api/auth/login", json={"username": "nobody", "password": "bad", "mode": "auto"})
            assert response.status_code == 401
        blocked = client.post("/api/auth/login", json={"username": "nobody", "password": "bad", "mode": "auto"})
        assert blocked.status_code == 429
        assert "retry-after" in blocked.headers


def test_enrollment_is_one_time_and_agent_requires_token(client, admin):
    token = issue_token(admin)
    agent = enroll_agent(admin, token)

    again = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": token,
            "hostname": "second",
            "os": "linux",
        },
    )
    assert again.status_code == 401

    unauthorized = client.post(
        f"/api/agent/{agent['agent_id']}/heartbeat",
        json=sample_metrics(),
        headers={"Authorization": "Bearer wrong"},
    )
    assert unauthorized.status_code == 401


def test_full_agent_command_wol_and_audit_flow(client, admin, monkeypatch):
    agent = enroll_agent(admin)
    first = heartbeat(admin, agent)
    assert first["commands"] == []

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["stats"]["total"] == 1
    assert dashboard["stats"]["online"] == 1
    machine = dashboard["agents"][0]
    assert machine["name"] == "test-box"
    assert machine["alert_count"] >= 1
    assert machine["gpus"][0]["name"] == "NVIDIA RTX 4090"
    assert machine["services"][0]["watched"] is True

    created = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "shell", "args": {"shell": "bash", "command": "printf hello", "timeout": 30}},
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    command = created.json()
    assert command["status"] == "queued"

    dispatched = heartbeat(admin, agent, sample_metrics(cpu_pct=41.0))
    assert len(dispatched["commands"]) == 1
    assert dispatched["commands"][0]["id"] == command["id"]
    assert dispatched["commands"][0]["args"]["command"] == "printf hello"

    # A second heartbeat must not dispatch the same command twice.
    assert heartbeat(admin, agent, sample_metrics(cpu_pct=41.0))["commands"] == []

    result = client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command['id']}/result",
        json={"status": "succeeded", "exit_code": 0, "stdout": "hello", "stderr": "", "error": ""},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["command"]["status"] == "succeeded"
    assert result.json()["command"]["stdout"] == "hello"

    # Idempotent result retries are accepted.
    retry = client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command['id']}/result",
        json={"status": "failed", "exit_code": 1, "stdout": "", "stderr": "", "error": ""},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True

    detail = client.get(f"/api/agents/{agent['agent_id']}").json()
    assert detail["commands"][0]["status"] == "succeeded"
    assert detail["agent"]["cpu_pct"] == 41.0

    def fake_send(mac, broadcast="255.255.255.255", port=9, packets=3, sock_factory=None):
        assert mac == "AA:BB:CC:DD:EE:FF"
        return 3

    monkeypatch.setattr("runner_control.server.send_magic_packet", fake_send)
    wake = client.post(
        f"/api/agents/{agent['agent_id']}/wake",
        json={"broadcast": "192.168.1.255", "port": 9, "packets": 3},
        headers=headers(admin),
    )
    assert wake.status_code == 200
    assert wake.json()["sent"] == 3

    audit = client.get("/api/audit").json()["events"]
    actions = {event["action"] for event in audit}
    assert {"auth.login", "enrollment.created", "agent.enrolled", "command.created", "wol.sent"} <= actions


def test_service_command_validation_and_viewer_read_only(app, client, admin):
    agent = enroll_agent(admin)
    bad = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "service", "args": {"action": "restart", "name": "nginx; rm -rf /"}},
        headers=headers(admin),
    )
    assert bad.status_code == 400

    bad_power = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "power", "args": {"action": "explode", "delay": 1}},
        headers=headers(admin),
    )
    assert bad_power.status_code == 400

    created_user = client.post(
        "/api/users",
        json={
            "username": "viewer1",
            "display_name": "Viewer One",
            "role": "viewer",
            "password": "viewer password 123",
        },
        headers=headers(admin),
    )
    assert created_user.status_code == 200, created_user.text

    with TestClient(app) as viewer_client:
        viewer = login(viewer_client, "viewer1", "viewer password 123")
        assert viewer_client.get("/api/dashboard").status_code == 200
        denied = viewer_client.post(
            f"/api/agents/{agent['agent_id']}/commands",
            json={"kind": "refresh", "args": {}},
            headers=headers(viewer),
        )
        assert denied.status_code == 403
        assert viewer_client.post("/api/wake-targets", json={"name": "x", "mac": "AA:BB:CC:DD:EE:FF"}, headers=headers(viewer)).status_code == 403


def test_wake_targets_crud(client, admin, monkeypatch):
    monkeypatch.setattr("runner_control.server.send_magic_packet", lambda *args, **kwargs: 3)
    created = client.post(
        "/api/wake-targets",
        json={"name": "Office PC", "mac": "aa-bb-cc-dd-ee-ff", "broadcast": "192.168.1.255", "port": 9},
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    target = created.json()
    assert target["mac"] == "AA:BB:CC:DD:EE:FF"

    invalid = client.post(
        "/api/wake-targets",
        json={"name": "Bad", "mac": "ZZ:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255", "port": 9},
        headers=headers(admin),
    )
    assert invalid.status_code == 400

    woke = client.post(f"/api/wake-targets/{target['id']}/wake", json={}, headers=headers(admin))
    assert woke.status_code == 200

    deleted = client.delete(f"/api/wake-targets/{target['id']}", headers=headers(admin))
    assert deleted.status_code == 204
    assert client.get("/api/wake-targets").json()["targets"] == []


def test_admin_can_manage_users_and_password_change(client, admin):
    created = client.post(
        "/api/users",
        json={
            "username": "operator1",
            "display_name": "Operator One",
            "role": "operator",
            "password": "operator password 123",
        },
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]

    updated = client.patch(f"/api/users/{user_id}", json={"role": "viewer"}, headers=headers(admin))
    assert updated.status_code == 200
    assert updated.json()["role"] == "viewer"

    # The only admin cannot be demoted.
    me = client.get("/api/me").json()["user"]
    last_admin = client.patch(f"/api/users/{me['id']}", json={"role": "viewer"}, headers=headers(admin))
    assert last_admin.status_code == 400

    changed = client.post(
        "/api/me/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "new correct horse battery"},
        headers=headers(admin),
    )
    assert changed.status_code == 200, changed.text
    new_csrf = changed.json()["csrf"]
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": new_csrf}).status_code == 204
    assert client.post("/api/auth/login", json={"username": "admin", "password": "new correct horse battery", "mode": "auto"}).status_code == 200


def test_agent_rotation_and_delete(client, admin):
    agent = enroll_agent(admin)
    rotated = client.post(f"/api/agents/{agent['agent_id']}/rotate-token", headers=headers(admin))
    assert rotated.status_code == 200
    new_token = rotated.json()["agent_token"]
    assert new_token != agent["agent_token"]

    old = client.post(
        f"/api/agent/{agent['agent_id']}/heartbeat",
        json=sample_metrics(),
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert old.status_code == 401
    new = client.post(
        f"/api/agent/{agent['agent_id']}/heartbeat",
        json=sample_metrics(),
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert new.status_code == 200

    assert client.delete(f"/api/agents/{agent['agent_id']}", headers=headers(admin)).status_code == 204
    assert client.get(f"/api/agents/{agent['agent_id']}").status_code == 404
