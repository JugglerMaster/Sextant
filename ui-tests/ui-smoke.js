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
  window.HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
}
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
  policy: { allow_shell: true, allow_power: true, allow_service: false, allow_packages: true },
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
  services: [{ name: "Spooler", display_name: "Print Spooler", status: "running", startup: "Auto", watched: true }],
  startup: [{ name: "Backup", command: "C:\\backup.exe", source: "HKCU Run", enabled: true }],
  extra: { cpu_model: "Ryzen 9", kernel: "10.0", gpu_processes: [{ pid: 4242, name: "python.exe", mem_used: 8000000000 }] },
  alert_count: 1,
  alerts: [{ level: "warning", message: "Disk C: is 85% full" }],
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
          role: "operator",
          source: "local",
          active: true,
          session_count: 2,
          last_login_at: "2026-11-06T08:00:00Z",
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
  const response = responses[key] || { status: 404, body: { detail: `No mock for ${key}` } };
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
  const updateBanner = window.document.getElementById("update-banner");
  if (updateBanner.hidden) throw new Error("update banner should be visible for an available release");
  if (!updateBanner.textContent.includes("9.9.9")) throw new Error("update banner missing version");
  const alertCenter = window.document.getElementById("alert-center");
  if (alertCenter.hidden) throw new Error("alert center should be visible");
  if (!alertCenter.textContent.includes("Disk C:")) throw new Error("alert center missing alert");
  const selector = window.document.querySelector(".agent-select");
  if (!selector) throw new Error("agent selection checkbox missing");
  selector.checked = false;
  selector.click();
  await sleep(10);
  if (window.document.getElementById("bulk-actions").hidden) throw new Error("bulk actions should be visible");
  window.document.getElementById("bulk-clear").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

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

  const serviceTab = window.document.querySelector('[data-tab="services"]');
  serviceTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(10);
  if (!window.document.getElementById("drawer-body").textContent.includes("Spooler")) {
    throw new Error("service list did not render");
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

  const historyTab = window.document.querySelector('[data-tab="history"]');
  historyTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(20);
  if (!findButton(window.document.getElementById("drawer-body"), "Cancel")) throw new Error("queued command cancel action missing");
  if (!findButton(window.document.getElementById("drawer-body"), "Retry")) throw new Error("failed command retry action missing");

  const editButton = findButton(window.document.getElementById("drawer-actions"), "Edit policy");
  if (!editButton) throw new Error("agent policy edit action missing");
  editButton.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  if (!window.document.getElementById("agent-edit-dialog").open) throw new Error("agent edit dialog did not open");
  window.document.querySelector("[data-close-agent-edit]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  window.document.querySelector('[data-page="users"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  const userList = window.document.getElementById("user-list");
  if (!userList.textContent.includes("Operator One")) throw new Error("user list did not render");
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

  if (errors.length) throw new Error(`browser errors: ${errors.join(" | ")}`);
  console.log("UI_DOM_OK: login, dashboard, drawer, services, GPU processes, remote terminal, history actions, policy/user/session dialogs");
  window.close();
})().catch((error) => {
  console.error(error.stack || error.message);
  if (errors.length) console.error(errors.join("\n"));
  window.close();
  process.exitCode = 1;
});
