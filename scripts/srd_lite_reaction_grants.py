"""Initialize the existing native Opportunity Attack capability on ordinary creatures."""

from copy import deepcopy


def bind_reaction_grants(runtime, record):
    """Keep the ordinary creature producer consistent with unchanged Reaction consumers."""
    prepare = deepcopy(runtime["tools"]["prepare_srd_creature"])
    created = [
        effect["create_entity"]["value"]
        for effect in prepare["effects"]
        if "create_entity" in effect
    ]
    assert len(created) == 1
    created[0].setdefault("available_reactions", ["'opportunity_attack'"])
    runtime["tools"]["prepare_srd_creature"] = prepare
    record["version"] = "0.3.7"
    return ["prepare_srd_creature"]


REACTION_DECISIONS = (
    "prepare_srd_coordinate_opportunity",
    "resolve_opportunity_attack",
    "resolve_unarmed_opportunity_attack",
    "complete_srd_coordinate_movement",
)


def bind_reaction_decisions(runtime, record):
    """Keep judgment-requiring saved continuations required, without automatic dispatch."""
    for name in REACTION_DECISIONS:
        tool = deepcopy(runtime["tools"][name])
        assert tool["metadata"]["model_behavior"] in {
            "required_engine_transition",
            "required_ruleset_decision",
        }
        tool["metadata"]["model_behavior"] = "required_ruleset_decision"
        runtime["tools"][name] = tool
    record["version"] = "0.3.8"
    return list(REACTION_DECISIONS)
