"""Real HTTP adapter behavior using the ASGI test transport."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from badbadger.web import create_app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "Install .[web,web-test] for HTTP integration tests")
class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.password = "test-only-password-with-32-characters"
        self.origin = "http://127.0.0.1:8000"
        self.app = create_app(Path(self.temp.name) / "save.db", self.password, self.origin, offline=True)
        self.client = self.enterContext(TestClient(self.app, base_url=self.origin, raise_server_exceptions=False))
        self.auth = ("victor", self.password)
        self.headers = {"Origin": self.origin, "X-BadBadger-Request": "turn"}

    def post(self, text="wait 1", revision=0, request_id="one", **kwargs):
        return self.client.post("/api/turn", json={
            "command": text, "expected_revision": revision, "request_id": request_id,
        }, auth=self.auth, headers=self.headers, **kwargs)

    def test_auth_required_for_page_assets_and_game(self):
        for path in ("/", "/assets/app.js", "/assets/style.css", "/api/game"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Basic", response.headers["www-authenticate"])
            self.assertNotIn("Relay Chamber", response.text)
        self.assertEqual(self.client.get("/api/game", auth=("victor", "wrong")).status_code, 401)
        response = self.client.post("/api/turn", headers=self.headers, json={})
        self.assertEqual(response.status_code, 401)

    def test_platform_health_probe_contains_no_private_data(self):
        response = self.client.get("/healthz", headers={"Host": "internal-probe"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_new_game_requires_auth_origin_confirmation_and_current_revision(self):
        payload = {"request_id": "reset", "expected_revision": 0, "confirmed": True}
        self.assertEqual(self.client.post("/api/new-game", json=payload, headers=self.headers).status_code, 401)
        self.assertEqual(self.client.post("/api/new-game", json=payload, auth=self.auth).status_code, 403)
        self.assertEqual(self.client.post("/api/new-game", json={**payload, "confirmed": False}, auth=self.auth, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post("/api/new-game", json=payload, auth=self.auth, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post("/api/new-game", json={**payload, "request_id": "stale"}, auth=self.auth, headers=self.headers).status_code, 409)
        view = self.client.get("/api/game", auth=self.auth).json()
        self.assertEqual(view["revision"], 1)
        self.assertEqual(view["transcript"], [])

    def test_chapter_selection_uses_allowlisted_keys_and_preserves_old_game(self):
        payload = {"request_id": "new-chapter", "expected_revision": 0, "confirmed": True, "scenario": "../../private.db"}
        response = self.client.post("/api/new-game", json=payload, auth=self.auth, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        payload["scenario"] = "blackbarrow"
        response = self.client.post("/api/new-game", json=payload, auth=self.auth, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/api/new-game", json=payload, auth=self.auth, headers=self.headers).json(), response.json())
        view = self.client.get("/api/game", auth=self.auth).json()
        self.assertEqual(view["scenario"]["key"], "blackbarrow")
        self.assertEqual(view["player"]["name"], "Victor Gravenholt")
        self.assertEqual(view["transcript"], [])
        self.assertEqual(self.client.get("/assets/blackbarrow_north_road.json", auth=self.auth).status_code, 404)

    def test_turn_retry_conflict_and_view(self):
        result = self.post("go to Relay Chamber")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.post("go to Relay Chamber").json(), result.json())
        self.assertEqual(self.post("wait 1", request_id="stale").status_code, 409)
        self.assertEqual(self.post("wait 1", revision=1).status_code, 409)
        view = self.client.get("/api/game", auth=self.auth).json()
        self.assertEqual(view["revision"], 1)
        self.assertEqual(len(view["transcript"]), 1)

    def test_cross_origin_missing_origin_and_host_are_rejected(self):
        payload = {"command": "wait 1", "expected_revision": 0, "request_id": "id"}
        for origin in (None, "https://evil.example", "null"):
            headers = {"X-BadBadger-Request": "turn"}
            if origin:
                headers["Origin"] = origin
            response = self.client.post("/api/turn", json=payload, auth=self.auth, headers=headers)
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/turn", json=payload, auth=self.auth, headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example"}, auth=self.auth).status_code, 400)
        self.assertEqual(self.client.options("/api/turn", headers={"Origin": "https://evil.example"}).headers.get("access-control-allow-origin"), None)

    def test_validation_and_body_limits(self):
        for text in ("", " " * 10, "a" * 2001):
            self.assertIn(self.post(text).status_code, (400, 422))
        self.assertEqual(self.post(revision=True).status_code, 422)
        response = self.client.post("/api/turn", content=b"x" * 16385, auth=self.auth,
                                    headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/api/turn", json={"command": "look", "request_id": "id", "expected_revision": 0, "developer": True},
                                    auth=self.auth, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/game", auth=self.auth).json()["revision"], 0)

    def test_private_files_and_inspection_unavailable(self):
        for path in ("/docs", "/openapi.json", "/save.db", "/assets/relay_crisis.json", "/assets/schema.sql",
                     "/assets/victor-gravenholt.json", "/characters/matilda-ash.json"):
            self.assertEqual(self.client.get(path, auth=self.auth).status_code, 404)
        result = self.post("npc Orr").json()
        self.assertIn("only in developer mode", " ".join(result["messages"]))
        public = self.client.get("/api/game", auth=self.auth).text
        self.assertNotIn("available_disclosures", public)
        self.assertNotIn("npc_parameters", public)

    def test_security_headers_and_generic_failures(self):
        response = self.client.get("/", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        with patch.object(self.app.state.game, "submit", side_effect=RuntimeError("PRIVATE_SENTINEL")):
            failed = self.post()
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(failed.headers["cache-control"], "no-store")
        self.assertNotIn("PRIVATE_SENTINEL", failed.text)
        self.assertNotIn("Traceback", failed.text)

    def test_rate_limits_bad_passwords(self):
        for _ in range(20):
            self.assertEqual(self.client.get("/", auth=("victor", "wrong")).status_code, 401)
        self.assertEqual(self.client.get("/", auth=("victor", "wrong")).status_code, 429)
        self.assertEqual(self.client.get("/", auth=self.auth).status_code, 200)

    def test_unsafe_configuration_rejected(self):
        for origin in ("http://remote.example", "https://example.com/path", "https://example.com/", "https://user:pass@example.com"):
            with self.assertRaises(ValueError):
                create_app(Path(self.temp.name) / "other.db", self.password, origin)
        with self.assertRaises(ValueError):
            create_app(Path(self.temp.name) / "other.db", "short", self.origin)
