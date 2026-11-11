from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.helpers import enroll_agent, headers, heartbeat, login


def _next_event(lines) -> tuple[str | None, dict | None]:
    event = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if event is None and not data:
                continue
            return event, json.loads("\n".join(data)) if data else None
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return None, None


def test_dashboard_sse_requires_authentication(client):
    assert client.get("/api/events").status_code == 401


def test_dashboard_sse_emits_dashboard_payload(client, admin):
    agent = enroll_agent(admin)
    heartbeat(admin, agent)

    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event, payload = _next_event(response.iter_lines())

    assert event == "dashboard"
    assert payload is not None
    dashboard = client.get("/api/dashboard").json()
    assert set(payload) == {"server_time", "stats", "agents"}
    assert set(payload["stats"]) == set(dashboard["stats"])
    assert payload["stats"]["total"] == 1
    assert payload["agents"][0]["name"] == "test-box"


def test_command_events_require_operator_and_close_on_terminal_state(app, client, admin):
    agent = enroll_agent(admin)
    created = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "shell", "args": {"shell": "bash", "command": "printf hello", "timeout": 30}},
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    command_id = created.json()["id"]

    with TestClient(app) as anonymous:
        assert anonymous.get(f"/api/commands/{command_id}/events").status_code == 401

    user = client.post(
        "/api/users",
        json={
            "username": "viewer-sse",
            "display_name": "Viewer SSE",
            "role": "viewer",
            "password": "viewer password 123",
        },
        headers=headers(admin),
    )
    assert user.status_code == 200, user.text
    with TestClient(app) as viewer_client:
        login(viewer_client, "viewer-sse", "viewer password 123")
        assert viewer_client.get(f"/api/commands/{command_id}/events").status_code == 403

    assert client.get("/api/commands/cmd_missing/events").status_code == 404

    heartbeat(admin, agent)
    result = client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command_id}/result",
        json={"status": "succeeded", "exit_code": 0, "stdout": "hello", "stderr": "", "error": ""},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert result.status_code == 200, result.text

    with client.stream("GET", f"/api/commands/{command_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        event, payload = _next_event(lines)
        # The generator closes after the terminal snapshot, so no more events follow.
        assert next(lines, None) is None

    assert event == "command"
    assert payload is not None
    assert payload["command"]["id"] == command_id
    assert payload["command"]["status"] == "succeeded"
    assert payload["command"]["stdout"] == "hello"
