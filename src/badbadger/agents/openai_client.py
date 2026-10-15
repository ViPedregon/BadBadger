"""Optional OpenAI Responses API adapter for structured NPC output."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
from typing import Any, Literal

from badbadger.agents.context import NPCContext
from badbadger.agents.npc import (
    ActionProposal,
    BeliefProposal,
    DisclosureProposal,
    NPCResponse,
    RelationshipProposal,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You portray one NPC in a deterministic role-playing simulation.
Use only the supplied NPC context and the player's exact input. Do not claim
knowledge absent from the context. Return dialogue plus optional proposed
actions, belief updates, relationship updates, and disclosure requests in the
required schema.
The canon field defines stable identity, voice, values, and boundaries. The
biography field contains bounded behavioral guidance selected by the engine.
Follow that guidance, but never state or explain a private or secret biography
entry unless the engine separately supplies its content through an authorized
disclosure. Biography guidance is context for characterization, not authority
to add facts or actions.
Relationship changes describe this NPC's own attitude and must be small,
justified reactions to the current exchange. Proposals do not change the
world; the game engine will validate them. Never invent player thoughts,
dialogue, decisions, or voluntary actions. Propose only actions listed in
available_actions, copying every applicable id field exactly. A share_belief
action may be selected only during an ENGINE DECISION TICK. During a decision
tick, do not select an action whose autonomous_eligible value is false. A speak
action may be selected only during an ENGINE DECISION TICK and must copy its
cue_id exactly; when selected, dialogue is the short utterance heard by the
player. Without a speak action, decision-tick dialogue is not audible. During a
conversation, available_disclosures contains safe topic metadata but never the
private content. When the player asks about one of those topics, copy its id
into a disclosure request; never invent or state the withheld content yourself.
During a player conversation, propose an interaction only when the player's exact input
explicitly asks the NPC to perform it; questions and requests for information
must return no action. An ENGINE DECISION TICK is autonomous and may select an
action without player permission."""


def _build_response_model() -> type[Any]:
    """Import the optional schema dependency only when OpenAI mode is enabled."""
    try:
        from pydantic import ConfigDict, Field, create_model
    except ImportError as error:
        raise RuntimeError(
            "OpenAI mode requires the optional dependencies. "
            "Install with: python -m pip install -e '.[openai]'"
        ) from error

    strict_config = ConfigDict(extra="forbid")
    belief_model = create_model(
        "BeliefProposalModel",
        __config__=strict_config,
        subject_id=(str, Field(min_length=1)),
        predicate=(str, Field(min_length=1)),
        value=(bool | int | float | str, ...),
        confidence=(float, Field(ge=0, le=1)),
    )
    action_model = create_model(
        "ActionProposalModel",
        __config__=strict_config,
        kind=(Literal["move", "wait", "interact", "share_belief", "speak"], ...),
        target_id=(str | None, ...),
        minutes=(int | None, Field(ge=1, le=1_440)),
        action_id=(str | None, ...),
        subject_id=(str | None, ...),
        predicate=(str | None, ...),
        cue_id=(int | None, Field(ge=1)),
    )
    relationship_model = create_model(
        "RelationshipProposalModel",
        __config__=strict_config,
        target_id=(str, Field(min_length=1)),
        trust_delta=(float, Field(ge=-0.25, le=0.25)),
        affinity_delta=(float, Field(ge=-0.25, le=0.25)),
        reason=(str, Field(min_length=1, max_length=500)),
    )
    disclosure_model = create_model(
        "DisclosureProposalModel",
        __config__=strict_config,
        disclosure_id=(str, Field(min_length=1)),
    )
    return create_model(
        "NPCResponseModel",
        __config__=strict_config,
        dialogue=(str, Field(min_length=1, max_length=2_000)),
        proposed_actions=(list[action_model], ...),
        belief_updates=(list[belief_model], ...),
        relationship_updates=(list[relationship_model], ...),
        disclosure_requests=(list[disclosure_model], ...),
    )


class OpenAIResponsesClient:
    """Generate an :class:`NPCResponse` using ``responses.parse``."""

    def __init__(
        self,
        model: str,
        *,
        sdk_client: Any | None = None,
        response_model: type[Any] | None = None,
    ) -> None:
        if sdk_client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise RuntimeError(
                    "OpenAI mode requires the optional dependencies. "
                    "Install with: python -m pip install -e '.[openai]'"
                ) from error
            sdk_client = OpenAI()
        self.sdk_client = sdk_client
        self.model = model
        self.response_model = response_model or _build_response_model()

    def respond(self, context: NPCContext, player_input: str) -> NPCResponse:
        payload = {
            "npc_context": asdict(context),
            "player_input": player_input,
        }
        logger.debug(
            "Requesting structured NPC response model=%s npc_id=%s",
            self.model,
            context.npc_id,
        )
        input_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        parsed = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.sdk_client.responses.parse(
                    model=self.model,
                    input=input_messages,
                    text_format=self.response_model,
                    store=False,
                )
                parsed = response.output_parsed
                if parsed is None:
                    raise ValueError("OpenAI response contained no parsed NPC output")
                break
            except Exception as error:
                last_error = error
                if attempt == 0 and isinstance(error, ValueError):
                    logger.warning(
                        "Structured NPC response failed local validation; retrying once (%s)",
                        type(error).__name__,
                    )
                    input_messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The previous response could not be parsed. Return exactly "
                                "one response conforming to the required schema."
                            ),
                        }
                    )
                else:
                    break
        if parsed is None:
            assert last_error is not None
            raise last_error
        logger.debug("Received structured NPC response: %r", parsed)
        return NPCResponse(
            dialogue=parsed.dialogue,
            proposed_actions=[
                ActionProposal(
                    item.kind,
                    {
                        key: value
                        for key, value in {
                            "target_id": item.target_id,
                            "minutes": item.minutes,
                            "action_id": getattr(item, "action_id", None),
                            "subject_id": getattr(item, "subject_id", None),
                            "predicate": getattr(item, "predicate", None),
                            "cue_id": getattr(item, "cue_id", None),
                        }.items()
                        if value is not None
                    },
                )
                for item in parsed.proposed_actions
            ],
            belief_updates=[
                BeliefProposal(
                    item.subject_id,
                    item.predicate,
                    item.value,
                    item.confidence,
                )
                for item in parsed.belief_updates
            ],
            relationship_updates=[
                RelationshipProposal(
                    item.target_id,
                    item.trust_delta,
                    item.affinity_delta,
                    item.reason,
                )
                for item in getattr(parsed, "relationship_updates", [])
            ],
            disclosure_requests=[
                DisclosureProposal(item.disclosure_id)
                for item in getattr(parsed, "disclosure_requests", [])
            ],
        )
