"""Custom HTTP actions: manual param validation, templating, host enforcement
(redirects + host injection blocked), header decryption, and action-in-loop."""

from __future__ import annotations

import httpx
import respx

from app.agents.tools import execute_custom_action, template_string, validate_params
from app.core.db import session_scope, uuid7
from app.core.security import encrypt_secret
from app.models.agent import CustomAction
from tests.agents.conftest import (
    conversation_with_message,
    get_run,
    get_steps,
    make_agent,
    run_now,
)

ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}


async def make_action(
    actx,
    *,
    name: str = "lookup_order",
    method: str = "GET",
    url: str = "https://api.orders.test/orders/{order_id}",
    headers: dict[str, str] | None = None,
    body_template: str | None = None,
    params_schema: dict | None = None,
) -> str:
    async with session_scope() as session:
        action = CustomAction(
            workspace_id=actx.workspace_id,
            name=name,
            description=f"{name} action",
            method=method,
            url=url,
            headers={k: encrypt_secret(v) for k, v in (headers or {}).items()},
            body_template=body_template,
            params_schema=params_schema if params_schema is not None else ORDER_SCHEMA,
        )
        session.add(action)
        await session.commit()
        return action.id


async def _load(action_id: str) -> CustomAction:
    async with session_scope() as session:
        action = await session.get(CustomAction, action_id)
        assert action is not None
        return action


# --- unit: validation + templating ------------------------------------------


def test_validate_params_required_and_types():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["a"],
    }
    assert validate_params(schema, {"a": "x"}) is None
    assert "missing required" in (validate_params(schema, {}) or "")
    assert "type" in (validate_params(schema, {"a": 5}) or "")
    assert "type" in (validate_params(schema, {"a": "x", "n": "no"}) or "")
    assert validate_params(schema, {"a": "x", "n": 3}) is None
    # bool must never satisfy integer/number
    assert "type" in (validate_params(schema, {"a": "x", "n": True}) or "")
    # empty schema disables validation
    assert validate_params({}, {"whatever": 1}) is None


def test_template_string_url_encodes():
    assert template_string("/orders/{id}", {"id": "a b"}, url_encode=True) == "/orders/a%20b"
    assert template_string('{"q":"{q}"}', {"q": "hi"}) == '{"q":"hi"}'
    assert template_string("/x/{missing}", {}) == "/x/"


# --- execute_custom_action --------------------------------------------------


async def test_custom_action_success_with_templating(actx):
    action_id = await make_action(
        actx,
        method="POST",
        url="https://api.orders.test/orders/{order_id}",
        body_template='{"note": "for {order_id}"}',
    )
    action = await _load(action_id)
    with respx.mock:
        route = respx.post("https://api.orders.test/orders/A42").mock(
            return_value=httpx.Response(200, json={"status": "shipped"})
        )
        result = await execute_custom_action(action, {"order_id": "A42"})
    assert result.ok and result.status == 200
    assert "shipped" in (result.body or "")
    assert route.called
    assert b'"note": "for A42"' in route.calls.last.request.content


async def test_custom_action_decrypts_headers_at_call_time(actx):
    action_id = await make_action(
        actx,
        method="GET",
        url="https://api.orders.test/orders/{order_id}",
        headers={"Authorization": "Bearer super-secret"},
    )
    action = await _load(action_id)
    # Stored header value is encrypted, not the plaintext.
    assert action.headers["Authorization"] != "Bearer super-secret"
    with respx.mock:
        route = respx.get("https://api.orders.test/orders/A1").mock(
            return_value=httpx.Response(200, text="ok")
        )
        result = await execute_custom_action(action, {"order_id": "A1"})
    assert result.ok
    assert route.calls.last.request.headers["Authorization"] == "Bearer super-secret"


async def test_custom_action_schema_validation_error_skips_http(actx):
    action_id = await make_action(actx, method="GET")
    action = await _load(action_id)
    with respx.mock:
        route = respx.get(url__regex=r".*").mock(return_value=httpx.Response(200))
        result = await execute_custom_action(action, {})  # missing required order_id
    assert not result.ok
    assert "missing required parameter" in (result.error or "")
    assert not route.called  # never left the process


async def test_custom_action_redirect_to_other_host_blocked(actx):
    action_id = await make_action(actx, method="GET", url="https://api.safe.test/orders/{order_id}")
    action = await _load(action_id)
    with respx.mock:
        respx.get("https://api.safe.test/orders/A42").mock(
            return_value=httpx.Response(302, headers={"Location": "https://evil.test/steal"})
        )
        result = await execute_custom_action(action, {"order_id": "A42"})
    assert not result.ok
    assert "evil.test" in (result.error or "")


async def test_custom_action_host_injection_blocked(actx):
    # A parameter in the host position can never match the configured (literal) host.
    action_id = await make_action(
        actx,
        method="GET",
        url="https://{tenant}.api.test/data",
        params_schema={"type": "object", "properties": {"tenant": {"type": "string"}}},
    )
    action = await _load(action_id)
    with respx.mock:
        route = respx.get(url__regex=r".*").mock(return_value=httpx.Response(200))
        result = await execute_custom_action(action, {"tenant": "evil"})
    assert not result.ok
    assert "host" in (result.error or "").lower()
    assert not route.called


async def test_action_runs_inside_the_agent_loop(actx):
    action_id = await make_action(
        actx, method="GET", url="https://api.orders.test/orders/{order_id}"
    )
    agent_id = await make_agent(actx, tools=[{"key": f"action:{action_id}", "policy": "auto"}])
    conversation_id, _ = await conversation_with_message(
        actx, 'Look up my order [[tool:lookup_order {"order_id": "A42"}]]'
    )
    with respx.mock:
        respx.get("https://api.orders.test/orders/A42").mock(
            return_value=httpx.Response(200, json={"status": "shipped"})
        )
        run_id = await run_now(actx, agent_id, conversation_id)

    assert (await get_run(run_id)).status == "completed"
    tool_results = [
        s for s in await get_steps(run_id) if s.kind == "tool_result" and s.name == "lookup_order"
    ]
    assert tool_results and tool_results[0].output.get("status") == 200


async def test_action_default_policy_requires_approval(actx):
    # An action listed without an explicit policy inherits require_approval.
    action_id = await make_action(actx, name=f"act_{uuid7()[:6]}", method="GET")
    agent_id = await make_agent(actx, tools=[{"key": f"action:{action_id}"}])
    async with session_scope() as session:
        action = await session.get(CustomAction, action_id)
        assert action is not None
        action_name = action.name
    conversation_id, _ = await conversation_with_message(
        actx, f'[[tool:{action_name} {{"order_id": "A1"}}]]'
    )
    run_id = await run_now(actx, agent_id, conversation_id)
    assert (await get_run(run_id)).status == "awaiting_approval"
