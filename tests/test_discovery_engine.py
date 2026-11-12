from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from sextant_control import discovery
from sextant_control.config import DiscoverySettings, Settings
from sextant_control.db import Database, loads, utcnow, utcnow_iso
from sextant_control.discovery import (
    ACKNOWLEDGEMENT,
    DeviceObservation,
    DiscoveryBusy,
    detect_identity_conflicts,
    verify_reachability,
    DiscoveryDisabled,
    DiscoveryError,
    DiscoveryManager,
    DiscoveryNotAcknowledged,
    classify_device,
    expand_targets,
    listen_ssdp,
    merge_observation,
    parse_http_banner,
    parse_neighbor_table,
    parse_scope,
    parse_ssh_banner,
    parse_ssdp_payload,
    probe_tcp_host,
    read_neighbor_table,
    record_went_dark,
    reverse_dns,
    run_scan,
    summarize_tls_certificate,
    validate_allowlist,
    validate_excludes,
    validate_methods,
    validate_network,
)
from sextant_control.oui import is_locally_administered, lookup_vendor, normalize_mac


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "discovery.sqlite3")
    db.initialize()
    return db


def _acknowledge(db: Database) -> None:
    db.set_discovery_acknowledgement("admin", ACKNOWLEDGEMENT)


def _config(**overrides) -> DiscoverySettings:
    values = {
        "enabled": True,
        "cidrs": ("192.168.1.0/29",),
        "exclude": (),
        "methods": ("neigh", "dns", "oui", "ssdp"),
        "probe_ports": (22, 80),
        "active_probes": False,
        "interval_hours": 0,
        "max_hosts": 16,
        "concurrency": 4,
        "timeout_ms": 500,
        "retention_days": 90,
        "agent_relay": False,
        "export_limit": 1000,
    }
    values.update(overrides)
    return DiscoverySettings(**values)


def _observation(**overrides) -> DeviceObservation:
    values = {
        "ip": "192.168.1.10",
        "mac": "b8:27:eb:11:22:33",
        "hostname": "pi.lan",
        "vendor": "",
        "source": "server",
        "segment": "192.168.1.0/24",
        "open_ports": [22],
        "services": {"22": {"protocol": "ssh", "banner": "SSH-2.0-OpenSSH_9.6"}},
    }
    values.update(overrides)
    return DeviceObservation(**values)


def test_discovery_settings_defaults_and_env_parsing():
    defaults = DiscoverySettings.from_env({})
    assert defaults.enabled is False
    assert defaults.active_probes is False
    assert defaults.agent_relay is False
    assert defaults.cidrs == ()
    assert defaults.methods == ("neigh", "dns", "oui", "ssdp")
    assert 22 in defaults.probe_ports and 32400 in defaults.probe_ports

    parsed = DiscoverySettings.from_env(
        {
            "SEXTANT_DISCOVERY_ENABLED": "1",
            "SEXTANT_DISCOVERY_CIDRS": "192.168.1.0/24, 10.20.0.0/16",
            "SEXTANT_DISCOVERY_EXCLUDE": "192.168.1.1/32",
            "SEXTANT_DISCOVERY_METHODS": "neigh,dns,tcp,bogus",
            "SEXTANT_DISCOVERY_PROBE_PORTS": "22,443,99999",
            "SEXTANT_DISCOVERY_ACTIVE_PROBES": "1",
            "SEXTANT_DISCOVERY_INTERVAL_HOURS": "6",
            "SEXTANT_DISCOVERY_MAX_HOSTS": "512",
            "SEXTANT_DISCOVERY_CONCURRENCY": "999",
            "SEXTANT_DISCOVERY_TIMEOUT_MS": "250",
            "SEXTANT_DISCOVERY_RETENTION_DAYS": "30",
            "SEXTANT_DISCOVERY_AGENT_RELAY": "1",
            "SEXTANT_DISCOVERY_EXPORT_LIMIT": "500",
        }
    )
    assert parsed.enabled is True
    assert parsed.cidrs == ("192.168.1.0/24", "10.20.0.0/16")
    assert parsed.exclude == ("192.168.1.1/32",)
    assert parsed.methods == ("neigh", "dns", "tcp")
    assert parsed.probe_ports == (22, 443)
    assert parsed.active_probes is True
    assert parsed.interval_hours == 6
    assert parsed.max_hosts == 512
    assert parsed.concurrency == 128
    assert parsed.timeout_ms == 250
    assert parsed.retention_days == 30
    assert parsed.agent_relay is True
    assert parsed.export_limit == 500


def test_settings_dataclass_carries_discovery():
    settings = Settings.from_env({"SEXTANT_DATA_DIR": "/tmp/opencode/sextant-config-test"})
    assert settings.discovery.enabled is False
    assert settings.discovery.max_hosts == 1024


@pytest.mark.parametrize(
    "value",
    [
        "8.8.8.0/24",
        "1.1.1.1/32",
        "127.0.0.0/8",
        "127.0.0.1/32",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "0.0.0.0/8",
        "0.0.0.0/0",
        "fec0::/48",
        "2001:db8::/48",
    ],
)
def test_network_validation_rejects_unsafe_ranges(value):
    with pytest.raises(DiscoveryError):
        validate_network(value)


@pytest.mark.parametrize(
    "value",
    ["192.168.1.0/24", "10.0.0.0/8", "172.16.0.0/12", "fd00::/64", "fc00::/48"],
)
def test_network_validation_accepts_private_ranges(value):
    assert str(validate_network(value)) == value


def test_scope_must_stay_inside_allowlist():
    allowlist = validate_allowlist(["192.168.1.0/24", "10.20.0.0/24"])
    assert [str(item) for item in parse_scope(None, allowlist)] == [
        "192.168.1.0/24",
        "10.20.0.0/24",
    ]
    assert [str(item) for item in parse_scope(["192.168.1.0/25"], allowlist)] == [
        "192.168.1.0/25"
    ]
    with pytest.raises(DiscoveryError):
        parse_scope(["192.168.2.0/24"], allowlist)
    with pytest.raises(DiscoveryError):
        parse_scope(["10.20.0.5/32"], allowlist)
    with pytest.raises(DiscoveryError):
        parse_scope(["8.8.8.0/24"], allowlist)


def test_excludes_and_host_cap_are_enforced():
    scope = [validate_network("192.168.1.0/29")]
    excludes = validate_excludes(["192.168.1.1/32", "192.168.1.6/31"])
    targets = expand_targets(scope, excludes, 16)
    assert "192.168.1.1" not in targets
    assert "192.168.1.6" not in targets and "192.168.1.7" not in targets
    assert len(targets) == 4
    with pytest.raises(DiscoveryError):
        expand_targets([validate_network("10.0.0.0/24")], [], 16)


def test_method_validation_requires_configured_methods_and_active_probes():
    config = _config(methods=("neigh", "dns", "oui", "ssdp"))
    assert validate_methods(config, None) == ["neigh", "dns", "oui", "ssdp"]
    with pytest.raises(DiscoveryError):
        validate_methods(config, ["tcp"])
    with pytest.raises(DiscoveryError):
        validate_methods(config, ["snmp"])
    active = _config(methods=("neigh", "tcp"), active_probes=True)
    assert validate_methods(active, ["tcp"]) == ["tcp"]


def test_manager_rate_limit_and_single_active_scan(database, monkeypatch):
    config = _config()
    manager = DiscoveryManager(database, config)
    manager.submit("dsc_one")
    with pytest.raises(DiscoveryBusy):
        manager.submit("dsc_two")
    manager._active_id = None  # simulate worker completion without starting it
    monkeypatch.setattr(discovery, "SCAN_RATE_LIMIT_SECONDS", 0.0)
    manager._last_start = 0.0
    manager.submit("dsc_three")


def test_parse_ip_neigh_and_arp_tables():
    text = """
192.168.1.1 dev eth0 lladdr AA:BB:CC:DD:EE:FF REACHABLE
192.168.1.2 dev eth0  FAILED
192.168.1.3 dev eth0 lladdr 11-22-33-44-55-66 STALE
"""
    entries = parse_neighbor_table(text)
    assert entries == [
        {"ip": "192.168.1.1", "mac": "aa:bb:cc:dd:ee:ff", "hostname": "", "state": "REACHABLE"},
        {"ip": "192.168.1.3", "mac": "11:22:33:44:55:66", "hostname": "", "state": "STALE"},
    ]

    arp = """
gateway (192.168.1.1) at 00:11:22:33:44:ff [ether] on eth0
? (192.168.1.20) at 00:11:22:33:44:55 on en0 ifscope [ethernet]
  192.168.1.30           b8-27-eb-aa-bb-cc     dynamic
"""
    parsed = parse_neighbor_table(arp)
    assert {item["ip"] for item in parsed} == {"192.168.1.1", "192.168.1.20", "192.168.1.30"}
    assert parsed[0]["hostname"] == "gateway"


def test_read_neighbor_table_falls_back_to_arp(monkeypatch):
    calls: list[list[str]] = []

    class Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def runner(command, **kwargs):
        calls.append(command)
        if command[0] == "ip":
            return Result("")
        return Result("gateway (192.168.1.1) at 00:11:22:33:44:ff [ether] on eth0\n")

    entries = read_neighbor_table(command_runner=runner)
    assert calls[0][0] == "ip" and calls[1][0] == "arp"
    assert entries[0]["mac"] == "00:11:22:33:44:ff"


def test_oui_lookup_and_randomized_mac_detection():
    assert lookup_vendor("B8:27:EB:11:22:33") == "Raspberry Pi Foundation"
    assert lookup_vendor("00:50:56:aa:bb:cc") == "VMware"
    assert lookup_vendor("02:11:22:33:44:55") == ""
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert is_locally_administered("02:11:22:33:44:55") is True
    assert is_locally_administered("b8:27:eb:11:22:33") is False


def test_reverse_dns_handles_success_and_failure():
    assert reverse_dns("192.168.1.10", resolver=lambda ip: ("printer.lan.", [], [])) == "printer.lan"

    def failing(ip):
        raise OSError("no PTR")

    assert reverse_dns("192.168.1.10", resolver=failing) == ""
    assert reverse_dns("not-an-ip") == ""


def test_ssh_http_and_tls_banner_parsing():
    ssh = parse_ssh_banner(b"SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13\r\n")
    assert ssh == {
        "protocol": "ssh",
        "banner": "SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13",
        "ssh_protocol": "2.0",
        "product": "OpenSSH",
        "version": "9.6p1",
    }
    assert parse_ssh_banner("not ssh") is None

    http = parse_http_banner(
        b"HTTP/1.1 200 OK\r\nServer: nginx/1.24\r\n\r\n<html><title>Printer &amp; NAS</title>"
    )
    assert http == {"protocol": "http", "status": 200, "server": "nginx/1.24", "title": "Printer &amp; NAS"}

    cert = {
        "subject": ((("commonName", "nas.lan"),),),
        "issuer": ((("commonName", "Lab CA"),),),
        "subjectAltName": [("DNS", "nas.lan"), ("IP Address", "192.168.1.20")],
        "notAfter": "Dec  1 00:00:00 2027 GMT",
    }
    assert summarize_tls_certificate(cert) == {
        "protocol": "tls",
        "subject": "nas.lan",
        "issuer": "Lab CA",
        "san": ["192.168.1.20", "nas.lan"],
        "not_after": "Dec  1 00:00:00 2027 GMT",
    }


def test_probe_tcp_host_parses_open_ssh():
    class FakeSocket:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.closed = False

        def settimeout(self, value):
            return None

        def recv(self, size):
            return self.payload

        def sendall(self, value):
            return None

        def close(self):
            self.closed = True

    def connector(ip, port, timeout):
        if port == 22:
            return FakeSocket(b"SSH-2.0-OpenSSH_9.6\r\n")
        raise OSError("closed")

    services = probe_tcp_host("192.168.1.10", [22, 80], timeout_seconds=0.1, connector=connector)
    assert services["22"]["product"] == "OpenSSH"
    assert "80" not in services


def test_ssdp_parsing_and_passive_receiver():
    payload = (
        "NOTIFY * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "NT: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        "USN: uuid:device-1::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        "SERVER: Linux/6.1 UPnP/1.1 Test/1.0\r\n\r\n"
    )
    parsed = parse_ssdp_payload(payload)
    assert parsed["nt"].endswith("MediaRenderer:1")
    assert parsed["server"].startswith("Linux/")

    def receiver(timeout):
        return [(payload.encode(), ("192.168.1.50", 1900))]

    results = listen_ssdp(0.1, receiver=receiver)
    assert results == [
        {
            "ip": "192.168.1.50",
            "host": "239.255.255.250:1900",
            "nt": "urn:schemas-upnp-org:device:MediaRenderer:1",
            "usn": "uuid:device-1::urn:schemas-upnp-org:device:MediaRenderer:1",
            "server": "Linux/6.1 UPnP/1.1 Test/1.0",
        }
    ]


@pytest.mark.parametrize(
    "evidence,expected",
    [
        ({"open_ports": [631, 9100], "services": {"631": {"protocol": "http"}}}, ("printer", "Embedded")),
        ({"open_ports": [8006]}, ("hypervisor", "Proxmox VE")),
        ({"open_ports": [445, 3389]}, ("Windows", "Windows")),
        ({"open_ports": [22], "services": {"22": {"banner": "SSH-2.0-OpenSSH_9.6 Ubuntu"}}}, ("Linux/Unix", "Linux/Unix")),
        ({"open_ports": [32400]}, ("media/IoT", "Plex")),
        ({"vendor": "Synology"}, ("NAS", "Embedded")),
        ({"ssdp": {"nt": "urn:schemas-upnp-org:device:MediaRenderer:1"}}, ("media/IoT", "Embedded")),
    ],
)
def test_classifier_rules(evidence, expected):
    device_type, os_guess, confidence = classify_device(**evidence)
    assert (device_type, os_guess) == expected
    assert confidence >= 0.5


def test_classifier_unknown_stays_unknown():
    assert classify_device() == ("unknown", "", 0.0)
    assert classify_device(open_ports=[5900]) == ("unknown", "", 0.15)


def test_merge_by_mac_ip_and_hostname(database):
    first = merge_observation(database, _observation())
    second = merge_observation(database, _observation(ip="192.168.1.10", hostname="renamed.lan"))
    assert first["id"] == second["id"]
    assert second["hostname"] == "renamed.lan"

    # Unknown MAC: merge by stable IP.
    by_ip = merge_observation(database, _observation(mac="", ip="192.168.1.30", hostname=""))
    same_ip = merge_observation(database, _observation(mac="", ip="192.168.1.30", hostname=""))
    assert by_ip["id"] == same_ip["id"]

    # Unknown MAC and no IP match: merge by hostname.
    by_name = merge_observation(database, _observation(mac="", ip="192.168.1.31", hostname="nas.lan"))
    same_name = merge_observation(database, _observation(mac="", ip="192.168.1.32", hostname="nas.lan"))
    assert by_name["id"] == same_name["id"]


def test_different_known_macs_are_never_merged(database):
    first = merge_observation(database, _observation(mac="00:11:22:33:44:01", ip="192.168.1.40"))
    second = merge_observation(database, _observation(mac="00:11:22:33:44:02", ip="192.168.1.40"))
    assert first["id"] != second["id"]
    events = [
        event
        for event in database.discovery_device_events(first["id"])
        if event["kind"] == "identity_conflict"
    ]
    assert events
    assert "00:11:22:33:44:02" in loads(events[0]["detail"], {})["observed_mac"]


def test_macless_observation_merges_into_mac_row(database):
    first = merge_observation(
        database, _observation(mac="b8:27:eb:11:22:33", ip="192.168.1.60")
    )
    anonymous = merge_observation(
        database, _observation(mac="", ip="192.168.1.60", hostname="")
    )
    assert anonymous["id"] == first["id"]
    assert anonymous["mac"] == "b8:27:eb:11:22:33"


def test_macless_duplicate_rows_are_absorbed(database):
    mac_row = merge_observation(
        database, _observation(mac="b8:27:eb:11:22:33", ip="192.168.1.61")
    )
    with database.connection() as conn:
        # Simulate the old bug: a MAC-less duplicate with a fresher timestamp.
        conn.execute(
            """INSERT INTO discovered_devices
               (id, first_seen, last_seen, mac, ip, hostname, state)
               VALUES ('dev_macless', '2026-01-01T00:00:00Z', '2026-11-01T00:00:00Z',
                       '', '192.168.1.61', '', 'new')"""
        )
    result = merge_observation(
        database, _observation(mac="", ip="192.168.1.61", hostname="")
    )
    assert result["id"] == mac_row["id"]
    assert result["mac"] == "b8:27:eb:11:22:33"
    rows, total = database.list_discovered_devices(mac="b8:27:eb:11:22:33")
    assert total == 1
    assert rows[0]["id"] == mac_row["id"]
    assert database.get_discovered_device("dev_macless") is None


def test_reobserved_new_device_becomes_known(database):
    first = merge_observation(database, _observation())
    assert first["state"] == "new"
    again = merge_observation(database, _observation())
    assert again["id"] == first["id"]
    assert again["state"] == "known"


def test_mac_change_after_stale_window_is_changed(database):
    first = merge_observation(
        database, _observation(mac="b8:27:eb:11:22:33", ip="192.168.1.62")
    )
    with database.connection() as conn:
        conn.execute(
            "UPDATE discovered_devices SET last_seen = ? WHERE id = ?",
            ("2026-01-01T00:00:00Z", first["id"]),
        )
    changed = merge_observation(
        database, _observation(mac="b8:27:eb:44:55:66", ip="192.168.1.62")
    )
    assert changed["id"] == first["id"]
    assert changed["mac"] == "b8:27:eb:44:55:66"
    assert changed["change_kind"] == "mac"
    assert changed["change"]["from"] == "b8:27:eb:11:22:33"
    assert changed["change"]["to"] == "b8:27:eb:44:55:66"
    rows, total = database.list_discovered_devices(state="changed")
    assert total == 1
    assert rows[0]["id"] == first["id"]


def test_private_mac_rotation_with_same_hostname_is_changed(database):
    first = merge_observation(
        database,
        _observation(mac="02:11:22:33:44:55", ip="192.168.1.92", hostname="phone.lan"),
    )
    rotated = merge_observation(
        database,
        _observation(mac="06:aa:bb:cc:dd:ee", ip="192.168.1.93", hostname="phone.lan"),
    )
    assert rotated["id"] == first["id"]
    assert rotated["change_kind"] == "mac"
    assert rotated["change"]["from"] == "02:11:22:33:44:55"
    assert rotated["change"]["to"] == "06:aa:bb:cc:dd:ee"
    conflicts = detect_identity_conflicts(database)
    assert conflicts == []


def test_operator_state_acknowledges_identity_change(database):
    first = merge_observation(
        database, _observation(mac="b8:27:eb:11:22:33", ip="192.168.1.63")
    )
    merge_observation(
        database, _observation(mac="b8:27:eb:11:22:33", ip="192.168.1.64")
    )
    assert database.get_discovered_device(first["id"])["change_kind"] == "ip"
    database.update_discovered_device(first["id"], state="known")
    row = database.get_discovered_device(first["id"])
    assert row["state"] == "known"
    assert row["change_kind"] == ""
    assert row["changed_at"] is None
    changed_rows, changed_total = database.list_discovered_devices(state="changed")
    assert changed_total == 0
    assert changed_rows == []


def test_event_deduplication_for_new_device_and_ports(database):
    first = merge_observation(database, _observation(open_ports=[22]))
    merge_observation(database, _observation(open_ports=[22]))
    events = database.discovery_device_events(first["id"])
    assert [event["kind"] for event in events if event["kind"] == "new_device"] == ["new_device"]
    assert not [event for event in events if event["kind"] == "new_port"]

    merge_observation(database, _observation(open_ports=[22, 80]))
    merge_observation(database, _observation(open_ports=[22, 80]))
    events = database.discovery_device_events(first["id"])
    new_ports = [event for event in events if event["kind"] == "new_port"]
    assert len(new_ports) == 1
    assert loads(new_ports[0]["detail"], {}) == {"port": 80}


def test_service_changed_events_are_deduplicated(database):
    first = merge_observation(
        database, _observation(open_ports=[22], services={"22": {"banner": "old"}})
    )
    merge_observation(
        database, _observation(open_ports=[22], services={"22": {"banner": "new"}})
    )
    merge_observation(
        database, _observation(open_ports=[22], services={"22": {"banner": "new"}})
    )
    events = [
        event
        for event in database.discovery_device_events(first["id"])
        if event["kind"] == "service_changed"
    ]
    assert len(events) == 1
    assert loads(events[0]["detail"], {})["from"] == {"banner": "old"}


def test_merge_links_enrolled_agent_and_wake_target(database):
    with database.connection() as conn:
        conn.execute(
            """INSERT INTO agents
               (id, name, hostname, os, mac, token_hash, enrolled_at)
               VALUES ('agt_1', 'Pi', 'pi.lan', 'linux', 'b8:27:eb:11:22:33', 'hash', ?)""",
            (utcnow_iso(),),
        )
        conn.execute(
            """INSERT INTO wake_targets
               (id, name, mac, created_by, created_at)
               VALUES ('wol_1', 'Pi wake', 'b8:27:eb:11:22:33', 'admin', ?)""",
            (utcnow_iso(),),
        )
    device = merge_observation(database, _observation())
    assert device["agent_id"] == "agt_1"
    assert device["wake_target_id"] == "wol_1"
    merge_observation(database, _observation(open_ports=[22, 80]))
    agent_events = database.list_agent_events("agt_1", 24, 50)
    assert any("Discovered new port 80" in event["title"] for event in agent_events)


def test_went_dark_is_recorded_once_per_last_seen(database):
    device = merge_observation(database, _observation())
    old = (utcnow() - timedelta(days=2)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with database.connection() as conn:
        conn.execute("UPDATE discovered_devices SET last_seen = ? WHERE id = ?", (old, device["id"]))
    marked = record_went_dark(database, ["192.168.1.0/24"], [])
    assert device["id"] in marked
    assert record_went_dark(database, ["192.168.1.0/24"], []) == []
    events = [
        event
        for event in database.discovery_device_events(device["id"])
        if event["kind"] == "went_dark"
    ]
    assert len(events) == 1


def test_run_scan_refuses_when_disabled_or_unacknowledged(database):
    scan = database.create_discovery_scan(["192.168.1.0/29"], ["neigh"], "admin")
    with pytest.raises(DiscoveryDisabled):
        run_scan(database, _config(enabled=False), scan)
    with pytest.raises(DiscoveryNotAcknowledged):
        run_scan(database, _config(), scan)


def test_run_scan_collects_neighbors_dns_ssdp_and_tcp(database, monkeypatch):
    _acknowledge(database)
    config = _config(
        cidrs=("192.168.1.0/29",),
        methods=("neigh", "dns", "oui", "ssdp", "tcp"),
        active_probes=True,
        probe_ports=(22,),
        max_hosts=16,
    )
    scan = database.create_discovery_scan(
        ["192.168.1.0/29"], ["neigh", "dns", "oui", "ssdp", "tcp"], "admin"
    )
    monkeypatch.setattr(
        discovery,
        "read_neighbor_table",
        lambda: [{"ip": "192.168.1.2", "mac": "b8:27:eb:aa:bb:cc", "hostname": "", "state": "REACHABLE"}],
    )
    monkeypatch.setattr(
        discovery,
        "reverse_dns",
        lambda ip: "printer.lan" if ip == "192.168.1.2" else "",
    )
    monkeypatch.setattr(
        discovery,
        "listen_ssdp",
        lambda timeout: [{"ip": "192.168.1.3", "nt": "urn:schemas-upnp-org:device:MediaRenderer:1"}],
    )
    monkeypatch.setattr(
        discovery,
        "probe_tcp_host",
        lambda ip, ports, **kwargs: {"22": {"protocol": "ssh", "banner": "SSH-2.0-OpenSSH_9.6"}}
        if ip == "192.168.1.3"
        else {},
    )
    progress: list[tuple[int, int]] = []
    result = run_scan(
        database,
        config,
        scan,
        progress=lambda percent, scanned: progress.append((percent, scanned)),
    )
    assert result["status"] == "succeeded"
    assert result["hosts_found"] == 2
    rows, total = database.list_discovered_devices(limit=10)
    assert total == 2
    by_ip = {row["ip"]: row for row in rows}
    assert by_ip["192.168.1.2"]["mac"] == "b8:27:eb:aa:bb:cc"
    assert by_ip["192.168.1.2"]["device_type"] == "printer"
    assert by_ip["192.168.1.3"]["mac"] == ""
    assert by_ip["192.168.1.3"]["device_type"] == "media/IoT"
    assert progress[-1] == (100, 2)


def test_identity_conflicts_group_ip_and_hostname(database):
    merge_observation(
        database,
        _observation(mac="00:11:22:33:44:01", ip="192.168.1.70", hostname="shared.lan"),
    )
    merge_observation(
        database,
        _observation(mac="00:11:22:33:44:02", ip="192.168.1.70", hostname="other.lan"),
    )
    merge_observation(
        database,
        _observation(mac="00:11:22:33:44:03", ip="192.168.1.71", hostname="shared.lan"),
    )
    conflicts = detect_identity_conflicts(database)
    types = {conflict["type"] for conflict in conflicts}
    assert "ip_multiple_macs" in types
    assert "hostname_multiple_identities" in types
    ip_conflict = next(item for item in conflicts if item["type"] == "ip_multiple_macs")
    assert ip_conflict["key"] == "192.168.1.70"
    assert len(ip_conflict["device_ids"]) == 2


def test_identity_change_for_mac_moving_ip_is_not_a_conflict(database):
    device = merge_observation(
        database, _observation(mac="00:11:22:33:44:09", ip="192.168.1.80")
    )
    moved = merge_observation(
        database, _observation(mac="00:11:22:33:44:09", ip="192.168.1.81")
    )
    assert moved["id"] == device["id"]
    assert moved["change_kind"] == "ip"
    assert moved["change"]["from"] == "192.168.1.80"
    assert moved["change"]["to"] == "192.168.1.81"
    events = database.discovery_device_events(device["id"])
    changed = [event for event in events if event["kind"] == "identity_changed"]
    assert changed
    assert loads(changed[0]["detail"], {})["kind"] == "ip"
    assert not [event for event in events if event["kind"] == "identity_conflict"]
    # A change is reported through the state filter, not as a conflict.
    changed_rows, changed_total = database.list_discovered_devices(state="changed")
    assert changed_total == 1
    assert changed_rows[0]["id"] == device["id"]
    conflict_rows, conflict_total = database.list_discovered_devices(conflict=True)
    assert conflict_total == 0
    assert conflict_rows == []


def test_exposure_filter_and_summary(database):
    merge_observation(
        database,
        _observation(
            mac="00:11:22:33:44:10",
            ip="192.168.1.90",
            open_ports=[22, 8006],
            services={"22": {"protocol": "ssh"}},
        ),
    )
    merge_observation(
        database,
        _observation(
            mac="00:11:22:33:44:11",
            ip="192.168.1.91",
            open_ports=[80],
            services={"80": {"protocol": "http"}},
        ),
    )
    rows, total = database.list_discovered_devices(exposure=True)
    assert total == 1
    assert rows[0]["ip"] == "192.168.1.90"
    with database.connection() as conn:
        conn.execute(
            """INSERT INTO agents (id, name, hostname, os, mac, token_hash, enrolled_at)
               VALUES ('agt_exposed', 'Exposed', 'exposed', 'linux',
                       '00:11:22:33:44:10', 'hash', ?)""",
            (utcnow_iso(),),
        )
    merge_observation(
        database,
        _observation(
            mac="00:11:22:33:44:10",
            ip="192.168.1.90",
            open_ports=[22, 8006],
            services={"22": {"protocol": "ssh"}},
        ),
    )
    rows, total = database.list_discovered_devices(exposure=True)
    assert total == 0
    summary = database.discovery_summary()
    assert summary["exposure"] == 0
    assert summary["managed"] == 1
    assert summary["unmanaged"] == 1
    assert summary["segments"][0]["gateway_hint"]


def test_wake_verification_neighbor_tcp_and_rejection():
    def neighbor_reader():
        return [
            {
                "ip": "192.168.1.40",
                "mac": "b8:27:eb:11:22:33",
                "hostname": "",
                "state": "REACHABLE",
            }
        ]

    by_neighbor = verify_reachability(
        "192.168.1.40", [], neighbor_reader=neighbor_reader
    )
    assert by_neighbor["reachable"] is True
    assert by_neighbor["method"] == "neighbor"

    def prober(ip, ports, **kwargs):
        return {"443": {"protocol": "tls"}}

    by_tcp = verify_reachability(
        "192.168.1.41", [443], neighbor_reader=lambda: [], prober=prober
    )
    assert by_tcp["reachable"] is True
    assert by_tcp["method"] == "tcp"
    assert by_tcp["open_ports"] == [443]

    missing = verify_reachability(
        "192.168.1.42", [], neighbor_reader=lambda: [], prober=prober
    )
    assert missing["reachable"] is False
    assert missing["method"] == ""

    with pytest.raises(DiscoveryError):
        verify_reachability("8.8.8.8", [], neighbor_reader=lambda: [])
