# Active Directory and Samba AD

Runner Control can authenticate users against Active Directory, Samba AD, or another LDAP v3 directory. Authorization is mapped from direct group membership to `admin`, `operator`, or `viewer`.

Local accounts remain available. This is deliberate: if the directory is unavailable, a break-glass local admin can still sign in.

Install the optional LDAP dependency before enabling directory login:

```bash
.venv/bin/pip install 'ldap3>=2.9,<3'
# or, from a source checkout:
.venv/bin/pip install -e '.[ldap]'
```

The base `requirements.lock` intentionally omits `ldap3` (LGPL-3.0, optional) so agent-only and local-auth deployments do not carry it.

## How login works

1. The app binds to LDAP with the service account (`RC_LDAP_BIND_DN` / `RC_LDAP_BIND_PASSWORD`).
2. It searches under `RC_LDAP_BASE_DN` using `RC_LDAP_USER_FILTER`, default `(sAMAccountName={username})`.
3. Exactly one entry must match.
4. It binds as that user's DN with the supplied password.
5. It maps `memberOf` DNs to a role:
   - any admin group → `admin`
   - else any operator group → `operator`
   - else any viewer group → `viewer`
   - else `RC_LDAP_DEFAULT_ROLE` (default `viewer`)
6. The user is created/updated in SQLite with `source=ad`.

The username in the login box is the value matched by the filter. With the default filter, use the `sAMAccountName` (for example `jdoe`), not `JDOE@corp.example.com`. Change the filter if you want UPN login:

```dotenv
RC_LDAP_USER_FILTER=(userPrincipalName={username})
```

## Samba AD example

Assume:

```text
Realm:      CORP.EXAMPLE.COM
Base DN:    DC=corp,DC=example,DC=com
DC:         dc1.corp.example.com
```

Create groups and a read-only service account with RSAT/`samba-tool`:

```bash
samba-tool group add "RC Admins"
samba-tool group add "RC Operators"
samba-tool group add "RC Viewers"

samba-tool user create svc-runner-control 'use-a-password-manager'
samba-tool user setexpiry svc-runner-control --noexpiry
# The service account only needs to read directory user attributes.
```

Add users to the groups:

```bash
samba-tool group addmembers "RC Admins" alice
samba-tool group addmembers "RC Operators" bob
samba-tool group addmembers "RC Viewers" carol
```

Group matching accepts full DNs. Use `samba-tool group list` and your directory tooling to confirm the DNs, for example:

```text
CN=RC Admins,OU=Groups,DC=corp,DC=example,DC=com
CN=RC Operators,OU=Groups,DC=corp,DC=example,DC=com
CN=RC Viewers,OU=Groups,DC=corp,DC=example,DC=com
```

If you override `RC_LDAP_USER_FILTER`, keep the `{username}` placeholder exactly once.

## Environment

Uncomment and set in `/etc/runner-control.env`:

```dotenv
RC_LDAP_ENABLED=1
RC_LDAP_SERVER=dc1.corp.example.com
RC_LDAP_PORT=636
RC_LDAP_USE_SSL=1
RC_LDAP_CA_CERT=/etc/ssl/certs/corp-root-ca.pem

RC_LDAP_BASE_DN=DC=corp,DC=example,DC=com
RC_LDAP_BIND_DN=CN=svc-runner-control,OU=Service Accounts,DC=corp,DC=example,DC=com
RC_LDAP_BIND_PASSWORD=use-a-password-manager

RC_LDAP_USER_FILTER=(sAMAccountName={username})
RC_LDAP_ADMIN_GROUPS=CN=RC Admins,OU=Groups,DC=corp,DC=example,DC=com
RC_LDAP_OPERATOR_GROUPS=CN=RC Operators,OU=Groups,DC=corp,DC=example,DC=com
RC_LDAP_VIEWER_GROUPS=CN=RC Viewers,OU=Groups,DC=corp,DC=example,DC=com
RC_LDAP_DEFAULT_ROLE=viewer
```

Then restart:

```bash
sudo systemctl restart runner-control
```

The login page shows an **Active Directory / Samba** mode selector when `RC_LDAP_ENABLED=1`.

## LDAPS certificate

`RC_LDAP_CA_CERT` is a PEM bundle containing the issuing CA. This is useful when Samba AD uses an internal CA. Export the root/issuing CA from your PKI and install it with mode 0644, readable by the service group.

When `RC_LDAP_CA_CERT` is set, the client requires a valid server certificate chain (`CERT_REQUIRED`) verified against that bundle. When it is omitted, the client still requires `CERT_REQUIRED` and validates against the host operating system trust store. Set `RC_LDAP_CA_CERT` when Samba AD uses an internal CA that is not installed system-wide.

Never use plain LDAP over an untrusted network. The bind password and user password are cleartext without TLS.

## Test the directory before restarting the app

```bash
ldapsearch -H ldaps://dc1.corp.example.com:636 \
  -D "CN=svc-runner-control,OU=Service Accounts,DC=corp,DC=example,DC=com" \
  -W -b "DC=corp,DC=example,DC=com" \
  "(sAMAccountName=alice)" displayName mail memberOf
```

Expected: exactly one entry with `displayName`, `mail`, and a `memberOf` containing the RC group DN.

Check the user bind manually if your directory tools allow it:

```bash
ldapwhoami -H ldaps://dc1.corp.example.com:636 \
  -D "CN=Alice,OU=People,DC=corp,DC=example,DC=com" -W
```

## Role changes

Roles are re-evaluated on every AD login. To demote an AD operator:

1. remove them from the operator group in AD;
2. they sign out/in (or wait for their next login).

The next login rewrites the SQLite role. An existing signed-in session continues using the role stored in SQLite until re-login; to force immediate refresh, have the user sign out/in or remove/disable the account. If you need immediate blocking, disable the AD account and remove the local record if one exists (the app does not expose AD account deletion in the UI).

## Troubleshooting

| Result | Likely cause |
| --- | --- |
| `503 Directory authentication is unavailable` | LDAPS unreachable, TLS/CA failure, bad service bind DN/password |
| `401 Invalid username or password` | No unique search match, wrong user password, or filter mismatch |
| User logs in as viewer despite group | Group DN typo, group not assigned, nested group, or wrong `memberOf` attribute |
| Local account and AD account share a name | Local account takes precedence; AD login is rejected by design |
| Works with `ldapsearch` but not the app | CA not trusted by the service, filter placeholder changed, duplicate entries |
| Slow login | Directory latency; check `RC_LDAP_TIMEOUT` (default 8s) |

The app logs the exception type, not the password, on LDAP failure:

```bash
sudo journalctl -u runner-control -n 100 --no-pager
```

## Limits

- Direct `memberOf` only; nested groups are not expanded.
- Exactly one search result is required.
- No LDAP referral chasing beyond ldap3 defaults.
- No StartTLS support; use LDAPS (`use_ssl`) for encrypted directory traffic.
- Group DNs must match case-insensitively, but the DN must otherwise be exact.
- AD password policy and lockout apply to the user bind.
