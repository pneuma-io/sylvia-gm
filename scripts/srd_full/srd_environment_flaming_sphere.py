"""A paid Flaming Sphere retains its real ground-supported body and later uses."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array
    from .srd_spell_environment import (
        _areas,
        _call,
        _each,
        _effect_lists,
        _emanation_geometry_tools,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _require,
        _set,
        _when,
    )
    from .srd_spell_object_content import _lookup_object, _metadata_patch
else:
    from srd_scene_relations import POSITION_EXPORTS, _array
    from srd_spell_environment import (
        _areas,
        _call,
        _each,
        _effect_lists,
        _emanation_geometry_tools,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _require,
        _set,
        _when,
    )
    from srd_spell_object_content import _lookup_object, _metadata_patch

SOURCE_SHA = "fc77476347fa04555ec34c6e894c157f7526c6dd3e113d393c951a1fcde54bef"
PROFILE = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 2,
    "range_kind": "distance",
    "range_feet": 60,
    "radius_feet": 2.5,
    "radius_per_slot_level": 0,
    "duration_rounds": 10,
    "concentration": True,
    "environment_mode": "flaming_sphere",
    "damage_dice": 2,
    "damage_die": "d6",
    "damage_per_slot_level": 1,
    "damage_type": "fire",
    "save_ability": "dexterity",
}
BODY_EXPORTS = ("_body_hit", "_body_entry", "_body_exit", "_body_bounds")


def _shape(source):
    """Only this genuine fixed-radius paid source supplies the mathematical body."""
    return [
        _set("_flame_shape", source),
        _require(
            "_flame_shape.profile == 'environment' and _flame_shape.mode == 'flaming_sphere' "
            "and _flame_shape.spell == 'flaming_sphere' and _flame_shape.radius == 2.5 and "
            f"_flame_shape.source_sha256 == {SOURCE_SHA!r}",
            "Use the actual pinned five-foot-diameter Flaming Sphere.",
        ),
    ]


def _source_tool():
    return _environment_tool(
        "Authenticate the actual paid Flaming Sphere, caster and concentration lifetime.",
        [_entity("caster_state"), _param("area_id")],
        [
            _set("_flaming_source", "state.persistent_spell_areas[area_id]"),
            _set(
                "_flaming_source_current",
                "_flaming_source.profile == 'environment' and _flaming_source.mode == "
                "'flaming_sphere' and _flaming_source.spell == 'flaming_sphere' and "
                f"_flaming_source.source_sha256 == {SOURCE_SHA!r}",
            ),
            _set(
                "_flaming_source_current",
                "_flaming_source_current == true and _flaming_source.active == true and "
                "state.lasting_tick < _flaming_source.expires_tick and "
                "caster_state.is_alive == true and caster_state.hp > 0 and "
                "_flaming_source.caster == _caster_state_id and "
                "caster_state.environment_area_id == area_id and "
                "caster_state.concentration_spell_id == 'flaming_sphere' and "
                "caster_state.concentration_effect_family == 'spell_environment'",
            ),
        ],
    )


def _ground_tool():
    return _environment_tool(
        "Qualify the whole Sphere on one actual same-plane flat Box support.",
        [_array("point"), _param("plane")],
        [
            _call(
                "qualify_srd_environment_ground",
                {"point": "{point}", "plane": "{plane}"},
                ("_environment_ground_id",),
            ),
            _set("_flaming_support", None),
            _set("_flaming_ground_entries", "state.spatial_scene.geometry"),
            _each(
                "_flaming_ground_entries",
                "_flaming_ground",
                [
                    _when(
                        "_flaming_ground.id == _environment_ground_id",
                        [
                            _set("_flaming_support", "_flaming_ground"),
                        ],
                    ),
                ],
            ),
            _require(
                "_flaming_support.volume.kind == 'box' and _flaming_support.active == true "
                "and _flaming_support.movement == 'blocking' and "
                "_flaming_support.metadata.ground == true and "
                "_flaming_support.metadata.plane == plane and "
                "abs(point[2] - _flaming_support.volume.max[2]) < 0.000000001",
                "The selected roll currently requires an actual flat Box ground surface.",
            ),
            _require(
                " and ".join(
                    f"point[{i}] - 2.5 >= _flaming_support.volume.min[{i}] and "
                    f"point[{i}] + 2.5 <= _flaming_support.volume.max[{i}]"
                    for i in range(2)
                ),
                "The whole Sphere must remain over its actual supporting surface.",
            ),
        ],
    )


def _body_tool():
    """Bind saved Box bodies to the existing exact rounded-Box/Sphere interval kernel."""
    original = _emanation_geometry_tools()["resolve_srd_environment_body_segment"]
    branch = deepcopy(original["effects"][-1]["on_true"])
    # Everything after admitted bounds is the existing affine distance kernel.
    kernel = branch[2:]
    assert kernel[0]["set"]["field"] == "_body_cuts"

    def fixed_radius(value):
        if isinstance(value, str):
            import re

            return re.sub(r"\bradius\b", "_flaming_body_radius", value)
        if isinstance(value, list):
            return [fixed_radius(v) for v in value]
        if isinstance(value, dict):
            return {k: fixed_radius(v) for k, v in value.items()}
        return value

    return _environment_tool(
        "Resolve the genuine Sphere route against one actual same-plane admitted Box body.",
        [
            _param("area_id"),
            _param("subject"),
            _param("subject_kind", "string", enum_values=["entity", "geometry"]),
            _array("start"),
            _array("end"),
        ],
        [
            *_shape("state.persistent_spell_areas[area_id]"),
            _require(
                " and ".join(
                    f"abs(start[{i}] - _flame_shape.origin[{i}]) < 0.000000001" for i in range(3)
                ),
                "The physical route must start at this Sphere's actual current center.",
            ),
            _set("_body_hit", False),
            _set("_flaming_body_touched", False),
            _set("_body_entry", 2),
            _set("_body_exit", -1),
            _set("_flaming_body_radius", 2.5),
            _set("_body_bounds", {}),
            _set("_flame_box_plane", None),
            _when(
                "subject_kind == 'entity'",
                [
                    _call(
                        "srd_information_scene_entity", {"subject": "{subject}"}, POSITION_EXPORTS
                    ),
                    _require(
                        "_info_scene_entity.footprint.kind == 'box'",
                        "Physical Sphere contact currently requires an admitted Box body.",
                    ),
                    _set("_flame_box_plane", "_info_scene_entity.metadata.plane"),
                    _set(
                        "_body_bounds",
                        {
                            "min": [
                                f"_info_position[{i}] - "
                                f"_info_scene_entity.footprint.half_extents[{i}]"
                                for i in range(2)
                            ]
                            + ["_info_position[2]"],
                            "max": [
                                f"_info_position[{i}] + "
                                f"_info_scene_entity.footprint.half_extents[{i}]"
                                for i in range(2)
                            ]
                            + ["_info_position[2] + _info_scene_entity.height"],
                        },
                    ),
                ],
                [
                    _set("_flame_box", None),
                    _set("_flame_boxes", "state.spatial_scene.geometry"),
                    _each(
                        "_flame_boxes",
                        "_flame_box_candidate",
                        [
                            _when(
                                "_flame_box_candidate.id == subject and "
                                "_flame_box_candidate.active == true",
                                [
                                    _set("_flame_box", "_flame_box_candidate"),
                                ],
                            ),
                        ],
                    ),
                    _require(
                        "_flame_box.volume.kind == 'box'",
                        "Physical Sphere route currently requires actual Box solid geometry.",
                    ),
                    _set("_flame_box_plane", "_flame_box.metadata.plane"),
                    _set("_body_bounds", "_flame_box.volume"),
                ],
            ),
            _when(
                "_flame_box_plane == _flame_shape.plane",
                [
                    *fixed_radius(kernel),
                    _set("_flaming_body_touched", "_body_hit"),
                    _when(
                        "_body_hit == true",
                        [
                            _set(
                                "_flame_mid",
                                [
                                    f"start[{i}] + (end[{i}] - start[{i}]) * "
                                    "(_body_entry + _body_exit) * 0.5"
                                    for i in range(3)
                                ],
                            ),
                            _set(
                                "_flame_nearest",
                                [
                                    f"min(max(_flame_mid[{i}], _body_bounds.min[{i}]), "
                                    f"_body_bounds.max[{i}])"
                                    for i in range(3)
                                ],
                            ),
                            _set(
                                "_body_hit",
                                " + ".join(
                                    f"(_flame_mid[{i}] - _flame_nearest[{i}]) ** 2"
                                    for i in range(3)
                                )
                                + " < 6.25 - 0.000000001",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _occupant_tool():
    return _environment_tool(
        "Qualify actual living creature contact, including admitted passable creature bodies.",
        [_entity("target_state"), _param("area_id"), _array("start"), _array("end")],
        [
            _set("_flaming_contact", False),
            _set("_flaming_contact_entry", 2),
            _when(
                "target_state.is_alive == true and target_state.hp > 0",
                [
                    _require(
                        "target_state.creature_kind == 'character' or "
                        "(target_state.creature_type != null and "
                        "target_state.creature_type != 'spell_force')",
                        "Admit the actual living creature type before Sphere contact.",
                    ),
                    _call(
                        "resolve_srd_flaming_sphere_box_segment",
                        {
                            "area_id": "{area_id}",
                            "subject": "{_target_state_id}",
                            "subject_kind": "entity",
                            "start": "{start}",
                            "end": "{end}",
                        },
                        BODY_EXPORTS,
                    ),
                    _set("_flaming_contact", "_body_hit"),
                    _set("_flaming_contact_entry", "_body_entry"),
                ],
            ),
        ],
    )


def _space_tool():
    return _environment_tool(
        "Reject genuine occupied Sphere formation and unsupported physical solid overlap.",
        [_param("area_id")],
        [
            *_shape("state.persistent_spell_areas[area_id]"),
            _set("_flaming_space_origin", "_flame_shape.origin"),
            _set("_flaming_space_entries", "state.spatial_scene.entities"),
            _each(
                "_flaming_space_entries",
                "_flaming_space_entry",
                [
                    _when(
                        "_flaming_space_entry.active == true and "
                        "_flaming_space_entry.metadata.participant_ref != null",
                        [
                            _call(
                                "qualify_srd_flaming_sphere_occupant",
                                {
                                    "target_state": (
                                        "{_flaming_space_entry.metadata.participant_ref}"
                                    ),
                                    "area_id": "{area_id}",
                                    "start": "{_flaming_space_origin}",
                                    "end": "{_flaming_space_origin}",
                                },
                                ("_flaming_contact",),
                            ),
                            _require(
                                "_flaming_contact != true",
                                "Flaming Sphere requires actual unoccupied creature space.",
                            ),
                        ],
                    ),
                    _when(
                        "_flaming_space_entry.active == true and "
                        "_flaming_space_entry.movement == 'blocking'",
                        [
                            _call(
                                "resolve_srd_flaming_sphere_box_segment",
                                {
                                    "area_id": "{area_id}",
                                    "subject": "{_flaming_space_entry.id}",
                                    "subject_kind": "entity",
                                    "start": "{_flaming_space_origin}",
                                    "end": "{_flaming_space_origin}",
                                },
                                ("_body_hit",),
                            ),
                            _require(
                                "_body_hit != true", "The Sphere body overlaps actual solid space."
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "qualify_srd_flaming_sphere_route_solids",
                {
                    "area_id": "{area_id}",
                    "destination": "{_flaming_space_origin}",
                },
            ),
        ],
    )


def _solids_tool():
    return _environment_tool(
        "Keep the whole actual Sphere clear of actual same-plane blocking Box geometry.",
        [_param("area_id"), _array("destination")],
        [
            *_shape("state.persistent_spell_areas[area_id]"),
            _set("_flaming_solid_origin", "_flame_shape.origin"),
            _set("_flaming_solid_plane", "_flame_shape.plane"),
            _set("_flaming_solids", "state.spatial_scene.geometry"),
            _each(
                "_flaming_solids",
                "_flaming_solid",
                [
                    _when(
                        "_flaming_solid.active == true and _flaming_solid.movement == 'blocking' "
                        "and _flaming_solid.metadata.plane == _flaming_solid_plane",
                        [
                            _call(
                                "resolve_srd_flaming_sphere_box_segment",
                                {
                                    "area_id": "{area_id}",
                                    "subject": "{_flaming_solid.id}",
                                    "subject_kind": "geometry",
                                    "start": "{_flaming_solid_origin}",
                                    "end": "{destination}",
                                },
                                ("_body_hit",),
                            ),
                            _require(
                                "_body_hit != true",
                                "An actual solid blocks this ground roll; "
                                "barrier jumps remain unsupported.",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _target_tool(existing):
    """The printed five-foot reach is measured from the genuine radius2.5 body."""
    tool = deepcopy(existing["qualify_srd_environment_sphere_target"])
    for effects in _effect_lists(tool["effects"]):
        for effect in effects:
            if effect.get("call") == "qualify_srd_environment_sphere_source":
                effect["call"] = "qualify_srd_flaming_sphere_source"
                effect["export_context"] = {"_sphere_source_current": "_flaming_source_current"}
            if effect.get("set", {}).get("field") == "_sphere_target_source":
                effect["set"]["value"] = "state.persistent_spell_areas[_environment_area_key]"
    source_index = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_sphere_target_source"
    )
    tool["effects"][source_index + 1 : source_index + 1] = [
        _set("_flaming_target_source", "_sphere_target_source"),
        _set(
            "_sphere_target_source",
            {
                "origin": "_flaming_target_source.origin",
                "plane": "_flaming_target_source.plane",
                "caster": "_flaming_target_source.caster",
                "radius": 7.5,
            },
        ),
    ]
    return tool


def _event_tool(existing):
    """Separate ram receipts from actual target-end receipts before the sole damage lifecycle."""
    tool = deepcopy(existing["resolve_srd_environment_sphere_event"])
    tool["parameters"].append(_param("trigger", "string", enum_values=["end", "ram"]))
    index = next(
        i for i, e in enumerate(tool["effects"]) if e.get("set", {}).get("field") == "_sphere_clock"
    )
    end = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_sphere_prior_targets"
    )
    tool["effects"][index:end] = [
        *_shape("_sphere_event_source"),
        _set("_sphere_clock", "'flaming_end'"),
        _set("_sphere_turn", "combat.turn_sequence"),
        _when(
            "trigger == 'end'",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _target_state_id",
                    "Flaming Sphere end damage requires the target's actual ending turn.",
                ),
            ],
            [
                _require(
                    "_sphere_event_source.ram_in_progress == true and "
                    "_sphere_event_source.ram_target == _target_state_id and "
                    "_sphere_event_source.roll_turn == combat.turn_sequence and "
                    "combat.turn_active == true and combat.active_actor == "
                    "_sphere_event_source.caster",
                    "Settle only this actual pending ground-roll ram.",
                ),
                _set("_sphere_clock", "'flaming_ram'"),
                _set("_sphere_turn", "_sphere_event_source.roll_sequence"),
            ],
        ),
    ]
    for effects in _effect_lists(tool["effects"]):
        for effect in effects:
            if effect.get("call") == "qualify_srd_environment_sphere_target":
                effect["call"] = "qualify_srd_flaming_sphere_target"
            elif effect.get("call") == "qualify_srd_environment_sphere_effect":
                effect.clear()
                effect.update(_set("_sphere_effect_applies", True))
            if effect.get("set", {}).get("field") == "target_state.last_environment_sphere_save":
                effect["set"]["field"] = "target_state.last_flaming_sphere_save"
                effect["set"]["value"]["trigger"] = "trigger"
    return tool


def _roll_tool():
    return _public(
        _environment_tool(
            "Spend the actual caster's Bonus Action to roll the ground Sphere "
            "and stop at its first creature.",
            [
                _entity("caster_state"),
                _param("area_id"),
                _array("point"),
                _optional("witness_state_ids", "array", None, items={"type": "string"}),
            ],
            [
                _call(
                    "qualify_srd_flaming_sphere_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "area_id": "{area_id}",
                    },
                    ("_flaming_source_current", "_flaming_source"),
                ),
                _require(
                    "_flaming_source_current == true and combat.turn_active == true and "
                    "combat.active_actor == _caster_state_id",
                    "Only the actual active caster can roll this retained Sphere.",
                ),
                _require(
                    "_flaming_source.roll_stopped_turn != combat.turn_sequence",
                    "This Sphere already rammed a creature and stopped for the actual turn.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _flaming_source.plane",
                    "Rolling needs this actual caster and Sphere on the same plane.",
                ),
                _set("_flaming_roll_origin", "_flaming_source.origin"),
                _set("_flaming_roll_destination", ["point[0]", "point[1]", "point[2] + 2.5"]),
                _require(
                    "abs(point[2] - _flaming_source.ground_point[2]) < 0.000000001",
                    "The supported ground roll remains on one actual flat surface.",
                ),
                _call(
                    "measure_srd_information_distance",
                    {
                        "start": "{_flaming_roll_origin}",
                        "end": "{_flaming_roll_destination}",
                    },
                    ("_scene_distance",),
                ),
                _require(
                    "_scene_distance > 0 and _scene_distance <= 30",
                    "Choose an actual positive ground roll of at most thirty feet.",
                ),
                _call(
                    "qualify_srd_flaming_sphere_ground",
                    {
                        "point": "{point}",
                        "plane": "{_flaming_source.plane}",
                    },
                    ("_environment_ground_id",),
                ),
                _require(
                    "_environment_ground_id == _flaming_source.ground_id",
                    "This roll needs one continuous flat support; pit jumps remain unsupported.",
                ),
                *_map_bounds("_flaming_roll_destination"),
                _set("_flaming_roll_t", 1),
                _set("_flaming_roll_target", None),
                _set("_flaming_roll_entries", "state.spatial_scene.entities"),
                _each(
                    "_flaming_roll_entries",
                    "_flaming_roll_entry",
                    [
                        _when(
                            "_flaming_roll_entry.active == true and "
                            "_flaming_roll_entry.metadata.participant_ref != null",
                            [
                                _call(
                                    "qualify_srd_flaming_sphere_occupant",
                                    {
                                        "target_state": (
                                            "{_flaming_roll_entry.metadata.participant_ref}"
                                        ),
                                        "area_id": "{area_id}",
                                        "start": "{_flaming_roll_origin}",
                                        "end": "{_flaming_roll_destination}",
                                    },
                                    ("_flaming_contact", "_flaming_contact_entry"),
                                ),
                                _when(
                                    "_flaming_contact == true and "
                                    "_flaming_contact_entry < _flaming_roll_t",
                                    [
                                        _set("_flaming_roll_t", "_flaming_contact_entry"),
                                        _set(
                                            "_flaming_roll_target",
                                            "_flaming_roll_entry.metadata.participant_ref",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _set(
                    "_flaming_roll_final",
                    [
                        f"_flaming_roll_origin[{i}] + (_flaming_roll_destination[{i}] - "
                        f"_flaming_roll_origin[{i}]) * _flaming_roll_t"
                        for i in range(3)
                    ],
                ),
                _call(
                    "qualify_srd_flaming_sphere_route_solids",
                    {
                        "area_id": "{area_id}",
                        "destination": "{_flaming_roll_final}",
                    },
                ),
                _each(
                    "_flaming_roll_entries",
                    "_flaming_roll_solid",
                    [
                        _when(
                            "_flaming_roll_solid.active == true and "
                            "_flaming_roll_solid.movement == 'blocking' and "
                            "_flaming_roll_solid.metadata.participant_ref == null",
                            [
                                _call(
                                    "resolve_srd_flaming_sphere_box_segment",
                                    {
                                        "area_id": "{area_id}",
                                        "subject": "{_flaming_roll_solid.id}",
                                        "subject_kind": "entity",
                                        "start": "{_flaming_roll_origin}",
                                        "end": "{_flaming_roll_final}",
                                    },
                                    ("_body_hit",),
                                ),
                                _require(
                                    "_body_hit != true",
                                    "An actual solid object blocks the Sphere body.",
                                ),
                            ],
                        ),
                    ],
                ),
                _call(
                    "consume_srd_bonus_action",
                    {
                        "actor_state": "{_caster_state_id}",
                        "bonus_action": "flaming_sphere_roll",
                    },
                ),
                _call(
                    "resolve_srd_flaming_sphere_ignition",
                    {
                        "area_id": "{area_id}",
                        "destination": "{_flaming_roll_final}",
                    },
                ),
                _set("state.persistent_spell_areas.{area_id}.origin", "_flaming_roll_final"),
                _call("refresh_srd_flaming_sphere_darkness", {}),
                _set(
                    "state.persistent_spell_areas.{area_id}.ground_point",
                    [
                        "_flaming_roll_final[0]",
                        "_flaming_roll_final[1]",
                        "_flaming_roll_final[2] - 2.5",
                    ],
                ),
                _set("state.persistent_spell_areas.{area_id}.roll_turn", "combat.turn_sequence"),
                _set(
                    "state.persistent_spell_areas.{area_id}.roll_sequence",
                    "_flaming_source.roll_sequence + 1",
                ),
                _set(
                    "state.persistent_spell_areas.{area_id}.last_roll",
                    {
                        "from": "_flaming_roll_origin",
                        "requested": "_flaming_roll_destination",
                        "to": "_flaming_roll_final",
                        "target": "_flaming_roll_target",
                        "turn": "combat.turn_sequence",
                        "tick": "state.lasting_tick",
                    },
                ),
                _when(
                    "_flaming_roll_target != null",
                    [
                        _set(
                            "state.persistent_spell_areas.{area_id}.roll_stopped_turn",
                            "combat.turn_sequence",
                        ),
                        _set(
                            "state.persistent_spell_areas.{area_id}.ram_target",
                            "_flaming_roll_target",
                        ),
                        _set("state.persistent_spell_areas.{area_id}.ram_in_progress", True),
                        _call(
                            "resolve_srd_flaming_sphere_event",
                            {
                                "target_state": "{_flaming_roll_target}",
                                "area_id": "{area_id}",
                                "trigger": "ram",
                                "witness_state_ids": "{witness_state_ids}",
                            },
                        ),
                        _set("state.persistent_spell_areas.{area_id}.ram_in_progress", False),
                    ],
                ),
            ],
            public=True,
        ),
        "combat",
        "Roll Flaming Sphere",
        [("Destination", "_flaming_roll_final")],
    )


def _map_bounds(center):
    return [
        _set("_flaming_bounds", "state.spatial_scene.map.bounds"),
        _require(
            " and ".join(
                f"{center}[{i}] - 2.5 >= _flaming_bounds.min[{i}] and "
                f"{center}[{i}] + 2.5 <= _flaming_bounds.max[{i}]"
                for i in range(3)
            ),
            "The whole actual fire Sphere must fit the admitted map.",
        ),
    ]


def tools(existing):
    return {
        "qualify_srd_flaming_sphere_source": _source_tool(),
        "qualify_srd_flaming_sphere_ground": _ground_tool(),
        "resolve_srd_flaming_sphere_box_segment": _body_tool(),
        "qualify_srd_flaming_sphere_occupant": _occupant_tool(),
        "qualify_srd_flaming_sphere_space": _space_tool(),
        "qualify_srd_flaming_sphere_route_solids": _solids_tool(),
        "qualify_srd_flaming_sphere_target": _target_tool(existing),
        "resolve_srd_flaming_sphere_event": _event_tool(existing),
        "roll_srd_flaming_sphere": _roll_tool(),
        "resolve_srd_flaming_sphere_light": _light_tool(),
        "ignite_srd_flaming_sphere_object": _ignite_tool(),
        "resolve_srd_flaming_sphere_ignition": _ignition_tool(),
        "dispel_srd_flaming_sphere_darkness_record": _darkness_record_tool(),
        "refresh_srd_flaming_sphere_darkness": _darkness_tool(),
    }


def _darkness_record_tool():
    """Darkness retires the whole low-slot fire source when its real light area overlaps."""
    return _environment_tool(
        "Settle exact light-Sphere overlap against an actual uncovered Darkness record.",
        [_param("area_id"), _param("record", "object", from_state="state.turn_effects")],
        [
            _set("_flaming_dark_source", "state.persistent_spell_areas[area_id]"),
            _require(
                "record in state.turn_effects and record.kind == 'illumination_spell' "
                "and record.spell == 'darkness' and "
                "_flaming_dark_source.mode == 'flaming_sphere'",
                "Use only genuine saved Flaming Sphere and Darkness sources.",
            ),
            _call(
                "qualify_srd_flaming_sphere_source",
                {
                    "caster_state": "{_flaming_dark_source.caster}",
                    "area_id": "{area_id}",
                },
                ("_flaming_source_current",),
            ),
            _call(
                "check_srd_illumination_record",
                {
                    "record": "{record}",
                    "caster_state": "{record.caster}",
                },
                (
                    "_illumination_active",
                    "_illumination_covered",
                    "_illumination_origin",
                    "_illumination_plane",
                ),
            ),
            _when(
                "_flaming_source_current == true and _flaming_dark_source.spell_level <= 2 "
                "and _illumination_active == true and _illumination_covered != true and "
                "_illumination_plane == _flaming_dark_source.plane",
                [
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{_flaming_dark_source.origin}",
                            "end": "{_illumination_origin}",
                        },
                        ("_scene_distance",),
                    ),
                    _when(
                        "_scene_distance <= 40 + record.dark_radius",
                        [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_flaming_dark_source.caster}",
                                    "target_state": "{_flaming_dark_source.caster}",
                                    "lasting_state": "state",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _darkness_tool():
    return _environment_tool(
        "Refresh exact low-slot Flaming Sphere retirement through the existing Darkness owner.",
        [],
        _areas(
            [
                _when(
                    "_environment_area.mode == 'flaming_sphere' and "
                    "_environment_area.spell_level <= 2 and state.turn_effects != null",
                    [
                        _set("_flaming_dark_records", "state.turn_effects"),
                        _each(
                            "_flaming_dark_records",
                            "_flaming_dark_record",
                            [
                                _when(
                                    "_flaming_dark_record.kind == 'illumination_spell' and "
                                    "_flaming_dark_record.spell == 'darkness'",
                                    [
                                        _call(
                                            "dispel_srd_flaming_sphere_darkness_record",
                                            {
                                                "area_id": "{_environment_id}",
                                                "record": "{_flaming_dark_record}",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ]
        ),
    )


def _ignite_tool():
    """Relight the ordinary-flame state actually consumed by the existing rain owner."""
    return _environment_tool(
        "Ignite a genuinely touched unworn/uncarried flammable object using its saved flame facts.",
        [_param("area_id"), _param("object_id"), _array("start"), _array("end")],
        [
            _set("_flaming_ignition_source", "state.persistent_spell_areas[area_id]"),
            _call(
                "qualify_srd_flaming_sphere_source",
                {
                    "caster_state": "{_flaming_ignition_source.caster}",
                    "area_id": "{area_id}",
                },
                ("_flaming_source_current",),
            ),
            _when(
                "_flaming_source_current == true",
                [
                    *_lookup_object(),
                    _when(
                        "_object_scene.metadata.is_object == true and "
                        "_object_scene.metadata.flammable == true and "
                        "_object_scene.metadata.worn_by == null and "
                        "_object_scene.metadata.carried_by == null and "
                        "_object_scene.metadata.plane == _flaming_ignition_source.plane",
                        [
                            _call(
                                "resolve_srd_flaming_sphere_box_segment",
                                {
                                    "area_id": "{area_id}",
                                    "subject": "{object_id}",
                                    "subject_kind": "entity",
                                    "start": "{start}",
                                    "end": "{end}",
                                },
                                ("_flaming_body_touched",),
                            ),
                            _when(
                                "_flaming_body_touched == true",
                                [
                                    _require(
                                        "_object_scene.metadata.srd_ordinary_flame.kind == "
                                        "'ordinary_flame' and "
                                        "_object_scene.metadata.srd_ordinary_flame.exposed != null",
                                        "Admit the ordinary flame's actual "
                                        "rain-exposure fact before ignition.",
                                    ),
                                    _set("_flaming_ignition_patch", {"burning": True}),
                                    _set(
                                        "_flaming_ordinary_flame",
                                        "merge("
                                        "_object_scene.metadata.srd_ordinary_flame, "
                                        "_flaming_ignition_patch)",
                                    ),
                                    *_metadata_patch(
                                        {"srd_ordinary_flame": "_flaming_ordinary_flame"}
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _ignition_tool():
    return _environment_tool(
        "Observe only actual retained-object fire contacts on this real Sphere segment.",
        [_param("area_id"), _array("destination")],
        [
            _set("_flaming_ignite_origin", "state.persistent_spell_areas[area_id].origin"),
            _set("_flaming_ignite_entries", "state.spatial_scene.entities"),
            _each(
                "_flaming_ignite_entries",
                "_flaming_ignite_entry",
                [
                    _when(
                        "_flaming_ignite_entry.active == true and "
                        "_flaming_ignite_entry.metadata.is_object == true and "
                        "_flaming_ignite_entry.metadata.flammable == true",
                        [
                            _call(
                                "ignite_srd_flaming_sphere_object",
                                {
                                    "area_id": "{area_id}",
                                    "object_id": "{_flaming_ignite_entry.id}",
                                    "start": "{_flaming_ignite_origin}",
                                    "end": "{destination}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _light_tool():
    """Read printed light only from the actual current same-plane fire body."""
    return _environment_tool(
        "Qualify printed Bright20/Dim40 from an actual retained Flaming Sphere.",
        [_array("point"), _param("plane")],
        [
            _set("_flaming_magic_bright", False),
            _set("_flaming_magic_dim", False),
            _when(
                "state.has_flaming_sphere == true",
                [_call("refresh_srd_flaming_sphere_darkness", {})]
                + _areas(
                    [
                        _when(
                            "_environment_area.mode == 'flaming_sphere' and "
                            "_environment_area.plane == plane",
                            [
                                _call(
                                    "qualify_srd_flaming_sphere_source",
                                    {
                                        "caster_state": "{_environment_area.caster}",
                                        "area_id": "{_environment_id}",
                                    },
                                    ("_flaming_source_current",),
                                ),
                                _when(
                                    "_flaming_source_current == true",
                                    [
                                        _call(
                                            "measure_srd_information_distance",
                                            {
                                                "start": "{_environment_area.origin}",
                                                "end": "{point}",
                                            },
                                            ("_scene_distance",),
                                        ),
                                        _when(
                                            "_scene_distance <= 20",
                                            [
                                                _set("_flaming_magic_bright", True),
                                            ],
                                            [
                                                _when(
                                                    "_scene_distance <= 40",
                                                    [
                                                        _set("_flaming_magic_dim", True),
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]
                ),
            ),
        ],
    )


def bind_sources(changed):
    """Bind actual installation and target-end damage without adding appearance or entry damage."""
    cast = changed["cast_spell_environment"]
    install = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "refresh_srd_environment_scene"
    )
    cast["effects"][install:install] = [
        _when(
            "_environment_profile.environment_mode == 'flaming_sphere'",
            [
                _require("save_dc >= 1", "Use the actual caster's Flaming Sphere save DC."),
                _call(
                    "qualify_srd_flaming_sphere_ground",
                    {
                        "point": "{_environment_cast_origin}",
                        "plane": "{_environment_plane}",
                    },
                    ("_environment_ground_id",),
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.ground_id",
                    "_environment_ground_id",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.ground_point",
                    "_environment_cast_origin",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.origin",
                    [
                        "_environment_cast_origin[0]",
                        "_environment_cast_origin[1]",
                        "_environment_cast_origin[2] + 2.5",
                    ],
                ),
                _set(
                    "_flaming_cast_center",
                    "state.persistent_spell_areas[_environment_cast_id].origin",
                ),
                *_map_bounds("_flaming_cast_center"),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.source_sha256",
                    repr(SOURCE_SHA),
                ),
                _set("state.persistent_spell_areas.{_environment_cast_id}.roll_sequence", 0),
                _set("state.persistent_spell_areas.{_environment_cast_id}.roll_stopped_turn", -1),
                _set("state.persistent_spell_areas.{_environment_cast_id}.event_turn", -1),
                _set("state.persistent_spell_areas.{_environment_cast_id}.event_clock", "'none'"),
                _set("state.persistent_spell_areas.{_environment_cast_id}.event_target_ids", []),
                _call("qualify_srd_flaming_sphere_space", {"area_id": "{_environment_cast_id}"}),
                _call("refresh_srd_flaming_sphere_darkness", {}),
                _call(
                    "resolve_srd_flaming_sphere_ignition",
                    {
                        "area_id": "{_environment_cast_id}",
                        "destination": "{_flaming_cast_center}",
                    },
                ),
            ],
        ),
    ]
    ending = changed["resolve_srd_environment_hazard_end_turn"]
    loop = next(e["for_each"] for e in ending["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'flaming_sphere' and combat.turn_active == true and "
            "combat.active_actor == _target_state_id",
            [
                _call(
                    "resolve_srd_flaming_sphere_event",
                    {
                        "target_state": "{_target_state_id}",
                        "area_id": "{_environment_id}",
                        "trigger": "end",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                ),
            ],
        )
    )
    index = changed["refresh_srd_environment_mode_indices"]
    index["effects"].insert(0, _set("state.has_flaming_sphere", False))
    index_loop = next(e["for_each"] for e in index["effects"] if "for_each" in e)
    index_loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'flaming_sphere'",
            [_set("state.has_flaming_sphere", True)],
        )
    )


def bind_runtime_consumers(text):
    """Bind source light into final actual owners, preserving every prior consumer body."""
    import yaml

    if __package__:
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_senses import _replace_tools

    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    assert "resolve_srd_flaming_sphere_light" in runtime["tools"]
    changed = {}
    light = deepcopy(runtime["tools"]["resolve_srd_information_light"])
    if "resolve_srd_flaming_sphere_light" not in str(light["effects"]):
        before = next(
            i
            for i, e in enumerate(light["effects"])
            if e.get("condition") == "_light_magic_dark == true"
        )
        light["effects"][before:before] = [
            _when(
                "lasting_state.has_flaming_sphere == true",
                [
                    _call(
                        "resolve_srd_flaming_sphere_light",
                        {
                            "point": "{point}",
                            "plane": "{plane}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ("_flaming_magic_bright", "_flaming_magic_dim"),
                    ),
                    _when("_flaming_magic_bright == true", [_set("_light_magic_bright", True)]),
                    _when("_flaming_magic_dim == true", [_set("_light_magic_dim", True)]),
                ],
            )
        ]
        changed["resolve_srd_information_light"] = light
    overlap = deepcopy(runtime["tools"]["resolve_srd_illumination_overlaps"])
    if "refresh_srd_flaming_sphere_darkness" not in str(overlap["effects"]):
        overlap["effects"].append(
            _when(
                "lasting_state.has_flaming_sphere == true",
                [
                    _call(
                        "refresh_srd_flaming_sphere_darkness",
                        {"lasting_state": "{_lasting_state_id}"},
                    ),
                ],
            )
        )
        changed["resolve_srd_illumination_overlaps"] = overlap
    return _replace_tools(text, changed)
