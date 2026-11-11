# Installation guide

This is the end-to-end install guide for Sextant. It covers:

- the **server** (Linux only in the supported deployment);
- the **agent on Linux**;
- the **agent on Windows**;
- first sign-in, enrollment, verification, upgrades, and uninstall.

For architecture and security background, see [ARCHITECTURE.md](ARCHITECTURE.md) and [SECURITY.md](SECURITY.md). For a deeper production checklist, see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 1. What you are installing

```text
admin browser ── HTTPS ──► nginx ──► Sextant server (127.0.0.1:8080)
                                              │
                              outbound HTTPS  │  UDP 9 (WoL, optional)
                                              ▼
                                   Linux / Windows agents
```

| Component | Supported platform | Runs as | Inbound ports |
|---|---|---|---|
| Server | Linux with systemd (Ubuntu/Debian/RHEL-family) | `sextant` user | none directly; nginx exposes 443 |
| Linux agent | systemd Linux | `root` | none |
| Windows agent | Windows 10/11, Server 2019+ | `SYSTEM` scheduled task | none |

The server never connects inbound to an endpoint. Agents poll the server over outbound HTTPS.

> **Windows server:** not a supported deployment. There is no Windows service wrapper or nginx config for the server. See [Appendix A](#appendix-a--server-on-windows-unsupported).

### Prerequisites checklist

- Python **3.11+** on the server and on every agent host (agents are Python programs).
- A DNS name, for example `ops.example.com`.
- A TLS certificate (Let's Encrypt is fine).
- A VPN or trusted admin network. **Do not expose the dashboard to the public internet.**
- Outbound HTTPS 443 from each endpoint to the server.
- Optional: outbound UDP 9 on the server's LAN for Wake-on-LAN.
- Optional: outbound LDAPS 636 from the server to domain controllers.

---

## 2. Install the server on Linux

The examples use Ubuntu/Debian. RHEL-family equivalents are shown where they differ.

### 2.1 Install OS packages

Ubuntu / Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv nginx sqlite3 rsync curl
python3 --version   # must be 3.11 or newer
```

RHEL / Rocky / AlmaLinux:

```bash
sudo dnf install -y python3 python3-pip nginx sqlite rsync curl
python3 --version
```

### 2.2 Create the service account and directories

```bash
sudo useradd --system --home /opt/sextant --shell /usr/sbin/nologin sextant
sudo install -d -o root -g sextant -m 0755 /opt/sextant
sudo install -d -o sextant -g sextant -m 0700 /var/lib/sextant
```

The server does **not** run as root. Only agents need elevated privileges.

### 2.3 Copy the source and install dependencies

From the checkout on your workstation, copy the tree (or clone it directly on the server):

```bash
sudo rsync -a --delete \
  --exclude .venv --exclude data --exclude node_modules --exclude __pycache__ \
  ./ /opt/sextant/
```

Then install with the hash-pinned lockfile:

```bash
sudo python3 -m venv /opt/sextant/.venv
sudo /opt/sextant/.venv/bin/pip install --upgrade pip
sudo /opt/sextant/.venv/bin/pip install --require-hashes -r /opt/sextant/requirements.lock
sudo /opt/sextant/.venv/bin/pip install --no-deps /opt/sextant
sudo chown -R root:sextant /opt/sextant
```

Optional, only if using Active Directory / Samba AD:

```bash
sudo /opt/sextant/.venv/bin/pip install 'ldap3>=2.9,<3'
```

Check the entry point:

```bash
sudo -u sextant /opt/sextant/.venv/bin/sextant --help
```

> **Development install instead:** `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` is fine for a lab. Use the lockfile path for anything persistent.

### 2.4 Configure the environment

```bash
sudo install -m 0640 -o root -g sextant \
  /opt/sextant/deploy/sextant.env.example \
  /etc/sextant.env
sudoedit /etc/sextant.env
```

Minimum production values:

```dotenv
SEXTANT_DATA_DIR=/var/lib/sextant
SEXTANT_PUBLIC_URL=https://ops.example.com
SEXTANT_ALLOWED_HOSTS=ops.example.com,127.0.0.1,localhost
SEXTANT_COOKIE_SECURE=1
SEXTANT_TRUST_PROXY=1

# Recommended command safety posture:
SEXTANT_OFFLINE_COMMAND_POLICY=reject
SEXTANT_MAX_QUEUED_COMMANDS_PER_AGENT=50
SEXTANT_QUEUED_COMMAND_TTL_HOURS=24
SEXTANT_DISPATCHED_GRACE_SECONDS=120
SEXTANT_SESSION_CLEANUP_DAYS=7
SEXTANT_COMMAND_RETENTION_DAYS=90
SEXTANT_AUDIT_RETENTION_DAYS=365
```

Important:

- `SEXTANT_SECRET_KEY` is optional. If set, it must be **at least 32 high-entropy bytes**; otherwise the server generates `/var/lib/sextant/secret.key`.
- `SEXTANT_ADMIN_PASSWORD` is optional and only used when the database has no users. It must pass the password policy (≥12 characters, not common/repeated/sequential); a weak value refuses startup. If unset, a random password is written to `/var/lib/sextant/bootstrap-admin.txt`.
- `SEXTANT_ALLOWED_HOSTS` is derived from `SEXTANT_PUBLIC_URL` when unset. Add extra names/IPs if you reach the console through more than one hostname; include `127.0.0.1,localhost` if you use the local health check below.
- `SEXTANT_TRUST_PROXY=1` is only correct when nginx overwrites `X-Forwarded-For` (the bundled config does).

### 2.5 Install and start the systemd service

```bash
sudo install -m 0644 /opt/sextant/deploy/sextant.service /etc/systemd/system/sextant.service
sudo systemctl daemon-reload
sudo systemctl enable --now sextant
sudo systemctl status sextant --no-pager
```

Check it locally:

```bash
curl -fsS http://127.0.0.1:8080/api/health
```

If the bootstrap password was generated:

```bash
sudo cat /var/lib/sextant/bootstrap-admin.txt
```

### 2.6 Put TLS in front with nginx

Obtain a certificate first (for example with certbot), then install the sample config:

```bash
sudo cp /opt/sextant/deploy/nginx.conf /etc/nginx/sites-available/sextant
sudo ln -s /etc/nginx/sites-available/sextant /etc/nginx/sites-enabled/sextant
sudo nginx -t
sudo systemctl reload nginx
```

Edit `server_name`, the certificate paths, and the upstream if needed. The sample:

- redirects HTTP to HTTPS;
- proxies to `127.0.0.1:8080`;
- overwrites `X-Forwarded-For` with `$remote_addr`;
- rate-limits `/api/auth/login`.

On RHEL-family, the snippet usually lives in `/etc/nginx/conf.d/sextant.conf` instead of `sites-available`.

### 2.7 Firewall

Open only what you need:

```text
inbound  443/tcp    from the admin VPN / trusted LAN
inbound  80/tcp     only if using ACME HTTP-01 (redirects to 443)
outbound 636/tcp    to domain controllers, if using LDAPS
outbound 9/udp      WoL broadcast, if using Wake-on-LAN
outbound 443/tcp    from endpoints to the server
```

Do **not** expose `8080` (the app binds loopback in the unit) or the SQLite data directory.

Ubuntu (UFW) example:

```bash
sudo ufw allow from 10.0.0.0/8 to any port 443 proto tcp
sudo ufw enable
```

RHEL (firewalld) example:

```bash
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### 2.8 First sign-in

1. Open `https://ops.example.com`.
2. Sign in as `admin` with the bootstrap password or `SEXTANT_ADMIN_PASSWORD`.
3. Change the password immediately.
4. Delete `/var/lib/sextant/bootstrap-admin.txt` if it was generated.

### 2.9 Create an enrollment token

Use **Enrollment** in the dashboard, or the CLI:

```bash
sudo -u sextant \
  SEXTANT_DATA_DIR=/var/lib/sextant \
  /opt/sextant/.venv/bin/sextant issue-enrollment \
  --label "linux-lab" --minutes 30
```

The token is shown once. Copy it into the agent command below.

List enrolled agents any time:

```bash
sudo -u sextant \
  SEXTANT_DATA_DIR=/var/lib/sextant \
  /opt/sextant/.venv/bin/sextant list-agents
```

---

## 3. Install the Linux agent

### 3.1 One-command installer (recommended)

Run as root on the endpoint, with the source tree available:

```bash
sudo SOURCE_DIR=/path/to/sextant \
  WATCH_SERVICES=ssh,nginx,postgresql \
  AGENT_TAGS=office \
  PROFILE=server \
  ./deploy/install-linux-agent.sh \
  https://ops.example.com \
  sextant_enroll_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  "fileserver-01"
```

Use the service names `systemctl` knows. On Debian/Ubuntu the SSH unit is usually `ssh.service`; on RHEL-family it is `sshd.service`.

Optional installer variables:

| Variable | Purpose |
|---|---|
| `SOURCE_DIR` | Source tree (or built wheel) to install |
| `SERVICE_FILE` | Optional explicit path to `sextant-agent.service`; otherwise resolved from the source/script tree |
| `WATCH_SERVICES` | Comma-separated systemd units to monitor |
| `AGENT_TAGS` | Comma-separated tags shown in the dashboard |
| `CA_FILE` | Private CA bundle for TLS verification |
| `ALLOW_PACKAGES` | Set to `0` to disable managed package actions locally (default `1`) |
| `NO_SELF_UPDATE` | Set to `1` to disable agent self-update in the config |
| `PROFILE` | Profile slug selected at enrollment (default `workstation`) |
| `INSTALL_DIR` | Default `/opt/sextant-agent` |
| `CONFIG_DIR` | Default `/etc/sextant-agent` |
| `CONFIG_FILE` | Default `<CONFIG_DIR>/config.json` |

The installer creates `/opt/sextant-agent/.venv`, the `current -> .venv` symlink, installs the package with `--no-deps`, enrolls, writes mode-0600 config, installs `/etc/systemd/system/sextant-agent.service`, and starts it.

### 3.2 Manual install

```bash
sudo apt install -y python3 python3-venv       # or dnf install -y python3
sudo install -d -m 0755 /opt/sextant-agent
sudo python3 -m venv /opt/sextant-agent/.venv
sudo /opt/sextant-agent/.venv/bin/pip install --upgrade pip
sudo /opt/sextant-agent/.venv/bin/pip install --no-deps /path/to/sextant
sudo ln -sfn .venv /opt/sextant-agent/current
sudo install -d -m 0700 /etc/sextant-agent

sudo /opt/sextant-agent/.venv/bin/sextant-agent enroll \
  --config /etc/sextant-agent/config.json \
  --server https://ops.example.com \
  --token sextant_enroll_... \
  --name "$(hostname)" \
  --profile server \
  --watch-service ssh \
  --tag office

sudo chmod 0600 /etc/sextant-agent/config.json
sudo install -m 0644 /path/to/sextant/deploy/sextant-agent.service /etc/systemd/system/sextant-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now sextant-agent
```

Private CA: add `--ca-file /etc/ssl/certs/corp-root-ca.pem` to `enroll`, or set `CA_FILE` for the installer. Avoid `--insecure` outside a lab.

### 3.3 Verify

```bash
systemctl status sextant-agent --no-pager
journalctl -u sextant-agent -n 50 --no-pager
sudo /opt/sextant-agent/.venv/bin/sextant-agent check --config /etc/sextant-agent/config.json
```

The machine should appear in the dashboard within one heartbeat interval (default 30 seconds).

### 3.4 Update or uninstall

Preferred: publish an `agent-wheel` release under the `sextant-agent` slug in the server package store, then:

```bash
sudo /opt/sextant-agent/current/bin/sextant-agent update --config /etc/sextant-agent/config.json
sudo /opt/sextant-agent/current/bin/sextant-agent update \
  --config /etc/sextant-agent/config.json --apply --restart
```

The updater verifies SHA-256/size, stages a new versioned venv, health-checks it, switches the `current` symlink, and keeps the previous releases for rollback. The unit uses `/opt/sextant-agent/current/bin/sextant-agent`.

Fallback (manual package copy):

```bash
sudo /opt/sextant-agent/.venv/bin/pip install --no-deps --upgrade /path/to/new/sextant
sudo systemctl restart sextant-agent
```

Uninstall:

```bash
sudo systemctl disable --now sextant-agent
sudo rm /etc/systemd/system/sextant-agent.service
sudo rm -rf /opt/sextant-agent /etc/sextant-agent
sudo systemctl daemon-reload
```

---

## 4. Install the Windows agent

The Windows agent is supported on Windows 10/11 and Windows Server 2019+. It runs as a **SYSTEM** scheduled task.

### 4.1 Prerequisites

- Windows PowerShell 5.1+ (built in).
- **Python 3.11+** installed for all users, available as `py -3` or `python`.
- Administrator PowerShell.
- Outbound HTTPS 443 to the server.
- For shutdown/reboot/service control, the task must run as SYSTEM (the installer does this).

Install Python if needed:

```powershell
winget install -e --id Python.Python.3.12
```

or download it from <https://www.python.org/downloads/windows/>. During setup, choose **Install for all users** (or install the `py` launcher) and enable **Add python.exe to PATH**. Open a new PowerShell afterward and check:

```powershell
py -3 --version
```

### 4.2 Copy the source

Copy the source tree (or a built wheel) to the host, for example:

```text
C:\packages\sextant
```

It must contain `pyproject.toml` when installing from source.

### 4.3 Run the installer

Open **PowerShell as Administrator**, allow the script for this session, and run:

```powershell
Set-Location C:\packages\sextant
Set-ExecutionPolicy -Scope Process Bypass -Force

.\deploy\install-windows-agent.ps1 `
  -Server https://ops.example.com `
  -Token sextant_enroll_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx `
  -Name "Studio PC" `
  -Profile workstation `
  -WatchServices Spooler,WSearch
```

If you copied only the script, point it at the package:

```powershell
.\install-windows-agent.ps1 `
  -Server https://ops.example.com `
  -Token sextant_enroll_... `
  -SourcePath C:\packages\sextant `
  -CaFile C:\certs\corp-root-ca.pem
```

Parameters:

| Parameter | Purpose |
|---|---|
| `-Server` | Console base URL; HTTPS strongly recommended |
| `-Token` | One-time enrollment token |
| `-Name` | Display name (defaults to `$env:COMPUTERNAME`) |
| `-SourcePath` | Source tree containing `pyproject.toml` (defaults to the parent of the script) |
| `-InstallDir` | Default `C:\ProgramData\SextantAgent` |
| `-WatchServices` | Windows service **names** (not display names) |
| `-CaFile` | Private CA bundle for TLS verification |
| `-AllowPackages` | Deprecated: package management is enabled by default |
| `-NoPackages` | Disable managed package actions locally |
| `-NoSelfUpdate` | Disable agent self-update in the config |
| `-Profile` | Profile slug selected at enrollment (default `workstation`) |

The installer:

1. creates `C:\ProgramData\SextantAgent\venv`;
2. installs the package with `--no-deps`;
3. enrolls and writes `C:\ProgramData\SextantAgent\config.json`;
4. restricts the config ACL to SYSTEM and Administrators;
5. registers the **SextantAgent** scheduled task at startup, running as SYSTEM;
6. starts the task.

### 4.4 Verify

```powershell
Get-ScheduledTask SextantAgent | Format-List
Get-Content C:\ProgramData\SextantAgent\agent.log -Tail 50
C:\ProgramData\SextantAgent\venv\Scripts\sextant-agent.exe check --config C:\ProgramData\SextantAgent\config.json
```

The machine should appear in the dashboard within one heartbeat interval.

If Python was installed after the script started, open a new elevated PowerShell so `py` is on `PATH`.

### 4.5 Update or uninstall

Preferred: publish an `agent-wheel` release under the `sextant-agent` slug, then:

```powershell
C:\ProgramData\SextantAgent\venv\Scripts\sextant-agent.exe update --config C:\ProgramData\SextantAgent\config.json
C:\ProgramData\SextantAgent\venv\Scripts\sextant-agent.exe update --config C:\ProgramData\SextantAgent\config.json --apply
```

Windows currently **stages** the verified version under `%ProgramData%\SextantAgent\releases\` and reports `restart_required`; automatic scheduled-task switching is not enabled yet. Use the manual path to switch:

```powershell
C:\ProgramData\SextantAgent\venv\Scripts\pip.exe install --no-deps --upgrade C:\packages\sextant
Stop-ScheduledTask SextantAgent
Start-ScheduledTask SextantAgent
```

Uninstall:

```powershell
Stop-ScheduledTask -TaskName SextantAgent -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName SextantAgent -Confirm:$false
Remove-Item -Recurse -Force C:\ProgramData\SextantAgent
```

---

## 4.6 Server package store and managed packages (optional)

The server hosts a Tier 0 package store under `/var/lib/sextant/store`. Publish an artifact (agent wheel, `.deb`, `.rpm`, `.msi`, `.msix`, portable zip/exe) from the server checkout:

```bash
sudo -u sextant \
  SEXTANT_DATA_DIR=/var/lib/sextant \
  /opt/sextant/.venv/bin/sextant store-publish \
  --slug sextant-agent \
  --version 0.3.0 \
  --file /tmp/sextant-0.3.0-py3-none-any.whl \
  --artifact-type agent-wheel \
  --kind agent
```

List or yank releases:

```bash
sudo -u sextant SEXTANT_DATA_DIR=/var/lib/sextant \
  /opt/sextant/.venv/bin/sextant store-list
sudo -u sextant SEXTANT_DATA_DIR=/var/lib/sextant \
  /opt/sextant/.venv/bin/sextant store-yank rel_... --reason "bad build"
```

Package management is opt-in. The device **Packages** tab shows inventory, managers, drift, and a **Sync now** action; editing desired-state rules is API-only until the rule editor ships:

1. The agent-local `allow_packages` flag is enabled by default; enroll with `--no-packages` (or `ALLOW_PACKAGES=0` / `-NoPackages`) to disable it.
2. The assigned profile must enable `package_management` and `allow_packages` (both are on in the built-in profiles; turn them off to opt out).
3. Set per-device rules with `PUT /api/agents/{id}/packages`, then trigger `POST /api/agents/{id}/packages/sync`.

The agent reports installed packages on the profile's package cadence (24h workstation, 6h server) and only sends changed inventory. Rules are `present`, `absent`, `latest`, or a pinned `version`. Package actions run as root/SYSTEM, so enable them deliberately.

The server update check is configured with `SEXTANT_UPDATE_FEED` (for example `github:owner/repo`) and shows an admin banner when a newer release exists. It does not apply the server update automatically.

## 4.7 Remote install over SSH (optional, Linux)

Admins can install an agent on a Linux host from **Remote install** in the dashboard. Requirements:

- A single server-side private key at `SEXTANT_SSH_KEY_PATH` (default `<data>/ssh/install_key`), mode `0600`.
- The public key installed for a target user that has passwordless `sudo`.
- `ssh` and `scp` on the server, outbound TCP 22.
- A published `sextant-agent` `agent-wheel` release in the package store.
- Python 3.11+ and `python3-venv` on the target.

Host-key verification is disabled because rebuilt PCs change host keys. Use this only on a trusted admin network/VPN. One install job runs at a time. See [REMOTE_INSTALL.md](REMOTE_INSTALL.md) for key setup, API, and troubleshooting.

## 5. Verify end to end

1. The dashboard shows the machine as **online**.
2. **Live health** shows CPU, memory, disks, and — if NVIDIA drivers are installed — GPU data.
3. Queue a harmless command:

   ```bash
   hostname && uptime
   ```

   or on Windows:

   ```powershell
   hostname
   ```

4. Confirm the result and exit code appear in command history.
5. Optionally send a Wake-on-LAN packet to an enrolled machine (same L2 segment).

If the agent does not appear, see [Troubleshooting](#7-troubleshooting).

---

## 6. Upgrades and backups

### Server

```bash
sudo sqlite3 /var/lib/sextant/sextant.sqlite3 \
  ".backup '/var/backups/sextant/pre-upgrade.sqlite3'"
sudo rsync -a --delete --exclude .venv --exclude data ./ /opt/sextant/
sudo /opt/sextant/.venv/bin/pip install --require-hashes -r /opt/sextant/requirements.lock
sudo /opt/sextant/.venv/bin/pip install --no-deps --upgrade /opt/sextant
sudo systemctl restart sextant
curl -fsS http://127.0.0.1:8080/api/health
```

The database migrates additively at startup (`PRAGMA user_version`). Back up the SQLite database and `secret.key` before upgrading; losing the secret invalidates all sessions.

### Agents

Use the update commands in [§3.4](#34-update-or-uninstall) and [§4.5](#45-update-or-uninstall). Config and agent identity survive updates unless you re-enroll.

### Backup checklist

- `/var/lib/sextant/sextant.sqlite3` (and `-wal`/`-shm` if copying live)
- `/var/lib/sextant/secret.key` (or `SEXTANT_SECRET_KEY`)
- `/var/lib/sextant/store/` (package-store blobs)
- `/etc/sextant.env`
- `/opt/sextant` (code/config reference)
- Agent config files: `/etc/sextant-agent/config.json`, `C:\ProgramData\SextantAgent\config.json`

---

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `401 Invalid agent credentials` | Token rotated, agent deleted, config from another server, or inconsistent `agent_id`/token. Re-enroll with a fresh token. |
| Agent installed but never appears | Outbound HTTPS blocked; wrong DNS/TLS name; certificate not trusted (use `--ca-file` or fix the chain); check `journalctl -u sextant-agent` or `agent.log`. |
| `401` on Windows task but `check` works | The scheduled task runs as SYSTEM and may not see user-installed Python/`PATH`; rerun the installer after installing Python for all users. |
| Dashboard shows client IP `127.0.0.1` | nginx is not forwarding the real IP. Set `SEXTANT_TRUST_PROXY=1` and make nginx overwrite `X-Forwarded-For` with `$remote_addr`. |
| Login returns `429` | Rate limited after repeated failures. Wait for the lock window or restart the server (in-process limiter). |
| `Cannot demote or deactivate the last active administrator` | Create/promote another admin first. |
| `SEXTANT_ADMIN_PASSWORD is not acceptable` | Use ≥12 characters and avoid common/repeated/sequential values, or unset it to auto-generate. |
| `SEXTANT_SECRET_KEY must be at least 32 bytes` | Use a password manager or `python -c "import secrets; print(secrets.token_hex(32))"`. |
| Wake-on-LAN does nothing | Server and target must share an L2 broadcast domain; check UDP 9 and the target MAC. |
| Service monitoring shows nothing | Use the platform's real service name (`ssh.service` vs `sshd.service`; Windows service *name*, not display name). Startup/service inventory refreshes on the `inventory_interval` cadence (default 1 hour). |
| GPU data missing | Install NVIDIA drivers with `nvidia-smi` in `PATH`; AMD ROCm is not collected. |
| Windows command needs a reboot | Zero-delay shutdown may power off before the result posts. Use the default delay. |

Logs:

```bash
# Server
journalctl -u sextant -n 100 --no-pager

# Linux agent
journalctl -u sextant-agent -n 100 --no-pager
```

```powershell
# Windows agent
Get-Content C:\ProgramData\SextantAgent\agent.log -Tail 100
```

---

## 8. Security notes

- Put the console behind a VPN or trusted admin network; do not publish it to the internet.
- Use HTTPS end to end. `SEXTANT_COOKIE_SECURE=1` is required behind TLS.
- Operators are effectively **remote root/SYSTEM** on managed hosts. Grant the role deliberately.
- Use the agent-local `allow_shell`, `allow_power`, and `allow_service` flags to reduce blast radius; the server can restrict further but cannot re-enable a local denial.
- Keep `SEXTANT_SECRET_KEY`, agent tokens, and config files mode 0600 / ACL-restricted.
- Review [SECURITY.md](SECURITY.md) before exposing the console to a wider network.

---

## Appendix A — Server on Windows (unsupported)

Sextant's supported server deployment is Linux + systemd. Windows has no provided service unit, TLS proxy config, or installer for the server. The code itself is cross-platform Python, so a lab setup can run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\pip install -e '.[dev]'
$env:SEXTANT_DATA_DIR = 'C:\ProgramData\sextant'
$env:SEXTANT_ADMIN_PASSWORD = 'use-a-password-manager'
.\.venv\Scripts\sextant serve --host 127.0.0.1 --port 8080
```

You would also need:

- a TLS reverse proxy (Caddy, IIS ARR, or nginx for Windows);
- a service wrapper (Task Scheduler "At startup" or NSSM);
- a firewall rule for 443;
- your own backup/log rotation.

This path is not tested by the project. Prefer a small Linux VM for the server, even if all endpoints are Windows. For Wake-on-LAN, the server must be on the same L2 broadcast domain as the targets.

## Appendix B — Air-gapped installs

On a connected machine, build a wheel and download the locked dependencies:

```bash
python3 -m pip wheel . -w dist/
pip download --require-hashes -r requirements.lock -d wheels/
```

Copy the wheel, `wheels/`, and `sextant.service` / `sextant-agent.service` to the offline host, then:

```bash
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links wheels/ --require-hashes -r requirements.lock
.venv/bin/pip install --no-deps dist/sextant-*.whl
```

Agent installs are simpler: the agent is stdlib-only, so a wheel with `--no-deps` is sufficient.

---

## Related documents

- [Deployment](DEPLOYMENT.md) — production checklist, nginx/TLS, backup details.
- [Agent profiles](PROFILES.md) — server-managed behavior, cadence, modules, and installer profile selection.
- [Agents](AGENTS.md) — agent configuration, policy, command behavior, diagnostics.
- [Active Directory / Samba AD](AD_LDAP.md) — optional LDAP(S) setup.
- [Security](SECURITY.md) — threat model and hardening.
- [API](API.md) — HTTP API reference.
- [Testing](TESTING.md) — how to run the test suite and what is not covered.
