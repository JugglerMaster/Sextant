# Changelog

All notable changes to Sextant are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
semantic versioning.

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

[0.3.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.3.0
[0.2.0]: https://github.com/JugglerMaster/Sextant/releases/tag/v0.2.0
