"""Structured action types accepted by the deterministic engine."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MoveAction:
    destination_id: str


@dataclass(frozen=True)
class ExamineAction:
    subject_id: str


@dataclass(frozen=True)
class WaitAction:
    minutes: int
