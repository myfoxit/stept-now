"""Integrations API schemas (W11) — the FE/BE contract for the catalog page.

Secrets are write-only throughout: requests may carry them, responses only ever
say `has_secret`. Connection tokens never appear in any schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderCategory = Literal["email", "knowledge", "channel", "app"]
ProviderAuth = Literal["oauth2", "token", "none"]
ConnectionStatusLiteral = Literal["connected", "reauth_required", "error", "revoked"]


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    status: ConnectionStatusLiteral
    account_label: str | None = None
    scopes: list[str] = []
    meta: dict[str, Any] = {}
    created_at: datetime


class CredentialOut(BaseModel):
    """The operator-visible view of an app credential (secret never echoed)."""

    client_id: str | None = None
    has_secret: bool = False
    # Which fields this provider's credential form needs ("client_id",
    # "client_secret", provider extras like "signing_secret").
    fields: list[str] = []
    # The exact redirect URI to register in the provider console.
    redirect_uri: str
    # True when resolution currently falls back to instance env vars.
    from_env: bool = False


class ProviderOut(BaseModel):
    id: str
    name: str
    category: ProviderCategory
    auth: ProviderAuth
    description: str
    doc_slug: str
    # True when a usable credential exists (workspace row or env) — i.e. the
    # Connect button can work.
    configured: bool
    connections: list[ConnectionOut] = []
    credential: CredentialOut | None = None


class IntegrationsOut(BaseModel):
    providers: list[ProviderOut]


class ConnectIn(BaseModel):
    # In-app path to land on after the OAuth round trip; validated to start
    # with "/" (no absolute URLs — open-redirect guard).
    return_to: str = Field(default="/settings/integrations", max_length=500)


class ConnectOut(BaseModel):
    authorize_url: str


class CredentialIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=320)
    # Omitted/None = keep the stored secret; "" clears it.
    client_secret: str | None = None
    extra: dict[str, str] = {}
