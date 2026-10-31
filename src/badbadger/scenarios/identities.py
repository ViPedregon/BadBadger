"""Load reusable authored identities without mixing them with mission state."""

from copy import deepcopy
import json
import math
from pathlib import Path
import re


class IdentityError(ValueError):
    pass


MISSION_PARAMETERS = {"autonomy_enabled", "first_decision_after_minutes",
                      "decision_cooldown_minutes", "proactive_speech_cooldown_minutes", "follow_player"}


def load_identity(path: Path, root: Path, *, allow_unapproved: bool = False) -> dict:
    root = root.resolve()
    path = Path(path)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise IdentityError("Identity path must be relative and remain inside the scenario directory")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise IdentityError("Identity path must remain inside the scenario directory")
    try:
        profile = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise IdentityError("Cannot read character identity") from error
    if not isinstance(profile, dict):
        raise IdentityError("Identity must be an object")
    allowed = {"schema_version", "identity_id", "revision", "status", "name", "kind",
               "canon", "personality", "motivations", "appearance", "author_notes"}
    if set(profile) - allowed:
        raise IdentityError("Unknown identity fields; location, beliefs, goals and relationships belong in the scenario")
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise IdentityError("Unsupported identity schema_version")
    if type(profile.get("revision")) is not int or profile["revision"] < 1:
        raise IdentityError("Identity revision must be a positive integer")
    if not isinstance(profile.get("identity_id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]*", profile["identity_id"]):
        raise IdentityError("Identity id must be a stable lowercase identifier")
    if not isinstance(profile.get("name"), str) or not profile["name"].strip():
        raise IdentityError("Identity name must be non-empty text")
    if profile.get("kind") not in {"player", "npc"}:
        raise IdentityError("Identity kind must be player or npc")
    if profile.get("status") not in {"awaiting_lore", "draft", "approved"}:
        raise IdentityError("Identity status must be awaiting_lore, draft or approved")
    if profile["status"] != "approved" and not allow_unapproved:
        raise IdentityError("Identity is not approved: supply authored lore and review it before using it in a scenario")
    if not isinstance(profile.get("canon"), dict):
        raise IdentityError("Identity canon must be an object")
    if not isinstance(profile.get("author_notes", ""), str):
        raise IdentityError("Identity author_notes must be text")
    motivations = profile.get("motivations", [])
    if not isinstance(motivations, list) or not all(isinstance(item, str) and item.strip() for item in motivations):
        raise IdentityError("Identity motivations must be non-empty text entries")
    if len(set(motivations)) != len(motivations):
        raise IdentityError("Duplicate identity motivations")
    personality = profile.get("personality", {})
    if not isinstance(personality, dict) or set(personality) & MISSION_PARAMETERS:
        raise IdentityError("Identity personality cannot contain mission scheduling parameters")
    for key, value in personality.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not isinstance(value, (str, bool, int, float)):
            raise IdentityError("Identity traits must have named scalar values")
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise IdentityError("Identity trait numbers must be finite")
        if isinstance(value, str) and not value.strip():
            raise IdentityError("Identity trait text cannot be blank")
    if "risk_tolerance" in personality and (type(personality["risk_tolerance"]) not in {int, float}
                                            or not 0 <= personality["risk_tolerance"] <= 1):
        raise IdentityError("Identity risk_tolerance must be between 0 and 1")
    if not isinstance(profile.get("appearance", {}), dict):
        raise IdentityError("Identity appearance must be an object")
    biography = profile["canon"].get("biography", [])
    if not isinstance(biography, list):
        raise IdentityError("Identity biography must be a list")
    for fragment in biography:
        if not isinstance(fragment, dict):
            raise IdentityError("Identity biography entries must be objects")
        if "related_character_ids" in fragment:
            raise IdentityError("Use related_identity_ids in reusable identity biographies")
        if fragment.get("action_influences"):
            raise IdentityError("Mission action influences belong in scenario-authored canon or decision policies")
        related = fragment.get("related_identity_ids", [])
        if (not isinstance(related, list) or not all(isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_]*", item) for item in related)
                or len(set(related)) != len(related) or profile["identity_id"] in related):
            raise IdentityError("Invalid related_identity_ids")
    # Reuse strict canon validation after translating stable relationship refs.
    from badbadger.scenarios.loader import ScenarioError, _validate_character_canon
    canon = deepcopy(profile["canon"])
    related_ids = {profile["identity_id"]}
    for fragment in canon.get("biography", []):
        fragment["related_character_ids"] = fragment.pop("related_identity_ids", [])
        related_ids.update(fragment["related_character_ids"])
    try:
        _validate_character_canon({"id": profile["identity_id"], "canon": canon}, related_ids)
    except ScenarioError as error:
        raise IdentityError(str(error)) from error
    return profile


def materialize_identities(data: dict, root: Path, *, allow_unapproved: bool = False) -> None:
    characters = data.get("characters", [])
    if not isinstance(characters, list):
        return
    identity_map = {}
    for character in characters:
        if not isinstance(character, dict):
            continue
        if "_identity_snapshot" in character:
            raise IdentityError("Identity snapshot is derived from identity_file, not scenario input")
        if "identity_file" not in character:
            continue
        reference = character["identity_file"]
        if not isinstance(reference, str) or not reference.strip():
            raise IdentityError("identity_file must be non-empty text")
        if "canon" in character or "canon_file" in character:
            raise IdentityError("An identity_file cannot be combined with canon or canon_file")
        profile = load_identity(Path(reference), root, allow_unapproved=allow_unapproved)
        if profile["identity_id"] in identity_map:
            raise IdentityError("A stable identity can occur only once in a scenario")
        identity_map[profile["identity_id"]] = character.get("id")
        for field in ("name", "kind"):
            if field in character and character[field] != profile[field]:
                raise IdentityError(f"Scenario {field} conflicts with reusable identity")
            character[field] = profile[field]
        parameters = character.get("parameters", {})
        if not isinstance(parameters, dict):
            raise IdentityError("Scenario parameters must be an object")
        if any(key in parameters and parameters[key] != value for key, value in profile.get("personality", {}).items()):
            raise IdentityError("Scenario parameters cannot silently override authored personality")
        character["parameters"] = {**profile.get("personality", {}), **parameters}
        if "appearance" in character and character["appearance"] != profile.get("appearance", {}):
            raise IdentityError("Scenario appearance conflicts with reusable identity")
        character["appearance"] = deepcopy(profile.get("appearance", {}))
        character["canon"] = deepcopy(profile["canon"])
        character["_identity_snapshot"] = profile
    for character in characters:
        if not isinstance(character, dict) or "_identity_snapshot" not in character:
            continue
        for fragment in character["canon"].get("biography", []):
            related = fragment.pop("related_identity_ids", [])
            fragment["related_character_ids"] = [identity_map[identity] for identity in related if identity in identity_map]
