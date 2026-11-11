# UI status and refinements

The dashboard is a vanilla HTML/CSS/JS single page backed by the FastAPI API. It is optimized for a small fleet (10–20 machines), not a dense enterprise console.

---

## Current shape

- **Fleet** — stats, search, status/profile/tag filters, agent cards with CPU/memory/disk/GPU/uptime, **heartbeat**, and **inventory updated**, click/Enter to open a drawer.
- **Device drawer** — Overview (with 24h sparklines), Disks, GPU, Services, Startup, Packages, Remote, History.
- **Packages** — inventory with interactive table, **desired-state rule editor** (admin), drift summary, Sync now.
- **Profiles** — create/edit/reset/delete profiles, modules, action gates, cadences, tag assignments.
- **Remote install** — Linux SSH install jobs with test/install/log.
- **Wake targets**, **Enrollment**, **Users**, **Audit**, **Security**.
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

Recommended display:

```text
Heartbeat: 12s ago
Inventory updated: 3h ago · 24h cadence
```

Do not label an old inventory as “stale” solely because the heartbeat is recent — they run on different cadences. Staleness rules:

- **Heartbeat stale** when `now - last_seen > max(2 × heartbeat_seconds, offline threshold)`.
- **Inventory stale** when `now - inventory_updated > 1.5 × profile package_inventory_seconds` (or service cadence for services).
- Show the cadence next to the age so an old timestamp is understandable.

Current state: the Packages tab, agent cards, and drawer Overview show **Heartbeat** and **Inventory updated**. The service-inventory timestamp is stored in `agents.service_inventory_at`. A cadence-aware stale badge is still pending.

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
- History search and command count.
- Services search, status filter, row count, and bounded show-more paging.
- Users search (username, display name, email, role, source) with a visible row count.
- Fleet filters: status, profile, and tag (persisted in local storage).
- Named fleet saved views stored in local storage.
- 24-hour CPU/memory/disk/GPU sparklines in the drawer.
- Drawer focus trap, focus return to the trigger, and sortable-header `aria-sort` markers.
- Cadence-aware stale badges for heartbeat and inventory age.
- Alert center with acknowledge, snooze, and clear actions for every current health alert.
- Bulk selection with refresh-telemetry, wake, reboot, shutdown, service, and profile actions.
- CSV export for the fleet and per-agent package inventory.
- Trends tab with 24h/7d detail charts and a merged command/alert timeline.
- Light/dark theme toggle and a Ctrl/Cmd+K command palette.
- Live dashboard updates over SSE with polling fallback.

---

## Remaining UI fixes

### 1. Table controls

Implemented for Packages, Startup, and Audit with search, sortable columns, row counts, and pagination. Services has search, a status filter, counts, and bounded show-more paging. Users has live search and a row count. History has search and counts. Remaining:

- full pagination/virtualization for Services and Users;
- sort/pagination for History command details;
- persistent filters per tab;
- CSV/JSON export for tables.

Priority: History export next.

### 2. Data freshness

- Compact **Heartbeat / Inventory updated** is on cards and drawer Overview, with profile cadence and stale badges.
- Service inventory timestamp is stored separately (`agents.service_inventory_at`).
- Remaining: show “data as of” in History and a dedicated stale-incident view.

### 3. Fleet UX

- Status/profile/tag filters, persistence, and named saved views are implemented.
- Bulk select with refresh-telemetry, wake, reboot, shutdown, service, and profile actions is implemented.
- Alert center lists current alerts with acknowledge/snooze/clear and links to the machine.
- Remaining: profile badges.

### 4. History and charts

- 24-hour CPU/memory/disk/GPU sparklines are implemented.
- The Trends tab adds 24h/7d detail charts for CPU, memory, disk, GPU utilization, and GPU temperature, plus a merged command/alert timeline.
- Remaining: explicit gaps for offline periods and a “what changed” view.

### 5. Accessibility

- Drawer focus trap, focus return, and sortable-header `aria-sort` are implemented.
- `aria-live` status regions, a light/dark theme toggle, and a Ctrl/Cmd+K command palette are implemented.
- Remaining: a full high-contrast theme and a formal contrast audit.

### 6. Live behavior

- The dashboard streams `GET /api/events` over `EventSource` and falls back to the previous 10-second dashboard polling if SSE is unavailable or fails. The stream closes while the document is hidden and reconnects on visibility.
- Command waits use `GET /api/commands/{id}/events` when available, with the existing poll as fallback. The terminal appends `output` events live, styling stdout and stderr separately. On reconnect the server replays stored chunks (bounded); the UI de-duplicates by `(stream, seq)`. When the terminal `command` event arrives, the canonical final stdout/stderr is rendered without repeating text already streamed. Chunk delivery is best-effort and the final result remains authoritative.
- Remaining: virtualization for very large tables (100+ agents).

---

## Status

| Area | Status |
|---|---|
| Fleet cards and drawer | Implemented |
| Package inventory + rule editor | Implemented |
| Freshness labels in Packages | Implemented |
| Freshness on cards/drawer | Implemented with cadence + stale badges |
| Fleet status/profile/tag filters | Implemented and persisted; named saved views in local storage |
| Table sort/pagination | Implemented for Packages, Startup, Audit; Services bounded show-more; Users search/count; History search/count |
| Charts/sparklines | 24h sparklines plus 24h/7d detail charts and a command/alert timeline |
| Accessibility pass | Implemented: focus trap/return, aria-sort, live regions, theme toggle, command palette; formal contrast audit pending |
| Alert center | Implemented with acknowledge/snooze/clear |
| Bulk actions | Implemented: refresh, wake, reboot, shutdown, service, profile |
| Saved views/export | CSV export and named fleet views stored in local storage |
| SSE/WebSocket | Dashboard SSE with polling fallback; command status/result SSE with live stdout/stderr chunks, replay, and poll fallback |
