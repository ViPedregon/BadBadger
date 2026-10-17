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
            matches = self._exact_reference_matches(
                reference, context.known_locations
            )
            if len(matches) == 1:
                return PlayerIntent("move", target_id=matches[0]["id"])

        examination = re.fullmatch(
            r"(?:examine|inspect|look at)\s+(?:the\s+)?(.+)", text, re.I
        )
        if examination:
            reference = self._normalize(examination.group(1))
            matches = [
                subject_id for subject_id in context.examinable_subject_ids
                if reference in self._aliases(subject_id, subject_id)
            ]
            if len(matches) == 1:
                return PlayerIntent("examine", target_id=matches[0])

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
            normalized_reference = self._normalize(reference)
            matches = []
            for npc in context.visible_npcs:
                aliases = self._aliases(npc["id"], npc["name"])
                if any(
                    normalized_reference == alias
                    or normalized_reference.startswith(alias + " ")
                    for alias in aliases
                ):
                    matches.append(npc)
            unique = {item["id"]: item for item in matches}
            if len(unique) == 1:
                return PlayerIntent("speak", target_id=next(iter(unique)))

        return PlayerIntent("unknown")

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.lower().replace("_", " ").split())

    @classmethod
    def _aliases(cls, item_id: str, name: str) -> set[str]:
        normalized_id = cls._normalize(item_id)
        normalized_name = cls._normalize(name)
        tokens = {token for token in normalized_name.split() if len(token) >= 3}
        return {normalized_id, normalized_name, *tokens}

    @classmethod
    def _exact_reference_matches(
        cls, reference: str, items: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        return [
            item for item in items
            if reference in cls._aliases(item["id"], item["name"])
        ]


class SafeIntentInterpreter:
    """Turn provider or validation failures into a non-mutating unknown intent."""

    def __init__(self, primary: IntentInterpreter) -> None:
        self.primary = primary
        self.failed_last_call = False

    def interpret(self, context: PlayerIntentContext, player_input: str) -> PlayerIntent:
        self.failed_last_call = False
        try:
            return self.primary.interpret(context, player_input)
        except Exception as error:
            self.failed_last_call = True
            logger.error(
                "Player-intent backend failed; returning unknown intent (%s)",
                type(error).__name__,
            )
            return PlayerIntent("unknown")

    def consume_failure(self) -> bool:
        failed = self.failed_last_call
        self.failed_last_call = False
        return failed
