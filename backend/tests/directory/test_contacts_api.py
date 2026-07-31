"""Contacts API: CRUD, search + cursor pagination, notes, events, tags, CSAT."""

from datetime import timedelta

from app.core.db import get_session_factory, utcnow
from app.models.contact import Contact
from tests.conftest import bearer, signup


async def _insert_contacts(workspace_id: str, specs: list[dict]) -> list[str]:
    """Insert contacts directly (lets tests control last_seen_at)."""
    ids = []
    async with get_session_factory()() as session:
        for spec in specs:
            contact = Contact(workspace_id=workspace_id, **spec)
            session.add(contact)
            await session.flush()
            ids.append(contact.id)
        await session.commit()
    return ids


async def test_contact_crud_roundtrip(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={
            "email": "Jo@Example.com",
            "name": "Jo",
            "external_id": "crm-1",
            "attributes": {"plan": "pro"},
        },
        headers=workspace_ctx.owner_headers,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["email"] == "jo@example.com"  # normalized
    contact_id = body["id"]

    detail = await client.get(
        f"{workspace_ctx.base}/contacts/{contact_id}", headers=workspace_ctx.owner_headers
    )
    assert detail.status_code == 200
    assert detail.json()["attributes"] == {"plan": "pro"}

    patch = await client.patch(
        f"{workspace_ctx.base}/contacts/{contact_id}",
        json={"name": "Joanna", "attributes": {"plan": "enterprise"}},
        headers=workspace_ctx.owner_headers,
    )
    assert patch.status_code == 200
    assert patch.json()["name"] == "Joanna"
    assert patch.json()["attributes"] == {"plan": "enterprise"}  # replaced wholesale

    delete = await client.delete(
        f"{workspace_ctx.base}/contacts/{contact_id}", headers=workspace_ctx.owner_headers
    )
    assert delete.status_code == 200
    gone = await client.get(
        f"{workspace_ctx.base}/contacts/{contact_id}", headers=workspace_ctx.owner_headers
    )
    assert gone.status_code == 404


async def test_contact_duplicate_external_id_conflict(client, workspace_ctx):
    first = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"external_id": "dup-1", "name": "One"},
        headers=workspace_ctx.owner_headers,
    )
    assert first.status_code == 201
    duplicate = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"external_id": "dup-1", "name": "Two"},
        headers=workspace_ctx.owner_headers,
    )
    assert duplicate.status_code == 409


async def test_contact_create_requires_some_identity(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/contacts", json={"name": "  "}, headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 422


async def test_list_sorted_with_cursor_pagination(client, workspace_ctx):
    now = utcnow()
    await _insert_contacts(
        workspace_ctx.id,
        [
            {"name": "Seen 2h ago", "last_seen_at": now - timedelta(hours=2)},
            {"name": "Seen 1h ago", "last_seen_at": now - timedelta(hours=1)},
            {"name": "Never seen (older)"},
            {"name": "Never seen (newer)"},
            {"name": "Seen 3h ago", "last_seen_at": now - timedelta(hours=3)},
        ],
    )
    page1 = await client.get(
        f"{workspace_ctx.base}/contacts?limit=2", headers=workspace_ctx.owner_headers
    )
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert [c["name"] for c in body1["items"]] == ["Seen 1h ago", "Seen 2h ago"]
    assert body1["next_cursor"]

    page2 = await client.get(
        f"{workspace_ctx.base}/contacts?limit=2&cursor={body1['next_cursor']}",
        headers=workspace_ctx.owner_headers,
    )
    body2 = page2.json()
    assert [c["name"] for c in body2["items"]] == ["Seen 3h ago", "Never seen (newer)"]
    assert body2["next_cursor"]

    page3 = await client.get(
        f"{workspace_ctx.base}/contacts?limit=2&cursor={body2['next_cursor']}",
        headers=workspace_ctx.owner_headers,
    )
    body3 = page3.json()
    assert [c["name"] for c in body3["items"]] == ["Never seen (older)"]
    assert body3["next_cursor"] is None

    malformed = await client.get(
        f"{workspace_ctx.base}/contacts?cursor=not-a-cursor", headers=workspace_ctx.owner_headers
    )
    assert malformed.status_code == 400


async def test_list_search_is_case_insensitive(client, workspace_ctx):
    await _insert_contacts(
        workspace_ctx.id,
        [
            {"name": "Ada Lovelace", "email": "ada@analytical.io"},
            {"name": "Grace Hopper", "email": "grace@navy.example"},
            {"name": "", "external_id": "ADA-999"},
        ],
    )
    by_name = await client.get(
        f"{workspace_ctx.base}/contacts?q=ada", headers=workspace_ctx.owner_headers
    )
    found = by_name.json()["items"]
    assert len(found) == 2  # name match + external_id match
    by_email = await client.get(
        f"{workspace_ctx.base}/contacts?q=NAVY", headers=workspace_ctx.owner_headers
    )
    assert [c["name"] for c in by_email.json()["items"]] == ["Grace Hopper"]


async def test_contact_notes_flow(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"name": "Note Target"},
        headers=workspace_ctx.owner_headers,
    )
    contact_id = create.json()["id"]
    note = await client.post(
        f"{workspace_ctx.base}/contacts/{contact_id}/notes",
        json={"body": "Prefers email over phone"},
        headers=workspace_ctx.owner_headers,
    )
    assert note.status_code == 201, note.text
    assert note.json()["author_id"] is not None

    listing = await client.get(
        f"{workspace_ctx.base}/contacts/{contact_id}/notes", headers=workspace_ctx.owner_headers
    )
    assert [n["body"] for n in listing.json()] == ["Prefers email over phone"]

    delete = await client.delete(
        f"{workspace_ctx.base}/contacts/{contact_id}/notes/{note.json()['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert delete.status_code == 200
    assert (
        await client.get(
            f"{workspace_ctx.base}/contacts/{contact_id}/notes",
            headers=workspace_ctx.owner_headers,
        )
    ).json() == []


async def test_event_tracking_api_key_scopes(client, workspace_ctx):
    contact = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"external_id": "evt-1", "name": "Tracked"},
        headers=workspace_ctx.owner_headers,
    )
    contact_id = contact.json()["id"]

    keys = {}
    for scope in ("write", "read"):
        created = await client.post(
            f"{workspace_ctx.base}/api-keys",
            json={"name": f"{scope} key", "scopes": [scope]},
            headers=workspace_ctx.owner_headers,
        )
        keys[scope] = {"Authorization": f"Bearer {created.json()['key']}"}

    tracked = await client.post(
        f"{workspace_ctx.base}/contacts/{contact_id}/events",
        json={"name": "signup", "meta": {"source": "landing"}},
        headers=keys["write"],
    )
    assert tracked.status_code == 201, tracked.text

    forbidden = await client.post(
        f"{workspace_ctx.base}/contacts/{contact_id}/events",
        json={"name": "signup"},
        headers=keys["read"],
    )
    assert forbidden.status_code == 403

    listing = await client.get(
        f"{workspace_ctx.base}/contacts/{contact_id}/events", headers=keys["read"]
    )
    assert listing.status_code == 200
    assert [e["name"] for e in listing.json()] == ["signup"]

    # Tracking an event bumps last_seen_at.
    detail = await client.get(
        f"{workspace_ctx.base}/contacts/{contact_id}", headers=workspace_ctx.owner_headers
    )
    assert detail.json()["last_seen_at"] is not None


async def test_contact_tag_attach_detach(client, workspace_ctx):
    contact = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"name": "Tagged"},
        headers=workspace_ctx.owner_headers,
    )
    contact_id = contact.json()["id"]
    tag = await client.post(
        f"{workspace_ctx.base}/tags",
        json={"name": "vip", "color": "#f59e0b"},
        headers=workspace_ctx.owner_headers,
    )
    tag_id = tag.json()["id"]

    attach = await client.post(
        f"{workspace_ctx.base}/contacts/{contact_id}/tags",
        json={"tag_id": tag_id},
        headers=workspace_ctx.owner_headers,
    )
    assert attach.status_code == 200
    assert [t["name"] for t in attach.json()] == ["vip"]

    # Idempotent re-attach.
    again = await client.post(
        f"{workspace_ctx.base}/contacts/{contact_id}/tags",
        json={"tag_id": tag_id},
        headers=workspace_ctx.owner_headers,
    )
    assert len(again.json()) == 1

    # Tags are hydrated in the directory listing.
    listing = await client.get(
        f"{workspace_ctx.base}/contacts", headers=workspace_ctx.owner_headers
    )
    item = next(c for c in listing.json()["items"] if c["id"] == contact_id)
    assert [t["name"] for t in item["tags"]] == ["vip"]

    unknown = await client.post(
        f"{workspace_ctx.base}/contacts/{contact_id}/tags",
        json={"tag_id": "00000000-0000-7000-8000-000000000042"},
        headers=workspace_ctx.owner_headers,
    )
    assert unknown.status_code == 404

    detach = await client.delete(
        f"{workspace_ctx.base}/contacts/{contact_id}/tags/{tag_id}",
        headers=workspace_ctx.owner_headers,
    )
    assert detach.status_code == 200
    assert detach.json() == []


async def test_csat_history_endpoint(client, workspace_ctx):
    from app.services import csat as csat_service

    contact = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"name": "Rater", "email": "rater@example.com"},
        headers=workspace_ctx.owner_headers,
    )
    contact_id = contact.json()["id"]
    async with get_session_factory()() as session:
        await csat_service.record_response(
            session,
            workspace_ctx.id,
            conversation_id="00000000-0000-7000-8000-000000000011",
            contact_id=contact_id,
            rating=4,
            feedback="great support",
        )
        await session.commit()

    history = await client.get(
        f"{workspace_ctx.base}/contacts/{contact_id}/csat", headers=workspace_ctx.owner_headers
    )
    assert history.status_code == 200, history.text
    assert [(r["rating"], r["feedback"]) for r in history.json()] == [(4, "great support")]


async def test_cross_workspace_contacts_invisible(client, workspace_ctx):
    other_auth = await signup(client, "other-dir-owner@example.com")
    other_ws = (
        await client.post(
            "/api/v1/workspaces", json={"name": "Other Dir Co"}, headers=bearer(other_auth)
        )
    ).json()
    other_base = f"/api/v1/w/{other_ws['id']}"
    foreign = await client.post(
        f"{other_base}/contacts", json={"name": "Foreign"}, headers=bearer(other_auth)
    )
    foreign_id = foreign.json()["id"]

    # ws1 listing doesn't include ws2's contact; direct fetch 404s.
    listing = await client.get(
        f"{workspace_ctx.base}/contacts", headers=workspace_ctx.owner_headers
    )
    assert all(c["id"] != foreign_id for c in listing.json()["items"])
    direct = await client.get(
        f"{workspace_ctx.base}/contacts/{foreign_id}", headers=workspace_ctx.owner_headers
    )
    assert direct.status_code == 404
    # And a non-member can't hit ws1 at all.
    forbidden = await client.get(f"{workspace_ctx.base}/contacts", headers=bearer(other_auth))
    assert forbidden.status_code == 403


async def test_viewer_cannot_mutate_contacts(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("dir-viewer@example.com", role="viewer")
    contact = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"name": "Readable"},
        headers=workspace_ctx.owner_headers,
    )
    contact_id = contact.json()["id"]

    assert (await client.get(f"{workspace_ctx.base}/contacts", headers=viewer)).status_code == 200
    for method, url, payload in [
        ("post", f"{workspace_ctx.base}/contacts", {"name": "Nope"}),
        ("patch", f"{workspace_ctx.base}/contacts/{contact_id}", {"name": "Nope"}),
        ("post", f"{workspace_ctx.base}/contacts/{contact_id}/notes", {"body": "no"}),
        ("post", f"{workspace_ctx.base}/contacts/{contact_id}/events", {"name": "no"}),
    ]:
        response = await getattr(client, method)(url, json=payload, headers=viewer)
        assert response.status_code == 403, f"{method} {url}"
    delete = await client.delete(f"{workspace_ctx.base}/contacts/{contact_id}", headers=viewer)
    assert delete.status_code == 403
