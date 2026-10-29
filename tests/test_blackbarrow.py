"""Complete chapter loops and the boundary between authored evidence and choice."""

from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path
import tempfile
import unittest

from badbadger.backup import snapshot
from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction
from badbadger.scenarios.loader import ScenarioError, load_scenario
from badbadger.service import RelayService, TurnConflict


class BlackbarrowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "save.db"
        self.game = RelayService(self.path, scenario="blackbarrow")
        self.revision = 0

    def command(self, text):
        response = self.game.submit(str(self.revision), text, self.revision)
        self.revision = response["revision"]
        return response

    def test_all_three_plans_complete_with_different_costs_and_save_resume(self):
        paths = {
            "controlled_crossing": ["examine convoy petition", "ask Horace what he knows", "go to Causeway",
                                    "examine causeway ruts", "go to Gatehouse", "tell Horace oversee a controlled crossing"],
            "high_track": ["examine convoy petition", "ask Matilda what she knows", "go to High Track",
                           "examine ridge markers", "go to Gatehouse", "tell Matilda guide the high track"],
            "hold_convoy": ["examine convoy petition", "ask Horace what he knows", "tell Horace hold the convoy"],
        }
        for index, (outcome, commands) in enumerate(paths.items()):
            with self.subTest(outcome=outcome):
                self.path = Path(self.temp.name) / f"branch-{index}.db"
                self.game = RelayService(self.path, scenario="blackbarrow")
                self.revision = 0
                for command in commands[:3]:
                    self.command(command)
                saved = self.game.view()
                self.game = RelayService(self.path)  # Default changed; this must still resume Blackbarrow.
                self.assertEqual(self.game.view(), saved)
                for command in commands[3:]:
                    self.command(command)
                self.assertEqual(self.game.view()["guidance"]["phase"], "aftermath")
                self.assertIn("You asked", str(self.game.view()["guidance"]["recap"]))
                self.command("ask Matilda what now")
                self.command("wait 6 minutes")
                self.assertEqual(self.game.view()["guidance"]["phase"], "ended")
                with GameRepository(self.path) as repository:
                    self.assertEqual(repository.get_fact("chapter", "outcome"), outcome)
                    self.assertEqual(repository.get_fact("convoy", "people_sheltered"), True)
                    self.assertEqual(repository.get_fact("convoy", "grain_delivered"), outcome == "controlled_crossing")
                    self.assertEqual(repository.get_fact("convoy", "medicine_delivered"), outcome != "hold_convoy")
                    self.assertEqual(repository.get_fact("convoy", "stores_spent"), outcome == "hold_convoy")
                    self.assertEqual(repository.get_player()["name"], "Victor Gravenholt")
                    self.assertEqual(repository.character_identity("matilda")["revision"], 2)
                recap = " ".join(self.command("summary")["messages"])
                self.assertIn("Chapter complete", recap)
                self.assertNotIn("decision_score", recap)
                self.assertNotIn("Alden", recap)

    def test_scoped_examination_is_not_available_remotely_and_records_direct_evidence(self):
        view = self.game.view()
        self.assertNotIn("examine ridge markers", str(view["guidance"]["suggestions"]))
        with GameRepository(self.path) as repository:
            from badbadger.engine.simulation import SimulationEngine
            result = SimulationEngine(repository).perform(ExamineAction("ridge_markers"))
            self.assertFalse(result.accepted)
            self.assertEqual(repository.current_time, 0)
        self.command("go to High Track")
        self.command("examine ridge markers")
        with GameRepository(self.path) as repository:
            self.assertEqual(len(repository.characters_at("high_track")), 3)
            for character in ("victor", "matilda", "horace"):
                knowledge = next(item for item in repository.beliefs_for(character) if item["predicate"] == "passable_on_foot")
                self.assertEqual(knowledge["evidence"][-1]["source_type"], "direct")
                self.assertTrue(repository.memories_for(character))
        self.assertIn("high track", " ".join(self.command("journal")["messages"]))

    def test_companions_already_trust_victor_and_travel_on_validated_routes(self):
        with GameRepository(self.path) as repository:
            for actor in ("matilda", "horace"):
                relationship = next(row for row in repository.relationships_for(actor) if row["target_id"] == "victor")
                self.assertEqual(relationship["trust"], .8)
        self.command("go to Causeway")
        with GameRepository(self.path) as repository:
            self.assertEqual({row["id"] for row in repository.characters_at("causeway")}, {"victor", "matilda", "horace"})
            self.assertEqual(repository.current_time, 2)
            self.assertIsNone(repository.pending_activity("matilda"))
            self.assertEqual(repository.connection.execute("SELECT count(*) FROM history WHERE record_type='npc_spoke'").fetchone()[0], 0)

    def test_busy_companion_is_not_teleported_or_available_for_conversation(self):
        with GameRepository(self.path) as repository:
            with repository.transaction():
                repository.create_travel("matilda", "high_track", 3)
            reply = self.game._app(repository).dialogue.converse("matilda", "ask Matilda what she knows")
            self.assertIn("travelling", reply[0])
        self.command("go to Causeway")
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.get_character("horace")["location_id"], "causeway")
            self.assertIsNotNone(repository.pending_activity("matilda"))
            self.assertNotIn("matilda", {row["id"] for row in repository.characters_at("gatehouse")})

    def test_question_is_not_commitment_and_inspection_is_not_a_mandatory_unlock(self):
        result = self.command("ask Horace why we should oversee a controlled crossing")
        self.assertIn("controlled crossing", " ".join(result["messages"]))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("chapter", "resolved"))
        self.command("tell Horace hold the convoy")
        self.assertEqual(self.game.view()["guidance"]["phase"], "aftermath")

    def test_waiting_allows_independent_action_without_player_credit(self):
        refused = self.command("wait 100")
        self.assertIn("between 1 and 6", refused["messages"][0])
        for _ in range(4):
            self.command("wait 6")
        recap = " ".join(self.game.view()["guidance"]["recap"])
        self.assertIn("high track", recap)
        self.assertNotIn("You asked", recap)
        reply = " ".join(self.command("ask Matilda why")["messages"])
        self.assertNotIn("your part in what happened", reply)
        self.command("wait 6")
        self.assertEqual(self.game.view()["guidance"]["phase"], "ended")

    def test_chapter_switch_archives_old_state_and_retries_cannot_switch_twice(self):
        relay_path = Path(self.temp.name) / "relay.db"
        relay = RelayService(relay_path)
        old = relay.submit("move", "go to Relay Chamber", 0)
        resumed = RelayService(relay_path, scenario="blackbarrow")
        self.assertEqual(resumed.view()["scenario"]["key"], "relay_crisis")
        result = resumed.new_game("switch", 1, confirmed=True, scenario="blackbarrow")
        self.assertEqual(resumed.new_game("switch", 1, confirmed=True, scenario="blackbarrow"), result)
        with self.assertRaises(TurnConflict):
            resumed.new_game("switch", 1, confirmed=True, scenario="relay_crisis")
        with self.assertRaises(TurnConflict):
            resumed.submit("stale", "go to Relay Chamber", 1)
        with self.assertRaises(ValueError):
            resumed.new_game("bad", 2, confirmed=True, scenario="../../private")
        self.assertEqual(resumed.submit("move", "go to Relay Chamber", 0), old)
        self.assertEqual(resumed.view()["scenario"]["key"], "blackbarrow")
        resumed.new_game("back", 2, confirmed=True, scenario="relay_crisis")
        with GameRepository(relay_path) as repository:
            archives = repository.connection.execute("SELECT state_json FROM playthrough_archives ORDER BY end_revision").fetchall()
            self.assertEqual(len(archives), 2)
            self.assertEqual(json.loads(archives[0][0])["scenario_id"], "relay-crisis-0.1")
            self.assertEqual(json.loads(archives[1][0])["scenario_id"], "blackbarrow-north-road-0.1")

    def test_backup_restore_contains_identity_evidence_and_active_chapter(self):
        self.command("go to Causeway")
        self.command("examine causeway ruts")
        original = self.game.view()
        backup = snapshot(self.path, Path(self.temp.name) / "backup.db")
        self.assertEqual(RelayService(backup).view(), original)
        with GameRepository(backup) as repository:
            self.assertEqual(repository.character_identity("horace")["name"], "Captain Horace")
            self.assertTrue(repository.beliefs_for("victor"))

    def test_invalid_scoped_observations_are_rejected_before_save_creation(self):
        original = json.loads(files("badbadger.scenarios").joinpath("blackbarrow_north_road.json").read_text())
        # Use no identity references in this malformed fixture, so the specific
        # authoring error is what prevents creating the database.
        for actor in original["characters"]:
            identity = actor.pop("identity_file")
            profile = json.loads(files("badbadger.scenarios").joinpath(identity).read_text())
            actor["name"], actor["kind"] = profile["name"], profile["kind"]
        for field, value in (("location_id", "missing"), ("beliefs", "not-a-list")):
            malformed = deepcopy(original)
            malformed["examinations"][0][field] = value
            source = Path(self.temp.name) / "bad.json"
            source.write_text(json.dumps(malformed))
            destination = Path(self.temp.name) / "bad.db"
            with self.assertRaises(ScenarioError):
                load_scenario(source, destination)
            self.assertFalse(destination.exists())
