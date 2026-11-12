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
- **Server update apply is admin-only, CSRF-protected, and audited.** The server
  reads release metadata from `SEXTANT_UPDATE_FEED` (GitHub or a static JSON URL),
  caches it for 1–168 hours, and shows an admin banner and Security-page panel.
  Apply requires an explicit target version and a wheel asset with a SHA-256 and
  size. The download is bounded, rejects unexpected redirect schemes/hosts, and
  is verified before staging. The staged venv is health-checked before the
  `current` symlink is switched. The unprivileged server writes a restart trigger
  under `/var/lib/sextant`; a root-owned systemd path unit performs the restart.
  The server never logs feed tokens, credentials, or authorization headers, and
  audit entries redact feed query strings. Releases without a hash are shown but
  cannot be applied. There is no automatic server update.
- **Update trust is still Tier 0.** A compromised feed or GitHub release can
  serve different bytes, but an admin must approve each apply. Signatures and
  feed pinning are deferred.
- **GitHub is not an app source.** App packages are uploaded to the store by an admin or CI; agents never download apps from GitHub.
- **Remote install is admin-only and one job at a time.** Linux uses one private key on the server filesystem (`SEXTANT_SSH_KEY_PATH`, mode 0600), not SQLite, with no SSH passwords or agent forwarding, a fixed install script, validated inputs, and full audit. Host-key verification is intentionally disabled for rebuilt hosts, so use SSH only on a trusted admin network/VPN. Windows uses WinRM over HTTPS with the optional `pywinrm` extra, certificate validation, a one-time password held only in the job thread, redacted logs/audit, a fixed PowerShell installer, SHA-256 wheel verification, and an authenticated-heartbeat success gate. There is no insecure TLS switch. Passwords and enrollment tokens are never stored in SQLite or returned by later APIs.

## Network discovery (D1–D2)

Discovery is a network-scanning capability and is therefore **off by default**.

- `SEXTANT_DISCOVERY_ENABLED=1` is necessary but not sufficient. An admin must
  also store the exact acknowledgement **"I am authorized to scan these
  networks"**. The actor, IP, and time are audited; clearing it is audited too.
- Every scan scope must be a subnet of a configured `SEXTANT_DISCOVERY_CIDRS`
  entry. Public, loopback, link-local, multicast, unspecified, reserved,
  documentation, and overly broad ranges are rejected. Individual host scopes
  are rejected unless they are an exact allowlisted CIDR. `SEXTANT_DISCOVERY_EXCLUDE`
  is applied after expansion.
- The per-scan host cap, concurrency, timeout, one-active-scan rule, queue cap,
  and start rate limit are enforced before or during queueing. The UI and API
  can cancel a queued or running scan; cancellation stops further host work.
- The server uses only unprivileged stdlib sockets plus `ip neigh`/`arp -a`.
  There are no raw sockets, packet captures, exploit modules, default-credential
  checks, brute force, or arbitrary host/URL targets. TCP probing is opt-in via
  `SEXTANT_DISCOVERY_ACTIVE_PROBES=1` and limited to the configured port set.
- Scan start/finish/cancel, acknowledgement changes, and device edit/delete are
  audited. Viewer reads are allowed; operator+ is required for scans and device
  metadata; admin is required for the acknowledgement and deletion.
- Discovery data can contain personal data (MAC, hostname, open ports). Scans
  and events are pruned after `SEXTANT_DISCOVERY_RETENTION_DAYS`; devices remain
  until an operator ignores them or an admin deletes them. Export is not
  implemented in D1–D2.
- The scan worker is a bounded daemon thread with a cancellation event and a
  clean lifespan shutdown. It never blocks the FastAPI event loop.
- Agent-relayed discovery (D3) is opt-in and uses the existing outbound
  heartbeat/command path; no inbound port or new listening service is opened.
  Relay requires `SEXTANT_DISCOVERY_AGENT_RELAY=1`, the profile `discovery`
  module, the per-agent policy `allow_discovery`, and the local agent
  `allow_discovery` flag. Local denial is a hard ceiling.
- The server sends only validated scope/excludes/methods/ports/timeout/
  concurrency/host-cap data to the agent. The agent revalidates it, filters every
  candidate address to the supplied CIDR, never scans public/loopback/
  link-local/multicast/unspecified/reserved/documentation ranges, and runs a
  bounded action with a local deadline. Windows reports `unsupported`.
- Agent reports are bearer-authenticated and bound to the scan's `agent_id`.
  The server rejects reports for another agent's scan, out-of-scope or excluded
  device IPs, more than 512 devices, oversized evidence objects, and bodies over
  1 MB. Merging is atomic; report failure/cancellation marks the scan failed or
  cancelled. A late report after cancellation is rejected.
- Discovery CSV/JSON export is admin-only, audited as `discovery.exported`, and
  capped by `SEXTANT_DISCOVERY_EXPORT_LIMIT`. The export inherits the active
  filters and does not add device data that the admin cannot otherwise read.
- Wake verification uses only the neighbour table and opt-in TCP connect checks.
  It never uses ICMP, raw sockets, packet capture, or credentials. Only private
  device addresses are accepted.
- Promotion requires operator+ and CSRF. Wake-target creation reuses a target for
  the same MAC. Existing-agent linking requires matching MACs. Install promotion
  generates a single-use enrollment token, stores only its SHA-256 hash, returns
  the token once in that response, and never puts it in audit detail, discovery
  events, logs, notifications, or subsequent device reads.
- Windows promotion is a manual elevated PowerShell command. The server does not
  accept or store Windows credentials. The planned credential-based remote
  installer is documented separately and is not implemented; see
  [WINDOWS_REMOTE_INSTALL.md](WINDOWS_REMOTE_INSTALL.md).
- Promotion does not email, notify, or log the generated install command. The UI
  clears the command from the promotion dialog when it closes.
- D6 imports are admin-only, audited, size/device-capped, and use bounded XML
  (no DTD/ENTITY), JSON, ARP, and lease parsers. Unsafe/non-private addresses are
  skipped. Imported rows merge through the same conflict rules and emit
  deduplicated events.
- SNMP uses a bounded stdlib v2c GET implementation with private-target,
  timeout, retry, and max-target caps. Community strings are write-only and
  memory-only; they are never stored in SQLite, returned by any API, or logged.
  SNMP v2c is unencrypted on the wire, so use it only on trusted management
  networks and prefer a read-only community.
- Vendor connectors are read-only HTTPS JSON fetchers with TLS verification by
  default, no redirects, short timeouts, and response-size caps. Tokens,
  passwords, and API secrets are memory-only and write-only. UniFi/RouterOS/
  OPNsense/Proxmox live endpoints are marked unverified; ESXi is fixture-only.
  Errors and `last_error` redact known secret values. Inventory pages are
  rendered without HTML injection.
- Integration secrets are lost on restart by design and must be re-entered.
  SMB, PsExec, WMI process creation, and impacket are not used.

## Docker inventory and actions (D1–D5)

- **Docker access is local and opt-in.** The Linux agent's `allow_docker` flag
  defaults to false, and the assigned profile must enable both `modules.docker`
  and `actions.allow_docker`. Exec additionally requires `allow_docker_exec`;
  cleanup requires `allow_docker_prune`. Either side can deny. Windows agents
  report `unsupported_os` and never appear.
- **Docker socket access is root-equivalent.** The agent reads the socket as its
  service user (root by default). Enabling Docker management therefore expands
  the agent's control surface; keep the same VPN/TLS trust boundary as remote
  shell. A privileged container or host bind mount still makes an exec shell
  powerful inside that container.
- **No server-side Docker socket.** The server never mounts or connects to a
  Docker socket; it stores inventory and queues authenticated agent work.
- **No raw Docker proxy.** Browser action models are strict, reject unknown
  fields, and only allow named lifecycle/logs/inspect/exec/remove/cleanup
  actions. The agent repeats validation and executes argv only; it never accepts
  arbitrary Docker flags or a raw host command string. One-shot exec is the
  explicitly gated exception for a shell *inside* the container.
- **One active mutation per host.** Server queue checks and an agent-side lock
  prevent overlapping destructive actions. Cleanup is one scope per request;
  there is no combined prune-everything action and volumes are never included by
  default.
- **Target ownership and re-resolution.** The server requires the target to exist
  in the cached inventory; the agent re-resolves it with `docker inspect` before
  acting. In-use image/volume/network removal is rejected by both layers where
  Docker can determine it.
- **Bounded and non-secret.** Inventory payloads are strict, row-capped, and
  size-capped. Raw inspect masks environment values. Logs/exec/cleanup output is
  bounded. Uploads and commands are scoped to the agent's bearer token/path ID.
- **Audit and events.** Every Docker action request and result is audited with
  actor, host, action, target, status, exit code, and reclaimed bytes where
  available. Completed mutations create `docker.action` agent events. Command
  text, environment values, and file bytes are not included in audit details.
- **Offline behavior.** Actions obey the normal offline-command policy and
  one-command-per-heartbeat queue. Cached inventory remains visible while
  offline, but mutations are rejected or queued according to that policy.
- **Clone is a validated safe subset (D5).** `container.clone` accepts only a
  source container ID, new name, existing image ID, optional argv command,
  restart policy, memory/CPU/PID limits, existing network IDs, and a Compose
  drift acknowledgment. Bind mounts, env values, devices, capabilities,
  privileged mode, and arbitrary flags are rejected by the server model and the
  agent executor. The server re-checks image/network ownership against cached
  inventory; the agent re-resolves image and networks and builds only
  `docker create` plus `docker network connect` argv. Compose-managed sources
  are blocked without `acknowledge_drift`, and the UI warns that a later
  `docker compose up` may revert the clone. Creation is audited and inventory is
  refreshed.
- **Multi-host deploy is per-host isolated (D5).** A request may target at most
  20 hosts. Each host gets an independent deployment/command and its own
  availability, Compose, revision, project-name collision, queue, and mutation
  checks; one host's failure is returned in the per-host result and does not
  cancel the rest. Project names and mutation locks remain per host.
- **Drift and health metadata are non-secret (D5).** Drift compares only the
  Compose `config-hash` label against the recorded deployment baseline. Health
  events store the host, container name/ID, and previous/new health; they do not
  include env values, logs, or terminal output. Repeated identical heartbeats do
  not create rows. Health events are available to the existing events/timeline
  surface but are not yet Telegram alert rows.
- **Live refresh is a bounded read (D5).** Docker Hosts polls the selected host
  every 12 seconds only while visible and pauses for hidden documents, open
  Docker dialogs/console, and active actions. It reuses the cached-inventory
  read route; it does not proxy Docker and does not create a second SSE stream.

## Compose library (D3)

- **Admin-managed files.** Only admins can create/edit/delete Compose stacks.
  Operators can deploy existing revisions; viewers see metadata only. `.env`
  content is never returned to operators/viewers and is never written to audit
  details.
- **Path safety.** The Compose root must be absolute, resolved, non-root, and
  free of symlink components. Files are flat, validated names only; traversal,
  absolute paths, subdirectories, devices, non-regular files, and symlinks are
  rejected. Writes use temp files and atomic replace with `0600` files and
  `0700` directories.
- **Content safety.** V1 accepts image-based Compose only and rejects `build:`
  services. Stack files are UTF-8, size/count/total capped, and hashed
  deterministically. Revisions are immutable once written.
- **Bundle safety.** The agent downloads only its own deployment bundle with
  bearer auth. The server sends bundle/revision hashes and size headers; the
  agent verifies both the archive SHA-256/size and the extracted revision hash,
  and rejects traversal, symlinks, hardlinks, non-regular entries, and oversize
  entries. It extracts into a managed directory and runs Compose there.
- **Deployment isolation.** Project names are validated and unique per host;
  collisions with active or external Compose projects are rejected. One active
  Docker mutation is allowed per host. `down` removes volumes only when explicitly
  requested; failed deploys leave the previous active revision intact.
- **Settings.** When `SEXTANT_DOCKER_COMPOSE_DIR` is set, the path is fixed and
  the API/UI refuse edits. Database-managed migration copies the library to an
  empty target, verifies every revision hash, then switches the setting; a
  non-empty non-Sextant target is refused.
- **Audit.** Stack create/update/delete, deploy requests/results, settings
  migration, and cancellation are audited. File bytes, `.env` values, and
  bundle contents are not audited. Deployment logs are bounded.

## Interactive console (D4)

- **Strictly `docker exec`, never `docker attach`.** The agent resolves the
  container ID again locally, uses only an allowlisted shell (`sh`, `bash`,
  `ash`), and rebuilds argv from validated user/workdir fields. Arbitrary Docker
  flags, `--privileged`, and host shell commands are rejected.
- **Two-key execution gate.** The local agent `allow_docker_exec` flag and the
  profile/per-agent `allow_docker_exec` gate must both allow console access.
  Windows agents are unsupported.
- **Operator-bound sessions.** A console belongs to the operator who opened it.
  Other operators, viewers, revoked sessions, disabled users, logout, and
  revoke-all cannot read or write it. Admin is not a bypass for ownership.
- **In-memory only.** Live stdin/stdout, resize state, and session queues are
  process-local and bounded. Only session metadata and bounded error/exit status
  are stored in `docker_console_sessions`; keystrokes and terminal output are
  never persisted or audited. A server restart marks open sessions failed.
- **Termination controls.** Explicit close, idle timeout, agent shutdown,
  missed agent polls, agent disconnect, operator logout/revocation, and server
  restart all terminate the PTY/process group. Per-agent session caps and
  input/output byte caps limit abuse.
- **Container-root caveat.** The shell runs with the container's privileges. A
  `root`/UID 0 selection is warned; a privileged container or host bind mount
  still makes the shell powerful inside that container. The console is not a
  host shell.
- **Audit metadata only.** Open, close, and failure transitions are audited with
  session/container/shell/user/exit/error metadata. Keystrokes and terminal
  output are not written to audit or command history.

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
