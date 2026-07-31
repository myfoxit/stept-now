"""Model registry. Importing this package registers every mapper with Base.metadata.

ORCHESTRATOR-OWNED: wave agents fill the individual modules but never edit this file.
"""

from app.models import (  # noqa: F401
    agent,
    agent_run,
    ai_provider,
    api_key,
    article,
    audit,
    automation,
    campaign,
    canned_response,
    contact,
    conversation,
    csat,
    inbox,
    knowledge,
    macro,
    message,
    notification,
    search_analytics,
    segment,
    sla,
    tag,
    team,
    tour,
    user,
    webhook,
    workspace,
)
from app.models.api_key import ApiKey  # noqa: F401
from app.models.audit import AuditLog  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.user import RefreshToken, User  # noqa: F401
from app.models.workspace import CustomRole, Invitation, Membership, Workspace  # noqa: F401
