from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from fastapi.testclient import TestClient

from sextant_control.server import create_app
from tests.helpers import (
    enroll_agent,
    headers,
    heartbeat,
    issue_token,
    login,
    sample_metrics,
)


def docker_summary(**overrides) -> dict:
    summary = {
        "available": True,
        "reason": "",
        "engine_version": "27.5.1",
        "api_version": "1.47",
        "compose_version": "2.32.4",
        "compose_available": True,
        "rootless": False,
        "storage_driver": "overlay2",
        "cgroup_driver": "systemd",
        "docker_root_dir": "/var/lib/docker",
        "counts": {
            "containers": 2,
            "running": 1,
            "paused": 0,
            "stopped": 1,
            "unhealthy": 0,
            "images": 1,
            "volumes": 1,
            "networks": 2,
        },
        "inventory_hash": "",
        "inventory_revision": 0,
        "collected_at": "",
    }
    summary.update(overrides)
    return summary


def container_row(index: int = 1, **overrides) -> dict:
    row = {
        "id": f"{index:064x}",
        "name": f"demo-{index}",
        "image": "nginx:latest",
        "image_id": "",
        "command": "nginx -g daemon off;",
        "state": "running" if index % 2 else "exited",
        "status": "Up 2 hours (healthy)" if index % 2 else "Exited (0) 3 hours ago",
        "health": "healthy" if index % 2 else "",
        "created_at": "2026-11-11T10:00:00Z",
        "started_at": "",
        "finished_at": "",
        "exit_code": None if index % 2 else 0,
        "restart_policy": "",
        "ports": ["0.0.0.0:8080->80/tcp"],
        "labels": {"com.docker.compose.project": "web"},
        "mounts": ["/data"],
        "networks": ["web_default"],
        "compose_project": "web",
        "compose_service": "nginx",
        "compose_config_hash": "abc123",
    }
    row.update(overrides)
    return row


def inventory_payload(**overrides) -> dict:
    payload = {
        "summary": docker_summary(),
        "containers": [container_row(1), container_row(2)],
        "images": [
            {
                "id": "sha256:" + "a" * 64,
                "repo_tags": ["nginx:latest"],
                "repo_digests": [],
                "size": 187000000,
                "shared_size": 0,
                "containers": 2,
                "dangling": False,
                "created_at": "2026-11-11T10:00:00Z",
            }
        ],
        "volumes": [
            {
                "name": "web_data",
                "driver": "local",
                "scope": "local",
                "mountpoint": "/var/lib/docker/volumes/web_data/_data",
                "labels": {"com.docker.compose.project": "web"},
                "in_use": False,
            }
        ],
        "networks": [
            {
                "id": "b" * 64,
                "name": "web_default",
                "driver": "bridge",
                "scope": "local",
                "internal": False,
                "attachable": False,
                "labels": {},
                "container_count": 2,
            }
        ],
        "collected_at": "2026-11-11T11:00:00Z",
        "hash": "c" * 64,
        "revision": 1,
    }
    payload.update(overrides)
    payload["summary"] = dict(payload["summary"])
    payload["summary"]["inventory_hash"] = payload["hash"]
    payload["summary"]["inventory_revision"] = payload["revision"]
    payload["summary"]["collected_at"] = payload["collected_at"]
    return payload


def enable_docker_profile(client, admin) -> None:
    profiles = client.get("/api/profiles").json()["profiles"]
    profile = next(item for item in profiles if item["slug"] == "workstation")
    settings = deepcopy(profile["settings"])
    settings.setdefault("modules", {})["docker"] = True
    settings.setdefault("actions", {})["allow_docker"] = True
    response = client.patch(
        f"/api/profiles/{profile['id']}",
        json={"settings": settings},
        headers=headers(admin),
    )
    assert response.status_code == 200, response.text


def upload_inventory(client, session, agent, payload=None):
    return client.post(
        f"/api/agent/{agent['agent_id']}/docker/inventory",
        json=payload or inventory_payload(),
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )


def enroll_windows_agent(client, admin) -> dict:
    token = issue_token(admin)
    response = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": token,
            "hostname": "windows-box",
            "machine_id": "windows-machine",
            "os": "windows",
            "os_version": "Windows 11",
            "arch": "AMD64",
            "ip": "192.0.2.20",
            "mac": "AA:BB:CC:DD:EE:FF",
            "agent_version": "0.3.1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_docker_inventory_requires_agent_token_and_profile(client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    other = enroll_agent(admin, hostname="other-box", machine_id="other-machine")
    denied = client.post(
        f"/api/agent/{agent['agent_id']}/docker/inventory",
        json=inventory_payload(),
    )
    assert denied.status_code == 401
    wrong_token = client.post(
        f"/api/agent/{agent['agent_id']}/docker/inventory",
        json=inventory_payload(),
        headers={"Authorization": f"Bearer {other['agent_token']}"},
    )
    assert wrong_token.status_code == 401

    # Docker is off in a custom profile by default.
    profile = next(item for item in client.get("/api/profiles").json()["profiles"] if item["slug"] == "workstation")
    settings = deepcopy(profile["settings"])
    settings["modules"]["docker"] = False
    settings["actions"]["allow_docker"] = False
    client.patch(f"/api/profiles/{profile['id']}", json={"settings": settings}, headers=headers(admin))
    disabled = upload_inventory(client, admin, agent)
    assert disabled.status_code == 409


def test_docker_inventory_upload_and_replacement(client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    response = upload_inventory(client, admin, agent)
    assert response.status_code == 200, response.text
    assert response.json()["containers"] == 2

    detail = client.get(f"/api/docker/hosts/{agent['agent_id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["host"]["agent_id"] == agent["agent_id"]
    assert body["host"]["engine_version"] == "27.5.1"
    assert body["host"]["counts"]["running"] == 1
    assert body["host"]["counts"]["stopped"] == 1
    assert len(body["containers"]) == 2
    assert body["containers"][0]["compose_project"] == "web"
    assert body["images"][0]["repo_tags"] == ["nginx:latest"]
    assert body["volumes"][0]["name"] == "web_data"
    assert body["networks"][0]["name"] == "web_default"

    second = inventory_payload(
        containers=[container_row(3)],
        images=[],
        volumes=[],
        networks=[],
        revision=2,
        hash="d" * 64,
        collected_at="2026-11-11T11:05:00Z",
    )
    replaced = upload_inventory(client, admin, agent, second)
    assert replaced.status_code == 200, replaced.text
    detail = client.get(f"/api/docker/hosts/{agent['agent_id']}").json()
    assert len(detail["containers"]) == 1
    assert detail["containers"][0]["name"] == "demo-3"
    assert detail["images"] == []
    assert detail["host"]["inventory_revision"] == 2
    assert detail["host"]["counts"]["containers"] == 1


def test_docker_inventory_rejects_unknown_fields_and_row_caps(client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    payload = inventory_payload()
    payload["summary"]["unexpected"] = True
    response = upload_inventory(client, admin, agent, payload)
    assert response.status_code == 422

    payload = inventory_payload(containers=[container_row(1)] * 1001)
    response = upload_inventory(client, admin, agent, payload)
    assert response.status_code == 422


def test_docker_inventory_payload_size_limit(settings, admin):
    app = create_app(replace(settings, docker_inventory_max_bytes=1024))
    with TestClient(app) as client:
        session = login(client)
        enable_docker_profile(client, session)
        agent = enroll_agent(session)
        response = upload_inventory(client, session, agent)
        assert response.status_code == 413


def test_deleting_agent_cascades_docker_inventory(app, client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    assert client.delete(f"/api/agents/{agent['agent_id']}", headers=headers(admin)).status_code == 204
    with app.state.db.connection() as conn:
        for table in (
            "docker_hosts",
            "docker_containers",
            "docker_images",
            "docker_volumes",
            "docker_networks",
        ):
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            assert row["n"] == 0


def test_docker_host_list_detail_and_refresh_rbac(app, client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    heartbeat(admin, agent, sample_metrics(docker=docker_summary()))
    listed = client.get("/api/docker/hosts")
    assert listed.status_code == 200
    assert len(listed.json()["hosts"]) == 1
    assert listed.json()["hosts"][0]["agent_id"] == agent["agent_id"]

    user_id = client.post(
        "/api/users",
        json={"username": "docker-viewer", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    ).json()["id"]
    with TestClient(app) as viewer_client:
        viewer = login(viewer_client, "docker-viewer", "viewer password 123")
        assert viewer_client.get("/api/docker/hosts").status_code == 200
        assert viewer_client.get(f"/api/docker/hosts/{agent['agent_id']}").status_code == 200
        denied = viewer_client.post(
            f"/api/docker/hosts/{agent['agent_id']}/refresh",
            json={},
            headers=headers(viewer),
        )
        assert denied.status_code == 403
        assert user_id

    queued = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/refresh",
        json={},
        headers=headers(admin),
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["queued"] is True
    assert queued.json()["kind"] == "docker"
    assert queued.json()["args"] == {"action": "inventory.refresh"}
    commands = client.get(f"/api/agents/{agent['agent_id']}/commands?status=queued")
    assert commands.status_code == 200
    assert any(item["kind"] == "docker" for item in commands.json()["commands"])


def test_docker_command_validation_only_allows_inventory_refresh(client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    bad = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "docker", "args": {"action": "container.remove"}},
        headers=headers(admin),
    )
    assert bad.status_code == 400
    good = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "docker", "args": {"action": "inventory.refresh"}},
        headers=headers(admin),
    )
    assert good.status_code == 200, good.text
    assert good.json()["kind"] == "docker"


def test_docker_command_is_blocked_when_profile_disables_docker(client, admin):
    agent = enroll_agent(admin)
    response = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "docker", "args": {"action": "inventory.refresh"}},
        headers=headers(admin),
    )
    assert response.status_code == 409
    assert "docker" in response.json()["detail"].lower()


def test_docker_refresh_respects_offline_policy(settings):
    app = create_app(replace(settings, offline_command_policy="reject"))
    with TestClient(app) as client:
        admin = login(client)
        enable_docker_profile(client, admin)
        agent = enroll_agent(admin)
        heartbeat(admin, agent, sample_metrics(docker=docker_summary()))
        with app.state.db.connection() as conn:
            conn.execute(
                "UPDATE agents SET last_seen = ? WHERE id = ?",
                ("2026-01-01T00:00:00Z", agent["agent_id"]),
            )
        response = client.post(
            f"/api/docker/hosts/{agent['agent_id']}/refresh",
            json={},
            headers=headers(admin),
        )
        assert response.status_code == 409
        assert "offline" in response.json()["detail"].lower()


def test_windows_and_disabled_agents_do_not_appear(client, admin):
    enable_docker_profile(client, admin)
    windows = enroll_windows_agent(client, admin)
    heartbeat(admin, windows, sample_metrics(os="windows", docker=docker_summary()))
    response = upload_inventory(client, admin, windows)
    assert response.status_code == 409
    assert client.get("/api/docker/hosts").json()["hosts"] == []

    linux = enroll_agent(admin, hostname="linux-disabled")
    heartbeat(
        admin,
        linux,
        sample_metrics(
            hostname="linux-disabled",
            docker=docker_summary(available=False, reason="disabled"),
        ),
    )
    assert client.get("/api/docker/hosts").json()["hosts"] == []


def test_heartbeat_requests_full_inventory_when_hash_is_missing(client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    response = heartbeat(admin, agent, sample_metrics(docker=docker_summary()))
    assert response["request_docker_inventory"] == "full"
    assert response["policy"]["allow_docker"] is True
