"""CSV contact import: upload → preview → map → run.

The uploaded file is stored once; the run is a queued task so a large file
doesn't hold an HTTP request open. Rows are matched to existing contacts by
`external_id` first, then `email`, then `phone` — the same precedence
`contacts.find_or_create` uses — so re-running an export updates rather than
duplicates.

Per-row errors are collected (capped) instead of aborting: a single malformed
line in a 40k-row export must not lose the other 39,999.

See docs/CHATWOOT-BACKLOG.md §1.7.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope, utcnow
from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor
from app.core.logging import log
from app.core.queue import TaskContext, task
from app.core.storage import get_storage
from app.models.contact import Contact
from app.models.contact_import import ContactImport, ImportStatus
from app.services import audit, custom_attributes

logger = log("contact_import")

CORE_FIELDS = ("name", "email", "phone", "external_id")
ATTR_PREFIX = "attributes."
MAX_ERRORS = 200
PREVIEW_ROWS = 5
# 20 MB of CSV is ~200k contacts; beyond that this wants a streaming importer.
MAX_BYTES = 20 * 1024 * 1024


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationFailure("Could not decode the file as text (try UTF-8)")


def parse_csv(raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = _decode(raw)
    try:
        dialect: Any = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        raise ValidationFailure("The file has no header row")
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    return headers, rows


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    """Best-guess column mapping so the common export lands correctly with no
    clicks. Anything unrecognised maps to "" (skip) for the user to decide."""
    aliases = {
        "name": "name",
        "full name": "name",
        "full_name": "name",
        "contact name": "name",
        "email": "email",
        "email address": "email",
        "e-mail": "email",
        "phone": "phone",
        "phone number": "phone",
        "phone_number": "phone",
        "mobile": "phone",
        "id": "external_id",
        "user id": "external_id",
        "user_id": "external_id",
        "external id": "external_id",
        "external_id": "external_id",
        "identifier": "external_id",
    }
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for header in headers:
        target = aliases.get(header.strip().lower(), "")
        if target and target not in used:
            mapping[header] = target
            used.add(target)
        else:
            mapping[header] = ""
    return mapping


def validate_mapping(headers: list[str], mapping: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    seen_core: set[str] = set()
    for header, target in (mapping or {}).items():
        if header not in headers:
            raise ValidationFailure(f"Unknown column {header!r}")
        target = (target or "").strip()
        if not target:
            continue
        if target in CORE_FIELDS:
            if target in seen_core:
                raise ValidationFailure(f"Two columns are mapped to {target!r}")
            seen_core.add(target)
        elif not (target.startswith(ATTR_PREFIX) and len(target) > len(ATTR_PREFIX)):
            raise ValidationFailure(
                f"Invalid mapping target {target!r} — use one of "
                f"{', '.join(CORE_FIELDS)} or attributes.<key>"
            )
        cleaned[header] = target
    if not seen_core & {"email", "phone", "external_id"}:
        raise ValidationFailure("Map at least one identifying column (email, phone or external_id)")
    return cleaned


async def get_import(session: AsyncSession, workspace_id: str, import_id: str) -> ContactImport:
    row = await session.get(ContactImport, import_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFoundError("Import not found")
    return row


async def list_imports(session: AsyncSession, workspace_id: str) -> list[ContactImport]:
    result = await session.execute(
        select(ContactImport)
        .where(ContactImport.workspace_id == workspace_id)
        .order_by(ContactImport.created_at.desc())
        .limit(50)
    )
    return list(result.scalars())


async def create_import(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    user_id: str | None,
    filename: str,
    raw: bytes,
) -> tuple[ContactImport, list[str], list[dict[str, str]]]:
    """Store the file and return it with the detected headers + a sample."""
    if len(raw) > MAX_BYTES:
        raise ValidationFailure(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
    headers, rows = parse_csv(raw)
    if not rows:
        raise ValidationFailure("The file has no data rows")

    stored = await get_storage().save(f"contact-import-{filename}", raw)
    record = ContactImport(
        workspace_id=workspace_id,
        filename=filename[:400],
        file_key=stored.key,
        status=ImportStatus.PENDING,
        mapping=suggest_mapping(headers),
        total_rows=len(rows),
        created_by=user_id,
    )
    session.add(record)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="contact_import.create",
        target_type="contact_import",
        target_id=record.id,
        meta={"filename": record.filename, "rows": len(rows)},
    )
    return record, headers, rows[:PREVIEW_ROWS]


async def start_import(
    session: AsyncSession,
    workspace_id: str,
    import_id: str,
    *,
    actor: Actor,
    mapping: dict[str, str] | None = None,
) -> ContactImport:
    from app.core.queue import enqueue

    record = await get_import(session, workspace_id, import_id)
    if record.status == ImportStatus.PROCESSING:
        raise ValidationFailure("This import is already running")
    raw = await get_storage().read(record.file_key)
    headers, _ = parse_csv(raw)
    record.mapping = validate_mapping(headers, mapping or record.mapping)
    record.status = ImportStatus.PROCESSING
    record.started_at = utcnow()
    record.processed_rows = 0
    record.created_count = 0
    record.updated_count = 0
    record.failed_count = 0
    record.errors = []
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="contact_import.start",
        target_type="contact_import",
        target_id=record.id,
        meta={"rows": record.total_rows},
    )
    await enqueue("run_contact_import", import_id=record.id)
    return record


def _row_to_fields(row: dict[str, str], mapping: dict[str, str]) -> tuple[dict, dict]:
    core: dict[str, Any] = {}
    attributes: dict[str, Any] = {}
    for header, target in mapping.items():
        value = (row.get(header) or "").strip()
        if not value:
            continue
        if target in CORE_FIELDS:
            core[target] = value
        elif target.startswith(ATTR_PREFIX):
            attributes[target[len(ATTR_PREFIX) :]] = value
    return core, attributes


async def _match_existing(
    session: AsyncSession, workspace_id: str, core: dict[str, Any]
) -> Contact | None:
    """external_id → email → phone, mirroring contacts.find_or_create."""
    for field in ("external_id", "email", "phone"):
        value = core.get(field)
        if not value:
            continue
        column = getattr(Contact, field)
        found = (
            await session.execute(
                select(Contact)
                .where(
                    Contact.workspace_id == workspace_id,
                    column == value,
                    Contact.merged_into_id.is_(None),
                )
                .order_by(Contact.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
    return None


async def process(import_id: str) -> ContactImport | None:
    """Run one import to completion. Its own unit of work (task entry point)."""
    async with session_scope() as session:
        record = await session.get(ContactImport, import_id)
        if record is None:
            logger.warning("contact import %s vanished before processing", import_id)
            return None
        workspace_id = record.workspace_id
        try:
            raw = await get_storage().read(record.file_key)
            _, rows = parse_csv(raw)
        except Exception as exc:  # unreadable file — fail the run, keep the row
            record.status = ImportStatus.FAILED
            record.completed_at = utcnow()
            record.errors = [{"row": 0, "error": f"Could not read the file: {exc}"}]
            await session.flush()
            return record

        mapping = dict(record.mapping or {})
        errors: list[dict[str, Any]] = []
        created = updated = failed = 0

        for index, row in enumerate(rows, start=2):  # row 1 is the header
            try:
                core, attributes = _row_to_fields(row, mapping)
                if not any(core.get(f) for f in ("email", "phone", "external_id")):
                    raise ValidationFailure("No email, phone or external_id in this row")
                if attributes:
                    attributes = await custom_attributes.validate_attributes(
                        session, workspace_id, "contact", attributes
                    )
                existing = await _match_existing(session, workspace_id, core)
                if existing is None:
                    session.add(
                        Contact(
                            workspace_id=workspace_id,
                            name=core.get("name", ""),
                            email=core.get("email"),
                            phone=core.get("phone"),
                            external_id=core.get("external_id"),
                            attributes=attributes,
                        )
                    )
                    created += 1
                else:
                    for field in CORE_FIELDS:
                        if core.get(field):
                            setattr(existing, field, core[field])
                    if attributes:
                        merged = dict(existing.attributes or {})
                        merged.update(attributes)
                        existing.attributes = merged
                    updated += 1
                await session.flush()
            except Exception as exc:
                await session.rollback()
                failed += 1
                if len(errors) < MAX_ERRORS:
                    errors.append({"row": index, "error": str(exc)})

        # Re-fetch: the rollbacks above may have expired the identity map entry.
        record = await session.get(ContactImport, import_id)
        if record is None:  # pragma: no cover — deleted mid-run
            return None
        record.processed_rows = len(rows)
        record.created_count = created
        record.updated_count = updated
        record.failed_count = failed
        record.errors = errors
        record.status = ImportStatus.COMPLETED
        record.completed_at = utcnow()
        await session.flush()
        return record


@task("run_contact_import")
async def _run_contact_import(ctx: TaskContext, *, import_id: str) -> None:
    await process(import_id)


async def export_rows(session: AsyncSession, workspace_id: str) -> str:
    """Whole-directory CSV export — the other half of "can I move my data"."""
    contacts = list(
        (
            await session.execute(
                select(Contact)
                .where(Contact.workspace_id == workspace_id, Contact.merged_into_id.is_(None))
                .order_by(Contact.created_at)
            )
        ).scalars()
    )
    attribute_keys: list[str] = []
    for contact in contacts:
        for key in contact.attributes or {}:
            if key not in attribute_keys:
                attribute_keys.append(key)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "name", "email", "phone", "external_id", "verified", "blocked", "created_at"]
        + [f"{ATTR_PREFIX}{k}" for k in attribute_keys]
    )
    for contact in contacts:
        attributes = contact.attributes or {}
        writer.writerow(
            [
                contact.id,
                contact.name,
                contact.email or "",
                contact.phone or "",
                contact.external_id or "",
                "true" if contact.verified else "false",
                "true" if contact.blocked else "false",
                contact.created_at.isoformat(),
            ]
            + [_csv_value(attributes.get(k)) for k in attribute_keys]
        )
    return buffer.getvalue()


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
