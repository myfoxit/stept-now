"""Demo knowledge seed: real Stept product docs + published help-center articles.

Creates the "Stept product docs" text source with three genuinely useful
markdown documents (ingested inline, so search works immediately after
seeding) and a "General" collection with two published articles reusing that
content. Idempotent: re-runs update nothing that already exists.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import Actor
from app.core.storage import get_storage
from app.models.article import Article
from app.models.knowledge import Document, KnowledgeSource
from app.rag.ingestion import ingest_document
from app.seed import SeedContext
from app.services import articles as articles_service

SOURCE_NAME = "Stept product docs"

WIDGET_DOC_TITLE = "Getting started & installing the chat widget"
WIDGET_DOC = """\
# Getting started & installing the chat widget

Stept's chat widget is the fastest way to talk to your customers. It is a small
script you embed on your website; visitors get a launcher bubble, and every
conversation lands in your shared inbox where humans and AI agents work together.

## Install the widget

Every workspace ships with a default "Website widget" inbox. Open **Settings →
Inboxes**, select the widget inbox, and copy the embed snippet. It looks like
this:

```html
<script>
  window.SteptSettings = { widgetKey: "wk_your_key_here" };
</script>
<script async src="https://your-stept-host/widget-assets/loader.js"></script>
```

Paste the snippet just before the closing `</body>` tag on every page where you
want the widget to appear. The `widgetKey` (starting with `wk_`) is the public
identifier of your widget inbox — it is safe to expose in your page source. The
loader script is tiny and loads the widget iframe lazily, so it will not slow
down your page.

## Identify your users

By default visitors are anonymous. To attach conversations to known users, pass
identity fields in `SteptSettings`:

```html
<script>
  window.SteptSettings = {
    widgetKey: "wk_your_key_here",
    user: { externalId: "user-42", email: "ada@example.com", name: "Ada" }
  };
</script>
```

For production we strongly recommend enabling **identity verification**: your
backend signs the user's external id with the HMAC secret from the inbox
settings and you pass the signature as `userHash`. With verification enabled,
Stept marks the contact as verified and rejects spoofed identities.

## Customize appearance and behavior

In the widget inbox settings you can change the accent color, the launcher
position (bottom-right or bottom-left), and the greeting shown when the
messenger opens (for example "Hi! How can we help?"). Office hours let you set
expectations: outside of the hours you configure, the widget shows your away
message and collects the visitor's email for follow-up.

## Test your installation

Open your site in a private browser window, click the launcher, and send a test
message. The conversation appears instantly in your Stept inbox — replies from
your team (or your AI agent) stream back to the visitor in real time. If the
widget does not appear, check that the snippet is present, that the `widgetKey`
matches the inbox, and that your domain is allowed to load the script.

That is all it takes: paste the snippet, optionally identify users, and start
answering. Most teams are live in under five minutes.
"""

AGENTS_DOC_TITLE = "How AI agents, approvals and handoff work"
AGENTS_DOC = """\
# How AI agents, approvals and handoff work

Stept ships an AI agent that can resolve support conversations on its own, ask
a human for sign-off before doing anything sensitive, and hand off gracefully
when a person is the better answer.

## What the AI agent does

When a new conversation arrives in an inbox with an AI agent enabled, the agent
reads the customer's message, searches your knowledge base (product docs,
crawled pages, and published help-center articles), and drafts an answer with
citations back to the exact sources it used. If the knowledge base does not
contain a confident answer, the agent says so and hands the conversation to
your team instead of guessing.

While the AI agent owns a conversation its status is **pending** — it does not
clutter your team's open queue. Every step the agent takes (searches, tool
calls, drafts) is recorded in an agent run, so you can always audit exactly why
it answered the way it did.

## Approvals: human sign-off for sensitive actions

Agents can use tools — issuing a refund, updating a subscription, or calling a
custom action against your own API. Any tool can be marked **requires
approval**. When the agent wants to run such a tool, it pauses and creates an
approval request. Teammates with the approve permission see the request with
the full context: the conversation, the tool, and the exact arguments the agent
wants to use. Approve it and the agent continues; reject it and the agent is
told why, so it can adjust its answer or escalate to a human. Nothing sensitive
ever happens without a person in the loop.

## Handoff to your team

Handoff happens in three ways. The agent hands off on its own when it cannot
answer confidently or when the customer is frustrated. The customer can ask for
a human at any time — phrases like "talk to a person" trigger an immediate
handoff. And your teammates can take over any pending conversation with one
click. On handoff the conversation moves to **open**, is routed by your
assignment rules, and the full AI transcript stays visible so nobody asks the
customer to repeat themselves.

## Configuring providers and models

Stept is provider-agnostic: connect Anthropic, OpenAI, Google, or any
OpenAI-compatible endpoint (including local Ollama) under **Settings → AI**.
Pick a default chat model and, optionally, an embedding model for retrieval.
Without any API key configured, Stept falls back to a deterministic offline
mock provider — useful for demos and tests, and it means the product works out
of the box before you paste a single key.
"""

BILLING_DOC_TITLE = "Plans, billing & refund policy (demo)"
BILLING_DOC = """\
# Plans, billing & refund policy (demo)

This is demo content for the Stept sample workspace. It shows how a typical
billing page becomes instantly answerable by the AI agent once it is in the
knowledge base.

## Plans

- **Free** — $0 forever. 2 seats, the chat widget, shared inbox, help center,
  and community support. Great for trying Stept or for very small teams.
- **Pro** — $49 per seat per month (billed annually) or $59 billed monthly.
  Everything in Free plus AI agents with approvals, unlimited knowledge
  sources, automation rules, product tours, and reporting.
- **Enterprise** — custom pricing. Everything in Pro plus SSO/SAML, custom
  roles, audit log export, a dedicated success manager, and a 99.9% uptime SLA.

You can change plans at any time from **Settings → Billing**. Upgrades take
effect immediately and we prorate the difference; downgrades apply at the start
of your next billing cycle so you never lose time you already paid for.

## Seats and usage

A seat is any teammate who can log in to the dashboard. Contacts, conversations
and AI answers are unlimited on every plan. Adding a seat mid-cycle is prorated
to the day; removing a seat credits your next invoice.

## Refund policy

We want you to be happy with Stept. If you are not, we offer a **full refund
within 30 days** of your first paid invoice — no questions asked. Email
billing@stept.example or just ask in the messenger, and include the workspace
name. Refunds are issued to the original payment method within 5–10 business
days.

After the first 30 days, we do not refund partial billing periods, but you can
cancel at any time and keep access until the end of the period you paid for.
Annual plans canceled within the first 30 days are refunded in full; after
that, the unused months beyond the current one are refunded on request, minus
the monthly-rate difference for the months used.

## Invoices and payment methods

We accept all major credit cards, and for Enterprise plans also ACH and SEPA
bank transfers with net-30 invoicing. Invoices are emailed to your billing
contact and are always available under **Settings → Billing → Invoices**. You
can add a VAT/tax ID and a purchase-order number to invoices at any time.

## Common questions

**Can I try Pro before paying?** Yes — every new workspace gets a 14-day Pro
trial, no credit card required. **What happens when a trial ends?** You drop to
Free automatically; nothing is deleted. **Do you offer discounts?** Nonprofits
and open-source projects get 50% off Pro — contact us with details.
"""

_DOCS: list[tuple[str, str]] = [
    (WIDGET_DOC_TITLE, WIDGET_DOC),
    (AGENTS_DOC_TITLE, AGENTS_DOC),
    (BILLING_DOC_TITLE, BILLING_DOC),
]

_ARTICLES: list[tuple[str, str, str]] = [
    ("installing-the-chat-widget", WIDGET_DOC_TITLE, WIDGET_DOC),
    ("plans-billing-and-refunds", BILLING_DOC_TITLE, BILLING_DOC),
]


async def _seed_source(session: AsyncSession, workspace_id: str) -> KnowledgeSource:
    source = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.workspace_id == workspace_id,
                KnowledgeSource.name == SOURCE_NAME,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = KnowledgeSource(workspace_id=workspace_id, type="text", name=SOURCE_NAME)
        session.add(source)
        await session.flush()
    return source


async def _seed_document(
    session: AsyncSession, source: KnowledgeSource, title: str, text: str
) -> Document:
    document = (
        await session.execute(
            select(Document).where(Document.source_id == source.id, Document.title == title)
        )
    ).scalar_one_or_none()
    if document is None:
        stored = await get_storage().save(f"{title}.md", text.encode("utf-8"))
        document = Document(
            workspace_id=source.workspace_id,
            source_id=source.id,
            title=title,
            uri=stored.key,
            mime="text/markdown",
            status="pending",
            meta={"filename": f"{title}.md", "seed": True},
        )
        session.add(document)
        await session.flush()
    await ingest_document(session, document, text)  # hash-skip makes re-runs cheap
    return document


async def seed(session: AsyncSession, ctx: SeedContext) -> None:
    source = await _seed_source(session, ctx.workspace.id)
    for title, text in _DOCS:
        await _seed_document(session, source, title, text)

    actor = Actor(type="user", id=ctx.owner.id, label=ctx.owner.name)
    collections = await articles_service.list_collections(session, ctx.workspace.id)
    general = next((c for c in collections if c.slug == "general"), None)
    if general is None:
        general = await articles_service.create_collection(
            session,
            ctx.workspace.id,
            actor=actor,
            name="General",
            slug="general",
            description="Everything you need to get the most out of Stept.",
            icon="📚",
        )

    for slug, title, body in _ARTICLES:
        existing = (
            await session.execute(
                select(Article).where(
                    Article.workspace_id == ctx.workspace.id, Article.slug == slug
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        article = await articles_service.create_article(
            session,
            ctx.workspace.id,
            actor=actor,
            title=title,
            body=body,
            slug=slug,
            collection_id=general.id,
        )
        await articles_service.publish_article(session, ctx.workspace.id, article.id, actor=actor)
