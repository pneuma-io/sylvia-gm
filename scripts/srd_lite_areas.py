"""Pending Lite area adapters composed from the existing scene and damage owners.

The caller selects an origin or a direction; actual scene membership supplies
creatures and supported objects. This module does not select profiles for release.
"""
from copy import deepcopy

from .srd_control_behaviors import _each, _entity, _param, _push, _when
from .srd_lite_profiles import lists
from .srd_lite_attacks import _effect_names
from .srd_scene_relations import _tool_scene
from .srd_sunburst_geometry import _nearest, _point_cover, _replace_names, _sphere_tool
from .srd_timed_actor_defenses import _call, _require, _set

AREAS = {"burning_hands", "fireball"}
_AREA_CONDITION = "spell == 'burning_hands' or spell == 'fireball'"


def _intent():
    return [
        _param(name, "array", required=False, default=[], items={"type": "number"}, min_items=0, max_items=3)
        for name in ("lite_area_origin", "lite_area_apex", "lite_area_direction")
    ]


def _rename(value):
    if isinstance(value, str):
        return value.replace("sunburst", "lite_area").replace("Sunburst", "Lite area")
    if isinstance(value, list):
        return [_rename(child) for child in value]
    if isinstance(value, dict):
        return {_rename(key): _rename(child) for key, child in value.items()}
    return value


def _sphere():
    tool = _rename(_sphere_tool())
    tool["parameters"] += [_param("range_feet", "integer"), _param("radius_feet", "integer")]
    ranged = sized = 0
    for rows in lists(tool["effects"]):
        for row in rows:
            requirement = row.get("require", {})
            if "<= 150 ** 2" in requirement.get("condition", ""):
                requirement["condition"] = requirement["condition"].replace("<= 150 ** 2", "<= range_feet ** 2")
                requirement["error"] = "The actual Lite area origin is beyond this spell's range."
                ranged += 1
            if "<= 60 ** 2" in row.get("condition", ""):
                row["condition"] = row["condition"].replace("<= 60 ** 2", "<= radius_feet ** 2")
                sized += 1
    assert ranged == sized == 1
    return tool


def _objects():
    """Apply the same chosen volume to actual supported non-creature scene objects."""
    return _tool_scene(
        "Collect actual prepared objects in this Lite area, excluding blocked paths.",
        [_entity("caster_state"), _param("shape"), _param("size_feet", "integer"),
         _param("origin", "array", items={"type": "number"}, min_items=3, max_items=3),
         *_intent()],
        [
            _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, ("_info_scene_entity",)),
            _set("_lite_area_source", "_info_scene_entity"),
            _set("_lite_area_objects", []), _set("_lite_area_source_index", -1), _set("_lite_area_index", 0),
            _set("_lite_area_scene_rows", "state.spatial_scene.entities"),
            _each("_lite_area_scene_rows", "_lite_area_entry", [
                _when("_lite_area_entry.id == _lite_area_source.id", [_set("_lite_area_source_index", "_lite_area_index")]),
                _set("_lite_area_index", "_lite_area_index + 1"),
            ]),
            _set("_lite_area_index", 0),
            _each("_lite_area_scene_rows", "_lite_area_entry", [
                _when("_lite_area_entry.active == true and _lite_area_entry.metadata.participant_ref == null and _lite_area_entry.metadata.is_object == true and _lite_area_entry.metadata.plane == _lite_area_source.metadata.plane and _lite_area_entry.metadata.worn_by == null and _lite_area_entry.metadata.carried_by == null", [
                    _require("_lite_area_entry.footprint.kind == 'box'", "Lite area object qualification needs an admitted box volume."),
                    _set("_lite_area_inside", False),
                    _when("shape == 'sphere'", [
                        _set("_lite_area_near", _nearest("_lite_area_entry", "origin")),
                        _set("_lite_area_inside", "(_lite_area_near[0] - origin[0]) ** 2 + (_lite_area_near[1] - origin[1]) ** 2 + (_lite_area_near[2] - origin[2]) ** 2 <= size_feet ** 2"),
                    ]),
                    _when("shape == 'cone'", [
                        _call("qualify_srd_cone_admitted_box", {"source_index": "{_lite_area_source_index}", "target_index": "{_lite_area_index}", "length_feet": "{size_feet}", "apex": "{lite_area_apex}", "direction": "{lite_area_direction}"}, ("_srd_cone_intersects",)),
                        _set("_lite_area_inside", "_srd_cone_intersects"),
                        _set("_lite_area_near", _nearest("_lite_area_entry", "origin")),
                    ]),
                    _when("_lite_area_inside == true", [
                        _call("srd_information_barriers", {"start": "{origin}", "end": "{_lite_area_near}", "rule": "solid", "plane": "{_lite_area_source.metadata.plane}"}, ("_information_blocked",)),
                        _when("_information_blocked != true", [
                            _call("qualify_srd_area_prepared_object", {"object_id": "{_lite_area_entry.id}"}),
                            _require("_lite_area_entry.metadata.flammable != null", "Record the actual area object's combustibility before casting."),
                            _require("_lite_area_entry.metadata.flammable != true or _lite_area_entry.metadata.srd_ordinary_flame.kind == 'ordinary_flame'", "Record the actual area object's ordinary fuel facts before casting."),
                            _require("_lite_area_entry.metadata.magical != null", "Record the actual area object's magic classification before casting."),
                            _require("len(_lite_area_objects) < 64", "Lite area exceeds the supported object recipient bound."),
                            _push("_lite_area_objects", "_lite_area_entry.id"),
                        ]),
                    ]),
                ]),
                _set("_lite_area_index", "_lite_area_index + 1"),
            ]),
        ],
    )


def bind_areas(runtime):
    """Prepare the two clear fire areas; Shatter stays pending for inorganic facts."""
    spells, headers, tools = runtime["definitions"]["spells"], runtime["definitions"]["spell_casting"], runtime["tools"]
    spells["burning_hands"]["area_size_feet"] = 17
    spells["fireball"].update(range_feet=60, area_size_feet=13)
    for name in AREAS:
        headers[name].update(components=["verbal", "somatic"], material_parts=[], material="", material_consumed=False, material_has_cost=False, focus_substitution=False)
    headers["burning_hands"]["range"] = "Self (17-foot cone)"
    headers["fireball"]["range"] = "60 feet"

    tools["collect_srd_lite_area_sphere_targets"] = _sphere()
    tools.update(_rename(_point_cover(tools)))
    tools["collect_srd_lite_area_objects"] = _objects()
    area = deepcopy(tools["cast_spell_instantaneous_area_save_damage"])
    area["parameters"] += _intent()
    area["pre_conditions"] = [row for row in area["pre_conditions"] if "origin_distance" not in row["condition"] and "half_cover_targets" not in row["condition"]]
    area["pre_conditions"].append({"condition": "targeting_profile == 'area' and area_width_feet == 0 and ((spell == 'burning_hands' and origin_kind == 'self' and range_feet == 0 and area_shape == 'cone' and area_size_feet == 17) or (spell == 'fireball' and origin_kind == 'point' and range_feet == 60 and area_shape == 'sphere' and area_size_feet == 13))", "error": "The Lite fire area must match its actual source profile."})
    area["effects"] = _replace_names(area["effects"], {
        "targets": "_lite_area_actual_targets", "half_cover_targets": "_lite_area_half_cover",
        "three_quarters_cover_targets": "_lite_area_three_quarters", "area_object_targets": "_lite_area_actual_objects",
        "shatter_object_targets": "_lite_area_no_shatter_objects",
    })
    qualify = [
        _when("spell == 'burning_hands'", [
            _call("collect_srd_cone_creature_targets", {"source_state": "{_caster_state_id}", "length_feet": 17, "apex": "{lite_area_apex}", "direction": "{lite_area_direction}"}, ("_srd_cone_targets", "_srd_cone_origin", "_srd_cone_axis", "_srd_cone_source")),
            _set("_lite_area_targets", []),
            _set("_lite_area_cone_candidates", "_srd_cone_targets"),
            _each("_lite_area_cone_candidates", "_lite_area_cone_candidate", [
                _when("_lite_area_cone_candidate != _caster_state_id", [
                    _push("_lite_area_targets", "_lite_area_cone_candidate"),
                ]),
            ]),
            _set("_lite_area_origin", "_srd_cone_origin"),
            _set("_lite_area_source_frames", ["_srd_cone_source"]),
            _call("collect_srd_spell_area_cover_targets", {"source_state": "{_caster_state_id}", "targets": "{_lite_area_targets}", "origin": "{_lite_area_origin}", "axis": "{_srd_cone_axis}", "source_frames": "{_lite_area_source_frames}"}, ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters")),
        ]),
        _when("spell == 'fireball'", [
            _call("collect_srd_lite_area_sphere_targets", {"caster_state": "{_caster_state_id}", "lite_area_origin": "{lite_area_origin}", "range_feet": 60, "radius_feet": "{area_size_feet}"}, ("_lite_area_sphere_targets", "_lite_area_origin", "_lite_area_plane")),
            _call("collect_srd_lite_area_cover_targets", {"source_state": "{_caster_state_id}", "targets": "{_lite_area_sphere_targets}", "origin": "{_lite_area_origin}"}, ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters")),
        ]),
        _set("_lite_area_actual_targets", "_srd_area_targets"), _set("_lite_area_half_cover", "_srd_area_half_cover"), _set("_lite_area_three_quarters", "_srd_area_three_quarters"),
        _call("collect_srd_lite_area_objects", {"caster_state": "{_caster_state_id}", "shape": "{area_shape}", "size_feet": "{area_size_feet}", "origin": "{_lite_area_origin}", **{p["name"]: "{" + p["name"] + "}" for p in _intent()}}, ("_lite_area_objects",)),
        _set("_lite_area_actual_objects", "_lite_area_objects"), _set("_lite_area_no_shatter_objects", []),
    ]
    coherence = "((spell == 'burning_hands' and origin_kind == 'self' and range_feet == 0 and area_shape == 'cone' and area_size_feet == 17) or (spell == 'fireball' and origin_kind == 'point' and range_feet == 60 and area_shape == 'sphere' and area_size_feet == 13))"
    tools["qualify_srd_lite_instant_area"] = _tool_scene(
        "Qualify actual Lite area geometry and prepared recipients before any budget reservation.",
        [_entity("caster_state"), _param("spell", "string"),
         _param("origin_kind", "string"), _param("range_feet", "integer"),
         _param("area_shape", "string"), _param("area_size_feet", "integer"), *_intent()],
        [_require(coherence, "The Lite area must use its actual bound source profile."), *qualify],
    )
    geometry_params = {name: "{" + name + "}" for name in ("spell", "origin_kind", "range_feet", "area_shape", "area_size_feet")}
    geometry_params.update({"caster_state": "{_caster_state_id}", **{p["name"]: "{" + p["name"] + "}" for p in _intent()}})
    area["effects"][:0] = [_call("qualify_srd_lite_instant_area", geometry_params, (
        "_lite_area_actual_targets", "_lite_area_half_cover", "_lite_area_three_quarters",
        "_lite_area_actual_objects", "_lite_area_no_shatter_objects",
    ))]
    # Reuse the paid, adjusted area roll and the existing object durability owner.
    # The retained fire branch already owns ignition; no second roll is needed.
    area["effects"].append(_set("_lite_area_object_damage", "max(0, _spell_damage_total)"))
    area["effects"].append(_each("_lite_area_actual_objects", "_lite_area_damage_object", [
        _call("apply_srd_area_object_damage", {
            "caster_state": "{_caster_state_id}", "object_id": "{_lite_area_damage_object}",
            "component_1_amount": "{_lite_area_object_damage}", "component_1_damage_type": "fire",
        }, ("_area_object_eligible", "_object_damage_applied")),
        _when("_area_object_eligible == true", [_push("_spell_detail_rows", {
            "label": "_lite_area_damage_object", "value": "_object_damage_applied",
        })]),
    ]))
    area.setdefault("pre_conditions", []).append({"condition": _AREA_CONDITION, "error": "Use this source owner only for the two Lite fire areas."})
    tools["cast_srd_lite_instant_area"] = area

    # Carry origin intent through the genuine public and staged casting owners.
    changed = {"cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"}
    for name in changed:
        tool = tools[name]
        existing = {p["name"] for p in tool["parameters"]}
        tool["parameters"] += [p for p in _intent() if p["name"] not in existing]
        for rows in list(lists(tool["effects"])):
            for index, effect in enumerate(rows):
                if effect.get("call") == "cast_spell_instantaneous_area_save_damage":
                    selected = deepcopy(effect)
                    selected["call"] = "cast_srd_lite_instant_area"
                    selected["params"].update({p["name"]: "{" + p["name"] + "}" for p in _intent()})
                    rows[index] = _when(_AREA_CONDITION, [selected], [effect])
                elif effect.get("call") in changed:
                    effect["params"].update({p["name"]: "{" + p["name"] + "}" for p in _intent()})
                assignment = effect.get("set", {})
                if name == "begin_srd_spell_action" and assignment.get("field") == "_retained_nullable_capture_rows" and any(row["key"] == "'environment_origin'" for row in assignment["value"]):
                    captured = {row["key"] for row in assignment["value"]}
                    assignment["value"] += [
                        {"key": repr(p["name"]), "missing": p["name"] + " == null", "value": p["name"]}
                        for p in _intent() if repr(p["name"]) not in captured
                    ]
    completion_calls = 0
    for rows in lists(tools["complete_srd_pending_action"]["effects"]):
        for effect in rows:
            if effect.get("call") == "cast_spell":
                effect["params"].update({
                    p["name"]: "{_pending_action.parameters." + p["name"] + "}"
                    for p in _intent()
                })
                completion_calls += 1
    if completion_calls != 1:
        raise ValueError("Lite area requires the single retained pending-cast completion owner")
    reservations = 0
    for rows in list(lists(tools["begin_srd_spell_action"]["effects"])):
        for index, effect in enumerate(rows):
            if effect.get("call") == "spend_spell_casting_budget":
                actual_profile = deepcopy(geometry_params)
                actual_profile.update({name: "{_spell_definition." + name + "}" for name in (
                    "origin_kind", "range_feet", "area_shape", "area_size_feet",
                )})
                rows.insert(index, _when(_AREA_CONDITION, [
                    _require("_spell_definition.family == 'instantaneous_area_save_damage'", "Use the actual instantaneous Lite area profile."),
                    _call("qualify_srd_lite_instant_area", actual_profile),
                ]))
                reservations += 1
                break
    if reservations != 1:
        raise ValueError("Lite area requires the single retained spell budget reservation")

    # The ordinary root's memory/charm consumers must see the actual area recipients
    # before they can mutate or reject. Retain canonical input values for other families.
    projected = {name: "_lite_cast_" + name for name in (
        "targets", "half_cover_targets", "three_quarters_cover_targets",
        "area_object_targets", "shatter_object_targets", "target", "_target_id",
    )}
    for name in changed:
        owner = tools[name]
        actual_profile = deepcopy(geometry_params)
        actual_profile.update({field: "{_lite_cast_profile." + field + "}" for field in (
            "origin_kind", "range_feet", "area_shape", "area_size_feet",
        )})
        owner["effects"] = [
            *[effect for original, actual in projected.items() for effect in (
                _set(actual, None),
                _when(original + ' != null', [_set(actual, original)]),
            )],
            _when(_AREA_CONDITION, [
                _set("_lite_cast_profile", "definitions.spells[spell]"),
                _call("qualify_srd_lite_instant_area", actual_profile, (
                    "_lite_area_actual_targets", "_lite_area_actual_objects", "_lite_area_no_shatter_objects",
                    "_lite_area_half_cover", "_lite_area_three_quarters",
                )),
                _set(projected["targets"], "_lite_area_actual_targets"),
                _set(projected["half_cover_targets"], None),
                _when('len(_lite_area_half_cover) > 0', [
                    _set(projected["half_cover_targets"], "_lite_area_half_cover"),
                ]),
                _set(projected["three_quarters_cover_targets"], None),
                _when('len(_lite_area_three_quarters) > 0', [
                    _set(projected["three_quarters_cover_targets"], "_lite_area_three_quarters"),
                ]),
                _set(projected["area_object_targets"], "_lite_area_actual_objects"),
                _set(projected["shatter_object_targets"], "_lite_area_no_shatter_objects"),
                _set(projected["target"], None), _set(projected["_target_id"], None),
            ]),
            *_effect_names(owner["effects"], projected),
        ]
