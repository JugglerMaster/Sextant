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
- Fleet filters: status, profile, and tag (persisted in local storage).
- 24-hour CPU/memory/disk/GPU sparklines in the drawer.
- Drawer focus trap, focus return to the trigger, and sortable-header `aria-sort` markers.
- Cadence-aware stale badges for heartbeat and inventory age.
- Alert center listing every current health alert with a link to the machine.
- Bulk selection with refresh-telemetry and wake actions.
- CSV export for the fleet and per-agent package inventory.

---

## Remaining UI fixes

### 1. Table controls

Implemented for Packages, Startup, and Audit with search, sortable columns, row counts, and pagination. History has search and counts. Remaining:

- pagination/virtualization for Services and Users;
- sort/pagination for History command details;
- persistent filters per tab;
- CSV/JSON export for tables.

Priority: Services and Users last; History export next.

### 2. Data freshness

- Compact **Heartbeat / Inventory updated** is on cards and drawer Overview, with profile cadence and stale badges.
- Service inventory timestamp is stored separately (`agents.service_inventory_at`).
- Remaining: show “data as of” in History and a dedicated stale-incident view.

### 3. Fleet UX

- Status/profile/tag filters and persistence are implemented.
- Bulk select with refresh-telemetry and wake is implemented.
- Alert center lists current alerts and links to the machine.
- Remaining: saved named views, profile badges, alert acknowledge/snooze, and bulk power/service/profile actions.

### 4. History and charts

- 24-hour CPU/memory/disk/GPU sparklines are implemented.
- Remaining: larger detail charts, explicit gaps for offline periods, incident timeline, and “what changed” view.

### 5. Accessibility

- Drawer focus trap, focus return, and sortable-header `aria-sort` are implemented.
- Remaining: `aria-live` for fresh metrics/alerts, keyboard shortcuts/command palette, light/high-contrast theme, and a contrast pass.

### 6. Live behavior

- Remaining: replace 10-second full-dashboard polling with SSE/WebSocket updates, stream command output, and virtualize very large tables for 100+ agents.

---

## Status

| Area | Status |
|---|---|
| Fleet cards and drawer | Implemented |
| Package inventory + rule editor | Implemented |
| Freshness labels in Packages | Implemented |
| Freshness on cards/drawer | Implemented with cadence + stale badges |
| Fleet status/profile/tag filters | Implemented and persisted |
| Table sort/pagination | Implemented for Packages, Startup, Audit; partial for History/Services/Users |
| Charts/sparklines | 24h sparklines implemented; detail charts pending |
| Accessibility pass | Partial (focus trap/return, aria-sort); remaining items listed |
| Alert center | Implemented (acknowledge/snooze pending) |
| Bulk actions | Implemented (refresh/wake); power/service/profile bulk pending |
| Saved views/export | CSV export implemented; named saved views pending |
| SSE/WebSocket | Pending |
