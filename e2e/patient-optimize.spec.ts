import { test, expect } from "@playwright/test";

const EMAIL = process.env.PLAYWRIGHT_USER_EMAIL ?? "playwright@example.com";
const PASSWORD = process.env.PLAYWRIGHT_USER_PASSWORD ?? "playwright-password-123";

test.describe("dashboard: seed demo patients + optimize", () => {
  test("loading demo patients from the empty state produces a schedule", async ({ page }) => {
    await page.goto("/users/sign_in");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/password/i).first().fill(PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();

    // Expect the post-login "No schedule yet" empty state (main user was reset in global setup).
    await expect(page.getByText(/no schedule yet/i)).toBeVisible({ timeout: 15000 });

    // Click "Load Demo Patients" — runs seedDemoPatients action which also optimizes.
    await page.getByRole("button", { name: /load demo patients/i }).click();

    // After the seed + optimize round-trip, the Stats popover button becomes visible
    // in the calendar header (only rendered when a schedule exists).
    await expect(page.getByRole("button", { name: /stats/i })).toBeVisible({ timeout: 60000 });
  });
});
