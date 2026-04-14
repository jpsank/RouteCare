import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    ignoreHTTPSErrors: true,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  // Assumes `bin/dev` is already running on :3000 and the deterministic
  // test user has been seeded via `bin/rails playwright:seed_user`.
  // Set PLAYWRIGHT_AUTOSTART=1 to let Playwright manage the dev server.
  webServer: process.env.PLAYWRIGHT_AUTOSTART
    ? {
        command: "bin/rails playwright:seed_user && bin/dev",
        url: "http://localhost:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      }
    : undefined,
});
