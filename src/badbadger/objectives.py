"""Player-safe stages derived from witnessed, engine-approved commitments.

Scenario authors define stages and explicit delegation phrases. Neither the
browser nor a language model may create actions or mark a stage complete.
"""

import json


def objective_stages(repository):
    definitions = repository.scenario_presentation().get("objective_stages", [])
    witnessed = {item["summary"] for item in repository.player_consequences()
                 if item["memory_kind"] == "visible_consequence"}
    actions = {row["id"]: dict(row) for row in repository.connection.execute(
        "SELECT * FROM npc_actions")}
    completed = {key for key, row in actions.items() if row["status"] == "used"
                 and json.loads(row["effects_json"]).get("visible_message") in witnessed}
    available = {action["id"] for npc in repository.characters_at(
        repository.get_player()["location_id"], kind="npc")
        if repository.pending_activity(npc["id"]) is None
        for action in repository.available_npc_actions(npc["id"])}
    statuses = {}
    result = []
    for stage in definitions:
        activation = stage.get("activate_after", [])
        if activation and not completed.intersection(activation):
            continue
        if completed.intersection(stage["action_ids"]):
            status = "completed"
        elif repository.simulation_status != "active":
            status = "not completed"
        elif stage.get("depends_on") and statuses.get(stage["depends_on"]) != "completed":
            status = "waiting"
        elif available.intersection(stage["action_ids"]):
            status = "available"
        else:
            status = "waiting"
        statuses[stage["id"]] = status
        result.append({"label": stage["label"], "status": status,
                       "optional": stage.get("optional", False)})
    return result
