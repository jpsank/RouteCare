import "@vitejs/plugin-react/preamble";
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { Toaster } from "sonner";
import "mapbox-gl/dist/mapbox-gl.css";
import { App } from "../components/App";
import { Sentry, initSentry } from "../lib/sentry";
import { queryClient } from "../lib/queryClient";

initSentry();

function ErrorFallback() {
  return (
    <div className="rc-app">
      <div className="rc-shell">
        <div className="rc-card space-y-2 text-center">
          <h2 className="rc-section-title">Something went wrong</h2>
          <p className="rc-section-subtitle">
            The error has been reported. Try reloading the page.
          </p>
          <button className="btn-primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    </div>
  );
}

const rootEl = document.getElementById("root");

if (rootEl) {
  createRoot(rootEl).render(
    <React.StrictMode>
      <Sentry.ErrorBoundary fallback={<ErrorFallback />}>
        <QueryClientProvider client={queryClient}>
          <App />
          <Toaster richColors position="top-right" closeButton />
          {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />}
        </QueryClientProvider>
      </Sentry.ErrorBoundary>
    </React.StrictMode>
  );
}
