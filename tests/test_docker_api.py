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


def enable_docker_profile(
    client,
    admin,
    *,
    exec_allowed=False,
    prune_allowed=False,
    compose_allowed=False,
) -> None:
    profiles = client.get("/api/profiles").json()["profiles"]
    profile = next(item for item in profiles if item["slug"] == "workstation")
    settings = deepcopy(profile["settings"])
    settings.setdefault("modules", {})["docker"] = True
    settings.setdefault("actions", {})["allow_docker"] = True
    settings.setdefault("actions", {})["allow_docker_exec"] = exec_allowed
    settings.setdefault("actions", {})["allow_docker_prune"] = prune_allowed
    settings.setdefault("actions", {})["allow_docker_compose"] = compose_allowed
    response = client.patch(
        f"/api/profiles/{profile['id']}",
        json={"settings": settings},
        headers=headers(admin),
    )
    assert response.status_code == 200, response.text


def clear_docker_commands(app) -> None:
    with app.state.db.connection() as conn:
        conn.execute("DELETE FROM commands WHERE kind = 'docker'")


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


def test_docker_command_validation_rejects_unknown_action(client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    bad = client.post(
        f"/api/agents/{agent['agent_id']}/commands",
        json={"kind": "docker", "args": {"action": "container.nuke"}},
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


def test_container_lifecycle_action_route(app, client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    container_id = container_row(1)["id"]
    for action in (
        "container.start",
        "container.stop",
        "container.restart",
        "container.pause",
        "container.unpause",
        "container.kill",
    ):
        # Each request is validated before the one-active-mutation guard; clear
        # the queued command between calls so this test exercises every route.
        response = client.post(
            f"/api/docker/hosts/{agent['agent_id']}/actions",
            json={"action": action, "container_id": container_id},
            headers=headers(admin),
        )
        assert response.status_code == 200, response.text
        assert response.json()["args"] == {"action": action, "container_id": container_id}
        clear_docker_commands(app)


def test_container_actions_inspect_logs_and_exec(client, admin):
    enable_docker_profile(client, admin, exec_allowed=True)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    container_id = container_row(1)["id"]

    logs = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={
            "action": "container.logs",
            "container_id": container_id,
            "tail": 1000,
            "timestamps": True,
        },
        headers=headers(admin),
    )
    assert logs.status_code == 200, logs.text
    assert logs.json()["args"]["tail"] == 1000
    assert logs.json()["args"]["timestamps"] is True

    inspect = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "container.inspect", "container_id": container_id},
        headers=headers(admin),
    )
    assert inspect.status_code == 200, inspect.text

    exec_response = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/containers/{container_id}/exec",
        json={
            "shell": "bash",
            "user": "root",
            "workdir": "/srv/app",
            "command": "id && uname -a",
            "timeout": 60,
        },
        headers=headers(admin),
    )
    assert exec_response.status_code == 200, exec_response.text
    assert exec_response.json()["args"]["action"] == "container.exec_one_shot"
    assert exec_response.json()["args"]["command"] == "id && uname -a"

    bad_exec = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/containers/{container_id}/exec",
        json={"shell": "zsh", "command": "id"},
        headers=headers(admin),
    )
    assert bad_exec.status_code == 422


def test_resource_remove_routes_and_in_use_protection(app, client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    payload = inventory_payload()
    payload["volumes"][0]["in_use"] = True
    payload["images"][0]["containers"] = 0
    payload["networks"][0]["container_count"] = 0
    assert upload_inventory(client, admin, agent, payload).status_code == 200

    image = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "image.remove", "image_id": "sha256:" + "a" * 64},
        headers=headers(admin),
    )
    assert image.status_code == 200, image.text
    assert image.json()["args"]["image_id"] == "sha256:" + "a" * 64
    clear_docker_commands(app)

    volume = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "volume.remove", "volume_name": "web_data"},
        headers=headers(admin),
    )
    assert volume.status_code == 409
    assert "in use" in volume.json()["detail"].lower()

    network = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "network.remove", "network_id": "b" * 64},
        headers=headers(admin),
    )
    assert network.status_code == 200, network.text


def test_docker_action_rbac_csrf_and_gate_denial(app, client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    container_id = container_row(1)["id"]
    body = {"action": "container.stop", "container_id": container_id}

    no_csrf = client.post(f"/api/docker/hosts/{agent['agent_id']}/actions", json=body)
    assert no_csrf.status_code == 403

    client.post(
        "/api/users",
        json={"username": "docker-operator-viewer", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    with TestClient(app) as viewer_client:
        viewer = login(viewer_client, "docker-operator-viewer", "viewer password 123")
        denied = viewer_client.post(
            f"/api/docker/hosts/{agent['agent_id']}/actions",
            json=body,
            headers=headers(viewer),
        )
        assert denied.status_code == 403

    exec_denied = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/containers/{container_id}/exec",
        json={"shell": "sh", "command": "id"},
        headers=headers(admin),
    )
    assert exec_denied.status_code == 409
    assert "exec" in exec_denied.json()["detail"].lower()

    cleanup_denied = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/cleanup",
        json={"scope": "dangling_images"},
        headers=headers(admin),
    )
    assert cleanup_denied.status_code == 409
    assert "cleanup" in cleanup_denied.json()["detail"].lower()


def test_one_active_docker_mutation_per_host(client, admin):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    body = {"action": "container.stop", "container_id": container_row(1)["id"]}
    first = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json=body,
        headers=headers(admin),
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "container.restart", "container_id": container_row(2)["id"]},
        headers=headers(admin),
    )
    assert second.status_code == 409
    assert "mutation" in second.json()["detail"].lower()

    # Read-only work is still allowed while a mutation is queued.
    inspect = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "container.inspect", "container_id": container_row(1)["id"]},
        headers=headers(admin),
    )
    assert inspect.status_code == 200, inspect.text


def test_docker_action_offline_policy(settings):
    app = create_app(replace(settings, offline_command_policy="reject"))
    with TestClient(app) as client:
        admin = login(client)
        enable_docker_profile(client, admin)
        agent = enroll_agent(admin)
        assert upload_inventory(client, admin, agent).status_code == 200
        with app.state.db.connection() as conn:
            conn.execute(
                "UPDATE agents SET last_seen = ? WHERE id = ?",
                ("2026-01-01T00:00:00Z", agent["agent_id"]),
            )
        response = client.post(
            f"/api/docker/hosts/{agent['agent_id']}/actions",
            json={"action": "container.stop", "container_id": container_row(1)["id"]},
            headers=headers(admin),
        )
        assert response.status_code == 409
        assert "offline" in response.json()["detail"].lower()


def test_docker_cleanup_preview_and_request(client, admin, app):
    enable_docker_profile(client, admin, prune_allowed=True)
    agent = enroll_agent(admin)
    payload = inventory_payload(
        containers=[
            container_row(
                3,
                name="orphan",
                state="exited",
                status="Exited (0) 1 hour ago",
                health="",
                exit_code=0,
                labels={},
                compose_project="",
                compose_service="",
                compose_config_hash="",
                mounts=[],
            )
        ],
        images=[
            {
                "id": "sha256:" + "a" * 64,
                "repo_tags": [],
                "repo_digests": [],
                "size": 1000,
                "shared_size": 0,
                "containers": 0,
                "dangling": True,
                "created_at": "2026-11-11T10:00:00Z",
            }
        ],
    )
    assert upload_inventory(client, admin, agent, payload).status_code == 200

    preview = client.get(f"/api/docker/hosts/{agent['agent_id']}/cleanup")
    assert preview.status_code == 200, preview.text
    scopes = {item["scope"]: item for item in preview.json()["scopes"]}
    assert scopes["stopped_containers"]["count"] == 1
    assert scopes["stopped_containers"]["candidates"][0]["name"] == "orphan"
    assert scopes["dangling_images"]["count"] == 1
    assert scopes["build_cache"]["estimated"] is False
    with app.state.db.connection() as conn:
        before = conn.execute("SELECT COUNT(*) AS n FROM commands").fetchone()["n"]
    assert before == 0

    cleanup = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/cleanup",
        json={"scope": "dangling_images"},
        headers=headers(admin),
    )
    assert cleanup.status_code == 200, cleanup.text
    assert cleanup.json()["args"] == {
        "action": "cleanup",
        "scope": "dangling_images",
        "include_compose": False,
    }

    action_cleanup = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "cleanup", "scope": "dangling_images"},
        headers=headers(admin),
    )
    assert action_cleanup.status_code == 422


def test_docker_result_audit_and_event(client, admin, app):
    enable_docker_profile(client, admin)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    queued = client.post(
        f"/api/docker/hosts/{agent['agent_id']}/actions",
        json={"action": "container.stop", "container_id": container_row(1)["id"]},
        headers=headers(admin),
    )
    assert queued.status_code == 200
    command_id = queued.json()["id"]
    beat = heartbeat(admin, agent, sample_metrics(docker=docker_summary()))
    assert any(item["id"] == command_id for item in beat["commands"])

    result = client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command_id}/result",
        json={
            "status": "succeeded",
            "exit_code": 0,
            "stdout": "Total reclaimed space: 2MB",
            "stderr": "",
        },
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert result.status_code == 200, result.text

    events = client.get(f"/api/agents/{agent['agent_id']}/events?hours=24")
    assert events.status_code == 200
    docker_events = [event for event in events.json()["events"] if event["kind"] == "docker.action"]
    assert docker_events
    assert docker_events[0]["detail"]["reclaimed_bytes"] == 2 * 1024**2

    with app.state.db.connection() as conn:
        audits = conn.execute(
            "SELECT action, detail FROM audit_log WHERE action LIKE 'docker.%' ORDER BY id"
        ).fetchall()
    assert any(row["action"] == "docker.action_requested" for row in audits)
    result_audit = next(row for row in audits if row["action"] == "docker.action_result")
    assert '"reclaimed_bytes":2097152' in result_audit["detail"]
    assert "command" not in result_audit["detail"]


def compose_files(compose: str | None = None, env: str = "SECRET_TOKEN=never-return-this\n") -> dict[str, str]:
    return {
        "compose.yaml": compose or "services:\n  web:\n    image: nginx:latest\n    ports:\n      - '8080:80'\n",
        ".env": env,
    }


def create_stack(client, admin, slug: str = "web", files: dict | None = None):
    return client.post(
        "/api/docker/stacks",
        json={
            "slug": slug,
            "name": f"{slug.title()} stack",
            "description": "test stack",
            "files": files or compose_files(),
            "message": "initial",
        },
        headers=headers(admin),
    )


def deploy_stack(
    client,
    admin,
    stack_id: str,
    agent_id: str,
    *,
    revision_hash: str = "",
    project_name: str = "",
    action: str = "up",
    pull_before_up: bool = False,
    remove_volumes: bool = False,
):
    return client.post(
        f"/api/docker/stacks/{stack_id}/deploy",
        json={
            "agent_id": agent_id,
            "revision_hash": revision_hash,
            "project_name": project_name,
            "action": action,
            "pull_before_up": pull_before_up,
            "remove_volumes": remove_volumes,
        },
        headers=headers(admin),
    )


def finish_docker_command(client, admin, agent, command_id: str, *, status="succeeded", stdout="ok"):
    heartbeat(admin, agent, sample_metrics(docker=docker_summary()))
    return client.post(
        f"/api/agent/{agent['agent_id']}/commands/{command_id}/result",
        json={"status": status, "exit_code": 0 if status == "succeeded" else 1, "stdout": stdout, "stderr": ""},
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )


def test_docker_stack_crud_and_env_redaction(app, client, admin):
    enable_docker_profile(client, admin)
    created = create_stack(client, admin)
    assert created.status_code == 200, created.text
    stack = created.json()["stack"]
    assert stack["slug"] == "web"
    assert stack["current_revision_hash"]
    assert stack["contents"][".env"] == "SECRET_TOKEN=never-return-this\n"

    listed = client.get("/api/docker/stacks")
    assert listed.status_code == 200
    assert listed.json()["stacks"][0]["deployed_host_count"] == 0

    client.post(
        "/api/users",
        json={"username": "compose-viewer", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    client.post(
        "/api/users",
        json={"username": "compose-operator", "role": "operator", "password": "operator password 123"},
        headers=headers(admin),
    )
    with TestClient(app) as viewer_client:
        viewer = login(viewer_client, "compose-viewer", "viewer password 123")
        viewer_detail = viewer_client.get(f"/api/docker/stacks/{stack['id']}")
        assert viewer_detail.status_code == 200
        assert viewer_detail.json()["stack"]["contents"] is None
        assert viewer_detail.json()["stack"]["files"]
        assert "SECRET_TOKEN" not in viewer_detail.text
        denied = viewer_client.post("/api/docker/stacks", json={"slug": "nope", "name": "Nope", "files": compose_files()}, headers=headers(viewer))
        assert denied.status_code == 403
    with TestClient(app) as operator_client:
        operator = login(operator_client, "compose-operator", "operator password 123")
        operator_detail = operator_client.get(f"/api/docker/stacks/{stack['id']}")
        assert operator_detail.status_code == 200
        assert operator_detail.json()["stack"]["contents"] is None
        assert "SECRET_TOKEN" not in operator_detail.text
        denied = operator_client.put(
            f"/api/docker/stacks/{stack['id']}",
            json={"name": "Nope"},
            headers=headers(operator),
        )
        assert denied.status_code == 403

    updated = client.put(
        f"/api/docker/stacks/{stack['id']}",
        json={"name": "Web v2", "files": compose_files("services:\n  web:\n    image: nginx:1.27\n"), "message": "update"},
        headers=headers(admin),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["stack"]["name"] == "Web v2"
    assert len(updated.json()["stack"]["revisions"]) == 2
    assert updated.json()["stack"]["current_revision_hash"] != stack["current_revision_hash"]

    deleted = client.delete(f"/api/docker/stacks/{stack['id']}", headers=headers(admin))
    assert deleted.status_code == 204
    assert client.get(f"/api/docker/stacks/{stack['id']}").status_code == 404


def test_docker_deploy_bundle_collision_and_history(client, admin):
    enable_docker_profile(client, admin, compose_allowed=True)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    created = create_stack(client, admin)
    stack = created.json()["stack"]
    revision = stack["current_revision_hash"]

    deployed = deploy_stack(
        client,
        admin,
        stack["id"],
        agent["agent_id"],
        revision_hash=revision,
        project_name="webapp",
        pull_before_up=True,
    )
    assert deployed.status_code == 200, deployed.text
    body = deployed.json()
    deployment = body["deployment"]
    command = body["command"]
    assert deployment["action"] == "up"
    assert deployment["status"] == "queued"
    assert set(command["args"]) == {"action", "deployment_id", "stack_id", "revision_hash", "project_name"}
    assert command["args"]["project_name"] == "webapp"

    history = client.get(f"/api/docker/deployments?stack_id={stack['id']}")
    assert history.status_code == 200
    assert history.json()["deployments"][0]["id"] == deployment["id"]

    bundle = client.get(
        f"/api/agent/{agent['agent_id']}/docker/deployments/{deployment['id']}/bundle",
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert bundle.status_code == 200
    assert bundle.content.startswith(b"\x1f\x8b")
    assert bundle.headers["x-bundle-sha256"]
    assert bundle.headers["x-compose-pull"] == "1"
    assert bundle.headers["x-compose-revision"] == revision

    other = enroll_agent(admin, hostname="other-docker", machine_id="other-docker")
    denied_bundle = client.get(
        f"/api/agent/{other['agent_id']}/docker/deployments/{deployment['id']}/bundle",
        headers={"Authorization": f"Bearer {other['agent_token']}"},
    )
    assert denied_bundle.status_code == 404

    second = create_stack(client, admin, slug="second", files=compose_files())
    assert second.status_code == 200
    collision = deploy_stack(
        client,
        admin,
        second.json()["stack"]["id"],
        agent["agent_id"],
        project_name="webapp",
    )
    assert collision.status_code == 409
    assert "already active" in collision.json()["detail"].lower()

    finished = finish_docker_command(
        client, admin, agent, command["id"], stdout="Container webapp-web-1 Started"
    )
    assert finished.status_code == 200, finished.text
    deployment_detail = client.get(f"/api/docker/deployments/{deployment['id']}")
    assert deployment_detail.status_code == 200
    assert deployment_detail.json()["deployment"]["status"] == "succeeded"
    assert deployment_detail.json()["deployment"]["active"] is True

    client.put(
        f"/api/docker/stacks/{stack['id']}",
        json={"files": compose_files("services:\n  web:\n    image: nginx:1.27\n"), "message": "newer"},
        headers=headers(admin),
    )
    drifted = client.get(f"/api/docker/deployments?stack_id={stack['id']}")
    drifted_row = next(item for item in drifted.json()["deployments"] if item["id"] == deployment["id"])
    assert drifted_row["drift"] is True

    down = deploy_stack(
        client,
        admin,
        stack["id"],
        agent["agent_id"],
        project_name="webapp",
        action="down",
    )
    assert down.status_code == 200, down.text
    failed = finish_docker_command(
        client, admin, agent, down.json()["command"]["id"], status="failed", stdout=""
    )
    assert failed.status_code == 200
    assert client.get(f"/api/docker/deployments/{deployment['id']}").json()["deployment"]["active"] is True
    assert client.get(f"/api/docker/deployments/{down.json()['deployment']['id']}").json()["deployment"]["status"] == "failed"

    blocked_delete = client.delete(f"/api/docker/stacks/{stack['id']}", headers=headers(admin))
    assert blocked_delete.status_code == 409


def test_docker_deploy_rejects_external_project_collision(client, admin):
    enable_docker_profile(client, admin, compose_allowed=True)
    agent = enroll_agent(admin)
    payload = inventory_payload(
        containers=[
            container_row(
                1,
                labels={"com.docker.compose.project": "externalapp"},
                compose_project="externalapp",
                compose_service="web",
            )
        ]
    )
    assert upload_inventory(client, admin, agent, payload).status_code == 200
    stack = create_stack(client, admin).json()["stack"]
    response = deploy_stack(
        client, admin, stack["id"], agent["agent_id"], project_name="externalapp"
    )
    assert response.status_code == 409
    assert "external" in response.json()["detail"].lower()


def test_docker_deployment_cancel_and_rbac(app, client, admin):
    enable_docker_profile(client, admin, compose_allowed=True)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    stack = create_stack(client, admin).json()["stack"]
    deployed = deploy_stack(client, admin, stack["id"], agent["agent_id"], project_name="cancelapp")
    assert deployed.status_code == 200
    command_id = deployed.json()["command"]["id"]
    deployment_id = deployed.json()["deployment"]["id"]

    client.post(
        "/api/users",
        json={"username": "compose-deploy-viewer", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    with TestClient(app) as viewer_client:
        viewer = login(viewer_client, "compose-deploy-viewer", "viewer password 123")
        denied = viewer_client.post(
            f"/api/docker/stacks/{stack['id']}/deploy",
            json={"agent_id": agent["agent_id"], "action": "up"},
            headers=headers(viewer),
        )
        assert denied.status_code == 403

    cancelled = client.post(f"/api/commands/{command_id}/cancel", json={}, headers=headers(admin))
    assert cancelled.status_code == 200
    assert client.get(f"/api/docker/deployments/{deployment_id}").json()["deployment"]["status"] == "cancelled"


def test_docker_settings_and_env_lock(settings):
    app = create_app(replace(settings, docker_compose_dir="", docker_compose_dir_from_env=False))
    with TestClient(app) as client:
        admin = login(client)
        enabled = client.get("/api/docker/settings", headers=headers(admin))
        assert enabled.status_code == 200
        data = enabled.json()
        assert data["editable"] is True
        assert data["source"] == "database"
        client.post(
            "/api/users",
            json={"username": "compose-settings-viewer", "role": "viewer", "password": "viewer password 123"},
            headers=headers(admin),
        )
        with TestClient(app) as viewer_client:
            viewer = login(viewer_client, "compose-settings-viewer", "viewer password 123")
            assert viewer_client.get("/api/docker/settings", headers=headers(viewer)).status_code == 403

        moved = settings.data_dir / "moved-compose"
        patched = client.patch(
            "/api/docker/settings",
            json={"compose_dir": str(moved)},
            headers=headers(admin),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["compose_dir"] == str(moved)
        assert moved.exists()

    locked = create_app(
        replace(
            settings,
            docker_compose_dir=str(settings.data_dir / "locked-compose"),
            docker_compose_dir_from_env=True,
        )
    )
    with TestClient(locked) as client:
        admin = login(client)
        locked_settings = client.get("/api/docker/settings", headers=headers(admin))
        assert locked_settings.status_code == 200
        assert locked_settings.json()["editable"] is False
        assert locked_settings.json()["source"] == "env"
        denied = client.patch(
            "/api/docker/settings",
            json={"compose_dir": str(settings.data_dir / "other")},
            headers=headers(admin),
        )
        assert denied.status_code == 409


def test_docker_detail_includes_capabilities_and_no_env_values(client, admin):
    enable_docker_profile(client, admin, exec_allowed=True, prune_allowed=False)
    agent = enroll_agent(admin)
    assert upload_inventory(client, admin, agent).status_code == 200
    detail = client.get(f"/api/docker/hosts/{agent['agent_id']}")
    assert detail.status_code == 200
    capabilities = detail.json()["host"]["capabilities"]
    assert capabilities == {"mutate": True, "exec": True, "prune": False, "compose": False}
    serialized = detail.text.lower()
    assert "secret" not in serialized
    assert "env" not in serialized
