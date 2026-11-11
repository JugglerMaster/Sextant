# Server package store — plan

**Status:** Tier 0 S1 implemented (backend + CLI); S2 partial (per-device store installs are not wired yet). No signatures.
**Scope:** a self-hosted artifact and application store on the Sextant server for packages that are **not** in apt, dnf, the Windows Store, winget, or Chocolatey — and for hosting **agent self-update** artifacts without publishing to PyPI.

Related: [Package management](PACKAGE_MANAGEMENT.md), [Self-update](SELF_UPDATE.md), [Architecture](ARCHITECTURE.md), [Security](SECURITY.md).

---

## 1. Why a store

Native repositories cover most OS software, but gaps remain:

- internal tools and vendor installers not published to a distro or winget;
- a specific `.deb`/`.rpm`/`.msi` version that must be pinned;
- software for air-gapped or restricted networks;
- the agent itself, which the project does not plan to publish to PyPI.

The store fills those gaps while keeping the pull model: agents download over their existing authenticated HTTPS channel, verify SHA-256 and size, and install locally. The server never connects inbound to an endpoint. Tier 0 integrity is the v1 decision; signatures are deferred and the fields are reserved (see §7).

GitHub remains the source/build/CI home. The store is the **distribution point for this fleet**, not a public package index.

---

## 2. Goals and non-goals

### Goals

- Publish a versioned artifact once and assign it to devices (later groups).
- Content-address artifacts by SHA-256; deduplicate identical uploads.
- Sign manifests offline; verify at upload and again on the agent.
- Serve only to authenticated, assigned agents.
- Install with native installers where possible; support a declarative recipe for the rest.
- Track installed state and audit every publish, download, install, upgrade, and removal.
- Host agent wheels for self-update.
- Keep storage bounded (quotas, retention, garbage collection).
- Single-node operation with SQLite and the local filesystem; no external service required.

### Non-goals (v1)

- A general-purpose public package registry (no multi-tenant, no anonymous downloads).
- Mirroring entire distro repositories.
- Replacing apt/dnf/winget/Chocolatey for packages they already provide.
- Container image hosting (an OCI registry is a separate concern).
- Arbitrary install scripts from untrusted sources.
- Automatic import of app packages from GitHub releases (GitHub is only the server-release feed; apps are uploaded to the store directly).

---

## 3. What the store holds

| Artifact type | Examples | Install path |
|---|---|---|
| `agent-wheel` | `sextant-0.3.0-py3-none-any.whl` | Self-update staging/switch |
| `deb` | internal agent, VPN client, printer driver | `apt-get install ./file.deb` (resolves deps) |
| `rpm` | internal tooling | `dnf install ./file.rpm` |
| `msi` | vendor agent, internal app | `msiexec /i … /qn /norestart` |
| `msix` / `appx` | packaged Windows app | `Add-AppxPackage` (per-user limits apply) |
| `zip` / `tar.gz` | portable tool with a recipe | Declarative recipe |
| `exe` | vendor installer | Declared silent flags + recipe |

The store also keeps **metadata**: name, slug, vendor, homepage, description, category, icon, supported platforms/architectures, changelog, and a SHA-256 checksum (signature fields reserved for a future Tier 1).

It does **not** store secrets (agent tokens, private keys), user data, or OS images.

---

## 4. Architecture

```text
 build host / GitHub Actions                Sextant server                 agent
 ─────────────────────────                  ───────────────────────                ─────
 build artifact                             store_packages / store_releases       heartbeat: assignments
 write manifest                             blobs/sha256/ab/<hash>                desired state
 compute SHA-256             ── upload ──►  verified metadata + blob        ◄──────  download (auth)
                                            assignments (device/tag/group)          verify SHA-256 + size
                                            audit + quota + GC                      install via native/recipe
                                                                                    report state
```

- **Blobs** are immutable and content-addressed.
- **Metadata** lives in SQLite; the blob path is derivable from the hash.
- **Assignments** reuse the package desired-state model (`manager=store`, `package=<slug>`), so the same reconcile engine handles apt, winget, and store packages.
- **Downloads** are authenticated with the agent bearer token and authorized against assignments.

---

## 5. Data model

### `store_packages`

| Column | Notes |
|---|---|
| `id`, `slug` | slug is the stable package ID used in rules |
| `name`, `vendor`, `homepage`, `description`, `category` | catalog metadata |
| `icon` | small image stored as a blob or data URI |
| `kind` | `app`, `driver`, `agent` |
| `platforms` | `linux`, `windows`, `any` |
| `architectures` | `amd64`, `arm64`, `all` |
| `created_by`, `created_at`, `updated_at`, `archived_at` | lifecycle |

### `store_releases`

| Column | Notes |
|---|---|
| `id`, `package_id`, `version` | unique per package |
| `artifact_type` | `agent-wheel`, `deb`, `rpm`, `msi`, `msix`, `zip`, `exe` |
| `filename`, `size`, `sha256` | blob identity |
| `manifest`, `signature`, `key_id` | manifest JSON; signature/key_id reserved for Tier 1 (null in v1) |
| `min_os`, `arch` | compatibility filters |
| `install_profile` | native installer type + silent flags, or recipe reference |
| `changelog`, `published_at`, `published_by` | audit |
| `yanked_at`, `yanked_reason` | stop offering |

### `store_events`

`at`, `package_id`, `release_id`, `agent_id`, `action` (`published`, `yanked`, `downloaded`, `verified`, `installed`, `upgraded`, `removed`, `failed`), `detail`, `actor`.

Package assignment and installed-state tables are shared with the package-management plan (`package_rules`, `package_inventory`, `package_jobs`).

---

## 6. Storage layout

```text
$SEXTANT_DATA_DIR/store/
  blobs/
    sha256/
      ab/
        abcdef…            # exact bytes, mode 0600, immutable
  tmp/                     # upload/staging, cleaned on startup
```

- **Content addressed:** the same artifact uploaded twice is stored once.
- **Atomic writes:** upload to `tmp/`, fsync, verify hash, then `rename()` into `blobs/`.
- **No user-controlled paths:** the filename is metadata; the blob path comes only from the hash.
- **Permissions:** `0700` directory, `0600` files, owned by the service user.
- **Quotas:** `SEXTANT_STORE_MAX_BLOB_MB` (default 512), `SEXTANT_STORE_MAX_TOTAL_GB` (default 20), per-package release count.
- **GC:** unreferenced blobs older than `SEXTANT_STORE_GC_DAYS` (default 30) are deleted; assigned/in-use releases are never removed. A release must be yanked and unreferenced before its blob is collected.
- **Backup:** SQLite + `store/blobs/` must be backed up together; blobs are immutable so `rsync --ignore-existing` works after the DB snapshot.

Later option: an S3-compatible backend for larger installations. Keep the local filesystem for v1.

---

## 7. Manifests and integrity (Tier 0)

**Decision for v1: Tier 0 only.** Integrity is TLS + agent bearer auth + SHA-256 + size. There is no store signature, no signing key, and no CA. Signature fields are reserved in the schema so Tier 1 can be added later without a protocol change.

Each release has a canonical manifest:

```json
{
  "schema": 1,
  "slug": "acme-vpn",
  "name": "Acme VPN Client",
  "version": "4.2.1",
  "artifact_type": "deb",
  "filename": "acme-vpn_4.2.1_amd64.deb",
  "sha256": "…",
  "size": 12345678,
  "platforms": ["linux"],
  "architectures": ["amd64"],
  "min_os": "ubuntu-22.04",
  "install_profile": {"type": "deb"},
  "released_at": "2026-11-09T00:00:00Z",
  "signature": null,
  "key_id": null
}
```

Rules:

- The **server computes** the SHA-256 and size from the uploaded bytes; a client-supplied hash is ignored.
- Upload the artifact + manifest (signature fields empty in v1).
- The server stores the blob, inserts metadata, and audits `published`.
- The agent downloads and re-computes SHA-256 + size; a mismatch aborts installation.
- OS-native signatures still apply where the package format has them (GPG for `.deb`/`.rpm`, Authenticode for MSI/EXE); they are verified by the native installer, not by the store.
- `signature`, `key_id`, and a verifier hook exist in the schema so Tier 1 can be enabled later.

### What Tier 0 does and does not protect

| Property | Tier 0 |
|---|---|
| Corruption, truncation, partial upload | Yes (SHA-256 + size) |
| “Byte-for-byte what the server hosts” | Yes |
| Tampering in storage/backup | Yes, as long as the stored hash is not also altered |
| Compromised server, DB, or admin account | **No** — the attacker can replace artifact and hash together |
| Compromised GitHub release (server update) | **No** — trust is GitHub over TLS plus admin review |

Accepted risk for v1: the server is the trust root. Mitigations: admin-only publishing, least-privilege server access, database/file permissions, backups, audit, admin review of server updates, and rollback to a known-good release.

### Tier 1 (deferred)

Tier 1 is an **offline detached signature** over the manifest:

- Generate an Ed25519/minisign keypair. Keep the private key offline; embed the public key in the agent.
- Sign the manifest bytes. Upload artifact + manifest + signature.
- The agent verifies the signature, then the SHA-256.
- A compromised server cannot produce a valid signature for new bytes.

“Enable Tier 1 for the agent wheel before the store distributes it” means: before the store starts serving agent wheels to agents for self-update, add that signature check to the agent update path. It is the highest-value place for a signature because the agent wheel replaces a process that runs as root/SYSTEM on every endpoint.

We are **not** doing that now. The store and self-update will ship with Tier 0 and keep the manifest fields/verifier hook for a later upgrade. If the agent is stdlib-only, the future verifier can be RSA PKCS#1 v1.5 via `pow`, a pure-Python Ed25519 verifier, or a bundled `cryptography` dependency; that choice is deferred with Tier 1.

### Implementation note

Do not trust a client-provided hash, and do not stream a blob to an agent before the server has computed and stored its hash. The upload path writes to `tmp/`, computes the digest, `fsync`s, and atomically renames into the content-addressed path.

---

## 8. Install profiles and recipes

Prefer native installers. The agent chooses an argv template by `artifact_type`:

| Type | Install | Upgrade | Remove |
|---|---|---|---|
| `deb` | `apt-get install -y ./file.deb` | same with new file | `apt-get remove -y <pkg>` |
| `rpm` | `dnf install -y ./file.rpm` | same | `dnf remove -y <pkg>` |
| `msi` | `msiexec /i "file.msi" /qn /norestart` | same | `msiexec /x "file.msi" /qn` |
| `msix` | `Add-AppxPackage -Path file.msix` | versioned | `Remove-AppxPackage` |
| `zip`/`exe` | recipe | recipe | recipe |

A **recipe** is a declarative JSON document, not a shell script:

```json
{
  "verify": [["test", "-x", "/opt/acme/bin/acme"]],
  "install": [["dpkg", "-i", "{artifact}"]],
  "upgrade": [["dpkg", "-i", "{artifact}"]],
  "uninstall": [["dpkg", "-r", "acme-vpn"]]
}
```

Rules:

- Argument vectors only; no shell, no pipes, no command substitution.
- Placeholders: `{artifact}`, `{version}`, `{install_dir}` only.
- Paths are validated; the artifact path is supplied by the store, not the recipe.
- Recipes are admin-defined; `allow_recipes` is a local agent flag (default `false`).
- Filesystem/registry/service side effects must be declared for audit/uninstall.
- Do not accept a recipe from a package upload unless an admin explicitly approved it.

Prefer `apt-get install ./file.deb` / `dnf install ./file.rpm` over raw `dpkg -i`/`rpm -U` so dependencies resolve from the host’s approved repositories. For air-gapped hosts, bundle dependencies or use `--no-deps` only for self-contained packages.

---

## 9. Publish flow

1. Build the artifact (CI or release engineer).
2. Upload it via the admin API or CLI; the **server** computes SHA-256 and size:
   ```bash
   sextant store-publish \
     --slug acme-vpn \
     --version 4.2.1 \
     --file acme-vpn_4.2.1_amd64.deb \
     --artifact-type deb \
     --name "Acme VPN Client" \
     --vendor "Acme"
   ```
3. Server stores the blob, inserts package/release rows, emits `published`.
4. Assign the release to a device via the package rule API (store install wiring is a later phase).
5. When store installation is enabled, the agent sees the assignment on the next heartbeat and installs it.

Raw API upload uses `PUT /api/store/packages/{slug}/releases/{version}` with headers `X-Artifact-Type`, `X-Filename`, optional `X-Sha256`, `X-Channel`, `X-Min-Version`, `X-Notes-Url`, `X-Mandatory`.

Yank:

```bash
sextant store yank --slug acme-vpn --version 4.2.1 --reason "CVE-2026-…"
```

Yanked releases are not offered or downloadable for new installs; already-installed copies are reported and can be replaced by an assigned newer/pinned release.

---

## 10. Agent install flow

1. Heartbeat reports installed `store` packages and inventory hash.
2. Server returns assigned store packages that differ from installed state.
3. Agent obtains a short-lived download URL (or uses the release ID with its bearer token).
4. Download to a temp file; enforce size cap; verify SHA-256 and size.
5. Choose install profile; run the argv template with a timeout and bounded output.
6. Verify post-install state (package present, version, optional `verify` recipe).
7. Report per-package result; keep the artifact in a local cache for rollback/retry (bounded).
8. On failure, do not retry indefinitely; report and let the server/operator decide.

Store installs use the same `package_jobs`/`package_rules` machinery as apt/winget, with `manager=store`.

---

## 11. API

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/store/packages` | viewer+ | catalog |
| GET | `/api/store/packages/{slug}` | viewer+ | versions/metadata |
| POST | `/api/store/packages` | admin | create package metadata |
| PUT | `/api/store/packages/{slug}/releases/{version}` | admin | upload artifact (raw body) with `X-Artifact-Type`/`X-Filename` headers |
| PATCH | `/api/store/packages/{slug}` | admin | edit metadata/archive |
| POST | `/api/store/releases/{id}/yank` | admin | stop offering |
| GET | `/api/store/releases/{id}/download` | agent bearer | authorized artifact download |
| GET | `/api/store/events` | viewer+ | publish/download/install history |
| GET | `/api/store/stats` | admin | quota, blob count, unreferenced bytes |

Assignments use the package-management endpoints (`/api/agents/{id}/packages`, later group endpoints).

Download authorization checks:

- agent token valid;
- release not yanked;
- release platform/arch/Python matches the agent (for `agent-wheel`);
- an enabled rule/assignment targets this agent or one of its groups;
- path is derived from the stored hash, never from user input.

---

## 12. UI

**Store page**

- Catalog with icon, name, vendor, platforms, latest version.
- Package detail: versions, changelog, size, hash, install profile, assignments, download/install counts.
- Upload wizard: artifact, manifest, metadata; shows the server-computed hash and verification result.
- Yank/archive with confirmation and reason.
- Storage usage and unreferenced blobs.

**Device drawer → Packages tab**

- A `store` manager section alongside apt/dnf/winget.
- Installed version, desired state, status, events.
- “Install from store” picker filtered to compatible releases.

Later: group assignments, approval queue, rollout/rollback.

---

## 13. Security

- **Admin-only publishing.** Uploads require an admin session, CSRF, and TLS.
- **Integrity (Tier 0).** The server computes SHA-256 and size at upload; the agent recomputes them after download. A mismatch aborts installation. This guarantees “same bytes as the server hosts,” not authenticity against a compromised server.
- **Immutable blobs.** Content-addressed; replacing a version requires a new release (version monotonicity).
- **No path traversal.** Filenames are display metadata; blob paths come from hashes.
- **Upload limits.** Size cap, total quota, streaming with a byte counter, MIME/extension checks, archive inspection (zip/tar bomb limits), optional ClamAV scan.
- **No arbitrary URLs.** Agents download from the server’s release endpoint only; external storage, if added, is configured by an admin and still verified by hash.
- **Assignment-scoped downloads (planned for S2).** The S1 implementation allows any authenticated agent to download any non-yanked release; per-agent assignment checks ship with the store install flow.
- **Recipes are high trust.** Off by default (`allow_recipes=false`), admin-approved, argv-only. Prefer native installers; do not accept recipes from untrusted uploads.
- **Typosquatting/confusion.** Slugs are unique and admin-created; show vendor and hash; no anonymous publish.
- **Audit.** Publish, yank, download, install, upgrade, remove, failure, quota changes.
- **Yank/rollback.** Yank stops new installs; rollback is an explicit assignment of an older pinned release (hash still required).
- **Supply chain.** Build in CI from a tagged commit; consider reproducible builds and SBOM generation. Tier 1 signing/offline keys are future work.
- **Disk exhaustion.** Quota enforcement and GC; refuse uploads when the quota is reached.

---

## 14. GitHub relationship

**GitHub is used only for two things:**

1. **Source and CI** — the repository and Actions workflows that build/test artifacts.
2. **Server release feed** — the server may check GitHub Releases to discover a new *server* version (see [SERVER_UPDATE.md](SERVER_UPDATE.md)). The admin approves the update; GitHub is not allowed to publish straight into the store.

**Apps are not downloaded from GitHub.** App packages are uploaded to the store by an admin (UI/CLI) or by CI after a build. There is no GitHub app-release importer in this plan. The store is the only app and agent-wheel distribution channel.

- **First install:** the initial agent install can come from GitHub/source or a manual wheel. Once a self-update-capable agent is installed, upgrades come from the store.
- **Public releases:** you can still attach wheels/installers to GitHub Releases for humans; the store remains the fleet channel.
- **No importer:** a future mirroring importer is out of scope and would need explicit signature/hash pinning if it is ever added.

---

## 15. Self-update integration

The self-update plan should use the store rather than a second artifact system:

- Agent releases are store packages with `kind=agent` and `artifact_type=agent-wheel`.
- Update policies select a release/version; the heartbeat advertises it.
- The agent downloads through `/api/store/releases/{id}/download`, verifies the same hash and size, stages the wheel, and performs the atomic switch described in [SELF_UPDATE.md](SELF_UPDATE.md).
- `agent_releases` can be implemented as a filtered view of `store_releases` (or a thin table referencing it).
- This gives one upload pipeline, one hash/verification scheme, one quota/GC story, and one audit trail.

Bootstrap: install the first update-capable agent from GitHub/source; all later updates come from the store.

The server-side orchestration — check for a server release, let an admin approve it, then publish the matching agent wheel and offer it to agents — is described in [SERVER_UPDATE.md](SERVER_UPDATE.md).

---

## 16. Operations

- **Disk sizing:** total quota + headroom for uploads/tmp; monitor `store/blobs` and SQLite.
- **Backup:** snapshot the database, then `rsync` blobs (immutable); verify a sample hash after restore.
- **nginx:** raise `client_max_body_size` for the upload endpoint (or upload through the loopback API), set generous timeouts for large artifacts, and keep the store endpoints behind the same auth/TLS.
- **Retention:** keep the last N releases per package (default 5) plus any release referenced by an assigned policy; yank before deletion.
- **GC:** run at startup/probabilistically, never delete referenced blobs; log reclaimed bytes.
- **Monitoring:** quota usage, failed uploads, download errors, install failures, hash/size mismatches.
- **Air-gapped:** copy the blob directory and DB (or export/import a bundle) to the offline server.

---

## 17. Phases

| Phase | Deliverable | Effort | Risk |
|---|---|---|---|
| S1 | Blob store, hash-pinned manifest format, admin upload/list/yank/download, CLI, quota/GC basics; host agent wheels | M | low |
| S2 | `store` manager per device: deb/rpm/msi native installs, inventory, jobs, events, audit | M | medium |
| S3 | Store UI (catalog, upload, assignments, stats), device Packages integration | M | low |
| S4 | Recipes, archive inspection, malware scan, retention/GC polish, backup tooling | M | medium |
| S5 | Group assignments, S3 backend, SBOM/provenance, optional Tier 1 signing | L | medium |

Recommended order: **S1 → self-update U2/U3 → S2 → S3 → S4**. S1 unlocks self-update hosting first, which is the immediate need.

---

## 18. Decisions and open questions

Decisions recorded from the current direction:

- **Linux target:** Debian/Ubuntu (`apt`) is the default and first backend.
- **Windows apps:** in scope. `winget` is the primary backend; Chocolatey is optional; the store covers vendor `.msi`/`.msix`/portable packages. Store/MSIX per-user limitations remain.
- **Signing:** **Tier 0 only for now.** TLS + agent auth + SHA-256 + size; no signatures, no keys, no CA. `signature`/`key_id` fields are reserved for a future Tier 1 but are not enabled. Tier 1 (offline Ed25519/minisign or stdlib RSA verification) is deferred until requested.
- **GitHub:** source/CI and the **server release feed only**. Apps are uploaded to the store directly; there is no GitHub app importer and agents never download apps from GitHub.
- **Agent wheel:** also shipped through the store at Tier 0 for now; Tier 1 for the wheel is deferred with the rest of signing.

Still open:

1. Which artifact types are required first: `agent-wheel`, `deb`, `rpm`, `msi`, `msix`, `zip`, `exe`?
2. Maximum artifact size and total quota on the server’s disk?
3. Are recipes acceptable at all, or should the store support only native installers?
4. Should unassigned agents be able to download anything (for bootstrap), or only assigned/self-update artifacts?
5. Retention: how many versions per package, and how long to keep yanked releases?
6. External/S3 backend now or later? (Local disk is simpler for a single node.)
7. Which Windows package types must ship first: `msi`, `msix`, or both?

---

## 19. Alternatives

| Option | Notes |
|---|---|
| **GitHub Releases only** | Simple and free, but public/rate-limited, mutable tags, token exposure, and not usable on restricted networks. Good for bootstrapping and human downloads, not fleet updates. |
| **Nexus / Artifactory / ProGet / Cloudsmith / Packagecloud** | Mature artifact repositories with formats, retention, and access control. More infrastructure and licensing; overkill for a single-node lab/SMB. |
| **APT/YUM repository (reprepro/createrepo) + nginx** | Excellent for Linux native packages and signatures; does not cover MSI/zip/agent wheels; needs repo metadata management. Could be a later backend. |
| **Chocolatey internal repository** | Good for Windows packages if you standardize on Chocolatey; not Linux. |
| **S3/MinIO + signed URLs** | Scales storage separately; adds an external service and URL/authorization complexity. |
| **OCI registry (ORAS)** | Good for arbitrary artifacts and provenance; more concepts than needed here. |
| **SharePoint/SMB file share** | No integrity, assignment, audit, or safe install flow. Not recommended for executables. |

For this project, a **small built-in store** (content-addressed blobs + SHA-256 manifests + SQLite metadata, Tier 0) is the right scope. It solves agent self-update hosting immediately and gives a clean path for internal apps without adopting a full artifact platform.
