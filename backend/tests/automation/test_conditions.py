"""Unit tests for the condition matcher (pure functions, no DB)."""

from __future__ import annotations

from app.automation.conditions import RuleContext, apply_op, field_value, matches
from app.core.events import Event
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message


def _ctx(
    *,
    status: str = "open",
    priority: str = "high",
    subject: str | None = "Refund request",
    inbox_id: str = "inbox-1",
    channel_type: str | None = "widget",
    email: str | None = "vip@acme.com",
    attributes: dict | None = None,
    content: str | None = "I need a refund please",
    tag_ids: list[str] | None = None,
) -> RuleContext:
    conversation = Conversation(
        workspace_id="w",
        number=1,
        inbox_id=inbox_id,
        contact_id="c",
        status=status,
        priority=priority,
        subject=subject,
    )
    contact = Contact(workspace_id="w", name="Nina", email=email, attributes=attributes or {})
    message = Message(
        workspace_id="w",
        conversation_id="conv",
        direction="in",
        author_type="contact",
        author_name="Nina",
        content=content or "",
    )
    return RuleContext(
        event=Event(name="message.created", workspace_id="w"),
        conversation=conversation,
        contact=contact,
        message=message,
        channel_type=channel_type,
        tag_ids=tag_ids or [],
    )


def test_field_value_resolution():
    ctx = _ctx(attributes={"plan": "enterprise"}, tag_ids=["t1", "t2"])
    assert field_value("status", ctx) == "open"
    assert field_value("priority", ctx) == "high"
    assert field_value("inbox_id", ctx) == "inbox-1"
    assert field_value("channel_type", ctx) == "widget"
    assert field_value("contact.email", ctx) == "vip@acme.com"
    assert field_value("contact.attributes.plan", ctx) == "enterprise"
    assert field_value("contact.attributes.missing", ctx) is None
    assert field_value("subject_contains", ctx) == "Refund request"
    assert field_value("content_contains", ctx) == "I need a refund please"
    assert field_value("tag", ctx) == ["t1", "t2"]


def test_op_eq_neq():
    assert apply_op("eq", "open", "open") is True
    assert apply_op("eq", "open", "resolved") is False
    assert apply_op("neq", "open", "resolved") is True
    assert apply_op("neq", "open", "open") is False
    # light coercion across JSON number/string
    assert apply_op("eq", 5, "5") is True


def test_op_contains_case_insensitive():
    assert apply_op("contains", "I need a REFUND please", "refund") is True
    assert apply_op("contains", "hello world", "goodbye") is False
    assert apply_op("contains", None, "x") is False


def test_op_in():
    assert apply_op("in", "high", ["high", "urgent"]) is True
    assert apply_op("in", "low", ["high", "urgent"]) is False


def test_op_exists():
    assert apply_op("exists", "vip@acme.com", None) is True
    assert apply_op("exists", None, None) is False
    assert apply_op("exists", "", None) is False
    assert apply_op("exists", [], None) is False
    assert apply_op("exists", ["t1"], None) is True


def test_op_on_tag_list():
    assert apply_op("eq", ["t1", "t2"], "t1") is True
    assert apply_op("eq", ["t1", "t2"], "t9") is False
    assert apply_op("contains", ["t1", "t2"], "t2") is True
    assert apply_op("neq", ["t1"], "t9") is True
    assert apply_op("in", ["t1", "t2"], ["t2", "t3"]) is True


def test_matches_and_semantics():
    ctx = _ctx(status="open", priority="high", attributes={"plan": "enterprise"})
    passing = [
        {"field": "status", "op": "eq", "value": "open"},
        {"field": "contact.attributes.plan", "op": "eq", "value": "enterprise"},
        {"field": "subject_contains", "op": "contains", "value": "refund"},
    ]
    assert matches(passing, ctx) is True

    failing = passing + [{"field": "priority", "op": "eq", "value": "low"}]
    assert matches(failing, ctx) is False


def test_empty_conditions_match_everything():
    assert matches([], _ctx()) is True
