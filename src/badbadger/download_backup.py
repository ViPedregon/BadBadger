"""Pull a verified shared save over HTTPS without exposing backup credentials."""

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
from uuid import uuid4

from badbadger.backup import snapshot


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download(origin, destination, token, *, max_bytes=512 * 1024 * 1024):
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Use an exact HTTPS origin without a trailing slash")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Set a backup token of at least 32 characters")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = Request(origin + "/api/backup", headers={"Authorization": "Bearer " + token})
    # Never forward a credential to another URL. Default HTTPS certificate
    # verification remains enabled; no insecure TLS option is provided.
    opener = build_opener(NoRedirects())
    with tempfile.TemporaryDirectory(prefix=".download-", dir=destination) as temporary:
        raw = Path(temporary) / "received.db"
        digest = hashlib.sha256()
        size = 0
        with opener.open(request, timeout=120) as response, raw.open("xb") as stream:
            expected = response.headers.get("X-BadBadger-SHA256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Missing backup checksum")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("Backup exceeds configured download limit")
                digest.update(chunk)
                stream.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("Backup checksum mismatch")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # Reuse schema, foreign-key and receipt checks before publishing locally.
        return snapshot(raw, destination / f"shared-{stamp}-{uuid4().hex[:8]}.db")


def main():
    parser = argparse.ArgumentParser(description="Download a verified shared BadBadger save")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = download(args.origin, args.destination, os.environ.get("BADBADGER_BACKUP_TOKEN", ""))
    except Exception:
        # Never print HTTP request objects, headers, provider responses or secrets.
        parser.exit(1, "Backup download failed; check connectivity, token and save integrity. Existing backups were retained.\n")
    print(f"Verified backup saved: {output.name}")


if __name__ == "__main__":
    main()
