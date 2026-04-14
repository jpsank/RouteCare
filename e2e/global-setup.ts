import { execSync } from "node:child_process";

export default async function globalSetup() {
  if (process.env.PLAYWRIGHT_SKIP_SEED) return;
  const tasks = ["playwright:seed_user", "playwright:seed_fresh_user", "playwright:reset_main_user"];
  for (const task of tasks) {
    execSync(`bin/rails ${task}`, { stdio: "inherit" });
  }
}
