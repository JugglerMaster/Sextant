const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./browser",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30000,
  expect: { timeout: 5000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    viewport: { width: 1440, height: 960 },
    colorScheme: "dark",
    timezoneId: "UTC",
    locale: "en-US",
    deviceScaleFactor: 1,
    screenshot: "off",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node browser/server.js",
    url: "http://127.0.0.1:4173/",
    cwd: __dirname,
    reuseExistingServer: false,
    timeout: 15000,
  },
});
