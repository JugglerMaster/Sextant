from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from sextant_control.config import DiscoverySettings, Settings
from sextant_control.db import Database, utcnow
from sextant_control.discovery import ACKNOWLEDGEMENT, DeviceObservation, merge_observation
from sextant_control.server import create_app
from tests.helpers import ADMIN_PASSWORD, headers, login

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
