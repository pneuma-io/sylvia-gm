"""Whirlpool uses its printed taper, actual water/body facts, and native saved consequences."""

import re
from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array
    from .srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _emanation_geometry_tools,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _push,
        _require,
        _set,
        _sphere_hazard_tools,
        _when,
    )
else:
    from srd_scene_relations import POSITION_EXPORTS, _array
    from srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _emanation_geometry_tools,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _push,
        _require,
        _set,
        _sphere_hazard_tools,
        _when,
    )


def choice():
    return _optional("whirlpool_top_diameter", "number", 50, minimum=5, maximum=50)


def formation(cube):
    """This selected bottom-centered taper fits real complete-depth water and the source Cube."""
    return [
        _require(
            f"{cube}.max[0] - {cube}.min[0] >= 50 and _water_depth >= 25",
            "Whirlpool needs an actual area at least fifty feet square and twenty-five feet deep.",
        ),
        _set(
            "_water_whirlpool",
            {
                "kind": "'tapered_whirlpool'",
                "origin": [
                    f"({cube}.min[0] + {cube}.max[0]) * 0.5",
                    f"({cube}.min[1] + {cube}.max[1]) * 0.5",
                    "_water_geometry.volume.min[2]",
                ],
                "base_radius": 2.5,
                "top_radius": "whirlpool_top_diameter * 0.5",
                "height": 25,
            },
        ),
    ]


def _source():
    return [
        _set("_whirl_source", "state.persistent_spell_areas[area_id]"),
        _set("_whirl_current", False),
        _set("_whirl_shape", None),
        _set("_whirl_water", None),
        _when(
            "_whirl_source.mode == 'control_water' and _whirl_source.water_effect == 'whirlpool'",
            [
                _call(
                    "qualify_srd_control_water_source",
                    {"caster_state": "{_whirl_source.caster}", "area_id": "{area_id}"},
                    {"_whirl_current": "_water_source_current", "_whirl_water": "_water_geometry"},
                ),
                _when(
                    "_whirl_current == true",
                    [
                        _require(
                            "_whirl_source.water_whirlpool.kind == 'tapered_whirlpool' and "
                            "_whirl_source.water_whirlpool.base_radius == 2.5 and "
                            "_whirl_source.water_whirlpool.height == 25 and "
                            "_whirl_source.water_whirlpool.top_radius >= 2.5 and "
                            "_whirl_source.water_whirlpool.top_radius <= 25",
                            "Use the actual printed paid Whirlpool taper.",
                        ),
                        _set("_whirl_shape", "_whirl_source.water_whirlpool"),
                    ],
                ),
            ],
        ),
    ]


def _nearest(point):
    return (
        f"max(0, abs({point}[0] - _whirl_shape.origin[0]) - "
        "_body_moving.footprint.half_extents[0]) ** 2 + "
        f"max(0, abs({point}[1] - _whirl_shape.origin[1]) - "
        "_body_moving.footprint.half_extents[1]) ** 2"
    )


def _geometry_tool():
    """A horizontal Box sweep meets the exact taper at its highest real vertical overlap."""
    original = _emanation_geometry_tools()["resolve_srd_environment_body_segment"]
    effects = deepcopy(original["effects"])
    branch = effects[-1]
    assert branch["condition"] == "_body_moving.metadata.plane == _body_fixed.metadata.plane"
    branch["condition"] = (
        "_whirl_current == true and _body_moving.metadata.plane == _whirl_source.plane"
    )
    body = branch["on_true"]
    body[0] = _set(
        "_body_bounds",
        {
            "min": [
                f"_whirl_shape.origin[{i}] - _body_moving.footprint.half_extents[{i}]"
                for i in range(2)
            ]
            + [0],
            "max": [
                f"_whirl_shape.origin[{i}] + _body_moving.footprint.half_extents[{i}]"
                for i in range(2)
            ]
            + [0],
        },
    )
    body[:] = [e for e in body if e.get("condition") != "point_space == true"]

    def cross_section(value):
        if isinstance(value, str):
            value = value.replace("start[2]", "0").replace("end[2]", "0")
            return re.sub(r"\bradius\b", "_whirl_section_radius", value)
        if isinstance(value, list):
            return [cross_section(v) for v in value]
        if isinstance(value, dict):
            return {k: cross_section(v) for k, v in value.items()}
        return value

    branch["on_true"] = [
        _require(
            "_body_moving.footprint.kind == 'box'",
            "Whirlpool contact needs the actual admitted Box body.",
        ),
        _require(
            "abs(start[2] - end[2]) < 0.000000001",
            "Vertical Whirlpool sweeps need their actual variable-radius interval consumer.",
        ),
        _set(
            "_whirl_vertical_overlap",
            "start[2] < _whirl_shape.origin[2] + 25 and "
            "start[2] + _body_moving.height > _whirl_shape.origin[2]",
        ),
        _when(
            "_whirl_vertical_overlap == true",
            [
                _set(
                    "_whirl_section_z",
                    "min(start[2] + _body_moving.height, _whirl_shape.origin[2] + 25)",
                ),
                _set(
                    "_whirl_section_radius",
                    "2.5 + (_whirl_shape.top_radius - 2.5) * "
                    "(_whirl_section_z - _whirl_shape.origin[2]) * 0.04",
                ),
                *cross_section(body),
                _when(
                    "_body_hit == true",
                    [
                        _set(
                            "_whirl_midpoint",
                            [
                                f"start[{i}] + (end[{i}] - start[{i}]) * "
                                "(_body_entry + _body_exit) * 0.5"
                                for i in range(3)
                            ],
                        ),
                        _set(
                            "_body_hit",
                            "_body_exit > _body_entry and "
                            + _nearest("_whirl_midpoint")
                            + " < _whirl_section_radius ** 2",
                        ),
                    ],
                ),
                _set("_whirl_start_inside", _nearest("start") + " < _whirl_section_radius ** 2"),
                _set("_whirl_end_inside", _nearest("end") + " < _whirl_section_radius ** 2"),
            ],
        ),
    ]
    return _environment_tool(
        "Resolve genuine horizontal Box-body contact with the saved Whirlpool taper.",
        [_param("moving_subject"), _param("area_id"), _array("start"), _array("end")],
        [
            *effects[:3],
            *_source(),
            _set("_body_hit", False),
            _set("_body_entry", 2),
            _set("_body_exit", -1),
            _set("_whirl_start_inside", False),
            _set("_whirl_end_inside", False),
            branch,
        ],
    )


def _target_tool():
    return _environment_tool(
        "Qualify actual living submerged Box contact against the current paid Whirlpool.",
        [_entity("target_state"), _param("area_id")],
        [
            _set("_whirl_target_inside", False),
            *_source(),
            _when(
                "_whirl_current == true and target_state.is_alive == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_whirl_target", "_info_scene_entity"),
                    _when(
                        "_whirl_target.metadata.plane == _whirl_source.plane",
                        [
                            _require(
                                "_whirl_target.footprint.kind == 'box'",
                                "Whirlpool membership needs the actual admitted Box body.",
                            ),
                            _set(
                                "_whirl_submerged",
                                " and ".join(
                                    f"_whirl_target.position[{i}] - "
                                    f"_whirl_target.footprint.half_extents[{i}] >= "
                                    f"_whirl_water.volume.min[{i}] and "
                                    f"_whirl_target.position[{i}] + "
                                    f"_whirl_target.footprint.half_extents[{i}] <= "
                                    f"_whirl_water.volume.max[{i}]"
                                    for i in range(2)
                                ),
                            ),
                            _set(
                                "_whirl_submerged",
                                "_whirl_submerged == true and "
                                "_whirl_target.position[2] >= _whirl_water.volume.min[2] and "
                                "_whirl_target.position[2] + _whirl_target.height <= "
                                "_whirl_water.volume.max[2]",
                            ),
                            _when(
                                "_whirl_submerged == true",
                                [
                                    _call(
                                        "resolve_srd_whirlpool_body_segment",
                                        {
                                            "moving_subject": "{_target_state_id}",
                                            "area_id": "{area_id}",
                                            "start": "{_whirl_target.position}",
                                            "end": "{_whirl_target.position}",
                                        },
                                        {"_whirl_target_inside": "_body_hit"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _rename(value):
    if isinstance(value, str):
        return re.sub(r"\b_sphere_", "_whirl_", value)
    if isinstance(value, list):
        return [_rename(v) for v in value]
    if isinstance(value, dict):
        return {_rename(k): _rename(v) for k, v in value.items()}
    return value


def _event_tool():
    """Entry's first-per-turn receipt is independent from the actual end-turn receipt."""
    tool = _rename(deepcopy(_sphere_hazard_tools()["resolve_srd_environment_sphere_event"]))
    tool["parameters"].append(_param("timing", "enum", enum_values=["entry", "end"]))
    tool["description"] = "Settle actual Whirlpool entry or end-turn Strength-half damage."
    for effects in _effect_lists(tool["effects"]):
        for e in effects:
            condition = e.get("condition", "")
            if "_whirl_event_source.event_clock ==" in condition:
                e["condition"] = "false"
            if "_whirl_event_source.appearance_in_progress" in condition:
                e["condition"] = "false"
            if e.get("call") == "qualify_srd_environment_sphere_target":
                e["call"] = "qualify_srd_whirlpool_target"
            if e.get("call") == "qualify_srd_environment_sphere_effect":
                e.clear()
                e.update(_set("_whirl_effect_applies", True))
            if e.get("store_as") == "_whirl_profile":
                e.clear()
                e.update(
                    _set(
                        "_whirl_profile",
                        {
                            "save_ability": "'strength'",
                            "damage_dice": 2,
                            "damage_die": "'d8'",
                            "damage_per_slot_level": 0,
                            "base_level": 4,
                            "damage_type": "'bludgeoning'",
                        },
                    )
                )
            field = e.get("set", {}).get("field", "")
            for old, new in (
                ("event_clock", "clock"),
                ("event_turn", "turn"),
                ("event_target_ids", "targets"),
            ):
                if field.endswith("." + old):
                    root = field.rsplit(".", 1)[0]
                    value = e["set"]["value"]
                    e.clear()
                    e.update(
                        _when(
                            "timing == 'entry'",
                            [
                                _set(root + ".whirlpool_entry_" + new, value),
                            ],
                            [_set(root + ".whirlpool_end_" + new, value)],
                        )
                    )
            if field == "target_state.last_environment_sphere_save":
                e["set"]["field"] = "target_state.last_whirlpool_save"
                e["set"]["value"].update(timing="timing", sequence="_whirl_event_source.sequence")
    # Source-bound timing never accepts an invented public save/entry verdict.
    index = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_whirl_event_source"
    )
    tool["effects"][index + 1 : index + 1] = [
        _set("_whirl_triggered", False),
        _require(
            "_whirl_event_source.water_effect == 'whirlpool' and "
            "_whirl_event_source.mode == 'control_water'",
            "Use this actual Whirlpool source.",
        ),
        _when(
            "timing == 'end'",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _target_state_id",
                    "Only the actual ending creature can receive its Whirlpool end-turn save.",
                ),
            ],
        ),
    ]
    prior = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("condition", "").startswith("_target_state_id not in")
    )
    tool["effects"][prior:prior] = [
        _when(
            "timing == 'entry' and _whirl_event_source.whirlpool_entry_clock == _whirl_clock "
            "and _whirl_event_source.whirlpool_entry_turn == _whirl_turn",
            [
                _set("_whirl_prior_targets", "_whirl_event_source.whirlpool_entry_targets"),
            ],
        ),
        _when(
            "timing == 'end' and _whirl_event_source.whirlpool_end_clock == _whirl_clock "
            "and _whirl_event_source.whirlpool_end_turn == _whirl_turn",
            [
                _set("_whirl_prior_targets", "_whirl_event_source.whirlpool_end_targets"),
            ],
        ),
    ]
    for effects in _effect_lists(tool["effects"]):
        index = next(
            (i for i, e in enumerate(effects) if e.get("call") == "apply_srd_qualifying_damage"),
            None,
        )
        if index is not None:
            effects.insert(index, _set("_whirl_triggered", True))
    # Rebind raw area keys after the cloned owner gains interpolated receipt paths.
    return tool


def _escape_tool():
    checks = {
        "actor_state": "{_actor_state_id}",
        "kind": "ability_check",
        "ability": "strength",
        "skill": "athletics",
        "circumstantial_modifier": 0,
        "target_number": "{_whirl_source.save_dc}",
    }
    return _public(
        _environment_tool(
            "Take the genuine source-DC Strength (Athletics) Action before swimming away.",
            [
                _entity("actor_state"),
                _param("area_id"),
                _optional("actor", "string", None, resolve="participant"),
            ],
            [
                _require(
                    "actor_state.is_alive == true and "
                    "'incapacitated' not in actor_state.conditions",
                    "The escaping creature must be able to act.",
                ),
                _call(
                    "qualify_srd_whirlpool_target",
                    {"target_state": "{_actor_state_id}", "area_id": "{area_id}"},
                    ("_whirl_target_inside", "_whirl_source"),
                ),
                _require("_whirl_target_inside == true", "Escape this actual current Whirlpool."),
                _require(
                    "combat.turn_active == true and combat.active_actor == _actor_state_id",
                    "Only the active creature can take this escape Action.",
                ),
                _set("_whirl_escape_proficiency", "'none'"),
                _when(
                    "actor != null",
                    [
                        _require(
                            "_actor_id == _actor_state_id",
                            "Use this creature's actual character data.",
                        ),
                        _when(
                            "'athletics' in actor.data.skill_proficiencies",
                            [_set("_whirl_escape_proficiency", "'normal'")],
                        ),
                    ],
                    [
                        _require(
                            "actor_state.skill_check_modifiers.athletics != null",
                            "Record this creature's actual Athletics modifier.",
                        ),
                    ],
                ),
                _call("spend_srd_action", {"magic_action": False}),
                _when(
                    "actor != null",
                    [
                        _call(
                            "d20_test",
                            {
                                **checks,
                                "actor": "{_actor_id}",
                                "proficiency_factor": "{_whirl_escape_proficiency}",
                            },
                            ("_d20_test", "_outcome"),
                        ),
                    ],
                    [
                        _call(
                            "d20_test",
                            {
                                **checks,
                                "ability_modifier": "{actor_state.skill_check_modifiers.athletics}",
                                "proficiency_bonus": 0,
                                "proficiency_factor": "none",
                            },
                            ("_d20_test", "_outcome"),
                        ),
                    ],
                ),
                _set(
                    "actor_state.whirlpool_escape",
                    {
                        "source_id": "area_id",
                        "sequence": "_whirl_source.sequence",
                        "effect_sequence": "_whirl_source.whirlpool_effect_sequence",
                        "succeeded": "_outcome == 'success'",
                        "consumed": False,
                        "roll": "_d20_test",
                        "turn": "combat.turn_sequence",
                    },
                ),
            ],
            public=True,
        ),
        "combat",
        "Whirlpool escape",
        [("Outcome", "_outcome")],
    )


def _route_tool():
    return _environment_tool(
        "Require this exact source's earned escape before actual swimming away from its taper.",
        [_entity("actor_state"), _array("start"), _array("end"), _param("area_id")],
        [
            _call(
                "resolve_srd_whirlpool_body_segment",
                {
                    "moving_subject": "{_actor_state_id}",
                    "area_id": "{area_id}",
                    "start": "{start}",
                    "end": "{end}",
                },
                (
                    "_whirl_current",
                    "_whirl_source",
                    "_body_moving",
                    "_whirl_shape",
                    "_whirl_start_inside",
                    "_whirl_end_inside",
                ),
            ),
            _when(
                "_whirl_start_inside == true",
                [
                    _set(
                        "_whirl_start_radius_squared",
                        "(start[0] - _whirl_shape.origin[0]) ** 2 + "
                        "(start[1] - _whirl_shape.origin[1]) ** 2",
                    ),
                    _set(
                        "_whirl_end_radius_squared",
                        "(end[0] - _whirl_shape.origin[0]) ** 2 + "
                        "(end[1] - _whirl_shape.origin[1]) ** 2",
                    ),
                    _set("_whirl_away", "_whirl_end_radius_squared > _whirl_start_radius_squared"),
                    _when(
                        "_whirl_away == true",
                        [
                            _require(
                                "actor_state.whirlpool_escape.source_id == area_id and "
                                "actor_state.whirlpool_escape.sequence == "
                                "_whirl_source.sequence and "
                                "actor_state.whirlpool_escape.effect_sequence == "
                                "_whirl_source.whirlpool_effect_sequence and "
                                "actor_state.whirlpool_escape.succeeded == true and "
                                "actor_state.whirlpool_escape.consumed != true",
                                "Pass the actual Whirlpool Athletics Action before swimming away.",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def tools():
    return {
        "resolve_srd_whirlpool_body_segment": _geometry_tool(),
        "qualify_srd_whirlpool_target": _target_tool(),
        "resolve_srd_whirlpool_event": _event_tool(),
        "escape_srd_whirlpool": _escape_tool(),
        "qualify_srd_whirlpool_route": _route_tool(),
    }


def bind_sources(changed):
    """Join the true first-entry boundary and independent real ending-turn callback."""
    entry = changed["resolve_srd_environment_hazard_entry"]
    loop = next(e["for_each"] for e in entry["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'control_water' and "
            "_environment_area.water_effect == 'whirlpool'",
            [
                _call(
                    "resolve_srd_whirlpool_body_segment",
                    {
                        "moving_subject": "{_actor_state_id}",
                        "area_id": "{_environment_id}",
                        "start": "{_hazard_start}",
                        "end": "{destination}",
                    },
                    ("_body_hit", "_body_entry", "_body_exit", "_whirl_start_inside"),
                ),
                _set("_whirl_entry_done", False),
                _when(
                    "_environment_area.whirlpool_entry_clock == _hazard_clock and "
                    "_environment_area.whirlpool_entry_turn == _hazard_turn",
                    [
                        _set(
                            "_whirl_entry_done",
                            "_actor_state_id in _environment_area.whirlpool_entry_targets",
                        ),
                    ],
                ),
                _when(
                    "_body_hit == true and _whirl_start_inside != true and "
                    "_whirl_entry_done != true and _body_exit > _body_entry",
                    [
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_hazard_start}", "end": "{destination}"},
                            ("_scene_distance",),
                        ),
                        _set(
                            "_body_entry",
                            "_body_entry + min((_body_exit - _body_entry) * 0.5, "
                            "0.000001 * max(0.000000001, _scene_distance) ** -1)",
                        ),
                        _when(
                            "_body_entry < _hazard_entry_t - 0.000000000001",
                            [
                                _set("_hazard_entry_events", []),
                                _set("_hazard_entry_t", "_body_entry"),
                            ],
                        ),
                        _when(
                            "abs(_body_entry - _hazard_entry_t) < 0.000000001",
                            [
                                _push(
                                    "_hazard_entry_events",
                                    {"area_id": "_environment_id", "target_id": "_actor_state_id"},
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    dispatch = changed["resolve_srd_environment_hazard_event"]
    old = dispatch["effects"][-1]
    dispatch["effects"][-1] = _when(
        "_hazard_dispatch_source.mode == 'control_water' and "
        "_hazard_dispatch_source.water_effect == 'whirlpool'",
        [
            _call(
                "resolve_srd_whirlpool_event",
                {
                    "target_state": "{_target_state_id}",
                    "area_id": "{area_id}",
                    "timing": "entry",
                    "witness_state_ids": "{witness_state_ids}",
                },
                ("_whirl_triggered",),
            ),
            _when("_whirl_triggered == true", [_set("target_state.whirlpool_escape", None)]),
        ],
        [old],
    )
    ending = changed["resolve_srd_environment_hazard_end_turn"]
    loop = next(e["for_each"] for e in ending["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'control_water' and "
            "_environment_area.water_effect == 'whirlpool'",
            [
                _call(
                    "resolve_srd_whirlpool_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "timing": "end",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                )
            ],
        )
    )


def bind_route(route):
    """Use real already-qualified water routes; never charge a second movement budget."""
    route["effects"].append(
        _each(
            "_water_route_source_ids",
            "_whirl_route_id",
            [
                _set("_whirl_route_source", "state.persistent_spell_areas[_whirl_route_id]"),
                _when(
                    "_whirl_route_source.mode == 'control_water' and "
                    "_whirl_route_source.water_effect == 'whirlpool' and "
                    "_water_start_wet == true and mode == 'swim'",
                    [
                        _call(
                            "qualify_srd_whirlpool_route",
                            {
                                "actor_state": "{_actor_state_id}",
                                "area_id": "{_whirl_route_id}",
                                "start": "{start}",
                                "end": "{end}",
                            },
                        ),
                    ],
                ),
            ],
        )
    )
