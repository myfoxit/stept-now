"""Widget public API registry (ORCHESTRATOR-OWNED).

Mounted at /api/widget with permissive CORS — this is what the embedded widget
talks to. Auth: workspace widget key + signed contact tokens (see widget/deps.py).
"""

from fastapi import APIRouter

from app.api.widget import (
    articles,
    boot,
    campaigns,
    checklists,
    conversations,
    copilot,
    csat,
    dap,
    feedback,
    media,
    surveys,
    tours,
)

widget_router = APIRouter()
widget_router.include_router(boot.router, tags=["widget"])
widget_router.include_router(conversations.router, tags=["widget"])
widget_router.include_router(copilot.router, tags=["widget-copilot"])
widget_router.include_router(articles.router, tags=["widget"])
widget_router.include_router(csat.router, tags=["widget"])
widget_router.include_router(tours.router, tags=["widget"])
widget_router.include_router(campaigns.router, tags=["widget"])
widget_router.include_router(feedback.router, tags=["widget"])
widget_router.include_router(dap.router, tags=["widget-dap"])
widget_router.include_router(checklists.router, tags=["widget"])
widget_router.include_router(surveys.router, tags=["widget"])
widget_router.include_router(media.router, tags=["widget"])
