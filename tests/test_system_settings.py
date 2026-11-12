from __future__ import annotations

import os
import stat
from pathlib import Path

from fastapi.testclient import TestClient

from sextant_control.config import DiscoverySettings, Settings
from sextant_control.security import hash_password
from sextant_control.server import create_app
from tests.helpers import ADMIN_PASSWORD, headers, login

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
        enrollment_ttl_minutes=30,
        discovery=DiscoverySettings(
            enabled=True,
            cidrs=("192.168.1.0/24",),
            methods=("neigh", "dns"),
        ),
    )


def test_ldap_group_dns_keep_their_commas(tmp_path):
    settings = Settings.from_env(
        {
            "SEXTANT_DATA_DIR": str(tmp_path),
            "SEXTANT_LDAP_ENABLED": "1",
            "SEXTANT_LDAP_SERVER": "dc1.corp.example.com",
            "SEXTANT_LDAP_BASE_DN": "DC=corp,DC=example,DC=com",
            "SEXTANT_LDAP_BIND_DN": "CN=svc-sextant,DC=corp,DC=example,DC=com",
            "SEXTANT_LDAP_BIND_PASSWORD": "secret",
            "SEXTANT_LDAP_ADMIN_GROUPS": (
                "CN=RC Admins,OU=Groups,DC=corp,DC=example,DC=com;"
                "CN=Second Admins,OU=Groups,DC=corp,DC=example,DC=com"
            ),
            "SEXTANT_LDAP_OPERATOR_GROUPS": "CN=RC Operators,OU=Groups,DC=corp,DC=example,DC=com",
        }
    )
    assert settings.ldap is not None
    assert settings.ldap.admin_groups == (
        "CN=RC Admins,OU=Groups,DC=corp,DC=example,DC=com",
        "CN=Second Admins,OU=Groups,DC=corp,DC=example,DC=com",
    )
    assert settings.ldap.operator_groups == (
        "CN=RC Operators,OU=Groups,DC=corp,DC=example,DC=com",
    )


def test_system_settings_persist_and_apply_without_restart(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    with TestClient(app) as client:
        admin = login(client)
        initial = client.get("/api/system/settings").json()
        assert initial["server"]["session_hours"] == 12
        assert initial["domain"]["enabled"] is False

        response = client.patch(
            "/api/system/settings",
            json={
                "server": {
                    "public_url": "https://sextant.example.com/",
                    "allowed_hosts": ["sextant.example.com", "192.168.1.194"],
                    "session_hours": 24,
                    "offline_after_seconds": 120,
                    "enrollment_ttl_minutes": 45,
                    "trust_proxy": True,
                },
                "domain": {
                    "enabled": True,
                    "domain": "corp.example.com",
                    "server": "dc1.corp.example.com",
                    "port": 636,
                    "use_ssl": True,
                    "bind_dn": "CN=svc-sextant,OU=Service Accounts,DC=corp,DC=example,DC=com",
                    "bind_password": "super-secret",
                    "display_name_attr": "displayName",
                    "email_attr": "mail",
                    "group_attr": "memberOf",
                },
                "ssh": {
                    "source": "managed",
                    "install_enabled": False,
                },
            },
            headers=headers(admin),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["server"]["public_url"] == "https://sextant.example.com"
        assert data["server"]["session_hours"] == 24
        assert data["server"]["allowed_hosts"] == ["sextant.example.com", "192.168.1.194"]
        assert "allowed_hosts" in data["meta"]["restart_required"]
        assert data["domain"]["enabled"] is True
        assert data["domain"]["base_dn"] == "DC=corp,DC=example,DC=com"
        assert data["domain"]["bind_password_set"] is True
        assert data["ssh"]["install_enabled"] is False

        # The live settings object is rebuilt for the next request.
        assert app.state.settings.public_url == "https://sextant.example.com"
        assert app.state.settings.session_hours == 24
        assert app.state.settings.ldap is not None
        assert app.state.settings.ldap.server == "dc1.corp.example.com"
        assert app.state.settings.ldap.domain == "corp.example.com"
        assert app.state.settings.ssh_install_enabled is False

        password_file = tmp_path / "secrets" / "ldap_bind_password"
        assert password_file.read_text(encoding="utf-8").strip() == "super-secret"
        assert stat.S_IMODE(password_file.stat().st_mode) == 0o600

        stored = database.get_system_settings()
        assert stored is not None
        assert "super-secret" not in stored["values_json"]

        # Reloading the app keeps the saved values.
        app2 = create_app(_settings(tmp_path))
        assert app2.state.settings.session_hours == 24
        assert app2.state.settings.ldap is not None
        assert app2.state.settings.ldap.bind_password == "super-secret"


def test_system_settings_validation_and_admin_only(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    database.create_user(
        "viewer",
        role="viewer",
        password_hash=hash_password("viewer-password-123"),
    )
    with TestClient(app) as client:
        viewer = login(client, username="viewer", password="viewer-password-123")
        assert client.get("/api/system/settings").status_code == 403
        blocked = client.patch(
            "/api/system/settings",
            json={"server": {"session_hours": 8}},
            headers=headers(viewer),
        )
        assert blocked.status_code == 403

        admin = login(client)
        missing = client.patch(
            "/api/system/settings",
            json={"domain": {"enabled": True, "server": "dc1.example.com"}},
            headers=headers(admin),
        )
        assert missing.status_code == 400
        assert "Base DN" in missing.json()["detail"]

        bad_filter = client.patch(
            "/api/system/settings",
            json={
                "domain": {
                    "enabled": True,
                    "server": "dc1.example.com",
                    "base_dn": "DC=example,DC=com",
                    "bind_dn": "CN=svc,DC=example,DC=com",
                    "user_filter": "(sAMAccountName=bob)",
                }
            },
            headers=headers(admin),
        )
        assert bad_filter.status_code == 400
        assert "{username}" in bad_filter.json()["detail"]

        bad_attr = client.patch(
            "/api/system/settings",
            json={"domain": {"username_attr": "bad attr!"}},
            headers=headers(admin),
        )
        assert bad_attr.status_code == 400


def test_ssh_key_upload_and_profile_update(tmp_path):
    app = create_app(_settings(tmp_path))
    key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "not-a-real-key-but-valid-shape\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    with TestClient(app) as client:
        admin = login(client)
        response = client.patch(
            "/api/system/settings",
            json={
                "ssh": {
                    "source": "managed",
                    "private_key": key,
                }
            },
            headers=headers(admin),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ssh"]["key_present"] is True
        assert data["ssh"]["effective_key_path"].endswith("install_key")
        key_file = Path(data["ssh"]["effective_key_path"])
        assert key_file.read_text(encoding="utf-8") == key
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

        tested = client.post(
            "/api/system/ssh/test",
            json={"ssh": {"source": "managed"}},
            headers=headers(admin),
        )
        assert tested.status_code == 200
        assert tested.json()["present"] is True

        current_user = os.environ.get("USER", "") or __import__("pwd").getpwuid(
            os.getuid()
        ).pw_name
        bad = client.patch(
            "/api/system/settings",
            json={
                "ssh": {
                    "source": "system_user",
                    "system_user": current_user,
                    "private_key": key,
                }
            },
            headers=headers(admin),
        )
        assert bad.status_code == 400
        assert "managed" in bad.json()["detail"]

        if current_user:
            system_user = client.patch(
                "/api/system/settings",
                json={"ssh": {"source": "system_user", "system_user": current_user}},
                headers=headers(admin),
            )
            assert system_user.status_code == 200, system_user.text
            assert system_user.json()["ssh"]["source"] == "system_user"

        profile = client.patch(
            "/api/me",
            json={"display_name": "Sextant Admin", "email": "admin@example.com"},
            headers=headers(admin),
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["user"]["display_name"] == "Sextant Admin"
        assert profile.json()["user"]["email"] == "admin@example.com"
        me = client.get("/api/me").json()
        assert me["user"]["display_name"] == "Sextant Admin"
        assert me["user"]["email"] == "admin@example.com"

        invalid = client.patch(
            "/api/me",
            json={"email": "not-an-email"},
            headers=headers(admin),
        )
        assert invalid.status_code == 400
