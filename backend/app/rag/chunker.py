"""Deterministic chunker (Onyx recipe, see docs/research/onyx.md).

512-token chunks, zero overlap (neighbor expansion happens at query time
instead), title prefixed onto every chunk, trailing tiny chunks merged.
Tokens are estimated as len(text) // 4 — no tokenizer dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

DEFAULT_TARGET_TOKENS = 512
DEFAULT_MIN_TOKENS = 50


def estimate_tokens(text: str) -> int:
    """Cheap token estimate used across the pipeline (~4 chars/token)."""
    return len(text) // 4


@dataclass
class ChunkDraft:
    content: str
    ord: int
    token_count: int
    headings: list[str] = field(default_factory=list)


@dataclass
class _Block:
    text: str
    headings: list[str]


def _split_blocks(text: str, title: str) -> list[_Block]:
    """Split on headings and blank-line paragraphs, tracking the heading trail."""
    trail: dict[int, str] = {}
    blocks: list[_Block] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(_Block("\n".join(paragraph).strip(), _trail_list(trail)))
            paragraph.clear()

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _HEADING_RE.match(line.strip())
        if match:
            flush_paragraph()
            level, heading = len(match.group(1)), match.group(2).strip()
            for deeper in [lvl for lvl in trail if lvl >= level]:
                del trail[deeper]
            trail[level] = heading
            blocks.append(_Block(f"{'#' * level} {heading}", _trail_list(trail)))
        elif line.strip():
            paragraph.append(line.rstrip())
        else:
            flush_paragraph()
    flush_paragraph()

    # Drop a leading heading identical to the title — it is re-added as the
    # uniform chunk prefix, and doubling it would waste budget on chunk 0.
    if blocks:
        first = _HEADING_RE.match(blocks[0].text)
        if first and first.group(2).strip() == title.strip():
            blocks = blocks[1:]
    return [block for block in blocks if block.text]


def _trail_list(trail: dict[int, str]) -> list[str]:
    return [trail[level] for level in sorted(trail)]


def _split_oversized(block: _Block, budget_tokens: int) -> list[_Block]:
    """Break a block bigger than the budget on sentences, hard-wrapping any
    sentence that is itself over budget."""
    if estimate_tokens(block.text) <= budget_tokens:
        return [block]
    budget_chars = max(budget_tokens * 4, 8)
    pieces: list[_Block] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current_len
        if current:
            pieces.append(_Block(" ".join(current), block.headings))
            current.clear()
            current_len = 0

    for sentence in _SENTENCE_SPLIT_RE.split(block.text):
        if not sentence:
            continue
        while len(sentence) > budget_chars:  # pathological unbroken text
            flush()
            pieces.append(_Block(sentence[:budget_chars], block.headings))
            sentence = sentence[budget_chars:]
        if current and current_len + 1 + len(sentence) > budget_chars:
            flush()
        current.append(sentence)
        current_len += len(sentence) + 1
    flush()
    return [piece for piece in pieces if piece.text.strip()]


def chunk_text(
    text: str,
    *,
    title: str,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
) -> list[ChunkDraft]:
    """Deterministically split `text` into title-prefixed chunks of roughly
    `target_tokens`; a trailing chunk under `min_tokens` merges into its
    predecessor. Returns [] for empty/whitespace-only input."""
    if not text or not text.strip():
        return []
    title = title.strip()
    prefix = f"# {title}\n\n" if title else ""
    budget = max(target_tokens - estimate_tokens(prefix), min_tokens)

    units: list[_Block] = []
    for block in _split_blocks(text, title):
        units.extend(_split_oversized(block, budget))
    if not units:
        return []

    groups: list[list[_Block]] = []
    current: list[_Block] = []
    current_tokens = 0
    for unit in units:
        unit_tokens = estimate_tokens(unit.text)
        if current and current_tokens + unit_tokens > budget:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        groups.append(current)

    # Merge a trailing tiny chunk into the previous one (overlap-free packing
    # otherwise strands the last few sentences in a low-signal fragment).
    if len(groups) > 1:
        last_tokens = sum(estimate_tokens(unit.text) for unit in groups[-1])
        if last_tokens < min_tokens:
            groups[-2].extend(groups.pop())

    drafts: list[ChunkDraft] = []
    for index, group in enumerate(groups):
        content = prefix + "\n\n".join(unit.text for unit in group)
        drafts.append(
            ChunkDraft(
                content=content,
                ord=index,
                token_count=estimate_tokens(content),
                headings=list(group[0].headings),
            )
        )
    return drafts
