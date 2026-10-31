"""Hourly local staging snapshots; off-host encrypted copying is separate.

No automatic deletion: do not expire the only recoverable copy. Configure a
retention policy after off-host backup/restore has been established.
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import argparse

from badbadger.backup import snapshot, backup_testers


def main():
    parser = argparse.ArgumentParser(description="Verified local backup staging")
    parser.add_argument("--source", type=Path, default=Path("/var/lib/badbadger/relay.db"))
    parser.add_argument("--backup-dir", type=Path, default=Path("/var/lib/badbadger/backups"))
    parser.add_argument("--testers", action="store_true", help="source is a tester save directory")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{'testers' if args.testers else 'relay'}-{stamp}-{uuid4().hex[:8]}"
    destination = args.backup_dir / (name if args.testers else name + ".db")
    if args.testers:
        backup_testers(args.source, destination)
    else:
        snapshot(args.source, destination)
    print("Verified local save snapshot completed; off-host copying is separate.")


if __name__ == "__main__":
    main()
