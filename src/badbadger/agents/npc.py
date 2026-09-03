"""Structured NPC response contract and deterministic prototype backend."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
from typing import Any, Protocol

from badbadger.agents.context import NPCContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BeliefProposal:
    subject_id: str
    predicate: str
    value: Any
    confidence: float


@dataclass(frozen=True)
class ActionProposal:
    kind: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NPCResponse:
    dialogue: str
    proposed_actions: list[ActionProposal] = field(default_factory=list)
    belief_updates: list[BeliefProposal] = field(default_factory=list)
    degraded: bool = False


class NPCBackend(Protocol):
    def respond(self, context: NPCContext, player_input: str) -> NPCResponse: ...


class DeterministicNPCBackend:
    """Small offline backend that exercises the future LLM response contract."""

    def respond(self, context: NPCContext, player_input: str) -> NPCResponse:
        lowered = player_input.lower()
        if "room b" in lowered and "safe" in lowered:
            belief = next(
                (
                    item
                    for item in context.beliefs
                    if item["subject_id"] == "room_b" and item["predicate"] == "is_safe"
                ),
                None,
            )
            if belief is None:
                return NPCResponse("I don't know whether Room B is safe.")
            qualifier = "I believe" if belief["confidence"] < 0.8 else "I'm confident"
            answer = "safe" if belief["value"] else "unsafe"
            return NPCResponse(f"{qualifier} Room B is {answer}.")

        if "lights" in lowered and any(word in lowered for word in ("out", "dark", "off")):
            return NPCResponse(
                "Understood. I'll remember that the lights in Room B are out.",
                belief_updates=[
                    BeliefProposal("room_b", "lights_on", False, confidence=0.7)
                ],
            )

        if any(word in lowered for word in ("hello", "hi", "talk", "speak")):
            return NPCResponse("Hello. What would you like to discuss?")
        return NPCResponse("I understand what you said, but I have nothing to add.")


class FallbackNPCBackend:
    """Use a safe local backend whenever the primary backend fails."""

    def __init__(self, primary: NPCBackend, fallback: NPCBackend) -> None:
        self.primary = primary
        self.fallback = fallback

    def respond(self, context: NPCContext, player_input: str) -> NPCResponse:
        try:
            return self.primary.respond(context, player_input)
        except Exception:
            logger.exception(
                "Primary NPC backend failed for npc_id=%s; using deterministic fallback",
                context.npc_id,
            )
            return replace(
                self.fallback.respond(context, player_input),
                degraded=True,
            )
