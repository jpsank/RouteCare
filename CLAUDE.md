# CLAUDE.md

## Project overview

RouteCare is a route-optimized field scheduling platform for home-visit clinicians (PTs, OTs, etc.). Rails 8.1 backend with a React/TypeScript frontend built with Vite.

## Tech stack

- **Backend:** Ruby 3.4 / Rails 8.1, PostgreSQL, Solid Queue/Cache/Cable (single DB)
- **Frontend:** React 19, TypeScript, Vite 8, Tailwind CSS 3
- **Frontend libs:** TanStack Query, react-hook-form + zod, Recharts
- **Email:** Postmark (via `postmark-rails` gem + ActionMailer)
- **SMS:** Telnyx (custom REST client in `app/services/integrations/telnyx_sms_client.rb`)
- **Auth:** Devise
- **PDF:** Prawn + prawn-table
- **Error tracking:** Sentry (Ruby: `sentry-rails`; JS: `@sentry/react`)
- **E2E tests:** Playwright (`e2e/`, config at `playwright.config.ts`)
- **Git hooks:** Husky + lint-staged (rubocop autofix on staged `*.rb`)
- **Deploy:** Railway (Docker) and Render (`render.yaml` + `bin/render-build.sh`)

## Common commands

```bash
bin/dev              # Start dev server (Rails + Vite)
bin/rails test       # Run Rails tests
bin/rubocop          # Lint (rubocop-rails-omakase style)
bin/brakeman         # Security scan
bin/bundler-audit    # Gem vulnerability scan
npm run test         # Run Vitest unit/component tests
npm run test:watch   # Vitest in watch mode
bin/rails playwright:seed_user  # Seed the E2E test user (idempotent)
npm run e2e          # Run Playwright E2E tests (requires bin/dev running on :3000)
npm run e2e:install  # One-time: download Playwright browsers
```

## Code layout

- `app/frontend/` — React components, styles (Tailwind + custom CSS in `styles/`)
- `app/services/integrations/` — External API clients (Telnyx, LLM, geocoding, routing)
- `app/services/messaging/` — Outbound dispatch, inbound reply processing
- `app/controllers/webhooks/` — Inbound webhooks (Telnyx SMS, Postmark email)
- `app/jobs/` — Async jobs (message delivery, alerts generation, cleanup)
- `config/recurring.yml` — Solid Queue scheduled tasks
- `solver_service/` — Python Benders-hybrid scheduler microservice (FastAPI, OR-Tools CP-SAT, pytest benchmarks)

## Patient import

Patient roster import accepts both CSV and Excel (`.xlsx`/`.xls`) via `POST /api/v1/patients/import` (spreadsheet parsing via the `roo` gem).

## Solver architecture

The scheduler is a **Benders decomposition hybrid** (`solver_service/solver/benders/`). The outer loop alternates:

1. **Envelope (master)** — CP-SAT assigns each visit a `(clinician, day, window)` slot (`envelope.py`). Uses a home-leg cost approximation on round 0; subsequent rounds get per-`(instance, clinician, day)` detour marginals fed back from the subproblem.
2. **Subproblems** — one per-vehicle routing solve per `(clinician, day)` (`subproblem.py`). Returns either a feasible `VehicleRoute` or a `Conflict`.
3. **No-good cuts** — conflicts are translated into cuts (`cuts.py`) and added to the envelope's `CutStore` for the next round.
4. Loop until all vehicles feasible, `MAX_BENDERS_ROUNDS` hits, or the time budget runs out. Warm-start from a prior `SolverOutput` is supported on round 0.
5. **Concrete timing pass** (`cpsat_timing.py`) assigns final start/end times per route, interleaving locked visits, lunch, breaks, and calendar blocks.
6. **`validate_plan`** is the final safety gate.

After Benders converges on a feasible incumbent, an optional **ALNS polish** (`lns.py`) runs destroy/repair with four operators (`random`, `worst_cost`, `shaw`, `worst_vehicle`) and adaptive weighting (classic ALNS reaction update every 5 iterations). Hill-climbing acceptance only — never worsens the incumbent. Skipped for warm-started re-solves (continuity with the prior plan wins over a few percent of drive savings) and for small problems (< 15 placed visits). Tunables via `SOLVER_LNS_*` env vars.

On solver transport/HTTP failure, Rails automatically falls back to the greedy Ruby optimizer and records `optimization_summary.scheduler_fallback`. The solver's own tests live in `solver_service/tests/` and run as a dedicated pytest CI job (including slow benchmark suites).

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
- `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE` — enables Ruby-side error tracking
- `VITE_SENTRY_DSN`, `VITE_SENTRY_TRACES_SAMPLE_RATE` — enables frontend error tracking
- `ROUTECARE_POSTMARK_API_KEY` — enables email delivery
- `ROUTECARE_TELNYX_API_KEY`, `ROUTECARE_TELNYX_FROM_NUMBER` — enables SMS
- `ROUTECARE_LLM_API_KEY` — enables AI-drafted messages
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` — enables calendar sync

**Scheduling / Python CP-SAT microservice** (`PYTHON_SOLVER_URL`, default `http://localhost:8000`):
- **Railway:** deploy `solver_service/` as a second service (root directory `solver_service`, config `solver_service/railway.toml`), assign it a domain, then set `PYTHON_SOLVER_URL` on the Rails service (see comments in repo `railway.toml`). For **private** DNS (`http://<service>.railway.internal`), you must include the solver’s **port** (Railway sets `PORT`; Uvicorn logs show the bind address, often `:8080`) — `http://host` alone defaults to port 80 and CP-SAT will fall back to greedy. Public HTTPS base URL also works. Both `railway.toml` files set **watch paths** so solver-only vs Rails-only commits don’t rebuild the other service. From the repo root, `bin/railway-add-solver-service` runs `railway add --repo …` to create that service; you still set **Root Directory** and **config path** in the dashboard (CLI cannot set those yet).
- `ROUTECARE_SCHEDULER_BACKEND` — `cpsat` (default, Python OR-Tools pipeline) or `greedy` (Ruby fallback optimizer). On CP-SAT transport/HTTP failures, automatically falls back to greedy and sets `optimization_summary.scheduler_fallback`.
- `ROUTECARE_CPSAT_TIME_BUDGET` — CP-SAT wall time in seconds (10–7200). If set, overrides the quality preset below.
- `ROUTECARE_SCHEDULE_QUALITY` — when `ROUTECARE_CPSAT_TIME_BUDGET` is unset: `fast` (30s), `balanced` (60s), `deep` (120s).
- Test-only: `ROUTECARE_TEST_CPSAT_FAIL=1` with `RAILS_ENV=test` forces a simulated `RemoteSolverError` to exercise fallback.
