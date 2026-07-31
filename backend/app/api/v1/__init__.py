"""API v1 router registry.

ORCHESTRATOR-OWNED: every domain router is pre-registered here against its stub
module. Wave agents implement routes inside their own module only.

Workspace-scoped routers hang under /w/{workspace_id}; their handlers use
`Member`/`require_perm` dependencies from app.core.deps.
"""

from fastapi import APIRouter

from app.api.v1 import (
    agent_runs,
    agents,
    ai_providers,
    api_keys,
    approvals,
    articles,
    audit,
    auth,
    automations,
    campaigns,
    canned_responses,
    contacts,
    conversations,
    files,
    health,
    inboxes,
    knowledge,
    macros,
    me,
    members,
    reports,
    search,
    search_analytics,
    segments,
    slas,
    tags,
    teams,
    tours,
    webhooks,
    workspaces,
)

api_router = APIRouter()

# global (no workspace scope)
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, tags=["me"])
api_router.include_router(workspaces.router, tags=["workspaces"])

# workspace-scoped
WS = "/w/{workspace_id}"
api_router.include_router(members.router, prefix=WS, tags=["members"])
api_router.include_router(api_keys.router, prefix=WS, tags=["api-keys"])
api_router.include_router(audit.router, prefix=WS, tags=["audit"])
api_router.include_router(files.router, prefix=WS, tags=["files"])
api_router.include_router(contacts.router, prefix=WS, tags=["contacts"])
api_router.include_router(segments.router, prefix=WS, tags=["segments"])
api_router.include_router(tags.router, prefix=WS, tags=["tags"])
api_router.include_router(teams.router, prefix=WS, tags=["teams"])
api_router.include_router(canned_responses.router, prefix=WS, tags=["canned-responses"])
api_router.include_router(inboxes.router, prefix=WS, tags=["inboxes"])
api_router.include_router(conversations.router, prefix=WS, tags=["conversations"])
api_router.include_router(search.router, prefix=WS, tags=["search"])
api_router.include_router(knowledge.router, prefix=WS, tags=["knowledge"])
api_router.include_router(articles.router, prefix=WS, tags=["articles"])
api_router.include_router(ai_providers.router, prefix=WS, tags=["ai-providers"])
api_router.include_router(agents.router, prefix=WS, tags=["agents"])
api_router.include_router(agent_runs.router, prefix=WS, tags=["agent-runs"])
api_router.include_router(approvals.router, prefix=WS, tags=["approvals"])
api_router.include_router(automations.router, prefix=WS, tags=["automations"])
api_router.include_router(macros.router, prefix=WS, tags=["macros"])
api_router.include_router(campaigns.router, prefix=WS, tags=["campaigns"])
api_router.include_router(slas.router, prefix=WS, tags=["slas"])
api_router.include_router(search_analytics.router, prefix=WS, tags=["search-analytics"])
api_router.include_router(webhooks.router, prefix=WS, tags=["webhooks"])
api_router.include_router(reports.router, prefix=WS, tags=["reports"])
api_router.include_router(tours.router, prefix=WS, tags=["tours"])
