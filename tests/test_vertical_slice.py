"""End-to-end tests for the deterministic SQLite vertical slice."""

import tempfile
import json
import sqlite3
import unittest
from importlib.resources import as_file, files
from types import SimpleNamespace
from pathlib import Path

from badbadger.agents.context import NPCContextBuilder
from badbadger.agents.intent import PlayerIntent
from badbadger.agents.npc import (
    ActionProposal,
    DeterministicNPCBackend,
    DisclosureProposal,
    FallbackNPCBackend,
    NPCResponse,
    RelationshipProposal,
)
from badbadger.agents.openai_client import OpenAIResponsesClient
from badbadger.agents.openai_intent import OpenAIIntentInterpreter
from badbadger.application import create_prototype, open_prototype
from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction, MoveAction, WaitAction
from badbadger.engine.dialogue import DialogueService
from badbadger.engine.npc_actions import resolve_npc_proposal
from badbadger.scenarios.loader import ScenarioError, load_scenario


class VerticalSliceTests(unittest.TestCase):
    def test_relay_crisis_relationships_are_directional_and_private(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)
            sable = NPCContextBuilder(app.repository).build("sable")
            orr = NPCContextBuilder(app.repository).build("orr")
            sable_to_orr = next(
                item for item in sable.relationships if item["target_id"] == "orr"
            )
            orr_to_sable = next(
                item for item in orr.relationships if item["target_id"] == "sable"
            )
            self.assertEqual(sable_to_orr["trust"], -0.35)
            self.assertEqual(orr_to_sable["trust"], -0.2)
            self.assertNotIn("Orr hopes", repr(sable.relationships))
            app.repository.close()

    def test_relationship_update_is_bounded_persistent_and_inspectable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            app = open_prototype(database)
            messages, _ = app.handle("tell Observer thank you")
            self.assertIn("welcome", messages[0].lower())
            relationship = app.repository.relationships_for("npc")[0]
            self.assertAlmostEqual(relationship["trust"], 0.1)
            self.assertAlmostEqual(relationship["affinity"], 0.05)
            self.assertEqual(
                relationship["recent_changes"][-1]["source_type"], "dialogue"
            )
            app.repository.close()

            resumed = open_prototype(database)
            relationship = resumed.repository.relationships_for("npc")[0]
            self.assertAlmostEqual(relationship["trust"], 0.1)
            with self.assertRaisesRegex(ValueError, "limited to 0.25"):
                resumed.repository.adjust_relationship(
                    "npc", "player", 0.3, 0, "Too large for one exchange."
                )
            resumed.repository.set_relationship(
                "npc", "player", 0.9, 0.9, reason="Near the upper limit."
            )
            bounded = resumed.repository.adjust_relationship(
                "npc", "player", 0.25, 0.25, "A strongly positive exchange."
            )
            self.assertEqual(bounded, {"trust": 1.0, "affinity": 1.0})
            inspected, _ = resumed.handle("npc Observer")
            self.assertIn("Player: trust=1.00, affinity=1.00", inspected[0])
            resumed.repository.close()

    def test_relationship_update_cannot_target_absent_character(self):
        class InventingBackend:
            def respond(self, context, player_input):
                return NPCResponse(
                    "I have reconsidered Orr.",
                    relationship_updates=[RelationshipProposal(
                        "orr", 0.2, 0.2, "An unsupported remote reassessment."
                    )],
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(
                    Path(temp_dir) / "save.db", scenario=scenario,
                    npc_backend=InventingBackend(),
                )
            before = next(
                item for item in app.repository.relationships_for("sable")
                if item["target_id"] == "orr"
            )
            app.handle("ask Director Sable about Orr")
            after = next(
                item for item in app.repository.relationships_for("sable")
                if item["target_id"] == "orr"
            )
            self.assertEqual(after["trust"], before["trust"])
            record = app.repository.connection.execute(
                "SELECT result_json FROM history WHERE record_type='dialogue_resolved' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIn('"accepted": false', record["result_json"])
            app.repository.close()

    def test_witnessed_memory_is_scoped_persistent_and_in_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(database, scenario=scenario)
            app.handle("go to Relay Chamber")
            with app.repository.transaction():
                app.repository.move_character("lin", "relay")
            app.handle("tell Technician Orr thank you")
            app.handle("tell Technician Orr thanks")
            app.handle("tell Technician Orr broadcast the distress packet")

            orr_memories = app.repository.memories_for("orr")
            lin_memories = app.repository.memories_for("lin")
            self.assertEqual(len(orr_memories), 1)
            self.assertEqual(len(lin_memories), 1)
            self.assertIn("rescue acknowledgment", lin_memories[0]["summary"])
            self.assertEqual(lin_memories[0]["source_character_id"], "orr")
            self.assertEqual(app.repository.memories_for("sable"), [])
            self.assertNotIn("crisis", repr(lin_memories))
            app.repository.close()

            resumed = open_prototype(database)
            context = NPCContextBuilder(resumed.repository).build("lin")
            self.assertEqual(context.memories[0]["summary"], lin_memories[0]["summary"])
            inspected, _ = resumed.handle("npc Dr. Lin")
            self.assertIn("rescue acknowledgment", inspected[0])
            resumed.repository.close()

    def test_memory_retrieval_is_bounded_and_salience_ordered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            with engine.repository.transaction():
                for index in range(10):
                    engine.repository.add_memory(
                        "npc", "room_a", "test", f"Memory {index}",
                        salience=index / 10,
                    )
            memories = engine.repository.memories_for("npc")
            self.assertEqual(len(memories), 8)
            self.assertEqual(memories[0]["summary"], "Memory 9")
            self.assertEqual(memories[-1]["summary"], "Memory 2")
            engine.repository.close()

    def test_relay_crisis_preserves_private_knowledge_and_appearance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)
            sable = NPCContextBuilder(app.repository).build("sable")
            orr = NPCContextBuilder(app.repository).build("orr")
            self.assertTrue(next(
                belief for belief in sable.beliefs
                if belief["subject_id"] == "sample"
            )["value"])
            self.assertFalse(next(
                belief for belief in orr.beliefs
                if belief["subject_id"] == "sample"
            )["value"])
            self.assertEqual(sable.appearance["hair"], "close-cropped silver")
            self.assertIn("architecture", sable.location["appearance"])
            self.assertNotIn("crisis", repr(sable))
            self.assertNotIn("outcome", repr(orr))
            app.repository.close()

    def test_player_can_trigger_irreversible_npc_choice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(database, scenario=scenario)
            app.handle("go to Relay Chamber")
            app.handle("tell Technician Orr thank you")
            app.handle("tell Technician Orr thanks")
            messages, _ = app.handle(
                "tell Technician Orr broadcast the distress packet"
            )
            self.assertTrue(any("rescue acknowledgment" in item for item in messages))
            self.assertEqual(app.repository.simulation_status, "active")
            self.assertEqual(
                app.repository.get_fact("crisis", "outcome"), "distress_broadcast"
            )
            orr_goal = app.repository.goals_for(
                "orr", include_resolved=True
            )[0]
            sable_goal = app.repository.goals_for(
                "sable", include_resolved=True
            )[0]
            lin_goal = app.repository.goals_for(
                "lin", include_resolved=True
            )[0]
            self.assertEqual(orr_goal["status"], "completed")
            self.assertEqual(sable_goal["status"], "failed")
            self.assertEqual(lin_goal["status"], "failed")
            self.assertEqual(
                orr_goal["history"][-1]["source_action_id"], "orr_broadcast"
            )
            inspected = app.inspect_npc("Technician Orr")[0]
            self.assertIn("broadcast_rescue (completed)", inspected)
            self.assertIn("received a rescue acknowledgment", inspected)
            self.assertIn("Latest (action via orr_broadcast)", inspected)
            self.assertIn("backed Orr's successful rescue attempt", inspected)
            app.repository.close()

            app = open_prototype(database)
            self.assertEqual(app.repository.simulation_status, "active")
            reaction, _ = app.handle("ask Technician Orr what now")
            self.assertIn("accomplished what mattered", reaction[0])
            self.assertIn("rescue acknowledgment", reaction[0])
            self.assertIn("trust your part", reaction[0])
            with app.repository.transaction():
                app.repository.move_character("lin", "relay")
            self.assertEqual(app.repository.available_npc_actions("lin"), [])
            aftermath, _ = app.handle("wait 8")
            self.assertTrue(any("aftermath settles" in item for item in aftermath))
            self.assertEqual(app.repository.simulation_status, "ended")
            ended, _ = app.handle("wait 1")
            self.assertIn("mission has ended", ended[0].lower())
            app.repository.close()
            resumed = open_prototype(database)
            self.assertEqual(resumed.repository.simulation_status, "ended")
            self.assertEqual(
                resumed.repository.get_fact("crisis", "outcome"),
                "distress_broadcast",
            )
            self.assertEqual(
                resumed.repository.goals_for(
                    "orr", include_resolved=True
                )[0]["status"],
                "completed",
            )
            resumed.repository.close()

    def test_private_disclosure_and_cooperation_require_earned_trust(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(database, scenario=scenario)
            app.handle("go to Relay Chamber")

            context = NPCContextBuilder(app.repository).build("orr")
            self.assertNotIn("mission minute twelve", repr(context))
            self.assertFalse(context.available_disclosures[0]["eligible"])

            refused, _ = app.handle("ask Technician Orr about the distress signal")
            self.assertTrue(any("don't know you well enough" in item for item in refused))
            self.assertFalse(any(
                belief["predicate"] == "final_window_minute"
                for belief in app.repository.beliefs_for("player")
            ))
            refused_action, _ = app.handle(
                "tell Technician Orr broadcast the distress packet"
            )
            self.assertTrue(any("does not trust you enough" in item for item in refused_action))
            self.assertEqual(app.repository.simulation_status, "active")

            app.handle("tell Technician Orr thank you")
            app.handle("tell Technician Orr thanks")
            shared, _ = app.handle("ask Technician Orr about the distress signal")
            self.assertTrue(any("mission minute twelve" in item for item in shared))
            player_belief = next(
                belief for belief in app.repository.beliefs_for("player")
                if belief["predicate"] == "final_window_minute"
            )
            self.assertEqual(player_belief["value"], 12)
            app.repository.close()

            resumed = open_prototype(database)
            affordance = resumed.repository.disclosure_affordances("orr", "player")[0]
            self.assertTrue(affordance["already_shared"])
            inspected, _ = resumed.handle("npc Technician Orr")
            self.assertIn("handshake window: shared", inspected[0])
            resumed.repository.close()

    def test_all_relay_outcomes_resolve_goals_relationships_and_reactions(self):
        outcomes = {
            "sable_seal_relay": {
                "actor": "sable",
                "goals": {
                    "sable": "completed", "orr": "failed", "lin": "abandoned",
                },
                "player_trust_deltas": {
                    "sable": 0.15, "orr": -0.15, "lin": 0.05,
                },
            },
            "orr_broadcast": {
                "actor": "orr",
                "goals": {
                    "sable": "failed", "orr": "completed", "lin": "failed",
                },
                "player_trust_deltas": {
                    "sable": -0.25, "orr": 0.15, "lin": -0.15,
                },
            },
            "lin_purge_sample": {
                "actor": "lin",
                "goals": {
                    "sable": "completed", "orr": "abandoned", "lin": "completed",
                },
                "player_trust_deltas": {
                    "sable": 0.1, "orr": -0.1, "lin": 0.15,
                },
            },
        }
        for action_id, expected in outcomes.items():
            with self.subTest(action_id=action_id), tempfile.TemporaryDirectory() as temp_dir:
                resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
                with as_file(resource) as scenario:
                    app = open_prototype(
                        Path(temp_dir) / "save.db", scenario=scenario
                    )
                actor_id = expected["actor"]
                with app.repository.transaction():
                    action_row = app.repository.connection.execute(
                        "SELECT location_id FROM npc_actions WHERE id=?", (action_id,)
                    ).fetchone()
                    app.repository.move_character(actor_id, action_row["location_id"])
                    if action_id == "orr_broadcast":
                        app.repository.set_relationship(
                            "orr", "player", 0.5, 0.15,
                            reason="Test setup satisfies the cooperation gate.",
                        )
                before = {
                    npc_id: next(
                        item for item in app.repository.relationships_for(npc_id)
                        if item["target_id"] == "player"
                    )["trust"]
                    for npc_id in ("sable", "orr", "lin")
                }
                with app.repository.transaction():
                    accepted, _ = app.repository.apply_npc_action(
                        actor_id, action_id, requested_by_player=True
                    )
                self.assertTrue(accepted)

                for npc_id, status in expected["goals"].items():
                    goal = app.repository.goals_for(
                        npc_id, include_resolved=True
                    )[0]
                    self.assertEqual(goal["status"], status)
                    relationship = next(
                        item for item in app.repository.relationships_for(npc_id)
                        if item["target_id"] == "player"
                    )
                    self.assertAlmostEqual(
                        relationship["trust"],
                        before[npc_id] + expected["player_trust_deltas"][npc_id],
                    )
                    consequence = relationship["recent_changes"][-1]
                    self.assertEqual(consequence["source_type"], "action")
                    self.assertEqual(consequence["source_action_id"], action_id)

                reactions = []
                service = DialogueService(
                    app.repository, DeterministicNPCBackend()
                )
                for npc_id in ("sable", "orr", "lin"):
                    with app.repository.transaction():
                        npc = app.repository.get_character(npc_id)
                        app.repository.move_character("player", npc["location_id"])
                    reactions.append(service.converse(npc_id, "What now?")[0])
                self.assertEqual(len(set(reactions)), 3)
                app.repository.close()

    def test_relationship_gain_cannot_unlock_disclosure_in_same_exchange(self):
        class ManipulativeBackend:
            def respond(self, context, player_input):
                return NPCResponse(
                    "Perhaps I can trust you.",
                    relationship_updates=[RelationshipProposal(
                        "player", 0.25, 0, "The player made a persuasive appeal."
                    )],
                    disclosure_requests=[DisclosureProposal("orr_rescue_window")],
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(
                    Path(temp_dir) / "save.db", scenario=scenario,
                    npc_backend=ManipulativeBackend(),
                )
            app.handle("go to Relay Chamber")
            first, _ = app.handle("ask Technician Orr about the distress signal")
            self.assertTrue(any("don't know you well enough" in item for item in first))
            self.assertFalse(any(
                belief["predicate"] == "final_window_minute"
                for belief in app.repository.beliefs_for("player")
            ))
            relationship = next(
                item for item in app.repository.relationships_for("orr")
                if item["target_id"] == "player"
            )
            self.assertEqual(relationship["trust"], 0.5)
            app.repository.close()

    def test_information_question_cannot_authorize_consequential_action(self):
        class OvereagerBackend:
            def respond(self, context, player_input):
                return NPCResponse(
                    "The receiver is listening.",
                    proposed_actions=[ActionProposal(
                        "interact", {"action_id": "orr_broadcast"}
                    )],
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(
                    Path(temp_dir) / "save.db", scenario=scenario,
                    npc_backend=OvereagerBackend(),
                )
            app.handle("go to Relay Chamber")
            messages, _ = app.handle("ask Technician Orr what he knows")
            self.assertIn("No consequential action was taken", messages[1])
            self.assertFalse(app.repository.get_fact("crisis", "resolved"))
            self.assertEqual(app.repository.simulation_status, "active")
            app.repository.close()

    def test_relay_crisis_allows_time_to_question_multiple_npcs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)
            app.handle("examine situation table")
            app.handle("ask Director Sable what she knows")
            app.handle("go to Relay Chamber")
            app.handle("ask Technician Orr what he knows")
            self.assertEqual(app.repository.current_time, 6)
            self.assertEqual(app.repository.simulation_status, "active")
            self.assertFalse(app.repository.get_fact("crisis", "resolved"))
            app.repository.close()

    def test_shared_belief_changes_explainable_autonomous_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)

            initial = app.repository.available_npc_actions("orr")[0]
            self.assertEqual(initial["id"], "orr_broadcast")
            self.assertAlmostEqual(initial["decision_score"], 1.1)
            self.assertTrue(initial["autonomous_eligible"])
            self.assertTrue(any(
                "prioritizes reaching rescue" in reason
                for reason in initial["decision_reasons"]
            ))
            self.assertTrue(any(
                "risk tolerance" in reason
                for reason in initial["decision_reasons"]
            ))

            with app.repository.transaction():
                app.repository.move_character("lin", "relay")
                accepted, _ = app.repository.share_belief(
                    "lin", "orr", "sample", "is_unstable"
                )
            self.assertTrue(accepted)
            changed = app.repository.available_npc_actions("orr")[0]
            self.assertAlmostEqual(changed["decision_score"], 0.1)
            self.assertFalse(changed["autonomous_eligible"])
            self.assertTrue(any(
                "endanger containment" in reason
                for reason in changed["decision_reasons"]
            ))

            with app.repository.transaction():
                accepted, message = resolve_npc_proposal(
                    app.repository,
                    "orr",
                    ActionProposal("interact", {"action_id": "orr_broadcast"}),
                    autonomous=True,
                )
            self.assertFalse(accepted)
            self.assertIn("priorities weigh against", message)
            self.assertEqual(app.repository.simulation_status, "active")
            inspected, _ = app.handle("npc Technician Orr")
            self.assertIn("score=0.10, minimum=1.00, eligible=False", inspected[0])
            self.assertIn("endanger containment", inspected[0])
            app.repository.close()

    def test_personality_parameter_changes_autonomous_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)

            willing = app.repository.available_npc_actions("orr")[0]
            self.assertAlmostEqual(willing["decision_score"], 1.1)
            self.assertTrue(willing["autonomous_eligible"])

            with app.repository.transaction():
                parameters = app.repository.npc_parameters("orr")
                parameters["risk_tolerance"] = 0.2
                app.repository.set_npc_parameters("orr", parameters)

            hesitant = app.repository.available_npc_actions("orr")[0]
            self.assertAlmostEqual(hesitant["decision_score"], 0.5)
            self.assertFalse(hesitant["autonomous_eligible"])
            self.assertFalse(any(
                "risk tolerance" in reason
                for reason in hesitant["decision_reasons"]
            ))
            with app.repository.transaction():
                accepted, message = app.repository.apply_npc_action(
                    "orr", "orr_broadcast", autonomous=True
                )
            self.assertFalse(accepted)
            self.assertIn("priorities weigh against", message)
            self.assertFalse(app.repository.get_fact("crisis", "resolved"))
            app.repository.close()

    def test_relationship_changes_autonomous_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)

            urgent = app.repository.available_npc_actions("orr")[0]
            self.assertAlmostEqual(urgent["decision_score"], 1.1)
            self.assertTrue(urgent["autonomous_eligible"])
            self.assertTrue(any(
                "distrust of Sable" in reason
                for reason in urgent["decision_reasons"]
            ))

            with app.repository.transaction():
                app.repository.set_relationship(
                    "orr", "sable", 0.0, -0.15,
                    reason="Orr no longer distrusts Sable's judgment.",
                )
            reconsidered = app.repository.available_npc_actions("orr")[0]
            self.assertAlmostEqual(reconsidered["decision_score"], 0.9)
            self.assertFalse(reconsidered["autonomous_eligible"])
            self.assertFalse(any(
                "distrust of Sable" in reason
                for reason in reconsidered["decision_reasons"]
            ))
            app.repository.close()

    def test_witnessed_memory_changes_autonomous_decision_until_it_expires(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "memory-decision.json"
            source.write_text(json.dumps({
                "scenario_id": "memory-decision",
                "locations": [
                    {"id": "lab", "name": "Lab", "description": "A lab."},
                ],
                "characters": [
                    {"id": "player", "kind": "player", "name": "Player",
                     "location_id": "lab"},
                    {"id": "source", "kind": "npc", "name": "Source",
                     "location_id": "lab"},
                    {"id": "witness", "kind": "npc", "name": "Witness",
                     "location_id": "lab"},
                ],
                "npc_actions": [
                    {
                        "id": "demonstrate", "character_id": "source",
                        "location_id": "lab", "label": "Demonstrate the alarm",
                        "description": "Make the alarm visibly sound.",
                        "effects": {"observations": [{
                            "kind": "alarm_sounded", "subject_id": "alarm",
                            "summary": "Source demonstrated the audible alarm.",
                            "salience": 0.8,
                        }]},
                    },
                    {
                        "id": "respond", "character_id": "witness",
                        "location_id": "lab", "label": "Respond to the alarm",
                        "description": "Act after witnessing the alarm.",
                        "effects": {"decision": {
                            "base_score": 0.1, "minimum_score": 0.5,
                            "memory_factors": [{
                                "memory_kind": "alarm_sounded",
                                "subject_id": "alarm",
                                "source_character_id": "source",
                                "min_salience": 0.7,
                                "within_minutes": 5,
                                "score": 0.6,
                                "reason": "Witness recently heard Source's alarm",
                            }],
                        }},
                    },
                ],
            }), encoding="utf-8")
            app = open_prototype(Path(temp_dir) / "save.db", scenario=source)

            before = app.repository.available_npc_actions("witness")[0]
            self.assertAlmostEqual(before["decision_score"], 0.1)
            self.assertFalse(before["autonomous_eligible"])

            with app.repository.transaction():
                accepted, _ = app.repository.apply_npc_action(
                    "source", "demonstrate"
                )
            self.assertTrue(accepted)
            after = app.repository.available_npc_actions("witness")[0]
            self.assertAlmostEqual(after["decision_score"], 0.7)
            self.assertTrue(after["autonomous_eligible"])
            self.assertTrue(any(
                "recently heard" in reason for reason in after["decision_reasons"]
            ))

            with app.repository.transaction():
                app.repository.advance_time(6)
            expired = app.repository.available_npc_actions("witness")[0]
            self.assertAlmostEqual(expired["decision_score"], 0.1)
            self.assertFalse(expired["autonomous_eligible"])
            app.repository.close()

    def test_engine_rejects_invented_npc_interaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)
            accepted, _ = resolve_npc_proposal(
                app.repository,
                "sable",
                ActionProposal("interact", {"action_id": "invented_override"}),
            )
            self.assertFalse(accepted)
            self.assertFalse(app.repository.get_fact("crisis", "resolved"))
            self.assertEqual(app.repository.simulation_status, "active")
            app.repository.close()

    def test_offscreen_npc_consequence_does_not_leak_narration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(Path(temp_dir) / "save.db", scenario=scenario)
            player_trust_before = {
                npc_id: next(
                    item for item in app.repository.relationships_for(npc_id)
                    if item["target_id"] == "player"
                )["trust"]
                for npc_id in ("sable", "orr", "lin")
            }
            accepted, message = resolve_npc_proposal(
                app.repository,
                "orr",
                ActionProposal("interact", {"action_id": "orr_broadcast"}),
            )
            self.assertTrue(accepted)
            self.assertEqual(message, "")
            self.assertEqual(
                app.repository.get_fact("crisis", "outcome"), "distress_broadcast"
            )
            for npc_id, trust in player_trust_before.items():
                relationship = next(
                    item for item in app.repository.relationships_for(npc_id)
                    if item["target_id"] == "player"
                )
                self.assertAlmostEqual(relationship["trust"], trust)
            app.repository.close()

    def test_npc_context_contains_only_reachable_action_affordances(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            context = NPCContextBuilder(engine.repository).build("npc")
            self.assertEqual(context.available_actions, [{
                "kind": "move", "target_id": "room_b", "target_name": "Room B",
                "duration_minutes": 5,
            }])
            self.assertNotIn("description", repr(context.available_actions))
            engine.repository.close()

    def test_autonomy_uses_custom_scenario_destination_without_hard_coding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "scenario.json"
            source.write_text(json.dumps({
                "scenario_id": "generic_autonomy",
                "locations": [
                    {"id": "lab", "name": "Lab", "description": "A lab."},
                    {"id": "atrium", "name": "Atrium", "description": "An atrium."},
                ],
                "connections": [{"from": "lab", "to": "atrium", "minutes": 7}],
                "characters": [
                    {"id": "player", "kind": "player", "name": "Player", "location_id": "lab"},
                    {"id": "bot", "kind": "npc", "name": "Bot", "location_id": "lab",
                     "parameters": {"first_decision_after_minutes": 1},
                     "goals": [{"description": "Survey the Atrium.", "priority": 1}]},
                ],
            }), encoding="utf-8")
            app = open_prototype(Path(temp_dir) / "save.db", scenario=source)
            messages, _ = app.handle("wait 1")
            self.assertTrue(any("Atrium" in message for message in messages))
            self.assertEqual(
                app.repository.pending_activity("bot")["destination_id"], "atrium"
            )
            app.repository.close()

    def test_autonomy_prompt_and_context_are_scenario_generic(self):
        class CapturingBackend:
            def respond(self, context, player_input):
                self.context = context
                self.player_input = player_input
                return NPCResponse("No action.")

        with tempfile.TemporaryDirectory() as temp_dir:
            backend = CapturingBackend()
            app = open_prototype(Path(temp_dir) / "save.db", npc_backend=backend)
            app.handle("wait 3")
            self.assertNotIn("room_b", backend.player_input)
            self.assertNotIn("Room B", backend.player_input)
            self.assertEqual(backend.context.available_actions[0]["target_id"], "room_b")
            app.repository.close()

    def test_offscreen_npcs_share_beliefs_and_evolve_relationships(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "social.json"
            source.write_text(json.dumps({
                "scenario_id": "autonomous_social_test",
                "locations": [
                    {"id": "lobby", "name": "Lobby", "description": "A lobby."},
                    {"id": "lab", "name": "Lab", "description": "A lab."},
                ],
                "characters": [
                    {"id": "player", "kind": "player", "name": "Player",
                     "location_id": "lobby"},
                    {"id": "source", "kind": "npc", "name": "Source",
                     "location_id": "lab",
                     "parameters": {"first_decision_after_minutes": 1},
                     "beliefs": [{"subject_id": "signal", "predicate": "is_real",
                                  "value": True, "confidence": 0.9}]},
                    {"id": "listener", "kind": "npc", "name": "Listener",
                     "location_id": "lab", "parameters": {"autonomy_enabled": False}},
                ],
            }), encoding="utf-8")
            database = Path(temp_dir) / "save.db"
            app = open_prototype(database, scenario=source)

            context = NPCContextBuilder(app.repository).build("source")
            social = next(
                action for action in context.available_actions
                if action["kind"] == "share_belief"
            )
            self.assertEqual(social["target_id"], "listener")
            messages, _ = app.handle("wait 1")
            self.assertNotIn("Source", " ".join(messages))
            learned = next(
                belief for belief in app.repository.beliefs_for("listener")
                if belief["subject_id"] == "signal"
            )
            self.assertTrue(learned["value"])
            self.assertAlmostEqual(learned["confidence"], 0.5625)
            relationship = app.repository.relationships_for("listener")[0]
            self.assertEqual(relationship["target_id"], "source")
            self.assertAlmostEqual(relationship["trust"], 0.04)
            self.assertIn("Source told Listener", app.repository.memories_for("listener")[0]["summary"])
            self.assertFalse(any(
                belief["subject_id"] == "signal"
                for belief in app.repository.beliefs_for("player")
            ))
            app.repository.close()

            resumed = open_prototype(database)
            self.assertTrue(any(
                belief["subject_id"] == "signal"
                for belief in resumed.repository.beliefs_for("listener")
            ))
            resumed.repository.close()

    def test_player_overhears_visible_npc_information_exchange(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            with engine.repository.transaction():
                engine.repository.add_character("listener", "npc", "Listener", "room_a")
                accepted, message = engine.repository.share_belief(
                    "npc", "listener", "room_b", "is_safe"
                )
            self.assertTrue(accepted)
            self.assertIn("Observer tells Listener", message)
            player_belief = next(
                belief for belief in engine.repository.beliefs_for("player")
                if belief["subject_id"] == "room_b"
            )
            self.assertTrue(player_belief["value"])
            self.assertAlmostEqual(player_belief["confidence"], 0.375)
            engine.repository.close()

    def test_directional_trust_calibrates_hearsay_before_belief_resolution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            with engine.repository.transaction():
                engine.repository.add_character("listener", "npc", "Listener", "room_a")
                engine.repository.set_belief(
                    "listener", "room_b", "is_safe", False, 0.3,
                    source_type="initial",
                )
                engine.repository.set_relationship(
                    "listener", "npc", -1.0, 0,
                    reason="Listener distrusts Observer.",
                )
                accepted, _ = engine.repository.share_belief(
                    "npc", "listener", "room_b", "is_safe"
                )
            self.assertTrue(accepted)
            resolved = next(
                belief for belief in engine.repository.beliefs_for("listener")
                if belief["subject_id"] == "room_b"
            )
            self.assertFalse(resolved["value"])
            true_evidence = next(
                item for item in resolved["evidence"] if item["value"] is True
            )
            self.assertAlmostEqual(true_evidence["confidence"], 0.15)

            with engine.repository.transaction():
                engine.repository.set_relationship(
                    "listener", "npc", 1.0, 0,
                    reason="Listener now completely trusts Observer.",
                )
                accepted, _ = engine.repository.share_belief(
                    "npc", "listener", "room_b", "is_safe"
                )
            self.assertTrue(accepted)
            resolved = next(
                belief for belief in engine.repository.beliefs_for("listener")
                if belief["subject_id"] == "room_b"
            )
            self.assertTrue(resolved["value"])
            # Supporting evidence totals .75, then resolution discounts it by
            # the remaining .30 of contrary evidence.
            self.assertAlmostEqual(resolved["confidence"], 0.75 / 1.3)
            engine.repository.close()

    def test_conflicting_npc_information_damages_trust_without_forcing_belief(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            with engine.repository.transaction():
                engine.repository.add_character("listener", "npc", "Listener", "room_a")
                engine.repository.set_belief(
                    "listener", "room_b", "is_safe", False, 0.95,
                    source_type="initial",
                )
                accepted, _ = engine.repository.share_belief(
                    "npc", "listener", "room_b", "is_safe"
                )
            self.assertTrue(accepted)
            resolved = next(
                belief for belief in engine.repository.beliefs_for("listener")
                if belief["subject_id"] == "room_b"
            )
            self.assertFalse(resolved["value"])
            relationship = engine.repository.relationships_for("listener")[0]
            self.assertAlmostEqual(relationship["trust"], -0.05)
            self.assertAlmostEqual(relationship["affinity"], -0.03)
            engine.repository.close()

    def test_dialogue_backend_cannot_force_npc_social_action(self):
        class SocialBackend:
            def respond(self, context, player_input):
                return NPCResponse(
                    "I will tell Listener.",
                    proposed_actions=[ActionProposal("share_belief", {
                        "target_id": "listener",
                        "subject_id": "room_b",
                        "predicate": "is_safe",
                    })],
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            with engine.repository.transaction():
                engine.repository.add_character("listener", "npc", "Listener", "room_a")
            service = DialogueService(engine.repository, SocialBackend())
            messages = service.converse("npc", "Tell Listener what you know.")
            self.assertTrue(any("only available" in item for item in messages))
            self.assertEqual(engine.repository.beliefs_for("listener"), [])
            engine.repository.close()

    def test_npc_parameters_are_inspectable_without_hidden_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = open_prototype(Path(temp_dir) / "save.db")
            messages, _ = app.handle("npc Observer")
            output = messages[0]
            self.assertIn("risk_tolerance: 0.35", output)
            self.assertIn("temperament: 'cautious'", output)
            self.assertIn("room_b.is_safe", output)
            self.assertNotIn("contains_access_code", output)
            self.assertEqual(
                NPCContextBuilder(app.repository).build("npc").parameters["risk_tolerance"],
                0.35,
            )
            app.repository.close()

    def test_custom_scenario_parameters_control_autonomy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "scenario.json"
            data = {
                "scenario_id": "quiet_test",
                "locations": [{"id": "lab", "name": "Lab", "description": "A lab."}],
                "characters": [
                    {"id": "player", "kind": "player", "name": "Player", "location_id": "lab"},
                    {"id": "bot", "kind": "npc", "name": "Bot", "location_id": "lab",
                     "parameters": {"autonomy_enabled": False, "first_decision_after_minutes": 1}},
                ],
            }
            source.write_text(json.dumps(data), encoding="utf-8")
            app = open_prototype(Path(temp_dir) / "save.db", scenario=source)
            self.assertFalse(app.repository.npc_parameters("bot")["autonomy_enabled"])
            app.handle("wait 1")
            self.assertEqual(app.repository.due_decision_events(10), [])
            app.repository.close()
            resumed = open_prototype(Path(temp_dir) / "save.db")
            self.assertEqual(resumed.repository.scenario_id, "quiet_test")
            self.assertFalse(resumed.repository.npc_parameters("bot")["autonomy_enabled"])
            resumed.repository.close()

    def test_invalid_scenario_is_rejected_before_state_is_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "bad.json"
            source.write_text(json.dumps({
                "scenario_id": "bad", "locations": [],
                "characters": [{"id": "p", "kind": "player", "name": "P", "location_id": "missing"}],
            }), encoding="utf-8")
            with self.assertRaises(ScenarioError):
                load_scenario(source, Path(temp_dir) / "save.db")

    def test_existing_save_migrates_goals_to_stable_lifecycle_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "legacy.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE npc_goals ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, "
                "description TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 1, "
                "active INTEGER NOT NULL DEFAULT 1, "
                "UNIQUE(character_id, description))"
            )
            connection.execute(
                "INSERT INTO npc_goals(character_id,description,priority,active) "
                "VALUES ('npc','Legacy goal',2,1)"
            )
            connection.commit()
            connection.close()

            repository = GameRepository(database)
            repository.create_schema()
            goal = repository.goals_for("npc", include_resolved=True)[0]
            self.assertEqual(goal["goal_id"], "legacy_1")
            self.assertEqual(goal["status"], "active")
            repository.close()

    def test_invalid_parameter_decision_factor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "bad-factor.json"
            source.write_text(json.dumps({
                "scenario_id": "bad-factor",
                "locations": [
                    {"id": "lab", "name": "Lab", "description": "A lab."},
                ],
                "characters": [
                    {"id": "player", "kind": "player", "name": "Player",
                     "location_id": "lab"},
                    {"id": "npc", "kind": "npc", "name": "NPC",
                     "location_id": "lab"},
                ],
                "npc_actions": [{
                    "id": "act", "character_id": "npc", "location_id": "lab",
                    "label": "Act", "description": "Take an action.",
                    "effects": {"decision": {"parameter_factors": [{
                        "parameter": "risk_tolerance", "operator": "approximately",
                        "value": 0.5, "score": 0.2, "reason": "Invalid operator",
                    }]}},
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ScenarioError, "operator"):
                load_scenario(source, Path(temp_dir) / "save.db")

    def test_invalid_memory_decision_factor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "bad-memory-factor.json"
            source.write_text(json.dumps({
                "scenario_id": "bad-memory-factor",
                "locations": [
                    {"id": "lab", "name": "Lab", "description": "A lab."},
                ],
                "characters": [
                    {"id": "player", "kind": "player", "name": "Player",
                     "location_id": "lab"},
                    {"id": "npc", "kind": "npc", "name": "NPC",
                     "location_id": "lab"},
                ],
                "npc_actions": [{
                    "id": "act", "character_id": "npc", "location_id": "lab",
                    "label": "Act", "description": "Take an action.",
                    "effects": {"decision": {"memory_factors": [{
                        "memory_kind": "warning", "within_minutes": -1,
                        "score": 0.2, "reason": "Invalid recency window",
                    }]}},
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ScenarioError, "window"):
                load_scenario(source, Path(temp_dir) / "save.db")

    def test_idle_npc_makes_bounded_independent_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = open_prototype(Path(temp_dir) / "save.db")
            messages, _ = app.handle("wait 3")
            self.assertTrue(any("departs" in message for message in messages))
            activity = app.repository.pending_activity("npc")
            self.assertEqual(activity["destination_id"], "room_b")
            self.assertEqual(app.repository.get_character("npc")["location_id"], "room_a")
            app.handle("wait 5")
            self.assertEqual(app.repository.get_character("npc")["location_id"], "room_b")
            app.repository.close()

    def test_deterministic_autonomy_prefers_highest_scored_goal_action(self):
        context = SimpleNamespace(
            goals=[{"description": "Stabilize the relay."}],
            available_actions=[
                {"id": "low", "kind": "interact", "label": "Stabilize the relay",
                 "decision_score": 0.4, "autonomous_eligible": True},
                {"id": "high", "kind": "interact", "label": "Stabilize the relay",
                 "decision_score": 0.9, "autonomous_eligible": True},
            ],
        )
        response = DeterministicNPCBackend().respond(
            context, "ENGINE DECISION TICK"
        )
        self.assertEqual(response.proposed_actions[0].parameters["action_id"], "high")

    def test_decision_call_budget_limits_npc_backend_calls(self):
        class CountingBackend:
            def __init__(self): self.calls = 0
            def respond(self, context, player_input):
                self.calls += 1
                return NPCResponse("I will remain here.")

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            with engine.repository.transaction():
                engine.repository.add_character("npc_2", "npc", "Second", "room_a")
                engine.repository.ensure_decision_event("npc_2", 3)
            engine.repository.close()
            backend = CountingBackend()
            app = open_prototype(
                Path(temp_dir) / "save.db", npc_backend=backend
            )
            app.handle("wait 3")
            self.assertEqual(backend.calls, 1)
            remaining = app.repository.due_decision_events(10)
            self.assertEqual(len(remaining), 1)
            app.repository.close()
    def test_npc_travel_is_scheduled_visible_and_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db=Path(temp_dir)/"save.db"; app=open_prototype(db)
            messages,_=app.handle("tell Observer to go check Room B")
            self.assertTrue(any("departs" in m for m in messages))
            self.assertEqual(app.repository.get_character("npc")["location_id"],"room_a")
            app.repository.close(); app=open_prototype(db)
            self.assertIsNotNone(app.repository.pending_activity("npc"))
            messages,_=app.handle("wait 4")
            self.assertFalse(any("arrives" in m for m in messages))
            self.assertEqual(app.repository.get_character("npc")["location_id"],"room_b")
            app.repository.close()

    def test_cancelled_npc_travel_never_arrives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app=open_prototype(Path(temp_dir)/"save.db")
            app.handle("tell Observer to go check Room B")
            activity=app.repository.pending_activity("npc")
            with app.repository.transaction(): app.repository.cancel_activity(activity["id"])
            app.handle("wait 10")
            self.assertEqual(app.repository.get_character("npc")["location_id"],"room_a")
            app.repository.close()
    def test_state_time_event_and_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            engine = create_prototype(database)

            examined = engine.perform(ExamineAction("panel"))
            moved = engine.perform(MoveAction("room_b"))
            waited = engine.perform(WaitAction(3))

            self.assertTrue(examined.accepted)
            self.assertTrue(moved.accepted)
            self.assertEqual(engine.repository.current_time, 10)
            self.assertIn(
                "The lights in Room B flicker and go dark.", waited.messages
            )
            self.assertFalse(engine.repository.get_fact("room_b", "lights_on"))
            engine.repository.close()

            with GameRepository(database) as reloaded:
                self.assertEqual(reloaded.current_time, 10)
                self.assertEqual(reloaded.get_player()["location_id"], "room_b")
                self.assertFalse(reloaded.get_fact("room_b", "lights_on"))
                status = reloaded.connection.execute(
                    "SELECT status FROM scheduled_events WHERE event_type='set_fact'"
                ).fetchone()["status"]
                self.assertEqual(status, "processed")

    def test_npc_beliefs_do_not_include_hidden_world_truth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            beliefs = engine.repository.beliefs_for("npc")

            self.assertEqual(len(beliefs), 1)
            self.assertEqual(beliefs[0]["subject_id"], "room_b")
            self.assertNotIn("contains_access_code", repr(beliefs))
            self.assertTrue(
                engine.repository.get_fact("panel", "contains_access_code")
            )
            context = NPCContextBuilder(engine.repository).build("npc")
            self.assertNotIn("contains_access_code", repr(context))
            engine.repository.close()

    def test_dialogue_and_belief_update_persist_across_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            app = open_prototype(database)

            messages, _ = app.handle("ask the Observer whether Room B is safe")
            self.assertIn("believe Room B is safe", messages[0])
            messages, _ = app.handle("tell Observer the lights in Room B are out")
            self.assertIn("remember", messages[0])
            self.assertEqual(app.repository.current_time, 2)
            app.repository.close()

            resumed = open_prototype(database)
            dialogue = resumed.repository.recent_dialogue("npc")
            self.assertEqual(len(dialogue), 4)
            self.assertEqual(
                dialogue[0]["text"], "ask the Observer whether Room B is safe"
            )
            lights = next(
                belief
                for belief in resumed.repository.beliefs_for("npc")
                if belief["predicate"] == "lights_on"
            )
            self.assertFalse(lights["value"])
            self.assertEqual(lights["evidence"][0]["source_type"], "hearsay")
            self.assertEqual(lights["evidence"][0]["source_character_id"], "player")
            resumed.repository.close()

    def test_contradictory_belief_evidence_resolves_deterministically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            repository = engine.repository

            initial = repository.beliefs_for("npc")[0]
            self.assertTrue(initial["value"])
            self.assertAlmostEqual(initial["confidence"], 0.6)
            self.assertEqual(initial["evidence"][0]["source_type"], "initial")

            with repository.transaction():
                repository.set_belief(
                    "npc",
                    "room_b",
                    "is_safe",
                    False,
                    0.7,
                    source_type="hearsay",
                    source_character_id="player",
                    detail="The player warned me.",
                )
            contradicted = repository.beliefs_for("npc")[0]
            self.assertFalse(contradicted["value"])
            self.assertAlmostEqual(contradicted["confidence"], 0.7 / 1.6)

            with repository.transaction():
                repository.set_belief(
                    "npc",
                    "room_b",
                    "is_safe",
                    True,
                    0.9,
                    source_type="direct",
                    detail="Inspected Room B personally.",
                )
            resolved = repository.beliefs_for("npc")[0]
            self.assertTrue(resolved["value"])
            self.assertAlmostEqual(resolved["confidence"], 1.5 / 1.7)
            self.assertEqual(
                [item["source_type"] for item in resolved["evidence"]],
                ["initial", "hearsay", "direct"],
            )
            repository.close()

    def test_existing_belief_is_bootstrapped_as_legacy_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            repository = engine.repository
            repository.connection.execute("DELETE FROM belief_evidence")
            repository.connection.commit()
            repository.create_schema()

            migrated = repository.beliefs_for("npc")[0]
            self.assertEqual(
                [item["source_type"] for item in migrated["evidence"]],
                ["legacy"],
            )

            with repository.transaction():
                repository.set_belief(
                    "npc",
                    "room_b",
                    "is_safe",
                    False,
                    0.8,
                    source_type="inference",
                    detail="New evidence after migration.",
                )

            belief = repository.beliefs_for("npc")[0]
            self.assertEqual(
                [item["source_type"] for item in belief["evidence"]],
                ["legacy", "inference"],
            )
            repository.close()

    def test_npc_action_proposal_cannot_directly_change_world_state(self):
        class ProposingBackend:
            def respond(self, context, player_input):
                return NPCResponse(
                    "Perhaps you should go elsewhere.",
                    proposed_actions=[
                        ActionProposal("move_player", {"destination_id": "room_b"})
                    ],
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            service = DialogueService(engine.repository, ProposingBackend())
            service.converse("npc", "Where should I go?")

            self.assertEqual(engine.repository.get_player()["location_id"], "room_a")
            record = engine.repository.connection.execute(
                "SELECT result_json FROM history WHERE record_type = 'dialogue_resolved'"
            ).fetchone()
            self.assertIn('"accepted": false', record["result_json"])
            engine.repository.close()

    def test_openai_adapter_sends_filtered_context_and_maps_structured_output(self):
        class FakeResponses:
            def __init__(self):
                self.calls = []

            def parse(self, **kwargs):
                self.calls.append(kwargs)
                parsed = SimpleNamespace(
                    dialogue="A structured reply.",
                    proposed_actions=[
                        SimpleNamespace(kind="wait", target_id=None, minutes=1),
                        SimpleNamespace(
                            kind="interact", target_id=None, minutes=None,
                            action_id="declared_action",
                        ),
                    ],
                    belief_updates=[
                        SimpleNamespace(
                            subject_id="player",
                            predicate="is_curious",
                            value=True,
                            confidence=0.8,
                        )
                    ],
                    relationship_updates=[SimpleNamespace(
                        target_id="player", trust_delta=0.1,
                        affinity_delta=0.05, reason="A helpful exchange.",
                    )],
                    disclosure_requests=[SimpleNamespace(
                        disclosure_id="declared_disclosure",
                    )],
                )
                return SimpleNamespace(output_parsed=parsed)

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            with engine.repository.transaction():
                engine.repository.add_memory(
                    "npc", "room_a", "witnessed_action",
                    "The status panel emitted a visible warning.", salience=0.8,
                    subject_id="panel",
                )
            context = NPCContextBuilder(engine.repository).build("npc")
            responses = FakeResponses()
            sdk = SimpleNamespace(responses=responses)
            client = OpenAIResponsesClient(
                "test-model", sdk_client=sdk, response_model=object
            )

            result = client.respond(context, "What do you know?")

            self.assertEqual(result.dialogue, "A structured reply.")
            self.assertEqual(result.proposed_actions[0].kind, "wait")
            self.assertEqual(result.proposed_actions[0].parameters, {"minutes": 1})
            self.assertEqual(
                result.proposed_actions[1].parameters,
                {"action_id": "declared_action"},
            )
            self.assertTrue(result.belief_updates[0].value)
            self.assertEqual(result.relationship_updates[0].target_id, "player")
            self.assertEqual(result.relationship_updates[0].trust_delta, 0.1)
            self.assertEqual(
                result.disclosure_requests[0].disclosure_id,
                "declared_disclosure",
            )
            call = responses.calls[0]
            self.assertEqual(call["model"], "test-model")
            self.assertIs(call["text_format"], object)
            self.assertFalse(call["store"])
            serialized_context = call["input"][1]["content"]
            self.assertIn("visible warning", serialized_context)
            self.assertNotIn("contains_access_code", serialized_context)
            self.assertNotIn("OPENAI_API_KEY", serialized_context)
            engine.repository.close()

    def test_openai_adapter_retries_one_unparsed_response(self):
        class RetryResponses:
            def __init__(self):
                self.calls = 0

            def parse(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return SimpleNamespace(output_parsed=None)
                return SimpleNamespace(
                    output_parsed=SimpleNamespace(
                        dialogue="Recovered.",
                        proposed_actions=[],
                        belief_updates=[],
                    )
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            context = NPCContextBuilder(engine.repository).build("npc")
            responses = RetryResponses()
            client = OpenAIResponsesClient(
                "test-model",
                sdk_client=SimpleNamespace(responses=responses),
                response_model=object,
            )

            with self.assertLogs("badbadger.agents.openai_client", level="WARNING"):
                result = client.respond(context, "Hello")

            self.assertEqual(result.dialogue, "Recovered.")
            self.assertEqual(responses.calls, 2)
            engine.repository.close()

    def test_failed_primary_backend_falls_back_without_corrupting_state(self):
        class FailingBackend:
            def respond(self, context, player_input):
                raise RuntimeError("simulated provider outage")

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            backend = FallbackNPCBackend(FailingBackend(), DeterministicNPCBackend())
            service = DialogueService(engine.repository, backend)

            with self.assertLogs("badbadger.agents.npc", level="ERROR"):
                messages = service.converse(
                    "npc", "ask the Observer whether Room B is safe"
                )

            self.assertIn("believe Room B is safe", messages[0])
            self.assertIn("did not advance time", messages[1])
            self.assertEqual(engine.repository.current_time, 0)
            self.assertEqual(len(engine.repository.recent_dialogue("npc")), 2)
            engine.repository.close()

    def test_text_commands_are_persisted_and_resumed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            app = open_prototype(database)

            messages, should_quit = app.handle("examine the panel")
            self.assertFalse(should_quit)
            self.assertIn("operating normally", messages[0])

            messages, _ = app.handle("travel to Room B")
            self.assertIn("Room B", messages[0])
            app.repository.close()

            resumed = open_prototype(database)
            self.assertEqual(resumed.repository.current_time, 7)
            self.assertEqual(resumed.repository.get_player()["location_id"], "room_b")
            messages, should_quit = resumed.handle("quit")
            self.assertTrue(should_quit)
            self.assertIn("saved", messages[0].lower())
            resumed.repository.close()

    def test_text_interpreter_rejects_unknown_input_without_advancing_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = open_prototype(Path(temp_dir) / "save.db")
            messages, should_quit = app.handle("perform quantum diplomacy")

            self.assertFalse(should_quit)
            self.assertIn("couldn't interpret", messages[0])
            self.assertEqual(app.repository.current_time, 0)
            app.repository.close()

    def test_llm_intent_handles_natural_movement_and_engine_validates_it(self):
        class NaturalLanguageInterpreter:
            def interpret(self, context, player_input):
                self.context = context
                self.player_input = player_input
                return PlayerIntent("move", target_id="room_b")

        with tempfile.TemporaryDirectory() as temp_dir:
            interpreter = NaturalLanguageInterpreter()
            app = open_prototype(
                Path(temp_dir) / "save.db",
                intent_interpreter=interpreter,
                intent_label="fake LLM",
            )
            text = "Head into the other room and see what's there"

            messages, _ = app.handle(text)

            self.assertIn("Room B", messages[0])
            self.assertEqual(app.repository.get_player()["location_id"], "room_b")
            self.assertEqual(interpreter.player_input, text)
            self.assertNotIn("contains_access_code", repr(interpreter.context))
            app.repository.close()

    def test_engine_rejects_llm_intent_with_invented_target(self):
        class InventingInterpreter:
            def interpret(self, context, player_input):
                return PlayerIntent("move", target_id="secret_moon_base")

        with tempfile.TemporaryDirectory() as temp_dir:
            app = open_prototype(
                Path(temp_dir) / "save.db",
                intent_interpreter=InventingInterpreter(),
            )

            messages, _ = app.handle("Take me to the secret moon base")

            self.assertIn("not available", messages[0])
            self.assertEqual(app.repository.get_player()["location_id"], "room_a")
            self.assertEqual(app.repository.current_time, 0)
            app.repository.close()

    def test_openai_intent_adapter_uses_visible_context_and_structured_output(self):
        class FakeResponses:
            def __init__(self):
                self.calls = []

            def parse(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(
                    output_parsed=SimpleNamespace(
                        kind="examine", target_id="panel", minutes=None
                    )
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            app = open_prototype(Path(temp_dir) / "save.db")
            context = app._intent_context()
            responses = FakeResponses()
            interpreter = OpenAIIntentInterpreter(
                "test-model",
                sdk_client=SimpleNamespace(responses=responses),
                response_model=object,
            )

            intent = interpreter.interpret(context, "Study that console closely")

            self.assertEqual(intent, PlayerIntent("examine", "panel"))
            call = responses.calls[0]
            self.assertFalse(call["store"])
            self.assertIs(call["text_format"], object)
            serialized = call["input"][1]["content"]
            self.assertIn("Study that console closely", serialized)
            self.assertNotIn("contains_access_code", serialized)
            self.assertNotIn("is_safe", serialized)
            app.repository.close()


if __name__ == "__main__":
    unittest.main()
