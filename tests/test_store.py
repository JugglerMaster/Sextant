from __future__ import annotations

import hashlib
from dataclasses import replace

from fastapi.testclient import TestClient

from sextant_control.server import create_app
from tests.helpers import enroll_agent, headers, issue_token, login


def _create_package(client, admin, slug="sextant-agent", kind="agent"):
    response = client.post(
        "/api/store/packages",
        json={
            "slug": slug,
            "name": "Sextant Agent",
            "vendor": "Sextant",
            "description": "Agent wheel",
            "kind": kind,
            "platforms": ["linux", "windows"],
        },
        headers=headers(admin),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _upload(client, admin, slug, version, data, *, artifact_type="agent-wheel", sha=None):
    extra = {
        "X-Artifact-Type": artifact_type,
        "X-Filename": f"{slug}-{version}.bin",
        "Content-Type": "application/octet-stream",
    }
    if sha:
        extra["X-Sha256"] = sha
    return client.put(
        f"/api/store/packages/{slug}/releases/{version}",
        content=data,
        headers={**headers(admin), **extra},
    )


def test_store_publish_list_and_download(client, admin):
    _create_package(client, admin)
    artifact = b"agent-wheel-bytes" * 10
    digest = hashlib.sha256(artifact).hexdigest()

    published = _upload(client, admin, "sextant-agent", "0.3.0", artifact)
    assert published.status_code == 200, published.text
    release = published.json()
    assert release["sha256"] == digest
    assert release["size"] == len(artifact)
    assert release["yanked_at"] is None

    listing = client.get("/api/store/packages", headers=headers(admin))
    assert listing.status_code == 200
    assert listing.json()["packages"][0]["slug"] == "sextant-agent"

    detail = client.get("/api/store/packages/sextant-agent", headers=headers(admin))
    assert detail.status_code == 200
    assert len(detail.json()["releases"]) == 1

    agent = enroll_agent(admin, token=issue_token(admin))
    download = client.get(
        f"/api/agent/{agent['agent_id']}/store/releases/{release['id']}/download",
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert download.status_code == 200
    assert download.content == artifact
    assert download.headers["x-checksum-sha256"] == digest


def test_store_rejects_bad_hash_and_duplicate_version(client, admin):
    _create_package(client, admin)
    bad = _upload(client, admin, "sextant-agent", "0.3.0", b"data", sha="0" * 64)
    assert bad.status_code == 400

    assert _upload(client, admin, "sextant-agent", "0.3.0", b"data").status_code == 200
    duplicate = _upload(client, admin, "sextant-agent", "0.3.0", b"other")
    assert duplicate.status_code == 409


def test_store_yank_blocks_download(client, admin):
    _create_package(client, admin)
    release = _upload(client, admin, "sextant-agent", "0.3.0", b"data").json()
    yank = client.post(
        f"/api/store/releases/{release['id']}/yank?reason=bad",
        headers=headers(admin),
    )
    assert yank.status_code == 200
    assert yank.json()["yanked_at"]

    agent = enroll_agent(admin, token=issue_token(admin))
    download = client.get(
        f"/api/agent/{agent['agent_id']}/store/releases/{release['id']}/download",
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert download.status_code == 404


def test_store_rbac_and_stats(client, admin):
    _create_package(client, admin)
    client.post(
        "/api/users",
        json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    viewer = login(client, "viewer1", "viewer password 123")
    assert client.get("/api/store/packages", headers=headers(viewer)).status_code == 200
    denied = client.post(
        "/api/store/packages",
        json={"slug": "other", "name": "Other"},
        headers=headers(viewer),
    )
    assert denied.status_code == 403

    admin = login(client)
    stats = client.get("/api/store/stats", headers=headers(admin))
    assert stats.status_code == 200
    assert stats.json()["packages"] == 1


def test_store_per_file_limit(settings):
    limited = replace(settings, store_max_blob_mb=1)
    app = create_app(limited)
    with TestClient(app) as client:
        admin = login(client)
        _create_package(client, admin)
        response = _upload(client, admin, "sextant-agent", "0.3.0", b"x" * (1024 * 1024 + 1))
        assert response.status_code == 413
