from __future__ import annotations

import base64
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from sextant_agent.config import AgentConfig
from sextant_agent.console import ConsoleError, ConsoleManager
from sextant_control.docker_console import ConsoleBroker, ConsoleError as BrokerError
from sextant_control.server import create_app
from tests.helpers import enroll_agent, headers, login
from tests.test_docker_api import (
    container_row,
    enable_docker_profile,
    enroll_windows_agent,
    upload_inventory,
)


def test_broker_lifecycle_and_bounds():
    broker = ConsoleBroker(max_per_agent=1, max_input_bytes=1024, max_output_bytes=4096)
    broker.create("con_one", agent_id="agt_1", created_by="alice", rows=32, cols=100)
    state = broker.get("con_one")
    assert state is not None
    assert state.status == "opening"
    assert broker.push_input("con_one", b"hello") == "ok"
    assert broker.push_input("con_one", b"x") == "rate"
    time.sleep(0.02)
    assert broker.push_input("con_one", b"x" * 1020) == "full"
    assert broker.push_resize("con_one", 40, 120) == "ok"
    assert broker.push_output("con_one", b"abcdef") is True
    assert broker.push_output("con_one", b"x" * 5000) is True
    # Total output is bounded.
    assert broker.get("con_one").output_bytes <= 4096

    broker.mark_open("con_one")
    assert broker.status("con_one")["status"] == "open"
    polled = broker.poll("con_one", timeout=1)
    assert polled["type"] == "input"
    assert base64.b64decode(polled["data"]) == b"hello"
    assert broker.poll("con_one", timeout=0.01)["type"] == "resize"
    broker.finish("con_one", status="exited", exit_code=0)
    assert broker.poll("con_one", timeout=0.01)["type"] == "close"


def test_broker_max_sessions():
    broker = ConsoleBroker(max_per_agent=1)
    broker.create("con_one", agent_id="agt_1", created_by="alice", rows=32, cols=100)
    with pytest.raises(BrokerError):
        broker.create("con_two", agent_id="agt_1", created_by="bob", rows=32, cols=100)


def test_broker_idle_and_missed_poll_pruning():
    broker = ConsoleBroker()
    broker.create("con_idle", agent_id="agt_1", created_by="alice", rows=32, cols=100)
    state = broker.get("con_idle")
    state.last_activity = time.monotonic() - 100
    closed = broker.prune(idle_seconds=10, poll_seconds=25)
    assert closed == [("con_idle", "failed", "Console idle timeout.")]
    assert broker.status("con_idle")["status"] == "failed"

    broker.create("con_missed", agent_id="agt_2", created_by="alice", rows=32, cols=100)
    state = broker.get("con_missed")
    state.last_poll = time.monotonic() - 100
    closed = broker.prune(idle_seconds=1000, poll_seconds=25)
    assert closed == [("con_missed", "failed", "Agent console poll missed.")]


def test_broker_user_close_and_status_contains_no_transcript():
    broker = ConsoleBroker()
    broker.create("con_one", agent_id="agt_1", created_by="alice", rows=32, cols=100)
    broker.push_output("con_one", b"secret terminal output")
    assert broker.close_for_user("alice") == 1
    status = broker.status("con_one")
    assert status["status"] == "closed"
    assert "secret" not in repr(status)


def test_console_argv_validation():
    assert ConsoleManager.build_argv(
        "a" * 64,
        {"shell": "bash", "user": "root", "workdir": "/srv"},
    ) == [
        "exec",
        "-it",
        "-u",
        "root",
        "-w",
        "/srv",
        "-e",
        "TERM=xterm-256color",
        "a" * 64,
        "bash",
        "-l",
    ]
    for args in (
        {"shell": "zsh"},
        {"shell": "sh", "user": "bad user"},
        {"shell": "sh", "workdir": "relative"},
    ):
        with pytest.raises(ConsoleError):
            ConsoleManager.build_argv("a" * 64, args)


class FakeConsoleClient:
    def __init__(self):
        self.outputs: list[bytes] = []
        self.output_seen = threading.Event()
        self.closed = threading.Event()
        self._input_sent = False

    def console_poll(self, session_id, timeout=30):
        if not self._input_sent:
            self._input_sent = True
            return {"type": "input", "data": base64.b64encode(b"hello\n").decode("ascii")}
        time.sleep(0.05)
        return {}

    def send_console_output(self, session_id, data):
        self.outputs.append(data)
        self.output_seen.set()

    def send_console_closed(self, session_id, exit_code, error=""):
        self.closed.set()


def test_console_manager_pty_round_trip_and_cleanup(monkeypatch):
    config = AgentConfig(
        server_url="https://example.test",
        agent_id="agt_1",
        agent_token="token",
        docker_command=sys.executable,
        docker_console_max_sessions=1,
        docker_console_idle_seconds=60,
    )
    monkeypatch.setattr(
        ConsoleManager,
        "build_argv",
        staticmethod(lambda _container_id, _args: ["-c", "import sys; sys.stdout.write(sys.stdin.readline())"]),
    )
    client = FakeConsoleClient()
    manager = ConsoleManager(config, client, threading.Event())
    manager.open(
        "con_test",
        "a" * 64,
        {"shell": "sh", "rows": 32, "cols": 100},
        {},
    )
    assert manager.active_count() == 1
    assert client.output_seen.wait(5)
    assert any(b"hello" in chunk for chunk in client.outputs)
    manager.close("con_test")
    assert client.closed.wait(5)
    assert manager.active_count() == 0


def _open_console(client, admin, agent, container_id, **overrides):
    body = {"shell": "sh", "user": "", "workdir": "/", "rows": 32, "cols": 100}
    body.update(overrides)
    return client.post(
        f"/api/docker/hosts/{agent['agent_id']}/containers/{container_id}/console",
        json=body,
        headers=headers(admin),
    )


def test_console_api_lifecycle_ownership_and_no_transcript(app, client, admin):
    enable_docker_profile(client, admin, exec_allowed=True)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    container_id = container_row(1)["id"]

    opened = _open_console(client, admin, agent, container_id)
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session_id"]
    assert session_id.startswith("con_")

    client.post(
        "/api/users",
        json={"username": "console-other", "role": "operator", "password": "operator password 123"},
        headers=headers(admin),
    )
    with TestClient(app) as other_client:
        other = login(other_client, "console-other", "operator password 123")
        denied_input = other_client.post(
            f"/api/docker/console/{session_id}/input",
            json={"data": base64.b64encode(b"id\n").decode("ascii")},
            headers=headers(other),
        )
        assert denied_input.status_code == 403
        denied_events = other_client.get(f"/api/docker/console/{session_id}/events")
        assert denied_events.status_code == 403

    supplied = client.post(
        f"/api/docker/console/{session_id}/input",
        json={"data": base64.b64encode(b"id\n").decode("ascii")},
        headers=headers(admin),
    )
    assert supplied.status_code == 204, supplied.text

    polled = client.post(
        f"/api/agent/{agent['agent_id']}/docker/console/{session_id}/poll",
        json={},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert polled.status_code == 200, polled.text
    assert polled.json()["type"] == "input"
    assert base64.b64decode(polled.json()["data"]) == b"id\n"

    output = client.post(
        f"/api/agent/{agent['agent_id']}/docker/console/{session_id}/output",
        json={"data": base64.b64encode(b"uid=0(root)\n").decode("ascii")},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert output.status_code == 204

    resized = client.post(
        f"/api/docker/console/{session_id}/resize",
        json={"rows": 40, "cols": 120},
        headers=headers(admin),
    )
    assert resized.status_code == 204

    # The SSE endpoint reports current status and archived in-memory output.
    with client.stream("GET", f"/api/docker/console/{session_id}/events") as response:
        assert response.status_code == 200
        prefix = []
        for line in response.iter_lines():
            prefix.append(line)
            if len(prefix) >= 6:
                break
        text = "\n".join(prefix)
        assert "event: status" in text
        assert "uid=0(root)" not in text  # output is base64 on the wire

    closed = client.delete(f"/api/docker/console/{session_id}", headers=headers(admin))
    assert closed.status_code == 204
    close_signal = client.post(
        f"/api/agent/{agent['agent_id']}/docker/console/{session_id}/poll",
        json={},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert close_signal.json()["type"] == "close"
    agent_closed = client.post(
        f"/api/agent/{agent['agent_id']}/docker/console/{session_id}/closed",
        json={"exit_code": 0, "error": ""},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert agent_closed.status_code == 204
    row = app.state.db.get_console_session(session_id)
    assert row["status"] == "closed"
    assert row["exit_code"] == 0

    with app.state.db.connection() as conn:
        columns = {
            str(item["name"])
            for item in conn.execute("PRAGMA table_info(docker_console_sessions)").fetchall()
        }
        audits = conn.execute(
            "SELECT detail FROM audit_log WHERE action LIKE 'docker.console%'"
        ).fetchall()
    assert not ({"input", "output", "stdout", "stderr", "transcript"} & columns)
    assert all("id\\n" not in str(item["detail"]) and "uid=0" not in str(item["detail"]) for item in audits)


def test_console_api_gates_caps_and_invalid_input(client, admin):
    enable_docker_profile(client, admin, exec_allowed=False)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    container_id = container_row(1)["id"]
    denied = _open_console(client, admin, agent, container_id)
    assert denied.status_code == 409

    enable_docker_profile(client, admin, exec_allowed=True)
    # The existing enrolled agent keeps its original policy; enable the profile
    # and re-enroll the same machine to exercise the open path.
    agent = enroll_agent(admin, machine_id="console-caps-machine")
    assert upload_inventory(client, admin, agent).status_code == 200
    opened = _open_console(client, admin, agent, container_id)
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session_id"]

    too_large = client.post(
        f"/api/docker/console/{session_id}/input",
        json={"data": base64.b64encode(b"x" * 70000).decode("ascii")},
        headers=headers(admin),
    )
    assert too_large.status_code == 413
    bad_resize = client.post(
        f"/api/docker/console/{session_id}/resize",
        json={"rows": 2, "cols": 5},
        headers=headers(admin),
    )
    assert bad_resize.status_code == 422
    bad_shell = _open_console(client, admin, agent, container_id, shell="zsh")
    assert bad_shell.status_code == 422


def test_console_rejects_windows_agent(client, admin):
    enable_docker_profile(client, admin, exec_allowed=True)
    windows = enroll_windows_agent(client, admin)
    response = _open_console(client, admin, windows, "a" * 64)
    assert response.status_code == 404


def test_console_server_restart_marks_open_sessions_failed(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        admin = login(client)
        enable_docker_profile(client, admin, exec_allowed=True)
        agent = enroll_agent(admin)
        app.state.db.create_console_session(
            "con_restart",
            agent_id=agent["agent_id"],
            container_id="a" * 64,
            shell="sh",
            user="",
            tty=True,
            created_by="admin",
        )
    restarted = create_app(settings)
    row = restarted.state.db.get_console_session("con_restart")
    assert row["status"] == "failed"
    assert "restarted" in row["error"].lower()
