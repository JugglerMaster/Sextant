from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from sextant_agent import selfupdate
from sextant_agent.config import AgentConfig
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
        json={"slug": "sextant-agent", "name": "Sextant Agent", "kind": "agent"},
        headers=headers(admin),
    )
    assert package.status_code == 200, package.text
    response = client.put(
        f"/api/store/packages/sextant-agent/releases/{version}",
        content=data,
        headers={
            **headers(admin),
            "X-Artifact-Type": "agent-wheel",
            "X-Filename": f"sextant-{version}-py3-none-any.whl",
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
        "sextant_agent.selfupdate.AgentClient.get_update",
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
        "filename": "sextant-1.2.3-py3-none-any.whl",
        "download_url": "/api/agent/agt_test/store/releases/rel_test/download",
    }

    def fake_download(self, path, destination, max_bytes=0):
        Path(destination).write_bytes(data)
        return len(data)

    switched = []
    monkeypatch.setattr("sextant_agent.selfupdate.AgentClient.download", fake_download)
    monkeypatch.setattr("sextant_agent.selfupdate._stage_venv", lambda version, wheel, install_dir: tmp_path / "venv")
    monkeypatch.setattr("sextant_agent.selfupdate._health_check", lambda venv, cfg: True)
    monkeypatch.setattr("sextant_agent.selfupdate._switch_current", lambda install_dir, version: switched.append(version))
    monkeypatch.setattr("sextant_agent.selfupdate._prune_releases", lambda install_dir: None)

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

    monkeypatch.setattr("sextant_agent.selfupdate.AgentClient.download", fake_download)
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


def test_selfupdate_apply_windows_switches_and_restarts(monkeypatch, tmp_path):
    data = b"windows-wheel"
    sha = hashlib.sha256(data).hexdigest()
    info = {
        "available": True,
        "version": "1.2.3",
        "sha256": sha,
        "size": len(data),
        "filename": "sextant-1.2.3-py3-none-any.whl",
        "download_url": "/api/agent/agt_test/store/releases/rel_test/download",
    }

    def fake_download(self, path, destination, max_bytes=0):
        Path(destination).write_bytes(data)
        return len(data)

    switched = []
    restarted = []
    monkeypatch.setattr("sextant_agent.selfupdate._is_windows", lambda: True)
    monkeypatch.setattr("sextant_agent.selfupdate.AgentClient.download", fake_download)
    monkeypatch.setattr("sextant_agent.selfupdate._stage_venv", lambda version, wheel, install_dir: tmp_path / "venv")
    monkeypatch.setattr("sextant_agent.selfupdate._health_check", lambda venv, cfg: True)
    monkeypatch.setattr("sextant_agent.selfupdate._ensure_windows_current", lambda install_dir: tmp_path / "current")
    monkeypatch.setattr("sextant_agent.selfupdate._update_windows_task_action", lambda install_dir: (True, "task ok"))
    monkeypatch.setattr("sextant_agent.selfupdate._switch_current", lambda install_dir, version: switched.append(version))
    monkeypatch.setattr("sextant_agent.selfupdate._prune_releases", lambda install_dir: None)
    monkeypatch.setattr("sextant_agent.selfupdate._restart_windows_task", lambda: restarted.append(True) or (True, ""))

    result = selfupdate.apply_update(
        _agent_config(),
        info,
        install_dir=tmp_path,
        config_path=tmp_path / "config.json",
        restart=True,
    )

    assert result["status"] == "switched"
    assert result["version"] == "1.2.3"
    assert result["restart_required"] is False
    assert result["task"] == "task ok"
    assert switched == ["1.2.3"]
    assert restarted == [True]


def test_windows_task_action_switches_to_current(monkeypatch, tmp_path):
    xml = """<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <Actions><Exec>
        <Command>C:\\ProgramData\\SextantAgent\\venv\\Scripts\\sextant-agent.exe</Command>
        <Arguments>run --config "C:\\ProgramData\\SextantAgent\\config.json" --log-file "C:\\ProgramData\\SextantAgent\\agent.log"</Arguments>
        <WorkingDirectory>C:\\ProgramData\\SextantAgent</WorkingDirectory>
      </Exec></Actions>
    </Task>"""
    calls = []

    def fake_run(args, timeout=300):
        calls.append(args)
        if args[:2] == ["schtasks.exe", "/Query"]:
            return subprocess.CompletedProcess(args, 0, xml, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("sextant_agent.selfupdate._run", fake_run)
    ok, message = selfupdate._update_windows_task_action(tmp_path)

    assert ok is True
    assert "updated" in message.lower()
    change = next(call for call in calls if "/Change" in call)
    run_spec = change[change.index("/TR") + 1]
    assert str(tmp_path / "current" / "Scripts" / "sextant-agent.exe") in run_spec
    assert 'run --config "C:\\ProgramData\\SextantAgent\\config.json"' in run_spec

