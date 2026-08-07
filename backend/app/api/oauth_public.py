"""Public OAuth callback router (BE-A fills; see docs/INTEGRATIONS-CONTRACTS.md).

Mounted at /api/integrations — unauthenticated browser redirects; workspace
identity travels in the signed state, never in the URL path.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
