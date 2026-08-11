"""The multilingual loop: what a visitor writes decides what they are shown.

Covers the seam between `app.services.language` (which decides) and the three
places that consume the decision: the widget's own interface, the help center,
and the agent's system prompt.
"""

from __future__ import annotations

import httpx

from app.agents.engine import _language_prompt
from app.core.db import get_session_factory
from app.models.agent import Agent
from app.models.article import Article
from app.models.conversation import Conversation
from app.rag.retrieval import LOCALE_MATCH_BOOST, LOCALE_MISMATCH_PENALTY, _locale_boost
from app.services.articles import _pick_locale_variants
from tests.widget.conftest import WidgetSetup, auth_headers, boot

GERMAN = "Hallo, ich kann mich nicht in mein Konto einloggen und brauche bitte Hilfe."
TURKISH = "Merhaba, hesabıma giriş yapamıyorum ve yardıma ihtiyacım var lütfen."


async def _boot_token(client: httpx.AsyncClient, widget: WidgetSetup, visitor_id: str) -> str:
    response = await boot(client, widget.widget_key, visitor_id=visitor_id)
    return response.json()["token"]


class TestLearningFromWhatTheVisitorWrites:
    async def test_a_german_message_reports_the_detected_locale(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        token = await _boot_token(client, widget, "v-de")
        created = await client.post(
            "/api/widget/conversations",
            json={"message": GERMAN},
            headers=auth_headers(token),
        )
        assert created.status_code == 201, created.text
        conversation_id = created.json()["id"]

        # The reply endpoint is what hands the locale back to the widget.
        replied = await client.post(
            f"/api/widget/conversations/{conversation_id}/messages",
            json={"message": "Ich habe es schon mehrfach versucht, aber es klappt nicht."},
            headers=auth_headers(token),
        )
        assert replied.status_code == 201, replied.text
        assert replied.json()["detected_locale"] == "de"

    async def test_the_locale_survives_to_the_next_visit(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        token = await _boot_token(client, widget, "v-returning")
        await client.post(
            "/api/widget/conversations",
            json={"message": TURKISH},
            headers=auth_headers(token),
        )
        # Booting again is what happens when they come back tomorrow: the widget
        # must render in Turkish before they type anything at all.
        again = await boot(client, widget.widget_key, visitor_id="v-returning")
        assert again.json()["contact"]["locale"] == "tr"

    async def test_a_short_ack_does_not_clear_a_known_locale(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        token = await _boot_token(client, widget, "v-ack")
        created = await client.post(
            "/api/widget/conversations",
            json={"message": GERMAN},
            headers=auth_headers(token),
        )
        conversation_id = created.json()["id"]
        replied = await client.post(
            f"/api/widget/conversations/{conversation_id}/messages",
            json={"message": "ok"},
            headers=auth_headers(token),
        )
        # "ok" is not evidence of anything; the interface must not flip back.
        assert replied.json()["detected_locale"] == "de"

    async def test_english_visitors_are_unaffected(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        token = await _boot_token(client, widget, "v-en")
        created = await client.post(
            "/api/widget/conversations",
            json={"message": "Hi, I can't log in to my account. How do I reset my password?"},
            headers=auth_headers(token),
        )
        conversation_id = created.json()["id"]
        replied = await client.post(
            f"/api/widget/conversations/{conversation_id}/messages",
            json={"message": "I have tried it several times and it still will not work for me."},
            headers=auth_headers(token),
        )
        assert replied.json()["detected_locale"] == "en"

    async def test_the_thread_is_stamped_so_the_agent_can_read_it(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        token = await _boot_token(client, widget, "v-stamp")
        created = await client.post(
            "/api/widget/conversations",
            json={"message": GERMAN},
            headers=auth_headers(token),
        )
        conversation_id = created.json()["id"]

        async with get_session_factory()() as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None
            # The engine composes its prompt from the conversation alone.
            assert conversation.attributes.get("locale") == "de"

    async def test_the_boot_config_carries_the_workspace_default(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        response = await boot(client, widget.widget_key, visitor_id="v-default")
        # Not set on this workspace, so it is explicitly null rather than absent —
        # the widget treats a missing key and a null the same, but the contract
        # is easier to reason about when the field is always present.
        assert "default_locale" in response.json()["config"]


class TestHelpCenterFollowsTheReader:
    async def test_articles_endpoint_accepts_an_explicit_locale(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        token = await _boot_token(client, widget, "v-help")
        response = await client.get(
            "/api/widget/articles", params={"locale": "de"}, headers=auth_headers(token)
        )
        assert response.status_code == 200, response.text

    async def test_an_unknown_locale_is_ignored_not_rejected(
        self, client: httpx.AsyncClient, widget: WidgetSetup
    ):
        token = await _boot_token(client, widget, "v-help2")
        response = await client.get(
            "/api/widget/articles", params={"locale": "klingon"}, headers=auth_headers(token)
        )
        assert response.status_code == 200, response.text


class TestPickLocaleVariants:
    """One row per translation group, in the best available language."""

    @staticmethod
    def _article(locale: str, key: str) -> Article:
        return Article(
            workspace_id="w",
            title=f"{key}-{locale}",
            slug=f"{key}-{locale}",
            locale=locale,
            translation_key=key,
        )

    def test_prefers_the_readers_language(self) -> None:
        rows = [self._article("en", "reset"), self._article("de", "reset")]
        picked = _pick_locale_variants(rows, "de", "en")
        assert [r.locale for r in picked] == ["de"]

    def test_falls_back_per_group_not_wholesale(self) -> None:
        # Only one of the two articles is translated. A German reader should get
        # the German one *and* the untranslated English one — not an empty
        # help center, and not the English version of both.
        rows = [
            self._article("en", "reset"),
            self._article("de", "reset"),
            self._article("en", "billing"),
        ]
        picked = _pick_locale_variants(rows, "de", "en")
        assert sorted((r.translation_key, r.locale) for r in picked) == [
            ("billing", "en"),
            ("reset", "de"),
        ]

    def test_falls_back_to_the_workspace_default_before_english(self) -> None:
        rows = [self._article("fr", "reset"), self._article("en", "reset")]
        picked = _pick_locale_variants(rows, "de", "fr")
        assert [r.locale for r in picked] == ["fr"]

    def test_returns_something_even_when_no_preference_matches(self) -> None:
        rows = [self._article("ja", "reset")]
        picked = _pick_locale_variants(rows, "de", "fr")
        assert [r.locale for r in picked] == ["ja"]

    def test_never_returns_the_same_article_twice(self) -> None:
        rows = [self._article(code, "reset") for code in ("en", "de", "fr", "ja")]
        assert len(_pick_locale_variants(rows, "de", "en")) == 1


class TestLocaleBoost:
    """Retrieval prefers the reader's language without excluding others."""

    def test_matching_locale_is_boosted(self) -> None:
        assert _locale_boost({"locale": "de"}, "de") == LOCALE_MATCH_BOOST

    def test_mismatched_locale_is_only_penalised(self) -> None:
        # A penalty, not a filter: an English article must still be able to win.
        assert _locale_boost({"locale": "en"}, "de") == LOCALE_MISMATCH_PENALTY
        assert LOCALE_MISMATCH_PENALTY > 0

    def test_documents_without_a_locale_are_untouched(self) -> None:
        # Crawled pages and uploads have no locale; a workspace that never
        # translates anything must see no ranking change at all.
        assert _locale_boost({}, "de") == 1.0
        assert _locale_boost({"article_id": "a"}, "de") == 1.0
        assert _locale_boost(None, "de") == 1.0

    def test_no_reader_locale_means_no_change(self) -> None:
        assert _locale_boost({"locale": "de"}, None) == 1.0


class TestReplyLanguagePrompt:
    def _agent(self, **settings: object) -> Agent:
        return Agent(workspace_id="w", name="Sage", settings=dict(settings))

    def test_default_tells_the_model_to_mirror_the_customer(self) -> None:
        prompt = _language_prompt({}, None)
        assert "same language the customer writes in" in prompt
        # The failure mode we are guarding against: an English system prompt
        # pulling the reply into English.
        assert "Never answer in English just because" in prompt

    def test_a_detected_locale_becomes_a_hint_not_a_lock(self) -> None:
        prompt = _language_prompt({}, "de")
        assert "German" in prompt
        # Still told to follow them if they switch — the detection is a guess.
        assert "If they switch languages, follow them" in prompt

    def test_a_configured_language_overrides_the_customer(self) -> None:
        prompt = _language_prompt({"reply_language": "ja"}, "de")
        assert "Always reply in Japanese" in prompt
        assert "German" not in prompt

    def test_a_configured_language_is_normalised(self) -> None:
        assert "Portuguese (Brazil)" in _language_prompt({"reply_language": "pt"}, None)

    def test_an_unknown_configured_language_falls_back_to_mirroring(self) -> None:
        prompt = _language_prompt({"reply_language": "klingon"}, None)
        assert "same language the customer writes in" in prompt
