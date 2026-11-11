# Changelog

All notable changes to Sextant are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
semantic versioning.

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

[0.4.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.4.1
[0.4.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.4.0
[0.3.1]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.3.1
[0.3.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.3.0
[0.2.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.2.0
