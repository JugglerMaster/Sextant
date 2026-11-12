from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sextant_control.config import DiscoverySettings, Settings
from sextant_control.discovery_integrations import (
    IntegrationSecretStore,
    validate_integration_config,
)
from sextant_control.server import create_app
from sextant_control.snmp import (
    OID_SYS_DESCR,
    OID_SYS_NAME,
    OID_SYS_OBJECT_ID,
    OID_SYS_UPTIME,
    SNMPError,
    _ber_int,
    _ber_oid,
    _tlv,
    decode_response,
    encode_get,
    snmp_device,
    snmp_get,
    snmp_status,
    target_ips,
)
from tests.helpers import ADMIN_PASSWORD, headers, login

STATIC_DIR = Path(__file__).resolve().parents[1] / "sextant_control" / "static"


def _octet(value: str) -> bytes:
    return _tlv(0x04, value.encode("utf-8"))


def _oid_value(oid: str) -> bytes:
    return _tlv(0x06, _ber_oid(oid))


def _timeticks(value: int) -> bytes:
    length = max(1, (value.bit_length() + 7) // 8)
    return _tlv(0x43, value.to_bytes(length, "big"))


def _response(varbinds: list[bytes], community: str = "public") -> bytes:
    pdu = _tlv(
        0xA2,
        _tlv(0x02, _ber_int(7))
        + _tlv(0x02, b"\x00")
        + _tlv(0x02, b"\x00")
        + _tlv(0x30, b"".join(varbinds)),
    )
    return _tlv(
        0x30,
        _tlv(0x02, _ber_int(1)) + _tlv(0x04, community.encode()) + pdu,
    )


def test_snmp_status_and_target_caps():
    status = snmp_status()
    assert status["available"] is True
    assert status["backend"] == "stdlib"
    assert status["optional_dependency"] is False

    targets = target_ips(["192.168.1.0/30", "10.20.0.5"], max_targets=3)
    assert targets == ["192.168.1.1", "192.168.1.2", "10.20.0.5"]
    with pytest.raises(SNMPError):
        target_ips(["8.8.8.0/24"])


def test_snmp_config_validation_and_limits():
    config = validate_integration_config(
        "snmp",
        {
            "targets": ["192.168.1.0/24", "10.0.0.0/24"],
            "timeout_ms": 99999,
            "retries": 99,
            "max_targets": 99999,
        },
    )
    assert config["targets"] == ["192.168.1.0/24", "10.0.0.0/24"]
    assert config["timeout_ms"] == 5000
    assert config["retries"] == 3
    assert config["max_targets"] == 1024
    with pytest.raises(ValueError):
        validate_integration_config("snmp", {"targets": ["8.8.8.8/32"]})
    with pytest.raises(ValueError):
        validate_integration_config("snmp", {"targets": ["192.168.1.0/24"] * 257})


def test_ber_encode_decode_and_oid_mapping():
    request = encode_get("public", request_id=7)
    assert request.startswith(b"\x30")
    assert b"public" in request

    response = _response(
        [
            _tlv(0x30, _oid_value(OID_SYS_DESCR) + _octet("Synology DiskStation DS920+")),
            _tlv(0x30, _oid_value(OID_SYS_NAME) + _octet("diskstation")),
            _tlv(0x30, _oid_value(OID_SYS_OBJECT_ID) + _oid_value("1.3.6.1.4.1.6574.1.1")),
            _tlv(0x30, _oid_value(OID_SYS_UPTIME) + _timeticks(123456)),
        ]
    )
    decoded = decode_response(response)
    assert decoded["values"][OID_SYS_NAME] == "diskstation"
    assert decoded["values"][OID_SYS_UPTIME] == 123456
    device = snmp_device("192.168.1.60", decoded["values"])
    assert device["vendor"] == "Synology"
    assert device["hostname"] == "diskstation"
    assert device["evidence"]["imported_device_type"] == "NAS"
    assert device["evidence"]["snmp"]["uptime_ticks"] == 123456

    cisco = snmp_device(
        "192.168.1.61",
        {
            OID_SYS_DESCR: "Cisco IOS Software, C2960",
            OID_SYS_NAME: "switch-1",
            OID_SYS_OBJECT_ID: "1.3.6.1.4.1.9.1.1208",
        },
    )
    assert cisco["vendor"] == "Cisco"
    assert cisco["evidence"]["imported_device_type"] == "router/switch"
    windows = snmp_device(
        "192.168.1.62",
        {
            OID_SYS_DESCR: "Hardware: Intel - Software: Windows Version 10.0",
            OID_SYS_NAME: "win-box",
            OID_SYS_OBJECT_ID: "1.3.6.1.4.1.311.1.1.3.1.1",
        },
    )
    assert windows["evidence"]["imported_device_type"] == "Windows"


def test_snmp_get_uses_bounded_udp_and_redacts_errors():
    class FakeSocket:
        def __init__(self):
            self.sent = None

        def settimeout(self, value):
            return None

        def sendto(self, data, address):
            self.sent = (data, address)

        def recvfrom(self, size):
            return (
                _response(
                    [
                        _tlv(0x30, _oid_value(OID_SYS_NAME) + _octet("lab-switch")),
                    ],
                    community="secret-community",
                ),
                ("192.168.1.60", 161),
            )

        def close(self):
            return None

    fake = FakeSocket()
    result = snmp_get(
        "192.168.1.60",
        "secret-community",
        timeout_ms=100,
        retries=0,
        sock_factory=lambda *args: fake,
    )
    assert result["values"][OID_SYS_NAME] == "lab-switch"
    assert fake.sent is not None
    assert b"secret-community" in fake.sent[0]

    class TimeoutSocket(FakeSocket):
        def recvfrom(self, size):
            raise socket.timeout()

    with pytest.raises(SNMPError) as exc_info:
        snmp_get(
            "192.168.1.60",
            "secret-community",
            timeout_ms=100,
            retries=0,
            sock_factory=lambda *args: TimeoutSocket(),
        )
    assert "secret-community" not in str(exc_info.value)


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


def test_snmp_integration_settings_redact_secret_and_poll(tmp_path, monkeypatch):
    app = create_app(_settings(tmp_path))
    community = "super-secret-community"
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        updated = client.patch(
            "/api/discovery/integrations/snmp",
            json={
                "enabled": True,
                "config": {
                    "targets": ["192.168.1.0/30"],
                    "use_discovered": False,
                    "timeout_ms": 200,
                    "retries": 0,
                    "max_targets": 4,
                },
                "secrets": {"community": community},
            },
            headers=headers(admin),
        )
        assert updated.status_code == 200, updated.text
        descriptor = updated.json()
        assert descriptor["enabled"] is True
        assert descriptor["secret_configured"] is True
        assert community not in json.dumps(descriptor)

        listed = client.get("/api/discovery/integrations")
        assert community not in listed.text
        assert listed.status_code == 200

        monkeypatch.setattr(
            "sextant_control.server.snmp_get",
            lambda ip, secret, **kwargs: {
                "values": {
                    OID_SYS_DESCR: "Cisco IOS Software",
                    OID_SYS_NAME: "edge-switch",
                    OID_SYS_OBJECT_ID: "1.3.6.1.4.1.9.1.1",
                }
            },
        )
        tested = client.post(
            "/api/discovery/integrations/snmp/test", json={}, headers=headers(admin)
        )
        assert tested.status_code == 200, tested.text
        assert "edge-switch" in tested.json()["detail"] or "SNMP GET" in tested.json()["detail"]

        polled = client.post(
            "/api/discovery/integrations/snmp/poll", json={}, headers=headers(admin)
        )
        assert polled.status_code == 200, polled.text
        assert polled.json()["devices_merged"] >= 1
        devices = client.get("/api/discovery/devices?q=edge-switch").json()
        assert devices["total"] == 1
        assert devices["devices"][0]["source"] == "snmp"
        assert devices["devices"][0]["vendor"] == "Cisco"
        assert devices["devices"][0]["device_type"] == "router/switch"

        audit_text = json.dumps(client.get("/api/audit").json())
        assert community not in audit_text
        assert community not in client.get("/api/discovery/integrations").text

        def failing(ip, secret, **kwargs):
            raise SNMPError("request failed for " + secret)

        monkeypatch.setattr("sextant_control.server.snmp_get", failing)
        failed = client.post(
            "/api/discovery/integrations/snmp/test", json={}, headers=headers(admin)
        )
        assert failed.status_code == 502
        assert community not in failed.text
        integration = client.get("/api/discovery/integrations").json()
        snmp_entry = next(item for item in integration["integrations"] if item["kind"] == "snmp")
        assert community not in snmp_entry["last_error"]

        secret_store = app.state.integration_secrets
        assert isinstance(secret_store, IntegrationSecretStore)
        assert secret_store.configured("snmp", ("community",)) is True
