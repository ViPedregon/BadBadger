"""Environment-driven runtime configuration."""

from __future__ import annotations

import os

from badbadger.agents.npc import DeterministicNPCBackend, FallbackNPCBackend, NPCBackend


DEFAULT_OPENAI_MODEL = "gpt-5.6"


def build_configured_npc_backend() -> tuple[NPCBackend, str]:
    """Enable OpenAI only when the user has explicitly supplied an API key."""
    if not os.environ.get("OPENAI_API_KEY"):
        return DeterministicNPCBackend(), "deterministic"

    from badbadger.agents.openai_client import OpenAIResponsesClient

    model = os.environ.get("BADBADGER_OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    primary = OpenAIResponsesClient(model)
    return (
        FallbackNPCBackend(primary, DeterministicNPCBackend()),
        f"OpenAI Responses API ({model}, deterministic fallback)",
    )
