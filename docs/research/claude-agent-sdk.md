# Claude Agent SDK (Python) — Architecture Research for Stept's Agent Engine

Source: `anthropics/claude-agent-sdk-python` @ `f8b9ec92` (2026-07-25), cloned to scratchpad.
Key files: `src/claude_agent_sdk/{types.py,client.py,query.py,__init__.py,_errors.py}`, `src/claude_agent_sdk/_internal/{query.py,message_parser.py,transport/subprocess_cli.py}`, `examples/session_stores/`.

## 1. Architecture map

The SDK is **Claude Code packaged as a library**: it spawns the bundled `claude` CLI as a subprocess (`_internal/transport/subprocess_cli.py:463` → `claude --output-format stream-json --input-format stream-json --verbose`) and speaks NDJSON over stdin/stdout. Two layers ride the same pipe:

1. **Message stream** (CLI → SDK): `user` / `assistant` / `system` / `result` / `stream_event` / `rate_limit_event` frames, parsed into typed dataclasses by `_internal/message_parser.py`.
2. **Control protocol** (bidirectional, `_internal/query.py::Query`): `control_request` / `control_response` / `control_cancel_request` frames. CLI→SDK requests: `can_use_tool`, `hook_callback`, `mcp_message` (in-process MCP routing). SDK→CLI requests: `initialize`, `interrupt`, `set_permission_mode`, `set_model`, `rewind_files`, `mcp_status`, `get_context_usage`, `mcp_reconnect`, `mcp_toggle`, `stop_task`. Every request has a `request_id`; responses are `{subtype: success|error}`; the CLI can cancel an in-flight callback via `control_cancel_request` (`_internal/query.py:305-311`).

Public surface:
- **`query(prompt, options, transport)`** (`query.py`) — one-shot/unidirectional; no interrupts, no follow-ups.
- **`ClaudeSDKClient`** (`client.py`) — bidirectional streaming: `connect() → query() → receive_response()/receive_messages() → interrupt()/set_permission_mode()/set_model() → disconnect()`; async context manager. `can_use_tool` **requires** streaming mode (`client.py:163`).
- **`tool()` + `create_sdk_mcp_server()`** (`__init__.py`) — in-process MCP tools.
- **Session persistence**: CLI writes local JSONL transcripts; optional `SessionStore` protocol (`types.py:1460`) mirrors every transcript line to Postgres/Redis/S3 (reference adapters in `examples/session_stores/`) and `resume` can rematerialize a session from the store before subprocess spawn (`_internal/session_resume.py`). This is how an in-process SDK survives machine loss — highly relevant precedent for Stept.
- **Errors** (`_errors.py`): `ClaudeSDKError` → `CLIConnectionError` → `CLINotFoundError`; `ProcessError(exit_code, stderr)`; `CLIJSONDecodeError`; `MessageParseError`. Process lifecycle: read-task errors are converted to structured stream errors; on `result.is_error` the trailing non-zero exit's `ProcessError` is replaced by the CLI's structured error text (`_internal/query.py:379-396`); `close()` is cancellation-shielded so the subprocess never leaks; an atexit handler kills orphans.

## 2. Options & permission system (the approval-gate state machine)

### ClaudeAgentOptions (`types.py:1759-2126`) — fields that matter for Stept

| Field | Semantics |
|---|---|
| `tools` | Base tool set: `list[str]`, `[]` (none), or `{"type":"preset","preset":"claude_code"}`. Controls *availability* (what's in context). |
| `allowed_tools` | Auto-approved without prompting. An entry can be whole-tool (`"Read"`, `"Read(*)"`) or a specifier (`"Bash(ls:*)"`). Controls *policy*, not availability. |
| `disallowed_tools` | Removed from model context entirely; cannot be called at all. |
| `permission_mode` | `default` (prompt/callback on dangerous ops) · `acceptEdits` (auto-approve edits) · `plan` (no execution) · `bypassPermissions` (approve everything except deny rules) · `dontAsk` (deny anything not pre-approved) · `auto` (a model classifier approves/denies each call). Changeable mid-run via `set_permission_mode()`. |
| `system_prompt` | `str` \| `{"type":"preset","preset":"claude_code","append":...,"exclude_dynamic_sections":...}` \| `{"type":"file","path":...}`. `exclude_dynamic_sections` keeps the prompt byte-stable for cross-user cache hits. |
| `mcp_servers` | `{name: config}` where config is `stdio` \| `sse` \| `http` \| `sdk` (in-process instance). `strict_mcp_config` ignores all filesystem MCP config. |
| `setting_sources` | Which settings layers load: `["user","project","local"]`; `[]` = full SDK isolation (no filesystem settings, no CLAUDE.md). |
| Limits | `max_turns`, `max_budget_usd` (→ `error_max_budget_usd` result), `task_budget` (model-aware token pacing), `effort`, `thinking`, `model` + `fallback_model`. |
| Sessions | `resume` (session id), `fork_session` (resume to a *new* id), `session_id` (pin UUID), `continue_conversation`, `session_store` + `session_store_flush` (`batched`/`eager`), `enable_file_checkpointing` + `rewind_files()`. |
| Observability | `include_partial_messages` (raw API `StreamEvent`s), `include_hook_events` (`HookEventMessage` lifecycle frames), `stderr` callback. |
| `agents` | `{name: AgentDefinition(description, prompt, tools, disallowedTools, model, permissionMode, maxTurns, effort, mcpServers, skills, background)}` — programmatic subagent definitions; subagents get their **own** permission mode and tool policy. |
| `output_format` | `{"type":"json_schema","schema":{...}}` → validated `ResultMessage.structured_output`. |

### The permission decision pipeline (extracted state machine)

For each tool call the CLI evaluates, in order (documented across `types.py:1929-1945`, hook types, and the shadow-warning logic `types.py:1677-1756`):

```
tool_call(name, input, tool_use_id)
  │ 0. disallowed_tools → tool not in context (call impossible)
  │ 1. PreToolUse hooks (matcher-filtered, concurrent, per-matcher timeout, default 60s)
  │      permissionDecision:
  │        "allow"  → skip rules AND skip can_use_tool → EXECUTE (optionally updatedInput)
  │        "deny"   → BLOCKED; permissionDecisionReason fed to model as tool error
  │        "ask"    → force the ask path; reason forwarded as context.decision_reason
  │        "defer"  → STOP THE RUN: ResultMessage.deferred_tool_use = {id, name, input}
  │ 2. Permission rules: deny rules always win → allow rules (allowed_tools /
  │      settings permissions.allow) auto-approve → mode logic:
  │        bypassPermissions → approve; acceptEdits → approve edit tools;
  │        plan → no execution; dontAsk → deny if not pre-approved;
  │        auto → model classifier approves/denies; default → outcome = "ask"
  │ 3. outcome "ask" → CLI sends control_request {subtype:"can_use_tool", tool_name,
  │      input, permission_suggestions, blocked_path, decision_reason, title,
  │      display_name, description, tool_use_id, agent_id} and BLOCKS the agent loop
  │      awaiting the SDK's control_response.  ← THIS is the approval gate.
  │ 4. EXECUTE (possibly with rewritten input) → PostToolUse hook (may replace output
  │      via updatedToolOutput / inject additionalContext) or PostToolUseFailure on error.
```

**`can_use_tool` exact contract** (`types.py:198-256`, dispatch in `_internal/query.py:429-481`):

```python
CanUseTool = Callable[[str, dict[str, Any], ToolPermissionContext], Awaitable[PermissionResult]]
# ToolPermissionContext: tool_use_id (always set), agent_id (subagent attribution),
#   suggestions: list[PermissionUpdate] (CLI-proposed rules like "always allow this"),
#   blocked_path, decision_reason, title, display_name, description (ready-made UI strings)

PermissionResultAllow(behavior="allow", updated_input: dict|None,      # rewrite args before exec
                      updated_permissions: list[PermissionUpdate]|None) # persist "always allow"
PermissionResultDeny(behavior="deny", message: str,   # fed to the model as the tool error
                     interrupt: bool = False)         # True → abort the whole turn
```

`PermissionUpdate` (`types.py:122`) is a first-class rule mutation: `addRules|replaceRules|removeRules|setMode|addDirectories|removeDirectories`, each rule `{toolName, ruleContent}` with behavior `allow|deny|ask` and a `destination` of `userSettings|projectSettings|localSettings|session` — i.e. approvals can be remembered at four scopes.

**Exact wire frames of the gate** (`types.py:2134-2146`, response encoding `_internal/query.py:459-481`) — the reference shape for Stept's `pending_tool_call` / `approval_decision` payloads:

```jsonc
// CLI → SDK (agent loop blocks until answered; cancellable via control_cancel_request)
{"type": "control_request", "request_id": "req_7_a1b2c3d4", "request": {
  "subtype": "can_use_tool", "tool_name": "Bash", "tool_use_id": "toolu_01X…",
  "input": {"command": "rm -rf build"},
  "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "rm:*"}],
                              "behavior": "allow", "destination": "session"}],
  "blocked_path": null, "decision_reason": null,
  "title": "Claude wants to run rm -rf build", "display_name": "Run command",
  "description": "Removes the build directory", "agent_id": null}}
// SDK → CLI (allow — note updatedInput always echoed, rewritten or original)
{"type": "control_response", "response": {"subtype": "success", "request_id": "req_7_a1b2c3d4",
  "response": {"behavior": "allow", "updatedInput": {"command": "rm -rf build"},
               "updatedPermissions": [ /* optional PermissionUpdate dicts */ ]}}}
// SDK → CLI (deny)
{"type": "control_response", "response": {"subtype": "success", "request_id": "req_7_a1b2c3d4",
  "response": {"behavior": "deny", "message": "Not allowed on prod paths", "interrupt": false}}}
```

**Composition rules & guardrails:**
- `can_use_tool` fires **only** when rules evaluate to "ask" — it is the programmatic replacement for the interactive prompt, not a universal interceptor. To see *every* call, use a PreToolUse hook (but a hook `allow` also skips the callback).
- The SDK emits `CanUseToolShadowedWarning` at connect when `bypassPermissions` or whole-tool `allowed_tools` entries make the callback dead code (`types.py:1696-1756`) — a great lint idea for Stept's config validation.
- `can_use_tool` requires streaming mode and auto-sets `permission_prompt_tool_name="stdio"`; mutually exclusive with a custom permission-prompt MCP tool (`client.py:161-181`).
- The callback is async and the run **blocks in-process** while it awaits — a human approval that outlives the process is impossible via this path. The escape hatch is `defer`: stop the run, persist `deferred_tool_use` from the ResultMessage, and later `resume` the session with the decision. **Stept's DB-backed gate = the `defer` pattern made first-class.**
- Interrupt: `client.interrupt()` control request; result carries `terminal_reason` = `aborted_streaming` / `aborted_tools`. Deny with `interrupt=True` aborts from inside the gate.

## 3. Hooks & custom tools (schemas)

### Hook events (`types.py:260-599`)

| Event | Input payload (beyond `session_id, transcript_path, cwd, permission_mode`) | Can do |
|---|---|---|
| `PreToolUse` | `tool_name, tool_input, tool_use_id` (+`agent_id/agent_type` in subagents) | allow/deny/ask/**defer**, `updatedInput`, `additionalContext` |
| `PostToolUse` | + `tool_response` | `updatedToolOutput` (replace result before model sees it), `additionalContext` |
| `PostToolUseFailure` | + `error, is_interrupt` | `additionalContext` |
| `UserPromptSubmit` | `prompt` | block (`decision:"block"` + `reason`), `additionalContext` |
| `Stop` / `SubagentStop` | `stop_hook_active` (+agent ids/transcript) | block stopping (force continuation) |
| `SubagentStart` | `agent_id, agent_type` | `additionalContext` |
| `PreCompact` | `trigger: manual\|auto, custom_instructions` | observe/steer compaction |
| `Notification` | `message, title, notification_type` | observe |
| `PermissionRequest` | `tool_name, tool_input, permission_suggestions` | `decision` dict |

Common output fields: `continue_` (False halts Claude entirely, with `stopReason`), `suppressOutput`, `decision:"block"` + `reason` (feedback to the model), `systemMessage` (user-facing warning). Async form `{async_: True, asyncTimeout}` defers execution. Registration: `hooks={event: [HookMatcher(matcher="Bash"|"Write|Edit", hooks=[cb], timeout=s)]}`; matchers are registered by callback-id at `initialize` and **dispatched concurrently** (`types.py:1953-1958`) — hooks must be independent. Wire: CLI sends `hook_callback {callback_id, input, tool_use_id}`, SDK returns the JSON output (`_internal/query.py:483-497`).

### Custom tools — in-process MCP (`__init__.py:160-525`)

```python
@tool("refund", "Refund an order", {"order_id": str, "amount": Annotated[float, "USD"]})
async def refund(args: dict) -> dict:
    return {"content": [{"type": "text", "text": "done"}], "is_error": False}
server = create_sdk_mcp_server(name="billing", version="1.0.0", tools=[refund])
options = ClaudeAgentOptions(mcp_servers={"billing": server},
                             allowed_tools=["mcp__billing__refund"])
```

- `input_schema`: `{"param": python_type}` dict, a `TypedDict` class, or raw JSON Schema; auto-converted (`_python_type_to_json_schema`, NotRequired → optional, `Annotated[t, "desc"]` → description).
- Result contract: `{"content": [text|image|resource blocks], "is_error": bool}` → MCP `CallToolResult`. Tool names are namespaced `mcp__{server}__{tool}` for policy rules.
- Transport trick: the "server" is a real `mcp.server.Server` living in the SDK process; the CLI proxies JSONRPC (`initialize`, `tools/list`, `tools/call`) over the control channel (`_internal/query.py:593-766`) — no extra process, tools share application state. `ToolAnnotations` (readOnly/destructive/openWorld) ride along as metadata — note the SDK *models* destructiveness but does not enforce it; enforcement is the permission pipeline's job.

## 4. Message / trace type hierarchy (`types.py:920-1358`, parser `_internal/message_parser.py`)

```
Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent | RateLimitEvent
ContentBlock = TextBlock{text} | ThinkingBlock{thinking, signature}
             | ToolUseBlock{id, name, input} | ToolResultBlock{tool_use_id, content, is_error}
             | ServerToolUseBlock{id, name∈(web_search, code_execution, advisor…), input}
             | ServerToolResultBlock{tool_use_id, content}
```

- `UserMessage{content, uuid, parent_tool_use_id, tool_use_result}` — tool results come back as user-role messages, mirroring the API wire shape.
- `AssistantMessage{content, model, parent_tool_use_id, error(auth|billing|rate_limit|invalid_request|server_error|unknown), usage, message_id, stop_reason, session_id, uuid}` — one row per LLM response, with per-call usage attached.
- `SystemMessage{subtype, data}` with typed subclasses: `TaskStartedMessage` / `TaskProgressMessage{usage: {total_tokens, tool_uses, duration_ms}}` / `TaskNotificationMessage{status: completed|failed|stopped}` / `TaskUpdatedMessage{patch}` (subagent/background-task lifecycle; terminal statuses = `{completed, failed, stopped, killed}`), `HookEventMessage{hook_started|hook_response}`, `MirrorErrorMessage` (store append failed — non-fatal).
- `ResultMessage` — the run summary row: `subtype` (`success` | `error_max_turns` | `error_max_budget_usd` | …), `duration_ms`, `duration_api_ms`, `is_error`, `num_turns`, `session_id`, `total_cost_usd`, `usage`, `model_usage: dict[model, ModelUsage{inputTokens, outputTokens, cacheReadInputTokens, cacheCreationInputTokens, costUSD, contextWindow, …}]`, `result` (final text), `structured_output`, `permission_denials`, **`deferred_tool_use{id,name,input}`**, `errors`, `api_error_status` (429/500/529), `terminal_reason` (`completed|max_turns|aborted_streaming|aborted_tools`).
- `StreamEvent{uuid, session_id, event(raw API delta), parent_tool_use_id}` — token-level streaming, opt-in.
- **Trace-tree key**: `parent_tool_use_id` links every subagent message back to the `Task` tool call that spawned it; `uuid` is a stable per-message idempotency key (the SessionStore contract uses it for upsert dedup, `types.py:1493-1497`); `session_id` scopes everything. These three fields are the entire lineage model — Stept should copy it verbatim.

## 5. Proposed Stept agent engine (Python/FastAPI, DB-backed, restart-safe)

The SDK's loop is in-process; its `defer` + `resume` + external `SessionStore` are the seams that make a durable version possible. Stept should own the agent loop (direct Messages API / multi-provider) and lift the SDK's *shapes*.

### 5.1 AgentRun state machine

```
queued → running ↔ awaiting_approval → resuming → running → completed
                 ↘ failed (error/max_turns/budget)   ↘ rejected-resume feeds denial to model
                 ↘ handed_off (handoff_to_human tool) ↘ canceled (operator interrupt)
awaiting_approval --(timeout, e.g. 24h)--> timed_out → policy: auto-deny-resume | handed_off
```

```python
class AgentRun(Base):
    id: UUID; conversation_id: UUID; agent_id: UUID; agent_version: int
    status: Literal["queued","running","awaiting_approval","resuming",
                    "completed","failed","handed_off","canceled","timed_out"]
    # Restart-safety: worker heartbeat + lease; a reaper re-queues runs whose
    # lease expired while "running" (idempotent because steps are journaled).
    lease_owner: str | None; lease_expires_at: datetime | None
    # The approval gate (== SDK deferred_tool_use + can_use_tool request payload):
    pending_tool_call: JSONB | None   # {tool_use_id, name, input, title, description,
                                      #  decision_reason, suggestions, requested_at}
    # Conversation snapshot: append-only message journal (5.3) is the source of
    # truth; snapshot_message_seq marks the last journaled seq included in the
    # provider-format messages array we replay on resume.
    snapshot_message_seq: int
    num_turns: int; total_cost_usd: Decimal; terminal_reason: str | None
    approval_timeout_at: datetime | None
```

**Resume sequence after a human decision** (mirrors SDK store-backed resume — load journal → materialize → continue):

1. `POST /approvals/{run_id}/{tool_use_id}` writes the `approval` row in the same transaction that flips `status → resuming` (guard: `WHERE status='awaiting_approval' AND pending_tool_call->>'tool_use_id' = :id` — stale/duplicate decisions no-op).
2. Queue consumer claims the run (`UPDATE … SET lease_owner, lease_expires_at WHERE status='resuming'`), replays journal steps `seq <= snapshot_message_seq` into the provider `messages` array.
3. Approved: execute the tool handler (writing a `tool_call` step with `status=started` first, so a crash mid-execution is detectable); append `tool_result` step. Rejected: append `tool_result{is_error: true, content: approval.message}` without executing.
4. Clear `pending_tool_call`, set `status=running`, continue the LLM loop from step 3's messages. Every step append is upsert-on-`uuid`, so a crashed resume re-runs idempotently.
5. Terminal: write the run rollup, set `completed|failed|handed_off`, emit the final SSE event.

**Gate flow:** worker's loop gets a `tool_use` from the LLM → policy check (5.2). If `require_approval`: persist `pending_tool_call` + full assistant message (journal), set `status=awaiting_approval`, release the lease, **exit the worker** (nothing held in memory — unlike the SDK's blocking callback, exactly the `defer` pattern). Inbox UI lists runs in `awaiting_approval`, rendering `title/description/input` (the SDK ships those UI strings on the wire — copy that). Human decision writes an `approval` row `{run_id, tool_use_id, decision: approved|approved_with_edits|rejected, updated_input, message, actor_id}` and flips status to `resuming` → queue. Resume worker rebuilds `messages` from the journal, then: approved → execute tool (with `updated_input` if edited) and append the `tool_result`; rejected → append `tool_result{is_error: true, content: message}` so **the model sees the denial and continues** (SDK deny semantics), unless `interrupt=true` → `canceled`. Timeout via `approval_timeout_at` scanned by the reaper. Idempotency: the tool execution row (5.3) is written `status=started` before the side effect; resume checks it to avoid double-execution.

### 5.2 Tool registry + per-agent per-tool policy

```python
class ToolPolicy(str, Enum): auto = "auto"; require_approval = "require_approval"; disabled = "disabled"

@dataclass
class BuiltinTool:                      # registry entries, mirroring @tool
    name: str; description: str; input_schema: dict          # JSON Schema
    handler: Callable[[ToolCtx, dict], Awaitable[ToolResult]] # ToolResult = {content, is_error}
    annotations: dict  # {"readOnly": bool, "destructive": bool} — drives default policy
    control: Literal["none","handoff","close"] = "none"       # loop-control side effects

REGISTRY = [
  BuiltinTool("search_knowledge", ..., annotations={"readOnly": True}),          # default auto
  BuiltinTool("collect_contact_details", ...),                                   # default auto
  BuiltinTool("tag_conversation", ...),                                         # default auto
  BuiltinTool("close_conversation", ..., control="close"),                      # default require_approval
  BuiltinTool("handoff_to_human", ..., control="handoff"),                      # always auto → handed_off
  BuiltinTool("http_request", ..., annotations={"destructive": True}),          # custom actions; default require_approval
]
```

`agent_tool_config` table: `{agent_id, tool_name, policy, param_rules JSONB}` — `disabled` removes the tool from the model's tool list entirely (SDK `disallowed_tools`: absence beats denial); `param_rules` are specifier-style constraints inspired by `allowed_tools` entries like `Bash(ls:*)` — e.g. `http_request(domain: api.stripe.com) → auto`, else escalate. Evaluation order per call (compressed SDK pipeline): **guardrail hook (deterministic validators: schema-validate input, PII/domain checks; may deny or rewrite) → deny rules → param-rule allow → tool policy (auto | require_approval) → execute**. Also port `PermissionUpdate` as `approval_memory`: "always allow for this conversation / this agent" rows written when a human ticks "don't ask again" (scopes ≈ session/project/user destinations). Validate configs at save time and surface a shadow warning ("approval on `search_knowledge` is dead because a rule auto-allows it") like `CanUseToolShadowedWarning`.

### 5.3 Trace schema — `agent_step` rows (informed by the Message hierarchy)

```python
class AgentStep(Base):
    id: UUID; run_id: UUID; seq: int                    # dense order within run
    uuid: str                                           # idempotency key (SDK message.uuid)
    parent_tool_use_id: str | None                      # subagent/task lineage (SDK field)
    kind: Literal["user_message","llm_call","assistant_message","thinking",
                  "tool_call","tool_result","approval_request","approval_decision",
                  "guardrail","handoff","system","result"]
    payload: JSONB          # kind-discriminated: content blocks / {tool_use_id,name,input}
                            # / {tool_use_id,content,is_error} / decision rows
    model: str | None; stop_reason: str | None
    usage: JSONB | None     # {input_tokens, output_tokens, cache_read, cache_creation}
    cost_usd: Decimal | None; latency_ms: int | None
    error: str | None; error_class: str | None          # SDK AssistantMessageError enum
    created_at: datetime
```

Plus a per-run rollup mirroring `ResultMessage`: `num_turns`, `duration_ms`, `duration_api_ms`, `total_cost_usd`, `model_usage` JSONB keyed by model (per-model tokens + cost — SDK `ModelUsage`), `terminal_reason`, `permission_denials`, `structured_output`. The step journal doubles as the conversation snapshot: replaying `kind ∈ {user_message, assistant_message, tool_result}` in `seq` order reconstructs the provider `messages` array (the SDK's JSONL-transcript-as-truth model). Stream steps to the inbox UI over SSE/WS as they're written — Stept's equivalent of `receive_messages()`.

### 5.4 Guardrails worth porting

- **`max_turns` + `max_budget_usd`** as first-class run limits with distinct terminal subtypes (`error_max_turns`, `error_max_budget_usd`) — never a generic failure.
- **Interrupt** as an API verb (`POST /runs/{id}/cancel`): worker checks a cancel flag between steps; record `terminal_reason="aborted"` (SDK `aborted_streaming/aborted_tools`).
- **Input rewriting on allow** (`updated_input`) — approvers can fix the refund amount instead of reject-and-retry.
- **Deny-with-feedback** — rejection text goes to the model as an `is_error` tool result so the agent adapts; separate flag for hard-abort.
- **Deterministic pre/post hooks**: PreToolUse-style validators (schema, allowlisted domains for `http_request`, rate caps) and PostToolUse-style output redaction (`updatedToolOutput`) before results reach the model.
- **Hook/callback timeouts** (SDK: 60s default per matcher; control requests 60s) and **concurrent-hook isolation** — never let one slow validator wedge the loop.
- **Per-message `uuid` idempotency + at-least-once journal writes with upsert** (SessionStore `append` contract) — makes crash-resume safe.
- **Config lint** for shadowed policies; **structured error taxonomy** (auth/billing/rate_limit/invalid_request/server_error) on steps for retry logic.

## 6. Top 10 actionable recommendations

1. **Model Stept's approval gate on `defer`, not on `can_use_tool`**: persist the pending tool call, end the worker turn, resume from the journal — the blocking-callback pattern cannot survive process restarts; the SDK itself added `defer` + `ResultMessage.deferred_tool_use` for exactly this round trip.
2. **Copy the two-result union**: `Allow{updated_input, remembered_scopes}` / `Deny{message → model-visible tool error, interrupt}` — approve-with-edits and deny-with-feedback are the highest-value UX features of the SDK's gate.
3. **Separate availability from policy from approval memory**: `tools` (what exists) vs `disabled` (removed from context) vs `auto/require_approval` vs remembered `PermissionUpdate`-style rules with explicit scopes (conversation/agent/workspace).
4. **Ship UI strings with the gate payload** (`title`, `display_name`, `description`, `decision_reason`, `suggestions`) so the inbox never reconstructs prompts from raw tool JSON.
5. **Adopt the three lineage fields** — `uuid` (idempotency), `session_id`/`run_id` (scope), `parent_tool_use_id` (subagent tree) — on every step row; they're sufficient for full trace trees.
6. **Persist a `ResultMessage`-shaped rollup per run** including per-model `model_usage` (tokens + cost by model) and `terminal_reason`; distinct terminal subtypes for limit hits.
7. **Use permission modes as agent presets**: map `default/acceptEdits/bypassPermissions/dontAsk/plan` to Stept agent "strictness" presets (e.g. `dontAsk` ≡ "only pre-approved tools", `plan` ≡ draft/suggest mode where the agent proposes replies without executing) — and allow runtime mode switch like `set_permission_mode`.
8. **Define builtin tools with the `@tool` contract**: name + description + JSON Schema + async handler returning `{content: [...blocks], is_error}` — identical to MCP's `CallToolResult`, which buys future MCP interop (customer-supplied MCP servers as custom actions) for free.
9. **Journal transcripts append-only with batched flush** (SDK: flush per turn or 500 entries/1MiB, upsert on `uuid`, mirror failures surfaced as non-fatal events) — and treat "replay the journal → provider messages array" as the only resume mechanism, never in-memory state.
10. **Add the SDK's operational guardrails from day one**: `max_turns`, budget caps, per-hook timeouts, interrupt verb, config shadow-lint, structured API error classes on steps, and lease+reaper for crashed workers (the piece the in-process SDK lacks and Stept must add).
