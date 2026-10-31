"""Server-owned, bounded closed-beta accounts; never accept client save paths."""

import hashlib
import json
from pathlib import Path
import re


def validate_testers(accounts):
    if not isinstance(accounts, dict) or not 1 <= len(accounts) <= 32:
        raise ValueError("Tester configuration must contain between 1 and 32 accounts")
    for username, password in accounts.items():
        if (not isinstance(username, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", username)
                or not isinstance(password, str) or len(password) < 24):
            raise ValueError("Tester usernames must be lowercase identifiers and passwords at least 24 characters")
    return dict(accounts)


def load_testers(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate tester username")
            result[key] = value
        return result

    try:
        accounts = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
        return validate_testers(accounts)
    except (OSError, ValueError):
        raise ValueError("Unable to load tester credentials; check the private JSON file") from None


def tester_scope(username):
    return hashlib.sha256(username.encode("utf-8")).hexdigest()
