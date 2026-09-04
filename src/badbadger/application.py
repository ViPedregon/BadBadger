"""Application service for the first SQLite-backed playable slice."""

from __future__ import annotations

from pathlib import Path
from importlib.resources import as_file, files

from badbadger.agents.intent import (
    DeterministicIntentInterpreter,
    IntentInterpreter,
    PlayerIntent,
    PlayerIntentContext,
)
from badbadger.agents.npc import DeterministicNPCBackend, NPCBackend
from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction, MoveAction, WaitAction
from badbadger.engine.autonomy import AutonomyScheduler
from badbadger.engine.dialogue import DialogueService
from badbadger.engine.simulation import SimulationEngine
from badbadger.scenarios.loader import load_scenario


HELP_TEXT = """Commands and examples:
  go to Room B       travel to a known location
  examine panel      inspect a known subject
  wait 3 minutes     let mission time pass
  look               describe your current location
  ask Observer ...   speak with an NPC at your location
  tell Observer ...  give information to an NPC
  talk to Observer   begin a conversation
  npc Observer       inspect an NPC's configured parameters
  status             show location and mission time
  help               show this message
  quit               save and leave the game"""


class GameApplication:
    """Translate a small text vocabulary and delegate mutations to the engine."""

    def __init__(
        self,
        engine: SimulationEngine,
        npc_backend: NPCBackend | None = None,
        backend_label: str = "deterministic",
        intent_interpreter: IntentInterpreter | None = None,
        intent_label: str = "deterministic",
    ) -> None:
        self.engine = engine
        self.backend_label = backend_label
        self.intent_label = intent_label
        self.local_interpreter = DeterministicIntentInterpreter()
        self.intent_interpreter = intent_interpreter
        self.dialogue = DialogueService(
            engine.repository, npc_backend or DeterministicNPCBackend()
        )
        self.autonomy = AutonomyScheduler(
            engine.repository, npc_backend or DeterministicNPCBackend()
        )

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
        player_text = raw_input.strip()
        text = " ".join(player_text.split())
        lowered = text.lower()
        if not text:
            return [], False
        if lowered in {"quit", "exit", "q"}:
            return ["Game saved. Goodbye."], True
        if lowered in {"help", "?"}:
            return [HELP_TEXT], False
        if lowered in {"look", "look around", "status", "where am i"}:
            return self.status(), False
        if lowered == "npc":
            names = [row["name"] for row in self.repository.list_npcs()]
            return ["NPCs: " + (", ".join(names) if names else "none")], False
        if lowered.startswith("npc "):
            return self.inspect_npc(text[4:].strip()), False
        if lowered.startswith("inspect npc "):
            return self.inspect_npc(text[12:].strip()), False

        context = self._intent_context()
        intent = self.local_interpreter.interpret(context, text)
        source = "deterministic"
        if intent.kind == "unknown" and self.intent_interpreter is not None:
            intent = self.intent_interpreter.interpret(context, player_text)
            source = "llm"
        with self.repository.transaction():
            self.repository.record(
                "intent_interpreted",
                actor_id=self.repository.get_player()["id"],
                input_data={"player_input": player_text, "source": source},
                result_data={
                    "kind": intent.kind,
                    "target_id": intent.target_id,
                    "minutes": intent.minutes,
                },
            )
        messages = self._execute_intent(intent, player_text)
        messages.extend(self.autonomy.process_due(call_budget=1))
        return messages, False

    def _intent_context(self) -> PlayerIntentContext:
        player = self.repository.get_player()
        current = self.repository.get_location(player["location_id"])
        if current is None:
            raise RuntimeError("Player is assigned to an unknown location")
        return PlayerIntentContext(
            current_location={"id": current["id"], "name": current["name"]},
            known_locations=[
                {"id": item["id"], "name": item["name"]}
                for item in self.repository.list_locations()
            ],
            visible_npcs=[
                {"id": item["id"], "name": item["name"]}
                for item in self.repository.characters_at(
                    current["id"], kind="npc"
                )
            ],
            examinable_subject_ids=self.repository.examinable_subject_ids(),
        )

    def _execute_intent(self, intent: PlayerIntent, player_input: str) -> list[str]:
        if intent.kind == "move" and intent.target_id:
            if self.repository.get_location(intent.target_id) is None:
                return ["That destination is not available."]
            return self.engine.perform(MoveAction(intent.target_id)).messages
        if intent.kind == "examine" and intent.target_id:
            if intent.target_id not in self.repository.examinable_subject_ids():
                return ["You find nothing meaningful to examine."]
            return self.engine.perform(ExamineAction(intent.target_id)).messages
        if intent.kind == "wait" and intent.minutes is not None:
            if not 1 <= intent.minutes <= 1_440:
                return ["You can only wait between 1 and 1,440 minutes."]
            return self.engine.perform(WaitAction(intent.minutes)).messages
        if intent.kind == "speak" and intent.target_id:
            npc = self.repository.get_character(intent.target_id)
            player = self.repository.get_player()
            if (
                npc is None
                or npc["kind"] != "npc"
                or npc["location_id"] != player["location_id"]
            ):
                return ["That person is not here."]
            return self.dialogue.converse(npc["id"], player_input)
        return ["I couldn't interpret that yet. Try rephrasing or type 'help'."]

    def inspect_npc(self, reference: str) -> list[str]:
        """Render NPC-scoped runtime state without leaking hidden world facts."""
        npc = self.repository.find_npc(reference)
        if npc is None:
            return [f"Unknown NPC: {reference}"]
        location = self.repository.get_location(npc["location_id"])
        activity = self.repository.pending_activity(npc["id"])
        parameters = self.repository.npc_parameters(npc["id"])
        lines = [
            f"NPC: {npc['name']} ({npc['id']})",
            f"Location: {location['name'] if location else npc['location_id']}",
            f"Activity: {activity['kind'] if activity else 'idle'}",
            "Parameters:",
        ]
        lines.extend(
            f"  {key}: {value!r}" for key, value in sorted(parameters.items())
        )
        if not parameters:
            lines.append("  (none)")
        lines.append("Goals:")
        goals = self.repository.goals_for(npc["id"])
        lines.extend(
            f"  [{goal['priority']}] {goal['description']}" for goal in goals
        )
        if not goals:
            lines.append("  (none)")
        lines.append("Beliefs:")
        beliefs = self.repository.beliefs_for(npc["id"])
        lines.extend(
            f"  {belief['subject_id']}.{belief['predicate']} = "
            f"{belief['value']!r} (confidence {belief['confidence']:.3f})"
            for belief in beliefs
        )
        if not beliefs:
            lines.append("  (none)")
        return ["\n".join(lines)]


def create_prototype(database: str | Path) -> SimulationEngine:
    """Create a deliberately generic two-room test simulation."""
    resource = files("badbadger.scenarios").joinpath("prototype.json")
    with as_file(resource) as path:
        return load_scenario(path, database)


def open_prototype(
    database: str | Path,
    *,
    npc_backend: NPCBackend | None = None,
    backend_label: str = "deterministic",
    intent_interpreter: IntentInterpreter | None = None,
    intent_label: str = "deterministic",
    scenario: str | Path | None = None,
) -> GameApplication:
    """Resume an existing prototype save, or create it on first launch."""
    path = Path(database)
    if path.exists() and path.stat().st_size > 0:
        repository = GameRepository(path)
        try:
            repository.create_schema()
            repository.current_time
            if repository.scenario_id == "prototype-0.1":
                with repository.transaction():
                    repository.add_connection("room_a", "room_b", 5)
                    repository.add_connection("room_b", "room_a", 5)
                    repository.add_goal("npc", "Inspect Room B when an opportunity arises.", 1)
                    if not repository.npc_parameters("npc"):
                        repository.set_npc_parameters("npc", {
                            "autonomy_enabled": True,
                            "decision_cooldown_minutes": 10,
                            "first_decision_after_minutes": 3,
                            "risk_tolerance": 0.35,
                            "temperament": "cautious",
                        })
                    repository.ensure_decision_event("npc", repository.current_time + 3)
        except Exception:
            repository.close()
            raise
        return GameApplication(
            SimulationEngine(repository),
            npc_backend,
            backend_label,
            intent_interpreter,
            intent_label,
        )
    engine = load_scenario(scenario, path) if scenario else create_prototype(path)
    return GameApplication(
        engine,
        npc_backend,
        backend_label,
        intent_interpreter,
        intent_label,
    )
