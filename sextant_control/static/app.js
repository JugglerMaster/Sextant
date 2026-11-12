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
  dockerContainerFilter: "all",
  dockerActionBusy: false,
  dockerDialogTrigger: null,
  dockerCleanup: null,
  composeStacks: [],
  composeStackId: null,
  composeDetail: null,
  composeTab: "files",
  composeDraftFiles: {},
  composeFile: "compose.yaml",
  composeSettings: null,
  composeLoading: false,
  dockerConsole: null,
  dockerConsoleSource: null,
  dockerConsoleBuffer: "",
  dockerConsoleIdleTimer: null,
  dockerConsoleIdleSeconds: 900,
  dockerConsoleLastActivity: 0,
  dockerConsoleResizeTimer: null,
  dockerConsoleTrigger: null,
  dockerAutoTimer: null,
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
  updates: null,
  updatesBusy: false,
  updatesNotice: "",
  settingsTab: "profile",
  systemSettings: null,
  systemSettingsLoading: false,
  domainJoin: null,
  domainJoinTimer: null,
  fleetStatusSignature: "",
  paletteItems: [],
  paletteIndex: 0,
  paletteTrigger: null,
  discovery: {
    settings: null,
    tab: "discover",
    devices: [],
    summary: null,
    scans: [],
    agents: [],
    relayEnabled: false,
    source: "server",
    activeScanId: null,
    detail: null,
    selectedId: null,
    filters: {
      state: "",
      type: "",
      segment: "",
      vendor: "",
      no_agent: false,
      admin_ports: false,
      exposure: false,
      conflict: false,
    },
    methods: [],
    conflicts: [],
    conflictIds: new Set(),
    integrations: [],
    importPreview: null,
    promotion: null,
    promoteTrigger: null,
    loading: false,
    pollTimer: null,
    trigger: null,
  },
};

const $ = (id) => document.getElementById(id);
const FLEET_PAGE_SIZE = 60;
const SERVICE_PAGE_SIZE = 50;
const SERVICE_STATUS_FILTERS = ["all", "running", "stopped", "failed", "watched"];
const HISTORY_PAGE_SIZES = [25, 50, 100];
const HISTORY_STATUS_FILTERS = ["all", "queued", "dispatched", "succeeded", "failed", "cancelled"];
const HISTORY_SEARCH_DELAY = 300;
const FLEET_STATUS_VALUES = ["all", "online", "offline", "alerts"];
const ADMIN_PAGES = ["enroll", "audit", "users", "profiles", "remote-install", "updates"];
const THEME_KEY = "sextant.theme";
const PAGE_META = {
  overview: ["Fleet", "Live machines and health at a glance."],
  discovery: ["Discovery", "Find devices on authorized networks and review their evidence."],
  wake: ["Wake targets", "Send Wake-on-LAN packets to machines without agents."],
  "docker-hosts": ["Docker Hosts", "Select a host to inspect its cached Docker inventory."],
  "compose-files": ["Compose Files", "Compose stack files, immutable revisions, and deployments."],
  enroll: ["Enrollment", "Issue one-time tokens and onboard agents."],
  audit: ["Audit log", "Who did what, from where, and when."],
  users: ["Users", "Local accounts and role-based access."],
  profiles: ["Profiles", "Global agent behavior, cadence, modules, and tag assignments."],
  "remote-install": ["Remote install", "Install a Linux agent over key-based SSH."],
  updates: ["Updates", "Server release checks, self-update, and rollback."],
  security: ["Security", "Session, identity provider, and account settings."],
  settings: ["Settings", "Your profile, server, directory, and discovery SSH settings."],
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
  if (seconds < 5) return "just now";
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
  if (themeColor) themeColor.setAttribute("content", value === "light" ? "#f3f3f3" : "#0b1118");
  const toggle = $("theme-toggle");
  if (toggle) {
    const light = value === "light";
    toggle.setAttribute("aria-pressed", light ? "true" : "false");
    toggle.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to classic light theme");
    toggle.title = light ? "Switch to dark theme" : "Switch to classic light theme";
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
  stopDockerAutoRefresh();
  closeDockerConsoleSource();
  state.dockerConsole = null;
  state.user = null;
  state.dockerHostId = null;
  state.dockerHostDetail = null;
  state.composeStackId = null;
  state.composeDetail = null;
  state.composeDraftFiles = {};
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

function updateFeedLabel(data) {
  if (!data?.enabled) return "Not configured";
  if (data.feed_state === "error") return "Feed error";
  if (data.feed_kind === "github") return "GitHub Releases";
  if (data.feed_kind === "static") return "Static JSON feed";
  return data.feed || "Configured";
}

function updateHashLabel(data) {
  const asset = data?.selected_asset;
  if (!asset) return "No server wheel asset";
  if (asset.sha256) return `SHA-256 ${asset.sha256.slice(0, 12)}… (${asset.name || "wheel"})`;
  return "SHA-256 unavailable";
}

function updateAssetSize(data) {
  const size = Number(data?.selected_asset?.size || 0);
  if (!size) return "—";
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
}

function syncUpdateNavButton() {
  const button = $("update-nav-button");
  if (!button) return;
  const show = Boolean(state.permissions?.admin && state.updates?.update_available);
  button.hidden = !show;
  if (show) {
    const version = state.updates?.latest_version || "";
    button.title = version ? `Sextant ${version} is available` : "Update available";
    button.setAttribute("aria-label", button.title);
  }
}

function renderUpdateBanner() {
  const banner = $("update-banner");
  syncUpdateNavButton();
  if (!banner) return;
  const data = state.updates;
  if (!state.permissions?.admin || !data?.update_available) {
    banner.hidden = true;
    return;
  }
  const hashNote = data.hash_available ? "" : " SHA-256 is unavailable, so automatic apply is disabled.";
  setText(
    "update-banner-text",
    `Sextant ${data.latest_version} is available (you are on ${data.current_version}).${hashNote}`,
  );
  const link = $("update-banner-link");
  if (link) {
    if (data.url) {
      link.href = data.url;
      link.hidden = false;
    } else {
      link.hidden = true;
    }
  }
  const check = $("update-banner-check");
  if (check) check.disabled = state.updatesBusy;
  const apply = $("update-banner-apply");
  if (apply) {
    apply.hidden = !data.apply_available;
    apply.disabled = state.updatesBusy || !data.apply_available;
    apply.title = data.apply_available ? "" : data.apply_disabled_reason || "Update cannot be applied.";
  }
  banner.hidden = false;
}

function renderUpdatesPanel() {
  const panel = $("updates-panel");
  if (!panel) return;
  if (!state.permissions?.admin) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const data = state.updates;
  const details = $("updates-details");
  if (!details) return;
  clear(details);
  if (!data) {
    setText("updates-state-label", "Loading…");
    details.append(h("dt", {}, "Status"), h("dd", {}, "Reading update status…"));
    return;
  }
  const checked = data.last_checked || data.checked_at;
  const entries = [
    ["Current version", data.current_version || state.settings?.version || "—"],
    ["Check interval", `${Number(data.check_interval_hours || 24)} hours`],
    ["Last checked", checked ? `${localTime(checked)} (${relativeTime(checked)})` : "never"],
    ["Feed", updateFeedLabel(data)],
    ["Latest version", data.latest_version || "—"],
    ["Artifact", data.selected_asset?.name ? `${data.selected_asset.name} · ${updateAssetSize(data)}` : "—"],
    ["SHA-256", updateHashLabel(data)],
    ["Previous release", data.rollback_available ? `Rollback to ${data.previous_version || "previous release"}` : "None recorded"],
    ["Restart", data.restart_pending ? "Requested; waiting for systemd" : "Not pending"],
  ];
  for (const [key, value] of entries) {
    details.append(h("dt", {}, key), h("dd", {}, value));
  }
  const stateLabel = data.restart_pending
    ? "Restart pending"
    : data.feed_state === "error"
      ? "Feed error"
      : data.update_available
        ? "Update available"
        : data.enabled
          ? "Up to date"
          : "Updates not configured";
  setText("updates-state-label", stateLabel);
  const notes = $("updates-notes");
  if (notes) {
    if (data.url) {
      notes.href = data.url;
      notes.hidden = false;
    } else {
      notes.hidden = true;
    }
  }
  const check = $("updates-check-now");
  if (check) check.disabled = state.updatesBusy;
  const apply = $("updates-apply");
  if (apply) {
    apply.hidden = !data.apply_available;
    apply.disabled = state.updatesBusy || !data.apply_available;
    apply.title = data.apply_available ? "" : data.apply_disabled_reason || "Update cannot be applied.";
  }
  const rollback = $("updates-rollback");
  if (rollback) {
    rollback.hidden = !data.rollback_available;
    rollback.disabled = state.updatesBusy;
  }
  const error = $("updates-error");
  if (error) {
    error.textContent = data.error
      ? data.error
      : data.update_available && !data.apply_available
        ? data.apply_disabled_reason || ""
        : "";
  }
  const hint = $("updates-hint");
  if (hint) {
    hint.textContent =
      state.updatesNotice ||
      (data.update_available && !data.apply_available
        ? data.apply_disabled_reason
        : "Updates are staged and health-checked before the stable current symlink is switched. The server restarts through a root-owned systemd handoff.");
  }
}

function applyUpdateState(data) {
  state.updates = data;
  renderUpdateBanner();
  renderUpdatesPanel();
}

async function checkForUpdates(force = false) {
  const banner = $("update-banner");
  if (!state.permissions?.admin) {
    if (banner) banner.hidden = true;
    renderUpdatesPanel();
    return;
  }
  state.updatesBusy = true;
  if (force) state.updatesNotice = "";
  renderUpdateBanner();
  renderUpdatesPanel();
  try {
    const data = await api(
      force ? "/api/update/check" : "/api/update/status",
      force ? { method: "POST" } : {},
    );
    applyUpdateState(data);
    if (force) toast("Update check complete.");
  } catch (error) {
    if (force) {
      setText("updates-error", error.message);
      toast(error.message, "error");
    } else if (banner) {
      banner.hidden = true;
    }
  } finally {
    state.updatesBusy = false;
    renderUpdateBanner();
    renderUpdatesPanel();
  }
}

async function waitForServerRestart(expectedVersion) {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    await sleep(2500);
    try {
      const data = await api("/api/update/status");
      applyUpdateState(data);
      if (!data.restart_pending && (!expectedVersion || data.current_version === expectedVersion)) {
        state.updatesNotice = `Sextant ${data.current_version} is running. Reloading…`;
        renderUpdateBanner();
        renderUpdatesPanel();
        setTimeout(() => window.location.reload(), 900);
        return;
      }
      state.updatesNotice = "Waiting for the service to restart…";
    } catch (_error) {
      state.updatesNotice = "Waiting for the service to restart…";
    }
    renderUpdateBanner();
    renderUpdatesPanel();
  }
  state.updatesNotice = "Restart is taking longer than expected. Reload the page to check.";
  renderUpdateBanner();
  renderUpdatesPanel();
}

async function applyServerUpdate() {
  const data = state.updates;
  if (!data?.apply_available || state.updatesBusy) return;
  const version = data.latest_version;
  const confirmed = await confirmAction(
    "Install server update",
    `Stage Sextant ${version}, verify its SHA-256, health-check it, switch /opt/sextant/current, and restart the service? Open sessions may disconnect briefly.`,
    "Update now",
  );
  if (!confirmed) return;
  state.updatesBusy = true;
  state.updatesNotice = `Staging Sextant ${version}…`;
  renderUpdateBanner();
  renderUpdatesPanel();
  try {
    const result = await api("/api/update/apply", {
      method: "POST",
      body: { version },
    });
    if (result.update) state.updates = result.update;
    state.updatesNotice =
      result.instructions ||
      (result.restarting
        ? "Update staged. The service is restarting now."
        : "Update staged. Restart the sextant service to start it.");
    toast(result.restarting ? "Update staged. Restarting…" : "Update staged. Restart required.", result.restarting ? "ok" : "warn");
    if (result.restarting) void waitForServerRestart(version);
  } catch (error) {
    state.updatesNotice = "";
    setText("updates-error", error.message);
    toast(error.message, "error");
  } finally {
    state.updatesBusy = false;
    renderUpdateBanner();
    renderUpdatesPanel();
  }
}

async function rollbackServerUpdate() {
  const data = state.updates;
  if (!data?.rollback_available || state.updatesBusy) return;
  const target = data.previous_version || "the previous release";
  const confirmed = await confirmAction(
    "Roll back server update",
    `Repoint /opt/sextant/current to ${target} and restart the service?`,
    "Rollback",
  );
  if (!confirmed) return;
  state.updatesBusy = true;
  state.updatesNotice = `Rolling back to ${target}…`;
  renderUpdateBanner();
  renderUpdatesPanel();
  try {
    const result = await api("/api/update/rollback", { method: "POST" });
    if (result.update) state.updates = result.update;
    state.updatesNotice =
      result.instructions ||
      (result.restarting
        ? "Rollback staged. The service is restarting now."
        : "Rollback staged. Restart the sextant service to start it.");
    toast(result.restarting ? "Rollback staged. Restarting…" : "Rollback staged. Restart required.", result.restarting ? "ok" : "warn");
    if (result.restarting) void waitForServerRestart(data.previous_version || "");
  } catch (error) {
    state.updatesNotice = "";
    setText("updates-error", error.message);
    toast(error.message, "error");
  } finally {
    state.updatesBusy = false;
    renderUpdateBanner();
    renderUpdatesPanel();
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
  if (page !== "docker-hosts") {
    stopDockerRefreshPoll();
    stopDockerAutoRefresh();
  }
  if (page !== "discovery") {
    stopDiscoveryPoll();
    closeDiscoveryDrawer();
  }
  if (!load) return;
  if (page === "overview") loadDashboard();
  if (page === "discovery") loadDiscovery();
  if (page === "wake") loadWakeTargets();
  if (page === "docker-hosts") {
    loadDockerHosts();
    if (state.dockerHostId) {
      loadDockerHostDetail(state.dockerHostId, true);
      startDockerAutoRefresh();
    }
  }
  if (page === "compose-files") loadComposeFiles();
  if (page === "enroll") loadEnrollmentTokens();
  if (page === "audit") loadAudit();
  if (page === "users") loadUsers();
  if (page === "profiles") loadProfiles();
  if (page === "remote-install") loadRemoteInstall();
  if (page === "updates") {
    renderUpdatesPanel();
    if (!state.updates) void checkForUpdates();
  }
  if (page === "security") renderSecurity();
  if (page === "settings") loadSettingsPage();
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
  if (!state.detailId) {
    renderAgentGrid();
    return;
  }
  const agent = data.agents.find((item) => item.id === state.detailId);
  if (!agent) {
    closeDrawer();
    return;
  }
  state.detailAgent = agent;
  updateDrawerChrome();
  if (!SELF_RENDERING_TABS.has(state.detailTab)) renderTabPreservingScroll();
}

const SELF_RENDERING_TABS = new Set(["remote", "history", "trends", "packages"]);

function renderTabPreservingScroll() {
  const body = $("drawer-body");
  if (!body) {
    renderTab();
    return;
  }
  const top = body.scrollTop;
  renderTab();
  body.scrollTop = top;
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
        h("strong", { title: localTime(agent.last_seen) }, relativeTime(agent.last_seen), stale.heartbeatStale ? h("span", { class: "tag tag-warn" }, "stale") : null),
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
  if (state.dashboard) renderAgentGrid();
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
    if (agent.online && ["windows", "linux"].includes(String(agent.os || "").toLowerCase())) {
      actions.append(button("Join domain", "button-secondary", () => openDomainJoinDialog(agent)));
    }
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
        infoTile("Last heartbeat", `${relativeTime(agent.last_seen)}${stale.heartbeatStale ? " · stale" : ""}`),
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
      status.textContent = `Heartbeat: ${relativeTime(agent.last_seen)} · Inventory updated: ${lastInventory ? relativeTime(lastInventory) : "never"}${managers.length ? ` · Managers: ${managers.join(", ")}` : ""}`;
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
        h("p", {}, `Retained commands. Heartbeat: ${relativeTime(agent.last_seen)}.`),
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
  form.elements.allow_discovery.checked = agent.policy?.allow_discovery === true;
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
  "discovery",
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
  updateRemoteInstallMode();
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
          h("p", { class: "form-hint" }, `${job.os || "linux"} · ${job.transport || "ssh"} · ${job.status} · ${job.profile} · ${localTime(job.created_at)}${job.error ? ` · ${job.error}` : ""}`),
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

function updateRemoteInstallMode() {
  const form = $("remote-install-form");
  if (!form) return;
  const windows = form.elements.os.value === "windows";
  const port = $("remote-install-port");
  const passwordWrap = $("remote-install-password-wrap");
  const hint = $("remote-install-hint");
  const windowsNote = $("remote-install-windows-note");
  if (passwordWrap) passwordWrap.hidden = !windows;
  if (windowsNote) windowsNote.hidden = !windows;
  if (hint) {
    hint.textContent = windows
      ? "Windows uses WinRM over HTTPS (5986) with administrator credentials and certificate validation. The password is used once for this job and is never stored."
      : "Linux uses key-based SSH for this user and requires passwordless sudo. Host-key verification is disabled for this installer; use hosts you control.";
  }
  if (port) {
    const value = Number(port.value || 0);
    port.value = windows ? (value === 22 || !value ? "5986" : String(value)) : (value === 5986 || !value ? "22" : String(value));
  }
}

async function testRemoteInstall() {
  const form = $("remote-install-form");
  const log = $("remote-install-log");
  setText("remote-install-error", "");
  const osName = form.elements.os.value;
  const body = {
    host: form.elements.host.value.trim(),
    port: Number(form.elements.port.value || (osName === "windows" ? 5986 : 22)),
    user: form.elements.user.value.trim(),
    name: form.elements.name.value.trim(),
    profile: form.elements.profile.value,
    watch_services: [],
    os: osName,
    transport: osName === "windows" ? "winrm" : "ssh",
    password: osName === "windows" ? form.elements.password.value : "",
  };
  if (log) log.textContent = osName === "windows" ? "Testing WinRM, admin rights, and Python…" : "Testing SSH and prerequisites…";
  try {
    const result = await api("/api/remote-install/test", { method: "POST", body });
    if (log) log.textContent = result.output || "Connection OK.";
  } catch (error) {
    setText("remote-install-error", error.message);
    if (log) log.textContent = error.message;
  } finally {
    if (osName === "windows") form.elements.password.value = "";
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
  const osName = form.elements.os.value;
  const body = {
    host: form.elements.host.value.trim(),
    port: Number(form.elements.port.value || (osName === "windows" ? 5986 : 22)),
    user: form.elements.user.value.trim(),
    name: form.elements.name.value.trim(),
    profile: form.elements.profile.value,
    watch_services: watch,
    os: osName,
    transport: osName === "windows" ? "winrm" : "ssh",
    password: osName === "windows" ? form.elements.password.value : "",
  };
  try {
    const job = await api("/api/remote-install", { method: "POST", body });
    if (osName === "windows") form.elements.password.value = "";
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

function dockerHost() {
  return state.dockerHostDetail?.host || null;
}

function dockerCan(gate = "mutate") {
  const host = dockerHost();
  if (!state.permissions.operate || !host?.available || !host?.online || state.dockerActionBusy) {
    return false;
  }
  return host.capabilities ? host.capabilities[gate] !== false : true;
}

function closeDockerDialog() {
  const dialog = $("docker-dialog");
  if (dialog?.open) dialog.close();
}

function openDockerDialog(title, eyebrow, bodyBuilder, actionsBuilder) {
  const dialog = $("docker-dialog");
  if (!dialog) return;
  if (dialog.open) dialog.close();
  state.dockerDialogTrigger = document.activeElement;
  setText("docker-dialog-title", title);
  setText("docker-dialog-eyebrow", eyebrow || "Docker");
  setText("docker-dialog-error", "");
  const body = $("docker-dialog-body");
  clear(body);
  if (typeof bodyBuilder === "function") bodyBuilder(body);
  const actions = $("docker-dialog-actions");
  clear(actions);
  actions.append(button("Close", "button-ghost", closeDockerDialog));
  if (typeof actionsBuilder === "function") actionsBuilder(actions);
  dialog.showModal();
  const first = body.querySelector("input, select, textarea, button");
  first?.focus();
}

function dockerCommandText(command) {
  const parts = [];
  if (command.stdout) parts.push(command.stdout);
  if (command.stderr) parts.push(command.stderr);
  if (command.error) parts.push(command.error);
  return parts.join("\n").trim() || "(no output)";
}

function parseDockerReclaimed(text) {
  const match = String(text || "").match(/Total reclaimed space:\s*([0-9.]+)\s*([KMGTPE]?i?B)/i);
  if (!match) return 0;
  const amount = Number(match[1]);
  const unit = match[2].toUpperCase();
  const factor = unit.startsWith("K")
    ? 1024
    : unit.startsWith("M")
      ? 1024 ** 2
      : unit.startsWith("G")
        ? 1024 ** 3
        : unit.startsWith("T")
          ? 1024 ** 4
          : 1;
  return Math.round(amount * factor);
}

function showDockerOutput(title, command) {
  const reclaimed = parseDockerReclaimed(dockerCommandText(command));
  openDockerDialog(title, "Docker result", (body) => {
    const status = command.status === "succeeded" ? "Succeeded" : command.status === "cancelled" ? "Cancelled" : "Failed";
    body.append(
      h("p", { class: "form-hint" }, `${status}${command.exit_code != null ? ` · exit ${command.exit_code}` : ""}${reclaimed ? ` · reclaimed ${fmtBytes(reclaimed)}` : ""}`),
      h("pre", { class: "terminal docker-dialog-output" }, dockerCommandText(command)),
    );
  });
}

async function waitForDockerCommand(commandId) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await sleep(1200);
    const data = await api(`/api/commands/${encodeURIComponent(commandId)}`);
    const command = data?.command;
    if (!command) continue;
    if (commandIsTerminal(command)) return command;
    const status = $("docker-hosts-status");
    if (status) status.textContent = command.status === "queued" ? "Docker action queued; waiting for the agent heartbeat…" : "Docker action running…";
  }
  throw new Error("Timed out waiting for the Docker action result. Check the command history.");
}

async function queueDockerAction(path, payload, label, options = {}) {
  if (state.dockerActionBusy) {
    toast("Wait for the current Docker action to finish.", "warn");
    return null;
  }
  state.dockerActionBusy = true;
  const status = $("docker-hosts-status");
  if (status) status.textContent = `${label}: queuing…`;
  try {
    const created = await api(path, { method: "POST", body: payload });
    if (status) status.textContent = `${label}: queued. Waiting for the agent heartbeat…`;
    const command = await waitForDockerCommand(created.id);
    if (status) status.textContent = `${label}: ${command.status}.`;
    if (options.showOutput !== false) {
      showDockerOutput(label, command);
    } else if (command.status === "succeeded") {
      toast(`${label} completed.`);
    } else {
      toast(`${label} failed: ${command.error || command.stderr || "see command history"}`, "error");
    }
    if (state.dockerHostId) await loadDockerHostDetail(state.dockerHostId, true);
    await loadDockerHosts(true);
    return command;
  } catch (error) {
    setText("docker-dialog-error", error.message);
    if (status) status.textContent = `${label}: ${error.message}`;
    toast(error.message, "error");
    return null;
  } finally {
    state.dockerActionBusy = false;
    if (state.dockerHostDetail) renderDockerTab();
  }
}

async function confirmDockerAction(title, message, confirmLabel, path, payload, label, options = {}) {
  const confirmed = await confirmAction(title, message, confirmLabel);
  if (!confirmed) return null;
  return queueDockerAction(path, payload, label, options);
}

function stopDockerAutoRefresh() {
  if (state.dockerAutoTimer) {
    clearInterval(state.dockerAutoTimer);
    state.dockerAutoTimer = null;
  }
}

function startDockerAutoRefresh() {
  stopDockerAutoRefresh();
  if (state.page !== "docker-hosts" || !state.dockerHostId) return;
  const delay = window.__fastDockerRefresh ? 300 : 12_000;
  state.dockerAutoTimer = setInterval(() => {
    if (document.hidden || state.dockerActionBusy) return;
    if ($("docker-console-dialog")?.open || $("docker-dialog")?.open) return;
    loadDockerHosts(true);
    loadDockerHostDetail(state.dockerHostId, true);
  }, delay);
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
          `${host.counts.running} running · ${host.counts.stopped} stopped · ${host.counts.unhealthy} unhealthy · ${host.counts.images} images · ${host.counts.volumes} volumes · ${host.counts.networks} networks · inventory ${dockerAge(host.age_seconds)} · rev ${host.inventory_revision}`,
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
  startDockerAutoRefresh();
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
  stopDockerAutoRefresh();
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
    else if (!host.online) messages.push("The agent is offline. Showing cached inventory; Docker actions are unavailable.");
    else if (host.stale) messages.push(`Cached inventory is older than ${Math.round((detail.stale_seconds || 900) / 60)} minutes. Use Refresh now.`);
    if (host.reason === "compose_missing") messages.push("Compose v2 was not detected on this host.");
    if (host.capabilities && !host.capabilities.mutate) messages.push("Docker actions are disabled by the agent profile or server policy.");
    else {
      if (host.capabilities && !host.capabilities.exec) messages.push("Docker exec is disabled by the agent profile or server policy.");
      if (host.capabilities && !host.capabilities.prune) messages.push("Docker cleanup is disabled by the agent profile or server policy.");
    }
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
  if (state.dockerTab === "stacks") return renderDockerHostStacks(body, detail);
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
  const cleanupHost = h("div", {});
  body.append(
    section(
      "Docker host",
      "Cleanup",
      h("p", { class: "form-hint" }, "Estimates come from the cached inventory. Review the exact filter before running a prune."),
      cleanupHost,
    ),
  );
  loadDockerCleanupPanel(cleanupHost);
}

async function loadDockerCleanupPanel(container) {
  clear(container);
  container.append(h("div", { class: "empty-inline" }, "Loading cleanup estimate…"));
  try {
    const data = await api(`/api/docker/hosts/${encodeURIComponent(state.dockerHostId)}/cleanup`);
    state.dockerCleanup = data;
    clear(container);
    const grid = h("div", { class: "docker-cleanup-grid" });
    for (const scope of data.scopes || []) {
      const meta = DOCKER_CLEANUP_META[scope.scope] || { label: scope.label, warning: "" };
      const size = scope.estimated && scope.estimated_bytes > 0
        ? fmtBytes(scope.estimated_bytes)
        : scope.estimated
          ? "size unknown"
          : "reported after prune";
      const disabled = !data.can_prune || (scope.scope !== "build_cache" && scope.count === 0);
      grid.append(
        h(
          "article",
          { class: "docker-cleanup-card" },
          h("h4", {}, meta.label),
          h("strong", {}, `${scope.count} item${scope.count === 1 ? "" : "s"}`),
          h("p", {}, size),
          h("p", {}, meta.warning || scope.warning || ""),
          button("Review", "button-secondary button-small", () => confirmDockerCleanup(scope.scope), disabled),
        ),
      );
    }
    container.append(grid);
    if (!data.can_prune) {
      container.append(h("p", { class: "form-hint" }, "Cleanup is disabled by the agent profile or server policy."));
    }
  } catch (error) {
    clear(container);
    container.append(h("div", { class: "empty-inline" }, error.message));
  }
}

function dockerActionUrl() {
  return `/api/docker/hosts/${encodeURIComponent(state.dockerHostId)}/actions`;
}

function dockerContainerMatches(container, filter) {
  const stateName = String(container.state || "").toLowerCase();
  if (filter === "running") return stateName === "running";
  if (filter === "stopped") return !["running", "paused"].includes(stateName);
  if (filter === "unhealthy") return container.health === "unhealthy";
  if (filter === "unmanaged") return !container.compose_project;
  return true;
}

function dockerContainerLabel(container) {
  return container.name || short(container.id, 12);
}

function renderDockerContainers(body, detail) {
  const containers = detail.containers || [];
  const filter = h(
    "select",
    { "aria-label": "Container status filter" },
    ...["all", "running", "stopped", "unhealthy", "unmanaged"].map((value) =>
      h("option", { value }, value === "all" ? "All containers" : value[0].toUpperCase() + value.slice(1)),
    ),
  );
  filter.value = state.dockerContainerFilter;
  filter.addEventListener("change", () => {
    state.dockerContainerFilter = filter.value;
    renderDockerTab();
  });
  const filtered = containers.filter((container) => dockerContainerMatches(container, state.dockerContainerFilter));
  const canMutate = dockerCan("mutate");
  const canExec = dockerCan("exec");
  const driftProjects = new Set(
    (detail.deployments || []).filter((deployment) => deployment.drift).map((deployment) => deployment.project_name),
  );
  const rows = filtered.map((container) => {
    const running = String(container.state || "").toLowerCase() === "running";
    const paused = String(container.state || "").toLowerCase() === "paused";
    const label = dockerContainerLabel(container);
    return h(
      "tr",
      {},
      h("td", {}, h("strong", {}, label)),
      h("td", { class: "mono" }, container.image || "—"),
      h("td", {}, container.state || "—"),
      h("td", {}, container.health ? statusPill(container.health, container.health === "unhealthy" ? "critical" : "online") : "—"),
      h("td", {}, (container.ports || []).join(", ") || "—"),
      h("td", {}, container.compose_project || "—", driftProjects.has(container.compose_project) ? h("span", { title: "Deployed revision or container config hash differs from the stack baseline." }, statusPill("drift", "warning")) : null),
      h("td", {}, container.status || "—"),
      h(
        "td",
        {},
        h(
          "div",
          { class: "docker-row-actions" },
          button("Inspect", "button-ghost button-small", () => openDockerContainerDetail(container), false),
          button("Logs", "button-ghost button-small", () => openDockerLogs(container), !canMutate),
          button("Exec", "button-secondary button-small", () => openDockerExec(container), !canExec),
          button("Console", "button-primary button-small", () => openDockerConsole(container), !canExec || !running),
          running
            ? button("Stop", "button-ghost button-small", () => dockerLifecycle(container, "container.stop"), !canMutate)
            : button("Start", "button-secondary button-small", () => dockerLifecycle(container, "container.start"), !canMutate),
          button("Restart", "button-ghost button-small", () => dockerLifecycle(container, "container.restart"), !canMutate),
          paused
            ? button("Unpause", "button-ghost button-small", () => dockerLifecycle(container, "container.unpause"), !canMutate)
            : button("Pause", "button-ghost button-small", () => dockerLifecycle(container, "container.pause"), !canMutate || !running),
          button("Rename", "button-ghost button-small", () => openDockerRename(container), !canMutate),
          button("Update", "button-ghost button-small", () => openDockerUpdate(container), !canMutate),
          button("Clone", "button-ghost button-small", () => openDockerClone(container), !canMutate),
          button("Remove", "button-danger button-small", () => removeDockerContainer(container), !canMutate),
        ),
      ),
    );
  });
  const children = [
    h("div", { class: "docker-filter-row" }, h("label", { class: "field" }, h("span", {}, "Status"), filter)),
    h("p", { class: "form-hint" }, `${filtered.length} of ${containers.length} containers shown. Actions are audited and queue on the agent heartbeat.`),
  ];
  if (!filtered.length) {
    children.push(h("div", { class: "empty-inline" }, containers.length ? "No containers match this filter." : "No containers reported. Refresh inventory if the host recently started a container."));
  } else {
    children.push(
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Image"), h("th", {}, "State"), h("th", {}, "Health"), h("th", {}, "Ports"), h("th", {}, "Compose project"), h("th", {}, "Uptime / status"), h("th", {}, "Actions"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter containers", pageSize: 25 },
      ),
    );
  }
  body.append(section("Docker host", "Containers", ...children));
}

function dockerLifecycle(container, action) {
  return queueDockerAction(
    dockerActionUrl(),
    { action, container_id: container.id },
    `${action.split(".")[1]} ${dockerContainerLabel(container)}`,
    { showOutput: true },
  );
}

function openDockerContainerDetail(container) {
  const host = dockerHost();
  openDockerDialog(
    dockerContainerLabel(container),
    "Container overview",
    (body) => {
      body.append(
        h(
          "div",
          { class: "info-grid" },
          infoTile("Image", container.image),
          infoTile("State", container.state),
          infoTile("Health", container.health || "—"),
          infoTile("Exit code", container.exit_code ?? "—"),
          infoTile("Restart policy", container.restart_policy || "—"),
          infoTile("Memory limit", container.memory_limit ? fmtBytes(container.memory_limit) : "unlimited"),
          infoTile("CPU limit", container.nano_cpus ? (container.nano_cpus / 1e9).toFixed(2) : "unlimited"),
          infoTile("PIDs limit", container.pids_limit ?? "unlimited"),
          infoTile("Compose project", container.compose_project || "—"),
          infoTile("Compose service", container.compose_service || "—"),
          infoTile("Container ID", short(container.id, 20)),
        ),
        h("p", { class: "form-hint" }, `Command: ${short(container.command || "—", 180)}`),
        h("p", { class: "form-hint" }, `Ports: ${(container.ports || []).join(", ") || "—"}`),
        h("h3", {}, "Mounted networks"),
        h("ul", { class: "docker-detail-list" }, ...(container.networks || ["—"]).map((item) => h("li", {}, item))),
        h("h3", {}, "Mounts"),
        h("ul", { class: "docker-detail-list" }, ...(container.mounts || ["—"]).map((item) => h("li", {}, item))),
        h("h3", {}, "Labels"),
        h("ul", { class: "docker-detail-list" }, ...(Object.entries(container.labels || {}).length
          ? Object.entries(container.labels).map(([key, value]) => h("li", {}, `${key}=${value}`))
          : [h("li", {}, "—")])),
      );
      if (host?.capabilities?.mutate && state.permissions.operate) {
        body.append(
          button("Load raw inspect (env masked)", "button-secondary button-small", async () => {
            await queueDockerAction(
              dockerActionUrl(),
              { action: "container.inspect", container_id: container.id },
              `Inspect ${dockerContainerLabel(container)}`,
              { showOutput: true },
            );
          }, state.dockerActionBusy),
        );
      }
    },
  );
}

function openDockerLogs(container) {
  openDockerDialog(`Logs · ${dockerContainerLabel(container)}`, "Container logs", (body) => {
    const tail = h("select", {}, h("option", { value: "100" }, "100 lines"), h("option", { value: "500", selected: true }, "500 lines"), h("option", { value: "1000" }, "1,000 lines"));
    const timestamps = h("input", { type: "checkbox" });
    const form = h(
      "form",
      { class: "form-stack" },
      h("label", { class: "field" }, h("span", {}, "Tail"), tail),
      h("label", { class: "checkbox-row" }, timestamps, h("span", {}, "Include timestamps")),
      h("p", { class: "form-hint" }, "Logs are fetched from the selected container and capped by the agent output limit."),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      queueDockerAction(
        dockerActionUrl(),
        { action: "container.logs", container_id: container.id, tail: Number(tail.value), timestamps: timestamps.checked },
        `Logs ${dockerContainerLabel(container)}`,
        { showOutput: true },
      );
    });
    body.append(form);
  }, (actions) => {
    actions.append(button("Load logs", "button-primary", () => {
      const form = $("docker-dialog-body").querySelector("form");
      form?.requestSubmit();
    }, !dockerCan("mutate")));
  });
}

function openDockerExec(container) {
  openDockerDialog(`Exec · ${dockerContainerLabel(container)}`, "One-shot container exec", (body) => {
    const shell = h("select", {}, h("option", { value: "sh" }, "sh"), h("option", { value: "bash" }, "bash"), h("option", { value: "ash" }, "ash"));
    const user = h("input", { type: "text", maxlength: "32", placeholder: "container user (optional)" });
    const workdir = h("input", { type: "text", maxlength: "256", placeholder: "/app (optional)" });
    const command = h("textarea", { rows: "4", maxlength: "8000", placeholder: "id && uname -a", required: true });
    const timeout = h("input", { type: "number", min: "5", max: "300", value: "60" });
    const form = h(
      "form",
      { class: "form-stack" },
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "Shell"), shell), h("label", { class: "field" }, h("span", {}, "Timeout seconds"), timeout)),
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "User"), user), h("label", { class: "field" }, h("span", {}, "Working directory"), workdir)),
      h("label", { class: "field" }, h("span", {}, "Command"), command),
      h("p", { class: "form-hint" }, "Runs inside the container with the container's privileges. Output is retained in command history."),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = command.value.trim();
      if (!text) return setText("docker-dialog-error", "Enter a command.");
      if (workdir.value.trim() && !workdir.value.trim().startsWith("/")) {
        return setText("docker-dialog-error", "Working directory must be an absolute path.");
      }
      queueDockerAction(
        `/api/docker/hosts/${encodeURIComponent(state.dockerHostId)}/containers/${encodeURIComponent(container.id)}/exec`,
        { shell: shell.value, user: user.value.trim(), workdir: workdir.value.trim(), command: text, timeout: Number(timeout.value) },
        `Exec ${dockerContainerLabel(container)}`,
        { showOutput: true },
      );
    });
    body.append(form);
  }, (actions) => {
    actions.append(button("Run command", "button-primary", () => {
      $("docker-dialog-body").querySelector("form")?.requestSubmit();
    }, !dockerCan("exec")));
  });
}

function bytesToBase64(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(String(value || ""));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function decodeConsoleBytes(bytes) {
  if (typeof TextDecoder === "function") return new TextDecoder().decode(bytes);
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return text;
}

function encodeConsoleText(text) {
  if (typeof TextEncoder === "function") return new TextEncoder().encode(text);
  return Uint8Array.from([...String(text)].map((char) => char.charCodeAt(0) & 0xff));
}

function dockerConsoleWrite(text) {
  let output = state.dockerConsoleBuffer;
  const cleaned = String(text || "")
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\x1b[@-_]/g, "");
  for (const char of cleaned) {
    if (char === "\r") {
      const lastNewline = output.lastIndexOf("\n");
      output = lastNewline >= 0 ? output.slice(0, lastNewline + 1) : "";
    } else if (char === "\b") {
      if (!output.endsWith("\n")) output = output.slice(0, -1);
    } else if (char === "\n" || char === "\t") {
      output += char;
    } else if (char >= " ") {
      output += char;
    }
  }
  state.dockerConsoleBuffer = output.slice(-200_000);
  const pre = $("docker-console-output");
  if (pre) {
    pre.textContent = state.dockerConsoleBuffer;
    pre.scrollTop = pre.scrollHeight;
  }
}

function updateDockerConsoleIdle() {
  const status = $("docker-console-status");
  if (!status || !state.dockerConsole?.session) return;
  const elapsed = (Date.now() - state.dockerConsoleLastActivity) / 1000;
  const remaining = Math.max(0, Math.round(state.dockerConsoleIdleSeconds - elapsed));
  const base = state.dockerConsole.status || "open";
  status.textContent = `${base} · idle timeout in ${Math.floor(remaining / 60)}m ${String(remaining % 60).padStart(2, "0")}s`;
}

function resetDockerConsoleIdle() {
  state.dockerConsoleLastActivity = Date.now();
  updateDockerConsoleIdle();
}

function dockerConsoleSize() {
  const pre = $("docker-console-output");
  const rows = Math.max(10, Math.min(200, Math.floor((pre?.clientHeight || 400) / 18)));
  const cols = Math.max(20, Math.min(300, Math.floor((pre?.clientWidth || 800) / 7)));
  return { rows, cols };
}

function sendDockerConsoleResize() {
  const session = state.dockerConsole?.session;
  if (!session || !dockerCan("exec")) return;
  const { rows, cols } = dockerConsoleSize();
  api(`/api/docker/console/${encodeURIComponent(session.session_id)}/resize`, {
    method: "POST",
    body: { rows, cols },
  }).catch(() => {});
}

function scheduleDockerConsoleResize() {
  if (!state.dockerConsole?.session) return;
  if (state.dockerConsoleResizeTimer) clearTimeout(state.dockerConsoleResizeTimer);
  state.dockerConsoleResizeTimer = setTimeout(sendDockerConsoleResize, 180);
}

function sendDockerConsoleInput(text) {
  const session = state.dockerConsole?.session;
  if (!session || !text) return;
  resetDockerConsoleIdle();
  const data = bytesToBase64(encodeConsoleText(text));
  api(`/api/docker/console/${encodeURIComponent(session.session_id)}/input`, {
    method: "POST",
    body: { data },
  }).catch((error) => {
    if (!/rate/i.test(error.message)) {
      const status = $("docker-console-status");
      if (status) status.textContent = error.message;
    }
  });
}

function openDockerConsole(container) {
  if (!dockerCan("exec")) return toast("Docker exec is disabled for this host.", "warn");
  if (String(container.state || "").toLowerCase() !== "running") {
    return toast("Start the container before opening a console.", "warn");
  }
  const host = dockerHost();
  state.dockerConsoleTrigger = document.activeElement;
  state.dockerConsole = { session: null, container, host, status: "opening" };
  state.dockerConsoleBuffer = "";
  setText("docker-console-title", container.name || short(container.id, 12));
  setText("docker-console-eyebrow", `Console · ${host?.name || ""}`);
  setText("docker-console-banner", `${host?.name || "Docker host"} · ${container.name || short(container.id, 12)} · ${container.image || ""}. The shell runs inside the container with its privileges.`);
  setText("docker-console-error", "");
  setText("docker-console-status", "");
  $("docker-console-setup").hidden = false;
  $("docker-console-live").hidden = true;
  $("docker-console-output").textContent = "";
  $("docker-console-user").value = "";
  $("docker-console-workdir").value = "/";
  $("docker-console-shell").value = "sh";
  $("docker-console-dialog").showModal();
  $("docker-console-shell").focus();
}

function updateDockerConsoleWarning() {
  const user = $("docker-console-user")?.value.trim().toLowerCase();
  const banner = $("docker-console-banner");
  if (!banner) return;
  const host = state.dockerConsole?.host;
  const container = state.dockerConsole?.container;
  const rootWarning = user === "root" || user === "0" ? " Requested user is root/UID 0 inside the container." : "";
  banner.textContent = `${host?.name || "Docker host"} · ${container?.name || "container"} · ${container?.image || ""}. The shell runs inside the container with its privileges.${rootWarning}`;
}

async function startDockerConsole() {
  const consoleState = state.dockerConsole;
  if (!consoleState?.container || consoleState.session) return;
  const shell = $("docker-console-shell").value;
  const user = $("docker-console-user").value.trim();
  const workdir = $("docker-console-workdir").value.trim();
  if (workdir && !workdir.startsWith("/")) {
    return setText("docker-console-error", "Working directory must be an absolute path.");
  }
  const { rows, cols } = dockerConsoleSize();
  $("docker-console-start").disabled = true;
  try {
    const data = await api(`/api/docker/hosts/${encodeURIComponent(state.dockerHostId)}/containers/${encodeURIComponent(consoleState.container.id)}/console`, {
      method: "POST",
      body: { shell, user, workdir, rows, cols },
    });
    consoleState.session = data;
    consoleState.status = data.status || "opening";
    state.dockerConsoleIdleSeconds = data.idle_seconds || 900;
    resetDockerConsoleIdle();
    $("docker-console-setup").hidden = true;
    $("docker-console-live").hidden = false;
    setText("docker-console-status", "opening · waiting for agent");
    connectDockerConsole(data.session_id);
    sendDockerConsoleResize();
    $("docker-console-output").focus();
  } catch (error) {
    setText("docker-console-error", error.message);
  } finally {
    $("docker-console-start").disabled = false;
  }
}

function connectDockerConsole(sessionId) {
  closeDockerConsoleSource();
  const source = new EventSource(`/api/docker/console/${encodeURIComponent(sessionId)}/events`);
  state.dockerConsoleSource = source;
  source.addEventListener("status", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (_error) {
      return;
    }
    if (!state.dockerConsole) return;
    state.dockerConsole.status = data.status;
    if (data.idle_seconds) state.dockerConsoleIdleSeconds = data.idle_seconds;
    const status = $("docker-console-status");
    if (status) {
      status.textContent = data.status === "open"
        ? "open"
        : data.status === "opening"
          ? "opening · waiting for agent"
          : `${data.status}${data.error ? ` · ${data.error}` : ""}${data.exit_code != null ? ` · exit ${data.exit_code}` : ""}`;
    }
    updateDockerConsoleIdle();
    if (["closed", "failed", "cancelled", "exited"].includes(data.status)) {
      closeDockerConsoleSource();
    }
  });
  source.addEventListener("output", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (_error) {
      return;
    }
    dockerConsoleWrite(decodeConsoleBytes(base64ToBytes(data.data)));
    resetDockerConsoleIdle();
  });
  source.addEventListener("auth", () => {
    const status = $("docker-console-status");
    if (status) status.textContent = "Console disconnected: operator session expired.";
    closeDockerConsoleSource();
  });
  source.addEventListener("error", () => {
    const status = $("docker-console-status");
    if (status && state.dockerConsole?.session) status.textContent = "Console connection interrupted. Waiting to reconnect…";
  });
  state.dockerConsoleIdleTimer = setInterval(updateDockerConsoleIdle, 1000);
}

function closeDockerConsoleSource() {
  if (state.dockerConsoleSource) {
    state.dockerConsoleSource.close();
    state.dockerConsoleSource = null;
  }
  if (state.dockerConsoleIdleTimer) {
    clearInterval(state.dockerConsoleIdleTimer);
    state.dockerConsoleIdleTimer = null;
  }
}

async function requestDockerConsoleClose() {
  const consoleState = state.dockerConsole;
  if (!consoleState?.session) {
    closeDockerConsoleImmediately();
    return;
  }
  const confirmed = await confirmAction(
    "Close the container console?",
    "The interactive shell process inside the container will be terminated.",
    "Close console",
  );
  if (!confirmed) return;
  try {
    await api(`/api/docker/console/${encodeURIComponent(consoleState.session.session_id)}`, { method: "DELETE" });
  } catch (_error) {
    // The session may already be closed or expired.
  }
  closeDockerConsoleImmediately();
}

function closeDockerConsoleImmediately() {
  closeDockerConsoleSource();
  state.dockerConsole = null;
  state.dockerConsoleBuffer = "";
  const dialog = $("docker-console-dialog");
  if (dialog?.open) dialog.close();
  const trigger = state.dockerConsoleTrigger;
  state.dockerConsoleTrigger = null;
  if (trigger && typeof trigger.focus === "function") trigger.focus();
}

function wireDockerConsoleEvents() {
  $("docker-console-start")?.addEventListener("click", startDockerConsole);
  $("docker-console-close")?.addEventListener("click", requestDockerConsoleClose);
  $("docker-console-copy")?.addEventListener("click", () => {
    copyText(state.dockerConsoleBuffer).then((ok) => toast(ok ? "Console output copied." : "Copy failed.", ok ? "ok" : "error"));
  });
  $("docker-console-user")?.addEventListener("input", updateDockerConsoleWarning);
  $("docker-console-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendDockerConsoleInput(`${event.currentTarget.value}\r`);
      event.currentTarget.value = "";
    }
  });
  $("docker-console-send")?.addEventListener("click", () => {
    const input = $("docker-console-input");
    sendDockerConsoleInput(`${input.value}\r`);
    input.value = "";
    input.focus();
  });
  $("docker-console-output")?.addEventListener("keydown", (event) => {
    if (!state.dockerConsole?.session) return;
    const keyMap = {
      Enter: "\r",
      Backspace: "\x7f",
      Tab: "\t",
      Escape: "\x1b",
      ArrowUp: "\x1b[A",
      ArrowDown: "\x1b[B",
      ArrowRight: "\x1b[C",
      ArrowLeft: "\x1b[D",
      Home: "\x1b[H",
      End: "\x1b[F",
      Delete: "\x1b[3~",
    };
    if (event.ctrlKey && (event.key === "c" || event.key === "C")) {
      event.preventDefault();
      sendDockerConsoleInput("\x03");
      return;
    }
    if (event.ctrlKey && (event.key === "d" || event.key === "D")) {
      event.preventDefault();
      sendDockerConsoleInput("\x04");
      return;
    }
    if (event.ctrlKey && (event.key === "l" || event.key === "L")) {
      event.preventDefault();
      sendDockerConsoleInput("\x0c");
      dockerConsoleWrite("\x1b[2J\x1b[H");
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (keyMap[event.key]) {
      event.preventDefault();
      sendDockerConsoleInput(keyMap[event.key]);
    } else if (event.key.length === 1) {
      event.preventDefault();
      sendDockerConsoleInput(event.key);
    }
  });
  $("docker-console-output")?.addEventListener("paste", (event) => {
    const text = event.clipboardData?.getData("text");
    if (!text) return;
    event.preventDefault();
    sendDockerConsoleInput(text);
  });
  $("docker-console-dialog")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    requestDockerConsoleClose();
  });
  $("docker-console-dialog")?.addEventListener("close", () => {
    closeDockerConsoleSource();
    state.dockerConsole = null;
    state.dockerConsoleBuffer = "";
    const trigger = state.dockerConsoleTrigger;
    state.dockerConsoleTrigger = null;
    if (trigger && typeof trigger.focus === "function") trigger.focus();
  });
  window.addEventListener("resize", scheduleDockerConsoleResize);
}

function openDockerRename(container) {
  openDockerDialog(`Rename · ${dockerContainerLabel(container)}`, "Container rename", (body) => {
    const name = h("input", { type: "text", maxlength: "128", required: true, value: container.name || "" });
    const form = h("form", { class: "form-stack" }, h("label", { class: "field" }, h("span", {}, "New name"), name));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      queueDockerAction(dockerActionUrl(), { action: "container.rename", container_id: container.id, new_name: name.value.trim() }, `Rename ${dockerContainerLabel(container)}`, { showOutput: true });
    });
    body.append(form);
  }, (actions) => {
    actions.append(button("Rename", "button-primary", () => $("docker-dialog-body").querySelector("form")?.requestSubmit(), !dockerCan("mutate")));
  });
}

function openDockerUpdate(container) {
  openDockerDialog(`Update · ${dockerContainerLabel(container)}`, "Container limits", (body) => {
    const restart = h("select", {}, h("option", { value: "" }, "No change"), ...["no", "always", "unless-stopped", "on-failure", "on-failure:3"].map((value) => h("option", { value }, value)));
    const memory = h("input", { type: "text", maxlength: "32", placeholder: "512m (optional)" });
    const cpus = h("input", { type: "text", maxlength: "16", placeholder: "1.5 (optional)" });
    const pids = h("input", { type: "number", min: "0", max: "1000000", placeholder: "pids limit (optional)" });
    const form = h(
      "form",
      { class: "form-stack" },
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "Restart policy"), restart), h("label", { class: "field" }, h("span", {}, "Memory"), memory)),
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "CPUs"), cpus), h("label", { class: "field" }, h("span", {}, "PIDs limit"), pids)),
      h("p", { class: "form-hint" }, "Docker can update restart policy and resource limits in place. Environment, ports, and mounts require a recreate."),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const payload = { action: "container.update", container_id: container.id };
      if (restart.value) payload.restart_policy = restart.value;
      if (memory.value.trim()) payload.memory = memory.value.trim();
      if (cpus.value.trim()) payload.cpus = cpus.value.trim();
      if (pids.value !== "") payload.pids_limit = Number(pids.value);
      if (Object.keys(payload).length === 2) return setText("docker-dialog-error", "Choose at least one limit.");
      queueDockerAction(dockerActionUrl(), payload, `Update ${dockerContainerLabel(container)}`, { showOutput: true });
    });
    body.append(form);
  }, (actions) => {
    actions.append(button("Apply update", "button-primary", () => $("docker-dialog-body").querySelector("form")?.requestSubmit(), !dockerCan("mutate")));
  });
}

function openDockerClone(container) {
  const detail = state.dockerHostDetail;
  const images = detail?.images || [];
  const networks = detail?.networks || [];
  const matchedImage = images.find((image) =>
    (image.repo_tags || []).some((tag) => tag === container.image || tag.split(":")[0] === container.image),
  );
  openDockerDialog(`Clone · ${dockerContainerLabel(container)}`, "Create container from safe spec", (body) => {
    const name = h("input", { type: "text", maxlength: "128", value: `${container.name || "container"}-clone` });
    const image = h(
      "select",
      {},
      ...images.map((item) => h("option", { value: item.id }, (item.repo_tags || []).join(", ") || short(item.id, 20))),
    );
    if (matchedImage) image.value = matchedImage.id;
    const command = h("textarea", { rows: "3", placeholder: "Optional command argv, one argument per line" });
    const restartPolicies = ["", "no", "always", "unless-stopped", "on-failure", "on-failure:3"];
    const restart = h("select", {}, ...restartPolicies.map((value) => h("option", { value }, value || "No restart policy (default)")));
    restart.value = restartPolicies.includes(container.restart_policy) ? container.restart_policy : "";
    const memory = h("input", { type: "text", maxlength: "32", value: container.memory_limit ? `${Math.round(container.memory_limit / (1024 * 1024))}m` : "" });
    const cpus = h("input", { type: "text", maxlength: "16", value: container.nano_cpus ? (container.nano_cpus / 1e9).toFixed(2) : "" });
    const pids = h("input", { type: "number", min: "0", max: "1000000", value: container.pids_limit ?? "" });
    const networkChecks = networks.map((network) => {
      const checked = (container.networks || []).includes(network.name);
      const input = h("input", { type: "checkbox", value: network.id, checked });
      return h("label", { class: "checkbox-row" }, input, h("span", {}, network.name));
    });
    const drift = h("input", { type: "checkbox" });
    const form = h(
      "form",
      { class: "form-stack" },
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "New name"), name), h("label", { class: "field" }, h("span", {}, "Image"), image)),
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "Restart policy"), restart), h("label", { class: "field" }, h("span", {}, "Memory"), memory)),
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "CPUs"), cpus), h("label", { class: "field" }, h("span", {}, "PIDs limit"), pids)),
      h("label", { class: "field" }, h("span", {}, "Command argv (optional)"), command),
      h("p", { class: "eyebrow" }, "Networks"),
      ...networkChecks,
      h("p", { class: "form-hint" }, "Clone copies only the allowlisted spec: image, new name, argv command, restart policy, resource limits, and existing networks. Host bind mounts, env values, devices, capabilities, privileged mode, and arbitrary flags are not copied."),
      container.compose_project
        ? h("div", { class: "callout" }, h("p", {}, `Source is Compose-managed (${container.compose_project}). Cloning can drift from the stack; docker compose up may revert it.`), h("label", { class: "checkbox-row" }, drift, h("span", {}, "I understand this may drift from Compose")))
        : null,
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const args = command.value.split("\n").map((line) => line.trim()).filter(Boolean);
      if (container.compose_project && !drift.checked) {
        return setText("docker-dialog-error", "Acknowledge Compose drift before cloning this container.");
      }
      const payload = {
        action: "container.clone",
        container_id: container.id,
        new_name: name.value.trim(),
        image_id: image.value,
        command: args,
        restart_policy: restart.value,
        memory: memory.value.trim(),
        cpus: cpus.value.trim(),
        pids_limit: pids.value === "" ? null : Number(pids.value),
        network_ids: [...form.querySelectorAll('input[type="checkbox"][value]:checked')].map((item) => item.value),
        acknowledge_drift: container.compose_project ? drift.checked : false,
      };
      const confirmed = await confirmAction(
        `Clone ${dockerContainerLabel(container)}?`,
        container.compose_project
          ? "This creates a separate container from the safe spec. It may drift from the Compose-managed stack."
          : "This creates a separate container from the safe spec. The source container is not changed.",
        "Clone container",
      );
      if (!confirmed) return;
      closeDockerDialog();
      await queueDockerAction(dockerActionUrl(), payload, `Clone ${dockerContainerLabel(container)}`, { showOutput: true });
    });
    body.append(form);
  }, (actions) => {
    actions.append(button("Clone", "button-primary", () => $("docker-dialog-body").querySelector("form")?.requestSubmit(), !dockerCan("mutate") || !images.length));
  });
}

async function removeDockerContainer(container) {
  const running = String(container.state || "").toLowerCase() === "running";
  return confirmDockerAction(
    `Remove ${dockerContainerLabel(container)}?`,
    running
      ? "This force-removes a running container. Volumes are not removed. This cannot be undone."
      : "This removes the stopped container. Volumes are not removed. This cannot be undone.",
    "Remove container",
    dockerActionUrl(),
    { action: "container.remove", container_id: container.id, force: running, remove_volumes: false },
    `Remove ${dockerContainerLabel(container)}`,
    { showOutput: true },
  );
}

const DOCKER_CLEANUP_META = {
  stopped_containers: {
    label: "Stopped containers",
    warning: "Removes stopped containers except Compose-managed containers by default. Volumes are not removed.",
  },
  dangling_images: { label: "Dangling images", warning: "Removes untagged images only. This is generally safe." },
  unused_images: { label: "Unused images", warning: "Destructive: removes tagged images not used by a cached container." },
  unused_volumes: { label: "Unused volumes", warning: "Destructive and irreversible. Docker only removes volumes not in use." },
  unused_networks: { label: "Unused networks", warning: "Removes unused non-default networks." },
  build_cache: { label: "Build cache", warning: "Clears Docker build cache." },
};

async function confirmDockerCleanup(scope) {
  const meta = DOCKER_CLEANUP_META[scope] || { label: scope, warning: "Docker cleanup." };
  return confirmDockerAction(
    `Clean up ${meta.label.toLowerCase()}?`,
    `${meta.warning} The exact filter is scope=${scope}. This action runs on the selected host.`,
    "Run cleanup",
    `/api/docker/hosts/${encodeURIComponent(state.dockerHostId)}/cleanup`,
    { scope, include_compose: false },
    `Cleanup ${meta.label}`,
    { showOutput: true },
  );
}

function renderDockerImages(body, detail) {
  const images = detail.images || [];
  const canMutate = dockerCan("mutate");
  const rows = images.map((image) => {
    const protectedImage = Number(image.containers || 0) > 0;
    return h(
      "tr",
      {},
      h("td", {}, (image.repo_tags || []).join(", ") || (image.dangling ? "<none>" : short(image.id, 16))),
      h("td", { class: "mono" }, short(image.id, 20)),
      h("td", {}, fmtBytes(image.size)),
      h("td", {}, image.dangling ? statusPill("dangling", "warning") : statusPill("tagged", "online")),
      h("td", {}, image.containers),
      h(
        "td",
        {},
        button(
          "Remove",
          "button-danger button-small",
          () => confirmDockerAction(
            "Remove image?",
            "Docker refuses to remove an image used by a container. This cannot be undone.",
            "Remove image",
            dockerActionUrl(),
            { action: "image.remove", image_id: image.id },
            "Remove image",
            { showOutput: true },
          ),
          !canMutate || protectedImage,
        ),
      ),
    );
  });
  const children = [
    h(
      "div",
      { class: "section-toolbar" },
      button("Prune dangling", "button-ghost button-small", () => confirmDockerCleanup("dangling_images"), !dockerCan("prune")),
      button("Prune unused", "button-danger button-small", () => confirmDockerCleanup("unused_images"), !dockerCan("prune")),
    ),
  ];
  if (!images.length) children.push(h("div", { class: "empty-inline" }, "No images reported."));
  else {
    children.push(
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Repository / tags"), h("th", {}, "Image ID"), h("th", {}, "Size"), h("th", {}, "State"), h("th", {}, "Containers"), h("th", {}, "Actions"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter images", pageSize: 25 },
      ),
    );
  }
  body.append(section("Docker host", "Images", ...children));
}

function renderDockerVolumes(body, detail) {
  const volumes = detail.volumes || [];
  const canMutate = dockerCan("mutate");
  const rows = volumes.map((volume) =>
    h(
      "tr",
      {},
      h("td", {}, h("strong", {}, volume.name)),
      h("td", {}, volume.driver || "—"),
      h("td", {}, volume.scope || "—"),
      h("td", {}, volume.in_use ? "yes" : "unknown"),
      h("td", { class: "mono" }, short(volume.mountpoint || "—", 72)),
      h(
        "td",
        {},
        button(
          "Remove",
          "button-danger button-small",
          () => confirmDockerAction(
            "Remove volume?",
            "This permanently deletes the volume and its data. This cannot be undone.",
            "Remove volume",
            dockerActionUrl(),
            { action: "volume.remove", volume_name: volume.name },
            `Remove volume ${volume.name}`,
            { showOutput: true },
          ),
          !canMutate || volume.in_use,
        ),
      ),
    ),
  );
  const children = [
    h(
      "div",
      { class: "section-toolbar" },
      button("Prune unused volumes", "button-danger button-small", () => confirmDockerCleanup("unused_volumes"), !dockerCan("prune")),
    ),
  ];
  if (!volumes.length) children.push(h("div", { class: "empty-inline" }, "No volumes reported."));
  else {
    children.push(
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Driver"), h("th", {}, "Scope"), h("th", {}, "In use"), h("th", {}, "Mountpoint"), h("th", {}, "Actions"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter volumes", pageSize: 25 },
      ),
    );
  }
  body.append(section("Docker host", "Volumes", ...children));
}

function renderDockerNetworks(body, detail) {
  const networks = detail.networks || [];
  const canMutate = dockerCan("mutate");
  const rows = networks.map((network) => {
    const isDefault = ["bridge", "host", "none"].includes(String(network.name || "").toLowerCase());
    return h(
      "tr",
      {},
      h("td", {}, h("strong", {}, network.name || short(network.id, 16))),
      h("td", {}, network.driver || "—"),
      h("td", {}, network.scope || "—"),
      h("td", {}, network.internal ? "yes" : "no"),
      h("td", {}, network.container_count),
      h(
        "td",
        {},
        button(
          "Remove",
          "button-danger button-small",
          () => confirmDockerAction(
            "Remove network?",
            "Docker refuses to remove a network with attached containers. This cannot be undone.",
            "Remove network",
            dockerActionUrl(),
            { action: "network.remove", network_id: network.id },
            `Remove network ${network.name || short(network.id, 12)}`,
            { showOutput: true },
          ),
          !canMutate || isDefault || Number(network.container_count || 0) > 0,
        ),
      ),
    );
  });
  const children = [
    h(
      "div",
      { class: "section-toolbar" },
      button("Prune unused networks", "button-ghost button-small", () => confirmDockerCleanup("unused_networks"), !dockerCan("prune")),
    ),
  ];
  if (!networks.length) children.push(h("div", { class: "empty-inline" }, "No networks reported."));
  else {
    children.push(
      interactiveTable(
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Driver"), h("th", {}, "Scope"), h("th", {}, "Internal"), h("th", {}, "Containers"), h("th", {}, "Actions"))),
          h("tbody", {}, ...rows),
        ),
        { placeholder: "Filter networks", pageSize: 25 },
      ),
    );
  }
  body.append(section("Docker host", "Networks", ...children));
}

function renderDockerHostStacks(body, detail) {
  const host = h("div", { class: "empty-inline" }, "Loading Compose projects…");
  body.append(section("Docker host", "Compose stacks", host));
  api(`/api/docker/deployments?agent_id=${encodeURIComponent(state.dockerHostId)}&limit=100`)
    .then((data) => {
      clear(host);
      const active = (data.deployments || []).filter((deployment) => deployment.active);
      const managedProjects = new Set(active.map((deployment) => deployment.project_name));
      const managedRows = active.map((deployment) =>
        h(
          "tr",
          {},
          h("td", {}, h("strong", {}, deployment.stack_name || deployment.stack_slug || "—")),
          h("td", {}, deployment.project_name),
          h("td", { class: "mono" }, short(deployment.revision_hash, 16)),
          h("td", {}, deployment.action),
          h("td", {}, deployment.drift ? statusPill("drift", "warning") : deployment.status),
          h("td", {}, button("Open", "button-secondary button-small", () => {
            navigate("compose-files");
            openComposeStack(deployment.stack_id);
          })),
        ),
      );
      const projects = new Map();
      for (const container of detail.containers || []) {
        const project = container.compose_project || "";
        if (!project || managedProjects.has(project)) continue;
        if (!projects.has(project)) projects.set(project, []);
        projects.get(project).push(container);
      }
      const externalRows = [...projects.entries()].map(([project, containers]) =>
        h(
          "tr",
          {},
          h("td", {}, h("strong", {}, project)),
          h("td", {}, `${containers.length} container${containers.length === 1 ? "" : "s"}`),
          h("td", {}, statusPill("external", "warning")),
          h("td", {}, "Read-only in Sextant"),
        ),
      );
      host.append(
        h("h3", {}, "Managed stacks"),
        managedRows.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Stack"), h("th", {}, "Project"), h("th", {}, "Revision"), h("th", {}, "Action"), h("th", {}, "Status"), h("th", {}, ""))), h("tbody", {}, ...managedRows)))
          : h("div", { class: "empty-inline" }, "No active Sextant Compose deployment on this host."),
        h("h3", {}, "External Compose projects"),
        externalRows.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Project"), h("th", {}, "Containers"), h("th", {}, "State"), h("th", {}, "Management"))), h("tbody", {}, ...externalRows)))
          : h("div", { class: "empty-inline" }, "No external Compose projects detected in the cached inventory."),
      );
    })
    .catch((error) => {
      clear(host);
      host.append(h("div", { class: "empty-inline" }, error.message));
    });
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

function composeAdmin() {
  return Boolean(state.permissions.admin);
}

async function loadComposeFiles(silent = false) {
  if (state.composeLoading) return;
  state.composeLoading = true;
  try {
    const [stacks, settings] = await Promise.all([
      api("/api/docker/stacks"),
      state.permissions.admin ? api("/api/docker/settings") : Promise.resolve(null),
    ]);
    state.composeStacks = stacks.stacks || [];
    state.composeSettings = settings;
    renderComposeStackList();
    renderComposeSettingsPanel();
    if (state.composeStackId && !state.composeStacks.some((stack) => stack.id === state.composeStackId)) {
      closeComposeStack();
    }
  } catch (error) {
    if (!silent) toast(error.message, "error");
    setText("compose-status", error.message);
  } finally {
    state.composeLoading = false;
  }
}

function renderComposeStackList() {
  const container = $("compose-stack-list");
  const empty = $("compose-empty");
  if (!container || !empty) return;
  clear(container);
  const stacks = state.composeStacks || [];
  empty.hidden = stacks.length > 0;
  setText(
    "compose-status",
    stacks.length
      ? `${stacks.length} Compose stack${stacks.length === 1 ? "" : "s"}. Select one to view revisions or deploy.`
      : "No Compose stacks yet.",
  );
  for (const stack of stacks) {
    const selected = stack.id === state.composeStackId;
    container.append(
      h(
        "div",
        { class: "list-row" },
        h(
          "div",
          {},
          h("strong", {}, stack.name, " ", selected ? statusPill("selected", "online") : null),
          h("p", { class: "form-hint" }, `${stack.slug} · revision ${short(stack.current_revision_hash, 12)} · ${stack.revision_count} revisions · ${stack.deployed_host_count} host${stack.deployed_host_count === 1 ? "" : "s"}`),
          h("p", { class: "form-hint" }, `Updated ${relativeTime(stack.updated_at)} by ${stack.created_by}`),
        ),
        h(
          "div",
          { class: "list-row-actions" },
          button(selected ? "Selected" : "Open", selected ? "button-ghost button-small" : "button-secondary button-small", () => openComposeStack(stack.id)),
          composeAdmin()
            ? button("Delete", "button-danger button-small", () => deleteComposeStack(stack), Boolean(stack.deployed_host_count))
            : null,
        ),
      ),
    );
  }
}

function renderComposeSettingsPanel() {
  const container = $("compose-settings");
  if (!container || !state.permissions.admin) return;
  clear(container);
  if (!state.composeSettings) {
    container.append(h("span", {}, "Loading storage settings…"));
    return;
  }
  const settings = state.composeSettings;
  const pathInput = h("input", { type: "text", value: settings.compose_dir || "", maxlength: "1024", disabled: !settings.editable });
  container.append(
    h("p", {}, `Effective path: ${settings.compose_dir}`),
    h("p", {}, `Source: ${settings.source === "env" ? "environment (read-only)" : "Sextant database"}`),
    h("p", {}, `${settings.counts?.stacks || 0} stacks · ${settings.counts?.revisions || 0} revision snapshots`),
    h("div", { class: "section-toolbar" }, h("label", { class: "field" }, h("span", {}, "Compose directory"), pathInput), button("Save path", "button-secondary button-small", async () => {
      try {
        await api("/api/docker/settings", { method: "PATCH", body: { compose_dir: pathInput.value.trim() } });
        toast("Compose storage path updated.");
        await loadComposeFiles(true);
      } catch (error) {
        toast(error.message, "error");
      }
    }, !settings.editable)),
  );
}

function closeComposeStack() {
  state.composeStackId = null;
  state.composeDetail = null;
  state.composeDraftFiles = {};
  const detail = $("compose-stack-detail");
  if (detail) detail.hidden = true;
  renderComposeStackList();
}

async function openComposeStack(stackId) {
  state.composeStackId = stackId;
  state.composeTab = "files";
  renderComposeStackList();
  const detail = $("compose-stack-detail");
  if (detail) detail.hidden = false;
  setText("compose-stack-title", "Loading Compose stack…");
  setText("compose-stack-meta", "Fetching revisions and files.");
  try {
    const data = await api(`/api/docker/stacks/${encodeURIComponent(stackId)}`);
    if (state.composeStackId !== stackId) return;
    state.composeDetail = data.stack;
    state.composeDraftFiles = data.stack.contents ? { ...data.stack.contents } : {};
    const names = Object.keys(state.composeDraftFiles);
    state.composeFile = names.includes("compose.yaml")
      ? "compose.yaml"
      : names.includes("compose.yml")
        ? "compose.yml"
        : names.sort()[0] || "compose.yaml";
    renderComposeDetail();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderComposeDetail() {
  const stack = state.composeDetail;
  if (!stack) return;
  setText("compose-stack-title", stack.name);
  setText("compose-stack-eyebrow", stack.slug);
  setText("compose-stack-meta", `Current revision ${short(stack.current_revision_hash, 16)} · ${stack.revisions.length} revisions · ${stack.deployed_host_count} deployed host${stack.deployed_host_count === 1 ? "" : "s"}`);
  const notice = $("compose-stack-notice");
  if (notice) {
    clear(notice);
    notice.hidden = true;
    if (!composeAdmin()) {
      notice.hidden = false;
      notice.append(h("p", {}, "Compose file contents and .env values are admin-only. You can deploy an existing revision."));
    }
  }
  const deploy = $("compose-deploy-current");
  if (deploy) {
    deploy.disabled = !state.permissions.operate || !stack.revisions.length;
  }
  document.querySelectorAll("#compose-tabs .tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.composeTab === state.composeTab);
  });
  renderComposeTab();
}

function renderComposeTab() {
  const body = $("compose-body");
  if (!body || !state.composeDetail) return;
  clear(body);
  if (state.composeTab === "revisions") return renderComposeRevisions(body);
  if (state.composeTab === "deployments") return renderComposeDeployments(body);
  renderComposeFiles(body);
}

function renderComposeFiles(body) {
  const stack = state.composeDetail;
  if (!composeAdmin()) {
    body.append(
      section("Compose stack", "Files", h("div", { class: "empty-inline" }, "Admins can view Compose file contents. Deploy an existing revision below.")),
      h("div", { class: "section-toolbar" }, button("Deploy current revision", "button-primary", () => deployComposeDialog(stack, stack.current_revision_hash), !state.permissions.operate)),
    );
    return;
  }
  const names = Object.keys(state.composeDraftFiles).sort();
  if (!names.includes(state.composeFile)) state.composeFile = names[0] || "compose.yaml";
  const fileList = h("div", { class: "compose-file-list" });
  for (const name of names) {
    fileList.append(
      button(name, `button-ghost button-small${name === state.composeFile ? " is-active" : ""}`, () => {
        state.composeFile = name;
        renderComposeTab();
      }),
    );
  }
  const editor = h("textarea", { class: "compose-editor", spellcheck: "false" });
  editor.value = state.composeDraftFiles[state.composeFile] || "";
  editor.addEventListener("input", () => {
    state.composeDraftFiles[state.composeFile] = editor.value;
  });
  const name = h("input", { type: "text", maxlength: "120", value: stack.name });
  const description = h("input", { type: "text", maxlength: "2000", value: stack.description || "" });
  body.append(
    section(
      "Compose stack",
      "Files",
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "Name"), name), h("label", { class: "field" }, h("span", {}, "Description"), description)),
      h("p", { class: "form-hint" }, `Files are stored under ${state.composeSettings?.compose_dir || "the configured Compose directory"}. Revisions are immutable; saving creates a new one.`),
      fileList,
      h("div", { class: "section-toolbar" },
        button("Add override", "button-ghost button-small", () => addComposeOverrideDialog()),
        button("Remove file", "button-danger button-small", () => {
          if (!["compose.yaml", "compose.yml"].includes(state.composeFile)) {
            delete state.composeDraftFiles[state.composeFile];
            state.composeFile = Object.keys(state.composeDraftFiles).sort()[0] || "compose.yaml";
            renderComposeTab();
          }
        }, ["compose.yaml", "compose.yml", ".env"].includes(state.composeFile)),
      ),
      editor,
      h("div", { class: "section-toolbar" },
        button("Save new revision", "button-primary", () => saveComposeStack(name.value.trim(), description.value.trim()), !name.value.trim() || state.composeBusy),
        button("Deploy current revision", "button-secondary", () => deployComposeDialog(stack, stack.current_revision_hash), !state.permissions.operate),
      ),
    ),
  );
}

function addComposeOverrideDialog() {
  openDockerDialog("Add Compose override", "Compose file", (body) => {
    const name = h("input", { type: "text", maxlength: "128", placeholder: "override.yaml" });
    const form = h("form", { class: "form-stack" }, h("label", { class: "field" }, h("span", {}, "File name"), name), h("p", { class: "form-hint" }, "Flat YAML only. use override.yaml or override.yml."));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = name.value.trim();
      if (!value || !/^[A-Za-z0-9._-]{1,128}\.(ya?ml)$/.test(value) || value.startsWith("compose.")) {
        return setText("docker-dialog-error", "Enter a valid override.yaml or override.yml name.");
      }
      state.composeDraftFiles[value] = "services: {}\n";
      state.composeFile = value;
      closeDockerDialog();
      renderComposeTab();
    });
    body.append(form);
  }, (actions) => {
    actions.append(button("Add file", "button-primary", () => $("docker-dialog-body").querySelector("form")?.requestSubmit()));
  });
}

async function saveComposeStack(name, description) {
  const stack = state.composeDetail;
  if (!stack || state.composeBusy) return;
  state.composeBusy = true;
  try {
    const payload = { name, description, files: state.composeDraftFiles, message: "UI edit" };
    const data = await api(`/api/docker/stacks/${encodeURIComponent(stack.id)}`, { method: "PUT", body: payload });
    state.composeDetail = data.stack;
    state.composeDraftFiles = data.stack.contents ? { ...data.stack.contents } : {};
    toast("Compose revision created.");
    await loadComposeFiles(true);
    renderComposeDetail();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.composeBusy = false;
  }
}

function newComposeStackDialog() {
  openDockerDialog("New Compose stack", "Compose Files", (body) => {
    const slug = h("input", { type: "text", maxlength: "64", placeholder: "web" });
    const name = h("input", { type: "text", maxlength: "120", placeholder: "Web stack" });
    const description = h("input", { type: "text", maxlength: "2000" });
    const compose = h("textarea", { class: "compose-editor", spellcheck: "false" });
    compose.value = "services:\n  web:\n    image: nginx:latest\n";
    const form = h(
      "form",
      { class: "form-stack" },
      h("div", { class: "form-grid" }, h("label", { class: "field" }, h("span", {}, "Slug"), slug), h("label", { class: "field" }, h("span", {}, "Name"), name)),
      h("label", { class: "field" }, h("span", {}, "Description"), description),
      h("label", { class: "field" }, h("span", {}, "compose.yaml"), compose),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const data = await api("/api/docker/stacks", {
          method: "POST",
          body: { slug: slug.value.trim(), name: name.value.trim(), description: description.value.trim(), files: { "compose.yaml": compose.value }, message: "Created in UI" },
        });
        closeDockerDialog();
        toast("Compose stack created.");
        await loadComposeFiles(true);
        await openComposeStack(data.stack.id);
      } catch (error) {
        setText("docker-dialog-error", error.message);
      }
    });
    body.append(form);
  }, (actions) => {
    actions.append(button("Create stack", "button-primary", () => $("docker-dialog-body").querySelector("form")?.requestSubmit()));
  });
}

async function deleteComposeStack(stack) {
  const confirmed = await confirmAction(`Delete ${stack.name}?`, "This removes the stack and its revision files. Active deployments must be down first.", "Delete stack");
  if (!confirmed) return;
  try {
    await api(`/api/docker/stacks/${encodeURIComponent(stack.id)}`, { method: "DELETE" });
    toast("Compose stack deleted.");
    closeComposeStack();
    await loadComposeFiles(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderComposeRevisions(body) {
  const stack = state.composeDetail;
  const rows = (stack.revisions || []).map((revision) =>
    h(
      "tr",
      {},
      h("td", { class: "mono" }, short(revision.revision_hash, 16)),
      h("td", {}, revision.created_at ? localTime(revision.created_at) : "—"),
      h("td", {}, revision.message || "—"),
      h("td", {}, revision.created_by),
      h("td", {}, (revision.files || []).map((file) => file.name).join(", ")),
      h("td", {}, h("div", { class: "docker-row-actions" },
        button("Deploy", "button-secondary button-small", () => deployComposeDialog(stack, revision.revision_hash), !state.permissions.operate),
        revision.revision_hash !== stack.current_revision_hash
          ? button("Roll back", "button-ghost button-small", () => deployComposeDialog(stack, revision.revision_hash), !state.permissions.operate)
          : null,
      )),
    ),
  );
  body.append(
    section(
      "Compose stack",
      "Revisions",
      rows.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Revision"), h("th", {}, "Created"), h("th", {}, "Message"), h("th", {}, "By"), h("th", {}, "Files"), h("th", {}, "Actions"))), h("tbody", {}, ...rows)))
        : h("div", { class: "empty-inline" }, "No revisions found."),
    ),
  );
}

async function renderComposeDeployments(body) {
  const stack = state.composeDetail;
  const host = h("div", { class: "empty-inline" }, "Loading deployments…");
  body.append(section("Compose stack", "Deployments", host));
  try {
    const data = await api(`/api/docker/deployments?stack_id=${encodeURIComponent(stack.id)}&limit=100`);
    clear(host);
    const rows = (data.deployments || []).map((deployment) =>
      h(
        "tr",
        {},
        h("td", {}, deployment.action),
        h("td", {}, deployment.agent_name || deployment.agent_id),
        h("td", {}, deployment.project_name),
        h("td", { class: "mono" }, short(deployment.revision_hash, 12)),
        h("td", {}, (() => {
          const state = deployment.drift
            ? "drift"
            : deployment.active && deployment.status === "succeeded"
              ? "deployed"
              : deployment.status;
          return statusPill(
            state,
            state === "failed" ? "critical" : state === "drift" ? "warning" : state === "deployed" ? "online" : "neutral",
          );
        })()),
        h("td", {}, deployment.created_by),
        h("td", {}, deployment.created_at ? relativeTime(deployment.created_at) : "—"),
        h("td", {}, h("div", { class: "docker-row-actions" },
          button("Log", "button-ghost button-small", () => showComposeDeploymentLog(deployment)),
          deployment.status === "queued" && deployment.command_id
            ? button("Cancel", "button-ghost button-small", () => cancelComposeDeployment(deployment))
            : null,
          deployment.active && deployment.action === "up"
            ? button("Down", "button-danger button-small", () => openComposeDownDialog(deployment))
            : null,
          deployment.active && deployment.action === "up"
            ? button("Restart", "button-ghost button-small", () => composeExistingAction(deployment, "restart"))
            : null,
          deployment.active && deployment.action === "up"
            ? button("Pull", "button-ghost button-small", () => composeExistingAction(deployment, "pull"))
            : null,
        )),
      ),
    );
    host.append(
      rows.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Action"), h("th", {}, "Host"), h("th", {}, "Project"), h("th", {}, "Revision"), h("th", {}, "Status"), h("th", {}, "Actor"), h("th", {}, "When"), h("th", {}, "Actions"))), h("tbody", {}, ...rows)))
        : h("div", { class: "empty-inline" }, "No deployments for this stack yet."),
    );
  } catch (error) {
    clear(host);
    host.append(h("div", { class: "empty-inline" }, error.message));
  }
}

function showComposeDeploymentLog(deployment) {
  api(`/api/docker/deployments/${encodeURIComponent(deployment.id)}`)
    .then((data) => {
      openDockerDialog(`${deployment.action} · ${deployment.project_name}`, "Deployment log", (body) => {
        body.append(
          h("div", { class: "compose-meta-grid" },
            infoTile("Status", deployment.status),
            infoTile("Host", deployment.agent_name || deployment.agent_id),
            infoTile("Revision", short(deployment.revision_hash, 16)),
            infoTile("Started", deployment.started_at ? localTime(deployment.started_at) : "—"),
            infoTile("Finished", deployment.finished_at ? localTime(deployment.finished_at) : "—"),
          ),
          h("pre", { class: "terminal docker-dialog-output" }, data.deployment.log || data.deployment.error || "(no log)"),
        );
      });
    })
    .catch((error) => toast(error.message, "error"));
}

async function cancelComposeDeployment(deployment) {
  try {
    await api(`/api/commands/${encodeURIComponent(deployment.command_id)}/cancel`, { method: "POST", body: {} });
    toast("Compose deployment cancelled.");
    renderComposeTab();
    await loadComposeFiles(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function composeExistingAction(deployment, action) {
  return composeDeploy(deployment.stack_id, deployment.agent_id, deployment.revision_hash, deployment.project_name, action, false);
}

function openComposeDownDialog(deployment) {
  openDockerDialog(`Down · ${deployment.project_name}`, "Compose down", (body) => {
    const removeVolumes = h("input", { type: "checkbox" });
    body.append(
      h("p", { class: "form-hint" }, "Stops and removes the selected Compose project. Volumes are retained unless explicitly selected."),
      h("label", { class: "checkbox-row" }, removeVolumes, h("span", {}, "Also remove named volumes (destructive)")),
    );
    body.append(button("Confirm down", "button-danger", () => {
      closeDockerDialog();
      composeDeploy(deployment.stack_id, deployment.agent_id, deployment.revision_hash, deployment.project_name, "down", removeVolumes.checked);
    }));
  });
}

function deployComposeDialog(stack, revisionHash) {
  api("/api/docker/hosts")
    .then((data) => {
      const hosts = (data.hosts || []).filter((host) => host.available && host.capabilities?.compose !== false);
      openDockerDialog(`Deploy ${stack.name}`, "Compose deployment", (body) => {
        if (!hosts.length) {
          body.append(h("div", { class: "empty-inline" }, "No Docker host currently allows Compose deployment."));
          return;
        }
        const hostChecks = hosts.map((item) => {
          const input = h("input", { type: "checkbox", value: item.agent_id });
          return h("label", { class: "checkbox-row" }, input, h("span", {}, `${item.name} (${item.hostname})`));
        });
        const revision = h("select", {}, ...(stack.revisions || []).map((item) => h("option", { value: item.revision_hash }, `${short(item.revision_hash, 16)} · ${item.created_at ? localTime(item.created_at) : ""}`)));
        if (revisionHash) revision.value = revisionHash;
        const project = h("input", { type: "text", maxlength: "63", value: `sextant_${stack.slug}`.slice(0, 63) });
        const pull = h("input", { type: "checkbox" });
        const form = h(
          "form",
          { class: "form-stack" },
          h("p", { class: "eyebrow" }, `Docker hosts (max 20)`),
          ...hostChecks,
          h("label", { class: "field" }, h("span", {}, "Revision"), revision),
          h("label", { class: "field" }, h("span", {}, "Project name per host"), project),
          h("label", { class: "checkbox-row" }, pull, h("span", {}, "Pull images before up")),
          h("p", { class: "form-hint" }, "One deployment and command is created per selected host. A failure on one host does not cancel the others. The previous active deployment stays active on a failed host."),
        );
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          const selected = [...form.querySelectorAll('input[type="checkbox"][value]:checked')].map((item) => item.value);
          if (!selected.length) return setText("docker-dialog-error", "Select at least one Docker host.");
          closeDockerDialog();
          composeDeployMany(stack.id, selected, revision.value, project.value.trim(), pull.checked);
        });
        body.append(form);
      }, (actions) => {
        actions.append(button("Deploy", "button-primary", () => $("docker-dialog-body").querySelector("form")?.requestSubmit(), !state.permissions.operate));
      });
    })
    .catch((error) => toast(error.message, "error"));
}

async function composeDeployMany(stackId, agentIds, revisionHash, projectName, pullBeforeUp = false) {
  const status = $("compose-status");
  if (status) status.textContent = `Queuing Compose deployment for ${agentIds.length} host${agentIds.length === 1 ? "" : "s"}…`;
  try {
    const data = await api(`/api/docker/stacks/${encodeURIComponent(stackId)}/deploy`, {
      method: "POST",
      body: {
        agent_ids: agentIds,
        revision_hash: revisionHash,
        project_name: projectName,
        action: "up",
        pull_before_up: pullBeforeUp,
      },
    });
    const results = data.results || [];
    const summary = data.summary || { total: results.length, queued: 0, failed: results.length };
    if (status) status.textContent = `Compose deploy: ${summary.queued} queued, ${summary.failed} failed.`;
    openDockerDialog("Compose deploy results", "Per-host summary", (body) => {
      body.append(
        h("p", { class: "form-hint" }, `${summary.queued} of ${summary.total} host(s) queued.`),
        h("div", { class: "table-wrap" }, h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Host"), h("th", {}, "Status"), h("th", {}, "Project"), h("th", {}, "Detail"))),
          h("tbody", {}, ...results.map((result) => h(
            "tr",
            {},
            h("td", {}, result.agent_name || result.agent_id),
            h("td", {}, result.status === "queued" ? statusPill("queued", "neutral") : statusPill("failed", "critical")),
            h("td", {}, result.deployment?.project_name || projectName),
            h("td", {}, result.error || (result.deployment?.id || "")),
          ))),
        )),
      );
    });
    if (state.composeStackId === stackId) {
      state.composeTab = "deployments";
      renderComposeDetail();
    }
    await loadComposeFiles(true);
  } catch (error) {
    if (status) status.textContent = error.message;
    toast(error.message, "error");
  }
}

async function composeDeploy(stackId, agentId, revisionHash, projectName, action, removeVolumes = false, pullBeforeUp = false) {
  const status = $("compose-status");
  if (status) status.textContent = `Queuing Compose ${action}…`;
  try {
    const data = await api(`/api/docker/stacks/${encodeURIComponent(stackId)}/deploy`, {
      method: "POST",
      body: {
        agent_id: agentId,
        revision_hash: revisionHash,
        project_name: projectName,
        action,
        pull_before_up: pullBeforeUp,
        remove_volumes: removeVolumes,
      },
    });
    if (status) status.textContent = `Compose ${action} queued for ${data.deployment.agent_name || agentId}.`;
    toast(`Compose ${action} queued.`);
    if (state.composeStackId === stackId) {
      if (state.composeTab !== "deployments") {
        state.composeTab = "deployments";
        renderComposeDetail();
      } else {
        renderComposeTab();
      }
    }
    await loadComposeFiles(true);
  } catch (error) {
    if (status) status.textContent = error.message;
    toast(error.message, "error");
  }
}

function settingsTabs() {
  return [...document.querySelectorAll("#settings-tabs .tab")];
}

function renderSettingsTabs() {
  const admin = Boolean(state.permissions.admin);
  const allowed = new Set(admin ? ["profile", "server", "domain", "ssh"] : ["profile"]);
  let active = state.settingsTab || "profile";
  if (!allowed.has(active)) active = "profile";
  state.settingsTab = active;
  settingsTabs().forEach((tab) => {
    tab.hidden = !allowed.has(tab.dataset.settingsTab);
    tab.classList.toggle("is-active", tab.dataset.settingsTab === active);
    tab.setAttribute("aria-pressed", tab.dataset.settingsTab === active ? "true" : "false");
  });
  document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.settingsPanel !== active;
  });
}

function loadSettingsPage() {
  renderSettings();
  if (state.permissions.admin && !state.systemSettings) {
    void loadSystemSettings();
  }
}

async function loadSystemSettings(force = false) {
  if (!state.permissions.admin || state.systemSettingsLoading) return;
  if (state.systemSettings && !force) {
    renderSettings();
    return;
  }
  state.systemSettingsLoading = true;
  try {
    state.systemSettings = await api("/api/system/settings");
    renderSettings();
  } catch (error) {
    setText("server-settings-error", error.message);
  } finally {
    state.systemSettingsLoading = false;
  }
}

function settingsUpdatedLabel(data) {
  if (!data?.meta?.updated_at) return "";
  const who = data.meta.updated_by ? ` by ${data.meta.updated_by}` : "";
  return `Updated ${localTime(data.meta.updated_at)}${who}`;
}

function renderSettings() {
  renderSettingsTabs();
  const user = state.user;
  if (!user) return;
  const profile = $("user-profile-form");
  if (profile) {
    profile.elements.display_name.value = user.display_name || user.username || "";
    profile.elements.email.value = user.email || "";
    const username = $("user-profile-username");
    if (username) username.value = user.username || "";
    const directory = user.source === "ad";
    profile.elements.display_name.disabled = directory;
    profile.elements.email.disabled = directory;
    const submit = profile.querySelector('button[type="submit"]');
    if (submit) submit.disabled = directory;
    setText(
      "user-profile-source-hint",
      directory
        ? "This directory account is managed by Active Directory / Samba. Change these attributes there."
        : "Your local account profile. Display name and email are shown in audit entries and notifications.",
    );
  }
  const account = $("settings-account-details");
  if (account) {
    clear(account);
    const entries = [
      ["Signed in as", user.display_name || user.username],
      ["Username", user.username],
      ["Role", user.role],
      ["Source", user.source === "ad" ? "Active Directory / Samba" : "Local account"],
      ["Directory auth", state.settings?.ldap_enabled ? "Enabled" : "Not configured"],
      ["Console version", state.settings?.version || "—"],
    ];
    for (const [key, value] of entries) {
      account.append(h("dt", {}, key), h("dd", {}, value));
    }
  }
  if (!state.permissions.admin) return;
  const data = state.systemSettings;
  if (!data) {
    setText("server-settings-status", "Loading server settings…");
    return;
  }
  renderServerSettings(data);
  renderDomainSettings(data);
  renderSshSettings(data);
}

function renderServerSettings(data) {
  const form = $("server-settings-form");
  if (!form || !data?.server) return;
  const server = data.server;
  form.elements.public_url.value = server.public_url || "";
  form.elements.allowed_hosts.value = (server.allowed_hosts || []).join(", ");
  form.elements.session_hours.value = String(server.session_hours ?? 12);
  form.elements.offline_after_seconds.value = String(server.offline_after_seconds ?? 90);
  form.elements.enrollment_ttl_minutes.value = String(server.enrollment_ttl_minutes ?? 30);
  form.elements.cookie_secure.checked = Boolean(server.cookie_secure);
  form.elements.trust_proxy.checked = Boolean(server.trust_proxy);
  setText("server-settings-status", settingsUpdatedLabel(data));
  setText(
    "server-settings-meta",
    `Data directory: ${server.data_dir || "—"} · version ${state.settings?.version || "—"}`,
  );
}

function renderDomainSettings(data) {
  const form = $("domain-settings-form");
  if (!form || !data?.domain) return;
  const domain = data.domain;
  form.elements.enabled.checked = Boolean(domain.enabled);
  form.elements.domain.value = domain.domain || "";
  form.elements.server.value = domain.server || "";
  form.elements.port.value = String(domain.port ?? 389);
  form.elements.use_ssl.checked = Boolean(domain.use_ssl);
  form.elements.base_dn.value = domain.base_dn || "";
  form.elements.bind_dn.value = domain.bind_dn || "";
  form.elements.bind_password.value = "";
  form.elements.bind_password.placeholder = domain.bind_password_set
    ? "configured — leave blank to keep"
    : "not configured";
  form.elements.user_filter.value = domain.user_filter || "(sAMAccountName={username})";
  form.elements.username_attr.value = domain.username_attr || "sAMAccountName";
  form.elements.display_name_attr.value = domain.display_name_attr || "displayName";
  form.elements.email_attr.value = domain.email_attr || "mail";
  form.elements.group_attr.value = domain.group_attr || "memberOf";
  form.elements.admin_groups.value = (domain.admin_groups || []).join("\n");
  form.elements.operator_groups.value = (domain.operator_groups || []).join("\n");
  form.elements.viewer_groups.value = (domain.viewer_groups || []).join("\n");
  form.elements.default_role.value = domain.default_role || "viewer";
  form.elements.ca_cert.value = domain.ca_cert || "";
  form.elements.timeout.value = String(domain.timeout ?? 8);
  form.elements.clear_bind_password.checked = false;
  setText("domain-settings-status", settingsUpdatedLabel(data));
  setText(
    "domain-settings-hint",
    data.meta?.ldap3_available
      ? "The optional ldap3 dependency is installed. Directory login is live after saving; local break-glass accounts stay available."
      : "The optional ldap3 dependency is not installed. Install `sextant[ldap]` before enabling directory login.",
  );
}

function renderSshSettings(data) {
  const form = $("ssh-settings-form");
  if (!form || !data?.ssh) return;
  const ssh = data.ssh;
  form.elements.source.value = ssh.source || "managed";
  form.elements.key_path.value = ssh.source === "managed" ? ssh.key_path || ssh.effective_key_path || "" : "";
  form.elements.key_path_override.value = ssh.source === "system_user" ? ssh.key_path || "" : "";
  form.elements.system_user.value = ssh.system_user || "";
  form.elements.config_path.value = ssh.config_path || "";
  form.elements.install_enabled.checked = Boolean(ssh.install_enabled);
  form.elements.private_key.value = "";
  form.elements.clear_key.checked = false;
  setText("ssh-settings-status", settingsUpdatedLabel(data));
  renderSshKeyDetails(data);
  syncSshFields();
}

function renderSshKeyDetails(data) {
  const details = $("ssh-key-details");
  if (!details) return;
  clear(details);
  const ssh = data?.ssh || {};
  const entries = [
    ["Source", ssh.source === "system_user" ? `System user ${ssh.system_user || "—"}` : "Sextant-managed key"],
    ["Effective key", ssh.effective_key_path || "—"],
    ["Key present", ssh.key_present ? "yes" : "no"],
    ["Key readable", ssh.key_readable ? "yes" : "no"],
    ["Fingerprint", ssh.key_fingerprint || "not available"],
    ["SSH config", ssh.config_path || "not set"],
    ["Remote install", ssh.install_enabled ? "enabled" : "disabled"],
  ];
  for (const [key, value] of entries) {
    details.append(h("dt", {}, key), h("dd", {}, value));
  }
  if (ssh.public_key) {
    details.append(
      h("dt", {}, "Public key"),
      h("dd", { class: "mono", style: "word-break:break-all" }, ssh.public_key),
    );
  }
}

function syncSshFields() {
  const form = $("ssh-settings-form");
  if (!form) return;
  const system = form.elements.source.value === "system_user";
  const managed = $("ssh-managed-fields");
  const systemFields = $("ssh-system-fields");
  if (managed) managed.hidden = system;
  if (systemFields) systemFields.hidden = !system;
}

function settingsFormError(id, error) {
  setText(id, error?.message || String(error || ""));
}

async function saveUserProfile(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setText("user-profile-error", "");
  try {
    const data = await api("/api/me", {
      method: "PATCH",
      body: {
        display_name: String(form.elements.display_name.value || "").trim(),
        email: String(form.elements.email.value || "").trim(),
      },
    });
    state.user = { ...state.user, ...data.user };
    updateChrome();
    renderSettings();
    toast("Profile updated.");
  } catch (error) {
    settingsFormError("user-profile-error", error);
  }
}

async function saveServerSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setText("server-settings-error", "");
  const button = form.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  try {
    const data = await api("/api/system/settings", {
      method: "PATCH",
      body: {
        server: {
          public_url: String(form.elements.public_url.value || "").trim(),
          allowed_hosts: splitList(form.elements.allowed_hosts.value),
          session_hours: Number(form.elements.session_hours.value || 12),
          offline_after_seconds: Number(form.elements.offline_after_seconds.value || 90),
          enrollment_ttl_minutes: Number(form.elements.enrollment_ttl_minutes.value || 30),
          cookie_secure: form.elements.cookie_secure.checked,
          trust_proxy: form.elements.trust_proxy.checked,
        },
      },
    });
    state.systemSettings = data;
    renderSettings();
    const restart = data.meta?.restart_required?.length
      ? " Restart the server to apply the host/secure-cookie changes."
      : "";
    toast(`Server settings saved.${restart}`, restart ? "warn" : "ok");
  } catch (error) {
    settingsFormError("server-settings-error", error);
  } finally {
    if (button) button.disabled = false;
  }
}

function domainPayloadFromForm(form) {
  const values = new FormData(form);
  return {
    enabled: form.elements.enabled.checked,
    domain: String(values.get("domain") || "").trim(),
    server: String(values.get("server") || "").trim(),
    port: Number(values.get("port") || 389),
    use_ssl: form.elements.use_ssl.checked,
    base_dn: String(values.get("base_dn") || "").trim(),
    bind_dn: String(values.get("bind_dn") || "").trim(),
    user_filter: String(values.get("user_filter") || "").trim(),
    username_attr: String(values.get("username_attr") || "").trim(),
    display_name_attr: String(values.get("display_name_attr") || "").trim(),
    email_attr: String(values.get("email_attr") || "").trim(),
    group_attr: String(values.get("group_attr") || "").trim(),
    admin_groups: splitList(values.get("admin_groups")),
    operator_groups: splitList(values.get("operator_groups")),
    viewer_groups: splitList(values.get("viewer_groups")),
    default_role: String(values.get("default_role") || "viewer"),
    ca_cert: String(values.get("ca_cert") || "").trim(),
    timeout: Number(values.get("timeout") || 8),
    clear_bind_password: form.elements.clear_bind_password.checked,
  };
}

async function saveDomainSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setText("domain-settings-error", "");
  const button = form.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  try {
    const domain = domainPayloadFromForm(form);
    const password = String(form.elements.bind_password.value || "");
    if (password) domain.bind_password = password;
    const data = await api("/api/system/settings", {
      method: "PATCH",
      body: { domain },
    });
    state.systemSettings = data;
    renderSettings();
    await loadSettingsPage();
    toast("Domain settings saved. Directory login uses them immediately.");
  } catch (error) {
    settingsFormError("domain-settings-error", error);
  } finally {
    if (button) button.disabled = false;
  }
}

async function testDomainSettings() {
  const form = $("domain-settings-form");
  if (!form) return;
  const result = $("domain-test-result");
  setText("domain-settings-error", "");
  if (result) {
    result.hidden = false;
    result.textContent = "Testing directory connection…";
  }
  try {
    const domain = domainPayloadFromForm(form);
    const password = String(form.elements.bind_password.value || "");
    if (password) domain.bind_password = password;
    const data = await api("/api/system/ldap/test", {
      method: "POST",
      body: {
        domain,
        username: String($("domain-test-username")?.value || "").trim(),
      },
    });
    const lines = [
      `Bound to ${data.server}:${data.port}.`,
      `Filter: ${data.filter}`,
      `${data.entries} matching entr${data.entries === 1 ? "y" : "ies"}.`,
    ];
    if (data.sample && Object.keys(data.sample).length) {
      lines.push("Sample attributes are shown below.");
    }
    if (result) {
      clear(result);
      result.append(h("strong", {}, "Directory test succeeded"), h("p", {}, lines.join(" ")));
      for (const [dn, attrs] of Object.entries(data.sample || {})) {
        result.append(h("p", { class: "mono" }, dn));
        result.append(h("pre", { class: "mono" }, JSON.stringify(attrs, null, 2)));
      }
    }
    toast("Directory test succeeded.");
  } catch (error) {
    if (result) {
      result.hidden = false;
      result.textContent = error.message;
    }
    settingsFormError("domain-settings-error", error);
  }
}

async function saveSshSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setText("ssh-settings-error", "");
  const button = form.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  try {
    const source = form.elements.source.value;
    const ssh = {
      source,
      system_user: String(form.elements.system_user.value || "").trim(),
      key_path:
        source === "managed"
          ? String(form.elements.key_path.value || "").trim()
          : String(form.elements.key_path_override.value || "").trim(),
      config_path: String(form.elements.config_path.value || "").trim(),
      install_enabled: form.elements.install_enabled.checked,
      clear_key: form.elements.clear_key.checked,
    };
    const privateKey = String(form.elements.private_key.value || "");
    if (source === "managed" && privateKey) ssh.private_key = privateKey;
    const data = await api("/api/system/settings", {
      method: "PATCH",
      body: { ssh },
    });
    state.systemSettings = data;
    renderSettings();
    toast(
      data.ssh?.key_present ? "SSH settings saved and key loaded." : "SSH settings saved.",
      data.ssh?.key_present ? "ok" : "warn",
    );
  } catch (error) {
    settingsFormError("ssh-settings-error", error);
  } finally {
    if (button) button.disabled = false;
  }
}

async function testSshSettings() {
  const form = $("ssh-settings-form");
  if (!form) return;
  setText("ssh-settings-error", "");
  try {
    const source = form.elements.source.value;
    const data = await api("/api/system/ssh/test", {
      method: "POST",
      body: {
        ssh: {
          source,
          system_user: String(form.elements.system_user.value || "").trim(),
          key_path:
            source === "managed"
              ? String(form.elements.key_path.value || "").trim()
              : String(form.elements.key_path_override.value || "").trim(),
          config_path: String(form.elements.config_path.value || "").trim(),
          install_enabled: form.elements.install_enabled.checked,
        },
      },
    });
    renderSshKeyDetails({ ssh: data });
    toast(
      data.present
        ? `Key found: ${data.fingerprint || data.effective_key_path}`
        : "No key found at the effective path.",
      data.present ? "ok" : "warn",
    );
  } catch (error) {
    settingsFormError("ssh-settings-error", error);
  }
}

async function restartForSettings() {
  const confirmed = await confirmAction(
    "Restart Sextant",
    "Restart the server now? Open sessions disconnect briefly and the page reloads.",
    "Restart server",
  );
  if (!confirmed) return;
  try {
    await api("/api/system/restart", { method: "POST", body: {} });
    toast("Restart requested. Reloading…", "warn");
    setTimeout(() => window.location.reload(), 12000);
  } catch (error) {
    settingsFormError("server-settings-error", error);
  }
}

function domainJoinLog(text) {
  const log = $("domain-join-log");
  if (log) log.textContent = text;
}

async function openDomainJoinDialog(agent) {
  if (!state.permissions.admin) {
    toast("Administrator access required.", "error");
    return;
  }
  if (!state.systemSettings) {
    try {
      state.systemSettings = await api("/api/system/settings");
    } catch (_error) {
      // The form still works without the domain prefill.
    }
  }
  state.domainJoin = { agent, commandId: null };
  renderDomainJoinDialog();
  const dialog = $("domain-join-dialog");
  if (dialog) dialog.showModal();
}

function closeDomainJoinDialog() {
  if (state.domainJoinTimer) {
    clearTimeout(state.domainJoinTimer);
    state.domainJoinTimer = null;
  }
  const dialog = $("domain-join-dialog");
  if (dialog?.open) dialog.close();
  state.domainJoin = null;
  setText("domain-join-error", "");
  clear($("domain-join-body"));
  clear($("domain-join-actions"));
}

function renderDomainJoinDialog() {
  const data = state.domainJoin;
  const body = $("domain-join-body");
  const actions = $("domain-join-actions");
  if (!data || !body || !actions) return;
  const agent = data.agent;
  const windows = String(agent.os || "").toLowerCase() === "windows";
  clear(body);
  clear(actions);
  setText("domain-join-error", "");
  const defaultDomain = state.systemSettings?.domain?.domain || "";
  const shortName = String(agent.hostname || "").split(".")[0].slice(0, 15);
  const form = h(
    "form",
    { class: "form-stack", id: "domain-join-form" },
    h(
      "p",
      { class: "form-hint" },
      `Join ${agent.name || agent.hostname} (${windows ? "Windows" : "Linux"}) to an AD domain. The password is sent only to this agent and is never stored or logged.`,
    ),
    h(
      "div",
      { class: "form-grid" },
      h(
        "label",
        { class: "field" },
        h("span", {}, "Domain"),
        h("input", { name: "domain", maxlength: "255", required: true, value: defaultDomain, placeholder: "corp.example.com" }),
      ),
      h(
        "label",
        { class: "field" },
        h("span", {}, "Domain user"),
        h("input", { name: "username", maxlength: "255", required: true, placeholder: windows ? "CORP\\svc-join" : "svc-join@corp.example.com" }),
      ),
    ),
    h(
      "label",
      { class: "field" },
      h("span", {}, "Password"),
      h("input", { name: "password", type: "password", maxlength: "4096", required: true, autocomplete: "new-password" }),
    ),
    h(
      "div",
      { class: "form-grid" },
      h(
        "label",
        { class: "field" },
        h("span", {}, "Computer name (optional)"),
        h("input", { name: "computer_name", maxlength: "15", value: shortName }),
      ),
      h(
        "label",
        { class: "field" },
        h("span", {}, "OU (optional)"),
        h("input", { name: "ou", maxlength: "1024", placeholder: "OU=Workstations,DC=corp,DC=example,DC=com" }),
      ),
    ),
    h(
      "label",
      { class: "field" },
      h("span", {}, windows ? "Domain controller (optional)" : "Domain controller (optional, adcli)"),
      h("input", { name: "server", maxlength: "255", disabled: windows, placeholder: windows ? "discovered automatically" : "dc1.corp.example.com" }),
    ),
    windows
      ? null
      : h(
          "label",
          { class: "field" },
          h("span", {}, "Linux method"),
          h(
            "select",
            { name: "method" },
            h("option", { value: "auto" }, "Auto (realmd, then adcli)"),
            h("option", { value: "realm" }, "realmd"),
            h("option", { value: "adcli" }, "adcli"),
          ),
        ),
    windows
      ? null
      : h(
          "label",
          { class: "checkbox-row" },
          h("input", { type: "checkbox", name: "install_prerequisites" }),
          h("span", {}, "Install realmd, SSSD, adcli, and Kerberos packages if missing"),
        ),
    h(
      "label",
      { class: "checkbox-row" },
      h("input", { type: "checkbox", name: "reboot" }),
      h("span", {}, windows ? "Reboot after a successful join" : "Reboot after a successful join (usually not required)"),
    ),
    h(
      "p",
      { class: "form-hint" },
      windows
        ? "Add-Computer joins without restarting first. Check the reboot box or use Reboot later to finish applying membership."
        : "realmd configures SSSD/PAM. adcli creates the computer account but may need SSSD tuning. Check status after the join.",
    ),
    h("pre", { class: "terminal", id: "domain-join-log", "aria-live": "polite" }, "No command queued yet."),
  );
  body.append(form);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitDomainJoin(form);
  });
  actions.append(
    button("Cancel", "button-ghost", closeDomainJoinDialog),
    button("Check status", "button-secondary", () => checkAgentDomainStatus(agent)),
    h("button", { class: "button button-primary", type: "submit", form: "domain-join-form" }, "Join domain"),
  );
  const first = form.querySelector("input, select");
  if (first) setTimeout(() => first.focus(), 30);
}

async function submitDomainJoin(form) {
  const data = state.domainJoin;
  if (!data) return;
  const values = new FormData(form);
  const password = String(values.get("password") || "");
  if (!password) {
    setText("domain-join-error", "Enter the domain password.");
    return;
  }
  setText("domain-join-error", "");
  const payload = {
    domain: String(values.get("domain") || "").trim(),
    username: String(values.get("username") || "").trim(),
    password,
    ou: String(values.get("ou") || "").trim(),
    computer_name: String(values.get("computer_name") || "").trim(),
    server: String(values.get("server") || "").trim(),
    method: String(values.get("method") || "auto"),
    install_prerequisites: form.elements.install_prerequisites?.checked === true,
    reboot: form.elements.reboot?.checked === true,
  };
  try {
    const result = await api(`/api/agents/${data.agent.id}/domain/join`, {
      method: "POST",
      body: payload,
    });
    form.elements.password.value = "";
    data.commandId = result.command?.id || "";
    domainJoinLog("Join command queued for the agent's next heartbeat.");
    toast("Domain join queued.");
    watchAgentDomainCommand(data.commandId);
  } catch (error) {
    setText("domain-join-error", error.message);
  }
}

async function checkAgentDomainStatus(agent) {
  setText("domain-join-error", "");
  try {
    const result = await api(`/api/agents/${agent.id}/domain/status`, {
      method: "POST",
      body: {},
    });
    domainJoinLog("Status command queued for the agent's next heartbeat.");
    watchAgentDomainCommand(result.command?.id || "");
  } catch (error) {
    setText("domain-join-error", error.message);
  }
}

function watchAgentDomainCommand(commandId) {
  if (!commandId) return;
  if (state.domainJoinTimer) clearTimeout(state.domainJoinTimer);
  const tick = async () => {
    try {
      const data = await api(`/api/commands/${encodeURIComponent(commandId)}`);
      const command = data.command || {};
      const parts = [
        `Status: ${command.status || "unknown"}`,
        command.stdout ? String(command.stdout).trim() : "",
        command.stderr ? String(command.stderr).trim() : "",
        command.error ? `Error: ${command.error}` : "",
      ].filter(Boolean);
      domainJoinLog(parts.join("\n\n") || "Waiting for the agent…");
      if (["succeeded", "failed", "cancelled"].includes(command.status)) {
        toast(
          command.status === "succeeded"
            ? "Domain join command completed."
            : "Domain join command failed.",
          command.status === "succeeded" ? "ok" : "error",
        );
        return;
      }
    } catch (error) {
      domainJoinLog(error.message);
    }
    state.domainJoinTimer = setTimeout(tick, 1500);
  };
  tick();
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
  renderUpdatesPanel();
  if (state.permissions.admin && !state.updates) void checkForUpdates();
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

function discoveryReady() {
  const settings = state.discovery.settings;
  return Boolean(
    settings &&
      settings.enabled &&
      settings.acknowledged &&
      Array.isArray(settings.cidrs) &&
      settings.cidrs.length,
  );
}

function discoveryDisabledMessage(settings) {
  if (!settings) return "Discovery settings are unavailable.";
  if (!settings.enabled) {
    return "Network discovery is off. An administrator must set SEXTANT_DISCOVERY_ENABLED=1, add private ranges to SEXTANT_DISCOVERY_CIDRS, and record the authorization acknowledgement.";
  }
  if (!settings.acknowledged) {
    return "Discovery is enabled and allowlisted, but no authorization acknowledgement is stored. An administrator must confirm they are authorized to scan these networks before any scan can run.";
  }
  if (!Array.isArray(settings.cidrs) || !settings.cidrs.length) {
    return "Discovery is enabled but SEXTANT_DISCOVERY_CIDRS does not contain any valid private ranges. Only allowlisted RFC1918 or ULA ranges can be scanned.";
  }
  const errors = settings.cidr_errors || [];
  return errors.length
    ? `Discovery is disabled by configuration: ${errors.join(" ")}`
    : "Discovery is not ready.";
}

function discoveryStat(label, value, detail, className = "") {
  return h(
    "article",
    { class: `stat-card ${className}` },
    h("span", { class: "stat-label" }, label),
    h("strong", { class: "stat-value" }, String(value)),
    h("span", { class: "stat-detail" }, detail),
  );
}

function renderDiscoveryTabs() {
  const tabs = $("discovery-tabs");
  const ready = discoveryReady();
  if (tabs) tabs.hidden = !ready;
  const admin = Boolean(state.permissions.admin);
  const allowed = new Set(["discover", "topology"]);
  if (admin) {
    for (const kind of ["imports", "snmp", "unifi", "routeros", "opnsense", "proxmox", "esxi"]) {
      allowed.add(kind);
    }
  }
  let active = state.discovery.tab || "discover";
  if (!allowed.has(active)) active = "discover";
  state.discovery.tab = active;
  document.querySelectorAll("#discovery-tabs .tab").forEach((tab) => {
    const key = tab.dataset.discoveryTab;
    tab.hidden = !allowed.has(key);
    tab.classList.toggle("is-active", key === active);
    tab.setAttribute("aria-pressed", key === active ? "true" : "false");
  });
  document.querySelectorAll("[data-discovery-panel]").forEach((panel) => {
    panel.hidden = !ready || panel.dataset.discoveryPanel !== active;
  });
}

function renderDiscovery() {
  const discovery = state.discovery;
  const settings = discovery.settings;
  const ready = discoveryReady();
  const disabled = $("discovery-disabled");
  const ackPanel = $("discovery-ack-panel");
  const ackEligible = Boolean(
    settings && settings.enabled && settings.acknowledged === false && state.permissions.admin,
  );

  if (disabled) {
    disabled.hidden = ready;
    disabled.textContent = ready ? "" : discoveryDisabledMessage(settings);
  }
  if (ackPanel) ackPanel.hidden = !ackEligible;
  if (settings && settings.enabled && settings.acknowledged === false) {
    setText(
      "discovery-ack-message",
      "Discovery will not send any probes until this acknowledgement is recorded. It is audited with your username and the current time.",
    );
  }

  for (const id of ["discovery-summary", "discovery-scan-panel", "discovery-devices-panel"]) {
    const node = $(id);
    if (node) node.hidden = !ready;
  }
  if (!ready) {
    setText("discovery-count", "");
    renderDiscoveryTabs();
    return;
  }
  renderDiscoverySummary();
  renderDiscoveryFilters();
  renderDiscoveryMethods();
  renderDiscoverySources();
  renderDiscoveryScanHistory();
  renderDiscoveryImportHistory();
  renderDiscoveryTable();
  renderDiscoveryTabs();
  if (state.permissions.admin) loadDiscoveryIntegrations(true);
}

function renderDiscoverySummary() {
  const container = $("discovery-summary");
  if (!container) return;
  const summary = state.discovery.summary || {
    total: 0,
    new: 0,
    no_agent: 0,
    states: {},
    types: [],
    segments: [],
  };
  clear(container);
  container.append(
    discoveryStat("Discovered devices", summary.total, `${summary.no_agent || 0} without an enrolled agent`),
    discoveryStat("New", summary.new || 0, "Seen once, awaiting review", "stat-warn"),
    discoveryStat("Known", summary.states?.known || 0, `${summary.states?.ignored || 0} ignored`, "stat-good"),
    discoveryStat(
      "Changed",
      summary.changed || 0,
      "IP, MAC, or hostname moved",
      summary.changed ? "stat-warn" : "",
    ),
    discoveryStat(
      "Exposure",
      summary.exposure || 0,
      "Admin ports open, no agent",
      summary.exposure ? "stat-warn" : "stat-good",
    ),
    discoveryStat(
      "Conflicts",
      summary.conflicts || 0,
      "Identity conflicts",
      summary.conflicts ? "stat-warn" : "",
    ),
    discoveryStat(
      "Segments",
      Array.isArray(summary.segments) ? summary.segments.length : 0,
      `${summary.managed || 0} managed · ${summary.unmanaged || 0} unmanaged`,
      "stat-info",
    ),
  );
  renderDiscoverySegments();
}

function renderDiscoverySegments() {
  const container = $("discovery-segment-list");
  const panel = $("discovery-segments-panel");
  if (!container || !panel) return;
  const segments = state.discovery.summary?.segments || [];
  panel.hidden = false;
  clear(container);
  if (!segments.length) {
    container.append(
      h("div", { class: "empty-inline" }, "No segments yet. Run a scan to map the allowlisted networks."),
    );
    return;
  }
  for (const segment of segments) {
    container.append(
      h(
        "div",
        { class: "list-row" },
        h(
          "div",
          { class: "list-row-main" },
          h("strong", {}, segment.segment || "unsegmented"),
          h(
            "small",
            {},
            `${segment.count} devices · ${segment.new} new · ${segment.managed} managed · ${segment.unmanaged} unmanaged`,
          ),
          h(
            "small",
            {},
            segment.gateway_hint
              ? `Gateway hint: ${segment.gateway_hint} (${segment.gateway_source})`
              : "Gateway hint: none",
          ),
        ),
        h(
          "div",
          { class: "list-row-actions" },
          segment.exposure
            ? statusPill(`${segment.exposure} exposed`, "warning")
            : statusPill("no exposure", "online"),
        ),
      ),
    );
  }
}

function fillDiscoverySelect(id, values, selected, allLabel) {
  const select = $(id);
  if (!select) return;
  clear(select);
  select.append(h("option", { value: "" }, allLabel));
  for (const value of values) {
    const label = value.label || value;
    select.append(h("option", { value: value.value || value }, label));
  }
  select.value = selected || "";
}

function renderDiscoveryFilters() {
  const summary = state.discovery.summary || {};
  const filters = state.discovery.filters;
  fillDiscoverySelect(
    "discovery-filter-type",
    (summary.types || []).map((item) => ({ value: item.type, label: `${item.type} (${item.count})` })),
    filters.type,
    "All types",
  );
  fillDiscoverySelect(
    "discovery-filter-segment",
    (summary.segments || []).map((item) => ({
      value: item.segment || "unsegmented",
      label: `${item.segment || "unsegmented"} (${item.count})`,
    })),
    filters.segment,
    "All segments",
  );
  fillDiscoverySelect(
    "discovery-filter-vendor",
    (summary.vendors || []).map((item) => ({ value: item.vendor, label: `${item.vendor} (${item.count})` })),
    filters.vendor,
    "All vendors",
  );
  const stateSelect = $("discovery-filter-state");
  if (stateSelect) stateSelect.value = filters.state || "";
  const noAgent = $("discovery-filter-no-agent");
  if (noAgent) noAgent.checked = Boolean(filters.no_agent);
  const adminPorts = $("discovery-filter-admin-ports");
  if (adminPorts) adminPorts.checked = Boolean(filters.admin_ports);
  const exposure = $("discovery-filter-exposure");
  if (exposure) exposure.checked = Boolean(filters.exposure);
}

function renderDiscoveryImportHistory() {
  const container = $("discovery-import-history");
  if (!container) return;
  const imports = (state.discovery.scans || []).filter((scan) =>
    String(scan.source || "").startsWith("import:"),
  );
  clear(container);
  if (!imports.length) {
    container.append(h("div", { class: "empty-inline" }, "No imports yet."));
    return;
  }
  for (const scan of imports.slice(0, 10)) {
    container.append(
      h(
        "div",
        { class: "list-row" },
        h(
          "div",
          { class: "list-row-main" },
          h("strong", {}, String(scan.source || "import").replace("import:", "")),
          h(
            "small",
            {},
            `${localTime(scan.started_at)} · ${scan.hosts_found} devices · ${scan.status}`,
          ),
          scan.error ? h("small", {}, scan.error) : null,
        ),
        statusPill(
          scan.status,
          scan.status === "succeeded"
            ? "online"
            : scan.status === "failed"
              ? "critical"
              : "warning",
        ),
      ),
    );
  }
}

function discoveryImportContent() {
  return String($("discovery-import-content")?.value || "");
}

function renderDiscoveryImportPreview(preview) {
  const container = $("discovery-import-preview-result");
  if (!container) return;
  clear(container);
  container.hidden = false;
  container.append(
    h("p", { class: "eyebrow" }, "Preview"),
    h(
      "p",
      {},
      `${preview.devices} parsed · ${preview.new} new · ${preview.existing} existing · ${preview.skipped} skipped${preview.truncated ? " · truncated" : ""}`,
    ),
    h(
      "div",
      { class: "list-stack" },
      ...(preview.sample || []).map((device) =>
        h(
          "div",
          { class: "list-row" },
          h(
            "div",
            { class: "list-row-main" },
            h("strong", {}, device.hostname || device.ip),
            h("small", {}, `${device.ip} · ${device.mac || "no MAC"} · ${device.vendor || "unknown vendor"}`),
          ),
          h("span", { class: "form-hint" }, (device.open_ports || []).join(", ") || "no ports"),
        ),
      ),
    ),
  );
}

function clearDiscoveryImport() {
  const content = $("discovery-import-content");
  if (content) content.value = "";
  const file = $("discovery-import-file");
  if (file) file.value = "";
  const preview = $("discovery-import-preview-result");
  if (preview) {
    preview.hidden = true;
    clear(preview);
  }
  setText("discovery-import-status", "");
}

async function previewDiscoveryImport() {
  const content = discoveryImportContent();
  setText("discovery-import-status", "");
  if (!content.trim()) {
    setText("discovery-import-status", "Paste or choose import content first.");
    return;
  }
  try {
    const preview = await api("/api/discovery/import", {
      method: "POST",
      body: {
        format: $("discovery-import-format")?.value || "nmap-xml",
        content,
        filename: $("discovery-import-file")?.files?.[0]?.name || "",
        dry_run: true,
      },
    });
    renderDiscoveryImportPreview(preview);
    setText("discovery-import-status", "Preview ready. Confirm to merge.");
  } catch (error) {
    setText("discovery-import-status", error.message);
  }
}

async function submitDiscoveryImport(event) {
  event.preventDefault();
  const content = discoveryImportContent();
  setText("discovery-import-status", "");
  if (!content.trim()) {
    setText("discovery-import-status", "Paste or choose import content first.");
    return;
  }
  try {
    const result = await api("/api/discovery/import", {
      method: "POST",
      body: {
        format: $("discovery-import-format")?.value || "nmap-xml",
        content,
        filename: $("discovery-import-file")?.files?.[0]?.name || "",
      },
    });
    setText(
      "discovery-import-status",
      `Imported ${result.devices_merged} devices (${result.skipped} skipped${result.truncated ? ", truncated" : ""}).`,
    );
    toast("Discovery import completed.");
    await Promise.all([
      loadDiscoveryDevices(),
      loadDiscoveryScans(),
      loadDiscoveryConflicts(),
    ]);
    renderDiscoveryImportHistory();
  } catch (error) {
    setText("discovery-import-status", error.message);
  }
}

function integrationSecretInput(kind, field, configured) {
  return h("input", {
    type: "password",
    name: `secret_${field}`,
    dataset: { secret: field },
    maxlength: "4096",
    autocomplete: "new-password",
    placeholder: configured ? "configured — enter to replace" : "not configured",
  });
}

function integrationField(label, input) {
  return h("label", { class: "field" }, h("span", {}, label), input);
}

function renderDiscoveryIntegrations() {
  const containers = new Map();
  document.querySelectorAll("[data-integration-kind]").forEach((el) => {
    containers.set(el.dataset.integrationKind, el);
    clear(el);
  });
  if (!containers.size) return;
  const integrations = state.discovery.integrations || [];
  if (!integrations.length) {
    for (const container of containers.values()) {
      container.append(h("div", { class: "empty-inline" }, "Loading integrations…"));
    }
    return;
  }
  const rendered = new Set();
  for (const integration of integrations) {
    const kind = integration.kind;
    const container = containers.get(kind);
    if (!container) continue;
    rendered.add(kind);
    const config = integration.config || {};
    const secretConfigured = Boolean(integration.secret_configured);
    const fields = [];
    if (kind === "snmp") {
      fields.push(
        integrationField(
          "Targets (private IP/CIDR, one per line)",
          h("textarea", { name: "targets", rows: "3" }, (config.targets || []).join("\n")),
        ),
        h("label", { class: "checkbox-row" }, h("input", { type: "checkbox", name: "use_discovered", checked: config.use_discovered !== false }), h("span", {}, "Include discovered devices")),
        h("div", { class: "form-grid" },
          integrationField("Timeout ms", h("input", { name: "timeout_ms", type: "number", min: "100", max: "5000", value: String(config.timeout_ms || 600) })),
          integrationField("Retries", h("input", { name: "retries", type: "number", min: "0", max: "3", value: String(config.retries ?? 1) })),
          integrationField("Max targets", h("input", { name: "max_targets", type: "number", min: "1", max: "1024", value: String(config.max_targets || 256) })),
        ),
        integrationField("Community (write-only)", integrationSecretInput(kind, "community", secretConfigured)),
      );
    } else {
      fields.push(
        integrationField("Base URL", h("input", { name: "base_url", maxlength: "1024", value: config.base_url || "", placeholder: "https://vendor.example" })),
      );
      if (kind === "unifi") {
        fields.push(
          integrationField("Site", h("input", { name: "site", maxlength: "128", value: config.site || "default" })),
          integrationField("Username (password auth)", h("input", { name: "username", maxlength: "128", value: config.username || "" })),
          integrationField("API token (write-only)", integrationSecretInput(kind, "api_token", secretConfigured)),
          integrationField("Password (write-only)", integrationSecretInput(kind, "password", secretConfigured)),
        );
      } else if (kind === "routeros") {
        fields.push(
          integrationField("Username", h("input", { name: "username", maxlength: "128", value: config.username || "" })),
          integrationField("Password (write-only)", integrationSecretInput(kind, "password", secretConfigured)),
        );
      } else if (kind === "opnsense") {
        fields.push(
          integrationField("API key (write-only)", integrationSecretInput(kind, "api_key", secretConfigured)),
          integrationField("API secret (write-only)", integrationSecretInput(kind, "api_secret", secretConfigured)),
        );
      } else if (kind === "proxmox") {
        fields.push(
          integrationField("Token name", h("input", { name: "token_name", maxlength: "128", value: config.token_name || "" })),
          integrationField("Token secret (write-only)", integrationSecretInput(kind, "token_secret", secretConfigured)),
          integrationField("Username (password auth)", h("input", { name: "username", maxlength: "128", value: config.username || "" })),
          integrationField("Password (write-only)", integrationSecretInput(kind, "password", secretConfigured)),
        );
      } else {
        fields.push(
          integrationField("Username", h("input", { name: "username", maxlength: "128", value: config.username || "" })),
          integrationField("Password (write-only)", integrationSecretInput(kind, "password", secretConfigured)),
        );
      }
      fields.push(
        h("div", { class: "form-grid" },
          integrationField("Timeout ms", h("input", { name: "timeout_ms", type: "number", min: "100", max: "30000", value: String(config.timeout_ms || 5000) })),
          integrationField("Max response bytes", h("input", { name: "max_response_bytes", type: "number", min: "1024", max: "4000000", value: String(config.max_response_bytes || 1000000) })),
        ),
        h("label", { class: "checkbox-row" }, h("input", { type: "checkbox", name: "verify_tls", checked: config.verify_tls !== false }), h("span", {}, "Verify TLS certificate")),
      );
    }
    fields.push(
      h("label", { class: "checkbox-row" }, h("input", { type: "checkbox", name: "clear_secrets" }), h("span", {}, "Clear stored secrets")),
      h("p", { class: "form-error", role: "alert", dataset: { integrationError: kind } }),
    );
    const form = h(
      "form",
      { class: "form-stack", dataset: { integration: kind } },
      h("label", { class: "checkbox-row" }, h("input", { type: "checkbox", name: "enabled", checked: Boolean(integration.enabled) }), h("span", {}, "Enabled")),
      ...fields,
    );
    const actions = h(
      "div",
      { class: "section-toolbar" },
      h("button", { class: "button button-primary button-small", type: "button", onClick: () => saveDiscoveryIntegration(kind, form, "") }, "Save"),
      h("button", { class: "button button-secondary button-small", type: "button", onClick: () => saveDiscoveryIntegration(kind, form, "test"), disabled: !integration.live_supported }, "Test connection"),
      h("button", { class: "button button-secondary button-small", type: "button", onClick: () => saveDiscoveryIntegration(kind, form, "poll"), disabled: !integration.live_supported || !integration.enabled }, "Poll now"),
    );
    const statusBits = [];
    if (integration.fixture_only) statusBits.push(statusPill("fixture-only", "warning"));
    if (!integration.verified) statusBits.push(statusPill("unverified live API", "warning"));
    if (secretConfigured) statusBits.push(statusPill("secret configured", "online"));
    const sectionNode = h(
      "section",
      { class: "callout" },
      h(
        "div",
        { class: "section-head" },
        h("div", {}, h("h3", {}, integration.label), h("p", { class: "form-hint" }, kind)),
        h("div", { class: "tag-row" }, ...statusBits),
      ),
      integration.fixture_only
        ? h("p", { class: "form-hint" }, "This connector is fixture/export-only; a live ESXi connector is unverified and disabled.")
        : null,
      integration.last_error ? h("p", { class: "form-hint" }, `Last error: ${integration.last_error}`) : null,
      form,
      actions,
    );
    container.append(sectionNode);
  }
  for (const [kind, container] of containers) {
    if (!rendered.has(kind)) {
      container.append(
        h("div", { class: "empty-inline" }, "This connector is not available in this build."),
      );
    }
  }
}

async function loadDiscoveryIntegrations(silent = false) {
  if (!state.permissions.admin) return;
  if (silent && state.discovery.integrations.length) {
    renderDiscoveryIntegrations();
    return;
  }
  try {
    const data = await api("/api/discovery/integrations");
    state.discovery.integrations = data.integrations || [];
    renderDiscoveryIntegrations();
    setText(
      "discovery-integrations-status",
      data.snmp?.backend ? `SNMP backend: ${data.snmp.backend}` : "",
    );
  } catch (error) {
    if (!silent) setText("discovery-integrations-status", error.message);
  }
}

function integrationConfigFromForm(kind, form) {
  const values = new FormData(form);
  if (kind === "snmp") {
    return {
      targets: String(values.get("targets") || "")
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean),
      use_discovered: form.elements.use_discovered?.checked !== false,
      timeout_ms: Number(values.get("timeout_ms") || 600),
      retries: Number(values.get("retries") || 0),
      max_targets: Number(values.get("max_targets") || 256),
    };
  }
  const config = {
    base_url: String(values.get("base_url") || "").trim(),
    timeout_ms: Number(values.get("timeout_ms") || 5000),
    max_response_bytes: Number(values.get("max_response_bytes") || 1000000),
    verify_tls: form.elements.verify_tls?.checked !== false,
  };
  for (const field of ["site", "username", "token_name", "auth_method"]) {
    if (values.get(field)) config[field] = String(values.get(field));
  }
  return config;
}

async function saveDiscoveryIntegration(kind, form, action) {
  const errorNode = form.querySelector(`[data-integration-error="${kind}"]`);
  if (errorNode) errorNode.textContent = "";
  const secrets = {};
  form.querySelectorAll("input[data-secret]").forEach((input) => {
    if (input.value) secrets[input.dataset.secret] = input.value;
  });
  const payload = {
    enabled: form.elements.enabled?.checked === true,
    config: integrationConfigFromForm(kind, form),
    secrets: Object.keys(secrets).length ? secrets : null,
    clear_secrets: form.elements.clear_secrets?.checked === true,
  };
  try {
    await api(`/api/discovery/integrations/${encodeURIComponent(kind)}`, {
      method: "PATCH",
      body: payload,
    });
    if (action === "test") {
      const result = await api(`/api/discovery/integrations/${encodeURIComponent(kind)}/test`, {
        method: "POST",
        body: {},
      });
      toast(result.detail || "Integration test succeeded.");
    } else if (action === "poll") {
      const result = await api(`/api/discovery/integrations/${encodeURIComponent(kind)}/poll`, {
        method: "POST",
        body: {},
      });
      toast(`Integration poll merged ${result.devices_merged} devices.`);
      await Promise.all([loadDiscoveryDevices(), loadDiscoveryScans(), loadDiscoveryConflicts()]);
    } else {
      toast("Integration settings saved.");
    }
    await loadDiscoveryIntegrations();
  } catch (error) {
    if (errorNode) errorNode.textContent = error.message;
    else toast(error.message, "error");
  }
}

function renderDiscoveryMethods() {
  const container = $("discovery-methods");
  if (!container) return;
  const settings = state.discovery.settings || { methods: [] };
  const configured = Array.isArray(settings.methods) ? settings.methods : [];
  if (!state.discovery.methods.length) {
    state.discovery.methods = configured.filter((method) => method !== "tcp" || settings.active_probes);
  }
  const labels = {
    neigh: "Neighbour table",
    dns: "Reverse DNS",
    oui: "OUI vendor lookup",
    ssdp: "Passive SSDP",
    tcp: "TCP fingerprint",
  };
  clear(container);
  for (const method of configured) {
    const active = method !== "tcp" || settings.active_probes;
    const input = h("input", {
      type: "checkbox",
      value: method,
      checked: state.discovery.methods.includes(method) && active,
      disabled: !active,
    });
    input.addEventListener("change", () => {
      const selected = new Set(state.discovery.methods);
      if (input.checked) selected.add(method);
      else selected.delete(method);
      state.discovery.methods = [...selected];
    });
    container.append(
      h(
        "label",
        { class: "checkbox-row" },
        input,
        h("span", {}, labels[method] || method),
        active ? null : h("small", { class: "form-hint" }, "requires SEXTANT_DISCOVERY_ACTIVE_PROBES=1"),
      ),
    );
  }
  const scope = $("discovery-scan-scope");
  if (scope) {
    const current = scope.value;
    clear(scope);
    for (const cidr of settings.cidrs || []) scope.append(h("option", { value: cidr }, cidr));
    if (current) scope.value = current;
  }
  setText(
    "discovery-scan-hint",
    settings.active_probes
      ? "Active TCP fingerprinting is enabled for the configured v1 port set. Scans are capped, rate-limited, and cancellable."
      : "Passive discovery is enabled. TCP fingerprinting stays off until SEXTANT_DISCOVERY_ACTIVE_PROBES=1.",
  );
}

function discoveryAgentName(agentId) {
  return (
    (state.discovery.agents || []).find((agent) => agent.id === agentId)?.name ||
    (state.dashboard?.agents || []).find((agent) => agent.id === agentId)?.name ||
    agentId
  );
}

function renderDiscoverySources() {
  const select = $("discovery-scan-source");
  if (!select) return;
  const discovery = state.discovery;
  const eligible = discovery.relayEnabled
    ? (discovery.agents || []).filter((agent) => agent.eligible)
    : [];
  clear(select);
  select.append(h("option", { value: "server" }, "This server"));
  for (const agent of eligible) {
    select.append(
      h(
        "option",
        { value: `agent:${agent.id}` },
        `${agent.name}${agent.online ? "" : " (offline)"} · ${agent.ip || agent.hostname || "no address"}${agent.segment ? ` · ${agent.segment}` : ""}`,
      ),
    );
  }
  const values = ["server", ...eligible.map((agent) => `agent:${agent.id}`)];
  if (!values.includes(discovery.source)) discovery.source = "server";
  select.value = discovery.source;

  if (!discovery.relayEnabled) {
    setText(
      "discovery-source-hint",
      "Agent-relayed scans are disabled. Set SEXTANT_DISCOVERY_AGENT_RELAY=1 to let an enrolled Linux agent scan its own VLAN.",
    );
    return;
  }
  if (!eligible.length) {
    const reasons = [...new Set((discovery.agents || []).flatMap((agent) => agent.reasons || []))];
    setText(
      "discovery-source-hint",
      `No agent is currently eligible for relayed discovery.${reasons.length ? ` ${reasons.join(" ")}` : ""} The agent must also have allow_discovery=true locally.`,
    );
    return;
  }
  setText(
    "discovery-source-hint",
    "Each agent scans only its own allowlisted segment and reports back over its outbound heartbeat connection. The local agent flag is a hard ceiling.",
  );
}

function discoveryStateKind(value) {
  return (
    {
      new: "warning",
      changed: "warning",
      conflict: "critical",
      promoted: "online",
      ignored: "offline",
    }[value] || "neutral"
  );
}

function discoveryChangeLabel(kind) {
  return (
    {
      ip: "IP changed",
      mac: "MAC changed",
      hostname: "Hostname changed",
    }[kind] || "Identity changed"
  );
}

function discoveryEffectiveState(device) {
  if (device.conflict || device.identity_conflict) {
    return { label: "conflict", kind: "critical", change: "" };
  }
  const kind = device.change_kind || device.change?.kind || "";
  if (kind) {
    return {
      label: "changed",
      kind: "warning",
      change: discoveryChangeLabel(kind),
    };
  }
  return { label: device.state, kind: discoveryStateKind(device.state), change: "" };
}

function discoveryDeviceName(device) {
  return device.hostname || device.ip || device.mac || device.id;
}

function renderDiscoveryTable() {
  const body = $("discovery-table-body");
  if (!body) return;
  const devices = state.discovery.devices || [];
  clear(body);
  const empty = $("discovery-empty");
  if (empty) {
    empty.hidden = devices.length > 0;
    setText(
      "discovery-empty-text",
      state.discovery.settings?.cidrs?.length
        ? "Run a scan or clear the filters to see discovered devices."
        : "Add an allowlisted CIDR before scanning.",
    );
  }
  setText(
    "discovery-count",
    `${devices.length} shown${state.discovery.summary ? ` · ${state.discovery.summary.total} total` : ""}`,
  );
  for (const device of devices) {
    const agent = (state.dashboard?.agents || []).find((item) => item.id === device.agent_id);
    const ports = (device.open_ports || []).join(", ") || "—";
    const tags = (device.tags || []).map((tag) => h("span", { class: "tag" }, tag));
    const effective = discoveryEffectiveState(device);
    const change = device.change || {};
    const changeDetail =
      change.kind === "ip"
        ? `${change.from} → ${change.to}`
        : change.kind === "mac"
          ? `${change.from || "none"} → ${change.to || "none"}`
          : change.kind === "hostname"
            ? `${change.from} → ${change.to}`
            : "";
    const action = h(
      "button",
      {
        class: "button button-ghost button-small",
        type: "button",
        "aria-label": `Open ${discoveryDeviceName(device)} details`,
        onClick: () => openDiscoveryDevice(device.id),
      },
      "Open",
    );
    body.append(
      h(
        "tr",
        {},
        h(
          "td",
          {},
          statusPill(effective.label, effective.kind),
          effective.change
            ? h("div", { class: "form-hint" }, effective.change)
            : null,
          changeDetail
            ? h("div", { class: "form-hint mono" }, changeDetail)
            : null,
        ),
        h("td", { class: "mono" }, device.ip || "—"),
        h(
          "td",
          {},
          h("div", { class: "mono" }, device.mac || "no MAC"),
          h(
            "div",
            { class: "form-hint" },
            `${device.vendor || "unknown vendor"}${device.mac_randomized ? " · randomized MAC" : ""}`,
          ),
        ),
        h("td", {}, device.hostname || "—"),
        h("td", {}, device.device_type || "unknown", h("div", { class: "form-hint" }, device.os_guess || "")),
        h("td", { class: "mono" }, ports),
        h(
          "td",
          {},
          device.wake_status
            ? statusPill(
                device.wake_status,
                device.wake_status === "woke"
                  ? "online"
                  : device.wake_status === "failed"
                    ? "critical"
                    : "warning",
              )
            : "—",
        ),
        h("td", { title: device.last_seen || "" }, relativeTime(device.last_seen)),
        h("td", {}, agent ? agent.name : device.agent_id ? "linked" : "—"),
        h("td", {}, tags.length ? h("div", { class: "tag-row" }, ...tags) : "—"),
        h("td", {}, action),
      ),
    );
  }
}

function renderDiscoveryScanHistory() {
  const container = $("discovery-scan-history");
  if (!container) return;
  clear(container);
  const scans = state.discovery.scans || [];
  if (!scans.length) {
    container.append(h("div", { class: "empty-inline" }, "No scans yet."));
  }
  const active = scans.find((scan) => scan.status === "queued" || scan.status === "running");
  state.discovery.activeScanId = active ? active.id : null;
  for (const scan of scans.slice(0, 8)) {
    const statusClass =
      scan.status === "succeeded"
        ? "online"
        : scan.status === "failed"
          ? "critical"
          : scan.status === "cancelled"
            ? "offline"
            : "warning";
    const sourceLabel = scan.agent_id
      ? `Agent ${discoveryAgentName(scan.agent_id)}`
      : "Server";
    container.append(
      h(
        "div",
        { class: "list-row" },
        h(
          "div",
          { class: "list-row-main" },
          h("strong", {}, `${(scan.scope || []).join(", ") || "no scope"}`),
          h(
            "small",
            {},
            `${sourceLabel} · ${(scan.methods || []).join(", ")} · ${localTime(scan.started_at)} · ${scan.initiated_by}`,
          ),
          scan.error ? h("small", {}, scan.error) : null,
        ),
        h(
          "div",
          { class: "list-row-actions" },
          statusPill(scan.status, statusClass),
          h("span", { class: "form-hint" }, `${scan.hosts_found} found`),
        ),
      ),
    );
  }
  const progress = $("discovery-scan-progress");
  const bar = $("discovery-progress-bar");
  const cancel = $("discovery-scan-cancel");
  const start = $("discovery-scan-start");
  if (progress) progress.hidden = !active;
  if (cancel) cancel.hidden = !active;
  if (start) start.disabled = Boolean(active);
  if (bar && active) bar.value = String(Math.max(0, Math.min(100, Number(active.progress || 0))));
  if (active) {
    setText(
      "discovery-progress-label",
      `Scanning ${(active.scope || []).join(", ")} · ${active.progress || 0}% · ${active.hosts_scanned || 0} hosts checked`,
    );
  }
  renderDiscoveryImportHistory();
}

async function loadDiscovery() {
  const discovery = state.discovery;
  if (discovery.loading) return;
  discovery.loading = true;
  try {
    discovery.settings = await api("/api/discovery/settings");
    renderDiscovery();
    if (!discoveryReady()) return;
    await Promise.all([
      loadDiscoveryDevices(),
      loadDiscoveryScans(),
      loadDiscoveryAgents(),
      loadDiscoveryConflicts(),
    ]);
    renderDiscovery();
    armDiscoveryPoll();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    discovery.loading = false;
  }
}

function discoveryFilterQuery() {
  const filters = state.discovery.filters;
  const params = new URLSearchParams();
  if (filters.state) params.set("state", filters.state);
  if (filters.type) params.set("type", filters.type);
  if (filters.segment) params.set("segment", filters.segment);
  if (filters.vendor) params.set("vendor", filters.vendor);
  if (filters.no_agent) params.set("no_agent", "true");
  if (filters.admin_ports) params.set("admin_ports", "true");
  if (filters.exposure) params.set("exposure", "true");
  if (filters.conflict) params.set("conflict", "true");
  const query = params.toString();
  return query ? `?${query}` : "";
}

async function loadDiscoveryDevices() {
  const data = await api(`/api/discovery/devices${discoveryFilterQuery()}`);
  state.discovery.devices = data.devices || [];
  state.discovery.summary = data.summary || null;
  renderDiscoverySummary();
  renderDiscoveryFilters();
  renderDiscoveryTable();
}

async function loadDiscoveryScans() {
  const data = await api("/api/discovery/scans");
  state.discovery.scans = data.scans || [];
  state.discovery.activeScanId = data.active_scan_id || null;
  renderDiscoveryScanHistory();
}

async function loadDiscoveryConflicts() {
  const data = await api("/api/discovery/conflicts");
  state.discovery.conflicts = data.conflicts || [];
  state.discovery.conflictIds = new Set(data.conflict_device_ids || []);
}

async function loadDiscoveryAgents() {
  const data = await api("/api/discovery/agents");
  state.discovery.agents = data.agents || [];
  state.discovery.relayEnabled = Boolean(data.relay_enabled);
  if (state.discovery.source !== "server") {
    const sourceId = state.discovery.source.slice("agent:".length);
    const current = state.discovery.agents.find(
      (agent) => agent.id === sourceId && agent.eligible,
    );
    if (!current) state.discovery.source = "server";
  }
  renderDiscoverySources();
}

function stopDiscoveryPoll() {
  if (state.discovery.pollTimer) {
    clearInterval(state.discovery.pollTimer);
    state.discovery.pollTimer = null;
  }
}

function armDiscoveryPoll() {
  if (!state.discovery.activeScanId) {
    stopDiscoveryPoll();
    return;
  }
  if (state.discovery.pollTimer) return;
  state.discovery.pollTimer = setInterval(async () => {
    if (state.page !== "discovery") {
      stopDiscoveryPoll();
      return;
    }
    const wasActive = state.discovery.activeScanId;
    try {
      await loadDiscoveryScans();
      if (!state.discovery.activeScanId) {
        stopDiscoveryPoll();
        if (wasActive) await loadDiscoveryDevices();
      }
    } catch (_error) {
      stopDiscoveryPoll();
    }
  }, 1500);
}

async function startDiscoveryScan(event) {
  event.preventDefault();
  const scope = $("discovery-scan-scope")?.value || "";
  const methods = [...state.discovery.methods];
  setText("discovery-scan-error", "");
  if (!scope) {
    setText("discovery-scan-error", "Choose an allowlisted scope.");
    return;
  }
  if (!methods.length) {
    setText("discovery-scan-error", "Select at least one discovery method.");
    return;
  }
  const start = $("discovery-scan-start");
  if (start) start.disabled = true;
  const source = state.discovery.source || "server";
  try {
    if (source.startsWith("agent:")) {
      const agentId = source.slice("agent:".length);
      await api(`/api/agents/${encodeURIComponent(agentId)}/discover`, {
        method: "POST",
        body: { scope: [scope], methods },
      });
      toast("Agent discovery scan queued.");
    } else {
      await api("/api/discovery/scan", {
        method: "POST",
        body: { scope: [scope], methods },
      });
      toast("Discovery scan queued.");
    }
    await loadDiscoveryScans();
    armDiscoveryPoll();
  } catch (error) {
    setText("discovery-scan-error", error.message);
    if (start) start.disabled = false;
  }
}

async function cancelDiscoveryScan() {
  const scanId = state.discovery.activeScanId;
  if (!scanId) return;
  try {
    await api(`/api/discovery/scans/${scanId}/cancel`, { method: "POST", body: {} });
    toast("Cancellation requested.");
    await loadDiscoveryScans();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function acknowledgeDiscovery() {
  const checkbox = $("discovery-ack-checkbox");
  setText("discovery-ack-error", "");
  if (!checkbox?.checked) {
    setText("discovery-ack-error", "Confirm the authorization statement first.");
    return;
  }
  const button = $("discovery-ack-button");
  if (button) button.disabled = true;
  try {
    const settings = state.discovery.settings;
    await api("/api/discovery/settings", {
      method: "PATCH",
      body: {
        acknowledged: true,
        acknowledgement: settings?.acknowledgement_text || "I am authorized to scan these networks",
      },
    });
    toast("Discovery authorization recorded.");
    await loadDiscovery();
  } catch (error) {
    setText("discovery-ack-error", error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

function discoveryServiceSummary(port, service) {
  if (!service || typeof service !== "object") return "Open TCP port";
  if (service.protocol === "ssh") {
    return [service.product, service.version, service.banner].filter(Boolean).join(" · ");
  }
  if (service.protocol === "http") {
    return [
      service.status ? `HTTP ${service.status}` : "",
      service.server || "",
      service.title ? `“${service.title}”` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (service.protocol === "tls") {
    return [
      service.subject ? `CN ${service.subject}` : "",
      service.issuer ? `issuer ${service.issuer}` : "",
      Array.isArray(service.san) && service.san.length ? `SAN ${service.san.join(", ")}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return service.protocol ? String(service.protocol) : "Open TCP port";
}

function discoveryEventLabel(kind) {
  return (
    {
      new_device: "New device",
      new_port: "New port",
      service_changed: "Service changed",
      identity_changed: "Identity changed",
      identity_conflict: "Identity conflict",
      went_dark: "Went dark",
      promoted: "Promoted",
      wake_verified: "Wake verified",
    }[kind] || kind
  );
}

function renderDiscoveryDrawer() {
  const detail = state.discovery.detail;
  const body = $("discovery-drawer-body");
  const actions = $("discovery-drawer-actions");
  if (!body || !detail) return;
  const device = detail.device;
  const name = discoveryDeviceName(device);
  const effective = discoveryEffectiveState(device);
  setText("discovery-drawer-title", name);
  setText("discovery-drawer-eyebrow", `${device.device_type || "unknown"} · ${effective.label}`);
  setText(
    "discovery-drawer-meta",
    `${device.ip || "no address"} · ${device.mac || "no MAC"} · ${device.vendor || "unknown vendor"}`,
  );
  clear(actions);
  if (state.permissions.admin && device.ip) {
    actions.append(
      button("Remote install", "button-secondary button-small", () =>
        openDiscoveryPromote(device, "remote_install"),
      ),
    );
  }
  if (state.permissions.operate) {
    actions.append(
      button("Promote", "button-primary button-small", () => openDiscoveryPromote(device)),
    );
    if (device.wake_target_id) {
      actions.append(
        button("Verify wake", "button-secondary button-small", () => verifyDiscoveryDevice(device)),
      );
    }
    if ((detail.agent_candidates || []).length) {
      actions.append(
        button("Link agent", "button-secondary button-small", () =>
          openDiscoveryPromote(device, "existing_agent"),
        ),
      );
    }
    if (device.state !== "ignored") {
      actions.append(
        button("Ignore", "button-secondary button-small", () => updateDiscoveryState("ignored")),
      );
    }
    if (device.state !== "known") {
      actions.append(
        button("Mark known", "button-secondary button-small", () => updateDiscoveryState("known")),
      );
    }
  }
  if (state.permissions.admin) {
    actions.append(button("Delete", "button-ghost button-small", () => deleteDiscoveryDevice(device)));
  }

  clear(body);
  body.append(
    section(
      "Identity",
      "Device record",
      h(
        "div",
        { class: "info-grid" },
        infoTile("IP address", device.ip),
        infoTile("MAC address", device.mac || "not observed"),
        infoTile("Vendor", device.vendor || "unknown"),
        infoTile("Hostname", device.hostname || "unknown"),
        infoTile("Type", device.device_type || "unknown"),
        infoTile("OS guess", device.os_guess || "unknown"),
        infoTile("Confidence", `${Math.round((device.confidence || 0) * 100)}%`),
        infoTile("Segment", device.segment || "unsegmented"),
        infoTile("Source", device.source || "server"),
        infoTile("First seen", localTime(device.first_seen)),
        infoTile("Last seen", localTime(device.last_seen)),
        infoTile("Randomized MAC", device.mac_randomized ? "yes" : "no"),
        infoTile("Wake status", device.wake_status || "not verified"),
        infoTile("Wake verified", device.wake_verified_at ? localTime(device.wake_verified_at) : "never"),
      ),
    ),
  );

  const change = device.change || {};
  if (device.change_kind || change.kind) {
    const kind = device.change_kind || change.kind;
    body.append(
      section(
        "Changed",
        "Acknowledged by saving a state",
        h(
          "div",
          { class: "info-grid" },
          infoTile("Change", discoveryChangeLabel(kind)),
          infoTile("Previous", change.from || "unknown"),
          infoTile("Current", change.to || "unknown"),
          infoTile("Detected", change.at ? localTime(change.at) : localTime(device.changed_at)),
        ),
      ),
    );
  }

  if ((detail.conflicts || []).length) {
    const conflictList = h("div", { class: "list-stack" });
    for (const conflict of detail.conflicts) {
      conflictList.append(
        h(
          "div",
          { class: "list-row" },
          h(
            "div",
            { class: "list-row-main" },
            h("strong", {}, conflict.type.replaceAll("_", " ")),
            h("small", {}, conflict.key),
            h("small", {}, JSON.stringify(conflict.detail || {})),
          ),
          statusPill("conflict", "critical"),
        ),
      );
    }
    body.append(section("Conflicts", "Identity evidence", conflictList));
  }

  const evidence = device.evidence || {};
  body.append(
    section(
      "Evidence",
      "What found this device",
      h(
        "div",
        { class: "info-grid" },
        infoTile("Methods", (evidence.methods || []).join(", ") || "unknown"),
        infoTile("Neighbour table", evidence.neighbor ? "yes" : "no"),
        infoTile("Reverse DNS", evidence.reverse_dns ? "yes" : "no"),
        infoTile("SSDP", evidence.ssdp ? "yes" : "no"),
        infoTile("TCP fingerprint", evidence.tcp ? "yes" : "no"),
      ),
    ),
  );

  const services = device.services && typeof device.services === "object" ? device.services : {};
  const ports = [...new Set([...(device.open_ports || []).map(String), ...Object.keys(services)])]
    .map(Number)
    .filter((port) => Number.isFinite(port))
    .sort((left, right) => left - right);
  const portBody = h("tbody");
  if (!ports.length) {
    portBody.append(h("tr", {}, h("td", { colspan: "2" }, "No open ports recorded.")));
  }
  for (const port of ports) {
    portBody.append(
      h(
        "tr",
        {},
        h("td", { class: "mono" }, String(port)),
        h("td", {}, discoveryServiceSummary(port, services[String(port)])),
      ),
    );
  }
  body.append(
    section(
      "Ports and services",
      `${ports.length} open port${ports.length === 1 ? "" : "s"}`,
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          { class: "data-table" },
          h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Port"), h("th", { scope: "col" }, "Evidence"))),
          portBody,
        ),
      ),
    ),
  );

  const events = detail.events || [];
  const eventList = h("div", { class: "list-stack" });
  if (!events.length) eventList.append(h("div", { class: "empty-inline" }, "No change events yet."));
  for (const event of events) {
    eventList.append(
      h(
        "div",
        { class: "list-row" },
        h(
          "div",
          { class: "list-row-main" },
          h("strong", {}, discoveryEventLabel(event.kind)),
          h("small", {}, JSON.stringify(event.detail || {})),
        ),
        h("span", { class: "form-hint" }, localTime(event.at)),
      ),
    );
  }
  body.append(section("History", "Change events", eventList));

  body.append(
    section(
      "Correlation",
      "Linked records",
      h(
        "div",
        { class: "info-grid" },
        infoTile("Enrolled agent", detail.agent?.name || device.agent_id || "not linked"),
        infoTile(
          "Agent status",
          detail.agent ? (detail.agent.online ? "online" : "offline") : "—",
        ),
        infoTile("Wake target", detail.wake_target?.name || device.wake_target_id || "not linked"),
        infoTile("Wake MAC", detail.wake_target?.mac || "—"),
        infoTile("Wake status", device.wake_status || "not verified"),
        infoTile("Last wake check", device.wake_verified_at ? localTime(device.wake_verified_at) : "never"),
      ),
    ),
  );

  const form = h(
    "form",
    { class: "form-stack" },
    h("label", { class: "field" }, h("span", {}, "Tags"), h("input", { name: "tags", maxlength: "2000", value: (device.tags || []).join(", "), placeholder: "office, lab" })),
    h("label", { class: "field" }, h("span", {}, "Notes"), h("textarea", { name: "notes", maxlength: "2000", rows: "3" }, device.notes || "")),
    h(
      "label",
      { class: "field" },
      h("span", {}, "State"),
      h(
        "select",
        { name: "state" },
        ...["new", "known", "ignored", "promoted"].map((value) =>
          h("option", { value, selected: value === device.state }, value),
        ),
      ),
    ),
    h(
      "p",
      { class: "form-hint" },
      device.change_kind || device.conflict
        ? "Saving a state acknowledges the identity change or conflict and clears the marker."
        : "New devices become known after they are seen in a later scan.",
    ),
    h("p", { class: "form-error", id: "discovery-edit-error", role: "alert" }),
    h("button", { class: "button button-primary", type: "submit", disabled: !state.permissions.operate }, "Save changes"),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.permissions.operate) return;
    const values = new FormData(form);
    setText("discovery-edit-error", "");
    try {
      const data = await api(`/api/discovery/devices/${device.id}`, {
        method: "PATCH",
        body: {
          tags: splitList(values.get("tags")),
          notes: String(values.get("notes") || "").trim(),
          state: String(values.get("state") || device.state),
        },
      });
      state.discovery.detail = { ...detail, device: data.device, events: data.events };
      renderDiscoveryDrawer();
      await loadDiscoveryDevices();
      toast("Device record updated.");
    } catch (error) {
      setText("discovery-edit-error", error.message);
    }
  });
  body.append(section("Operator metadata", "Tags and notes", form));
}

function exportDiscovery(format) {
  if (!state.permissions.admin) return;
  const query = discoveryFilterQuery();
  const suffix = query ? `&${query.slice(1)}` : "";
  window.location.href = `/api/discovery/export?format=${encodeURIComponent(format)}${suffix}`;
}

async function refreshDiscoveryDetail(deviceId) {
  try {
    const detail = await api(`/api/discovery/devices/${deviceId}`);
    state.discovery.detail = detail;
    renderDiscoveryDrawer();
  } catch (_error) {
    closeDiscoveryDrawer();
  }
}

async function verifyDiscoveryDevice(device) {
  if (!state.permissions.operate) return;
  try {
    const data = await api(`/api/discovery/devices/${device.id}/verify`, {
      method: "POST",
      body: {},
    });
    state.discovery.detail = {
      ...(state.discovery.detail || {}),
      device: data.device,
    };
    renderDiscoveryDrawer();
    await loadDiscoveryDevices();
    await loadDiscoveryConflicts();
    toast(`Wake verification: ${data.verification.status}.`);
  } catch (error) {
    toast(error.message, "error");
  }
}

function closeDiscoveryPromote() {
  const dialog = $("discovery-promote-dialog");
  if (dialog?.open) dialog.close();
  clear($("discovery-promote-body"));
  clear($("discovery-promote-actions"));
  setText("discovery-promote-error", "");
  state.discovery.promotion = null;
  if (state.discovery.promoteTrigger && typeof state.discovery.promoteTrigger.focus === "function") {
    state.discovery.promoteTrigger.focus();
  }
  state.discovery.promoteTrigger = null;
}

function discoveryRemoteOs(device) {
  const text = `${device.os_guess || ""} ${device.device_type || ""} ${device.vendor || ""}`.toLowerCase();
  if (text.includes("windows") || text.includes("microsoft")) return "windows";
  return "linux";
}

function openDiscoveryPromote(device, initialTarget = "wake_target") {
  const dialog = $("discovery-promote-dialog");
  if (!dialog) return;
  let target = !device.mac && initialTarget === "wake_target" ? "linux_agent" : initialTarget;
  if (target === "remote_install" && !state.permissions.admin) target = "linux_agent";
  state.discovery.promoteTrigger = document.activeElement;
  state.discovery.promotion = { device, target };
  renderDiscoveryPromote();
  dialog.showModal();
  const first = $("discovery-promote-body").querySelector("select, input, button");
  if (first) first.focus();
}

function renderDiscoveryPromote() {
  const promotion = state.discovery.promotion;
  const body = $("discovery-promote-body");
  const actions = $("discovery-promote-actions");
  if (!promotion || !body || !actions) return;
  const device = promotion.device;
  const candidates = (state.discovery.detail?.agent_candidates || []).filter(
    (agent) => agent.id,
  );
  clear(body);
  clear(actions);
  setText("discovery-promote-error", "");
  setText("discovery-promote-title", `Promote ${discoveryDeviceName(device)}`);

  const targetSelect = h(
    "select",
    { name: "target", id: "discovery-promote-target" },
    h(
      "option",
      { value: "wake_target", selected: promotion.target === "wake_target", disabled: !device.mac },
      "Wake target",
    ),
    h(
      "option",
      { value: "linux_agent", selected: promotion.target === "linux_agent" },
      "Linux agent install",
    ),
    h(
      "option",
      { value: "windows_agent", selected: promotion.target === "windows_agent" },
      "Windows agent install",
    ),
    state.permissions.admin
      ? h(
          "option",
          {
            value: "remote_install",
            selected: promotion.target === "remote_install",
            disabled: !device.ip,
          },
          "Remote install (SSH/WinRM)",
        )
      : null,
    h(
      "option",
      {
        value: "existing_agent",
        selected: promotion.target === "existing_agent",
        disabled: !candidates.length,
      },
      "Link an enrolled agent",
    ),
  );
  const form = h("form", { class: "form-stack", id: "discovery-promote-form" });
  const fields = h("div", { class: "form-stack" });
  form.append(
    h("label", { class: "field" }, h("span", {}, "Onboarding path"), targetSelect),
    fields,
    h("p", { class: "form-error", id: "discovery-promote-form-error", role: "alert" }),
  );

  const submitButton = h(
    "button",
    { class: "button button-primary", type: "button", onClick: () => submitDiscoveryPromotion(form) },
    "Continue",
  );
  const renderFields = () => {
    const target = targetSelect.value;
    promotion.target = target;
    submitButton.textContent =
      target === "wake_target"
        ? "Create wake target"
        : target === "existing_agent"
          ? "Link agent"
          : target === "remote_install"
            ? "Start remote install"
            : "Generate install command";
    clear(fields);
    if (target === "wake_target") {
      fields.append(
        h("label", { class: "field" }, h("span", {}, "Wake target name"), h("input", { name: "name", maxlength: "120", value: device.hostname || device.ip || "" })),
        h("label", { class: "field" }, h("span", {}, "Broadcast address"), h("input", { name: "broadcast", maxlength: "64", value: "255.255.255.255" })),
        h("label", { class: "field" }, h("span", {}, "UDP port"), h("input", { name: "port", type: "number", min: "1", max: "65535", value: "9" })),
        h("p", { class: "form-hint" }, device.mac_randomized ? "This MAC looks randomized; WoL may not work after reboot." : `Uses the observed MAC ${device.mac}.`),
      );
    } else if (target === "existing_agent") {
      fields.append(
        h(
          "label",
          { class: "field" },
          h("span", {}, "Enrolled agent"),
          h(
            "select",
            { name: "agent_id" },
            ...candidates.map((agent) =>
              h("option", { value: agent.id }, `${agent.name} · ${agent.mac || "no MAC"}`),
            ),
          ),
        ),
        h("p", { class: "form-hint" }, "The discovered MAC must match the enrolled agent MAC."),
      );
    } else if (target === "remote_install") {
      const profiles = state.profiles?.length
        ? state.profiles
        : [
            { slug: "workstation", name: "Workstation" },
            { slug: "server", name: "Server" },
          ];
      const osSelect = h(
        "select",
        { name: "remote_os", id: "discovery-remote-os" },
        h("option", { value: "linux" }, "Linux (key-based SSH)"),
        h("option", { value: "windows" }, "Windows (WinRM over HTTPS)"),
      );
      osSelect.value = discoveryRemoteOs(device);
      const portInput = h("input", {
        name: "remote_port",
        type: "number",
        min: "1",
        max: "65535",
        value: osSelect.value === "windows" ? "5986" : "22",
      });
      const userInput = h("input", {
        name: "remote_user",
        maxlength: "128",
        value: osSelect.value === "windows" ? "Administrator" : "root",
      });
      const passwordWrap = h(
        "label",
        { class: "field", hidden: osSelect.value !== "windows" },
        h("span", {}, "Windows password"),
        h("input", {
          name: "remote_password",
          type: "password",
          maxlength: "4096",
          autocomplete: "new-password",
          placeholder: "used only for this job",
        }),
      );
      const syncRemote = () => {
        const windows = osSelect.value === "windows";
        passwordWrap.hidden = !windows;
        portInput.value = windows ? "5986" : "22";
        userInput.value = windows ? "Administrator" : "root";
      };
      osSelect.addEventListener("change", syncRemote);
      const profileSelect = h(
        "select",
        { name: "remote_profile" },
        ...profiles.map((profile) =>
          h("option", { value: profile.slug }, `${profile.name} (${profile.slug})`),
        ),
      );
      profileSelect.value = profiles.some((profile) => profile.slug === "workstation")
        ? "workstation"
        : profiles[0]?.slug || "workstation";
      fields.append(
        h(
          "p",
          { class: "form-hint" },
          `Starts a remote install job against ${device.ip}. Linux uses the configured SSH key; Windows uses WinRM over HTTPS and the password is used only for this job. Admin only.`,
        ),
        h("label", { class: "field" }, h("span", {}, "Target OS"), osSelect),
        h(
          "label",
          { class: "field" },
          h("span", {}, "Host"),
          h("input", { name: "remote_host", maxlength: "255", value: device.ip, readonly: true }),
        ),
        h(
          "div",
          { class: "form-grid" },
          h("label", { class: "field" }, h("span", {}, "Port"), portInput),
          h("label", { class: "field" }, h("span", {}, "User"), userInput),
        ),
        passwordWrap,
        h(
          "div",
          { class: "form-grid" },
          h(
            "label",
            { class: "field" },
            h("span", {}, "Agent name"),
            h("input", { name: "remote_name", maxlength: "255", value: device.hostname || device.ip || "" }),
          ),
          h(
            "label",
            { class: "field" },
            h("span", {}, "Profile"),
            profileSelect,
          ),
        ),
        h(
          "label",
          { class: "field" },
          h("span", {}, "Watch services (optional)"),
          h("input", { name: "remote_watch_services", maxlength: "1000", placeholder: "ssh, nginx" }),
        ),
        h(
          "p",
          { class: "form-hint" },
          "The job runs in the background. The Remote install page opens with its live log.",
        ),
      );
    } else {
      const windows = target === "windows_agent";
      fields.append(
        h("label", { class: "field" }, h("span", {}, "Agent name"), h("input", { name: "name", maxlength: "120", value: device.hostname || device.ip || "" })),
        h(
          "label",
          { class: "field" },
          h("span", {}, "Token lifetime"),
          h(
            "select",
            { name: "expires_minutes" },
            h("option", { value: "30" }, "30 minutes"),
            h("option", { value: "60" }, "1 hour"),
            h("option", { value: "240" }, "4 hours"),
            h("option", { value: "1440" }, "24 hours"),
          ),
        ),
        h(
          "p",
          { class: "form-hint" },
          windows
            ? "Manual elevated PowerShell only. Sextant does not implement remote Windows installation."
            : "Run the command as root on the target after installing the agent package.",
        ),
        windows
          ? h(
              "p",
              { class: "form-hint" },
              h(
                "a",
                {
                  href: "https://github.com/JugglerMaster/Sextant/blob/main/docs/WINDOWS_REMOTE_INSTALL.md",
                  target: "_blank",
                  rel: "noopener",
                },
                "Read the Windows remote-install plan",
              ),
            )
          : null,
        h(
          "p",
          { class: "form-hint" },
          "The one-time token is shown only in the response and is stored only as a hash.",
        ),
      );
    }
  };
  targetSelect.addEventListener("change", renderFields);
  renderFields();
  body.append(form);
  actions.append(
    button("Cancel", "button-ghost", closeDiscoveryPromote),
    submitButton,
  );
}

async function submitDiscoveryPromotion(form) {
  const promotion = state.discovery.promotion;
  if (!promotion) return;
  const values = new FormData(form);
  const target = String(values.get("target") || promotion.target);
  if (target === "remote_install") {
    const osName = String(values.get("remote_os") || "linux");
    const payload = {
      host: String(values.get("remote_host") || promotion.device.ip || "").trim(),
      port: Number(values.get("remote_port") || (osName === "windows" ? 5986 : 22)),
      user: String(values.get("remote_user") || (osName === "windows" ? "Administrator" : "root")).trim(),
      name: String(values.get("remote_name") || "").trim(),
      profile: String(values.get("remote_profile") || "workstation"),
      watch_services: String(values.get("remote_watch_services") || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      os: osName,
      transport: osName === "windows" ? "winrm" : "ssh",
      password: osName === "windows" ? String(values.get("remote_password") || "") : "",
    };
    setText("discovery-promote-error", "");
    setText("discovery-promote-form-error", "");
    try {
      const job = await api("/api/remote-install", { method: "POST", body: payload });
      closeDiscoveryPromote();
      state.remoteJobId = job.id;
      toast("Remote install started.");
      navigate("remote-install");
    } catch (error) {
      setText("discovery-promote-form-error", error.message);
    }
    return;
  }
  const body = { target };
  if (target === "existing_agent") {
    body.agent_id = String(values.get("agent_id") || "");
  } else {
    body.name = String(values.get("name") || "");
    if (target === "wake_target") {
      body.broadcast = String(values.get("broadcast") || "255.255.255.255");
      body.port = Number(values.get("port") || 9);
    } else {
      body.expires_minutes = Number(values.get("expires_minutes") || 30);
    }
  }
  setText("discovery-promote-error", "");
  setText("discovery-promote-form-error", "");
  try {
    const result = await api(`/api/discovery/devices/${promotion.device.id}/promote`, {
      method: "POST",
      body,
    });
    if (result.enrollment) {
      const enrollment = { ...result.enrollment };
      delete enrollment.token;
      renderDiscoveryPromotionResult(enrollment);
    } else {
      closeDiscoveryPromote();
      await loadDiscoveryDevices();
      await loadDiscoveryConflicts();
      await refreshDiscoveryDetail(promotion.device.id);
      toast(
        result.wake_target
          ? result.duplicate
            ? "Existing wake target linked."
            : "Wake target created."
          : "Agent linked.",
      );
    }
  } catch (error) {
    setText("discovery-promote-form-error", error.message);
  }
}

function renderDiscoveryPromotionResult(enrollment) {
  const body = $("discovery-promote-body");
  const actions = $("discovery-promote-actions");
  if (!body || !actions) return;
  clear(body);
  clear(actions);
  setText("discovery-promote-error", "");
  const code = h("code", { class: "code-block" }, enrollment.install_command);
  body.append(
    h(
      "div",
      { class: "callout" },
      h("p", { class: "eyebrow" }, "Shown once"),
      h("p", { class: "form-hint" }, `Use this command on ${enrollment.address || "the target host"}. It expires at ${localTime(enrollment.expires_at)}.`),
      code,
      h(
        "div",
        { class: "section-toolbar" },
        h(
          "button",
          {
            class: "button button-secondary button-small",
            type: "button",
            onClick: async () => {
              if (await copyText(enrollment.install_command)) toast("Install command copied.");
              else toast("Could not copy. Select the command manually.", "warn");
            },
          },
          "Copy command",
        ),
      ),
      h("p", { class: "eyebrow" }, "Prerequisites"),
      h("ul", { class: "feature-list" }, ...(enrollment.prerequisites || []).map((item) => h("li", {}, item))),
      enrollment.windows_plan
        ? h(
            "p",
            { class: "form-hint" },
            h(
              "a",
              { href: enrollment.windows_plan, target: "_blank", rel: "noopener" },
              "Windows remote-install plan",
            ),
          )
        : null,
    ),
  );
  actions.append(button("Close", "button-ghost", closeDiscoveryPromote));
}

async function openDiscoveryDevice(deviceId) {
  state.discovery.trigger = document.activeElement;
  try {
    const detail = await api(`/api/discovery/devices/${deviceId}`);
    state.discovery.selectedId = deviceId;
    state.discovery.detail = detail;
    renderDiscoveryDrawer();
    $("discovery-drawer").hidden = false;
    document.body.classList.add("drawer-open");
    document.addEventListener("keydown", discoveryDrawerTrapHandler);
    const first = $("discovery-drawer").querySelector("button, input, select, textarea, [tabindex]:not([tabindex='-1'])");
    if (first) first.focus();
  } catch (error) {
    toast(error.message, "error");
  }
}

function discoveryDrawerTrapHandler(event) {
  const drawer = $("discovery-drawer");
  if (!drawer || drawer.hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeDiscoveryDrawer();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...drawer.querySelectorAll("button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])")].filter(
    (element) => !element.disabled && element.offsetParent !== null,
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

async function updateDiscoveryState(nextState) {
  const device = state.discovery.detail?.device;
  if (!device) return;
  try {
    const data = await api(`/api/discovery/devices/${device.id}`, {
      method: "PATCH",
      body: { state: nextState },
    });
    state.discovery.detail = { ...state.discovery.detail, device: data.device, events: data.events };
    renderDiscoveryDrawer();
    await loadDiscoveryDevices();
    toast(`Device marked ${nextState}.`);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteDiscoveryDevice(device) {
  if (!state.permissions.admin) return;
  const confirmed = await confirmAction(
    `Delete ${discoveryDeviceName(device)}?`,
    "This removes the discovered device and its change history. An enrolled agent, wake target, and audit rows are not deleted.",
    "Delete device",
  );
  if (!confirmed) return;
  try {
    await api(`/api/discovery/devices/${device.id}`, { method: "DELETE" });
    closeDiscoveryDrawer();
    await loadDiscoveryDevices();
    toast("Discovered device deleted.");
  } catch (error) {
    toast(error.message, "error");
  }
}

function closeDiscoveryDrawer() {
  const drawer = $("discovery-drawer");
  if (!drawer || drawer.hidden) return;
  drawer.hidden = true;
  document.body.classList.remove("drawer-open");
  document.removeEventListener("keydown", discoveryDrawerTrapHandler);
  state.discovery.selectedId = null;
  state.discovery.detail = null;
  if (state.discovery.trigger && typeof state.discovery.trigger.focus === "function") {
    state.discovery.trigger.focus();
  }
  state.discovery.trigger = null;
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

  $("update-nav-button")?.addEventListener("click", () => navigate("updates"));
  $("docker-refresh-list")?.addEventListener("click", () => loadDockerHosts());
  $("docker-refresh-host")?.addEventListener("click", refreshDockerHost);
  $("docker-close-host")?.addEventListener("click", closeDockerHost);
  wireDockerConsoleEvents();
  $("docker-dialog")?.addEventListener("close", () => {
    const trigger = state.dockerDialogTrigger;
    state.dockerDialogTrigger = null;
    if (trigger && typeof trigger.focus === "function") trigger.focus();
  });
  document.querySelectorAll("#docker-host-tabs .tab").forEach((el) => {
    el.addEventListener("click", () => {
      state.dockerTab = el.dataset.dockerTab;
      document.querySelectorAll("#docker-host-tabs .tab").forEach((tab) => {
        tab.classList.toggle("is-active", tab === el);
      });
      renderDockerTab();
    });
  });
  $("discovery-ack-button")?.addEventListener("click", acknowledgeDiscovery);
  $("discovery-scan-source")?.addEventListener("change", (event) => {
    const source = event.target.value || "server";
    state.discovery.source = source;
    if (source.startsWith("agent:")) {
      const agentId = source.slice("agent:".length);
      const agent = (state.discovery.agents || []).find((item) => item.id === agentId);
      const scope = $("discovery-scan-scope");
      if (agent?.segment && scope && [...scope.options].some((option) => option.value === agent.segment)) {
        scope.value = agent.segment;
      }
    }
    renderDiscoverySources();
  });
  $("discovery-scan-form")?.addEventListener("submit", startDiscoveryScan);
  $("discovery-scan-cancel")?.addEventListener("click", cancelDiscoveryScan);
  document.querySelectorAll("[data-close-discovery-drawer]").forEach((el) => {
    el.addEventListener("click", closeDiscoveryDrawer);
  });
  $("domain-join-dialog")?.addEventListener("close", () => {
    if (state.domainJoinTimer) {
      clearTimeout(state.domainJoinTimer);
      state.domainJoinTimer = null;
    }
    state.domainJoin = null;
    setText("domain-join-error", "");
  });
  $("discovery-promote-dialog")?.addEventListener("close", () => {
    clear($("discovery-promote-body"));
    clear($("discovery-promote-actions"));
    setText("discovery-promote-error", "");
    state.discovery.promotion = null;
    const trigger = state.discovery.promoteTrigger;
    state.discovery.promoteTrigger = null;
    if (trigger && typeof trigger.focus === "function") trigger.focus();
  });
  const discoveryFilters = {
    "discovery-filter-state": "state",
    "discovery-filter-type": "type",
    "discovery-filter-segment": "segment",
    "discovery-filter-vendor": "vendor",
  };
  for (const [id, key] of Object.entries(discoveryFilters)) {
    $(id)?.addEventListener("change", (event) => {
      state.discovery.filters[key] = event.target.value;
      loadDiscoveryDevices().catch((error) => toast(error.message, "error"));
    });
  }
  $("discovery-filter-no-agent")?.addEventListener("change", (event) => {
    state.discovery.filters.no_agent = event.target.checked;
    loadDiscoveryDevices().catch((error) => toast(error.message, "error"));
  });
  $("discovery-filter-admin-ports")?.addEventListener("change", (event) => {
    state.discovery.filters.admin_ports = event.target.checked;
    loadDiscoveryDevices().catch((error) => toast(error.message, "error"));
  });
  $("discovery-filter-exposure")?.addEventListener("change", (event) => {
    state.discovery.filters.exposure = event.target.checked;
    loadDiscoveryDevices().catch((error) => toast(error.message, "error"));
  });
  $("discovery-export-csv")?.addEventListener("click", () => exportDiscovery("csv"));
  $("discovery-export-json")?.addEventListener("click", () => exportDiscovery("json"));
  $("discovery-import-form")?.addEventListener("submit", submitDiscoveryImport);
  $("discovery-import-preview")?.addEventListener("click", previewDiscoveryImport);
  $("discovery-import-clear")?.addEventListener("click", clearDiscoveryImport);
  $("discovery-import-file")?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 1_000_000) {
      setText("discovery-import-status", "Import file exceeds the 1 MB limit.");
      event.target.value = "";
      return;
    }
    try {
      $("discovery-import-content").value = await file.text();
      setText("discovery-import-status", `${file.name} loaded (${file.size} bytes).`);
    } catch (_error) {
      setText("discovery-import-status", "Could not read the selected file.");
    }
  });
  document.querySelectorAll(".discovery-integrations-refresh").forEach((button) => {
    button.addEventListener("click", () => loadDiscoveryIntegrations());
  });
  document.querySelectorAll("#discovery-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.discovery.tab = tab.dataset.discoveryTab || "discover";
      renderDiscoveryTabs();
    });
  });
  $("discovery-filter-clear")?.addEventListener("click", () => {
    state.discovery.filters = {
      state: "",
      type: "",
      segment: "",
      vendor: "",
      no_agent: false,
      admin_ports: false,
      exposure: false,
      conflict: false,
    };
    renderDiscoveryFilters();
    loadDiscoveryDevices().catch((error) => toast(error.message, "error"));
  });

  $("compose-new")?.addEventListener("click", newComposeStackDialog);
  $("compose-close")?.addEventListener("click", closeComposeStack);
  $("compose-deploy-current")?.addEventListener("click", () => {
    if (state.composeDetail) deployComposeDialog(state.composeDetail, state.composeDetail.current_revision_hash);
  });
  document.querySelectorAll("#compose-tabs .tab").forEach((el) => {
    el.addEventListener("click", () => {
      state.composeTab = el.dataset.composeTab;
      document.querySelectorAll("#compose-tabs .tab").forEach((tab) => {
        tab.classList.toggle("is-active", tab === el);
      });
      renderComposeTab();
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
  $("remote-install-os").addEventListener("change", updateRemoteInstallMode);

  $("update-banner-check")?.addEventListener("click", () => checkForUpdates(true));
  $("update-banner-apply")?.addEventListener("click", applyServerUpdate);
  $("updates-check-now")?.addEventListener("click", () => checkForUpdates(true));
  $("updates-apply")?.addEventListener("click", applyServerUpdate);
  $("updates-rollback")?.addEventListener("click", rollbackServerUpdate);

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

  $("user-profile-form")?.addEventListener("submit", saveUserProfile);
  $("server-settings-form")?.addEventListener("submit", saveServerSettings);
  $("domain-settings-form")?.addEventListener("submit", saveDomainSettings);
  $("ssh-settings-form")?.addEventListener("submit", saveSshSettings);
  $("domain-settings-test")?.addEventListener("click", testDomainSettings);
  $("ssh-settings-test")?.addEventListener("click", testSshSettings);
  $("server-settings-restart")?.addEventListener("click", restartForSettings);
  $("settings-open-security")?.addEventListener("click", () => navigate("security"));
  document.querySelectorAll("#settings-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.settingsTab = tab.dataset.settingsTab || "profile";
      renderSettings();
    });
  });
  $("ssh-settings-form")?.elements.source?.addEventListener("change", syncSshFields);
  $("ssh-key-file")?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 65536) {
      settingsFormError("ssh-settings-error", "Key file exceeds the 64 KB limit.");
      event.target.value = "";
      return;
    }
    try {
      const form = $("ssh-settings-form");
      form.elements.private_key.value = await file.text();
      toast(`${file.name} loaded. Save to install it.`);
    } catch (_error) {
      settingsFormError("ssh-settings-error", "Could not read the selected key file.");
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
          allow_discovery: form.elements.allow_discovery.checked,
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
      stopDockerAutoRefresh();
      return;
    }
    loadDashboard(true);
    startLiveUpdates();
    if (state.page === "docker-hosts") startDockerAutoRefresh();
  });
}

init();
