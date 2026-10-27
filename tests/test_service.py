"""Persistence, privacy and turn-boundary checks for the browser service seam."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from badbadger.application import GameApplication
from badbadger.db.repository import GameRepository
from badbadger.engine.autonomy import AutonomyScheduler
from badbadger.service import RelayService, TurnConflict


class RelayServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "relay.db"
        self.service = RelayService(self.path)

    def test_replay_resume_and_request_id_conflict(self):
        result = self.service.submit("move", "go to Relay Chamber", 0)
        resumed = RelayService(self.path)
        self.assertEqual(resumed.submit("move", "go to Relay Chamber", 0), result)
        self.assertEqual(resumed.view()["revision"], 1)
        self.assertEqual(len(resumed.view()["transcript"]), 1)
        with self.assertRaises(TurnConflict):
            resumed.submit("move", "wait 1", 1)
        with self.assertRaises(TurnConflict):
            resumed.submit("stale", "wait 1", 0)
        self.assertEqual(resumed.view()["revision"], 1)

    def test_simultaneous_requests_execute_once(self):
        def send(index):
            try:
                return self.service.submit(str(index), "wait 1", 0)
            except TurnConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(sum(item is not None for item in results), 1)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.current_time, 1)

    def test_simultaneous_retries_return_same_receipt(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: self.service.submit("same", "wait 1", 0), range(2)
            ))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.service.view()["revision"], 1)

    def test_failure_after_main_action_rolls_back_entire_turn(self):
        with GameRepository(self.path) as repository:
            initial_history = repository.connection.execute(
                "SELECT count(*) FROM history"
            ).fetchone()[0]
        with patch.object(AutonomyScheduler, "process_due", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.service.submit("retry", "wait 2", 0)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.current_time, 0)
            self.assertEqual(repository.connection.execute(
                "SELECT count(*) FROM history"
            ).fetchone()[0], initial_history)
        self.assertEqual(self.service.view()["transcript"], [])
        self.service.submit("retry", "wait 2", 0)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.current_time, 2)

    def test_receipt_write_failure_rolls_back_engine_and_autonomy(self):
        with GameRepository(self.path) as repository:
            repository.connection.execute("""
                CREATE TRIGGER fail_receipt BEFORE INSERT ON player_turns
                BEGIN SELECT RAISE(ABORT, 'receipt failed'); END
            """)
        with self.assertRaises(Exception):
            self.service.submit("retry", "go to Relay Chamber", 0)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.current_time, 0)
            self.assertEqual(repository.get_player()["location_id"], "operations")
            self.assertEqual(repository.connection.execute(
                "SELECT count(*) FROM dialogue"
            ).fetchone()[0], 0)
            repository.connection.execute("DROP TRIGGER fail_receipt")
        self.assertEqual(self.service.view()["revision"], 0)
        self.service.submit("retry", "go to Relay Chamber", 0)

    def test_private_output_and_hidden_description_are_not_exposed(self):
        with GameRepository(self.path) as repository:
            with repository.transaction():
                repository.set_fact("secret_panel", "description", "PRIVATE_SENTINEL", hidden=True)
        result = self.service.submit("inspect", "npc Orr", 0)
        self.assertIn("only in developer mode", " ".join(result["messages"]))
        self.service.submit("examine", "examine secret panel", 1)
        with patch.object(GameApplication, "chapter_summary", side_effect=AssertionError):
            self.service.submit("summary", "summary", 2)
        public = json.dumps(self.service.view())
        self.assertNotIn("PRIVATE_SENTINEL", public)
        self.assertNotIn("npc_parameters", public)
        self.assertNotIn("available_disclosures", public)

    def test_complete_rescue_and_resume(self):
        commands = ["go to Relay Chamber", "tell Orr thank you", "tell Orr thanks",
                    "tell Orr broadcast the distress packet", "wait 10 minutes"]
        for revision, command in enumerate(commands):
            self.service.submit(str(revision), command, revision)
        resumed = RelayService(self.path)
        self.assertEqual(resumed.view()["revision"], len(commands))
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.simulation_status, "ended")
            self.assertEqual(repository.get_fact("crisis", "outcome"), "distress_broadcast")

    def test_invalid_requests_do_not_change_save(self):
        for args in [("", "look", 0), ("id", " " * 5, 0),
                     ("id", "look", True), ("id", "x" * 2001, 0)]:
            with self.assertRaises(ValueError):
                self.service.submit(*args)
        self.assertEqual(self.service.view()["revision"], 0)


if __name__ == "__main__":
    unittest.main()
