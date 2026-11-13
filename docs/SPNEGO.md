# SPNEGO / Integrated Windows Authentication — plan

**Status:** Proposed. Nothing here is implemented yet. Sextant currently
authenticates directory users with an LDAP password bind (`ldap_auth.py`) and
has no Kerberos, SSO, or header-trust path.

**Goal:** let a domain user sign in to the Sextant console silently with the
Kerberos ticket they already have, on both Windows and Linux, while keeping the
existing password form as the fallback.

**Non-goals:** MFA (that belongs with OIDC), agent authentication, AD domain
management, delegation/impersonation, and NTLM as a fallback mechanism.

Related: [AD_LDAP.md](AD_LDAP.md), [SAMBA_AD.md](SAMBA_AD.md),
[DEPLOYMENT.md](DEPLOYMENT.md), [SECURITY.md](SECURITY.md).

**Read this first:** sections 2–4 are blocking environment work (DNS, TLS,
time, keytab). Section 5 is the code change. Section 10 is the list of
non-obvious traps. Section 12 is the actionable checklist. Do not start
section 5 until sections 2–4 pass their exit criteria.

---

## 1. What already exists

| Piece | Today |
| --- | --- |
| Password directory login | `sextant_control/ldap_auth.py`: service bind, one-user search, user bind, `memberOf` → role |
| Session issuance | `issue_session()` in `server.py`; signed cookie + SQLite session row |
| User provisioning | `Database.upsert_ad_user()` with `source='ad'` |
| CSRF | non-GET `/api/*` requires `X-CSRF-Token`; `/api/auth/login` is exempt |
| Config | frozen `Settings` / `LdapSettings` dataclasses, `SEXTANT_*` env vars |
| Login UI | `static/index.html` + `app.js`; AD mode selector when LDAP is enabled |
| TLS | nginx example in `deploy/nginx.conf`; `SEXTANT_COOKIE_SECURE` defaults to **false** |

SPNEGO replaces only step 2–3 of the login flow: the proof of identity comes
from a Kerberos AP-REQ instead of a password. Authorization still comes from
the same LDAP group lookup, so roles and the existing user table do not change.

---

## 2. Blocking prerequisites

Do these before writing SPNEGO code. Each one is a hard dependency.

| # | Requirement | Why |
| --- | --- | --- |
| 1 | Stable DNS name, e.g. `sextant.corp.example.com` in Samba DNS, resolvable by every client | The SPN and the TLS SAN must equal the name in the browser's address bar |
| 2 | HTTPS on that name with a SAN matching it | Negotiate tokens and session cookies must not travel in clear text; browsers restrict Negotiate on insecure origins |
| 3 | Time sync within 5 minutes of the DC | Kerberos rejects skew (`KRB_AP_ERR_SKEW`) |
| 4 | Outbound DNS (53), Kerberos (88/tcp+udp), LDAPS (636) from Sextant to the DC | Keytab use and role lookup |
| 5 | A dedicated AD service account with an SPN and an exported keytab | Service identity |
| 6 | The existing LDAP service bind working | SSO proves *who*; LDAP still decides *role* |
| 7 | `SEXTANT_COOKIE_SECURE=1` | The session cookie is the real credential after login |

If any of 1–3 is missing, stop. SPNEGO without TLS is a lab experiment, not a
deployment.

---

## 3. Certificates and trust

There are **three independent trust relationships**. They are easy to conflate:

| # | Path | Trust anchor | Configured in |
| --- | --- | --- | --- |
| 1 | Browser → Sextant (HTTPS) | your internal root CA | client OS/browser store |
| 2 | Sextant → DC (LDAPS) | your internal root CA (or Samba's `ca.pem`) | `SEXTANT_LDAP_CA_CERT` |
| 3 | Client → DC (Kerberos) | domain trust, automatic for joined clients | AD join |

"Publish the Samba DC certificate" only solves #2. It does not make browsers
trust Sextant, and it does not give Sextant a certificate.

### 3.1 About Samba's generated certificate

Samba provisions `private/tls/{cert,key,ca}.pem` on first start and uses them
for LDAPS. Two constraints:

- `ca.pem` is a trust anchor for Samba's LDAPS cert. It is **not** an
  enterprise CA service — Samba has no AD CS. Check whether a CA private key
  (`ca.key.pem` or similar) actually exists in `private/tls/`; if it does not,
  you cannot issue new certificates from it.
- The leaf `cert.pem` is for `dc1.corp.example.com`. Do not reuse it for the
  Sextant console: wrong SAN, wrong role.

### 3.2 Recommended: one internal CA, two certificates

Run a small internal CA (step-ca is the least-effort option; OpenSSL or
easy-rsa are fine) and issue:

| Certificate | SAN | EKU | Used by |
| --- | --- | --- | --- |
| `dc1.corp.example.com` | `DNS:dc1.corp.example.com` | serverAuth | Samba LDAPS (`tls certfile`/`tls keyfile`/`tls cafile`) |
| `sextant.corp.example.com` | `DNS:sextant.corp.example.com` | serverAuth | nginx or uvicorn TLS |

Then distribute **one** root:

- **Windows domain clients:** GPO → Computer Configuration → Policies →
  Windows Settings → Security Settings → Public Key Policies → Trusted Root
  Certification Authorities. Alternatively a startup script with
  `certutil -addstore -f Root \\sysvol\...\corp-root.crt`.
- **Linux clients:** `/usr/local/share/ca-certificates/corp-root.crt` +
  `update-ca-certificates`; Firefox `security.enterprise_roots.enabled=true`
  or a `policies.json` certificate install; Chrome/Edge NSS or enterprise
  policy.
- **Non-domain / Home devices:** MDM or manual install. If this population is
  large, a public ACME certificate via DNS-01 is less painful than teaching
  every user to trust a private root (it needs a real domain name and
  outbound ACME, not inbound port 80).

### 3.3 If you genuinely cannot distribute a root

- `mkcert` is acceptable for a developer laptop only.
- HTTP + browser allowlist "works" but exposes the Kerberos token and the
  session cookie to anyone on the path. Do not put real credentials on it.
- NTLM instead of Kerberos avoids the keytab but is deprecated, relay-prone,
  and being disabled by Microsoft. Do not build on it.

---

## 4. AD objects and keytab

Use a dedicated user account, not the DC machine account, and do **not** join
the Sextant host to the domain. A keytab plus `krb5.conf` is sufficient and
avoids a computer object and realm membership.

### Samba AD

```bash
samba-tool user create svc-sextant --random-password \
  --description "Sextant HTTP SPNEGO"
samba-tool spn add HTTP/sextant.corp.example.com svc-sextant
samba-tool domain exportkeytab /tmp/sextant.keytab \
  --principal=HTTP/sextant.corp.example.com@CORP.EXAMPLE.COM

sudo install -d -m 0750 -o root -g sextant /etc/sextant
sudo install -m 0640 -o root -g sextant /tmp/sextant.keytab /etc/sextant/sextant.keytab
rm -f /tmp/sextant.keytab
```

### Windows AD

```powershell
setspn -S HTTP/sextant.corp.example.com svc-sextant
ktpass /out sextant.keytab `
  /princ HTTP/sextant.corp.example.com@CORP.EXAMPLE.COM `
  /mapuser CORP\svc-sextant `
  /crypto AES256-SHA256 /ptype KRB5_NT_PRINCIPAL /pass *
```

`setspn -S` fails if the SPN is already registered; fix duplicates with
`setspn -X` rather than forcing it.

### Kerberos client config

```ini
# /etc/krb5.conf
[libdefaults]
    default_realm = CORP.EXAMPLE.COM
    dns_lookup_realm = false
    dns_lookup_kdc = false
    rdns = false
    default_ccache_name = KEYRING:persistent:%{uid}

[realms]
    CORP.EXAMPLE.COM = {
        kdc = dc1.corp.example.com
        admin_server = dc1.corp.example.com
    }

[domain_realm]
    .corp.example.com = CORP.EXAMPLE.COM
    corp.example.com = CORP.EXAMPLE.COM
```

Verify before touching Sextant:

```bash
klist -k -e /etc/sextant/sextant.keytab      # principal + AES keys + kvno
kvno HTTP/sextant.corp.example.com           # KDC can issue this service ticket
KRB5_TRACE=/dev/stderr kinit alice@CORP.EXAMPLE.COM
curl --negotiate -u : https://sextant.corp.example.com/api/auth/config -v
```

The `curl` should answer `200` with `WWW-Authenticate: Negotiate` visible in
the trace, from a machine that has a TGT.

---

## 5. Server design

### 5.1 New config

Add a frozen `SpnegoSettings` next to `LdapSettings` in
`sextant_control/config.py`:

| Env var | Default | Meaning |
| --- | --- | --- |
| `SEXTANT_SPNEGO_ENABLED` | `0` | Feature gate |
| `SEXTANT_SPNEGO_KEYTAB` | `/etc/sextant/sextant.keytab` | Service keytab |
| `SEXTANT_SPNEGO_SERVICE` | `HTTP/<public_url host>` | Diagnostic/validation principal |
| `SEXTANT_SPNEGO_REALM` | derived | Expected realm |
| `SEXTANT_SPNEGO_ALLOW_NTLM` | `0` | Refuse NTLM by default |
| `SEXTANT_SPNEGO_CHANNEL_BINDING` | `off` | `off` / `try` / `require` |
| `SEXTANT_SPNEGO_MAX_TOKEN_BYTES` | `65536` | Header/token cap |
| `SEXTANT_SPNEGO_TIMEOUT` | `8` | GSSAPI/LDAP budget |
| `SEXTANT_SPNEGO_AUTO` | `0` | Silent attempt on load vs explicit button |

Validation at startup: keytab exists and is readable; `ldap` is configured (or
an explicit `SEXTANT_SPNEGO_DEFAULT_ROLE` is set); warn loudly if
`cookie_secure` is false while `public_url` is `https`; warn if the service
principal host does not match `public_url`.

### 5.2 New module `sextant_control/spnego_auth.py`

```
authenticate_negotiate(token_b64, settings, ldap_settings, tls_cert_der=None)
    -> NegotiateIdentity | None
```

Steps:

1. Strict base64 decode, enforce `MAX_TOKEN_BYTES`.
2. Build accept credentials from the keytab (honor `KRB5_KTNAME`, or pass the
   service name explicitly).
3. `ctx.step(token, channel_bindings=...)`; require `ctx.complete` in one
   round. Kerberos normally returns an AP-REP in the same step; if a client
   forces a multi-round exchange, fail closed rather than keeping in-memory
   GSS context state (uvicorn may be multi-worker).
4. Verify the negotiated mechanism is Kerberos (`1.2.840.113554.1.2.2`), not
   NTLM (`1.3.6.1.4.1.311.2.2.10`), unless `ALLOW_NTLM` is explicitly set.
5. Extract `ctx.initiator_name` → `user@REALM`.
6. Look up the account over the existing LDAP service bind. Search by
   `userPrincipalName` first, fall back to `sAMAccountName` before `@`, and
   require exactly one entry.
7. Reject disabled/locked/expired accounts from directory attributes
   (`userAccountControl` ACCOUNTDISABLE, `lockoutTime`).
8. Reuse `role_for_groups()` for `admin`/`operator`/`viewer`.
9. Return the same shape as `LdapIdentity`, plus the principal for audit.

Add a small refactor in `ldap_auth.py` so the service-bind + search + role
mapping is a shared helper used by both the password and SSO paths.

### 5.3 API

```
GET /api/auth/negotiate?next=/
```

Use **GET**, not POST: the CSRF guard exempts GETs, the browser performs the
Negotiate handshake at top level reliably, and the endpoint can answer with a
redirect.

Behavior:

| Request | Response |
| --- | --- |
| No `Authorization: Negotiate` | `401` + `WWW-Authenticate: Negotiate` (this is the challenge) |
| Valid Kerberos token | issue session cookie, `302` to a validated local `next` |
| Bad/expired token | `401` without a challenge (avoid a loop), redirect to `/?sso=failed` or render a short error |
| NTLM token | `401`, audit `ntlm_rejected` |
| Directory down | `503`, same message as password login |
| Oversize token | `431`/`400`, audit `token_too_large` |

Also add `negotiate_enabled` to `GET /api/auth/config`.

Audit events: `auth.login` with `method=negotiate`, and
`auth.negotiate_failed` with a reason enum. Never log the token, the keytab,
or the raw principal beyond the username.

Rate limiting: reuse `app.state.limiter`, keyed by client IP (the username is
not known until after validation). Keep the existing per-user login limiter
untouched.

### 5.4 User provisioning

`upsert_ad_user()` already preserves `is_active` and refuses to take over a
local account. Reuse it, then apply the same `is_active` check as the password
path. A disabled Sextant user must stay disabled even with a valid ticket.

### 5.5 UI

- `GET /api/auth/config` exposes `negotiate_enabled` and `sso_auto`.
- Add a **Sign in with Windows** button. With `SEXTANT_SPNEGO_AUTO=1`, try it
  once on load, guarded by a `sessionStorage` flag and a short-lived
  `sso_suppressed` cookie so a failure does not loop.
- On success the server redirects to `/`; the SPA loads normally.
- On failure, show the password form with a concrete message ("No Windows
  sign-in ticket found" vs "Directory unavailable").
- **Logout:** clearing the cookie is not enough if the page immediately
  auto-negotiates. Set `sso_suppressed=1` (Max-Age ~60s) on logout, or make
  SSO button-only. Document that "switch user" requires signing out of Windows
  or a private window.

### 5.6 Packaging and deployment

- `pyproject.toml`: optional extra `negotiate = ["gssapi>=1.8,<2"]`; document
  `apt install libkrb5-dev` for source builds. Keep the base lock free of it.
- `deploy/sextant.service`: `Environment=KRB5_KTNAME=/etc/sextant/sextant.keytab`
  and `Environment=KRB5_CONFIG=/etc/krb5.conf`. The unit already runs as
  `sextant` with `ProtectSystem=strict`; a root:sextant 0640 keytab is
  readable.
- `deploy/nginx.conf`: pass `Authorization` (nginx does by default), keep
  `Host`, do not intercept 401s, and raise
  `large_client_header_buffers 4 16k;` because PAC-heavy tickets can exceed
  the 8k default.
- `deploy/sextant.env.example`: document the new variables.
- `sextant sso-check` CLI: check keytab readability/principal, realm, service
  name, `cookie_secure`, and LDAP reachability before restarting the service.

---

## 6. TLS termination and channel binding

This is the least obvious design constraint.

- `tls-server-end-point` channel binding (RFC 5929) ties the Kerberos AP-REQ to
  the TLS server certificate and blocks replay.
- If **nginx terminates TLS**, the Python app only sees plain HTTP from
  `127.0.0.1`. It cannot compute the binding, because it never sees the
  certificate.
- Options:
  1. Terminate TLS in uvicorn (`--ssl-certfile/--ssl-keyfile`); keep nginx
     optional for logging. Channel binding works. **Recommended if you want
     replay protection.**
  2. Terminate in nginx and set `SEXTANT_SPNEGO_CHANNEL_BINDING=off`; rely on
     the localhost proxy hop and keep `cookie_secure=1`. Simpler ops, weaker
     replay story.
  3. nginx `stream` passthrough to uvicorn TLS. End-to-end, but loses HTTP
     rate limiting and path routing.
- TLS 1.3 uses `tls-exporter` (RFC 9266) rather than `tls-server-end-point`;
  check what your Python TLS stack exposes before promising `require`.

---

## 7. Testing

| Layer | What |
| --- | --- |
| Unit | Inject a fake `gssapi` module (mirror the `ldap_module` injection in `authenticate_ldap`): no header, bad base64, oversize, NTLM mech, incomplete context, UPN vs sAM mapping, disabled account, no LDAP entry, role mapping, audit redaction |
| Endpoint | `TestClient`: 401 challenge, session cookie on success, 503 on directory failure, `next` open-redirect rejection, rate limit |
| Integration | Marked skip unless `SEXTANT_TEST_KDC=1`; local `krb5-kdc` or a Samba AD container, create principal+keytab, `kinit`, `curl --negotiate` |
| Manual | Windows Edge/Chrome (zone/allowlist), Linux Firefox/Chrome with SSSD TGT, no-TGT fallback, Home PC fallback, disabled user, wrong SPN, clock skew, logout loop |

The repo already treats "real Windows/Samba AD CI" as future work; SPNEGO makes
that a prerequisite for confidence, not a nice-to-have.

---

## 8. Rollout phases

| Phase | Work | Exit criterion |
| --- | --- | --- |
| 0 | CA, certs, DNS, NTP, `SEXTANT_COOKIE_SECURE=1` | Browser shows a trusted `https://sextant.corp.example.com` |
| 1 | Service account, SPN, keytab, `krb5.conf` | `kvno` and `curl --negotiate` succeed |
| 2 | Config + `spnego_auth.py` + `sso-check` CLI | CLI validates the keytab and prints the principal |
| 3 | `/api/auth/negotiate` + provisioning + audit + tests | API tests pass; a real client gets a session |
| 4 | UI button, optional auto, logout suppression | No redirect loops; password fallback works |
| 5 | Hardening: reject NTLM, channel binding, caps, rate limit | Security review checklist signed off |
| 6 | Docs + GPO/browser policy + integration CI | A new admin can deploy from docs alone |

---

## 9. Runbook

### Verify

```bash
sudo -u sextant KRB5_KTNAME=/etc/sextant/sextant.keytab klist -k -e
kvno HTTP/sextant.corp.example.com
curl --negotiate -u : -v https://sextant.corp.example.com/api/auth/negotiate
```

### Rotate the keytab without downtime

1. Add the new key to the account (AD keeps the old kvno valid).
2. Re-export the keytab to a temp path, verify with `klist -k -e`.
3. Install over `/etc/sextant/sextant.keytab` (atomic rename, same owner/mode).
4. `systemctl restart sextant`.
5. Remove the old key from the account after the new one is confirmed.

Changing the service-account password invalidates the old keytab. Re-export
immediately, or use `ktpass`/`samba-tool` in the same change window.

### Troubleshoot

| Symptom | Likely cause |
| --- | --- |
| `Server not found in Kerberos database` | Missing SPN, wrong realm, or DNS/`krb5.conf` |
| `Clock skew too great` | NTP; DC and Sextant differ by >5 min |
| `KRB_AP_ERR_MODIFIED` | Wrong keytab, duplicate SPN, or RC4/AES mismatch |
| `No credentials cache found` (client) | No TGT: not domain-joined, local account, or expired ticket |
| Browser prompts for a password | URL not in the browser allowlist/zone |
| Browser does nothing | No `WWW-Authenticate: Negotiate` on the 401, or a proxy intercepted it |
| `431` / `400` from nginx | Ticket too large; raise header buffers |
| Login works then bounces to the form | Cookie not `Secure`, `SameSite`/path mismatch, or `public_url` host mismatch |
| SSO works, role is viewer | Nested group, wrong `memberOf` DN, or service account cannot read groups |

---

## 10. Things that commonly surprise people

1. **Kerberos does not need TLS; your deployment does.** The AP-REQ is
   replayable within the ticket lifetime, and the session cookie is a bearer
   token. TLS is for those, not for the KDC exchange.
2. **The SPN must match the browser's hostname exactly.** `HTTP/sextant...`
   covers `https://sextant...`; it does not cover `https://sextant/`,
   `https://10.0.0.10/`, or a CNAME unless you add SPNs or redirect to the
   canonical name. Pick one FQDN and enforce it.
3. **Samba's `ca.pem` is not an enterprise CA.** Publishing it helps the
   Sextant→DC LDAPS trust; it does not make browsers trust Sextant. Run a real
   internal CA and issue both certs from it.
4. **No domain join is required on the server.** A keytab + `krb5.conf` is
   enough. Joining adds a computer object and realm membership for little
   gain.
5. **The service account, not the machine, owns the SPN.** Duplicate SPNs
   produce `KRB_AP_ERR_MODIFIED`; `setspn -X` / `samba-tool spn list` first.
6. **Nested groups are not expanded.** `memberOf` is direct-only in
   `ldap_auth.py`. A user in `RC Admins` via a nested group gets the default
   role. Decide whether to expand recursively (bounded) or document it.
7. **A disabled account keeps working until its TGT expires.** Disabling an
   AD user does not revoke issued tickets. Check directory status on every
   SSO login, and remember an already-issued Sextant session survives until
   expiry.
8. **Logout is a UX problem.** Silent SSO logs the user straight back in.
   Suppress SSO after logout or make the button explicit.
9. **Home and non-domain machines have no TGT.** Password login is not
   optional; Windows Home can never do silent SSO.
10. **Linux SSO needs a TGT too.** `realmd`/`sssd` join plus a browser
    negotiate allowlist, or `kinit`; otherwise Linux users get the password
    form.
11. **Browser policy is mandatory.** Edge/Chrome `AuthServerAllowlist` or the
    Local Intranet zone; Firefox `network.negotiate-auth.trusted-uris`. Without
    it there is no silent auth, and sometimes no prompt at all.
12. **Reject NTLM.** If you let GSSAPI negotiate NTLM, you reintroduce relay
    risk and build on a protocol Microsoft is disabling.
13. **A TLS-terminating proxy breaks channel binding.** See section 6.
14. **The reverse proxy must pass 401 and `Authorization` through.** Header
    size matters for PAC-heavy tickets.
15. **Role and status are re-evaluated at login only.** A demoted or disabled
    user with a live session stays until session expiry; decide whether that
    is acceptable or add periodic revalidation.
16. **Multi-worker uvicorn and multi-step GSSAPI.** Keep the accept to one
    round or add shared state; the current rate limiter is already in-process,
    so assume one worker unless you change that.

---

## 11. Decisions needed

1. Is the DC Samba AD, Windows AD, or both? The keytab commands differ.
2. Can you run an internal CA (step-ca/OpenSSL) and push its root by GPO, or
   do you need a public ACME certificate?
3. What is the canonical console FQDN, and is it already in DNS?
4. Silent auto-SSO on page load, or an explicit "Sign in with Windows" button?
5. Are Linux clients domain-joined with SSSD, or is Linux SSO a later phase?
6. Is MFA on the near-term roadmap? If yes, plan OIDC now and treat SPNEGO as
   an optional fast path, not the final identity architecture.

Record the answers here when known:

| Decision | Answer |
| --- | --- |
| DC type | |
| CA strategy | |
| Canonical FQDN | |
| SSO trigger (`auto` vs button) | |
| Linux clients joined | |
| MFA/OIDC timeline | |

---

## 12. Implementation checklist

### Phase 0 — environment (blocking)

- [ ] Choose canonical FQDN: `sextant.________________`
- [ ] Add DNS A record in Samba DNS (`samba-tool dns add ...`)
- [ ] Stand up internal CA (step-ca / OpenSSL / easy-rsa)
- [ ] Issue DC LDAPS cert with SAN + serverAuth; configure Samba `tls certfile/keyfile/cafile`
- [ ] Issue Sextant HTTPS cert with SAN + serverAuth
- [ ] Distribute root CA: Windows GPO Trusted Root, Linux `update-ca-certificates`, browser policy
- [ ] Verify NTP: `chronyc tracking`; DC and Sextant within 5 minutes
- [ ] Open outbound 53, 88/tcp+udp, 636 from Sextant to DC
- [ ] Set `SEXTANT_PUBLIC_URL=https://sextant.corp.example.com`
- [ ] Set `SEXTANT_COOKIE_SECURE=1`

### Phase 1 — service identity

- [ ] Create `svc-sextant` with a long random password
- [ ] Register SPN `HTTP/sextant.corp.example.com`
- [ ] Export keytab; install as `/etc/sextant/sextant.keytab` root:sextant 0640
- [ ] Write `/etc/krb5.conf`
- [ ] `klist -k -e` shows the principal, AES keys, and kvno
- [ ] `kvno HTTP/sextant.corp.example.com` succeeds
- [ ] `curl --negotiate -u :` against the console returns `WWW-Authenticate: Negotiate`

### Phase 2 — server code

- [ ] `sextant_control/config.py`: add `SpnegoSettings`, `Settings.spnego`, `_spnego_from_env()`, startup validation
- [ ] `sextant_control/ldap_auth.py`: extract shared service-bind/search/role helpers; add `lookup_identity(principal)`
- [ ] `sextant_control/spnego_auth.py`: GSSAPI accept, Kerberos-only check, principal → LDAP identity, disabled-account check
- [ ] `sextant_control/server.py`: add `GET /api/auth/negotiate`, `negotiate_enabled` in `/api/auth/config`, audit events, limiter
- [ ] `sextant_control/cli.py`: add `sextant sso-check`
- [ ] `pyproject.toml`: optional extra `negotiate = ["gssapi>=1.8,<2"]`

### Phase 3 — UI and lifecycle

- [ ] `static/index.html`: "Sign in with Windows" button / status message
- [ ] `static/app.js`: optional auto attempt with `sessionStorage` + `sso_suppressed` guard; failure falls back to the password form
- [ ] Logout sets `sso_suppressed` so it does not immediately re-authenticate
- [ ] `next` parameter restricted to local paths

### Phase 4 — deployment and tests

- [ ] `deploy/sextant.service`: `KRB5_KTNAME` and `KRB5_CONFIG`
- [ ] `deploy/nginx.conf`: `large_client_header_buffers 4 16k;`, pass `Authorization`, do not intercept 401
- [ ] `deploy/sextant.env.example`: document `SEXTANT_SPNEGO_*`
- [ ] `tests/test_spnego.py`: unit coverage with fake `gssapi`
- [ ] Endpoint tests: challenge, success, 503, open-redirect, rate limit
- [ ] `tests/integration/`: KDC-backed test behind `SEXTANT_TEST_KDC=1`
- [ ] Manual matrix: Windows Edge/Chrome, Linux Firefox/Chrome, no TGT, Home PC, disabled user, wrong SPN, clock skew, logout loop
- [ ] `docs/AD_LDAP.md` links here; `CHANGELOG.md` entry

### Definition of done

- [ ] A domain user on a domain-joined Windows PC reaches the console with no
  password prompt, and the resulting role matches their LDAP groups.
- [ ] The same works from a domain-joined Linux client with an SSSD TGT.
- [ ] A non-domain or Home PC still signs in with the password form.
- [ ] No SSO redirect loop after logout.
- [ ] NTLM tokens are rejected and audited.
- [ ] The keytab is not world-readable and is never logged.
- [ ] `sextant sso-check` fails clearly for each prerequisite.

---

## 13. Configuration example

`/etc/sextant.env` additions:

```dotenv
# Console identity
SEXTANT_PUBLIC_URL=https://sextant.corp.example.com
SEXTANT_COOKIE_SECURE=1

# Existing directory auth (role lookup still uses this)
SEXTANT_LDAP_ENABLED=1
SEXTANT_LDAP_SERVER=dc1.corp.example.com
SEXTANT_LDAP_PORT=636
SEXTANT_LDAP_USE_SSL=1
SEXTANT_LDAP_CA_CERT=/etc/sextant/corp-root-ca.pem
SEXTANT_LDAP_BASE_DN=DC=corp,DC=example,DC=com
SEXTANT_LDAP_BIND_DN=CN=svc-sextant,OU=Service Accounts,DC=corp,DC=example,DC=com
SEXTANT_LDAP_BIND_PASSWORD=use-a-password-manager
SEXTANT_LDAP_USER_FILTER=(sAMAccountName={username})
SEXTANT_LDAP_ADMIN_GROUPS=CN=RC Admins,OU=Groups,DC=corp,DC=example,DC=com
SEXTANT_LDAP_OPERATOR_GROUPS=CN=RC Operators,OU=Groups,DC=corp,DC=example,DC=com
SEXTANT_LDAP_VIEWER_GROUPS=CN=RC Viewers,OU=Groups,DC=corp,DC=example,DC=com

# SPNEGO
SEXTANT_SPNEGO_ENABLED=1
SEXTANT_SPNEGO_KEYTAB=/etc/sextant/sextant.keytab
SEXTANT_SPNEGO_SERVICE=HTTP/sextant.corp.example.com
SEXTANT_SPNEGO_REALM=CORP.EXAMPLE.COM
SEXTANT_SPNEGO_ALLOW_NTLM=0
SEXTANT_SPNEGO_CHANNEL_BINDING=try
SEXTANT_SPNEGO_MAX_TOKEN_BYTES=65536
SEXTANT_SPNEGO_TIMEOUT=8
SEXTANT_SPNEGO_AUTO=0
```

`/etc/systemd/system/sextant.service` additions:

```ini
Environment=KRB5_KTNAME=/etc/sextant/sextant.keytab
Environment=KRB5_CONFIG=/etc/krb5.conf
```

`deploy/nginx.conf` additions inside the TLS server block:

```nginx
large_client_header_buffers 4 16k;
client_header_buffer_size 8k;

location = /api/auth/negotiate {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    # Do not add auth_basic here; it would fight the Negotiate challenge.
}
```

---

## 14. Code anchors

| File | Role in this plan |
| --- | --- |
| `sextant_control/config.py` | `LdapSettings` pattern to copy for `SpnegoSettings`; `Settings.load()` / `from_env`; `allowed_hosts` derivation |
| `sextant_control/ldap_auth.py` | `escape_filter_chars`, `role_for_groups`, service/user bind flow, injectable `ldap_module` |
| `sextant_control/server.py` | `issue_session`, `_session_cookie`, `current_user` / guard middleware, `/api/auth/login`, `/api/auth/config`, audit calls, rate limiter |
| `sextant_control/db.py` | `upsert_ad_user`, `get_user_by_name`, session tables |
| `sextant_control/static/app.js` | `showLogin`, `enterApp`, `/api/auth/config` bootstrap, logout handler |
| `sextant_control/static/index.html` | login form and mode selector |
| `deploy/sextant.service` | systemd env, `ProtectSystem`, keytab readability |
| `deploy/nginx.conf` | TLS termination, header size, 401 passthrough |
| `deploy/sextant.env.example` | operator-facing env documentation |
| `docs/AD_LDAP.md` | current LDAP behavior and role mapping |
| `docs/SAMBA_AD.md` | LDAPS cert setup, Samba has no AD CS |
| `docs/SECURITY.md` | known limitations (no SSO today, no MFA) |

---

## 15. Summary for future me

You have two separate jobs: (1) give Sextant a trusted HTTPS name and a
Kerberos keytab, and (2) add a `negotiate` login path that reuses the existing
LDAP role lookup. The first is environment work and is the long pole. The
second is a small, isolated change if `ldap_auth.py` is refactored to share its
search/role logic.

The two decisions that shape everything else are **where TLS terminates**
(channel binding vs proxy simplicity) and **whether SSO is silent or a button**
(logout UX). Samba's `ca.pem` does not solve browser trust; run an internal CA
and publish its root, or use a public ACME certificate. Do not ship SPNEGO over
plain HTTP, and keep the password form for Home and non-domain machines.
