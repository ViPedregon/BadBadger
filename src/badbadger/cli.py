"""Command-line interface for the SQLite-backed BadBadger prototype."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import logging
from pathlib import Path
import sys
from importlib.resources import as_file, files
from typing import Iterator, TextIO

from badbadger.application import open_prototype
from badbadger.config import (
    build_configured_intent_interpreter,
    build_configured_npc_backend,
)


BANNER = r"""
  ____            _ ____            _
 | __ )  __ _  __| | __ )  __ _  __| | __ _  ___ _ __
 |  _ \ / _` |/ _` |  _ \ / _` |/ _` |/ _` |/ _ \ '__|
 | |_) | (_| | (_| | |_) | (_| | (_| | (_| |  __/ |
 |____/ \__,_|\__,_|____/ \__,_|\__,_|\__, |\___|_|
                                       |___/
 SQLite prototype. Type 'help' for examples.
"""

WORKING_LABEL = "[Working...]"
INSTANT_COMMANDS = {
    "", "help", "?", "look", "look around", "status", "where am i",
    "objective", "mission", "goal", "choices", "suggestions",
    "what can i do", "timing", "journal", "notes", "knowledge", "what do i know",
    "people", "characters", "cast", "who is here", "summary", "recap",
    "quit", "exit", "q", "npc",
}


def command_needs_working_status(raw: str) -> bool:
    """Return whether a command may invoke simulation or AI work."""
    normalized = " ".join(raw.lower().split())
    return normalized not in INSTANT_COMMANDS and not (
        normalized.startswith("npc ") or normalized.startswith("inspect npc ")
    )


@contextmanager
def working_status(
    *, enabled: bool, stream: TextIO | None = None
) -> Iterator[None]:
    """Show a transient status slug without polluting redirected output."""
    target = stream or sys.stdout
    visible = enabled and target.isatty()
    if visible:
        target.write(WORKING_LABEL)
        target.flush()
    try:
        yield
    finally:
        if visible:
            target.write("\r" + (" " * len(WORKING_LABEL)) + "\r")
            target.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the BadBadger prototype")
    parser.add_argument(
        "--save",
        type=Path,
        default=Path("relay-crisis-save.db"),
        help="save path (default: ./relay-crisis-save.db)",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        help="JSON scenario to load when creating a new save",
    )
    parser.add_argument(
        "--new-game", action="store_true",
        help="replace the selected save and begin the scenario again",
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="play without API calls using deterministic NPC responses",
    )
    parser.add_argument(
        "--developer", action="store_true",
        help="enable NPC state inspection commands",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.developer else logging.CRITICAL,
        format="%(message)s",
        force=True,
    )
    save_exists = args.save.exists() and args.save.stat().st_size > 0
    if args.new_game and args.save.exists():
        args.save.unlink()
        save_exists = False
    args.save.parent.mkdir(parents=True, exist_ok=True)
    npc_backend, backend_label = build_configured_npc_backend(offline=args.offline)
    intent_interpreter, intent_label = build_configured_intent_interpreter(
        offline=args.offline
    )
    try:
        if save_exists or args.scenario is not None:
            app = open_prototype(
                args.save, npc_backend=npc_backend, backend_label=backend_label,
                intent_interpreter=intent_interpreter, intent_label=intent_label,
                scenario=args.scenario, developer_mode=args.developer,
            )
        else:
            resource = files("badbadger.scenarios").joinpath("relay_crisis.json")
            with as_file(resource) as scenario:
                app = open_prototype(
                    args.save, npc_backend=npc_backend, backend_label=backend_label,
                    intent_interpreter=intent_interpreter, intent_label=intent_label,
                    scenario=scenario, developer_mode=args.developer,
                )
    except Exception as error:
        print(f"Could not start BadBadger: {error}", file=sys.stderr)
        print("Try a new save with --new-game, or run with --offline.", file=sys.stderr)
        return 1
    print(BANNER)
    print("Playing offline." if args.offline or "OpenAI" not in app.backend_label else "AI dialogue enabled.")
    print("Type 'help' at any time.")
    for message in (app.status() if save_exists else app.introduction()):
        print(message)

    try:
        while True:
            try:
                raw = input("\n> ")
            except (EOFError, KeyboardInterrupt):
                print("\nGame saved. Goodbye.")
                break

            with working_status(enabled=command_needs_working_status(raw)):
                messages, should_quit = app.handle(raw)
            for message in messages:
                print(message)
            if should_quit:
                break
    finally:
        app.repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
