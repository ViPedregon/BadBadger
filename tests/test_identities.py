"""Reusable authored identity, save pinning and player-agency boundaries."""

from copy import deepcopy
from importlib.resources import as_file, files
import json
from pathlib import Path
import tempfile
import unittest

from badbadger.application import GameApplication, open_prototype
from badbadger.agents.context import NPCContextBuilder
from badbadger.backup import snapshot
from badbadger.db.repository import GameRepository
from badbadger.scenarios.identities import IdentityError, load_identity
from badbadger.scenarios.loader import ScenarioError, load_scenario
from badbadger.service import RelayService


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.player = self.profile("test_player", "Test Player", "player")
        self.guide = self.profile("test_guide", "Test Guide", "npc")
        self.guide["canon"]["biography"] = [{
            "id": "shared_past", "summary": "PRIVATE_GUIDE_HISTORY", "behavioral_guidance": "Ask careful questions.",
            "visibility": "private", "importance": .9, "related_identity_ids": ["test_player"],
        }]
        self.write_profiles()
        self.scenario = {"scenario_id": "identity-test", "locations": [
            {"id": "room", "name": "Test Room", "description": "A test fixture, not production lore."}],
            "characters": [
                {"id": "player", "location_id": "room", "identity_file": "player.json"},
                {"id": "guide", "location_id": "room", "identity_file": "guide.json",
                 "parameters": {"autonomy_enabled": False},
                 "goals": [{"id": "chapter_goal", "description": "Discuss the test question.", "priority": 1}],
                 "relationships": [{"target_id": "player", "trust": .2, "affinity": .1, "reason": "Test fixture"}]}]}

    @staticmethod
    def profile(identity_id, name, kind):
        return {"schema_version": 1, "identity_id": identity_id, "revision": 1, "status": "approved",
                "name": name, "kind": kind,
                "canon": {"role": "Test role", "voice": "Test voice", "values": ["care"],
                          "boundaries": ["Do not invent player decisions"], "biography": []},
                "personality": {"risk_tolerance": .35, "temperament": "test caution"},
                "motivations": ["Test motivation"], "appearance": {"role": "Public test role"},
                "author_notes": "TEST_AUTHOR_NOTES_NOT_MODEL_CONTEXT"}

    def write_profiles(self):
        (self.root / "player.json").write_text(json.dumps(self.player))
        (self.root / "guide.json").write_text(json.dumps(self.guide))

    def load(self, data=None, name="save.db"):
        path = self.root / "scenario.json"
        path.write_text(json.dumps(self.scenario if data is None else data))
        engine = load_scenario(path, self.root / name)
        self.addCleanup(engine.repository.close)
        return engine

    def test_confirmed_names_have_no_invented_lore_and_are_not_activated(self):
        root = files("badbadger.scenarios")
        for name, display, kind in (("victor-gravenholt", "Victor Gravenholt", "player"),
                                    ("matilda-ash", "Matilda Ash", "npc"), ("horace", "Horace", "npc")):
            with self.subTest(name=name), as_file(root) as directory:
                path = Path("characters") / f"{name}.json"
                profile = load_identity(path, directory, allow_unapproved=True)
                self.assertEqual((profile["name"], profile["kind"]), (display, kind))
                self.assertEqual(profile["status"], "awaiting_lore")
                self.assertEqual(profile["canon"]["biography"], [])
                self.assertEqual(profile["personality"], {})
                with self.assertRaises(IdentityError):
                    load_identity(path, directory)

    def test_authored_identity_enters_only_its_own_npc_context(self):
        engine = self.load()
        context = NPCContextBuilder(engine.repository).build("guide", "shared past")
        self.assertEqual(context.canon["voice"], "Test voice")
        self.assertEqual(context.canon["personality"]["risk_tolerance"], .35)
        self.assertEqual(context.parameters["risk_tolerance"], .35)
        self.assertFalse(context.parameters["autonomy_enabled"])
        self.assertEqual(context.canon["motivations"], ["Test motivation"])
        self.assertNotIn("PRIVATE_GUIDE_HISTORY", str(context))
        self.assertNotIn("TEST_AUTHOR_NOTES", str(context))
        self.assertNotIn("canon", context.visible_characters[0])
        self.assertEqual(engine.repository.character_canon("guide")["biography"][0]["related_character_ids"], ["player"])

    def test_saved_identity_is_pinned_while_mission_state_changes(self):
        engine = self.load()
        original = engine.repository.character_identity("guide")
        with engine.repository.transaction():
            engine.repository.set_belief("guide", "room", "is_quiet", True, .8)
            engine.repository.adjust_relationship("guide", "player", .1, .1, "A new exchange")
        self.assertEqual(engine.repository.character_identity("guide"), original)
        self.guide["revision"] = 2
        self.guide["canon"]["voice"] = "Revised test voice"
        self.write_profiles()
        resumed = open_prototype(self.root / "save.db", developer_mode=False)
        self.addCleanup(resumed.repository.close)
        self.assertEqual(resumed.repository.character_identity("guide")["revision"], 1)
        self.assertEqual(resumed.repository.character_canon("guide")["voice"], "Test voice")
        fresh = self.load(name="fresh.db")
        self.assertEqual(fresh.repository.character_identity("guide")["revision"], 2)
        self.assertEqual(fresh.repository.character_canon("guide")["voice"], "Revised test voice")

    def test_stable_relationship_history_maps_to_another_scenarios_local_ids(self):
        first = self.load()
        second = deepcopy(self.scenario)
        second["scenario_id"] = "another-chapter"
        second["characters"][0]["id"] = "visitor"
        second["characters"][1]["id"] = "contact"
        second["characters"][1]["relationships"][0]["target_id"] = "visitor"
        second["characters"][1]["goals"][0]["description"] = "A different mission goal."
        engine = self.load(second, "chapter-two.db")
        self.assertEqual(engine.repository.character_identity("contact"), first.repository.character_identity("guide"))
        self.assertEqual(engine.repository.character_canon("contact")["biography"][0]["related_character_ids"], ["visitor"])
        self.assertEqual(engine.repository.character_identity("contact")["canon"]["biography"][0]["related_identity_ids"], ["test_player"])

    def test_player_card_preserves_agency_and_hides_nonpublic_or_forgotten_background(self):
        self.player["canon"]["biography"] = [
            {"id": "public", "summary": "PUBLIC_PLAYER_HISTORY", "visibility": "public", "importance": .9},
            {"id": "private", "summary": "PRIVATE_PLAYER_HISTORY", "visibility": "private"},
            {"id": "forgotten", "summary": "FORGOTTEN_PLAYER_HISTORY", "visibility": "public", "awareness": "forgotten"}]
        self.write_profiles()
        engine = self.load()
        app = GameApplication(engine, developer_mode=False)
        card = " ".join(app.handle("self")[0])
        self.assertIn("Test Player", card)
        self.assertIn("PUBLIC_PLAYER_HISTORY", card)
        self.assertIn("choices and words remain yours", card)
        self.assertNotIn("PRIVATE_PLAYER_HISTORY", card)
        self.assertNotIn("FORGOTTEN_PLAYER_HISTORY", card)
        self.assertEqual(engine.repository.current_time, 0)
        npc = NPCContextBuilder(engine.repository).build("guide")
        self.assertNotIn("PRIVATE_PLAYER_HISTORY", str(npc))
        self.assertNotIn("FORGOTTEN_PLAYER_HISTORY", str(npc))

    def test_conflicting_or_mission_state_definitions_are_rejected_before_save_creation(self):
        for fields in ({"name": "Different Name"}, {"kind": "player"}, {"canon": {}},
                       {"canon_file": "another.json"}, {"parameters": {"risk_tolerance": .9}}):
            with self.subTest(fields=fields):
                data = deepcopy(self.scenario)
                data["characters"][1].update(fields)
                with self.assertRaises(ScenarioError):
                    self.load(data, "invalid.db")
                self.assertFalse((self.root / "invalid.db").exists())
        for field, value in (("goals", []), ("relationships", []), ("location_id", "room"), ("beliefs", [])):
            with self.subTest(field=field):
                profile = deepcopy(self.guide)
                profile[field] = value
                (self.root / "bad.json").write_text(json.dumps(profile))
                with self.assertRaises(IdentityError):
                    load_identity(Path("bad.json"), self.root)

    def test_profile_paths_duplicate_identities_and_invalid_traits_are_rejected(self):
        outside = self.root.parent / (self.root.name + "-outside.json")
        outside.write_text(json.dumps(self.guide))
        self.addCleanup(outside.unlink)
        link = self.root / "escape.json"
        link.symlink_to(outside)
        for reference in (outside, Path("../") / outside.name, Path("escape.json")):
            with self.subTest(reference=str(reference)), self.assertRaises(IdentityError):
                load_identity(reference, self.root)
        duplicate = deepcopy(self.scenario)
        duplicate["characters"].append({"id": "copy", "location_id": "room", "identity_file": "guide.json"})
        with self.assertRaises(ScenarioError):
            self.load(duplicate)
        for traits in ({"risk_tolerance": 2}, {"risk_tolerance": True}, {"autonomy_enabled": True}, {"temperament": []}):
            profile = deepcopy(self.guide)
            profile["personality"] = traits
            (self.root / "bad.json").write_text(json.dumps(profile))
            with self.subTest(traits=traits), self.assertRaises(IdentityError):
                load_identity(Path("bad.json"), self.root)

    def test_relay_crisis_remains_separate_and_player_card_is_read_only(self):
        service = RelayService(self.root / "relay.db")
        before = service.view()
        self.assertEqual(before["player"]["name"], "Player")
        result = service.submit("self", "self", 0)
        self.assertIn("You play Player", " ".join(result["messages"]))
        self.assertEqual(service.view()["guidance"]["phase"], "investigating")
        self.assertIn("Mission time: 0 minutes.", result["status"])
        self.assertIn("Director Sable", str(before))
        self.assertNotIn("Matilda", str(before))

    def test_new_game_archive_and_backup_preserve_previous_identity_snapshot(self):
        resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
        data = json.loads(resource.read_text())
        player = next(item for item in data["characters"] if item["kind"] == "player")
        for key in ("name", "kind", "appearance"):
            player.pop(key, None)
        player["identity_file"] = "player.json"
        self.load(data)
        service = RelayService(self.root / "save.db")
        service.new_game("reset", 0, confirmed=True)
        backup = snapshot(self.root / "save.db", self.root / "backup.db")
        with GameRepository(backup) as repository:
            self.assertIsNone(repository.character_identity("player"))
            archive = json.loads(repository.connection.execute("SELECT state_json FROM playthrough_archives").fetchone()[0])
            identity = archive["tables"]["character_identity"][0]
            self.assertEqual(identity["identity_id"], "test_player")
            self.assertEqual(json.loads(identity["snapshot_json"])["name"], "Test Player")
