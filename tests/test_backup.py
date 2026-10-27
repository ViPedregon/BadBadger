"""Verify recoverable game state, live-WAL capture and safe copy failures."""

from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

from badbadger.backup import snapshot
from badbadger.service import RelayService


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.save = self.root / "relay.db"
        self.service = RelayService(self.save)

    def test_backup_restore_resumes_transcript_and_request_receipts(self):
        response = self.service.submit("move", "go to Relay Chamber", 0)
        original = self.service.view()
        backup = snapshot(self.save, self.root / "backups" / "snapshot.db")
        self.service.submit("wait", "wait 1", 1)
        restored = snapshot(backup, self.root / "restored.db")
        resumed = RelayService(restored)
        self.assertEqual(resumed.view(), original)
        self.assertEqual(resumed.submit("move", "go to Relay Chamber", 0), response)
        self.assertEqual(resumed.view()["revision"], 1)
        self.assertEqual(self.service.view()["revision"], 2)
        if os.name == "posix":
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_live_wal_commits_are_included(self):
        connection = sqlite3.connect(self.save)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        connection.execute("BEGIN")
        connection.execute("SELECT current_time_minutes FROM simulation").fetchone()
        self.service.submit("wait", "wait 2", 0)
        self.assertTrue(Path(str(self.save) + "-wal").exists())
        restored = snapshot(self.save, self.root / "wal-backup.db")
        self.assertEqual(RelayService(restored).view(), self.service.view())

    def test_missing_or_invalid_source_does_not_publish_backup(self):
        output = self.root / "output.db"
        for source in (self.root / "missing.db", self.root / "invalid.db"):
            if source.name == "invalid.db":
                source.write_text("not a database")
            with self.assertRaises((OSError, sqlite3.Error, ValueError)):
                snapshot(source, output)
            self.assertFalse(output.exists())
        self.assertFalse((self.root / "missing.db").exists())
        self.assertEqual(list(self.root.glob(".badbadger-*")), [])

    def test_existing_destination_and_source_are_never_overwritten(self):
        destination = self.root / "existing.db"
        destination.write_bytes(b"keep this")
        before = self.save.read_bytes()
        for output in (destination, self.save):
            with self.assertRaises(FileExistsError):
                snapshot(self.save, output)
        self.assertEqual(destination.read_bytes(), b"keep this")
        self.assertEqual(self.save.read_bytes(), before)

    def test_unrelated_database_is_rejected(self):
        source = self.root / "other.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE other (id INTEGER)")
        with self.assertRaises(sqlite3.Error):
            snapshot(source, self.root / "output.db")
        self.assertFalse((self.root / "output.db").exists())
