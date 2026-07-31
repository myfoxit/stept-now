"""POST /api/widget/boot: identity verification, require_identity, visitor persistence."""

from __future__ import annotations

import httpx

from tests.widget.conftest import (
    WidgetSetup,
    boot,
    create_widget_setup,
    identity_payload,
)


async def test_boot_anonymous_happy_path(client: httpx.AsyncClient, widget: WidgetSetup):
    response = await boot(client, widget.widget_key, visitor_id="visitor-1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["visitor_id"] == "visitor-1"
    assert body["contact"]["id"]
    assert body["workspace"]["name"] == "Acme Support"
    assert body["config"]["greeting"] == "Hi there"
    assert body["conversations"] == []
    assert body["help_center_enabled"] is False


async def test_boot_unknown_widget_key_404(client: httpx.AsyncClient, widget: WidgetSetup):
    response = await boot(client, "wk_does_not_exist")
    assert response.status_code == 404


async def test_boot_mints_visitor_id_when_absent(client: httpx.AsyncClient, widget: WidgetSetup):
    response = await boot(client, widget.widget_key)
    assert response.status_code == 200
    assert response.json()["visitor_id"]  # server-minted uuid returned to persist


async def test_boot_visitor_persistence_same_contact(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    first = await boot(client, widget.widget_key, visitor_id="stable-visitor")
    second = await boot(client, widget.widget_key, visitor_id="stable-visitor")
    assert first.json()["contact"]["id"] == second.json()["contact"]["id"]


async def test_boot_distinct_visitors_distinct_contacts(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    first = await boot(client, widget.widget_key, visitor_id="visitor-a")
    second = await boot(client, widget.widget_key, visitor_id="visitor-b")
    assert first.json()["contact"]["id"] != second.json()["contact"]["id"]


async def test_boot_identity_verified(client: httpx.AsyncClient, widget: WidgetSetup):
    identity = identity_payload("user-42", email="user42@acme.com", name="User 42")
    response = await boot(client, widget.widget_key, identity=identity)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contact"]["email"] == "user42@acme.com"
    assert body["contact"]["name"] == "User 42"


async def test_boot_identity_hash_mismatch_403(client: httpx.AsyncClient, widget: WidgetSetup):
    response = await boot(
        client,
        widget.widget_key,
        identity={"external_id": "user-42", "hash": "deadbeef", "email": "user42@acme.com"},
    )
    assert response.status_code == 403


async def test_boot_identity_same_external_id_same_contact(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    identity = identity_payload("user-99")
    first = await boot(client, widget.widget_key, identity=identity)
    second = await boot(client, widget.widget_key, identity=identity)
    assert first.json()["contact"]["id"] == second.json()["contact"]["id"]


async def test_boot_require_identity_without_identity(client: httpx.AsyncClient):
    setup = await create_widget_setup(require_identity=True)
    response = await boot(client, setup.widget_key)
    assert response.status_code == 200
    body = response.json()
    assert body == {"require_identity": True}
    assert "token" not in body


async def test_boot_require_identity_with_identity(client: httpx.AsyncClient):
    setup = await create_widget_setup(require_identity=True)
    response = await boot(client, setup.widget_key, identity=identity_payload("vip-1"))
    assert response.status_code == 200
    assert response.json()["token"]
