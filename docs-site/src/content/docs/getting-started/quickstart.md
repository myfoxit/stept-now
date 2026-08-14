---
title: Quickstart (cloud)
description: From signup to a live AI-answered chat widget in about ten minutes.
---

This walks you from an empty account to a chat widget on your site that answers from your
own docs.

## 1. Create your workspace

Sign up at [app.stepped.ai/signup](https://app.stepped.ai/signup) and create a workspace.
The workspace is your tenant — team members, inboxes, knowledge and settings all live in it.

## 2. Add knowledge

Go to **Knowledge** and add a source:

- **Website crawl** — point it at your docs or marketing site; Stept follows internal links
  (up to 200 pages, depth 5) and indexes the content.
- **Files** — upload PDFs, Word documents, Markdown or text.
- **Connectors** — sync Notion, Confluence, Google Drive, Zendesk Help Center or a GitHub repo.

Trigger a sync and watch documents appear. Everything indexed becomes retrievable by the AI
agent, semantic search and the reply copilot.

## 3. Configure the AI agent

Under **AI → Agents**, create or configure the agent: pick the model provider (bring your own
API key — it is encrypted at rest), write the instructions, and choose which tools it may
use. Test it in the agent sandbox — you see the full tool-call trace of every run.

## 4. Embed the widget

Under your widget inbox settings you get an embed snippet with your key filled in:

```html
<script>
  window.SteptSettings = { workspaceKey: "wk_..." };
  window.Stept = window.Stept || function () { (window.Stept.q = window.Stept.q || []).push(arguments) };
</script>
<script src="https://app.stepped.ai/widget-assets/loader.js" async></script>
```

(The second line is the pre-load command queue; all the settings — identity verification,
locale, tour autostart — are on the [widget page](/product/widget/).)

Put it on your site and open the page — the widget connects to your inbox, and the agent
answers with citations from the knowledge you added in step 2. When it can't help, it hands
the conversation to your team with a summary.

## 5. Invite your team

**Settings → Members** — invite teammates as admin, agent or viewer (or define custom roles
with granular permissions).
