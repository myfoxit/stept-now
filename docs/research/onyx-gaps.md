# Onyx → Stept gap inventory (implementation reference)

> Source: fresh shallow clone of onyx-dot-app/onyx (2026-07-31), read for the
> competitive-parity wave. Binding reference for connectors, scheduled sync,
> rerank, and search analytics. Complements `docs/research/onyx.md`.

## 1. Connector framework essentials
- Interface trio: `LoadConnector.load_from_state()`, `PollConnector.poll_source(start, end)`,
  `CheckpointedConnector.load_from_checkpoint(...) -> checkpoint` (generator returns next checkpoint);
  `SlimConnector.retrieve_all_slim_docs()` = IDs only, used solely for **deletion pruning**.
- `Connector.refresh_freq` (seconds, min 60, None = never auto-index) + `prune_freq` (min 300);
  beat tick every 15s runs `should_index`: index if never indexed, else when
  `now - last_attempt >= refresh_freq`. Poll windows overlap by 30 min (`POLL_CONNECTOR_OFFSET`)
  to absorb clock skew. Per-run `IndexAttempt` rows record status/counts/errors.
- Per-doc `ConnectorFailure`s are yielded inline (one bad doc never kills a run) and retried selectively.
- `Document.content_hash` (MD5 of title+sections) gates re-embedding when timestamps are unreliable
  (web docs deliberately leave `doc_updated_at` unset).

## 2. Web connector (`connectors/web/connector.py`)
- Modes: `recursive | single | sitemap | upload`.
- **Sitemap**: GET the URL, parse `<loc>` entries (ElementTree; strip XML namespace via
  `re.match(r"\{.*\}", root.tag)`); **recurse into `<sitemapindex>`**. Discovery fallback:
  `/sitemap.xml`, `/sitemap_index.xml`, then `robots.txt` `Sitemap:` lines.
- **Recursive crawl**: same-site check = netloc equal after `removeprefix("www.")` AND candidate
  path == base path or under `base_path + "/"`; strip `#fragments`; `urljoin` relatives;
  `visited_links` set + **content-hash dedup** `hash((title, cleaned_text))` for near-duplicate pages;
  redirects re-checked against visited.
- SSRF guard: scheme http(s) only; resolve all IPs via `socket.getaddrinfo`, each must be
  `ipaddress.ip_address(ip).is_global`; re-check after redirects.
- Retries: 3, delay `min(2**n + jitter, 10)`.

## 3. GitHub connector
- REST via authenticated client (`github_access_token`); config
  `{repo_owner, repositories, branch?, include_prs=True, include_issues=False, include_files=False}`.
- Files stage filters: extensions `{.md,.mdx,.markdown,.rst,.txt}`, well-known filenames
  (readme/license/changelog/contributing/...), path denylist `{.git,node_modules,vendor,dist,build,.venv,__pycache__}`,
  max 1MB/file. Docs `id = html_url`. Issues/PRs = body + joined comments.

## 4. Notion connector
- Headers `Notion-Version` + `Authorization: Bearer {integration token}`; 30s timeout, retry x3.
- Endpoints: `POST /v1/search` (filter object=page, sort last_edited_time desc, cursor),
  `GET /v1/blocks/{id}/children?start_cursor=` (recursive traversal collecting child pages),
  `GET /v1/pages/{id}`. Root-page mode forces full traversal (search API misses pages).
- URL normalization: last hyphen segment → 32 hex chars → hyphenated UUID.

## 5. Search pipeline deltas vs Stept
- Stept already has: RRF(k=60) dense+lexical, recency decay `max(1/(1+0.5*age_years), 0.75)`,
  source boost [0.5, 2.0], ±1 neighbor expansion, stopword-stripped keyword leg.
- Onyx extras worth porting:
  - **Weighted multi-query RRF**: semantic rephrase weight 1.3, keyword expansion 1.0, raw query 0.5;
    `weighted_reciprocal_rank_fusion(score = Σ weight/(k + rank))`, k=50.
  - **LLM relevance/selection pass** (replaces cross-encoders by default): after retrieval, one cheap
    LLM call picks relevant sections; 60s timeout with graceful fallback to fused order. Cross-encoder
    rerank is optional/legacy (Cohere/LiteLLM/local model server).
  - Document feedback boost: thumbs on retrieval mutate `Document.boost` ±1 →
    sigmoid multiplier 0.5×–2× at rank time.
  - Two-tier expansion: default ±1 chunks; LLM-selected best docs expand to ±5.

## 6. Query analytics & feedback models
- `ChatMessage.citations: {citation_num -> SearchDoc.id}` + `SearchDoc` snapshot rows per message.
- `ChatMessageFeedback {chat_message_id, is_positive, required_followup, feedback_text, predefined_feedback}`.
- `DocumentRetrievalFeedback {chat_message_id, document_id, document_rank, clicked, feedback}` →
  mutates `Document.boost`.
- Analytics endpoints (30-day lookback): per-day `{total_queries, total_likes, total_dislikes}`;
  daily active users; bot deflection `auto_resolved = total_queries - negatives`; per-assistant stats;
  query history browser + CSV export.

## 7. Other stealable pieces (roadmap)
- **Standard answers**: keyword/regex-matched canned replies checked before the LLM; linked to messages
  for analytics.
- Answer gating: suppress bot answers with zero citations (`well_answered_postfilter`).
- Escalation: `required_followup` feedback + follow-up/resolved buttons.
- Personas: system_prompt + task_prompt + document-set scoping + starter messages + `search_start_date` floor.
- ACL-as-data on chunks (`is_public + allowed_emails[] + allowed_group_ids[]`) enforced as mandatory filter.
- Federated live search of chatty sources (Slack) with the user's own token, merged into RRF.
- Curator role; document hide + manual boost admin; information-content chunk boost.
