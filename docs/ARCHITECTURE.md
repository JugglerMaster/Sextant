# Architecture

Runner Control is a small, single-node remote operations system. It has three planes:

1. **Admin plane** — browser → nginx/TLS → FastAPI dashboard/API.
2. **Agent plane** — endpoint agents poll the server over outbound HTTPS.
3. **Directory plane** — optional LDAP(S) bind to Active Directory or Samba AD.

There is no agent listener and no third-party relay.

## Components

| Component | Location | Responsibility |
| --- | --- | --- |
| Web dashboard | `runner_control/static/` | Vanilla HTML/CSS/JS single page. Login, fleet view, drawers, terminal, admin pages. |
| HTTP API | `runner_control/server.py` | FastAPI routes, session/CSRF checks, RBAC, command queue, WoL, enrollment, audit. |
| SQLite store | `runner_control/db.py` | Users, sessions, agents, enrollment tokens, commands, audit events, wake targets. WAL mode. |
| LDAP client | `runner_control/ldap_auth.py` | Service bind, user search, user credential bind, group→role mapping. |
| Package store | `runner_control/store.py` | Content-addressed artifact blobs, SHA-256 integrity, quotas, GC. |
| Update checker | `runner_control/updates.py` | Read-only server release check (GitHub or static JSON). |
| Remote install | `runner_control/remote_install.py` | Key-based SSH install for Linux, job log, one-time enrollment. |
| Agent | `runner_agent/` | Heartbeat, telemetry collectors, package inventory/actions, self-update, queued command execution, result posting. |
| Deployment assets | `deploy/` | systemd units, nginx config, Linux/Windows installers, env template. |

## Request and command flow

```text
agent                         server                         browser
  │                              │                               │
  │ POST heartbeat + metrics     │                               │
  ├─────────────────────────────►│                               │
  │                              │ update agent row              │
  │                              │ select queued commands        │
  │◄─────────────────────────────┤ mark dispatched, return them  │
  │                              │                               │
  │ execute locally              │                               │
  │                              │◄──────────────────────────────┤ POST command
  │                              │ validate role/args            │
  │                              │ insert queued command, audit  │
  │                              │                               │
  │ POST command result          │                               │
  ├─────────────────────────────►│ update command, last_seen     │
  │                              │                               │
  │                              │◄──────────────────────────────┤ poll command
  │                              │ return stdout/stderr/status   │
```

Key properties:

- Commands are delivered only in response to a heartbeat. The default offline policy queues work until the agent returns; `RC_OFFLINE_COMMAND_POLICY=reject` refuses new work instead.
- The server caps queued commands per agent (`RC_MAX_QUEUED_COMMANDS_PER_AGENT`).
- A command is marked `dispatched` in the same SQLite transaction that selects it, so two simultaneous heartbeats cannot dispatch the same command.
- Only one command is in flight per agent. The agent polls again immediately after completing it, so a backlog drains without waiting a full heartbeat interval and the stale-command sweeper cannot fail a command that is simply queued behind another.
- Results are idempotent: re-posting a completed command returns the existing row.
- Queued commands can be cancelled; failed/cancelled commands can be retried as new command rows.
- Dispatched commands are failed automatically after their timeout plus a grace period. Queued commands expire after `RC_QUEUED_COMMAND_TTL_HOURS` so an endpoint does not receive week-old work when it reconnects.
- The server does not execute shell commands. It only stores them and returns them to the matching agent.
- Shell/service/power commands are created only by `operator` or `admin` roles.
- `POST /api/agents/bulk` fans one validated action out to at most 200 unique agents, reusing the single-agent queue, policy, WoL, and profile checks. Per-agent failures are returned in place and never abort the rest of the batch; the request and each action are audited.

## Data model

| Table | Contents |
| --- | --- |
| `users` | Local/AD users, role, scrypt hash, source, active flag, `session_version` |
| `sessions` | Random session ID, user, IP, user agent, created/last-seen/expiry, revocation |
| `agents` | Identity, stable machine ID, MAC/IP, token hash, tags/notes, watched services, policy, last-known telemetry |
| `telemetry_samples` | Bounded per-heartbeat history: CPU, memory, disk max %, GPU max util/temp, watched-service-down count |
| `agent_events` | Best-effort state-change log per agent: connectivity, service, package, version, profile, and alert lifecycle |
| `store_packages`, `store_releases` | Package metadata and versioned artifacts (SHA-256, size, channel, yank state) |
| `store_events` | Publish, download, yank audit events |
| `package_inventory` | Latest installed packages and detected managers per agent |
| `package_rules` | Per-device desired state (`present`/`absent`/`latest`/pinned) |
| `package_inventory_state` | Last package inventory hash and full-refresh timestamp |
| `agent_profiles` | Server-managed behavior profiles (cadence, modules, action gates, update policy) |
| `profile_tag_assignments` | Tag → profile assignments (applied to matching agents) |
| `enrollment_tokens` | One-time token hashes with expiry and use metadata |
| `commands` | Kind, validated args, status, dispatcher, timestamps, output |
| `saved_views` | Per-user named fleet filters (`status`, `profile`, `tag`, `search`), unique per user |
| `audit_log` | Actor, action, target, detail JSON, source IP, time |
| `wake_targets` | Agent-less named MAC addresses, broadcast address, port, last wake |

Telemetry is stored denormalized in the agent row (`disks`, `gpus`, `services`, `startup`, `extra`) because the dashboard only needs the latest sample. A separate `telemetry_samples` table keeps one bounded row per heartbeat for trend queries; it is pruned to `RC_TELEMETRY_RETENTION_DAYS` (default 7) and can be disabled with `RC_TELEMETRY_HISTORY=0`. Trend queries fill every requested bucket, so missing heartbeats are explicit `null` points with real timestamps rather than omitted rows. Command history is retained per agent (the drawer History tab paginates and filters it server-side, with CSV export; the overview keeps a bounded latest-50 preview) and terminal commands are pruned after `RC_COMMAND_RETENTION_DAYS` (default 90). Audit rows are pruned after `RC_AUDIT_RETENTION_DAYS` (default 365).

`agent_events` is an append-only, best-effort state-change log recording transitions rather than every heartbeat: an agent returning online after being offline, watched-service status changes, package additions/removals/version changes, agent version changes, profile assignments, and alert raise/clear/acknowledge/snooze lifecycle entries. Writes are fail-open so they cannot break a heartbeat or command path, and detail JSON is bounded. Events share the command retention policy (`RC_COMMAND_RETENTION_DAYS`, default 90).

`saved_views` belongs to a user row and is scoped by `user_id`, with a case-insensitive unique name per user. The browser imports legacy `rc.savedViews` localStorage data after sign-in, skipping names already present, and uses localStorage only as an offline fallback; the database is authoritative when the API is reachable.

## Authentication and authorization

Admin users authenticate with either a local account or LDAP:

1. Local: `scrypt` with per-password salt; format `scrypt$n$r$p$salt$hash`.
2. AD/Samba: service bind to search `RC_LDAP_USER_FILTER`, then a second bind as the user's DN to verify the password. Role comes from direct `memberOf` group DNs.
3. A signed session cookie contains `uid`, `session version`, `session ID`, `csrf`, issue time, and expiry. The signature is HMAC-SHA-256 using `secret.key` or `RC_SECRET_KEY`.
4. Every state-changing `/api/` request (except login and agent endpoints) must include `X-CSRF-Token`.
5. Every authenticated request checks the server-side session row: not revoked, not expired, user active, and session version current.
6. Password changes, admin password resets, disabling a user, and “revoke all” increment `session_version` and revoke the relevant session rows. Revoking all on your own account issues a fresh session for the current browser.
7. `admin` can list/revoke another user's sessions; each session shows IP, user agent, last-seen, and expiry.

Roles:

| Role | Read fleet/commands | Read users/audit | Wake | Commands | Manage users/tokens/agents |
| --- | --- | --- | --- | --- | --- |
| Viewer | yes | no | no | no | no |
| Operator | yes | no | yes | yes | no |
| Admin | yes | yes | yes | yes | yes |

Bulk refresh, wake, power, and service actions follow the same operator gate;
bulk profile apply requires admin. Saved views are available to every signed-in
user but each user can only read, create, or delete their own.

Agent authentication is separate: each agent has a 256-bit bearer token. The database stores only `SHA-256(token)`. The route agent ID and token must match. Enrollment tokens are also one-time and stored hashed.

Agents report a stable machine identity (Windows `MachineGuid`, Linux `/etc/machine-id`, or a host/MAC fallback). Re-enrolling the same machine rotates credentials on the existing agent row instead of creating a duplicate, preserving command history and admin-managed policy.

## Live dashboard updates

`GET /api/events` is a change-driven SSE stream backed by a small in-process dashboard state object:

- State-changing endpoints (agent enrollment/heartbeat, command queue/cancel/retry/result, alert acknowledge/snooze/clear, agent patch/delete/token rotation, profile changes, and related dashboard mutations) increment a thread-safe revision counter after their database commit. The notify call is safe from FastAPI sync endpoints running in threadpool workers.
- The first dashboard snapshot is sent immediately. Afterwards the stream emits a new snapshot only when the revision changes, and wakes all waiting clients when a mutation occurs.
- A bounded safety refresh (about 10 seconds) re-emits the payload even without a mutation so time-derived fields such as `online`/`offline` and `server_time` stay current. Comment keep-alives are sent every 15 seconds.
- The revision cache and an async build lock are shared by every stream in the process, so N clients at the same revision perform one dashboard build rather than N builds. There is no Redis or other dependency.
- While the stream is open, the same signed-cookie/session/user validation used by normal authenticated requests is re-run every few seconds. Revocation, expiry, a disabled user, or a changed `session_version` produces a final `auth` event and closes the stream.

This is deliberately single-node state. It is not shared or coordinated across multiple server processes or replicas; each process would maintain its own revision and cache. Running multiple control-plane replicas is outside the current deployment model.

On the browser side, the fleet grid keeps a keyed map of agent id to card node, reuses unchanged cards, and replaces only cards whose rendered signature changed. This preserves keyboard focus and selected checkboxes through SSE updates. The initial render is bounded (60 cards) with a **Show more** control, and offscreen cards use CSS `content-visibility`, so a large fleet does not create an unbounded DOM in one pass.

## Agent policy

Admins can edit an agent's display name, tags, notes, watched services, and three action permissions (shell, power, service). The heartbeat response carries the current policy; the agent applies it before executing any commands in that heartbeat.

Safety is an intersection:

```text
effective_permission = local_agent_config_permission AND server_policy_permission
```

The server can restrict an action but can never widen a local denial. Watched services are server-managed after enrollment, so changing the watch list does not require editing the endpoint config file.

## Monitoring

The agent collects in two tiers. **Fast metrics** (CPU, memory, disks, GPU utilization/VRAM/temperature, network, uptime) run on every heartbeat. **Inventory** (services, startup entries, GPU compute processes, CPU model, kernel, OS version) is cached and refreshed on the profile's service cadence (6h workstation, 1h server; legacy `inventory_interval` fallback), when the server policy's watch list changes, or when an operator sends a `refresh` command. Package inventory is separate: 24h workstation / 6h server, revisioned with weekly full reconciliation, plus hash-only heartbeats when nothing changed.

### Linux agent

- CPU: delta of the aggregate `/proc/stat` line.
- Memory: `/proc/meminfo`, preferring `MemAvailable`.
- Disks: non-pseudo filesystems from `/proc/mounts` via `statvfs`.
- Services: `systemctl list-units` + `list-unit-files`; watched services (editable in the server UI) always included; payload capped at 300.
- Startup: enabled systemd service unit files plus `/etc/xdg/autostart` and all users' `~/.config/autostart` desktop entries.
- GPU: `nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu` plus compute processes (`pid,process_name,used_memory`).
- Network: default route interface from `/proc/net/route` and its MAC; outbound local IP.
- Uptime/kernel/CPU model from `/proc` and `platform`.

### Windows agent

A fast PowerShell metrics script runs on every heartbeat; a second inventory script runs on the inventory cadence. Together they collect:

- OS, CPU load, CPU model, memory, uptime.
- Fixed local disks (`Win32_LogicalDisk`, `DriveType=3`).
- Services (`Win32_Service`: name, display name, state, start mode); watched services are editable in the server UI.
- Startup registry Run keys (HKLM 64/32 and HKCU), both Startup folders, and `Win32_StartupCommand` for all users, deduplicated.
- Adapter IP/MAC from `Get-NetIPConfiguration`.
- Video controller names from WMI.

NVIDIA GPUs are then queried with `nvidia-smi` for actual utilization/VRAM/temperature and compute processes. If it is absent, only GPU names are reported with zero statistics.

### Alerts

Computed at read time, not stored:

| Alert | Threshold |
| --- | --- |
| Disk warning / critical | ≥ 80% / ≥ 90% |
| Memory warning / critical | ≥ 80% / ≥ 90% |
| GPU temperature warning | ≥ 85 °C |
| Watched service not running | status not `running`/`active` |

GPU utilization is displayed but not itself alerted: AI workloads and mining both legitimately show sustained high utilization. Investigate with the service list, startup entries, and a shell command.

## Package store and application management

- Artifacts are stored as immutable blobs under `RC_DATA_DIR/store/blobs/sha256/<prefix>/<hash>`. The server computes SHA-256 and size at upload; agents re-verify after download. Tier 0 (no signature, no CA) is the current decision.
- The store hosts agent wheels (`artifact_type=agent-wheel`), `.deb`/`.rpm`/`.msi`/`.msix`/portable artifacts, and is the distribution point for self-update.
- Agents are assigned a server-managed profile (`workstation`, `server`, or custom) at enrollment, by an admin, or through a tag assignment. The profile controls heartbeat cadence, service/package inventory cadence, enabled modules, action gates, output caps, and self-update policy. The current profile is returned on every heartbeat; profile edits apply without reinstalling.
- Package inventory is revisioned: the agent sends a hash and revision every heartbeat, deltas (upserts + deletes) at the package cadence, and a full snapshot weekly, on first contact, or when the server requests one. The server applies deltas only when the base revision matches.
- The server update checker is read-only: it reads `RC_UPDATE_FEED` metadata and shows an admin banner. Applying a server update is not automated in this version.
- `POST /api/agents/{id}/packages/sync` queues one batched `package` command (at most 25 install/remove/upgrade actions). Actions execute with argv (no shell) and require both the local and server `allow_packages` flags.
- Remote install is an admin SSH job for Linux: fixed install script, one server key, host-key verification disabled for rebuilt hosts, one job at a time, job log in SQLite.
- Agent self-update verifies the wheel hash/size, stages a versioned venv, health-checks it, switches the `current` symlink, and keeps previous releases. Windows currently stages and requires a manual task switch.

## Wake-on-LAN

The server builds the standard 102-byte magic packet (`FF×6 + MAC×16`) and sends it via UDP with `SO_BROADCAST`. It can wake:

- an enrolled agent, using the last-reported MAC;
- a saved target with a name, MAC, broadcast address, and port.

The server must be on the same layer-2 broadcast domain as the target (or have a directed broadcast routed with the network team's approval). Agents do not act as WoL relays in this version.

## Command execution

| Kind | Arguments | Agent behavior |
| --- | --- | --- |
| `shell` | `shell`: bash/powershell/cmd, `command`, `timeout` 5–300s | Local shell, bounded streaming output, process-tree kill on timeout or output cap |
| `service` | `action`: start/stop/restart/enable/disable/status, `name` | systemctl or PowerShell service cmdlets |
| `power` | `action`: shutdown/reboot/cancel, `delay` 0–600s | `shutdown`/`shutdown.exe` |
| `refresh` | none | No-op result; prompts the UI to refresh after the next heartbeat |

The server validates service names and power actions, but `shell` is intentionally arbitrary. Agent-local flags can disable shell, power, or service execution entirely.

Output is bounded to 200,000 characters per stream by default (`RC_COMMAND_OUTPUT_LIMIT` on the server, `max_output_bytes` in agent config). The agent reads stdout/stderr incrementally into bounded buffers and kills the whole process group on timeout or when the cap is exceeded, returning a failed result.

## Deployment topology

Recommended:

```text
admin VPN / trusted LAN
        │ 443
    nginx TLS ──► 127.0.0.1:8080 Runner Control
                              │
                              ├── LDAPS 636 → domain controllers
                              └── UDP 9 broadcast → LAN

endpoints ── outbound 443 ──► nginx
```

The app server binds loopback by default in the provided unit. nginx terminates TLS and forwards client IP/proto. `RC_COOKIE_SECURE=1`, `RC_TRUST_PROXY=1`, and `RC_PUBLIC_URL=https://…` must be set for this topology.

## Deliberate limits

- No pixel-level remote desktop.
- No persistent interactive PTY; the remote shell is a queued command console.
- No multi-server HA, job queue, or external metrics store.
- Inline SVG charts cover 24-hour sparklines and 24h/7d detail charts; gap-aware history remains available from `GET /api/agents/{id}/history`, and persistent state-change events come from `GET /api/agents/{id}/events`.
- No command approval workflow yet (queue guardrails and expiry only).
- No nested AD group resolution.
- No MFA in the app itself (deferred; see the roadmap).
- NVIDIA-only utilization telemetry.
- WoL is limited to the server's broadcast domain.
- Windows agents require Python 3.11+.
