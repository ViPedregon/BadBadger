"""Game state models: Player, NPC, and GameState."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Player:
    """Represents the human player character."""

    name: str
    health: int = 100
    inventory: list[str] = field(default_factory=list)


@dataclass
class NPC:
    """Represents a non-player character managed by an agent."""

    name: str
    description: str
    dialogue_history: list[dict[str, str]] = field(default_factory=list)
    is_active: bool = True

    def add_dialogue(self, speaker: str, text: str) -> None:
        """Record a line of dialogue."""
        self.dialogue_history.append({"speaker": speaker, "text": text})


@dataclass
class Location:
    """A place in the game world."""

    name: str
    description: str
    npcs: list[str] = field(default_factory=list)  # NPC names present here


@dataclass
class GameState:
    """Central game state managed by the Game Master."""

    player: Player
    locations: dict[str, Location] = field(default_factory=dict)
    npcs: dict[str, NPC] = field(default_factory=dict)
    current_location: str = ""
    turn: int = 0
    game_over: bool = False
    log: list[str] = field(default_factory=list)

    def record(self, message: str) -> None:
        """Append a message to the game log."""
        self.log.append(message)

    def current_location_obj(self) -> Optional[Location]:
        return self.locations.get(self.current_location)

    def npcs_at_current_location(self) -> list[NPC]:
        loc = self.current_location_obj()
        if loc is None:
            return []
        return [self.npcs[n] for n in loc.npcs if n in self.npcs]
