# Deploying RouteCare

This guide covers deploying RouteCare to **Railway** (primary) or **Render** (alternative).

---

## Railway (recommended)

### 1. Create a Railway account

Go to [railway.com](https://railway.com) and sign up (GitHub login is easiest).

### 2. Create a new project

1. Click **New Project → Deploy from GitHub Repo**
2. Select `jpsank/RouteCare`
3. Railway detects the `Dockerfile` and starts building

### 3. Add a PostgreSQL database

1. In your project, click **New → Database → Add PostgreSQL**
2. Railway auto-injects `DATABASE_URL` into your app service

### 4. Set environment variables

Go to your app service → **Variables** and add:

| Variable | Value / Where to get it |
|---|---|
| `RAILS_MASTER_KEY` | Contents of `config/master.key` (`cat config/master.key`) |
| `RAILS_ENV` | `production` |
| `SOLID_QUEUE_IN_PUMA` | `true` |
| `RAILS_SERVE_STATIC_FILES` | `true` |
| `RAILS_LOG_LEVEL` | `info` |
| `ROUTECARE_TELNYX_API_KEY` | [Telnyx Portal](https://portal.telnyx.com) → API Keys |
| `ROUTECARE_TELNYX_FROM_NUMBER` | Your Telnyx phone number, e.g. `+15551234567` |
| `ROUTECARE_TELNYX_PUBLIC_KEY` | Telnyx Portal → Webhook settings (Ed25519 public key) |
| `ROUTECARE_POSTMARK_API_KEY` | [Postmark](https://postmarkapp.com) → Server → API Tokens |
| `ROUTECARE_POSTMARK_INBOUND_TOKEN` | Token for verifying inbound email webhooks |
| `ROUTECARE_MAILER_FROM` | e.g. `care@yourdomain.com` |
| `ROUTECARE_APP_HOST` | Your app URL, e.g. `routecare-production.up.railway.app` |
| `ROUTECARE_LLM_API_KEY` | [OpenAI](https://platform.openai.com/api-keys) (optional — AI-drafted messages) |
| `ROUTECARE_LLM_BASE_URL` | `https://api.openai.com/v1` |
| `ROUTECARE_LLM_MODEL` | `gpt-4o-mini` |
| `GOOGLE_OAUTH_CLIENT_ID` | [Google Cloud Console](https://console.cloud.google.com) (optional — calendar sync) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google Cloud Console (optional — calendar sync) |

`DATABASE_URL` is auto-injected by Railway when you link the Postgres service — no need to set it manually.

### 5. Set webhook URLs

After the first successful deploy, your app URL will be shown in Railway (e.g. `https://routecare-production.up.railway.app`).

**Telnyx SMS webhook:**
In the [Telnyx Portal](https://portal.telnyx.com) → Messaging → your profile → set webhook URL to:
```
https://YOUR-APP.up.railway.app/webhooks/telnyx/sms
```

**Postmark inbound email webhook:**
In [Postmark](https://postmarkapp.com) → Server → Inbound → set webhook URL to:
```
https://YOUR-APP.up.railway.app/webhooks/postmark/inbound
```

### 6. Deploy

Every push to `main` triggers a new deploy. Railway builds the Docker image, runs the entrypoint (which runs `db:prepare`), and starts the server.

---

## Render (alternative)

### 1. Create a Render account

Go to [render.com](https://render.com) and sign up (GitHub login is easiest).

### 2. Deploy via Blueprint

1. Click **New → Blueprint** → connect GitHub → select `jpsank/RouteCare`
2. Render reads `render.yaml` and creates: **1 web service** + **1 PostgreSQL database**
3. Click **Apply**

### 3. Set environment variables

Go to your **routecare** web service → **Environment** and add the same variables listed in the Railway section above, plus:

| Variable | Value |
|---|---|
| `RAILS_MASTER_KEY` | Contents of `config/master.key` |

`DATABASE_URL` is auto-wired from the Render database — no need to set manually.

### 4. Auto-deploy

Every push to `main` triggers a new deploy via `bin/render-build.sh`.

---

## Troubleshooting

**Build fails on `rails assets:precompile`**
→ Ensure Node.js 22.12+ is being used. Railway uses the Dockerfile (pinned to 22.16.0). Render uses the `NODE_VERSION` env var.

**App boots but shows "We're sorry, but something went wrong"**
→ Check logs. Most likely a missing env var — especially `RAILS_MASTER_KEY`.

**`relation "solid_queue_recurring_tasks" does not exist`**
→ The Solid Queue migration hasn't run. Manually run `rails db:migrate` via the platform's shell/console, or redeploy to trigger `db:prepare` in the entrypoint.

**Solid Queue not processing jobs**
→ Confirm `SOLID_QUEUE_IN_PUMA=true` is set.
