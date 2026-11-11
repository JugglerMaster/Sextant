# Notifications — plan

**Status:** Telegram MVP implemented; ntfy/webhook/email/push and the configurable rules UI are still proposed.
**Scope:** turn Sextant's existing alert conditions and state-change events into configurable outbound notifications: critical health events (disk, memory, offline, Docker, services), operational events (failed commands, updates), and security events (failed logins, enrollment).

Related: [Roadmap](ROADMAP.md), [Architecture](ARCHITECTURE.md), [UI status](UI.md), [Security](SECURITY.md), [Docker management](DOCKER_MANAGEMENT.md), [Discovery](DISCOVERY.md) (which already anticipates reusing a generic alert channel).

---

## 0. What is implemented today (Telegram MVP)

The server now runs a background notification evaluator when Telegram is configured. With no browser open it:

- reconciles disk, memory, GPU-temperature, and watched-service conditions for every agent;
- detects offline → online transitions using `SEXTANT_OFFLINE_AFTER_SECONDS`;
- sends one Telegram alert per incident, plus an optional recovery message;
- respects the existing alert acknowledgement, snooze, and acknowledgement-expiry behavior;
- retries failed deliveries with backoff and keeps an outbox in SQLite.

Configuration is environment-only for now (restart the server after changing it):

| Variable | Default | Purpose |
|---|---|---|
| `SEXTANT_NOTIFY_ENABLED` | `1` | Run the background evaluator |
| `SEXTANT_TELEGRAM_ENABLED` | `1` | Enable the Telegram channel |
| `SEXTANT_TELEGRAM_BOT_TOKEN` | empty | Bot token from @BotFather (secret) |
| `SEXTANT_TELEGRAM_CHAT_ID` | empty | Private chat, group, or channel id |
| `SEXTANT_TELEGRAM_THREAD_ID` | empty | Optional forum topic id |
| `SEXTANT_TELEGRAM_EVENTS` | `offline,disk,memory,gpu,service` | Comma-separated event kinds |
| `SEXTANT_TELEGRAM_MIN_SEVERITY` | `warning` | `info`, `warning`, or `critical` |
| `SEXTANT_TELEGRAM_RECOVERY` | `1` | Send recovery messages |
| `SEXTANT_NOTIFY_INTERVAL_SECONDS` | `20` | Evaluation cadence (minimum 5) |
| `SEXTANT_NOTIFICATION_RETENTION_DAYS` | `90` | Outbox retention |
| `SEXTANT_TELEGRAM_API_BASE` | `https://api.telegram.org` | Override for a local Bot API server |
| `SEXTANT_TELEGRAM_TIMEOUT_SECONDS` | `10` | Per-request timeout |

Verify it:

```bash
export SEXTANT_DATA_DIR=/var/lib/sextant \
       SEXTANT_TELEGRAM_BOT_TOKEN='123456:AA...' \
       SEXTANT_TELEGRAM_CHAT_ID='123456789'
sextant notify-test
sextant notify-log --limit 20
```

Admin API: `GET /api/notifications/status`, `GET /api/notifications/log`, `POST /api/notifications/test`, `POST /api/notifications/retry`. The token is never returned.

Not implemented yet: the Notifications settings page, editable rules/thresholds, quiet hours, per-tag/agent scoping, ntfy/webhook/email/push channels, Docker container events, and command/security events. The rest of this document is the design for those pieces.

---

## 1. The gap today

Sextant already detects a lot and records it well:

- Health conditions are computed from the latest heartbeat in `sextant_control/views.py` (`_agent_alerts`): disk ≥ 80/90%, memory ≥ 80/90%, GPU temperature ≥ 85 °C, and watched services that are not running.
- Conditions are reconciled into `alert_states` on every heartbeat and on dashboard/detail reads (`_reconcile_agent_alerts`, `Database.reconcile_alert_states`). That gives stable incident ids, first/last seen, acknowledgement, snooze, and recurring incidents.
- `agent_events` already records transitions: online, service, package, version, profile, and the alert lifecycle (`raised`, `cleared`, `acknowledged`, `reacknowledged`, `snoozed`, `acknowledgement_expired`).
- The dashboard has an alert center with acknowledge/snooze/clear, and the server update check has an admin login banner.

What is missing is anything that reaches a human **when the dashboard is closed**:

- Offline is derived only when someone renders an agent (`agent_to_dict` compares `last_seen`); there is no "went offline" transition and no server-side loop.
- Alert reconciliation mostly happens on heartbeat or dashboard read. If nobody opens the dashboard, no one evaluates the fleet.
- There is no webhook, chat, email, or mobile push. The roadmap names this as the next low-effort, high-value item.
- Docker is not monitored at all. "Docker crashed" cannot fire a notification yet.

The plan therefore has two halves: **a background evaluator** so detection does not depend on a browser, and **a configurable delivery layer** so each event can go to the channels an operator chooses.

---

## 2. Goals and non-goals

### Goals

- Detect and notify while no browser is open.
- Make the **event set**, **thresholds**, **scope**, **severity**, **quiet hours**, **cooldown/repeat**, and **channels** configurable in the UI.
- One notification per incident, not one per heartbeat. Recovery notifications where meaningful.
- Durable delivery with retries, backoff, a visible delivery log, and a test button.
- Zero new mandatory runtime dependencies for the MVP (`urllib`, `smtplib`, `zoneinfo` are stdlib).
- Keep the alert center and notifications consistent: the thresholds that notify are the thresholds shown in the UI.
- Do not leak secrets into payloads, logs, or the UI.

### Non-goals (v1)

- Guaranteed delivery or exactly-once semantics. This is at-least-once with per-event/per-channel deduplication.
- Replacing a real on-call stack (PagerDuty/Opsgenie). Webhooks can bridge to one.
- Paging by SMS.
- Notifying when the Sextant server itself is completely down (it cannot send while dead — see the watchdog in §6.7).
- Multi-node delivery coordination beyond a single-writer lease.
- User-level notification preferences. Channels and rules are server-wide in v1; per-user email/push subscriptions are future work.

---

## 3. What could be notified, ranked by value and effort

Effort is relative to this codebase: **S** ≈ half a day to a day, **M** ≈ 1–3 days, **L** ≈ a week or more.

| # | Event | Trigger | Default severity | Value | Effort | Notes |
|---|---|---|---|---|---|---|
| 1 | **Agent offline / back online** | `last_seen` older than threshold; recovery on heartbeat | critical | very high | S | Needs the background loop. The single most valuable notification. |
| 2 | **Disk full / low free space** | existing disk conditions, plus a free-GB condition | critical at 90% | very high | S | Already computed; just needs delivery. |
| 3 | **Memory pressure** | existing memory condition | warning/critical | high | S | Already computed. |
| 4 | **Watched service stopped** | existing watched-service condition | critical | high | S | Already computed; covers systemd/Windows services. |
| 5 | **Docker container down / unhealthy / restarting** | Docker inventory + condition | critical | high | S–M | Not monitored today. Directly addresses "docker crashed". Depends on the [Docker management plan](DOCKER_MANAGEMENT.md). |
| 6 | **Command failed / timed out / went stale** | command result, dispatcher sweeper | warning | high | S | Especially useful for power, service, and package jobs run from the UI. |
| 7 | **GPU temperature high** | existing ≥ 85 °C condition | warning | medium | S | Already computed; noisy on laptops, so default off or higher threshold. |
| 8 | **Failed-login burst / lockout** | audit-log window (`N` failures in `M` minutes) | warning | high | S–M | Security value; must be rate-limited to avoid storms. |
| 9 | **New agent enrolled / token rotated / user or role changed** | audit hooks | info | medium | S | Informational; high value on an untrusted network. |
| 10 | **Agent self-update failed / restart required** | self-update result, heartbeat state | warning | medium | S | Hook the existing result/notice path. |
| 11 | **Package sync failed / drift above threshold** | package command result / inventory | warning | medium | S–M | Keep opt-in; can be noisy. |
| 12 | **Server update available** | existing read-only update check | info | low–medium | S | Already exists as an in-app banner; route it to a channel/daily digest. |
| 13 | **Certificate expiring** | new server-side TLS probe per target | critical at 7 days | high | M | Not collected today. Needs a host/port watch list. |
| 14 | **Sextant server unreachable** | external dead-man's switch or external monitor | critical | very high | S (config) / M (built-in ping) | Cannot be sent by the server itself. See §6.7. |
| 15 | **Daily / weekly digest** | scheduled summary | info | medium | M | Good for non-urgent updates and noisy environments. |
| 16 | **Telegram bot push** | channel | — | high | S | Best if you already use Telegram; bot token + chat id, no new app. |
| 17 | **Browser/PWA Web Push** | channel, not event | — | high | M–L | Nice UX, but VAPID + service worker + subscription storage. Defer. |
| 18 | **SMS / on-call escalation** | channel | — | medium | L | Cost, provider accounts, and secret handling. Webhook to a provider first. |

### 3.1 The short answer: best value and easiest

**If you already use Telegram, start there.** It needs no new mobile app: create a bot, get a chat id, store the bot token in Sextant, and you are done. ntfy remains the recommendation when you want self-hosted delivery, no third-party account, or better content privacy.

Build these first, in this order:

1. **A notification service with Telegram and/or ntfy, plus a generic JSON webhook (and Slack/Discord presets on the same transport).** One outbound HTTP POST covers mobile push, chat, and automation (Home Assistant, n8n, Node-RED, Uptime Kuma). This is the cheapest path to "my phone buzzes".
2. **Offline/online, disk, memory, and watched service events.** The conditions already exist. The new work is the background evaluator, deduplication, and delivery.
3. **Failed commands.** A tiny hook at the result endpoint and the stale-command sweeper gives out-of-band visibility into async work.
4. **Docker containers.** The [Docker management plan](DOCKER_MANAGEMENT.md) supplies the inventory; notifications only need a small monitor rule on top.
5. **Email/SMTP second, not first.** It is the most universal fallback, but it is more config (host, TLS, sender, recipients) and has deliverability caveats. Stdlib `smtplib` keeps it dependency-free.
6. **Browser Web Push last.** Highest convenience, but by far the most moving parts for this app.

For the specific events you named:

- **Disk full** — already detected, so it ships in the MVP.
- **Server down** — agent offline ships in the MVP; the Sextant server being down needs the external watchdog in §6.7.
- **Docker crashed** — depends on the Docker inventory; the notification rule itself is small and lands in Phase 3.

---

## 4. Architecture

```text
 heartbeat / API handlers / periodic evaluator
                    │
                    ▼
        condition evaluator (shared)
        thresholds + scope + duration
                    │ transitions
                    ▼
        notification_events  (dedupe_key UNIQUE)
                    │ routing rules
                    ▼
        notification_deliveries (pending, attempts)
                    │ background worker
                    ▼
   ntfy  ·  Telegram  ·  webhook  ·  Slack/Discord  ·  SMTP  ·  (push later)
```

### 4.1 The background evaluator

`create_app` currently has no lifespan task. Add one:

- New module `sextant_control/notifications.py` with a `NotificationService`.
- Start it from a FastAPI lifespan handler on startup, cancel it on shutdown.
- Tick every `SEXTANT_NOTIFY_EVALUATE_SECONDS` (default 20–30 s; must be shorter than the offline threshold).
- Each tick:
  1. Load all agents (bounded; hundreds are fine at this cadence).
  2. Evaluate health conditions for every agent using the same logic as the dashboard.
  3. Reconcile `alert_states` (so incidents advance and clear even with no dashboard open).
  4. Detect offline/online transitions.
  5. Drain due deliveries.
  6. Prune old events/deliveries.

Two safety details:

- **Single writer.** Uvicorn is expected to run one worker. Still, add a lightweight SQLite lease row (`notification_runtime.owner`, `lease_until`) so a second process does not double-send. Refresh the lease each tick; only the lease holder evaluates and delivers.
- **Tests.** Make the loop opt-in via settings (`notifications_enabled=False` in test fixtures) and test the evaluator by calling `service.tick(now=...)` directly, deterministically. Do not rely on sleeping timers.

### 4.2 Conditions and transitions

Refactor the hardcoded condition computation out of `views.py` into a small shared module (for example `sextant_control/conditions.py`):

- Inputs: the agent row, `now`, and the resolved monitor rules.
- Output: a list of conditions with a stable `key`, `kind`, `level`, `message`, and structured `value`/`threshold`.
- `views.py` uses it for the alert center and cards.
- The evaluator uses it for reconciliation and notifications.

This preserves the current behavior while making thresholds configurable in one place. Existing keys (`disk:…`, `memory`, `gpu:<n>`, `service:<name>`) stay stable. Add new keys for `agent:offline` and `container:<name>`.

Stateful conditions keep using `alert_states` (incident id, first/last seen, ack, snooze). A condition that opens or recurs produces `alert.raised`; one that disappears produces `alert.cleared`. That is exactly the existing lifecycle, so acknowledgements and snoozes automatically have a meaning for notifications too.

Discrete events (command failed, failed-login burst, new enrollment) do not need an incident state. They produce an event with a dedupe key such as `command.failed:<command_id>` or `security.failed_logins:<window_start>`.

### 4.3 Events, routing, and delivery

Three stages, all in SQLite:

1. **Produce** a `notification_events` row with a unique `dedupe_key` (for example `alert.raised:<agent_id>:<incident_id>` and `alert.cleared:<agent_id>:<incident_id>`). A duplicate produce is a no-op.
2. **Route** the event against enabled rules to create one `notification_deliveries` row per matching channel, with `UNIQUE(event_id, channel_id)`.
3. **Deliver** pending rows oldest-first with bounded concurrency (about 4), per-request timeouts (10 s), no redirects, response-size caps, and exponential backoff (`30s, 2m, 10m, 1h, 6h`, then `failed`).

Stages 1–2 can happen in the request/heartbeat transaction. Stage 3 is the worker. The UI gets a **delivery log** with status, attempts, last error (redacted), and a manual retry.

### 4.4 Deduplication and anti-noise

This is where notification systems usually fail. Make it explicit and configurable:

- **One per incident, by default.** `repeat_minutes=0` means notify once when the incident opens and once when it clears. A recurrence is a new incident and notifies again.
- **Optional repeat while active.** `repeat_minutes > 0` re-sends at that cadence until acknowledged, snoozed, or cleared.
- **Acknowledgement/snooze suppression.** Before delivery, skip if the incident is acknowledged (unless `notify_acknowledged=true`) or snoozed. When an acknowledgement expires, the existing `acknowledgement_expired` event can notify again.
- **Duration gate.** `for_seconds` requires the condition to persist across evaluations before notifying (default 0 for disk/memory, and the offline threshold is itself the duration). This kills transient spikes.
- **Cooldown.** A per-rule/per-channel minimum gap, plus a global rate limit (for example 30 messages/minute) and per-channel token bucket. Excess is delayed, not dropped.
- **Quiet hours.** IANA timezone via stdlib `zoneinfo`, start/end, weekday mask, and a `critical_bypass` flag. Default: no quiet hours.
- **Maintenance/mute.** A simple per-agent mute ("don't notify until…") in v1; tag- or window-based maintenance mode can reuse the roadmap's maintenance-window work later.
- **Flap handling.** The incident model already makes a resolve→recur a fresh incident. Optional `flap_window_minutes` can suppress reporting a recovered condition that reopens within the window, while still recording it in the alert center.
- **Grouping (later).** For "12 agents are above 90% disk", a digest/grouped mode is Phase 4.

### 4.5 Restart and delivery semantics

- On startup, pending deliveries resume. Events that were produced but not delivered are not lost.
- The dedupe key prevents a restart from re-notifying an incident that was already sent.
- Delivery is at-least-once: a rare duplicate is possible if the process dies after the remote endpoint accepted a request but before the `sent` commit. Include `event_id` in the payload so receivers can dedupe.
- Delivery failures never break a heartbeat, a command result, or a dashboard read. Producer writes are best-effort and bounded, like `agent_events`.

---

## 5. Configuration model

"Fully configurable" means three layers, kept separate in the UI and schema:

1. **Monitor rules** — what to detect and at what threshold.
2. **Channels** — where to send it.
3. **Routing on the rule** — severity floor, quiet hours, repeat, recovery, and which channels.

A separate route table is only needed later if the same condition must notify differently by scope; v1 stores the routing fields on the monitor rule.

### 5.1 Monitor rules — what to detect

Each rule defines one condition type and its parameters:

| Field | Meaning |
|---|---|
| `kind` | `offline`, `disk`, `memory`, `gpu_temp`, `service`, `container`, `command`, `security`, `agent_update`, `package`, `certificate`, `server_update`, `digest` |
| `enabled` | Turn this condition on/off |
| `scope` | `all`, `agent_ids`, `tags`, `profiles` (most specific wins) |
| `params` | Type-specific thresholds and durations |
| `severity` | `info` / `warning` / `critical` (can be computed, e.g. disk warning vs critical) |

Defaults are seeded from today's hardcoded values so an upgrade changes nothing until the operator edits a rule. The alert center reads the same resolved rules, so the UI and notifications cannot disagree.

Scope precedence: **agent override → tag override → profile override → global default**. The UI shows which rule won for a given agent.

### 5.2 Channels — where to send

| Field | Meaning |
|---|---|
| `kind` | `ntfy`, `telegram`, `webhook`, `slack`, `discord`, `smtp` (future: `push`) |
| `name` | Human label |
| `config` | Type-specific target and options |
| `enabled` | Pause without deleting |
| `min_severity` | Channel-level floor |
| `is_default` | Used when a rule does not name channels |
| `secret_ref` / write-only fields | Tokens/passwords are never returned by the API |

### 5.3 Routing — which events go where

Routing is stored on the monitor rule: channel multi-select plus delivery policy. If a rule has no channels, it goes to the default channel(s). If no channel is configured, events are still recorded in the notification history but nothing is sent. This makes the feature safe to enable before setup.

Per-rule delivery policy:

- minimum severity,
- quiet hours,
- repeat/cooldown,
- recovery notifications on/off,
- notify-on-acknowledged on/off.

### 5.4 Global settings

| Setting | Default | Purpose |
|---|---|---|
| `SEXTANT_NOTIFY_ENABLED` | `1` | Run the evaluator/delivery loop |
| `SEXTANT_NOTIFY_EVALUATE_SECONDS` | `20` | Evaluation cadence |
| `SEXTANT_NOTIFY_RETENTION_DAYS` | `90` | Events/deliveries retention |
| `SEXTANT_NOTIFY_MAX_ATTEMPTS` | `5` | Delivery attempts |
| `SEXTANT_NOTIFY_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `SEXTANT_NOTIFY_ALLOW_PRIVATE` | `0` | Allow private/loopback webhook targets (needed for self-hosted ntfy/HA) |
| `SEXTANT_NOTIFY_RATE_LIMIT` | `30` | Global messages/minute |
| `SEXTANT_HEARTBEAT_PING_URL` | empty | Dead-man's-switch ping (see §6.7) |

Env values bootstrap the defaults. Once a channel/rule exists in the database, the database is the source of truth (the UI shows when an env default is shadowed). Secrets are better supplied by env for immutable deployments: `SEXTANT_TELEGRAM_BOT_TOKEN`, `SEXTANT_NOTIFY_<NAME>_TOKEN`, `SEXTANT_SMTP_PASSWORD`, etc.

Example rule set after installation:

```text
Offline        all agents        critical  after 180 s   recovery on   → ntfy, email
Disk           tag:servers       critical  >= 90%        repeat 0      → ntfy
Disk           tag:servers       warning   >= 80%        repeat 1440   → email
Memory         all agents        critical  >= 95%        repeat 60     → ntfy
Docker         all agents        critical  any watched container not running/unhealthy → ntfy
Command        all agents        warning   failed/timed out → ntfy
Security       all agents        warning   10 failed logins / 10 min → email
```

---

## 6. Channels in detail

### 6.1 Telegram (best if you already use it)

Telegram is a very good first channel when you already have the app: no new mobile software, excellent push delivery, rich messages, and a tap-through button. Sextant calls the Telegram Bot API over outbound HTTPS; no inbound port or webhook is needed.

```text
Sextant server ──HTTPS POST──▶ api.telegram.org ──Telegram push──▶ your phone
   (bot client)                  (Bot API)                         (Telegram app)
```

**Setup**

1. In Telegram, message [@BotFather](https://t.me/BotFather), run `/newbot`, choose a name and a username ending in `bot`, and copy the HTTP API token (`123456789:AA...`). Use a dedicated bot for Sextant.
2. Open a chat with the new bot and press **Start** (`/start`). A bot cannot initiate a conversation with a user who has never messaged it.
3. Get the chat id. The simplest way is to send the bot a message and call:

   ```bash
   curl -sS "https://api.telegram.org/bot<TOKEN>/getUpdates"
   ```

   Read `message.chat.id`. For a group, add the bot to the group, send a message, and use the negative id (`-100...`). For a forum topic, also record `message_thread_id`.
4. Store the bot token as a secret and the chat id with the channel.
5. Send a test from the Notifications page. Sextant does roughly this:

   ```bash
   curl -sS "https://api.telegram.org/bot<TOKEN>/sendMessage" \
     -d chat_id=123456789 \
     -d disable_web_page_preview=true \
     --data-urlencode "text=🔴 fileserver-01: disk / is 94% full" \
     --data-urlencode 'reply_markup={"inline_keyboard":[[{"text":"Open Sextant","url":"https://ops.example.com/#agent/agt_..."}]]}'
   ```

**Implementation notes**

- Build the URL from the bot token, `POST` form fields, and never log the token. The Bot API returns `{"ok": false, "description": ...}` with HTTP 4xx; surface the description in the delivery log after redacting the token.
- `parse_mode` is optional. Use HTML with escaped `&`, `<`, `>` for bold/`<code>`; plain text is safest. The message cap is 4096 characters, so truncate.
- An inline-keyboard URL button gives a clean tap-through to the agent or alert center.
- Telegram has no ntfy-style priority levels. Use `disable_notification: true` for `info`, and normal delivery for `warning`/`critical`; the phone's own DND settings still apply.
- Rate limits are roughly **1 message/second per chat** and **30 messages/second per bot**. A disk incident across 20 agents must be coalesced or queued by the notification worker, not sent as 20 parallel requests.
- For a private chat only you receive it; for a group, everyone in the group does. A channel also works, with the bot added as an admin.
- Content transits Telegram's cloud. Bot messages are **not** end-to-end encrypted. If alert content is sensitive, use self-hosted ntfy or a generic webhook to your own endpoint instead.
- Revoking the token in BotFather immediately disables this channel. Changing the chat id (or removing the bot from a group) also stops delivery.
- Telegram can be blocked on some networks. The **Sextant server**, not your phone, must be able to reach `api.telegram.org`; test with the channel's test button.

**Telegram vs ntfy:** Telegram wins on setup and UX when you already use it. ntfy wins on self-hosting, no third-party account, and keeping content on your own infrastructure. They are complementary: Telegram for you, ntfy/webhook for a self-hosted audit path or a team.

### 6.2 ntfy (recommended for self-hosted/privacy)

**How it reaches the phone:** ntfy is topic-based pub/sub, not a direct connection from Sextant to the phone.

```text
Sextant server ──HTTPS POST──▶ ntfy server ──push/connection──▶ ntfy phone app
   (publisher)                  (broker)                        (subscriber)
```

1. Install the ntfy app (Android: Google Play or F-Droid; iOS: App Store) and subscribe to a topic such as `sextant-7f3k9q2m`.
2. Sextant `POST`s the alert to `https://ntfy.sh/<topic>` (or a self-hosted ntfy URL).
3. The ntfy server fans the message out to every subscriber. On Android with the Google Play build and ntfy.sh, that is Firebase Cloud Messaging (FCM); on iOS it is APNs via ntfy.sh. A self-hosted server has no FCM, so the Android app keeps a persistent connection (instant delivery) instead.

Practical notes:

- `POST` a JSON body or use ntfy headers (`Title`, `Priority`, `Tags`, `Click`); optional bearer token.
- Map severity to ntfy priority: `info`→`low`, `warning`→`high`, `critical`→`urgent`; set tags (`warning`, `rotating_light`) and `click` to the agent URL.
- Topic names are effectively secrets on the public server. Generate a random topic, recommend self-hosting, never log the full URL.
- Self-hosted iOS has one wrinkle: set `upstream-base-url: https://ntfy.sh` and make `base-url` match the server URL configured in the iOS app, because iOS push still goes through ntfy.sh. Android can use the F-Droid build or UnifiedPush to avoid FCM.
- The phone must be able to reach the ntfy server. If Sextant itself is VPN-only, push still arrives (the phone talks to ntfy), but the tap-through link needs VPN access to the console.
- ntfy is best-effort and not end-to-end encrypted; the broker can read messages. Use a self-hosted server for sensitive content, and keep email/webhook as a second channel for critical alerts.
- Mobile push works without the Sextant app being open. This is the highest value-per-line-of-code in the whole plan.

### 6.3 Generic JSON webhook

A generic webhook is the "bring your own integration" channel: Sextant `POST`s a JSON document to a URL you control whenever a matching event fires. The receiver decides what to do with it.

```text
Sextant ──HTTPS POST JSON──▶ your endpoint (n8n, Home Assistant, FastAPI, Node-RED, ...)
   │                              │
   │  retry on non-2xx            └─▶ SMS, smart plug, ticket, log, another chat, ...
   ▼
delivery log
```

**Request shape**

```http
POST /hooks/sextant HTTP/1.1
Content-Type: application/json
User-Agent: sextant/0.4.0
X-Sextant-Event: evt_01J...
X-Sextant-Timestamp: 1762886530
X-Sextant-Signature: sha256=5f2c...
```

The body is the payload in Appendix A: `event`, `id`, `at`, `severity`, `recovery`, `incident_id`, `agent`, `condition`, `title`, `text`, and `url`. The `id` is stable across retries, so the receiver can deduplicate.

**Signature verification**

`X-Sextant-Signature` is `sha256=` + HMAC-SHA256 of `"{timestamp}.{raw_body}"` using the channel's secret. Verify the raw bytes before parsing JSON, compare with `hmac.compare_digest`, and reject timestamps older than a few minutes to prevent replay.

```python
import hashlib, hmac, time
from fastapi import FastAPI, HTTPException, Request

SECRET = b"the same secret configured in Sextant"
app = FastAPI()

@app.post("/hooks/sextant")
async def receive(request: Request):
    raw = await request.body()
    ts = request.headers.get("X-Sextant-Timestamp", "")
    signature = request.headers.get("X-Sextant-Signature", "")
    expected = "sha256=" + hmac.new(
        SECRET, f"{ts}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "bad signature")
    if abs(time.time() - int(ts)) > 300:
        raise HTTPException(401, "stale request")

    event = await request.json()
    if event["severity"] == "critical":
        ...  # send an SMS, toggle a smart plug, open a ticket, etc.
    return {"ok": True}
```

**Receiver behavior**

- Return any `2xx` within about 10 seconds; the response body is ignored.
- A non-2xx, timeout, or connection error is retried with exponential backoff (`30s, 2m, 10m, 1h, 6h`, default 5 attempts), then marked `failed` in the delivery log.
- Treat delivery as at-least-once and deduplicate on `event.id` if the action is not idempotent.
- Keep secrets out of query strings where possible; Sextant also supports a custom `Authorization` header instead of (or in addition to) the HMAC secret.

**Typical uses**

- A tiny bridge from Sextant to Twilio or another SMS/voice provider.
- Home Assistant automations: flash a light, announce on a speaker, or trigger a siren.
- n8n/Node-RED: enrich the alert, create a ticket, or fan it out to several services.
- A second, independent logging or audit endpoint.
- Anything provider-specific that Sextant does not ship as a native channel.

Because the receiver is yours, the content leaves Sextant but does not have to touch a third-party notification cloud. For self-hosted targets, set `SEXTANT_NOTIFY_ALLOW_PRIVATE=1`; Sextant still blocks link-local/metadata addresses and does not follow redirects.

### 6.4 Slack / Discord-compatible webhook

- Presets over the same HTTP transport: Slack `{"text": "..."}`, Discord `{"content": "..."}`.
- Low effort because the transport and retry/backoff are shared.
- Good for a shared ops channel; not a pager.

### 6.5 Email / SMTP

- Stdlib `smtplib` + `email.message.EmailMessage`; STARTTLS and certificate verification by default.
- Config: host, port, username, password, from address, recipients, subject prefix.
- Keep it synchronous in a worker thread with a timeout; never block the event loop.
- Caveats: deliverability, blocked port 25, and the need for an app password. It is the best fallback when phones are not set up, but it is not the easiest first channel.

### 6.6 Browser / PWA Web Push (later)

- Requires HTTPS, a service worker, VAPID keys, subscription storage, and encryption (typically `pywebpush`/`cryptography`, which would be the first new runtime dependency worth adding).
- High convenience for an operator already using the dashboard, but it does not help if the dashboard is never opened or the browser is closed on mobile.
- Defer until ntfy/webhook/email have proven insufficient.

### 6.7 Server-down watchdog (special case)

A server cannot notify about its own death. Cover it in two ways:

1. **External monitor (recommended, zero code):** point Uptime Kuma, Healthchecks.io, or any monitor at `GET /api/health` and have it alert on failure.
2. **Dead-man's switch (small code):** the notification loop pings a configured URL every 60 s (`SEXTANT_HEARTBEAT_PING_URL`). The external service alerts when the pings stop. This catches a hung process even if the host is up, and it requires no inbound port.

Do not try to have agents send "server unreachable" messages with channel credentials: it distributes secrets to every endpoint for little benefit. Document the external watchdog instead.

---

## 7. New condition coverage

### 7.1 Offline / online

- Compute from `last_seen` against the resolved offline threshold (default remains `SEXTANT_OFFLINE_AFTER_SECONDS=90`).
- Treat it as a first-class condition (`agent:offline`) reconciled in the same loop, so it gets an incident id, ack/snooze, and recovery.
- Decide explicitly whether the alert center should show "offline" rows. Recommendation: yes, with `alert_count` still counting it, because it is the most important condition. If that is too invasive, keep offline notification state separate but reuse the same event schema.
- Do not evaluate telemetry conditions repeatedly while an agent is offline; the last sample is stale. Keep the last known conditions visible but do not generate new transitions from them.

### 7.2 Docker containers

Docker inventory is already specified by the [Docker management plan](DOCKER_MANAGEMENT.md). Do not build a second collector: consume its `docker_hosts` / `docker_containers` tables and `docker.*` agent events.

- The Docker plan uploads a bounded full inventory (container name, image, state, status, health, restart count) and records state changes as `docker.*` events in `agent_events`.
- Add a **Docker container** monitor rule that reads the current `docker_containers` snapshot and alerts when a **watched** container is not running, is `restarting`, or is `unhealthy`.
- Do not alert on every container by default. Add a per-agent `watch_containers` list (like `watch_services`), with an "all containers" option and a per-container mute.
- If Docker lands after notifications, the MVP can ship without it; add the rule when the Docker inventory exists. This keeps the two plans independent.
- Windows containers: out of scope, matching the Docker plan.

### 7.3 Other collectors

- **Certificate expiry:** a server-side TLS probe to a configured list of `host:port` targets, daily, with warning/critical day thresholds. This is a server concern, not an agent concern.
- **Disk free-GB:** add to the disk condition so a small partition cannot hide behind a percentage.
- **Failed-login burst:** query `audit_log` in a window; dedupe by window bucket.
- **Command failures:** hook `agent_command_result` and `sweep_stale_commands`; include kind, agent, exit code, and a link, but **not** full stdout/stderr (bounded preview only, and never for commands marked sensitive).
- **Agent update failure/restart required:** hook the self-update result and the `agent_update` notice.

---

## 8. Data model

All additive; no destructive migration. Follow the existing `db.py` `_migrate` pattern.

```sql
CREATE TABLE IF NOT EXISTS notification_channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('ntfy','telegram','webhook','slack','discord','smtp')),
    config TEXT NOT NULL DEFAULT '{}',          -- target + options; secret fields masked
    enabled INTEGER NOT NULL DEFAULT 1,
    min_severity TEXT NOT NULL DEFAULT 'warning',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT                             -- soft delete keeps delivery history
);

CREATE TABLE IF NOT EXISTS notification_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                          -- condition type
    enabled INTEGER NOT NULL DEFAULT 1,
    scope TEXT NOT NULL DEFAULT '{}',            -- {mode, agent_ids, tags, profiles}
    params TEXT NOT NULL DEFAULT '{}',           -- thresholds, for_seconds, etc.
    min_severity TEXT NOT NULL DEFAULT 'warning',
    channel_ids TEXT NOT NULL DEFAULT '[]',
    quiet_hours TEXT NOT NULL DEFAULT '{}',
    repeat_minutes INTEGER NOT NULL DEFAULT 0,
    cooldown_minutes INTEGER NOT NULL DEFAULT 0,
    notify_recovery INTEGER NOT NULL DEFAULT 1,
    notify_acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_events (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    event TEXT NOT NULL,                         -- alert.raised, agent.offline, command.failed, ...
    severity TEXT NOT NULL,
    agent_id TEXT,
    incident_id TEXT,
    dedupe_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES notification_events(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL REFERENCES notification_channels(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sending','sent','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    sent_at TEXT,
    response_code INTEGER,
    error TEXT NOT NULL DEFAULT '',
    UNIQUE(event_id, channel_id)
);

CREATE TABLE IF NOT EXISTS notification_runtime (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    owner TEXT NOT NULL DEFAULT '',
    lease_until TEXT
);
```

Notes:

- `dedupe_key` is the idempotency backbone. Examples: `alert.raised:<agent>:<incident>`, `alert.cleared:<agent>:<incident>`, `agent.offline:<agent>:<offline_since>`, `command.failed:<command_id>`, `security.logins:<window>`.
- Indexes on `notification_events(at)`, `notification_deliveries(status, next_attempt_at)`, and `notification_deliveries(channel_id)`.
- Prune events and deliveries after `SEXTANT_NOTIFY_RETENTION_DAYS` (90 default).
- The DB now contains channel secrets. The data directory is already created `0700`; also set the SQLite file to `0600` and document encrypted/restricted backups.

---

## 9. API

Admin-only for configuration; admin/operator for read-only history. CSRF applies to mutations as usual.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/notifications/status` | Loop state, lease, pending/failed counts, last tick |
| `GET` | `/api/notifications/channels` | List channel summaries (secrets masked) |
| `POST` | `/api/notifications/channels` | Create a channel |
| `PATCH` | `/api/notifications/channels/{id}` | Update/enable/disable |
| `DELETE` | `/api/notifications/channels/{id}` | Delete |
| `POST` | `/api/notifications/channels/{id}/test` | Send a synthetic test event |
| `GET` | `/api/notifications/rules` | List monitor/route rules |
| `POST` | `/api/notifications/rules` | Create a rule |
| `PATCH` | `/api/notifications/rules/{id}` | Update a rule |
| `DELETE` | `/api/notifications/rules/{id}` | Delete a rule |
| `GET` | `/api/notifications/events` | Recent notification events (agent/kind window filters) |
| `GET` | `/api/notifications/deliveries` | Delivery log with status filters |
| `POST` | `/api/notifications/deliveries/{id}/retry` | Retry a failed delivery |

Keep the existing alert endpoints unchanged. Acknowledgement/snooze actions automatically affect delivery suppression through `alert_states`; optionally add a `notify` flag to the ack payload later. Deleting a channel archives it (`archived_at`) so the delivery history stays readable.

---

## 10. UI

A new admin **Notifications** page (added to the nav only for admins):

- **Status banner:** enabled/disabled, last evaluation, pending/failed counts, lease owner.
- **Channels tab:** list, add/edit, enable/disable, **Send test**, masked secrets, per-channel min severity. Show a clear "not configured yet" empty state.
- **Rules tab:** one row per condition, with an enable toggle, threshold fields, scope picker (all / tags / agents / profiles), severity, repeat/cooldown, recovery toggle, quiet hours, and channel multi-select. Render type-specific fields from the rule kind.
- **History tab:** recent events and deliveries, status chips, attempt counts, redacted errors, manual retry, filters by agent/severity/status.
- **Alert center integration:** show a small bell/"notified" marker on incidents and a delivery status detail; a snooze explains that notifications are suppressed until the snooze ends.
- **Agent drawer:** a "Mute notifications until…" control and a Containers section (with watch toggles) once Docker lands.
- **Test product loop:** "Add ntfy → Send test → enable Offline rule" should take under two minutes, with inline validation.

All new pages must pass the existing CSP, keyboard/focus, and axe tests. No inline styles or scripts.

---

## 11. Security

- Configuration is **admin-only**; every change is audited (channel create/update/delete/test, rule changes). Never audit secret values.
- Channel secrets are write-only in the API: list/get returns `configured: true` and a redacted hint, never the full URL/token/password. A Telegram bot token is a full-control credential for that bot; use a dedicated bot and revoke it in BotFather if it leaks.
- Delivery logs redact credentials and query strings; error messages are truncated.
- Require `https://` by default. Allow `http://` only to private/loopback targets with `SEXTANT_NOTIFY_ALLOW_PRIVATE=1` (self-hosted ntfy/Home Assistant). Always block link-local/metadata addresses (`169.254.0.0/16`, `fd00:ec2::254`) to avoid cloud metadata SSRF.
- No redirects, 10 s timeout, response cap (64 KiB), bounded concurrency, per-channel rate limit. The test endpoint is rate-limited too.
- Payloads never include agent bearer tokens, session ids, passwords, or full command output. Include only the agent id/name/hostname/tags, the condition, and a dashboard link.
- Optional HMAC signature for generic webhooks; document a shared `X-Sextant-Event` id for receiver-side dedupe.
- Treat the SQLite database as sensitive because it holds channel credentials. Set `0600`, keep `SEXTANT_DATA_DIR` at `0700`, and document backup handling. If encryption at rest is required later, add it with an optional dependency rather than a hand-rolled cipher.
- Update `docs/SECURITY.md` and `deploy/sextant.env.example` with the new secret-bearing variables.

---

## 12. Phases

### Phase 0 — foundations (≈ 0.5 day)

- [ ] Extract conditions into `conditions.py`; `views.py` keeps the same output.
- [x] Add settings, schema tables, migration, and `NotificationService` skeleton.
- [x] Add the FastAPI lifespan task (single-writer lease still pending).
- [x] Add a test path that does not start the background loop; unit tests call `tick()` directly.

### Phase 1 — MVP (≈ 2–3 days) — best value

- [x] Evaluate all agents on a timer; reconcile alerts with no dashboard open.
- [x] Offline/online conditions and events.
- [x] Publish `alert.raised` / `alert.cleared` for disk, memory, GPU, and watched services.
- [x] Telegram channel (ntfy and generic webhook still pending).
- [ ] Routing rules with severity/scope/repeat/cooldown; default channels.
- [x] Durable deliveries with retry/backoff and a delivery log API.
- [ ] Admin Notifications page with add channel, test, enable rules.
- [x] Retention pruning, test/retry audit entries, secret masking.

**Acceptance**

- With no browser open, an agent that stops heartbeating produces exactly one offline notification (and one recovery when it returns).
- A disk crossing 90% produces one critical notification for the incident, not one per heartbeat; clearing produces a recovery.
- Acknowledging or snoozing an incident suppresses further notifications for it.
- Restarting the server does not re-send already-delivered incidents; pending deliveries resume.
- Delivery failures appear in the UI with a retry action and never break heartbeat/command paths.
- No secret appears in an API response, payload, or log.

### Phase 2 — operational and security coverage (≈ 2–3 days)

- [ ] SMTP channel.
- [ ] Failed/timed-out/stale command notifications (with bounded output preview).
- [ ] Failed-login burst, new enrollment, token rotation, user/role change.
- [ ] Agent self-update failure/restart-required; package sync failure/drift.
- [ ] Tag/agent/profile scope precedence in the UI; quiet hours; per-agent mute.
- [ ] Server update available as an optional channel event.

### Phase 3 — the stated critical set (≈ 2–4 days)

- [ ] Docker container inventory, watch list, and alerts (Linux).
- [ ] Certificate-expiry probe and alerts.
- [ ] Dead-man's-switch ping and documentation for external server-down monitoring.
- [ ] Digest mode (daily summary) and optional repeat-while-active.
- [ ] Grouped notifications for fleet-wide conditions.

### Phase 4 — future

- [ ] Browser/PWA Web Push.
- [ ] Per-user subscriptions and escalation policies.
- [ ] Message templates and provider plugins (Pushover, Matrix, PagerDuty).
- [ ] Multi-node/HA delivery coordination.

---

## 13. Testing

Follow the existing `pytest` + jsdom + Playwright split.

- `tests/test_notifications.py`:
  - condition evaluation and rule scope precedence;
  - offline transition with no dashboard read;
  - incident raise/clear/recurrence dedupe;
  - acknowledgement/snooze suppression and ack-expiry;
  - duration gates, cooldown, rate limit, quiet hours (fixed clock);
  - retry/backoff state machine with a fake transport;
  - delivery log redaction;
  - RBAC and CSRF for every new endpoint;
  - lease prevents a second worker from delivering.
- Channel payload tests use a fake `urllib` opener / SMTP stub; no network in tests.
- `tests/test_assets.py` / `ui-tests/ui-smoke.js`: Notifications page, add/test channel, rule editor, delivery log, alert-center "notified" marker.
- Playwright + axe: Notifications page in both themes and mobile width, keyboard flow, no serious/critical violations.
- Update `docs/TESTING.md` with the new file and coverage.
- Test the single-worker assumption explicitly. The lease is a safety net, not a license to run multiple workers without testing.

---

## 14. Open decisions (with recommendations)

| Decision | Recommendation |
|---|---|
| First channel | **Telegram if you already use the app** (no new install); **ntfy** if you want self-hosted delivery or better content privacy |
| Should offline be a normal alert in the alert center? | **Yes**; it is the most important condition and reuses ack/snooze |
| Default notification state | Enabled loop, **no channels** → events recorded, nothing sent until configured |
| Secrets storage | Database (masked in API) for v1; env overrides for immutable deployments; encrypt later with an optional dependency |
| Private webhook targets | Allow with an explicit `SEXTANT_NOTIFY_ALLOW_PRIVATE=1`; block metadata addresses always |
| Repeat behavior | Once per incident + recovery by default; opt-in repeats |
| Email timing | Phase 2, after ntfy/webhook prove the pipeline |
| Browser push | Phase 4 unless mobile ntfy is rejected by the operator |
| Server-down coverage | External watchdog; optional built-in dead-man's-switch ping |

---

## Appendix A — example payload

```json
{
  "event": "alert.raised",
  "id": "evt_01J...",
  "source": "sextant",
  "at": "2026-11-11T18:42:10Z",
  "severity": "critical",
  "recovery": false,
  "incident_id": "inc_01J...",
  "agent": {
    "id": "agt_...",
    "name": "fileserver-01",
    "hostname": "fileserver-01",
    "tags": ["servers", "storage"],
    "profile": "server"
  },
  "condition": {
    "key": "disk:!2F",
    "kind": "disk",
    "value": 94.2,
    "threshold": 90,
    "message": "Disk / is 94% full"
  },
  "title": "fileserver-01: disk / is 94% full",
  "text": "Critical disk alert on fileserver-01 (tag: servers). Disk / is 94.2% full. Acknowledge in Sextant.",
  "url": "https://ops.example.com/#agent/agt_..."
}
```

## Appendix B — implementation touch points

| Area | File |
|---|---|
| Settings / env | `sextant_control/config.py`, `deploy/sextant.env.example` |
| Schema, queries, lease, dedupe | `sextant_control/db.py` |
| Shared conditions | new `sextant_control/conditions.py`; used by `views.py` |
| Evaluator, routing, delivery | new `sextant_control/notifications.py` |
| Lifespan, API, hooks | `sextant_control/server.py` |
| Admin UI | `sextant_control/static/index.html`, `app.js`, `app.css` |
| Tests | `tests/test_notifications.py`, `tests/conftest.py`, `ui-tests/ui-smoke.js` |
| Docs | this file, `ROADMAP.md`, `ARCHITECTURE.md`, `SECURITY.md`, `TESTING.md`, `UI.md`, `API.md` |
