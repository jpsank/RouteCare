import { test, expect } from "@playwright/test";

const EMAIL = process.env.PLAYWRIGHT_USER_EMAIL ?? "playwright@example.com";
const PASSWORD = process.env.PLAYWRIGHT_USER_PASSWORD ?? "playwright-password-123";

test.describe("authenticated dashboard", () => {
  test("signs in and lands on the weekly calendar", async ({ page }) => {
    await page.goto("/users/sign_in");

    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/password/i).first().fill(PASSWORD);
    await Promise.all([
      page.waitForURL((url) => !url.pathname.startsWith("/users/sign_in"), { timeout: 15000 }),
      page.getByRole("button", { name: /log in|sign in/i }).click(),
    ]);

    // Main app renders the "RouteCare" header brand.
    await expect(page.getByText(/RouteCare/i).first()).toBeVisible({ timeout: 15000 });
  });

  test("sign-in form rejects bad credentials", async ({ page }) => {
    await page.goto("/users/sign_in");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/password/i).first().fill("wrong-password");
    await page.getByRole("button", { name: /log in|sign in/i }).click();

    // Devise redirects back to sign_in with a flash error.
    await expect(page).toHaveURL(/\/users\/sign_in/);
  });
});
