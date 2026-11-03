"""Single-player HTTP adapter. Run behind HTTPS for remote access."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from contextlib import asynccontextmanager
import json
import hashlib
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import tempfile
from importlib.resources import files
from urllib.parse import urlsplit
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from badbadger.service import RelayService, TurnConflict
from badbadger.backup import snapshot
from badbadger.testers import load_testers, validate_testers, tester_scope
from badbadger.scenarios.catalog import CHAPTERS

logger = logging.getLogger(__name__)
security = HTTPBasic(auto_error=False)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1, max_length=2000)
    expected_revision: StrictInt = Field(ge=0)


class NewGameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=0)
    confirmed: StrictBool
    scenario: Literal["relay_crisis", "blackbarrow"] | None = None


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=0)
    archive_id: StrictInt = Field(ge=0)
    confirmed: StrictBool


class RateLimit:
    """Small bounded limiter for a single private instance, not a public gateway."""

    def __init__(self):
        self.events = defaultdict(deque)
        self.expirations = {}
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window: int = 60):
        with self.lock:
            now = time.monotonic()
            for old in list(self.events):
                if self.expirations.get(old, 0) <= now:
                    del self.events[old]
                    self.expirations.pop(old, None)
            if key not in self.events and len(self.events) >= 1024:
                raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
            entries = self.events[key]
            while entries and entries[0] <= now - window:
                entries.popleft()
            if len(entries) >= limit:
                raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
            entries.append(now)
            self.expirations[key] = now + window


def create_app(database: str | Path, password: str, origin: str, *, offline: bool = False, scenario: str = "relay_crisis",
               testers: dict[str, str] | None = None, tester_save_dir: str | Path | None = None,
               backup_token: str | None = None) -> FastAPI:
    """Create one private game. These arguments are server configuration only."""
    parsed = urlsplit(origin)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.path or parsed.query
            or parsed.fragment or origin.endswith("/")):
        raise ValueError("Origin must be an exact http(s) origin without a trailing slash")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote browser access requires HTTPS")
    if testers is None and (not isinstance(password, str) or len(password) < 24):
        raise ValueError("Set a web password of at least 24 characters")
    accounts = validate_testers(testers) if testers is not None else None
    if backup_token is not None and (not isinstance(backup_token, str) or len(backup_token) < 32
                                    or backup_token == password
                                    or (accounts is not None and backup_token in accounts.values())):
        raise ValueError("Backup token must be a separate secret of at least 32 characters")
    if accounts is not None and tester_save_dir is None:
        raise ValueError("Tester mode requires a persistent tester save directory")
    limiter = RateLimit()
    backup_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if accounts is None:
            app.state.game = RelayService(database, offline=offline, scenario=scenario)
        else:
            root = Path(tester_save_dir).resolve()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            app.state.games = {}
            for username in accounts:
                target = root / (tester_scope(username) + ".db")
                if target.is_symlink():
                    raise ValueError("Tester save cannot be a symbolic link")
                app.state.games[username] = RelayService(target, offline=offline, scenario=scenario)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def dispatch(request, call_next):
        try:
            return await call_next(request)
        except Exception as error:
            # Avoid logging request text, database contents or credential values.
            logger.error("Web request failed (%s)", type(error).__name__)
            return JSONResponse({"detail": "Unable to complete request. Retry the same submission."}, status_code=500)

    @app.middleware("http")
    async def guards(request: Request, call_next):
        # Do not trust forwarded host/origin values for CSRF checks.
        if request.url.path == "/healthz" and request.method == "GET":
            response = await dispatch(request, call_next)
        elif request.headers.get("host") != parsed.netloc:
            response = JSONResponse({"detail": "Invalid host"}, status_code=400)
        elif request.method == "POST" and (
            request.headers.get("origin") != origin
            or request.headers.get("x-badbadger-request") != "turn"
            or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
        ):
            response = JSONResponse({"detail": "Request origin or format rejected"}, status_code=403)
        elif request.method == "POST":
            chunks = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16384:
                    break
                chunks.append(chunk)
            if size > 16384:
                response = JSONResponse({"detail": "Request too large"}, status_code=413)
            else:
                # BaseHTTPMiddleware replays a cached body to the downstream app.
                request._body = b"".join(chunks)
                response = await dispatch(request, call_next)
        else:
            response = await dispatch(request, call_next)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        })
        if parsed.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def authenticated(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)):
        username = credentials.username if credentials else ""
        expected = accounts.get(username) if accounts is not None else password if username == "victor" else None
        valid = credentials is not None and expected is not None and secrets.compare_digest(
            credentials.password.encode(), expected.encode())
        if not valid:
            address = request.client.host if request.client else "unknown"
            limiter.check("auth:" + address, 20)
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": 'Basic realm="BadBadger"'})
        request.state.player_scope = tester_scope(username) if accounts is not None else None
        request.state.game = request.app.state.games[username] if accounts is not None else request.app.state.game
        if (accounts is not None and request.method == "POST"
                and request.headers.get("x-badbadger-player") != request.state.player_scope):
            raise HTTPException(409, "Player changed. Reload before submitting.")

    @app.get("/healthz")
    def health():
        # Platform probes get no game state, paths or version information.
        return {"status": "ok"}

    @app.get("/api/backup")
    def download_backup(request: Request):
        # Deliberately separate from browser credentials and unavailable in
        # tester mode: a tester must never download another player's world.
        if backup_token is None or accounts is not None:
            raise HTTPException(404, "Not found")
        address = request.client.host if request.client else "unknown"
        authorization = request.headers.get("authorization", "")
        if not secrets.compare_digest(authorization.encode(), ("Bearer " + backup_token).encode()):
            limiter.check("backup-auth:" + address, 20)
            raise HTTPException(401, "Backup authentication required")
        limiter.check("backup-download", 6, window=3600)
        if not backup_lock.acquire(blocking=False):
            raise HTTPException(503, "Backup is busy. Retry later.", headers={"Retry-After": "60"})
        temporary = None
        try:
            temporary = tempfile.TemporaryDirectory(prefix="badbadger-download-")
            target = snapshot(request.app.state.game.database, Path(temporary.name) / "shared.db")
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            return FileResponse(target, media_type="application/octet-stream", filename="badbadger-shared.db",
                                headers={"X-BadBadger-SHA256": digest},
                                background=BackgroundTask(temporary.cleanup))
        except Exception:
            if temporary is not None:
                temporary.cleanup()
            raise
        finally:
            backup_lock.release()

    @app.get("/", dependencies=[Depends(authenticated)])
    def index():
        return Response(files("badbadger").joinpath("static/index.html").read_text(encoding="utf-8"), media_type="text/html")

    @app.get("/assets/{name}", dependencies=[Depends(authenticated)])
    def asset(name: str, request: Request):
        allowed = {"app.js": "text/javascript", "style.css": "text/css"}
        if name not in allowed:
            raise HTTPException(404, "Not found")
        content = files("badbadger").joinpath("static", name).read_text(encoding="utf-8")
        if name == "app.js" and request.state.player_scope:
            content = content.replace("const playerScope = null;", "const playerScope = " + json.dumps(request.state.player_scope) + ";")
        return Response(content, media_type=allowed[name])

    @app.get("/api/game", dependencies=[Depends(authenticated)])
    def game(request: Request):
        return request.state.game.view()

    @app.post("/api/turn", dependencies=[Depends(authenticated)])
    def turn(payload: TurnRequest, request: Request):
        limiter.check("turns:" + (request.state.player_scope or "default"), 60)
        try:
            return request.state.game.submit(payload.request_id, payload.command, payload.expected_revision)
        except TurnConflict as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except sqlite3.OperationalError:
            raise HTTPException(503, "Save is busy. Retry the same submission.", headers={"Retry-After": "1"}) from None

    @app.post("/api/new-game", dependencies=[Depends(authenticated)])
    def new_game(payload: NewGameRequest, request: Request):
        limiter.check("turns:" + (request.state.player_scope or "default"), 60)
        try:
            return request.state.game.new_game(payload.request_id, payload.expected_revision, confirmed=payload.confirmed, scenario=payload.scenario)
        except TurnConflict as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except sqlite3.OperationalError:
            raise HTTPException(503, "Save is busy. Retry the same request.", headers={"Retry-After": "1"}) from None

    @app.post("/api/resume", dependencies=[Depends(authenticated)])
    def resume(payload: ResumeRequest, request: Request):
        limiter.check("turns:" + (request.state.player_scope or "default"), 60)
        try:
            return request.state.game.resume_archive(payload.request_id, payload.expected_revision, payload.archive_id, confirmed=payload.confirmed)
        except TurnConflict as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except sqlite3.OperationalError:
            raise HTTPException(503, "Save is busy. Retry the same request.", headers={"Retry-After": "1"}) from None

    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Run the private BadBadger browser application")
    parser.add_argument("--save", type=Path, required=True, help="persistent server-owned SQLite save path")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", help="bind address; public/container binding requires an HTTPS origin")
    parser.add_argument("--offline", action="store_true", help="disable AI calls even when an API key is configured")
    parser.add_argument("--scenario", choices=tuple(CHAPTERS), default=os.environ.get("BADBADGER_SCENARIO", "blackbarrow"),
                        help="chapter for a fresh save; existing saves always resume")
    parser.add_argument("--testers", type=Path, help="private server-owned JSON username/password mapping")
    parser.add_argument("--tester-save-dir", type=Path, help="persistent directory for isolated tester saves")
    args = parser.parse_args()
    if bool(args.testers) != bool(args.tester_save_dir):
        parser.error("--testers and --tester-save-dir must be supplied together")
    # New saves and SQLite sidecar files should be private on hosted instances.
    os.umask(0o077)
    password = os.environ.get("BADBADGER_WEB_PASSWORD", "")
    origin = os.environ.get("BADBADGER_WEB_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{args.port}"
    try:
        if args.host not in {"127.0.0.1", "localhost", "::1"} and urlsplit(origin).scheme != "https":
            raise ValueError("A public/container bind requires an explicitly configured HTTPS origin")
        app = create_app(args.save, password, origin, offline=args.offline, scenario=args.scenario,
                         testers=load_testers(args.testers) if args.testers else None, tester_save_dir=args.tester_save_dir,
                         backup_token=os.environ.get("BADBADGER_BACKUP_TOKEN") or None)
    except ValueError as error:
        parser.error(str(error))
    # An HTTPS reverse proxy can forward to this loopback-only listener.
    uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
