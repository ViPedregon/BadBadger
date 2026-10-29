"""Established continuity stays distinct from private familiarity and future arcs."""

from importlib.resources import as_file, files
import json
from pathlib import Path
import tempfile
import unittest

from badbadger.application import GameApplication, open_prototype
from badbadger.agents.context import NPCContextBuilder
from badbadger.scenarios.loader import load_scenario


class GravenholtLoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "characters").mkdir()
        resource = files("badbadger.scenarios").joinpath("characters")
        self.profiles = {}
        for filename in ("victor-gravenholt", "matilda-ash", "horace"):
            raw = resource.joinpath(filename + ".json").read_text()
            (self.root / "characters" / (filename + ".json")).write_text(raw)
            self.profiles[filename] = json.loads(raw)
        scenario = {"scenario_id": "lore-integration-test", "locations": [
            {"id": "room", "name": "Test Room", "description": "A noncanonical test scene."}],
            "characters": [
                {"id": "victor", "location_id": "room", "identity_file": "characters/victor-gravenholt.json"},
                {"id": "matilda", "location_id": "room", "identity_file": "characters/matilda-ash.json", "parameters": {"autonomy_enabled": False}},
                {"id": "horace", "location_id": "room", "identity_file": "characters/horace.json", "parameters": {"autonomy_enabled": False}}]}
        self.source = self.root / "scene.json"
        self.source.write_text(json.dumps(scenario))
        self.database = self.root / "save.db"
        self.engine = load_scenario(self.source, self.database)
        self.addCleanup(self.engine.repository.close)

    def test_player_card_preserves_victors_background_without_forcing_the_arc(self):
        app = GameApplication(self.engine)
        card = " ".join(app.handle("self")[0])
        self.assertIn("Victor Gravenholt", card)
        self.assertIn("southern kingdoms", card)
        self.assertIn("languages, diplomacy, history", card)
        self.assertIn("not an exceptional warrior", card)
        self.assertIn("choices and words remain yours", card)
        for private in ("Bear", "Tracks", "romance", "Elena", "Valenbruck"):
            self.assertNotIn(private, card)

    def test_companions_have_distinct_voices_and_familiarity_without_private_nicknames(self):
        matilda = NPCContextBuilder(self.engine.repository).build("matilda", "Victor Horace trust friendship ceremony")
        horace = NPCContextBuilder(self.engine.repository).build("horace", "Victor Matilda trust friendship military")
        self.assertIn("Reserved", matilda.canon["voice"])
        self.assertIn("Calm", horace.canon["voice"])
        self.assertIn("already trusts", matilda.canon["personality"]["trust_style"])
        self.assertNotEqual(matilda.canon["voice"], horace.canon["voice"])
        for context in (matilda, horace):
            projected = json.dumps(context.canon) + json.dumps(context.biography)
            for private in ("Bear", "Tracks", "Alden", "Brenner", "Elena", "Valenbruck", "romance"):
                self.assertNotIn(private, projected)
        self.assertTrue(any("sibling" in item["guidance"] for item in horace.biography))

    def test_provisional_details_remain_provisional_and_missing_history_is_not_filled_in(self):
        for profile in self.profiles.values():
            self.assertIn("provisional", profile["appearance"]["age"])
        captain = self.profiles["horace"]
        self.assertEqual(captain["name"], "Captain Horace")
        self.assertIn("Alden and Brenner are candidates", captain["author_notes"])
        matilda = self.profiles["matilda-ash"]
        self.assertIn("Family history and early training are explicitly unestablished", matilda["author_notes"])
        self.assertIn("earned her position", str(matilda["canon"]["biography"]))

    def test_house_and_role_boundaries_are_encoded_without_promoting_horace(self):
        for profile in self.profiles.values():
            public = " ".join(fragment["summary"] for fragment in profile["canon"]["biography"] if fragment["visibility"] == "public")
            self.assertIn("We Do Not Yield", public)
            self.assertIn("bone-colored shield", public)
            self.assertIn("Stronger people matter more", public)
        horace = self.profiles["horace"]
        self.assertIn("rather than commanding Blackbarrow", str(horace["canon"]["biography"]))
        self.assertIn("older brother", " ".join(horace["canon"]["boundaries"]))
        self.assertIn("old sword", horace["appearance"]["weapon"])

    def test_supplied_gifts_and_private_familiarity_survive_save_resume(self):
        resumed = open_prototype(self.database, developer_mode=False)
        self.addCleanup(resumed.repository.close)
        matilda = resumed.repository.character_identity("matilda")
        self.assertIn("broken-back seax", str(matilda["canon"]["biography"]))
        self.assertIn("black wool ceremonial gown", matilda["appearance"]["ceremonial_attire"])
        horace = resumed.repository.character_identity("horace")
        private = [entry["summary"] for entry in horace["canon"]["biography"] if entry["visibility"] == "private"]
        self.assertTrue(any("Tracks" in entry for entry in private))
        self.assertTrue(any("Bear" in entry and "generally can" in entry for entry in private))
        local = resumed.repository.character_canon("horace")["biography"]
        self.assertTrue(any("matilda" in item["related_character_ids"] for item in local))

    def test_future_continuity_is_not_present_as_a_memory_or_extra_party_member(self):
        self.assertEqual(len(self.engine.repository.characters_at("room")), 3)
        for character in ("victor", "matilda", "horace"):
            self.assertEqual(self.engine.repository.memories_for(character), [])
        ledger = json.loads((Path(__file__).resolve().parents[1] / "lore/house-gravenholt.json").read_text())
        self.assertEqual(ledger["continuity"]["romance"]["milestones"], [])
        self.assertEqual(ledger["continuity"]["later_adventure"]["introduces"], "Elena")
        self.assertIsNone(ledger["unresolved"]["horace_surname"]["selected"])
