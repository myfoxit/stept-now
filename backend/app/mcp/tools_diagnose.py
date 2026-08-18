"""Diagnosis MCP tools: "why isn't this showing?" and "what would this visitor
see right now?".

The design rule here is the whole point of the module: **do not re-derive
delivery.** Every gate below is evaluated with the same helper the widget
bootstrap uses (``_in_schedule``, ``_frequency_allows``, ``_audience_matches``,
the same ``fnmatch``), and :func:`diagnose_contact` gets its "showing" list from
``deliverable_*`` itself rather than from a parallel implementation. A diagnosis
that can disagree with delivery is worse than none — it sends people to change
config that was never the problem.

That is why this module reaches for a few private ``_``-prefixed helpers in
``app.services.tours``. Importing them is deliberate: the alternative is a
second copy of the rules that drifts.

Vocabulary, kept distinct because conflating them reads wrong:
  * a **gate** is a JUDGMENT — "does this block delivery?" → pass / fail / unknown
  * a **condition** is a FACT — "is this filter satisfied?" → matched / unmatched
An audience filter can be `matched` while the gate it feeds still fails for a
different reason.

`unknown` is load-bearing: it means the gate depends on a runtime fact the
caller did not supply (no `url`, no `contact_id`), not that the check errored.
"""

from __future__ import annotations

from datetime import datetime
from fnmatch import fnmatch
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import AppError
from app.core.permissions import Perm
from app.mcp.annotations import READ_ONLY
from app.mcp.auth import (
    authorization_error,
    open_session,
    permission_error,
    resolve_request_key,
)
from app.mcp.server import mcp
from app.models.checklist import Checklist
from app.models.contact import Contact
from app.models.survey import Survey
from app.models.tour import Tour
from app.services import checklists as checklists_service
from app.services import segments as segments_service
from app.services import surveys as surveys_service
from app.services import tours as tours_service

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


def _gate(gate_id: str, status: str, detail: str) -> dict[str, str]:
    return {"gate": gate_id, "status": status, "detail": detail}


async def _load_contact(
    session: AsyncSession, workspace_id: str, contact_id: str | None
) -> Contact | None:
    if not contact_id:
        return None
    return (
        await session.execute(
            select(Contact).where(Contact.workspace_id == workspace_id, Contact.id == contact_id)
        )
    ).scalar_one_or_none()


async def _annotate_audience(
    session: AsyncSession,
    workspace_id: str,
    audience: dict[str, Any] | None,
    contact: Contact | None,
) -> list[dict[str, Any]]:
    """Per-filter status, with the contact's ACTUAL value attached.

    An unmatched filter that also reports the value it was compared against
    explains itself — no follow-up `get_contact` and no date arithmetic by the
    reader.
    """
    audience = audience or {}
    if audience.get("type") != "filters":
        return []
    out: list[dict[str, Any]] = []
    for spec in audience.get("filters") or []:
        entry: dict[str, Any] = {
            "field": spec.get("field"),
            "op": spec.get("op"),
            "value": spec.get("value"),
        }
        if contact is None:
            entry["status"] = UNKNOWN
            entry["note"] = "No contact supplied — pass contact_id to evaluate this filter."
        else:
            matched = await segments_service.contact_matches(session, workspace_id, contact, [spec])
            entry["status"] = "matched" if matched else "unmatched"
            entry["actual"] = _actual_value(contact, str(spec.get("field") or ""))
        out.append(entry)
    return out


def _actual_value(contact: Contact, field: str) -> Any:
    if field.startswith("attributes."):
        key = field.split(".", 1)[1]
        return (contact.attributes or {}).get(key)
    value: Any = getattr(contact, field, None)
    return value.isoformat() if isinstance(value, datetime) else value


def _trigger_gate(trigger: dict[str, Any], url: str | None) -> dict[str, str]:
    kind = (trigger or {}).get("type") or "manual"
    if kind == "manual":
        return _gate(
            "trigger",
            FAIL,
            "Trigger is `manual`, so this is NEVER auto-delivered. It only runs when something "
            "asks for it by id (Stept('startTour', id), a checklist item, the in-app agent, or "
            "browser_run_tour). This is not a bug unless you expected it to appear on its own.",
        )
    pattern = (trigger or {}).get("url_pattern")
    if not pattern:
        return _gate("trigger", FAIL, "Trigger is `url_match` but no url_pattern is set.")
    if url is None:
        return _gate(
            "trigger",
            UNKNOWN,
            f"Matches url_pattern `{pattern}` — pass `url` to check a specific page.",
        )
    if fnmatch(url, pattern):
        return _gate("trigger", PASS, f"`{url}` matches url_pattern `{pattern}`.")
    return _gate("trigger", FAIL, f"`{url}` does not match url_pattern `{pattern}`.")


def _status_gate(status: str) -> dict[str, str]:
    if status == "live":
        return _gate("status", PASS, "Status is `live`.")
    return _gate(
        "status",
        FAIL,
        f"Status is `{status}` — only `live` content is delivered. Publish it.",
    )


def _schedule_gate(schedule: dict[str, Any] | None) -> dict[str, str]:
    schedule = schedule or {}
    if not schedule.get("start_at") and not schedule.get("end_at"):
        return _gate("schedule", PASS, "No delivery window set — always on.")
    if tours_service._in_schedule(schedule, utcnow()):
        return _gate("schedule", PASS, "Inside the delivery window.")
    return _gate(
        "schedule",
        FAIL,
        f"Outside the delivery window (start_at={schedule.get('start_at')}, "
        f"end_at={schedule.get('end_at')}, now={utcnow().isoformat()}).",
    )


async def _frequency_gate(
    session: AsyncSession,
    workspace_id: str,
    tour_id: str,
    frequency: dict[str, Any] | None,
    contact: Contact | None,
) -> dict[str, str]:
    ftype = (frequency or {}).get("type") or "until_dismissed"
    if contact is None:
        return _gate(
            "frequency",
            UNKNOWN,
            f"Frequency is `{ftype}` — pass contact_id to check whether that visitor has "
            "already seen it.",
        )
    history = await tours_service._event_history(session, workspace_id, contact.id, [tour_id])
    if tours_service._frequency_allows(frequency or {}, history.get(tour_id, {}), utcnow()):
        return _gate("frequency", PASS, f"Frequency `{ftype}` still allows delivery.")
    seen = ", ".join(sorted(history.get(tour_id, {}))) or "none"
    return _gate(
        "frequency",
        FAIL,
        f"Frequency `{ftype}` excludes this contact — recorded events: {seen}. "
        "Frequency governs unsolicited delivery only; starting it by id still works.",
    )


async def _audience_gate(
    session: AsyncSession,
    workspace_id: str,
    audience: dict[str, Any] | None,
    contact: Contact | None,
) -> dict[str, str]:
    audience = audience or {}
    if audience.get("type") != "filters" or not (audience.get("filters") or []):
        return _gate("audience", PASS, "Targets everyone.")
    if contact is None:
        return _gate(
            "audience",
            UNKNOWN,
            "Audience filters are set — pass contact_id to evaluate them. Note that anonymous "
            "visitors never match filters at all.",
        )
    if await tours_service._audience_matches(session, workspace_id, audience, contact):
        return _gate("audience", PASS, "This contact matches the audience filters.")
    return _gate(
        "audience",
        FAIL,
        "This contact does not match the audience filters — see `conditions` for which one.",
    )


def _content_gate(count: int, noun: str) -> dict[str, str]:
    if count:
        return _gate("content", PASS, f"Has {count} {noun}.")
    return _gate("content", FAIL, f"No {noun} — there is nothing to render.")


def _verdict(gates: list[dict[str, str]]) -> str:
    if any(gate["status"] == FAIL for gate in gates):
        return "blocked"
    if any(gate["status"] == UNKNOWN for gate in gates):
        return "indeterminate"
    return "showing"


def _summary(verdict: str, gates: list[dict[str, str]], name: str) -> str:
    if verdict == "showing":
        return f"{name} passes every gate and should be delivered."
    if verdict == "blocked":
        blocking = [gate["gate"] for gate in gates if gate["status"] == FAIL]
        return f"{name} is blocked by: {', '.join(blocking)}."
    pending = [gate["gate"] for gate in gates if gate["status"] == UNKNOWN]
    return (
        f"{name} clears every gate that could be checked; {', '.join(pending)} need "
        "`url` and/or `contact_id` to decide."
    )


@mcp.tool(annotations=READ_ONLY)
async def diagnose_experience(
    type: str,
    experience_id: str,
    url: str | None = None,
    contact_id: str | None = None,
) -> dict[str, Any]:
    """Why isn't this tour / checklist / survey showing up?

    Evaluates the delivery gates in the order the widget applies them — status,
    content, trigger, schedule, audience, frequency — using the SAME helpers the
    widget bootstrap uses, so the answer cannot disagree with reality.

    Each gate returns `pass`, `fail`, or `unknown`. `unknown` means the gate
    needs a runtime fact you did not supply: pass `url` (the page you expect it
    on) and `contact_id` (the visitor you expect it for) to turn unknowns into
    verdicts. Audience filters come back individually annotated with the
    contact's actual value, so an unmatched filter explains itself.

    Use this BEFORE changing targeting. For "what would this visitor see across
    the whole product", use `diagnose_contact` instead.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_READ):
            return permission_error(Perm.TOURS_READ)
        workspace_id = key.workspace_id
        contact = await _load_contact(session, workspace_id, contact_id)
        if contact_id and contact is None:
            return {"error": f"Contact {contact_id} not found in this workspace"}

        try:
            if type == "tour":
                tour = await tours_service.get_tour(session, workspace_id, experience_id)
                name, status = tour.name, tour.status
                gates = [
                    _status_gate(status),
                    _content_gate(len(tour.steps or []), "steps"),
                    _trigger_gate(tour.trigger or {}, url),
                    _schedule_gate(tour.schedule),
                    await _audience_gate(session, workspace_id, tour.audience, contact),
                    await _frequency_gate(session, workspace_id, tour.id, tour.frequency, contact),
                ]
                audience = tour.audience
            elif type == "checklist":
                checklist = await checklists_service.get_checklist(
                    session, workspace_id, experience_id
                )
                name, status = checklist.name, checklist.status
                gates = [
                    _status_gate(status),
                    _content_gate(len(checklist.items or []), "items"),
                    _trigger_gate(checklist.trigger or {}, url),
                    await _audience_gate(session, workspace_id, checklist.audience, contact),
                ]
                audience = checklist.audience
            elif type == "survey":
                survey = await surveys_service.get_survey(session, workspace_id, experience_id)
                name, status = survey.name, survey.status
                gates = [
                    _status_gate(status),
                    _content_gate(len(survey.questions or []), "questions"),
                    _trigger_gate(survey.trigger or {}, url),
                    _schedule_gate(survey.schedule),
                    await _audience_gate(session, workspace_id, survey.audience, contact),
                ]
                audience = survey.audience
            else:
                return {"error": f"Unknown type {type!r} — expected tour, checklist or survey"}
        except AppError as exc:
            return {"error": exc.message}

        verdict = _verdict(gates)
        return {
            "id": experience_id,
            "type": type,
            "name": name,
            "status": status,
            "verdict": verdict,
            "summary": _summary(verdict, gates, name),
            "gates": gates,
            "conditions": await _annotate_audience(session, workspace_id, audience, contact),
            "evaluated_for": {"url": url, "contact_id": contact_id},
        }


@mcp.tool(annotations=READ_ONLY)
async def diagnose_contact(contact_id: str, url: str) -> dict[str, Any]:
    """What would this visitor see on this page, right now?

    Sorts every live experience into `showing` and `blocked`, with the priority
    ordering and the 5-per-page delivery cap already applied. The `showing` list
    is produced by the delivery code itself — it is what the widget bootstrap
    would actually return for this contact on this URL, not a re-derivation.

    Start here for "customer X says onboarding is broken", then deep-dive one
    row with `diagnose_experience`. Each blocked row names the first gate that
    stopped it.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_READ):
            return permission_error(Perm.TOURS_READ)
        workspace_id = key.workspace_id
        contact = await _load_contact(session, workspace_id, contact_id)
        if contact is None:
            return {"error": f"Contact {contact_id} not found in this workspace"}

        # Authoritative: ask delivery what it would deliver.
        delivered_tours = await tours_service.deliverable_tours(
            session, workspace_id, url=url, contact=contact
        )
        delivered_checklists = await checklists_service.deliverable_checklists(
            session, workspace_id, url=url, contact=contact
        )
        delivered_surveys = await surveys_service.deliverable_surveys(
            session, workspace_id, url=url, contact=contact
        )
        showing_ids = (
            {tour.id for tour in delivered_tours}
            | {item.get("id") for item in delivered_checklists}
            | {item.get("id") for item in delivered_surveys}
        )

        showing: list[dict[str, Any]] = [
            {"id": tour.id, "type": "tour", "name": tour.name, "priority": tour.priority}
            for tour in delivered_tours
        ]
        showing += [
            {"id": item.get("id"), "type": "checklist", "name": item.get("name")}
            for item in delivered_checklists
        ]
        showing += [
            {"id": item.get("id"), "type": "survey", "name": item.get("name")}
            for item in delivered_surveys
        ]

        blocked: list[dict[str, Any]] = []
        # The three models share the fields this loop touches (status / trigger /
        # audience / name) but have no common base declaring them, so rows are
        # walked as Any rather than duplicating the body three times.
        content_models: tuple[tuple[Any, str, str, str], ...] = (
            (Tour, "tour", "steps", "steps"),
            (Checklist, "checklist", "items", "items"),
            (Survey, "survey", "questions", "questions"),
        )
        for model, type_name, content_field, noun in content_models:
            rows: list[Any] = list(
                (await session.execute(select(model).where(model.workspace_id == workspace_id)))
                .scalars()
                .all()
            )
            for row in rows:
                if row.id in showing_ids:
                    continue
                gates = [
                    _status_gate(row.status),
                    _content_gate(len(getattr(row, content_field) or []), noun),
                    _trigger_gate(row.trigger or {}, url),
                    await _audience_gate(session, workspace_id, row.audience, contact),
                ]
                if type_name == "tour":
                    gates.append(
                        await _frequency_gate(session, workspace_id, row.id, row.frequency, contact)
                    )
                first_fail = next(
                    (gate for gate in gates if gate["status"] == FAIL),
                    None,
                )
                blocked.append(
                    {
                        "id": row.id,
                        "type": type_name,
                        "name": row.name,
                        "status": row.status,
                        "blocked_by": first_fail["gate"] if first_fail else "delivery_cap",
                        "reason": (
                            first_fail["detail"]
                            if first_fail
                            else "Passes every gate but lost the priority ordering — the widget "
                            "delivers at most 5 experiences per page."
                        ),
                    }
                )

        return {
            "contact": {
                "id": contact.id,
                "external_id": contact.external_id,
                "email": contact.email,
                "identified": bool(contact.external_id),
            },
            "url": url,
            "showing": showing,
            "blocked": blocked,
        }
