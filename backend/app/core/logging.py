"""Logging setup: concise key=value lines in dev, JSON lines in prod."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    if root.handlers:  # already configured (tests / reload)
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.env == "prod":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO if settings.env != "test" else logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log(name: str) -> logging.Logger:
    return logging.getLogger(f"stept.{name}")
