"""Authenticated snapshot export and local validation without real credentials."""

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from badbadger.download_backup import download, NoRedirects
from badbadger.service import RelayService

try:
    from fastapi.testclient import TestClient
    from fastapi import HTTPException
    from badbadger.web import create_app, RateLimit
except ImportError:
    TestClient = None


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "save.db"
        game = RelayService(self.source)
        game.submit("move", "go to Relay Chamber", 0)
        self.expected = game.view()
        self.data = self.source.read_bytes()

    def response(self, data=None, digest=None):
        data = self.data if data is None else data
        stream = io.BytesIO(data)
        stream.headers = {"X-BadBadger-SHA256": digest or hashlib.sha256(data).hexdigest()}
        return stream

    def test_verified_download_resumes_and_preserves_existing_backups(self):
        destination = self.root / "backups"
        opener = Mock()
        opener.open.side_effect = lambda *a, **k: self.response()
        with patch("badbadger.download_backup.build_opener", return_value=opener):
            first = download("https://game.example", destination, "t" * 40)
            second = download("https://game.example", destination, "t" * 40)
        self.assertNotEqual(first, second)
        self.assertEqual(RelayService(first).view(), self.expected)
        self.assertEqual(RelayService(first).submit("move", "go to Relay Chamber", 0)["revision"], 1)
        self.assertEqual(len(list(destination.glob("*.db"))), 2)
        self.assertEqual(list(destination.glob(".download-*")), [])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://game.example/api/backup")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + "t" * 40)

    def test_corrupt_truncated_invalid_or_oversized_download_not_published(self):
        cases = [(self.data, "0" * 64, 10**7), (b"not sqlite", None, 10**7),
                 (self.data, None, 10), (self.data[:100], hashlib.sha256(self.data).hexdigest(), 10**7)]
        for index, (data, digest, limit) in enumerate(cases):
            destination = self.root / str(index)
            opener = Mock()
            opener.open.return_value = self.response(data, digest)
            with patch("badbadger.download_backup.build_opener", return_value=opener):
                with self.assertRaises(Exception):
                    download("https://game.example", destination, "t" * 40, max_bytes=limit)
            self.assertEqual(list(destination.iterdir()), [])

    def test_unsafe_origins_and_redirects_rejected(self):
        for origin in ("http://example.com", "https://user:secret@example.com", "https://example.com/", "https://example.com?token=x"):
            with self.assertRaises(ValueError):
                download(origin, self.root / "unused", "t" * 40)
        self.assertFalse((self.root / "unused").exists())
        self.assertIsNone(NoRedirects().redirect_request(None, None, 302, "redirect", {}, "https://other.example"))


@unittest.skipIf(TestClient is None, "Install .[web,web-test] for backup HTTP checks")
class BackupEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = "backup-test-secret-" + "x" * 32
        self.password = "game-password-" + "y" * 32

    def app(self, **kwargs):
        return create_app(self.root / "game.db", self.password, "http://localhost", offline=True, **kwargs)

    def test_disabled_separate_auth_and_recoverable_snapshot(self):
        with TestClient(self.app(), base_url="http://localhost") as client:
            self.assertEqual(client.get("/api/backup").status_code, 404)
        app = self.app(backup_token=self.token)
        with TestClient(app, base_url="http://localhost") as client:
            for kwargs in ({}, {"auth": ("victor", self.password)}, {"headers": {"Authorization": "Bearer wrong"}}):
                self.assertEqual(client.get("/api/backup", **kwargs).status_code, 401)
            app.state.game.submit("move", "go to Relay Chamber", 0)
            before = app.state.game.view()
            response = client.get("/api/backup", headers={"Authorization": "Bearer " + self.token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-badbadger-sha256"], hashlib.sha256(response.content).hexdigest())
            restored = self.root / "recovered.db"
            restored.write_bytes(response.content)
            self.assertEqual(RelayService(restored).view(), before)
            self.assertEqual(app.state.game.view(), before)
            self.assertEqual(client.get("/api/game", headers={"Authorization": "Bearer " + self.token}).status_code, 401)

    def test_tester_mode_never_exports_and_token_cannot_equal_game_password(self):
        with self.assertRaises(ValueError):
            self.app(backup_token=self.password)
        app = self.app(backup_token=self.token, testers={"alice": self.password}, tester_save_dir=self.root / "players")
        with TestClient(app, base_url="http://localhost") as client:
            self.assertEqual(client.get("/api/backup", headers={"Authorization": "Bearer " + self.token}).status_code, 404)

    def test_rate_limits_keep_independent_expiration_windows(self):
        limiter = RateLimit()
        with patch("badbadger.web.time.monotonic", return_value=0):
            limiter.check("backup", 1, 3600)
        with patch("badbadger.web.time.monotonic", return_value=120):
            limiter.check("game", 60, 60)
            with self.assertRaises(HTTPException):
                limiter.check("backup", 1, 3600)
        with patch("badbadger.web.time.monotonic", return_value=3601):
            limiter.check("backup", 1, 3600)
