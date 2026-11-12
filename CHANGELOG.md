# Changelog

All notable changes to Sextant are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
semantic versioning.

## [0.5.6] - 2026-11-12

### Changed

- Version-alignment release for validating the update-only topbar shortcut and
  the Updates tab. No functional code changes.

## [0.5.5] - 2026-11-12

### Changed

- Removed the top-right Refresh now button. Fleet data already refreshes through
  live SSE with polling fallback.
- Moved server updates to their own admin **Updates** tab.
- Added an update-only topbar shortcut with an up-arrow icon. It appears only
  while a newer release is known and navigates to the Updates tab.
- Changed the command-palette trigger to a search icon and a Search tooltip,
  while keeping Ctrl/Cmd+K.

## [0.5.4] - 2026-11-12

### Fixed

- After a server update or rollback, the admin UI now polls the update status
  until the restarted service reports the new version and then reloads itself.
  This clears the stale "Restart pending" state and loads the new static assets.

## [0.5.3] - 2026-11-12

### Changed

- Version-alignment release for validating the live server self-update path from
  0.5.2 to 0.5.3. No functional code changes.

## [0.5.2] - 2026-11-12

### Added

- Admin server self-update: daily cached GitHub/static-feed checks (default 24
  hours, 1–168 range), a Security-page Updates panel, and a login banner with
  **Check now**, **Update now**, and **Release notes**.
- Manual apply stages `/opt/sextant/releases/<version>/venv`, installs the wheel
  with dependencies, health-checks `sextant --help` plus an import/version
  check, atomically repoints `/opt/sextant/current`, and records the previous
  target for **Rollback**.
- Release metadata now carries asset SHA-256 (GitHub asset digest or static feed
  `sha256`), selects the server wheel by filename/architecture, and disables
  Apply with an explanation when no hash is published.
- Root-owned systemd restart handoff (`sextant-update-restart.path`/`.service`
  and `/usr/local/sbin/sextant-update-restart`) so the unprivileged server never
  calls `systemctl` directly. The main unit now runs
  `/opt/sextant/current/bin/sextant` and sets `TimeoutStopSec=15`.
- Uvicorn graceful-shutdown timeout (`SEXTANT_GRACEFUL_SHUTDOWN_SECONDS`, default
  10s) so open SSE connections do not block an update restart.
- Audit entries for check, check-now, apply, and rollback with feed-token and
  credential redaction.

### Changed

- `SEXTANT_UPDATE_CHECK_HOURS` now defaults to 24 instead of 6.

## [0.5.1] - 2026-11-12

### Fixed

- Live fleet updates no longer re-render self-managed drawer tabs (Trends,
  History, Packages, Remote) while they are open. This stops the Trends drawer
  from jumping back to the top and flickering on every SSE refresh.
- Overview refreshes preserve the drawer scroll position; the fleet grid is
  refreshed when the drawer closes instead of being rebuilt behind it.
- Heartbeat and inventory ages now resolve to seconds after 5 seconds instead
  of continuing to say "just now" for an entire heartbeat interval. The card
  heartbeat value also has an absolute-time title.

## [0.5.0] - 2026-11-12

### Added

- CIDR-scoped network discovery (D1–D2): default-off with an audited
  authorization acknowledgement, private CIDR allowlists/excludes, neighbour
  table + reverse DNS + bundled OUI + passive SSDP collection, opt-in TCP
  connect/banner/TLS fingerprinting, device classification, MAC/IP/hostname
  merging, deduplicated change events, a cancellable bounded scan worker, and a
  Discovery page with filters, device drawer, scan progress/history, and
  disabled/unacknowledged states. Agent-relayed discovery (D3) lets an eligible
  Linux agent scan its own allowlisted VLAN over the existing outbound command
  channel and report bounded results back; local, profile, per-agent policy, and
  server relay gates all apply. Discovery analysis (D4) adds exposure and
  identity-conflict views, per-segment topology summaries, TCP/neighbour wake
  verification, and bounded audited CSV/JSON export. Discovery onboarding (D5)
  promotes a device to a wake target, a one-time Linux install command, a manual
  Windows elevated-PowerShell command, or an existing-agent link, without
  duplicating wake targets or exposing tokens after the response. Discovery
  integrations (D6) add Nmap XML/JSON, arp-scan, dnsmasq/ISC lease, and normalized
  JSON imports; bounded stdlib SNMP v2c polling; and read-only UniFi/RouterOS/
  OPNsense/Proxmox/ESXi adapters. All integrations are off by default, admin-only,
  audited, and use write-only in-memory secrets that are never persisted or
  logged. Live vendor connectors are marked unverified; ESXi is fixture-only.
  Credential-based Windows remote install is implemented over WinRM/HTTPS with
  the optional `windows-remote` (pywinrm) extra: certificate validation, a
  one-time admin password held only in the job thread, SHA-256 wheel checks,
  reviewed PowerShell installer, redacted logs/audit, SYSTEM scheduled task, and
  authenticated-heartbeat success verification. OpenSSH for Windows remains a
  future alternative, and live WinRM is fake-transport tested in CI.

### Changed

- Replaced the former light skin with a flat **Classic** theme: white panels, Windows blue accent, Segoe UI, square controls, and no gradient glows. The dark theme is still the default and now uses one teal accent for tags and statistics, with semantic colors reserved for status/alerts.

## [0.4.1] - 2026-11-11

### Fixed

- Agent enrollment no longer pins Docker action gates to the profile state at
  enrollment time. Enabling Docker in a profile later now takes effect without
  manually editing each agent policy. Per-agent policy remains an optional
  additional restriction.
- The Docker Hosts page correctly picks up an agent after its profile is enabled.

## [0.4.0] - 2026-11-11

### Added

- Telegram notifications for offline/online, disk, memory, GPU-temperature, and
  watched-service incidents, with per-incident deduplication, acknowledgement
  and snooze suppression, recovery messages, a persisted outbox with retry and
  backoff, admin status/log/test/retry API endpoints, and `sextant notify-test`,
  `notify-log`, and `sextant notify-retry` CLI commands.
- Docker Hosts and Compose Files views for Linux agents: bounded engine and
  container/image/volume/network inventory, cached host detail, allowlisted
  container actions, logs, one-shot exec, cleanup, an interactive `docker exec`
  console, server-side Compose revisions and multi-host deploy/update/down/
  rollback, drift indicators, health events, and a validated container clone.
- Agent cards show the reported agent version; the API and CSV export already
  include it.

## [0.3.1] - 2026-11-11

### Added

- Windows self-update now switches the active release instead of staging only.
  The installer creates a stable `current` directory junction, the scheduled task
  points at `current\Scripts\sextant-agent.exe`, and `sextant-agent update
  --apply --restart` migrates a legacy task action, repoints the junction, and
  restarts the `SextantAgent` task.
- Windows installer accepts a built wheel as `SourcePath` as well as a source
  tree.

### Fixed

- Windows update preserves the previous junction target if the new junction
  cannot be created.
- Release pruning never removes the active release.

## [0.3.0] - 2026-11-11

### Added

- Sextant product identity, `sextant` server CLI, `sextant-agent` client CLI,
  `SEXTANT_*` environment variables, and new Linux/Windows install paths.
- Incremental command stdout/stderr streaming from the agent over SSE, with
  ordered chunks, bounded replay, and final-result reconciliation.
- Change-driven dashboard SSE with a shared per-revision snapshot cache, a
  periodic safety refresh, and in-stream session revalidation.
- Server-side paginated command history with status/text/time filters and
  filtered CSV export.
- Gap-aware telemetry charts with real timestamp axes and explicit null buckets.
- Persistent agent event log, event timeline, and the What changed summary.
- Keyed incremental fleet rendering, bounded card rendering, batch fleet
  actions with per-agent results, and per-user server-side saved views.
- Playwright + axe-core browser tests across dark/light themes and desktop/mobile
  widths, plus keyboard and focus coverage.
- Alert incident lifecycle: first/last seen, resolved state, recurrence as a new
  incident, acknowledgement TTL, and lifecycle events.

### Fixed

- Small command output is now flushed while the process is still running.
- The Linux installer resolves the systemd unit correctly when `SOURCE_DIR` is a
  built wheel.
- Inline styles blocked by the Content Security Policy were removed.
- SVG charts now render correctly in real browsers.

### Validated

- 192 Python tests, 10 browser tests, and a live Raspberry Pi armv7l install,
  telemetry run, streaming command, SSE check, self-update from 0.2.0 to 0.3.0,
  and alert/saved-view/bulk-action API flow.

## [0.2.0] - 2026-11-09

Pre-rename baseline: agents, profiles, package inventory and store, remote
install, telemetry history, alerts, fleet UI, and the initial test suite.

[0.5.6]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.6
[0.5.5]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.5
[0.5.4]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.4
[0.5.3]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.3
[0.5.2]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.2
[0.5.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.1
[0.5.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.0
[0.4.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.4.1
[0.4.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.4.0
[0.3.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.3.1
[0.3.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.3.0
[0.2.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.2.0
