#!/bin/sh
# Stept API container entrypoint.
#
#   serve      wait for the database, run migrations, then start uvicorn
#   migrate    run migrations and exit (used by the deploy workflow)
#   <other>    exec'd verbatim, so `docker compose run api python -m app.seed` works
#
# Migrations run here rather than in the app's lifespan because env=prod
# deliberately does not create tables on boot — schema changes are alembic's job,
# and running them once per container start is idempotent and ordered.
set -eu

wait_for_db() {
    # asyncpg has no CLI; ask SQLAlchemy, which already knows how to read our URL.
    attempts=0
    until python - <<'PY'
import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


async def main() -> int:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            return 0
    except Exception:
        return 1
    finally:
        await engine.dispose()


sys.exit(asyncio.run(main()))
PY
    do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 60 ]; then
            echo "database not reachable after ${attempts} attempts; giving up" >&2
            exit 1
        fi
        echo "waiting for database (${attempts}/60)…"
        sleep 2
    done
}

case "${1:-serve}" in
    serve)
        wait_for_db
        echo "running migrations…"
        alembic upgrade head
        echo "starting uvicorn on :8600 with ${STEPT_WEB_CONCURRENCY:-4} workers"
        exec uvicorn app.main:app \
            --host 0.0.0.0 \
            --port 8600 \
            --workers "${STEPT_WEB_CONCURRENCY:-4}" \
            --proxy-headers \
            --forwarded-allow-ips '*' \
            --no-access-log
        ;;
    migrate)
        wait_for_db
        exec alembic upgrade head
        ;;
    *)
        exec "$@"
        ;;
esac
