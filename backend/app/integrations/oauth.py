"""OAuth core (BE-A fills; see docs/INTEGRATIONS-CONTRACTS.md).

Signed-state authorize URLs, the public callback's code exchange + whoami, and
IntegrationAuthError. Provider base URLs honor settings.oauth_base_override so
the round trip is provable against a stub provider.
"""

from __future__ import annotations
