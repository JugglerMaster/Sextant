# HTTP API

Base URL: `https://ops.example.com`. All request and response bodies are JSON unless noted.

Interactive docs are intentionally disabled (`/docs`, `/openapi.json`).

## Authentication

### Browser/session API

1. `POST /api/auth/login` with `username`, `password`, and optional `mode` (`auto`, `local`, `ad`).
2. The response sets an HttpOnly `rc_session` cookie and returns a CSRF token, the user, permissions, and public settings.
3. Send the cookie on subsequent requests.
4. Send `X-CSRF-Token: <token>` on every `POST`, `PATCH`, or `DELETE` except agent endpoints and login.
5. Every request also checks the server-side session row. Sessions can be revoked individually or all at once; disabling a user or resetting a password invalidates their sessions.

```bash
curl -fsS -c /tmp/rc.cookies \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"…","mode":"auto"}' \
  https://ops.example.com/api/auth/login > /tmp/rc.login.json

CSRF=$(python3 -c 'import json;print(json.load(open("/tmp/rc.login.json"))["csrf"])')
curl -fsS -b /tmp/rc.cookies https://ops.example.com/api/dashboard
```

The session cookie expires after `RC_SESSION_HOURS` (default 12, max 8784 hours). A password change issues a new cookie and invalidates older sessions for that user.

### Agent API

Agents send:

```http
Authorization: Bearer rc_agent_...
```

The path agent ID and token must belong together. Enrollment uses a one-time enrollment token in the request body instead of a bearer token.

## Public endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness/version, no secrets |
| `GET` | `/api/auth/config` | Whether LDAP is enabled, version, public URL |
| `POST` | `/api/auth/login` | Local/AD login; sets cookie |

## Session/user endpoints

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/auth/logout` | any | Revoke current session and clear cookie |
| `GET` | `/api/me` | any | Current user, CSRF, permissions, public settings |
| `GET` | `/api/settings` | any | Public settings + server time + permissions |
| `POST` | `/api/me/password` | any local user | Change password, revoke other sessions, issue new session |
| `GET` | `/api/sessions` | any | List your active sessions |
| `DELETE` | `/api/sessions/{id}` | self/admin | Revoke one session; current session also clears the cookie |
| `GET` | `/api/users/{id}/sessions` | admin | List a user's active sessions |
| `POST` | `/api/users/{id}/revoke-sessions` | admin | Revoke all sessions; self gets a fresh session |
| `POST` | `/api/users/{id}/reset-password` | admin | Reset a local password and revoke sessions |

`GET /api/me` example:

```json
{
  "user": {
    "id": 1,
    "username": "alice",
    "display_name": "Alice",
    "email": "",
    "role": "admin",
    "source": "local",
    "active": true,
    "created_at": "2026-11-06T07:00:00Z",
    "last_login_at": "2026-11-06T08:00:00Z",
    "csrf": "…"
  },
  "settings": {
    "ldap_enabled": false,
    "local_auth": true,
    "offline_after_seconds": 90,
    "version": "0.2.0",
    "public_url": "https://ops.example.com"
  },
  "permissions": {"operate": true, "admin": true, "viewer": true}
}
```

`GET /api/sessions`:

```json
{
  "sessions": [
    {
      "id": "ses_...",
      "created_at": "2026-11-06T08:00:00Z",
      "last_seen_at": "2026-11-06T08:05:00Z",
      "expires_at": "2026-11-06T20:00:00Z",
      "ip": "10.0.0.5",
      "user_agent": "Firefox/…",
      "current": true
    }
  ]
}
```

## Fleet endpoints

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/dashboard` | any | Stats + all agents with latest telemetry |
| `GET` | `/api/agents/{id}` | any | One agent + latest 50 commands |
| `GET` | `/api/agents/{id}/history?hours=24&points=200` | any | Downsampled telemetry history (1–720 hours, 10–1000 points) |
| `PATCH` | `/api/agents/{id}` | admin | Edit tags, notes, watched services, action policy |
| `POST` | `/api/agents/{id}/commands` | operator | Queue a command |
| `GET` | `/api/agents/{id}/commands?limit=50` | any | Command history preview (1–200) |
| `GET` | `/api/commands/{id}` | any | One command with full output |
| `POST` | `/api/commands/{id}/cancel` | operator | Cancel a still-queued command |
| `POST` | `/api/commands/{id}/retry` | operator | Retry a failed/cancelled command as a new row |
| `POST` | `/api/agents/{id}/wake` | operator | Send WoL to the agent's MAC |
| `POST` | `/api/agents/{id}/rotate-token` | admin | Rotate agent bearer token |
| `DELETE` | `/api/agents/{id}` | admin | Delete agent and command history |

Dashboard agent fields include `online`, `last_seen`, `cpu_pct`, `mem_pct`, `mem_total`, `mem_used`, `uptime_seconds`, `disks`, `gpus`, `services`, `startup`, `alerts`, `alert_count`, tags, IP/MAC, machine ID, OS, agent version, watched services, and the server policy. `alerts` are computed at read time.

### Queue a command

```http
POST /api/agents/agt_123/commands
X-CSRF-Token: …
Content-Type: application/json

{"kind":"shell","args":{"shell":"bash","command":"df -h","timeout":30}}
```

Kinds and validated arguments:

```json
{"kind":"shell","args":{"shell":"bash","command":"uptime","timeout":60}}
{"kind":"service","args":{"action":"restart","name":"nginx.service"}}
{"kind":"power","args":{"action":"shutdown","delay":30}}
{"kind":"refresh","args":{}}
{"kind":"package","args":{"actions":[{"manager":"apt","action":"install","package":"nginx"}]}}
```

- `shell`: `bash` | `powershell` | `cmd`; command 1–8000 chars; timeout 5–300 seconds.
- `service`: `start` | `stop` | `restart` | `enable` | `disable` | `status`; name matches a conservative pattern.
- `power`: `shutdown` | `reboot` | `cancel`; delay 0–600 seconds.
- `refresh`: no args.
- `package`: 1–25 actions; each has `manager` (`apt` | `dnf` | `winget` | `choco`), `action` (`install` | `remove` | `upgrade`), `package`, optional `version`. Requires both the agent-local `allow_packages` flag and the server policy flag.

Queue guardrails:

- `RC_OFFLINE_COMMAND_POLICY=reject` refuses commands for offline agents (409). The default `queue` holds them until the next heartbeat.
- `RC_MAX_QUEUED_COMMANDS_PER_AGENT` caps waiting work (409 when full).
- `RC_QUEUED_COMMAND_TTL_HOURS` expires queued work so offline endpoints do not receive stale commands.
- Dispatched work fails after its shell timeout plus `RC_DISPATCHED_GRACE_SECONDS`.
- Only `queued` commands can be cancelled. If a command is already `dispatched`, the agent may execute it; cancellation returns 409.
- `failed`/`cancelled` commands can be retried, creating a new command row and audit entry.
- Commands blocked by the agent's server policy (shell/power/service) are rejected with 409 before they enter the queue.

Response is the queued command. It stays `queued` until the agent's next heartbeat, then `dispatched`, then `succeeded` or `failed`.

```json
{
  "id": "cmd_123",
  "agent_id": "agt_123",
  "kind": "shell",
  "args": {"shell": "bash", "command": "df -h", "timeout": 30},
  "status": "queued",
  "created_by": "alice",
  "created_at": "2026-11-06T08:00:00Z",
  "dispatched_at": null,
  "completed_at": null,
  "exit_code": null,
  "stdout": "",
  "stderr": "",
  "error": ""
}
```

Poll `GET /api/commands/{id}` until `status` is terminal. Results are idempotent.

Cancel and retry:

```http
POST /api/commands/cmd_123/cancel
X-CSRF-Token: …
Content-Type: application/json
{}
```

```http
POST /api/commands/cmd_123/retry
X-CSRF-Token: …
Content-Type: application/json
{}
```

Both return the command (cancel) or the new queued command (retry).

### Edit an agent policy

```http
PATCH /api/agents/agt_123
X-CSRF-Token: …
Content-Type: application/json

{
  "name": "Studio PC",
  "tags": ["office", "gpu"],
  "notes": "Managed by ops",
  "watch_services": ["sshd", "nginx"],
  "allow_shell": true,
  "allow_power": false,
  "allow_service": true
}
```

All fields are optional, but at least one must be present. Policy applies on the next heartbeat. The agent's local config is a hard ceiling; server policy cannot re-enable a locally denied action. Watch services set here are authoritative for the server-managed watch list.

### Read telemetry history

```http
GET /api/agents/agt_123/history?hours=24&points=200
```

Any authenticated role can read. `hours` must be 1–720 and `points` must be 10–1000. The server filters to the lookback window and averages samples into at most `points` time buckets, so the response stays bounded even for long-running agents. Unknown agents return 404. Points are sorted ascending by time:

```json
{
  "agent_id": "agt_123",
  "hours": 24,
  "points": [
    {
      "at": "2026-11-06T08:00:00Z",
      "cpu_pct": 22.5,
      "mem_pct": 37.5,
      "disk_max_pct": 95.0,
      "gpu_max_util": 97.0,
      "gpu_max_temp": 71.0,
      "service_down": 0
    }
  ]
}
```

One sample is stored per heartbeat, derived from the latest validated payload. History is kept for `RC_TELEMETRY_RETENTION_DAYS` (default 7, 1–365) and disabled entirely with `RC_TELEMETRY_HISTORY=0`. Disabling history does not affect the latest telemetry shown on the dashboard.

## Package management

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/agents/{id}/packages` | any | Inventory, detected managers, desired-state rules, and drift |
| PUT | `/api/agents/{id}/packages` | admin | Replace the per-device rule list |
| POST | `/api/agents/{id}/packages/sync` | operator+ | Queue a batched `package` command for current drift |

Rules are `{manager, package, desired_state, version_spec}` where `desired_state` is `present`, `absent`, `latest`, or `version`. Sync computes the minimum actions (install/remove/upgrade, at most 25) and queues them as one command. Package actions require the agent-local `allow_packages` flag and the server policy flag; either can deny.

## Package store (Tier 0)

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/store/packages` | any | Catalog |
| GET | `/api/store/packages/{slug}` | any | Package metadata and releases |
| POST | `/api/store/packages` | admin | Create package metadata |
| PUT | `/api/store/packages/{slug}/releases/{version}` | admin | Upload an artifact (raw body) |
| POST | `/api/store/releases/{id}/yank` | admin | Stop offering a release |
| GET | `/api/agent/{agent_id}/store/releases/{id}/download` | agent bearer | Download an artifact |
| GET | `/api/store/events` | admin | Publish/download/yank events |
| GET | `/api/store/stats` | admin | Package/release counts and blob usage |

Upload headers: `X-Artifact-Type` (`agent-wheel`, `deb`, `rpm`, `msi`, `msix`, `zip`, `exe`), `X-Filename`, optional `X-Sha256`, `X-Channel`, `X-Min-Version`, `X-Notes-Url`, `X-Mandatory`. The server computes SHA-256 and size from the body; a supplied hash must match. CLI equivalents: `runner-control store-publish`, `store-list`, `store-yank`.

## Agent profiles

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/profiles` | any | List profiles with settings, revision, and assigned count |
| POST | `/api/profiles` | admin | Create a custom profile |
| PATCH | `/api/profiles/{id or slug}` | admin | Update name/description/settings (bumps revision) |
| DELETE | `/api/profiles/{id or slug}` | admin | Delete an unassigned, non-system profile |
| POST | `/api/profiles/{id or slug}/reset` | admin | Reset a built-in profile to defaults |

Profiles control heartbeat cadence, service/package inventory cadence, module flags, action gates, self-update policy, and output caps. They are edited globally; agents receive the current profile in every heartbeat response. Enrollment accepts `"profile": "workstation" | "server" | <custom-slug>`; an empty value preserves the existing profile on re-enrollment.

Tag assignments:

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/profile-tags` | any | List tag → profile assignments |
| PUT | `/api/profile-tags/{tag}` | admin | Assign a profile to a tag; applies to matching agents |
| DELETE | `/api/profile-tags/{tag}` | admin | Remove a tag assignment |

`PATCH /api/agents/{id}` accepts `{"profile": "server"}` to reassign a device without re-enrollment. Profile values are not editable per device.

## Update check (notify only)

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/update/status?force=false` | admin | Cached server release check |
| GET | `/api/agent/{agent_id}/update?current_version=…` | agent bearer | Latest agent release in the store |

| GET | `/api/export/agents.csv` | any | Export agent inventory CSV |
| GET | `/api/export/packages.csv?agent_id=…` | any | Export package inventory CSV (all agents if omitted) |

## Remote install (admin, Linux)

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/remote-install/test` | SSH and prerequisite check |
| POST | `/api/remote-install` | Start an install job |
| GET | `/api/remote-install` | List jobs |
| GET | `/api/remote-install/{id}` | Job detail and log |

Key-based SSH only, one job at a time. Host-key verification is disabled for rebuilt hosts. See [REMOTE_INSTALL.md](REMOTE_INSTALL.md).

`RC_UPDATE_FEED=github:owner/repo` or an HTTPS JSON URL enables the check. The dashboard shows an admin banner when a newer server version exists. This version does **not** apply server updates automatically.

### Wake an agent

```http
POST /api/agents/agt_123/wake
X-CSRF-Token: …
Content-Type: application/json

{"broadcast":"192.168.1.255","port":9,"packets":3}
```

The agent must have reported a MAC. Response:

```json
{"sent": 3, "mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255", "port": 9}
```

## Wake targets

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/wake-targets` | any | List saved targets |
| `POST` | `/api/wake-targets` | operator | Create target |
| `DELETE` | `/api/wake-targets/{id}` | operator | Delete target |
| `POST` | `/api/wake-targets/{id}/wake` | operator | Send magic packet |

Create body:

```json
{"name":"Office PC","mac":"AA:BB:CC:DD:EE:FF","ip":"192.168.1.50","broadcast":"192.168.1.255","port":9}
```

`mac` accepts colon or dash separators and is returned normalized. `broadcast` must be an IPv4 address.

## Enrollment (admin)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/enrollment-tokens` | Issue a one-time token |
| `GET` | `/api/enrollment-tokens` | List tokens (hashes never returned) |
| `DELETE` | `/api/enrollment-tokens/{id}` | Revoke an unused token |

Create body:

```json
{"label":"Linux lab","expires_minutes":30}
```

`expires_minutes` is optional (5–1440). When omitted, `RC_ENROLLMENT_TTL_MINUTES` (default 30) is used.

Response includes the plaintext token **once** and an install command:

```json
{
  "id": "enr_123",
  "token": "rc_enroll_...",
  "label": "Linux lab",
  "expires_at": "2026-11-06T08:30:00Z",
  "install_command": "runner-agent enroll --server https://ops.example.com --token rc_enroll_..."
}
```

## Users (admin)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/users` | List users, active flag, and session count |
| `POST` | `/api/users` | Create local user |
| `PATCH` | `/api/users/{id}` | Change role and/or active state |
| `POST` | `/api/users/{id}/reset-password` | Reset local password and revoke sessions |
| `GET` | `/api/users/{id}/sessions` | List active sessions |
| `POST` | `/api/users/{id}/revoke-sessions` | Revoke all sessions |

Create body:

```json
{"username":"bob","display_name":"Bob","email":"bob@example.com","role":"operator","password":"at least 12 characters"}
```

Update body (at least one field):

```json
{"role": "viewer", "active": true}
```

Roles: `viewer`, `operator`, `admin`. The last **active** admin cannot be demoted or deactivated. You cannot deactivate your own account. Disabling a user revokes every session and increments their session version.

Reset body:

```json
{"new_password": "a new long password"}
```

AD-backed users cannot have a local password reset; change it in the directory.

Revoking all sessions on your own account returns a new CSRF token and user object, and sets a fresh session cookie:

```json
{"revoked": 3, "csrf": "…", "user": {"id": 1, "username": "admin", "role": "admin", "active": true}}
```

Resetting a password returns `{"user_id": 2, "username": "bob"}`. Resetting your own password also returns a fresh `csrf`/`user` and sets a new session cookie.

## Audit (admin)

`GET /api/audit?limit=100` returns newest-first events (1–500):

```json
{
  "events": [
    {
      "id": 42,
      "at": "2026-11-06T08:00:00Z",
      "actor": "alice",
      "action": "command.created",
      "target": "agt_123",
      "detail": {"command_id":"cmd_123","kind":"shell","agent":"box"},
      "ip": "10.0.0.5"
    }
  ]
}
```

## Agent endpoints

These paths bypass browser CSRF and use the agent bearer token.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/agent/enroll` | enrollment token in body | Exchange one-time token for agent credentials |
| `POST` | `/api/agent/{id}/heartbeat` | Bearer | Upload telemetry, fetch queued commands |
| `POST` | `/api/agent/{id}/commands/{command_id}/result` | Bearer | Upload command result |

### Enroll

```http
POST /api/agent/enroll
Content-Type: application/json

{
  "enrollment_token": "rc_enroll_...",
  "hostname": "box-01",
  "name": "box-01",
  "machine_id": "4f3c…",
  "os": "linux",
  "os_version": "Ubuntu 24.04",
  "arch": "x86_64",
  "ip": "192.0.2.10",
  "mac": "AA:BB:CC:DD:EE:FF",
  "agent_version": "0.2.0",
  "profile": "server",
  "tags": ["lab"],
  "watch_services": ["ssh"],
  "notes": ""
}
```

Response (credentials shown once):

```json
{
  "agent_id": "agt_123",
  "agent_token": "rc_agent_...",
  "reenrolled": false,
  "server_time": "2026-11-06T08:00:00Z",
  "heartbeat_interval": 30,
  "profile": "server",
  "version": "0.2.0"
}
```

If `machine_id` matches an existing agent, the server updates that row, invalidates the old token, preserves command history and admin policy, and returns `reenrolled: true`.

### Heartbeat

```http
POST /api/agent/agt_123/heartbeat
Authorization: Bearer rc_agent_...
Content-Type: application/json

{
  "hostname": "box-01",
  "machine_id": "4f3c…",
  "os": "linux",
  "os_version": "Ubuntu 24.04",
  "arch": "x86_64",
  "ip": "192.0.2.10",
  "mac": "AA:BB:CC:DD:EE:FF",
  "agent_version": "0.2.0",
  "cpu_pct": 12.5,
  "mem_pct": 38.0,
  "mem_total": 17179869184,
  "mem_used": 6528350208,
  "uptime_seconds": 7200,
  "disks": [
    {"mount":"/","total":107374182400,"used":10737418240,"free":96636764160,"percent":10.0,"fs":"ext4"}
  ],
  "gpus": [
    {"name":"NVIDIA RTX 4090","util_pct":97.0,"mem_used":21474836480,"mem_total":25769803776,"temp_c":71.0}
  ],
  "services": [
    {"name":"ssh.service","display_name":"OpenSSH","status":"running","startup":"enabled","watched":true}
  ],
  "startup": [
    {"name":"nginx.service","command":"systemctl enable nginx.service","source":"systemd","enabled":true}
  ],
  "packages": [
    {"manager":"apt","package":"nginx","version":"1.24.0-1","arch":"amd64","source":""}
  ],
  "package_managers": [
    {"name":"apt","version":"2.7.14"}
  ],
  "package_inventory_hash": "…",
  "package_inventory_full": false,
  "package_inventory_collected_at": "2026-11-10T12:00:00Z",
  "package_inventory_mode": "delta",
  "package_inventory_base_revision": 4,
  "package_inventory_revision": 5,
  "package_inventory_deletes": [["apt", "telnet"]],
  "service_inventory_collected_at": "2026-11-10T12:00:00Z",
  "extra": {
    "cpu_model":"…",
    "kernel":"6.8",
    "gpu_source":"nvidia-smi",
    "gpu_processes":[{"pid":4242,"name":"python3","mem_used":8589934592}]
  }
}
```

Response:

```json
{
  "server_time": "2026-11-06T08:00:05Z",
  "offline_after_seconds": 90,
  "policy": {
    "allow_shell": true,
    "allow_power": false,
    "allow_service": true,
    "allow_packages": false,
    "watch_services": ["sshd", "nginx"]
  },
  "profile": {
    "id": "prof_server",
    "slug": "server",
    "revision": 3,
    "heartbeat_seconds": 30,
    "modules": {"telemetry": true, "services": true, "packages": true},
    "collection": {
      "service_inventory_seconds": 3600,
      "package_inventory_seconds": 21600,
      "package_full_seconds": 604800
    },
    "actions": {"allow_shell": true, "allow_power": true, "allow_service": true, "allow_packages": true}
  },
  "request_package_inventory": "",
  "package_inventory_revision": 5,
  "agent_update": {
    "available": true,
    "current_version": "0.2.0",
    "version": "0.3.0",
    "sha256": "…",
    "size": 123456,
    "download_url": "/api/agent/agt_123/store/releases/rel_123/download"
  },
  "commands": [
    {"id":"cmd_123","kind":"shell","args":{"shell":"bash","command":"uptime","timeout":60},"created_at":"2026-11-06T08:00:00Z"}
  ]
}
```

The agent applies `policy` before executing any commands in that heartbeat. Local config remains a hard ceiling.

The server marks returned commands `dispatched` before sending. An agent that crashes after receiving but before reporting leaves the command `dispatched`; create a new command or inspect history. There is no automatic redelivery.

### Command result

```http
POST /api/agent/agt_123/commands/cmd_123/result
Authorization: Bearer rc_agent_...
Content-Type: application/json

{"status":"succeeded","exit_code":0,"stdout":"...","stderr":"","error":""}
```

`status` is `succeeded` or `failed`. The server force-fails a “succeeded” result with a non-zero exit code. Output is truncated to `RC_COMMAND_OUTPUT_LIMIT` (200,000 chars). Re-posting a terminal command returns the stored result with `"idempotent": true`.

## Status codes

| Code | Meaning |
| --- | --- |
| `200` | Success |
| `204` | Deleted/logged out, no body |
| `400` | Validation or business rule failure |
| `401` | Missing/expired session, bad login, bad agent/enrollment token |
| `403` | Role or CSRF failure |
| `404` | Unknown agent/command/target/user |
| `409` | Duplicate username, result for an undispatched command, queue full, offline-policy rejection, cancel after dispatch, revoking an already-used enrollment token |
| `413` | Body over `RC_MAX_BODY_BYTES` |
| `422` | Pydantic shape/field validation |
| `429` | Login rate limit |
| `502` | Wake packet send failure |
| `503` | LDAP directory unavailable |

Errors are `{"detail":"..."}` (FastAPI validation errors use its standard list shape).
