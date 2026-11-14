"""Earthquake's ground tremor uses actual scene support, saves and concentration."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_body_soul_spells import _clock
    from .srd_control_behaviors import _when
    from .srd_feather_fall import _ground
    from .srd_scene_relations import (
        MEASURE_EXPORTS,
        POSITION_EXPORTS,
        _array,
        _snapshot,
        _tool_scene,
    )
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools
else:
    from srd_body_soul_spells import _clock
    from srd_control_behaviors import _when
    from srd_feather_fall import _ground
    from srd_scene_relations import (
        MEASURE_EXPORTS,
        POSITION_EXPORTS,
        _array,
        _snapshot,
        _tool_scene,
    )
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _loop, _optional, _push, _replace_tools

SOURCE_PAGE = 127
SOURCE_SHA = "17be018e35fb0282e55954f4b5ddb8fb3336e715278c1d31fd854fec8f00cd84"
MAX_TARGETS = 8
PROFILE = {
    "family": "earthquake_spell",
    "base_level": 8,
    "casting_time": "action",
    "range_kind": "point",
    "range_feet": 500,
    "concentration": True,
    "duration_rounds": 10,
}
CAST_CHOICES = [
    _array("earthquake_point", default=[0, 0, 0]),
    _optional("earthquake_fissures", "boolean", False),
    _optional("earthquake_structures", "boolean", False),
]
HIDDEN_SOURCE = [
    _optional("spell_cast_id", default="", model_hidden=True),
    _optional("spell_cast_owner", resolve="entity", model_hidden=True),
    _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True),
]
PUBLIC_TOOLS = set()
FAMILY_GUARD = "_spell_definition.family == 'earthquake_spell'"


def validate_profiles(definitions):
    """The pinned tremor has no upcast scaling or caller-selected DC/duration."""
    for spell, definition in definitions.items():
        if definition.get("family") == PROFILE["family"] and (
            spell != "earthquake" or definition != PROFILE
        ):
            raise ValueError("Earthquake differs from its pinned profile")


def _source():
    return [
        _require(
            "state.persistent_spell_areas != null and area_id in state.persistent_spell_areas",
            "Select an actual retained Earthquake source.",
        ),
        _set("_quake_source", "state.persistent_spell_areas[area_id]"),
        _require(
            f"_quake_source.profile == 'earthquake' and _quake_source.spell == 'earthquake' "
            f"and _quake_source.source_sha256 == '{SOURCE_SHA}' and _quake_source.id == area_id",
            "Earthquake requires its exact printed source and area identity.",
        ),
    ]


def _floor():
    return [
        _set("_quake_floor", None),
        _set("_quake_geometry", "state.spatial_scene.geometry"),
        _loop(
            "_quake_geometry",
            "_quake_piece",
            [
                _when(
                    "_quake_piece.id == _quake_ground_id",
                    [_set("_quake_floor", "_quake_piece")],
                )
            ],
        ),
        _require(
            "_quake_floor != null and _quake_floor.volume.kind == 'box' and "
            "_quake_floor.active == true and _quake_floor.metadata.ground == true",
            "This tremor requires an actual active flat Box floor.",
        ),
        *[
            _require(
                f"point[{axis}] - 100 >= _quake_floor.volume.min[{axis}] and "
                f"point[{axis}] + 100 <= _quake_floor.volume.max[{axis}]",
                "The full Earthquake disk must fit on the actual admitted flat floor.",
            )
            for axis in range(2)
        ],
        _require(
            "abs(point[2] - _quake_floor.volume.max[2]) < 0.000000001",
            "The chosen point must be on the actual upper ground face.",
        ),
    ]


def _geometry():
    return _tool_scene(
        "Validate the visible chosen ground and supported flat-floor tremor before paid costs.",
        [_entity("caster_state"), _array("point")],
        _snapshot()
        + [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_quake_plane", "_info_scene_entity.metadata.plane"),
            _require("_quake_plane != null", "Earthquake needs an actual admitted plane."),
            _call(
                "measure_srd_information_distance",
                {"start": "{_info_position}", "end": "{point}"},
                MEASURE_EXPORTS,
            ),
            _require("_scene_distance <= 500", "The Earthquake point is beyond 500 feet."),
            _call(
                "resolve_srd_illumination_point_sight",
                {"observer_state": "{_caster_state_id}", "point": "{point}"},
                ["_illumination_point_can_see"],
            ),
            _require("_illumination_point_can_see == true", "The ground point must be seen."),
            _call(
                "qualify_srd_environment_ground",
                {"point": "{point}", "plane": "{_quake_plane}"},
                ["_environment_ground_id"],
            ),
            _set("_quake_ground_id", "_environment_ground_id"),
            *_floor(),
            _call(
                "collect_srd_earthquake_targets",
                {"point": "{point}", "plane": "{_quake_plane}", "ground_id": "{_quake_ground_id}"},
                ["_quake_targets"],
            ),
            _loop(
                "_quake_targets",
                "_quake_harmed_id",
                [
                    _call(
                        "validate_srd_charmer_harm",
                        {
                            "actor_state": "{_caster_state_id}",
                            "target_state": "{_quake_harmed_id}",
                        },
                    )
                ],
            ),
        ],
    )


def _body_ground():
    """Read the existing actual support ray; no ground result is a genuine non-grounded body."""
    effects = [
        effect
        for effect in deepcopy(_ground())
        if effect.get("require", {}).get("condition") != "_fall_ground_height != null"
        and effect.get("set", {}).get("field") != "_fall_ground_position"
    ]
    return _tool_scene(
        "Derive actual current ground contact through the existing supporting-face ray.",
        [_entity("target_state")],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            *effects,
            _set("_quake_body", "_info_scene_entity"),
            _set("_quake_body_ground", "_fall_ground_id"),
            _set("_quake_body_grounded", False),
            _when(
                "_fall_ground_height != null and _info_scene_entity.metadata.flying != true",
                [
                    _set(
                        "_quake_body_grounded",
                        "abs(_info_position[2] - _fall_ground_height) < 0.000000001",
                    )
                ],
            ),
        ],
    )


def _targets():
    return _tool_scene(
        "Collect actual supported creature spaces in this retained flat-floor circle.",
        [_array("point"), _parameter("plane"), _parameter("ground_id")],
        [
            _set("_quake_targets", []),
            _set("_quake_scene_bodies", "state.spatial_scene.entities"),
            _loop(
                "_quake_scene_bodies",
                "_quake_scene_body",
                [
                    _when(
                        "_quake_scene_body.active == true and "
                        "_quake_scene_body.metadata.participant_ref != null and "
                        "_quake_scene_body.metadata.plane == plane",
                        [
                            _call(
                                "qualify_srd_earthquake_body",
                                {
                                    "point": "{point}",
                                    "plane": "{plane}",
                                    "ground_id": "{ground_id}",
                                    "target_state": "{_quake_scene_body.metadata.participant_ref}",
                                },
                                ["_quake_target_inside", "_quake_target_id"],
                            ),
                            _when(
                                "_quake_target_inside == true",
                                [
                                    _require(
                                        "_quake_target_id not in _quake_targets",
                                        "Earthquake scene body identity is ambiguous.",
                                    ),
                                    _require(
                                        f"len(_quake_targets) < {MAX_TARGETS}",
                                        "Earthquake supports at most eight grounded creatures.",
                                    ),
                                    _push("_quake_targets", "_quake_target_id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _qualify_body():
    distance = " + ".join(
        f"max(_quake_body.position[{axis}] - _quake_body.footprint.half_extents[{axis}] "
        f"- point[{axis}], point[{axis}] - "
        f"_quake_body.position[{axis}] - _quake_body.footprint.half_extents[{axis}], 0) ** 2"
        for axis in range(2)
    )
    return _tool_scene(
        "Intersect an actual ground-contact Box body with the retained 100-foot circle.",
        [_entity("target_state"), _array("point"), _parameter("plane"), _parameter("ground_id")],
        [
            _set("_quake_target_inside", False),
            _set("_quake_target_id", "_target_state_id"),
            _when(
                "target_state.is_alive == true",
                [
                    _call(
                        "query_srd_earthquake_body_ground",
                        {"target_state": "{_target_state_id}"},
                        ["_quake_body", "_quake_body_ground", "_quake_body_grounded"],
                    ),
                    _require(
                        "_quake_body.footprint.kind == 'box'",
                        "Earthquake currently qualifies actual Box creature spaces.",
                    ),
                    _when(
                        "_quake_body_grounded == true and "
                        "_quake_body_ground == ground_id and "
                        "_quake_body.metadata.plane == plane",
                        [_set("_quake_target_inside", distance + " <= 10000")],
                    ),
                ],
            ),
        ],
    )


def _save():
    save_params = {
        "actor_state": "{_target_state_id}",
        "ability": "dexterity",
        "kind": "saving_throw",
        "proficiency_factor": "none",
        "target_number": "{_quake_source.save_dc}",
        "save_avoids_condition": "none",
        "circumstantial_modifier": 0,
    }
    exports = ["_outcome", "_d20_test", "_adjusted_total"]
    return _tool_scene(
        "Roll actual Dexterity; failed tremors break Concentration even when Prone is immune.",
        [_entity("target_state"), _parameter("area_id")],
        _source()
        + [
            _require(
                "_quake_source.event_in_progress == true",
                "A tremor save belongs to an actual committed casting or caster-end event.",
            ),
            _when(
                "_quake_source.caster != _target_state_id",
                [
                    _call(
                        "record_srd_hostile_actor_pair",
                        {
                            "actor_state": "{_quake_source.caster}",
                            "target_state": "{_target_state_id}",
                        },
                    ),
                ],
            ),
            _when(
                "target_state.creature_kind == 'character'",
                [_call("d20_test", save_params | {"actor": "{_target_state_id}"}, exports)],
                [
                    _require(
                        "target_state.saving_throw_modifiers.dexterity != null",
                        "Earthquake needs the creature's actual Dexterity saving throw modifier.",
                    ),
                    _call(
                        "d20_test",
                        save_params
                        | {
                            "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                            "proficiency_bonus": 0,
                        },
                        exports,
                    ),
                ],
            ),
            _set("_quake_concentration_broken", False),
            _set("_quake_prone_applied", False),
            _when(
                "_outcome == 'failure'",
                [
                    _call(
                        "check_srd_target_condition_immunity",
                        {
                            "target_state": "{_target_state_id}",
                            "condition": "prone",
                            "source_state_id": "{_quake_source.caster}",
                        },
                        ["_source_condition_immune"],
                    ),
                    _when(
                        "_source_condition_immune != true",
                        [
                            _call(
                                "apply_srd_condition",
                                {
                                    "target": "{_target_state_id}",
                                    "condition": "prone",
                                    "source_state_id": "{_quake_source.caster}",
                                    "source_is_magical": True,
                                },
                            ),
                            _set("_quake_prone_applied", True),
                        ],
                    ),
                    _when(
                        "target_state.concentration_spell_id != null",
                        [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_target_state_id}",
                                    "target_state": "{target_state.concentration_target_state_id}",
                                },
                            ),
                            _set("_quake_concentration_broken", True),
                        ],
                    ),
                ],
            ),
            _set(
                "_quake_save_row",
                {
                    "target": "_target_state_id",
                    "name": "target_state.name",
                    "roll": "_d20_test.total",
                    "total": "_adjusted_total",
                    "dc": "_quake_source.save_dc",
                    "outcome": "_outcome",
                    "prone_applied": "_quake_prone_applied",
                    "concentration_broken": "_quake_concentration_broken",
                },
            ),
        ],
    )


def _retire():
    return _tool_scene(
        "Retire only this tremor and its terrain; raw concentration retirement makes no new calls.",
        [_parameter("area_id")],
        _source()
        + [
            _set("state.persistent_spell_areas.{area_id}.active", False),
            _set("_quake_zones", "state.spatial_scene.zones"),
            _set("_quake_retained", []),
            _loop(
                "_quake_zones",
                "_quake_zone",
                [
                    _when(
                        "_quake_zone.metadata.source_environment_id != area_id",
                        [_push("_quake_retained", "_quake_zone")],
                    )
                ],
            ),
            _when(
                "len(_quake_retained) != len(_quake_zones)",
                [
                    _set("state.spatial_scene.zones", "_quake_retained"),
                    {"increment": {"field": "state.spatial_scene.map.revision", "amount": 1}},
                    *_snapshot(),
                ],
            ),
            _set("_quake_live", []),
            _set("_quake_index", "state.earthquake_area_ids"),
            _loop(
                "_quake_index",
                "_quake_index_id",
                [
                    _when(
                        "state.persistent_spell_areas[_quake_index_id].active == true",
                        [_push("_quake_live", "_quake_index_id")],
                    ),
                ],
            ),
            _set("state.earthquake_area_ids", "_quake_live"),
            _set("state.has_earthquakes", "len(_quake_live) > 0"),
        ],
    )


def _event():
    return _tool_scene(
        "Resolve one actual tremor event without replaying its saved end-turn receipt.",
        [_parameter("area_id"), _parameter("event_turn", "integer")],
        _source()
        + [
            _require("_quake_source.active == true", "The tremor source has ended."),
            _when(
                "event_turn != _quake_source.last_end_turn",
                [
                    _set("state.persistent_spell_areas.{area_id}.last_end_turn", "event_turn"),
                    _set("state.persistent_spell_areas.{area_id}.event_in_progress", True),
                    _call(
                        "collect_srd_earthquake_targets",
                        {
                            "point": "{_quake_source.origin}",
                            "plane": "{_quake_source.plane}",
                            "ground_id": "{_quake_source.ground_id}",
                        },
                        ["_quake_targets"],
                    ),
                    _set("_quake_event_rows", []),
                    _loop(
                        "_quake_targets",
                        "_quake_actor",
                        [
                            _call(
                                "resolve_srd_earthquake_save",
                                {"target_state": "{_quake_actor}", "area_id": "{area_id}"},
                                ["_quake_save_row"],
                            ),
                            _push("_quake_event_rows", "_quake_save_row"),
                        ],
                    ),
                    _set("state.persistent_spell_areas.{area_id}.event_in_progress", False),
                    _set(
                        "state.persistent_spell_areas.{area_id}.last_event_rows",
                        "_quake_event_rows",
                    ),
                ],
            ),
        ],
    )


def _install():
    return _tool_scene(
        "Install the genuine paid source, initial tremor and actual circular ground terrain.",
        [
            _entity("caster_state"),
            _array("point"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _parameter("save_modifier", "integer"),
            *HIDDEN_SOURCE,
        ],
        [
            _require(
                "spell_cast_id != '' and spell_cast_id == caster_state.last_spell_source_cast_id "
                "and caster_state.last_spell_source_spell == 'earthquake' and "
                "_spell_cast_owner_id == _caster_state_id and actual_spell_level == slot_level",
                "Earthquake needs its actual paid caster, slot and committed cast identity.",
            ),
            _call(
                "validate_srd_earthquake_geometry",
                {"caster_state": "{_caster_state_id}", "point": "{point}"},
                ["_quake_ground_id", "_quake_plane"],
            ),
            _set("_quake_concentration_focus", ["_caster_state_id"]),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "targets": "{_quake_concentration_focus}",
                    "spell": "earthquake",
                    "duration_rounds": 10,
                    "save_modifier": "{save_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                    "spell_cast_owner": "{_caster_state_id}",
                    "actual_spell_level": "{actual_spell_level}",
                },
                ["_condition_concentration_sequence"],
            ),
            _clock(),
            _call(
                "format_srd_information_projection",
                {"message": "{_caster_state_id}:earthquake:{_condition_concentration_sequence}"},
                ["_info_projection_message"],
            ),
            _set("_quake_id", "_info_projection_message"),
            _when(
                "state.persistent_spell_areas == null", [_set("state.persistent_spell_areas", {})]
            ),
            _when(
                "state.persistent_spell_area_ids == null",
                [_set("state.persistent_spell_area_ids", [])],
            ),
            _set(
                "state.persistent_spell_areas.{_quake_id}",
                {
                    "id": "_quake_id",
                    "profile": "'earthquake'",
                    "spell": "'earthquake'",
                    "mode": "'earthquake'",
                    "caster": "_caster_state_id",
                    "active": True,
                    "source_sha256": repr(SOURCE_SHA),
                    "plane": "_quake_plane",
                    "origin": "point",
                    "ground_id": "_quake_ground_id",
                    "save_dc": "save_dc",
                    "sequence": "_condition_concentration_sequence",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 20",
                    "spell_level": "slot_level",
                    "concentration": True,
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "ongoing_duration": True,
                    "dispel_ongoing": True,
                    "last_end_turn": -2,
                    "event_in_progress": False,
                    "volume": {
                        "kind": "'cylinder'",
                        "center": "point",
                        "radius": 100,
                        "height": 0.000001,
                    },
                },
            ),
            _set("_quake_ids", "state.persistent_spell_area_ids"),
            _push("_quake_ids", "_quake_id"),
            _set("state.persistent_spell_area_ids", "_quake_ids"),
            _when("state.earthquake_area_ids == null", [_set("state.earthquake_area_ids", [])]),
            _set("_quake_own_ids", "state.earthquake_area_ids"),
            _push("_quake_own_ids", "_quake_id"),
            _set("state.earthquake_area_ids", "_quake_own_ids"),
            _set("state.has_earthquakes", True),
            _set("caster_state.earthquake_area_id", "_quake_id"),
            _set("caster_state.concentration_effect_family", "'earthquake_spell'"),
            _call("install_srd_environment_ground_zone", {"area_id": "{_quake_id}"}),
            _call(
                "resolve_srd_earthquake_event",
                {"area_id": "{_quake_id}", "event_turn": -1},
                ["_quake_event_rows"],
            ),
            _set(
                "_spell_summary_rows",
                [
                    {
                        "label": "'Earthquake'",
                        "value": "'100-foot ground circle; up to 1 minute'",
                    }
                ],
            ),
            _set("_spell_detail_rows", []),
            _loop(
                "_quake_event_rows",
                "_quake_row",
                [
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "Dexterity save: roll {_quake_row.roll}, "
                            "total {_quake_row.total} "
                            "vs DC {_quake_row.dc}: {_quake_row.outcome}. "
                            "Prone applied: {_quake_row.prone_applied}; "
                            "Concentration broken: {_quake_row.concentration_broken}.",
                        },
                        ["_info_projection_message"],
                    ),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "_quake_row.name",
                            "value": "_info_projection_message",
                        },
                    ),
                ],
            ),
        ],
    )


def _boundary():
    return _tool_scene(
        "Refresh exact tremor lifetimes and resolve only the genuine creating-caster turn end.",
        [
            _optional("boundary_actor", default=""),
            _parameter("boundary_phase"),
            _parameter("boundary_turn", "integer"),
        ],
        [
            _set("_quake_boundary_actor", "''"),
            _when(
                "boundary_actor != ''",
                [
                    _call(
                        "format_srd_information_projection",
                        {"message": "{boundary_actor}"},
                        ["_info_projection_message"],
                    ),
                    _set("_quake_boundary_actor", "_info_projection_message"),
                ],
            ),
            _set("_quake_ids", "state.earthquake_area_ids"),
            _loop(
                "_quake_ids",
                "_quake_area_id",
                [
                    _set("_quake_source", "state.persistent_spell_areas[_quake_area_id]"),
                    _when(
                        "_quake_source.profile == 'earthquake' and _quake_source.active == true",
                        [
                            _when(
                                "state.lasting_tick >= _quake_source.expires_tick",
                                [
                                    _call(
                                        "retire_srd_earthquake_source",
                                        {"area_id": "{_quake_area_id}"},
                                    )
                                ],
                                [
                                    _when(
                                        "boundary_phase == 'end' and "
                                        "_quake_boundary_actor == _quake_source.caster",
                                        [
                                            _call(
                                                "resolve_srd_earthquake_event",
                                                {
                                                    "area_id": "{_quake_area_id}",
                                                    "event_turn": "{boundary_turn}",
                                                },
                                            )
                                        ],
                                    )
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def extend_runtime(text):
    """Compose before final reaction captures; final movement qualification is a separate hook."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    existing = {p["name"] for p in cast["parameters"]}
    cast["parameters"].extend(deepcopy(p) for p in CAST_CHOICES if p["name"] not in existing)
    validate_guard = FAMILY_GUARD + " and _spell_cast_failed != true"
    commit_guard = (
        "_spell_cast_failed != true and _pending_spell_operation_lost != true and " + FAMILY_GUARD
    )
    cast["effects"] = [
        e for e in cast["effects"] if e.get("condition") not in {validate_guard, commit_guard}
    ]
    validate_at = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "resolve_srd_spell_materials"
    )
    cast["effects"].insert(
        validate_at,
        _when(
            validate_guard,
            [
                _require(
                    "earthquake_fissures != true and earthquake_structures != true",
                    "Earthquake fissure/collapse geometry and consequences are not yet supported.",
                ),
                _call(
                    "validate_srd_earthquake_geometry",
                    {"caster_state": "{_caster_state_id}", "point": "{earthquake_point}"},
                ),
            ],
        ),
    )
    commit_at = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field") == "_spell_card_summary"
    )
    cast["effects"].insert(
        commit_at,
        _when(
            commit_guard,
            [
                _when(
                    "_spell_source_cast_id == ''",
                    [
                        _call(
                            "begin_srd_spell_source_instance",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell": "earthquake",
                                "slot_level": "{_slot_level}",
                            },
                            ["_spell_source_cast_id"],
                        )
                    ],
                ),
                _call(
                    "cast_srd_earthquake",
                    {
                        "caster_state": "{_caster_state_id}",
                        "point": "{earthquake_point}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "save_modifier": "{_casting_profile.concentration_save_modifier}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                        "spell_cast_owner": "{_caster_state_id}",
                        "actual_spell_level": "{_slot_level}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
            ],
        ),
    )
    end = deepcopy(tools["end_srd_concentration_node"])
    end_guard = "source_state.concentration_effect_family == 'earthquake_spell'"
    end["effects"] = [e for e in end["effects"] if e.get("condition") != end_guard]
    end["effects"].insert(
        0,
        _when(
            end_guard,
            [
                _call(
                    "retire_srd_earthquake_source", {"area_id": "{source_state.earthquake_area_id}"}
                ),
                _set("source_state.earthquake_area_id", None),
            ],
        ),
    )
    boundary = deepcopy(tools["process_srd_turn_effects"])
    guard = "lasting_state.has_earthquakes == true"
    boundary["effects"] = [e for e in boundary["effects"] if e.get("condition") != guard]
    boundary["effects"].append(
        _when(
            guard,
            [
                _call(
                    "process_srd_earthquake_boundary",
                    {
                        "boundary_actor": "{boundary_actor}",
                        "boundary_phase": "{boundary_phase}",
                        "boundary_turn": "{boundary_turn}",
                    },
                )
            ],
        )
    )
    changed = {
        "cast_spell": cast,
        "end_srd_concentration_node": end,
        "process_srd_turn_effects": boundary,
        "cast_srd_earthquake": _install(),
        "validate_srd_earthquake_geometry": _geometry(),
        "query_srd_earthquake_body_ground": _body_ground(),
        "qualify_srd_earthquake_body": _qualify_body(),
        "collect_srd_earthquake_targets": _targets(),
        "resolve_srd_earthquake_save": _save(),
        "resolve_srd_earthquake_event": _event(),
        "retire_srd_earthquake_source": _retire(),
        "process_srd_earthquake_boundary": _boundary(),
    }
    text = _replace_tools(text, changed)
    if "      earthquake_spell:" not in text:
        properties = {
            k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
            for k, v in PROFILE.items()
            if k != "family"
        }
        schema = yaml.safe_dump(
            {
                PROFILE["family"]: {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                }
            },
            sort_keys=False,
        )
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text


def _route_ground():
    return _tool_scene(
        "Qualify only the actual still-active supporting ground face of this tremor.",
        [_parameter("area_id"), _array("point"), _parameter("plane")],
        _source()
        + [
            _set("_quake_route_applies", False),
            _when(
                "_quake_source.active == true and state.lasting_tick < "
                "_quake_source.expires_tick and _quake_source.plane == plane and "
                "abs(point[2] - _quake_source.origin[2]) < 0.000000001",
                [
                    _set("_quake_route_geometry", "state.spatial_scene.geometry"),
                    _loop(
                        "_quake_route_geometry",
                        "_quake_route_floor",
                        [
                            _when(
                                "_quake_route_floor.id == _quake_source.ground_id and "
                                "_quake_route_floor.active == true and "
                                "_quake_route_floor.metadata.ground == true and "
                                "_quake_route_floor.metadata.plane == plane and "
                                "_quake_route_floor.movement == 'blocking' and "
                                "_quake_route_floor.volume.kind == 'box'",
                                [
                                    _when(
                                        "abs(point[2] - _quake_route_floor.volume.max[2]) "
                                        "< 0.000000001 and "
                                        "point[0] >= _quake_route_floor.volume.min[0] and "
                                        "point[0] <= _quake_route_floor.volume.max[0] and "
                                        "point[1] >= _quake_route_floor.volume.min[1] and "
                                        "point[1] <= _quake_route_floor.volume.max[1]",
                                        [_set("_quake_route_applies", True)],
                                    )
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_movement_consumers(text):
    """Follow final terrain consumers; preserve their ordered interval and difficult-cost union."""
    tools = _load_runtime(text)["tools"]
    if "cast_srd_earthquake" not in tools:
        raise ValueError("Earthquake must precede its ground-cost consumer")
    route = deepcopy(tools["srd_information_movement_cost"])
    guard = "_route_zone.metadata.source_spell == 'earthquake' and _segment_hit == true"
    qualification = _when(
        guard,
        [
            _call(
                "qualify_srd_earthquake_route_ground",
                {
                    "area_id": "{_route_zone.metadata.source_environment_id}",
                    "point": "{_route_point}",
                    "plane": "{plane}",
                },
                ["_quake_route_applies"],
            ),
            _set("_segment_hit", "_quake_route_applies == true"),
        ],
    )

    def insert(effects):
        effects[:] = [e for e in effects if e.get("condition") != guard]
        matches = 0
        for node in list(effects):
            if node.get("condition", "").startswith(
                "_segment_hit == true and _route_zone.metadata.plane == plane"
            ):
                effects.insert(effects.index(node), deepcopy(qualification))
                matches += 1
            for key in ("on_true", "on_false"):
                if isinstance(node.get(key), list):
                    matches += insert(node[key])
            if "for_each" in node:
                matches += insert(node["for_each"]["do"])
        return matches

    if insert(route["effects"]) != 1:
        raise ValueError("Earthquake requires the actual unique route-zone midpoint owner")
    return _replace_tools(
        text,
        {
            "srd_information_movement_cost": route,
            "qualify_srd_earthquake_route_ground": _route_ground(),
        },
    )
