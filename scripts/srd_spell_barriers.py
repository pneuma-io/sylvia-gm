"""Source-owned barriers read the admitted scene and preserve exact casting lifetimes."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import _each, _push
    from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from .srd_spell_environment import _emanation_geometry_tools
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _optional, _public, _replace_tools
else:
    from srd_control_behaviors import _each, _push
    from srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from srd_spell_environment import _emanation_geometry_tools
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _optional, _public, _replace_tools


PROFILES = {
    "antilife_shell": {
        "family": "spell_barrier",
        "base_level": 5,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "duration_rounds": 600,
        "concentration": True,
        "barrier_mode": "antilife",
        "radius_feet": 10,
    },
    "resilient_sphere": {
        "family": "spell_barrier",
        "base_level": 4,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 30,
        "duration_rounds": 10,
        "concentration": True,
        "barrier_mode": "resilient",
        "radius_feet": 0,
    },
    "tiny_hut": {
        "family": "spell_barrier",
        "base_level": 3,
        "casting_time": "1_minute",
        "range_kind": "self",
        "range_feet": 0,
        "duration_rounds": 4800,
        "concentration": False,
        "barrier_mode": "hut",
        "radius_feet": 10,
    },
}
PUBLIC_TOOLS = {"command_srd_tiny_hut", "roll_srd_resilient_sphere"}
BODY_EXPORTS = ("_barrier_body", "_barrier_plane", "_barrier_radius")
MEMBERSHIP_EXPORTS = ("_barrier_inside", "_barrier_overlap")


def validate_profiles(definitions):
    """Admit only the fixed profiles reviewed against the pinned SRD pages."""
    for spell, definition in definitions.items():
        if definition.get("family") == "spell_barrier" and definition != PROFILES.get(spell):
            raise SystemExit(f"Barrier profile differs from its source: {spell}")


def _schema():
    properties = {}
    for key, value in PROFILES["tiny_hut"].items():
        if key != "family":
            properties[key] = {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _tool(description, parameters, effects):
    tool = _tool_scene(description, parameters, effects)
    for parameter in tool["parameters"]:
        if parameter["name"] == "lasting_state":
            parameter["model_hidden"] = True
    return tool


def _scene(subject, name=None):
    call = _call("srd_information_scene_entity", {"subject": "{" + subject + "}"}, POSITION_EXPORTS)
    return [call] + ([_set(name, "_info_scene_entity")] if name else [])


def _sources(effects):
    return [
        _when(
            "state.has_spell_barriers == true",
            [
                _set("_barrier_ids", "state.spell_barrier_source_ids"),
                _each(
                    "_barrier_ids",
                    "_barrier_id",
                    [
                        _set("_barrier_record", "state.spell_barrier_sources[_barrier_id]"),
                        _when("_barrier_record.installed == true", effects),
                    ],
                ),
            ],
        )
    ]


def _geometry_tools():
    """Reuse the established box Emanation interval with a source-owned stationary body."""
    boundary = _tool(
        "Read the exact saved barrier body; moving shells use their actual anchor.",
        [_parameter("source_id")],
        [
            _set("_boundary_source", "state.spell_barrier_sources[source_id]"),
            _require("_boundary_source.installed == true", "Select an installed barrier source."),
            _set("_barrier_body", "_boundary_source.body"),
            _set("_barrier_plane", "_boundary_source.plane"),
            _set("_barrier_radius", "_boundary_source.radius"),
            _when(
                "_boundary_source.mode == 'antilife'",
                [
                    *_scene("_boundary_source.caster_state_id", "_barrier_body"),
                    _set("_barrier_plane", "_barrier_body.metadata.plane"),
                ],
            ),
            _when(
                "_boundary_source.mode == 'resilient'",
                [
                    *_scene("_boundary_source.subject_id", "_sphere_anchor"),
                    _set("_barrier_plane", "_sphere_anchor.metadata.plane"),
                    _set(
                        "_barrier_body",
                        {
                            "position": [
                                "_sphere_anchor.position[0]",
                                "_sphere_anchor.position[1]",
                                "_sphere_anchor.position[2] + _boundary_source.center_offset",
                            ],
                            "footprint": {"kind": "'box'", "half_extents": [0, 0]},
                            "height": 0,
                            "metadata": {"plane": "_barrier_plane"},
                        },
                    ),
                ],
            ),
            _require(
                "_barrier_body.footprint.kind == 'box'",
                "Barrier boundaries currently require admitted box creature spaces.",
            ),
        ],
    )
    membership = _tool(
        "Resolve overlap and full containment of an actual admitted box body.",
        [
            _parameter("subject"),
            _parameter("source_id"),
            _optional(
                "position",
                "array",
                None,
                items={"type": "number"},
                min_items=3,
                max_items=3,
                model_hidden=True,
            ),
        ],
        [
            *_scene("subject", "_membership_subject"),
            _require(
                "_membership_subject.footprint.kind == 'box'",
                "Barrier membership currently requires an admitted box subject.",
            ),
            _set("_membership_point", "_membership_subject.position"),
            _when("position != null", [_set("_membership_point", "position")]),
            _call("read_srd_barrier_body", {"source_id": "{source_id}"}, BODY_EXPORTS),
            _set("_barrier_inside", False),
            _set("_barrier_overlap", False),
            _when(
                "_membership_subject.metadata.plane == _barrier_plane",
                [
                    *[
                        _set(
                            f"_membership_axis_{i}",
                            f"abs(_membership_point[{i}] - _barrier_body.position[{i}])",
                        )
                        for i in range(2)
                    ],
                    _set(
                        "_membership_axis_2",
                        "abs(_membership_point[2] + "
                        "_membership_subject.height * 0.5 - _barrier_body.position[2] - "
                        "_barrier_body.height * 0.5)",
                    ),
                    *[
                        _set(
                            f"_membership_far_{i}",
                            f"max(0, _membership_axis_{i} + "
                            f"_membership_subject.footprint.half_extents[{i}] - "
                            f"_barrier_body.footprint.half_extents[{i}])",
                        )
                        for i in range(2)
                    ],
                    _set(
                        "_membership_far_2",
                        "max(0, _membership_axis_2 + "
                        "_membership_subject.height * 0.5 - _barrier_body.height * 0.5)",
                    ),
                    *[
                        _set(
                            f"_membership_near_{i}",
                            f"max(0, _membership_axis_{i} - "
                            f"_membership_subject.footprint.half_extents[{i}] - "
                            f"_barrier_body.footprint.half_extents[{i}])",
                        )
                        for i in range(2)
                    ],
                    _set(
                        "_membership_near_2",
                        "max(0, _membership_axis_2 - "
                        "_membership_subject.height * 0.5 - _barrier_body.height * 0.5)",
                    ),
                    _set(
                        "_barrier_inside",
                        "_membership_far_0 ** 2 + _membership_far_1 ** 2 + "
                        "_membership_far_2 ** 2 <= _barrier_radius ** 2",
                    ),
                    _set(
                        "_barrier_overlap",
                        "_membership_near_0 ** 2 + _membership_near_1 ** 2 + "
                        "_membership_near_2 ** 2 <= _barrier_radius ** 2",
                    ),
                ],
            ),
        ],
    )
    segment = deepcopy(_emanation_geometry_tools()["resolve_srd_environment_body_segment"])
    segment["description"] = "Resolve an actual body segment against the exact barrier boundary."
    segment["parameters"] = [
        p for p in segment["parameters"] if p["name"] not in {"fixed_subject", "radius"}
    ]
    segment["parameters"].append(_parameter("source_id"))
    fixed = next(
        i
        for i, e in enumerate(segment["effects"])
        if e.get("call") == "srd_information_scene_entity"
        and e.get("params", {}).get("subject") == "{fixed_subject}"
    )
    segment["effects"][fixed : fixed + 2] = [
        _call("read_srd_barrier_body", {"source_id": "{source_id}"}, BODY_EXPORTS),
        _set("_body_fixed", "_barrier_body"),
    ]

    def radius(value):
        if isinstance(value, str):
            return re.sub(r"\bradius\b", "_barrier_radius", value)
        if isinstance(value, list):
            return [radius(v) for v in value]
        if isinstance(value, dict):
            return {k: radius(v) for k, v in value.items()}
        return value

    segment["effects"] = radius(segment["effects"])
    return {
        "read_srd_barrier_body": boundary,
        "resolve_srd_barrier_membership": membership,
        "resolve_srd_barrier_body_segment": segment,
    }


def _source_tools():
    retire = _tool(
        "Retire exactly one barrier record without re-entering concentration removal.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            _set("_retired_barrier", "state.spell_barrier_sources[source_id]"),
            _require("_retired_barrier != null", "Select an actual saved barrier source."),
            _when(
                "_retired_barrier.installed == true",
                [
                    _set("state.spell_barrier_sources.{source_id}.installed", False),
                    _set("state.spell_barrier_sources.{source_id}.ended_reason", "reason"),
                    _set(
                        "state.spell_barrier_sources.{source_id}.ended_tick", "state.lasting_tick"
                    ),
                ],
            ),
        ],
    )
    finish = _tool(
        "End only the concentration belonging to this exact retired barrier source.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _when(
                "caster_state.concentration_effect_family == 'spell_barrier' and "
                "caster_state.barrier_source_id == source_id",
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
        ],
    )
    end = _tool(
        "Remove one exact barrier and its own concentration, preserving other casts.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            _set("_ending_barrier", "state.spell_barrier_sources[source_id]"),
            _require("_ending_barrier != null", "Select an actual saved barrier source."),
            _when(
                "_ending_barrier.installed == true",
                [
                    _call(
                        "retire_srd_spell_barrier_record",
                        {"source_id": "{source_id}", "reason": "{reason}"},
                    ),
                    _when(
                        "_ending_barrier.concentration == true",
                        [
                            _call(
                                "finish_srd_spell_barrier_concentration",
                                {
                                    "caster_state": "{_ending_barrier.caster_state_id}",
                                    "source_id": "{source_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    check = _tool(
        "Check a barrier's actual caster concentration and source-defined lifetime.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_checked_barrier", "state.spell_barrier_sources[source_id]"),
            _set(
                "_barrier_source_active",
                "_checked_barrier.installed == true and "
                "state.lasting_tick < _checked_barrier.expires_tick",
            ),
            _when(
                "_checked_barrier.concentration == true",
                [
                    _set(
                        "_barrier_source_active",
                        "_barrier_source_active == true and "
                        "caster_state.is_alive == true and caster_state.concentration_spell_id == "
                        "_checked_barrier.spell_id and caster_state.concentration_effect_family == "
                        "'spell_barrier' and caster_state.barrier_source_id == source_id",
                    ),
                ],
            ),
            _when(
                "_barrier_source_active == true and _checked_barrier.mode == 'hut'",
                [
                    _call(
                        "resolve_srd_barrier_membership",
                        {
                            "subject": "{_checked_barrier.caster_state_id}",
                            "source_id": "{source_id}",
                        },
                        MEMBERSHIP_EXPORTS,
                    ),
                    _set("_barrier_source_active", "_barrier_inside == true"),
                ],
            ),
            _when(
                "_barrier_source_active != true and _checked_barrier.installed == true",
                [
                    _call(
                        "end_srd_spell_barrier_source",
                        {"source_id": "{source_id}", "reason": "lifetime"},
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Refresh only registered barriers; inactive scenes take a cheap branch.",
        [],
        [
            _set("_barriers_live", False),
            *_sources(
                [
                    _call(
                        "check_srd_spell_barrier_source",
                        {
                            "caster_state": "{_barrier_record.caster_state_id}",
                            "source_id": "{_barrier_id}",
                        },
                        ("_barrier_source_active",),
                    ),
                    _when("_barrier_source_active == true", [_set("_barriers_live", True)]),
                ]
            ),
            _set("state.has_spell_barriers", "_barriers_live"),
        ],
    )
    return {
        "retire_srd_spell_barrier_record": retire,
        "finish_srd_spell_barrier_concentration": finish,
        "end_srd_spell_barrier_source": end,
        "check_srd_spell_barrier_source": check,
        "refresh_srd_spell_barriers": refresh,
    }


def _point_tools():
    """Project source-owned interior light and one-way opacity at actual query points."""
    point = _tool(
        "Test a derived scene point against the exact active barrier boundary.",
        [_array("point"), _parameter("plane"), _parameter("source_id")],
        [
            _call("read_srd_barrier_body", {"source_id": "{source_id}"}, BODY_EXPORTS),
            _set("_barrier_point_inside", False),
            _when(
                "plane == _barrier_plane",
                [
                    _set(
                        "_barrier_nearest_point",
                        [
                            f"min(max(point[{i}], _barrier_body.position[{i}] - "
                            f"_barrier_body.footprint.half_extents[{i}]), "
                            f"_barrier_body.position[{i}] + "
                            f"_barrier_body.footprint.half_extents[{i}])"
                            for i in range(2)
                        ]
                        + [
                            "min(max(point[2], _barrier_body.position[2]), "
                            "_barrier_body.position[2] + _barrier_body.height)"
                        ],
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{point}", "end": "{_barrier_nearest_point}"},
                        ("_scene_distance",),
                    ),
                    _set("_barrier_point_inside", "_scene_distance <= _barrier_radius"),
                ],
            ),
        ],
    )
    segment = deepcopy(_geometry_tools()["resolve_srd_barrier_body_segment"])
    segment["description"] = "Resolve a derived observation segment against the saved barrier."
    segment["parameters"] = [
        p for p in segment["parameters"] if p["name"] not in {"moving_subject", "point_space"}
    ]
    segment["parameters"].append(_parameter("plane"))
    first = next(
        i
        for i, e in enumerate(segment["effects"])
        if e.get("call") == "srd_information_scene_entity"
    )
    segment["effects"][first : first + 3] = [
        _set(
            "_body_moving",
            {
                "position": "start",
                "footprint": {"kind": "'box'", "half_extents": [0, 0]},
                "height": 0,
                "metadata": {"plane": "plane"},
            },
        ),
        _set("point_space", True),
    ]
    interior = _tool(
        "Read the latest active Tiny Hut's actual commanded interior atmosphere.",
        [_array("point"), _parameter("plane")],
        [
            _set("_barrier_interior_light", None),
            _set("_barrier_comfortable_dry", False),
            _set("_barrier_light_serial", -1),
            _when(
                "state.has_spell_barriers == true",
                [
                    _call("refresh_srd_spell_barriers", {}),
                    *_sources(
                        [
                            _when(
                                "_barrier_record.mode == 'hut' and _barrier_record.plane == plane",
                                [
                                    _call(
                                        "resolve_srd_barrier_point",
                                        {
                                            "point": "{point}",
                                            "plane": "{plane}",
                                            "source_id": "{_barrier_id}",
                                        },
                                        ("_barrier_point_inside",),
                                    ),
                                    _when(
                                        "_barrier_point_inside == true and "
                                        "_barrier_record.serial > _barrier_light_serial",
                                        [
                                            _set(
                                                "_barrier_interior_light",
                                                "_barrier_record.interior_light",
                                            ),
                                            _set("_barrier_comfortable_dry", True),
                                            _set("_barrier_light_serial", "_barrier_record.serial"),
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
    observation = _tool(
        "Apply Tiny Hut's opaque exterior and transparent interior to actual rays.",
        [_array("start"), _array("end"), _parameter("plane")],
        [
            _set("_barrier_observation_blocked", False),
            _when(
                "state.has_spell_barriers == true",
                [
                    _call("refresh_srd_spell_barriers", {}),
                    *_sources(
                        [
                            _when(
                                "_barrier_record.mode == 'hut' and _barrier_record.plane == plane",
                                [
                                    _call(
                                        "resolve_srd_barrier_point",
                                        {
                                            "point": "{start}",
                                            "plane": "{plane}",
                                            "source_id": "{_barrier_id}",
                                        },
                                        ("_barrier_point_inside",),
                                    ),
                                    _when(
                                        "_barrier_point_inside != true",
                                        [
                                            _call(
                                                "resolve_srd_barrier_point_segment",
                                                {
                                                    "start": "{start}",
                                                    "end": "{end}",
                                                    "plane": "{plane}",
                                                    "source_id": "{_barrier_id}",
                                                },
                                                ("_body_hit",),
                                            ),
                                            _when(
                                                "_body_hit == true",
                                                [_set("_barrier_observation_blocked", True)],
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
    return {
        "resolve_srd_barrier_point": point,
        "resolve_srd_barrier_point_segment": segment,
        "resolve_srd_barrier_interior_light": interior,
        "check_srd_barrier_observation": observation,
    }


def _movement_tool():
    """Check actual body paths before the shared relocation, with no alternate position state."""
    blocked = _require(
        "_barrier_movement_allowed == true",
        "An active spell barrier prevents this movement segment.",
    )
    return _tool(
        "Check source-qualified movement through active barriers before mutation.",
        [
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False, model_hidden=True),
            _optional("sphere_source_id", default="", model_hidden=True),
        ],
        [
            _set("_barrier_movement_allowed", True),
            _when(
                "state.has_spell_barriers == true",
                [
                    _call("refresh_srd_spell_barriers", {}),
                    *_scene("subject", "_barrier_mover"),
                    *_sources(
                        [
                            _when(
                                "_barrier_record.mode == 'hut' and "
                                "_barrier_mover.id not in _barrier_record.allowed_subject_ids",
                                [
                                    _call(
                                        "resolve_srd_barrier_body_segment",
                                        {
                                            "moving_subject": "{subject}",
                                            "source_id": "{_barrier_id}",
                                            "start": "{_barrier_mover.position}",
                                            "end": "{destination}",
                                        },
                                        ("_body_hit", "_body_entry", "_body_exit"),
                                    ),
                                    _when(
                                        "_body_hit == true and _barrier_record.plane == plane",
                                        [_set("_barrier_movement_allowed", False)],
                                    ),
                                ],
                            ),
                            _when(
                                "_barrier_record.mode == 'resilient'",
                                [
                                    _when(
                                        "(_barrier_mover.id == _barrier_record.subject_id or "
                                        "_barrier_mover.metadata.participant_ref == "
                                        "_barrier_record.target_state_id)",
                                        [
                                            _when(
                                                "sphere_source_id != _barrier_id",
                                                [_set("_barrier_movement_allowed", False)],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_barrier_mover.id != _barrier_record.subject_id and "
                                        "_barrier_mover.metadata.participant_ref != "
                                        "_barrier_record.target_state_id",
                                        [
                                            _call(
                                                "resolve_srd_barrier_body_segment",
                                                {
                                                    "moving_subject": "{subject}",
                                                    "source_id": "{_barrier_id}",
                                                    "start": "{_barrier_mover.position}",
                                                    "end": "{destination}",
                                                },
                                                ("_body_hit", "_body_entry", "_body_exit"),
                                            ),
                                            _when(
                                                "_body_hit == true and _barrier_record.plane == "
                                                "plane",
                                                [_set("_barrier_movement_allowed", False)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_barrier_record.mode == 'antilife' and "
                                "_barrier_mover.metadata.participant_ref != null and "
                                "_barrier_mover.metadata.participant_ref != "
                                "_barrier_record.caster_state_id",
                                [
                                    _call(
                                        "check_srd_antilife_creature_movement",
                                        {
                                            "actor_state": (
                                                "{_barrier_mover.metadata.participant_ref}"
                                            ),
                                            "source_id": "{_barrier_id}",
                                            "destination": "{destination}",
                                            "plane": "{plane}",
                                            "teleport": "{teleport}",
                                        },
                                        ("_antilife_movement_allowed",),
                                    ),
                                    _when(
                                        "_antilife_movement_allowed != true",
                                        [_set("_barrier_movement_allowed", False)],
                                    ),
                                ],
                            ),
                            _when(
                                "_barrier_record.mode == 'antilife' and "
                                "_barrier_mover.metadata.participant_ref == "
                                "_barrier_record.caster_state_id",
                                [
                                    _call(
                                        "check_srd_antilife_caster_movement",
                                        {
                                            "source_id": "{_barrier_id}",
                                            "destination": "{destination}",
                                            "plane": "{plane}",
                                            "teleport": "{teleport}",
                                        },
                                    ),
                                ],
                            ),
                            _when(
                                "_barrier_record.mode == 'hut' and "
                                "_barrier_mover.metadata.participant_ref == "
                                "_barrier_record.caster_state_id",
                                [
                                    _call(
                                        "resolve_srd_barrier_membership",
                                        {
                                            "subject": "{subject}",
                                            "source_id": "{_barrier_id}",
                                            "position": "{destination}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _when(
                                        "_barrier_inside != true or _barrier_record.plane != plane",
                                        [
                                            _call(
                                                "end_srd_spell_barrier_source",
                                                {
                                                    "source_id": "{_barrier_id}",
                                                    "reason": "caster_left",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ]
                    ),
                    blocked,
                ],
            ),
        ],
    )


def _antilife_tools():
    creature = _tool(
        "Qualify a creature's type and actual body crossing of Antilife Shell.",
        [
            _entity("actor_state"),
            _parameter("source_id"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False, model_hidden=True),
        ],
        [
            _set("_antilife_movement_allowed", True),
            _require(
                "actor_state.creature_type != null",
                "Antilife Shell requires the creature's actual saved type.",
            ),
            _when(
                "actor_state.creature_type != 'construct' and actor_state.creature_type != "
                "'undead' and teleport != true",
                [
                    *_scene("_actor_state_id", "_antilife_actor"),
                    _call(
                        "resolve_srd_barrier_membership",
                        {"subject": "{_actor_state_id}", "source_id": "{source_id}"},
                        MEMBERSHIP_EXPORTS,
                    ),
                    _set("_antilife_start_inside", "_barrier_inside"),
                    _call(
                        "resolve_srd_barrier_body_segment",
                        {
                            "moving_subject": "{_actor_state_id}",
                            "source_id": "{source_id}",
                            "start": "{_antilife_actor.position}",
                            "end": "{destination}",
                        },
                        ("_body_hit", "_body_entry", "_body_exit"),
                    ),
                    _call(
                        "resolve_srd_barrier_membership",
                        {
                            "subject": "{_actor_state_id}",
                            "source_id": "{source_id}",
                            "position": "{destination}",
                        },
                        MEMBERSHIP_EXPORTS,
                    ),
                    _when(
                        "_antilife_actor.metadata.plane == plane and "
                        "((_antilife_start_inside == true and _barrier_inside != true) or "
                        "(_antilife_start_inside != true and _body_hit == true))",
                        [_set("_antilife_movement_allowed", False)],
                    ),
                ],
            ),
        ],
    )
    caster = _tool(
        "End the shell when caster movement forces an affected body across it.",
        [
            _parameter("source_id"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False, model_hidden=True),
        ],
        [
            _set("_antilife_cast_source", "state.spell_barrier_sources[source_id]"),
            *_scene("_antilife_cast_source.caster_state_id", "_antilife_caster"),
            _when(
                "teleport != true and plane == _antilife_caster.metadata.plane",
                [
                    _set("_antilife_scene_entities", "state.spatial_scene.entities"),
                    _each(
                        "_antilife_scene_entities",
                        "_antilife_other",
                        [
                            _when(
                                "state.spell_barrier_sources[source_id].installed == true and "
                                "_antilife_other.active == true and "
                                "_antilife_other.metadata.participant_ref != null and "
                                "_antilife_other.metadata.participant_ref != "
                                "_antilife_cast_source.caster_state_id and "
                                "_antilife_other.metadata.plane == plane",
                                [
                                    _set(
                                        "_antilife_relative_destination",
                                        [
                                            "_antilife_other.position[0] - destination[0] + "
                                            "_antilife_caster.position[0]",
                                            "_antilife_other.position[1] - destination[1] + "
                                            "_antilife_caster.position[1]",
                                            "_antilife_other.position[2] - destination[2] + "
                                            "_antilife_caster.position[2]",
                                        ],
                                    ),
                                    _call(
                                        "check_srd_antilife_creature_movement",
                                        {
                                            "actor_state": (
                                                "{_antilife_other.metadata.participant_ref}"
                                            ),
                                            "source_id": "{source_id}",
                                            "plane": "{plane}",
                                            "destination": "{_antilife_relative_destination}",
                                        },
                                        ("_antilife_movement_allowed",),
                                    ),
                                    _when(
                                        "_antilife_movement_allowed != true",
                                        [
                                            _call(
                                                "end_srd_spell_barrier_source",
                                                {
                                                    "source_id": "{source_id}",
                                                    "reason": "forced_crossing",
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
    return {
        "check_srd_antilife_creature_movement": creature,
        "check_srd_antilife_caster_movement": caster,
    }


def _cast_tool():
    initial = [
        _set("_initial_scene_entities", "state.spatial_scene.entities"),
        _each(
            "_initial_scene_entities",
            "_initial_subject",
            [
                _when(
                    "_initial_subject.active == true and "
                    "_initial_subject.metadata.plane == _barrier_cast_plane",
                    [
                        _call(
                            "resolve_srd_barrier_membership",
                            {"subject": "{_initial_subject.id}", "source_id": "{_new_barrier_id}"},
                            MEMBERSHIP_EXPORTS,
                        ),
                        _when(
                            "_barrier_overlap == true and "
                            "_initial_subject.metadata.participant_ref "
                            "!= null and _barrier_inside != true",
                            [_set("_barrier_cast_failed", True)],
                        ),
                        _when(
                            "_barrier_inside == true",
                            [_push("_hut_allowed", "_initial_subject.id")],
                        ),
                    ],
                ),
            ],
        ),
        _set("state.spell_barrier_sources.{_new_barrier_id}.allowed_subject_ids", "_hut_allowed"),
        _when(
            "_barrier_cast_failed == true",
            [
                _call(
                    "end_srd_spell_barrier_source",
                    {"source_id": "{_new_barrier_id}", "reason": "initial_encapsulation_failed"},
                ),
            ],
        ),
    ]
    return _tool(
        "Resolve the actual barrier target/save and install a source-bound scene boundary.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("concentration_save_modifier", "integer"),
            _optional("save_dc", "integer", 0),
            _optional("subject", default=""),
            _optional("expected_target_state", default=None, resolve="entity", model_hidden=True),
            _optional("validate_target_binding", "boolean", False, model_hidden=True),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("target_willing", "boolean", False),
            _optional("interior_light", default="dim"),
            _optional("exterior_color", default="gray"),
        ],
        [
            _when(
                "state.has_spell_barriers == true",
                [
                    _call("refresh_srd_spell_barriers", {}),
                ],
            ),
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_barrier_profile",
            },
            _require(
                "_barrier_profile.family == 'spell_barrier' and "
                "slot_level >= _barrier_profile.base_level and slot_level <= 9",
                "Use the actual source-defined barrier and casting slot.",
            ),
            _require(
                "interior_light == 'dim' or interior_light == 'darkness'",
                "Tiny Hut offers Dim Light or Darkness.",
            ),
            _require(
                "len(exterior_color) > 0 and len(exterior_color) <= 80",
                "Choose a short exterior color description.",
            ),
            *_scene("_caster_state_id", "_barrier_caster_body"),
            _require(
                "_barrier_caster_body.footprint.kind == 'box'",
                "Barrier origins currently require an admitted box caster.",
            ),
            _set("_barrier_cast_plane", "_barrier_caster_body.metadata.plane"),
            _set("_barrier_cast_body", "_barrier_caster_body"),
            _set("_barrier_subject_id", "_barrier_caster_body.id"),
            _set("_barrier_target_id", "_caster_state_id"),
            _set("_barrier_cast_radius", "_barrier_profile.radius_feet"),
            _set("_barrier_cast_center_offset", 0),
            _set("_barrier_cast_failed", False),
            _set("_sphere_target_saved", False),
            _set("_barrier_save", None),
            _when(
                "_barrier_profile.barrier_mode == 'resilient'",
                [
                    # Exact call templates preserve scene IDs despite implicit creature bindings.
                    _call(
                        "normalize_srd_barrier_scene_subject",
                        {"subject": "{subject}"},
                        ("_barrier_raw_scene_subject",),
                    ),
                    _set("_selected_sphere_subject", "_barrier_raw_scene_subject"),
                    _require(
                        "_selected_sphere_subject != ''",
                        "Select an actual scene creature or object for the sphere.",
                    ),
                    *_scene("_selected_sphere_subject", "_sphere_subject"),
                    _require(
                        "validate_target_binding != true or "
                        "(_sphere_subject.metadata.participant_ref == null and "
                        "expected_target_state == null) or "
                        "(_sphere_subject.metadata.participant_ref != null and "
                        "expected_target_state != null and "
                        "_sphere_subject.metadata.participant_ref == _expected_target_state_id)",
                        "The barrier subject must match the cast's actual validated target; "
                        "an independent scene choice may identify only an object.",
                    ),
                    _require(
                        "_sphere_subject.footprint.kind == 'box'",
                        "Resilient Sphere currently requires an admitted box subject.",
                    ),
                    _require(
                        "_sphere_subject.metadata.size_rank != null or "
                        "_sphere_subject.metadata.participant_ref != null",
                        "The sphere needs the object's actual admitted size rank.",
                    ),
                    _set("_sphere_size", 0),
                    _set("_barrier_target_id", None),
                    _when(
                        "_sphere_subject.metadata.participant_ref == null",
                        [
                            _set("_sphere_size", "_sphere_subject.metadata.size_rank"),
                        ],
                    ),
                    _when(
                        "_sphere_subject.metadata.participant_ref != null",
                        [
                            _call(
                                "resolve_srd_resilient_creature_target",
                                {
                                    "target_state": "{_sphere_subject.metadata.participant_ref}",
                                    "caster_state": "{_caster_state_id}",
                                    "target_willing": "{target_willing}",
                                    "save_dc": "{save_dc}",
                                    "validate_only": True,
                                },
                                ("_sphere_size",),
                            ),
                            _set("_barrier_target_id", "_sphere_subject.metadata.participant_ref"),
                        ],
                    ),
                    _require(
                        "_sphere_size >= 0 and _sphere_size <= 3",
                        "Resilient Sphere encloses only Large or smaller subjects.",
                    ),
                    _require(
                        "_sphere_subject.metadata.plane == _barrier_cast_plane",
                        "The sphere target is not on the caster's actual plane.",
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{_barrier_caster_body.position}",
                            "end": "{_sphere_subject.position}",
                        },
                        ("_scene_distance",),
                    ),
                    _require(
                        "_scene_distance <= 30", "Resilient Sphere's target is outside 30 feet."
                    ),
                    _call(
                        "srd_information_barriers",
                        {
                            "start": "{_barrier_caster_body.position}",
                            "end": "{_sphere_subject.position}",
                            "rule": "solid",
                        },
                        ("_information_blocked",),
                    ),
                    _require(
                        "_information_blocked != true", "A solid barrier blocks the sphere target."
                    ),
                    _set("_barrier_subject_id", "_sphere_subject.id"),
                    _set("_barrier_cast_center_offset", "_sphere_subject.height * 0.5"),
                    _set(
                        "_barrier_cast_radius",
                        "(_sphere_subject.footprint.half_extents[0] ** 2 + "
                        "_sphere_subject.footprint.half_extents[1] ** 2 + "
                        "(_sphere_subject.height * 0.5) ** 2) ** 0.5",
                    ),
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
            _when(
                "state.spell_barrier_sources == null",
                [
                    _set("state.spell_barrier_sources", {}),
                    _set("state.spell_barrier_source_ids", []),
                ],
            ),
            _when(
                "_barrier_profile.barrier_mode == 'hut'",
                _sources(
                    [
                        _when(
                            "_barrier_record.mode == 'hut' and _barrier_record.caster_state_id == "
                            "_caster_state_id",
                            [
                                _call(
                                    "end_srd_spell_barrier_source",
                                    {"source_id": "{_barrier_id}", "reason": "recast"},
                                ),
                            ],
                        ),
                    ]
                ),
            ),
            _when(
                "_barrier_profile.concentration == true and "
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
                "_barrier_profile.barrier_mode == 'resilient' and _barrier_target_id != null",
                [
                    *_scene("subject", "_sphere_subject"),
                    _call(
                        "resolve_srd_resilient_creature_target",
                        {
                            "target_state": "{_sphere_subject.metadata.participant_ref}",
                            "caster_state": "{_caster_state_id}",
                            "target_willing": "{target_willing}",
                            "save_dc": "{save_dc}",
                        },
                        ("_sphere_size", "_sphere_target_saved", "_barrier_save"),
                    ),
                    _require(
                        "_sphere_size >= 0 and _sphere_size <= 3",
                        "Resilient Sphere needs the actual current Large-or-smaller body.",
                    ),
                    _set("_barrier_cast_center_offset", "_sphere_subject.height * 0.5"),
                    _set(
                        "_barrier_cast_radius",
                        "(_sphere_subject.footprint.half_extents[0] ** 2 + "
                        "_sphere_subject.footprint.half_extents[1] ** 2 + "
                        "(_sphere_subject.height * 0.5) ** 2) ** 0.5",
                    ),
                ],
            ),
            _set("_new_barrier_id", None),
            _when(
                "_barrier_cast_failed != true and _sphere_target_saved != true",
                [
                    {"increment": {"field": "state.lasting_serial", "amount": 1}},
                    _call(
                        "format_srd_information_projection",
                        {"message": "{_caster_state_id}:{spell}:{state.lasting_serial}"},
                        ("_info_projection_message",),
                    ),
                    _set("_new_barrier_id", "_info_projection_message"),
                    _set(
                        "state.spell_barrier_sources.{_new_barrier_id}",
                        {
                            "installed": True,
                            "cast_id": "spell_cast_id",
                            "source_caster_id": "_caster_state_id",
                            "ongoing_duration": "spell_cast_id != ''",
                            "spell_id": "spell",
                            "spell_level": "slot_level",
                            "mode": "_barrier_profile.barrier_mode",
                            "caster_state_id": "_caster_state_id",
                            "target_state_id": "_barrier_target_id",
                            "subject_id": "_barrier_subject_id",
                            "started_tick": "state.lasting_tick",
                            "expires_tick": (
                                "state.lasting_tick + _barrier_profile.duration_rounds * 2"
                            ),
                            "serial": "state.lasting_serial",
                            "concentration": "_barrier_profile.concentration",
                            "body": "_barrier_cast_body",
                            "plane": "_barrier_cast_plane",
                            "radius": "_barrier_cast_radius",
                            "center_offset": "_barrier_cast_center_offset",
                            "allowed_subject_ids": [],
                            "interior_light": "interior_light",
                            "exterior_color": "exterior_color",
                            "save": "_barrier_save",
                            "dispel_ongoing": True,
                        },
                    ),
                    _set("_registered_barriers", []),
                    _set("_previous_barrier_ids", "state.spell_barrier_source_ids"),
                    _each(
                        "_previous_barrier_ids",
                        "_previous_barrier_id",
                        [
                            _when(
                                "state.spell_barrier_sources[_previous_barrier_id].installed == "
                                "true",
                                [_push("_registered_barriers", "_previous_barrier_id")],
                            ),
                        ],
                    ),
                    _require(
                        "len(_registered_barriers) < 128", "At most 128 active barrier sources."
                    ),
                    _push("_registered_barriers", "_new_barrier_id"),
                    _set("state.spell_barrier_source_ids", "_registered_barriers"),
                    _set("state.has_spell_barriers", True),
                    _when(
                        "_barrier_profile.barrier_mode == 'hut'",
                        [
                            _set("_hut_allowed", []),
                            *initial,
                        ],
                    ),
                    _when(
                        "_barrier_profile.concentration == true",
                        [
                            _set("caster_state.concentration_spell_id", "spell"),
                            _set("caster_state.concentration_effect_family", "'spell_barrier'"),
                            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                            _set("caster_state.concentration_target_state_ids", []),
                            _set(
                                "caster_state.concentration_save_modifier",
                                "concentration_save_modifier",
                            ),
                            _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                            _set(
                                "caster_state.concentration_expires_tick",
                                "state.lasting_tick + _barrier_profile.duration_rounds * 2",
                            ),
                            _set("caster_state.barrier_source_id", "_new_barrier_id"),
                        ],
                    ),
                ],
            ),
            _set(
                "_spell_summary_rows",
                [
                    {"label": "'Barrier'", "value": "spell"},
                    {"label": "'Spell failed'", "value": "_barrier_cast_failed"},
                ],
            ),
            _set("_spell_detail_rows", [{"label": "'Source'", "value": "_new_barrier_id"}]),
        ],
    )


def _creature_target_tool():
    save = [
        _call(
            "d20_test",
            {
                "actor_state": "{_target_state_id}",
                "kind": "saving_throw",
                "ability": "dexterity",
                "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                "proficiency_bonus": 0,
                "proficiency_factor": "none",
                "target_number": "{save_dc}",
                "circumstantial_modifier": 0,
                "advantage": False,
                "disadvantage": False,
            },
            ("_d20_test", "_outcome", "_adjusted_total", "_combined_modifier"),
        ),
        _set(
            "_barrier_save",
            {
                "roll": "_d20_test",
                "total": "_adjusted_total",
                "dc": "save_dc",
                "combined_modifier": "_combined_modifier",
                "modifier": "target_state.saving_throw_modifiers.dexterity",
                "outcome": "_outcome",
            },
        ),
        _set("_sphere_target_saved", "_outcome == 'success'"),
    ]
    return _tool(
        "Use the actual creature size and Dexterity save; a willing target needs no die.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("target_willing", "boolean"),
            _parameter("save_dc", "integer"),
            _optional("validate_only", "boolean", False, model_hidden=True),
        ],
        [
            _require(
                "target_state.size_rank != null", "The sphere needs the creature's saved size."
            ),
            _set("_sphere_size", "target_state.size_rank"),
            _set("_sphere_target_saved", False),
            _set("_barrier_save", None),
            _when(
                "target_willing != true",
                [
                    _require(
                        "save_dc >= 1 and target_state.saving_throw_modifiers.dexterity != null",
                        "The unwilling creature needs its actual Dexterity save and caster DC.",
                    ),
                    _when("validate_only != true", save),
                ],
            ),
        ],
    )


def _public_tools():
    command = _public(
        _tool(
            "Change this caster's active Tiny Hut light or exterior color, no action.",
            [
                _entity("caster_state"),
                _parameter("source_id"),
                _parameter("interior_light"),
                _parameter("exterior_color"),
            ],
            [
                _call("refresh_srd_spell_barriers", {}),
                _set("_command_hut", "state.spell_barrier_sources[source_id]"),
                _require(
                    "_command_hut.installed == true and _command_hut.mode == 'hut' and "
                    "_command_hut.caster_state_id == _caster_state_id",
                    "Command this caster's actual active Tiny Hut.",
                ),
                _require(
                    "interior_light == 'dim' or interior_light == 'darkness'",
                    "Tiny Hut offers Dim Light or Darkness.",
                ),
                _require(
                    "len(exterior_color) > 0 and len(exterior_color) <= 80",
                    "Choose a short exterior color description.",
                ),
                _set("state.spell_barrier_sources.{source_id}.interior_light", "interior_light"),
                _set("state.spell_barrier_sources.{source_id}.exterior_color", "exterior_color"),
                _set("_hut_light", "interior_light"),
                _set("_hut_color", "exterior_color"),
            ],
        ),
        "spell",
        "Tiny Hut",
        [("Interior", "_hut_light"), ("Exterior", "_hut_color")],
    )
    roll = _public(
        _tool(
            "Spend an Action to roll an actual enclosing sphere up to half Speed.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _array("destination"),
            ],
            [
                _call("refresh_srd_spell_barriers", {}),
                _set("_rolling_sphere", "state.spell_barrier_sources[source_id]"),
                _require(
                    "_rolling_sphere.installed == true and _rolling_sphere.mode == 'resilient' "
                    "and _rolling_sphere.target_state_id == _actor_state_id",
                    "Roll only the sphere actually enclosing this creature.",
                ),
                _require(
                    "actor_state.is_alive == true and 'incapacitated' not in "
                    "actor_state.conditions "
                    "and 'unconscious' not in actor_state.conditions and "
                    "'paralyzed' not in actor_state.conditions and 'petrified' "
                    "not in actor_state.conditions",
                    "The enclosed creature must be able to act.",
                ),
                *_scene("_actor_state_id", "_rolling_subject"),
                _require(
                    "len(state.spatial_scene.geometry) == 0",
                    "Sphere rolling currently requires an open admitted scene; its round solid "
                    "clearance is not represented by ordinary box movement.",
                ),
                _call(
                    "derive_srd_scene_movement_budget",
                    {"actor_state": "{_actor_state_id}", "mode": "walk"},
                    ("_movement_speed",),
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_rolling_subject.position}", "end": "{destination}"},
                    ("_scene_distance",),
                ),
                _require(
                    "_scene_distance > 0 and _scene_distance <= _movement_speed * 0.5",
                    "The sphere rolls at most half the creature's actual Speed for this Action.",
                ),
                *[
                    _require(
                        f"destination[{i}] - _rolling_sphere.radius >= "
                        f"state.spatial_scene.map.bounds.min[{i}] and destination[{i}] + "
                        f"_rolling_sphere.radius <= state.spatial_scene.map.bounds.max[{i}]",
                        "The enclosing sphere must fit within the admitted map.",
                    )
                    for i in range(2)
                ],
                _when(
                    "combat.encounter_active == true",
                    [
                        _require(
                            "combat.turn_active == true and combat.active_actor == _actor_state_id",
                            "Roll the sphere on this creature's actual turn.",
                        ),
                        _call("spend_srd_action", {"control_action_kind": "move"}),
                    ],
                ),
                _call(
                    "check_srd_barrier_movement",
                    {
                        "subject": "{_rolling_subject.id}",
                        "destination": "{destination}",
                        "plane": "{_rolling_subject.metadata.plane}",
                        "sphere_source_id": "{source_id}",
                    },
                ),
                _call(
                    "move_srd_travel_subject",
                    {
                        "subject": "{_rolling_subject.id}",
                        "destination": "{destination}",
                        "plane": "{_rolling_subject.metadata.plane}",
                        "teleport": False,
                        "sphere_source_id": "{source_id}",
                    },
                ),
                _set("_sphere_rolled_feet", "_scene_distance"),
            ],
        ),
        "spell",
        "Resilient Sphere",
        [("Rolled feet", "_sphere_rolled_feet")],
    )
    return {"command_srd_tiny_hut": command, "roll_srd_resilient_sphere": roll}


def _effect_tools():
    """Export eligibility at the real source/target boundary, leaving committed costs intact."""
    effect = _tool(
        "Qualify an actual effect path through active spell barriers.",
        [
            _parameter("source_subject"),
            _parameter("target_subject"),
            _parameter("effect_kind"),
            _optional("actual_spell_level", "integer", -1, model_hidden=True),
            _optional("ranged_weapon", "boolean", False, model_hidden=True),
            _optional("reach_weapon", "boolean", False, model_hidden=True),
            _optional("effect_subject", default="", model_hidden=True),
        ],
        [
            _set("_barrier_effect_allowed", True),
            _set("_barrier_blocking_source_ids", []),
            _require(
                "effect_kind == 'spell' or effect_kind == 'physical' or effect_kind == 'energy'",
                "Use the owning effect's real spell, physical or energy classification.",
            ),
            _when(
                "state.has_spell_barriers == true",
                [
                    _call("refresh_srd_spell_barriers", {}),
                    *_scene("source_subject", "_effect_origin"),
                    *_scene("target_subject", "_effect_target"),
                    *_sources(
                        [
                            _set("_barrier_path_crosses", False),
                            _when(
                                "_effect_origin.metadata.plane == _effect_target.metadata.plane",
                                [
                                    _call(
                                        "resolve_srd_barrier_membership",
                                        {
                                            "subject": "{source_subject}",
                                            "source_id": "{_barrier_id}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _set("_effect_start_inside", "_barrier_inside"),
                                    _call(
                                        "resolve_srd_barrier_membership",
                                        {
                                            "subject": "{target_subject}",
                                            "source_id": "{_barrier_id}",
                                        },
                                        MEMBERSHIP_EXPORTS,
                                    ),
                                    _set("_effect_end_inside", "_barrier_inside"),
                                    _call(
                                        "resolve_srd_barrier_body_segment",
                                        {
                                            "moving_subject": "{source_subject}",
                                            "source_id": "{_barrier_id}",
                                            "start": "{_effect_origin.position}",
                                            "end": "{_effect_target.position}",
                                            "point_space": True,
                                        },
                                        ("_body_hit", "_body_entry", "_body_exit"),
                                    ),
                                    _set(
                                        "_barrier_path_crosses",
                                        "_body_hit == true and "
                                        "(_effect_start_inside != true or _effect_end_inside != "
                                        "true)",
                                    ),
                                ],
                            ),
                            _set("_this_barrier_blocks", False),
                            _when(
                                "_barrier_path_crosses == true",
                                [
                                    _when(
                                        "_barrier_record.mode == 'resilient'",
                                        [_set("_this_barrier_blocks", True)],
                                    ),
                                    _when(
                                        "_barrier_record.mode == 'hut' and effect_kind == 'spell'",
                                        [
                                            _require(
                                                "actual_spell_level >= 0 and actual_spell_level "
                                                "<= 9",
                                                "Tiny Hut needs the actual spell level at the "
                                                "effect owner.",
                                            ),
                                            _set("_this_barrier_blocks", "actual_spell_level <= 3"),
                                        ],
                                    ),
                                    _when(
                                        "_barrier_record.mode == 'hut' and "
                                        "effect_kind == 'physical'",
                                        [
                                            _set("_this_barrier_blocks", True),
                                            _when(
                                                "effect_subject != '' and effect_subject in "
                                                "_barrier_record.allowed_subject_ids",
                                                [_set("_this_barrier_blocks", False)],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_barrier_record.mode == 'antilife' and effect_kind == "
                                        "'physical' "
                                        "and ranged_weapon != true and reach_weapon != true and "
                                        "_effect_origin.metadata.participant_ref != null",
                                        [
                                            _call(
                                                "check_srd_antilife_reaching_creature",
                                                {
                                                    "actor_state": (
                                                        "{_effect_origin.metadata.participant_ref}"
                                                    )
                                                },
                                                ("_antilife_reach_blocked",),
                                            ),
                                            _set("_this_barrier_blocks", "_antilife_reach_blocked"),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_this_barrier_blocks == true",
                                [
                                    _set("_barrier_effect_allowed", False),
                                    _push("_barrier_blocking_source_ids", "_barrier_id"),
                                ],
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )
    reaching = _tool(
        "Read the actual reaching creature's Construct/Undead exclusions.",
        [_entity("actor_state")],
        [
            _require(
                "actor_state.creature_type != null",
                "Antilife Shell requires the reaching creature's actual saved type.",
            ),
            _set(
                "_antilife_reach_blocked",
                "actor_state.creature_type != 'construct' and "
                "actor_state.creature_type != 'undead'",
            ),
        ],
    )
    return {"check_srd_barrier_effect": effect, "check_srd_antilife_reaching_creature": reaching}


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _effect_lists(effect[key])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def extend_runtime(text):
    """Compose a source-owned family; final movement/reaction consumers retain their owners."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_geometry_tools(),
        **_source_tools(),
        **_point_tools(),
        **_antilife_tools(),
        **_public_tools(),
        **_effect_tools(),
        "check_srd_barrier_movement": _movement_tool(),
        "cast_srd_spell_barrier": _cast_tool(),
        "resolve_srd_resilient_creature_target": _creature_target_tool(),
        "normalize_srd_barrier_scene_subject": {
            "description": "Retain an optional raw scene ID before any creature snapshot binding.",
            "parameters": [_optional("subject", default=None)],
            "effects": [
                _set("_barrier_raw_scene_subject", ""),
                _when("subject != null", [_set("_barrier_raw_scene_subject", "subject")]),
            ],
            "metadata": {"category": "internal", "visible_to_player": False, "costs_action": False},
        },
    }
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    choices = [
        _optional("barrier_subject", default=""),
        _optional("barrier_interior_light", default="dim"),
        _optional("barrier_exterior_color", default="gray"),
    ]
    cast["parameters"] += [p for p in choices if p["name"] not in names]
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'spell_barrier'" not in e.get("condition", "")
        ]
    branch = _when(
        "_spell_definition.family == 'spell_barrier'",
        [
            _call(
                "normalize_srd_barrier_scene_subject",
                {"subject": "{barrier_subject}"},
                ("_barrier_raw_scene_subject",),
            ),
            _set("_selected_barrier_subject", "_barrier_raw_scene_subject"),
            _when(
                "_spell_definition.barrier_mode == 'resilient' and _selected_barrier_subject == ''",
                [
                    _set("_selected_barrier_subject", "_scalar_target_id"),
                ],
            ),
            _when(
                "_spell_definition.barrier_mode == 'resilient'",
                [
                    _require(
                        "_selected_barrier_subject != null and _selected_barrier_subject != ''",
                        "Select the sphere's actual validated creature target or scene object.",
                    ),
                ],
            ),
            _call(
                "cast_srd_spell_barrier",
                {
                    "spell": "{spell}",
                    "caster_state": "{_caster_state_id}",
                    "slot_level": "{_slot_level}",
                    "save_dc": "{_casting_profile.save_dc}",
                    "concentration_save_modifier": "{_casting_profile.concentration_save_modifier}",
                    "subject": "{_selected_barrier_subject}",
                    "expected_target_state": "{_scalar_target_id}",
                    "validate_target_binding": True,
                    "spell_cast_id": "{_spell_source_cast_id}",
                    "target_willing": "{target_willing}",
                    "interior_light": "{barrier_interior_light}",
                    "exterior_color": "{barrier_exterior_color}",
                },
                ("_spell_summary_rows", "_spell_detail_rows", "_barrier_cast_failed"),
            ),
            _when("_barrier_cast_failed == true", [_set("_spell_cast_failed", True)]),
        ],
    )
    index = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(index + 1, branch)
    changed["cast_spell"] = cast
    end = deepcopy(tools["end_srd_concentration_node"])
    for effects in _effect_lists(end["effects"]):
        effects[:] = [
            e
            for e in effects
            if "source_state.concentration_effect_family == "
            "'spell_barrier'" not in e.get("condition", "")
        ]
    end["effects"].insert(
        0,
        _when(
            "source_state.concentration_effect_family == 'spell_barrier' "
            "and source_state.barrier_source_id != null",
            [
                _call(
                    "retire_srd_spell_barrier_record",
                    {"source_id": "{source_state.barrier_source_id}", "reason": "concentration"},
                ),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = end
    text = _replace_tools(text, changed)
    schema = yaml.safe_dump({"spell_barrier": _schema()}, sort_keys=False)
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      spell_barrier:" in text:
        text = re.sub(
            r"(?ms)^      spell_barrier:\n.*?(?=^      [a-z])",
            lambda _: schema + "\n",
            text,
            count=1,
        )
    else:
        text = text.replace("      spell_environment:", schema + "\n      spell_environment:", 1)
    return text


def bind_runtime_consumers(text):
    """Bind after travel's final segment owners; no no-op barrier can report a false arrival."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("move_srd_travel_subject", "move_srd_scene_creature_environment_segment"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        for effects in _effect_lists(tool["effects"]):
            effects[:] = [e for e in effects if "check_srd_barrier_movement" not in str(e)]
        if name == "move_srd_travel_subject":
            if not any(p["name"] == "sphere_source_id" for p in tool["parameters"]):
                tool["parameters"].append(
                    _optional("sphere_source_id", default="", model_hidden=True)
                )
            check = _call(
                "check_srd_barrier_movement",
                {
                    "subject": "{subject}",
                    "destination": "{destination}",
                    "plane": "{plane}",
                    "teleport": "{teleport}",
                    "sphere_source_id": "{sphere_source_id}",
                },
            )
            before = [check]
        else:
            before = [
                *_scene("_actor_state_id", "_moving_barrier_subject"),
                _call(
                    "check_srd_barrier_movement",
                    {
                        "subject": "{_actor_state_id}",
                        "destination": "{destination}",
                        "plane": "{_moving_barrier_subject.metadata.plane}",
                    },
                ),
            ]
        tool["effects"].insert(0, _when("lasting_state.has_spell_barriers == true", before))
        changed[name] = tool
    if "resolve_srd_information_light" in tools:
        light = deepcopy(tools["resolve_srd_information_light"])
        for effects in _effect_lists(light["effects"]):
            effects[:] = [e for e in effects if "resolve_srd_barrier_interior_light" not in str(e)]
        index = next(
            i
            for i, e in enumerate(light["effects"])
            if e.get("set", {}).get("field") == "_information_light"
            and e.get("set", {}).get("value") == "ambient"
        )
        light["effects"].insert(
            index + 1,
            _when(
                "lasting_state.has_spell_barriers == true",
                [
                    _call(
                        "resolve_srd_barrier_interior_light",
                        {"point": "{point}", "plane": "{plane}"},
                        ("_barrier_interior_light",),
                    ),
                    _when(
                        "_barrier_interior_light != null",
                        [
                            _set("_information_light", "_barrier_interior_light"),
                        ],
                    ),
                ],
            ),
        )
        changed["resolve_srd_information_light"] = light
    return _replace_tools(text, changed)
