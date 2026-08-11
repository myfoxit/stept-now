"""Guide lookup for the in-app assistant.

"How do I create an invoice?" has three good answers, in this order:

1. **Show me** — walk the visitor through the real UI (a published tour).
2. **Tell me** — answer from the knowledge base with citations.
3. **Do it** — drive the page (see `app.agents.page_tools`).

This module answers (1): given a natural-language question and where the visitor
currently is, which published tour or checklist actually teaches that? Tours are
short and few (tens per workspace, not thousands of chunks), so a deterministic
lexical scorer beats embeddings here — it needs no index to stay fresh when an
author renames a tour, and it is reproducible in tests.

Scoring, strongest signal first:
- a term hit in the tour NAME (authors name tours after the task);
- then description, then step titles, then step bodies;
- a tour whose URL trigger matches the visitor's current page is boosted — the
  same question asked on /billing and /settings should not get the same guide;
- ties break on priority, then name, so the output is stable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist import Checklist
from app.models.tour import Tour

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be but by can could did do does for from get got had has have how i if in "
    "into is it its me my need of on or our should so that the their them then there these they "
    "this to us want was we were what when where which who why will with would you your".split()
)

# Field weights — a name hit is the strongest signal an author can give us.
_WEIGHT_NAME = 6.0
_WEIGHT_DESCRIPTION = 3.0
_WEIGHT_STEP_TITLE = 2.0
_WEIGHT_STEP_BODY = 1.0
_URL_MATCH_BOOST = 4.0
_MIN_SCORE = 1.0
_BODY_SCAN_CHARS = 400


@dataclass
class GuideMatch:
    """A guide the assistant can offer to play, ranked against the question."""

    kind: str  # "tour" | "checklist"
    id: str
    name: str
    description: str
    step_count: int
    score: float
    """Lexical relevance; only used for ordering, never shown to the visitor."""
    url_pattern: str | None = None
    """Where this guide is meant to run (`None` = anywhere / manual trigger)."""
    step_titles: list[str] = field(default_factory=list)
    """First few step titles — enough for the model to describe the guide."""

    def to_tool_payload(self) -> dict[str, Any]:
        """The shape the model sees. Deliberately small: ids plus a description."""
        payload: dict[str, Any] = {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "steps": self.step_count,
        }
        if self.description:
            payload["description"] = self.description[:300]
        if self.step_titles:
            payload["step_titles"] = self.step_titles
        if self.url_pattern:
            payload["runs_on"] = self.url_pattern
        return payload


def keywords(text: str) -> set[str]:
    """Content words of a phrase, lowercased, stopwords dropped."""
    return {word for word in _WORD_RE.findall(text.lower()) if word not in _STOPWORDS}


def _field_score(terms: set[str], text: str, weight: float) -> float:
    """Weighted count of query terms present in `text` (presence, not frequency).

    Frequency would let a long step body outrank an exact name match just by
    repeating a word, which is the opposite of what an author intends.
    """
    if not text:
        return 0.0
    haystack = keywords(text)
    return weight * len(terms & haystack)


def score_tour(tour: Tour, terms: set[str], url: str | None) -> float:
    """Relevance of one tour to the question terms and the visitor's page."""
    if not terms:
        return 0.0
    steps = tour.steps if isinstance(tour.steps, list) else []
    total = _field_score(terms, tour.name or "", _WEIGHT_NAME)
    total += _field_score(terms, tour.description or "", _WEIGHT_DESCRIPTION)
    for step in steps:
        if not isinstance(step, dict):
            continue
        total += _field_score(terms, str(step.get("title") or ""), _WEIGHT_STEP_TITLE)
        total += _field_score(
            terms, str(step.get("body") or "")[:_BODY_SCAN_CHARS], _WEIGHT_STEP_BODY
        )
    if total > 0 and url and _trigger_matches(tour.trigger, url):
        total += _URL_MATCH_BOOST
    return total


def _trigger_matches(trigger: dict[str, Any] | None, url: str) -> bool:
    """Does a `url_match` trigger's fnmatch pattern cover this URL?

    Case-insensitive, mirroring the widget's own matcher — a host page that
    capitalises a path segment must not silently miss its guide.
    """
    if not isinstance(trigger, dict) or trigger.get("type") != "url_match":
        return False
    pattern = trigger.get("url_pattern")
    if not isinstance(pattern, str) or not pattern:
        return False
    return fnmatch(url.lower(), pattern.lower())


def _url_pattern_of(trigger: dict[str, Any] | None) -> str | None:
    if not isinstance(trigger, dict) or trigger.get("type") != "url_match":
        return None
    pattern = trigger.get("url_pattern")
    return pattern if isinstance(pattern, str) and pattern else None


async def search_guides(
    session: AsyncSession,
    workspace_id: str,
    query: str,
    *,
    url: str | None = None,
    limit: int = 4,
) -> list[GuideMatch]:
    """Rank published tours (and checklists) against a natural-language question.

    Only `live` experiences are considered — a draft is not something to walk a
    real visitor through. Returns at most `limit` matches above a small score
    floor, so a question with no matching guide returns nothing rather than the
    workspace's alphabetically-first tour.
    """
    terms = keywords(query)
    if not terms or limit <= 0:
        return []

    tours = (
        (
            await session.execute(
                select(Tour).where(Tour.workspace_id == workspace_id, Tour.status == "live")
            )
        )
        .scalars()
        .all()
    )
    matches: list[GuideMatch] = []
    for tour in tours:
        score = score_tour(tour, terms, url)
        if score < _MIN_SCORE:
            continue
        steps = tour.steps if isinstance(tour.steps, list) else []
        matches.append(
            GuideMatch(
                kind="tour",
                id=tour.id,
                name=tour.name,
                description=tour.description or "",
                step_count=len(steps),
                score=score,
                url_pattern=_url_pattern_of(tour.trigger),
                step_titles=[
                    str(step.get("title") or "")
                    for step in steps[:5]
                    if isinstance(step, dict) and step.get("title")
                ],
            )
        )

    checklists = (
        (
            await session.execute(
                select(Checklist).where(
                    Checklist.workspace_id == workspace_id, Checklist.status == "live"
                )
            )
        )
        .scalars()
        .all()
    )
    for checklist in checklists:
        items = checklist.items if isinstance(checklist.items, list) else []
        score = _field_score(terms, checklist.name or "", _WEIGHT_NAME)
        score += _field_score(terms, checklist.description or "", _WEIGHT_DESCRIPTION)
        for item in items:
            if isinstance(item, dict):
                score += _field_score(terms, str(item.get("title") or ""), _WEIGHT_STEP_TITLE)
        if score < _MIN_SCORE:
            continue
        matches.append(
            GuideMatch(
                kind="checklist",
                id=checklist.id,
                name=checklist.name,
                description=checklist.description or "",
                step_count=len(items),
                score=score,
                step_titles=[
                    str(item.get("title") or "")
                    for item in items[:5]
                    if isinstance(item, dict) and item.get("title")
                ],
            )
        )

    matches.sort(key=lambda match: (-match.score, match.name.lower(), match.id))
    return matches[:limit]


async def live_tour(session: AsyncSession, workspace_id: str, tour_id: str) -> Tour | None:
    """Fetch a tour the assistant is allowed to start (published, this workspace)."""
    tour = await session.get(Tour, tour_id)
    if tour is None or tour.workspace_id != workspace_id or tour.status != "live":
        return None
    return tour


async def next_tour_after(
    session: AsyncSession, workspace_id: str, completed: Tour, *, url: str | None = None
) -> GuideMatch | None:
    """The one live tour worth suggesting after `completed` — or None.

    "Obvious" is deliberately strict: the candidate must clear the normal
    relevance floor against the finished tour's own name/description, so a
    workspace with three unrelated tours suggests nothing rather than whichever
    sorts first. Only tours qualify (a checklist is not "the next tour").
    """
    query = f"{completed.name} {completed.description or ''}".strip()
    if not query:
        return None
    matches = await search_guides(session, workspace_id, query, url=url, limit=4)
    for match in matches:
        if match.kind == "tour" and match.id != completed.id:
            return match
    return None


# ---------------------------------------------------------------------------
# tour-start intent
# ---------------------------------------------------------------------------

#: "Being shown" verbs aimed at the speaker — enough on their own: someone who
#: says "show me…" is asking to be walked through, whatever the object is.
_SHOW_ME_RES = (
    # en
    r"\b(?:show|walk|guide|take)\s+(?:me|us)\b",
    r"\bwalk\s+(?:me|us)\s+through\b",
    # de
    r"\bzeig(?:e|en)?\s+(?:mir|uns|es mir)\b",
    r"\bführ(?:e|en)?\s+(?:mich|uns)\s+(?:durch|dadurch)\b",
    # fr
    r"\bmontre(?:z|r)?[- ](?:moi|nous)\b",
    r"\bguide(?:z)?[- ](?:moi|nous)\b",
    # es / pt-BR
    r"\bmu[eé]str[ae]me(?:lo)?\b",
    r"\bens[eé]ñame\b",
    r"\bgu[ií]ame\b",
    r"\b(?:me\s+)?mostr[ae](?:[- ]me)?\b",
    # it
    r"\bmostrami\b",
    r"\bfammi\s+vedere\b",
    # nl
    r"\blaat\s+(?:me|mij|ons)\s+(?:eens\s+)?zien\b",
    # pl
    r"\bpoka[żz]\s+(?:mi|nam)\b",
    # tr
    r"\bg[öo]ster(?:ir misin| bana)?\b",
)

#: Play/start/replay verbs — these need a guide-ish OBJECT nearby ("start the
#: tour"), otherwise "how do I start a subscription?" would false-positive.
_PLAY_VERB_RE = (
    r"\b(?:play|start|launch|begin|run|replay|restart|repeat|resume|open)\b"
    r"|\b(?:spiel|spiele|spielen|starte|starten|wiederhol|wiederhole)\b"
    r"|\bnochmal\b|\berneut\b"
    r"|\b(?:lance[zr]?|d[ée]marre[zr]?|rejoue[zr]?)\b"
    r"|\b(?:inicia|iniciar|reproduce|reproducir|reinicia)\b"
    r"|\b(?:avvia|riavvia|riproduci)\b"
    r"|\b(?:speel|start|herhaal)\b"
    r"|\b(?:uruchom|odtw[óo]rz|powt[óo]rz)\b"
    r"|\b(?:ba[şs]lat|oynat|tekrar)\b"
)
_GUIDE_NOUN_RE = (
    r"\b(?:tour|tours|guide|walkthrough|walk-through|tutorial|demo|onboarding)\b"
    r"|\b(?:anleitung|rundgang|führung|einführung)\b"
    r"|\b(?:visite|guidée|didacticiel|tutoriel)\b"
    r"|\b(?:recorrido|guía|tutorial)\b"
    r"|\b(?:tour guidato|guida)\b"
    r"|\b(?:rondleiding|handleiding)\b"
    r"|\b(?:przewodnik|samouczek)\b"
    r"|\b(?:tur|rehber|kılavuz)\b"
    r"|ツアー|ガイド|チュートリアル|투어|가이드|导览|教程|引导|جولة|دليل"
)
#: CJK/Japanese/Korean/Arabic "show me / start" phrasings (no word boundaries).
_NON_LATIN_IMPERATIVES = (
    "見せて",
    "案内して",
    "開始して",
    "もう一度",
    "보여줘",
    "보여 주세요",
    "시작해",
    "다시 보여",
    "演示",
    "带我",
    "播放",
    "再看一遍",
    "أرني",
    "شغل الجولة",
)

_SHOW_ME_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _SHOW_ME_RES]
_PLAY_VERB_PATTERN = re.compile(_PLAY_VERB_RE, re.IGNORECASE)
_GUIDE_NOUN_PATTERN = re.compile(_GUIDE_NOUN_RE, re.IGNORECASE)


def is_tour_imperative(text: str | None) -> bool:
    """Did the visitor explicitly ask to be SHOWN (start/play/replay a tour)?

    This is the gate between "answer in words and attach an offer card" and
    "start the tour right now" (`tour_autostart_policy: ask`). Deliberately
    deterministic and conservative: an informational "How do I set up X?" must
    stay False, while "show me", "play that tour again", "zeig mir das nochmal"
    must be True. False negatives are cheap (the visitor gets a one-tap offer
    card); false positives replay today's bug of hijacking the screen.
    """
    if not text:
        return False
    cleaned = " ".join(text.split())
    if not cleaned:
        return False
    if any(pattern.search(cleaned) for pattern in _SHOW_ME_PATTERNS):
        return True
    if any(phrase in cleaned for phrase in _NON_LATIN_IMPERATIVES):
        return True
    # Verb + guide noun ("start the tour", "spiel die Tour nochmal ab").
    return bool(_PLAY_VERB_PATTERN.search(cleaned) and _GUIDE_NOUN_PATTERN.search(cleaned))
