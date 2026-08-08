"""Origin/Referer check for the handful of endpoints that authenticate by cookie.

Almost every route here authenticates with a Bearer access token the SPA holds in
memory, and a token an attacker's page cannot read is a token it cannot send —
those routes have no CSRF exposure at all. The exceptions are the refresh-cookie
routes (``/auth/refresh``, ``/auth/logout``), which the browser authenticates for
you, and that is exactly the shape CSRF exploits.

``SameSite=Lax`` on the cookie already blocks the cross-site POST that would
carry it, so this is defence in depth rather than the only lock. It is worth
having because SameSite is a browser-side promise: it does nothing for a stale
browser that ignores the attribute, and nothing for a same-site attacker (a
sibling subdomain is "same site" to a cookie but a different origin here). An
explicit origin check is enforced by us, on our side, and is testable.

Deliberately *not* applied to the OAuth callbacks: those are top-level redirects
arriving from accounts.google.com and github.com, so a foreign Origin is correct
there. They carry their own defence — a nonce cookie tied to the flow that
started in this browser (see ``api/v1/auth_oauth.py``).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import ForbiddenError
from app.core.logging import log

logger = log("csrf")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def allowed_origins() -> set[str]:
    """Where our own front-ends live: the dashboard, the API itself (same-origin
    XHR in the single-domain deployment) and any extra CORS origins configured."""
    settings = get_settings()
    origins = {
        _origin_of(settings.app_base_url),
        _origin_of(settings.public_base_url),
    }
    for extra in settings.cors_origins:
        origins.add(_origin_of(extra))
    return {origin for origin in origins if origin}


def check_origin(request: Request) -> None:
    """Reject a state-changing cookie-authenticated request from a foreign page.

    A missing Origin *and* Referer is treated as trusted: non-browser clients
    (curl, the CLI, server-to-server) send neither, and they are not the threat
    model — CSRF needs a browser that attaches the cookie for you. Browsers have
    sent Origin on cross-origin POSTs for years, so the header being absent is
    itself evidence the request did not come from another site's page.
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        origin = _origin_of(referer) if referer else None
    if origin is None:
        return
    permitted = allowed_origins()
    # The request's own origin counts: same-origin XHR is never CSRF, and it
    # keeps this working behind whatever hostname the deployment actually uses.
    permitted.add(f"{request.url.scheme}://{request.url.netloc}")
    if origin not in permitted:
        logger.warning(
            "blocked cross-origin %s %s from %s", request.method, request.url.path, origin
        )
        raise ForbiddenError("Cross-origin request rejected")
