# Onyx (formerly Danswer) — Architecture Research for Stept

Source: https://github.com/onyx-dot-app/onyx @ `5e2cf03` (2026-07-31), shallow clone under scratchpad `repos/onyx`.
All paths below are relative to `backend/` in that repo. Onyx is the leading OSS enterprise RAG platform:
FastAPI + Postgres (SQLAlchemy/Alembic) + Vespa (hybrid index) + Redis + Celery + separate "model server" for
embedding/rerank inference + Next.js web app. Licensed MIT (core) with `ee/` enterprise dir.

## 1. Architecture map

Top-level services (from `backend/supervisord.conf`, `deployment/`): API server (FastAPI `onyx/main.py`),
Celery workers (primary/light/heavy/docfetching/docprocessing) + beat, Slack bot (`onyx/onyxbot/`),
model server (`model_server/` — embedding + legacy rerank HTTP service), Vespa, Postgres, Redis, MinIO file store.

`backend/onyx/` modules that matter:

| Module | Role |
|---|---|
| `connectors/` | ~60 source connectors + framework (`interfaces.py`, `models.py`, `factory.py`, `registry.py`, `connector_runner.py`) |
| `indexing/` | `chunker.py`, `embedder.py`, `indexing_pipeline.py`, `content_classification.py`, `vector_db_insertion.py`, staged `chunk_batch_store.py` |
| `document_index/` | Index abstraction `interfaces_new.py`; backends `vespa/` (primary) and `opensearch/` (new) |
| `context/search/` | Search pipeline: `pipeline.py`, `retrieval/search_runner.py`, `preprocessing/access_filters.py`, `models.py` |
| `chat/` | `process_message.py`, `llm_loop.py`, `citation_processor.py`, `emitter.py`, `stream_buffer.py` (Redis resumable streams), `compression.py` |
| `llm/` | `interfaces.py` (LLM ABC), litellm-backed impl (`litellm_singleton.py`, `cost.py`), factory |
| `tools/` | Tool framework + `tool_implementations/`: search, web_search, open_url, images, python, bash, file_reader, memory, custom (OpenAPI), mcp, knowledge_graph, coding_agent |
| `background/celery/` | Task queues, `tasks/beat_schedule.py`, docfetching/docprocessing split |
| `db/` | SQLAlchemy models (`models.py`, ~5k lines), per-domain query modules |
| `access/` | Document ACL model (`models.py: ExternalAccess`) |
| `secondary_llm_flows/` | Small LLM calls: `query_expansion.py`, `source_filter.py`, `time_filter.py`, `chat_session_naming.py`, `document_filter.py` |
| `server/` | API routers; streaming packet models in `server/query_and_chat/streaming_models.py` |
| `federated_connectors/` | Query-time live search of sources (e.g. Slack) without indexing |
| `file_processing/`, `file_store/` | Extraction (PDF/docx/…), image summarization, S3/MinIO-backed store |
| `natural_language_processing/` | Tokenizers, embedding-model client, `english_stopwords.py` |
| `deep_research/`, `kg/`, `mcp_server/`, `onyxbot/` | Deep-research agent, knowledge graph, MCP server, Slack bot |
| `ee/onyx/` | Enterprise: external permission sync, user groups, RBAC |

Key architectural stance: Postgres is control plane (connectors, credentials, chat, personas, permissions);
Vespa is the data plane holding chunks + embeddings + ACL filters; Redis for Celery broker, locks, stream buffers.

## 2. Connector framework

### Interfaces (`onyx/connectors/interfaces.py`)
- `BaseConnector(abc.ABC, Generic[CT])`: `load_credentials(dict) -> dict|None`, `validate_connector_settings()`,
  `parse_metadata()`, `set_allow_images()`, `build_dummy_checkpoint()`.
- `LoadConnector.load_from_state() -> Iterator[list[Document|HierarchyNode]]` — full one-shot load.
- `PollConnector.poll_source(start, end)` — epoch-seconds window incremental sync.
- `CheckpointedConnector[CT].load_from_checkpoint(start, end, checkpoint) -> Generator[Document|HierarchyNode|ConnectorFailure, None, CT]`
  — generator *returns* the next checkpoint; `ConnectorCheckpoint` base is just `{has_more: bool}` (models.py:511), subclasses add cursors. Resumable mid-window.
- `SlimConnector.retrieve_all_slim_docs()` — IDs only, used for **pruning** (detect source-side deletions).
- `SlimConnectorWithPermSync` / `CheckpointedConnectorWithPermSync` — same + `ExternalAccess` per doc (EE perm sync).
- `EventConnector.handle_event(event)` — event-driven (rarely used).
- `OAuthConnector` — classmethods `oauth_id()`, `oauth_authorization_url()`, `oauth_code_to_token()`.
- `CredentialsConnector` + `CredentialsProviderInterface` — dynamic (refreshable) credentials with locking (`get_provider_key`, `is_dynamic`).
- `HierarchyConnector.load_hierarchy()` — folder/space tree (`HierarchyNode`), and `Resolver.reindex(errors)` — re-fetch exactly the previously failed docs.
- `InputType` enum (models.py:17): `load_state | poll | event | slim_retrieval`.

### Data model yielded by connectors (`onyx/connectors/models.py`)
`Document`: `id` (stable source ID/URL), `sections: list[TextSection|ImageSection|TabularSection]`,
`source`, `semantic_identifier` (UI name) vs `title` (search field), `metadata: dict[str, str|list[str]]`,
`doc_updated_at/doc_created_at`, `primary_owners/secondary_owners: BasicExpertInfo`, `external_access`,
`parent_hierarchy_raw_node_id`, `content_hash()` (MD5 of title+section text+meta+owners — dedup fallback when
no `doc_updated_at`, e.g. web crawl). **Each `Section` carries its own `link`** — this is what makes citations
deep-link to anchors later. `ConnectorFailure` = exactly one of `failed_document {document_id, link}` or
`failed_entity {entity_id, missed_time_range}` — persisted per index attempt, retried via `Resolver`.

### Postgres config model (`onyx/db/models.py`)
- `Connector` (line 1941): `source` enum, `input_type`, **`connector_specific_config JSONB`** (the connector's
  `__init__` kwargs), `refresh_freq` (secs, min 60), `prune_freq` (min 300), `indexing_start`.
- `Credential` (2008): `credential_json` as **EncryptedJson** column, `admin_public`, `curator_public`, owner `user_id`.
- `ConnectorCredentialPair` (841): the "cc_pair" join = unit of scheduling/status (status, last success time, in-repeated-error-state).
- `IndexAttempt` (2416): one run — status, checkpoint pointer, error counts; `SearchSettings` (2129) holds embedding
  model/index config (supports dual-index model migration via `IndexModelStatus` PRESENT/FUTURE).

### Sync scheduling & failure handling
Celery beat (`background/celery/tasks/beat_schedule.py`): `check-for-indexing` every **15s** picks cc_pairs due per
`refresh_freq` and spawns attempts; `check-for-connector-deletion` 20s; `check-for-vespa-sync` (metadata/boost/ACL
re-push) 20s; pruning checks; checkpoint cleanup hourly; index-attempt cleanup 30min. Fetch and process are **split
workers**: docfetching runs the connector and stages raw doc batches in the file store (`indexing/chunk_batch_store.py`),
docprocessing chunks/embeds/writes. Heartbeat + stop signals via `IndexingHeartbeatInterface` (`should_stop/progress`).
Per-doc failures don't kill runs; entity failures record missed time ranges for later re-cover.

### The ~10 connectors that matter, with `connector_specific_config` shapes (from `__init__` kwargs)
- **file** (`connectors/file/connector.py:294`): `{file_locations: [file-store ids], zip_metadata_file_id?}` — files uploaded to file store first.
- **web** (`connectors/web/connector.py:346`): `{base_url, web_connector_type: recursive|single|sitemap|upload, mintlify_cleanup: true, scroll_before_scraping: false}` — Playwright crawler, content-hash dedup, `oauth`-less.
- **notion** (`connectors/notion/connector.py:139`): `{root_page_id?: str, recursive_index_enabled: bool}` + integration token credential.
- **confluence** (`connectors/confluence/connector.py:165`): `{wiki_base, is_cloud, space?, page_id?, index_recursively, cql_query?, labels_to_skip: [], timezone_offset}`.
- **github** (`connectors/github/connector.py:609`): `{repo_owner, repositories?, state_filter: "all", include_prs: true, include_issues: false, include_files: false, branch?}`.
- **google_drive** (`connectors/google_drive/connector.py:277`): `{include_shared_drives, include_my_drives, include_files_shared_with_me, shared_drive_urls?, my_drive_emails?, shared_folder_urls?, specific_user_emails?}` — service-account or OAuth; checkpointed; perm-sync flagship.
- **slack** (`connectors/slack/connector.py:834`): `{channels?: [..], channel_regex_enabled, exclude_channels?, exclude_channel_regex_enabled}` — threads become docs; also available *federated* (live search, no index).
- **gmail**, **jira**, **zendesk**, **sharepoint** — same pattern; zendesk/intercom-style ticket sources are simple PollConnectors.
Registry maps `DocumentSource -> class` in `connectors/registry.py`; `factory.py:instantiate_connector` passes JSONB config as kwargs and injects credentials.

## 3. Indexing / chunking pipeline (exact parameters)

Flow (`onyx/indexing/indexing_pipeline.py`): upsert Document rows → skip unchanged docs
(`get_docs_to_update`: compare `doc_updated_at`, fallback `content_hash`) → process sections (ImageSection → LLM
image summarization; TabularSection → CSV streamed row-wise from file store) → **Chunker** → optional contextual
RAG summaries → **Embedder** (batched) → stage → write to Vespa → metadata sync.

Chunking (`onyx/indexing/chunker.py`, `chonkie.SentenceChunker` = sentence-aware max-size splitter):
- **Chunk size: 512 tokens** = `DOC_EMBEDDING_CONTEXT_SIZE` (`shared_configs/configs.py:41`) — sized to the embedding model context.
- **Overlap: 0** (`chunker.py:27` — "unclear if overlaps actually help quality at all"; they rely on neighbor-chunk expansion at query time instead).
- **Blurb: 128 tokens** (`BLURB_SIZE`, app_configs.py:59) — first sentences of the doc, stored per chunk for UI snippets.
- **Title prefixing**: title (blurbed) + separator prepended to every chunk's embedded content; title tokens are subtracted from the 512 budget.
- **Metadata suffix**: semantic form `"Metadata:\n\tkey - value"` appended for embedding; values-only string for the keyword/BM25 field. Skipped if metadata ≥ **25%** of chunk budget (`MAX_METADATA_PERCENTAGE = 0.25`).
- **Min content: 256 tokens** (`CHUNK_MIN_CONTENT`) — if title+metadata squeeze content below this, drop prefix/suffix entirely.
- **Multipass / mini-chunks** (off by default, `ENABLE_MULTIPASS_INDEXING`): mini-chunks of **150 tokens** (`MINI_CHUNK_SIZE`) embedded alongside full chunk (multi-vector per chunk); **large chunks** = **4** consecutive chunks combined (`LARGE_CHUNK_RATIO`) with `large_chunk_reference_ids` back to constituents.
- **Contextual RAG** (Anthropic-style, ON by default when search settings enable it: `USE_DOCUMENT_SUMMARY=true`, `USE_CHUNK_SUMMARY=true`): reserves **100 tokens per summary** (`MAX_CONTEXT_TOKENS`, llm/utils.py:39) out of the 512 (so 200 reserved → ~312 content); skipped when the whole doc fits in one chunk. Prompts in `onyx/prompts/contextual_retrieval.py`: doc summary ("short succinct summary") + chunk situating prompt ("give a short succinct context to situate this chunk…"), doc text cached across chunk calls.
- Chunk record (`DocAwareChunk`): `blurb`, `content`, `source_links {char_offset -> link}` (per-section links preserved!), `section_continuation`, `title_prefix`, `metadata_suffix_semantic/keyword`, `doc_summary`, `chunk_context`, `mini_chunk_texts`, `large_chunk_id`.
- Embedder (`onyx/indexing/embedder.py`): content embeddings batched; **separate `title_embedding`** computed once per doc and cached across its chunks; mini-chunk embeddings appended as extra vectors. `content_classification.py` scores "information content" per chunk → `aggregated_chunk_boost_factor` (chunk-quality boost at rank time).

## 4. Hybrid search + rerank + citations (exact algorithms)

### Query preprocessing
- ACL filter injected server-side (`context/search/preprocessing/access_filters.py`).
- Stopword stripping for the keyword leg (`natural_language_processing/english_stopwords.py: strip_stopwords`) — needed because BM25-on-title normalization is sensitive to junk terms.
- Optional LLM query expansion (`secondary_llm_flows/query_expansion.py`) producing keyword + semantic variants (off by default for basic search: `USE_SEMANTIC_KEYWORD_EXPANSIONS_BASIC_SEARCH=false`); LLM source/time filter extraction (`source_filter.py`, `time_filter.py`). In chat, the LLM *tool call* itself generates the queries (SearchToolQueriesDelta packets).
- Multiple retrievals (expansions, federated) merged by `combine_retrieval_results` (`retrieval/search_runner.py:27`) — dedupe by chunk key, keep max score.

### Hybrid ranking (Vespa `document_index/vespa/app_config/schemas/danswer_chunk.sd.jinja:228-291`)
- `title` and `content` fields are BM25-indexed; `embeddings` is multi-vector (chunk + mini-chunks), plus `title_embedding`.
- **first-phase (recall)**: pure vector: `tcr*closeness(title_emb) + (1-tcr)*closeness(content_emb)` — "first phase must be vector to allow hits that have no keyword matches".
- **global-phase (rerank-count 1000)** — the hybrid fusion, all components **min-max normalized over the result set** (`normalize_linear`), then linearly mixed and multiplied by boosts:

```
score = [ alpha * ( tcr * norm(title_vec_score) + (1-tcr) * norm(cos(content_emb)) )
        + (1-alpha) * ( tcr * norm(bm25(title)) + (1-tcr) * norm(bm25(content)) ) ]
        * document_boost * recency_bias * aggregated_chunk_boost
```

- **alpha = HYBRID_ALPHA = 0.5** (`configs/chat_configs.py:69`) — even dense/sparse split.
- **tcr = TITLE_CONTENT_RATIO = 0.10** — title is "more of a boost than a separate field" since the title is already prefixed into content.
- `title_vec_score = max(cos(content_emb), cos(title_emb))` — fallback so title-less docs aren't zeroed.
- **document_boost** (user feedback votes, `default_rank` profile): stretched sigmoid, range **0.5x–2x**: `boost<0 → 0.5 + 1/(1+e^(-b/3))`, else `2/(1+e^(-b/3))`.
- **recency_bias = max(1/(1 + decay*age_years), 0.75)** — `DOC_TIME_DECAY=0.5` (floor hit at ~2y), floor **0.75**, `FAVOR_RECENT_DECAY_MULTIPLIER=2.0` when query wants recent; missing dates assumed 3 months old.
- Retrieval depth `NUM_RETURNED_HITS = 50` chunks.

### Post-retrieval: expansion + selection (rerank)
- Chunk → `InferenceSection`: pull **1 chunk above + 1 below** the hit (`CONTEXT_CHUNKS_ABOVE/BELOW = 1`) and merge.
- **Cross-encoder rerank is legacy/optional**: `model_server/legacy/reranker.py`; `DEFAULT_CROSS_ENCODER_MODEL_NAME` defaults to **None** (suggested local model: `mixedbread-ai/mxbai-rerank-xsmall-v1`; cloud rerank providers via API key). The *current* chat path replaces it with an **LLM relevance/selection pass**:
  `tools/tool_implementations/search/search_tool.py` trims sections front-to-back into a budget of
  `MAX_CHUNKS_FED_TO_CHAT(25) × 512 tokens` (+75-token metadata estimate per section), then
  `select_sections_for_expansion` — one cheap LLM call that picks which sections deserve full-document expansion
  (`SECONDARY_LLM_FLOW_TIMEOUT_S=60`, graceful fallback = keep order as-is).
- Final context: selected sections serialized with source metadata + **numbered doc list**, capped by the LLM's real input window.

### Citations (`onyx/chat/citation_processor.py` — DynamicCitationProcessor)
- Retrieval results are numbered (1..n, monotonically across tool calls in a turn — `get_next_citation_number`; web-search vs open_url duplicate numbers resolved keep-first). LLM is prompted to emit `[n]` markers.
- Streaming token processor: buffers tokens that *might* be partial citations (regex `[\[【［]+(?:\d+(?:, ?\d+)*(?:, ?)?)?$` — deliberately linear-time, comment at line 195 explains the ReDoS-safe form), matches complete `[1]`, `[1, 2]`, `[[1]]`, unicode brackets `【1】/［1］`; **skips citation parsing inside code blocks** (``` parity check).
- Three modes: `HYPERLINK` (default: rewrite `[1]` → `[[1]](url)` markdown and emit a `CitationInfo{citation_number, document_id}` packet **before** the rewritten text so the UI can render immediately), `KEEP_MARKERS`, `REMOVE` (for Slack/Discord bots).
- Dedup: repeated citation of the same doc within 5 non-citation chars emits no new CitationInfo; cited docs kept in first-cited order; `citation_utils.collapse_citations` renumbers for deep-research report assembly.
- Persistence: `ChatMessage.citations: JSONB {citation_num -> SearchDoc.id}` (db/models.py:3200) with `SearchDoc` rows (db/models.py:3350) stored per message — history replays with working citations.

## 5. LLM + assistant/tool config schemas

### LLM abstraction
- `onyx/llm/interfaces.py`: `LLM` ABC — `config -> LLMConfig`, `invoke(...)`, `stream(...)`; concrete impl wraps **litellm** (`llm/litellm_singleton.py`; cost via litellm price map in `llm/cost.py`). Providers therefore = anything litellm supports.
- DB (`db/models.py:3470`): `LLMProvider {provider, api_key (EncryptedString), api_base, api_version, custom_config JSONB (e.g. AWS keys), deployment_name, is_public, groups}` → `ModelConfiguration` rows per model (visibility, max input tokens) → `LLMModelFlow` marks defaults per flow (CHAT / VISION), replacing older `is_default_provider` booleans. Personas can be restricted to providers (`llm_provider__persona`).
- Retry/timeout policy: `LLM_FIRST_CHUNK_MAX_RETRIES=2` (retry only if stream fails **before first token**), socket read timeout 60s.

### Streaming protocol (`onyx/server/query_and_chat/streaming_models.py`)
`Packet {ind: int, obj: <discriminated union on obj_type>}` over SSE. Packet families: `AgentResponseStart/Delta`,
`ReasoningStart/Delta/Done`, `CitationInfo`, `SearchToolStart/QueriesDelta/FilterDelta/DocumentsDelta`,
`OpenUrlStart/Urls/Documents`, `ImageGenerationToolStart/Heartbeat/Final`, `PythonToolStart/Delta`, `BashTool*`,
`CustomToolStart/Args/Delta`, `ToolCallArgumentDelta`, `FileReaderStart/Result`, `MemoryTool*`, `DeepResearchPlan*`,
`IntermediateReport*`, `CodingAgent*`, `SectionEnd`, `OverallStop`, `PacketException`, `ChatHeartbeat` (keepalive
every 15s — ALB-safe), `TopLevelBranching`. Streams are **buffered in Redis** (`chat/stream_buffer.py`: live TTL 1h,
done TTL 10min, 16MB cap, 0.2s resume poll) → refresh/reconnect-safe streaming.

### Assistants ("Personas", `db/models.py:4016`) and tools
- `Persona`: `name`, `description`, `system_prompt` + `replace_base_system_prompt` flag, `task_prompt`,
  `datetime_aware`, `starter_messages`, `default_model_configuration_id` (per-assistant model override),
  `search_start_date`, M2M **document_sets** (knowledge scope) and **tools**, sharing (owner user XOR group,
  `is_public` + per-share permission levels VIEWER/EDITOR), display metadata (icon, priority, featured/listed), soft delete.
- `Tool` (`db/models.py:3853`): `name/description` (LLM-facing), `in_code_tool_id` for builtins,
  **`openapi_schema JSONB`** for UI-defined custom actions (parsed into callable methods), `mcp_input_schema` +
  `mcp_server_id` for MCP tools, `custom_headers`, `passthrough_auth` (forward user OAuth), `oauth_config_id`.
- Chat loop (`chat/process_message.py` + `llm_loop.py`): message tree (`parent_message_id` / `latest_child_message_id`
  → edits/regeneration branches; empty root node trick), history compression when > **75%** of context
  (`COMPRESSION_TRIGGER_RATIO`, summary messages carry `last_summarized_message_id`), tool loop bounded by
  **`MAX_LLM_CYCLES=6`**, tool packets emitted as they run, search docs persisted + numbered, final tokens through
  citation processor, assistant `ChatMessage` saved with `citations`, `token_count`, files, error.

### Permissions / ACL sync
- Core: `access/models.py: ExternalAccess {external_user_emails: set, external_user_group_ids: set, is_public}` (soft cap 5000 entries) → prefixed strings in Vespa `access_control_list` (weightedset, `rank: filter`) → query-time mandatory filter from user identity + group memberships. `document_sets` likewise a filter field.
- EE (`ee/onyx/external_permissions/`): per-connector doc-permission sync + external **group** sync as separate Celery tasks (`connector_doc_permissions_sync`, `connector_external_group_sync`, `doc_permissions_upsert` queues); connectors expose `...WithPermSync` variants.

### Background workers (Celery, queues in `configs/constants.py:426`)
- primary `celery` (fast orchestration checks) | light: `vespa_metadata_sync`, `doc_permissions_upsert`, `connector_deletion`, `llm_model_update`, `checkpoint_cleanup`, `index_attempt_cleanup`, `chat_ttl_deletion` | heavy: `connector_pruning`, `connector_doc_permissions_sync`, `connector_external_group_sync`, `connector_hierarchy_fetching`, `csv_generation` | pipeline: `connector_doc_fetching`, `docprocessing`, `user_file_processing/…`, `port` (background re-embedding migration to new embedding model — dual-index PRESENT/FUTURE switchover in `SearchSettings`).

## 6. What Stept should adopt / simplify / drop (pgvector-only design)

**Adopt (high value, portable):**
1. Connector interface trio: `LoadConnector` / `PollConnector(start, end)` / `CheckpointedConnector` returning
   `(docs | failures) → checkpoint`, plus `SlimConnector` for deletion pruning. This generator-based contract is the
   single best-designed part of Onyx. Model config exactly as Onyx does: `connector.config JSONB` +
   `credential.secret_json` (encrypted) + `connector_credential` join as scheduling unit + `index_attempt` audit rows.
2. Document→Section→Chunk model with **per-section links** carried onto chunks (`source_links`) — this is what makes
   citations deep-link. Keep `semantic_identifier` vs `title` distinction and metadata as `dict[str, str|list[str]]`.
3. Chunking recipe: sentence-aware splitter, **512-token chunks, 0 overlap**, title prefix (cap ~128 tokens),
   metadata suffix capped at 25% of chunk, min-content 256 → drop prefixes rule. Replace overlap with **±1
   neighbor-chunk expansion at read time** (cheap in Postgres: `WHERE document_id = X AND chunk_ord IN (n-1,n,n+1)`).
4. Streamed citation processor: port `DynamicCitationProcessor` semantics wholesale — partial-marker buffering,
   code-block skip, `[n]` → `[[n]](url)` rewrite, CitationInfo-before-token packet ordering, per-message
   `citations JSONB {n -> search_doc_id}` persistence with SearchDoc snapshot rows.
5. litellm as the provider layer + Onyx's DB shape (`llm_provider` + `model_configuration` + default-per-flow),
   encrypted API keys, per-assistant model override.
6. Typed streaming packet protocol (discriminated union on `obj_type`, `ind` ordering, heartbeat every 15s) and the
   Redis (or Postgres LISTEN/NOTIFY) resumable stream buffer — this is exactly what an embeddable widget needs on
   flaky connections.
7. Persona/tool schema: system_prompt + task_prompt + document_sets M2M + tools M2M + starter messages;
   `tool.openapi_schema JSONB` for custom actions. Message-tree chat history with branch pointers.
8. ACL as data: `is_public + allowed_emails[] + allowed_group_ids[]` on documents, enforced as a mandatory SQL
   filter at query time (Stept: a `WHERE` clause / RLS instead of Vespa weightedset).

**Simplify (replace heavyweight infra):**
- **Vespa → Postgres pgvector + tsvector.** Onyx's fusion is linear score mixing with min-max normalization — in
  Postgres it's simpler and more robust to use **RRF** (reciprocal rank fusion) over two CTEs:
  `dense`: `ORDER BY embedding <=> query LIMIT 50`; `sparse`: `ts_rank_cd(tsv, websearch_to_tsquery(...)) LIMIT 50`;
  fuse `score = Σ w_i / (60 + rank_i)` with **w_dense = w_sparse = 1.0** (mirrors HYBRID_ALPHA 0.5). Skip separate
  title vectors: emulate Onyx's real behavior (title prefixed into content, TITLE_CONTENT_RATIO only 0.10) by
  prefixing title into the embedded text and giving title words weight `A` in the tsvector
  (`setweight(to_tsvector(title),'A') || setweight(to_tsvector(content),'B')`).
- Multiplicative boosts applied **after fusion**: `final = rrf * recency * source_boost`, with Onyx's exact recency
  curve `max(1/(1 + 0.5*age_years), 0.75)` and a bounded source/feedback boost in [0.5, 2.0].
- **Celery+Redis → lightweight asyncio task queue on Postgres** (e.g. `SELECT ... FOR UPDATE SKIP LOCKED` jobs table
  or arq if Redis is already present). Keep Onyx's *shape*: a 15–30s scheduler tick that enqueues due connector runs,
  separate queues/labels for `sync` (heavy) vs `maintenance` (light) vs `prune`, per-run `index_attempt` rows with
  checkpoint JSON for resume.
- Cross-encoder rerank → **optional LLM rerank/selection** exactly like Onyx's current default: no model server;
  after RRF take top ~20 sections, one cheap LLM call ("select the sections that answer the query") with a 60s
  timeout and fall back to fused order. Add cross-encoder (e.g. via an API reranker) later behind the same interface.
- Contextual RAG: keep the **chunk-situating prompt** (100-token context prepended before embedding) as an optional
  per-source flag; skip doc-summary embedding averaging. For support articles (Stept's domain) chunk context is the
  higher-value half.
- Multipass mini-chunks/large-chunks: **skip**; off by default in Onyx anyway. One embedding per chunk.
- Model server: **skip**; use hosted embedding APIs through the same provider abstraction (store
  `model_name/dim/query_prefix/passage_prefix/normalize` like Onyx's `SearchSettings`, and keep the
  dual-index re-embed switchover concept as a simple `embedding_version` column instead of a second index).

**Drop (not needed for Intercom+Fin scope):**
- Knowledge graph (`kg/`), deep research multi-agent, coding agent/bash/python sandbox tools, federated connectors,
  hierarchy nodes (folder-tree browsing), OpenSearch second backend, multi-tenant sharding, Slack/Discord bots
  (Stept's channel is its own widget), user-file projects, `port` re-embed workers (v1: re-embed = re-run pipeline),
  EE group sync (keep the ExternalAccess fields, defer group resolution), content-classification chunk boost.

## 7. Top 10 actionable recommendations for Stept

1. Copy the connector contract verbatim: `poll(start, end)` generators yielding `Document | Failure`, checkpoint
   JSON returned by the generator, `connector_specific_config JSONB` + encrypted `credential_json` in Postgres,
   `connector_credential_pair` + `index_attempt` tables. Start with: file upload, web crawl, sitemap, Notion,
   Zendesk/Intercom import, GitHub — each is <500 LOC against this interface.
2. Chunk at **512 tokens, overlap 0, sentence-aware**, title prefix (≤128 tok) + metadata suffix (≤25% of chunk,
   dropped if content <256 tok); store `chunk_ord`, `blurb` (first ~128 tok), `source_links {offset→url}`,
   `section_continuation` per chunk row; fetch ±1 neighbor chunks at answer time instead of overlap.
3. Hybrid = RRF(k=60) over pgvector cosine top-50 and weighted tsvector top-50, equal weights; then multiply by
   `recency = max(1/(1+0.5*age_years), 0.75)` and a per-source boost clamped to [0.5, 2]. Strip stopwords from the
   keyword leg. One SQL statement, no extra service.
4. Make retrieval return **sections** (hit chunk + neighbors merged), trim into a `25 × 512`-token context budget
   front-to-back, and only then (optionally) run an LLM selection pass with graceful fallback — skip cross-encoders
   in v1 entirely, matching Onyx's own current default.
5. Prompt with a numbered doc list and stream through a ported `DynamicCitationProcessor`: buffer partial `[n`,
   ignore code blocks, rewrite to `[[n]](url)`, emit `citation` packets before the marker text, persist
   `citations {n → search_doc.id}` + SearchDoc snapshots per message so old chats replay with live citations.
6. Adopt the packet protocol: SSE of `{ind, obj: {type: message_delta | citation | search_start | search_docs |
   tool_start | tool_delta | section_end | stop | heartbeat | error}}` with a 15s heartbeat, and buffer packets
   server-side so widget reconnects can resume mid-answer.
7. Use litellm behind a 3-method `LLM` interface; DB tables `llm_provider` (encrypted key, base_url, custom JSONB)
   and `model_configuration` (+ default flags per flow: chat, vision, fast/secondary) — Onyx's "fast model for
   secondary flows" split (query expansion, selection, session naming) is worth copying on day one.
8. Assistants = Onyx personas minus sharing complexity: `{name, description, system_prompt, task_prompt,
   starter_messages JSONB, model_config_id?, document_set M2M, tools M2M}`; custom actions = `openapi_schema JSONB`
   on a `tool` row; approval gates are Stept's addition — model them as a flag on the tool row (`requires_approval`)
   checked in the tool loop (bounded at ~6 cycles like `MAX_LLM_CYCLES`).
9. Task system: one scheduler tick (15–30s, advisory-locked) scanning `connector_credential_pair.refresh_freq` into
   a Postgres `SKIP LOCKED` job table with queue labels (`fetch`, `process`, `maintenance`, `prune`); per-doc failure
   rows + resumable checkpoint JSON on the attempt; slim-doc pruning pass for source deletions.
10. Bake ACL fields into the chunk/document rows from day one (`is_public`, `allowed_emails[]`, `allowed_group_ids[]`
    + `document_set` tags) and make every retrieval query filter on them — retrofitting permissions into RAG (Onyx's
    EE moat) is far harder than carrying two array columns early.
