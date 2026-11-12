# Agent domain join

**Status:** implemented for Linux and Windows agents.

Sextant can ask an enrolled agent to join its host to an Active Directory /
Samba AD domain. The action runs on the agent as root/SYSTEM over the existing
outbound heartbeat channel; no inbound port is opened.

Open a machine's drawer, choose **Join domain**, and fill in:

- Domain name
- Domain user (`CORP\svc-join` or `svc-join@corp.example.com`)
- Password (write-only)
- Optional computer name
- Optional target OU
- Optional domain controller (Linux `adcli` path)
- Flush local DNS caches before joining (on by default)
- Optional reboot after success
- Linux only: install missing `realmd`, SSSD, `adcli`, and Kerberos packages

The dialog also has **Check status**, which runs a read-only status command.

## Windows

Uses `Add-Computer` with a `PSCredential` and `-Restart:$false`. This avoids
restarting before the agent can upload the result. Enable **Reboot after a
successful join** or use the normal Reboot action when you are ready. Windows
Home cannot join a domain.

## Linux

Auto mode prefers `realm join` when realmd is installed. realmd receives the
password through a pseudo-terminal prompt, so it never appears in argv or the
log. If realmd is not available, `adcli join --stdin-password` is used; adcli
creates the computer account but does not fully configure SSSD/PAM, so verify
`sssd` and the login policy afterward. Supplying an explicit domain controller
switches auto mode to adcli so the override is honored. The status action
reports `realm list`, `adcli testjoin`, and `sssd` state.

Before joining, the agent optionally flushes local resolver caches
(`ipconfig /flushdns` on Windows; `resolvectl`, `systemd-resolve`, or `nscd` on
Linux). This does not update stale records on the AD DNS server itself.

Prerequisite installation is off by default. It requires the agent profile to
allow package management, then uses the host's apt/dnf/zypper/pacman package
manager. Unsupported distributions return a clear error.

## Security

- Admin-only endpoint and audited action.
- The command row stores no password; the server keeps it in memory only until
  the agent's next heartbeat and then removes it.
- The agent passes it through a child-only environment variable on Windows or a
  PTY prompt/adcli stdin on Linux.
- Output is redacted if the password ever appears in it.
- A server restart between queueing and pickup drops the secret and the command
  fails safely instead of falling back to a cached credential.

## Prerequisites

- Windows: DNS points at the DC, clock within five minutes, and the account has
  delegated create-computer-object rights or the default machine-account quota.
- Linux: `realmd` + `sssd` or `adcli`, Kerberos packages, DNS SRV records, and a
  clock within five minutes.
- The domain user must be allowed to join computers, not necessarily a Domain
  Admin.

## API

| Method | Route | Role | Notes |
|---|---|---|---|
| POST | `/api/agents/{id}/domain/join` | admin | Queues the join with a write-only password. |
| POST | `/api/agents/{id}/domain/status` | admin | Queues a read-only status check. |

The resulting command is visible through the normal command API and log view.
