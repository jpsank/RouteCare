# Warn at boot when optional service keys are missing.
# Required keys (DATABASE_URL, RAILS_MASTER_KEY) are enforced by Rails/adapters;
# these are optional integrations that silently degrade without warning.

OPTIONAL_ENV_KEYS = {
  "ROUTECARE_POSTMARK_API_KEY" => "Email delivery (Postmark)",
  "ROUTECARE_TELNYX_API_KEY" => "SMS delivery (Telnyx)",
  "ROUTECARE_TELNYX_FROM_NUMBER" => "SMS sender number (Telnyx)",
  "ROUTECARE_LLM_API_KEY" => "AI-drafted messages",
  "GOOGLE_MAPS_API_KEY" => "Geocoding/routing (Google, used if Mapbox is unset or fails)",
  "MAPBOX_ACCESS_TOKEN" => "Geocoding/routing (Mapbox, tried first)",
  "GOOGLE_OAUTH_CLIENT_ID" => "Google Calendar sync",
  "GOOGLE_OAUTH_CLIENT_SECRET" => "Google Calendar sync"
}.freeze

Rails.application.config.after_initialize do
  missing = OPTIONAL_ENV_KEYS.select { |key, _| ENV[key].blank? }
  next if missing.empty?

  Rails.logger.info("[EnvCheck] Missing optional env vars (features will be disabled):")
  missing.each { |key, desc| Rails.logger.info("  #{key} — #{desc}") }
end
