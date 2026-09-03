"""Optional OpenAI Responses API adapter for structured NPC output."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
from typing import Any, Literal

from badbadger.agents.context import NPCContext
from badbadger.agents.npc import ActionProposal, BeliefProposal, NPCResponse

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You portray one NPC in a deterministic role-playing simulation.
Use only the supplied NPC context and the player's exact input. Do not claim
knowledge absent from the context. Return dialogue plus optional proposed
actions and belief updates in the required schema. Proposals do not change the
world; the game engine will validate them. Never invent player thoughts,
dialogue, decisions, or voluntary actions."""


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
        kind=(Literal["move", "wait"], ...),
        target_id=(str | None, ...),
        minutes=(int | None, Field(ge=1, le=1_440)),
    )
    return create_model(
        "NPCResponseModel",
        __config__=strict_config,
        dialogue=(str, Field(min_length=1, max_length=2_000)),
        proposed_actions=(list[action_model], ...),
        belief_updates=(list[belief_model], ...),
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
        )
