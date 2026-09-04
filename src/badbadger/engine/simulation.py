"""Authoritative deterministic action resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

from badbadger.db.repository import GameRepository
from badbadger.engine.actions import ExamineAction, MoveAction, WaitAction
from badbadger.engine.events import process_due_events


@dataclass(frozen=True)
class ActionOutcome:
    accepted: bool
    messages: list[str] = field(default_factory=list)
    elapsed_minutes: int = 0


class SimulationEngine:
    """Validate actions, mutate state, advance time, and resolve due events."""

    def __init__(self, repository: GameRepository) -> None:
        self.repository = repository

    def perform(
        self, action: MoveAction | ExamineAction | WaitAction
    ) -> ActionOutcome:
        with self.repository.transaction():
            if isinstance(action, MoveAction):
                outcome = self._move(action)
            elif isinstance(action, ExamineAction):
                outcome = self._examine(action)
            elif isinstance(action, WaitAction):
                outcome = self._wait(action)
            else:
                raise TypeError(f"Unsupported action: {type(action).__name__}")

            event_messages: list[str] = []
            if outcome.accepted and outcome.elapsed_minutes:
                self.repository.advance_time(outcome.elapsed_minutes)
                event_messages = process_due_events(self.repository)

            final = ActionOutcome(
                accepted=outcome.accepted,
                messages=[*outcome.messages, *event_messages],
                elapsed_minutes=outcome.elapsed_minutes,
            )
            self.repository.record(
                "action_resolved",
                actor_id=self.repository.get_player()["id"],
                input_data={"action": type(action).__name__, **action.__dict__},
                result_data={
                    "accepted": final.accepted,
                    "messages": final.messages,
                    "elapsed_minutes": final.elapsed_minutes,
                },
            )
            return final

    def _move(self, action: MoveAction) -> ActionOutcome:
        destination = self.repository.get_location(action.destination_id)
        if destination is None:
            return ActionOutcome(False, ["That destination does not exist."])
        player = self.repository.get_player()
        if player["location_id"] == action.destination_id:
            return ActionOutcome(False, ["You are already there."])
        duration=self.repository.connection_duration(player["location_id"], action.destination_id)
        if duration is None:
            return ActionOutcome(False,["There is no route to that destination."])
        self.repository.move_character(player["id"], action.destination_id)
        return ActionOutcome(
            True,
            [f"You travel to {destination['name']}."],
            elapsed_minutes=duration,
        )

    def _examine(self, action: ExamineAction) -> ActionOutcome:
        description = self.repository.get_fact(action.subject_id, "description")
        if description is None:
            return ActionOutcome(False, ["You find nothing meaningful to examine."])
        return ActionOutcome(True, [str(description)], elapsed_minutes=2)

    @staticmethod
    def _wait(action: WaitAction) -> ActionOutcome:
        if action.minutes <= 0:
            return ActionOutcome(False, ["Waiting must consume positive time."])
        return ActionOutcome(
            True,
            [f"You wait for {action.minutes} minutes."],
            elapsed_minutes=action.minutes,
        )
