"""Turn retrieved chunks into a prompt-ready context block with citations.

Ported from the old repo's `services/rag/context_builder.py`. It replaces the
blunt "first 500 characters of each of k chunks" that both the agent tool and the
reply copilot used to do, which has two failure modes: the answer sits at
character 700 of the best chunk and gets cut, while three weak chunks each spend
their full 500 on boilerplate.

Three jobs:

- **budget**: fill a token allowance in relevance order, truncating only the last
  chunk that fits, so a strong chunk gets room rather than an equal slice;
- **compress**: when a chunk is long, drop sentences that share no content word
  with the question — but never so aggressively that the chunk stops reading as
  prose (if more than half would go, keep it whole);
- **cite**: number the blocks `[1] Title` so the model's `[n]` markers line up
  with the citation list the widget renders under the reply.

Token counts are the standard ~4-chars-per-token estimate. A real tokenizer would
be more accurate and would tie this module to one provider's vocabulary; the
budget exists to avoid blowing a context window, not to bill anyone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rag.retrieval import RetrievedChunk

CHARS_PER_TOKEN = 4
DEFAULT_MAX_TOKENS = 1500
#: Below this, a truncated block is not worth including — a 40-character fragment
#: teaches the model nothing and still costs a citation slot.
MIN_USEFUL_CHARS = 240
#: Chunks shorter than this are passed through whole; compression only pays off
#: on long prose.
COMPRESS_ABOVE_CHARS = 600

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be but by can did do does for from had has have how i if in into is it "
    "its me my of on or our so that the their them then there these they this to us was we were "
    "what when where which who why will with you your".split()
)


@dataclass
class Citation:
    n: int
    title: str
    url: str | None = None
    document_id: str | None = None
    content: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "n": self.n,
            "title": self.title,
            "url": self.url,
            "document_id": self.document_id,
            "content": self.content,
        }


@dataclass
class BuiltContext:
    context_text: str
    """Numbered source blocks, ready to drop into a prompt."""
    citations: list[Citation] = field(default_factory=list)
    chunks_used: int = 0
    tokens_estimate: int = 0
    truncated: bool = False
    """True when the last included block had to be cut to fit the budget."""

    def citation_dicts(self) -> list[dict[str, object]]:
        return [citation.to_dict() for citation in self.citations]


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def build_context(
    chunks: list[RetrievedChunk],
    query: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> BuiltContext:
    """Assemble the highest-relevance chunks that fit the token budget."""
    if not chunks or max_tokens <= 0:
        return BuiltContext(context_text="")

    max_chars = max_tokens * CHARS_PER_TOKEN
    blocks: list[str] = []
    citations: list[Citation] = []
    used = 0
    truncated = False

    for chunk in chunks:
        content = compress(chunk.content, query)
        if not content.strip():
            continue
        n = len(citations) + 1
        block = _format_block(n, chunk.title, content)
        separator = 2 if blocks else 0  # the "\n\n" join between blocks

        if used + len(block) + separator > max_chars:
            remaining = max_chars - used - separator - len(_format_block(n, chunk.title, ""))
            if remaining < MIN_USEFUL_CHARS:
                break
            block = _format_block(n, chunk.title, content[:remaining] + " […]")
            truncated = True
            blocks.append(block)
            citations.append(_citation(n, chunk, content))
            used += len(block) + separator
            break

        blocks.append(block)
        citations.append(_citation(n, chunk, content))
        used += len(block) + separator

    context_text = "\n\n".join(blocks)
    return BuiltContext(
        context_text=context_text,
        citations=citations,
        chunks_used=len(blocks),
        tokens_estimate=estimate_tokens(context_text),
        truncated=truncated,
    )


def compress(content: str, query: str) -> str:
    """Drop sentences that share no content word with the query.

    The first and last sentences always stay: the first usually carries the topic
    and the last the conclusion, and a middle-only excerpt reads as though the
    source is incoherent. If compression would remove more than half the
    sentences, the chunk is kept whole — that much overlap-free text usually means
    the query terms are phrased differently, not that the chunk is irrelevant.
    """
    if len(content) <= COMPRESS_ABOVE_CHARS:
        return content
    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(content) if part.strip()]
    if len(sentences) <= 3:
        return content
    terms = _keywords(query)
    if not terms:
        return content

    kept = [
        sentence
        for index, sentence in enumerate(sentences)
        if index == 0 or index == len(sentences) - 1 or terms & _keywords(sentence)
    ]
    if len(kept) < len(sentences) * 0.5:
        return content
    compressed = " ".join(kept)
    return compressed if len(compressed) < len(content) else content


def _format_block(n: int, title: str, content: str) -> str:
    return f"[{n}] {title}\n{content}".rstrip()


def _citation(n: int, chunk: RetrievedChunk, content: str) -> Citation:
    return Citation(
        n=n,
        title=chunk.title,
        url=chunk.url,
        document_id=chunk.document_id,
        content=content[:500],
    )


def _keywords(text: str) -> set[str]:
    return {word for word in _WORD_RE.findall(text.lower()) if word not in _STOPWORDS}
