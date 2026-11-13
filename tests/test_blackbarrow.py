"""Complete chapter loops and the boundary between authored evidence and choice."""

from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path
import tempfile
import unittest

from badbadger.backup import snapshot
from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction
from badbadger.scenarios.loader import ScenarioError, load_scenario
from badbadger.service import RelayService, TurnConflict


class BlackbarrowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "save.db"
        self.game = RelayService(self.path, scenario="blackbarrow")
        self.revision = 0

    def command(self, text):
        response = self.game.submit(str(self.revision), text, self.revision)
        self.revision = response["revision"]
        return response

    def test_character_inspection_is_public_and_costs_no_time(self):
        before = self.game.view()
        profiles = {profile["id"]: profile for profile in before["characters"]}
        self.assertEqual(set(profiles), {"victor", "matilda", "horace"})
        self.assertTrue(profiles["victor"]["is_player"])
        self.assertIn("Tracking", profiles["matilda"]["attributes"]["strengths"])
        self.assertTrue(profiles["horace"]["motivations"])
        with GameRepository(self.path) as repository:
            for actor in ("victor", "matilda", "horace"):
                for fragment in repository.biography_fragments(actor):
                    if fragment["visibility"] != "public":
                        self.assertNotIn(fragment["summary"], profiles[actor]["background"])
            parameters = repository.npc_parameters("matilda")
            parameters["secret_attribute"] = "HIDDEN_ATTRIBUTE_MARKER"
            repository.set_npc_parameters("matilda", parameters)
            repository.connection.commit()
        after = self.game.view()
        self.assertNotIn("HIDDEN_ATTRIBUTE_MARKER", str(after["characters"]))
        self.assertNotIn("motivation_weights", str(after["characters"]))
        self.assertNotIn("decision_score", str(after["characters"]))
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(before["status"], after["status"])

    def test_plan_discussion_explains_tradeoffs_without_authorizing_actions(self):
        from unittest.mock import Mock
        backend = self.game.npc_backend
        self.game.npc_backend = Mock(side_effect=AssertionError("Authored advice must not need a model"))
        for phrase, expected in (
            ("ask Horace about controlled crossing", "ties up household guards"),
            ("Matilda, what do you think about the high track?", "Loaded grain wagons cannot follow"),
            ("ask Horace what concerns you about holding the convoy?", "medicine are delayed"),
        ):
            reply = self.command(phrase)
            text = " ".join(reply["messages"])
            self.assertIn(expected, text)
            self.assertIn("I have not committed", text)
            self.assertNotIn("decision_score", text)
            with GameRepository(self.path) as repository:
                self.assertFalse(repository.get_fact("chapter", "resolved"))
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.current_time, 3)
        self.assertEqual(self.game.npc_backend.respond.call_count, 0)
        self.game.npc_backend = backend
        self.command("Matilda, guide the high track")
        reply = self.command("ask Horace about grain guard")
        self.assertIn("guarded, not delivered", " ".join(reply["messages"]))
        with GameRepository(self.path) as repository:
            self.assertIsNone(repository.get_fact("convoy", "grain_guarded"))

    def test_plan_advice_rechecks_beliefs_trust_and_availability(self):
        with GameRepository(self.path) as repository:
            repository.connection.execute(
                "UPDATE beliefs SET confidence=0.4 WHERE character_id='matilda' AND subject_id='high_track' AND predicate='passable_on_foot'"
            )
            repository.connection.commit()
        reply = self.command("ask Matilda about high track")
        self.assertIn("inspect the ridge markers", " ".join(reply["messages"]))
        self.assertNotIn("would guide", " ".join(reply["messages"]))
        self.command("go to High Track")
        self.command("examine ridge markers")
        self.command("go to Gatehouse")
        reply = self.command("ask Matilda about high track")
        self.assertIn("would guide", " ".join(reply["messages"]))
        with GameRepository(self.path) as repository:
            repository.connection.execute("UPDATE relationships SET trust=0.1 WHERE observer_id='horace' AND target_id='victor'")
            repository.connection.commit()
        reply = self.command("ask Horace about controlled crossing")
        self.assertIn("need more trust", " ".join(reply["messages"]))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("chapter", "resolved"))
            self.assertIn("not available", repository.discuss_plan("horace", "grain guard"))
            self.assertIsNone(repository.discuss_plan("horace", "secret plan"))

    def test_supplied_portrait_appears_without_resetting_the_save(self):
        from unittest.mock import patch
        root = Path(self.temp.name)
        portraits = root / "static" / "portraits"
        portraits.mkdir(parents=True)
        (portraits / "matilda_ash.png").write_bytes(b"test portrait")
        with patch("badbadger.application.files", return_value=root):
            profiles = {profile["id"]: profile for profile in self.game.view()["characters"]}
        self.assertEqual(profiles["matilda"]["portrait"], "/assets/portraits/matilda_ash.png")
        self.assertIsNone(profiles["horace"]["portrait"])
        self.assertEqual(self.game.view()["revision"], 0)

    def test_character_conversation_options_follow_presence_and_available_plans(self):
        profiles = {p["id"]: p for p in self.game.view()["characters"]}
        self.assertEqual(profiles["victor"]["conversation_options"], [])
        self.assertEqual(profiles["matilda"]["availability"], "here")
        options = profiles["matilda"]["conversation_options"]
        self.assertTrue(all(not option["consequential"] for option in options))
        self.assertIn("ask Matilda Ash about high track", [option["command"] for option in options])
        self.assertNotIn("grain guard", str(profiles["horace"]["conversation_options"]))
        self.command("go to High Track")
        profiles = {p["id"]: p for p in self.game.view()["characters"]}
        self.assertEqual(profiles["matilda"]["availability"], "here")
        self.assertEqual(len(profiles["matilda"]["conversation_options"]), 1)
        self.assertIn("priority", profiles["matilda"]["conversation_options"][0]["command"])

    def test_character_cards_do_not_offer_conversation_with_busy_or_absent_companions(self):
        with GameRepository(self.path) as repository:
            repository.create_travel("matilda", "high_track", 3)
            repository.connection.execute("UPDATE characters SET location_id='causeway' WHERE id='horace'")
            repository.connection.commit()
        profiles = {p["id"]: p for p in self.game.view()["characters"]}
        self.assertEqual(profiles["matilda"]["availability"], "travelling")
        self.assertEqual(profiles["horace"]["availability"], "away")
        for actor in ("matilda", "horace"):
            self.assertEqual(profiles[actor]["conversation_options"], [])
            self.assertNotIn("location_id", profiles[actor])
            self.assertNotIn("pending_activity", profiles[actor])

    def test_character_conversation_changes_to_aftermath_and_stops_at_chapter_end(self):
        self.command("Matilda, guide the high track")
        profiles = {p["id"]: p for p in self.game.view()["characters"]}
        self.assertEqual(profiles["matilda"]["conversation_options"][0]["command"], "ask Matilda Ash what now")
        self.assertIn("grain guard", str(profiles["horace"]["conversation_options"]))
        self.command("wait 6")
        for profile in self.game.view()["characters"]:
            self.assertEqual(profile["conversation_options"], [])
            if not profile["is_player"]:
                self.assertEqual(profile["availability"], "ended")

    def test_matilda_conditions_refusal_and_reconsideration_follow_beliefs(self):
        with GameRepository(self.path) as repository:
            repository.connection.execute(
                "UPDATE beliefs SET confidence=0.4 WHERE character_id='matilda' AND subject_id='high_track' AND predicate='passable_on_foot'"
            )
            repository.connection.commit()
            self.assertEqual(repository.player_order_response("matilda", "high_track")["status"], "condition")
        result = self.command("Matilda, guide the high track")
        self.assertIn("inspect the ridge markers", " ".join(result["messages"]))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("chapter", "resolved"))
            repository.connection.execute(
                "UPDATE beliefs SET value_json='false', confidence=1.0 WHERE character_id='matilda' AND subject_id='high_track' AND predicate='passable_on_foot'"
            )
            repository.connection.commit()
            self.assertEqual(repository.player_order_response("matilda", "high_track")["status"], "refuse")
        result = self.command("Matilda, guide the high track")
        self.assertIn("cannot justify", " ".join(result["messages"]))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("chapter", "resolved"))
        self.command("go to High Track")
        self.command("examine ridge markers")
        self.command("go to Gatehouse")
        self.game = RelayService(self.path)
        self.command("Matilda, guide the high track")
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.get_fact("chapter", "outcome"), "high_track")

    def test_horace_reconsiders_guard_order_without_spending_guards_on_refusal(self):
        self.command("Matilda, guide the high track")
        with GameRepository(self.path) as repository:
            repository.connection.execute(
                "UPDATE beliefs SET confidence=0.4 WHERE character_id='horace' AND subject_id='holding' AND predicate='delays_supplies'"
            )
            repository.connection.commit()
        result = self.command("Horace, arrange a guard")
        self.assertIn("examine the convoy petition", " ".join(result["messages"]))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("convoy", "guards_committed"))
        self.command("examine convoy petition")
        with GameRepository(self.path) as repository:
            belief = next(item for item in repository.beliefs_for("horace")
                          if item["subject_id"] == "holding" and item["predicate"] == "delays_supplies")
            self.assertEqual(belief["confidence"], 1.0)
            self.assertEqual(belief["evidence"][-1]["source_type"], "direct")
            self.assertEqual(repository.player_order_response("horace", "guard_grain")["status"], "accept")
            self.assertFalse(repository.get_fact("convoy", "guards_committed"))
        self.game = RelayService(self.path)
        discussion = self.command("ask Horace about grain guard")
        self.assertIn("would post that guard", " ".join(discussion["messages"]))
        self.command("Horace, arrange a guard")
        with GameRepository(self.path) as repository:
            self.assertTrue(repository.get_fact("convoy", "grain_guarded"))

    def test_order_decision_reuses_goal_and_personality_scoring(self):
        with GameRepository(self.path) as repository:
            initial = repository.player_order_response("matilda", "high_track")
            self.assertEqual(initial["status"], "accept")
            repository.set_goal_status("matilda", "find_passage", "abandoned", "Test different priorities.")
            self.assertEqual(repository.player_order_response("matilda", "high_track")["status"], "condition")
            row = repository.connection.execute("SELECT effects_json FROM npc_actions WHERE id='high_track'").fetchone()
            effects = json.loads(row[0])
            effects["decision"]["parameter_factors"] = [{
                "parameter": "independent_field_judgment", "operator": "equals", "value": True,
                "score": 0.5, "reason": "Confidence in independent field judgment.",
            }]
            repository.connection.execute("UPDATE npc_actions SET effects_json=? WHERE id='high_track'", (json.dumps(effects),))
            parameters = repository.npc_parameters("matilda")
            parameters["independent_field_judgment"] = True
            repository.set_npc_parameters("matilda", parameters)
            self.assertEqual(repository.player_order_response("matilda", "high_track")["status"], "accept")

    def test_motivation_conflict_changes_cooperation_and_survives_resume(self):
        with GameRepository(self.path) as repository:
            original = repository.player_order_response("horace", "controlled_crossing")
            self.assertEqual(original["status"], "accept")
            parameters = repository.npc_parameters("horace")
            parameters["motivation_weights"]["protect_convoy"] = 0.0
            repository.set_npc_parameters("horace", parameters)
            changed = repository.player_order_response("horace", "controlled_crossing")
            self.assertEqual(changed["status"], "condition")
            self.assertAlmostEqual(original["decision_score"] - changed["decision_score"], .4)
            self.assertEqual(repository.player_order_response("horace", "hold_convoy")["status"], "accept")
            repository.connection.commit()
        self.game = RelayService(self.path)
        reply = self.command("Horace, oversee a controlled crossing")
        self.assertIn("examine the causeway ruts", " ".join(reply["messages"]))
        self.assertNotIn("motivation strength", str(reply))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("chapter", "resolved"))
            parameters = repository.npc_parameters("horace")
            parameters["motivation_weights"]["preserve_readiness"] = 0.0
            repository.set_npc_parameters("horace", parameters)
            self.assertEqual(repository.player_order_response("horace", "controlled_crossing")["status"], "accept")

    def test_autonomy_cannot_bypass_a_companions_condition(self):
        with GameRepository(self.path) as repository:
            repository.connection.execute(
                "UPDATE beliefs SET confidence=0.4 WHERE character_id='matilda' AND subject_id='high_track' AND predicate='passable_on_foot'"
            )
            action = next(a for a in repository.available_npc_actions("matilda") if a["id"] == "high_track")
            self.assertFalse(action["autonomous_eligible"])
            accepted, _ = repository.apply_npc_action("matilda", "high_track", autonomous=True)
            self.assertFalse(accepted)
            self.assertFalse(repository.get_fact("chapter", "resolved"))

    def test_matilda_motivations_affect_willingness_without_changing_her_beliefs(self):
        with GameRepository(self.path) as repository:
            beliefs = repository.beliefs_for("matilda")
            self.assertEqual(repository.player_order_response("matilda", "high_track")["status"], "accept")
            parameters = repository.npc_parameters("matilda")
            parameters["motivation_weights"] = {key: 0.0 for key in parameters["motivation_weights"]}
            repository.set_npc_parameters("matilda", parameters)
            self.assertEqual(repository.player_order_response("matilda", "high_track")["status"], "condition")
            self.assertEqual(repository.beliefs_for("matilda"), beliefs)
            parameters["motivation_weights"]["independent_judgment"] = True
            with self.assertRaises(ValueError):
                repository.set_npc_parameters("matilda", parameters)

    def test_motivation_policy_validation_precedes_database_creation(self):
        original = json.loads(files("badbadger.scenarios").joinpath("blackbarrow_north_road.json").read_text(encoding="utf-8"))
        # Inline identities so the temporary scenario needs no adjacent files.
        for actor in original["characters"]:
            profile = json.loads(files("badbadger.scenarios").joinpath(actor.pop("identity_file")).read_text(encoding="utf-8"))
            actor["name"], actor["kind"] = profile["name"], profile["kind"]
        for invalid in (True, -0.1, 1.1, float("nan"), "strong"):
            malformed = deepcopy(original)
            malformed["characters"][1]["parameters"]["motivation_weights"]["independent_judgment"] = invalid
            source = Path(self.temp.name) / "invalid.json"
            destination = Path(self.temp.name) / "invalid.db"
            source.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(ScenarioError):
                load_scenario(source, destination)
            self.assertFalse(destination.exists())
        for invalid in ({"motivation_id": "unknown", "impact": .2, "reason": "Test"},
                        {"motivation_id": "independent_judgment", "impact": float("inf"), "reason": "Test"}):
            malformed = deepcopy(original)
            malformed["npc_actions"][1]["effects"]["decision"]["motivation_factors"] = [invalid]
            source.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(ScenarioError):
                load_scenario(source, destination)
            self.assertFalse(destination.exists())

    def test_all_three_plans_complete_with_different_costs_and_save_resume(self):
        paths = {
            "controlled_crossing": ["examine convoy petition", "ask Horace what he knows", "go to Causeway",
                                    "examine causeway ruts", "go to Gatehouse", "tell Horace oversee a controlled crossing"],
            "high_track": ["examine convoy petition", "ask Matilda what she knows", "go to High Track",
                           "examine ridge markers", "go to Gatehouse", "tell Matilda guide the high track"],
            "hold_convoy": ["examine convoy petition", "ask Horace what he knows", "tell Horace hold the convoy"],
        }
        for index, (outcome, commands) in enumerate(paths.items()):
            with self.subTest(outcome=outcome):
                self.path = Path(self.temp.name) / f"branch-{index}.db"
                self.game = RelayService(self.path, scenario="blackbarrow")
                self.revision = 0
                for command in commands[:3]:
                    self.command(command)
                saved = self.game.view()
                self.game = RelayService(self.path)  # Default changed; this must still resume Blackbarrow.
                self.assertEqual(self.game.view(), saved)
                for command in commands[3:]:
                    self.command(command)
                self.assertEqual(self.game.view()["guidance"]["phase"], "aftermath")
                self.assertIn("You asked", str(self.game.view()["guidance"]["recap"]))
                self.command("ask Matilda what now")
                self.command("wait 6 minutes")
                self.assertEqual(self.game.view()["guidance"]["phase"], "ended")
                with GameRepository(self.path) as repository:
                    self.assertEqual(repository.get_fact("chapter", "outcome"), outcome)
                    self.assertEqual(repository.get_fact("convoy", "people_sheltered"), True)
                    self.assertEqual(repository.get_fact("convoy", "grain_delivered"), outcome == "controlled_crossing")
                    self.assertEqual(repository.get_fact("convoy", "medicine_delivered"), outcome != "hold_convoy")
                    self.assertEqual(repository.get_fact("convoy", "stores_spent"), outcome == "hold_convoy")
                    self.assertEqual(repository.get_player()["name"], "Victor Gravenholt")
                    self.assertEqual(repository.character_identity("matilda")["revision"], 2)
                recap = " ".join(self.command("summary")["messages"])
                self.assertIn("Chapter complete", recap)
                self.assertNotIn("decision_score", recap)
                self.assertNotIn("Alden", recap)

    def test_blocked_orders_explain_location_and_plan_prerequisite(self):
        from unittest.mock import Mock

        backend = self.game.npc_backend
        self.game.npc_backend = Mock(wraps=backend)
        def respond(context, text):
            if text.lower() in {"matilda, guide the high track", "horace, arrange a guard for the wagons."}:
                raise AssertionError("Order reached dialogue backend")
            return backend.respond(context, text)
        self.game.npc_backend.respond.side_effect = respond
        self.command("go to High Track")
        self.command("examine ridge markers")
        route = self.command("Matilda, guide the high track")["messages"]
        guard = self.command("Horace, arrange a guard for the wagons.")["messages"]
        self.assertIn("Return to the Gatehouse", " ".join(route))
        self.assertIn("once we commit to the high-track plan", " ".join(guard))
        self.assertNotIn("No movement was requested", " ".join(route + guard))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("chapter", "resolved"))
            self.assertIsNone(repository.get_fact("convoy", "grain_guarded"))
        self.command("go to Gatehouse")
        self.command("Matilda, guide the high track")
        guard = self.command("Horace, arrange a guard for the wagons.")["messages"]
        self.assertIn("grain is guarded, not delivered", " ".join(guard))

    def test_model_interprets_paraphrases_and_engine_checks_order_state(self):
        from unittest.mock import Mock
        from badbadger.agents.npc import ActionProposal, NPCResponse

        backend = self.game.npc_backend
        def respond(context, text):
            if "look after it" in text:
                self.assertIn("guard_grain", context.interpretable_orders)
                return NPCResponse("Which supplies do you want me to guard?")
            if "watch over the carts" in text:
                self.assertIn("guard_grain", context.interpretable_orders)
                return NPCResponse("Done.", [ActionProposal("interact", {"action_id": "guard_grain"})])
            if "shepherd the families" in text:
                self.assertIn("high_track", context.interpretable_orders)
                return NPCResponse("Done.", [ActionProposal("interact", {"action_id": "high_track"})])
            return backend.respond(context, text)

        self.game.npc_backend = Mock(wraps=backend)
        self.game.npc_backend.respond.side_effect = respond
        self.command("go to High Track")
        route = "Matilda, shepherd the families over the ridge."
        self.assertIn("Return to the Gatehouse", " ".join(self.command(route)["messages"]))
        self.command("go to Gatehouse")
        guard = "Horace, have your people watch over the carts."
        self.assertIn("once we commit", " ".join(self.command(guard)["messages"]))
        self.command(route)
        ambiguous = self.command("Horace, look after it.")
        self.assertIn("Which supplies", " ".join(ambiguous["messages"]))
        self.command("Horace, don't watch over the carts.")
        self.command("Horace, should we watch over the carts?")
        with GameRepository(self.path) as repository:
            self.assertIsNone(repository.get_fact("convoy", "grain_guarded"))
        self.assertIn("grain is guarded, not delivered", " ".join(self.command(guard)["messages"]))
        self.assertIn("already have a guard", " ".join(self.command(guard)["messages"]))

    def test_guard_grain_followup_is_validated_persistent_and_not_delivery(self):
        self.assertNotIn("arrange a guard", str(self.game.view()["guidance"]["suggestions"]))
        self.command("tell Horace arrange a guard for the grain")
        with GameRepository(self.path) as repository:
            self.assertIsNone(repository.get_fact("convoy", "grain_guarded"))
        self.command("tell Matilda guide the high track")
        self.assertIn("arrange a guard", str(self.game.view()["guidance"]["suggestions"]))
        self.command("ask Captain Horace why we should arrange a guard for the grain")
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("convoy", "guards_committed"))
        result = self.command("Captain Horace, arrange a guard")
        self.assertIn("grain is guarded, not delivered", " ".join(result["messages"]))
        self.game = RelayService(self.path)
        with GameRepository(self.path) as repository:
            self.assertTrue(repository.get_fact("convoy", "grain_guarded"))
            self.assertTrue(repository.get_fact("convoy", "guards_committed"))
            self.assertFalse(repository.get_fact("convoy", "grain_delivered"))
            self.assertEqual(repository.get_fact("chapter", "outcome"), "high_track")
        self.assertNotIn("arrange a guard", str(self.game.view()["guidance"]["suggestions"]))
        self.command("wait 6")
        self.assertIn("guard for the grain", " ".join(self.command("summary")["messages"]))

    def test_delegation_phrases_and_stages_survive_resume_and_unfinished_end(self):
        self.assertEqual([s["status"] for s in self.game.view()["guidance"]["stages"]], ["available"])
        self.command("tell Matilda guide the high track")
        stages = self.game.view()["guidance"]["stages"]
        self.assertEqual([s["status"] for s in stages], ["completed", "available"])
        self.assertTrue(stages[1]["optional"])
        self.assertNotIn("action_ids", str(stages))
        self.command("tell Horace don't secure what we leave behind")
        self.assertEqual(self.game.view()["guidance"]["stages"][1]["status"], "available")
        self.command("Captain Horace, could you secure what we leave behind?")
        expected = self.game.view()["guidance"]["stages"]
        self.assertEqual([s["status"] for s in expected], ["completed", "completed"])
        self.game = RelayService(self.path)
        self.assertEqual(self.game.view()["guidance"]["stages"], expected)
        self.assertIn("completed", " ".join(self.command("objective")["messages"]))

        self.path = Path(self.temp.name) / "unfinished.db"
        self.game = RelayService(self.path, scenario="blackbarrow")
        self.revision = 0
        self.command("tell Matilda guide the high track")
        self.command("wait 6")
        self.assertEqual(self.game.view()["guidance"]["stages"][1]["status"], "not completed")

    def test_authored_delegation_phrases_preserve_consent_boundaries(self):
        from badbadger.engine.consent import requests_action
        for phrase in ("guard the grain", "secure the grain", "protect the grain wagons"):
            self.assertTrue(requests_action("Horace, " + phrase, "horace", "Captain Horace", "Arrange a guard for the grain", [phrase]))
            for prefix in ("why should you ", "don't ", "if possible ", "explain "):
                self.assertFalse(requests_action("Horace, " + prefix + phrase, "horace", "Captain Horace", "Arrange a guard for the grain", [phrase]))

    def test_scoped_examination_is_not_available_remotely_and_records_direct_evidence(self):
        view = self.game.view()
        self.assertNotIn("examine ridge markers", str(view["guidance"]["suggestions"]))
        with GameRepository(self.path) as repository:
            from badbadger.engine.simulation import SimulationEngine
            result = SimulationEngine(repository).perform(ExamineAction("ridge_markers"))
            self.assertFalse(result.accepted)
            self.assertEqual(repository.current_time, 0)
        self.command("go to High Track")
        self.command("examine ridge markers")
        with GameRepository(self.path) as repository:
            self.assertEqual(len(repository.characters_at("high_track")), 3)
            for character in ("victor", "matilda", "horace"):
                knowledge = next(item for item in repository.beliefs_for(character) if item["predicate"] == "passable_on_foot")
                self.assertEqual(knowledge["evidence"][-1]["source_type"], "direct")
                self.assertTrue(repository.memories_for(character))
        self.assertIn("high track", " ".join(self.command("journal")["messages"]))

    def test_companions_already_trust_victor_and_travel_on_validated_routes(self):
        with GameRepository(self.path) as repository:
            for actor in ("matilda", "horace"):
                relationship = next(row for row in repository.relationships_for(actor) if row["target_id"] == "victor")
                self.assertEqual(relationship["trust"], .8)
        self.command("go to Causeway")
        with GameRepository(self.path) as repository:
            self.assertEqual({row["id"] for row in repository.characters_at("causeway")}, {"victor", "matilda", "horace"})
            self.assertEqual(repository.current_time, 2)
            self.assertIsNone(repository.pending_activity("matilda"))
            self.assertEqual(repository.connection.execute("SELECT count(*) FROM history WHERE record_type='npc_spoke'").fetchone()[0], 0)

    def test_busy_companion_is_not_teleported_or_available_for_conversation(self):
        with GameRepository(self.path) as repository:
            with repository.transaction():
                repository.create_travel("matilda", "high_track", 3)
            reply = self.game._app(repository).dialogue.converse("matilda", "ask Matilda what she knows")
            self.assertIn("travelling", reply[0])
        self.command("go to Causeway")
        with GameRepository(self.path) as repository:
            self.assertEqual(repository.get_character("horace")["location_id"], "causeway")
            self.assertIsNotNone(repository.pending_activity("matilda"))
            self.assertNotIn("matilda", {row["id"] for row in repository.characters_at("gatehouse")})

    def test_question_is_not_commitment_and_inspection_is_not_a_mandatory_unlock(self):
        result = self.command("ask Horace why we should oversee a controlled crossing")
        self.assertIn("controlled crossing", " ".join(result["messages"]))
        with GameRepository(self.path) as repository:
            self.assertFalse(repository.get_fact("chapter", "resolved"))
        self.command("tell Horace hold the convoy")
        self.assertEqual(self.game.view()["guidance"]["phase"], "aftermath")

    def test_waiting_allows_independent_action_without_player_credit(self):
        refused = self.command("wait 100")
        self.assertIn("between 1 and 6", refused["messages"][0])
        for _ in range(4):
            self.command("wait 6")
        recap = " ".join(self.game.view()["guidance"]["recap"])
        self.assertIn("high track", recap)
        self.assertNotIn("You asked", recap)
        reply = " ".join(self.command("ask Matilda why")["messages"])
        self.assertNotIn("your part in what happened", reply)
        self.command("wait 6")
        self.assertEqual(self.game.view()["guidance"]["phase"], "ended")

    def test_chapter_switch_archives_old_state_and_retries_cannot_switch_twice(self):
        relay_path = Path(self.temp.name) / "relay.db"
        relay = RelayService(relay_path)
        old = relay.submit("move", "go to Relay Chamber", 0)
        resumed = RelayService(relay_path, scenario="blackbarrow")
        self.assertEqual(resumed.view()["scenario"]["key"], "relay_crisis")
        result = resumed.new_game("switch", 1, confirmed=True, scenario="blackbarrow")
        self.assertEqual(resumed.new_game("switch", 1, confirmed=True, scenario="blackbarrow"), result)
        with self.assertRaises(TurnConflict):
            resumed.new_game("switch", 1, confirmed=True, scenario="relay_crisis")
        with self.assertRaises(TurnConflict):
            resumed.submit("stale", "go to Relay Chamber", 1)
        with self.assertRaises(ValueError):
            resumed.new_game("bad", 2, confirmed=True, scenario="../../private")
        self.assertEqual(resumed.submit("move", "go to Relay Chamber", 0), old)
        self.assertEqual(resumed.view()["scenario"]["key"], "blackbarrow")
        resumed.new_game("back", 2, confirmed=True, scenario="relay_crisis")
        with GameRepository(relay_path) as repository:
            archives = repository.connection.execute("SELECT state_json FROM playthrough_archives ORDER BY end_revision").fetchall()
            self.assertEqual(len(archives), 2)
            self.assertEqual(json.loads(archives[0][0])["scenario_id"], "relay-crisis-0.1")
            self.assertEqual(json.loads(archives[1][0])["scenario_id"], "blackbarrow-north-road-0.1")

    def test_backup_restore_contains_identity_evidence_and_active_chapter(self):
        self.command("go to Causeway")
        self.command("examine causeway ruts")
        original = self.game.view()
        backup = snapshot(self.path, Path(self.temp.name) / "backup.db")
        self.assertEqual(RelayService(backup).view(), original)
        with GameRepository(backup) as repository:
            self.assertEqual(repository.character_identity("horace")["name"], "Captain Horace")
            self.assertTrue(repository.beliefs_for("victor"))

    def test_invalid_scoped_observations_are_rejected_before_save_creation(self):
        original = json.loads(files("badbadger.scenarios").joinpath("blackbarrow_north_road.json").read_text(encoding="utf-8"))
        # Use no identity references in this malformed fixture, so the specific
        # authoring error is what prevents creating the database.
        for actor in original["characters"]:
            identity = actor.pop("identity_file")
            profile = json.loads(files("badbadger.scenarios").joinpath(identity).read_text(encoding="utf-8"))
            actor["name"], actor["kind"] = profile["name"], profile["kind"]
        for field, value in (("location_id", "missing"), ("beliefs", "not-a-list")):
            malformed = deepcopy(original)
            malformed["examinations"][0][field] = value
            source = Path(self.temp.name) / "bad.json"
            source.write_text(json.dumps(malformed))
            destination = Path(self.temp.name) / "bad.db"
            with self.assertRaises(ScenarioError):
                load_scenario(source, destination)
            self.assertFalse(destination.exists())

        for change in ("cycle", "private_action", "bad_phrases", "bad_advice"):
            malformed = deepcopy(original)
            if change == "cycle":
                malformed["presentation"]["objective_stages"][0]["depends_on"] = "grain_security"
            elif change == "private_action":
                malformed["presentation"]["objective_stages"][0]["action_ids"] = ["unpublished_action"]
            elif change == "bad_advice":
                malformed["npc_actions"][0]["effects"]["plan_advice"]["cost"] = ""
            else:
                malformed["npc_actions"][-1]["effects"]["request_phrases"] = [""]
            source.write_text(json.dumps(malformed))
            with self.assertRaises(ScenarioError):
                load_scenario(source, destination)
            self.assertFalse(destination.exists())
