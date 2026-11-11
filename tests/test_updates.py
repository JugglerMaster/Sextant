from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from runner_control import updates
from runner_control.server import create_app
from tests.helpers import headers, login


def test_parse_version_ordering():
    assert updates.parse_version("v0.10.0") > updates.parse_version("0.9.9")
    assert updates.parse_version("1.0.0-rc1") == (1, 0, 0, 1)
    assert updates.parse_version("garbage") == (0,)


def test_check_update_github(monkeypatch):
    monkeypatch.setattr(
        updates,
        "_github_release",
        lambda repo: {
            "version": "0.3.0",
            "url": "https://example.test/release",
            "notes": "changes",
            "published_at": "2026-11-09T00:00:00Z",
            "assets": [],
        },
    )
    result = updates.check_update("github:owner/repo", current_version="0.2.0")
    assert result["enabled"] is True
    assert result["latest_version"] == "0.3.0"
    assert result["update_available"] is True
    assert result["error"] == ""


def test_check_update_reports_errors(monkeypatch):
    def boom(_repo):
        raise ValueError("no release")

    monkeypatch.setattr(updates, "_github_release", boom)
    result = updates.check_update("github:owner/repo", current_version="0.2.0")
    assert result["update_available"] is False
    assert "no release" in result["error"]


def test_update_status_endpoint_and_cache(settings, monkeypatch):
    calls = {"n": 0}

    def fake(feed, current_version=updates.__version__):
        calls["n"] += 1
        return {
            "enabled": True,
            "feed": feed,
            "current_version": current_version,
            "latest_version": "9.9.9",
            "update_available": True,
            "url": "https://example.test",
            "notes": "",
            "published_at": "",
            "assets": [],
            "checked_at": "",
            "error": "",
        }

    monkeypatch.setattr(updates, "check_update", fake)
    app = create_app(replace(settings, update_feed="github:owner/repo"))
    with TestClient(app) as client:
        admin = login(client)
        first = client.get("/api/update/status", headers=headers(admin))
        second = client.get("/api/update/status", headers=headers(admin))
        assert first.status_code == 200
        assert first.json()["update_available"] is True
        assert second.status_code == 200
        assert calls["n"] == 1

        forced = client.get("/api/update/status?force=true", headers=headers(admin))
        assert forced.status_code == 200
        assert calls["n"] == 2


def test_update_status_requires_admin_and_feed(client, admin):
    no_feed = client.get("/api/update/status", headers=headers(admin))
    assert no_feed.status_code == 200
    assert no_feed.json()["enabled"] is False

    client.post(
        "/api/users",
        json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    viewer = login(client, "viewer1", "viewer password 123")
    denied = client.get("/api/update/status", headers=headers(viewer))
    assert denied.status_code == 403
