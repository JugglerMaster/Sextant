from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from sextant_control.config import LdapSettings
from sextant_control.ldap_auth import LdapIdentity
from sextant_control.server import create_app
from tests.helpers import login


LDAP = LdapSettings(
    server="dc.example.com",
    base_dn="DC=example,DC=com",
    bind_dn="CN=svc,DC=example,DC=com",
    bind_password="secret",
    admin_groups=("CN=Ops Admins,OU=Groups,DC=example,DC=com",),
)


def test_ad_login_provisions_user_and_maps_role(settings, monkeypatch):
    app = create_app(replace(settings, ldap=LDAP))
    monkeypatch.setattr(
        "sextant_control.server.authenticate_ldap",
        lambda username, password, ldap: LdapIdentity(
            username=username,
            display_name="Directory User",
            email="directory@example.com",
            role="admin",
        ),
    )
    with TestClient(app) as client:
        config = client.get("/api/auth/config").json()
        assert config["ldap_enabled"] is True

        session = login(client, "directory-user", "anything", mode="ad")
        assert session["user"]["source"] == "ad"
        assert session["user"]["role"] == "admin"
        assert session["user"]["display_name"] == "Directory User"
        assert client.get("/api/users").status_code == 200

        me = client.get("/api/me").json()
        assert me["permissions"]["admin"] is True


def test_ad_login_rejects_bad_credentials_and_outage(settings, monkeypatch):
    app = create_app(replace(settings, ldap=LDAP))
    with TestClient(app) as client:
        monkeypatch.setattr("sextant_control.server.authenticate_ldap", lambda *args: None)
        response = client.post("/api/auth/login", json={"username": "nobody", "password": "bad", "mode": "ad"})
        assert response.status_code == 401

        def outage(*args):
            raise RuntimeError("directory unavailable")

        monkeypatch.setattr("sextant_control.server.authenticate_ldap", outage)
        response = client.post("/api/auth/login", json={"username": "nobody", "password": "bad", "mode": "ad"})
        assert response.status_code == 503


def test_local_admin_does_not_fall_through_to_ad(settings, monkeypatch):
    app = create_app(replace(settings, ldap=LDAP))
    called = {"value": False}

    def should_not_run(*args):
        called["value"] = True
        return LdapIdentity("admin", "AD admin", "", "admin")

    monkeypatch.setattr("sextant_control.server.authenticate_ldap", should_not_run)
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong local password", "mode": "auto"})
        assert response.status_code == 401
        assert called["value"] is False
