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

  test(`Compose Files renders and opens deploy controls in the ${theme} theme`, async ({ page }) => {
    await login(page, { theme });
    await page.locator('[data-page="compose-files"]').click();
    await expect(page.locator("#compose-stack-list")).toContainText("Web stack");
    await expect(page.locator("#compose-settings")).toContainText("/var/lib/sextant/docker/compose");
    await page.locator("#compose-stack-list").getByRole("button", { name: "Open" }).click();
    await expect(page.locator("#compose-stack-detail")).toBeVisible();
    await expect(page.locator("#compose-body .compose-editor")).toHaveValue(/nginx:latest/);
    await page.locator('#compose-tabs [data-compose-tab="revisions"]').click();
    await expect(page.locator("#compose-body")).toContainText("eeeeeeeeeeee");
    await page.locator("#compose-body").getByRole("button", { name: "Deploy" }).first().click();
    await expect(page.locator("#docker-dialog")).toBeVisible();
    await expect(page.locator("#docker-dialog")).toContainText("Project name");
    await expect(page.locator("#docker-dialog")).toContainText("Docker hosts (max 20)");
    await expect(page.locator('#docker-dialog input[type="checkbox"][value]')).toHaveCount(1);
    await page.locator("#docker-dialog").getByRole("button", { name: "Close" }).click();
    await screenshot(page, `compose-files-${theme}`);
    await scan(page, `compose files ${theme}`);
  });

  test(`Docker Hosts auto-refreshes, shows drift, and opens a safe clone dialog in the ${theme} theme`, async ({ page }) => {
    await login(page, { theme });
    await page.locator('[data-page="docker-hosts"]').click();
    await expect(page.locator("#docker-host-list")).toContainText("rev");
    const beforeRev = Number((await page.locator("#docker-host-list").innerText()).match(/rev (\d+)/)?.[1]);
    await page.evaluate(() => {
      window.__fastDockerRefresh = true;
      window.__autoDockerRefresh = true;
    });
    await page.locator("#docker-host-list").getByRole("button", { name: "Open" }).click();
    await expect.poll(async () => {
      const text = await page.locator("#docker-host-list").innerText();
      return Number(text.match(/rev (\d+)/)?.[1]);
    }).toBeGreaterThan(beforeRev);
    await page.evaluate(() => {
      window.__fastDockerRefresh = false;
      window.__autoDockerRefresh = false;
    });
    await page.locator('#docker-host-tabs [data-docker-tab="containers"]').click();
    await expect(page.locator("#docker-host-body .status-pill.warning", { hasText: "drift" })).toBeVisible();
    await page.locator("#docker-host-body").getByRole("button", { name: "Clone" }).first().click();
    const dialog = page.locator("#docker-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Host bind mounts");
    await expect(dialog).toContainText("Compose-managed");
    await expect(dialog.locator("#docker-dialog-body input").first()).toBeFocused();
    await dialog.getByRole("button", { name: "Clone", exact: true }).click();
    await expect(page.locator("#docker-dialog-error")).toContainText("Acknowledge Compose drift");
    await screenshot(page, `docker-clone-${theme}`);
    await scan(page, `docker clone ${theme}`);
    await dialog.getByRole("button", { name: "Close" }).click();
  });

  test(`Docker console opens, renders output, and closes in the ${theme} theme`, async ({ page }) => {
    await login(page, { theme });
    await page.locator('[data-page="docker-hosts"]').click();
    await page.locator("#docker-host-list").getByRole("button", { name: "Open" }).click();
    await page.locator('#docker-host-tabs [data-docker-tab="containers"]').click();
    await page.getByRole("button", { name: "Console" }).first().click();
    await expect(page.locator("#docker-console-dialog")).toBeVisible();
    await expect(page.locator("#docker-console-banner")).toContainText("runs inside the container");
    await page.locator("#docker-console-start").click();
    await expect(page.locator("#docker-console-live")).toBeVisible();
    await page.evaluate(() => {
      const source = [...window.__eventSources].reverse().find((item) => item.url.includes("/api/docker/console/con_browser/events"));
      source.dispatch("status", { type: "status", data: JSON.stringify({ status: "open", idle_seconds: 900 }) });
      source.dispatch("output", { type: "output", data: JSON.stringify({ data: btoa("hello from console\n") }) });
    });
    await expect(page.locator("#docker-console-output")).toContainText("hello from console");
    await screenshot(page, `docker-console-${theme}`);
    await scan(page, `docker console ${theme}`);
    await page.locator("#docker-console-close").click();
    await expect(page.locator("#confirm-dialog")).toBeVisible();
    await page.locator("#confirm-accept").click();
    await expect(page.locator("#docker-console-dialog")).toBeHidden();
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

test("Docker console modal renders at the mobile viewport", async ({ page }) => {
  await login(page, { width: 390, height: 844 });
  await page.evaluate(() => document.querySelector('[data-page="docker-hosts"]').click());
  await page.locator("#docker-host-list").getByRole("button", { name: "Open" }).click();
  await page.locator('#docker-host-tabs [data-docker-tab="containers"]').click();
  await page.getByRole("button", { name: "Console" }).first().click();
  await expect(page.locator("#docker-console-dialog")).toBeVisible();
  await page.locator("#docker-console-start").click();
  await expect(page.locator("#docker-console-live")).toBeVisible();
  await page.evaluate(() => {
    const source = [...window.__eventSources].reverse().find((item) => item.url.includes("/api/docker/console/con_browser/events"));
    source.dispatch("status", { type: "status", data: JSON.stringify({ status: "open", idle_seconds: 900 }) });
    source.dispatch("output", { type: "output", data: JSON.stringify({ data: btoa("mobile console\n") }) });
  });
  await expect(page.locator("#docker-console-output")).toContainText("mobile console");
  await screenshot(page, "docker-console-dark-mobile");
  await scan(page, "docker console dark mobile");
});

test("Docker Hosts exposes cached inspect and cleanup confirmations", async ({ page }) => {
  await login(page);
  await page.locator('[data-page="docker-hosts"]').click();
  await expect(page.locator("#docker-host-list")).toContainText("Build Server");
  await page.locator("#docker-host-list").getByRole("button", { name: "Open" }).click();
  await expect(page.locator("#docker-host-detail")).toBeVisible();
  await expect(page.locator("#docker-host-title")).toHaveText("Build Server");
  await expect(page.locator(".docker-cleanup-card")).toHaveCount(6);
  await page.locator('#docker-host-tabs [data-docker-tab="containers"]').click();
  await expect(page.locator("#docker-host-body")).toContainText("web-1");
  await page.locator("#docker-host-body").getByRole("button", { name: "Inspect" }).first().click();
  await expect(page.locator("#docker-dialog")).toBeVisible();
  await expect(page.locator("#docker-dialog")).toContainText("Container overview");
  await page.locator("#docker-dialog").getByRole("button", { name: "Close" }).click();
  await page.locator('#docker-host-tabs [data-docker-tab="overview"]').click();
  await expect(page.locator(".docker-cleanup-card")).toHaveCount(6);

  const cleanupCard = page.locator(".docker-cleanup-card", { hasText: "Dangling images" });
  await cleanupCard.getByRole("button", { name: "Review" }).click();
  await expect(page.locator("#confirm-dialog")).toBeVisible();
  await expect(page.locator("#confirm-dialog")).toContainText("Clean up dangling images?");
  await page.locator("#confirm-dialog").getByRole("button", { name: "Cancel" }).click();
  await screenshot(page, "docker-hosts-dark");
  await scan(page, "docker hosts dark");
});

test("Ctrl/Cmd+K opens, filters, activates, and closes the command palette", async ({ page }) => {
  await login(page);
  const palette = page.locator("#command-palette");
  const input = page.locator("#command-palette-input");
  const list = page.locator("#command-palette-list");

  await page.keyboard.press("Control+k");
  await expect(palette).toBeVisible();
  await expect(input).toBeFocused();
  await expect(list.getByRole("option")).toHaveCount(13);
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
