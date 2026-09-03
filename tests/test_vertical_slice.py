"""End-to-end tests for the deterministic SQLite vertical slice."""

import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from badbadger.agents.context import NPCContextBuilder
from badbadger.agents.npc import (
    ActionProposal,
    DeterministicNPCBackend,
    FallbackNPCBackend,
    NPCResponse,
)
from badbadger.agents.openai_client import OpenAIResponsesClient
from badbadger.application import create_prototype, open_prototype
from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction, MoveAction, WaitAction
from badbadger.engine.dialogue import DialogueService


class VerticalSliceTests(unittest.TestCase):
    def test_state_time_event_and_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            engine = create_prototype(database)

            examined = engine.perform(ExamineAction("panel"))
            moved = engine.perform(MoveAction("room_b"))
            waited = engine.perform(WaitAction(3))

            self.assertTrue(examined.accepted)
            self.assertTrue(moved.accepted)
            self.assertEqual(engine.repository.current_time, 10)
            self.assertIn(
                "The lights in Room B flicker and go dark.", waited.messages
            )
            self.assertFalse(engine.repository.get_fact("room_b", "lights_on"))
            engine.repository.close()

            with GameRepository(database) as reloaded:
                self.assertEqual(reloaded.current_time, 10)
                self.assertEqual(reloaded.get_player()["location_id"], "room_b")
                self.assertFalse(reloaded.get_fact("room_b", "lights_on"))
                status = reloaded.connection.execute(
                    "SELECT status FROM scheduled_events"
                ).fetchone()["status"]
                self.assertEqual(status, "processed")

    def test_npc_beliefs_do_not_include_hidden_world_truth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            beliefs = engine.repository.beliefs_for("npc")

            self.assertEqual(len(beliefs), 1)
            self.assertEqual(beliefs[0]["subject_id"], "room_b")
            self.assertNotIn("contains_access_code", repr(beliefs))
            self.assertTrue(
                engine.repository.get_fact("panel", "contains_access_code")
            )
            context = NPCContextBuilder(engine.repository).build("npc")
            self.assertNotIn("contains_access_code", repr(context))
            engine.repository.close()

    def test_dialogue_and_belief_update_persist_across_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            app = open_prototype(database)

            messages, _ = app.handle("ask the Observer whether Room B is safe")
            self.assertIn("believe Room B is safe", messages[0])
            messages, _ = app.handle("tell Observer the lights in Room B are out")
            self.assertIn("remember", messages[0])
            self.assertEqual(app.repository.current_time, 2)
            app.repository.close()

            resumed = open_prototype(database)
            dialogue = resumed.repository.recent_dialogue("npc")
            self.assertEqual(len(dialogue), 4)
            self.assertEqual(
                dialogue[0]["text"], "ask the Observer whether Room B is safe"
            )
            lights = next(
                belief
                for belief in resumed.repository.beliefs_for("npc")
                if belief["predicate"] == "lights_on"
            )
            self.assertFalse(lights["value"])
            resumed.repository.close()

    def test_npc_action_proposal_cannot_directly_change_world_state(self):
        class ProposingBackend:
            def respond(self, context, player_input):
                return NPCResponse(
                    "Perhaps you should go elsewhere.",
                    proposed_actions=[
                        ActionProposal("move_player", {"destination_id": "room_b"})
                    ],
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            service = DialogueService(engine.repository, ProposingBackend())
            service.converse("npc", "Where should I go?")

            self.assertEqual(engine.repository.get_player()["location_id"], "room_a")
            record = engine.repository.connection.execute(
                "SELECT result_json FROM history WHERE record_type = 'dialogue_resolved'"
            ).fetchone()
            self.assertIn("rejected_action_proposals", record["result_json"])
            engine.repository.close()

    def test_openai_adapter_sends_filtered_context_and_maps_structured_output(self):
        class FakeResponses:
            def __init__(self):
                self.calls = []

            def parse(self, **kwargs):
                self.calls.append(kwargs)
                parsed = SimpleNamespace(
                    dialogue="A structured reply.",
                    proposed_actions=[
                        SimpleNamespace(kind="wait", parameters={"minutes": 1})
                    ],
                    belief_updates=[
                        SimpleNamespace(
                            subject_id="player",
                            predicate="is_curious",
                            value=True,
                            confidence=0.8,
                        )
                    ],
                )
                return SimpleNamespace(output_parsed=parsed)

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            context = NPCContextBuilder(engine.repository).build("npc")
            responses = FakeResponses()
            sdk = SimpleNamespace(responses=responses)
            client = OpenAIResponsesClient(
                "test-model", sdk_client=sdk, response_model=object
            )

            result = client.respond(context, "What do you know?")

            self.assertEqual(result.dialogue, "A structured reply.")
            self.assertEqual(result.proposed_actions[0].kind, "wait")
            self.assertTrue(result.belief_updates[0].value)
            call = responses.calls[0]
            self.assertEqual(call["model"], "test-model")
            self.assertIs(call["text_format"], object)
            self.assertFalse(call["store"])
            serialized_context = call["input"][1]["content"]
            self.assertNotIn("contains_access_code", serialized_context)
            self.assertNotIn("OPENAI_API_KEY", serialized_context)
            engine.repository.close()

    def test_openai_adapter_retries_one_unparsed_response(self):
        class RetryResponses:
            def __init__(self):
                self.calls = 0

            def parse(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return SimpleNamespace(output_parsed=None)
                return SimpleNamespace(
                    output_parsed=SimpleNamespace(
                        dialogue="Recovered.",
                        proposed_actions=[],
                        belief_updates=[],
                    )
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            context = NPCContextBuilder(engine.repository).build("npc")
            responses = RetryResponses()
            client = OpenAIResponsesClient(
                "test-model",
                sdk_client=SimpleNamespace(responses=responses),
                response_model=object,
            )

            with self.assertLogs("badbadger.agents.openai_client", level="WARNING"):
                result = client.respond(context, "Hello")

            self.assertEqual(result.dialogue, "Recovered.")
            self.assertEqual(responses.calls, 2)
            engine.repository.close()

    def test_failed_primary_backend_falls_back_without_corrupting_state(self):
        class FailingBackend:
            def respond(self, context, player_input):
                raise RuntimeError("simulated provider outage")

        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_prototype(Path(temp_dir) / "save.db")
            backend = FallbackNPCBackend(FailingBackend(), DeterministicNPCBackend())
            service = DialogueService(engine.repository, backend)

            with self.assertLogs("badbadger.agents.npc", level="ERROR"):
                messages = service.converse(
                    "npc", "ask the Observer whether Room B is safe"
                )

            self.assertIn("believe Room B is safe", messages[0])
            self.assertIn("did not advance time", messages[1])
            self.assertEqual(engine.repository.current_time, 0)
            self.assertEqual(len(engine.repository.recent_dialogue("npc")), 2)
            engine.repository.close()

    def test_text_commands_are_persisted_and_resumed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "save.db"
            app = open_prototype(database)

            messages, should_quit = app.handle("examine the panel")
            self.assertFalse(should_quit)
            self.assertIn("operating normally", messages[0])

            messages, _ = app.handle("travel to Room B")
            self.assertIn("Room B", messages[0])
            app.repository.close()

            resumed = open_prototype(database)
            self.assertEqual(resumed.repository.current_time, 7)
            self.assertEqual(resumed.repository.get_player()["location_id"], "room_b")
            messages, should_quit = resumed.handle("quit")
            self.assertTrue(should_quit)
            self.assertIn("saved", messages[0].lower())
            resumed.repository.close()

    def test_text_interpreter_rejects_unknown_input_without_advancing_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = open_prototype(Path(temp_dir) / "save.db")
            messages, should_quit = app.handle("perform quantum diplomacy")

            self.assertFalse(should_quit)
            self.assertIn("couldn't interpret", messages[0])
            self.assertEqual(app.repository.current_time, 0)
            app.repository.close()


if __name__ == "__main__":
    unittest.main()
