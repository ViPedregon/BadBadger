"""Avoid queued goal recaps duplicating an NPC's direct aftermath answer."""

from importlib.resources import as_file, files
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from badbadger.application import open_prototype
from badbadger.agents.context import NPCContextBuilder
from badbadger.agents.npc import DeterministicNPCBackend, NPCResponse


class ReactionDialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
        with as_file(resource) as scenario:
            self.app = open_prototype(Path(self.temp.name) / "save.db", scenario=scenario, developer_mode=False)
        self.repository = self.app.repository
        self.addCleanup(self.repository.close)
        with self.repository.transaction():
            self.repository.move_character("player", "relay")
            self.repository.move_character("lin", "relay")
            accepted, _ = self.repository.apply_npc_action("lin", "lin_purge_sample", autonomous=True)
            self.assertTrue(accepted)

    def test_direct_answer_covers_only_existing_goal_recaps_for_that_npc(self):
        original = self.repository.pending_goal_reaction_ids("lin")
        others = self.repository.pending_goal_reaction_ids("orr")
        self.assertTrue(original)
        self.assertTrue(others)
        backend = Mock()
        backend.respond.return_value = NPCResponse("I purged it to protect everyone on the station.")
        self.app.dialogue.backend = backend
        messages, _ = self.app.handle("ask Dr. Lin why they purged the sample")
        self.assertEqual(sum(message.startswith('Dr. Lin: "') for message in messages), 1)
        self.assertEqual(self.repository.pending_goal_reaction_ids("lin"), [])
        self.assertEqual(self.repository.pending_goal_reaction_ids("orr"), others)
        self.assertEqual(self.repository.get_fact("crisis", "outcome"), "sample_purged")
        history = self.repository.connection.execute(
            "SELECT result_json FROM history WHERE record_type='npc_reactions_covered'"
        ).fetchone()
        self.assertIsNotNone(history)
        self.assertEqual(backend.respond.call_count, 1)

    def test_unrelated_question_and_degraded_reply_preserve_recap(self):
        for text, degraded in (("ask Dr. Lin hello", False), ("ask Dr. Lin why", True)):
            with self.subTest(text=text):
                original = self.repository.pending_goal_reaction_ids("lin")
                backend = Mock()
                backend.respond.return_value = NPCResponse("I have nothing to add.", degraded=degraded)
                self.app.dialogue.backend = backend
                self.app.dialogue.converse("lin", text)
                self.assertEqual(self.repository.pending_goal_reaction_ids("lin"), original)

    def test_covering_old_recaps_preserves_new_events_and_non_goal_cues(self):
        original = self.repository.pending_goal_reaction_ids("lin")
        with self.repository.transaction():
            new = self.repository.queue_reaction("lin", "goal_resolved", "A new outcome.")
            arrival = self.repository.queue_reaction("lin", "character_arrived", "Someone arrived.")
            self.repository.cover_goal_reactions("lin", original)
        self.assertEqual(self.repository.pending_goal_reaction_ids("lin"), [new])
        status = self.repository.connection.execute(
            "SELECT status FROM npc_reaction_cues WHERE id=?", (arrival,)
        ).fetchone()[0]
        self.assertEqual(status, "pending")

    def test_offline_goal_reaction_uses_first_person_without_internal_summary(self):
        context = NPCContextBuilder(self.repository).build("lin", interaction_mode="decision")
        response = DeterministicNPCBackend().respond(context, "ENGINE DECISION TICK")
        self.assertEqual(response.dialogue, "That is the outcome I hoped for.")
        self.assertEqual(response.proposed_actions[0].kind, "speak")
        self.assertNotIn("Lin destroyed", response.dialogue)
