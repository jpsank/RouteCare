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
#    Vite and rolldown are devDependencies with platform-specific optional native
#    bindings. npm has a known bug (npm/cli#4828) where optional deps from the
#    lockfile aren't resolved for the build platform. Work around it by doing a
#    fresh npm install (not ci) so npm resolves bindings for linux-x64.
echo ">>> npm install (fresh, with dev deps)"
rm -rf node_modules
npm install --include=dev

# 3. Compile front-end assets (Vite → propshaft)
echo ">>> rails assets:precompile"
bundle exec rails assets:precompile

# 4. Prepare all databases (primary + queue + cache + cable).
#    db:prepare creates missing databases and runs pending migrations
#    or loads schema files for Solid Queue/Cache/Cable.
echo ">>> rails db:prepare"
bundle exec rails db:prepare

echo "──────────────────────────────────────"
echo " Build complete"
echo "──────────────────────────────────────"
