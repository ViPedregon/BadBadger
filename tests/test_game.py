"""Tests for BadBadger game engine."""

import pytest

from badbadger.models import GameState, Location, NPC, Player
from badbadger.npc_agent import NPCAgent
from badbadger.game_master import GameMaster


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

def test_player_defaults():
    p = Player(name="Alice")
    assert p.health == 100
    assert p.inventory == []


def test_npc_add_dialogue():
    npc = NPC(name="Bob", description="A test NPC.")
    npc.add_dialogue("Alice", "Hello!")
    npc.add_dialogue("Bob", "Hi there.")
    assert len(npc.dialogue_history) == 2
    assert npc.dialogue_history[0] == {"speaker": "Alice", "text": "Hello!"}


def test_game_state_npcs_at_location():
    player = Player(name="Hero")
    loc = Location(name="Inn", description="An inn.", npcs=["Barkeep"])
    npc = NPC(name="Barkeep", description="The barkeep.")
    state = GameState(
        player=player,
        locations={"inn": loc},
        npcs={"Barkeep": npc},
        current_location="inn",
    )
    assert state.npcs_at_current_location() == [npc]


# ---------------------------------------------------------------------------
# NPC Agent tests
# ---------------------------------------------------------------------------

def test_npc_agent_greet_response():
    npc = NPC(name="Mira", description="A barmaid.")
    agent = NPCAgent(npc)
    response = agent.respond("hello", "Hero")
    assert "Mira" in response
    assert len(npc.dialogue_history) == 2  # player + npc lines


def test_npc_agent_identity_response():
    npc = NPC(name="Old Tom", description="A farmer.")
    agent = NPCAgent(npc)
    response = agent.respond("who are you?", "Hero")
    assert "Old Tom" in response


def test_npc_agent_farewell():
    npc = NPC(name="Sylvan", description="An elf.")
    agent = NPCAgent(npc)
    response = agent.respond("goodbye", "Hero")
    assert "farewell" in response.lower() or "safe" in response.lower()


def test_npc_agent_fallback():
    npc = NPC(name="Guard", description="A stoic guard.")
    agent = NPCAgent(npc)
    response = agent.respond("xyzzy", "Hero")
    assert isinstance(response, str)
    assert len(response) > 0


# ---------------------------------------------------------------------------
# Game Master tests
# ---------------------------------------------------------------------------

def test_new_game_creates_state():
    gm = GameMaster.new_game("Tester")
    assert gm.state.player.name == "Tester"
    assert gm.state.current_location == "tavern"
    assert not gm.state.game_over


def test_look_command():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("look")
    combined = "\n".join(result.messages)
    assert "Rusty Flagon" in combined
    assert not result.game_over


def test_go_command():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("go forest")
    combined = "\n".join(result.messages)
    assert "Whispering Forest" in combined
    assert gm.state.current_location == "forest"


def test_go_unknown_location():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("go nowhere")
    assert any("can't go" in m for m in result.messages)


def test_talk_command():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("talk Mira hello")
    combined = "\n".join(result.messages)
    assert "Mira" in combined


def test_talk_no_target():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("talk")
    assert any(result.messages)  # should get some feedback


def test_inventory_empty():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("inventory")
    assert any("not carrying" in m for m in result.messages)


def test_inventory_with_items():
    gm = GameMaster.new_game("Tester")
    gm.state.player.inventory.append("sword")
    result = gm.process_command("inventory")
    assert any("sword" in m for m in result.messages)


def test_quit_command():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("quit")
    assert result.game_over
    assert gm.state.game_over


def test_help_command():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("help")
    combined = "\n".join(result.messages)
    assert "look" in combined
    assert "talk" in combined


def test_unknown_command():
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("fly")
    assert any("help" in m for m in result.messages)


def test_talk_multiword_npc():
    """Talking to 'Old Tom' should not pass 'Tom hello' as player text."""
    gm = GameMaster.new_game("Tester")
    result = gm.process_command("talk Old Tom hello")
    combined = "\n".join(result.messages)
    assert "Old Tom" in combined
    # The NPC response line should exist; player line should show "hello" not "Tom hello"
    assert '"hello"' in combined or "hello" in combined.lower()
    assert "Tom hello" not in combined


def test_turn_increments():
    gm = GameMaster.new_game("Tester")
    assert gm.state.turn == 0
    gm.process_command("look")
    assert gm.state.turn == 1
    gm.process_command("look")
    assert gm.state.turn == 2
