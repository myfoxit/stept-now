"""Query understanding and context assembly — the two deterministic layers
around retrieval.

Both are pure functions, so these tests pin behaviour rather than relevance:
what the rewriter does to a messy question, and what the builder does when the
budget runs out.
"""

from __future__ import annotations

import pytest

from app.rag.context import (
    BuiltContext,
    build_context,
    compress,
    estimate_tokens,
)
from app.rag.query import (
    INTENT_COMPARATIVE,
    INTENT_FACTUAL,
    INTENT_NAVIGATIONAL,
    INTENT_PROCEDURAL,
    INTENT_TROUBLESHOOTING,
    classify_query,
    reset_caches,
    rewrite_query,
)
from app.rag.retrieval import RetrievedChunk


@pytest.fixture(autouse=True)
def _clear_intent_cache():
    reset_caches()
    yield
    reset_caches()


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("How do I install the widget?", INTENT_PROCEDURAL),
        ("steps to invite a teammate", INTENT_PROCEDURAL),
        ("the widget is not working", INTENT_TROUBLESHOOTING),
        ("why doesn't my email channel connect", INTENT_TROUBLESHOOTING),
        ("difference between plans", INTENT_COMPARATIVE),
        ("Pro vs Business", INTENT_COMPARATIVE),
        ("where is the billing page", INTENT_NAVIGATIONAL),
        ("what is a knowledge source", INTENT_FACTUAL),
        ("bananas", INTENT_FACTUAL),
    ],
)
def test_intent_classification(query, expected):
    assert classify_query(query).intent == expected


def test_a_broken_thing_wins_over_a_how_do_i(cf=None):
    """Mixed signals resolve to the more specific intent."""
    assert classify_query("how do I fix the broken import?").intent == INTENT_TROUBLESHOOTING


def test_procedural_and_navigational_ask_for_a_title_leg():
    assert classify_query("how do I add a teammate").prefers_title_match is True
    assert classify_query("where is billing").prefers_title_match is True
    assert classify_query("what is an inbox").prefers_title_match is False


def test_procedural_questions_are_guide_first():
    assert classify_query("how do I create an invoice").guide_first is True
    assert classify_query("what is an invoice").guide_first is False


def test_classification_is_cached_case_insensitively():
    first = classify_query("How Do I Install It?")
    assert classify_query("how do i install it?") is first


# --- rewriting --------------------------------------------------------------


def test_filler_is_stripped():
    assert rewrite_query("hey, um, basically how do I install the widget?").rewritten == (
        "how do I install the widget?"
    )


def test_a_query_of_only_filler_survives_as_itself():
    assert rewrite_query("thanks!").rewritten == "thanks!"


def test_abbreviations_are_expanded_alongside_the_original():
    result = rewrite_query("how do I set up SSO")
    expanded = next(alt for alt in result.alternatives if "single sign-on" in alt)
    assert "SSO" in expanded, "the short form must survive — docs may only use it"


def test_an_already_expanded_abbreviation_is_not_repeated():
    result = rewrite_query("how do I set up single sign-on (SSO)")
    assert not any(alt.lower().count("single sign-on") > 1 for alt in result.alternatives)


def test_a_question_gets_an_imperative_alternative():
    assert "install the widget" in rewrite_query("how do I install the widget").alternatives


def test_a_statement_gets_a_how_to_alternative():
    assert "how to reset a password" in rewrite_query("reset a password").alternatives


def test_a_verb_synonym_alternative_is_offered():
    result = rewrite_query("how do I create a team")
    assert any("set up a team" in alt for alt in result.alternatives)


def test_alternatives_never_repeat_the_query_and_are_capped():
    result = rewrite_query("how do I create and configure an API key for SSO")
    assert result.rewritten not in result.alternatives
    assert len(result.alternatives) <= 3
    assert len(set(result.alternatives)) == len(result.alternatives)


def test_a_pronoun_is_resolved_from_the_previous_turn():
    result = rewrite_query("how do I cancel it?", ["I just started a Pro subscription trial today"])
    assert "it" not in result.rewritten.split()
    assert "Pro subscription" in result.rewritten or "started Pro subscription" in result.rewritten
    assert result.resolved_references


def test_a_quoted_topic_wins_the_pronoun_resolution():
    result = rewrite_query("where is it?", ['I cannot find the "Team settings" page'])
    assert "Team settings" in result.rewritten


def test_a_pronoun_without_history_is_left_alone():
    result = rewrite_query("how do I cancel it?")
    assert result.rewritten == "how do I cancel it?"
    assert result.resolved_references == []


def test_an_empty_query_rewrites_to_nothing():
    result = rewrite_query("   ")
    assert (result.rewritten, result.alternatives) == ("", [])


# --- context assembly -------------------------------------------------------


def chunk(
    n: int, *, title: str = "Doc", content: str = "body", url: str | None = None
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{n}",
        document_id=f"d{n}",
        content=content,
        score=1.0 / n,
        title=title,
        url=url,
        ord=0,
    )


def test_build_context_numbers_blocks_and_citations_together():
    context = build_context(
        [chunk(1, title="Widgets", content="Install it."), chunk(2, title="Billing")],
        "install",
    )
    assert context.context_text.startswith("[1] Widgets\nInstall it.")
    assert "[2] Billing" in context.context_text
    assert [citation.n for citation in context.citations] == [1, 2]
    assert context.chunks_used == 2
    assert context.tokens_estimate == estimate_tokens(context.context_text)


def test_build_context_stops_at_the_budget():
    chunks = [chunk(n, title=f"Doc {n}", content="x" * 800) for n in range(1, 8)]
    context = build_context(chunks, "x", max_tokens=400)
    assert context.chunks_used < 7
    assert len(context.context_text) <= 400 * 4 + 200


def test_the_last_block_is_truncated_rather_than_dropped_when_useful():
    # 1600-char budget: block 1 takes 1008, leaving ~580 for a cut-down block 2.
    context = build_context(
        [chunk(1, content="a" * 1000), chunk(2, content="b" * 1000)], "a", max_tokens=400
    )
    assert context.truncated is True
    assert context.chunks_used == 2
    assert context.context_text.rstrip().endswith("[…]")


def test_a_block_too_small_to_be_useful_is_dropped_instead():
    # 1200-char budget leaves under 240 chars for block 2 — a fragment that size
    # teaches the model nothing and still costs a citation slot.
    context = build_context(
        [chunk(1, content="a" * 1000), chunk(2, content="b" * 1000)], "a", max_tokens=300
    )
    assert context.chunks_used == 1
    assert context.truncated is False
    assert "[2]" not in context.context_text


def test_build_context_handles_no_results():
    empty = build_context([], "anything")
    assert empty == BuiltContext(context_text="")
    assert build_context([chunk(1)], "q", max_tokens=0).context_text == ""


def test_empty_chunks_do_not_consume_a_citation_number():
    context = build_context([chunk(1, content="   "), chunk(2, content="real")], "real")
    assert [citation.n for citation in context.citations] == [1]
    assert context.context_text.startswith("[1] Doc\nreal")


def test_citations_carry_url_and_document_id_for_the_widget():
    context = build_context([chunk(1, title="Guide", url="/portal/guide")], "q")
    assert context.citations[0].to_dict() == {
        "n": 1,
        "title": "Guide",
        "url": "/portal/guide",
        "document_id": "d1",
        "content": "body",
    }


# --- compression ------------------------------------------------------------


def test_compression_drops_sentences_with_no_overlap():
    # Mostly on-topic with two asides: enough overlap that compression is applied
    # rather than abandoned by the >50%-removed guard.
    content = (
        "Refunds are issued within fourteen calendar days of an approved request. "
        "The team plays football on Fridays and everyone is welcome to join in. "
        "A refund is only available on annual plans that are still inside their term. "
        "Our office is in Berlin and the front desk is staffed from nine until five. "
        "Refund requests are handled by billing support rather than the sales team. "
        "A partial refund follows exactly the same refund timeline as a full one. "
        "Every refund is returned to the original payment method used at checkout. "
        "Approved refunds show up on a statement within two more working days. "
        "Closing note about the refund policy."
    )
    assert len(content) > 600, "compression only applies above the length gate"
    compressed = compress(content, "refund policy")
    assert "Refunds are issued within fourteen calendar days" in compressed
    assert "football" not in compressed
    assert "Berlin" not in compressed
    assert compressed.endswith("Closing note about the refund policy.")


def test_short_chunks_are_never_compressed():
    short = "Refunds take 14 days. We are in Berlin."
    assert compress(short, "refund") == short


def test_compression_gives_up_rather_than_gutting_a_chunk():
    content = " ".join(f"Sentence {n} about unrelated matters entirely." for n in range(40))
    assert compress(content, "refund policy") == content


def test_compression_keeps_the_first_and_last_sentence():
    body = "Opening line. " + "Middle noise sentence. " * 20 + "Closing line."
    compressed = compress(body, "opening closing")
    assert compressed.startswith("Opening line.")
    assert compressed.endswith("Closing line.")
