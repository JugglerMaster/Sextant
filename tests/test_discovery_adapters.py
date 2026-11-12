from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sextant_control.config import DiscoverySettings, Settings
from sextant_control.discovery_integrations import (
    AdapterResult,
    HttpFetcher,
    IntegrationError,
    IntegrationSecretStore,
    adapter_descriptor,
    fetch_adapter_devices,
    parse_esxi_hosts,
    parse_opnsense_arp,
    parse_proxmox_nodes,
    parse_routeros_arp,
    parse_unifi,
    validate_integration_config,
)
from sextant_control.server import create_app
from tests.helpers import ADMIN_PASSWORD, headers, login

STATIC_DIR = Path(__file__).resolve().parents[1] / "sextant_control" / "static"

UNIFI = {
    "data": [
        {
            "mac": "aa:bb:cc:dd:ee:01",
            "ip": "192.168.1.10",
            "name": "office-ap",
            "oui": "Ubiquiti",
            "type": "uap",
            "port_table": [{"port_idx": 1, "up": True}],
        }
    ]
}
ROUTEROS = [
    {
        "address": "192.168.1.11",
        "mac-address": "aa:bb:cc:dd:ee:02",
        "host-name": "router-client",
        "interface": "bridge",
        "status": "reachable",
    }
]
OPNSENSE = {
    "rows": [
        {
            "ip": "192.168.1.12",
            "mac": "aa:bb:cc:dd:ee:03",
            "hostname": "opn-client",
            "interface": "lan",
        }
    ]
}
PROXMOX = {
    "data": [
        {
            "name": "pve1",
            "ip": "192.168.1.13",
            "mac": "aa:bb:cc:dd:ee:04",
            "status": "online",
            "maxcpu": 8,
        }
    ]
}
ESXI = {
    "hosts": [
        {
            "name": "esxi1",
            "ip": "192.168.1.14",
            "mac": "aa:bb:cc:dd:ee:05",
            "version": "8.0",
        }
    ]
}


def test_fixture_parsers_normalize_adapter_devices():
    cases = [
        ("unifi", parse_unifi(UNIFI), "192.168.1.10", "office-ap"),
        ("routeros", parse_routeros_arp(ROUTEROS), "192.168.1.11", "router-client"),
        ("opnsense", parse_opnsense_arp(OPNSENSE), "192.168.1.12", "opn-client"),
        ("proxmox", parse_proxmox_nodes(PROXMOX), "192.168.1.13", "pve1"),
        ("esxi", parse_esxi_hosts(ESXI), "192.168.1.14", "esxi1"),
    ]
    for kind, devices, ip, hostname in cases:
        assert devices, kind
        device = devices[0]
        assert device["ip"] == ip
        assert device["hostname"] == hostname
        assert device["evidence"]["adapter"] == kind
        assert device["evidence"]["import_format"] == f"adapter:{kind}"
        assert device["evidence"]["adapter_verified"] is False


def test_adapter_malformed_responses_and_fixture_only_esxi():
    with pytest.raises(IntegrationError):
        parse_unifi({"data": "wrong"})
    with pytest.raises(IntegrationError):
        parse_routeros_arp({"not": "a list"})
    with pytest.raises(IntegrationError):
        parse_opnsense_arp({"rows": "wrong"})
    with pytest.raises(IntegrationError):
        parse_proxmox_nodes({"data": "wrong"})
    with pytest.raises(IntegrationError):
        parse_esxi_hosts({"hosts": "wrong"})

    descriptor = adapter_descriptor("esxi")
    assert descriptor["live_supported"] is False
    assert descriptor["verified"] is False
    with pytest.raises(IntegrationError):
        fetch_adapter_devices(
            "esxi",
            validate_integration_config(
                "esxi", {"base_url": "https://esxi.local", "username": "root"}
            ),
            {"password": "secret"},
        )


def test_adapter_config_validation_and_fetcher_bounds():
    config = validate_integration_config(
        "unifi",
        {
            "base_url": "https://unifi.local/",
            "site": "default",
            "timeout_ms": 99999,
            "max_response_bytes": 999999999,
        },
    )
    assert config["base_url"] == "https://unifi.local"
    assert config["timeout_ms"] == 30000
    assert config["max_response_bytes"] == 4_000_000
    assert config["verify_tls"] is True
    with pytest.raises(IntegrationError):
        validate_integration_config("unifi", {"base_url": "http://unifi.example"})
    with pytest.raises(IntegrationError):
        validate_integration_config(
            "unifi", {"base_url": "https://user:pass@unifi.local"}
        )
    fetcher = HttpFetcher({"timeout_ms": 1234, "max_response_bytes": 2048}, {})
    assert fetcher.timeout == 1.234
    assert fetcher.max_bytes == 2048


def test_fetch_adapter_uses_headers_without_leaking_secrets():
    seen: dict[str, str] = {}

    def fetcher(url, request_headers):
        seen["url"] = url
        seen.update(request_headers)
        return UNIFI

    result = fetch_adapter_devices(
        "unifi",
        validate_integration_config(
            "unifi", {"base_url": "https://unifi.local", "site": "default"}
        ),
        {"api_token": "secret-api-token"},
        fetcher=fetcher,
    )
    assert result.devices[0]["ip"] == "192.168.1.10"
    assert seen["X-API-KEY"] == "secret-api-token"
    assert "secret-api-token" not in json.dumps(result.devices)
    assert result.verified is False


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


def test_adapter_settings_redact_and_poll(tmp_path, monkeypatch):
    app = create_app(_settings(tmp_path))
    token = "secret-api-token"
    with TestClient(app) as client:
        admin = login(client)
        _ack(admin)
        updated = client.patch(
            "/api/discovery/integrations/unifi",
            json={
                "enabled": True,
                "config": {
                    "base_url": "https://unifi.local",
                    "site": "default",
                    "timeout_ms": 1000,
                },
                "secrets": {"api_token": token},
            },
            headers=headers(admin),
        )
        assert updated.status_code == 200, updated.text
        assert token not in updated.text
        listed = client.get("/api/discovery/integrations")
        assert token not in listed.text
        unifi = next(
            item for item in listed.json()["integrations"] if item["kind"] == "unifi"
        )
        assert unifi["secret_configured"] is True
        assert unifi["verified"] is False

        def fake_fetch(kind, config, secrets, fetcher=None):
            assert kind == "unifi"
            return AdapterResult(
                kind="unifi",
                devices=parse_unifi(UNIFI),
                live_supported=True,
                verified=False,
                detail="fixture",
            )

        monkeypatch.setattr("sextant_control.server.fetch_adapter_devices", fake_fetch)
        polled = client.post(
            "/api/discovery/integrations/unifi/poll", json={}, headers=headers(admin)
        )
        assert polled.status_code == 200, polled.text
        assert polled.json()["devices_merged"] == 1
        devices = client.get("/api/discovery/devices?q=office-ap").json()
        assert devices["total"] == 1
        assert devices["devices"][0]["source"] == "adapter:unifi"
        assert devices["devices"][0]["device_type"] == "router/switch"

        audit_text = json.dumps(client.get("/api/audit").json())
        assert token not in audit_text
        assert token not in client.get("/api/discovery/integrations").text

        def failing(kind, config, secrets, fetcher=None):
            raise IntegrationError("credential rejected " + secrets["api_token"])

        monkeypatch.setattr("sextant_control.server.test_adapter", failing)
        failed = client.post(
            "/api/discovery/integrations/unifi/test", json={}, headers=headers(admin)
        )
        assert failed.status_code == 502
        assert token not in failed.text
        listed = client.get("/api/discovery/integrations").json()
        entry = next(item for item in listed["integrations"] if item["kind"] == "unifi")
        assert token not in entry["last_error"]
        assert "***" in entry["last_error"]
        assert isinstance(app.state.integration_secrets, IntegrationSecretStore)
