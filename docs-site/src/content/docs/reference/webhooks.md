---
title: Webhooks
description: Outbound webhooks — subscribable events, HMAC-signed deliveries, verification snippets in Python and Node, retries and the delivery log.
---

Outbound webhooks push domain events to a URL you control — no polling. Create them under
**Automation → Webhooks** or via the API (requires `webhooks:manage`):

```sh
curl -X POST $API/api/v1/w/$WS/webhooks \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{
    "url": "https://example.com/hooks/stept",
    "events": ["conversation.created", "message.created"]
  }'
```

The response includes the generated signing `secret` — store it; you need it to verify
deliveries.

## Events

Subscribe to any of the 23 domain events, or `"*"` for all of them:

| Area | Events |
| ---- | ------ |
| Workspace | `workspace.created`, `member.joined` |
| Contacts | `contact.created` |
| Conversations | `conversation.created`, `conversation.updated`, `conversation.assigned`, `conversation.status_changed`, `message.created` |
| Feedback | `csat.submitted`, `message.feedback` |
| Ops | `sla.breached`, `campaign.sent` |
| Knowledge | `document.indexed` |
| Integrations | `integration.connected`, `integration.disconnected`, `integration.reauth_required` |
| AI agent | `agent_run.started`, `agent_run.completed`, `approval.requested`, `approval.decided` |
| Adoption | `tour.event`, `checklist.event`, `survey.submitted` |

The `agent_run.*` and `approval.*` events are what make agent-ops integrations possible —
page your on-call when a run fails, or push approval requests into Slack and decide from
there.

## Delivery format

Each delivery is a `POST` with a JSON body:

```json
{
  "event": "conversation.created",
  "payload": { "conversation_id": "…", "inbox_id": "…" },
  "timestamp": "2026-08-14T09:30:00+00:00",
  "workspace_id": "…"
}
```

Headers:

- `X-Stept-Event` — the event name.
- `X-Stept-Signature` — HMAC-SHA256 hex digest of the **exact raw request body**, keyed
  with the webhook's secret.

Respond with any 2xx within 10 seconds. Anything else counts as a failure.

## Verifying the signature

Compute HMAC-SHA256 over the raw body bytes and compare in constant time. Do **not**
re-serialize the parsed JSON — the body is encoded with sorted keys and compact separators
(`json.dumps(sort_keys=True, separators=(",", ":"))`), and your serializer will not
reproduce it byte-for-byte. Verify first, parse second.

Python (FastAPI):

```python
import hashlib, hmac, json
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
SECRET = "…"  # the webhook's secret, from the create response

@app.post("/hooks/stept")
async def stept_hook(request: Request):
    raw = await request.body()
    expected = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("x-stept-signature", "")):
        raise HTTPException(401)
    event = json.loads(raw)
    # handle event["event"], event["payload"] …
    return {"ok": True}
```

Node (Express — note the raw body parser on this route):

```js
import crypto from 'node:crypto'
import express from 'express'

const app = express()
const SECRET = '…' // the webhook's secret, from the create response

app.post('/hooks/stept', express.raw({ type: 'application/json' }), (req, res) => {
  const sig = req.get('x-stept-signature') ?? ''
  const expected = crypto.createHmac('sha256', SECRET).update(req.body).digest('hex')
  const ok =
    sig.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))
  if (!ok) return res.sendStatus(401)
  const event = JSON.parse(req.body)
  // handle event.event, event.payload …
  res.sendStatus(204)
})
```

## Retries and the delivery log

A failed delivery (non-2xx, timeout, transport error) is retried up to **3 attempts** with
short backoff, then marked `failed`. The signature stays identical across retries — it is
computed over the stored body, so a retry is byte-for-byte the same request.

- `GET /api/v1/w/{ws}/webhooks/{id}/deliveries` — the delivery log: status, response code,
  error, attempt count per event.
- `POST /api/v1/w/{ws}/webhooks/{id}/test` — sends a `webhook.test` event to that hook,
  delivered regardless of its event subscriptions. The fastest way to check your endpoint
  and signature code end to end.

Webhook URLs must be public: private and internal addresses are refused, and re-checked at
delivery time (a hook whose DNS starts resolving into your infrastructure stops being
delivered).
