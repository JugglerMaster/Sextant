from __future__ import annotations

from sextant_agent import packages
from sextant_agent.collectors import MetricsCollector
from sextant_agent.config import AgentConfig
from tests.helpers import enroll_agent, heartbeat, headers, sample_metrics


def _pkg(name, version="1.0"):
    return {"manager": "apt", "package": name, "version": version, "arch": "amd64"}


def test_server_only_rewrites_package_inventory_when_hash_changes(client, admin):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]

    first = heartbeat(
        admin,
        agent,
        sample_metrics(
            packages=[_pkg("nginx")],
            package_managers=[{"name": "apt", "version": "2.7"}],
            package_inventory_hash="h1",
            package_inventory_full=True,
        ),
    )
    assert first["request_package_inventory"] == ""
    inventory = client.get(f"/api/agents/{agent_id}/packages", headers=headers(admin)).json()
    assert [row["package"] for row in inventory["inventory"]] == ["nginx"]

    # Same hash, no rows: the server should not touch inventory.
    same = heartbeat(admin, agent, sample_metrics(package_inventory_hash="h1"))
    assert same["request_package_inventory"] == ""
    inventory = client.get(f"/api/agents/{agent_id}/packages", headers=headers(admin)).json()
    assert [row["package"] for row in inventory["inventory"]] == ["nginx"]

    # New hash without rows: request a full snapshot.
    mismatch = heartbeat(admin, agent, sample_metrics(package_inventory_hash="h2"))
    assert mismatch["request_package_inventory"] == "full"

    # Full replacement.
    heartbeat(
        admin,
        agent,
        sample_metrics(
            packages=[_pkg("nginx", "2.0"), _pkg("curl")],
            package_managers=[{"name": "apt", "version": "2.7"}],
            package_inventory_hash="h2",
            package_inventory_full=True,
        ),
    )
    inventory = client.get(f"/api/agents/{agent_id}/packages", headers=headers(admin)).json()
    assert {row["package"] for row in inventory["inventory"]} == {"nginx", "curl"}
    assert next(row for row in inventory["inventory"] if row["package"] == "nginx")["version"] == "2.0"


def test_service_inventory_timestamp_is_stored(client, admin):
    agent = enroll_agent(admin)
    heartbeat(
        admin,
        agent,
        sample_metrics(service_inventory_collected_at="2026-11-10T12:00:00Z"),
    )
    detail = client.get(f"/api/agents/{agent['agent_id']}", headers=headers(admin)).json()["agent"]
    assert detail["service_inventory_at"] == "2026-11-10T12:00:00Z"


def test_server_applies_inventory_delta_and_rejects_bad_base(client, admin):
    agent = enroll_agent(admin)
    agent_id = agent["agent_id"]
    first = heartbeat(
        admin,
        agent,
        sample_metrics(
            packages=[_pkg("nginx")],
            package_managers=[{"name": "apt", "version": "2.7"}],
            package_inventory_hash="h1",
            package_inventory_full=True,
            package_inventory_mode="full",
        ),
    )
    assert first["package_inventory_revision"] == 1

    delta = heartbeat(
        admin,
        agent,
        sample_metrics(
            packages=[_pkg("curl")],
            package_inventory_deletes=[["apt", "nginx"]],
            package_inventory_hash="h2",
            package_inventory_mode="delta",
            package_inventory_base_revision=1,
        ),
    )
    assert delta["request_package_inventory"] == ""
    assert delta["package_inventory_revision"] == 2
    inventory = client.get(f"/api/agents/{agent_id}/packages", headers=headers(admin)).json()
    assert {row["package"] for row in inventory["inventory"]} == {"curl"}

    bad_base = heartbeat(
        admin,
        agent,
        sample_metrics(
            package_inventory_hash="h3",
            package_inventory_mode="delta",
            package_inventory_base_revision=99,
        ),
    )
    assert bad_base["request_package_inventory"] == "full"
    assert bad_base["package_inventory_revision"] == 2

    unchanged = heartbeat(
        admin,
        agent,
        sample_metrics(
            package_inventory_hash="h2",
            package_inventory_mode="delta",
            package_inventory_base_revision=2,
        ),
    )
    assert unchanged["request_package_inventory"] == ""
    assert unchanged["package_inventory_revision"] == 2


def test_agent_package_payload_is_change_gated(monkeypatch):
    config = AgentConfig(
        server_url="https://example.test",
        agent_id="agt_test",
        agent_token="token",
        package_inventory_interval=300,
        package_full_interval=3600,
    )
    config.validate()
    collector = MetricsCollector(config)
    rows = [_pkg("nginx")]
    monkeypatch.setattr(packages, "collect_inventory", lambda: list(rows))
    monkeypatch.setattr(packages, "detect_managers", lambda: [{"name": "apt", "version": "2.7"}])

    collector._ensure_package_inventory()
    assert collector._package_payload is not None
    assert collector._package_payload["full"] is True
    assert collector._package_hash

    collector.ack_package_payload(1)
    collector._ensure_package_inventory()
    assert collector._package_payload is None

    rows.append(_pkg("curl"))
    collector._package_forced = True
    collector._ensure_package_inventory()
    assert collector._package_payload is not None
    assert collector._package_payload["full"] is False
    assert collector._package_payload["mode"] == "delta"
    assert collector._package_payload["base_revision"] == 1
    assert {row["package"] for row in collector._package_payload["packages"]} == {"curl"}

    collector.ack_package_payload(2)
    collector.request_package_full()
    collector._ensure_package_inventory()
    assert collector._package_payload is not None
    assert collector._package_payload["full"] is True


def test_agent_applies_server_profile_cadence():
    from sextant_agent.agent import AgentRunner

    config = AgentConfig(server_url="https://example.test", agent_id="a", agent_token="b")
    runner = AgentRunner(config)
    runner._apply_profile(
        {
            "heartbeat_seconds": 90,
            "modules": {"shell": False, "package_management": False},
            "collection": {
                "service_inventory_seconds": 7200,
                "package_inventory_seconds": 86400,
                "package_full_seconds": 604800,
            },
            "actions": {"allow_shell": True, "allow_packages": True},
            "update": {"allow_self_update": True},
            "max_output_bytes": 5000,
        }
    )
    assert runner.config.interval == 90
    assert runner.config.service_inventory_interval == 7200
    assert runner.config.package_inventory_interval == 86400
    assert runner.config.max_output_bytes == 5000
    assert runner.remote_policy["allow_shell"] is False
    assert runner.remote_policy["allow_packages"] is False
