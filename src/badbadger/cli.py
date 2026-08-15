"""Command-line interface for BadBadger."""

from __future__ import annotations

import sys

from badbadger.game_master import ActionResult, GameMaster


BANNER = r"""
  ____            _ ____            _
 | __ )  __ _  __| | __ )  __ _  __| | __ _  ___ _ __
 |  _ \ / _` |/ _` |  _ \ / _` |/ _` |/ _` |/ _ \ '__|
 | |_) | (_| | (_| | |_) | (_| | (_| | (_| |  __/ |
 |____/ \__,_|\__,_|____/ \__,_|\__,_|\__, |\___|_|
                                       |___/
 A dialogue-based adventure.  Type 'help' to begin.
"""


def main() -> None:
    """Entry-point for the CLI game loop."""
    print(BANNER)

    player_name = input("Enter your character's name: ").strip() or "Hero"
    gm = GameMaster.new_game(player_name)

    # Show the starting location without incrementing the turn counter
    look = ActionResult()
    gm._cmd_look(look)
    for msg in look.messages:
        print(msg)

    while not gm.state.game_over:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nFarewell.")
            break

        if not raw:
            continue

        result = gm.process_command(raw)
        for msg in result.messages:
            print(msg)

        if result.game_over:
            break


if __name__ == "__main__":
    main()
