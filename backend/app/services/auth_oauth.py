"""Social login (Google, GitHub): OAuth for *user identity*, not integrations.

Deliberately separate from ``app.integrations``: a different state token ``typ``
("login_state" vs "integration_state") so a state minted for one flow can never
be replayed into the other, instance-level credentials only (no per-workspace
overrides), and a different outcome — a User session instead of an
IntegrationConnection. The state/exchange idioms mirror
``app.integrations.oauth`` on purpose; keep the two in stylistic lockstep.

Account resolution:
- (provider, provider_user_id) already linked        → log that user in.
- provider-verified email matches an existing user   → link a new identity.
- provider-verified email, no user                   → create a passwordless user.
- email missing or unverified at the provider        → "email_unverified" error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import get_settings
from app.core.db import utcnow, uuid7
from app.core.errors import UnauthorizedError
from app.core.logging import log
from app.models.user import User, UserIdentity

logger = log("auth_oauth")

STATE_TTL = timedelta(minutes=15)
# Distinct `typ` keeps login states disjoint from every other Stept token —
# most importantly from "integration_state", so a workspace-integration state
# can never be replayed as a login (decode_token pins the typ claim).
_STATE_TOKEN_TYPE = cast("security.TokenType", "login_state")

HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


class SocialLoginError(UnauthorizedError):
    """A social-login failure. The callback turns it into a 302 back to
    ``/login?error=<redirect_code>`` — the message never reaches the browser."""

    code = "social_login_failed"

    def __init__(self, redirect_code: str = "oauth_failed", message: str | None = None):
        super().__init__(message or "Social login failed")
        self.redirect_code = redirect_code


# ---------------------------------------------------------------------------
# provider specs + credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoginProviderSpec:
    id: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    # Extra headers for the token exchange (GitHub answers urlencoded unless
    # asked for JSON explicitly).
    token_headers: dict[str, str] = field(default_factory=dict)


LOGIN_PROVIDERS: dict[str, LoginProviderSpec] = {
    spec.id: spec
    for spec in (
        LoginProviderSpec(
            id="google",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            # Identity only — no offline access, no data scopes.
            scopes=("openid", "email", "profile"),
        ),
        LoginProviderSpec(
            id="github",
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            scopes=("read:user", "user:email"),
            token_headers={"Accept": "application/json"},
        ),
    )
}


@dataclass(frozen=True)
class ProviderIdentity:
    """What a provider asserted about the person who just authorized."""

    provider: str
    provider_user_id: str
    email: str | None
    email_verified: bool
    name: str | None


def resolve_url(provider_id: str, url: str) -> str:
    """Apply ``settings.oauth_base_override`` (test/dev stub) to a provider URL —
    same idiom as ``app.integrations.oauth.resolve_url``."""
    override = get_settings().oauth_base_override.get(provider_id)
    if not override:
        return url
    return override.rstrip("/") + urlsplit(url).path


def redirect_uri_for(provider_id: str) -> str:
    """The exact redirect URI operators register in the provider console."""
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/api/v1/auth/oauth/{provider_id}/callback"


def resolve_login_credential(provider_id: str) -> tuple[str, str] | None:
    """(client_id, client_secret) for a login provider, or None (not configured).

    Google prefers the dedicated login app (``google_login_client_id``) and
    falls back to the integrations app *pair-wise* — a half-configured login
    pair never mixes with the integration secret.
    """
    settings = get_settings()
    if provider_id == "google":
        if settings.google_login_client_id and settings.google_login_client_secret:
            return settings.google_login_client_id, settings.google_login_client_secret
        if settings.google_client_id and settings.google_client_secret:
            return settings.google_client_id, settings.google_client_secret
        return None
    if provider_id == "github":
        if settings.github_client_id and settings.github_client_secret:
            return settings.github_client_id, settings.github_client_secret
        return None
    return None


def configured_providers() -> list[str]:
    return [pid for pid in LOGIN_PROVIDERS if resolve_login_credential(pid) is not None]


# ---------------------------------------------------------------------------
# signed state
# ---------------------------------------------------------------------------


def safe_next_path(value: object) -> str:
    """An in-app path or "/" — never an absolute/protocol-relative URL."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def mint_login_state(
    *, provider: str, next_path: str | None = None, invite_token: str | None = None
) -> str:
    claims: dict[str, Any] = {"provider": provider, "nonce": uuid7()}
    if next_path:
        claims["next"] = safe_next_path(next_path)
    if invite_token:
        claims["invite"] = invite_token
    return security._encode(claims, STATE_TTL, _STATE_TOKEN_TYPE)


def verify_login_state(state: str) -> dict[str, Any]:
    """Decode + validate a login state JWT; raises SocialLoginError when unusable."""
    try:
        claims = security.decode_token(state, _STATE_TOKEN_TYPE)
    except UnauthorizedError as exc:
        raise SocialLoginError("oauth_failed", "Invalid or expired login state") from exc
    if not claims.get("provider"):
        raise SocialLoginError("oauth_failed", "Invalid or expired login state")
    return claims


# ---------------------------------------------------------------------------
# authorize URL + code exchange
# ---------------------------------------------------------------------------


def build_authorize_url(
    spec: LoginProviderSpec, client_id: str, *, state: str, redirect_uri: str
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(spec.scopes),
        "state": state,
    }
    base = resolve_url(spec.id, spec.authorize_url)
    return base + "?" + urlencode(params, quote_via=quote)


async def exchange_code(
    spec: LoginProviderSpec,
    credential: tuple[str, str],
    *,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Authorization-code exchange. Raises SocialLoginError on any failure shape.

    GitHub answers HTTP 200 with ``{"error": ...}`` for a bad code — treated as
    a failure like any 4xx. Bodies never end up in error messages (they can
    echo credentials).
    """
    client_id, client_secret = credential
    token_url = resolve_url(spec.id, spec.token_url)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers=spec.token_headers,
            )
    except httpx.HTTPError as exc:
        raise SocialLoginError("oauth_failed", f"{spec.id}: token endpoint unreachable") from exc
    if response.status_code >= 400:
        raise SocialLoginError(
            "oauth_failed", f"{spec.id}: token endpoint returned HTTP {response.status_code}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise SocialLoginError(
            "oauth_failed", f"{spec.id}: token endpoint returned non-JSON"
        ) from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise SocialLoginError("oauth_failed", f"{spec.id}: token endpoint response unusable")
    return data


# ---------------------------------------------------------------------------
# identity per provider
# ---------------------------------------------------------------------------


def _google_identity(token_data: dict[str, Any]) -> ProviderIdentity:
    """Identity from the OIDC id_token, decoded WITHOUT signature verification.

    Deliberate (same argument as app.integrations.oauth._id_token_claims): the
    id_token arrived directly from Google's token endpoint over TLS in the same
    response as the access token — there is no untrusted hop to defend against,
    so fetching JWKS would add a network dependency for zero security. This
    shortcut is ONLY valid for the server-side code flow; never reuse it for an
    id_token accepted from a browser.
    """
    id_token = token_data.get("id_token")
    if not id_token:
        raise SocialLoginError("oauth_failed", "google: token response carried no id_token")
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise SocialLoginError("oauth_failed", "google: id_token undecodable") from exc
    if not isinstance(claims, dict) or not claims.get("sub"):
        raise SocialLoginError("oauth_failed", "google: id_token missing sub")
    email = claims.get("email")
    # Google may serialize email_verified as bool or string.
    verified = claims.get("email_verified") in (True, "true")
    name = claims.get("name")
    return ProviderIdentity(
        provider="google",
        provider_user_id=str(claims["sub"]),
        email=str(email) if email else None,
        email_verified=bool(email) and verified,
        name=str(name) if name else None,
    )


async def _github_identity(token_data: dict[str, Any]) -> ProviderIdentity:
    """GitHub has no id_token: GET /user for the account, GET /user/emails for a
    provider-verified address (primary+verified preferred, any verified next)."""
    headers = {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Accept": "application/vnd.github+json",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            user_response = await client.get(
                resolve_url("github", GITHUB_USER_URL), headers=headers
            )
            emails_response = await client.get(
                resolve_url("github", GITHUB_EMAILS_URL), headers=headers
            )
    except httpx.HTTPError as exc:
        raise SocialLoginError("oauth_failed", "github: user API unreachable") from exc
    if user_response.status_code >= 400:
        raise SocialLoginError(
            "oauth_failed", f"github: /user returned HTTP {user_response.status_code}"
        )
    account = user_response.json()
    if not isinstance(account, dict) or account.get("id") is None:
        raise SocialLoginError("oauth_failed", "github: /user response unusable")

    email: str | None = None
    verified = False
    if emails_response.status_code < 400:
        emails = emails_response.json()
        if isinstance(emails, list):
            rows = [
                e for e in emails if isinstance(e, dict) and e.get("verified") and e.get("email")
            ]
            primary = next((e for e in rows if e.get("primary")), None)
            chosen = primary or (rows[0] if rows else None)
            if chosen:
                email = str(chosen["email"])
                verified = True

    name = account.get("name") or account.get("login")
    return ProviderIdentity(
        provider="github",
        provider_user_id=str(account["id"]),
        email=email,
        email_verified=verified,
        name=str(name) if name else None,
    )


async def fetch_identity(spec: LoginProviderSpec, token_data: dict[str, Any]) -> ProviderIdentity:
    if spec.id == "google":
        return _google_identity(token_data)
    if spec.id == "github":
        return await _github_identity(token_data)
    raise SocialLoginError("oauth_failed", f"{spec.id}: no identity handler")


# ---------------------------------------------------------------------------
# account resolution
# ---------------------------------------------------------------------------


async def resolve_user(session: AsyncSession, identity: ProviderIdentity) -> User:
    """Identity → User: existing link wins; else link-by-verified-email or create."""
    linked = (
        await session.execute(
            select(UserIdentity).where(
                UserIdentity.provider == identity.provider,
                UserIdentity.provider_user_id == identity.provider_user_id,
            )
        )
    ).scalar_one_or_none()
    if linked is not None:
        user = await session.get(User, linked.user_id)
        if user is None:  # unreachable with the FK cascade; belt and braces
            raise SocialLoginError("oauth_failed", "identity points at a missing user")
        user.last_seen_at = utcnow()
        return user

    # First time we see this provider account: only a provider-VERIFIED email
    # may claim (or create) a Stept account, otherwise anyone could register an
    # unverified address at the provider and take over the matching user here.
    if not identity.email or not identity.email_verified:
        raise SocialLoginError("email_unverified", f"{identity.provider}: no verified email")
    email = identity.email.strip().lower()

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            name=(identity.name or email.split("@", 1)[0]).strip()[:200],
            password_hash=None,
        )
        session.add(user)
        await session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=identity.provider,
            provider_user_id=identity.provider_user_id,
            email=email,
        )
    )
    user.last_seen_at = utcnow()
    await session.flush()
    return user
