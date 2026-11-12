// Shared, deterministic browser-test fixtures. The shapes mirror the real API
// payloads and the realistic sample data in ../ui-smoke.js.
const FIXED_NOW = "2026-11-06T08:05:00Z";

const demoAgent = {
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
  last_seen: "2026-11-06T08:04:30Z",
  service_inventory_at: "2026-11-06T07:45:00Z",
  online: true,
  cpu_pct: 18.2,
  mem_pct: 44.0,
  mem_total: 34359738368,
  mem_used: 15118284800,
  uptime_seconds: 90000,
  disks: [
    { mount: "C:", total: 1000, used: 850, free: 150, percent: 85, fs: "NTFS" },
    { mount: "D:", total: 2000, used: 600, free: 1400, percent: 30, fs: "NTFS" },
  ],
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
  extra: {
    cpu_model: "Ryzen 9",
    kernel: "10.0",
    gpu_processes: [{ pid: 4242, name: "python.exe", mem_used: 8000000000 }],
  },
  alert_count: 2,
  alerts: [
    {
      key: "disk_root",
      incident_id: "inc_disk_1",
      first_seen_at: "2026-11-06T07:10:00Z",
      last_seen_at: "2026-11-06T08:04:30Z",
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
      last_seen_at: "2026-11-06T08:04:30Z",
      level: "critical",
      message: "Memory is 91% used",
      acknowledged: true,
      acknowledged_at: "2026-11-06T07:30:00Z",
      snoozed: false,
      snoozed_until: null,
    },
  ],
};

const buildAgent = {
  ...demoAgent,
  id: "agt_build",
  name: "Build Server",
  hostname: "build-01",
  os: "linux",
  os_version: "Ubuntu 24.04 LTS",
  arch: "x86_64",
  ip: "10.20.0.14",
  mac: "AA:BB:CC:11:22:33",
  machine_id: "machine-build",
  tags: ["ci", "build"],
  watch_services: ["docker"],
  profile_id: "prof_server",
  last_seen: "2026-11-06T08:04:45Z",
  service_inventory_at: "2026-11-06T07:50:00Z",
  cpu_pct: 63.5,
  mem_pct: 71.0,
  mem_total: 68719476736,
  mem_used: 48799753216,
  uptime_seconds: 604800,
  disks: [{ mount: "/", total: 4000, used: 2200, free: 1800, percent: 55, fs: "ext4" }],
  gpus: [],
  services: [
    { name: "docker", display_name: "Docker Engine", status: "running", startup: "enabled", watched: true },
    { name: "nginx", display_name: "Nginx", status: "running", startup: "enabled", watched: false },
  ],
  startup: [],
  extra: { cpu_model: "Xeon Gold 6338", kernel: "6.8.0", gpu_processes: [] },
  alert_count: 0,
  alerts: [],
};

const labAgent = {
  ...demoAgent,
  id: "agt_lab",
  name: "Lab Laptop",
  hostname: "lab-laptop",
  os: "linux",
  os_version: "Fedora 41",
  arch: "x86_64",
  ip: "10.20.0.51",
  mac: "AA:BB:CC:44:55:66",
  machine_id: "machine-lab",
  tags: ["lab"],
  watch_services: ["sshd"],
  profile_id: "prof_workstation",
  last_seen: "2026-11-05T17:12:00Z",
  service_inventory_at: "2026-11-05T17:00:00Z",
  online: false,
  cpu_pct: 4.0,
  mem_pct: 22.0,
  mem_total: 17179869184,
  mem_used: 3779571220,
  uptime_seconds: 3600,
  disks: [{ mount: "/", total: 512, used: 220, free: 292, percent: 43, fs: "btrfs" }],
  gpus: [],
  services: [{ name: "sshd", display_name: "OpenSSH server", status: "running", startup: "enabled", watched: true }],
  startup: [],
  extra: { cpu_model: "Core i7", kernel: "6.11.0", gpu_processes: [] },
  alert_count: 1,
  alerts: [
    {
      key: "service_sshd",
      incident_id: "inc_service_sshd_1",
      first_seen_at: "2026-11-05T16:40:00Z",
      last_seen_at: "2026-11-05T17:12:00Z",
      level: "warning",
      message: "Watched service sshd is stopped",
      acknowledged: false,
      acknowledged_at: null,
      snoozed: false,
      snoozed_until: null,
    },
  ],
};

const agents = [demoAgent, buildAgent, labAgent];

const dockerHost = {
  agent_id: "agt_build",
  name: "Build Server",
  hostname: "build-01",
  os: "linux",
  online: true,
  last_seen: "2026-11-06T08:04:45Z",
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
  counts: { containers: 2, running: 1, paused: 0, stopped: 1, unhealthy: 0, images: 1, volumes: 1, networks: 2 },
  inventory_hash: "a".repeat(64),
  inventory_revision: 4,
  inventory_at: "2026-11-06T08:04:30Z",
  collected_at: "2026-11-06T08:04:30Z",
  heartbeat_at: "2026-11-06T08:04:45Z",
  age_seconds: 30,
  stale: false,
  last_error: "",
  capabilities: { mutate: true, exec: true, prune: true },
};

const dockerDetail = {
  server_time: "2026-11-06T08:05:00Z",
  stale_seconds: 900,
  host: dockerHost,
  deployments: [
    {
      id: "dep_web",
      stack_id: "stk_web",
      agent_id: "agt_build",
      revision_hash: "e".repeat(64),
      project_name: "web",
      action: "up",
      status: "succeeded",
      active: true,
      drift: true,
      config_drift: true,
      stack_name: "Web stack",
      agent_name: "Build Server",
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
      in_use: true,
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
    agent_id: "agt_build",
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
    agent_name: "Build Server",
  },
];

const dockerCleanup = {
  server_time: "2026-11-06T08:05:00Z",
  host: dockerHost,
  can_prune: true,
  scopes: [
    { scope: "stopped_containers", label: "Stopped containers", count: 1, estimated_bytes: 0, estimated: true, candidates: [], warning: "Compose-managed stopped containers are excluded by default." },
    { scope: "dangling_images", label: "Dangling images", count: 1, estimated_bytes: 1048576, estimated: true, candidates: [], warning: "Untagged images only." },
    { scope: "unused_images", label: "Unused images", count: 0, estimated_bytes: 0, estimated: true, candidates: [], warning: "Destructive." },
    { scope: "unused_volumes", label: "Unused volumes", count: 1, estimated_bytes: 0, estimated: false, candidates: [], warning: "Destructive and irreversible." },
    { scope: "unused_networks", label: "Unused networks", count: 0, estimated_bytes: 0, estimated: true, candidates: [], warning: "Default networks excluded." },
    { scope: "build_cache", label: "Build cache", count: 0, estimated_bytes: 0, estimated: false, candidates: [], warning: "Reported after prune." },
  ],
};

const historyCommands = [
  {
    id: "cmd_queued",
    agent_id: "agt_demo",
    kind: "shell",
    args: { shell: "bash", command: "uptime" },
    status: "queued",
    created_at: "2026-11-06T08:04:00Z",
    completed_at: null,
    exit_code: null,
    stdout: "",
    stderr: "",
    error: "",
    output_truncated: false,
  },
  {
    id: "cmd_failed",
    agent_id: "agt_demo",
    kind: "service",
    args: { action: "restart", name: "Spooler" },
    status: "failed",
    created_at: "2026-11-06T08:02:00Z",
    completed_at: "2026-11-06T08:02:30Z",
    exit_code: 1,
    stdout: "",
    stderr: "failed to restart",
    error: "",
    output_truncated: false,
  },
  {
    id: "cmd_alpha",
    agent_id: "agt_demo",
    kind: "shell",
    args: { shell: "bash", command: "alpha search target" },
    status: "succeeded",
    created_at: "2026-11-06T08:00:00Z",
    completed_at: "2026-11-06T08:00:10Z",
    exit_code: 0,
    stdout: "alpha output",
    stderr: "",
    error: "",
    output_truncated: false,
  },
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

const historyPoints = [
  { at: "2026-11-06T02:00:00Z", cpu_pct: 8, mem_pct: 38, disk_max_pct: 85, gpu_max_util: 10, gpu_max_temp: 54 },
  { at: "2026-11-06T03:00:00Z", cpu_pct: 12, mem_pct: 41, disk_max_pct: 85, gpu_max_util: 18, gpu_max_temp: 58 },
  { at: "2026-11-06T04:00:00Z", cpu_pct: null, mem_pct: null, disk_max_pct: null, gpu_max_util: null, gpu_max_temp: null },
  { at: "2026-11-06T05:00:00Z", cpu_pct: null, mem_pct: null, disk_max_pct: null, gpu_max_util: null, gpu_max_temp: null },
  { at: "2026-11-06T06:00:00Z", cpu_pct: 24, mem_pct: 43, disk_max_pct: 85, gpu_max_util: 45, gpu_max_temp: 66 },
  { at: "2026-11-06T07:00:00Z", cpu_pct: 10, mem_pct: 40, disk_max_pct: 85, gpu_max_util: 20, gpu_max_temp: 60 },
  { at: "2026-11-06T07:30:00Z", cpu_pct: null, mem_pct: null, disk_max_pct: null, gpu_max_util: null, gpu_max_temp: null },
  { at: "2026-11-06T08:00:00Z", cpu_pct: 18, mem_pct: 44, disk_max_pct: 85, gpu_max_util: 97, gpu_max_temp: 78 },
];

const agentEvents = [
  { id: 4, agent_id: "agt_demo", at: "2026-11-06T08:00:00Z", kind: "online", severity: "success", title: "Agent back online", detail: {} },
  {
    id: 3,
    agent_id: "agt_demo",
    at: "2026-11-06T07:57:00Z",
    kind: "alert",
    severity: "warning",
    title: "Alert raised: Disk C: is 85% full",
    detail: { action: "raised" },
  },
  {
    id: 2,
    agent_id: "agt_demo",
    at: "2026-11-06T07:55:00Z",
    kind: "service",
    severity: "critical",
    title: "Service Spooler stopped",
    detail: { service: "Spooler", from: "running", to: "stopped" },
  },
  {
    id: 1,
    agent_id: "agt_demo",
    at: "2026-11-06T07:50:00Z",
    kind: "package",
    severity: "info",
    title: "Package version changed: apt:nginx 1.25.0",
    detail: { action: "version_changed" },
  },
];

const loginUser = {
  id: 1,
  username: "admin",
  display_name: "Admin",
  email: "",
  role: "admin",
  source: "local",
  created_at: "2026-11-06T07:00:00Z",
  last_login_at: "2026-11-06T08:00:00Z",
};

function loginPayload() {
  return {
    user: loginUser,
    csrf: "csrf-demo",
    permissions: { viewer: true, operate: true, admin: true },
    settings: {
      ldap_enabled: false,
      local_auth: true,
      offline_after_seconds: 90,
      version: "0.3.0",
      public_url: "",
    },
  };
}

const packagesPayload = {
  inventory: [
    { manager: "apt", package: "nginx", version: "1.24.0-1", arch: "amd64", source: "", last_seen: "2026-11-06T08:00:00Z" },
    { manager: "apt", package: "openssl", version: "3.0.13-1", arch: "amd64", source: "", last_seen: "2026-11-06T08:00:00Z" },
    { manager: "apt", package: "curl", version: "8.5.0-2", arch: "amd64", source: "", last_seen: "2026-11-06T08:00:00Z" },
  ],
  managers: [{ name: "apt", version: "2.7.14" }],
  rules: [{ manager: "apt", package: "nginx", desired_state: "present", version_spec: "", enabled: true }],
  drift: [],
};

const profilesPayload = {
  profiles: [
    {
      id: "prof_workstation",
      slug: "workstation",
      name: "Workstation",
      description: "Relaxed",
      is_system: true,
      revision: 1,
      agent_count: 2,
      settings: {
        heartbeat_seconds: 60,
        modules: {
          telemetry: true,
          services: true,
          startup: true,
          gpu: true,
          packages: true,
          shell: true,
          power: true,
          service_control: true,
          package_management: true,
          self_update: true,
        },
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
      agent_count: 1,
      settings: {
        heartbeat_seconds: 30,
        modules: {
          telemetry: true,
          services: true,
          startup: true,
          gpu: true,
          packages: true,
          shell: true,
          power: true,
          service_control: true,
          package_management: true,
          self_update: true,
        },
        collection: { service_inventory_seconds: 3600, package_inventory_seconds: 21600, package_full_seconds: 604800 },
        actions: { allow_shell: true, allow_power: true, allow_service: true, allow_packages: true },
        update: { allow_self_update: true, channel: "stable" },
        max_output_bytes: 200000,
      },
    },
  ],
};

const dashboardPayload = {
  server_time: FIXED_NOW,
  stats: { total: 3, online: 2, offline: 1, alerts: 3, active_commands: 1 },
  agents,
};

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
  agent_relay: true,
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
    wake_target_id: null,
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
    tags: [],
    notes: "VM host",
    mac_randomized: false,
  },
];
const discoverySummary = {
  total: 2,
  new: 1,
  states: { new: 1, known: 1, ignored: 0, promoted: 0 },
  types: [
    { type: "printer", count: 1 },
    { type: "hypervisor", count: 1 },
  ],
  segments: [{ segment: "192.168.1.0/24", count: 2 }],
  vendors: [
    { vendor: "Raspberry Pi Foundation", count: 1 },
    { vendor: "VMware", count: 1 },
  ],
  no_agent: 1,
};
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
function createApi(session) {
  let dockerCommand = null;
  let dockerDetailReads = 0;
  const discoveryDeviceState = discoveryDevices.map((device) => ({
    ...device,
    open_ports: [...device.open_ports],
    tags: [...device.tags],
    services: { ...device.services },
    evidence: { ...device.evidence },
  }));
  let activeScan = null;
  return function respond(url, method, body = null) {
    const key = url.pathname;
    if (key === "/api/auth/config") {
      return { status: 200, body: { ldap_enabled: false, version: "0.3.0", public_url: "" } };
    }
    if (key === "/api/me") {
      return session.authenticated
        ? { status: 200, body: loginPayload() }
        : { status: 401, body: { detail: "Authentication required." } };
    }
    if (key === "/api/auth/login" && method === "POST") {
      session.authenticated = true;
      return { status: 200, body: loginPayload() };
    }
    if (key === "/api/auth/logout" && method === "POST") {
      session.authenticated = false;
      return { status: 204, body: null };
    }
    if (key === "/api/dashboard") return { status: 200, body: dashboardPayload };
    if (key === "/api/discovery/settings") {
      if (method === "PATCH") {
        session.discoveryAcknowledged = Boolean(body?.acknowledged);
      }
      const acknowledged =
        session.discoveryAcknowledged === undefined
          ? discoverySettings.acknowledged
          : Boolean(session.discoveryAcknowledged);
      return {
        status: 200,
        body: {
          ...discoverySettings,
          enabled: !session.discoveryDisabled,
          acknowledged,
          agent_relay: !session.discoveryRelayDisabled,
          acknowledged_at: acknowledged ? discoverySettings.acknowledged_at : null,
          disabled_reasons: session.discoveryDisabled
            ? ["SEXTANT_DISCOVERY_ENABLED is 0."]
            : [],
        },
      };
    }
    if (key === "/api/discovery/agents") {
      return {
        status: 200,
        body: {
          relay_enabled: !session.discoveryRelayDisabled,
          agents: session.discoveryRelayDisabled
            ? []
            : [
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
              ],
          local_flag_required: true,
        },
      };
    }
    if (key === "/api/discovery/devices") {
      let filtered = [...discoveryDeviceState];
      const state = url.searchParams.get("state") || "";
      const type = url.searchParams.get("type") || "";
      const segment = url.searchParams.get("segment") || "";
      const vendor = url.searchParams.get("vendor") || "";
      if (state) filtered = filtered.filter((device) => device.state === state);
      if (type) filtered = filtered.filter((device) => device.device_type === type);
      if (segment) filtered = filtered.filter((device) => device.segment === segment);
      if (vendor) filtered = filtered.filter((device) => device.vendor === vendor);
      if (url.searchParams.get("no_agent") === "true") {
        filtered = filtered.filter((device) => !device.agent_id);
      }
      if (url.searchParams.get("admin_ports") === "true") {
        filtered = filtered.filter((device) =>
          device.open_ports.some((port) => [22, 3389, 5900, 5985, 5986].includes(port)),
        );
      }
      return {
        status: 200,
        body: { devices: filtered, total: filtered.length, limit: 50, offset: 0, summary: discoverySummary },
      };
    }
    if (method === "GET" && key.startsWith("/api/discovery/devices/")) {
      const id = key.slice("/api/discovery/devices/".length);
      const device = discoveryDeviceState.find((item) => item.id === id);
      return device
        ? {
            status: 200,
            body: {
              device,
              events: id === "dev_printer" ? discoveryEvents : [],
              agent: device.agent_id ? demoAgent : null,
              wake_target: null,
            },
          }
        : { status: 404, body: { detail: "Discovered device not found." } };
    }
    if (method === "PATCH" && key.startsWith("/api/discovery/devices/")) {
      const id = key.slice("/api/discovery/devices/".length);
      const index = discoveryDeviceState.findIndex((item) => item.id === id);
      if (index < 0) return { status: 404, body: { detail: "Discovered device not found." } };
      const current = discoveryDeviceState[index];
      const next = {
        ...current,
        ...(body?.tags !== undefined ? { tags: body.tags } : {}),
        ...(body?.notes !== undefined ? { notes: body.notes } : {}),
        ...(body?.state !== undefined ? { state: body.state } : {}),
      };
      discoveryDeviceState[index] = next;
      return { status: 200, body: { device: next, events: id === "dev_printer" ? discoveryEvents : [] } };
    }
    if (method === "DELETE" && key.startsWith("/api/discovery/devices/")) {
      const id = key.slice("/api/discovery/devices/".length);
      const index = discoveryDeviceState.findIndex((item) => item.id === id);
      if (index >= 0) discoveryDeviceState.splice(index, 1);
      return { status: 204, body: null };
    }
    if (key === "/api/discovery/scans") {
      return {
        status: 200,
        body: {
          scans: activeScan ? [activeScan, ...discoveryScans] : discoveryScans,
          total: activeScan ? discoveryScans.length + 1 : discoveryScans.length,
          limit: 50,
          offset: 0,
          active_scan_id: activeScan?.status === "running" ? activeScan.id : null,
        },
      };
    }
    if (method === "POST" && /^\/api\/agents\/[^/]+\/discover$/.test(key)) {
      const agentId = key.split("/")[3];
      activeScan = {
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
      return {
        status: 200,
        body: {
          scan: activeScan,
          command: { id: "cmd_discovery", kind: "discovery", args: { action: "discovery.scan" }, status: "queued" },
        },
      };
    }
    if (method === "POST" && key === "/api/discovery/scan") {
      activeScan = {
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
      return { status: 200, body: { scan: activeScan } };
    }
    if (method === "POST" && /^\/api\/discovery\/scans\/[^/]+\/cancel$/.test(key)) {
      if (activeScan) {
        activeScan = {
          ...activeScan,
          status: "cancelled",
          finished_at: "2026-11-06T08:06:00Z",
        };
      }
      return { status: 200, body: { scan: activeScan } };
    }
    if (key === "/api/update/status") {
      return {
        status: 200,
        body: {
          update_available: true,
          current_version: "0.3.0",
          latest_version: "9.9.9",
          url: "https://example.test/releases",
        },
      };
    }
    if (key === "/api/profiles") return { status: 200, body: profilesPayload };
    if (key === "/api/profile-tags") return { status: 200, body: { assignments: [] } };
    if (key === "/api/saved-views") return { status: 200, body: { views: [] } };
    if (key === "/api/remote-install") return { status: 200, body: { jobs: [] } };
    if (key === "/api/agents/agt_demo") {
      return { status: 200, body: { agent: demoAgent, commands: historyCommands.slice(0, 2) } };
    }
    if (key === "/api/docker/hosts") {
      return { status: 200, body: { server_time: "2026-11-06T08:05:00Z", stale_seconds: 900, hosts: [dockerHost] } };
    }
    if (key === "/api/docker/hosts/agt_build") {
      dockerDetailReads += 1;
      if (dockerDetailReads > 1) {
        dockerHost.inventory_revision += 1;
        dockerHost.age_seconds = 2;
      }
      return { status: 200, body: dockerDetail };
    }
    if (key === "/api/docker/hosts/agt_build/cleanup") {
      return { status: 200, body: dockerCleanup };
    }
    if (method === "POST" && /^\/api\/docker\/hosts\/agt_build\/containers\/[^/]+\/console$/.test(key)) {
      return {
        status: 200,
        body: {
          session_id: "con_browser",
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
    }
    if (method === "POST" && (key === "/api/docker/console/con_browser/input" || key === "/api/docker/console/con_browser/resize")) {
      return { status: 204, body: null };
    }
    if (method === "DELETE" && key === "/api/docker/console/con_browser") {
      return { status: 204, body: null };
    }
    if (key === "/api/docker/stacks") {
      return { status: 200, body: { stacks: [composeStack] } };
    }
    if (key === "/api/docker/stacks/stk_web") {
      return { status: 200, body: { stack: composeStack } };
    }
    if (key === "/api/docker/deployments") {
      return { status: 200, body: { deployments: composeDeployments } };
    }
    if (key === "/api/docker/deployments/dep_web") {
      return { status: 200, body: { deployment: composeDeployments[0] } };
    }
    if (key === "/api/docker/settings") {
      return {
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
      };
    }
    if (
      method === "POST" &&
      (key === "/api/docker/hosts/agt_build/actions" ||
        key === "/api/docker/hosts/agt_build/cleanup" ||
        /^\/api\/docker\/hosts\/agt_build\/containers\/[^/]+\/exec$/.test(key))
    ) {
      const action = key.endsWith("/cleanup")
        ? "cleanup"
        : key.endsWith("/exec")
          ? "container.exec_one_shot"
          : body?.action;
      dockerCommand = {
        id: "cmd_docker_browser",
        agent_id: "agt_build",
        kind: "docker",
        args: body || {},
        status: "succeeded",
        created_at: "2026-11-06T08:05:00Z",
        completed_at: "2026-11-06T08:05:05Z",
        exit_code: 0,
        stdout:
          action === "cleanup"
            ? "Total reclaimed space: 1MB"
            : action === "container.exec_one_shot"
              ? "uid=0(root)"
              : action === "container.inspect"
                ? '{"Config":{"Env":["<masked>"]}}'
                : "ok",
        stderr: "",
        error: "",
        output_truncated: false,
      };
      return { status: 200, body: dockerCommand };
    }
    if (key === "/api/commands/cmd_docker_browser" && dockerCommand) {
      return { status: 200, body: { command: dockerCommand } };
    }
    if (key === "/api/agents/agt_demo/packages") return { status: 200, body: packagesPayload };
    if (key === "/api/agents/agt_demo/history") {
      return { status: 200, body: { agent_id: "agt_demo", hours: Number(url.searchParams.get("hours") || 24), points: historyPoints } };
    }
    if (key === "/api/agents/agt_demo/events") {
      return { status: 200, body: { agent_id: "agt_demo", hours: 24, events: agentEvents } };
    }
    if (key === "/api/agents/agt_demo/commands") {
      const status = url.searchParams.get("status") || "all";
      const query = (url.searchParams.get("q") || "").toLowerCase();
      const limit = Number(url.searchParams.get("limit") || 25);
      const offset = Number(url.searchParams.get("offset") || 0);
      let filtered = historyCommands.filter((command) => status === "all" || command.status === status);
      if (query) {
        filtered = filtered.filter((command) => JSON.stringify(command).toLowerCase().includes(query));
      }
      return {
        status: 200,
        body: { commands: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset },
      };
    }
    return { status: 404, body: { detail: `No mock for ${key}` } };
  };
}

function eventSourceInit() {
  window.__eventSources = [];
  class MockEventSource {
    constructor(url) {
      this.url = url;
      this.closed = false;
      this.readyState = 0;
      this.listeners = new Map();
      window.__eventSources.push(this);
      queueMicrotask(() => {
        if (this.closed) return;
        this.readyState = 1;
        this.dispatch("open", { type: "open" });
      });
    }
    addEventListener(type, handler) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(handler);
    }
    removeEventListener(type, handler) {
      const handlers = this.listeners.get(type) || [];
      this.listeners.set(
        type,
        handlers.filter((entry) => entry !== handler),
      );
    }
    dispatch(type, event) {
      for (const handler of this.listeners.get(type) || []) handler(event);
    }
    emit(type, data) {
      this.dispatch(type, { type, data: JSON.stringify(data) });
    }
    close() {
      this.closed = true;
      this.readyState = 2;
    }
  }
  window.EventSource = MockEventSource;
}

module.exports = {
  FIXED_NOW,
  createApi,
  dashboardPayload,
  demoAgent,
  eventSourceInit,
  loginPayload,
};
