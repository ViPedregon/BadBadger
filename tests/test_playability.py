"""Player guidance, knowledge-safe endings and actionable conversation feedback."""

from pathlib import Path
import tempfile
import unittest

from badbadger.db.repository import GameRepository
from badbadger.service import RelayService


class PlayabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "save.db"
        self.service = RelayService(self.path)
        self.revision = 0

    def command(self, text):
        result = self.service.submit(str(self.revision), text, self.revision)
        self.revision = result["revision"]
        return result

    def test_start_explains_stakes_costs_and_reading_is_free(self):
        view = self.service.view()
        self.assertIn("each plan sacrifices", " ".join(view["opening"]))
        self.assertIn("Other crew members can act", " ".join(view["opening"]))
        choices = view["guidance"]["suggestions"]
        self.assertTrue(any(item["command"] == "examine situation table" for item in choices))
        self.assertTrue(any(item["consequential"] and "seal the relay" in item["command"] for item in choices))
        for text in ("look", "objective", "people", "journal", "choices", "summary", "help", "timing"):
            self.command(text)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.current_time, 0)

    def test_normal_questions_and_direct_address_work_offline(self):
        answer = self.command("Director Sable, what do you think the risks are?")
        self.assertIn("sample is unstable", " ".join(answer["messages"]))
        self.assertIn("My priority", " ".join(answer["messages"]))
        self.command("move to Relay Chamber")
        answer = self.command("Orr, what do you think?")
        self.assertIn("I'm not certain", " ".join(answer["messages"]))

    def test_refused_request_does_not_claim_success_in_reply_or_saved_dialogue(self):
        self.command("go to Relay Chamber")
        result = self.command("tell Orr broadcast the distress packet")
        text = " ".join(result["messages"])
        self.assertIn("does not trust", text)
        self.assertIn("No action was taken", text)
        self.assertNotIn("I'll broadcast", text)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.get_fact("crisis", "outcome"), "undecided")
            dialogue = repository.recent_dialogue("orr")
            self.assertTrue(any(item["text"] == "I haven't taken that action." for item in dialogue))
            self.assertFalse(repository.player_consequences())

    def test_all_three_outcomes_have_visible_aftermath_and_safe_ending(self):
        cases = [("sable", "sable_seal_relay", "operations", "transmitter falls silent"),
                 ("orr", "orr_broadcast", "relay", "rescue acknowledgment"),
                 ("lin", "lin_purge_sample", "relay", "years of research")]
        for actor, action, location, expected in cases:
            with self.subTest(action=action):
                self.service.new_game("reset-" + actor, self.revision, confirmed=True)
                self.revision += 1
                with GameRepository(self.path) as repository:
                    with repository.transaction():
                        repository.move_character("player", location)
                        repository.move_character(actor, location)
                        # This fixture exercises each actual action/effect set;
                        # no outcomes or visible recap text are invented in tests.
                        if actor == "orr":
                            repository.adjust_relationship("orr", "player", .2, 0, "Earned cooperation")
                        accepted, _ = repository.apply_npc_action(actor, action, requested_by_player=True)
                        self.assertTrue(accepted)
                guidance = self.service.view()["guidance"]
                self.assertEqual(guidance["phase"], "aftermath")
                self.assertIn("10 mission minutes", guidance["message"])
                self.assertIn(expected, " ".join(guidance["recap"]))
                self.assertIn("You asked", " ".join(guidance["recap"]))
                self.command("wait 10 minutes")
                guidance = self.service.view()["guidance"]
                self.assertEqual(guidance["phase"], "ended")
                self.assertIn("Chapter complete", " ".join(guidance["recap"]))
                self.assertIn(expected, " ".join(self.command("summary")["messages"]))
                self.assertNotIn("goal_id", str(guidance))
                self.assertNotIn("decision_score", str(guidance))

    def test_offscreen_resolution_does_not_leak_into_recap(self):
        with GameRepository(self.path) as repository:
            with repository.transaction():
                repository.move_character("lin", "relay")
                accepted, _ = repository.apply_npc_action("lin", "lin_purge_sample", autonomous=True)
                self.assertTrue(accepted)
        guidance = self.service.view()["guidance"]
        self.assertEqual(guidance["phase"], "investigating")
        self.assertEqual(guidance["recap"], [])
        self.command("wait 10 minutes")
        recap = " ".join(self.service.view()["guidance"]["recap"])
        self.assertIn("did not record", recap)
        self.assertNotIn("purged", recap)
        self.assertNotIn("permanently contained", recap)

    def test_autonomous_outcome_does_not_credit_player_and_new_game_clears_it(self):
        with GameRepository(self.path) as repository:
            with repository.transaction():
                repository.apply_npc_action("sable", "sable_seal_relay", autonomous=True)
        recap = " ".join(self.service.view()["guidance"]["recap"])
        self.assertIn("Director Sable seals", recap)
        self.assertNotIn("You asked", recap)
        self.service.new_game("reset", 0, confirmed=True)
        guidance = self.service.view()["guidance"]
        self.assertEqual(guidance["phase"], "investigating")
        self.assertEqual(guidance["recap"], [])
