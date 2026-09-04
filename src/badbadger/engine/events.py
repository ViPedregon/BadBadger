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

def _arrival(repository: GameRepository, payload: dict[str, Any]) -> str:
    activity=repository.connection.execute("SELECT * FROM character_activities WHERE id=?",(payload['activity_id'],)).fetchone()
    if not activity or activity['status']!='pending': return ""
    repository.move_character(payload['character_id'],payload['destination_id'])
    repository.connection.execute("UPDATE character_activities SET status='completed' WHERE id=?",(payload['activity_id'],))
    actor=repository.get_character(payload['character_id']); player=repository.get_player()
    return f"{actor['name']} arrives." if player['location_id']==payload['destination_id'] else ""


EVENT_HANDLERS: dict[str, EventHandler] = {"set_fact": _set_fact,"character_arrival":_arrival}


def process_due_events(repository: GameRepository) -> list[str]:
    """Process all currently due events in stable order."""
    messages: list[str] = []
    for event in repository.due_events():
        if event["event_type"] == "npc_decision":
            continue
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
        if message and (event["event_type"]=="character_arrival" or event["payload"].get("player_visible", False)):
            messages.append(message)
    return messages
