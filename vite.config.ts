import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import RubyPlugin from "vite-plugin-ruby";
import { sentryVitePlugin } from "@sentry/vite-plugin";

declare const process: { env: Record<string, string | undefined> };

// Only upload source maps when the auth token is present (production CI builds).
// Local dev builds still emit source maps but skip the upload step.
const sentryAuthToken = process.env.SENTRY_AUTH_TOKEN;
const sentryOrg = process.env.SENTRY_ORG;
const sentryProject = process.env.SENTRY_PROJECT ?? "routecare-frontend";

export default defineConfig({
  build: {
    sourcemap: true,
  },
  plugins: [
    RubyPlugin(),
    react(),
    sentryAuthToken && sentryOrg
      ? sentryVitePlugin({
          org: sentryOrg,
          project: sentryProject,
          authToken: sentryAuthToken,
          telemetry: false,
          release: process.env.SENTRY_RELEASE
            ? { name: process.env.SENTRY_RELEASE }
            : undefined,
        })
      : null,
  ],
});
