"""OpenAI Responses API adapter for structured player-intent interpretation."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
from typing import Any, Literal

from badbadger.agents.intent import PlayerIntent, PlayerIntentContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Classify the player's exact input into one game intent using
only the supplied player-visible context. Select target_id only from the IDs in
that context. Use unknown when the request is ambiguous, impossible to map, or
contains multiple actions. Do not invent player dialogue, decisions, thoughts,
or voluntary actions. This output proposes an intent; the engine will validate
it before any state changes."""


def _build_intent_model() -> type[Any]:
    try:
        from pydantic import ConfigDict, Field, create_model
    except ImportError as error:
        raise RuntimeError(
            "OpenAI mode requires the optional dependencies. "
            "Install with: python -m pip install -e '.[openai]'"
        ) from error
    return create_model(
        "PlayerIntentModel",
        __config__=ConfigDict(extra="forbid"),
        kind=(Literal["move", "examine", "wait", "speak", "unknown"], ...),
        target_id=(str | None, ...),
        minutes=(int | None, Field(ge=1, le=1_440)),
    )


class OpenAIIntentInterpreter:
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
        self.response_model = response_model or _build_intent_model()

    def interpret(
        self, context: PlayerIntentContext, player_input: str
    ) -> PlayerIntent:
        payload = {
            "player_visible_context": asdict(context),
            "player_input": player_input,
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        parsed = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.sdk_client.responses.parse(
                    model=self.model,
                    input=messages,
                    text_format=self.response_model,
                    store=False,
                )
                parsed = response.output_parsed
                if parsed is None:
                    raise ValueError("OpenAI response contained no parsed player intent")
                break
            except Exception as error:
                last_error = error
                if attempt == 0 and isinstance(error, ValueError):
                    logger.warning(
                        "Intent parsing failed local validation; retrying once (%s)",
                        type(error).__name__,
                    )
                    messages.append(
                        {
                            "role": "system",
                            "content": "Return exactly one intent matching the schema.",
                        }
                    )
                else:
                    break
        if parsed is None:
            assert last_error is not None
            raise last_error
        return PlayerIntent(
            kind=parsed.kind,
            target_id=parsed.target_id,
            minutes=parsed.minutes,
        )
