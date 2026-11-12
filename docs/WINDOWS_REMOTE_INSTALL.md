# Windows remote install — plan

**Status:** proposed. Not implemented.

Sextant currently supports remote agent installation on Linux over key-based
SSH only. The Discovery onboarding flow can generate a **manual elevated
PowerShell** enrollment command for a discovered Windows device and links to
this document so operators know what is and is not supported.

This document is a concrete plan for a future, credential-based Windows remote
install path. It is deliberately separate from the existing outbound-only agent
model: the installer initiates the connection from the Sextant server to the
target, and the target must explicitly allow that management channel.

---

## Goals

- Install and enroll a Windows agent on a host when an administrator supplies
  valid administrative credentials for that host.
- Prefer a modern, encrypted, certificate-validated management channel.
- Never persist, log, or return Windows credentials.
- Make every attempt auditable and reversible.
- Bound timeouts, output, concurrency, and retries.
- Keep the agent's normal operation outbound-only after installation.

## Non-goals

- Credential discovery, password spraying, credential guessing, or pass-the-hash.
- SMB service creation, PsExec, WMI remote process creation, or impacket-style
  remote execution.
- Installing on hosts the operator has not explicitly targeted.
- Storing domain or local administrator passwords in SQLite, logs, audit rows,
  browser local storage, or environment files.
- Remote desktop, screen control, or an interactive Windows PTY.

---

## Options

### Option A — WinRM over HTTPS (preferred)

Use PowerShell Remoting / WinRM over HTTPS (`5986`) with certificate validation.

**Why:** WinRM is the native Windows management channel, supports PowerShell
sessions and file transfer, can be constrained with `JEA`/session
configurations, and works with domain or local administrative credentials.
HTTPS avoids cleartext Basic/NTLM negotiation on the wire.

Requirements on the target:

- WinRM enabled and listening on HTTPS (5986) with a certificate whose name
  matches the target DNS name or IP SAN.
- The management certificate chains to a CA the server trusts.
- The supplied account is a local administrator or has the required WinRM
  permissions.
- Firewall allows TCP 5986 from the Sextant server.
- `TrustedHosts` is **not** used as a substitute for certificate validation.

Server-side implementation sketch:

1. Accept a target hostname/IP, optional port, username, a secret credential
   value, and an explicit confirmation flag.
2. Validate the host format rather than accepting a URL or arbitrary command.
3. Use a per-job WinRM client with:
   - HTTPS only by default;
   - certificate and hostname validation;
   - an optional private CA bundle;
   - a bounded connection and operation timeout;
   - no redirects.
4. Upload the signed agent wheel (or a small bootstrap archive) to a temporary
   directory on the target over the authenticated session.
5. Create a temporary install script from a fixed template. The script:
   - verifies the wheel SHA-256;
   - creates `C:\ProgramData\SextantAgent\venv`;
   - installs the wheel with `--no-deps`;
   - allows only the package and `--no-deps` arguments;
   - writes the config with an ACL restricted to SYSTEM and Administrators;
   - registers the `SextantAgent` scheduled task at startup as SYSTEM;
   - starts the task and reports a health result.
6. Enroll with a one-time token generated for this job. The token is held only
   in memory for the duration of the job and is never written to audit/logs.
7. Verify the agent's first heartbeat before marking the job successful.
8. Close the WinRM session and delete temporary files.

Fallback order for transport:

1. HTTPS with a trusted certificate.
2. HTTPS with an operator-supplied private CA bundle.
3. Refuse. Do not silently downgrade to HTTP, NTLM-only HTTP, or Basic auth.

### Option B — OpenSSH for Windows

Use Microsoft's OpenSSH Server (`sshd`) when it is already installed and
managed.

**Why:** it reuses the SSH model and key-based automation Familiar from Linux,
and can be restricted with `AllowUsers`, `ForceCommand`, and `Match` blocks.

Requirements:

- `OpenSSH Server` installed and running on Windows.
- Host key pinning or a trusted `known_hosts` entry. Do not use
  `StrictHostKeyChecking=no` for a production path.
- An administrative account with key-based authentication.
- TCP 22 reachable from the Sextant server.

Implementation would mirror the Linux remote-install job but with PowerShell
commands and Windows-specific paths. It still requires uploading the wheel,
creating the venv, writing the ACL-restricted config, and registering the
scheduled task.

### Which to choose

Prefer **WinRM over HTTPS** for a Windows-first environment with certificate
management already in place. Choose **OpenSSH** when the fleet already uses
centralized SSH keys and host-key pinning for Windows.

Do not implement both in the first release. Ship one transport behind an
explicit setting, with the other as a later adapter.

---

## Credential handling

- Credentials are accepted only in a dedicated install-job request, never in a
  URL, query string, filename, or command line.
- A server-side request handler should read the secret into a mutable buffer,
  use it for the single WinRM/SSH session, and zero/clear references as soon as
  the session ends. Python cannot guarantee memory zeroization, so the goal is
  to minimize lifetime and copies, not to claim perfect erasure.
- Never write the password to SQLite, audit detail, application logs, access
  logs, tracebacks, or the browser after the response.
- Do not include the password in shell command arguments. Use a client library
  or an in-memory credential object.
- Domain credentials must be explicitly labelled (`DOMAIN\user` or UPN) so the
  operator knows which identity is being used.
- Do not cache credentials for retries. A retry requires the operator to supply
  them again, or uses a short-lived credential broker outside Sextant.
- Do not store credentials in Windows Credential Manager on the Sextant server
  for this feature.
- If the implementation ever supports a vault/secret manager, make it an
  explicit integration with its own threat model; do not add a local password
  store.

## Certificate validation

- WinRM: require HTTPS, validate the server certificate against the system trust
  store or an explicit CA bundle, and validate the target hostname/IP SAN.
- OpenSSH: require a pinned host key or a trusted `known_hosts` entry.
- Refuse `--insecure`/skip-validation modes for remote Windows install. A lab
  escape hatch, if added, must be a separate explicit setting, visibly labelled,
  audited, and never the default.
- Certificate pinning or a private CA should be configurable per deployment,
  not per request, to prevent an operator from silently disabling validation.

## Server and target changes

Server:

- A bounded job queue (one or a small number of concurrent Windows installs).
- A new job table or reuse `remote_install_jobs` with `os='windows'` and a
  transport field.
- Fixed, reviewed install scripts shipped with the package.
- SHA-256/size verification of the uploaded wheel.
- Per-job timeout, output cap, and cleanup.
- Tests with a fake WinRM/SSH transport; no real credentials in tests.

Target:

- Temporary directory for the wheel and log.
- Python 3.11+ available. If not present, the plan should fail with a clear
  prerequisite message rather than downloading Python automatically.
- Scheduled task `SextantAgent` running as SYSTEM.
- Config ACL: SYSTEM + Administrators only.
- Firewall rule for outbound HTTPS to the console only. No inbound agent port.

## Audit

Record:

- who requested the install;
- target host/IP and transport;
- whether credential validation succeeded;
- the enrollment token **id**, never its value;
- package/wheel SHA-256 and size;
- job phases and timings;
- final status and bounded error;
- cleanup/rollback result.

Do not record:

- passwords or password hashes;
- bearer/agent tokens;
- full command lines containing secrets;
- uploaded wheel bytes.

## Failure and rollback

Fail closed at each phase. A job is not successful until the agent has completed
at least one authenticated heartbeat.

Rollback guidance:

- If the scheduled task or service was created but the agent never heartbeats,
  disable the task and report the failure.
- If installation partially wrote a venv, remove the versioned install
  directory only after confirming it was created by this job.
- Keep the one-time enrollment token revocable from the Enrollment page.
- Never delete an existing agent installation or config without an explicit
  replace confirmation; re-enrollment should normally rotate credentials on the
  existing machine identity instead.
- Leave a bounded log on the target for operator review, with secrets redacted.

## VLAN, routing, and reachability

Remote install is **server-initiated**, unlike normal agent operation. The
server must be able to reach the target's WinRM/SSH port across VLANs and
firewalls. Discovery can identify a likely Windows host, but it cannot prove
isolation or firewall policy.

- Show the target IP, segment, and transport port before the operator confirms.
- Reject public, loopback, link-local, multicast, unspecified, reserved, and
  documentation ranges for install targets, matching Discovery.
- Require the target to be in an operator-selected allowlisted network.
- Do not attempt to route around network isolation.
- If the agent is reachable only over the outbound model, fall back to the
  manual elevated PowerShell command generated by Discovery.

## Why not SMB, PsExec, WMI process creation, or impacket

These approaches are intentionally excluded:

- **SMB/PsExec** often rely on admin shares and service creation, create
  privileged remote execution paths that are hard to audit, and are heavily
  abused by ransomware and lateral-movement tooling. They also tend to require
  broad SMB exposure.
- **WMI remote process creation** is similarly a generic remote-execution
  primitive and provides little useful structure for a bounded installer.
- **impacket-style** authentication and remote execution libraries make
  pass-the-hash and NTLM relay abuse easier to accidentally enable, expand the
  dependency and credential-handling surface, and are difficult to defend as a
  narrow installer-only mechanism.
- None of these give a better audit or certificate story than WinRM over HTTPS
  or pinned OpenSSH.

Sextant should not become a general-purpose remote Windows execution tool. The
installer is a narrow, reviewed workflow, not a remote shell.

## Suggested rollout

1. **Plan and threat model** (this document).
2. **Manual Windows command** in Discovery (implemented today): generate the
   elevated PowerShell command and document prerequisites. No credentials cross
   the server.
3. **WinRM/OpenSSH transport adapter** behind one explicit setting, with a fake
   transport and isolated tests.
4. **Job UI and audit** on the Remote install page, reusing the existing Linux
   job patterns.
5. **Operational validation** on a Windows test VM: certificate failure,
   wrong password, missing Python, firewall block, successful install, first
   heartbeat, rollback, and token expiry.

## Acceptance criteria for the future implementation

- No credential is persisted or written to any log/audit/response field.
- HTTP or unvalidated TLS is refused for WinRM unless an explicit lab setting is
  enabled and audited.
- The target must be in an allowlisted private network.
- The uploaded wheel is SHA-256/size verified.
- A successful job requires an authenticated agent heartbeat.
- The install token is single-use, expires, is revocable, and is never logged.
- A failed job leaves a clear, bounded error and does not claim success.
- SMB, PsExec, WMI process creation, and impacket are not used.

---

## Current Discovery behavior

The Discovery promote flow for `windows_agent` generates a **manual** elevated
PowerShell enrollment command. It does not contact the Windows host, does not
accept Windows credentials, and does not claim remote install support. The
response includes a link back to this plan and prerequisites; the one-time token
is shown only in that response and stored by Sextant only as a hash.
