"""Narrow SQLite repository used by the deterministic simulation engine."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator


class GameRepository:
    """Own the database connection and expose explicit state operations."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self.connection = sqlite3.connect(self.database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._transaction_depth = 0

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "GameRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Join an outer operation without letting inner services commit it."""
        depth = self._transaction_depth
        savepoint = f"operation_{depth}"
        if depth:
            self.connection.execute(f"SAVEPOINT {savepoint}")
        elif not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield
        except BaseException:
            if depth:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.connection.rollback()
            raise
        else:
            if depth:
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.connection.commit()
        finally:
            self._transaction_depth -= 1

    def create_schema(self) -> None:
        schema = files("badbadger.db").joinpath("schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        goal_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(npc_goals)")
        }
        if "goal_id" not in goal_columns:
            self.connection.execute("ALTER TABLE npc_goals ADD COLUMN goal_id TEXT")
        if "status" not in goal_columns:
            self.connection.execute(
                "ALTER TABLE npc_goals ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
        self.connection.execute(
            "UPDATE npc_goals SET goal_id='legacy_' || id WHERE goal_id IS NULL"
        )
        self.connection.execute(
            "UPDATE npc_goals SET status=CASE WHEN active=1 THEN 'active' ELSE 'abandoned' END "
            "WHERE status IS NULL OR status=''"
        )
        biography_columns = {
            row["name"] for row in self.connection.execute(
                "PRAGMA table_info(character_biography_fragments)"
            )
        }
        if "behavioral_guidance" not in biography_columns:
            self.connection.execute(
                "ALTER TABLE character_biography_fragments "
                "ADD COLUMN behavioral_guidance TEXT"
            )
        if "action_influences_json" not in biography_columns:
            self.connection.execute(
                "ALTER TABLE character_biography_fragments "
                "ADD COLUMN action_influences_json TEXT NOT NULL DEFAULT '[]'"
            )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS npc_goals_by_stable_id "
            "ON npc_goals(character_id, goal_id)"
        )
        self.connection.execute(
            """
            INSERT INTO belief_evidence(
                belief_id, source_type, value_json, confidence, detail, game_time
            )
            SELECT b.id, 'legacy', b.value_json, b.confidence,
                   'Belief predates evidence tracking.', b.updated_at_game_time
            FROM beliefs AS b
            WHERE NOT EXISTS (
                SELECT 1 FROM belief_evidence AS e WHERE e.belief_id = b.id
            )
            """
        )
        self.connection.commit()

    def initialize_simulation(self, scenario_id: str) -> None:
        self.connection.execute(
            "INSERT INTO simulation(id, scenario_id) VALUES (1, ?)",
            (scenario_id,),
        )

    def set_scenario_presentation(self, details: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO scenario_presentation(id,details_json) VALUES (1,?) "
            "ON CONFLICT(id) DO UPDATE SET details_json=excluded.details_json",
            (json.dumps(details, sort_keys=True),),
        )

    def scenario_presentation(self) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT details_json FROM scenario_presentation WHERE id=1"
        ).fetchone()
        return json.loads(row["details_json"]) if row else {}

    @property
    def current_time(self) -> int:
        row = self.connection.execute(
            "SELECT current_time_minutes FROM simulation WHERE id = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Simulation has not been initialized")
        return int(row["current_time_minutes"])

    @property
    def scenario_id(self) -> str:
        row = self.connection.execute(
            "SELECT scenario_id FROM simulation WHERE id = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Simulation has not been initialized")
        return str(row["scenario_id"])

    @property
    def simulation_status(self) -> str:
        row = self.connection.execute("SELECT status FROM simulation WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("Simulation has not been initialized")
        return str(row["status"])

    def advance_time(self, minutes: int) -> int:
        if minutes < 0:
            raise ValueError("Mission time cannot move backwards")
        self.connection.execute(
            "UPDATE simulation SET current_time_minutes = current_time_minutes + ? WHERE id = 1",
            (minutes,),
        )
        return self.current_time

    def add_location(self, location_id: str, name: str, description: str) -> None:
        self.connection.execute(
            "INSERT INTO locations(id, name, description) VALUES (?, ?, ?)",
            (location_id, name, description),
        )

    def set_appearance(
        self, subject_id: str, subject_kind: str, details: dict[str, Any]
    ) -> None:
        if subject_kind not in {"character", "location"}:
            raise ValueError(f"Unknown appearance subject kind: {subject_kind}")
        self.connection.execute(
            """
            INSERT INTO appearances(subject_id, subject_kind, details_json)
            VALUES (?, ?, ?)
            ON CONFLICT(subject_kind, subject_id) DO UPDATE SET
                details_json=excluded.details_json
            """,
            (subject_id, subject_kind, json.dumps(details, sort_keys=True)),
        )

    def appearance_for(self, subject_id: str, subject_kind: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT details_json FROM appearances WHERE subject_id=? AND subject_kind=?",
            (subject_id, subject_kind),
        ).fetchone()
        return json.loads(row["details_json"]) if row else {}

    def set_character_canon(
        self, character_id: str, canon: dict[str, Any]
    ) -> None:
        if self.get_character(character_id) is None:
            raise ValueError(f"Unknown character: {character_id}")
        self.connection.execute(
            """
            INSERT INTO character_canon(
                character_id,role,voice,values_json,boundaries_json
            ) VALUES (?,?,?,?,?)
            ON CONFLICT(character_id) DO UPDATE SET
                role=excluded.role,
                voice=excluded.voice,
                values_json=excluded.values_json,
                boundaries_json=excluded.boundaries_json
            """,
            (
                character_id,
                canon.get("role", ""),
                canon.get("voice", ""),
                json.dumps(canon.get("values", [])),
                json.dumps(canon.get("boundaries", [])),
            ),
        )
        self.connection.execute(
            "DELETE FROM character_biography_fragments WHERE character_id=?",
            (character_id,),
        )
        for fragment in canon.get("biography", []):
            self.connection.execute(
                """
                INSERT INTO character_biography_fragments(
                    character_id,fragment_id,summary,behavioral_guidance,
                    action_influences_json,
                    importance,visibility,
                    awareness,certainty,topics_json,emotional_tags_json,
                    related_character_ids_json,period,source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    character_id, fragment["id"], fragment["summary"],
                    fragment.get("behavioral_guidance"),
                    json.dumps(fragment.get("action_influences", [])),
                    float(fragment.get("importance", 0.5)),
                    fragment.get("visibility", "private"),
                    fragment.get("awareness", "conscious"),
                    float(fragment.get("certainty", 1.0)),
                    json.dumps(fragment.get("topics", [])),
                    json.dumps(fragment.get("emotional_tags", [])),
                    json.dumps(fragment.get("related_character_ids", [])),
                    fragment.get("period"), fragment.get("source"),
                ),
            )

    def character_canon(self, character_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM character_canon WHERE character_id=?",
            (character_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "character_id": character_id,
            "role": row["role"],
            "voice": row["voice"],
            "values": json.loads(row["values_json"]),
            "boundaries": json.loads(row["boundaries_json"]),
            "biography": self.biography_fragments(character_id),
        }

    def biography_fragments(self, character_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM character_biography_fragments WHERE character_id=? "
            "ORDER BY importance DESC,fragment_id",
            (character_id,),
        ).fetchall()
        fragments: list[dict[str, Any]] = []
        for row in rows:
            fragments.append({
                "id": row["fragment_id"],
                "summary": row["summary"],
                "behavioral_guidance": row["behavioral_guidance"],
                "action_influences": json.loads(row["action_influences_json"]),
                "importance": float(row["importance"]),
                "visibility": row["visibility"],
                "awareness": row["awareness"],
                "certainty": float(row["certainty"]),
                "topics": json.loads(row["topics_json"]),
                "emotional_tags": json.loads(row["emotional_tags_json"]),
                "related_character_ids": json.loads(
                    row["related_character_ids_json"]
                ),
                "period": row["period"],
                "source": row["source"],
            })
        return fragments

    def add_connection(self, origin: str, destination: str, minutes: int) -> None:
        self.connection.execute("INSERT OR IGNORE INTO location_connections VALUES (?,?,?)", (origin,destination,minutes))

    def connection_duration(self, origin: str, destination: str) -> int | None:
        row=self.connection.execute("SELECT duration_minutes FROM location_connections WHERE origin_id=? AND destination_id=?",(origin,destination)).fetchone()
        return int(row[0]) if row else None

    def reachable_locations(self, origin: str) -> list[dict[str, Any]]:
        """Return deterministic movement affordances from one location."""
        rows = self.connection.execute(
            """
            SELECT l.id, l.name, c.duration_minutes
            FROM location_connections AS c
            JOIN locations AS l ON l.id = c.destination_id
            WHERE c.origin_id = ?
            ORDER BY l.id
            """,
            (origin,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_npc_action(
        self,
        action_id: str,
        character_id: str,
        location_id: str,
        label: str,
        description: str,
        preconditions: list[dict[str, Any]],
        effects: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO npc_actions(
                id, character_id, location_id, label, description,
                preconditions_json, effects_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id, character_id, location_id, label, description,
                json.dumps(preconditions, sort_keys=True),
                json.dumps(effects, sort_keys=True),
            ),
        )

    def add_npc_disclosure(
        self,
        disclosure_id: str,
        character_id: str,
        topic: str,
        request_terms: list[str],
        min_trust: float,
        content: str,
        belief: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO npc_disclosures(
                id, character_id, topic, request_terms_json, min_trust, content,
                subject_id, predicate, value_json, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                disclosure_id, character_id, topic,
                json.dumps(request_terms, sort_keys=True), min_trust, content,
                belief["subject_id"], belief["predicate"],
                json.dumps(belief["value"], sort_keys=True),
                belief.get("confidence", 1.0),
            ),
        )

    def disclosure_affordances(
        self, character_id: str, recipient_id: str
    ) -> list[dict[str, Any]]:
        relationship = self.connection.execute(
            "SELECT trust FROM relationships WHERE observer_id=? AND target_id=?",
            (character_id, recipient_id),
        ).fetchone()
        trust = float(relationship["trust"]) if relationship else 0.0
        rows = self.connection.execute(
            """
            SELECT id, topic, request_terms_json, min_trust
            FROM npc_disclosures WHERE character_id=? ORDER BY id
            """,
            (character_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "topic": row["topic"],
                "request_terms": json.loads(row["request_terms_json"]),
                "eligible": trust >= float(row["min_trust"]),
                "already_shared": self.connection.execute(
                    "SELECT 1 FROM disclosure_events WHERE disclosure_id=? AND recipient_id=?",
                    (row["id"], recipient_id),
                ).fetchone() is not None,
            }
            for row in rows
        ]

    def resolve_disclosure(
        self, character_id: str, recipient_id: str, disclosure_id: str
    ) -> tuple[bool, str]:
        row = self.connection.execute(
            "SELECT * FROM npc_disclosures WHERE id=? AND character_id=?",
            (disclosure_id, character_id),
        ).fetchone()
        if row is None:
            return False, "That information is unavailable."
        character = self.get_character(character_id)
        recipient = self.get_character(recipient_id)
        if (
            character is None
            or recipient is None
            or character["location_id"] != recipient["location_id"]
        ):
            return False, "That information cannot be shared at a distance."
        relationship = self.connection.execute(
            "SELECT trust FROM relationships WHERE observer_id=? AND target_id=?",
            (character_id, recipient_id),
        ).fetchone()
        trust = float(relationship["trust"]) if relationship else 0.0
        if trust < float(row["min_trust"]):
            return False, "I don't know you well enough to discuss that."
        self.connection.execute(
            "INSERT OR IGNORE INTO disclosure_events VALUES (?, ?, ?)",
            (disclosure_id, recipient_id, self.current_time),
        )
        self.set_belief(
            recipient_id, row["subject_id"], row["predicate"],
            json.loads(row["value_json"]), float(row["confidence"]),
            source_type="hearsay", source_character_id=character_id,
            detail=f"Disclosed by {character_id}: {row['topic']}",
        )
        return True, str(row["content"])

    def available_npc_actions(self, character_id: str) -> list[dict[str, Any]]:
        character = self.get_character(character_id)
        if character is None:
            return []
        rows = self.connection.execute(
            """
            SELECT * FROM npc_actions
            WHERE character_id=? AND location_id=? AND status='available'
            ORDER BY id
            """,
            (character_id, character["location_id"]),
        ).fetchall()
        actions: list[dict[str, Any]] = []
        for row in rows:
            preconditions = json.loads(row["preconditions_json"])
            if all(
                self.get_fact(item["subject_id"], item["predicate"])
                == item["equals"]
                for item in preconditions
            ):
                effects = json.loads(row["effects_json"])
                decision = self._evaluate_action_decision(
                    character_id, row["id"], effects.get("decision", {})
                )
                actions.append({
                    "id": row["id"],
                    "kind": "interact",
                    "label": row["label"],
                    "description": row["description"],
                    **decision,
                })
        return actions

    def _evaluate_action_decision(
        self, character_id: str, action_id: str, policy: dict[str, Any]
    ) -> dict[str, Any]:
        """Compute explainable utility from inspectable NPC state."""
        score = float(policy.get("base_score", 1.0))
        minimum = float(policy.get("minimum_score", 0.0))
        reasons = [f"Base score: {score:+.2f}."]
        for fragment in self.biography_fragments(character_id):
            if fragment["awareness"] != "conscious":
                continue
            for influence in fragment["action_influences"]:
                if influence["action_id"] != action_id:
                    continue
                authored_change = float(influence["score"])
                certainty = float(fragment["certainty"])
                change = authored_change * certainty
                score += change
                reasons.append(
                    f"{influence['reason']} ({change:+.2f}; canon "
                    f"{authored_change:+.2f} x certainty {certainty:.2f})."
                )
        parameters = self.npc_parameters(character_id)
        for factor in policy.get("parameter_factors", []):
            actual = parameters.get(factor["parameter"])
            expected = factor["value"]
            operator = factor.get("operator", "equals")
            matches = actual == expected
            if (
                operator in {"gte", "lte"}
                and isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and isinstance(expected, (int, float))
                and not isinstance(expected, bool)
            ):
                matches = actual >= expected if operator == "gte" else actual <= expected
            elif operator != "equals":
                matches = False
            if matches:
                change = float(factor["score"])
                score += change
                reasons.append(f"{factor['reason']} ({change:+.2f}).")
        memories = self.memories_for(character_id)
        for factor in policy.get("memory_factors", []):
            matches = any(
                memory["memory_kind"] == factor["memory_kind"]
                and (
                    "subject_id" not in factor
                    or memory["subject_id"] == factor["subject_id"]
                )
                and (
                    "source_character_id" not in factor
                    or memory["source_character_id"]
                    == factor["source_character_id"]
                )
                and float(memory["salience"])
                >= float(factor.get("min_salience", 0.0))
                and (
                    "within_minutes" not in factor
                    or self.current_time - int(memory["game_time"])
                    <= int(factor["within_minutes"])
                )
                for memory in memories
            )
            if matches:
                change = float(factor["score"])
                score += change
                reasons.append(f"{factor['reason']} ({change:+.2f}).")
        goals = {
            item["goal_id"]: item
            for item in self.goals_for(character_id, include_resolved=True)
        }
        for factor in policy.get("goal_factors", []):
            goal = goals.get(factor["goal_id"])
            if goal is not None and goal["status"] == factor.get("status", "active"):
                change = float(factor["score_per_priority"]) * int(goal["priority"])
                score += change
                reasons.append(f"{factor['reason']} ({change:+.2f}).")
        relationships = {
            item["target_id"]: item for item in self.relationships_for(character_id)
        }
        for factor in policy.get("relationship_factors", []):
            relationship = relationships.get(factor["target_id"])
            if relationship is None:
                continue
            actual = float(relationship[factor["metric"]])
            expected = float(factor["value"])
            operator = factor["operator"]
            matches = (
                actual >= expected if operator == "gte" else actual <= expected
            )
            if matches:
                change = float(factor["score"])
                score += change
                reasons.append(f"{factor['reason']} ({change:+.2f}).")
        beliefs = {
            (item["subject_id"], item["predicate"]): item
            for item in self.beliefs_for(character_id)
        }
        for factor in policy.get("belief_factors", []):
            belief = beliefs.get((factor["subject_id"], factor["predicate"]))
            if (
                belief is not None
                and belief["value"] == factor["equals"]
                and float(belief["confidence"])
                >= float(factor.get("min_confidence", 0.0))
            ):
                change = float(factor["score"])
                score += change
                reasons.append(f"{factor['reason']} ({change:+.2f}).")
        return {
            "decision_score": round(score, 4),
            "minimum_score": minimum,
            "autonomous_eligible": score >= minimum,
            "decision_reasons": reasons,
        }

    def available_social_actions(
        self, character_id: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Offer bounded, non-secret belief sharing with co-located NPCs."""
        speaker = self.get_character(character_id)
        if speaker is None or speaker["kind"] != "npc":
            return []
        actions: list[dict[str, Any]] = []
        beliefs = self.beliefs_for(character_id)
        targets = [
            item for item in self.characters_at(speaker["location_id"], kind="npc")
            if item["id"] != character_id
        ]
        for target in targets:
            target_beliefs = {
                (item["subject_id"], item["predicate"]): item
                for item in self.beliefs_for(target["id"])
            }
            for belief in beliefs:
                existing = target_beliefs.get(
                    (belief["subject_id"], belief["predicate"])
                )
                shared_confidence = self.hearsay_confidence(
                    target["id"], character_id, float(belief["confidence"])
                )["effective_confidence"]
                if (
                    existing is not None
                    and existing["value"] == belief["value"]
                    and float(existing["confidence"]) >= shared_confidence
                ):
                    continue
                actions.append({
                    "kind": "share_belief",
                    "target_id": target["id"],
                    "target_name": target["name"],
                    "subject_id": belief["subject_id"],
                    "predicate": belief["predicate"],
                })
                if len(actions) >= limit:
                    return actions
        return actions

    def share_belief(
        self,
        speaker_id: str,
        target_id: str,
        subject_id: str,
        predicate: str,
    ) -> tuple[bool, str]:
        """Validate and resolve one NPC-to-NPC information exchange."""
        speaker = self.get_character(speaker_id)
        target = self.get_character(target_id)
        if (
            speaker is None
            or target is None
            or speaker["kind"] != "npc"
            or target["kind"] != "npc"
            or speaker_id == target_id
            or not speaker["active"]
            or not target["active"]
            or speaker["location_id"] != target["location_id"]
        ):
            return False, "The social target is unavailable."
        belief = next(
            (
                item for item in self.beliefs_for(speaker_id)
                if item["subject_id"] == subject_id and item["predicate"] == predicate
            ),
            None,
        )
        if belief is None:
            return False, "The NPC does not hold that belief."
        existing = next(
            (
                item for item in self.beliefs_for(target_id)
                if item["subject_id"] == subject_id and item["predicate"] == predicate
            ),
            None,
        )
        calibration = self.hearsay_confidence(
            target_id, speaker_id, float(belief["confidence"])
        )
        confidence = calibration["effective_confidence"]
        if (
            existing is not None
            and existing["value"] == belief["value"]
            and float(existing["confidence"]) >= confidence
        ):
            return False, "The target already holds that belief."
        conflict = existing is not None and existing["value"] != belief["value"]
        self.set_belief(
            target_id, subject_id, predicate, belief["value"], confidence,
            source_type="hearsay", source_character_id=speaker_id,
            detail=f"Shared directly by {speaker['name']}.",
        )
        summary = (
            f"{speaker['name']} told {target['name']} that "
            f"{subject_id}.{predicate} is {belief['value']!r}."
        )
        for character_id in (speaker_id, target_id):
            self.add_memory(
                character_id, speaker["location_id"], "npc_conversation", summary,
                salience=0.6, subject_id=subject_id,
                source_character_id=speaker_id,
            )
        trust_delta, affinity_delta = ((-0.05, -0.03) if conflict else (0.04, 0.02))
        self.adjust_relationship(
            target_id, speaker_id, trust_delta, affinity_delta,
            "The NPC received conflicting information." if conflict
            else "The NPC received useful information.",
        )
        player = self.get_player()
        visible = player["location_id"] == speaker["location_id"]
        player_calibration = None
        if visible:
            player_calibration = self.hearsay_confidence(
                player["id"], speaker_id, float(belief["confidence"])
            )
            self.set_belief(
                player["id"], subject_id, predicate, belief["value"],
                player_calibration["effective_confidence"],
                source_type="hearsay", source_character_id=speaker_id,
                detail=f"Overheard {speaker['name']} speaking to {target['name']}.",
            )
        self.record(
            "npc_social_interaction", actor_id=speaker_id,
            input_data={
                "target_id": target_id, "subject_id": subject_id,
                "predicate": predicate,
            },
            result_data={
                "accepted": True,
                "conflict": conflict,
                "calibration": calibration,
                "player_calibration": player_calibration,
            },
        )
        message = f"{speaker['name']} tells {target['name']}: {subject_id}.{predicate} is {belief['value']!r}."
        return True, message if visible else ""

    def apply_npc_action(
        self,
        character_id: str,
        action_id: str,
        *,
        requested_by_player: bool = False,
        autonomous: bool = False,
    ) -> tuple[bool, str]:
        """Apply one predeclared interaction after rechecking every constraint."""
        row = self.connection.execute(
            "SELECT * FROM npc_actions WHERE id=? AND character_id=? AND status='available'",
            (action_id, character_id),
        ).fetchone()
        character = self.get_character(character_id)
        if row is None or character is None or character["location_id"] != row["location_id"]:
            return False, "Interaction is not currently available."
        preconditions = json.loads(row["preconditions_json"])
        if not all(
            self.get_fact(item["subject_id"], item["predicate"]) == item["equals"]
            for item in preconditions
        ):
            return False, "Interaction is not currently available."
        effects = json.loads(row["effects_json"])
        if autonomous:
            decision = self._evaluate_action_decision(
                character_id, action_id, effects.get("decision", {})
            )
            if not decision["autonomous_eligible"]:
                return False, "The NPC's current priorities weigh against that action."
        if requested_by_player and "player_trust_required" in effects:
            player = self.get_player()
            relationship = self.connection.execute(
                "SELECT trust FROM relationships WHERE observer_id=? AND target_id=?",
                (character_id, player["id"]),
            ).fetchone()
            trust = float(relationship["trust"]) if relationship else 0.0
            if trust < float(effects["player_trust_required"]):
                return False, f"{character['name']} does not trust you enough to do that."
        for fact in effects.get("facts", []):
            self.set_fact(
                fact["subject_id"], fact["predicate"], fact["value"],
                hidden=bool(fact.get("hidden", False)),
            )
        status = effects.get("simulation_status")
        if status is not None:
            if status not in {"active", "succeeded", "failed", "ended"}:
                raise ValueError(f"Invalid simulation status: {status}")
            self.connection.execute("UPDATE simulation SET status=? WHERE id=1", (status,))
        for observation in effects.get("observations", []):
            for witness in self.characters_at(row["location_id"], kind="npc"):
                self.add_memory(
                    witness["id"],
                    row["location_id"],
                    observation.get("kind", "witnessed_action"),
                    observation["summary"],
                    salience=float(observation.get("salience", 0.5)),
                    subject_id=observation.get("subject_id"),
                    source_character_id=character_id,
                )
        for goal_effect in effects.get("goals", []):
            self.set_goal_status(
                goal_effect["character_id"], goal_effect["goal_id"],
                goal_effect["status"], goal_effect["reason"],
                source_action_id=action_id,
            )
        for relationship_effect in effects.get("relationships", []):
            if relationship_effect.get("when_requested_by_player", False) and not (
                requested_by_player
            ):
                continue
            self.apply_action_relationship_change(
                relationship_effect["observer_id"],
                relationship_effect["target_id"],
                float(relationship_effect.get("trust_delta", 0.0)),
                float(relationship_effect.get("affinity_delta", 0.0)),
                relationship_effect["reason"],
                action_id,
            )
        for cancellation_key in effects.get("cancel_events", []):
            self.cancel_events(cancellation_key)
        for scheduled in effects.get("scheduled_events", []):
            self.schedule_event(
                scheduled["type"],
                self.current_time + int(scheduled["delay_minutes"]),
                scheduled.get("payload", {}),
                scheduled.get("cancellation_key"),
            )
        self.connection.execute("UPDATE npc_actions SET status='used' WHERE id=?", (action_id,))
        return True, str(effects.get("visible_message", f"{row['label']} is complete."))

    def add_memory(
        self,
        character_id: str,
        location_id: str,
        memory_kind: str,
        summary: str,
        *,
        salience: float = 0.5,
        subject_id: str | None = None,
        source_character_id: str | None = None,
    ) -> int:
        if not summary.strip():
            raise ValueError("Memory summary cannot be empty")
        if not 0 <= salience <= 1:
            raise ValueError("Memory salience must be between zero and one")
        cursor = self.connection.execute(
            """
            INSERT INTO memories(
                character_id, game_time, location_id, memory_kind, subject_id,
                summary, salience, source_character_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character_id, self.current_time, location_id, memory_kind,
                subject_id, summary, salience, source_character_id,
            ),
        )
        return int(cursor.lastrowid)

    def queue_reaction(
        self,
        npc_id: str,
        trigger_kind: str,
        summary: str,
        *,
        salience: float = 0.5,
        source_character_id: str | None = None,
        lifetime_minutes: int = 10,
    ) -> int:
        npc = self.get_character(npc_id)
        if npc is None or npc["kind"] != "npc":
            raise ValueError(f"Reaction cues require an NPC: {npc_id}")
        if not trigger_kind.strip() or not summary.strip():
            raise ValueError("Reaction cues require a kind and summary")
        if not 0 <= salience <= 1 or lifetime_minutes < 1:
            raise ValueError("Invalid reaction cue salience or lifetime")
        cursor = self.connection.execute(
            "INSERT INTO npc_reaction_cues("
            "npc_id,created_time,expires_at,trigger_kind,summary,salience,"
            "source_character_id) VALUES (?,?,?,?,?,?,?)",
            (
                npc_id, self.current_time, self.current_time + lifetime_minutes,
                trigger_kind, summary, salience, source_character_id,
            ),
        )
        player = self.get_player()
        if player["location_id"] == npc["location_id"]:
            self.expedite_decision_event(npc_id)
        return int(cursor.lastrowid)

    def available_speech_actions(self, npc_id: str) -> list[dict[str, Any]]:
        npc = self.get_character(npc_id)
        player = self.get_player()
        if npc is None or npc["location_id"] != player["location_id"]:
            return []
        parameters = self.npc_parameters(npc_id)
        cooldown = parameters.get("proactive_speech_cooldown_minutes", 3)
        if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 1:
            cooldown = 3
        last = self.connection.execute(
            "SELECT MAX(spoken_at) AS spoken_at FROM npc_reaction_cues "
            "WHERE npc_id=? AND status='spoken'",
            (npc_id,),
        ).fetchone()["spoken_at"]
        if last is not None and self.current_time - int(last) < cooldown:
            return []
        rows = self.connection.execute(
            "SELECT id,trigger_kind,summary,salience,source_character_id "
            "FROM npc_reaction_cues WHERE npc_id=? AND status='pending' "
            "AND expires_at>=? ORDER BY salience DESC,created_time DESC,id DESC LIMIT 3",
            (npc_id, self.current_time),
        ).fetchall()
        return [
            {
                "kind": "speak",
                "cue_id": int(row["id"]),
                "trigger_kind": row["trigger_kind"],
                "summary": row["summary"],
                "salience": float(row["salience"]),
                "source_character_id": row["source_character_id"],
            }
            for row in rows
        ]

    def pending_goal_reaction_ids(self, npc_id: str) -> list[int]:
        return [int(row["id"]) for row in self.connection.execute(
            "SELECT id FROM npc_reaction_cues WHERE npc_id=? "
            "AND trigger_kind='goal_resolved' AND status='pending' AND expires_at>=?",
            (npc_id, self.current_time),
        )]

    def cover_goal_reactions(self, npc_id: str, cue_ids: list[int]) -> None:
        """Retire previously queued goal recaps covered by a direct conversation."""
        covered = []
        for cue_id in cue_ids:
            cursor = self.connection.execute(
                "UPDATE npc_reaction_cues SET status='expired' WHERE id=? AND npc_id=? "
                "AND trigger_kind='goal_resolved' AND status='pending'",
                (cue_id, npc_id),
            )
            if cursor.rowcount:
                covered.append(cue_id)
        if covered:
            self.record("npc_reactions_covered", actor_id=npc_id,
                        input_data={"cue_ids": covered},
                        result_data={"reason": "Discussed the aftermath directly with the player."})

    def speak_reaction(
        self, npc_id: str, cue_id: int, utterance: str
    ) -> tuple[bool, str]:
        available = {
            item["cue_id"]: item for item in self.available_speech_actions(npc_id)
        }
        cue = available.get(cue_id)
        npc = self.get_character(npc_id)
        if cue is None or npc is None:
            return False, "That reaction is no longer available."
        spoken = " ".join(utterance.split())
        if not spoken or len(spoken) > 500:
            return False, "The proposed speech is invalid."
        self.connection.execute(
            "UPDATE npc_reaction_cues SET status='spoken',spoken_at=?,utterance=? "
            "WHERE id=? AND npc_id=? AND status='pending'",
            (self.current_time, spoken, cue_id, npc_id),
        )
        self.append_dialogue(npc_id, npc_id, spoken)
        for witness in self.characters_at(npc["location_id"]):
            if witness["id"] == npc_id:
                continue
            self.add_memory(
                witness["id"], npc["location_id"], "overheard_dialogue",
                f'{npc["name"]} said: "{spoken}"',
                salience=float(cue["salience"]),
                source_character_id=npc_id,
            )
        self.record(
            "npc_spoke",
            actor_id=npc_id,
            input_data={"cue_id": cue_id, "trigger_kind": cue["trigger_kind"]},
            result_data={"utterance": spoken},
        )
        remaining = self.connection.execute(
            "SELECT 1 FROM npc_reaction_cues WHERE npc_id=? AND status='pending' "
            "AND expires_at>=? LIMIT 1",
            (npc_id, self.current_time),
        ).fetchone()
        if remaining is not None:
            parameters = self.npc_parameters(npc_id)
            cooldown = parameters.get("proactive_speech_cooldown_minutes", 3)
            if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 1:
                cooldown = 3
            self.schedule_event(
                "npc_decision", self.current_time + cooldown,
                {"npc_id": npc_id, "reaction_only": True},
            )
        return True, f'{npc["name"]}: "{spoken}"'

    def queue_arrival_reactions(self, character_id: str, location_id: str) -> None:
        actor = self.get_character(character_id)
        location = self.get_location(location_id)
        if actor is None or location is None:
            return
        for npc in self.characters_at(location_id, kind="npc"):
            if npc["id"] == character_id:
                continue
            self.queue_reaction(
                npc["id"], "character_arrived",
                f"{actor['name']} arrived in {location['name']}.",
                salience=0.5, source_character_id=character_id,
                lifetime_minutes=5,
            )

    def expedite_decision_event(self, npc_id: str) -> None:
        parameters = self.npc_parameters(npc_id)
        cooldown = parameters.get("proactive_speech_cooldown_minutes", 3)
        if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 1:
            cooldown = 3
        last_spoken = self.connection.execute(
            "SELECT MAX(spoken_at) AS spoken_at FROM npc_reaction_cues "
            "WHERE npc_id=? AND status='spoken'",
            (npc_id,),
        ).fetchone()["spoken_at"]
        due_time = self.current_time
        if last_spoken is not None:
            due_time = max(due_time, int(last_spoken) + cooldown)
        row = self.connection.execute(
            "SELECT id,due_time FROM scheduled_events "
            "WHERE event_type='npc_decision' AND status='pending' "
            "AND json_extract(payload_json,'$.npc_id')=? "
            "AND json_extract(payload_json,'$.reaction_only')=1 LIMIT 1",
            (npc_id,),
        ).fetchone()
        if row is None:
            self.schedule_event(
                "npc_decision", due_time,
                {"npc_id": npc_id, "reaction_only": True},
            )
        elif int(row["due_time"]) > due_time:
            self.connection.execute(
                "UPDATE scheduled_events SET due_time=? WHERE id=?",
                (due_time, int(row["id"])),
            )

    def memories_for(
        self, character_id: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Return a bounded deterministic view: salient first, newest on ties."""
        rows = self.connection.execute(
            """
            SELECT id, game_time, location_id, memory_kind, subject_id,
                   summary, salience, source_character_id
            FROM memories
            WHERE character_id=?
            ORDER BY salience DESC, game_time DESC, id DESC
            LIMIT ?
            """,
            (character_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_relationship(
        self,
        observer_id: str,
        target_id: str,
        trust: float,
        affinity: float,
        *,
        reason: str = "Scenario-defined starting relationship.",
    ) -> None:
        if (
            not isinstance(trust, (int, float))
            or isinstance(trust, bool)
            or not isinstance(affinity, (int, float))
            or isinstance(affinity, bool)
        ):
            raise ValueError("Relationship values must be numeric")
        if observer_id == target_id:
            raise ValueError("A character cannot have a relationship with itself")
        if not -1 <= trust <= 1 or not -1 <= affinity <= 1:
            raise ValueError("Relationship values must be between -1 and 1")
        if self.get_character(observer_id) is None or self.get_character(target_id) is None:
            raise ValueError("Relationships require known characters")
        self.connection.execute(
            """
            INSERT INTO relationships(
                observer_id, target_id, trust, affinity, updated_at_game_time
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(observer_id, target_id) DO UPDATE SET
                trust=excluded.trust,
                affinity=excluded.affinity,
                updated_at_game_time=excluded.updated_at_game_time
            """,
            (observer_id, target_id, trust, affinity, self.current_time),
        )
        self.connection.execute(
            """
            INSERT INTO relationship_events(
                observer_id, target_id, game_time, source_type,
                trust_delta, affinity_delta, reason
            ) VALUES (?, ?, ?, 'initial', ?, ?, ?)
            """,
            (observer_id, target_id, self.current_time, trust, affinity, reason),
        )

    def adjust_relationship(
        self,
        observer_id: str,
        target_id: str,
        trust_delta: float,
        affinity_delta: float,
        reason: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(trust_delta, (int, float))
            or isinstance(trust_delta, bool)
            or not isinstance(affinity_delta, (int, float))
            or isinstance(affinity_delta, bool)
        ):
            raise ValueError("Relationship changes must be numeric")
        if not -0.25 <= trust_delta <= 0.25 or not -0.25 <= affinity_delta <= 0.25:
            raise ValueError("Relationship changes are limited to 0.25 per exchange")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Relationship changes require a reason")
        row = self.connection.execute(
            "SELECT trust, affinity FROM relationships WHERE observer_id=? AND target_id=?",
            (observer_id, target_id),
        ).fetchone()
        if row is None:
            if observer_id == target_id:
                raise ValueError("A character cannot have a relationship with itself")
            if self.get_character(observer_id) is None or self.get_character(target_id) is None:
                raise ValueError("Relationships require known characters")
            self.connection.execute(
                """
                INSERT INTO relationships(
                    observer_id, target_id, trust, affinity, updated_at_game_time
                ) VALUES (?, ?, 0, 0, ?)
                """,
                (observer_id, target_id, self.current_time),
            )
            row = self.connection.execute(
                "SELECT trust, affinity FROM relationships WHERE observer_id=? AND target_id=?",
                (observer_id, target_id),
            ).fetchone()
        trust = max(-1.0, min(1.0, float(row["trust"]) + trust_delta))
        affinity = max(-1.0, min(1.0, float(row["affinity"]) + affinity_delta))
        self.connection.execute(
            """
            UPDATE relationships
            SET trust=?, affinity=?, updated_at_game_time=?
            WHERE observer_id=? AND target_id=?
            """,
            (trust, affinity, self.current_time, observer_id, target_id),
        )
        self.connection.execute(
            """
            INSERT INTO relationship_events(
                observer_id, target_id, game_time, source_type,
                trust_delta, affinity_delta, reason
            ) VALUES (?, ?, ?, 'dialogue', ?, ?, ?)
            """,
            (
                observer_id, target_id, self.current_time,
                trust_delta, affinity_delta, reason,
            ),
        )
        return {"trust": trust, "affinity": affinity}

    def hearsay_confidence(
        self, listener_id: str, source_id: str, claimed_confidence: float
    ) -> dict[str, float]:
        """Calibrate a claim using the listener's directional trust in its source."""
        if (
            not isinstance(claimed_confidence, (int, float))
            or isinstance(claimed_confidence, bool)
            or not 0 <= claimed_confidence <= 1
        ):
            raise ValueError("Claimed confidence must be between zero and one")
        relationship = self.connection.execute(
            "SELECT trust FROM relationships WHERE observer_id=? AND target_id=?",
            (listener_id, source_id),
        ).fetchone()
        trust = float(relationship["trust"]) if relationship else 0.0
        credibility = 0.625 + (0.375 * trust)
        return {
            "claimed_confidence": float(claimed_confidence),
            "source_trust": trust,
            "credibility": credibility,
            "effective_confidence": float(claimed_confidence) * credibility,
        }

    def apply_action_relationship_change(
        self,
        observer_id: str,
        target_id: str,
        trust_delta: float,
        affinity_delta: float,
        reason: str,
        action_id: str,
    ) -> dict[str, float]:
        """Apply a validated consequence without mislabeling it as dialogue."""
        if not -1 <= trust_delta <= 1 or not -1 <= affinity_delta <= 1:
            raise ValueError("Action relationship changes must be between -1 and 1")
        if observer_id == target_id:
            raise ValueError("A character cannot have a relationship with itself")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Action relationship changes require a reason")
        row = self.connection.execute(
            "SELECT trust,affinity FROM relationships "
            "WHERE observer_id=? AND target_id=?",
            (observer_id, target_id),
        ).fetchone()
        if row is None:
            if self.get_character(observer_id) is None or self.get_character(target_id) is None:
                raise ValueError("Relationships require known characters")
            self.connection.execute(
                "INSERT INTO relationships("
                "observer_id,target_id,trust,affinity,updated_at_game_time"
                ") VALUES (?,?,0,0,?)",
                (observer_id, target_id, self.current_time),
            )
            trust = trust_delta
            affinity = affinity_delta
        else:
            trust = float(row["trust"]) + trust_delta
            affinity = float(row["affinity"]) + affinity_delta
        trust = max(-1.0, min(1.0, trust))
        affinity = max(-1.0, min(1.0, affinity))
        self.connection.execute(
            "UPDATE relationships SET trust=?,affinity=?,updated_at_game_time=? "
            "WHERE observer_id=? AND target_id=?",
            (trust, affinity, self.current_time, observer_id, target_id),
        )
        self.connection.execute(
            "INSERT INTO relationship_action_events("
            "observer_id,target_id,game_time,trust_delta,affinity_delta,"
            "reason,source_action_id) VALUES (?,?,?,?,?,?,?)",
            (
                observer_id, target_id, self.current_time, trust_delta,
                affinity_delta, reason, action_id,
            ),
        )
        if abs(trust_delta) >= 0.15 or abs(affinity_delta) >= 0.15:
            self.queue_reaction(
                observer_id, "relationship_changed", reason,
                salience=0.8, source_character_id=target_id,
            )
        return {"trust": trust, "affinity": affinity}

    def relationships_for(self, observer_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT r.target_id, c.name AS target_name, r.trust, r.affinity,
                   r.updated_at_game_time
            FROM relationships AS r
            JOIN characters AS c ON c.id = r.target_id
            WHERE r.observer_id=?
            ORDER BY r.target_id
            """,
            (observer_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            events = self.connection.execute(
                """
                SELECT game_time,source_type,trust_delta,affinity_delta,reason,
                       source_action_id
                FROM (
                    SELECT game_time,source_type,trust_delta,affinity_delta,reason,
                           NULL AS source_action_id,id AS event_id,0 AS event_table
                    FROM relationship_events
                    WHERE observer_id=? AND target_id=?
                    UNION ALL
                    SELECT game_time,'action',trust_delta,affinity_delta,reason,
                           source_action_id,id AS event_id,1 AS event_table
                    FROM relationship_action_events
                    WHERE observer_id=? AND target_id=?
                )
                ORDER BY game_time DESC,event_table DESC,event_id DESC LIMIT 5
                """,
                (
                    observer_id, row["target_id"],
                    observer_id, row["target_id"],
                ),
            ).fetchall()
            item["recent_changes"] = [dict(event) for event in reversed(events)]
            result.append(item)
        return result

    def pending_activity(self, character_id: str) -> dict[str, Any] | None:
        row=self.connection.execute("SELECT * FROM character_activities WHERE character_id=? AND status='pending'",(character_id,)).fetchone()
        return dict(row) if row else None

    def add_goal(
        self, character_id: str, goal_id: str, description: str, priority: int = 1
    ) -> None:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO npc_goals("
            "character_id,goal_id,description,priority,status,active"
            ") VALUES (?,?,?,?,'active',1)",
            (character_id, goal_id, description, priority),
        )
        if cursor.rowcount:
            goal_row = self.connection.execute(
                "SELECT id FROM npc_goals WHERE character_id=? AND goal_id=?",
                (character_id, goal_id),
            ).fetchone()
            self.connection.execute(
                "INSERT INTO npc_goal_events("
                "goal_row_id,game_time,status,reason"
                ") VALUES (?,?,'active','Scenario-defined starting goal.')",
                (goal_row["id"], self.current_time),
            )

    def goals_for(
        self, character_id: str, *, include_resolved: bool = False
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id,goal_id,description,priority,status FROM npc_goals "
            "WHERE character_id=?"
        )
        if not include_resolved:
            sql += " AND status='active'"
        sql += " ORDER BY priority DESC,id"
        rows = self.connection.execute(sql, (character_id,)).fetchall()
        result = []
        for row in rows:
            goal = dict(row)
            goal["history"] = [dict(event) for event in self.connection.execute(
                "SELECT game_time,status,reason,source_action_id "
                "FROM npc_goal_events WHERE goal_row_id=? ORDER BY id",
                (row["id"],),
            ).fetchall()]
            result.append(goal)
        return result

    def set_goal_status(
        self,
        character_id: str,
        goal_id: str,
        status: str,
        reason: str,
        *,
        source_action_id: str | None = None,
    ) -> None:
        if status not in {"active", "completed", "failed", "abandoned"}:
            raise ValueError(f"Invalid goal status: {status}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Goal status changes require a reason")
        row = self.connection.execute(
            "SELECT id,status FROM npc_goals WHERE character_id=? AND goal_id=?",
            (character_id, goal_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown goal: {character_id}.{goal_id}")
        if row["status"] == status:
            return
        self.connection.execute(
            "UPDATE npc_goals SET status=?,active=? WHERE id=?",
            (status, int(status == "active"), row["id"]),
        )
        self.connection.execute(
            "INSERT INTO npc_goal_events("
            "goal_row_id,game_time,status,reason,source_action_id"
            ") VALUES (?,?,?,?,?)",
            (row["id"], self.current_time, status, reason, source_action_id),
        )
        if status != "active":
            self.queue_reaction(
                character_id, "goal_resolved", reason,
                salience=0.9, lifetime_minutes=10,
            )

    def ensure_decision_event(self, character_id: str, due_time: int) -> None:
        exists = self.connection.execute(
            "SELECT 1 FROM scheduled_events WHERE event_type='npc_decision' AND status='pending' AND json_extract(payload_json,'$.npc_id')=?",
            (character_id,),
        ).fetchone()
        if not exists:
            self.schedule_event("npc_decision", due_time, {"npc_id": character_id})

    def due_decision_events(self, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM scheduled_events WHERE event_type='npc_decision' AND status='pending' AND due_time<=? ORDER BY due_time,id LIMIT ?",
            (self.current_time, limit),
        ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def create_travel(self, character_id: str, destination: str, minutes: int) -> dict[str, Any]:
        actor=self.get_character(character_id); assert actor
        due=self.current_time+minutes
        cur=self.connection.execute("INSERT INTO character_activities(character_id,kind,origin_id,destination_id,started_at,due_time) VALUES (?,'travel',?,?,?,?)",(character_id,actor['location_id'],destination,self.current_time,due))
        activity_id=int(cur.lastrowid)
        event_id=self.schedule_event("character_arrival",due,{"activity_id":activity_id,"character_id":character_id,"destination_id":destination})
        self.connection.execute("UPDATE character_activities SET event_id=? WHERE id=?",(event_id,activity_id))
        return {"id":activity_id,"due_time":due,"event_id":event_id}

    def cancel_activity(self, activity_id: int) -> None:
        row=self.connection.execute("SELECT event_id FROM character_activities WHERE id=? AND status='pending'",(activity_id,)).fetchone()
        if row:
            self.connection.execute("UPDATE character_activities SET status='cancelled' WHERE id=?",(activity_id,))
            self.connection.execute("UPDATE scheduled_events SET status='cancelled' WHERE id=?",(row['event_id'],))

    def add_character(
        self, character_id: str, kind: str, name: str, location_id: str
    ) -> None:
        self.connection.execute(
            "INSERT INTO characters(id, kind, name, location_id) VALUES (?, ?, ?, ?)",
            (character_id, kind, name, location_id),
        )

    def set_npc_parameters(self, character_id: str, parameters: dict[str, Any]) -> None:
        character = self.get_character(character_id)
        if character is None or character["kind"] != "npc":
            raise ValueError(f"NPC parameters require an NPC: {character_id}")
        self.connection.execute(
            """
            INSERT INTO npc_parameters(character_id, parameters_json) VALUES (?, ?)
            ON CONFLICT(character_id) DO UPDATE SET parameters_json=excluded.parameters_json
            """,
            (character_id, json.dumps(parameters, sort_keys=True)),
        )

    def npc_parameters(self, character_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT parameters_json FROM npc_parameters WHERE character_id=?",
            (character_id,),
        ).fetchone()
        return json.loads(row["parameters_json"]) if row else {}

    def find_npc(self, reference: str) -> dict[str, Any] | None:
        normalized = reference.strip().lower()
        rows = self.connection.execute(
            "SELECT * FROM characters WHERE kind='npc' ORDER BY id"
        ).fetchall()
        for row in rows:
            if normalized in {row["id"].lower(), row["name"].lower()}:
                return dict(row)
        return None

    def list_npcs(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM characters WHERE kind='npc' ORDER BY name, id"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_character(self, character_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_player(self) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM characters WHERE kind = 'player'"
        ).fetchone()
        if row is None:
            raise RuntimeError("Simulation has no player")
        return dict(row)

    def characters_at(
        self, location_id: str, *, kind: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM characters WHERE location_id = ? AND active = 1"
        parameters: list[Any] = [location_id]
        if kind is not None:
            sql += " AND kind = ?"
            parameters.append(kind)
        sql += " ORDER BY id"
        rows = self.connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def resolve_npc_at_player_location(
        self, reference: str
    ) -> tuple[dict[str, Any], str] | None:
        """Resolve an NPC name at the player's location and return trailing text."""
        player = self.get_player()
        candidate = reference.strip()
        if candidate.lower().startswith("the "):
            candidate = candidate[4:]
        for npc in self.characters_at(player["location_id"], kind="npc"):
            name = npc["name"]
            if candidate.lower() == name.lower():
                return npc, ""
            prefix = f"{name.lower()} "
            if candidate.lower().startswith(prefix):
                return npc, candidate[len(name) :].strip()
        return None

    def get_location(self, location_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM locations WHERE id = ?", (location_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_locations(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM locations ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def resolve_location(self, reference: str) -> dict[str, Any] | None:
        """Resolve a player-facing location name without fuzzy guessing."""
        normalized = " ".join(reference.lower().replace("_", " ").split())
        for location in self.list_locations():
            candidates = {
                " ".join(location["id"].lower().replace("_", " ").split()),
                " ".join(location["name"].lower().split()),
            }
            if normalized in candidates:
                return location
        return None

    def describe_subject(self, reference: str) -> tuple[str, str] | None:
        """Resolve an examinable subject and its objective description."""
        normalized = " ".join(reference.lower().replace("_", " ").split())
        rows = self.connection.execute(
            "SELECT subject_id, value_json FROM facts WHERE predicate = 'description'"
        ).fetchall()
        for row in rows:
            subject_name = " ".join(row["subject_id"].lower().replace("_", " ").split())
            if normalized == subject_name:
                return row["subject_id"], str(json.loads(row["value_json"]))
        return None

    def examinable_subject_ids(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT subject_id FROM facts "
            "WHERE predicate = 'description' AND hidden = 0 ORDER BY subject_id"
        ).fetchall()
        return [str(row["subject_id"]) for row in rows]

    def move_character(self, character_id: str, location_id: str) -> None:
        cursor = self.connection.execute(
            "UPDATE characters SET location_id = ? WHERE id = ? AND active = 1",
            (location_id, character_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Unknown or inactive character: {character_id}")

    def set_fact(
        self, subject_id: str, predicate: str, value: Any, *, hidden: bool = False
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO facts(subject_id, predicate, value_json, hidden)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(subject_id, predicate) DO UPDATE SET
                value_json = excluded.value_json,
                hidden = excluded.hidden
            """,
            (subject_id, predicate, json.dumps(value), int(hidden)),
        )

    def get_fact(self, subject_id: str, predicate: str) -> Any | None:
        row = self.connection.execute(
            "SELECT value_json FROM facts WHERE subject_id = ? AND predicate = ?",
            (subject_id, predicate),
        ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def set_belief(
        self,
        character_id: str,
        subject_id: str,
        predicate: str,
        value: Any,
        confidence: float = 1.0,
        *,
        source_type: str = "inference",
        source_character_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        valid_sources = {"initial", "direct", "hearsay", "inference", "legacy"}
        if source_type not in valid_sources:
            raise ValueError(f"Unknown belief evidence source: {source_type}")
        if not 0 <= confidence <= 1:
            raise ValueError("Belief confidence must be between zero and one")

        serialized = json.dumps(value, sort_keys=True)
        belief = self.connection.execute(
            """
            SELECT * FROM beliefs
            WHERE character_id = ? AND subject_id = ? AND predicate = ?
            """,
            (character_id, subject_id, predicate),
        ).fetchone()
        if belief is None:
            cursor = self.connection.execute(
                """
                INSERT INTO beliefs(
                    character_id, subject_id, predicate, value_json,
                    confidence, updated_at_game_time
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    character_id,
                    subject_id,
                    predicate,
                    serialized,
                    confidence,
                    self.current_time,
                ),
            )
            belief_id = int(cursor.lastrowid)
        else:
            belief_id = int(belief["id"])
            evidence_count = self.connection.execute(
                "SELECT COUNT(*) AS count FROM belief_evidence WHERE belief_id = ?",
                (belief_id,),
            ).fetchone()["count"]
            if evidence_count == 0:
                self.connection.execute(
                    """
                    INSERT INTO belief_evidence(
                        belief_id, source_type, value_json, confidence, detail, game_time
                    ) VALUES (?, 'legacy', ?, ?, ?, ?)
                    """,
                    (
                        belief_id,
                        belief["value_json"],
                        belief["confidence"],
                        "Belief predates evidence tracking.",
                        belief["updated_at_game_time"],
                    ),
                )

        self.connection.execute(
            """
            INSERT INTO belief_evidence(
                belief_id, source_type, source_character_id,
                value_json, confidence, detail, game_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                belief_id,
                source_type,
                source_character_id,
                serialized,
                confidence,
                detail,
                self.current_time,
            ),
        )
        self._resolve_belief(belief_id)

    def _resolve_belief(self, belief_id: int) -> None:
        """Resolve contradictory evidence by score, then most-recent evidence."""
        rows = self.connection.execute(
            """
            SELECT id, value_json, confidence FROM belief_evidence
            WHERE belief_id = ? ORDER BY id
            """,
            (belief_id,),
        ).fetchall()
        candidates: dict[str, dict[str, float | int]] = {}
        for row in rows:
            candidate = candidates.setdefault(
                row["value_json"], {"score": 0.0, "latest_id": 0}
            )
            candidate["score"] = float(candidate["score"]) + float(row["confidence"])
            candidate["latest_id"] = int(row["id"])
        winning_value, winner = max(
            candidates.items(),
            key=lambda item: (float(item[1]["score"]), int(item[1]["latest_id"])),
        )
        opposition = sum(
            float(candidate["score"])
            for value, candidate in candidates.items()
            if value != winning_value
        )
        resolved_confidence = min(1.0, float(winner["score"]) / (1.0 + opposition))
        self.connection.execute(
            """
            UPDATE beliefs
            SET value_json = ?, confidence = ?, updated_at_game_time = ?
            WHERE id = ?
            """,
            (winning_value, resolved_confidence, self.current_time, belief_id),
        )

    def belief_evidence(self, belief_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, source_type, source_character_id, value_json,
                   confidence, detail, game_time
            FROM belief_evidence WHERE belief_id = ? ORDER BY id
            """,
            (belief_id,),
        ).fetchall()
        evidence: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["value"] = json.loads(item.pop("value_json"))
            evidence.append(item)
        return evidence

    def beliefs_for(self, character_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, subject_id, predicate, value_json, confidence, updated_at_game_time
            FROM beliefs WHERE character_id = ? ORDER BY id
            """,
            (character_id,),
        ).fetchall()
        beliefs: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["value"] = json.loads(item.pop("value_json"))
            item["evidence"] = self.belief_evidence(int(item["id"]))
            beliefs.append(item)
        return beliefs

    def append_dialogue(self, npc_id: str, speaker_id: str, text: str) -> None:
        if not text.strip():
            raise ValueError("Dialogue text cannot be empty")
        self.connection.execute(
            "INSERT INTO dialogue(game_time, npc_id, speaker_id, text) VALUES (?, ?, ?, ?)",
            (self.current_time, npc_id, speaker_id, text),
        )

    def recent_dialogue(self, npc_id: str, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT d.game_time, d.speaker_id, c.name AS speaker_name, d.text
            FROM dialogue AS d
            JOIN characters AS c ON c.id = d.speaker_id
            WHERE d.npc_id = ?
            ORDER BY d.id DESC LIMIT ?
            """,
            (npc_id, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def schedule_event(
        self,
        event_type: str,
        due_time: int,
        payload: dict[str, Any],
        cancellation_key: str | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO scheduled_events(event_type, due_time, payload_json, cancellation_key)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, due_time, json.dumps(payload), cancellation_key),
        )
        return int(cursor.lastrowid)

    def cancel_events(self, cancellation_key: str) -> int:
        cursor = self.connection.execute(
            "UPDATE scheduled_events SET status='cancelled' "
            "WHERE cancellation_key=? AND status='pending'",
            (cancellation_key,),
        )
        return int(cursor.rowcount)

    def due_events(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM scheduled_events
            WHERE status = 'pending' AND due_time <= ?
            ORDER BY due_time, id
            """,
            (self.current_time,),
        ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def mark_event_processed(self, event_id: int) -> None:
        self.connection.execute(
            "UPDATE scheduled_events SET status = 'processed' WHERE id = ? AND status = 'pending'",
            (event_id,),
        )

    def record(
        self,
        record_type: str,
        *,
        actor_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        result_data: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO history(game_time, record_type, actor_id, input_json, result_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                self.current_time,
                record_type,
                actor_id,
                json.dumps(input_data or {}),
                json.dumps(result_data or {}),
            ),
        )
