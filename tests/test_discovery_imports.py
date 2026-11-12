from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from sextant_control.config import DiscoverySettings, Settings
from sextant_control.discovery import DeviceObservation, merge_observations
from sextant_control.discovery_imports import (
    DiscoveryImportError,
    MAX_IMPORT_DEVICES,
    parse_import,
)
from sextant_control.server import create_app
from tests.helpers import ADMIN_PASSWORD, headers, login

STATIC_DIR = Path(__file__).resolve().parents[1] / "sextant_control" / "static"

NMAP_XML = """<?xml version="1.0"?>
<nmaprun>
  <host>
    <status state="up"/>
    <address addr="192.168.1.10" addrtype="ipv4"/>
    <address addr="B8:27:EB:11:22:33" addrtype="mac" vendor="Raspberry Pi"/>
    <hostnames><hostname name="printer.lan"/></hostnames>
    <ports>
      <port protocol="tcp" portid="22">
        <state state="open"/>
        <service name="ssh" product="OpenSSH" version="9.6"/>
      </port>
      <port protocol="tcp" portid="80">
        <state state="open"/>
        <service name="http" product="nginx" version="1.24"/>
      </port>
      <port protocol="tcp" portid="443"><state state="closed"/></port>
    </ports>
    <os><osmatch name="Linux 5.0"/></os>
  </host>
</nmaprun>
"""

NMAP_JSON = json.dumps(
    [
        {
            "host": {
                "status": {"state": "up"},
                "address": [
                    {"addr": "192.168.1.20", "addrtype": "ipv4"},
                    {"addr": "AA:BB:CC:DD:EE:20", "addrtype": "mac", "vendor": "VMware"},
                ],
                "hostnames": {"hostname": [{"name": "esxi.lan"}]},
                "ports": {
                    "port": [
                        {
                            "protocol": "tcp",
                            "portid": 8006,
                            "state": {"state": "open"},
                            "service": {"name": "https", "product": "VMware"},
                        }
                    ]
                },
                "os": {"osmatch": [{"name": "VMware ESXi"}]},
            }
        }
    ]
)

ARP_SCAN = """Interface: eth0, type: EN10MB, MAC: aa:bb:cc:dd:ee:ff, IPv4: 192.168.1.2
Starting arp-scan 1.10.0 with 256 hosts
192.168.1.10\tb8:27:eb:11:22:33\tRaspberry Pi Foundation
192.168.1.11\taa:bb:cc:dd:ee:11\tVMware, Inc.
192.168.1.10\tb8:27:eb:11:22:33\tRaspberry Pi Foundation
"""

DNSMASQ = """1700000000 b8:27:eb:11:22:33 192.168.1.10 printer.lan *
1700000001 aa:bb:cc:dd:ee:11 192.168.1.11 * 01:aa:bb:cc:dd:ee:11
"""

ISC = """lease 192.168.1.30 {
  starts 1 2026/11/10 10:00:00;
  ends 1 2026/11/10 12:00:00;
  binding state active;
  hardware ethernet b8:27:eb:aa:bb:cc;
  client-hostname "nas.lan";
}
lease 192.168.1.31 {
  binding state free;
  hardware ethernet aa:bb:cc:dd:ee:31;
}
"""


def test_nmap_xml_json_and_arp_scan_parsing():
    xml = parse_import("nmap-xml", NMAP_XML)
    assert xml.format == "nmap-xml"
    assert len(xml.devices) == 1
    device = xml.devices[0]
    assert device["ip"] == "192.168.1.10"
    assert device["mac"] == "b8:27:eb:11:22:33"
    assert device["hostname"] == "printer.lan"
    assert device["vendor"] == "Raspberry Pi"
    assert device["open_ports"] == [22, 80]
    assert device["services"]["22"]["product"] == "OpenSSH"
    assert device["evidence"]["import_format"] == "nmap-xml"

    nmap_json = parse_import("nmap-json", NMAP_JSON)
    assert nmap_json.devices[0]["ip"] == "192.168.1.20"
    assert nmap_json.devices[0]["open_ports"] == [8006]
    assert nmap_json.devices[0]["hostname"] == "esxi.lan"

    arp = parse_import("arp-scan", ARP_SCAN)
    assert len(arp.devices) == 2
    assert arp.devices[0]["vendor"] == "Raspberry Pi Foundation"
    assert arp.devices[1]["mac"] == "aa:bb:cc:dd:ee:11"


def test_lease_and_generic_json_parsing():
    dnsmasq = parse_import("dnsmasq", DNSMASQ)
    assert len(dnsmasq.devices) == 2
    assert dnsmasq.devices[0]["hostname"] == "printer.lan"
    assert dnsmasq.devices[1]["hostname"] == ""

    isc = parse_import("isc-dhcpd", ISC)
    assert len(isc.devices) == 1
    assert isc.devices[0]["ip"] == "192.168.1.30"
    assert isc.devices[0]["hostname"] == "nas.lan"

    generic = parse_import(
        "json",
        json.dumps(
            {
                "devices": [
                    {
                        "ip": "192.168.1.40",
                        "mac": "b8:27:eb:11:22:40",
                        "hostname": "camera.lan",
                        "vendor": "Acme",
                        "open_ports": [80, 443],
                        "services": {"80": {"protocol": "http"}},
                    }
                ]
            }
        ),
    )
    assert generic.devices[0]["hostname"] == "camera.lan"
    assert generic.devices[0]["open_ports"] == [80, 443]


def test_import_rejects_malformed_and_unsafe_content():
    for fmt, content in (
        ("nmap-xml", "<nmaprun>"),
        ("nmap-json", "{"),
        ("json", "[] trailing"),
        ("arp-scan", "\x00"),
    ):
        if fmt == "arp-scan":
            parsed = parse_import(fmt, content)
            assert parsed.devices == []
            continue
        try:
            parse_import(fmt, content)
        except DiscoveryImportError:
            pass
        else:
            raise AssertionError(f"{fmt} malformed input was accepted")

    try:
        parse_import("nmap-xml", '<!DOCTYPE x [<!ENTITY y "z">]><nmaprun/>')
    except DiscoveryImportError as exc:
        assert "DTD" in str(exc)
    else:
        raise AssertionError("DTD/ENTITY input was accepted")

    try:
        parse_import("json", "x" * 1_100_000)
    except DiscoveryImportError as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("oversized import was accepted")

    parsed = parse_import("json", json.dumps([{"ip": "8.8.8.8"}]), max_devices=2)
    assert parsed.devices == []
    assert parsed.skipped == 1


def test_import_device_cap_and_internal_dedupe():
    payload = json.dumps(
        [
            {"ip": f"192.168.1.{index}", "mac": f"aa:bb:cc:dd:ee:{index:02x}"}
            for index in range(2, 10)
        ]
    )
    parsed = parse_import("json", payload, max_devices=4)
    assert len(parsed.devices) == 4
    assert parsed.truncated is True
    assert MAX_IMPORT_DEVICES >= 4


def test_repeated_import_does_not_flood_events(tmp_path):
    from sextant_control.db import Database

    database = Database(tmp_path / "imports.sqlite3")
    database.initialize()
    parsed = parse_import("arp-scan", ARP_SCAN)
    observations = [
        DeviceObservation(
            ip=item["ip"],
            mac=item["mac"],
            hostname=item["hostname"],
            vendor=item["vendor"],
            source="import:arp-scan",
            segment="192.168.1.0/24",
            open_ports=item["open_ports"],
            services=item["services"],
            evidence=item["evidence"],
        )
        for item in parsed.devices
    ]
    merge_observations(database, observations, now="2026-11-12T10:00:00Z")
    merge_observations(database, observations, now="2026-11-12T10:05:00Z")
    rows, total = database.list_discovered_devices()
    assert total == 2
    for row in rows:
        events = database.discovery_device_events(row["id"])
        assert len([event for event in events if event["kind"] == "new_device"]) == 1


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        static_dir=STATIC_DIR,
        secret_key=b"test-secret-key-that-is-long-enough",
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        discovery=DiscoverySettings(
            enabled=True,
            cidrs=("192.168.1.0/24",),
            methods=("neigh", "dns", "oui", "ssdp"),
            max_hosts=256,
        ),
    )


def _ack(session) -> None:
    response = session["client"].patch(
        "/api/discovery/settings",
        json={
            "acknowledged": True,
            "acknowledgement": "I am authorized to scan these networks",
        },
        headers=headers(session),
    )
    assert response.status_code == 200, response.text


def test_import_api_preview_commit_rbac_audit_and_history(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        preview = client.post(
            "/api/discovery/import",
            json={"format": "nmap-xml", "content": NMAP_XML, "dry_run": True},
            headers=headers(admin),
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["devices"] == 1
        assert preview.json()["new"] == 1
        assert preview.json()["existing"] == 0

        imported = client.post(
            "/api/discovery/import",
            json={"format": "nmap-xml", "content": NMAP_XML, "filename": "scan.xml"},
            headers=headers(admin),
        )
        assert imported.status_code == 200, imported.text
        payload = imported.json()
        assert payload["scan"]["source"] == "import:nmap-xml"
        assert payload["devices_merged"] == 1
        listing = client.get("/api/discovery/devices?q=printer").json()
        assert listing["total"] == 1
        assert listing["devices"][0]["source"] == "import:nmap-xml"
        assert listing["devices"][0]["segment"] == "192.168.1.0/24"

        second = client.post(
            "/api/discovery/import",
            json={"format": "nmap-xml", "content": NMAP_XML},
            headers=headers(admin),
        )
        assert second.status_code == 200
        events = client.get(
            f"/api/discovery/devices/{listing['devices'][0]['id']}"
        ).json()["events"]
        assert len([event for event in events if event["kind"] == "new_device"]) == 1
        history = client.get("/api/discovery/scans").json()
        assert any(scan["source"] == "import:nmap-xml" for scan in history["scans"])
        audit = client.get("/api/audit").json()["events"]
        assert any(event["action"] == "discovery.imported" for event in audit)

        created = client.post(
            "/api/users",
            json={
                "username": "viewer_import",
                "display_name": "Viewer",
                "role": "viewer",
                "password": ADMIN_PASSWORD,
            },
            headers=headers(admin),
        )
        assert created.status_code == 200
        with TestClient(app) as viewer_client:
            viewer = login(viewer_client, username="viewer_import")
            denied = viewer_client.post(
                "/api/discovery/import",
                json={"format": "arp-scan", "content": ARP_SCAN},
                headers=headers(viewer),
            )
            assert denied.status_code == 403

        no_csrf = client.post(
            "/api/discovery/import",
            json={"format": "arp-scan", "content": ARP_SCAN},
        )
        assert no_csrf.status_code == 403
