"""The 422 envelope must stay JSON-serialisable and must not echo the payload.

Regression: an error raised from a Pydantic `@model_validator` carries the
original exception object under `ctx.error`. Serialising Pydantic's raw error
list then blew up inside `json.dumps`, turning a 422 into a 500.
"""

from __future__ import annotations

import json

from app.core.errors import _clean_errors


class TestCleanErrors:
    def test_non_serialisable_ctx_is_stringified(self):
        raw = [
            {
                "type": "value_error",
                "loc": ("body",),
                "msg": "Value error, boom",
                "input": {"password": "hunter2"},
                "ctx": {"error": ValueError("boom")},
            }
        ]
        cleaned = _clean_errors(raw)
        json.dumps(cleaned)  # must not raise
        assert cleaned[0]["ctx"]["error"] == "boom"

    def test_input_is_dropped(self):
        """`input` echoes the caller's body, which can hold credentials."""
        cleaned = _clean_errors([{"type": "x", "msg": "y", "input": {"password": "hunter2"}}])
        assert "input" not in cleaned[0]
        assert "hunter2" not in json.dumps(cleaned)

    def test_tuples_become_lists(self):
        cleaned = _clean_errors([{"loc": ("body", "name"), "msg": "required"}])
        assert cleaned[0]["loc"] == ["body", "name"]

    def test_non_dict_entries_survive(self):
        assert _clean_errors(["oops"]) == [{"msg": "oops"}]


class TestEndToEnd:
    async def test_model_validator_failure_returns_422_not_500(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "resolved"},
                "conversation_ids": ["x"],
                "query": {"match": "all", "conditions": []},
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "validation_failed"
        assert "exactly one of" in json.dumps(body["error"]["details"])
