"""AI agent configuration models (Wave 2 agent G).

- `Agent` is a configurable "Fin-style" support agent: a model reference, a system
  prompt, retrieval/guardrail settings, and a per-tool policy list.
- `CustomAction` is an HTTP tool the agent can call. Header VALUES are encrypted
  at rest (`encrypt_secret`) and only decrypted at execution time; the host of the
  configured `url` is the only host a templated request may reach.

See docs/CONTRACTS.md → "Wave 2 — Agent G".
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class AgentStatus(enum.StrEnum):
    DRAFT = "draft"
    LIVE = "live"
    OFF = "off"


class Agent(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "agents"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    avatar_emoji: Mapped[str | None] = mapped_column(String(16))
    # "draft" | "live" | "off" — only "live" agents auto-answer conversations.
    status: Mapped[str] = mapped_column(String(10), default=AgentStatus.DRAFT, nullable=False)
    # "provider_id:model_key" or "mock"; null → workspace default chat model → mock.
    model_ref: Mapped[str | None] = mapped_column(String(300))
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    temperature: Mapped[float | None] = mapped_column()
    # {retrieval: {enabled, k, source_ids}, handoff_message, guardrails:
    #  {max_tool_calls, require_citations}}
    settings: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # [{"key": "<builtin>"|"action:<id>", "policy": "auto"|"require_approval"|"disabled"}]
    tools: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)


class CustomAction(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "custom_actions"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(80), nullable=False)  # slug-ish, exposed to the LLM
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    method: Mapped[str] = mapped_column(String(10), default="POST", nullable=False)
    # May contain {param} templates; its host is the ONLY host a request may reach.
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    # {header_name: encrypted_value} — decrypted only at execution time.
    headers: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # JSON body with {param} placeholders (sent verbatim after substitution).
    body_template: Mapped[str | None] = mapped_column(Text)
    # JSON Schema (object) exposed to the LLM + validated manually before calling.
    params_schema: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, nullable=False
    )
    timeout_s: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
