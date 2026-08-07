"""Scheduled re-sync: scan_due_sources + the knowledge_refresh_scan job."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import respx

from tests.conftest import drain_tasks

PAGE_URL = "https://status.example.com/page"


def page_response():
    return httpx.Response(
        200,
        content=(
            b"<html><head><title>Status</title></head>"
            b"<body><p>All systems operational.</p></body></html>"
        ),
        headers={"content-type": "text/html"},
    )


async def create_urls_source(client, ctx, *, refresh_minutes=None, name="Site"):
    config = {"urls": [PAGE_URL]}
    if refresh_minutes is not None:
        config["refresh_minutes"] = refresh_minutes
    response = await client.post(
        f"{ctx.base}/knowledge/sources",
        json={"type": "urls", "name": name, "config": config},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def set_source_state(source_id, *, last_synced_at=None, status=None):
    from app.core.db import get_session_factory
    from app.models.knowledge import KnowledgeSource

    async with get_session_factory()() as session:
        source = await session.get(KnowledgeSource, source_id)
        if last_synced_at is not None:
            source.last_synced_at = last_synced_at
        if status is not None:
            source.status = status
        await session.commit()


async def get_source_json(client, ctx, source_id):
    response = await client.get(
        f"{ctx.base}/knowledge/sources/{source_id}", headers=ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_scan_enqueues_due_source_and_advances_last_synced(client, workspace_ctx):
    from app.core.db import utcnow
    from app.rag.tasks import scan_due_sources

    source = await create_urls_source(client, workspace_ctx, refresh_minutes=5)
    stale = utcnow() - timedelta(minutes=10)
    await set_source_state(source["id"], last_synced_at=stale)

    with respx.mock:
        respx.get(PAGE_URL).mock(return_value=page_response())
        assert await scan_due_sources() == 1
        await drain_tasks()

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert parse_dt(refreshed["last_synced_at"]) > stale  # sync ran and advanced the clock
    docs = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert docs.json()["total"] == 1


async def test_scan_skips_fresh_syncing_and_unconfigured_sources(client, workspace_ctx):
    from app.core.db import utcnow
    from app.rag.tasks import scan_due_sources

    fresh = await create_urls_source(client, workspace_ctx, refresh_minutes=5, name="Fresh")
    await set_source_state(fresh["id"], last_synced_at=utcnow())

    syncing = await create_urls_source(client, workspace_ctx, refresh_minutes=5, name="Busy")
    await set_source_state(
        syncing["id"], last_synced_at=utcnow() - timedelta(hours=1), status="syncing"
    )

    await create_urls_source(client, workspace_ctx, name="NoRefresh")  # no refresh_minutes

    assert await scan_due_sources() == 0


async def test_scan_treats_never_synced_sources_as_due(client, workspace_ctx):
    from app.rag.tasks import scan_due_sources

    source = await create_urls_source(client, workspace_ctx, refresh_minutes=5)
    assert source["last_synced_at"] is None

    with respx.mock:
        respx.get(PAGE_URL).mock(return_value=page_response())
        assert await scan_due_sources() == 1
        await drain_tasks()

    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_rescan_does_not_double_enqueue(client, workspace_ctx):
    from app.core.db import utcnow
    from app.rag.tasks import scan_due_sources

    source = await create_urls_source(client, workspace_ctx, refresh_minutes=5)
    await set_source_state(source["id"], last_synced_at=utcnow() - timedelta(minutes=30))

    with respx.mock:
        respx.get(PAGE_URL).mock(return_value=page_response())
        assert await scan_due_sources() == 1
        # Whether or not the enqueued sync already finished, the source is either
        # "syncing" or freshly synced — never due again.
        assert await scan_due_sources() == 0
        await drain_tasks()

    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_scan_ignores_refresh_minutes_below_floor(client, workspace_ctx):
    from app.core.db import get_session_factory, utcnow
    from app.models.knowledge import KnowledgeSource
    from app.rag.tasks import scan_due_sources

    source = await create_urls_source(client, workspace_ctx)
    # Sneak an invalid value straight into the DB (the API rejects < 5).
    async with get_session_factory()() as session:
        row = await session.get(KnowledgeSource, source["id"])
        row.config = {**row.config, "refresh_minutes": 1}
        row.last_synced_at = utcnow() - timedelta(hours=2)
        await session.commit()

    assert await scan_due_sources() == 0


async def test_refresh_scan_job_registered_and_runs(workspace_ctx):
    from app.core.scheduler import JOBS, reset_jobs_state, run_due

    job = JOBS["knowledge_refresh_scan"]
    assert job.every == timedelta(seconds=60)

    reset_jobs_state()
    ran = await run_due()
    assert "knowledge_refresh_scan" in ran  # no due sources → still a clean no-op run


# ---------------------------------------------------------------------------
# stuck-sync recovery
# ---------------------------------------------------------------------------


async def make_source_stuck(source_id, *, since):
    """Simulate a crashed worker: status frozen at "syncing" with every
    activity stamp (start stamp, updated_at, last_synced_at) aged to `since`.
    updated_at is set explicitly, which suppresses the onupdate refresh."""
    from app.core.db import get_session_factory
    from app.models.knowledge import KnowledgeSource

    async with get_session_factory()() as session:
        source = await session.get(KnowledgeSource, source_id)
        source.status = "syncing"
        source.config = {**source.config, "sync_started_at": since.isoformat()}
        source.last_synced_at = since
        source.updated_at = since
        await session.commit()


async def test_stuck_syncing_source_fails_over_after_timeout(client, workspace_ctx):
    from app.core.db import utcnow
    from app.rag.tasks import scan_due_sources

    source = await create_urls_source(client, workspace_ctx)  # no refresh_minutes
    await make_source_stuck(source["id"], since=utcnow() - timedelta(minutes=45))

    assert await scan_due_sources() == 0  # recovered, but not due (no refresh_minutes)
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert refreshed["error"] == "sync timed out"


async def test_stuck_source_with_refresh_due_is_resynced_in_the_same_scan(client, workspace_ctx):
    from app.core.db import utcnow
    from app.rag.tasks import scan_due_sources

    source = await create_urls_source(client, workspace_ctx, refresh_minutes=5)
    await make_source_stuck(source["id"], since=utcnow() - timedelta(hours=2))

    with respx.mock:
        respx.get(PAGE_URL).mock(return_value=page_response())
        assert await scan_due_sources() == 1  # failed over, then immediately re-enqueued
        await drain_tasks()

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"  # scheduled refresh resumed
    assert refreshed["error"] is None


async def test_recently_started_sync_is_not_failed_over(client, workspace_ctx):
    from app.core.db import utcnow
    from app.rag.tasks import scan_due_sources

    source = await create_urls_source(client, workspace_ctx, refresh_minutes=5)
    await make_source_stuck(source["id"], since=utcnow() - timedelta(minutes=5))

    assert await scan_due_sources() == 0
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "syncing"  # inside the timeout — leave it alone


async def test_sync_records_a_start_stamp_in_config(client, workspace_ctx):
    from datetime import datetime

    from app.core.db import utcnow

    source = await create_urls_source(client, workspace_ctx)
    with respx.mock:
        respx.get(PAGE_URL).mock(return_value=page_response())
        response = await client.post(
            f"{workspace_ctx.base}/knowledge/sources/{source['id']}/sync",
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 200
        await drain_tasks()

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    started = datetime.fromisoformat(refreshed["config"]["sync_started_at"])
    assert utcnow() - started < timedelta(minutes=1)
