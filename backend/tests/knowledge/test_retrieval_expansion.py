"""Retrieval with the expansion legs in place.

Two kinds of test here, deliberately separated:

- **mechanism** tests exercise the new pieces directly (`_title_leg`,
  `_bm25_scores`, the extra dense legs), because that is the only way to know
  they do what they claim;
- **relevance** tests assert end-to-end outcomes for queries whose wording does
  not match the winning document. Several of these also pass with the legs
  disabled — the hash embedder is already decent at them — so they are
  regressions guarding the answers, not evidence that any one leg fired.
"""

from __future__ import annotations

from app.core.db import session_scope
from app.rag.retrieval import (
    BM25_WEIGHT,
    EXPANSION_DEPTH,
    TITLE_MATCH_BOOST,
    _bm25_scores,
    _title_leg,
    search_chunks,
)
from tests.knowledge.expansion_fixtures import add_doc, add_source, seed_corpus, wait_indexed


async def top_titles(workspace_id: str, query: str, *, k: int = 3, **kwargs) -> list[str]:
    async with session_scope() as session:
        results = await search_chunks(session, workspace_id, query, k=k, **kwargs)
        return [result.title for result in results]


# --- mechanism --------------------------------------------------------------


async def test_title_leg_returns_chunks_of_title_matching_documents_only(workspace_ctx):
    await seed_corpus(
        workspace_ctx.id,
        [
            ("Billing page", "Invoices and payment methods live here."),
            ("Release notes", "Nothing about money at all in this document."),
        ],
    )
    async with session_scope() as session:
        matched = await _title_leg(session, workspace_ctx.id, "where is the billing page", None)
        chunk_titles = [
            result.title
            for result in await search_chunks(session, workspace_ctx.id, "billing", k=5)
        ]
    assert matched, "a document titled 'Billing page' must match a billing question"
    assert len(matched) <= EXPANSION_DEPTH
    assert "Billing page" in chunk_titles


async def test_title_leg_matches_whole_words_not_substrings(workspace_ctx):
    """ "add" must not match "Additional settings" — near-misses erode trust fast."""
    await seed_corpus(
        workspace_ctx.id, [("Additional settings", "Assorted preferences and toggles.")]
    )
    async with session_scope() as session:
        assert await _title_leg(session, workspace_ctx.id, "how do I add a teammate", None) == set()


async def test_title_leg_respects_a_source_filter(workspace_ctx):
    await seed_corpus(workspace_ctx.id, [("Billing page", "Invoices live here.")])
    other = await add_source(workspace_ctx.id, name="Other")
    async with session_scope() as session:
        assert await _title_leg(session, workspace_ctx.id, "billing page", [other]) == set()


def test_bm25_normalises_to_the_best_candidate_and_penalises_length():
    class FakeChunk:
        def __init__(self, chunk_id: str, content: str) -> None:
            self.id = chunk_id
            self.content = content

    short = FakeChunk("short", "Data export runs from settings. Confirm the export.")
    long = FakeChunk(
        "long",
        "Background material at length. " * 40 + "Data export is mentioned here once.",
    )
    scores = _bm25_scores("data export", [short, long])  # type: ignore[arg-type]
    assert scores["short"] == 1.0, "the best candidate is normalised to 1.0"
    assert scores["long"] < scores["short"]
    assert 0.0 < BM25_WEIGHT < 1.0 and TITLE_MATCH_BOOST > 1.0


def test_bm25_is_empty_when_nothing_matches():
    class FakeChunk:
        id = "a"
        content = "completely unrelated prose"

    assert _bm25_scores("data export", [FakeChunk()]) == {}  # type: ignore[list-item]
    assert _bm25_scores("", []) == {}


# --- relevance --------------------------------------------------------------


async def test_a_create_question_reaches_a_doc_that_says_set_up(workspace_ctx):
    """ "how do I create a team" must reach a doc that only says "set up a team"."""
    await seed_corpus(
        workspace_ctx.id,
        [
            (
                "Workspace glossary",
                "A team is a named group of members. Teams appear on the members page. "
                "Every workspace starts with no teams at all.",
            ),
            (
                "Set up a team",
                "To set up a team, open Settings, choose Teams, then use the New team "
                "button. Give the team a name and add members to it.",
            ),
        ],
    )
    titles = await top_titles(workspace_ctx.id, "how do I create a team")
    assert titles[0] == "Set up a team"


async def test_a_where_is_question_prefers_the_document_named_after_it(workspace_ctx):
    await seed_corpus(
        workspace_ctx.id,
        [
            (
                "Release notes",
                "We moved several pages around this quarter. The billing screen was "
                "redesigned and now loads faster than before on slow connections.",
            ),
            (
                "Billing page",
                "Everything about invoices, payment methods and your plan lives here.",
            ),
        ],
    )
    titles = await top_titles(workspace_ctx.id, "where is the billing page")
    assert titles[0] == "Billing page"


async def test_bm25_prefers_the_short_exact_answer_over_a_long_aside(workspace_ctx):
    filler = (
        "This section covers unrelated background material at length. "
        "It mentions the export process only in passing, among many other topics, "
        "and continues for several paragraphs about migrations, retention windows, "
        "audit trails, archived workspaces and historical pricing experiments. "
    ) * 6
    await seed_corpus(
        workspace_ctx.id,
        [
            ("Long background note", filler + "Data export is mentioned here once."),
            (
                "Export your data",
                "Data export runs from Settings. Choose Data export, pick a format, "
                "then confirm the export. The export finishes by email.",
            ),
        ],
    )
    titles = await top_titles(workspace_ctx.id, "data export")
    assert titles[0] == "Export your data"


async def test_conversation_history_resolves_a_pronoun_into_a_real_query(workspace_ctx):
    await seed_corpus(
        workspace_ctx.id,
        [
            (
                "Cancelling a Pro subscription",
                "A Pro subscription can be cancelled from the billing page at any time. "
                "Cancellation takes effect at the end of the current period.",
            ),
            (
                "Deleting a workspace",
                "Deleting a workspace removes every conversation, contact and article. "
                "This cannot be undone and is unrelated to subscriptions.",
            ),
        ],
    )
    bare = await top_titles(workspace_ctx.id, "how do I cancel it?")
    with_history = await top_titles(
        workspace_ctx.id,
        "how do I cancel it?",
        history=["I signed up for a Pro subscription yesterday"],
    )
    assert with_history[0] == "Cancelling a Pro subscription"
    assert bare, "the bare query still returns something — it is just less certain"


async def test_expand_query_false_keeps_the_search_literal(workspace_ctx):
    """The admin playground searches exactly what was typed."""
    await seed_corpus(
        workspace_ctx.id,
        [("Set up a team", "To set up a team, open Settings and choose Teams.")],
    )
    async with session_scope() as session:
        literal = await search_chunks(
            session, workspace_ctx.id, "how do I create a team", k=3, expand_query=False
        )
        expanded = await search_chunks(
            session, workspace_ctx.id, "how do I create a team", k=3, expand_query=True
        )
    # Both find the one document; the expanded run scores it higher because more
    # legs voted for it.
    assert literal and expanded
    assert expanded[0].score > literal[0].score


async def test_source_boost_still_wins_a_tie_after_the_new_legs(workspace_ctx):
    """The boost hook must keep working now that more signals multiply together."""
    content = "Zebra kangaroo platypus wombat. The migration corridor opens in spring."
    plain = await add_source(workspace_ctx.id, name="Plain")
    boosted = await add_source(workspace_ctx.id, name="Boosted", config={"boost": 2.0})
    await add_doc(workspace_ctx.id, plain, title="Animals A", content=content)
    await add_doc(workspace_ctx.id, boosted, title="Animals B", content=content)
    await wait_indexed(workspace_ctx.id, 2)

    titles = await top_titles(workspace_ctx.id, "kangaroo migration corridor", k=2)
    assert titles[0] == "Animals B"


async def test_an_empty_workspace_still_returns_nothing(workspace_ctx):
    assert await top_titles(workspace_ctx.id, "how do I create a team") == []
