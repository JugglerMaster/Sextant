# Package management — plan

**Status:** P0 implemented (read-only apt/winget inventory) and P1 partially implemented (per-device apt/dnf/winget/choco desired-state API, batched package jobs, and a device Packages tab with inventory filtering, heartbeat/inventory age, and a desired-state rule editor). Group assignment, table pagination/virtualization, and store installs are pending.
**Scope:** per-device, desired-state management of operating-system applications for Linux and Windows, using native package managers where possible and a self-hosted store for everything else.

Related: [Package store](PACKAGE_STORE.md), [Self-update](SELF_UPDATE.md), [Agents](AGENTS.md), [Security](SECURITY.md), [Roadmap](ROADMAP.md).

---

## 1. Current state

- The server has no package inventory and no desired-state engine.
- An operator can run package commands through the existing **shell** command kind, but there is no validation, no inventory, no drift detection, no approval, and no per-device policy.
- Agents run as root (Linux) / SYSTEM (Windows), so package operations are technically possible today — and dangerous.
- Commands are one-shot and serialized (one in flight per agent), which is a poor fit for reconciling many packages.
- RBAC exists (`viewer`/`operator`/`admin`), audit exists, and the agent already reports a version and telemetry.

The goal is an **Intune-like app layer** scoped to this fleet: declare what should be installed on a device, let the agent reconcile it, show compliance, and audit every change.

---

## 2. Goals and non-goals

### Goals

- Per-device desired state for applications: `present`, `absent`, `latest`, or a pinned version.
- Native backends first: **apt** (Debian/Ubuntu), **dnf** (RHEL/Fedora), later **pacman**; **winget** on Windows, optional **Chocolatey**.
- A **store** backend for apps not in apt/Store/winget/Chocolatey (see [PACKAGE_STORE.md](PACKAGE_STORE.md)).
- Package inventory and drift/compliance reporting.
- Idempotent, batched reconciliation with bounded output and per-package results.
- Audit every desired-state change and every install/remove/upgrade.
- Linux-first; Windows package management optional and explicitly scoped.
- Per-device management now; group/tag/directory assignment later.

### Non-goals (v1)

- A full configuration-management system (Ansible/Puppet/Salt replacement).
- Arbitrary scripts, arbitrary repositories, or `curl | bash`.
- Distro upgrades, kernel replacements, or unattended `dist-upgrade`.
- Managing OS images, drivers, firmware, or Store subscriptions.
- Silent BYOD management. These are administratively owned hosts.

---

## 3. Concepts

| Term | Meaning |
|---|---|
| **Manager** | `apt`, `dnf`, `pacman`, `snap`, `flatpak`, `winget`, `choco`, `store` |
| **Package** | Manager-specific identifier (e.g. `nginx`, `7zip.7zip`, `Spooler` is a service, not a package) |
| **Desired state** | `present`, `absent`, `latest`, or `version:<pin>` |
| **Inventory** | What the agent currently has installed, with versions |
| **Drift** | Installed state differs from desired state |
| **Compliance** | No drift for all managed packages on the device |
| **Reconcile** | Apply the minimum set of idempotent actions to reach desired state |
| **Scope** | Where a rule applies: device (phase 1), tag/group/global (phase 2+) |

Version pinning is deliberately simple. Use exact manager-native versions (`pkg=1.2.3-1`, `7zip.7zip --version 24.09`) and `latest`. Do not attempt cross-manager semantic-version comparison.

---

## 4. Backends

| Manager | Platform | Inventory | Install | Remove | Notes |
|---|---|---|---|---|---|
| `apt` | Debian/Ubuntu | `dpkg-query -W` | `apt-get install -y --no-install-recommends pkg[=ver]` | `apt-get remove -y` / `purge` | Primary Linux backend; native signatures |
| `dnf` | RHEL/Fedora | `rpm -qa` | `dnf install -y pkg[-ver]` | `dnf remove -y` | Second Linux backend |
| `pacman` | Arch | `pacman -Q` | `pacman -S --noconfirm pkg` | `pacman -Rns --noconfirm` | Optional |
| `snap` | Linux | `snap list` | `snap install pkg [--channel=]` | `snap remove` | Optional; confined apps |
| `flatpak` | Linux | `flatpak list` | `flatpak install -y` | `flatpak uninstall -y` | Optional; desktop apps |
| `winget` | Windows | `winget list` / `winget export` | `winget install --id X --exact --silent --scope machine` | `winget uninstall` | Built into modern Windows; see §11 |
| `choco` | Windows | `choco list --local-only` | `choco install -y pkg [--version=]` | `choco uninstall -y` | Optional; CLI is Apache-2.0, community repo free; commercial features licensed |
| `store` | Both | Store database | Native installer or declarative recipe (planned) | Same | Self-hosted artifacts; [PACKAGE_STORE.md](PACKAGE_STORE.md); install wiring pending |

Do not mix managers for the same package unless explicitly configured. A package ID is always `(manager, name)`.

**Decided platform priority:** Debian/Ubuntu with `apt` is the default and first target. Windows app management is in scope: `winget` is the primary backend, Chocolatey is optional, and the store covers vendor `.msi`/`.msix`/portable packages that are not in winget. Windows Store/MSIX apps are often per-user, so they are supported through the store only where a machine-wide install is actually possible.

### Windows Store vs winget vs Chocolatey

- **winget** is the default CLI for Windows. It can install from the community repository and, where available, `--source msstore`.
- **Microsoft Store for Business/Education is retired**; do not design around it. The modern paths are the Store app, Intune’s Store app integration, and winget/`msstore`.
- **Store/MSIX apps are often per-user**, and installing them from a SYSTEM scheduled task is unreliable. Use winget’s machine-scope installers for fleet management; treat Store-only apps as a manual/user-context exception.
- **Chocolatey** is a reasonable fallback for packages not in winget, but it requires bootstrapping the CLI and trusting a third-party community feed. Require an explicit admin opt-in and, preferably, an internal mirror.
- For a Linux-first deployment, Windows package management is optional. Implement `apt` first; add `winget` only when there is a real need.

---

## 5. Desired-state model

### Phase 1 — per device (requested)

A device has a list of managed package rules:

| Field | Example |
|---|---|
| `agent_id` | `agt_123` |
| `manager` | `apt` |
| `package` | `nginx` |
| `desired_state` | `present`, `absent`, `latest`, `version:1.24.0-1ubuntu1` |
| `source_profile` | `ubuntu-main` (optional; restricts where it can come from) |
| `priority` | integer (for future group overrides) |
| `notes`, `created_by`, `created_at` | audit metadata |

A device can also have an explicit `ignore` entry to suppress a package that a future group policy would otherwise install.

### Phase 2 — groups (later)

Assignments gain scopes:

```
device override > group assignment (priority) > tag assignment > global
explicit deny > allow
```

Groups can be:

- **static** (created in the console, membership by agent),
- **tags** (already available on agents),
- **directory groups** (Samba AD / OpenLDAP / FreeIPA), synced into the server.

See §12 for the directory options and why plain “SMB groups” are not a directory service.

---

## 6. Data model

`package_inventory`
| Column | Notes |
|---|---|
| `agent_id`, `manager`, `package`, `installed_version` | current state |
| `available_version`, `source`, `arch` | optional, if cheap to obtain |
| `last_seen` | inventory timestamp |

`package_rules`
| Column | Notes |
|---|---|
| `id`, `agent_id`, `manager`, `package` | phase 1 scope |
| `desired_state`, `version_spec`, `source_profile` | |
| `enabled`, `priority`, `notes` | |
| `created_by`, `created_at`, `updated_at` | |

`package_jobs`
| Column | Notes |
|---|---|
| `id`, `agent_id`, `created_by`, `created_at` | one reconcile batch |
| `status` | `queued`, `dispatched`, `running`, `succeeded`, `failed`, `cancelled` |
| `reason` | `drift`, `manual`, `scheduled` |
| `result` | JSON: per-package status/version/error |

`package_events`
`at`, `agent_id`, `manager`, `package`, `action`, `from_version`, `to_version`, `status`, `detail`, `actor`.

Retention follows the command/audit policy (`RC_COMMAND_RETENTION_DAYS`, `RC_AUDIT_RETENTION_DAYS`).

---

## 7. Reconciliation flow

```text
heartbeat (inventory hash + manager availability)
        │
        ▼
server: desired state per device → compute drift
        │
        ├── no drift → compliance, no job
        │
        └── drift → create package_job (batch, bounded)
                         │
                         ▼
                  dispatched on heartbeat
                         │
                         ▼
agent: lock manager → dry-run (optional) → apply actions → collect results
                         │
                         ▼
                  post per-package result
                         │
                         ▼
server: update inventory/state, events, audit, alerts
```

Design choices:

- **Batch, not one command per package.** One `package_job` carries an ordered list of actions (install/remove/upgrade) with a cap (for example 25 per job) to avoid 30-second waits per package.
- **Idempotent actions.** Re-running a job when the package is already in the desired state is a no-op success.
- **Inventory first.** The agent sends a compact inventory and a hash. If the hash is unchanged, the server skips recomputation.
- **No shell.** The agent uses argument vectors, never `shell=True`; package names and versions are validated against a strict pattern.
- **One package job per agent** and a local lock file, so apt/dpkg/dnf/msi locks are never contended by this tool.
- **Dry-run first** for risky actions (remove, upgrade): `apt-get -s`, `dnf --assumeno`, `winget --dry-run` where available. Show the planned action in the UI and audit it.

### New command/job kind

Add a dedicated `package` job rather than reusing `shell`:

```
kind: package
args: {
  "actions": [
    {"manager": "apt", "package": "nginx", "action": "install", "version": null},
    {"manager": "apt", "package": "telnet", "action": "remove"}
  ],
  "dry_run": false,
  "job_id": "pkgjob_..."
}
```

Validation is server-side and agent-side. The agent checks `allow_packages` before executing anything.

---

## 8. Agent implementation

New module: `runner_agent/packages.py` (stdlib only).

- **Manager detection:** `shutil.which("apt-get")`, `dnf`, `pacman`, `snap`, `flatpak`, `winget`, `choco`.
- **Inventory adapters** per manager, bounded to a configurable number of packages (for example 2,000) and cached; send a hash plus only changed/new rows if the payload would be large.
- **Action adapters** with argv lists, timeouts, bounded output, and structured results.
- **Locking:** `/run/runner-agent-packages.lock` (Linux) / a named mutex or lock file (Windows).
- **Environment:** `DEBIAN_FRONTEND=noninteractive`, `APT_LISTCHANGES_FRONTEND=none`, `NEEDRESTART_MODE=a` only if explicitly enabled.
- **Reboot detection:** `/var/run/reboot-required`, `needrestart -b`, or Windows pending-reboot registry keys. Report `reboot_required`; do not reboot automatically unless policy allows it.
- **Local flags:** `allow_packages` (default `true`, set `false` or `--no-packages` to deny), `package_managers` (allowlist), `package_max_actions` (default 25). Profiles/policy cannot widen a local denial.

Inventory cadence comes from the agent profile: `workstation` collects packages every 24h, `server` every 6h, with a weekly full reconciliation. The agent sends a hash/revision every heartbeat, a revisioned delta (upserts + deletes) at the collection interval, and a full snapshot weekly, on first contact, or when the server requests one. The server skips writes when nothing changed and requests a full snapshot if the base revision is stale.

---

## 9. API

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/agents/{id}/packages` | viewer+ | inventory + rules + drift |
| PUT | `/api/agents/{id}/packages` | admin | replace the per-device desired-state list |
| POST | `/api/agents/{id}/packages` | admin | add/update one rule |
| DELETE | `/api/agents/{id}/packages/{manager}/{package}` | admin | remove a rule |
| POST | `/api/agents/{id}/packages/sync` | operator+ | queue a reconcile job now |
| GET | `/api/agents/{id}/package-jobs` | viewer+ | job history |
| GET | `/api/agents/{id}/package-events` | viewer+ | install/remove/upgrade events |
| GET | `/api/package-managers` | viewer+ | detected managers/versions per agent |
| GET | `/api/packages/catalog` | viewer+ | optional: known packages from inventory |

Package policy changes are **admin-only** because they translate into root actions. Operators can trigger a sync of an already-approved desired state. Approval workflows can later gate high-risk changes.

---

## 10. UI

**Device drawer → Packages tab**

- Manager tabs (`apt`, `dnf`, `winget`, …) with counts.
- Table: package, installed version, desired state, available version, status (`compliant`, `drift`, `installing`, `failed`, `reboot required`).
- Add rule dialog: search installed packages, or enter a package name; choose state/version; show the exact command that will run.
- Per-package history and last error.
- “Sync now”, “Dry run”, and “Refresh inventory”.
- Compliance banner and filters for non-compliant devices.

**Fleet view**

- Compliance summary; devices with failures; packages with the most drift.
- Later: group assignments, bulk apply, maintenance windows.

---

## 11. Security

- **Two-key control:** `allow_packages` is enabled by default locally, but the assigned profile must also enable `package_management` and `allow_packages`. Either side can disable it; a compromised server cannot widen a local denial.
- **Admin-only policy:** desired state is a root-level control.
- **Strict identifiers:** package names match `^[A-Za-z0-9][A-Za-z0-9+._:@-]{0,127}$`; versions match a conservative pattern. No shell metacharacters; argv execution only.
- **No arbitrary sources:** source profiles are predefined (distro repos, approved internal mirrors). Adding a repo or key requires an admin action outside the package job and is audited.
- **No insecure flags:** reject `--allow-unauthenticated`, `--force-yes`, `--nogpgcheck`, `-Uvh --nodeps`, and similar.
- **Native signatures first:** apt/dnf verify repository metadata; winget verifies installer hashes; Chocolatey verifies checksums. The store adds SHA-256 integrity (Tier 0; signatures are deferred — see [PACKAGE_STORE.md](PACKAGE_STORE.md)).
- **Critical-package guard:** kernel, bootloader, systemd, openssh, and the agent itself require explicit approval and are excluded from `latest` catches by default.
- **Reboot policy:** report, do not reboot automatically unless a maintenance window and explicit policy allow it.
- **Audit:** every rule change, job, action, version, and result.
- **Resource limits:** one job per agent, action cap, timeout, bounded output, retry backoff, no infinite reconcile loops.

---

## 12. Group management

### Phase 1 — per device

Exactly as requested: rules are attached to `agent_id`. No inheritance, no group logic. This keeps the first version predictable and auditable.

### Phase 2 — static groups and tags

- Create groups in the console; assign agents manually or by tag.
- A group carries package rules; assignments have priorities.
- Precedence: **device override > group (priority) > tag > global**; `absent`/deny wins over `present`.
- Changing a group recomputes desired state for members; jobs are queued only for drift.

### Phase 3 — directory groups

Options, all free:

| Option | Notes |
|---|---|
| **Samba AD** | Free, Linux-hostable, speaks LDAP/Kerberos; can provide `memberOf` groups. Good if you want an AD-like directory without Windows Server licensing. |
| **OpenLDAP** | Free, lightweight; group membership via `memberOf`/`groupOfNames`. |
| **FreeIPA** | Free, Linux identity + policy; richer than needed for package groups. |
| **Static console groups** | Simplest; no directory dependency. |

The server already has an LDAP adapter and maps direct `memberOf` DNs to roles. Reuse that binding/search logic for a read-only group sync: periodically list groups and members, store `(directory_group, agent/user)` mappings, and let package assignments target those groups.

**“SMB groups” caveat:** plain SMB file-share groups/ACLs (`smb.conf`, share permissions) are not a directory service and do not expose fleet-wide group membership. If you mean **Samba AD**, that works and is free. If you mean SMB share ACLs, use static groups or a directory instead.

Windows AD is **not required**. For a Linux-first fleet, static groups or Samba AD/OpenLDAP are sufficient. Windows Server AD only makes sense if you already run it; it is not free.

---

## 13. Windows specifics

- **winget as SYSTEM:** ensure `winget.exe` is resolvable from the SYSTEM task (absolute path under `%LOCALAPPDATA%\Microsoft\WindowsApps` may not be available to SYSTEM). Prefer a machine-scope install path or Chocolatey. Test on a real Windows runner.
- **Machine vs user scope:** `--scope machine` where the installer supports it; otherwise the package is per-user and out of scope for a SYSTEM task.
- **Store apps:** MSIX/Appx are often per-user; `Add-AppxPackage` from SYSTEM is limited. Treat Store-only apps as manual or use Intune; do not promise Store automation.
- **Chocolatey:** optional backend; require it preinstalled or install it through a separate approved bootstrap, and pin the community feed or an internal mirror.
- **Silent installs:** pass vendor silent flags; store them in a per-package profile. Do not guess.
- **Reboot:** detect pending reboot; report; reboot only via the existing power-command path with approval.
- **Uninstall:** `winget uninstall --id X --exact` or `choco uninstall`; verify the package is gone.

---

## 14. Linux specifics

- **apt:** run `apt-get update` only when the cache is stale (for example `-o Acquire::Max-FutureTime`/mtime check), with a lock timeout (`-o DPkg::Lock::Timeout=60`). Never run `dist-upgrade`.
- **dnf:** `dnf -y install`, `dnf -y remove`, `dnf -y upgrade pkg`; avoid `--nogpgcheck`.
- **Snap/Flatpak:** separate managers; snaps update automatically unless held; Flatpak remotes are a supply-chain decision.
- **unattended-upgrades / dnf-automatic:** can conflict. Detect and document precedence: this tool applies explicit package rules; security updates may still be handled by the OS. Do not fight the package manager lock.
- **Package name variance:** Debian `apache2` vs RHEL `httpd`, `openssh-server` vs `openssh-server`; rules are per manager, so no cross-distro magic.
- **Reboot-required:** report via `/var/run/reboot-required` or `needrestart`; defer until a maintenance window.
- **Rollback:** apt/dnf do not offer transaction rollback. Keep the previous package version available and allow a pinned downgrade; recommend filesystem snapshots (LVM/btrfs) for critical hosts.

---

## 15. Store backend

The `store` manager covers apps that are not in apt, the Windows Store, winget, or Chocolatey: vendor `.deb`/`.rpm`/`.msi`/`.msix` packages, internal tools, and the agent itself.

- Artifacts are uploaded by admins, content-addressed by SHA-256, hash-verified, and served only to assigned agents.
- Installation uses native installers (`dpkg -i`, `rpm -U`, `msiexec /i /qn`, `Add-AppxPackage`) or a declarative recipe.
- The same desired-state model applies: `present`, `absent`, `version:<pin>`.
- The store is also the recommended host for agent self-update artifacts, so the project does not need PyPI. See [PACKAGE_STORE.md](PACKAGE_STORE.md) and [SELF_UPDATE.md](SELF_UPDATE.md).

---

## 16. Phases

| Phase | Deliverable | Effort | Risk |
|---|---|---|---|
| P0 | Read-only inventory for apt/dnf/winget; Packages tab; no actions | S–M | low |
| P1 | Per-device desired state for apt/dnf; batched `package` jobs; dry-run; audit | M | medium |
| P2 | Windows `winget` (machine scope); optional Chocolatey; per-package silent profiles | M | medium |
| P3 | Static groups/tags and assignment precedence; bulk apply | M | medium |
| P4 | Directory group sync (Samba AD/OpenLDAP/FreeIPA); approval/maintenance windows; compliance alerts | M–L | medium |
| P5 | snap/flatpak/pacman; internal mirrors; snapshot/rollback guidance; store integration | L | medium |

Start with P0 + P1 on Debian/Ubuntu. Add Windows only after a real Windows runner proves the SYSTEM-context behavior.

---

## 17. Decisions and open questions

Decisions recorded:

- **Linux:** Debian/Ubuntu `apt` is the default and first implementation; `dnf` follows.
- **Windows:** in scope. `winget` first, Chocolatey optional, store for vendor MSI/MSIX/portable packages.
- **Scope:** per-device rules first; static groups/tags next; directory groups (Samba AD/OpenLDAP/FreeIPA) later. Windows AD is not required.
- **Package actions:** native managers first; the store is the fallback for gaps.
- **Windows Store:** no dependence on Microsoft Store for Business (retired); use winget or the internal store.

Still open:

1. Are Windows Store/MSIX-only apps required, or is winget coverage enough?
2. Who may change desired state — admin only, or operator with approval?
3. Should `latest` be allowed for critical packages (kernel, SSH, the agent), or only pinned versions?
4. How should OS security updates interact with this tool (`unattended-upgrades` vs managed)?
5. Reboot automation: never, maintenance window only, or explicit approval?
6. Group source: static console groups first, or Samba AD/OpenLDAP from day one?
7. How large can the package inventory payload be on hosts with thousands of packages, and how often should it refresh?

---

## 18. Alternatives

| Option | Notes |
|---|---|
| **Ansible / Puppet / Salt / Chef** | Mature, push-based, full config management. Use if you already run one; the built-in feature should stay a lightweight per-device app layer, not compete. |
| **Intune** | Windows/cloud-centric, licensing, not Linux-first. Not a fit for a self-hosted Linux fleet. |
| **Landscape / Katello / Foreman** | Strong Ubuntu/RHEL fleet management; heavier than this project’s scope. |
| **unattended-upgrades / dnf-automatic** | Good for security updates; no desired app sets or per-device compliance. |
| **Nix / Guix** | Excellent declarative model but a major platform pivot. |
| **osquery / Fleet** | Inventory and policy visibility, not installation. |
| **Store only** | Not enough; most fleet apps come from native repositories. |
| **Shell commands** | Works today; no inventory, drift, idempotency, or safety. |

Recommended: **inventory first**, then apt/dnf desired state, then groups; add the store for the gaps and for agent self-update.
