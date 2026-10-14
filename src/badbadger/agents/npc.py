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
class RelationshipProposal:
    target_id: str
    trust_delta: float
    affinity_delta: float
    reason: str


@dataclass(frozen=True)
class DisclosureProposal:
    disclosure_id: str


@dataclass(frozen=True)
class ActionProposal:
    kind: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NPCResponse:
    dialogue: str
    proposed_actions: list[ActionProposal] = field(default_factory=list)
    belief_updates: list[BeliefProposal] = field(default_factory=list)
    relationship_updates: list[RelationshipProposal] = field(default_factory=list)
    disclosure_requests: list[DisclosureProposal] = field(default_factory=list)
    degraded: bool = False


class NPCBackend(Protocol):
    def respond(self, context: NPCContext, player_input: str) -> NPCResponse: ...


class DeterministicNPCBackend:
    """Small offline backend that exercises the future LLM response contract."""

    def respond(self, context: NPCContext, player_input: str) -> NPCResponse:
        lowered = player_input.lower()
        if "engine decision tick" in lowered:
            goals = " ".join(goal["description"] for goal in context.goals).lower()
            candidates = [
                action
                for action in context.available_actions
                if (
                    action["kind"] == "move"
                    and (
                        action["target_id"].lower() in goals
                        or action["target_name"].lower() in goals
                    )
                )
                or (
                    action["kind"] == "interact"
                    and action.get("autonomous_eligible", True)
                    and action["label"].lower() in goals
                )
            ]
            selected = max(
                candidates,
                key=lambda action: float(action.get("decision_score", 0.0)),
                default=None,
            )
            if selected is not None:
                if selected["kind"] == "interact":
                    return NPCResponse(
                        f"I choose to {selected['label'].lower()}.",
                        proposed_actions=[
                            ActionProposal("interact", {"action_id": selected["id"]})
                        ],
                    )
                return NPCResponse(
                    f"I will pursue my goal in {selected['target_name']}.",
                    proposed_actions=[
                        ActionProposal("move", {"target_id": selected["target_id"]})
                    ],
                )
            social = next(
                (
                    action for action in context.available_actions
                    if action["kind"] == "share_belief"
                ),
                None,
            )
            if social is not None:
                return NPCResponse(
                    f"I will share what I know with {social['target_name']}.",
                    proposed_actions=[ActionProposal("share_belief", {
                        "target_id": social["target_id"],
                        "subject_id": social["subject_id"],
                        "predicate": social["predicate"],
                    })],
                )
            return NPCResponse("I have no useful action to take right now.")
        interaction = next(
            (
                action for action in context.available_actions
                if action["kind"] == "interact"
                and all(word in lowered for word in action["label"].lower().split())
            ),
            None,
        )
        if interaction is not None:
            return NPCResponse(
                f"I'll {interaction['label'].lower()}.",
                proposed_actions=[
                    ActionProposal("interact", {"action_id": interaction["id"]})
                ],
            )
        if "room b" in lowered and any(word in lowered for word in ("go", "move", "check")):
            return NPCResponse("I'll go check Room B.", proposed_actions=[ActionProposal("move", {"target_id":"room_b"})])
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

        disclosure = next(
            (
                item for item in context.available_disclosures
                if any(term.lower() in lowered for term in item["request_terms"])
            ),
            None,
        )
        if disclosure is not None:
            return NPCResponse(
                "Let me consider what I can tell you.",
                disclosure_requests=[DisclosureProposal(disclosure["id"])],
            )

        if "lights" in lowered and any(word in lowered for word in ("out", "dark", "off")):
            return NPCResponse(
                "Understood. I'll remember that the lights in Room B are out.",
                belief_updates=[
                    BeliefProposal("room_b", "lights_on", False, confidence=0.7)
                ],
            )

        if any(phrase in lowered for phrase in ("thank you", "thanks")):
            player = next(
                (
                    character for character in context.visible_characters
                    if character["kind"] == "player"
                ),
                None,
            )
            if player is not None:
                return NPCResponse(
                    "You're welcome.",
                    relationship_updates=[RelationshipProposal(
                        player["id"], 0.1, 0.05,
                        "The player expressed sincere appreciation.",
                    )],
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
        except Exception as error:
            logger.error(
                "Primary NPC backend failed for npc_id=%s; using deterministic fallback (%s)",
                context.npc_id,
                type(error).__name__,
            )
            return replace(
                self.fallback.respond(context, player_input),
                degraded=True,
            )
