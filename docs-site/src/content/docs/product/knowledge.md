---
title: Knowledge base & crawling
description: Knowledge sources, the website crawler, chunking and embedding, hybrid retrieval, and the search API.
---

Knowledge sources ingest content into documents and chunks. Everything indexed is
retrievable by the AI agent, the reply copilot, semantic search, MCP clients and the
widget's article search.

## Source types

| Type | Content |
| ---- | ------- |
| `files` | Uploaded PDF, DOCX, Markdown, HTML, TXT, CSV |
| `text` | Authored/pasted markdown documents |
| `urls` | An explicit list of URLs, fetched and indexed |
| `sitemap` | A sitemap.xml (index sitemaps followed, `lastmod` used for incremental skips), up to 500 pages |
| `crawl` | Website crawler — follows internal links from a base URL |
| `github` | A repository: `repo_owner`, `repo`, optional `branch`, `include_files` / `include_issues` / `include_prs` |
| `notion` | Notion pages — OAuth connection or internal integration token |
| `confluence` | Confluence Cloud — OAuth (`connection_id`) or Basic auth (`base_url`, `email` + API token), optional `space_keys` |
| `gdrive` | Google Drive folders (OAuth `connection_id` + `folder_ids`); Docs/Sheets/Slides exported |
| `zendesk` | Zendesk Help Center articles (`subdomain` + email/API token), incremental sync |
| `articles` | Auto-managed: your published help-center articles. Not creatable via the API |

Connector credentials go in the write-only `secrets` field — encrypted at rest, never
returned.

## Crawl configuration

```json
{
  "type": "crawl",
  "name": "Docs site",
  "config": {
    "base_url": "https://docs.example.com/guides",
    "max_pages": 100,
    "max_depth": 3,
    "include_patterns": ["/guides/*"],
    "exclude_patterns": ["/guides/changelog/*", "*/print"],
    "respect_robots": true,
    "delay_ms": 250,
    "refresh_minutes": 1440,
    "boost": 1.2
  }
}
```

- `base_url` (required) — crawling stays on the same site, under the base path.
- `max_pages` — default 30, capped at **200**. `max_depth` — default 3, capped at **5**.
- `delay_ms` — politeness delay per fetch, default 250, capped at **2000**.
- `include_patterns` / `exclude_patterns` — fnmatch globs against the URL path, up to 20
  each; **exclude wins**; empty include means everything.
- `respect_robots` — default `true`; honors the site's `robots.txt` (`User-agent: *` group).
- `refresh_minutes` — minimum **5**; enables scheduled re-syncs.
- `boost` — retrieval score multiplier for this source, clamped to 0.5–2.0.

The crawler fetches HTML only (2 MB per-page cap) and does not execute JavaScript, so
client-rendered SPAs yield little text.

## Sync lifecycle

Creating a source does **not** sync it. Trigger a sync explicitly:

```bash
POST /api/v1/w/{workspace_id}/knowledge/sources/{source_id}/sync
```

The source's `status` walks `idle → syncing → idle` (or `error`, with a truncated error
summary); `last_synced_at` is set either way. With `refresh_minutes` configured, a
scheduler re-syncs the source on that interval. Documents have their own status
(`pending | processing | indexed | failed`) and a per-document `retry` endpoint.

## Chunking and embedding

Documents are split into ~512-token chunks along headings (each chunk keeps its heading
trail and document title for context). Chunks are embedded with the workspace's default
embedding model (any OpenAI-compatible embedding endpoint); without one, a local hash
embedder is used as a dev-quality fallback. A content hash skips re-embedding unchanged
documents on re-sync.

## Retrieval

Search is hybrid: the query is classified and rewritten, then **dense** (vector) and
**lexical** (full-text) candidate lists are fused with reciprocal-rank fusion, adjusted by
recency, the source `boost` and title matches, optionally re-ranked by an LLM
(`rerank: true`), and expanded with neighboring chunks. The AI agent uses the same pipeline
with rerank enabled.

Set `ai_searchable: false` on any document (PATCH it) to exclude it from retrieval
everywhere without deleting it.

## Search API

```bash
POST /api/v1/w/{workspace_id}/knowledge/search
```

```json
{ "query": "how do refunds work", "k": 8, "source_ids": null, "rerank": false }
```

Returns `{ "results": [{ "chunk_id", "document_id", "content", "score", "title", "url",
"ord" }], "latency_ms": … }`. `k` ranges 1–50 (default 8).
