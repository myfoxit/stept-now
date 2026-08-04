# The in-app assistant

> A visitor asks in the embedded chat, and the AI answers **on their screen** —
> pointing at the real UI, walking them through it, or (with consent) doing it for
> them — while still answering knowledge questions with citations.

This document is the contract for that feature: how the pieces fit, what each
guardrail guarantees, and where the code lives.

## The answer ladder

Three answers to "how do I do X?", best first. The agent prompt spells the ladder
out because the default failure mode is the opposite — models explain in prose
when they could point, and click when they should have asked.

1. **Show me a tour** — `find_guide` searches published tours/checklists; a match
   plays through `show_guide`, using the real tour row so telemetry and analytics
   attribute correctly.
2. **Show me here** — no tour fits: `page_snapshot`, then `show_steps` composes a
   walkthrough anchored to elements on the visitor's actual screen.
3. **Tell me** — a knowledge question rather than a task: `search_knowledge`, cited
   `[n]` as before.

Plus, on explicit request only: **do it for me** — `page_act` / `page_navigate`.

## How a page tool runs

The agent loop runs on the server; its page tools run in the visitor's browser.
That split reuses the engine's existing defer/resume machinery — the same one the
human approval gate uses.

```
model calls page_act
        │
        ▼
_pause_for_client                 backend/app/agents/engine.py
  persists pending_tool_call + messages_snapshot
  status → awaiting_client
  broadcast "copilot.op" AFTER COMMIT   ← see "The commit race" below
        │
        ▼  ws  conv:{conversation_id}
controller.onCopilotOp             widget/src/app/controller.ts
  postMessage COPILOT_OP
        │
        ▼  postMessage
loader.runCopilotOp → PageAgent    widget/src/loader.ts, widget/src/page-agent.ts
  executes in the HOST DOM, returns a fresh compact snapshot
        │
        ▼  postMessage COPILOT_RESULT
POST /api/widget/conversations/{id}/copilot/result
        │
        ▼
submit_client_result → enqueue resume → loop continues
```

Nothing is held in memory: a worker restart mid-walkthrough loses nothing, and a
visitor who closes the tab leaves a run that `sweep_stale_client_waits` finishes
with an error the model can explain, rather than silence.

### The commit race

The op is broadcast from an `after_commit` listener, not inline. A flush is
invisible to other sessions, and the widget is fast enough to execute an op and
POST its result while the parking transaction is still open — the result would then
hit a run row that still says `running` and be rejected as stale, stalling the
guide until the timeout sweep. The mirror case (a resume that arrives before the
result commits) raises `_ResumeNotReady`, which the queue retries.

## Guardrails

| Guarantee | Where |
|---|---|
| Off unless the workspace enables it (`settings.page_control.enabled`) | `page_tools.client_tools_available` |
| Acting on the page needs BOTH the agent's `allow_actions` and the visitor's per-conversation consent | same; consent lives on `conversation.attributes.page_control_consent` |
| A withheld tool is never offered to the model at all — it cannot propose what it cannot do | `page_tools.client_tool_specs` |
| The widget's own DOM is invisible to the AI (it can never click itself) | `page-agent.ts` `WIDGET_SELECTORS`, matched by `stept-` class prefix |
| A host page can fence off anything with `data-stept-no-ai` | `page-agent.ts` `OPT_OUT_SELECTOR` |
| Password fields are never typed into, and read back masked | `page-agent.ts`, `dom-capture` `formState` |
| Navigation is same-origin unless the host opts extra origins in (`SteptSettings.aiAllowedOrigins`) | `page-agent.ts` `originAllowed` |
| At most `MAX_MUTATING_OPS` page changes per run, counted from the trace so it survives pause/resume | `engine._reject_client_call` |
| A malformed call is answered locally instead of spending a browser round-trip | `page_tools.validate` |
| A click that changed nothing visible says so, instead of reporting success | `page-agent.ts` `act` |

## What the visitor sees

One line above the composer (`widget/src/app/components/PageAssist.tsx`): a
checkbox to let the assistant act, or a live status while it does. Everything else
is the existing messenger — the walkthrough itself renders through the same tour
player a recorded tour uses.

## Retrieval upgrades

Ported from the old repo's `services/rag/` and rebuilt deterministically (no LLM
round-trip before retrieval — see `app/rag/query.py` for why):

- **intent classification** → a title-match leg for "where is…" / "how do I…";
- **query rewriting** → filler stripped, abbreviations expanded alongside the
  original, pronouns resolved against conversation history;
- **multi-query expansion** → each alternative phrasing is its own retrieval leg;
- **BM25 blend** → length-normalised lexical scoring so a short exact answer beats
  a long tangential chunk;
- **context builder** (`app/rag/context.py`) → token-budgeted assembly with
  query-focused compression, replacing "first 500 chars of each of k chunks".

## Editor AI

`POST /w/{ws}/ai/write` (`app/agents/writer.py`) backs the editor's AI menu:
draft/outline (grounded in the knowledge base, returning citations) and
rewrite/shorten/expand/simplify/fix/translate/title over the selection. Gated by
`knowledge:write`. The author's text always travels inside a `<text>` delimiter so
an instruction that happens to appear in the document reads as content.

## Files

| Area | Files |
|---|---|
| Client tool contract | `backend/app/agents/page_tools.py` |
| Defer/resume + prompt | `backend/app/agents/engine.py` |
| Guide search | `backend/app/agents/guides.py` |
| Widget endpoints | `backend/app/api/widget/copilot.py`, `backend/app/schemas/copilot.py` |
| Editor AI | `backend/app/agents/writer.py`, `frontend/src/components/editor/AiMenu.tsx` |
| Retrieval | `backend/app/rag/{query,context,retrieval}.py` |
| Host-page runtime | `widget/src/page-agent.ts`, `packages/dom-capture/src/compact.ts` |
| Widget plumbing | `widget/src/{protocol,loader}.ts`, `widget/src/app/controller.ts` |
| Visitor UI | `widget/src/app/components/PageAssist.tsx` |
| Dashboard settings | `frontend/src/features/ai/pages/AgentBuilderPage.tsx` |
| Tests | `backend/tests/agents/test_{page_tools,writer}.py`, `backend/tests/widget/test_copilot.py`, `backend/tests/knowledge/test_{query_context,retrieval_expansion}.py`, `widget/src/{page-agent,app/copilot}.test.ts`, `packages/dom-capture/src/compact.test.ts`, `frontend/src/components/editor/AiMenu.test.tsx`, `e2e/tests/copilot-journey.spec.ts` |
