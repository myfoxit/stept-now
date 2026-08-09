"""Schemas for the in-app assistant's widget channel.

Two directions cross this boundary:

- the widget tells the backend where the visitor is and whether they consented to
  the assistant touching the page (`PageContextIn`);
- the widget returns the result of a deferred page op (`ClientOpResultIn`) so the
  parked agent run can resume.

`ClientOpResultIn.result` is deliberately a loose dict: it is the widget's
`PageOpResult` (url/title/elements/text/found/note/error), it travels straight
into the model's tool result, and pinning every field here would mean a schema
change every time the page runtime learns a new op.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PageContextIn(BaseModel):
    """Where the visitor is, and what the assistant may do there."""

    url: str = Field(max_length=2000)
    title: str | None = Field(default=None, max_length=300)
    path: str | None = Field(default=None, max_length=1000)
    #: Tri-state on purpose: `None` leaves an earlier answer untouched (the loader
    #: pushes context on every SPA navigation and must not silently revoke it).
    allow_actions: bool | None = None
    #: Actions the host page registered (`Stept('action', …)`), same tri-state:
    #: `None` leaves the stored defs untouched, `[]` clears them. Items are loose
    #: dicts on purpose — invalid defs are dropped by normalization, never 422'd,
    #: so a buggy page cannot break its own conversation.
    client_actions: list[dict[str, Any]] | None = Field(default=None, max_length=100)


class PageContextOut(BaseModel):
    ok: bool = True
    #: Echoed back so the widget can show the right affordance without guessing.
    page_control: bool = False
    allow_actions: bool = False
    #: Names of the client actions that survived normalization — the SDK warns
    #: about the difference so a rejected def is debuggable, not silent.
    accepted_actions: list[str] = Field(default_factory=list)


class ClientOpResultIn(BaseModel):
    """The widget's answer to one deferred page op."""

    run_id: str
    op_id: str
    result: dict[str, Any] = Field(default_factory=dict)


class ClientOpAck(BaseModel):
    ok: bool = True
    #: `resumed` when the run was re-enqueued, `ignored` for a stale/duplicate op.
    status: str = "resumed"


class PendingOpOut(BaseModel):
    """A page op waiting on this conversation.

    The realtime push is the normal delivery path; this exists for the reload
    case — a visitor who refreshes mid-guide loses the websocket frame, and
    without a way to ask "is anything waiting for me?" the run would sit until the
    sweep timed it out.
    """

    run_id: str
    op_id: str
    tool: str
    op: str
    args: dict[str, Any] = Field(default_factory=dict)


class TourStepsOut(BaseModel):
    """Steps of a tour the assistant asked the widget to play."""

    tour_id: str
    name: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    theme: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
