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
    source_quote: str | None = None


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
        if context.interaction_mode == "decision":
            speech = next(
                (
                    action for action in context.available_actions
                    if action["kind"] == "speak"
                ),
                None,
            )
            if speech is not None:
                utterance = f"Here's where things stand: {speech['summary']}"
                if speech.get("trigger_kind") == "goal_resolved":
                    goal = next((goal for goal in context.goals if goal.get("history")
                                 and goal["history"][-1].get("reason") == speech["summary"]), None)
                    if goal is not None:
                        utterance = {
                            "completed": "That is the outcome I hoped for.",
                            "failed": "That is not the outcome I wanted.",
                            "abandoned": "My original plan is no longer possible.",
                        }.get(goal["status"], utterance)
                return NPCResponse(
                    utterance,
                    proposed_actions=[ActionProposal(
                        "speak", {"cue_id": speech["cue_id"]}
                    )],
                )
            goals = " ".join(
                goal["description"]
                for goal in context.goals if goal.get("status", "active") == "active"
            ).lower()
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
        if any(
            phrase in lowered
            for phrase in ("what now", "how do you feel", "what happened", "outcome", "why")
        ):
            resolved_goal = next(
                (
                    goal for goal in context.goals
                    if goal.get("status", "active") != "active"
                ),
                None,
            )
            if resolved_goal is not None:
                history = resolved_goal.get("history", [])
                reason = (
                    history[-1].get("reason", resolved_goal["description"])
                    if history else resolved_goal["description"]
                )
                reactions = {
                    "completed": "We accomplished what mattered to me.",
                    "failed": "This is not the outcome I fought for.",
                    "abandoned": "That goal no longer has a path forward.",
                }
                player_id = next((item["id"] for item in context.visible_characters if item["kind"] == "player"), None)
                player_relationship = next(
                    (
                        item for item in context.relationships
                        if item["target_id"] == player_id
                    ),
                    None,
                )
                attitude = ""
                if player_relationship is not None:
                    action_id = history[-1].get("source_action_id") if history else None
                    attributed = action_id is not None and any(
                        change.get("source_type") == "action" and change.get("source_action_id") == action_id
                        for change in player_relationship.get("recent_changes", [])
                    )
                    if float(player_relationship["trust"]) >= 0.35:
                        attitude = " I trust your part in what happened." if attributed else " I trust your judgment."
                    elif attributed and float(player_relationship["trust"]) <= -0.1:
                        attitude = " Your part in this has damaged my trust."
                return NPCResponse(
                    f"{reactions[resolved_goal['status']]} {reason}{attitude}"
                )
        from badbadger.engine.consent import requests_action
        interaction = next(
            (
                action for action in context.available_actions
                if action["kind"] == "interact"
                and requests_action(player_input, context.npc_id, context.name, action["label"], action.get("request_phrases", []))
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
                    BeliefProposal("room_b", "lights_on", False, confidence=0.7, source_quote=player_input)
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

        if any(word in lowered for word in ("know", "think", "risk", "priority", "plan", "why", "missing", "belong",
                                           "notice", "recommend", "suggest", "options", "crossing", "track", "convoy", "what should", "what can")):
            active = [goal for goal in context.goals if goal.get("status", "active") == "active"]
            lines = ["My priority is to " + active[0]["description"][0].lower()
                     + active[0]["description"][1:]] if active else []
            descriptions = {
                ("sample", "is_unstable"): ("the sample is unstable", "the sample is stable"),
                ("rescue", "is_listening"): ("rescue can hear us", "rescue is not listening"),
                ("purge", "will_destroy_research"): ("purging will destroy the research", "purging will preserve the research"),
            }
            for belief in context.beliefs:
                wording = descriptions.get((belief["subject_id"], belief["predicate"]))
                if wording is not None and isinstance(belief["value"], bool):
                    qualifier = "I believe" if belief["confidence"] >= 0.8 else "I'm not certain, but I think"
                    supporting = [item for item in belief.get("evidence", []) if item["value"] == belief["value"]]
                    source = max(supporting, key=lambda item: (item["confidence"], item["id"]), default={})
                    if source.get("source_type") == "hearsay":
                        lines.append(f"I've been told that {wording[0 if belief['value'] else 1]}, but I haven't confirmed it.")
                    else:
                        lines.append(f"{qualifier} {wording[0 if belief['value'] else 1]}.")
            if lines:
                if context.parameters.get("situation_advice"):
                    lines.append(str(context.parameters["situation_advice"]))
                return NPCResponse(" ".join(lines))

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
