"""Faerie Fire marks the actual initial sphere's creatures, then travels with them."""

from .srd_lite_profiles import lists
from .srd_control_behaviors import _each
from .srd_timed_actor_defenses import _call, _require, _set, _when


def bind_faerie_zone(runtime):
    """Keep the existing save, attached-light, attack and concentration owners."""
    runtime["definitions"]["spell_casting"]["faerie_fire"].update(
        components=["verbal"], material="", material_parts=[], material_has_cost=False,
        material_consumed=False, focus_substitution=False,
        duration="Concentration, up to 10 turns", range="60 feet")
    tool = runtime["tools"]["cast_srd_faerie_fire_cube"]
    tool["description"] = "Mark admitted creatures in the initial twenty-foot-radius sphere after actual Dexterity saves."
    # The shared illumination request defaults to white for ordinary Light.
    # Use the existing admitted violet presentation for an omitted Faerie choice.
    parent = runtime['tools']['cast_spell_illumination_spell']
    calls = 0
    for effects in list(lists(parent['effects'])):
        for index in range(len(effects)-1,-1,-1):
            effect=effects[index]
            if effect.get('call') != 'cast_srd_faerie_fire_cube':
                continue
            effect['params']['color']='{_lite_faerie_color}'
            effects[index:index]=[_set('_lite_faerie_color','illumination_color'),
                _when("illumination_color == 'white'",[_set('_lite_faerie_color',"'violet'")])]
            calls+=1
    assert calls == 1, 'Expected the actual Faerie Fire argument owner'
    # The source registry prunes elapsed attached marks, but its original refresh
    # leaves the caster's concentration header behind. Use that existing exact
    # caster/instance cleanup owner after pruning, while the old records remain
    # available; an elapsed older instance cannot clear its replacement.
    cleanup = 0
    refresh = runtime['tools']['refresh_srd_illumination_spells']
    for effects in list(lists(refresh['effects'])):
        for index in range(len(effects)-1,-1,-1):
            if effects[index].get('set',{}).get('field') != 'lasting_state.turn_effects':
                continue
            effects[index+1:index+1] = [_each('_light_refresh_records', '_lite_faerie_elapsed', [
                _when("_lite_faerie_elapsed.kind == 'illumination_spell' and "
                      "_lite_faerie_elapsed.spell == 'faerie_fire' and "
                      "_lite_faerie_elapsed.expires_tick != null and "
                      "lasting_state.lasting_tick >= _lite_faerie_elapsed.expires_tick", [
                    _call('finish_srd_illumination_concentration', {
                        'caster_state':'{_lite_faerie_elapsed.caster}',
                        'instance':'{_lite_faerie_elapsed.instance}'})])])]
            cleanup += 1
    assert cleanup == 1, 'Expected one illumination expiry pruning owner'
    shapes = 0
    for effects in lists(tool["effects"]):
        for effect in list(effects):
            query = effect.get("spatial_query", {}).get("query", {})
            if query.get("shape", {}).get("kind") == "box":
                # Area queries admit Boxes/Cylinders. A coarse Box is followed by
                # exact nearest-body-point Sphere membership before any effect.
                query["shape"] = {"kind":"box",
                    "min":"{_faerie_sphere_min}", "max":"{_faerie_sphere_max}"}
                shapes += 1
                position = effects.index(effect)
                effects[position:position] = [
                    _set("_faerie_sphere_min", [f"max(origin[{i}] - 20, lasting_state.spatial_scene.map.bounds.min[{i}])" for i in range(3)]),
                    _set("_faerie_sphere_max", [f"min(origin[{i}] + 20, lasting_state.spatial_scene.map.bounds.max[{i}])" for i in range(3)]),
                    _call("srd_information_barriers", {
                        "start":"{_info_position}", "end":"{origin}", "rule":"solid"},
                        ("_information_blocked",)),
                    _require("_information_blocked != true",
                             "A solid barrier blocks the actual Faerie Fire sphere center."),
                ]
            req = effect.get("require", {})
            if "cube" in req.get("error", "").lower():
                req["error"] = req["error"].replace("cube", "sphere")
            if effect.get("condition") == "_faerie_entry.metadata.plane == plane":
                position = effects.index(effect)
                z = "min(max(origin[2], _faerie_entry.position[2]), _faerie_entry.position[2] + _faerie_entry.height)"
                effects[position:position] = [
                    _require("_faerie_entry.footprint.kind == 'box' or _faerie_entry.footprint.kind == 'circle'",
                             "Faerie Fire requires admitted Box or Circle creature/object space."),
                    _when("_faerie_entry.footprint.kind == 'box'", [
                        _set("_faerie_nearest", [
                            f"min(max(origin[{i}], _faerie_entry.position[{i}] - _faerie_entry.footprint.half_extents[{i}]), _faerie_entry.position[{i}] + _faerie_entry.footprint.half_extents[{i}])"
                            for i in range(2)] + [z])]),
                    _when("_faerie_entry.footprint.kind == 'circle'", [
                        _set("_faerie_horizontal", "((origin[0] - _faerie_entry.position[0]) ** 2 + (origin[1] - _faerie_entry.position[1]) ** 2) ** 0.5"),
                        _set("_faerie_nearest", [
                            f"_faerie_entry.position[{i}] + (origin[{i}] - _faerie_entry.position[{i}]) * min(1, _faerie_entry.footprint.radius / max(0.000000001, _faerie_horizontal))"
                            for i in range(2)] + [z])]),
                    _call("measure_srd_information_distance", {
                        "start":"{origin}", "end":"{_faerie_nearest}"}, ("_scene_distance",)),
                ]
                effect["condition"] += " and _scene_distance <= 20 + 0.000000001"
    assert shapes == 1, "Expected one actual Faerie Fire subject selector"
    tool["effects"].insert(3, _require(" and ".join(
        f"origin[{i}] >= lasting_state.spatial_scene.map.bounds.min[{i}] and "
        f"origin[{i}] <= lasting_state.spatial_scene.map.bounds.max[{i}]"
        for i in range(3)), "The Faerie Fire sphere center must be inside the admitted map."))
