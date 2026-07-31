"""Schemas for the AI agent engine: agents, custom actions, runs, approvals, copilot.

`model_ref` collides with pydantic's protected `model_` namespace, so the agent
schemas disable that guard. Custom-action responses expose only header *names*
(never the encrypted values).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ToolPolicyLiteral = Literal["auto", "require_approval", "disabled"]
AgentStatusLiteral = Literal["draft", "live", "off"]

_NO_PROTECTED_NS = ConfigDict(protected_namespaces=())


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


class ToolConfig(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    policy: ToolPolicyLiteral = "auto"


class RetrievalSettings(BaseModel):
    enabled: bool = True
    k: int = Field(default=6, ge=1, le=20)
    source_ids: list[str] | None = None


class GuardrailSettings(BaseModel):
    max_tool_calls: int = Field(default=8, ge=1, le=30)
    require_citations: bool = False


class AgentSettings(BaseModel):
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    handoff_message: str = "Let me connect you with a teammate who can help."
    guardrails: GuardrailSettings = Field(default_factory=GuardrailSettings)


class AgentCreate(BaseModel):
    model_config = _NO_PROTECTED_NS

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    avatar_emoji: str | None = Field(default=None, max_length=16)
    status: AgentStatusLiteral = "draft"
    model_ref: str | None = Field(default=None, max_length=300)
    system_prompt: str = ""
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    settings: AgentSettings = Field(default_factory=AgentSettings)
    tools: list[ToolConfig] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    model_config = _NO_PROTECTED_NS

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    avatar_emoji: str | None = Field(default=None, max_length=16)
    status: AgentStatusLiteral | None = None
    model_ref: str | None = Field(default=None, max_length=300)
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    settings: AgentSettings | None = None
    tools: list[ToolConfig] | None = None


class AgentOut(BaseModel):
    model_config = _NO_PROTECTED_NS

    id: str
    name: str
    description: str | None
    avatar_emoji: str | None
    status: str
    model_ref: str | None
    system_prompt: str
    temperature: float | None
    settings: dict[str, Any]
    tools: list[Any]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# custom actions
# ---------------------------------------------------------------------------


class CustomActionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = ""
    method: str = Field(default="POST", pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    url: str = Field(min_length=1, max_length=1000)
    headers: dict[str, str] = Field(default_factory=dict)  # write-only (encrypted at rest)
    body_template: str | None = None
    params_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = Field(default=10, ge=1, le=60)


class CustomActionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str | None = None
    method: str | None = Field(default=None, pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    url: str | None = Field(default=None, min_length=1, max_length=1000)
    headers: dict[str, str] | None = None
    body_template: str | None = None
    params_schema: dict[str, Any] | None = None
    timeout_s: int | None = Field(default=None, ge=1, le=60)


class CustomActionOut(BaseModel):
    id: str
    name: str
    description: str
    method: str
    url: str
    header_names: list[str]  # masked — values are never returned
    body_template: str | None
    params_schema: dict[str, Any]
    timeout_s: int
    created_at: datetime
    updated_at: datetime


class ActionTestRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class ActionTestResult(BaseModel):
    ok: bool
    status: int | None = None
    body: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# trace / runs
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    n: int
    title: str
    url: str | None = None
    document_id: str | None = None


class AgentStepOut(BaseModel):
    id: str
    ord: int
    kind: str
    name: str | None
    input: dict[str, Any]
    output: dict[str, Any]
    latency_ms: int | None
    input_tokens: int
    output_tokens: int
    created_at: datetime


class AgentRunOut(BaseModel):
    id: str
    conversation_id: str
    agent_id: str
    agent_name: str | None = None
    trigger_message_id: str | None
    status: str
    error: str | None
    input_tokens: int
    output_tokens: int
    started_at: datetime | None
    finished_at: datetime | None
    reply_message_id: str | None
    created_at: datetime


class AgentRunDetail(BaseModel):
    run: AgentRunOut
    steps: list[AgentStepOut]


# ---------------------------------------------------------------------------
# sandbox test
# ---------------------------------------------------------------------------


class TestHistoryItem(BaseModel):
    role: Literal["user", "assistant", "contact", "agent"] = "user"
    content: str


class AgentTestRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[TestHistoryItem] = Field(default_factory=list)


class AgentTestResult(BaseModel):
    reply: str | None
    status: str
    steps: list[AgentStepOut]
    citations: list[Citation]


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


class ApprovalOut(BaseModel):
    id: str
    run_id: str
    conversation_id: str
    agent_id: str
    agent_name: str | None = None
    tool_key: str
    tool_input: dict[str, Any]
    status: str
    requested_at: datetime
    expires_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    created_at: datetime


class ApprovalDecideRequest(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# copilot
# ---------------------------------------------------------------------------


class CopilotRequest(BaseModel):
    conversation_id: str


class CopilotResult(BaseModel):
    content: str
    citations: list[Citation]
