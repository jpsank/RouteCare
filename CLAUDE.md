# CLAUDE.md

## Project overview

RouteCare is a route-optimized field scheduling platform for home-visit clinicians (PTs, OTs, etc.). Rails 8.1 backend with a React/TypeScript frontend built with Vite.

## Tech stack

- **Backend:** Ruby 3.4 / Rails 8.1, PostgreSQL, Solid Queue/Cache/Cable (single DB)
- **Frontend:** React 19, TypeScript, Vite 8, Tailwind CSS 3
- **Email:** Postmark (via `postmark-rails` gem + ActionMailer)
- **SMS:** Telnyx (custom REST client in `app/services/integrations/telnyx_sms_client.rb`)
- **Auth:** Devise
- **Deploy:** Railway (Docker) and Render (`render.yaml` + `bin/render-build.sh`)

## Common commands

```bash
bin/dev              # Start dev server (Rails + Vite)
bin/rails test       # Run tests
bin/rubocop          # Lint (rubocop-rails-omakase style)
bin/brakeman         # Security scan
bin/bundler-audit    # Gem vulnerability scan
```

## Code layout

- `app/frontend/` — React components, styles (Tailwind + custom CSS in `styles/`)
- `app/services/integrations/` — External API clients (Telnyx, LLM, geocoding, routing)
- `app/services/messaging/` — Outbound dispatch, inbound reply processing
- `app/controllers/webhooks/` — Inbound webhooks (Telnyx SMS, Postmark email)
- `app/jobs/` — Async jobs (message delivery, alerts generation, cleanup)
- `config/recurring.yml` — Solid Queue scheduled tasks

## Architecture decisions

- **Single database** for everything including Solid Queue/Cache/Cable tables (created via migration in `db/migrate/20260405000001_create_solid_queue_tables.rb`)
- **No Redis** — Solid Stack replaces Redis for jobs, cache, and WebSockets
- **No Thruster** in production — Railway/Render handle SSL termination; Puma listens directly on `PORT`
- Webhook signature verification: Telnyx uses Ed25519 (`ed25519` gem), Postmark uses token-based auth

## Style conventions

- Ruby: rubocop-rails-omakase (spaces inside array brackets, etc.)
- CSS: Tailwind utility classes preferred; custom component classes use `rc-` prefix in `app/frontend/styles/tailwind.css`; app-level styles in `app/frontend/styles/app.css`
- Frontend: React functional components with TypeScript

## Testing

- CI runs on GitHub Actions: security scan, lint, unit tests, system tests
- Tests use PostgreSQL (not SQLite)
- `DATABASE_URL` env var required for test DB connection

## Environment variables

Required for production (set in Railway/Render dashboard):
- `RAILS_MASTER_KEY` — decrypts `config/credentials.yml.enc`
- `DATABASE_URL` — auto-injected by Railway/Render
- `RAILS_ENV=production`
- `SOLID_QUEUE_IN_PUMA=true`
- `ROUTECARE_APP_HOST` — app domain for mailer URLs

Optional:
- `ROUTECARE_POSTMARK_API_KEY` — enables email delivery
- `ROUTECARE_TELNYX_API_KEY`, `ROUTECARE_TELNYX_FROM_NUMBER` — enables SMS
- `ROUTECARE_LLM_API_KEY` — enables AI-drafted messages
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` — enables calendar sync

**Scheduling / Python CP-SAT microservice** (`PYTHON_SOLVER_URL`, default `http://localhost:8000`):
- **Railway:** deploy `solver_service/` as a second service (root directory `solver_service`, config `solver_service/railway.toml`), assign it a domain, then set `PYTHON_SOLVER_URL` on the Rails service to that base URL (see comments in repo `railway.toml`). Both `railway.toml` files set **watch paths** so solver-only vs Rails-only commits don’t rebuild the other service.
- `ROUTECARE_SCHEDULER_BACKEND` — `greedy` (default Ruby optimizer) or `cpsat` (Python OR-Tools pipeline). On CP-SAT transport/HTTP failures, falls back to greedy and sets `optimization_summary.scheduler_fallback`.
- `ROUTECARE_CPSAT_TIME_BUDGET` — CP-SAT wall time in seconds (10–7200). If set, overrides the quality preset below.
- `ROUTECARE_SCHEDULE_QUALITY` — when `ROUTECARE_CPSAT_TIME_BUDGET` is unset: `fast` (30s), `balanced` (60s), `deep` (120s).
- Test-only: `ROUTECARE_TEST_CPSAT_FAIL=1` with `RAILS_ENV=test` forces a simulated `RemoteSolverError` to exercise fallback.
