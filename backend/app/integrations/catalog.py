"""Provider catalog (BE-A fills; see docs/INTEGRATIONS-CONTRACTS.md).

Declarative registry: PROVIDERS: dict[str, ProviderSpec] with google, microsoft,
slack, notion, confluence, zendesk. Data only — the OAuth core must not need
editing to add a provider.
"""

from __future__ import annotations
