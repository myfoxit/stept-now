"""Query understanding: classify the intent, then rewrite for retrieval.

Ported from the old repo's `services/rag/{query_classifier,query_rewriter}.py`,
with one deliberate change: everything here is **rules-based and deterministic**.
The original asked an LLM first and fell back to regex; that cost a round-trip
before retrieval even started, on the one path where latency is most visible (a
visitor waiting in a chat widget), and made results irreproducible run to run.
The parts that actually moved relevance — intent → source boosts, filler
stripping, abbreviation expansion, pronoun resolution from history, and
alternative phrasings for multi-query expansion — need no model.

Two things come out of here and feed `app.rag.retrieval`:

- `QueryIntent`: what kind of question this is, which decides what to boost
  (a "how do I…" should surface a walkthrough; a "what is…" should surface prose);
- `RewrittenQuery`: the cleaned query plus 2-3 alternative phrasings, each run as
  its own retrieval leg so a doc that only matches the user's *other* wording is
  still found.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field

# Intents, in the order a tie is broken (most specific first).
INTENT_PROCEDURAL = "procedural"
INTENT_TROUBLESHOOTING = "troubleshooting"
INTENT_COMPARATIVE = "comparative"
INTENT_NAVIGATIONAL = "navigational"
INTENT_FACTUAL = "factual"

_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    (
        INTENT_TROUBLESHOOTING,
        re.compile(
            r"\b(error|not\s+working|broken|fix|bug|issue|problem|fail(ed|ing|s)?|"
            r"crash(ed|ing|es)?|doesn['’]?t\s+work|can['’]?t\s+(connect|open|access|load|find|log\s*in)|"
            r"troubleshoot|debug|wrong|stuck|why\s+(is|isn['’]?t|does|doesn['’]?t))\b",
            re.I,
        ),
        0.85,
    ),
    (
        INTENT_PROCEDURAL,
        re.compile(
            r"\b(how\s+(do|can|to|should|would)\s+i|how\s+do\s+we|steps?\s+to|"
            r"walk\s+me\s+through|show\s+me\s+how|guide\s+(me|to|for)|tutorial|"
            r"set\s*up|configure|install|create|add|invite|enable|disable|delete|"
            r"process\s+for|procedure|instructions)\b",
            re.I,
        ),
        0.80,
    ),
    (
        INTENT_COMPARATIVE,
        re.compile(
            r"\b(differ(ence|ent|s)?(\s+between)?|compar(e|ing|ison)|\bvs\.?\b|versus|"
            r"pros?\s+and\s+cons?|which\s+(one|is|should))\b",
            re.I,
        ),
        0.80,
    ),
    (
        INTENT_NAVIGATIONAL,
        re.compile(
            r"\b(where\s+(is|are|can|do)|find|show\s+me|locate|look\s+up|"
            r"search\s+for|go\s+to|take\s+me\s+to|open)\b",
            re.I,
        ),
        0.75,
    ),
    (
        INTENT_FACTUAL,
        re.compile(
            r"\b(what\s+(is|are|does|do|was)|explain|define|describe|meaning\s+of|"
            r"tell\s+me\s+about|overview|summary|who\s+(is|are|was)|when\s+(is|are|does))\b",
            re.I,
        ),
        0.70,
    ),
]

#: Intents where a document TITLE match is worth its own retrieval leg. Someone
#: asking "where is the billing page" usually names the thing they want.
TITLE_LEG_INTENTS = frozenset({INTENT_NAVIGATIONAL, INTENT_PROCEDURAL})

#: Intents where an in-app walkthrough beats prose, so the assistant should try
#: `find_guide` before answering. Consumed by the agent prompt, not by retrieval.
GUIDE_FIRST_INTENTS = frozenset({INTENT_PROCEDURAL, INTENT_NAVIGATIONAL})


@dataclass
class QueryIntent:
    intent: str
    confidence: float

    @property
    def prefers_title_match(self) -> bool:
        return self.intent in TITLE_LEG_INTENTS

    @property
    def guide_first(self) -> bool:
        return self.intent in GUIDE_FIRST_INTENTS


@dataclass
class RewrittenQuery:
    original: str
    rewritten: str
    """Cleaned query used for the primary retrieval legs."""
    alternatives: list[str] = field(default_factory=list)
    """Extra phrasings, each retrieved separately and fused (multi-query expansion)."""
    resolved_references: list[str] = field(default_factory=list)
    """`it → deployment pipeline` style notes, for debugging a surprising result."""


# --- classification ---------------------------------------------------------


def classify_query(query: str) -> QueryIntent:
    """Label the question by intent; unrecognised shapes read as factual.

    Cached on the normalised query: the same phrasing gets asked over and over in
    a support inbox, and the whole point of dropping the LLM here was to make this
    step free.
    """
    key = _normalise(query)
    cached = _intent_cache_get(key)
    if cached is not None:
        return cached
    best = QueryIntent(intent=INTENT_FACTUAL, confidence=0.3)
    for intent, pattern, confidence in _PATTERNS:
        if pattern.search(query) and confidence > best.confidence:
            best = QueryIntent(intent=intent, confidence=confidence)
    _intent_cache_put(key, best)
    return best


# --- rewriting --------------------------------------------------------------

_FILLER_WORDS = (
    "um uh basically actually just really very quite simply literally honestly frankly "
    "anyway anyways please thanks hey hi hello okay alright"
).split()
_FILLER_PHRASES = ["thank you", "i mean", "you know", "kind of", "sort of", "quick question"]
_FILLER_RE = re.compile(
    r"\b("
    + "|".join(re.escape(w) for w in sorted(_FILLER_PHRASES + _FILLER_WORDS, key=len, reverse=True))
    + r")\b",
    re.I,
)

#: Expanded ALONGSIDE the abbreviation, never instead of it: a doc may spell it
#: either way, and dropping the short form loses an exact lexical match.
_ABBREVIATIONS: dict[str, str] = {
    "2fa": "two-factor authentication",
    "api": "application programming interface",
    "cli": "command-line interface",
    "config": "configuration",
    "csat": "customer satisfaction",
    "db": "database",
    "env": "environment",
    "faq": "frequently asked questions",
    "jwt": "json web token",
    "kb": "knowledge base",
    "mfa": "multi-factor authentication",
    "rbac": "role-based access control",
    "repo": "repository",
    "sla": "service level agreement",
    "sso": "single sign-on",
    "ui": "user interface",
    "ux": "user experience",
}

_PRONOUN_RE = re.compile(r"\b(it|this|that|these|those|they|them|its|their|the same)\b", re.I)

_VERB_SYNONYMS: dict[str, str] = {
    "create": "set up",
    "set up": "configure",
    "configure": "set up",
    "install": "set up",
    "delete": "remove",
    "remove": "delete",
    "update": "change",
    "change": "update",
    "fix": "resolve",
    "cancel": "stop",
    "invite": "add",
}

_QUESTION_PREFIXES = (
    "how do i ",
    "how can i ",
    "how do we ",
    "how to ",
    "where can i ",
    "where is ",
)


def rewrite_query(query: str, history: list[str] | None = None) -> RewrittenQuery:
    """Clean the query for retrieval and produce alternative phrasings.

    `history` is the recent conversation turns, newest last. It exists for one
    job: "how do I cancel it?" is unanswerable on its own, and the antecedent is
    almost always in the previous turn.
    """
    original = query.strip()
    if not original:
        return RewrittenQuery(original="", rewritten="", alternatives=[])

    resolved: list[str] = []
    working = _FILLER_RE.sub(" ", original)
    working = re.sub(r"\s{2,}", " ", working).strip(" ,;:")
    # "thanks!" is entirely filler; stripping it leaves punctuation, which would
    # embed as noise and retrieve nothing. A query with no content word left is
    # not a better query — keep what the person actually typed.
    if not re.search(r"\w", working):
        working = original

    if history and _PRONOUN_RE.search(working):
        topic = _last_topic(history)
        if topic:
            match = _PRONOUN_RE.search(working)
            if match:
                resolved.append(f"{match.group(0)} → {topic}")
                working = working[: match.start()] + topic + working[match.end() :]
                working = re.sub(r"\s{2,}", " ", working).strip()

    expanded = _expand_abbreviations(working)
    alternatives = _alternatives(working)
    if expanded != working:
        alternatives.insert(0, expanded)

    return RewrittenQuery(
        original=original,
        rewritten=working or original,
        alternatives=_dedupe(alternatives, exclude=working)[:3],
        resolved_references=resolved,
    )


def _expand_abbreviations(query: str) -> str:
    """Append expansions for any abbreviations present, keeping the original."""
    extras: list[str] = []
    lowered = query.lower()
    for abbreviation, expansion in _ABBREVIATIONS.items():
        if re.search(rf"\b{re.escape(abbreviation)}\b", lowered) and expansion not in lowered:
            extras.append(expansion)
    return f"{query} {' '.join(extras)}".strip() if extras else query


def _alternatives(query: str) -> list[str]:
    """Rule-based paraphrases, each cheap and each a genuinely different angle."""
    out: list[str] = []
    lowered = query.lower()

    # Question ⇄ imperative: docs are written as "Set up SSO", questions are not.
    stripped = next(
        (
            query[len(prefix) :].strip()
            for prefix in _QUESTION_PREFIXES
            if lowered.startswith(prefix)
        ),
        None,
    )
    if stripped:
        out.append(stripped)
    elif not lowered.startswith(("what", "why", "who", "when")):
        out.append(f"how to {query}")

    # Verb synonym: "create a team" vs the doc's "set up a team".
    for verb, synonym in _VERB_SYNONYMS.items():
        if re.search(rf"\b{re.escape(verb)}\b", lowered):
            swapped = re.sub(rf"\b{re.escape(verb)}\b", synonym, query, count=1, flags=re.I)
            if swapped.lower() != lowered:
                out.append(swapped)
            break

    return out


def _last_topic(history: list[str]) -> str | None:
    """Best guess at what a pronoun refers to: the last turn's key phrase."""
    for entry in reversed(history):
        text = (entry or "").strip()
        if not text:
            continue
        quoted = re.findall(r'"([^"]+)"', text)
        if quoted:
            return quoted[-1]
        words = [
            word
            for word in re.sub(r"[^\w\s]", " ", text).split()
            if len(word) > 2 and word.lower() not in _FILLER_WORDS
        ]
        if words:
            return " ".join(words[:4])
    return None


def _dedupe(values: list[str], *, exclude: str) -> list[str]:
    seen = {exclude.lower().strip()}
    out: list[str] = []
    for value in values:
        key = value.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(value.strip())
    return out


def _normalise(query: str) -> str:
    return " ".join(query.lower().split())


# --- intent cache -----------------------------------------------------------
#
# A plain LRU over an OrderedDict. `functools.lru_cache` would work too, but the
# old repo's version paired it with a `cache_clear()` after every write, which
# defeated the cache after one entry — worth not repeating.

_CACHE_SIZE = 256
_intent_cache: OrderedDict[str, QueryIntent] = OrderedDict()


def _intent_cache_get(key: str) -> QueryIntent | None:
    hit = _intent_cache.get(key)
    if hit is not None:
        _intent_cache.move_to_end(key)
    return hit


def _intent_cache_put(key: str, intent: QueryIntent) -> None:
    _intent_cache[key] = intent
    _intent_cache.move_to_end(key)
    while len(_intent_cache) > _CACHE_SIZE:
        _intent_cache.popitem(last=False)


def reset_caches() -> None:
    """Test hook — clear the intent cache between runs."""
    _intent_cache.clear()
