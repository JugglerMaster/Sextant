from __future__ import annotations

from fastapi.testclient import TestClient

from runner_control.db import dumps
from tests.helpers import enroll_agent, headers, login


def _insert_command(
    app,
    agent_id: str,
    command_id: str,
    *,
    kind: str = "shell",
    args: dict | None = None,
    status: str = "succeeded",
    created_at: str = "2026-01-01T00:00:00Z",
    completed_at: str | None = None,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
) -> None:
    with app.state.db.transaction() as conn:
        conn.execute(
            """INSERT INTO commands
               (id, agent_id, kind, args, status, created_by, created_at,
                completed_at, exit_code, stdout, stderr, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                command_id,
                agent_id,
                kind,
                dumps(args or {}),
                status,
                "admin",
                created_at,
                completed_at,
                exit_code,
                stdout,
                stderr,
                error,
            ),
        )


def _seed_history(app, admin):
    agent = enroll_agent(admin)
    for day in range(1, 6):
        _insert_command(
            app,
            agent["agent_id"],
            f"cmd_{day}",
            kind="shell",
            args={"shell": "bash", "command": f"needle-args-{day}"},
            status="failed" if day == 4 else "succeeded",
            created_at=f"2026-01-0{day}T12:00:00Z",
            completed_at=f"2026-01-0{day}T12:00:05Z",
            exit_code=1 if day == 4 else 0,
            stdout=f"stdout line {day}",
            stderr="needle-stderr" if day == 2 else "",
            error="needle-error" if day == 3 else "",
        )
    return agent


def test_command_history_pagination_and_total(client, admin, app):
    agent = _seed_history(app, admin)
    path = f"/api/agents/{agent['agent_id']}/commands"

    first = client.get(path, params={"limit": 2, "offset": 0})
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["total"] == 5
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert [row["id"] for row in data["commands"]] == ["cmd_5", "cmd_4"]

    second = client.get(path, params={"limit": 2, "offset": 2})
    assert second.status_code == 200, second.text
    assert second.json()["total"] == 5
    assert [row["id"] for row in second.json()["commands"]] == ["cmd_3", "cmd_2"]

    last = client.get(path, params={"limit": 2, "offset": 4})
    assert last.status_code == 200, last.text
    assert [row["id"] for row in last.json()["commands"]] == ["cmd_1"]

    beyond = client.get(path, params={"limit": 2, "offset": 50})
    assert beyond.status_code == 200, beyond.text
    assert beyond.json()["commands"] == []
    assert beyond.json()["total"] == 5


def test_command_history_status_and_text_search(client, admin, app):
    agent = _seed_history(app, admin)
    path = f"/api/agents/{agent['agent_id']}/commands"

    failed = client.get(path, params={"status": "failed"})
    assert failed.status_code == 200, failed.text
    assert failed.json()["total"] == 1
    assert failed.json()["commands"][0]["id"] == "cmd_4"

    by_args = client.get(path, params={"q": "needle-args-2"})
    assert by_args.status_code == 200, by_args.text
    assert by_args.json()["total"] == 1
    assert by_args.json()["commands"][0]["id"] == "cmd_2"

    by_stderr = client.get(path, params={"q": "needle-stderr"})
    assert by_stderr.status_code == 200, by_stderr.text
    assert [row["id"] for row in by_stderr.json()["commands"]] == ["cmd_2"]

    by_error = client.get(path, params={"q": "needle-error"})
    assert by_error.status_code == 200, by_error.text
    assert [row["id"] for row in by_error.json()["commands"]] == ["cmd_3"]

    by_stdout = client.get(path, params={"q": "stdout line 5"})
    assert by_stdout.status_code == 200, by_stdout.text
    assert [row["id"] for row in by_stdout.json()["commands"]] == ["cmd_5"]

    all_statuses = client.get(path, params={"status": "all"})
    assert all_statuses.status_code == 200, all_statuses.text
    assert all_statuses.json()["total"] == 5


def test_command_history_since_and_until(client, admin, app):
    agent = _seed_history(app, admin)
    path = f"/api/agents/{agent['agent_id']}/commands"

    window = client.get(
        path,
        params={"since": "2026-01-02T00:00:00Z", "until": "2026-01-04T23:59:59Z"},
    )
    assert window.status_code == 200, window.text
    assert [row["id"] for row in window.json()["commands"]] == ["cmd_4", "cmd_3", "cmd_2"]

    normalized = client.get(
        path,
        params={"since": "2026-01-04T00:00:00+00:00", "until": "2026-01-05T23:59:59+00:00"},
    )
    assert normalized.status_code == 200, normalized.text
    assert [row["id"] for row in normalized.json()["commands"]] == ["cmd_5", "cmd_4"]


def test_command_history_validates_ranges_and_timestamps(client, admin, app):
    agent = _seed_history(app, admin)
    path = f"/api/agents/{agent['agent_id']}/commands"

    for params in (
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"status": "unknown"},
        {"since": "not-a-date"},
        {"until": "not-a-date"},
        {"since": "2026-02-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
    ):
        response = client.get(path, params=params)
        assert response.status_code == 400, (params, response.text)

    missing = client.get("/api/agents/agt_missing/commands", params={"limit": 10})
    assert missing.status_code == 404


def test_command_history_read_access_for_viewer_and_anonymous(app, client, admin):
    agent = _seed_history(app, admin)
    path = f"/api/agents/{agent['agent_id']}/commands"

    with TestClient(app) as anonymous_client:
        assert anonymous_client.get(path).status_code == 401

    created = client.post(
        "/api/users",
        json={
            "username": "historyreader",
            "display_name": "History Reader",
            "role": "viewer",
            "password": "viewer password 123",
        },
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text

    with TestClient(app) as viewer_client:
        login(viewer_client, "historyreader", "viewer password 123")
        response = viewer_client.get(path, params={"limit": 2, "status": "succeeded"})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 4
        assert len(response.json()["commands"]) == 2
