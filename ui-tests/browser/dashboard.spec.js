const { test, expect } = require("@playwright/test");
const { gotoApp, login, openDrawer, scan, screenshot, selectTab, signIn, tabTo } = require("./helpers");

const THEMES = ["dark", "light"];

for (const theme of THEMES) {
  test(`login page renders in the ${theme} theme`, async ({ page }) => {
    await gotoApp(page, { theme });
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Your machines/ })).toBeVisible();
    await screenshot(page, `login-${theme}-desktop`, { fullPage: true });
    await scan(page, `login ${theme} desktop`);
  });

  test(`fleet dashboard renders agents and alerts in the ${theme} theme`, async ({ page }) => {
    await login(page, { theme });
    await expect(page.locator("#agent-grid .agent-card")).toHaveCount(3);
    await expect(page.locator("#alert-center")).toBeVisible();
    await expect(page.locator("#alert-center")).toContainText("Disk C: is 85% full");
    await expect(page.locator("#alert-center")).toContainText("Watched service sshd is stopped");
    await expect(page.locator("#update-banner")).toContainText("9.9.9");
    await expect(page.locator("#agent-grid").getByText("office", { exact: true })).toBeVisible();
    await screenshot(page, `dashboard-${theme}-desktop`, { fullPage: true });
    await scan(page, `dashboard ${theme} desktop`);
  });

  test(`device drawer tabs render in the ${theme} theme`, async ({ page }) => {
    await login(page, { theme });
    await openDrawer(page);
    await expect(page.locator("#drawer-body .sparkline").first()).toBeVisible();
    await screenshot(page, `drawer-overview-${theme}`);
    await scan(page, `drawer overview ${theme}`);

    await selectTab(page, "trends");
    await expect(page.locator("#drawer-body .chart-card")).toHaveCount(5);
    await expect(page.locator("#drawer-body .timeline-entry")).toHaveCount(6);
    const chartGeometry = await page.locator("#drawer-body .chart-card .detail-chart").first().evaluate((svg) => {
      const texts = [...svg.querySelectorAll("text")].map((node) => Math.round(node.getBoundingClientRect().top));
      const paths = [...svg.querySelectorAll("polyline")].map((node) => node.getBoundingClientRect().height);
      return { textRows: new Set(texts).size, pathHeight: Math.max(0, ...paths), box: svg.getBoundingClientRect().height };
    });
    expect(chartGeometry.box).toBeGreaterThan(120);
    expect(chartGeometry.textRows).toBeGreaterThan(2);
    expect(chartGeometry.pathHeight).toBeGreaterThan(20);
    await screenshot(page, `drawer-trends-${theme}`);
    await scan(page, `drawer trends ${theme}`);

    await selectTab(page, "services");
    await expect(page.locator("#service-filter-count")).toContainText("55 of 55 services");
    await screenshot(page, `drawer-services-${theme}`);

    await selectTab(page, "packages");
    await expect(page.locator("#drawer-body")).toContainText("nginx");
    await expect(page.locator("#drawer-body")).toContainText("Desired state");
    await screenshot(page, `drawer-packages-${theme}`);

    await selectTab(page, "history");
    await expect(page.locator("[data-history-count]")).toContainText("1-30 of 30 commands");
    await screenshot(page, `drawer-history-${theme}`);

    await selectTab(page, "remote");
    await expect(page.locator("#drawer-body .terminal")).toBeVisible();
    await expect(page.locator("#drawer-body .terminal-bar")).toContainText("agent@studio-pc");
    await screenshot(page, `drawer-remote-${theme}`);
  });
}

for (const theme of THEMES) {
  test(`login and fleet dashboard render at the mobile viewport in the ${theme} theme`, async ({ page }) => {
    await gotoApp(page, { theme, width: 390, height: 844 });
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await screenshot(page, `login-${theme}-mobile`, { fullPage: true });
    await scan(page, `login ${theme} mobile`);

    await signIn(page);
    await expect(page.locator("#agent-grid .agent-card")).toHaveCount(3);
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible();
    const searchBox = await page.locator(".search-box").boundingBox();
    expect(searchBox.width).toBeGreaterThan(120);
    await screenshot(page, `dashboard-${theme}-mobile`, { fullPage: true });
    await scan(page, `dashboard ${theme} mobile`);
  });
}

test("Ctrl/Cmd+K opens, filters, activates, and closes the command palette", async ({ page }) => {
  await login(page);
  const palette = page.locator("#command-palette");
  const input = page.locator("#command-palette-input");
  const list = page.locator("#command-palette-list");

  await page.keyboard.press("Control+k");
  await expect(palette).toBeVisible();
  await expect(input).toBeFocused();
  await expect(list.getByRole("option")).toHaveCount(11);
  await screenshot(page, "command-palette-dark");

  await input.fill("studio");
  await expect(list.getByRole("option")).toHaveCount(1);
  await expect(list).toContainText("Studio PC");
  await screenshot(page, "command-palette-filtered-dark");

  await page.keyboard.press("Enter");
  await expect(palette).toBeHidden();
  await expect(page.locator("#agent-drawer")).toBeVisible();
  await expect(page.locator("#drawer-title")).toHaveText("Studio PC");
  await expect
    .poll(() => page.evaluate(() => document.getElementById("agent-drawer")?.contains(document.activeElement)))
    .toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.locator("#agent-drawer")).toBeHidden();

  await page.locator("#command-palette-button").click();
  await expect(palette).toBeVisible();
  await input.fill("wake");
  await expect(list.getByRole("option")).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(palette).toBeHidden();
  await expect(page.locator("#page-wake")).toBeVisible();

  await page.locator("#command-palette-button").click();
  await expect(palette).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(palette).toBeHidden();
  await expect(page.locator("#command-palette-button")).toBeFocused();

  // Meta+K is the macOS spelling of the same shortcut.
  await page.keyboard.press("Meta+k");
  await expect(palette).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(palette).toBeHidden();
});

test("keyboard opens the drawer, traps focus, and returns focus to the trigger", async ({ page }) => {
  await login(page);

  const reached = await tabTo(page, ".agent-open");
  expect(reached).toBe(true);
  const triggerLabel = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  expect(triggerLabel).toBe("Open Studio PC details");

  await page.keyboard.press("Enter");
  await expect(page.locator("#agent-drawer")).toBeVisible();
  await expect(page.locator("#drawer-title")).toHaveText("Studio PC");
  await expect
    .poll(() => page.evaluate(() => document.getElementById("agent-drawer")?.contains(document.activeElement)))
    .toBe(true);

  const focusables = await page.evaluate(() => {
    const drawer = document.getElementById("agent-drawer");
    const list = [
      ...drawer.querySelectorAll("button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])"),
    ].filter((element) => !element.disabled && element.offsetParent !== null);
    list.forEach((element, index) => {
      element.dataset.testFocusIndex = String(index);
    });
    return { count: list.length, first: list[0]?.dataset.testFocusIndex, last: list[list.length - 1]?.dataset.testFocusIndex };
  });
  expect(focusables.count).toBeGreaterThan(2);

  await expect
    .poll(() => page.evaluate(() => document.activeElement?.dataset.testFocusIndex))
    .toBe(focusables.first);
  await page.keyboard.press("Shift+Tab");
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.dataset.testFocusIndex))
    .toBe(focusables.last);
  await screenshot(page, "drawer-keyboard-focus-dark");
  await page.keyboard.press("Tab");
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.dataset.testFocusIndex))
    .toBe(focusables.first);

  await page.keyboard.press("Escape");
  await expect(page.locator("#agent-drawer")).toBeHidden();
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.getAttribute("aria-label")))
    .toBe("Open Studio PC details");
});
