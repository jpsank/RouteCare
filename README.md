# RouteCare

RouteCare is a route-optimized field scheduling platform for home-visit physical therapists, occupational therapists, and other field clinicians.

It solves three core workflow gaps:

1. Weekly schedules are usually built manually without geography/travel awareness.
2. Patient time confirmation requires repetitive back-and-forth over SMS/email.
3. Clinicians need their external calendar and route plan kept in sync.

This repository is built with a TariffNinja-style stack: <!-- pragma: allowlist secret -->

- Rails 8 + Ruby 3.4
- PostgreSQL 16
- Solid Queue / Solid Cache / Solid Cable
- React + TypeScript frontend via Vite
- Service object architecture in `app/services`
- JSON API under `app/controllers/api/v1`

## Current V1 Scope

Implemented foundation:

- Patient roster management (CRUD + deactivation)
- Weekly schedule optimization with:
  - hard calendar block constraints
  - patient availability window preference
  - route/travel-time-aware ordering
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
  models/                  # Domain models and validations
  services/                # Business logic / integrations
  serializers/             # API payload serializers
  frontend/                # React + TypeScript UI
db/
  migrate/                 # RouteCare schema
```

## Local Development

### 1) Prerequisites

- Ruby 3.4+
- Node 22+
- PostgreSQL 16+

### 2) Install dependencies

```bash
bundle install
npm install
gem install foreman
```

### 3) Database setup

Update `config/database.yml` credentials as needed, then:

```bash
bin/rails db:prepare
```

### 4) Run app + Vite

```bash
bin/dev
```

`bin/dev` uses `Procfile.dev` via Foreman to run both Rails and Vite.
Rails runs on port `3000`, Vite dev server on `3036`.

## Key API Endpoints

- `GET /api/v1/patients`
- `POST /api/v1/patients`
- `PATCH /api/v1/patients/:id`
- `POST /api/v1/patients/:id/deactivate`
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
- `POST /api/v1/messages/inbound`
- `GET /api/v1/calendar_blocks`
- `POST /api/v1/calendar_blocks`
- `GET /api/v1/alerts`
- `PATCH /api/v1/alerts/:id`
- `POST /webhooks/twilio/sms` (Twilio inbound SMS webhook)
- `POST /webhooks/mailgun/inbound` (Mailgun inbound email webhook)

## Integrations (Pluggable Boundaries)

Service boundaries are already in place for:

- Calendar: Google + Outlook (`app/services/integrations/calendar`)
- Routing/geocoding (`app/services/integrations/routing_client.rb`, `geocoding_client.rb`)
- Messaging dispatch (Twilio/email provider adapter entrypoint is `MessageDeliveryJob`)
- LLM-assisted message drafting and reply interpretation (`app/services/integrations/llm_client.rb`)

The current implementation includes safe local fallbacks/mocks for development and testing.

### Background jobs

- `AlertsGenerationJob` — scans all clinician users and creates/updates alerts for unconfirmed visits, calendar conflicts, and significant schedule changes. Run periodically (e.g., every 15 minutes via cron or `recurring` Solid Queue config).

### Optional LLM configuration

Natural-language messaging can use an OpenAI-compatible chat API when configured:

- `ROUTECARE_LLM_API_KEY`
- `ROUTECARE_LLM_BASE_URL` (optional, default `https://api.openai.com/v1`)
- `ROUTECARE_LLM_MODEL` (optional, default `gpt-4o-mini`)

If these are not set (or the provider call fails), RouteCare falls back to deterministic local templates and rule-based parsing.

### Messaging delivery configuration

To send real outbound messages, configure SMS and/or SMTP:

- SMS (Twilio)
  - `ROUTECARE_TWILIO_ACCOUNT_SID`
  - `ROUTECARE_TWILIO_AUTH_TOKEN`
  - `ROUTECARE_TWILIO_FROM_NUMBER` (E.164 format, e.g. `+15551234567`)
- Email (SMTP + mailer sender)
  - `ROUTECARE_MAILER_FROM` (e.g. `care@yourdomain.com`)
  - `ROUTECARE_SMTP_ADDRESS`
  - `ROUTECARE_SMTP_PORT` (default `587`)
  - `ROUTECARE_SMTP_USERNAME`
  - `ROUTECARE_SMTP_PASSWORD`
  - `ROUTECARE_SMTP_DOMAIN` (optional)
  - `ROUTECARE_SMTP_AUTH` (default `plain`)
  - `ROUTECARE_SMTP_STARTTLS` (default `true`)

If delivery is not configured for a selected channel, queued messages will be marked `failed` with a provider error in message metadata.

## Security/HIPAA Notes

RouteCare is designed to support HIPAA-oriented patterns:

- encrypted transport (TLS in deployment)
- strict authentication + user scoping in APIs
- parameter filtering for sensitive fields
- audit log capture for critical workflow actions

Production hardening still required before live PHI workloads:

- encryption at rest + key management policy
- BAA-backed vendor configuration for all integrations
- role-based authorization expansion
- comprehensive audit retention/export policy

## Tests

Run the main verification checks:

```bash
bin/rails test
bundle exec rubocop
npm run build
```
