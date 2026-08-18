"""Authoring MCP tools: create / update / publish DAP experiences.

Stept's MCP surface could read tours and record them by demonstration, but not
*author* them from a spec — so "build me an onboarding flow" ended at a draft
someone had to finish by hand. These tools close that.

Every tool is THIN and routes through the same Pydantic schemas the REST API
uses (``TourCreate``/``ChecklistUpdate``/…), so an MCP-authored experience obeys
byte-for-byte the same rules as a dashboard-authored one — including the
per-step-type validators that are the difference between content that renders
and content that publishes green and never appears.

Error convention (module-wide): failures are RETURNED, never raised.
Pydantic failures come back as ``{"error": …, "details": [{loc, message}]}`` so
the caller can fix a specific field instead of guessing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import AppError
from app.core.permissions import Perm
from app.mcp.annotations import READ_ONLY, write_annotations_for
from app.mcp.auth import (
    ResolvedMcpKey,
    authorization_error,
    open_session,
    permission_error,
    resolve_request_key,
)
from app.mcp.authoring_guide import (
    CORE_SECTION_IDS,
    SECTION_IDS,
    TABLE_OF_CONTENTS,
    render_sections,
)
from app.mcp.server import mcp
from app.mcp.validation import validate_checklist, validate_survey, validate_tour
from app.models.checklist import Checklist
from app.models.survey import Survey
from app.models.tour import Tour
from app.schemas.checklists import ChecklistCreate, ChecklistUpdate
from app.schemas.surveys import SurveyCreate, SurveyUpdate
from app.schemas.tours import TourCreate, TourUpdate
from app.services import audit
from app.services import checklists as checklists_service
from app.services import surveys as surveys_service
from app.services import tours as tours_service

#: The three authorable experience types, and everything each one needs to be
#: created, updated, published, validated and diagnosed by one code path.
EXPERIENCE_TYPES = ("tour", "checklist", "survey")

#: Create/update schema pair per type, backing ``get_experience_schema``. Lives
#: at module scope because that tool's parameter is named ``type`` (the wire name
#: callers expect), which shadows the builtin inside the function body.
_SCHEMA_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "tour": (TourCreate, TourUpdate),
    "checklist": (ChecklistCreate, ChecklistUpdate),
    "survey": (SurveyCreate, SurveyUpdate),
}


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    """A Pydantic failure the caller can act on.

    ``input`` is deliberately dropped: a rejected payload could otherwise echo
    back whatever the caller sent, and these tools accept free-form dicts.
    """
    return {
        "error": "Invalid input — see details.",
        "details": [
            {
                "loc": ".".join(str(part) for part in err["loc"]),
                "message": err["msg"],
            }
            for err in exc.errors()
        ],
    }


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    """Strip unset optionals so a partial update patches instead of clearing."""
    return {key: value for key, value in values.items() if value is not None}


def _dump(model: BaseModel | None, **kwargs: Any) -> dict[str, Any] | None:
    return None if model is None else model.model_dump(**kwargs)


def _tour_out(tour: Tour) -> dict[str, Any]:
    return {
        "id": tour.id,
        "type": "tour",
        "name": tour.name,
        "kind": tour.kind,
        "status": tour.status,
        "version": tour.version,
        "steps": len(tour.steps or []),
        "trigger": tour.trigger,
        "priority": tour.priority,
    }


def _checklist_out(checklist: Checklist) -> dict[str, Any]:
    return {
        "id": checklist.id,
        "type": "checklist",
        "name": checklist.name,
        "status": checklist.status,
        "version": checklist.version,
        "items": len(checklist.items or []),
        "trigger": checklist.trigger,
        "priority": checklist.priority,
    }


def _survey_out(survey: Survey) -> dict[str, Any]:
    return {
        "id": survey.id,
        "type": "survey",
        "name": survey.name,
        "status": survey.status,
        "version": survey.version,
        "questions": len(survey.questions or []),
        "presentation": survey.presentation,
        "trigger": survey.trigger,
        "priority": survey.priority,
    }


async def _audited(
    session: Any,
    key: ResolvedMcpKey,
    *,
    action: str,
    target_type: str,
    target_id: str,
    meta: dict[str, Any],
) -> None:
    await audit.record(
        session,
        key.workspace_id,
        actor=key.actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        meta={**meta, "via": "mcp"},
    )


# ---------------------------------------------------------------------------
# guide + schema introspection
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def get_authoring_guide(section: list[str] | None = None) -> dict[str, Any]:
    """The contract for authoring DAP content that actually renders. Read this
    BEFORE creating or editing a tour, checklist or survey.

    Called with no arguments it returns the core sections (lifecycle +
    publish requirements) plus a table of contents. Then fetch the sections for
    your content type in ONE call — `section` takes an array, e.g.
    `["tour-steps", "targets", "targeting"]`.

    Sections: lifecycle, tour-steps, targets, targeting, checklists, surveys,
    banners-announcements, markdown, sdk, publish-requirements, diagnosis.
    """
    requested = list(section) if section else list(CORE_SECTION_IDS)
    rendered, unknown = render_sections(requested)
    out: dict[str, Any] = {"contents": TABLE_OF_CONTENTS, "sections": rendered}
    if unknown:
        out["unknown_sections"] = unknown
        out["available_sections"] = list(SECTION_IDS)
    return out


@mcp.tool(annotations=READ_ONLY)
async def get_experience_schema(type: str) -> dict[str, Any]:
    """The exact JSON Schema for authoring one experience type — field names,
    enums, bounds and which fields are required.

    `type` is "tour", "checklist" or "survey". Call this before writing a body
    you are unsure about rather than guessing at shapes; the schema returned is
    the one the create/update tools validate against, so anything it accepts
    will be accepted.
    """
    if type not in _SCHEMA_MODELS:
        return {"error": f"Unknown type {type!r} — expected one of {', '.join(EXPERIENCE_TYPES)}"}
    create_model, update_model = _SCHEMA_MODELS[type]
    return {
        "type": type,
        "create": create_model.model_json_schema(),
        "update": update_model.model_json_schema(),
    }


# ---------------------------------------------------------------------------
# tours
# ---------------------------------------------------------------------------


@mcp.tool(annotations=write_annotations_for("create_tour"))
async def create_tour(
    name: str,
    steps: list[dict[str, Any]] | None = None,
    description: str = "",
    kind: str = "flow",
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Create a product tour as a DRAFT. Read `get_authoring_guide` first.

    `kind` is "flow" (multi-step), "banner" (single docked bar) or
    "announcement" (single centered modal). `steps` follow the step contract —
    tooltip/hotspot/action steps require a `selector`, action steps require an
    `action`, wait steps require a `wait`.

    `trigger` decides delivery: `{"type": "url_match", "url_pattern": "*/app*"}`
    auto-delivers on matching pages; `{"type": "manual"}` (the default) is only
    ever started by id. Drafts reach nobody — call `publish_tour` when ready,
    ideally after `validate_experience`.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_MANAGE):
            return permission_error(Perm.TOURS_MANAGE)
        try:
            body = TourCreate.model_validate(
                _drop_none(
                    {
                        "name": name,
                        "description": description,
                        "kind": kind,
                        "steps": steps,
                        "trigger": trigger,
                        "audience": audience,
                        "schedule": schedule,
                        "frequency": frequency,
                        "settings": settings,
                        "theme": theme,
                        "priority": priority,
                    }
                )
            )
        except ValidationError as exc:
            return _validation_error(exc)
        try:
            tour = await tours_service.create_tour(
                session,
                key.workspace_id,
                actor=key.actor,
                name=body.name,
                description=body.description,
                kind=body.kind,
                trigger=body.trigger.model_dump(),
                audience=body.audience.model_dump(),
                schedule=body.schedule.model_dump(mode="json", exclude_none=True),
                frequency=body.frequency.model_dump(exclude_none=True),
                priority=body.priority,
                settings=body.settings.model_dump(),
                steps=[step.model_dump(by_alias=True) for step in body.steps],
                theme=body.theme.model_dump(),
            )
        except AppError as exc:
            return {"error": exc.message}
        await _audited(
            session,
            key,
            action="tour.create",
            target_type="tour",
            target_id=tour.id,
            meta={"name": tour.name, "kind": tour.kind},
        )
        return _tour_out(tour)


@mcp.tool(annotations=write_annotations_for("update_tour"))
async def update_tour(
    tour_id: str,
    name: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    description: str | None = None,
    kind: str | None = None,
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """Update a tour. Only the fields you pass change; omitted fields are left
    alone.

    `steps` is a FULL REPLACEMENT, not a patch — pass the complete list, because
    a step you omit is deleted. Read the tour with `get_tour_steps` first if you
    are editing rather than replacing. Editing is allowed in any status; a
    content change bumps `version`, which invalidates visitors who are mid-tour.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_MANAGE):
            return permission_error(Perm.TOURS_MANAGE)
        try:
            body = TourUpdate.model_validate(
                _drop_none(
                    {
                        "name": name,
                        "description": description,
                        "kind": kind,
                        "steps": steps,
                        "trigger": trigger,
                        "audience": audience,
                        "schedule": schedule,
                        "frequency": frequency,
                        "settings": settings,
                        "theme": theme,
                        "priority": priority,
                    }
                )
            )
        except ValidationError as exc:
            return _validation_error(exc)
        try:
            tour = await tours_service.update_tour(
                session,
                key.workspace_id,
                tour_id,
                actor=key.actor,
                name=body.name,
                description=body.description,
                kind=body.kind,
                trigger=_dump(body.trigger),
                audience=_dump(body.audience),
                schedule=_dump(body.schedule, mode="json", exclude_none=True),
                frequency=_dump(body.frequency, exclude_none=True),
                priority=body.priority,
                settings=_dump(body.settings),
                steps=(
                    None
                    if body.steps is None
                    else [step.model_dump(by_alias=True) for step in body.steps]
                ),
                theme=_dump(body.theme),
            )
        except AppError as exc:
            return {"error": exc.message}
        await _audited(
            session,
            key,
            action="tour.update",
            target_type="tour",
            target_id=tour.id,
            meta={"name": tour.name, "version": tour.version},
        )
        return _tour_out(tour)


@mcp.tool(annotations=write_annotations_for("publish_tour"))
async def publish_tour(tour_id: str) -> dict[str, Any]:
    """Publish a tour — sets status to `live` so the widget starts delivering it.

    Validate first: `validate_experience("tour", tour_id)` catches content that
    publishes green and never renders. A `manual`-trigger tour goes live but is
    still only started by id.
    """
    return await _set_experience_status("tour", tour_id, publish=True)


@mcp.tool(annotations=write_annotations_for("pause_tour"))
async def pause_tour(tour_id: str) -> dict[str, Any]:
    """Pause a tour — stops delivery, keeps the tour and its analytics. Publish
    again to resume."""
    return await _set_experience_status("tour", tour_id, publish=False)


# ---------------------------------------------------------------------------
# checklists
# ---------------------------------------------------------------------------


@mcp.tool(annotations=write_annotations_for("create_checklist"))
async def create_checklist(
    name: str,
    items: list[dict[str, Any]] | None = None,
    description: str = "",
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    launcher: dict[str, Any] | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Create an onboarding checklist as a DRAFT (max 20 items).

    Each item has a `title`, optional markdown `body`, an `action` (what its CTA
    does — `start_tour` / `open_url` / `open_messenger` / `none`) and a
    `completion` (how it ticks off — `manual` / `tour_completed` /
    `url_visited`). Pair `action: start_tour` with `completion: tour_completed`
    on the same `tour_id` so doing the thing checks the box.

    See the `checklists` section of `get_authoring_guide` for the full shapes.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_MANAGE):
            return permission_error(Perm.TOURS_MANAGE)
        try:
            body = ChecklistCreate.model_validate(
                _drop_none(
                    {
                        "name": name,
                        "description": description,
                        "items": items,
                        "trigger": trigger,
                        "audience": audience,
                        "theme": theme,
                        "launcher": launcher,
                        "priority": priority,
                    }
                )
            )
        except ValidationError as exc:
            return _validation_error(exc)
        try:
            checklist = await checklists_service.create_checklist(
                session,
                key.workspace_id,
                actor=key.actor,
                name=body.name,
                description=body.description,
                items=[item.model_dump() for item in body.items],
                trigger=body.trigger.model_dump(),
                audience=body.audience.model_dump(),
                theme=body.theme.model_dump(),
                launcher=body.launcher.model_dump(),
                priority=body.priority,
            )
        except AppError as exc:
            return {"error": exc.message}
        return _checklist_out(checklist)


@mcp.tool(annotations=write_annotations_for("update_checklist"))
async def update_checklist(
    checklist_id: str,
    name: str | None = None,
    items: list[dict[str, Any]] | None = None,
    description: str | None = None,
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    launcher: dict[str, Any] | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """Update a checklist. Only the fields you pass change.

    `items` is a FULL REPLACEMENT — an item you omit is deleted, and visitor
    progress on it goes with it. Read the checklist first if you are editing
    rather than replacing.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_MANAGE):
            return permission_error(Perm.TOURS_MANAGE)
        try:
            body = ChecklistUpdate.model_validate(
                _drop_none(
                    {
                        "name": name,
                        "description": description,
                        "items": items,
                        "trigger": trigger,
                        "audience": audience,
                        "theme": theme,
                        "launcher": launcher,
                        "priority": priority,
                    }
                )
            )
        except ValidationError as exc:
            return _validation_error(exc)
        try:
            checklist = await checklists_service.update_checklist(
                session,
                key.workspace_id,
                checklist_id,
                actor=key.actor,
                name=body.name,
                description=body.description,
                items=(None if body.items is None else [item.model_dump() for item in body.items]),
                trigger=_dump(body.trigger),
                audience=_dump(body.audience),
                theme=_dump(body.theme),
                launcher=_dump(body.launcher),
                priority=body.priority,
            )
        except AppError as exc:
            return {"error": exc.message}
        return _checklist_out(checklist)


@mcp.tool(annotations=write_annotations_for("publish_checklist"))
async def publish_checklist(checklist_id: str) -> dict[str, Any]:
    """Publish a checklist — its launcher starts appearing on matching pages."""
    return await _set_experience_status("checklist", checklist_id, publish=True)


@mcp.tool(annotations=write_annotations_for("pause_checklist"))
async def pause_checklist(checklist_id: str) -> dict[str, Any]:
    """Pause a checklist — hides the launcher, keeps visitor progress."""
    return await _set_experience_status("checklist", checklist_id, publish=False)


# ---------------------------------------------------------------------------
# surveys
# ---------------------------------------------------------------------------


@mcp.tool(annotations=write_annotations_for("create_survey"))
async def create_survey(
    name: str,
    questions: list[dict[str, Any]] | None = None,
    presentation: str = "slideout",
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    thanks_message: str | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Create an in-app survey as a DRAFT (max 10 questions).

    Question types: `nps` (0-10), `rating` (1-5), `text`, and `select` which
    needs 2-6 unique non-blank `options`. `presentation` is "slideout" or
    "modal". `frequency` defaults to `once`, which is almost always right for a
    survey.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_MANAGE):
            return permission_error(Perm.TOURS_MANAGE)
        try:
            body = SurveyCreate.model_validate(
                _drop_none(
                    {
                        "name": name,
                        "questions": questions,
                        "presentation": presentation,
                        "trigger": trigger,
                        "audience": audience,
                        "schedule": schedule,
                        "frequency": frequency,
                        "theme": theme,
                        "thanks_message": thanks_message,
                        "priority": priority,
                    }
                )
            )
        except ValidationError as exc:
            return _validation_error(exc)
        try:
            survey = await surveys_service.create_survey(
                session,
                key.workspace_id,
                actor=key.actor,
                name=body.name,
                questions=[question.model_dump() for question in body.questions],
                presentation=body.presentation,
                trigger=body.trigger.model_dump(),
                audience=body.audience.model_dump(),
                schedule=body.schedule.model_dump(mode="json", exclude_none=True),
                frequency=body.frequency.model_dump(exclude_none=True),
                priority=body.priority,
                theme=body.theme.model_dump(),
                thanks_message=body.thanks_message,
            )
        except AppError as exc:
            return {"error": exc.message}
        return _survey_out(survey)


@mcp.tool(annotations=write_annotations_for("update_survey"))
async def update_survey(
    survey_id: str,
    name: str | None = None,
    questions: list[dict[str, Any]] | None = None,
    presentation: str | None = None,
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    thanks_message: str | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """Update a survey. Only the fields you pass change.

    `questions` is a FULL REPLACEMENT — a question you omit is deleted. Existing
    responses are kept but will reference question ids that no longer exist, so
    prefer pausing a survey and creating a new one over re-cutting its questions
    once it has responses.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_MANAGE):
            return permission_error(Perm.TOURS_MANAGE)
        try:
            body = SurveyUpdate.model_validate(
                _drop_none(
                    {
                        "name": name,
                        "questions": questions,
                        "presentation": presentation,
                        "trigger": trigger,
                        "audience": audience,
                        "schedule": schedule,
                        "frequency": frequency,
                        "theme": theme,
                        "thanks_message": thanks_message,
                        "priority": priority,
                    }
                )
            )
        except ValidationError as exc:
            return _validation_error(exc)
        try:
            survey = await surveys_service.update_survey(
                session,
                key.workspace_id,
                survey_id,
                actor=key.actor,
                name=body.name,
                questions=(
                    None
                    if body.questions is None
                    else [question.model_dump() for question in body.questions]
                ),
                presentation=body.presentation,
                trigger=_dump(body.trigger),
                audience=_dump(body.audience),
                schedule=_dump(body.schedule, mode="json", exclude_none=True),
                frequency=_dump(body.frequency, exclude_none=True),
                priority=body.priority,
                theme=_dump(body.theme),
                thanks_message=body.thanks_message,
            )
        except AppError as exc:
            return {"error": exc.message}
        return _survey_out(survey)


@mcp.tool(annotations=write_annotations_for("publish_survey"))
async def publish_survey(survey_id: str) -> dict[str, Any]:
    """Publish a survey — it starts being shown to matching visitors."""
    return await _set_experience_status("survey", survey_id, publish=True)


@mcp.tool(annotations=write_annotations_for("pause_survey"))
async def pause_survey(survey_id: str) -> dict[str, Any]:
    """Pause a survey — stops showing it, keeps the responses."""
    return await _set_experience_status("survey", survey_id, publish=False)


# ---------------------------------------------------------------------------
# shared publish/pause + validation
# ---------------------------------------------------------------------------


async def _set_experience_status(type: str, experience_id: str, *, publish: bool) -> dict[str, Any]:
    """One publish/pause path for all three types.

    Each service owns its own status transition (they differ: tours emit
    delivery events, checklists broadcast to open widgets), so this dispatches
    rather than reimplementing.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_MANAGE):
            return permission_error(Perm.TOURS_MANAGE)
        try:
            if type == "tour":
                call = tours_service.publish_tour if publish else tours_service.pause_tour
                return _tour_out(
                    await call(session, key.workspace_id, experience_id, actor=key.actor)
                )
            if type == "checklist":
                cl_call = (
                    checklists_service.publish_checklist
                    if publish
                    else checklists_service.pause_checklist
                )
                return _checklist_out(
                    await cl_call(session, key.workspace_id, experience_id, actor=key.actor)
                )
            sv_call = surveys_service.publish_survey if publish else surveys_service.pause_survey
            return _survey_out(
                await sv_call(session, key.workspace_id, experience_id, actor=key.actor)
            )
        except AppError as exc:
            return {"error": exc.message}


@mcp.tool(annotations=READ_ONLY)
async def validate_experience(type: str, experience_id: str) -> dict[str, Any]:
    """Dry-run check before publishing: would this content actually reach
    anyone, and would it render?

    The schema validators already rejected malformed input at create/update
    time. This catches the next class of problem — well-formed content that
    cannot work: a flow with no steps, a tooltip whose selector is empty, a
    checklist item pointing at a deleted tour, an audience filter matching zero
    contacts, a schedule that already ended.

    Returns `{ok, errors, warnings}`. Errors mean publishing produces something
    broken or unreachable; warnings mean it renders but probably not to whom you
    intended. `ok` is true when there are no errors.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.TOURS_READ):
            return permission_error(Perm.TOURS_READ)
        try:
            if type == "tour":
                tour = await tours_service.get_tour(session, key.workspace_id, experience_id)
                report = await validate_tour(session, key.workspace_id, tour)
            elif type == "checklist":
                checklist = await checklists_service.get_checklist(
                    session, key.workspace_id, experience_id
                )
                report = await validate_checklist(session, key.workspace_id, checklist)
            elif type == "survey":
                survey = await surveys_service.get_survey(session, key.workspace_id, experience_id)
                report = await validate_survey(session, key.workspace_id, survey)
            else:
                return {
                    "error": (
                        f"Unknown type {type!r} — expected one of {', '.join(EXPERIENCE_TYPES)}"
                    )
                }
        except AppError as exc:
            return {"error": exc.message}
        return report
