"""Segments API: CRUD, filter validation, preview, use in the directory list."""


async def _make_contacts(client, base, headers):
    for name, plan in [("Enda", "enterprise"), ("Erin", "enterprise"), ("Paul", "pro")]:
        response = await client.post(
            f"{base}/contacts",
            json={"name": name, "attributes": {"plan": plan}},
            headers=headers,
        )
        assert response.status_code == 201, response.text


async def test_segment_crud(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/segments",
        json={
            "name": "Enterprise customers",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
        headers=workspace_ctx.owner_headers,
    )
    assert create.status_code == 201, create.text
    segment = create.json()
    assert segment["filters"][0]["field"] == "attributes.plan"

    listing = await client.get(
        f"{workspace_ctx.base}/segments", headers=workspace_ctx.owner_headers
    )
    assert [s["name"] for s in listing.json()] == ["Enterprise customers"]

    patch = await client.patch(
        f"{workspace_ctx.base}/segments/{segment['id']}",
        json={"filters": [{"field": "verified", "op": "eq", "value": True}]},
        headers=workspace_ctx.owner_headers,
    )
    assert patch.status_code == 200
    assert patch.json()["filters"][0]["field"] == "verified"

    delete = await client.delete(
        f"{workspace_ctx.base}/segments/{segment['id']}", headers=workspace_ctx.owner_headers
    )
    assert delete.status_code == 200
    assert (
        await client.get(f"{workspace_ctx.base}/segments", headers=workspace_ctx.owner_headers)
    ).json() == []


async def test_segment_rejects_bad_filters(client, workspace_ctx):
    unknown_field = await client.post(
        f"{workspace_ctx.base}/segments",
        json={"name": "Bad", "filters": [{"field": "password", "op": "eq", "value": "x"}]},
        headers=workspace_ctx.owner_headers,
    )
    assert unknown_field.status_code == 422
    unknown_op = await client.post(
        f"{workspace_ctx.base}/segments",
        json={"name": "Bad", "filters": [{"field": "email", "op": "regex", "value": "x"}]},
        headers=workspace_ctx.owner_headers,
    )
    assert unknown_op.status_code == 422
    # Op/field combinations that can't compile are rejected too (422).
    bad_combo = await client.post(
        f"{workspace_ctx.base}/segments",
        json={"name": "Bad", "filters": [{"field": "email", "op": "gt", "value": "x"}]},
        headers=workspace_ctx.owner_headers,
    )
    assert bad_combo.status_code == 422


async def test_segment_preview_contacts(client, workspace_ctx):
    await _make_contacts(client, workspace_ctx.base, workspace_ctx.owner_headers)
    segment = (
        await client.post(
            f"{workspace_ctx.base}/segments",
            json={
                "name": "Enterprise",
                "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
            },
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    preview = await client.get(
        f"{workspace_ctx.base}/segments/{segment['id']}/contacts",
        headers=workspace_ctx.owner_headers,
    )
    assert preview.status_code == 200, preview.text
    assert {c["name"] for c in preview.json()} == {"Enda", "Erin"}


async def test_directory_list_filtered_by_segment_with_pagination(client, workspace_ctx):
    await _make_contacts(client, workspace_ctx.base, workspace_ctx.owner_headers)
    segment = (
        await client.post(
            f"{workspace_ctx.base}/segments",
            json={
                "name": "Enterprise",
                "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
            },
            headers=workspace_ctx.owner_headers,
        )
    ).json()

    page1 = await client.get(
        f"{workspace_ctx.base}/contacts?segment_id={segment['id']}&limit=1",
        headers=workspace_ctx.owner_headers,
    )
    body1 = page1.json()
    assert len(body1["items"]) == 1
    assert body1["items"][0]["attributes"]["plan"] == "enterprise"
    assert body1["next_cursor"]

    page2 = await client.get(
        f"{workspace_ctx.base}/contacts?segment_id={segment['id']}&limit=1"
        f"&cursor={body1['next_cursor']}",
        headers=workspace_ctx.owner_headers,
    )
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["items"][0]["attributes"]["plan"] == "enterprise"
    assert body2["items"][0]["id"] != body1["items"][0]["id"]
    assert body2["next_cursor"] is None

    missing = await client.get(
        f"{workspace_ctx.base}/contacts?segment_id=00000000-0000-7000-8000-0000000000ff",
        headers=workspace_ctx.owner_headers,
    )
    assert missing.status_code == 404


async def test_segment_write_requires_contacts_write(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("segment-viewer@example.com", role="viewer")
    agent = await workspace_ctx.add_member("segment-agent@example.com", role="agent")

    assert (await client.get(f"{workspace_ctx.base}/segments", headers=viewer)).status_code == 200
    assert (
        await client.post(
            f"{workspace_ctx.base}/segments", json={"name": "V", "filters": []}, headers=viewer
        )
    ).status_code == 403
    assert (
        await client.post(
            f"{workspace_ctx.base}/segments", json={"name": "A", "filters": []}, headers=agent
        )
    ).status_code == 201
