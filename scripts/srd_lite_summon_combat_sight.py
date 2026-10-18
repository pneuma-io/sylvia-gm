"""Use saved point illumination for Summon Dragon's combat placement sight."""

from copy import deepcopy

from .srd_lite_attacks import _copy_optional_fact
from .srd_timed_actor_defenses import _call

OWNER = "create_srd_source_actor_scene"
POINT_SIGHT = "resolve_srd_illumination_point_sight"
AMBIENT = "_summon_birth_ambient"
CONDITION = "combat.encounter_active == true and spell == 'summon_dragon'"


def bind_summon_combat_sight(runtime):
    """Keep other summons and exploration unchanged; retain ambient light on the born body."""
    tools = runtime["tools"]
    if POINT_SIGHT not in tools:
        raise ValueError("Summon placement requires the existing point-sight owner")
    owner = tools[OWNER]
    effects = owner["effects"]
    if any(row.get("condition") == CONDITION for row in effects):
        raise ValueError("Summon combat point sight was already bound")
    sites = [index for index, row in enumerate(effects) if row.get("call") == "resolve_srd_sight"]
    bodies = [
        row["set"]["value"]
        for row in effects
        if row.get("set", {}).get("field") == "_created_actor_scene"
    ]
    if len(sites) != 1 or len(bodies) != 1:
        raise ValueError("Preserve the actual created-actor sight and scene admission owners")
    index = sites[0]
    original = deepcopy(effects[index])
    if original["params"].get("lighting") != "{created_actor_lighting}" or (
        bodies[0]["metadata"].get("lighting") != "created_actor_lighting"
    ):
        raise ValueError("The source-owned summon lighting handoff changed")
    actual = _call(
        POINT_SIGHT,
        {
            "observer_state": "{_caster_state_id}",
            "point": "{created_actor_position}",
        },
    )
    actual["export_context"] = {
        "_sight_can_see": "_illumination_point_can_see",
        AMBIENT: "_illumination_point_ambient",
    }
    effects[index] = {"condition": CONDITION, "on_true": [actual], "on_false": [original]}
    effects[:0] = [_copy_optional_fact(AMBIENT, "created_actor_lighting")]
    # Effective magical light stays with its source. Persisting it as ambient would
    # leave the new body falsely illuminated after that source expires or is removed.
    bodies[0]["metadata"]["lighting"] = AMBIENT
    return [OWNER]
