# Changelog

All notable changes to Sextant are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
semantic versioning.

## [0.8.5] - 2026-11-13

### Added

- The Enrollment screen now shows ready-to-run Linux and Windows installer
  commands. Before a token is generated the commands show an
  `insert enrollment code here` placeholder; generating a token inserts the
  code into the hint and both commands, each with its own copy button. The API
  response adds `install_commands` (Linux and Windows) alongside the existing
  `install_command`.

## [0.8.4] - 2026-11-12

### Fixed

- Linux domain-join prerequisite installation now includes `libnss-sss`,
  `libpam-sss`, and PackageKit, and starts `packagekit`. realmd uses PackageKit
  for its package check; without it, realmd reports installed packages as
  missing and refuses to join.

## [0.8.3] - 2026-11-12

### Changed

- Heartbeat wait messages now use the machine's configured heartbeat interval
  instead of a hard-coded 30 seconds, including the domain-join log, remote
  shell hint, and queued command status.

## [0.8.2] - 2026-11-12

### Fixed

- Linux domain join now obtains a Kerberos ticket with `kinit` and runs
  `realm join --unattended` from that ticket, avoiding realmd's unreliable PTY
  password prompt. `adcli join --stdin-password` remains the fallback.

## [0.8.1] - 2026-11-12

### Fixed

- Linux domain join now sends the password immediately for realmd/adcli's
  `--stdin-password` mode instead of waiting for a prompt. This fixes
  `Preauthentication failed` when the account and DNS are correct.

## [0.8.0] - 2026-11-12

### Added

- Minimize button on the machine drawer. Minimized machines appear in a bottom
  tray, keep their tab and terminal state, survive navigation to other sections,
  and can be restored or closed independently. Multiple machines can be
  minimized at once; the tray is not persisted across a full page reload.
- Search box on the Discovery inventory that matches the whole device row:
  IP, hostname, MAC, vendor, type, OS, state, source, segment, ports, tags,
  notes, evidence, wake status, and linked agent name.
- A server-side per-agent **Docker collection** setting. It can enable Docker
  inventory even when the local agent config and profile default deny it, with
  separate server toggles for exec, Compose, and cleanup.

### Changed

- Remote install shows the live job log first, with the paginated job history
  below it (8 jobs per page).
- The remote installer now installs the distribution's venv package when
  `python3 -m venv` fails, instead of only printing an error.

### Fixed

- Domain-join watcher fixes, DNS cache flush, apt sandbox compatibility,
  discovery search, and the other 0.7.3 fixes are included.

## [0.7.3] - 2026-11-12

### Added

- A search box on the Discovery inventory searches the whole device row:
  IP, hostname, MAC, vendor, type, OS guess, state, source, segment, open
  ports, tags, notes, evidence, wake status, and linked agent name.

### Fixed

- The domain-join dialog tracks the join and status commands independently, so
  a failed join now shows its output instead of staying on "command queued".
  Check status is disabled while a join is active.
- Linux prerequisite installs work with the agent's `NoNewPrivileges` unit by
  disabling apt's sandbox user for agent-driven apt commands. Package
  management uses the same fix.
- Added an optional DNS cache flush before joining (`ipconfig /flushdns` on
  Windows; `resolvectl`, `systemd-resolve`, or `nscd` on Linux). Supplying a DC
  in auto mode now selects adcli so the override is honored.

## [0.7.2] - 2026-11-12

### Fixed

- Remote install restarts `sextant-agent` after enrollment, so re-installing
  over an already-running agent loads the rotated token instead of exiting with
  HTTP 401 and showing the host offline.

## [0.7.1] - 2026-11-12

### Fixed

- Remote install uses the release's published wheel filename without adding a
  second `sextant-` prefix, so pip accepts the uploaded wheel.

## [0.7.0] - 2026-11-12

### Added

- **Join domain** on the machine drawer for Windows and Linux agents.
  Windows uses `Add-Computer` without an immediate restart; Linux prefers
  `realm join` and falls back to `adcli`. Optional OU, computer name, DC,
  prerequisite package installation, per-run reboot, and a read-only status
  check. Admin-only and audited.
- Domain-join passwords are held only in server memory until the agent fetches
  the command, then injected into the agent process without touching SQLite,
  argv, or logs.

### Fixed

- SSH key permission errors now explain that the Sextant service user cannot
  read the key.
- Remote install uploads the published `.whl` filename, so pip accepts wheels
  stored under their SHA-256 blob name.
- The remote installer recreates a virtualenv that is missing `pip` and gives a
  clear error when the target lacks `python3-venv`.
- The remote-install job log is now explicitly white for readability.

## [0.6.7] - 2026-11-12

### Changed

- The Discovery remote-install dialog defaults the agent profile to
  **Workstation** instead of the first profile returned by the server.

## [0.6.6] - 2026-11-12

### Added

- The Discovery device drawer has an admin **Remote install** action. It opens
  a prefilled onboarding dialog for the discovered IP: Linux uses the configured
  SSH key, Windows uses WinRM over HTTPS with a one-time password, and it starts
  the normal remote-install job and opens its live log.

## [0.6.5] - 2026-11-12

### Changed

- The Security page now points directory setup at Settings -> Domain &
  directory instead of only environment variables.

## [0.6.4] - 2026-11-12

### Fixed

- The settings module imports cleanly on Windows, where the POSIX `pwd`
  module is unavailable. System-user SSH configuration is explicitly
  Linux/macOS-only; every other setting remains available.

## [0.6.3] - 2026-11-12

### Changed

- Settings -> Domain now derives and shows the DNS domain from the Base DN when
  one was not saved, clarifies that multiple group DNs use semicolons or
  newlines, and labels TLS as LDAPS.

## [0.6.2] - 2026-11-12

### Fixed

- **Test directory** now uses the effective environment-provided LDAP/AD
  configuration before the first UI save instead of reporting that directory
  login is disabled.
- Server updates carry installed optional extras (currently `ldap3` and
  `pywinrm`) into the new release venv, so directory login keeps working after
  an update.

## [0.6.1] - 2026-11-12

### Fixed

- LDAP/AD group lists preserve the commas inside a distinguished name. Use
  semicolons or newlines between multiple group DNs. This fixes full-DN role
  mapping and the group fields shown in Settings.
- Saving domain settings persists an environment-provided bind password to the
  managed secret file, so the configuration survives later env changes.

## [0.6.0] - 2026-11-12

### Added

- A **Settings** page with a self-service **Profile** tab and admin **Server**,
  **Domain & directory**, and **SSH** tabs.
- Server settings can be changed from the UI and are persisted in SQLite over
  the environment defaults: public URL, allowed hosts, session length, offline
  threshold, enrollment token lifetime, secure cookies, and proxy trust. A
  restart action covers the host/secure-cookie changes installed at startup.
- Domain & directory settings configure AD/Samba/LDAP from the UI: domain name,
  server/port, LDAPS and CA path, base/bind DN, write-only bind password, user
  filter, attribute mapping for username/display name/email/groups, group role
  mapping, default role, and a live **Test directory** diagnostic.
- SSH settings can upload or paste a managed private key, or use a local system
  user's `~/.ssh` key and config. The effective path, readability, and
  fingerprint are shown; private keys are never returned.
- `PATCH /api/me` for local display name and email.

### Changed

- LDAP authentication now reads configurable attribute names instead of
  hard-coding `sAMAccountName`, `displayName`, `mail`, and `memberOf`.
- Remote install can pass a system user's `~/.ssh/config` to `ssh`/`scp`.
- The sidebar adds **Settings**; the command palette includes it.

## [0.5.9] - 2026-11-12

### Changed

- The Discovery filter bar now wraps and gives the state/type/segment/vendor
  selects a usable width, so the new **Changed** and **Conflict** state options
  are easy to read and choose.

## [0.5.8] - 2026-11-12

### Fixed

- A reused hostname with a rotated private/randomized MAC is now absorbed as a
  **MAC changed** identity change instead of remaining a hostname conflict.
  This clears stale phone duplicates that kept showing as conflicts.

## [0.5.7] - 2026-11-12

### Changed

- Split Discovery into tabs: **Discover** keeps the scan form, summary, and
  device list; **Topology** holds segments; **Imports** holds external scan and
  lease ingestion; and each connector has its own admin tab (**SNMP**, **UniFi**,
  **RouterOS**, **OPNsense**, **Proxmox**, **ESXi**).
- The Discovery state filter now includes **Changed** and **Conflict**. The
  separate identity-conflict checkbox was removed.
- A device seen in a later scan moves from **New** to **Known** automatically;
  operator decisions are preserved.

### Fixed

- MAC-less observations now merge into the row that already owns the IP instead
  of creating duplicate devices that kept showing as new.
- An IP change on the same MAC is reported as **IP changed** with the previous
  and current address, not as a conflict.
- A MAC change against a stale row is absorbed into that row and reported as
  **MAC changed**. Stale duplicate rows sharing an IP or hostname are merged,
  with their event history preserved.
- Historical `identity_conflict` events no longer keep a device flagged as
  conflicted after the identity has been reconciled.

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

[0.8.5]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.8.5
[0.8.4]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.8.4
[0.8.3]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.8.3
[0.8.2]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.8.2
[0.8.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.8.1
[0.8.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.8.0
[0.7.3]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.7.3
[0.7.2]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.7.2
[0.7.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.7.1
[0.7.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.7.0
[0.6.7]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.7
[0.6.6]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.6
[0.6.5]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.5
[0.6.4]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.4
[0.6.3]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.3
[0.6.2]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.2
[0.6.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.1
[0.6.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.6.0
[0.5.9]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.9
[0.5.8]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.8
[0.5.7]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.5.7
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
