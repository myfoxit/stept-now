"""Detecting which of our thirteen languages a piece of text is written in.

**Why this is not an LLM call.** The model already writes its reply in the
customer's language without being told which one that is — that is what models
are good at, and asking first would add a round trip to the one path where
latency is most visible. What a model cannot do is tell the *widget* which
language to render its buttons in, or tell retrieval which locale's articles to
prefer, before the reply exists. That is this module's job, and for those two
uses a fast deterministic answer beats an accurate slow one: being wrong costs a
mislabelled interface that the next message corrects, not a wrong answer.

**How it works.** Two stages, because the two script families need different
evidence:

1. *Script* settles Japanese, Korean, Chinese and Arabic outright. Kana, Hangul
   and Arabic block characters appear in no other language we ship, so a single
   character is proof. Han characters without kana mean Chinese (Japanese text
   of any length practically always carries kana).
2. *Latin-script* languages need statistics: a bag of high-frequency function
   words per language, plus diacritics and letter pairs that only some of them
   use. "der/die/das" is German the way "ı/ğ/ş" is Turkish.

Deliberately conservative. `detect_language` returns None unless the winner
clears an absolute score *and* beats the runner-up, because "hi" and "ok, thanks"
are not evidence of anything, and silently deciding a visitor is Dutch on the
strength of the word "is" is worse than not deciding at all.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import SUPPORTED_LOCALES
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import AuthorType, Message, MessageDirection

#: Below this many word characters we do not guess at all.
MIN_CHARS = 8
#: The winner must reach this score…
MIN_SCORE = 2.0
#: …and beat the runner-up by this much, so near-ties stay undecided.
MIN_MARGIN = 1.0

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# --------------------------------------------------------------------------
# Stage 1: scripts that identify a language by themselves
# --------------------------------------------------------------------------

_HIRAGANA = (0x3040, 0x309F)
_KATAKANA = (0x30A0, 0x30FF)
_HANGUL = ((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F))
_HAN = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))
_ARABIC = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF))


def _in(code: int, *ranges: tuple[int, int]) -> bool:
    return any(low <= code <= high for low, high in ranges)


def _script_language(text: str) -> str | None:
    """Language implied by the script alone, if any."""
    kana = hangul = han = arabic = 0
    for char in text:
        code = ord(char)
        if _in(code, _HIRAGANA, _KATAKANA):
            kana += 1
        elif _in(code, *_HANGUL):
            hangul += 1
        elif _in(code, *_HAN):
            han += 1
        elif _in(code, *_ARABIC):
            arabic += 1

    if arabic >= 2:
        return "ar"
    if hangul >= 2:
        return "ko"
    # Kana settle Japanese vs Chinese: Japanese prose of any length has kana,
    # and kana never appear in Chinese.
    if kana >= 1:
        return "ja"
    if han >= 2:
        return "zh-CN"
    return None


# --------------------------------------------------------------------------
# Stage 2: Latin-script scoring
# --------------------------------------------------------------------------

#: High-frequency function words. Chosen for *discrimination*, not frequency:
#: a word shared by four of our languages earns nobody any points, so shared
#: articles and copulas are largely left out in favour of ones that split them.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        "the and is are you your what how can not have has with this that for "
        "was were they there their would could should about please thanks want "
        "need doesn't don't i'm it's my me our we do does did to in it at on am "
        "when where why which will get got just still my account".split()
    ),
    "de": frozenset(
        "der die das und ist sind ich nicht ein eine einen wie mit für auf sich "
        "kann wir sie aber oder auch noch schon bitte danke haben habe wurde "
        "möchte kein keine gibt bei zum zur".split()
    ),
    "fr": frozenset(
        "le la les des est sont je ne pas une pour avec dans que vous nous mais "
        "ou aussi bien merci comment pourquoi puis peux avoir suis très cette "
        "mon ma mes votre sur au aux".split()
    ),
    "es": frozenset(
        "el los las una para con que por como pero también gracias hola donde "
        "cuando porque muy este esta esto mi mis su sus hay estoy tengo puedo "
        "quiero necesito ser está están cómo dónde cuándo qué quién más sí "
        "cuenta".split()
    ),
    "pt-BR": frozenset(
        "não uma para com que por como mas também obrigado olá onde quando "
        "porque muito este esta isso meu minha seu sua tem estou posso quero "
        "preciso ser está estão você vocês então".split()
    ),
    "it": frozenset(
        "il lo gli una per con che come già perché grazie ciao dove quando "
        "molto questo questa mio mia suo sua sono posso voglio bisogno essere "
        "anche ma però delle degli".split()
    ),
    "nl": frozenset(
        "het een en is zijn niet voor met dat ik je jij hoe kan mijn maar ook "
        "nog wel graag bedankt hallo waar wanneer waarom heel deze dit heb "
        "hebben wil moet worden naar".split()
    ),
    "pl": frozenset(
        "nie jest są w z na do że się jak mam moje ale czy dla tak tego jestem "
        "chcę mogę dziękuję cześć gdzie kiedy dlaczego bardzo ten ta to być "
        "który która przez".split()
    ),
    "tr": frozenset(
        "ve bir bu için ile var yok nasıl ben benim ama değil çok daha bunu "
        "şey teşekkürler merhaba nerede zaman neden olarak olan ise gibi kadar "
        "sonra önce beni bana".split()
    ),
}

#: Characters that only some of these languages use. Strong evidence, so they
#: are weighted above a single stopword hit.
_DIACRITICS: dict[str, tuple[frozenset[str], float]] = {
    "de": (frozenset("ßäöü"), 1.5),
    "fr": (frozenset("çéèêëàâîïôûùœ"), 1.2),
    "es": (frozenset("ñ¿¡"), 2.0),
    "pt-BR": (frozenset("ãõâê"), 1.8),
    "it": (frozenset("àèéìòù"), 0.8),
    # `ó` is deliberately absent: Polish shares it with Spanish, Italian, French
    # and Portuguese, and including it made "¿Dónde puedo…?" score as Polish.
    # The remaining eight appear in no other language we ship.
    "pl": (frozenset("ąćęłńśźż"), 2.5),
    "tr": (frozenset("ığşİĞŞ"), 2.5),
    "nl": (frozenset("ĳ"), 1.0),
}

#: Letter sequences that are near-signatures. Cheap to check, and they rescue
#: the cases where a sentence happens to contain no function word we know.
_NGRAMS: dict[str, tuple[tuple[str, ...], float]] = {
    "de": (("sch", "ung", "eit", "ich "), 0.6),
    "nl": (("ij", "aan", "zijn", "lijk"), 0.7),
    "pt-BR": (("ção", "ões", "nh"), 1.5),
    "es": (("ción", "ll", "qué"), 0.7),
    "fr": (("qu'", "c'est", "eux", "tion "), 0.6),
    "it": (("gli", "zione", "cch"), 0.9),
    "pl": (("sz", "cz", "rz", "prz"), 0.8),
    "tr": (("lar", "ler", "yor", "mek"), 0.7),
}


def _word_weight(word: str) -> float:
    """Longer function words are stronger evidence than shorter ones.

    Two- and three-letter words are where languages collide: Polish "do"/"to"
    are also English words, French "la" is Spanish, Dutch "en" is French. An
    unweighted count let a Polish reading win an English sentence on those two
    words alone. Length is a decent proxy for how many languages share a token.
    """
    if len(word) <= 2:
        return 0.4
    if len(word) == 3:
        return 0.7
    return 1.0


def _latin_scores(text: str) -> dict[str, float]:
    lowered = text.lower()
    words = _WORD_RE.findall(lowered)
    scores: defaultdict[str, float] = defaultdict(float)

    for language, stopwords in _STOPWORDS.items():
        hit = sum(_word_weight(word) for word in words if word in stopwords)
        if hit:
            scores[language] += hit

    for language, (chars, weight) in _DIACRITICS.items():
        hits = sum(1 for char in lowered if char in chars)
        if hits:
            # Diminishing returns: five umlauts are not five times the evidence
            # of one, and without a cap a single accented word can outvote a
            # sentence full of function words.
            scores[language] += weight * min(hits, 3)

    for language, (grams, weight) in _NGRAMS.items():
        hits = sum(lowered.count(gram) for gram in grams)
        if hits:
            scores[language] += weight * min(hits, 3)

    return dict(scores)


def detect_language(text: str | None) -> str | None:
    """Best guess at `text`'s language, or None when the evidence is thin.

    Only ever returns a locale we ship, so the result can be stored on a
    contact or handed to the widget without further validation.
    """
    if not text:
        return None
    cleaned = " ".join(text.split())
    if not cleaned:
        return None

    # Strip anything that is not prose before measuring length: a one-word
    # question wrapped in a URL is still a one-word question.
    prose = re.sub(r"https?://\S+|\S+@\S+|`[^`]*`", " ", cleaned)
    letters = [c for c in prose if unicodedata.category(c).startswith("L")]

    by_script = _script_language(prose)
    if by_script is not None:
        return by_script

    if len(letters) < MIN_CHARS:
        return None

    scores = _latin_scores(prose)
    if not scores:
        return None

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_score < MIN_SCORE or (best_score - runner_up_score) < MIN_MARGIN:
        return None
    return best if best in SUPPORTED_LOCALES else None


async def learn_contact_locale(
    session: AsyncSession,
    contact: Contact,
    *,
    conversation: Conversation,
) -> str | None:
    """Update `contact.locale` from what they have actually been writing.

    Called after an inbound message lands. Reads the recent inbound messages of
    the thread rather than trusting the newest one alone: a single sentence
    should not flip someone's interface language, but a conversation that has
    clearly moved into German should.

    Returns the contact's locale after the update, so the caller can hand it
    straight back to the widget.
    """
    rows = (
        await session.execute(
            select(Message.content)
            .where(
                Message.conversation_id == conversation.id,
                Message.direction == MessageDirection.IN.value,
                Message.author_type == AuthorType.CONTACT.value,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(_HISTORY_WINDOW)
        )
    ).scalars()

    texts = [text for text in reversed(list(rows)) if text]
    if not texts:
        return contact.locale

    settled = detect_conversation_language(texts)
    # Never *clear* a known locale: "ok thanks" is not evidence that we were
    # wrong, and dropping back to browser detection mid-conversation would make
    # the widget flicker between languages.
    if settled is not None and settled != contact.locale:
        contact.locale = settled

    # Stamp the thread too. The agent engine composes its prompt from the
    # conversation alone and has no contact loaded; a JSON attribute keeps that
    # read free rather than adding a join to every agent turn.
    if contact.locale and conversation.attributes.get("locale") != contact.locale:
        conversation.attributes = {**conversation.attributes, "locale": contact.locale}
    return contact.locale


#: How many recent visitor messages inform the contact's language.
_HISTORY_WINDOW = 8


def detect_conversation_language(texts: list[str]) -> str | None:
    """Language for a whole conversation, from the visitor's messages.

    Later messages win ties because people warm up: an opening "hi" in English
    followed by three paragraphs of German is a German conversation. Each
    message is weighted by how much prose it actually contains, so one long
    message outvotes several one-word ones.
    """
    weighted: defaultdict[str, float] = defaultdict(float)
    for index, text in enumerate(texts):
        code = detect_language(text)
        if code is None:
            continue
        length_weight = min(len(text.split()), 40) / 10.0
        recency_weight = 1.0 + index / max(len(texts), 1)
        weighted[code] += max(length_weight, 0.5) * recency_weight
    if not weighted:
        return None
    return max(weighted.items(), key=lambda item: item[1])[0]
