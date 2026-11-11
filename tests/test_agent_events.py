from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tests.helpers import (
    enroll_agent,
    headers,
    heartbeat,
    login,
    sample_metrics,
)

VIEWER_PASSWORD = "event viewer password 123"


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _events(client, admin, agent_id: str, query: str = "") -> list[dict]:
    suffix = f"?{query}" if query else ""
    response = client.get(f"/api/agents/{agent_id}/events{suffix}", headers=headers(admin))
    assert response.status_code == 200, response.text
    return response.json()["events"]


def _package(manager: str, package: str, version: str) -> dict:
    return {
        "manager": manager,
        "package": package,
        "version": version,
        "arch": "amd64",
        "source": "",
    }


def _service(name: str, status: str, watched: bool = True) -> dict:
    return {
        "name": name,
        "display_name": name,
        "status": status,
        "startup": "enabled",
        "watched": watched,
    }


def _quiet_disk() -> list[dict]:
    return [{"mount": "/", "total": 100, "used": 50, "free": 50, "percent": 50, "fs": "ext4"}]


def test_heartbeat_events_are_transition_based_and_not_duplicated(client, admin, app):
    agent = enroll_agent(admin, watch_services=["Spooler"])
    agent_id = agent["agent_id"]
    base = sample_metrics(
        disks=_quiet_disk(),
        services=[_service("Spooler", "running")],
        packages=[_package("apt", "nginx", "1.24.0")],
        package_inventory_hash="hash-one",
    )

    heartbeat(admin, agent, base)
    initial = _events(client, admin, agent_id)
    assert [event["kind"] for event in initial] == ["package"]
    assert initial[0]["detail"]["action"] == "added"
    assert "nginx" in initial[0]["detail"]["packages"][0]

    heartbeat(admin, agent, base)
    assert len(_events(client, admin, agent_id)) == len(initial), "unchanged heartbeat duplicated an event"

    # Move last_seen outside the offline window so the next heartbeat is a return.
    old_seen = _iso(datetime.now(timezone.utc) - timedelta(minutes=10))
    with app.state.db.connection() as conn:
        conn.execute("UPDATE agents SET last_seen = ? WHERE id = ?", (old_seen, agent_id))
    heartbeat(admin, agent, base)
    online = [event for event in _events(client, admin, agent_id) if event["kind"] == "online"]
    assert len(online) == 1
    assert online[0]["detail"]["last_seen"] == old_seen

    heartbeat(admin, agent, base)
    assert len([event for event in _events(client, admin, agent_id) if event["kind"] == "online"]) == 1

    stopped = sample_metrics(
        disks=_quiet_disk(),
        services=[_service("Spooler", "stopped")],
        packages=[_package("apt", "nginx", "1.24.0")],
        package_inventory_hash="hash-one",
    )
    heartbeat(admin, agent, stopped)
    service_events = [event for event in _events(client, admin, agent_id) if event["kind"] == "service"]
    assert len(service_events) == 1
    assert service_events[0]["detail"] == {"service": "Spooler", "from": "running", "to": "stopped"}
    # A watched service going down also raises an alert exactly once.
    alerts = [event for event in _events(client, admin, agent_id) if event["kind"] == "alert"]
    assert len(alerts) == 1
    assert alerts[0]["detail"]["action"] == "raised"

    heartbeat(admin, agent, stopped)
    assert len([event for event in _events(client, admin, agent_id) if event["kind"] == "service"]) == 1
    assert len([event for event in _events(client, admin, agent_id) if event["kind"] == "alert"]) == 1

    upgraded = sample_metrics(
        agent_version="0.3.0",
        disks=_quiet_disk(),
        services=[_service("Spooler", "stopped")],
        packages=[_package("apt", "nginx", "1.25.0"), _package("apt", "curl", "8.5.0")],
        package_inventory_hash="hash-two",
    )
    heartbeat(admin, agent, upgraded)
    events = _events(client, admin, agent_id)
    version_events = [event for event in events if event["kind"] == "version"]
    assert len(version_events) == 1
    assert version_events[0]["detail"] == {"from": "0.2.0", "to": "0.3.0"}
    package_events = [event for event in events if event["kind"] == "package"]
    actions = {event["detail"].get("action") for event in package_events}
    assert {"added", "version_changed"} <= actions
    assert any("curl" in str(event["detail"]) for event in package_events)
    assert any("1.25.0" in str(event["detail"]) for event in package_events)

    # A profile PATCH records a persistent profile event without blocking the response.
    changed = client.patch(
        f"/api/agents/{agent_id}",
        json={"profile": "server"},
        headers=headers(admin),
    )
    assert changed.status_code == 200, changed.text
    profile_events = [event for event in _events(client, admin, agent_id) if event["kind"] == "profile"]
    assert len(profile_events) == 1
    assert profile_events[0]["detail"]["to"] == "prof_server"


def test_alert_actions_are_recorded(client, admin):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    heartbeat(
        admin,
        agent,
        sample_metrics(
            disks=[
                {"mount": "/", "total": 100, "used": 95, "free": 5, "percent": 95, "fs": "ext4"}
            ],
        ),
    )
    detail = client.get(f"/api/agents/{agent_id}", headers=headers(admin)).json()
    alert = next(item for item in detail["agent"]["alerts"] if item["key"] == "disk:!2F")
    alert_key = alert["key"]

    acknowledged = client.post(
        f"/api/agents/{agent_id}/alerts/{alert_key}/ack",
        headers=headers(admin),
    )
    assert acknowledged.status_code == 200, acknowledged.text
    snoozed = client.post(
        f"/api/agents/{agent_id}/alerts/{alert_key}/snooze",
        json={"minutes": 30},
        headers=headers(admin),
    )
    assert snoozed.status_code == 200, snoozed.text
    cleared = client.delete(
        f"/api/agents/{agent_id}/alerts/{alert_key}",
        headers=headers(admin),
    )
    assert cleared.status_code == 204, cleared.text

    actions = [
        event["detail"].get("action")
        for event in _events(client, admin, agent_id)
        if event["kind"] == "alert"
    ]
    assert "acknowledged" in actions
    assert "snoozed" in actions
    assert "cleared" in actions


def test_event_api_filters_limits_and_rbac(client, admin, app):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    now = datetime.now(timezone.utc)
    app.state.db.record_agent_event(
        agent_id,
        "service",
        "info",
        "Service old started",
        {"service": "old"},
        at=_iso(now - timedelta(hours=3)),
    )
    app.state.db.record_agent_event(
        agent_id,
        "service",
        "warning",
        "Service recent stopped",
        {"service": "recent"},
        at=_iso(now - timedelta(minutes=30)),
    )
    app.state.db.record_agent_event(
        agent_id,
        "version",
        "info",
        "Agent version changed",
        {"to": "9.9.9"},
        at=_iso(now - timedelta(minutes=5)),
    )

    recent = _events(client, admin, agent_id, "hours=1")
    assert [event["title"] for event in recent] == [
        "Agent version changed",
        "Service recent stopped",
    ]
    limited = _events(client, admin, agent_id, "hours=720&limit=1")
    assert len(limited) == 1
    assert limited[0]["title"] == "Agent version changed"

    for query in ("hours=0", "hours=721", "limit=0", "limit=501"):
        response = client.get(
            f"/api/agents/{agent_id}/events?{query}",
            headers=headers(admin),
        )
        assert response.status_code == 400, (query, response.text)
    assert client.get("/api/agents/agt_missing/events", headers=headers(admin)).status_code == 404
    with TestClient(app) as anonymous:
        assert anonymous.get(f"/api/agents/{agent_id}/events").status_code == 401

    created = client.post(
        "/api/users",
        json={
            "username": "event-viewer",
            "display_name": "Event Viewer",
            "role": "viewer",
            "password": VIEWER_PASSWORD,
        },
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    with TestClient(app) as viewer_client:
        login(viewer_client, "event-viewer", VIEWER_PASSWORD)
        response = viewer_client.get(f"/api/agents/{agent_id}/events")
        assert response.status_code == 200, response.text
        assert len(response.json()["events"]) == 3


def test_event_retention_prunes_old_rows(client, admin, app):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    now = datetime.now(timezone.utc)
    old_at = _iso(now - timedelta(days=10))
    recent_at = _iso(now - timedelta(minutes=5))
    app.state.db.record_agent_event(agent_id, "service", "info", "Old event", at=old_at)
    app.state.db.record_agent_event(agent_id, "service", "info", "Recent event", at=recent_at)

    removed = app.state.db.prune_agent_events(7)
    assert removed == 1
    with app.state.db.connection() as conn:
        remaining = conn.execute(
            "SELECT title FROM agent_events WHERE agent_id = ? ORDER BY at",
            (agent_id,),
        ).fetchall()
    assert [row["title"] for row in remaining] == ["Recent event"]
