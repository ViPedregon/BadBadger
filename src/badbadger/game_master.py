"""Game Master – the central engine that owns the GameState and coordinates
player actions with NPC agents.

Responsibilities
----------------
* Parse player commands (``talk``, ``look``, ``go``, ``inventory``, ``quit``).
* Route dialogue to the appropriate :class:`NPCAgent`.
* Update the :class:`GameState` after every turn.
* Return structured :class:`ActionResult` objects that the CLI (or any other
  front-end) can render.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from badbadger.models import GameState, Location, NPC, Player
from badbadger.npc_agent import NPCAgent


@dataclass
class ActionResult:
    """The outcome of processing one player command."""

    messages: list[str] = field(default_factory=list)
    game_over: bool = False

    def add(self, msg: str) -> None:
        self.messages.append(msg)


class GameMaster:
    """Central game engine that manages state and dispatches actions."""

    def __init__(self, state: GameState) -> None:
        self.state = state
        # Lazily created agents, one per NPC
        self._agents: dict[str, NPCAgent] = {}

    # ------------------------------------------------------------------
    # Factory / setup helpers
    # ------------------------------------------------------------------

    @classmethod
    def new_game(cls, player_name: str = "Hero") -> "GameMaster":
        """Create a fresh game with a default world."""
        player = Player(name=player_name)

        tavern = Location(
            name="The Rusty Flagon",
            description=(
                "A dimly lit tavern that smells of stale ale and sawdust. "
                "A fire crackles in the corner."
            ),
            npcs=["Mira", "Old Tom"],
        )
        forest = Location(
            name="Whispering Forest",
            description=(
                "Ancient trees loom overhead. Strange sounds drift between "
                "the trunks."
            ),
            npcs=["Sylvan"],
        )

        npcs = {
            "Mira": NPC(
                name="Mira",
                description="The sharp-eyed barmaid who knows every rumour in town.",
            ),
            "Old Tom": NPC(
                name="Old Tom",
                description="A weathered farmer nursing his third ale of the evening.",
            ),
            "Sylvan": NPC(
                name="Sylvan",
                description="A mysterious elven ranger who rarely speaks to strangers.",
            ),
        }

        state = GameState(
            player=player,
            locations={"tavern": tavern, "forest": forest},
            npcs=npcs,
            current_location="tavern",
        )
        state.record(f"A new game has begun. {player_name} enters the world.")
        return cls(state)

    # ------------------------------------------------------------------
    # Agent access
    # ------------------------------------------------------------------

    def _agent_for(self, npc_name: str) -> NPCAgent:
        if npc_name not in self._agents:
            self._agents[npc_name] = NPCAgent(self.state.npcs[npc_name])
        return self._agents[npc_name]

    # ------------------------------------------------------------------
    # Command processing
    # ------------------------------------------------------------------

    def process_command(self, raw_input: str) -> ActionResult:
        """Parse *raw_input* and execute the appropriate game action."""
        result = ActionResult()
        tokens = raw_input.strip().split(maxsplit=1)

        if not tokens:
            result.add("(silence…)")
            return result

        verb = tokens[0].lower()
        argument = tokens[1] if len(tokens) > 1 else ""

        self.state.turn += 1

        if verb in ("quit", "exit", "q"):
            result.add("Thanks for playing BadBadger. Goodbye!")
            result.game_over = True
            self.state.game_over = True

        elif verb == "look":
            self._cmd_look(result)

        elif verb == "go":
            self._cmd_go(argument, result)

        elif verb == "talk":
            self._cmd_talk(argument, result)

        elif verb in ("inventory", "inv", "i"):
            self._cmd_inventory(result)

        elif verb == "help":
            self._cmd_help(result)

        else:
            result.add(
                f"You're not sure how to '{raw_input}'. "
                "Type 'help' for a list of commands."
            )

        return result

    # ------------------------------------------------------------------
    # Individual command handlers
    # ------------------------------------------------------------------

    def _cmd_look(self, result: ActionResult) -> None:
        loc = self.state.current_location_obj()
        if loc is None:
            result.add("You are nowhere.")
            return
        result.add(f"[{loc.name}]")
        result.add(loc.description)
        npcs_here = self.state.npcs_at_current_location()
        if npcs_here:
            names = ", ".join(n.name for n in npcs_here if n.is_active)
            result.add(f"You see: {names}.")
        else:
            result.add("There is no one else here.")

    def _cmd_go(self, destination: str, result: ActionResult) -> None:
        dest = destination.lower().strip()
        if dest in self.state.locations:
            self.state.current_location = dest
            loc = self.state.locations[dest]
            result.add(f"You travel to {loc.name}.")
            self._cmd_look(result)
        else:
            available = ", ".join(self.state.locations.keys())
            result.add(
                f"You can't go to '{destination}'. "
                f"Known locations: {available}."
            )

    def _cmd_talk(self, npc_name: str, result: ActionResult) -> None:
        if not npc_name:
            npcs_here = self.state.npcs_at_current_location()
            if not npcs_here:
                result.add("There is no one here to talk to.")
            else:
                names = ", ".join(n.name for n in npcs_here if n.is_active)
                result.add(f"Who do you want to talk to? ({names})")
            return

        # Find NPC and extract the remainder of the argument as player text.
        # e.g. "talk Old Tom hello" → NPC="Old Tom", player_text="hello"
        matched, player_text = self._find_npc_and_text(npc_name)
        if matched is None:
            result.add(f"There is no one called '{npc_name}' here.")
            return
        if not matched.is_active:
            result.add(f"{matched.name} doesn't seem to want to talk.")
            return

        agent = self._agent_for(matched.name)
        response = agent.respond(player_text, self.state.player.name)
        result.add(f"{self.state.player.name}: \"{player_text}\"")
        result.add(f"{matched.name}: \"{response}\"")
        self.state.record(f"Turn {self.state.turn}: dialogue with {matched.name}")

    def _cmd_inventory(self, result: ActionResult) -> None:
        items = self.state.player.inventory
        if items:
            result.add("You are carrying: " + ", ".join(items) + ".")
        else:
            result.add("You are not carrying anything.")

    def _cmd_help(self, result: ActionResult) -> None:
        result.add(
            "Commands:\n"
            "  look               – describe your current location\n"
            "  go <location>      – travel to a location\n"
            "  talk <npc> [text]  – speak with an NPC\n"
            "  inventory          – check what you're carrying\n"
            "  help               – show this message\n"
            "  quit               – end the game"
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _find_npc_and_text(self, argument: str) -> tuple[NPC | None, str]:
        """Parse *argument* (the part after 'talk') into an NPC and player text.

        Matching strategy (in order):
        1. Check whether *argument* starts with any NPC's full name
           (case-insensitive).  This handles multi-word names like "Old Tom".
        2. Fall back to first-word prefix matching.

        Returns ``(npc, player_text)`` where *player_text* is whatever follows
        the matched name portion, defaulting to ``"hello"`` if empty.
        """
        arg_lower = argument.lower()
        npcs_here = self.state.npcs_at_current_location()

        # Pass 1: full-name prefix match
        for npc in npcs_here:
            prefix = npc.name.lower()
            if arg_lower.startswith(prefix):
                remainder = argument[len(prefix):].strip()
                return npc, remainder or "hello"

        # Pass 2: first-word prefix match (e.g. "mir" → "Mira")
        first_word = argument.split()[0].lower() if argument else ""
        for npc in npcs_here:
            if npc.name.lower().startswith(first_word):
                # Strip the first token from argument to get player text
                parts = argument.split(maxsplit=1)
                remainder = parts[1].strip() if len(parts) > 1 else ""
                return npc, remainder or "hello"

        return None, ""
