"""Locale registry, Accept-Language negotiation, and message catalogs.

One catalog format is shared by all four runtimes in this repo — this module,
the dashboard (i18next), the widget, and the extension — so a key can be moved
between surfaces without rewriting it:

- **flat dotted keys**: ``{"email.reset.subject": "Reset your password"}``.
  Flat beats nested for a catalog that is diffed and machine-validated: a
  missing key is one missing line, not a collapsed subtree.
- **interpolation** with ``{{name}}``.
- **plurals** as sibling keys suffixed with a CLDR category —
  ``count_one`` / ``count_other``, plus ``_zero``/``_two``/``_few``/``_many``
  where the language needs them. This is i18next's JSON-v4 layout, which the
  browser surfaces get natively from ``Intl.PluralRules``.

Python has no ``Intl``, so `plural_category` implements the CLDR rules for the
five locales we ship. That is a bounded, checkable amount of code; a general
CLDR engine would not be. Adding a locale means adding its rule here *and* to
the ``PLURAL_CATEGORIES`` test fixture, which is deliberate friction — shipping
a locale whose plurals silently fall back to English is worse than not shipping
it.

Lookup falls back down a chain (``pt-BR`` → ``pt`` → ``en``) and, if a key is
missing everywhere, returns the key itself rather than raising. A missing string
should degrade to a visible key in one line of UI, never take down a password
reset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Directory holding ``<locale>.json`` catalogs for backend-rendered copy.
CATALOG_DIR = Path(__file__).resolve().parent.parent / "i18n"

DEFAULT_LOCALE = "en"


@dataclass(frozen=True)
class LocaleInfo:
    """A locale we ship translations for."""

    #: BCP-47 tag as we canonicalise it (the key used in catalogs and columns).
    code: str
    #: Name in English, for the dashboard's locale picker when it renders in English.
    english_name: str
    #: Endonym — what speakers call the language. A picker that lists "German"
    #: to someone who only reads German is a picker they cannot use.
    native_name: str
    #: "ltr" | "rtl" — drives ``<html dir>`` and the widget's layout direction.
    direction: str = "ltr"


#: The shipped set. Mirrored in `frontend/src/i18n/locales.ts`,
#: `widget/src/i18n/locales.ts` and `extension/src/i18n/locales.ts`; the
#: `scripts/check-i18n.mjs` guard fails the build if the copies drift.
LOCALES: dict[str, LocaleInfo] = {
    "en": LocaleInfo("en", "English", "English"),
    "de": LocaleInfo("de", "German", "Deutsch"),
    "fr": LocaleInfo("fr", "French", "Français"),
    "es": LocaleInfo("es", "Spanish", "Español"),
    "it": LocaleInfo("it", "Italian", "Italiano"),
}

SUPPORTED_LOCALES: tuple[str, ...] = tuple(LOCALES)

#: Tags people and browsers actually send that are not our canonical spelling.
#: Region-stripping (``de-AT`` → ``de``) is handled generically in
#: `normalize_locale`; this table is only for the cases where the *base*
#: language alone would resolve wrongly or not at all.
_ALIASES: dict[str, str] = {}
# Empty since the shipped set shrank to five base-language locales; the
# mechanism stays for the day a regional locale (pt-BR, zh-CN, …) returns.

#: English names for languages the *agent* can be told to reply in.
#:
#: Deliberately far wider than `LOCALES`, because those are two different
#: questions. `LOCALES` answers "which languages is our interface translated
#: into"; `agent.settings.reply_language` answers "which language should the AI
#: write in". A workspace whose customers are Japanese can want Japanese replies
#: while its own agents read the English dashboard — resolving that setting
#: through `normalize_locale` tied the two together and silently discarded any
#: configured language we happened to have no catalog for.
#:
#: Same doctrine as `app.services.language`, which recognises more languages than
#: it can render: naming is cheap, rendering is not.
_REPLY_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pt-br": "Portuguese (Brazil)",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-hans": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "zh-hant": "Traditional Chinese",
    "ar": "Arabic",
    "he": "Hebrew",
    "ru": "Russian",
    "uk": "Ukrainian",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "nb": "Norwegian Bokmål",
    "fi": "Finnish",
    "is": "Icelandic",
    "cs": "Czech",
    "sk": "Slovak",
    "hu": "Hungarian",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "el": "Greek",
    "hr": "Croatian",
    "sr": "Serbian",
    "sl": "Slovenian",
    "et": "Estonian",
    "lv": "Latvian",
    "lt": "Lithuanian",
    "ca": "Catalan",
    "gl": "Galician",
    "eu": "Basque",
    "ga": "Irish",
    "id": "Indonesian",
    "ms": "Malay",
    "th": "Thai",
    "vi": "Vietnamese",
    "tl": "Tagalog",
    "fil": "Filipino",
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "ur": "Urdu",
    "fa": "Persian",
    "sw": "Swahili",
    "af": "Afrikaans",
}

# Subtags are 1–8 chars, not 2–8: BCP-47 singletons (`-x-` for private use,
# `-u-` for Unicode extensions) are a single character, and `de-DE-u-co-phonebk`
# is a tag a real browser can send.
_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$")


def normalize_locale(raw: str | None) -> str | None:
    """Canonicalise a BCP-47 tag to a supported locale, or `None`.

    Case-insensitive, tolerant of ``_`` separators, and falls back from a
    region to its base language (``de-AT`` → ``de``) before giving up.
    """
    if not raw:
        return None
    tag = raw.strip().replace("_", "-")
    if not tag or not _TAG_RE.match(tag):
        return None
    lowered = tag.lower()

    # Exact match against the shipped set, case-insensitively.
    for code in SUPPORTED_LOCALES:
        if code.lower() == lowered:
            return code
    if lowered in _ALIASES:
        return _ALIASES[lowered]

    # Strip subtags right-to-left: `pt-BR-x-foo` → `pt-BR` → `pt`.
    parts = lowered.split("-")
    while len(parts) > 1:
        parts.pop()
        candidate = "-".join(parts)
        for code in SUPPORTED_LOCALES:
            if code.lower() == candidate:
                return code
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    return None


def reply_language_name(raw: str | None) -> str | None:
    """English name for a configured agent reply language, or `None`.

    Unlike `normalize_locale` this is **not** restricted to the shipped locales:
    what language the AI writes is independent of what languages the interface
    is translated into. A regional tag keeps its own name where we have one
    (``pt-BR`` → Portuguese (Brazil)) and otherwise falls back to its base
    language (``de-AT`` → German).

    `None` means "mirror the customer instead". That is the right answer for a
    tag we cannot name: instructing a model to "always reply in xx" is worse
    than letting it follow the language the customer is already writing in.
    """
    if not raw:
        return None
    tag = raw.strip().replace("_", "-")
    if not tag or not _TAG_RE.match(tag):
        return None

    parts = tag.lower().split("-")
    while parts:
        name = _REPLY_LANGUAGE_NAMES.get("-".join(parts))
        if name is not None:
            return name
        parts.pop()
    return None


def parse_accept_language(header: str | None) -> list[str]:
    """Return supported locales from an ``Accept-Language`` header, best first.

    Honours q-values and drops malformed segments rather than rejecting the
    whole header — browsers and proxies send some strange things, and a bad
    segment should cost the user one preference, not their language.
    """
    if not header:
        return []
    scored: list[tuple[float, int, str]] = []
    for index, segment in enumerate(header.split(",")):
        piece = segment.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        quality = 1.0
        if params:
            key, _, value = params.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 1.0
        if quality <= 0:
            continue  # `q=0` means "explicitly not this one"
        code = normalize_locale(tag)
        if code is None:
            continue
        # `index` keeps the header's own order stable among equal q-values.
        scored.append((-quality, index, code))

    ordered: list[str] = []
    for _, _, code in sorted(scored):
        if code not in ordered:
            ordered.append(code)
    return ordered


def negotiate(
    header: str | None = None,
    *,
    preferred: str | None = None,
    fallback: str | None = None,
) -> str:
    """Pick a locale: explicit preference, then the header, then the fallback.

    `preferred` is a stored choice (a user's setting, a widget boot option) and
    always wins — someone who set the dashboard to Japanese means it, whatever
    their browser advertises.
    """
    explicit = normalize_locale(preferred)
    if explicit:
        return explicit
    for code in parse_accept_language(header):
        return code
    return normalize_locale(fallback) or DEFAULT_LOCALE


def workspace_locale(settings: dict[str, Any] | None) -> str | None:
    """A workspace's default language from its settings blob, if it set one.

    Lives in `Workspace.settings` rather than a column, alongside `timezone` —
    both are "how this tenant wants things rendered" and neither is queried.
    """
    if not settings:
        return None
    return normalize_locale(settings.get("default_locale"))


def direction(locale: str) -> str:
    """``"rtl"`` for right-to-left locales, else ``"ltr"``."""
    info = LOCALES.get(normalize_locale(locale) or DEFAULT_LOCALE)
    return info.direction if info else "ltr"


# --------------------------------------------------------------------------
# CLDR plural categories
# --------------------------------------------------------------------------


def plural_category(locale: str, count: float) -> str:
    """CLDR plural category for `count` in `locale`.

    Implements only the five shipped locales; anything else is treated as
    English. Counts are compared as integers where the rule is integer-only,
    which is what every call site here passes.
    """
    code = normalize_locale(locale) or DEFAULT_LOCALE
    n = abs(count)
    i = int(n)
    is_int = n == i

    match code:
        case "fr":
            # French treats 0 and 1 alike ("0 message", "1 message").
            return "one" if i in (0, 1) else "other"
        case "es":
            return "one" if n == 1 else "other"
        case _:
            # en, de, it: `one` only for the integer 1.
            return "one" if is_int and i == 1 else "other"


# --------------------------------------------------------------------------
# Catalogs
# --------------------------------------------------------------------------


@lru_cache(maxsize=len(SUPPORTED_LOCALES) + 1)
def load_catalog(locale: str) -> dict[str, str]:
    """Load and cache one locale's catalog. Missing file ⇒ empty catalog."""
    code = normalize_locale(locale) or DEFAULT_LOCALE
    path = CATALOG_DIR / f"{code}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def fallback_chain(locale: str) -> list[str]:
    """Locales to search, most specific first, always ending at the default."""
    code = normalize_locale(locale) or DEFAULT_LOCALE
    chain = [code]
    base = code.split("-")[0]
    if base != code and base in LOCALES:
        chain.append(base)
    if DEFAULT_LOCALE not in chain:
        chain.append(DEFAULT_LOCALE)
    return chain


_INTERPOLATION_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _interpolate(template: str, values: dict[str, Any]) -> str:
    """Substitute ``{{name}}`` placeholders; unknown names are left in place.

    Leaving an unresolved placeholder visible beats raising: the reader still
    gets the sentence, and the gap is obvious in a screenshot.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _INTERPOLATION_RE.sub(replace, template)


def translate(key: str, locale: str = DEFAULT_LOCALE, /, **values: Any) -> str:
    """Look up `key` in `locale`, interpolate `values`, and return the string.

    Pass ``count=`` to select a plural form: the lookup tries
    ``<key>_<category>`` before the bare key, so a catalog can define
    ``items_one``/``items_other`` and callers just ask for ``items``.
    """
    count = values.get("count")
    candidates: list[str] = []
    if isinstance(count, int | float) and not isinstance(count, bool):
        candidates.append(f"{key}_{plural_category(locale, count)}")
        # `_other` is the safety net for a catalog that only defined the common
        # forms — better a slightly-wrong plural than a raw key.
        candidates.append(f"{key}_other")
    candidates.append(key)

    for code in fallback_chain(locale):
        catalog = load_catalog(code)
        for candidate in candidates:
            template = catalog.get(candidate)
            if template is not None:
                return _interpolate(template, values)
    return key


#: Short alias — `t("email.reset.subject", locale)` reads better at call sites.
t = translate
