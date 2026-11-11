# Runner Control

A self-hosted remote operations console for a private network: a Linux server, cross-platform runner agents, and a web dashboard for wake-on-LAN, health monitoring, disk/GPU telemetry, service management, shutdown/reboot, and audited remote shells.

It is intentionally a single-tenant, self-hosted tool. There is no cloud relay. The server initiates nothing toward agents; agents make outbound authenticated HTTPS requests and pick up queued work.

> **Security first:** an operator account can execute arbitrary commands as the agent's OS user (root/SYSTEM by default). Read [docs/SECURITY.md](docs/SECURITY.md), put the console behind HTTPS and a VPN or trusted admin network, and do not expose it directly to the public internet.

## Features

- **Authentication and roles** — local accounts with scrypt password hashes, signed HttpOnly session cookies, CSRF protection, login rate limiting, failed-login auditing, and `admin` / `operator` / `viewer` roles.
- **Session and user administration** — server-side session registry with IP/user-agent, single-session and revoke-all controls, user disable/enable, and admin password reset. Password changes, admin resets, disabling a user, and revoke-all invalidate the relevant sessions.
- **Active Directory / Samba AD** — optional LDAP(S) bind authentication with group-to-role mapping and per-user provisioning.
- **Cross-platform agents** — Linux and Windows agents with outbound-only heartbeat/command polling and stable machine identity.
- **Wake-on-LAN** — server-side magic packets for agents (using their reported MAC) and for saved agent-less targets.
- **Live health** — online/offline status, CPU, memory, uptime, disks, NVIDIA GPU utilization/VRAM/temperature, GPU compute processes, service states, and startup entries.
- **Agent profiles** — server-managed behavior profiles (`workstation`, `server`, custom) control heartbeat, collection cadence, enabled modules, action gates, and update policy. Includes a dashboard editor and tag-based assignment.
- **Remote install (Linux)** — install an agent on a fresh Linux host over key-based SSH, with prerequisite checks, job log, and one-time enrollment.
- **Telemetry history** — bounded per-heartbeat samples with configurable retention and a downsampled history API for trend queries.
- **Package store (Tier 0)** — self-hosted, content-addressed artifacts with SHA-256 integrity; hosts agent wheels and internal app packages.
- **Package inventory and desired state** — read-only apt/winget inventory, per-device install/remove/upgrade sync, and a device Packages tab.
- **Agent self-update** — pulls the agent wheel from the store, verifies SHA-256, stages, health-checks, and switches on Linux.
- **Server update check** — read-only GitHub/static-feed check with an admin login banner (notify only).
- **Disk and GPU alerts** — thresholds for disk, memory, GPU temperature, and monitored services.
- **Service management** — start, stop, restart, enable, or disable systemd units and Windows services.
- **Remote shell** — queued Bash, PowerShell, or Windows `cmd` execution from the browser with output capture and command history.
- **Command guardrails** — per-agent queue caps, offline-command policy, cancel queued work, retry failed/cancelled work, and stale-command expiry.
- **Power control** — shutdown, reboot, and cancel scheduled power actions, per machine.
- **Editable agent policy** — server-side tags, notes, watched services, and shell/power/service permissions; local agent policy can always restrict further.
- **Audit log** — logins, failed logins, sessions, enrollments, wake packets, commands, user changes, and agent token changes.
- **Enrollment security** — one-time enrollment tokens; per-agent bearer tokens stored only as SHA-256 hashes; token rotation, re-enrollment deduplication, and agent revocation.
- **No inbound agent ports** — only outbound HTTPS from each agent.

## Architecture

```text
                         admin browser
                              │ HTTPS
                              ▼
                    ┌───────────────────┐
                    │ nginx / TLS       │
                    └─────────┬─────────┘
                              │ 127.0.0.1:8080
                    ┌─────────▼─────────┐
                    │ Runner Control    │
                    │ FastAPI + SQLite  │◄──── LDAP(S) ──── AD / Samba AD
                    │ dashboard + API   │
                    └──────┬──────┬─────┘
             outbound HTTPS│      │ UDP magic packet
                    ┌──────▼──┐ ┌─▼──────────────┐
                    │ Linux   │ │ Wake-on-LAN    │
                    │ agent   │ │ capable PCs    │
                    └─────────┘ └────────────────┘
                    ┌─────────┐
                    │ Windows │
                    │ agent   │
                    └─────────┘
```

Agents heartbeat on an interval (default 30 seconds). The heartbeat uploads telemetry and returns queued commands. The agent executes a command, then posts the exit code and bounded stdout/stderr back to the server. No inbound port is opened on the endpoint.

## Quick start (development)

For a production server and agent installation, see **[docs/INSTALL.md](docs/INSTALL.md)**.

Requirements: Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
source .venv/bin/activate

export RC_DATA_DIR="$PWD/data"
export RC_ADMIN_PASSWORD='a long password for local admin'
.venv/bin/runner-control serve --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>, sign in as `admin`, then create an enrollment token under **Enrollment**. On a Linux target:

```bash
./deploy/install-linux-agent.sh http://127.0.0.1:8080 <TOKEN> my-linux-box
```

Or enroll manually:

```bash
runner-agent enroll --server http://127.0.0.1:8080 --token <TOKEN> --name my-linux-box --watch-service ssh
runner-agent run --config /etc/runner-agent/config.json
```

For a Windows target, run PowerShell as Administrator:

```powershell
.\deploy\install-windows-agent.ps1 -Server https://ops.example.com -Token <TOKEN> -Name "Studio PC" -WatchServices Spooler
```

The development server uses HTTP. Put TLS in front of it for any real deployment; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Tests

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q runner_control runner_agent
node --check runner_control/static/app.js

# Browser-side dashboard smoke test (jsdom)
cd ui-tests && npm ci && npm test
```

The Python suite covers password/session/CSRF security, the API RBAC and authentication contract, local and AD login, session revocation, user disable/reset, failed-login audit, enrollment, heartbeat, command dispatch/idempotency/cancel/retry/expiry, queue caps, offline policy, agent policy, re-enrollment deduplication, WoL, audit logging, agent parsers, executor behavior, config handling, the Tier 0 package store, server update checks, agent self-update, package inventory/desired-state sync, agent profiles, revisioned inventory, and key-based SSH remote install. The jsdom test loads the real dashboard, logs in against mocked API responses, and verifies the fleet, drawer, history actions, policy/user/session dialogs, GPU process list, and remote terminal render. A real end-to-end server/agent smoke test is described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#validation). See [docs/TESTING.md](docs/TESTING.md) for the full run commands and current coverage gaps.

## Configuration

All server configuration is environment variables. The common ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RC_DATA_DIR` | `./data` | SQLite DB, server secret, bootstrap admin file |
| `RC_PUBLIC_URL` | empty | Public base URL used in enrollment commands |
| `RC_COOKIE_SECURE` | `0` | Set to `1` when serving over HTTPS |
| `RC_TRUST_PROXY` | `0` | Trust `X-Forwarded-For` / proxy headers (the proxy must overwrite the header) |
| `RC_ALLOWED_HOSTS` | from `RC_PUBLIC_URL` | Comma-separated `Host` header allowlist |
| `RC_ADMIN_USER` | `admin` | Bootstrap username, only when DB has no users |
| `RC_ADMIN_PASSWORD` | unset | Bootstrap password; otherwise one is generated |
| `RC_SESSION_HOURS` | `12` | Session lifetime |
| `RC_OFFLINE_AFTER_SECONDS` | `90` | Online/offline threshold |
| `RC_OFFLINE_COMMAND_POLICY` | `queue` | `reject` refuses commands for offline agents |
| `RC_MAX_QUEUED_COMMANDS_PER_AGENT` | `50` | Queue cap per agent |
| `RC_QUEUED_COMMAND_TTL_HOURS` | `24` | Expire work queued for offline agents |
| `RC_DISPATCHED_GRACE_SECONDS` | `120` | Extra time before a dispatched command is failed |
| `RC_SESSION_CLEANUP_DAYS` | `7` | Retention for expired/revoked session rows |
| `RC_COMMAND_RETENTION_DAYS` | `90` | Retention for terminal command rows |
| `RC_AUDIT_RETENTION_DAYS` | `365` | Retention for audit rows |
| `RC_TELEMETRY_HISTORY` | `1` | Store bounded per-heartbeat telemetry history |
| `RC_TELEMETRY_RETENTION_DAYS` | `7` | Telemetry history retention (1–365 days) |
| `RC_STORE_ENABLED` | `1` | Enable the package store |
| `RC_STORE_MAX_BLOB_MB` | `512` | Per-artifact upload limit |
| `RC_STORE_MAX_TOTAL_GB` | `20` | Total store quota |
| `RC_UPDATE_FEED` | empty | `github:owner/repo` or HTTPS JSON for the server update check |
| `RC_UPDATE_CHECK_HOURS` | `6` | Update-check cache interval |
| `RC_LDAP_*` | unset | AD/Samba LDAP settings; see the env example |

See [deploy/runner-control.env.example](deploy/runner-control.env.example) for a deployment template and `runner_control/config.py` for every accepted variable. See [docs/AD_LDAP.md](docs/AD_LDAP.md) for directory setup.

## Documentation

- [Installation guide](docs/INSTALL.md)
- [Agent profiles](docs/PROFILES.md)
- [UI status and refinements](docs/UI.md)
- [Remote install over SSH](docs/REMOTE_INSTALL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Agent installation and operation](docs/AGENTS.md)
- [Discovery plan](docs/DISCOVERY.md)
- [Package management plan](docs/PACKAGE_MANAGEMENT.md)
- [Package store plan](docs/PACKAGE_STORE.md)
- [Agent self-update plan](docs/SELF_UPDATE.md)
- [Server update plan](docs/SERVER_UPDATE.md)
- [Code and dependency review](docs/CODE_REVIEW.md)
- [Security model and hardening](docs/SECURITY.md)
- [Active Directory / Samba AD](docs/AD_LDAP.md)
- [HTTP API](docs/API.md)
- [Testing and coverage gaps](docs/TESTING.md)
- [Roadmap and upgrade plan](docs/ROADMAP.md)

## Scope and limitations

- **Remote control is shell + service + power control**, not pixel-level remote desktop or keyboard/mouse injection.
- **GPU telemetry currently uses `nvidia-smi`.** Windows falls back to WMI GPU names without utilization data. AMD ROCm is not collected.
- **Wake-on-LAN is layer-2 local.** A server packet reaches its own broadcast domain. Use a directed broadcast or a separate relay for other VLANs; agents do not act as WoL relays.
- **AD group mapping checks direct `memberOf` membership.** Nested group expansion is not implemented.
- **Single node.** SQLite and the in-process login limiter target a home/lab/small-office deployment, not a multi-region cluster.
- **Charts are inline SVG.** The dashboard has 24-hour sparklines and 24h/7d detail charts with a command/alert timeline; it does not embed a charting library.
- **Package store is Tier 0.** SHA-256 integrity, no signatures or CA; a compromised server can replace artifacts and hashes.
- **Package management UI is functional.** The Packages tab has an interactive inventory table (search/sort/pagination), a desired-state rule editor, heartbeat/inventory age, and sync.
- **Data freshness is shown on cards and in the drawer.** Heartbeat and inventory age are visible, with cadence-aware stale badges.
- **Table controls are partial.** Packages, Startup, and Audit have search/sort/pagination; Services and Users have search, counts, and filtering, but only Services has bounded “show more” paging. History is server-paginated and filterable with CSV export.
- **Fleet operations** — status/profile/tag filters, named saved views, bulk refresh/wake/reboot/shutdown/service/profile actions, an alert center with acknowledge/snooze, cadence-aware stale badges, 24h sparklines, detail charts, and CSV export.
- **Live updates use SSE with polling fallback.** The dashboard streams `/api/events`; command status/result streams use `/api/commands/{id}/events`. Agents still upload one final command result, so raw incremental stdout is not implemented.
- **Package management is enabled by default.** Both the profile and agent-local flags must allow it; disable either side to opt out.
- **Profile assignment is enrollment, admin, or tag.** Profiles are selected at enrollment, reassigned from the device drawer/API, or inherited from a tag. AD/LDAP group sync is future work.
- **Remote install is Linux-only and one-at-a-time.** It uses a single server SSH key and disables host-key verification for rebuilt hosts; use it on a trusted admin network.
- **Windows remote install is not implemented.** Windows agents use the manual installer or OpenSSH/PowerShell tooling until a remote path is added.
- **Windows self-update stages only.** The new version is verified and staged; scheduled-task switching remains manual.
- **Server updates are notify-only.** The console checks for a release and shows an admin banner; it does not apply server updates.
- **No MFA yet.** It is deferred in the roadmap; use a VPN/SSO proxy if you need a second factor now.
- **No command approval workflow yet.** Operators execute immediately; command queue limits and expiry reduce stale-work risk but are not a four-eyes control.
- **Python agents.** Windows agents require Python 3.11+ on the endpoint.
- **Linux agent runs as root** to perform shutdown/reboot and systemd management. Windows runs as SYSTEM. Use the local `allow_shell`, `allow_power`, and `allow_service` config flags to reduce risk.

## Legitimate use

Use Runner Control only on machines you own or administer with authorization. The audit trail, explicit enrollment, agent-side policy flags, and per-agent tokens are there so access is deliberate and reviewable. This project does not implement stealth, persistence beyond the documented service/scheduled task, credential theft, or evasion.

## License

MIT. See [LICENSE](LICENSE).
