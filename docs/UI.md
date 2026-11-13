# UI status and refinements

The dashboard is a vanilla HTML/CSS/JS single page backed by the FastAPI API. The fleet grid is incremental and bounded so it stays responsive with hundreds of agents without rebuilding the whole DOM on every update.

---

## Current shape

- **Fleet** — stats, search, status/profile/tag filters, keyed incremental agent cards with CPU/memory/disk/GPU/uptime, **heartbeat**, and **inventory updated**, click/Enter to open a drawer. The first pass renders 60 cards with a **Show more** control; offscreen cards use `content-visibility`.
- **Saved views** — per-user server-side views (`GET`/`POST`/`DELETE /api/saved-views`) with a localStorage import/fallback only.
- **Bulk actions** — one `POST /api/agents/bulk` request with a per-agent queued/sent/updated/failed summary, including failed machine names and messages.
- **Device drawer** — Overview (with 24h sparklines), Disks, GPU, Services, Startup, Packages, Remote, History, Trends. The drawer can be minimized into a bottom tray; multiple machines keep their tab and terminal state across navigation, and each tray chip restores or closes independently. Admins also get a **Join domain** action that queues an audited agent-side Windows `Add-Computer` or Linux `realm`/`adcli` join, with optional OU, computer name, prerequisite installation, reboot, and a read-only status check. The password is write-only and never persisted. See [DOMAIN_JOIN.md](DOMAIN_JOIN.md).
- **Discovery** (D1–D2) — top-level page for signed-in users. The main **Discover** tab keeps the scan and inventory workflow: summary counts by state/type/segment, the **Changed** count, filters for state (including **Changed** and **Conflict**), type, segment, vendor, no-agent, and admin-ports-open; a search box that matches the whole device row, a table with IP, MAC/vendor, hostname, type, open ports, last seen, linked agent, tags, and changed-state detail. **Topology** holds segments, **Imports** holds external scan/lease ingestion, and each connector has its own admin tab (**SNMP**, **UniFi**, **RouterOS**, **OPNsense**, **Proxmox**, **ESXi**) with its own settings and Test connection/Poll now actions. The device drawer shows identity, identity-change evidence, evidence, ports/services, first/last seen, deduplicated change events, conflicts, and linked agent/wake target, with operator tag/state controls and admin delete. Admins also get a **Remote install** action that opens a prefilled onboarding dialog: Linux uses the configured SSH key, Windows uses WinRM over HTTPS with a one-time password, and the trigger starts the normal remote-install job and opens its live log. The scan panel selects a source (This server or an eligible relayed Linux agent), an allowlisted scope, and configured methods, shows progress, supports cancellation and scan history, and links to the audit log. Agent sources are labelled with the reported IP/VLAN segment; relay-disabled and no-eligible-agent states explain the `SEXTANT_DISCOVERY_AGENT_RELAY`, profile module, per-agent policy, and local agent flag requirements.

D4/D5 adds an **Exposure only** filter, **Identity conflicts** filter and badges, exposure/conflict summary cards, a per-segment topology list with managed/unmanaged counts and gateway hints, and wake-status badges. The device drawer shows conflict evidence, wake status/verification time, a **Verify wake** action, and **Promote**. Promotion offers Wake target, Linux agent install, Windows agent install, or linking an enrolled agent. Install promotion shows the one-time command with a copy button, expiry, prerequisites, and the Windows manual-install plan link; the command/token is cleared from the dialog when it closes. Export CSV/JSON buttons are admin-only and reuse the active filters.

D6 adds an admin-only **Import** panel (format, file/paste, preview counts, confirm, import history) and an admin-only **Integrations** panel for SNMP and UniFi/RouterOS/OPNsense/Proxmox/ESXi. Integration forms show enabled state, config, write-only secret fields, test/poll actions, redacted last errors, SNMP backend status, and explicit **unverified live API** / **fixture-only** badges. Secrets are never rendered back after save. Viewers remain read-only; operators can scan/promote; admin is required for import, integrations, export, and settings. Disabled, unacknowledged, and empty-allowlist states explain the environment flag, acknowledgement, and CIDR requirements. Admins record the explicit **I am authorized to scan these networks** acknowledgement on their first enable.
- **Settings** — a signed-in **Profile** tab for local display name/email and a link to password/session controls, plus admin tabs for **Server**, **Domain & directory**, **Domain login**, and **SSH**. Server settings cover public URL, allowed hosts, session lifetime, offline threshold, enrollment token lifetime, secure cookies, and proxy trust, with a **Restart server** action for startup-only changes. Domain settings configure the AD/Samba/LDAP server, bind account, TLS, user filter, attribute mapping (username, display name, email, group), group-to-role mapping, and a **Test directory** lookup. Domain login saves SSH/PAM/home/sudo/short-name toggles plus a **Protected local users** list and pushes the policy to selected Linux agents, with per-host results in command history. SSH settings choose a Sextant-managed uploaded key or a local system user's key/config and show the effective path, readability, and fingerprint. Secrets are write-only. See [SYSTEM_SETTINGS.md](SYSTEM_SETTINGS.md).
- **Docker Hosts** (D2–D5) — host list of agents reporting Docker. Docker collection can be enabled per agent from **Edit policy** with a server-pushed setting that overrides the local default and profile module; exec, Compose, and cleanup have separate server toggles. Each selected host has Overview, Containers, Images, Volumes, Networks, and Stacks tabs from cached inventory, a freshness badge, inventory revision, unavailable reasons, **Refresh now**, and a 12-second visibility-aware auto-refresh. Containers support inspect, logs, one-shot exec, interactive console, clone, start/stop/restart/pause/rename/update/remove with confirmations. Compose config-hash drift shows a compact badge in Containers, Stacks, and deployment history. Images/volumes/networks support guarded removal, and Overview has cached cleanup cards with per-scope confirmation and reclaimed-size results. The Stacks tab separates active Sextant-managed deployments from read-only external Compose projects.
- **Compose Files** (D3/D5) — stack list, admin flat-file editor with compose.yaml/.env/override files, immutable revision history, multi-host deploy picker (up to 20 hosts with per-host results), deployment history/logs with down/restart/pull, and admin storage settings showing whether the path is fixed by environment. Operators can deploy but cannot edit or read `.env`; viewers are metadata-only.
- **Container console** (D4) — operator Console action on running containers, setup for shell/user/workdir, warning banner, ANSI-cleaned terminal output, keystroke forwarding and input row, paste/copy, resize, idle countdown, explicit close confirmation, focus return, and mobile layout. No transcript is retained.
- **Packages** — inventory with interactive table, **desired-state rule editor** (admin), drift summary, Sync now.
- **Profiles** — create/edit/reset/delete profiles, modules, action gates, cadences, tag assignments.
- **Remote install** — Linux key-based SSH and Windows WinRM-over-HTTPS install jobs with OS-aware prerequisite test, one-time password field (Windows), install/log/history, and admin-only access.
- **Wake targets**, **Users**, **Audit**, **Security**.
- **Enrollment** — admins generate a one-time code and see Linux and Windows installer commands. Before generation the commands show `insert enrollment code here`; generating a token inserts the code into the hint and both commands, with copy buttons. The code is shown once, and the history lists active, used, and expired tokens.
- Update banner, toasts, native dialogs, dark theme, responsive breakpoints.

---

## Freshness terminology

Use these consistently in the UI and API:

| Term | Meaning | Source |
|---|---|---|
| **Heartbeat** | Last successful agent contact/liveness. | `agents.last_seen` |
| **Telemetry updated** | Last fast metrics sample (CPU, memory, disks, GPU). Normally the heartbeat. | heartbeat / `telemetry_samples.at` |
| **Inventory updated** | Last service/startup/GPU-process inventory snapshot. | `agents.service_inventory_at` |
| **Package inventory updated** | Last package snapshot or delta acknowledgement. | `package_inventory.last_seen` / `package_inventory_state.updated_at` |
| **Docker inventory updated** | Last uploaded Docker inventory snapshot. | `docker_hosts.inventory_at` / `collected_at` |

Recommended display:

```text
Heartbeat: 12s ago
Inventory updated: 3h ago · 24h cadence
```

Do not label an old inventory as “stale” solely because the heartbeat is recent — they run on different cadences. Staleness rules:

- **Heartbeat stale** when `now - last_seen > max(2 × heartbeat_seconds, offline threshold)`.
- **Inventory stale** when `now - inventory_updated > 1.5 × profile package_inventory_seconds` (or service cadence for services).
- Show the cadence next to the age so an old timestamp is understandable.

Current state: the Packages tab, agent cards, and drawer Overview show **Heartbeat** and **Inventory updated**. The service-inventory timestamp is stored in `agents.service_inventory_at`. The Docker Hosts page shows Docker inventory age, revision, and a stale badge using `docker_hosts.inventory_at` and `SEXTANT_DOCKER_INVENTORY_STALE_SECONDS`; successful Docker mutations request a fresh inventory upload before the UI reloads. While Docker Hosts is visible and no Docker dialog/console/action is active, the selected host refreshes every 12 seconds and pauses while the document is hidden.

---

## Completed in the current UI pass

- Package rule editor:
  - add/update/remove `present`, `absent`, `latest`, and pinned version rules;
  - manager selection (`apt`, `dnf`, `winget`, `choco`);
  - validation for package/version and required pinned version;
  - Save rules (`PUT /api/agents/{id}/packages`);
  - Sync now and drift summary.
- Package inventory filter and row count.
- Heartbeat + inventory updated labels in the Packages tab, agent cards, and drawer Overview.
- Profiles editor and tag assignments.
- Remote install page.
- Interactive tables (Packages, Startup, Audit) with search, sort, row counts, and pagination.
- History server-side search, status filter, page size, Previous/Next paging, X-Y of Z count, and filter-aware CSV export.
- Services search, status filter, row count, and bounded show-more paging.
- Users search (username, display name, email, role, source) with a visible row count.
- Fleet filters: status, profile, and tag (persisted in local storage).
- Named fleet saved views stored per user on the server; legacy localStorage views are migrated once without duplicating names and remain a fallback when the API is unreachable.
- 24-hour CPU/memory/disk/GPU sparklines in the drawer.
- Drawer focus trap, focus return to the trigger, and sortable-header `aria-sort` markers.
- Cadence-aware stale badges for heartbeat and inventory age.
- Alert center with acknowledge, snooze, and clear actions for every current health alert, including incident first/last-seen timestamps, incident age, acknowledgement age, and an explicit “condition still active” label for acknowledged alerts.
- Bulk selection with refresh-telemetry, wake, reboot, shutdown, service, and profile actions, sent as one batch request and reported per agent.
- CSV export for the fleet, per-agent package inventory, and filtered command history.
- Trends tab with gap-aware 24h/7d detail charts, a persistent state-change timeline, and a What changed summary.
- Classic light/dark theme toggle, a Ctrl/Cmd+K search palette, and a dedicated admin Updates tab. An up-arrow shortcut appears in the topbar only when a newer server release is known.
- Live dashboard updates over change-driven SSE with polling fallback.
- D2 Docker Hosts actions with a shared accessible dialog, focused action/confirm flows, offline/gate-disabled states, cached cleanup cards, and an `aria-live` action/refresh status.
- D3 Compose Files views with admin editing, revision/deploy dialogs, deployment history, .env redaction for operators, storage settings, and managed-vs-external host stack display.
- D4 interactive console modal with PTY-backed terminal output, keystroke/input handling, resize, idle countdown, close confirmation, focus return, and no transcript.
- D5 Docker live refresh with revision/age display, drift badges, multi-host Compose deploy with a per-host summary, and a prefilled clone dialog limited to the safe allowlist with Compose drift acknowledgment.

---

## Remaining UI fixes

### 1. Table controls

Implemented for Packages, Startup, and Audit with search, sortable columns, row counts, and pagination. Services has search, a status filter, counts, and bounded show-more paging. Users has live search and a row count. History uses the paginated command API with debounced server-side search, a status filter, 25/50/100 page sizes, Previous/Next controls, an X-Y of Z count, and a CSV export that carries the current filters. Remaining:

- full pagination/virtualization for Services and Users;
- persistent filters per tab;
- CSV/JSON export for the remaining tables.

Priority: full pagination for Services and Users next.

### 2. Data freshness

- Compact **Heartbeat / Inventory updated** is on cards and drawer Overview, with profile cadence and stale badges.
- Service inventory timestamp is stored separately (`agents.service_inventory_at`).
- Remaining: show “data as of” in History and a dedicated stale-incident view.

### 3. Fleet UX

- Status/profile/tag filters, persistence, and per-user server-side saved views are implemented.
- Agent cards render incrementally by keyed agent id, reuse unchanged nodes, preserve selection/focus through SSE updates, and are bounded with Show more.
- Bulk select with refresh-telemetry, wake, reboot, shutdown, service, and profile actions is implemented through one batch request; results list counts and failed agent names/messages. Selection is preserved until the user clears it.
- Alert center lists current alerts with acknowledge/snooze/clear and links to the machine. Each row shows first seen, last seen, incident age, and (when acknowledged) the acknowledgement age with a “condition still active” label. A resolved-then-recurring alert is a new incident, so it renders as active and unacknowledged automatically.
- Remaining: profile badges.

### 4. History and charts

- Server updates live on their own admin tab, with Check now, Update now, Rollback, feed/hash state, and restart progress. The top-right refresh button was removed because fleet data already refreshes through SSE.

The History tab queries `GET /api/agents/{id}/commands` directly, so retained commands beyond the overview's latest-50 preview are reachable. Search is debounced and runs server-side; the status filter and page size (25/50/100) reset to the first page, Previous/Next preserve the current filters, refresh keeps the current page when possible, and Export CSV includes the active search/status filters. Command expansion, cancel, and retry remain available on each row.
- 24-hour CPU/memory/disk/GPU sparklines are implemented.
- The Trends tab adds 24h/7d detail charts for CPU, memory, disk, GPU utilization, and GPU temperature. Points are positioned by their real bucket timestamps, null buckets break the line, and offline intervals remain visible as gaps instead of being bridged.
- The Trends timeline merges retained commands with persistent `agent_events` and alert lifecycle entries, using real event timestamps. A compact **What changed** section summarizes the most recent service, package, version, profile, connectivity, and alert transitions in the selected window, with an explicit empty state when nothing changed.
- Real-browser tests exposed an SVG namespace bug that jsdom did not catch: charts and sparklines were created as HTML unknown elements, so axes and lines did not render in a real browser. The DOM helper now creates SVG nodes in the SVG namespace, and detail charts have an explicit CSS aspect ratio.

### 5. Accessibility

- Drawer focus trap, focus return, and sortable-header `aria-sort` are implemented.
- `aria-live` status regions, a Classic light/dark theme toggle, and a Ctrl/Cmd+K command palette are implemented.
- A Playwright + axe-core suite runs against real Chromium at desktop and a 390px mobile viewport in both themes. It covers login, the fleet dashboard, the drawer Overview/Trends tabs, command palette keyboard flow, and keyboard-only drawer opening with focus trap and focus return. Login, dashboard, drawer, and trends scans have no `serious` or `critical` axe violations in either theme.
- Contrast fixes from the browser pass: the dark theme's dim text and terminal chrome were lightened, the Classic light accent is Windows blue with flat white panels, dialogs have accessible names, progress bars have labels, and the agent card no longer nests a checkbox inside a fake button role (its title is now a real button).
- Remaining: a full high-contrast theme, Firefox/WebKit runs, zoom/reflow checks, and committed pixel baselines. Screenshots are currently review artifacts, not failing visual diffs.

### 6. Live behavior

- The dashboard streams `GET /api/events` over `EventSource`. The server sends an immediate snapshot, then a new dashboard event only when dashboard state changes (heartbeat, command, alert, agent/profile update), plus a roughly 10-second safety refresh for time-derived fields such as online/offline and server time. Clients at the same revision share one server-side build. If the stream fails, the UI falls back to the previous 10-second dashboard polling. If the server sends an `auth` event because the session was revoked, expired, disabled, or its version changed, the UI closes the stream without reconnecting and shows the login view. The stream closes while the document is hidden and reconnects on visibility.
- Command waits use `GET /api/commands/{id}/events` when available, with the existing poll as fallback. The terminal appends `output` events live, styling stdout and stderr separately. On reconnect the server replays stored chunks (bounded); the UI de-duplicates by `(stream, seq)`. When the terminal `command` event arrives, the canonical final stdout/stderr is rendered without repeating text already streamed. Chunk delivery is best-effort and the final result remains authoritative.
- The fleet grid grows in bounded increments (60 cards initially, then Show more) and uses `content-visibility` for offscreen cards. Remaining: full row virtualization for very large tables.

---

## Status

| Area | Status |
|---|---|
| Fleet cards and drawer | Implemented |
| Package inventory + rule editor | Implemented |
| Freshness labels in Packages | Implemented |
| Freshness on cards/drawer | Implemented with cadence + stale badges |
| Fleet status/profile/tag filters | Implemented and persisted; named saved views stored per user on the server (localStorage migration/fallback) |
| Table sort/pagination | Implemented for Packages, Startup, Audit, and History; Services bounded show-more; Users search/count |
| Charts/sparklines | 24h sparklines plus gap-aware 24h/7d detail charts, persistent event timeline, and What changed summary; SVG rendering now verified in a real browser |
| Accessibility pass | Implemented and browser-tested: focus trap/return, aria-sort, live regions, theme toggle, command palette, labelled dialogs/progress bars, and axe scans with no serious/critical violations in both themes |
| Alert center | Implemented with acknowledge/snooze/clear, incident first/last-seen and age, acknowledgement age, and recurrence reset |
| Bulk actions | Implemented through one batch request: refresh, wake, reboot, shutdown, service, profile, with per-agent results |
| Saved views/export | CSV export (fleet, packages, filtered command history) and per-user server-side named fleet views |
| SSE/WebSocket | Change-driven dashboard SSE with shared snapshot cache, periodic safety refresh, session revalidation, and polling fallback; command status/result SSE with live stdout/stderr chunks, replay, and poll fallback |
