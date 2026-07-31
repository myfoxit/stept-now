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

## Wave 2 contracts (E channels, F automation/reports, G agent engine, H tours)
Written at wave-2 kickoff — see PLAN.md.
