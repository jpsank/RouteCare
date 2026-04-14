import { test, expect } from "@playwright/test";

const EMAIL = process.env.PLAYWRIGHT_FRESH_EMAIL ?? "playwright-fresh@example.com";
const PASSWORD = process.env.PLAYWRIGHT_USER_PASSWORD ?? "playwright-password-123";

test.describe("setup wizard", () => {
  test("completes the wizard happy path and lands on the main app", async ({ page }) => {
    await page.goto("/users/sign_in");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/password/i).first().fill(PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();

    // Step 0: welcome + display name
    await expect(page.getByRole("heading", { name: /let's get you set up/i })).toBeVisible({ timeout: 15000 });
    await page.getByPlaceholder("Dr. Jane Smith").fill("Playwright Fresh");
    await page.getByRole("button", { name: /continue/i }).click();

    // Step 1: working days & hours (defaults are fine)
    await expect(page.getByRole("heading", { name: /your work schedule/i })).toBeVisible();
    await page.getByRole("button", { name: /continue/i }).click();

    // Step 2: choose "add my own patients" to avoid triggering the solver
    await expect(page.getByRole("heading", { name: /add your patients/i })).toBeVisible();
    await page.getByRole("button", { name: /i'll add my own patients/i }).click();

    // Main app renders the RouteCare header brand.
    await expect(page.getByText("RouteCare").first()).toBeVisible({ timeout: 15000 });
  });
});
