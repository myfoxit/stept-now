"""Test harness.

Defaults: in-memory SQLite, in-memory pubsub/queue, mock AI, temp-dir storage —
zero external services. Tests needing real Postgres are marked @pytest.mark.pg
and skip unless STEPT_TEST_PG_URL is set.

Key fixtures:
- client         httpx AsyncClient against the app (lifespan running)
- session        direct AsyncSession (commit explicitly when preparing data)
- workspace_ctx  signed-up owner + workspace → ids/headers + helpers
"""

from __future__ import annotations

import os
import tempfile

# Environment must be set before any app import.
os.environ.setdefault("STEPT_ENV", "test")
os.environ.setdefault("STEPT_DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("STEPT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("STEPT_STORAGE_DIR", tempfile.mkdtemp(prefix="stept-test-storage-"))
os.environ.setdefault("STEPT_EMBEDDING_DIM", "64")  # keep vectors small in tests

import httpx  # noqa: E402
import pytest  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402

from app.core.config import reset_settings_cache  # noqa: E402
from app.core.db import dispose_engine, get_session_factory, init_db  # noqa: E402
from app.core.pubsub import reset_pubsub  # noqa: E402
from app.core.queue import get_queue, reset_queue  # noqa: E402
from app.core.ratelimit import reset_rate_limits  # noqa: E402
from app.core.storage import reset_storage  # noqa: E402


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("STEPT_TEST_PG_URL"):
        return
    skip_pg = pytest.mark.skip(reason="Postgres not configured (set STEPT_TEST_PG_URL)")
    for item in items:
        if "pg" in item.keywords:
            item.add_marker(skip_pg)


@pytest.fixture(autouse=True)
async def _fresh_state():
    """Isolate every test: fresh settings, singletons, and in-memory database."""
    reset_settings_cache()
    reset_rate_limits()
    yield
    await reset_queue()
    await reset_pubsub()
    reset_storage()
    await dispose_engine()


@pytest.fixture
async def app():
    from app.main import create_app

    application = create_app()
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
async def session(app):
    """Direct DB access (schema exists because the app lifespan ran init_db)."""
    async with get_session_factory()() as db_session:
        yield db_session
        await db_session.rollback()


@pytest.fixture
async def db_only():
    """Schema without the app (pure service-layer tests)."""
    await init_db()
    async with get_session_factory()() as db_session:
        yield db_session
        await db_session.rollback()


async def drain_tasks() -> None:
    """Wait for in-process background tasks (ingestion, webhooks, …)."""
    await get_queue().drain()


# ---------------------------------------------------------------------------
# auth/workspace helpers
# ---------------------------------------------------------------------------


async def signup(
    client: httpx.AsyncClient,
    email: str,
    *,
    name: str = "Test User",
    password: str = "password-123",
) -> dict:
    response = await client.post(
        "/api/v1/auth/signup", json={"email": email, "name": name, "password": password}
    )
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token_payload: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_payload['access_token']}"}


class WorkspaceCtx:
    """Owner + workspace, plus helpers to add members with roles."""

    def __init__(self, client: httpx.AsyncClient, workspace: dict, owner_auth: dict):
        self.client = client
        self.workspace = workspace
        self.owner_auth = owner_auth
        self.owner_headers = bearer(owner_auth)

    @property
    def id(self) -> str:
        return self.workspace["id"]

    @property
    def base(self) -> str:
        return f"/api/v1/w/{self.id}"

    async def add_member(self, email: str, role: str = "agent") -> dict[str, str]:
        """Invite + accept + return the new member's auth headers."""
        invite = await self.client.post(
            f"{self.base}/invitations",
            json={"email": email, "role": role},
            headers=self.owner_headers,
        )
        assert invite.status_code == 201, invite.text
        member_auth = await signup(self.client, email)
        from sqlalchemy import select

        from app.models.workspace import Invitation

        async with get_session_factory()() as db_session:
            token = (
                await db_session.execute(select(Invitation.token).where(Invitation.email == email))
            ).scalar_one()
        accept = await self.client.post(
            "/api/v1/invitations/accept", json={"token": token}, headers=bearer(member_auth)
        )
        assert accept.status_code == 200, accept.text
        return bearer(member_auth)


@pytest.fixture
async def workspace_ctx(client) -> WorkspaceCtx:
    owner_auth = await signup(client, "owner@example.com", name="Owner")
    response = await client.post(
        "/api/v1/workspaces", json={"name": "Acme Support"}, headers=bearer(owner_auth)
    )
    assert response.status_code == 201, response.text
    return WorkspaceCtx(client, response.json(), owner_auth)
