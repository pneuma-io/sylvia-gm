"""Earthquake damages genuine whole structures and retains earned collapse consequences.

This bounded owner accepts a whole Large-or-smaller Box object with an admitted
Breaking Objects profile. It does not infer a building's HP from its dimensions,
destroy supporting roof geometry, or turn a section of a Huge building into the
whole building. Rubble and burial persist independently of the spell's lifetime.
"""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _walk, _when
    from .srd_earthquake import FAMILY_GUARD, MAX_TARGETS, SOURCE_SHA, _source
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from .srd_spell_object_targets import _lookup_object, _validate_profile
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_control_behaviors import _walk, _when
    from srd_earthquake import FAMILY_GUARD, MAX_TARGETS, SOURCE_SHA, _source
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from srd_spell_object_targets import _lookup_object, _validate_profile
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

MAX_STRUCTURES = 2
PUBLIC_TOOLS = {"escape_srd_earthquake_rubble"}
EVENT_GUARD = "_quake_source.structures == true"


def _box(prefix, entity):
    return [
        _require(
            entity + ".footprint.kind == 'box'", "Collapse requires actual Box body geometry."
        ),
        _set(
            prefix + "_min",
            [
                entity + f".position[{i}] - " + entity + f".footprint.half_extents[{i}]"
                for i in range(2)
            ]
            + [entity + ".position[2]"],
        ),
        _set(
            prefix + "_max",
            [
                entity + f".position[{i}] + " + entity + f".footprint.half_extents[{i}]"
                for i in range(2)
            ]
            + [entity + ".position[2] + " + entity + ".height"],
        ),
    ]


def _qualify():
    """Require real flat-floor contact and a whole supported object, never a numeric verdict."""
    distance = " + ".join(
        f"(point[{i}] - min(max(point[{i}], _collapse_box_min[{i}]), _collapse_box_max[{i}])) ** 2"
        for i in range(2)
    )
    return _tool_scene(
        "Qualify an actual whole structure against the chosen ground circle before casting costs.",
        [_parameter("object_id"), _array("point"), _parameter("plane"), _parameter("ground_id")],
        [
            *_lookup_object(),
            _set("_collapse_structure_inside", False),
            _require(
                "_object_scene.metadata.structure == true", "Select an admitted actual structure."
            ),
            *_box("_collapse_box", "_object_scene"),
            _when(
                "_object_scene.metadata.plane == plane and " + distance + " <= 10000",
                [
                    _set("_collapse_floor", None),
                    _set("_collapse_geometry", "state.spatial_scene.geometry"),
                    _loop(
                        "_collapse_geometry",
                        "_collapse_piece",
                        [
                            _when(
                                "_collapse_piece.id == ground_id",
                                [_set("_collapse_floor", "_collapse_piece")],
                            ),
                            _require(
                                "_collapse_piece.metadata.structure_id != object_id and "
                                "_collapse_piece.metadata.object_id != object_id",
                                "Structure collapse does not yet retire separate roof or "
                                "structural solids.",
                            ),
                        ],
                    ),
                    _require(
                        "_collapse_floor != null and _collapse_floor.active == true and "
                        "_collapse_floor.volume.kind == 'box' and "
                        "_collapse_floor.metadata.ground == true",
                        "A whole structure needs the actual retained flat supporting floor.",
                    ),
                    _when(
                        "abs(_object_scene.position[2] - _collapse_floor.volume.max[2]) < "
                        "0.000000001",
                        [
                            *_validate_profile(),
                            _require(
                                "_object_scene.metadata.structure_section != true and "
                                "_object_scene.metadata.damage_threshold == null and "
                                "_object_scene.height <= 10 and "
                                "_object_scene.footprint.half_extents[0] <= 5 and "
                                "_object_scene.footprint.half_extents[1] <= 5",
                                "Collapse supports whole Large-or-smaller structures, without "
                                "damage thresholds.",
                            ),
                            *[
                                _require(
                                    f"_collapse_box_min[{i}] >= "
                                    f"_collapse_floor.volume.min[{i}] and "
                                    f"_collapse_box_max[{i}] <= _collapse_floor.volume.max[{i}]",
                                    "The whole admitted structure must contact its actual "
                                    "supporting floor.",
                                )
                                for i in range(2)
                            ],
                            _set("_collapse_structure_inside", True),
                        ],
                    ),
                ],
            ),
        ],
    )


def _nearby():
    """Use the distance between actual creature and structure volumes, not caller distances."""
    distance = " + ".join(
        f"max(0, _collapse_box_min[{i}] - _collapse_body_max[{i}], "
        f"_collapse_body_min[{i}] - _collapse_box_max[{i}]) ** 2"
        for i in range(3)
    )
    return _tool_scene(
        "Read actual creature spaces within half the collapsing structure's genuine height.",
        [_parameter("object_id")],
        [
            *_lookup_object(),
            *_box("_collapse_box", "_object_scene"),
            _set("_collapse_nearby", []),
            _set("_collapse_bodies", "state.spatial_scene.entities"),
            _loop(
                "_collapse_bodies",
                "_collapse_body",
                [
                    _when(
                        "_collapse_body.active == true and "
                        "_collapse_body.metadata.participant_ref != null and "
                        "_collapse_body.metadata.plane == _object_scene.metadata.plane",
                        [
                            *_box("_collapse_body", "_collapse_body"),
                            _when(
                                distance + " <= (_object_scene.height * 0.5) ** 2",
                                [
                                    _require(
                                        f"len(_collapse_nearby) < {MAX_TARGETS} and "
                                        "_collapse_body.metadata.participant_ref not in "
                                        "_collapse_nearby",
                                        "Collapse supports eight unambiguous actual creature "
                                        "spaces.",
                                    ),
                                    _set(
                                        "_collapse_candidate_id",
                                        "_collapse_body.metadata.participant_ref",
                                    ),
                                    _call(
                                        "validate_srd_earthquake_collapse_recipient",
                                        {"target_state": "{_collapse_candidate_id}"},
                                        ("_collapse_recipient_alive",),
                                    ),
                                    _when(
                                        "_collapse_recipient_alive == true",
                                        [
                                            _push(
                                                "_collapse_nearby",
                                                "_collapse_body.metadata.participant_ref",
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


def _recipient():
    return _tool_scene(
        "Check actual living recipients and their admitted Dexterity save statistics.",
        [_entity("target_state")],
        [
            _set("_collapse_recipient_alive", "target_state.is_alive == true"),
            _when(
                "_collapse_recipient_alive == true",
                [
                    _require(
                        "target_state.creature_kind == 'character' or "
                        "target_state.saving_throw_modifiers.dexterity != null",
                        "Collapse requires the actual creature Dexterity saving throw modifier.",
                    ),
                    _require(
                        "target_state.magic_jar_source_id == null and "
                        "target_state.form_statistics_replaced != true and "
                        "(target_state.current_body_source_id == null or "
                        "target_state.current_body_source_id == _target_state_id)",
                        "Burial of a transferred or replaced body requires its physical "
                        "receiving owner.",
                    ),
                ],
            ),
        ],
    )


def _collect():
    return _tool_scene(
        "Collect all actual supported whole structures in the chosen circle; unsupported ones "
        "reject.",
        [_entity("caster_state"), _array("point"), _parameter("plane"), _parameter("ground_id")],
        [
            _set("_collapse_structures", []),
            _set("_collapse_recipient_count", 0),
            _set("_collapse_objects", "state.spatial_scene.entities"),
            _loop(
                "_collapse_objects",
                "_collapse_object",
                [
                    _when(
                        "_collapse_object.active == true and "
                        "_collapse_object.metadata.structure == true",
                        [
                            _call(
                                "qualify_srd_earthquake_structure",
                                {
                                    "object_id": "{_collapse_object.id}",
                                    "point": "{point}",
                                    "plane": "{plane}",
                                    "ground_id": "{ground_id}",
                                },
                                ("_collapse_structure_inside",),
                            ),
                            _when(
                                "_collapse_structure_inside == true",
                                [
                                    _require(
                                        f"len(_collapse_structures) < {MAX_STRUCTURES}",
                                        "Earthquake supports two whole admitted structures per "
                                        "event.",
                                    ),
                                    _call(
                                        "collect_srd_earthquake_collapse_recipients",
                                        {
                                            "object_id": "{_collapse_object.id}",
                                        },
                                        ("_collapse_nearby",),
                                    ),
                                    _set(
                                        "_collapse_recipient_count",
                                        "_collapse_recipient_count + len(_collapse_nearby)",
                                    ),
                                    _require(
                                        f"_collapse_recipient_count <= {MAX_TARGETS}",
                                        "An event supports eight total collapse recipients "
                                        "across its structures.",
                                    ),
                                    _loop(
                                        "_collapse_nearby",
                                        "_collapse_harmed",
                                        [
                                            _call(
                                                "validate_srd_charmer_harm",
                                                {
                                                    "actor_state": "{_caster_state_id}",
                                                    "target_state": "{_collapse_harmed}",
                                                },
                                            ),
                                        ],
                                    ),
                                    _push("_collapse_structures", "_collapse_object.id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _fixed_receiver(suffix):
    """Keep the actual final object HP/destruction callbacks; fixed damage makes no attack roll."""
    return _tool_scene(
        "Apply the printed 50 Bludgeoning at an actual committed structure event.",
        [_parameter("object_id"), _parameter("area_id")],
        [
            *_source(),
            _require(
                "_quake_source.event_in_progress == true and _quake_source.structures == true",
                "Structure damage belongs to this actual opted-in Earthquake event.",
            ),
            *_lookup_object(),
            *_validate_profile(),
            _set("_caster_state_id", "_quake_source.caster"),
            _set("_object_hp_before", "_object_profile.hp"),
            _set("_object_damage_roll", None),
            _set("_object_damage_rolled", 50),
            _set("_object_damage_applied", "min(_object_hp_before, 50)"),
            _set("damage_type", "'bludgeoning'"),
            *deepcopy(suffix),
        ],
    )


def _collapse():
    """Collapse one actually destroyed object, then roll one shared simultaneous damage amount."""
    return _tool_scene(
        "Commit earned collapse/rubble and actual nearby recipient saves, without rebuilding "
        "on spell end.",
        [_parameter("object_id"), _parameter("area_id")],
        [
            *_source(),
            _require(
                "_quake_source.event_in_progress == true", "An actual collapse event is required."
            ),
            *_lookup_object(),
            _require(
                "_object_scene.metadata.srd_spell_object.hp == 0 and "
                "_object_scene.metadata.srd_spell_object.destroyed == true",
                "Only actual zero structure HP can produce collapse.",
            ),
            *_box("_collapse_box", "_object_scene"),
            _call(
                "collect_srd_earthquake_collapse_recipients",
                {"object_id": "{object_id}"},
                ("_collapse_nearby",),
            ),
            _set("_collapse_structure", "_object_scene"),
            _set("_collapse_event_rows", []),
            _set(
                "_collapse_metadata_changes",
                {
                    "earthquake_collapsed": True,
                    "collapse_source_id": "area_id",
                    "collapse_cast_id": "_quake_source.cast_id",
                    "collapse_height": "_object_scene.height",
                },
            ),
            _set("_collapse_metadata", "merge(_object_scene.metadata, _collapse_metadata_changes)"),
            *_snapshot(),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{_info_snapshot.geometry_revision}",
                    "target": {"kind": "entity", "id": "{object_id}"},
                    "patch": {"active": False, "metadata": "{_collapse_metadata}"},
                },
                "store_as": "_collapse_mutation",
            },
            _call(
                "format_srd_information_projection",
                {"message": "{object_id}:rubble"},
                ("_info_projection_message",),
            ),
            _set("_collapse_rubble_id", "_info_projection_message"),
            _set("_collapse_zone_ids", []),
            _set("_collapse_zones", "state.spatial_scene.zones"),
            _loop(
                "_collapse_zones",
                "_collapse_zone",
                [_push("_collapse_zone_ids", "_collapse_zone.id")],
            ),
            _require(
                "_collapse_rubble_id not in _collapse_zone_ids",
                "This structure already has retained rubble.",
            ),
            _push(
                "_collapse_zones",
                {
                    "id": "_collapse_rubble_id",
                    "map_id": "state.spatial_scene.map.id",
                    "shape": {
                        "kind": "'box'",
                        "min": "_collapse_box_min",
                        "max": "_collapse_box_max",
                    },
                    "metadata": {
                        "plane": "_collapse_structure.metadata.plane",
                        "difficult": True,
                        "ground_environment": True,
                        "collapsed_structure": "object_id",
                    },
                },
            ),
            _set("state.spatial_scene.zones", "_collapse_zones"),
            {"increment": {"field": "state.spatial_scene.map.revision", "amount": 1}},
            {"dice": {"expression": "12d6"}, "store_as": "_collapse_damage_roll"},
            _loop(
                "_collapse_nearby",
                "_collapse_actor",
                [
                    _call(
                        "resolve_srd_earthquake_collapse_save",
                        {
                            "target_state": "{_collapse_actor}",
                            "area_id": "{area_id}",
                            "object_id": "{object_id}",
                            "damage_total": "{_collapse_damage_roll.total}",
                        },
                        ("_collapse_save_row",),
                    ),
                    _push("_collapse_event_rows", "_collapse_save_row"),
                ],
            ),
        ],
    )


def _save():
    params = {
        "actor_state": "{_target_state_id}",
        "ability": "dexterity",
        "kind": "saving_throw",
        "proficiency_factor": "none",
        "target_number": "{_quake_source.save_dc}",
        "save_avoids_condition": "none",
        "circumstantial_modifier": 0,
    }
    exports = ("_outcome", "_d20_test", "_adjusted_total")
    return _tool_scene(
        "Roll the actual collapse save, apply earned damage and record only failed-save burial.",
        [
            _entity("target_state"),
            _parameter("area_id"),
            _parameter("object_id"),
            _parameter("damage_total", "integer", minimum=12, maximum=72),
        ],
        [
            *_source(),
            _require(
                "_quake_source.event_in_progress == true", "The collapse producer owns this save."
            ),
            _call(
                "validate_srd_earthquake_collapse_recipient", {"target_state": "{_target_state_id}"}
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_collapse_receiving_body", "_info_scene_entity"),
            _when(
                "target_state.creature_kind == 'character'",
                [_call("d20_test", params | {"actor": "{_target_state_id}"}, exports)],
                [
                    _call(
                        "d20_test",
                        params
                        | {
                            "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                            "proficiency_bonus": 0,
                        },
                        exports,
                    )
                ],
            ),
            _set("_collapse_outcome", "_outcome"),
            _set("_collapse_roll", "_d20_test.total"),
            _set("_collapse_total", "_adjusted_total"),
            _call(
                "resolve_srd_save_damage",
                {
                    "target_state": "{_target_state_id}",
                    "damage_total": "{damage_total}",
                    "save_ability": "dexterity",
                    "save_outcome": "{_collapse_outcome}",
                    "success_damage": "half",
                },
                ("_save_damage_amount", "_evasion_available"),
            ),
            _set("_collapse_divisor", 1),
            _set("_collapse_remainder", 0),
            _set("_collapse_applied", 0),
            _when(
                "_collapse_outcome == 'success' or _evasion_available == true",
                [
                    _set("_collapse_divisor", 2),
                    _set("_collapse_remainder", "damage_total % 2"),
                ],
            ),
            _when(
                "not (_collapse_outcome == 'success' and _evasion_available == true)",
                [
                    _call(
                        "apply_srd_qualifying_damage",
                        {
                            "target": "{_target_state_id}",
                            "amount": "{_save_damage_amount}",
                            "adjustment": 0,
                            "damage_type": "bludgeoning",
                            "source_state_id": "{_quake_source.caster}",
                            "game_damage_roll": True,
                            "damage_roll_divisor": "{_collapse_divisor}",
                            "damage_roll_base_remainder": "{_collapse_remainder}",
                        },
                        ("_effective_damage", "_is_alive_after"),
                    ),
                    _set("_collapse_applied", "_effective_damage"),
                ],
            ),
            _when(
                "_collapse_outcome == 'failure'",
                [
                    _call(
                        "check_srd_target_condition_immunity",
                        {
                            "target_state": "{_target_state_id}",
                            "condition": "prone",
                            "source_state_id": "{_quake_source.caster}",
                        },
                        ("_source_condition_immune",),
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
                            )
                        ],
                    ),
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "{area_id}:{object_id}:{_collapse_receiving_body.id}:buried",
                        },
                        ("_info_projection_message",),
                    ),
                    _set("_collapse_burial_id", "_info_projection_message"),
                    _set("_collapse_effects", "state.turn_effects"),
                    _push(
                        "_collapse_effects",
                        {
                            "kind": "'earthquake_burial'",
                            "source_id": "_collapse_burial_id",
                            "spell": "'earthquake'",
                            "source_sha256": repr(SOURCE_SHA),
                            "caster": "_quake_source.caster",
                            "target": "_target_state_id",
                            "owner": "_target_state_id",
                            "phase": "'never'",
                            "turn": 0,
                            "area_id": "area_id",
                            "object_id": "object_id",
                            "body_id": "_collapse_receiving_body.id",
                            "position": "_collapse_receiving_body.position",
                            "plane": "_collapse_receiving_body.metadata.plane",
                            "active": True,
                            "ongoing_duration": False,
                            "dispel_ongoing": False,
                        },
                    ),
                    _set("state.turn_effects", "_collapse_effects"),
                    _set("target_state.earthquake_buried", True),
                ],
            ),
            _set(
                "_collapse_save_row",
                {
                    "target": "_target_state_id",
                    "structure": "object_id",
                    "roll": "_collapse_roll",
                    "total": "_collapse_total",
                    "dc": "_quake_source.save_dc",
                    "outcome": "_collapse_outcome",
                    "damage_roll": "damage_total",
                    "damage_taken": "_collapse_applied",
                    "buried": "_collapse_outcome == 'failure'",
                },
            ),
        ],
    )


def _event():
    return _tool_scene(
        "Resolve fixed structure damage during the existing paid/turn event, exactly once per "
        "producer.",
        [_parameter("area_id")],
        [
            *_source(),
            _require(
                "_quake_source.event_in_progress == true", "Use the actual tremor event receipt."
            ),
            _call(
                "collect_srd_earthquake_structures",
                {
                    "caster_state": "{_quake_source.caster}",
                    "point": "{_quake_source.origin}",
                    "plane": "{_quake_source.plane}",
                    "ground_id": "{_quake_source.ground_id}",
                },
                ("_collapse_structures",),
            ),
            _set("_collapse_event_rows", []),
            _set("_collapse_damage_rows", []),
            _loop(
                "_collapse_structures",
                "_collapse_structure_id",
                [
                    _call(
                        "damage_srd_earthquake_structure",
                        {
                            "object_id": "{_collapse_structure_id}",
                            "area_id": "{area_id}",
                        },
                        ("_object_destroyed", "_object_damage_applied", "_object_hp_after"),
                    ),
                    _push(
                        "_collapse_damage_rows",
                        {
                            "structure": "_collapse_structure_id",
                            "damage": "_object_damage_applied",
                            "hp_after": "_object_hp_after",
                            "collapsed": "_object_destroyed",
                        },
                    ),
                    _when(
                        "_object_destroyed == true",
                        [
                            _call(
                                "collapse_srd_earthquake_structure",
                                {
                                    "object_id": "{_collapse_structure_id}",
                                    "area_id": "{area_id}",
                                },
                            )
                            | {
                                "export_context": {
                                    "_collapse_structure_rows": "_collapse_event_rows",
                                }
                            }
                        ]
                        + [
                            _loop(
                                "_collapse_structure_rows",
                                "_collapse_structure_row",
                                [
                                    _push("_collapse_event_rows", "_collapse_structure_row"),
                                ],
                            )
                        ],
                    ),
                ],
            ),
            _set(
                "state.persistent_spell_areas.{area_id}.last_collapse_rows", "_collapse_event_rows"
            ),
            _set(
                "state.persistent_spell_areas.{area_id}.last_structure_damage_rows",
                "_collapse_damage_rows",
            ),
        ],
    )


def _burial():
    return _tool_scene(
        "Resolve only retained earned physical burial at this actor's actual current body.",
        [_entity("target_state")],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_collapse_burials", []),
            _set("_collapse_effects", "state.turn_effects"),
            _loop(
                "_collapse_effects",
                "_collapse_effect",
                [
                    _when(
                        "_collapse_effect.kind == 'earthquake_burial' and "
                        "_collapse_effect.active == true "
                        f"and _collapse_effect.source_sha256 == '{SOURCE_SHA}' and "
                        "_collapse_effect.target == _target_state_id and "
                        "_collapse_effect.body_id == _info_scene_entity.id and "
                        "_collapse_effect.plane == _info_scene_entity.metadata.plane",
                        [
                            _when(
                                "_collapse_effect.position[0] == "
                                "_info_scene_entity.position[0] and "
                                "_collapse_effect.position[1] == "
                                "_info_scene_entity.position[1] and "
                                "_collapse_effect.position[2] == _info_scene_entity.position[2]",
                                [_push("_collapse_burials", "_collapse_effect.source_id")],
                            )
                        ],
                    ),
                ],
            ),
            _set("target_state.earthquake_buried", "len(_collapse_burials) > 0"),
        ],
    )


def _escape():
    check = {
        "actor_state": "{_actor_state_id}",
        "ability": "strength",
        "skill": "athletics",
        "kind": "ability_check",
        "circumstantial_modifier": 0,
        "target_number": 20,
    }
    exports = ("_d20_test", "_outcome", "_adjusted_total")
    return _public(
        _tool_scene(
            "Spend this actual creature's Action and DC20 Strength (Athletics) check to escape "
            "earned rubble.",
            [
                _entity("actor_state"),
                _parameter("burial_id"),
                _optional("actor", resolve="participant"),
            ],
            [
                _require(
                    "combat.encounter_active == true and combat.turn_active == true and "
                    "combat.active_actor == _actor_state_id",
                    "Use the buried creature's actual turn Action.",
                ),
                _call(
                    "resolve_srd_earthquake_burial",
                    {"target_state": "{_actor_state_id}"},
                    ("_collapse_burials",),
                ),
                _require(
                    "burial_id in _collapse_burials",
                    "Select this actual body's retained Earthquake burial.",
                ),
                _set("_collapse_proficiency", "'none'"),
                _when(
                    "actor != null",
                    [
                        _require(
                            "_actor_id == _actor_state_id",
                            "Use the acting character's own skill statistics.",
                        ),
                        _when(
                            "actor.data.skill_proficiencies != null and 'athletics' in "
                            "actor.data.skill_proficiencies",
                            [_set("_collapse_proficiency", "'normal'")],
                        ),
                        _when(
                            "actor.data.skill_expertise != null and 'athletics' in "
                            "actor.data.skill_expertise",
                            [_set("_collapse_proficiency", "'double'")],
                        ),
                    ],
                    [
                        _require(
                            "actor_state.skill_check_modifiers.athletics != null",
                            "Record the source creature's actual Athletics modifier.",
                        )
                    ],
                ),
                _call("spend_srd_action", {"magic_action": False}),
                _set("combat.action_taken", "'escape_earthquake_rubble'"),
                _when(
                    "actor != null",
                    [
                        _call(
                            "d20_test",
                            check
                            | {
                                "actor": "{_actor_id}",
                                "proficiency_factor": "{_collapse_proficiency}",
                            },
                            exports,
                        )
                    ],
                    [
                        _call(
                            "d20_test",
                            check
                            | {
                                "ability_modifier": "{actor_state.skill_check_modifiers.athletics}",
                                "proficiency_bonus": 0,
                                "proficiency_factor": "none",
                            },
                            exports,
                        )
                    ],
                ),
                _when(
                    "_outcome == 'success'",
                    [
                        _set("_collapse_effects", "state.turn_effects"),
                        _set("_collapse_retained_effects", []),
                        _loop(
                            "_collapse_effects",
                            "_collapse_effect",
                            [
                                _when(
                                    "_collapse_effect.source_id != burial_id",
                                    [_push("_collapse_retained_effects", "_collapse_effect")],
                                ),
                            ],
                        ),
                        _set("state.turn_effects", "_collapse_retained_effects"),
                        _call(
                            "resolve_srd_earthquake_burial",
                            {
                                "target_state": "{_actor_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "spell",
        "Escape Earthquake Rubble",
        [("Outcome", "_outcome"), ("Total", "_adjusted_total")],
    )


def extend_runtime(text):
    """Compose structure admission and events before reaction continuations capture them."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    blocks = [
        node
        for node in _walk(cast["effects"])
        if node.get("condition") == FAMILY_GUARD + " and _spell_cast_failed != true"
    ]
    if len(blocks) != 1:
        raise ValueError("Collapse requires the unique actual pre-material Earthquake admission")
    guard = blocks[0]["on_true"][0].get("require", {}).get("condition")
    if guard not in {
        "earthquake_fissures != true and earthquake_structures != true",
        "earthquake_fissures != true",
    }:
        raise ValueError("Earthquake's selected optional-effects admission changed")
    blocks[0]["on_true"][0] = _require(
        "earthquake_fissures != true", "Earthquake fissure geometry is not yet supported."
    )
    blocks[0]["on_true"] = [
        e for e in blocks[0]["on_true"] if e.get("condition") != "earthquake_structures == true"
    ]
    geometry = [
        node
        for node in _walk(blocks[0]["on_true"])
        if node.get("call") == "validate_srd_earthquake_geometry"
    ]
    if len(geometry) != 1:
        raise ValueError("Structure admission requires the actual chosen-ground validation")
    geometry[0].setdefault("export_context", {}).update(
        _quake_plane="_quake_plane", _quake_ground_id="_quake_ground_id"
    )
    blocks[0]["on_true"].append(
        _when(
            "earthquake_structures == true",
            [
                _call(
                    "collect_srd_earthquake_structures",
                    {
                        "caster_state": "{_caster_state_id}",
                        "point": "{earthquake_point}",
                        "plane": "{_quake_plane}",
                        "ground_id": "{_quake_ground_id}",
                    },
                ),
            ],
        )
    )
    calls = [node for node in _walk(cast["effects"]) if node.get("call") == "cast_srd_earthquake"]
    if len(calls) != 1:
        raise ValueError("Collapse requires the actual unique paid installer")
    calls[0]["params"]["structures"] = "{earthquake_structures}"
    install = deepcopy(tools["cast_srd_earthquake"])
    if not any(p["name"] == "structures" for p in install["parameters"]):
        install["parameters"].append(_optional("structures", "boolean", False))
    records = [
        node["set"]["value"]
        for node in _walk(install["effects"])
        if node.get("set", {}).get("field") == "lasting_state.persistent_spell_areas.{_quake_id}"
    ]
    if len(records) != 1:
        raise ValueError("Collapse requires the actual retained source installation")
    records[0]["structures"] = "structures == true"
    summary_guard = "structures == true"
    install["effects"] = [e for e in install["effects"] if e.get("condition") != summary_guard]
    install["effects"].append(
        _when(
            summary_guard,
            [
                _set(
                    "_collapse_damage_rows",
                    "state.persistent_spell_areas[_quake_id].last_structure_damage_rows",
                ),
                _set(
                    "_collapse_rows", "state.persistent_spell_areas[_quake_id].last_collapse_rows"
                ),
                _loop(
                    "_collapse_damage_rows",
                    "_collapse_row",
                    [
                        _call(
                            "format_srd_information_projection",
                            {
                                "message": "Printed 50 Bludgeoning; actual object HP damage "
                                "{_collapse_row.damage}; "
                                "HP {_collapse_row.hp_after}; collapsed {_collapse_row.collapsed}.",
                            },
                            ("_info_projection_message",),
                        ),
                        _push(
                            "_spell_detail_rows",
                            {
                                "label": "_collapse_row.structure",
                                "value": "_info_projection_message",
                            },
                        ),
                    ],
                ),
                _loop(
                    "_collapse_rows",
                    "_collapse_row",
                    [
                        _call(
                            "format_srd_information_projection",
                            {
                                "message": "Collapse DEX {_collapse_row.roll} / "
                                "{_collapse_row.total} "
                                "vs DC {_collapse_row.dc}: {_collapse_row.outcome}. "
                                "12d6 {_collapse_row.damage_roll}; damage "
                                "{_collapse_row.damage_taken}; "
                                "buried {_collapse_row.buried}.",
                            },
                            ("_info_projection_message",),
                        ),
                        _push(
                            "_spell_detail_rows",
                            {"label": "_collapse_row.target", "value": "_info_projection_message"},
                        ),
                    ],
                ),
            ],
        )
    )
    event = deepcopy(tools["resolve_srd_earthquake_event"])
    receipts = [
        node["on_true"]
        for node in _walk(event["effects"])
        if node.get("condition") == "event_turn != _quake_source.last_end_turn"
    ]
    if len(receipts) != 1:
        raise ValueError("Collapse requires the actual unique caster-event receipt")
    effects = receipts[0]
    effects[:] = [e for e in effects if e.get("condition") != EVENT_GUARD]
    end = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field")
        == "lasting_state.persistent_spell_areas.{area_id}.event_in_progress"
        and e["set"]["value"] is False
    )
    effects.insert(
        end,
        _when(
            EVENT_GUARD,
            [
                _call("resolve_srd_earthquake_structures", {"area_id": "{area_id}"}),
            ],
        ),
    )
    changed = {
        "cast_spell": cast,
        "cast_srd_earthquake": install,
        "resolve_srd_earthquake_event": event,
        "qualify_srd_earthquake_structure": _qualify(),
        "collect_srd_earthquake_structures": _collect(),
        "collect_srd_earthquake_collapse_recipients": _nearby(),
        "validate_srd_earthquake_collapse_recipient": _recipient(),
        "collapse_srd_earthquake_structure": _collapse(),
        "resolve_srd_earthquake_collapse_save": _save(),
        "resolve_srd_earthquake_structures": _event(),
        "resolve_srd_earthquake_burial": _burial(),
        "escape_srd_earthquake_rubble": _escape(),
    }
    return _replace_tools(text, changed)


def bind_final_receivers(text):
    """Follow final object callbacks without rewriting ordinary attack or damage owners."""
    tools = _load_runtime(text)["tools"]
    attack = tools["resolve_srd_spell_object_attack"]
    markers = [
        i
        for i, e in enumerate(attack["effects"])
        if e.get("set")
        == {"field": "_object_hp_after", "value": "_object_hp_before - _object_damage_applied"}
    ]
    if len(markers) != 1:
        raise ValueError("Earthquake requires the actual unique final object HP receiving suffix")
    suffix = attack["effects"][markers[0] :]
    if not any("spatial_mutation" in str(e) for e in suffix):
        raise ValueError(
            "Earthquake's object receiver must persist genuine HP before destruction callbacks"
        )
    changed = {"damage_srd_earthquake_structure": _fixed_receiver(suffix)}
    for name in ("spend_combat_movement", "derive_srd_scene_movement_budget"):
        tool = deepcopy(tools[name])
        guard = "actor_state.earthquake_buried == true"
        tool["effects"] = [e for e in tool["effects"] if e.get("condition") != guard]
        tool["effects"].insert(
            0,
            _when(
                guard,
                [
                    _call("resolve_srd_earthquake_burial", {"target_state": "{_actor_state_id}"}),
                    _require(
                        "actor_state.earthquake_buried != true",
                        "Escape the actual rubble before moving this body.",
                    ),
                ],
            ),
        )
        changed[name] = tool
    return _replace_tools(text, changed)
