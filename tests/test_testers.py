"""Closed-beta accounts must not share saves, receipts or archive authority."""

import json
from pathlib import Path
import tempfile
import unittest

from badbadger.testers import load_testers, tester_scope, validate_testers

try:
    from fastapi.testclient import TestClient
    from badbadger.web import create_app
except ImportError:
    TestClient = None


class TesterConfigTests(unittest.TestCase):
    def test_invalid_accounts_and_duplicate_usernames_rejected(self):
        for value in ({}, [], {"../escape": "x" * 32}, {"alice": "short"}, {"alice": 123},
                      {f"user{i}": "x" * 32 for i in range(33)}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_testers(value)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.json"
            path.write_text('{"alice":"' + 'x' * 32 + '","alice":"' + 'y' * 32 + '"}')
            with self.assertRaisesRegex(ValueError, "Unable to load"):
                load_testers(path)


@unittest.skipIf(TestClient is None, "Install .[web,web-test] for HTTP integration tests")
class TesterIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.accounts = {"alice": "a" * 32, "bob": "b" * 32}
        self.origin = "http://localhost"
        self.app = self.build()
        self.client = self.enterContext(TestClient(self.app, base_url=self.origin))

    def build(self):
        return create_app(self.root / "unused.db", "", self.origin, offline=True,
                          testers=self.accounts, tester_save_dir=self.root / "testers")

    def view(self, user):
        response = self.client.get("/api/game", auth=(user, self.accounts[user]))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def post(self, user, endpoint, payload, scope=None):
        return self.client.post(endpoint, json=payload, auth=(user, self.accounts[user]), headers={
            "Origin": self.origin, "X-BadBadger-Request": "turn",
            "X-BadBadger-Player": scope or tester_scope(user)})

    def test_worlds_receipts_archives_and_restart_are_isolated(self):
        turn = {"request_id": "same-id", "command": "go to Relay Chamber", "expected_revision": 0}
        self.assertEqual(self.post("alice", "/api/turn", turn).status_code, 200)
        self.assertEqual(self.view("bob")["revision"], 0)
        self.assertEqual(self.view("bob")["transcript"], [])
        self.assertEqual(self.post("bob", "/api/turn", {**turn, "command": "wait 1"}).status_code, 200)
        new = {"request_id": "new", "expected_revision": 1, "confirmed": True}
        self.assertEqual(self.post("alice", "/api/new-game", new).status_code, 200)
        self.assertEqual(len(self.view("alice")["archives"]), 1)
        self.assertEqual(self.view("bob")["archives"], [])
        resume = {"request_id": "steal", "expected_revision": 1, "confirmed": True, "archive_id": 1}
        self.assertEqual(self.post("bob", "/api/resume", resume).status_code, 400)
        before = {user: self.view(user) for user in self.accounts}
        with TestClient(self.build(), base_url=self.origin) as restarted:
            for user, password in self.accounts.items():
                self.assertEqual(restarted.get("/api/game", auth=(user, password)).json(), before[user])
        self.assertFalse((self.root / "unused.db").exists())

    def test_stale_account_tab_and_cross_account_credentials_rejected(self):
        turn = {"request_id": "stale", "command": "wait 1", "expected_revision": 0}
        self.assertEqual(self.post("bob", "/api/turn", turn, tester_scope("alice")).status_code, 409)
        self.assertEqual(self.view("bob")["revision"], 0)
        for auth in (("alice", self.accounts["bob"]), ("victor", "a" * 32), ("../alice", "a" * 32)):
            self.assertEqual(self.client.get("/api/game", auth=auth).status_code, 401)
        for user in self.accounts:
            script = self.client.get("/assets/app.js", auth=(user, self.accounts[user])).text
            self.assertIn('const playerScope = "' + tester_scope(user) + '";', script)
            self.assertNotIn(self.accounts[user], script)
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
