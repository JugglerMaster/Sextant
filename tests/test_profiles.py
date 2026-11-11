from __future__ import annotations

from tests.helpers import enroll_agent, headers, heartbeat, login, sample_metrics


def test_seeded_profiles_and_rbac(client, admin):
    listing = client.get("/api/profiles", headers=headers(admin))
    assert listing.status_code == 200
    profiles = {profile["slug"]: profile for profile in listing.json()["profiles"]}
    assert set(profiles) == {"workstation", "server"}
    assert profiles["workstation"]["settings"]["collection"]["package_inventory_seconds"] == 86400
    assert profiles["server"]["settings"]["collection"]["package_inventory_seconds"] == 21600
    assert profiles["server"]["settings"]["collection"]["service_inventory_seconds"] == 3600
    assert profiles["workstation"]["settings"]["modules"]["package_management"] is True
    assert profiles["workstation"]["settings"]["actions"]["allow_packages"] is True
    assert profiles["server"]["settings"]["modules"]["package_management"] is True
    assert profiles["workstation"]["settings"]["modules"]["docker"] is False
    assert profiles["server"]["settings"]["modules"]["docker"] is False
    assert profiles["workstation"]["settings"]["actions"]["allow_docker"] is False
    assert profiles["server"]["settings"]["collection"]["docker_inventory_seconds"] == 300

    client.post(
        "/api/users",
        json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    viewer = login(client, "viewer1", "viewer password 123")
    assert client.get("/api/profiles", headers=headers(viewer)).status_code == 200
    denied = client.post(
        "/api/profiles",
        json={"slug": "custom", "name": "Custom"},
        headers=headers(viewer),
    )
    assert denied.status_code == 403


def test_profile_create_update_reset_and_delete(client, admin):
    created = client.post(
        "/api/profiles",
        json={"slug": "lab", "name": "Lab", "description": "Lab profile", "settings": {"heartbeat_seconds": 45}},
        headers=headers(admin),
    )
    assert created.status_code == 200, created.text
    profile = created.json()
    assert profile["settings"]["heartbeat_seconds"] == 45
    assert profile["revision"] == 1

    updated = client.patch(
        f"/api/profiles/{profile['id']}",
        json={"name": "Lab v2", "settings": {"heartbeat_seconds": 90}},
        headers=headers(admin),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Lab v2"
    assert updated.json()["settings"]["heartbeat_seconds"] == 90
    assert updated.json()["revision"] == 2

    reset = client.post("/api/profiles/prof_server/reset", headers=headers(admin))
    assert reset.status_code == 200
    assert reset.json()["revision"] >= 2

    system_delete = client.delete("/api/profiles/prof_server", headers=headers(admin))
    assert system_delete.status_code == 400

    deleted = client.delete(f"/api/profiles/{profile['id']}", headers=headers(admin))
    assert deleted.status_code == 200


def test_enrollment_applies_profile_and_heartbeat_syncs(client, admin):
    agent = enroll_agent(admin, profile="server")
    detail = client.get(f"/api/agents/{agent['agent_id']}", headers=headers(admin))
    assert detail.status_code == 200
    assert detail.json()["agent"]["profile_id"] == "prof_server"

    beat = heartbeat(admin, agent, sample_metrics())
    assert beat["profile"]["slug"] == "server"
    assert beat["profile"]["collection"]["package_inventory_seconds"] == 21600
    assert beat["profile"]["heartbeat_seconds"] == 30
    assert beat["policy"]["allow_packages"] is True


def test_admin_can_reassign_profile_without_reenrollment(client, admin):
    agent = enroll_agent(admin)
    response = client.patch(
        f"/api/agents/{agent['agent_id']}",
        json={"profile": "server"},
        headers=headers(admin),
    )
    assert response.status_code == 200
    assert response.json()["profile_id"] == "prof_server"


def test_tag_profile_assignment_applies_to_members(client, admin):
    first = enroll_agent(admin, hostname="tagged-1", machine_id="m1")
    second = enroll_agent(admin, hostname="tagged-2", machine_id="m2")
    response = client.put(
        "/api/profile-tags/lab",
        json={"profile": "server"},
        headers=headers(admin),
    )
    assert response.status_code == 200
    assert response.json()["agents"] == 2
    for agent in (first, second):
        detail = client.get(f"/api/agents/{agent['agent_id']}", headers=headers(admin)).json()
        assert detail["agent"]["profile_id"] == "prof_server"

    client.patch(
        f"/api/agents/{first['agent_id']}",
        json={"profile": "workstation"},
        headers=headers(admin),
    )
    detail = client.get(f"/api/agents/{first['agent_id']}", headers=headers(admin)).json()
    assert detail["agent"]["profile_id"] == "prof_workstation"

    listing = client.get("/api/profile-tags", headers=headers(admin))
    assert listing.json()["assignments"][0]["tag"] == "lab"
    deleted = client.delete("/api/profile-tags/lab", headers=headers(admin))
    assert deleted.status_code == 200


def test_docker_profile_gate_and_cadence_sync_on_heartbeat(client, admin):
    profile = next(
        item for item in client.get("/api/profiles").json()["profiles"] if item["slug"] == "workstation"
    )
    settings = profile["settings"]
    settings["modules"]["docker"] = True
    settings["actions"]["allow_docker"] = True
    settings["collection"]["docker_inventory_seconds"] = 120
    updated = client.patch(
        f"/api/profiles/{profile['id']}",
        json={"settings": settings},
        headers=headers(admin),
    )
    assert updated.status_code == 200, updated.text

    agent = enroll_agent(admin)
    beat = heartbeat(admin, agent, sample_metrics())
    assert beat["profile"]["modules"]["docker"] is True
    assert beat["profile"]["collection"]["docker_inventory_seconds"] == 120
    assert beat["policy"]["allow_docker"] is True
    assert beat["policy"]["allow_docker_exec"] is False
    assert beat["policy"]["allow_docker_compose"] is False


def test_unknown_profile_is_rejected(client, admin):
    response = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": client.post(
                "/api/enrollment-tokens", json={"label": "x"}, headers=headers(admin)
            ).json()["token"],
            "hostname": "bad-profile",
            "machine_id": "",
            "os": "linux",
            "profile": "does-not-exist",
        },
    )
    assert response.status_code == 400
