# Agent profiles

**Status:** implemented. Built-in `workstation` and `server` profiles, custom profiles, server-side sync, profile-driven collection cadence, dashboard editor, admin reassignment, and tag-based assignment. AD/LDAP group sync remains future work.

Agent profiles are named sets of **agent behavior settings**. They are created and edited globally on the server; they are not per-device overrides. An agent is assigned a profile at enrollment (installer) and receives the current profile on every heartbeat.

---

## Built-in profiles

| Setting | `workstation` (default) | `server` |
|---|---|---|
| Heartbeat | 60s | 30s |
| Service / startup / GPU process inventory | 6h | **1h** |
| Package inventory collection | **24h** | **6h** |
| Package full reconciliation | 7d | 7d |
| Telemetry | every heartbeat | every heartbeat |
| Shell / power / service | enabled | enabled |
| Package management | enabled (optional) | enabled (optional) |
| Docker inventory | **off** | **off** |
| Docker inventory cadence | 300s when enabled | 300s when enabled |
| Self-update | enabled | enabled |
| Max command output | 200,000 chars | 200,000 chars |

New agents default to `workstation`. The installer can select a profile:

```bash
# Linux
sudo PROFILE=server ./deploy/install-linux-agent.sh https://ops.example.com <TOKEN> "db-01"
```

```powershell
# Windows
.\install-windows-agent.ps1 -Server https://ops.example.com -Token <TOKEN> -Profile server
```

The selected profile is stored on the agent at enrollment. Its settings live on the server; editing the profile changes every agent assigned to it. There are no per-device profile values.

---

## Settings schema

```json
{
  "heartbeat_seconds": 60,
  "modules": {
    "telemetry": true,
    "services": true,
    "startup": true,
    "gpu": true,
    "packages": true,
    "shell": true,
    "power": true,
    "service_control": true,
    "package_management": true,
    "self_update": true,
    "docker": false,
    "discovery": false
  },
  "collection": {
    "service_inventory_seconds": 21600,
    "package_inventory_seconds": 86400,
    "package_full_seconds": 604800,
    "docker_inventory_seconds": 300
  },
  "actions": {
    "allow_shell": true,
    "allow_power": true,
    "allow_service": true,
    "allow_packages": true,
    "allow_docker": false,
    "allow_docker_exec": false,
    "allow_docker_compose": false,
    "allow_docker_prune": false
  },
  "update": {
    "allow_self_update": true,
    "channel": "stable"
  },
  "max_output_bytes": 200000
}
```

Ranges:

| Field | Range |
|---|---|
| `heartbeat_seconds` | 10–3600 |
| `service_inventory_seconds` | 60–86400 |
| `package_inventory_seconds` | 300–604800 |
| `package_full_seconds` | 3600–2592000, ≥ package inventory |
| `docker_inventory_seconds` | 60–86400 |
| `max_output_bytes` | 1,024–1,000,000 |

Modules disable collection or action paths. Disabling telemetry still leaves the heartbeat/liveness channel working but sends empty metrics. Disabling `package_management` forces package actions off even if `allow_packages` is true. Docker is off by default in both built-in profiles: enable `modules.docker` **and** `actions.allow_docker`, then install/enroll the Linux agent with `ALLOW_DOCKER=1`. Local agent config remains a hard ceiling. Agent-relayed discovery is off by default: enable `modules.discovery`, set the per-agent policy `allow_discovery`, enable `SEXTANT_DISCOVERY_AGENT_RELAY=1`, and install/enroll the Linux agent with `ALLOW_DISCOVERY=1`. The agent's local flag is the final hard ceiling. D2 uses `allow_docker_exec` for one-shot exec and `allow_docker_prune` for cleanup; D3 uses `allow_docker_compose` for Compose deploy/down/restart/pull. D4 uses the same `allow_docker_exec` gate for the interactive container console (there is no separate console-toggle profile flag). Enabling Compose therefore requires local `allow_docker_compose`, the profile module, and profile `allow_docker_compose`; enabling console/one-shot exec requires local `allow_docker_exec` plus profile `allow_docker_exec`.

---

## How sync works

1. At enrollment, the server resolves the requested profile slug and stores `profile_id` on the agent. The enrollment response includes the profile slug and heartbeat interval, and the agent's per-agent policy is initialized from the profile's action gates.
2. Every heartbeat response includes the full `profile` object and its revision.
3. The agent applies heartbeat interval, collection cadences, module flags (including `docker` and `discovery`), Docker inventory cadence, max output, and self-update policy immediately. No reinstall is needed. Discovery also requires the per-agent policy `allow_discovery` and the local agent flag.
4. Profile edits bump the revision; agents apply the new settings on their next heartbeat. The per-agent policy remains a **narrowing** override only — it can restrict an action further but cannot widen the profile.
5. If an agent is offline, it keeps its last profile until the next heartbeat.

---

## Package inventory behavior

The profile controls two separate cadences:

- **service inventory** — services, startup entries, GPU processes, CPU model;
- **package inventory** — installed packages and managers.

Package inventory is revisioned and change-gated:

1. The agent collects packages on `package_inventory_seconds`.
2. It computes a SHA-256 hash over sorted `(manager, package, version, arch)` rows.
3. Every heartbeat sends the hash, revision, and collection time (tiny).
4. At the collection interval it computes a **delta** against the last acknowledged inventory: upserts plus deletes.
5. The delta carries `base_revision`; the server applies it only if that matches its stored revision, then increments the revision.
6. If the base revision is stale, the server returns `request_package_inventory: "full"`.
7. First contact, a weekly `package_full_seconds` deadline, or an explicit full request sends a full snapshot.
8. If nothing changed, only the hash/revision is sent and the server performs zero inventory writes.
9. The server stores the hash, revision, and `last_full_at` in `package_inventory_state`.

This removes the previous behavior of deleting and re-inserting every package row on every 30-second heartbeat while keeping a weekly full reconciliation and a full fallback for missed revisions.

---

## API

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/profiles` | viewer+ | List profiles, settings, revision, assigned count |
| POST | `/api/profiles` | admin | Create a custom profile |
| PATCH | `/api/profiles/{id or slug}` | admin | Update name/description/settings; bumps revision |
| DELETE | `/api/profiles/{id or slug}` | admin | Delete an unassigned, non-system profile |
| POST | `/api/profiles/{id or slug}/reset` | admin | Reset a built-in profile to defaults |
| GET | `/api/profile-tags` | viewer+ | List tag → profile assignments |
| PUT | `/api/profile-tags/{tag}` | admin | Assign a profile to a tag and apply it to matching agents |
| DELETE | `/api/profile-tags/{tag}` | admin | Remove a tag assignment |

Create body:

```json
{
  "slug": "lab",
  "name": "Lab",
  "description": "Relaxed lab machines",
  "settings": {"heartbeat_seconds": 120}
}
```

Partial settings are filled with defaults by validation. `PATCH` accepts `name`, `description`, and/or a full `settings` object.

Enrollment body accepts `"profile": "server"`. An empty profile keeps the existing profile on re-enrollment (or uses `workstation` for a new agent).

---

## CLI

```bash
sextant profile-list
sextant profile-create --slug lab --name "Lab" --from server
sextant profile-create --slug strict --name "Strict" --settings-file strict.json
sextant profile-update --slug lab --settings-file lab.json
sextant profile-reset --slug server
sextant profile-delete --slug lab
```

Run these as the service user with `SEXTANT_DATA_DIR` set, or use the API from the dashboard/admin session.

---

## Device assignment

- **Enrollment:** selected with the installer (`PROFILE` / `-Profile` / `--profile`).
- **Admin reassignment:** the device drawer has a profile selector, or `PATCH /api/agents/{id}` with `{"profile": "server"}`. This changes the assignment only; profile values stay global.
- **Tags:** assign a profile to a tag under **Profiles → Tag assignments** or `PUT /api/profile-tags/{tag}`. Matching agents are updated immediately, and new enrollments with that tag inherit it. An explicit profile passed at enrollment wins for that enrollment; a later tag assignment applies.
- Group/tag inheritance is intentionally simple: tags set the same `profile_id` per device. There are no per-device profile value overrides.

## Dashboard

**Profiles** page (admin):

- list built-in and custom profiles, assigned agent counts, revision, and a cadence summary;
- create/edit name, description, heartbeat, service/package/full Docker cadences, max output, modules, action gates, and update policy;
- reset built-in profiles to defaults;
- delete unassigned custom profiles;
- manage tag → profile assignments.

The device drawer also has a profile selector for admins.

---

## Security

- Profiles are admin-managed and audited (`profile.created`, `profile.updated`, `profile.reset`, `profile.deleted`).
- Local agent flags remain hard ceilings. A profile cannot enable something the local config denies.
- `package_management` and `allow_packages` are separate gates; both must be enabled for package actions.
- Built-in profiles cannot be deleted; assigned profiles cannot be deleted until devices are moved.
- Cadence changes are not a security boundary, but shorter intervals increase database and network load.
