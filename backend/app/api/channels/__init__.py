"""Inbound channel webhook registry (ORCHESTRATOR-OWNED).

Mounted at /api/channels. Server-to-server endpoints (Slack events, Telegram
webhooks, inbound email posts) — authenticated per-channel, no CORS needed.
"""

from fastapi import APIRouter

from app.api.channels import email, slack, telegram

channels_router = APIRouter()
channels_router.include_router(email.router, prefix="/email", tags=["channels"])
channels_router.include_router(slack.router, prefix="/slack", tags=["channels"])
channels_router.include_router(telegram.router, prefix="/telegram", tags=["channels"])
