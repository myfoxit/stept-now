# Vercel AI SDK — Architecture Research for Stept

Source: shallow clones of `vercel/ai` (ai v7.0.44, provider spec v4.0.4) and `vercel/ai-chatbot` at
`/private/tmp/claude-501/-Users-ahoehne-repos-stept-now/418ccf97-660c-4f06-b9bd-eb697d851efe/scratchpad/repos/{vercel-ai,ai-chatbot}`.
All paths below are repo-relative. Current spec version is **LanguageModelV4** (`packages/provider/src/language-model/v4/`); v2/v3 are kept for back-compat and all first-party providers implement v4.

## 1. Architecture map

Layered monorepo, strict one-way dependencies:

- **`packages/provider`** — pure TypeScript types, zero runtime. The `LanguageModelV4` spec (call options, prompt, stream parts, usage, finish reasons) plus embedding/image/speech specs. Versioned side-by-side (`v2/ v3/ v4/`) so providers and core can upgrade independently.
- **`packages/provider-utils`** — shared runtime for providers: HTTP + SSE response handlers (`createEventSourceResponseHandler`, `createJsonErrorResponseHandler`), `retry-with-exponential-backoff.ts` (default `maxRetries=2`, honors `isRetryable`), and `streaming-tool-call-tracker.ts` (index-based tool-call delta accumulator reused by OpenAI-shaped providers).
- **`packages/ai`** — the core SDK: `generate-text/` (generateText/streamText + tool loop), `agent/` (`ToolLoopAgent`), `ui-message-stream/` (server→client SSE chunk protocol), `ui/` (framework-agnostic `Chat` class, `UIMessage` model, transports), `middleware/`, `registry/` (provider registry: `"openai:gpt-4o"` style ids).
- **Provider packages** — `openai/` (chat + responses + completion models), `anthropic/`, `google/`, `openai-compatible/` (base class many vendors extend — the right template for Stept's Ollama/OpenAI-compatible support).
- **Framework bindings** — `react/` (`useChat` = thin wrapper over `ai`'s `Chat` class with throttled re-render).

**Two distinct stream protocols** (the key design idea to copy):
1. **Provider stream parts** (`LanguageModelV4StreamPart`) — the normalized model-output stream each adapter must emit.
2. **UI message chunks** (`UIMessageChunk`) — the server↔browser SSE protocol; `packages/ai/src/generate-text/to-ui-message-stream.ts`-adjacent code maps 1→2, adding message/step framing and app data parts.

## 2. LanguageModel interface (exact shapes)

`packages/provider/src/language-model/v4/language-model-v4.ts`:

```ts
type LanguageModelV4 = {
  specificationVersion: 'v4';
  provider: string;              // e.g. "anthropic"
  modelId: string;
  supportedUrls: Record<string, RegExp[]> | Promise<...>;  // media-type -> URL patterns the model can fetch natively
  doGenerate(options: LanguageModelV4CallOptions): PromiseLike<GenerateResult>;
  doStream(options: LanguageModelV4CallOptions): PromiseLike<{ stream: ReadableStream<StreamPart>, request?, response? }>;
}
```

**CallOptions** (`language-model-v4-call-options.ts`): `prompt`, `maxOutputTokens`, `temperature`, `stopSequences`, `topP`, `topK`, `presencePenalty`, `frequencyPenalty`, `responseFormat: {type:'text'} | {type:'json', schema?, name?, description?}`, `seed`, `tools`, `toolChoice`, `includeRawChunks`, `abortSignal`, `headers`, `reasoning: 'provider-default'|'none'|'minimal'|'low'|'medium'|'high'|'xhigh'`, `providerOptions` (namespaced `Record<providerName, Record<string, JSONValue>>` escape hatch).

**Prompt** (`language-model-v4-prompt.ts`) — array of role messages, every message/part carries optional `providerOptions`:
- `system`: `content: string`
- `user`: parts `text | file` (file data is a tagged union: `{type:'data'|'url'|'reference'|'text'}` + `mediaType`)
- `assistant`: parts `text | file | custom | reasoning | reasoning-file | tool-call | tool-result` (tool-call: `{toolCallId, toolName, input: unknown, providerExecuted?}`)
- `tool`: parts `tool-result | tool-approval-response` (`{approvalId, approved, reason?}`)

**Tool result output** is a union (not just a string): `{type:'text'|'json'|'execution-denied'|'error-text'|'error-json'|'content'}` where `content` is an array of text/file/custom parts — lets tools return images, and lets denial be a first-class result the model sees.

**Tools** (`language-model-v4-function-tool.ts`, `-provider-tool.ts`):
```ts
FunctionTool = { type:'function', name, description?, inputSchema: JSONSchema7, inputExamples?, strict?, providerOptions? }
ProviderTool = { type:'provider', id: `${provider}.${type}`, name, args }   // e.g. anthropic.web_search — executed server-side by the provider
ToolChoice  = {type:'auto'|'none'|'required'} | {type:'tool', toolName}
```

**Usage** (`language-model-v4-usage.ts`) — every field `number | undefined` (undefined = "provider didn't report", never fake 0):
```ts
{ inputTokens: { total, noCache, cacheRead, cacheWrite }, outputTokens: { total, text, reasoning }, raw?: JSONObject }
```

**FinishReason** (`language-model-v4-finish-reason.ts`): `{ unified: 'stop'|'length'|'content-filter'|'tool-calls'|'error'|'other', raw: string|undefined }` — normalized value plus the provider's raw string preserved.

**GenerateResult**: `{ content: Content[], finishReason, usage, providerMetadata?, request?: {body}, response?: {id, timestamp, modelId, headers, body}, warnings: Warning[] }`. Warnings surface unsupported settings instead of throwing (e.g. "topK not supported").

## 3. Stream protocol (exact part types + SSE format)

### 3a. Provider-level stream parts (`language-model-v4-stream-part.ts`)

Block-oriented: every text/reasoning/tool-input span has start/delta/end with a shared `id`, so multiple concurrent blocks can interleave:

```
stream-start {warnings[]}                       — always first
response-metadata {id?, modelId?, timestamp?}   — as soon as known
text-start {id} / text-delta {id, delta} / text-end {id}
reasoning-start {id} / reasoning-delta {id, delta} / reasoning-end {id}
tool-input-start {id, toolName, providerExecuted?, dynamic?, title?}
tool-input-delta {id, delta}                    — raw partial JSON string
tool-input-end {id}
tool-call {toolCallId, toolName, input: string, providerExecuted?, dynamic?}   — complete call, input is a JSON *string*
tool-result {toolCallId, toolName, result, isError?, preliminary?, dynamic?}   — provider-executed tools only
tool-approval-request {approvalId, toolCallId}
source {sourceType:'url'|'document', id, url|mediaType, title?, filename?}
file {data, mediaType} | reasoning-file | custom {kind}
raw {rawValue}                                  — only when includeRawChunks
finish {usage, finishReason, providerMetadata?} — always last on success
error {error}                                   — in-band, stream can continue
```

### 3b. UI message stream (server → useChat client)

`packages/ai/src/ui-message-stream/ui-message-chunks.ts` (types + zod schema — the client validates every chunk). Wire format (`json-to-sse-transform-stream.ts`): SSE with anonymous events, one JSON object per `data:` line, terminated by `data: [DONE]`. Headers (`ui-message-stream-headers.ts`): `content-type: text/event-stream`, `cache-control: no-cache`, `x-vercel-ai-ui-message-stream: v1`, `x-accel-buffering: no` (critical behind nginx).

Full chunk enumeration:
```
start {messageId?, messageMetadata?}            — begin assistant message
start-step / finish-step                        — one pair per LLM call in the tool loop
text-start/-delta/-end {id, delta}
reasoning-start/-delta/-end {id, delta}
tool-input-start {toolCallId, toolName, providerExecuted?, dynamic?, title?}
tool-input-delta {toolCallId, inputTextDelta}
tool-input-available {toolCallId, toolName, input, ...}      — parsed+validated args
tool-input-error {toolCallId, toolName, input, errorText}
tool-approval-request {approvalId, toolCallId, isAutomatic?, signature?}   — signature = server HMAC, replayed on approval
tool-approval-response {approvalId, approved, reason?}
tool-output-available {toolCallId, output, preliminary?}
tool-output-error {toolCallId, errorText}
tool-output-denied {toolCallId}
source-url {sourceId, url, title?} | source-document {sourceId, mediaType, title, filename?}
file {url, mediaType} | reasoning-file {url, mediaType}
data-<name> {id?, data, transient?}             — app-defined typed parts; transient = not persisted into message
message-metadata {messageMetadata}              — merge metadata mid-stream
error {errorText}
abort {reason?}
finish {finishReason?, messageMetadata?}
```

### 3c. Client state (`packages/ai/src/ui/`)

`UIMessage = { id, role, metadata?, parts: UIMessagePart[] }` (`ui-messages.ts`). Tool parts are a **state machine per toolCallId**: `input-streaming → input-available → approval-requested → approval-responded → output-available | output-error | output-denied`. `Chat` class (`chat.ts`): `status: 'submitted' | 'streaming' | 'ready' | 'error'`; `submitted` set on send, flips to `streaming` on first chunk. `resumeStream()` triggers `reconnectToStream` (GET to same endpoint) and no-ops if no active stream. `sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithToolCalls` auto-resubmits after client-side tool results / approval responses are added (`addToolResult`, `addToolApprovalResponse`). React binding re-render throttling: `experimental_throttle` ms (`packages/react/src/use-chat.ts:57`).

## 4. Tool loop + agent semantics

Core loop in `packages/ai/src/generate-text/generate-text.ts` (do/while at ~line 813; streaming variant mirrors it in `stream-text.ts` + `execute-tools-from-stream.ts`):

1. **prepareStep** hook (`prepare-step.ts`) — called before every step with `{steps, stepNumber, model, messages, ...}`; may override `model`, `instructions/system`, `messages` (compaction!), `toolChoice`, `activeTools` (subset visible this step), `toolOrder`, `runtimeContext`.
2. `doGenerate`/`doStream` inside `retry(...)` (exponential backoff, default 2 retries).
3. **parseToolCall** (`parse-tool-call.ts`): JSON-parse args, validate against the tool's schema. On `NoSuchToolError`/`InvalidToolInputError` → call **`repairToolCall`** (`tool-call-repair-function.ts`: gets `{toolCall, tools, inputSchema, error, messages, system}`, returns fixed `LanguageModelV4ToolCall` or `null` to rethrow). Unknown provider-executed tools pass through as `dynamic: true` instead of erroring.
4. **Approvals** (`collect-tool-approvals.ts`, `resolve-tool-approval.ts`): tools can `needsApproval`; approval requests pause the loop and are emitted as stream parts; denied approvals are fed back as `tool-result` with `output: {type:'execution-denied', reason}` so the model sees the denial. Optional HMAC signing of approval requests (`tool-approval-signature.ts`, `approvalSigningSecret`) prevents client-forged approvals.
5. **executeTools** runs client tools (with `onToolExecutionStart/End` callbacks, abort signal, `preliminary` streaming results support); results appended as response messages (`to-response-messages.ts`) and the next step's prompt.
6. **Continue condition** (generate-text.ts:1399): loop iff (all client tool calls got outputs or denials, or provider-executed deferred results pending) **and** no `stopWhen` condition met. Natural stops: finishReason ≠ `tool-calls`, a tool without `execute`, or a pending approval.
7. **stopWhen** (`stop-condition.ts`): array of predicates over `steps`; built-ins `isStepCount(n)`, `hasToolCall(name)`, `isLoopFinished()`.

**Agent** (`packages/ai/src/agent/tool-loop-agent.ts`): `ToolLoopAgent` is just constructor-bound settings (model, instructions, tools, `stopWhen` default `isStepCount(20)` at line 132) exposing `generate()`, `stream()`, and `respond()`/`createAgentUIStreamResponse()` which pipes straight to the UI message stream. No planner magic — an agent IS a configured tool loop. `InferAgentUIMessage` derives the typed client message from the agent's tools.

## 5. Provider adapter gotchas (OpenAI vs Anthropic vs Google)

**OpenAI chat** (`packages/openai/src/chat/openai-chat-language-model.ts`):
- SSE of whole-choice deltas. Text: single implicit block — adapter opens `text-start {id:'0'}` lazily on first `delta.content`, closes in flush.
- **Tool calls arrive as indexed fragments**: `delta.tool_calls[].{index, id?, function.name?, function.arguments}` — first fragment has id+name, later ones only `index` + argument chunks. `provider-utils/src/streaming-tool-call-tracker.ts` accumulates by index, emits `tool-input-start/-delta`, finalizes `tool-call` on flush; also handles single-chunk complete calls.
- `finish_reason` on the choice; usage only in the final chunk (needs `stream_options.include_usage`). `delta.annotations` → `source` parts. JSON mode: `response_format: {type:'json_schema', json_schema:{schema, strict: true}}` (strict default true). Reasoning models use the separate Responses API model class (item-based event stream, different world).

**Anthropic** (`packages/anthropic/src/anthropic-language-model.ts`, event switch ~line 1633):
- **Typed SSE events**, block-oriented: `message_start → content_block_start/delta/stop* → message_delta → message_stop`, plus `ping` (ignore) and in-stream `error` events (`overloaded_error` mapped to status 529, `isRetryable: true` — line 2651).
- `content_block_delta` sub-types: `text_delta`, `thinking_delta`, `signature_delta` (reasoning signature must be round-tripped via providerMetadata), `input_json_delta` (tool args as partial JSON string). Blocks keyed by `index`; `content_block_start` for `tool_use` may already contain full `input`.
- **Usage is split**: `message_start` carries input tokens, `message_delta` carries output tokens + `stop_reason`. `input_tokens` EXCLUDES cache reads/writes — total = input + cache_read + cache_creation (`convert-anthropic-usage.ts`; also handles per-iteration usage arrays for compaction/advisor).
- Stop reasons (`map-anthropic-stop-reason.ts`): `end_turn|stop_sequence|pause_turn → stop`, `refusal → content-filter`, `tool_use → tool-calls` **unless the JSON was produced via a synthetic tool → stop**, `max_tokens|model_context_window_exceeded → length`.
- JSON mode: no universal native response_format — for older models the adapter injects a **synthetic tool carrying the schema** and treats the tool call as the JSON output (`isJsonResponseFromTool`); newer models use native `output_format`. Tool names are mapped through a `toolNameMapping` for provider tools (web_search, code_execution...).

**Google** (`packages/google/src/google-language-model.ts`):
- `POST ...:streamGenerateContent?alt=sse` (line 598) — SSE of full `GenerateContentResponse` chunks, parts-based not delta-based.
- **Function calls arrive complete in one chunk** — `part.functionCall.{name, args}` with args already an object (adapter does `JSON.stringify(args)` to fit the unified `input: string`; emit start/available together, no arg streaming). Often **no call id** → adapter generates one (line 469).
- Reasoning = ordinary text parts flagged `thought: true`; `thoughtSignature` must be round-tripped in providerMetadata for tool-call continuation. Grounding metadata → source parts.
- Finish reasons (`map-google-finish-reason.ts`): `STOP` + tool calls present → `tool-calls` (Google doesn't distinguish!); `MALFORMED_FUNCTION_CALL → error`; SAFETY/RECITATION/SPII/etc → `content-filter`. Usage in `usageMetadata` (`promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`, `cachedContentTokenCount`). JSON mode: `responseMimeType: 'application/json'` + `responseSchema` (subset of JSON Schema — needs sanitizing).

**Cross-cutting**: every adapter converts HTTP errors via a per-provider error schema into a common `APICallError {statusCode, isRetryable, responseBody}`; retry lives in core, not adapters; unsupported settings become `warnings`, never exceptions; `abortSignal` flows into fetch.

## 6. Proposed Stept Python protocol + SSE wire format

### 6a. `ChatProvider` protocol (backend/app/ai/)

```python
# --- messages (unified prompt) ---
@dataclass
class TextPart:      type: Literal["text"] = "text"; text: str = ""
@dataclass
class FilePart:      type: Literal["file"] = "file"; media_type: str = ""; data: str | bytes = b""; url: str | None = None
@dataclass
class ToolCallPart:  type: Literal["tool_call"] = "tool_call"; tool_call_id: str = ""; tool_name: str = ""; input: dict = field(default_factory=dict)
@dataclass
class ToolResultPart:
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""; tool_name: str = ""
    output: ToolOutput = ...   # union: OutputText | OutputJSON | OutputError | OutputDenied(reason)
@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    parts: list[Part]
    provider_options: dict[str, dict] = field(default_factory=dict)  # {"anthropic": {"cache_control": ...}}

@dataclass
class ToolSpec:
    name: str; description: str; input_schema: dict  # JSON Schema
    strict: bool = False

@dataclass
class Usage:
    input_tokens: int | None = None; cache_read_tokens: int | None = None; cache_write_tokens: int | None = None
    output_tokens: int | None = None; reasoning_tokens: int | None = None; raw: dict = field(default_factory=dict)

FinishReason = Literal["stop", "length", "content_filter", "tool_calls", "error", "other"]

# --- stream events (provider layer, mirrors LanguageModelV4StreamPart) ---
@dataclass
class StreamEvent: ...   # discriminated union via `type`
# types: stream_start(warnings) | text_start(id) | text_delta(id, delta) | text_end(id)
#        reasoning_start/delta/end(id, delta) | tool_input_start(id, tool_name) | tool_input_delta(id, delta)
#        tool_call(tool_call_id, tool_name, input: dict, raw_input: str) | source(url|document, ...)
#        response_metadata(model_id, response_id) | finish(finish_reason: FinishReason+raw, usage: Usage)
#        error(message, code, retryable)

class ChatProvider(Protocol):
    provider_id: str          # "openai" | "anthropic" | "google" | "ollama" | "openai_compatible"
    async def generate(self, req: ChatRequest) -> GenerateResult: ...
    def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]: ...

@dataclass
class ChatRequest:
    model: str; messages: list[Message]; tools: list[ToolSpec] = ...
    tool_choice: ToolChoice = "auto"; max_output_tokens: int | None = None
    temperature: float | None = None; response_format: ResponseFormat | None = None  # text | json(schema)
    provider_options: dict = ...; abort: asyncio.Event | None = None; headers: dict = ...
```

Key rules copied from the SDK: usage fields are `None` when unreported; finish reason keeps `(unified, raw)`; `tool_call.input` is delivered both parsed (dict) and raw (str) so schema-validation/repair can operate on the raw text; adapters emit warnings for dropped params instead of raising; one retry helper with `retryable` classification lives above the adapters.

### 6b. Stept SSE wire protocol (FastAPI `StreamingResponse`, `text/event-stream`)

Use **named SSE events** (nicer for non-JS clients + widget) with JSON data; heartbeat comments every 15s; end with `event: done`. Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `X-Stept-Stream: v1`.

```
event: run.started        data: {"run_id", "conversation_id", "message_id", "agent_id"}
event: step.started       data: {"step": 1}
event: text.delta         data: {"id": "t1", "delta": "Hello"}          (text.start/text.end optional framing)
event: reasoning.delta    data: {"id": "r1", "delta": "..."}
event: tool.started       data: {"tool_call_id", "tool_name", "title"}
event: tool.args.delta    data: {"tool_call_id", "delta": "{\"query\": \"refu"}
event: tool.args          data: {"tool_call_id", "tool_name", "input": {...}}   (validated, final)
event: approval.required  data: {"approval_id", "tool_call_id", "tool_name", "input", "signature"}   → run pauses, status=waiting_approval
event: approval.resolved  data: {"approval_id", "approved", "reason"}
event: tool.result        data: {"tool_call_id", "output", "is_error": false, "preliminary": false}
event: tool.denied        data: {"tool_call_id", "reason"}
event: source             data: {"source_id", "kind": "url"|"document", "url"|"document_id", "title", "snippet"}   (citations for Fin-style answers)
event: handoff            data: {"from_agent", "to": "agent"|"human", "team_id", "reason"}
event: run.status         data: {"status": "running"|"waiting_approval"|"waiting_human"|"resolved"}
event: data               data: {"name": "typing_indicator"|..., "data": {...}, "transient": true}   (app-defined, Stept widget extras)
event: step.finished      data: {"step": 1, "finish_reason": "tool_calls", "usage": {...}}
event: message.metadata   data: {"model", "latency_ms", ...}
event: error              data: {"code": "provider_overloaded", "message", "retryable": true}
event: run.finished       data: {"finish_reason": "stop", "usage": {aggregated}, "message_id"}
event: done               data: [DONE]
```

Persist every event (minus `transient` data and deltas, which fold into their block) so a GET `/runs/{id}/stream?after=<event_seq>` can **resume** mid-generation (ai-chatbot does this with `consumeSseStream` + `resumable-stream` on Redis — copy that: tee the SSE into Redis keyed by run, replay on reconnect). Widget state machine mirrors useChat: `submitted → streaming → ready | error`, tool parts per tool_call_id: `args_streaming → args_ready → approval_requested → running → done | error | denied`.

### 6c. Normalization map (adapter cheat sheet)

| Concern | OpenAI(-compatible/Ollama) | Anthropic | Google |
|---|---|---|---|
| Text delta | `choices[0].delta.content` (one block) | `content_block_delta.text_delta` per block index | `candidates[0].content.parts[].text` (thought=false) |
| Reasoning | Responses API items / not in chat | `thinking_delta` + `signature_delta` (round-trip signature) | text part with `thought: true` (+ `thoughtSignature`) |
| Tool args streaming | fragments keyed by `index` (id+name first fragment only) → **use an index-keyed tracker** | `input_json_delta` on block index; may be complete in `content_block_start` | **never streamed** — complete `functionCall.args` object; synthesize id |
| Finish reason | `finish_reason` per choice (`tool_calls`) | `message_delta.stop_reason` (`tool_use`; JSON-via-tool ⇒ stop) | `finishReason: STOP` even with tool calls ⇒ infer `tool_calls`; `MALFORMED_FUNCTION_CALL` ⇒ error |
| Usage | final chunk, needs `stream_options.include_usage` | split: input @ `message_start`, output @ `message_delta`; input excludes cache tokens | `usageMetadata` on chunks; `thoughtsTokenCount` separate |
| JSON mode | `response_format: json_schema (strict)` | synthetic tool with schema (older) / `output_format` (newer); map its tool_use → text | `responseMimeType + responseSchema` (sanitize schema subset) |
| Errors | HTTP status + error JSON | in-stream `error` events too; `overloaded_error` ⇒ retryable 529 | HTTP; safety blocks come as finish reasons, not errors |

## 7. Top 10 actionable recommendations

1. **Two-layer protocol, exactly like the SDK**: internal `StreamEvent` (provider normalization) and public SSE run events (adds run/step/approval/handoff framing). Never let provider quirks leak past the adapter.
2. **Block-oriented deltas with ids** (`text_start/delta/end`): required for interleaved reasoning+text (Anthropic) and future parallel blocks; trivial for providers that only have one block.
3. **Tool results as a typed union including `execution_denied`** — denial must be a model-visible tool result, not an exception; this is what makes approval gates composable with the loop.
4. **Approval gates as stream pause + replayable response**: emit `approval.required{approval_id, signature}`, persist run state `waiting_approval`, resume the loop when the approval response arrives; HMAC-sign approval payloads server-side (the SDK does this — `tool-approval-signature.ts`) so clients can't forge approvals.
5. **Steal `stopWhen` + `prepareStep`**: loop control as predicates over accumulated steps (default `step_count <= 10`), and a per-step hook that can swap model/tools/messages — this one hook subsumes model routing, context compaction, and phased tool exposure.
6. **Tool-call repair hook**: on schema-validation failure, call a repair function (re-ask the model with the error + schema) before failing the run; pass the raw JSON string, not just the parsed dict.
7. **Usage: `None` ≠ `0`, keep `raw`, and get the provider math right** (Anthropic input excludes cache tokens; OpenAI needs `include_usage`; Google `thoughtsTokenCount`). Aggregate per step and per run — this is Stept's billing/analytics substrate.
8. **Resumable streams from day 1**: tee every run's SSE into Redis (`event_seq`-keyed), `GET /runs/{id}/stream` replays + follows; widget `resume()` on reconnect. ai-chatbot's `consumeSseStream` + `resumable-stream` pattern (`app/(chat)/api/chat/route.ts:406`) is the blueprint.
9. **Transient data events** for UX sugar: ai-chatbot streams `data-waiting-status` ("Still waiting...", model-health warnings after a timer) and `data-chat-title` as transient parts outside the message body — perfect fit for Stept's typing indicators, agent status, and conversation title updates without polluting stored messages.
10. **Widget message model = parts array with a per-tool state machine** (`args_streaming → ... → done|error|denied`) and 4-value chat status (`submitted/streaming/ready/error`) + render throttling (~50ms) — copy `UIMessage`/`useChat` semantics wholesale; also copy the loose-parsing stance (client validates chunks but ignores unknown fields) so the wire protocol can evolve.
