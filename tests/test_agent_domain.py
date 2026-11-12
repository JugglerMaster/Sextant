from __future__ import annotations

import pytest

from sextant_agent import domain


def test_domain_validation_and_status_action():
    with pytest.raises(ValueError):
        domain._validate({"domain": "bad domain", "username": "u", "password": "p"}, need_password=True)
    with pytest.raises(ValueError):
        domain._validate(
            {"domain": "corp.example.com", "username": "u", "password": "p", "computer_name": "not a name"},
            need_password=True,
        )
    values, name = domain._validate(
        {
            "domain": "corp.example.com",
            "username": "CORP\\svc-join",
            "password": "secret",
            "ou": "OU=Workstations,DC=corp,DC=example,DC=com",
            "computer_name": "WS-01",
        },
        need_password=True,
    )
    assert name == "corp.example.com"
    assert values["computer_name"] == "WS-01"
    invalid = domain.run_domain({"action": "delete"})
    assert invalid["status"] == "failed"


def test_pty_sends_password_without_a_prompt():
    result = domain._run_pty(
        ["bash", "-c", "IFS= read -r value; printf 'GOT=%s\\n' \"$value\""],
        "super-secret",
        timeout=5,
        max_output=10_000,
        send_immediately=True,
    )
    assert result["status"] == "succeeded"
    assert "GOT=[redacted]" in result["stdout"]
    assert "super-secret" not in result["stdout"]


def test_pty_answers_a_password_prompt():
    result = domain._run_pty(
        [
            "bash",
            "-c",
            "printf 'Password: '; IFS= read -r value; printf 'GOT=%s\\n' \"$value\"",
        ],
        "super-secret",
        timeout=5,
        max_output=10_000,
    )
    assert result["status"] == "succeeded"
    assert "Password:" in result["stdout"]
    assert "GOT=[redacted]" in result["stdout"]


def test_windows_join_keeps_password_out_of_script(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input_text"] = kwargs.get("input_text") or ""
        captured["env"] = kwargs.get("env") or {}
        return {"status": "succeeded", "exit_code": 0, "stdout": "Domain: corp.example.com\nPartOfDomain: True\n", "stderr": "", "error": ""}

    monkeypatch.setattr(domain.sys, "platform", "win32")
    monkeypatch.setattr(domain.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(domain, "run_capture", fake_run)
    result = domain.run_domain(
        {
            "action": "join",
            "domain": "corp.example.com",
            "username": "CORP\\svc-join",
            "password": "super-secret",
            "computer_name": "WS-01",
            "ou": "OU=Workstations,DC=corp,DC=example,DC=com",
        }
    )
    assert result["status"] == "succeeded"
    assert "super-secret" not in captured["input_text"]
    assert captured["env"]["SEXTANT_DOMAIN_PASSWORD"] == "super-secret"
    assert captured["env"]["SEXTANT_DOMAIN_OU"].startswith("OU=Workstations")


def test_linux_adcli_uses_stdin_password(monkeypatch):
    captured = {"calls": []}

    def fake_run(args, **kwargs):
        captured["calls"].append((args, kwargs))
        return {"status": "succeeded", "exit_code": 0, "stdout": "joined", "stderr": "", "error": ""}

    monkeypatch.setattr(domain.sys, "platform", "linux")
    monkeypatch.setattr(domain.shutil, "which", lambda name: {"adcli": "/usr/sbin/adcli", "systemctl": "/usr/bin/systemctl"}.get(name))
    monkeypatch.setattr(domain, "run_capture", fake_run)
    result = domain.run_domain(
        {
            "action": "join",
            "domain": "corp.example.com",
            "username": "svc-join@corp.example.com",
            "password": "super-secret",
            "method": "adcli",
            "server": "dc1.corp.example.com",
        }
    )
    assert result["status"] == "succeeded"
    join_call = captured["calls"][0]
    assert "--stdin-password" in join_call[0]
    assert "super-secret" not in " ".join(join_call[0])
    assert join_call[1]["input_text"] == "super-secret\n"
    assert any("--domain-controller=dc1.corp.example.com" == item for item in join_call[0])


def test_linux_prerequisite_install_respects_package_policy(monkeypatch):
    monkeypatch.setattr(domain.sys, "platform", "linux")
    result = domain.run_domain(
        {
            "action": "join",
            "domain": "corp.example.com",
            "username": "svc",
            "password": "secret",
            "install_prerequisites": True,
        },
        allow_packages=False,
    )
    assert result["status"] == "failed"
    assert "Package installation" in result["error"]
