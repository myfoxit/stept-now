"""Chunker properties: determinism, sizing, title prefix, ord sequence, merging."""

from __future__ import annotations

from app.rag.chunker import chunk_text, estimate_tokens


def make_paragraphs(count: int, sentence: str) -> str:
    return "\n\n".join(f"{sentence} Paragraph number {i}." for i in range(count))


LONG_TEXT = (
    "# Overview\n\n"
    + make_paragraphs(30, "Stept routes conversations to the right team automatically.")
    + "\n\n## Details\n\n"
    + make_paragraphs(30, "The knowledge base powers AI answers with citations.")
)


def test_deterministic():
    first = chunk_text(LONG_TEXT, title="Guide")
    second = chunk_text(LONG_TEXT, title="Guide")
    assert [c.content for c in first] == [c.content for c in second]
    assert [c.headings for c in first] == [c.headings for c in second]


def test_chunk_sizes_respect_target():
    chunks = chunk_text(LONG_TEXT, title="Guide", target_tokens=128, min_tokens=50)
    assert len(chunks) > 1
    # all chunks stay near target (small slack for the title prefix + joins);
    # only the last may additionally absorb a merged tiny tail (< min_tokens).
    for chunk in chunks[:-1]:
        assert chunk.token_count <= 128 + 16
    assert chunks[-1].token_count <= 128 + 50 + 16
    for chunk in chunks:
        assert chunk.token_count == estimate_tokens(chunk.content)


def test_title_prefix_on_every_chunk():
    chunks = chunk_text(LONG_TEXT, title="My Guide", target_tokens=128)
    assert len(chunks) > 2
    for chunk in chunks:
        assert chunk.content.startswith("# My Guide\n\n")
    # the doc's own leading "# Overview" heading is kept, but a duplicate of
    # the title would have been dropped
    assert chunks[0].content.count("# My Guide") == 1


def test_leading_heading_matching_title_not_duplicated():
    text = "# Same Title\n\nBody paragraph one."
    chunks = chunk_text(text, title="Same Title")
    assert len(chunks) == 1
    assert chunks[0].content.count("# Same Title") == 1


def test_ord_sequence_is_contiguous():
    chunks = chunk_text(LONG_TEXT, title="Guide", target_tokens=96)
    assert [chunk.ord for chunk in chunks] == list(range(len(chunks)))


def test_trailing_tiny_chunk_merged():
    # Body fills one chunk exactly; the tiny tail would overflow into its own
    # 2-token chunk — the chunker must merge it back into the previous one.
    body = "word " * 409  # strips to 2044 chars = 511 tokens = the exact budget
    text = body.strip() + "\n\nTiny tail."
    chunks = chunk_text(text, title="T", target_tokens=512, min_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].content.endswith("Tiny tail.")


def test_trailing_chunk_above_min_not_merged():
    body = "word " * 409
    text = body.strip() + "\n\n" + ("tail sentence here. " * 15).strip()
    chunks = chunk_text(text, title="T", target_tokens=512, min_tokens=50)
    assert len(chunks) == 2
    assert chunks[1].token_count >= 50


def test_short_text_single_chunk():
    chunks = chunk_text("Just one small paragraph.", title="Small")
    assert len(chunks) == 1
    assert chunks[0].ord == 0
    assert chunks[0].content == "# Small\n\nJust one small paragraph."


def test_empty_and_whitespace_input():
    assert chunk_text("", title="X") == []
    assert chunk_text("   \n\n  ", title="X") == []


def test_headings_tracked_per_chunk():
    text = (
        "# Setup\n\n"
        + make_paragraphs(20, "Install the widget snippet on your site today.")
        + "\n\n## Identity\n\n"
        + make_paragraphs(20, "Verify users with an HMAC signature for security.")
    )
    chunks = chunk_text(text, title="Install", target_tokens=128)
    assert chunks[0].headings == ["Setup"]
    assert chunks[-1].headings == ["Setup", "Identity"]


def test_oversized_unbroken_text_hard_splits():
    blob = "x" * 5000  # no sentences, no paragraphs
    chunks = chunk_text(blob, title="Blob", target_tokens=100)
    assert len(chunks) > 1
    assert all(chunk.token_count <= 132 for chunk in chunks)
    assert "".join(c.content.removeprefix("# Blob\n\n").replace("\n\n", "") for c in chunks) == blob
