from __future__ import annotations

import pytest

from runner_agent import packages
from runner_agent.config import AgentConfig
from runner_agent.executor import execute_command
from tests.helpers import enroll_agent, heartbeat, headers, issue_token, sample_metrics


def _agent() -> AgentConfig:
    return AgentConfig(
        server_url="https://example.test",
        agent_id="agt_test",
        agent_token="token",
        allow_packages=True,
    )


def test_package_command_builders():
    assert packages._command("apt", "install", "nginx", "") == [
        "apt-get",
        "install",
        "-y",
        "--no-install-recommends",
        "-o",
        "DPkg::Lock::Timeout=60",
        "nginx",
    ]
    assert packages._command("apt", "install", "nginx", "1.24.0-1")[-1] == "nginx=1.24.0-1"
    assert packages._command("dnf", "install", "httpd", "2.4.62")[-1] == "httpd-2.4.62"
    assert packages._command("winget", "install", "7zip.7zip", "")[:3] == ["winget", "install", "--id"]
    assert packages._command("choco", "upgrade", "git", "")[:4] == ["choco", "upgrade", "-y", "--no-progress"]
    with pytest.raises(ValueError):
        packages.normalize_actions([{"manager": "apt", "action": "install", "package": "-bad"}])


def test_package_inventory_parsers(monkeypatch):
    dpkg = type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": "nginx\t1.24.0-1\tamd64\npython3\t3.12.3\tamd64\n",
            "stderr": "",
        },
    )()
    winget = type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": "Name    Id            Version  Available  Source\n----    --            -------  ---------  ------\n7-Zip   7zip.7zip     24.09               winget\n",
            "stderr": "",
        },
    )()

    def fake_run(args, timeout=60):
        if args[0] == "dpkg-query":
            return dpkg
        if args[0] == "winget":
            return winget
        return None

    monkeypatch.setattr(packages.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(packages, "_run", fake_run)
    inventory = packages.collect_inventory()
    assert {entry["package"] for entry in inventory} == {"nginx", "python3", "7zip.7zip"}
    assert {entry["manager"] for entry in inventory} == {"apt", "winget"}


def test_package_actions_respect_policy(monkeypatch):
    denied = packages.apply_actions(
        [{"manager": "apt", "action": "install", "package": "nginx"}],
        _agent(),
        {"allow_packages": False},
    )
    assert denied["status"] == "failed"
    assert "disabled" in denied["error"]

    calls = []

    def fake_run_capture(args, timeout=300, max_output=200000, env=None):
        calls.append((args, env))
        return {"status": "succeeded", "exit_code": 0, "stdout": "ok", "stderr": "", "error": ""}

    monkeypatch.setattr("runner_agent.executor.run_capture", fake_run_capture)
    allowed = packages.apply_actions(
        [{"manager": "apt", "action": "install", "package": "nginx"}],
        _agent(),
        {"allow_packages": True},
    )
    assert allowed["status"] == "succeeded"
    assert calls and calls[0][1]["DEBIAN_FRONTEND"] == "noninteractive"


def test_executor_routes_package_commands():
    config = AgentConfig(
        server_url="https://example.test",
        agent_id="agt_test",
        agent_token="token",
        allow_packages=False,
    )
    result = execute_command(
        {"kind": "package", "args": {"actions": [{"manager": "apt", "action": "install", "package": "nginx"}]}},
        config,
        {"allow_packages": True},
    )
    assert result["status"] == "failed"
    assert "disabled" in result["error"]


def test_package_inventory_and_desired_state_api(client, admin):
    agent = enroll_agent(admin, token=issue_token(admin), profile="server")
    agent_id = agent["agent_id"]
    client.patch(
        f"/api/agents/{agent_id}",
        json={"allow_packages": True},
        headers=headers(admin),
    )
    heartbeat(
        admin,
        agent,
        sample_metrics(
            packages=[
                {"manager": "apt", "package": "nginx", "version": "1.24.0-1", "arch": "amd64"},
                {"manager": "apt", "package": "telnet", "version": "0.17-42", "arch": "amd64"},
            ],
            package_managers=[{"name": "apt", "version": "2.7.14"}],
        ),
    )
    packages_response = client.get(f"/api/agents/{agent_id}/packages", headers=headers(admin))
    assert packages_response.status_code == 200
    body = packages_response.json()
    assert body["inventory"][0]["package"] == "nginx"
    assert body["managers"][0]["name"] == "apt"

    rules = client.put(
        f"/api/agents/{agent_id}/packages",
        json=[
            {"manager": "apt", "package": "nginx", "desired_state": "present"},
            {"manager": "apt", "package": "telnet", "desired_state": "absent"},
        ],
        headers=headers(admin),
    )
    assert rules.status_code == 200

    sync = client.post(f"/api/agents/{agent_id}/packages/sync", headers=headers(admin))
    assert sync.status_code == 200, sync.text
    assert sync.json()["queued"] is True
    assert sync.json()["actions"] == [{"manager": "apt", "action": "remove", "package": "telnet"}]

    dispatched = heartbeat(admin, agent)
    assert dispatched["commands"][0]["kind"] == "package"


def test_package_sync_rejects_disabled_policy(client, admin):
    agent = enroll_agent(admin, token=issue_token(admin))
    client.patch(
        f"/api/agents/{agent['agent_id']}",
        json={"allow_packages": False},
        headers=headers(admin),
    )
    client.put(
        f"/api/agents/{agent['agent_id']}/packages",
        json=[{"manager": "apt", "package": "nginx", "desired_state": "present"}],
        headers=headers(admin),
    )
    response = client.post(f"/api/agents/{agent['agent_id']}/packages/sync", headers=headers(admin))
    assert response.status_code == 409
    assert "blocked" in response.text.lower()
