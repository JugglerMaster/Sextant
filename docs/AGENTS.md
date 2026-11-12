# Agents

Sextant agents run on each managed Linux or Windows host. They only make outbound HTTP(S) requests. The server never connects inbound to an endpoint.

For OS-specific install commands, see [INSTALL.md](INSTALL.md). This page covers configuration, policy, and day-to-day operation.

The agent binary is a Python package (`sextant_agent`) installed by the same `sextant` distribution.

## Linux

### Requirements

- Python 3.11+ with `venv` (`python3-venv` on Debian/Ubuntu).
- Root privileges for shutdown/reboot and systemd management. The provided unit runs as root. It sets `NoNewPrivileges`, a fixed `PATH`, and a restricted address-family set, but it is intentionally **not** a sandbox: remote shell is a root shell by design.
- Outbound HTTPS to the console.
- A valid server certificate, or `--ca-file` / `--insecure` for a lab. Do not use `--insecure` on a real network.

### Install from the source tree

On the endpoint, with the repository available:

```bash
sudo SOURCE_DIR=/path/to/ccdadisman \
  WATCH_SERVICES=ssh,nginx,postgresql \
  ./deploy/install-linux-agent.sh \
  https://ops.example.com \
  sextant_enroll_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  "fileserver-01"
```

The installer:

1. creates `/opt/sextant-agent/.venv`;
2. installs this project into it;
3. writes `/etc/sextant-agent/config.json` (mode 0600);
4. enrolls with the one-time token;
5. installs and starts `/etc/systemd/system/sextant-agent.service`.

Environment options for the installer:

| Variable | Purpose |
| --- | --- |
| `SOURCE_DIR` | Source tree or built wheel to install |
| `WATCH_SERVICES` | Comma-separated systemd units to monitor |
| `AGENT_TAGS` | Comma-separated tags shown in the dashboard |
| `CA_FILE` | Private CA bundle for TLS verification |
| `ALLOW_PACKAGES` | Set to `0` to disable managed package actions locally (default `1`) |
| `ALLOW_DOCKER` | Set to `1` to enable Docker discovery/inventory and lifecycle actions (default `0`) |
| `ALLOW_DOCKER_EXEC` | Set to `1` to enable one-shot exec inside containers |
| `ALLOW_DOCKER_COMPOSE` | Set to `1` to enable D3 Compose deploy/down/restart/pull actions |
| `ALLOW_DOCKER_PRUNE` | Set to `1` to enable Docker cleanup/prune actions |
| `ALLOW_DISCOVERY` | Set to `1` to allow server-requested discovery scans of this agent's local VLAN (Linux only, default `0`) |
| `DOCKER_SOCKET` | Override the Docker socket/`DOCKER_HOST` (default `unix:///var/run/docker.sock`) |
| `DOCKER_COMMAND` | Override the Docker CLI command/path (default `docker`) |
| `DOCKER_COMPOSE_DIR` | Agent-owned Compose runtime directory (default `/var/lib/sextant-agent/docker/stacks`) |
| `DOCKER_CONSOLE_MAX_SESSIONS` | Concurrent PTY consoles per agent (1–4, default 2) |
| `DOCKER_CONSOLE_IDLE_SECONDS` | Kill idle consoles (60–3600, default 900) |
| `NO_SELF_UPDATE` | Set to `1` to disable agent self-update |
| `PROFILE` | Profile slug selected at enrollment (default `workstation`) |
| `INSTALL_DIR` | Default `/opt/sextant-agent` |
| `CONFIG_DIR` | Default `/etc/sextant-agent` |
| `CONFIG_FILE` | Default `$CONFIG_DIR/config.json` |

Check it:

```bash
systemctl status sextant-agent --no-pager
journalctl -u sextant-agent -n 50 --no-pager
sudo /opt/sextant-agent/.venv/bin/sextant-agent check --config /etc/sextant-agent/config.json
```

### Manual install

```bash
sudo apt install -y python3 python3-venv
sudo install -d -m 0755 /opt/sextant-agent
sudo python3 -m venv /opt/sextant-agent/.venv
sudo /opt/sextant-agent/.venv/bin/pip install --upgrade pip
sudo /opt/sextant-agent/.venv/bin/pip install --no-deps /path/to/ccdadisman
sudo ln -sfn .venv /opt/sextant-agent/current
sudo install -d -m 0700 /etc/sextant-agent

sudo /opt/sextant-agent/.venv/bin/sextant-agent enroll \
  --config /etc/sextant-agent/config.json \
  --server https://ops.example.com \
  --token sextant_enroll_... \
  --name "$(hostname)" \
  --profile server \
  --watch-service ssh \
  --watch-service nginx

sudo chmod 0600 /etc/sextant-agent/config.json
sudo install -m 0644 /path/to/ccdadisman/deploy/sextant-agent.service \
  /etc/systemd/system/sextant-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now sextant-agent
```

## Windows

### Requirements

- Windows 10/11 or Windows Server with PowerShell 5.1+.
- Python 3.11+ installed for all users, available as `py -3` or `python`.
- Administrator PowerShell.
- Outbound HTTPS to the console.
- For shutdown/reboot/service management, the scheduled task runs as `SYSTEM`.

### Install

Copy the source tree or a built wheel to the Windows host, then run an elevated PowerShell. `-SourcePath` may point at the package source directory or a wheel file:

```powershell
Set-Location C:\path\to\ccdadisman
.\deploy\install-windows-agent.ps1 `
  -Server https://ops.example.com `
  -Token sextant_enroll_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx `
  -Name "Studio PC" `
  -WatchServices Spooler, WSearch
```

If you copied only the script, point it at the package source directory:

```powershell
.\install-windows-agent.ps1 `
  -Server https://ops.example.com `
  -Token sextant_enroll_... `
  -SourcePath C:\packages\sextant `
  -CaFile C:\certs\corp-root-ca.pem
```

The installer:

1. creates `C:\ProgramData\SextantAgent\venv`;
2. installs the package;
3. creates the stable `C:\ProgramData\SextantAgent\current` junction;
4. enrolls and writes `C:\ProgramData\SextantAgent\config.json`;
5. restricts the config ACL to SYSTEM and Administrators;
6. registers the **SextantAgent** scheduled task at startup against `current`, running as SYSTEM;
7. starts the task immediately.

Check it:

```powershell
Get-ScheduledTask SextantAgent | Format-List
Get-Content C:\ProgramData\SextantAgent\agent.log -Tail 50
C:\ProgramData\SextantAgent\current\Scripts\sextant-agent.exe check --config C:\ProgramData\SextantAgent\config.json
```

If Python was installed after the script started, open a new elevated PowerShell so `py` is on `PATH`.

### Uninstall

```powershell
Stop-ScheduledTask -TaskName SextantAgent -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName SextantAgent -Confirm:$false
Remove-Item -Recurse -Force C:\ProgramData\SextantAgent
```

For Linux:

```bash
sudo systemctl disable --now sextant-agent
sudo rm /etc/systemd/system/sextant-agent.service
sudo rm -rf /opt/sextant-agent /etc/sextant-agent
sudo systemctl daemon-reload
```

## Configuration file

`sextant-agent enroll` writes this JSON (token redacted):

```json
{
  "server_url": "https://ops.example.com",
  "agent_id": "agt_...",
  "agent_token": "sextant_agent_...",
  "name": "fileserver-01",
  "verify_tls": true,
  "ca_file": "",
  "interval": 30,
  "timeout": 25,
  "inventory_interval": 3600,
  "service_inventory_interval": 21600,
  "package_inventory_interval": 86400,
  "package_full_interval": 604800,
  "profile": "workstation",
  "watch_services": ["ssh", "nginx"],
  "tags": ["office"],
  "allow_shell": true,
  "allow_power": true,
  "allow_service": true,
  "allow_packages": true,
  "allow_self_update": true,
  "allow_docker": false,
  "allow_docker_exec": false,
  "allow_docker_compose": false,
  "allow_docker_prune": false,
  "allow_discovery": false,
  "docker_socket": "unix:///var/run/docker.sock",
  "docker_command": "docker",
  "docker_timeout": 60,
  "docker_inventory_interval": 300,
  "docker_compose_dir": "/var/lib/sextant-agent/docker/stacks",
  "docker_console_max_sessions": 2,
  "docker_console_idle_seconds": 900,
  "max_output_bytes": 200000
}
```

| Field | Meaning |
| --- | --- |
| `server_url` | Base URL of the console; HTTPS strongly recommended |
| `agent_id` / `agent_token` | Issued at enrollment; token is a secret |
| `name` | Display name (defaults to hostname) |
| `verify_tls` | Verify the server certificate |
| `ca_file` | Optional private CA bundle |
| `interval` | Heartbeat seconds (10–3600, default 30) |
| `timeout` | HTTP timeout seconds (5–300) |
| `inventory_interval` | Seconds between slow inventory refreshes — services, startup entries, GPU processes, CPU model (60–86400, default 3600) |
| `service_inventory_interval` | Service/startup/GPU inventory cadence (profile overrides) |
| `package_inventory_interval` | Package inventory cadence (profile overrides; default 24h on workstation) |
| `package_full_interval` | Forced full package reconciliation (default 7d) |
| `profile` | Profile requested at enrollment; the server's current profile is applied on every heartbeat |
| `watch_services` | Initial service watch list; admins can change it from the dashboard after enrollment |
| `tags` | Initial dashboard labels; admins can change them from the dashboard |
| `allow_shell` | If false, reject all remote shell commands locally |
| `allow_power` | If false, reject shutdown/reboot/cancel locally |
| `allow_service` | If false, reject service start/stop/restart/enable/disable locally |
| `allow_packages` | Enabled by default; set false (or enroll with `--no-packages`) to reject managed package actions locally |
| `allow_self_update` | If false, ignore agent update notices and refuse `sextant-agent update --apply` |
| `allow_docker` | Hard local ceiling for Docker discovery/inventory; false by default |
| `allow_docker_exec` | Local ceiling for one-shot exec and the D4 interactive console; false by default |
| `allow_docker_compose` | Local ceiling for D3 Compose deployment actions; false by default |
| `allow_docker_prune` | Local ceiling for D2 cleanup actions; false by default |
| `allow_discovery` | Hard local ceiling for agent-relayed discovery scans; false by default. Linux only. |
| `docker_socket` | Docker socket or `DOCKER_HOST`; local config only |
| `docker_command` | Docker CLI command/path |
| `docker_timeout` | Docker CLI timeout in seconds (5–300) |
| `docker_inventory_interval` | Local fallback inventory cadence (60–86400; profile overrides) |
| `docker_compose_dir` | Agent-owned runtime directory for downloaded Compose revision bundles |
| `docker_console_max_sessions` | Agent-side concurrent PTY console cap (1–4) |
| `docker_console_idle_seconds` | Kill an idle console (60–3600) |
| `max_output_bytes` | Local cap before upload (server also caps) |

The local flags are a hard ceiling. Server-side policy can disable an action, but it cannot re-enable one denied locally. The effective permission is the intersection of local config and server policy.

The file contains a bearer token. Keep it mode 0600 (Linux) and ACL-restricted (Windows).

## Service names

- **Linux:** use systemctl unit names, e.g. `ssh.service`, `nginx.service`. The agent also matches without the `.service` suffix for watch lists. Commands use the exact unit name.
- **Windows:** use the service *name*, not the display name, e.g. `Spooler`, `W32Time`, `WinDefend`. Find it with `Get-Service | Select Name,DisplayName`.

## Monitoring details

- Linux reads `/proc`, `statvfs`, `systemctl`, and `nvidia-smi`.
- Windows runs a fast PowerShell CIM script every heartbeat and a separate inventory script on the `inventory_interval` cadence, then `nvidia-smi` if available.
- Fast metrics (CPU, memory, disks, GPU utilization/VRAM/temperature) refresh every heartbeat. Services, startup entries, GPU compute processes, CPU model, and kernel are cached and refreshed on the profile's `service_inventory_seconds` cadence (6h workstation, 1h server; legacy `inventory_interval` fallback), when the server watch list changes, or when an operator sends `refresh`. Packages use `package_inventory_seconds` (24h workstation, 6h server) and are hash-gated.
- GPU utilization/VRAM/temperature and GPU compute-process names/PIDs require NVIDIA drivers with `nvidia-smi` in `PATH`.
- **Docker (D1–D5, Linux only):** when `allow_docker` is true and the assigned profile enables the Docker module, the agent probes the local Docker CLI/socket and sends a compact heartbeat summary. It uploads a bounded full inventory (containers, images, volumes, networks) on the profile cadence, when the server requests it, or after a Docker action. Allowlisted lifecycle/update/rename/remove/clone actions, logs, one-shot exec, and cleanup use argv only (no host shell). Exec requires `allow_docker_exec`; cleanup requires `allow_docker_prune`; Compose deployment requires `allow_docker_compose`. The agent re-resolves targets with `docker inspect`, enforces a per-host mutation guard, caps output/timeouts, masks container environment values from raw inspect, and refreshes inventory after successful mutations. Inventory transitions create deduplicated `docker.health` events on the server; the agent does not send a separate alert stream.
- **Container clone (D5):** `container.clone` builds `docker create --name <new> [--restart] [--memory] [--cpus] [--pids-limit] [--network <id>] <image-id> [argv…]` and attaches additional networks with `docker network connect`. It accepts no bind mounts, env values, devices, capabilities, privileged mode, or raw flags. The server canonicalizes the image/network IDs against inventory and blocks Compose-managed sources unless the operator sends `acknowledge_drift=true`.
- **Compose deployment (D3):** the agent downloads the deployment bundle with its bearer token, verifies the bundle SHA-256/size and the extracted revision hash, rejects traversal/symlinks/hardlinks/oversize entries and `build:` services, extracts flat files under `docker_compose_dir/<project>/<revision>/`, runs `docker compose config --quiet`, then `up -d --remove-orphans`, `down` (volumes only when explicitly requested), `restart`, or `pull`. A mutation guard prevents overlapping Compose operations.
- **Agent-relayed discovery (D3, Linux only):** when local `allow_discovery` is true and the server profile/policy allow relay, the agent accepts a `discovery.scan` command. It validates the supplied CIDR scope and excludes again, uses `ip neigh`/`arp -a`, PTR lookup, the shared OUI table, optional SSDP, and opt-in TCP/banner/TLS fingerprints on the configured port set. It never targets an address outside the scope, never opens an inbound port, and reports a bounded device/evidence payload to the server. The scan is one bounded command action (120-second local budget) and runs while the normal heartbeat loop is waiting. Windows agents report `unsupported`. Local denial always wins over server policy.
- **Interactive console (D4):** a `console.open` command starts `docker exec -it ... <shell> -l` under a PTY in a background thread. The agent resolves the container again, enforces the `allow_docker_exec` gate, max sessions, idle timeout, bounded buffers, and terminates the process group/PTY on close, timeout, shutdown, or missed server polls. A poll thread forwards stdin/resize/close while a reader thread uploads bounded PTY output. Heartbeats and the normal command loop continue. No transcript is written. Windows agents report `unsupported_os` and never appear as Docker hosts.
- Startup entries are listed read-only. Use the Services tab for start/stop/restart/enable/disable.
- Watched services that are stopped produce a critical alert. Watched service inventory is refreshed on the inventory cadence (or immediately after a watch-list change or `refresh`).
- Admins can edit watched services, tags, notes, and action permissions in the machine drawer. Changes are delivered on the next heartbeat.
- The agent reports a stable machine identity (`MachineGuid` on Windows, `/etc/machine-id` on Linux, or a host/MAC fallback) so re-enrollment updates the existing machine instead of creating a duplicate.

## Command behavior

| Kind | Server validation | Agent execution |
| --- | --- | --- |
| Shell | shell ∈ {bash, powershell, cmd}, command ≤ 8000 chars, timeout 5–300s | Local subprocess, process tree killed on timeout or when the output cap is exceeded |
| Service | action and conservative name pattern | `systemctl` or PowerShell service cmdlets |
| Power | shutdown/reboot/cancel, delay 0–600s | `shutdown` or `shutdown.exe` |
| Package | 1–25 actions; manager ∈ {apt, dnf, winget, choco}; action ∈ {install, remove, upgrade}; validated name/version | argv-based package manager call, no shell; requires local `allow_packages` and profile package settings |
| Docker | `inventory.refresh`, container lifecycle/rename/remove/update/clone/logs/inspect/exec, console open, image/volume/network remove, cleanup scopes, Compose up/down/restart/pull | argv-only allowlisted Docker CLI call; target re-resolved with `docker inspect`; exec/console require `allow_docker_exec`; cleanup requires `allow_docker_prune`; Compose requires `allow_docker_compose`; mutation guard and bounded output; clone allows only image/name/argv/restart/limits/networks |
| Discovery | `discovery.scan` with validated scope, excludes, methods, ports, timeout, concurrency, host cap, and budget | revalidates locally; local + server gate; Linux only; bounded scan; reports to the server over the outbound connection |
| Refresh | no args | Immediate success; next heartbeat refreshes metrics and Docker inventory when enabled |

Linux power commands use `shutdown -h +N` / `shutdown -r +N` for a delay (minimum one minute due to the tool), and `now` for zero. Windows uses `shutdown.exe /s|/r /t N`.

The executor reads stdout and stderr in parallel and emits chunks while the
process is still running. Chunks are batched on a background thread (about
150 ms or 16 KiB) and posted to
`POST /api/agent/{id}/commands/{command_id}/output` with a per-stream sequence
number. Streaming is best-effort: chunk upload failures are logged and dropped,
and they never prevent the final result from being posted. The final result at
`/result` is authoritative and is what command history uses. The local
`max_output_bytes` cap still applies, so streaming never emits more than the
agent captured.

For a zero-delay shutdown, the machine may go down before the result is posted; the command stays `dispatched` in history. Use the default 30-second delay in the UI when you need a reliable result.

Queue behavior:

- The server may queue work for offline agents or reject it, depending on `SEXTANT_OFFLINE_COMMAND_POLICY`.
- A per-agent queue cap limits how much work can wait.
- Queued commands can be cancelled before dispatch.
- Failed/cancelled commands can be retried from history.
- Dispatched commands automatically fail after their timeout plus grace; queued commands expire after the configured TTL.
- If server policy disables shell/power/service, the agent rejects the command even if the local config allows it.

## Rotating and revoking agents

Admin-only:

- Dashboard → machine drawer → **Delete agent** removes the server record and command history. The installed agent gets 401 until re-enrolled.
- `POST /api/agents/{id}/rotate-token` (or the API directly) issues a new bearer token. Update the config file with the new token and restart the agent.
- Re-enrolling a host with the same machine identity rotates credentials on the existing row and keeps command history. The old token is invalidated; update the config or rerun the installer.
- One-time enrollment tokens are listed on the Enrollment page; active unused tokens can be revoked.

CLI listing:

```bash
sudo -u sextant SEXTANT_DATA_DIR=/var/lib/sextant \
  /opt/sextant/.venv/bin/sextant list-agents
```

## Updating an agent

The supported self-update path uses the server package store. Publish an `agent-wheel` release under the `sextant-agent` slug, then:

```bash
# Check only
sudo /opt/sextant-agent/current/bin/sextant-agent update --config /etc/sextant-agent/config.json

# Download, verify SHA-256/size, stage a new venv, health-check, switch `current`, restart
sudo /opt/sextant-agent/current/bin/sextant-agent update \
  --config /etc/sextant-agent/config.json --apply --restart
```

The service unit runs `/opt/sextant-agent/current/bin/sextant-agent`; the installer creates `current -> .venv`. The updater keeps versioned releases under `/opt/sextant-agent/releases/` and prunes to the last three. If the staged health check fails, the current version is left untouched.

Windows uses the same release layout under `%ProgramData%\SextantAgent\releases\` and a stable `current` directory junction. The installer registers the `SextantAgent` scheduled task against `current\Scripts\sextant-agent.exe`. Run the updater from an **elevated PowerShell**; it stages and health-checks the new wheel, updates a legacy task action to `current` if necessary, repoints the junction, and, with `--restart`, restarts the scheduled task:

```powershell
C:\ProgramData\SextantAgent\current\Scripts\sextant-agent.exe update --config C:\ProgramData\SextantAgent\config.json
C:\ProgramData\SextantAgent\current\Scripts\sextant-agent.exe update --config C:\ProgramData\SextantAgent\config.json --apply --restart
```

The running agent keeps using the old release until Task Scheduler starts the new one, so executable file locks do not block the switch. Config and agent identity survive updates unless you re-enroll.

## Diagnose

```bash
sudo /opt/sextant-agent/.venv/bin/sextant-agent check --config /etc/sextant-agent/config.json
sudo /opt/sextant-agent/.venv/bin/sextant-agent run --config /etc/sextant-agent/config.json --once --log-level DEBUG
```

`--once` sends one heartbeat, executes any returned commands, posts results, and exits. It is useful for testing but not for normal operation.

Common causes of `401 Invalid agent credentials`:

- the token was rotated or the agent was deleted;
- the config file is from a different server;
- the agent ID and token were edited inconsistently;
- the enrollment was rolled back by restoring an old database.

Re-enroll with a fresh one-time token if the credential is gone. Do not copy one agent's config to another host: each agent has its own identity and token.
