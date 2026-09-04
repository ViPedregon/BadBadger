"""Application service for the first SQLite-backed playable slice."""

from __future__ import annotations

from pathlib import Path

from badbadger.agents.intent import (
    DeterministicIntentInterpreter,
    IntentInterpreter,
    PlayerIntent,
    PlayerIntentContext,
)
from badbadger.agents.npc import DeterministicNPCBackend, NPCBackend
from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction, MoveAction, WaitAction
from badbadger.engine.dialogue import DialogueService
from badbadger.engine.simulation import SimulationEngine


HELP_TEXT = """Commands and examples:
  go to Room B       travel to a known location
  examine panel      inspect a known subject
  wait 3 minutes     let mission time pass
  look               describe your current location
  ask Observer ...   speak with an NPC at your location
  tell Observer ...  give information to an NPC
  talk to Observer   begin a conversation
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
        return self._execute_intent(intent, player_text), False

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


def create_prototype(database: str | Path) -> SimulationEngine:
    """Create a deliberately generic two-room test simulation."""
    repository = GameRepository(database)
    repository.create_schema()
    with repository.transaction():
        repository.initialize_simulation("prototype-0.1")
        repository.add_location("room_a", "Room A", "A plain testing room.")
        repository.add_location("room_b", "Room B", "Another plain testing room.")
        repository.add_connection("room_a", "room_b", 5)
        repository.add_connection("room_b", "room_a", 5)
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
            source_type="initial",
            detail="Scenario-defined starting belief.",
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


def open_prototype(
    database: str | Path,
    *,
    npc_backend: NPCBackend | None = None,
    backend_label: str = "deterministic",
    intent_interpreter: IntentInterpreter | None = None,
    intent_label: str = "deterministic",
) -> GameApplication:
    """Resume an existing prototype save, or create it on first launch."""
    path = Path(database)
    if path.exists() and path.stat().st_size > 0:
        repository = GameRepository(path)
        try:
            repository.create_schema()
            repository.current_time
            with repository.transaction():
                repository.add_connection("room_a", "room_b", 5)
                repository.add_connection("room_b", "room_a", 5)
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
    return GameApplication(
        create_prototype(path),
        npc_backend,
        backend_label,
        intent_interpreter,
        intent_label,
    )
