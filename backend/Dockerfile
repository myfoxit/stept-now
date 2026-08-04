# Stept API — production image.
#
# Two stages so the runtime carries no build toolchain: uv resolves the locked
# environment into /app/.venv, then only that venv and app/ ship.
#
#   docker build -f backend/Dockerfile -t stept-api backend

# Both stages sit on the same Docker Hub base and uv is pip-installed, so the
# build needs no second registry — one less thing to authenticate in CI.
FROM python:3.12-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir uv==0.10.0

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer survives every
# change that does not touch pyproject.toml/uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim-bookworm AS runtime

# libpq for asyncpg's build-free wheel path is not needed, but curl is: the
# container healthcheck must not depend on the app's own dependency tree.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Never run the app as root, and give it a real home so nothing writes to /.
RUN groupadd --gid 10001 stept \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin stept

WORKDIR /app

COPY --from=builder --chown=stept:stept /app/.venv /app/.venv
COPY --chown=stept:stept alembic.ini ./
COPY --chown=stept:stept alembic ./alembic
COPY --chown=stept:stept app ./app
COPY --chown=stept:stept docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data/uploads \
    && chown -R stept:stept /data

ENV PATH="/app/.venv/bin:$PATH" \
    # Explicit so `docker compose run api python -m app.seed` and one-off scripts
    # import correctly instead of depending on the working directory.
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STEPT_ENV=prod \
    STEPT_STORAGE_DIR=/data/uploads

USER stept
EXPOSE 8600

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8600/api/v1/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve"]
