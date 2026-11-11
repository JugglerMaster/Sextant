# Agents

Runner agents run on each managed Linux or Windows host. They only make outbound HTTP(S) requests. The server never connects inbound to an endpoint.

For OS-specific install commands, see [INSTALL.md](INSTALL.md). This page covers configuration, policy, and day-to-day operation.

The agent binary is a Python package (`runner_agent`) installed by the same `runner-control` distribution.

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
  rc_enroll_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  "fileserver-01"
```

The installer:

1. creates `/opt/runner-agent/.venv`;
2. installs this project into it;
3. writes `/etc/runner-agent/config.json` (mode 0600);
4. enrolls with the one-time token;
5. installs and starts `/etc/systemd/system/runner-agent.service`.

Environment options for the installer:

| Variable | Purpose |
| --- | --- |
| `SOURCE_DIR` | Source tree or built wheel to install |
| `WATCH_SERVICES` | Comma-separated systemd units to monitor |
| `AGENT_TAGS` | Comma-separated tags shown in the dashboard |
| `CA_FILE` | Private CA bundle for TLS verification |
| `ALLOW_PACKAGES` | Set to `0` to disable managed package actions locally (default `1`) |
| `NO_SELF_UPDATE` | Set to `1` to disable agent self-update |
| `PROFILE` | Profile slug selected at enrollment (default `workstation`) |
| `INSTALL_DIR` | Default `/opt/runner-agent` |
| `CONFIG_DIR` | Default `/etc/runner-agent` |
| `CONFIG_FILE` | Default `$CONFIG_DIR/config.json` |

Check it:

```bash
systemctl status runner-agent --no-pager
journalctl -u runner-agent -n 50 --no-pager
sudo /opt/runner-agent/.venv/bin/runner-agent check --config /etc/runner-agent/config.json
```

### Manual install

```bash
sudo apt install -y python3 python3-venv
sudo install -d -m 0755 /opt/runner-agent
sudo python3 -m venv /opt/runner-agent/.venv
sudo /opt/runner-agent/.venv/bin/pip install --upgrade pip
sudo /opt/runner-agent/.venv/bin/pip install --no-deps /path/to/ccdadisman
sudo ln -sfn .venv /opt/runner-agent/current
sudo install -d -m 0700 /etc/runner-agent

sudo /opt/runner-agent/.venv/bin/runner-agent enroll \
  --config /etc/runner-agent/config.json \
  --server https://ops.example.com \
  --token rc_enroll_... \
  --name "$(hostname)" \
  --profile server \
  --watch-service ssh \
  --watch-service nginx

sudo chmod 0600 /etc/runner-agent/config.json
sudo install -m 0644 /path/to/ccdadisman/deploy/runner-agent.service \
  /etc/systemd/system/runner-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now runner-agent
```

## Windows

### Requirements

- Windows 10/11 or Windows Server with PowerShell 5.1+.
- Python 3.11+ installed for all users, available as `py -3` or `python`.
- Administrator PowerShell.
- Outbound HTTPS to the console.
- For shutdown/reboot/service management, the scheduled task runs as `SYSTEM`.

### Install

Copy the source tree to the Windows host, then run an elevated PowerShell. The Windows installer checks for `pyproject.toml`, so `-SourcePath` must be the package source directory, not a built wheel file:

```powershell
Set-Location C:\path\to\ccdadisman
.\deploy\install-windows-agent.ps1 `
  -Server https://ops.example.com `
  -Token rc_enroll_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx `
  -Name "Studio PC" `
  -WatchServices Spooler, WSearch
```

If you copied only the script, point it at the package source directory:

```powershell
.\install-windows-agent.ps1 `
  -Server https://ops.example.com `
  -Token rc_enroll_... `
  -SourcePath C:\packages\runner-control `
  -CaFile C:\certs\corp-root-ca.pem
```

The installer:

1. creates `C:\ProgramData\RunnerAgent\venv`;
2. installs the package;
3. enrolls and writes `C:\ProgramData\RunnerAgent\config.json`;
4. restricts the config ACL to SYSTEM and Administrators;
5. registers the **RunnerControlAgent** scheduled task at startup, running as SYSTEM;
6. starts the task immediately.

Check it:

```powershell
Get-ScheduledTask RunnerControlAgent | Format-List
Get-Content C:\ProgramData\RunnerAgent\agent.log -Tail 50
C:\ProgramData\RunnerAgent\venv\Scripts\runner-agent.exe check --config C:\ProgramData\RunnerAgent\config.json
```

If Python was installed after the script started, open a new elevated PowerShell so `py` is on `PATH`.

### Uninstall

```powershell
Stop-ScheduledTask -TaskName RunnerControlAgent -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName RunnerControlAgent -Confirm:$false
Remove-Item -Recurse -Force C:\ProgramData\RunnerAgent
```

For Linux:

```bash
sudo systemctl disable --now runner-agent
sudo rm /etc/systemd/system/runner-agent.service
sudo rm -rf /opt/runner-agent /etc/runner-agent
sudo systemctl daemon-reload
```

## Configuration file

`runner-agent enroll` writes this JSON (token redacted):

```json
{
  "server_url": "https://ops.example.com",
  "agent_id": "agt_...",
  "agent_token": "rc_agent_...",
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
| `allow_self_update` | If false, ignore agent update notices and refuse `runner-agent update --apply` |
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
| Refresh | no args | Immediate success; next heartbeat refreshes metrics |

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

- The server may queue work for offline agents or reject it, depending on `RC_OFFLINE_COMMAND_POLICY`.
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
sudo -u runner-control RC_DATA_DIR=/var/lib/runner-control \
  /opt/runner-control/.venv/bin/runner-control list-agents
```

## Updating an agent

The supported self-update path uses the server package store. Publish an `agent-wheel` release under the `runner-agent` slug, then:

```bash
# Check only
sudo /opt/runner-agent/current/bin/runner-agent update --config /etc/runner-agent/config.json

# Download, verify SHA-256/size, stage a new venv, health-check, switch `current`, restart
sudo /opt/runner-agent/current/bin/runner-agent update \
  --config /etc/runner-agent/config.json --apply --restart
```

The service unit runs `/opt/runner-agent/current/bin/runner-agent`; the installer creates `current -> .venv`. The updater keeps versioned releases under `/opt/runner-agent/releases/` and prunes to the last three. If the staged health check fails, the current version is left untouched.

Windows stages the new version under `%ProgramData%\RunnerAgent\releases\` and reports `staged`; automatic scheduled-task switching is not enabled yet. Use the manual path:

```powershell
C:\ProgramData\RunnerAgent\venv\Scripts\pip.exe install --no-deps --upgrade C:\packages\runner-control
Stop-ScheduledTask RunnerControlAgent
Start-ScheduledTask RunnerControlAgent
```

Config and agent identity survive updates unless you re-enroll.

## Diagnose

```bash
sudo /opt/runner-agent/.venv/bin/runner-agent check --config /etc/runner-agent/config.json
sudo /opt/runner-agent/.venv/bin/runner-agent run --config /etc/runner-agent/config.json --once --log-level DEBUG
```

`--once` sends one heartbeat, executes any returned commands, posts results, and exits. It is useful for testing but not for normal operation.

Common causes of `401 Invalid agent credentials`:

- the token was rotated or the agent was deleted;
- the config file is from a different server;
- the agent ID and token were edited inconsistently;
- the enrollment was rolled back by restoring an old database.

Re-enroll with a fresh one-time token if the credential is gone. Do not copy one agent's config to another host: each agent has its own identity and token.
