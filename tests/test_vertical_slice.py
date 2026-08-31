"""End-to-end tests for the deterministic SQLite vertical slice."""

import tempfile
import unittest
from pathlib import Path

from badbadger.application import create_prototype
from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction, MoveAction, WaitAction


class VerticalSliceTests(unittest.TestCase):
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
                    "SELECT status FROM scheduled_events"
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
            engine.repository.close()


if __name__ == "__main__":
    unittest.main()
