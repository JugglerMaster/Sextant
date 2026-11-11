from __future__ import annotations

import asyncio
import json
import queue
import threading
import time

from fastapi.testclient import TestClient

import runner_control.server as server_module
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


def _read_dashboard_events(app, cookie: str, events: queue.Queue, count: int) -> None:
    """Read ``count`` events from a fresh client in a background thread."""
    try:
        with TestClient(app) as stream_client:
            stream_client.cookies.set("rc_session", cookie)
            with stream_client.stream("GET", "/api/events") as response:
                lines = response.iter_lines()
                for _ in range(count):
                    events.put(_next_event(lines))
    except Exception as exc:  # surface reader failures to the test
        events.put(("error", {"detail": str(exc)}))


def _read_dashboard_until_closed(app, cookie: str, events: queue.Queue) -> None:
    try:
        with TestClient(app) as stream_client:
            stream_client.cookies.set("rc_session", cookie)
            with stream_client.stream("GET", "/api/events") as response:
                lines = response.iter_lines()
                while True:
                    event, payload = _next_event(lines)
                    if event is None and payload is None:
                        events.put(("closed", None))
                        return
                    events.put((event, payload))
    except Exception as exc:
        events.put(("error", {"detail": str(exc)}))


def _session_id(admin) -> str:
    return str(admin["client"].app.state.db.list_sessions(admin["user"]["id"])[0]["id"])


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


def test_dashboard_sse_emits_after_heartbeat_without_waiting_for_interval(app, client, admin):
    agent = enroll_agent(admin)
    cookie = client.cookies.get("rc_session")
    events: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_read_dashboard_events, args=(app, cookie, events, 2), daemon=True)
    reader.start()

    first_event, first_payload = events.get(timeout=3)
    assert first_event == "dashboard"
    assert first_payload is not None
    assert first_payload["stats"]["total"] == 1

    started = time.monotonic()
    heartbeat(admin, agent)
    event, payload = events.get(timeout=4)
    elapsed = time.monotonic() - started

    reader.join(timeout=2)
    assert not reader.is_alive()
    assert event == "dashboard"
    assert payload is not None
    assert payload["agents"][0]["cpu_pct"] == 22.5
    # The old implementation only refreshed every five seconds.
    assert elapsed < 4.0


def test_dashboard_sse_does_not_repeat_unchanged_revision(app, client, admin):
    enroll_agent(admin)
    cookie = client.cookies.get("rc_session")
    events: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_read_dashboard_events, args=(app, cookie, events, 2), daemon=True)
    reader.start()

    first_event, _ = events.get(timeout=3)
    assert first_event == "dashboard"

    time.sleep(1.5)
    assert events.empty(), "unchanged revision should not emit another dashboard event"

    # Wake the stream once so the reader can finish and close cleanly.
    app.state.dashboard_state.notify()
    event, payload = events.get(timeout=3)
    reader.join(timeout=2)
    assert not reader.is_alive()
    assert event == "dashboard"
    assert payload is not None


def test_dashboard_cache_builds_once_per_revision(app):
    state = app.state.dashboard_state
    calls: list[int] = []

    def build() -> dict:
        calls.append(1)
        return {"build": len(calls)}

    async def exercise() -> None:
        state.notify()
        revision = state.revision()
        first = await state.payload(revision, build)
        second = await state.payload(revision, build)
        assert first is second
        assert len(calls) == 1

        state.notify()
        next_revision = state.revision()
        results = await asyncio.gather(
            state.payload(next_revision, build),
            state.payload(next_revision, build),
            state.payload(next_revision, build),
        )
        assert all(result is results[0] for result in results)
        assert len(calls) == 2

    asyncio.run(exercise())


def test_dashboard_stream_closes_when_session_revoked(app, client, admin, monkeypatch):
    monkeypatch.setattr(server_module, "DASHBOARD_AUTH_RECHECK_SECONDS", 0.2)
    cookie = client.cookies.get("rc_session")
    events: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_read_dashboard_until_closed, args=(app, cookie, events), daemon=True)
    reader.start()

    event, payload = events.get(timeout=3)
    assert event == "dashboard"
    assert payload is not None

    app.state.db.revoke_session(_session_id(admin))
    event, payload = events.get(timeout=3)
    assert event == "auth"
    assert payload == {"authenticated": False}
    assert events.get(timeout=3) == ("closed", None)
    reader.join(timeout=2)
    assert not reader.is_alive()


def test_dashboard_stream_closes_when_session_expires(app, client, admin, monkeypatch):
    monkeypatch.setattr(server_module, "DASHBOARD_AUTH_RECHECK_SECONDS", 0.2)
    cookie = client.cookies.get("rc_session")
    events: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_read_dashboard_until_closed, args=(app, cookie, events), daemon=True)
    reader.start()

    event, _ = events.get(timeout=3)
    assert event == "dashboard"

    session_id = _session_id(admin)
    with app.state.db.connection() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", session_id),
        )
    event, payload = events.get(timeout=3)
    assert event == "auth"
    assert payload == {"authenticated": False}
    assert events.get(timeout=3) == ("closed", None)
    reader.join(timeout=2)
    assert not reader.is_alive()


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
