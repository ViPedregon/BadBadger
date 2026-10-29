"""Deterministic consent checks; model proposals never grant permission."""

import re


def request_body(text: str, npc_id: str, npc_name: str) -> str | None:
    text = " ".join(text.lower().strip().split())
    aliases = {npc_id.lower(), npc_name.lower(), *npc_name.lower().split()}
    names = "(?:" + "|".join(re.escape(name) for name in sorted(aliases, key=len, reverse=True)) + ")"
    text = re.sub(r"^(?:tell|ask)\s+(?:the\s+)?" + names + r"\b[,:]?\s*", "", text)
    text = re.sub(r"^" + names + r"\b[,:]?\s*", "", text)
    text = re.sub(r"^(?:please\s+)?(?:to\s+)?", "", text)
    polite = bool(re.match(r"^(?:can|could|would|will) you\s+", text))
    text = re.sub(r"^(?:can|could|would|will) you\s+", "", text)
    text = re.sub(r"^(?:please\s+|go ahead and\s+|i want you to\s+)", "", text)
    if "?" in text and not polite:
        return None
    if re.search(r"\b(?:not|never|don't|if|unless|whether|why|explain|pretend|ignore)\b", text):
        return None
    return re.sub(r"\s+please$", "", text.rstrip(".!? "))


def requests_action(text: str, npc_id: str, npc_name: str, label: str) -> bool:
    return request_body(text, npc_id, npc_name) == " ".join(label.lower().split())


def requests_move(text: str, npc_id: str, npc_name: str, destinations: list[dict], target_id: str) -> bool:
    body = request_body(text, npc_id, npc_name)
    if body is None:
        return False
    for destination in destinations:
        if destination.get("kind") != "move" or destination.get("target_id") != target_id:
            continue
        target = re.escape(destination["target_name"].lower())
        if re.fullmatch(r"(?:go|move|travel)(?: to| check| and check)? (?:the )?" + target, body):
            return True
    return False
