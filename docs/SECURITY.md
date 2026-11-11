# Security

Sextant is a privileged remote administration tool. An operator can run arbitrary commands as the endpoint's local service account (root on Linux, SYSTEM on Windows), change services, and power machines off. Treat access to this console as equivalent to administrative access to every enrolled machine.

The design is intentionally explicit: one-time enrollment, per-agent tokens, role-based access, audit logs, outbound-only agents, and local policy switches. It is **not** stealth software.

## Trust boundaries

```text
browser/admin ── HTTPS ──► nginx ── loopback ──► Sextant ── LDAPS ──► AD
                                                   │
                                                   ├── SQLite (secrets hashes, output, audit)
                                                   └── UDP broadcast (WoL)

endpoint agent ── outbound HTTPS ──► nginx
```

- The server trusts a valid session cookie for browser access.
- The server trusts a valid per-agent bearer token for agent endpoints.
- The agent trusts the server certificate (unless explicitly disabled).
- The agent executes what an authenticated operator queues, subject to its local `allow_*` policy.

### Selected threats and controls

| Threat | Control | Residual risk |
| --- | --- | --- |
| Credential stuffing / brute force | Per `IP:username` limiter, bounded in-memory state, audit events, nginx `limit_req` recommended | Limiter is process-local and resets on restart; not shared across workers |
| Session forgery / replay | HMAC-SHA-256 signature, server-side session rows, CSRF token, bounded payload and `iat`/`exp` sanity checks | Secret or database-write compromise defeats these controls |
| Forged client address | `X-Forwarded-For` honored only when `SEXTANT_TRUST_PROXY=1`; the rightmost valid hop is used and the bundled nginx config overwrites the header with `$remote_addr` | Any other proxy must overwrite XFF; otherwise clients can still influence the header |
| Oversized request bodies | `Content-Length` checked and chunked transfer encoding rejected on API mutations | A proxy that rewrites content length must retain the configured cap |
| Weak operator-supplied secret | `SEXTANT_SECRET_KEY` must be at least 32 bytes; auto-generated `secret.key` is 96 hex characters | A leaked secret still permits session forgery |
| Output flood on an endpoint | Agent streams stdout/stderr into bounded buffers and terminates the process group when the cap is reached | A privileged command can still consume CPU until the cap/timeout fires |
| LDAP filter injection | Username escaped per RFC 4515 before interpolation into the filter | A custom `SEXTANT_LDAP_USER_FILTER` must keep `{username}` in a value position |
| LDAP credential disclosure | LDAPS with certificate validation; bind password never logged | Plain LDAP sends the bind password in cleartext |
| Stolen operator session | Short session lifetime, revocation, role checks, audit | No MFA: a stolen password/session can perform privileged actions |
| Destructive remote command | Role checks, agent policy, queue guardrails, audit | No command approval workflow yet |

## Browser authentication

- Local passwords use `scrypt` (N=16384, r=8, p=1) with a 16-byte random salt and 32-byte derived key. Plaintext passwords are never stored.
- Sessions are signed with HMAC-SHA-256 using `secret.key` (or `SEXTANT_SECRET_KEY`). An operator-supplied secret must be at least 32 bytes; the generated `secret.key` is 96 hex characters. The cookie payload contains user ID, session version, session ID, CSRF token, issue time, and expiry. It is signed, not encrypted; it contains no password.
- Cookie flags: `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` when `SEXTANT_COOKIE_SECURE=1`.
- Every request checks a server-side session row for revocation, expiry, user active state, current `session_version`, and that the row's `user_id` matches the cookie's `uid`. The last check is defense in depth if the signing secret is ever weak or leaked.
- Session lifetime defaults to 12 hours and is clamped to 366 days. Decoded sessions require integer `iat`/`exp`, a positive lifetime, an issue time no more than 120 seconds in the future, and a bounded token/payload size. Expired, revoked, or tampered cookies are rejected.
- Password changes, admin password resets, account deactivation, and “revoke all” invalidate the relevant sessions. Revoking all on your own account issues a fresh session for the current browser.
- Admins can list sessions (IP, user agent, last seen, expiry) and revoke one or all sessions.
- Every mutating `/api/` request requires `X-CSRF-Token` matching the signed session, except login and agent endpoints. This blocks cross-site request forgery even if a browser sends the cookie. The middleware keys decisions off the ASGI path, not a Host-header-derived URL, and an optional `SEXTANT_ALLOWED_HOSTS` allowlist rejects unexpected Host headers.
- API request bodies must carry `Content-Length`; chunked transfer encoding is rejected with `411` so the configured body cap cannot be bypassed.
- Login is rate-limited per `IP:username` to 5 failures in 5 minutes, then a 5-minute lock. Limiter state is swept and capped (oldest keys evicted) so spoofed or rotating addresses cannot grow memory without bound. This limiter is in-process, resets on restart, and is not shared between workers; use nginx `limit_req` and a shared limiter for multi-worker deployments.
- Failed logins and rate-limit hits are written to the audit log with username, mode, reason, and source IP. Passwords are never logged. Unknown local usernames still perform a dummy scrypt verification to reduce timing-based account enumeration.
- The login response does not distinguish unknown users from bad passwords.
- API docs (`/docs`, `/openapi.json`) are disabled.
- Responses carry CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, referrer policy, Permissions Policy, COOP/CORP, and `Cache-Control: no-store` on API routes, including early middleware rejections.

### Roles

| Role | Capabilities |
| --- | --- |
| `viewer` | View fleet, telemetry, services, startup, history |
| `operator` | Viewer + wake, shell, power, service control |
| `admin` | Operator + users, enrollment tokens, agent rotation/deletion, audit log |

There is no self-registration. Local users are created by admins or the CLI.

## Directory authentication

Active Directory / Samba AD uses LDAP(S):

1. Service bind with `SEXTANT_LDAP_BIND_DN`.
2. Search for exactly one user matching `SEXTANT_LDAP_USER_FILTER` (default `(sAMAccountName={username})`).
3. Bind again as the user's DN with the supplied password.
4. Map direct `memberOf` group DNs to `admin`, `operator`, or `viewer`.
5. Provision/update the user record with source `ad`.

Important properties and limits:

- Usernames are escaped for the LDAP filter per RFC 4515 (`*`, `(`, `)`, `\`, and NUL), preventing filter injection. Only the username is interpolated; keep `{username}` in a value position if you customize `SEXTANT_LDAP_USER_FILTER`.
- With `SEXTANT_LDAP_USE_SSL=1`, the client always requires a valid server certificate (`ssl.CERT_REQUIRED`). Without `SEXTANT_LDAP_CA_CERT` it validates against the operating system trust store; with `SEXTANT_LDAP_CA_CERT` it uses only that CA bundle.
- Authentication/transport errors expose only the exception type. Logs and error responses never include the supplied password or bind secret.
- The app never falls back to AD for an existing local username with a wrong password. A local account cannot be silently taken over by a directory identity.
- AD users cannot set a local password through the console; they change it in AD.
- Role assignment is re-evaluated on each AD login from direct group membership. Nested groups are not expanded.
- Use **LDAPS** (`SEXTANT_LDAP_USE_SSL=1`, port 636) or a trusted private network. Plain LDAP sends the bind password in cleartext.
- `SEXTANT_LDAP_CA_CERT` can point to a private Samba AD CA bundle for verification.
- The service account only needs permission to search users and read attributes; do not use a Domain Admin account.
- If a local user and an AD user share a username, the local record wins and AD login for that name is rejected.

See [AD_LDAP.md](AD_LDAP.md).

## Agent authentication and enrollment

- Enrolling requires a one-time token (`sextant_enroll_…`, 256 bits) that expires (default 30 minutes) and is stored as SHA-256.
- Enrollment consumes the token in a SQLite transaction, so replay loses.
- On enrollment the agent receives an agent ID and a unique 256-bit bearer token (`sextant_agent_…`).
- Only the token hash is stored. Lookup compares the supplied token hash with `hmac.compare_digest`.
- Agent routes require both the matching agent ID and bearer token.
- Admins can rotate an agent token or delete an agent. The old credential stops working immediately.
- Re-enrolling the same stable machine identity rotates the credential on the existing agent row instead of creating a duplicate. The old token is invalidated.
- Enrollment does not accept arbitrary user-supplied agent IDs or tokens.

Agents make only outbound requests. No listening port is opened on the endpoint.

## Remote command risk

`operator` is effectively remote root/SYSTEM on all managed hosts.

Mitigations:

- Validate all non-shell command arguments and service names server-side; service names cannot start with `-`, preventing `systemctl` option injection.
- Cap shell command length (8,000 chars) and timeout (5–300 seconds).
- Stream stdout/stderr into bounded buffers and terminate the process group once the per-stream cap is reached (`SEXTANT_COMMAND_OUTPUT_LIMIT`, default 200,000 characters).
- Cap queued commands per agent (`SEXTANT_MAX_QUEUED_COMMANDS_PER_AGENT`).
- Set `SEXTANT_OFFLINE_COMMAND_POLICY=reject` if you do not want work queued for offline endpoints.
- Queued commands can be cancelled before dispatch. Failed/cancelled commands can be retried as new audited rows.
- Dispatched commands automatically fail after their timeout plus grace. Queued commands expire after `SEXTANT_QUEUED_COMMAND_TTL_HOURS`.
- Agent kills the entire process tree on timeout.
- Agent-local switches can disable shell, power, or service execution:
  - `allow_shell: false`
  - `allow_power: false`
  - `allow_service: false`
- Server-side agent policy can also restrict shell/power/service, but the effective permission is the intersection: the server can never widen a local denial.
- Queue only to agents you trust; an offline agent will execute queued work when it returns (unless offline policy is `reject`).
- Use a dedicated OS account for the agent when possible. The provided installers use root/SYSTEM because power and service operations require it.
- Every command creation, cancellation, retry, and expiry is audited with actor/agent/kind/command ID and source IP. The full shell text is stored in the command row, not the audit detail.

There is no pixel-level remote desktop or input injection in this version. “Remote control” means shell, service, and power actions.

## Package store, package management, and self-update

- **Store integrity is Tier 0:** TLS + agent bearer auth + server-computed SHA-256 + size. There is no store signature and no CA. The agent verifies that it got exactly the bytes the server stored. A compromised server, database, or admin account can replace both artifact and hash; that is accepted for this version. Tier 1 signing is deferred.
- **Store downloads are authenticated and scoped to the agent route.** Artifacts are immutable, content-addressed blobs; filenames never become paths.
- **Profiles are admin-managed** and audited. They control cadence, modules, and action gates, but cannot widen a local agent denial.
- **Package management is enabled by default**, but remains a deliberate two-key control: the agent-local `allow_packages` flag and the assigned profile's `package_management`/`allow_packages` settings must both be true. Either can disable it. Actions are argv-based (no shell), names/versions are validated, and only `install`/`remove`/`upgrade` are accepted. Every rule change and sync is audited.
- **Package jobs are admin-controlled.** Desired state is admin-only; operators can trigger a sync of already-approved rules. Critical packages should be pinned rather than tracked with `latest`.
- **Agent self-update** downloads the wheel over the authenticated store route, verifies SHA-256 and size, stages a new versioned venv, health-checks it, and switches the `current` symlink. `allow_self_update: false` is a local hard stop. The old release is kept for rollback.
- **Server update checks are notify-only.** The server reads release metadata from `SEXTANT_UPDATE_FEED` (GitHub or a static JSON URL) and shows an admin banner. It does not download or apply a server update in this version.
- **GitHub is not an app source.** App packages are uploaded to the store by an admin or CI; agents never download apps from GitHub.
- **Remote install is admin-only and key-based.** One private key is stored on the server filesystem (`SEXTANT_SSH_KEY_PATH`, mode 0600), not in SQLite. No SSH passwords, no agent forwarding, no forwarding, fixed install script, validated inputs, one job at a time, full audit. Host-key verification is intentionally disabled for rebuilt hosts, so use SSH only on a trusted admin network/VPN.

## Docker inventory (D1)

- **Read-only in D1.** Docker Hosts exposes cached container/image/volume/network
  metadata; it does not start, stop, remove, exec, prune, or deploy anything yet.
- **Docker access is local and opt-in.** The Linux agent's `allow_docker` flag
  defaults to false, and the assigned profile must enable both `modules.docker`
  and `actions.allow_docker`. Either side can deny. Windows agents report
  `unsupported_os` and never appear.
- **Docker socket access is root-equivalent.** The agent reads the socket as its
  service user (root by default). Enabling Docker inventory therefore expands
  the agent's read surface; keep the same VPN/TLS trust boundary as remote shell.
- **No server-side Docker socket.** The server never mounts or connects to a
  Docker socket; it stores inventory uploaded by the authenticated agent.
- **Bounded and non-secret.** Inventory payloads are strict, row-capped, and
  size-capped. Container environment variables are never collected. Uploads are
  scoped to the agent's bearer token and path agent ID.
- **Refresh is non-mutating.** `POST /api/docker/hosts/{id}/refresh` queues only
  `docker` / `inventory.refresh`, obeys offline policy and the command queue, and
  is audited like other command creation.

## Transport and deployment

- Run behind HTTPS. The supplied deployment uses nginx with TLS 1.2/1.3 and HTTP→HTTPS redirect.
- Set `SEXTANT_COOKIE_SECURE=1` and `SEXTANT_TRUST_PROXY=1`, and `SEXTANT_PUBLIC_URL=https://…`.
- Only set `SEXTANT_TRUST_PROXY=1` when nginx (or another trusted proxy) overwrites `X-Forwarded-For` with the real peer address. The bundled nginx config uses `$remote_addr`; the application uses the rightmost valid hop. Otherwise clients can spoof their source IP, distorting rate limiting and audit records. Invalid or unparseable XFF values fall back to the socket peer address.
- Bind the app to loopback (`127.0.0.1:8080`) so it is not directly reachable.
- Restrict the console to a VPN or trusted admin subnet. Do not expose it to the public internet without additional access control.
- Do not use `sextant-agent enroll --insecure` outside a lab.
- Use a private CA or public certificate for agent TLS verification.
- Restrict outbound endpoint traffic to the console host where practical.

## Secret and data storage

| Item | Location | Protection |
| --- | --- | --- |
| Session HMAC secret | `<data>/secret.key` or `SEXTANT_SECRET_KEY` | 0600, owned by service user; `SEXTANT_SECRET_KEY` must be ≥32 bytes |
| SQLite DB | `<data>/sextant.sqlite3` | 0600; directory 0700 |
| Bootstrap admin password | `<data>/bootstrap-admin.txt` | 0600; delete after first login |
| Agent bearer token | Agent config file | 0600 / ACL SYSTEM+Admins |
| Agent token hashes | SQLite | hash only |
| Enrollment tokens | SQLite | hash only; shown once at creation |
| LDAP bind password | `/etc/sextant.env` | 0640 root:service group |
| Telegram bot token | `/etc/sextant.env` | 0640 root:service group; never returned by the API; revoke in BotFather if leaked |
| Command output | SQLite | may contain secrets; protect/backup accordingly |

Notification titles/bodies are sent to the Telegram Bot API and therefore leave the host. Do not enable Telegram for a deployment whose alert content is itself sensitive; use a self-hosted channel (planned) in that case. The server does not encrypt the SQLite database at rest. Use full-disk encryption or an encrypted volume if required.

## Audit log

Audited actions include:

- successful login/logout, failed logins, rate-limit hits, and password changes;
- session revocation and user disable/enable;
- enrollment token creation/deletion and agent enrollment/re-enrollment;
- command creation, cancellation, retry, and automatic expiry;
- WoL packets;
- user creation, role changes, and admin password resets;
- agent token rotation, policy updates, and deletion.

The audit log is stored in SQLite and shown to admins. It is not cryptographically tamper-evident: anyone with database write access can alter it. Export or ship it to a separate log system for stronger retention.

## Known limitations

- No MFA yet; deferred in [ROADMAP.md](ROADMAP.md). Put an authenticating reverse proxy or VPN in front for a second factor.
- The login rate limiter is in-process, bounded in memory, and resets on restart. It is not shared across workers; use a proxy limiter or shared store for multi-worker/HA deployments.
- Source IPs come from `X-Forwarded-For` only when `SEXTANT_TRUST_PROXY=1`; that mode trusts the proxy to overwrite the header.
- SQLite and the command queue are single-node.
- No password reset email, SSO/OIDC, or SCIM.
- No nested AD group resolution.
- No command approval workflow yet; operators execute immediately subject to queue guardrails.
- No automated retention/compaction for command output and audit rows yet.
- Agents must run with elevated privileges for power/service actions.
- WoL magic packets themselves are unauthenticated; only the API action is authenticated. Someone on the same LAN can send their own magic packet regardless.
- PowerShell on Windows runs with the agent's SYSTEM context; `ExecutionPolicy Bypass` is used for agent commands and metrics. Do not widen this to untrusted input.

## Hardening checklist

- [ ] Put the console behind HTTPS and a VPN/trusted admin network.
- [ ] Set `SEXTANT_COOKIE_SECURE=1`, `SEXTANT_TRUST_PROXY=1`, and `SEXTANT_PUBLIC_URL`.
- [ ] Delete `bootstrap-admin.txt` after changing the initial password.
- [ ] Use a password manager and a unique admin password.
- [ ] Use LDAPS with a private CA for AD/Samba.
- [ ] Use a read-only AD service account.
- [ ] Create named operator/admin accounts; reserve `admin` for administration.
- [ ] Run the server as the unprivileged `sextant` user.
- [ ] Keep the data directory 0700 and DB 0600.
- [ ] Keep agent configs 0600 / ACL-restricted.
- [ ] Disable agent shell/power/service locally where not needed.
- [ ] Consider `SEXTANT_OFFLINE_COMMAND_POLICY=reject` and a low queue cap.
- [ ] Review active sessions after role/account changes.
- [ ] Configure nginx login rate limiting.
- [ ] Back up the DB and secrets securely.
- [ ] Review the audit log after changes and incidents.
- [ ] Keep server, agents, Python, and TLS libraries patched.

## Incident response

If you suspect compromise:

1. Isolate the console host and snapshot it for investigation.
2. Stop `sextant` and `nginx` if sessions may be active.
3. Rotate every agent token (or delete/re-enroll agents).
4. Reset all local passwords with the CLI:
   ```bash
   sudo -u sextant SEXTANT_DATA_DIR=/var/lib/sextant \
     /opt/sextant/.venv/bin/sextant reset-password admin
   ```
5. Replace `<data>/secret.key` (or `SEXTANT_SECRET_KEY`) and restart to invalidate all sessions.
6. Revoke the AD service account password / disable the bind account; disable suspicious directory users.
7. Export and preserve the SQLite DB, audit log, nginx logs, and `journalctl` output.
8. Review command history and agent enrollment changes.
9. Rebuild hosts if the agent config or SYSTEM/root context may have been used for persistence.

## Reporting

This is a local project; report security issues to the maintainer of your copy. Do not include live agent tokens, passwords, or command output in reports.
