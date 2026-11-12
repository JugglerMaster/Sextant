from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sextant_agent import discovery as agent_discovery
from sextant_agent.agent import AgentRunner
from sextant_agent.cli import build_parser
from sextant_agent.config import AgentConfig, load_config, save_config
from sextant_agent.discovery import (
    AgentDiscoveryError,
    discovery_allowed,
    parse_http_banner,
    parse_neighbor_table,
    parse_ssh_banner,
    probe_tcp_host,
    run_agent_scan,
    validate_scope,
)
from sextant_control.models import DiscoveryReportDeviceIn, DiscoveryReportIn


def _config(**overrides) -> AgentConfig:
    values = {
        "server_url": "https://ops.example.test",
        "agent_id": "agt_test",
        "agent_token": "sextant_agent_test",
        "allow_discovery": True,
    }
    values.update(overrides)
    return AgentConfig(**values)


def test_agent_discovery_defaults_and_config_round_trip(tmp_path: Path):
    config = _config(allow_discovery=False)
    assert config.allow_discovery is False
    path = tmp_path / "config.json"
    save_config(config, path)
    raw = json.loads(path.read_text())
    assert raw["allow_discovery"] is False
    config.allow_discovery = True
    save_config(config, path)
    assert load_config(path).allow_discovery is True

    parser = build_parser()
    args = parser.parse_args(
        [
            "enroll",
            "--server",
            "https://ops.example.test",
            "--token",
            "sextant_enroll_x",
            "--allow-discovery",
        ]
    )
    assert args.allow_discovery is True


def test_local_and_remote_discovery_gates_are_intersection():
    config = _config(allow_discovery=False)
    assert discovery_allowed(config, {"allow_discovery": True}) is False
    config.allow_discovery = True
    assert discovery_allowed(config, {"allow_discovery": False}) is False
    assert discovery_allowed(config, {"allow_discovery": True}) is True
    assert discovery_allowed(config, {}) is False


@pytest.mark.parametrize(
    "value",
    [
        "8.8.8.0/24",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "0.0.0.0/8",
        "0.0.0.0/0",
        "192.0.2.0/24",
        "2001:db8::/48",
    ],
)
def test_agent_scope_validation_rejects_unsafe_ranges(value):
    with pytest.raises(AgentDiscoveryError):
        validate_scope([value])


@pytest.mark.parametrize("value", ["192.168.1.0/24", "10.0.0.0/8", "fd00::/64"])
def test_agent_scope_validation_accepts_private_ranges(value):
    assert str(validate_scope([value])[0]) == value


def test_agent_neighbor_parsing_and_scope_filtering():
    class Result:
        stdout = (
            "192.168.1.2 dev eth0 lladdr b8:27:eb:aa:bb:cc REACHABLE\n"
            "192.168.1.99 dev eth0 lladdr 00:50:56:aa:bb:cc REACHABLE\n"
        )

    def runner(command, **kwargs):
        return Result()

    report = run_agent_scan(
        scope=["192.168.1.0/29"],
        methods=["neigh", "oui"],
        ports=[],
        max_hosts=16,
        command_runner=runner,
    )
    assert report["status"] == "succeeded"
    assert [device["ip"] for device in report["devices"]] == ["192.168.1.2"]
    assert report["devices"][0]["vendor"] == "Raspberry Pi Foundation"
    assert report["segment"] == "192.168.1.0/29"


def test_agent_scan_never_includes_out_of_scope_neighbors():
    text = (
        "192.168.1.2 dev eth0 lladdr b8:27:eb:aa:bb:cc REACHABLE\n"
        "192.168.2.2 dev eth0 lladdr b8:27:eb:aa:bb:cd REACHABLE\n"
    )
    entries = parse_neighbor_table(text)
    assert {entry["ip"] for entry in entries} == {"192.168.1.2", "192.168.2.2"}

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    report = run_agent_scan(
        scope=["192.168.1.0/29"],
        methods=["neigh"],
        ports=[],
        max_hosts=16,
        command_runner=lambda command, **kwargs: Result(text),
    )
    assert [device["ip"] for device in report["devices"]] == ["192.168.1.2"]


def test_agent_windows_reports_unsupported():
    report = run_agent_scan(
        scope=["192.168.1.0/29"], methods=["neigh"], ports=[], os_name="win32"
    )
    assert report["status"] == "unsupported"
    assert "Linux only" in report["error"]


def test_agent_banner_parsing_and_tcp_probe():
    ssh = parse_ssh_banner(b"SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13\r\n")
    assert ssh["product"] == "OpenSSH"
    assert ssh["version"] == "9.6p1"
    http = parse_http_banner(
        b"HTTP/1.1 200 OK\r\nServer: nginx/1.24\r\n\r\n<title>Lab printer</title>"
    )
    assert http["status"] == 200
    assert http["title"] == "Lab printer"

    class FakeSocket:
        def settimeout(self, value):
            return None

        def recv(self, size):
            return b"SSH-2.0-OpenSSH_9.6\r\n"

        def sendall(self, value):
            return None

        def close(self):
            return None

    def connector(ip, port, timeout):
        if port == 22:
            return FakeSocket()
        raise OSError("closed")

    services = probe_tcp_host("192.168.1.2", [22, 80], timeout_seconds=0.1, connector=connector)
    assert services["22"]["product"] == "OpenSSH"
    assert "80" not in services


def test_agent_scan_host_cap_and_invalid_methods():
    report = run_agent_scan(
        scope=["10.0.0.0/24"],
        methods=["neigh"],
        ports=[],
        max_hosts=4,
        command_runner=lambda command, **kwargs: type("R", (), {"stdout": ""})(),
    )
    assert report["status"] == "failed"
    assert "host cap" in report["error"]

    report = run_agent_scan(
        scope=["192.168.1.0/29"],
        methods=["snmp"],
        ports=[],
    )
    assert report["status"] == "failed"
    assert "Unsupported discovery method" in report["error"]


def test_agent_runner_enforces_local_discovery_gate(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.reports = []
            self.results = []

        def report_discovery(self, scan_id, report):
            self.reports.append((scan_id, report))

        def send_result(self, command_id, result):
            self.results.append((command_id, result))

    def fake_scan(**kwargs):
        return {
            "status": "succeeded",
            "devices": [{"ip": "192.168.1.2"}],
            "collected_at": "2026-11-12T00:00:00Z",
            "segment": "192.168.1.0/29",
            "truncated": False,
            "error": "",
        }

    monkeypatch.setattr(agent_discovery, "run_agent_scan", fake_scan)
    command = {
        "id": "cmd_discovery",
        "kind": "discovery",
        "args": {
            "action": "discovery.scan",
            "scan_id": "dsc_test",
            "scope": ["192.168.1.0/29"],
            "methods": ["neigh"],
            "ports": [],
        },
    }

    disabled = AgentRunner(_config(allow_discovery=False))
    disabled.client = FakeClient()
    disabled.remote_policy = {"allow_discovery": True}
    disabled._execute_discovery(command)
    assert disabled.client.reports == []
    assert disabled.client.results[0][1]["status"] == "failed"
    assert "disabled" in disabled.client.results[0][1]["error"].lower()

    enabled = AgentRunner(_config(allow_discovery=True))
    enabled.client = FakeClient()
    enabled.remote_policy = {"allow_discovery": True}
    enabled._execute_discovery(command)
    assert enabled.client.reports[0][0] == "dsc_test"
    assert enabled.client.results[0][1]["status"] == "succeeded"


def test_discovery_report_models_are_bounded():
    device = {
        "ip": "192.168.1.2",
        "mac": "b8:27:eb:aa:bb:cc",
        "open_ports": [22, 80],
        "services": {"22": {"protocol": "ssh"}},
    }
    report = DiscoveryReportIn(scan_id="dsc_abc123", devices=[device])
    assert report.devices[0].ip == "192.168.1.2"

    with pytest.raises(ValidationError):
        DiscoveryReportDeviceIn(ip="not-an-ip")
    with pytest.raises(ValidationError):
        DiscoveryReportDeviceIn(
            ip="192.168.1.2",
            services={"x": "a" * 9000},
        )
    with pytest.raises(ValidationError):
        DiscoveryReportIn(
            scan_id="dsc_abc123",
            devices=[device] * 513,
        )
    with pytest.raises(ValidationError):
        DiscoveryReportIn(scan_id="not-a-scan")
