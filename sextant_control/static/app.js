const state = {
  user: null,
  csrf: null,
  settings: null,
  permissions: { viewer: true, operate: false, admin: false },
  dashboard: null,
  page: "overview",
  detailId: null,
  detailAgent: null,
  detailTab: "overview",
  trendRange: "24",
  agentCommands: {},
  commandHistory: {},
  serviceFilters: {},
  terminals: {},
  resetUser: null,
  sessionsUser: null,
  pollTimer: null,
  eventSource: null,
  loadingDashboard: false,
  search: "",
  profiles: [],
  profileTags: [],
  editingProfile: null,
  remoteJobs: [],
  remoteJobId: null,
  remoteTimer: null,
  dockerHosts: [],
  dockerHostId: null,
  dockerHostDetail: null,
  dockerTab: "overview",
  dockerLoading: false,
  dockerRefreshTimer: null,
  fleetFilters: { status: "all", profile: "", tag: "" },
  fleetRenderLimit: 60,
  agentCards: new Map(),
  agentSignatures: new Map(),
  fleetEmptyNode: null,
  savedViews: [],
  selectedAgents: new Set(),
  drawerTrigger: null,
  userSearch: "",
  theme: "dark",
  fleetStatusSignature: "",
  paletteItems: [],
  paletteIndex: 0,
  paletteTrigger: null,
};

const $ = (id) => document.getElementById(id);
const FLEET_PAGE_SIZE = 60;
const SERVICE_PAGE_SIZE = 50;
const SERVICE_STATUS_FILTERS = ["all", "running", "stopped", "failed", "watched"];
const HISTORY_PAGE_SIZES = [25, 50, 100];
const HISTORY_STATUS_FILTERS = ["all", "queued", "dispatched", "succeeded", "failed", "cancelled"];
const HISTORY_SEARCH_DELAY = 300;
const FLEET_STATUS_VALUES = ["all", "online", "offline", "alerts"];
const ADMIN_PAGES = ["enroll", "audit", "users", "profiles", "remote-install"];
const THEME_KEY = "sextant.theme";
const PAGE_META = {
  overview: ["Fleet", "Live machines and health at a glance."],
  wake: ["Wake targets", "Send Wake-on-LAN packets to machines without agents."],
  "docker-hosts": ["Docker Hosts", "Select a host to inspect its cached Docker inventory."],
  enroll: ["Enrollment", "Issue one-time tokens and onboard agents."],
  audit: ["Audit log", "Who did what, from where, and when."],
  users: ["Users", "Local accounts and role-based access."],
  profiles: ["Profiles", "Global agent behavior, cadence, modules, and tag assignments."],
  "remote-install": ["Remote install", "Install a Linux agent over key-based SSH."],
  security: ["Security", "Session, identity provider, and account settings."],
};

const SVG_TAGS = new Set(["svg", "g", "path", "circle", "line", "polyline", "text", "rect"]);
const SVG_NS = "http://www.w3.org/2000/svg";

function h(tag, attrs = {}, ...children) {
  const el = SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") {
      if (el.namespaceURI === SVG_NS) el.setAttribute("class", String(value));
      else el.className = value;
    } else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "text") el.textContent = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function interactiveTable(table, { placeholder = "Filter", pageSize = 25 } = {}) {
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  let query = "";
  let page = 0;
  let sortColumn = -1;
  let sortDirection = 1;
  const search = h("input", { type: "search", placeholder, "aria-label": placeholder });
  const count = h("span", { class: "form-hint" });
  const info = h("span", { class: "form-hint" });
  const prev = button("Previous", "button-ghost button-small", () => {
    page = Math.max(0, page - 1);
    draw();
  });
  const next = button("Next", "button-ghost button-small", () => {
    page += 1;
    draw();
  });
  const pager = h("div", { class: "section-toolbar" }, prev, info, next);
  const wrap = h(
    "div",
    { class: "table-controls" },
    h("div", { class: "section-toolbar" }, search, count),
    h("div", { class: "table-wrap" }, table),
    pager,
  );

  table.querySelectorAll("th").forEach((th, index) => {
    th.tabIndex = 0;
    th.setAttribute("role", "button");
    th.title = "Sort";
    const toggle = () => {
      if (sortColumn === index) sortDirection *= -1;
      else {
        sortColumn = index;
        sortDirection = 1;
      }
      page = 0;
      draw();
    };
    th.addEventListener("click", toggle);
    th.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
  });

  function draw() {
    const normalized = query.trim().toLowerCase();
    let filtered = rows.filter((row) => !normalized || row.textContent.toLowerCase().includes(normalized));
    if (sortColumn >= 0) {
      filtered = [...filtered].sort((left, right) => {
        const a = left.cells[sortColumn]?.textContent.trim() || "";
        const b = right.cells[sortColumn]?.textContent.trim() || "";
        const comparison = a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
        return comparison * sortDirection;
      });
    }
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    page = Math.min(page, pages - 1);
    const start = page * pageSize;
    const visible = filtered.slice(start, start + pageSize);
    clear(tbody);
    tbody.append(...visible);
    table.querySelectorAll("th").forEach((th, index) => {
      th.setAttribute("aria-sort", sortColumn === index ? (sortDirection > 0 ? "ascending" : "descending") : "none");
    });
    count.textContent = `${filtered.length} of ${rows.length}`;
    info.textContent = `Page ${page + 1} of ${pages}`;
    prev.disabled = page === 0;
    next.disabled = page >= pages - 1;
    pager.hidden = pages <= 1;
  }

  search.addEventListener("input", () => {
    query = search.value;
    page = 0;
    draw();
  });
  draw();
  return wrap;
}

function fmtBytes(value) {
  const n = Number(value || 0);
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let index = 0;
  let amount = n;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function fmtUptime(value) {
  let seconds = Math.max(0, Number(value || 0));
  const days = Math.floor(seconds / 86400);
  seconds %= 86400;
  const hours = Math.floor(seconds / 3600);
  seconds %= 3600;
  const minutes = Math.floor(seconds / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function relativeTime(value) {
  const date = parseDate(value);
  if (!date) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 15) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)}d ago`;
  return date.toLocaleDateString();
}

function elapsedLabel(value) {
  const date = parseDate(value);
  if (!date) return "unknown";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function localTime(value) {
  const date = parseDate(value);
  return date ? date.toLocaleString() : "—";
}

function short(value, max = 80) {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function osLabel(agent) {
  const os = String(agent?.os || "unknown").toLowerCase();
  if (os === "windows") return "Windows";
  if (os === "linux") return "Linux";
  return os || "Unknown";
}

function statusPill(text, kind = "neutral") {
  return h("span", { class: `status-pill ${kind}` }, text);
}

function progressBar(value, warn = 80, critical = 90, label = "") {
  const n = Math.max(0, Math.min(100, Number(value || 0)));
  const level = n >= critical ? " is-critical" : n >= warn ? " is-warn" : "";
  const attrs = { class: `progress${level}`, max: "100", value: String(n) };
  if (label) attrs["aria-label"] = label;
  return h("progress", attrs);
}

function metricRow(label, value, extra = "") {
  return h(
    "div",
    { class: "metric-row" },
    h("div", { class: "metric-label" }, h("span", {}, label), h("strong", {}, extra || `${Math.round(value)}%`)),
    progressBar(value, 80, 90, label),
  );
}

function toast(message, kind = "ok") {
  const item = h("div", { class: `toast ${kind === "error" ? "error" : kind === "warn" ? "warn" : ""}` }, message);
  $("toast-region").append(item);
  setTimeout(() => item.remove(), 4800);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function loadTheme() {
  let theme = "dark";
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") theme = stored;
  } catch (_error) {
    // Storage may be unavailable in private contexts.
  }
  applyTheme(theme, false);
}

function applyTheme(theme, persist = true) {
  const value = theme === "light" ? "light" : "dark";
  state.theme = value;
  document.documentElement.dataset.theme = value;
  const schemeMeta = document.querySelector('meta[name="color-scheme"]');
  if (schemeMeta) schemeMeta.setAttribute("content", value);
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.setAttribute("content", value === "light" ? "#eef2f6" : "#0b1118");
  const toggle = $("theme-toggle");
  if (toggle) {
    const light = value === "light";
    toggle.setAttribute("aria-pressed", light ? "true" : "false");
    toggle.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
    toggle.title = light ? "Switch to dark theme" : "Switch to light theme";
  }
  const icon = $("theme-toggle-icon");
  if (icon) icon.textContent = value === "light" ? "☾" : "☀";
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, value);
    } catch (_error) {
      // Storage may be unavailable in private contexts.
    }
  }
}

function toggleTheme() {
  applyTheme(state.theme === "light" ? "dark" : "light");
}

function fleetSummary(prefix = "Fleet updated") {
  const stats = state.dashboard?.stats || { total: 0, online: 0, alerts: 0 };
  return `${prefix}: ${stats.total} machine${stats.total === 1 ? "" : "s"}, ${stats.online} online, ${stats.alerts} alert${stats.alerts === 1 ? "" : "s"}.`;
}

function announceFleetStatus(message) {
  const region = $("fleet-status");
  if (!region) return;
  region.textContent = "";
  setTimeout(() => {
    region.textContent = message;
  }, 60);
}

function commandPaletteItems(query = "") {
  const pages = Object.keys(PAGE_META)
    .filter((page) => !ADMIN_PAGES.includes(page) || state.permissions.admin)
    .map((page) => {
      const [title, subtitle] = PAGE_META[page];
      return {
        type: "page",
        page,
        kindLabel: "Page",
        label: title,
        hint: subtitle,
        search: `${page} ${title} ${subtitle}`.toLowerCase(),
      };
    });
  const agents = (state.dashboard?.agents || []).map((agent) => ({
    type: "agent",
    id: agent.id,
    kindLabel: "Machine",
    label: agent.name,
    hint: `${agent.online ? "online" : "offline"} · ${osLabel(agent)} · ${agent.ip || "no address"}${agent.tags?.length ? ` · ${agent.tags.join(", ")}` : ""}`,
    search: [agent.name, agent.hostname, agent.os, agent.os_version, agent.ip, agent.mac, agent.id, ...(agent.tags || [])]
      .join(" ")
      .toLowerCase(),
  }));
  const all = [...pages, ...agents];
  const normalized = query.trim().toLowerCase();
  if (!normalized) return all;
  return all.filter((item) => item.search.includes(normalized));
}

function renderCommandPalette(query) {
  const list = $("command-palette-list");
  const input = $("command-palette-input");
  if (!list || !input) return;
  clear(list);
  state.paletteItems = commandPaletteItems(query);
  state.paletteIndex = 0;
  if (!state.paletteItems.length) {
    list.append(h("div", { class: "command-palette-empty", role: "presentation" }, "No pages or machines match."));
    input.removeAttribute("aria-activedescendant");
    return;
  }
  state.paletteItems.forEach((item, index) => {
    list.append(
      h(
        "div",
        {
          class: "command-palette-item",
          id: `palette-option-${index}`,
          role: "option",
          "aria-selected": index === 0 ? "true" : "false",
          onClick: () => runCommandPaletteItem(item),
          onMouseEnter: () => setCommandPaletteActive(index, false),
        },
        h("span", { class: "command-palette-kind" }, item.kindLabel),
        h("span", { class: "command-palette-main" }, h("strong", {}, item.label), h("small", {}, item.hint)),
      ),
    );
  });
  setCommandPaletteActive(0, false);
}

function setCommandPaletteActive(index, scroll = true) {
  const list = $("command-palette-list");
  const input = $("command-palette-input");
  if (!list || !input || !state.paletteItems.length) return;
  const next = Math.max(0, Math.min(state.paletteItems.length - 1, index));
  state.paletteIndex = next;
  list.querySelectorAll(".command-palette-item").forEach((row, rowIndex) => {
    const active = rowIndex === next;
    row.classList.toggle("is-active", active);
    row.setAttribute("aria-selected", active ? "true" : "false");
  });
  input.setAttribute("aria-activedescendant", `palette-option-${next}`);
  if (scroll) {
    const activeRow = list.querySelector(`#palette-option-${next}`);
    if (activeRow && typeof activeRow.scrollIntoView === "function") activeRow.scrollIntoView({ block: "nearest" });
  }
}

function moveCommandPalette(delta) {
  if (!state.paletteItems.length) return;
  const count = state.paletteItems.length;
  setCommandPaletteActive((state.paletteIndex + delta + count) % count);
}

function openCommandPalette() {
  const overlay = $("command-palette");
  const input = $("command-palette-input");
  if (!overlay || !input) return;
  state.paletteTrigger = document.activeElement;
  overlay.hidden = false;
  input.value = "";
  input.setAttribute("aria-expanded", "true");
  renderCommandPalette("");
  input.focus();
}

function closeCommandPalette({ restoreFocus = true } = {}) {
  const overlay = $("command-palette");
  const input = $("command-palette-input");
  if (!overlay) return;
  overlay.hidden = true;
  if (input) {
    input.value = "";
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }
  state.paletteItems = [];
  state.paletteIndex = 0;
  if (restoreFocus && state.paletteTrigger && typeof state.paletteTrigger.focus === "function") {
    state.paletteTrigger.focus();
  }
  state.paletteTrigger = null;
}

function runCommandPaletteItem(item) {
  closeCommandPalette();
  if (item.type === "page") {
    navigate(item.page);
    return;
  }
  if (item.type === "agent") {
    navigate("overview", false);
    openAgent(item.id);
  }
}

function runCommandPaletteSelection() {
  const item = state.paletteItems[state.paletteIndex];
  if (item) runCommandPaletteItem(item);
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  let body;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (state.csrf && !["GET", "HEAD"].includes(method)) headers["X-CSRF-Token"] = state.csrf;
  let response;
  try {
    response = await fetch(path, { method, headers, body, credentials: "same-origin" });
  } catch (_error) {
    throw new Error("Network error: the server is unreachable.");
  }
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    showLogin("Your session expired. Sign in again.");
    throw new Error("Authentication required.");
  }
  if (response.status === 204) return null;
  const raw = await response.text();
  let data = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch (_error) {
      data = { detail: raw };
    }
  }
  if (!response.ok) {
    const detail = data?.detail || `Request failed (${response.status}).`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_error) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "clipboard-shim";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

function showLogin(message = "") {
  stopLiveUpdates();
  stopDockerRefreshPoll();
  state.user = null;
  state.dockerHostId = null;
  state.dockerHostDetail = null;
  state.csrf = null;
  state.detailId = null;
  state.detailAgent = null;
  state.terminals = {};
  state.resetUser = null;
  state.sessionsUser = null;
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  $("login-view").hidden = false;
  $("app-view").hidden = true;
  closeDrawer();
  setText("login-error", message);
  const password = $("login-password");
  if (password) password.value = "";
  setTimeout(() => $("login-username")?.focus(), 10);
}

function updateChrome() {
  const user = state.user;
  if (!user) return;
  setText("user-name", user.display_name || user.username);
  setText("user-role", user.role);
  setText("user-avatar", (user.display_name || user.username || "?").slice(0, 1));
  const admin = Boolean(state.permissions.admin);
  document.querySelectorAll(".nav-admin").forEach((el) => {
    el.hidden = !admin;
  });
  document.querySelectorAll('[data-page="docker-hosts"]').forEach((el) => {
    el.hidden = state.settings?.docker?.enabled === false;
  });
  const securityHint = $("password-source-hint");
  if (securityHint) {
    securityHint.textContent =
      user.source === "ad"
        ? "This directory account is managed by Active Directory / Samba. Change the password there."
        : "Use at least 12 characters. Changing your password revokes other sessions.";
  }
  $("password-form")?.querySelectorAll("input, button").forEach((el) => {
    el.disabled = user.source !== "local";
  });
}

function applyPermissions() {
  const operate = Boolean(state.permissions.operate);
  const admin = Boolean(state.permissions.admin);
  document.querySelectorAll(".nav-admin").forEach((el) => {
    el.hidden = !admin;
  });
  const form = $("wake-target-form");
  if (form) form.hidden = !operate;
  $("enrollment-form")?.closest(".panel")?.toggleAttribute("hidden", !admin);
}

function enterApp(me) {
  state.user = me.user;
  state.csrf = me.csrf || me.user?.csrf;
  state.settings = me.settings;
  const role = me.user?.role;
  state.permissions =
    me.permissions || {
      viewer: true,
      operate: role === "operator" || role === "admin",
      admin: role === "admin",
    };
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  updateChrome();
  applyPermissions();
  navigate("overview", false);
  loadDashboard();
  loadProfiles(true);
  void loadSavedViewsFromServer();
  void checkForUpdates();
  startLiveUpdates();
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startPolling() {
  if (state.pollTimer || !state.user) return;
  state.pollTimer = setInterval(() => {
    if (!document.hidden && state.user) loadDashboard(true);
  }, 10000);
}

function stopLiveUpdates() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  stopPolling();
}

function startLiveUpdates() {
  if (!state.user) return;
  stopPolling();
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  if (typeof EventSource === "function") {
    try {
      const source = new EventSource("/api/events");
      state.eventSource = source;
      source.addEventListener("dashboard", (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch (_error) {
          return;
        }
        applyDashboard(data);
      });
      source.addEventListener("open", () => {
        if (state.eventSource === source) stopPolling();
      });
      source.addEventListener("auth", () => {
        if (state.eventSource !== source) return;
        // The server revalidated the session while streaming and it is no
        // longer valid. Close explicitly so EventSource does not reconnect,
        // then return to the login view instead of polling a dead session.
        source.close();
        state.eventSource = null;
        stopPolling();
        showLogin("Your session expired. Sign in again.");
      });
      source.addEventListener("error", () => {
        if (state.eventSource !== source) return;
        // EventSource retries by itself, but a failed stream may stay broken
        // (for example behind a proxy). Drop to the proven polling fallback.
        source.close();
        state.eventSource = null;
        startPolling();
      });
      return;
    } catch (_error) {
      state.eventSource = null;
    }
  }
  startPolling();
}

async function checkForUpdates() {
  const banner = $("update-banner");
  if (!banner || !state.permissions?.admin) {
    if (banner) banner.hidden = true;
    return;
  }
  try {
    const data = await api("/api/update/status");
    if (!data.update_available) {
      banner.hidden = true;
      return;
    }
    setText("update-banner-text", `Sextant ${data.latest_version} is available (you are on ${data.current_version}).`);
    const link = $("update-banner-link");
    if (data.url) {
      link.href = data.url;
      link.hidden = false;
    } else {
      link.hidden = true;
    }
    banner.hidden = false;
  } catch (_error) {
    banner.hidden = true;
  }
}

function navigate(page, load = true) {
  if (!PAGE_META[page]) page = "overview";
  if (ADMIN_PAGES.includes(page) && !state.permissions.admin) page = "overview";
  state.page = page;
  const [title, subtitle] = PAGE_META[page];
  setText("page-title", title);
  setText("page-subtitle", subtitle);
  document.querySelectorAll(".page").forEach((el) => {
    el.hidden = el.id !== `page-${page}`;
  });
  document.querySelectorAll(".nav-item[data-page]").forEach((el) => {
    const active = el.dataset.page === page;
    el.classList.toggle("is-active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("sidebar").classList.remove("is-open");
  $("sidebar-scrim").hidden = true;
  if (page !== "docker-hosts") stopDockerRefreshPoll();
  if (!load) return;
  if (page === "overview") loadDashboard();
  if (page === "wake") loadWakeTargets();
  if (page === "docker-hosts") {
    loadDockerHosts();
    if (state.dockerHostId) loadDockerHostDetail(state.dockerHostId, true);
  }
  if (page === "enroll") loadEnrollmentTokens();
  if (page === "audit") loadAudit();
  if (page === "users") loadUsers();
  if (page === "profiles") loadProfiles();
  if (page === "remote-install") loadRemoteInstall();
  if (page === "security") renderSecurity();
}

async function loadDashboard(silent = false) {
  if (state.loadingDashboard) return;
  state.loadingDashboard = true;
  try {
    const data = await api("/api/dashboard");
    applyDashboard(data);
  } catch (error) {
    if (!silent) toast(error.message, "error");
  } finally {
    state.loadingDashboard = false;
  }
}

function applyDashboard(data) {
  if (!data || !data.stats || !Array.isArray(data.agents)) return;
  state.dashboard = data;
  renderStats();
  renderAgentGrid();
  if (state.detailId) {
    const agent = data.agents.find((item) => item.id === state.detailId);
    if (!agent) closeDrawer();
    else {
      state.detailAgent = agent;
      updateDrawerChrome();
      if (!["remote", "history"].includes(state.detailTab)) renderTab();
    }
  }
}

function renderStats() {
  const stats = state.dashboard?.stats || { total: 0, online: 0, offline: 0, alerts: 0, active_commands: 0 };
  setText("stat-total", String(stats.total));
  setText("stat-online", String(stats.online));
  setText("stat-alerts", String(stats.alerts));
  setText("stat-commands", String(stats.active_commands));
  setText("stat-total-detail", stats.total ? `${stats.offline} offline` : "No agents enrolled");
  setText("stat-online-detail", stats.total ? `${Math.round((stats.online / stats.total) * 100)}% of fleet` : "Waiting for heartbeat");
  const affected = (state.dashboard?.agents || []).filter((agent) => agent.alert_count > 0).length;
  setText("stat-alerts-detail", affected ? `${affected} machine${affected === 1 ? "" : "s"} affected` : "All clear");
  const signature = `${stats.total}|${stats.online}|${stats.offline}|${stats.alerts}`;
  if (signature !== state.fleetStatusSignature) {
    state.fleetStatusSignature = signature;
    announceFleetStatus(fleetSummary());
  }
}

function renderFleetFilters(all) {
  const status = $("filter-status");
  const profile = $("filter-profile");
  const tag = $("filter-tag");
  if (!status || !profile || !tag) return;
  const selectedProfile = state.fleetFilters.profile;
  const selectedTag = state.fleetFilters.tag;
  clear(profile);
  profile.append(h("option", { value: "" }, "All profiles"));
  for (const item of state.profiles) {
    profile.append(h("option", { value: item.id }, item.name));
  }
  profile.value = selectedProfile;
  clear(tag);
  tag.append(h("option", { value: "" }, "All tags"));
  const tags = [...new Set(all.flatMap((agent) => agent.tags || []))].sort((a, b) => a.localeCompare(b));
  for (const name of tags) tag.append(h("option", { value: name }, name));
  tag.value = selectedTag;
  status.value = state.fleetFilters.status;
  const filtered = filteredAgents().length;
  setText("fleet-filter-count", `${filtered} of ${all.length} machines`);
}

function filteredAgents() {
  const agents = state.dashboard?.agents || [];
  const query = state.search.trim().toLowerCase();
  const filters = state.fleetFilters;
  return agents.filter((agent) => {
    if (filters.status === "online" && !agent.online) return false;
    if (filters.status === "offline" && agent.online) return false;
    if (filters.status === "alerts" && !agent.alert_count) return false;
    if (filters.profile && agent.profile_id !== filters.profile) return false;
    if (filters.tag && !(agent.tags || []).includes(filters.tag)) return false;
    if (!query) return true;
    return [agent.name, agent.hostname, agent.os, agent.os_version, agent.ip, agent.mac, ...(agent.tags || [])]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

function resetFleetRenderLimit() {
  state.fleetRenderLimit = FLEET_PAGE_SIZE;
}

function agentCardSignature(agent) {
  const profile = profileForAgent(agent);
  return JSON.stringify([
    state.permissions.operate,
    Math.floor(Date.now() / 60000),
    agent.id,
    agent.name,
    agent.hostname,
    agent.os,
    agent.os_version,
    agent.ip,
    agent.mac,
    agent.online,
    agent.cpu_pct,
    agent.mem_pct,
    agent.mem_used,
    agent.mem_total,
    agent.uptime_seconds,
    agent.last_seen,
    agent.service_inventory_at,
    agent.tags || [],
    (agent.disks || []).map((disk) => disk.percent || 0),
    (agent.gpus || []).map((gpu) => [gpu.name, gpu.util_pct, gpu.temp_c]),
    agent.alert_count,
    profile ? [profile.settings?.heartbeat_seconds, profile.settings?.collection?.service_inventory_seconds] : null,
  ]);
}

function captureFleetFocus(grid) {
  const active = document.activeElement;
  if (!active || !grid.contains(active)) return null;
  const card = active.closest(".agent-card");
  if (!card || !card.dataset.agentId) return null;
  return {
    agentId: card.dataset.agentId,
    select: active.classList.contains("agent-select"),
    open: active.classList.contains("agent-open"),
  };
}

function restoreFleetFocus(info) {
  if (!info) return;
  const card = state.agentCards.get(info.agentId);
  if (!card) return;
  const target = info.select
    ? card.querySelector(".agent-select")
    : info.open
      ? card.querySelector(".agent-open")
      : card;
  if (target && document.activeElement !== target) target.focus();
}

function renderFleetMore(total, shown) {
  const wrap = $("fleet-more");
  const control = $("fleet-more-button");
  if (!wrap || !control) return;
  wrap.hidden = total <= shown;
  if (!wrap.hidden) control.textContent = `Show more machines (${total - shown} remaining)`;
}

function renderAgentGrid() {
  const grid = $("agent-grid");
  if (!grid) return;
  const focusInfo = captureFleetFocus(grid);
  const all = state.dashboard?.agents || [];
  const currentIds = new Set(all.map((agent) => agent.id));
  for (const id of [...state.selectedAgents]) {
    if (!currentIds.has(id)) state.selectedAgents.delete(id);
  }
  renderFleetFilters(all);
  renderAlertCenter();
  renderBulkActions();
  const agents = filteredAgents();
  $("empty-agents").hidden = all.length > 0;
  if (all.length === 0) {
    for (const [id, node] of [...state.agentCards]) {
      node.remove();
      state.agentCards.delete(id);
      state.agentSignatures.delete(id);
    }
    if (state.fleetEmptyNode) {
      state.fleetEmptyNode.remove();
      state.fleetEmptyNode = null;
    }
    renderFleetMore(0, 0);
    return;
  }
  const visible = agents.slice(0, state.fleetRenderLimit);
  const wanted = new Set(visible.map((agent) => agent.id));
  for (const [id, node] of [...state.agentCards]) {
    if (!wanted.has(id)) {
      node.remove();
      state.agentCards.delete(id);
      state.agentSignatures.delete(id);
    }
  }
  let index = 0;
  for (const agent of visible) {
    const signature = agentCardSignature(agent);
    let node = state.agentCards.get(agent.id);
    const signatureChanged = state.agentSignatures.get(agent.id) !== signature;
    if (!node || signatureChanged) {
      const next = agentCard(agent);
      if (node) node.replaceWith(next);
      state.agentCards.set(agent.id, next);
      state.agentSignatures.set(agent.id, signature);
      node = next;
    } else {
      const checkbox = node.querySelector(".agent-select");
      if (checkbox) checkbox.checked = state.selectedAgents.has(agent.id);
    }
    const current = grid.children[index];
    if (current !== node) grid.insertBefore(node, current || null);
    index += 1;
  }
  if (agents.length === 0) {
    if (!state.fleetEmptyNode) {
      state.fleetEmptyNode = h("div", { class: "empty-inline" }, "No machines match that search.");
    }
    if (!state.fleetEmptyNode.isConnected) grid.append(state.fleetEmptyNode);
  } else if (state.fleetEmptyNode) {
    state.fleetEmptyNode.remove();
  }
  renderFleetMore(agents.length, visible.length);
  restoreFleetFocus(focusInfo);
}

function profileForAgent(agent) {
  return state.profiles.find((profile) => profile.id === agent.profile_id) || null;
}

function cadenceLabel(seconds) {
  const value = Number(seconds || 0);
  if (value >= 3600) return `${Math.round(value / 3600)}h cadence`;
  if (value >= 60) return `${Math.round(value / 60)}m cadence`;
  return `${Math.round(value)}s cadence`;
}

function staleInfo(agent) {
  const profile = profileForAgent(agent);
  const heartbeatSeconds = profile?.settings?.heartbeat_seconds || 30;
  const inventorySeconds = profile?.settings?.collection?.service_inventory_seconds || 21600;
  const now = Date.now();
  const lastSeen = Date.parse(agent.last_seen || "");
  const inventoryAt = Date.parse(agent.service_inventory_at || "");
  const heartbeatStale = Boolean(
    lastSeen && now - lastSeen > Math.max(2 * heartbeatSeconds * 1000, 90_000),
  );
  const inventoryStale = Boolean(
    inventoryAt && now - inventoryAt > inventorySeconds * 1.5 * 1000,
  );
  return { heartbeatStale, inventoryStale, heartbeatSeconds, inventorySeconds };
}

function alertIsActive(alert) {
  return !alert.acknowledged && !alert.snoozed;
}

function alertRow(agent, alert, showAgent = false) {
  const active = alertIsActive(alert);
  const actions = [];
  if (state.permissions.operate) {
    if (active) {
      actions.push(button("Acknowledge", "button-secondary button-small", () => acknowledgeAlert(agent.id, alert.key)));
      actions.push(button("Snooze", "button-ghost button-small", () => snoozeAlert(agent.id, alert.key)));
    } else {
      actions.push(button("Clear", "button-ghost button-small", () => clearAlert(agent.id, alert.key)));
    }
  }
  if (showAgent) actions.push(button("View", "button-ghost button-small", () => openAgent(agent.id)));
  const meta = [];
  if (showAgent) meta.push(agent.name);
  if (alert.acknowledged) {
    meta.push("Acknowledged · condition still active");
    if (alert.acknowledged_at) meta.push(`Acknowledged ${elapsedLabel(alert.acknowledged_at)} ago`);
  } else if (alert.snoozed) {
    meta.push(alert.snoozed_until ? `Snoozed until ${localTime(alert.snoozed_until)}` : "Snoozed");
  }
  const timing = [];
  if (alert.first_seen_at) timing.push(`First seen ${relativeTime(alert.first_seen_at)}`);
  if (alert.last_seen_at) timing.push(`Last seen ${relativeTime(alert.last_seen_at)}`);
  if (alert.first_seen_at) timing.push(`Incident age ${elapsedLabel(alert.first_seen_at)}`);
  const className = `alert-item${active && alert.level === "critical" ? " critical" : ""}${active ? "" : " muted"}${alert.acknowledged ? " acknowledged" : ""}`;
  return h(
    "div",
    { class: className, "data-alert-key": alert.key, "data-incident-id": alert.incident_id || "" },
    h("span", { class: "alert-mark" }, active ? (alert.level === "critical" ? "!" : "•") : alert.acknowledged ? "A" : "✓"),
    h(
      "div",
      { class: "alert-body" },
      meta.length ? h("span", { class: "alert-state" }, meta.join(" · ")) : null,
      h("span", {}, alert.message),
      timing.length ? h("span", { class: "alert-state alert-timing" }, timing.join(" · ")) : null,
    ),
    actions.length ? h("div", { class: "alert-actions" }, ...actions) : null,
  );
}

async function acknowledgeAlert(agentId, alertKey) {
  try {
    await api(`/api/agents/${encodeURIComponent(agentId)}/alerts/${encodeURIComponent(alertKey)}/ack`, {
      method: "POST",
      body: {},
    });
    toast("Alert acknowledged.");
    await loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function snoozeAlert(agentId, alertKey) {
  const answer = window.prompt("Snooze this alert for how many minutes? (5–10080)", "60");
  if (answer === null) return;
  const minutes = Number(answer);
  if (!Number.isInteger(minutes) || minutes < 5 || minutes > 10080) {
    toast("Snooze must be between 5 and 10080 minutes.", "error");
    return;
  }
  try {
    await api(`/api/agents/${encodeURIComponent(agentId)}/alerts/${encodeURIComponent(alertKey)}/snooze`, {
      method: "POST",
      body: { minutes },
    });
    toast(`Alert snoozed for ${minutes} minutes.`);
    await loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function clearAlert(agentId, alertKey) {
  try {
    await api(`/api/agents/${encodeURIComponent(agentId)}/alerts/${encodeURIComponent(alertKey)}`, {
      method: "DELETE",
    });
    toast("Alert cleared.");
    await loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderAlertCenter() {
  const sectionEl = $("alert-center");
  const list = $("alert-center-list");
  if (!sectionEl || !list) return;
  const agents = (state.dashboard?.agents || []).filter((agent) => (agent.alerts || []).length);
  sectionEl.hidden = agents.length === 0;
  const active = agents.reduce(
    (total, agent) => total + (agent.alerts || []).filter((alert) => alertIsActive(alert)).length,
    0,
  );
  setText(
    "alert-center-count",
    agents.length ? `${active} active · ${agents.length} machine${agents.length === 1 ? "" : "s"}` : "",
  );
  clear(list);
  for (const agent of agents) {
    for (const alert of agent.alerts || []) {
      list.append(alertRow(agent, alert, true));
    }
  }
}

const BULK_BUTTON_IDS = [
  "bulk-refresh",
  "bulk-wake",
  "bulk-reboot",
  "bulk-shutdown",
  "bulk-service-start",
  "bulk-service-stop",
  "bulk-service-restart",
  "bulk-profile-apply",
  "bulk-clear",
];

function renderBulkActions() {
  const bar = $("bulk-actions");
  if (!bar) return;
  const selected = [...state.selectedAgents];
  bar.hidden = !state.permissions.operate;
  setText(
    "bulk-count",
    selected.length ? `${selected.length} selected` : "0 selected · select machines to enable bulk actions",
  );
  updateBulkControls();
}

function updateBulkControls() {
  const selected = state.selectedAgents.size;
  const noSelection = selected === 0;
  const serviceName = ($("bulk-service-name")?.value || "").trim();
  const profileSlug = $("bulk-profile")?.value || "";
  for (const id of BULK_BUTTON_IDS) {
    const control = $(id);
    if (!control) continue;
    let disabled = noSelection;
    if (["bulk-service-start", "bulk-service-stop", "bulk-service-restart"].includes(id)) {
      disabled = disabled || !serviceName;
    }
    if (id === "bulk-profile-apply") {
      disabled = disabled || !profileSlug || !state.permissions.admin;
    }
    control.disabled = disabled;
  }
}

function selectedAgents() {
  const all = state.dashboard?.agents || [];
  return all.filter((agent) => state.selectedAgents.has(agent.id));
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function bulkTargets(capability) {
  const allowed = [];
  const skipped = [];
  for (const agent of selectedAgents()) {
    const blocked =
      (capability === "power" && agent.policy?.allow_power === false) ||
      (capability === "service" && agent.policy?.allow_service === false);
    if (!agent.online || blocked) skipped.push(agent);
    else allowed.push(agent);
  }
  return { allowed, skipped };
}

function clearBulkResult() {
  const el = $("bulk-result");
  if (!el) return;
  el.hidden = true;
  el.className = "bulk-result";
  clear(el);
}

function bulkCountsText(summary) {
  return [
    `${summary.queued || 0} queued`,
    `${summary.sent || 0} sent`,
    `${summary.updated || 0} updated`,
    `${summary.failed || 0} failed`,
  ].join(" · ");
}

function renderBulkResult(label, result) {
  const el = $("bulk-result");
  if (!el) return;
  const summary = result.summary || { total: (result.results || []).length, failed: 0 };
  const failed = (result.results || []).filter((item) => item.status === "failed");
  el.hidden = false;
  el.className = `bulk-result${failed.length ? " has-failures" : ""}`;
  clear(el);
  el.append(
    h("strong", {}, `${label}: ${summary.total || 0} machine${summary.total === 1 ? "" : "s"}`),
    h("span", { class: "form-hint" }, bulkCountsText(summary)),
  );
  if (failed.length) {
    const list = h("ul", { class: "bulk-failures" });
    for (const item of failed) {
      list.append(h("li", {}, `${item.name || item.agent_id}: ${item.message || "failed"}`));
    }
    el.append(list);
  }
}

async function runBulk(label, action, agents, args = {}, note = "") {
  if (!agents.length) return { results: [], summary: { total: 0, queued: 0, sent: 0, updated: 0, failed: 0 } };
  clearBulkResult();
  try {
    const result = await api("/api/agents/bulk", {
      method: "POST",
      body: { action, agent_ids: agents.map((agent) => agent.id), args },
    });
    renderBulkResult(label, result);
    const summary = result.summary || {};
    const suffix = note ? ` ${note}` : "";
    if (summary.failed) {
      toast(`${label}: ${summary.failed} failed. See the per-machine result summary.${suffix}`, "warn");
    } else {
      toast(`${label}: ${bulkCountsText(summary)}.${suffix}`);
    }
    return result;
  } catch (error) {
    const el = $("bulk-result");
    if (el) {
      el.hidden = false;
      el.className = "bulk-result has-failures";
      clear(el);
      el.append(
        h("strong", {}, `${label}: request failed`),
        h("span", { class: "form-hint" }, error.message),
      );
    }
    toast(`${label} failed: ${error.message}`, "error");
    return { results: [], summary: { total: agents.length, queued: 0, sent: 0, updated: 0, failed: agents.length } };
  }
}

async function bulkRefresh() {
  const agents = selectedAgents();
  if (!agents.length) return;
  await runBulk("Telemetry refresh", "refresh", agents);
  loadDashboard(true);
}

async function bulkWake() {
  const agents = selectedAgents();
  if (!agents.length) return;
  const offline = agents.filter((agent) => !agent.online && agent.mac);
  if (!offline.length) {
    toast("No selected machines needed a wake packet.", "warn");
    return;
  }
  await runBulk("Wake", "wake", offline);
}

async function bulkPower(action) {
  if (!state.permissions.operate) return toast("Operator access required.", "error");
  const selected = selectedAgents();
  if (!selected.length) return;
  const { allowed, skipped } = bulkTargets("power");
  const verb = action === "shutdown" ? "Shut down" : "Reboot";
  if (!allowed.length) {
    toast(`No selected machines are online with power actions allowed. ${plural(skipped.length, "machine")} skipped.`, "warn");
    return;
  }
  const note = skipped.length ? `${plural(skipped.length, "offline or blocked machine")} skipped.` : "";
  const confirmed = await confirmAction(
    `${verb} ${plural(allowed.length, "machine")}?`,
    `The agents will schedule ${action} in 30 seconds. Unsaved work on those machines may be lost.${note ? ` ${note}` : ""}`,
    verb,
  );
  if (!confirmed) return;
  await runBulk(verb, action, allowed, {}, note);
  loadDashboard(true);
}

async function bulkService(action) {
  if (!state.permissions.operate) return toast("Operator access required.", "error");
  const input = $("bulk-service-name");
  const name = (input?.value || "").trim();
  if (!name) {
    toast("Enter a service name first.", "warn");
    input?.focus();
    return;
  }
  const { allowed, skipped } = bulkTargets("service");
  const verb = action.charAt(0).toUpperCase() + action.slice(1);
  if (!allowed.length) {
    toast(`No selected machines are online with service control allowed. ${plural(skipped.length, "machine")} skipped.`, "warn");
    return;
  }
  const note = skipped.length ? `${plural(skipped.length, "offline or blocked machine")} skipped.` : "";
  const confirmed = await confirmAction(
    `${verb} ${name} on ${plural(allowed.length, "machine")}?`,
    `Queue a ${action} command for the ${name} service on every eligible selected machine.${note ? ` ${note}` : ""}`,
    `${verb} service`,
  );
  if (!confirmed) return;
  await runBulk(`${verb} ${name}`, `service_${action}`, allowed, { name }, note);
  loadDashboard(true);
}

async function bulkApplyProfile() {
  if (!state.permissions.admin) return toast("Administrator access required.", "error");
  const selected = selectedAgents();
  if (!selected.length) return;
  const select = $("bulk-profile");
  const slug = select?.value || "";
  if (!slug) {
    toast("Choose a profile first.", "warn");
    select?.focus();
    return;
  }
  const profile = state.profiles.find((item) => item.slug === slug);
  const result = await runBulk(`Profile ${profile?.name || slug}`, "profile_apply", selected, { profile: slug });
  if (!result.summary?.failed) {
    await loadProfiles(true);
    loadDashboard(true);
  }
}

function agentCard(agent) {
  const disk = (agent.disks || []).reduce((worst, item) => Math.max(worst, Number(item.percent || 0)), 0);
  const gpu = (agent.gpus || [])[0];
  const stale = staleInfo(agent);
  const card = h(
    "article",
    {
      class: `agent-card${agent.online ? "" : " is-offline"}${stale.heartbeatStale || stale.inventoryStale ? " is-stale" : ""}`,
      dataset: { agentId: agent.id },
      onClick: (event) => {
        if (event.target.closest("button, input, select, textarea, a")) return;
        openAgent(agent.id);
      },
    },
    h(
      "div",
      { class: "agent-card-head" },
      h(
        "div",
        { class: "agent-name" },
        state.permissions.operate
          ? h("input", {
              type: "checkbox",
              class: "agent-select",
              "aria-label": `Select ${agent.name}`,
              checked: state.selectedAgents.has(agent.id),
              onClick: (event) => {
                event.stopPropagation();
                if (event.target.checked) state.selectedAgents.add(agent.id);
                else state.selectedAgents.delete(agent.id);
                renderBulkActions();
              },
            })
          : null,
        h(
          "div",
          {},
          h(
            "h3",
            {},
            h(
              "button",
              {
                class: "agent-open",
                type: "button",
                "aria-label": `Open ${agent.name} details`,
                onClick: (event) => {
                  event.stopPropagation();
                  openAgent(agent.id);
                },
              },
              agent.name,
            ),
          ),
          h("p", { class: "agent-os" }, `${osLabel(agent)}${agent.os_version ? ` · ${short(agent.os_version, 38)}` : ""}`),
        ),
      ),
      h(
        "div",
        { class: "button-row" },
        stale.heartbeatStale ? h("span", { class: "tag tag-warn" }, "stale heartbeat") : null,
        stale.inventoryStale ? h("span", { class: "tag tag-warn" }, "stale inventory") : null,
        agent.alert_count ? h("span", { class: "alert-dot", title: `${agent.alert_count} alerts` }, String(agent.alert_count)) : null,
        statusPill(agent.online ? "online" : "offline", agent.online ? "online" : "offline"),
      ),
    ),
    h("p", { class: "agent-ip" }, agent.ip || "no address"),
    h(
      "div",
      {},
      metricRow("CPU", Number(agent.cpu_pct || 0)),
      metricRow("Memory", Number(agent.mem_pct || 0), `${fmtBytes(agent.mem_used)} / ${fmtBytes(agent.mem_total)}`),
    ),
    h(
      "div",
      { class: "agent-facts" },
      h("div", { class: "fact" }, h("span", {}, "Primary disk"), h("strong", {}, disk ? `${Math.round(disk)}% used` : "No data")),
      h(
        "div",
        { class: "fact" },
        h("span", {}, "GPU"),
        h("strong", {}, gpu ? `${Math.round(gpu.util_pct || 0)}% · ${Math.round(gpu.temp_c || 0)}°C` : "No GPU data"),
      ),
      h("div", { class: "fact" }, h("span", {}, "Uptime"), h("strong", {}, fmtUptime(agent.uptime_seconds))),
      h(
        "div",
        { class: "fact" },
        h("span", {}, "Heartbeat"),
        h("strong", {}, agent.online ? "now" : relativeTime(agent.last_seen), stale.heartbeatStale ? h("span", { class: "tag tag-warn" }, "stale") : null),
      ),
      h(
        "div",
        { class: "fact" },
        h("span", {}, "Inventory updated"),
        h(
          "strong",
          {},
          agent.service_inventory_at ? `${relativeTime(agent.service_inventory_at)} · ${cadenceLabel(stale.inventorySeconds)}` : "never",
          stale.inventoryStale ? h("span", { class: "tag tag-warn" }, "stale") : null,
        ),
      ),
    ),
    h(
      "div",
      { class: "agent-foot" },
      h("div", { class: "tag-row" }, ...(agent.tags || []).slice(0, 3).map((tag) => h("span", { class: "tag" }, tag))),
      h("small", {}, `agent v${agent.agent_version || "unknown"} · ${agent.mac || agent.id}`),
    ),
  );
  return card;
}

function openAgent(id) {
  let agent = (state.dashboard?.agents || []).find((item) => item.id === id);
  if (!agent) {
    toast("Machine not found. Refresh and try again.", "warn");
    return;
  }
  state.drawerTrigger = document.activeElement;
  state.detailId = id;
  state.detailAgent = agent;
  state.detailTab = "overview";
  $("agent-drawer").hidden = false;
  document.body.classList.add("drawer-open");
  renderDrawer();
  loadAgentDetail(id);
  document.addEventListener("keydown", drawerTrapHandler);
  setTimeout(() => {
    const first = $("agent-drawer").querySelector("button, input, select, textarea, [tabindex]:not([tabindex='-1'])");
    if (first) first.focus();
  }, 20);
}

function drawerTrapHandler(event) {
  if (event.key !== "Tab") return;
  const drawer = $("agent-drawer");
  if (!drawer || drawer.hidden) return;
  const focusable = [...drawer.querySelectorAll("button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])")].filter(
    (el) => !el.disabled && el.offsetParent !== null,
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function closeDrawer() {
  $("agent-drawer").hidden = true;
  document.body.classList.remove("drawer-open");
  document.removeEventListener("keydown", drawerTrapHandler);
  state.detailId = null;
  state.detailAgent = null;
  if (state.drawerTrigger && typeof state.drawerTrigger.focus === "function") {
    state.drawerTrigger.focus();
  }
  state.drawerTrigger = null;
}

function updateDrawerChrome() {
  const agent = state.detailAgent;
  if (!agent) return;
  setText("drawer-title", agent.name);
  setText("drawer-os", `${osLabel(agent)} · ${agent.online ? "online" : "offline"}`);
  setText("drawer-meta", `${agent.ip || "no address"} · ${agent.os_version || "unknown OS"} · ${agent.id}`);
  renderDrawerActions(agent);
  document.querySelectorAll("#drawer-tabs .tab").forEach((el) => {
    el.classList.toggle("is-active", el.dataset.tab === state.detailTab);
  });
}

function renderDrawerActions(agent) {
  const actions = $("drawer-actions");
  clear(actions);
  if (!state.permissions.operate) {
    actions.append(h("span", { class: "form-hint" }, "Read-only access. Ask an administrator for the operator role to run actions."));
    return;
  }
  const powerAllowed = agent.policy?.allow_power !== false;
  if (!agent.online) {
    if (agent.mac) actions.append(button("Wake", "button-secondary", () => wakeAgent(agent)));
    actions.append(h("span", { class: "form-hint" }, "Offline: shell and service actions are disabled until the next heartbeat."));
    if (powerAllowed) actions.append(button("Cancel power action", "button-ghost", () => queuePower(agent, "cancel")));
  } else {
    actions.append(button("Refresh telemetry", "button-secondary", () => sendCommand(agent.id, "refresh", {}, "Metrics refresh queued.")));
    if (powerAllowed) {
      actions.append(button("Cancel power action", "button-ghost", () => queuePower(agent, "cancel")));
      actions.append(button("Reboot", "button-secondary", () => queuePower(agent, "reboot")));
      actions.append(button("Shut down", "button-danger", () => queuePower(agent, "shutdown")));
    }
  }
  if (state.permissions.admin) {
    if (state.profiles.length) {
      const select = h(
        "select",
        { class: "field", "aria-label": "Agent profile" },
        ...state.profiles.map((profile) => h("option", { value: profile.slug }, `${profile.name} (${profile.slug})`)),
      );
      const current = state.profiles.find((profile) => profile.id === agent.profile_id);
      select.value = current ? current.slug : "workstation";
      select.addEventListener("change", async () => {
        try {
          await api(`/api/agents/${agent.id}`, { method: "PATCH", body: { profile: select.value } });
          toast(`Profile set to ${select.value}.`);
          loadAgentDetail(agent.id);
          loadDashboard(true);
        } catch (error) {
          toast(error.message, "error");
        }
      });
      actions.append(h("label", { class: "field" }, h("span", {}, "Profile"), select));
    }
    actions.append(button("Edit policy", "button-secondary", () => openAgentEditDialog(agent)));
    actions.append(button("Delete agent", "button-ghost", () => deleteAgent(agent)));
  }
}

function button(label, className, handler, disabled = false) {
  return h("button", { class: `button ${className}`, type: "button", onClick: handler, disabled }, label);
}

function renderDrawer() {
  const agent = state.detailAgent;
  if (!agent) return;
  updateDrawerChrome();
  renderTab();
}

function renderTab() {
  const body = $("drawer-body");
  if (!state.detailAgent) return;
  clear(body);
  switch (state.detailTab) {
    case "overview": return renderOverview(body);
    case "disks": return renderDisks(body);
    case "gpu": return renderGpu(body);
    case "services": return renderServices(body);
    case "startup": return renderStartup(body);
    case "packages": return renderPackages(body);
    case "remote": return renderRemote(body);
    case "history": return renderHistory(body);
    case "trends": return renderTrends(body);
    default: return renderOverview(body);
  }
}

function section(eyebrow, title, ...children) {
  return h(
    "section",
    { class: "detail-section" },
    h("div", { class: "detail-section-head" }, h("div", {}, h("p", { class: "eyebrow" }, eyebrow), h("h3", {}, title))),
    ...children,
  );
}

function infoTile(label, value) {
  return h("div", { class: "info-tile" }, h("span", {}, label), h("strong", { title: String(value) }, value || "—"));
}

function renderOverview(body) {
  const agent = state.detailAgent;
  const alerts = agent.alerts || [];
  const stale = staleInfo(agent);
  body.append(
    section(
      "Identity",
      "System",
      h(
        "div",
        { class: "info-grid" },
        infoTile("Hostname", agent.hostname),
        infoTile("Address", agent.ip),
        infoTile("Operating system", agent.os_version || osLabel(agent)),
        infoTile("Architecture", agent.arch),
        infoTile("MAC address", agent.mac),
        infoTile("Agent version", agent.agent_version),
        infoTile("Enrolled", localTime(agent.enrolled_at)),
        infoTile("Last heartbeat", `${agent.online ? "just now" : relativeTime(agent.last_seen)}${stale.heartbeatStale ? " · stale" : ""}`),
        infoTile(
          "Inventory updated",
          agent.service_inventory_at
            ? `${relativeTime(agent.service_inventory_at)} · ${cadenceLabel(stale.inventorySeconds)}${stale.inventoryStale ? " · stale" : ""}`
            : "never",
        ),
        infoTile("Uptime", fmtUptime(agent.uptime_seconds)),
        infoTile("CPU model", agent.extra?.cpu_model || "—"),
        infoTile("Machine ID", agent.machine_id || "—"),
      ),
    ),
    section(
      "Policy",
      "Remote action policy",
      h(
        "div",
        { class: "info-grid" },
        infoTile("Remote shell", agent.policy?.allow_shell === false ? "Blocked" : "Allowed"),
        infoTile("Power actions", agent.policy?.allow_power === false ? "Blocked" : "Allowed"),
        infoTile("Service control", agent.policy?.allow_service === false ? "Blocked" : "Allowed"),
        infoTile("Watched services", (agent.watch_services || []).join(", ") || "None"),
      ),
      h("p", { class: "form-hint" }, "Server policy applies on the next heartbeat, but a local agent config can still deny an action."),
    ),
    section(
      "Live telemetry",
      "Resource use",
      metricRow("CPU", Number(agent.cpu_pct || 0)),
      h("div", { class: "metric-row" }, h("div", { class: "metric-label" }, h("span", {}, "Memory"), h("strong", {}, `${fmtBytes(agent.mem_used)} / ${fmtBytes(agent.mem_total)}`)), progressBar(agent.mem_pct, 80, 90, "Memory")),
      h("div", { class: "metric-row" }, h("div", { class: "metric-label" }, h("span", {}, "Primary disk"), h("strong", {}, `${Math.round(Math.max(0, ...(agent.disks || []).map((d) => Number(d.percent || 0))))}%`)), progressBar(Math.max(0, ...(agent.disks || []).map((d) => Number(d.percent || 0))), 80, 90, "Primary disk")),
    ),
  );
  if (alerts.length) {
    const activeAlerts = alerts.filter((alert) => alertIsActive(alert)).length;
    body.append(
      section(
        "Attention",
        `${activeAlerts} active health alert${activeAlerts === 1 ? "" : "s"}`,
        h("div", { class: "alert-stack" }, ...alerts.map((alert) => alertRow(agent, alert))),
      ),
    );
  } else {
    body.append(section("Attention", "Health", h("div", { class: "empty-inline" }, "No disk, memory, GPU, or monitored-service alerts.")));
  }
  if (agent.notes) body.append(section("Notes", "Operator notes", h("p", { class: "form-hint" }, agent.notes)));
  renderTrend(body, agent);
}

function sparkline(values, color) {
  const numeric = values.map((value) =>
    value === null || value === undefined || value === "" ? null : Number(value),
  );
  const usable = numeric.filter((value) => Number.isFinite(value));
  const max = Math.max(1, ...usable);
  const min = Math.min(0, ...usable);
  const width = 180;
  const height = 38;
  const span = Math.max(1, max - min);
  const xFor = (index) => (numeric.length === 1 ? 0 : (index / (numeric.length - 1)) * width);
  const yFor = (value) => height - ((value - min) / span) * (height - 4) - 2;
  const segments = [];
  let segment = [];
  numeric.forEach((value, index) => {
    if (!Number.isFinite(value)) {
      if (segment.length) {
        segments.push(segment);
        segment = [];
      }
      return;
    }
    segment.push(`${xFor(index).toFixed(1)},${yFor(value).toFixed(1)}`);
  });
  if (segment.length) segments.push(segment);
  return h(
    "svg",
    { class: "sparkline", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "telemetry trend" },
    ...segments.map((points) =>
      h("polyline", { points: points.join(" "), fill: "none", stroke: color, "stroke-width": "2", "stroke-linejoin": "round", "stroke-linecap": "round" }),
    ),
  );
}

function telemetryHasSamples(points) {
  return (points || []).some((point) =>
    ["cpu_pct", "mem_pct", "disk_max_pct", "gpu_max_util", "gpu_max_temp"].some((key) => {
      const value = point?.[key];
      return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
    }),
  );
}

async function renderTrend(body, agent) {
  const host = h("div", { class: "service-list" }, h("div", { class: "empty-inline" }, "Loading 24h trend…"));
  body.append(section("History", "24-hour trend", h("p", { class: "form-hint" }, "Latest samples from telemetry history. Gaps indicate offline periods."), host));
  const detailId = agent.id;
  try {
    const data = await api(`/api/agents/${detailId}/history?hours=24&points=60`);
    if (state.detailId !== detailId || state.detailTab !== "overview") return;
    clear(host);
    const points = data.points || [];
    if (!telemetryHasSamples(points)) {
      host.append(h("div", { class: "empty-inline" }, "No telemetry history yet."));
      return;
    }
    const series = [
      ["CPU %", points.map((point) => point.cpu_pct), "var(--accent)"],
      ["Memory %", points.map((point) => point.mem_pct), "var(--blue)"],
      ["Disk max %", points.map((point) => point.disk_max_pct), "var(--amber)"],
      ["GPU util %", points.map((point) => point.gpu_max_util), "var(--purple)"],
      ["GPU temp °C", points.map((point) => point.gpu_max_temp), "var(--red)"],
    ];
    for (const [label, values, color] of series) {
      const usable = values.filter((value) => value !== null && value !== undefined);
      if (!usable.length) continue;
      const latest = usable[usable.length - 1];
      host.append(
        h(
          "div",
          { class: "sparkline-row" },
          h("div", { class: "sparkline-label" }, h("span", {}, label), h("strong", {}, `${Number(latest).toFixed(1)}`)),
          sparkline(values, color),
        ),
      );
    }
    if (!host.childElementCount) host.append(h("div", { class: "empty-inline" }, "No numeric trend data yet."));
  } catch (error) {
    if (state.detailId !== detailId || state.detailTab !== "overview") return;
    clear(host);
    host.append(h("div", { class: "empty-inline" }, error.message));
  }
}

const TREND_RANGES = {
  "24": { hours: 24, label: "24h", points: 120 },
  "168": { hours: 168, label: "7d", points: 180 },
};

const TREND_SERIES = [
  { key: "cpu_pct", title: "CPU %", unit: "%", color: "var(--accent)", domain: [0, 100], note: "Average processor load" },
  { key: "mem_pct", title: "Memory %", unit: "%", color: "var(--blue)", domain: [0, 100], note: "Used physical memory" },
  { key: "disk_max_pct", title: "Disk max %", unit: "%", color: "var(--amber)", domain: [0, 100], note: "Fullest filesystem" },
  { key: "gpu_max_util", title: "GPU utilization %", unit: "%", color: "var(--purple)", domain: [0, 100], note: "Busiest GPU" },
  { key: "gpu_max_temp", title: "GPU temperature", unit: "°C", color: "var(--red)", domain: null, note: "Hottest GPU" },
];

function trendContextActive(agentId, rangeKey) {
  return state.detailId === agentId && state.detailTab === "trends" && state.trendRange === rangeKey;
}

function chartAxisValue(value, unit = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number.toFixed(Math.abs(number) >= 10 ? 0 : 1)}${unit}`;
}

function fmtChartValue(value, unit = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "No data";
  return `${Math.abs(number) >= 100 ? number.toFixed(0) : number.toFixed(1)}${unit}`;
}

function chartTimeLabel(value, rangeKey) {
  const date = parseDate(value);
  if (!date) return "—";
  if (rangeKey === "168") {
    return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function detailChart(values, spec, points, rangeKey) {
  const width = 640;
  const height = 220;
  const pad = { left: 52, right: 16, top: 16, bottom: 34 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const numeric = values.map((value) => (value === null || value === undefined || value === "" ? null : Number(value)));
  const usable = numeric.filter((value) => Number.isFinite(value));
  let min = spec.domain ? spec.domain[0] : Math.min(...usable);
  let max = spec.domain ? spec.domain[1] : Math.max(...usable);
  if (!spec.domain) {
    const spread = Math.max(1, max - min);
    min -= spread * 0.12;
    max += spread * 0.12;
  }
  const span = Math.max(0.0001, max - min);
  const times = (points || []).map((point) => parseDate(point?.at)?.getTime() ?? null);
  const validTimes = times.filter((value) => Number.isFinite(value));
  const minTime = validTimes.length ? Math.min(...validTimes) : null;
  const maxTime = validTimes.length ? Math.max(...validTimes) : null;
  const timeSpan = minTime !== null && maxTime !== null ? maxTime - minTime : 0;
  const xFor = (index) => {
    const time = times[index];
    if (Number.isFinite(time) && timeSpan > 0) {
      return pad.left + ((time - minTime) / timeSpan) * plotWidth;
    }
    return numeric.length <= 1 ? pad.left + plotWidth / 2 : pad.left + (index / (numeric.length - 1)) * plotWidth;
  };
  const yFor = (value) => pad.top + plotHeight - ((Math.min(max, Math.max(min, value)) - min) / span) * plotHeight;
  const segments = [];
  let segment = [];
  numeric.forEach((value, index) => {
    if (!Number.isFinite(value)) {
      if (segment.length) {
        segments.push(segment);
        segment = [];
      }
      return;
    }
    segment.push([xFor(index), yFor(value)]);
  });
  if (segment.length) segments.push(segment);
  const gridLines = [];
  const axisLabels = [];
  for (const tick of [max, (max + min) / 2, min]) {
    const y = yFor(tick);
    gridLines.push(h("line", { class: "chart-grid", x1: pad.left, y1: y, x2: pad.left + plotWidth, y2: y }));
    axisLabels.push(
      h("text", { class: "chart-axis-label", x: pad.left - 8, y: y + 3, "text-anchor": "end" }, chartAxisValue(tick, spec.unit)),
    );
  }
  const xTicks = [...new Set([0, Math.floor((numeric.length - 1) / 2), numeric.length - 1])].filter((index) => index >= 0);
  const xLabels = xTicks.map((index) => {
    const ratio = numeric.length <= 1 ? 0.5 : index / (numeric.length - 1);
    const time = times[index];
    const x = Number.isFinite(time) && timeSpan > 0 ? xFor(index) : pad.left + ratio * plotWidth;
    const anchor = ratio <= 0 ? "start" : ratio >= 1 ? "end" : "middle";
    return h(
      "text",
      { class: "chart-axis-label", x, y: height - 10, "text-anchor": anchor },
      chartTimeLabel(points[index]?.at, rangeKey),
    );
  });
  const lines = segments.map((linePoints) =>
    h("polyline", {
      points: linePoints.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "),
      fill: "none",
      stroke: spec.color,
      "stroke-width": "2",
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    }),
  );
  const dots = numeric
    .map((value, index) =>
      Number.isFinite(value)
        ? h("circle", { class: "chart-point", cx: xFor(index), cy: yFor(value), r: 1.6, fill: spec.color })
        : null,
    )
    .filter(Boolean);
  return h(
    "svg",
    { class: "detail-chart", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${spec.title} history` },
    ...gridLines,
    ...axisLabels,
    ...xLabels,
    ...lines,
    ...dots,
  );
}

function trendChartCard(spec, values, points, rangeKey) {
  const numeric = (values || []).map((value) =>
    value === null || value === undefined || value === "" ? null : Number(value),
  );
  const usable = numeric.filter((value) => Number.isFinite(value));
  const latest = usable.length ? usable[usable.length - 1] : null;
  const card = h(
    "div",
    { class: "chart-card", dataset: { series: spec.key } },
    h(
      "div",
      { class: "chart-head" },
      h("div", {}, h("strong", {}, spec.title), h("span", { class: "form-hint" }, spec.note)),
      h("span", { class: "chart-latest" }, fmtChartValue(latest, spec.unit)),
    ),
  );
  if (!usable.length) {
    card.append(h("div", { class: "empty-inline" }, "No samples for this series in the selected range."));
    return card;
  }
  card.append(detailChart(numeric, spec, points, rangeKey));
  card.append(
    h(
      "div",
      { class: "chart-foot" },
      h("span", {}, `min ${fmtChartValue(Math.min(...usable), spec.unit)}`),
      h("span", {}, `latest ${fmtChartValue(latest, spec.unit)}`),
      h("span", {}, `max ${fmtChartValue(Math.max(...usable), spec.unit)}`),
    ),
  );
  return card;
}

async function loadTrendCharts(host, agent, rangeKey) {
  const range = TREND_RANGES[rangeKey];
  try {
    const data = await api(`/api/agents/${agent.id}/history?hours=${range.hours}&points=${range.points}`);
    if (!trendContextActive(agent.id, rangeKey)) return;
    clear(host);
    const points = data.points || [];
    if (!telemetryHasSamples(points)) {
      host.append(
        h(
          "div",
          { class: "empty-inline trend-empty" },
          `No telemetry history in the last ${range.label}. Samples appear after this machine reports a heartbeat.`,
        ),
      );
      return;
    }
    for (const spec of TREND_SERIES) {
      host.append(trendChartCard(spec, points.map((point) => point[spec.key]), points, rangeKey));
    }
  } catch (error) {
    if (!trendContextActive(agent.id, rangeKey)) return;
    clear(host);
    host.append(h("div", { class: "empty-inline" }, error.message));
  }
}

function timelineStatusKind(kind, status) {
  if (status === "critical") return "critical";
  if (status === "warning") return "warning";
  if (["success", "online", "succeeded"].includes(status)) return "online";
  if (status === "failed") return "critical";
  if (status === "cancelled") return "warning";
  if (kind === "alert") return "neutral";
  return "neutral";
}

function timelineEntryClass(kind) {
  const base = `timeline-entry timeline-${kind}`;
  return kind === "command" || kind === "alert" ? base : `${base} timeline-event`;
}

function buildTimelineEntries(commands, events) {
  const entries = [];
  for (const command of commands) {
    const at = command.created_at || command.updated_at || null;
    entries.push({
      at,
      sort: parseDate(at)?.getTime() || 0,
      kind: "command",
      status: command.status || "unknown",
      description: commandDescription(command),
    });
  }
  for (const event of events) {
    const at = event.at || null;
    entries.push({
      at,
      sort: parseDate(at)?.getTime() || 0,
      kind: event.kind || "event",
      status: event.severity || "info",
      description: event.title || event.kind || "State change",
      detail: event.detail,
    });
  }
  entries.sort((left, right) => right.sort - left.sort);
  return entries;
}

function renderTimelineEntries(host, countEl, commands, events, error = "") {
  clear(host);
  const entries = buildTimelineEntries(commands, events);
  if (error) host.append(h("div", { class: "empty-inline" }, error));
  if (!entries.length) {
    host.append(h("div", { class: "empty-inline" }, "No commands or state changes recorded for this machine yet."));
  } else {
    host.append(
      ...entries.map((entry) =>
        h(
          "div",
          { class: timelineEntryClass(entry.kind) },
          h("span", { class: "timeline-time" }, entry.at ? localTime(entry.at) : "—"),
          h("span", { class: `timeline-kind ${entry.kind}` }, entry.kind),
          statusPill(entry.status, timelineStatusKind(entry.kind, entry.status)),
          h("span", { class: "timeline-desc", title: entry.description }, entry.description),
        ),
      ),
    );
  }
  if (countEl) countEl.textContent = `${entries.length} event${entries.length === 1 ? "" : "s"}`;
}

function renderWhatChanged(host, events, range, error = "") {
  clear(host);
  if (error) {
    host.append(h("div", { class: "empty-inline" }, error));
    return;
  }
  if (!events.length) {
    host.append(
      h("div", { class: "empty-inline what-changed-empty" }, `No state changes recorded in the last ${range.label}.`),
    );
    return;
  }
  const summary = `${events.length} state change${events.length === 1 ? "" : "s"} in the last ${range.label}`;
  const items = events.slice(0, 6).map((event) =>
    h(
      "div",
      { class: "what-changed-item" },
      h("span", { class: "what-changed-time" }, localTime(event.at)),
      h("span", { class: `timeline-kind ${event.kind || "event"}` }, event.kind || "event"),
      h("span", { class: "what-changed-title", title: event.title || "" }, event.title || "State change"),
    ),
  );
  host.append(h("p", { class: "form-hint what-changed-summary" }, summary), h("div", { class: "what-changed-list" }, ...items));
}

async function loadTrendActivity(agent, rangeKey, whatHost, timelineHost, countEl) {
  const range = TREND_RANGES[rangeKey];
  let events = [];
  let error = "";
  try {
    const data = await api(`/api/agents/${encodeURIComponent(agent.id)}/events?hours=${range.hours}&limit=100`);
    if (!trendContextActive(agent.id, rangeKey)) return;
    events = data.events || [];
  } catch (requestError) {
    if (!trendContextActive(agent.id, rangeKey)) return;
    error = requestError.message;
  }
  const commands = state.agentCommands[agent.id] || [];
  renderWhatChanged(whatHost, events, range, error);
  renderTimelineEntries(timelineHost, countEl, commands, events, error);
}

function renderTimelineSection(body, agent, rangeKey) {
  const range = TREND_RANGES[rangeKey];
  const count = h("span", { class: "form-hint timeline-count" }, "Loading events…");
  const host = h("div", { class: "timeline" }, h("div", { class: "empty-inline" }, "Loading timeline…"));
  body.append(
    section(
      "Timeline",
      "Commands, alerts, and state changes",
      h(
        "div",
        { class: "section-toolbar" },
        h("p", {}, `Commands and recorded state changes in the last ${range.label}.`),
        count,
        button("Refresh timeline", "button-secondary button-small", () => loadAgentDetail(agent.id)),
      ),
      host,
    ),
  );
  return { host, count };
}

function renderWhatChangedSection(body, rangeKey) {
  const range = TREND_RANGES[rangeKey];
  const host = h("div", { class: "what-changed" }, h("div", { class: "empty-inline" }, "Loading state changes…"));
  body.append(
    section(
      "What changed",
      `State changes in the last ${range.label}`,
      h("p", { class: "form-hint" }, "Recent service, package, version, profile, connectivity, and alert transitions."),
      host,
    ),
  );
  return host;
}

function renderTrends(body) {
  const agent = state.detailAgent;
  const rangeKey = TREND_RANGES[state.trendRange] ? state.trendRange : "24";
  const toggle = h(
    "div",
    { class: "range-toggle", role: "group", "aria-label": "History range" },
    ...Object.entries(TREND_RANGES).map(([key, range]) =>
      h(
        "button",
        {
          class: `range-option${key === rangeKey ? " is-active" : ""}`,
          type: "button",
          "aria-pressed": String(key === rangeKey),
          onClick: () => {
            if (state.trendRange === key) return;
            state.trendRange = key;
            renderTab();
          },
        },
        range.label,
      ),
    ),
  );
  const host = h(
    "div",
    { class: "chart-list" },
    h("div", { class: "empty-inline" }, `Loading ${TREND_RANGES[rangeKey].label} history…`),
  );
  body.append(
    section(
      "Trends",
      "Telemetry detail",
      h(
        "div",
        { class: "section-toolbar" },
        h("p", {}, "Larger charts from recorded telemetry. Gaps indicate offline periods."),
        toggle,
      ),
      host,
    ),
  );
  loadTrendCharts(host, agent, rangeKey);
  const whatHost = renderWhatChangedSection(body, rangeKey);
  const timeline = renderTimelineSection(body, agent, rangeKey);
  loadTrendActivity(agent, rangeKey, whatHost, timeline.host, timeline.count);
}

function renderDisks(body) {
  const disks = state.detailAgent.disks || [];
  if (!disks.length) {
    body.append(section("Storage", "Disks", h("div", { class: "empty-inline" }, "No local disk telemetry reported.")));
    return;
  }
  const rows = disks.map((disk) =>
    h(
      "div",
      { class: "command-row" },
      h(
        "div",
        { class: "service-row" },
        h(
          "div",
          { class: "service-main" },
          h("strong", {}, `${disk.mount || "?"} · ${disk.fs || "filesystem"}`),
          h("small", {}, `${fmtBytes(disk.used)} used of ${fmtBytes(disk.total)} · ${fmtBytes(disk.free)} free`),
        ),
        h("div", { class: "button-row" }, statusPill(`${Math.round(disk.percent || 0)}%`, disk.percent >= 90 ? "critical" : disk.percent >= 80 ? "warning" : "neutral")),
      ),
      progressBar(disk.percent, 80, 90, `${disk.mount || "Disk"} usage`),
    ),
  );
  body.append(section("Storage", "Filesystems", h("div", { class: "service-list" }, ...rows)));
}

function renderGpu(body) {
  const gpus = state.detailAgent.gpus || [];
  if (!gpus.length) {
    body.append(
      section(
        "Accelerators",
        "GPU",
        h("div", { class: "empty-inline" }, "No GPU telemetry reported. NVIDIA statistics require nvidia-smi on the agent host."),
      ),
    );
    return;
  }
  const cards = gpus.map((gpu) =>
    h(
      "div",
      { class: "info-tile" },
      h("span", {}, gpu.name || "GPU"),
      h("strong", {}, `${Math.round(gpu.util_pct || 0)}% util · ${Math.round(gpu.temp_c || 0)}°C`),
      h("div", { class: "form-hint" }, `${fmtBytes(gpu.mem_used)} / ${fmtBytes(gpu.mem_total)} VRAM`),
      progressBar(gpu.util_pct, 80, 90, `${gpu.name || "GPU"} utilization`),
    ),
  );
  body.append(section("Accelerators", "GPU telemetry", h("div", { class: "info-grid" }, ...cards)));
  const processes = state.detailAgent.extra?.gpu_processes || [];
  if (processes.length) {
    const table = h(
      "table",
      { class: "data-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "PID"), h("th", {}, "Process"), h("th", {}, "VRAM"))),
      h(
        "tbody",
        {},
        ...processes.map((process) =>
          h("tr", {}, h("td", { class: "mono" }, String(process.pid ?? "—")), h("td", { class: "mono" }, short(process.name || "—", 80)), h("td", {}, fmtBytes(process.mem_used))),
        ),
      ),
    );
    body.append(section("Workload", "GPU compute processes", h("div", { class: "table-wrap" }, table)));
  }
  body.append(section("Workload", "Interpretation", h("p", { class: "form-hint" }, "Sustained high GPU utilization with high VRAM use can indicate AI workloads or mining. Correlate with the process list and service state before acting. GPU process data comes from nvidia-smi.")));
}

function serviceFilterState(agentId) {
  let filters = state.serviceFilters[agentId];
  if (!filters || typeof filters !== "object") {
    filters = { search: typeof filters === "string" ? filters : "", status: "all", limit: SERVICE_PAGE_SIZE };
    state.serviceFilters[agentId] = filters;
  }
  if (typeof filters.search !== "string") filters.search = "";
  if (!SERVICE_STATUS_FILTERS.includes(filters.status)) filters.status = "all";
  if (!Number.isFinite(filters.limit) || filters.limit < SERVICE_PAGE_SIZE) filters.limit = SERVICE_PAGE_SIZE;
  return filters;
}

function filteredServices(agent, filters) {
  const query = String(filters.search || "").trim().toLowerCase();
  const statusFilter = filters.status || "all";
  return (agent.services || []).filter((service) => {
    const status = String(service.status || "unknown").toLowerCase();
    if (statusFilter === "watched" && !service.watched) return false;
    if (["running", "stopped", "failed"].includes(statusFilter) && status !== statusFilter) return false;
    if (!query) return true;
    return `${service.name || ""} ${service.display_name || ""} ${status} ${service.startup || ""}`
      .toLowerCase()
      .includes(query);
  });
}

function renderServices(body) {
  const agent = state.detailAgent;
  const services = agent.services || [];
  const filters = serviceFilterState(agent.id);
  const list = h("div", { class: "service-list", id: "service-filter-list" });
  const count = h("p", { class: "form-hint", id: "service-filter-count", "aria-live": "polite" });
  const search = h("input", {
    class: "mini-search",
    id: "service-filter-search",
    type: "search",
    placeholder: "Filter services",
    value: filters.search,
    "aria-label": "Filter services",
  });
  const status = h(
    "select",
    { class: "mini-search", id: "service-filter-status", "aria-label": "Filter services by status" },
    ...[
      ["all", "All statuses"],
      ["running", "Running"],
      ["stopped", "Stopped"],
      ["failed", "Failed"],
      ["watched", "Watched"],
    ].map(([value, label]) => h("option", { value, selected: value === filters.status }, label)),
  );
  const moreHost = h("div", { class: "section-toolbar" });
  const toolbar = h("div", { class: "section-toolbar service-toolbar" }, search, status, count);
  const draw = (focusSearch = false) => {
    filters.search = search.value;
    filters.status = status.value;
    const matches = filteredServices(agent, filters);
    renderServiceList(list, agent, filters);
    const shown = Math.min(matches.length, filters.limit);
    count.textContent = `${matches.length} of ${services.length} services${matches.length > shown ? ` · showing ${shown}` : ""}`;
    clear(moreHost);
    if (matches.length > shown) {
      moreHost.append(
        button("Show more", "button-ghost button-small", () => {
          filters.limit += SERVICE_PAGE_SIZE;
          draw();
        }),
      );
    }
    if (focusSearch) search.focus();
  };
  search.addEventListener("input", () => {
    filters.limit = SERVICE_PAGE_SIZE;
    draw(true);
  });
  status.addEventListener("change", () => {
    filters.limit = SERVICE_PAGE_SIZE;
    draw();
  });
  body.append(section("Services", "Service control", toolbar, list, moreHost));
  draw();
}

function serviceStartupEnabled(startup) {
  return /auto|enabled|automatic/i.test(String(startup || ""));
}

function renderServiceList(container, agent, filters = {}) {
  const normalized = typeof filters === "string" ? { search: filters, status: "all" } : filters;
  clear(container);
  const services = filteredServices(agent, normalized);
  if (!services.length) {
    container.append(h("div", { class: "empty-inline" }, "No services match that filter."));
    return;
  }
  const limit = Number.isFinite(normalized.limit) ? normalized.limit : SERVICE_PAGE_SIZE;
  const canAct = state.permissions.operate && agent.online && agent.policy?.allow_service !== false;
  for (const service of services.slice(0, limit)) {
    const status = String(service.status || "unknown").toLowerCase();
    const kind = status === "running" ? "online" : status === "failed" ? "critical" : "neutral";
    const startup = String(service.startup || "");
    container.append(
      h(
        "div",
        { class: "service-row" },
        h(
          "div",
          { class: "service-main" },
          h("strong", { title: service.name }, service.name),
          h("small", {}, service.display_name || "No description"),
          h(
            "div",
            { class: "service-sub" },
            statusPill(status, kind),
            service.watched ? h("span", { class: "tag" }, "watched") : null,
            startup ? h("span", { class: "form-hint" }, startup) : null,
          ),
        ),
        h(
          "div",
          { class: "service-actions" },
          button("Start", "button-secondary", () => queueService(agent, "start", service.name), !canAct || status === "running"),
          button("Stop", "button-secondary", () => queueService(agent, "stop", service.name), !canAct || status === "stopped"),
          button("Restart", "button-secondary", () => queueService(agent, "restart", service.name), !canAct),
          button(serviceStartupEnabled(startup) ? "Disable boot" : "Enable boot", "button-ghost", () => queueService(agent, serviceStartupEnabled(startup) ? "disable" : "enable", service.name), !canAct),
        ),
      ),
    );
  }
}

function renderPackages(body) {
  const agent = state.detailAgent;
  const status = h("p", { class: "form-hint" }, "Loading package inventory…");
  const rulesHost = h("div", { class: "service-list" });
  const inventoryHost = h("div", { class: "service-list" });
  const sync = button(
    "Sync now",
    "button-secondary button-small",
    async () => {
      try {
        const result = await api(`/api/agents/${agent.id}/packages/sync`, { method: "POST" });
        toast(result.queued ? "Package sync queued." : result.message || "Packages are in the desired state.");
        if (result.queued) loadDashboard(true);
        renderTab();
      } catch (error) {
        toast(error.message, "error");
      }
    },
    !state.permissions.operate,
  );
  const toolbar = h(
    "div",
    { class: "section-toolbar" },
    h("p", {}, "Inventory, desired-state rules, and drift."),
    button("Export CSV", "button-ghost button-small", () => {
      window.location.href = `/api/export/packages.csv?agent_id=${encodeURIComponent(agent.id)}`;
    }),
    sync,
  );
  body.append(section("Software", "Packages", status, toolbar, rulesHost, inventoryHost));

  api(`/api/agents/${agent.id}/packages`)
    .then((data) => {
      const inventory = data.inventory || [];
      const managers = (data.managers || []).map((manager) => `${manager.name}${manager.version ? ` (${manager.version})` : ""}`);
      const lastInventory = inventory
        .map((pkg) => pkg.last_seen)
        .filter(Boolean)
        .sort()
        .pop();
      status.textContent = `Heartbeat: ${agent.online ? "just now" : relativeTime(agent.last_seen)} · Inventory updated: ${lastInventory ? relativeTime(lastInventory) : "never"}${managers.length ? ` · Managers: ${managers.join(", ")}` : ""}`;
      renderPackageRules(rulesHost, agent, data.rules || [], data.drift || []);
      renderPackageInventory(inventoryHost, inventory);
    })
    .catch((error) => {
      status.textContent = "";
      rulesHost.append(h("div", { class: "empty-inline" }, error.message));
    });
}

function renderPackageRules(host, agent, initialRules, drift) {
  clear(host);
  let rules = initialRules.map((rule) => ({ ...rule }));
  const editable = state.permissions.admin;
  const header = h(
    "div",
    { class: "section-toolbar" },
    h("p", {}, drift.length ? `${drift.length} pending action${drift.length === 1 ? "" : "s"}.` : "Rules match the current inventory."),
  );
  const form = editable ? packageRuleForm(agent, rules, (updated) => {
    rules = updated;
    draw();
  }) : null;
  const tableHost = h("div", { class: "table-wrap" });
  const save = editable
    ? button("Save rules", "button-primary button-small", async () => {
        try {
          await api(`/api/agents/${agent.id}/packages`, { method: "PUT", body: rules });
          toast("Package rules saved.");
          renderTab();
        } catch (error) {
          toast(error.message, "error");
        }
      })
    : null;
  const draw = () => {
    clear(tableHost);
    if (form) form.dataset.rules = JSON.stringify(rules);
    if (!rules.length) {
      tableHost.append(h("div", { class: "empty-inline" }, "No desired-state rules. Installed packages are unmanaged."));
      return;
    }
    const rows = rules.map((rule, index) =>
      h(
        "tr",
        {},
        h("td", {}, rule.manager),
        h("td", {}, rule.package),
        h("td", {}, rule.desired_state === "version" ? `version ${rule.version_spec}` : rule.desired_state),
        editable
          ? h("td", {}, button("Remove", "button-ghost button-small", () => {
              rules.splice(index, 1);
              draw();
            }))
          : h("td", {}, "—"),
      ),
    );
    tableHost.append(
      h(
        "table",
        { class: "data-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Manager"), h("th", {}, "Package"), h("th", {}, "Desired state"), h("th", {}, ""))),
        h("tbody", {}, ...rows),
      ),
    );
  };
  host.append(header);
  if (form) host.append(form);
  host.append(tableHost, h("div", { class: "section-toolbar" }, save || h("span", {}, "")));
  draw();
}

function packageRuleForm(agent, rules, onChange) {
  const manager = h(
    "select",
    {},
    ...["apt", "dnf", "winget", "choco"].map((name) => h("option", { value: name }, name)),
  );
  const name = h("input", { type: "text", placeholder: "package name", maxlength: "128" });
  const state = h(
    "select",
    {},
    h("option", { value: "present" }, "present"),
    h("option", { value: "absent" }, "absent"),
    h("option", { value: "latest" }, "latest"),
    h("option", { value: "version" }, "pinned version"),
  );
  const version = h("input", { type: "text", placeholder: "version (for pinned)", maxlength: "128", disabled: true });
  state.addEventListener("change", () => {
    version.disabled = state.value !== "version";
    if (version.disabled) version.value = "";
  });
  const form = h(
    "form",
    { class: "section-toolbar" },
    h("label", { class: "field" }, h("span", {}, "Manager"), manager),
    h("label", { class: "field" }, h("span", {}, "Package"), name),
    h("label", { class: "field" }, h("span", {}, "State"), state),
    h("label", { class: "field" }, h("span", {}, "Version"), version),
    button("Add rule", "button-secondary button-small", (event) => {
      event.preventDefault();
      const packageName = name.value.trim();
      if (!packageName) {
        toast("Enter a package name.", "error");
        return;
      }
      if (state.value === "version" && !version.value.trim()) {
        toast("Enter a version for a pinned rule.", "error");
        return;
      }
      const next = rules.filter((rule) => !(rule.manager === manager.value && rule.package === packageName));
      next.push({
        manager: manager.value,
        package: packageName,
        desired_state: state.value,
        version_spec: state.value === "version" ? version.value.trim() : "",
      });
      rules.length = 0;
      rules.push(...next.sort((a, b) => `${a.manager}:${a.package}`.localeCompare(`${b.manager}:${b.package}`)));
      name.value = "";
      version.value = "";
      onChange(rules);
    }, false),
  );
  form.setAttribute("data-agent", agent.id);
  return form;
}

function renderPackageInventory(host, inventory) {
  clear(host);
  if (!inventory.length) {
    host.append(h("div", { class: "empty-inline" }, "No package inventory reported yet. Inventory refreshes on the profile cadence."));
    return;
  }
  const rows = inventory.map((pkg) =>
    h(
      "tr",
      {},
      h("td", {}, pkg.package),
      h("td", {}, pkg.version || "—"),
      h("td", {}, pkg.manager),
      h("td", {}, pkg.arch || "—"),
    ),
  );
  host.append(
    interactiveTable(
      h(
        "table",
        { class: "data-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Package"), h("th", {}, "Version"), h("th", {}, "Manager"), h("th", {}, "Arch"))),
        h("tbody", {}, ...rows),
      ),
      { placeholder: "Filter packages", pageSize: 50 },
    ),
  );
}

function renderStartup(body) {
  const startup = state.detailAgent.startup || [];
  if (!startup.length) {
    body.append(section("Boot", "Startup apps", h("div", { class: "empty-inline" }, "No startup entries reported.")));
    return;
  }
  const table = h(
    "table",
    { class: "data-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Source"), h("th", {}, "Enabled"), h("th", {}, "Command"))),
    h(
      "tbody",
      {},
      ...startup.map((entry) =>
        h(
          "tr",
          {},
          h("td", {}, h("strong", {}, entry.name || "—")),
          h("td", { class: "mono" }, short(entry.source || "—", 42)),
          h("td", {}, statusPill(entry.enabled ? "enabled" : "disabled", entry.enabled ? "online" : "neutral")),
          h("td", { class: "mono", title: entry.command || "" }, short(entry.command || "—", 90)),
        ),
      ),
    ),
  );
  body.append(section("Boot", "Startup applications and units", interactiveTable(table, { placeholder: "Filter startup", pageSize: 25 })));
  body.append(section("Reference", "Management", h("p", { class: "form-hint" }, "Startup entries are listed read-only. Use the Services tab to start, stop, restart, enable, or disable services.")));
}

function renderRemote(body) {
  const agent = state.detailAgent;
  const term = terminalState(agent.id);
  const shellAllowed = agent.policy?.allow_shell !== false;
  const defaultShell = agent.os === "windows" ? "powershell" : "bash";
  const selected = term.shell || defaultShell;
  const shellSelect = h(
    "select",
    { class: "input", "aria-label": "Shell" },
    ...(agent.os === "windows"
      ? [["powershell", "PowerShell"], ["cmd", "Command prompt"], ["bash", "Bash (if installed)"]]
      : [["bash", "Bash"], ["powershell", "PowerShell (pwsh)"]]
    ).map(([value, label]) => h("option", { value, selected: value === selected }, label)),
  );
  const commandInput = h("textarea", { placeholder: agent.os === "windows" ? "Get-Service | Select -First 10" : "uptime && df -h", "aria-label": "Command" });
  const form = h(
    "form",
    {
      class: "remote-form",
      onSubmit: (event) => {
        event.preventDefault();
        runRemoteCommand(agent, shellSelect.value, commandInput.value);
      },
    },
    h(
      "div",
      { class: "remote-controls" },
      shellSelect,
      commandInput,
      h("button", { class: "button button-primary", type: "submit", disabled: !state.permissions.operate || !shellAllowed || !agent.online || Boolean(term.pending) }, "Run"),
    ),
  );
  commandInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      runRemoteCommand(agent, shellSelect.value, commandInput.value);
    }
  });
  const output = h("div", { class: "terminal-output", "data-terminal-output": "true" });
  const status = h("span", { "data-terminal-status": "true" }, term.pending ? "Command pending" : "Ready");
  const terminal = h(
    "div",
    { class: "terminal" },
    h("div", { class: "terminal-bar" }, h("span", {}, `agent@${agent.hostname} · remote control`), status),
    output,
  );
  body.append(
    section("Remote control", "Shell", form, terminal, h("p", { class: "form-hint" }, "Commands are queued until the agent's next heartbeat, typically within 30 seconds. Ctrl/Cmd + Enter runs. Every command is audited.")),
  );
  if (!shellAllowed) body.append(h("div", { class: "alert-item" }, h("span", { class: "alert-mark" }, "!"), "Remote shell is blocked by server agent policy."));
  if (!agent.online) body.append(h("div", { class: "alert-item" }, h("span", { class: "alert-mark" }, "!"), "This agent is offline. Wake it or wait for its next heartbeat."));
  renderTerminalDom(agent);
}

function terminalState(agentId) {
  if (!state.terminals[agentId]) {
    state.terminals[agentId] = {
      lines: [],
      pending: null,
      shell: "",
      status: "",
      outputSeen: {},
      streamed: { stdout: "", stderr: "" },
    };
  }
  const term = state.terminals[agentId];
  if (!term.outputSeen) term.outputSeen = {};
  if (!term.streamed) term.streamed = { stdout: "", stderr: "" };
  return term;
}

function renderTerminalDom(agent) {
  const term = terminalState(agent.id);
  const output = document.querySelector("[data-terminal-output]");
  const status = document.querySelector("[data-terminal-status]");
  if (output) {
    clear(output);
    const visible = term.lines.slice(-300);
    for (const line of visible) {
      output.append(h("div", { class: `terminal-line-${line.type}` }, line.text));
    }
    output.scrollTop = output.scrollHeight;
  }
  if (status) {
    status.textContent = term.pending ? term.status || "Command pending" : "Ready";
  }
}

async function runRemoteCommand(agent, shell, command) {
  if (!state.permissions.operate) return toast("Operator access required.", "error");
  if (agent.policy?.allow_shell === false) return toast("Remote shell is blocked by server policy.", "warn");
  if (!agent.online) return toast("Agent is offline.", "warn");
  const text = command.trim();
  if (!text) return toast("Enter a command first.", "warn");
  const term = terminalState(agent.id);
  if (term.pending) return toast("Wait for the current command to finish.", "warn");
  term.shell = shell;
  term.outputSeen = {};
  term.streamed = { stdout: "", stderr: "" };
  try {
    const created = await api(`/api/agents/${agent.id}/commands`, {
      method: "POST",
      body: { kind: "shell", args: { shell, command: text, timeout: 60 } },
    });
    term.lines.push({ type: "cmd", text: `${shell === "powershell" ? "PS" : shell === "cmd" ? "CMD" : "$"} ${text}` });
    term.lines.push({ type: "note", text: "Queued for the agent's next heartbeat." });
    term.pending = created.id;
    term.status = "Queued";
    renderTerminalDom(agent);
    const textarea = document.querySelector(".remote-controls textarea");
    if (textarea) textarea.value = "";
    pollCommand(agent.id, created.id);
  } catch (error) {
    toast(error.message, "error");
  }
}

function commandIsTerminal(command) {
  return ["succeeded", "failed", "cancelled"].includes(command.status);
}

function appendLiveOutput(term, stream, seq, text) {
  if (!text || (stream !== "stdout" && stream !== "stderr")) return;
  const key = `${stream}:${seq}`;
  if (term.outputSeen[key]) return;
  term.outputSeen[key] = true;
  term.streamed[stream] = (term.streamed[stream] || "") + text;
  const type = stream === "stderr" ? "err" : "out";
  const last = term.lines[term.lines.length - 1];
  if (last && last.type === type && last.streaming) {
    last.text += text;
  } else {
    term.lines.push({ type, text, streaming: true });
  }
}

function appendFinalOutput(term, stream, text) {
  if (!text) return;
  const streamed = term.streamed[stream] || "";
  let shared = 0;
  const limit = Math.min(streamed.length, text.length);
  while (shared < limit && streamed[shared] === text[shared]) shared += 1;
  const remaining = text.slice(shared);
  if (remaining) {
    const type = stream === "stderr" ? "err" : "out";
    const last = term.lines[term.lines.length - 1];
    if (last && last.type === type && last.streaming) {
      last.text += remaining;
    } else {
      term.lines.push({ type, text: remaining });
    }
  }
  term.streamed[stream] = text;
}

function applyCommandUpdate(agentId, commandId, command) {
  const term = terminalState(agentId);
  if (term.pending !== commandId || !command) return;
  if (commandIsTerminal(command)) {
    const hadStreamed = Boolean(term.streamed.stdout || term.streamed.stderr);
    if (command.stdout) appendFinalOutput(term, "stdout", command.stdout);
    if (command.stderr) appendFinalOutput(term, "stderr", command.stderr);
    if (command.error) term.lines.push({ type: "err", text: command.error });
    if (!command.stdout && !command.stderr && !command.error && !hadStreamed) {
      term.lines.push({ type: "note", text: "(no output)" });
    }
    term.lines.push({ type: "note", text: `${command.status} · exit ${command.exit_code ?? "n/a"}` });
    term.pending = null;
    term.status = "Ready";
    term.streamed = { stdout: "", stderr: "" };
    term.outputSeen = {};
    if (state.detailAgent?.id === agentId && state.detailTab === "remote") renderTerminalDom(state.detailAgent);
    loadDashboard(true);
    loadAgentDetail(agentId);
    return;
  }
  term.status = command.status === "dispatched" ? "Running on agent" : "Queued for heartbeat";
  if (state.detailAgent?.id === agentId && state.detailTab === "remote") renderTerminalDom(state.detailAgent);
}

function streamCommand(agentId, commandId) {
  // Command SSE carries both status snapshots and incremental output chunks.
  // A reconnect replays stored chunks; the terminal snapshot is canonical.
  return new Promise((resolve) => {
    const term = terminalState(agentId);
    let source = null;
    let settled = false;
    let timeout = null;
    const finish = (streamed) => {
      if (settled) return;
      settled = true;
      if (timeout) clearTimeout(timeout);
      if (source) source.close();
      resolve(streamed);
    };
    if (term.pending !== commandId) {
      resolve(true);
      return;
    }
    try {
      source = new EventSource(`/api/commands/${encodeURIComponent(commandId)}/events`);
    } catch (_error) {
      resolve(false);
      return;
    }
    source.addEventListener("command", (event) => {
      if (term.pending !== commandId) {
        finish(true);
        return;
      }
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (_error) {
        finish(false);
        return;
      }
      if (!data?.command) {
        finish(false);
        return;
      }
      applyCommandUpdate(agentId, commandId, data.command);
      if (commandIsTerminal(data.command)) finish(true);
    });
    source.addEventListener("output", (event) => {
      if (term.pending !== commandId) return;
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      appendLiveOutput(term, data?.stream, Number(data?.seq), String(data?.text ?? ""));
      if (state.detailAgent?.id === agentId && state.detailTab === "remote") {
        renderTerminalDom(state.detailAgent);
      }
    });
    source.addEventListener("error", () => finish(false));
    timeout = setTimeout(() => {
      if (term.pending === commandId) {
        term.lines.push({ type: "err", text: "Timed out waiting for a command result. Check the History tab." });
        term.pending = null;
        if (state.detailAgent?.id === agentId) renderTerminalDom(state.detailAgent);
      }
      finish(true);
    }, 8 * 60 * 1000);
  });
}

async function pollCommand(agentId, commandId) {
  if (typeof EventSource === "function") {
    const streamed = await streamCommand(agentId, commandId);
    if (streamed) return;
    const term = terminalState(agentId);
    if (term.pending !== commandId) return;
  }
  await pollCommandFallback(agentId, commandId);
}

async function pollCommandFallback(agentId, commandId) {
  const term = terminalState(agentId);
  for (let attempt = 0; attempt < 320; attempt += 1) {
    await sleep(1500);
    if (term.pending !== commandId) return;
    let data;
    try {
      data = await api(`/api/commands/${commandId}`);
    } catch (error) {
      term.lines.push({ type: "err", text: error.message });
      term.pending = null;
      if (state.detailAgent?.id === agentId) renderTerminalDom(state.detailAgent);
      return;
    }
    applyCommandUpdate(agentId, commandId, data.command);
    if (commandIsTerminal(data.command)) return;
  }
  term.lines.push({ type: "err", text: "Timed out waiting for a command result. Check the History tab." });
  term.pending = null;
  if (state.detailAgent?.id === agentId) renderTerminalDom(state.detailAgent);
}

function historyState(agentId) {
  if (!state.commandHistory[agentId]) {
    state.commandHistory[agentId] = {
      q: "",
      status: "all",
      limit: 50,
      offset: 0,
      commands: [],
      total: 0,
      loaded: false,
      loading: false,
      error: "",
      seq: 0,
      timer: null,
      paint: null,
    };
  }
  return state.commandHistory[agentId];
}

function historyQuery(hist) {
  const params = new URLSearchParams();
  params.set("limit", String(hist.limit));
  params.set("offset", String(hist.offset));
  if (hist.status && hist.status !== "all") params.set("status", hist.status);
  if (hist.q) params.set("q", hist.q);
  return params;
}

function historyExportUrl(agentId, hist) {
  const params = new URLSearchParams();
  params.set("agent_id", agentId);
  if (hist.status && hist.status !== "all") params.set("status", hist.status);
  if (hist.q) params.set("q", hist.q);
  return `/api/export/commands.csv?${params.toString()}`;
}

async function loadCommandHistory(agentId) {
  const hist = historyState(agentId);
  const seq = hist.seq + 1;
  hist.seq = seq;
  hist.loading = true;
  hist.error = "";
  hist.paint?.();
  let retry = false;
  try {
    const data = await api(`/api/agents/${encodeURIComponent(agentId)}/commands?${historyQuery(hist).toString()}`);
    if (seq !== hist.seq || state.detailId !== agentId) return;
    hist.commands = Array.isArray(data.commands) ? data.commands : [];
    hist.total = Number(data.total ?? hist.commands.length);
    hist.loaded = true;
    if (hist.total > 0 && hist.offset >= hist.total) {
      hist.offset = Math.max(0, Math.floor((hist.total - 1) / hist.limit) * hist.limit);
      retry = true;
    }
  } catch (error) {
    if (seq !== hist.seq || state.detailId !== agentId) return;
    hist.error = error.message;
  } finally {
    if (seq === hist.seq) {
      hist.loading = false;
      if (state.detailAgent?.id === agentId && state.detailTab === "history") hist.paint?.();
      if (hist.error) toast(hist.error, "error");
    }
  }
  if (retry) loadCommandHistory(agentId);
}

function commandHistoryRow(agent, command) {
  const output = h("div", { class: "command-output" });
  if (command.stdout) output.append(h("div", {}, command.stdout));
  if (command.stderr) output.append(h("div", { class: "err" }, command.stderr));
  if (command.error) output.append(h("div", { class: "err" }, command.error));
  if (command.output_truncated) output.append(h("div", { class: "form-hint" }, "History preview only. Use GET /api/commands/{id} for full output."));
  if (!command.stdout && !command.stderr && !command.error) output.append(h("div", {}, "No output."));
  const actionButtons = [];
  if (state.permissions.operate && command.status === "queued") {
    actionButtons.push(button("Cancel", "button-ghost", () => cancelCommand(agent.id, command.id)));
  }
  if (state.permissions.operate && ["failed", "cancelled"].includes(command.status)) {
    actionButtons.push(button("Retry", "button-secondary", () => retryCommand(agent.id, command.id)));
  }
  const actionWrap = h("div", { class: "command-summary-actions" }, ...actionButtons);
  if (actionButtons.length) {
    actionWrap.addEventListener("click", (event) => event.stopPropagation());
  }
  return h(
    "details",
    { class: "command-row" },
    h(
      "summary",
      {},
      h(
        "div",
        { class: "command-summary-main" },
        statusPill(command.status, command.status === "succeeded" ? "online" : command.status === "failed" ? "critical" : "neutral"),
        h("strong", {}, short(commandDescription(command), 100)),
        h("small", {}, localTime(command.created_at)),
      ),
      h("div", { class: "command-summary-tail" }, actionWrap, h("span", { class: "form-hint" }, `${command.exit_code ?? "—"}`)),
    ),
    output,
  );
}

function renderHistory(body) {
  const agent = state.detailAgent;
  const hist = historyState(agent.id);
  const list = h("div", { class: "service-list" });
  const count = h("span", { class: "form-hint", "data-history-count": "true" });
  const exportLink = h(
    "a",
    {
      class: "button button-secondary button-small",
      href: historyExportUrl(agent.id, hist),
      download: "sextant-commands.csv",
      "data-history-export": "true",
    },
    "Export CSV",
  );
  const prev = button(
    "Previous",
    "button-secondary button-small",
    () => {
      if (hist.offset <= 0) return;
      hist.offset = Math.max(0, hist.offset - hist.limit);
      loadCommandHistory(agent.id);
    },
    true,
  );
  const next = button(
    "Next",
    "button-secondary button-small",
    () => {
      if (hist.offset + hist.limit >= hist.total) return;
      hist.offset += hist.limit;
      loadCommandHistory(agent.id);
    },
    true,
  );
  const search = h("input", {
    type: "search",
    placeholder: "Search command history",
    "aria-label": "Search command history",
    value: hist.q,
  });
  const status = h(
    "select",
    { "aria-label": "Filter command status" },
    ...HISTORY_STATUS_FILTERS.map((value) =>
      h("option", { value, selected: value === hist.status }, value === "all" ? "All statuses" : value),
    ),
  );
  const pageSize = h(
    "select",
    { "aria-label": "Commands per page" },
    ...HISTORY_PAGE_SIZES.map((value) =>
      h("option", { value: String(value), selected: value === hist.limit }, String(value)),
    ),
  );
  const refresh = button("Refresh history", "button-secondary button-small", () => loadCommandHistory(agent.id));
  const paint = () => {
    clear(list);
    if (hist.loading && !hist.loaded) {
      list.append(h("div", { class: "empty-inline" }, "Loading command history…"));
    } else if (hist.error) {
      list.append(h("div", { class: "empty-inline" }, hist.error));
    } else if (!hist.commands.length) {
      list.append(h("div", { class: "empty-inline" }, "No commands match these filters."));
    } else {
      list.append(...hist.commands.map((command) => commandHistoryRow(agent, command)));
    }
    const start = hist.total ? hist.offset + 1 : 0;
    const end = hist.total ? Math.min(hist.offset + hist.commands.length, hist.total) : 0;
    count.textContent = `${start}-${end} of ${hist.total} commands`;
    prev.disabled = hist.loading || hist.offset <= 0;
    next.disabled = hist.loading || hist.offset + hist.limit >= hist.total;
    exportLink.href = historyExportUrl(agent.id, hist);
  };
  hist.paint = paint;
  search.addEventListener("input", () => {
    hist.q = search.value.trim();
    hist.offset = 0;
    exportLink.href = historyExportUrl(agent.id, hist);
    clearTimeout(hist.timer);
    hist.timer = setTimeout(() => loadCommandHistory(agent.id), HISTORY_SEARCH_DELAY);
  });
  status.addEventListener("change", () => {
    hist.status = status.value;
    hist.offset = 0;
    exportLink.href = historyExportUrl(agent.id, hist);
    loadCommandHistory(agent.id);
  });
  pageSize.addEventListener("change", () => {
    hist.limit = Number(pageSize.value);
    hist.offset = 0;
    loadCommandHistory(agent.id);
  });
  body.append(
    section(
      "History",
      "Command history",
      h(
        "div",
        { class: "section-toolbar" },
        h("p", {}, `Retained commands. Heartbeat: ${agent.online ? "just now" : relativeTime(agent.last_seen)}.`),
        search,
        status,
        pageSize,
        count,
        prev,
        next,
        exportLink,
        refresh,
      ),
      list,
    ),
  );
  paint();
  if (!hist.loaded && !hist.loading) loadCommandHistory(agent.id);
}

async function cancelCommand(agentId, commandId) {
  try {
    await api(`/api/commands/${commandId}/cancel`, { method: "POST", body: {} });
    toast("Queued command cancelled.");
    loadAgentDetail(agentId);
    loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function retryCommand(agentId, commandId) {
  try {
    await api(`/api/commands/${commandId}/retry`, { method: "POST", body: {} });
    toast("Command queued again.");
    loadAgentDetail(agentId);
    loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

function commandDescription(command) {
  const args = command.args || {};
  if (command.kind === "shell") return `${args.shell || "shell"}: ${args.command || ""}`;
  if (command.kind === "power") return `power: ${args.action || "?"}${args.action === "cancel" ? "" : ` in ${args.delay ?? 30}s`}`;
  if (command.kind === "service") return `service: ${args.action || "?"} ${args.name || ""}`;
  if (command.kind === "refresh") return "refresh telemetry";
  return command.kind;
}

async function loadAgentDetail(agentId) {
  try {
    const data = await api(`/api/agents/${agentId}`);
    if (state.detailId !== agentId) return;
    state.detailAgent = data.agent;
    state.agentCommands[agentId] = data.commands;
    updateDrawerChrome();
    if (state.detailTab === "history") loadCommandHistory(agentId);
    else if (state.detailTab === "trends") renderTab();
  } catch (error) {
    if (state.detailId === agentId) {
      closeDrawer();
      toast(error.message, "error");
    }
  }
}

async function sendCommand(agentId, kind, args, successMessage) {
  try {
    const command = await api(`/api/agents/${agentId}/commands`, { method: "POST", body: { kind, args } });
    if (successMessage) toast(successMessage);
    loadAgentDetail(agentId);
    loadDashboard(true);
    return command;
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

function queueService(agent, action, name) {
  if (!state.permissions.operate) return toast("Operator access required.", "error");
  if (agent.policy?.allow_service === false) return toast("Service control is blocked by server policy.", "warn");
  if (!agent.online) return toast("Agent is offline.", "warn");
  sendCommand(agent.id, "service", { action, name }, `${action} requested for ${name}.`);
}

async function queuePower(agent, action) {
  if (!state.permissions.operate) return toast("Operator access required.", "error");
  if (agent.policy?.allow_power === false) return toast("Power actions are blocked by server policy.", "warn");
  if (action === "cancel") {
    const confirmed = await confirmAction("Cancel power action", `Cancel any scheduled shutdown or reboot on ${agent.name}?`, "Cancel action");
    if (!confirmed) return;
    await sendCommand(agent.id, "power", { action: "cancel", delay: 0 }, "Power cancellation sent.");
    return;
  }
  if (!agent.online) return toast("Agent is offline. Wake it first.", "warn");
  const verb = action === "shutdown" ? "Shut down" : "Reboot";
  const confirmed = await confirmAction(`${verb} ${agent.name}?`, `The agent will schedule ${action} in 30 seconds. Unsaved work on the machine may be lost.`, verb);
  if (!confirmed) return;
  await sendCommand(agent.id, "power", { action, delay: 30 }, `${verb} scheduled in 30 seconds.`);
}

async function wakeAgent(agent) {
  if (!state.permissions.operate) return toast("Operator access required.", "error");
  try {
    const result = await api(`/api/agents/${agent.id}/wake`, { method: "POST", body: {} });
    toast(`Wake packet sent to ${result.mac} (${result.sent} packets).`);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteAgent(agent) {
  if (!state.permissions.admin) return;
  const confirmed = await confirmAction(`Delete ${agent.name}?`, "This removes the agent and its command history from the server. The installed agent will be rejected until re-enrolled.", "Delete agent");
  if (!confirmed) return;
  try {
    await api(`/api/agents/${agent.id}`, { method: "DELETE" });
    toast("Agent deleted.");
    closeDrawer();
    loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

function splitList(value) {
  return String(value || "")
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function openAgentEditDialog(agent) {
  const form = $("agent-edit-form");
  form.reset();
  setText("agent-edit-title", `Edit ${agent.name}`);
  form.elements.name.value = agent.name || "";
  form.elements.tags.value = (agent.tags || []).join(", ");
  form.elements.watch_services.value = (agent.watch_services || []).join(", ");
  form.elements.notes.value = agent.notes || "";
  form.elements.allow_shell.checked = agent.policy?.allow_shell !== false;
  form.elements.allow_power.checked = agent.policy?.allow_power !== false;
  form.elements.allow_service.checked = agent.policy?.allow_service !== false;
  setText("agent-edit-error", "");
  $("agent-edit-dialog").showModal();
}

function confirmAction(title, message, confirmLabel = "Confirm") {
  return new Promise((resolve) => {
    const dialog = $("confirm-dialog");
    setText("confirm-title", title);
    setText("confirm-message", message);
    $("confirm-accept").textContent = confirmLabel;
    dialog.returnValue = "cancel";
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "confirm");
    };
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
}

async function loadWakeTargets() {
  const container = $("wake-target-list");
  try {
    const data = await api("/api/wake-targets");
    clear(container);
    if (!data.targets.length) {
      container.append(h("div", { class: "empty-inline" }, "No saved wake targets yet."));
      return;
    }
    for (const target of data.targets) {
      container.append(
        h(
          "div",
          { class: "list-row" },
          h(
            "div",
            { class: "list-row-main" },
            h("strong", {}, target.name),
            h("small", {}, `${target.mac} · ${target.broadcast}:${target.port}${target.ip ? ` · ${target.ip}` : ""}`),
            h("small", {}, `Last woken: ${target.last_woken_at ? relativeTime(target.last_woken_at) : "never"}`),
          ),
          h(
            "div",
            { class: "list-row-actions" },
            state.permissions.operate ? button("Wake", "button-primary button-small", () => wakeTarget(target)) : null,
            state.permissions.operate ? button("Delete", "button-ghost button-small", () => deleteWakeTarget(target)) : null,
          ),
        ),
      );
    }
  } catch (error) {
    clear(container);
    container.append(h("div", { class: "empty-inline" }, error.message));
  }
}

async function wakeTarget(target) {
  try {
    const result = await api(`/api/wake-targets/${target.id}/wake`, { method: "POST", body: {} });
    toast(`Wake sent to ${result.mac} (${result.sent} packets).`);
    loadWakeTargets();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteWakeTarget(target) {
  const confirmed = await confirmAction("Delete wake target", `Delete ${target.name}?`, "Delete");
  if (!confirmed) return;
  try {
    await api(`/api/wake-targets/${target.id}`, { method: "DELETE" });
    toast("Wake target deleted.");
    loadWakeTargets();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadEnrollmentTokens() {
  const container = $("enrollment-list");
  try {
    const data = await api("/api/enrollment-tokens");
    clear(container);
    if (!data.tokens.length) {
      container.append(h("div", { class: "empty-inline" }, "No enrollment tokens issued yet."));
      return;
    }
    for (const token of data.tokens) {
      container.append(
        h(
          "div",
          { class: "list-row" },
          h(
            "div",
            { class: "list-row-main" },
            h("strong", {}, token.label || "Unlabeled token"),
            h("small", {}, `${token.state} · expires ${localTime(token.expires_at)}${token.used_at ? ` · used ${relativeTime(token.used_at)}` : ""}`),
          ),
          h(
            "div",
            { class: "list-row-actions" },
            statusPill(token.state, token.state === "active" ? "online" : token.state === "expired" ? "warning" : "neutral"),
            token.state === "active" ? button("Revoke", "button-ghost button-small", () => deleteEnrollmentToken(token)) : null,
          ),
        ),
      );
    }
  } catch (error) {
    clear(container);
    container.append(h("div", { class: "empty-inline" }, error.message));
  }
}

async function deleteEnrollmentToken(token) {
  const confirmed = await confirmAction("Revoke enrollment token", `Revoke the token "${token.label || "unlabeled"}"?`, "Revoke");
  if (!confirmed) return;
  try {
    await api(`/api/enrollment-tokens/${token.id}`, { method: "DELETE" });
    toast("Enrollment token revoked.");
    loadEnrollmentTokens();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadAudit() {
  const host = $("audit-table-host");
  clear(host);
  const body = h("tbody", {});
  try {
    const data = await api("/api/audit?limit=200");
    if (!data.events.length) {
      body.append(h("tr", {}, h("td", { colspan: "6", class: "table-empty" }, "No audit events yet.")));
    } else {
      for (const event of data.events) {
        body.append(
          h(
            "tr",
            {},
            h("td", { class: "mono" }, localTime(event.at)),
            h("td", {}, h("strong", {}, event.actor)),
            h("td", {}, h("span", { class: "tag" }, event.action)),
            h("td", { class: "mono" }, short(event.target || "—", 36)),
            h("td", { class: "mono" }, event.ip || "—"),
            h("td", { class: "mono" }, short(JSON.stringify(event.detail || {}), 110)),
          ),
        );
      }
    }
  } catch (error) {
    body.append(h("tr", {}, h("td", { colspan: "6", class: "table-empty" }, error.message)));
  }
  const table = h(
    "table",
    { class: "data-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Actor"), h("th", {}, "Action"), h("th", {}, "Target"), h("th", {}, "Source IP"), h("th", {}, "Detail"))),
    body,
  );
  host.append(interactiveTable(table, { placeholder: "Filter audit", pageSize: 50 }));
}

function userRow(user) {
  const select = h(
    "select",
    { class: "input", "aria-label": `Role for ${user.username}` },
    ...[["viewer", "Viewer"], ["operator", "Operator"], ["admin", "Administrator"]].map(([value, label]) =>
      h("option", { value, selected: value === user.role }, label),
    ),
  );
  select.addEventListener("change", () => updateUserRole(user, select));
  return h(
    "div",
    { class: "list-row" },
    h(
      "div",
      { class: "list-row-main" },
      h("strong", {}, user.display_name || user.username),
      h(
        "small",
        {},
        `${user.username} · ${user.source === "ad" ? "Active Directory" : "Local"} · last login ${user.last_login_at ? relativeTime(user.last_login_at) : "never"}`,
      ),
    ),
    h(
      "div",
      { class: "user-row-actions" },
      statusPill(user.active ? "active" : "disabled", user.active ? "online" : "warning"),
      h("span", { class: "tag" }, `${user.session_count || 0} session${user.session_count === 1 ? "" : "s"}`),
      select,
      button("Sessions", "button-ghost button-small", () => openSessionsDialog(user)),
      user.source === "local"
        ? button("Reset password", "button-ghost button-small", () => openResetPasswordDialog(user))
        : null,
      state.user?.id === user.id
        ? null
        : button(user.active ? "Disable" : "Enable", user.active ? "button-danger button-small" : "button-secondary button-small", () =>
            setUserActive(user, !user.active),
          ),
    ),
  );
}

function userMatchesQuery(user, query) {
  if (!query) return true;
  return [user.username, user.display_name, user.email, user.role, user.source]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes(query);
}

async function loadUsers() {
  const container = $("user-list");
  try {
    const data = await api("/api/users");
    const users = data.users || [];
    clear(container);
    const search = h("input", {
      class: "mini-search",
      id: "user-search",
      type: "search",
      placeholder: "Search users",
      value: state.userSearch,
      "aria-label": "Search users",
    });
    const count = h("span", { class: "form-hint", id: "user-count", "aria-live": "polite" });
    const rows = h("div", { class: "list-stack" });
    container.append(h("div", { class: "section-toolbar user-toolbar" }, search, count), rows);

    const draw = () => {
      state.userSearch = search.value;
      const query = state.userSearch.trim().toLowerCase();
      const filtered = users.filter((user) => userMatchesQuery(user, query));
      clear(rows);
      count.textContent = `${filtered.length} of ${users.length} users`;
      if (!filtered.length) {
        rows.append(h("div", { class: "empty-inline" }, "No users match that search."));
        return;
      }
      for (const user of filtered) rows.append(userRow(user));
    };
    search.addEventListener("input", draw);
    draw();
  } catch (error) {
    clear(container);
    container.append(h("div", { class: "empty-inline" }, error.message));
  }
}

async function updateUserRole(user, select) {
  const previous = user.role;
  try {
    const updated = await api(`/api/users/${user.id}`, { method: "PATCH", body: { role: select.value } });
    if (state.user?.id === updated.id) {
      const me = await api("/api/me");
      state.user = me.user;
      state.permissions = me.permissions;
      updateChrome();
      applyPermissions();
      if (!state.permissions.admin && ["enroll", "audit", "users"].includes(state.page)) {
        navigate("overview");
      }
    }
    toast(`Role updated for ${user.username}.`);
    loadUsers();
  } catch (error) {
    select.value = previous;
    toast(error.message, "error");
  }
}

async function setUserActive(user, active) {
  const confirmed = await confirmAction(
    `${active ? "Enable" : "Disable"} ${user.username}?`,
    active
      ? "The account will be able to sign in again. Existing sessions were revoked when it was disabled."
      : "The account will be blocked and every active session will be revoked immediately.",
    active ? "Enable user" : "Disable user",
  );
  if (!confirmed) return;
  try {
    await api(`/api/users/${user.id}`, { method: "PATCH", body: { active } });
    toast(`User ${active ? "enabled" : "disabled"}.`);
    loadUsers();
  } catch (error) {
    toast(error.message, "error");
  }
}

function openResetPasswordDialog(user) {
  state.resetUser = user;
  const form = $("reset-password-form");
  form.reset();
  setText("reset-password-user", `${user.display_name || user.username} (${user.username})`);
  setText("reset-password-error", "");
  $("reset-password-dialog").showModal();
}

async function openSessionsDialog(user) {
  state.sessionsUser = user;
  setText("sessions-title", `Sessions for ${user.display_name || user.username}`);
  setText("sessions-subtitle", "Active browser sessions. Revoking all invalidates every session for this user.");
  const container = $("sessions-list");
  clear(container);
  container.append(h("div", { class: "empty-inline" }, "Loading sessions…"));
  setText("sessions-error", "");
  $("sessions-dialog").showModal();
  await reloadSessionsDialog();
}

async function reloadSessionsDialog() {
  const user = state.sessionsUser;
  if (!user) return;
  const container = $("sessions-list");
  try {
    const data = await api(`/api/users/${user.id}/sessions`);
    clear(container);
    if (!data.sessions.length) {
      container.append(h("div", { class: "empty-inline" }, "No active sessions."));
      return;
    }
    for (const session of data.sessions) {
      container.append(
        h(
          "div",
          { class: "list-row session-row" },
          h(
            "div",
            { class: "list-row-main" },
            h("strong", {}, session.current ? "This browser session" : short(session.user_agent || "Unknown client", 72)),
            h("small", {}, `${session.ip || "unknown IP"} · last seen ${relativeTime(session.last_seen_at)} · expires ${localTime(session.expires_at)}`),
          ),
          h(
            "div",
            { class: "list-row-actions" },
            session.current ? statusPill("current", "online") : button("Revoke", "button-ghost button-small", () => revokeOneSession(session.id)),
          ),
        ),
      );
    }
  } catch (error) {
    clear(container);
    container.append(h("div", { class: "empty-inline" }, error.message));
  }
}

async function revokeOneSession(sessionId) {
  try {
    await api(`/api/sessions/${sessionId}`, { method: "DELETE" });
    toast("Session revoked.");
    await reloadSessionsDialog();
    loadUsers();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function revokeAllUserSessions() {
  const user = state.sessionsUser;
  if (!user) return;
  const confirmed = await confirmAction(
    `Revoke all sessions for ${user.username}?`,
    "Every browser session for this user will be signed out immediately. If this is your own account, a new session will be issued for this browser.",
    "Revoke sessions",
  );
  if (!confirmed) return;
  try {
    const data = await api(`/api/users/${user.id}/revoke-sessions`, { method: "POST", body: {} });
    if (data.csrf) {
      state.csrf = data.csrf;
      if (data.user) state.user = { ...state.user, ...data.user };
      updateChrome();
    }
    $("sessions-dialog").close();
    toast(`Revoked ${data.revoked} session${data.revoked === 1 ? "" : "s"}.`);
    loadUsers();
  } catch (error) {
    setText("sessions-error", error.message);
  }
}

const PROFILE_MODULE_FIELDS = [
  "telemetry",
  "services",
  "startup",
  "gpu",
  "packages",
  "shell",
  "power",
  "service_control",
  "package_management",
  "self_update",
  "docker",
];
const PROFILE_ACTION_FIELDS = ["allow_shell", "allow_power", "allow_service", "allow_packages", "allow_docker"];
const PROFILE_FUTURE_ACTION_FIELDS = ["allow_docker_exec", "allow_docker_compose", "allow_docker_prune"];

async function loadProfiles(silent = false) {
  try {
    const [profiles, tags] = await Promise.all([api("/api/profiles"), api("/api/profile-tags")]);
    state.profiles = profiles.profiles || [];
    state.profileTags = tags.assignments || [];
    renderProfileList();
    renderTagAssignments();
    populateTagProfileSelect();
    populateRemoteProfileSelect();
    populateBulkProfileSelect();
    if (state.dashboard) renderAgentGrid();
    if (state.detailAgent) renderDrawerActions(state.detailAgent);
  } catch (error) {
    if (!silent) toast(error.message, "error");
  }
}

function profileById(id) {
  return state.profiles.find((profile) => profile.id === id);
}

function profileSummary(profile) {
  const settings = profile.settings || {};
  const collection = settings.collection || {};
  const modules = settings.modules || {};
  const actions = settings.actions || {};
  const enabled = PROFILE_MODULE_FIELDS.filter((key) => modules[key] !== false).length;
  const gates = PROFILE_ACTION_FIELDS.filter((key) => actions[key] !== false).length;
  return `${enabled}/${PROFILE_MODULE_FIELDS.length} modules · ${gates}/${PROFILE_ACTION_FIELDS.length} action gates · packages ${Math.round((collection.package_inventory_seconds || 86400) / 3600)}h`;
}

function renderProfileList() {
  const container = $("profile-list");
  if (!container) return;
  clear(container);
  if (!state.profiles.length) {
    container.append(h("div", { class: "empty-inline" }, "No profiles loaded."));
    return;
  }
  for (const profile of state.profiles) {
    const row = h(
      "div",
      { class: "list-row" },
      h(
        "div",
        {},
        h("strong", {}, `${profile.name}${profile.is_system ? " · built-in" : ""}`),
        h("p", { class: "form-hint" }, `${profile.slug} · rev ${profile.revision} · ${profile.agent_count} agent${profile.agent_count === 1 ? "" : "s"} · ${profileSummary(profile)}`),
      ),
      h(
        "div",
        { class: "row-actions" },
        button("Edit", "button-secondary button-small", () => editProfile(profile.id)),
        button("Reset", "button-ghost button-small", () => resetProfile(profile), !profile.is_system),
        button("Delete", "button-danger button-small", () => deleteProfile(profile), profile.is_system || profile.agent_count > 0),
      ),
    );
    container.append(row);
  }
}

function populateTagProfileSelect() {
  const select = $("tag-profile-select");
  if (!select) return;
  const current = select.value;
  clear(select);
  for (const profile of state.profiles) {
    select.append(h("option", { value: profile.slug }, `${profile.name} (${profile.slug})`));
  }
  if (current) select.value = current;
}

function populateBulkProfileSelect() {
  const select = $("bulk-profile");
  if (!select) return;
  const current = select.value;
  clear(select);
  select.append(h("option", { value: "" }, "Choose profile…"));
  for (const profile of state.profiles) {
    select.append(h("option", { value: profile.slug }, `${profile.name} (${profile.slug})`));
  }
  if (state.profiles.some((profile) => profile.slug === current)) select.value = current;
  updateBulkControls();
}

function renderTagAssignments() {
  const container = $("tag-assignment-list");
  if (!container) return;
  clear(container);
  if (!state.profileTags.length) {
    container.append(h("div", { class: "empty-inline" }, "No tag assignments. Devices keep their enrollment profile."));
    return;
  }
  for (const assignment of state.profileTags) {
    const profile = profileById(assignment.profile_id);
    container.append(
      h(
        "div",
        { class: "list-row" },
        h("div", {}, h("strong", {}, assignment.tag), h("p", { class: "form-hint" }, `→ ${profile ? `${profile.name} (${profile.slug})` : assignment.profile_id}`)),
        button("Remove", "button-ghost button-small", () => deleteProfileTag(assignment.tag)),
      ),
    );
  }
}

function editProfile(id) {
  const profile = profileById(id);
  const form = $("profile-form");
  if (!profile || !form) return;
  state.editingProfile = profile;
  form.elements.id.value = profile.id;
  form.elements.slug.value = profile.slug;
  form.elements.slug.disabled = true;
  form.elements.name.value = profile.name;
  form.elements.description.value = profile.description || "";
  const settings = profile.settings || {};
  form.elements.heartbeat_seconds.value = settings.heartbeat_seconds ?? 60;
  form.elements.max_output_bytes.value = settings.max_output_bytes ?? 200000;
  const collection = settings.collection || {};
  form.elements.service_inventory_seconds.value = collection.service_inventory_seconds ?? 21600;
  form.elements.package_inventory_seconds.value = collection.package_inventory_seconds ?? 86400;
  form.elements.package_full_seconds.value = collection.package_full_seconds ?? 604800;
  form.elements.docker_inventory_seconds.value = collection.docker_inventory_seconds ?? 300;
  form.elements.update_channel.value = settings.update?.channel || "stable";
  for (const key of PROFILE_MODULE_FIELDS) {
    form.elements[`module_${key}`].checked = settings.modules?.[key] !== false;
  }
  for (const key of PROFILE_ACTION_FIELDS) {
    form.elements[key].checked = settings.actions?.[key] !== false;
  }
  form.elements.allow_self_update.checked = settings.update?.allow_self_update !== false;
  setText("profile-form-title", `Edit ${profile.name}`);
  $("profile-reset").hidden = !profile.is_system;
  $("profile-delete").hidden = profile.is_system || profile.agent_count > 0;
}

function resetProfileForm() {
  const form = $("profile-form");
  if (!form) return;
  state.editingProfile = null;
  form.reset();
  form.elements.id.value = "";
  form.elements.slug.disabled = false;
  setText("profile-form-title", "New profile");
  $("profile-reset").hidden = true;
  $("profile-delete").hidden = true;
  setText("profile-error", "");
}

function profileSettingsFromForm(form) {
  const modules = {};
  for (const key of PROFILE_MODULE_FIELDS) modules[key] = Boolean(form.elements[`module_${key}`]?.checked);
  const actions = {};
  for (const key of PROFILE_ACTION_FIELDS) actions[key] = Boolean(form.elements[key]?.checked);
  for (const key of PROFILE_FUTURE_ACTION_FIELDS) {
    actions[key] = Boolean(state.editingProfile?.settings?.actions?.[key]);
  }
  return {
    heartbeat_seconds: Number(form.elements.heartbeat_seconds.value),
    modules,
    collection: {
      service_inventory_seconds: Number(form.elements.service_inventory_seconds.value),
      package_inventory_seconds: Number(form.elements.package_inventory_seconds.value),
      package_full_seconds: Number(form.elements.package_full_seconds.value),
      docker_inventory_seconds: Number(form.elements.docker_inventory_seconds.value),
    },
    actions,
    update: {
      allow_self_update: Boolean(form.elements.allow_self_update.checked),
      channel: form.elements.update_channel.value,
    },
    max_output_bytes: Number(form.elements.max_output_bytes.value),
  };
}

async function saveProfile(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setText("profile-error", "");
  const body = {
    name: form.elements.name.value.trim(),
    description: form.elements.description.value.trim(),
    settings: profileSettingsFromForm(form),
  };
  try {
    if (state.editingProfile) {
      await api(`/api/profiles/${state.editingProfile.id}`, { method: "PATCH", body });
      toast("Profile updated.");
    } else {
      await api("/api/profiles", {
        method: "POST",
        body: { ...body, slug: form.elements.slug.value.trim() },
      });
      toast("Profile created.");
    }
    resetProfileForm();
    await loadProfiles();
  } catch (error) {
    setText("profile-error", error.message);
  }
}

async function resetProfile(profile) {
  if (!profile?.is_system) return;
  try {
    await api(`/api/profiles/${profile.id}/reset`, { method: "POST", body: {} });
    toast(`${profile.name} reset to defaults.`);
    resetProfileForm();
    await loadProfiles();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteProfile(profile) {
  if (!profile || profile.is_system) return;
  try {
    await api(`/api/profiles/${profile.id}`, { method: "DELETE" });
    toast(`${profile.name} deleted.`);
    resetProfileForm();
    await loadProfiles();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function assignProfileTag(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const tag = form.elements.tag.value.trim();
  const profile = form.elements.profile.value;
  if (!tag || !profile) return;
  try {
    const result = await api(`/api/profile-tags/${encodeURIComponent(tag)}`, {
      method: "PUT",
      body: { profile },
    });
    toast(`Tag ${tag} assigned to ${profile} (${result.agents} agent${result.agents === 1 ? "" : "s"}).`);
    form.elements.tag.value = "";
    await loadProfiles();
    loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteProfileTag(tag) {
  try {
    await api(`/api/profile-tags/${encodeURIComponent(tag)}`, { method: "DELETE" });
    toast(`Tag ${tag} unassigned.`);
    await loadProfiles();
    loadDashboard(true);
  } catch (error) {
    toast(error.message, "error");
  }
}


function populateRemoteProfileSelect() {
  const select = $("remote-install-profile");
  if (!select) return;
  const current = select.value || "workstation";
  clear(select);
  for (const profile of state.profiles) {
    select.append(h("option", { value: profile.slug }, `${profile.name} (${profile.slug})`));
  }
  select.value = state.profiles.some((profile) => profile.slug === current) ? current : "workstation";
}

async function loadRemoteInstall() {
  populateRemoteProfileSelect();
  try {
    const data = await api("/api/remote-install");
    state.remoteJobs = data.jobs || [];
    renderRemoteJobs();
    if (state.remoteJobId) showRemoteJob(state.remoteJobId);
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderRemoteJobs() {
  const container = $("remote-install-list");
  if (!container) return;
  clear(container);
  if (!state.remoteJobs.length) {
    container.append(h("div", { class: "empty-inline" }, "No remote install jobs yet."));
    return;
  }
  for (const job of state.remoteJobs) {
    container.append(
      h(
        "div",
        { class: "list-row" },
        h(
          "div",
          {},
          h("strong", {}, `${job.user}@${job.host}:${job.port}`),
          h("p", { class: "form-hint" }, `${job.status} · ${job.profile} · ${localTime(job.created_at)}${job.error ? ` · ${job.error}` : ""}`),
        ),
        button("View log", "button-secondary button-small", () => showRemoteJob(job.id)),
      ),
    );
  }
}

function scheduleRemotePoll() {
  if (state.remoteTimer) clearTimeout(state.remoteTimer);
  state.remoteTimer = setTimeout(async () => {
    state.remoteTimer = null;
    if (!state.remoteJobId) return;
    await showRemoteJob(state.remoteJobId);
    if (state.page === "remote-install" && state.remoteJobs.some((job) => ["queued", "running"].includes(job.status))) {
      scheduleRemotePoll();
    }
  }, 1500);
}

async function showRemoteJob(jobId) {
  state.remoteJobId = jobId;
  try {
    const job = await api(`/api/remote-install/${jobId}`);
    const log = $("remote-install-log");
    if (log) log.textContent = job.log || "No output yet.";
    const index = state.remoteJobs.findIndex((item) => item.id === job.id);
    if (index >= 0) state.remoteJobs[index] = job;
    if (["queued", "running"].includes(job.status)) scheduleRemotePoll();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function testRemoteInstall() {
  const form = $("remote-install-form");
  const log = $("remote-install-log");
  setText("remote-install-error", "");
  const body = {
    host: form.elements.host.value.trim(),
    port: Number(form.elements.port.value || 22),
    user: form.elements.user.value.trim(),
    name: form.elements.name.value.trim(),
    profile: form.elements.profile.value,
    watch_services: [],
  };
  if (log) log.textContent = "Testing SSH and prerequisites…";
  try {
    const result = await api("/api/remote-install/test", { method: "POST", body });
    if (log) log.textContent = result.output || "Connection OK.";
  } catch (error) {
    setText("remote-install-error", error.message);
    if (log) log.textContent = error.message;
  }
}

async function startRemoteInstall(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setText("remote-install-error", "");
  const watch = form.elements.watch_services.value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const body = {
    host: form.elements.host.value.trim(),
    port: Number(form.elements.port.value || 22),
    user: form.elements.user.value.trim(),
    name: form.elements.name.value.trim(),
    profile: form.elements.profile.value,
    watch_services: watch,
  };
  try {
    const job = await api("/api/remote-install", { method: "POST", body });
    toast("Remote install started.");
    state.remoteJobId = job.id;
    await loadRemoteInstall();
    await showRemoteJob(job.id);
  } catch (error) {
    setText("remote-install-error", error.message);
  }
}


const DOCKER_REASON_LABELS = {
  disabled: "Docker is disabled in the local agent config or profile",
  unsupported_os: "Windows Docker is not supported",
  not_installed: "Docker CLI is not installed",
  socket_missing: "Docker socket is missing",
  permission_denied: "The agent cannot access the Docker socket",
  daemon_unreachable: "The Docker daemon is unreachable",
  compose_missing: "Docker Compose v2 is not installed",
  probe_error: "Docker probe failed",
};

function dockerReason(reason) {
  return DOCKER_REASON_LABELS[reason] || (reason ? "Docker is unavailable" : "");
}

function dockerAge(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "never";
  if (value < 60) return `${Math.max(0, Math.round(value))}s ago`;
  if (value < 3600) return `${Math.floor(value / 60)}m ago`;
  if (value < 86400) return `${Math.floor(value / 3600)}h ago`;
  return `${Math.floor(value / 86400)}d ago`;
}

function stopDockerRefreshPoll() {
  if (state.dockerRefreshTimer) {
    clearTimeout(state.dockerRefreshTimer);
    state.dockerRefreshTimer = null;
  }
}

async function loadDockerHosts(silent = false) {
  if (state.dockerLoading) return;
  state.dockerLoading = true;
  try {
    const data = await api("/api/docker/hosts");
    state.dockerHosts = data.hosts || [];
    if (state.dockerHostId && !state.dockerHosts.some((host) => host.agent_id === state.dockerHostId)) {
      closeDockerHost();
    }
    renderDockerHostList();
  } catch (error) {
    if (!silent) toast(error.message, "error");
    const status = $("docker-hosts-status");
    if (status) status.textContent = error.message;
  } finally {
    state.dockerLoading = false;
  }
}

function renderDockerHostList() {
  const container = $("docker-host-list");
  const empty = $("docker-hosts-empty");
  if (!container || !empty) return;
  clear(container);
  const hosts = state.dockerHosts || [];
  empty.hidden = hosts.length > 0;
  const status = $("docker-hosts-status");
  if (status) {
    status.textContent = hosts.length
      ? `${hosts.length} Docker host${hosts.length === 1 ? "" : "s"} reporting. Select one to inspect its cached inventory.`
      : "No Docker hosts are reporting yet.";
  }
  for (const host of hosts) {
    const selected = host.agent_id === state.dockerHostId;
    const row = h(
      "div",
      { class: "list-row" },
      h(
        "div",
        {},
        h(
          "strong",
          {},
          host.name,
          " ",
          host.online ? statusPill("online", "online") : statusPill("offline", "neutral"),
          " ",
          host.stale ? statusPill("stale", "warning") : statusPill("fresh", "online"),
        ),
        h(
          "p",
          { class: "form-hint" },
          `${host.hostname || host.agent_id} · Docker ${host.engine_version || "unknown"} · Compose ${host.compose_available ? host.compose_version : "missing"}${host.rootless ? " · rootless" : ""}`,
        ),
        h(
          "p",
          { class: "form-hint" },
          `${host.counts.running} running · ${host.counts.stopped} stopped · ${host.counts.unhealthy} unhealthy · ${host.counts.images} images · ${host.counts.volumes} volumes · ${host.counts.networks} networks · inventory ${dockerAge(host.age_seconds)}`,
        ),
        host.reason
          ? h("p", { class: "form-hint" }, dockerReason(host.reason))
          : null,
      ),
      h(
        "div",
        { class: "list-row-actions" },
        button(selected ? "Selected" : "Open", selected ? "button-ghost button-small" : "button-secondary button-small", () => openDockerHost(host.agent_id)),
      ),
    );
    container.append(row);
  }
}

function openDockerHost(agentId) {
  stopDockerRefreshPoll();
  state.dockerHostId = agentId;
  state.dockerTab = "overview";
  state.dockerHostDetail = null;
  const detail = $("docker-host-detail");
  if (detail) detail.hidden = false;
  renderDockerHostList();
  setText("docker-host-title", "Loading Docker host…");
  setText("docker-host-meta", "Fetching the latest cached inventory.");
  const body = $("docker-host-body");
  if (body) {
    clear(body);
    body.append(h("div", { class: "empty-inline" }, "Loading containers, images, volumes, and networks…"));
  }
  loadDockerHostDetail(agentId);
}

function closeDockerHost() {
  stopDockerRefreshPoll();
  state.dockerHostId = null;
  state.dockerHostDetail = null;
  const detail = $("docker-host-detail");
  if (detail) detail.hidden = true;
  renderDockerHostList();
}

async function loadDockerHostDetail(agentId, silent = false) {
  try {
    const data = await api(`/api/docker/hosts/${encodeURIComponent(agentId)}`);
    if (state.dockerHostId !== agentId) return;
    state.dockerHostDetail = data;
    renderDockerHostDetail();
  } catch (error) {
    if (!silent) toast(error.message, "error");
    if (/not found/i.test(error.message)) closeDockerHost();
  }
}

function renderDockerHostDetail() {
  const detail = state.dockerHostDetail;
  const host = detail?.host;
  if (!host) return;
  setText("docker-host-title", host.name);
  setText("docker-host-eyebrow", host.hostname || host.agent_id);
  const stale = host.stale ? " · stale" : "";
  setText(
    "docker-host-meta",
    `${host.online ? "online" : "offline"} · Docker ${host.engine_version || "unknown"} · Compose ${host.compose_available ? host.compose_version : "missing"} · inventory ${dockerAge(host.age_seconds)}${stale}`,
  );
  const notice = $("docker-host-notice");
  if (notice) {
    clear(notice);
    const messages = [];
    if (!host.available) messages.push(`${dockerReason(host.reason)}. Showing the last cached inventory.`);
    else if (!host.online) messages.push("The agent is offline. Showing cached inventory; mutations are unavailable in this phase.");
    else if (host.stale) messages.push(`Cached inventory is older than ${Math.round((detail.stale_seconds || 900) / 60)} minutes. Use Refresh now.`);
    if (host.reason === "compose_missing") messages.push("Compose v2 was not detected on this host.");
    notice.hidden = messages.length === 0;
    for (const message of messages) notice.append(h("p", {}, message));
  }
  const refresh = $("docker-refresh-host");
  if (refresh) {
    refresh.disabled = !host.available || !state.permissions.operate;
    refresh.title = host.available
      ? "Queue a read-only Docker inventory refresh"
      : "Docker is not currently reachable on this host";
  }
  document.querySelectorAll("#docker-host-tabs .tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.dockerTab === state.dockerTab);
  });
  renderDockerTab();
}

function renderDockerTab() {
  const body = $("docker-host-body");
  const detail = state.dockerHostDetail;
  if (!body || !detail) return;
  clear(body);
  if (state.dockerTab === "containers") return renderDockerContainers(body, detail);
  if (state.dockerTab === "images") return renderDockerImages(body, detail);
  if (state.dockerTab === "volumes") return renderDockerVolumes(body, detail);
  if (state.dockerTab === "networks") return renderDockerNetworks(body, detail);
  renderDockerOverview(body, detail);
}

function renderDockerOverview(body, detail) {
  const host = detail.host;
  body.append(
    section(
      "Docker host",
      "Overview",
      h(
        "div",
        { class: "info-grid" },
        infoTile("Engine", host.engine_version || "unknown"),
        infoTile("API", host.api_version || "—"),
        infoTile("Compose", host.compose_available ? host.compose_version : "missing"),
        infoTile("Rootless", host.rootless ? "yes" : "no"),
        infoTile("Storage driver", host.storage_driver || "—"),
        infoTile("Cgroup driver", host.cgroup_driver || "—"),
        infoTile("Docker root", host.docker_root_dir || "—"),
        infoTile("Inventory revision", host.inventory_revision),
      ),
      h(
        "div",
        { class: "info-grid" },
        infoTile("Containers", host.counts.containers),
        infoTile("Running", host.counts.running),
        infoTile("Stopped", host.counts.stopped),
        infoTile("Unhealthy", host.counts.unhealthy),
        infoTile("Images", host.counts.images),
        infoTile("Volumes", host.counts.volumes),
        infoTile("Networks", host.counts.networks),
        infoTile("Inventory age", dockerAge(host.age_seconds)),
      ),
      h(
        "p",
        { class: "form-hint" },
        `Last collected ${host.collected_at ? localTime(host.collected_at) : "never"}. Heartbeat ${host.heartbeat_at ? relativeTime(host.heartbeat_at) : "not reported"}.`,
      ),
    ),
  );
}

function renderDockerContainers(body, detail) {
  const containers = detail.containers || [];
  if (!containers.length) {
    body.append(section("Docker host", "Containers", h("div", { class: "empty-inline" }, "No containers reported. Refresh inventory if the host recently started a container.")));
    return;
  }
  const rows = containers.map((container) =>
    h(
      "tr",
      {},
      h("td", {}, h("strong", {}, container.name || short(container.id, 12))),
      h("td", { class: "mono" }, container.image || "—"),
      h("td", {}, container.state || "—"),
      h("td", {}, container.health ? statusPill(container.health, container.health === "unhealthy" ? "critical" : "online") : "—"),
      h("td", {}, (container.ports || []).join(", ") || "—"),
      h("td", {}, container.compose_project || "—"),
      h("td", {}, container.status || "—"),
    ),
  );
  body.append(
    section(
      "Docker host",
      "Containers",
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Image"), h("th", {}, "State"), h("th", {}, "Health"), h("th", {}, "Ports"), h("th", {}, "Compose project"), h("th", {}, "Uptime / status"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter containers", pageSize: 25 },
      ),
    ),
  );
}

function renderDockerImages(body, detail) {
  const images = detail.images || [];
  if (!images.length) {
    body.append(section("Docker host", "Images", h("div", { class: "empty-inline" }, "No images reported.")));
    return;
  }
  const rows = images.map((image) =>
    h(
      "tr",
      {},
      h("td", {}, (image.repo_tags || []).join(", ") || (image.dangling ? "<none>" : short(image.id, 16))),
      h("td", { class: "mono" }, short(image.id, 20)),
      h("td", {}, fmtBytes(image.size)),
      h("td", {}, image.dangling ? statusPill("dangling", "warning") : statusPill("tagged", "online")),
      h("td", {}, image.containers),
    ),
  );
  body.append(
    section(
      "Docker host",
      "Images",
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Repository / tags"), h("th", {}, "Image ID"), h("th", {}, "Size"), h("th", {}, "State"), h("th", {}, "Containers"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter images", pageSize: 25 },
      ),
    ),
  );
}

function renderDockerVolumes(body, detail) {
  const volumes = detail.volumes || [];
  if (!volumes.length) {
    body.append(section("Docker host", "Volumes", h("div", { class: "empty-inline" }, "No volumes reported.")));
    return;
  }
  const rows = volumes.map((volume) =>
    h(
      "tr",
      {},
      h("td", {}, h("strong", {}, volume.name)),
      h("td", {}, volume.driver || "—"),
      h("td", {}, volume.scope || "—"),
      h("td", {}, volume.in_use ? "yes" : "unknown"),
      h("td", { class: "mono" }, short(volume.mountpoint || "—", 72)),
    ),
  );
  body.append(
    section(
      "Docker host",
      "Volumes",
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Driver"), h("th", {}, "Scope"), h("th", {}, "In use"), h("th", {}, "Mountpoint"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter volumes", pageSize: 25 },
      ),
    ),
  );
}

function renderDockerNetworks(body, detail) {
  const networks = detail.networks || [];
  if (!networks.length) {
    body.append(section("Docker host", "Networks", h("div", { class: "empty-inline" }, "No networks reported.")));
    return;
  }
  const rows = networks.map((network) =>
    h(
      "tr",
      {},
      h("td", {}, h("strong", {}, network.name || short(network.id, 16))),
      h("td", {}, network.driver || "—"),
      h("td", {}, network.scope || "—"),
      h("td", {}, network.internal ? "yes" : "no"),
      h("td", {}, network.container_count),
    ),
  );
  body.append(
    section(
      "Docker host",
      "Networks",
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Driver"), h("th", {}, "Scope"), h("th", {}, "Internal"), h("th", {}, "Containers"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter networks", pageSize: 25 },
      ),
    ),
  );
}

async function refreshDockerHost() {
  const agentId = state.dockerHostId;
  if (!agentId) return;
  const refresh = $("docker-refresh-host");
  if (refresh) refresh.disabled = true;
  const baseline = state.dockerHostDetail?.host?.inventory_at || "";
  const status = $("docker-hosts-status");
  if (status) status.textContent = "Queueing Docker inventory refresh…";
  try {
    await api(`/api/docker/hosts/${encodeURIComponent(agentId)}/refresh`, { method: "POST", body: {} });
    if (status) status.textContent = "Docker inventory refresh queued. Waiting for the agent heartbeat…";
    startDockerRefreshPoll(agentId, baseline);
  } catch (error) {
    if (status) status.textContent = error.message;
    toast(error.message, "error");
    if (refresh) refresh.disabled = !state.dockerHostDetail?.host?.available;
  }
}

function startDockerRefreshPoll(agentId, baseline) {
  stopDockerRefreshPoll();
  let attempts = 0;
  const tick = async () => {
    if (state.dockerHostId !== agentId) return;
    attempts += 1;
    await loadDockerHostDetail(agentId, true);
    const current = state.dockerHostDetail?.host?.inventory_at || "";
    if (current && current !== baseline) {
      await loadDockerHosts(true);
      const status = $("docker-hosts-status");
      if (status) status.textContent = "Docker inventory updated.";
      stopDockerRefreshPoll();
      return;
    }
    if (attempts >= 10) {
      const status = $("docker-hosts-status");
      if (status) status.textContent = "Refresh queued; the agent has not reported new inventory yet.";
      const refresh = $("docker-refresh-host");
      if (refresh) refresh.disabled = !state.dockerHostDetail?.host?.available;
      return;
    }
    state.dockerRefreshTimer = setTimeout(tick, 3000);
  };
  state.dockerRefreshTimer = setTimeout(tick, 1200);
}

function renderSecurity() {
  const details = $("security-details");
  clear(details);
  if (!state.user) return;
  const entries = [
    ["Signed in as", state.user.display_name || state.user.username],
    ["Username", state.user.username],
    ["Role", state.user.role],
    ["Source", state.user.source === "ad" ? "Active Directory / Samba" : "Local account"],
    ["Directory auth", state.settings?.ldap_enabled ? "Enabled" : "Not configured"],
    ["Console version", state.settings?.version || "—"],
    ["Transport", location.protocol === "https:" ? "HTTPS" : "HTTP (put TLS in front before remote use)"],
    ["Session", "Signed, HttpOnly, SameSite=Strict cookie"],
  ];
  for (const [key, value] of entries) {
    details.append(h("dt", {}, key), h("dd", {}, value));
  }
  updateChrome();
}

function saveFleetFilters() {
  try {
    localStorage.setItem("sextant.fleetFilters", JSON.stringify(state.fleetFilters));
  } catch (_error) {
    // Storage may be unavailable in private contexts.
  }
}

function loadFleetFilters() {
  try {
    const raw = localStorage.getItem("sextant.fleetFilters");
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      state.fleetFilters = {
        status: FLEET_STATUS_VALUES.includes(parsed.status) ? parsed.status : "all",
        profile: typeof parsed.profile === "string" ? parsed.profile : "",
        tag: typeof parsed.tag === "string" ? parsed.tag : "",
      };
    }
  } catch (_error) {
    // Ignore corrupt storage.
  }
}

function normalizeSavedView(view) {
  const source = view && typeof view.filters === "object" && view.filters ? view.filters : view || {};
  return {
    id: typeof view?.id === "string" ? view.id : "",
    name: typeof view?.name === "string" ? view.name.trim() : "",
    status: FLEET_STATUS_VALUES.includes(source.status) ? source.status : "all",
    profile: typeof source.profile === "string" ? source.profile : "",
    tag: typeof source.tag === "string" ? source.tag : "",
    search: typeof source.search === "string" ? source.search : "",
  };
}

function readLocalSavedViews() {
  try {
    const raw = localStorage.getItem("sextant.savedViews");
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(normalizeSavedView)
      .filter((view) => view.name);
  } catch (_error) {
    return [];
  }
}

function loadSavedViews() {
  state.savedViews = readLocalSavedViews();
}

function persistSavedViews() {
  try {
    localStorage.setItem("sextant.savedViews", JSON.stringify(state.savedViews));
  } catch (_error) {
    // Storage may be unavailable in private contexts.
  }
}

function clearLocalSavedViews() {
  try {
    localStorage.removeItem("sextant.savedViews");
  } catch (_error) {
    // Storage may be unavailable in private contexts.
  }
}

async function migrateLocalSavedViews() {
  const local = readLocalSavedViews();
  if (!local.length) return;
  let complete = true;
  const existing = new Set(state.savedViews.map((view) => view.name.toLowerCase()));
  for (const view of local) {
    if (existing.has(view.name.toLowerCase())) continue;
    try {
      const data = await api("/api/saved-views", {
        method: "POST",
        body: {
          name: view.name,
          filters: { status: view.status, profile: view.profile, tag: view.tag, search: view.search },
        },
      });
      const created = normalizeSavedView(data.view || view);
      state.savedViews.push(created);
      existing.add(created.name.toLowerCase());
    } catch (error) {
      if (/already exists/i.test(error.message)) {
        existing.add(view.name.toLowerCase());
        continue;
      }
      complete = false;
    }
  }
  state.savedViews.sort((a, b) => a.name.localeCompare(b.name));
  if (complete) clearLocalSavedViews();
}

async function loadSavedViewsFromServer() {
  try {
    const data = await api("/api/saved-views");
    state.savedViews = (data.views || []).map(normalizeSavedView).filter((view) => view.name);
    await migrateLocalSavedViews();
    renderSavedViews();
  } catch (error) {
    loadSavedViews();
    renderSavedViews();
    toast(`Saved views are unavailable from the server (${error.message}). Showing this browser's local copy.`, "warn");
  }
}

function updateSavedViewControls() {
  const select = $("saved-view-select");
  const remove = $("delete-view");
  if (remove) remove.disabled = !select || !select.value;
}

function renderSavedViews(selected = "") {
  const select = $("saved-view-select");
  if (!select) return;
  clear(select);
  select.append(h("option", { value: "" }, "No saved view"));
  for (const view of state.savedViews) select.append(h("option", { value: view.name }, view.name));
  select.value = state.savedViews.some((view) => view.name === selected) ? selected : "";
  updateSavedViewControls();
}

async function saveCurrentFleetView() {
  const answer = window.prompt("Name this saved view", "");
  if (answer === null) return;
  const name = answer.trim();
  if (!name) {
    toast("Saved view needs a name.", "warn");
    return;
  }
  const view = {
    name,
    status: state.fleetFilters.status,
    profile: state.fleetFilters.profile,
    tag: state.fleetFilters.tag,
    search: state.search,
  };
  const existing = state.savedViews.find((item) => item.name.toLowerCase() === name.toLowerCase());
  try {
    if (existing?.id) {
      await api(`/api/saved-views/${encodeURIComponent(existing.id)}`, { method: "DELETE" });
    }
    const data = await api("/api/saved-views", {
      method: "POST",
      body: {
        name,
        filters: { status: view.status, profile: view.profile, tag: view.tag, search: view.search },
      },
    });
    const saved = normalizeSavedView(data.view || view);
    const index = state.savedViews.findIndex((item) => item.name.toLowerCase() === name.toLowerCase());
    if (index >= 0) state.savedViews[index] = saved;
    else state.savedViews.push(saved);
    state.savedViews.sort((a, b) => a.name.localeCompare(b.name));
    if (state.savedViews.every((item) => item.id)) clearLocalSavedViews();
    renderSavedViews(saved.name);
    toast(`Saved view "${saved.name}".`);
  } catch (error) {
    const index = state.savedViews.findIndex((item) => item.name.toLowerCase() === name.toLowerCase());
    if (index >= 0) state.savedViews[index] = view;
    else state.savedViews.push(view);
    persistSavedViews();
    renderSavedViews(name);
    toast(`Could not save the view to the server: ${error.message}. It was kept in this browser and will sync later.`, "warn");
  }
}

function applySavedView(name) {
  const view = state.savedViews.find((item) => item.name === name);
  if (!view) {
    updateSavedViewControls();
    return;
  }
  state.fleetFilters = { status: view.status, profile: view.profile, tag: view.tag };
  state.search = view.search;
  $("search-input").value = view.search;
  saveFleetFilters();
  resetFleetRenderLimit();
  renderAgentGrid();
  updateSavedViewControls();
}

async function deleteSavedView() {
  const select = $("saved-view-select");
  const name = select ? select.value : "";
  if (!name) return;
  const view = state.savedViews.find((item) => item.name === name);
  if (!view) return;
  if (view.id) {
    try {
      await api(`/api/saved-views/${encodeURIComponent(view.id)}`, { method: "DELETE" });
    } catch (error) {
      toast(`Could not delete the saved view: ${error.message}`, "error");
      return;
    }
  }
  state.savedViews = state.savedViews.filter((item) => item.name !== name);
  if (state.savedViews.every((item) => item.id)) clearLocalSavedViews();
  else persistSavedViews();
  renderSavedViews();
  toast(`Deleted saved view "${name}".`);
}

async function init() {
  loadTheme();
  loadFleetFilters();
  loadSavedViews();
  wireEvents();
  renderSavedViews();
  try {
    const config = await api("/api/auth/config");
    if (config.ldap_enabled) $("login-mode-wrap").hidden = false;
    setText("login-version", `Sextant v${config.version || ""}`);
  } catch (_error) {
    // Login remains usable with local authentication defaults.
  }
  try {
    const me = await api("/api/me");
    enterApp(me);
  } catch (_error) {
    showLogin();
  }
}

function wireEvents() {
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const buttonEl = $("login-submit");
    setText("login-error", "");
    buttonEl.disabled = true;
    buttonEl.textContent = "Signing in…";
    try {
      const data = await api("/api/auth/login", {
        method: "POST",
        body: {
          username: $("login-username").value.trim(),
          password: $("login-password").value,
          mode: $("login-mode").value,
        },
      });
      state.csrf = data.csrf;
      enterApp(data);
    } catch (error) {
      setText("login-error", error.message);
    } finally {
      buttonEl.disabled = false;
      buttonEl.textContent = "Sign in";
    }
  });

  $("logout-button").addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: {} });
    } catch (_error) {
      // Clear the local view even if the session already expired.
    }
    showLogin("Signed out.");
  });

  document.querySelectorAll("[data-page]").forEach((el) => {
    el.addEventListener("click", () => navigate(el.dataset.page));
  });

  document.querySelectorAll("#drawer-tabs .tab").forEach((el) => {
    el.addEventListener("click", () => {
      state.detailTab = el.dataset.tab;
      document.querySelectorAll("#drawer-tabs .tab").forEach((tab) => tab.classList.toggle("is-active", tab === el));
      renderTab();
    });
  });

  document.querySelectorAll("[data-close-drawer]").forEach((el) => {
    el.addEventListener("click", closeDrawer);
  });

  $("mobile-menu").addEventListener("click", () => {
    const open = !$("sidebar").classList.contains("is-open");
    $("sidebar").classList.toggle("is-open", open);
    $("sidebar-scrim").hidden = !open;
  });
  $("sidebar-scrim").addEventListener("click", () => {
    $("sidebar").classList.remove("is-open");
    $("sidebar-scrim").hidden = true;
  });

  $("refresh-button").addEventListener("click", async () => {
    if (state.page === "overview") await loadDashboard(true);
    else navigate(state.page);
    if (state.detailId) loadAgentDetail(state.detailId);
    announceFleetStatus(fleetSummary("Fleet refreshed"));
  });

  $("docker-refresh-list")?.addEventListener("click", () => loadDockerHosts());
  $("docker-refresh-host")?.addEventListener("click", refreshDockerHost);
  $("docker-close-host")?.addEventListener("click", closeDockerHost);
  document.querySelectorAll("#docker-host-tabs .tab").forEach((el) => {
    el.addEventListener("click", () => {
      state.dockerTab = el.dataset.dockerTab;
      document.querySelectorAll("#docker-host-tabs .tab").forEach((tab) => {
        tab.classList.toggle("is-active", tab === el);
      });
      renderDockerTab();
    });
  });

  $("theme-toggle")?.addEventListener("click", toggleTheme);
  $("command-palette-button")?.addEventListener("click", openCommandPalette);
  document.querySelectorAll("[data-close-palette]").forEach((el) => {
    el.addEventListener("click", () => closeCommandPalette());
  });
  $("command-palette-input")?.addEventListener("input", (event) => {
    renderCommandPalette(event.target.value);
  });
  $("command-palette-input")?.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveCommandPalette(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveCommandPalette(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      runCommandPaletteSelection();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeCommandPalette();
    } else if (event.key === "Tab") {
      event.preventDefault();
      event.target.focus();
    }
  });

  $("search-input").addEventListener("input", (event) => {
    state.search = event.target.value;
    resetFleetRenderLimit();
    renderAgentGrid();
  });

  $("filter-status").addEventListener("change", (event) => {
    state.fleetFilters.status = event.target.value;
    saveFleetFilters();
    resetFleetRenderLimit();
    renderAgentGrid();
  });
  $("filter-profile").addEventListener("change", (event) => {
    state.fleetFilters.profile = event.target.value;
    saveFleetFilters();
    resetFleetRenderLimit();
    renderAgentGrid();
  });
  $("filter-tag").addEventListener("change", (event) => {
    state.fleetFilters.tag = event.target.value;
    saveFleetFilters();
    resetFleetRenderLimit();
    renderAgentGrid();
  });
  $("filter-clear").addEventListener("click", () => {
    state.fleetFilters = { status: "all", profile: "", tag: "" };
    state.search = "";
    $("search-input").value = "";
    saveFleetFilters();
    resetFleetRenderLimit();
    renderSavedViews();
    renderAgentGrid();
  });
  $("fleet-more-button")?.addEventListener("click", () => {
    state.fleetRenderLimit += FLEET_PAGE_SIZE;
    renderAgentGrid();
  });
  $("saved-view-select").addEventListener("change", (event) => {
    applySavedView(event.target.value);
  });
  $("save-view").addEventListener("click", saveCurrentFleetView);
  $("delete-view").addEventListener("click", deleteSavedView);
  $("export-agents").addEventListener("click", () => {
    window.location.href = "/api/export/agents.csv";
  });

  $("bulk-clear").addEventListener("click", () => {
    state.selectedAgents.clear();
    clearBulkResult();
    renderAgentGrid();
  });
  $("bulk-refresh").addEventListener("click", bulkRefresh);
  $("bulk-wake").addEventListener("click", bulkWake);
  $("bulk-reboot").addEventListener("click", () => bulkPower("reboot"));
  $("bulk-shutdown").addEventListener("click", () => bulkPower("shutdown"));
  $("bulk-service-start").addEventListener("click", () => bulkService("start"));
  $("bulk-service-stop").addEventListener("click", () => bulkService("stop"));
  $("bulk-service-restart").addEventListener("click", () => bulkService("restart"));
  $("bulk-service-name").addEventListener("input", updateBulkControls);
  $("bulk-profile").addEventListener("change", updateBulkControls);
  $("bulk-profile-apply").addEventListener("click", bulkApplyProfile);

  $("wake-target-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setText("wake-target-error", "");
    try {
      await api("/api/wake-targets", {
        method: "POST",
        body: {
          name: String(values.get("name") || ""),
          mac: String(values.get("mac") || ""),
          broadcast: String(values.get("broadcast") || "255.255.255.255"),
          port: Number(values.get("port") || 9),
        },
      });
      form.reset();
      form.querySelector('[name="broadcast"]').value = "255.255.255.255";
      toast("Wake target saved.");
      loadWakeTargets();
    } catch (error) {
      setText("wake-target-error", error.message);
    }
  });

  $("enrollment-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setText("enrollment-error", "");
    try {
      const data = await api("/api/enrollment-tokens", {
        method: "POST",
        body: { label: String(values.get("label") || ""), expires_minutes: Number(values.get("expires_minutes") || 30) },
      });
      $("enrollment-result").hidden = false;
      setText("enrollment-token", data.token);
      setText("enrollment-command", data.install_command);
      $("enrollment-result").dataset.command = data.install_command;
      loadEnrollmentTokens();
      toast("Enrollment token created. Copy it now.");
    } catch (error) {
      setText("enrollment-error", error.message);
    }
  });

  $("copy-enrollment").addEventListener("click", async () => {
    const command = $("enrollment-result").dataset.command || "";
    if (await copyText(command)) toast("Install command copied.");
    else toast("Could not copy. Select the text manually.", "warn");
  });

  $("audit-refresh").addEventListener("click", loadAudit);

  $("profile-form").addEventListener("submit", saveProfile);
  $("profile-cancel").addEventListener("click", resetProfileForm);
  $("profile-reset").addEventListener("click", () => {
    if (state.editingProfile) resetProfile(state.editingProfile);
  });
  $("profile-delete").addEventListener("click", () => {
    if (state.editingProfile) deleteProfile(state.editingProfile);
  });
  $("tag-profile-form").addEventListener("submit", assignProfileTag);

  $("remote-install-form").addEventListener("submit", startRemoteInstall);
  $("remote-install-test").addEventListener("click", testRemoteInstall);

  $("user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setText("user-error", "");
    try {
      await api("/api/users", {
        method: "POST",
        body: {
          username: String(values.get("username") || ""),
          display_name: String(values.get("display_name") || ""),
          role: String(values.get("role") || "operator"),
          password: String(values.get("password") || ""),
        },
      });
      form.reset();
      toast("Local user created.");
      loadUsers();
    } catch (error) {
      setText("user-error", error.message);
    }
  });

  $("password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setText("password-error", "");
    try {
      const data = await api("/api/me/password", {
        method: "POST",
        body: {
          current_password: String(values.get("current_password") || ""),
          new_password: String(values.get("new_password") || ""),
        },
      });
      state.csrf = data.csrf;
      state.user = data.user;
      form.reset();
      updateChrome();
      toast("Password updated. Other sessions were revoked.");
    } catch (error) {
      setText("password-error", error.message);
    }
  });

  $("agent-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const agent = state.detailAgent;
    if (!agent) return;
    const form = event.currentTarget;
    setText("agent-edit-error", "");
    try {
      await api(`/api/agents/${agent.id}`, {
        method: "PATCH",
        body: {
          name: form.elements.name.value.trim(),
          tags: splitList(form.elements.tags.value),
          watch_services: splitList(form.elements.watch_services.value),
          notes: form.elements.notes.value.trim(),
          allow_shell: form.elements.allow_shell.checked,
          allow_power: form.elements.allow_power.checked,
          allow_service: form.elements.allow_service.checked,
        },
      });
      $("agent-edit-dialog").close();
      toast("Agent policy saved. It applies on the next heartbeat.");
      loadAgentDetail(agent.id);
      loadDashboard(true);
    } catch (error) {
      setText("agent-edit-error", error.message);
    }
  });

  document.querySelectorAll("[data-close-agent-edit]").forEach((el) => {
    el.addEventListener("click", () => $("agent-edit-dialog").close());
  });

  $("reset-password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const user = state.resetUser;
    if (!user) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setText("reset-password-error", "");
    try {
      const data = await api(`/api/users/${user.id}/reset-password`, {
        method: "POST",
        body: { new_password: String(values.get("new_password") || "") },
      });
      if (data.csrf) {
        state.csrf = data.csrf;
        if (data.user) state.user = { ...state.user, ...data.user };
        updateChrome();
      }
      $("reset-password-dialog").close();
      form.reset();
      toast(`Password reset for ${user.username}. Existing sessions were revoked.`);
      loadUsers();
    } catch (error) {
      setText("reset-password-error", error.message);
    }
  });

  document.querySelectorAll("[data-close-reset-password]").forEach((el) => {
    el.addEventListener("click", () => $("reset-password-dialog").close());
  });

  document.querySelectorAll("[data-close-sessions]").forEach((el) => {
    el.addEventListener("click", () => $("sessions-dialog").close());
  });

  $("revoke-all-sessions").addEventListener("click", revokeAllUserSessions);

  document.addEventListener("keydown", (event) => {
    const paletteOpen = !$("command-palette").hidden;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (paletteOpen) closeCommandPalette();
      else openCommandPalette();
      return;
    }
    if (event.key === "Escape" && paletteOpen) {
      event.preventDefault();
      closeCommandPalette();
      return;
    }
    if (event.key === "Escape" && !$("agent-drawer").hidden) closeDrawer();
    if (event.key === "Escape") {
      $("sidebar").classList.remove("is-open");
      $("sidebar-scrim").hidden = true;
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!state.user) return;
    if (document.hidden) {
      stopLiveUpdates();
      return;
    }
    loadDashboard(true);
    startLiveUpdates();
  });
}

init();
