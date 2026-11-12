const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");

const path = require("path");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(`${root}/sextant_control/static/index.html`, "utf8");
const source = fs.readFileSync(`${root}/sextant_control/static/app.js`, "utf8");
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => errors.push(`jsdom: ${error.message}`));
virtualConsole.on("error", (...args) => errors.push(`console: ${args.join(" ")}`));

const dom = new JSDOM(html, {
  url: "http://localhost:8080/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;
window.addEventListener("error", (event) => errors.push(`window: ${event.message}`));
if (!window.HTMLDialogElement.prototype.showModal) {
  window.HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  window.HTMLDialogElement.prototype.close = function (value) {
    this.removeAttribute("open");
    if (value !== undefined) this.returnValue = value;
    this.dispatchEvent(new window.Event("close"));
  };
}

const eventSources = [];
class MockEventSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.listeners = {};
    eventSources.push(this);
  }
  addEventListener(type, handler) {
    this.listeners[type] = this.listeners[type] || [];
    this.listeners[type].push(handler);
  }
  emit(type, data) {
    const event = { type, data: JSON.stringify(data) };
    for (const handler of this.listeners[type] || []) handler(event);
  }
  close() {
    this.closed = true;
  }
}
window.EventSource = MockEventSource;
window.__eventSources = eventSources;
const findButton = (root, text) =>
  [...root.querySelectorAll("button")].find((button) => button.textContent.trim() === text);

const agent = {
  id: "agt_demo",
  name: "Studio PC",
  hostname: "studio-pc",
  os: "windows",
  os_version: "Windows 11 Pro",
  arch: "AMD64",
  ip: "192.168.1.20",
  mac: "AA:BB:CC:DD:EE:FF",
  machine_id: "machine-demo",
  agent_version: "0.3.0",
  tags: ["office", "gpu"],
  notes: "",
  watch_services: ["Spooler"],
  profile_id: "prof_workstation",
  policy: { allow_shell: true, allow_power: true, allow_service: true, allow_packages: true },
  enrolled_at: "2026-11-06T07:00:00Z",
  last_seen: "2026-11-06T08:00:00Z",
  service_inventory_at: "2026-11-06T07:45:00Z",
  online: true,
  cpu_pct: 18.2,
  mem_pct: 44.0,
  mem_total: 34359738368,
  mem_used: 15118284800,
  uptime_seconds: 90000,
  disks: [{ mount: "C:", total: 1000, used: 850, free: 150, percent: 85, fs: "NTFS" }],
  gpus: [{ name: "NVIDIA RTX 4090", util_pct: 97, mem_used: 20000000000, mem_total: 25000000000, temp_c: 78 }],
  services: [
    { name: "Spooler", display_name: "Print Spooler", status: "running", startup: "Auto", watched: true },
    { name: "wuauserv", display_name: "Windows Update", status: "stopped", startup: "Manual", watched: false },
    { name: "BrokenSvc", display_name: "Broken Service", status: "failed", startup: "Auto", watched: true },
    ...Array.from({ length: 52 }, (_, index) => ({
      name: `svc-${index + 1}`,
      display_name: `Managed service ${index + 1}`,
      status: index % 2 === 0 ? "running" : "stopped",
      startup: "Auto",
      watched: false,
    })),
  ],
  startup: [{ name: "Backup", command: "C:\\backup.exe", source: "HKCU Run", enabled: true }],
  extra: { cpu_model: "Ryzen 9", kernel: "10.0", gpu_processes: [{ pid: 4242, name: "python.exe", mem_used: 8000000000 }] },
  alert_count: 1,
  alerts: [
    {
      key: "disk_root",
      incident_id: "inc_disk_1",
      first_seen_at: "2026-11-06T07:10:00Z",
      last_seen_at: "2026-11-06T08:00:00Z",
      level: "warning",
      message: "Disk C: is 85% full",
      acknowledged: false,
      acknowledged_at: null,
      snoozed: false,
      snoozed_until: null,
    },
    {
      key: "memory",
      incident_id: "inc_memory_1",
      first_seen_at: "2026-11-06T07:20:00Z",
      last_seen_at: "2026-11-06T08:00:00Z",
      level: "critical",
      message: "Memory is 91% used",
      acknowledged: true,
      acknowledged_at: "2026-11-06T07:30:00Z",
      snoozed: false,
      snoozed_until: null,
    },
  ],
};

const historyCommands = [
  { id: "cmd_queued", agent_id: "agt_demo", kind: "shell", args: { shell: "bash", command: "uptime" }, status: "queued", created_at: "2026-11-06T08:00:00Z", completed_at: null, exit_code: null, stdout: "", stderr: "", error: "", output_truncated: false },
  { id: "cmd_failed", agent_id: "agt_demo", kind: "service", args: { action: "restart", name: "Spooler" }, status: "failed", created_at: "2026-11-06T07:59:00Z", completed_at: "2026-11-06T07:59:30Z", exit_code: 1, stdout: "", stderr: "failed to restart", error: "", output_truncated: false },
  { id: "cmd_alpha", agent_id: "agt_demo", kind: "shell", args: { shell: "bash", command: "alpha search target" }, status: "succeeded", created_at: "2026-11-06T07:58:00Z", completed_at: "2026-11-06T07:58:10Z", exit_code: 0, stdout: "alpha output", stderr: "", error: "", output_truncated: false },
  ...Array.from({ length: 27 }, (_, index) => ({
    id: `cmd_retained_${index + 1}`,
    agent_id: "agt_demo",
    kind: "refresh",
    args: {},
    status: "succeeded",
    created_at: `2026-11-06T07:${String(57 - index).padStart(2, "0")}:00Z`,
    completed_at: `2026-11-06T07:${String(57 - index).padStart(2, "0")}:05Z`,
    exit_code: 0,
    stdout: "",
    stderr: "",
    error: "",
    output_truncated: false,
  })),
];

const dockerHost = {
  agent_id: "agt_demo",
  name: "Studio PC",
  hostname: "studio-pc",
  os: "linux",
  online: true,
  last_seen: "2026-11-06T08:00:00Z",
  available: true,
  reason: "",
  engine_version: "27.5.1",
  api_version: "1.47",
  compose_version: "2.32.4",
  compose_available: true,
  rootless: false,
  storage_driver: "overlay2",
  cgroup_driver: "systemd",
  docker_root_dir: "/var/lib/docker",
  counts: {
    containers: 2,
    running: 1,
    paused: 0,
    stopped: 1,
    unhealthy: 0,
    images: 1,
    volumes: 1,
    networks: 2,
  },
  inventory_hash: "a".repeat(64),
  inventory_revision: 3,
  inventory_at: "2026-11-06T08:00:00Z",
  collected_at: "2026-11-06T08:00:00Z",
  heartbeat_at: "2026-11-06T08:00:00Z",
  age_seconds: 30,
  stale: false,
  last_error: "",
  capabilities: { mutate: true, exec: true, prune: true },
};
const dockerHost2 = {
  ...dockerHost,
  agent_id: "agt_second",
  name: "Second Docker",
  hostname: "docker-02",
  inventory_revision: 2,
  counts: { ...dockerHost.counts, containers: 1, running: 1, stopped: 0, images: 2, volumes: 0, networks: 2 },
};
const dockerDetail = {
  server_time: "2026-11-06T08:00:30Z",
  stale_seconds: 900,
  host: dockerHost,
  deployments: [
    {
      id: "dep_drift",
      stack_id: "stk_web",
      agent_id: "agt_demo",
      revision_hash: "e".repeat(64),
      project_name: "web",
      action: "up",
      status: "succeeded",
      active: true,
      drift: true,
      config_drift: true,
      stack_name: "Web stack",
      agent_name: "Studio PC",
    },
  ],
  containers: [
    {
      id: "a".repeat(64),
      name: "web-1",
      image: "nginx:latest",
      command: "nginx -g daemon off;",
      state: "running",
      status: "Up 2 hours (healthy)",
      health: "healthy",
      exit_code: null,
      restart_policy: "unless-stopped",
      ports: ["0.0.0.0:8080->80/tcp"],
      labels: { "com.docker.compose.project": "web" },
      mounts: ["web_data:/data"],
      networks: ["web_default"],
      compose_project: "web",
      compose_service: "nginx",
    },
    {
      id: "b".repeat(64),
      name: "worker-1",
      image: "worker:latest",
      command: "./worker",
      state: "exited",
      status: "Exited (0) 3 hours ago",
      health: "",
      exit_code: 0,
      restart_policy: "no",
      ports: [],
      labels: {},
      mounts: [],
      networks: ["web_default"],
      compose_project: "",
      compose_service: "",
    },
  ],
  images: [
    {
      id: "sha256:" + "c".repeat(64),
      repo_tags: ["nginx:latest"],
      repo_digests: [],
      size: 187000000,
      shared_size: 0,
      containers: 1,
      dangling: false,
      created_at: "2026-11-06T07:00:00Z",
    },
  ],
  volumes: [
    {
      name: "web_data",
      driver: "local",
      scope: "local",
      mountpoint: "/var/lib/docker/volumes/web_data/_data",
      labels: {},
      in_use: false,
    },
  ],
  networks: [
    {
      id: "d".repeat(64),
      name: "web_default",
      driver: "bridge",
      scope: "local",
      internal: false,
      attachable: false,
      labels: {},
      container_count: 2,
    },
  ],
};

const dockerCommandResults = {};
let dockerCommandSeq = 0;

const composeRevision = "e".repeat(64);
const composeStack = {
  id: "stk_web",
  slug: "web",
  name: "Web stack",
  description: "Compose web stack",
  current_revision_hash: composeRevision,
  created_by: "admin",
  created_at: "2026-11-06T07:00:00Z",
  updated_at: "2026-11-06T08:00:00Z",
  archived_at: null,
  deployed_host_count: 1,
  revision_count: 1,
  files: [{ name: ".env", size: 19 }, { name: "compose.yaml", size: 74 }],
  contents: {
    "compose.yaml": "services:\n  web:\n    image: nginx:latest\n",
    ".env": "TOKEN=secret-value\n",
  },
  revisions: [
    {
      id: "rev_web",
      stack_id: "stk_web",
      revision_hash: composeRevision,
      message: "initial",
      created_by: "admin",
      created_at: "2026-11-06T08:00:00Z",
      files: [{ name: ".env", size: 19 }, { name: "compose.yaml", size: 74 }],
    },
  ],
};
const composeDeployments = [
  {
    id: "dep_web",
    stack_id: "stk_web",
    agent_id: "agt_demo",
    revision_hash: composeRevision,
    project_name: "sextant_web",
    action: "up",
    status: "succeeded",
    command_id: "cmd_compose",
    active: true,
    pull_before_up: true,
    remove_volumes: false,
    created_by: "admin",
    created_at: "2026-11-06T08:00:00Z",
    started_at: "2026-11-06T08:00:01Z",
    finished_at: "2026-11-06T08:00:10Z",
    error: "",
    log: "Container web-1 Started",
    stack_name: "Web stack",
    stack_slug: "web",
    agent_name: "Studio PC",
  },
];

const discoverySettings = {
  enabled: true,
  acknowledged: true,
  acknowledged_at: "2026-11-06T07:00:00Z",
  acknowledged_by: "admin",
  acknowledgement_text: "I am authorized to scan these networks",
  cidrs: ["192.168.1.0/24"],
  cidr_errors: [],
  exclude: [],
  methods: ["neigh", "dns", "oui", "ssdp", "tcp"],
  probe_ports: [22, 80, 443, 8006],
  active_probes: true,
  interval_hours: 0,
  max_hosts: 1024,
  concurrency: 32,
  timeout_ms: 600,
  retention_days: 90,
  agent_relay: false,
  export_limit: 10000,
  disabled_reasons: [],
};
const discoveryDevices = [
  {
    id: "dev_printer",
    first_seen: "2026-11-06T07:00:00Z",
    last_seen: "2026-11-06T08:00:00Z",
    mac: "b8:27:eb:11:22:33",
    ip: "192.168.1.40",
    hostname: "printer.lan",
    vendor: "Raspberry Pi Foundation",
    device_type: "printer",
    os_guess: "Embedded",
    confidence: 0.9,
    source: "server",
    segment: "192.168.1.0/24",
    open_ports: [22, 80, 631],
    services: {
      "22": { protocol: "ssh", banner: "SSH-2.0-OpenSSH_9.6", product: "OpenSSH", version: "9.6" },
      "80": { protocol: "http", status: 200, server: "nginx/1.24", title: "Printer" },
    },
    mdns: {},
    ssdp: {},
    evidence: { methods: ["neigh", "dns", "oui", "ssdp"], neighbor: true, reverse_dns: true, ssdp: false, tcp: true },
    state: "new",
    agent_id: null,
    wake_target_id: "wol_dev",
    wake_status: "sent",
    wake_verified_at: "2026-11-06T07:58:00Z",
    identity_conflict: true,
    tags: ["office"],
    notes: "",
    mac_randomized: false,
  },
  {
    id: "dev_esxi",
    first_seen: "2026-11-06T07:05:00Z",
    last_seen: "2026-11-06T07:55:00Z",
    mac: "00:50:56:aa:bb:cc",
    ip: "192.168.1.50",
    hostname: "esxi.lan",
    vendor: "VMware",
    device_type: "hypervisor",
    os_guess: "Hypervisor",
    confidence: 0.85,
    source: "server",
    segment: "192.168.1.0/24",
    open_ports: [443, 8006],
    services: { "443": { protocol: "tls", subject: "esxi.lan", issuer: "Lab CA", san: ["esxi.lan"] } },
    mdns: {},
    ssdp: {},
    evidence: { methods: ["neigh", "dns", "oui", "tcp"], neighbor: true, reverse_dns: true, ssdp: false, tcp: true },
    state: "known",
    agent_id: "agt_demo",
    wake_target_id: null,
    wake_status: "woke",
    wake_verified_at: "2026-11-06T07:59:00Z",
    identity_conflict: false,
    tags: [],
    notes: "VM host",
    mac_randomized: false,
  },
];
const discoverySummary = {
  total: 2,
  new: 1,
  new_count: 1,
  states: { new: 1, known: 1, ignored: 0, promoted: 0 },
  types: [
    { type: "printer", count: 1 },
    { type: "hypervisor", count: 1 },
  ],
  segments: [
    {
      segment: "192.168.1.0/24",
      count: 2,
      new: 1,
      managed: 1,
      unmanaged: 1,
      exposure: 1,
      gateway_hint: "192.168.1.1",
      gateway_source: "lowest host",
    },
  ],
  vendors: [
    { vendor: "Raspberry Pi Foundation", count: 1 },
    { vendor: "VMware", count: 1 },
  ],
  no_agent: 1,
  managed: 1,
  unmanaged: 1,
  exposure: 1,
  conflicts: 1,
  conflict_device_ids: ["dev_printer"],
};
const discoveryConflicts = [
  {
    type: "ip_multiple_macs",
    key: "192.168.1.40",
    device_ids: ["dev_printer"],
    devices: [discoveryDevices[0]],
    detail: { ip: "192.168.1.40", macs: ["b8:27:eb:11:22:33", "aa:bb:cc:dd:ee:ff"] },
  },
];
const discoveryEvents = [
  { id: 2, at: "2026-11-06T07:50:00Z", device_id: "dev_printer", kind: "new_port", detail: { port: 631 } },
  { id: 1, at: "2026-11-06T07:00:00Z", device_id: "dev_printer", kind: "new_device", detail: { ip: "192.168.1.40" } },
];
const discoveryScans = [
  {
    id: "dsc_demo",
    started_at: "2026-11-06T07:00:00Z",
    finished_at: "2026-11-06T07:01:00Z",
    scope: ["192.168.1.0/24"],
    methods: ["neigh", "dns", "oui", "ssdp"],
    initiated_by: "admin",
    status: "succeeded",
    hosts_found: 2,
    error: "",
    progress: 100,
    hosts_scanned: 254,
    cancel_requested: false,
  },
];
const discoveryAgents = [
  {
    id: "agt_demo",
    name: "Studio PC",
    hostname: "studio-pc",
    os: "linux",
    ip: "192.168.1.20",
    segment: "192.168.1.0/24",
    online: true,
    eligible: true,
    reasons: [],
    discovery_module: true,
    allow_discovery: true,
  },
];
let discoveryActiveScan = null;

const responses = {
  "/api/auth/config": { status: 200, body: { ldap_enabled: false, version: "0.3.0", public_url: "" } },
  "/api/me": { status: 401, body: { detail: "Authentication required." } },
  "/api/auth/login": {
    status: 200,
    body: {
      user: {
        id: 1,
        username: "admin",
        display_name: "Admin",
        email: "",
        role: "admin",
        source: "local",
        created_at: "2026-11-06T07:00:00Z",
        last_login_at: "2026-11-06T08:00:00Z",
      },
      csrf: "csrf-demo",
      permissions: { viewer: true, operate: true, admin: true },
      settings: { ldap_enabled: false, local_auth: true, offline_after_seconds: 90, version: "0.3.0", public_url: "" },
    },
  },
  "/api/update/status": {
    status: 200,
    body: {
      update_available: true,
      current_version: "0.3.0",
      latest_version: "9.9.9",
      url: "https://example.test/releases",
    },
  },
  "/api/dashboard": {
    status: 200,
    body: {
      server_time: "2026-11-06T08:00:00Z",
      stats: { total: 1, online: 1, offline: 0, alerts: 1, active_commands: 0 },
      agents: [agent],
    },
  },
  "/api/agents/agt_demo/packages": {
    status: 200,
    body: {
      inventory: [
        { manager: "apt", package: "nginx", version: "1.24.0-1", arch: "amd64", source: "", last_seen: "2026-11-06T08:00:00Z" },
      ],
      managers: [{ name: "apt", version: "2.7.14" }],
      rules: [{ manager: "apt", package: "nginx", desired_state: "present", version_spec: "", enabled: true }],
      drift: [],
    },
  },
  "/api/agents/agt_demo/alerts/disk_root/ack": { status: 200, body: { alert: agent.alerts[0] } },
  "/api/agents/agt_demo/alerts/disk_root/snooze": { status: 200, body: { alert: agent.alerts[0] } },
  "/api/agents/agt_demo/alerts/disk_root": { status: 204, body: null },
  "/api/agents/agt_demo/alerts/memory": { status: 204, body: null },
  "/api/agents/agt_demo/history": {
    status: 200,
    body: {
      agent_id: "agt_demo",
      hours: 24,
      points: [
        { at: "2026-11-06T07:00:00Z", cpu_pct: 10, mem_pct: 40, disk_max_pct: 85, gpu_max_util: 20, gpu_max_temp: 60 },
        { at: "2026-11-06T07:30:00Z", cpu_pct: null, mem_pct: null, disk_max_pct: null, gpu_max_util: null, gpu_max_temp: null },
        { at: "2026-11-06T08:00:00Z", cpu_pct: 18, mem_pct: 44, disk_max_pct: 85, gpu_max_util: 97, gpu_max_temp: 78 },
      ],
    },
  },
  "/api/agents/agt_demo/events": {
    status: 200,
    body: {
      agent_id: "agt_demo",
      hours: 24,
      events: [
        { id: 4, agent_id: "agt_demo", at: "2026-11-06T08:00:00Z", kind: "online", severity: "success", title: "Agent back online", detail: {} },
        { id: 3, agent_id: "agt_demo", at: "2026-11-06T07:57:00Z", kind: "alert", severity: "warning", title: "Alert raised: Disk C: is 85% full", detail: { action: "raised" } },
        { id: 2, agent_id: "agt_demo", at: "2026-11-06T07:55:00Z", kind: "service", severity: "critical", title: "Service Spooler stopped", detail: { service: "Spooler", from: "running", to: "stopped" } },
        { id: 1, agent_id: "agt_demo", at: "2026-11-06T07:50:00Z", kind: "package", severity: "info", title: "Package version changed: apt:nginx 1.25.0", detail: { action: "version_changed" } },
      ],
    },
  },
  "/api/agents/agt_demo": {
    status: 200,
    body: {
      agent,
      commands: historyCommands.slice(0, 2),
    },
  },
  "/api/profile-tags": { status: 200, body: { assignments: [] } },
  "/api/docker/hosts": { status: 200, body: { server_time: "2026-11-06T08:00:30Z", stale_seconds: 900, hosts: [dockerHost, dockerHost2] } },
  "/api/docker/hosts/agt_demo": { status: 200, body: dockerDetail },
  "/api/docker/stacks": { status: 200, body: { stacks: [composeStack] } },
  "/api/docker/stacks/stk_web": { status: 200, body: { stack: composeStack } },
  "/api/docker/deployments": { status: 200, body: { deployments: composeDeployments } },
  "/api/docker/deployments/dep_web": { status: 200, body: { deployment: composeDeployments[0] } },
  "/api/docker/settings": {
    status: 200,
    body: {
      compose_dir: "/var/lib/sextant/docker/compose",
      source: "database",
      editable: true,
      max_file_bytes: 262144,
      max_files: 16,
      max_total_bytes: 1048576,
      max_bundle_bytes: 4194304,
      deployment_retention_days: 90,
      counts: { stacks: 1, revisions: 1 },
    },
  },
  "/api/docker/hosts/agt_demo/cleanup": {
    status: 200,
    body: {
      server_time: "2026-11-06T08:00:30Z",
      host: dockerHost,
      can_prune: true,
      scopes: [
        { scope: "stopped_containers", label: "Stopped containers", count: 1, estimated_bytes: 0, estimated: true, candidates: [{ id: "b".repeat(64), name: "worker-1" }], warning: "Compose-managed stopped containers are excluded by default." },
        { scope: "dangling_images", label: "Dangling images", count: 1, estimated_bytes: 1024 * 1024, estimated: true, candidates: [], warning: "Untagged images only." },
        { scope: "unused_images", label: "Unused images", count: 0, estimated_bytes: 0, estimated: true, candidates: [], warning: "Destructive." },
        { scope: "unused_volumes", label: "Unused volumes", count: 1, estimated_bytes: 0, estimated: false, candidates: [], warning: "Destructive and irreversible." },
        { scope: "unused_networks", label: "Unused networks", count: 0, estimated_bytes: 0, estimated: true, candidates: [], warning: "Default networks excluded." },
        { scope: "build_cache", label: "Build cache", count: 0, estimated_bytes: 0, estimated: false, candidates: [], warning: "Reported after prune." },
      ],
    },
  },
  "/api/remote-install": { status: 200, body: { jobs: [] } },
  "/api/discovery/settings": { status: 200, body: discoverySettings },
  "/api/discovery/agents": {
    status: 200,
    body: { relay_enabled: true, agents: discoveryAgents, local_flag_required: true },
  },
  "/api/discovery/devices": {
    status: 200,
    body: { devices: discoveryDevices, total: discoveryDevices.length, limit: 50, offset: 0, summary: discoverySummary },
  },
  "/api/discovery/devices/dev_printer": {
    status: 200,
    body: {
      device: discoveryDevices[0],
      events: discoveryEvents,
      agent: null,
      wake_target: null,
      conflicts: discoveryConflicts,
      agent_candidates: [],
    },
  },
  "/api/discovery/scans": {
    status: 200,
    body: { scans: discoveryScans, total: discoveryScans.length, limit: 50, offset: 0, active_scan_id: discoveryActiveScan?.id || null },
  },
  "/api/discovery/conflicts": {
    status: 200,
    body: { conflicts: discoveryConflicts, conflict_device_ids: ["dev_printer"], total: 1 },
  },
  "/api/profiles": {
    status: 200,
    body: {
      profiles: [
        {
          id: "prof_workstation",
          slug: "workstation",
          name: "Workstation",
          description: "Relaxed",
          is_system: true,
          revision: 1,
          agent_count: 1,
          settings: {
            heartbeat_seconds: 60,
            modules: { telemetry: true, services: true, startup: true, gpu: true, packages: true, shell: true, power: true, service_control: true, package_management: true, self_update: true },
            collection: { service_inventory_seconds: 21600, package_inventory_seconds: 86400, package_full_seconds: 604800 },
            actions: { allow_shell: true, allow_power: true, allow_service: true, allow_packages: true },
            update: { allow_self_update: true, channel: "stable" },
            max_output_bytes: 200000,
          },
        },
        {
          id: "prof_server",
          slug: "server",
          name: "Server",
          description: "Tighter",
          is_system: true,
          revision: 1,
          agent_count: 0,
          settings: {
            heartbeat_seconds: 30,
            modules: { telemetry: true, services: true, startup: true, gpu: true, packages: true, shell: true, power: true, service_control: true, package_management: true, self_update: true },
            collection: { service_inventory_seconds: 3600, package_inventory_seconds: 21600, package_full_seconds: 604800 },
            actions: { allow_shell: true, allow_power: true, allow_service: true, allow_packages: true },
            update: { allow_self_update: true, channel: "stable" },
            max_output_bytes: 200000,
          },
        },
      ],
    },
  },
  "/api/users": {
    status: 200,
    body: {
      users: [
        {
          id: 2,
          username: "operator",
          display_name: "Operator One",
          email: "operator@example.test",
          role: "operator",
          source: "local",
          active: true,
          session_count: 2,
          last_login_at: "2026-11-06T08:00:00Z",
        },
        {
          id: 3,
          username: "jdoe",
          display_name: "Jane Doe",
          email: "jane.doe@example.test",
          role: "viewer",
          source: "ad",
          active: true,
          session_count: 0,
          last_login_at: null,
        },
      ],
    },
  },
  "/api/users/2/sessions": {
    status: 200,
    body: {
      user: { id: 2, username: "operator" },
      sessions: [
        { id: "ses_one", created_at: "2026-11-06T07:00:00Z", last_seen_at: "2026-11-06T08:00:00Z", expires_at: "2026-11-06T20:00:00Z", ip: "10.0.0.5", user_agent: "Firefox", current: false },
      ],
    },
  },
};

const savedViews = [];

window.fetch = async (path, options = {}) => {
  const url = String(path);
  const key = url.split("?")[0];
  const method = String(options.method || "GET").toUpperCase();
  let body = null;
  if (options.body) {
    try {
      body = JSON.parse(options.body);
    } catch (_error) {
      body = options.body;
    }
  }
  window.__requests = window.__requests || [];
  window.__requests.push({ path: key, url, method, body });
  let response;
  if (method === "POST" && key === "/api/agents/bulk") {
    const ids = Array.isArray(body?.agent_ids) ? body.agent_ids : [];
    const action = body?.action || "refresh";
    const status = action === "wake" ? "sent" : action === "profile_apply" ? "updated" : "queued";
    const results = ids.map((id) => ({
      agent_id: id,
      name: id === "agt_demo" ? "Studio PC" : id,
      status,
      message: `${action} applied`,
      ...(status === "queued" ? { command_id: "cmd_bulk" } : {}),
    }));
    response = {
      status: 200,
      body: {
        action,
        bulk_id: "blk_demo",
        results,
        summary: {
          total: ids.length,
          queued: status === "queued" ? ids.length : 0,
          sent: status === "sent" ? ids.length : 0,
          updated: status === "updated" ? ids.length : 0,
          failed: 0,
        },
      },
    };
  } else if (method === "GET" && key === "/api/saved-views") {
    response = { status: 200, body: { views: savedViews.map((view) => ({ ...view, filters: { ...view.filters } })) } };
  } else if (method === "POST" && key === "/api/saved-views") {
    const name = String(body?.name || "").trim();
    if (savedViews.some((view) => view.name.toLowerCase() === name.toLowerCase())) {
      response = { status: 409, body: { detail: "A saved view with that name already exists." } };
    } else {
      const view = {
        id: `sv_${savedViews.length + 1}`,
        name,
        filters: body?.filters || {},
        created_at: "2026-11-06T08:00:00Z",
        updated_at: "2026-11-06T08:00:00Z",
      };
      savedViews.push(view);
      response = { status: 200, body: { view } };
    }
  } else if (method === "DELETE" && key.startsWith("/api/saved-views/")) {
    const id = key.slice("/api/saved-views/".length);
    const index = savedViews.findIndex((view) => view.id === id);
    if (index < 0) {
      response = { status: 404, body: { detail: "Saved view not found." } };
    } else {
      savedViews.splice(index, 1);
      response = { status: 204, body: null };
    }
  } else if (method === "GET" && key === "/api/discovery/settings" && window.__discoveryDisabled) {
    response = {
      status: 200,
      body: {
        ...discoverySettings,
        enabled: false,
        disabled_reasons: ["SEXTANT_DISCOVERY_ENABLED is 0."],
      },
    };
  } else if (method === "GET" && key === "/api/discovery/agents" && (window.__discoveryRelayDisabled || window.__discoveryNoAgents)) {
    response = {
      status: 200,
      body: {
        relay_enabled: !window.__discoveryRelayDisabled,
        agents: window.__discoveryNoAgents ? [] : discoveryAgents,
        local_flag_required: true,
      },
    };
  } else if (method === "PATCH" && key === "/api/discovery/settings") {
    discoverySettings.acknowledged = Boolean(body?.acknowledged);
    discoverySettings.acknowledged_at = body?.acknowledged ? "2026-11-06T08:05:00Z" : null;
    response = { status: 200, body: discoverySettings };
  } else if (method === "GET" && key === "/api/discovery/devices") {
    const params = new URLSearchParams(url.split("?")[1] || "");
    let filtered = [...discoveryDevices];
    const state = params.get("state") || "";
    const type = params.get("type") || "";
    const segment = params.get("segment") || "";
    const vendor = params.get("vendor") || "";
    if (state) filtered = filtered.filter((device) => device.state === state);
    if (type) filtered = filtered.filter((device) => device.device_type === type);
    if (segment) filtered = filtered.filter((device) => device.segment === segment);
    if (vendor) filtered = filtered.filter((device) => device.vendor === vendor);
    if (params.get("no_agent") === "true") filtered = filtered.filter((device) => !device.agent_id);
    if (params.get("admin_ports") === "true") {
      filtered = filtered.filter((device) =>
        device.open_ports.some((port) => [22, 3389, 5900, 5985, 5986, 8006].includes(port)),
      );
    }
    if (params.get("exposure") === "true") {
      filtered = filtered.filter(
        (device) =>
          !device.agent_id &&
          device.open_ports.some((port) => [22, 3389, 5900, 5985, 5986, 8006].includes(port)),
      );
    }
    if (params.get("conflict") === "true") {
      filtered = filtered.filter((device) => device.identity_conflict);
    }
    response = {
      status: 200,
      body: { devices: filtered, total: filtered.length, limit: 50, offset: 0, summary: discoverySummary },
    };
  } else if (method === "GET" && key.startsWith("/api/discovery/devices/")) {
    const id = key.slice("/api/discovery/devices/".length);
    const device = discoveryDevices.find((item) => item.id === id);
    const events = id === "dev_printer" ? discoveryEvents : [];
    const conflicts = id === "dev_printer" ? discoveryConflicts : [];
    response = device
      ? {
          status: 200,
          body: {
            device,
            events,
            agent: device.agent_id ? agent : null,
            wake_target: null,
            conflicts,
            agent_candidates: [],
          },
        }
      : { status: 404, body: { detail: "Discovered device not found." } };
  } else if (method === "PATCH" && key.startsWith("/api/discovery/devices/")) {
    const id = key.slice("/api/discovery/devices/".length);
    const index = discoveryDevices.findIndex((item) => item.id === id);
    if (index < 0) {
      response = { status: 404, body: { detail: "Discovered device not found." } };
    } else {
      const current = discoveryDevices[index];
      const next = {
        ...current,
        ...(body?.tags !== undefined ? { tags: body.tags } : {}),
        ...(body?.notes !== undefined ? { notes: body.notes } : {}),
        ...(body?.state !== undefined ? { state: body.state } : {}),
      };
      discoveryDevices[index] = next;
      if (id === "dev_printer") {
        discoveryEvents.unshift({
          id: discoveryEvents.length + 3,
          at: "2026-11-06T08:05:00Z",
          device_id: id,
          kind: next.state === "ignored" ? "promoted" : "new_device",
          detail: { state: next.state },
        });
      }
      response = { status: 200, body: { device: next, events: id === "dev_printer" ? discoveryEvents : [] } };
    }
  } else if (method === "DELETE" && key.startsWith("/api/discovery/devices/")) {
    const id = key.slice("/api/discovery/devices/".length);
    const index = discoveryDevices.findIndex((item) => item.id === id);
    if (index >= 0) discoveryDevices.splice(index, 1);
    response = { status: 204, body: null };
  } else if (method === "GET" && key === "/api/discovery/scans") {
    response = {
      status: 200,
      body: {
        scans: discoveryActiveScan ? [discoveryActiveScan, ...discoveryScans] : discoveryScans,
        total: discoveryActiveScan ? discoveryScans.length + 1 : discoveryScans.length,
        limit: 50,
        offset: 0,
        active_scan_id: discoveryActiveScan?.status === "running" ? discoveryActiveScan.id : null,
      },
    };
  } else if (method === "POST" && /^\/api\/discovery\/devices\/[^/]+\/(promote|wake-target|link-agent|verify)$/.test(key)) {
    const id = key.split("/")[4];
    const action = key.split("/")[5];
    const device = discoveryDevices.find((item) => item.id === id);
    if (!device) {
      response = { status: 404, body: { detail: "Discovered device not found." } };
    } else if (action === "verify") {
      device.wake_status = "woke";
      device.wake_verified_at = "2026-11-06T08:06:00Z";
      response = {
        status: 200,
        body: {
          device,
          verification: { reachable: true, method: "tcp", status: "woke", open_ports: [22], checked_at: "2026-11-06T08:06:00Z" },
        },
      };
    } else if (action === "wake-target" || body?.target === "wake_target") {
      device.state = "promoted";
      device.wake_target_id = "wol_dev";
      response = {
        status: 200,
        body: {
          device,
          wake_target: {
            id: "wol_dev",
            name: body?.name || device.hostname,
            mac: device.mac,
            ip: device.ip,
            broadcast: body?.broadcast || "255.255.255.255",
            port: body?.port || 9,
            created_at: "2026-11-06T08:06:00Z",
            last_woken_at: null,
          },
          duplicate: false,
        },
      };
    } else if (action === "link-agent") {
      device.state = "promoted";
      device.agent_id = body?.agent_id || "agt_demo";
      response = { status: 200, body: { device, agent } };
    } else {
      const osName = body?.target === "windows_agent" ? "windows" : "linux";
      device.state = "promoted";
      const command =
        osName === "windows"
          ? "# Run in an elevated PowerShell on 192.168.1.40.\\n& 'C:\\ProgramData\\SextantAgent\\current\\Scripts\\sextant-agent.exe' enroll --server 'https://ops.example.test' --token 'sextant_enroll_TOPSECRET' --name 'printer.lan'"
          : "# Run on 192.168.1.40 as root.\\nsudo /opt/sextant-agent/.venv/bin/sextant-agent enroll --server 'https://ops.example.test' --token 'sextant_enroll_TOPSECRET' --name 'printer.lan'";
      response = {
        status: 200,
        body: {
          device,
          enrollment: {
            id: "enr_promote",
            token: "sextant_enroll_TOPSECRET",
            label: `discovery:${id}`,
            os: osName,
            name: body?.name || device.hostname,
            address: device.ip,
            expires_at: "2026-11-06T08:36:00Z",
            install_command: command,
            prerequisites:
              osName === "windows"
                ? ["Run in an elevated PowerShell on the target host.", "Manual Windows install only; remote Windows install is not implemented."]
                : ["Run as root on the target host."],
            windows_plan:
              osName === "windows"
                ? "https://github.com/JugglerMaster/Sextant/blob/main/docs/WINDOWS_REMOTE_INSTALL.md"
                : "",
          },
        },
      };
    }
  } else if (method === "POST" && /^\/api\/agents\/[^/]+\/discover$/.test(key)) {
    const agentId = key.split("/")[3];
    const scan = {
      id: "dsc_agent",
      started_at: "2026-11-06T08:05:00Z",
      finished_at: null,
      scope: body?.scope || ["192.168.1.0/24"],
      methods: body?.methods || ["neigh"],
      initiated_by: "admin",
      agent_id: agentId,
      source: `agent:${agentId}`,
      command_id: "cmd_discovery",
      status: "queued",
      hosts_found: 0,
      error: "",
      progress: 0,
      hosts_scanned: 0,
      cancel_requested: false,
    };
    discoveryActiveScan = scan;
    response = {
      status: 200,
      body: {
        scan,
        command: { id: "cmd_discovery", kind: "discovery", args: { action: "discovery.scan", scan_id: "dsc_agent" }, status: "queued" },
      },
    };
  } else if (method === "POST" && key === "/api/discovery/scan") {
    const scan = {
      id: "dsc_live",
      started_at: "2026-11-06T08:05:00Z",
      finished_at: null,
      scope: body?.scope || ["192.168.1.0/24"],
      methods: body?.methods || ["neigh"],
      initiated_by: "admin",
      status: "running",
      hosts_found: 0,
      error: "",
      progress: 12,
      hosts_scanned: 30,
      cancel_requested: false,
    };
    discoveryActiveScan = scan;
    response = { status: 200, body: { scan } };
  } else if (method === "POST" && /^\/api\/discovery\/scans\/[^/]+\/cancel$/.test(key)) {
    if (discoveryActiveScan) {
      discoveryActiveScan = { ...discoveryActiveScan, status: "cancelled", finished_at: "2026-11-06T08:06:00Z" };
    }
    response = { status: 200, body: { scan: discoveryActiveScan } };
  } else if (method === "GET" && key === "/api/agents/agt_demo/commands") {
    const params = new URLSearchParams(url.split("?")[1] || "");
    const status = params.get("status") || "all";
    const query = (params.get("q") || "").toLowerCase();
    const limit = Number(params.get("limit") || 50);
    const offset = Number(params.get("offset") || 0);
    let filtered = historyCommands.filter((command) => status === "all" || command.status === status);
    if (query) filtered = filtered.filter((command) => JSON.stringify(command).toLowerCase().includes(query));
    response = {
      status: 200,
      body: { commands: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset },
    };
  } else if (method === "POST" && /^\/api\/agents\/[^/]+\/commands$/.test(key)) {
    response = { status: 200, body: { id: "cmd_bulk", agent_id: "agt_demo", kind: body?.kind || "refresh", args: body?.args || {}, status: "queued" } };
  } else if (method === "PATCH" && key === "/api/agents/agt_demo") {
    response = { status: 200, body: { agent: { ...agent, profile_id: body?.profile === "server" ? "prof_server" : agent.profile_id } } };
  } else if (method === "POST" && key === "/api/agents/agt_demo/wake") {
    response = { status: 200, body: { mac: agent.mac, sent: 3 } };
  } else if (method === "GET" && key === "/api/docker/hosts" && window.__dockerEmpty) {
    response = { status: 200, body: { server_time: "2026-11-06T08:00:30Z", stale_seconds: 900, hosts: [] } };
  } else if (method === "GET" && key === "/api/docker/stacks" && window.__composeEmpty) {
    response = { status: 200, body: { stacks: [] } };
  } else if (method === "GET" && key === "/api/docker/hosts/agt_demo" && window.__autoDockerRefresh) {
    dockerHost.inventory_revision += 1;
    dockerHost.age_seconds = 2;
    response = { status: 200, body: dockerDetail };
  } else if (method === "POST" && /^\/api\/docker\/hosts\/agt_demo\/containers\/[^/]+\/console$/.test(key)) {
    response = {
      status: 200,
      body: {
        session_id: "con_test",
        status: "opening",
        idle_seconds: 900,
        poll_seconds: 25,
        rows: 32,
        cols: 100,
        shell: body?.shell || "sh",
        user: body?.user || "",
        container_id: key.split("/")[5],
      },
    };
  } else if (method === "POST" && (key === "/api/docker/console/con_test/input" || key === "/api/docker/console/con_test/resize")) {
    response = { status: 204, body: null };
  } else if (method === "DELETE" && key === "/api/docker/console/con_test") {
    response = { status: 204, body: null };
  } else if (method === "POST" && key === "/api/docker/hosts/agt_demo/refresh") {
    dockerHost.inventory_at = "2026-11-06T08:01:00Z";
    dockerHost.collected_at = "2026-11-06T08:01:00Z";
    dockerHost.inventory_revision += 1;
    dockerHost.age_seconds = 5;
    dockerHost.stale = false;
    response = { status: 200, body: { id: "cmd_docker", agent_id: "agt_demo", kind: "docker", args: { action: "inventory.refresh" }, status: "queued", queued: true } };
  } else if (method === "POST" && (key === "/api/docker/hosts/agt_demo/actions" || key === "/api/docker/hosts/agt_demo/cleanup" || /^\/api\/docker\/hosts\/agt_demo\/containers\/[^/]+\/exec$/.test(key))) {
    const id = `cmd_docker_${++dockerCommandSeq}`;
    const action = key.endsWith("/cleanup") ? "cleanup" : key.endsWith("/exec") ? "container.exec_one_shot" : body?.action;
    let stdout = "ok";
    if (action === "container.inspect") stdout = JSON.stringify([{ Config: { Env: ["<masked>"], Image: "nginx:latest" } }], null, 2);
    else if (action === "container.logs") stdout = "log line one\nlog line two";
    else if (action === "container.exec_one_shot") stdout = "uid=0(root) gid=0(root)";
    else if (action === "cleanup") stdout = "Total reclaimed space: 1MB";
    dockerCommandResults[id] = {
      id,
      agent_id: "agt_demo",
      kind: "docker",
      args: body || {},
      status: "succeeded",
      created_at: "2026-11-06T08:01:00Z",
      completed_at: "2026-11-06T08:01:05Z",
      exit_code: 0,
      stdout,
      stderr: "",
      error: "",
      output_truncated: false,
    };
    response = { status: 200, body: dockerCommandResults[id] };
  } else if (method === "GET" && key.startsWith("/api/commands/cmd_docker_")) {
    const commandId = key.slice("/api/commands/".length);
    response = { status: 200, body: { command: dockerCommandResults[commandId] } };
  } else if (method === "POST" && key === "/api/docker/stacks/stk_web/deploy") {
    const deployment = {
      ...composeDeployments[0],
      id: `dep_${composeDeployments.length + 1}`,
      action: body?.action || "up",
      status: "queued",
      active: false,
      project_name: body?.project_name || "sextant_web",
      revision_hash: body?.revision_hash || composeRevision,
      created_at: "2026-11-06T08:05:00Z",
      started_at: null,
      finished_at: null,
    };
    composeDeployments.unshift(deployment);
    if (Array.isArray(body?.agent_ids)) {
      const results = body.agent_ids.map((agentId, index) => ({
        agent_id: agentId,
        agent_name: index === 0 ? "Studio PC" : "Second Docker",
        status: "queued",
        deployment,
        command: { id: `cmd_compose_${index}`, kind: "docker", args: { action: "compose.up" }, status: "queued", queued: true },
      }));
      response = {
        status: 200,
        body: { results, summary: { total: results.length, queued: results.length, failed: 0 } },
      };
    } else {
      response = {
        status: 200,
        body: {
          deployment,
          command: { id: "cmd_compose", kind: "docker", args: { action: `compose.${deployment.action}` }, status: "queued", queued: true },
        },
      };
    }
  } else if (method === "PUT" && key === "/api/docker/stacks/stk_web") {
    const next = {
      ...composeStack,
      name: body?.name || composeStack.name,
      description: body?.description || composeStack.description,
      contents: body?.files || composeStack.contents,
      files: Object.keys(body?.files || composeStack.contents).sort().map((name) => ({ name, size: String((body?.files || composeStack.contents)[name]).length })),
      current_revision_hash: "f".repeat(64),
      revision_count: (composeStack.revision_count || 1) + 1,
      revisions: [
        {
          id: "rev_web_2",
          stack_id: "stk_web",
          revision_hash: "f".repeat(64),
          message: body?.message || "UI edit",
          created_by: "admin",
          created_at: "2026-11-06T08:05:00Z",
          files: [{ name: "compose.yaml", size: 74 }],
        },
        ...(composeStack.revisions || []),
      ],
    };
    Object.assign(composeStack, next);
    responses["/api/docker/stacks/stk_web"] = { status: 200, body: { stack: composeStack } };
    responses["/api/docker/stacks"] = { status: 200, body: { stacks: [composeStack] } };
    response = { status: 200, body: { stack: composeStack } };
  } else if (method === "POST" && key === "/api/docker/stacks") {
    response = { status: 200, body: { stack: { ...composeStack, id: "stk_new", slug: body?.slug || "new", name: body?.name || "New stack" } } };
  } else if (method === "DELETE" && key === "/api/docker/stacks/stk_web") {
    response = { status: 204, body: null };
  } else {
    response = responses[key] || { status: 404, body: { detail: `No mock for ${key}` } };
  }
  return {
    status: response.status,
    ok: response.status >= 200 && response.status < 300,
    text: async () => JSON.stringify(response.body),
  };
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

(async () => {
  window.localStorage.setItem(
    "sextant.savedViews",
    JSON.stringify([{ name: "Migrated fleet", status: "online", profile: "", tag: "", search: "studio" }]),
  );
  window.eval(source);
  await sleep(30);

  const loginView = window.document.getElementById("login-view");
  if (loginView.hidden) throw new Error("login view should be visible without a session");
  if (!window.document.getElementById("app-view").hidden) throw new Error("app shell should start hidden");

  window.document.getElementById("login-username").value = "admin";
  window.document.getElementById("login-password").value = "password";
  window.document.getElementById("login-form").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }),
  );
  await sleep(140);

  if (window.document.getElementById("app-view").hidden) throw new Error("app shell should be visible after login");
  if (window.document.getElementById("stat-total").textContent !== "1") throw new Error("stats did not render");
  const migrationRequest = window.__requests.find(
    (request) => request.method === "POST" && request.path === "/api/saved-views" && request.body?.name === "Migrated fleet",
  );
  if (!migrationRequest) throw new Error("localStorage saved view was not migrated to the server");
  if (savedViews.filter((view) => view.name === "Migrated fleet").length !== 1) {
    throw new Error("localStorage saved view migration duplicated a name");
  }
  if (window.localStorage.getItem("sextant.savedViews")) {
    throw new Error("migrated localStorage saved views should be cleared");
  }
  const liveSource = window.__eventSources[0];
  if (!liveSource || liveSource.url !== "/api/events") {
    throw new Error("dashboard did not open the SSE stream");
  }
  const dashboardBody = responses["/api/dashboard"].body;
  liveSource.emit("dashboard", {
    ...dashboardBody,
    stats: { ...dashboardBody.stats, total: 2, offline: 1 },
  });
  await sleep(20);
  if (window.document.getElementById("stat-total").textContent !== "2") {
    throw new Error("SSE dashboard event did not update the stats");
  }
  liveSource.emit("dashboard", dashboardBody);
  await sleep(100);
  if (window.document.getElementById("stat-total").textContent !== "1") {
    throw new Error("SSE dashboard event did not restore the stats");
  }
  const keyedCard = window.document.querySelector("#agent-grid .agent-card");
  const keyedSelector = keyedCard.querySelector(".agent-select");
  keyedSelector.checked = false;
  keyedSelector.click();
  keyedSelector.focus();
  liveSource.emit("dashboard", dashboardBody);
  await sleep(30);
  if (window.document.querySelector("#agent-grid .agent-card") !== keyedCard) {
    throw new Error("keyed rendering rebuilt an unchanged agent card");
  }
  if (!keyedSelector.checked) throw new Error("keyed rendering lost the selected checkbox state");
  if (window.document.activeElement !== keyedSelector) throw new Error("keyed rendering lost keyboard focus");
  liveSource.emit("dashboard", {
    ...dashboardBody,
    agents: [{ ...agent, cpu_pct: 55.0 }],
  });
  await sleep(30);
  const updatedCard = window.document.querySelector("#agent-grid .agent-card");
  const updatedSelector = updatedCard.querySelector(".agent-select");
  if (!updatedSelector.checked) throw new Error("updated agent card lost the selected checkbox state");
  if (window.document.activeElement !== updatedSelector) throw new Error("updated agent card lost keyboard focus");
  updatedSelector.click();
  await sleep(10);
  liveSource.emit("dashboard", dashboardBody);
  await sleep(30);
  const fleetStatus = window.document.getElementById("fleet-status");
  if (!fleetStatus || fleetStatus.getAttribute("aria-live") !== "polite" || fleetStatus.getAttribute("role") !== "status") {
    throw new Error("fleet status live region missing");
  }
  if (!fleetStatus.textContent.includes("1 machine")) throw new Error("fleet status was not announced");
  const updateBanner = window.document.getElementById("update-banner");
  if (updateBanner.hidden) throw new Error("update banner should be visible for an available release");
  if (!updateBanner.textContent.includes("9.9.9")) throw new Error("update banner missing version");
  const alertCenter = window.document.getElementById("alert-center");
  if (alertCenter.hidden) throw new Error("alert center should be visible");
  if (!alertCenter.textContent.includes("Disk C:")) throw new Error("alert center missing alert");
  if (!alertCenter.textContent.includes("First seen")) throw new Error("alert center missing first-seen timing");
  if (!alertCenter.textContent.includes("Last seen")) throw new Error("alert center missing last-seen timing");
  if (!alertCenter.textContent.includes("Incident age")) throw new Error("alert center missing incident age");
  if (!alertCenter.textContent.includes("Acknowledged · condition still active")) {
    throw new Error("alert center should mark an acknowledged alert as still active");
  }
  if (!findButton(alertCenter, "Acknowledge")) throw new Error("alert acknowledge action missing");
  if (!findButton(alertCenter, "Snooze")) throw new Error("alert snooze action missing");
  if (!findButton(alertCenter, "Clear")) throw new Error("alert clear action missing");
  const alertPrompt = window.prompt;
  window.prompt = () => "60";
  findButton(alertCenter, "Snooze").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  window.prompt = alertPrompt;
  if (!window.document.getElementById("toast-region").textContent.includes("snoozed for 60 minutes")) {
    throw new Error("alert snooze action did not run");
  }
  findButton(alertCenter, "Acknowledge").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  findButton(alertCenter, "Clear").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  if (!window.document.getElementById("toast-region").textContent.includes("Alert cleared")) {
    throw new Error("alert clear action did not run");
  }
  const recurredMemory = {
    ...agent.alerts[1],
    incident_id: "inc_memory_2",
    first_seen_at: "2026-11-06T08:05:00Z",
    last_seen_at: "2026-11-06T08:06:00Z",
    acknowledged: false,
    acknowledged_at: null,
  };
  liveSource.emit("dashboard", {
    ...dashboardBody,
    agents: [{ ...agent, alerts: [agent.alerts[0], recurredMemory] }],
  });
  await sleep(30);
  const recurredRow = alertCenter.querySelector('[data-alert-key="memory"]');
  if (!recurredRow) throw new Error("recurring alert did not render");
  if (recurredRow.dataset.incidentId !== "inc_memory_2") {
    throw new Error("recurring alert did not use its new incident id");
  }
  if (recurredRow.textContent.includes("Acknowledged")) {
    throw new Error("recurring alert should not stay acknowledged");
  }
  if (!findButton(recurredRow, "Acknowledge")) {
    throw new Error("recurring alert should be active again");
  }
  liveSource.emit("dashboard", dashboardBody);
  await sleep(30);
  const bulkBar = window.document.getElementById("bulk-actions");
  if (bulkBar.hidden) throw new Error("bulk actions should be visible for operators");
  const rebootButton = window.document.getElementById("bulk-reboot");
  const shutdownButton = window.document.getElementById("bulk-shutdown");
  if (!rebootButton || !shutdownButton) throw new Error("bulk power controls missing");
  if (!rebootButton.disabled) throw new Error("bulk actions should be disabled without a selection");
  const selector = window.document.querySelector(".agent-select");
  if (!selector) throw new Error("agent selection checkbox missing");
  selector.checked = false;
  selector.click();
  await sleep(10);
  if (window.document.getElementById("bulk-count").textContent !== "1 selected") {
    throw new Error("bulk selection count is wrong");
  }
  if (rebootButton.disabled) throw new Error("bulk actions should enable with a selection");

  const closeConfirm = async (expected) => {
    const dialog = window.document.getElementById("confirm-dialog");
    if (!dialog.open) throw new Error(`confirmation dialog did not open for ${expected}`);
    if (!dialog.textContent.includes(expected)) throw new Error(`confirmation text missing ${expected}`);
    dialog.returnValue = "confirm";
    dialog.close("confirm");
    await sleep(60);
  };

  rebootButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await closeConfirm("Reboot 1 machine?");
  const powerRequest = window.__requests.find(
    (request) => request.method === "POST" && request.path === "/api/agents/bulk" && request.body?.action === "reboot",
  );
  if (!powerRequest || !powerRequest.body.agent_ids.includes("agt_demo")) {
    throw new Error("bulk reboot was not sent through the batch endpoint");
  }
  if (
    window.__requests.some(
      (request) => request.method === "POST" && request.path === "/api/agents/agt_demo/commands" && request.body?.kind === "power",
    )
  ) {
    throw new Error("bulk reboot still made a per-agent request");
  }
  const bootResult = window.document.getElementById("bulk-result");
  if (!bootResult.textContent.includes("Reboot: 1 machine") || !bootResult.textContent.includes("1 queued")) {
    throw new Error(`bulk reboot result summary is missing: ${bootResult.textContent}`);
  }
  if (window.document.getElementById("bulk-count").textContent !== "1 selected") {
    throw new Error("bulk selection should be preserved after a power action");
  }
  if (rebootButton.disabled) throw new Error("bulk actions should stay enabled after a power action");

  const serviceName = window.document.getElementById("bulk-service-name");
  const serviceRestart = window.document.getElementById("bulk-service-restart");
  if (!serviceName || !serviceRestart) throw new Error("bulk service controls missing");
  if (!serviceRestart.disabled) throw new Error("bulk service action should require a service name");
  serviceName.value = "Spooler";
  serviceName.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  if (serviceRestart.disabled) throw new Error("bulk service action should enable with a name");
  serviceRestart.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await closeConfirm("Restart Spooler on 1 machine?");
  const serviceRequest = window.__requests.find(
    (request) => request.method === "POST" && request.path === "/api/agents/bulk" && request.body?.action === "service_restart",
  );
  if (!serviceRequest || serviceRequest.body.args.name !== "Spooler") {
    throw new Error("bulk service was not sent through the batch endpoint");
  }
  if (!window.document.getElementById("bulk-result").textContent.includes("Restart Spooler: 1 machine")) {
    throw new Error("bulk service result summary was not reported");
  }

  const bulkProfile = window.document.getElementById("bulk-profile");
  const applyProfile = window.document.getElementById("bulk-profile-apply");
  if (!bulkProfile || !applyProfile) throw new Error("bulk profile controls missing");
  if (!bulkProfile.textContent.includes("Server")) throw new Error("bulk profile options missing");
  bulkProfile.value = "server";
  bulkProfile.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(10);
  if (applyProfile.disabled) throw new Error("bulk profile apply should enable with a profile");
  applyProfile.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  const profileRequest = window.__requests.find(
    (request) => request.method === "POST" && request.path === "/api/agents/bulk" && request.body?.action === "profile_apply",
  );
  if (!profileRequest || profileRequest.body.args?.profile !== "server") {
    throw new Error("bulk profile apply was not sent through the batch endpoint");
  }
  if (
    window.__requests.some((request) => request.method === "PATCH" && request.path === "/api/agents/agt_demo")
  ) {
    throw new Error("bulk profile apply still patched agents one at a time");
  }
  const profileResult = window.document.getElementById("bulk-result");
  if (!profileResult.textContent.includes("Profile Server: 1 machine") || !profileResult.textContent.includes("1 updated")) {
    throw new Error(`bulk profile result summary was not reported: ${profileResult.textContent}`);
  }
  if (window.document.getElementById("bulk-count").textContent !== "1 selected") {
    throw new Error("bulk selection should be preserved after profile apply");
  }

  window.renderBulkResult("Probe", {
    summary: { total: 2, queued: 1, sent: 0, updated: 0, failed: 1 },
    results: [
      { agent_id: "agt_demo", name: "Studio PC", status: "queued", message: "ok" },
      { agent_id: "agt_bad", name: "Broken PC", status: "failed", message: "Agent is offline" },
    ],
  });
  const failureResult = window.document.getElementById("bulk-result");
  if (!failureResult.textContent.includes("1 failed") || !failureResult.textContent.includes("Broken PC: Agent is offline")) {
    throw new Error(`per-agent failure summary is missing: ${failureResult.textContent}`);
  }

  window.document.getElementById("bulk-clear").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);

  const savedViewSelect = window.document.getElementById("saved-view-select");
  const saveViewButton = window.document.getElementById("save-view");
  const deleteViewButton = window.document.getElementById("delete-view");
  if (!savedViewSelect || !saveViewButton || !deleteViewButton) throw new Error("saved view controls missing");
  if (savedViewSelect.value !== "" || !savedViewSelect.textContent.includes("No saved view")) {
    throw new Error("saved view default option missing");
  }
  const statusFilter = window.document.getElementById("filter-status");
  const searchInput = window.document.getElementById("search-input");
  statusFilter.value = "online";
  statusFilter.dispatchEvent(new window.Event("change", { bubbles: true }));
  searchInput.value = "studio";
  searchInput.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  const originalPrompt = window.prompt;
  window.prompt = () => "GPU hosts";
  saveViewButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  window.prompt = originalPrompt;
  const saveRequest = [...window.__requests]
    .reverse()
    .find((request) => request.method === "POST" && request.path === "/api/saved-views" && request.body?.name === "GPU hosts");
  if (!saveRequest) throw new Error("saved view was not created through the API");
  const savedFilters = saveRequest.body.filters || {};
  if (
    savedFilters.status !== "online" ||
    savedFilters.profile !== "" ||
    savedFilters.tag !== "" ||
    savedFilters.search !== "studio"
  ) {
    throw new Error(`saved view filters are wrong: ${JSON.stringify(savedFilters)}`);
  }
  if (savedViews.filter((view) => view.name === "GPU hosts").length !== 1) {
    throw new Error("saved view was not stored on the server");
  }
  if (window.localStorage.getItem("sextant.savedViews")) {
    throw new Error("server-backed saved views should not keep a localStorage copy");
  }
  if (!savedViewSelect.textContent.includes("GPU hosts")) throw new Error("saved view option missing");
  if (savedViewSelect.value !== "GPU hosts") throw new Error("saved view was not selected after save");
  if (deleteViewButton.disabled) throw new Error("delete view should be enabled for a selection");
  statusFilter.value = "offline";
  statusFilter.dispatchEvent(new window.Event("change", { bubbles: true }));
  searchInput.value = "";
  searchInput.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  savedViewSelect.value = "GPU hosts";
  savedViewSelect.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(10);
  if (statusFilter.value !== "online") throw new Error("saved view did not restore status");
  if (searchInput.value !== "studio") throw new Error("saved view did not restore search");
  if (window.document.querySelectorAll("#agent-grid .agent-card").length !== 1) {
    throw new Error("saved view did not restore filtered cards");
  }
  deleteViewButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  const deleteRequest = window.__requests.find(
    (request) => request.method === "DELETE" && request.path.startsWith("/api/saved-views/"),
  );
  if (!deleteRequest) throw new Error("saved view was not deleted through the API");
  if (savedViews.some((view) => view.name === "GPU hosts")) {
    throw new Error("saved view was not deleted from the server");
  }
  if (savedViewSelect.value !== "") throw new Error("saved view selection was not cleared");
  if (!deleteViewButton.disabled) throw new Error("delete view should be disabled without a selection");
  window.document.getElementById("filter-clear").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);

  const profilesNav = window.document.querySelector('[data-page="profiles"]');
  profilesNav.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!window.document.getElementById("profile-list").textContent.includes("Workstation")) {
    throw new Error("profile list did not render");
  }
  const remoteNav = window.document.querySelector('[data-page="remote-install"]');
  remoteNav.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!window.document.getElementById("remote-install-form").textContent.includes("Install agent")) {
    throw new Error("remote install form did not render");
  }
  window.document.querySelector('[data-page="overview"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  const cards = window.document.querySelectorAll("#agent-grid .agent-card");
  if (cards.length !== 1) throw new Error(`expected one agent card, got ${cards.length}`);
  if (!cards[0].textContent.includes("Studio PC")) throw new Error("agent name missing from card");
  if (!cards[0].textContent.includes("agent v0.3.0")) throw new Error("agent version missing from card");

  cards[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  if (window.document.getElementById("agent-drawer").hidden) throw new Error("drawer did not open");
  if (!window.document.getElementById("drawer-title").textContent.includes("Studio PC")) {
    throw new Error("drawer title missing");
  }
  if (!findButton(window.document.getElementById("drawer-body"), "Acknowledge")) {
    throw new Error("drawer alert acknowledge action missing");
  }
  if (!findButton(window.document.getElementById("drawer-body"), "Snooze")) {
    throw new Error("drawer alert snooze action missing");
  }
  if (!findButton(window.document.getElementById("drawer-body"), "Clear")) {
    throw new Error("drawer alert clear action missing");
  }

  const serviceTab = window.document.querySelector('[data-tab="services"]');
  serviceTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  if (!window.document.getElementById("drawer-body").textContent.includes("Spooler")) {
    throw new Error("service list did not render");
  }
  const serviceCount = window.document.getElementById("service-filter-count");
  if (!serviceCount || !serviceCount.textContent.includes("55 of 55 services")) {
    throw new Error(`service count is wrong: ${serviceCount ? serviceCount.textContent : "missing"}`);
  }
  if (window.document.querySelectorAll("#service-filter-list .service-row").length !== 50) {
    throw new Error("service list should be bounded to 50 rows");
  }
  const serviceMore = findButton(window.document.getElementById("drawer-body"), "Show more");
  if (!serviceMore) throw new Error("service show more control missing");
  serviceMore.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  if (window.document.querySelectorAll("#service-filter-list .service-row").length !== 55) {
    throw new Error("service show more did not reveal remaining rows");
  }
  const serviceStatus = window.document.getElementById("service-filter-status");
  const serviceSearch = window.document.getElementById("service-filter-search");
  serviceStatus.value = "stopped";
  serviceStatus.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(10);
  if (!serviceCount.textContent.includes("27 of 55 services")) throw new Error("stopped service count is wrong");
  const serviceListText = window.document.getElementById("service-filter-list").textContent;
  if (!serviceListText.includes("wuauserv") || serviceListText.includes("Spooler")) {
    throw new Error("stopped service filter is wrong");
  }
  serviceStatus.value = "watched";
  serviceStatus.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(10);
  if (!serviceCount.textContent.includes("2 of 55 services")) throw new Error("watched service count is wrong");
  if (!window.document.getElementById("service-filter-list").textContent.includes("Spooler")) {
    throw new Error("watched service filter missed Spooler");
  }
  serviceStatus.value = "all";
  serviceStatus.dispatchEvent(new window.Event("change", { bubbles: true }));
  serviceSearch.value = "spooler";
  serviceSearch.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  if (!serviceCount.textContent.includes("1 of 55 services")) throw new Error("service search count is wrong");
  if (!window.document.getElementById("service-filter-list").textContent.includes("Spooler")) {
    throw new Error("service search lost Spooler");
  }
  window.document.querySelector('[data-tab="gpu"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  serviceTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  if (window.document.getElementById("service-filter-search").value !== "spooler") {
    throw new Error("per-agent service filter state was not preserved");
  }

  const gpuTab = window.document.querySelector('[data-tab="gpu"]');
  gpuTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  if (!window.document.getElementById("drawer-body").textContent.includes("python.exe")) {
    throw new Error("GPU process list did not render");
  }

  const packagesTab = window.document.querySelector('[data-tab="packages"]');
  packagesTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!window.document.getElementById("drawer-body").textContent.includes("nginx")) {
    throw new Error("package inventory did not render");
  }
  if (!window.document.getElementById("drawer-body").textContent.includes("Add rule")) {
    throw new Error("package rule editor did not render");
  }
  if (!window.document.getElementById("drawer-body").textContent.includes("present")) {
    throw new Error("package rule did not render");
  }

  const remoteTab = window.document.querySelector('[data-tab="remote"]');
  remoteTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  if (!window.document.querySelector(".terminal")) throw new Error("remote terminal did not render");

  const remoteForm = window.document.querySelector(".remote-form");
  const remoteInput = window.document.querySelector(".remote-controls textarea");
  remoteInput.value = "echo live";
  remoteForm.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await sleep(30);
  const commandSource = [...window.__eventSources]
    .reverse()
    .find((source) => source.url.includes("/api/commands/cmd_bulk/events"));
  if (!commandSource) throw new Error("command output SSE stream did not open");
  commandSource.emit("command", {
    command: { id: "cmd_bulk", agent_id: "agt_demo", kind: "shell", args: {}, status: "dispatched", stdout: "", stderr: "", error: "" },
  });
  commandSource.emit("output", { stream: "stdout", seq: 1, text: "alpha\n" });
  commandSource.emit("output", { stream: "stderr", seq: 1, text: "beta\n" });
  await sleep(20);
  let terminalOutput = window.document.querySelector("[data-terminal-output]");
  if (!terminalOutput.querySelector(".terminal-line-out")?.textContent.includes("alpha")) {
    throw new Error("live stdout chunk was not appended");
  }
  if (!terminalOutput.querySelector(".terminal-line-err")?.textContent.includes("beta")) {
    throw new Error("live stderr chunk was not appended");
  }
  commandSource.emit("command", {
    command: {
      id: "cmd_bulk",
      agent_id: "agt_demo",
      kind: "shell",
      args: {},
      status: "succeeded",
      exit_code: 0,
      stdout: "alpha\ngamma\n",
      stderr: "beta\n",
      error: "",
    },
  });
  await sleep(30);
  terminalOutput = window.document.querySelector("[data-terminal-output]");
  const terminalText = terminalOutput.textContent;
  if ((terminalText.match(/alpha/g) || []).length !== 1) throw new Error("streamed stdout was duplicated");
  if ((terminalText.match(/beta/g) || []).length !== 1) throw new Error("streamed stderr was duplicated");
  if (!terminalText.includes("gamma")) throw new Error("final canonical stdout remainder was not rendered");
  if (!terminalText.includes("succeeded")) throw new Error("terminal command status was not rendered");
  if (!commandSource.closed) throw new Error("command SSE stream stayed open after the terminal event");

  const historyTab = window.document.querySelector('[data-tab="history"]');
  historyTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!findButton(window.document.getElementById("drawer-body"), "Cancel")) throw new Error("queued command cancel action missing");
  if (!findButton(window.document.getElementById("drawer-body"), "Retry")) throw new Error("failed command retry action missing");

  const historyBody = window.document.getElementById("drawer-body");
  const historyCount = historyBody.querySelector("[data-history-count]");
  const historySearch = historyBody.querySelector('input[aria-label="Search command history"]');
  const historyStatus = historyBody.querySelector('select[aria-label="Filter command status"]');
  const historyPageSize = historyBody.querySelector('select[aria-label="Commands per page"]');
  const historyExport = historyBody.querySelector("[data-history-export]");
  if (!historyCount || !historySearch || !historyStatus || !historyPageSize) {
    throw new Error("command history controls missing");
  }
  if (!historyCount.textContent.includes("1-30 of 30 commands")) {
    throw new Error(`initial history count is wrong: ${historyCount.textContent}`);
  }
  if (!historyExport || !historyExport.href.includes("agent_id=agt_demo")) {
    throw new Error("command history export link missing agent filter");
  }
  historyPageSize.value = "25";
  historyPageSize.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(30);
  if (!historyCount.textContent.includes("1-25 of 30 commands")) {
    throw new Error(`page size did not apply: ${historyCount.textContent}`);
  }
  findButton(historyBody, "Next").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!historyCount.textContent.includes("26-30 of 30 commands")) {
    throw new Error(`next page did not load: ${historyCount.textContent}`);
  }
  findButton(historyBody, "Previous").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!historyCount.textContent.includes("1-25 of 30 commands")) {
    throw new Error(`previous page did not load: ${historyCount.textContent}`);
  }
  historySearch.value = "alpha";
  historySearch.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(360);
  if (!historyCount.textContent.includes("1-1 of 1 commands")) {
    throw new Error(`search did not filter history: ${historyCount.textContent}`);
  }
  const searchRequest = [...window.__requests]
    .reverse()
    .find((request) => request.method === "GET" && request.path === "/api/agents/agt_demo/commands");
  if (!searchRequest || !searchRequest.url.includes("q=alpha")) throw new Error("history search query was not sent");
  if (!historyExport.href.includes("q=alpha")) throw new Error("export link did not carry the search query");
  historySearch.value = "";
  historySearch.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(360);
  historyStatus.value = "failed";
  historyStatus.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(30);
  if (!historyCount.textContent.includes("1-1 of 1 commands")) {
    throw new Error(`status filter did not apply: ${historyCount.textContent}`);
  }
  if (!historyExport.href.includes("status=failed")) throw new Error("export link did not carry the status filter");
  historyStatus.value = "all";
  historyStatus.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(30);

  const drawerTabs = [...window.document.querySelectorAll("#drawer-tabs .tab")].map((tab) => tab.dataset.tab);
  const historyIndex = drawerTabs.indexOf("history");
  const trendsIndex = drawerTabs.indexOf("trends");
  if (trendsIndex === -1) throw new Error("trends tab missing");
  if (trendsIndex !== historyIndex + 1) throw new Error("trends tab should follow history");
  const trendsTab = window.document.querySelector('[data-tab="trends"]');
  trendsTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const trendsBody = window.document.getElementById("drawer-body");
  const trendCharts = trendsBody.querySelectorAll(".chart-card");
  if (trendCharts.length !== 5) throw new Error(`expected five trend charts, got ${trendCharts.length}`);
  if (!trendsBody.querySelector(".detail-chart")) throw new Error("detail chart SVG missing");
  if (!trendsBody.textContent.includes("GPU temperature")) throw new Error("trend chart labels missing");
  const cpuChart = trendsBody.querySelector('.chart-card[data-series="cpu_pct"]');
  if (!cpuChart) throw new Error("CPU trend chart missing");
  if (cpuChart.querySelectorAll(".detail-chart polyline").length < 2) {
    throw new Error("gap bucket did not break the trend line");
  }
  if (cpuChart.querySelectorAll(".chart-point").length !== 2) {
    throw new Error("null buckets should not render chart points");
  }
  const whatChanged = trendsBody.querySelector(".what-changed");
  if (!whatChanged) throw new Error("What changed section missing");
  if (!whatChanged.textContent.includes("Agent back online") || !whatChanged.textContent.includes("Package version changed")) {
    throw new Error(`What changed summary missing recent events: ${whatChanged.textContent}`);
  }
  if (whatChanged.textContent.includes("No state changes recorded")) {
    throw new Error("What changed should not show its empty state with events");
  }
  if (!trendsBody.querySelector(".timeline")) throw new Error("timeline did not render");
  const timelineEntries = trendsBody.querySelectorAll(".timeline-entry");
  if (timelineEntries.length !== 6) {
    throw new Error(`expected 6 timeline entries, got ${timelineEntries.length}`);
  }
  if (!trendsBody.querySelector(".timeline-command") || !trendsBody.querySelector(".timeline-alert")) {
    throw new Error("timeline should merge commands and alert lifecycle events");
  }
  if (!trendsBody.querySelector(".timeline-service") || !trendsBody.querySelector(".timeline-package")) {
    throw new Error("timeline should include persistent state-change event types");
  }
  if (trendsBody.querySelectorAll(".timeline-event").length < 3) {
    throw new Error("timeline should mark persistent state-change entries");
  }
  if ([...timelineEntries].some((entry) => entry.querySelector(".timeline-time").textContent.includes("—"))) {
    throw new Error("timeline entries must use real event timestamps");
  }
  if (!trendsBody.textContent.includes("6 events")) throw new Error("timeline count missing");
  const sevenDay = findButton(trendsBody, "7d");
  if (!sevenDay) throw new Error("7d trend range toggle missing");
  sevenDay.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const activeRange = [...trendsBody.querySelectorAll(".range-option")].find((option) => option.classList.contains("is-active"));
  if (!activeRange || activeRange.textContent.trim() !== "7d") throw new Error("7d trend range did not activate");
  if (trendsBody.querySelectorAll(".chart-card").length !== 5) throw new Error("trend charts missing after 7d toggle");
  if (!trendsBody.querySelector(".what-changed") || !trendsBody.querySelector(".timeline")) {
    throw new Error("trend activity sections missing after 7d toggle");
  }

  const editButton = findButton(window.document.getElementById("drawer-actions"), "Edit policy");
  if (!editButton) throw new Error("agent policy edit action missing");
  editButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  if (!window.document.getElementById("agent-edit-dialog").open) throw new Error("agent edit dialog did not open");
  window.document.querySelector("[data-close-agent-edit]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  window.document.querySelector('[data-page="users"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  const userList = window.document.getElementById("user-list");
  if (!userList.textContent.includes("Operator One")) throw new Error("user list did not render");
  const userSearch = window.document.getElementById("user-search");
  const userCount = window.document.getElementById("user-count");
  if (!userSearch || !userCount) throw new Error("user search controls missing");
  if (!userCount.textContent.includes("2 of 2 users")) throw new Error("user count is wrong");
  userSearch.value = "jane.doe@example.test";
  userSearch.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  if (!userCount.textContent.includes("1 of 2 users")) throw new Error("filtered user count is wrong");
  if (!userList.textContent.includes("Jane Doe") || userList.textContent.includes("Operator One")) {
    throw new Error("user email search did not filter rows");
  }
  userSearch.value = "local";
  userSearch.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  if (!userCount.textContent.includes("1 of 2 users") || !userList.textContent.includes("Operator One")) {
    throw new Error("user source search did not filter rows");
  }
  userSearch.value = "";
  userSearch.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  if (!userCount.textContent.includes("2 of 2 users")) throw new Error("user search did not clear");
  if (!findButton(userList, "Disable")) throw new Error("disable user action missing");
  const sessionsButton = findButton(userList, "Sessions");
  if (!sessionsButton) throw new Error("sessions action missing");
  sessionsButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!window.document.getElementById("sessions-list").textContent.includes("10.0.0.5")) {
    throw new Error("sessions dialog did not render");
  }
  window.document.querySelector("[data-close-sessions]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  const resetButton = findButton(userList, "Reset password");
  if (!resetButton) throw new Error("reset password action missing");
  resetButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  if (!window.document.getElementById("reset-password-dialog").open) throw new Error("reset password dialog did not open");
  window.document.querySelector("[data-close-reset-password]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  window.document.querySelector('[data-page="docker-hosts"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  if (window.document.getElementById("page-docker-hosts").hidden) throw new Error("Docker Hosts page did not open");
  const dockerList = window.document.getElementById("docker-host-list");
  if (!dockerList.textContent.includes("Studio PC") || !dockerList.textContent.includes("27.5.1")) {
    throw new Error("Docker host list did not render");
  }
  if (!window.document.getElementById("docker-hosts-status").textContent.includes("Select one")) {
    throw new Error("Docker host status did not render");
  }
  const dockerOpen = findButton(dockerList, "Open");
  if (!dockerOpen) throw new Error("Docker host Open action missing");
  dockerOpen.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  if (window.document.getElementById("docker-host-detail").hidden) throw new Error("Docker host detail did not open");
  if (!window.document.getElementById("docker-host-title").textContent.includes("Studio PC")) {
    throw new Error("Docker host detail title is wrong");
  }
  await sleep(80);
  const cleanupCard = [...window.document.querySelectorAll(".docker-cleanup-card")].find((card) => card.textContent.includes("Dangling images"));
  if (!cleanupCard) throw new Error("Docker cleanup preview did not render");
  const cleanupReview = findButton(cleanupCard, "Review");
  if (!cleanupReview) throw new Error("Docker cleanup review action missing");
  cleanupReview.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await closeConfirm("Clean up dangling images?");
  await sleep(2600);
  if (!window.document.getElementById("docker-dialog").open) throw new Error("Docker cleanup result dialog did not open");
  if (!window.document.getElementById("docker-dialog-body").textContent.includes("Total reclaimed")) {
    throw new Error("Docker cleanup result output missing");
  }
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(600);

  window.document.querySelector('[data-docker-tab="containers"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  const dockerBody = window.document.getElementById("docker-host-body");
  if (!dockerBody.textContent.includes("web-1")) {
    throw new Error("Docker containers did not render");
  }
  const driftBadge = [...dockerBody.querySelectorAll(".status-pill.warning")].find((pill) => pill.textContent === "drift");
  if (!driftBadge) throw new Error("Docker drift badge did not render for a drifted Compose project");

  findButton(dockerBody, "Clone").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!window.document.getElementById("docker-dialog").open) throw new Error("Docker clone dialog did not open");
  const cloneDialog = window.document.getElementById("docker-dialog");
  if (!cloneDialog.textContent.includes("Host bind mounts") || !cloneDialog.textContent.includes("Compose-managed")) {
    throw new Error("Docker clone dialog is missing its safe-spec warning");
  }
  findButton(cloneDialog, "Clone").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!window.document.getElementById("docker-dialog-error").textContent.includes("Acknowledge Compose drift")) {
    throw new Error("Docker clone did not require Compose drift acknowledgment");
  }
  const cloneDriftAck = cloneDialog.querySelector('#docker-dialog-body .callout input[type="checkbox"]');
  if (!cloneDriftAck) throw new Error("Docker clone drift acknowledgment checkbox missing");
  cloneDriftAck.checked = true;
  findButton(cloneDialog, "Clone").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await closeConfirm("Clone web-1?");
  await sleep(1800);
  const cloneRequest = window.__requests.find(
    (request) => request.method === "POST" && request.path === "/api/docker/hosts/agt_demo/actions" && request.body?.action === "container.clone",
  );
  if (!cloneRequest) throw new Error("Docker clone action was not sent");
  if (cloneRequest.body.acknowledge_drift !== true) throw new Error("Docker clone did not send drift acknowledgment");
  if (cloneRequest.body.image_id !== "sha256:" + "c".repeat(64)) throw new Error("Docker clone image was not canonicalized");
  for (const forbidden of ["privileged", "devices", "cap_add", "env", "mounts", "binds", "flags"]) {
    if (forbidden in cloneRequest.body) throw new Error(`Docker clone accepted a forbidden field: ${forbidden}`);
  }
  if (!window.document.getElementById("docker-dialog").open) throw new Error("Docker clone result dialog did not open");
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(600);

  const inspectButton = findButton(dockerBody, "Inspect");
  if (!inspectButton) throw new Error(`Docker inspect action missing: ${dockerBody.textContent.slice(0, 200)}`);
  inspectButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!window.document.getElementById("docker-dialog").open || !window.document.getElementById("docker-dialog-eyebrow").textContent.includes("Container overview")) {
    throw new Error("Docker inspect dialog did not open");
  }
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  findButton(dockerBody, "Logs").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  findButton(window.document.getElementById("docker-dialog"), "Load logs").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(1800);
  if (!window.document.getElementById("docker-dialog-body").textContent.includes("log line one")) {
    throw new Error("Docker logs result missing");
  }
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(600);

  findButton(window.document.getElementById("docker-host-body"), "Exec").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  const execCommand = window.document.querySelector("#docker-dialog-body textarea");
  execCommand.value = "id";
  findButton(window.document.getElementById("docker-dialog"), "Run command").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(1800);
  if (!window.document.getElementById("docker-dialog-body").textContent.includes("uid=0")) {
    throw new Error("Docker exec result missing");
  }
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(600);

  findButton(window.document.getElementById("docker-host-body"), "Console").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!window.document.getElementById("docker-console-dialog").open) throw new Error("Docker console dialog did not open");
  if (!window.document.getElementById("docker-console-setup").hidden) {
    findButton(window.document.getElementById("docker-console-setup"), "Start console").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  }
  await sleep(60);
  if (window.document.getElementById("docker-console-live").hidden) throw new Error("Docker console did not start");
  const consoleSource = [...window.__eventSources].reverse().find((source) => source.url.includes("/api/docker/console/con_test/events"));
  if (!consoleSource) throw new Error("Docker console SSE stream missing");
  consoleSource.emit("status", { status: "open", idle_seconds: 900 });
  consoleSource.emit("output", { data: Buffer.from("hello from console\n").toString("base64") });
  await sleep(20);
  if (!window.document.getElementById("docker-console-output").textContent.includes("hello from console")) {
    throw new Error("Docker console output did not render");
  }
  const consoleInput = window.document.getElementById("docker-console-input");
  consoleInput.value = "ls";
  findButton(window.document.getElementById("docker-console-live"), "Send").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!window.__requests.some((request) => request.method === "POST" && request.path === "/api/docker/console/con_test/input")) {
    throw new Error("Docker console input was not sent");
  }
  window.document.getElementById("docker-console-close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await closeConfirm("Close the container console?");
  await sleep(30);
  if (window.document.getElementById("docker-console-dialog").open) throw new Error("Docker console dialog did not close");
  if (!window.__requests.some((request) => request.method === "DELETE" && request.path === "/api/docker/console/con_test")) {
    throw new Error("Docker console close was not sent");
  }

  findButton(window.document.getElementById("docker-host-body"), "Remove").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await closeConfirm("Remove web-1?");
  await sleep(1800);
  if (!window.document.getElementById("docker-dialog").open) throw new Error("Docker remove result dialog did not open");
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(600);
  dockerHost.online = false;
  dockerHost.capabilities = { mutate: false, exec: false, prune: false };
  window.document.getElementById("docker-refresh-list").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  findButton(dockerList, "Selected").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  window.document.querySelector('[data-docker-tab="containers"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  const offlineStop = findButton(window.document.getElementById("docker-host-body"), "Stop");
  const offlineConsole = findButton(window.document.getElementById("docker-host-body"), "Console");
  if (!offlineStop || !offlineStop.disabled || !offlineConsole || !offlineConsole.disabled) {
    throw new Error("Docker actions and console should disable while offline");
  }
  if (!window.document.getElementById("docker-host-notice").textContent.includes("offline")) {
    throw new Error("Docker offline notice missing");
  }
  dockerHost.online = true;
  dockerHost.capabilities = { mutate: true, exec: true, prune: true };
  window.document.getElementById("docker-refresh-list").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  findButton(dockerList, "Selected").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);

  window.__fastDockerRefresh = true;
  window.__autoDockerRefresh = true;
  const revisionBeforeRefresh = dockerHost.inventory_revision;
  findButton(dockerList, "Selected").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(700);
  if (dockerHost.inventory_revision <= revisionBeforeRefresh) {
    throw new Error("Docker auto-refresh did not reload the selected host");
  }
  if (!dockerList.textContent.includes(`rev ${dockerHost.inventory_revision}`)) {
    throw new Error("Docker auto-refresh did not update the inventory revision");
  }
  window.__fastDockerRefresh = false;
  window.__autoDockerRefresh = false;
  window.document.getElementById("docker-close-host").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  findButton(dockerList, "Open").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);

  window.document.querySelector('[data-docker-tab="images"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!window.document.getElementById("docker-host-body").textContent.includes("nginx:latest")) {
    throw new Error("Docker images did not render");
  }
  window.document.getElementById("docker-refresh-host").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(1500);
  if (!window.__requests.some((request) => request.method === "POST" && request.path === "/api/docker/hosts/agt_demo/refresh")) {
    throw new Error("Docker refresh was not queued");
  }
  if (!window.document.getElementById("docker-hosts-status").textContent.includes("updated")) {
    throw new Error("Docker refresh status did not update");
  }
  window.document.getElementById("docker-close-host").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!window.document.getElementById("docker-host-detail").hidden) throw new Error("Docker detail did not close");

  window.__dockerEmpty = true;
  window.document.getElementById("docker-refresh-list").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  if (window.document.getElementById("docker-hosts-empty").hidden) throw new Error("Docker empty state did not render");
  if (!window.document.getElementById("docker-hosts-empty-text").textContent.includes("Windows Docker is not supported")) {
    throw new Error("Docker empty state is missing platform guidance");
  }
  window.__dockerEmpty = false;
  window.document.getElementById("docker-refresh-list").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  if (!window.document.getElementById("docker-host-list").textContent.includes("Studio PC")) {
    throw new Error("Docker host list did not recover after empty state");
  }

  window.document.querySelector('[data-page="compose-files"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const composeList = window.document.getElementById("compose-stack-list");
  if (!composeList.textContent.includes("Web stack")) throw new Error("Compose stack list did not render");
  if (!window.document.getElementById("compose-settings").textContent.includes("/var/lib/sextant/docker/compose")) {
    throw new Error("Compose storage settings did not render");
  }
  findButton(composeList, "Open").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  if (window.document.getElementById("compose-stack-detail").hidden) throw new Error("Compose stack detail did not open");
  if (!window.document.getElementById("compose-stack-title").textContent.includes("Web stack")) {
    throw new Error("Compose stack title is wrong");
  }
  const composeEditor = window.document.querySelector("#compose-body .compose-editor");
  if (!composeEditor || !composeEditor.value.includes("nginx:latest")) {
    throw new Error("Compose editor did not load compose.yaml");
  }
  findButton(window.document.querySelector(".compose-file-list"), ".env").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  if (!window.document.querySelector("#compose-body .compose-editor").value.includes("secret-value")) {
    throw new Error("Admin Compose .env editor did not render");
  }
  findButton(window.document.querySelector(".compose-file-list"), "compose.yaml").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  window.document.querySelector('[data-compose-tab="revisions"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!window.document.getElementById("compose-body").textContent.includes(composeRevision.slice(0, 12))) {
    throw new Error("Compose revisions did not render");
  }
  findButton(window.document.getElementById("compose-body"), "Deploy").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  if (!window.document.getElementById("docker-dialog").open) throw new Error("Compose deploy dialog did not open");
  if (!window.document.getElementById("docker-dialog").textContent.includes("Project name")) {
    throw new Error("Compose deploy form is missing project name");
  }
  const hostChecks = [...window.document.getElementById("docker-dialog").querySelectorAll('input[type="checkbox"][value]')];
  if (hostChecks.length < 2) throw new Error("Compose multi-host selector missing hosts");
  hostChecks[0].checked = true;
  hostChecks[1].checked = true;
  findButton(window.document.getElementById("docker-dialog"), "Deploy").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(140);
  const deployRequest = window.__requests.find((request) => request.method === "POST" && request.path === "/api/docker/stacks/stk_web/deploy");
  if (!deployRequest || deployRequest.body?.agent_ids?.length !== 2) {
    throw new Error("Multi-host Compose deploy was not sent");
  }
  if (!window.document.getElementById("docker-dialog").textContent.includes("Per-host summary")) {
    throw new Error("Compose per-host deploy summary did not render");
  }
  if (!window.document.getElementById("docker-dialog").textContent.includes("Second Docker")) {
    throw new Error("Compose per-host summary is missing a host");
  }
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  window.document.querySelector('[data-compose-tab="files"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  const newEditor = window.document.querySelector("#compose-body .compose-editor");
  newEditor.value = "services:\n  web:\n    image: nginx:1.27\n";
  newEditor.dispatchEvent(new window.Event("input", { bubbles: true }));
  findButton(window.document.getElementById("compose-body"), "Save new revision").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(100);
  if (!window.__requests.some((request) => request.method === "PUT" && request.path === "/api/docker/stacks/stk_web")) {
    throw new Error("Compose revision save was not sent");
  }
  window.document.getElementById("compose-new").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!window.document.getElementById("docker-dialog").open) throw new Error("New Compose stack dialog did not open");
  findButton(window.document.getElementById("docker-dialog"), "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  window.__composeEmpty = true;
  window.document.querySelector('[data-page="overview"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  window.document.querySelector('[data-page="compose-files"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  if (window.document.getElementById("compose-empty").hidden) throw new Error("Compose empty state did not render");
  window.__composeEmpty = false;

  // Discovery page: summary, filters, drawer, scan panel, acknowledgement,
  // cancel, disabled state, and audit link.
  window.document.querySelector('[data-page="discovery"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(120);
  if (window.document.getElementById("page-discovery").hidden) throw new Error("Discovery page did not open");
  const discoverySummaryNode = window.document.getElementById("discovery-summary");
  if (discoverySummaryNode.hidden || !discoverySummaryNode.textContent.includes("Discovered devices")) {
    throw new Error("Discovery summary did not render");
  }
  const discoveryTable = window.document.getElementById("discovery-table-body");
  if (!discoveryTable.textContent.includes("printer.lan") || !discoveryTable.textContent.includes("VMware")) {
    throw new Error("Discovery table did not render devices");
  }
  if (!discoveryTable.textContent.includes("Raspberry Pi Foundation")) {
    throw new Error("Discovery table is missing vendor data");
  }
  const discoveryStateFilter = window.document.getElementById("discovery-filter-state");
  discoveryStateFilter.value = "new";
  discoveryStateFilter.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(60);
  if (discoveryTable.textContent.includes("esxi.lan") || !discoveryTable.textContent.includes("printer.lan")) {
    throw new Error("Discovery state filter did not narrow the table");
  }
  window.document.getElementById("discovery-filter-clear").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  if (!discoveryTable.textContent.includes("esxi.lan")) throw new Error("Discovery filter clear did not restore devices");
  const segmentPanel = window.document.getElementById("discovery-segment-list");
  if (!segmentPanel.textContent.includes("Gateway hint") || !segmentPanel.textContent.includes("192.168.1.1")) {
    throw new Error("Discovery segment summary did not render the gateway hint");
  }
  if (!discoverySummaryNode.textContent.includes("Exposure") || !discoverySummaryNode.textContent.includes("Conflicts")) {
    throw new Error("Discovery summary is missing exposure/conflict counts");
  }
  const exposureFilter = window.document.getElementById("discovery-filter-exposure");
  exposureFilter.checked = true;
  exposureFilter.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(60);
  if (!discoveryTable.textContent.includes("printer.lan") || discoveryTable.textContent.includes("esxi.lan")) {
    throw new Error("Discovery exposure filter did not narrow the table");
  }
  exposureFilter.checked = false;
  exposureFilter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const conflictFilter = window.document.getElementById("discovery-filter-conflict");
  conflictFilter.checked = true;
  conflictFilter.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(60);
  if (!discoveryTable.textContent.includes("conflict") || discoveryTable.textContent.includes("esxi.lan")) {
    throw new Error("Discovery conflict filter/badge did not render");
  }
  conflictFilter.checked = false;
  conflictFilter.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(60);
  if (!findButton(window.document.getElementById("discovery-filters"), "Export CSV")) {
    throw new Error("Discovery export buttons are missing");
  }

  const discoveryOpen = [...discoveryTable.querySelectorAll("button")].find((item) =>
    (item.getAttribute("aria-label") || "").includes("printer.lan"),
  );
  if (!discoveryOpen) throw new Error("Discovery device action missing");
  discoveryOpen.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const discoveryDrawer = window.document.getElementById("discovery-drawer");
  if (discoveryDrawer.hidden) throw new Error("Discovery device drawer did not open");
  const discoveryDrawerBody = window.document.getElementById("discovery-drawer-body");
  for (const expected of ["Device record", "What found this device", "Ports and services", "Change events", "Linked records"]) {
    if (!discoveryDrawerBody.textContent.includes(expected)) {
      throw new Error(`Discovery drawer is missing ${expected}`);
    }
  }
  if (!discoveryDrawerBody.textContent.includes("OpenSSH")) {
    throw new Error("Discovery drawer is missing service evidence");
  }
  if (!discoveryDrawerBody.textContent.includes("Identity evidence") || !discoveryDrawerBody.textContent.includes("Wake status")) {
    throw new Error("Discovery drawer is missing conflicts or wake status");
  }
  const verifyButton = findButton(window.document.getElementById("discovery-drawer-actions"), "Verify wake");
  if (!verifyButton) throw new Error("Discovery verify action is missing");
  verifyButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(100);
  if (!window.__requests.some((request) => request.method === "POST" && request.path === "/api/discovery/devices/dev_printer/verify")) {
    throw new Error("Discovery wake verification was not sent");
  }
  if (!window.document.getElementById("toast-region").textContent.includes("Wake verification: woke")) {
    throw new Error("Discovery wake verification result did not render");
  }

  const promoteButton = findButton(window.document.getElementById("discovery-drawer-actions"), "Promote");
  if (!promoteButton) throw new Error("Discovery promote action is missing");
  promoteButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  const promoteDialog = window.document.getElementById("discovery-promote-dialog");
  if (!promoteDialog.open) throw new Error("Discovery promote dialog did not open");
  findButton(promoteDialog, "Create wake target").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(120);
  if (!window.__requests.some((request) => request.method === "POST" && request.path === "/api/discovery/devices/dev_printer/promote" && request.body.target === "wake_target")) {
    throw new Error("Discovery wake-target promotion was not sent");
  }
  if (promoteDialog.open) throw new Error("Wake-target promotion should close the dialog");

  const promoteButton2 = findButton(window.document.getElementById("discovery-drawer-actions"), "Promote");
  if (!promoteButton2) throw new Error("Discovery promote action disappeared");
  promoteButton2.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!promoteDialog.open) throw new Error("Discovery promote dialog did not reopen for Linux");
  const promoteTarget = window.document.getElementById("discovery-promote-target");
  promoteTarget.value = "linux_agent";
  promoteTarget.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(20);
  findButton(promoteDialog, "Generate install command").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(120);
  const promoteBody = window.document.getElementById("discovery-promote-body");
  if (!promoteBody.textContent.includes("sextant_enroll_TOPSECRET") || !promoteBody.textContent.includes("sudo")) {
    throw new Error("Linux install command did not render");
  }
  if (!findButton(promoteDialog, "Copy command")) throw new Error("Install command copy button is missing");
  findButton(promoteDialog, "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);
  if (promoteBody.textContent.includes("sextant_enroll_TOPSECRET")) {
    throw new Error("Discovery token stayed visible after the promotion dialog closed");
  }
  if (JSON.stringify(window.__requests).includes("sextant_enroll_TOPSECRET")) {
    throw new Error("Discovery token was sent back to the server in a request");
  }

  const promoteButton3 = findButton(window.document.getElementById("discovery-drawer-actions"), "Promote");
  if (!promoteButton3) throw new Error("Discovery promote action disappeared");
  promoteButton3.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!promoteDialog.open) throw new Error("Discovery promote dialog did not reopen for Windows");
  const winTarget = window.document.getElementById("discovery-promote-target");
  winTarget.value = "windows_agent";
  winTarget.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(20);
  findButton(promoteDialog, "Generate install command").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(120);
  if (
    !promoteBody.textContent.includes("PowerShell") ||
    !promoteBody.querySelector('a[href*="WINDOWS_REMOTE_INSTALL.md"]')
  ) {
    throw new Error(`Windows manual install guidance did not render: ${promoteBody.textContent.slice(0, 300)}`);
  }
  findButton(promoteDialog, "Close").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(40);

  const discoveryForm = discoveryDrawerBody.querySelector("form");
  discoveryForm.elements.tags.value = "office, shelf";
  discoveryForm.elements.notes.value = "checked by operator";
  discoveryForm.elements.state.value = "known";
  discoveryForm.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await sleep(100);
  const discoveryPatch = window.__requests.find(
    (request) => request.method === "PATCH" && request.path === "/api/discovery/devices/dev_printer",
  );
  if (!discoveryPatch || !discoveryPatch.body.tags.includes("shelf") || discoveryPatch.body.state !== "known") {
    throw new Error("Discovery device edit was not sent");
  }
  discoveryDrawer.querySelector("[data-close-discovery-drawer]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  if (!discoveryDrawer.hidden) throw new Error("Discovery drawer did not close");

  const discoverySource = window.document.getElementById("discovery-scan-source");
  if (![...discoverySource.options].some((option) => option.textContent.includes("Studio PC"))) {
    throw new Error("Discovery agent source option is missing");
  }
  discoverySource.value = "agent:agt_demo";
  discoverySource.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(30);
  if (window.document.getElementById("discovery-scan-scope").value !== "192.168.1.0/24") {
    throw new Error("Discovery agent source did not select the agent segment");
  }
  window.document.getElementById("discovery-scan-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await sleep(100);
  const agentScanRequest = window.__requests.find(
    (request) => request.method === "POST" && request.path === "/api/agents/agt_demo/discover",
  );
  if (!agentScanRequest || agentScanRequest.body.scope[0] !== "192.168.1.0/24") {
    throw new Error("Agent-relayed discovery scan was not started");
  }
  if (window.document.getElementById("discovery-scan-progress").hidden) {
    throw new Error("Agent discovery progress did not appear");
  }
  window.document.getElementById("discovery-scan-cancel").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(100);
  if (!window.document.getElementById("discovery-scan-progress").hidden) {
    throw new Error("Agent discovery progress should hide after cancellation");
  }
  discoverySource.value = "server";
  discoverySource.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(30);

  const discoveryStart = window.document.getElementById("discovery-scan-start");
  window.document.getElementById("discovery-scan-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await sleep(100);
  const scanRequest = window.__requests.find(
    (request) => request.method === "POST" && request.path === "/api/discovery/scan",
  );
  if (!scanRequest || scanRequest.body.scope[0] !== "192.168.1.0/24") {
    throw new Error("Discovery scan was not started with the allowlisted scope");
  }
  if (!discoveryStart.disabled) throw new Error("Discovery start should disable while a scan runs");
  if (window.document.getElementById("discovery-scan-progress").hidden) {
    throw new Error("Discovery scan progress did not appear");
  }
  window.document.getElementById("discovery-scan-cancel").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(100);
  if (!window.__requests.some((request) => request.method === "POST" && request.path === "/api/discovery/scans/dsc_live/cancel")) {
    throw new Error("Discovery scan cancel was not sent");
  }
  if (!window.document.getElementById("discovery-scan-progress").hidden) {
    throw new Error("Discovery progress should hide after cancellation");
  }
  if (!findButton(window.document.getElementById("discovery-scan-panel"), "Audit log")) {
    throw new Error("Discovery audit link is missing");
  }

  window.__discoveryDisabled = true;
  window.document.querySelector('[data-page="overview"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  window.document.querySelector('[data-page="discovery"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const discoveryDisabled = window.document.getElementById("discovery-disabled");
  if (discoveryDisabled.hidden || !discoveryDisabled.textContent.includes("SEXTANT_DISCOVERY_ENABLED")) {
    throw new Error("Discovery disabled state is missing the environment flag explanation");
  }
  window.__discoveryDisabled = false;
  discoverySettings.acknowledged = false;
  window.document.querySelector('[data-page="overview"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  window.document.querySelector('[data-page="discovery"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  if (window.document.getElementById("discovery-ack-panel").hidden) {
    throw new Error("Discovery acknowledgement panel did not appear");
  }
  window.document.getElementById("discovery-ack-checkbox").checked = true;
  window.document.getElementById("discovery-ack-button").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(120);
  if (!window.__requests.some((request) => request.method === "PATCH" && request.path === "/api/discovery/settings")) {
    throw new Error("Discovery acknowledgement was not sent");
  }
  if (!window.document.getElementById("discovery-ack-panel").hidden) {
    throw new Error("Discovery acknowledgement panel should hide after authorization");
  }

  window.__discoveryRelayDisabled = true;
  window.document.querySelector('[data-page="overview"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  window.document.querySelector('[data-page="discovery"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const relaySource = window.document.getElementById("discovery-scan-source");
  if (relaySource.options.length !== 1 || relaySource.value !== "server") {
    throw new Error("Discovery relay-disabled source state is wrong");
  }
  if (!window.document.getElementById("discovery-source-hint").textContent.includes("SEXTANT_DISCOVERY_AGENT_RELAY")) {
    throw new Error("Discovery relay-disabled hint is missing");
  }
  window.__discoveryRelayDisabled = false;

  const themeToggle = window.document.getElementById("theme-toggle");
  if (!themeToggle) throw new Error("theme toggle missing");
  if (window.document.documentElement.dataset.theme !== "dark") throw new Error("dark theme should be the default");
  themeToggle.click();
  await sleep(10);
  if (window.document.documentElement.dataset.theme !== "light") throw new Error("theme toggle did not switch to light");
  if (window.localStorage.getItem("sextant.theme") !== "light") throw new Error("light theme was not persisted");
  if (themeToggle.getAttribute("aria-pressed") !== "true") throw new Error("theme toggle aria-pressed missing");
  themeToggle.click();
  await sleep(10);
  if (window.document.documentElement.dataset.theme !== "dark") throw new Error("theme toggle did not switch back to dark");

  const palette = window.document.getElementById("command-palette");
  const paletteInput = window.document.getElementById("command-palette-input");
  const paletteList = window.document.getElementById("command-palette-list");
  if (!palette || !paletteInput || !paletteList) throw new Error("command palette markup missing");
  const paletteDialog = palette.querySelector('[role="dialog"]');
  if (!paletteDialog || paletteDialog.getAttribute("aria-modal") !== "true" || !paletteDialog.getAttribute("aria-labelledby")) {
    throw new Error("command palette dialog semantics missing");
  }
  window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true, cancelable: true }));
  await sleep(10);
  if (palette.hidden) throw new Error("Ctrl+K did not open the command palette");
  if (window.document.activeElement !== paletteInput) throw new Error("command palette did not focus its input");
  if (!paletteList.textContent.includes("Fleet") || !paletteList.textContent.includes("Studio PC")) {
    throw new Error("command palette is missing pages or agents");
  }
  paletteInput.value = "studio";
  paletteInput.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  if (paletteList.querySelectorAll('[role="option"]').length !== 1) throw new Error("command palette did not filter");
  paletteInput.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await sleep(40);
  if (!palette.hidden) throw new Error("command palette did not close after activation");
  if (window.document.getElementById("agent-drawer").hidden) throw new Error("command palette did not open the selected agent");
  if (!window.document.getElementById("drawer-title").textContent.includes("Studio PC")) {
    throw new Error("command palette opened the wrong agent");
  }
  window.document.querySelector("[data-close-drawer]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);

  window.document.getElementById("command-palette-button").click();
  await sleep(10);
  paletteInput.value = "wake";
  paletteInput.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(10);
  paletteInput.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await sleep(30);
  if (window.document.getElementById("page-wake").hidden) throw new Error("command palette did not activate a page");
  window.document.getElementById("command-palette-button").click();
  await sleep(10);
  paletteInput.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  await sleep(10);
  if (!palette.hidden) throw new Error("Escape did not close the command palette");

  liveSource.emit("error", {});
  await sleep(20);
  if (!liveSource.closed) throw new Error("SSE failure did not close the stream and fall back");
  window.document.dispatchEvent(new window.Event("visibilitychange"));
  await sleep(60);
  const reconnectedSource = window.__eventSources[window.__eventSources.length - 1];
  if (!reconnectedSource || reconnectedSource === liveSource || reconnectedSource.closed) {
    throw new Error("SSE did not reconnect when the document became visible");
  }
  window.document.getElementById("logout-button").click();
  await sleep(80);
  if (!reconnectedSource.closed) throw new Error("SSE stream stayed open after logout");

  if (errors.length) throw new Error(`browser errors: ${errors.join(" | ")}`);
  console.log("UI_DOM_OK: login, SSE live dashboard with polling fallback, bulk power/service/profile actions, saved views, theme toggle, command palette, drawer, services filters, GPU processes, remote terminal, paginated history search/filter/paging/export, trends charts, timeline, Docker Hosts list/detail/refresh/empty/auto-refresh plus drift badges, inspect/logs/exec/console/clone/remove/cleanup/offline states, Compose Files list/editor/revisions/multi-host deploy/per-host summary/deployment history, Discovery summary/filters/exposure/conflicts/segments/table/drawer/edit/verify/promotion commands/token clearing/server-and-agent scan source/start/cancel/relay-disabled/disabled/unacknowledged states, policy/user search/session dialogs");
  window.close();
})().catch((error) => {
  console.error(error.stack || error.message);
  if (errors.length) console.error(errors.join("\n"));
  window.close();
  process.exitCode = 1;
});
