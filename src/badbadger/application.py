"""Composition helpers for the first SQLite-backed vertical slice."""

from __future__ import annotations

from pathlib import Path

from badbadger.db.repository import GameRepository
from badbadger.engine.simulation import SimulationEngine


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
