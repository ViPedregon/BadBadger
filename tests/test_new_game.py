"""New playthroughs preserve archives and reject stale or duplicated requests."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from badbadger.backup import snapshot
from badbadger.db.repository import GameRepository
from badbadger.service import RelayService, TurnConflict


class NewGameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "relay.db"
        self.service = RelayService(self.path)
        self.old_receipt = self.service.submit("move", "go to Relay Chamber", 0)

    def test_restart_archives_state_and_clears_only_the_current_transcript(self):
        result = self.service.new_game("new", 1, confirmed=True)
        self.assertEqual(result["revision"], 2)
        resumed = RelayService(self.path)
        view = resumed.view()
        self.assertEqual(view["revision"], 2)
        self.assertEqual(view["transcript"], [])
        self.assertIn("Mission time: 0 minutes.", view["status"])
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.get_player()["location_id"], "operations")
            self.assertEqual(repository.get_fact("crisis", "outcome"), "undecided")
            archive = json.loads(repository.connection.execute("SELECT state_json FROM playthrough_archives").fetchone()[0])
            player = next(row for row in archive["tables"]["characters"] if row["id"] == "player")
            self.assertEqual(player["location_id"], "relay")
            self.assertEqual(archive["end_revision"], 1)
        resumed.submit("next", "wait 1", 2)
        self.assertEqual(len(resumed.view()["transcript"]), 1)

    def test_retry_reset_and_old_turn_cannot_modify_new_playthrough(self):
        result = self.service.new_game("new", 1, confirmed=True)
        self.assertEqual(self.service.new_game("new", 1, confirmed=True), result)
        self.assertEqual(self.service.submit("move", "go to Relay Chamber", 0), self.old_receipt)
        with self.assertRaises(TurnConflict):
            self.service.submit("stale", "wait 1", 1)
        with self.assertRaises(TurnConflict):
            self.service.new_game("stale-reset", 1, confirmed=True)
        with self.assertRaises(TurnConflict):
            self.service.submit("new", "look", 2)
        with self.assertRaises(TurnConflict):
            self.service.new_game("move", 2, confirmed=True)
        self.assertEqual(self.service.view()["revision"], 2)
        self.assertEqual(self.service.view()["transcript"], [])

    def test_requires_confirmation_and_rolls_back_failed_reset(self):
        old = self.service.view()
        with self.assertRaises(ValueError):
            self.service.new_game("new", 1)
        with GameRepository(self.path) as repository:
            repository.connection.execute("""CREATE TRIGGER fail_reset BEFORE INSERT ON player_turns
                WHEN NEW.operation='new-game' BEGIN SELECT RAISE(ABORT, 'failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.new_game("new", 1, confirmed=True)
        self.assertEqual(self.service.view(), old)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.connection.execute("SELECT count(*) FROM playthrough_archives").fetchone()[0], 0)

    def test_simultaneous_reset_and_turn_only_one_commits(self):
        def run(reset):
            try:
                if reset:
                    return self.service.new_game("reset", 1, confirmed=True)
                return self.service.submit("wait", "wait 1", 1)
            except TurnConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (True, False)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.service.view()["revision"], 2)

    def test_backup_after_multiple_resets_preserves_archive_and_revisions(self):
        self.service.new_game("new", 1, confirmed=True)
        self.service.new_game("new-again", 2, confirmed=True)
        backup = snapshot(self.path, Path(self.temp.name) / "backup.db")
        self.assertEqual(RelayService(backup).view(), self.service.view())
        with GameRepository(backup) as repository:
            self.assertEqual(repository.connection.execute("SELECT count(*) FROM playthrough_archives").fetchone()[0], 2)

    def test_old_deployed_save_migrates_without_losing_turns(self):
        before = self.service.view()
        with GameRepository(self.path) as repository:
            repository.connection.execute("ALTER TABLE player_session DROP COLUMN game_start_revision")
            repository.connection.execute("ALTER TABLE player_turns DROP COLUMN operation")
            repository.connection.execute("DROP TABLE playthrough_archives")
        migrated = RelayService(self.path)
        self.assertEqual(migrated.view(), before)
        migrated.new_game("new", 1, confirmed=True)
        self.assertEqual(migrated.view()["transcript"], [])

    def test_new_game_after_ended_chapter_restores_active_goals_and_choices(self):
        for revision, command in enumerate(("tell Orr thank you", "tell Orr thanks",
                                             "tell Orr broadcast the distress packet", "wait 10 minutes"), 1):
            self.service.submit(str(revision), command, revision)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.simulation_status, "ended")
        self.service.new_game("reset-ended", 5, confirmed=True)
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.simulation_status, "active")
            self.assertEqual(repository.current_time, 0)
            self.assertEqual(repository.get_fact("crisis", "outcome"), "undecided")
            for npc in repository.list_npcs():
                self.assertTrue(all(goal["status"] == "active" for goal in repository.goals_for(npc["id"], include_resolved=True)))
        result = self.service.submit("move-again", "go to Relay Chamber", 6)
        self.assertIn("You travel to Relay Chamber.", result["messages"])
