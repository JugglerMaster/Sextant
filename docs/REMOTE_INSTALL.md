# Remote install over SSH

**Status:** implemented for **Linux**, key-based SSH only, one job at a time. Windows/OpenSSH is not implemented.

The **Remote install** page (admin) installs the Runner Agent on a Linux host using a single server-side SSH key. It is intended for machines you control, especially freshly rebuilt hosts where host keys change.

---

## Requirements

On the server:

- `ssh` and `scp` binaries (OpenSSH client).
- One private key at `RC_SSH_KEY_PATH` (default `<data>/ssh/install_key`), mode `0600`, readable by the `runner-control` user.
- Outbound TCP 22 to the target.
- A published `agent-wheel` release under the `runner-agent` store slug.
- `RC_SSH_INSTALL_ENABLED=1` (default).

On the target:

- Linux with `systemd`.
- Python 3.11+ and `python3-venv`.
- SSH server reachable, key-based login for the selected user.
- Passwordless `sudo` (`sudo -n`) for that user, or root login.
- Outbound HTTPS to the console (for enrollment).

The target does **not** need an inbound agent port.

---

## Key setup

Generate a dedicated key on the server:

```bash
sudo install -d -m 0700 -o runner-control -g runner-control /var/lib/runner-control/ssh
sudo -u runner-control ssh-keygen -t ed25519 -N '' \
  -f /var/lib/runner-control/ssh/install_key -C runner-control-install
sudo chmod 600 /var/lib/runner-control/ssh/install_key
sudo cat /var/lib/runner-control/ssh/install_key.pub
```

Install the public key on each target (for example in `/root/.ssh/authorized_keys`, or for a sudo-capable service account), then verify:

```bash
sudo -u runner-control ssh -i /var/lib/runner-control/ssh/install_key \
  -o BatchMode=yes -o StrictHostKeyChecking=no root@192.0.2.50 \
  'python3 --version && sudo -n true && systemctl --version | head -1'
```

Override the key path with:

```dotenv
RC_SSH_KEY_PATH=/var/lib/runner-control/ssh/install_key
```

The key is **not stored in SQLite**. Never upload it through the browser or copy it into the package store.

---

## Host-key handling

Host-key verification is intentionally **disabled** for this installer:

```text
-o StrictHostKeyChecking=no
-o UserKnownHostsFile=/dev/null
-o GlobalKnownHostsFile=/dev/null
```

This is deliberate because target PCs are frequently reimaged and their host keys change. It means the installer does not detect a MITM on the SSH path; use it only on a trusted admin network/VPN. The key is the only authentication factor.

If you later want stricter behavior, make it configurable and default to fingerprint approval.

---

## What the job does

1. Checks `python3 --version`, `systemctl`, and `sudo -n` over SSH.
2. Uploads the agent wheel and a systemd unit to `/tmp` with `scp`.
3. Creates `/opt/runner-agent` and `/etc/runner-agent`.
4. Creates or reuses `/opt/runner-agent/.venv`, installs the wheel with `--no-deps`.
5. Creates `current -> .venv`.
6. Enrolls with a freshly issued one-time enrollment token, the selected profile, and optional watched services.
7. Restricts the config to mode `0600`.
8. Installs the unit, `systemctl daemon-reload`, `enable --now`.
9. Verifies `systemctl is-active runner-agent`.
10. Removes the temporary wheel/unit.

The job log is stored in SQLite and streamed to the page by polling. The enrollment token is not written to the job log.

---

## UI

**Remote install** page (admin):

- host, SSH port, SSH user, display name, profile, watch services;
- **Test connection** checks Python, systemd, and passwordless sudo without installing;
- **Install agent** creates a job and polls its log;
- job history with status, target, profile, error, and timestamps.

Only one remote install job can be queued/running at a time. This is intentional for the current scope.

---

## API

| Method | Route | Role | Purpose |
|---|---|---|---|
| POST | `/api/remote-install/test` | admin | SSH + prerequisite check |
| POST | `/api/remote-install` | admin | Start an install job |
| GET | `/api/remote-install` | admin | List jobs |
| GET | `/api/remote-install/{id}` | admin | Job detail and log |

Create body:

```json
{
  "host": "192.0.2.50",
  "port": 22,
  "user": "root",
  "name": "fileserver-01",
  "profile": "server",
  "watch_services": ["ssh", "nginx"]
}
```

The server issues a 30-minute one-time enrollment token for the job and consumes it through the normal enrollment flow.

---

## Security

- Admin-only; every job creation, success, and failure is audited.
- Key-based auth only; no SSH passwords and no `sshpass`.
- Key must be mode 0600 and is read from the server filesystem, not the database.
- Fixed install script; no arbitrary remote command execution through the API.
- Host, port, user, profile, name, and services are validated; names are shell-quoted.
- No SSH agent forwarding, no port forwarding, no TTY.
- `sudo -n` is required; the installer will not prompt for a password.
- One job at a time; job log capped at 200,000 characters.
- Host-key verification is disabled by design; use a VPN/trusted admin network.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `SSH key not found` | Create the key at `RC_SSH_KEY_PATH` and chmod 600 |
| `Permission denied (publickey)` | Add the public key to the target user's `authorized_keys` |
| `sudo: a password is required` | Configure passwordless sudo or use root |
| `Python 3.11+ required` | Install a newer Python and `python3-venv` |
| `systemctl: command not found` | The target is not systemd-based |
| `Publish an agent-wheel release...` | Publish a `runner-agent` `agent-wheel` release in the store |
| `Another remote install is already running` | Wait for the current job to finish |
| `Host key verification failed` | Should not occur with this installer; check the SSH client options |
| Agent installed but offline | Confirm the target can reach `RC_PUBLIC_URL` over HTTPS and check `journalctl -u runner-agent` |

## Future

- Windows OpenSSH support.
- Bulk/import targets.
- Optional host-key fingerprint approval.
- Scheduling remote installs as a policy.
- Multiple keys or per-target key selection.
