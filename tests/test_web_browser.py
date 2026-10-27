"""Optional real-browser acceptance checks: install .[web,web-test] + Chromium."""

import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

try:
    from playwright.sync_api import sync_playwright, expect
    import badbadger.web
except ImportError:
    sync_playwright = None

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")


@unittest.skipUnless(sync_playwright and CHROMIUM, "Install .[web,web-test] and system Chromium for browser checks")
class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.save = Path(self.temp.name) / "relay.db"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.password = secrets.token_urlsafe(32)
        self.log = self.enterContext(open(Path(self.temp.name) / "server.log", "w"))
        self.process = None
        self.addCleanup(self.stop_server)
        self.start_server()
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(http_credentials={"username": "victor", "password": self.password})
        self.page = self.context.new_page()
        self.page.goto(self.origin)
        expect(self.page.locator("#command")).to_be_enabled()

    def start_server(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "badbadger.web", "--save", str(self.save), "--port", str(self.port)],
            env={**os.environ, "BADBADGER_WEB_PASSWORD": self.password, "BADBADGER_WEB_ORIGIN": self.origin},
            stdout=self.log, stderr=self.log,
        )
        for _ in range(100):
            if self.process.poll() is not None:
                self.fail("Web process stopped during startup")
            try:
                urlopen(self.origin, timeout=.2)
            except HTTPError as error:
                if error.code == 401:
                    return
            except (URLError, TimeoutError):
                pass
            time.sleep(.05)
        self.fail("Web process did not start")

    def stop_server(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def command(self, text):
        self.page.locator("#command").fill(text)
        self.page.locator("#send").click()
        expect(self.page.locator("#notice")).to_have_text("Saved.")
        expect(self.page.locator("#command")).to_be_enabled()

    def test_play_close_resume_restart_and_finish(self):
        if os.name == "posix":
            self.assertEqual(self.save.stat().st_mode & 0o777, 0o600)
        for command in ("go to Relay Chamber", "tell Orr thank you", "tell Orr thanks"):
            self.command(command)
        self.context.close()
        self.context = self.browser.new_context(http_credentials={"username": "victor", "password": self.password})
        self.page = self.context.new_page()
        self.page.goto(self.origin)
        expect(self.page.locator("#status")).to_contain_text("Relay Chamber")
        expect(self.page.locator(".player")).to_have_count(3)
        self.stop_server()
        self.start_server()
        self.page.reload()
        expect(self.page.locator(".player")).to_have_count(3)
        self.command("tell Orr broadcast the distress packet")
        expect(self.page.locator("#transcript")).to_contain_text("rescue acknowledgment")
        self.command("wait 10 minutes")
        expect(self.page.locator("#status")).to_contain_text("Mission status: ended")

    def test_lost_response_reload_and_retry_executes_once(self):
        def lose_response(route):
            route.fetch()
            route.abort("failed")
        self.page.route("**/api/turn", lose_response)
        self.page.locator("#command").fill("wait 1")
        self.page.locator("#send").click()
        expect(self.page.locator("#notice")).to_contain_text("Connection interrupted")
        self.page.unroute("**/api/turn")
        self.page.reload()
        expect(self.page.locator("#retry")).to_be_visible()
        self.page.locator("#retry").click()
        expect(self.page.locator("#notice")).to_have_text("Saved.")
        expect(self.page.locator(".player")).to_have_count(1)
        expect(self.page.locator("#status")).to_contain_text("Mission time: 1 minutes.")

    def test_two_tabs_stale_turn_and_text_is_not_html(self):
        other = self.context.new_page()
        other.goto(self.origin)
        expect(other.locator("#command")).to_be_enabled()
        self.command("wait 1")
        other.locator("#command").fill("wait 2")
        other.locator("#send").click()
        expect(other.locator("#notice")).to_contain_text("Your turn was not submitted")
        expect(other.locator("#status")).to_contain_text("Mission time: 1 minutes.")
        self.command('<img src=x onerror="window.badbadgerInjected=true">')
        self.assertFalse(self.page.evaluate("Boolean(window.badbadgerInjected)"))
        expect(self.page.locator("#transcript img")).to_have_count(0)
        expect(self.page.locator("#transcript")).to_contain_text("<img src=x")
