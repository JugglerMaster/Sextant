from __future__ import annotations

import csv
import io

from runner_control.db import dumps
from tests.helpers import enroll_agent, heartbeat, headers, sample_metrics


def _insert_command(
    app,
    agent_id: str,
    command_id: str,
    *,
    status: str = "succeeded",
    created_at: str = "2026-01-01T00:00:00Z",
    stdout: str = "",
    stderr: str = "",
    error: str = "",
    args: dict | None = None,
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
                "shell",
                dumps(args or {"shell": "bash", "command": command_id}),
                status,
                "admin",
                created_at,
                created_at,
                0 if status == "succeeded" else 1,
                stdout,
                stderr,
                error,
            ),
        )


def test_agents_csv_export(client, admin):
    enroll_agent(admin, hostname="csv-box")
    response = client.get("/api/export/agents.csv", headers=headers(admin))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "id,name,hostname" in response.text
    assert "csv-box" in response.text


def test_packages_csv_export(client, admin):
    agent = enroll_agent(admin)
    heartbeat(
        admin,
        agent,
        sample_metrics(
            packages=[{"manager": "apt", "package": "nginx", "version": "1.24", "arch": "amd64"}],
            package_managers=[{"name": "apt", "version": "2.7"}],
            package_inventory_hash="csvhash",
            package_inventory_full=True,
        ),
    )
    response = client.get(f"/api/export/packages.csv?agent_id={agent['agent_id']}", headers=headers(admin))
    assert response.status_code == 200
    assert "agent_id,agent_name,manager,package" in response.text
    assert "nginx" in response.text
    assert "1.24" in response.text


def test_commands_csv_export_filters_and_quotes(client, admin, app):
    agent = enroll_agent(admin, hostname="csv-history-box")
    other = enroll_agent(admin, hostname="other-history-box")
    _insert_command(
        app,
        agent["agent_id"],
        "cmd_failed",
        status="failed",
        stdout='He said "hello", ok\nline two',
        stderr='err, "quoted"',
        error='bad, "thing"',
        args={"shell": "bash", "command": "printf 'needle, \"quote\"'"},
    )
    _insert_command(
        app,
        agent["agent_id"],
        "cmd_succeeded",
        status="succeeded",
        stdout="plain success",
    )
    _insert_command(app, other["agent_id"], "cmd_other", status="failed", stdout="other agent data")

    response = client.get(
        "/api/export/commands.csv",
        params={"agent_id": agent["agent_id"], "status": "failed"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.startswith("created_at,completed_at,kind,status,exit_code,description,args,stdout,stderr,error")

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["agent_id"] == agent["agent_id"]
    assert rows[0]["status"] == "failed"
    assert rows[0]["stdout"] == 'He said "hello", ok\nline two'
    assert rows[0]["stderr"] == 'err, "quoted"'
    assert rows[0]["error"] == 'bad, "thing"'
    assert "needle" in rows[0]["args"]


def test_commands_csv_export_text_and_time_filters(client, admin, app):
    agent = enroll_agent(admin, hostname="csv-filter-box")
    _insert_command(
        app,
        agent["agent_id"],
        "cmd_jan",
        status="succeeded",
        created_at="2026-01-01T00:00:00Z",
        stdout="alpha only",
    )
    _insert_command(
        app,
        agent["agent_id"],
        "cmd_feb",
        status="succeeded",
        created_at="2026-02-01T00:00:00Z",
        stdout="beta unique needle",
    )

    text = client.get(
        "/api/export/commands.csv",
        params={"agent_id": agent["agent_id"], "q": "unique needle"},
    )
    assert text.status_code == 200, text.text
    rows = list(csv.DictReader(io.StringIO(text.text)))
    assert [row["stdout"] for row in rows] == ["beta unique needle"]

    window = client.get(
        "/api/export/commands.csv",
        params={
            "agent_id": agent["agent_id"],
            "since": "2026-01-15T00:00:00Z",
            "until": "2026-02-15T00:00:00Z",
        },
    )
    assert window.status_code == 200, window.text
    rows = list(csv.DictReader(io.StringIO(window.text)))
    assert [row["stdout"] for row in rows] == ["beta unique needle"]

    invalid = client.get("/api/export/commands.csv", params={"limit": 0})
    assert invalid.status_code == 400
