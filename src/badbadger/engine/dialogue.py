"""Validate and apply NPC dialogue results without granting backend authority."""

from __future__ import annotations

from badbadger.agents.context import NPCContextBuilder
from badbadger.agents.npc import NPCBackend, NPCResponse
from badbadger.db.repository import GameRepository
from badbadger.engine.events import process_due_events


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
            for proposal in response.belief_updates:
                self.repository.set_belief(
                    npc_id,
                    proposal.subject_id,
                    proposal.predicate,
                    proposal.value,
                    proposal.confidence,
                )
            # A short exchange advances time, after which normal events run.
            self.repository.advance_time(1)
            event_messages = process_due_events(self.repository)
            self.repository.record(
                "dialogue_resolved",
                actor_id=player["id"],
                input_data={"npc_id": npc_id, "player_input": player_input},
                result_data={
                    "dialogue": response.dialogue,
                    "belief_updates": [item.__dict__ for item in response.belief_updates],
                    "rejected_action_proposals": [
                        item.__dict__ for item in response.proposed_actions
                    ],
                },
            )
        return [f"{npc['name']}: \"{response.dialogue}\"", *event_messages]

    @staticmethod
    def _validate_response(response: NPCResponse) -> None:
        if not response.dialogue.strip() or len(response.dialogue) > 2_000:
            raise ValueError("NPC dialogue must contain 1 to 2,000 characters")
        for proposal in response.belief_updates:
            if not proposal.subject_id.strip() or not proposal.predicate.strip():
                raise ValueError("Belief proposals require a subject and predicate")
            if not 0 <= proposal.confidence <= 1:
                raise ValueError("Belief confidence must be between zero and one")
