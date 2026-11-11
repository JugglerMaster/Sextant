from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import enroll_agent, headers, heartbeat, login


def _create_user(client: TestClient, admin: dict, username: str, role: str) -> dict:
    response = client.post(
        "/api/users",
        json={
            "username": username,
            "display_name": username.title(),
            "role": role,
            "password": "bulk password 123",
        },
        headers=headers(admin),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bulk(client: TestClient, session: dict, action: str, agent_ids: list[str], args: dict | None = None):
    return client.post(
        "/api/agents/bulk",
        json={"action": action, "agent_ids": agent_ids, "args": args or {}},
        headers=headers(session),
    )


def test_bulk_partial_results_validation_and_audit(client, admin):
    first = enroll_agent(admin, hostname="bulk-a", machine_id="bulk-a")
    second = enroll_agent(admin, hostname="bulk-b", machine_id="bulk-b")
    heartbeat(admin, first)
    heartbeat(admin, second)

    response = _bulk(
        client,
        admin,
        "refresh",
        [first["agent_id"], second["agent_id"], "agt_missing"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "refresh"
    assert body["summary"] == {"total": 3, "queued": 2, "sent": 0, "updated": 0, "failed": 1}
    assert len(body["results"]) == 3
    by_id = {item["agent_id"]: item for item in body["results"]}
    assert by_id[first["agent_id"]]["status"] == "queued"
    assert by_id[first["agent_id"]]["name"] == "bulk-a"
    assert by_id[first["agent_id"]]["command_id"].startswith("cmd_")
    assert by_id["agt_missing"]["status"] == "failed"
    assert "not found" in by_id["agt_missing"]["message"].lower()
    for item in body["results"]:
        assert {"agent_id", "name", "status", "message"} <= set(item)

    assert _bulk(client, admin, "refresh", []).status_code == 422
    assert _bulk(client, admin, "refresh", [f"agt_{index}" for index in range(201)]).status_code == 422
    assert _bulk(client, admin, "not-a-real-action", [first["agent_id"]]).status_code == 422
    missing_name = _bulk(client, admin, "service_restart", [first["agent_id"]])
    assert missing_name.status_code == 400
    unknown_profile = _bulk(
        client,
        admin,
        "profile_apply",
        [first["agent_id"]],
        {"profile": "does-not-exist"},
    )
    assert unknown_profile.status_code == 400

    audit = client.get("/api/audit").json()["events"]
    bulk_events = [event for event in audit if event["action"] == "agents.bulk"]
    assert bulk_events
    assert bulk_events[0]["detail"]["bulk_id"] == body["bulk_id"]
    assert bulk_events[0]["detail"]["summary"]["failed"] == 1
    command_events = [
        event
        for event in audit
        if event["action"] == "command.created"
        and event["detail"].get("bulk_id") == body["bulk_id"]
    ]
    assert len(command_events) == 2


def test_bulk_permissions_and_supported_actions(app, client, admin, monkeypatch):
    first = enroll_agent(admin, hostname="bulk-c", machine_id="bulk-c")
    second = enroll_agent(admin, hostname="bulk-d", machine_id="bulk-d")
    heartbeat(admin, first, {"hostname": "bulk-c", "os": "linux", "cpu_pct": 10})
    heartbeat(admin, second, {"hostname": "bulk-d", "os": "linux", "cpu_pct": 10})

    service = _bulk(
        client,
        admin,
        "service_restart",
        [first["agent_id"]],
        {"name": "nginx.service"},
    )
    assert service.status_code == 200, service.text
    assert service.json()["results"][0]["status"] == "queued"
    alias = _bulk(
        client,
        admin,
        "service",
        [second["agent_id"]],
        {"action": "start", "name": "nginx.service"},
    )
    assert alias.status_code == 200, alias.text
    assert alias.json()["action"] == "service_start"
    heartbeat(admin, first, {"hostname": "bulk-c", "os": "linux", "cpu_pct": 10})
    detail = client.get(f"/api/agents/{first['agent_id']}").json()
    queued = [item for item in detail["commands"] if item["status"] in {"queued", "dispatched"}]
    assert queued and queued[0]["kind"] == "service"
    assert queued[0]["args"] == {"action": "restart", "name": "nginx.service"}

    power = _bulk(
        client,
        admin,
        "reboot",
        [first["agent_id"]],
        {"delay": 45},
    )
    assert power.status_code == 200, power.text
    assert power.json()["results"][0]["status"] == "queued"

    monkeypatch.setattr("runner_control.server.send_magic_packet", lambda *args, **kwargs: 3)
    wake = _bulk(client, admin, "wake", [first["agent_id"]])
    assert wake.status_code == 200, wake.text
    assert wake.json()["results"][0]["status"] == "sent"

    profile = _bulk(
        client,
        admin,
        "profile_apply",
        [first["agent_id"], second["agent_id"]],
        {"profile": "server"},
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["summary"]["updated"] == 2

    _create_user(client, admin, "bulkoperator", "operator")
    _create_user(client, admin, "bulkviewer", "viewer")
    with TestClient(app) as operator_client, TestClient(app) as viewer_client:
        operator = login(operator_client, "bulkoperator", "bulk password 123")
        viewer = login(viewer_client, "bulkviewer", "bulk password 123")
        assert _bulk(operator_client, operator, "refresh", [first["agent_id"]]).status_code == 200
        assert (
            _bulk(
                operator_client,
                operator,
                "profile_apply",
                [first["agent_id"]],
                {"profile": "workstation"},
            ).status_code
            == 403
        )
        assert _bulk(viewer_client, viewer, "refresh", [first["agent_id"]]).status_code == 403
        assert _bulk(viewer_client, viewer, "wake", [first["agent_id"]]).status_code == 403


def test_saved_views_crud_user_isolation_and_filter_validation(app, client, admin):
    _create_user(client, admin, "vieweralice", "operator")
    _create_user(client, admin, "viewerbob", "viewer")

    with TestClient(app) as anon:
        assert anon.get("/api/saved-views").status_code == 401

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        alice = login(alice_client, "vieweralice", "bulk password 123")
        bob = login(bob_client, "viewerbob", "bulk password 123")

        assert alice_client.get("/api/saved-views").json()["views"] == []
        created = alice_client.post(
            "/api/saved-views",
            json={
                "name": "GPU hosts",
                "filters": {"status": "online", "profile": "prof_server", "tag": "gpu", "search": "studio"},
            },
            headers=headers(alice),
        )
        assert created.status_code == 200, created.text
        view = created.json()["view"]
        assert view["name"] == "GPU hosts"
        assert view["filters"] == {
            "status": "online",
            "profile": "prof_server",
            "tag": "gpu",
            "search": "studio",
        }
        assert view["id"].startswith("sv_")
        assert len(alice_client.get("/api/saved-views").json()["views"]) == 1

        duplicate = alice_client.post(
            "/api/saved-views",
            json={"name": "gpu hosts", "filters": {"status": "all"}},
            headers=headers(alice),
        )
        assert duplicate.status_code == 409
        assert len(alice_client.get("/api/saved-views").json()["views"]) == 1

        invalid_status = alice_client.post(
            "/api/saved-views",
            json={"name": "Bad status", "filters": {"status": "exploded"}},
            headers=headers(alice),
        )
        assert invalid_status.status_code == 400
        unknown_filter = alice_client.post(
            "/api/saved-views",
            json={"name": "Bad filter", "filters": {"color": "red"}},
            headers=headers(alice),
        )
        assert unknown_filter.status_code == 400
        blank_name = alice_client.post(
            "/api/saved-views",
            json={"name": "   ", "filters": {"status": "all"}},
            headers=headers(alice),
        )
        assert blank_name.status_code == 400

        assert bob_client.get("/api/saved-views").json()["views"] == []
        assert bob_client.delete(f"/api/saved-views/{view['id']}", headers=headers(bob)).status_code == 404
        bob_created = bob_client.post(
            "/api/saved-views",
            json={"name": "GPU hosts", "filters": {"status": "offline"}},
            headers=headers(bob),
        )
        assert bob_created.status_code == 200, bob_created.text
        assert bob_created.json()["view"]["filters"]["status"] == "offline"
        assert len(bob_client.get("/api/saved-views").json()["views"]) == 1

        assert alice_client.delete(f"/api/saved-views/{view['id']}", headers=headers(alice)).status_code == 204
        assert alice_client.get("/api/saved-views").json()["views"] == []
        assert len(bob_client.get("/api/saved-views").json()["views"]) == 1

    actions = [event["action"] for event in client.get("/api/audit").json()["events"]]
    assert "saved_view.created" in actions
    assert "saved_view.deleted" in actions
