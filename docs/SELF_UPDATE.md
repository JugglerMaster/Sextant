# Built-in agent self-update — plan

**Status:** U1–U2 implemented, plus the Windows switch/restart path. The server advertises a store agent wheel; Linux and Windows `sextant-agent update` verify, stage, health-check, and switch, and `--restart` restarts the local service/task. Check-in notices remain notify-only; automatic background apply and a watchdog rollback helper are not enabled.
**Scope:** update enrolled Linux and Windows agents from artifacts published by the Sextant server, with **Tier 0 integrity** (TLS + agent auth + SHA-256 + size), staged rollout, health checks, and rollback. Signatures are deferred; the manifest fields remain reserved.

Related: [Installation](INSTALL.md), [Agents](AGENTS.md), [Architecture](ARCHITECTURE.md), [Server update orchestration](SERVER_UPDATE.md), [Package store](PACKAGE_STORE.md), [Roadmap](ROADMAP.md).

---

## 1. Current state

- The agent reports `agent_version` on every heartbeat (`sextant_agent/collectors.py`), and the server stores/displays it (`sextant_control/server.py`, `static/app.js`).
- There is **no update channel**: the heartbeat response contains policy and queued commands, not releases.
- There is **no file-transfer or package-hosting feature**.
- Updates are manual: copy a wheel/source tree to the endpoint, `pip install --no-deps --upgrade`, restart systemd/scheduled task.
- The server can queue a shell command that performs those steps, but that is operator-driven remote execution, not managed updating — and restarting the agent from inside the command usually prevents the result from being posted.
- The roadmap lists auto-update with staged rollout and rollback (`docs/ROADMAP.md`, agent lifecycle). Signing is a later Tier 1.

---

## 2. Goals and non-goals

### Goals

- Publish a new agent version once and let agents pull it over their existing outbound HTTPS channel.
- Verify integrity (SHA-256 + size) before executing anything. Authenticity (signatures) is Tier 1 and deferred.
- Update atomically, keep the previous version, and roll back on failure.
- Report success/failure/version back to the server and the audit log.
- Support staged rollout: per agent, per tag, percentage, maintenance window, pause, yank.
- Work with the current deployment layout: Linux venv + systemd; Windows venv + scheduled task.
- Keep the agent stdlib-only and the server single-node.

### Non-goals (v1)

- Updating the **server** itself in this document; see [SERVER_UPDATE.md](SERVER_UPDATE.md). The server-first orchestration (server update → publish matching agent wheel → agent rollout) is described there.
- Distributing arbitrary binaries or third-party packages.
- Silent forced updates without an explicit policy.
- Replacing OS package managers or configuration management.
- A full artifact repository UI with channels, promotion, and retention beyond what is needed for agents.

---

## 3. Threat model

| Threat | Control |
|---|---|
| Server or transport compromise pushes malicious code | **Not covered at Tier 0.** Accepted risk; admin-only publishing, least privilege, audit, and rollback are the mitigations. Tier 1 adds an offline signature |
| Tampered or truncated download | SHA-256 in the manifest + size check; reject on mismatch |
| Downgrade to a vulnerable version | Monotonic version check; `min_version`; explicit rollback only |
| Leaked agent token used to fetch releases | Token scope is agent-only; downloads are still authenticated; no cross-agent access |
| Malicious redirect / SSRF | No redirects; allowlisted download host; server-issued relative URL only |
| Update while a command is running | Update lock; defer until idle; maintenance window |
| Interrupted update bricks the agent | Versioned directories + atomic switch; keep previous release; health check before commit; watchdog/systemd restart |
| Disk exhaustion during staging | Preflight free-space check; cleanup of old releases |

**Tier 0 trust model:** the server is the trust root. TLS + agent bearer auth + SHA-256 guarantee that the agent gets the exact bytes the server stored. A compromised server, database, or admin account can replace both the artifact and the hash. This is an explicit, documented risk for v1. Tier 1 (offline signature, public key embedded in the agent) is reserved for later; it is **not planned for the current scope** and is not enabled now.

---

## 4. Architecture

```text
 build host                     Sextant server              agent
 ──────────                     ───────────────────────            ─────
 build wheel                    store blobs + manifests          heartbeat (version, caps)
 sign manifest + artifact  ──►  release registry             ◄─────
                                update policy / rollout            update directive ◄─┐
                                audit + state                       │                  │
                                                                    ▼                  │
                                                         download from store ──────────┘
                                                         verify SHA-256 + size
                                                         stage new version
                                                         health check
                                                         atomic switch + restart
                                                         report result
```

The artifact registry, blob storage, quota, and GC are shared with the [package store](PACKAGE_STORE.md). Self-update is a store consumer with `artifact_type=agent-wheel`.

Pull model: the server never connects to an agent. The agent fetches only what the heartbeat tells it to fetch.

---

## 5. Release artifacts

### Artifact

- Primary: a **wheel** of the `sextant` distribution (`sextant_control` + `sextant_agent`). The agent is stdlib-only, so no dependency resolution is needed.
- Hosting: artifacts live in the built-in [package store](PACKAGE_STORE.md), not PyPI. GitHub is the source/build/CI home; it is not the agent update channel. Agents pull through the authenticated store download endpoint.
- Later: a Windows single-file `.exe` or MSI for hosts without Python. That is a packaging project; the update protocol below stays the same.

### Manifest (Tier 0: hash-only)

```json
{
  "schema": 1,
  "version": "0.3.0",
  "channel": "stable",
  "platform": "any",
  "python_requires": ">=3.11",
  "artifact": {
    "filename": "sextant-0.3.0-py3-none-any.whl",
    "sha256": "…",
    "size": 123456
  },
  "min_version": "0.2.0",
  "mandatory": false,
  "released_at": "2026-11-09T00:00:00Z",
  "notes_url": "https://ops.example.com/releases/0.3.0",
  "signature": null,
  "key_id": null
}
```

The manifest carries the SHA-256 and size. In Tier 0 there is no signature; the server computes the hash from the uploaded bytes, and the agent recomputes it after download. The `signature`/`key_id` fields are reserved for a future Tier 1.

### Signing (Tier 1 — deferred)

Tier 1 would add an offline detached signature over the manifest, with the public key embedded in the agent. That is **not enabled now**. When it is added:

- Sign the manifest bytes with an offline Ed25519/minisign key (or an RSA key with a stdlib verifier).
- Upload artifact + manifest + signature.
- The agent verifies the signature, then the SHA-256.
- Ship `key_id` + a backup public key for rotation.

See [PACKAGE_STORE.md](PACKAGE_STORE.md) §7 for the tier comparison and the accepted Tier 0 risk.

### Build and publish

1. Bump version in `pyproject.toml`.
2. Build the wheel.
3. Compute SHA-256.
4. Generate the manifest.
5. Sign the manifest offline.
6. Upload wheel + manifest via an admin API or CLI.
7. Server validates: filename, size, server-computed hash, version format, platform, `min_version`, and that the version is newer than existing non-yanked releases.

---

## 6. Server design

### Schema

Agent releases use the [package store](PACKAGE_STORE.md) registry:

- `store_packages` with `kind='agent'` (one row for the agent);
- `store_releases` with `artifact_type='agent-wheel'`, plus the agent-specific release fields `channel`, `min_version`, `mandatory`, `python_requires`, and `notes_url` (columns or fields in the manifest);
- blob storage, quota, GC, hash verification, yank, and audit are shared with every other store package.

`update_policies`
| Column | Notes |
|---|---|
| `id`, `name`, `enabled` | |
| `target_type` | `global`, `tag`, `agent` |
| `target_id` | tag or agent ID |
| `channel` | `stable`, `beta` |
| `mode` | `manual`, `auto` |
| `max_version` | optional ceiling |
| `maintenance_window` | days/time range/timezone |
| `rollout_percent` | 0–100 |
| `paused_at` | emergency stop |

`agent_update_state`
| Column | Notes |
|---|---|
| `agent_id`, `current_version`, `target_version` | |
| `status` | `idle`, `downloading`, `staged`, `restarting`, `succeeded`, `failed`, `rolled_back` |
| `attempts`, `last_error`, `updated_at`, `completed_at` | |

`release_events` (audit): published, yanked, offered, downloaded, verified, staged, switched, rolled back, failed.

### API

| Method | Route | Role |
|---|---|---|
| POST | `/api/releases` (multipart: wheel, manifest; signature reserved) | admin |
| GET | `/api/releases?channel=&platform=` | admin/operator |
| POST | `/api/releases/{id}/yank` | admin |
| GET/PUT | `/api/update-policies` | admin |
| POST | `/api/agents/{id}/update` (manual trigger) | admin |
| POST | `/api/agents/{id}/rollback` | admin |
| GET | `/api/agents/{id}/update-state` | viewer+ |
| GET | `/api/agents/{id}/releases/{version}/download` | agent bearer |

The download endpoint serves only the artifact matching the agent’s platform and a non-yanked release; it never serves arbitrary paths.

### Config

| Variable | Default | Purpose |
|---|---|---|
| `SEXTANT_UPDATE_ENABLED` | `0` | Master switch; off means no update directives |
| `SEXTANT_UPDATE_CHANNEL` | `stable` | Default channel |
| `SEXTANT_UPDATE_AUTO` | `0` | Global auto-update default; per-policy override |
| `SEXTANT_UPDATE_ALLOWED_HOSTS` | server host | Optional external download host allowlist |
| `SEXTANT_UPDATE_MAX_ARTIFACT_MB` | `50` | Upload/download cap |
| `SEXTANT_UPDATE_RETENTION` | `3` | Releases kept per channel/platform |

---

## 7. Agent update flow

The agent advertises `update_capable: 1` and its current version in the heartbeat. The server returns an `update` object only when all of these hold:

- updates are enabled;
- the agent matches an enabled policy;
- the target release is newer and not yanked;
- the agent is idle (no command in flight);
- the maintenance window is open (for auto mode);
- the agent has not exceeded attempt limits.

### Steps

1. **Preflight** — free disk space ≥ 3× artifact size; writable release root; no active command; update lock acquired.
2. **Download** — fetch manifest then artifact over the authenticated HTTPS channel; no redirects; cap size; write to a temp directory.
3. **Verify** — SHA-256, size, platform, `min_version`, Python version, monotonic version. Reject on any mismatch. (Tier 1 would add a signature check here.)
4. **Stage** — create `/opt/sextant-agent/releases/<version>/` with a fresh venv and install the wheel `--no-deps`; keep the current release untouched.
5. **Health check** — run the staged `sextant-agent check --config …` (and a local import/`--help` check). Optionally send one reconciliation heartbeat from the staged interpreter.
6. **Switch** — atomically repoint `current` (symlink on Linux; directory/task action on Windows).
7. **Restart** — see OS notes below.
8. **Report** — write a pending-result file before restart; on next start, post `succeeded`/`failed` with old/new versions and error. A stale “restarting” state is failed after a timeout.
9. **Rollback** — if staging or health checks fail, delete the staged directory and keep running. If the new version fails to start twice, the watchdog or a helper switches `current` back and reports `rolled_back`.

### Linux (systemd)

`ExecStart` points at `/opt/sextant-agent/current/bin/sextant-agent`. Updating:

- `current` is a symlink to a versioned release directory;
- the switch is a single `rename()`/symlink replacement (atomic);
- restart from inside the service’s own cgroup is unsafe (restarting kills the updater). Use one of:
  - a transient unit: `systemd-run --unit=sextant-agent-update --collect /opt/sextant-agent/current/bin/sextant-agent-update-switch <version>`;
  - or a small root helper script invoked outside the service cgroup;
  - or exit with a special code and let a `Restart=on-failure`/watchdog unit start the new version.
- Keep the previous release directory; prune to the last N.
- Verify the unit uses the `current` symlink, not a hard-coded version path.

### Windows (scheduled task)

- Release root: `C:\ProgramData\SextantAgent\releases\<version>\`.
- The installer creates `C:\ProgramData\SextantAgent\current` as a directory junction to the active release and registers the `SextantAgent` task against `current\Scripts\sextant-agent.exe`.
- The updater:
  1. creates `current` from the legacy `venv` layout if it does not exist;
  2. migrates a legacy task action that points at `venv\Scripts\sextant-agent.exe` to the stable `current` path;
  3. installs the new wheel into `releases\<version>\venv` and health-checks it;
  4. removes and recreates the `current` junction, restoring the previous target if creation fails;
  5. prunes old releases while keeping the active one;
  6. with `--restart`, ends and starts the `SextantAgent` scheduled task and reports `restart_required` if the restart fails.
- File locks on the running `.exe` are avoided because the running process lives in the old release directory; the junction only affects the next task start.
- Keep the previous release; rollback is a junction repoint to the prior release if needed.

### Agent-local flags

```json
{
  "allow_self_update": true,
  "update_channel": "stable"
}
```

`allow_self_update=false` is a hard local denial; server policy cannot widen it. The installer should set it explicitly.

---

## 8. Rollout policy

1. **Publish** a release to `stable` or `beta`.
2. **Assign** a policy: global, tag, or single agent.
3. **Canary**: start with one agent or a low percentage (e.g. 5%).
4. **Observe** update state, heartbeats, and errors for a soak period.
5. **Expand** percentage/tags; pause or yank at any time.
6. **Version floor**: prevent agents below `min_version` from being offered releases that require a newer base (bootstrap chain).
7. **Maintenance windows**: auto-update only inside the configured window; manual triggers always allowed.
8. **Forced update**: optional `mandatory` flag for critical security releases; still respects local `allow_self_update` and idle checks.
9. **Rollback**: admin can request a rollback to the previous known-good release; agents refuse older-than-minimum rollbacks unless explicitly allowed.

Percentage selection is deterministic: `hash(agent_id + release_id) % 100 < percent`, so an agent does not flap between included/excluded.

---

## 9. Failure handling

| Failure | Behavior |
|---|---|
| Download timeout / 5xx | Retry with exponential backoff; cap attempts; report error; do not disrupt the running agent |
| Hash/size mismatch | Delete artifact, report `failed`, quarantine release if multiple agents fail identically |
| Disk full | Abort before staging; report; clean temp files |
| Staged health check fails | Delete staged release; keep current; report |
| Restart fails twice | Watchdog/helper restores the previous `current`; report `rolled_back` |
| Agent never reports after restart | Server marks `failed` after `heartbeat_interval + grace`; admin is notified |
| New version crashes on start | systemd restart loop; helper detects two failures and rolls back |
| Update offered to an unsupported Python | Server filters by `python_requires`; agent re-checks |
| Server DB restored from backup | Release registry and state restore with it; agents are unaffected |

Updates never run while a command is executing, and the agent takes a file lock so two update attempts cannot race.

The implemented CLI is:

```bash
sextant-agent update --config /etc/sextant-agent/config.json          # check
sextant-agent update --config /etc/sextant-agent/config.json --apply  # stage + switch
sextant-agent update --config /etc/sextant-agent/config.json --apply --restart
```

---

## 10. Security guardrails

- **Tier 0 integrity:** SHA-256 + size + TLS + agent bearer auth. The server is the trust root; a compromised server can replace the artifact and hash. This is accepted for v1.
- Downloads are authenticated with the agent bearer token and restricted to that agent’s platform/release.
- No redirects, no arbitrary URLs, no user-supplied artifact paths.
- Version monotonicity and `min_version` checks; rollback requires explicit admin action.
- Tier 1 (offline signature, embedded public key) is reserved for later; no signing keys exist in v1.
- Update actions are audited: who published, who triggered, which agent, old/new version, result.
- Releases can be yanked server-side; yanked releases are never offered.
- `allow_self_update` is a local hard ceiling.
- Consider requiring **approval** for `mandatory` releases until the approval workflow exists.
- Do not auto-update while a maintenance window is closed unless an admin explicitly triggers it.

---

## 11. Bootstrapping

Self-update cannot install itself into agents that predate the feature. The first update-capable release must be deployed with the existing manual installer (or configuration management). After that, the server can offer upgrades.

The installer should:

- create the versioned release root and `current` layout from day one;
- set `allow_self_update` explicitly;
- record the initial version;
- keep the previous install path working until the new layout is proven.

---

## 12. Testing plan

- Unit: manifest canonicalization, hash/size checks, version comparison, `min_version`, platform filtering, rollout hashing. Signature verification tests are added with Tier 1.
- Integration: fake release server; stage/switch/rollback in temp directories; interrupted download; bad hash; disk-full simulation; restart reporting.
- Process-level: Linux systemd switch in a disposable VM/container; Windows scheduled-task switch on a `windows-latest` runner.
- Failure injection: kill the updater mid-stage; corrupt the staged wheel; make the health check fail; simulate two failed starts.
- Load: 100 agents offered the same release; download concurrency/backpressure on SQLite/nginx.
- Security: token from agent A cannot fetch agent B’s release directive; yanked release not served; downgrade rejected.

---

## 13. Phases

| Phase | Deliverable | Effort | Risk |
|---|---|---|---|
| U1 | Release registry, hash-pinned manifest format, upload/list/yank API, download endpoint; validation tests | M | low |
| U2 | Agent staging/verification/health check; manual `sextant-agent update` / `update --apply`; per-agent manual trigger | M | medium |
| U3 | Atomic switch + restart + result reporting on Linux and Windows; watchdog rollback; audit | M–L | high (switch/restart done; watchdog and local result reporting pending) |
| U4 | Policies (tag/percentage/window), auto-update, pause/yank, UI panels | M | medium |
| U5 | Windows single-file/MSI packaging; canary automation; server self-update (separate); optional Tier 1 signing | L | high |

Start with U1–U3 on Linux and Windows; the Windows task/junction switch is implemented but still needs a disposable Windows runner for live proof.

---

## 14. Open decisions

1. Artifact format: wheel only first, or invest early in a Windows `.exe`?
2. Hosting: server-hosted artifacts through the built-in store only, or allow an external HTTPS mirror? (Store-hosted is the default; an external mirror is optional and still hash-verified.)
3. Auto-update default: off (manual approve) vs on for `stable` with maintenance windows?
4. Tier 1 signing: deferred. When revisited, choose minisign/Ed25519 vs a stdlib RSA verifier; the manifest fields are already reserved.
5. Restart mechanism on Linux: transient `systemd-run` helper vs a dedicated `sextant-agent-updater` unit.
6. Windows layout: the stable `current` directory junction is implemented; a disposable Windows runner is still needed to prove the scheduled-task switch and file-lock behavior.
7. How long to retain old releases (disk vs rollback safety)?
8. Should `mandatory` releases bypass maintenance windows? (Recommend: only with explicit admin action until approval exists.)

---

## 15. Alternatives

| Option | Notes |
|---|---|
| Manual / Ansible / Intune / SCCM | Works today and is the recommended approach until this ships. No protocol changes, but no built-in rollout or agent-side health checks |
| GitHub Releases as the update source | Free, but public/rate-limited, mutable tags, and unavailable on restricted networks. Use it for bootstrapping and human downloads, not fleet updates |
| `pip install --upgrade` via remote shell | Ad-hoc; result reporting is unreliable when the agent restarts; no signing or rollback |
| Watchtower-style container updates | Only if the agent is containerized (it is not, and agents need host root) |
| OS package manager (deb/rpm) | Good for signed distribution, but adds repository/key management and still needs a restart/rollback design |
| Single-file zipapp/PyInstaller agent | Attractive for Windows/Python-free hosts; larger change, but compatible with the same manifest/verify/switch protocol |
