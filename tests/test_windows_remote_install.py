from __future__ import annotations

import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from sextant_control import windows_remote
from sextant_control.server import create_app
from tests.helpers import headers, login


class _Result:
    def __init__(self, status_code=0, std_out="ok", std_err=""):
        self.status_code = status_code
        self.std_out = std_out
        self.std_err = std_err


class FakeClient:
    def __init__(self, *, fail_with: str = ""):
        self.scripts: list[str] = []
        self.uploads: dict[str, bytes] = {}
        self.fail_with = fail_with

    def run_ps(self, script: str) -> str:
        self.scripts.append(script)
        if self.fail_with:
            raise windows_remote.WindowsRemoteError(self.fail_with)
        if "install-windows-agent.ps1" in script:
            return "Sextant agent installed and started."
        return "admin=ok python=ok"

    def upload_file(self, remote_path: str, data: bytes) -> None:
        self.uploads[remote_path] = data


def test_windows_target_validation_and_optional_dependency(monkeypatch):
    with pytest.raises(windows_remote.WindowsRemoteError):
        windows_remote._validate_target("8.8.8.8", 5986, "Administrator")
    with pytest.raises(windows_remote.WindowsRemoteError):
        windows_remote._validate_target("bad host", 5986, "Administrator")
    with pytest.raises(windows_remote.WindowsRemoteError):
        windows_remote._validate_target("192.168.1.10", 5986, "bad/user name")
    windows_remote._validate_target("192.168.1.10", 5986, r"CORP\Administrator")

    monkeypatch.setitem(sys.modules, "winrm", None)
    assert windows_remote.windows_remote_available() is False
    with pytest.raises(windows_remote.WindowsRemoteError):
        windows_remote._load_winrm()


def test_windows_connection_and_install_script_redacts_secrets(tmp_path, monkeypatch):
    wheel = tmp_path / "sextant-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-data")
    logs: list[str] = []
    fake = FakeClient()
    password = "super-secret-password"
    token = "sextant_enroll_secret_token"

    def factory(host, port, user, supplied_password, ca_file, timeout):
        assert supplied_password == password
        return fake

    output = windows_remote.test_connection(
        host="192.168.1.10",
        port=5986,
        user="Administrator",
        password=password,
        client_factory=factory,
    )
    assert "admin=ok" in output
    assert password not in output

    windows_remote.run_install(
        host="192.168.1.10",
        port=5986,
        user="Administrator",
        password=password,
        server_url="https://ops.example.com",
        token=token,
        name="studio-pc",
        profile="workstation",
        watch_services=["Spooler"],
        wheel_path=str(wheel),
        client_factory=factory,
        log=logs.append,
    )
    assert fake.uploads
    installer_data = b"".join(
        data for path, data in fake.uploads.items() if path.endswith(".ps1")
    )
    assert b"ExpectedSha256" in installer_data
    assert b"Register-ScheduledTask" in installer_data
    install_script = next(script for script in fake.scripts if "install-windows-agent.ps1" in script)
    assert "SEXTANT_ENROLL_TOKEN" in install_script
    assert "studio-pc" in install_script
    assert "ExpectedSha256" in install_script
    assert all(password not in line and token not in line for line in logs)


def test_windows_install_failure_redacts_credentials(tmp_path):
    wheel = tmp_path / "sextant-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-data")
    password = "super-secret-password"
    token = "sextant_enroll_secret_token"

    with pytest.raises(windows_remote.WindowsRemoteError) as exc_info:
        windows_remote.run_install(
            host="192.168.1.10",
            port=5986,
            user="Administrator",
            password=password,
            server_url="https://ops.example.com",
            token=token,
            name="studio-pc",
            profile="workstation",
            watch_services=[],
            wheel_path=str(wheel),
            client_factory=lambda *args: FakeClient(fail_with=f"bad {password} {token}"),
        )
    message = str(exc_info.value)
    assert password not in message
    assert token not in message
    assert "***" in message


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


def test_windows_remote_install_api_requires_password_and_redacts(settings, monkeypatch, tmp_path):
    app = create_app(settings)
    captured = {}
    password = "super-secret-password"
    token_holder = {}

    def fake_install(**kwargs):
        captured.update(kwargs)
        token_holder["token"] = kwargs["token"]
        kwargs["log"]("fake windows install log")

    monkeypatch.setattr(windows_remote, "run_install", fake_install)
    monkeypatch.setattr(
        "sextant_control.server._wait_for_remote_agent_heartbeat",
        lambda *args, **kwargs: True,
    )

    with TestClient(app) as client:
        admin = login(client)
        _publish_agent_wheel(client, admin)

        missing = client.post(
            "/api/remote-install",
            json={
                "host": "192.168.1.10",
                "port": 5986,
                "user": "Administrator",
                "os": "windows",
                "transport": "winrm",
            },
            headers=headers(admin),
        )
        assert missing.status_code == 400

        monkeypatch.setattr(windows_remote, "test_connection", lambda **kwargs: "admin=ok")
        tested = client.post(
            "/api/remote-install/test",
            json={
                "host": "192.168.1.10",
                "port": 5986,
                "user": "Administrator",
                "os": "windows",
                "transport": "winrm",
                "password": password,
            },
            headers=headers(admin),
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["output"] == "admin=ok"

        created = client.post(
            "/api/remote-install",
            json={
                "host": "192.168.1.10",
                "port": 5986,
                "user": "Administrator",
                "name": "studio-pc",
                "profile": "workstation",
                "watch_services": ["Spooler"],
                "os": "windows",
                "transport": "winrm",
                "password": password,
            },
            headers=headers(admin),
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        assert created.json()["os"] == "windows"
        assert created.json()["transport"] == "winrm"
        assert password not in created.text

        deadline = time.monotonic() + 5
        status = ""
        while time.monotonic() < deadline:
            job = client.get(f"/api/remote-install/{job_id}", headers=headers(admin)).json()
            status = job["status"]
            if status in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert status == "succeeded", job
        job = client.get(f"/api/remote-install/{job_id}", headers=headers(admin)).json()
        assert "fake windows install log" in job["log"]
        assert captured["host"] == "192.168.1.10"
        assert captured["port"] == 5986
        assert captured["password"] == password
        assert password not in job["log"]
        assert password not in json.dumps(client.get("/api/audit").json())
        assert token_holder["token"] not in job["log"]
        assert token_holder["token"] not in json.dumps(client.get("/api/audit").json())
