"""Provider registry — implemented by Wave 1 agent C (see docs/CONTRACTS.md).

Maps provider kinds (openai|anthropic|google|openai_compatible|ollama|mock) to
ChatProvider adapters; resolves workspace-configured chat/embedding providers.
"""
