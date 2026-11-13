# Windows remote install — plan

**Status:** implemented with WinRM over HTTPS and an optional `pywinrm`
dependency. The OpenSSH-for-Windows transport remains a future alternative.

Sextant's **Remote install** page now supports a credential-based Windows
install job. The implementation uses WinRM over HTTPS (default port 5986),
validates the target certificate, uploads the signed agent wheel and the
reviewed PowerShell installer, verifies the wheel SHA-256, enrolls with a
one-time token, locks down the config ACL, creates the SYSTEM scheduled task,
starts it, and waits for an authenticated agent heartbeat before marking the job
successful. It is admin-only and audited. Credentials and the enrollment token
exist only in the job request/thread and are redacted from logs/errors/audit.

The Discovery onboarding flow still generates a **manual elevated PowerShell**
enrollment command for a discovered Windows device. That path does not require
the optional dependency and is useful when WinRM is not enabled.

The installer still initiates the connection from the Sextant server to the
target, so the target must explicitly allow that management channel.

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

**WinRM over HTTPS is the implemented transport.** OpenSSH for Windows remains
a future adapter. Choose WinRM when the target can be configured with a trusted
management certificate and TCP 5986 from the Sextant server.

---

## Implemented details

Install the optional extra on the Sextant server:

```bash
/opt/sextant/.venv/bin/pip install 'sextant[windows-remote]'
```

Environment:

| Variable | Default | Purpose |
|---|---|---|
| `SEXTANT_WINDOWS_INSTALL_ENABLED` | `1` | Enable the Windows remote-install job path |
| `SEXTANT_WINDOWS_INSTALL_CA_FILE` | empty | Optional CA bundle when the pywinrm build supports `ca_trust_path`; otherwise install the CA in the server trust store |
| `SEXTANT_WINDOWS_INSTALL_TIMEOUT_SECONDS` | `600` | Job operation timeout (60–3600) |

Target requirements:

- WinRM enabled on HTTPS 5986 with a certificate whose hostname/IP SAN matches
  the target and which chains to a trusted CA.
- Firewall allows TCP 5986 from the Sextant server.
- An administrative local or domain account. Domain users may be supplied as
  `DOMAIN\user` or UPN.
- Python 3.11+ already installed and available as `py -3` or `python`.
- A published `sextant-agent` `agent-wheel` release in the package store.

API request:

```json
{
  "host": "192.168.1.40",
  "port": 5986,
  "user": "CORP\\Administrator",
  "password": "one-time secret",
  "name": "studio-pc",
  "profile": "workstation",
  "watch_services": ["Spooler"],
  "os": "windows",
  "transport": "winrm"
}
```

The password is never written to SQLite, the job row, logs, audit rows, or
subsequent API responses. The one-time enrollment token is stored only as a
SHA-256 hash and is redacted from job logs/errors. A job succeeds only after the
server observes an authenticated agent heartbeat.

Known limitations of this implementation:

- Live WinRM behavior depends on the optional `pywinrm` package and the target's
  WinRM/certificate configuration. It is fixture/fake-transport tested in CI.
- Custom CA files require a pywinrm build exposing `ca_trust_path`; otherwise the
  CA must be in the server trust store. There is no insecure/skip-validation
  switch.
- The password is used once for the job. Retrying after a failure requires
  re-entering it.
- OpenSSH for Windows is not implemented.

## Domain deployment plan (AD-joined Windows fleet)

**Status:** plan, not implemented. The current WinRM installer is usable per-host
with an admin credential. This section describes how to make Windows machine
targeting repeatable across an Active Directory domain.

### When to use this plan

Use the **manual elevated PowerShell command** or the per-host WinRM installer
when you have only a handful of Windows machines, especially 2–3 machines or
one-off rebuilds. At that scale, manual installation is easier and has a smaller
security surface.

Use the domain plan when one or more of these is true:

- more than roughly 10 Windows targets, or recurring rebuilds;
- multiple OUs, VLANs, or sites;
- a central PKI and GPO/Intune management already exist;
- the same installer identity and audit policy must be applied consistently;
- operators should not know or type a domain administrator password per job.

Do not deploy the domain plan just to avoid walking to three PCs. A
short-lived manual command from Discovery is usually the better tradeoff there.

### Target architecture

```text
                    AD CS / Intune PKI
                           │ autoenrollment
                           ▼
  Sextant server ──HTTPS 5986──► Windows target (WinRM HTTPS listener)
       │                              │
       │                              ├─ certificate: Client/Server Auth
       │                              ├─ firewall: Sextant server only
       │                              └─ Python 3.11+ + agent wheel
       │
       └─ dedicated least-privilege identity / Kerberos or NTLM over HTTPS
```

Recommended model:

- Sextant runs on Linux and is **not** a domain member. It trusts the issuing CA
  and reaches target FQDNs over WinRM HTTPS.
- Targets get a WinRM HTTPS certificate automatically from AD CS (or Intune
  SCEP/PKCS) with a DNS SAN matching the computer FQDN.
- A GPO (or Intune configuration profile) enables WinRM HTTPS, restricts the
  firewall to the Sextant server, and deploys Python.
- A dedicated AD group and least-privilege account/gMSA is used for installs.
  Do not use Domain Admin.
- Prefer Kerberos over NTLM. The current implementation uses NTLM through
  pywinrm; Kerberos support is a code enhancement described below.
- No SMB, PsExec, WMI process creation, or impacket path is used.

### Phase 0 — inventory and scope

- List target OUs, hostnames/FQDNs, IP ranges, VLANs, and who owns them.
- Confirm the Sextant server can reach TCP 5986 on every target segment.
- Confirm DNS A/AAAA records and reverse DNS are correct. Prefer FQDNs over IP
  addresses for certificate SANs and WinRM targets.
- Confirm time synchronization (NTP/AD) on the server and targets. Kerberos
  requires clocks within five minutes; NTLM does not, but correct time matters
  for token expiry and audit.
- Decide whether Python will be pre-installed by GPO/Intune/SCCM or baked into
  the base image. The current installer does not download Python.
- Decide the install identity: dedicated AD user, gMSA with Kerberos keytab, or a
  short-lived credential from an external vault.

### Phase 1 — PKI and certificates

- Create or reuse an internal CA and a certificate template for WinRM:
  - Server Authentication EKU (`1.3.6.1.5.5.7.3.1`);
  - subject/SAN containing the target’s DNS name;
  - autoenrollment for the target computer accounts;
  - key usage appropriate for TLS server authentication.
- Publish the root/intermediate CA to the Sextant server trust store, or set
  `SEXTANT_WINDOWS_INSTALL_CA_FILE` if the installed pywinrm build supports
  `ca_trust_path`.
- Do not use a self-signed certificate for a domain rollout unless the
  certificate is explicitly distributed and trusted. Never disable certificate
  validation.
- Decide whether IP SANs are needed for statically addressed hosts. FQDN SANs are
  preferable.

### Phase 2 — WinRM HTTPS and firewall via GPO/Intune

Apply a computer-targeted policy to the Windows install OU/group:

1. Set the WinRM service startup type to Automatic.
2. Enable WinRM and create an HTTPS listener on TCP 5986 using the
   autoenrolled certificate.
3. Disable the HTTP listener if it is not required.
4. Leave `AllowUnencrypted` disabled and Basic authentication disabled unless a
   documented, reviewed exception exists.
5. Restrict inbound TCP 5986 to the Sextant server IP(s) or a dedicated
   management subnet. Do not open 5986 to the whole LAN.
6. Keep the Windows Firewall enabled on all profiles; allow 5986 only on the
   domain/management profile as appropriate.

Typical target-side listener command, for a pilot or a reviewed startup task:

```powershell
$fqdn = "$env:COMPUTERNAME.$env:USERDNSDOMAIN"
$cert = Get-ChildItem Cert:\LocalMachine\My |
  Where-Object {
    $_.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.1' -and
    $_.Subject -match [regex]::Escape($env:COMPUTERNAME)
  } |
  Select-Object -First 1
if (-not $cert) { throw 'No WinRM server certificate found.' }

winrm create winrm/config/Listener?Address=*+Transport=HTTPS `
  "@{Hostname=`"$fqdn`"; CertificateThumbprint=`"$($cert.Thumbprint)`"}"

New-NetFirewallRule `
  -DisplayName 'Sextant WinRM HTTPS' `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 5986 `
  -RemoteAddress '<SEXTANT_SERVER_IP>' `
  -Action Allow
```

For a production GPO, prefer the equivalent Windows Firewall and certificate
autoenrollment settings over embedding one server IP in every machine’s startup
script. Use a group-scoped firewall rule so the allowlist is manageable.

### Phase 3 — identity, RBAC, and least privilege

- Create an AD security group such as `Sextant-Windows-Installers`.
- Create a dedicated install identity, for example
  `svc-sextant-winrm@corp.example.com`. Do not use Domain Admin or a personal
  account.
- Grant that identity only the rights needed to connect to WinRM and install the
  agent on the target OU/group.
- Add the group to local Administrators on the install OU, or, preferably, use a
  JEA endpoint that allows only the reviewed installer command and its fixed
  parameters.
- Restrict WinRM `AllowedGroups` to the Sextant install group.
- Rotate the credential on a schedule and store it in a vault or password
  manager, not in the repository, environment file, or SQLite.
- If using a gMSA:
  - Kerberos is required;
  - the Sextant server needs a keytab and a working Kerberos configuration;
  - the current NTLM-only client path must be extended before this is usable.

Current Sextant limitation: the Windows job UI prompts for a password per job.
For a domain, that is operationally awkward and encourages shared credentials.
The recommended next step is a credential-vault/Kerberos integration rather than
putting a domain password in a config file.

### Phase 4 — Python and agent runtime on targets

- Pre-install Python 3.11+ through Intune, SCCM, GPO startup scripts, or the base
  image. Confirm `py -3 --version` or `python --version` succeeds for the
  SYSTEM/admin context used by WinRM.
- Publish the signed `sextant-agent` wheel in the Sextant package store.
- Keep the agent installation under `C:\ProgramData\SextantAgent` with the config
  ACL restricted to SYSTEM and Administrators, as the installer already does.
- Confirm outbound HTTPS from the target to the Sextant console, including any
  proxy and certificate-trust requirements.
- Keep the scheduled task running as SYSTEM. The agent itself remains
  outbound-only after installation.

### Phase 5 — Sextant server configuration

- Install the optional WinRM client:
  ```bash
  /opt/sextant/.venv/bin/pip install 'sextant[windows-remote]'
  ```
- Set:
  - `SEXTANT_WINDOWS_INSTALL_ENABLED=1`;
  - `SEXTANT_WINDOWS_INSTALL_TIMEOUT_SECONDS` to a sensible bounded value;
  - `SEXTANT_WINDOWS_INSTALL_CA_FILE` if supported, otherwise install the CA in
    the server trust store.
- Ensure the server can resolve target FQDNs and route to 5986 across VLANs.
- Store the install identity’s credential in a vault, not in `sextant.env`.
- Decide whether the Sextant server’s own outbound access should be restricted to
  the management VLAN and package store/CDN endpoints.
- Monitor the Sextant audit log for `remote_install.created`, `.tested`,
  `.failed`, and `.completed`; monitor target WinRM operational logs for failed
  authentication.

### Phase 6 — rollout rings

1. **Lab:** one Windows VM, non-production OU. Verify certificate issuance,
   listener, firewall, Python, install, first heartbeat, rollback.
2. **Pilot:** 3–5 machines in one OU. Verify the dedicated identity, logging,
   token expiry, and failure behavior.
3. **Wave 1:** one department or site.
4. **Broad rollout:** remaining OUs, with a documented pause/rollback point.

Do not start with Domain Admin credentials on every target. Fix permission and
PKI issues in the lab first.

### Phase 7 — validation checklist

- [ ] `Test-NetConnection <fqdn> -Port 5986` succeeds from the Sextant server.
- [ ] The certificate chain validates and the DNS SAN matches the target FQDN.
- [ ] WinRM HTTPS listener is present; HTTP listener is absent or intentionally
      restricted.
- [ ] Firewall allows 5986 only from the Sextant server/subnet.
- [ ] `py -3 --version` or `python --version` returns 3.11+ in the WinRM context.
- [ ] The install identity is not a Domain Admin.
- [ ] The agent wheel is published and its SHA-256 matches.
- [ ] A failed password, missing Python, and blocked firewall each produce a
      clear failure, not a false success.
- [ ] The agent appears online and sends a heartbeat.
- [ ] The job succeeds only after that heartbeat.
- [ ] The audit log contains no password or token.
- [ ] The target’s PowerShell script-block/transcription logging has been
      reviewed for token exposure.
- [ ] Rollback disables the scheduled task and revokes the enrollment token.

### Phase 8 — rollback

- Disable/stop the `SextantAgent` scheduled task.
- Revoke the one-time enrollment token from the Enrollment page.
- Remove the agent config and install directory only after confirming the job
  created them.
- Remove the WinRM HTTPS listener/firewall rule only if the machine is being
  decommissioned from Sextant management.
- Audit the rollback and record the reason.

### Security decisions for a domain rollout

- **Kerberos vs NTLM:** Kerberos is the preferred domain authentication model.
  The current implementation uses NTLM over HTTPS. NTLM over HTTPS is encrypted
  and certificate-validated, but it carries more relay/credential-handling risk.
  Add Kerberos support before a large domain rollout if your security policy
  requires it.
- **JEA:** A constrained JEA endpoint is preferable to full local Administrator
  for the install identity. It should allow only the reviewed installer with
  fixed parameters and no arbitrary command execution.
- **PowerShell logging:** the current installer passes the one-time enrollment
  token in the remote PowerShell script. If Script Block Logging or transcription
  is enabled, the token may be captured on the target. Before broad rollout:
  optionally deliver the token through a short-lived restricted channel, keep
  token TTL very short, revoke it immediately after use, and document the
  residual logging risk. Do not treat this as solved merely because the server
  redacts its own logs.
- **Privilege:** use a dedicated group added to local Administrators only on the
  target OU, or JEA. Never Domain Admin.
- **Network:** 5986 only from Sextant. No broad SMB/PsExec path.
- **Secrets:** vault or Kerberos keytab; never a repository file, environment
  example, or SQLite row.

### Required code enhancements before a domain rollout

These are not implemented yet:

1. Selectable WinRM authentication transport (`ntlm` today; Kerberos next).
2. Optional JEA configuration name / endpoint allowlisting.
3. A credential-vault or Kerberos keytab integration so operators do not type a
   domain password per job.
4. A token-delivery design that avoids script-block/transcription exposure, or a
   documented, reviewed compensating control.
5. Optional bulk/OU-scoped job orchestration with per-host results and bounded
   concurrency.
6. Domain-aware target selection from Discovery (FQDN, OU, and segment).

### Decision matrix

| Situation | Recommended path |
|---|---|
| 1–3 Windows machines, one-off | Manual elevated PowerShell command from Discovery |
| 3–10 machines, occasional rebuilds | Per-host WinRM installer with a dedicated credential |
| 10+ machines, recurring rebuilds | Domain plan with PKI, GPO/Intune, dedicated identity |
| Strong Kerberos-only policy | Implement Kerberos transport first; do not use NTLM broadly |
| No WinRM allowed | Manual install or pre-baked image; do not enable SMB/PsExec |
| Existing Intune/SCCM | Deploy Python and the agent package through those tools; use Sextant for management and audit |

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

1. **Per-host WinRM** (implemented): one Windows job at a time, with fake
   transport/CI coverage and live validation on a test VM.
2. **Manual elevated PowerShell** in Discovery (implemented): useful when WinRM is
   not enabled or only a few machines need installation.
3. **Domain pilot** (plan): PKI, GPO/Intune, dedicated group, one pilot OU.
4. **Domain hardening** (future code): Kerberos transport, JEA endpoint,
   credential-vault integration, and token-logging mitigation.
5. **Broad rollout**: staged OU/site waves with rollback checkpoints.

## Acceptance criteria for the implemented per-host path

- No credential is persisted or written to any log/audit/response field.
- HTTP or unvalidated TLS is refused for WinRM unless an explicit lab setting is
  enabled and audited.
- The target must be in an allowlisted private network.
- The uploaded wheel is SHA-256/size verified.
- A successful job requires an authenticated agent heartbeat.
- The install token is single-use, expires, is revocable, and is never logged by
  Sextant.
- A failed job leaves a clear, bounded error and does not claim success.
- SMB, PsExec, WMI process creation, and impacket are not used.

## Acceptance criteria for a future domain rollout

- Kerberos is available where policy requires it; NTLM over HTTPS is not the only
  option.
- The install identity is dedicated, non-Domain-Admin, and scoped to the target
  OU/group.
- WinRM HTTPS is issued by a trusted PKI and restricted to the Sextant
  server/subnet.
- Script-block/transcription logging cannot expose the enrollment token, or a
  documented compensating control is approved.
- Bulk jobs are bounded, audited per host, and never widen the target allowlist.
- Rollback/revocation is tested and audited.

---

## Current Discovery behavior

The Discovery promote flow for `windows_agent` generates a **manual** elevated
PowerShell enrollment command. It does not contact the Windows host, does not
accept Windows credentials, and does not claim remote install support. The
response includes a link back to this plan and prerequisites; the one-time token
is shown only in that response and stored by Sextant only as a hash.
