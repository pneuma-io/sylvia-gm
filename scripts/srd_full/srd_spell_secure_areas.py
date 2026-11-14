"""Source-qualified secure areas use admitted bodies, fixed volumes and actual clocks."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import _each, _push
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_spell_magic_zones import _when
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _public, _replace_tools
else:
    from srd_control_behaviors import _each, _push
    from srd_scene_relations import _array
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_spell_magic_zones import _when
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _public, _replace_tools


PROFILES = {
    "magic_circle": {
        "family": "spell_secure_area",
        "base_level": 3,
        "casting_time": "1_minute",
        "range_kind": "distance",
        "range_feet": 10,
        "duration_rounds": 600,
        "concentration": False,
        "secure_mode": "circle",
    },
    "forbiddance": {
        "family": "spell_secure_area",
        "base_level": 6,
        "casting_time": "10_minutes",
        "range_kind": "touch",
        "range_feet": 0,
        "duration_rounds": 14400,
        "concentration": False,
        "secure_mode": "forbiddance",
    },
    "private_sanctum": {
        "family": "spell_secure_area",
        "base_level": 4,
        "casting_time": "10_minutes",
        "range_kind": "distance",
        "range_feet": 120,
        "duration_rounds": 14400,
        "concentration": False,
        "secure_mode": "sanctum",
    },
}
TYPE_CHOICES = {"celestial", "elemental", "fey", "fiend", "undead"}
SECURITY_CHOICES = {
    "sound",
    "vision",
    "divination_sensors",
    "divination_targets",
    "teleport",
    "planar",
}
PUBLIC_TOOLS = {"move_srd_creature_speaking_password"}


def _schema():
    properties = {
        "base_level": {"type": "integer", "minimum": 3, "maximum": 6},
        "casting_time": {"type": "string", "enum": ["1_minute", "10_minutes"]},
        "range_kind": {"type": "string", "enum": ["touch", "distance"]},
        "range_feet": {"type": "integer", "minimum": 0},
        "duration_rounds": {"type": "integer", "minimum": 600},
        "concentration": {"type": "boolean"},
        "secure_mode": {"type": "string", "enum": ["circle", "forbiddance", "sanctum"]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    """Admit only the exact printed secure-area profiles, independent of casting choices."""
    for spell, definition in definitions.items():
        if definition.get("family") == "spell_secure_area" and definition != PROFILES.get(spell):
            raise SystemExit(f"Secure-area profile differs from its printed source: {spell}")


def _read_source():
    return [
        _require(
            "state.spell_secure_area_sources != null and "
            "source_id in state.spell_secure_area_sources",
            "Use the actual installed secure-area source.",
        ),
        _set("_secure_source", "state.spell_secure_area_sources[source_id]"),
        _set("_secure_volume", "_secure_source.volume"),
    ]


def _point_tool():
    return _tool(
        "Qualify a real private point against the exact source Cube or Cylinder and plane.",
        [_parameter("source_id"), _array("point"), _parameter("plane")],
        [
            *_read_source(),
            _set("_secure_area_point_inside", False),
            _when(
                "plane == _secure_source.plane",
                [
                    _when(
                        "_secure_volume.kind == 'box'",
                        [
                            _set(
                                "_secure_area_point_inside",
                                "point[0] >= _secure_volume.min[0] and "
                                "point[0] <= _secure_volume.max[0] and "
                                "point[1] >= _secure_volume.min[1] and "
                                "point[1] <= _secure_volume.max[1] and "
                                "point[2] >= _secure_volume.min[2] and "
                                "point[2] <= _secure_volume.max[2]",
                            )
                        ],
                        [
                            _set("_secure_dx", "point[0] - _secure_volume.center[0]"),
                            _set("_secure_dy", "point[1] - _secure_volume.center[1]"),
                            _set(
                                "_secure_area_point_inside",
                                "_secure_dx * _secure_dx + _secure_dy * _secure_dy <= "
                                "_secure_volume.radius * _secure_volume.radius and "
                                "point[2] >= _secure_volume.center[2] and "
                                "point[2] <= _secure_volume.center[2] + _secure_volume.height",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _subject_tool():
    """Body membership preserves actual box/circular footprints and vertical occupancy."""
    return _tool(
        "Qualify the actual admitted subject body at its current or privately planned position.",
        [
            _parameter("source_id"),
            _optional("subject"),
            {**_array("position"), "required": False, "default": None},
            _optional("plane", default=None),
        ],
        [
            *_read_source(),
            _call(
                "normalize_srd_barrier_scene_subject",
                {"subject": "{subject}"},
                ("_barrier_raw_scene_subject",),
            ),
            *_scene("_barrier_raw_scene_subject", "_secure_subject"),
            _set("_secure_position", "_secure_subject.position"),
            _when("position != null", [_set("_secure_position", "position")]),
            _set("_secure_subject_plane", "_secure_subject.metadata.plane"),
            _when("plane != null", [_set("_secure_subject_plane", "plane")]),
            _set("_secure_area_subject_inside", False),
            _when(
                "_secure_subject_plane == _secure_source.plane",
                [
                    _set("_secure_half_x", 0),
                    _set("_secure_half_y", 0),
                    _when(
                        "_secure_subject.footprint.kind == 'box'",
                        [
                            _set("_secure_half_x", "_secure_subject.footprint.half_extents[0]"),
                            _set("_secure_half_y", "_secure_subject.footprint.half_extents[1]"),
                        ],
                        [
                            _set("_secure_half_x", "_secure_subject.footprint.radius"),
                            _set("_secure_half_y", "_secure_subject.footprint.radius"),
                        ],
                    ),
                    _when(
                        "_secure_volume.kind == 'box'",
                        [
                            _set(
                                "_secure_area_subject_inside",
                                "_secure_position[0] + _secure_half_x >= _secure_volume.min[0] "
                                "and _secure_position[0] - _secure_half_x <= _secure_volume.max[0] "
                                "and _secure_position[1] + _secure_half_y >= _secure_volume.min[1] "
                                "and _secure_position[1] - _secure_half_y <= _secure_volume.max[1] "
                                "and _secure_position[2] + _secure_subject.height >= "
                                "_secure_volume.min[2] and "
                                "_secure_position[2] <= _secure_volume.max[2]",
                            ),
                            _when(
                                "_secure_subject.footprint.kind == 'circle'",
                                [
                                    _set(
                                        "_secure_dx",
                                        "_secure_position[0] - min("
                                        "_secure_volume.max[0], max(_secure_volume.min[0], "
                                        "_secure_position[0]))",
                                    ),
                                    _set(
                                        "_secure_dy",
                                        "_secure_position[1] - min("
                                        "_secure_volume.max[1], max(_secure_volume.min[1], "
                                        "_secure_position[1]))",
                                    ),
                                    _set(
                                        "_secure_area_subject_inside",
                                        "_secure_area_subject_inside == true and "
                                        "_secure_dx * _secure_dx + _secure_dy * _secure_dy <= "
                                        "_secure_half_x * _secure_half_x",
                                    ),
                                ],
                            ),
                        ],
                        [
                            _set("_secure_radius", "_secure_volume.radius"),
                            _when(
                                "_secure_subject.footprint.kind == 'box'",
                                [
                                    _set(
                                        "_secure_dx",
                                        "_secure_volume.center[0] - min("
                                        "_secure_position[0] + _secure_half_x, max("
                                        "_secure_position[0] - _secure_half_x, "
                                        "_secure_volume.center[0]))",
                                    ),
                                    _set(
                                        "_secure_dy",
                                        "_secure_volume.center[1] - min("
                                        "_secure_position[1] + _secure_half_y, max("
                                        "_secure_position[1] - _secure_half_y, "
                                        "_secure_volume.center[1]))",
                                    ),
                                ],
                                [
                                    _set(
                                        "_secure_dx",
                                        "_secure_position[0] - _secure_volume.center[0]",
                                    ),
                                    _set(
                                        "_secure_dy",
                                        "_secure_position[1] - _secure_volume.center[1]",
                                    ),
                                    _set("_secure_radius", "_secure_radius + _secure_half_x"),
                                ],
                            ),
                            _set(
                                "_secure_area_subject_inside",
                                "_secure_dx * _secure_dx + _secure_dy * _secure_dy <= "
                                "_secure_radius * _secure_radius and "
                                "_secure_position[2] + _secure_subject.height >= "
                                "_secure_volume.center[2] and "
                                "_secure_position[2] <= _secure_volume.center[2] + "
                                "_secure_volume.height",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _segment_tool():
    """A real straight ray tests the fixed source boundary without mutating the scene."""
    box = [
        _each(
            "_secure_axes",
            "_secure_axis",
            [
                _set("_secure_delta", "end[_secure_axis] - start[_secure_axis]"),
                _when(
                    "_secure_delta == 0",
                    [
                        _when(
                            "start[_secure_axis] < _secure_volume.min[_secure_axis] or "
                            "start[_secure_axis] > _secure_volume.max[_secure_axis]",
                            [_set("_secure_area_segment_intersects", False)],
                        )
                    ],
                    [
                        _set(
                            "_secure_t1",
                            "(_secure_volume.min[_secure_axis] - "
                            "start[_secure_axis]) * _secure_delta ** -1",
                        ),
                        _set(
                            "_secure_t2",
                            "(_secure_volume.max[_secure_axis] - "
                            "start[_secure_axis]) * _secure_delta ** -1",
                        ),
                        _set("_secure_near", "max(_secure_near, min(_secure_t1, _secure_t2))"),
                        _set("_secure_far", "min(_secure_far, max(_secure_t1, _secure_t2))"),
                    ],
                ),
            ],
        ),
    ]
    cylinder = [
        _set("_secure_delta", "end[2] - start[2]"),
        _when(
            "_secure_delta == 0",
            [
                _when(
                    "start[2] < _secure_volume.center[2] or "
                    "start[2] > _secure_volume.center[2] + _secure_volume.height",
                    [_set("_secure_area_segment_intersects", False)],
                )
            ],
            [
                _set("_secure_t1", "(_secure_volume.center[2] - start[2]) * _secure_delta ** -1"),
                _set(
                    "_secure_t2",
                    "(_secure_volume.center[2] + _secure_volume.height - start[2]) "
                    "* _secure_delta ** -1",
                ),
                _set("_secure_near", "max(0, min(_secure_t1, _secure_t2))"),
                _set("_secure_far", "min(1, max(_secure_t1, _secure_t2))"),
            ],
        ),
        _set("_secure_dx", "end[0] - start[0]"),
        _set("_secure_dy", "end[1] - start[1]"),
        _set("_secure_ax", "start[0] - _secure_volume.center[0]"),
        _set("_secure_ay", "start[1] - _secure_volume.center[1]"),
        _set("_secure_length_squared", "_secure_dx * _secure_dx + _secure_dy * _secure_dy"),
        _set("_secure_closest_t", "_secure_near"),
        _when(
            "_secure_length_squared > 0 and _secure_near <= _secure_far",
            [
                _set("_secure_dot", "_secure_ax * _secure_dx + _secure_ay * _secure_dy"),
                _set("_secure_projection", "(0 - _secure_dot) * _secure_length_squared ** -1"),
                _set(
                    "_secure_closest_t",
                    "min(_secure_far, max(_secure_near, _secure_projection))",
                ),
            ],
        ),
        _set("_secure_ax", "_secure_ax + _secure_closest_t * _secure_dx"),
        _set("_secure_ay", "_secure_ay + _secure_closest_t * _secure_dy"),
        _when(
            "_secure_ax * _secure_ax + _secure_ay * _secure_ay > "
            "_secure_volume.radius * _secure_volume.radius",
            [_set("_secure_area_segment_intersects", False)],
        ),
    ]
    return _tool(
        "Test the real private point-to-point segment against an admitted secure volume.",
        [_parameter("source_id"), _array("start"), _array("end"), _parameter("plane")],
        [
            *_read_source(),
            _set("_secure_area_segment_intersects", False),
            _when(
                "plane == _secure_source.plane",
                [
                    _set("_secure_area_segment_intersects", True),
                    _set("_secure_near", 0),
                    _set("_secure_far", 1),
                    _set("_secure_axes", [0, 1, 2]),
                    _when("_secure_volume.kind == 'box'", box, cylinder),
                    _when(
                        "_secure_near > _secure_far",
                        [
                            _set("_secure_area_segment_intersects", False),
                        ],
                    ),
                ],
            ),
        ],
    )


def _source_tools():
    """Nonconcentration wards keep their real clock through caster death and suppression."""
    retire = _tool(
        "Retire only the named source; unrelated castings and actor concentration remain intact.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            *_read_source(),
            _when(
                "_secure_source.installed == true",
                [
                    _set("state.spell_secure_area_sources.{source_id}.installed", False),
                    _set(
                        "state.spell_secure_area_sources.{source_id}.ended_tick",
                        "state.lasting_tick",
                    ),
                    _set("state.spell_secure_area_sources.{source_id}.end_reason", "reason"),
                ],
            ),
        ],
    )
    check = _tool(
        "Qualify actual lifetime independently from temporary source suppression.",
        [_parameter("source_id")],
        [
            *_read_source(),
            _set(
                "_secure_area_lifetime_active",
                "_secure_source.installed == true and (_secure_source.permanent == true or "
                "state.lasting_tick < _secure_source.expires_tick)",
            ),
            _set(
                "_secure_area_effect_active",
                "_secure_area_lifetime_active == true and _secure_source.suppressed != true",
            ),
            _when(
                "_secure_area_lifetime_active != true and _secure_source.installed == true",
                [
                    _call(
                        "end_srd_secure_area_source",
                        {"source_id": "{source_id}", "reason": "lifetime"},
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Prune expired source indexes while retained suppressed sources can still restore.",
        [],
        [
            _set("_secure_active_ids", []),
            _set("_secure_has_circle", False),
            _set("_secure_existing_ids", "state.spell_secure_area_source_ids"),
            _each(
                "_secure_existing_ids",
                "_secure_id",
                [
                    _call(
                        "check_srd_secure_area_source",
                        {"source_id": "{_secure_id}"},
                        (
                            "_secure_area_lifetime_active",
                            "_secure_source",
                        ),
                    ),
                    _when(
                        "_secure_area_lifetime_active == true",
                        [
                            _push("_secure_active_ids", "_secure_id"),
                            _when(
                                "_secure_source.mode == 'circle'",
                                [_set("_secure_has_circle", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.spell_secure_area_source_ids", "_secure_active_ids"),
            _set("state.has_spell_secure_areas", "len(_secure_active_ids) > 0"),
            _set("state.has_magic_circle_sources", "_secure_has_circle"),
        ],
    )
    return {
        "end_srd_secure_area_source": retire,
        "check_srd_secure_area_source": check,
        "refresh_srd_secure_area_sources": refresh,
    }


def _sources(effects, *, lifetime=False):
    return [
        _when(
            "state.has_spell_secure_areas == true",
            [
                _set("_secure_ids", "state.spell_secure_area_source_ids"),
                _each(
                    "_secure_ids",
                    "_secure_id",
                    [
                        _call(
                            "check_srd_secure_area_source",
                            {"source_id": "{_secure_id}"},
                            (
                                "_secure_area_effect_active",
                                "_secure_area_lifetime_active",
                                "_secure_source",
                            ),
                        ),
                        _when(
                            "_secure_area_lifetime_active == true"
                            if lifetime
                            else "_secure_area_effect_active == true",
                            effects,
                        ),
                    ],
                ),
            ],
        )
    ]


def _cast_parameters():
    return [
        _entity("caster_state"),
        _parameter("spell"),
        _parameter("slot_level", "integer"),
        _parameter("save_dc", "integer"),
        _array("origin"),
        _optional("chosen_types", "array", [], items={"type": "string"}, max_items=6),
        _optional("security", "array", [], items={"type": "string"}, max_items=6),
        _optional("reverse", "boolean", False),
        _optional("side", "number", 100),
        _optional("floor_width", "number", 200),
        _optional("floor_length", "number", 200),
        _optional("damage_type", default="radiant"),
        _optional("password", default=""),
        _optional("caster_can_see_origin", "boolean", False),
        _optional("caster_can_touch_origin", "boolean", False),
    ]


def _daily_history_effects():
    """Count exact same-volume source casts; array equality is not a runtime expression."""
    return [
        _set("_secure_history_axes", [0, 1, 2]),
        _set("_secure_history_values", "state.secure_area_daily_history"),
        _each(
            "_secure_history_values",
            "_secure_history",
            [
                _set(
                    "_secure_history_matches",
                    "_secure_history.spell_id == spell and _secure_history.caster_state_id == _cas"
                    "ter_state_id and _secure_history.plane == _secure_cast_plane",
                ),
                _when(
                    "_secure_history_matches == true",
                    [
                        _each(
                            "_secure_history_axes",
                            "_secure_history_axis",
                            [
                                _when(
                                    "_secure_history.volume.min[_secure_history_axis] != _secure_v"
                                    "olume.min[_secure_history_axis] or _secure_history.volume.max"
                                    "[_secure_history_axis] != _secure_volume.max[_secure_history_"
                                    "axis]",
                                    [_set("_secure_history_matches", False)],
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_secure_history_matches == true",
                    [
                        _require(
                            "state.spell_secure_area_sources != null and _secure_history.source_id"
                            " in state.spell_secure_area_sources",
                            "Daily permanence needs the actual prior secure-area source.",
                        ),
                        _set(
                            "_secure_daily_source",
                            "state.spell_secure_area_sources[_secure_history.source_id]",
                        ),
                        _require(
                            "_secure_daily_source.spell_id == spell and _secure_daily_source.caste"
                            "r_state_id == _caster_state_id and _secure_daily_source.plane == _sec"
                            "ure_cast_plane and _secure_daily_source.volume.kind == _secure_volume"
                            ".kind and _secure_daily_source.daily_count == _secure_history.days an"
                            "d _secure_daily_source.daily_day == _secure_history.last_day",
                            "The daily history must match its real saved source.",
                        ),
                        _each(
                            "_secure_history_axes",
                            "_secure_history_axis",
                            [
                                _require(
                                    "_secure_daily_source.volume.min[_secure_history_axis] == _sec"
                                    "ure_volume.min[_secure_history_axis] and _secure_daily_source"
                                    ".volume.max[_secure_history_axis] == _secure_volume.max[_secu"
                                    "re_history_axis]",
                                    "The daily volume must match its real prior source.",
                                ),
                            ],
                        ),
                        _set("_secure_history_id", "_secure_history.id"),
                        _when(
                            "_secure_daily_source.daily_day == _secure_cast_day",
                            [_set("_secure_daily_count", "_secure_daily_source.daily_count")],
                            [
                                _when(
                                    "_secure_daily_source.daily_day == _secure_cast_day - 1",
                                    [
                                        _set(
                                            "_secure_daily_count",
                                            "_secure_daily_source.daily_count + 1",
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _prepare_tool():
    """Derive placement and daily history from actual cast facts before permanent consumption."""
    choices = sorted(TYPE_CHOICES)
    return _tool(
        "Validate the source volume and derive the real final-casting material obligation.",
        _cast_parameters(),
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_secure_profile",
            },
            _require(
                "_secure_profile.family == 'spell_secure_area' and slot_level >= _secure_profile.b"
                "ase_level and slot_level <= 9",
                "Use an actual reviewed secure-area spell and slot.",
            ),
            *_scene("_caster_state_id", "_secure_caster_body"),
            _set("_secure_cast_plane", "_secure_caster_body.metadata.plane"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_secure_caster_body.position}", "end": "{origin}"},
                ("_scene_distance",),
            ),
            _require(
                "(spell == 'forbiddance' and caster_can_touch_origin == true and _scene_distance <"
                "= 5) or (spell != 'forbiddance' and _scene_distance <= _secure_profile.range_feet"
                ")",
                "Use the actual touch point or source range of this secure area.",
            ),
            _require(
                "spell != 'magic_circle' or caster_can_see_origin == true",
                "Magic Circle needs an actual visible ground point.",
            ),
            _set(
                "_secure_volume",
                {
                    "kind": "'box'",
                    "min": "origin",
                    "max": [f"origin[{i}] + side" for i in range(3)],
                },
            ),
            _when(
                "spell == 'private_sanctum'",
                [
                    _require(
                        "side >= 5 and side <= 100 + max(0, slot_level - 4) * 100",
                        "Choose the printed Sanctum Cube size for the actual slot.",
                    ),
                    _set("_secure_security_unique", []),
                    _set("_secure_security_choices", sorted(SECURITY_CHOICES)),
                    _each(
                        "security",
                        "_secure_choice",
                        [
                            _require(
                                "_secure_choice in _secure_security_choices",
                                "Select only a printed Sanctum security property.",
                            ),
                            _require(
                                "_secure_choice not in _secure_security_unique",
                                "Each Sanctum security property is selected once.",
                            ),
                            _push("_secure_security_unique", "_secure_choice"),
                        ],
                    ),
                ],
                [
                    _require(
                        "len(chosen_types) >= 1", "Select actual printed affected creature types."
                    ),
                    _set("_secure_types_unique", []),
                    _set("_secure_type_choices", choices),
                    _when(
                        "spell == 'forbiddance'", [_push("_secure_type_choices", "'aberration'")]
                    ),
                    _each(
                        "chosen_types",
                        "_secure_choice",
                        [
                            _require(
                                "_secure_choice in _secure_type_choices and _secure_choice not in "
                                "_secure_types_unique",
                                "Select distinct printed affected creature types.",
                            ),
                            _push("_secure_types_unique", "_secure_choice"),
                        ],
                    ),
                    _call(
                        "qualify_srd_environment_ground",
                        {"point": "{origin}", "plane": "{_secure_cast_plane}"},
                        ("_environment_ground_id",),
                    ),
                    _when(
                        "spell == 'magic_circle'",
                        [
                            _set(
                                "_secure_volume",
                                {
                                    "kind": "'cylinder'",
                                    "center": "origin",
                                    "radius": 10,
                                    "height": 20,
                                },
                            )
                        ],
                        [
                            _require(
                                "floor_width > 0 and floor_length > 0 and floor_width * floor_leng"
                                "th <= 40000",
                                "Forbiddance protects at most 40000 square feet of actual floor.",
                            ),
                            _require(
                                "damage_type == 'radiant' or damage_type == 'necrotic'",
                                "Choose actual Forbiddance Radiant or Necrotic damage.",
                            ),
                            _set("_secure_floor", None),
                            _set("_secure_floor_geometry", "state.spatial_scene.geometry"),
                            _each(
                                "_secure_floor_geometry",
                                "_secure_ground",
                                [
                                    _when(
                                        "_secure_ground.id == _environment_ground_id",
                                        [_set("_secure_floor", "_secure_ground")],
                                    )
                                ],
                            ),
                            _require(
                                "_secure_floor != null and _secure_floor.volume.kind == 'box' and "
                                "_secure_floor.volume.max[2] == origin[2]",
                                "Forbiddance needs its admitted actual flat floor surface.",
                            ),
                            _require(
                                "origin[0] >= _secure_floor.volume.min[0] and origin[0] + floor_wi"
                                "dth <= _secure_floor.volume.max[0] and origin[1] >= _secure_floor"
                                ".volume.min[1] and origin[1] + floor_length <= _secure_floor.volu"
                                "me.max[1]",
                                "The selected protected rectangle must remain on that actual floor"
                                ".",
                            ),
                            _set(
                                "_secure_volume",
                                {
                                    "kind": "'box'",
                                    "min": "origin",
                                    "max": [
                                        "origin[0] + floor_width",
                                        "origin[1] + floor_length",
                                        "origin[2] + 30",
                                    ],
                                },
                            ),
                            *_sources(
                                [
                                    _when(
                                        "_secure_source.mode == 'forbiddance' and _secure_source.p"
                                        "lane == _secure_cast_plane",
                                        [
                                            _require(
                                                "_secure_volume.max[0] <= _secure_source.volume.mi"
                                                "n[0] or _secure_volume.min[0] >= _secure_source.v"
                                                "olume.max[0] or _secure_volume.max[1] <= _secure_"
                                                "source.volume.min[1] or _secure_volume.min[1] >= "
                                                "_secure_source.volume.max[1] or _secure_volume.ma"
                                                "x[2] <= _secure_source.volume.min[2] or _secure_v"
                                                "olume.min[2] >= _secure_source.volume.max[2]",
                                                "Forbiddance cannot overlap another actual active "
                                                "Forbiddance.",
                                            )
                                        ],
                                    )
                                ],
                                lifetime=True,
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_secure_volume.kind == 'box'",
                [
                    _set("_secure_axes", [0, 1, 2]),
                    _each(
                        "_secure_axes",
                        "_secure_axis",
                        [
                            _require(
                                "_secure_volume.min[_secure_axis] >= state.spatial_scene.map.bound"
                                "s.min[_secure_axis] and _secure_volume.max[_secure_axis] <= state"
                                ".spatial_scene.map.bounds.max[_secure_axis]",
                                "The secure volume must fit the admitted map.",
                            )
                        ],
                    ),
                ],
                [
                    _require(
                        "origin[0] - 10 >= state.spatial_scene.map.bounds.min[0] and origin[0] + 1"
                        "0 <= state.spatial_scene.map.bounds.max[0] and origin[1] - 10 >= state.sp"
                        "atial_scene.map.bounds.min[1] and origin[1] + 10 <= state.spatial_scene.m"
                        "ap.bounds.max[1] and origin[2] >= state.spatial_scene.map.bounds.min[2] a"
                        "nd origin[2] + 20 <= state.spatial_scene.map.bounds.max[2]",
                        "The Magic Circle must fit the admitted map.",
                    )
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_secure_cast_day", "state.lasting_tick // 28800"),
            _set("_secure_daily_count", 1),
            _set("_secure_history_id", None),
            _set("_secure_materials_consumed", False),
            _set("_secure_permanent", False),
            _when(
                "spell != 'magic_circle' and state.secure_area_daily_history != null",
                _daily_history_effects(),
            ),
            _when(
                "spell == 'forbiddance' and _secure_daily_count >= 30",
                [_set("_secure_permanent", True), _set("_secure_materials_consumed", True)],
            ),
            _when(
                "spell == 'private_sanctum' and _secure_daily_count >= 365",
                [_set("_secure_permanent", True)],
            ),
        ],
    )


PREPARE_EXPORTS = (
    "_secure_profile",
    "_secure_cast_plane",
    "_secure_volume",
    "_secure_cast_day",
    "_secure_daily_count",
    "_secure_history_id",
    "_secure_permanent",
    "_secure_materials_consumed",
)


def _cast_tool():
    params = _cast_parameters() + [_optional("spell_cast_id", default="", model_hidden=True)]
    forward = {p["name"]: "{" + p["name"] + "}" for p in _cast_parameters()}
    forward["caster_state"] = "{_caster_state_id}"
    return _tool(
        "Install the exact selected volume and source lifetime after the ordinary paid casting own"
        "er.",
        params,
        [
            _call("prepare_srd_secure_area_cast", forward, PREPARE_EXPORTS),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            _when(
                "state.spell_secure_area_sources == null",
                [
                    _set("state.spell_secure_area_sources", {}),
                    _set("state.spell_secure_area_source_ids", []),
                    _set("state.secure_area_daily_history", []),
                ],
            ),
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _call(
                "format_srd_information_projection",
                {"message": "{_caster_state_id}:{spell}:{state.lasting_serial}"},
                ("_info_projection_message",),
            ),
            _set("_new_secure_source_id", "_info_projection_message"),
            _set("_secure_duration", "_secure_profile.duration_rounds"),
            _when(
                "spell == 'magic_circle'",
                [
                    _set("_secure_duration", "600 * (1 + slot_level - 3)"),
                ],
            ),
            _set(
                "state.spell_secure_area_sources.{_new_secure_source_id}",
                {
                    "installed": True,
                    "spell_id": "spell",
                    "spell_level": "slot_level",
                    "mode": "_secure_profile.secure_mode",
                    "caster_state_id": "_caster_state_id",
                    "source_caster_id": "_caster_state_id",
                    "cast_id": "spell_cast_id",
                    "ongoing_duration": "spell_cast_id != ''",
                    "dispel_ongoing": True,
                    "started_tick": "state.lasting_tick",
                    "serial": "state.lasting_serial",
                    "expires_tick": "state.lasting_tick + _secure_duration * 2",
                    "permanent": "_secure_permanent",
                    "suppressed": False,
                    "volume": "_secure_volume",
                    "plane": "_secure_cast_plane",
                    "chosen_types": "chosen_types",
                    "security": "security",
                    "reverse": "reverse",
                    "save_dc": "save_dc",
                    "damage_type": "damage_type",
                    "password": "password",
                    "daily_count": "_secure_daily_count",
                    "daily_day": "_secure_cast_day",
                    "entry_damage_turns": {},
                    "password_admissions": {},
                    "end_damage_turns": {},
                },
            ),
            _set("_secure_installed_ids", "state.spell_secure_area_source_ids"),
            _push("_secure_installed_ids", "_new_secure_source_id"),
            _set("state.spell_secure_area_source_ids", "_secure_installed_ids"),
            _set("state.has_spell_secure_areas", True),
            _when("spell == 'magic_circle'", [_set("state.has_magic_circle_sources", True)]),
            _when(
                "spell != 'magic_circle'",
                [
                    _set("_secure_histories", []),
                    _set("_secure_history_values", "state.secure_area_daily_history"),
                    _each(
                        "_secure_history_values",
                        "_secure_history",
                        [
                            _when(
                                "_secure_history.id != _secure_history_id",
                                [_push("_secure_histories", "_secure_history")],
                            ),
                        ],
                    ),
                    _push(
                        "_secure_histories",
                        {
                            "id": "_new_secure_source_id",
                            "spell_id": "spell",
                            "caster_state_id": "_caster_state_id",
                            "plane": "_secure_cast_plane",
                            "volume": "_secure_volume",
                            "days": "_secure_daily_count",
                            "last_day": "_secure_cast_day",
                            "source_id": "_new_secure_source_id",
                        },
                    ),
                    _set("state.secure_area_daily_history", "_secure_histories"),
                ],
            ),
            _set("_spell_summary_rows", [{"label": "Spell", "value": "spell"}]),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "Source", "value": "_new_secure_source_id"},
                    {"label": "Permanent", "value": "_secure_permanent"},
                ],
            ),
            _call("refresh_srd_secure_area_sources", {}),
        ],
    )


def _membership(subject, *, position=None, plane=None, store=None):
    params = {"source_id": "{_secure_id}", "subject": subject}
    if position is not None:
        params["position"] = position
    if plane is not None:
        params["plane"] = plane
    effects = [_call("qualify_srd_secure_area_subject", params, ("_secure_area_subject_inside",))]
    if store:
        effects.append(_set(store, "_secure_area_subject_inside"))
    return effects


def _creature_type_tool():
    return _tool(
        "Read the real saved creature type; objects never acquire a creature classification.",
        [_entity("actor_state")],
        [
            _require(
                "actor_state.creature_type != null",
                "Record the actual causing or moving creature's type first.",
            ),
            _set("_secure_creature_type", "actor_state.creature_type"),
        ],
    )


def _circle_tools():
    attack = _tool(
        "Derive Circle attack Disadvantage from the actual attacker, target and reversed source.",
        [_entity("actor_state"), _entity("target_state")],
        [
            _set("_secure_circle_attack_disadvantage", False),
            *_sources(
                [
                    _when(
                        "_secure_source.mode == 'circle'",
                        [
                            _call(
                                "read_srd_secure_area_creature_type",
                                {"actor_state": "{_actor_state_id}"},
                                ("_secure_creature_type",),
                            ),
                            _when(
                                "_secure_creature_type in _secure_source.chosen_types",
                                [
                                    *_membership(
                                        "{_target_state_id}", store="_secure_target_inside"
                                    ),
                                    _when(
                                        "_secure_source.reverse != true",
                                        [
                                            _when(
                                                "_secure_target_inside == true",
                                                [_set("_secure_circle_attack_disadvantage", True)],
                                            ),
                                        ],
                                        [
                                            *_membership(
                                                "{_actor_state_id}", store="_secure_actor_inside"
                                            ),
                                            _when(
                                                "_secure_actor_inside == true and _secure_target_i"
                                                "nside != true",
                                                [_set("_secure_circle_attack_disadvantage", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    immunity = _tool(
        "Derive actual Circle source-qualified charm, fear and possession immunity.",
        [
            _entity("target_state"),
            _parameter("condition"),
            _optional("source_state_id", default=""),
        ],
        [
            _set("_secure_circle_condition_immune", False),
            _set("_secure_circle_condition_needs_source", False),
            _when(
                "condition == 'charmed' or condition == 'frightened' or condition == 'possession'",
                [
                    *_sources(
                        [
                            _when(
                                "_secure_source.mode == 'circle'",
                                [
                                    *_membership(
                                        "{_target_state_id}", store="_secure_target_inside"
                                    ),
                                    _set(
                                        "_secure_target_protected", "_secure_target_inside == true"
                                    ),
                                    _when(
                                        "_secure_source.reverse == true",
                                        [
                                            _set(
                                                "_secure_target_protected",
                                                "_secure_target_inside != true",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_secure_target_protected == true",
                                        [
                                            _when(
                                                "source_state_id == ''",
                                                [
                                                    _set(
                                                        "_secure_circle_condition_needs_source",
                                                        True,
                                                    ),
                                                ],
                                                [
                                                    _call(
                                                        "read_srd_secure_area_creature_type",
                                                        {"actor_state": "{source_state_id}"},
                                                        ("_secure_creature_type",),
                                                    ),
                                                    _set("_secure_causing_inside", True),
                                                    _when(
                                                        "_secure_source.reverse == true",
                                                        [
                                                            *_membership(
                                                                "{source_state_id}",
                                                                store="_secure_causing_inside",
                                                            ),
                                                        ],
                                                    ),
                                                    _when(
                                                        "_secure_causing_inside == true and "
                                                        "_secure_creature_type in _secure_source.c"
                                                        "hosen_types",
                                                        [
                                                            _set(
                                                                "_secure_circle_condition_immune",
                                                                True,
                                                            )
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
                ],
            ),
        ],
    )
    transit = _tool(
        "Roll the actual source CHA save for a qualified magical Circle crossing.",
        [_entity("actor_state"), _parameter("source_id")],
        [
            *_read_source(),
            _call(
                "check_srd_secure_area_source",
                {"source_id": "{source_id}"},
                ("_secure_source", "_secure_area_effect_active"),
            ),
            _require(
                "_secure_area_effect_active == true and _secure_source.mode == 'circle'",
                "Use the real active Magic Circle crossing source.",
            ),
            _call(
                "read_srd_secure_area_creature_type",
                {"actor_state": "{_actor_state_id}"},
                ("_secure_creature_type",),
            ),
            _require(
                "_secure_creature_type in _secure_source.chosen_types and "
                "actor_state.saving_throw_modifiers.charisma != null",
                "The actual selected creature needs its saved Charisma save.",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_actor_state_id}",
                    "kind": "saving_throw",
                    "ability": "charisma",
                    "ability_modifier": "{actor_state.saving_throw_modifiers.charisma}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{_secure_source.save_dc}",
                    "advantage": False,
                    "disadvantage": False,
                },
                ("_d20_test", "_adjusted_total", "_outcome"),
            ),
            _set("_secure_circle_transit_allowed", "_outcome == 'success'"),
            _set(
                "_secure_circle_save",
                {
                    "roll": "_d20_test",
                    "total": "_adjusted_total",
                    "dc": "_secure_source.save_dc",
                    "outcome": "_outcome",
                },
            ),
        ],
    )
    return {
        "check_srd_magic_circle_attack": attack,
        "check_srd_magic_circle_condition": immunity,
        "resolve_srd_magic_circle_transit": transit,
    }


def _movement_tool():
    """Queries export source save requirements; paid transport owners settle their real failures."""
    return _tool(
        "Qualify actual source permissions before movement, without relocating or spending.",
        [
            _optional("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False),
            _optional("magical", "boolean", False),
            _optional("voluntary", "boolean", True),
        ],
        [
            _set("_secure_area_movement_allowed", True),
            _set("_secure_area_magic_save_source_ids", []),
            _set("_secure_area_blocking_source_ids", []),
            _call(
                "normalize_srd_barrier_scene_subject",
                {"subject": "{subject}"},
                ("_barrier_raw_scene_subject",),
            ),
            *_scene("_barrier_raw_scene_subject", "_secure_mover"),
            *_sources(
                [
                    *_membership("{_barrier_raw_scene_subject}", store="_secure_origin_inside"),
                    *_membership(
                        "{_barrier_raw_scene_subject}",
                        position="{destination}",
                        plane="{plane}",
                        store="_secure_destination_inside",
                    ),
                    _set("_secure_reject", False),
                    _when(
                        "_secure_source.mode == 'sanctum'",
                        [
                            _when(
                                "teleport == true and 'teleport' in _secure_source.security and "
                                "(_secure_origin_inside == true or _secure_destination_inside == t"
                                "rue)",
                                [_set("_secure_reject", True)],
                            ),
                            _when(
                                "plane != _secure_mover.metadata.plane and "
                                "'planar' in _secure_source.security and "
                                "(_secure_origin_inside == true or _secure_destination_inside == t"
                                "rue)",
                                [_set("_secure_reject", True)],
                            ),
                        ],
                    ),
                    _when(
                        "_secure_source.mode == 'forbiddance' and "
                        "_secure_mover.metadata.participant_ref != null",
                        [
                            _when(
                                "teleport == true and _secure_destination_inside == true",
                                [_set("_secure_reject", True)],
                            ),
                            _when(
                                "plane != _secure_mover.metadata.plane and "
                                "(_secure_origin_inside == true or _secure_destination_inside == t"
                                "rue)",
                                [_set("_secure_reject", True)],
                            ),
                        ],
                    ),
                    _when(
                        "_secure_source.mode == 'circle' and voluntary == true and "
                        "_secure_mover.metadata.participant_ref != null",
                        [
                            _call(
                                "read_srd_secure_area_creature_type",
                                {"actor_state": "{_secure_mover.metadata.participant_ref}"},
                                ("_secure_creature_type",),
                            ),
                            _when(
                                "_secure_creature_type in _secure_source.chosen_types",
                                [
                                    _set(
                                        "_secure_circle_crossing",
                                        "_secure_origin_inside != true and "
                                        "_secure_destination_inside == true",
                                    ),
                                    _when(
                                        "_secure_source.reverse == true",
                                        [
                                            _set(
                                                "_secure_circle_crossing",
                                                "_secure_origin_inside == true and "
                                                "_secure_destination_inside != true",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_secure_source.reverse != true and _secure_origin_inside !"
                                        "= true "
                                        "and _secure_destination_inside != true and "
                                        "plane == _secure_mover.metadata.plane and magical != true "
                                        "and teleport != true",
                                        [
                                            _call(
                                                "qualify_srd_secure_area_segment",
                                                {
                                                    "source_id": "{_secure_id}",
                                                    "start": "{_secure_mover.position}",
                                                    "end": "{destination}",
                                                    "plane": "{plane}",
                                                },
                                                ("_secure_area_segment_intersects",),
                                            ),
                                            _set(
                                                "_secure_circle_crossing",
                                                "_secure_area_segment_intersects == true",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_secure_circle_crossing == true",
                                        [
                                            _when(
                                                "magical == true or teleport == true or "
                                                "plane != _secure_mover.metadata.plane",
                                                [
                                                    _push(
                                                        "_secure_area_magic_save_source_ids",
                                                        "_secure_id",
                                                    ),
                                                ],
                                                [_set("_secure_reject", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_secure_reject == true",
                        [
                            _set("_secure_area_movement_allowed", False),
                            _push("_secure_area_blocking_source_ids", "_secure_id"),
                        ],
                    ),
                ]
            ),
        ],
    )


def _information_tool():
    """Fog and sound block actual edge crossings, rather than all interactions inside a Cube."""
    return _tool(
        "Qualify a source-bound information segment or actual Divination spell target.",
        [
            _array("start"),
            _array("end"),
            _parameter("plane"),
            _parameter("query_kind"),
            _optional("spell", default=""),
            _optional("subject", default=""),
        ],
        [
            _require(
                "query_kind == 'vision' or query_kind == 'sound' or "
                "query_kind == 'divination_sensor' or query_kind == 'divination_target'",
                "Use the actual source-qualified information operation.",
            ),
            _set("_secure_area_information_allowed", True),
            _set("_secure_area_information_blocking_ids", []),
            _when(
                "query_kind == 'divination_target'",
                [
                    {
                        "table_lookup": {
                            "table": "definitions.spell_casting",
                            "key": "spell",
                            "required": True,
                        },
                        "store_as": "_secure_query_spell",
                    },
                ],
            ),
            *_sources(
                [
                    _when(
                        "_secure_source.mode == 'sanctum'",
                        [
                            _set("_secure_reject", False),
                            _when(
                                "(query_kind == 'vision' and 'vision' in _secure_source.security) o"
                                "r "
                                "(query_kind == 'sound' and 'sound' in _secure_source.security) or "
                                "(query_kind == 'divination_sensor' and "
                                "'divination_sensors' in _secure_source.security)",
                                [
                                    _call(
                                        "qualify_srd_secure_area_point",
                                        {
                                            "source_id": "{_secure_id}",
                                            "point": "{start}",
                                            "plane": "{plane}",
                                        },
                                        ("_secure_area_point_inside",),
                                    ),
                                    _set("_secure_start_inside", "_secure_area_point_inside"),
                                    _call(
                                        "qualify_srd_secure_area_point",
                                        {
                                            "source_id": "{_secure_id}",
                                            "point": "{end}",
                                            "plane": "{plane}",
                                        },
                                        ("_secure_area_point_inside",),
                                    ),
                                    _set("_secure_end_inside", "_secure_area_point_inside"),
                                    _when(
                                        "_secure_start_inside != true or _secure_end_inside != tru"
                                        "e",
                                        [
                                            _call(
                                                "qualify_srd_secure_area_segment",
                                                {
                                                    "source_id": "{_secure_id}",
                                                    "start": "{start}",
                                                    "end": "{end}",
                                                    "plane": "{plane}",
                                                },
                                                ("_secure_area_segment_intersects",),
                                            ),
                                            _set(
                                                "_secure_reject",
                                                "_secure_area_segment_intersects == true",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "query_kind == 'divination_sensor' and _secure_end_inside "
                                        "== true",
                                        [_set("_secure_reject", True)],
                                    ),
                                ],
                            ),
                            _when(
                                "query_kind == 'divination_target' and "
                                "'divination_targets' in _secure_source.security and "
                                "_secure_query_spell.school == 'divination'",
                                [
                                    *_membership("{subject}"),
                                    _set("_secure_reject", "_secure_area_subject_inside == true"),
                                ],
                            ),
                            _when(
                                "_secure_reject == true",
                                [
                                    _set("_secure_area_information_allowed", False),
                                    _push("_secure_area_information_blocking_ids", "_secure_id"),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )


def _forbiddance_password_effects():
    """Derive actual spoken-password qualification at the entering boundary."""
    return [
        _set("_secure_password_spoken", False),
        _when(
            "event == 'entry' and _secure_source.password != '' and target_state.pending_secure_ar"
            "ea_speech != null",
            [
                _when(
                    "target_state.pending_secure_area_speech == _secure_source.password and 'uncon"
                    "scious' not in target_state.conditions and target_state.form_can_speak != fal"
                    "se",
                    [
                        _set("_secure_password_spoken", True),
                        _when(
                            "state.has_spell_environments == true",
                            [
                                _call(
                                    "resolve_srd_environment_actor",
                                    {"target_state": "{_target_state_id}"},
                                    ("_environment_entirely_silenced",),
                                ),
                                _when(
                                    "_environment_entirely_silenced == true",
                                    [_set("_secure_password_spoken", False)],
                                ),
                            ],
                        ),
                    ],
                )
            ],
        ),
    ]


def _forbiddance_admission_effects():
    """Password entry protects this source while inside; actual exit retires that admission."""
    return [
        *_membership("{_target_state_id}", store="_secure_current_inside"),
        _set("_secure_entered", False),
        _set("_secure_password_spoken", False),
        _set("_secure_password_admitted", False),
        _when(
            "_secure_source.password_admissions != null and _target_state_id in _secure_source.pas"
            "sword_admissions",
            [
                _set(
                    "_secure_password_admitted",
                    "_secure_source.password_admissions[_target_state_id] == true",
                ),
            ],
        ),
        _when(
            "event == 'entry'",
            [
                _require(
                    "origin != null and origin_plane != ''",
                    "An entry must retain its actual pre-movement origin and plane.",
                ),
                *_membership(
                    "{_target_state_id}",
                    position="{origin}",
                    plane="{origin_plane}",
                    store="_secure_prior_inside",
                ),
                _set(
                    "_secure_entered",
                    "_secure_prior_inside != true and _secure_current_inside == true",
                ),
            ],
        ),
        _when(
            "_secure_current_inside != true",
            [
                _set("_secure_password_admitted", False),
                _set(
                    "state.spell_secure_area_sources.{_secure_id}.password_admissions.{_target_sta"
                    "te_id}",
                    False,
                ),
            ],
        ),
        _when(
            "_secure_entered == true",
            [
                *_forbiddance_password_effects(),
                _set("_secure_password_admitted", "_secure_password_spoken == true"),
                _set(
                    "state.spell_secure_area_sources.{_secure_id}.password_admissions.{_target_sta"
                    "te_id}",
                    "_secure_password_admitted",
                ),
            ],
        ),
    ]


def _forbiddance_trigger_effects():
    """Apply one actual entry or turn-end receipt and its source damage packet."""
    return [
        _set(
            "_secure_event_receipt",
            {
                "clock": "_secure_event_clock",
                "turn": "_secure_event_turn",
                "password_spoken": "_secure_password_spoken",
                "password_admitted": "_secure_password_admitted",
            },
        ),
        _when(
            "event == 'entry'",
            [
                _set(
                    "state.spell_secure_area_sources.{_secure_id}.entry_damage_turns.{_target_stat"
                    "e_id}",
                    "_secure_event_receipt",
                )
            ],
            [
                _set(
                    "state.spell_secure_area_sources.{_secure_id}.end_damage_turns.{_target_state_"
                    "id}",
                    "_secure_event_receipt",
                )
            ],
        ),
        _when(
            "_secure_password_admitted != true",
            [
                {"dice": {"expression": "5d10"}, "store_as": "_secure_damage_roll"},
                _call(
                    "apply_srd_qualifying_damage",
                    {
                        "target": "{_target_state_id}",
                        "amount": "{_secure_damage_roll.total}",
                        "adjustment": 0,
                        "damage_type": "{_secure_source.damage_type}",
                        "source_state_id": "{_secure_source.caster_state_id}",
                        "game_damage_roll": True,
                    },
                    ("_effective_damage", "_is_alive_after"),
                ),
                _push(
                    "_secure_forbiddance_packets",
                    {
                        "source_id": "_secure_id",
                        "event": "event",
                        "roll": "_secure_damage_roll",
                        "damage_type": "_secure_source.damage_type",
                        "damage": "_effective_damage",
                        "alive_after": "_is_alive_after",
                    },
                ),
            ],
        ),
    ]


def _forbiddance_source_effects():
    """Qualify the selected source type, body entry and independent turn receipt."""
    return [
        _when(
            "_secure_source.mode == 'forbiddance' and target_state.is_alive == true",
            [
                _call(
                    "read_srd_secure_area_creature_type",
                    {"actor_state": "{_target_state_id}"},
                    ("_secure_creature_type",),
                ),
                *_forbiddance_admission_effects(),
                _when(
                    "_secure_creature_type in _secure_source.chosen_types",
                    [
                        _set("_secure_trigger", "_secure_current_inside == true"),
                        _when(
                            "event == 'entry'", [_set("_secure_trigger", "_secure_entered == true")]
                        ),
                        _set("_secure_prior_event", None),
                        _when(
                            "event == 'entry' and _target_state_id in _secure_source.entry_damage_"
                            "turns",
                            [
                                _set(
                                    "_secure_prior_event",
                                    "_secure_source.entry_damage_turns[_target_state_id]",
                                )
                            ],
                        ),
                        _when(
                            "event == 'end' and _target_state_id in _secure_source.end_damage_turn"
                            "s",
                            [
                                _set(
                                    "_secure_prior_event",
                                    "_secure_source.end_damage_turns[_target_state_id]",
                                )
                            ],
                        ),
                        _when(
                            "_secure_prior_event != null",
                            [
                                _when(
                                    "_secure_prior_event.clock == _secure_event_clock and _secure_"
                                    "prior_event.turn == _secure_event_turn",
                                    [_set("_secure_trigger", False)],
                                )
                            ],
                        ),
                        _when("_secure_trigger == true", _forbiddance_trigger_effects()),
                    ],
                ),
            ],
        )
    ]


def _forbiddance_tool():
    """Entry and end-of-turn damage have separate printed triggers and authentic saved receipts."""
    return _tool(
        "Resolve actual Forbiddance entry or turn-end damage, never a caller's damage verdict.",
        [
            _entity("target_state"),
            _parameter("event"),
            {**_array("origin"), "required": False, "default": None},
            _optional("origin_plane", default=""),
        ],
        [
            _require(
                "event == 'entry' or event == 'end'", "Use an actual entry or turn-end boundary."
            ),
            _set("_secure_forbiddance_packets", []),
            _set("_secure_event_clock", "'round'"),
            _set("_secure_event_turn", "state.lasting_tick // 2"),
            _when(
                "combat.encounter_active == true",
                [
                    _set("_secure_event_clock", "'combat'"),
                    _set("_secure_event_turn", "combat.turn_sequence"),
                ],
            ),
            *_sources(_forbiddance_source_effects()),
        ],
    )


def _password_movement_tool():
    """Speech belongs only to this genuine movement operation and is cleared before returning."""
    return _public(
        _tool(
            "Speak the selected password while taking an actual budgeted movement attempt.",
            [
                _entity("actor_state"),
                _array("destination"),
                _parameter("password"),
                _optional("mode", default="walk"),
                _optional("difficult_terrain", "boolean", False),
            ],
            [
                _require(
                    "len(password) > 0 and len(password) <= 120",
                    "Speak a bounded actual password for this movement attempt.",
                ),
                _require(
                    "actor_state.form_can_speak != false and 'unconscious' not in actor_state.cond"
                    "itions",
                    "The actual actor must be capable of speaking the entering password.",
                ),
                _set("actor_state.pending_secure_area_speech", "password"),
                _call(
                    "move_srd_scene_creature",
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{destination}",
                        "mode": "{mode}",
                        "difficult_terrain": "{difficult_terrain}",
                    },
                    ("_scene_movement_cost",),
                ),
                _set("actor_state.pending_secure_area_speech", None),
                _set("_secure_spoken_destination", "destination"),
            ],
        ),
        "movement",
        "Move and speak",
        [("Destination", "_secure_spoken_destination")],
    )


def _remove_consumer(tool, leaf):
    tool["effects"] = [effect for effect in tool["effects"] if leaf not in str(effect)]


def _compose_consumers(tools, changed):
    """Attach at real target-aware owners with one cheap inactive branch."""
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    _remove_consumer(attack, "check_srd_magic_circle_attack")
    attack["effects"].append(
        _when(
            "lasting_state.has_magic_circle_sources == true",
            [
                _call(
                    "check_srd_magic_circle_attack",
                    {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                    ("_secure_circle_attack_disadvantage",),
                ),
                _when(
                    "_secure_circle_attack_disadvantage == true",
                    [_set("_condition_disadvantage", True)],
                ),
            ],
        )
    )
    changed["resolve_srd_attack_conditions"] = attack
    immunity = deepcopy(tools["check_srd_target_condition_immunity"])
    _remove_consumer(immunity, "check_srd_magic_circle_condition")
    immunity["effects"].append(
        _when(
            "lasting_state.has_magic_circle_sources == true and "
            "(condition == 'charmed' or condition == 'frightened' or condition == 'possession')",
            [
                _call(
                    "check_srd_magic_circle_condition",
                    {
                        "target_state": "{_target_state_id}",
                        "condition": "{condition}",
                        "source_state_id": "{source_state_id}",
                    },
                    ("_secure_circle_condition_immune", "_secure_circle_condition_needs_source"),
                ),
                _when(
                    "_secure_circle_condition_immune == true",
                    [_set("_source_condition_immune", True)],
                ),
                _when(
                    "_secure_circle_condition_needs_source == true",
                    [_set("_source_condition_needs_source", True)],
                ),
            ],
        )
    )
    changed["check_srd_target_condition_immunity"] = immunity
    for name in ("advance_srd_lasting_time", "process_srd_turn_effects"):
        tool = deepcopy(tools[name])
        _remove_consumer(tool, "refresh_srd_secure_area_sources")
        tool["effects"].append(
            _when(
                "lasting_state.has_spell_secure_areas == true",
                [_call("refresh_srd_secure_area_sources", {})],
            )
        )
        changed[name] = tool
    end = deepcopy(tools["end_combat_turn"])
    _remove_consumer(end, "resolve_srd_forbiddance_event")
    index = next(
        i
        for i, e in enumerate(end["effects"])
        if e.get("set", {}).get("field") == "combat.action_available"
    )
    end["effects"].insert(
        index,
        _when(
            "state.has_spell_secure_areas == true",
            [
                _call(
                    "resolve_srd_forbiddance_event", {"target_state": "{_actor_id}", "event": "end"}
                ),
            ],
        ),
    )
    changed["end_combat_turn"] = end
    for name, output, target in (
        ("resolve_srd_illumination_target_sight", "_illumination_target_can_see", "target"),
        ("resolve_srd_illumination_point_sight", "_illumination_point_can_see", "point"),
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        _remove_consumer(tool, "check_srd_secure_area_information")
        effects = [*_scene("_observer_state_id", "_secure_observer")]
        endpoint, plane = "{point}", "{_illumination_point_plane}"
        if target == "target":
            effects += _scene("_target_state_id", "_secure_sight_target")
            endpoint, plane = (
                "{_secure_sight_target.position}",
                "{_secure_sight_target.metadata.plane}",
            )
        effects += [
            _call(
                "check_srd_secure_area_information",
                {
                    "start": "{_secure_observer.position}",
                    "end": endpoint,
                    "plane": plane,
                    "query_kind": "vision",
                },
                ("_secure_area_information_allowed",),
            ),
            _when(
                "_secure_area_information_allowed != true and "
                "(observer_state.blindsight_feet == null or "
                "observer_state.blindsight_feet < _illumination_point_distance or "
                "_illumination_point_total_cover == true)",
                [_set(output, False)],
            ),
        ]
        tool["effects"].append(_when("lasting_state.has_spell_secure_areas == true", effects))
        changed[name] = tool


def extend_runtime(text):
    """Compose source leaves before final removal, extended casting and reaction consumers."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_source_tools(),
        **_circle_tools(),
        "qualify_srd_secure_area_point": _point_tool(),
        "qualify_srd_secure_area_subject": _subject_tool(),
        "qualify_srd_secure_area_segment": _segment_tool(),
        "read_srd_secure_area_creature_type": _creature_type_tool(),
        "prepare_srd_secure_area_cast": _prepare_tool(),
        "cast_srd_spell_secure_area": _cast_tool(),
        "check_srd_secure_area_movement": _movement_tool(),
        "check_srd_secure_area_information": _information_tool(),
        "resolve_srd_forbiddance_event": _forbiddance_tool(),
        "move_srd_creature_speaking_password": _password_movement_tool(),
    }
    _compose_consumers(tools, changed)
    cast = deepcopy(tools["cast_spell"])
    for parameter in (
        _array("secure_area_origin", default=[0, 0, 0]),
        _optional("secure_area_types", "array", [], items={"type": "string"}, max_items=6),
        _optional("secure_area_security", "array", [], items={"type": "string"}, max_items=6),
        _optional("secure_area_reverse", "boolean", False),
        _optional("secure_area_side", "number", 100),
        _optional("secure_area_floor_width", "number", 200),
        _optional("secure_area_floor_length", "number", 200),
        _optional("secure_area_password", default=""),
    ):
        if not any(p["name"] == parameter["name"] for p in cast["parameters"]):
            cast["parameters"].append(parameter)
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'spell_secure_area'" not in e.get("condition", "")
        ]
    index = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(
        index + 1,
        _when(
            "_spell_definition.family == 'spell_secure_area'",
            [
                _call(
                    "cast_srd_spell_secure_area",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "origin": "{secure_area_origin}",
                        "chosen_types": "{secure_area_types}",
                        "security": "{secure_area_security}",
                        "reverse": "{secure_area_reverse}",
                        "side": "{secure_area_side}",
                        "floor_width": "{secure_area_floor_width}",
                        "floor_length": "{secure_area_floor_length}",
                        "password": "{secure_area_password}",
                        "damage_type": "{selected_damage_type}",
                        "caster_can_see_origin": "{caster_can_see_origin}",
                        "caster_can_touch_origin": "{caster_can_touch_target}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    text = _replace_tools(text, changed)
    # The shared fragment writer appends a blank after a new tool but replaces without it.
    new_tools = {name: tool for name, tool in changed.items() if name not in tools}
    if new_tools:
        text = _replace_tools(text, new_tools)
    schema = yaml.safe_dump({"spell_secure_area": _schema()}, sort_keys=False)
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      spell_secure_area:" in text:
        text = re.sub(
            r"(?ms)^      spell_secure_area:\n.*?(?=^      [a-z])",
            lambda _: schema + "\n",
            text,
            count=1,
        )
    else:
        variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
        anchor = "      " + next(iter(variants)) + ":"
        if text.count(anchor) != 1:
            raise ValueError("Secure-area schema needs one existing spell-family anchor.")
        text = text.replace(anchor, schema + "\n" + anchor, 1)
    return text


def bind_runtime_consumers(text):
    """Bind ordinary voluntary movement to its real origin, guard and post-relocation trigger."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    _compose_consumers(tools, changed)
    for name in ("move_srd_scene_creature_environment_segment",):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        _remove_consumer(tool, "check_srd_secure_area_movement")
        _remove_consumer(tool, "resolve_srd_forbiddance_event")
        # Travel's forced/paid classifications are joined by Root at their actual producer.
        condition = "lasting_state.has_spell_secure_areas == true"
        before = [
            *_scene("_actor_state_id", "_secure_movement_body"),
            _set("_secure_movement_origin", "_secure_movement_body.position"),
            _set("_secure_movement_plane", "_secure_movement_body.metadata.plane"),
            _call(
                "check_srd_secure_area_movement",
                {
                    "subject": "{_actor_state_id}",
                    "destination": "{destination}",
                    "plane": "{_secure_movement_body.metadata.plane}",
                    "teleport": False,
                },
                ("_secure_area_movement_allowed", "_secure_area_magic_save_source_ids"),
            ),
            _require(
                "_secure_area_movement_allowed == true",
                "The actual secure-area source prevents this ordinary voluntary movement.",
            ),
            _require(
                "len(_secure_area_magic_save_source_ids) == 0",
                "This planar Circle crossing needs the paid transit save owner.",
            ),
        ]
        tool["effects"].insert(0, _when(condition, before))
        tool["effects"].append(
            _when(
                condition,
                [
                    _when(
                        "_secure_movement_body.metadata.participant_ref != null",
                        [
                            _call(
                                "resolve_srd_forbiddance_event",
                                {
                                    "target_state": "{_secure_movement_body.metadata."
                                    "participant_ref}",
                                    "event": "entry",
                                    "origin": "{_secure_movement_origin}",
                                    "origin_plane": "{_secure_movement_plane}",
                                },
                                ("_secure_forbiddance_packets",),
                            ),
                        ],
                    ),
                ],
            )
        )
        changed[name] = tool
    return _replace_tools(text, changed)
