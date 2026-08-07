---
title: AI agent
description: Configure the AI agent — model, retrieval, tools and policies, custom actions, approvals, run traces and the sandbox.
---

The agent sits on an inbox, answers from your knowledge base with citations, and acts
through tools. Every run leaves a full step-by-step trace, and risky tools can require
human approval.

## Configuration

```json
{
  "name": "Support Agent",
  "status": "live",
  "model_ref": "<provider_id>:claude-sonnet-5",
  "system_prompt": "You are …",
  "temperature": 0.3,
  "settings": {
    "retrieval": { "enabled": true, "k": 6, "source_ids": null },
    "handoff_message": "Let me bring in a teammate.",
    "guardrails": { "max_tool_calls": 8, "require_citations": false },
    "page_control": { "enabled": false, "allow_actions": false },
    "mcp": { "enabled": false, "approval_mode": "ask_in_chat" }
  },
  "tools": [{ "key": "search_knowledge", "policy": "auto" }]
}
```

- `status`: `draft | live | off` — only **live** agents join conversations and reply.
- `model_ref`: `"<provider_id>:<model_key>"` from your configured AI providers (keys are
  encrypted at rest). `null` falls back to the workspace default chat model, then to an
  offline mock.
- `retrieval`: `k` 1–20 results per lookup, optionally restricted to specific sources.
- `guardrails`: `max_tool_calls` caps tool use per run (1–30); `require_citations` refuses
  uncited answers.
- `page_control`: lets the agent see/operate the visitor's page via the widget — see
  [Chat widget](/product/widget/).

## Builtin tools and policies

Each tool has a policy: `auto` (runs immediately), `require_approval` (run pauses for a
human), or `disabled`.

| Tool | Default policy | Does |
| ---- | -------------- | ---- |
| `search_knowledge` | auto | RAG lookup with citation indexes |
| `find_guide` | auto | Finds a matching live product tour |
| `handoff_to_human` | auto | Escalates with a summary note |
| `tag_conversation` | auto | Tags the conversation |
| `close_conversation` | **require_approval** | Resolves the conversation |
| `collect_contact_details` | auto | Captures name/email onto the contact |
| `note_to_team` | auto | Leaves an internal note |

## Custom actions

Custom actions are HTTP tools you define: method, URL, headers (encrypted), a body
template, a JSON-schema for the model-supplied parameters, and a timeout. Requests are
**host-locked** — a call may only reach the host of the configured URL, so a
prompt-injected URL can't redirect it. Default policy is `require_approval`; each action
has a test endpoint. In agent tool lists they appear as `action:<id>`.

## Attaching to an inbox and the auto-reply lifecycle

Set the agent on the inbox: `PATCH /api/v1/w/{ws}/inboxes/{id}` with
`"config": { "ai_agent_id": "<agent_id>", … }` (the config dict is replaced wholesale —
re-send keys you want to keep), or pick it in **Settings → Channels → Configure**.

- On conversation creation the live agent joins and the status flips to **`pending`**.
- The agent replies to each inbound public message **while the status is `pending`**.
- `handoff_to_human` (and any failure fallback) flips the conversation to **`open`** —
  from then on it belongs to your team; the agent does not re-engage in that thread.

## Approvals

When a tool with `require_approval` fires, the run parks as `awaiting_approval` and the
request appears on the **Approvals** page (`GET /ai/approvals`, decide via
`POST /ai/approvals/{id}/decide` with `{"approved": true|false}`). Pending approvals
expire after 24 hours as rejected; the run resumes either way with the decision recorded.

## Run traces

Every run is an `AgentRun` (`queued → running → completed | failed | handed_off |
awaiting_approval | awaiting_client | canceled`) with token counts and ordered
`AgentStep` rows: LLM calls, tool calls and results, approval requests/decisions, page
operations, guardrail hits, the final reply. Inspect them at **AI → Runs**
(`GET /ai/runs`, `GET /ai/runs/{id}`).

## Sandbox testing

Test without a real conversation: `POST /api/v1/w/{ws}/ai/agents/{id}/test` with
`{"message": "How do I …?"}` returns the reply plus the full step trace. In sandbox mode
mutating builtins are dry-runs — nothing is tagged, closed or escalated.
