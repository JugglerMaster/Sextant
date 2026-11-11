from __future__ import annotations

import hashlib
from pathlib import Path

from runner_agent import selfupdate
from runner_agent.config import AgentConfig
from tests.helpers import enroll_agent, headers, issue_token, sample_metrics, heartbeat


def _agent_config() -> AgentConfig:
    return AgentConfig(
        server_url="https://example.test",
        agent_id="agt_test",
        agent_token="token",
        allow_self_update=True,
    )


def _publish_agent_wheel(client, admin, version="9.9.9", data=b"wheel-bytes"):
    package = client.post(
        "/api/store/packages",
        json={"slug": "runner-agent", "name": "Runner Agent", "kind": "agent"},
        headers=headers(admin),
    )
    assert package.status_code == 200, package.text
    response = client.put(
        f"/api/store/packages/runner-agent/releases/{version}",
        content=data,
        headers={
            **headers(admin),
            "X-Artifact-Type": "agent-wheel",
            "X-Filename": f"runner_control-{version}-py3-none-any.whl",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_agent_update_endpoint_and_heartbeat(client, admin):
    release = _publish_agent_wheel(client, admin)
    agent = enroll_agent(admin, token=issue_token(admin))

    response = client.get(
        f"/api/agent/{agent['agent_id']}/update?current_version=0.2.0",
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["version"] == "9.9.9"
    assert body["sha256"] == release["sha256"]

    current = client.get(
        f"/api/agent/{agent['agent_id']}/update?current_version=9.9.9",
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert current.status_code == 200
    assert current.json()["available"] is False

    beat = heartbeat(admin, agent, sample_metrics(agent_version="0.2.0"))
    assert beat["agent_update"]["available"] is True
    assert beat["agent_update"]["version"] == "9.9.9"


def test_selfupdate_check_uses_client(monkeypatch):
    config = _agent_config()
    monkeypatch.setattr(
        "runner_agent.selfupdate.AgentClient.get_update",
        lambda self, current: {"available": True, "version": "1.2.3", "current_version": current},
    )
    info = selfupdate.check(config)
    assert info["available"] is True
    assert info["version"] == "1.2.3"


def test_selfupdate_apply_verifies_and_switches(monkeypatch, tmp_path):
    data = b"new-wheel"
    sha = hashlib.sha256(data).hexdigest()
    info = {
        "available": True,
        "version": "1.2.3",
        "sha256": sha,
        "size": len(data),
        "filename": "runner_control-1.2.3-py3-none-any.whl",
        "download_url": "/api/agent/agt_test/store/releases/rel_test/download",
    }

    def fake_download(self, path, destination, max_bytes=0):
        Path(destination).write_bytes(data)
        return len(data)

    switched = []
    monkeypatch.setattr("runner_agent.selfupdate.AgentClient.download", fake_download)
    monkeypatch.setattr("runner_agent.selfupdate._stage_venv", lambda version, wheel, install_dir: tmp_path / "venv")
    monkeypatch.setattr("runner_agent.selfupdate._health_check", lambda venv, cfg: True)
    monkeypatch.setattr("runner_agent.selfupdate._switch_current", lambda install_dir, version: switched.append(version))
    monkeypatch.setattr("runner_agent.selfupdate._prune_releases", lambda install_dir: None)

    result = selfupdate.apply_update(
        _agent_config(),
        info,
        install_dir=tmp_path,
        config_path=tmp_path / "config.json",
    )
    assert result["status"] == "switched"
    assert switched == ["1.2.3"]


def test_selfupdate_rejects_bad_hash(monkeypatch, tmp_path):
    info = {
        "available": True,
        "version": "1.2.3",
        "sha256": "0" * 64,
        "size": 4,
        "filename": "wheel.whl",
        "download_url": "/download",
    }

    def fake_download(self, path, destination, max_bytes=0):
        Path(destination).write_bytes(b"data")
        return 4

    monkeypatch.setattr("runner_agent.selfupdate.AgentClient.download", fake_download)
    result = selfupdate.apply_update(
        _agent_config(),
        info,
        install_dir=tmp_path,
        config_path=tmp_path / "config.json",
    )
    assert result["status"] == "failed"
    assert "verification" in result["error"].lower()


def test_selfupdate_disabled_locally(tmp_path):
    config = _agent_config()
    config.allow_self_update = False
    result = selfupdate.apply_update(
        config,
        {"available": True, "version": "1.2.3"},
        install_dir=tmp_path,
        config_path=tmp_path / "config.json",
    )
    assert result["status"] == "disabled"
