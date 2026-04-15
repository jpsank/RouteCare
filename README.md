# RouteCare

RouteCare is a route-optimized field scheduling platform for home-visit physical therapists, occupational therapists, and other field clinicians.

It solves three core workflow gaps:

1. Weekly schedules are usually built manually without geography/travel awareness.
2. Patient time confirmation requires repetitive back-and-forth over SMS/email.
3. Clinicians need their external calendar and route plan kept in sync.

## Tech stack

- Rails 8.1 + Ruby 3.4
- PostgreSQL 16
- Solid Queue / Solid Cache / Solid Cable (single DB, no Redis)
- React 19 + TypeScript via Vite 8, Tailwind CSS 3
- TanStack Query, react-hook-form + zod, Recharts
- Devise authentication
- Prawn + prawn-table for PDF export
- Sentry error tracking (Ruby + JS)
- Playwright E2E tests, Vitest unit tests
- Python CP-SAT microservice (`solver_service/`) using OR-Tools + LNS for schedule optimization
- Service object architecture in `app/services`
- JSON API under `app/controllers/api/v1`

## Current V1 Scope

- Patient roster management (CRUD + deactivation), CSV and Excel (`.xlsx`/`.xls`) import
- Weekly schedule optimization with:
  - hard calendar block constraints
  - per-day patient availability windows
  - route/travel-time-aware ordering with precomputed travel matrix
  - CP-SAT solver with Large Neighborhood Search (Shaw + worst-vehicle destroy operators, adaptive weights, warm-start partial repair)
  - automatic fallback to a Ruby greedy optimizer if the solver service is unreachable
  - optimized vs baseline drive-time metrics
- Visit editing and quick rescheduling
- Patient communication lifecycle:
  - outbound confirmation drafts, approval, queueing, and dispatch
  - clinician display name support in generated outbound messages
  - inbound intent parsing (confirm/decline/reschedule)
  - lightweight availability extraction from reschedule replies
  - automated follow-up drafting when patients ask to reschedule
  - route-aware suggested reschedule slots based on the current weekly plan
  - clinician-reviewed or auto-sent follow-up based on communication settings
- Alerts pipeline:
  - unconfirmed visits within 48h
  - calendar conflict detection
  - significant schedule-shift notifications
- Core audit logging model and events
- Mobile-friendly route/calendar frontend views

## Project Structure

```text
app/
  controllers/api/v1/      # Thin JSON controllers
  controllers/webhooks/    # Telnyx SMS + Postmark email inbound webhooks
  models/                  # Domain models and validations
  services/                # Business logic / integrations
  services/integrations/   # External API clients (Telnyx, LLM, geocoding, routing)
  services/messaging/      # Outbound dispatch, inbound reply processing
  serializers/             # API payload serializers
  jobs/                    # Async jobs (delivery, alerts, cleanup)
  frontend/                # React + TypeScript UI
db/
  migrate/                 # RouteCare schema (incl. Solid Queue tables)
solver_service/            # Python CP-SAT microservice (FastAPI + OR-Tools)
e2e/                       # Playwright end-to-end tests
config/recurring.yml       # Solid Queue scheduled tasks
```

## Local Development

### 1) Prerequisites

- Ruby 3.4+
- Node 22+
- PostgreSQL 16+
- Python 3.11+ (optional, for the CP-SAT solver service)

### 2) Install dependencies

```bash
bundle install
npm install
gem install foreman
```

### 3) Database setup

```bash
bin/rails db:prepare
```

### 4) Run app + Vite

```bash
bin/dev
```

Rails runs on port `3000`, Vite dev server on `3036`.

### 5) (Optional) Run the CP-SAT solver service

```bash
cd solver_service
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Then point Rails at it with `PYTHON_SOLVER_URL=http://localhost:8000`. Without it, Rails falls back to the greedy Ruby optimizer.

## Common commands

```bash
bin/dev              # Start dev server (Rails + Vite)
bin/rails test       # Run Rails tests
bin/rubocop          # Lint (rubocop-rails-omakase)
bin/brakeman         # Security scan
bin/bundler-audit    # Gem vulnerability scan
npm run test         # Vitest unit/component tests
npm run e2e          # Playwright E2E (requires bin/dev running)
bin/rails playwright:seed_user  # Seed the E2E test user
```

## Key API Endpoints

- `GET /api/v1/patients`
- `POST /api/v1/patients`
- `PATCH /api/v1/patients/:id`
- `POST /api/v1/patients/:id/deactivate`
- `POST /api/v1/patients/import` (CSV or Excel)
- `GET /api/v1/schedule?week_start_on=YYYY-MM-DD`
- `POST /api/v1/schedule/optimize`
- `POST /api/v1/schedule/approve`
- `GET /api/v1/visits`
- `PATCH /api/v1/visits/:id`
- `POST /api/v1/visits/:id/reschedule`
- `GET /api/v1/messages`
- `POST /api/v1/messages`
- `POST /api/v1/messages/:id/approve`
- `POST /api/v1/messages/:id/select_suggestion`
- `GET /api/v1/calendar_blocks`
- `POST /api/v1/calendar_blocks`
- `GET /api/v1/alerts`
- `PATCH /api/v1/alerts/:id`
- `POST /webhooks/telnyx` (inbound SMS)
- `POST /webhooks/postmark` (inbound email)

## Integrations

- **SMS:** Telnyx (`app/services/integrations/telnyx_sms_client.rb`, Ed25519 webhook signature verification)
- **Email:** Postmark via `postmark-rails` gem + ActionMailer (token-based webhook auth)
- **Calendar:** Google + Outlook (`app/services/integrations/calendar`)
- **Routing/geocoding:** `app/services/integrations/routing_client.rb`, `geocoding_client.rb`
- **LLM-assisted drafting and reply interpretation:** `app/services/integrations/llm_client.rb`

Safe local fallbacks/mocks are included for development and testing.

### Background jobs

- `AlertsGenerationJob` — scans clinician users and creates/updates alerts for unconfirmed visits, calendar conflicts, and significant schedule changes. Scheduled via `config/recurring.yml`.

## Environment variables

Required for production:

- `RAILS_MASTER_KEY`
- `DATABASE_URL`
- `RAILS_ENV=production`
- `SOLID_QUEUE_IN_PUMA=true`
- `ROUTECARE_APP_HOST`

Optional:

- `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`
- `VITE_SENTRY_DSN`, `VITE_SENTRY_TRACES_SAMPLE_RATE`
- `ROUTECARE_POSTMARK_API_KEY`
- `ROUTECARE_TELNYX_API_KEY`, `ROUTECARE_TELNYX_FROM_NUMBER`
- `ROUTECARE_LLM_API_KEY`, `ROUTECARE_LLM_BASE_URL`, `ROUTECARE_LLM_MODEL`
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`

Scheduling / CP-SAT microservice:

- `PYTHON_SOLVER_URL` — base URL of the `solver_service/` FastAPI app (default `http://localhost:8000`). On Railway private DNS, include the port.
- `ROUTECARE_SCHEDULER_BACKEND` — `cpsat` (default) or `greedy`. Automatic fallback to greedy on solver transport/HTTP errors, exposed via `optimization_summary.scheduler_fallback`.
- `ROUTECARE_CPSAT_TIME_BUDGET` — CP-SAT wall time in seconds (10–7200). Overrides the quality preset.
- `ROUTECARE_SCHEDULE_QUALITY` — `fast` (30s), `balanced` (60s, default), or `deep` (120s).

## Deployment

- **Railway:** Docker-based; Rails and `solver_service/` deploy as two services. Both `railway.toml` files set watch paths so solver-only or Rails-only commits don't rebuild the other service. `bin/railway-add-solver-service` creates the solver service; set its Root Directory and config path in the Railway dashboard.
- **Render:** `render.yaml` + `bin/render-build.sh`.
- No Thruster in production — Railway/Render terminate SSL; Puma listens directly on `PORT`.

## Security/HIPAA Notes

RouteCare is designed to support HIPAA-oriented patterns:

- encrypted transport (TLS in deployment)
- strict authentication + user scoping in APIs
- parameter filtering for sensitive fields
- audit log capture for critical workflow actions
- signed inbound webhooks (Telnyx Ed25519, Postmark token)

Production hardening still required before live PHI workloads:

- encryption at rest + key management policy
- BAA-backed vendor configuration for all integrations
- role-based authorization expansion
- comprehensive audit retention/export policy

## Tests

```bash
bin/rails test         # Rails unit + system tests
bundle exec rubocop    # Ruby lint
npm run test           # Vitest
npm run e2e            # Playwright (requires bin/dev)
cd solver_service && pytest   # CP-SAT solver tests
npm run build          # Frontend build check
```

CI runs on GitHub Actions: security scan, lint, unit tests, system tests, and a dedicated solver job running the full pytest suite including slow benchmarks.
