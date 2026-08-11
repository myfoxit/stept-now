"""Boot payload branding/behavior config.

Dogfood defect: the bound agent's display name ("Northplane Guide") existed
server-side and never reached the UI. Boot now plumbs `brand_display_name`,
`agent_display_name`, `ai_disclosure`, and `tour_autostart_policy` from the
widget inbox settings JSON, with server-side fallbacks.
"""

from __future__ import annotations

from app.core.db import get_session_factory
from app.models.agent import Agent
from app.models.inbox import Inbox
from tests.widget.conftest import boot, create_widget_setup


async def _configure(widget, config_updates: dict) -> None:
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, widget.inbox_id)
        inbox.config = {**(inbox.config or {}), **config_updates}
        await session.commit()


async def _make_agent(workspace_id: str, name: str) -> str:
    async with get_session_factory()() as session:
        agent = Agent(workspace_id=workspace_id, name=name, status="live")
        session.add(agent)
        await session.commit()
        return agent.id


async def test_boot_defaults_brand_to_workspace_and_policy_to_ask(client, widget):
    resp = await boot(client, widget.widget_key)
    assert resp.status_code == 200, resp.text
    config = resp.json()["config"]
    assert config["brand_display_name"] == "Acme Support"  # workspace name fallback
    assert config["agent_display_name"] is None
    assert config["ai_disclosure"] is True
    assert config["tour_autostart_policy"] == "ask"


async def test_boot_exposes_configured_branding_and_agent_name(client):
    widget = await create_widget_setup()
    agent_id = await _make_agent(widget.workspace_id, "Northplane Guide")
    await _configure(
        widget,
        {
            "brand_display_name": "Northplane",
            "ai_agent_id": agent_id,
            "ai_disclosure": False,
            "tour_autostart_policy": "auto",
        },
    )

    resp = await boot(client, widget.widget_key)
    assert resp.status_code == 200, resp.text
    config = resp.json()["config"]
    assert config["brand_display_name"] == "Northplane"
    assert config["agent_display_name"] == "Northplane Guide"
    assert config["ai_disclosure"] is False
    assert config["tour_autostart_policy"] == "auto"


async def test_boot_sanitizes_bad_values(client):
    widget = await create_widget_setup()
    await _configure(
        widget,
        {
            "brand_display_name": "   ",  # whitespace → fall back to workspace name
            "ai_agent_id": "00000000-0000-7000-8000-00000000dead",  # dangling ref
            "tour_autostart_policy": "yolo",  # unknown → "ask"
        },
    )
    resp = await boot(client, widget.widget_key)
    assert resp.status_code == 200, resp.text
    config = resp.json()["config"]
    assert config["brand_display_name"] == "Acme Support"
    assert config["agent_display_name"] is None
    assert config["tour_autostart_policy"] == "ask"
