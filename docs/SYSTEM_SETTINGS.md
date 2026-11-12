# System settings

**Status:** implemented.

Sextant reads its bootstrap configuration from the environment and then layers
UI-saved overrides on top. The environment remains the source of truth for
values that were never saved in the UI; a saved value wins until it is changed
again.

Open **Settings** in the sidebar.

## Profile (every signed-in user)

- Display name and email for local accounts.
- Directory (AD/Samba) accounts are read-only here; change those attributes in
  the directory.
- **Change password** opens the Security page. Only local accounts can change a
  password in Sextant.

`PATCH /api/me` updates the local profile. The audit log records the change.

## Server (admin)

| Setting | Effect |
|---|---|
| Public URL | Base URL in install commands, links, and public settings. |
| Allowed hosts | Host header allowlist. Empty means no host middleware. |
| Session length | New session cookie lifetime, in hours. |
| Offline threshold | Seconds without a heartbeat before an agent is offline. |
| Enrollment token lifetime | Default expiry for new enrollment tokens. |
| Require HTTPS cookie | Adds `Secure` to the session cookie and HSTS. |
| Trust proxy headers | Uses `X-Forwarded-For`/`X-Forwarded-Proto` for client IPs. |

Most values apply to the next request. Changes to **Allowed hosts** and
**Require HTTPS cookie** take full effect after a restart because the host
middleware and security headers are installed at startup. The page offers
**Restart server**, which uses the same root-owned systemd handoff as updates.

## Domain & directory (admin)

This is the UI equivalent of the `SEXTANT_LDAP_*` environment variables plus
attribute mapping:

- Domain name (used to derive `DC=...` when Base DN is blank).
- Directory server, port, LDAPS/TLS, CA certificate path.
- Base DN, bind DN, write-only bind password.
- User search filter; it must contain `{username}` exactly once.
- Attribute names for username, display name, email, and groups.
- Admin/operator/viewer group DNs and the default role.

**Test directory** performs a service bind and optional one-user lookup without
saving. Sample attributes are shown so you can confirm the mapping. The optional
`ldap3` dependency must be installed:

```bash
pip install 'sextant[ldap]'
```

The bind password is stored in a separate 0600 file, not in the settings JSON,
and is never returned by the API. Local accounts remain available as a
break-glass path.

Enabling directory login takes effect on the next request. The login page adds
the **Active Directory / Samba** mode selector after a reload.

## SSH (admin)

The SSH section controls the key used by **Remote install** and discovery
onboarding over SSH.

- **Sextant-managed private key** — upload or paste a PEM/OpenSSH private key.
  It is written to the configured path with mode 0600. The API never returns it.
- **Use a local system user's SSH configuration** — choose a Unix user on the
  server. Sextant reads (never copies) that user's `~/.ssh/id_ed25519`,
  `id_rsa`, `id_ecdsa`, or `id_dsa`, and uses `~/.ssh/config` when present. The
  service user must be able to read the key.
- **Key path / config path** overrides are available for either mode.
- **Test key** reports the effective path, whether it is present/readable, and
  the `ssh-keygen` fingerprint.

## Persistence and security

- Saved values live in the `system_settings` row as one JSON document.
- The LDAP bind password lives at `data/secrets/ldap_bind_password` (0600).
- Uploaded SSH private keys are written to the effective key path (0600).
- API routes are admin-only except `PATCH /api/me`.
- Every save, directory test, and restart request is audited.

## API

| Method | Route | Role | Notes |
|---|---|---|---|
| GET | `/api/system/settings` | admin | Effective server, domain, and SSH settings; secrets redacted. |
| PATCH | `/api/system/settings` | admin | Partial `server`, `domain`, and `ssh` objects. |
| POST | `/api/system/ldap/test` | admin | Optional unsaved `domain` object and `username`. |
| POST | `/api/system/ssh/test` | admin | Optional unsaved `ssh` object. |
| POST | `/api/system/restart` | admin | Writes the systemd restart trigger. |
| PATCH | `/api/me` | signed in | Local display name and email. |
