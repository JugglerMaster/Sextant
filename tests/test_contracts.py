from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.helpers import (
    enroll_agent,
    headers,
    heartbeat,
    issue_token,
    login,
    sample_metrics,
)

USER_PASSWORD = "contract test password 123"


def _create_user(client: TestClient, admin: dict, username: str, role: str) -> dict:
    response = client.post(
        "/api/users",
        json={
            "username": username,
            "display_name": username,
            "role": role,
            "password": USER_PASSWORD,
        },
        headers=headers(admin),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _queue(client: TestClient, session: dict, agent_id: str, command: str = "printf contract") -> dict:
    response = client.post(
        f"/api/agents/{agent_id}/commands",
        json={"kind": "shell", "args": {"shell": "bash", "command": command, "timeout": 30}},
        headers=headers(session),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_viewer_can_read_but_cannot_operate_or_administer(app, client, admin):
    agent = enroll_agent(admin)
    user = _create_user(client, admin, "contract-viewer", "viewer")

    with TestClient(app) as viewer_client:
        viewer = login(viewer_client, user["username"], USER_PASSWORD)

        # General reads are available to viewers.
        assert viewer_client.get("/api/dashboard").status_code == 200
        assert viewer_client.get(f"/api/agents/{agent['agent_id']}").status_code == 200
        assert viewer_client.get(f"/api/agents/{agent['agent_id']}/commands").status_code == 200
        assert viewer_client.get("/api/wake-targets").status_code == 200
        assert viewer_client.get("/api/sessions").status_code == 200

        # Admin-only reads are not.
        assert viewer_client.get("/api/audit").status_code == 403
        assert viewer_client.get("/api/users").status_code == 403
        assert viewer_client.get("/api/enrollment-tokens").status_code == 403

        denied = [
            viewer_client.post(
                f"/api/agents/{agent['agent_id']}/commands",
                json={"kind": "refresh", "args": {}},
                headers=headers(viewer),
            ),
            viewer_client.post(f"/api/agents/{agent['agent_id']}/wake", json={}, headers=headers(viewer)),
            viewer_client.post("/api/wake-targets", json={"name": "x", "mac": "AA:BB:CC:DD:EE:FF"}, headers=headers(viewer)),
            viewer_client.post("/api/enrollment-tokens", json={"label": "x"}, headers=headers(viewer)),
            viewer_client.post(
                "/api/users",
                json={"username": "nope", "display_name": "Nope", "role": "viewer", "password": USER_PASSWORD},
                headers=headers(viewer),
            ),
            viewer_client.patch(f"/api/agents/{agent['agent_id']}", json={"notes": "nope"}, headers=headers(viewer)),
        ]
        assert [response.status_code for response in denied] == [403] * len(denied)


def test_operator_can_operate_but_cannot_administer(app, client, admin, monkeypatch):
    agent = enroll_agent(admin)
    user = _create_user(client, admin, "contract-operator", "operator")
    monkeypatch.setattr("sextant_control.server.send_magic_packet", lambda *args, **kwargs: 3)

    with TestClient(app) as operator_client:
        operator = login(operator_client, user["username"], USER_PASSWORD)

        assert operator_client.get("/api/dashboard").status_code == 200
        command = operator_client.post(
            f"/api/agents/{agent['agent_id']}/commands",
            json={"kind": "refresh", "args": {}},
            headers=headers(operator),
        )
        assert command.status_code == 200, command.text
        wake = operator_client.post(f"/api/agents/{agent['agent_id']}/wake", json={}, headers=headers(operator))
        assert wake.status_code == 200, wake.text
        target = operator_client.post(
            "/api/wake-targets",
            json={"name": "operator target", "mac": "AA:BB:CC:DD:EE:FF"},
            headers=headers(operator),
        )
        assert target.status_code == 200, target.text

        denied = [
            operator_client.get("/api/audit"),
            operator_client.get("/api/users"),
            operator_client.get("/api/enrollment-tokens"),
            operator_client.post("/api/enrollment-tokens", json={"label": "x"}, headers=headers(operator)),
            operator_client.post(
                "/api/users",
                json={"username": "blocked", "display_name": "Blocked", "role": "viewer", "password": USER_PASSWORD},
                headers=headers(operator),
            ),
            operator_client.patch(f"/api/agents/{agent['agent_id']}", json={"notes": "nope"}, headers=headers(operator)),
            operator_client.post(f"/api/agents/{agent['agent_id']}/rotate-token", headers=headers(operator)),
        ]
        assert [response.status_code for response in denied] == [403] * len(denied)


def test_admin_can_use_admin_routes(client, admin, monkeypatch):
    agent = enroll_agent(admin)
    monkeypatch.setattr("sextant_control.server.send_magic_packet", lambda *args, **kwargs: 3)

    created_user = _create_user(client, admin, "contract-admin-target", "viewer")
    assert created_user["role"] == "viewer"
    assert client.get("/api/audit").status_code == 200
    assert client.get("/api/users").status_code == 200
    assert client.get("/api/enrollment-tokens").status_code == 200
    assert issue_token(admin, "contract")
    assert client.patch(f"/api/agents/{agent['agent_id']}", json={"notes": "allowed"}, headers=headers(admin)).status_code == 200
    assert client.post(f"/api/agents/{agent['agent_id']}/wake", json={}, headers=headers(admin)).status_code == 200


def test_unauthenticated_mutations_return_401(client):
    cases = [
        ("post", "/api/auth/logout", {}),
        ("post", "/api/me/password", {"current_password": "old password 123", "new_password": "new password 123"}),
        ("delete", "/api/sessions/ses_missing", None),
        ("post", "/api/users/1/revoke-sessions", {}),
        ("post", "/api/users/1/reset-password", {"new_password": USER_PASSWORD}),
        ("patch", "/api/agents/agt_missing", {"notes": "x"}),
        ("post", "/api/agents/agt_missing/commands", {"kind": "refresh", "args": {}}),
        ("post", "/api/commands/cmd_missing/cancel", {}),
        ("post", "/api/commands/cmd_missing/retry", {}),
        ("post", "/api/agents/agt_missing/wake", {}),
        ("post", "/api/wake-targets", {"name": "x", "mac": "AA:BB:CC:DD:EE:FF"}),
        ("delete", "/api/wake-targets/wol_missing", None),
        ("post", "/api/wake-targets/wol_missing/wake", {}),
        ("post", "/api/enrollment-tokens", {"label": "x"}),
        ("delete", "/api/enrollment-tokens/enr_missing", None),
        (
            "post",
            "/api/users",
            {"username": "nobody", "display_name": "Nobody", "role": "viewer", "password": USER_PASSWORD},
        ),
        ("patch", "/api/users/1", {"active": False}),
        ("post", "/api/agents/agt_missing/rotate-token", None),
        ("delete", "/api/agents/agt_missing", None),
        ("post", "/api/agent/agt_missing/heartbeat", sample_metrics()),
        ("post", "/api/agent/agt_missing/commands/cmd_missing/result", {"status": "succeeded", "exit_code": 0}),
    ]
    for method, path, payload in cases:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
        assert response.status_code == 401, f"{method.upper()} {path} returned {response.status_code}: {response.text}"


def test_unauthenticated_reads_return_401_but_public_routes_stay_open(client):
    protected = [
        "/api/me",
        "/api/settings",
        "/api/sessions",
        "/api/dashboard",
        "/api/agents/agt_missing",
        "/api/agents/agt_missing/commands",
        "/api/commands/cmd_missing",
        "/api/wake-targets",
        "/api/enrollment-tokens",
        "/api/users",
        "/api/users/1/sessions",
        "/api/audit",
    ]
    for path in protected:
        response = client.get(path)
        assert response.status_code == 401, f"GET {path} returned {response.status_code}: {response.text}"

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/config").status_code == 200
    assert client.get("/").status_code == 200


def test_csrf_required_for_mutations_but_not_gets_or_agent_routes(client, admin, app):
    agent = enroll_agent(admin)
    path = f"/api/agents/{agent['agent_id']}/commands"
    body = {"kind": "refresh", "args": {}}

    missing = client.post(path, json=body, headers={})
    assert missing.status_code == 403
    wrong = client.post(path, json=body, headers={"X-CSRF-Token": "not-the-token"})
    assert wrong.status_code == 403

    allowed = client.post(path, json=body, headers=headers(admin))
    assert allowed.status_code == 200, allowed.text

    # Safe reads do not require the CSRF header.
    assert client.get("/api/dashboard").status_code == 200

    # Agent routes authenticate with the bearer token instead of CSRF.
    assert heartbeat(admin, agent)["server_time"]


def test_request_body_limits(client, admin, settings):
    oversized = str(settings.max_body_bytes + 1)

    declared = client.get("/api/health", headers={"Content-Length": oversized})
    assert declared.status_code == 413
    invalid = client.get("/api/health", headers={"Content-Length": "not-a-number"})
    assert invalid.status_code == 400

    # The guard runs before route handling, including for mutations with valid CSRF.
    mutation = client.post(
        "/api/wake-targets",
        json={"name": "x", "mac": "AA:BB:CC:DD:EE:FF"},
        headers={**headers(admin), "Content-Length": oversized},
    )
    assert mutation.status_code == 413
    invalid_mutation = client.post(
        "/api/wake-targets",
        json={"name": "x", "mac": "AA:BB:CC:DD:EE:FF"},
        headers={**headers(admin), "Content-Length": "nope"},
    )
    assert invalid_mutation.status_code == 400

    # An actual oversized body with a correct Content-Length is also rejected.
    actual = client.post(
        "/api/health",
        content=b"x" * (settings.max_body_bytes + 1),
        headers=headers(admin),
    )
    assert actual.status_code == 413


def test_agent_bearer_auth_rejections(client, admin):
    agent = enroll_agent(admin)
    payload = sample_metrics()

    for header in (
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Token abc"},
        {"Authorization": "Bearer wrong"},
    ):
        response = client.post(
            f"/api/agent/{agent['agent_id']}/heartbeat",
            json=payload,
            headers=header,
        )
        assert response.status_code == 401, f"headers={header!r} returned {response.status_code}"

    unknown = client.post(
        "/api/agent/agt_does_not_exist/heartbeat",
        json=payload,
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert unknown.status_code == 401

    wrong_prefix = client.post(
        "/api/agent/not-an-agent/heartbeat",
        json=payload,
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert wrong_prefix.status_code == 401


def test_single_session_revocation_only_invalidates_that_session(app, client, admin):
    user = _create_user(client, admin, "contract-sessions", "operator")

    with TestClient(app) as first, TestClient(app) as second:
        login(first, user["username"], USER_PASSWORD)
        login(second, user["username"], USER_PASSWORD)

        first_sessions = first.get("/api/sessions").json()["sessions"]
        assert len(first_sessions) == 2
        current = [session for session in first_sessions if session["current"]]
        assert len(current) == 1
        revoked = client.delete(f"/api/sessions/{current[0]['id']}", headers=headers(admin))
        assert revoked.status_code == 204

        assert first.get("/api/me").status_code == 401
        assert second.get("/api/me").status_code == 200


def test_revoke_all_keeps_the_current_session_and_rotates_csrf(app, client, admin):
    me = client.get("/api/me").json()["user"]

    with TestClient(app) as other:
        login(other)

        revoked = client.post(
            f"/api/users/{me['id']}/revoke-sessions",
            json={},
            headers=headers(admin),
        )
        assert revoked.status_code == 200, revoked.text
        payload = revoked.json()
        assert payload["revoked"] >= 1
        assert payload["csrf"]

        # The current browser gets a fresh session; the other session is gone.
        assert client.get("/api/me").status_code == 200
        assert other.get("/api/me").status_code == 401

        # The old CSRF token belongs to the revoked session and is rejected.
        stale = client.post("/api/auth/logout", json={}, headers=headers(admin))
        assert stale.status_code == 403
        fresh = client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": payload["csrf"]})
        assert fresh.status_code == 204


def test_disabling_an_active_user_blocks_their_next_request(app, client, admin):
    user = _create_user(client, admin, "contract-disabled", "operator")

    with TestClient(app) as target:
        login(target, user["username"], USER_PASSWORD)
        assert target.get("/api/me").status_code == 200

        disabled = client.patch(
            f"/api/users/{user['id']}",
            json={"active": False},
            headers=headers(admin),
        )
        assert disabled.status_code == 200
        assert disabled.json()["active"] is False
        assert target.get("/api/me").status_code == 401


def test_expired_enrollment_token_is_rejected(app, client, admin):
    token = issue_token(admin, "expired-contract")
    with app.state.db.connection() as conn:
        conn.execute("UPDATE enrollment_tokens SET expires_at = '2020-01-01T00:00:00Z' WHERE used_at IS NULL")

    response = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": token,
            "hostname": "expired-box",
            "os": "linux",
        },
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "shell", "args": {"shell": "zsh", "command": "printf x", "timeout": 30}},
        {"kind": "shell", "args": {"shell": "bash", "command": "", "timeout": 30}},
        {"kind": "shell", "args": {"shell": "bash", "command": "   ", "timeout": 30}},
        {"kind": "shell", "args": {"shell": "bash", "command": "printf x", "timeout": 4}},
        {"kind": "shell", "args": {"shell": "bash", "command": "printf x", "timeout": 301}},
        {"kind": "service", "args": {"action": "restart", "name": "nginx; rm -rf /"}},
        {"kind": "service", "args": {"action": "explode", "name": "nginx"}},
        {"kind": "power", "args": {"action": "explode", "delay": 1}},
        {"kind": "power", "args": {"action": "shutdown", "delay": 601}},
    ],
)
def test_invalid_command_arguments_return_400(client, admin, body):
    agent = enroll_agent(admin)
    response = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json=body,
        headers=headers(admin),
    )
    assert response.status_code == 400, response.text


def test_unknown_and_cross_agent_command_results_return_404(client, admin):
    first = enroll_agent(admin, hostname="first-box", machine_id="contract-first")
    second = enroll_agent(admin, hostname="second-box", machine_id="contract-second")

    missing = client.post(
        f"/api/agent/{first['agent_id']}/commands/cmd_missing/result",
        json={"status": "succeeded", "exit_code": 0},
        headers={"Authorization": f"Bearer {first['agent_token']}"},
    )
    assert missing.status_code == 404

    command = _queue(client, admin, first["agent_id"])
    assert heartbeat(admin, first)["commands"][0]["id"] == command["id"]

    cross_agent = client.post(
        f"/api/agent/{second['agent_id']}/commands/{command['id']}/result",
        json={"status": "succeeded", "exit_code": 0},
        headers={"Authorization": f"Bearer {second['agent_token']}"},
    )
    assert cross_agent.status_code == 404


def test_unknown_agent_returns_404_for_agent_scoped_routes(client, admin):
    unknown = "agt_does_not_exist"

    assert client.get(f"/api/agents/{unknown}").status_code == 404
    assert client.post(
        f"/api/agents/{unknown}/commands",
        json={"kind": "refresh", "args": {}},
        headers=headers(admin),
    ).status_code == 404
    assert client.post(f"/api/agents/{unknown}/wake", json={}, headers=headers(admin)).status_code == 404
    assert client.patch(f"/api/agents/{unknown}", json={"notes": "x"}, headers=headers(admin)).status_code == 404
    assert client.post(f"/api/agents/{unknown}/rotate-token", headers=headers(admin)).status_code == 404
    assert client.delete(f"/api/agents/{unknown}", headers=headers(admin)).status_code == 404


def test_unknown_agent_command_history_returns_404(client, admin):
    assert client.get("/api/agents/agt_does_not_exist/commands").status_code == 404
