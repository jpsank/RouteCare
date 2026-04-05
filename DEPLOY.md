# Deploying RouteCare to Render.com

This guide takes you from zero to a live URL in about 15 minutes. After setup, every `git push` to `main` redeploys automatically — no manual steps ever again.

---

## Why Render?

| Need | How Render handles it |
|---|---|
| PostgreSQL | Free managed Postgres (same instance for all Solid adapters) |
| Background jobs | Solid Queue runs inside Puma — no separate worker service |
| Action Cable | Solid Cable (database-backed) — no Redis needed |
| Asset build | Ruby + Node 22 buildpack runs Vite automatically |
| Auto-deploy | Connected to GitHub; every push to `main` triggers a deploy |
| SSL | Free HTTPS at `https://routecare.onrender.com` |

**Free tier caveat:** Free web services spin down after 15 minutes of inactivity. The next request takes ~30 seconds to cold-start. For always-on access from your phone, upgrade the web service to the **Starter** plan ($7/mo) and the database to **Starter** ($7/mo) at any time.

---

## One-time setup (~15 min)

### 1. Create a Render account

Go to [render.com](https://render.com) and sign up (GitHub login is easiest).

### 2. Connect your GitHub repo

In the Render dashboard → **New → Blueprint** → connect GitHub if prompted → search for `jpsank/RouteCare` and authorize it.

### 3. Deploy via Blueprint (render.yaml)

Since this repo contains `render.yaml`, Render will offer to deploy everything automatically:

1. Click **New → Blueprint**
2. Select `jpsank/RouteCare`
3. Render reads `render.yaml` and shows you: **1 web service** + **1 PostgreSQL database**
4. Click **Apply**

Render will create both resources, wire `DATABASE_URL` automatically, and kick off the first build.

### 4. Set required environment variables

After the Blueprint is applied, go to your **routecare** web service → **Environment** and add these secrets (values come from your `.env.local` or the services listed below):

| Variable | Where to get it |
|---|---|
| `RAILS_MASTER_KEY` | Contents of `config/master.key` on your Mac (`cat config/master.key`) |
| `ROUTECARE_TWILIO_ACCOUNT_SID` | [Twilio Console](https://console.twilio.com) → Account Info |
| `ROUTECARE_TWILIO_AUTH_TOKEN` | Twilio Console → Account Info |
| `ROUTECARE_TWILIO_FROM_NUMBER` | Your Twilio phone number, e.g. `+15551234567` |
| `ROUTECARE_LLM_API_KEY` | [OpenAI Platform](https://platform.openai.com/api-keys) (optional — only needed for AI-drafted messages) |
| `ROUTECARE_SMTP_ADDRESS` | `smtp.mailgun.org` (if using Mailgun) |
| `ROUTECARE_SMTP_USERNAME` | Mailgun SMTP username |
| `ROUTECARE_SMTP_PASSWORD` | Mailgun SMTP password |
| `ROUTECARE_SMTP_DOMAIN` | Your Mailgun sending domain |
| `ROUTECARE_MAILER_FROM` | e.g. `care@mg.yourdomain.com` |
| `ROUTECARE_MAILGUN_WEBHOOK_SIGNING_KEY` | Mailgun dashboard → Webhooks |

After adding env vars, click **Save Changes** — Render will redeploy automatically.

### 5. Get RAILS_MASTER_KEY

On your Mac, in the project directory:

```bash
cat config/master.key
```

Copy the output (a 32-char hex string) and paste it as the value of `RAILS_MASTER_KEY` in the Render dashboard.

### 6. Set the Twilio webhook URL (for inbound SMS)

After the first successful deploy, your app URL will be:

```
https://routecare.onrender.com
```

In the [Twilio Console](https://console.twilio.com) → Phone Numbers → your number → **Messaging** → set **"A message comes in"** webhook to:

```
https://routecare.onrender.com/webhooks/twilio/inbound
```

(Adjust the path to match wherever your Twilio inbound route is defined in `config/routes.rb`.)

---

## Auto-deploy from GitHub

Every push to the `main` branch triggers a new deploy. The process is:

1. Render detects the push
2. Runs `bin/render-build.sh` (installs gems + npm packages, builds Vite assets, runs migrations)
3. Zero-downtime swap: new instance starts, health check passes at `/up`, old instance stops

No action needed on your part after the initial setup.

---

## Accessing from your phone

Open `https://routecare.onrender.com` in your phone's browser. It's a full HTTPS URL with a valid SSL certificate. You can add it to your home screen (iOS: Share → Add to Home Screen).

---

## Upgrading from Free tier

If the cold-start delay bothers you, upgrade in the Render dashboard:

- **Web service**: Settings → Plan → **Starter** ($7/mo) — always on, no spin-down
- **Database**: Settings → Plan → **Starter** ($7/mo) — no 90-day expiry, backups included

---

## Local environment variables

Your `.env.local` file already has real values. Copy them to Render's Environment tab as described in Step 4. Never commit `.env.local` to git (it's already in `.gitignore`).

---

## Troubleshooting

**Build fails on `rails assets:precompile`**
→ Check that `NODE_VERSION=22.11.0` is set in Render environment vars.

**App boots but shows "We're sorry, but something went wrong"**
→ Check Render Logs tab. Most likely a missing env var — especially `RAILS_MASTER_KEY`.

**Database migration errors on first deploy**
→ In Render → Shell (or one-off job), run: `bundle exec rails db:migrate`

**Solid Queue not processing jobs**
→ Confirm `SOLID_QUEUE_IN_PUMA=true` is set. Check Render logs for `SolidQueue::Supervisor` startup messages.
