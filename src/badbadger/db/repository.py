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

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "GameRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit a complete engine operation or roll it back as a unit."""
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def create_schema(self) -> None:
        schema = files("badbadger.db").joinpath("schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
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

    def add_connection(self, origin: str, destination: str, minutes: int) -> None:
        self.connection.execute("INSERT OR IGNORE INTO location_connections VALUES (?,?,?)", (origin,destination,minutes))

    def connection_duration(self, origin: str, destination: str) -> int | None:
        row=self.connection.execute("SELECT duration_minutes FROM location_connections WHERE origin_id=? AND destination_id=?",(origin,destination)).fetchone()
        return int(row[0]) if row else None

    def pending_activity(self, character_id: str) -> dict[str, Any] | None:
        row=self.connection.execute("SELECT * FROM character_activities WHERE character_id=? AND status='pending'",(character_id,)).fetchone()
        return dict(row) if row else None

    def add_goal(self, character_id: str, description: str, priority: int = 1) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO npc_goals(character_id,description,priority) VALUES (?,?,?)",
            (character_id, description, priority),
        )

    def goals_for(self, character_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT description,priority FROM npc_goals WHERE character_id=? AND active=1 ORDER BY priority DESC,id",
            (character_id,),
        ).fetchall()
        return [dict(row) for row in rows]

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
            "SELECT subject_id FROM facts WHERE predicate = 'description' ORDER BY subject_id"
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
