from badbadger.agents.npc import ActionProposal
from badbadger.db.repository import GameRepository

def resolve_npc_proposal(repo: GameRepository, npc_id: str, proposal: ActionProposal) -> tuple[bool,str]:
    if proposal.kind != "move": return False,"Unsupported NPC action."
    npc=repo.get_character(npc_id); destination=proposal.parameters.get("target_id")
    if not npc or not isinstance(destination,str) or not repo.get_location(destination): return False,"Invalid destination."
    if repo.pending_activity(npc_id): return False,"NPC is already occupied."
    duration=repo.connection_duration(npc["location_id"],destination)
    if duration is None: return False,"No valid route."
    repo.create_travel(npc_id,destination,duration)
    player=repo.get_player()
    return True, f"{npc['name']} departs for {repo.get_location(destination)['name']}." if player['location_id']==npc['location_id'] else ""
