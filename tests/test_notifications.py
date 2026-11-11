from __future__ import annotations

import io
import urllib.error
import urllib.parse
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from sextant_control.config import Settings
from sextant_control.notifications import (
    NotificationError,
    NotificationService,
    TelegramClient,
)
from sextant_control.server import create_app
from tests.helpers import (
    enroll_agent,
    headers,
    heartbeat,
    login,
    sample_metrics,
)


class FakeTelegram:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.sent: list[dict] = []
        self.attempts = 0

    def send(self, title: str, body: str = "", severity: str = "info", url: str = "") -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise NotificationError("simulated delivery failure")
        self.sent.append({"title": title, "body": body, "severity": severity, "url": url})

    def send_test(self) -> None:
        self.send("test", "test body", severity="warning")


def _metrics(disk_percent: float = 40, gpu_temp: float = 70, services: list[dict] | None = None) -> dict:
    return sample_metrics(
        disks=[
            {
                "mount": "/",
                "total": 100,
                "used": disk_percent,
                "free": 100 - disk_percent,
                "percent": disk_percent,
                "fs": "ext4",
            }
        ],
        gpus=[
            {
                "name": "Test GPU",
                "util_pct": 10,
                "mem_used": 1,
                "mem_total": 8,
                "temp_c": gpu_temp,
            }
        ],
        services=services
        if services is not None
        else [
            {
                "name": "sshd.service",
                "display_name": "OpenSSH",
                "status": "running",
                "startup": "enabled",
                "watched": True,
            }
        ],
    )


def _service(app, settings: Settings, client: FakeTelegram) -> NotificationService:
    return NotificationService(app.state.db, settings, client=client)


def test_telegram_is_not_configured_without_credentials(settings: Settings):
    assert TelegramClient.from_settings(settings) is None


def test_telegram_payload_uses_bot_api(monkeypatch):
    captured: dict = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return b'{"ok":true}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = TelegramClient(
        "123:secret-token",
        "-100123",
        thread_id="42",
        api_base="https://api.telegram.org",
        timeout=7,
    )
    client.send(
        "🔴 fileserver: disk full",
        "Disk / is 95% full",
        severity="critical",
        url="https://ops.example.com/#agent/agt_1",
    )
    assert captured["url"] == "https://api.telegram.org/bot123:secret-token/sendMessage"
    assert captured["timeout"] == 7
    fields = urllib.parse.parse_qs(captured["data"].decode())
    assert fields["chat_id"] == ["-100123"]
    assert fields["message_thread_id"] == ["42"]
    assert "Disk / is 95% full" in fields["text"][0]
    assert "disable_notification" not in fields
    assert "inline_keyboard" in fields["reply_markup"][0]


def test_telegram_error_redacts_token(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"ok":false,"description":"Unauthorized"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = TelegramClient("123:secret-token", "42")
    with pytest.raises(NotificationError) as excinfo:
        client.send("hello")
    assert "secret-token" not in str(excinfo.value)


def test_offline_and_recovery_are_deduplicated(client, admin, app, settings):
    agent = enroll_agent(admin)
    heartbeat(admin, agent, _metrics())
    fake = FakeTelegram()
    service = _service(app, settings, fake)

    future = service.database.get_availability(agent["agent_id"])
    assert future is None

    from sextant_control.db import utcnow

    later = utcnow() + timedelta(seconds=settings.offline_after_seconds + 5)
    service.tick(now=later)
    assert len(fake.sent) == 1
    assert "is offline" in fake.sent[0]["title"]
    assert fake.sent[0]["severity"] == "critical"

    service.tick(now=later)
    assert len(fake.sent) == 1

    heartbeat(admin, agent, _metrics())
    service.tick()
    assert len(fake.sent) == 2
    assert "back online" in fake.sent[1]["title"]
    assert fake.sent[1]["severity"] == "info"

    service.tick()
    assert len(fake.sent) == 2


def test_never_seen_agent_does_not_notify(client, admin, app, settings):
    enroll_agent(admin)
    fake = FakeTelegram()
    _service(app, settings, fake).tick()
    assert fake.sent == []


def test_disk_alert_fires_once_and_sends_recovery(client, admin, app, settings):
    agent = enroll_agent(admin)
    heartbeat(admin, agent, _metrics(disk_percent=95))
    fake = FakeTelegram()
    service = _service(app, settings, fake)

    service.tick()
    assert len(fake.sent) == 1
    assert "Disk / is 95% full" in fake.sent[0]["title"]
    assert fake.sent[0]["severity"] == "critical"

    service.tick()
    assert len(fake.sent) == 1

    heartbeat(admin, agent, _metrics(disk_percent=40))
    service.tick()
    assert len(fake.sent) == 2
    assert "recovered" in fake.sent[1]["title"]

    service.tick()
    assert len(fake.sent) == 2


def test_acknowledged_alert_is_not_notified(client, admin, app, settings):
    agent = enroll_agent(admin)
    heartbeat(admin, agent, _metrics(disk_percent=95))
    detail = client.get(f"/api/agents/{agent['agent_id']}").json()["agent"]
    key = next(alert["key"] for alert in detail["alerts"] if alert["key"].startswith("disk:"))

    response = client.post(
        f"/api/agents/{agent['agent_id']}/alerts/{key}/ack",
        headers=headers(admin),
    )
    assert response.status_code == 200, response.text

    fake = FakeTelegram()
    _service(app, settings, fake).tick()
    assert fake.sent == []


def test_min_severity_filters_warning_alerts(client, admin, app, settings):
    agent = enroll_agent(admin)
    heartbeat(admin, agent, _metrics(gpu_temp=86))
    fake = FakeTelegram()
    critical_only = replace(settings, telegram_min_severity="critical")
    _service(app, critical_only, fake).tick()
    assert fake.sent == []

    fake = FakeTelegram()
    _service(app, settings, fake).tick()
    assert len(fake.sent) == 1
    assert fake.sent[0]["severity"] == "warning"


def test_watched_service_alert(client, admin, app, settings):
    agent = enroll_agent(admin, watch_services=["sshd.service"])
    heartbeat(
        admin,
        agent,
        _metrics(
            services=[
                {
                    "name": "sshd.service",
                    "display_name": "OpenSSH",
                    "status": "failed",
                    "startup": "enabled",
                    "watched": True,
                }
            ]
        ),
    )
    fake = FakeTelegram()
    _service(app, settings, fake).tick()
    assert len(fake.sent) == 1
    assert "sshd.service" in fake.sent[0]["title"]
    assert fake.sent[0]["severity"] == "critical"


def test_outbox_deduplicates_across_service_restarts(client, admin, app, settings):
    agent = enroll_agent(admin)
    heartbeat(admin, agent, _metrics(disk_percent=95))
    first = FakeTelegram()
    _service(app, settings, first).tick()
    assert len(first.sent) == 1

    second = FakeTelegram()
    _service(app, settings, second).tick()
    assert second.sent == []


def test_failed_delivery_retries_with_backoff(client, admin, app, settings):
    agent = enroll_agent(admin)
    heartbeat(admin, agent, _metrics(disk_percent=95))
    fake = FakeTelegram(fail_times=1)
    service = _service(app, settings, fake)

    from sextant_control.db import utcnow

    now = utcnow()
    service.tick(now=now)
    assert fake.sent == []
    rows = app.state.db.list_notifications()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempts"] == 1
    assert rows[0]["next_attempt_at"]

    service.tick(now=now + timedelta(seconds=31))
    assert len(fake.sent) == 1
    rows = app.state.db.list_notifications()
    assert rows[0]["status"] == "sent"


def test_notification_status_and_test_endpoint_rbac(settings, tmp_path):
    configured = replace(
        settings,
        telegram_bot_token="123:token",
        telegram_chat_id="-100123456",
        notify_enabled=False,
    )
    app = create_app(configured)
    fake = FakeTelegram()
    app.state.notifications.client = fake

    with TestClient(app) as client:
        admin = login(client)
        status = client.get("/api/notifications/status", headers=headers(admin))
        assert status.status_code == 200
        body = status.json()
        assert body["configured"] is True
        assert body["telegram"]["chat_id"].endswith("3456")
        assert "token" not in str(body).lower()

        response = client.post("/api/notifications/test", headers=headers(admin))
        assert response.status_code == 200, response.text
        assert fake.sent

        viewer = client.post(
            "/api/users",
            json={
                "username": "viewer1",
                "password": "a long viewer password",
                "role": "viewer",
            },
            headers=headers(admin),
        )
        assert viewer.status_code == 200, viewer.text
        session = login(client, username="viewer1", password="a long viewer password")
        forbidden = client.post("/api/notifications/test", headers=headers(session))
        assert forbidden.status_code == 403


def test_test_endpoint_requires_configuration(client, admin):
    response = client.post("/api/notifications/test", headers=headers(admin))
    assert response.status_code == 400
    assert "not configured" in response.json()["detail"].lower()
