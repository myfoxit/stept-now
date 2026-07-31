"""Widget public API registry (ORCHESTRATOR-OWNED).

Mounted at /api/widget with permissive CORS — this is what the embedded widget
talks to. Auth: workspace widget key + signed contact tokens (see widget/deps.py).
"""

from fastapi import APIRouter

from app.api.widget import articles, boot, campaigns, conversations, csat, feedback, tours

widget_router = APIRouter()
widget_router.include_router(boot.router, tags=["widget"])
widget_router.include_router(conversations.router, tags=["widget"])
widget_router.include_router(articles.router, tags=["widget"])
widget_router.include_router(csat.router, tags=["widget"])
widget_router.include_router(tours.router, tags=["widget"])
widget_router.include_router(campaigns.router, tags=["widget"])
widget_router.include_router(feedback.router, tags=["widget"])
