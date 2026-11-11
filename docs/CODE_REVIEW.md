# Code and dependency review

**Date:** 2026-11-09
**Scope:** current tree after the tiered-telemetry, telemetry-history, security-hardening, and contract-test work.
**Method:** read-only review of all production modules, deployment assets, tests, and docs; `pytest`, `pyflakes`, `compileall`, `node --check`, `npm audit`, `pip check`, installed metadata, OSV/PyPI/npm queries, and in-process reproductions where noted.
**Baseline:** 98 tests passing.

**Status legend:** `open` · `in progress` · `fixed` · `deferred` · `accepted risk`

---

## 1. Dependency verdicts

| Dependency | Role | License | Health | Risk | Verdict |
|---|---|---|---|---|---|
| `fastapi` (was `>=0.115,<0.116`) | HTTP API | MIT | Pin was ~47 minor releases behind current | High | **Fixed** — upgraded to 0.162.2 and locked |
| `starlette` (transitive, was 0.46.2) | ASGI toolkit | BSD-3 | 14 OSV advisories; fixes in ≥0.49.1 | High | **Fixed** — upgraded to 1.10.0 |
| `uvicorn` | ASGI server | BSD-3 | Current | Low | Keep; pinned in lockfile |
| `uvicorn[standard]` extras (`uvloop`, `httptools`, `watchfiles`, `websockets`, `PyYAML`, `python-dotenv`) | Server | permissive | Current | Medium | **Fixed** — plain `uvicorn`; unused extras removed |
| `ldap3` 2.9.1 | Optional AD/Samba auth | **LGPL-3.0** | Dormant since 2021 | Medium | **Fixed** — optional `[ldap]` extra, pinned |
| `pyasn1` (transitive) | ldap3 codec | BSD-2 | Current | Low | Keep |
| `pydantic`, `anyio`, `h11`, `click` | Transitive | MIT/BSD | Current | Low | Keep; pinned in lockfile |
| `pytest`, `httpx`, `pyflakes` | Dev | MIT/BSD | Current | Low | Keep; `pip-audit` added |
| `jsdom` 24 (npm dev) | Headless DOM tests | MIT | Current-ish; lockfile with integrity; `npm audit` clean | Low | Keep; dev-only |
| System tools (`systemctl`, `bash`, `shutdown`, `powershell`, `nvidia-smi`, `getmac`) | OS integration | OS | Varies | Medium/High | **Fixed** — fixed service `PATH`; agent installers use `--no-deps` |
| Python ≥3.11 | Runtime | PSF | Current | Low | Keep; pin deployment version |

**Process gaps at review time:** no Python lockfile with hashes, no CI, no Dependabot/Renovate, no `pip-audit`/SBOM. npm had a lockfile and clean audit. All but the SBOM have since been addressed; see §5.

**Licensing:** all permissive except `ldap3` (LGPL-3.0). Acceptable while it remains a separate, unmodified, dynamically imported package with its notice; it complicates a bundled single-file agent.

---

## 2. Findings

### P0

| ID | Finding | Evidence | Status |
|---|---|---|---|
| P0-1 | Reachable Starlette advisories: (a) `request.url.path` is derived from the `Host` header in the old Starlette, so crafted Host values can bypass the CSRF/body-limit middleware; (b) unauthenticated `Range` requests to `FileResponse`/`StaticFiles` cause quadratic CPU work (fixed in Starlette 0.49.1). | Verified in-process with `TestClient`; OSV advisories against 0.46.2 | **fixed** — FastAPI 0.162.2 + Starlette 1.10.0; middleware uses `request.scope["path"]`; optional `TrustedHostMiddleware`; regression tests |

### P1

| ID | Finding | Evidence | Status |
|---|---|---|---|
| P1-1 | Batch dispatch (10 commands) + serial agent execution: the sweeper fails commands that are still queued behind others; the agent executes them anyway, the late result is discarded as idempotent, and retry can duplicate destructive work. | Reproduced with forced stale `dispatched_at` | **fixed** — one command in flight per agent; agent re-polls immediately after completing it |
| P1-2 | `X-Forwarded-For` spoofing: app trusts the first entry; sample nginx appends rather than overwrites. Defeats the login limiter and poisons audit IPs. | Reproduced with rotating XFF | **fixed** — rightmost valid hop; nginx uses `$remote_addr`; regression tests |
| P1-3 | Chunked request bodies bypass `SEXTANT_MAX_BODY_BYTES`; only `Content-Length` is checked. | Reproduced 2.1 MB chunked body accepted past the limit | **fixed** — chunked API mutations rejected with `411` |
| P1-4 | Agent buffers unbounded stdout/stderr before truncating; a noisy command can OOM the endpoint as root/SYSTEM. | Verified by inspection (`executor.py`) | **fixed** — incremental bounded readers; process group killed on cap |
| P1-5 | Session cookie `uid` is not bound to the session row; a valid signed cookie can be re-signed for another user if `SEXTANT_SECRET_KEY` is weak or leaked. No minimum secret strength. | Reproduced viewer→admin with re-signed cookie | **fixed** — `uid`/`user_id` binding; 32-byte minimum secret |
| P1-6 | `SEXTANT_ADMIN_PASSWORD` bypasses the password policy; `SEXTANT_ADMIN_PASSWORD=admin` creates a working admin. | Reproduced | **fixed** — startup validates the bootstrap password and refuses weak values |
| P1-7 | Agent installation is not reproducible and installs server/non-permissive deps as root/SYSTEM even though the agent is stdlib-only. | Installer inspection | **fixed (partial)** — hash-pinned lockfiles, agent installs use `--no-deps`, `ldap3` is an optional `[ldap]` extra; signed wheels remain future work |
| P1-8 | `sextant-agent.service` runs as root with none of the control unit's hardening. | Unit comparison | **fixed** — fixed `PATH`, `NoNewPrivileges`, `UMask`, restricted address families; explicitly not a sandbox |

### P2

| ID | Finding | Evidence | Status |
|---|---|---|---|
| P2-1 | Re-enrollment by `machine_id` + leaked enrollment token can take over an existing agent row. | Plausible | **deferred** — documented; requires a re-enrollment challenge design |
| P2-2 | Last-active-admin invariant is not checked under `BEGIN IMMEDIATE`; concurrent demotions can leave zero active admins. | Plausible | **fixed** — update runs in `BEGIN IMMEDIATE` |
| P2-3 | Late agent result can overwrite a sweeper-terminal state because the result update has no status predicate. | Verified by code | **fixed** — update restricted to `dispatched`/`running`; late results are idempotent |
| P2-4 | `SEXTANT_ENROLLMENT_TTL_MINUTES` is parsed but never used. | Verified | **fixed** — used as the default when the request omits `expires_minutes` |
| P2-5 | Service names may start with `-`, enabling `systemctl` option injection (not shell injection). | Verified | **fixed** — leading `-` rejected by the service-name pattern |
| P2-6 | Login timing can enumerate local usernames (unknown users skip scrypt). | Plausible | **fixed** — dummy scrypt verification for unknown local users |
| P2-7 | Sweeper runs on many read paths and scans all queued/dispatched rows; telemetry prune has no index led by `at`; no command/audit retention. | Verified by code; performance plausible | **fixed** — sweeper limited to dashboard/heartbeat, status indexes added, telemetry `at` index added, command/audit retention added |
| P2-8 | Agent HTTP client follows redirects and forwards the bearer token; response body is read without a size cap. | Verified by stdlib source + code | **fixed** — redirects refused; response capped at 5 MB |
| P2-9 | AD role changes are not applied to existing sessions until re-login. | Verified; documented | **deferred** — documented; periodic group re-check is future work |
| P2-10 | Every new agent defaults `allow_shell`/`allow_power`/`allow_service` to true. | Verified | **accepted risk** — product is a root-capable control plane; installers should make this explicit |
| P2-11 | Security headers are missing on early middleware rejections. | Verified | **fixed** — headers applied to early 400/401/403/411/413 responses |
| P2-12 | `SEXTANT_SESSION_HOURS` has no upper bound; >366 days fails session decoding. | Verified | **fixed** — clamped to 8784 hours (366 days) |

---

## 3. What is genuinely good

- SQL is consistently parameterized; no SQL injection found.
- `BEGIN IMMEDIATE` protects command dispatch and enrollment-token use.
- Agent and enrollment tokens are SHA-256 hashed and compared in constant time.
- Session design: HMAC-SHA-256, bounded decode, `iat`/`exp` sanity, server-side revocation, `session_version`, `HttpOnly`, `SameSite=Strict`, optional `Secure`, CSRF on browser mutations.
- scrypt password hashing with per-password salt.
- Dashboard is XSS-resistant by construction (`textContent`, no `innerHTML`) and has a strict CSP.
- Server unit is genuinely hardened; agent execution is bounded at the server (length/timeout/policy/queue caps).
- The agent is stdlib-only.
- 295 tests (294 passed on Linux, one Windows-only junction test skipped), clean `pyflakes`/`compileall`/`node --check`, clean `npm audit`; 18 Chromium Playwright/axe tests pass.

---

## 4. Test gaps identified

Host-header bypass, chunked bodies, XFF spoofing, sweeper vs in-flight commands, output-limit OOM, session/UID binding, weak secret/admin password, last-admin race, re-enrollment takeover, real Windows/Samba AD, browser CSP, load/migration/fuzz, and the dead enrollment-TTL setting.

---

## 5. Remediation status

Completed:

1. **P0-1** — upgraded to FastAPI 0.162.2 / Starlette 1.10.0; middleware uses `request.scope["path"]`; optional `TrustedHostMiddleware`; regression tests for Host-header CSRF and trusted hosts.
2. **P1 quick wins** — rightmost-hop XFF + nginx `$remote_addr`; chunked API bodies rejected with `411`; session `uid` bound to the session row; 32-byte minimum `SEXTANT_SECRET_KEY`; bootstrap password policy; leading `-` service names rejected; agent redirects refused and responses capped; security headers on early rejections; `SEXTANT_SESSION_HOURS` clamped; `SEXTANT_ENROLLMENT_TTL_MINUTES` used.
3. **P1 medium** — one in-flight command per agent with immediate re-poll; result updates restricted to `dispatched`/`running`; incremental bounded stdout/stderr with process-group kill on cap.
4. **P1 packaging** — hash-pinned `requirements.lock` and `requirements-dev.lock`; agent installers use `--no-deps`; `ldap3` moved to the `[ldap]` extra; agent unit hardened with a fixed `PATH`.
5. **P2** — last-admin update in `BEGIN IMMEDIATE`; status-specific command indexes and a telemetry `at` index; command and audit retention; sweeper limited to dashboard/heartbeat; dummy scrypt verification for unknown users.
6. **Process** — committed CI workflow (`.github/workflows/ci.yml`), Dependabot config, `pip-audit` in the dev extra, and `make lock`/`make audit`.

Deferred / accepted:

- **P2-1** re-enrollment takeover: documented; needs a re-enrollment challenge design.
- **P2-9** AD role drift: documented; periodic group re-check is future work.
- **P2-10** permissive agent defaults: accepted product behavior.
- **SBOM and signed wheels**: not yet implemented.

## 6. Verification

- `pytest`: **294 passed, 1 skipped** on Linux (295 total; the skipped test is the Windows junction integration test, run by the Windows CI job). The suite now includes notifications and Docker D1–D5 coverage; see `docs/TESTING.md` for the per-file map.
- `pip check`: no broken requirements.
- `pip-audit`: no known vulnerabilities.
- `npm audit`: 0 vulnerabilities.
- `pip install --require-hashes -r requirements-dev.lock` + `pip install --no-deps -e .` in a fresh venv: **294 passed, 1 skipped**.
- `pyflakes`, `compileall`, `node --check`, and the jsdom smoke test pass.
