"""Hourly local staging snapshots; off-host encrypted copying is separate.

No automatic deletion: do not expire the only recoverable copy. Configure a
retention policy after off-host backup/restore has been established.
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from badbadger.backup import snapshot


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path("/var/lib/badbadger/backups") / f"relay-{stamp}-{uuid4().hex[:8]}.db"
    snapshot("/var/lib/badbadger/relay.db", destination)
    print("Verified local save snapshot completed; off-host copying is separate.")


if __name__ == "__main__":
    main()
