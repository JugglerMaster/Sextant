const fs = require("fs");
const path = require("path");
const { expect } = require("@playwright/test");
const { AxeBuilder } = require("@axe-core/playwright");
const { FIXED_NOW, createApi, eventSourceInit } = require("./fixtures");

const artifacts = path.join(__dirname, "..", "artifacts");

function artifactPath(name) {
  fs.mkdirSync(artifacts, { recursive: true });
  return path.join(artifacts, `${name}.png`);
}

async function screenshot(page, name, options = {}) {
  const file = artifactPath(name);
  // content-visibility is a production performance optimization, but Chromium
  // can skip painting far-offscreen cards in a full-page screenshot.
  await page.addStyleTag({ content: ".agent-card { content-visibility: visible !important; }" });
  await page.screenshot({ path: file, animations: "disabled", ...options });
  return file;
}

async function preparePage(page, { theme = "dark" } = {}) {
  const session = { authenticated: false };
  const respond = createApi(session);
  await page.addInitScript(eventSourceInit);
  await page.addInitScript(
    ({ selectedTheme, fixedNow }) => {
      try {
        localStorage.setItem("sextant.theme", selectedTheme);
      } catch (_error) {
        // The document may not have an origin yet.
      }
      const RealDate = Date;
      const fixed = new RealDate(fixedNow).getTime();
      class FixedDate extends RealDate {
        constructor(...args) {
          if (args.length === 0) super(fixed);
          else super(...args);
        }
        static now() {
          return fixed;
        }
      }
      globalThis.Date = FixedDate;
    },
    { selectedTheme: theme, fixedNow: FIXED_NOW },
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const response = respond(url, request.method().toUpperCase());
    if (response.status === 204) {
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fulfill({
      status: response.status,
      contentType: "application/json",
      body: JSON.stringify(response.body),
    });
  });
  return session;
}

async function gotoApp(page, { theme = "dark", width = 1440, height = 960 } = {}) {
  await preparePage(page, { theme });
  await page.setViewportSize({ width, height });
  await page.goto("/");
  await expect(page.locator("#login-view")).toBeVisible();
}

async function signIn(page) {
  await page.locator("#login-username").fill("admin");
  await page.locator("#login-password").fill("password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator("#app-view")).toBeVisible();
  await expect(page.locator("#stat-total")).toHaveText("3");
  await expect(page.getByRole("heading", { name: "Studio PC" })).toBeVisible();
  return page;
}

async function login(page, { theme = "dark", width = 1440, height = 960 } = {}) {
  await gotoApp(page, { theme, width, height });
  return signIn(page);
}

async function openDrawer(page, name = "Studio PC") {
  const card = page.locator(".agent-card", { has: page.getByRole("heading", { name }) });
  await card.locator(".agent-open").click();
  await expect(page.locator("#agent-drawer")).toBeVisible();
  await expect(page.locator("#drawer-title")).toHaveText(name);
  await expect(page.locator("#drawer-body")).toContainText("System");
}

async function selectTab(page, tab) {
  await page.locator(`#drawer-tabs [data-tab="${tab}"]`).click();
  await expect(page.locator(`#drawer-tabs [data-tab="${tab}"]`)).toHaveClass(/is-active/);
}

async function tabTo(page, selector, max = 80) {
  for (let index = 0; index < max; index += 1) {
    await page.keyboard.press("Tab");
    const matches = await page.evaluate((target) => document.activeElement?.matches(target) || false, selector);
    if (matches) return true;
  }
  return false;
}

async function scan(page, label) {
  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations.filter((violation) => ["serious", "critical"].includes(violation.impact));
  const details = blocking
    .map((violation) => {
      const targets = violation.nodes.map((node) => node.target.join(" ")).join(" | ");
      return `${violation.id} (${violation.impact}): ${violation.help}\n  ${targets}`;
    })
    .join("\n");
  expect(blocking, `${label} axe violations:\n${details}`).toEqual([]);
  return results;
}

module.exports = { artifactPath, gotoApp, login, openDrawer, preparePage, scan, screenshot, selectTab, signIn, tabTo };
