"""Slack post-connect provisioning (BE-A fills; see docs/INTEGRATIONS-CONTRACTS.md).

After oauth.v2.access: store the connection, then find-or-create the slack
channel inbox for team_id with secrets {bot_token, signing_secret}.
"""

from __future__ import annotations
