"""Bounded processing of persistent NPC decision events."""

from __future__ import annotations

from badbadger.agents.context import NPCContextBuilder
from badbadger.agents.npc import NPCBackend
from badbadger.db.repository import GameRepository
from badbadger.engine.npc_actions import resolve_npc_proposal
from badbadger.engine.knowledge import guard_response


class AutonomyScheduler:
    def __init__(self, repository: GameRepository, backend: NPCBackend, cooldown: int = 10) -> None:
        self.repository = repository
        self.backend = backend
        self.cooldown = cooldown
        self.context_builder = NPCContextBuilder(repository)

    def process_due(self, call_budget: int = 1) -> list[str]:
        if self.repository.simulation_status != "active":
            return []
        messages: list[str] = []
        calls = 0
        # Fetch more than the call budget because occupied NPCs consume no call.
        for event in self.repository.due_decision_events(max(8, call_budget)):
            npc_id = event["payload"]["npc_id"]
            reaction_only = bool(event["payload"].get("reaction_only", False))
            npc = self.repository.get_character(npc_id)
            activity = self.repository.pending_activity(npc_id)
            parameters = self.repository.npc_parameters(npc_id)
            enabled = parameters.get("autonomy_enabled", True)
            cooldown = parameters.get("decision_cooldown_minutes", self.cooldown)
            if not isinstance(enabled, bool):
                enabled = True
            if not isinstance(cooldown, int) or cooldown < 1:
                cooldown = self.cooldown
            if not enabled:
                with self.repository.transaction():
                    self.repository.mark_event_processed(event["id"])
                continue
            if not npc or not npc["active"] or activity:
                with self.repository.transaction():
                    self.repository.mark_event_processed(event["id"])
                    if not reaction_only:
                        next_time = (
                            activity["due_time"] if activity
                            else self.repository.current_time + cooldown
                        )
                        self.repository.ensure_decision_event(npc_id, next_time)
                continue
            if calls >= call_budget:
                break
            calls += 1
            context = self.context_builder.build(npc_id, "autonomous decision", interaction_mode="decision")
            request = (
                "ENGINE DECISION TICK: Based only on your goals and knowledge, "
                "propose at most one useful action from available_actions. Copy its "
                "kind and all applicable id fields exactly; otherwise propose no action. "
                "If a speak action is available, prioritize one speak action and use "
                "dialogue as the exact short utterance the player will hear."
            )
            response = self.backend.respond(context, request)
            response, protected_output = guard_response(self.repository, response)
            with self.repository.transaction():
                results = []
                if not response.degraded:
                    for proposal in response.proposed_actions[:1]:
                        if reaction_only and proposal.kind != "speak":
                            results.append({
                                "accepted": False,
                                "proposal": proposal.__dict__,
                                "reason": "Reaction events allow only speech.",
                            })
                            continue
                        accepted, message = resolve_npc_proposal(
                            self.repository, npc_id, proposal,
                            allow_social=not reaction_only,
                            allow_proactive=True,
                            autonomous=True,
                            spoken_text=response.dialogue,
                        )
                        results.append({"accepted": accepted, "proposal": proposal.__dict__})
                        if message:
                            messages.append(message)
                self.repository.mark_event_processed(event["id"])
                if not reaction_only:
                    self.repository.ensure_decision_event(
                        npc_id, self.repository.current_time + cooldown
                    )
                self.repository.record(
                    "npc_decision",
                    actor_id=npc_id,
                    input_data={"event_id": event["id"]},
                    result_data={
                        "degraded": response.degraded,
                        "protected_output_blocked": protected_output,
                        "proposals": results,
                        "decision_context": [
                            {
                                "action_id": action.get("id"),
                                "score": action.get("decision_score"),
                                "eligible": action.get("autonomous_eligible"),
                                "reasons": action.get("decision_reasons", []),
                            }
                            for action in context.available_actions
                            if action["kind"] == "interact"
                        ],
                        "reaction_context": [
                            {
                                "cue_id": action["cue_id"],
                                "trigger_kind": action["trigger_kind"],
                                "salience": action["salience"],
                            }
                            for action in context.available_actions
                            if action["kind"] == "speak"
                        ],
                        "biography_context": [
                            {
                                "id": item["id"],
                                "score": item["score"],
                                "selection_reasons": item["selection_reasons"],
                            }
                            for item in context.biography
                        ],
                    },
                )
            if self.repository.simulation_status != "active":
                break
        return messages
