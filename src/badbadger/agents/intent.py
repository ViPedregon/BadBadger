"""Player-visible context and provider-neutral intent interpretation."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

IntentKind = Literal["move", "examine", "wait", "speak", "unknown"]


@dataclass(frozen=True)
class PlayerIntent:
    kind: IntentKind
    target_id: str | None = None
    minutes: int | None = None


@dataclass(frozen=True)
class PlayerIntentContext:
    current_location: dict[str, str]
    known_locations: list[dict[str, str]]
    visible_npcs: list[dict[str, str]]
    examinable_subject_ids: list[str]


class IntentInterpreter(Protocol):
    def interpret(
        self, context: PlayerIntentContext, player_input: str
    ) -> PlayerIntent: ...


class DeterministicIntentInterpreter:
    """Cheap parser for familiar command forms; unknown phrasing can go to an LLM."""

    def interpret(self, context: PlayerIntentContext, player_input: str) -> PlayerIntent:
        text = " ".join(player_input.strip().split())

        movement = re.fullmatch(r"(?:go|move|travel)(?:\s+to)?\s+(.+)", text, re.I)
        if movement:
            reference = self._normalize(movement.group(1))
            for location in context.known_locations:
                if reference in {
                    self._normalize(location["id"]),
                    self._normalize(location["name"]),
                }:
                    return PlayerIntent("move", target_id=location["id"])

        examination = re.fullmatch(
            r"(?:examine|inspect|look at)\s+(?:the\s+)?(.+)", text, re.I
        )
        if examination:
            reference = self._normalize(examination.group(1))
            for subject_id in context.examinable_subject_ids:
                if reference == self._normalize(subject_id):
                    return PlayerIntent("examine", target_id=subject_id)

        waiting = re.fullmatch(
            r"wait(?:\s+for)?\s+(\d+)(?:\s+minutes?)?", text, re.I
        )
        if waiting:
            return PlayerIntent("wait", minutes=int(waiting.group(1)))

        dialogue_prefix = next(
            (
                prefix
                for prefix in ("talk to ", "speak to ", "ask ", "tell ")
                if text.lower().startswith(prefix)
            ),
            None,
        )
        if dialogue_prefix:
            reference = text[len(dialogue_prefix) :].strip()
            if reference.lower().startswith("the "):
                reference = reference[4:]
            for npc in context.visible_npcs:
                name = npc["name"]
                if reference.lower() == name.lower() or reference.lower().startswith(
                    f"{name.lower()} "
                ):
                    return PlayerIntent("speak", target_id=npc["id"])

        return PlayerIntent("unknown")

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.lower().replace("_", " ").split())


class SafeIntentInterpreter:
    """Turn provider or validation failures into a non-mutating unknown intent."""

    def __init__(self, primary: IntentInterpreter) -> None:
        self.primary = primary

    def interpret(self, context: PlayerIntentContext, player_input: str) -> PlayerIntent:
        try:
            return self.primary.interpret(context, player_input)
        except Exception:
            logger.exception("Player-intent backend failed; returning unknown intent")
            return PlayerIntent("unknown")
