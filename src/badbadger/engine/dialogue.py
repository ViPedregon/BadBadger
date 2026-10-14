"""Validate and apply NPC dialogue results without granting backend authority."""

from __future__ import annotations

import re

from badbadger.agents.context import NPCContextBuilder
from badbadger.agents.npc import NPCBackend, NPCResponse
from badbadger.db.repository import GameRepository
from badbadger.engine.events import process_due_events
from badbadger.engine.npc_actions import resolve_npc_proposal


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

        context = self.context_builder.build(npc_id)
        response = self.backend.respond(context, player_input)
        self._validate_response(response)

        with self.repository.transaction():
            self.repository.append_dialogue(npc_id, player["id"], player_input)
            self.repository.append_dialogue(npc_id, npc_id, response.dialogue)
            if not response.degraded:
                event_messages=[]
                proposal_results=[]
                for proposal in response.proposed_actions:
                    if proposal.kind == "interact" and not self._interaction_requested(
                        context.available_actions, proposal.parameters.get("action_id"),
                        player_input,
                    ):
                        accepted = False
                        message = (
                            "(No consequential action was taken; explicitly request "
                            "the interaction if you want it performed.)"
                        )
                    else:
                        accepted,message=resolve_npc_proposal(
                            self.repository,npc_id,proposal
                        )
                    proposal_results.append({"accepted":accepted,"proposal":proposal.__dict__})
                    if message:
                        event_messages.append(message)
                for proposal in response.belief_updates:
                    self.repository.set_belief(
                        npc_id,
                        proposal.subject_id,
                        proposal.predicate,
                        proposal.value,
                        proposal.confidence,
                        source_type="hearsay",
                        source_character_id=player["id"],
                        detail=player_input,
                    )
                relationship_results = []
                visible_ids = {
                    character["id"] for character in context.visible_characters
                }
                for proposal in response.relationship_updates:
                    if proposal.target_id not in visible_ids:
                        relationship_results.append({
                            "accepted": False,
                            "proposal": proposal.__dict__,
                            "reason": "Target is not visible to the NPC.",
                        })
                        continue
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
                self.repository.advance_time(1)
                event_messages.extend(process_due_events(self.repository))
            else:
                event_messages = [
                    "(AI backend unavailable; the offline reply did not advance time.)"
                ]
            self.repository.record(
                "dialogue_resolved",
                actor_id=player["id"],
                input_data={"npc_id": npc_id, "player_input": player_input},
                result_data={
                    "dialogue": response.dialogue,
                    "degraded": response.degraded,
                    "belief_updates": [item.__dict__ for item in response.belief_updates],
                    "relationship_updates": (
                        relationship_results if not response.degraded else []
                    ),
                    "action_proposals": proposal_results if not response.degraded else [],
                },
            )
        return [f"{npc['name']}: \"{response.dialogue}\"", *event_messages]

    @staticmethod
    def _interaction_requested(
        available_actions: list[dict[str, object]],
        action_id: object,
        player_input: str,
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
        words = set(re.findall(r"[a-z0-9]+", player_input.lower()))
        label_words = set(re.findall(r"[a-z0-9]+", str(action["label"]).lower()))
        return bool(label_words) and label_words.issubset(words)

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
