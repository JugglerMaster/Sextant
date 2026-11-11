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
  "/api/remote-install": { status: 200, body: { jobs: [] } },
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
  console.log("UI_DOM_OK: login, SSE live dashboard with polling fallback, bulk power/service/profile actions, saved views, theme toggle, command palette, drawer, services filters, GPU processes, remote terminal, paginated history search/filter/paging/export, trends charts, timeline, policy/user search/session dialogs");
  window.close();
})().catch((error) => {
  console.error(error.stack || error.message);
  if (errors.length) console.error(errors.join("\n"));
  window.close();
  process.exitCode = 1;
});
