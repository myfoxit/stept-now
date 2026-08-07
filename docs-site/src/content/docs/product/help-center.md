---
title: Help center
description: Author help-center articles, serve them on the public portal and the widget's Help tab, and feed them into the AI agent automatically.
---

Articles are markdown documents organized into collections (name, emoji, order). An
article is `draft` until you publish it; only published articles are ever visible outside
the dashboard.

## Public portal

Published content is served unauthenticated at:

```
GET /portal/{workspace_slug}                          → collections + article list
GET /portal/{workspace_slug}/articles/{article_slug}  → one article (markdown body)
```

Drafts and unknown slugs return 404, so nothing unpublished can leak. The endpoints return
JSON with open CORS and no framing restrictions — render them with your own front end or
embed them where you need them.

## Articles feed the AI agent

Publishing an article syncs it into the auto-managed **articles** knowledge source, where
it is chunked and embedded like any other document. That means:

- The AI agent retrieves and cites published articles when answering.
- Semantic search and the reply copilot see them.
- Unpublishing or deleting an article removes its document from retrieval.

You never manage this source by hand — it mirrors the published state of your help center.

## Widget Help tab

Once at least one article is published, the widget shows a **Help** tab automatically:
visitors browse collections and read articles without leaving your site. The tab's search
runs over the embedded article chunks (the same retrieval pipeline as the agent), so it
finds answers by meaning, not just title matches.
