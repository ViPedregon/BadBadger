"""Deterministic handlers for scheduled world events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from badbadger.db.repository import GameRepository

EventHandler = Callable[[GameRepository, dict[str, Any]], str]


def _set_fact(repository: GameRepository, payload: dict[str, Any]) -> str:
    repository.set_fact(
        payload["subject_id"],
        payload["predicate"],
        payload["value"],
        hidden=bool(payload.get("hidden", False)),
    )
    return payload.get("visible_message", "The world changes.")


EVENT_HANDLERS: dict[str, EventHandler] = {"set_fact": _set_fact}


def process_due_events(repository: GameRepository) -> list[str]:
    """Process all currently due events in stable order."""
    messages: list[str] = []
    for event in repository.due_events():
        handler = EVENT_HANDLERS.get(event["event_type"])
        if handler is None:
            raise ValueError(f"Unknown event type: {event['event_type']}")
        message = handler(repository, event["payload"])
        repository.mark_event_processed(event["id"])
        repository.record(
            "event_processed",
            input_data={"event_id": event["id"], "event_type": event["event_type"]},
            result_data={"message": message},
        )
        if event["payload"].get("player_visible", False):
            messages.append(message)
    return messages
