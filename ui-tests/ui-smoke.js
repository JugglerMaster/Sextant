const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");

const path = require("path");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(`${root}/runner_control/static/index.html`, "utf8");
const source = fs.readFileSync(`${root}/runner_control/static/app.js`, "utf8");
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
  agent_version: "0.2.0",
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
    { key: "disk_root", level: "warning", message: "Disk C: is 85% full", acknowledged: false, snoozed: false, snoozed_until: null },
    { key: "memory", level: "critical", message: "Memory is 91% used", acknowledged: true, snoozed: false, snoozed_until: null },
  ],
};

const responses = {
  "/api/auth/config": { status: 200, body: { ldap_enabled: false, version: "0.2.0", public_url: "" } },
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
      settings: { ldap_enabled: false, local_auth: true, offline_after_seconds: 90, version: "0.2.0", public_url: "" },
    },
  },
  "/api/update/status": {
    status: 200,
    body: {
      update_available: true,
      current_version: "0.2.0",
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
        { at: "2026-11-06T08:00:00Z", cpu_pct: 18, mem_pct: 44, disk_max_pct: 85, gpu_max_util: 97, gpu_max_temp: 78 },
      ],
    },
  },
  "/api/agents/agt_demo": {
    status: 200,
    body: {
      agent,
      commands: [
        { id: "cmd_queued", agent_id: "agt_demo", kind: "shell", args: { shell: "bash", command: "uptime" }, status: "queued", created_at: "2026-11-06T08:00:00Z", exit_code: null, stdout: "", stderr: "", error: "" },
        { id: "cmd_failed", agent_id: "agt_demo", kind: "service", args: { action: "restart", name: "Spooler" }, status: "failed", created_at: "2026-11-06T07:50:00Z", exit_code: 1, stdout: "", stderr: "failed", error: "", output_truncated: false },
      ],
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

window.fetch = async (path, options = {}) => {
  const key = String(path).split("?")[0];
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
  window.__requests.push({ path: key, method, body });
  let response;
  if (method === "POST" && /^\/api\/agents\/[^/]+\/commands$/.test(key)) {
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
  await sleep(80);

  if (window.document.getElementById("app-view").hidden) throw new Error("app shell should be visible after login");
  if (window.document.getElementById("stat-total").textContent !== "1") throw new Error("stats did not render");
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
    (request) => request.method === "POST" && request.path === "/api/agents/agt_demo/commands" && request.body?.kind === "power",
  );
  if (!powerRequest || powerRequest.body.args.action !== "reboot" || powerRequest.body.args.delay !== 30) {
    throw new Error("bulk reboot command was not queued");
  }
  if (!window.document.getElementById("toast-region").textContent.includes("Reboot: 1 of 1 succeeded")) {
    throw new Error("bulk reboot result was not reported");
  }
  if (window.document.getElementById("bulk-count").textContent !== "0 selected · select machines to enable bulk actions") {
    throw new Error("selection should clear after a bulk power action");
  }
  if (!rebootButton.disabled) throw new Error("bulk actions should disable after clearing the selection");

  window.document.querySelector(".agent-select").click();
  await sleep(10);
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
    (request) => request.method === "POST" && request.path === "/api/agents/agt_demo/commands" && request.body?.kind === "service",
  );
  if (!serviceRequest || serviceRequest.body.args.action !== "restart" || serviceRequest.body.args.name !== "Spooler") {
    throw new Error("bulk service command was not queued");
  }
  if (!window.document.getElementById("toast-region").textContent.includes("Restart Spooler: 1 of 1 succeeded")) {
    throw new Error("bulk service result was not reported");
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
    (request) => request.method === "PATCH" && request.path === "/api/agents/agt_demo",
  );
  if (!profileRequest || profileRequest.body?.profile !== "server") {
    throw new Error("bulk profile patch was not sent");
  }
  if (!window.document.getElementById("toast-region").textContent.includes("Profile Server: 1 of 1 succeeded")) {
    throw new Error("bulk profile result was not reported");
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
  await sleep(10);
  window.prompt = originalPrompt;
  const storedViews = JSON.parse(window.localStorage.getItem("rc.savedViews") || "[]");
  if (storedViews.length !== 1) throw new Error("saved view was not persisted");
  const storedView = storedViews[0];
  if (
    storedView.name !== "GPU hosts" ||
    storedView.status !== "online" ||
    storedView.profile !== "" ||
    storedView.tag !== "" ||
    storedView.search !== "studio"
  ) {
    throw new Error(`saved view fields are wrong: ${JSON.stringify(storedView)}`);
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
  await sleep(10);
  if (JSON.parse(window.localStorage.getItem("rc.savedViews") || "[]").length !== 0) {
    throw new Error("saved view was not deleted");
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
  if (!trendsBody.querySelector(".timeline")) throw new Error("timeline did not render");
  if (trendsBody.querySelectorAll(".timeline-entry").length !== 4) {
    throw new Error(`expected 4 timeline entries, got ${trendsBody.querySelectorAll(".timeline-entry").length}`);
  }
  if (!trendsBody.querySelector(".timeline-command") || !trendsBody.querySelector(".timeline-alert")) {
    throw new Error("timeline should merge commands and alerts");
  }
  if (!trendsBody.textContent.includes("4 events")) throw new Error("timeline count missing");
  const sevenDay = findButton(trendsBody, "7d");
  if (!sevenDay) throw new Error("7d trend range toggle missing");
  sevenDay.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(80);
  const activeRange = [...trendsBody.querySelectorAll(".range-option")].find((option) => option.classList.contains("is-active"));
  if (!activeRange || activeRange.textContent.trim() !== "7d") throw new Error("7d trend range did not activate");
  if (trendsBody.querySelectorAll(".chart-card").length !== 5) throw new Error("trend charts missing after 7d toggle");

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
  if (window.localStorage.getItem("rc.theme") !== "light") throw new Error("light theme was not persisted");
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
  console.log("UI_DOM_OK: login, SSE live dashboard with polling fallback, bulk power/service/profile actions, saved views, theme toggle, command palette, drawer, services filters, GPU processes, remote terminal, history actions, trends charts, timeline, policy/user search/session dialogs");
  window.close();
})().catch((error) => {
  console.error(error.stack || error.message);
  if (errors.length) console.error(errors.join("\n"));
  window.close();
  process.exitCode = 1;
});
