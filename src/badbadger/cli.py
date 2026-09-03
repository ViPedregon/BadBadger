"""Command-line interface for the SQLite-backed BadBadger prototype."""

from __future__ import annotations

import argparse
from pathlib import Path

from badbadger.application import open_prototype
from badbadger.config import build_configured_npc_backend


BANNER = r"""
  ____            _ ____            _
 | __ )  __ _  __| | __ )  __ _  __| | __ _  ___ _ __
 |  _ \ / _` |/ _` |  _ \ / _` |/ _` |/ _` |/ _ \ '__|
 | |_) | (_| | (_| | |_) | (_| | (_| | (_| |  __/ |
 |____/ \__,_|\__,_|____/ \__,_|\__,_|\__, |\___|_|
                                       |___/
 SQLite prototype. Type 'help' for examples.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the BadBadger prototype")
    parser.add_argument(
        "--save",
        type=Path,
        default=Path("badbadger-save.db"),
        help="SQLite save path (default: ./badbadger-save.db)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    npc_backend, backend_label = build_configured_npc_backend()
    app = open_prototype(
        args.save,
        npc_backend=npc_backend,
        backend_label=backend_label,
    )
    print(BANNER)
    print(f"Save: {args.save.resolve()}")
    print(f"NPC backend: {app.backend_label}")
    for message in app.status():
        print(message)

    try:
        while True:
            try:
                raw = input("\n> ")
            except (EOFError, KeyboardInterrupt):
                print("\nGame saved. Goodbye.")
                break

            messages, should_quit = app.handle(raw)
            for message in messages:
                print(message)
            if should_quit:
                break
    finally:
        app.repository.close()


if __name__ == "__main__":
    main()
