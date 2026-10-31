"""Validate and apply NPC dialogue results without granting backend authority."""

from __future__ import annotations

import re

from badbadger.agents.context import NPCContextBuilder
from badbadger.agents.npc import NPCBackend, NPCResponse
from badbadger.db.repository import GameRepository
from badbadger.engine.events import process_due_events
from badbadger.engine.npc_actions import resolve_npc_proposal
from badbadger.engine.consent import requests_action, requests_move
from badbadger.engine.knowledge import grounded_claim, guard_response, inspection_request


class DialogueService:
    def __init__(self, repository: GameRepository, backend: NPCBackend) -> None:
        self.repository = repository
        self.backend = backend
        self.context_builder = NPCContextBuilder(repository)

    def converse(self, npc_id: str, player_input: str) -> list[str]:
        player = self.repository.get_player()
        npc = self.repository.get_character(npc_id)
        if npc is None or npc["kind"] != "npc":
            return ["That person is unavailable."]
        if npc["location_id"] != player["location_id"]:
            return [f"{npc['name']} is not here."]
        if self.repository.pending_activity(npc_id) is not None:
            return [f"{npc['name']} is travelling. Speak with them after they arrive."]
        if inspection_request(player_input):
            return [f'{npc["name"]}: "Ask me about the situation or what I believe. I cannot provide private records."']

        context = self.context_builder.build(npc_id, player_input)
        aftermath_question = bool(re.search(
            r"\bwhy\b|\bwhat now\b|\bhow do you feel\b|\bwhat happened\b|\boutcome\b",
            player_input, re.IGNORECASE,
        ))
        covered_cues = (
            self.repository.pending_goal_reaction_ids(npc_id)
            if aftermath_question and any(goal.get("status", "active") != "active" for goal in context.goals)
            else []
        )
        response = self.backend.respond(context, player_input)
        self._validate_response(response)
        response, protected_output = guard_response(self.repository, response)
        presented_dialogue = ("I haven't taken that action." if response.degraded and response.proposed_actions
                              else response.dialogue)

        with self.repository.transaction():
            self.repository.append_dialogue(npc_id, player["id"], player_input)
            if not response.degraded:
                # Only old recaps are covered: new outcomes during this exchange
                # must still be eligible for reactions, as must other NPCs.
                self.repository.cover_goal_reactions(npc_id, covered_cues)
                event_messages=[]
                proposal_results=[]
                for proposal in response.proposed_actions:
                    if proposal.kind == "interact" and not self._interaction_requested(
                        context.available_actions, proposal.parameters.get("action_id"),
                        player_input,
                        npc_id, npc["name"],
                    ):
                        accepted = False
                        message = (
                            "(No consequential action was taken; explicitly request "
                            "the interaction if you want it performed.)"
                        )
                    elif proposal.kind == "move" and not requests_move(
                        player_input, npc_id, npc["name"], context.available_actions, proposal.parameters.get("target_id"),
                    ):
                        accepted, message = False, "No movement was requested; no action was taken."
                    else:
                        accepted,message=resolve_npc_proposal(
                            self.repository, npc_id, proposal, requested_by_player=True
                        )
                    proposal_results.append({"accepted":accepted,"proposal":proposal.__dict__})
                    if proposal.kind in {"interact", "move"} and not accepted:
                        presented_dialogue = "I haven't taken that action."
                    if message:
                        event_messages.append(message)
                belief_results = []
                updated_beliefs = set()
                for proposal in response.belief_updates:
                    key = (proposal.subject_id, proposal.predicate)
                    if key in updated_beliefs or not grounded_claim(self.repository, context, player_input, proposal):
                        belief_results.append({"accepted": False, "reason": "No new grounded player assertion.",
                                               "proposal": proposal.__dict__})
                        continue
                    updated_beliefs.add(key)
                    calibration = self.repository.hearsay_confidence(
                        npc_id, player["id"], proposal.confidence
                    )
                    self.repository.set_belief(
                        npc_id,
                        proposal.subject_id,
                        proposal.predicate,
                        proposal.value,
                        calibration["effective_confidence"],
                        source_type="hearsay",
                        source_character_id=player["id"],
                        detail=(
                            f"{player_input} "
                            f"(claimed={proposal.confidence:.3f}, "
                            f"source_trust={calibration['source_trust']:.3f})"
                        ),
                    )
                    belief_results.append({
                        "accepted": True,
                        "proposal": proposal.__dict__,
                        "calibration": calibration,
                    })
                disclosure_results = []
                requested_disclosures = set()
                eligible_disclosures = {item["id"] for item in context.available_disclosures if item["eligible"]}
                for proposal in response.disclosure_requests:
                    if proposal.disclosure_id in requested_disclosures:
                        continue
                    requested_disclosures.add(proposal.disclosure_id)
                    if proposal.disclosure_id not in eligible_disclosures:
                        accepted, message = False, "I don't know you well enough to discuss that."
                    else:
                        accepted, message = self.repository.resolve_disclosure(
                            npc_id, player["id"], proposal.disclosure_id
                        )
                    disclosure_results.append({
                        "accepted": accepted,
                        "disclosure_id": proposal.disclosure_id,
                    })
                    if message:
                        event_messages.append(f'{npc["name"]}: "{message}"')
                relationship_results = []
                updated_relationships = set()
                visible_ids = {
                    character["id"] for character in context.visible_characters
                }
                for proposal in response.relationship_updates:
                    if proposal.target_id not in visible_ids or proposal.target_id in updated_relationships:
                        relationship_results.append({
                            "accepted": False,
                            "proposal": proposal.__dict__,
                            "reason": "Target is not visible or already updated this exchange.",
                        })
                        continue
                    updated_relationships.add(proposal.target_id)
                    values = self.repository.adjust_relationship(
                        npc_id,
                        proposal.target_id,
                        proposal.trust_delta,
                        proposal.affinity_delta,
                        proposal.reason,
                    )
                    relationship_results.append({
                        "accepted": True,
                        "proposal": proposal.__dict__,
                        "result": values,
                    })
                # A successful exchange advances time, then normal events run.
                self.repository.append_dialogue(npc_id, npc_id, presented_dialogue)
                self.repository.add_memory(npc_id, npc["location_id"], "conversation",
                    f'{player["name"]} said: "{player_input}"', salience=0.45,
                    source_character_id=player["id"])
                self.repository.add_memory(npc_id, npc["location_id"], "conversation",
                    f'I said: "{presented_dialogue}"', salience=0.45,
                    source_character_id=npc_id)
                self.repository.add_memory(player["id"], player["location_id"], "conversation",
                    f'{npc["name"]} said: "{presented_dialogue}"', salience=0.45,
                    source_character_id=npc_id)
                self.repository.advance_time(1)
                event_messages.extend(process_due_events(self.repository))
            else:
                self.repository.append_dialogue(npc_id, npc_id, presented_dialogue)
                event_messages = ["No action was taken."] if protected_output else [
                    "(AI dialogue was temporarily unavailable. This offline reply "
                    "did not advance time.)"
                ]
            self.repository.record(
                "dialogue_resolved",
                actor_id=player["id"],
                input_data={"npc_id": npc_id, "player_input": player_input},
                result_data={
                    "dialogue": presented_dialogue,
                    "degraded": response.degraded,
                    "protected_output_blocked": protected_output,
                    "biography_context": [
                        {
                            "id": item["id"],
                            "score": item["score"],
                            "selection_reasons": item["selection_reasons"],
                        }
                        for item in context.biography
                    ],
                    "belief_updates": belief_results if not response.degraded else [],
                    "relationship_updates": (
                        relationship_results if not response.degraded else []
                    ),
                    "disclosures": disclosure_results if not response.degraded else [],
                    "action_proposals": proposal_results if not response.degraded else [],
                },
            )
        return [f"{npc['name']}: \"{presented_dialogue}\"", *event_messages]

    @staticmethod
    def _interaction_requested(
        available_actions: list[dict[str, object]],
        action_id: object,
        player_input: str,
        npc_id: str = "",
        npc_name: str = "",
    ) -> bool:
        """Require the declared action label to appear in the player's own words."""
        action = next(
            (
                item for item in available_actions
                if item.get("kind") == "interact" and item.get("id") == action_id
            ),
            None,
        )
        if action is None:
            return False
        return requests_action(player_input, npc_id, npc_name, str(action["label"]), action.get("request_phrases", []))

    @staticmethod
    def _validate_response(response: NPCResponse) -> None:
        if not response.dialogue.strip() or len(response.dialogue) > 2_000:
            raise ValueError("NPC dialogue must contain 1 to 2,000 characters")
        for proposal in response.belief_updates:
            if not proposal.subject_id.strip() or not proposal.predicate.strip():
                raise ValueError("Belief proposals require a subject and predicate")
            if not 0 <= proposal.confidence <= 1:
                raise ValueError("Belief confidence must be between zero and one")
        for proposal in response.relationship_updates:
            if (
                not isinstance(proposal.target_id, str)
                or not proposal.target_id.strip()
                or not isinstance(proposal.reason, str)
                or not proposal.reason.strip()
            ):
                raise ValueError("Relationship proposals require a target and reason")
            if (
                not isinstance(proposal.trust_delta, (int, float))
                or isinstance(proposal.trust_delta, bool)
                or not isinstance(proposal.affinity_delta, (int, float))
                or isinstance(proposal.affinity_delta, bool)
            ):
                raise ValueError("Relationship changes must be numeric")
            if not -0.25 <= proposal.trust_delta <= 0.25:
                raise ValueError("Trust changes are limited to 0.25 per exchange")
            if not -0.25 <= proposal.affinity_delta <= 0.25:
                raise ValueError("Affinity changes are limited to 0.25 per exchange")
        for proposal in response.disclosure_requests:
            if (
                not isinstance(proposal.disclosure_id, str)
                or not proposal.disclosure_id.strip()
            ):
                raise ValueError("Disclosure proposals require an id")
