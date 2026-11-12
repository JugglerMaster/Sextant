from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from sextant_control.config import DiscoverySettings, Settings
from sextant_control.security import hash_password
from sextant_control.server import create_app
from tests.helpers import ADMIN_PASSWORD, enroll_agent, headers, heartbeat, login, sample_metrics

STATIC_DIR = Path(__file__).resolve().parents[1] / "sextant_control" / "static"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        static_dir=STATIC_DIR,
        secret_key=b"test-secret-key-that-is-long-enough",
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        cookie_secure=False,
        offline_after_seconds=90,
        discovery=DiscoverySettings(enabled=False),
    )


def test_domain_join_queues_without_persisting_password_and_injects_on_dispatch(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    with TestClient(app) as client:
        admin = login(client)
        agent = enroll_agent(admin, hostname="win-box")
        response = client.post(
            f"/api/agents/{agent['agent_id']}/domain/join",
            json={
                "domain": "corp.example.com",
                "username": "CORP\\svc-join",
                "password": "super-secret",
                "ou": "OU=Workstations,DC=corp,DC=example,DC=com",
                "computer_name": "WIN-BOX",
                "reboot": True,
            },
            headers=headers(admin),
        )
        assert response.status_code == 200, response.text
        command = response.json()["command"]
        assert command["kind"] == "domain"
        assert command["args"]["domain"] == "corp.example.com"
        assert command["args"]["password"] == ""
        with database.connection() as conn:
            stored = conn.execute(
                "SELECT args FROM commands WHERE id = ?", (command["id"],)
            ).fetchone()
            audit_rows = conn.execute(
                "SELECT detail FROM audit_log ORDER BY id DESC LIMIT 5"
            ).fetchall()
        assert "super-secret" not in stored["args"]
        assert "super-secret" not in json.dumps([dict(row) for row in audit_rows])
        assert len(app.state.domain_secrets) == 1

        beat = heartbeat(admin, agent, sample_metrics())
        delivered = next(item for item in beat["commands"] if item["id"] == command["id"])
        assert delivered["args"]["password"] == "super-secret"
        assert len(app.state.domain_secrets) == 0

        blocked = client.post(
            f"/api/agents/{agent['agent_id']}/domain/join",
            json={
                "domain": "corp.example.com",
                "username": "CORP\\svc-join",
                "password": "super-secret",
            },
            headers=headers(admin),
        )
        assert blocked.status_code == 409


def test_domain_join_requires_admin_and_valid_domain(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    database.create_user(
        "domop",
        role="operator",
        password_hash=hash_password("operator-password-123"),
    )
    with TestClient(app) as client:
        admin = login(client)
        agent = enroll_agent(admin, hostname="linux-box")
        database.create_user(
            "operator",
            role="operator",
            password_hash=hash_password("operator-password-123"),
        )
        operator = login(client, username="domop", password="operator-password-123")
        denied = client.post(
            f"/api/agents/{agent['agent_id']}/domain/join",
            json={"domain": "corp.example.com", "username": "svc", "password": "secret"},
            headers=headers(operator),
        )
        assert denied.status_code == 403

        admin = login(client)
        invalid = client.post(
            f"/api/agents/{agent['agent_id']}/domain/join",
            json={"domain": "not a domain", "username": "svc", "password": "secret"},
            headers=headers(admin),
        )
        assert invalid.status_code == 400

        status = client.post(
            f"/api/agents/{agent['agent_id']}/domain/status",
            headers=headers(admin),
        )
        assert status.status_code == 200, status.text
        assert status.json()["command"]["kind"] == "domain"
        assert status.json()["command"]["args"]["action"] == "status"
