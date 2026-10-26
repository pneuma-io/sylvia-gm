"""Guarded Web follows the actual final environment route without changing movement bodies."""

from copy import deepcopy
from pathlib import Path

from scripts.srd_full.srd_guards_wards_web import _bind_ground_routes
from scripts.srd_full.srd_spell_barriers import _effect_lists
from scripts.srd_full.srd_spell_environment import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime


def test_current_environment_route_keeps_guarded_web_before_its_real_final_endpoint():
    source = Path("rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    tools = _load_runtime(extend_runtime(source))["tools"]
    before = deepcopy(tools)
    changed = _bind_ground_routes(tools)
    assert tools == before
    assert _bind_ground_routes(tools | changed) == changed
    assert set(changed) == {"move_srd_scene_creature_node"}
    node = changed["move_srd_scene_creature_node"]
    original = before["move_srd_scene_creature_node"]
    assert node["parameters"] == original["parameters"]
    assert node["pre_conditions"] == original["pre_conditions"]
    routes = [
        rows
        for rows in _effect_lists(node["effects"])
        if any(e.get("list_push", {}).get("value") == "'final_endpoint'" for e in rows)
    ]
    assert len(routes) == 1
    route = routes[0]
    endpoint = next(
        i for i, e in enumerate(route) if e.get("list_push", {}).get("value") == "'final_endpoint'"
    )
    assert "_ward_web_movement_ids" in str(route[endpoint - 1])
    del route[endpoint - 1]
    for rows in _effect_lists(node["effects"]):
        for effect in rows:
            if effect.get("on_true") is route:
                effect["condition"] = effect["condition"].removesuffix(
                    " or lasting_state.has_guarded_web_sections == true"
                )
    assert node == original
    assert "has_guarded_web_sections" not in str(
        tools["move_srd_scene_creature_environment_segment"]
    )
