from __future__ import annotations

from fastapi.testclient import TestClient

ADMIN_PASSWORD = "correct horse battery staple"


def login(client: TestClient, username: str = "admin", password: str = ADMIN_PASSWORD, mode: str = "auto") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password, "mode": mode})
    assert response.status_code == 200, response.text
    data = response.json()
    return {
        "client": client,
        "csrf": data["csrf"],
        "user": data["user"],
        "settings": data["settings"],
        "permissions": data.get("permissions"),
    }


def headers(session: dict) -> dict[str, str]:
    return {"X-CSRF-Token": session["csrf"]}


def issue_token(session: dict, label: str = "test") -> str:
    response = session["client"].post(
        "/api/enrollment-tokens",
        json={"label": label, "expires_minutes": 30},
        headers=headers(session),
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def enroll_agent(
    session: dict,
    token: str | None = None,
    hostname: str = "test-box",
    machine_id: str = "",
    watch_services: list[str] | None = None,
    profile: str | None = None,
) -> dict:
    token = token or issue_token(session)
    body = {
        "enrollment_token": token,
        "hostname": hostname,
        "machine_id": machine_id,
        "os": "linux",
        "os_version": "Ubuntu 24.04",
        "arch": "x86_64",
        "ip": "192.0.2.10",
        "mac": "AA:BB:CC:DD:EE:FF",
        "agent_version": "0.2.0",
        "tags": ["lab"],
        "watch_services": watch_services or [],
    }
    if profile:
        body["profile"] = profile
    response = session["client"].post(
        "/api/agent/enroll",
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def sample_metrics(**overrides) -> dict:
    payload = {
        "hostname": "test-box",
        "os": "linux",
        "os_version": "Ubuntu 24.04",
        "arch": "x86_64",
        "ip": "192.0.2.10",
        "mac": "AA:BB:CC:DD:EE:FF",
        "agent_version": "0.2.0",
        "cpu_pct": 22.5,
        "mem_total": 16 * 1024**3,
        "mem_used": 6 * 1024**3,
        "uptime_seconds": 7200,
        "disks": [
            {"mount": "/", "total": 100 * 1024**3, "used": 95 * 1024**3, "free": 5 * 1024**3, "percent": 95, "fs": "ext4"}
        ],
        "gpus": [
            {"name": "NVIDIA RTX 4090", "util_pct": 97.0, "mem_used": 20 * 1024**3, "mem_total": 24 * 1024**3, "temp_c": 71.0}
        ],
        "services": [
            {"name": "sshd.service", "display_name": "OpenSSH", "status": "running", "startup": "enabled", "watched": True}
        ],
        "startup": [{"name": "nginx.service", "command": "systemctl enable nginx.service", "source": "systemd", "enabled": True}],
        "extra": {"cpu_model": "Test CPU", "kernel": "6.8"},
    }
    payload.update(overrides)
    return payload


def heartbeat(session: dict, agent: dict, payload: dict | None = None) -> dict:
    response = session["client"].post(
        f"/api/agent/{agent['agent_id']}/heartbeat",
        json=payload or sample_metrics(),
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
    )
    assert response.status_code == 200, response.text
    return response.json()
