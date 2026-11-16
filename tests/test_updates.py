from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from sextant_control import selfupdate, updates
from sextant_control.server import create_app
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


def test_list_releases_sorts_and_dedupes(monkeypatch):
    monkeypatch.setattr(
        updates,
        "_github_releases",
        lambda repo, limit=20: [
            {"version": "0.9.0", "url": "", "notes": "", "published_at": "", "assets": []},
            {"version": "0.10.0", "url": "", "notes": "", "published_at": "", "assets": []},
            {"version": "v0.9.0", "url": "", "notes": "", "published_at": "", "assets": []},
        ],
    )
    result = updates.list_releases("github:owner/repo")
    assert [release["version"] for release in result] == ["0.10.0", "0.9.0"]


def _feed_release(version: str) -> dict:
    data = b"wheel"
    return {
        "version": version,
        "url": f"https://example.test/releases/{version}",
        "notes": "",
        "published_at": "",
        "assets": [
            {
                "name": f"sextant-{version}-py3-none-any.whl",
                "url": f"https://example.test/sextant-{version}-py3-none-any.whl",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        ],
    }


def test_prepare_agent_releases_endpoint(settings, monkeypatch):
    feed = [_feed_release("0.9.1"), _feed_release("0.9.0")]
    monkeypatch.setattr(updates, "list_releases", lambda feed_url, limit=30: feed)

    def fake_download(url, destination, **kwargs):
        data = b"wheel"
        Path(destination).write_bytes(data)
        return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    monkeypatch.setattr(selfupdate, "download_artifact", fake_download)
    app = create_app(replace(settings, update_feed="github:owner/repo"))
    with TestClient(app) as client:
        admin = login(client)
        first = client.post("/api/agent-updates/prepare", headers=headers(admin))
        assert first.status_code == 200, first.text
        body = first.json()
        assert [item["version"] for item in body["prepared"]] == ["0.9.1", "0.9.0"]
        assert body["versions"] == ["0.9.1", "0.9.0"]

        second = client.post("/api/agent-updates/prepare", headers=headers(admin))
        assert second.status_code == 200, second.text
        assert second.json()["prepared"] == []
        assert set(second.json()["skipped"]) == {"0.9.1", "0.9.0"}

        detail = client.get("/api/store/packages/sextant-agent", headers=headers(admin))
        assert detail.status_code == 200
        versions = [release["version"] for release in detail.json()["releases"]]
        assert sorted(versions) == ["0.9.0", "0.9.1"]

        client.post(
            "/api/users",
            json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
            headers=headers(admin),
        )
        viewer = login(client, "viewer1", "viewer password 123")
        denied = client.post("/api/agent-updates/prepare", headers=headers(viewer))
        assert denied.status_code == 403


def test_prepare_agent_releases_requires_feed(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        admin = login(client)
        response = client.post("/api/agent-updates/prepare", headers=headers(admin))
        assert response.status_code == 409


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
