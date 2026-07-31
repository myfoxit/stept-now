"""Inbound channel webhook registry (ORCHESTRATOR-OWNED).

Mounted at /api/channels. Server-to-server endpoints (Slack events, Telegram
webhooks, inbound email posts, Meta/Twilio/LINE webhooks) — authenticated
per-channel, no CORS needed.
"""

from fastapi import APIRouter

from app.api.channels import email, line, messenger, slack, sms, telegram, whatsapp

channels_router = APIRouter()
channels_router.include_router(email.router, prefix="/email", tags=["channels"])
channels_router.include_router(slack.router, prefix="/slack", tags=["channels"])
channels_router.include_router(telegram.router, prefix="/telegram", tags=["channels"])
channels_router.include_router(whatsapp.router, prefix="/whatsapp", tags=["channels"])
channels_router.include_router(messenger.router, prefix="/messenger", tags=["channels"])
channels_router.include_router(messenger.instagram_router, prefix="/instagram", tags=["channels"])
channels_router.include_router(sms.router, prefix="/sms", tags=["channels"])
channels_router.include_router(line.router, prefix="/line", tags=["channels"])
