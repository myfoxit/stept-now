"""Workspace CRUD, membership management, RBAC enforcement, custom roles."""

from tests.conftest import bearer, signup


async def test_create_and_get_workspace(client, workspace_ctx):
    response = await client.get(workspace_ctx.base, headers=workspace_ctx.owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Acme Support"
    assert "identity_secret" not in body["settings"]  # credential, not configuration

    me = await client.get("/api/v1/me", headers=workspace_ctx.owner_headers)
    membership = me.json()["memberships"][0]
    assert membership["role"] == "owner"
    assert "workspace:delete" in membership["permissions"]


async def test_identity_secret_needs_workspace_manage(client, workspace_ctx):
    """It forges any visitor's identity, so membership alone must not reveal it."""
    reveal = await client.get(
        f"{workspace_ctx.base}/identity-secret", headers=workspace_ctx.owner_headers
    )
    assert reveal.status_code == 200
    secret = reveal.json()["identity_secret"]
    assert secret  # auto-generated at workspace creation

    for role in ("viewer", "agent"):
        headers = await workspace_ctx.add_member(f"{role}@example.com", role=role)
        # Not in the workspace payload…
        listed = await client.get(workspace_ctx.base, headers=headers)
        assert "identity_secret" not in listed.json()["settings"]
        # …nor in the membership payload embedded in /me…
        me = await client.get("/api/v1/me", headers=headers)
        for membership in me.json()["memberships"]:
            assert "identity_secret" not in membership["workspace"]["settings"]
        # …nor readable from the dedicated endpoint.
        denied = await client.get(f"{workspace_ctx.base}/identity-secret", headers=headers)
        assert denied.status_code == 403, role


async def test_identity_secret_stays_unsettable_through_the_api(client, workspace_ctx):
    before = (
        await client.get(
            f"{workspace_ctx.base}/identity-secret", headers=workspace_ctx.owner_headers
        )
    ).json()["identity_secret"]
    patch = await client.patch(
        workspace_ctx.base,
        json={"settings": {"identity_secret": "attacker-chosen", "brand_color": "#fff"}},
        headers=workspace_ctx.owner_headers,
    )
    assert patch.status_code == 200
    after = (
        await client.get(
            f"{workspace_ctx.base}/identity-secret", headers=workspace_ctx.owner_headers
        )
    ).json()["identity_secret"]
    assert after == before
    assert patch.json()["settings"]["brand_color"] == "#fff"


async def test_cross_workspace_access_forbidden(client, workspace_ctx):
    outsider = await signup(client, "outsider@example.com")
    response = await client.get(workspace_ctx.base, headers=bearer(outsider))
    assert response.status_code == 403


async def test_invite_accept_and_role_enforcement(client, workspace_ctx):
    agent_headers = await workspace_ctx.add_member("sam@example.com", role="agent")

    # Agent can read members but cannot manage them.
    listing = await client.get(f"{workspace_ctx.base}/members", headers=agent_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 2

    invite = await client.post(
        f"{workspace_ctx.base}/invitations",
        json={"email": "third@example.com", "role": "agent"},
        headers=agent_headers,
    )
    assert invite.status_code == 403

    # Agent cannot update workspace settings.
    patch = await client.patch(workspace_ctx.base, json={"name": "Hacked"}, headers=agent_headers)
    assert patch.status_code == 403


async def test_viewer_is_read_only(client, workspace_ctx):
    viewer_headers = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    keys = await client.get(f"{workspace_ctx.base}/api-keys", headers=viewer_headers)
    assert keys.status_code == 403  # apikeys:manage required even to list


async def test_last_owner_protection(client, workspace_ctx):
    members = (
        await client.get(f"{workspace_ctx.base}/members", headers=workspace_ctx.owner_headers)
    ).json()
    owner_member = next(m for m in members if m["role"] == "owner")

    demote = await client.patch(
        f"{workspace_ctx.base}/members/{owner_member['id']}",
        json={"role": "agent"},
        headers=workspace_ctx.owner_headers,
    )
    assert demote.status_code == 409

    remove = await client.delete(
        f"{workspace_ctx.base}/members/{owner_member['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert remove.status_code == 409


async def test_custom_role_lifecycle(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/roles",
        json={
            "name": "Knowledge Editor",
            "description": "Can curate the KB",
            "permissions": ["knowledge:read", "knowledge:write", "conversations:read"],
        },
        headers=workspace_ctx.owner_headers,
    )
    assert create.status_code == 201, create.text
    role_id = create.json()["id"]

    # Assign the custom role to a member and verify their effective permissions.
    member_headers = await workspace_ctx.add_member("editor@example.com", role="viewer")
    members = (
        await client.get(f"{workspace_ctx.base}/members", headers=workspace_ctx.owner_headers)
    ).json()
    editor = next(m for m in members if m["user"]["email"] == "editor@example.com")
    assign = await client.patch(
        f"{workspace_ctx.base}/members/{editor['id']}",
        json={"role": "custom", "custom_role_id": role_id},
        headers=workspace_ctx.owner_headers,
    )
    assert assign.status_code == 200

    me = await client.get("/api/v1/me", headers=member_headers)
    perms = me.json()["memberships"][0]["permissions"]
    assert "knowledge:write" in perms
    assert "members:manage" not in perms

    # Deleting an in-use role is blocked.
    delete = await client.delete(
        f"{workspace_ctx.base}/roles/{role_id}", headers=workspace_ctx.owner_headers
    )
    assert delete.status_code == 409

    # Unknown permission strings are rejected.
    bad = await client.post(
        f"{workspace_ctx.base}/roles",
        json={"name": "Broken", "permissions": ["not:a-perm"]},
        headers=workspace_ctx.owner_headers,
    )
    assert bad.status_code == 400


async def test_invitation_validation(client, workspace_ctx):
    await workspace_ctx.add_member("dup-member@example.com", role="agent")
    # Already a member → conflict.
    response = await client.post(
        f"{workspace_ctx.base}/invitations",
        json={"email": "dup-member@example.com", "role": "agent"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 409
    # Owner invites are rejected.
    response = await client.post(
        f"{workspace_ctx.base}/invitations",
        json={"email": "new-owner@example.com", "role": "owner"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 400


async def test_workspace_delete_owner_only(client, workspace_ctx):
    admin_headers = await workspace_ctx.add_member("admin@example.com", role="admin")
    forbidden = await client.delete(workspace_ctx.base, headers=admin_headers)
    assert forbidden.status_code == 403
    allowed = await client.delete(workspace_ctx.base, headers=workspace_ctx.owner_headers)
    assert allowed.status_code == 200
    gone = await client.get(workspace_ctx.base, headers=workspace_ctx.owner_headers)
    assert gone.status_code == 403


async def test_workspace_and_owner_membership_survive_foreign_keys(client):
    """Regression: the owner Membership must not be inserted before its Workspace.

    Membership carries only a raw workspace_id FK — no relationship() to Workspace
    — so SQLAlchemy's unit of work has no ordering edge and a single combined
    flush was free to write memberships first. Postgres rejects that; SQLite used
    to accept it, so signup worked in every test and failed in production.
    """
    from sqlalchemy import select

    from app.core.db import get_session_factory
    from app.models.workspace import Membership, Workspace

    auth = await signup(client, "fk-order@example.com", name="FK Order")
    created = await client.post(
        "/api/v1/workspaces", json={"name": "FK Order Co"}, headers=bearer(auth)
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]

    async with get_session_factory()() as session:
        workspace = await session.get(Workspace, workspace_id)
        assert workspace is not None
        membership = (
            await session.execute(select(Membership).where(Membership.workspace_id == workspace_id))
        ).scalar_one()
        assert membership.role == "owner"
