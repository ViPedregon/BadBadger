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
from badbadger.objectives import objective_stages


HELP_TEXT = """You can type naturally. Useful examples:
  look                         describe your surroundings
  go to <place>                travel to a connected location
  ask <name> what they know    question someone present
  tell <name> <request>        explicitly request cooperation
  examine <subject>            inspect something nearby
  wait 2 minutes               let events unfold
  objective                    review your immediate purpose
  journal                      review facts and events you have learned
  people                       review the cast without revealing locations
  self                         review your authored character background
  choices                      see a few contextual suggestions
  summary                      review the chapter after it ends
  timing                       learn which actions spend mission time
  quit                         save and leave the game"""


class GameApplication:
    """Translate a small text vocabulary and delegate mutations to the engine."""

    def __init__(
        self,
        engine: SimulationEngine,
        npc_backend: NPCBackend | None = None,
        backend_label: str = "deterministic",
        intent_interpreter: IntentInterpreter | None = None,
        intent_label: str = "deterministic",
        developer_mode: bool = False,
    ) -> None:
        self.engine = engine
        self.backend_label = backend_label
        self.intent_label = intent_label
        self.local_interpreter = DeterministicIntentInterpreter()
        self.intent_interpreter = intent_interpreter
        self.developer_mode = developer_mode
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
        visible = self.repository.characters_at(location["id"], kind="npc")
        exits = self.repository.reachable_locations(location["id"])
        return [
            f"[{location['name']}]",
            location["description"],
            "Here: " + (", ".join(item["name"] for item in visible) or "no one else"),
            "Exits: " + (", ".join(item["name"] for item in exits) or "none"),
            f"Mission time: {self.repository.current_time} minutes.",
            f"Mission status: {self.repository.simulation_status}.",
        ]

    def introduction(self) -> list[str]:
        presentation = self.repository.scenario_presentation()
        title = presentation.get("title", self.repository.scenario_id)
        intro = presentation.get("intro", "Your next decision is waiting.")
        objective = presentation.get(
            "objective", "Explore, speak with the people present, and decide what to do."
        )
        hints = []
        if presentation.get("starting_hint"):
            hints.append(presentation["starting_hint"])
        elif self.repository.scenario_id == "relay-crisis-0.1":
            hints.append("Start with 'examine situation table', then question the people here. "
                         "There is no single safe choice: each plan sacrifices something.")
        return [f"=== {title} ===", intro, f"Objective: {objective}", *hints, *self.timing(), *self.status()]

    @staticmethod
    def timing() -> list[str]:
        return ["Mission time advances when you travel, examine, wait, or have a successful conversation. "
                "A conversation takes 1 minute; examining takes 2. Travel times appear in suggestions. "
                "Reading look, objective, people, self, journal, choices, summary, help, or timing costs no time. "
                "Other crew members can act as time advances. Closing the page does not advance time."]

    def suggested_commands(self) -> list[dict]:
        player = self.repository.get_player()
        if self.repository.simulation_status != "active":
            return [{"command": "summary", "label": "Review the chapter", "consequential": False},
                    {"command": "journal", "label": "Read your journal", "consequential": False}]
        aftermath = bool(self.repository.player_consequences())
        suggestions = []
        if not aftermath:
            for subject in self.repository.examinable_subject_ids():
                suggestions.append({"command": "examine " + subject.replace("_", " "),
                                    "label": "Examine " + subject.replace("_", " ") + " · 2 min", "consequential": False})
        presentation = self.repository.scenario_presentation()
        public_actions = set(presentation.get("public_action_ids", []))
        for npc in self.repository.characters_at(player["location_id"], kind="npc"):
            question = "what now" if aftermath else "what they know"
            suggestions.append({"command": f"ask {npc['name']} {question}",
                                "label": f"Ask {npc['name']} · 1 min", "consequential": False})
            # Relay Crisis publicly introduces these three plans. Never expose
            # arbitrary scenario actions, scores, preconditions or private goals.
            for action in self.repository.available_npc_actions(npc["id"]):
                if self.repository.scenario_id == "relay-crisis-0.1" or action["id"] in public_actions:
                    suggestions.append({"command": f"tell {npc['name']} {action['label'].lower()}",
                                        "label": f"Request: {action['label']} · irreversible if accepted",
                                        "consequential": True})
        for location in self.repository.reachable_locations(player["location_id"]):
            suggestions.append({"command": f"go to {location['name']}",
                                "label": f"Go to {location['name']} · {location['duration_minutes']} min", "consequential": False})
        if aftermath:
            suggestions.append({"command": "wait 1 minute", "label": "Wait · 1 min", "consequential": False})
            deadline = self.repository.public_chapter_deadline()
            if deadline is not None and deadline > self.repository.current_time:
                remaining = deadline - self.repository.current_time
                suggestions.append({"command": f"wait {remaining} minutes", "label": f"Finish aftermath · {remaining} min", "consequential": False})
        return suggestions

    def player_guidance(self) -> dict:
        ended = self.repository.simulation_status != "active"
        aftermath = bool(self.repository.player_consequences())
        phase = "ended" if ended else "aftermath" if aftermath else "investigating"
        if ended:
            message = "This chapter is complete. Review what you witnessed, or choose New game to explore another outcome."
        elif aftermath:
            deadline = self.repository.public_chapter_deadline()
            remaining = max(0, deadline - self.repository.current_time) if deadline is not None else None
            message = "An irreversible decision has been made. Ask the crew how they feel about it."
            if remaining is not None:
                message += f" The aftermath ends in {remaining} mission minutes."
        else:
            message = self.repository.scenario_presentation().get("investigation_hint", "Question the crew and compare their plans. NPCs can decide without you as mission time advances.")
        return {"phase": phase, "message": message,
                "stages": objective_stages(self.repository),
                "objective": self.repository.scenario_presentation().get("objective", "Explore and decide what to do."),
                "suggestions": self.suggested_commands(),
                "recap": self.player_summary() if ended or aftermath else []}

    def player_summary(self) -> list[str]:
        ended = self.repository.simulation_status != "active"
        consequences = self.repository.player_consequences()
        if not ended and not consequences:
            return ["The chapter is still unfolding. No decisive action has been recorded in your view yet."]
        lines = ["=== Chapter complete ===" if ended else "=== Decision aftermath ===",
                 f"Mission time: {self.repository.current_time} minutes.", "What you witnessed:"]
        lines.extend(item["summary"] for item in consequences)
        if not consequences:
            lines.append("You did not record the decisive action firsthand. Your journal and saved conversations contain what you learned.")
        lines.append("Choose New game to explore another outcome." if ended else "You can still speak with the crew before this chapter ends.")
        return ["\n".join(lines)]

    def choices(self) -> list[str]:
        return ["You could try (suggestions do not spend time until you send them):\n  "
                + "\n  ".join(item["command"] for item in self.suggested_commands())
                + "\nRequests to commit a plan are irreversible if accepted. Asking about a plan does not authorize it."]

    def journal(self) -> list[str]:
        """Render only knowledge and memories owned by the player."""
        player = self.repository.get_player()
        presentation = self.repository.scenario_presentation()
        lines = [
            "=== Journal ===",
            "Objective: " + presentation.get(
                "objective", "Explore, speak with the people present, and decide what to do."
            ),
            "",
            "What you know:",
        ]
        beliefs = self.repository.beliefs_for(player["id"])
        for belief in beliefs:
            subject = belief["subject_id"].replace("_", " ")
            predicate = belief["predicate"].replace("_", " ")
            value = belief["value"]
            rendered = "yes" if value is True else "no" if value is False else str(value)
            confidence = float(belief["confidence"])
            certainty = "confident" if confidence >= 0.85 else "likely" if confidence >= 0.6 else "uncertain"
            evidence = belief.get("evidence", [])
            source_id = evidence[-1].get("source_character_id") if evidence else None
            source = self.repository.get_character(source_id) if source_id else None
            attribution = f"; from {source['name']}" if source else ""
            lines.append(
                f"  {subject} {predicate}: {rendered} ({certainty}{attribution})"
            )
        if not beliefs:
            lines.append("  You have not confirmed any private information yet.")

        lines.extend(["", "What you remember:"])
        memories = self.repository.memories_for(player["id"], limit=5)
        lines.extend(f"  {item['summary']}" for item in memories)
        if not memories:
            lines.append("  Nothing significant has been recorded yet.")
        return ["\n".join(lines)]

    def people(self) -> list[str]:
        player = self.repository.get_player()
        lines = ["=== People ==="]
        for npc in self.repository.list_npcs():
            presence = "here" if npc["location_id"] == player["location_id"] and self.repository.pending_activity(npc["id"]) is None else "elsewhere"
            appearance = self.repository.appearance_for(npc["id"], "character")
            role = appearance.get("role") or appearance.get("clothing")
            detail = f" - {role}" if role else ""
            lines.append(f"{npc['name']} ({presence}){detail}")
        return ["\n".join(lines)]

    def player_identity(self) -> dict:
        player = self.repository.get_player()
        canon = self.repository.character_canon(player["id"]) or {}
        appearance = self.repository.appearance_for(player["id"], "character")
        return {"name": player["name"], "role": canon.get("role") or appearance.get("role", ""),
                "values": canon.get("values", []),
                "background": [fragment["summary"] for fragment in canon.get("biography", [])
                               if fragment["visibility"] == "public" and fragment["awareness"] == "conscious"
                               and not self.repository.contains_protected_text(fragment["summary"])]}

    def character_card(self) -> list[str]:
        identity = self.player_identity()
        lines = [f"You play {identity['name']}."]
        if identity["role"]:
            lines.append(identity["role"])
        if identity["values"]:
            lines.append("Values: " + ", ".join(identity["values"]))
        lines.extend(identity["background"])
        lines.append("Your choices and words remain yours; the game does not decide your thoughts for you.")
        return ["\n".join(lines)]

    def chapter_summary(self) -> list[str]:
        if self.repository.simulation_status == "active":
            return ["The chapter is still unfolding. Keep investigating or make a choice."]
        lines = [
            "=== Chapter complete ===",
            f"Final time: {self.repository.current_time} minutes.",
        ]
        for npc in self.repository.list_npcs():
            goals = self.repository.goals_for(npc["id"], include_resolved=True)
            for goal in goals:
                if goal["status"] != "active":
                    reason = goal["history"][-1]["reason"] if goal["history"] else goal["description"]
                    lines.append(f"{npc['name']} — {goal['status']}: {reason}")
        lines.append("Your choices and the NPCs' decisions are saved in this playthrough.")
        return ["\n".join(lines)]

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
            suffix = (
                "\n  npc <name>                   inspect private NPC engine state"
                if self.developer_mode else ""
            )
            return [HELP_TEXT + suffix, *self.timing(), "Examples here:\n" + "\n".join(item["command"] for item in self.suggested_commands()[:4])], False
        if lowered == "timing":
            return self.timing(), False
        if lowered in {"look", "look around", "status", "where am i"}:
            return self.status(), False
        if lowered in {"objective", "mission", "goal"}:
            presentation = self.repository.scenario_presentation()
            return ["Objective: " + presentation.get(
                "objective", "Explore, speak with the people present, and decide what to do."
            ), *[f"{stage['label']}: {stage['status']}" + (" (optional)" if stage['optional'] else "")
                  for stage in objective_stages(self.repository)]], False
        if lowered in {"journal", "notes", "knowledge", "what do i know"}:
            return self.journal(), False
        if lowered in {"people", "characters", "cast", "who is here"}:
            return self.people(), False
        if lowered in {"self", "my character", "who am i"}:
            return self.character_card(), False
        if lowered in {"choices", "suggestions", "what can i do"}:
            return self.choices(), False
        if lowered in {"summary", "recap"}:
            return (self.chapter_summary() if self.developer_mode else self.player_summary()), False
        if lowered == "npc":
            if not self.developer_mode:
                return ["NPC inspection is available only in developer mode."], False
            names = [row["name"] for row in self.repository.list_npcs()]
            return ["NPCs: " + (", ".join(names) if names else "none")], False
        if lowered.startswith("npc "):
            if not self.developer_mode:
                return ["NPC inspection is available only in developer mode."], False
            return self.inspect_npc(text[4:].strip()), False
        if lowered.startswith("inspect npc "):
            if not self.developer_mode:
                return ["NPC inspection is available only in developer mode."], False
            return self.inspect_npc(text[12:].strip()), False
        if self.repository.simulation_status != "active":
            return [
                "This mission has ended. Type 'summary' to review the chapter or start a new game."
            ], False

        context = self._intent_context()
        intent = self.local_interpreter.interpret(context, text)
        source = "deterministic"
        if intent.kind == "unknown" and self.intent_interpreter is not None:
            intent = self.intent_interpreter.interpret(context, player_text)
            source = "llm"
        intent_backend_failed = False
        consume_failure = getattr(self.intent_interpreter, "consume_failure", None)
        if callable(consume_failure):
            intent_backend_failed = bool(consume_failure())
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
        if intent_backend_failed and intent.kind == "unknown":
            messages = [
                "AI language interpretation is temporarily unavailable. "
                "Try a command shown by 'choices', or continue in offline mode."
            ]
        else:
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
            maximum = self.repository.scenario_presentation().get("max_wait_minutes", 1440)
            if not 1 <= intent.minutes <= maximum:
                return [f"Wait between 1 and {maximum} minutes at a time so you can respond as events unfold."]
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
            f"Appearance: {self.repository.appearance_for(npc['id'], 'character')!r}",
        ]
        canon = self.repository.character_canon(npc["id"])
        lines.append("Character canon:")
        if canon is None:
            lines.append("  (none)")
        else:
            lines.extend([
                f"  Role: {canon['role'] or '(unspecified)'}",
                f"  Voice: {canon['voice'] or '(unspecified)'}",
                "  Values: " + (", ".join(canon["values"]) or "(none)"),
                "  Boundaries: " + (", ".join(canon["boundaries"]) or "(none)"),
                "  Biography fragments:",
            ])
            for fragment in canon["biography"]:
                lines.append(
                    f"    {fragment['id']} [{fragment['visibility']}, "
                    f"{fragment['awareness']}, importance={fragment['importance']:.2f}, "
                    f"certainty={fragment['certainty']:.2f}]: {fragment['summary']}"
                )
            if not canon["biography"]:
                lines.append("    (none)")
        lines.append("Parameters:")
        lines.extend(
            f"  {key}: {value!r}" for key, value in sorted(parameters.items())
        )
        if not parameters:
            lines.append("  (none)")
        lines.append("Goals:")
        goals = self.repository.goals_for(npc["id"], include_resolved=True)
        lines.extend(
            f"  [{goal['priority']}] {goal['goal_id']} ({goal['status']}): "
            f"{goal['description']}"
            + (
                f"\n    Latest: {goal['history'][-1]['reason']}"
                if goal["history"] and goal["status"] != "active" else ""
            )
            for goal in goals
        )
        if not goals:
            lines.append("  (none)")
        lines.append("Autonomous action considerations:")
        decisions = self.repository.available_npc_actions(npc["id"])
        lines.extend(
            f"  {action['label']}: score={action['decision_score']:.2f}, "
            f"minimum={action['minimum_score']:.2f}, "
            f"eligible={action['autonomous_eligible']}\n    "
            + " ".join(action["decision_reasons"])
            for action in decisions
        )
        if not decisions:
            lines.append("  (none at current location)")
        lines.append("Beliefs:")
        beliefs = self.repository.beliefs_for(npc["id"])
        lines.extend(
            f"  {belief['subject_id']}.{belief['predicate']} = "
            f"{belief['value']!r} (confidence {belief['confidence']:.3f})"
            for belief in beliefs
        )
        if not beliefs:
            lines.append("  (none)")
        lines.append("Memories:")
        memories = self.repository.memories_for(npc["id"])
        lines.extend(
            f"  [t={memory['game_time']}, salience={memory['salience']:.2f}] "
            f"{memory['summary']}"
            for memory in memories
        )
        if not memories:
            lines.append("  (none)")
        lines.append("Relationships:")
        relationships = self.repository.relationships_for(npc["id"])
        lines.extend(
            f"  {relationship['target_name']}: trust={relationship['trust']:.2f}, "
            f"affinity={relationship['affinity']:.2f}"
            + (
                f"\n    Latest ({relationship['recent_changes'][-1]['source_type']}"
                + (
                    f" via {relationship['recent_changes'][-1]['source_action_id']}"
                    if relationship["recent_changes"][-1].get("source_action_id")
                    else ""
                )
                + f"): {relationship['recent_changes'][-1]['reason']}"
                if relationship["recent_changes"] else ""
            )
            for relationship in relationships
        )
        if not relationships:
            lines.append("  (none)")
        lines.append("Disclosure topics:")
        disclosures = self.repository.disclosure_affordances(
            npc["id"], self.repository.get_player()["id"]
        )
        lines.extend(
            f"  {item['topic']}: "
            f"{'shared' if item['already_shared'] else 'available' if item['eligible'] else 'withheld'}"
            for item in disclosures
        )
        if not disclosures:
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
    developer_mode: bool = True,
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
                    repository.add_goal(
                        "npc", "inspect_room_b",
                        "Inspect Room B when an opportunity arises.", 1,
                    )
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
            developer_mode,
        )
    engine = load_scenario(scenario, path) if scenario else create_prototype(path)
    return GameApplication(
        engine,
        npc_backend,
        backend_label,
        intent_interpreter,
        intent_label,
        developer_mode,
    )
