"""Widget CSAT: rating recording, validation, ownership."""

from __future__ import annotations

import httpx

from tests.widget.conftest import WidgetSetup, auth_headers, boot


async def _conversation(client: httpx.AsyncClient, widget: WidgetSetup, visitor_id: str):
    token = (await boot(client, widget.widget_key, visitor_id=visitor_id)).json()["token"]
    created = await client.post(
        "/api/widget/conversations", json={"message": "hi"}, headers=auth_headers(token)
    )
    return token, created.json()["id"]


async def test_submit_csat(client: httpx.AsyncClient, widget: WidgetSetup):
    token, conversation_id = await _conversation(client, widget, "v1")
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/csat",
        json={"rating": 5, "feedback": "Great help!"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert body["rating"] == 5
    assert body["feedback"] == "Great help!"


async def test_csat_rating_out_of_range_422(client: httpx.AsyncClient, widget: WidgetSetup):
    token, conversation_id = await _conversation(client, widget, "v1")
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/csat",
        json={"rating": 9},
        headers=auth_headers(token),
    )
    assert response.status_code == 422


async def test_csat_cross_contact_404(client: httpx.AsyncClient, widget: WidgetSetup):
    _token_a, conversation_id = await _conversation(client, widget, "visitor-a")
    token_b = (await boot(client, widget.widget_key, visitor_id="visitor-b")).json()["token"]
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/csat",
        json={"rating": 3},
        headers=auth_headers(token_b),
    )
    assert response.status_code == 404
