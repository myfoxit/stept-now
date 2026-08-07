# Stept docs — the static documentation site for docs.stepped.ai, served by
# nginx. Built from the repo root so the shared static-site nginx config in
# deploy/ is in context:
#
#   docker build -f docs-site/Dockerfile -t stept-docs .

FROM node:22-bookworm-slim AS builder

RUN corepack enable

WORKDIR /src

COPY docs-site/package.json docs-site/pnpm-lock.yaml ./
RUN pnpm install --ignore-workspace --frozen-lockfile

COPY docs-site/astro.config.mjs docs-site/tsconfig.json ./
COPY docs-site/src ./src
COPY docs-site/public ./public

RUN pnpm build


FROM nginx:1.27-alpine AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/nginx-static.conf /etc/nginx/conf.d/site.conf

COPY --from=builder /src/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
