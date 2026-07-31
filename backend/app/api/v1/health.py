"""Liveness/readiness."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

import app
from app.core.deps import Db

router = APIRouter()


class HealthOut(BaseModel):
    status: str
    version: str
    database: str


@router.get("/healthz", response_model=HealthOut)
async def healthz(session: Db) -> HealthOut:
    await session.execute(text("SELECT 1"))
    return HealthOut(status="ok", version=app.__version__, database="ok")
