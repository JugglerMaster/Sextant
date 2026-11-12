# Server self-update and agent orchestration — plan

**Status:** V1–V3 implemented (daily cached release check, admin Updates panel, "Check now", manual self-update apply, staged venv, SHA-256/size verification, health check, atomic `current` switch, rollback, and a root-owned systemd restart handoff). V4+ (automatic agent-wheel publish and fleet rollout policy) is not implemented. Server updates are never automatic.
**Scope:** let the server check for a new release, notify admins, apply the server update on approval, then publish the matching agent artifact and tell agents to upgrade on check-in.

Related: [Self-update](SELF_UPDATE.md), [Package store](PACKAGE_STORE.md), [Installation](INSTALL.md), [Security](SECURITY.md), [Roadmap](ROADMAP.md).

---

## 1. Current state

- The server checks `SEXTANT_UPDATE_FEED` at most every `SEXTANT_UPDATE_CHECK_HOURS` (default **24**, range 1–168), caches the result, and exposes it to admins only.
- Admins see an Updates panel on the Security page and a login banner with **Check now**, **Update now**, and **Release notes** when a newer release is available.
- "Update now" downloads the server wheel with the Python standard library, enforces the configured size limit, verifies the release SHA-256 and exact size, stages `/opt/sextant/releases/<version>/venv`, installs the wheel with dependencies, and runs `sextant --help` plus an import/version check.
- On success the stable `/opt/sextant/current` symlink is switched atomically, the previous target is recorded in `$SEXTANT_DATA_DIR/update-state.json`, and a restart trigger is written for a root-owned systemd path unit. The request returns `restart_required`/`restarting`; the updater never calls `systemctl` directly.
- **Rollback** repoints `current` to the recorded previous release and requests the same restart.
- A release without a SHA-256 digest is still shown, but Apply is disabled with an explanation. A release without a server wheel, size, or download URL is likewise not applicable.
- The server and agent ship from the **same distribution** (`sextant_control` + `sextant_agent`, shared version). A server release therefore contains the matching agent code.
- The package store and agent self-update plans provide blob storage, hash-verified manifests, and the agent pull/verify/switch path. Tier 0 integrity is the current scope; signatures are deferred.

---

## 2. The two update planes

| Plane | Who runs it | Trigger | Mechanism |
|---|---|---|---|
| **Server** | Admin | Login notice + “Update now” | Server downloads a release, verifies, stages, switches, restarts itself |
| **Agent** | Server policy | Heartbeat | Agent pulls the matching wheel from the store and self-updates |

Order of operations:

```text
GitHub / release feed
        │  new server version
        ▼
server checks + notifies admins
        │  admin clicks "Update now"
        ▼
server updates itself (staged, verified, rollback)
        │  on success
        ▼
server publishes the matching agent wheel to the store
        │
        ▼
update policy targets agents (manual, canary, or auto)
        │
        ▼
agents see the directive on their next heartbeat and self-update
```

Updating the server first is intentional: the new server advertises the new agent version and understands any protocol changes. Agents remain on the previous version until the policy says otherwise.

---

## 3. Goals and non-goals

### Goals

- Check for server updates from a configurable feed (GitHub Releases API or a static JSON feed).
- Notify admins on login and in a persistent banner; never update silently by default.
- Show version, changelog, size, SHA-256, and compatibility.
- “Update now” stages, verifies, health-checks, switches, and restarts with rollback.
- On success, publish the bundled agent wheel to the store and set an agent update policy.
- Support canary/staged agent rollout, pause, yank, and rollback.
- Work offline/air-gapped through a manual upload or an internal feed.
- Audit every check, approval, download, switch, rollback, and agent directive.

### Non-goals (v1)

- Silent unattended server updates (opt-in later, if ever).
- Downgrading the database automatically.
- Updating the OS or system packages.
- Multi-node/HA rolling upgrades (single node only).
- Updating agents before the server (server-first ordering is required).

---

## 4. Server release feed

Two supported sources:

### A. GitHub Releases API (default for this project)

- Configure `SEXTANT_UPDATE_FEED=github:<owner>/<repo>`.
- The server calls `GET /repos/{owner}/{repo}/releases?per_page=20` and selects releases by channel/tag pattern.
- Prefer **release assets** (wheel, checksums, manifest) over source tarballs. This feed is for the **server release only**; app packages are never downloaded from GitHub.
- Use an optional `SEXTANT_GITHUB_TOKEN` for private repos and higher rate limits. Public GitHub allows 60 requests/hour unauthenticated; authenticated allows 5,000/hour.
- Poll at most every `SEXTANT_UPDATE_CHECK_HOURS` (default 24, range 1–168), plus once at startup. The admin **Check now** action forces a fresh check without waiting for the cache interval.
- Cache the result; do not call GitHub on every login.

### B. Static feed

- `SEXTANT_UPDATE_FEED_URL=https://ops.example.com/updates/server.json`
- The feed is a small JSON document listing versions, channels, artifacts, SHA-256, changelog, and `min_version`.
- Works on restricted networks and avoids GitHub dependency; can be served by the store or a static web server.
- Verify the artifact SHA-256 and size before staging. The feed itself can be allowlisted/pinned; Tier 1 feed signing is deferred.

### Why not a GitHub “release importer” for this?

An importer copies GitHub release assets into the store automatically. That is useful for mirroring, but the server update check only needs **metadata** and a **verified artifact**, not a full import. Keeping the check read-only avoids GitHub being able to publish arbitrary code directly into the store. The admin approval step remains the gate.

---

## 5. Admin notification and approval

### On login

If a newer release exists, show an admin-only banner/notice:

```text
Sextant 0.4.0 is available (you are on 0.3.2).
Released 2026-11-20 · stable · 4.2 MB · SHA-256 verified
Changelog: …
[View details] [Update now] [Remind me later] [Skip this version]
```

- Only `admin` sees the action. Operators/viewers see an informational badge at most.
- Show the current version, target version, channel, release notes, and whether the artifact hash verified.
- “Remind me later” snoozes for a configurable period; “Skip this version” pins the current version until the next release.
- If the feed is unreachable, show a neutral “update check failed” state, not an error on every page.

### Pre-update checks

- Disk space for two releases plus backup headroom.
- Database backup (SQLite `.backup`) and a copy of `secret.key`/environment.
- Confirm the running server is idle enough (no long command queue activity).
- Verify artifact SHA-256 and size **before** staging.
- Check `min_version` and Python compatibility.
- Show the exact version and a maintenance-window note; require explicit confirmation.

### Update execution (implemented)

1. The admin confirms **Update now** with the exact target version.
2. `POST /api/update/apply` re-checks the feed and rejects a target that is not the latest, already current, missing a server wheel, missing a SHA-256 or size, or larger than `SEXTANT_UPDATE_DOWNLOAD_MAX_MB` (default 256 MB).
3. The artifact is downloaded with the Python standard library into a temporary directory. The download is bounded by the configured limit, and redirects to unexpected schemes, credentials, or hosts are rejected. GitHub asset redirects are limited to the known GitHub asset hosts.
4. Exact size and SHA-256 are verified before staging.
5. A new venv is created at `SEXTANT_UPDATE_RELEASE_ROOT/<version>/venv` (default `/opt/sextant/releases/<version>/venv`) and the wheel is installed **with dependencies**.
6. The staged `sextant --help` and an import/version check must succeed. On failure the staged release directory is removed and the running release is untouched.
7. The previous `current` target is recorded in `$SEXTANT_DATA_DIR/update-state.json`, then `/opt/sextant/current` is replaced atomically with a temporary symlink plus `os.replace`.
8. A restart request is written atomically to `SEXTANT_UPDATE_RESTART_TRIGGER` (default `/var/lib/sextant/restart-request`). The API returns `restart_required: true`, `restarting: true`, and instructions. The server process does not call `systemctl` and does not wait for the restart.
9. The root-owned path unit consumes the trigger and restarts `sextant.service` after a short delay so the HTTP response can be delivered.

The current implementation does **not** take an automatic database backup, does not run migrations separately, and does not automatically roll back after a bad restart. Take a backup before applying an update if the release contains a schema change.

### Failure and rollback

- Download, size, SHA-256, staging, or health-check failure: the staged release is deleted and the running release continues.
- **Rollback** in the Updates panel (or `POST /api/update/rollback`) repoints `current` to the previous target recorded by the last apply and requests a restart. It rejects a missing/out-of-root target.
- Database migration failure: restore the pre-update backup manually; this remains the one case where automatic rollback is risky.
- Previous release directories are kept for rollback. Prune them manually after a soak period.

---

## 6. Restart mechanics

The server cannot cleanly restart itself from inside its own process/service cgroup: `systemctl restart` kills the updater before it can finish. This implementation uses a **root-owned restart handoff**:

- The unprivileged server writes `/var/lib/sextant/restart-request` (atomic temp file plus rename).
- `deploy/sextant-update-restart.path` watches that file and starts `sextant-update-restart.service`.
- `deploy/sextant-update-restart.sh` is installed as `/usr/local/sbin/sextant-update-restart` (root-owned, mode 0755). It removes the trigger and runs `systemctl restart sextant.service`.
- The service unit runs `ExecStartPre=/bin/sleep 2` so the apply/rollback HTTP response can reach the browser.
- `deploy/sextant.service` runs `/opt/sextant/current/bin/sextant` and sets `TimeoutStopSec=15`; uvicorn uses a 10-second graceful shutdown timeout by default (`SEXTANT_GRACEFUL_SHUTDOWN_SECONDS`) so open SSE connections do not block the restart indefinitely.

nginx keeps serving while the app restarts (502 briefly); use a short maintenance page if desired.

### Manual deployment steps for an existing install

Run these once on an existing 0.5.1 server before using **Update now**:

```bash
# 1. Install the current symlink layout. Keep the existing venv as the first release.
sudo install -d -o sextant -g sextant -m 0755 /opt/sextant/releases
sudo install -d -o sextant -g sextant -m 0755 /opt/sextant/releases/0.5.1
sudo ln -sfn /opt/sextant/.venv /opt/sextant/releases/0.5.1/venv
sudo ln -sfn /opt/sextant/releases/0.5.1/venv /opt/sextant/current

# 2. Let the unprivileged server stage releases and replace the symlink.
#    The service already has ReadWritePaths=/opt/sextant and /var/lib/sextant.
sudo chown sextant:sextant /opt/sextant /opt/sextant/releases

# 3. Install the restart handoff (root-owned).
sudo install -m 0755 /opt/sextant/deploy/sextant-update-restart.sh \
  /usr/local/sbin/sextant-update-restart
sudo install -m 0644 /opt/sextant/deploy/sextant-update-restart.service \
  /etc/systemd/system/sextant-update-restart.service
sudo install -m 0644 /opt/sextant/deploy/sextant-update-restart.path \
  /etc/systemd/system/sextant-update-restart.path
sudo systemctl daemon-reload
sudo systemctl enable --now sextant-update-restart.path

# 4. Update the main unit to use current and the graceful stop timeout.
sudo install -m 0644 /opt/sextant/deploy/sextant.service /etc/systemd/system/sextant.service
sudo systemctl daemon-reload
sudo systemctl restart sextant

# 5. Verify.
readlink -f /opt/sextant/current
sudo systemctl status sextant-update-restart.path --no-pager
```

If `/opt/sextant` must remain root-owned, stage and switch through an equivalent root-owned helper instead; the built-in apply path assumes the service user can write the release root and the `current` symlink parent.

---

## 7. Agent orchestration after a server update

Because server and agent share a version, a successful server update gives the server the matching agent wheel.

1. **Publish** the bundled `sextant-<version>-py3-none-any.whl` as a store release (`artifact_type=agent-wheel`). Tier 0 uses hash/size verification; no signing key is involved.
2. **Create or update** an agent update policy:
   - default: `manual` (admins see “agent update available” and can approve per device/tag);
   - optional: `auto` for a canary tag, then a percentage, then the fleet;
   - channel `stable`/`beta`;
   - maintenance windows.
3. **Advertise** the target version in the heartbeat response (see [SELF_UPDATE.md](SELF_UPDATE.md)).
4. **Agents** download, verify, stage, health-check, switch, restart, and report. Old agents that do not support self-update are unaffected and must be updated manually once.
5. **Compatibility:** the server should support at least the previous agent protocol; do not require an agent upgrade for the server to function. Set a `min_version` only when a protocol change requires it, and roll out in stages.
6. **UI:** show fleet agent versions, pending upgrades, canary status, failures, and rollback.

Ordering rules:

- server update → publish agent wheel → offer agent update;
- never offer an agent version newer than the server that serves it;
- if the server rolls back, pause agent updates and optionally roll agents back to the matching version.

---

## 8. Security

- **Admin approval by default.** No silent server updates. “Auto” is opt-in and scoped to a channel/maintenance window.
- **Verify before staging.** The server computes and checks SHA-256 + size. In Tier 0 the server/feed is the trust root; a compromised server or GitHub release can serve different bytes. Admin approval and backups are the mitigation.
- **GitHub is only a server-release feed.** It is not an app source and cannot publish into the store. A compromised GitHub release still requires an admin to approve the update. Tier 1 signatures (deferred) would remove that trust.
- **Protect the server.** Admin-only update actions, least privilege, TLS, audit, and a tested backup/restore path.
- **Back up before switching.** Database + secret + env. Test restore.
- **Least privilege.** The updater runs as root only for the switch/restart; the server remains an unprivileged user.
- **Audit.** Check, notify, snooze, skip, approve, download, verify, stage, switch, restart, rollback, agent publish, agent policy change.
- **Rate limits and caching.** Do not hammer GitHub; cache checks; handle rate-limit responses gracefully.
- **Offline mode.** Allow a manual upload or internal feed; never block startup because the feed is unreachable.

---

## 9. Phases

| Phase | Deliverable | Effort | Risk |
|---|---|---|---|
| V1 | Release feed reader + admin login banner; version/changelog/hash display; “notify only” — **implemented** | S | low |
| V2 | Artifact download/verify (SHA-256); staged venv; health check; manual “Update now” with rollback — **implemented** | M–L | high |
| V3 | Updater helper/systemd unit; backup/restore integration; result reporting — **restart handoff implemented; automatic backup/result soak not implemented** | M | high |
| V4 | On successful update, publish the matching agent wheel to the store; agent policy UI — not implemented | M | medium |
| V5 | Canary/percentage agent rollout, pause/yank/rollback, scheduled maintenance windows | M | medium |
| V6 | Optional server auto-update (opt-in), Tier 1 signing/key rotation, static feed, air-gapped bundle | L | high |

Start with V1: it is low risk and immediately useful even before automated server updates exist.

---

## 10. Open questions

1. Default feed: GitHub Releases API or a static feed? (GitHub is convenient; static is more self-contained.)
2. Should a server update ever be automatic? (Recommend: no by default, opt-in later.)
3. Is a brief 502 during restart acceptable, or do you want an nginx maintenance page?
4. Do you want the updater to back up the database automatically, or require an external backup first?
5. Agent rollout default after a server update: manual, canary tag, or auto?
6. How many previous server and agent releases should be retained for rollback?
7. GitHub authentication: public repo only, or a token for private releases?
8. Should “Skip this version” be per admin or global?

---

## 11. Alternatives

| Option | Notes |
|---|---|
| **Manual server update** (current) | Safest and simplest; the UI can still notify and link to instructions |
| **Ansible/CI-driven server update** | Mature, auditable, no self-modifying server process; good if you already have automation |
| **Watchtower/container recreate** | Only if the server is containerized; not the current deployment |
| **Distro package (deb/rpm)** | Native upgrade/signature story, but requires packaging and repo hosting; still needs a restart/rollback design |
| **GitHub release importer** | Convenient mirroring, but not required for server updates; adds a trust path from GitHub into the store |
