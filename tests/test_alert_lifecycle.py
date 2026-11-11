from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from sextant_control.config import Settings
from tests.helpers import enroll_agent, headers, heartbeat, login, sample_metrics

DISK_ALERT_KEY = "disk:!2F"


def _disk_metrics(percent: float) -> dict:
    return sample_metrics(
        disks=[
            {
                "mount": "/",
                "total": 100,
                "used": percent,
                "free": 100 - percent,
                "percent": percent,
                "fs": "ext4",
            }
        ]
    )


def _detail_alerts(client, agent_id: str) -> dict[str, dict]:
    detail = client.get(f"/api/agents/{agent_id}").json()["agent"]
    return {alert["key"]: alert for alert in detail["alerts"]}


def _state(app, agent_id: str) -> dict:
    with app.state.db.connection() as conn:
        return {
            row["alert_key"]: row
            for row in conn.execute(
                "SELECT * FROM alert_states WHERE agent_id = ?", (agent_id,)
            ).fetchall()
        }


def _alert_events(client, admin, agent_id: str) -> list[dict]:
    response = client.get(f"/api/agents/{agent_id}/events?hours=24", headers=headers(admin))
    assert response.status_code == 200, response.text
    return [event for event in response.json()["events"] if event["kind"] == "alert"]


def test_acknowledged_alert_clears_and_recurs_as_new_incident(client, admin, app):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    heartbeat(admin, agent, _disk_metrics(95))

    alert = _detail_alerts(client, agent_id)[DISK_ALERT_KEY]
    first_incident = alert["incident_id"]
    assert first_incident
    assert alert["first_seen_at"] and alert["last_seen_at"]
    assert alert["acknowledged"] is False

    acknowledged = client.post(
        f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}/ack",
        headers=headers(admin),
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["alert"]["acknowledged"] is True
    assert acknowledged.json()["alert"]["incident_id"] == first_incident

    # The condition goes away: state is resolved rather than deleted, and the
    # acknowledgement is cleared so a later recurrence cannot inherit it.
    heartbeat(admin, agent, _disk_metrics(40))
    assert DISK_ALERT_KEY not in _detail_alerts(client, agent_id)
    cleared_state = _state(app, agent_id)[DISK_ALERT_KEY]
    assert cleared_state["cleared_at"]
    assert cleared_state["acknowledged_at"] is None
    assert cleared_state["acknowledged_by"] == ""
    assert cleared_state["snoozed_until"] is None

    # The same key reappears: a fresh incident, active and unacknowledged.
    heartbeat(admin, agent, _disk_metrics(96))
    recurred = _detail_alerts(client, agent_id)[DISK_ALERT_KEY]
    assert recurred["acknowledged"] is False
    assert recurred["acknowledged_at"] is None
    assert recurred["snoozed"] is False
    assert recurred["incident_id"] != first_incident
    assert recurred["first_seen_at"]

    recurred_state = _state(app, agent_id)[DISK_ALERT_KEY]
    assert recurred_state["cleared_at"] is None
    assert recurred_state["incident_id"] == recurred["incident_id"]

    # Acknowledging the recurring incident is a fresh acknowledgement; a
    # second acknowledgement on the same incident is a re-acknowledgement.
    acknowledged_again = client.post(
        f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}/ack",
        headers=headers(admin),
    )
    assert acknowledged_again.status_code == 200, acknowledged_again.text
    assert acknowledged_again.json()["alert"]["acknowledged"] is True
    renewed = client.post(
        f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}/ack",
        headers=headers(admin),
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["alert"]["acknowledged"] is True

    actions = [event["detail"].get("action") for event in _alert_events(client, admin, agent_id)]
    assert "acknowledged" in actions
    assert "cleared" in actions
    assert "reacknowledged" in actions
    raised = [
        event
        for event in _alert_events(client, admin, agent_id)
        if event["detail"].get("action") == "raised"
    ]
    assert len(raised) == 2
    assert raised[0]["detail"]["recurrence"] is True
    assert raised[0]["detail"]["previous_incident_id"] == first_incident
    assert raised[0]["detail"]["incident_id"] == recurred["incident_id"]


def test_still_present_alert_updates_last_seen_without_losing_ack(client, admin, app):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    heartbeat(admin, agent, _disk_metrics(95))
    acknowledged = client.post(
        f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}/ack",
        headers=headers(admin),
    )
    assert acknowledged.status_code == 200, acknowledged.text

    old_first_seen = "2026-01-01T00:00:00Z"
    old_last_seen = "2026-01-01T00:00:05Z"
    with app.state.db.connection() as conn:
        conn.execute(
            """UPDATE alert_states SET first_seen_at = ?, last_seen_at = ?
               WHERE agent_id = ? AND alert_key = ?""",
            (old_first_seen, old_last_seen, agent_id, DISK_ALERT_KEY),
        )

    heartbeat(admin, agent, _disk_metrics(95))
    alert = _detail_alerts(client, agent_id)[DISK_ALERT_KEY]
    assert alert["acknowledged"] is True
    assert alert["acknowledged_by"] == "admin"
    assert alert["first_seen_at"] == old_first_seen
    assert alert["last_seen_at"] != old_last_seen
    assert alert["last_seen_at"] > old_last_seen


def test_acknowledgement_ttl_expiry_requires_re_ack(client, admin, app):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    heartbeat(admin, agent, _disk_metrics(95))
    assert (
        client.post(
            f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}/ack",
            headers=headers(admin),
        ).status_code
        == 200
    )

    expired_at = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    with app.state.db.connection() as conn:
        conn.execute(
            """UPDATE alert_states SET acknowledged_at = ?
               WHERE agent_id = ? AND alert_key = ?""",
            (expired_at, agent_id, DISK_ALERT_KEY),
        )

    dashboard = client.get("/api/dashboard").json()
    alert = next(
        item
        for item in dashboard["agents"][0]["alerts"]
        if item["key"] == DISK_ALERT_KEY
    )
    assert alert["acknowledged"] is False
    assert alert["acknowledged_at"] is None
    assert dashboard["agents"][0]["alert_count"] == 1
    state = _state(app, agent_id)[DISK_ALERT_KEY]
    assert state["acknowledged_at"] is None
    actions = [event["detail"].get("action") for event in _alert_events(client, admin, agent_id)]
    assert "acknowledgement_expired" in actions


def test_ack_ttl_setting_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("SEXTANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEXTANT_SECRET_KEY", "x" * 32)
    assert Settings.from_env().alert_ack_ttl_hours == 168
    monkeypatch.setenv("SEXTANT_ALERT_ACK_TTL_HOURS", "0")
    assert Settings.from_env().alert_ack_ttl_hours == 1
    monkeypatch.setenv("SEXTANT_ALERT_ACK_TTL_HOURS", "48")
    assert Settings.from_env().alert_ack_ttl_hours == 48
    monkeypatch.setenv("SEXTANT_ALERT_ACK_TTL_HOURS", "99999")
    assert Settings.from_env().alert_ack_ttl_hours == 8760


def test_reconciliation_is_idempotent_and_concurrent_reads_are_safe(app, client, admin):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    heartbeat(admin, agent, _disk_metrics(95))

    initial = _state(app, agent_id)[DISK_ALERT_KEY]
    initial_actions = [
        event["detail"].get("action") for event in _alert_events(client, admin, agent_id)
    ]
    assert initial_actions.count("raised") == 1

    for _ in range(3):
        assert client.get("/api/dashboard").status_code == 200
        assert client.get(f"/api/agents/{agent_id}").status_code == 200

    after = _state(app, agent_id)[DISK_ALERT_KEY]
    assert after["incident_id"] == initial["incident_id"]
    assert after["first_seen_at"] == initial["first_seen_at"]
    assert after["cleared_at"] is None
    repeat_actions = [
        event["detail"].get("action") for event in _alert_events(client, admin, agent_id)
    ]
    assert repeat_actions.count("raised") == 1
    assert "cleared" not in repeat_actions

    def read_dashboard(_: int) -> int:
        with TestClient(app) as concurrent_client:
            login(concurrent_client)
            response = concurrent_client.get("/api/dashboard")
            return response.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(read_dashboard, range(4)))
    assert statuses == [200, 200, 200, 200]

    final = _state(app, agent_id)[DISK_ALERT_KEY]
    assert final["incident_id"] == initial["incident_id"]
    final_actions = [
        event["detail"].get("action") for event in _alert_events(client, admin, agent_id)
    ]
    assert final_actions.count("raised") == 1
    assert "cleared" not in final_actions


def test_alert_actions_remain_operator_only(app, client, admin):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    heartbeat(admin, agent, _disk_metrics(95))

    created = client.post(
        "/api/users",
        json={
            "username": "alert-lifecycle-viewer",
            "display_name": "Alert Lifecycle Viewer",
            "role": "viewer",
            "password": "viewer password 123",
        },
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    with TestClient(app) as viewer_client:
        viewer = login(viewer_client, "alert-lifecycle-viewer", "viewer password 123")
        assert (
            viewer_client.post(
                f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}/ack",
                headers=headers(viewer),
            ).status_code
            == 403
        )
        assert (
            viewer_client.post(
                f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}/snooze",
                json={"minutes": 30},
                headers=headers(viewer),
            ).status_code
            == 403
        )
        assert (
            viewer_client.delete(
                f"/api/agents/{agent_id}/alerts/{DISK_ALERT_KEY}",
                headers=headers(viewer),
            ).status_code
            == 403
        )
