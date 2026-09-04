"""Validate and load small JSON scenarios into a fresh simulation database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from badbadger.db.repository import GameRepository
from badbadger.engine.simulation import SimulationEngine


class ScenarioError(ValueError):
    pass


def _required(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if not isinstance(value, expected):
        raise ScenarioError(f"Scenario field '{key}' must be {expected.__name__}")
    return value


def _validate(data: dict[str, Any]) -> None:
    locations = _required(data, "locations", list)
    characters = _required(data, "characters", list)
    location_ids: set[str] = set()
    character_ids: set[str] = set()
    for index, item in enumerate(locations):
        if not isinstance(item, dict):
            raise ScenarioError(f"locations[{index}] must be an object")
        for key in ("id", "name", "description"):
            _required(item, key, str)
        if item["id"] in location_ids:
            raise ScenarioError(f"Duplicate location id: {item['id']}")
        location_ids.add(item["id"])
    for index, item in enumerate(characters):
        if not isinstance(item, dict):
            raise ScenarioError(f"characters[{index}] must be an object")
        for key in ("id", "kind", "name", "location_id"):
            _required(item, key, str)
        if item["kind"] not in {"player", "npc"}:
            raise ScenarioError(f"Invalid character kind: {item['kind']}")
        if item["id"] in character_ids:
            raise ScenarioError(f"Duplicate character id: {item['id']}")
        if item["location_id"] not in location_ids:
            raise ScenarioError(f"Unknown character location: {item['location_id']}")
        character_ids.add(item["id"])
        parameters = item.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ScenarioError(f"Parameters for {item['id']} must be an object")
    if sum(item["kind"] == "player" for item in characters) != 1:
        raise ScenarioError("Scenario must define exactly one player")
    for connection in data.get("connections", []):
        if not isinstance(connection, dict):
            raise ScenarioError("Each connection must be an object")
        if connection.get("from") not in location_ids or connection.get("to") not in location_ids:
            raise ScenarioError("Connection references an unknown location")
        if not isinstance(connection.get("minutes"), int) or connection["minutes"] < 1:
            raise ScenarioError("Connection minutes must be a positive integer")


def load_scenario(path: str | Path, database: str | Path) -> SimulationEngine:
    """Load a validated scenario atomically into a new SQLite save."""
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScenarioError(f"Cannot read scenario {source}: {error}") from error
    if not isinstance(data, dict):
        raise ScenarioError("Scenario root must be an object")
    scenario_id = _required(data, "scenario_id", str)
    _validate(data)
    locations = data["locations"]
    characters = data["characters"]

    repository = GameRepository(database)
    repository.create_schema()
    try:
        with repository.transaction():
            repository.initialize_simulation(scenario_id)
            for item in locations:
                repository.add_location(item["id"], item["name"], item["description"])
            for item in data.get("connections", []):
                repository.add_connection(item["from"], item["to"], item["minutes"])
            for item in characters:
                repository.add_character(item["id"], item["kind"], item["name"], item["location_id"])
                if item["kind"] == "npc":
                    repository.set_npc_parameters(item["id"], item.get("parameters", {}))
                    for goal in item.get("goals", []):
                        repository.add_goal(item["id"], goal["description"], goal.get("priority", 1))
                    for belief in item.get("beliefs", []):
                        repository.set_belief(
                            item["id"], belief["subject_id"], belief["predicate"],
                            belief["value"], belief.get("confidence", 1.0),
                            source_type="initial", detail="Scenario-defined starting belief.",
                        )
                    decision_after = item.get("parameters", {}).get("first_decision_after_minutes")
                    if isinstance(decision_after, int):
                        repository.ensure_decision_event(item["id"], decision_after)
            for fact in data.get("facts", []):
                repository.set_fact(fact["subject_id"], fact["predicate"], fact["value"], hidden=fact.get("hidden", False))
            for event in data.get("events", []):
                repository.schedule_event(event["type"], event["due_time"], event.get("payload", {}), event.get("cancellation_key"))
            repository.record("simulation_created", result_data={"scenario": scenario_id})
    except Exception:
        repository.close()
        raise
    return SimulationEngine(repository)
