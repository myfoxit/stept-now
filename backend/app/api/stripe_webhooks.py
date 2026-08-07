"""Stripe webhook receiver.

Mounted UNAUTHENTICATED at /api/stripe (alongside /api/channels and
/api/integrations): authenticity comes from the Stripe-Signature HMAC over the
RAW request body, so this handler reads ``request.body()`` itself and must
never sit behind a parsed-body dependency. Any verification failure is a 400 —
Stripe treats non-2xx as "retry later", which is exactly right here.
"""

from __future__ import annotations

import json
from typing import Any

import stripe
from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.deps import Db
from app.core.errors import BadRequestError
from app.core.logging import log
from app.services import billing as billing_service

logger = log("stripe_webhooks")

router = APIRouter()


@router.post("/webhook")
async def stripe_webhook(request: Request, session: Db) -> dict[str, Any]:
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise BadRequestError("Stripe webhooks are not configured on this instance")
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        # Never echo the signature or payload — just the fact it failed.
        logger.warning("rejected Stripe webhook: %s", type(exc).__name__)
        raise BadRequestError("Invalid Stripe webhook signature") from exc
    # construct_event verified both signature and JSON; re-parse to plain dicts
    # so the service layer stays independent of Stripe object classes.
    event: dict[str, Any] = json.loads(payload)
    await billing_service.apply_webhook_event(session, event)
    return {"received": True}
