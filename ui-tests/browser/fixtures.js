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
  agent_version: "0.2.0",
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
      level: "warning",
      message: "Disk C: is 85% full",
      acknowledged: false,
      snoozed: false,
      snoozed_until: null,
    },
    {
      key: "memory",
      level: "critical",
      message: "Memory is 91% used",
      acknowledged: true,
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
      level: "warning",
      message: "Watched service sshd is stopped",
      acknowledged: false,
      snoozed: false,
      snoozed_until: null,
    },
  ],
};

const agents = [demoAgent, buildAgent, labAgent];

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
      version: "0.2.0",
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

function createApi(session) {
  return function respond(url, method) {
    const key = url.pathname;
    if (key === "/api/auth/config") {
      return { status: 200, body: { ldap_enabled: false, version: "0.2.0", public_url: "" } };
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
    if (key === "/api/update/status") {
      return {
        status: 200,
        body: {
          update_available: true,
          current_version: "0.2.0",
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
