"""Player-facing chapter boundary with engine-validated AI proposals.

Construct once at startup with a server-owned path. Each call owns its SQLite
connection; BEGIN IMMEDIATE serializes turns even across service instances.
No network listener, authentication, or client-controlled save paths live here.
"""

from __future__ import annotations

import json
import tempfile
from importlib.resources import as_file, files
from pathlib import Path

from badbadger.application import GameApplication, open_prototype
from badbadger.config import build_configured_intent_interpreter, build_configured_npc_backend
from badbadger.db.repository import GameRepository
from badbadger.engine.simulation import SimulationEngine
from badbadger.scenarios.catalog import CHAPTERS, chapter, chapter_key


class TurnConflict(ValueError):
    """The request id was reused or the client viewed an older revision."""


class RelayService:
    """Persist complete turns, their public transcript, and replayable receipts."""

    def __init__(self, database: str | Path, *, offline: bool = True, scenario: str = "relay_crisis") -> None:
        selected = chapter(scenario)
        self.npc_backend, self.backend_label = build_configured_npc_backend(offline=offline)
        self.intent_interpreter, self.intent_label = build_configured_intent_interpreter(offline=offline)
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        resource = files("badbadger.scenarios").joinpath(selected["file"])
        with as_file(resource) as scenario:
            app = open_prototype(self.database, scenario=scenario, developer_mode=False)
        try:
            repository = app.repository
            chapter_key(repository.scenario_id)
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
                CREATE TABLE IF NOT EXISTS playthrough_archives (
                    end_revision INTEGER PRIMARY KEY,
                    saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    state_json TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in repository.connection.execute("PRAGMA table_info(player_session)")}
            if "game_start_revision" not in columns:
                repository.connection.execute("ALTER TABLE player_session ADD COLUMN game_start_revision INTEGER NOT NULL DEFAULT 0")
            columns = {row["name"] for row in repository.connection.execute("PRAGMA table_info(player_turns)")}
            if "operation" not in columns:
                repository.connection.execute("ALTER TABLE player_turns ADD COLUMN operation TEXT NOT NULL DEFAULT 'turn'")
            with repository.transaction():
                repository.connection.execute(
                    "INSERT OR IGNORE INTO player_session(id,revision,opening_json) VALUES (1, 0, ?)",
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
                    "SELECT command, response_json FROM player_turns WHERE revision>? ORDER BY revision",
                    (session["game_start_revision"],),
                ).fetchall()
                return {
                    "scenario": {"key": chapter_key(repository.scenario_id),
                                 **{key: chapter(chapter_key(repository.scenario_id))[key] for key in ("title", "tagline")}},
                    "chapters": [{"key": key, "title": value["title"] + (" (prototype)" if key == "relay_crisis" else "")}
                                 for key, value in CHAPTERS.items()],
                    "revision": session["revision"],
                    "dialogue_mode": "ai" if self.backend_label.startswith("OpenAI") else "offline",
                    "guidance": self._app(repository).player_guidance(),
                    "player": self._app(repository).player_identity(),
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
                    "SELECT command, response_json, operation FROM player_turns WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if receipt:
                    if receipt["operation"] != "turn" or receipt["command"] != command:
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
                    messages, should_quit = app.player_summary(), False
                else:
                    messages, should_quit = app.handle(command)
                response = {
                    "revision": revision + 1,
                    "messages": messages,
                    "should_quit": should_quit,
                    "status": app.status(),
                }
                repository.connection.execute(
                    "INSERT INTO player_turns(revision,request_id,command,response_json) VALUES (?, ?, ?, ?)",
                    (revision + 1, request_id, command, json.dumps(response)),
                )
                repository.connection.execute(
                    "UPDATE player_session SET revision=? WHERE id=1", (revision + 1,)
                )
                return response

    def new_game(self, request_id: str, expected_revision: int, *, confirmed: bool = False, scenario: str | None = None) -> dict:
        """Archive the old engine state and reseed in one serialized transaction.

        Revisions never reset, so old browser tabs cannot submit to the new game.
        Receipts are retained across playthroughs so a retried reset is harmless.
        """
        if confirmed is not True:
            raise ValueError("Confirm starting a new game first")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("Request id must contain 1 to 128 characters")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Revision must be a nonnegative integer")
        if scenario is not None:
            chapter(scenario)
        with GameRepository(self.database) as repository:
            with repository.transaction():
                receipt = repository.connection.execute(
                    "SELECT operation,response_json FROM player_turns WHERE request_id=?", (request_id,),
                ).fetchone()
                if receipt:
                    if receipt["operation"] != "new-game":
                        raise TurnConflict("Request id already used for another command")
                    previous = json.loads(receipt["response_json"])
                    if scenario is not None and previous.get("scenario") != scenario:
                        raise TurnConflict("Request id already used for another chapter")
                    return previous
                session = repository.connection.execute("SELECT * FROM player_session WHERE id=1").fetchone()
                if session["revision"] != expected_revision:
                    raise TurnConflict("Game changed; refresh before starting a new game")
                selected_key = scenario or chapter_key(repository.scenario_id)
                selected = chapter(selected_key)
                resource = files("badbadger.scenarios").joinpath(selected["file"])
                with tempfile.TemporaryDirectory(prefix="badbadger-new-") as directory:
                    with as_file(resource) as scenario:
                        fresh = open_prototype(Path(directory) / "fresh.db", scenario=scenario, developer_mode=False)
                    try:
                        tables = [row["name"] for row in fresh.repository.connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                        )]
                        # Names come only from the bundled engine schema, never a client.
                        def quoted(name):
                            return '"' + name.replace('"', '""') + '"'
                        old = {table: [dict(row) for row in repository.connection.execute(f"SELECT * FROM {quoted(table)}")]
                               for table in tables}
                        archive = {"tables": old, "scenario_id": repository.scenario_id, "opening": json.loads(session["opening_json"]),
                                   "start_revision": session["game_start_revision"], "end_revision": expected_revision}
                        repository.connection.execute("INSERT INTO playthrough_archives(end_revision,state_json) VALUES (?,?)",
                                                      (expected_revision, json.dumps(archive)))
                        repository.connection.execute("PRAGMA defer_foreign_keys=ON")
                        for table in tables:
                            repository.connection.execute(f"DELETE FROM {quoted(table)}")
                        for table in tables:
                            for row in fresh.repository.connection.execute(f"SELECT * FROM {quoted(table)}"):
                                columns = ','.join(quoted(column) for column in row.keys())
                                placeholders = ','.join('?' for _ in row.keys())
                                repository.connection.execute(f"INSERT INTO {quoted(table)} ({columns}) VALUES ({placeholders})", tuple(row))
                        opening = fresh.introduction()
                    finally:
                        fresh.repository.close()
                revision = expected_revision + 1
                response = {"revision": revision, "scenario": selected_key, "messages": [f"New {selected['title']} game started."],
                            "should_quit": False, "status": self._app(repository).status()}
                repository.connection.execute(
                    "UPDATE player_session SET revision=?,game_start_revision=?,opening_json=? WHERE id=1",
                    (revision, revision, json.dumps(opening)),
                )
                repository.connection.execute(
                    "INSERT INTO player_turns(revision,request_id,command,response_json,operation) VALUES (?,?,?,?,'new-game')",
                    (revision, request_id, "New game", json.dumps(response)),
                )
                return response
