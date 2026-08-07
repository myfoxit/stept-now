"""Integrations framework (W11): provider catalog, OAuth core, token seam.

See docs/INTEGRATIONS-CONTRACTS.md. Modules:

- ``catalog``: declarative ProviderSpec registry (the apps.yml pattern)
- ``oauth``: authorize-URL builder, signed state, code exchange, whoami
- ``tokens``: connection lookup + auto-refreshing access-token seam consumed by
  the email channel and knowledge connectors
- ``slack_install``: Slack-specific post-connect inbox provisioning
"""
