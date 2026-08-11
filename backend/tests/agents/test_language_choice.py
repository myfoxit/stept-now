"""Reply-language selection: the LATEST visitor message wins.

Dogfood defect: a contact with older German conversations asked three English
questions and got German answers 3/3 — the stored locale hint outvoted the
message in front of the model. The rule (this repo's own W13 rule) is
per-message: detect on the latest inbound; the stored locale is only a tiebreak
for messages too short to carry evidence.
"""

from __future__ import annotations

from app.agents import engine
from app.ai.local import MockChatProvider
from app.core.db import session_scope
from app.models.conversation import Conversation
from tests.agents.conftest import (
    conversation_with_message,
    get_run,
    make_agent,
    public_messages,
    run_now,
)


class _RecordingProvider:
    """Delegates to the real mock provider, remembering every system prompt."""

    def __init__(self) -> None:
        self.inner = MockChatProvider()
        self.system_prompts: list[str] = []

    async def generate(self, request):
        system = next((m.content for m in request.messages if m.role == "system"), "") or ""
        self.system_prompts.append(system)
        return await self.inner.generate(request)

    def stream(self, request):  # pragma: no cover — unused
        raise NotImplementedError


async def _stamp_locale(conversation_id: str, locale: str) -> None:
    """What `services.language.learn_contact_locale` leaves behind after a few
    German messages: the thread-level locale stamp."""
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.attributes = {**conversation.attributes, "locale": locale}
        await session.commit()


async def test_english_question_after_german_history_gets_english_instruction(actx, monkeypatch):
    """The regression from conversation #9: EN question, DE stored locale."""
    recorder = _RecordingProvider()

    async def resolve(session, workspace_id, model_ref=None):
        return recorder, "mock"

    monkeypatch.setattr(engine, "resolve_chat", resolve)
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, "What is the difference between a problem and an alert?"
    )
    await _stamp_locale(conversation_id, "de")
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    assert (await get_run(run_id)).status == "completed"
    prompt = recorder.system_prompts[0]
    assert "most recent message is written in English" in prompt
    assert "Reply in English" in prompt
    assert "writing in German" not in prompt


async def test_german_message_gets_german_instruction_even_on_fresh_contact(actx, monkeypatch):
    recorder = _RecordingProvider()

    async def resolve(session, workspace_id, model_ref=None):
        return recorder, "mock"

    monkeypatch.setattr(engine, "resolve_chat", resolve)
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, "Wie kann ich eine neue Rufbereitschaft für mein Team einrichten?"
    )
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    assert (await get_run(run_id)).status == "completed"
    assert "most recent message is written in German" in recorder.system_prompts[0]


async def test_short_message_falls_back_to_stored_locale(actx, monkeypatch):
    """ "ok" carries no evidence — the stored conversation locale is the tiebreak."""
    recorder = _RecordingProvider()

    async def resolve(session, workspace_id, model_ref=None):
        return recorder, "mock"

    monkeypatch.setattr(engine, "resolve_chat", resolve)
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(actx, "ok")
    await _stamp_locale(conversation_id, "de")
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    assert (await get_run(run_id)).status == "completed"
    prompt = recorder.system_prompts[0]
    assert "has been writing in German" in prompt
    assert "follow them" in prompt


async def test_configured_reply_language_still_wins_over_detection(actx, monkeypatch):
    recorder = _RecordingProvider()

    async def resolve(session, workspace_id, model_ref=None):
        return recorder, "mock"

    monkeypatch.setattr(engine, "resolve_chat", resolve)
    settings = {
        "retrieval": {"enabled": True, "k": 6, "source_ids": None},
        "guardrails": {"max_tool_calls": 8, "require_citations": False},
        "reply_language": "ja",
    }
    agent_id = await make_agent(actx, settings=settings)
    conversation_id, message_id = await conversation_with_message(
        actx, "What is the difference between a problem and an alert?"
    )
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    assert (await get_run(run_id)).status == "completed"
    assert "Always reply in Japanese" in recorder.system_prompts[0]


async def test_full_flow_still_produces_a_reply(actx):
    """End to end with the plain mock: nothing in the language path breaks the run."""
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, "What is the difference between a problem and an alert?"
    )
    await _stamp_locale(conversation_id, "de")
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    assert (await get_run(run_id)).status == "completed"
    assert [m for m in await public_messages(conversation_id) if m.author_type == "agent"]
