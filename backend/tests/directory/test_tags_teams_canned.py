"""Tags, teams, canned responses: CRUD, uniqueness, membership rules, authz."""

from tests.conftest import bearer, signup

# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------


async def test_tag_crud_and_name_conflict(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/tags",
        json={"name": "billing", "color": "#3b82f6"},
        headers=workspace_ctx.owner_headers,
    )
    assert create.status_code == 201, create.text
    tag_id = create.json()["id"]

    duplicate = await client.post(
        f"{workspace_ctx.base}/tags", json={"name": "billing"}, headers=workspace_ctx.owner_headers
    )
    assert duplicate.status_code == 409

    bad_color = await client.post(
        f"{workspace_ctx.base}/tags",
        json={"name": "ugly", "color": "red"},
        headers=workspace_ctx.owner_headers,
    )
    assert bad_color.status_code == 422

    patch = await client.patch(
        f"{workspace_ctx.base}/tags/{tag_id}",
        json={"name": "billing-issues", "color": "#111111"},
        headers=workspace_ctx.owner_headers,
    )
    assert patch.status_code == 200
    assert patch.json()["color"] == "#111111"

    listing = await client.get(f"{workspace_ctx.base}/tags", headers=workspace_ctx.owner_headers)
    assert [t["name"] for t in listing.json()] == ["billing-issues"]

    delete = await client.delete(
        f"{workspace_ctx.base}/tags/{tag_id}", headers=workspace_ctx.owner_headers
    )
    assert delete.status_code == 200
    assert (
        await client.get(f"{workspace_ctx.base}/tags", headers=workspace_ctx.owner_headers)
    ).json() == []


async def test_tag_delete_cascades_contact_links(client, workspace_ctx):
    contact = await client.post(
        f"{workspace_ctx.base}/contacts",
        json={"name": "Linked"},
        headers=workspace_ctx.owner_headers,
    )
    contact_id = contact.json()["id"]
    tag = await client.post(
        f"{workspace_ctx.base}/tags", json={"name": "doomed"}, headers=workspace_ctx.owner_headers
    )
    tag_id = tag.json()["id"]
    await client.post(
        f"{workspace_ctx.base}/contacts/{contact_id}/tags",
        json={"tag_id": tag_id},
        headers=workspace_ctx.owner_headers,
    )

    await client.delete(f"{workspace_ctx.base}/tags/{tag_id}", headers=workspace_ctx.owner_headers)
    detail = await client.get(
        f"{workspace_ctx.base}/contacts/{contact_id}", headers=workspace_ctx.owner_headers
    )
    assert detail.json()["tags"] == []  # link rows removed with the tag


async def test_tag_mutations_require_manage_perm(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("tag-viewer@example.com", role="viewer")
    agent = await workspace_ctx.add_member("tag-agent@example.com", role="agent")

    assert (
        await client.post(f"{workspace_ctx.base}/tags", json={"name": "nope"}, headers=viewer)
    ).status_code == 403
    assert (await client.get(f"{workspace_ctx.base}/tags", headers=viewer)).status_code == 200

    created = await client.post(
        f"{workspace_ctx.base}/tags", json={"name": "agent-made"}, headers=agent
    )
    assert created.status_code == 201  # agents hold conversations:manage
    tag_id = created.json()["id"]
    assert (
        await client.patch(
            f"{workspace_ctx.base}/tags/{tag_id}", json={"name": "x"}, headers=viewer
        )
    ).status_code == 403
    assert (
        await client.delete(f"{workspace_ctx.base}/tags/{tag_id}", headers=viewer)
    ).status_code == 403


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------


async def test_team_crud(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/teams",
        json={"name": "Support", "icon": "🎧", "description": "Frontline"},
        headers=workspace_ctx.owner_headers,
    )
    assert create.status_code == 201, create.text
    team = create.json()
    assert team["icon"] == "🎧"
    assert team["members"] == []

    duplicate = await client.post(
        f"{workspace_ctx.base}/teams", json={"name": "Support"}, headers=workspace_ctx.owner_headers
    )
    assert duplicate.status_code == 409

    patch = await client.patch(
        f"{workspace_ctx.base}/teams/{team['id']}",
        json={"description": "Tier 1"},
        headers=workspace_ctx.owner_headers,
    )
    assert patch.json()["description"] == "Tier 1"

    delete = await client.delete(
        f"{workspace_ctx.base}/teams/{team['id']}", headers=workspace_ctx.owner_headers
    )
    assert delete.status_code == 200
    assert (
        await client.get(f"{workspace_ctx.base}/teams", headers=workspace_ctx.owner_headers)
    ).json() == []


async def test_team_member_add_remove(client, workspace_ctx):
    await workspace_ctx.add_member("teammate@example.com", role="agent")
    members = (
        await client.get(f"{workspace_ctx.base}/members", headers=workspace_ctx.owner_headers)
    ).json()
    teammate = next(m for m in members if m["user"]["email"] == "teammate@example.com")

    team = (
        await client.post(
            f"{workspace_ctx.base}/teams",
            json={"name": "Escalations"},
            headers=workspace_ctx.owner_headers,
        )
    ).json()

    add = await client.post(
        f"{workspace_ctx.base}/teams/{team['id']}/members",
        json={"user_id": teammate["user"]["id"]},
        headers=workspace_ctx.owner_headers,
    )
    assert add.status_code == 200, add.text
    assert [u["email"] for u in add.json()["members"]] == ["teammate@example.com"]

    # Idempotent duplicate add.
    again = await client.post(
        f"{workspace_ctx.base}/teams/{team['id']}/members",
        json={"user_id": teammate["user"]["id"]},
        headers=workspace_ctx.owner_headers,
    )
    assert len(again.json()["members"]) == 1

    remove = await client.delete(
        f"{workspace_ctx.base}/teams/{team['id']}/members/{teammate['user']['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert remove.status_code == 200
    assert remove.json()["members"] == []


async def test_team_member_must_be_workspace_member(client, workspace_ctx):
    outsider_auth = await signup(client, "team-outsider@example.com")
    me = await client.get("/api/v1/me", headers=bearer(outsider_auth))
    outsider_id = me.json()["user"]["id"]

    team = (
        await client.post(
            f"{workspace_ctx.base}/teams",
            json={"name": "Insiders"},
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    rejected = await client.post(
        f"{workspace_ctx.base}/teams/{team['id']}/members",
        json={"user_id": outsider_id},
        headers=workspace_ctx.owner_headers,
    )
    assert rejected.status_code == 400


async def test_team_mutations_forbidden_for_viewer(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("team-viewer@example.com", role="viewer")
    assert (
        await client.post(f"{workspace_ctx.base}/teams", json={"name": "V"}, headers=viewer)
    ).status_code == 403
    assert (await client.get(f"{workspace_ctx.base}/teams", headers=viewer)).status_code == 200


# ---------------------------------------------------------------------------
# canned responses
# ---------------------------------------------------------------------------


async def test_canned_response_crud_and_validation(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/canned-responses",
        json={"shortcut": "refund-policy", "content": "Refunds within **30 days**."},
        headers=workspace_ctx.owner_headers,
    )
    assert create.status_code == 201, create.text
    canned = create.json()
    assert canned["created_by"] is not None

    duplicate = await client.post(
        f"{workspace_ctx.base}/canned-responses",
        json={"shortcut": "refund-policy", "content": "other"},
        headers=workspace_ctx.owner_headers,
    )
    assert duplicate.status_code == 409

    with_spaces = await client.post(
        f"{workspace_ctx.base}/canned-responses",
        json={"shortcut": "has spaces", "content": "x"},
        headers=workspace_ctx.owner_headers,
    )
    assert with_spaces.status_code == 422

    patch = await client.patch(
        f"{workspace_ctx.base}/canned-responses/{canned['id']}",
        json={"content": "Hi {{contact.name}}!"},
        headers=workspace_ctx.owner_headers,
    )
    assert patch.json()["content"] == "Hi {{contact.name}}!"

    delete = await client.delete(
        f"{workspace_ctx.base}/canned-responses/{canned['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert delete.status_code == 200
    assert (
        await client.get(
            f"{workspace_ctx.base}/canned-responses", headers=workspace_ctx.owner_headers
        )
    ).json() == []


async def test_canned_response_authz(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("canned-viewer@example.com", role="viewer")
    agent = await workspace_ctx.add_member("canned-agent@example.com", role="agent")

    # Viewers may read but not write.
    assert (
        await client.get(f"{workspace_ctx.base}/canned-responses", headers=viewer)
    ).status_code == 200
    assert (
        await client.post(
            f"{workspace_ctx.base}/canned-responses",
            json={"shortcut": "nope", "content": "x"},
            headers=viewer,
        )
    ).status_code == 403
    # Agents (conversations:manage) may write.
    assert (
        await client.post(
            f"{workspace_ctx.base}/canned-responses",
            json={"shortcut": "greeting", "content": "Hi {{contact.name}}"},
            headers=agent,
        )
    ).status_code == 201
