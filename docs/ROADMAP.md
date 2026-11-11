# Roadmap and upgrade plan

This document tracks work beyond the initial build and the first hardening pass. It is intentionally separate from the implementation docs so future changes can be prioritized without rewriting the deployment/security guidance.

Related plans: [Discovery](DISCOVERY.md) (proposed, not implemented), [Docker management](DOCKER_MANAGEMENT.md) (proposed, Linux-only), [Notifications](NOTIFICATIONS.md) (proposed, not implemented), [Package management](PACKAGE_MANAGEMENT.md), [Package store](PACKAGE_STORE.md), [Agent self-update](SELF_UPDATE.md), [Server update](SERVER_UPDATE.md), [Agent profiles](PROFILES.md), [UI status](UI.md), and [Code review](CODE_REVIEW.md) (remediation status).

## Current state

### Implemented in the hardening pass

- [x] One-time command queue caps per agent.
- [x] Server-side offline-command policy (`queue` or `reject`).
- [x] Cancel queued commands; retry failed/cancelled commands.
- [x] Stale dispatched-command sweeper (shell timeout + grace).
- [x] Expiry for commands queued while an agent is offline.
- [x] Command cancellation/retry/expiry audit entries.
- [x] Real session registry with IP, user agent, last-seen, expiry, and revocation.
- [x] Session listing, single-session revocation, and revoke-all.
- [x] User disable/enable, admin password reset, and session invalidation.
- [x] Failed-login and rate-limited login audit entries.
- [x] Stable machine identity and re-enrollment deduplication.
- [x] Editable server-side agent tags, notes, watched services, and action policy.
- [x] Agent policy handshake on heartbeat; server policy can restrict shell, power, and service actions.
- [x] Agent local policy still wins: local denial is never overridden by the server.
- [x] Bounded telemetry history (`telemetry_samples`) with retention and a downsampled history API.
- [x] Tiered agent collection: fast metrics every heartbeat, slow inventory on `inventory_interval`.
- [x] Security hardening: bounded login limiter, stricter session/password validation, validated proxy IPs, explicit LDAPS certificate validation.
- [x] Tier 0 package store: content-addressed blobs, SHA-256 integrity, admin upload/download/yank, CLI.
- [x] Read-only server update check with admin login banner.
- [x] Agent self-update (Linux and Windows switch/restart; Windows uses a stable `current` junction) from the store.
- [x] Read-only package inventory and per-device apt/dnf/winget/choco desired-state sync (API + device Packages tab; rule editor pending).
- [x] Server-managed agent profiles (`workstation`/`server`/custom) with installer selection, heartbeat sync, module flags, cadence, dashboard editor, admin reassignment, tag assignment, true package inventory delta, and weekly full reconciliation.
- [x] Linux remote install over key-based SSH with prerequisite checks, one-time enrollment, job log, and one-at-a-time jobs.
- [x] Package desired-state rule editor, inventory filter, heartbeat/inventory freshness labels, interactive tables, fleet filters and per-user server-side saved views, alert center with acknowledge/snooze and recurrence-aware incidents, cadence-aware stale badges, gap-aware 24h/7d detail charts with a persistent event timeline and What changed summary, batch fleet actions, light/dark theme, command palette, Playwright/axe browser tests, change-driven SSE, and incremental command stdout streaming ([UI plan](UI.md)).
- [ ] Remaining UI refinements: profile badges and a high-contrast/zoom accessibility pass.

### Explicitly deferred

- [ ] **MFA / TOTP / WebAuthn** — deferred by request. Authentication remains password + session + CSRF.
- [ ] **SSO/OIDC/SAML** — future identity-provider work; MFA would likely ship with it.
- [ ] Graphical remote desktop / screen control.
- [ ] Persistent interactive PTY sessions.

## Priority 0 — security and reliability

These are the next items that materially reduce operational risk.

### 1. Command approval and maintenance windows

**Why:** Operators still execute arbitrary commands immediately. The queue guardrails prevent accidental backlog, but there is no four-eyes control or change window.

Scope:

- Optional approval mode: operator-created `shell`/`power` commands enter `pending_approval`.
- Admin approves/rejects with a reason; approvals are audited.
- Per-agent or tag-scoped maintenance windows.
- Optional policy “power actions require approval” separate from shell.
- UI queue for pending approvals.

Acceptance criteria:

- Approval can be enabled globally or per agent policy.
- A rejected command never reaches an agent.
- Every approval decision is in the audit log with actor and reason.
- Approvals work with command TTL, queue caps, and stale-command sweeping.

Effort: medium. Depends on the command lifecycle already implemented.

### 2. Alerting and offline notifications

**Why:** Monitoring is passive; nobody knows a disk filled or an agent went offline unless they open the dashboard.

Implemented: a background evaluator with Telegram delivery for offline/online, disk, memory, GPU-temperature, and watched-service incidents; per-incident deduplication; acknowledgement/snooze suppression; recovery messages; a persisted outbox with retry/backoff; admin status/log/test/retry endpoints; and `sextant notify-test` / `notify-log` / `notify-retry` CLI commands.

Remaining scope:

- Generic webhook (JSON), ntfy, Discord/Slack-compatible webhook, email/SMTP.
- Editable rules and thresholds, per-tag/agent scoping, quiet hours.
- Docker container down/unhealthy and command/security events.
- Test-notification button in the UI and an admin Notifications page.

Acceptance criteria:

- Alert fires once per incident, not every poll.
- Recovery notification where applicable.
- Delivery failures are logged and visible.
- No secrets in webhook payloads.

Effort: small–medium. No new datastore required if deduplication state is in SQLite.

See [Notifications plan](NOTIFICATIONS.md) for the full event catalog, configurable monitor rules/channels, background evaluator, Docker integration, and phased delivery.

### 3. Metrics history and charts (implemented)

**Why:** The dashboard only shows the latest sample. Disk and GPU trends are more useful than a snapshot.

Done: bounded `telemetry_samples` table, retention (`SEXTANT_TELEMETRY_RETENTION_DAYS`, default 7 days), `GET /api/agents/{id}/history` with server-side downsampling and gap-aware buckets, 24-hour sparklines, and 24h/7d detail charts with a real timestamp axis. Persistent `agent_events` feed the timeline and What changed summary.

Remaining scope:

- Optional Prometheus exporter.

Acceptance criteria:

- History does not grow without bound.
- Dashboard remains responsive with 100+ agents.
- Charts show gaps for offline periods rather than interpolating fake data.
- Backup/restore includes or intentionally excludes history.

Effort: medium. Prefer a separate history table over expanding the `agents` row.

### 4. Windows packaging and real integration tests

**Why:** The Windows agent currently requires Python and was not exercised on Windows in this environment. Real AD was tested with a mock LDAP implementation only.

Scope:

- Build a signed Windows single-file `.exe` (PyInstaller or equivalent) and optionally an MSI.
- Code-sign agent and installer.
- CI runner on `windows-latest`: metrics collection, service commands, PowerShell shell, power-command construction.
- Samba AD container integration test in CI for LDAP bind, group mapping, and password failure.
- Smoke test for the PowerShell installer with a disposable Windows runner.

Acceptance criteria:

- A clean Windows host can install without Python preinstalled.
- CI executes the Windows agent and verifies a shell command result.
- Wrong AD passwords return 401, not 503; directory outage returns 503.
- Agent config ACLs are verified.

Effort: large. Highest real-world deployment impact.

### 5. Data retention, pagination, and audit export

**Why:** Command output, audit rows, and sessions grow forever. The UI only shows recent slices.

Scope:

- Configurable retention for command output, audit, and session records.
- Audit pagination/filtering by actor/action/date and CSV/JSON export.
- Command history pagination; full-output fetch only on demand.
- Vacuum/checkpoint maintenance job.
- Backup documentation and a tested restore script.

Acceptance criteria:

- Retention job runs on a schedule and is audited.
- Export works without loading the entire table into browser memory.
- Restore test proves the database, secret, and session behavior.

Effort: small–medium.

## Priority 1 — operator experience

### 6. Live updates and interactive shell

- Dashboard updates use SSE (`/api/events`) with 10-second polling as fallback; command status/result events use `/api/commands/{id}/events`.
- WebSocket PTY for Linux; ConPTY/PowerShell streaming for Windows.
- Incremental command output streaming, resize events, and session termination.
- Explicit “remote control session active” banner with audit start/end.

**Risk:** a PTY is a much larger security boundary than one-shot commands. Add approval, idle timeout, and per-session audit before exposing it.

### 7. File transfer

- Upload/download with per-file size limits, checksums, and audit.
- Restrict destinations to configured roots; no arbitrary server-side path traversal.
- Optional approval for uploads to sensitive groups.
- UI progress and cancellation.

### 8. Process manager

- Top processes by CPU/RAM; optional GPU process mapping.
- Kill process with confirmation and audit.
- Link GPU compute process to service/command when possible.
- Windows process start time/user; Linux cgroup/systemd unit.

### 9. Startup management

- Toggle startup entries rather than read-only listing.
- Linux: systemd enable/disable, desktop autostart enabled flag.
- Windows: Run keys, startup folders, scheduled tasks with guardrails.
- Always show the exact command being enabled/disabled and audit it.

### 10. Fleet UX polish

- Bulk select → wake, refresh, shutdown, service action, policy edit.
- Tag/group filters and saved views.
- Virtualized service tables and audit pagination.
- Loading skeletons, empty states, and inline field validation.
- Focus trap for drawer/dialogs; keyboard shortcuts; screen-reader labels.
- Light theme and denser table view.
- Mobile PWA/installability.

### 11. Wake-on-LAN improvements

- Wake verification with ping/TCP check after sending.
- Per-VLAN WoL relays via an agent on that subnet.
- Directed-broadcast validation and per-target relay selection.
- Wake schedules and “wake group” actions.

## Priority 2 — platform and integration

### 12. Identity and access

- TOTP/WebAuthn MFA (deferred for now).
- OIDC/SSO; group/claim mapping.
- Scoped API tokens for automation (read-only, operator, admin).
- Tag/group-scoped RBAC instead of global roles.
- Per-action permissions: `shell`, `power`, `service`, `wol`, `file_transfer`.
- Login risk notifications and optional IP allowlists.

### 13. Agent lifecycle

- Auto-update with staged rollout and rollback (Tier 0 integrity first; signing deferred).
- Version floor / forced-update policy.
- Agent health/collector error reporting (last metrics error, PowerShell failures, GPU probe failures).
- Agent uninstall action from dashboard.
- Per-agent maintenance mode and command pause.
- Group policy inheritance with explicit per-agent overrides.

### 14. Observability and integrations

- Prometheus `/metrics` endpoint and Grafana examples.
- Audit shipping to syslog/HTTP/SIEM.
- Webhook event bus for external automation.
- Health/readiness endpoints including DB/LDAP checks.
- Structured JSON logs with request IDs.

### 15. Deployment and operations

- Docker Compose and a hardened container image.
- Ansible role, systemd templates, and an uninstaller.
- Caddy example alongside nginx.
- Automated certificate renewal and agent CA distribution.
- Backup timer, restore test, and logrotate/journald guidance.
- Multi-worker-safe rate limiting (Redis or proxy-only).

### 16. Browser test coverage

- Playwright tests for login, fleet, drawer, command execution, approvals, user/session dialogs, and mobile layout.
- API fuzz tests for malformed/oversized payloads and role bypass.
- Load test for 100–500 agents at the default heartbeat interval.
- Migration tests from the previous schema version.

The current automated coverage and the remaining gaps are listed in [TESTING.md](TESTING.md).

### 17. Docker host and Compose management (D1/D2 implemented, D3–D5 proposed)

See [Docker management](DOCKER_MANAGEMENT.md) for the full plan.

D1 and D2 are implemented: Linux agents opt into Docker discovery, upload a
bounded inventory, and the Docker Hosts page supports allowlisted container
actions, inspect/logs, one-shot exec, image/volume/network removal, cleanup, and
audit/events. Interactive console, Compose deployment, and recreate/clone remain
future phases.

**Why:** Docker is now the deployment unit for many services, but the console
cannot discover engines, inspect containers, clean up stale resources, or deploy
Compose stacks. Operators currently fall back to shell commands or a separate
Portainer instance.

Scope (Linux only; Windows Docker is explicitly unsupported):

- Agents detect a local Docker engine and upload bounded container/image/volume/
  network inventory, gated by local config, profile, and per-agent policy.
- A separate **Docker Hosts** view lists every agent that reports Docker.
  Selecting one machine shows its environment from cached inventory, with an
  on-demand refresh.
- Container inspect, logs, lifecycle/update/rename actions, and confirmed
  cleanup for stopped containers, dangling/unused images, unused volumes/
  networks, and build cache.
- One-shot exec commands and an interactive PTY console (exec, not `docker
  attach`), with stdin, resize, idle timeout, session caps, and audit.
- A server-side **Compose Files** library on disk with a configurable storage
  path, immutable revisions, and deploy/update/down/rollback to one selected
  host.
- Audit, retention, path containment, secret redaction, and no raw Docker API
  proxy.

Acceptance criteria:

- A Docker-enabled Linux agent appears in Docker Hosts with fresh inventory;
  disabled and Windows agents do not.
- Actions, exec, console, and cleanup respect local/profile/policy denials and
  are audited.
- The interactive console keeps heartbeating, closes on idle/disconnect, and
  does not persist keystrokes or terminal output by default.
- Compose files live in the configured directory, revisions are immutable, and
  bundle/path attacks are rejected.
- The server never connects to a remote Docker socket or opens an inbound port.

Effort: medium–large across five phases (read-only visibility, actions/cleanup
and one-shot exec, Compose deployment, interactive console, recreate/live polish).

## Suggested milestones

### v0.2 — hardening (current)

Command guardrails, sessions/users, agent identity and editable policy. MFA deferred.

### v0.3 — operations visibility

Alert webhooks, metrics history/charts, audit pagination/export, readiness checks.

### v0.4 — remote administration depth

Approval workflow/maintenance windows, process manager, file transfer, startup toggles, interactive PTY behind approval.

### v0.5 — enterprise identity and packaging

MFA/WebAuthn, OIDC/SSO, scoped API tokens, Windows MSI/signed binary, real Windows/Samba AD CI.

### v1.0 — deployment maturity

Container/Ansible installers, auto-update, Prometheus, retention automation, tested restore, Playwright/load coverage.

## Dependencies and risks

- **Interactive PTY / remote desktop** expands the attack surface substantially. Design approval, consent banners, idle timeouts, and session recording before implementation.
- **Alerting** needs idempotent incident state; otherwise every heartbeat creates noise.
- **Metrics history** must have retention from day one or SQLite becomes the bottleneck.
- **Windows packaging** affects certificate signing, update distribution, and code signing key custody.
- **MFA/SSO** should be designed together with session revocation and directory group mapping.
- **Tag-scoped RBAC** changes every authorization check; do not bolt it on after file transfer and approvals.
- **API tokens** require scopes and rotation from the start; avoid a second god-token model.

## How to choose the next item

Recommended order:

1. Alerting and readiness checks (low effort, immediate operational value).
2. Metrics retention/charts (visibility without new remote-execution risk).
3. Approval mode and maintenance windows (largest remaining safety gap).
4. Windows signed packaging + real Windows/Samba AD CI (largest deployment gap).
5. File transfer/process manager/startup toggles (feature depth).
6. MFA/SSO and scoped API tokens (identity maturity).
7. Interactive PTY/remote desktop only with a separate security design.
