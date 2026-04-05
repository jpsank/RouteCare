#!/usr/bin/env bash
# Render.com build script for RouteCare
# Runs once per deploy; Render caches the bundle between builds.
set -o errexit

echo "──────────────────────────────────────"
echo " RouteCare build starting"
echo "──────────────────────────────────────"

# 1. Ruby dependencies
echo ">>> bundle install"
bundle install

# 2. Node/JS dependencies (needed for Vite build)
echo ">>> npm install"
npm install

# 3. Compile front-end assets (Vite → propshaft)
echo ">>> rails assets:precompile"
bundle exec rails assets:precompile

# 4. Ensure Solid adapter migration directories exist, then copy
#    each gem's migrations into the correct path.
#    These generators are idempotent — safe to run on every deploy.
echo ">>> installing Solid adapter migrations"
mkdir -p db/cache_migrate db/queue_migrate db/cable_migrate
bundle exec rails solid_queue:install:migrations  2>/dev/null || true
bundle exec rails solid_cache:install:migrations  2>/dev/null || true
bundle exec rails solid_cable:install:migrations  2>/dev/null || true

# 5. Run all pending migrations (primary + cache + queue + cable).
#    All four connections point to the same Render PostgreSQL instance,
#    so a single db:migrate covers everything.
echo ">>> rails db:migrate"
bundle exec rails db:migrate

echo "──────────────────────────────────────"
echo " Build complete"
echo "──────────────────────────────────────"
