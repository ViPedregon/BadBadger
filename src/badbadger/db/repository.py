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

    def add_character(
        self, character_id: str, kind: str, name: str, location_id: str
    ) -> None:
        self.connection.execute(
            "INSERT INTO characters(id, kind, name, location_id) VALUES (?, ?, ?, ?)",
            (character_id, kind, name, location_id),
        )

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

    def get_location(self, location_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM locations WHERE id = ?", (location_id,)
        ).fetchone()
        return dict(row) if row else None

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
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO beliefs(
                character_id, subject_id, predicate, value_json,
                confidence, updated_at_game_time
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id, subject_id, predicate) DO UPDATE SET
                value_json = excluded.value_json,
                confidence = excluded.confidence,
                updated_at_game_time = excluded.updated_at_game_time
            """,
            (
                character_id,
                subject_id,
                predicate,
                json.dumps(value),
                confidence,
                self.current_time,
            ),
        )

    def beliefs_for(self, character_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT subject_id, predicate, value_json, confidence, updated_at_game_time
            FROM beliefs WHERE character_id = ? ORDER BY id
            """,
            (character_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "value": json.loads(row["value_json"]),
            }
            for row in rows
        ]

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
