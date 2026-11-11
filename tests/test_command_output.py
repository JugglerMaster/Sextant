from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from sextant_control.server import create_app
from tests.helpers import enroll_agent, headers, heartbeat, login


def _queue(session, agent_id: str, command: str = "printf hello") -> dict:
    response = session["client"].post(
        f"/api/agents/{agent_id}/commands",
        json={"kind": "shell", "args": {"shell": "bash", "command": command, "timeout": 30}},
        headers=headers(session),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _dispatch(session, agent: dict) -> dict:
    dispatched = heartbeat(session, agent)
    assert dispatched["commands"]
    return dispatched["commands"][0]


def _post_output(client, agent: dict, command_id: str, stream: str, seq: int, text: str):
    return client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command_id}/output",
        json={"stream": stream, "seq": seq, "text": text},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )


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


def test_output_endpoint_auth_ownership_ordering_and_terminal_state(client, admin):
    first = enroll_agent(admin, hostname="first-box", machine_id="output-first")
    second = enroll_agent(admin, hostname="second-box", machine_id="output-second")
    command = _queue(admin, first["agent_id"])

    missing_auth = client.post(
        f"/api/agent/{first['agent_id']}/commands/{command['id']}/output",
        json={"stream": "stdout", "seq": 1, "text": "hello"},
    )
    assert missing_auth.status_code == 401
    bad_token = client.post(
        f"/api/agent/{first['agent_id']}/commands/{command['id']}/output",
        json={"stream": "stdout", "seq": 1, "text": "hello"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert bad_token.status_code == 401

    queued = _post_output(client, first, command["id"], "stdout", 1, "hello")
    assert queued.status_code == 409
    cross_agent = _post_output(client, second, command["id"], "stdout", 1, "hello")
    assert cross_agent.status_code == 404

    _dispatch(admin, first)

    accepted = _post_output(client, first, command["id"], "stdout", 1, "hello ")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["seq"] == 1
    assert accepted.json()["idempotent"] is False
    assert client.get(f"/api/commands/{command['id']}").json()["command"]["status"] == "dispatched"

    duplicate = _post_output(client, first, command["id"], "stdout", 1, "hello ")
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["idempotent"] is True

    conflict = _post_output(client, first, command["id"], "stdout", 1, "different")
    assert conflict.status_code == 409

    second_chunk = _post_output(client, first, command["id"], "stdout", 2, "world\n")
    assert second_chunk.status_code == 200, second_chunk.text
    stderr_independent = _post_output(client, first, command["id"], "stderr", 1, "warning\n")
    assert stderr_independent.status_code == 200, stderr_independent.text

    out_of_order = _post_output(client, first, command["id"], "stdout", 1, "old\n")
    assert out_of_order.status_code == 409
    assert "sequence" in out_of_order.json()["detail"].lower()

    result = client.post(
        f"/api/agent/{first['agent_id']}/commands/{command['id']}/result",
        json={
            "status": "succeeded",
            "exit_code": 0,
            "stdout": "hello world\n",
            "stderr": "warning\n",
            "error": "",
        },
        headers={"Authorization": f"Bearer {first['agent_token']}"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["command"]["stdout"] == "hello world\n"

    after_terminal = _post_output(client, first, command["id"], "stdout", 3, "late\n")
    assert after_terminal.status_code == 409
    assert "finished" in after_terminal.json()["detail"].lower()


def test_output_endpoint_enforces_chunk_and_command_caps(settings):
    app = create_app(replace(settings, command_output_limit=1024))
    with TestClient(app) as client:
        admin = login(client)
        agent = enroll_agent(admin)
        command = _queue(admin, agent["agent_id"])
        _dispatch(admin, agent)

        first = _post_output(client, agent, command["id"], "stdout", 1, "x" * 800)
        assert first.status_code == 200, first.text
        over_total = _post_output(client, agent, command["id"], "stdout", 2, "y" * 800)
        assert over_total.status_code == 413
        assert "limit" in over_total.json()["detail"].lower()

        too_large = _post_output(client, agent, command["id"], "stderr", 1, "z" * (64 * 1024 + 1))
        assert too_large.status_code == 413


def test_command_output_chunks_are_pruned_with_command_retention(client, admin, app):
    agent = enroll_agent(admin)
    command = _queue(admin, agent["agent_id"])
    _dispatch(admin, agent)
    assert _post_output(client, agent, command["id"], "stdout", 1, "hello\n").status_code == 200
    result = client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command['id']}/result",
        json={"status": "succeeded", "exit_code": 0, "stdout": "hello\n", "stderr": "", "error": ""},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert result.status_code == 200, result.text

    with app.state.db.connection() as conn:
        conn.execute(
            "UPDATE commands SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?",
            (command["id"],),
        )
    pruned = app.state.db.prune_commands(1)
    assert pruned == 1
    with app.state.db.connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM commands WHERE id = ?", (command["id"],)
        ).fetchone() is None
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM command_output WHERE command_id = ?", (command["id"],)
        ).fetchone()["n"] == 0


def test_command_events_replay_output_then_close(client, admin):
    agent = enroll_agent(admin)
    command = _queue(admin, agent["agent_id"])
    _dispatch(admin, agent)
    assert _post_output(client, agent, command["id"], "stdout", 1, "hello\n").status_code == 200
    assert _post_output(client, agent, command["id"], "stderr", 1, "warning\n").status_code == 200
    result = client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command['id']}/result",
        json={
            "status": "succeeded",
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": "warning\n",
            "error": "",
        },
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert result.status_code == 200, result.text

    events: list[tuple[str | None, dict | None]] = []
    with client.stream("GET", f"/api/commands/{command['id']}/events") as response:
        assert response.status_code == 200
        lines = response.iter_lines()
        while True:
            event, payload = _next_event(lines)
            if event is None:
                break
            events.append((event, payload))

    assert events[0][0] == "command"
    assert events[0][1]["command"]["status"] == "succeeded"
    output_events = [payload for event, payload in events if event == "output"]
    assert output_events == [
        {"command_id": command["id"], "stream": "stdout", "seq": 1, "text": "hello\n"},
        {"command_id": command["id"], "stream": "stderr", "seq": 1, "text": "warning\n"},
    ]
    assert events[-1][0] == "output"


def test_command_events_emit_live_output_and_terminal_event(app, client, admin):
    agent = enroll_agent(admin)
    command = _queue(admin, agent["agent_id"])
    _dispatch(admin, agent)

    def produce_output() -> None:
        worker = TestClient(app)
        time.sleep(0.2)
        worker.post(
            f"/api/agent/{agent['agent_id']}/commands/{command['id']}/output",
            json={"stream": "stdout", "seq": 1, "text": "live\n"},
            headers={"Authorization": f"Bearer {agent['agent_token']}"},
        )
        time.sleep(1.2)
        worker.post(
            f"/api/agent/{agent['agent_id']}/commands/{command['id']}/result",
            json={"status": "succeeded", "exit_code": 0, "stdout": "live\n", "stderr": "", "error": ""},
            headers={"Authorization": f"Bearer {agent['agent_token']}"},
        )
        worker.close()

    producer = threading.Thread(target=produce_output, daemon=True)
    producer.start()
    events: list[tuple[str | None, dict | None]] = []
    with client.stream("GET", f"/api/commands/{command['id']}/events") as response:
        assert response.status_code == 200
        lines = response.iter_lines()
        while True:
            event, payload = _next_event(lines)
            if event is None:
                break
            events.append((event, payload))
            if event == "command" and payload["command"]["status"] in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                break
    producer.join(timeout=5)

    assert any(
        event == "output" and payload["stream"] == "stdout" and payload["text"] == "live\n"
        for event, payload in events
    )
    terminal = [payload for event, payload in events if event == "command"]
    assert terminal[-1]["command"]["status"] == "succeeded"
    assert terminal[-1]["command"]["stdout"] == "live\n"
