"""Locale negotiation, CLDR plurals, and catalog lookup."""

from __future__ import annotations

import json

import pytest

from app.core import i18n


class TestNormalizeLocale:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("en", "en"),
            ("EN", "en"),
            ("de-DE", "de"),
            ("de_AT", "de"),  # underscore separator, region stripped
            ("pt", "pt-BR"),  # we ship only pt-BR
            ("pt-BR", "pt-BR"),
            ("pt-br", "pt-BR"),
            ("pt-PT", "pt-BR"),  # region falls back to the shipped Portuguese
            ("zh", "zh-CN"),
            ("zh-Hans", "zh-CN"),
            ("zh-TW", "zh-CN"),
            ("ar-EG", "ar"),
            ("pt-BR-x-private", "pt-BR"),  # strips right-to-left until it hits a match
        ],
    )
    def test_canonicalises(self, raw: str, expected: str) -> None:
        assert i18n.normalize_locale(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "klingon", "xx", "!!", "123", "e"])
    def test_rejects_unsupported_and_malformed(self, raw: str | None) -> None:
        assert i18n.normalize_locale(raw) is None


class TestAcceptLanguage:
    def test_orders_by_q_value(self) -> None:
        header = "fr;q=0.5, de;q=0.9, en;q=0.1"
        assert i18n.parse_accept_language(header) == ["de", "fr", "en"]

    def test_missing_q_defaults_to_one_and_keeps_header_order(self) -> None:
        assert i18n.parse_accept_language("de, fr;q=0.9, ja") == ["de", "ja", "fr"]

    def test_drops_unsupported_and_q_zero(self) -> None:
        assert i18n.parse_accept_language("klingon, de;q=0, fr") == ["fr"]

    def test_deduplicates_after_normalisation(self) -> None:
        # de-DE and de-AT both normalise to `de`; the better q wins, once.
        assert i18n.parse_accept_language("de-DE;q=0.9, de-AT;q=0.8, ja;q=0.5") == ["de", "ja"]

    def test_survives_garbage(self) -> None:
        assert i18n.parse_accept_language("de;q=notanumber, ,;;, fr") == ["de", "fr"]

    def test_empty_header(self) -> None:
        assert i18n.parse_accept_language(None) == []
        assert i18n.parse_accept_language("") == []


class TestNegotiate:
    def test_stored_preference_beats_header(self) -> None:
        assert i18n.negotiate("de,fr", preferred="ja") == "ja"

    def test_falls_through_to_header_when_preference_unsupported(self) -> None:
        assert i18n.negotiate("de,fr", preferred="klingon") == "de"

    def test_falls_back_to_workspace_default_then_english(self) -> None:
        assert i18n.negotiate(None, fallback="it") == "it"
        assert i18n.negotiate(None) == "en"


class TestPluralCategory:
    @pytest.mark.parametrize(
        ("locale", "count", "expected"),
        [
            # Germanic/Romance: `one` is exactly 1.
            ("en", 0, "other"),
            ("en", 1, "one"),
            ("en", 2, "other"),
            ("de", 1, "one"),
            ("nl", 2, "other"),
            ("it", 1, "one"),
            ("es", 1, "one"),
            ("tr", 1, "one"),
            ("tr", 3, "other"),
            # French and Brazilian Portuguese group 0 with 1.
            ("fr", 0, "one"),
            ("fr", 1, "one"),
            ("fr", 2, "other"),
            ("pt-BR", 0, "one"),
            ("pt-BR", 5, "other"),
            # No grammatical plural at all.
            ("ja", 0, "other"),
            ("ja", 1, "other"),
            ("ko", 7, "other"),
            ("zh-CN", 1, "other"),
            # Polish: one / few / many.
            ("pl", 1, "one"),
            ("pl", 2, "few"),
            ("pl", 4, "few"),
            ("pl", 5, "many"),
            ("pl", 12, "many"),  # 12–14 are `many`, not `few`
            ("pl", 13, "many"),
            ("pl", 22, "few"),  # …but 22 is `few` again
            ("pl", 25, "many"),
            # Arabic uses all six.
            ("ar", 0, "zero"),
            ("ar", 1, "one"),
            ("ar", 2, "two"),
            ("ar", 3, "few"),
            ("ar", 10, "few"),
            ("ar", 11, "many"),
            ("ar", 99, "many"),
            ("ar", 100, "other"),
            ("ar", 103, "few"),  # 103 % 100 == 3
        ],
    )
    def test_cldr_rules(self, locale: str, count: int, expected: str) -> None:
        assert i18n.plural_category(locale, count) == expected

    def test_unknown_locale_behaves_like_english(self) -> None:
        assert i18n.plural_category("klingon", 1) == "one"
        assert i18n.plural_category("klingon", 2) == "other"

    def test_negative_counts_use_magnitude(self) -> None:
        assert i18n.plural_category("en", -1) == "one"


class TestDirection:
    def test_arabic_is_rtl_everything_else_ltr(self) -> None:
        assert i18n.direction("ar") == "rtl"
        assert i18n.direction("ar-EG") == "rtl"
        assert i18n.direction("en") == "ltr"
        assert i18n.direction("ja") == "ltr"
        assert i18n.direction("klingon") == "ltr"


class TestTranslate:
    def test_returns_english_string(self) -> None:
        assert i18n.t("api.auth.logged_out", "en") == "Logged out"

    def test_interpolates(self) -> None:
        out = i18n.t("email.invite.subject", "en", workspace="Acme")
        assert out == "You've been invited to Acme on Stept"

    def test_unknown_placeholder_is_left_visible_not_raised(self) -> None:
        # Missing `workspace` must not explode a password-reset send.
        assert "{{workspace}}" in i18n.t("email.invite.subject", "en")

    def test_missing_key_returns_the_key(self) -> None:
        assert i18n.t("nope.not.a.key", "en") == "nope.not.a.key"

    def test_plural_selection_uses_the_locale_rules(self) -> None:
        assert (
            i18n.t("email.invite.validity", "en", count=1) == "This invitation is valid for 1 day."
        )
        assert (
            i18n.t("email.invite.validity", "en", count=7) == "This invitation is valid for 7 days."
        )

    def test_falls_back_through_the_chain(self) -> None:
        # Every shipped locale resolves this key, via its own catalog or English.
        for code in i18n.SUPPORTED_LOCALES:
            assert i18n.t("api.auth.logged_out", code) != "api.auth.logged_out"

    def test_fallback_chain_shape(self) -> None:
        assert i18n.fallback_chain("pt-BR") == ["pt-BR", "en"]
        assert i18n.fallback_chain("de") == ["de", "en"]
        assert i18n.fallback_chain("en") == ["en"]


class TestCatalogIntegrity:
    """The shipped catalogs must actually be complete and well-formed.

    This is the guard that makes translations safe to accept from contributors:
    a PR that adds a key to English without adding it everywhere, or that leaves
    a stray `{{placeholder}}` untranslated, fails here rather than in production.
    """

    def test_every_locale_has_a_catalog(self) -> None:
        for code in i18n.SUPPORTED_LOCALES:
            assert (i18n.CATALOG_DIR / f"{code}.json").is_file(), f"missing catalog for {code}"

    def test_no_locale_is_missing_or_inventing_keys(self) -> None:
        english = set(i18n.load_catalog("en"))
        for code in i18n.SUPPORTED_LOCALES:
            if code == "en":
                continue
            keys = set(i18n.load_catalog(code))
            # Plural keys legitimately differ: Polish needs `_few`/`_many`,
            # Japanese needs only `_other`. Compare on the base key instead.
            assert self._bases(keys) == self._bases(english), f"{code} key set diverges from en"

    def test_plural_keys_cover_the_locales_categories(self) -> None:
        english = i18n.load_catalog("en")
        plural_bases = {k.rsplit("_", 1)[0] for k in english if k.rsplit("_", 1)[-1] == "one"}
        for code in i18n.SUPPORTED_LOCALES:
            catalog = i18n.load_catalog(code)
            for base in plural_bases:
                # Whatever categories this language uses for 0–200, each must resolve.
                needed = {i18n.plural_category(code, n) for n in range(0, 200)}
                for category in needed:
                    assert f"{base}_{category}" in catalog or f"{base}_other" in catalog, (
                        f"{code}: {base} has no form for category {category}"
                    )

    def test_placeholders_match_english(self) -> None:
        english = i18n.load_catalog("en")
        for code in i18n.SUPPORTED_LOCALES:
            if code == "en":
                continue
            for key, translated in i18n.load_catalog(code).items():
                source = english.get(key) or english.get(f"{key.rsplit('_', 1)[0]}_other")
                if source is None:
                    continue
                assert self._placeholders(translated) <= self._placeholders(source), (
                    f"{code}:{key} interpolates a variable English does not provide"
                )

    def test_catalogs_are_flat_string_maps(self) -> None:
        for code in i18n.SUPPORTED_LOCALES:
            raw = json.loads((i18n.CATALOG_DIR / f"{code}.json").read_text(encoding="utf-8"))
            assert isinstance(raw, dict)
            for key, value in raw.items():
                assert isinstance(value, str), f"{code}:{key} is not a string"

    @staticmethod
    def _bases(keys: set[str]) -> set[str]:
        categories = {"zero", "one", "two", "few", "many", "other"}
        return {k.rsplit("_", 1)[0] if k.rsplit("_", 1)[-1] in categories else k for k in keys}

    @staticmethod
    def _placeholders(text: str) -> set[str]:
        return set(i18n._INTERPOLATION_RE.findall(text))
