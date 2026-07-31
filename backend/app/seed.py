"""Demo seed: `python -m app.seed` (idempotent).

ORCHESTRATOR-OWNED. Each wave extends `_seed_domains` via the try/import blocks —
domain seeders live in their own modules as `async def seed(session, ctx) -> None`
and receive a SeedContext with the demo workspace + users.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import init_db, session_scope
from app.core.logging import configure_logging, log
from app.core.security import hash_password
from app.models.user import User
from app.models.workspace import Membership, Workspace
from app.services.workspaces import create_workspace

logger = log("seed")

DEMO_PASSWORD = "stept-demo"  # noqa: S105 — demo credentials, documented in README


@dataclass
class SeedContext:
    workspace: Workspace
    owner: User
    agent: User
    extra: dict[str, Any] = field(default_factory=dict)


async def _get_or_create_user(session: AsyncSession, email: str, name: str) -> User:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(email=email, name=name, password_hash=hash_password(DEMO_PASSWORD))
        session.add(user)
        await session.flush()
        logger.info("created user %s (password: %s)", email, DEMO_PASSWORD)
    return user


async def seed_core(session: AsyncSession) -> SeedContext:
    owner = await _get_or_create_user(session, "owner@stept.dev", "Odette Owner")
    agent = await _get_or_create_user(session, "agent@stept.dev", "Sam Support")

    workspace = (
        await session.execute(select(Workspace).where(Workspace.slug == "stept-demo"))
    ).scalar_one_or_none()
    if workspace is None:
        workspace = await create_workspace(session, owner, name="Stept Demo")
        workspace.slug = "stept-demo"
        session.add(Membership(workspace_id=workspace.id, user_id=agent.id, role="agent"))
        await session.flush()
        logger.info("created workspace %s", workspace.slug)
    return SeedContext(workspace=workspace, owner=owner, agent=agent)


async def _seed_domains(session: AsyncSession, ctx: SeedContext) -> None:
    """Domain seeders, added wave by wave."""
    for module_name in (
        "app.services.contacts_seed",
        "app.services.conversations_seed",
        "app.rag.seed",
        "app.ai.seed",
        "app.agents.seed",
        "app.automation.seed",
        "app.dap.seed",
    ):
        try:
            module = __import__(module_name, fromlist=["seed"])
        except ImportError:
            continue
        await module.seed(session, ctx)
        logger.info("seeded %s", module_name)


async def main() -> None:
    configure_logging()
    await init_db()
    async with session_scope() as session:
        ctx = await seed_core(session)
        await _seed_domains(session, ctx)
    logger.info("seed complete — log in as owner@stept.dev / %s", DEMO_PASSWORD)


if __name__ == "__main__":
    asyncio.run(main())
