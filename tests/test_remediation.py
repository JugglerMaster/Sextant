from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runner_agent.client import _NoRedirectHandler
from runner_agent.executor import run_capture
from runner_control.config import Settings
from runner_control.security import decode_session, sign_session
from runner_control.server import create_app
from runner_control.views import validate_command_args
from tests.helpers import (
    enroll_agent,
    heartbeat,
    headers,
    issue_token,
    login,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "runner_control" / "static"
WEAK_ADMIN_HASH = "admin"


# --- P0: Host header and body limits ---------------------------------------


def test_host_header_cannot_bypass_csrf(client, admin):
    # The middleware must use the ASGI path, not request.url.path reconstructed
    # from this untrusted header.
    response = client.post(
        "/api/wake-targets",
        json={"name": "x", "mac": "AA:BB:CC:DD:EE:FF"},
        headers={"Host": "example.com/abc?bar="},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid CSRF token."


def test_trusted_host_middleware_rejects_unlisted_host(settings):
    restricted = replace(settings, allowed_hosts=("ops.example.com",))
    app = create_app(restricted)
    with TestClient(app) as client:
        assert client.get("/api/health", headers={"Host": "testserver"}).status_code == 400
        assert client.get("/api/health", headers={"Host": "ops.example.com"}).status_code == 200


def test_chunked_api_body_is_rejected(client, admin):
    response = client.post(
        "/api/auth/login",
        content=iter([b'{"username":"admin","password":"x"}']),
        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
    )
    assert response.status_code == 411


def test_early_rejections_still_carry_security_headers(client):
    response = client.get("/api/sessions")
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


# --- P1: sessions, secrets, bootstrap, validation ---------------------------


def test_session_cookie_uid_must_match_session_row(app, client, admin):
    settings = app.state.settings
    created = client.post(
        "/api/users",
        json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    viewer_id = created.json()["id"]

    viewer = login(client, "viewer1", "viewer password 123")
    assert viewer["user"]["id"] == viewer_id
    cookie = client.cookies.get(settings.cookie_name)
    payload = decode_session(cookie, settings.secret_key)
    assert payload is not None
    payload["uid"] = admin["user"]["id"]
    client.cookies.set(settings.cookie_name, sign_session(payload, settings.secret_key))

    # The session row still belongs to the viewer, so the forged cookie is rejected.
    assert client.get("/api/users").status_code == 401


def test_configured_secret_key_must_be_strong(tmp_path):
    with pytest.raises(ValueError):
        Settings.from_env({"RC_DATA_DIR": str(tmp_path), "RC_SECRET_KEY": "short"})


def test_session_hours_are_clamped(tmp_path):
    settings = Settings.from_env({"RC_DATA_DIR": str(tmp_path), "RC_SESSION_HOURS": "999999"})
    assert settings.session_hours == 8784


def test_weak_bootstrap_admin_password_is_refused(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        static_dir=STATIC_DIR,
        secret_key=b"test-secret-key-that-is-long-enough",
        admin_password=WEAK_ADMIN_HASH,
    )
    with pytest.raises(RuntimeError):
        create_app(settings)


def test_service_names_cannot_start_with_a_dash():
    with pytest.raises(ValueError):
        validate_command_args("service", {"action": "status", "name": "-H"})
    assert validate_command_args("service", {"action": "status", "name": "sshd.service"})["name"] == "sshd.service"


def test_enrollment_token_uses_configured_default(settings):
    configured = replace(settings, enrollment_ttl_minutes=7)
    app = create_app(configured)
    with TestClient(app) as client:
        session = login(client)
        response = client.post(
            "/api/enrollment-tokens",
            json={"label": "seven"},
            headers=headers(session),
        )
        assert response.status_code == 200, response.text
        expires = response.json()["expires_at"]
        from runner_control.db import parse_iso

        delta = (parse_iso(expires) - parse_iso(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        assert 6 * 60 <= delta.total_seconds() <= 8 * 60


# --- P1: command dispatch, result races, output bounds ----------------------


def test_heartbeat_dispatches_one_command_at_a_time(client, admin):
    agent = enroll_agent(admin, token=issue_token(admin))
    for _ in range(2):
        response = client.post(
            f"/api/agents/{agent['agent_id']}/commands",
            json={"kind": "refresh", "args": {}},
            headers=headers(admin),
        )
        assert response.status_code == 200, response.text

    first = heartbeat(admin, agent)
    assert len(first["commands"]) == 1
    second = heartbeat(admin, agent)
    assert len(second["commands"]) == 1


def test_late_result_cannot_overwrite_terminal_state(app, client, admin):
    agent = enroll_agent(admin, token=issue_token(admin))
    created = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "refresh", "args": {}},
        headers=headers(admin),
    ).json()
    dispatched = heartbeat(admin, agent)["commands"][0]
    assert dispatched["id"] == created["id"]

    with app.state.db.transaction() as conn:
        conn.execute("UPDATE commands SET status = 'failed' WHERE id = ?", (created["id"],))

    response = client.post(
        f"/api/agent/{agent['agent_id']}/commands/{created['id']}/result",
        json={"status": "succeeded", "exit_code": 0, "stdout": "late"},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["idempotent"] is True
    with app.state.db.connection() as conn:
        status = conn.execute("SELECT status FROM commands WHERE id = ?", (created["id"],)).fetchone()["status"]
    assert status == "failed"


def test_agent_output_is_bounded_and_terminates():
    result = run_capture(
        [sys.executable, "-c", "print('x' * 200000)"],
        timeout=10,
        max_output=1024,
    )
    assert result["status"] == "failed"
    assert "exceeded" in result["error"].lower()
    assert len(result["stdout"]) <= 1024


def test_agent_client_refuses_redirects():
    assert _NoRedirectHandler().redirect_request(None, None, 302, "", {}, "https://evil.example") is None
