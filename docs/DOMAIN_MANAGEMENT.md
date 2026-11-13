# Domain management — plan

**Status:** Proposed. Nothing in this document is implemented. Sextant still
only authenticates against LDAP(S) and manages packages per device.

**Scope:** an AD operations layer on top of an existing Samba AD or Windows AD
domain: read-only directory inventory, directory-group and OU targeting,
Windows machine policy, software delivery, compliance reporting, and optional
GPO authoring. Sextant is not a domain controller and does not implement
Kerberos, DNS, SYSVOL, replication, or the AD schema.

Related: [Active Directory / Samba AD authentication](AD_LDAP.md),
[Samba AD domain setup](SAMBA_AD.md),
[Windows remote install and AD domain plan](WINDOWS_REMOTE_INSTALL.md),
[Package management](PACKAGE_MANAGEMENT.md),
[Package store](PACKAGE_STORE.md),
[Agents](AGENTS.md),
[Security](SECURITY.md),
[Roadmap](ROADMAP.md).

---

## 1. Why

Sextant can already authenticate a user against Active Directory or Samba AD
(`sextant_control/ldap_auth.py`) and reconcile packages per device
(`package_rules`). It cannot:

- enumerate users, groups, computers, or OUs;
- target a package or policy by directory group or OU;
- report which GPOs apply to a computer, or whether they succeeded;
- report Windows Update state;
- apply machine policy or deliver software by directory group.

The goal is an **Intune-like operations layer over AD**, not a replacement for
AD. Identity and native policy stay in the directory; Sextant provides
targeting, desired state, reporting, approval, and audit.

---

## 2. Division of responsibilities

| Concern | Owner |
|---|---|
| Users, groups, computers, OUs, domain join | AD |
| Kerberos, DNS, SYSVOL, replication | AD |
| Password and security baseline, user policy, startup scripts | GPO |
| Windows Update policy (deferrals, rings, WSUS targeting) | GPO / WSUS / WUfB |
| Software catalog, desired state, install/remove/upgrade, drift | Sextant agent + package store |
| Directory inventory and group/OU targeting | Sextant (read-only LDAP) |
| Machine-level registry, services, tasks, local groups | Sextant policy module or GPO |
| Ad-hoc policy authoring and GPO lifecycle | Sextant + optional Windows bridge |

**Rules of thumb:**

- Do not make Sextant a domain controller.
- Do not make GPO the software deployment engine.
- Do not apply user policy through the SYSTEM agent. Use GPO.
- Do not write `Registry.pol` or SYSVOL files by hand.

---

## 3. Current state

| Area | Today |
|---|---|
| Directory auth | `ldap_auth.py` does a service bind, searches one user, binds as that user, and maps direct `memberOf` to a role. Auth only. |
| Nested groups | Not expanded; direct membership only. |
| Directory inventory | None. |
| Packages | Per-device desired state, `apt`/`dnf`/`winget`/`choco`, store artifacts, drift. Directory group sync is P4 in [PACKAGE_MANAGEMENT.md](PACKAGE_MANAGEMENT.md). |
| Windows agent | Runs as SYSTEM, heartbeats outbound, executes queued commands, runs PowerShell for inventory. No policy module. |
| Profiles | `workstation`/`server` modules, cadence, local action ceilings. |
| Approval | Not implemented; command approval and maintenance windows are P0 in [ROADMAP.md](ROADMAP.md). |
| Storage | SQLite with additive migrations; no general migration framework. |
| Network | Agents are outbound-only; the server never connects inbound to an agent. |

---

## 4. Goals and non-goals

### Goals

- Read users, groups, computers, and OUs from AD over LDAPS.
- Link an AD computer object to the matching Sextant agent.
- Target packages and policies by directory group, OU, tag, or agent.
- Report GPO application, registry state, and Windows Update state.
- Apply typed machine policy idempotently and report compliance.
- Deliver software through the existing package engine and store.
- Make every directory and policy change admin-only, approval-gated, and
  audited.
- Keep working when the directory is unavailable: local accounts, cached
  inventory, and a clear stale banner.
- Onboard domain-joined Windows machines into Sextant through the existing
  WinRM remote installer, with directory groups/OUs used for targeting, PKI/GPO
  for WinRM HTTPS, and a dedicated non-Domain-Admin install identity.

### Non-goals

- A domain controller, Kerberos KDC, DNS server, SYSVOL, or AD replication.
- Replacing GPO, WSUS, or Intune.
- Arbitrary registry writes, arbitrary PowerShell, or a generic remote
  execution API as "policy".
- User policy through the SYSTEM agent.
- Creating or deleting AD users, groups, or computers in the first version.
- A patch approval engine.
- Managing non-domain-joined or unmanaged Windows hosts.

---

## 5. Concepts

| Term | Meaning |
|---|---|
| **Directory object** | A cached AD user, group, computer, or OU. |
| **Link** | The association between an agent and its AD computer object. |
| **Policy** | A typed desired state: registry value, service state, scheduled task, local group, Windows Update setting, or package. |
| **Assignment** | Binds a policy to a target: agent, directory group, OU, tag, or global. |
| **Effective policy** | The deterministic result of all assignments for one agent. |
| **Compliance** | The agent's current state matches the effective policy, GPOs applied, and no reboot is pending. |
| **Drift** | Current state differs from desired state. |
| **Baseline** | A policy or GPO owned by an administrator and expected to be stable. |
| **Dynamic policy** | A Sextant-owned policy used for short-lived or per-device changes. |
| **Ring** | A rollout group: canary, broad, critical. |
| **Bridge** | An optional domain-joined Windows host that runs GPMC/PowerShell on Sextant's behalf. |

---

## 6. Architecture

```text
                    ┌────────────────────────────────────────────┐
                    │ Sextant server (FastAPI + SQLite)          │
                    │                                            │
  LDAPS read-only   │  directory sync ──► directory cache        │
  AD / Samba AD ───►│  assignments   ──► policy compiler        │
                    │  approval + audit + compliance            │
                    └───────┬───────────────────────┬────────────┘
                            │ outbound HTTPS        │ optional HTTPS/WinRM
                            ▼                       ▼
                    ┌───────────────┐       ┌─────────────────────┐
                    │ Windows agent │       │ Windows GPO bridge  │
                    │ policy apply  │       │ RSAT + GPMC         │
                    │ GPO/WU report │       │ delegated rights    │
                    └───────┬───────┘       └──────────┬──────────┘
                            │                          │
                            ▼                          ▼
                    Windows clients             AD / SYSVOL / GPO
```

Components:

| Component | Responsibility |
|---|---|
| `directory.py` | Read-only LDAP client: bind, page, search, normalize. Reuses the connection and TLS logic from `ldap_auth.py` without sharing the auth bind account. |
| `directory_sync.py` | Scheduled full/incremental sync, nested-group expansion, computer-to-agent matching, stale handling. |
| `policy.py` | Policy definitions, assignments, precedence, validation, effective-policy compiler. |
| `compliance.py` | Per-agent results, drift events, GPO/WU summaries, exports. |
| `gpo_bridge.py` | Optional allowlisted GPO operations over HTTPS/mTLS or WinRM. |
| `windows_update.py` | WU report ingestion, optional read-only WSUS adapter. |
| `sextant_agent/policy.py` | Typed policy application, journal, rollback, local allow flags. |
| `sextant_agent/windows_report.py` | `gpresult`, registry snapshot, Windows Update inventory. |

---

## 7. Data model

Additive tables; bump `PRAGMA user_version`. No destructive migration.

```text
directory_objects
  id, object_guid, dn, type, sam, upn, display_name, mail,
  enabled, when_changed, synced_at, raw_json

directory_members
  group_dn, member_dn, member_type, direct, synced_at

agent_directory_links
  agent_id, computer_dn, object_guid, fqdn, linked_by, linked_at

policy_definitions
  id, name, kind, scope, payload_json, schema_version,
  created_by, created_at, updated_at

policy_assignments
  id, policy_id, target_type, target_id, priority, effect,
  created_by, created_at

policy_states
  agent_id, policy_id, desired_hash, applied_hash, status,
  last_error, applied_at

policy_runs
  id, agent_id, policy_id, action, status, before_json,
  after_json, actor, created_at

gpo_cache
  guid, name, dn, version, enabled, updated_at

gpo_links
  guid, container_dn, enforced, enabled, link_order

windows_update_status
  agent_id, installed_json, pending_json, last_scan,
  reboot_required, source, updated_at
```

Indexes: `directory_objects(type, sam)`, `directory_members(group_dn)`,
`policy_assignments(target_type, target_id)`, `policy_states(agent_id)`,
`windows_update_status(agent_id)`.

`raw_json` is bounded and excludes secrets. Directory password hashes and
`unicodePwd` are never requested.

---

## 8. Directory inventory

### Bind account

Use a **separate read-only account** for inventory, not the authentication bind
account and never a Domain Admin:

```text
CN=svc-sextant-ro,OU=Service Accounts,DC=corp,DC=example,DC=com
```

The authentication bind account only needs to search and read the attributes
used at login. The inventory account needs read access to users, groups,
computers, and OUs. In a default AD, authenticated users can read most of these
attributes; verify with `ldapsearch` before enabling the sync.

### Searches

| Type | Filter | Notes |
|---|---|---|
| Users | `(&(objectCategory=person)(objectClass=user))` | Exclude disabled if desired with `(!(userAccountControl:1.2.840.113556.1.4.803:=2))`. |
| Groups | `(objectCategory=group)` | Read `member`, `memberOf`, `groupType`, `sAMAccountName`. |
| Computers | `(objectCategory=computer)` | Read `dNSHostName`, `operatingSystem`, `lastLogonTimestamp`, `objectGUID`. |
| OUs | `(objectClass=organizationalUnit)` | Read `distinguishedName`, `name`, `description`. |

Use paged searches (`paged_size=500`) and a bounded page count. Keep the
server's page order stable and do not re-sort across pages; load one page into
memory at a time.

### Nested groups

The login path intentionally reads direct `memberOf` only. For **targeting**,
expand nested groups server-side with the AD matching rule:

```text
(memberOf:1.2.840.113556.1.4.1941:=CN=RC Admins,OU=Groups,DC=corp,DC=example,DC=com)
```

Cache both direct and expanded membership. Never expand nested groups during a
login request; do it in the background sync.

### Computer-to-agent link

AD creates computer objects at domain join; Sextant should link them, not
create them.

- Primary match: `dNSHostName` (FQDN) to the agent-reported FQDN/hostname.
- Secondary: computer `sAMAccountName` without the trailing `$` to the agent
  name.
- Store `objectGUID` and allow an admin to override or unlink.
- Do not use the Windows `MachineGuid` as the AD key; it is not the AD object
  GUID.
- Show unlinked agents and unlinked computers; never silently merge on a weak
  match.

### Sync behavior

- Full sync every 6 hours; incremental by `whenChanged` every 15 minutes if the
  directory supports it.
- Keep the last-known-good cache on LDAP failure and show a stale banner.
- Mark objects missing from a full sync as `stale`, then remove after a
  configurable grace period.
- Directory sync must not affect local login or authentication.
- Audit sync start, finish, counts, and errors; never log bind passwords.

### Writes

Deferred. Creating, disabling, moving, or deleting AD objects is a privileged
operation and is not part of the first version. If added later, use a separate
delegated account, an explicit approval, and a dry-run/diff step.

---

## 9. Policy model

### Kinds

| Kind | Scope | Applied by | Notes |
|---|---|---|---|
| `registry` | Computer | Agent | `HKLM` only; typed value; no arbitrary hive. |
| `service` | Computer | Agent | Startup type and running state only; never change `binPath`. |
| `scheduled_task` | Computer | Agent | Validated action path and trigger; no arbitrary script. |
| `local_group` | Computer | Agent | Add/remove approved members; `Administrators` requires extra approval. |
| `windows_update` | Computer | Agent or GPO | Policy knobs, not update approvals. |
| `package` | Computer | Agent | Reuses `package_rules` and the store. |
| `gpo` | Computer or user | GPO bridge | Optional; native GPO lifecycle. |

User policy is **GPO-only** in this plan. A SYSTEM service cannot reliably
apply `HKCU` for every user at logon. If Sextant must apply user settings
without GPO, use a per-user logon task or `Active Setup`; that is a separate
feature with a different threat model.

### Example

```json
{
  "kind": "registry",
  "name": "Disable Windows consumer features",
  "scope": "computer",
  "hive": "HKLM",
  "key": "SOFTWARE\\Policies\\Microsoft\\Windows\\CloudContent",
  "value": "DisableWindowsConsumerFeatures",
  "type": "REG_DWORD",
  "data": 1
}
```

### Precedence

```text
agent override > directory group (priority) > OU (nearest) > tag > global
explicit deny > allow
```

- The most specific scope wins.
- Within a scope, higher `priority` wins.
- `deny` wins a tie.
- Two conflicting values at the same specificity are rejected at save time, not
  resolved at apply time.
- An agent override is visible in the UI and audit; it is not a silent bypass.

### GPO conflicts

GPO and Sextant policies can set the same registry value. Do not let them
fight.

- Define ownership: GPO owns the security baseline and user policy; Sextant
  owns namespaced or dynamic machine settings.
- Maintain a configurable list of GPO-managed key prefixes.
- The agent refuses to overwrite a key in that list unless the assignment has
  `override_gpo` and an explicit admin approval.
- Report the conflict instead of silently winning.
- The first version should simply **not overlap**; conflict detection is a
  later refinement.

### Dangerous settings

The policy schema is typed and allowlisted, not a generic registry writer. Deny
or require extra approval for persistence and code-execution keys, including:

```text
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options
HKLM\SYSTEM\CurrentControlSet\Services
HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run
HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce
HKLM\SECURITY
HKLM\SAM
HKLM\SYSTEM\CurrentControlSet\Control\Lsa
```

Do not allow service binary paths, task actions, or local-group additions to
`Administrators` through the normal policy editor. Those are code execution and
need a separate, explicitly audited workflow.

### Rollback

- The agent journals the previous value or state before applying a policy.
- `policy.rollback` restores the previous state for a run.
- Journals are bounded and stored under
  `C:\ProgramData\SextantAgent\policy\` with an ACL restricted to SYSTEM and
  Administrators.
- Rollback is best-effort: a failed apply may leave a partial state. Report
  exactly what was and was not restored.
- Do not promise rollback for package installs unless the package manager
  supports a pinned downgrade.

---

## 10. Agent protocol

Extend the existing outbound command channel. Do not open an inbound port.
"Server → agent" means the command is queued and returned on the agent's next
heartbeat; the server still never connects inbound.

| Command | Direction | Purpose |
|---|---|---|
| `policy.sync` | Server → agent | Deliver the effective policy snapshot, revision, and assignments. |
| `policy.report` | Agent → server | Per-policy status, current values, reboot required. |
| `policy.rollback` | Server → agent | Restore a previous run. |
| `gpo.report` | Server → agent | Return a bounded `gpresult` summary. |
| `windows_update.report` | Server → agent | Return installed, pending, and reboot state. |
| `windows_update.scan` | Server → agent | Trigger a scan only; never install. |

Heartbeat additions: `policy_revision`, `policy_status`, `gpo_last_applied`,
`pending_updates`, `reboot_required`. Send the full report only when it changes
or when requested; keep the heartbeat small.

Example `policy.sync` payload:

```json
{
  "revision": "sha256:8f2c...",
  "agent_id": "agt_123",
  "policies": [
    {
      "id": "pol_01",
      "kind": "registry",
      "scope": "computer",
      "priority": 100,
      "source": "directory-group:CN=Workstations,OU=Groups,DC=corp,DC=example,DC=com",
      "payload": {
        "hive": "HKLM",
        "key": "SOFTWARE\\Policies\\Microsoft\\Windows\\CloudContent",
        "value": "DisableWindowsConsumerFeatures",
        "type": "REG_DWORD",
        "data": 1
      }
    }
  ]
}
```

The agent applies the snapshot in order, reports per-policy results, and stores
the revision only after the whole snapshot is processed. A partial failure is
reported as partial, not silently retried forever.

### Local gates

Add local agent flags, default **false**:

```text
allow_policy
allow_policy_registry
allow_policy_services
allow_policy_tasks
allow_policy_local_groups
allow_gpo_report
allow_windows_update_report
```

The effective permission is the intersection of the server profile, the
per-agent server policy, and the local config. Local denial always wins.

### Execution rules

- Run as SYSTEM (the existing Windows scheduled task).
- Apply one policy at a time; stop on a validation error.
- Validate the payload against a versioned schema before touching the system.
- Use `winreg` for registry, `sc.exe` for services, `schtasks` for tasks; no
  shell string interpolation.
- Bound output, timeouts, and journal size.
- Idempotent: applying the same revision twice is a no-op.
- A reboot-required result does not trigger a reboot. Reuse the existing power
  command with approval and maintenance windows.
- If the server is unreachable, keep the last applied policy; do not revert.

---

## 11. Compliance and reporting

### GPO

- Run `gpresult /x <temp.xml>` or
  `Get-GPResultantSetOfPolicy -ReportType Xml -Path <temp.xml>`.
- Parse applied/denied GPO names, link order, last-applied time, and errors.
- Upload a summary, not the full XML, unless an admin explicitly requests it.
- Never upload registry contents from the GPO report.

### Registry

- Snapshot only the keys managed by Sextant plus a small allowlist of
  compliance keys.
- Compare desired and actual; report `match`, `drift`, or `unknown`.
- Do not scan the whole registry.

### Windows Update

- Installed: `Get-HotFix` or `Win32_QuickFixEngineering`.
- Pending: `Microsoft.Update.Session` search with `IsInstalled=0`.
- Reboot: `HKLM\SOFTWARE\Microsoft\Windows\WindowsUpdate\Auto Update\RebootRequired`
  and `PendingFileRenameOperations`.
- Last scan time and policy source (WSUS/WUfB/other).
- Do not use `wuauclt`; it is deprecated and version-dependent.

### Packages

Reuse the existing inventory, desired state, drift, and job results.

### Events and UI

Emit deduplicated events: `policy.applied`, `policy.failed`, `policy.drift`,
`gpo.changed`, `windows_update.pending`, `windows_update.installed`,
`reboot.required`. Reuse the notification evaluator.

The fleet Compliance page shows agent, computer object, group/OU, policy
revision, GPO status, update status, reboot state, and last-seen. Filter by
group, OU, tag, and status. Export bounded CSV/JSON as admin.

---

## 12. Windows Update

Sextant is not a patch engine. The division is:

| Layer | Owner |
|---|---|
| WU policy (deferrals, AU options, target group, pause) | GPO or agent |
| Update approval/download | WSUS (native self-hosted) or WUfB deferral rings (no approval server) |
| Installed and pending inventory | Sextant agent |
| Scan trigger | Sextant agent |
| Reboot orchestration | Sextant power command + approval/maintenance window |
| Reporting and drift | Sextant |

- **WSUS** is a Windows Server role. Samba AD can deliver the WSUS GPO, but
  WSUS itself is not part of Samba. It is the only native self-hosted
  per-update approval engine.
- **Windows Update for Business** needs no on-prem server. It gives deferral
  rings and pause, but no per-update approval. If you are avoiding Windows
  Server entirely, WUfB may be the practical choice.
- Sextant may add a **read-only WSUS adapter** later
  (`Get-WsusServer`, `Get-WsusUpdate`, `Get-WsusComputer`) on a Windows host.
  Approval writes are a separate, delegated feature.
- Do not build a patch scanner or a package-based OS updater.

---

## 13. GPO bridge (optional, later)

Without a bridge, an administrator manages GPOs with RSAT/GPMC and Sextant only
reads/reports them. With a bridge, Sextant can create and update a small number
of Sextant-owned GPOs.

### Bridge requirements

- Domain-joined Windows host with RSAT and the `GroupPolicy` PowerShell module.
- Windows Pro/Enterprise/Server; GPMC is not available on Home.
- HTTPS with mTLS, or WinRM over HTTPS. No plaintext.
- A dedicated service account with **delegated** rights: Group Policy Creator
  Owners plus link rights on the target OUs. Not Domain Admin.
- A fixed cmdlet allowlist and typed parameters. No arbitrary PowerShell.

### Allowed operations

```text
New-GPO
Set-GPRegistryValue
New-GPLink
Set-GPLink
Set-GPPermission
Remove-GPLink
Remove-GPO
Get-GPOReport
```

Security templates, Group Policy Preferences, and software installation
packages are out of scope for the first bridge. Use GPMC manually for those.

### GPO ownership

Prefer a small number of Sextant-owned GPOs per scope:

```text
Sextant Baseline - Workstations
Sextant Baseline - Servers
Sextant User Policy - Workstations
```

The server compiles the effective settings and the bridge writes them. Do not
create one GPO per registry value; that produces sprawl and precedence
surprises.

### Precedence

GPO precedence is LSDOU (local, site, domain, OU), with enforced links, blocked
inheritance, security filtering, and WMI filters. If Sextant cannot model those
rules, it must not author GPOs. Reading and reporting is always safer than
writing.

### Hard rules

- Never edit `Registry.pol` by hand.
- Never write the `groupPolicyContainer` AD object or SYSVOL directly.
- Use GPMC/PowerShell so version numbers and SYSVOL replication are handled.
- Verify every write by reading the GPO back and by running `gpupdate` /
  `gpresult` on a canary client.
- Require approval and audit the diff.

---

## 14. Security model

This feature is **tier-0**. A policy write is equivalent to code execution as
SYSTEM or domain-wide policy. Treat Sextant accordingly.

- **Separate accounts.** Authentication bind (read), directory inventory
  (read), GPO bridge (delegated write). Never reuse the auth bind account for
  inventory or policy.
- **No Domain Admin.** Delegate GPO creation/link rights on specific OUs. Do
  not store Domain Admin credentials in SQLite.
- **Secrets.** Environment/file with mode 0600, or an external secret store.
  Never log bind passwords, GPO credentials, or policy payloads containing
  secrets.
- **Admin-only writes.** Directory and policy changes are admin-only.
  Operators may request a sync or report.
- **Approval.** Default to four-eyes for policy creation, GPO changes, and
  rollback. A single-admin lab can disable the second approver explicitly;
  self-approval is audited.
- **Typed schema and allowlist.** No arbitrary registry, service binary path,
  task action, or PowerShell.
- **Audit.** Record actor, before/after, revision, target, approval, apply
  result, and rollback. Retain per `SEXTANT_AUDIT_RETENTION_DAYS`.
- **Blast radius.** Canary ring first, bounded concurrency, per-agent results,
  no fleet-wide apply without a preview.
- **Kill switch.** `SEXTANT_POLICY_ENABLED=0`, per-agent `allow_policy=0`, and a
  GPO-bridge disable switch.
- **Break-glass.** Local accounts and last-known-good directory cache remain
  available if the directory is down.
- **Network.** VPN or a management VLAN; HTTPS; no direct public exposure.
  This is already the project's deployment posture in
  [SECURITY.md](SECURITY.md).

---

## 15. API sketch

| Method and path | Role | Purpose |
|---|---|---|
| `GET /api/directory/summary` | viewer | Counts, last sync, stale flag. |
| `GET /api/directory/objects?type=user\|group\|computer\|ou` | viewer | Paged, filtered inventory. |
| `GET /api/directory/objects/{guid}` | viewer | Object detail and memberships. |
| `POST /api/directory/sync` | admin | Trigger a sync; audited. |
| `GET /api/agents/{id}/directory` | viewer | Linked computer object and groups. |
| `POST /api/agents/{id}/directory/link` | admin | Manual link/override. |
| `GET /api/policies` | viewer | Definitions and assignments. |
| `POST /api/policies` | admin | Create a typed policy. |
| `POST /api/policies/{id}/assignments` | admin | Assign to agent/group/OU/tag/global. |
| `POST /api/policies/{id}/approve` | admin | Approve a pending change. |
| `POST /api/agents/{id}/policy/sync` | operator | Request a sync. |
| `GET /api/agents/{id}/compliance` | viewer | GPO, registry, WU, package state. |
| `POST /api/agents/{id}/gpo/report` | operator | Request a GPO report. |
| `GET /api/gpos` | viewer | Cached GPOs and links. |
| `POST /api/gpos/apply` | admin | Optional bridge operation, approval-gated. |
| `GET /api/windows-update/summary` | viewer | Fleet update state. |

Reuse the existing session, CSRF, role, rate-limit, and audit middleware. New
write endpoints are admin-only by default.

---

## 16. UI

- **Directory** — users, groups, computers, OUs; sync status; search and
  filters; computer-to-agent link state.
- **Policies** — list, typed editor, assignment editor, preview/diff, approval
  queue, rollback.
- **Agent drawer → Directory** — computer DN, objectGUID, group memberships,
  last logon, link override.
- **Agent drawer → Compliance** — effective policy, GPOs applied/denied,
  registry drift, pending updates, reboot state.
- **Compliance** — fleet matrix, filters, export, drift events.
- **GPO** — read-only list/links/versions; authoring only when the bridge is
  configured.
- **Windows Update** — installed/pending/reboot, policy source, rings; optional
  WSUS read-only view.
- **Settings** — inventory bind account, sync cadence, policy flags, approval
  mode, GPO bridge.

---

## 17. Phases

| Phase | Deliverable | Effort | Risk |
|---|---|---|---|
| **D0** | Windows endpoint onboarding and remote targeting prerequisite: per-host WinRM install (implemented), then directory-targeted bulk install/preflight | S–M | medium |
| **D1** | Read-only directory inventory, computer linking, directory-group/OU targeting for packages | M | low |
| **D2** | Compliance reporting: `gpresult`, registry snapshot, Windows Update status | M | low |
| **D3** | Typed Windows machine policy: registry, services, tasks, local groups, WU settings; approval and rollback | L | high |
| **D4** | Software delivery by directory group through the existing package engine/store; MSI/store artifacts; rings; reboot orchestration | M–L | medium |
| **D5** | GPO read/report; optional GPO authoring through the Windows bridge | L | high |
| **D6** | Windows Update reporting and optional read-only WSUS adapter | M | medium |

### D0 — Windows endpoint onboarding and remote targeting

This is a prerequisite for directory-group policy and package targeting: an AD
computer object is only useful once the machine has a Sextant agent.

**Implemented today:** the Remote install page supports a per-host Windows
install over WinRM/HTTPS using the optional `pywinrm` extra. It requires a
target FQDN/IP, HTTPS 5986 with a trusted certificate, an administrative
credential supplied per job, and Python 3.11+ on the target. A job succeeds only
after an authenticated agent heartbeat. The Discovery onboarding flow also
generates a manual elevated PowerShell command. Neither path currently uses AD
group/OU targeting.

**Planned domain targeting:**

- Let an admin choose an AD computer, directory group, or OU as the install
  target.
- Resolve each computer to its FQDN and preflight:
  - WinRM HTTPS 5986 reachable;
  - certificate valid for the FQDN and trusted by the server;
  - Python 3.11+ available in the WinRM context;
  - target in the configured private/management network allowlist.
- Create bounded per-host install jobs with one-time enrollment tokens,
  per-host results, and a concurrency cap.
- Use a dedicated non-Domain-Admin install identity or Kerberos/gMSA, not shared
  Domain Admin credentials.
- Prefer GPO/Intune for the WinRM HTTPS listener, firewall scope, Python, and
  certificate distribution. Sextant should not become the domain's PKI or GPO
  engine.
- Record every preflight/attempt/result in audit; skip unreachable or
  non-compliant hosts without failing the whole OU/group job.

**Manual-vs-domain tradeoff:** for 2–3 Windows machines, use the manual
PowerShell command or per-host Remote install. A directory-targeted bulk
installer is only worth the PKI, GPO, and credential work when the fleet is
larger or rebuilds are recurring.

**Acceptance:** an admin selects a test OU/group; Sextant resolves the computers,
preflights WinRM/Python/certificates, installs on the reachable compliant hosts,
returns per-host results, and leaves unreachable/invalid hosts clearly marked.
No Domain Admin credential is stored or required. See
[WINDOWS_REMOTE_INSTALL.md](WINDOWS_REMOTE_INSTALL.md#domain-deployment-plan-ad-joined-windows-fleet)
for the detailed PKI/GPO/Kerberos/JEA plan.

### D1 — Directory inventory

- New read-only LDAP client and sync job.
- Cache users, groups, computers, OUs; expand nested groups for targeting.
- Link computer objects to agents; manual override.
- Directory page and agent Directory tab.
- Package assignments may target directory groups/OUs.
- No directory writes.

**Acceptance:** a Samba AD lab with users, groups, OUs, and a joined Windows VM
appears correctly; the agent is linked to its computer object; a package rule
targeted at a directory group applies only to members; LDAP outage leaves auth
and the last-known-good cache working.

### D2 — Compliance reporting

- `gpo.report`, `windows_update.report`, and a registry snapshot for managed
  keys.
- Compliance page and events.
- No writes.

**Acceptance:** a GPO applied to a test OU is visible in Sextant; a pending
update and a reboot-required state are visible; a registry drift is visible;
the report is bounded and contains no secrets.

### D3 — Machine policy

- Typed policy definitions, assignments, precedence, compiler, approval, apply,
  report, rollback.
- Local allow flags; server/local intersection.
- Dangerous-key and dangerous-action rejection.
- Canary ring and maintenance windows.

**Acceptance:** a harmless registry value is applied to a canary agent through
approval; a second apply is a no-op; a drift is detected; rollback restores the
previous value; a dangerous key is rejected; a GPO-managed key is refused unless
explicitly overridden; every step is audited.

### D4 — Software delivery

- Directory-group targeting for `package_rules`.
- Store/MSI support and a dry-run where the backend supports it.
- Rings, reboot orchestration, and per-agent results.

**Acceptance:** a package assigned to a directory group installs only on
members; a pinned version and `absent` work; a reboot is scheduled only through
the approved power path; the job result is visible and audited.

### D5 — GPO bridge

- Read/report first.
- Optional bridge with a fixed cmdlet allowlist, delegated rights, approval,
  and verification.
- A small set of Sextant-owned GPOs.

**Acceptance:** Sextant can read GPO links and produce a report; with the bridge
enabled, it can set a harmless registry value in a Sextant-owned GPO linked to
a test OU, verify it with `gpupdate`/`gpresult`, and remove the link; no
operation writes SYSVOL directly.

### D6 — Windows Update

- WU status reporting and scan trigger.
- Optional read-only WSUS adapter.
- No approval or install engine.

**Acceptance:** installed/pending/reboot state is visible for a Windows VM;
a scan can be triggered; no update is installed by Sextant; a WSUS host can be
read without modifying approvals.

---

## 18. Testing

- **Unit:** LDAP filters, paging, nested-group expansion, computer matching,
  policy schema validation, precedence, conflict rejection, compiler,
  rollback journal, dangerous-key rejection.
- **Integration:** Samba AD lab from [SAMBA_AD.md](SAMBA_AD.md); a Windows 11
  Pro VM joined to the domain; a test OU and a harmless registry value.
- **Negative:** unauthorized role, invalid payload, dangerous key, GPO-managed
  key, LDAP outage, agent offline, partial apply, reboot required, missing
  computer link, duplicate group names.
- **Scale:** more than 1,000 users/groups/computers; paging and UI filters.
- **Fixtures:** LDIF/LDAP mock for unit tests; `gpresult` XML fixture; Windows
  Update COM mock.
- **CI:** add a `windows-latest` runner before claiming Windows policy support.
  The project currently has no real Windows CI; see
  [TESTING.md](TESTING.md).

Do not test with production GPOs or security baselines. Use a disposable test
OU and a value with no security impact.

---

## 19. Rollout and rollback

- Feature flags default off: `SEXTANT_DIRECTORY_SYNC_ENABLED=0`,
  `SEXTANT_POLICY_ENABLED=0`, `SEXTANT_GPO_BRIDGE_ENABLED=0`.
- Ship D1 read-only first; no directory writes.
- Require a per-agent local allow flag before any policy apply.
- Canary one agent, then a small group, then the fleet.
- Back up SQLite before schema changes; additive tables only.
- Rollback: disable the flags, stop the sync, clear pending policy jobs, restore
  the database, and disable the GPO bridge. Agents keep their last applied
  state until a rollback command is approved.
- Document the blast radius and the break-glass path in the runbook.

---

## 20. Open questions

1. Is GPO authoring required, or is read/report plus agent policy enough?
2. WSUS for per-update approval, or WUfB deferral rings only?
3. Should Sextant ever create/disable AD users and groups, or remain read-only?
4. Is user policy a hard requirement? If so, GPO is the only reliable path.
5. Samba AD only, or must the design stay Windows AD-compatible? (It should.)
6. What policy catalog is the source of truth: a curated Sextant catalog, CIS
   benchmarks, or imported ADMX?
7. Multi-DC and SYSVOL replication: who operates it, and how does the bridge
   choose a DC?
8. Which Windows versions and editions are supported (Pro/Enterprise, 10/11,
   Server)?
9. Who is the second approver in a single-admin deployment?
10. What is the retention period for policy runs, GPO reports, and WU history?

---

## 21. Acceptance criteria for the whole feature

- No directory write, policy apply, or GPO change without an admin and an audit
  entry.
- No policy applies without both server policy and the local agent flag.
- A directory outage does not break local login or the last-known-good cache.
- A dangerous registry/service/task change is rejected by schema, not by
  documentation.
- GPO changes go through GPMC/PowerShell, never direct SYSVOL/`Registry.pol`
  edits.
- Compliance is visible per agent and fleet-wide, with drift and reboot state.
- Rollback works for machine policy and is honest about partial failures.
- No secrets in SQLite, logs, reports, or audit payloads.
- The feature is disabled by default.
