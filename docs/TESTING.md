# Testing

This page covers the automated checks in the repository, how to run them, and what is intentionally not covered yet. The suite is deterministic: API tests use a temporary data directory and an in-process `TestClient`; WoL sends are monkeypatched, and directory authentication uses a fake LDAP module instead of a live domain controller.

## Setup

Requirements:

- Python 3.11+.
- A development install with the `dev` extra: `.venv/bin/pip install -e '.[dev]'`, or the reproducible path: `.venv/bin/pip install --require-hashes -r requirements-dev.lock && .venv/bin/pip install --no-deps -e .`.
- Node.js 18+ for the jsdom dashboard smoke test and Playwright browser tests.

The production deployment venv intentionally omits pytest and other test dependencies. Run the tests from a development checkout.

## Python tests

Run the whole suite:

```bash
.venv/bin/python -m pytest -q
```

`make test` runs the same command. For a single file or test:

```bash
.venv/bin/python -m pytest -q tests/test_api.py
.venv/bin/python -m pytest -q tests/test_contracts.py
.venv/bin/python -m pytest -q tests/test_contracts.py::test_csrf_required_for_mutations_but_not_gets_or_agent_routes
.venv/bin/python -m pytest -q -k "rbac or session"
```

| File | Focus |
| --- | --- |
| `tests/test_api.py` | Public routes, login/logout, CSRF, enrollment, heartbeat, command dispatch/results, WoL, audit, user administration, agent rotation/deletion |
| `tests/test_contracts.py` | API contract edge cases: RBAC matrix, unauthenticated 401s, CSRF, body limits, agent bearer auth, session revocation/disable, expired enrollment, command argument validation, unknown agent/command handling |
| `tests/test_remediation.py` | Regression coverage for the code-review fixes: Host-header/CSRF, trusted hosts, chunked bodies, session/UID binding, weak secrets and bootstrap passwords, service-name option injection, enrollment TTL, one-command dispatch, late results, bounded agent output, redirect refusal |
| `tests/test_store.py` | Tier 0 package store: publish/upload SHA-256, duplicate/bad-hash rejection, yank, agent download, RBAC, per-file quota |
| `tests/test_updates.py` | Version comparison, GitHub/static update check, endpoint cache/force, admin-only access |
| `tests/test_selfupdate.py` | Agent update endpoint/heartbeat directive, check/apply flow, hash mismatch, local disable, Linux switch, Windows task-action migration, Windows switch/restart flow, and a Windows-only junction integration test |
| `tests/test_packages.py` | Package command builders, inventory parsers, policy intersection, executor routing, inventory API, desired-state sync |
| `tests/test_inventory.py` | Revisioned package inventory (full/delta, hash, stale-base fallback), agent payload cache, profile cadence application |
| `tests/test_profiles.py` | Built-in/custom profiles, RBAC, create/update/reset/delete, enrollment profile selection, admin reassignment, tag assignment, heartbeat sync |
| `tests/test_remote_install.py` | SSH host-key options, install script steps, token redaction, API job lifecycle, RBAC, missing key, one-at-a-time guard |
| `tests/test_exports.py` | Agents, package inventory, and filtered command-history CSV exports, including quoting and content type |
| `tests/test_command_history.py` | Paginated command history: total counts, status/text/time filters, limit/offset/timestamp validation, and viewer/anonymous read access |
| `tests/test_history.py` | Heartbeat-derived telemetry samples, gap-aware bucket series with explicit nulls and timestamps, point caps, viewer access, retention, disabled history, legacy schema migration |
| `tests/test_agent_events.py` | Transition-based event recording without heartbeat duplicates, service/package/version/profile/alert lifecycle events, event API hours/limit/RBAC, and event retention |
| `tests/test_alert_lifecycle.py` | Alert incident reconciliation: acknowledgement then clear, recurrence as a new incident, last-seen updates preserving acknowledgement, acknowledgement TTL expiry, idempotent/concurrent dashboard reads, lifecycle events, and alert-action RBAC |
| `tests/test_notifications.py` | Telegram payload/URL encoding and error redaction, offline/online transitions with no browser, health-alert raise/recovery deduplication, acknowledgement and minimum-severity suppression, watched-service alerts, outbox retry/backoff, persistence across service restarts, and notification API RBAC |
| `tests/test_command_output.py` | Agent output chunk upload auth/ownership, ordering/idempotency, per-command cap, terminal-state rejection, retention pruning, and SSE live/replay output |
| `tests/test_events.py` | Change-driven dashboard SSE payload/content type, prompt heartbeat/mutation delivery, shared per-revision cache, 401/403 auth, session revocation/expiry stream close, command terminal payload and stream close |
| `tests/test_bulk_saved_views.py` | Batch fleet actions (RBAC, validation, partial failures, per-agent results, audit) and per-user saved-view CRUD, isolation, duplicate names, and filter validation |
| `tests/test_assets.py` | Static assets stay compatible with the strict CSP: no inline style attributes or DOM inline-style writes |
| `tests/test_hardening.py` | Failed-login audit, session registry and revocation, user disable/reset, command cancel/retry/expiry, queue caps, offline policy, agent policy, re-enrollment deduplication |
| `tests/test_security.py` | Password hashing/policy, session signing/tampering/expiry, token hashing, magic packets, LDAP escaping and role mapping, fake LDAP bind flow |
| `tests/test_agent.py` | Linux/metric parsers, collector normalization, config persistence (including Docker flags/cadence), executor builders and policy intersection, agent loop smoke test |
| `tests/test_docker_validation.py` | Docker action model/normalizer validation: action allowlists, IDs/names/paths/shells/limits, cleanup scopes, unknown-field and raw-flag rejection |
| `tests/test_docker_agent.py` | Docker CLI detection reasons, Windows unsupported, heartbeat summary, bounded inventory parsing/caps/hash/revision, Compose-missing reporting, no inspect/env collection, profile/policy gates, argv-only actions, exec construction and timeout forwarding, clone argv allowlist (no raw flags/networks/image), cleanup reclaimed parsing/Compose exclusion, mutation guard, volume/network usage |
| `tests/test_docker_api.py` | Docker inventory bearer/ownership/profile gates, strict payload/caps/size limits, atomic replacement, host list/detail RBAC/capabilities, refresh/action/exec/cleanup routes, offline policy, one-active-mutation, audit/events/result propagation, cleanup preview, Windows/disabled behavior, Compose stack CRUD/RBAC, .env redaction, deploy/bundle/history/cancel/settings, duplicate-project protection, multi-host partial failure, clone validation/RBAC/Compose warning, config-hash drift, health-event deduplication |
| `tests/test_docker_store.py` | Compose deterministic hashing, flat filename/size/path/symlink/build rejection, atomic current/revision writes, pruning, bundle creation, directory migration/refusal |
| `tests/test_docker_compose_agent.py` | Compose bundle hash/size/revision verification, traversal/symlink/oversize/build rejection, config/pull/up argv order, down volumes opt-in, project/gate validation |
| `tests/test_docker_console.py` | Broker lifecycle/caps/idle/disconnect, console API open/input/resize/output/close, ownership/gates/limits, no transcript in DB/audit, server-restart cleanup, Windows rejection, PTY argv/round-trip/termination and nonblocking agent behavior |
| `tests/test_discovery_engine.py` | Discovery configuration, CIDR allowlist/excludes and all rejection classes, scope subset, host caps, method/active-probe gates, scan rate/queue, `ip neigh`/`arp` parsing, OUI/randomized MAC, reverse DNS, SSDP parsing, TCP/banner/TLS parsing, classifier, MAC/IP/hostname merge rules, agent/wake links, event deduplication, went-dark, and an end-to-end mocked scan |
| `tests/test_discovery_api.py` | Discovery RBAC/CSRF, audited acknowledgement, disabled/unacknowledged refusal, scan lifecycle/progress/cancel/concurrency, out-of-scope and host-cap rejection, device list/detail/filter, patch/delete, audit actions, and retention pruning |
| `tests/test_ad_login.py` | AD login provisioning, role mapping, bad credentials, directory outage, local-account precedence |
| `tests/test_db.py` | Schema initialization and migration of legacy schemas, including alert incident lifecycle columns |

Fixtures live in `tests/conftest.py`. `settings` points at a fresh `tmp_path`, `app` builds the FastAPI application, `client` wraps it in a `TestClient`, and `admin` signs in the bootstrap administrator. Helpers in `tests/helpers.py` create enrollment tokens, enroll agents, send heartbeats, and build headers.

Notification tests never contact Telegram. They construct `NotificationService` with a fake client and call `tick(now=...)` directly, so offline transitions and retry backoff are deterministic and the background loop is not started in the normal app fixture. `tests/test_notifications.py` also verifies that the bot token is never returned by the status endpoint or embedded in delivery errors.

The contract tests also exercise the request body guard. They set an explicit `Content-Length` header where needed, because the `TestClient` normally recomputes a correct length for a request body.

## Compile and static checks

```bash
.venv/bin/python -m compileall -q sextant_control sextant_agent
.venv/bin/python -m pyflakes sextant_control sextant_agent tests
node --check sextant_control/static/app.js
```

`make check` runs all three. These checks do not execute the application; they catch syntax and basic unused-name errors.

## Dependency and supply-chain checks

```bash
.venv/bin/pip check
.venv/bin/pip-audit
cd ui-tests && npm audit --audit-level=high
```

`make audit` runs the Python and npm audits. `make lock` regenerates `requirements.lock` and `requirements-dev.lock` with hashes from `pyproject.toml`. The committed CI workflow (`.github/workflows/ci.yml`) installs from `requirements-dev.lock`, runs the suite and static checks, and runs `pip-audit` and `npm audit`.

## jsdom dashboard test

```bash
cd ui-tests
npm ci
npm test
```

`npm test` runs `ui-tests/ui-smoke.js`. It loads the real `sextant_control/static/index.html` and `app.js` in jsdom, replaces `window.fetch` with mocked API responses and `EventSource` with a controllable mock, signs in, and checks the fleet view, update banner, SSE live updates and polling fallback, bulk power/service/profile actions, saved views, theme toggle, command palette, drawer tabs, package inventory and rule editor, service filters, GPU process list, gap-aware trends charts (timestamp-positioned points and broken lines over null buckets), persistent state-change timeline entries with real event timestamps, the What changed summary, alert first/last-seen and acknowledgement-age labels, recurrence rendering as a fresh active incident, alert actions, paginated command history (search, status filter, page sizes, Previous/Next, X-Y of Z count, and filter-aware CSV export), remote terminal, profiles page, remote install page, Docker Hosts list/detail/tabs/refresh/empty/auto-refresh state plus drift badges, inspect, logs, exec, console open/input/close, clone validation/warning, remove, cleanup confirmation/results, and offline-disabled actions, Compose Files list/editor/revisions/multi-host deploy/per-host summary/deployment history/empty state, Discovery summary/filters/table/device drawer/edit/scan start/cancel/disabled/unacknowledged states, and the policy/user/session dialogs. It is a DOM smoke test, not a browser end-to-end test.

`ui-tests/node_modules` is not committed. `npm ci` needs network access to fetch jsdom; if the directory is absent and the environment is offline, skip the UI test rather than vendoring dependencies.

## Browser accessibility and visual tests

`npm run test:visual` runs Playwright against the real dashboard in Chromium. The harness serves the production `sextant_control/static/index.html`, `app.css`, and `app.js` from a small static server, intercepts every `/api/**` request with deterministic fixtures, and replaces `EventSource` with an in-page mock. The date is frozen, the timezone is UTC, and the runner is pinned to one worker so screenshots are reproducible.

Install the test dependencies and the matching Chromium build once:

```bash
cd ui-tests
npm ci
npx playwright install chromium
# equivalent shortcut: npm run test:visual:install
```

Run the browser suite:

```bash
cd ui-tests
npm run test:visual
```

The suite covers the login page, fleet dashboard with agents and alerts, the drawer Overview/Trends/Services/Packages/History/Remote tabs, Compose Files list/editor/revisions/deploy controls, Discovery summary/filters/drawer/scan start/cancel plus disabled and unacknowledged states at desktop dark/light and a mobile viewport, Docker console open/output/close, Docker Hosts fast-poll revision updates, drift badges and the safe clone dialog with focus and Compose drift validation, Ctrl/Cmd+K command palette open/filter/activate/Escape, keyboard-only drawer opening with focus trap and focus return, Docker Hosts inspect/cleanup and Compose Files deploy-dialog flows with axe scans, and axe-core scans of login, dashboard, drawer, trends, and Discovery in both themes. The axe gate fails on any `serious` or `critical` violation. No rule is excluded for convenience; if a rule is a documented false positive, name the rule and the reason next to the scan.

Screenshots are written to `ui-tests/artifacts/` for manual review. Both `ui-tests/artifacts/` and `ui-tests/test-results/` are gitignored, as are `node_modules` and downloaded browser binaries. The screenshots are review artifacts, not pixel baselines: the suite does not fail on screenshot differences.

If Playwright reports a missing browser, run `npx playwright install chromium` for the installed Playwright version. Pointing Playwright at an existing Chromium executable is also possible with `launchOptions.executablePath`, but the browser must match or closely match the pinned Playwright release.

To run only the fast DOM smoke test (as before):

```bash
cd ui-tests
npm test
```

## Manual live validation

A real-agent smoke test was run on a Raspberry Pi (Raspbian 13, armv7l, Python 3.13.5) against a temporary server:

- Built `sextant-0.2.0-py3-none-any.whl`, installed it with `deploy/install-linux-agent.sh`, enrolled as `pi-live` with the `server` profile, and started the systemd unit.
- Verified heartbeat telemetry (CPU, memory, root and boot disks, 211 services, 962 apt packages, `arch=armv7l`).
- Published `0.3.0` to the server package store and ran `sextant-agent update --apply --restart`; the agent verified SHA-256, staged a new venv, switched `current`, restarted, and reported `0.3.0`.
- Ran a six-line shell command and consumed `/api/commands/{id}/events`: 12 output events arrived once per second before the terminal event, followed by the canonical final stdout/stderr.
- Verified `/api/events` sends the next dashboard snapshot immediately after a command mutation and closes an open stream with an `auth` event when the session is logged out.
- Drove the real dashboard in Playwright: login, drawer, gap-aware Trends, paginated History, 50-row package/service tables, live terminal output, command palette, and light-theme toggle.

The live run found and fixed two defects the unit/jsdom suites missed: small stdout was buffered until process exit (`read()` vs `read1()`), and the wheel installer looked for the systemd unit under the wheel path. Inline styles blocked by the CSP were also removed. These remain manual checks; the automated browser suite uses mocked APIs.

## Coverage gaps

The following are explicitly not exercised by the automated suite today:

- **Real Windows CI.** The Windows CI job runs the self-update tests, including a real directory-junction switch. It does not execute the full PowerShell metrics/inventory scripts, service or power commands, config ACL setup, the Windows installer, or the scheduled-task action update. The Windows update flow's `schtasks` calls are mocked outside that job. See the roadmap item for broader `windows-latest` coverage.
- **Real Active Directory / Samba AD.** LDAP tests use a fake `ldap3` module and a monkeypatched `authenticate_ldap`; there is no live domain controller, LDAPS certificate/CA validation, group provisioning, or lockout test. A Samba AD container integration test remains on the roadmap.
- **Cross-browser and visual regression.** Playwright covers Chromium at desktop and one narrow mobile viewport. It does not run Firefox or WebKit, compare screenshots against committed pixel baselines, or exercise browser zoom/reflow and a high-contrast theme. The dashboard browser tests intercept the API and mock SSE, so a live server, real SSE reconnects, and streamed terminal output remain outside the browser suite.
- **Load and concurrency.** There is no load test for 100–500 agents, no sustained heartbeat/command throughput test, and no SQLite multi-writer stress test beyond normal request coverage.
- **Migration matrix.** `tests/test_db.py` migrates one legacy schema fixture to the current schema. There is no multi-version migration matrix, rollback test, or automated backup/restore verification.
- **Deployment integration.** systemd, nginx/TLS, UFW, certificate renewal, and the Linux/Windows installers are not exercised by tests. The deployment guide describes manual validation instead.
- **WoL on the wire.** `send_magic_packet` is mocked or tested with a fake socket; no test sends a real UDP broadcast or verifies wake-up on hardware.
- **Automated end-to-end server/agent run.** The full uvicorn server plus real agent smoke test in [DEPLOYMENT.md](DEPLOYMENT.md#validation) is manual. The automated suite simulates the agent through the API.
- **API fuzzing and malformed-input campaigns.** Individual validation cases are covered, but there is no fuzzing or property-based test suite for oversized/malformed payloads or role-bypass attempts.
- **Coverage threshold.** The committed CI workflow runs tests, static checks, and dependency audits, but there is no coverage measurement or minimum threshold yet.

When adding tests, prefer the existing style: API flows through the shared helpers, temporary SQLite databases, monkeypatched external effects, and no fixed sleeps. Update this file when a gap above is closed.
