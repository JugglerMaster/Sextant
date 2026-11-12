# Discovery — plan

**Status:** D1–D3 implemented. D4–D6 remain proposed.

**Scope:** find, identify, and analyze devices on the networks this console is authorized to manage, then offer a path from "found" to "managed" (wake target or enrolled agent).

**Implemented now:** CIDR-scoped server-side discovery with allowlists and excludes, neighbour-table/PTR/OUI/SSDP collection, opt-in TCP connect and banner/TLS fingerprinting, classification, MAC/IP/hostname merging, deduplicated change events, a bounded background scan worker with cancellation, a read/write API, a Discovery page, and opt-in agent-relayed scans for an enrolled Linux agent's local VLAN over the existing outbound command/heartbeat path. See [Implementation notes](#implementation-notes-d1d3) at the end of this document for the exact route and data-model decisions.

**Not implemented yet:** analysis/reporting and export (D4), promotion/onboarding (D5), and DHCP/AP/hypervisor/SNMP integrations (D6).

Discovery is a separate concern from remote control. It answers *what is on the network*; control remains the existing agent path (queued shell, service management, power actions, WoL). Discovery does not add RDP/VNC/SSH/PTY support.

---

## Why

Today the server knows only two things:

1. agents that enrolled with a one-time token (`agents` table), and
2. wake targets typed in by an admin (`wake_targets`).

There is no subnet scan, ARP-table import, mDNS/SSDP listener, DHCP lease parser, SNMP poller, or hypervisor integration. An unmanaged machine, printer, NAS, camera, switch, or rogue device is invisible until someone enrolls it manually.

Discovery closes that gap and enables:

- an asset inventory of managed **and** unmanaged devices;
- onboarding of unmanaged hosts without manual MAC hunting;
- "new device on the network" and "new open port" alerts;
- a prioritized exposure list (admin ports with no agent);
- a more reliable WoL flow (promote a discovered MAC to a wake target).

---

## Current state (verified)

- `sextant_agent` reports only its own host: default-route interface, IP, MAC, disks, GPU, services, startup.
- `sextant_control` has no scanning, probing, or passive network code.
- WoL sends a magic packet to the server's broadcast domain; agents are not relays.
- The server runs unprivileged (`deploy/sextant.service`, `User=sextant`, no `CAP_NET_RAW`), binds loopback, and is normally fronted by nginx.
- Agents run as root/SYSTEM, make outbound HTTPS only, and execute queued work.
- Roles: `viewer` (read), `operator` (commands/WoL), `admin` (users/tokens/agents).
- Alerts are computed at read time; there is no push notification yet.
- Telemetry history exists (`telemetry_samples`, `GET /api/agents/{id}/history`).

---

## Scope

### In scope

- Device liveness, identity (IP ↔ MAC ↔ hostname ↔ vendor), and service fingerprinting.
- Passive and opt-in active probes from the server and from enrolled agents.
- Classification into coarse device types with a confidence score.
- Change detection: new device, new port/service, identity conflict, device went dark.
- Promotion to wake target or enrollment, with audit.
- Bounded retention and export.

### Non-goals

- Exploitation, credential guessing, default-password checks, vulnerability exploitation.
- Packet capture, traffic analysis, or credential interception.
- Scanning public internet ranges.
- A full NMS/SIEM replacement.
- Pixel-level remote desktop or interactive PTY.

### Ethics and authorization

Only scan networks you own or administer. Discovery is **disabled by default**; enabling it requires an explicit, audited acknowledgement. Every scan, promotion, ignore, and export is audited.

---

# What we would implement

## 1. Architecture

Three sources feed one normalization pipeline. Start with the server-side path; add agent-relayed coverage when multi-VLAN discovery is needed.

```text
  server LAN                    enrolled agent (per VLAN)          optional integrations
  ──────────                    ────────────────────────          ────────────────────
  ARP/neigh table               ARP/neigh table                   DHCP lease file/API
  reverse DNS                   reverse DNS                       UniFi / RouterOS / OPNsense
  SSDP (UDP 1900)               SSDP / mDNS                       Proxmox / ESXi guest list
  opt-in TCP fingerprint        opt-in TCP fingerprint            SNMP sysDescr (opt-in)
        │                              │                                  │
        └──────────────► discovery engine (server) ◄────────────────────┘
                                  │
                 normalize → dedupe by MAC → classify → diff
                                  │
             discovered_devices + discovery_scans + discovery_events (SQLite)
                                  │
                    Discovery page / API / alerts / export
```

Design constraints:

- **Single node.** The engine runs inside the server process (background thread + bounded queue), consistent with the existing single-node SQLite design. Do not introduce a worker service for v1.
- **No inbound agent ports.** Agent-relayed scans reuse the existing outbound heartbeat/command path.
- **No new runtime dependencies for v1.** Use stdlib (`socket`, `ipaddress`, `subprocess` for `ip neigh`/`arp -a`) and a bundled OUI table. mDNS/SNMP/integration parsers arrive later behind optional extras.
- **Least privilege first.** The server cannot open raw sockets; prefer neighbor tables, TCP connect, reverse DNS, and SSDP. ICMP/passive ARP/mDNS are agent-side capabilities.

## 2. Discovery methods, by phase

| Phase | Method | Where | Privilege | Noise | Value |
|---|---|---|---|---|---|
| D1 | Neighbor table (`ip neigh` / `arp -a`) | server + agent | none | none | IP + MAC of recently active hosts |
| D1 | Reverse DNS (PTR) | server + agent | none | low | hostname |
| D1 | OUI vendor lookup | server | none | none | vendor/manufacturer |
| D1 | SSDP / UPnP listen (UDP 1900) | server + agent | none | passive | TVs, printers, NAS, IoT |
| D1 | Device merge/dedupe by MAC | server | none | none | one row per device |
| D2 | TCP connect scan (curated ports) | server + agent | none | medium | liveness + fingerprint |
| D2 | Banner/header grab (SSH, HTTP, TLS cert) | server + agent | none | low | OS/service guess |
| D2 | Classification rules + confidence | server | none | none | device type |
| D3 | ICMP sweep | agent | root | medium | hosts without open TCP ports |
| D3 | Passive ARP/mDNS continuous | agent | root / optional extra | none | real-time new-device signals |
| D3 | Agent-relayed multi-VLAN scans | agent | none | medium | coverage outside the server LAN |
| D4 | SNMP v2c read (`sysDescr`, model, uptime) | server | none | low | printers, switches, UPS |
| D4 | DHCP lease import (file/API) | server | none | none | full lease history, offline devices |
| D4 | AP/hypervisor APIs (UniFi, RouterOS, OPNsense, Proxmox, ESXi) | server | token | none | Wi-Fi clients and VMs |
| D4 | External scanner import (Nmap XML/JSON, `arp-scan`) | server | none | none | high-fidelity results without writing probes |

**v1 port set** (deliberately small): `22, 53, 80, 139, 443, 445, 631, 1883, 3389, 5900, 5985, 5986, 8006, 8080, 8443, 9100, 32400`. Anything else is opt-in.

Probe defaults: per-host timeout 300–800 ms, concurrency 32–64, per-scan host cap, scan window and rate limit, hard exclusions for gateway/DC/hypervisor management ranges unless explicitly overridden.

## 3. Data model

### `discovered_devices`

| Column | Notes |
|---|---|
| `id`, `first_seen`, `last_seen` | lifecycle |
| `mac`, `ip`, `hostname`, `vendor` | MAC is the primary merge key |
| `device_type`, `os_guess`, `confidence` | classifier output |
| `source` | `server`, `agent:<id>`, `dhcp`, `hypervisor`, `snmp`, `import` |
| `segment` | CIDR/VLAN label |
| `open_ports`, `services`, `mdns`, `ssdp` | bounded JSON |
| `state` | `new`, `known`, `ignored`, `promoted` |
| `agent_id`, `wake_target_id` | correlation links |
| `tags`, `notes` | operator metadata |
| `mac_randomized` | locally-administered bit set |

### `discovery_scans`

`id`, `started_at`, `finished_at`, `scope`, `methods`, `initiated_by`, `status`, `hosts_found`, `error`.

### `discovery_events`

`id`, `at`, `device_id`, `kind` (`new_device`, `new_port`, `service_changed`, `went_dark`, `identity_conflict`, `promoted`), `detail` JSON.

Retention: scans ~30 days, events ~90 days, devices kept until ignored/deleted. Same bounded-pruning pattern as `telemetry_samples`.

### Correlation rules

1. Merge on MAC (primary).
2. Else merge on IP if the MAC is unknown and the IP is stable.
3. Else merge on hostname (lowest confidence).
4. Link to `agents` when the MAC matches an enrolled agent's reported MAC.
5. Link to `wake_targets` when the MAC matches.
6. Never merge two different known MACs into one device automatically; surface an identity conflict instead.

## 4. Classification and analysis

Classification is a rule table, not ML. Examples:

| Evidence | Guess |
|---|---|
| `445` + SMB + NetBIOS name | Windows workstation/server |
| `22` + SSH banner | Linux/Unix |
| `631` + `9100`; mDNS `_ipp._tcp` | printer |
| `8006` | Proxmox |
| `32400` | Plex |
| `3389` | Windows RDP host |
| `5900` | VNC-enabled host |
| `1883` | MQTT/IoT broker |
| SSDP `MediaRenderer` | TV/media device |
| Hypervisor/AP API | VM/host |

Each match contributes to a confidence score. Below threshold the device stays `unknown`. Randomized MACs are labelled, not treated as spoofing.

Analysis layer (what makes the list useful):

- **Baseline and drift:** first-seen port/service set; alert on new port, changed service, hostname/vendor change.
- **New-device detection:** MAC never seen before (grace period for randomized MACs).
- **Identity conflicts:** same MAC on two IPs, two MACs on one IP, OUI mismatch, hostname reuse.
- **Exposure report:** hosts with `22/3389/5900/5985` open and no enrolled agent.
- **Lifecycle:** first/last seen, SNMP uptime, "went dark" event.
- **Wake verification:** after WoL, confirm ICMP/TCP response; mark `woke` vs `sent`.
- **Asset inventory:** tags, owner, location, notes, CSV/JSON export, printable labels.
- **Topology hints:** gateway, per-VLAN counts, which agent/segment sees each device.

## 5. API (proposed)

| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/api/discovery/devices?state=new&segment=…&q=…&limit=…` | viewer+ | list/search |
| GET | `/api/discovery/devices/{id}` | viewer+ | detail + evidence + events |
| PATCH | `/api/discovery/devices/{id}` | operator+ | tags, notes, state |
| POST | `/api/discovery/devices/{id}/promote` | operator+ | create wake target / link agent |
| GET | `/api/discovery/scans` | viewer+ | scan history |
| GET | `/api/discovery/scans/{id}` | viewer+ | progress/results |
| POST | `/api/discovery/scan` | operator+ | start server-side scan |
| POST | `/api/agents/{id}/discover` | operator+ | queue agent-relayed scan |
| POST | `/api/agent/{id}/discovery/report` | agent bearer | ingest a bounded agent scan report |
| GET | `/api/discovery/agents` | viewer+ | eligible agent sources and segments |
| GET | `/api/discovery/export?format=csv` | admin | bounded export |
| DELETE | `/api/discovery/devices/{id}` | admin | delete |

Scans are asynchronous: start returns a scan ID; the UI polls until `finished`. SSE/WebSocket can replace polling later.

## 6. UI

New **Discovery** page:

- **Fleet summary:** counts by state/type/segment; new devices since last scan.
- **Table:** state, IP, MAC/vendor, hostname, type, open ports, last seen, linked agent, tags.
- **Filters:** new/known/ignored/promoted, segment, device type, vendor, "no agent", "admin ports open".
- **Device drawer:** identity, evidence (which probe found what), port/service history, first/last seen, identity conflicts, actions.
- **Scan panel:** scope, methods, start, progress, cancellation, audit link.
- **Promote flow:** create wake target, or generate a one-time enrollment token + OS-specific install command with the device name prefilled.
- **Export** (admin) and **ignore** (operator) with confirmation.
- Viewer sees read-only data; all mutations require operator/admin and CSRF.

## 7. Configuration (proposed)

| Variable | Default | Purpose |
|---|---|---|
| `SEXTANT_DISCOVERY_ENABLED` | `0` | Master switch; requires audited acknowledgement |
| `SEXTANT_DISCOVERY_CIDRS` | unset | Allowlisted scan scopes |
| `SEXTANT_DISCOVERY_EXCLUDE` | gateway/DC suggestions | Never-scan ranges |
| `SEXTANT_DISCOVERY_METHODS` | `neigh,dns,oui,ssdp` | Enabled probe set |
| `SEXTANT_DISCOVERY_PROBE_PORTS` | v1 port set | TCP fingerprint ports |
| `SEXTANT_DISCOVERY_ACTIVE_PROBES` | `0` | TCP/ICMP probing off by default |
| `SEXTANT_DISCOVERY_INTERVAL_HOURS` | `0` (manual) | Scheduled scans; `0` disables |
| `SEXTANT_DISCOVERY_MAX_HOSTS` | `1024` | Per-scan cap |
| `SEXTANT_DISCOVERY_CONCURRENCY` | `32` | Probe concurrency |
| `SEXTANT_DISCOVERY_TIMEOUT_MS` | `600` | Per-probe timeout |
| `SEXTANT_DISCOVERY_RETENTION_DAYS` | `90` | Events/scan retention |
| `SEXTANT_DISCOVERY_AGENT_RELAY` | `0` | Allow agent-relayed scans. Also requires the profile `discovery` module, per-agent `allow_discovery`, and local agent `allow_discovery` |
| `SEXTANT_DISCOVERY_EXPORT_LIMIT` | `10000` | Max export rows |

Agent-side: `allow_discovery` in `config.json` (default `false`), settable by installer, and server policy can restrict but never widen it.

## 8. Security and guardrails

Non-negotiable:

- **Default off.** Enabling requires an explicit "I am authorized to scan these networks" acknowledgement, audited with actor/IP/time.
- **Scope validation.** Every target must be inside configured CIDRs. Reject loopback, link-local, multicast, and public ranges.
- **Rate and window limits.** Concurrency caps, per-scan host cap, scan windows, and excludes for infrastructure.
- **No offensive actions.** No default credentials, no exploit modules, no brute force, no packet capture.
- **Agent-local denial wins.** `allow_discovery=false` cannot be overridden by server policy.
- **Least privilege.** Server probes use unprivileged sockets and neighbor tables; raw ICMP/passive ARP require the agent (root) and are opt-in.
- **Privacy.** MAC/hostname/port data can be personal data; retention controls, viewer visibility, and export restrictions apply.
- **Audit everything.** Scan start/finish, promotion, ignore, export, settings changes.
- **No SSRF/scope escape.** Do not let a scan target be supplied as an arbitrary host or URL; only CIDRs from the allowlist, expanded internally.

## 9. Rollout

| Phase | Deliverable | Effort | Risk | Acceptance criteria |
|---|---|---|---|---|
| **D1** ✅ | Schema + engine skeleton; server-side neighbor/PTR/OUI/SSDP; Discovery page read-only; API; audit; default off | M | low | A scan of an allowlisted CIDR returns devices with IP/MAC/vendor; no packets sent when disabled; viewer can read, operator can scan |
| **D2** ✅ | Opt-in TCP fingerprint + banners + classifier + drift/new-device events | M | medium | New device/new port events are deduplicated; scan is rate-limited and cancellable; unknown stays unknown |
| **D3** ✅ | Agent-relayed discovery for other VLANs (`discovery.scan` over the existing outbound command/heartbeat path, `allow_discovery`) | M | medium | An agent scans its own subnet and reports; server policy can disable; local agent denial wins; no inbound port opened |
| **D4** | Analysis: exposure report, identity conflicts, wake verification, CSV/JSON export | S–M | low | False-positive rate acceptable on a test LAN; exports are bounded and audited |
| **D5** | Onboarding: promote to wake target; enrollment token + install command; link to agent | S | low | Promoted device is marked and auditable; no duplicate wake targets |
| **D6** | Integrations: DHCP leases, UniFi/RouterOS/OPNsense, Proxmox/ESXi, SNMP | L each | low–medium | Import/merge does not overwrite operator metadata; failures are visible |

Recommended sequence: **D1 → D2 → D5 → D4 → D3 → D6**. Onboarding early makes D1 immediately useful; agent relay can wait until a second VLAN matters.

---

# Other options considered

These are alternatives to the hybrid recommendation, not additions to it. Choose one primary approach.

## Option A — Recommended: hybrid (server + agent + optional integrations)

**What:** server-side passive/neighbor discovery first, opt-in active TCP, agent-relayed per-VLAN later, integrations last.

**Pros:** no inbound ports; covers multiple VLANs; least privilege on the server; incremental; no new deps for v1.

**Cons:** more moving parts; agent relay needs new job plumbing; passive coverage depends on broadcast traffic.

**Choose when:** you want broad coverage without giving the server raw-socket privileges and without deploying a separate scanner.

## Option B — Server-only active scanning

**What:** server runs ARP/ICMP/TCP sweeps on its own interfaces; no agent changes.

**Pros:** simplest; one implementation; immediate results.

**Cons:** only the server's L2 segment; ICMP needs `CAP_NET_RAW` or root; noisy and firewall-visible; misses other VLANs entirely.

**Choose when:** all managed devices are on one subnet and the server may be granted `CAP_NET_RAW`.

## Option C — Agent-only discovery

**What:** every enrolled agent scans its local subnet; the server only aggregates.

**Pros:** preserves outbound-only model; naturally multi-VLAN; agents already run as root.

**Cons:** cannot discover a VLAN with no enrolled agent; still needs server UI/schema; agent policy and rollout complexity; scan load on endpoints.

**Choose when:** agents already exist on every segment and you want zero server privileges.

## Option D — Passive-only

**What:** listen to ARP, mDNS, SSDP, LLMNR, DHCP; no active probes.

**Pros:** zero scan noise; excellent for printers/TVs/IoT; safe on sensitive networks.

**Cons:** misses quiet hosts; needs raw sockets/capabilities; OS-dependent; incomplete inventories.

**Choose when:** stealth/noise is the hard constraint and coverage can be partial.

## Option E — External scanner import

**What:** import Nmap XML/JSON, `arp-scan`, or AP/DHCP exports; the app only normalizes, stores, and displays.

**Pros:** highest fidelity with least code; no scanning bugs; uses tools the network team already trusts; easy compliance story.

**Cons:** operational dependency; users must run scans elsewhere; no "scan now" button; format drift; scheduling gaps.

**Choose when:** a network team already owns scanning and you want the console to be the system of record, not the scanner.

## Option F — Full NMS integration

**What:** integrate with an existing NMS/SNMP platform (Zabbix, LibreNMS, PRTG, NetBox) or become one.

**Pros:** mature polling, topology, and alerting; no probe code.

**Cons:** heavy dependency; API limits; duplicated inventory; conflicts with single-node scope.

**Choose when:** one already exists and this console should only consume its inventory.

## Option G — No built-in discovery; export/integrate only

**What:** keep discovery out; accept manual wake targets and agent enrollment; integrate with NetBox/UniFi for asset data later.

**Pros:** smallest attack surface and maintenance burden; no scanning liability.

**Cons:** no asset inventory, no new-device alerts, no onboarding shortcut.

**Choose when:** the product should stay strictly a remote-operations console.

## Option H — Sidecar scanner container

**What:** run Nmap/arp-scan in a separate container or VM, controlled by the server, results imported.

**Pros:** isolates scanning privileges and dependencies; can be placed per VLAN; strong tooling.

**Cons:** another deployable; container/network permissions; still needs normalization/UI; harder for a home-lab install.

**Choose when:** scanning must be privileged but the server should stay unprivileged.

## Comparison

| Option | Coverage | Noise | Server privilege | Effort | Dependency risk | Best for |
|---|---|---|---|---|---|---|
| A Hybrid (recommended) | high | low–medium | low | M | low | general self-hosted |
| B Server-only | one VLAN | medium | medium/high | S | low | single subnet |
| C Agent-only | per agent VLAN | medium | none on server | M | low | multi-VLAN, agents everywhere |
| D Passive-only | partial | none | low–medium | S–M | low | sensitive networks |
| E Import | depends on scanner | none | none | S | external tool | existing scanner owner |
| F NMS | high | external | none | L | high | existing NMS |
| G None | none | none | none | — | none | strict remote-control scope |
| H Sidecar | high | medium | isolated | L | container tooling | privileged scanning, separate blast radius |

---

# Dependencies and interactions

- **Alerting:** new-device/new-port/went-dark events should reuse the generic alert channel (webhook/ntfy/SMTP) once it exists. Until then, events are visible only in the Discovery page.
- **Telemetry history:** discovery is network-level; `telemetry_samples` remains per-agent. Do not merge the two schemas.
- **Command approval:** promotion to enrollment generates a token; that action should require operator+ and be audited. If approval mode ships, promotion can require approval too.
- **WoL:** promotion should create a wake target and later verify the wake with ICMP/TCP.
- **RBAC:** viewer read is proposed; if discovery data is considered sensitive, restrict reads to operator+ by configuration.
- **Retention:** reuse the existing bounded-pruning pattern; add an index whose leftmost column is `at` for events/scans.

---

# Open decisions

1. **Purpose:** asset inventory, rogue-device security monitoring, or onboarding? This changes the UI, retention, and alert defaults.
2. **Coverage:** which subnets/VLANs must be covered, and is the server on any of them?
3. **Active probing:** is passive-only acceptable, or are TCP/ICMP probes allowed? Which port set?
4. **Agent relay:** are agents allowed to scan their local segment, or must all scanning stay on the server?
5. **Privacy/retention:** how long may MAC/hostname data be kept, and who may read/export it?
6. **Integrations:** is there an existing DHCP/AP/NMS/hypervisor source to prefer over probing?
7. **Onboarding:** should promotion auto-issue an enrollment token, or only generate a command for an admin to run?
8. **Role visibility:** can viewers see discovery data, or operator+ only?

---

# Success criteria

- Discovery is off by default and cannot be enabled without an audited acknowledgement.
- A scan of an allowlisted CIDR returns a deduplicated device list with IP, MAC, vendor, and evidence.
- A new device or new open port produces exactly one event, not one per heartbeat/scan.
- No scan can target an address outside the configured CIDRs.
- Promotion creates a wake target or enrollment command without duplicating existing records.
- Retention keeps tables bounded; the dashboard stays responsive with 100+ agents and thousands of devices.
- No new runtime dependency is required for D1; optional extras are isolated behind `[ldap]`/`[discovery]` style extras.

---

# Related documents

- [Architecture](ARCHITECTURE.md) — request/command flow, data model, deliberate limits.
- [Security](SECURITY.md) — threat model, trust boundaries, audit expectations.
- [Roadmap](ROADMAP.md) — alerting, history/charts, approval workflow, agent lifecycle.
- [API](API.md) — existing routes and conventions.
- [Agents](AGENTS.md) — agent config, policy, and execution behavior.

---

# Implementation notes (D1–D3)

## Configuration

| Variable | Default | Implemented behavior |
|---|---|---|
| `SEXTANT_DISCOVERY_ENABLED` | `0` | Master switch. Scans are refused while it is `0`, even if the acknowledgement exists. |
| `SEXTANT_DISCOVERY_CIDRS` | unset | Comma-separated allowlist. Each entry must validate as a private RFC1918/ULA CIDR. Public, documentation, loopback, link-local, multicast, unspecified, reserved, and overly broad (`< /8` IPv4, `< /48` IPv6) ranges are rejected. |
| `SEXTANT_DISCOVERY_EXCLUDE` | unset | Comma-separated CIDRs removed after expansion. Excludes may be inside or outside an allowlisted range. |
| `SEXTANT_DISCOVERY_METHODS` | `neigh,dns,oui,ssdp` | Enabled methods. `tcp` must also be listed here and requires active probes. Unknown method names are ignored at startup. |
| `SEXTANT_DISCOVERY_PROBE_PORTS` | v1 port set | Comma-separated TCP ports. Invalid and out-of-range values are dropped. |
| `SEXTANT_DISCOVERY_ACTIVE_PROBES` | `0` | Enables TCP connect/banner fingerprinting. Passive methods stay available while this is off. |
| `SEXTANT_DISCOVERY_INTERVAL_HOURS` | `0` | `0` disables scheduled scans. A non-zero value schedules an allowlisted scan from the worker after a stored acknowledgement exists. |
| `SEXTANT_DISCOVERY_MAX_HOSTS` | `1024` | Hard per-scan expansion cap (1–65536). A scope larger than the cap is rejected before any packet is sent. |
| `SEXTANT_DISCOVERY_CONCURRENCY` | `32` | Probe worker cap (1–128), also bounded by the number of targets. |
| `SEXTANT_DISCOVERY_TIMEOUT_MS` | `600` | Per-connect/banner timeout (100–5000 ms). |
| `SEXTANT_DISCOVERY_RETENTION_DAYS` | `90` | Scans and discovery events older than this are pruned at startup and after each scan. Devices are retained until deleted or ignored. |
| `SEXTANT_DISCOVERY_AGENT_RELAY` | `0` | Enables `POST /api/agents/{id}/discover` and agent report ingestion. Relay also requires the profile `discovery` module and per-agent `allow_discovery`; the agent's local `allow_discovery` is a hard ceiling. |
| `SEXTANT_DISCOVERY_EXPORT_LIMIT` | `10000` | Parsed and exposed for the future D4 export; export is not implemented. |

## Safety model

- Discovery is off by default. `POST /api/discovery/scan` requires both `SEXTANT_DISCOVERY_ENABLED=1` and an admin-stored acknowledgement of **"I am authorized to scan these networks"**. The acknowledgement is audited with actor and IP.
- Every requested scope must be a subnet of a configured allowlist CIDR. Individual host scopes, out-of-scope ranges, oversized scopes, and excluded-only scopes are rejected before queueing.
- The server uses only unprivileged stdlib sockets and `ip neigh`/`arp -a`; there are no raw sockets, packet captures, exploit modules, credential checks, or arbitrary-target/URL inputs.
- One scan runs at a time. Submissions are bounded by a small in-process queue and a five-second start rate limit; the UI can cancel a queued or running scan.
- The scan worker is a daemon thread started by the FastAPI lifespan and stopped on shutdown. Cancellation is checked between hosts and futures. No work runs on the event loop.
- DNS, neighbour, SSDP, and TCP work is best-effort. A failed host or port never aborts the scan or the worker.

## Agent relay (D3)

`POST /api/agents/{agent_id}/discover` (operator+) queues a `discovery.scan`
command for an enrolled **Linux** agent and creates a `discovery_scans` row with
`agent_id`, `source=agent:<id>`, and the queued `command_id`. The server passes
only validated data: allowlisted CIDR scope, excludes, methods, the documented
port set, timeout, concurrency, host cap, and a bounded budget. The agent
validates all of it again locally, filters every candidate IP to the supplied
scope, and refuses public, loopback, link-local, multicast, unspecified,
reserved, and documentation ranges. Windows agents report `unsupported`.

The effective relay gate is the intersection of:

1. `SEXTANT_DISCOVERY_ENABLED=1`;
2. `SEXTANT_DISCOVERY_AGENT_RELAY=1`;
3. the assigned profile's `modules.discovery`;
4. the per-agent policy `allow_discovery`;
5. the agent's local `allow_discovery` (default `false`).

The agent reports through the existing outbound connection to
`POST /api/agent/{id}/discovery/report`. Reports are bearer-authenticated and
bound to the scan's `agent_id`. Devices must be inside the scan scope and not
excluded; row count is capped at 512, evidence objects are bounded, and the
whole report body is capped at 1 MB. Merging and change detection are performed
atomically through `merge_observations`, with `source=agent:<id>`, the computed
segment, and relay evidence attached. The scan becomes `succeeded` only after a
valid report; a command failure marks it failed, cancellation marks queued work
cancelled immediately, and a report that arrives after cancellation is rejected.
The agent uses the existing `ip neigh`/`arp -a`, PTR, shared OUI table, optional
SSDP, and opt-in TCP/banner/TLS methods. It runs the scan as one bounded command
action with a local deadline; no inbound port is opened and the heartbeat model
is unchanged.

The Discovery page adds a **Scan source** selector. It shows only eligible
agents, their reported IP and allowlisted segment, and a relay-disabled or
no-eligible-agent explanation. Selecting an agent scopes the form to that
agent's segment; selecting **This server** uses the server-side worker from D1–D2.

## Data model

- `discovered_devices` stores identity, classifier output, source/segment, bounded JSON evidence (`open_ports`, `services`, `mdns`, `ssdp`, `evidence`), operator metadata, and agent/wake-target links.
- `discovery_scans` stores the requested scope/methods, initiator, status (`queued`, `running`, `succeeded`, `failed`, `cancelled`), host count, progress, and cancellation flag.
- `discovery_events` stores `new_device`, `new_port`, `service_changed`, `identity_conflict`, `went_dark`, and reserved `promoted` rows. Events are deduplicated by `(device_id, kind, detail)`.
- Merge order is MAC → stable IP when the MAC is unknown → hostname when no MAC/IP match exists. Two different known MACs are never merged; the existing row receives an `identity_conflict` event and a new device row is created.
- A locally-administered MAC bit is labelled `mac_randomized`. Randomized MACs still merge by IP/hostname and lose ten confidence points.
- `went_dark` is emitted once per device and `last_seen` value when a device in a scanned segment has not been seen for 24 hours.

## API

| Method | Route | Role | Notes |
|---|---|---|---|
| GET | `/api/discovery/devices` | viewer+ | Filters: `state`, `segment`, `q`, `type`, `vendor`, `no_agent`, `admin_ports`, `mac`, `limit`, `offset`. Returns `summary`. |
| GET | `/api/discovery/devices/{id}` | viewer+ | Device, events, linked agent, linked wake target. |
| PATCH | `/api/discovery/devices/{id}` | operator+ | `tags`, `notes`, `state`; audited. |
| DELETE | `/api/discovery/devices/{id}` | admin | Removes the device and its events; audited. |
| GET | `/api/discovery/scans` | viewer+ | Paginated history and `active_scan_id`. |
| GET | `/api/discovery/scans/{id}` | viewer+ | Status and progress. |
| POST | `/api/discovery/scan` | operator+ | CSRF + acknowledgement + allowlist validation + host cap; returns the queued scan. |
| POST | `/api/discovery/scans/{id}/cancel` | operator+ | Requests cancellation; audited. |
| GET | `/api/discovery/settings` | viewer+ | Effective allowlist, methods, caps, acknowledgement state, and disabled reasons. |
| PATCH | `/api/discovery/settings` | admin | Stores or clears the audited acknowledgement. |

Audit actions: `discovery.acknowledged`, `discovery.acknowledgement_cleared`, `discovery.scan_started`, `discovery.scan_finished`, `discovery.scan_cancel_requested`, `discovery.scan_cancelled`, `discovery.device_updated`, and `discovery.device_deleted`.

When a discovered MAC matches an enrolled agent, first discovery and new ports also create an `agent_events` row with kind `discovery`, so the new-device/new-port signal appears in that agent's History/What-changed timeline as well as the Discovery page.

## UI

The top-level **Discovery** page is available to every signed-in user. It renders disabled/unacknowledged explanations, fleet summary counts, segmented/typed/vendor filters, an inventory table, a device drawer with evidence/ports/events/correlations, and a scan panel with scope, methods, progress, cancellation, history, and an audit link. Operators can tag, annotate, ignore, and mark devices known; admins can delete. No inline styles are used, focus is trapped in the drawer, and the page is covered by the jsdom and Playwright/axe suites.

## Known limitations

- Server-side discovery only sees the server's own L2 segment and its neighbour table. Coverage of other VLANs requires an enrolled Linux agent on each segment with relay and local discovery enabled.
- Agent-relayed scans run as a bounded command action while the agent's normal heartbeat loop is waiting; a very large active scan can delay that heartbeat for the scan budget (currently 120 seconds). No separate worker process or inbound listener is opened.
- Passive SSDP requires the server to be able to bind UDP 1900; otherwise SSDP collection returns no results without failing the scan.
- Reverse DNS depends on the host resolver and is best-effort.
- TLS subject/SAN extraction uses the CPython stdlib X.509 test decoder; platforms that omit it still report an open TLS port but no certificate names.
- There is no CSV/JSON export, promotion, or automatic alert delivery yet (D4/D5 and the alerting work).
