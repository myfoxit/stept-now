"""Public help-center portal API — no authentication, published content only.

Mounted at /portal (see app.main). Workspaces are resolved by slug; drafts and
unknown slugs 404 so nothing unpublished ever leaks.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import Db
from app.schemas.articles import PortalArticleOut, PortalHomeOut
from app.services import articles as articles_service

router = APIRouter()


@router.get("/{workspace_slug}", response_model=PortalHomeOut)
async def portal_home(workspace_slug: str, session: Db) -> PortalHomeOut:
    return await articles_service.get_portal_home(session, workspace_slug)


@router.get("/{workspace_slug}/articles/{article_slug}", response_model=PortalArticleOut)
async def portal_article(workspace_slug: str, article_slug: str, session: Db) -> PortalArticleOut:
    return await articles_service.get_portal_article(session, workspace_slug, article_slug)
