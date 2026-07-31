"""Extension API (/api/widget/dap): token mint + authorize, tour CRUD,
optimistic step saves, draft-only meta edits, auth/check, authz + isolation."""

from __future__ import annotations

from datetime import timedelta

import jwt

from app.core.config import get_settings
from app.core.db import utcnow, uuid7
from app.core.security import (
    EXTENSION_TOKEN_TTL_DAYS,
    create_extension_token,
    create_recorder_token,
    create_widget_token,
)
from tests.tours.conftest import (
    create_tour,
    extension_headers,
    publish_tour,
    user_id_from_headers,
)

STEP = {"type": "tooltip", "selector": "#save", "title": "Save", "text_hint": "Save"}


async def test_extension_token_mint_and_ttl(client, workspace_ctx):
    resp = await client.post(
        f"{workspace_ctx.base}/tours/extension-token", headers=workspace_ctx.owner_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_days"] == EXTENSION_TOKEN_TTL_DAYS == 30

    payload = jwt.decode(
        body["token"], get_settings().secret_key, algorithms=["HS256"], issuer="stept"
    )
    assert payload["typ"] == "extension"
    assert payload["ws"] == workspace_ctx.id


async def test_recorder_token_ttl_is_single_sourced(client, workspace_ctx):
    from app.core.security import RECORDER_TOKEN_TTL_DAYS

    resp = await client.post(
        f"{workspace_ctx.base}/tours/recorder-token", headers=workspace_ctx.owner_headers
    )
    assert resp.json()["expires_days"] == RECORDER_TOKEN_TTL_DAYS == 7


async def test_extension_token_requires_manage(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("ext-viewer@example.com", role="viewer")
    denied = await client.post(f"{workspace_ctx.base}/tours/extension-token", headers=viewer)
    assert denied.status_code == 403


async def test_auth_check_reports_identity(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    resp = await client.post("/api/widget/dap/auth/check", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == workspace_ctx.id
    assert body["workspace_name"] == "Acme Support"
    assert body["user_name"] == "Owner"
    assert body["perms_ok"] is True


async def test_recorder_token_accepted_for_back_compat(client, workspace_ctx):
    user_id = user_id_from_headers(workspace_ctx.owner_headers)
    legacy = {"Authorization": f"Bearer {create_recorder_token(workspace_ctx.id, user_id)}"}
    assert (await client.post("/api/widget/dap/auth/check", headers=legacy)).status_code == 200


async def test_extension_auth_failures(client, workspace_ctx):
    # No header at all.
    assert (await client.post("/api/widget/dap/auth/check")).status_code == 401
    # Garbage / wrong type.
    for token in ("nonsense", create_widget_token(workspace_ctx.id, uuid7())):
        resp = await client.post(
            "/api/widget/dap/auth/check", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401
    # Well-signed but the subject is not a member.
    stranger = create_extension_token(workspace_ctx.id, uuid7())
    resp = await client.post(
        "/api/widget/dap/auth/check", headers={"Authorization": f"Bearer {stranger}"}
    )
    assert resp.status_code == 403


async def test_expired_extension_token_401(client, workspace_ctx):
    now = utcnow()
    expired = jwt.encode(
        {
            "ws": workspace_ctx.id,
            "sub": user_id_from_headers(workspace_ctx.owner_headers),
            "iss": "stept",
            "typ": "extension",
            "iat": int((now - timedelta(days=40)).timestamp()),
            "exp": int((now - timedelta(days=1)).timestamp()),
        },
        get_settings().secret_key,
        algorithm="HS256",
    )
    resp = await client.post(
        "/api/widget/dap/auth/check", headers={"Authorization": f"Bearer {expired}"}
    )
    assert resp.status_code == 401


async def test_extension_permission_revalidated_per_call(client, workspace_ctx):
    """A viewer's token (minted out-of-band) is refused by every dap endpoint."""
    viewer = await workspace_ctx.add_member("ext-noperm@example.com", role="viewer")
    token = create_extension_token(workspace_ctx.id, user_id_from_headers(viewer))
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/widget/dap/tours", headers=headers)).status_code == 403
    created = await client.post(
        "/api/widget/dap/tours", json={"name": "X", "steps": [STEP]}, headers=headers
    )
    assert created.status_code == 403


async def test_dap_tour_crud_roundtrip(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)

    created = await client.post(
        "/api/widget/dap/tours",
        json={
            "name": "Recorded checkout",
            "url_pattern": "*/checkout*",
            "steps": [
                {**STEP, "target": {"selectors": [{"kind": "css", "value": "#save"}]}},
                {"type": "action", "selector": "#pay", "action": {"kind": "click"}},
                {"type": "wait", "wait": {"for": "url", "url_pattern": "*/thanks*"}},
            ],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    tour = created.json()
    assert tour["status"] == "draft"
    assert tour["version"] == 1
    assert tour["trigger"] == {"type": "url_match", "url_pattern": "*/checkout*"}
    # Missing titles are filled in, rich fields survive.
    assert [s["title"] for s in tour["steps"]] == ["Save", "Step 2", "Step 3"]
    assert tour["steps"][0]["target"]["selectors"][0]["value"] == "#save"
    assert tour["steps"][2]["wait"]["url_pattern"] == "*/thanks*"

    listing = await client.get("/api/widget/dap/tours", headers=headers)
    assert listing.status_code == 200
    row = next(t for t in listing.json() if t["id"] == tour["id"])
    assert row == {
        "id": tour["id"],
        "name": "Recorded checkout",
        "kind": "flow",
        "status": "draft",
        "steps_count": 3,
        "version": 1,
        "updated_at": row["updated_at"],
    }

    detail = await client.get(f"/api/widget/dap/tours/{tour['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "Recorded checkout"

    # And the dashboard sees the same draft.
    app_view = await client.get(
        f"{workspace_ctx.base}/tours/{tour['id']}", headers=workspace_ctx.owner_headers
    )
    assert app_view.json()["steps"][1]["action"]["kind"] == "click"


async def test_put_steps_optimistic_concurrency(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    tour = await create_tour(client, workspace_ctx, steps=[STEP])
    tid = tour["id"]

    saved = await client.put(
        f"/api/widget/dap/tours/{tid}/steps",
        json={"steps": [STEP, {**STEP, "selector": "#next", "title": "Next"}], "base_version": 1},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    assert len(saved.json()["steps"]) == 2

    # A stale extension (still on v1) must not clobber the dashboard's edit.
    stale = await client.put(
        f"/api/widget/dap/tours/{tid}/steps",
        json={"steps": [STEP], "base_version": 1},
        headers=headers,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["details"]["current_version"] == 2

    unchanged = await client.get(
        f"{workspace_ctx.base}/tours/{tid}", headers=workspace_ctx.owner_headers
    )
    assert len(unchanged.json()["steps"]) == 2


async def test_put_steps_screenshot_only_change_keeps_version(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    tour = await create_tour(client, workspace_ctx, steps=[STEP])
    resp = await client.put(
        f"/api/widget/dap/tours/{tour['id']}/steps",
        json={"steps": [{**STEP, "screenshot_key": "public/ws/shot.png"}], "base_version": 1},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 1
    assert resp.json()["steps"][0]["screenshot_key"] == "public/ws/shot.png"


async def test_patch_draft_meta_and_live_conflict(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    tour = await create_tour(client, workspace_ctx, name="Untitled", steps=[STEP])

    renamed = await client.patch(
        f"/api/widget/dap/tours/{tour['id']}",
        json={"name": "Checkout flow", "url_pattern": "*/cart*"},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Checkout flow"
    assert renamed.json()["trigger"] == {"type": "url_match", "url_pattern": "*/cart*"}

    # Clearing the pattern reverts to a manual trigger.
    cleared = await client.patch(
        f"/api/widget/dap/tours/{tour['id']}", json={"url_pattern": None}, headers=headers
    )
    assert cleared.json()["trigger"]["type"] == "manual"

    await publish_tour(client, workspace_ctx, tour["id"])
    blocked = await client.patch(
        f"/api/widget/dap/tours/{tour['id']}", json={"name": "Nope"}, headers=headers
    )
    assert blocked.status_code == 409


async def test_dap_endpoints_are_workspace_scoped(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    foreign = await client.post(
        f"{other_base}/tours",
        json={"name": "Foreign", "steps": [STEP]},
        headers=workspace_ctx.owner_headers,
    )
    fid = foreign.json()["id"]

    assert (await client.get(f"/api/widget/dap/tours/{fid}", headers=headers)).status_code == 404
    put = await client.put(
        f"/api/widget/dap/tours/{fid}/steps",
        json={"steps": [STEP], "base_version": 1},
        headers=headers,
    )
    assert put.status_code == 404
    patched = await client.patch(
        f"/api/widget/dap/tours/{fid}", json={"name": "x"}, headers=headers
    )
    assert patched.status_code == 404
    assert [
        t["id"] for t in (await client.get("/api/widget/dap/tours", headers=headers)).json()
    ] != [fid]


async def test_legacy_recorder_endpoint_still_works(client, workspace_ctx):
    """The paste-a-token flow keeps working, delegating to the same service."""
    mint = await client.post(
        f"{workspace_ctx.base}/tours/recorder-token", headers=workspace_ctx.owner_headers
    )
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={
            "token": mint.json()["token"],
            "name": "Legacy flow",
            "url_pattern": "*/settings*",
            "steps": [{"selector": "#a"}, {"selector": "#b", "title": "Second"}],
        },
    )
    assert resp.status_code == 201, resp.text
    got = await client.get(
        f"{workspace_ctx.base}/tours/{resp.json()['id']}", headers=workspace_ctx.owner_headers
    )
    tour = got.json()
    assert [s["title"] for s in tour["steps"]] == ["Step 1", "Second"]
    assert [s["type"] for s in tour["steps"]] == ["tooltip", "tooltip"]
