"""NPC Agent – generates dialogue responses for a single NPC.

Each NPC has its own agent instance that maintains its dialogue history and
produces contextually appropriate responses.  The default implementation uses
simple rule-based logic so the game works without any external API dependency.
A subclass can override ``respond`` to call an LLM or any other back-end.
"""

from __future__ import annotations

from badbadger.models import NPC


class NPCAgent:
    """Manages dialogue for one NPC."""

    def __init__(self, npc: NPC) -> None:
        self.npc = npc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def respond(self, player_text: str, player_name: str = "Player") -> str:
        """Return the NPC's response to *player_text*.

        Records both sides of the exchange in the NPC's dialogue history.
        """
        self.npc.add_dialogue(player_name, player_text)
        response = self._generate_response(player_text)
        self.npc.add_dialogue(self.npc.name, response)
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_response(self, player_text: str) -> str:
        """Rule-based response generation (no external dependencies)."""
        text_lower = player_text.lower()

        if any(word in text_lower for word in ("hello", "hi", "hey", "greet")):
            return (
                f"Well met, traveller. I am {self.npc.name}. "
                f"{self.npc.description}"
            )

        if any(word in text_lower for word in ("who are you", "name", "yourself")):
            return f"My name is {self.npc.name}. {self.npc.description}"

        if any(word in text_lower for word in ("bye", "farewell", "goodbye", "leave")):
            return "Farewell, adventurer. Safe travels."

        if any(word in text_lower for word in ("help", "quest", "task")):
            return (
                f"I may have something for you… but first you must prove yourself "
                f"worthy. Speak with me again when you are ready."
            )

        # Generic fallback
        return (
            f"{self.npc.name} considers your words carefully. "
            f"\"Interesting. Tell me more.\""
        )
