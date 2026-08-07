"""OAuth core: signed state, authorize URLs, code exchange, provider whoami.

State is a compact JWT minted with the same helpers that sign every other Stept
token (``app.core.security``): 15-minute expiry, ``purpose`` claim pinned to
"integrations.connect" so it can never be replayed as anything else. Provider
base URLs pass through :func:`resolve_url` so ``settings.oauth_base_override``
can point a whole provider at a local stub for live round-trip verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import get_settings
from app.core.db import uuid7
from app.core.errors import ConflictError, UnauthorizedError
from app.integrations.catalog import PROVIDERS, ProviderSpec
from app.models.integration import IntegrationAppCredential

STATE_TTL = timedelta(minutes=15)
STATE_PURPOSE = "integrations.connect"
# Distinct `typ` keeps state JWTs disjoint from access/refresh/widget tokens.
# The literal lives here (not in security.TokenType) because only this module
# ever mints or accepts it; the cast lets us reuse the shared encode/decode.
_STATE_TOKEN_TYPE = cast("security.TokenType", "integration_state")

HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ATLASSIAN_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"


class IntegrationAuthError(ConflictError):
    """An OAuth exchange/refresh the user must fix by reauthorizing (409)."""

    code = "integration_auth_error"


def resolve_url(provider_id: str, url: str) -> str:
    """Apply ``settings.oauth_base_override`` (test/dev stub) to a provider URL."""
    override = get_settings().oauth_base_override.get(provider_id)
    if not override:
        return url
    return override.rstrip("/") + urlsplit(url).path


def redirect_uri_for(provider_id: str) -> str:
    """The exact redirect URI operators register in the provider console."""
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/api/integrations/oauth/{provider_id}/callback"


# ---------------------------------------------------------------------------
# credential resolution: workspace row → instance env
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedCredential:
    client_id: str
    client_secret: str
    extra: dict[str, str]
    from_env: bool


async def resolve_credential(
    session: AsyncSession, workspace_id: str, provider_id: str
) -> ResolvedCredential | None:
    """Usable OAuth client for a provider, or None (provider "needs setup").

    A workspace row always wins over env — even a row missing its secret does
    not fall through (the operator deliberately replaced the instance app and
    must finish the form), it just resolves to None.
    """
    row = (
        await session.execute(
            select(IntegrationAppCredential).where(
                IntegrationAppCredential.workspace_id == workspace_id,
                IntegrationAppCredential.provider == provider_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        if not row.client_secret_encrypted:
            return None
        return ResolvedCredential(
            client_id=row.client_id,
            client_secret=security.decrypt_secret(row.client_secret_encrypted),
            extra={str(k): str(v) for k, v in dict(row.extra or {}).items()},
            from_env=False,
        )
    settings = get_settings()
    client_id = getattr(settings, f"{provider_id}_client_id", None)
    client_secret = getattr(settings, f"{provider_id}_client_secret", None)
    if not client_id or not client_secret:
        return None
    extra: dict[str, str] = {}
    if provider_id == "slack" and settings.slack_signing_secret:
        extra["signing_secret"] = settings.slack_signing_secret
    return ResolvedCredential(
        client_id=client_id, client_secret=client_secret, extra=extra, from_env=True
    )


# ---------------------------------------------------------------------------
# signed state
# ---------------------------------------------------------------------------


def mint_state(
    *,
    workspace_id: str,
    provider: str,
    user_id: str | None,
    return_to: str,
    connection_id: str | None = None,
) -> str:
    claims: dict[str, Any] = {
        "ws": workspace_id,
        "provider": provider,
        "purpose": STATE_PURPOSE,
        "nonce": uuid7(),
        "return_to": return_to,
        "sub": user_id,
    }
    if connection_id:
        claims["connection_id"] = connection_id
    return security._encode(claims, STATE_TTL, _STATE_TOKEN_TYPE)


def verify_state(state: str) -> dict[str, Any]:
    """Decode + validate a state JWT; raises IntegrationAuthError when unusable."""
    try:
        claims = security.decode_token(state, _STATE_TOKEN_TYPE)
    except UnauthorizedError as exc:
        raise IntegrationAuthError("Invalid or expired OAuth state") from exc
    if claims.get("purpose") != STATE_PURPOSE or not claims.get("ws") or not claims.get("provider"):
        raise IntegrationAuthError("Invalid or expired OAuth state")
    return claims


# ---------------------------------------------------------------------------
# authorize URL + code exchange
# ---------------------------------------------------------------------------


def build_authorize_url(
    spec: ProviderSpec,
    credential: ResolvedCredential,
    *,
    state: str,
    redirect_uri: str,
) -> str:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": credential.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if spec.scopes:
        params["scope"] = spec.scope_separator.join(spec.scopes)
    params.update(spec.extra_authorize_params)
    base = resolve_url(spec.id, spec.authorize_url)
    return base + "?" + urlencode(params, quote_via=quote)


def parse_token_response(provider_id: str, response: httpx.Response) -> dict[str, Any]:
    """Normalize a token-endpoint response; raises on any shape of failure.

    Slack answers HTTP 200 with ``{"ok": false, "error": ...}`` — treated as a
    failure like any 4xx. Bodies are never included in error messages (they can
    echo credentials).
    """
    if response.status_code >= 400:
        raise IntegrationAuthError(
            f"{provider_id}: token endpoint returned HTTP {response.status_code}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise IntegrationAuthError(f"{provider_id}: token endpoint returned non-JSON") from exc
    if not isinstance(data, dict) or data.get("ok") is False or not data.get("access_token"):
        raise IntegrationAuthError(f"{provider_id}: token endpoint response unusable")
    return data


async def exchange_code(
    spec: ProviderSpec,
    credential: ResolvedCredential,
    *,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Authorization-code exchange. Notion wants HTTP Basic + JSON; the rest form."""
    token_url = resolve_url(spec.id, spec.token_url)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            if spec.token_auth == "basic":
                response = await client.post(
                    token_url,
                    json={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    auth=(credential.client_id, credential.client_secret),
                )
            else:
                response = await client.post(
                    token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": credential.client_id,
                        "client_secret": credential.client_secret,
                    },
                )
    except httpx.HTTPError as exc:
        raise IntegrationAuthError(f"{spec.id}: token endpoint unreachable") from exc
    return parse_token_response(spec.id, response)


# ---------------------------------------------------------------------------
# whoami — account_label + routing meta per provider
# ---------------------------------------------------------------------------


def _id_token_claims(token_data: dict[str, Any]) -> dict[str, Any]:
    """Claims from the OIDC id_token, decoded WITHOUT signature verification.

    Deliberate: the id_token arrived directly from the provider's token
    endpoint over TLS in the same response as the access token — there is no
    untrusted hop to defend against, so fetching JWKS here would add a network
    dependency for zero security.
    """
    id_token = token_data.get("id_token")
    if not id_token:
        return {}
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}
    return claims if isinstance(claims, dict) else {}


async def _whoami_google(
    spec: ProviderSpec, token_data: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    claims = _id_token_claims(token_data)
    email = claims.get("email")
    return (str(email) if email else None), {}


async def _whoami_microsoft(
    spec: ProviderSpec, token_data: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    # preferred_username/upn is the UPN — the identity SMTP/IMAP logins need.
    claims = _id_token_claims(token_data)
    label = claims.get("preferred_username") or claims.get("upn")
    meta: dict[str, Any] = {}
    if claims.get("tid"):
        meta["tenant"] = claims["tid"]
    return (str(label) if label else None), meta


async def _whoami_slack(
    spec: ProviderSpec, token_data: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    team = token_data.get("team") or {}
    meta = {"team_id": team.get("id"), "bot_user_id": token_data.get("bot_user_id")}
    name = team.get("name")
    return (str(name) if name else None), meta


async def _whoami_notion(
    spec: ProviderSpec, token_data: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    name = token_data.get("workspace_name")
    meta = {"bot_id": token_data.get("bot_id"), "workspace_name": name}
    return (str(name) if name else None), meta


async def _whoami_confluence(
    spec: ProviderSpec, token_data: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    """Atlassian 3LO routes through a cloud id; one site pins it, several defer
    the choice to the knowledge-source config (meta.sites)."""
    url = resolve_url(spec.id, ATLASSIAN_RESOURCES_URL)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token_data['access_token']}"}
            )
    except httpx.HTTPError as exc:
        raise IntegrationAuthError("confluence: accessible-resources unreachable") from exc
    if response.status_code >= 400:
        raise IntegrationAuthError(
            f"confluence: accessible-resources returned HTTP {response.status_code}"
        )
    sites = response.json()
    if not isinstance(sites, list):
        raise IntegrationAuthError("confluence: accessible-resources response unusable")
    if len(sites) == 1:
        site = sites[0]
        label = site.get("name") or site.get("url")
        return (
            (str(label) if label else None),
            {"cloud_id": site.get("id"), "site_url": site.get("url")},
        )
    return (
        f"{len(sites)} sites",
        {
            "sites": [
                {"cloud_id": s.get("id"), "site_url": s.get("url"), "name": s.get("name")}
                for s in sites
            ]
        },
    )


_WHOAMI: dict[str, Any] = {
    "google": _whoami_google,
    "microsoft": _whoami_microsoft,
    "slack": _whoami_slack,
    "notion": _whoami_notion,
    "confluence": _whoami_confluence,
}


async def whoami(
    spec: ProviderSpec, token_data: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    """(account_label, meta) for a fresh token response. Default: id_token email."""
    handler = _WHOAMI.get(spec.id, _whoami_google)
    result: tuple[str | None, dict[str, Any]] = await handler(spec, token_data)
    return result


def spec_for(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)
