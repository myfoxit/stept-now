"""Database layer.

Stept runs on Postgres (production; pgvector + full-text search) and SQLite
(tests, zero-dependency dev). Portability rules:

- Use `GUID` for ids/FKs, `PortableJSON` for JSON, `EmbeddingVector` for vectors.
- Dialect-specific SQL must be branched on `is_postgres(session)`.
- ids come from `uuid7()` (time-ordered — safe for cursor pagination).
"""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CHAR, JSON, DateTime, MetaData, TypeDecorator, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# ids & time
# ---------------------------------------------------------------------------


def uuid7() -> str:
    """UUIDv7 (draft RFC 9562 layout): 48-bit unix-ms timestamp + randomness.

    Time-ordered, which keeps b-tree inserts append-mostly and makes ids usable
    as tiebreakers in cursor pagination.
    """
    ts_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ts_ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76  # version 7
    value |= rand_a << 64
    value |= 0b10 << 62  # variant
    value |= rand_b
    return str(uuid.UUID(int=value))


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# portable column types
# ---------------------------------------------------------------------------


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every dialect (SQLite returns naive
    values for DateTime(timezone=True); this normalizes reads back to UTC)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class GUID(TypeDecorator[str]):
    """String uuid on every dialect (native UUID type on Postgres)."""

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID

            return dialect.type_descriptor(UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(value).lower()

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else str(value)


class PortableJSON(TypeDecorator[Any]):
    """JSONB on Postgres, JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class EmbeddingVector(TypeDecorator[Any]):
    """pgvector `vector` on Postgres; JSON-encoded float list elsewhere.

    The Postgres column is dimensionless so any embedding model works out of the
    box; `make db-index-embeddings DIM=…` types the column and adds HNSW at scale.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return [float(v) for v in value]

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, str):  # pgvector text protocol
            return [float(v) for v in value.strip("[]").split(",") if v]
        return [float(v) for v in value]


# ---------------------------------------------------------------------------
# base + engine
# ---------------------------------------------------------------------------

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _json_serializer(obj: Any) -> str:
    return json.dumps(obj, default=str)


def build_engine(url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    url = url or settings.database_url
    kwargs: dict[str, Any] = {"json_serializer": _json_serializer}
    if url.startswith("sqlite"):
        if ":memory:" in url or url.rstrip("/").endswith("sqlite+aiosqlite:"):
            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = int(os.environ.get("STEPT_DB_POOL_SIZE", "10"))
        kwargs["max_overflow"] = 10
        kwargs["pool_pre_ping"] = True
    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        _enforce_sqlite_foreign_keys(engine)
    return engine


def _enforce_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Turn on SQLite's foreign-key enforcement, which is off by default.

    Without this the zero-dependency dev/test database silently accepts rows
    Postgres would reject — insert ordering, missing parents, cascade behaviour —
    so a whole class of bug can only ever be discovered in production. It cost us
    exactly that once: workspace creation inserted `memberships` before
    `workspaces` and passed every SQLite test.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine()
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def is_postgres(session_or_engine: AsyncSession | AsyncEngine) -> bool:
    bind = session_or_engine
    if isinstance(session_or_engine, AsyncSession):
        bind = session_or_engine.get_bind()  # type: ignore[assignment]
    return getattr(bind, "dialect", bind).name == "postgresql"  # type: ignore[union-attr]


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Unit of work for background tasks/workers: commit on success, rollback on error."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# schema init (dev/test; production uses alembic)
# ---------------------------------------------------------------------------


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create tables and Postgres extensions/indexes. Idempotent."""
    import app.models  # noqa: F401  (register all mappers)

    engine = engine or get_engine()
    if is_postgres(engine):
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if is_postgres(engine):
        await ensure_pg_indexes(engine)


async def ensure_pg_indexes(engine: AsyncEngine) -> None:
    """Expression indexes that SQLAlchemy models can't express portably."""
    statements = [
        # Hybrid-search FTS over knowledge chunks (table arrives in Wave 1).
        """CREATE INDEX IF NOT EXISTS ix_chunks_fts ON chunks
           USING GIN (to_tsvector('english', content))""",
        """CREATE INDEX IF NOT EXISTS ix_contacts_name_trgm ON contacts
           USING GIN (name gin_trgm_ops)""",
    ]
    for stmt in statements:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:  # noqa: BLE001 — table may not exist yet in early waves
            continue
