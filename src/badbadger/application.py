"""Application service for the first SQLite-backed playable slice."""

from __future__ import annotations

from pathlib import Path
import re

from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction, MoveAction, WaitAction
from badbadger.engine.simulation import SimulationEngine


HELP_TEXT = """Commands and examples:
  go to Room B       travel to a known location
  examine panel      inspect a known subject
  wait 3 minutes     let mission time pass
  look               describe your current location
  status             show location and mission time
  help               show this message
  quit               save and leave the game"""


class GameApplication:
    """Translate a small text vocabulary and delegate mutations to the engine."""

    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    @property
    def repository(self) -> GameRepository:
        return self.engine.repository

    def status(self) -> list[str]:
        player = self.repository.get_player()
        location = self.repository.get_location(player["location_id"])
        if location is None:
            raise RuntimeError("Player is assigned to an unknown location")
        return [
            f"[{location['name']}]",
            location["description"],
            f"Mission time: {self.repository.current_time} minutes.",
        ]

    def handle(self, raw_input: str) -> tuple[list[str], bool]:
        """Handle one text input and return ``(messages, should_quit)``."""
        text = " ".join(raw_input.strip().split())
        lowered = text.lower()
        if not text:
            return [], False
        if lowered in {"quit", "exit", "q"}:
            return ["Game saved. Goodbye."], True
        if lowered in {"help", "?"}:
            return [HELP_TEXT], False
        if lowered in {"look", "look around", "status", "where am i"}:
            return self.status(), False

        movement = re.fullmatch(r"(?:go|move|travel)(?:\s+to)?\s+(.+)", text, re.I)
        if movement:
            location = self.repository.resolve_location(movement.group(1))
            if location is None:
                names = ", ".join(loc["name"] for loc in self.repository.list_locations())
                return [f"Unknown destination. Known locations: {names}."], False
            outcome = self.engine.perform(MoveAction(location["id"]))
            return outcome.messages, False

        examination = re.fullmatch(
            r"(?:examine|inspect|look at)\s+(?:the\s+)?(.+)", text, re.I
        )
        if examination:
            subject = self.repository.describe_subject(examination.group(1))
            if subject is None:
                return ["You find nothing meaningful to examine."], False
            outcome = self.engine.perform(ExamineAction(subject[0]))
            return outcome.messages, False

        waiting = re.fullmatch(r"wait(?:\s+for)?\s+(\d+)(?:\s+minutes?)?", text, re.I)
        if waiting:
            outcome = self.engine.perform(WaitAction(int(waiting.group(1))))
            return outcome.messages, False

        return ["I couldn't interpret that yet. Type 'help' for examples."], False


def create_prototype(database: str | Path) -> SimulationEngine:
    """Create a deliberately generic two-room test simulation."""
    repository = GameRepository(database)
    repository.create_schema()
    with repository.transaction():
        repository.initialize_simulation("prototype-0.1")
        repository.add_location("room_a", "Room A", "A plain testing room.")
        repository.add_location("room_b", "Room B", "Another plain testing room.")
        repository.add_character("player", "player", "Player", "room_a")
        repository.add_character("npc", "npc", "Observer", "room_a")

        repository.set_fact(
            "panel",
            "description",
            "A status panel shows that the test system is operating normally.",
        )
        repository.set_fact("panel", "contains_access_code", True, hidden=True)
        repository.set_belief(
            "npc",
            "room_b",
            "is_safe",
            True,
            confidence=0.6,
        )
        repository.schedule_event(
            "set_fact",
            due_time=10,
            payload={
                "subject_id": "room_b",
                "predicate": "lights_on",
                "value": False,
                "player_visible": True,
                "visible_message": "The lights in Room B flicker and go dark.",
            },
            cancellation_key="room_b_lights",
        )
        repository.record("simulation_created", result_data={"scenario": "prototype-0.1"})
    return SimulationEngine(repository)


def open_prototype(database: str | Path) -> GameApplication:
    """Resume an existing prototype save, or create it on first launch."""
    path = Path(database)
    if path.exists() and path.stat().st_size > 0:
        repository = GameRepository(path)
        try:
            repository.current_time
        except Exception:
            repository.close()
            raise
        return GameApplication(SimulationEngine(repository))
    return GameApplication(create_prototype(path))
