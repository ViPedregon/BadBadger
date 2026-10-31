"""Archive swaps preserve worlds, transcripts and global request identity."""
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from badbadger.db.repository import GameRepository
from badbadger.service import RelayService, TurnConflict
from badbadger.backup import snapshot


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "game.db"
        self.game = RelayService(self.path, scenario="blackbarrow")
        self.turn = self.game.submit("route", "tell Matilda guide the high track", 0)
        self.original = self.game.view()
        self.game.new_game("new", 1, confirmed=True, scenario="relay_crisis")

    def test_resume_retry_switch_back_and_backup(self):
        listing = self.game.view()["archives"]
        self.assertEqual(set(listing[0]), {"id", "saved_at", "title"})
        self.assertEqual(listing[0]["id"], 1)
        response = self.game.resume_archive("resume", 2, 1, confirmed=True)
        self.assertEqual(self.game.resume_archive("resume", 2, 1, confirmed=True), response)
        restored = self.game.view()
        for key in ("scenario", "guidance", "transcript", "status", "player", "opening"):
            self.assertEqual(restored[key], self.original[key])
        self.assertEqual(self.game.submit("route", "tell Matilda guide the high track", 0), self.turn)
        self.game.submit("guard", "Horace, guard the grain", 3)
        guarded = self.game.view()
        self.game.resume_archive("relay-back", 4, 2, confirmed=True)
        self.assertEqual(self.game.view()["scenario"]["key"], "relay_crisis")
        self.assertEqual(self.game.view()["transcript"], [])
        self.game.resume_archive("guarded-back", 5, 4, confirmed=True)
        self.assertEqual(self.game.view()["transcript"], guarded["transcript"])
        self.assertEqual(self.game.view()["guidance"], guarded["guidance"])
        backup = snapshot(self.path, Path(self.temp.name) / "backup.db")
        self.assertEqual(RelayService(backup).view(), self.game.view())
        self.game.new_game("fresh-again", 6, confirmed=True)
        self.assertEqual(self.game.view()["transcript"], [])
        self.game.resume_archive("back-after-new", 7, 6, confirmed=True)
        self.assertEqual(self.game.view()["transcript"], guarded["transcript"])

    def test_stale_confirmation_unknown_and_receipt_collisions(self):
        before = self.game.view()
        for kwargs in ({"confirmed": False}, {"confirmed": True, "archive_id": 999}):
            with self.assertRaises(ValueError):
                self.game.resume_archive("bad", 2, **{"archive_id": 1, **kwargs})
        with self.assertRaises(TurnConflict):
            self.game.resume_archive("new", 2, 1, confirmed=True)
        with self.assertRaises(TurnConflict):
            self.game.resume_archive("stale", 1, 1, confirmed=True)
        self.assertEqual(self.game.view(), before)
        self.game.resume_archive("restore", 2, 1, confirmed=True)
        with self.assertRaises(TurnConflict):
            self.game.resume_archive("restore", 3, 2, confirmed=True)
        with self.assertRaises(TurnConflict):
            self.game.submit("restore", "look", 3)

    def test_legacy_archive_uses_original_transcript_range(self):
        with GameRepository(self.path) as repository:
            with repository.transaction():
                archive = json.loads(repository.connection.execute("SELECT state_json FROM playthrough_archives").fetchone()[0])
                del archive["transcript"]
                del archive["scenario_id"]
                repository.connection.execute("UPDATE playthrough_archives SET state_json=?", (json.dumps(archive),))
        self.game.resume_archive("legacy", 2, 1, confirmed=True)
        self.assertEqual(self.game.view()["transcript"], self.original["transcript"])

    def test_concurrent_duplicate_resume_archives_current_game_once(self):
        def resume(_):
            return self.game.resume_archive("same", 2, 1, confirmed=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(resume, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.game.view()["revision"], 3)
        self.assertEqual(len(self.game.view()["archives"]), 2)

    def test_invalid_snapshot_rolls_back_current_world_and_archive(self):
        with GameRepository(self.path) as repository:
            with repository.transaction():
                archive = json.loads(repository.connection.execute("SELECT state_json FROM playthrough_archives").fetchone()[0])
                archive["tables"]["characters"][0]["location_id"] = "missing-location"
                repository.connection.execute("UPDATE playthrough_archives SET state_json=?", (json.dumps(archive),))
        before = self.game.view()
        with self.assertRaises(ValueError):
            self.game.resume_archive("broken", 2, 1, confirmed=True)
        self.assertEqual(self.game.view(), before)
