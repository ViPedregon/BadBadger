"""Checks on untrusted dialogue and player-sourced belief proposals."""

from dataclasses import replace
import re
from badbadger.engine.consent import request_body

from badbadger.agents.npc import NPCResponse


def inspection_request(text: str) -> bool:
    return bool(re.search(
        r"\b(?:show|reveal|dump|print|give|ignore|override)\b.*"
        r"\b(?:system prompt|developer instructions|npc context|engine state|hidden facts|private biography|api key|previous instructions)\b",
        text, re.I,
    ))


def guard_response(repository, response: NPCResponse) -> tuple[NPCResponse, bool]:
    internal_dump = bool(re.search(
        r'''["'](?:npc_id|available_actions|available_disclosures|beliefs|relationships|parameters|biography)["']\s*:''',
        response.dialogue,
    ))
    if not internal_dump and not repository.contains_protected_text(response.dialogue):
        return response, False
    return replace(response, dialogue="I can't confirm that from what I'm able to discuss.",
                   proposed_actions=[], belief_updates=[], relationship_updates=[],
                   disclosure_requests=[], degraded=True), True


def grounded_claim(repository, context, player_input, proposal) -> bool:
    """Lexical grounding is not proof: accepted statements remain hearsay."""
    quote = proposal.source_quote
    if not isinstance(quote, str) or not quote or quote not in player_input or len(quote.strip()) < 4:
        return False
    normalized = quote.lower().strip()
    body = request_body(player_input, context.npc_id, context.name)
    if player_input.lstrip().lower().startswith("ask ") or body is None:
        return False
    if re.match(r"^(?:is|are|was|were|what|why|how|could|would|can|do|does)\b", body):
        return False
    if "?" in player_input or re.search(r"\b(?:whether|suppose|pretend|hypothetically|imagine)\b", player_input, re.I):
        return False
    if re.match(r"^(?:is|are|was|were|what|why|how|could|would|can|do|does)\b", normalized):
        return False
    if repository.protected_belief(proposal.subject_id, proposal.predicate):
        return False
    subjects = {item["subject_id"] for item in context.beliefs}
    subjects.update(item["id"] for item in repository.list_locations())
    subjects.update(item["id"] for item in context.visible_characters)
    if proposal.subject_id not in subjects:
        return False
    tokens = {word for word in re.findall(r"[a-z0-9]+", proposal.subject_id.lower()) if len(word) > 1}
    return bool(tokens & set(re.findall(r"[a-z0-9]+", normalized)))
