"""Catalog registry: exact endpoints/scopes per the provider cheat sheet."""

from __future__ import annotations

from app.integrations.catalog import PROVIDERS


def test_registry_has_exactly_the_wave_providers():
    assert set(PROVIDERS) == {"google", "microsoft", "slack", "notion", "confluence", "zendesk"}
    for provider_id, spec in PROVIDERS.items():
        assert spec.id == provider_id
        assert spec.category in {"email", "knowledge", "channel", "app"}
        assert spec.auth in {"oauth2", "token", "none"}
        assert spec.description and spec.doc_slug


def test_google_spec():
    spec = PROVIDERS["google"]
    assert spec.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert spec.token_url == "https://oauth2.googleapis.com/token"
    assert spec.scopes == (
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/drive.readonly",
        "openid",
        "email",
    )
    assert spec.extra_authorize_params == {"access_type": "offline", "prompt": "consent"}
    assert spec.category == "email"


def test_microsoft_spec():
    spec = PROVIDERS["microsoft"]
    assert spec.authorize_url == "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    assert spec.token_url == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    assert spec.scopes == (
        "offline_access",
        "openid",
        "email",
        "https://outlook.office365.com/IMAP.AccessAsUser.All",
        "https://outlook.office365.com/SMTP.Send",
    )
    assert spec.extra_authorize_params == {"prompt": "select_account"}


def test_slack_spec():
    spec = PROVIDERS["slack"]
    assert spec.authorize_url == "https://slack.com/oauth/v2/authorize"
    assert spec.token_url == "https://slack.com/api/oauth.v2.access"
    assert spec.scopes == (
        "channels:history",
        "channels:read",
        "chat:write",
        "groups:history",
        "groups:read",
        "im:history",
        "im:read",
        "im:write",
        "users:read",
        "team:read",
    )
    assert spec.scope_separator == ","  # Slack wants CSV
    assert spec.credential_fields == ("client_id", "client_secret", "signing_secret")
    assert spec.category == "channel"


def test_notion_spec():
    spec = PROVIDERS["notion"]
    assert spec.authorize_url == "https://api.notion.com/v1/oauth/authorize"
    assert spec.token_url == "https://api.notion.com/v1/oauth/token"
    assert spec.scopes == ()
    assert spec.extra_authorize_params == {"owner": "user"}
    assert spec.token_auth == "basic"


def test_confluence_spec():
    spec = PROVIDERS["confluence"]
    assert spec.authorize_url == "https://auth.atlassian.com/authorize"
    assert spec.token_url == "https://auth.atlassian.com/oauth/token"
    assert spec.scopes == (
        "read:confluence-content.all",
        "read:confluence-space.summary",
        "search:confluence",
        "offline_access",
    )
    assert spec.extra_authorize_params == {"audience": "api.atlassian.com", "prompt": "consent"}


def test_zendesk_is_token_auth_only():
    spec = PROVIDERS["zendesk"]
    assert spec.auth == "token"
    assert spec.authorize_url == "" and spec.token_url == ""
    assert spec.credential_fields == ()
    assert spec.category == "knowledge"
