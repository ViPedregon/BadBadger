"""Whole tester inventories remain recoverable without changing live saves."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from badbadger.backup import backup_testers, restore_testers, snapshot
from badbadger.service import RelayService
from badbadger.testers import tester_scope


class TesterBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.saves = self.root / "players"
        self.names = [tester_scope(user) + ".db" for user in ("alice", "retired-tester")]
        self.games = [RelayService(self.saves / name) for name in self.names]
        self.games[0].submit("same", "go to Relay Chamber", 0)
        self.games[1].submit("same", "wait 2", 0)

    def test_batch_restore_recovers_each_world_receipts_and_archives(self):
        self.games[0].new_game("new", 1, confirmed=True)
        before = [game.view() for game in self.games]
        batch = backup_testers(self.saves, self.root / "batch")
        manifest = json.loads((batch / "manifest.json").read_text())
        self.assertEqual(len(manifest["saves"]), 2)
        self.games[1].submit("later", "wait 1", 1)
        restored = restore_testers(batch, self.root / "recovery")
        for name, expected in zip(self.names, before):
            self.assertEqual(RelayService(restored / name).view(), expected)
        self.assertEqual(self.games[1].view()["revision"], 2)
        first = RelayService(restored / self.names[0])
        first.resume_archive("recover-archive", 2, 1, confirmed=True)
        self.assertEqual(first.view()["transcript"][0]["command"], "go to Relay Chamber")
        second = RelayService(restored / self.names[1])
        response = second.submit("same", "wait 2", 0)
        self.assertEqual(response["revision"], 1)
        self.assertEqual(second.view()["revision"], 1)

    def test_corruption_and_path_escape_rejected_before_restore_directory_creation(self):
        batch = backup_testers(self.saves, self.root / "batch")
        save = batch / self.names[0]
        original = save.read_bytes()
        save.write_bytes(original + b"corrupt")
        with self.assertRaises(ValueError):
            restore_testers(batch, self.root / "recovery")
        self.assertFalse((self.root / "recovery").exists())
        save.write_bytes(original)
        manifest = json.loads((batch / "manifest.json").read_text())
        manifest["saves"][0]["file"] = "../outside.db"
        (batch / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            restore_testers(batch, self.root / "recovery")

    def test_existing_destinations_empty_inventory_and_nested_backup_rejected(self):
        backup_testers(self.saves, self.root / "batch")
        with self.assertRaises(FileExistsError):
            backup_testers(self.saves, self.root / "batch")
        with self.assertRaises(ValueError):
            backup_testers(self.saves, self.saves / "nested")
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            backup_testers(empty, self.root / "empty-backup")
        with self.assertRaises(FileExistsError):
            restore_testers(self.root / "batch", self.saves)

    def test_failed_batch_never_has_complete_manifest(self):
        calls = 0
        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            return snapshot(source, destination)
        with patch("badbadger.backup.snapshot", side_effect=fail_second):
            with self.assertRaises(OSError):
                backup_testers(self.saves, self.root / "partial")
        self.assertFalse((self.root / "partial/manifest.json").exists())
        with self.assertRaises(OSError):
            restore_testers(self.root / "partial", self.root / "recovery")

    def test_inventory_change_does_not_publish_complete_batch(self):
        added = False
        def add_account(source, destination):
            nonlocal added
            result = snapshot(source, destination)
            if not added:
                added = True
                RelayService(self.saves / (tester_scope("new-player") + ".db"))
            return result
        with patch("badbadger.backup.snapshot", side_effect=add_account):
            with self.assertRaisesRegex(ValueError, "inventory changed"):
                backup_testers(self.saves, self.root / "changed")
        self.assertFalse((self.root / "changed/manifest.json").exists())

    def test_unexpected_database_and_omitted_manifest_entries_rejected(self):
        other = self.saves / "unmanaged.db"
        other.write_bytes(b"not a tester save")
        with self.assertRaises(ValueError):
            backup_testers(self.saves, self.root / "unexpected")
        self.assertFalse((self.root / "unexpected").exists())
        other.unlink()
        batch = backup_testers(self.saves, self.root / "batch")
        path = batch / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["saves"].pop()
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "inventory"):
            restore_testers(batch, self.root / "omitted")
        self.assertFalse((self.root / "omitted").exists())
