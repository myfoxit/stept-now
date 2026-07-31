# Stept — Domain Contracts

Binding contracts for wave agents. Read `CLAUDE.md` first (conventions), then your
section. Research background: `docs/research/*.md`. If reality forces a deviation,
implement the closest faithful version and CLEARLY list deviations in your final report.

Shared rules recap: portable DB types from `app.core.db`; every table workspace-scoped
(`WorkspaceScopedMixin`) unless noted; permissions via `require_perm`; events via
`app.core.events`; realtime via `app.realtime.manager.broadcast(topic, type, data)`;
background work via `app.core.queue.enqueue` + `@task`; secrets via
`encrypt_secret/decrypt_secret`; audit significant config mutations via
`app.services.audit.record`. Emit OpenAPI-friendly endpoints (typed response_model
everywhere — the frontend generates TS types from the schema).

---

## Wave 1 — Agent A: Directory (contacts, tags, teams, canned responses, segments, CSAT)

**Owns:** `app/models/{tag,team,canned_response,segment,csat}.py` (+ add to `contact.py`
ONLY if a column is missing — the Contact/ContactNote/ContactEvent models are already
written and FK'd by others; extend, don't restructure), `app/schemas/{contacts,tags,teams,canned_responses,segments}.py`,
`app/services/{contacts,tags,teams,canned_responses,segments,contacts_seed}.py`,
`app/api/v1/{contacts,tags,teams,canned_responses,segments}.py`, `tests/directory/*`.

### Models
- `Tag`: ws, name (uq per ws), color (hex str). `ContactTag`: contact_id, tag_id (uq pair).
  (Conversation tagging is agent B's `ConversationTag` — same Tag table.)
- `Team`: ws, name (uq per ws), icon (emoji str), description. `TeamMember`: team_id,
  user_id (uq pair).
- `CannedResponse`: ws, shortcut (uq per ws, no spaces, e.g. "refund-policy"), content
  (markdown, supports `{{contact.name}}` / `{{agent.name}}` placeholders), created_by.
- `Segment`: ws, name, filters JSON, created_by. Filter schema:
  `[{"field": "email|name|external_id|last_seen_at|created_at|verified|attributes.<key>",
     "op": "eq|neq|contains|starts_with|exists|not_exists|gt|lt", "value": ...}]` (AND semantics).
- `CsatResponse` (csat.py): ws, conversation_id (GUID, no FK constraint needed — B owns
  that table; use plain GUID column), contact_id FK, rating int 1..5, feedback text nullable.

### Services (signatures other agents rely on)
- `contacts.find_or_create(session, workspace_id, *, external_id=None, email=None,
  name=None, attributes=None, verified=False, actor=Actor.system()) -> tuple[Contact, bool]`
  — match priority: external_id, then email (most recent), else create. Updates
  name/email/attrs if provided (never downgrades verified True→False). Emits
  `contact.created` on create. Touches first_seen_at/last_seen_at.
- `contacts.list_contacts(...)`: search `q` (name/email/external_id, case-insensitive),
  segment filter application, cursor pagination sorted by last_seen_at desc nulls-last
  then created_at desc.
- `segments.apply_filters(session, workspace_id, filters) -> list[Contact]` — SQL for
  core fields; `attributes.<key>` may filter in Python after a candidate query (portable).
- `csat.record_response(session, workspace_id, *, conversation_id, contact_id, rating,
  feedback=None)` — idempotent per conversation (update existing), emits `csat.submitted`.

### APIs (all under /w/{workspace_id}, perms: contacts:read/write; teams+tags via
conversations:manage for CRUD; canned read=conversations:read write=conversations:manage;
segments read=contacts:read write=contacts:write)
- `GET/POST /contacts`, `GET/PATCH/DELETE /contacts/{id}`, `GET/POST /contacts/{id}/notes`,
  `DELETE /contacts/{id}/notes/{note_id}`, `GET/POST /contacts/{id}/events`
  (POST usable by API keys for event tracking), `POST /contacts/{id}/tags` {tag_id},
  `DELETE /contacts/{id}/tags/{tag_id}`.
- `GET/POST /tags`, `PATCH/DELETE /tags/{id}` (deleting cascades link rows).
- `GET/POST /teams`, `PATCH/DELETE /teams/{id}`, `POST /teams/{id}/members` {user_id},
  `DELETE /teams/{id}/members/{user_id}` (validate user is a workspace member).
- `GET/POST /canned-responses`, `PATCH/DELETE /canned-responses/{id}`.
- `GET/POST /segments`, `PATCH/DELETE /segments/{id}`, `GET /segments/{id}/contacts` (preview).
- CSAT read: `GET /contacts/{id}/csat` list.

### Seed (`app/services/contacts_seed.py`, `async def seed(session, ctx)`)
8–10 contacts w/ varied attributes (plan: free/pro/enterprise, company), 3 tags
("vip", "bug", "billing"), team "Support" (both demo users), 4 canned responses,
1 segment ("Enterprise customers"), idempotent (check by name/email first).

### Tests (tests/directory/)
find_or_create matching matrix (external_id wins, email fallback, attribute merge,
verified never downgraded), list search+cursor pagination, segment filters (incl.
attributes.plan eq + exists + last_seen gt), tags/teams/canned CRUD + authz (viewer 403
on mutations), cross-workspace isolation (contact from ws2 invisible), CSAT idempotency,
API-key event tracking (write scope can POST events, read scope cannot).

---

## Wave 1 — Agent B: Conversations, messages, inboxes, realtime

**Owns:** `app/models/{inbox,conversation,message}.py`, `app/schemas/{inboxes,conversations,messages}.py`,
`app/services/{inboxes,conversations,conversations_seed}.py`, `app/api/v1/{inboxes,conversations}.py`,
`app/channels/registry.py`, `tests/conversations/*`.

Background: docs/research/chatwoot.md (tracker columns, contact_inboxes, statuses,
timestamps-not-counters unread). Follow it.

### Models
- `Inbox`: ws, name, channel_type ("widget"|"email"|"slack"|"telegram"|"api"), enabled
  bool, config JSON (public config: widget theming {accent_color, launcher_position,
  greeting, require_identity bool, office_hours}, email {address}, etc.),
  secrets_encrypted (Fernet-encrypted JSON string, nullable; store via
  set_secrets(dict)/get_secrets() helpers on the service), widget_key (str, unique,
  indexed, auto `wk_` + token for widget inboxes — the public embed key).
- `ContactInbox` (chatwoot's identity spine): ws, contact_id FK, inbox_id FK, source_id
  (uq per inbox — widget: visitor uuid; email: email address; telegram: chat id),
  hmac_verified bool, meta JSON.
- `ConversationCounter`: workspace_id PK, value int — per-ws display numbers.
- `Conversation`: ws, number int (per-ws sequential), inbox_id FK, contact_id FK,
  contact_inbox_id FK nullable, status ("open"|"pending"|"snoozed"|"resolved"; "pending"
  = AI-agent-owned per research), snoozed_until, priority ("none"|"low"|"medium"|"high"|
  "urgent"), assignee_user_id FK nullable, team_id (GUID plain, A owns teams — no FK
  constraint), subject nullable, attributes JSON, ai_agent_id GUID nullable (active AI
  agent; G's domain), waiting_since, first_reply_at, resolved_at, last_activity_at
  (indexed w/ ws+status), agent_last_seen_at, contact_last_seen_at, csat_requested bool.
  Indexes: (ws, status, last_activity_at), (ws, assignee_user_id), (ws, contact_id).
  Unique (ws, number).
- `Message`: ws, conversation_id FK indexed, direction ("in"|"out"), visibility
  ("public"|"note"|"activity"), author_type ("contact"|"user"|"agent"|"system"),
  author_id GUID nullable, author_name str (denormalized for display), content text
  (markdown), attachments JSON list [{key,name,size,content_type}], source_id nullable
  (uq per conversation when set — dedupe), delivery_status ("pending"|"sent"|"failed"|
  null for inbound), delivery_error nullable, meta JSON (citations
  [{n,title,url,document_id}], agent_run_id, email headers…). Index (conversation_id, created_at).

### Service contracts (E/F/G depend on these EXACT signatures)
```python
async def ingest_inbound(session, inbox: Inbox, *, source_id: str, content: str,
    contact_info: dict, attachments: list | None = None, message_source_id: str | None = None,
    subject: str | None = None, meta: dict | None = None) -> tuple[Conversation, Message]
    # find_or_create contact (via A's contacts.find_or_create), find/create ContactInbox,
    # reuse latest non-resolved conversation for that contact_inbox else create,
    # add inbound message. Dedupes on message_source_id.

async def create_conversation(session, *, inbox, contact, contact_inbox=None, subject=None,
    attributes=None, actor) -> Conversation  # assigns number, emits conversation.created,
    # broadcasts, runs round-robin auto-assign if inbox.config.auto_assign

async def add_message(session, conversation, *, direction, author_type, author_id,
    author_name, content, visibility="public", attachments=None, source_id=None,
    meta=None, actor, deliver=True) -> Message
    # THE single entry point for messages. Updates trackers per research:
    # inbound public: waiting_since=now if None; last_activity; reopens resolved→open
    #   (unless conversation.status=="pending" keep pending)
    # outbound public by user/agent: first_reply_at set once, waiting_since=None
    # notes/activity: last_activity only. Emits message.created (payload includes
    # conversation_id, message_id, direction, author_type). Broadcasts
    # 'message.created' to ws:{ws} and conv:{conversation_id} topics with full
    # MessageOut payload + conversation summary. If deliver and direction=="out" and
    # visibility=="public" and channel_type not in ("widget","api"): enqueue task
    # "deliver_message" {message_id}.

async def update_status(session, conversation, status, *, actor, snoozed_until=None)
async def assign(session, conversation, *, assignee_user_id=..., team_id=..., actor)
async def set_priority(...); async def add_tag/remove_tag(conversation, tag_id)
def unread_count(conversation) -> computed from agent_last_seen_at vs last inbound at
```
- `app/channels/registry.py`: `SENDERS: dict[str, Callable]` mapping channel_type →
  async sender(session, inbox, message); `@task("deliver_message")` looks up conversation
  /inbox, dispatches to SENDERS.get(channel_type) — if missing, mark message
  delivery_status="failed", delivery_error="no sender registered" (E fills SENDERS in
  wave 2). Also `register_sender(channel_type)` decorator.
- Event handler: on `workspace.created` create default widget Inbox ("Website widget",
  widget_key generated, config {accent_color "#6366f1", greeting "Hi! How can we help?",
  auto_assign True}).
- `conversation.updated` broadcast on any status/assign/priority/tag change with
  ConversationOut payload.

### APIs
- `GET/POST /inboxes` (channels:manage for POST; GET conversations:read), `GET/PATCH/DELETE
  /inboxes/{id}` (PATCH re-encrypts secrets if provided; never return secrets — response
  includes has_secrets bool + widget embed snippet for widget inboxes:
  `{"embed_snippet": "<script>…loader.js…</script>"` using settings.public_base_url).
- `GET /conversations` filters: status (multi), inbox_id, assignee ("me"|"unassigned"|user_id),
  team_id, contact_id, tag_id, priority, q (subject/contact name/email ilike);
  sort last_activity_at desc; cursor pagination; each item = ConversationListItem
  {id, number, subject, status, priority, contact {id,name,email,avatar_url}, inbox
  {id,name,channel_type}, assignee {id,name} | null, last_message_preview (first 140 chars,
  private notes excluded), last_activity_at, unread bool, tag_ids, waiting_since}.
- `POST /conversations` {contact_id, inbox_id, content, subject?} — outbound start (adds
  first out message).
- `GET /conversations/{id}` full detail incl. contact w/ attributes.
- `PATCH /conversations/{id}` {status?, snoozed_until?, priority?, assignee_user_id?
  (null to unassign), team_id?} (conversations:manage).
- `GET /conversations/{id}/messages` cursor (desc, page ~30, return asc within page ok —
  document choice), `POST /conversations/{id}/messages` {content, visibility ("public"|
  "note"), attachments?} author = current user (conversations:write).
- `POST /conversations/{id}/tags` {tag_id} / `DELETE .../tags/{tag_id}`.
- `POST /conversations/{id}/read` sets agent_last_seen_at=now.
- `GET /conversations/counts` → {open, unassigned, mine, pending, snoozed, resolved}
  for sidebar badges.

### Seed
6 conversations over 2 inboxes (default widget + an "api" inbox), varied statuses/
priorities, realistic support content (message threads 2–6 msgs incl. one private note),
one assigned to ctx.agent, tags applied, one resolved w/ CSAT-ready state.

### Tests (tests/conversations/)
ingest_inbound (new contact/existing contact/dedupe by message_source_id/reopen-on-new-
message), tracker columns matrix (waiting_since set+cleared, first_reply once, unread),
status transitions + snooze, assignment + round-robin (create inbox w/ auto_assign, 2
members, 3 conversations → distribution), list filters + cursor, authz (viewer read-only,
cross-ws 403/404), number sequence uniqueness, delivery task marks failed when no sender,
message POST with attachments, counts endpoint.

---

## Wave 1 — Agent C: AI provider layer

**Owns:** `app/models/ai_provider.py`, `app/schemas/ai_providers.py`,
`app/services/ai_providers.py`, `app/api/v1/ai_providers.py`, `app/ai/registry.py`,
`app/ai/providers/{__init__,openai_compat,anthropic,google}.py`, `app/ai/seed.py`,
`tests/ai/*`. May EDIT ONLY the `resolve_embedding_provider` hook usage inside
`app/ai/embeddings.py` if needed (keep its public functions stable).

Background: docs/research/vercel-ai.md (adapter gotchas — follow its normalization
cheat-sheet). Interfaces: `app/ai/base.py` (already written — do not modify; build to it).

### Models
- `AiProvider`: ws, kind ("openai"|"anthropic"|"google"|"openai_compatible"|"ollama"|
  "mock"), name, base_url nullable (required for openai_compatible/ollama), api_key_encrypted
  nullable, enabled bool, meta JSON. The mock provider needs no row to work, but seed one
  for visibility.
- `AiModel`: ws, provider_id FK, model_key (e.g. "claude-opus-5"), display_name, modality
  ("chat"|"embedding"), context_window int nullable, enabled bool, is_default bool
  (service enforces: max one default per (ws, modality); setting one clears others).

### Registry (`app/ai/registry.py`)
```python
def build_chat_provider(provider: AiProvider) -> ChatProvider  # kind → adapter w/
    # decrypted key + base_url; "mock" → MockChatProvider()
async def resolve_chat(session, workspace_id, model_ref: str | None) -> tuple[ChatProvider, str]
    # model_ref "provider_id:model_key" | None → ws default chat model → ("mock", "mock").
    # NEVER raises for missing config — always falls back to mock (log warning).
async def resolve_workspace_embedder(session, workspace_id) -> EmbeddingProvider | None
    # ws default embedding model → RemoteEmbedder via openai-compatible /embeddings;
    # None if not configured (caller falls back to local hash embedder).
```

### Adapters (httpx.AsyncClient, timeout 60s connect 10s; NO retries here; raise
ProviderError(status_code=, retryable=429/5xx/529) on failures; never log keys)
- `openai_compat.py` — Chat Completions API (`{base}/chat/completions`): tools via
  "tools":[{"type":"function","function":{...}}]; streaming SSE `data:` lines; tool-call
  args arrive as index-keyed fragments (id+name only in first fragment) — accumulate;
  usage in final chunk (send stream_options {"include_usage": true}); json_mode →
  response_format {"type":"json_object"}. Covers openai (default base
  https://api.openai.com/v1), openai_compatible, ollama (base http://host:11434/v1).
  Also `async def embed(texts, model)` via `{base}/embeddings`.
- `anthropic.py` — Messages API (https://api.anthropic.com/v1/messages, headers
  x-api-key + anthropic-version: 2023-06-01): system prompt as top-level `system`;
  tools [{name, description, input_schema}]; tool results = user message with
  [{"type":"tool_result","tool_use_id","content"}]; max_tokens required (default 4096
  or request.max_tokens). Streaming: SSE events message_start / content_block_start
  (type text|tool_use w/ id+name) / content_block_delta (text_delta.text |
  input_json_delta.partial_json) / content_block_stop / message_delta (stop_reason,
  usage.output_tokens) / message_stop. Map stop_reason end_turn→"stop",
  tool_use→"tool_calls", max_tokens→"length". Usage: input_tokens from message_start,
  output from message_delta. 529 overloaded → retryable ProviderError.
- `google.py` — Gemini API (`https://generativelanguage.googleapis.com/v1beta/models/
  {model}:generateContent` / `:streamGenerateContent?alt=sse`, header x-goog-api-key):
  contents [{role: "user"|"model", parts:[{text}]}], system_instruction separate; tools
  [{"functionDeclarations":[...]}]; function calls arrive COMPLETE (args object, often no
  id → generate uuid); functionResponse parts for results; finishReason STOP w/ calls →
  "tool_calls"; usageMetadata {promptTokenCount, candidatesTokenCount}.
- Known-model catalog constant `CATALOG: dict[kind, list[{model_key, display_name,
  modality, context_window}]]` — anthropic: claude-opus-5 (1M), claude-sonnet-5 (1M),
  claude-haiku-4-5 (200K); openai: gpt-4o, gpt-4o-mini, gpt-4.1, o3-mini,
  text-embedding-3-small (embedding); google: gemini-2.5-pro, gemini-2.5-flash;
  ollama/openai_compatible: empty (user supplies). Exposed via API for UI pickers.

### APIs (ai:read for GET, ai:manage for mutations)
- `GET/POST /ai/providers` (api_key write-only; GET returns masked "…abc4" last 4 +
  has_key bool), `PATCH/DELETE /ai/providers/{id}` (PATCH may rotate key),
  `POST /ai/providers/{id}/test` → {ok, message, latency_ms} (chat: 1-token "ping"
  generate on the provider's cheapest catalog model or given model_key; mock: always ok).
- `GET/POST /ai/providers/{id}/models`, `PATCH/DELETE /ai/models/{id}`,
  `POST /ai/models/{id}/set-default`.
- `GET /ai/models` flat enabled list [{id, provider_id, provider_kind, provider_name,
  model_key, display_name, modality, is_default}] — always includes a virtual entry
  {id: "mock", model_key: "mock", provider_kind: "mock", modality: "chat"} so pickers
  never come up empty. `GET /ai/catalog` → CATALOG.

### Seed (`app/ai/seed.py`)
Mock provider row + its chat model (default) + embedding noted absent (local hash used).

### Tests (tests/ai/, respx for HTTP mocking)
Adapter matrix per provider: non-stream generate (text + tool call parse), streaming
(SSE fixtures incl. split tool-arg fragments across chunks), error mapping (401→
ProviderError retryable False, 429/529→retryable True), anthropic tool_result round-trip
shape, google complete-args normalization, registry resolve fallback to mock when no
default, encryption at rest (db value != plaintext, decrypt works), default uniqueness,
masked key in API response, test endpoint w/ respx, authz (agent role 403 on POST).

---

## Wave 1 — Agent D: Knowledge / RAG + help center articles + portal

**Owns:** `app/models/{knowledge,article}.py`, `app/schemas/{knowledge,articles}.py`,
`app/services/{knowledge,articles}.py`, `app/api/v1/{knowledge,articles}.py`,
`app/api/portal.py`, `app/rag/{parsers,chunker,ingestion,tasks,retrieval,seed}.py`,
`tests/knowledge/*`.

Background: docs/research/onyx.md — follow its Stept mapping (512-token chunks, overlap 0,
neighbor expansion, RRF k=60). Embeddings via `app.ai.embeddings.embed_texts` (already
works offline via hash embedder). Uploads via `app.core.storage.get_storage()`.

### Models
- `KnowledgeSource`: ws, type ("files"|"urls"|"text"|"articles"), name, config JSON
  ({urls: [...]} for urls type), status ("idle"|"syncing"|"error"), error nullable,
  last_synced_at. The "articles" source is auto-created singleton per ws on first publish.
- `Document`: ws, source_id FK, title, uri nullable (url or storage key), mime, content_hash
  (sha256 of extracted text — skip re-embed when unchanged), status ("pending"|"processing"|
  "indexed"|"failed"), error nullable, token_count int, meta JSON. Index (ws, source_id).
- `Chunk` (tablename "chunks" — matches core pg index bootstrap): ws, document_id FK
  (ondelete CASCADE), ord int, content text, embedding EmbeddingVector nullable, meta JSON
  ({title, url, headings}), token_count. Index (ws, document_id, ord). Column NAME must be
  `content` (the pg FTS expression index targets chunks(content)).
- `ArticleCollection`: ws, name, slug (uq per ws), description, icon, ord int.
- `Article`: ws, collection_id FK nullable, title, slug (uq per ws), body (markdown),
  status ("draft"|"published"), author_id, published_at, meta JSON.

### Pipeline (app/rag/)
- `parsers.py`: `extract(filename, content: bytes, mime) -> ParsedDoc{title, text, meta}`
  for pdf (pypdf), docx (python-docx), html (bs4 → headings preserved as "## " lines),
  md/txt/csv passthrough (csv → markdown table capped 200 rows). Graceful errors.
- `chunker.py`: `chunk_text(text, *, title, target_tokens=512, min_tokens=50) ->
  list[ChunkDraft{content, ord, token_count, headings}]` — split on headings/paragraphs/
  sentences, pack to target (token estimate len//4), prefix each chunk with title line
  ("# {title}\n"), merge trailing tiny chunks. Deterministic.
- `ingestion.py`: `async def ingest_document(session, document, text)` — hash-check,
  delete old chunks, chunk, `embed_texts` in batches of 64, insert, status="indexed",
  emit document.indexed. `@task("ingest_document")` {document_id} wrapper w/ session_scope
  + status/error handling. `@task("sync_source")` {source_id}: for urls type fetch each
  URL (httpx 10s, 2MB cap, parse html) upsert Document per URL then ingest inline;
  status transitions idle→syncing→idle/error.
- `retrieval.py`:
```python
@dataclass RetrievedChunk: chunk_id, document_id, content, score, title, url|None, ord
async def search_chunks(session, workspace_id, query, *, k=8, source_ids=None,
    expand_neighbors=True) -> list[RetrievedChunk]
```
  PG path: dense top-50 `ORDER BY embedding <=> :qvec` (cast via pgvector) + lexical
  top-50 `plainto_tsquery`/`ts_rank_cd(to_tsvector('english', content))` → RRF (k=60,
  equal weights) → top-k → neighbor expansion (append ord±1 content to each result's
  content, dedupe). SQLite path: python cosine over ws chunks + token-overlap keyword
  score → same RRF. Must return same shape both paths (tests run sqlite; mark a @pytest.mark.pg
  variant exercising the SQL path).
- articles service: publish → upsert into "articles" source (Document per article, uri
  = portal slug path) + ingest inline (await, not enqueue — keeps publish→searchable
  synchronous); unpublish/delete → remove document+chunks.

### APIs
- `GET/POST /knowledge/sources` (knowledge:read/write), `GET/PATCH/DELETE /knowledge/
  sources/{id}`, `POST /knowledge/sources/{id}/sync` (enqueue sync_source),
  `POST /knowledge/sources/{id}/documents` — multipart file upload (parse now, create
  Document, enqueue ingest_document) OR json {title, content} for text paste,
- `GET /knowledge/documents` (source_id filter, status filter, OffsetPage), `GET /knowledge/
  documents/{id}` (+ first 5 chunks preview), `DELETE`, `POST /knowledge/documents/{id}/retry`.
- `POST /knowledge/search` {query, k?, source_ids?} → {results: [RetrievedChunk…],
  latency_ms} (knowledge:read) — the playground + cmd-k backend.
- `GET/POST /articles/collections`, `PATCH/DELETE /articles/collections/{id}`,
  `GET/POST /articles` (collection filter, status filter), `GET/PATCH/DELETE /articles/{id}`,
  `POST /articles/{id}/publish`, `POST /articles/{id}/unpublish`.
- Portal (public, app/api/portal.py): `GET /portal/{workspace_slug}` → {workspace
  {name, logo_url}, collections: [{name, slug, icon, description, articles: [{title, slug}]}]},
  `GET /portal/{workspace_slug}/articles/{article_slug}` → {title, body, collection,
  published_at}. Published only; 404 otherwise.

### Seed (`app/rag/seed.py`)
Source "Stept product docs" (type text) w/ 3 markdown docs — write real, useful content
about Stept itself (~500 words each): "Getting started & installing the chat widget",
"How AI agents, approvals and handoff work", "Plans, billing & refund policy (demo)".
Ingest inline. 1 collection "General" + 2 published articles (reuse doc content).
Idempotent.

### Tests (tests/knowledge/)
parser unit tests (build a docx via python-docx in-test; pdf via minimal hand-rolled pdf
bytes or pypdf writer; html w/ headings), chunker properties (deterministic, sizes,
title prefix, ord sequence), upload→drain_tasks→indexed end-to-end via API, hash skip
(re-ingest unchanged → chunk ids stable), retrieval relevance on seeded docs ("how do I
install the widget" → widget doc top-1; "refund" → billing doc), neighbor expansion,
source_ids filter, articles publish→searchable→unpublish removes, portal public access
(no auth header) + draft invisible, ws isolation on search, authz (viewer can read+search,
cannot POST).

---

## Wave 2 — Agent E: Widget public API + channels (email, Slack, Telegram)

**Owns:** `app/api/widget/{deps,boot,conversations,articles,csat}.py`,
`app/realtime/widget_ws.py`, `app/channels/{email,slack,telegram}.py` (new modules),
`app/api/channels/{email,slack,telegram}.py`, `tests/widget/*`, `tests/channels/*`.
(`app/api/widget/tours.py` belongs to agent H — do not touch.)

Consumes (wave-1, now merged): B's `app/services/conversations.py`
(ingest_inbound/add_message/update_status), B's Inbox/ContactInbox models +
`register_sender`, A's contacts + csat services, D's retrieval/articles services.

### Widget auth (deps.py)
- `resolve_inbox(session, widget_key) -> Inbox` (404 if unknown/disabled; channel_type
  must be "widget").
- `WidgetPrincipal` dataclass: workspace, inbox, contact, contact_inbox.
- Dependency `widget_auth`: header `X-Widget-Token` (JWT typ "widget" carrying ws + sub
  =contact_id + inbox) → load rows → principal. 401 on invalid.

### boot.py — POST /api/widget/boot
Body {widget_key, visitor_id?, identity?: {external_id, email?, name?, hash}}.
- Identity verification: if identity present, verify hash =
  compute_identity_hash(workspace.settings["identity_secret"], external_id); mismatch →
  403. If inbox.config.require_identity and no identity → return {require_identity: true}
  variant (no token).
- find_or_create contact (verified=True when HMAC ok), find/create ContactInbox
  (source_id = external_id or visitor_id or new uuid — return visitor_id so the widget
  persists it), touch last_seen.
- Response: {token (create_widget_token), visitor_id, contact {id,name,email},
  workspace {name, logo_url}, config (inbox public config), conversations: last 10
  summaries [{id, status, last_message_preview, last_activity_at, unread bool via
  contact_last_seen_at}], help_center_enabled bool (any published article)}.

### conversations.py (all authed by widget_auth; NOTES AND ACTIVITY NEVER EXPOSED —
filter visibility=="public")
- GET /api/widget/conversations; POST /api/widget/conversations {message, attachments?}
  → ingest_inbound on principal's inbox/contact (source_id from contact_inbox).
- GET /api/widget/conversations/{id}/messages (cursor; ownership check contact_id);
  POST …/messages {message}; POST …/read (contact_last_seen_at=now);
  POST …/typing {is_typing} → broadcast to conv topic (source "contact").
- Message shape out: {id, direction, author_type, author_name, content, attachments,
  created_at, meta.citations only (strip the rest)}.

### widget_ws.py — /ws/widget?token=
Decode widget token → join conversation_topic for each of the contact's conversations
(+ re-join on "subscribe" {conversation_id} client message after creating new conv).
Forward typing from client. Server pushes message.created / typing / conversation.updated
(already broadcast by B on conv topics).

### articles.py / csat.py
- GET /api/widget/articles?query= → published articles: query empty → collections tree;
  else D's search restricted to the "articles" source mapped back to articles
  [{title, slug, snippet}].
- GET /api/widget/articles/{slug} → {title, body, collection}.
- POST /api/widget/conversations/{id}/csat {rating 1..5, feedback?} → A's
  csat.record_response (ownership check).

### Channel adapters (app/channels/<ch>.py + inbound routers)
Register outbound senders via `@register_sender("<type>")` from app.channels.registry:
`async def send(session, inbox, message) -> None` (raise on failure — registry task marks
delivery_status; set "sent" on success — check registry semantics and keep consistent).
- **email.py**: outbound SMTP via app.services.email.send_email with
  from_override=inbox.config["address"], reply_to `reply+{conversation_id}@` domain of
  address, subject "Re: {conversation.subject or 'your conversation'}",
  headers In-Reply-To/References from last inbound meta when present. Inbound router
  POST /api/channels/email/inbound (open JSON: {to, from|from_email, subject, text,
  html?, message_id, in_reply_to?}): resolve conversation by reply+<uuid> in `to`, else
  by in_reply_to lookup (message meta), else new conversation on the email inbox matching
  `to` address; ingest_inbound (contact from `from`, message_source_id=message_id,
  strip quoted reply tails naively: split on "\nOn " + "wrote:" heuristic + "-----Original").
- **slack.py**: secrets {bot_token, signing_secret}. Inbound POST /api/channels/slack/events:
  url_verification → echo challenge; verify X-Slack-Signature (v0 HMAC of
  "v0:{ts}:{body}" with signing_secret, reject stale ts >5min); event_callback
  message events (ignore bot_id/message_changed): source_id = "{channel}:{thread_ts or ts}"
  → ingest_inbound (contact name from user id — display "Slack user {id}", meta
  {slack_user}); route inbox by team_id in config or single slack inbox per ws (find
  first enabled slack inbox — document). Outbound sender: chat.postMessage {channel,
  thread_ts} from conversation's contact_inbox.source_id split; httpx, raise on !ok.
- **telegram.py**: secrets {bot_token}, config {webhook_secret}. Inbound POST
  /api/channels/telegram/webhook/{inbox_id} (query `secret` must equal config
  webhook_secret): message updates → source_id=str(chat.id), contact name from
  first_name/username; ingest. Outbound: sendMessage chat_id=source_id. Include
  POST /inboxes/{id}/telegram/setup helper? No — document manual setWebhook in config
  UI copy (skip API).

### Tests
Widget: boot happy/HMAC-mismatch/require_identity, visitor persistence (same visitor_id
→ same contact), conversation create+reply+read+unread, NOTES INVISIBLE (create note via
B service, widget messages excludes), csat, articles search, ws-token 401s, cross-contact
ownership 403/404. Channels (respx outbound): email inbound routes by reply+uuid /
new-conversation path + outbound smtp (monkeypatch send_email), slack signature
verify (valid/invalid/stale), slack event→conversation + threaded outbound payload,
telegram webhook secret + inbound/outbound, dedupe by message_source_id, sender
registered check (deliver_message no longer fails for these types).

---

## Wave 2 — Agent F: Automation rules, outbound webhooks, reports, global search

**Owns:** `app/models/{automation,webhook}.py`, `app/schemas/{automations,webhooks,reports}.py`,
`app/automation/{engine.py,seed.py}` (+ conditions.py/actions.py as needed),
`app/services/{webhooks.py,reports.py}`, `app/api/v1/{automations,webhooks,reports,search}.py`,
`tests/automation/*`, `tests/reports/*`.

### Models
- `AutomationRule`: ws, name, event (one of EventNames values: conversation.created,
  message.created, conversation.status_changed, csat.submitted, contact.created),
  conditions JSON, actions JSON, enabled bool, ord int, created_by. Condition schema:
  [{"field": "inbox_id|channel_type|status|priority|subject_contains|content_contains|
  contact.email|contact.attributes.<k>|tag", "op": "eq|neq|contains|in|exists", "value"}]
  (AND). Actions: [{"type": "assign_user"|"assign_team"|"set_priority"|"add_tag"|
  "set_status"|"send_reply"|"send_note"|"notify_member"|"send_webhook", "params": {…}}].
- `Webhook`: ws, url, secret (plain — used for signing, generated server-side, shown once?
  store plain, return always — it's a signing secret the user needs; fine), events JSON
  list (subset of EventNames + "*"), enabled, description. `WebhookDelivery`: ws,
  webhook_id FK, event_name, payload JSON, status ("pending"|"success"|"failed"),
  response_code, error, attempts, created_at. Index (webhook_id, created_at).

### Engine (app/automation/engine.py)
- Subscribe via @on(...) for the five events (registration happens on module import;
  import engine at top of app/api/v1/automations.py so it registers with app build).
- Evaluation: load enabled rules for (ws, event) ordered by ord; hydrate context
  (conversation/contact/message from payload ids); check conditions; execute actions via
  B's services (add_message for send_reply/send_note w/ author_type "system",
  author_name "Automation"), notifications.notify for notify_member, enqueue webhook
  delivery for send_webhook. Guard: rules acting on message.created must skip messages
  authored by system/automation (loop prevention) + cap 1 execution per (rule,
  conversation, message) — track via handled ids in payload/meta.
- Webhook fan-out: also subscribe "*": for each enabled webhook where event in list or
  "*": enqueue task "deliver_webhook" {delivery_id} (create WebhookDelivery row first).
  Task posts JSON {event, workspace_id, payload, timestamp} with headers
  X-Stept-Event + X-Stept-Signature: sha256 hmac of body with secret; 10s timeout;
  success 2xx; on final failure mark failed (queue retries 3x automatically).

### Reports (app/services/reports.py + api)
GET /reports/overview?days=7|30|90 (reports:read) → {totals: {new_conversations,
resolved_conversations, resolution_rate, median_first_response_minutes,
median_resolution_minutes, csat_avg, csat_count, ai_runs, ai_resolved,
ai_resolution_rate}, by_day: [{date, new, resolved}], by_channel: [{channel_type,
count}], by_agent: [{user_id, name, resolved, median_first_response_minutes}]}.
Compute portable SQL (python medians fine). AgentRun table may not exist rows if G
unfinished — query defensively (table exists from model stub? G owns model — import
inside try/except ImportError and zero the ai_* stats if unavailable).

### Global search — app/api/v1/search.py
GET /search?q=&limit=5 → {conversations: [{id, number, subject, contact_name,
last_activity_at}], contacts: [{id, name, email}], articles: [{id, title, slug}],
documents: [{document_id, title, snippet(160), score}]} — ilike for entities, D's
search_chunks for documents. Perms: each section filtered by caller's read perms
(conversations:read, contacts:read, knowledge:read) — include only permitted sections.

### APIs
GET/POST /automations, GET/PATCH/DELETE /automations/{id}, POST /automations/{id}/toggle,
POST /automations/reorder {ordered_ids} (automations:read/manage). GET/POST /webhooks,
PATCH/DELETE /webhooks/{id}, GET /webhooks/{id}/deliveries (OffsetPage), POST
/webhooks/{id}/test (send sample payload now) (webhooks:manage). Reports + search above.

### Seed (app/automation/seed.py)
2 rules ("VIP tagging": contact.attributes.plan eq enterprise → add_tag vip +
set_priority high, on conversation.created; "Away autoreply" example disabled), 1 webhook
disabled example. Idempotent.

### Tests
Rule evaluation matrix (conditions ops incl. contact.attributes, actions incl.
send_reply author system + loop prevention), ordering, toggle, webhook delivery task
(respx: success, 500→retry→failed, signature correct), deliveries listing, reports math
on seeded fixtures (build 6 conversations w/ known timestamps → assert medians/rates),
search across entities + permission filtering, authz.

---

## Wave 2 — Agent G: AI agent engine (tools, runs, APPROVAL GATES, traces, copilot)

**Owns:** `app/models/{agent.py,agent_run.py}`, `app/schemas/agents.py`,
`app/agents/{tools.py,engine.py,copilot.py,tasks.py,seed.py}`,
`app/api/v1/{agents,agent_runs,approvals}.py`, `tests/agents/*`.

Background (MANDATORY): docs/research/claude-agent-sdk.md — the defer-and-resume
state machine, allow/deny result union, lease+reaper; docs/research/vercel-ai.md —
denial-as-tool-result. Providers via app/ai/registry.resolve_chat (wave-1 C, merged);
retrieval via app/rag/retrieval.search_chunks (wave-1 D); conversations via B's
services (add_message, update_status).

### Models
- `Agent` (agent.py): ws, name, description, avatar_emoji, status ("draft"|"live"|"off"),
  model_ref str nullable ("provider_id:model_key"; null → ws default → mock),
  system_prompt text, temperature float nullable, settings JSON
  {retrieval: {enabled: true, k: 6, source_ids: null}, handoff_message: str,
  guardrails: {max_tool_calls: 8, require_citations: false}}, tools JSON:
  [{"key": "search_knowledge"|"handoff_to_human"|"tag_conversation"|
  "close_conversation"|"collect_contact_details"|"note_to_team"|"action:<action_id>",
  "policy": "auto"|"require_approval"|"disabled"}] — unlisted builtin keys use
  DEFAULT_POLICIES = {search_knowledge: auto, handoff_to_human: auto, note_to_team: auto,
  collect_contact_details: auto, tag_conversation: auto, close_conversation:
  require_approval, action:*: require_approval}.
- `CustomAction` (agent.py): ws, name (slug-ish), description (shown to LLM), method,
  url (may contain {param} templates), headers JSON (values encrypted via
  encrypt_secret at rest — decrypt only at execution), body_template str (json with
  {param} placeholders), params_schema JSON (JSON Schema object exposed to the LLM),
  timeout_s int default 10, allowed_domains derived: host of url is the only allowed
  host (enforce at execution after templating).
- `AgentRun` (agent_run.py): ws, conversation_id (GUID), agent_id FK,
  trigger_message_id GUID nullable, status ("queued"|"running"|"awaiting_approval"|
  "completed"|"failed"|"handed_off"|"canceled"), error nullable, input_tokens int,
  output_tokens int, started_at, finished_at, lease_expires_at, messages_snapshot JSON
  (provider ChatMessage list, serialized dicts — for pause/resume), pending_tool_call
  JSON nullable {id, name, input, approval_request_id}, citations JSON (last search
  results for [n] mapping), reply_message_id GUID nullable.
- `AgentStep`: run_id FK indexed, ord, kind ("llm_call"|"tool_call"|"tool_result"|
  "approval_request"|"approval_decision"|"final_reply"|"guardrail"|"error"|"handoff"),
  name nullable, input JSON, output JSON, latency_ms int nullable, input_tokens,
  output_tokens, created_at.
- `ApprovalRequest`: ws, run_id FK, conversation_id, agent_id, tool_key, tool_input JSON,
  status ("pending"|"approved"|"rejected"|"expired"), requested_at, expires_at
  (+24h), decided_by GUID nullable, decided_at, note nullable. Index (ws, status).

### Engine semantics (engine.py + tasks.py)
- Trigger: @on(EventNames.CONVERSATION_CREATED): if conversation's inbox
  config.ai_agent_id → set conversation.ai_agent_id + status "pending" (activity
  message "Sage joined the conversation" style). @on(MESSAGE_CREATED): payload direction
  "in" + visibility public + conversation.ai_agent_id + conversation.status=="pending"
  + no run active (queued/running/awaiting_approval) for the conversation → create
  AgentRun(queued) + enqueue "execute_agent_run" {run_id}.
- execute_agent_run task: session_scope; guard status in (queued, running w/ expired
  lease → treat as crash-resume from snapshot? v1: expired running → mark failed);
  set running + lease now+120s. Build/extend messages: system = compose(system_prompt,
  workspace name, tool usage guidance, retrieval instruction "cite sources as [n]"),
  history = conversation public messages (contact→user, others→assistant) capped last 30.
  Loop (max guardrails.max_tool_calls, default 8):
  provider, model_key = await resolve_chat(session, ws, agent.model_ref);
  result = await provider.generate(ChatRequest(model=model_key, messages, tools=
  enabled ToolSpecs, temperature)); record llm_call step (+usage aggregate on run).
  - result.tool_calls → process FIRST call only per iteration (append assistant msg w/
    all calls but execute sequentially; simplest correct: if multiple, handle first,
    append tool-error "one tool at a time" for the rest).
    policy disabled → tool_result step {"error": "tool disabled"} appended, continue.
    policy auto → execute via tools.py registry, steps tool_call+tool_result, continue.
    policy require_approval → persist ApprovalRequest + pending_tool_call +
    messages_snapshot (serialize ChatMessages incl. the assistant tool_call msg),
    status awaiting_approval, step approval_request; notify ALL members w/ ai:approve
    perm (notifications.notify type "approval", link "/ai/approvals") + broadcast ws
    topic "approval.pending" {approval_id, conversation_id, agent name, tool_key,
    tool_input} + emit approval.requested; RETURN (paused — survives restarts).
  - result.content (no calls) → final: post-process citations: map [n] markers against
    run.citations (from last search_knowledge result); n beyond range stripped.
    add_message(conversation, direction "out", author_type "agent", author_id agent.id,
    author_name agent.name, content, meta {agent_run_id, citations}) via B; step
    final_reply; status completed (+ reply_message_id, finished_at); emit
    agent_run.completed; broadcast "agent_run.updated". Empty content → execute
    handoff_to_human fallback.
  - Loop exhausted → guardrail step + handoff fallback.
  - ProviderError retryable → raise (queue retries); non-retryable → status failed +
    error + handoff fallback (conversation must never be stranded: status pending →
    open + activity note "AI agent failed — waiting for a teammate").
- decide_approval(session, approval, *, approved, decided_by, note): guard pending
  (+expiry check → expired). Record decision step; approved → re-enqueue
  execute_agent_run (engine restores messages_snapshot, executes pending tool, appends
  tool_result, continues loop); rejected → tool_result {"error": "Rejected by {name}:
  {note}"} appended to snapshot, re-enqueue (LLM sees denial and adapts — denial-as-
  tool-result per research). Broadcast approval.decided + emit event. Approvals listing
  auto-expires overdue pending rows (lazy, on list/decide).
- tools.py registry: ToolSpec builders + executors
  `async def execute(session, run, conversation, agent, name, input) -> dict`:
  - search_knowledge{query} → search_chunks(k from settings, source_ids) → store
    citations on run → {"results": [{"n": i+1, "title", "content": first 500 chars,
    "url"}]}
  - handoff_to_human{reason?} → conversation status "open" via B update_status (actor
    agent), activity message "Handed off to team{: reason}", run status handed_off,
    signals loop stop → {"ok": true, "note": "conversation handed to a human"}
  - tag_conversation{tag_name} → existing tag lookup (no auto-create) + B add_tag →
    {"ok"|"error": "unknown tag"}
  - close_conversation{closing_message?} → optional final reply then status resolved →
    {"ok": true}
  - collect_contact_details{email?, name?} → update contact fields if empty/changed,
    activity note → {"ok": true}
  - note_to_team{text} → add_message visibility "note", author agent → {"ok": true}
  - action:<id> → validate input against params_schema (jsonschema-lite: required keys
    + type checks manually — no new deps), template url/body ({param} substitution,
    url-encode in url), enforce final host == original url host, httpx request w/
    timeout, response {"status": code, "body": first 2000 chars}. Decrypt headers at
    call time only.
- copilot.py: `async def suggest_reply(session, conversation, member_name) ->
  {content, citations}` — retrieval on last ≤3 contact messages + single generate with
  drafting system prompt; POST /ai/copilot/suggest {conversation_id}
  (conversations:write) → suggestion (never auto-sends).

### Sandbox test endpoint
POST /ai/agents/{id}/test {message, history?: [{role, content}]} (ai:manage) — runs the
SAME loop against an ephemeral in-memory context (no conversation writes; handoff/close/
tag/note become dry-run results {"dry_run": true, ...}; search + actions real but
actions still domain-enforced) → {reply, steps: serialized, citations}. Implement via
engine mode flag, not a fork of the loop.

### APIs
GET/POST /ai/agents (ai:read/manage), GET/PATCH/DELETE /ai/agents/{id}, POST test (above).
GET/POST /ai/actions, PATCH/DELETE /ai/actions/{id} (ai:manage; headers write-only
masked on read), POST /ai/actions/{id}/test {params} → dry HTTP call result.
GET /ai/runs (filters agent_id/conversation_id/status; OffsetPage; summary rows),
GET /ai/runs/{id} → {run, steps[]}. GET /ai/approvals?status=pending (ai:approve
or ai:read? decide: ai:approve to act, listing needs ai:read — use ai:approve for both
per contract simplicity), POST /ai/approvals/{id}/decide {approved, note?} (ai:approve).

### Seed (app/agents/seed.py)
Agent "Sage" live, model_ref null (→ mock), retrieval on, tools defaults (+
close_conversation require_approval), system prompt referencing Stept docs; wire it as
default: set demo widget inbox config.ai_agent_id = sage.id (mutate inbox config via B
model directly). Idempotent.

### Tests (tests/agents/) — mock provider directives drive everything ([[tool:...]])
Full happy path: contact msg w/ [[tool:search_knowledge {"query":"widget"}]] on seeded
docs → run completed, reply contains citation [1] + meta.citations non-empty, steps
sequence llm_call→tool_call→tool_result→llm_call→final_reply. APPROVAL GATE: agent w/
close_conversation policy require_approval + directive → run awaiting_approval +
ApprovalRequest pending + notification created; decide approve → run completes +
conversation resolved; decide reject → run completes with polite reply, conversation
NOT resolved; expiry → expired + resume-as-rejected. Disabled tool → error result +
model continues. handoff → status open + run handed_off. Guardrail max_tool_calls
loop (script many directives) → handoff. Custom action: respx endpoint + templating +
host enforcement (redirect/other-host blocked) + schema validation error. Copilot
suggestion w/ citations. Sandbox test endpoint dry-run (no conversation mutations).
Trigger wiring: conversation created on inbox w/ ai_agent_id → pending + run on first
contact message; human takeover (status open) stops further runs. Provider failure
(patch resolve_chat to raise) → conversation open + activity note. Authz: agent role
can approve (ai:approve), viewer cannot; ai:manage required for agent CRUD.

---

## Wave 2 — Agent H: DAP tours backend

**Owns:** `app/models/tour.py`, `app/schemas/tours.py`, `app/services/tours.py`,
`app/dap/seed.py`, `app/api/v1/tours.py`, `app/api/widget/tours.py`, `tests/tours/*`.

### Models
- `Tour`: ws, name, description, status ("draft"|"live"|"paused"), trigger JSON
  {"type": "manual"|"url_match", "url_pattern": str? (glob-ish: * wildcard)}, audience
  JSON {"type": "all"} | {"type": "filters", "filters": [segment-filter schema from A]},
  steps JSON [{"id": str, "selector": str, "title": str, "body": markdown, "placement":
  "auto"|"top"|"bottom"|"left"|"right"}], theme JSON {accent}, version int (increment
  on steps change), created_by. `TourEvent`: ws, tour_id FK, contact_id GUID nullable,
  event ("started"|"step_viewed"|"completed"|"dismissed"), step_index int nullable,
  created_at, meta. Index (tour_id, created_at).

### APIs
- App: GET/POST /tours (tours:read/manage), GET/PATCH/DELETE /tours/{id}, POST
  /tours/{id}/publish, POST /tours/{id}/pause, GET /tours/{id}/stats → {starts,
  completions, dismissals, completion_rate, steps: [{index, title, viewed, drop_off}]},
  POST /tours/recorder-token → {token: create_recorder_token(ws, user), expires_days: 7}
  (tours:manage).
- Widget/public (implement OWN light auth — do NOT depend on app/api/widget/deps.py):
  `_resolve(session, widget_key) -> (workspace_id, inbox)` via Inbox.widget_key;
  optional `X-Widget-Token` decoded via security.decode_token("widget") for contact_id.
  - GET /api/widget/tours?widget_key=&url= → live tours where trigger.url_match matches
    url (fnmatch) or manual excluded; audience filters evaluated against contact when
    token present (A's segments.apply-filter logic reuse — import
    app.services.segments helper if it exposes one, else implement minimal matcher);
    exclude tours the contact already completed/dismissed (TourEvent lookup). Response
    [{id, name, steps, theme, version}].
  - POST /api/widget/tours/{id}/events {event, step_index?} (widget_key required,
    contact from token optional) → record TourEvent. started/step_viewed/completed/
    dismissed only.
  - POST /api/widget/tours/recorder {token, name, url_pattern?, steps: [{selector,
    title?, body?}]} — recorder token auth (decode "recorder" typ; ws from claim; check
    user still member w/ tours:manage) → create draft Tour (fill default titles "Step
    N"), version 1 → {id, name, app_url: settings.app_base_url + "/tours/" + id}.

### Seed (app/dap/seed.py)
1 live tour "Welcome to Stept" (3 steps w/ selectors matching the Stept dashboard:
[data-tour="inbox"], [data-tour="knowledge"], [data-tour="ai"]), url_match "*/inbox*";
1 draft. A few TourEvents for stats. Idempotent.

### Tests (tests/tours/)
CRUD + publish/pause + version bump on steps change, stats math from seeded events,
recorder-token flow (mint → create draft via public endpoint → 401 on bad/expired token
→ member-without-perm 403), widget delivery: url matching, audience filters (attributes
plan), completed/dismissed exclusion, event recording validation, authz + ws isolation.

---

# WAVE 3 — Frontend features (3 parallel agents)

Shared frontend rules (all three agents): read `CLAUDE.md` §Frontend. Stack is React 19 +
TS strict + TanStack Query v5 + react-router v7 + shadcn (`@/components/ui/*`) + Tailwind
v4 tokens (dark mode must work) + sonner + react-hook-form/zod + lucide + recharts.
API via `@/api/client` (`api.get/post/patch/delete`, `ws(path)` for workspace-scoped URLs,
throws `ApiError`), realtime via `@/api/ws` (`useRealtime(type, handler)`, `sendRealtime`).
Auth/permissions via `@/stores/auth` (`useAuthStore`, `useCurrentMembership`, `useHasPerm`,
`currentWorkspaceId`). Types: **run `make types` yourself first** (exports OpenAPI →
`src/api/schema.d.ts`) then import `components['schemas']['XxxOut']` via a local
`type Xxx = components['schemas']['XxxOut']` alias where helpful — but hand-written
interfaces matching the backend are acceptable when they read cleaner; keep them in the
feature's `api.ts`. Every page module is a pre-registered lazy route exporting
`Component` as default (fill the placeholder files listed per agent). Query keys start
`[<area>, workspaceId, ...]`; mutations invalidate precisely; use `sonner` toast for
success/error. Gate mutating UI on `useHasPerm(...)`. Ship vitest tests
(`@/test/helpers` → `renderApp`, `mockFetch`) for non-trivial logic/components.

STRICT ownership: each agent owns only its `src/features/<areas>/**` + its placeholder
page files. NEVER edit `src/router.tsx`, `src/main.tsx`, `src/api/*`, `src/stores/*`,
`src/components/ui/*`, `src/components/layout/*` (except FE1 may add an inbox-specific
layout INSIDE its feature folder), `package.json`, `index.css`. Regenerate `schema.d.ts`
via `make types` (needs backend importable — it is). Three agents run concurrently on
different feature folders. Definition of done per agent: `cd frontend && pnpm tsc
--noEmit && pnpm vitest run <your test globs>` green, and `pnpm build` succeeds
(run it once at the end — it type-checks + bundles everything).

## Wave 3 — Agent FE1: Inbox (the flagship screen)
**Owns:** `src/features/inbox/**` (fill `pages/InboxPage.tsx`), `src/features/contacts/**`
(fill `pages/ContactsPage.tsx`, `pages/ContactDetailPage.tsx`). (Contacts here because the
inbox contact panel shares its API/hooks.)
- **InboxPage** = 3-pane: (1) filter rail (status tabs w/ live counts from
  `GET /conversations/counts`, assignee me/unassigned/all, inbox filter, priority);
  (2) conversation list (infinite cursor scroll of `GET /conversations`, each row = avatar,
  contact name, subject/preview, channel icon, unread dot, waiting-since relative time,
  priority pill, tags); (3) thread pane (message timeline w/ public vs note styling —
  notes yellow, activity centered muted, agent/AI messages badged, citations rendered as
  footnote links from `meta.citations`) + composer (public reply / private note toggle,
  attachment upload via `POST /w/{ws}/files` then send, canned-response `/` picker from
  `GET /canned-responses`, **"Suggest reply" (copilot)** button → `POST /ai/copilot/suggest`
  inserts draft) + right rail (contact card: attributes, tags add/remove, recent
  conversations, notes; conversation actions: assign to member/team, status
  open/pending/snoozed/resolved, priority, tags). Realtime: `useRealtime('message.created')`
  appends to the open thread + bumps list; `conversation.updated` patches list/detail;
  `typing` shows indicator; mark read on open (`POST .../read`). Optimistic send.
- **ContactsPage**: searchable, filterable (segment dropdown) table of contacts (TanStack
  virtual ok), row → ContactDetailPage. **ContactDetailPage**: profile (editable
  attributes), tags, timeline (events + conversations), notes.
- Tests: conversation-list rendering + filter switching, message bubble variants (note vs
  public vs citation), composer note/public toggle, copilot insert, realtime append
  handler (dispatch a fake message.created → appears), permission gating (viewer can't
  send). ~12+ tests.

## Wave 3 — Agent FE2: Knowledge, AI providers/models, Agent builder + approvals + traces, Help center
**Owns:** `src/features/knowledge/**` (fill KnowledgePage, SourceDetailPage, ArticlesPage,
SearchPlaygroundPage), `src/features/ai/**` (fill AiOverviewPage, ProvidersPage, AgentsPage,
AgentBuilderPage, ApprovalsPage, RunsPage, RunDetailPage).
- **Knowledge**: sources list + create (files upload / urls / text), source detail
  (documents table w/ status badges, re-sync, retry failed), ArticlesPage (collections +
  articles CRUD w/ a markdown editor — textarea + preview is fine, publish/unpublish),
  **SearchPlaygroundPage** (query box → `POST /knowledge/search` → ranked chunks w/ scores,
  titles, source links — showcases the RAG).
- **AI**: ProvidersPage (add provider by kind, key entry (write-only), enable models from
  catalog, set default, test-connection button → latency/ok). **AgentsPage** (list, status
  live/draft/off). **AgentBuilderPage** (THE key screen: name/avatar/model picker from
  `GET /ai/models`, system prompt editor, retrieval toggle + k + source scoping, **per-tool
  policy matrix** auto/require_approval/disabled for each builtin tool + custom actions,
  guardrails (max tool calls), and a **live test sandbox** panel calling
  `POST /ai/agents/{id}/test` that renders the step trace + reply — use the mock provider
  directive hint in placeholder text so users can try `[[tool:search_knowledge {"query":"..."}]]`).
  Custom actions CRUD. **ApprovalsPage** (pending approvals queue from `GET /ai/approvals`,
  each shows agent, conversation link, tool + input, approve/reject w/ note; realtime
  `useRealtime('approval.pending')` prepends; `approval.decided` removes). **RunsPage** +
  **RunDetailPage** (run list w/ status, RunDetail = full AgentStep timeline: llm calls,
  tool calls/results, approvals, citations, token usage).
- Tests: provider add + model enable flow, agent builder tool-policy matrix state, sandbox
  trace render from a mocked test response, approvals approve/reject mutation + realtime
  prepend, run trace step rendering, search playground results. ~12+ tests.

## Wave 3 — Agent FE3: Contacts-segments? no — Automation, Tours builder, Reports, Settings/Team/RBAC
**Owns:** `src/features/automation/**` (AutomationPage), `src/features/tours/**` (ToursPage,
TourEditorPage), `src/features/reports/**` (ReportsPage), `src/features/settings/**`
(SettingsLayout + section panels).
- **AutomationPage**: rules list (enabled toggle, reorder), rule editor (event select →
  condition builder rows [field/op/value] → action builder rows [type/params]); webhooks
  sub-tab (CRUD, deliveries log, test). Use the same condition schema as segments.
- **Tours**: ToursPage (list, status, stats sparkline), **TourEditorPage** (steps list
  editor — selector, title, body markdown, placement; reorder; live preview note;
  publish/pause; "connect recorder" → shows recorder token from
  `POST /tours/recorder-token` + extension install hint).
- **ReportsPage**: dashboard from `GET /reports/overview?days=` — KPI stat tiles
  (new/resolved/resolution-rate/first-response/CSAT/AI-resolution), by-day area chart,
  by-channel bar, by-agent table. Use **recharts** + the dataviz skill palette; must look
  polished in light & dark.
- **Settings** (SettingsLayout w/ sub-nav routed by `:section`): Workspace (name/logo),
  Members (invite, role change, remove — RBAC-gated), Roles (custom role editor w/
  permission catalog from `GET /roles/catalog`), API keys (create w/ scopes, reveal once,
  revoke), Audit log (filterable table), Profile (name, password change), Channels
  (inboxes list w/ per-channel config + widget embed snippet copy). Gate every section on
  the relevant permission.
- Tests: condition/action builder add-row + serialize, tour step editor reorder, reports
  KPI render from mocked overview, member role change mutation gated by perm, api-key
  create/reveal flow, roles permission toggle. ~12+ tests.

# WAVE 4 — Embeddable widget + Chrome tour-recorder extension

Runs in parallel with Wave 3 (disjoint packages). Both talk only to the already-merged
public backend APIs under `/api/widget/*` (see backend/app/api/widget/*.py for exact
request/response shapes — match them). Neither imports from `frontend/`.

## Wave 4 — Agent W1: Embeddable widget (`widget/` package)
**Owns:** everything under `widget/` (package.json exists — deps: preact, vite, vitest,
typescript; add a `build` + `test` script and `vite.config.ts`). Two build outputs into
`widget/dist/` (served by the backend at `/widget-assets/` — see backend/app/main.py
StaticFiles mount): (1) `loader.js` (the script sites embed) and (2) the iframe app
(`app.html` + bundled JS/CSS).

Architecture (Intercom-style, from docs/research/chatwoot.md widget section):
- **loader.ts → loader.js** (plain TS, no framework, tiny): reads `window.SteptSettings`
  ({workspaceKey, identity?: {external_id, email?, name?, hash}, apiBase?}). Creates a
  launcher button (fixed corner bubble) + an iframe pointing at
  `{apiBase or script-origin}/widget-assets/app.html#<serialized boot params>`. Manages
  open/close, unread badge, and a **postMessage bridge** between host page and iframe
  (messages: `stept:ready`, `stept:resize`, `stept:unread`, `stept:open`, `stept:close`,
  and — critically — `stept:tour:start`/`stept:tour:event` for DAP). Exposes
  `window.Stept` API: `Stept('boot', settings)`, `Stept('open')`, `Stept('close')`,
  `Stept('shutdown')`, `Stept('show'|'hide')`, `Stept('startTour', tourId)`. Queue-style
  shim so calls before load are replayed.
- **The DAP tour player runs in the HOST page (not the iframe)** — it must highlight host
  DOM elements. loader.ts contains a lightweight tour player: given a tour
  {steps:[{selector,title,body,placement}]}, it renders a tooltip/spotlight overlay
  positioned against `document.querySelector(step.selector)`, Next/Back/Done controls,
  and posts `stept:tour:event` (started/step_viewed/completed/dismissed) which the loader
  relays to `POST /api/widget/tours/{id}/events`. On boot (and on SPA url changes) it
  calls `GET /api/widget/tours?widget_key=&url=<location.href>` and auto-starts the first
  eligible tour (respecting already-seen exclusion the backend applies).
- **iframe app (Preact)**: the messenger. Screens: conversation list (home), a
  conversation thread (message bubbles: contact right, agent/AI left w/ avatar; public
  only; citations rendered as links; typing indicator), composer (text + attachment via
  `POST /api/widget/files`? — check: widget uses conversation POST with attachments;
  if no widget file endpoint exists, support text-only and note it), a help-center browser
  (search + article read via `GET /api/widget/articles`), and a pre-chat/identity gate
  when boot returns require_identity. Boot via `POST /api/widget/boot`; persist the
  returned `visitor_id` in localStorage (key per workspaceKey) and reuse on next boot.
  Realtime via `/ws/widget?token=` (append incoming message.created, show typing). Theme
  from boot `config` (accent_color, greeting). Clean, modern, mobile-friendly, self-
  contained CSS (no external fonts/CDN).
- A small `widget/src/api.ts` typed client (fetch-based, bearer widget token) + an
  `api-client.test.ts` and a couple of component tests (vitest + preact; jsdom). Keep it
  light — aim ~8 tests. Also provide `widget/demo.html` (a standalone page embedding the
  loader against localhost:8600) for manual testing + e2e.
- `pnpm --filter @stept/widget build` must emit `widget/dist/{loader.js,app.html,...}`.
  Configure Vite for two entry points (library build for loader.js as an IIFE; separate
  app build). `pnpm --filter @stept/widget test -- --run` green.

## Wave 4 — Agent W2: Chrome MV3 tour-recorder extension (`extension/` package)
**Owns:** everything under `extension/` (package.json exists — preact, vite, typescript).
Build to `extension/dist/` (loadable unpacked).
- **manifest.json** (MV3): name "Stept Tour Recorder", permissions [activeTab, scripting,
  storage], action popup, a content script injectable on the active tab.
- **Popup (Preact)**: paste a recorder token (from the dashboard's Tours → connect
  recorder; token is a "recorder" JWT) + optional apiBase (default http://localhost:8600),
  stored via chrome.storage. Buttons: Start recording / Stop & save. On start, tells the
  content script to begin; shows a running list of captured steps (editable title/body,
  delete, reorder). On save, POST `/api/widget/tours/recorder` {token, name, url_pattern?,
  steps:[{selector,title?,body?}]} → shows the returned app_url link.
- **Content script**: on record mode, listens for clicks; for each clicked element compute
  a **robust, stable CSS selector** (prefer [data-tour], then id, then a short unique
  path with :nth-of-type fallback — implement a small selector generator + uniqueness
  check via querySelectorAll length===1) and send {selector, textHint} to the popup
  (via chrome.runtime messaging). Visual affordance (outline on hover while recording).
- Selector-generator unit tests (vitest, jsdom) — this is the testable core; aim ~8 tests
  covering data-tour preference, id, nth-of-type disambiguation, uniqueness.
- README.md in extension/ with load-unpacked instructions.
- `pnpm --filter @stept/extension build` emits `extension/dist/` with manifest + popup +
  content script; `pnpm --filter @stept/extension test -- --run` green (add test script).

# WAVE 5 — e2e journeys, migration, docs, release (orchestrator)
Playwright journeys (auth/onboarding, widget↔inbox realtime, KB ingest→AI answer w/
citations, approval-gate decision, tour create→play), alembic squashed initial migration,
docs polish, final full verify, merge to master.

---

# Wave 3 — Frontend feature agents (FE1, FE2, FE3)

Shared rules: React 19 + TS strict, shadcn components from `@/components/ui/*` ONLY (61
vendored — includes chat primitives message.tsx / message-scroller.tsx / attachment.tsx,
resizable.tsx panels, chart.tsx for recharts, empty.tsx, field.tsx, spinner.tsx, kbd.tsx,
combobox.tsx, sidebar.tsx). Data via TanStack Query + `api`/`ws()` from `@/api/client`
(query keys `[area, workspaceId, …]`), realtime via `useRealtime(type, handler)` from
`@/api/ws` (server pushes documented per domain below), permissions via
`useHasPerm('perm')` from `@/stores/auth` (hide/disable UI the user can't use), toasts
via sonner, forms react-hook-form+zod, dates via `@/lib/format`. Generated API types:
`import type { paths, components } from '@/api/schema'` — prefer
`components['schemas']['X']`; hand-write minimal local interfaces only when generation
lags. EVERY list needs: loading skeletons, empty state (empty.tsx with guidance CTA),
error state with retry. Dark mode must look right (tokens only). Key flows keyboard-
accessible. Feature dirs are yours alone: `src/features/<area>/{api.ts,hooks.ts,
components/,pages/}` — page files are pre-registered in src/router.tsx (NEVER edit
router.tsx, main.tsx, App shell, package.json, components/ui/*). Each agent adds vitest
tests (renderApp + mockFetch from `@/test/helpers`) for its critical components/hooks —
aim 10+ per agent. Verify: `cd frontend && pnpm tsc --noEmit && pnpm test -- --run &&
pnpm build` (fix everything you broke).

## FE1 — Inbox (the flagship screen)

**Owns:** `src/features/inbox/**`. Routes: /inbox/:conversationId?.

3-pane layout via resizable panels: (1) conversation list pane — status tabs w/ counts
from GET /conversations/counts (Open, Mine, Unassigned, Pending [AI], Snoozed, Resolved),
filter popover (inbox, priority, tag, assignee), search input (q), infinite cursor list
(useInfiniteQuery), rows: contact name/avatar-initials, preview, time (timeAgo), unread
dot, priority flag, channel icon, AI badge when status pending; (2) thread pane —
header (contact name, channel, copy conversation number, status/priority/assignee/team
controls, snooze w/ date popover, resolve/reopen buttons), message scroller (grouped by
day, direction-aligned bubbles, notes rendered distinctly [amber tint + lock icon],
activity lines centered/muted, attachments (image preview / file chip w/ size),
citations in agent messages as numbered chips linking meta.citations urls, delivery
status/error on outbound), composer: Reply/Note tabs, textarea (Enter sends,
Shift+Enter newline), canned-response slash-popup (type "/" → filter shortcuts → insert
content w/ {{contact.name}} substituted), attachment upload (POST /files then attach),
typing indicator emission (ws send typing debounced), "AI Copilot suggest" button →
POST /ai/copilot/suggest → editable draft inserted into composer w/ citation chips;
(3) context pane — contact card (name/email/attrs, link to /contacts/:id), conversation
attributes, tags editor (combobox add/remove), pending approval card when an
approval.pending arrives for this conversation (Approve/Reject inline w/ note →
POST /ai/approvals/{id}/decide), recent conversations of contact.

Realtime: message.created (append/invalidate + list bump + sound? skip sound),
conversation.updated (update row + open thread), typing (show "… is typing" in thread +
list), presence.state/presence.changed (green dot on assignee avatars), approval.pending
(toast + context card + invalidate approvals), agent_run.updated (refresh AI badge).
Mark read: POST /conversations/{id}/read on open/focus. New-conversation button (pick
contact + inbox → POST /conversations).

Tests: list row rendering states (unread/priority/AI), composer canned-insertion +
Enter-to-send, status change optimistic flow, approval card decide calls, realtime
handler updates cache (simulate handler call).

## FE2 — Knowledge, AI, Help Center

**Owns:** `src/features/knowledge/**`, `src/features/ai/**`. Routes: /knowledge,
/knowledge/sources/:sourceId, /knowledge/articles, /knowledge/search, /ai, /ai/providers,
/ai/agents, /ai/agents/:agentId, /ai/approvals, /ai/runs, /ai/runs/:runId.

Knowledge: sources overview (cards w/ type icon, doc counts, status badge incl. error
tooltip, sync button firing POST sync + poll/refetch), add-source dialog (tabs: Upload
files [multi-file dropzone → POST documents multipart, per-file progress/status], Add
URLs [textarea one-per-line → source config], Paste text [title+markdown]); source
detail: documents table (status chips w/ error popover, retry, delete, token counts);
Search playground: query box → POST /knowledge/search → ranked results w/ score bars,
content preview, doc link — sell the RAG quality. Articles: collections sidebar (CRUD,
icon picker w/ emoji), article list (status filter), editor page: title, collection
select, markdown textarea w/ live preview split (render via marked? NO new deps — write
a tiny md renderer or reuse a util: simple regex-based renderer acceptable, or reuse
widget approach — keep minimal headings/bold/l
