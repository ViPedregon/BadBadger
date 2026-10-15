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
    presentation = data.get("presentation", {})
    if not isinstance(presentation, dict):
        raise ScenarioError("Scenario presentation must be an object")
    for key in ("title", "intro", "objective"):
        value = presentation.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ScenarioError(f"Presentation {key} must be non-empty text")
    locations = _required(data, "locations", list)
    characters = _required(data, "characters", list)
    location_ids: set[str] = set()
    character_ids: set[str] = set()
    character_kinds: dict[str, str] = {}
    for index, item in enumerate(locations):
        if not isinstance(item, dict):
            raise ScenarioError(f"locations[{index}] must be an object")
        for key in ("id", "name", "description"):
            _required(item, key, str)
        if item["id"] in location_ids:
            raise ScenarioError(f"Duplicate location id: {item['id']}")
        location_ids.add(item["id"])
        if not isinstance(item.get("appearance", {}), dict):
            raise ScenarioError(f"Appearance for location {item['id']} must be an object")
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
        character_kinds[item["id"]] = item["kind"]
        parameters = item.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ScenarioError(f"Parameters for {item['id']} must be an object")
        if not isinstance(item.get("appearance", {}), dict):
            raise ScenarioError(f"Appearance for character {item['id']} must be an object")
    if sum(item["kind"] == "player" for item in characters) != 1:
        raise ScenarioError("Scenario must define exactly one player")
    goal_ids_by_character: dict[str, set[str]] = {}
    for item in characters:
        goals = item.get("goals", [])
        if not isinstance(goals, list):
            raise ScenarioError(f"Goals for {item['id']} must be a list")
        if goals and item["kind"] != "npc":
            raise ScenarioError("Only NPCs may define goals")
        goal_ids: set[str] = set()
        for index, goal in enumerate(goals, start=1):
            if not isinstance(goal, dict):
                raise ScenarioError(f"Invalid goal for {item['id']}")
            goal_id = goal.get("id", f"goal_{index}")
            description = goal.get("description")
            priority = goal.get("priority", 1)
            if not isinstance(goal_id, str) or not goal_id.strip():
                raise ScenarioError(f"Goal id for {item['id']} must be non-empty text")
            if goal_id in goal_ids:
                raise ScenarioError(f"Duplicate goal id for {item['id']}: {goal_id}")
            if not isinstance(description, str) or not description.strip():
                raise ScenarioError(
                    f"Goal description for {item['id']} must be non-empty text"
                )
            if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
                raise ScenarioError(
                    f"Goal priority for {item['id']} must be a positive integer"
                )
            goal_ids.add(goal_id)
        goal_ids_by_character[item["id"]] = goal_ids
    for item in characters:
        relationships = item.get("relationships", [])
        if not isinstance(relationships, list):
            raise ScenarioError(f"Relationships for {item['id']} must be a list")
        for relationship in relationships:
            if not isinstance(relationship, dict):
                raise ScenarioError(f"Invalid relationship for {item['id']}")
            target_id = relationship.get("target_id")
            if target_id not in character_ids or target_id == item["id"]:
                raise ScenarioError(f"Invalid relationship target for {item['id']}")
            for key in ("trust", "affinity"):
                value = relationship.get(key, 0.0)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not -1 <= value <= 1
                ):
                    raise ScenarioError(
                        f"Relationship {key} for {item['id']} must be between -1 and 1"
                    )
            reason = relationship.get(
                "reason", "Scenario-defined starting relationship."
            )
            if not isinstance(reason, str) or not reason.strip():
                raise ScenarioError(
                    f"Relationship reason for {item['id']} must be non-empty text"
                )
    for connection in data.get("connections", []):
        if not isinstance(connection, dict):
            raise ScenarioError("Each connection must be an object")
        if connection.get("from") not in location_ids or connection.get("to") not in location_ids:
            raise ScenarioError("Connection references an unknown location")
        if not isinstance(connection.get("minutes"), int) or connection["minutes"] < 1:
            raise ScenarioError("Connection minutes must be a positive integer")
    disclosure_ids: set[str] = set()
    for item in characters:
        disclosures = item.get("disclosures", [])
        if not isinstance(disclosures, list):
            raise ScenarioError(f"Disclosures for {item['id']} must be a list")
        if disclosures and item["kind"] != "npc":
            raise ScenarioError("Only NPCs may define disclosures")
        for disclosure in disclosures:
            if not isinstance(disclosure, dict):
                raise ScenarioError(f"Invalid disclosure for {item['id']}")
            for key in ("id", "topic", "content"):
                _required(disclosure, key, str)
                if not disclosure[key].strip():
                    raise ScenarioError(
                        f"Disclosure {key} for {item['id']} cannot be empty"
                    )
            if disclosure["id"] in disclosure_ids:
                raise ScenarioError(f"Duplicate disclosure id: {disclosure['id']}")
            disclosure_ids.add(disclosure["id"])
            terms = disclosure.get("request_terms", [])
            if not isinstance(terms, list) or not terms or not all(
                isinstance(term, str) and term.strip() for term in terms
            ):
                raise ScenarioError(
                    f"Disclosure terms for {disclosure['id']} must be non-empty text"
                )
            min_trust = disclosure.get("min_trust", 0.0)
            if (
                not isinstance(min_trust, (int, float))
                or isinstance(min_trust, bool)
                or not -1 <= min_trust <= 1
            ):
                raise ScenarioError(
                    f"Disclosure trust for {disclosure['id']} must be between -1 and 1"
                )
            belief = disclosure.get("belief")
            if not isinstance(belief, dict) or not all(
                key in belief for key in ("subject_id", "predicate", "value")
            ):
                raise ScenarioError(f"Invalid belief for disclosure {disclosure['id']}")
            if not all(
                isinstance(belief[key], str) and belief[key].strip()
                for key in ("subject_id", "predicate")
            ):
                raise ScenarioError(
                    f"Disclosure belief ids for {disclosure['id']} must be non-empty text"
                )
            confidence = belief.get("confidence", 1.0)
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ScenarioError(
                    f"Disclosure confidence for {disclosure['id']} must be between 0 and 1"
                )
    for action in data.get("npc_actions", []):
        if not isinstance(action, dict):
            raise ScenarioError("Each NPC action must be an object")
        for key in ("id", "character_id", "location_id", "label", "description"):
            _required(action, key, str)
        if action["character_id"] not in character_ids:
            raise ScenarioError(f"NPC action references unknown character: {action['character_id']}")
        if character_kinds[action["character_id"]] != "npc":
            raise ScenarioError(f"NPC action actor is not an NPC: {action['character_id']}")
        if action["location_id"] not in location_ids:
            raise ScenarioError(f"NPC action references unknown location: {action['location_id']}")
        if not isinstance(action.get("preconditions", []), list):
            raise ScenarioError(f"Preconditions for action {action['id']} must be a list")
        if not isinstance(action.get("effects", {}), dict):
            raise ScenarioError(f"Effects for action {action['id']} must be an object")
        for condition in action.get("preconditions", []):
            if not isinstance(condition, dict) or not all(
                key in condition for key in ("subject_id", "predicate", "equals")
            ):
                raise ScenarioError(f"Invalid precondition for action {action['id']}")
        effects = action.get("effects", {})
        decision = effects.get("decision", {})
        if not isinstance(decision, dict):
            raise ScenarioError(f"Decision policy for action {action['id']} must be an object")
        for key, default in (("base_score", 1.0), ("minimum_score", 0.0)):
            value = decision.get(key, default)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ScenarioError(
                    f"Decision {key} for action {action['id']} must be numeric"
                )
        factors = decision.get("belief_factors", [])
        if not isinstance(factors, list):
            raise ScenarioError(f"Belief factors for action {action['id']} must be a list")
        for factor in factors:
            if not isinstance(factor, dict) or not all(
                key in factor
                for key in ("subject_id", "predicate", "equals", "score", "reason")
            ):
                raise ScenarioError(f"Invalid belief factor for action {action['id']}")
            if not all(
                isinstance(factor[key], str) and factor[key].strip()
                for key in ("subject_id", "predicate", "reason")
            ):
                raise ScenarioError(f"Belief factor text for action {action['id']} is invalid")
            if not isinstance(factor["score"], (int, float)) or isinstance(
                factor["score"], bool
            ):
                raise ScenarioError(f"Belief factor score for action {action['id']} is invalid")
            confidence = factor.get("min_confidence", 0.0)
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ScenarioError(
                    f"Belief factor confidence for action {action['id']} must be between 0 and 1"
                )
        parameter_factors = decision.get("parameter_factors", [])
        if not isinstance(parameter_factors, list):
            raise ScenarioError(
                f"Parameter factors for action {action['id']} must be a list"
            )
        for factor in parameter_factors:
            if not isinstance(factor, dict) or not all(
                key in factor
                for key in ("parameter", "operator", "value", "score", "reason")
            ):
                raise ScenarioError(f"Invalid parameter factor for action {action['id']}")
            if not all(
                isinstance(factor[key], str) and factor[key].strip()
                for key in ("parameter", "operator", "reason")
            ):
                raise ScenarioError(
                    f"Parameter factor text for action {action['id']} is invalid"
                )
            if factor["operator"] not in {"equals", "gte", "lte"}:
                raise ScenarioError(
                    f"Parameter factor operator for action {action['id']} is invalid"
                )
            if isinstance(factor["value"], (dict, list)):
                raise ScenarioError(
                    f"Parameter factor value for action {action['id']} must be scalar"
                )
            if not isinstance(factor["score"], (int, float)) or isinstance(
                factor["score"], bool
            ):
                raise ScenarioError(
                    f"Parameter factor score for action {action['id']} is invalid"
                )
            if factor["operator"] in {"gte", "lte"} and (
                not isinstance(factor["value"], (int, float))
                or isinstance(factor["value"], bool)
            ):
                raise ScenarioError(
                    f"Ordered parameter factor for action {action['id']} requires a numeric value"
                )
        memory_factors = decision.get("memory_factors", [])
        if not isinstance(memory_factors, list):
            raise ScenarioError(
                f"Memory factors for action {action['id']} must be a list"
            )
        for factor in memory_factors:
            if not isinstance(factor, dict) or not all(
                key in factor for key in ("memory_kind", "score", "reason")
            ):
                raise ScenarioError(f"Invalid memory factor for action {action['id']}")
            if not all(
                isinstance(factor[key], str) and factor[key].strip()
                for key in ("memory_kind", "reason")
            ):
                raise ScenarioError(
                    f"Memory factor text for action {action['id']} is invalid"
                )
            for key in ("subject_id", "source_character_id"):
                if key in factor and (
                    not isinstance(factor[key], str) or not factor[key].strip()
                ):
                    raise ScenarioError(
                        f"Memory factor {key} for action {action['id']} is invalid"
                    )
            if (
                "source_character_id" in factor
                and factor["source_character_id"] not in character_ids
            ):
                raise ScenarioError(
                    f"Memory factor for action {action['id']} references unknown character"
                )
            if not isinstance(factor["score"], (int, float)) or isinstance(
                factor["score"], bool
            ):
                raise ScenarioError(
                    f"Memory factor score for action {action['id']} is invalid"
                )
            salience = factor.get("min_salience", 0.0)
            if (
                not isinstance(salience, (int, float))
                or isinstance(salience, bool)
                or not 0 <= salience <= 1
            ):
                raise ScenarioError(
                    f"Memory factor salience for action {action['id']} must be between 0 and 1"
                )
            within = factor.get("within_minutes")
            if within is not None and (
                not isinstance(within, int) or isinstance(within, bool) or within < 0
            ):
                raise ScenarioError(
                    f"Memory factor window for action {action['id']} must be a non-negative integer"
                )
        goal_factors = decision.get("goal_factors", [])
        if not isinstance(goal_factors, list):
            raise ScenarioError(f"Goal factors for action {action['id']} must be a list")
        for factor in goal_factors:
            if not isinstance(factor, dict) or not all(
                key in factor for key in ("goal_id", "score_per_priority", "reason")
            ):
                raise ScenarioError(f"Invalid goal factor for action {action['id']}")
            if factor["goal_id"] not in goal_ids_by_character[action["character_id"]]:
                raise ScenarioError(
                    f"Goal factor for action {action['id']} references unknown actor goal"
                )
            if not isinstance(factor["reason"], str) or not factor["reason"].strip():
                raise ScenarioError(
                    f"Goal factor reason for action {action['id']} is invalid"
                )
            if not isinstance(factor["score_per_priority"], (int, float)) or isinstance(
                factor["score_per_priority"], bool
            ):
                raise ScenarioError(
                    f"Goal factor score for action {action['id']} is invalid"
                )
            if factor.get("status", "active") not in {
                "active", "completed", "failed", "abandoned"
            }:
                raise ScenarioError(
                    f"Goal factor status for action {action['id']} is invalid"
                )
        relationship_factors = decision.get("relationship_factors", [])
        if not isinstance(relationship_factors, list):
            raise ScenarioError(
                f"Relationship factors for action {action['id']} must be a list"
            )
        for factor in relationship_factors:
            if not isinstance(factor, dict) or not all(
                key in factor
                for key in ("target_id", "metric", "operator", "value", "score", "reason")
            ):
                raise ScenarioError(
                    f"Invalid relationship factor for action {action['id']}"
                )
            if (
                factor["target_id"] not in character_ids
                or factor["target_id"] == action["character_id"]
            ):
                raise ScenarioError(
                    f"Relationship factor target for action {action['id']} is invalid"
                )
            if factor["metric"] not in {"trust", "affinity"}:
                raise ScenarioError(
                    f"Relationship factor metric for action {action['id']} is invalid"
                )
            if factor["operator"] not in {"gte", "lte"}:
                raise ScenarioError(
                    f"Relationship factor operator for action {action['id']} is invalid"
                )
            for key in ("value", "score"):
                if not isinstance(factor[key], (int, float)) or isinstance(
                    factor[key], bool
                ):
                    raise ScenarioError(
                        f"Relationship factor {key} for action {action['id']} is invalid"
                    )
            if not -1 <= factor["value"] <= 1:
                raise ScenarioError(
                    f"Relationship factor value for action {action['id']} must be between -1 and 1"
                )
            if not isinstance(factor["reason"], str) or not factor["reason"].strip():
                raise ScenarioError(
                    f"Relationship factor reason for action {action['id']} is invalid"
                )
        trust_required = effects.get("player_trust_required")
        if trust_required is not None and (
            not isinstance(trust_required, (int, float))
            or isinstance(trust_required, bool)
            or not -1 <= trust_required <= 1
        ):
            raise ScenarioError(
                f"Player trust requirement for action {action['id']} must be between -1 and 1"
            )
        if not isinstance(effects.get("facts", []), list):
            raise ScenarioError(f"Fact effects for action {action['id']} must be a list")
        if not isinstance(effects.get("observations", []), list):
            raise ScenarioError(f"Observations for action {action['id']} must be a list")
        for observation in effects.get("observations", []):
            if not isinstance(observation, dict) or not isinstance(
                observation.get("summary"), str
            ):
                raise ScenarioError(f"Invalid observation for action {action['id']}")
            salience = observation.get("salience", 0.5)
            if not isinstance(salience, (int, float)) or not 0 <= salience <= 1:
                raise ScenarioError(f"Invalid observation salience for action {action['id']}")
        goal_effects = effects.get("goals", [])
        if not isinstance(goal_effects, list):
            raise ScenarioError(f"Goal effects for action {action['id']} must be a list")
        for goal_effect in goal_effects:
            if not isinstance(goal_effect, dict) or not all(
                key in goal_effect
                for key in ("character_id", "goal_id", "status", "reason")
            ):
                raise ScenarioError(f"Invalid goal effect for action {action['id']}")
            target_id = goal_effect["character_id"]
            if (
                target_id not in goal_ids_by_character
                or goal_effect["goal_id"] not in goal_ids_by_character[target_id]
            ):
                raise ScenarioError(
                    f"Goal effect for action {action['id']} references unknown goal"
                )
            if goal_effect["status"] not in {"completed", "failed", "abandoned"}:
                raise ScenarioError(
                    f"Goal effect status for action {action['id']} is invalid"
                )
            if not isinstance(goal_effect["reason"], str) or not goal_effect["reason"].strip():
                raise ScenarioError(
                    f"Goal effect reason for action {action['id']} is invalid"
                )
        relationship_effects = effects.get("relationships", [])
        if not isinstance(relationship_effects, list):
            raise ScenarioError(
                f"Relationship effects for action {action['id']} must be a list"
            )
        for relationship_effect in relationship_effects:
            if not isinstance(relationship_effect, dict) or not all(
                key in relationship_effect
                for key in ("observer_id", "target_id", "reason")
            ):
                raise ScenarioError(
                    f"Invalid relationship effect for action {action['id']}"
                )
            observer_id = relationship_effect["observer_id"]
            target_id = relationship_effect["target_id"]
            if (
                observer_id not in character_ids
                or target_id not in character_ids
                or observer_id == target_id
            ):
                raise ScenarioError(
                    f"Relationship effect pair for action {action['id']} is invalid"
                )
            for key in ("trust_delta", "affinity_delta"):
                value = relationship_effect.get(key, 0.0)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not -1 <= value <= 1
                ):
                    raise ScenarioError(
                        f"Relationship effect {key} for action {action['id']} is invalid"
                    )
            if not isinstance(relationship_effect["reason"], str) or not (
                relationship_effect["reason"].strip()
            ):
                raise ScenarioError(
                    f"Relationship effect reason for action {action['id']} is invalid"
                )
            conditional = relationship_effect.get("when_requested_by_player", False)
            if not isinstance(conditional, bool):
                raise ScenarioError(
                    f"Relationship effect condition for action {action['id']} is invalid"
                )
        scheduled_events = effects.get("scheduled_events", [])
        if not isinstance(scheduled_events, list):
            raise ScenarioError(
                f"Scheduled effects for action {action['id']} must be a list"
            )
        for scheduled in scheduled_events:
            if not isinstance(scheduled, dict) or not all(
                key in scheduled for key in ("type", "delay_minutes", "payload")
            ):
                raise ScenarioError(f"Invalid scheduled effect for action {action['id']}")
            if scheduled["type"] != "set_simulation_status":
                raise ScenarioError(
                    f"Unsupported scheduled effect for action {action['id']}"
                )
            if (
                not isinstance(scheduled["delay_minutes"], int)
                or isinstance(scheduled["delay_minutes"], bool)
                or scheduled["delay_minutes"] < 1
            ):
                raise ScenarioError(
                    f"Scheduled effect delay for action {action['id']} must be positive"
                )
            payload = scheduled["payload"]
            if not isinstance(payload, dict) or payload.get("status") not in {
                "succeeded", "failed", "ended"
            }:
                raise ScenarioError(
                    f"Scheduled terminal status for action {action['id']} is invalid"
                )
            if "visible_message" in payload and not isinstance(
                payload["visible_message"], str
            ):
                raise ScenarioError(
                    f"Scheduled message for action {action['id']} is invalid"
                )
        cancel_events = effects.get("cancel_events", [])
        if not isinstance(cancel_events, list) or not all(
            isinstance(key, str) and key.strip() for key in cancel_events
        ):
            raise ScenarioError(
                f"Cancelled event keys for action {action['id']} must be text"
            )
        if effects.get("simulation_status") not in {
            None, "active", "succeeded", "failed", "ended"
        }:
            raise ScenarioError(f"Invalid mission status for action {action['id']}")

    events = data.get("events", [])
    if not isinstance(events, list):
        raise ScenarioError("Scenario events must be a list")
    for event in events:
        if not isinstance(event, dict):
            raise ScenarioError("Each scheduled event must be an object")
        if event.get("type") not in {
            "set_fact", "set_simulation_status", "narration"
        }:
            raise ScenarioError("Scheduled event has an unsupported type")
        due_time = event.get("due_time")
        if not isinstance(due_time, int) or isinstance(due_time, bool) or due_time < 0:
            raise ScenarioError("Scheduled event due_time must be non-negative")
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            raise ScenarioError("Scheduled event payload must be an object")
        if event["type"] == "narration" and (
            not isinstance(payload.get("message"), str)
            or not payload["message"].strip()
            or payload.get("player_visible") is not True
        ):
            raise ScenarioError(
                "Narration events require a visible, non-empty message"
            )
        cancellation_key = event.get("cancellation_key")
        if cancellation_key is not None and (
            not isinstance(cancellation_key, str) or not cancellation_key.strip()
        ):
            raise ScenarioError("Scheduled event cancellation_key must be text")


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
            repository.set_scenario_presentation(data.get("presentation", {}))
            for item in locations:
                repository.add_location(item["id"], item["name"], item["description"])
                if item.get("appearance"):
                    repository.set_appearance(item["id"], "location", item["appearance"])
            for item in data.get("connections", []):
                repository.add_connection(item["from"], item["to"], item["minutes"])
            for item in characters:
                repository.add_character(item["id"], item["kind"], item["name"], item["location_id"])
                if item.get("appearance"):
                    repository.set_appearance(item["id"], "character", item["appearance"])
                if item["kind"] == "npc":
                    repository.set_npc_parameters(item["id"], item.get("parameters", {}))
                    for index, goal in enumerate(item.get("goals", []), start=1):
                        repository.add_goal(
                            item["id"], goal.get("id", f"goal_{index}"),
                            goal["description"], goal.get("priority", 1),
                        )
                    for belief in item.get("beliefs", []):
                        repository.set_belief(
                            item["id"], belief["subject_id"], belief["predicate"],
                            belief["value"], belief.get("confidence", 1.0),
                            source_type="initial", detail="Scenario-defined starting belief.",
                        )
                    decision_after = item.get("parameters", {}).get("first_decision_after_minutes")
                    if isinstance(decision_after, int):
                        repository.ensure_decision_event(item["id"], decision_after)
                    for disclosure in item.get("disclosures", []):
                        repository.add_npc_disclosure(
                            disclosure["id"], item["id"], disclosure["topic"],
                            disclosure["request_terms"],
                            float(disclosure.get("min_trust", 0.0)),
                            disclosure["content"], disclosure["belief"],
                        )
            for item in characters:
                for relationship in item.get("relationships", []):
                    repository.set_relationship(
                        item["id"], relationship["target_id"],
                        float(relationship.get("trust", 0.0)),
                        float(relationship.get("affinity", 0.0)),
                        reason=relationship.get(
                            "reason", "Scenario-defined starting relationship."
                        ),
                    )
            for fact in data.get("facts", []):
                repository.set_fact(fact["subject_id"], fact["predicate"], fact["value"], hidden=fact.get("hidden", False))
            for action in data.get("npc_actions", []):
                repository.add_npc_action(
                    action["id"], action["character_id"], action["location_id"],
                    action["label"], action["description"],
                    action.get("preconditions", []), action.get("effects", {}),
                )
            for event in data.get("events", []):
                repository.schedule_event(event["type"], event["due_time"], event.get("payload", {}), event.get("cancellation_key"))
            repository.record("simulation_created", result_data={"scenario": scenario_id})
    except Exception:
        repository.close()
        raise
    return SimulationEngine(repository)
