from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from . import __version__
from .config import Settings
from .db import Database, loads, parse_iso, utcnow, utcnow_iso
from .views import _agent_alerts

logger = logging.getLogger("sextant_control.notifications")

SEVERITY_RANK = {"info": 1, "warning": 2, "critical": 3}
RETRY_SECONDS = (30, 120, 600, 3600, 21600)
MAX_ATTEMPTS = len(RETRY_SECONDS) + 1
MAX_MESSAGE_CHARS = 4000
TELEGRAM_RESPONSE_LIMIT = 64 * 1024


class NotificationError(Exception):
    """A delivery error safe to show to an operator."""


def _severity_at_least(value: str, minimum: str) -> bool:
    return SEVERITY_RANK.get(str(value).lower(), 0) >= SEVERITY_RANK.get(str(minimum).lower(), 2)


def _severity_icon(severity: str) -> str:
    return {"critical": "🔴", "warning": "🟠", "info": "🔵"}.get(str(severity).lower(), "🔵")


def _agent_url(settings: Settings, agent_id: str) -> str:
    if not settings.public_url:
        return ""
    return f"{settings.public_url.rstrip('/')}/#agent/{urllib.parse.quote(agent_id, safe='')}"


def _alert_map(row: sqlite3.Row) -> dict[str, dict[str, Any]]:
    disks = loads(row["disks"], [])
    gpus = loads(row["gpus"], [])
    services = loads(row["services"], [])
    if not isinstance(disks, list):
        disks = []
    if not isinstance(gpus, list):
        gpus = []
    if not isinstance(services, list):
        services = []
    alerts = _agent_alerts(disks, gpus, services, float(row["mem_pct"] or 0))
    return {str(alert["key"]): alert for alert in alerts}


def _utc_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class TelegramClient:
    """Minimal Telegram Bot API client using only the standard library."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        thread_id: str = "",
        api_base: str = "https://api.telegram.org",
        timeout: int = 10,
    ) -> None:
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.thread_id = thread_id.strip()
        self.api_base = api_base.rstrip("/")
        self.timeout = max(2, min(60, int(timeout)))

    @classmethod
    def from_settings(cls, settings: Settings) -> "TelegramClient | None":
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return None
        return cls(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            settings.telegram_thread_id,
            settings.telegram_api_base,
            settings.telegram_timeout_seconds,
        )

    def _url(self, method: str) -> str:
        # The token is part of the URL by Telegram's design. Never log this URL.
        return f"{self.api_base}/bot{self.token}/{method}"

    def send(
        self,
        title: str,
        body: str = "",
        severity: str = "info",
        url: str = "",
    ) -> None:
        text = f"{title}\n\n{body}".strip() if body else str(title).strip()
        payload: dict[str, str] = {
            "chat_id": self.chat_id,
            "text": text[:MAX_MESSAGE_CHARS],
            "disable_web_page_preview": "true",
        }
        if self.thread_id.isdigit():
            payload["message_thread_id"] = self.thread_id
        if str(severity).lower() == "info":
            payload["disable_notification"] = "true"
        if url.startswith(("https://", "http://")):
            payload["reply_markup"] = json.dumps(
                {"inline_keyboard": [[{"text": "Open Sextant", "url": url}]]},
                separators=(",", ":"),
            )
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url("sendMessage"),
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": f"sextant/{__version__}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = int(response.status)
                raw = response.read(TELEGRAM_RESPONSE_LIMIT)
        except urllib.error.HTTPError as exc:
            description = _telegram_description(exc)
            raise NotificationError(
                f"Telegram API HTTP {exc.code}: {description}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NotificationError(f"Telegram request failed: {reason}") from None

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise NotificationError(f"Telegram returned an invalid response (HTTP {status}).") from None
        if status >= 300 or not isinstance(parsed, dict) or not parsed.get("ok"):
            description = str(parsed.get("description") or f"HTTP {status}")[:300]
            raise NotificationError(f"Telegram rejected the message: {description}")

    def send_test(self) -> None:
        stamp = utcnow_iso()
        self.send(
            "🔵 Sextant test notification",
            f"Telegram notifications are configured correctly.\nServer time: {stamp}",
            severity="warning",
        )


def _telegram_description(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(TELEGRAM_RESPONSE_LIMIT)
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict) and parsed.get("description"):
            return str(parsed["description"])[:300]
    except (ValueError, UnicodeDecodeError, OSError, AttributeError):
        pass
    return str(exc.reason or "request failed")[:300]


class NotificationService:
    """Evaluate conditions and deliver Telegram notifications from an outbox."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        client: TelegramClient | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.client = client if client is not None else TelegramClient.from_settings(settings)
        self._task: asyncio.Task[None] | None = None
        self._last_tick_at = ""
        self._last_sent_at = ""
        self._last_error = ""
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.client is not None

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.notify_enabled
            and self.settings.telegram_enabled
            and self.configured
        )

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="sextant-notifications")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                logger.exception("Notification evaluation failed")
                self._set_error(f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(max(5, int(self.settings.notify_interval_seconds)))

    def tick(self, now: datetime | None = None) -> None:
        """Evaluate conditions and flush the outbox. Safe to call directly in tests."""
        if not self.configured or not self.settings.telegram_enabled:
            return
        current = now or utcnow()
        self._evaluate(current)
        self._deliver_due(current)
        with self._lock:
            self._last_tick_at = _utc_stamp(current)
        self.database.prune_notifications(self.settings.notification_retention_days)

    def send_test(self) -> None:
        if self.client is None:
            raise NotificationError("Telegram notifications are not configured.")
        self.client.send_test()
        with self._lock:
            self._last_sent_at = utcnow_iso()
            self._last_error = ""

    def status(self) -> dict[str, Any]:
        with self._lock:
            last_tick_at = self._last_tick_at
            last_sent_at = self._last_sent_at
            last_error = self._last_error
        chat_id = self.settings.telegram_chat_id
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "telegram": {
                "enabled": self.settings.telegram_enabled,
                "chat_id": ("***" + chat_id[-4:]) if chat_id else "",
                "thread_id": self.settings.telegram_thread_id,
                "events": list(self.settings.telegram_events),
                "min_severity": self.settings.telegram_min_severity,
                "recovery": self.settings.telegram_recovery,
            },
            "interval_seconds": self.settings.notify_interval_seconds,
            "last_tick_at": last_tick_at,
            "last_sent_at": last_sent_at,
            "last_error": last_error,
            "counts": self.database.notification_counts(),
        }

    # ---- evaluation -----------------------------------------------------

    def _evaluate(self, now: datetime) -> None:
        with self.database.connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name COLLATE NOCASE").fetchall()

        events = set(self.settings.telegram_events)
        for row in rows:
            alerts = _alert_map(row)
            states = self.database.reconcile_alert_states(
                row["id"],
                alerts,
                now=now,
                ack_ttl_hours=self.settings.alert_ack_ttl_hours,
                allow_clear=bool(row["last_seen"]),
            )
            last_seen = parse_iso(row["last_seen"])
            online = bool(
                last_seen
                and (now - last_seen).total_seconds() <= self.settings.offline_after_seconds
            )
            if "offline" in events:
                self._offline_transition(row, last_seen, online)
            if online:
                self._health_notifications(row, alerts, states, now, events)

    def _offline_transition(
        self,
        row: sqlite3.Row,
        last_seen: datetime | None,
        online: bool,
    ) -> None:
        if last_seen is None:
            return
        agent_id = str(row["id"])
        availability = self.database.get_availability(agent_id)
        if online:
            if availability is None or not availability["offline_incident_id"]:
                return
            incident_id = str(availability["offline_incident_id"])
            if self.settings.telegram_recovery:
                since = availability["offline_since"] or "unknown"
                self.database.enqueue_notification(
                    f"agent.online:{agent_id}:{incident_id}",
                    "offline",
                    "info",
                    agent_id,
                    f"🟢 {row['name']} is back online",
                    f"{row['name']} ({row['hostname']}) is reporting again.\n"
                    f"Offline since: {since}\nLast seen: {row['last_seen']}",
                    _agent_url(self.settings, agent_id),
                )
            self.database.clear_offline_availability(agent_id)
            return

        if availability is not None and availability["offline_incident_id"]:
            return
        incident_id = f"off_{int(last_seen.timestamp())}"
        self.database.enqueue_notification(
            f"agent.offline:{agent_id}:{incident_id}",
            "offline",
            "critical",
            agent_id,
            f"🔴 {row['name']} is offline",
            f"{row['name']} ({row['hostname']}) has not reported since {row['last_seen']}.\n"
            f"Last IP: {row['ip'] or 'unknown'}\n"
            f"Offline threshold: {self.settings.offline_after_seconds}s",
            _agent_url(self.settings, agent_id),
        )
        self.database.set_offline_availability(agent_id, incident_id, str(row["last_seen"]))

    def _health_notifications(
        self,
        row: sqlite3.Row,
        alerts: dict[str, dict[str, Any]],
        states: dict[str, sqlite3.Row],
        now: datetime,
        events: set[str],
    ) -> None:
        agent_id = str(row["id"])
        ack_ttl = timedelta(hours=max(1, min(8760, int(self.settings.alert_ack_ttl_hours))))
        for key, state in states.items():
            kind = key.split(":", 1)[0]
            if kind not in events:
                continue
            incident_id = str(state["incident_id"] or "")
            if not incident_id:
                continue
            if state["cleared_at"]:
                self._recovery_notification(row, key, kind, incident_id)
                continue
            alert = alerts.get(key)
            if alert is None:
                continue
            severity = str(alert.get("level") or "warning")
            if not _severity_at_least(severity, self.settings.telegram_min_severity):
                continue
            if self._suppressed(state, now, ack_ttl):
                continue
            event_key = f"alert.raised:{agent_id}:{incident_id}"
            if self.database.notification_exists(event_key):
                continue
            message = str(alert.get("message") or key)
            self.database.enqueue_notification(
                event_key,
                kind,
                severity,
                agent_id,
                f"{_severity_icon(severity)} {row['name']}: {message}",
                f"{message}\n"
                f"Agent: {row['name']} ({row['hostname']})\n"
                f"Severity: {severity}\n"
                f"First seen: {state['first_seen_at'] or 'unknown'}\n"
                f"Incident: {incident_id}",
                _agent_url(self.settings, agent_id),
            )

    def _recovery_notification(
        self,
        row: sqlite3.Row,
        key: str,
        kind: str,
        incident_id: str,
    ) -> None:
        if not self.settings.telegram_recovery:
            return
        agent_id = str(row["id"])
        raised = self.database.get_notification(f"alert.raised:{agent_id}:{incident_id}")
        if raised is None:
            return
        event_key = f"alert.cleared:{agent_id}:{incident_id}"
        if self.database.notification_exists(event_key):
            return
        self.database.enqueue_notification(
            event_key,
            kind,
            "info",
            agent_id,
            f"🟢 {row['name']}: recovered",
            f"{raised['title']}\n\nThe condition is no longer active.",
            _agent_url(self.settings, agent_id),
        )

    @staticmethod
    def _suppressed(state: sqlite3.Row, now: datetime, ack_ttl: timedelta) -> bool:
        acknowledged_at = parse_iso(state["acknowledged_at"])
        if acknowledged_at is not None and now - acknowledged_at < ack_ttl:
            return True
        snoozed_until = parse_iso(state["snoozed_until"])
        return bool(snoozed_until is not None and snoozed_until > now)

    # ---- delivery -------------------------------------------------------

    def _deliver_due(self, now: datetime) -> None:
        due = self.database.due_notifications(_utc_stamp(now), limit=10)
        for index, row in enumerate(due):
            if index:
                # Telegram allows roughly one message per second per chat.
                time.sleep(1.0)
            try:
                if self.client is None:
                    return
                self.client.send(
                    str(row["title"]),
                    str(row["body"]),
                    severity=str(row["severity"]),
                    url=str(row["url"]),
                )
            except NotificationError as exc:
                attempts = int(row["attempts"] or 0)
                next_attempt = _next_attempt(now, attempts)
                self.database.mark_notification_failed(int(row["id"]), str(exc), next_attempt)
                self._set_error(str(exc))
                logger.warning("Telegram delivery failed for %s: %s", row["event_key"], exc)
            else:
                self.database.mark_notification_sent(int(row["id"]))
                with self._lock:
                    self._last_sent_at = utcnow_iso()
                    self._last_error = ""

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message[:1000]


def _next_attempt(now: datetime, attempts: int) -> str | None:
    if attempts >= MAX_ATTEMPTS - 1:
        return None
    return _utc_stamp(now + timedelta(seconds=RETRY_SECONDS[attempts]))
