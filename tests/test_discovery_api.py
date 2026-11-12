from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from sextant_control.config import DiscoverySettings, Settings
from sextant_control.db import Database, dumps, loads, utcnow
from sextant_control.discovery import ACKNOWLEDGEMENT, DeviceObservation, merge_observation
from sextant_control.server import create_app
from tests.helpers import ADMIN_PASSWORD, enroll_agent, headers, login

STATIC_DIR = Path(__file__).resolve().parents[1] / "sextant_control" / "static"


def _settings(tmp_path: Path, **overrides) -> Settings:
    discovery_values = {
        "enabled": True,
        "cidrs": ("192.168.1.0/24",),
        "exclude": (),
        "methods": ("neigh", "dns", "oui", "ssdp"),
        "probe_ports": (22, 80),
        "active_probes": False,
        "interval_hours": 0,
        "max_hosts": 256,
        "concurrency": 4,
        "timeout_ms": 500,
        "retention_days": 90,
        "agent_relay": False,
        "export_limit": 1000,
    }
    discovery_values.update(overrides)
    return Settings(
        data_dir=tmp_path,
        static_dir=STATIC_DIR,
        secret_key=b"test-secret-key-that-is-long-enough",
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        cookie_secure=False,
        offline_after_seconds=90,
        enrollment_ttl_minutes=30,
        discovery=DiscoverySettings(**discovery_values),
    )


def _ack(session: dict) -> None:
    response = session["client"].patch(
        "/api/discovery/settings",
        json={"acknowledged": True, "acknowledgement": ACKNOWLEDGEMENT},
        headers=headers(session),
    )
    assert response.status_code == 200, response.text


def _seed_devices(database: Database) -> list[dict]:
    first = merge_observation(
        database,
        DeviceObservation(
            ip="192.168.1.10",
            mac="b8:27:eb:11:22:33",
            hostname="printer.lan",
            segment="192.168.1.0/24",
            open_ports=[22, 80],
            services={"22": {"protocol": "ssh", "banner": "SSH-2.0-OpenSSH_9.6"}},
        ),
    )
    second = merge_observation(
        database,
        DeviceObservation(
            ip="192.168.1.20",
            mac="00:50:56:aa:bb:cc",
            hostname="esxi.lan",
            segment="192.168.1.0/24",
            open_ports=[443, 8006],
        ),
    )
    return [first, second]


def _wait_for_scan(client: TestClient, scan_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        response = client.get(f"/api/discovery/scans/{scan_id}")
        assert response.status_code == 200, response.text
        last = response.json()["scan"]
        if last["status"] in statuses:
            return last
        time.sleep(0.02)
    raise AssertionError(f"scan {scan_id} did not reach {statuses}: {last}")


def test_discovery_settings_and_audited_acknowledgement(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        admin = login(client)
        response = client.get("/api/discovery/settings")
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["acknowledged"] is False
        assert payload["cidrs"] == ["192.168.1.0/24"]
        assert payload["acknowledgement_text"] == ACKNOWLEDGEMENT

        wrong = client.patch(
            "/api/discovery/settings",
            json={"acknowledged": True, "acknowledgement": "yes"},
            headers=headers(admin),
        )
        assert wrong.status_code == 400

        _ack(admin)
        assert client.get("/api/discovery/settings").json()["acknowledged"] is True
        audit = client.get("/api/audit").json()["events"]
        assert any(event["action"] == "discovery.acknowledged" for event in audit)

        cleared = client.patch(
            "/api/discovery/settings",
            json={"acknowledged": False, "acknowledgement": ""},
            headers=headers(admin),
        )
        assert cleared.status_code == 200
        assert cleared.json()["acknowledged"] is False


def test_scan_is_blocked_when_disabled_or_unacknowledged(tmp_path):
    disabled = create_app(_settings(tmp_path / "disabled", enabled=False))
    with TestClient(disabled) as client:
        admin = login(client)
        _ack(admin)
        response = client.post(
            "/api/discovery/scan",
            json={"scope": [], "methods": []},
            headers=headers(admin),
        )
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    unacknowledged = create_app(_settings(tmp_path / "unacknowledged"))
    with TestClient(unacknowledged) as client:
        admin = login(client)
        response = client.post(
            "/api/discovery/scan",
            json={"scope": [], "methods": []},
            headers=headers(admin),
        )
        assert response.status_code == 403
        assert "acknowledgement" in response.json()["detail"].lower()


def test_scan_lifecycle_finish_and_cancel(tmp_path):
    app = create_app(_settings(tmp_path))
    release = threading.Event()

    def fake_executor(database, config, scan, cancel_event, progress):
        progress(25, 1)
        release.wait(0.02)
        while not release.wait(0.02):
            if cancel_event.is_set():
                return {"status": "cancelled", "hosts_found": 0, "error": ""}
        return {"status": "succeeded", "hosts_found": 2, "error": ""}

    app.state.discovery_manager.executor = fake_executor
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        start = client.post(
            "/api/discovery/scan",
            json={"scope": ["192.168.1.0/24"], "methods": ["neigh"]},
            headers=headers(admin),
        )
        assert start.status_code == 200, start.text
        scan = start.json()["scan"]
        assert scan["status"] in {"queued", "running"}
        _wait_for_scan(client, scan["id"], {"running"})
        cancel = client.post(
            f"/api/discovery/scans/{scan['id']}/cancel",
            json={},
            headers=headers(admin),
        )
        assert cancel.status_code == 200
        done = _wait_for_scan(client, scan["id"], {"cancelled"})
        assert done["status"] == "cancelled"
        audit_actions = {event["action"] for event in client.get("/api/audit").json()["events"]}
        assert "discovery.scan_started" in audit_actions
        assert "discovery.scan_cancel_requested" in audit_actions
        assert "discovery.scan_cancelled" in audit_actions


def test_scan_records_success_and_rejects_concurrent_scans(tmp_path):
    app = create_app(_settings(tmp_path))
    release = threading.Event()

    def fake_executor(database, config, scan, cancel_event, progress):
        release.wait(1.0)
        return {"status": "succeeded", "hosts_found": 3, "error": ""}

    app.state.discovery_manager.executor = fake_executor
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        first = client.post(
            "/api/discovery/scan",
            json={"scope": [], "methods": ["neigh"]},
            headers=headers(admin),
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/discovery/scan",
            json={"scope": [], "methods": ["neigh"]},
            headers=headers(admin),
        )
        assert second.status_code == 429
        release.set()
        done = _wait_for_scan(client, first.json()["scan"]["id"], {"succeeded"})
        assert done["hosts_found"] == 3
        assert done["progress"] == 100
        history = client.get("/api/discovery/scans").json()
        assert history["total"] >= 1


def test_scan_scope_host_cap_and_acknowledgement_checks(tmp_path):
    app = create_app(
        _settings(tmp_path, max_hosts=16, methods=("neigh", "tcp"), active_probes=False)
    )
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        out_of_scope = client.post(
            "/api/discovery/scan",
            json={"scope": ["192.168.2.0/24"], "methods": ["neigh"]},
            headers=headers(admin),
        )
        assert out_of_scope.status_code == 400
        assert "outside" in out_of_scope.json()["detail"].lower()

        too_large = client.post(
            "/api/discovery/scan",
            json={"scope": ["192.168.1.0/24"], "methods": ["neigh"]},
            headers=headers(admin),
        )
        assert too_large.status_code == 400
        assert "max_hosts" in too_large.json()["detail"].lower()

        tcp_without_active = client.post(
            "/api/discovery/scan",
            json={"scope": ["192.168.1.0/28"], "methods": ["tcp"]},
            headers=headers(admin),
        )
        assert tcp_without_active.status_code == 400
        assert "ACTIVE_PROBES" in tcp_without_active.json()["detail"]


def test_device_list_detail_filters_patch_and_delete(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    first, second = _seed_devices(database)
    with TestClient(app) as client:
        admin = login(client)
        listing = client.get("/api/discovery/devices").json()
        assert listing["total"] == 2
        assert listing["summary"]["total"] == 2
        assert listing["summary"]["new"] == 2
        states = client.get("/api/discovery/devices?state=new").json()
        assert states["total"] == 2
        filtered = client.get("/api/discovery/devices?q=printer").json()
        assert filtered["total"] == 1
        assert filtered["devices"][0]["id"] == first["id"]
        typed = client.get("/api/discovery/devices?type=hypervisor").json()
        assert typed["total"] == 1

        detail = client.get(f"/api/discovery/devices/{first['id']}").json()
        assert detail["device"]["id"] == first["id"]
        assert detail["events"][0]["kind"] == "new_device"

        patch = client.patch(
            f"/api/discovery/devices/{first['id']}",
            json={"tags": ["lab", "lab"], "notes": "on the shelf", "state": "known"},
            headers=headers(admin),
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["device"]["tags"] == ["lab"]
        assert patch.json()["device"]["notes"] == "on the shelf"
        assert patch.json()["device"]["state"] == "known"

        ignored = client.get("/api/discovery/devices?state=known").json()
        assert ignored["total"] == 1

        deleted = client.delete(
            f"/api/discovery/devices/{second['id']}", headers=headers(admin)
        )
        assert deleted.status_code == 204
        assert client.get(f"/api/discovery/devices/{second['id']}").status_code == 404
        actions = {event["action"] for event in client.get("/api/audit").json()["events"]}
        assert {"discovery.device_updated", "discovery.device_deleted"} <= actions


def test_discovery_rbac_and_csrf(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    first, _ = _seed_devices(database)
    with TestClient(app) as admin_client:
        admin = login(admin_client)
        _ack(admin)
        created = admin_client.post(
            "/api/users",
            json={
                "username": "viewer1",
                "display_name": "Viewer",
                "role": "viewer",
                "password": "correct horse battery staple",
            },
            headers=headers(admin),
        )
        assert created.status_code == 200, created.text
        created_operator = admin_client.post(
            "/api/users",
            json={
                "username": "operator1",
                "display_name": "Operator",
                "role": "operator",
                "password": "correct horse battery staple",
            },
            headers=headers(admin),
        )
        assert created_operator.status_code == 200, created_operator.text

        # Missing CSRF is rejected for a state-changing discovery endpoint.
        no_csrf = admin_client.post(
            "/api/discovery/scan", json={"scope": [], "methods": []}
        )
        assert no_csrf.status_code == 403

        with TestClient(app) as viewer_client:
            viewer = login(viewer_client, username="viewer1")
            assert viewer_client.get("/api/discovery/devices").status_code == 200
            assert (
                viewer_client.post(
                    "/api/discovery/scan",
                    json={"scope": [], "methods": []},
                    headers=headers(viewer),
                ).status_code
                == 403
            )
            assert (
                viewer_client.patch(
                    f"/api/discovery/devices/{first['id']}",
                    json={"tags": ["nope"]},
                    headers=headers(viewer),
                ).status_code
                == 403
            )
            assert (
                viewer_client.delete(
                    f"/api/discovery/devices/{first['id']}", headers=headers(viewer)
                ).status_code
                == 403
            )
            assert (
                viewer_client.patch(
                    "/api/discovery/settings",
                    json={"acknowledged": True, "acknowledgement": ACKNOWLEDGEMENT},
                    headers=headers(viewer),
                ).status_code
                == 403
            )

        with TestClient(app) as operator_client:
            operator = login(operator_client, username="operator1")
            assert (
                operator_client.patch(
                    f"/api/discovery/devices/{first['id']}",
                    json={"notes": "operator edit"},
                    headers=headers(operator),
                ).status_code
                == 200
            )
            # Only admins may change acknowledgement or delete devices.
            assert (
                operator_client.patch(
                    "/api/discovery/settings",
                    json={"acknowledged": True, "acknowledgement": ACKNOWLEDGEMENT},
                    headers=headers(operator),
                ).status_code
                == 403
            )
            assert (
                operator_client.delete(
                    f"/api/discovery/devices/{first['id']}", headers=headers(operator)
                ).status_code
                == 403
            )


def test_discovery_retention_prunes_scans_and_events_but_keeps_devices(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    device = _seed_devices(database)[0]
    old = (utcnow() - timedelta(days=5)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with database.connection() as conn:
        conn.execute(
            """INSERT INTO discovery_scans
               (id, started_at, scope, methods, initiated_by, status)
               VALUES ('dsc_old', ?, '[]', '[]', 'admin', 'succeeded')""",
            (old,),
        )
        conn.execute(
            """INSERT INTO discovery_events (at, device_id, kind, detail)
               VALUES (?, ?, 'new_device', '{}')""",
            (old, device["id"]),
        )
    scans, events = database.prune_discovery(1)
    assert scans == 1
    assert events >= 1
    assert database.get_discovery_scan("dsc_old") is None
    assert database.get_discovered_device(device["id"]) is not None


def _eligible_agent(app, admin, name: str = "relay-box", ip: str = "192.168.1.10") -> dict:
    agent = enroll_agent(admin, hostname=name)
    database = app.state.db
    with database.transaction() as conn:
        row = conn.execute("SELECT profile_id, policy FROM agents WHERE id = ?", (agent["agent_id"],)).fetchone()
        profile = conn.execute(
            "SELECT settings FROM agent_profiles WHERE id = ?", (row["profile_id"],)
        ).fetchone()
        settings = loads(profile["settings"], {})
        modules = settings.get("modules") if isinstance(settings.get("modules"), dict) else {}
        modules["discovery"] = True
        settings["modules"] = modules
        conn.execute(
            "UPDATE agent_profiles SET settings = ?, revision = revision + 1 WHERE id = ?",
            (dumps(settings), row["profile_id"]),
        )
        policy = loads(row["policy"], {})
        policy["allow_discovery"] = True
        conn.execute(
            "UPDATE agents SET policy = ?, ip = ? WHERE id = ?",
            (dumps(policy), ip, agent["agent_id"]),
        )
    agent["client"] = admin["client"]
    return agent


def _agent_scan(admin, agent_id: str, scope=None, methods=None):
    return admin["client"].post(
        f"/api/agents/{agent_id}/discover",
        json={
            "scope": scope if scope is not None else ["192.168.1.0/24"],
            "methods": methods if methods is not None else ["neigh", "dns", "oui"],
        },
        headers=headers(admin),
    )


def _report(agent: dict, scan_id: str, devices: list[dict], **overrides):
    payload = {
        "scan_id": scan_id,
        "collected_at": "2026-11-12T08:00:00Z",
        "segment": "192.168.1.0/24",
        "devices": devices,
        **overrides,
    }
    return agent["client"].post(
        f"/api/agent/{agent['agent_id']}/discovery/report",
        json=payload,
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )


def _report_device(**overrides):
    values = {
        "ip": "192.168.1.40",
        "mac": "b8:27:eb:11:22:33",
        "hostname": "printer.lan",
        "vendor": "Raspberry Pi Foundation",
        "open_ports": [22, 80, 631],
        "services": {
            "22": {"protocol": "ssh", "banner": "SSH-2.0-OpenSSH_9.6"},
            "80": {"protocol": "http", "status": 200, "server": "nginx"},
        },
        "evidence": {"methods": ["neigh", "dns", "oui", "tcp"], "neighbor": True, "tcp": True},
    }
    values.update(overrides)
    return values


def test_agent_relay_disabled_and_eligibility(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=False))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        agent = _eligible_agent(app, admin)
        rejected = _agent_scan(admin, agent["agent_id"])
        assert rejected.status_code == 403
        assert "AGENT_RELAY" in rejected.json()["detail"]
        listing = client.get("/api/discovery/agents").json()
        assert listing["relay_enabled"] is False
        assert listing["agents"][0]["eligible"] is False
        assert any("AGENT_RELAY" in reason for reason in listing["agents"][0]["reasons"])


def test_agent_relay_profile_policy_and_platform_gates(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=True))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        agent = _eligible_agent(app, admin)
        database = app.state.db
        with database.transaction() as conn:
            row = conn.execute("SELECT profile_id, policy FROM agents WHERE id = ?", (agent["agent_id"],)).fetchone()
            profile = conn.execute(
                "SELECT settings FROM agent_profiles WHERE id = ?", (row["profile_id"],)
            ).fetchone()
            settings = loads(profile["settings"], {})
            settings["modules"]["discovery"] = False
            conn.execute(
                "UPDATE agent_profiles SET settings = ? WHERE id = ?",
                (dumps(settings), row["profile_id"]),
            )
        blocked_profile = _agent_scan(admin, agent["agent_id"])
        assert blocked_profile.status_code == 409
        assert "profile" in blocked_profile.json()["detail"].lower()

        with database.transaction() as conn:
            profile = conn.execute(
                "SELECT settings FROM agent_profiles WHERE id = ?", (row["profile_id"],)
            ).fetchone()
            settings = loads(profile["settings"], {})
            settings["modules"]["discovery"] = True
            conn.execute(
                "UPDATE agent_profiles SET settings = ? WHERE id = ?",
                (dumps(settings), row["profile_id"]),
            )
            policy = loads(row["policy"], {})
            policy["allow_discovery"] = False
            conn.execute(
                "UPDATE agents SET policy = ? WHERE id = ?",
                (dumps(policy), agent["agent_id"]),
            )
        blocked_policy = _agent_scan(admin, agent["agent_id"])
        assert blocked_policy.status_code == 409

        with database.transaction() as conn:
            policy = loads(row["policy"], {})
            policy["allow_discovery"] = True
            conn.execute(
                "UPDATE agents SET policy = ?, os = 'windows' WHERE id = ?",
                (dumps(policy), agent["agent_id"]),
            )
        blocked_os = _agent_scan(admin, agent["agent_id"])
        assert blocked_os.status_code == 409


def test_agent_scan_lifecycle_cancel_and_ownership(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=True))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        agent = _eligible_agent(app, admin)
        other = _eligible_agent(app, admin, name="other-box", ip="192.168.1.11")
        started = _agent_scan(admin, agent["agent_id"])
        assert started.status_code == 200, started.text
        scan = started.json()["scan"]
        assert scan["agent_id"] == agent["agent_id"]
        assert scan["source"] == f"agent:{agent['agent_id']}"
        assert scan["status"] == "queued"
        command = started.json()["command"]
        assert command["kind"] == "discovery"
        assert command["args"]["action"] == "discovery.scan"
        assert command["args"]["scan_id"] == scan["id"]
        assert command["args"]["scope"] == ["192.168.1.0/24"]

        # A different agent cannot report this scan.
        wrong_owner = _report(other, scan["id"], [_report_device()])
        assert wrong_owner.status_code == 404

        cancelled = client.post(
            f"/api/discovery/scans/{scan['id']}/cancel",
            json={},
            headers=headers(admin),
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["scan"]["status"] == "cancelled"
        late = _report(agent, scan["id"], [_report_device()])
        assert late.status_code == 409
        assert client.get(f"/api/discovery/scans/{scan['id']}").json()["scan"]["status"] == "cancelled"
        history = client.get("/api/discovery/scans").json()
        assert history["scans"][0]["source"] == f"agent:{agent['agent_id']}"
        audit_actions = {event["action"] for event in client.get("/api/audit").json()["events"]}
        assert "discovery.scan_started" in audit_actions
        assert "discovery.scan_cancelled" in audit_actions


def test_agent_report_merges_source_segment_and_dedupes_events(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=True))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        agent = _eligible_agent(app, admin)
        started = _agent_scan(admin, agent["agent_id"]).json()
        first_scan = started["scan"]
        reported = _report(agent, first_scan["id"], [_report_device()])
        assert reported.status_code == 200, reported.text
        assert reported.json()["devices_merged"] == 1
        scan = client.get(f"/api/discovery/scans/{first_scan['id']}").json()["scan"]
        assert scan["status"] == "succeeded"
        assert scan["hosts_found"] == 1
        assert scan["progress"] == 100

        listing = client.get("/api/discovery/devices?q=printer").json()
        assert listing["total"] == 1
        device = listing["devices"][0]
        assert device["source"] == f"agent:{agent['agent_id']}"
        assert device["segment"] == "192.168.1.0/24"
        assert device["device_type"] == "printer"
        assert device["evidence"]["agent_id"] == agent["agent_id"]
        assert device["evidence"]["agent_relay"] is True

        # The same report is idempotent and does not duplicate events.
        again = _report(agent, first_scan["id"], [_report_device()])
        assert again.status_code == 200
        assert again.json()["idempotent"] is True
        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE commands SET status = 'succeeded', completed_at = ? WHERE id = ?",
                (utcnow().isoformat(timespec="seconds").replace("+00:00", "Z"), started["command"]["id"]),
            )
        events = client.get(
            f"/api/discovery/devices/{device['id']}"
        ).json()["events"]
        assert [event["kind"] for event in events if event["kind"] == "new_device"] == ["new_device"]

        # A second scan for the same device creates no duplicate new_device event.
        second_scan = _agent_scan(admin, agent["agent_id"]).json()["scan"]
        second_report = _report(agent, second_scan["id"], [_report_device()])
        assert second_report.status_code == 200
        assert second_report.json()["devices_merged"] == 1
        events = client.get(
            f"/api/discovery/devices/{device['id']}"
        ).json()["events"]
        assert [event["kind"] for event in events if event["kind"] == "new_device"] == ["new_device"]
        listing = client.get("/api/discovery/devices").json()
        assert listing["total"] == 1


def test_agent_report_rejects_out_of_scope_and_unusual_caps(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=True))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        agent = _eligible_agent(app, admin)
        scan = _agent_scan(admin, agent["agent_id"]).json()["scan"]
        too_many = _report(
            agent,
            scan["id"],
            [_report_device(ip=f"192.168.1.{index % 250 + 2}") for index in range(513)],
        )
        assert too_many.status_code == 422

        oversized_device = _report_device(
            evidence={"methods": ["neigh"], "blob": "a" * 8000}
        )
        oversized = _report(
            agent,
            scan["id"],
            [oversized_device] * 130,
        )
        assert oversized.status_code == 413

        out_of_scope = _report(agent, scan["id"], [_report_device(ip="192.168.2.40")])
        assert out_of_scope.status_code == 400
        assert "outside the scan scope" in out_of_scope.json()["detail"]


def test_agent_scan_scope_and_host_cap_validation(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=True, max_hosts=16))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        agent = _eligible_agent(app, admin)
        out_of_scope = _agent_scan(admin, agent["agent_id"], scope=["192.168.2.0/24"])
        assert out_of_scope.status_code == 400
        assert "outside" in out_of_scope.json()["detail"].lower()

        too_large = _agent_scan(admin, agent["agent_id"], scope=["192.168.1.0/24"])
        assert too_large.status_code == 400
        assert "MAX_HOSTS" in too_large.json()["detail"]

        wrong_segment = _agent_scan(admin, agent["agent_id"], scope=["10.20.0.0/24"])
        assert wrong_segment.status_code == 400
        assert "outside the configured discovery allowlist" in wrong_segment.json()["detail"]


def test_agent_discovery_rbac_and_csrf(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=True))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        agent = _eligible_agent(app, admin)
        created = client.post(
            "/api/users",
            json={
                "username": "viewer2",
                "display_name": "Viewer",
                "role": "viewer",
                "password": ADMIN_PASSWORD,
            },
            headers=headers(admin),
        )
        assert created.status_code == 200

        no_csrf = client.post(
            f"/api/agents/{agent['agent_id']}/discover",
            json={"scope": ["192.168.1.0/24"], "methods": ["neigh"]},
        )
        assert no_csrf.status_code == 403

        with TestClient(app) as viewer_client:
            viewer = login(viewer_client, username="viewer2")
            denied = viewer_client.post(
                f"/api/agents/{agent['agent_id']}/discover",
                json={"scope": ["192.168.1.0/24"], "methods": ["neigh"]},
                headers=headers(viewer),
            )
            assert denied.status_code == 403


def test_agent_discovery_profile_policy_sync_reaches_heartbeat(tmp_path):
    app = create_app(_settings(tmp_path, agent_relay=True))
    with TestClient(app) as client:
        admin = login(client)
        from tests.helpers import heartbeat

        baseline_agent = enroll_agent(admin, hostname="sync-box")
        baseline = heartbeat(admin, baseline_agent)
        assert baseline["policy"]["allow_discovery"] is False

        eligible = _eligible_agent(app, admin, name="sync-eligible")
        synced = heartbeat(admin, eligible)
        assert synced["policy"]["allow_discovery"] is True


def _discovered(database, *, mac: str, ip: str, hostname: str = "", ports=None, type_hint: str = "") -> dict:
    return merge_observation(
        database,
        DeviceObservation(
            ip=ip,
            mac=mac,
            hostname=hostname,
            segment="192.168.1.0/24",
            open_ports=list(ports or []),
            services={str(port): {"protocol": "tcp"} for port in (ports or [])},
        ),
    )


def test_exposure_conflicts_and_segment_summary(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    exposed = _discovered(
        database, mac="aa:bb:cc:dd:ee:10", ip="192.168.1.100", ports=[22, 8006]
    )
    _discovered(database, mac="aa:bb:cc:dd:ee:11", ip="192.168.1.100", ports=[80])
    _discovered(
        database,
        mac="aa:bb:cc:dd:ee:12",
        ip="192.168.1.101",
        hostname="shared.lan",
        ports=[80],
    )
    _discovered(
        database,
        mac="aa:bb:cc:dd:ee:13",
        ip="192.168.1.102",
        hostname="shared.lan",
        ports=[80],
    )
    with TestClient(app) as client:
        login(client)
        exposure = client.get("/api/discovery/devices?exposure=true").json()
        assert exposure["total"] == 1
        assert exposure["devices"][0]["id"] == exposed["id"]
        assert exposure["summary"]["exposure"] == 1
        assert exposure["devices"][0]["identity_conflict"] is True

        conflicts = client.get("/api/discovery/conflicts").json()
        types = {item["type"] for item in conflicts["conflicts"]}
        assert "ip_multiple_macs" in types
        assert "hostname_multiple_identities" in types
        assert conflicts["total"] >= 2

        filtered = client.get("/api/discovery/devices?conflict=true").json()
        assert filtered["total"] >= 2

        detail = client.get(f"/api/discovery/devices/{exposed['id']}").json()
        assert detail["conflicts"]
        assert detail["device"]["identity_conflict"] is True

        summary = exposure["summary"]
        assert summary["segments"]
        segment = summary["segments"][0]
        assert segment["gateway_hint"]
        assert segment["managed"] == 0
        assert segment["unmanaged"] == 4
        assert segment["exposure"] == 1


def test_discovery_export_rbac_caps_and_audit(tmp_path):
    app = create_app(_settings(tmp_path, export_limit=1))
    database = app.state.db
    _discovered(database, mac="aa:bb:cc:dd:ee:20", ip="192.168.1.110", ports=[22])
    _discovered(database, mac="aa:bb:cc:dd:ee:21", ip="192.168.1.111", ports=[443])
    with TestClient(app) as client:
        assert client.get("/api/discovery/export?format=csv").status_code == 401
        admin = login(client)
        created = client.post(
            "/api/users",
            json={
                "username": "viewer3",
                "display_name": "Viewer",
                "role": "viewer",
                "password": ADMIN_PASSWORD,
            },
            headers=headers(admin),
        )
        assert created.status_code == 200
        with TestClient(app) as viewer_client:
            viewer = login(viewer_client, username="viewer3")
            assert (
                viewer_client.get(
                    "/api/discovery/export?format=csv", headers=headers(viewer)
                ).status_code
                == 403
            )

        csv_response = client.get("/api/discovery/export?format=csv")
        assert csv_response.status_code == 200
        assert csv_response.headers["x-discovery-truncated"] == "true"
        lines = [line for line in csv_response.text.splitlines() if line]
        assert len(lines) == 2  # header + one capped row

        json_response = client.get("/api/discovery/export?format=json&exposure=false")
        assert json_response.status_code == 200
        payload = json_response.json()
        assert payload["returned"] == 1
        assert payload["truncated"] is True
        audit = client.get("/api/audit").json()["events"]
        exported = [event for event in audit if event["action"] == "discovery.exported"]
        assert exported and exported[0]["detail"]["rows"] == 1
        assert exported[0]["detail"]["cap"] == 1


def test_promote_wake_target_is_idempotent(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    device = _discovered(
        database, mac="aa:bb:cc:dd:ee:30", ip="192.168.1.120", hostname="wake-me.lan"
    )
    with TestClient(app) as client:
        admin = login(client)
        first = client.post(
            f"/api/discovery/devices/{device['id']}/wake-target",
            json={"name": "Wake me", "broadcast": "255.255.255.255", "port": 9},
            headers=headers(admin),
        )
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["duplicate"] is False
        assert result["wake_target"]["mac"].lower() == "aa:bb:cc:dd:ee:30"
        assert result["device"]["state"] == "promoted"
        assert result["device"]["wake_target_id"] == result["wake_target"]["id"]

        second = client.post(
            f"/api/discovery/devices/{device['id']}/promote",
            json={"target": "wake_target", "name": "Duplicate"},
            headers=headers(admin),
        )
        assert second.status_code == 200, second.text
        assert second.json()["duplicate"] is True
        assert second.json()["wake_target"]["id"] == result["wake_target"]["id"]
        with database.connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM wake_targets WHERE mac = ? COLLATE NOCASE",
                ("aa:bb:cc:dd:ee:30",),
            ).fetchone()["n"]
        assert count == 1
        events = client.get(f"/api/discovery/devices/{device['id']}").json()["events"]
        assert len([event for event in events if event["kind"] == "promoted"]) == 1
        actions = {event["action"] for event in client.get("/api/audit").json()["events"]}
        assert "discovery.device_promoted" in actions


def test_promote_install_tokens_are_one_time_and_redacted(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    linux = _discovered(
        database, mac="aa:bb:cc:dd:ee:40", ip="192.168.1.140", hostname="printer.lan"
    )
    windows = _discovered(
        database, mac="aa:bb:cc:dd:ee:41", ip="192.168.1.141", hostname="studio-pc"
    )
    with TestClient(app) as client:
        admin = login(client)
        linux_response = client.post(
            f"/api/discovery/devices/{linux['id']}/promote",
            json={"target": "linux_agent", "name": "printer.lan", "expires_minutes": 30},
            headers=headers(admin),
        )
        assert linux_response.status_code == 200, linux_response.text
        enrollment = linux_response.json()["enrollment"]
        token = enrollment["token"]
        assert token.startswith("sextant_enroll_")
        assert token in enrollment["install_command"]
        assert "printer.lan" in enrollment["install_command"]
        assert "192.168.1.140" in enrollment["install_command"]
        assert linux_response.json()["device"]["state"] == "promoted"

        windows_response = client.post(
            f"/api/discovery/devices/{windows['id']}/promote",
            json={"target": "windows_agent", "name": "studio-pc"},
            headers=headers(admin),
        )
        assert windows_response.status_code == 200, windows_response.text
        win_enrollment = windows_response.json()["enrollment"]
        assert win_enrollment["os"] == "windows"
        assert "PowerShell" in win_enrollment["install_command"]
        assert "WINDOWS_REMOTE_INSTALL.md" in win_enrollment["windows_plan"]
        assert any("Manual Windows install" in item for item in win_enrollment["prerequisites"])

        audit_text = json.dumps(client.get("/api/audit").json())
        tokens_text = json.dumps(client.get("/api/enrollment-tokens").json())
        for secret in (token, win_enrollment["token"]):
            assert secret not in audit_text
            assert secret not in tokens_text
        with database.connection() as conn:
            row = conn.execute(
                "SELECT token_hash FROM enrollment_tokens WHERE id = ?",
                (enrollment["id"],),
            ).fetchone()
        assert row["token_hash"] != token


def test_link_existing_agent_by_mac_and_conflict(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    device = _discovered(
        database,
        mac="AA:BB:CC:DD:EE:FF",
        ip="192.168.1.150",
        hostname="linked-box",
    )
    with TestClient(app) as client:
        admin = login(client)
        agent = enroll_agent(admin, hostname="linked-box")
        detail = client.get(f"/api/discovery/devices/{device['id']}").json()
        assert detail["agent_candidates"][0]["id"] == agent["agent_id"]
        linked = client.post(
            f"/api/discovery/devices/{device['id']}/link-agent",
            json={"agent_id": agent["agent_id"]},
            headers=headers(admin),
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["device"]["agent_id"] == agent["agent_id"]
        assert linked.json()["device"]["state"] == "promoted"
        events = client.get(f"/api/discovery/devices/{device['id']}").json()["events"]
        assert any(event["kind"] == "promoted" for event in events)

        mismatch = _discovered(
            database, mac="aa:bb:cc:dd:ee:51", ip="192.168.1.151", hostname="other"
        )
        denied = client.post(
            f"/api/discovery/devices/{mismatch['id']}/link-agent",
            json={"agent_id": agent["agent_id"]},
            headers=headers(admin),
        )
        assert denied.status_code == 409


def test_wake_verification_status_and_events(tmp_path, monkeypatch):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    device = _discovered(
        database, mac="aa:bb:cc:dd:ee:60", ip="192.168.1.160", hostname="wake-check.lan"
    )
    monkeypatch.setattr(
        "sextant_control.server.send_magic_packet",
        lambda *args, **kwargs: 3,
    )
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        target = client.post(
            f"/api/discovery/devices/{device['id']}/wake-target",
            json={"name": "Wake check"},
            headers=headers(admin),
        ).json()["wake_target"]
        sent = client.post(
            f"/api/wake-targets/{target['id']}/wake",
            json={"packets": 3},
            headers=headers(admin),
        )
        assert sent.status_code == 200
        detail = client.get(f"/api/discovery/devices/{device['id']}").json()
        assert detail["device"]["wake_status"] == "sent"

        monkeypatch.setattr(
            "sextant_control.server.verify_reachability",
            lambda *args, **kwargs: {
                "reachable": False,
                "method": "",
                "neighbor": False,
                "open_ports": [],
                "services": {},
                "checked_at": "2026-11-12T09:00:00Z",
            },
        )
        still_sent = client.post(
            f"/api/discovery/devices/{device['id']}/verify",
            json={},
            headers=headers(admin),
        )
        assert still_sent.status_code == 200
        assert still_sent.json()["verification"]["status"] == "sent"

        monkeypatch.setattr(
            "sextant_control.server.verify_reachability",
            lambda *args, **kwargs: {
                "reachable": True,
                "method": "tcp",
                "neighbor": False,
                "open_ports": [443],
                "services": {},
                "checked_at": "2026-11-12T09:01:00Z",
            },
        )
        woke = client.post(
            f"/api/discovery/devices/{device['id']}/verify",
            json={},
            headers=headers(admin),
        )
        assert woke.status_code == 200
        assert woke.json()["verification"]["status"] == "woke"
        assert woke.json()["device"]["wake_status"] == "woke"
        events = client.get(f"/api/discovery/devices/{device['id']}").json()["events"]
        verified = [event for event in events if event["kind"] == "wake_verified"]
        assert {event["detail"]["status"] for event in verified} >= {"sent", "woke"}
        actions = {event["action"] for event in client.get("/api/audit").json()["events"]}
        assert "discovery.wake_verified" in actions


def test_promotion_and_verify_rbac(tmp_path):
    app = create_app(_settings(tmp_path))
    database = app.state.db
    device = _discovered(
        database, mac="aa:bb:cc:dd:ee:70", ip="192.168.1.170", hostname="rbac.lan"
    )
    with TestClient(app) as client:
        admin = login(client)
        created = client.post(
            "/api/users",
            json={
                "username": "viewer4",
                "display_name": "Viewer",
                "role": "viewer",
                "password": ADMIN_PASSWORD,
            },
            headers=headers(admin),
        )
        assert created.status_code == 200
        with TestClient(app) as viewer_client:
            viewer = login(viewer_client, username="viewer4")
            for path, payload in (
                (f"/api/discovery/devices/{device['id']}/wake-target", {"name": "x"}),
                (f"/api/discovery/devices/{device['id']}/promote", {"target": "linux_agent"}),
                (f"/api/discovery/devices/{device['id']}/verify", {}),
            ):
                response = viewer_client.post(path, json=payload, headers=headers(viewer))
                assert response.status_code == 403
