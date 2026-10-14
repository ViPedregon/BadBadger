from badbadger.agents.npc import ActionProposal
from badbadger.db.repository import GameRepository

def resolve_npc_proposal(
    repo: GameRepository,
    npc_id: str,
    proposal: ActionProposal,
    *,
    requested_by_player: bool = False,
    allow_social: bool = False,
) -> tuple[bool,str]:
    if proposal.kind == "share_belief":
        if not allow_social:
            return False, "Social actions are only available during autonomous decisions."
        target_id = proposal.parameters.get("target_id")
        subject_id = proposal.parameters.get("subject_id")
        predicate = proposal.parameters.get("predicate")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (target_id, subject_id, predicate)
        ):
            return False, "Invalid social action."
        return repo.share_belief(npc_id, target_id, subject_id, predicate)
    if proposal.kind == "interact":
        action_id = proposal.parameters.get("action_id")
        if not isinstance(action_id, str):
            return False, "Invalid interaction."
        accepted, message = repo.apply_npc_action(
            npc_id, action_id, requested_by_player=requested_by_player
        )
        npc = repo.get_character(npc_id)
        player = repo.get_player()
        if accepted and npc and npc["location_id"] != player["location_id"]:
            message = ""
        return accepted, message
    if proposal.kind != "move": return False,"Unsupported NPC action."
    npc=repo.get_character(npc_id); destination=proposal.parameters.get("target_id")
    if not npc or not isinstance(destination,str) or not repo.get_location(destination): return False,"Invalid destination."
    if repo.pending_activity(npc_id): return False,"NPC is already occupied."
    duration=repo.connection_duration(npc["location_id"],destination)
    if duration is None: return False,"No valid route."
    repo.create_travel(npc_id,destination,duration)
    player=repo.get_player()
    return True, f"{npc['name']} departs for {repo.get_location(destination)['name']}." if player['location_id']==npc['location_id'] else ""
