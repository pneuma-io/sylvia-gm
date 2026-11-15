"""Telekinesis keeps one actual target, saved caster-turn grips and real scene movement."""

from copy import deepcopy

if __package__:
    from .srd_feather_fall import _ground, _sweep
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _admission_guard, _metadata_patch
    from .srd_spell_travel_levitate import _body
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_feather_fall import _ground, _sweep
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _admission_guard, _metadata_patch
    from srd_spell_travel_levitate import _body
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools


PUBLIC_TOOLS = {
    "record_srd_telekinesis_object_size",
    "use_srd_telekinesis",
    "operate_srd_telekinesis_object",
    "pour_srd_telekinesis_vial",
}


def _source():
    return [
        _set("_tk_source", None),
        _when(
            "state.telekinesis_sources != null",
            [
                _set("_tk_sources", "state.telekinesis_sources"),
                _loop(
                    "_tk_sources",
                    "_tk_candidate",
                    [
                        _when(
                            "_tk_candidate.id == source_id",
                            [
                                _require(
                                    "_tk_source == null",
                                    "Telekinesis source identity is ambiguous.",
                                ),
                                _set("_tk_source", "_tk_candidate"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _require("_tk_source != null", "Select an actual saved Telekinesis source."),
    ]


def _save_source():
    return [
        _set("_tk_retained", []),
        _set("_tk_sources", "state.telekinesis_sources"),
        _loop(
            "_tk_sources",
            "_tk_candidate",
            [
                {
                    "condition": "_tk_candidate.id == _tk_source.id",
                    "on_true": [_push("_tk_retained", "_tk_source")],
                    "on_false": [_push("_tk_retained", "_tk_candidate")],
                }
            ],
        ),
        _set("state.telekinesis_sources", "_tk_retained"),
    ]


def _patch_source(changes):
    # Bare nullable expressions become literal strings in Set; admit null explicitly first.
    nullable = {
        key: value
        for key, value in changes.items()
        if key in {"creature_id", "restraint_id", "source_caster_id"} and isinstance(value, str)
    }
    initial = {key: None if key in nullable else value for key, value in changes.items()}
    return [
        _set("_tk_patch", initial),
        *[
            _when(
                f"{value} != null",
                [
                    _set("_tk_nullable_patch", {key: value}),
                    _set("_tk_patch", "merge(_tk_patch, _tk_nullable_patch)"),
                ],
            )
            for key, value in nullable.items()
        ],
        _set("_tk_source", "merge(_tk_source, _tk_patch)"),
        *_save_source(),
    ]


def _patch_body(changes):
    return [
        *_snapshot(),
        _set("_tk_body_patch", changes),
        _set("_tk_metadata", "merge(_info_scene_entity.metadata, _tk_body_patch)"),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                "patch": {"metadata": "{_tk_metadata}"},
            },
            "store_as": "_tk_marker_mutation",
        },
    ]


def _save_roll():
    return [
        _require(
            "target_state.saving_throw_modifiers.strength != null",
            "Telekinesis needs the actual creature's Strength saving-throw modifier.",
        ),
        _call(
            "d20_test",
            {
                "actor_state": "{_target_state_id}",
                "ability": "strength",
                "kind": "saving_throw",
                "ability_modifier": "{target_state.saving_throw_modifiers.strength}",
                "proficiency_bonus": 0,
                "proficiency_factor": "none",
                "circumstantial_modifier": 0,
                "target_number": "{save_dc}",
                "advantage": False,
                "disadvantage": False,
                "forego_save": False,
            },
            ["_d20_test", "_outcome"],
        ),
        _set("_tk_save", "_d20_test"),
        _set("_tk_save_outcome", "_outcome"),
        _set("target_state.last_telekinesis_save", "_d20_test"),
        _set("target_state.last_telekinesis_save_outcome", "_outcome"),
    ]


def _destination_support():
    """Query real support below the proposed endpoint without altering the admitted body."""

    def endpoint(value):
        if isinstance(value, str):
            return value.replace("_info_position", "destination")
        if isinstance(value, list):
            return [endpoint(v) for v in value]
        if isinstance(value, dict):
            return {k: endpoint(v) for k, v in value.items()}
        return value

    return [*endpoint(_ground()), _set("_tk_destination_ground", "_fall_ground_height")]


def _action_clock():
    return [
        _set("_tk_action_mode", "'exploration'"),
        _set("_tk_action_turn", "state.lasting_tick // 2"),
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _caster_state_id "
                    "and caster_state.turns_started != null",
                    "Telekinesis belongs to the actual caster's active turn.",
                ),
                _set("_tk_action_mode", "'combat'"),
                _set("_tk_action_turn", "caster_state.turns_started"),
            ],
        ),
    ]


def telekinesis_tools(tools):
    """Keep source qualification, saves and movement independent of caller outcome verdicts."""
    if __package__:
        from .srd_spell_travel import ACTIVE, _measure, _placement, _scene_subject
    else:
        from srd_spell_travel import ACTIVE, _measure, _placement, _scene_subject

    size = _public(
        _tool_scene(
            "Record the actual admitted object's size category once.",
            [
                _parameter("object_id"),
                _parameter(
                    "size",
                    "enum",
                    enum_values=["tiny", "small", "medium", "large", "huge", "gargantuan"],
                ),
            ],
            [
                *_admission_guard(),
                _require(
                    "_object_scene.metadata.is_object == true", "Select an actual admitted object."
                ),
                _require(
                    "_object_scene.metadata.size_rank == null",
                    "This object's actual size category is already admitted.",
                ),
                _set("_tk_size_rank", 0),
                *[
                    _when(f"size == {name!r}", [_set("_tk_size_rank", rank)])
                    for rank, name in enumerate(
                        ("tiny", "small", "medium", "large", "huge", "gargantuan")
                    )
                ],
                *_metadata_patch({"size_rank": "_tk_size_rank"}),
            ],
        ),
        "exploration",
        "Object size",
        [("Size rank", "_tk_size_rank")],
    )
    creature = _tool_scene(
        "Qualify the actual visible Huge-or-smaller creature and its saved Strength modifier.",
        [_entity("caster_state"), _entity("target_state")],
        [
            _when(
                "target_state.has_transformations == true",
                [
                    _call(
                        "project_srd_transformation",
                        {"actor_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                ],
            ),
            _require(
                "target_state.size_rank != null and target_state.size_rank >= 0 and "
                "target_state.size_rank <= 4",
                "Telekinesis affects an actual Huge-or-smaller creature.",
            ),
            _require(
                "target_state.saving_throw_modifiers.strength != null",
                "Record the actual creature's Strength saving-throw modifier.",
            ),
            _call(
                "resolve_srd_scene_relation_node",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                },
                ["_scene_same_plane", "_scene_can_see", "_scene_distance"],
            ),
            _require(
                "_scene_same_plane == true and _scene_can_see == true and _scene_distance <= 60",
                "Telekinesis needs an actual visible target within sixty feet.",
            ),
        ],
    )
    holder = _tool_scene(
        "Qualify the actual object holder and saved Strength modifier before payment.",
        [_entity("target_state"), _parameter("object_id")],
        [
            _require(
                "target_state.saving_throw_modifiers.strength != null",
                "Record the actual holder's Strength saving-throw modifier.",
            ),
            *_body("object_id"),
            _set("_tk_inventory_item", None),
            _when(
                "_info_scene_entity.metadata.srd_object_utility != null",
                [
                    _set(
                        "_tk_inventory_item",
                        "_info_scene_entity.metadata.srd_object_utility.inventory_item",
                    ),
                ],
            ),
            *[
                _require(
                    f"target_state.{slot} == null or _tk_inventory_item == null or "
                    f"target_state.{slot}.inventory_item != _tk_inventory_item",
                    "This worn armor or shield needs its actual equipment-removal owner.",
                )
                for slot in ("worn_armor", "worn_shield")
            ],
        ],
    )
    validate = _tool_scene(
        "Validate the actual visible target, thirty-foot route and clear destination.",
        [
            _entity("caster_state"),
            _parameter("subject"),
            _array("destination"),
            _optional("targets", "array", None, items={"type": "string"}),
        ],
        [
            _require(
                "targets == null or len(targets) == 0",
                "Select one actual Telekinesis scene subject.",
            ),
            *_scene_subject("{_caster_state_id}", "_tk_caster"),
            *_body("subject"),
            _set("_tk_body", "_info_scene_entity"),
            _set("_tk_origin", "_info_position"),
            _require(
                "_tk_body.metadata.plane != null and "
                "_tk_body.metadata.plane == _tk_caster.metadata.plane",
                "Telekinesis needs the target on the caster's actual plane.",
            ),
            _set("_tk_creature", None),
            _set("_tk_holder", None),
            _require(
                "_tk_body.metadata.telekinesis_source_id == null or "
                "_tk_body.metadata.telekinesis_source_id == caster_state.telekinesis_source_id",
                "Reconcile independent Telekinesis grips before selecting this target.",
            ),
            {
                "condition": "_tk_body.metadata.is_object == true",
                "on_true": [
                    _call(
                        "resolve_srd_object_content_geometry",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{_tk_body.id}",
                            "range_feet": 60,
                            "requires_sight": True,
                        },
                        ["_object_scene"],
                    ),
                    _set("_tk_body", "_object_scene"),
                    _set("_tk_origin", "_object_scene.position"),
                    _require(
                        "_tk_body.metadata.size_rank != null and "
                        "_tk_body.metadata.size_rank >= 0 and _tk_body.metadata.size_rank <= 4",
                        "Telekinesis affects an actual Huge-or-smaller object.",
                    ),
                    _when(
                        "_tk_body.metadata.carried_by != null and "
                        "_tk_body.metadata.carried_by != ''",
                        [
                            _set("_tk_holder", "_tk_body.metadata.carried_by"),
                        ],
                    ),
                    _when(
                        "_tk_body.metadata.worn_by != null and _tk_body.metadata.worn_by != ''",
                        [
                            _require(
                                "_tk_holder == null or _tk_holder == _tk_body.metadata.worn_by",
                                "Reconcile this object's actual holder before Telekinesis.",
                            ),
                            _set("_tk_holder", "_tk_body.metadata.worn_by"),
                        ],
                    ),
                    _when(
                        "_tk_holder != null",
                        [
                            _call(
                                "qualify_srd_telekinesis_holder",
                                {
                                    "target_state": "{_tk_holder}",
                                    "object_id": "{_tk_body.id}",
                                },
                            ),
                            _require(
                                "_tk_body.metadata.inventory_owner_ref == null and "
                                "_tk_body.metadata.inventory_definition_ref == null",
                                "Linked inventory objects need their actual relocation owner.",
                            ),
                        ],
                    ),
                ],
                "on_false": [
                    _require(
                        "_tk_body.metadata.creature_type != 'spell_force'",
                        "Telekinesis affects an actual creature or object.",
                    ),
                    _set("_tk_creature", "_tk_body.id"),
                    _when(
                        "_tk_body.metadata.participant_ref != null",
                        [
                            _set("_tk_creature", "_tk_body.metadata.participant_ref"),
                        ],
                    ),
                    _call(
                        "qualify_srd_telekinesis_creature",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_tk_creature}",
                        },
                    ),
                ],
            },
            *_measure("{_tk_origin}", "{destination}", "_tk_distance"),
            _require(
                "_tk_distance <= 30", "Telekinesis moves the actual target up to thirty feet."
            ),
            *_measure("{_tk_caster.position}", "{destination}", "_tk_end_distance"),
            _require(
                "_tk_end_distance <= 60", "Keep the actual Telekinesis destination within range."
            ),
            *_body("_tk_body.id"),
            *_sweep("destination"),
            _set("_travel_excluded", []),
            *_placement("{_tk_body.id}", "{destination}", "{_tk_body.metadata.plane}"),
            _require(
                "_travel_placement.distance == 0",
                "Telekinesis needs an actual clear unoccupied destination.",
            ),
            _when("_tk_creature != null", [*_destination_support()]),
        ],
    )
    start = _tool_scene(
        "Validate optional initial exertion without inventing a target for the paid ability.",
        [
            _entity("caster_state"),
            _optional("subject", default=""),
            _array("destination"),
            _optional("targets", "array", None, items={"type": "string"}),
            _optional("operation", "enum", "move", enum_values=["move", "open", "close", "pour"]),
        ],
        [
            _require(
                "targets == null or len(targets) == 0",
                "Select one actual Telekinesis scene subject or omit initial exertion.",
            ),
            _require(
                "operation == 'move' or subject != ''",
                "Initial fine control needs its actual object.",
            ),
            {
                "condition": "subject == ''",
                "on_true": [
                    _require(
                        "destination[0] == 0 and destination[1] == 0 and destination[2] == 0",
                        "Omit the destination when casting Telekinesis without an initial subject.",
                    ),
                    *_scene_subject("{_caster_state_id}", "_tk_caster"),
                    _require(
                        "_tk_caster.metadata.plane != null",
                        "Record the caster's actual plane for the Telekinesis ability.",
                    ),
                ],
                "on_false": [
                    {
                        "condition": "operation == 'move'",
                        "on_true": [
                            _call(
                                "validate_srd_telekinesis_cast",
                                {
                                    "caster_state": "{_caster_state_id}", "subject": "{subject}",
                                    "destination": "{destination}", "targets": "{targets}",
                                },
                                ["_tk_caster"],
                            ),
                        ],
                        "on_false": [
                            _require(
                                "destination[0] == 0 and destination[1] == 0 and "
                                "destination[2] == 0",
                                "Initial open or close has no motion destination.",
                            ),
                            _call(
                                "qualify_srd_telekinesis_object_access",
                                {"caster_state": "{_caster_state_id}", "object_id": "{subject}"},
                                ["_tk_fine_caster"],
                            ),
                            _set("_tk_caster", "_tk_fine_caster"),
                            _when(
                                "operation == 'pour'",
                                [_call("qualify_srd_plain_water_vial", {
                                    "caster_state": "{_caster_state_id}",
                                    "object_id": "{subject}",
                                })],
                            ),
                        ],
                    },
                ],
            },
        ],
    )
    restraint = _tool_scene(
        "Install the failed creature's exact Restrained source independently of movement.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            _parameter("concentration_sequence", "integer"),
        ],
        [
            _set("_tk_restraint", None),
            _set("_tk_immunities", []),
            _when(
                "target_state.condition_immunities != null",
                [
                    _set("_tk_immunities", "target_state.condition_immunities"),
                ],
            ),
            _when(
                "'restrained' not in _tk_immunities",
                [
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "state",
                            "spell": "telekinesis",
                            "condition": "restrained",
                            "save_dc": "{save_dc}",
                            "duration_rounds": 0,
                            "repeat_save_timing": "none",
                            "save_ability": "strength",
                            "concentration_sequence": "{concentration_sequence}",
                        },
                        ["_timed_condition_source_id"],
                    ),
                    _set("_tk_restraint", "_timed_condition_source_id"),
                ],
            ),
        ],
    )
    release = _tool_scene(
        "Retire the exact previous grip and queue its genuine suspended-body release.",
        [_parameter("source_id")],
        [
            *_source(),
            _when(
                "_tk_source.subject != null",
                [
                    _when(
                        "_tk_source.restraint_id != null",
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "source_id": "{_tk_source.restraint_id}",
                                    "lasting_state": "state",
                                },
                            ),
                        ],
                    ),
                    *_body("_tk_source.subject"),
                    _when(
                        "_info_scene_entity.metadata.telekinesis_source_id == _tk_source.id",
                        [
                            *_patch_body({"telekinesis_source_id": None}),
                        ],
                    ),
                    _when(
                        "_tk_source.creature_id != null",
                        [
                            _when(
                                "state.telekinesis_releases == null",
                                [_set("state.telekinesis_releases", [])],
                            ),
                            _when(
                                "state.telekinesis_release_sequence == null",
                                [_set("state.telekinesis_release_sequence", 0)],
                            ),
                            _set(
                                "state.telekinesis_release_sequence",
                                "state.telekinesis_release_sequence + 1",
                            ),
                            _set("_tk_releases", "state.telekinesis_releases"),
                            _push(
                                "_tk_releases",
                                {
                                    "source_id": "_tk_source.id",
                                    "source_sequence": "_tk_source.sequence",
                                    "cast_id": "_tk_source.cast_id",
                                    "source_caster_id": "_tk_source.caster",
                                    "release_serial": "state.telekinesis_release_sequence",
                                    "subject": "_tk_source.subject",
                                    "creature_id": "_tk_source.creature_id",
                                    "plane": "_tk_source.plane",
                                    "needs_fall": "_tk_source.suspended",
                                    "condition_projected": False,
                                    "processed": False,
                                },
                            ),
                            _set("state.telekinesis_releases", "_tk_releases"),
                            _set("state.has_telekinesis_pending_releases", True),
                        ],
                    ),
                    *_patch_source(
                        {
                            "subject": None,
                            "creature_id": None,
                            "restraint_id": None,
                            "suspended": False,
                            "movement_destination": None,
                        }
                    ),
                ],
            ),
        ],
    )
    attempt = _tool_scene(
        "Resolve a new real Strength save and move only its failed actual recipient.",
        [
            _parameter("source_id"),
            _entity("caster_state"),
            _parameter("subject"),
            _array("destination"),
        ],
        [
            *_source(),
            _call(
                "validate_srd_telekinesis_cast",
                {
                    "caster_state": "{_caster_state_id}",
                    "subject": "{subject}",
                    "destination": "{destination}",
                },
                ["_tk_body", "_tk_origin", "_tk_creature", "_tk_holder"],
            ),
            _when(
                "_tk_source.subject != null and _tk_source.subject != _tk_body.id",
                [
                    _call("release_srd_telekinesis_grip", {"source_id": "{source_id}"}),
                    *_source(),
                ],
            ),
            _set("_tk_save_outcome", "'failure'"),
            _set("_tk_save", None),
            _set("_tk_save_actor", None),
            _when("_tk_creature != null", [_set("_tk_save_actor", "_tk_creature")]),
            _when("_tk_holder != null", [_set("_tk_save_actor", "_tk_holder")]),
            _when(
                "_tk_save_actor != null",
                [
                    _call(
                        "roll_srd_telekinesis_save",
                        {
                            "target_state": "{_tk_save_actor}",
                            "save_dc": "{_tk_source.save_dc}",
                        },
                        ["_tk_save", "_tk_save_outcome"],
                    ),
                ],
            ),
            _when(
                "_tk_save_outcome == 'failure'",
                [
                    _when(
                        "_tk_source.subject != null",
                        [
                            # Renewing a suspension never queues an intervening fall.
                            *_patch_source({"suspended": False}),
                            _call("release_srd_telekinesis_grip", {"source_id": "{source_id}"}),
                            *_source(),
                        ],
                    ),
                    _set("_tk_restraint", None),
                    _when(
                        "_tk_creature != null",
                        [
                            _call(
                                "install_srd_telekinesis_restraint",
                                {
                                    "target_state": "{_tk_creature}",
                                    "caster_state": "{_caster_state_id}",
                                    "save_dc": "{_tk_source.save_dc}",
                                    "concentration_sequence": "{_tk_source.sequence}",
                                },
                                ["_tk_restraint"],
                            ),
                        ],
                    ),
                    _when(
                        "_tk_holder != null",
                        [
                            _call(
                                "detach_srd_telekinesis_object",
                                {
                                    "target_state": "{_tk_holder}",
                                    "object_id": "{_tk_body.id}",
                                },
                            ),
                        ],
                    ),
                    *_action_clock(),
                    *_patch_source(
                        {
                            "subject": "_tk_body.id",
                            "creature_id": "_tk_creature",
                            "restraint_id": "_tk_restraint",
                            "suspended": False,
                            "grip_mode": "_tk_action_mode",
                            "grip_end_turn": "_tk_action_turn + 1",
                            "grip_expires_tick": "state.lasting_tick + 2",
                            "movement_destination": "destination",
                        }
                    ),
                    *_body("_tk_body.id"),
                    *_patch_body({"telekinesis_source_id": "_tk_source.id"}),
                    _call(
                        "move_srd_travel_subject",
                        {
                            "subject": "{_tk_body.id}",
                            "destination": "{destination}",
                            "plane": "{_tk_source.plane}",
                            "teleport": False,
                            "telekinesis_source_id": "{_tk_source.id}",
                        },
                    ),
                    # Entry consequences may have ended concentration; preserve that retirement.
                    *_source(),
                    *_patch_source({"movement_destination": None}),
                    _when(
                        "_tk_source.active == true and _tk_creature != null",
                        [
                            *_body("_tk_body.id"),
                            *_ground(),
                            *_patch_source(
                                {"suspended": "_info_position[2] > _fall_ground_height"}
                            ),
                        ],
                    ),
                ],
            ),
            _set("_tk_attempt_moved", "_tk_save_outcome == 'failure'"),
        ],
    )
    detach = _tool_scene(
        "Pull the actual scene object from its holder and remove only its exact inventory item.",
        [_entity("target_state"), _parameter("object_id")],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{object_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _require(
                "_info_scene_entity.metadata.is_object == true and "
                "(_info_scene_entity.metadata.carried_by == _target_state_id or "
                "_info_scene_entity.metadata.worn_by == _target_state_id)",
                "Pull only the actual object from its saved holder.",
            ),
            _set("_tk_inventory_item", None),
            _when(
                "_info_scene_entity.metadata.srd_object_utility != null and "
                "_info_scene_entity.metadata.srd_object_utility.inventory_item != ''",
                [
                    _set(
                        "_tk_inventory_item",
                        "_info_scene_entity.metadata.srd_object_utility.inventory_item",
                    ),
                ],
            ),
            *_patch_body({"carried_by": None, "worn_by": None}),
            _when(
                "_tk_inventory_item != null",
                [
                    *[
                        effect
                        for field in ("inventory", "held_items")
                        for effect in [
                            _set("_tk_inventory", []),
                            _set("_tk_kept_inventory", []),
                            _when(
                                f"target_state.{field} != null",
                                [
                                    _set("_tk_inventory", f"target_state.{field}"),
                                ],
                            ),
                            _loop(
                                "_tk_inventory",
                                "_tk_item",
                                [
                                    _when(
                                        "_tk_item != _tk_inventory_item",
                                        [
                                            _push("_tk_kept_inventory", "_tk_item"),
                                        ],
                                    ),
                                ],
                            ),
                            _set(f"target_state.{field}", "_tk_kept_inventory"),
                        ]
                    ],
                ],
            ),
        ],
    )
    install = _tool_scene(
        "Install the paid ten-minute concentration ability with optional first exertion.",
        [
            _entity("caster_state"),
            _optional("subject", default=""),
            _array("destination"),
            _optional("operation", "enum", "move", enum_values=["move", "open", "close", "pour"]),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            _parameter("concentration_modifier", "integer"),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional(
                "actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True
            ),
        ],
        [
            _call(
                "validate_srd_telekinesis_start",
                {
                    "caster_state": "{_caster_state_id}",
                    "subject": "{subject}",
                    "destination": "{destination}",
                    "operation": "{operation}",
                },
                ["_tk_caster"],
            ),
            _set("_tk_focus", ["_caster_state_id"]),
            _set("_tk_cast_owner", None),
            _when("spell_cast_owner != null", [_set("_tk_cast_owner", "_spell_cast_owner_id")]),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "spell": "telekinesis",
                    "targets": "{_tk_focus}",
                    "duration_rounds": 100,
                    "save_modifier": "{concentration_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                    "spell_cast_owner": "{_tk_cast_owner}",
                    "actual_spell_level": "{actual_spell_level}",
                },
            ),
            _set("caster_state.concentration_effect_family", "'travel_telekinesis'"),
            _when("state.telekinesis_sources == null", [_set("state.telekinesis_sources", [])]),
            _require(
                "len(state.telekinesis_sources) < 128",
                "Retire existing Telekinesis history before adding more sources.",
            ),
            _call(
                "bind_srd_telekinesis_source_identity",
                {
                    "identity": "telekinesis:{_caster_state_id}:"
                    "{caster_state.concentration_sequence}",
                },
                ["_tk_source_id"],
            ),
            *_action_clock(),
            _set(
                "_tk_source",
                {
                    "id": "_tk_source_id",
                    "spell": "'telekinesis'",
                    "caster": "_caster_state_id",
                    "sequence": "caster_state.concentration_sequence",
                    "slot_level": "slot_level",
                    "save_dc": "save_dc",
                    "plane": "_tk_caster.metadata.plane",
                    "active": True,
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 200",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": None,
                    "ongoing_duration": "spell_cast_id != ''",
                    "subject": None,
                    "creature_id": None,
                    "restraint_id": None,
                    "suspended": False,
                    "movement_destination": None,
                    "last_action_mode": "_tk_action_mode",
                    "last_action_turn": "_tk_action_turn",
                    "created_action_mode": "_tk_action_mode",
                    "created_action_turn": "_tk_action_turn",
                },
            ),
            _set("_tk_sources", "state.telekinesis_sources"),
            _push("_tk_sources", "_tk_source"),
            _set("state.telekinesis_sources", "_tk_sources"),
            _when(
                "_tk_cast_owner != null",
                [
                    *_patch_source({"source_caster_id": "_tk_cast_owner"}),
                ],
            ),
            _set("state.has_telekinesis_sources", True),
            _set("caster_state.telekinesis_source_id", "_tk_source_id"),
            _set("_tk_attempt_moved", False),
            _set("_tk_save_outcome", "'not attempted'"),
            _when(
                "subject != '' and operation == 'move'",
                [
                    _call(
                        "resolve_srd_telekinesis_attempt",
                        {
                            "source_id": "{_tk_source_id}",
                            "caster_state": "{_caster_state_id}",
                            "subject": "{subject}",
                            "destination": "{destination}",
                        },
                        ["_tk_attempt_moved", "_tk_save_outcome"],
                    ),
                ],
            ),
            _when(
                "operation == 'open' or operation == 'close'",
                [
                    _call(
                        "check_srd_telekinesis_object_magic",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{subject}",
                            "slot_level": "{slot_level}",
                        },
                    ),
                    _call(
                        "operate_srd_object_access_source_node",
                        {
                            "actor_state": "{_caster_state_id}",
                            "object_id": "{subject}",
                            "operation": "{operation}",
                            "password": "",
                            "source_range_feet": 60,
                            "source_requires_sight": True,
                            "lasting_state": "state",
                        },
                    ),
                    _set("_tk_fine_operation", "operation"),
                ],
            ),
            _when(
                "operation == 'pour'",
                [
                    _call(
                        "check_srd_telekinesis_object_magic",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{subject}",
                            "slot_level": "{slot_level}",
                        },
                    ),
                    _call(
                        "pour_srd_object_vial_source_node",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{subject}",
                            "source_id": "{_tk_source_id}",
                        },
                        ["_force_poured"],
                    ),
                    _set("_tk_fine_operation", "operation"),
                ],
            ),
            _call("prepare_srd_telekinesis_releases", {}),
        ],
    )
    later = _public(
        _tool_scene(
            "Use the actual Telekinesis concentration ability on a later Magic Action.",
            [
                _entity("caster_state"),
                _parameter("source_id"),
                _parameter("subject"),
                _array("destination"),
            ],
            [
                *_source(),
                _require(
                    ACTIVE.replace("actor_state", "caster_state"),
                    "An Incapacitated caster cannot use Telekinesis.",
                ),
                _require(
                    "combat.pending_action == null and (state.pending_srd_action_stack == null "
                    "or len(state.pending_srd_action_stack) == 0)",
                    "Finish the actual pending action before using Telekinesis.",
                ),
                _require(
                    "state.has_telekinesis_pending_releases != true",
                    "Finish the genuine queued Telekinesis fall before another Magic Action.",
                ),
                _require(
                    "_tk_source.active == true and _tk_source.caster == _caster_state_id and "
                    "state.lasting_tick < _tk_source.expires_tick",
                    "Select this caster's actual live Telekinesis source.",
                ),
                _call(
                    "check_srd_condition_concentration",
                    {
                        "caster_state": "{_caster_state_id}",
                        "sequence": "{_tk_source.sequence}",
                    },
                    ["_condition_concentration_active"],
                ),
                _require(
                    "_condition_concentration_active == true and "
                    "caster_state.concentration_spell_id == 'telekinesis' and "
                    "caster_state.concentration_effect_family == 'travel_telekinesis'",
                    "Use the exact active Telekinesis concentration.",
                ),
                _call(
                    "validate_srd_telekinesis_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "subject": "{subject}",
                        "destination": "{destination}",
                    },
                ),
                *_action_clock(),
                _require(
                    "_tk_source.created_action_mode != _tk_action_mode or "
                    "_tk_action_turn > _tk_source.created_action_turn",
                    "Use Telekinesis only on a later turn.",
                ),
                _call(
                    "check_srd_active_control_action",
                    {
                        "actor_state": "{_caster_state_id}",
                        "action_kind": "action",
                        "magic_action": True,
                    },
                ),
                _when(
                    "combat.encounter_active == true",
                    [
                        _call("spend_srd_action", {"magic_action": True}),
                    ],
                ),
                *_patch_source(
                    {"last_action_mode": "_tk_action_mode", "last_action_turn": "_tk_action_turn"}
                ),
                _call(
                    "resolve_srd_telekinesis_attempt",
                    {
                        "source_id": "{source_id}",
                        "caster_state": "{_caster_state_id}",
                        "subject": "{subject}",
                        "destination": "{destination}",
                    },
                    ["_tk_attempt_moved", "_tk_save_outcome"],
                ),
                _call("prepare_srd_telekinesis_releases", {}),
            ],
        ),
        "exploration",
        "Telekinesis",
        [("Moved", "_tk_attempt_moved"), ("Strength save", "_tk_save_outcome")],
    )
    retire = _tool_scene(
        "End only this exact Telekinesis concentration and queue its suspension release.",
        [_parameter("source_id")],
        [
            *_source(),
            _when(
                "_tk_source.active == true",
                [
                    _call("release_srd_telekinesis_grip", {"source_id": "{source_id}"}),
                    *_source(),
                    *_patch_source({"active": False}),
                ],
            ),
        ],
    )
    project_release = _tool_scene(
        "Reproject exact retired recipient conditions outside the source retirement call graph.",
        [_entity("target_state")],
        [
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "state",
                },
            )
        ],
    )
    prepare_releases = _tool_scene(
        "Prepare actual elevated recipients for the existing outer scene-fall reaction allocator.",
        [],
        [
            _set("_tk_release_targets", []),
            _set("state.has_telekinesis_pending_releases", False),
            _when(
                "state.telekinesis_releases != null and len(state.telekinesis_releases) > 0",
                [
                    *_snapshot(),
                    _set("_tk_pending_releases", "state.telekinesis_releases"),
                    _set("_tk_retained_releases", []),
                    _set("_tk_prepared_serials", []),
                    _loop(
                        "_tk_pending_releases",
                        "_tk_release",
                        [
                            _require(
                                "_tk_release.release_serial != null and "
                                "_tk_release.source_sequence != null",
                                "Reconcile the exact Telekinesis release identity.",
                            ),
                            _push("_tk_prepared_serials", "_tk_release.release_serial"),
                            _when(
                                "_tk_release.processed != true",
                                [
                                    _when(
                                        "_tk_release.condition_projected != true",
                                        [
                                            _call(
                                                "project_srd_telekinesis_release",
                                                {
                                                    "target_state": "{_tk_release.creature_id}",
                                                },
                                            ),
                                            _set(
                                                "_tk_release_patch", {"condition_projected": True}
                                            ),
                                            _set(
                                                "_tk_release",
                                                "merge(_tk_release, _tk_release_patch)",
                                            ),
                                        ],
                                    ),
                                    _set("_tk_release_falls", False),
                                    _when(
                                        "_tk_release.needs_fall == true",
                                        [
                                            *_body("_tk_release.subject"),
                                            _require(
                                                "_info_scene_entity.metadata.plane == "
                                                "_tk_release.plane",
                                                "Reconcile the released creature's plane.",
                                            ),
                                            *_ground(),
                                            _set(
                                                "_tk_release_falls",
                                                "_info_position[2] > _fall_ground_height",
                                            ),
                                            _when(
                                                "_tk_release_falls == true",
                                                [
                                                    _require(
                                                        "_info_scene_entity.metadata."
                                                        "telekinesis_source_id == null",
                                                        "Reconcile independent grips.",
                                                    ),
                                                    _when(
                                                        "_tk_release.creature_id not in "
                                                        "_tk_release_targets",
                                                        [
                                                            _push(
                                                                "_tk_release_targets",
                                                                "_tk_release.creature_id",
                                                            ),
                                                        ],
                                                    ),
                                                    _set(
                                                        "state.has_telekinesis_pending_releases",
                                                        True,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_tk_release_falls != true",
                                        [
                                            _set("_tk_release_patch", {"processed": True}),
                                            _set(
                                                "_tk_release",
                                                "merge(_tk_release, _tk_release_patch)",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_tk_release.processed != true",
                                [
                                    _push("_tk_retained_releases", "_tk_release"),
                                ],
                            ),
                        ],
                    ),
                    _set("_tk_live_releases", "state.telekinesis_releases"),
                    _loop(
                        "_tk_live_releases",
                        "_tk_new_release",
                        [
                            _when(
                                "_tk_new_release.release_serial not in _tk_prepared_serials",
                                [
                                    _push("_tk_retained_releases", "_tk_new_release"),
                                    _set("state.has_telekinesis_pending_releases", True),
                                ],
                            ),
                        ],
                    ),
                    _set("state.telekinesis_releases", "_tk_retained_releases"),
                ],
            ),
        ],
    )
    turn = _tool_scene(
        "Release grips at the actual caster's next completed turn rather than the target's turn.",
        [_entity("caster_state"), _parameter("boundary_turn", "integer")],
        [
            _when(
                "state.telekinesis_sources != null",
                [
                    _set("_tk_turn_sources", "state.telekinesis_sources"),
                    _loop(
                        "_tk_turn_sources",
                        "_tk_turn_source",
                        [
                            _when(
                                "_tk_turn_source.caster == _caster_state_id and "
                                "_tk_turn_source.subject != null and "
                                "_tk_turn_source.grip_mode == 'combat' "
                                "and boundary_turn >= _tk_turn_source.grip_end_turn",
                                [
                                    _call(
                                        "release_srd_telekinesis_grip",
                                        {"source_id": "{_tk_turn_source.id}"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    refresh_one = _tool_scene(
        "Refresh real concentration and the exploration grip clock; queue releases.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _when(
                "_tk_source.active == true",
                [
                    _call(
                        "refresh_srd_concentration_source",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                            "lasting_state": "state",
                        },
                    ),
                    _call(
                        "check_srd_condition_concentration",
                        {
                            "caster_state": "{_caster_state_id}",
                            "sequence": "{_tk_source.sequence}",
                        },
                        ["_condition_concentration_active"],
                    ),
                    {
                        "condition": "_condition_concentration_active != true or "
                        "state.lasting_tick >= _tk_source.expires_tick",
                        "on_true": [
                            _call("end_srd_telekinesis_source", {"source_id": "{source_id}"})
                        ],
                        "on_false": [
                            _when(
                                "_tk_source.subject != null and "
                                "_tk_source.grip_mode == 'exploration' "
                                "and state.lasting_tick >= _tk_source.grip_expires_tick",
                                [
                                    _call(
                                        "release_srd_telekinesis_grip", {"source_id": "{source_id}"}
                                    ),
                                ],
                            )
                        ],
                    },
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Refresh registered Telekinesis abilities through actual source concentration.",
        [],
        [
            _set("state.has_telekinesis_sources", False),
            _when(
                "state.telekinesis_sources != null",
                [
                    _set("_tk_clock_sources", "state.telekinesis_sources"),
                    _loop(
                        "_tk_clock_sources",
                        "_tk_clock_source",
                        [
                            _when(
                                "_tk_clock_source.active == true",
                                [
                                    _call(
                                        "refresh_srd_telekinesis_source",
                                        {
                                            "source_id": "{_tk_clock_source.id}",
                                            "caster_state": "{_tk_clock_source.caster}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("_tk_clock_sources", "state.telekinesis_sources"),
                    _loop(
                        "_tk_clock_sources",
                        "_tk_clock_source",
                        [
                            _when(
                                "_tk_clock_source.active == true",
                                [_set("state.has_telekinesis_sources", True)],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    guard = _tool_scene(
        "A suspended body moves through its exact owning intent or genuine teleport.",
        [
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False),
            _optional("telekinesis_source_id", default="", model_hidden=True),
        ],
        [
            *_body("subject"),
            _when(
                "_info_scene_entity.metadata.telekinesis_source_id != null and teleport != true",
                [
                    _set("_tk_marker", "_info_scene_entity.metadata.telekinesis_source_id"),
                    _call(
                        "qualify_srd_telekinesis_movement",
                        {
                            "source_id": "{_tk_marker}",
                            "subject": "{_info_scene_entity.id}",
                            "destination": "{destination}",
                            "plane": "{plane}",
                            "authorization": "{telekinesis_source_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    qualify = _tool_scene(
        "Qualify source-owned forced movement from the actual saved target and exact destination.",
        [
            _parameter("source_id"),
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("authorization", default="", model_hidden=True),
        ],
        [
            *_source(),
            _when(
                "_tk_source.suspended == true or _tk_source.movement_destination != null",
                [
                    _set("_tk_destinations", ["_tk_source.movement_destination"]),
                    _require(
                        "authorization == _tk_source.id and _tk_source.active == true and "
                        "_tk_source.subject == subject and _tk_source.plane == plane and "
                        "destination in _tk_destinations",
                        "A suspended Telekinesis body needs its exact source-owned movement.",
                    ),
                ],
            ),
        ],
    )
    result = {
        "record_srd_telekinesis_object_size": size,
        "qualify_srd_telekinesis_creature": creature,
        "qualify_srd_telekinesis_holder": holder,
        "validate_srd_telekinesis_cast": validate,
        "validate_srd_telekinesis_start": start,
        "roll_srd_telekinesis_save": _tool_scene(
            "Roll the printed actual Strength saving throw.",
            [_entity("target_state"), _parameter("save_dc", "integer", minimum=1, maximum=40)],
            _save_roll(),
        ),
        "install_srd_telekinesis_restraint": restraint,
        "release_srd_telekinesis_grip": release,
        "resolve_srd_telekinesis_attempt": attempt,
        "detach_srd_telekinesis_object": detach,
        "install_srd_telekinesis": install,
        "use_srd_telekinesis": later,
        "end_srd_telekinesis_source": retire,
        "project_srd_telekinesis_release": project_release,
        "prepare_srd_telekinesis_releases": prepare_releases,
        "end_srd_telekinesis_turn": turn,
        "refresh_srd_telekinesis_source": refresh_one,
        "refresh_srd_telekinesis_sources": refresh,
        "check_srd_telekinesis_movement": guard,
        "qualify_srd_telekinesis_movement": qualify,
        "qualify_srd_telekinesis_release_identity": _tool_scene(
            "Retain the released recipient's exact source serial, caster and cast identity.",
            [
                _parameter("source_id"),
                _parameter("source_sequence", "integer", minimum=1),
                _parameter("source_caster_id"),
                _optional("cast_id", default=""),
            ],
            [
                *_source(),
                _require(
                    "_tk_source.sequence == source_sequence and "
                    "_tk_source.caster == source_caster_id and _tk_source.cast_id == cast_id",
                    "The pending release must retain its actual Telekinesis source identity.",
                ),
            ],
        ),
        "bind_srd_telekinesis_source_identity": _tool_scene(
            "Bind the actual concentration identity.",
            [_parameter("identity")],
            [_set("_tk_source_id", "identity")],
        ),
    }
    if "begin_srd_scene_fall" in tools:
        result["handoff_srd_telekinesis_releases"] = _fall_handoff()
    if __package__:
        from .srd_spell_travel_telekinesis_fine import fine_tools
    else:
        from srd_spell_travel_telekinesis_fine import fine_tools
    fine = fine_tools(tools, later)
    result.update(fine)
    if __package__:
        from .srd_spell_travel_telekinesis_pour import pour_tools
    else:
        from srd_spell_travel_telekinesis_pour import pour_tools
    result.update(pour_tools(tools, fine.get("operate_srd_telekinesis_object")))
    return result


def emit_late_handoff(text):
    """Add only the deferred release leaf after final Reaction emission supplies the allocator."""
    tools = _load_runtime(text)["tools"]
    if "begin_srd_scene_fall" not in tools:
        return text
    required = {"prepare_srd_telekinesis_releases", "qualify_srd_telekinesis_release_identity"}
    missing = required - tools.keys()
    if missing:
        raise ValueError(f"Telekinesis late handoff requires existing owners: {sorted(missing)}")
    leaf = _fall_handoff()
    if tools.get("handoff_srd_telekinesis_releases") == leaf:
        return text
    return _replace_tools(text, {"handoff_srd_telekinesis_releases": leaf})


def _fall_handoff():
    """Hand exact releases to the existing saved fall owner after current windows finish."""
    return _tool_scene(
        "Allocate the actual scene-fall window once for source-qualified released recipients.",
        [],
        [
            _set("_tk_handoff_action_id", None),
            _set("_tk_handoff_targets", []),
            _call("prepare_srd_telekinesis_releases", {}, ["_tk_release_targets"]),
            _when(
                "len(_tk_release_targets) > 0 and state.pending_srd_action == null and "
                "(state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0)",
                [
                    _loop(
                        "_tk_release_targets",
                        "_tk_falling_id",
                        [
                            _when(
                                "len(_tk_handoff_targets) < 64",
                                [_push("_tk_handoff_targets", "_tk_falling_id")],
                            ),
                        ],
                    ),
                    _call(
                        "begin_srd_scene_fall",
                        {
                            "target_states": "{_tk_handoff_targets}",
                            "fall_cause": "support_lost",
                        },
                        ["_pending_action_id"],
                    ),
                    _set("_tk_handoff_action_id", "_pending_action_id"),
                    _set("_tk_handoff_event", "state.pending_srd_actions[_pending_action_id]"),
                    _require(
                        "_tk_handoff_event.kind == 'scene_fall' and "
                        "_tk_handoff_event.status == 'open'",
                        "The actual fall allocator must retain its saved open event.",
                    ),
                    _set("_tk_handoff_receipts", []),
                    _set("_tk_unhanded_releases", []),
                    _set("_tk_handoff_releases", "state.telekinesis_releases"),
                    _loop(
                        "_tk_handoff_releases",
                        "_tk_handoff_release",
                        [
                            {
                                "condition": "_tk_handoff_release.creature_id in "
                                "_tk_handoff_targets and _tk_handoff_release.processed != true",
                                "on_true": [
                                    _call(
                                        "qualify_srd_telekinesis_release_identity",
                                        {
                                            "source_id": "{_tk_handoff_release.source_id}",
                                            "source_sequence": "{_tk_handoff_release."
                                            "source_sequence}",
                                            "source_caster_id": "{_tk_handoff_release."
                                            "source_caster_id}",
                                            "cast_id": "{_tk_handoff_release.cast_id}",
                                        },
                                    ),
                                    _set(
                                        "_tk_handoff_patch",
                                        {
                                            "handed_off": True,
                                            "pending_action_id": "_pending_action_id",
                                        },
                                    ),
                                    _set(
                                        "_tk_handoff_release",
                                        "merge(_tk_handoff_release, _tk_handoff_patch)",
                                    ),
                                    _push("_tk_handoff_receipts", "_tk_handoff_release"),
                                ],
                                "on_false": [
                                    _push("_tk_unhanded_releases", "_tk_handoff_release"),
                                ],
                            },
                        ],
                    ),
                    _set(
                        "state.pending_srd_actions.{_pending_action_id}.context."
                        "telekinesis_release_receipts",
                        "_tk_handoff_receipts",
                    ),
                    _set("state.telekinesis_releases", "_tk_unhanded_releases"),
                    _set(
                        "state.has_telekinesis_pending_releases",
                        "len(_tk_unhanded_releases) > 0",
                    ),
                ],
            ),
        ],
    )


def bind_telekinesis_consumers(tools, changed):
    """Retire grips and observe exact clocks at their owning boundaries, preserving one mover."""
    ending = deepcopy(
        changed.get("end_srd_concentration_node", tools["end_srd_concentration_node"])
    )
    marker = "source_state.concentration_effect_family == 'travel_telekinesis'"
    ending["effects"] = [e for e in ending["effects"] if e.get("condition") != marker]
    ending["effects"].insert(
        0,
        _when(
            marker,
            [
                _when(
                    "source_state.telekinesis_source_id != null",
                    [
                        _call(
                            "end_srd_telekinesis_source",
                            {"source_id": "{source_state.telekinesis_source_id}"},
                        ),
                        _set("source_state.telekinesis_source_id", None),
                    ],
                ),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = ending
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [
        e for e in clock["effects"] if "refresh_srd_telekinesis_sources" not in str(e)
    ]
    clock["effects"].append(
        _when(
            "state.has_telekinesis_sources == true",
            [
                _call("refresh_srd_telekinesis_sources", {}),
            ],
        )
    )
    clock["effects"] = [
        e for e in clock["effects"] if e.get("call") != "prepare_srd_telekinesis_releases"
    ]
    clock["effects"].append(_call("prepare_srd_telekinesis_releases", {}))
    changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(changed.get("end_combat_turn", tools["end_combat_turn"]))
    end["effects"] = [e for e in end["effects"] if "end_srd_telekinesis_turn" not in str(e)]
    boundary = next(
        i
        for i, e in enumerate(end["effects"])
        if e.get("set", {}).get("field") == "combat.action_available"
    )
    end["effects"].insert(
        boundary,
        _when(
            "state.has_telekinesis_sources == true",
            [
                _call(
                    "end_srd_telekinesis_turn",
                    {"caster_state": "{_actor_id}", "boundary_turn": "{_ending_turn}"},
                ),
            ],
        ),
    )
    end["effects"] = [
        e for e in end["effects"] if e.get("call") != "prepare_srd_telekinesis_releases"
    ]
    end["effects"].append(_call("prepare_srd_telekinesis_releases", {}))
    changed["end_combat_turn"] = end
    for name in ("end_srd_concentration_voluntarily", "end_concentration"):
        if name in tools or name in changed:
            end_source = deepcopy(changed.get(name, tools.get(name)))
            end_source["effects"] = [
                e
                for e in end_source["effects"]
                if e.get("call") != "prepare_srd_telekinesis_releases"
            ]
            end_source["effects"].append(_call("prepare_srd_telekinesis_releases", {}))
            changed[name] = end_source
    for name in (
        "move_srd_travel_subject",
        "move_srd_travel_subject_node",
        "move_srd_scene_creature_environment_segment",
    ):
        if name not in tools and name not in changed:
            continue
        move = deepcopy(changed.get(name, tools.get(name)))
        move["effects"] = [
            e for e in move["effects"] if "check_srd_telekinesis_movement" not in str(e)
        ]
        params = {
            "subject": "{_actor_state_id}",
            "destination": "{destination}",
            "plane": "{_travel_movement_plane}",
            "teleport": False,
        }
        if name in {"move_srd_travel_subject", "move_srd_travel_subject_node"}:
            if not any(p["name"] == "telekinesis_source_id" for p in move["parameters"]):
                move["parameters"].append(
                    _optional("telekinesis_source_id", default="", model_hidden=True)
                )
            params = {
                "subject": "{_travel_mover.id}",
                "destination": "{destination}",
                "plane": "{plane}",
                "teleport": "{teleport}",
                "telekinesis_source_id": "{telekinesis_source_id}",
            }
            if move["effects"][0].get("call") == "move_srd_travel_subject_node":
                move["effects"][0]["params"]["telekinesis_source_id"] = "{telekinesis_source_id}"
                changed[name] = move
                continue
        boundary = next(i for i, e in enumerate(move["effects"]) if "spatial_mutation" in e)
        move["effects"].insert(
            boundary,
            _when(
                "state.has_telekinesis_sources == true",
                [
                    _call("check_srd_telekinesis_movement", params),
                ],
            ),
        )
        changed[name] = move
