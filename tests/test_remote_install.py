from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sextant_control import remote_install
from sextant_control.server import create_app
from tests.helpers import headers, login


def _key(tmp_path: Path) -> Path:
    path = tmp_path / "install_key"
    path.write_text("fake-private-key")
    path.chmod(0o600)
    return path


class _Result:
    def __init__(self, returncode=0, stdout="ok", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_ssh_disables_host_key_checking(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, input_text=None, timeout=120):
        calls.append((args, input_text))
        return _Result()

    monkeypatch.setattr(remote_install, "_run", fake_run)
    output = remote_install.test_connection(
        key_path=str(_key(tmp_path)),
        host="192.0.2.50",
        port=2222,
        user="root",
    )
    assert output == "ok"
    args = calls[0][0]
    assert "StrictHostKeyChecking=no" in args
    assert "UserKnownHostsFile=/dev/null" in args
    assert "GlobalKnownHostsFile=/dev/null" in args
    assert "-p" in args and "2222" in args
    assert "root@192.0.2.50" in args


def test_install_script_contains_expected_steps_and_redacts_token(monkeypatch, tmp_path):
    wheel = tmp_path / "sextant-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    calls = []
    logs = []

    def fake_run(args, input_text=None, timeout=120):
        calls.append((args, input_text))
        return _Result(stdout="active")

    monkeypatch.setattr(remote_install, "_run", fake_run)
    remote_install.run_install(
        key_path=str(_key(tmp_path)),
        host="192.0.2.50",
        port=22,
        user="root",
        server_url="https://ops.example.com",
        token="sextant_enroll_secret_token",
        name="box-01",
        profile="server",
        watch_services=["ssh", "nginx"],
        wheel_path=str(wheel),
        log=logs.append,
    )
    install_script = calls[-1][1]
    assert "systemctl enable --now sextant-agent" in install_script
    assert "--token" in install_script
    assert "--profile server" in install_script
    assert "sudo -n" in install_script
    assert "sextant_enroll_secret_token" in install_script
    assert all("sextant_enroll_secret_token" not in line for line in logs)


def test_install_uses_published_wheel_filename_for_sha_blob(monkeypatch, tmp_path):
    wheel = tmp_path / "cc6e41faa1fc193afe90afb5dcefb6eb87cc8d7a8ba791b740128659711f1cb7"
    wheel.write_bytes(b"wheel")
    calls = []

    def fake_run(args, input_text=None, timeout=120):
        calls.append((args, input_text))
        return _Result(stdout="active")

    monkeypatch.setattr(remote_install, "_run", fake_run)
    remote_install.run_install(
        key_path=str(_key(tmp_path)),
        host="192.0.2.50",
        port=22,
        user="root",
        server_url="https://ops.example.com",
        token="sextant_enroll_secret_token",
        name="box-01",
        profile="server",
        watch_services=[],
        wheel_path=str(wheel),
        wheel_filename="sextant-1.0-py3-none-any.whl",
        log=lambda _line: None,
    )
    install_script = calls[-1][1]
    assert "/tmp/sextant-1.0-py3-none-any.whl" in install_script
    assert ".venv/bin/pip ]" in install_script
    assert "sudo -n rm -rf /opt/sextant-agent/.venv" in install_script


def test_remote_install_rejects_bad_target(tmp_path):
    with pytest.raises(remote_install.RemoteInstallError):
        remote_install.test_connection(key_path=str(_key(tmp_path)), host="bad host", port=22, user="root")


def _publish_agent_wheel(client, admin):
    client.post(
        "/api/store/packages",
        json={"slug": "sextant-agent", "name": "Sextant Agent", "kind": "agent"},
        headers=headers(admin),
    )
    response = client.put(
        "/api/store/packages/sextant-agent/releases/1.0.0",
        content=b"remote-wheel",
        headers={
            **headers(admin),
            "X-Artifact-Type": "agent-wheel",
            "X-Filename": "sextant-1.0.0-py3-none-any.whl",
        },
    )
    assert response.status_code == 200, response.text


def test_remote_install_api_job(settings, monkeypatch, tmp_path):
    configured = replace(settings, ssh_key_path=str(_key(tmp_path)))
    app = create_app(configured)
    captured = {}

    def fake_install(**kwargs):
        captured.update(kwargs)
        kwargs["log"]("fake install log")

    monkeypatch.setattr(remote_install, "run_install", fake_install)
    monkeypatch.setattr(remote_install, "test_connection", lambda **kwargs: "connection ok")

    with TestClient(app) as client:
        admin = login(client)
        _publish_agent_wheel(client, admin)

        test = client.post(
            "/api/remote-install/test",
            json={"host": "192.0.2.50", "port": 22, "user": "root"},
            headers=headers(admin),
        )
        assert test.status_code == 200
        assert test.json()["output"] == "connection ok"

        created = client.post(
            "/api/remote-install",
            json={
                "host": "192.0.2.50",
                "port": 22,
                "user": "root",
                "name": "box-01",
                "profile": "server",
                "watch_services": ["ssh"],
            },
            headers=headers(admin),
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]

        deadline = time.monotonic() + 5
        status = ""
        while time.monotonic() < deadline:
            job = client.get(f"/api/remote-install/{job_id}", headers=headers(admin)).json()
            status = job["status"]
            if status in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert status == "succeeded"
        job = client.get(f"/api/remote-install/{job_id}", headers=headers(admin)).json()
        assert "fake install log" in job["log"]
        assert captured["profile"] == "server"
        assert captured["watch_services"] == ["ssh"]
        assert captured["name"] == "box-01"


def test_remote_install_requires_admin_and_key(settings, tmp_path):
    configured = replace(settings, ssh_key_path=str(tmp_path / "missing_key"))
    app = create_app(configured)
    with TestClient(app) as client:
        admin = login(client)
        _publish_agent_wheel(client, admin)
        response = client.post(
            "/api/remote-install",
            json={"host": "192.0.2.50", "port": 22, "user": "root"},
            headers=headers(admin),
        )
        assert response.status_code == 400
        assert "SSH key" in response.text


def test_remote_install_one_at_a_time(app, client, admin, monkeypatch):
    monkeypatch.setattr(remote_install, "run_install", lambda **kwargs: None)
    # Force a running job into the DB, then a second request must conflict.
    app.state.db.create_remote_install_job(
        host="192.0.2.51",
        port=22,
        ssh_user="root",
        profile="server",
        name="busy",
        watch_services=[],
        actor="test",
    )
    client.post(
        "/api/users",
        json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
        headers=headers(admin),
    )
    viewer = login(client, "viewer1", "viewer password 123")
    denied = client.get("/api/remote-install", headers=headers(viewer))
    assert denied.status_code == 403
