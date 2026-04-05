# syntax=docker/dockerfile:1
# check=error=true

# Production Dockerfile for RouteCare
# Build: docker build -t routecare .
# Run:   docker run -d -p 3000:3000 -e RAILS_MASTER_KEY=<value> --name routecare routecare

ARG RUBY_VERSION=3.4.4
FROM docker.io/library/ruby:$RUBY_VERSION-slim AS base

WORKDIR /rails

# Install runtime dependencies
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y curl libjemalloc2 libvips postgresql-client && \
    ln -s /usr/lib/$(uname -m)-linux-gnu/libjemalloc.so.2 /usr/local/lib/libjemalloc.so && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

ENV RAILS_ENV="production" \
    BUNDLE_DEPLOYMENT="1" \
    BUNDLE_PATH="/usr/local/bundle" \
    BUNDLE_WITHOUT="development" \
    LD_PRELOAD="/usr/local/lib/libjemalloc.so"

# ---------- Build stage ----------
FROM base AS build

# Install build dependencies
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y build-essential git libpq-dev libyaml-dev pkg-config && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

# Install Node.js (pre-built binary)
ARG NODE_VERSION=22.16.0
ENV PATH=/usr/local/node/bin:$PATH
RUN mkdir -p /usr/local/node && \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    | tar -xJ --strip-components=1 -C /usr/local/node/

# Install Ruby gems (BuildKit cache survives across builds)
COPY Gemfile Gemfile.lock ./
COPY vendor/ vendor/
RUN bundle install && \
    rm -rf ~/.bundle/ "${BUNDLE_PATH}"/ruby/*/cache "${BUNDLE_PATH}"/ruby/*/bundler/gems/*/.git && \
    bundle exec bootsnap precompile --gemfile

# Install Node modules (BuildKit cache for npm)
COPY package.json package-lock.json ./
RUN npm ci --include=dev

# Copy application code and precompile
COPY . .
RUN bundle exec bootsnap precompile app/ lib/ && \
    SECRET_KEY_BASE_DUMMY=1 ./bin/rails assets:precompile && \
    rm -rf node_modules

# ---------- Runtime stage ----------
FROM base

RUN groupadd --system --gid 1000 rails && \
    useradd rails --uid 1000 --gid 1000 --create-home --shell /bin/bash
USER 1000:1000

COPY --chown=rails:rails --from=build "${BUNDLE_PATH}" "${BUNDLE_PATH}"
COPY --chown=rails:rails --from=build /rails /rails

ENTRYPOINT ["/rails/bin/docker-entrypoint"]

CMD ["./bin/rails", "server", "-b", "0.0.0.0"]
