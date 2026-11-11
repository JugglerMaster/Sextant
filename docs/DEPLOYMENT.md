# Deployment

For the complete step-by-step install (server, Linux agent, Windows agent), see [INSTALL.md](INSTALL.md). This document is the production checklist and deployment reference.

This guide targets a small Linux server (Ubuntu 22.04/24.04, Debian 12, or similar), nginx TLS termination, systemd, and one or more Linux/Windows endpoints. Adjust paths if you use containers or a different init system.

## 1. Prerequisites

On the server:

```bash
sudo apt update
sudo apt install -y python3 python3-venv nginx sqlite3 rsync
python3 --version   # 3.11 or newer
```

Plan for:

- a DNS name such as `ops.example.com`;
- a TLS certificate (Let's Encrypt is fine);
- a VPN or trusted admin network; do not publish the dashboard openly;
- outbound UDP 9 (or your WoL port) on the server's LAN interface;
- outbound LDAPS 636 to domain controllers if using AD;
- outbound HTTPS 443 from endpoints to the server.

## 2. Create the service account and directories

```bash
sudo useradd --system --home /opt/runner-control --shell /usr/sbin/nologin runner-control
sudo install -d -o root -g runner-control -m 0755 /opt/runner-control
sudo install -d -o runner-control -g runner-control -m 0700 /var/lib/runner-control
```

The service does **not** run as root. WoL, SQLite, and LDAP do not require root. Only the endpoint agents need elevated privileges for shutdown/service management.

## 3. Install the application

Copy the source tree to `/opt/runner-control` (or clone it), then:

```bash
sudo rsync -a --delete --exclude .venv --exclude data ./ /opt/runner-control/
sudo python3 -m venv /opt/runner-control/.venv
sudo /opt/runner-control/.venv/bin/pip install --upgrade pip
sudo /opt/runner-control/.venv/bin/pip install --require-hashes -r /opt/runner-control/requirements.lock
sudo /opt/runner-control/.venv/bin/pip install --no-deps /opt/runner-control
sudo chown -R root:runner-control /opt/runner-control
```

Check the console script:

```bash
sudo -u runner-control /opt/runner-control/.venv/bin/runner-control --help
```

## 4. Configure environment

```bash
sudo install -m 0640 -o root -g runner-control \
  /opt/runner-control/deploy/runner-control.env.example \
  /etc/runner-control.env
sudoedit /etc/runner-control.env
```

At minimum set:

```dotenv
RC_DATA_DIR=/var/lib/runner-control
RC_PUBLIC_URL=https://ops.example.com
RC_ALLOWED_HOSTS=ops.example.com,127.0.0.1,localhost
RC_COOKIE_SECURE=1
RC_TRUST_PROXY=1

# Recommended command safety posture:
RC_OFFLINE_COMMAND_POLICY=reject
RC_MAX_QUEUED_COMMANDS_PER_AGENT=50
RC_QUEUED_COMMAND_TTL_HOURS=24
RC_DISPATCHED_GRACE_SECONDS=120
RC_SESSION_CLEANUP_DAYS=7
RC_COMMAND_RETENTION_DAYS=90
RC_AUDIT_RETENTION_DAYS=365

# Package store (Tier 0: SHA-256 integrity, no signatures).
RC_STORE_ENABLED=1
RC_STORE_MAX_BLOB_MB=512
RC_STORE_MAX_TOTAL_GB=20

# Remote install over SSH (Linux only, key-based, one job at a time).
# RC_SSH_INSTALL_ENABLED=1
# RC_SSH_KEY_PATH=/var/lib/runner-control/ssh/install_key

# Server update check (notify-only). github:owner/repo or an HTTPS JSON URL.
# RC_UPDATE_FEED=github:owner/repo
# RC_UPDATE_CHECK_HOURS=6
```

The package store lives in `RC_DATA_DIR/store`. Back it up with the database. The update check only reads release metadata; it does not apply server updates automatically.

`RC_OFFLINE_COMMAND_POLICY=queue` is the default. Use `reject` if you do not want an operator to accidentally schedule a shutdown or shell command for a machine that is currently offline.

Notes:

- If `RC_ADMIN_PASSWORD` is unset and the database is empty, the first start creates a random administrator password in `/var/lib/runner-control/bootstrap-admin.txt` (mode 0600). Sign in, change it, and delete the file.
- If you prefer to seed the password, set `RC_ADMIN_PASSWORD` before first start and remove it afterward. The value must satisfy the password policy (at least 12 characters, not a common/repeated/sequential password); startup refuses a weak value. Environment files are readable by root and the service group.
- `RC_SECRET_KEY`, when set, must be at least 32 bytes of high-entropy data. Otherwise the server generates `secret.key` on first start.
- `RC_ALLOWED_HOSTS` is a comma-separated allowlist for the `Host` header. It is derived from `RC_PUBLIC_URL` when unset; set it explicitly if the console is reached by several names or IPs. Include `127.0.0.1,localhost` if you use local health checks.
- Do not set `RC_COOKIE_SECURE=0` on a real HTTPS deployment. The browser will not send the cookie over plain HTTP; that is intentional.
- `RC_TRUST_PROXY=1` is required for correct client IPs/rate limiting behind nginx, and only safe when nginx overwrites `X-Forwarded-For` with `$remote_addr`.

Optional directory settings are documented in [AD_LDAP.md](AD_LDAP.md).

## 5. Install the systemd unit

```bash
sudo install -m 0644 /opt/runner-control/deploy/runner-control.service /etc/systemd/system/runner-control.service
sudo systemctl daemon-reload
sudo systemctl enable --now runner-control
sudo systemctl status runner-control --no-pager
```

If the generated bootstrap password was used:

```bash
sudo cat /var/lib/runner-control/bootstrap-admin.txt
```

Then sign in at `https://ops.example.com` (after step 6), change the password, and delete the file.

Check local health:

```bash
curl -fsS http://127.0.0.1:8080/api/health
```

## 6. Put TLS in front with nginx

```bash
sudo cp /opt/runner-control/deploy/nginx.conf /etc/nginx/sites-available/runner-control
sudo ln -s /etc/nginx/sites-available/runner-control /etc/nginx/sites-enabled/runner-control
sudo nginx -t
sudo systemctl reload nginx
```

Edit `server_name`, certificate paths, and the upstream if needed. Obtain a certificate first (for example with certbot). The sample config:

- redirects HTTP to HTTPS;
- proxies to `127.0.0.1:8080`;
- overwrites `X-Forwarded-For` with `$remote_addr` and sets `X-Forwarded-Proto`;
- rate-limits `/api/auth/login`.

The sample allows large uploads (`client_max_body_size 512m`) for the package store. The application still enforces its own per-file and total limits, and non-store API bodies remain capped by `RC_MAX_BODY_BYTES`.

The app also sets CSP, frame denial, MIME sniffing protection, referrer policy, and HSTS (when `RC_COOKIE_SECURE=1`).

### Direct TLS alternative

You can skip nginx and give uvicorn a certificate:

```bash
/opt/runner-control/.venv/bin/runner-control serve \
  --host 0.0.0.0 --port 8443 \
  --ssl-keyfile /etc/ssl/private/ops.key \
  --ssl-certfile /etc/ssl/certs/ops.crt
```

Set `RC_COOKIE_SECURE=1` and update the systemd unit. nginx is still recommended for certificate renewal, access logging, and request limits.

## 7. Firewall

```text
Internet/VPN ── 443/tcp ──► nginx
admin LAN   ── 22/tcp  ──► SSH (restricted)
loopback    ── 8080/tcp ─► app (never expose)
server      ── 636/tcp ──► domain controllers (LDAPS)
server      ── 9/udp   ──► LAN broadcast (WoL)
endpoints   ── 443/tcp ──► server (outbound only)
```

If you use UFW:

```bash
sudo ufw allow from 10.0.0.0/8 to any port 443 proto tcp
sudo ufw allow from 10.0.0.0/8 to any port 22 proto tcp
sudo ufw enable
```

Do not allow 8080 from untrusted networks.

## 8. Create the first enrollment token

Use the CLI as the service account so the SQLite file stays writable by the service:

```bash
sudo -u runner-control \
  RC_DATA_DIR=/var/lib/runner-control \
  RC_PUBLIC_URL=https://ops.example.com \
  /opt/runner-control/.venv/bin/runner-control issue-enrollment \
  --label "first Linux box" --minutes 30
```

The token is printed once. Use it in the agent installer or the **Enrollment** page in the dashboard.

## 9. Install agents

- Linux: [AGENTS.md#linux](AGENTS.md#linux)
- Windows: [AGENTS.md#windows](AGENTS.md#windows)

Validate that the agent appears in the dashboard within one heartbeat interval.

## Validation

Automated (run in a development checkout; the production venv intentionally omits test dependencies):

```bash
cd /path/to/ccdadisman
.venv/bin/pytest -q
.venv/bin/python -m compileall -q runner_control runner_agent
node --check runner_control/static/app.js
cd ui-tests && npm ci && npm test
```

End-to-end smoke test against a live server:

```bash
# 1. Log in and keep the session cookie + CSRF token.
curl -fsS -c /tmp/rc.cookies \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR PASSWORD","mode":"auto"}' \
  https://ops.example.com/api/auth/login > /tmp/rc.login.json
CSRF=$(python3 -c 'import json;print(json.load(open("/tmp/rc.login.json"))["csrf"])')

# 2. Confirm the dashboard is authenticated.
curl -fsS -b /tmp/rc.cookies https://ops.example.com/api/dashboard | python3 -m json.tool | head

# 3. After enrolling an agent, queue a harmless command.
AGENT=$(curl -fsS -b /tmp/rc.cookies https://ops.example.com/api/dashboard \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["agents"][0]["id"])')

curl -fsS -b /tmp/rc.cookies \
  -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"kind":"shell","args":{"shell":"bash","command":"printf ok; uname -s","timeout":30}}' \
  "https://ops.example.com/api/agents/$AGENT/commands"

# 4. Poll commands/agent detail or use the dashboard Remote tab for the result.
```

A healthy agent heartbeat appears as online within `RC_OFFLINE_AFTER_SECONDS` (default 90) and should show disk/service telemetry.

## Backups

The database contains command output, agent identities, users, sessions, and audit history.

Online backup:

```bash
sudo install -d -o runner-control -g runner-control -m 0700 /var/backups/runner-control
sudo -u runner-control sqlite3 /var/lib/runner-control/runner-control.sqlite3 \
  ".backup '/var/backups/runner-control/runner-control-$(date +%F).sqlite3'"
```

Also back up:

- `/var/lib/runner-control/secret.key` (or `RC_SECRET_KEY`) — losing it invalidates all sessions;
- `/etc/runner-control.env`;
- nginx TLS material (standard backup).

The database is written in WAL mode. A plain `cp` while running can be inconsistent; use `.backup` or stop the service first.

## Upgrade

```bash
sudo systemctl stop runner-control
sudo -u runner-control sqlite3 /var/lib/runner-control/runner-control.sqlite3 \
  ".backup '/var/backups/runner-control/pre-upgrade.sqlite3'"
# update /opt/runner-control source
sudo /opt/runner-control/.venv/bin/pip install --require-hashes -r /opt/runner-control/requirements.lock
sudo /opt/runner-control/.venv/bin/pip install --no-deps --upgrade /opt/runner-control
sudo systemctl start runner-control
curl -fsS http://127.0.0.1:8080/api/health
```

The schema is created idempotently and additive column migrations run at startup (the users active flag, agent machine ID, watched services, policy columns, profile ID, and service-inventory timestamp); the session registry, `telemetry_samples`, `store_packages`/`store_releases`, `package_inventory`, `package_rules`, `package_inventory_state`, `agent_profiles`, `profile_tag_assignments`, and `remote_install_jobs` tables are also created idempotently (`PRAGMA user_version=6`). There is still no general migration framework; take a backup and inspect release notes before upgrading a production instance.

Upgrading from v0.1 to v0.2 introduces the server-side session registry. Existing browser cookies are invalidated; users must sign in again. Existing agents populate their machine ID on the next heartbeat unless they are re-enrolled.

## Uninstall

```bash
sudo systemctl disable --now runner-control
sudo rm /etc/systemd/system/runner-control.service
sudo systemctl daemon-reload
# Keep /var/lib/runner-control if you may need the audit log or command history.
```

Remove the nginx site and TLS certificate separately.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| 502 from nginx | `systemctl status runner-control`; app bound to `127.0.0.1:8080` |
| Session immediately expires | `RC_COOKIE_SECURE=1` while browsing over HTTP, or proxy not setting `X-Forwarded-Proto` |
| Client IP is `127.0.0.1` | Set `RC_TRUST_PROXY=1` and make nginx overwrite `X-Forwarded-For` with `$remote_addr` |
| Login says directory unavailable | LDAPS reachability, CA file, bind DN/password; `journalctl -u runner-control` |
| Agent 401 | Token rotated/deleted, agent re-enrolled elsewhere, wrong config path, wrong server URL |
| User cannot sign in | Account disabled, session revoked, or directory password changed; check the audit log and Users page |
| Command stays queued | Offline policy is `queue` and agent has not heartbeated; check queue cap and TTL |
| Command will not cancel | It is already `dispatched`; the agent may execute it. Wait for the result or let the stale-command sweep fail it |
| Agent never appears | Outbound TLS, DNS, CA trust, clock, `journalctl` on server and agent logs |
| WoL does nothing | Server and target on different VLAN/broadcast domain; BIOS/NIC wake enabled; use directed broadcast |
| Disk alert on a tiny partition | Alert thresholds are fixed percentages (warning at 80%, critical at 90%); there is no per-partition threshold setting |

Logs:

```bash
sudo journalctl -u runner-control -f
sudo journalctl -u runner-agent -f
```

## Validation status

The repository test suite exercises the API, security primitives, sessions/users, command guardrails, agent policy and re-enrollment, agent parsers/executor, AD role mapping, and a simulated agent workflow. The live smoke test above is the manual end-to-end check; it is not automated in the repository. Run it with a real uvicorn server and a real Linux agent to cover enrollment, heartbeat, a remote shell command, result capture, and audit entries. [docs/TESTING.md](TESTING.md) lists the remaining coverage gaps.
