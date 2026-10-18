"""Pinned SRD information spells consume the admitted scene, language and action owners."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import _array, _scene_tools, _snapshot, _spatial, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from .srd_timed_senses import (
        _lookup,
        _loop,
        _optional,
        _public,
        _push,
        _replace_tools,
        _RuntimeDumper,
    )
else:
    from srd_scene_relations import _array, _scene_tools, _snapshot, _spatial, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from srd_timed_senses import (
        _lookup,
        _loop,
        _optional,
        _public,
        _push,
        _replace_tools,
        _RuntimeDumper,
    )


PUBLIC_TOOLS = {
    "query_srd_information_spell",
    "communicate_srd_spell",
    "operate_srd_information_proxy",
    "command_srd_plants",
    "dismiss_srd_information_spell",
    "resolve_srd_scene_relation",
    "move_srd_scene_creature",
    "observe_srd_information_proxy",
}
SOURCE = {
    "arcane_eye": (110, "Arcane Eye", "Arcane Hand"),
    "clairvoyance": (115, "Clairvoyance", "Clone"),
    "detect_evil_and_good": (123, "Detect Evil and Good", "Detect Magic"),
    "detect_magic": (123, "Detect Magic", "Detect Poison and Disease"),
    "detect_poison_and_disease": (123, "Detect Poison and Disease", "Detect Thoughts"),
    "find_traps": (131, "Find Traps", "Finger of Death"),
    "locate_animals_or_plants": ([144, 145], "Locate Animals or Plants", "Locate Creature"),
    "locate_creature": (145, "Locate Creature", "Locate Object"),
    "locate_object": (145, "Locate Object", "Longstrider"),
    "speak_with_animals": (164, "Speak with Animals", "Speak with Dead"),
    "speak_with_plants": (164, "Speak with Plants", "Spider Climb"),
    "tongues": ([169, 170], "Tongues", "Transport via Plants"),
}
PROFILES = {}
for _spell, _level, _range, _feet, _rounds, _concentration, _radius, _barrier, _mode in (
    ("arcane_eye", 4, "distance", 30, 600, True, 0, "solid", "proxy"),
    ("clairvoyance", 3, "distance", 5280, 100, True, 0, "none", "proxy"),
    ("detect_evil_and_good", 1, "self", 0, 100, True, 30, "detect", "detect"),
    ("detect_magic", 1, "self", 0, 100, True, 30, "detect", "detect"),
    ("detect_poison_and_disease", 1, "self", 0, 100, True, 30, "detect", "detect"),
    ("find_traps", 2, "distance", 120, 0, False, 120, "sight", "instant"),
    ("locate_animals_or_plants", 2, "self", 0, 0, False, 26400, "none", "instant"),
    ("locate_creature", 4, "self", 0, 600, True, 1000, "lead", "locate"),
    ("locate_object", 2, "self", 0, 100, True, 1000, "lead", "locate"),
    ("speak_with_animals", 1, "self", 0, 100, False, 0, "none", "language"),
    ("speak_with_plants", 3, "self", 0, 100, False, 30, "none", "plants"),
    ("tongues", 3, "touch", 0, 600, False, 0, "none", "language"),
):
    PROFILES[_spell] = {
        "family": "information_spell",
        "base_level": _level,
        "casting_time": "10_minutes" if _spell == "clairvoyance" else "action",
        "range_kind": _range,
        "range_feet": _feet,
        "effect_rounds": _rounds,
        "concentration": _concentration,
        "query_radius": _radius,
        "barrier": _barrier,
        "information_mode": _mode,
    }
    if _rounds:
        PROFILES[_spell]["duration_rounds"] = _rounds

ACTIVE = (
    "caster_state.is_alive == true and not ('incapacitated' in caster_state.conditions or "
    "'paralyzed' in caster_state.conditions or 'petrified' in caster_state.conditions or "
    "'stunned' in caster_state.conditions or 'unconscious' in caster_state.conditions)"
)
RECORD_EXPORTS = ("_info_record", "_info_active", "_info_instance_id")
POSITION_EXPORTS = ("_info_scene_entity", "_info_position")
MEASURE_EXPORTS = ("_scene_distance", "_scene_direction")
QUERY_EXPORTS = (
    "_info_found",
    "_info_results",
    "_info_dangers",
    "_info_distance",
    "_info_direction",
    "_info_movement_direction",
    "_info_perception",
    "_info_orb",
)


def validate_profiles(definitions):
    """Source constants are exact; declarations cannot silently add a rule or omit a clause."""
    for spell, definition in definitions.items():
        if definition.get("family") == "information_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Information source profile differs: {spell}")


def _schema():
    fields = {}
    for profile in PROFILES.values():
        for name, value in profile.items():
            if name == "family":
                continue
            fields[name] = {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
    return {
        "type": "object",
        "properties": fields,
        "required": [key for key in fields if key != "duration_rounds"],
        "additionalProperties": False,
    }


def _enum(name, values, default=None):
    parameter = _parameter(name, "enum", enum_values=values)
    if default is not None:
        parameter.update(required=False, default=default)
    return parameter


def _interval_effects():
    """Intersect a segment and the admitted box/cylinder; thickness is derived, never supplied."""
    effects = [
        _set("_segment_entry", 0),
        _set("_segment_exit", 1),
        _set("_segment_hit", True),
        _set("_segment_delta", [f"end[{i}] - start[{i}]" for i in range(3)]),
    ]
    for axis in range(3):
        effects.append(
            _when(
                f"volume.kind == 'box' or {axis} == 2",
                [
                    _set("_axis_min", f"volume.min[{axis}]"),
                    _set("_axis_max", f"volume.max[{axis}]"),
                    _when(
                        "volume.kind == 'cylinder'",
                        [
                            _set("_axis_min", "volume.center[2]"),
                            _set("_axis_max", "volume.center[2] + volume.height"),
                        ],
                    ),
                    {
                        "condition": f"abs(_segment_delta[{axis}]) < 0.000000000001",
                        "on_true": [
                            _when(
                                f"start[{axis}] < _axis_min or start[{axis}] > _axis_max",
                                [_set("_segment_hit", False)],
                            )
                        ],
                        "on_false": [
                            _set(
                                "_axis_first",
                                f"(_axis_min - start[{axis}]) * _segment_delta[{axis}] ** -1",
                            ),
                            _set(
                                "_axis_second",
                                f"(_axis_max - start[{axis}]) * _segment_delta[{axis}] ** -1",
                            ),
                            _set(
                                "_segment_entry",
                                "max(_segment_entry, min(_axis_first, _axis_second))",
                            ),
                            _set(
                                "_segment_exit",
                                "min(_segment_exit, max(_axis_first, _axis_second))",
                            ),
                        ],
                    },
                ],
            )
        )
    effects.append(
        _when(
            "volume.kind == 'cylinder'",
            [
                _set("_circle_x", "start[0] - volume.center[0]"),
                _set("_circle_y", "start[1] - volume.center[1]"),
                _set("_circle_a", "_segment_delta[0] ** 2 + _segment_delta[1] ** 2"),
                _set(
                    "_circle_b",
                    "2 * (_circle_x * _segment_delta[0] + _circle_y * _segment_delta[1])",
                ),
                _set("_circle_c", "_circle_x ** 2 + _circle_y ** 2 - volume.radius ** 2"),
                {
                    "condition": "_circle_a < 0.000000000001",
                    "on_true": [_when("_circle_c > 0", [_set("_segment_hit", False)])],
                    "on_false": [
                        _set("_circle_discriminant", "_circle_b ** 2 - 4 * _circle_a * _circle_c"),
                        {
                            "condition": "_circle_discriminant < 0",
                            "on_true": [_set("_segment_hit", False)],
                            "on_false": [
                                _set("_circle_root", "_circle_discriminant ** 0.5"),
                                _set(
                                    "_circle_first",
                                    "(-_circle_b - _circle_root) * (2 * _circle_a) ** -1",
                                ),
                                _set(
                                    "_circle_second",
                                    "(-_circle_b + _circle_root) * (2 * _circle_a) ** -1",
                                ),
                                _set("_segment_entry", "max(_segment_entry, _circle_first)"),
                                _set("_segment_exit", "min(_segment_exit, _circle_second)"),
                            ],
                        },
                    ],
                },
            ],
        )
    )
    effects += [
        _set("_segment_hit", "_segment_hit == true and _segment_entry <= _segment_exit"),
        _set("_segment_thickness", 0),
        _when(
            "_segment_hit == true",
            [
                _call(
                    "measure_srd_information_distance",
                    {"start": "{start}", "end": "{end}"},
                    MEASURE_EXPORTS,
                ),
                _set(
                    "_segment_thickness", "max(0, _segment_exit - _segment_entry) * _scene_distance"
                ),
            ],
        ),
    ]
    return effects


def _barrier_tools():
    interval = _tool(
        "Calculate a crossed interval using source-admitted physical geometry.",
        [
            _array("start"),
            _array("end"),
            _parameter("volume", "object", from_state="state.spatial_scene"),
        ],
        _interval_effects(),
    )
    barriers = _tool_scene(
        "Apply the exact source material thresholds on geometry crossed by the direct segment.",
        [
            _array("start"),
            _array("end"),
            _enum("rule", ["none", "detect", "lead", "solid", "light"]),
        ],
        _snapshot()
        + [
            _set("_information_blocked", False),
            _set("_barrier_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_barrier_geometry",
                "_barrier",
                [
                    _when(
                        "rule != 'none' and _barrier.active == true",
                        [
                            _call(
                                "srd_information_segment_interval",
                                {"start": "{start}", "end": "{end}", "volume": "{_barrier.volume}"},
                                ["_segment_hit", "_segment_thickness"],
                            ),
                            _when(
                                "_segment_hit == true and _segment_thickness > 0.000000000001",
                                [
                                    _when(
                                        "rule == 'solid' and _barrier.movement == 'blocking'",
                                        [_set("_information_blocked", True)],
                                    ),
                                    _when(
                                        "rule == 'light' and _barrier.visibility == 'opaque'",
                                        [_set("_information_blocked", True)],
                                    ),
                                    _when(
                                        "rule != 'solid' and rule != 'light'",
                                        [
                                            _require(
                                                "_barrier.metadata.material != null",
                                                "Crossed geometry needs its actual "
                                                "material for this spell.",
                                            ),
                                            _when(
                                                "_barrier.metadata.material == 'lead'",
                                                [_set("_information_blocked", True)],
                                            ),
                                            _when(
                                                "rule == 'detect' and "
                                                "((_barrier.metadata.material in "
                                                "['stone', 'dirt', 'wood'] and "
                                                "_segment_thickness + 0.000000000001 >= 1) "
                                                "or "
                                                "(_barrier.metadata.material == 'metal' and "
                                                "_segment_thickness + 0.000000000001 >= "
                                                "1 * 12 ** -1))",
                                                [_set("_information_blocked", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"srd_information_segment_interval": interval, "srd_information_barriers": barriers}


def _record_tools():
    identity = _tool(
        "Read an exact information cast identity, including an unambiguous legacy source tuple.",
        [_parameter("record", "object", from_state="state.turn_effects")],
        [
            _set("_info_instance_id", "record.instance_id"),
            _when(
                "record.instance_id == null",
                [
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "legacy:{record.caster}:{record.spell}:"
                            "{record.target}:{record.started_tick}"
                        },
                        ["_info_projection_message"],
                    ),
                    _set("_info_instance_id", "_info_projection_message"),
                ],
            ),
        ],
    )
    check = _tool(
        "Check one exact source record against time and concentration ownership.",
        [_entity("caster_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _set(
                "_info_active",
                "record.kind == 'information_spell' and state.lasting_tick < record.expires_tick",
            ),
            _when(
                "record.concentration == true",
                [
                    _set(
                        "_info_active",
                        "_info_active == true and " + ACTIVE + " and "
                        "caster_state.concentration_spell_id == record.spell and "
                        "caster_state.concentration_effect_family == 'information_spell' and "
                        "caster_state.concentration_started_tick == record.started_tick",
                    ),
                ],
            ),
        ],
    )
    collect = _tool(
        "Select an active source-owned information record for a caster and spell.",
        [_entity("caster_state"), _parameter("spell"), _optional("instance_id", default="")],
        [
            _set("_info_record", None),
            _set("_info_active", False),
            _set("_info_instance_id", "''"),
            _set("_information_records", []),
            _when(
                "state.turn_effects != null", [_set("_information_records", "state.turn_effects")]
            ),
            _loop(
                "_information_records",
                "_information_record",
                [
                    _when(
                        "_information_record.kind == 'information_spell' and "
                        "_information_record.caster == _caster_state_id and "
                        "_information_record.spell == spell",
                        [
                            _call(
                                "read_srd_information_instance",
                                {"record": "{_information_record}"},
                                ["_info_instance_id"],
                            ),
                            _call(
                                "check_srd_information_record",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_information_record}",
                                },
                                ["_info_active"],
                            ),
                            _when(
                            "_info_active == true and "
                            "(instance_id == '' or instance_id == _info_instance_id)",
                                [_set("_info_record", "_information_record")],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_info_active", "_info_record != null"),
            _when(
                "_info_active == true",
                [
                    _call(
                        "read_srd_information_instance",
                        {"record": "{_info_record}"},
                        ["_info_instance_id"],
                    ),
                ],
            ),
        ],
    )
    return {
        "read_srd_information_instance": identity,
        "check_srd_information_record": check,
        "collect_srd_information_record": collect,
    }


def _candidate_tools():
    """Normalize admitted entities, physical objects and areas without a second scene model."""
    closest = _tool(
        "Calculate the nearest point in an admitted box or cylinder.",
        [
            _array("origin"),
            _parameter("volume", "object", from_state="state.spatial_scene"),
        ],
        [
            _set("_volume_point", [0, 0, 0]),
            {
                "condition": "volume.kind == 'box'",
                "on_true": [
                    _set(
                        "_volume_point",
                        [
                            f"min(volume.max[{i}], max(volume.min[{i}], origin[{i}]))"
                            for i in range(3)
                        ],
                    )
                ],
                "on_false": [
                    _set(
                        "_volume_xy_distance",
                        "((origin[0] - volume.center[0]) ** 2 + (origin[1] - "
                        "volume.center[1]) ** 2) ** 0.5",
                    ),
                    _set("_volume_scale", 1),
                    _when(
                        "_volume_xy_distance > volume.radius",
                        [_set("_volume_scale", "volume.radius * _volume_xy_distance ** -1")],
                    ),
                    _set(
                        "_volume_point",
                        [
                            f"volume.center[{i}] + "
                            f"(origin[{i}] - volume.center[{i}]) * _volume_scale"
                            for i in range(2)
                        ]
                        + [
                            "min(volume.center[2] + volume.height, max(volume.center[2], "
                            "origin[2]))"
                        ],
                    ),
                ],
            },
        ],
    )
    collect = _tool_scene(
        "Select source-qualified subjects from all admitted scene collections.",
        [_array("origin")],
        _snapshot()
        + [
            _set("_query_candidates", []),
            _set("_candidate_entities", "state.spatial_scene.entities"),
            _loop(
                "_candidate_entities",
                "_candidate_entry",
                [
                    _push("_query_candidates", "merge(_candidate_entry, {'scene_kind': 'entity'})"),
                ],
            ),
        ],
    )
    # Object literals are effect values in the closed expression grammar.
    collect["effects"][-1]["for_each"]["do"] = [
        _set("_candidate_source_patch", {"scene_kind": "'entity'"}),
        _push("_query_candidates", "merge(_candidate_entry, _candidate_source_patch)"),
    ]
    for collection, field in (("geometry", "volume"), ("zones", "shape")):
        collect["effects"] += [
            _set("_candidate_entries", "state.spatial_scene." + collection),
            _loop(
                "_candidate_entries",
                "_candidate_entry",
                [
                    _when(
                        "_candidate_entry.active == true"
                        if collection == "geometry"
                        else "_candidate_entry.metadata.active != false",
                        [
                            _call(
                                "srd_information_volume_point",
                                {
                                    "origin": "{origin}",
                                    "volume": "{_candidate_entry." + field + "}",
                                },
                                ["_volume_point"],
                            ),
                            _set(
                                "_candidate_source_patch",
                                {
                                    "scene_kind": "'" + collection + "'",
                                    "position": "_volume_point",
                                    "active": True,
                                },
                            ),
                            _push(
                                "_query_candidates",
                                "merge(_candidate_entry, _candidate_source_patch)",
                            ),
                        ],
                    ),
                ],
            ),
        ]
    visible = _tool_scene(
        "Use the admitted volume's front-face visibility samples, matching the scene "
        "exposure model.",
        [
            _array("origin"),
            _parameter("candidate", "object"),
        ],
        _snapshot()
        + [
            _set("_candidate_line_visible", False),
            _set("_visibility_geometry", "state.spatial_scene.geometry"),
            _set("_visibility_volume", None),
            {
                "condition": "candidate.scene_kind == 'geometry'",
                "on_true": [_set("_visibility_volume", "candidate.volume")],
                "on_false": [_set("_visibility_volume", "candidate.shape")],
            },
            {
                "condition": "_visibility_volume.kind == 'box'",
                "on_true": [
                    _set("_visibility_low", "_visibility_volume.min"),
                    _set("_visibility_high", "_visibility_volume.max"),
                ],
                "on_false": [
                    _set(
                        "_visibility_low",
                        [
                            "_visibility_volume.center[0] - _visibility_volume.radius",
                            "_visibility_volume.center[1] - _visibility_volume.radius",
                            "_visibility_volume.center[2]",
                        ],
                    ),
                    _set(
                        "_visibility_high",
                        [
                            "_visibility_volume.center[0] + _visibility_volume.radius",
                            "_visibility_volume.center[1] + _visibility_volume.radius",
                            "_visibility_volume.center[2] + _visibility_volume.height",
                        ],
                    ),
                ],
            },
            _set("_visibility_dx", "(_visibility_low[0] + _visibility_high[0]) * 0.5 - origin[0]"),
            _set("_visibility_dy", "(_visibility_low[1] + _visibility_high[1]) * 0.5 - origin[1]"),
            _set("_visibility_fractions", [1 / 6, 0.5, 5 / 6]),
            _loop(
                "_visibility_fractions",
                "_visibility_horizontal",
                [
                    _loop(
                        "_visibility_fractions",
                        "_visibility_vertical",
                        [
                            _when(
                                "_candidate_line_visible != true",
                                [
                                    _set(
                                        "_visibility_z",
                                        "_visibility_low[2] + (_visibility_high[2] - "
                                        "_visibility_low[2]) * _visibility_vertical",
                                    ),
                                    {
                                        "condition": "abs(_visibility_dx) >= abs(_visibility_dy)",
                                        "on_true": [
                                            _set("_visibility_x", "_visibility_low[0]"),
                                            _when(
                                                "_visibility_dx < 0",
                                                [_set("_visibility_x", "_visibility_high[0]")],
                                            ),
                                            _set(
                                                "_visibility_y",
                                                "_visibility_low[1] + (_visibility_high[1] - "
                                                "_visibility_low[1]) * _visibility_horizontal",
                                            ),
                                        ],
                                        "on_false": [
                                            _set("_visibility_y", "_visibility_low[1]"),
                                            _when(
                                                "_visibility_dy < 0",
                                                [_set("_visibility_y", "_visibility_high[1]")],
                                            ),
                                            _set(
                                                "_visibility_x",
                                                "_visibility_low[0] + (_visibility_high[0] - "
                                                "_visibility_low[0]) * _visibility_horizontal",
                                            ),
                                        ],
                                    },
                                    _set(
                                        "_visibility_sample",
                                        ["_visibility_x", "_visibility_y", "_visibility_z"],
                                    ),
                                    _set("_visibility_sample_clear", True),
                                    _loop(
                                        "_visibility_geometry",
                                        "_visibility_barrier",
                                        [
                                            _when(
                                                "_visibility_sample_clear == true and "
                                                "_visibility_barrier.active == true and "
                                                "_visibility_barrier.visibility == 'opaque' "
                                                "and _visibility_barrier.id != candidate.id",
                                                [
                                                    _call(
                                                        "srd_information_segment_interval",
                                                        {
                                                            "start": "{origin}",
                                                            "end": "{_visibility_sample}",
                                                            "volume": "{_visibility_barrier."
                                                            "volume}",
                                                        },
                                                        ["_segment_hit", "_segment_thickness"],
                                                    ),
                                                    _when(
                                                        "_segment_hit == true and "
                                                        "_segment_thickness > "
                                                        "0.000000000001",
                                                        [_set("_visibility_sample_clear", False)],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_visibility_sample_clear == true",
                                        [_set("_candidate_line_visible", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    active = _tool(
        "Check exact saved spell ownership before its magic enters an information result.",
        [
            _entity("source_state"),
            _parameter("record", "object", from_state="state.turn_effects"),
        ],
        [
            _set("_information_effect_active", True),
            _when(
                "record.expires_tick != null",
                [_set("_information_effect_active", "state.lasting_tick < record.expires_tick")],
            ),
            _when(
                "record.concentration == true",
                [
                    _set(
                        "_information_effect_active",
                        "_information_effect_active == true and "
                        "source_state.concentration_spell_id == record.spell",
                    ),
                    _when(
                        "record.started_tick != null",
                        [
                            _set(
                                "_information_effect_active",
                                "_information_effect_active == true and "
                                "source_state.concentration_started_tick == "
                                "record.started_tick",
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    magic = _tool(
        "Derive source spell schools and active effects on a bearing subject.",
        [
            _parameter("candidate", "object", from_state="state.spatial_scene"),
        ],
        [
            _set("_bearer_magic", "candidate.metadata.magical_effect == true"),
            _set("_bearer_schools", []),
            _when(
                "candidate.metadata.source_spell != null and "
                "candidate.metadata.magical_effect == true",
                [
                    {
                        "table_lookup": {
                            "table": "definitions.spell_casting",
                            "key": "candidate.metadata.source_spell",
                            "required": True,
                        },
                        "store_as": "_bearer_header",
                    },
                    _push("_bearer_schools", "_bearer_header.school"),
                ],
            ),
            _set("_bearer_effects", []),
            _when("state.turn_effects != null", [_set("_bearer_effects", "state.turn_effects")]),
            _loop(
                "_bearer_effects",
                "_bearer_effect",
                [
                    _when(
                        "_bearer_effect.spell != null and _bearer_effect.caster != null and "
                        "(_bearer_effect.target == candidate.id or "
                        "(candidate.metadata.participant_ref != null and "
                        "_bearer_effect.target == candidate.metadata.participant_ref))",
                        [
                            _call(
                                "check_srd_information_source_effect",
                                {
                                    "source_state": "{_bearer_effect.caster}",
                                    "record": "{_bearer_effect}",
                                },
                                ["_information_effect_active"],
                            ),
                            _when(
                                "_information_effect_active == true",
                                [
                                    _set("_bearer_magic", True),
                                    {
                                        "table_lookup": {
                                            "table": "definitions.spell_casting",
                                            "key": "_bearer_effect.spell",
                                            "required": True,
                                        },
                                        "store_as": "_bearer_header",
                                    },
                                    _when(
                                        "_bearer_header.school not in _bearer_schools",
                                        [_push("_bearer_schools", "_bearer_header.school")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    invisible = _tool(
        "Read the actual observed subject's Invisible condition.",
        [_entity("subject_state")],
        [
            _set(
                "_information_subject_invisible",
                "subject_state.conditions != null and 'invisible' in subject_state.conditions",
            ),
        ],
    )
    projection = _tool(
        "Format only a source-authorized, already-filtered information result.",
        [_parameter("message")],
        [_set("_info_projection_message", "message")],
    )
    return {
        "resolve_srd_information_subject_invisibility": invisible,
        "format_srd_information_projection": projection,
        "srd_information_volume_point": closest,
        "collect_srd_information_candidates": collect,
        "srd_information_candidate_visibility": visible,
        "check_srd_information_source_effect": active,
        "resolve_srd_information_bearer_magic": magic,
    }


def _lighting_tools():
    """Compose source-owned light at the observed point with the admitted ambient light."""
    return {
        "resolve_srd_information_light": _tool_scene(
            "Calculate active actor spell light using actual source positions and occlusion.",
            [
                _array("point"),
                _parameter("plane"),
                _enum("ambient", ["bright", "dim", "darkness", "magical_darkness"]),
            ],
            _snapshot()
            + [
                _set("_information_light", "ambient"),
                _set("_lighting_records", []),
                _when(
                    "ambient != 'magical_darkness' and state.turn_effects != null",
                    [_set("_lighting_records", "state.turn_effects")],
                ),
                _loop(
                    "_lighting_records",
                    "_lighting_record",
                    [
                        _when(
                            "_lighting_record.kind == 'actor_spell_modifier' and "
                            "(_lighting_record.mode == 'fire_shield_warm' or "
                            "_lighting_record.mode == 'fire_shield_chill')",
                            [
                                _call(
                                    "check_srd_actor_spell_light",
                                    {
                                        "actor_state": "{_lighting_record.target}",
                                        "lasting_state": "state",
                                    },
                                    [
                                        "_actor_spell_bright_light_feet",
                                        "_actor_spell_dim_light_feet",
                                    ],
                                ),
                                _when(
                                    "_actor_spell_dim_light_feet > 0",
                                    [
                                        _call(
                                            "srd_information_scene_entity",
                                            {"subject": "{_lighting_record.target}"},
                                            POSITION_EXPORTS,
                                        ),
                                        _set("_light_origin", "_info_position"),
                                        _call(
                                            "measure_srd_information_distance",
                                            {"start": "{_light_origin}", "end": "{point}"},
                                            MEASURE_EXPORTS,
                                        ),
                                        _set("_light_distance", "_scene_distance"),
                                        _set(
                                            "_light_same_plane",
                                            "_info_scene_entity.metadata.plane == plane",
                                        ),
                                        _call(
                                            "srd_information_barriers",
                                            {
                                                "start": "{_light_origin}",
                                                "end": "{point}",
                                                "rule": "light",
                                            },
                                            ["_information_blocked"],
                                        ),
                                        _when(
                                            "_light_same_plane == true and "
                                            "_information_blocked != true",
                                            [
                                                _when(
                                                    "_light_distance <= "
                                                    "_actor_spell_bright_light_feet",
                                                    [_set("_information_light", "'bright'")],
                                                ),
                                                _when(
                                                    "_information_light == 'darkness' and "
                                                    "_light_distance <= _actor_spell_dim_ligh"
                                                    "t_feet",
                                                    [_set("_information_light", "'dim'")],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
    }


def _query_tools():
    # Metadata qualifies fiction; the scene supplies positions and calculated limits.
    prepare = [
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_query_origin", "_info_position"),
        _set("_query_plane", "_info_scene_entity.metadata.plane"),
        _require("_query_plane != null", "The observer needs its actual scene plane."),
        _lookup("spell", "_query_profile"),
        _set("_info_found", False),
        _set("_info_results", []),
        _set("_info_dangers", []),
        _set("_info_distance", None),
        _set("_info_direction", []),
        _set("_info_movement_direction", []),
        _set("_info_perception", "''"),
        _set("_info_orb", False),
    ]
    scan = [
        *[
            _require(
                f"_query_origin[{axis}] - _query_profile.query_radius >= "
                f"state.spatial_scene.map.bounds.min[{axis}] and "
                f"_query_origin[{axis}] + _query_profile.query_radius <= "
                f"state.spatial_scene.map.bounds.max[{axis}]",
                "The admitted scene must cover this spell's complete search radius.",
            )
            for axis in range(3)
        ],
        _set("_query_record", {"origin": "_query_origin", "radius": "_query_profile.query_radius"}),
        _call(
            "collect_srd_information_candidates",
            {"origin": "{_query_origin}"},
            ["_query_candidates"],
        ),
        _loop(
            "_query_candidates",
            "_candidate",
            [
                _when(
                    "_candidate.active == true",
                    [
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_query_origin}", "end": "{_candidate.position}"},
                            MEASURE_EXPORTS,
                        ),
                        _set("_candidate_distance", "_scene_distance"),
                        _set("_candidate_direction", "_scene_direction"),
                        _set("_query_record.radius", "_candidate_distance"),
                        _set(
                            "_candidate_eligible",
                            "_candidate_distance <= _query_profile.query_radius",
                        ),
                        _when(
                            "_candidate_eligible == true",
                            [
                                _call(
                                    "qualify_srd_information_candidate",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "spell": "{spell}",
                                        "mode": "{mode}",
                                        "candidate": "{_candidate}",
                                        "sought_kind": "{sought_kind}",
                                        "sought_subject": "{sought_subject}",
                                        "record": "{_query_record}",
                                    },
                                    [
                                        "_candidate_eligible",
                                        "_candidate_danger",
                                        "_candidate_kind",
                                        "_candidate_schools",
                                    ],
                                ),
                                _when(
                                    "_candidate_eligible == true",
                                    [
                                        _require(
                                            "_candidate.metadata.plane != null",
                                            "A qualified subject needs its actual plane.",
                                        ),
                                        _set(
                                            "_candidate_eligible",
                                            "_candidate.metadata.plane == _query_plane",
                                        ),
                                        _call(
                                            "srd_information_barriers",
                                            {
                                                "start": "{_query_origin}",
                                                "end": "{_candidate.position}",
                                                "rule": "{_query_barrier_rule}",
                                            },
                                            ["_information_blocked"],
                                        ),
                                        _when(
                                            "_candidate_eligible == true and "
                                            "_information_blocked != true",
                                            [
                                                _set("_info_found", True),
                                                _when(
                                                    "spell == 'find_traps' and "
                                                    "_candidate_danger not in "
                                                    "_info_dangers",
                                                    [_push("_info_dangers", "_candidate_danger")],
                                                ),
                                                _when(
                                                    "spell == 'detect_evil_and_good' or "
                                                    "spell == 'detect_poison_and_disease'",
                                                    [
                                                        _call(
                                                            "format_srd_information_projection",
                                                            {
                                                                "message": "{_candidate_kind} at "
                                                                "{_candidate.position} "
                                                                "feet"
                                                            },
                                                            ["_info_projection_message"],
                                                        ),
                                                        _push(
                                                            "_info_results",
                                                            "_info_projection_message",
                                                        ),
                                                    ],
                                                ),
                                                _when(
                                                    "spell == 'detect_magic' and mode == 'aura'",
                                                    [
                                                        _call(
                                                            "format_srd_information_projection",
                                                            {
                                                                "message": "{_candidate.id}: faint "
                                                                "aura; spell schools "
                                                                "{_candidate_schools}"
                                                            },
                                                            ["_info_projection_message"],
                                                        ),
                                                        _push(
                                                            "_info_results",
                                                            "_info_projection_message",
                                                        ),
                                                    ],
                                                ),
                                                _when(
                                                    "spell == 'locate_animals_or_plants' or "
                                                    "spell == 'locate_object' or "
                                                    "spell == 'locate_creature'",
                                                    [
                                                        _when(
                                                            "_info_distance == null or "
                                                            "_candidate_distance < "
                                                            "_info_distance",
                                                            [
                                                                _set(
                                                                    "_info_distance",
                                                                    "_candidate_distance",
                                                                ),
                                                                _set(
                                                                    "_info_direction",
                                                                    "_candidate_direction",
                                                                ),
                                                                _set(
                                                                    "_info_movement_direction", []
                                                                ),
                                                                _when(
                                                                    "_candidate.metadata.previous_p"
                                                                    "osition != null and "
                                                                    "_candidate.metadata.moving == "
                                                                    "true and (_candidate.metadata."
                                                                    "motion_tick == null or "
                                                                    "_candidate.metadata.motion_tic"
                                                                    "k == state.lasting_tick)",
                                                                    [
                                                                        _call(
                                                                            "measure_srd_information_distance",
                                                                            {
                                                                                "start": (
                                                                                    "{_candidate."
                                                                                    "metadata."
                                                                                    "previous_"
                                                                                    "position}"
                                                                                ),
                                                                                "end": (
                                                                                    "{_candidate."
                                                                                    "position}"
                                                                                ),
                                                                            },
                                                                            MEASURE_EXPORTS,
                                                                        ),
                                                                        _when(
                                                                            "_scene_distance > 0",
                                                                            [
                                                                                _set(
                                                                                    "_info_movement_direction",
                                                                                    "_scene_direction",
                                                                                ),
                                                                            ],
                                                                        ),
                                                                    ],
                                                                ),
                                                            ],
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        # The locator spells except the instantaneous five-mile locator do not reveal distance.
        _when("spell != 'locate_animals_or_plants'", [_set("_info_distance", None)]),
    ]
    candidate = _tool_scene(
        "Qualify a scene candidate, then enforce Mind Blank before aggregation.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("mode"),
            _parameter("candidate", "object"),
            _optional("sought_kind", default=""),
            _optional("sought_subject", default=""),
            _parameter("record", "object"),
        ],
        [
            _set("_candidate_eligible", False),
            _set("_candidate_danger", "''"),
            _set("_candidate_kind", "''"),
            _set("_candidate_schools", []),
            _when(
                "spell == 'detect_magic'",
                [
                    _call(
                        "resolve_srd_information_bearer_magic",
                        {"candidate": "{candidate}"},
                        ["_bearer_magic", "_bearer_schools"],
                    ),
                    _set(
                        "_candidate_eligible",
                        "_bearer_magic == true and (mode != 'aura' or candidate.scene_kind "
                        "!= 'zones')",
                    ),
                    _set("_candidate_schools", "_bearer_schools"),
                ],
            ),
            _when(
                "spell == 'detect_evil_and_good'",
                [
                    _set(
                        "_candidate_eligible",
                        "candidate.metadata.creature_type in "
                        "['aberration', 'celestial', 'elemental', 'fey', 'fiend', 'undead'] or "
                        "candidate.metadata.hallow == true",
                    ),
                    _set("_candidate_kind", "candidate.metadata.creature_type"),
                    _when(
                        "candidate.metadata.hallow == true", [_set("_candidate_kind", "'hallow'")]
                    ),
                ],
            ),
            _when(
                "spell == 'detect_poison_and_disease'",
                [
                    _set(
                        "_candidate_eligible",
                        "candidate.metadata.poison == true or "
                        "candidate.metadata.poisonous == true or candidate.metadata.venomous "
                        "== true or "
                        "candidate.metadata.magical_contagion == true",
                    ),
                    _when(
                        "_candidate_eligible == true",
                        [
                            _require(
                                "candidate.metadata.detected_kind != null",
                                "Detected poison/creature/contagion needs its kind.",
                            ),
                            _set("_candidate_kind", "candidate.metadata.detected_kind"),
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'find_traps'",
                [
                    _set("_candidate_eligible", "candidate.metadata.intentional_trap == true"),
                    _when(
                        "_candidate_eligible == true",
                        [
                            _require(
                                "candidate.metadata.general_danger != null",
                                "A trap needs its general danger fact.",
                            ),
                            _set("_candidate_danger", "candidate.metadata.general_danger"),
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'locate_animals_or_plants'",
                [
                    _set(
                        "_candidate_eligible",
                        "candidate.metadata.kind == sought_kind and "
                        "(candidate.metadata.creature_type in ['beast', 'plant'] or "
                        "(candidate.metadata.nonmagical_plant == true and "
                        "candidate.metadata.magical_effect != true))",
                    ),
                ],
            ),
            _when(
                "spell == 'locate_object' or spell == 'locate_creature'",
                [
                    _set(
                        "_candidate_eligible",
                        "(sought_subject != '' and (candidate.id == sought_subject or "
                        "candidate.metadata.participant_ref == sought_subject)) or "
                        "(sought_subject == '' and candidate.metadata.kind == sought_kind)",
                    ),
                    _when(
                        "spell == 'locate_object'",
                        [
                            _set(
                                "_candidate_eligible",
                                "_candidate_eligible == true and "
                                "candidate.metadata.is_object == true",
                            )
                        ],
                    ),
                    _when(
                        "spell == 'locate_creature'",
                        [
                            _set(
                                "_candidate_eligible",
                                "_candidate_eligible == true and "
                                "candidate.metadata.creature_type != null and "
                                "candidate.metadata.different_form != true",
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "_candidate_eligible == true and (spell == 'find_traps' or "
                "(spell == 'detect_magic' and mode == 'aura'))",
                [
                    {
                        "condition": "candidate.scene_kind == 'entity'",
                        "on_true": [
                            _spatial(
                                "exposure",
                                {
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "observer": {"point": "{record.origin}"},
                                    "target": {"entity_id": "{candidate.id}"},
                                    "samples": {"horizontal": 3, "vertical": 3},
                                    "include": ["geometry"],
                                },
                                "_candidate_exposure",
                            ),
                            _set("_candidate_eligible", "_candidate_exposure.visible_samples > 0"),
                        ],
                        "on_false": [
                            _call(
                                "srd_information_candidate_visibility",
                                {"origin": "{record.origin}", "candidate": "{candidate}"},
                                ["_candidate_line_visible"],
                            ),
                            _set("_candidate_eligible", "_candidate_line_visible"),
                        ],
                    },
                    _when(
                        "spell == 'detect_magic' and mode == 'aura'",
                        [
                            _require(
                                "candidate.metadata.lighting != null",
                                "Aura sight needs actual lighting.",
                            ),
                            _call(
                                "resolve_srd_information_light",
                                {
                                    "point": "{candidate.position}",
                                    "plane": "{candidate.metadata.plane}",
                                    "ambient": "{candidate.metadata.lighting}",
                                },
                                ["_information_light"],
                            ),
                            _set("_candidate_subject", None),
                            _when(
                                "candidate.metadata.participant_ref != null",
                                [_set("_candidate_subject", "candidate.metadata.participant_ref")],
                            ),
                            _call(
                                "resolve_srd_sight",
                                {
                                    "observer_state": "{_caster_state_id}",
                                    "subject_state": "{_candidate_subject}",
                                    "distance": "{ceil(record.radius)}",
                                    "lighting": "{_information_light}",
                                    "obstruction": "clear",
                                    "subject_invisible": "{candidate.metadata.invisible == true}",
                                },
                                ["_sight_can_see"],
                            ),
                            _set(
                                "_candidate_eligible",
                                "_candidate_eligible == true and _sight_can_see == true",
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_candidate_eligible == true and candidate.metadata.participant_ref != null",
                [
                    _set("_candidate_query_kind", "'information_gathering'"),
                    _when(
                        "spell == 'locate_creature' or spell == 'detect_evil_and_good'",
                        [_set("_candidate_query_kind", "'magical_location'")],
                    ),
                    _call(
                        "check_srd_mind_blank_information",
                        {
                            "target_state": "{candidate.metadata.participant_ref}",
                            "query_kind": "{_candidate_query_kind}",
                        },
                        ["_mind_blank_information_blocked"],
                    ),
                    _set("_candidate_eligible", "_mind_blank_information_blocked != true"),
                ],
            ),
        ],
    )
    query = _public(
        _tool_scene(
            "Consume an active information spell using calculated scene facts and "
            "source-only outputs.",
            [
                _entity("caster_state"),
                _enum("spell", list(PROFILES)),
                _enum("mode", ["sense", "aura", "locate", "remote_perception"], "sense"),
            ],
            [
                _call(
                    "collect_srd_information_record",
                    {"caster_state": "{_caster_state_id}", "spell": "{spell}"},
                    RECORD_EXPORTS,
                ),
                _require(
                    "_info_active == true",
                    "The selected information spell is not active for this caster.",
                ),
                _require(
                    "spell not in ['find_traps', 'locate_animals_or_plants', 'tongues', "
                    "'speak_with_animals', 'speak_with_plants']",
                    "Use the spell's casting, language or plants consumer.",
                ),
                _require(
                    "mode != 'aura' or spell == 'detect_magic'",
                    "Only Detect Magic has an aura Magic action.",
                ),
                _when(
                    "mode == 'aura' and combat.encounter_active == true",
                    [
                        _require(
                            "combat.turn_active == true and combat.active_actor == "
                            "_caster_state_id",
                            "Aura needs the caster's turn.",
                        ),
                        _call("spend_srd_action", {"magic_action": True}),
                    ],
                ),
                _call(
                    "scan_srd_information_spell",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "mode": "{mode}",
                        "sought_kind": "{_info_record.sought_kind}",
                        "sought_subject": "{_info_record.sought_subject}",
                        "record": "{_info_record}",
                    },
                    QUERY_EXPORTS,
                ),
            ],
        ),
        "exploration",
        "Information",
        [
            ("Present", "_info_found"),
            ("Information", "_info_results"),
            ("Direction", "_info_direction"),
            ("Distance", "_info_distance"),
            ("Movement direction", "_info_movement_direction"),
            ("Perception", "_info_perception"),
        ],
    )
    scanner = _tool_scene(
        "Scan the admitted scene at casting time or on a later active effect query.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("mode"),
            _optional("sought_kind", default=""),
            _optional("sought_subject", default=""),
            _parameter("record", "object"),
        ],
        prepare
        + [
            _set("_query_barrier_rule", "_query_profile.barrier"),
            _when("_query_barrier_rule == 'sight'", [_set("_query_barrier_rule", "'none'")]),
            {
                "condition": "spell == 'arcane_eye' or spell == 'clairvoyance'",
                "on_true": [
                    _call(
                        "perceive_srd_information_proxy",
                        {"caster_state": "{_caster_state_id}", "record": "{record}"},
                        QUERY_EXPORTS,
                    )
                ],
                "on_false": scan,
            },
        ],
    )
    return {
        "scan_srd_information_spell": scanner,
        "qualify_srd_information_candidate": candidate,
        "query_srd_information_spell": query,
    }


CAST_CHOICES = [
    _array("information_position", default=[0, 0, 0]),
    _optional("information_lighting", "string", None),
    _enum("information_sense", ["seeing", "hearing"], "seeing"),
    _optional("information_kind", default=""),
    _optional("information_subject", default=""),
    _optional("information_familiar", "boolean", False),
    _optional("information_seen_close", "boolean", False),
    _optional("information_obvious_place", "boolean", False),
]


def _casting_tools():
    # Ordinary grants use casting facts; an admitted scene supplies additional native checks.
    cast = _tool_scene(
        "Apply the complete information spell after the shared casting owner resolves costs.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("spell"),
            _parameter("caster_can_touch_target", "boolean"),
            _parameter("concentration_modifier", "integer"),
            _parameter("material_item_ids", "array", items={"type": "string"}),
            *deepcopy(CAST_CHOICES),
        ],
        [
            _lookup("spell", "_install_profile"),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_information_origin", None),
            _set("_information_plane", None),
            _when(
                "state.spatial_scene != null and _install_profile.information_mode != 'language'",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_information_origin", "_info_position"),
                    _require(
                        "_info_scene_entity.metadata.plane != null",
                        "The caster needs its actual plane.",
                    ),
                    _set("_information_plane", "_info_scene_entity.metadata.plane"),
                ],
            ),
            _set("_information_target", "_caster_state_id"),
            _when(
                "spell == 'tongues'",
                [
                    _require(
                        "caster_can_touch_target == true", "Tongues requires touching its target."
                    ),
                    _when(
                        "state.spatial_scene != null and _caster_state_id != _target_state_id",
                        [
                            _call(
                                "resolve_srd_scene_geometry_relation",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                },
                                ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
                            ),
                            _set("_information_touch_reach", 5),
                            _when(
                                "caster_state.melee_reach != null",
                                [
                                    _set("_information_touch_reach", "caster_state.melee_reach"),
                                ],
                            ),
                            _require(
                                "_scene_distance <= _information_touch_reach and "
                                "_scene_same_plane == true and _scene_line_of_sight == true",
                                "Tongues needs actual same-plane contact "
                                "within the caster's reach.",
                            ),
                        ],
                    ),
                    _set("_information_target", "_target_state_id"),
                ],
            ),
            _when(
                "spell == 'locate_animals_or_plants' or spell == 'locate_object' or spell == "
                "'locate_creature'",
                [
                    _require(
                        "information_kind != '' or information_subject != ''",
                        "Describe a specific subject or kind to locate.",
                    ),
                    _when(
                        "spell == 'locate_creature' or (spell == 'locate_object' and "
                        "information_subject != '')",
                        [
                            _require(
                                "information_familiar == true and information_seen_close == true",
                                "The source requires familiarity and prior sight within 30 feet.",
                            ),
                        ],
                    ),
                    _when(
                        "spell == 'locate_object'",
                        [
                            _require(
                                "information_familiar == true",
                                "Describe an object or kind familiar to the caster.",
                            )
                        ],
                    ),
                    _when(
                        "spell == 'locate_animals_or_plants'",
                        [
                            _require(
                                "information_kind != '' and information_subject == ''",
                                "Name a kind of Beast or Plant to locate.",
                            ),
                        ],
                    ),
                ],
            ),
            _set("_information_proxy", "''"),
            _when(
                "spell == 'arcane_eye' or spell == 'clairvoyance'",
                [
                    _require(
                        "state.spatial_scene != null",
                        "This sensor needs the admitted spatial scene.",
                    ),
                    _require(
                        "information_lighting in ['bright', 'dim', 'darkness', 'magical_darkness']",
                        "Specify actual ambient lighting at the sensor position.",
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_information_origin}", "end": "{information_position}"},
                        MEASURE_EXPORTS,
                    ),
                    _require(
                        "_scene_distance <= _install_profile.range_feet",
                        "The sensor origin is outside the spell's range.",
                    ),
                    _when(
                        "spell == 'clairvoyance'",
                        [
                            _require(
                                "information_familiar == true or information_obvious_place == true",
                                "Clairvoyance needs a familiar or obvious place.",
                            ),
                            _set("_focus_matches", False),
                            _set("_information_materials", "caster_state.spell_materials"),
                            _loop(
                                "_information_materials",
                                "_information_material",
                                [
                                    _when(
                                        "_information_material.id in material_item_ids and "
                                        "_information_material.value_cp >= 10000 and "
                                        "((information_sense == 'hearing' and "
                                        "'clairvoyance.horn' in _information_material.compone"
                                        "nts) or "
                                        "(information_sense == 'seeing' and "
                                        "'clairvoyance.glass_eye' in "
                                        "_information_material.components))",
                                        [_set("_focus_matches", True)],
                                    ),
                                ],
                            ),
                            _require(
                                "_focus_matches == true",
                                "Use the admitted 100+ GP horn for hearing or glass eye for "
                                "seeing.",
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_install_profile.concentration == true and "
                "caster_state.concentration_spell_id != null",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{caster_state.concentration_target_state_id}",
                        },
                    ),
                ],
            ),
            _when(
                "_install_profile.concentration == true",
                [
                    _call(
                        "remove_srd_information_effects",
                        {"caster_state": "{_caster_state_id}", "spell": "{spell}"},
                    ),
                ],
            ),
            _when(
                "_install_profile.concentration == true",
                [
                    _set("caster_state.concentration_spell_id", "spell"),
                    _set("caster_state.concentration_effect_family", "'information_spell'"),
                    _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                    _set("caster_state.concentration_target_state_ids", []),
                    _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                    _set(
                        "caster_state.concentration_expires_tick",
                        "state.lasting_tick + _install_profile.effect_rounds * 2",
                    ),
                    _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                ],
            ),
            _when(
                "spell == 'arcane_eye' or spell == 'clairvoyance'",
                [
                    _call(
                        "create_srd_information_proxy",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "position": "{information_position}",
                            "lighting": "{information_lighting}",
                            "proxy_id": "{_caster_state_id}:{spell}",
                        },
                        ["_information_proxy"],
                    ),
                ],
            ),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _call(
                "format_srd_information_projection",
                {"message": "{_caster_state_id}:{spell}:{state.lasting_serial}"},
                ["_info_projection_message"],
            ),
            _set("_information_instance_id", "_info_projection_message"),
            _set(
                "_installed_record",
                {
                    "kind": "'information_spell'",
                    "instance_id": "_information_instance_id",
                    "spell": "spell",
                    "label": "spell",
                    "caster": "_caster_state_id",
                    "target": "_information_target",
                    "owner": "'__time_only__'",
                    "phase": "'time'",
                    "turn": 1000000000,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + _install_profile.effect_rounds * 2",
                    "concentration": "_install_profile.concentration",
                    "magnitude": 0,
                    "dice_count": 0,
                    "damage_die": "''",
                    "damage_type": "''",
                    "origin": None,
                    "sought_kind": "information_kind",
                    "sought_subject": "information_subject",
                    "proxy_id": "_information_proxy",
                    "modality": "information_sense",
                    "terrain": "'unchanged'",
                    "plane": None,
                },
            ),
            _when(
                "_information_origin != null",
                [
                    _set(
                        "_information_scene_fields",
                        {"origin": "_information_origin", "plane": "_information_plane"},
                    ),
                    _set(
                        "_installed_record", "merge(_installed_record, _information_scene_fields)"
                    ),
                ],
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            {
                "condition": "_install_profile.effect_rounds == 0",
                "on_true": [
                    _call(
                        "scan_srd_information_spell",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "mode": "sense",
                            "sought_kind": "{information_kind}",
                            "sought_subject": "{information_subject}",
                            "record": "{_installed_record}",
                        },
                        QUERY_EXPORTS,
                    ),
                    _when(
                        "spell == 'find_traps'",
                        [
                            _push(
                                "_spell_detail_rows",
                                {"label": "'Trap present'", "value": "_info_found"},
                            ),
                            _push(
                                "_spell_detail_rows",
                                {"label": "'General danger'", "value": "_info_dangers"},
                            ),
                        ],
                    ),
                    _when(
                        "spell == 'locate_animals_or_plants'",
                        [
                            _push(
                                "_spell_detail_rows", {"label": "'Present'", "value": "_info_found"}
                            ),
                            _push(
                                "_spell_detail_rows",
                                {"label": "'Direction'", "value": "_info_direction"},
                            ),
                            _push(
                                "_spell_detail_rows",
                                {"label": "'Distance'", "value": "_info_distance"},
                            ),
                        ],
                    ),
                ],
                "on_false": [
                    _set("_information_saved", []),
                    _when(
                        "state.turn_effects != null",
                        [_set("_information_saved", "state.turn_effects")],
                    ),
                    _require(
                        "len(_information_saved) < 128",
                        "Resolve existing lasting effects before adding another.",
                    ),
                    _push("_information_saved", "_installed_record"),
                    _set("state.turn_effects", "_information_saved"),
                    _set("state.has_information_spells", True),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "'Effect'",
                            "value": "'Active; use its later information consumer.'",
                        },
                    ),
                    _push(
                        "_spell_detail_rows",
                        {"label": "'Spell instance'", "value": "_information_instance_id"},
                    ),
                ],
            },
        ],
    )
    return {"cast_spell_information_spell": cast}


def _cleanup_tools():
    remove = _tool_scene(
        "Remove exact information sources and deactivate their physical proxy entries.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _optional("instance_id", default=""),
        ],
        [
            _set("_info_remove_prior", []),
            _set("_info_remove_after", []),
            _set("_info_remove_count", 0),
            _set("_info_remaining", False),
            _when("state.turn_effects != null", [_set("_info_remove_prior", "state.turn_effects")]),
            _loop(
                "_info_remove_prior",
                "_info_remove_record",
                [
                    _set("_info_remove_instance", "''"),
                    _when(
                        "_info_remove_record.kind == 'information_spell'",
                        [
                            _call(
                                "read_srd_information_instance",
                                {"record": "{_info_remove_record}"},
                                ["_info_instance_id"],
                            ),
                            _set("_info_remove_instance", "_info_instance_id"),
                        ],
                    ),
                    {
                        "condition": "_info_remove_record.kind == 'information_spell' and "
                        "_info_remove_record.caster == _caster_state_id and "
                        "_info_remove_record.spell == spell and "
                        "(instance_id == '' or _info_remove_instance == instance_id)",
                        "on_true": [
                            _set("_info_remove_count", "_info_remove_count + 1"),
                            _when(
                                "_info_remove_record.proxy_id != ''",
                                [
                                    _call(
                                        "deactivate_srd_information_proxy",
                                        {"proxy_id": "{_info_remove_record.proxy_id}"},
                                    ),
                                ],
                            ),
                        ],
                        "on_false": [
                            _push("_info_remove_after", "_info_remove_record"),
                            _when(
                                "_info_remove_record.kind == 'information_spell'",
                                [_set("_info_remaining", True)],
                            ),
                        ],
                    },
                ],
            ),
            _set("state.turn_effects", "_info_remove_after"),
            _set("state.has_information_spells", "_info_remaining"),
        ],
    )
    refresh = _tool(
        "Expire inactive information effects and their proxies at the actual clock boundary.",
        [],
        [
            _when(
                "state.has_information_spells == true",
                [
                    _set("_refresh_info_records", "state.turn_effects"),
                    _loop(
                        "_refresh_info_records",
                        "_refresh_info_record",
                        [
                            _when(
                                "_refresh_info_record.kind == 'information_spell'",
                                [
                                    _call(
                                        "check_srd_information_record",
                                        {
                                            "caster_state": "{_refresh_info_record.caster}",
                                            "record": "{_refresh_info_record}",
                                        },
                                        ["_info_active"],
                                    ),
                                    _when(
                                        "_info_active != true",
                                        [
                                            _call(
                                                "read_srd_information_instance",
                                                {"record": "{_refresh_info_record}"},
                                                ["_info_instance_id"],
                                            ),
                                            _call(
                                                "remove_srd_information_effects",
                                                {
                                                    "caster_state": "{_refresh_info_record.caster}",
                                                    "spell": "{_refresh_info_record.spell}",
                                                    "instance_id": "{_info_instance_id}",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    dismiss = _public(
        _tool(
            "Dismiss this caster's information spell without an action.",
            [
                _entity("caster_state"),
                _enum("spell", list(PROFILES)),
                _optional("instance_id", default=""),
            ],
            [
                _require(ACTIVE, "An Incapacitated caster cannot dismiss a spell."),
                _call(
                    "collect_srd_information_record",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "instance_id": "{instance_id}",
                    },
                    RECORD_EXPORTS,
                ),
                _require(
                    "_info_active == true", "This caster has no active selected information spell."
                ),
                {
                    "condition": "_info_record.concentration == true",
                    "on_true": [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_caster_state_id}",
                                "target_state": "{_caster_state_id}",
                            },
                        )
                    ],
                    "on_false": [
                        _call(
                            "remove_srd_information_effects",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell": "{spell}",
                                "instance_id": "{_info_instance_id}",
                            },
                        )
                    ],
                },
                _set("_info_dismissed", True),
            ],
        ),
        "exploration",
        "Spell dismissed",
        [("Dismissed", "_info_dismissed")],
    )
    return {
        "remove_srd_information_effects": remove,
        "refresh_srd_information_spells": refresh,
        "dismiss_srd_information_spell": dismiss,
    }


def _clearance_distance_tool():
    """Calculate segment clearance from convex boxes and cylinders."""
    closest = [
        _set(
            "_clearance_point",
            [f"start[{i}] + (end[{i}] - start[{i}]) * _clearance_t" for i in range(3)],
        ),
        {
            "condition": "volume.kind == 'box'",
            "on_true": [
                _set(
                    "_clearance_nearest",
                    [
                        f"min(volume.max[{i}], max(volume.min[{i}], _clearance_point[{i}]))"
                        for i in range(3)
                    ],
                )
            ],
            "on_false": [
                _set(
                    "_clearance_rho",
                    "((_clearance_point[0] - volume.center[0]) ** 2 + (_clearance_point[1] - "
                    "volume.center[1]) ** 2) ** 0.5",
                ),
                _set("_clearance_scale", 1),
                _when(
                    "_clearance_rho > volume.radius",
                    [_set("_clearance_scale", "volume.radius * _clearance_rho ** -1")],
                ),
                _set(
                    "_clearance_nearest",
                    [
                        f"volume.center[{i}] + "
                        f"(_clearance_point[{i}] - volume.center[{i}]) * _clearance_scale"
                        for i in range(2)
                    ]
                    + [
                        "min(volume.center[2] + volume.height, max(volume.center[2], "
                        "_clearance_point[2]))"
                    ],
                ),
            ],
        },
        _set(
            "_clearance_offset",
            [f"_clearance_point[{i}] - _clearance_nearest[{i}]" for i in range(3)],
        ),
    ]
    return _tool(
        "Calculate minimum actual clearance to one admitted solid convex volume.",
        [
            _array("start"),
            _array("end"),
            _parameter("volume", "object", from_state="state.spatial_scene"),
        ],
        [
            _set("_clearance_low", 0),
            _set("_clearance_high", 1),
            _set("_clearance_steps", list(range(48))),
            _loop(
                "_clearance_steps",
                "_clearance_step",
                [
                    _set("_clearance_t", "(_clearance_low + _clearance_high) * 0.5"),
                    *deepcopy(closest),
                    _set(
                        "_clearance_derivative",
                        "_clearance_offset[0] * (end[0] - start[0]) + _clearance_offset[1] * "
                        "(end[1] - start[1]) + _clearance_offset[2] * (end[2] - start[2])",
                    ),
                    {
                        "condition": "_clearance_derivative < 0",
                        "on_true": [_set("_clearance_low", "_clearance_t")],
                        "on_false": [_set("_clearance_high", "_clearance_t")],
                    },
                ],
            ),
            _set("_clearance_t", "(_clearance_low + _clearance_high) * 0.5"),
            *deepcopy(closest),
            _set(
                "_clearance_squared",
                "_clearance_offset[0] ** 2 + _clearance_offset[1] ** 2 + _clearance_offset[2] ** 2",
            ),
        ],
    )


def _proxy_tools():
    create = _tool_scene(
        "Create the saved invisible, invulnerable proxy inside the same admitted scene.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _array("position"),
            _parameter("proxy_id"),
            _parameter("lighting"),
        ],
        _snapshot()
        + [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_information_proxy", "proxy_id"),
            _set(
                "_proxy_metadata",
                {
                    "source_ref": "spell",
                    "source_caster": "_caster_state_id",
                    "information_proxy": "spell",
                    "plane": "_info_scene_entity.metadata.plane",
                    "invisible": True,
                    "invulnerable": True,
                    "hovering": True,
                    "lighting": "lighting",
                },
            ),
            _set(
                "_proxy_entity",
                {
                    "id": "_information_proxy",
                    "map_id": "state.spatial_scene.map.id",
                    "position": "position",
                    "footprint": {"kind": "'circle'", "radius": "1 * 24 ** -1"},
                    "height": "1 * 12 ** -1",
                    "facing": [1, 0],
                    "active": True,
                    "movement": "'passable'",
                    "visibility": "'clear'",
                    "metadata": "_proxy_metadata",
                },
            ),
            _set("_proxy_prior", "state.spatial_scene.entities"),
            _set("_proxy_after", []),
            _loop(
                "_proxy_prior",
                "_proxy_old",
                [
                    _when(
                        "_proxy_old.id != _information_proxy", [_push("_proxy_after", "_proxy_old")]
                    ),
                ],
            ),
            _push("_proxy_after", "_proxy_entity"),
            _set("state.spatial_scene.entities", "_proxy_after"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
        ],
    )
    deactivate = _tool_scene(
        "Deactivate only the exact source's proxy and advance the admitted scene revision.",
        [
            _parameter("proxy_id"),
        ],
        _snapshot()
        + [
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "request": {
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{state.spatial_scene.map.revision}",
                        "target": {"kind": "entity", "id": "{proxy_id}"},
                        "patch": {"active": False},
                    },
                },
                "store_as": "_proxy_deactivated",
            },
        ],
    )
    clearance = _tool_scene(
        "Enforce the one-inch eye clearance against admitted solid box/cylinder barriers.",
        [
            _array("start"),
            _array("end"),
        ],
        _snapshot()
        + [
            _set("_eye_blocked", False),
            _set("_eye_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_eye_geometry",
                "_eye_barrier",
                [
                    _when(
                        "_eye_barrier.active == true and _eye_barrier.movement == 'blocking'",
                        [
                            _set("_eye_volume", "_eye_barrier.volume"),
                            {
                                "condition": "_eye_volume.kind == 'box'",
                                "on_true": [
                                    _set(
                                        "_eye_expanded",
                                        {
                                            "kind": "'box'",
                                            "min": [
                                                f"_eye_volume.min[{i}] - 1 * 24 ** -1 "
                                                "+ 0.000000000001"
                                                for i in range(3)
                                            ],
                                            "max": [
                                                f"_eye_volume.max[{i}] + 1 * 24 ** -1 "
                                                "- 0.000000000001"
                                                for i in range(3)
                                            ],
                                        },
                                    )
                                ],
                                "on_false": [
                                    _set(
                                        "_eye_expanded",
                                        {
                                            "kind": "'cylinder'",
                                            "center": [
                                                "_eye_volume.center[0]",
                                                "_eye_volume.center[1]",
                                                "_eye_volume.center[2] - 1 * 24 ** -1 + "
                                                "0.000000000001",
                                            ],
                                            "radius": "_eye_volume.radius + 1 * 24 ** -1 - "
                                            "0.000000000001",
                                            "height": "_eye_volume.height + 1 * 12 ** -1 - "
                                            "0.000000000002",
                                        },
                                    )
                                ],
                            },
                            _call(
                                "srd_information_segment_interval",
                                {"start": "{start}", "end": "{end}", "volume": "{_eye_expanded}"},
                                ["_segment_hit"],
                            ),
                            _when(
                                "_segment_hit == true",
                                [
                                    _call(
                                        "srd_information_clearance_distance",
                                        {
                                            "start": "{start}",
                                            "end": "{end}",
                                            "volume": "{_eye_barrier.volume}",
                                        },
                                        ["_clearance_squared"],
                                    ),
                                    _when(
                                        "_clearance_squared + 0.000000000001 < (1 * 24 ** -1) ** 2",
                                        [_set("_eye_blocked", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    operation = _public(
        _tool_scene(
            "Move Arcane Eye or switch Clairvoyance's sense using its actual Bonus Action.",
            [
                _entity("caster_state"),
                _enum("spell", ["arcane_eye", "clairvoyance"]),
                _array("destination", default=[0, 0, 0]),
                _enum("modality", ["seeing", "hearing"], "seeing"),
            ],
            [
                _require(ACTIVE, "An Incapacitated caster cannot operate a sensor."),
                _call(
                    "collect_srd_information_record",
                    {"caster_state": "{_caster_state_id}", "spell": "{spell}"},
                    RECORD_EXPORTS,
                ),
                _require("_info_active == true", "The selected sensor spell is not active."),
                _when(
                    "combat.encounter_active == true",
                    [
                        _require(
                            "combat.turn_active == true and combat.active_actor == "
                            "_caster_state_id",
                            "Operate the sensor on its caster's turn.",
                        ),
                        _require(
                            "caster_state.control_confusion_active != true",
                            "Confusion prevents Bonus Actions.",
                        ),
                        _call(
                            "consume_srd_bonus_action",
                            {"actor_state": "{_caster_state_id}", "bonus_action": "{spell}"},
                        ),
                    ],
                ),
                {
                    "condition": "spell == 'arcane_eye'",
                    "on_true": [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_info_record.proxy_id}"},
                            POSITION_EXPORTS,
                        ),
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_info_position}", "end": "{destination}"},
                            MEASURE_EXPORTS,
                        ),
                        _require(
                            "_scene_distance <= 30",
                            "An Arcane Eye Bonus Action moves at most 30 feet.",
                        ),
                        _call(
                            "check_srd_arcane_eye_clearance",
                            {"start": "{_info_position}", "end": "{destination}"},
                            ["_eye_blocked"],
                        ),
                        _require(
                            "_eye_blocked != true",
                            "A solid barrier or opening narrower than one inch blocks the eye.",
                        ),
                        {
                            "spatial_mutation": {
                                "scene": "state.spatial_scene",
                                "request": {
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{state.spatial_scene.map.revision}",
                                    "target": {"kind": "entity", "id": "{_info_record.proxy_id}"},
                                    "patch": {"position": "{destination}"},
                                },
                            },
                            "store_as": "_eye_movement",
                        },
                        _set("_operated_position", "destination"),
                    ],
                    "on_false": [
                        _call(
                            "update_srd_information_record",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell": "{spell}",
                                "modality": "{modality}",
                                "terrain": "{_info_record.terrain}",
                                "instance_id": "{_info_instance_id}",
                            },
                        ),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_info_record.proxy_id}"},
                            POSITION_EXPORTS,
                        ),
                        _require(
                            "(destination[0] == 0 and destination[1] == 0 and destination[2] "
                            "== 0) or "
                            "(destination[0] == _info_position[0] and destination[1] == "
                            "_info_position[1] and destination[2] == _info_position[2])",
                            "Clairvoyance's fixed sensor cannot move.",
                        ),
                        _set("_operated_position", "_info_position"),
                    ],
                },
                _set("_operated_modality", "modality"),
            ],
        ),
        "exploration",
        "Sensor operated",
        [("Position", "_operated_position"), ("Sense", "_operated_modality")],
    )
    perceive = _tool_scene(
        "Receive only sight/hearing available at the source's actual sensor position.",
        [
            _entity("caster_state"),
            _parameter("record", "object"),
        ],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{record.proxy_id}"}, POSITION_EXPORTS
            ),
            _set("_remote_origin", "_info_position"),
            _set("_remote_plane", "_info_scene_entity.metadata.plane"),
            _set("_remote_entities", "state.spatial_scene.entities"),
            _set("_info_results", []),
            _set("_info_found", False),
            _set("_info_perception", "record.modality"),
            _set("_info_dangers", []),
            _set("_info_distance", None),
            _set("_info_direction", []),
            _set("_info_movement_direction", []),
            _set("_info_orb", False),
            _loop(
                "_remote_entities",
                "_remote_entity",
                [
                    _when(
                        "_remote_entity.active == true and _remote_entity.id != "
                        "record.proxy_id and "
                        "(_remote_entity.metadata.plane == _remote_plane or (record.spell == "
                        "'clairvoyance' and _remote_plane == 'material' and "
                        "_remote_entity.metadata.plane == 'ethereal'))",
                        [
                            _set("_remote_visible", False),
                            _set("_remote_invisible", "_remote_entity.metadata.invisible == true"),
                            _set("_remote_subject", None),
                            _when(
                                "_remote_entity.metadata.participant_ref != null",
                                [
                                    _set(
                                        "_remote_subject", "_remote_entity.metadata.participant_ref"
                                    ),
                                    _call(
                                        "resolve_srd_information_subject_invisibility",
                                        {
                                            "subject_state": "{_remote_entity.metadata."
                                            "participant_ref}"
                                        },
                                        ["_information_subject_invisible"],
                                    ),
                                    _set(
                                        "_remote_invisible",
                                        "_remote_invisible == true or "
                                        "_information_subject_invisible == true",
                                    ),
                                ],
                            ),
                            {
                                "condition": "record.modality == 'hearing' and record.spell == "
                                "'clairvoyance'",
                                "on_true": [
                                    _set(
                                        "_remote_visible",
                                        "'deafened' not in caster_state.conditions and "
                                        "_remote_entity.metadata.audible_to != null and "
                                        "record.proxy_id in _remote_entity.metadata.audible_t"
                                        "o",
                                    )
                                ],
                                "on_false": [
                                    _spatial(
                                        "exposure",
                                        {
                                            "map_id": "{state.spatial_scene.map.id}",
                                            "observer": {"point": "{_remote_origin}"},
                                            "target": {"entity_id": "{_remote_entity.id}"},
                                            "samples": {"horizontal": 3, "vertical": 3},
                                            "include": ["geometry"],
                                        },
                                        "_remote_exposure",
                                    ),
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_remote_origin}",
                                            "end": "{_remote_entity.position}",
                                        },
                                        MEASURE_EXPORTS,
                                    ),
                                    _require(
                                        "_remote_entity.metadata.lighting != null",
                                        "Remote sight needs actual light at its subject.",
                                    ),
                                    _call(
                                        "resolve_srd_information_light",
                                        {
                                            "point": "{_remote_entity.position}",
                                            "plane": "{_remote_entity.metadata.plane}",
                                            "ambient": "{_remote_entity.metadata.lighting}",
                                        },
                                        ["_information_light"],
                                    ),
                                    {
                                        "condition": "record.spell == 'arcane_eye'",
                                        "on_true": [
                                            _set(
                                                "_remote_visible",
                                                "_remote_exposure.visible_samples > 0 and "
                                                "_remote_invisible != true and "
                                                "(_information_light in ['bright', 'dim'] or "
                                                "(_information_light == 'darkness' and "
                                                "_scene_distance <= 30))",
                                            )
                                        ],
                                        "on_false": [
                                            _call(
                                                "resolve_srd_sight",
                                                {
                                                    "observer_state": "{_caster_state_id}",
                                                    "distance": "{ceil(_scene_distance)}",
                                                    "lighting": "{_information_light}",
                                                    "obstruction": "clear",
                                                    "subject_state": "{_remote_subject}",
                                                    "subject_invisible": "{_remote_invisible}",
                                                    "subject_ethereal": (
                                                        "{_remote_entity.metadata.plane "
                                                        "!= _remote_plane}"
                                                    ),
                                                },
                                                ["_sight_can_see"],
                                            ),
                                            _set(
                                                "_remote_visible",
                                                "_sight_can_see == true and "
                                                "_remote_exposure.visible_samples > 0",
                                            ),
                                        ],
                                    },
                                ],
                            },
                            _when(
                                "_remote_visible == true and _remote_entity.metadata.particip"
                                "ant_ref != null",
                                [
                                    _call(
                                        "check_srd_mind_blank_information",
                                        {
                                            "target_state": "{_remote_entity.metadata."
                                            "participant_ref}",
                                            "query_kind": "remote_observation",
                                        },
                                        ["_mind_blank_information_blocked"],
                                    ),
                                    _set(
                                        "_remote_visible", "_mind_blank_information_blocked != true"
                                    ),
                                ],
                            ),
                            _when(
                                "_remote_visible == true",
                                [
                                    _call(
                                        "format_srd_information_projection",
                                        {"message": "{_remote_entity.id}: {record.modality}"},
                                        ["_info_projection_message"],
                                    ),
                                    _push("_info_results", "_info_projection_message"),
                                    _set("_info_found", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    observe = _public(
        _tool_scene(
            "Observe an active invisible sensor with actual observer senses and scene obstruction.",
            [
                _entity("observer_state"),
                _parameter("proxy_id"),
            ],
            [
                _call("srd_information_scene_entity", {"subject": "{proxy_id}"}, POSITION_EXPORTS),
                _set("_observed_proxy", "_info_scene_entity"),
                _require(
                    "_observed_proxy.metadata.information_proxy != null",
                    "Select an information sensor.",
                ),
                _call(
                    "collect_srd_information_record",
                    {
                        "caster_state": "{_observed_proxy.metadata.source_caster}",
                        "spell": "{_observed_proxy.metadata.information_proxy}",
                    },
                    RECORD_EXPORTS,
                ),
                _require("_info_active == true", "The sensor spell has ended."),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_observer_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_proxy_observer_plane", "_info_scene_entity.metadata.plane"),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_info_position}", "end": "{_observed_proxy.position}"},
                    MEASURE_EXPORTS,
                ),
                _spatial(
                    "exposure",
                    {
                        "map_id": "{state.spatial_scene.map.id}",
                        "observer": {"point": "{_info_position}"},
                        "target": {"entity_id": "{proxy_id}"},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry"],
                    },
                    "_proxy_exposure",
                ),
                _set("_proxy_observer_distance", "_scene_distance"),
                _call(
                    "resolve_srd_information_light",
                    {
                        "point": "{_observed_proxy.position}",
                        "plane": "{_observed_proxy.metadata.plane}",
                        "ambient": "{_observed_proxy.metadata.lighting}",
                    },
                    ["_information_light"],
                ),
                _when(
                    "_observed_proxy.metadata.information_proxy == 'clairvoyance'",
                    [_set("_information_light", "'bright'")],
                ),
                _call(
                    "resolve_srd_sight",
                    {
                        "observer_state": "{_observer_state_id}",
                        "distance": "{ceil(_proxy_observer_distance)}",
                        "lighting": "{_information_light}",
                        "obstruction": "clear",
                        "subject_invisible": True,
                        "subject_ethereal": "{_proxy_observer_plane != "
                        "_observed_proxy.metadata.plane}",
                    },
                    ["_sight_can_see"],
                ),
                _set(
                    "_proxy_observed",
                    "_sight_can_see == true and _proxy_exposure.visible_samples > 0 and "
                    "(_proxy_observer_plane == _observed_proxy.metadata.plane or "
                    "(_proxy_observer_plane == 'material' and "
                    "_observed_proxy.metadata.plane == 'ethereal'))",
                ),
                _set(
                    "_info_orb",
                    "_proxy_observed == true and _observed_proxy.metadata.information_proxy "
                    "== 'clairvoyance'",
                ),
            ],
        ),
        "exploration",
        "Sensor observation",
        [("Visible", "_proxy_observed"), ("Luminous fist-size orb", "_info_orb")],
    )
    return {
        "srd_information_clearance_distance": _clearance_distance_tool(),
        "create_srd_information_proxy": create,
        "deactivate_srd_information_proxy": deactivate,
        "check_srd_arcane_eye_clearance": clearance,
        "operate_srd_information_proxy": operation,
        "perceive_srd_information_proxy": perceive,
        "observe_srd_information_proxy": observe,
    }


def _update_record_tool():
    return _tool(
        "Update only the exact caster's source-owned sensor modality or plants terrain choice.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("modality"),
            _parameter("terrain"),
            _optional("instance_id", default=""),
        ],
        [
            _set("_update_info_prior", "state.turn_effects"),
            _set("_update_info_after", []),
            _loop(
                "_update_info_prior",
                "_update_info_record",
                [
                    _set("_update_info_instance", "''"),
                    _when(
                        "_update_info_record.kind == 'information_spell'",
                        [
                            _call(
                                "read_srd_information_instance",
                                {"record": "{_update_info_record}"},
                                ["_info_instance_id"],
                            ),
                            _set("_update_info_instance", "_info_instance_id"),
                        ],
                    ),
                    {
                        "condition": "_update_info_record.kind == 'information_spell' and "
                        "_update_info_record.caster == _caster_state_id and "
                        "_update_info_record.spell == spell and "
                        "(instance_id == '' or _update_info_instance == instance_id)",
                        "on_true": [
                            _set("_update_patch", {"modality": "modality", "terrain": "terrain"}),
                            _push(
                                "_update_info_after", "merge(_update_info_record, _update_patch)"
                            ),
                        ],
                        "on_false": [_push("_update_info_after", "_update_info_record")],
                    },
                ],
            ),
            _set("state.turn_effects", "_update_info_after"),
        ],
    )


def _language_tools():
    collect = _tool(
        "Collect active incoming/outgoing Tongues and source-owned Beast/Plant speech grants.",
        [
            _entity("target_state"),
        ],
        [
            _set("_language_tongues", False),
            _set("_language_animals", False),
            _set("_language_plants", []),
            _set("_language_records", []),
            _when("state.turn_effects != null", [_set("_language_records", "state.turn_effects")]),
            _loop(
                "_language_records",
                "_language_record",
                [
                    _when(
                        "_language_record.kind == 'information_spell' and "
                        "_language_record.target == _target_state_id",
                        [
                            _call(
                                "check_srd_information_record",
                                {
                                    "caster_state": "{_language_record.caster}",
                                    "record": "{_language_record}",
                                },
                                ["_info_active"],
                            ),
                            _when(
                                "_info_active == true",
                                [
                                    _when(
                                        "_language_record.spell == 'tongues'",
                                        [_set("_language_tongues", True)],
                                    ),
                                    _when(
                                        "_language_record.spell == 'speak_with_animals'",
                                        [_set("_language_animals", True)],
                                    ),
                                    _when(
                                        "_language_record.spell == 'speak_with_plants'",
                                        [_push("_language_plants", "_language_record")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    language = _tool(
        "Resolve actual perceived speech/signs using native and source-owned comprehension.",
        [
            _entity("listener_state"),
            _entity("speaker_state"),
            _enum("channel", ["speech", "signs"], "speech"),
            _optional("perceived", "boolean", True),
            _optional("language", default=""),
        ],
        [
            _set("_language_understands", False),
            _set("_language_can_reply", False),
            _set("_language_influence_all_skills", False),
            _set(
                "_language_perceived",
                "perceived == true and ((channel == 'speech' and 'deafened' "
                "not in listener_state.conditions) or (channel == 'signs' and 'blinded' not "
                "in listener_state.conditions))",
            ),
            _set("_language_listener_native", []),
            _set("_language_speaker_native", []),
            _when(
                "listener_state.languages != null",
                [_set("_language_listener_native", "listener_state.languages")],
            ),
            _when(
                "speaker_state.languages != null",
                [_set("_language_speaker_native", "speaker_state.languages")],
            ),
            _set("_utterance_language", "language"),
            _when(
                "language == '' and len(_language_speaker_native) == 1",
                [_set("_utterance_language", "_language_speaker_native[0]")],
            ),
            _loop(
                "_language_listener_native",
                "_native_language",
                [
                    _when(
                        "_utterance_language != '' and _native_language == "
                        "_utterance_language and _native_language in _language_speaker_native",
                        [_set("_language_understands", True), _set("_language_can_reply", True)],
                    ),
                ],
            ),
            _call(
                "collect_srd_information_language",
                {"target_state": "{_listener_state_id}"},
                ["_language_tongues", "_language_animals", "_language_plants"],
            ),
            _set("_listener_tongues", "_language_tongues"),
            _when(
                "_language_tongues == true and (_utterance_language != '' or "
                "len(_language_speaker_native) > 0)",
                [
                    _set("_language_understands", True),
                    _set("_language_can_reply", True),
                ],
            ),
            _call(
                "resolve_srd_timed_senses",
                {"target_state": "{_listener_state_id}"},
                ["_sense_comprehension"],
            ),
            _when(
                "_sense_comprehension == true and channel == 'speech' and "
                "(_utterance_language != '' or len(_language_speaker_native) > 0)",
                [_set("_language_understands", True)],
            ),
            _when(
                "_language_animals == true and speaker_state.creature_type == 'beast' and "
                "channel == 'speech'",
                [
                    _set("_language_understands", True),
                    _set("_language_can_reply", True),
                    _set("_language_influence_all_skills", True),
                ],
            ),
            _when(
                "len(_language_plants) > 0 and speaker_state.creature_type == 'plant'",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_speaker_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _loop(
                        "_language_plants",
                        "_language_plant_record",
                        [
                            _call(
                                "measure_srd_information_distance",
                                {
                                    "start": "{_language_plant_record.origin}",
                                    "end": "{_info_position}",
                                },
                                MEASURE_EXPORTS,
                            ),
                            _when(
                                "_scene_distance <= 30 and _info_scene_entity.metadata.plane "
                                "== _language_plant_record.plane",
                                [
                                    _set("_language_understands", True),
                                    _set("_language_can_reply", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "collect_srd_information_language",
                {"target_state": "{_speaker_state_id}"},
                ["_language_tongues"],
            ),
            _when(
                "_language_tongues == true and len(_language_listener_native) > 0",
                [_set("_language_understands", True)],
            ),
            _set(
                "_language_understands",
                "_language_understands == true and _language_perceived == true",
            ),
            _set(
                "_language_can_reply", "_language_can_reply == true and _language_perceived == true"
            ),
            _set(
                "_language_influence_all_skills",
                "_language_influence_all_skills == true and _language_perceived == true",
            ),
        ],
    )
    communicate = _public(
        _tool(
            "Consume spell-enabled communication and source-bounded information eligibility.",
            [
                _entity("listener_state"),
                _entity("speaker_state"),
                _enum("channel", ["speech", "signs"], "speech"),
                _optional("perceived", "boolean", True),
                _optional("language", default=""),
                _enum(
                    "purpose",
                    ["conversation", "nearby_places", "nearby_monsters", "past_day", "influence"],
                    "conversation",
                ),
                _optional("hours_ago", "number", 0, minimum=0),
                _optional("information_about", "string", None, resolve="entity"),
                _optional("actor", "string", None, resolve="participant"),
                _enum(
                    "influence_skill",
                    ["animal_handling", "deception", "intimidation", "persuasion", "performance"],
                    "persuasion",
                ),
                _optional("target_number", "integer", 10, minimum=0),
                _optional("circumstantial_modifier", "integer", 0),
                _optional("advantage", "boolean", False),
                _optional("disadvantage", "boolean", False),
            ],
            [
                _call(
                    "resolve_srd_spell_language",
                    {
                        "listener_state": "{_listener_state_id}",
                        "speaker_state": "{_speaker_state_id}",
                        "channel": "{channel}",
                        "perceived": "{perceived}",
                        "language": "{language}",
                    },
                    [
                        "_language_understands",
                        "_language_can_reply",
                        "_language_influence_all_skills",
                    ],
                ),
                _require(
                    "_language_understands == true",
                    "The listener cannot understand this actual speech/signing.",
                ),
                _require(
                    "purpose == 'conversation' or _language_influence_all_skills == true",
                    "Guaranteed Beast information requires active verbal Speak with Animals.",
                ),
                _require(
                    "purpose != 'past_day' or hours_ago <= 24",
                    "The spell's guaranteed information covers the past day.",
                ),
                _require(
                    "purpose != 'influence' or _language_influence_all_skills == true",
                    "Every Influence skill option requires active verbal Beast communication.",
                ),
                _set("_language_information_allowed", True),
                _when(
                    "information_about != null",
                    [
                        _call(
                            "check_srd_mind_blank_information",
                            {
                                "target_state": "{_information_about_id}",
                                "query_kind": "information_gathering",
                            },
                            ["_mind_blank_information_blocked"],
                        ),
                        _set(
                            "_language_information_allowed",
                            "_mind_blank_information_blocked != true",
                        ),
                    ],
                ),
                _set("_language_influence_total", None),
                _set("_language_influence_outcome", None),
                _when(
                    "purpose == 'influence'",
                    [
                        _require(
                            "not ('incapacitated' in listener_state.conditions or 'paralyzed' "
                            "in listener_state.conditions or 'petrified' "
                            "in listener_state.conditions "
                            "or 'stunned' in listener_state.conditions or 'unconscious' "
                            "in listener_state.conditions)",
                            "An incapacitated creature cannot take the Influence action.",
                        ),
                        _require(
                            "combat.encounter_active != true or (combat.turn_active == true "
                            "and combat.active_actor == _listener_state_id)",
                            "Take the Influence action on the communicating creature's "
                            "active turn.",
                        ),
                        {
                            "condition": "actor != null",
                            "on_true": [
                                _require(
                                    "actor.participant_id == _listener_state_id",
                                    "The admitted Influence actor must be the "
                                    "spell-enabled listener.",
                                ),
                                _set("_language_proficiency", "'none'"),
                                _when(
                                    "actor.data.skill_proficiencies != null and "
                                    "influence_skill in actor.data.skill_proficiencies",
                                    [_set("_language_proficiency", "'normal'")],
                                ),
                                _set("_language_check_ability", "'charisma'"),
                                _when(
                                    "influence_skill == 'animal_handling'",
                                    [_set("_language_check_ability", "'wisdom'")],
                                ),
                                {
                                    "condition": "combat.encounter_active == true",
                                    "on_true": [
                                        _call(
                                            "influence_srd_action",
                                            {
                                                "actor": "{_actor_id}",
                                                "actor_state": "{_listener_state_id}",
                                                "skill": "{influence_skill}",
                                                "proficiency_factor": "{_language_proficiency}",
                                                "target_number": "{target_number}",
                                                "circumstantial_modifier": (
                                                    "{circumstantial_modifier}"
                                                ),
                                                "advantage": "{advantage}",
                                                "disadvantage": "{disadvantage}",
                                            },
                                            ["_d20_total", "_d20_outcome"],
                                        )
                                    ],
                                    "on_false": [
                                        _call(
                                            "d20_test",
                                            {
                                                "actor": "{_actor_id}",
                                                "actor_state": "{_listener_state_id}",
                                                "ability": "{_language_check_ability}",
                                                "skill": "{influence_skill}",
                                                "kind": "ability_check",
                                                "proficiency_factor": "{_language_proficiency}",
                                                "target_number": "{target_number}",
                                                "circumstantial_modifier": (
                                                    "{circumstantial_modifier}"
                                                ),
                                                "advantage": "{advantage}",
                                                "disadvantage": "{disadvantage}",
                                            },
                                            ["_adjusted_total", "_outcome"],
                                        ),
                                        _set("_d20_total", "_adjusted_total"),
                                        _set("_d20_outcome", "_outcome"),
                                    ],
                                },
                            ],
                            "on_false": [
                                _require(
                                    "listener_state.creature_kind != 'character'",
                                    "A saved Player Character must use its admitted "
                                    "participant for Influence.",
                                ),
                                _set("_language_check_ability", "'charisma'"),
                                _when(
                                    "influence_skill == 'animal_handling'",
                                    [_set("_language_check_ability", "'wisdom'")],
                                ),
                                _require(
                                    "listener_state.skill_check_modifiers != null and "
                                    "listener_state.skill_check_modifiers[influence_skill] "
                                    "!= null",
                                    "Record the NPC's actual stat-block skill modifier "
                                    "before Influence.",
                                ),
                                _set(
                                    "_language_npc_modifier",
                                    "listener_state.skill_check_modifiers[influence_skill]",
                                ),
                                _when(
                                    "combat.encounter_active == true",
                                    [
                                        _call("spend_srd_action", {"magic_action": False}),
                                    ],
                                ),
                                _call(
                                    "d20_test",
                                    {
                                        "actor_state": "{_listener_state_id}",
                                        "ability": "{_language_check_ability}",
                                        "skill": "{influence_skill}",
                                        "kind": "ability_check",
                                        "ability_modifier": "{_language_npc_modifier}",
                                        "proficiency_bonus": 0,
                                        "proficiency_factor": "none",
                                        "target_number": "{target_number}",
                                        "circumstantial_modifier": ("{circumstantial_modifier}"),
                                        "advantage": "{advantage}",
                                        "disadvantage": "{disadvantage}",
                                    },
                                    ["_adjusted_total", "_outcome"],
                                ),
                                _set("_d20_total", "_adjusted_total"),
                                _set("_d20_outcome", "_outcome"),
                            ],
                        },
                        _set("_language_influence_total", "_d20_total"),
                        _set("_language_influence_outcome", "_d20_outcome"),
                    ],
                ),
            ],
        ),
        "social",
        "Communication",
        [
            ("Understands", "_language_understands"),
            ("Can reply", "_language_can_reply"),
            ("All Influence skills", "_language_influence_all_skills"),
            ("Information available", "_language_information_allowed"),
            ("Influence total", "_language_influence_total"),
            ("Influence outcome", "_language_influence_outcome"),
        ],
    )
    return {
        "collect_srd_information_language": collect,
        "resolve_srd_spell_language": language,
        "communicate_srd_spell": communicate,
    }


def _sphere_interval_tool():
    return _tool(
        "Calculate a movement segment's interval inside a fixed thirty-foot Emanation.",
        [
            _array("start"),
            _array("end"),
            _array("origin"),
        ],
        [
            _set("_sphere_delta", [f"end[{i}] - start[{i}]" for i in range(3)]),
            _set("_sphere_offset", [f"start[{i}] - origin[{i}]" for i in range(3)]),
            _set(
                "_sphere_a", "_sphere_delta[0] ** 2 + _sphere_delta[1] ** 2 + _sphere_delta[2] ** 2"
            ),
            _set(
                "_sphere_b",
                "2 * (_sphere_offset[0] * _sphere_delta[0] + _sphere_offset[1] * "
                "_sphere_delta[1] + _sphere_offset[2] * _sphere_delta[2])",
            ),
            _set(
                "_sphere_c",
                "_sphere_offset[0] ** 2 + _sphere_offset[1] ** 2 + _sphere_offset[2] ** 2 - 900",
            ),
            _set("_sphere_entry", 0),
            _set("_sphere_exit", 1),
            _set("_sphere_hit", False),
            {
                "condition": "_sphere_a < 0.000000000001",
                "on_true": [_set("_sphere_hit", "_sphere_c <= 0")],
                "on_false": [
                    _set("_sphere_d", "_sphere_b ** 2 - 4 * _sphere_a * _sphere_c"),
                    _when(
                        "_sphere_d >= 0",
                        [
                            _set(
                                "_sphere_entry",
                                "max(0, (-_sphere_b - _sphere_d ** 0.5) * (2 * _sphere_a) ** -1)",
                            ),
                            _set(
                                "_sphere_exit",
                                "min(1, (-_sphere_b + _sphere_d ** 0.5) * (2 * _sphere_a) ** -1)",
                            ),
                            _set("_sphere_hit", "_sphere_entry <= _sphere_exit"),
                        ],
                    ),
                ],
            },
        ],
    )


def _plant_tools():
    command = _public(
        _tool_scene(
            "Issue only source-permitted commands within the immobile plants Emanation.",
            [
                _entity("caster_state"),
                _enum(
                    "command",
                    [
                        "question",
                        "move_branches",
                        "move_tendrils",
                        "move_stalks",
                        "ordinary_terrain",
                        "difficult_terrain",
                        "uproot",
                        "move_plant",
                    ],
                ),
                _optional("plant_id", default=""),
                _optional("instance_id", default=""),
                _optional("plant_part_id", default=""),
                _array("destination", default=[0, 0, 0]),
                _optional("hours_ago", "number", 0, minimum=0),
                _optional("information_about", "string", None, resolve="entity"),
            ],
            [
                _call(
                    "collect_srd_information_record",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "speak_with_plants",
                        "instance_id": "{instance_id}",
                    },
                    RECORD_EXPORTS,
                ),
                _require("_info_active == true", "Speak with Plants is not active."),
                _require(
                    "command != 'uproot' and command != 'move_plant'",
                    "The spell cannot uproot plants or move them about.",
                ),
                _set("_plant_eligible", True),
                _set("_plant_events", []),
                _when(
                    "command in ['question', 'move_branches', 'move_tendrils', 'move_stalks']",
                    [
                        _require("plant_id != ''", "Select a plant inside the Emanation."),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{plant_id}"},
                            POSITION_EXPORTS,
                        ),
                        _require(
                            "_info_scene_entity.metadata.is_plant == true or "
                            "_info_scene_entity.metadata.nonmagical_plant == true or "
                            "_info_scene_entity.metadata.creature_type == 'plant'",
                            "The selected scene subject is not a plant.",
                        ),
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_info_record.origin}", "end": "{_info_position}"},
                            MEASURE_EXPORTS,
                        ),
                        _require(
                            "_scene_distance <= 30 and _info_scene_entity.metadata.plane "
                            "== _info_record.plane",
                            "The plant is outside the fixed Emanation.",
                        ),
                        _set("_commanded_plant", "_info_scene_entity"),
                        _require(
                            "command != 'question' or hours_ago <= 24",
                            "The spell's guaranteed questions cover the past day.",
                        ),
                        _when(
                            "information_about != null",
                            [
                                _call(
                                    "check_srd_mind_blank_information",
                                    {
                                        "target_state": "{_information_about_id}",
                                        "query_kind": "information_gathering",
                                    },
                                    ["_mind_blank_information_blocked"],
                                ),
                                _set("_plant_eligible", "_mind_blank_information_blocked != true"),
                            ],
                        ),
                        _when(
                            "command == 'question' and _commanded_plant.metadata.plant_events "
                            "!= null and _plant_eligible == true",
                            [
                                _set(
                                    "_plant_event_facts", "_commanded_plant.metadata.plant_events"
                                ),
                                _loop(
                                    "_plant_event_facts",
                                    "_plant_event",
                                    [
                                        _require(
                                            "_plant_event.position != null and "
                                            "_plant_event.tick != null "
                                            "and _plant_event.description != null",
                                            "Recorded plant events need their actual "
                                            "position and clock.",
                                        ),
                                        _call(
                                            "measure_srd_information_distance",
                                            {
                                                "start": "{_info_record.origin}",
                                                "end": "{_plant_event.position}",
                                            },
                                            MEASURE_EXPORTS,
                                        ),
                                        _set(
                                            "_plant_event_allowed",
                                            "_scene_distance <= 30 and "
                                            "_plant_event.tick <= state.lasting_tick and "
                                            "state.lasting_tick - _plant_event.tick <= 28800",
                                        ),
                                        _when(
                                            "_plant_event_allowed == true and "
                                            "_plant_event.subject_ref != null",
                                            [
                                                _call(
                                                    "check_srd_mind_blank_information",
                                                    {
                                                        "target_state": (
                                                            "{_plant_event.subject_ref}"
                                                        ),
                                                        "query_kind": "information_gathering",
                                                    },
                                                    ["_mind_blank_information_blocked"],
                                                ),
                                                _set(
                                                    "_plant_event_allowed",
                                                    "_mind_blank_information_blocked != true",
                                                ),
                                            ],
                                        ),
                                        _when(
                                            "_plant_event_allowed == true",
                                            [
                                                _push("_plant_events", "_plant_event.description"),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "command == 'ordinary_terrain' or command == 'difficult_terrain'",
                    [
                        _call(
                            "update_srd_information_record",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell": "speak_with_plants",
                                "modality": "{_info_record.modality}",
                                "terrain": "{command}",
                                "instance_id": "{_info_instance_id}",
                            },
                        ),
                    ],
                ),
                _when(
                    "command in ['move_branches', 'move_tendrils', 'move_stalks']",
                    [
                        _require(
                            "plant_part_id != ''",
                            "Select the admitted branch, tendril or stalk to move.",
                        ),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{plant_part_id}"},
                            POSITION_EXPORTS,
                        ),
                        _require(
                            "_info_scene_entity.id != _commanded_plant.id and "
                            "_info_scene_entity.metadata.plant_owner == _commanded_plant.id",
                            "Move only a part owned by this rooted plant.",
                        ),
                        _require(
                            "(command == 'move_branches' and _info_scene_entity.metadata.plan"
                            "t_part == 'branch') or "
                            "(command == 'move_tendrils' and _info_scene_entity.metadata.plan"
                            "t_part == 'tendril') or "
                            "(command == 'move_stalks' and _info_scene_entity.metadata.plant_"
                            "part == 'stalk')",
                            "Select the actual kind of plant part being commanded.",
                        ),
                        _require(
                            "_info_scene_entity.metadata.reach_feet != null",
                            "The admitted plant part needs its natural reach.",
                        ),
                        _set("_plant_part", "_info_scene_entity"),
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_commanded_plant.position}", "end": "{destination}"},
                            MEASURE_EXPORTS,
                        ),
                        _require(
                            "_scene_distance <= _plant_part.metadata.reach_feet",
                            "The command exceeds this plant part's actual reach.",
                        ),
                        _call(
                            "srd_information_barriers",
                            {
                                "start": "{_plant_part.position}",
                                "end": "{destination}",
                                "rule": "solid",
                            },
                            ["_information_blocked"],
                        ),
                        _require(
                            "_information_blocked != true",
                            "The commanded part cannot pass through a solid barrier.",
                        ),
                        {
                            "spatial_mutation": {
                                "scene": "state.spatial_scene",
                                "request": {
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{state.spatial_scene.map.revision}",
                                    "target": {"kind": "entity", "id": "{_plant_part.id}"},
                                    "patch": {"position": "{destination}"},
                                },
                            },
                            "store_as": "_plant_part_movement",
                        },
                    ],
                ),
                _set("_plant_command", "command"),
            ],
        ),
        "exploration",
        "Plant command",
        [
            ("Command", "_plant_command"),
            ("Information available", "_plant_eligible"),
            ("Past-day events in the area", "_plant_events"),
        ],
    )
    cost = _tool_scene(
        "Integrate source plant terrain changes over the actual admitted movement segment.",
        [
            _array("start"),
            _array("end"),
            _parameter("base_difficult", "boolean"),
            _parameter("plane"),
        ],
        _snapshot()
        + [
            _call(
                "measure_srd_information_distance",
                {"start": "{start}", "end": "{end}"},
                MEASURE_EXPORTS,
            ),
            _set("_route_distance", "_scene_distance"),
            _set("_route_cost", 0),
            _set("_route_cuts", [{"t": 0}, {"t": 1}]),
            _set("_route_zones", "state.spatial_scene.zones"),
            _set("_route_plant_records", []),
            _set("_route_prior", []),
            _when("state.turn_effects != null", [_set("_route_prior", "state.turn_effects")]),
            _loop(
                "_route_prior",
                "_route_record",
                [
                    _when(
                        "_route_record.kind == 'information_spell' and _route_record.spell "
                        "== 'speak_with_plants' "
                        "and state.lasting_tick < _route_record.expires_tick and "
                        "_route_record.terrain != 'unchanged' and _route_record.plane == "
                        "plane",
                        [
                            _push("_route_plant_records", "_route_record"),
                            _call(
                                "srd_plants_route_interval",
                                {
                                    "start": "{start}",
                                    "end": "{end}",
                                    "origin": "{_route_record.origin}",
                                },
                                ["_sphere_entry", "_sphere_exit", "_sphere_hit"],
                            ),
                            _when(
                                "_sphere_hit == true",
                                [
                                    _push("_route_cuts", {"t": "_sphere_entry"}),
                                    _push("_route_cuts", {"t": "_sphere_exit"}),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _loop(
                "_route_zones",
                "_route_zone",
                [
                    _call(
                        "srd_information_segment_interval",
                        {"start": "{start}", "end": "{end}", "volume": "{_route_zone.shape}"},
                        ["_segment_entry", "_segment_exit", "_segment_hit"],
                    ),
                    _when(
                        "_segment_hit == true",
                        [
                            _push("_route_cuts", {"t": "_segment_entry"}),
                            _push("_route_cuts", {"t": "_segment_exit"}),
                        ],
                    ),
                ],
            ),
            {"sort": {"collection": "_route_cuts", "by": "t", "direction": "asc"}},
            _set("_route_previous", 0),
            _loop(
                "_route_cuts",
                "_route_cut",
                [
                    _when(
                        "_route_cut.t > _route_previous",
                        [
                            _set("_route_mid", "(_route_cut.t + _route_previous) * 0.5"),
                            _set(
                                "_route_point",
                                [
                                    f"start[{i}] + (end[{i}] - start[{i}]) * _route_mid"
                                    for i in range(3)
                                ],
                            ),
                            _set("_route_piece_difficult", "base_difficult"),
                            _set("_route_nonplant_difficult", False),
                            _set("_route_growth_difficult", False),
                            _set("_route_plants", False),
                            _loop(
                                "_route_zones",
                                "_route_zone",
                                [
                                    _call(
                                        "srd_information_segment_interval",
                                        {
                                            "start": "{_route_point}",
                                            "end": "{_route_point}",
                                            "volume": "{_route_zone.shape}",
                                        },
                                        ["_segment_hit"],
                                    ),
                                    _when(
                                        "_segment_hit == true and "
                                        "_route_zone.metadata.plane == plane",
                                        [
                                            _when(
                                                "_route_zone.metadata.plants == true",
                                                [_set("_route_plants", True)],
                                            ),
                                            _when(
                                                "_route_zone.metadata.difficult == true",
                                                [
                                                    _set("_route_piece_difficult", True),
                                                    {
                                                        "condition": "_route_zone.metadata."
                                                        "plant_growth == true",
                                                        "on_true": [
                                                            _set("_route_growth_difficult", True)
                                                        ],
                                                        "on_false": [
                                                            _set("_route_nonplant_difficult", True)
                                                        ],
                                                    },
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _loop(
                                "_route_plant_records",
                                "_route_plant",
                                [
                                    _call(
                                        "measure_srd_information_distance",
                                        {"start": "{_route_plant.origin}", "end": "{_route_point}"},
                                        MEASURE_EXPORTS,
                                    ),
                                    _when(
                                        "_scene_distance <= 30",
                                        [
                                            _when(
                                                "_route_plant.terrain == 'ordinary_terrain' "
                                                "and _route_growth_difficult == true "
                                                "and _route_nonplant_difficult != true "
                                                "and base_difficult != true",
                                                [_set("_route_piece_difficult", False)],
                                            ),
                                            _when(
                                                "_route_plant.terrain == 'difficult_terrain' "
                                                "and _route_plants == true",
                                                [_set("_route_piece_difficult", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _set(
                                "_route_piece_cost",
                                "(_route_cut.t - _route_previous) * _route_distance",
                            ),
                            _when(
                                "_route_piece_difficult == true",
                                [_set("_route_piece_cost", "_route_piece_cost * 2")],
                            ),
                            _set("_route_cost", "_route_cost + _route_piece_cost"),
                        ],
                    ),
                    _set("_route_previous", "_route_cut.t"),
                ],
            ),
        ],
    )
    return {
        "command_srd_plants": command,
        "srd_plants_route_interval": _sphere_interval_tool(),
        "srd_information_movement_cost": cost,
    }


def _movement_tool(tools):
    return _public(
        _tool_scene(
            "Move an admitted scene creature along a clear segment and consume actual route "
            "terrain cost.",
            [
                _entity("actor_state"),
                _array("destination"),
                _enum("mode", ["walk", "fly", "climb", "swim"], "walk"),
                _optional("difficult_terrain", "boolean", False),
            ],
            [
                _when(
                    "actor_state.has_actor_spell_modifiers == true",
                    [
                        _call(
                            "refresh_srd_timed_condition_target",
                            {"target_state": "{_actor_state_id}", "lasting_state": "state"},
                        ),
                    ],
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_move_origin", "_info_position"),
                _set("_move_scene_entity", "_info_scene_entity"),
                _call(
                    "srd_information_barriers",
                    {"start": "{_move_origin}", "end": "{destination}", "rule": "solid"},
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true",
                    "A solid barrier blocks this straight movement segment.",
                ),
                _call(
                    "srd_information_movement_cost",
                    {
                        "start": "{_move_origin}",
                        "end": "{destination}",
                        "base_difficult": "{difficult_terrain}",
                        "plane": "{_move_scene_entity.metadata.plane}",
                    },
                    ["_route_cost", "_route_distance"],
                ),
                _when(
                    "actor_state.spell_freedom_of_movement == true",
                    [_set("_route_cost", "_route_distance")],
                ),
                _require("_route_distance > 0", "Actual movement needs a positive distance."),
                {
                    "condition": "combat.encounter_active == true",
                    "on_true": [
                        _require(
                            "combat.turn_active == true and combat.active_actor == _actor_state_id",
                            "Move the active creature on its turn.",
                        ),
                        _call(
                            "spend_combat_movement",
                            {
                                "actor": "{_actor_state_id}",
                                "actor_state": "{_actor_state_id}",
                                "distance": "{_route_cost}",
                                "difficult_terrain": False,
                                "movement_kind": "{mode}",
                            },
                        ),
                    ],
                    "on_false": [
                        _call(
                            "derive_srd_scene_movement_budget",
                            {"actor_state": "{_actor_state_id}", "mode": "{mode}"},
                            ["_scene_exploration_remaining"],
                        ),
                        _require(
                            "_route_cost <= _scene_exploration_remaining",
                            "Advance the actual exploration round before spending more movement.",
                        ),
                        _set(
                            "actor_state.scene_movement_spent",
                            "actor_state.scene_movement_spent + _route_cost",
                        ),
                    ],
                },
                _set(
                    "_move_patch",
                    {
                        "previous_position": "_move_origin",
                        "moving": True,
                        "motion_tick": "state.lasting_tick",
                    },
                ),
                _set("_move_metadata", "merge(_move_scene_entity.metadata, _move_patch)"),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "request": {
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{state.spatial_scene.map.revision}",
                            "target": {"kind": "entity", "id": "{_move_scene_entity.id}"},
                            "patch": {"position": "{destination}", "metadata": "{_move_metadata}"},
                        },
                    },
                    "store_as": "_moved_scene",
                },
                _set("_scene_movement_distance", "_route_distance"),
                _set("_scene_movement_cost", "_route_cost"),
                _set("_scene_actor_entity_id", "_move_scene_entity.id"),
                _set("_scene_actor_position", "destination"),
            ],
        ),
        "movement",
        "Scene movement",
        [
            ("Distance", "_scene_movement_distance"),
            ("Movement spent", "_scene_movement_cost"),
            ("Position", "_scene_actor_position"),
        ],
    )


def _scene_move_budget_tool(tools):
    """Use the existing Speed projection with a saved per-actor exploration-round budget."""
    bindings = {
        "combat.dashes_taken": "actor_state.scene_dashes_taken",
        "combat.movement_spent": "actor_state.scene_movement_spent",
        "combat.movement_mode": "mode",
        "combat.movement_remaining": "_scene_exploration_remaining",
    }

    def bind(value):
        if isinstance(value, str):
            for original, replacement in bindings.items():
                value = value.replace(original, replacement)
            return value
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    guards = [
        _require(p["condition"], p["error"])
        for p in tools["spend_combat_movement"]["pre_conditions"]
        if "actor_state." in p["condition"]
    ]
    projected_speed = bind(deepcopy(tools["refresh_srd_movement_budget"]["effects"][0]["on_true"]))

    def honor_freedom(effects):
        for effect in effects:
            condition = effect.get("condition", "")
            if condition == "actor_state.spell_speed_reduction != null":
                effect["condition"] += " and actor_state.spell_freedom_of_movement != true"
            if (
                "actor_state.timed_control_speed_zero == true" in condition
                and "actor_state.spell_freedom_of_movement" not in condition
            ):
                effect["condition"] = condition.replace(
                    "actor_state.timed_control_speed_zero == true",
                    "(actor_state.timed_control_speed_zero == true and "
                    "actor_state.spell_freedom_of_movement != true)",
                )
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    honor_freedom(effect[branch])

    honor_freedom(projected_speed)
    return _tool(
        "Spend movement from the actual exploration clock interval using the shared Speed owner.",
        [
            _entity("actor_state"),
            _enum("mode", ["walk", "fly", "climb", "swim"]),
        ],
        guards
        + [
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when(
                "actor_state.scene_movement_tick == null or actor_state.scene_movement_tick "
                "!= state.lasting_tick // 2",
                [
                    _set("actor_state.scene_movement_tick", "state.lasting_tick // 2"),
                    _set("actor_state.scene_movement_spent", 0),
                    _set("actor_state.scene_dashes_taken", 0),
                ],
            ),
            _call(
                "project_srd_turn_modifiers",
                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
            ),
            _call(
                "project_srd_timed_actor_mobility",
                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
            ),
            _require(
                "actor_state.spell_flight_fall_pending != true",
                "Resolve lost flight before voluntary movement.",
            ),
            *projected_speed,
        ],
    )


def _public_wrappers(changed):
    """Hide package bindings on public schemas and preserve resolved entity references."""
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
            continue
        node_name = name + "_node"
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[node_name] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        params = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in tool["parameters"]
        }
        params["lasting_state"] = "state"
        exports = [
            row["value"]["path"].removeprefix("context.")
            for row in tool["metadata"]["result_card"]["summary"]
        ]
        optional_references = [
            _when(p["name"] + " == null", [_set("_" + p["name"] + "_id", None)])
            for p in tool["parameters"]
            if p.get("resolve") in {"entity", "participant"} and p.get("required") is False
        ]
        tool["effects"] = optional_references + [_call(node_name, params, exports)]


def extend_runtime(text):
    """Regenerate owned information fragments and the narrow existing-consumer composition."""
    tools = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))["tools"]
    changed = (
        _scene_tools()
        | _barrier_tools()
        | _record_tools()
        | _candidate_tools()
        | _lighting_tools()
        | _query_tools()
        | _casting_tools()
        | _cleanup_tools()
        | _proxy_tools()
        | _language_tools()
        | _plant_tools()
    )
    relation = changed["resolve_srd_scene_relation"]
    sight_index = next(
        i
        for i, effect in enumerate(relation["effects"])
        if effect.get("call") == "resolve_srd_sight"
    )
    relation["effects"][sight_index]["params"]["lighting"] = "{_information_light}"
    relation["effects"].insert(
        sight_index,
        _call(
            "resolve_srd_information_light",
            {
                "point": "{_scene_target_position}",
                "plane": "{_info_scene_entity.metadata.plane}",
                "ambient": "{_info_scene_entity.metadata.lighting}",
            },
            ["_information_light"],
        ),
    )
    changed["update_srd_information_record"] = _update_record_tool()
    changed["move_srd_scene_creature"] = _movement_tool(tools)
    changed["derive_srd_scene_movement_budget"] = _scene_move_budget_tool(tools)

    def bind_guard(effects):
        for effect in effects:
            if effect.get("call") == "check_srd_mind_blank_information":
                effect["params"]["lasting_state"] = "state"
            for key in ("on_true", "on_false"):
                if key in effect:
                    bind_guard(effect[key])
            if "for_each" in effect:
                bind_guard(effect["for_each"]["do"])

    def normalize(value):
        if isinstance(value, str):
            # The closed expression grammar has arrays as effect values, not inline literals.
            pattern = r"([A-Za-z_][A-Za-z0-9_.]*) (not in|in) \[([^\]]+)\]"

            def membership(match):
                terms = [term.strip() for term in match[3].split(",")]
                op, conjunction = ("!=", " and ") if match[2] == "not in" else ("==", " or ")
                return "(" + conjunction.join(f"{match[1]} {op} {term}" for term in terms) + ")"

            return re.sub(pattern, membership, value)
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            if "spatial_mutation" in value:
                body = value["spatial_mutation"]
                if "request" in body:
                    body.update(body.pop("request"))
            return {key: normalize(item) for key, item in value.items()}
        return value

    argument_index = 0

    def normalize_calls(effects):
        nonlocal argument_index
        result = []
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = normalize_calls(effect[key])
            if "for_each" in effect:
                effect["for_each"]["do"] = normalize_calls(effect["for_each"]["do"])
            if "call" in effect:
                for name, value in effect["params"].items():
                    if (
                        isinstance(value, str)
                        and re.fullmatch(r"\{[^{}]+\}", value)
                        and not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", value)
                    ):
                        argument_index += 1
                        variable = f"_info_argument_{argument_index}"
                        result.append(_set(variable, value[1:-1]))
                        effect["params"][name] = "{" + variable + "}"
            result.append(effect)
        return result

    for name, tool in changed.items():
        bind_guard(tool["effects"])
        changed[name] = normalize(tool)
        changed[name]["effects"] = normalize_calls(changed[name]["effects"])
    _public_wrappers(changed)
    cast = deepcopy(tools["cast_spell"])
    prior_names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in prior_names]
    cast["effects"] = [
        e
        for e in cast["effects"]
        if e.get("condition") != "_spell_definition.family == 'information_spell'"
    ]
    params = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    params.update(
        spell="{spell}",
        caster_state="{_caster_state_id}",
        target_state="{_scalar_target_id}",
        caster_can_touch_target="{_caster_can_touch_target}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
        material_item_ids="{material_item_ids}",
    )
    branch = _when(
        "_spell_definition.family == 'information_spell'",
        [
            _call("cast_spell_information_spell", params),
        ],
    )
    branch["on_true"][0]["export_context"] = {
        "_nested_info_summary": "_spell_summary_rows",
        "_nested_info_details": "_spell_detail_rows",
    }
    for suffix, local in (("summary", "_nested_info_summary"), ("detail", "_nested_info_details")):
        branch["on_true"].append(
            _loop(local, "_info_card_row", [_push(f"_spell_{suffix}_rows", "_info_card_row")])
        )
    last = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(last + 1, branch)
    changed["cast_spell"] = cast
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_information_spells" not in str(clock["effects"]):
        # Expiry cleanup must precede the generic expiry filter that removes time-owned records.
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        )
        clock["effects"].insert(index + 1, _call("refresh_srd_information_spells", {}))
    changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(tools["end_srd_concentration_node"])
    if "remove_srd_information_effects" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'information_spell'",
                [
                    _call(
                        "remove_srd_information_effects",
                        {
                            "caster_state": "{_source_state_id}",
                            "spell": "{source_state.concentration_spell_id}",
                        },
                    ),
                ],
            ),
        )
    changed["end_srd_concentration_node"] = end
    # Shared generic movement has a target binding, including its existing control guard.
    movement = deepcopy(tools["spend_srd_movement"])
    for precondition in movement["pre_conditions"]:
        precondition["condition"] = precondition["condition"].replace(
            "actor_state.control_", "target.control_"
        )
    changed["spend_srd_movement"] = movement
    text = _replace_tools(text, changed)
    if "      information_spell:" not in text:
        schema = yaml.dump({"information_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
