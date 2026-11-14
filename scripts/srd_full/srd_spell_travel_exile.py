"""Temporary planar exile reuses saved scene positions, concentration and actual Study rolls."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_spell_travel_banishment import associated_type_condition, banishment_tools
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_scene_relations import _tool_scene
    from srd_spell_travel_banishment import associated_type_condition, banishment_tools
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public, _push


def exile_tools(tools):
    """Record historical return anchors; the shared relocation leaf owns every plane mutation."""
    if __package__:
        from .srd_spell_travel import ACTIVE, _move, _placement, _scene_subject
    else:
        from srd_spell_travel import ACTIVE, _move, _placement, _scene_subject

    identity = [
        _optional("spell_cast_id", default="", model_hidden=True),
        _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
        _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True),
    ]
    validate = _tool_scene(
        "Validate actual visible exile recipients before the shared cast payment.",
        [
            _entity("caster_state"),
            _parameter("spell", "enum", enum_values=["banishment", "maze"]),
            _parameter("slot_level", "integer", minimum=4, maximum=9),
            _parameter("targets", "array", items={"type": "string"}),
        ],
        [
            _require(
                "(spell == 'maze' and slot_level >= 8 and len(targets) == 1) or "
                "(spell == 'banishment' and len(targets) >= 1 and len(targets) <= slot_level - 3)",
                "Maze selects one creature; Banishment adds one creature per slot above fourth.",
            ),
            _set("_exile_seen", []),
            _set("_exile_associated_count", 0),
            _loop(
                "targets",
                "_exile_target",
                [
                    _require(
                        "(spell == 'maze' or _exile_target != _caster_state_id) and "
                        "_exile_target not in _exile_seen",
                        "Select distinct targets; self-Banishment is not yet supported.",
                    ),
                    _push("_exile_seen", "_exile_target"),
                    _call(
                        "validate_srd_travel_exile_target",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_exile_target}",
                            "spell": "{spell}",
                        },
                        ["_exile_type"],
                    ),
                    _when(
                        "spell == 'banishment' and " + associated_type_condition("_exile_type"),
                        [_set("_exile_associated_count", "_exile_associated_count + 1")],
                    ),
                ],
            ),
            _require(
                "_exile_associated_count <= 1",
                "Multiple associated-plane recipients await simultaneous random arrival "
                "settlement.",
            ),
        ],
    )
    target_validate = _tool_scene(
        "Qualify the actual creature body, plane, range and sight for an exile spell.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("spell", "enum", enum_values=["banishment", "maze"]),
        ],
        [
            _when(
                "target_state.has_transformations == true",
                [
                    _call(
                        "project_srd_transformation",
                        {"target_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                ],
            ),
            _require("target_state.travel_exile == null", "End the existing exile source first."),
            *_scene_subject("{_target_state_id}", "_exile_body"),
            _require(
                "_exile_body.metadata.is_object != true and "
                "_exile_body.metadata.creature_type != 'spell_force'",
                "This exile spell targets an actual creature.",
            ),
            _set("_exile_type", None),
            {
                "condition": "target_state.creature_type != null",
                "on_true": [
                    _set("_exile_type", "target_state.creature_type"),
                ],
                "on_false": [
                    _when(
                        "_exile_body.metadata.creature_type != null",
                        [
                            _set("_exile_type", "_exile_body.metadata.creature_type"),
                        ],
                    ),
                ],
            },
            _require("_exile_type != null", "Record the actual creature type before planar exile."),
            _when(
                "spell == 'banishment' and " + associated_type_condition("_exile_type"),
                [_call("resolve_srd_banishment_association", {
                    "target_state": "{_target_state_id}", "creature_type": "{_exile_type}",
                })],
            ),
            _require("_exile_body.metadata.plane != null", "Record the target's actual plane."),
            _call(
                "resolve_srd_scene_relation_node",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ["_scene_same_plane", "_scene_can_see", "_scene_distance"],
            ),
            _set("_exile_range", 30),
            _when("spell == 'maze'", [_set("_exile_range", 60)]),
            _require(
                "_scene_same_plane == true and _scene_can_see == true and "
                "_scene_distance <= _exile_range",
                "Exile requires a creature the actual caster can see within its printed range.",
            ),
            _when(
                "spell == 'banishment'",
                [
                    _require(
                        "target_state.saving_throw_modifiers.charisma != null",
                        "Record the target's actual Charisma saving-throw modifier.",
                    ),
                ],
            ),
        ],
    )
    save = _tool_scene(
        "Roll the actual Charisma save for exile independently of Incapacitated immunity.",
        [_entity("target_state"), _parameter("save_dc", "integer", minimum=1, maximum=40)],
        [
            _require(
                "target_state.saving_throw_modifiers.charisma != null",
                "Record the actual Charisma saving-throw modifier.",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "ability": "charisma",
                    "kind": "saving_throw",
                    "ability_modifier": "{target_state.saving_throw_modifiers.charisma}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{save_dc}",
                    "advantage": False,
                    "disadvantage": False,
                    "forego_save": False,
                },
                ["_d20_test", "_outcome", "_adjusted_total"],
            ),
            _set("_exile_save", "_d20_test"),
            _set("_exile_save_outcome", "_outcome"),
            _set("target_state.last_banishment_save", "_d20_test"),
            _set("target_state.last_banishment_save_outcome", "_outcome"),
        ],
    )
    apply = _tool_scene(
        "Install one concentration-bound actual exile and its original return anchor.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("spell", "enum", enum_values=["banishment", "maze"]),
            _parameter("slot_level", "integer", minimum=4, maximum=9),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            *identity,
        ],
        [
            _require("target_state.travel_exile == null", "End the existing exile source first."),
            *_scene_subject("{_target_state_id}", "_exile_departure"),
            _set("_exile_owner", None),
            _when("spell_cast_owner != null", [_set("_exile_owner", "_spell_cast_owner_id")]),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "_exile_owner == _caster_state_id and actual_spell_level == slot_level and "
                        "caster_state.last_spell_source_cast_id == spell_cast_id and "
                        "caster_state.last_spell_source_spell == spell",
                        "Exile must retain its actual allocated cast owner and slot.",
                    ),
                ],
            ),
            _call(
                "bind_srd_travel_exile_plane",
                {
                    "plane": (
                        "srd_exile:{_caster_state_id}:"
                        "{caster_state.concentration_sequence}:{_target_state_id}"
                    ),
                },
                ["_exile_plane"],
            ),
            _set(
                "target_state.travel_exile",
                {
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "slot_level": "slot_level",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": None,
                    "ongoing_duration": "spell_cast_id != ''",
                    "sequence": "caster_state.concentration_sequence",
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "caster_state.concentration_expires_tick",
                    "return_plane": "_exile_departure.metadata.plane",
                    "return_position": "_exile_departure.position",
                    "demiplane": "_exile_plane",
                    "condition_source_id": None,
                    "return_pending": False,
                    "permanent_pending": False,
                    "association": None,
                },
            ),
            _set("_exile_granted_type", "target_state.creature_type"),
            _when(
                "target_state.creature_type == null",
                [_set("_exile_granted_type", "_exile_departure.metadata.creature_type")],
            ),
            _when(
                "spell == 'banishment' and " + associated_type_condition("_exile_granted_type"),
                [
                    _call("resolve_srd_banishment_association", {
                        "target_state": "{_target_state_id}",
                        "creature_type": "{_exile_granted_type}",
                    }, ["_banishment_association"]),
                    _set("target_state.travel_exile.association", "_banishment_association"),
                ],
            ),
            _when(
                "spell_cast_owner != null",
                [
                    _set("target_state.travel_exile.source_caster_id", "_spell_cast_owner_id"),
                ],
            ),
            _when("state.travel_exile_targets == null", [_set("state.travel_exile_targets", [])]),
            _set("_exile_registered", "state.travel_exile_targets"),
            _when(
                "_target_state_id not in _exile_registered",
                [_push("_exile_registered", "_target_state_id")],
            ),
            _set("state.travel_exile_targets", "_exile_registered"),
            *_move("{_target_state_id}", "{_exile_departure.position}", "{_exile_plane}", False),
            _when(
                "spell == 'banishment'",
                [
                    _call(
                        "check_srd_target_condition_immunity",
                        {
                            "target_state": "{_target_state_id}",
                            "condition": "incapacitated",
                            "source_state_id": "{_caster_state_id}",
                            "source_is_magical": True,
                        },
                        ["_source_condition_immune"],
                    ),
                    _when(
                        "_source_condition_immune != true",
                        [
                            _call(
                                "install_srd_timed_condition_source",
                                {
                                    "target_state": "{_target_state_id}",
                                    "caster_state": "{_caster_state_id}",
                                    "lasting_state": "state",
                                    "spell": "banishment",
                                    "condition": "incapacitated",
                                    "save_dc": "{save_dc}",
                                    "duration_rounds": 10,
                                    "repeat_save_timing": "none",
                                    "save_ability": "charisma",
                                    "concentration_sequence": (
                                        "{caster_state.concentration_sequence}"
                                    ),
                                    "actual_spell_level": "{actual_spell_level}",
                                    "spell_cast_id": "{spell_cast_id}",
                                    "spell_cast_owner": "{_exile_owner}",
                                },
                                ["_timed_condition_source_id"],
                            ),
                            _set(
                                "target_state.travel_exile.condition_source_id",
                                "_timed_condition_source_id",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    end = _tool_scene(
        "Return only this exile recipient through exact actual nearest clear placement.",
        [_entity("target_state")],
        [
            _when(
                "target_state.travel_exile != null",
                [
                    _set("_exile_return_record", "target_state.travel_exile"),
                    _when(
                        "_exile_return_record.condition_source_id != null",
                        [
                            _set(
                                "_exile_return_condition",
                                "state.timed_condition_sources[_exile_return_record.condition_source_id]",
                            ),
                            _when(
                                "_exile_return_condition != null and "
                                "_exile_return_condition.installed == true "
                                "and _exile_return_condition.spell_id == 'banishment' and "
                                "_exile_return_condition.target_state_id == _target_state_id and "
                                "_exile_return_condition.concentration_sequence == "
                                "_exile_return_record.sequence",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "lasting_state": "state",
                                            "source_id": (
                                                "{_exile_return_record.condition_source_id}"
                                            ),
                                        },
                                    )
                                ],
                            ),
                            _call(
                                "refresh_srd_timed_condition_target",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "state",
                                },
                            ),
                        ],
                    ),
                    {
                        "condition": "_exile_return_record.permanent_pending == true",
                        "on_true": [
                            _call("transport_srd_permanent_banishment", {
                                "target_state": "{_target_state_id}",
                            }),
                        ],
                        "on_false": [
                            *_placement(
                                "{_target_state_id}",
                                "{_exile_return_record.return_position}",
                                "{_exile_return_record.return_plane}",
                                [],
                            ),
                            *_move(
                                "{_target_state_id}",
                                "{_travel_placement.position}",
                                "{_exile_return_record.return_plane}",
                                False,
                            ),
                        ],
                    },
                    _set("target_state.travel_exile", None),
                ],
            )
        ],
    )
    refresh_target = _tool_scene(
        "Settle an exile when its real concentration or printed lifetime has ended.",
        [_entity("target_state"), _entity("caster_state")],
        [
            _when(
                "target_state.travel_exile != null",
                [
                    _when(
                        "caster_state.concentration_effect_family == 'travel_exile' and "
                        "caster_state.concentration_sequence == target_state.travel_exile.sequence",
                        [
                            _call(
                                "refresh_srd_concentration_source",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "state",
                                },
                            )
                        ],
                    ),
                    _call(
                        "check_srd_condition_concentration",
                        {
                            "caster_state": "{_caster_state_id}",
                            "sequence": "{target_state.travel_exile.sequence}",
                        },
                        ["_condition_concentration_active"],
                    ),
                    _when(
                        "_condition_concentration_active != true or "
                        "state.lasting_tick >= target_state.travel_exile.expires_tick",
                        [
                            _call(
                                "end_srd_travel_exile_target",
                                {"target_state": "{_target_state_id}"},
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    refresh = _tool_scene(
        "Refresh the actual indexed exile grants after the shared clock advances.",
        [],
        [
            _when(
                "state.travel_exile_targets != null",
                [
                    _set("_exile_refresh_ids", "state.travel_exile_targets"),
                    _set("_exile_remaining_ids", []),
                    _loop(
                        "_exile_refresh_ids",
                        "_exile_refresh_id",
                        [
                            _call(
                                "refresh_srd_travel_exile_indexed_target",
                                {"target_state": "{_exile_refresh_id}"},
                                ["_exile_retained"],
                            ),
                            _when(
                                "_exile_retained == true",
                                [_push("_exile_remaining_ids", "_exile_refresh_id")],
                            ),
                        ],
                    ),
                    _set("state.travel_exile_targets", "_exile_remaining_ids"),
                ],
            )
        ],
    )
    indexed = _tool_scene(
        "Resolve the actual saved caster for one indexed exile recipient.",
        [_entity("target_state")],
        [
            _when(
                "target_state.travel_exile != null",
                [
                    _call(
                        "refresh_srd_travel_exile_target",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{target_state.travel_exile.caster}",
                        },
                    ),
                ],
            ),
            _set("_exile_retained", "target_state.travel_exile != null"),
        ],
    )
    cast = _tool_scene(
        "Start the actual exile concentration and resolve each source-defined recipient.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            _parameter("concentration_modifier", "integer"),
            _parameter("targets", "array", items={"type": "string"}),
            *identity,
        ],
        [
            _call(
                "validate_srd_travel_exile_cast",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                    "slot_level": "{slot_level}",
                    "targets": "{targets}",
                },
            ),
            _set("_exile_cast_owner", None),
            _when("spell_cast_owner != null", [_set("_exile_cast_owner", "_spell_cast_owner_id")]),
            _set("_exile_duration", 10),
            _when("spell == 'maze'", [_set("_exile_duration", 100)]),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "spell": "{spell}",
                    "targets": "{targets}",
                    "duration_rounds": "{_exile_duration}",
                    "save_modifier": "{concentration_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                    "spell_cast_owner": "{_exile_cast_owner}",
                    "actual_spell_level": "{actual_spell_level}",
                },
            ),
            _set("caster_state.concentration_effect_family", "'travel_exile'"),
            _set("caster_state.travel_exile_targets", "targets"),
            _loop(
                "targets",
                "_exile_cast_target",
                [
                    _set("_exile_save_outcome", "'failure'"),
                    _when(
                        "spell == 'banishment'",
                        [
                            _call(
                                "roll_srd_banishment_save",
                                {"target_state": "{_exile_cast_target}", "save_dc": "{save_dc}"},
                                ["_exile_save_outcome"],
                            ),
                        ],
                    ),
                    _when(
                        "_exile_save_outcome == 'failure'",
                        [
                            _call(
                                "apply_srd_travel_exile_target",
                                {
                                    "target_state": "{_exile_cast_target}",
                                    "caster_state": "{_caster_state_id}",
                                    "spell": "{spell}",
                                    "slot_level": "{slot_level}",
                                    "save_dc": "{save_dc}",
                                    "spell_cast_id": "{spell_cast_id}",
                                    "spell_cast_owner": "{_exile_cast_owner}",
                                    "actual_spell_level": "{actual_spell_level}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    # Native participant D20 calculation and stat-block NPC modifiers share the same later riders.
    study_roll = deepcopy(
        next(e for e in tools["study_srd_action"]["effects"] if e.get("call") == "d20_test")
    )
    study_roll["params"].update(
        actor="{_actor_id}",
        actor_state="{_actor_state_id}",
        skill="investigation",
        target_number=20,
        proficiency_factor="{maze_proficiency_factor}",
        circumstantial_modifier=0,
        advantage=False,
        disadvantage=False,
    )
    study_roll["export_context"] = {
        "_maze_total": "_adjusted_total",
        "_maze_outcome": "_outcome",
        "_maze_roll": "_d20_test",
    }
    study_parameters = [
        _optional("actor", default=None, resolve="participant"),
        _entity("actor_state"),
        _optional(
            "maze_skill",
            "enum",
            "investigation",
            enum_values=["investigation"],
            model_derive={"kind": "constant", "value": "investigation"},
        ),
        _parameter(
            "maze_proficiency_factor",
            "enum",
            enum_values=["none", "half", "normal", "double"],
            model_derive={
                "kind": "participant_membership",
                "participant": "actor",
                "source": "maze_skill",
                "field": "skill_proficiencies",
                "present": "normal",
                "absent": "none",
            },
        ),
    ]
    study_parameters[1]["model_derive"] = {"kind": "copy", "source": "actor"}
    npc_roll = _call(
        "d20_test",
        {
            "actor_state": "{_actor_state_id}",
            "ability": "intelligence",
            "skill": "investigation",
            "kind": "ability_check",
            "ability_modifier": "{actor_state.skill_check_modifiers.investigation}",
            "proficiency_bonus": 0,
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": 20,
            "advantage": False,
            "disadvantage": False,
        },
    )
    npc_roll["export_context"] = deepcopy(study_roll["export_context"])
    study = _public(
        _tool_scene(
            "Use the actual Study action and DC20 Intelligence Investigation to escape Maze.",
            study_parameters,
            [
                _call(
                    "refresh_srd_travel_exile_indexed_target", {"target_state": "{_actor_state_id}"}
                ),
                _require(
                    "actor_state.travel_exile != null and actor_state.travel_exile.spell == 'maze'",
                    "This creature has no active Maze to escape.",
                ),
                *_scene_subject("{_actor_state_id}", "_maze_body"),
                _require(
                    "_maze_body.metadata.plane == actor_state.travel_exile.demiplane",
                    "The creature must actually be inside its Maze.",
                ),
                _require(ACTIVE, "An Incapacitated creature cannot take the Study action."),
                _require(
                    "state.pending_srd_action_stack == null or "
                    "len(state.pending_srd_action_stack) == 0",
                    "Complete the pending action before Study.",
                ),
                _call(
                    "check_srd_active_control_action",
                    {
                        "actor_state": "{_actor_state_id}",
                        "action_kind": "action",
                        "magic_action": False,
                    },
                ),
                _when(
                    "combat.encounter_active == true",
                    [
                        _require(
                            "combat.turn_active == true and combat.active_actor == _actor_state_id "
                            "and combat.action_available == true",
                            "Study uses this creature's available Action on its actual turn.",
                        ),
                        _call("spend_srd_action", {"magic_action": False}),
                        _set("combat.action_taken", "'study'"),
                    ],
                ),
                {
                    "condition": "actor != null",
                    "on_true": [
                        _require(
                            "_actor_id == _actor_state_id",
                            "Study must bind the actual creature participant.",
                        ),
                        study_roll,
                    ],
                    "on_false": [
                        _require(
                            "actor_state.creature_kind != 'character' and "
                            "actor_state.skill_check_modifiers.investigation != null",
                            "An NPC needs its actual Investigation modifier; "
                            "a Player Character needs its participant.",
                        ),
                        npc_roll,
                    ],
                },
                _set(
                    "actor_state.last_maze_study",
                    {"roll": "_maze_roll", "total": "_maze_total", "outcome": "_maze_outcome"},
                ),
                _when(
                    "_maze_outcome == 'success'",
                    [
                        _call(
                            "finish_srd_maze_escape",
                            {
                                "caster_state": "{actor_state.travel_exile.caster}",
                                "target_state": "{_actor_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "exploration",
        "Maze Study",
        [("Outcome", "_maze_outcome"), ("Total", "_maze_total")],
    )
    finish = _tool_scene(
        "End the exact current Maze concentration after a successful actual escape check.",
        [_entity("caster_state"), _entity("target_state")],
        [
            _require(
                "target_state.travel_exile != null and "
                "target_state.travel_exile.spell == 'maze' and "
                "caster_state.concentration_sequence == target_state.travel_exile.sequence and "
                "caster_state.concentration_spell_id == 'maze'",
                "Maze escape must end its actual source.",
            ),
            _call(
                "end_srd_concentration_node",
                {"source_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
            ),
            _call("settle_srd_travel_exile_returns", {}),
        ],
    )
    return {
        **banishment_tools(tools),
        "validate_srd_travel_exile_cast": validate,
        "validate_srd_travel_exile_target": target_validate,
        "roll_srd_banishment_save": save,
        "apply_srd_travel_exile_target": apply,
        "end_srd_travel_exile_target": end,
        "refresh_srd_travel_exile_target": refresh_target,
        "refresh_srd_travel_exile_sources": refresh,
        "refresh_srd_travel_exile_indexed_target": indexed,
        "cast_srd_travel_exile": cast,
        "study_srd_maze_escape": study,
        "finish_srd_maze_escape": finish,
        "bind_srd_travel_exile_plane": _tool_scene(
            "Bind the source-created demiplane identity.",
            [_parameter("plane")],
            [_set("_exile_plane", "plane")],
        ),
        "mark_srd_travel_exile_return": _tool_scene(
            "Queue only the exact concentration recipient for outer return settlement.",
            [_entity("target_state"), _entity("caster_state"), _parameter("sequence", "integer")],
            [
                _when(
                    "target_state.travel_exile != null and "
                    "target_state.travel_exile.caster == _caster_state_id and "
                    "target_state.travel_exile.sequence == sequence and "
                    "target_state.travel_exile.return_pending != true",
                    [
                        _set(
                            "target_state.travel_exile.permanent_pending",
                            "target_state.travel_exile.spell == 'banishment' and "
                            "target_state.travel_exile.association != null and "
                            "state.lasting_tick >= target_state.travel_exile.expires_tick and "
                            "caster_state.is_alive == true and "
                            "'incapacitated' not in caster_state.conditions and "
                            "'paralyzed' not in caster_state.conditions and "
                            "'petrified' not in caster_state.conditions and "
                            "'stunned' not in caster_state.conditions and "
                            "'unconscious' not in caster_state.conditions",
                        ),
                        _set("target_state.travel_exile.return_pending", True),
                    ],
                )
            ],
        ),
        "settle_srd_travel_exile_returns": _tool_scene(
            "Settle queued exact returns after the outer concentration-ending owner completes.",
            [],
            [
                _when(
                    "state.travel_exile_targets != null",
                    [
                        _set("_exile_settlement_ids", "state.travel_exile_targets"),
                        _loop(
                            "_exile_settlement_ids",
                            "_exile_settlement_id",
                            [
                                _call(
                                    "settle_srd_travel_exile_target",
                                    {
                                        "target_state": "{_exile_settlement_id}",
                                    },
                                ),
                            ],
                        ),
                    ],
                )
            ],
        ),
        "settle_srd_travel_exile_target": _tool_scene(
            "Return an actually queued recipient without starting another lifetime transition.",
            [_entity("target_state")],
            [
                _when(
                    "target_state.travel_exile != null and "
                    "target_state.travel_exile.return_pending == true",
                    [
                        _call(
                            "end_srd_travel_exile_target", {"target_state": "{_target_state_id}"}
                        ),
                    ],
                )
            ],
        ),
    }


def bind_exile_lifecycle(tools, changed):
    """Queue exact returns at retirement and settle them outside the ending call graph."""
    ending = deepcopy(
        changed.get("end_srd_concentration_node", tools["end_srd_concentration_node"])
    )
    marker = "source_state.concentration_effect_family == 'travel_exile'"
    ending["effects"] = [e for e in ending["effects"] if e.get("condition") != marker]
    ending["effects"].insert(
        0,
        _when(
            marker,
            [
                _set("_exile_ending_targets", "source_state.travel_exile_targets"),
                _loop(
                    "_exile_ending_targets",
                    "_exile_ending_target",
                    [
                        _call(
                            "mark_srd_travel_exile_return",
                            {
                                "target_state": "{_exile_ending_target}",
                                "caster_state": "{_source_state_id}",
                                "sequence": "{source_state.concentration_sequence}",
                            },
                        ),
                    ],
                ),
                _set("source_state.travel_exile_targets", None),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = ending
    concentration = deepcopy(
        changed.get("refresh_srd_concentration_source", tools["refresh_srd_concentration_source"])
    )
    concentration_marker = "source_state.concentration_effect_family == 'travel_exile'"
    concentration["effects"] = [
        e for e in concentration["effects"] if e.get("condition") != concentration_marker
    ]
    invalidity = next(
        i
        for i, effect in enumerate(concentration["effects"])
        if effect.get("set", {}).get("field") == "_source_concentration_invalid"
    )
    concentration["effects"].insert(
        invalidity,
        _when(
            concentration_marker,
            [
                _set(
                    "_source_concentration_target_invalid",
                    "source_state.travel_exile_targets == null or "
                    "_target_state_id not in source_state.travel_exile_targets",
                ),
            ],
        ),
    )
    changed["refresh_srd_concentration_source"] = concentration
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [
        e for e in clock["effects"] if e.get("call") != "refresh_srd_travel_exile_sources"
    ]
    clock["effects"].append(_call("refresh_srd_travel_exile_sources", {}))
    changed["advance_srd_lasting_clock"] = clock
    for name in ("end_srd_concentration_voluntarily", "end_concentration", "cast_srd_travel_exile"):
        tool = deepcopy(changed.get(name, tools.get(name)))
        if tool is not None:
            tool["effects"] = [
                e for e in tool["effects"] if e.get("call") != "settle_srd_travel_exile_returns"
            ]
            tool["effects"].append(_call("settle_srd_travel_exile_returns", {}))
            changed[name] = tool
