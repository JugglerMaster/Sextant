# Docker management — plan

**Status:** **D1 and D2 implemented.** D3–D5 are proposed and not implemented.

> D1 ships Linux Docker detection, compact heartbeat summaries, bounded inventory
> upload, SQLite storage, and the cached Docker Hosts page. D2 adds allowlisted
> container actions, inspect/logs, one-shot exec, cleanup, audit/events, and the
> action UI. Compose deployment, interactive console, and recreate/clone remain
> future phases.

**Scope:** add a separate **Docker Hosts** view to Sextant for Linux hosts. Every
enrolled agent that reports a usable Docker engine appears in that view. Selecting
one host shows its environment: containers, images, volumes, networks, Compose
projects, container inspection, logs, an exec console, and container actions.
Operators can also deploy Compose stacks whose source files live on the Sextant
server's disk.

This is deliberately not a general Docker API proxy. It is an allowlisted
management surface built on the existing agent command path. The server never
opens an inbound port, never connects to a remote Docker socket, and never
becomes a raw `docker` gateway.

The container **console** is an exec session inside the selected container, not
`docker attach` to the container's PID 1. Exec matches Portainer's console
behavior and avoids the surprises of attaching to a service's primary process.

---

## Why

Sextant already manages services, packages, and remote shells on Linux hosts.
Docker is now the deployment unit for many of those services, but the console
cannot answer basic questions:

- Which enrolled Linux hosts have a Docker engine?
- What containers are running, stopped, unhealthy, or unmanaged?
- Which images and volumes are consuming disk?
- Which Compose project owns a container, and what revision is deployed?
- Can an operator deploy or update a Compose stack without pasting a shell
  command or using a separate Portainer instance?

The goal is a small, auditable Docker console for a private fleet — not a
replacement for Portainer's full feature set.

---

## Goals

- Add a distinct top-level **Docker Hosts** view. Each enrolled Linux agent that
  reports Docker appears there; hosts that do not report Docker do not.
- Select one machine at a time to see its Docker environment. No fleet-wide
  container aggregate in v1.
- Serve the last known environment from SQLite so the page is useful while an
  agent is offline or slow, with an explicit freshness badge.
- Offer a **Refresh now** path that asks the selected agent for a new inventory.
- Inspect containers: full bounded details, mounts, networks, ports, labels,
  restart policy, resource limits, health, and recent logs.
- Manage container lifecycle: start, stop, restart, pause, unpause, rename,
  update restart policy/resource limits, and remove.
- **Exec into a container** for one-shot commands and an interactive console,
  similar to Portainer's container console. This includes stdin, stdout/stderr,
  TTY resize, and an idle timeout.
- Manage images, volumes, and networks, with safe cleanup actions for unused
  containers, dangling/unused images, unused volumes, unused networks, and
  build cache.
- Store Compose files on disk in a configurable server directory, version them,
  and deploy a selected revision to a selected host.
- Keep every action audited and gated by the existing role model.
- Keep local agent denial authoritative. Server policy can restrict Docker
  capability, never widen it.

---

## Non-goals

- **Windows Docker is not supported.** Docker Desktop, Windows containers, and
  WSL engines are out of scope. A Windows agent reports Docker as unsupported
  even if a `docker` command exists.
- Kubernetes, Swarm orchestration, and multi-node cluster management.
- A raw Docker Engine API proxy or arbitrary `docker` command runner in the UI.
  The existing remote shell remains the escape hatch for arbitrary commands.
- Registry browsing, image building/pushing, and a full image vulnerability
  scanner in v1.
- Fleet-wide bulk container actions in v1. Every destructive action is scoped
  to one selected host.
- Docker host discovery by network scanning. Docker hosts are discovered only
  through the agent heartbeat path. The separate [Discovery plan](DISCOVERY.md)
  is unrelated.
- **`docker attach` to PID 1.** The console is `docker exec` into the container.
  Attaching to the main process can stop or disturb the service and is not safe
  for a multi-user console.
- A host PTY or arbitrary host shell inside the container console. The console
  runs a shell *inside the container*. The existing remote shell remains the
  arbitrary host shell.
- Raw Docker Engine API access, arbitrary `docker` flags, `--privileged` on
  exec, or `docker exec` arguments other than the validated shell command,
  user, workdir, and TTY size. The shell command itself is intentionally
  operator-supplied and gated.
- **Encrypted or managed secret storage.** Compose `.env` and stack files are
  plaintext at rest under restrictive file permissions, like other server
  configuration. Use Docker secrets or an external secret manager for real
  secrets.

---

## Current state (verified)

- `sextant_agent` runs as root/SYSTEM, makes outbound HTTPS only, and executes
  queued work. Linux collection already reads `/proc`, `systemctl`, and
  `nvidia-smi`.
- `sextant_control` queues validated commands in SQLite; one command is in
  flight per agent. Results and incremental output are streamed back to the
  browser. The existing command path is one-way after start: it has no stdin
  channel and no PTY, so it cannot implement an interactive container console.
- Server policy and agent profiles use an intersection:
  `effective = local agent config AND server profile AND per-agent policy`.
- Agent profiles already have `modules`, `actions`, and `collection` sections,
  and are applied on every heartbeat.
- `commands` has no SQL `CHECK` on `kind`; new kinds are added in the Pydantic
  model and `validate_command_args`.
- Dashboard updates use a change-driven SSE stream; the Docker Hosts view can
  use polling first and reuse SSE later.
- There is no Docker detection, Docker inventory, compose storage, or Docker
  route in the server today.
- The package store already demonstrates the project's on-disk artifact
  pattern: content-addressed files under `SEXTANT_DATA_DIR`, hashes, quotas,
  atomic writes, and audit.
- Roles: `viewer` reads, `operator` runs commands and actions, `admin` manages
  users, profiles, settings, and other agents.

---

## Architecture

Docker management adds one collection stream and one command family to the
existing agent plane. The server remains the system of record; the agent remains
the only component that talks to Docker.

```text
 browser
    │ HTTPS + CSRF
    ▼
 Sextant server (FastAPI + SQLite)
    │  Docker Hosts / Compose Files / API
    │  stores cached inventory + compose revisions
    │
    │  heartbeat response: docker flags, inventory request
    │  command queue: docker actions
    ▼
 enrolled Linux agent (outbound HTTPS only)
    │  docker CLI / Docker socket (local)
    ▼
 Docker Engine
```

### Why the agent, not the server

- A remote host's Docker socket is root-equivalent. Exposing it to the server
  would create a new inbound or cross-host trust boundary.
- Agents already run on each host, already hold local authority, and already
  have a hardened outbound command channel.
- A per-host local flag means an agent can refuse Docker access even if the
  server asks.

### Inventory flow

1. The agent checks Docker locally when the Docker module is enabled.
2. Every heartbeat includes a compact Docker summary: availability, engine and
   Compose versions, counts, inventory hash/revision, and collection time.
3. The agent uploads a full inventory to an authenticated agent endpoint on the
   profile cadence, after a Docker action, and when the server requests it.
4. The server validates and bounds the payload, replaces the host's current
   inventory rows in one transaction, records state-change events, and notifies
   the dashboard.
5. The Docker Hosts view reads the cached rows from SQLite. It shows
   `collected_at` and heartbeat freshness. **Refresh now** queues an inventory
   command; the next uploaded snapshot updates the view.

The full inventory is not sent on every heartbeat. Heartbeats stay small and
fast; container/image detail is uploaded separately so a large inventory cannot
bloat the heartbeat transaction.

### Action flow

1. An operator selects one host and one allowlisted action.
2. The server validates the action, target, role, host capability, profile gate,
   and local policy.
3. The server inserts a `docker` command and audits the request.
4. On the next heartbeat the agent receives the command, executes argv directly
   (no shell), and posts a bounded result plus a fresh inventory.
5. The server updates the command, deployment, events, and audit rows.

**Latency:** the existing queue is one command in flight per agent and defaults
to a 30-second heartbeat. A Docker action can therefore take up to one heartbeat
to dispatch, plus execution time. The UI must show `queued`/`dispatched` state
and must not pretend the action is instant. A dedicated faster Docker lane is a
possible later optimization, not a v1 requirement.

### Console flow

One-shot exec commands reuse the normal command queue and output stream. The
interactive console cannot: it needs duplex stdin and a PTY, while the agent
must keep heartbeating. Use a separate outbound long-poll channel:

```text
 browser                         server                          agent
   │ POST console (shell, tty)     │                               │
   ├──────────────────────────────►│ create session, queue open     │
   │                               ├──────────────────────────────►│ heartbeat
   │                               │                               │ start docker exec -it
   │                               │◄──── poll for input ──────────┤
   │ POST keystrokes               │                               │
   ├──────────────────────────────►│ bounded input queue           │
   │                               ├──── input chunk ─────────────►│ write to PTY
   │◄──── SSE output ──────────────┤◄──── output chunk ────────────┤
   │ DELETE console                │                               │
   ├──────────────────────────────►│ close session                 │
   │                               ├──────────────────────────────►│ terminate exec, close PTY
```

- The agent keeps heartbeating while the console runs in a background thread.
- The console never blocks the normal command queue.
- The long-poll is outbound-only and keeps the existing no-inbound-port model.
- Agent-side session limits, idle timeout, and a bounded input/output buffer
  prevent a leaked console from consuming the host.
- Server restart or a missed poll terminates the session. Console output is not
  persisted by default; only session metadata and open/close events are stored.

---

## Discovery: which hosts appear

A **Docker host** is an enrolled Linux agent that reports all of the following:

- local `allow_docker` is true;
- the assigned profile has `modules.docker` and the relevant action gate enabled;
- the per-agent server policy allows Docker (it may only narrow);
- the Docker CLI and socket are usable, or the Engine API is reachable at the
  configured local socket.

The heartbeat summary includes a reason when Docker is unavailable:

| Reason | Meaning |
| --- | --- |
| `disabled` | Local agent config or profile has Docker disabled |
| `unsupported_os` | Windows or another non-Linux host |
| `not_installed` | No usable Docker CLI/plugin found |
| `socket_missing` | Configured socket does not exist |
| `permission_denied` | Agent cannot access the socket |
| `daemon_unreachable` | CLI exists but the engine does not respond |
| `compose_missing` | Engine is available, but Compose v2 is not installed |
| `probe_error` | Bounded error text; never includes secrets |

Hosts that are unavailable remain listed only if they have a previous snapshot,
marked unavailable and stale. A host that never reported Docker is not shown.

**Linux only.** The agent checks `sys.platform`. On Windows it reports
`unsupported_os` and refuses every Docker command. The Windows installer does
not add Docker flags.

---

## Data model

Add the following SQLite tables. All JSON columns are bounded and parsed
defensively, matching the existing `dumps`/`loads` helpers.

### `docker_hosts`

One row per agent that has reported Docker state.

| Column | Notes |
| --- | --- |
| `agent_id` | Primary key, references `agents(id)` on delete cascade |
| `available` | 0/1 |
| `reason` | Unavailable reason from the table above |
| `engine_version`, `api_version`, `os`, `arch` | Engine identity |
| `rootless`, `storage_driver`, `cgroup_driver`, `docker_root_dir` | Engine facts; rootless and driver are informational |
| `compose_version` | `docker compose version --short`, if available |
| `container_count`, `running_count`, `paused_count`, `stopped_count`, `unhealthy_count` | Summary counts |
| `image_count`, `volume_count`, `network_count` | Summary counts |
| `inventory_hash`, `inventory_revision`, `inventory_at` | Optimization and freshness |
| `collected_at`, `heartbeat_at`, `last_error` | Freshness and diagnostics |

### `docker_containers`

| Column | Notes |
| --- | --- |
| `agent_id`, `container_id` | Composite primary key; full ID |
| `name`, `image`, `image_id`, `command` | Bounded display fields |
| `state`, `status`, `health` | `running`, `exited`, `paused`, etc.; health from inspect |
| `created_at`, `started_at`, `finished_at`, `exit_code` | Timestamps/status |
| `restart_policy` | From `HostConfig.RestartPolicy.Name` (including `on-failure:N`) |
| `memory_limit`, `nano_cpus`, `pids_limit` | From bounded `docker inspect` host config (no environment data) |
| `ports`, `labels`, `mounts`, `networks` | Bounded JSON |
| `compose_project`, `compose_service`, `compose_config_hash` | Extracted from Compose labels |
| `cpu_pct`, `mem_used`, `mem_limit` | Optional, only from an on-demand stats snapshot |
| `updated_at` | Row timestamp |

### `docker_images`

`agent_id`, `image_id` (composite key), `repo_tags` JSON, `repo_digests` JSON,
`size`, `shared_size`, `container_count`, `dangling`, `created_at`,
`updated_at`.

### `docker_volumes`

`agent_id`, `name` (composite key), `driver`, `scope`, `mountpoint`, `labels`
JSON, `in_use`, `updated_at`.

### `docker_networks`

`agent_id`, `network_id` (composite key), `name`, `driver`, `scope`,
`internal`, `attachable`, `labels` JSON, `container_count`, `updated_at`.

### `docker_stacks`

Metadata for the server-side Compose library. File bytes live on disk, not in
SQLite.

| Column | Notes |
| --- | --- |
| `id` | `stk_...` |
| `slug` | Unique, `^[a-z0-9][a-z0-9-]{1,63}$` |
| `name`, `description` | Display fields |
| `current_revision_hash` | SHA-256 of the current file set |
| `created_by`, `created_at`, `updated_at`, `archived_at` | Lifecycle |

### `docker_stack_revisions`

Immutable snapshots of a stack's files.

`id`, `stack_id`, `revision_hash`, `files` JSON manifest
(`name`, `size`, `sha256`), `message`, `created_by`, `created_at`,
`UNIQUE(stack_id, revision_hash)`.

### `docker_deployments`

One row per deploy/update/down attempt.

`id`, `stack_id`, `agent_id`, `revision_hash`, `project_name`, `action`
(`up`, `down`, `restart`, `pull`), `status`, `command_id`, `compose_config_hash`,
`created_by`, `created_at`, `started_at`, `finished_at`, `error`, `log`.

`log` is bounded and scrubbed. Compose content and environment values are never
stored in the deployment log or audit detail.

### `docker_console_sessions`

Metadata and audit state for interactive exec sessions. PTY output is streamed
through bounded in-memory queues; it is not written to SQLite by default.

`id`, `agent_id`, `container_id`, `shell`, `user`, `tty`, `status`
(`opening`, `open`, `closed`, `failed`), `created_by`, `created_at`,
`last_seen_at`, `closed_at`, `exit_code`, `error`, `client_ip`.

Add an index on `(agent_id, status)` and `(last_seen_at)`. Sessions are bounded
per agent and expire on idle, agent disconnect, server restart, or an explicit
close. Closed rows are pruned with the command/deployment retention policy.

### Migration and compatibility

- New tables and indexes are additive and created with `CREATE TABLE IF NOT
  EXISTS` in the existing schema block.
- Bump `PRAGMA user_version` from 6 to 7. No existing table is rewritten.
- Existing profile JSON has no Docker keys. The Pydantic defaults fill them on
  read; new edits persist the full settings object. Built-in profiles remain
  Docker-off until an admin enables them.
- Existing agents keep their local config. A missing `allow_docker` is false.
- Existing commands, output streaming, history, and CSV export continue to work;
  add a `docker` label to command descriptions and preserve unknown-kind
  handling for old rows.

### Events and retention

- Container/image/volume/network state changes are recorded in the existing
  `agent_events` table as `docker.*` kinds, not one row per heartbeat.
- Deployment rows and logs are pruned after
  `SEXTANT_DOCKER_DEPLOYMENT_RETENTION_DAYS` (default 90).
- Current inventory tables hold only the latest snapshot per host.
- Stack revisions are pruned to the current revision, the last 10 revisions, and
  any revision referenced by a retained deployment.
- Temporary bundle files are cleaned at startup and after their TTL.

---

## Agent behavior

### New module: `sextant_agent/docker.py`

Responsibilities:

- Detect the Linux Docker CLI, Compose v2 plugin, and socket.
- Collect a compact summary for the heartbeat.
- Collect a bounded full inventory on cadence, request, or after an action.
- Execute allowlisted container, image, network, volume, prune, and Compose
  actions using argv (never a shell).
- Run one-shot container exec commands through the normal command queue.
- Run interactive container consoles in a background PTY session without
  blocking heartbeats.
- Verify downloaded Compose bundles before writing or deploying them.

Prefer the local CLI (`docker`, `docker compose`) over implementing the Engine
API. It matches the existing `systemctl`/`nvidia-smi` approach, avoids a new
runtime dependency, and makes behavior easy to diagnose. If the CLI disappears
or the socket is unreachable, report a reason instead of failing the heartbeat.

### Collection commands

Use bounded, machine-readable output. Exact flags should be chosen during
implementation, but the shape is:

- Engine: `docker version --format '{{json .Server}}'`
- Engine info: `docker info --format '{{json .}}'`
- Compose: `docker compose version --short`
- Containers: `docker ps -a --no-trunc --format '{{json .}}'`, then bounded
  `docker inspect` in chunks for health, restart policy, mounts, networks, and
  exit codes.
- Images: `docker image ls --no-trunc --digests --format '{{json .}}'`
- Volumes: `docker volume ls --format '{{json .}}'` plus bounded inspect.
- Networks: `docker network ls --format '{{json .}}'` plus bounded inspect.
- Cleanup estimates: `docker system df --format '{{json .}}'` and the cached
  inventory. Do not run destructive prunes to produce a preview.

Caps are enforced before upload: containers, images, volumes, networks, log
tail, serialized bytes, and per-command timeout. Environment variables from
containers are never collected.

### Inventory upload

```
POST /api/agent/{agent_id}/docker/inventory
Authorization: Bearer sextant_agent_...
```

The payload is a bounded JSON object:

```json
{
  "summary": {
    "available": true,
    "engine_version": "27.5.1",
    "compose_version": "2.32.4",
    "rootless": false,
    "storage_driver": "overlay2",
    "counts": {"containers": 12, "running": 8, "unhealthy": 1}
  },
  "containers": [],
  "images": [],
  "volumes": [],
  "networks": [],
  "collected_at": "2026-11-11T12:00:00Z",
  "hash": "sha256:...",
  "revision": 14
}
```

The server validates the payload against Pydantic models with row caps, requires
the agent token to match the path agent, and replaces that host's inventory rows
in one transaction. The server never accepts a host ID or file path from the
agent payload.

### Agent config additions

```json
{
  "allow_docker": false,
  "allow_docker_exec": false,
  "allow_docker_compose": false,
  "allow_docker_prune": false,
  "docker_socket": "unix:///var/run/docker.sock",
  "docker_command": "docker",
  "docker_timeout": 60,
  "docker_inventory_interval": 300,
  "docker_compose_dir": "/var/lib/sextant-agent/docker/stacks",
  "docker_console_max_sessions": 2,
  "docker_console_idle_seconds": 900
}
```

| Field | Meaning |
| --- | --- |
| `allow_docker` | Hard local ceiling for read, lifecycle, and modify actions |
| `allow_docker_exec` | Hard local ceiling for one-shot exec and interactive console |
| `allow_docker_compose` | Hard local ceiling for Compose deploy/down/pull |
| `allow_docker_prune` | Hard local ceiling for cleanup actions |
| `docker_socket` | Local socket or `DOCKER_HOST` value; local config only |
| `docker_command` | CLI executable/path |
| `docker_timeout` | Per-action timeout, clamped 5–300 seconds |
| `docker_inventory_interval` | Inventory cadence, profile may narrow or set by policy |
| `docker_compose_dir` | Agent-owned runtime directory for downloaded bundles |
| `docker_console_max_sessions` | Agent-side concurrent console cap (1–4) |
| `docker_console_idle_seconds` | Kill an idle console (60–3600) |

All flags default to false. The Linux installer can set them explicitly:

```bash
sudo ALLOW_DOCKER=1 ALLOW_DOCKER_EXEC=1 ALLOW_DOCKER_COMPOSE=1 ALLOW_DOCKER_PRUNE=0 \
  ./deploy/install-linux-agent.sh https://ops.example.com <TOKEN> docker-01
```

The Windows installer does not expose these flags.

### Profile and policy additions

Extend `ProfileSettings`:

```json
{
  "modules": {
    "docker": true
  },
  "actions": {
    "allow_docker": true,
    "allow_docker_exec": true,
    "allow_docker_compose": true,
    "allow_docker_prune": false
  },
  "collection": {
    "docker_inventory_seconds": 300
  }
}
```

Rules:

- The built-in profiles default Docker to **off**. An admin opts in.
- The per-agent policy may add `allow_docker`, `allow_docker_exec`,
  `allow_docker_compose`, and `allow_docker_prune` to further restrict a host.
  It cannot widen local flags or profile gates.
- `modules.docker` controls collection and the Docker Hosts view; action gates
  control mutations independently. Exec and console require
  `allow_docker_exec`; lifecycle and inspect require `allow_docker`.
- A missing Compose plugin is reported but does not disable container control.
- The effective permission remains:

```text
effective = local config AND profile module/action AND per-agent policy
```

### Agent command handling

Add `docker` to the command kinds and `CommandIn`. The agent branch checks the
local and remote gates before doing anything. Example action envelope:

```json
{
  "kind": "docker",
  "args": {
    "action": "container.restart",
    "target": "a1b2c3d4e5f6",
    "options": {}
  }
}
```

The server validates the action, target format, and options; the agent repeats
the validation and executes only an allowlisted argv. For non-exec Docker
actions a raw command string is never accepted. Exec commands are a separate,
explicitly gated shell-inside-the-container path, described below.

### Container exec and console

Two exec modes are exposed:

1. **Send a command** — one-shot, non-interactive exec. The browser supplies a
   command string, a shell from an allowlist (`sh`, `bash`, `ash`, optional
   `zsh`), an optional user, and an optional working directory. The server
   queues a `docker` command; the agent runs `docker exec` with `sh -lc` (or the
   selected shell) and streams stdout/stderr through the existing command output
   path. This is useful for quick fixes, `env`, `ls`, `cat`, migrations, and
   one-liners.
2. **Open console** — interactive exec with a TTY. The browser opens a console
   session for one container and gets an SSE output stream plus stdin and resize
   endpoints. The agent starts `docker exec -it` under a local PTY in a
   background thread. The command queue entry returns immediately once the
   session is open; the background session keeps running while the agent
   continues heartbeating.

Rebuild the exec argv from validated fields:

```text
docker exec -i [-t] [-u <user>] [-w <workdir>] -e TERM=xterm-256color <container_id> <shell> [-l]
```

Rules:

- Container target is the immutable full ID from the cached inventory, resolved
  again by the agent before execution.
- `user` is optional and matches `^[A-Za-z0-9_.-]{1,32}$`. A `root`/UID 0 choice
  is shown with a warning, because it is root inside that container.
- `workdir` must be a bounded absolute path.
- `--privileged`, arbitrary `-e`, `--device`, `--network`, and arbitrary raw
  flags are never accepted.
- One-shot exec and the interactive console both require `allow_docker_exec`
  and `allow_docker`. The one-shot form is audited as `docker.container_exec`.
- Console open/close is audited. Keystrokes and output are not written to the
  audit log or SQLite by default.
- The agent enforces a maximum of `docker_console_max_sessions`, kills sessions
  after `docker_console_idle_seconds`, caps pending input/output bytes, and
  terminates the exec process and PTY on close, timeout, or missed server poll.
- `docker attach` is intentionally not offered. Portainer-style console is
  `docker exec`; attaching to PID 1 is a separate, unsafe operation.

### Container inspect and modify

- **Inspect** returns the bounded current container record: image, command,
  state, health, exit code, restart policy, resource limits, mounts, networks,
  ports, labels, Compose project/service, and created/started timestamps.
  Environment variables are masked and never returned in bulk.
- **Lifecycle:** start, stop, restart, pause, unpause, kill, rename, and remove.
  Remove supports `force` and `volumes` only with explicit confirmation.
- **Update:** restart policy and a small allowlist of resource limits
  (`--memory`, `--cpus`, `--pids-limit`) via `docker update`. Docker cannot
  change env, ports, mounts, or labels in place.
- **Recreate/clone** is a later phase. The UI can prefill a form from inspect,
  but the operator must re-submit a validated container spec. Containers owned
  by a Sextant Compose deployment are blocked from manual recreate/rename unless
  the operator explicitly acknowledges that it will drift from the stack.
- Compose-managed containers show their project label and a link to the stack.
  Actions that would immediately be undone by `docker compose up` are marked as
  drift-producing, not silently allowed.

---

## Compose storage

### Server-side library is canonical

The Compose files an operator deploys live on the Sextant server's disk. The
agent receives an immutable bundle for a specific deployment, writes it under
its own managed directory, and runs `docker compose` there. The server copy is
the source of truth; the agent copy is runtime state and can be rebuilt.

Default layout:

```text
<compose_dir>/
  stacks/
    homeassistant/
      compose.yaml
      .env
      override.yaml
    monitoring/
      compose.yaml
  .sextant/
    revisions/
      homeassistant/
        <revision-hash>/
          compose.yaml
          .env
          override.yaml
    tmp/
```

- `compose_dir` defaults to `$SEXTANT_DATA_DIR/docker/compose`.
- `SEXTANT_DOCKER_COMPOSE_DIR` seeds the setting. If the environment variable
  is set, the path is shown read-only in the UI. If it is unset, an admin can
  set the path on the Docker settings page.
- Paths are absolute, resolved, must not be `/`, must not contain symlinks in
  the managed path, and are created with mode `0700`; files are `0600`.
- Stack slugs and file names are validated. V1 supports a flat set of files
  (`compose.yaml` or `compose.yml`, `.env`, and `*.yaml`/`*.yml` overrides); no
  subdirectories and no `..`.
- Writes are atomic: temp file, fsync, rename. A failed write never replaces the
  current revision.
- Changing the configured directory when stacks already exist requires an
  explicit migration. The server copies the library to an empty target,
  verifies each revision hash, then switches the setting. A non-empty,
  non-Sextant target is refused.
- Moving a library is audited. The UI reports file counts and total bytes before
  and after the move.

### Revisions and deployment

1. An admin creates or edits a stack in the Compose library.
2. The server writes the files and computes a deterministic SHA-256 over sorted
   file names, lengths, and bytes. That hash is the revision.
3. The revision is stored immutably under `.sextant/revisions/`; the current
   working files are updated only after the snapshot succeeds.
4. An operator picks a host and a revision and clicks **Deploy**.
5. The server validates host capability, action gates, project-name collision,
   and stack revision, creates a `docker_deployment`, and queues a `docker`
   command containing only the deployment ID, stack ID, hash, and project name.
6. The agent downloads
   `GET /api/agent/{agent_id}/docker/deployments/{deployment_id}/bundle`
   with its bearer token. The server returns a gzip/tar bundle for that exact
   deployment, with a size limit and `SHA-256` header.
7. The agent verifies the hash, extracts only regular flat files into
   `docker_compose_dir/<project>/<revision>/`, rejects traversal, symlinks, and
   oversized entries, then runs `docker compose -p <project> -f compose.yaml
   config --quiet` before `up -d --remove-orphans`.
8. The agent posts the command result and a fresh inventory. The server marks
   the deployment succeeded or failed, records events, and keeps the previous
   deployment state on failure.

Project names:

- Default: `sextant_<stack_slug>`.
- Per-deployment override: `^[a-z0-9][a-z0-9_-]{0,62}$`.
- Unique per host among active Sextant deployments. A collision with an
  existing external Compose project is rejected; the operator must choose a
  different override.
- The same stack can be deployed to several hosts; each host has its own
  deployment and project namespace.

Update and rollback:

- Editing a stack creates a new revision; it never mutates a deployed revision.
- Deploying a new revision is an update (`up -d --remove-orphans`).
- Rollback is deploying an older retained revision. The UI shows revision
  history and which revision each host has deployed.
- `down` requires a deployment. Removing volumes is a separate explicit option
  and is never the default.
- A deployment can be cancelled while its command is still queued, using the
  existing command cancel path. A dispatched deployment cannot be safely
  cancelled mid-Compose and is shown as running.

V1 supports image-based services. Compose files that use `build:` are rejected
with a clear error until a later phase adds bundle build contexts and a safe
build workflow. This avoids accepting arbitrary build contexts as part of a
first release.

### Drift and external stacks

- Containers with `com.docker.compose.project` are grouped by project.
- Projects that match a Sextant stack and a successful deployment are
  **managed**.
- Projects with a Compose label but no Sextant deployment are **external**:
  readable, with container actions and logs, but no Sextant deploy/update until
  an admin imports or creates a matching stack.
- Compose projects whose container config hash no longer matches the deployed
  revision are marked **drifted**.
- Containers with no Compose project are **unmanaged** and listed separately.
- Sextant never deletes external or unmanaged resources during a default prune.

---

## Server API (proposed)

All browser routes use the existing signed cookie, session checks, CSRF, RBAC,
and security headers.

### Hosts and inventory

| Method | Route | Role | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/docker/hosts` | viewer+ | List Docker-capable and previously seen hosts with summary and freshness |
| `GET` | `/api/docker/hosts/{agent_id}` | viewer+ | Selected host: summary, containers, images, volumes, networks, deployments |
| `GET` | `/api/docker/hosts/{agent_id}/containers/{container_id}` | viewer+ | Inspect one cached container |
| `POST` | `/api/docker/hosts/{agent_id}/refresh` | operator+ | Queue an inventory refresh command |
| `POST` | `/api/docker/hosts/{agent_id}/actions` | operator+ | Execute one allowlisted container/image/volume/network action |
| `POST` | `/api/docker/hosts/{agent_id}/containers/{container_id}/exec` | operator+ | Queue a one-shot exec command |
| `POST` | `/api/docker/hosts/{agent_id}/containers/{container_id}/console` | operator+ | Open an interactive exec console |
| `GET` | `/api/docker/console/{session_id}/events` | operator+ | SSE output and session status |
| `POST` | `/api/docker/console/{session_id}/input` | operator+ | Send stdin bytes |
| `POST` | `/api/docker/console/{session_id}/resize` | operator+ | Send terminal rows/columns |
| `DELETE` | `/api/docker/console/{session_id}` | operator+ | Close the console session |
| `GET` | `/api/docker/hosts/{agent_id}/cleanup` | viewer+ | Cached cleanup estimate and safe candidates |
| `POST` | `/api/docker/hosts/{agent_id}/cleanup` | operator+ | Execute one cleanup scope |

The detail response is intentionally one host only. There is no
`GET /api/docker/containers` fleet endpoint in v1.

Console open body:

```json
{
  "shell": "bash",
  "user": "",
  "workdir": "/",
  "tty": true,
  "rows": 32,
  "cols": 120
}
```

`input` carries a bounded base64/UTF-8 chunk. Console routes require
`allow_docker_exec`, the operator role, CSRF, and an active agent session. Input
and output are rate-limited; a session cannot be reused after close.

### Compose library

| Method | Route | Role | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/docker/stacks` | viewer+ | Stack metadata and current revision; no file content |
| `GET` | `/api/docker/stacks/{stack_id}` | admin | Metadata, revision list, and file contents |
| `POST` | `/api/docker/stacks` | admin | Create a stack and first revision |
| `PUT` | `/api/docker/stacks/{stack_id}` | admin | Replace files and create a new revision |
| `DELETE` | `/api/docker/stacks/{stack_id}` | admin | Archive/delete only after deployments are down |
| `POST` | `/api/docker/stacks/{stack_id}/deploy` | operator+ | Deploy a revision to one host |
| `GET` | `/api/docker/deployments?agent_id=&stack_id=` | viewer+ | Deployment history |
| `GET` | `/api/docker/deployments/{deployment_id}` | viewer+ | One deployment with bounded log |

### Settings

| Method | Route | Role | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/docker/settings` | admin | Effective storage path and source, limits, retention |
| `PATCH` | `/api/docker/settings` | admin | Change editable compose directory and limits |

### Agent-only routes

Both use the existing bearer-token agent auth. They are exempt from browser
CSRF because they are agent endpoints.

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/agent/{agent_id}/docker/inventory` | Upload a bounded full inventory |
| `GET` | `/api/agent/{agent_id}/docker/deployments/{deployment_id}/bundle` | Download the exact deployment bundle after ownership and hash checks |
| `POST` | `/api/agent/{agent_id}/docker/console/{session_id}/poll` | Long-poll for pending stdin, resize, or close |
| `POST` | `/api/agent/{agent_id}/docker/console/{session_id}/output` | Upload bounded PTY output |
| `POST` | `/api/agent/{agent_id}/docker/console/{session_id}/closed` | Report process exit and final status |

Console agent routes verify that the session belongs to the path agent and is
still open. Polls return `204` on idle timeout so the agent can close cleanly.

---

## UI

### Navigation

Add two top-level views:

- **Docker Hosts** (`data-page="docker-hosts"`, `PAGE_META["docker-hosts"]`) —
  the primary view. It lists agents that report Docker and shows one selected
  host's environment.
- **Compose Files** (`data-page="compose-files"`) — the server-side Compose
  library. Everyone can see stack metadata; admins edit files and operators can
  deploy to a Docker Host. If the nav should stay shorter, this can remain a
  sub-view of Docker Hosts, but a separate view matches the existing admin-page
  pattern best.

Both are available to every signed-in user; mutations are gated in the UI and
API by role and host capability.

A machine drawer **Docker** tab is optional; the top-level Docker Hosts view is
the primary surface so the host-selector requirement stays obvious.

### Host selection

Docker Hosts is a two-step experience:

1. **Host list** — one row/card per Docker host:
   - host name, online/offline, agent last-seen;
   - engine/Compose version, rootless badge;
   - container counts (running/stopped/unhealthy), image/volume/network counts;
   - inventory age and a stale badge;
   - a disabled/unavailable reason where applicable.
2. **Selected host** — no environment is shown until a host is selected. The
   selection is kept in page state and reflected in the URL fragment if the app
   later adds deep links.

The list is built from `docker_hosts` plus the agent row. A host that has never
reported Docker is not shown; hosts with a previous snapshot but a current
unavailable reason are shown dimmed with their last snapshot age.

### Selected host tabs

- **Overview** — engine facts, counts, freshness, **Refresh now**, and quick
  cleanup links.
- **Containers** — searchable/filterable table: name, image, state, health,
  ports, Compose project, uptime, actions. Status filters: all/running/stopped/
  unhealthy/unmanaged. Per-row: inspect, logs, console, exec, start, stop,
  restart, pause/unpause, rename, update, remove.
- **Images** — tags/digests, size, dangling, used-by count. Remove one image;
  open cleanup panel.
- **Volumes** — name, driver, scope, in-use, mountpoint; remove one; prune.
- **Networks** — name, driver, scope, internal, attached containers; remove
  one; prune.
- **Stacks** — Sextant-managed projects deployed on this host, their revision,
  deployment status, drift badge, and down/restart/pull actions. A **Deploy a
  stack** action opens the library picker. External Compose projects are shown
  separately and marked read-only for Compose operations.
- **Deployments** — history for this host: action, stack, revision, status,
  actor, timestamps, bounded log.
- **Cleanup** — the quick-links surface described below.

### Container detail

Selecting a container opens a detail panel or drawer scoped to that container:

- **Overview** — image, command, state, health, exit code, restart policy,
  resource limits, ports, Compose project/service, and timestamps.
- **Inspect** — read-only mounts, networks, labels, and a bounded raw inspect
  view with environment values masked.
- **Logs** — last 100/500/1,000 lines with timestamps, bounded and streamed as a
  normal command.
- **Console** — interactive TTY console with xterm-like rendering, copy/paste,
  resize, and a close button. Opening it requires `allow_docker_exec` and shows
  a warning that the shell runs inside the container with the container's
  privileges. Use a vendored terminal renderer (recommended: xterm.js as a
  static asset with its license notice) or a deliberately small ANSI/VT subset.
  Do not load a CDN; the existing CSP is `script-src 'self'`.
- **Exec** — one-shot command form with shell, user, working directory, and
  timeout. Output is the normal command result and is retained in command
  history.
- **Modify** — restart policy, memory/CPU/PID limits, rename, and remove.
  Lifecycle and update actions call the selected host's agent and then refresh
  the container record.

The console is a modal with a visible session banner: host, container, shell,
user, start time, and an idle-timeout countdown. Closing the modal sends an
explicit close so the agent kills the PTY.

### Compose Files view

Admins get the **Compose Files** view:

- stack list with slug, name, current revision, last edited, deployed host count;
- create/edit form with a file list and editor for `compose.yaml`, `.env`, and
  override files;
- server-side validation feedback and a clear note that files are stored at the
  configured path;
- revision history and a diff-friendly hash;
- deploy picker: host, revision, project name, pull-before-up, confirmation;
- delete/archive only when no active deployments remain;
- settings panel showing the effective compose directory and whether it comes
  from the environment or the database.

Operators can see stack metadata and deploy, but cannot edit or read `.env`
contents. Viewers can see host/container metadata and stack names, but no secret
file content.

### Cleanup panel ("quick links")

Show a card grid with an estimated reclaimable amount and a clear count. Each
card opens a confirmation dialog that lists the exact resources or filter. Do
not add a single "prune everything" button.

| Action | Default behavior |
| --- | --- |
| Remove stopped containers | Excludes running containers; excludes containers belonging to Sextant-managed stacks by default |
| Remove dangling images | Safe default; no tags |
| Remove unused images | Destructive; requires an explicit choice and confirmation |
| Remove unused volumes | Destructive; requires an explicit choice and typed confirmation |
| Remove unused networks | Excludes default bridge/host/none |
| Clear build cache | Requires confirmation; optional `until` filter |
| Per-item remove | Always available on the matching tab with a confirmation |

The preview is computed from cached inventory plus `docker system df`. It is an
estimate; the actual reclaimed size is parsed from the action output and shown
with the result. Every cleanup action is audited with actor, host, scope,
resource counts, and reclaimed size.

### Freshness and failure states

- Show `collected_at`, agent `last_seen`, and a cadence-aware stale badge.
- Cached data remains visible when the agent is offline, marked as stale.
- Mutations while offline follow the existing offline-command policy: queued or
  rejected. The UI must say which happened.
- If the engine disappears, keep the last snapshot but disable actions and show
  the reported reason.
- If Compose is missing, container/image actions still work; Compose controls
  are disabled with an explanation.
- Empty states explain exactly how to enable Docker: local agent flag, profile
  module/action, and enrollment/install command.

---

## Configuration (proposed)

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEXTANT_DOCKER_ENABLED` | `1` | Enable the Docker Hosts/Compose Files views and API; host access still needs local/profile opt-in |
| `SEXTANT_DOCKER_COMPOSE_DIR` | `$SEXTANT_DATA_DIR/docker/compose` | Canonical compose library; read-only in UI when set by env |
| `SEXTANT_DOCKER_MAX_FILE_BYTES` | `262144` | Per compose file limit |
| `SEXTANT_DOCKER_MAX_FILES` | `16` | Files per stack |
| `SEXTANT_DOCKER_MAX_BUNDLE_BYTES` | `4194304` | Deploy bundle limit |
| `SEXTANT_DOCKER_MAX_CONTAINERS` | `1000` | Inventory row cap |
| `SEXTANT_DOCKER_MAX_IMAGES` | `2000` | Inventory row cap |
| `SEXTANT_DOCKER_MAX_VOLUMES` | `1000` | Inventory row cap |
| `SEXTANT_DOCKER_MAX_NETWORKS` | `500` | Inventory row cap |
| `SEXTANT_DOCKER_INVENTORY_STALE_SECONDS` | `900` | UI stale threshold |
| `SEXTANT_DOCKER_LOG_TAIL_MAX` | `5000` | Container log tail cap |
| `SEXTANT_DOCKER_ACTION_TIMEOUT` | `300` | Container/compose action timeout |
| `SEXTANT_DOCKER_PRUNE_TIMEOUT` | `600` | Cleanup timeout |
| `SEXTANT_DOCKER_DEPLOYMENT_RETENTION_DAYS` | `90` | Deployment/log retention |
| `SEXTANT_DOCKER_BUNDLE_TTL_MINUTES` | `30` | Temporary bundle lifetime |
| `SEXTANT_DOCKER_CONSOLE_IDLE_SECONDS` | `900` | Server-side console idle timeout |
| `SEXTANT_DOCKER_CONSOLE_MAX_SESSIONS` | `2` | Concurrent consoles per agent |
| `SEXTANT_DOCKER_CONSOLE_MAX_INPUT_BYTES` | `65536` | Pending stdin cap per session |
| `SEXTANT_DOCKER_CONSOLE_MAX_OUTPUT_BYTES` | `1048576` | Buffered output cap per session |
| `SEXTANT_DOCKER_CONSOLE_POLL_SECONDS` | `25` | Agent long-poll hold time |
| `SEXTANT_DOCKER_EXEC_TIMEOUT` | `120` | One-shot exec timeout |

The Docker settings page reads the effective values, shows which are fixed by
the environment, and lets an admin edit only the compose directory when the env
variable is unset. All settings changes are audited.

---

## Security and guardrails

Docker socket access is root-equivalent. Treat enabling it as granting the same
power as the existing remote shell, but through a narrower, audited interface.

Non-negotiable:

- **Local opt-in.** `allow_docker` and the action gates default to false. Server
  policy cannot override a local denial.
- **Linux only.** Windows agents report unsupported and reject Docker commands.
- **No raw proxy.** Only allowlisted Docker actions and argv are permitted. The
  remote shell remains the explicit arbitrary *host* path; the container console
  is a separately gated `docker exec`, with no arbitrary Docker CLI flags.
- **Strict identifiers.** Validate container/image/volume/network IDs and names,
  project names, stack slugs, and action names. Reject anything outside the
  published pattern.
- **Path containment.** Compose paths are resolved under the configured root;
  reject `..`, absolute file names, symlinks, devices, and non-regular files.
  Verify containment again after resolution.
- **Atomic, bounded writes.** File, file-count, bundle, decompression, and row
  caps. Temp files are used and cleaned. No partial revision becomes current.
- **Bundle safety.** Reject tar traversal, symlinks, hard links, nested paths not
  in the manifest, and oversized entries. Verify the deployment's SHA-256 before
  extraction.
- **Secret hygiene.** `.env` values are never returned to viewers/operators,
  never logged, and never written to audit or deployment detail. Admin edits may
  show masked values. The on-disk file is `0600`. Container environment
  variables are masked in inspect responses.
- **Compose warnings.** Flag `privileged`, host networking, Docker-socket mounts,
  root bind mounts, and unusual capabilities in the deploy confirmation. V1
  rejects `build:` services.
- **Exec is code execution.** `allow_docker_exec` is a separate local ceiling and
  defaults to false. Every exec and console session is audited. The console is
  `docker exec`, not `docker attach`; no `--privileged` flag is accepted. A
  privileged container or host bind mount still makes the container shell
  powerful, so show that warning in the console banner.
- **Console isolation.** A console session is bound to its creating operator and
  agent; another signed-in user cannot attach to it. Session IDs are random,
  CSRF-protected, rate-limited, capped per agent, and closed on idle, logout,
  missed poll, server restart, or explicit close. Input and output are hard
  capped. Keystrokes and terminal output are not persisted by default.
- **Container modification.** Restart policy and resource limits are validated
  against allowlists. Recreate/clone requires a new validated spec and is blocked
  or warned for Compose-managed containers. A manual change that will be reverted
  by `docker compose up` is labelled as drift.
- **One destructive action at a time per host.** Prevent overlapping prunes,
  compose operations, console opens, and deploy/down races with a per-host lock
  in the server queue and an agent-side guard.
- **Audit everything.** Host refresh, every action, cleanup scope and result,
  stack create/edit/delete, deploy/update/down/rollback, exec, console
  open/close, settings changes, and bundle downloads. Include actor, host,
  target, and result; exclude file bytes, keystrokes, terminal output, and env
  values.
- **Bounded output.** Logs, compose output, exec output, console buffers, and
  errors use the existing output caps. A Docker action cannot fill the command
  table.
- **No server-side Docker access.** The server does not mount or connect to a
  Docker socket. It only stores inventory, brokers bounded console bytes, and
  queues work.

The security documentation and agent guide must add an explicit warning:
`allow_docker=true` lets an operator with Docker rights start privileged
containers, mount host paths, and effectively control the host. Use it only on
hosts you administer, behind the same VPN/TLS boundary as the rest of Sextant.

---

## RBAC summary

| Capability | Viewer | Operator | Admin |
| --- | --- | --- | --- |
| See Docker hosts, inventory, counts, stack names | yes | yes | yes |
| Inspect a container (env masked) and view logs | yes | yes | yes |
| Refresh inventory | no | yes | yes |
| Container lifecycle, rename, update, remove | no | yes (if gates allow) | yes |
| One-shot exec command | no | yes (if `allow_docker_exec`) | yes |
| Interactive console | no | yes (if `allow_docker_exec`) | yes |
| Image/volume/network actions | no | yes | yes |
| Cleanup and prune | no | yes (if gates allow) | yes |
| Deploy a Compose revision to a host | no | yes (if gates allow) | yes |
| Read/edit compose files and `.env` | no | no | yes |
| Change Docker settings / storage path | no | no | yes |

Server-side checks are authoritative; the UI only hides controls.

---

## Rollout

| Phase | Deliverable | Effort | Risk | Acceptance criteria |
| --- | --- | --- | --- | --- |
| **D1 (implemented)** | Schema, settings, profile/local gates, agent detection + summary, inventory upload, **Docker Hosts** host list + cached read-only host view | M | low | A Linux agent with Docker enabled appears in Docker Hosts; its containers/images/volumes/networks are visible from a cached snapshot; Windows and disabled agents do not appear; no mutation path exists |
| **D2 (implemented)** | `docker` command kind, container inspect, lifecycle/update/rename, logs, refresh, cleanup preview/execute, one-shot exec, events, audit, UI actions | M | medium | Start/stop/restart/pause/rename/update/remove, inspect, logs, cleanup, and one-shot exec work on the selected host; every action is audit-visible; policy/local denials are enforced |
| **D3** | Compose library on disk, revisions, compose directory setting/migration, Compose Files view, deploy bundle endpoint, deploy/update/down/rollback, deployment history | L | medium–high | Admin creates a stack on disk; operator deploys it to one host; containers carry the expected Compose labels; a second revision updates in place; rollback works; bundle/path attacks are rejected |
| **D4** | Interactive container console: PTY exec, long-poll stdin, SSE output, resize, idle/close handling, session caps, audit | L | high | An operator opens a real shell inside a container, runs interactive commands, resizes the terminal, and closes it; heartbeats continue; the session dies on idle or agent/server disconnect; no transcript is persisted |
| **D5** | Recreate/clone container, drift polish, live Docker revision refresh, optional container-health events/alerts, multi-host deploy ergonomics | S–M | medium | Recreate uses a validated spec and warns/blocks Compose-managed containers; Docker Hosts updates without manual reload; drift is visible; alerts are deduplicated |

Recommended sequence: **D1 → D2 → D3 → D4 → D5**. D1 made the fleet visible with
no new destructive surface. D2 delivers day-to-day container work and one-shot
exec. D3 adds Compose deployment. D4 adds the interactive PTY console, which is
the highest-risk new channel. D5 adds recreate/clone and live polish.

Effort estimates assume the existing command queue, SSE, audit, and profile
machinery are reused; they do not include Windows Docker or image builds.

---

## Testing

### Python

- `tests/test_docker_validation.py`
  - every action accepts only its documented arguments;
  - shell metacharacters, path traversal, malformed IDs, oversized options, and
  unknown fields are rejected;
  - prune scopes and filters are a fixed allowlist.
- `tests/test_docker_store.py`
  - deterministic revision hashing;
  - flat file-name validation, size caps, atomic writes;
  - symlink/`..`/absolute path escapes;
  - compose directory migration copies and verifies hashes;
  - revision pruning keeps referenced revisions;
  - `.env` redaction in API responses and logs.
- `tests/test_docker_agent.py`
  - use a fake `docker` executable in `tmp_path` that emits fixture JSON and
    records argv;
  - detection reasons (`not_installed`, `socket_missing`, `compose_missing`,
    `unsupported_os`);
  - inventory parsing, caps, and hash/revision behavior;
  - action argv ordering, no shell, timeout handling;
  - one-shot exec builds `docker exec` exactly from validated shell/user/workdir
    fields and never passes a raw flag;
  - bundle extraction rejects traversal/symlink/oversize entries.
- `tests/test_docker_console.py`
  - session lifecycle: open, input, resize, output, close, idle timeout, agent
    disconnect, server restart, and per-agent cap;
  - input/output byte caps and backpressure;
  - agent/session ownership and operator binding;
  - PTY process termination on close/timeout, no orphan process;
  - heartbeats continue while a console is open;
  - no stdin/keystrokes/terminal output in audit, command, or deployment rows.
- `tests/test_docker_api.py`
  - agent inventory upload auth, bounds, and replacement transaction;
  - host list/detail only returns the selected host;
  - refresh queues exactly one command and respects offline policy;
  - RBAC/CSRF for viewer/operator/admin;
  - inspect masks container environment values;
  - exec/console routes require `allow_docker_exec` and reject disabled agents;
  - deploy creates a deployment + command, bundle ownership is enforced;
  - Windows/Docker-disabled agents cannot execute commands;
  - failed command leaves the previous deployment intact.
- Extend existing profile/policy tests for the new module/action/collection keys
  and the local hard-ceiling intersection.

### Browser

- jsdom smoke coverage for the Docker Hosts view: host list, host selection,
  cached age, container filters, inspect, logs, exec form, console modal,
  refresh, cleanup confirmation, Compose Files form, and empty/disabled states.
- Playwright:
  - Docker Hosts and Compose Files in dark/light at desktop and mobile widths;
  - keyboard selection of a host, console open/close, and focus return from
    confirmation dialogs;
  - viewer cannot see mutation/exec/console controls or edit `.env`;
  - stale/offline host keeps cached data but disables actions;
  - axe-core serious/critical checks.

### Live validation

On a Linux host with Docker Engine and Compose v2:

1. Enroll with Docker flags enabled; confirm the host appears in Docker Hosts
   with a fresh summary.
2. Start/stop/restart/rename a disposable container; inspect it, view logs, and
   verify inventory updates.
3. Run a one-shot exec (`sh -lc 'id; uname -a'`) and confirm the result appears
   in command history.
4. Open the interactive console, run an interactive command, resize the modal,
   send Ctrl-C, and close it. Confirm the container process is gone and the
   agent keeps heartbeating.
5. Create a small Compose stack in Compose Files, deploy it, inspect labels,
   update to a new revision, and roll back.
6. Confirm `down` stops the project and does not remove volumes by default.
7. Preview cleanup, then remove a stopped container and dangling image; verify
   reclaimed size and audit rows.
8. Disable `allow_docker`/`allow_docker_exec` locally, restart the agent, and
   confirm the host leaves the list and commands are rejected.
9. Repeat on a Windows agent and confirm `unsupported_os` with no Docker route
   or console execution.

---

## Open decisions

1. **Compose location:** proposed server-side canonical library with an agent
   runtime copy. The alternative is agent-side files only, which loses central
   revision history and multi-host deploy consistency.
2. **Compose scope:** proposed image-based Compose only in v1, with `build:`
   rejected until a safe build-context model exists.
3. **RBAC:** proposed viewer read, operator actions/deploy, admin compose edit
   and settings. Confirm whether operators should also edit compose files in
   this single-tenant deployment.
4. **Secrets:** proposed plaintext `.env` under `0600` with redaction. If real
   secrets are required, decide on an external secret manager or an encrypted
   store before D3.
5. **Cleanup defaults:** proposed excluding Sextant-managed stopped containers
   and never including volumes in a default prune. Confirm whether operators may
   prune volumes without admin involvement.
6. **Latency:** proposed reusing the existing one-command-per-heartbeat queue.
   Confirm whether a faster Docker-only request lane is required for a
   Portainer-like feel.
7. **Multi-host stacks:** proposed the same revision deployable to multiple
   hosts with per-host deployment records. Confirm whether one stack should be
   pinned to one host instead.
8. **Console transport:** proposed an outbound long-poll channel with a PTY so
   the agent stays standard-library-only and keeps heartbeating. A WebSocket
   would be lower latency but adds a client dependency and a new protocol.
9. **Terminal renderer:** proposed vendoring xterm.js as a static asset (no CDN,
   no runtime dependency) for correct ANSI/VT behavior. A hand-written subset
   avoids a third-party file but will handle interactive apps poorly.
10. **Console transcript:** proposed no persistent keystroke/terminal transcript
   by default, only session metadata and audit events. Confirm whether compliance
   needs an opt-in transcript with encryption and retention.
11. **Exec privilege:** proposed a separate `allow_docker_exec` gate and no
    `--privileged` exec flag. Confirm whether operators may choose UID 0 inside
    containers by default or only with an admin override.
12. **Recreate/clone:** proposed a later-phase validated form, blocked or warned
    for Compose-managed containers. Confirm whether in-place container “editing”
    can be limited to restart policy/resource limits for v1.

---

## Success criteria

- A Linux agent with Docker enabled appears in Docker Hosts with engine,
  container, image, volume, and network data within one inventory cycle.
- Windows agents and agents with Docker disabled never appear as hosts and
  cannot execute Docker commands.
- The selected host view is useful from cached data while offline, clearly
  labeled with its age, and refreshable on demand.
- Container actions and cleanup are allowlisted, confirmed, bounded, audited,
  and blocked by local or profile denial.
- An operator can inspect and modify a container (lifecycle, restart policy,
  resource limits, rename), run a one-shot command, and open a real interactive
  console; the console continues while heartbeats run, is bounded and
  idle-timed, and leaves no persistent transcript by default.
- Compose files live in the configured server directory, revisions are
  immutable, and only a selected revision is deployed to a selected host.
- A failed deploy or a path/bundle attack cannot activate a partial revision or
  escape the managed directory.
- No raw Docker API or arbitrary `docker` command surface is exposed from the
  Docker Hosts or Compose Files views.
- The server never opens an inbound port or connects directly to a host's Docker
  socket.
- Inventory and deployment data remain bounded by retention and row caps.

---

## Related documents

- [Architecture](ARCHITECTURE.md) — agent/command flow, data model, deliberate
  limits.
- [Agents](AGENTS.md) — agent config, local policy, command execution.
- [Profiles](PROFILES.md) — module/action/collection gates.
- [Security](SECURITY.md) — trust boundaries and audit expectations.
- [Package store](PACKAGE_STORE.md) — the existing on-disk artifact pattern.
- [API](API.md) — existing route and RBAC conventions.
- [Roadmap](ROADMAP.md) — prioritization and dependencies.
- [Discovery](DISCOVERY.md) — separate network discovery work; Docker hosts come
  from agents, not scans.
