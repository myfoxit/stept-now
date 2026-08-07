# Stept landing — the static marketing site for the apex domain, served by nginx.
#
# Built from the repo root so the shared static-site nginx config in deploy/ is
# in context (the landing itself is isolated from the pnpm workspace and carries
# its own lockfile):
#
#   docker build -f landing/Dockerfile -t stept-landing .

FROM node:22-bookworm-slim AS builder

RUN corepack enable

WORKDIR /src

COPY landing/package.json landing/pnpm-lock.yaml ./
RUN pnpm install --ignore-workspace --frozen-lockfile

COPY landing/astro.config.mjs landing/tsconfig.json ./
COPY landing/src ./src
COPY landing/public ./public

RUN pnpm build


FROM nginx:1.27-alpine AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/nginx-static.conf /etc/nginx/conf.d/site.conf

COPY --from=builder /src/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
