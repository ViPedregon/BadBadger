"""Player-facing Relay Crisis boundary with engine-validated AI proposals.

Construct once at startup with a server-owned path. Each call owns its SQLite
connection; BEGIN IMMEDIATE serializes turns even across service instances.
No network listener, authentication, or client-controlled save paths live here.
"""

from __future__ import annotations

import json
from importlib.resources import as_file, files
from pathlib import Path

from badbadger.application import GameApplication, open_prototype
from badbadger.config import build_configured_intent_interpreter, build_configured_npc_backend
from badbadger.db.repository import GameRepository
from badbadger.engine.simulation import SimulationEngine


class TurnConflict(ValueError):
    """The request id was reused or the client viewed an older revision."""


class RelayService:
    """Persist complete turns, their public transcript, and replayable receipts."""

    def __init__(self, database: str | Path, *, offline: bool = True) -> None:
        self.npc_backend, self.backend_label = build_configured_npc_backend(offline=offline)
        self.intent_interpreter, self.intent_label = build_configured_intent_interpreter(offline=offline)
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
        with as_file(resource) as scenario:
            app = open_prototype(self.database, scenario=scenario, developer_mode=False)
        try:
            repository = app.repository
            with as_file(resource) as scenario:
                expected = json.loads(scenario.read_text())["scenario_id"]
            if repository.scenario_id != expected:
                raise ValueError("This service requires a Relay Crisis save")
            # Run only at startup, never during an application turn.
            repository.connection.executescript("""
                CREATE TABLE IF NOT EXISTS player_session (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                    opening_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS player_turns (
                    revision INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    command TEXT NOT NULL, response_json TEXT NOT NULL
                );
            """)
            with repository.transaction():
                repository.connection.execute(
                    "INSERT OR IGNORE INTO player_session VALUES (1, 0, ?)",
                    (json.dumps(app.introduction()),),
                )
        finally:
            app.repository.close()

    def _app(self, repository: GameRepository) -> GameApplication:
        return GameApplication(
            SimulationEngine(repository), npc_backend=self.npc_backend,
            backend_label=self.backend_label, intent_interpreter=self.intent_interpreter,
            intent_label=self.intent_label, developer_mode=False,
        )

    def view(self) -> dict:
        """Return only approved player output, never raw engine records."""
        with GameRepository(self.database) as repository:
            with repository.transaction():
                session = repository.connection.execute(
                    "SELECT * FROM player_session WHERE id=1"
                ).fetchone()
                turns = repository.connection.execute(
                    "SELECT command, response_json FROM player_turns ORDER BY revision"
                ).fetchall()
                return {
                    "revision": session["revision"],
                    "dialogue_mode": "ai" if self.backend_label.startswith("OpenAI") else "offline",
                    "opening": json.loads(session["opening_json"]),
                    "status": self._app(repository).status(),
                    "transcript": [
                        {"command": row["command"], **json.loads(row["response_json"])}
                        for row in turns
                    ],
                }

    def submit(self, request_id: str, command: str, expected_revision: int) -> dict:
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("Request id must contain 1 to 128 characters")
        if not isinstance(command, str) or not 1 <= len(command.strip()) <= 2000:
            raise ValueError("Command must contain 1 to 2000 characters")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Revision must be a nonnegative integer")
        if len(command) > 2000:
            raise ValueError("Command is too long")
        with GameRepository(self.database) as repository:
            with repository.transaction():
                receipt = repository.connection.execute(
                    "SELECT command, response_json FROM player_turns WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if receipt:
                    if receipt["command"] != command:
                        raise TurnConflict("Request id already used for another command")
                    return json.loads(receipt["response_json"])
                revision = repository.connection.execute(
                    "SELECT revision FROM player_session WHERE id=1"
                ).fetchone()[0]
                if revision != expected_revision:
                    raise TurnConflict("Game changed; refresh before submitting again")
                app = self._app(repository)
                normalized = " ".join(command.lower().split())
                # The CLI summary includes global private goal outcomes. Until a
                # player-knowledge summary exists, expose only public status.
                if normalized in {"summary", "recap"}:
                    messages, should_quit = app.status(), False
                else:
                    messages, should_quit = app.handle(command)
                response = {
                    "revision": revision + 1,
                    "messages": messages,
                    "should_quit": should_quit,
                    "status": app.status(),
                }
                repository.connection.execute(
                    "INSERT INTO player_turns VALUES (?, ?, ?, ?)",
                    (revision + 1, request_id, command, json.dumps(response)),
                )
                repository.connection.execute(
                    "UPDATE player_session SET revision=? WHERE id=1", (revision + 1,)
                )
                return response
