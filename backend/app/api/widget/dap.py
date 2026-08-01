"""Widget experiences bootstrap + Chrome-extension API.

Two audiences share this module because they serve one product surface (DAP),
but their auth differs:

- `GET /api/widget/experiences` — the widget bootstrap. Light widget-key auth
  (same as the public tours routes), returning every deliverable experience kind
  in one round trip.
- `/api/widget/dap/*` — the logged-in Chrome extension. Bearer JWT (`typ`
  "extension", or "recorder" for the legacy paste-a-token flow) whose subject's
  membership + `tours:manage` are re-validated on EVERY call, so revoking a
  member instantly kills their stored token.

Both are mounted under the open-CORS `/api/widget` prefix and never use cookies.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.widget.media import save_public
from app.api.widget.tours import contact_from_token, resolve_widget_key
from app.core.config import get_settings
from app.core.deps import Db
from app.core.errors import PayloadTooLargeError, UnauthorizedError, ValidationFailure
from app.core.events import Actor
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.tours import (
    DapAuthCheckOut,
    DapStepsPut,
    DapTourCreate,
    DapTourPatch,
    DapTourSummary,
    ExperiencesOut,
    ScreenshotOut,
    SnapshotOut,
    TourOut,
)
from app.services import tours as tours_service

router = APIRouter()

SCREENSHOT_CONTENT_TYPES = {"image/png", "image/jpeg"}
SCREENSHOT_MAX_BYTES = 2 * 1024 * 1024
# A DOM replica is one page's markup plus its inlined same-origin CSS. Real
# app screens land at 200 KB–2 MB; the cap is the point past which a capture is
# more likely a runaway data-URI than a usable sandbox screen.
SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# extension auth
# ---------------------------------------------------------------------------


async def authorize_extension(session: AsyncSession, request: Request) -> tuple[str, str]:
    """(workspace_id, user_id) for the calling extension, or 401/403."""
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise UnauthorizedError("Missing extension token")
    return await tours_service.authorize_extension(session, header[7:].strip())


def _actor(user_id: str) -> Actor:
    return Actor(type="user", id=user_id)


# ---------------------------------------------------------------------------
# widget bootstrap
# ---------------------------------------------------------------------------


async def _optional_experiences(
    module_name: str, fn_name: str, session: AsyncSession, workspace_id: str, **kwargs: Any
) -> list[dict[str, Any]]:
    """Call a sibling delivery service if it has shipped yet.

    Checklists/surveys land in a parallel workstream; the bootstrap degrades to
    tours-only rather than failing while they are in flight."""
    try:
        module = __import__(f"app.services.{module_name}", fromlist=[fn_name])
        fn = getattr(module, fn_name)
    except (ImportError, AttributeError):
        return []
    result = await fn(session, workspace_id, **kwargs)
    return list(result or [])


@router.get("/experiences", response_model=ExperiencesOut)
async def list_experiences(request: Request, session: Db, widget_key: str, url: str):
    """One call, every deliverable DAP experience for this page + visitor."""
    workspace_id, _inbox = await resolve_widget_key(session, widget_key)
    contact = await contact_from_token(request, session, workspace_id)
    tours = await tours_service.deliverable_tours(session, workspace_id, url=url, contact=contact)
    checklists = await _optional_experiences(
        "checklists", "deliverable_checklists", session, workspace_id, url=url, contact=contact
    )
    surveys = await _optional_experiences(
        "surveys", "deliverable_surveys", session, workspace_id, url=url, contact=contact
    )
    return ExperiencesOut(
        tours=[tours_service.widget_tour_out(t) for t in tours],
        checklists=checklists,
        surveys=surveys,
    )


# ---------------------------------------------------------------------------
# extension API
# ---------------------------------------------------------------------------


@router.post("/dap/auth/check", response_model=DapAuthCheckOut)
async def auth_check(request: Request, session: Db):
    """Extension settings screen: confirm the stored token still works."""
    workspace_id, user_id = await authorize_extension(session, request)
    workspace = await session.get(Workspace, workspace_id)
    user = await session.get(User, user_id)
    return DapAuthCheckOut(
        workspace_id=workspace_id,
        workspace_name=workspace.name if workspace is not None else "",
        user_name=user.name if user is not None else "",
        perms_ok=True,
        app_base_url=get_settings().app_base_url,
    )


@router.get("/dap/tours", response_model=list[DapTourSummary])
async def list_dap_tours(request: Request, session: Db):
    workspace_id, _user_id = await authorize_extension(session, request)
    tours = await tours_service.list_tours(session, workspace_id)
    return [
        DapTourSummary(
            id=t.id,
            name=t.name,
            kind=t.kind or "flow",
            status=t.status,
            steps_count=len(t.steps or []),
            version=t.version,
            updated_at=t.updated_at,
        )
        for t in tours
    ]


@router.post("/dap/tours", response_model=TourOut, status_code=201)
async def create_dap_tour(body: DapTourCreate, request: Request, session: Db):
    """Save a recording as a draft tour (rich superset of the legacy recorder)."""
    workspace_id, user_id = await authorize_extension(session, request)
    tour = await tours_service.create_extension_draft(
        session,
        workspace_id,
        user_id=user_id,
        name=body.name,
        url_pattern=body.url_pattern,
        steps=[s.model_dump(by_alias=True) for s in body.steps],
    )
    return TourOut.model_validate(tour)


@router.get("/dap/tours/{tour_id}", response_model=TourOut)
async def get_dap_tour(tour_id: str, request: Request, session: Db):
    workspace_id, _user_id = await authorize_extension(session, request)
    tour = await tours_service.get_tour(session, workspace_id, tour_id)
    return TourOut.model_validate(tour)


@router.put("/dap/tours/{tour_id}/steps", response_model=TourOut)
async def put_dap_steps(tour_id: str, body: DapStepsPut, request: Request, session: Db):
    """Replace the deck; 409 when the dashboard edited it since `base_version`."""
    workspace_id, user_id = await authorize_extension(session, request)
    tour = await tours_service.update_steps(
        session,
        workspace_id,
        tour_id,
        actor=_actor(user_id),
        steps=[s.model_dump(by_alias=True) for s in body.steps],
        base_version=body.base_version,
    )
    return TourOut.model_validate(tour)


@router.patch("/dap/tours/{tour_id}", response_model=TourOut)
async def patch_dap_tour(tour_id: str, body: DapTourPatch, request: Request, session: Db):
    workspace_id, user_id = await authorize_extension(session, request)
    tour = await tours_service.patch_draft_meta(
        session,
        workspace_id,
        tour_id,
        actor=_actor(user_id),
        name=body.name,
        url_pattern=body.url_pattern,
        url_pattern_set="url_pattern" in body.model_fields_set,
    )
    return TourOut.model_validate(tour)


@router.post("/dap/screenshots", response_model=ScreenshotOut, status_code=201)
async def upload_screenshot(file: UploadFile, request: Request, session: Db):
    """Tour-independent upload: the recorder captures eagerly, long before a
    draft exists, and references the returned key as `screenshot_key` on save.
    Orphaned screenshots are acceptable (v1)."""
    workspace_id, _user_id = await authorize_extension(session, request)
    content_type = file.content_type or "application/octet-stream"
    if content_type not in SCREENSHOT_CONTENT_TYPES:
        raise ValidationFailure("Screenshots must be image/png or image/jpeg")
    data = await file.read()
    if not data:
        raise ValidationFailure("Empty screenshot")
    if len(data) > SCREENSHOT_MAX_BYTES:
        raise PayloadTooLargeError("Screenshots are limited to 2 MB")
    key = await save_public(workspace_id, file.filename or "screenshot.png", data)
    return ScreenshotOut(key=key)


@router.post("/dap/snapshots", response_model=SnapshotOut, status_code=201)
async def upload_snapshot(file: UploadFile, request: Request, session: Db):
    """Store one sandbox DOM replica and hand back its `sandbox_key`.

    Like screenshots this is tour-independent: the recorder captures a replica
    per step long before a draft exists. The body is the JSON envelope produced
    by `@stept/dom-capture`'s `captureSnapshot` — it is stored and re-served
    verbatim as `application/json`, never as HTML, so the markup inside can only
    ever execute inside the sandboxed iframe the player builds for it.
    """
    workspace_id, _user_id = await authorize_extension(session, request)
    data = await file.read()
    if not data:
        raise ValidationFailure("Empty snapshot")
    if len(data) > SNAPSHOT_MAX_BYTES:
        raise PayloadTooLargeError("Sandbox snapshots are limited to 8 MB")
    try:
        envelope = json.loads(data)
    except ValueError as exc:
        raise ValidationFailure("Snapshot must be JSON") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("html"), str):
        raise ValidationFailure("Snapshot must be an object with an `html` string")
    # Force the extension so the public media route serves it as JSON.
    key = await save_public(workspace_id, "snapshot.json", data)
    return SnapshotOut(key=key, bytes=len(data))
