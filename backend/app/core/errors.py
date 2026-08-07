"""Application error hierarchy and FastAPI handlers.

Services raise these instead of HTTPException so business logic stays
transport-agnostic. All errors serialize to one envelope:
    {"error": {"code": "...", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import log

logger = log("errors")


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.code.replace("_", " ")
        self.details = details
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class BlockedContactError(ForbiddenError):
    """Inbound from a blocked contact. Its own type so channel webhooks can
    acknowledge the provider (200) instead of signalling a delivery failure that
    would make the provider retry forever."""

    code = "contact_blocked"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class ValidationFailure(AppError):
    status_code = 422
    code = "validation_failed"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


def _envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def _jsonable(value: Any) -> Any:
    """Best-effort JSON coercion for values embedded in validation details."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    return str(value)


def _clean_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Make Pydantic's error list JSON-safe.

    Errors raised from a `@model_validator` carry the original exception object
    under `ctx.error`, which `json.dumps` cannot encode — serialising the raw
    list turns a 422 into a 500. Coerce anything non-primitive to its string
    form, and drop `input` since it echoes the caller's payload (which can hold
    credentials) straight back into the response body.
    """
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):
            cleaned.append({"msg": str(error)})
            continue
        item = {k: _jsonable(v) for k, v in error.items() if k != "input"}
        cleaned.append(item)
    return cleaned


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "validation_failed", "Request validation failed", _clean_errors(exc.errors())
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "Something went wrong"),
        )
