from __future__ import annotations

from fastapi.testclient import TestClient

from sextant_control.db import dumps
from tests.helpers import headers, login


USER_PASSWORD = "audit test password 123"


def _create_user(client: TestClient, admin: dict, username: str, role: str) -> dict:
    response = client.post(
        "/api/users",
        json={
            "username": username,
            "display_name": username,
            "role": role,
            "password": USER_PASSWORD,
        },
        headers=headers(admin),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _clear_audit(app) -> None:
    with app.state.db.transaction() as conn:
        conn.execute("DELETE FROM audit_log")


def _insert_audit(app, actor, action, *, at, target="", detail=None, ip="10.0.0.1"):
    with app.state.db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_log (at, actor, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (at, actor, action, target, dumps(detail or {}), ip),
        )


def _seed(app):
    _clear_audit(app)
    _insert_audit(app, "admin", "agent.delete", at="2026-01-01T00:00:00Z", target="agt_1", ip="10.0.0.1")
    _insert_audit(app, "admin", "auth.login", at="2026-01-02T00:00:00Z", target="admin", ip="10.0.0.2")
    _insert_audit(app, "jdoe", "auth.login_failed", at="2026-01-03T00:00:00Z", target="jdoe", ip="10.0.0.3")
    _insert_audit(app, "jdoe", "command.create", at="2026-01-04T00:00:00Z", target="agt_2", detail={"kind": "shell"}, ip="10.0.0.4")


def test_audit_pagination_and_total(client, admin, app):
    _seed(app)
    first = client.get("/api/audit?limit=2&offset=0", headers=headers(admin)).json()
    assert first["total"] == 4
    assert first["limit"] == 2
    assert first["offset"] == 0
    assert len(first["events"]) == 2
    assert first["events"][0]["action"] == "command.create"
    assert first["events"][1]["action"] == "auth.login_failed"

    second = client.get("/api/audit?limit=2&offset=2", headers=headers(admin)).json()
    assert len(second["events"]) == 2
    assert second["events"][0]["action"] == "auth.login"
    assert second["events"][1]["action"] == "agent.delete"

    ids = {event["id"] for event in first["events"]} | {event["id"] for event in second["events"]}
    assert len(ids) == 4


def test_audit_actor_filter(client, admin, app):
    _seed(app)
    data = client.get("/api/audit?actor=jdoe", headers=headers(admin)).json()
    assert data["total"] == 2
    assert all(event["actor"] == "jdoe" for event in data["events"])


def test_audit_action_filter(client, admin, app):
    _seed(app)
    data = client.get("/api/audit?action=auth.login", headers=headers(admin)).json()
    assert data["total"] == 1
    assert data["events"][0]["action"] == "auth.login"

    # Free-text search also matches the action namespace.
    data = client.get("/api/audit?q=auth.", headers=headers(admin)).json()
    assert data["total"] == 2
    assert all(event["action"].startswith("auth.") for event in data["events"])


def test_audit_text_search(client, admin, app):
    _seed(app)
    data = client.get("/api/audit?q=agt_2", headers=headers(admin)).json()
    assert data["total"] == 1
    assert data["events"][0]["target"] == "agt_2"

    data = client.get("/api/audit?q=10.0.0.3", headers=headers(admin)).json()
    assert data["total"] == 1
    assert data["events"][0]["ip"] == "10.0.0.3"

    data = client.get("/api/audit?q=shell", headers=headers(admin)).json()
    assert data["total"] == 1
    assert data["events"][0]["detail"]["kind"] == "shell"


def test_audit_date_range(client, admin, app):
    _seed(app)
    data = client.get("/api/audit?since=2026-01-02T00:00:00Z&until=2026-01-03T23:59:59Z", headers=headers(admin)).json()
    assert data["total"] == 2
    actions = {event["action"] for event in data["events"]}
    assert actions == {"auth.login", "auth.login_failed"}


def test_audit_validation_errors(client, admin):
    response = client.get("/api/audit?limit=0", headers=headers(admin))
    assert response.status_code == 400
    response = client.get("/api/audit?limit=501", headers=headers(admin))
    assert response.status_code == 400
    response = client.get("/api/audit?offset=-1", headers=headers(admin))
    assert response.status_code == 400
    response = client.get("/api/audit?since=not-a-timestamp", headers=headers(admin))
    assert response.status_code == 400
    response = client.get("/api/audit?since=2026-01-02T00:00:00Z&until=2026-01-01T00:00:00Z", headers=headers(admin))
    assert response.status_code == 400


def test_audit_rbac(client, admin, app):
    user = _create_user(client, admin, "audit-viewer", "viewer")
    with TestClient(app) as viewer_client:
        login(viewer_client, user["username"], USER_PASSWORD)
        assert viewer_client.get("/api/audit").status_code == 403
        assert viewer_client.get("/api/export/audit.csv").status_code == 403
    assert client.get("/api/audit").status_code == 200
    assert client.get("/api/audit", headers=headers(admin)).status_code == 200


def test_audit_unauthenticated(client):
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/export/audit.csv").status_code == 401


def test_audit_default_and_max_limit(client, admin, app):
    _clear_audit(app)
    for i in range(5):
        _insert_audit(app, "admin", f"action.{i}", at=f"2026-01-0{i + 1}T00:00:00Z", target=f"t{i}")
    # default limit is 100, all 5 returned
    data = client.get("/api/audit", headers=headers(admin)).json()
    assert data["limit"] == 100
    assert data["total"] == 5
    assert len(data["events"]) == 5
    # limit=500 accepted
    assert client.get("/api/audit?limit=500", headers=headers(admin)).status_code == 200


def test_audit_export_csv(client, admin, app):
    _seed(app)
    response = client.get("/api/export/audit.csv", headers=headers(admin))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert 'filename="sextant-audit.csv"' in response.headers["content-disposition"]
    lines = response.text.splitlines()
    assert lines[0] == "at,actor,action,target,ip,detail"
    assert len(lines) == 5  # header + 4 rows
    assert "command.create" in response.text
    assert "agt_2" in response.text


def test_audit_export_filters(client, admin, app):
    _seed(app)
    response = client.get("/api/export/audit.csv?actor=jdoe", headers=headers(admin))
    assert response.status_code == 200
    lines = response.text.splitlines()
    assert len(lines) == 3  # header + 2 rows
    assert "jdoe" in response.text
    assert "agent.delete" not in response.text
    assert "auth.login" not in response.text or "auth.login_failed" in response.text
