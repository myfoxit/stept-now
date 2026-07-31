"""Alembic environment — async engine, URL + metadata from the app.

Migrations target Postgres (production). For dev/tests the app uses create_all on
SQLite; migrations are the production deployment path.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.core.db import Base

# Register every mapper so Base.metadata is complete.
import app.models  # noqa: F401,E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata

# Expression indexes bootstrapped at startup by app.core.db.ensure_pg_indexes rather
# than declared on the metadata. Autogenerate cannot see them, so without this filter
# every new revision proposes dropping them.
BOOTSTRAP_INDEXES = {"ix_chunks_fts", "ix_contacts_name_trgm"}


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "index" and name in BOOTSTRAP_INDEXES)


def _run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
