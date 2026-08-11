"""Language detection for widget UI locale and article routing."""

from __future__ import annotations

import pytest

from app.services.language import detect_conversation_language, detect_language

#: One realistic support message per shipped language. These are the sentences
#: a visitor actually types into a chat widget, not textbook prose.
SUPPORT_MESSAGES = {
    "en": "Hi, I can't log in to my account. How do I reset my password?",
    "de": "Hallo, ich kann mich nicht in mein Konto einloggen. "
    "Wie kann ich mein Passwort zurücksetzen?",
    "fr": "Bonjour, je n'arrive pas à me connecter à mon compte. "
    "Comment puis-je réinitialiser mon mot de passe ?",
    "es": "Hola, no puedo iniciar sesión en mi cuenta. ¿Cómo puedo restablecer mi contraseña?",
    "pt-BR": "Olá, não consigo entrar na minha conta. Como faço para redefinir a minha senha?",
    "it": "Ciao, non riesco ad accedere al mio account. Come posso reimpostare la mia password?",
    "nl": "Hallo, ik kan niet inloggen op mijn account. "
    "Hoe kan ik mijn wachtwoord opnieuw instellen?",
    "pl": "Cześć, nie mogę zalogować się na swoje konto. Jak mogę zresetować moje hasło?",
    "tr": "Merhaba, hesabıma giriş yapamıyorum. Şifremi nasıl sıfırlayabilirim?",
    "ja": "こんにちは、アカウントにログインできません。"
    "パスワードをリセットするにはどうすればいいですか？",
    "ko": "안녕하세요, 계정에 로그인할 수 없습니다. 비밀번호를 재설정하려면 어떻게 해야 하나요?",
    "zh-CN": "你好，我无法登录我的账户。请问怎样重置密码？",
    "ar": "مرحبًا، لا أستطيع تسجيل الدخول إلى حسابي. كيف يمكنني إعادة تعيين كلمة المرور؟",
}

#: A second, structurally different message per language, so the tests are not
#: measuring how well the stopword lists memorised one sentence.
BILLING_MESSAGES = {
    "en": "I was charged twice this month and I would like a refund please.",
    "de": "Mir wurde diesen Monat zweimal etwas berechnet "
    "und ich möchte bitte eine Rückerstattung.",
    "fr": "J'ai été facturé deux fois ce mois-ci et je voudrais un remboursement.",
    "es": "Me han cobrado dos veces este mes y me gustaría un reembolso, gracias.",
    "pt-BR": "Fui cobrado duas vezes neste mês e gostaria de um reembolso, obrigado.",
    "it": "Mi hanno addebitato due volte questo mese e vorrei un rimborso, grazie.",
    "nl": "Ik ben deze maand twee keer in rekening gebracht en ik wil graag mijn geld terug.",
    "pl": "W tym miesiącu obciążono mnie dwa razy i chcę zwrot pieniędzy.",
    "tr": "Bu ay benden iki kez ücret alındı ve para iadesi istiyorum.",
    "ja": "今月2回請求されました。返金をお願いできますか。",
    "ko": "이번 달에 두 번 청구되었습니다. 환불을 받고 싶습니다.",
    "zh-CN": "这个月我被扣了两次费用，我想申请退款。",
    "ar": "تم خصم المبلغ مرتين هذا الشهر وأود استرداد المبلغ من فضلك.",
}


@pytest.mark.parametrize(("expected", "text"), sorted(SUPPORT_MESSAGES.items()))
def test_detects_a_password_reset_question(expected: str, text: str) -> None:
    assert detect_language(text) == expected


@pytest.mark.parametrize(("expected", "text"), sorted(BILLING_MESSAGES.items()))
def test_detects_a_billing_complaint(expected: str, text: str) -> None:
    assert detect_language(text) == expected


class TestScriptDetection:
    """Kana / Hangul / Arabic identify a language on their own."""

    def test_japanese_wins_over_chinese_when_kana_present(self) -> None:
        # Mostly Han characters, one kana — still Japanese.
        assert detect_language("設定を変更") == "ja"

    def test_han_without_kana_is_chinese(self) -> None:
        assert detect_language("我想取消订阅") == "zh-CN"

    def test_short_scripts_still_resolve(self) -> None:
        # Script evidence bypasses the length floor, because two Hangul
        # syllables are already conclusive in a way two Latin words are not.
        assert detect_language("안녕하세요") == "ko"
        assert detect_language("شكرا") == "ar"


class TestRefusesToGuess:
    """The failure mode we care about is confident nonsense, not silence."""

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "ok",
            "hi",
            "yes",
            "?",
            "123456",
            "!!!",
        ],
    )
    def test_returns_none_for_non_evidence(self, text: str) -> None:
        assert detect_language(text) is None

    def test_none_for_empty_input(self) -> None:
        assert detect_language(None) is None

    def test_a_bare_url_is_not_a_language(self) -> None:
        assert detect_language("https://example.com/a/very/long/path/that/looks/wordy") is None

    def test_an_email_address_is_not_a_language(self) -> None:
        assert detect_language("someone.longname@example-company.com") is None

    def test_product_names_alone_stay_undecided(self) -> None:
        # No function words in any language: nothing to go on.
        assert detect_language("Kubernetes Postgres Redis Nginx") is None

    def test_never_returns_an_unshipped_locale(self) -> None:
        for text in [*SUPPORT_MESSAGES.values(), *BILLING_MESSAGES.values(), "svenska text här"]:
            result = detect_language(text)
            assert result is None or result in SUPPORT_MESSAGES


class TestConfusablePairs:
    """The pairs that a naive detector gets wrong."""

    def test_spanish_vs_portuguese(self) -> None:
        assert detect_language("¿Dónde puedo cambiar mi dirección de envío?") == "es"
        assert detect_language("Onde posso alterar o meu endereço de entrega?") == "pt-BR"

    def test_german_vs_dutch(self) -> None:
        assert detect_language("Ich möchte mein Abonnement kündigen, bitte.") == "de"
        assert detect_language("Ik wil graag mijn abonnement opzeggen, alsjeblieft.") == "nl"

    def test_italian_vs_spanish(self) -> None:
        assert detect_language("Vorrei sapere come posso cambiare il mio piano.") == "it"
        assert detect_language("Quiero saber cómo puedo cambiar mi plan.") == "es"


class TestConversationLanguage:
    def test_a_long_german_message_outweighs_an_english_greeting(self) -> None:
        # The classic pattern: greet in English, then switch to your own language.
        texts = [
            "hello",
            "Ich habe ein Problem mit meiner Rechnung und verstehe nicht, "
            "warum mir dieser Betrag berechnet wurde. Können Sie mir bitte helfen?",
        ]
        assert detect_conversation_language(texts) == "de"

    def test_undetectable_messages_are_skipped_not_counted(self) -> None:
        texts = ["ok", "thanks", "Merhaba, siparişimi iptal etmek istiyorum lütfen."]
        assert detect_conversation_language(texts) == "tr"

    def test_none_when_nothing_is_detectable(self) -> None:
        assert detect_conversation_language(["ok", "yes", "👍"]) is None

    def test_empty_history(self) -> None:
        assert detect_conversation_language([]) is None

    def test_later_messages_break_a_tie(self) -> None:
        # Equal-length messages in two languages: the more recent one wins,
        # because that is the language they settled into.
        texts = [
            "Bonjour, je voudrais annuler mon abonnement mensuel s'il vous plaît.",
            "Hallo, ich möchte mein monatliches Abonnement bitte kündigen.",
        ]
        assert detect_conversation_language(texts) == "de"


# --- short, diacritic-free questions (2026-08-11 prod regression) --------------

#: The class of message that broke in production: plain English carries no
#: diacritics, and with no English n-grams configured, "How do I set up an
#: on-call rotation?" scored below MIN_SCORE — the agent then answered a fresh
#: English visitor in the language its persona was written in (German).
SHORT_PLAIN_QUESTIONS = {
    "en": "How do I set up an on-call rotation?",
    "de": "Wie richte ich eine Rufbereitschaft ein?",
    "es": "¿Dónde puedo configurar las alertas?",
    "pl": "Jak skonfigurować alerty?",
}


@pytest.mark.parametrize(("expected", "text"), sorted(SHORT_PLAIN_QUESTIONS.items()))
def test_detects_short_plain_questions(expected: str, text: str) -> None:
    assert detect_language(text) == expected


class TestEnglishEvidence:
    def test_the_dogfood_sentences(self) -> None:
        assert detect_language("How do I set up an on-call rotation?") == "en"
        assert detect_language("What is the difference between a problem and an alert?") == "en"
        assert detect_language("Show me that tour again.") == "en"
        assert detect_language("i cant log in to my account") == "en"

    def test_bare_i_does_not_hijack_italian(self) -> None:
        # "i" is the Italian plural article; the 0.4 short-word weight must not
        # let English steal an Italian sentence. (This sentence was undecided
        # before the English boost — it: 0.7 vs es: 0.7 — and must not now
        # resolve to English on the strength of one collided article.)
        assert detect_language("Ho un problema con i pagamenti e vorrei un rimborso.") != "en"
        italian = "Grazie mille, gli ordini sono arrivati e la fattura è corretta."
        assert detect_language(italian) == "it"

    def test_genuinely_short_stays_undecided(self) -> None:
        assert detect_language("Yes, show me.") is None
