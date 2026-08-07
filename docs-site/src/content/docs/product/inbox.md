---
title: Inbox & conversations
description: How conversations, assignment, SLAs, automation rules, macros and reporting work in the Stept shared inbox.
---

Every channel feeds the same inbox. A conversation is a per-workspace numbered thread
between one contact and your team, living in one inbox (channel instance).

## Conversations

- **Status**: `open` (human-owned) · `pending` (owned by an AI agent) · `snoozed` · `resolved`.
  Taking over a pending conversation means opening it — the AI agent only replies while the
  status is `pending`.
- **Priority**: `none | low | medium | high | urgent`.
- **Trackers**: `waiting_since` (set on inbound messages, cleared on your reply),
  `first_reply_at`, `resolved_at`, `last_activity_at` (list sort key). Unread state comes
  from per-side last-seen timestamps, not counters.

Messages have a `visibility`:

- **`public`** — delivered to the contact over their channel. Outbound public messages carry
  a delivery status (`pending | sent | failed`) with the error text on failure.
- **`note`** — internal, never delivered or shown to the contact. Use notes for context,
  @-discussion and AI agent summaries.
- **`activity`** — system timeline entries ("Sam resolved the conversation").

Message content is markdown; attachments are stored server-side and linked from the message.

## Assignment and teams

Conversations can be assigned to a member (`assignee`) and/or a **team**. Teams are named
groups of members (with an emoji and description) — assign a conversation to "Support" and
anyone on that team picks it up. Widget inboxes have an `auto_assign` config flag; it is off
by default so new conversations land in Unassigned for the team to triage.

## Channels

An inbox is one channel instance; a workspace can have many. Channel types:
`widget`, `email`, `slack`, `telegram`, `whatsapp`, `messenger`, `instagram`, `line`,
`sms`, `api`. Each inbox carries its own config (widget theming, email transport, …) and
encrypted channel credentials. See [Chat widget](/product/widget/) and
[Integrations](/integrations/overview/) for per-channel setup.

## SLAs

An SLA policy defines up to three thresholds in minutes: **first response**, **next
response** (per waiting episode) and **resolution**. Optionally count only business hours
(`only_during_business_hours`) so nights and weekends don't breach.

Apply a policy per conversation (`PUT /conversations/{id}/sla`) or automatically: set
`sla_policy_id` in an inbox's config and every new conversation in that inbox gets it. A
scheduled scan (every 60 s) records breach events (`frt`, `nrt`, `rt`); the applied SLA
walks `active → hit | missed | active_with_misses`. The SLA report shows attainment per
policy.

## Automation rules

A rule subscribes to one event — `conversation.created`, `message.created`,
`conversation.status_changed`, `csat.submitted` or `contact.created` — checks AND-ed
conditions, and runs ordered actions:

```json
{
  "name": "Route billing to the billing team",
  "event": "conversation.created",
  "conditions": [
    { "field": "channel_type", "op": "eq", "value": "email" },
    { "field": "content", "op": "contains", "value": "invoice" }
  ],
  "actions": [
    { "type": "assign_team", "params": { "team_id": "…" } },
    { "type": "set_priority", "params": { "priority": "high" } }
  ]
}
```

Condition fields: `inbox_id`, `channel_type`, `status`, `priority`, `subject`, `content`,
`contact.email`, `tag`, plus `contact.attributes.*`. Operators: `eq`, `neq`, `contains`,
`in`, `exists`. An empty condition list matches everything.

Actions: `assign_user`, `assign_team`, `set_priority`, `set_status`, `add_tag`,
`send_reply`, `send_note`, `notify_member`, `send_webhook`. Rules run in order (`ord`) and
can be disabled without deleting.

## Macros

Macros are the same action vocabulary (plus `remove_tag`), saved as a named shortcut a
member runs manually on a conversation — "tag as bug, reply with the triage template,
assign to engineering" in one click. Visibility is `personal` (creator only) or `global`
(whole workspace). Reply/note content supports `{{contact.name}}` and `{{agent.name}}`
placeholders.

## Reporting

`GET /api/v1/w/{workspace_id}/reports/overview?days=7` returns:

- **Volume**: new and resolved conversations, per day and per channel.
- **Resolution**: resolution rate, median first-response and median resolution minutes,
  resolved-per-agent breakdown.
- **CSAT**: average rating and response count. When a conversation is resolved, the widget
  asks the contact for a 1–5 rating with optional feedback.
- **AI**: agent runs, AI-resolved count and AI resolution rate.

A breakdown endpoint slices the same numbers by inbox, team, agent or tag, and every report
has a CSV export variant. SLA attainment lives at `GET /reports/sla`.
