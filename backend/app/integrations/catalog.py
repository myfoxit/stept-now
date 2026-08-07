"""Provider catalog: the declarative integrations registry (apps.yml pattern).

Everything provider-specific that the OAuth core needs is data on the spec —
adding a provider is a new ``ProviderSpec`` entry, never an edit to the core.
Endpoint/scope values follow docs/INTEGRATIONS-CONTRACTS.md "Provider cheat
sheet" (validated against provider docs, 2026-08).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

# Pinned Notion API version (knowledge connectors import this so all Notion
# calls agree on one version).
NOTION_VERSION = "2025-09-03"


@dataclass(frozen=True)
class ProviderSpec:
    id: str  # "google" | "microsoft" | "slack" | "notion" | "confluence" | ...
    name: str  # "Google (Gmail & Drive)"
    category: str  # "email" | "knowledge" | "channel" | "app"
    auth: str  # "oauth2" | "token" | "none"
    description: str
    doc_slug: str  # anchor into docs/INTEGRATIONS-SETUP.md
    authorize_url: str = ""  # base authorize endpoint (oauth2 only)
    token_url: str = ""
    scopes: tuple[str, ...] = ()
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)
    credential_fields: tuple[str, ...] = ("client_id", "client_secret")  # operator-supplied
    uses_pkce: bool = False
    # How the scope param is joined ("," for Slack's CSV, " " for everyone else).
    scope_separator: str = " "
    # Token-endpoint auth style: "form" posts client_id/client_secret fields;
    # "basic" (Notion) sends HTTP Basic + a JSON body.
    token_auth: str = "form"


PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec(
            id="google",
            name="Google (Gmail & Drive)",
            category="email",
            auth="oauth2",
            description=(
                "Connect a Google account for two-way Gmail support email and "
                "read-only Google Drive knowledge sync."
            ),
            doc_slug="google",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            scopes=(
                "https://mail.google.com/",
                "https://www.googleapis.com/auth/drive.readonly",
                "openid",
                "email",
            ),
            # offline + consent forces a refresh token on every authorize.
            extra_authorize_params={"access_type": "offline", "prompt": "consent"},
        ),
        ProviderSpec(
            id="microsoft",
            name="Microsoft 365",
            category="email",
            auth="oauth2",
            description=(
                "Connect a Microsoft 365 mailbox for two-way support email over IMAP and SMTP."
            ),
            doc_slug="microsoft",
            authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            scopes=(
                "offline_access",
                "openid",
                "email",
                "https://outlook.office365.com/IMAP.AccessAsUser.All",
                "https://outlook.office365.com/SMTP.Send",
            ),
            extra_authorize_params={"prompt": "select_account"},
        ),
        ProviderSpec(
            id="slack",
            name="Slack",
            category="channel",
            auth="oauth2",
            description=(
                "One-click Slack install: turn Slack threads into support "
                "conversations and reply from the inbox."
            ),
            doc_slug="slack",
            authorize_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",
            scopes=(
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
            ),
            credential_fields=("client_id", "client_secret", "signing_secret"),
            scope_separator=",",
        ),
        ProviderSpec(
            id="notion",
            name="Notion",
            category="knowledge",
            auth="oauth2",
            description="Sync pages the integration is shared with into your knowledge base.",
            doc_slug="notion",
            authorize_url="https://api.notion.com/v1/oauth/authorize",
            token_url="https://api.notion.com/v1/oauth/token",
            scopes=(),  # Notion public OAuth has no scope param
            extra_authorize_params={"owner": "user"},
            token_auth="basic",
        ),
        ProviderSpec(
            id="confluence",
            name="Confluence",
            category="knowledge",
            auth="oauth2",
            description="Sync Confluence Cloud spaces into your knowledge base (Atlassian 3LO).",
            doc_slug="confluence",
            authorize_url="https://auth.atlassian.com/authorize",
            token_url="https://auth.atlassian.com/oauth/token",
            scopes=(
                "read:confluence-content.all",
                "read:confluence-space.summary",
                "search:confluence",
                "offline_access",
            ),
            extra_authorize_params={"audience": "api.atlassian.com", "prompt": "consent"},
        ),
        ProviderSpec(
            id="zendesk",
            name="Zendesk",
            category="knowledge",
            auth="token",
            description=(
                "Import your Zendesk help-center articles as a knowledge source "
                "(configured with an API token when adding the source)."
            ),
            doc_slug="zendesk",
            credential_fields=(),
        ),
    )
}
