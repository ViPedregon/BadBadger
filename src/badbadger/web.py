"""Single-player, offline HTTP adapter. Run behind HTTPS for remote access."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from importlib.resources import files
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from badbadger.service import RelayService, TurnConflict

logger = logging.getLogger(__name__)
security = HTTPBasic(auto_error=False)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1, max_length=2000)
    expected_revision: StrictInt = Field(ge=0)


class RateLimit:
    """Small bounded limiter for a single private instance, not a public gateway."""

    def __init__(self):
        self.events = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window: int = 60):
        with self.lock:
            now = time.monotonic()
            for old in list(self.events):
                if not self.events[old] or self.events[old][-1] <= now - window:
                    del self.events[old]
            if key not in self.events and len(self.events) >= 1024:
                raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
            entries = self.events[key]
            while entries and entries[0] <= now - window:
                entries.popleft()
            if len(entries) >= limit:
                raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
            entries.append(now)


def create_app(database: str | Path, password: str, origin: str) -> FastAPI:
    """Create one private game. These arguments are server configuration only."""
    parsed = urlsplit(origin)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.path or parsed.query
            or parsed.fragment or origin.endswith("/")):
        raise ValueError("Origin must be an exact http(s) origin without a trailing slash")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote browser access requires HTTPS")
    if not isinstance(password, str) or len(password) < 24:
        raise ValueError("Set a web password of at least 24 characters")
    limiter = RateLimit()

    @asynccontextmanager
    async def lifespan(app):
        app.state.game = RelayService(database)
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
        valid = (
            credentials is not None
            and secrets.compare_digest(credentials.username.encode(), b"victor")
            and secrets.compare_digest(credentials.password.encode(), password.encode())
        )
        if not valid:
            address = request.client.host if request.client else "unknown"
            limiter.check("auth:" + address, 20)
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": 'Basic realm="BadBadger"'})

    @app.get("/healthz")
    def health():
        # Platform probes get no game state, paths or version information.
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(authenticated)])
    def index():
        return Response(files("badbadger").joinpath("static/index.html").read_text(), media_type="text/html")

    @app.get("/assets/{name}", dependencies=[Depends(authenticated)])
    def asset(name: str):
        allowed = {"app.js": "text/javascript", "style.css": "text/css"}
        if name not in allowed:
            raise HTTPException(404, "Not found")
        return Response(files("badbadger").joinpath("static", name).read_text(), media_type=allowed[name])

    @app.get("/api/game", dependencies=[Depends(authenticated)])
    def game(request: Request):
        return request.app.state.game.view()

    @app.post("/api/turn", dependencies=[Depends(authenticated)])
    def turn(payload: TurnRequest, request: Request):
        limiter.check("turns", 60)
        try:
            return request.app.state.game.submit(payload.request_id, payload.command, payload.expected_revision)
        except TurnConflict as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except sqlite3.OperationalError:
            raise HTTPException(503, "Save is busy. Retry the same submission.", headers={"Retry-After": "1"}) from None

    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Run the private offline BadBadger browser application")
    parser.add_argument("--save", type=Path, required=True, help="persistent server-owned SQLite save path")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", help="bind address; public/container binding requires an HTTPS origin")
    args = parser.parse_args()
    # New saves and SQLite sidecar files should be private on hosted instances.
    os.umask(0o077)
    password = os.environ.get("BADBADGER_WEB_PASSWORD", "")
    origin = os.environ.get("BADBADGER_WEB_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{args.port}"
    try:
        if args.host not in {"127.0.0.1", "localhost", "::1"} and urlsplit(origin).scheme != "https":
            raise ValueError("A public/container bind requires an explicitly configured HTTPS origin")
        app = create_app(args.save, password, origin)
    except ValueError as error:
        parser.error(str(error))
    # An HTTPS reverse proxy can forward to this loopback-only listener.
    uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
