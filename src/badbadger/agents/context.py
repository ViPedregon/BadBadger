"""Build character-scoped views without exposing objective mission facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from badbadger.db.repository import GameRepository


@dataclass(frozen=True)
class NPCContext:
    npc_id: str
    name: str
    location: dict[str, str]
    visible_characters: list[dict[str, str]]
    beliefs: list[dict[str, Any]]
    recent_dialogue: list[dict[str, Any]]
    goals: list[dict[str, Any]]


class NPCContextBuilder:
    """Create the complete and intentionally narrow input for an NPC backend."""

    def __init__(self, repository: GameRepository) -> None:
        self.repository = repository

    def build(self, npc_id: str) -> NPCContext:
        npc = self.repository.get_character(npc_id)
        if npc is None or npc["kind"] != "npc":
            raise ValueError(f"Unknown NPC: {npc_id}")
        location = self.repository.get_location(npc["location_id"])
        if location is None:
            raise RuntimeError(f"NPC {npc_id} has an unknown location")
        visible = [
            {"id": character["id"], "name": character["name"], "kind": character["kind"]}
            for character in self.repository.characters_at(npc["location_id"])
            if character["id"] != npc_id
        ]
        return NPCContext(
            npc_id=npc_id,
            name=npc["name"],
            location={
                "id": location["id"],
                "name": location["name"],
                "description": location["description"],
            },
            visible_characters=visible,
            beliefs=self.repository.beliefs_for(npc_id),
            recent_dialogue=self.repository.recent_dialogue(npc_id),
            goals=self.repository.goals_for(npc_id),
        )
