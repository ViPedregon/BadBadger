"""Build character-scoped views without exposing objective mission facts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from badbadger.db.repository import GameRepository


@dataclass(frozen=True)
class NPCContext:
    npc_id: str
    name: str
    appearance: dict[str, Any]
    canon: dict[str, Any]
    biography: list[dict[str, Any]]
    location: dict[str, Any]
    visible_characters: list[dict[str, Any]]
    beliefs: list[dict[str, Any]]
    recent_dialogue: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    available_disclosures: list[dict[str, Any]]
    goals: list[dict[str, Any]]
    parameters: dict[str, Any]
    available_actions: list[dict[str, Any]]
    interaction_mode: str = "conversation"


class NPCContextBuilder:
    """Create the complete and intentionally narrow input for an NPC backend."""

    def __init__(self, repository: GameRepository) -> None:
        self.repository = repository

    def build(self, npc_id: str, relevance_text: str = "", *, interaction_mode: str = "conversation") -> NPCContext:
        npc = self.repository.get_character(npc_id)
        if npc is None or npc["kind"] != "npc":
            raise ValueError(f"Unknown NPC: {npc_id}")
        location = self.repository.get_location(npc["location_id"])
        if location is None:
            raise RuntimeError(f"NPC {npc_id} has an unknown location")
        visible = []
        for character in self.repository.characters_at(npc["location_id"]):
            if character["id"] != npc_id:
                visible.append({
                    "id": character["id"],
                    "name": character["name"],
                    "kind": character["kind"],
                    "appearance": self.repository.appearance_for(character["id"], "character"),
                })
        goals = self.repository.goals_for(npc_id, include_resolved=True)
        recent_dialogue = self.repository.recent_dialogue(npc_id)
        canon = self.repository.character_canon(npc_id)
        biography = self._select_biography(
            canon,
            relevance_text=" ".join([
                relevance_text,
                location["name"],
                location["description"],
                *(character["name"] for character in visible),
                *(goal["description"] for goal in goals if goal["status"] == "active"),
                *(item["text"] for item in recent_dialogue[-2:]),
            ]),
            visible_ids={character["id"] for character in visible},
        )
        identity = ({
            "role": canon["role"],
            "voice": canon["voice"],
            "values": canon["values"],
            "boundaries": canon["boundaries"],
        } if canon else {})
        authored_profile = self.repository.character_identity(npc_id)
        if authored_profile is not None:
            identity["personality"] = authored_profile.get("personality", {})
            identity["motivations"] = authored_profile.get("motivations", [])
        context = NPCContext(
            npc_id=npc_id,
            name=npc["name"],
            appearance=self.repository.appearance_for(npc_id, "character"),
            canon=identity,
            biography=biography,
            location={
                "id": location["id"],
                "name": location["name"],
                "description": location["description"],
                "appearance": self.repository.appearance_for(location["id"], "location"),
            },
            visible_characters=visible,
            beliefs=[belief for belief in self.repository.beliefs_for(npc_id)
                     if not self.repository.protected_belief(belief["subject_id"], belief["predicate"])],
            recent_dialogue=recent_dialogue,
            memories=self.repository.memories_for(npc_id, relevance_text=relevance_text),
            relationships=self.repository.relationships_for(npc_id),
            available_disclosures=self.repository.disclosure_affordances(
                npc_id, self.repository.get_player()["id"]
            ),
            goals=goals,
            parameters=self.repository.npc_parameters(npc_id),
            available_actions=[
                {
                    "kind": "move",
                    "target_id": destination["id"],
                    "target_name": destination["name"],
                    "duration_minutes": destination["duration_minutes"],
                }
                for destination in self.repository.reachable_locations(location["id"])
            ] + self.repository.available_npc_actions(
                npc_id
            ) + self.repository.available_speech_actions(
                npc_id
            ) + self.repository.available_social_actions(npc_id),
            interaction_mode=interaction_mode,
        )
        # Historical text is untrusted too: a player may have typed a correct
        # secret guess, or a legacy model reply may have echoed private content.
        protected = self.repository.protected_texts()
        def protected_text(text):
            return self.repository.contains_protected_text(text, protected=protected)
        context.recent_dialogue[:] = [item for item in context.recent_dialogue
                                     if not protected_text(item["text"])]
        context.memories[:] = [item for item in context.memories
                              if not protected_text(item["summary"])]
        context.biography[:] = [item for item in context.biography
                               if not protected_text(item["guidance"])]
        for belief in context.beliefs:
            evidence = belief.get("evidence", [])
            strongest = max(evidence, key=lambda item: item["confidence"], default=None)
            selected = {item["id"]: item for item in evidence[-4:]}
            if strongest is not None:
                selected[strongest["id"]] = strongest
            belief["evidence"] = sorted(selected.values(), key=lambda item: item["id"])
            for item in belief["evidence"]:
                if item.get("detail"):
                    item["detail"] = item["detail"][:400]
        # Bound prompt size without deleting stored memories or dialogue.
        for items, key, budget in ((context.recent_dialogue, "text", 4000), (context.memories, "summary", 4000)):
            for item in reversed(items) if key == "text" else items:
                item[key] = item[key][:max(0, budget)]
                budget -= len(item[key])
        def project(value):
            if isinstance(value, str):
                return "[withheld]" if protected_text(value) else value
            if isinstance(value, dict):
                return {key: project(item) for key, item in value.items()}
            if isinstance(value, list):
                return [project(item) for item in value]
            return value
        return NPCContext(**project(asdict(context)))

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            token for token in re.findall(r"[a-z0-9]+", value.lower())
            if len(token) >= 3
        }

    @classmethod
    def _select_biography(
        cls,
        canon: dict[str, Any] | None,
        *,
        relevance_text: str,
        visible_ids: set[str],
        limit: int = 4,
        character_budget: int = 1_600,
    ) -> list[dict[str, Any]]:
        """Choose safe biography projections using deterministic relevance."""
        if canon is None:
            return []
        relevance_tokens = cls._tokens(relevance_text)
        candidates: list[tuple[float, float, str, dict[str, Any]]] = []
        for fragment in canon["biography"]:
            if fragment["awareness"] != "conscious":
                continue
            guidance = (
                fragment["summary"] if fragment["visibility"] == "public"
                else fragment.get("behavioral_guidance")
            )
            if not guidance:
                continue
            score = float(fragment["importance"]) * 0.2
            reasons: list[str] = []
            if float(fragment["importance"]) >= 0.8:
                score += 0.25
                reasons.append("enduring high importance")
            topic_matches = sorted(
                topic for topic in fragment["topics"]
                if cls._tokens(topic) & relevance_tokens
            )
            if topic_matches:
                score += min(0.6, 0.3 * len(topic_matches))
                reasons.append("topic match: " + ", ".join(topic_matches))
            related_matches = sorted(
                set(fragment["related_character_ids"]) & visible_ids
            )
            if related_matches:
                score += 0.35
                reasons.append("related character present: " + ", ".join(related_matches))
            summary_overlap = cls._tokens(fragment["summary"]) & relevance_tokens
            if summary_overlap:
                score += min(0.3, 0.05 * len(summary_overlap))
                reasons.append("current-language overlap")
            if score < 0.3:
                continue
            projection = {
                "id": fragment["id"],
                "guidance": guidance,
                "visibility": fragment["visibility"],
                "certainty": fragment["certainty"],
                "score": round(score, 3),
                "selection_reasons": reasons,
            }
            candidates.append((
                score, float(fragment["importance"]), fragment["id"], projection
            ))
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        selected: list[dict[str, Any]] = []
        used = 0
        for _, _, _, projection in candidates:
            size = len(projection["guidance"])
            if len(selected) >= limit or used + size > character_budget:
                continue
            selected.append(projection)
            used += size
        return selected
