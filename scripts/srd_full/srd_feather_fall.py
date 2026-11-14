"""Feather Fall consumes a saved scene fall and the existing shared clock and positions."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _replace_tools

PROFILES = {
    "feather_fall": {
        "family": "reaction_spell",
        "casting_time": "reaction",
        "base_level": 1,
        "range_kind": "distance",
        "range_feet": 60,
        "effect_kind": "feather_fall",
        "effect_rounds": 10,
    }
}


def validate_profiles(definitions):
    """Keep the one-minute non-concentration Reaction profile bound to pinned p130."""
    if "feather_fall" in definitions and definitions["feather_fall"] != PROFILES["feather_fall"]:
        raise SystemExit("Feather Fall source profile differs")


def _targets(name, maximum=64):
    return _parameter(name, "array", items={"type": "string"}, min_items=1, max_items=maximum)


def _subject():
    return _call(
        "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
    )


def _ground():
    """Use the admitted downward ray and active blocking same-plane ground, never map bounds."""
    return [
        _require(
            "_info_scene_entity.metadata.plane != null", "A falling body needs its actual plane."
        ),
        _set("_fall_ground_height", None),
        _set("_fall_ground_id", None),
        _set(
            "_fall_ray_origin",
            ["_info_position[0]", "_info_position[1]", "_info_position[2] + 0.000001"],
        ),
        _set(
            "_fall_ray_end",
            ["_info_position[0]", "_info_position[1]", "state.spatial_scene.map.bounds.min[2]"],
        ),
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "ray",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "origin": "{_fall_ray_origin}",
                    "destination": "{_fall_ray_end}",
                    "include": ["geometry"],
                    "max_intersections": 1000,
                },
            },
            "store_as": "_fall_ground_ray",
        },
        _require(
            "_fall_ground_ray.truncated != true",
            "Fall ground support exceeds the admitted ray bound.",
        ),
        _set("_fall_hits", "_fall_ground_ray.intersections"),
        _set("_fall_geometry", "state.spatial_scene.geometry"),
        _loop(
            "_fall_hits",
            "_fall_hit",
            [
                _when(
                    "_fall_hit.normal[2] > 0 and _fall_hit.metadata.ground == true and "
                    "_fall_hit.metadata.plane == _info_scene_entity.metadata.plane",
                    [
                        _loop(
                            "_fall_geometry",
                            "_fall_support",
                            [
                                _when(
                                    "_fall_support.id == _fall_hit.id and _fall_support.active "
                                    "== true and "
                                    "_fall_support.movement == 'blocking'",
                                    [
                                        _when(
                                            "_fall_ground_height == null or _fall_hit.point[2] "
                                            "> _fall_ground_height",
                                            [
                                                _set("_fall_ground_height", "_fall_hit.point[2]"),
                                                _set("_fall_ground_id", "_fall_support.id"),
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
        _require(
            "_fall_ground_height != null",
            "A scene fall needs actual same-plane supporting ground below the body.",
        ),
        _set(
            "_fall_ground_position",
            ["_info_position[0]", "_info_position[1]", "_fall_ground_height"],
        ),
    ]


def _sweep(destination):
    return [
        _set("_fall_material_plane", "_info_scene_entity.metadata.plane == 'material'"),
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "sweep",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{_info_scene_entity.id}",
                    "destination": "{" + destination + "}",
                    "include_entities": False,
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{_info_scene_entity.metadata.plane}",
                        "missing_matches": "{_fall_material_plane}",
                    },
                },
            },
            "store_as": "_fall_clearance",
        },
        _require(
            "_fall_clearance.supported == true and _fall_clearance.clear == true",
            "The actual falling body needs a supported clear descent path.",
        ),
    ]


def _move(destination):
    return _call(
        "move_srd_travel_subject",
        {
            "subject": "{_target_state_id}",
            "destination": "{" + destination + "}",
            "plane": "{_info_scene_entity.metadata.plane}",
            "teleport": False,
        },
    )


def _automatic_move(destination):
    return [
        _set("target_state.scene_fall.settling", True),
        _move(destination),
        _set("target_state.scene_fall.settling", False),
    ]


def _qualify_tools():
    one = _tool_scene(
        "Qualify an explicit fall intent against the actual airborne body and landing geometry.",
        [
            _entity("target_state"),
            _parameter(
                "fall_cause", "enum", enum_values=["departure", "support_lost", "flight_ended"]
            ),
        ],
        [
            _require("target_state.is_alive == true", "Select an actual living falling creature."),
            _require(
                "target_state.scene_fall == null or (target_state.scene_fall.status != 'pending' "
                "and target_state.scene_fall.status != 'descending')",
                "This creature already has an unresolved actual scene fall.",
            ),
            _require(
                "fall_cause != 'flight_ended' or target_state.spell_flight_fall_pending == true",
                "A flight-ended fall needs the actual lost-flight source consequence.",
            ),
            _subject(),
            *_ground(),
            _require(
                "_info_position[2] > _fall_ground_height",
                "The body is already on its supporting ground.",
            ),
            *_sweep("_fall_ground_position"),
            _set(
                "_falling_event",
                {
                    "actor_id": "_target_state_id",
                    "scene_id": "_info_scene_entity.id",
                    "origin": "_info_position",
                    "plane": "_info_scene_entity.metadata.plane",
                    "ground_id": "_fall_ground_id",
                    "ground_position": "_fall_ground_position",
                    "distance": "_info_position[2] - _fall_ground_height",
                    "started_tick": "state.lasting_tick",
                    "cause": "fall_cause",
                },
            ),
        ],
    )
    many = _tool_scene(
        "Qualify simultaneous scene falls before Root opens their existing saved reaction window.",
        [
            _targets("target_states"),
            _parameter(
                "fall_cause", "enum", enum_values=["departure", "support_lost", "flight_ended"]
            ),
        ],
        [
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_falling_events", []),
            _set("_falling_targets", []),
            _loop(
                "target_states",
                "_fall_actor",
                [
                    _require(
                        "_fall_actor not in _falling_targets", "Choose each falling creature once."
                    ),
                    _call(
                        "qualify_srd_scene_fall_target",
                        {"target_state": "{_fall_actor}", "fall_cause": "{fall_cause}"},
                        ["_falling_event"],
                    ),
                    {"list_push": {"list": "_falling_events", "value": "_falling_event"}},
                    {"list_push": {"list": "_falling_targets", "value": "_fall_actor"}},
                ],
            ),
            _set("_fall_first_actor", "_falling_targets[0]"),
        ],
    )
    bind_one = _tool_scene(
        "Bind only a checked participant from the real saved scene-fall event.",
        [_entity("target_state"), _parameter("pending_action_id")],
        [
            _set("_bound_fall", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_bound_fall.kind == 'scene_fall' and _bound_fall.status == 'open'",
                "Bind an actual open scene-fall event.",
            ),
            _set("_bound_rows", "_bound_fall.parameters.falling_events"),
            _set("_bound_row", None),
            _loop(
                "_bound_rows",
                "_bound_candidate",
                [
                    _when(
                        "_bound_candidate.actor_id == _target_state_id",
                        [
                            _require(
                                "_bound_row == null", "The fall event has duplicate participants."
                            ),
                            _set("_bound_row", "_bound_candidate"),
                        ],
                    )
                ],
            ),
            _require("_bound_row != null", "This actor is not a checked falling participant."),
            _set(
                "target_state.scene_fall",
                {
                    "event_id": "pending_action_id",
                    "status": "'pending'",
                    "started_tick": "_bound_row.started_tick",
                    "settled_tick": "_bound_row.started_tick",
                    "scene_id": "_bound_row.scene_id",
                    "plane": "_bound_row.plane",
                    "origin": "_bound_row.origin",
                    "cause": "_bound_row.cause",
                },
            ),
            _when("state.falling_target_ids == null", [_set("state.falling_target_ids", [])]),
            _set("_fall_actor_ids", "state.falling_target_ids"),
            _when(
                "_target_state_id not in _fall_actor_ids",
                [
                    {"list_push": {"list": "_fall_actor_ids", "value": "_target_state_id"}},
                    _set("state.falling_target_ids", "_fall_actor_ids"),
                ],
            ),
            _set("state.has_scene_falls", True),
        ],
    )
    bind = _tool_scene(
        "Bind the exact checked saved event to its actual falling actors.",
        [_parameter("pending_action_id")],
        [
            _set(
                "_fall_bind_events",
                "state.pending_srd_actions[pending_action_id].parameters.falling_events",
            ),
            _loop(
                "_fall_bind_events",
                "_fall_bind_row",
                [
                    _call(
                        "bind_srd_scene_fall_target",
                        {
                            "target_state": "{_fall_bind_row.actor_id}",
                            "pending_action_id": "{pending_action_id}",
                        },
                    )
                ],
            ),
        ],
    )
    return {
        "qualify_srd_scene_fall_target": one,
        "qualify_srd_scene_fall": many,
        "bind_srd_scene_fall_target": bind_one,
        "bind_srd_scene_fall_event": bind,
    }


def _validate_tools():
    chosen = _tool_scene(
        "Check a selected real falling creature's actual same-plane casting range.",
        [_entity("caster_state"), _entity("target_state")],
        [
            _require(
                "target_state.scene_fall != null and (target_state.scene_fall.status == 'pending' "
                "or target_state.scene_fall.status == 'descending')",
                "Feather Fall selects actual saved falling creatures.",
            ),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ["_scene_distance", "_scene_same_plane"],
            ),
            _require(
                "_scene_same_plane == true and _scene_distance <= 60",
                "A selected falling creature must be within sixty actual feet.",
            ),
        ],
    )
    trigger_one = _tool_scene(
        "Derive sight of one actual triggering falling creature, including self.",
        [_entity("caster_state"), _entity("target_state")],
        [
            _set("_fall_trigger_seen", "_caster_state_id == _target_state_id"),
            _when(
                "_caster_state_id != _target_state_id",
                [
                    _call(
                        "resolve_srd_scene_geometry_relation",
                        {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                        ["_scene_distance", "_scene_same_plane"],
                    ),
                    _when(
                        "_scene_same_plane == true and _scene_distance <= 60",
                        [
                            _call(
                                "resolve_srd_illumination_target_sight",
                                {
                                    "observer_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                },
                                ["_illumination_target_can_see"],
                            ),
                            _set("_fall_trigger_seen", "_illumination_target_can_see == true"),
                        ],
                    ),
                ],
            ),
            _require(
                "target_state.scene_fall.status == 'pending'",
                "The triggering fall is no longer pending.",
            ),
        ],
    )
    validate = _tool_scene(
        "Validate Feather Fall against real saved falling participants; only the trigger needs "
        "sight.",
        [_entity("caster_state"), _parameter("reaction_trigger_id"), _targets("targets", 5)],
        [
            _require(
                "state.pending_srd_actions != null and reaction_trigger_id in "
                "state.pending_srd_actions",
                "Feather Fall needs the actual saved falling trigger.",
            ),
            _set("_feather_trigger", "state.pending_srd_actions[reaction_trigger_id]"),
            _require(
                "_feather_trigger.kind == 'scene_fall' and _feather_trigger.status == 'open' "
                "and _feather_trigger.stage == 'reaction'",
                "Feather Fall requires an open scene-fall reaction window.",
            ),
            _set("_feather_trigger_rows", "_feather_trigger.parameters.falling_events"),
            _set("_feather_trigger_qualified", False),
            _loop(
                "_feather_trigger_rows",
                "_feather_trigger_row",
                [
                    _call(
                        "qualify_srd_feather_fall_observation",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_feather_trigger_row.actor_id}",
                        },
                        ["_fall_trigger_seen"],
                    ),
                    _when("_fall_trigger_seen == true", [_set("_feather_trigger_qualified", True)]),
                ],
            ),
            _require(
                "_feather_trigger_qualified == true",
                "See a falling creature within sixty feet, or be falling yourself.",
            ),
            _set("_feather_chosen", []),
            _loop(
                "targets",
                "_feather_target",
                [
                    _require(
                        "_feather_target not in _feather_chosen",
                        "Choose each Feather Fall target once.",
                    ),
                    _call(
                        "qualify_srd_feather_fall_target",
                        {"caster_state": "{_caster_state_id}", "target_state": "{_feather_target}"},
                    ),
                    {"list_push": {"list": "_feather_chosen", "value": "_feather_target"}},
                ],
            ),
        ],
    )
    return {
        "qualify_srd_feather_fall_target": chosen,
        "qualify_srd_feather_fall_observation": trigger_one,
        "validate_srd_feather_fall_trigger": validate,
    }


def _source_tools():
    install_one = _tool_scene(
        "Install one exact non-concentration Feather Fall source on an actual falling target.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("instance", "integer"),
            _parameter("reaction_trigger_id"),
            _parameter("actual_spell_level", "integer", minimum=1, maximum=9),
            _optional("spell_cast_id", default=""),
        ],
        [
            _set("_feather_sources", "state.turn_effects"),
            {
                "list_push": {
                    "list": "_feather_sources",
                    "value": {
                        "kind": "'feather_fall'",
                        "spell": "'feather_fall'",
                        "caster": "_caster_state_id",
                        "target": "_target_state_id",
                        "instance": "instance",
                        "active": True,
                        "started_tick": "state.lasting_tick",
                        "expires_tick": "state.lasting_tick + 20",
                        "concentration": False,
                        "spell_level": "actual_spell_level",
                        "cast_id": "spell_cast_id",
                        "source_caster_id": "_caster_state_id",
                        "ongoing_duration": True,
                        "dispel_ongoing": True,
                        "trigger_id": "reaction_trigger_id",
                    },
                }
            },
            _set("state.turn_effects", "_feather_sources"),
            _set(
                "_feather_summary",
                {
                    "label": "target_state.name",
                    "value": "'Descent 60 feet per round; safe landing before expiry.'",
                },
            ),
        ],
    )
    install = _tool_scene(
        "Apply the paid Reaction's source-owned ten-round descent to at most five real falling "
        "targets.",
        [
            _entity("caster_state"),
            _parameter("reaction_trigger_id"),
            _targets("targets", 5),
            _optional("actual_spell_level", "integer", 1, minimum=1, maximum=9),
            _optional("spell_cast_id", default=""),
        ],
        [
            _call(
                "validate_srd_feather_fall_trigger",
                {
                    "caster_state": "{_caster_state_id}",
                    "reaction_trigger_id": "{reaction_trigger_id}",
                    "targets": "{targets}",
                },
            ),
            _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
            _when("state.feather_fall_serial == null", [_set("state.feather_fall_serial", 0)]),
            _set("state.feather_fall_serial", "state.feather_fall_serial + 1"),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _loop(
                "targets",
                "_installed_feather_target",
                [
                    _call(
                        "install_srd_feather_fall_target",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_installed_feather_target}",
                            "instance": "{state.feather_fall_serial}",
                            "reaction_trigger_id": "{reaction_trigger_id}",
                            "actual_spell_level": "{actual_spell_level}",
                            "spell_cast_id": "{spell_cast_id}",
                        },
                        ["_feather_summary"],
                    ),
                    {"list_push": {"list": "_spell_summary_rows", "value": "_feather_summary"}},
                    {"list_push": {"list": "_spell_detail_rows", "value": "_feather_summary"}},
                ],
            ),
        ],
    )
    active = _tool_scene(
        "Read only authentic installed Feather Fall instances for this falling actor and interval.",
        [_entity("target_state")],
        [
            _set("_feather_expires_tick", None),
            _set("_feather_source_found", False),
            _when(
                "state.turn_effects != null",
                [
                    _set("_feather_effects", "state.turn_effects"),
                    _loop(
                        "_feather_effects",
                        "_feather_effect",
                        [
                            _when(
                                "_feather_effect.kind == 'feather_fall' and "
                                "_feather_effect.spell == 'feather_fall' and "
                                "_feather_effect.target == _target_state_id and "
                                "_feather_effect.active == true and "
                                "_feather_effect.expires_tick > "
                                "target_state.scene_fall.settled_tick",
                                [
                                    _set("_feather_source_found", True),
                                    _when(
                                        "_feather_expires_tick == null or "
                                        "_feather_effect.expires_tick > _feather_expires_tick",
                                        [
                                            _set(
                                                "_feather_expires_tick",
                                                "_feather_effect.expires_tick",
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
    retire = _tool_scene(
        "Retire only this creature's actual Feather Fall sources after landing or expiry.",
        [_entity("target_state"), _parameter("reason")],
        [
            _when(
                "state.turn_effects != null",
                [
                    _set("_retire_feather_sources", "state.turn_effects"),
                    _set("_kept_feather_sources", []),
                    _loop(
                        "_retire_feather_sources",
                        "_retired_feather",
                        [
                            _when(
                                "_retired_feather.kind == 'feather_fall' and "
                                "_retired_feather.target == _target_state_id and "
                                "_retired_feather.active == true",
                                [
                                    _set(
                                        "_retired_feather_patch",
                                        {"active": False, "ended_reason": "reason"},
                                    ),
                                    _set(
                                        "_retired_feather",
                                        "merge(_retired_feather, _retired_feather_patch)",
                                    ),
                                ],
                            ),
                            {
                                "list_push": {
                                    "list": "_kept_feather_sources",
                                    "value": "_retired_feather",
                                }
                            },
                        ],
                    ),
                    _set("state.turn_effects", "_kept_feather_sources"),
                ],
            ),
        ],
    )
    return {
        "install_srd_feather_fall_target": install_one,
        "install_srd_feather_fall": install,
        "read_srd_feather_fall_sources": active,
        "retire_srd_feather_fall_target": retire,
    }


def _land(reason, damage=False):
    effects = [*_sweep("_fall_ground_position")]
    if damage:
        effects += [
            _set("_fall_remaining_distance", "max(0, _info_position[2] - _fall_ground_height)"),
            _set("_fall_effective_damage", 0),
            _set("_fall_hp_lost", 0),
            _set("_fall_temp_hp_absorbed", 0),
            _when(
                "target_state.is_alive == true",
                [
                    _call(
                        "resolve_srd_fall",
                        {
                            "target_state": "{_target_state_id}",
                            "distance": "{_fall_remaining_distance}",
                            "landing": "ground",
                            "reaction": "none",
                        },
                        ["_fall_effective_damage", "_fall_hp_lost", "_fall_temp_hp_absorbed"],
                    )
                ],
            ),
        ]
    else:
        effects += [
            _set("_fall_effective_damage", 0),
            _set("_fall_hp_lost", 0),
            _set("_fall_temp_hp_absorbed", 0),
        ]
    effects += [
        *_automatic_move("_fall_ground_position"),
        _set("target_state.scene_fall.status", "'landed'"),
        _set("target_state.scene_fall.ended_reason", "'" + reason + "'"),
        _set("target_state.scene_fall.settled_tick", "state.lasting_tick"),
        _set("target_state.spell_flight_fall_pending", False),
        _set("target_state.flying_aloft", False),
        _call(
            "retire_srd_feather_fall_target",
            {"target_state": "{_target_state_id}", "reason": reason},
        ),
    ]
    return effects


def _settle_tools():
    settle = _tool_scene(
        "Settle actual shared-clock descent; ordinary falls use only the real remaining distance.",
        [_entity("target_state"), _optional("resolve_pending", "boolean", False)],
        [
            _set("_scene_fall_pending", False),
            _set("_fall_effective_damage", 0),
            _when(
                "target_state.scene_fall != null and (target_state.scene_fall.status == "
                "'descending' or "
                "(resolve_pending == true and target_state.scene_fall.status == 'pending'))",
                [
                    _subject(),
                    *_ground(),
                    _require(
                        "_info_scene_entity.id == target_state.scene_fall.scene_id and "
                        "_info_scene_entity.metadata.plane == target_state.scene_fall.plane",
                        "The falling body changed scene identity or plane before its fall settled.",
                    ),
                    _call(
                        "read_srd_scene_descent_sources",
                        {"target_state": "{_target_state_id}"},
                        ["_slow_descent_sources_found", "_slow_descent_end_tick"],
                    ),
                    {
                        "condition": "_slow_descent_sources_found == true",
                        "on_true": [
                            _set("target_state.scene_fall.status", "'descending'"),
                            _set(
                                "_fall_interval_end",
                                "min(state.lasting_tick, _slow_descent_end_tick)",
                            ),
                            _set(
                                "_fall_elapsed",
                                "max(0, _fall_interval_end - target_state.scene_fall.settled_tick)",
                            ),
                            _set(
                                "_fall_height",
                                "max(_fall_ground_height, _info_position[2] - _fall_elapsed * 30)",
                            ),
                            _set(
                                "_fall_destination",
                                ["_info_position[0]", "_info_position[1]", "_fall_height"],
                            ),
                            _when(
                                "_fall_elapsed > 0",
                                [
                                    *_sweep("_fall_destination"),
                                    *_automatic_move("_fall_destination"),
                                ],
                            ),
                            _set("target_state.scene_fall.settled_tick", "_fall_interval_end"),
                            {
                                "condition": "_fall_height <= _fall_ground_height",
                                "on_true": _land("feather_landing"),
                                "on_false": [
                                    _when(
                                        "state.lasting_tick >= _slow_descent_end_tick",
                                        [
                                            _subject(),
                                            *_ground(),
                                            *_land("feather_expired", damage=True),
                                        ],
                                    )
                                ],
                            },
                        ],
                        "on_false": _land("ordinary_fall", damage=True),
                    },
                ],
            ),
            _when(
                "target_state.scene_fall != null and target_state.scene_fall.status == 'arrested'",
                [
                    _set("target_state.scene_fall.settled_tick", "state.lasting_tick"),
                    _call(
                        "read_srd_feather_fall_sources",
                        {"target_state": "{_target_state_id}"},
                        ["_feather_source_found"],
                    ),
                    _set("_scene_fall_pending", "_feather_source_found"),
                ],
            ),
            _when(
                "target_state.scene_fall != null",
                [
                    _set("_scene_fall_landed", "target_state.scene_fall.status == 'landed'"),
                    _call(
                        "settle_srd_scene_descent_receipts",
                        {"target_state": "{_target_state_id}", "landed": "{_scene_fall_landed}"},
                    ),
                ],
            ),
            _when(
                "target_state.scene_fall != null and (target_state.scene_fall.status == 'pending' "
                "or target_state.scene_fall.status == 'descending')",
                [_set("_scene_fall_pending", True)],
            ),
            _set("_scene_fall_status", "target_state.scene_fall.status"),
        ],
    )
    resolve = _tool_scene(
        "Resume the exact saved fall once its reaction window completes.",
        [_parameter("pending_action_id")],
        [
            _set("_resolved_fall_event", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_resolved_fall_event.kind == 'scene_fall' and _resolved_fall_event.status == "
                "'open'",
                "Resolve the actual open scene-fall event.",
            ),
            _set("_resolve_fall_rows", "_resolved_fall_event.parameters.falling_events"),
            _set("_scene_fall_summary_rows", []),
            _set("_scene_fall_detail_rows", []),
            _loop(
                "_resolve_fall_rows",
                "_resolve_fall_row",
                [
                    _call(
                        "settle_srd_scene_fall_target",
                        {"target_state": "{_resolve_fall_row.actor_id}", "resolve_pending": True},
                        ["_scene_fall_status", "_fall_effective_damage"],
                    ),
                    _call(
                        "record_srd_result_text",
                        {
                            "text": "{_resolve_fall_row.actor_id}: {_scene_fall_status}; damage "
                            "{_fall_effective_damage}."
                        },
                        ["_recorded_result_text"],
                    ),
                    {
                        "list_push": {
                            "list": "_scene_fall_summary_rows",
                            "value": {
                                "label": "_resolve_fall_row.actor_id",
                                "value": "_recorded_result_text",
                            },
                        }
                    },
                    {
                        "list_push": {
                            "list": "_scene_fall_detail_rows",
                            "value": {
                                "label": "_resolve_fall_row.actor_id",
                                "value": "_recorded_result_text",
                            },
                        }
                    },
                ],
            ),
        ],
    )
    return {"settle_srd_scene_fall_target": settle, "resolve_srd_scene_fall_event": resolve}


def _continuation_tools():
    """Retire exact sources and observe completed relocation without recursively moving the body."""
    retire = _tool_scene(
        "Retire one exact target/caster/instance while preserving independent Feather sources.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("instance", "integer", minimum=1),
            _parameter("reason"),
        ],
        [
            _set("_feather_instance_retired", False),
            _set("_instance_feather_rows", "state.turn_effects"),
            _set("_instance_feather_kept", []),
            _loop(
                "_instance_feather_rows",
                "_instance_feather",
                [
                    _when(
                        "_instance_feather.kind == 'feather_fall' and "
                        "_instance_feather.spell == 'feather_fall' and "
                        "_instance_feather.target == _target_state_id and "
                        "_instance_feather.caster == _caster_state_id and "
                        "_instance_feather.instance == instance and "
                        "_instance_feather.active == true",
                        [
                            _set(
                                "_instance_feather_patch",
                                {"active": False, "ended_reason": "reason"},
                            ),
                            _set(
                                "_instance_feather",
                                "merge(_instance_feather, _instance_feather_patch)",
                            ),
                            _set("_feather_instance_retired", True),
                        ],
                    ),
                    {"list_push": {"list": "_instance_feather_kept", "value": "_instance_feather"}},
                ],
            ),
            _set("state.turn_effects", "_instance_feather_kept"),
            _when(
                "_feather_instance_retired == true and target_state.scene_fall != null and "
                "target_state.scene_fall.status == 'descending'",
                [
                    _call("settle_srd_scene_fall_target", {"target_state": "{_target_state_id}"}),
                ],
            ),
        ],
    )
    landing_target = _tool_scene(
        "Observe actual relocation onto ground without a second movement or damage packet.",
        [_entity("target_state")],
        [
            _when(
                "target_state.scene_fall != null and "
                "(target_state.scene_fall.status == 'descending' or "
                "target_state.scene_fall.status == 'arrested') "
                "and target_state.scene_fall.settling != true",
                [
                    _subject(),
                    *_ground(),
                    _set("target_state.scene_fall.plane", "_info_scene_entity.metadata.plane"),
                    _when(
                        "_info_position[2] <= _fall_ground_height",
                        [
                            _set("target_state.scene_fall.status", "'landed'"),
                            _set("target_state.scene_fall.ended_reason", "'feather_landing'"),
                            _set("target_state.scene_fall.settled_tick", "state.lasting_tick"),
                            _set("target_state.spell_flight_fall_pending", False),
                            _set("target_state.flying_aloft", False),
                            _call(
                                "retire_srd_feather_fall_target",
                                {"target_state": "{_target_state_id}", "reason": "feather_landing"},
                            ),
                            _call(
                                "settle_srd_scene_descent_receipts",
                                {"target_state": "{_target_state_id}", "landed": True},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    landing = _tool_scene(
        "Observe only an actual relocated creature from the sole admitted scene.",
        [_parameter("subject")],
        [
            _call("srd_information_scene_entity", {"subject": "{subject}"}, POSITION_EXPORTS),
            _when(
                "_info_scene_entity.metadata.participant_ref != null",
                [
                    _call(
                        "observe_srd_feather_fall_landing_target",
                        {"target_state": "{_info_scene_entity.metadata.participant_ref}"},
                    ),
                ],
            ),
        ],
    )
    arrest = _tool_scene(
        "Observe genuine successful flying movement without retiring independent Feather grants.",
        [_entity("actor_state")],
        [
            _when(
                "actor_state.scene_fall != null and actor_state.scene_fall.status == 'descending'",
                [
                    _call(
                        "read_srd_scene_descent_sources",
                        {"target_state": "{_actor_state_id}"},
                        ["_wind_descent_owned"],
                    ),
                    _when(
                        "_wind_descent_owned == true",
                        [
                            _set("actor_state.wind_walk_descent.status", "'arrested'"),
                            _set(
                                "actor_state.wind_walk_descent.settled_tick",
                                "min(state.lasting_tick, actor_state.wind_walk_descent.end_tick)",
                            ),
                        ],
                    ),
                    _set("actor_state.scene_fall.status", "'arrested'"),
                    _set("actor_state.scene_fall.ended_reason", "'resumed_flight'"),
                    _set("actor_state.scene_fall.settled_tick", "state.lasting_tick"),
                    _set("actor_state.spell_flight_fall_pending", False),
                    _set("actor_state.flying_aloft", True),
                ],
            ),
        ],
    )
    return {
        "retire_srd_feather_fall_instance": retire,
        "observe_srd_feather_fall_landing_target": landing_target,
        "observe_srd_feather_fall_landing": landing,
        "observe_srd_scene_flight_movement": arrest,
    }


def _shared_descent_tools():
    """One body descends once while each authenticated source keeps its own lifetime."""
    read = _tool_scene(
        "Read exact Feather and ended Wind source windows without extending either source.",
        [_entity("target_state")],
        [
            _call(
                "read_srd_feather_fall_sources",
                {"target_state": "{_target_state_id}"},
                ["_feather_source_found", "_feather_expires_tick"],
            ),
            _set("_slow_descent_sources_found", "_feather_source_found"),
            _set("_slow_descent_end_tick", None),
            _when(
                "_feather_source_found == true",
                [
                    _set("_slow_descent_end_tick", "_feather_expires_tick"),
                ],
            ),
            _set("_wind_descent_owned", False),
            _when(
                "target_state.wind_walk_descent != null and "
                "(target_state.wind_walk_descent.status == 'pending' or "
                "target_state.wind_walk_descent.status == 'arrested') and "
                "state.transformation_sources != null and "
                "target_state.wind_walk_descent.source_id in state.transformation_sources",
                [
                    _set(
                        "_shared_wind_source",
                        "state.transformation_sources[target_state.wind_walk_descent.source_id]",
                    ),
                    _set(
                        "_wind_descent_owned",
                        "_shared_wind_source.spell_id == 'wind_walk' and "
                        "_shared_wind_source.target_state_id == _target_state_id and "
                        "_shared_wind_source.serial == "
                        "target_state.wind_walk_descent.source_serial and "
                        "target_state.wind_walk_descent.end_tick == "
                        "target_state.wind_walk_descent.started_tick + 20 and "
                        "(_shared_wind_source.installed != true or "
                        "state.lasting_tick >= _shared_wind_source.expires_tick)",
                    ),
                    _when(
                        "_wind_descent_owned == true and "
                        "target_state.wind_walk_descent.status == 'pending' and "
                        "target_state.wind_walk_descent.end_tick > "
                        "target_state.scene_fall.settled_tick",
                        [
                            _set("_slow_descent_sources_found", True),
                            _when(
                                "_slow_descent_end_tick == null or "
                                "target_state.wind_walk_descent.end_tick > _slow_descent_end_tick",
                                [
                                    _set(
                                        "_slow_descent_end_tick",
                                        "target_state.wind_walk_descent.end_tick",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    receipts = _tool_scene(
        "Settle each exact slow-source receipt while an independent source continues protection.",
        [_entity("target_state"), _parameter("landed", "boolean")],
        [
            _call(
                "read_srd_scene_descent_sources",
                {"target_state": "{_target_state_id}"},
                ["_wind_descent_owned"],
            ),
            _when(
                "_wind_descent_owned == true",
                [
                    _set(
                        "target_state.wind_walk_descent.settled_tick",
                        "min(state.lasting_tick, target_state.wind_walk_descent.end_tick)",
                    ),
                    {
                        "condition": "landed == true",
                        "on_true": [
                            _set("target_state.wind_walk_descent.status", "'landed'"),
                            _when(
                                "target_state.scene_fall.ended_reason != 'feather_landing'",
                                [
                                    _set("target_state.wind_walk_descent.status", "'fallen'"),
                                ],
                            ),
                        ],
                        "on_false": [
                            _when(
                                "state.lasting_tick >= target_state.wind_walk_descent.end_tick",
                                [
                                    _set("target_state.wind_walk_descent.status", "'expired'"),
                                ],
                            )
                        ],
                    },
                ],
            ),
            _when(
                "state.turn_effects != null",
                [
                    _set("_expired_feather_rows", "state.turn_effects"),
                    _set("_expired_feather_kept", []),
                    _loop(
                        "_expired_feather_rows",
                        "_expired_feather",
                        [
                            _when(
                                "_expired_feather.kind == 'feather_fall' and "
                                "_expired_feather.target == _target_state_id and "
                                "_expired_feather.active == true and "
                                "_expired_feather.expires_tick <= state.lasting_tick",
                                [
                                    _set(
                                        "_expired_feather_patch",
                                        {"active": False, "ended_reason": "'expiry'"},
                                    ),
                                    _set(
                                        "_expired_feather",
                                        "merge(_expired_feather, _expired_feather_patch)",
                                    ),
                                ],
                            ),
                            {
                                "list_push": {
                                    "list": "_expired_feather_kept",
                                    "value": "_expired_feather",
                                }
                            },
                        ],
                    ),
                    _set("state.turn_effects", "_expired_feather_kept"),
                ],
            ),
        ],
    )
    return {"read_srd_scene_descent_sources": read, "settle_srd_scene_descent_receipts": receipts}


def bind_runtime_consumers(text):
    """Bind actual relocation and defer Wind's physical step to the authenticated shared fall."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in (
        "move_srd_travel_subject",
        "move_srd_scene_creature_environment_segment",
        "move_srd_scene_creature_node",
    ):
        if name not in tools or not any(
            "spatial_mutation" in str(effect) for effect in tools[name]["effects"]
        ):
            continue
        tool = deepcopy(tools[name])
        if "observe_srd_feather_fall_landing" not in str(tool["effects"]):
            subject = "{subject}" if name == "move_srd_travel_subject" else "{_actor_state_id}"
            tool["effects"].append(
                _when(
                    "lasting_state.has_scene_falls == true",
                    [
                        _call("observe_srd_feather_fall_landing", {"subject": subject}),
                    ],
                )
            )
        if name != "move_srd_travel_subject" and "observe_srd_scene_flight_movement" not in str(
            tool["effects"]
        ):
            tool["effects"].append(
                _when(
                    "lasting_state.has_scene_falls == true and mode == 'fly' and "
                    "_scene_movement_distance > 0.000000001",
                    [
                        _call(
                            "observe_srd_scene_flight_movement",
                            {"actor_state": "{_actor_state_id}"},
                        ),
                    ],
                )
            )
        changed[name] = tool
    name = "settle_srd_wind_walk_descent_target"
    if name in tools:
        tool = deepcopy(tools[name])
        guard = (
            "target_state.scene_fall != null and (target_state.scene_fall.status == 'pending' "
            "or target_state.scene_fall.status == 'descending')"
        )
        suffix = " and not (" + guard + ")"
        for effect in tool["effects"]:
            clause = effect.get("condition", "")
            if "target_state.wind_walk_descent.settled_tick" in clause and " > " in clause:
                effect["condition"] = clause.removesuffix(suffix) + suffix
        changed[name] = tool
    return _replace_tools(text, changed)


def extend_runtime(text):
    """Add source leaves and the real clock consumer; Root owns public event/reaction dispatch."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_qualify_tools(),
        **_validate_tools(),
        **_source_tools(),
        **_settle_tools(),
        **_continuation_tools(),
        **_shared_descent_tools(),
    }
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    clock["effects"] = [
        effect
        for effect in clock["effects"]
        if effect.get("condition") != "lasting_state.has_scene_falls == true"
    ]
    clock["effects"].append(
        _when(
            "lasting_state.has_scene_falls == true",
            [
                _set("_clock_falling_targets", "lasting_state.falling_target_ids"),
                _set("_remaining_falling_targets", []),
                _loop(
                    "_clock_falling_targets",
                    "_clock_falling_actor",
                    [
                        _call(
                            "settle_srd_scene_fall_target",
                            {
                                "target_state": "{_clock_falling_actor}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                            ["_scene_fall_pending"],
                        ),
                        _when(
                            "_scene_fall_pending == true",
                            [
                                {
                                    "list_push": {
                                        "list": "_remaining_falling_targets",
                                        "value": "_clock_falling_actor",
                                    }
                                },
                            ],
                        ),
                    ],
                ),
                _set("lasting_state.falling_target_ids", "_remaining_falling_targets"),
                _set("lasting_state.has_scene_falls", "len(_remaining_falling_targets) > 0"),
            ],
        )
    )
    changed["advance_srd_lasting_clock"] = clock
    return _replace_tools(text, changed)
