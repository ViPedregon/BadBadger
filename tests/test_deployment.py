"""Optional HTTPS proxy + restored-save drill with a locally trusted test cert."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from badbadger.backup import snapshot

CADDY = os.environ.get("BADBADGER_TEST_CADDY") or shutil.which("caddy")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@unittest.skipUnless(CADDY and shutil.which("openssl"), "Set BADBADGER_TEST_CADDY to test the optional HTTPS proxy")
class DeploymentTests(unittest.TestCase):
    def test_https_auth_backup_restore_and_receipt_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend, public = free_port(), free_port()
            origin = f"https://localhost:{public}"
            password = secrets.token_urlsafe(32)
            auth = "Basic " + base64.b64encode(f"victor:{password}".encode()).decode()
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                "-keyout", str(key), "-out", str(cert),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            trusted = ssl.create_default_context(cafile=str(cert))
            template = (Path(__file__).resolve().parents[1] / "deploy/Caddyfile").read_text()
            config = template.replace("badbadger.example.com {", f"{origin} {{\n    tls {cert} {key}")
            config = config.replace("127.0.0.1:8000", f"127.0.0.1:{backend}")
            config = "{\n admin off\n auto_https disable_redirects\n}\n" + config
            path = root / "Caddyfile"
            path.write_text(config)
            processes = []
            self.addCleanup(lambda: [stop(process) for process in processes])

            def launch(save):
                process = subprocess.Popen([
                    sys.executable, "-m", "badbadger.web", "--save", str(save), "--port", str(backend),
                ], env={**os.environ, "BADBADGER_WEB_PASSWORD": password, "BADBADGER_WEB_ORIGIN": origin},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                wait_for(f"http://127.0.0.1:{backend}/healthz")
                return process

            live = launch(root / "live.db")
            proxy = subprocess.Popen([CADDY, "run", "--config", str(path), "--adapter", "caddyfile"],
                env={**os.environ, "XDG_DATA_HOME": str(root / "data"), "XDG_CONFIG_HOME": str(root / "config")},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(proxy)
            wait_for(origin + "/healthz", context=trusted)

            def request(path, payload=None, authenticated=True):
                headers = {"Authorization": auth} if authenticated else {}
                data = None
                if payload is not None:
                    headers.update({"Origin": origin, "X-BadBadger-Request": "turn", "Content-Type": "application/json"})
                    data = json.dumps(payload).encode()
                with urlopen(Request(origin + path, data=data, headers=headers), context=trusted, timeout=5) as response:
                    self.assertIn("max-age", response.headers["strict-transport-security"])
                    return json.load(response)

            with self.assertRaises(HTTPError) as denied:
                request("/api/game", authenticated=False)
            self.assertEqual(denied.exception.code, 401)
            turn = {"request_id": "move", "command": "go to Relay Chamber", "expected_revision": 0}
            receipt = request("/api/turn", turn)
            original = request("/api/game")
            backup = snapshot(root / "live.db", root / "backup.db")
            stop(live)
            restored = snapshot(backup, root / "restored.db")
            launch(restored)
            self.assertEqual(request("/api/game"), original)
            self.assertEqual(request("/api/turn", turn), receipt)
            self.assertEqual(request("/api/game")["revision"], 1)
            # Stop all children before the temporary certificates/save are removed.
            for process in processes:
                stop(process)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def wait_for(url, context=None):
    for _ in range(100):
        try:
            with urlopen(url, context=context, timeout=.3) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(.05)
    raise AssertionError("Local deployment test service did not become ready")
