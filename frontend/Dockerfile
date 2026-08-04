# Stept web — the dashboard SPA plus the embeddable widget bundle, served by nginx.
#
# Built from the repo root because pnpm needs the workspace manifest and the
# widget depends on the local @stept/dom-capture package:
#
#   docker build -f frontend/Dockerfile -t stept-web .

FROM node:22-bookworm-slim AS builder

RUN corepack enable

WORKDIR /src

# Manifests first: this layer is reused until a dependency actually changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json ./frontend/
COPY widget/package.json ./widget/
COPY extension/package.json ./extension/
COPY packages/dom-capture/package.json ./packages/dom-capture/
COPY e2e/package.json ./e2e/

# The extension's build toolchain (wxt/web-ext-run) is a browser-extension
# development harness — nothing in it ships to the server, and its transitive
# dev tree carries advisories, so it is deliberately left out of this image.
RUN pnpm install --frozen-lockfile --filter '@stept/frontend...' --filter '@stept/widget...'

COPY packages/dom-capture ./packages/dom-capture
COPY widget ./widget
COPY frontend ./frontend

# `pnpm build` in frontend runs tsc --noEmit first, so a type error fails the
# image rather than shipping a stale bundle.
RUN pnpm --filter @stept/frontend build \
    && pnpm --filter @stept/widget build


FROM nginx:1.27-alpine AS runtime

# Drop the stock config; ours owns SPA fallback and cache policy.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/nginx.conf /etc/nginx/conf.d/stept.conf

COPY --from=builder /src/frontend/dist /usr/share/nginx/html
COPY --from=builder /src/widget/dist /usr/share/nginx/html/widget-assets

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
