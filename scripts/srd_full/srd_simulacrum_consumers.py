"""Attach Simulacrum's casting continuity and native recovery to actual package owners."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _when
    from .srd_scene_relations import MEASURE_EXPORTS, POSITION_EXPORTS, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _public, _replace_tools
else:
    from srd_control_behaviors import _when
    from srd_scene_relations import MEASURE_EXPORTS, POSITION_EXPORTS, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _public, _replace_tools

PUBLIC_TOOLS = {"begin_srd_simulacrum_repair"}


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])


def _declare_interrupted_continuation_receipt(tool, spell_name):
    """Capture existing channel facts before observation can commit its cleanup."""
    lists = list(_lists(tool["effects"]))
    selected = [
        effect["on_true"]
        for effects in lists
        for effect in effects
        if effect.get("condition") == "caster_state.extended_casting.spell == 'simulacrum'"
    ]
    interrupted = [
        effect["on_true"]
        for effects in lists
        for effect in effects
        if effect.get("condition") == "_sim_proximity_failed == true"
    ]
    assert len(selected) == len(interrupted) == 1, (
        "Expected the existing Simulacrum continuation branches"
    )
    before = selected[0]
    observe = next(
        i
        for i, effect in enumerate(before)
        if effect.get("call") == "observe_srd_simulacrum_channel"
    )
    captures = [
        _set("_continued_spell_label", repr(spell_name)),
        _set("_continued_cast_rounds", "caster_state.extended_casting.progress_rounds"),
        _set("_continued_cast_required_rounds", "caster_state.extended_casting.required_rounds"),
    ]
    if before[max(0, observe - len(captures)) : observe] != captures:
        before[observe:observe] = captures
    stage = _set("_continued_cast_stage", "'interrupted'")
    if interrupted[0][0] != stage:
        interrupted[0].insert(0, stage)


def _channel_tools():
    return {
        "observe_srd_simulacrum_channel": _tool_scene(
            "Retain a broken proximity receipt from the actual scene, even after moving back.",
            [_entity("caster_state")],
            [
                _set("_sim_channel_active", "caster_state.extended_casting.spell == 'simulacrum'"),
                _when(
                    "_sim_channel_active == true",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_caster_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _set("_sim_channel_caster", "_info_scene_entity"),
                        _call(
                            "srd_information_scene_entity",
                            {
                                "subject": "{caster_state.extended_casting.simulacrum_original}",
                            },
                            POSITION_EXPORTS,
                        ),
                        _set("_sim_channel_original", "_info_scene_entity"),
                        _call(
                            "measure_srd_information_distance",
                            {
                                "start": "{_sim_channel_caster.position}",
                                "end": "{_sim_channel_original.position}",
                            },
                            MEASURE_EXPORTS,
                        ),
                        _when(
                            "_scene_distance > 10 or _sim_channel_caster.metadata.plane != "
                            "_sim_channel_original.metadata.plane",
                            [
                                _set(
                                    "caster_state.extended_casting.simulacrum_proximity_broken",
                                    True,
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "observe_srd_simulacrum_channels": _tool_scene(
            "Observe actual completed movement/time without recursively ending Concentration.",
            [_optional("lasting_state", resolve="entity", default="state")],
            [
                _set("_sim_channels", []),
                _set("_sim_any_channel_active", False),
                _when(
                    "lasting_state.simulacrum_channel_caster_ids != null",
                    [
                        _set("_sim_channels", "lasting_state.simulacrum_channel_caster_ids"),
                    ],
                ),
                {
                    "for_each": {
                        "collection": "_sim_channels",
                        "as": "_sim_channel_caster_id",
                        "do": [
                            _call(
                                "observe_srd_simulacrum_channel",
                                {
                                    "caster_state": "{_sim_channel_caster_id}",
                                },
                                ["_sim_channel_active"],
                            ),
                            _when(
                                "_sim_channel_active == true",
                                [
                                    _set("_sim_any_channel_active", True),
                                ],
                            ),
                        ],
                    }
                },
                _set("lasting_state.has_simulacrum_channels", "_sim_any_channel_active"),
            ],
        ),
        "require_srd_simulacrum_channel": _tool_scene(
            "Finish only the same original retained by this uninterrupted twelve-hour casting.",
            [_entity("caster_state"), _entity("original_state")],
            [
                _call("observe_srd_simulacrum_channel", {"caster_state": "{_caster_state_id}"}),
                _require(
                    "caster_state.extended_casting.spell == 'simulacrum' and "
                    "caster_state.extended_casting.simulacrum_original == _original_state_id and "
                    "caster_state.extended_casting.simulacrum_proximity_broken != true",
                    "Simulacrum requires the same original within ten feet throughout casting.",
                ),
            ],
        ),
    }


def _repair_tools():
    """Spend admitted hundred-GP component units during the creator's actual Long Rest."""
    return {
        "check_srd_simulacrum_repair_distance": _tool_scene(
            "Measure the real creator and repaired body; never accept a proximity verdict.",
            [_entity("caster_state"), _entity("duplicate_state")],
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_sim_repair_caster", "_info_scene_entity"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_duplicate_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_sim_repair_body", "_info_scene_entity"),
                _call(
                    "measure_srd_information_distance",
                    {
                        "start": "{_sim_repair_caster.position}",
                        "end": "{_sim_repair_body.position}",
                    },
                    MEASURE_EXPORTS,
                ),
                _set(
                    "_sim_repair_near",
                    "_scene_distance <= 5 and "
                    "_sim_repair_caster.metadata.plane == _sim_repair_body.metadata.plane",
                ),
            ],
        ),
        "begin_srd_simulacrum_repair": _public(
            _tool_scene(
                "During the creator's Long Rest, spend actual 100-GP component units per "
                "chosen HP. "
                "The selected repair completes only with this successful rest and continuous "
                "five-foot proximity.",
                [
                    _entity("caster_state"),
                    _entity("duplicate_state"),
                    _parameter("hit_points", "integer", minimum=1, maximum=10000),
                    _parameter("material_id"),
                ],
                [
                    _call("require_srd_pending_window_closed", {}),
                    _call(
                        "read_srd_simulacrum_source",
                        {"target_state": "{_duplicate_state_id}"},
                        ["_sim_record", "_sim_active"],
                    ),
                    _require(
                        "_sim_active == true and _sim_record.caster == _caster_state_id",
                        "Only the actual creator can repair its living Simulacrum.",
                    ),
                    _require(
                        "caster_state.magic_jar_role == null",
                        "Occupied-body repair components need their physical stock owner.",
                    ),
                    _require(
                        "caster_state.rest_phase == 'active' and caster_state.rest_kind == 'long' "
                        "and caster_state.rest_activity == 'light' and "
                        "caster_state.simulacrum_repair == null",
                        "Begin one repair during the creator's actual Long Rest light activity.",
                    ),
                    _require(
                        "duplicate_state.hp + hit_points <= duplicate_state.max_hp",
                        "Select only the duplicate's actual missing Hit Points.",
                    ),
                    _call(
                        "check_srd_simulacrum_repair_distance",
                        {
                            "caster_state": "{_caster_state_id}",
                            "duplicate_state": "{_duplicate_state_id}",
                        },
                        ["_sim_repair_near"],
                    ),
                    _require(
                        "_sim_repair_near == true",
                        "The Simulacrum must be within five feet for repair.",
                    ),
                    _set("_sim_repair_materials", "caster_state.spell_materials"),
                    _set("_sim_repair_paid", False),
                    _set("_sim_repair_retained_materials", []),
                    {
                        "for_each": {
                            "collection": "_sim_repair_materials",
                            "as": "_sim_component",
                            "do": [
                                _when(
                                    "_sim_component.id == material_id",
                                    [
                                        _require(
                                            "_sim_component.kind == 'specific' and "
                                            "_sim_component.value_cp == 10000 "
                                            "and _sim_component.quantity >= hit_points and "
                                            "_sim_component.inventory_item in "
                                            "caster_state.inventory",
                                            "Repair uses actual carried components, each "
                                            "admitted unit worth 100 GP.",
                                        ),
                                        _require(
                                            "_sim_repair_paid != true",
                                            "The selected repair lot identity must be unique.",
                                        ),
                                        _set(
                                            "_sim_repair_component_patch",
                                            {"quantity": "_sim_component.quantity - hit_points"},
                                        ),
                                        _set(
                                            "_sim_component",
                                            "merge(_sim_component, _sim_repair_component_patch)",
                                        ),
                                        _set("_sim_repair_paid", True),
                                    ],
                                ),
                                {
                                    "list_push": {
                                        "list": "_sim_repair_retained_materials",
                                        "value": "_sim_component",
                                    }
                                },
                            ],
                        }
                    },
                    _require(
                        "_sim_repair_paid == true",
                        "Select the actual carried repair component lot.",
                    ),
                    _set("caster_state.spell_materials", "_sim_repair_retained_materials"),
                    _set(
                        "caster_state.simulacrum_repair",
                        {
                            "duplicate": "_duplicate_state_id",
                            "rest_serial": "caster_state.dream_rest_serial",
                            "amount": "hit_points",
                            "valid": True,
                        },
                    ),
                    _set("_sim_repair_casters", []),
                    _when(
                        "state.simulacrum_repair_caster_ids != null",
                        [
                            _set("_sim_repair_casters", "state.simulacrum_repair_caster_ids"),
                        ],
                    ),
                    _when(
                        "_caster_state_id not in _sim_repair_casters",
                        [
                            {
                                "list_push": {
                                    "list": "_sim_repair_casters",
                                    "value": "_caster_state_id",
                                }
                            },
                        ],
                    ),
                    _set("state.simulacrum_repair_caster_ids", "_sim_repair_casters"),
                    _set("state.has_simulacrum_repairs", True),
                ],
            ),
            "recovery",
            "Simulacrum repair begun",
            [("Creator", "caster_state.name")],
        ),
        "observe_srd_simulacrum_repair": _tool_scene(
            "Retain an exact interrupted repair receipt rather than inventing a repair success.",
            [_entity("caster_state")],
            [
                _set("_sim_repair_active", "caster_state.simulacrum_repair != null"),
                _when(
                    "_sim_repair_active == true",
                    [
                        _set("_sim_repair_near", False),
                        _call(
                            "read_srd_simulacrum_source",
                            {
                                "target_state": "{caster_state.simulacrum_repair.duplicate}",
                            },
                            ["_sim_active"],
                        ),
                        _when(
                            "_sim_active == true",
                            [
                                _call(
                                    "check_srd_simulacrum_repair_distance",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "duplicate_state": (
                                            "{caster_state.simulacrum_repair.duplicate}"
                                        ),
                                    },
                                    ["_sim_repair_near"],
                                ),
                            ],
                        ),
                        _when(
                            "_sim_repair_near != true or caster_state.rest_kind != 'long' or "
                            "caster_state.rest_phase == 'abandoned' or "
                            "caster_state.dream_rest_serial != "
                            "caster_state.simulacrum_repair.rest_serial",
                            [
                                _set("caster_state.simulacrum_repair.valid", False),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "observe_srd_simulacrum_repairs": _tool_scene(
            "Observe only retained repair episodes after actual movement or elapsed time.",
            [],
            [
                _set("_sim_repair_casters", []),
                _set("_sim_any_repair_active", False),
                _when(
                    "state.simulacrum_repair_caster_ids != null",
                    [
                        _set("_sim_repair_casters", "state.simulacrum_repair_caster_ids"),
                    ],
                ),
                {
                    "for_each": {
                        "collection": "_sim_repair_casters",
                        "as": "_sim_repair_caster_id",
                        "do": [
                            _call(
                                "observe_srd_simulacrum_repair",
                                {
                                    "caster_state": "{_sim_repair_caster_id}",
                                },
                                ["_sim_repair_active"],
                            ),
                            _when(
                                "_sim_repair_active == true",
                                [
                                    _set("_sim_any_repair_active", True),
                                ],
                            ),
                        ],
                    }
                },
                _set("state.has_simulacrum_repairs", "_sim_any_repair_active"),
            ],
        ),
        "finish_srd_simulacrum_repair": _tool_scene(
            "Only the actual completed creator Long Rest restores the paid repair HP.",
            [_entity("caster_state")],
            [
                _when(
                    "caster_state.simulacrum_repair != null",
                    [
                        _call(
                            "observe_srd_simulacrum_repair", {"caster_state": "{_caster_state_id}"}
                        ),
                        _when(
                            "caster_state.rest_phase == 'complete' and caster_state.rest_kind "
                            "== 'long' "
                            "and caster_state.simulacrum_repair.valid == true and "
                            "caster_state.dream_rest_serial == "
                            "caster_state.simulacrum_repair.rest_serial",
                            [
                                _call(
                                    "apply_srd_simulacrum_repair",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "duplicate_state": (
                                            "{caster_state.simulacrum_repair.duplicate}"
                                        ),
                                    },
                                ),
                            ],
                        ),
                        _set("caster_state.simulacrum_repair", None),
                        _call("observe_srd_simulacrum_repairs", {}),
                    ],
                )
            ],
        ),
        "apply_srd_simulacrum_repair": _tool_scene(
            "Repair only the actual paid source; independent HP recovery prohibitions still apply.",
            [_entity("caster_state"), _entity("duplicate_state")],
            [
                _call(
                    "read_srd_simulacrum_source",
                    {"target_state": "{_duplicate_state_id}"},
                    ["_sim_record", "_sim_active"],
                ),
                _when(
                    "_sim_active == true and _sim_record.caster == _caster_state_id and "
                    "duplicate_state.hit_point_recovery_blocked != true",
                    [
                        {
                            "heal": {
                                "target": "duplicate_state",
                                "amount": "caster_state.simulacrum_repair.amount",
                            }
                        },
                        _set(
                            "duplicate_state.bloodied",
                            "duplicate_state.hp <= duplicate_state.max_hp // 2",
                        ),
                    ],
                ),
            ],
        ),
    }


def _dispel_tool():
    return _tool_scene(
        "Dispel the chosen actual ongoing Simulacrum effect with a genuine source-level check.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_ability"),
            _optional("caster", resolve="participant", default=None),
            _optional("recovery_source_id", default=""),
        ],
        [
            _call(
                "read_srd_simulacrum_source",
                {"target_state": "{_target_state_id}"},
                ["_sim_record", "_sim_active"],
            ),
            _require(
                "_sim_active == true and _sim_record.dispel_ongoing == true and "
                "_sim_record.spell_level >= 7 and _sim_record.spell_level <= 9",
                "Select an actual ongoing Simulacrum source with its genuine casting level.",
            ),
            _require(
                "recovery_source_id == '' or recovery_source_id == _sim_record.cast_id",
                "The selected Simulacrum cast must match its actual retained body source.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_sim_dispel_caster", "_info_scene_entity"),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_sim_dispel_target", "_info_scene_entity"),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_sim_dispel_caster.position}",
                    "end": "{_sim_dispel_target.position}",
                },
                MEASURE_EXPORTS,
            ),
            _require(
                "_scene_distance <= 120 and _sim_dispel_caster.metadata.plane != null and "
                "_sim_dispel_caster.metadata.plane == _sim_dispel_target.metadata.plane",
                "The actual magical effect must be within Dispel Magic's range on this plane.",
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_sim_dispel_caster.position}",
                    "end": "{_sim_dispel_target.position}",
                    "plane": "{_sim_dispel_caster.metadata.plane}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true",
                "A solid barrier prevents targeting this actual effect.",
            ),
            _call(
                "resolve_srd_dispel_source_check",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_level": "{_sim_record.spell_level}",
                    "slot_level": "{slot_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                    "caster": "{_caster_id}",
                },
                ["_dispel_succeeded", "_dispel_roll"],
            ),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "end_srd_simulacrum_source",
                        {"target_state": "{_target_state_id}", "reason": "dispel"},
                    ),
                ],
            ),
            _set(
                "_spell_summary_rows",
                [{"label": "'Simulacrum dispelled'", "value": "_dispel_succeeded"}],
            ),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "'Actual Dispel roll'", "value": "_dispel_roll"},
                    {"label": "'Source spell level'", "value": "_sim_record.spell_level"},
                ],
            ),
        ],
    )


def bind_consumers(text):
    """Compose only source-qualified hooks; ordinary living damage/healing stay cheap."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = _channel_tools() | _repair_tools() | {"dispel_srd_simulacrum_source": _dispel_tool()}
    recovery = deepcopy(tools["cast_srd_recovery"])
    branch = "spell == 'dispel_magic' and recovery_choice == 'simulacrum'"
    if len(recovery["effects"]) == 1 and recovery["effects"][0].get("condition") == branch:
        recovery["effects"] = recovery["effects"][0]["on_false"]
    recovery["effects"] = [
        _when(
            branch,
            [
                _set("_revival_succeeded", False),
                _call(
                    "dispel_srd_simulacrum_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_state_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                        "caster": "{_caster_id}",
                        "recovery_source_id": "{recovery_source_id}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
            ],
            recovery["effects"],
        )
    ]
    changed["cast_srd_recovery"] = recovery
    begin = deepcopy(tools["begin_srd_spell_cast"])
    begin["effects"] = [
        e
        for e in begin["effects"]
        if e.get("condition")
        not in {
            "spell == 'simulacrum'",
            "spell == 'simulacrum' and caster_state.extended_casting.spell == 'simulacrum'",
        }
    ]
    begin["effects"].insert(
        0,
        _when(
            "spell == 'simulacrum'",
            [
                _require(
                    "target != null", "Retain Simulacrum's actual original from the casting start."
                ),
                _call("qualify_srd_simulacrum_original", {"original_state": "{_target_id}"}),
                _call(
                    "qualify_srd_simulacrum_geometry",
                    {
                        "caster_state": "{_caster_state_id}",
                        "original_state": "{_target_id}",
                        "pile": "",
                        "finishing": False,
                    },
                ),
            ],
        ),
    )
    begin["effects"].append(
        _when(
            "spell == 'simulacrum' and caster_state.extended_casting.spell == 'simulacrum'",
            [
                _set("caster_state.extended_casting.simulacrum_original", "_target_id"),
                _set("caster_state.extended_casting.simulacrum_proximity_broken", False),
                _set("_sim_channels", []),
                _when(
                    "lasting_state.simulacrum_channel_caster_ids != null",
                    [
                        _set("_sim_channels", "lasting_state.simulacrum_channel_caster_ids"),
                    ],
                ),
                _when(
                    "_caster_state_id not in _sim_channels",
                    [
                        {"list_push": {"list": "_sim_channels", "value": "_caster_state_id"}},
                    ],
                ),
                _set("lasting_state.simulacrum_channel_caster_ids", "_sim_channels"),
                _set("lasting_state.has_simulacrum_channels", True),
            ],
        )
    )
    changed["begin_srd_spell_cast"] = begin
    continue_cast = deepcopy(tools["continue_srd_spell_cast"])
    ordinary_continuation = continue_cast["effects"]
    if ordinary_continuation[-1].get("condition") == "_sim_proximity_failed == true":
        ordinary_continuation = ordinary_continuation[-1]["on_false"]
    continue_cast["effects"] = [
        _call("require_srd_pending_window_closed", {}),
        _set("_sim_proximity_failed", False),
        _when(
            "caster_state.extended_casting.spell == 'simulacrum'",
            [
                _call("observe_srd_simulacrum_channel", {"caster_state": "{_caster_state_id}"}),
                _set(
                    "_sim_proximity_failed",
                    "caster_state.extended_casting.simulacrum_proximity_broken == true",
                ),
            ],
        ),
        _when(
            "_sim_proximity_failed == true",
            [
                _call("interrupt_srd_extended_casting", {"caster_state": "{_caster_state_id}"}),
                _call("observe_srd_simulacrum_channels", {}),
            ],
            ordinary_continuation,
        ),
    ]
    _declare_interrupted_continuation_receipt(
        continue_cast,
        runtime["definitions"]["spell_casting"]["simulacrum"]["name"],
    )
    changed["continue_srd_spell_cast"] = continue_cast
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        tool["effects"] = [
            e for e in tool["effects"] if e.get("condition") != "spell == 'simulacrum'"
        ]
        tool["effects"].insert(
            0,
            _when(
                "spell == 'simulacrum'",
                [
                    _call(
                        "require_srd_simulacrum_channel",
                        {
                            "caster_state": "{_caster_state_id}",
                            "original_state": "{_target_id}",
                        },
                    ),
                ],
            ),
        )
        guard = "caster_state.created_spell_id != 'simulacrum' or spell != 'simulacrum'"
        if not any(p.get("condition") == guard for p in tool.get("pre_conditions", [])):
            tool.setdefault("pre_conditions", []).append(
                {
                    "condition": guard,
                    "error": "A Simulacrum cannot cast Simulacrum.",
                }
            )
        changed[name] = tool
    complete = changed.get("complete_srd_rest", deepcopy(tools["complete_srd_rest"]))
    complete["effects"] = [
        e
        for e in complete["effects"]
        if e.get("condition")
        != "target_state.simulacrum_repair != null and _dream_rest_denied != true"
    ]
    complete["effects"].append(
        _when(
            "target_state.simulacrum_repair != null and _dream_rest_denied != true",
            [
                _call("finish_srd_simulacrum_repair", {"caster_state": "{_target_state_id}"}),
            ],
        )
    )
    changed["complete_srd_rest"] = complete
    guard = "caster_state.created_spell_id != 'simulacrum' or spell != 'simulacrum'"
    if not any(p.get("condition") == guard for p in begin.get("pre_conditions", [])):
        begin.setdefault("pre_conditions", []).append(
            {
                "condition": guard,
                "error": "A Simulacrum cannot cast Simulacrum.",
            }
        )
    for name in (
        "begin_srd_rest",
        "complete_srd_rest",
        "recover_srd_rest_resources",
        "take_srd_short_rest_hit_die",
    ):
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        guard = "target_state.created_spell_id != 'simulacrum'"
        if not any(p.get("condition") == guard for p in tool.get("pre_conditions", [])):
            tool.setdefault("pre_conditions", []).append(
                {
                    "condition": guard,
                    "error": "A Simulacrum cannot take a Short or Long Rest.",
                }
            )
        changed[name] = tool
    healing = deepcopy(tools["restore_hit_points"])
    for effects in _lists(healing["effects"]):
        for effect in effects:
            if effect.get("condition") == "target.hit_point_recovery_blocked != true":
                effect["condition"] += " and target.created_spell_id != 'simulacrum'"
    changed["restore_hit_points"] = healing
    for name in ("move_srd_scene_creature", "move_srd_travel_subject", "advance_srd_lasting_time"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        hook = "lasting_state.has_simulacrum_channels == true"
        tool["effects"] = [e for e in tool["effects"] if e.get("condition") != hook]
        tool["effects"].append(_when(hook, [_call("observe_srd_simulacrum_channels", {})]))
        # State must be an actual resolved binding at borrowed owner boundaries.
        if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
            tool["parameters"].append(
                _optional("lasting_state", resolve="entity", default="state", model_hidden=True)
            )
        hook_effect = tool["effects"][-1]
        hook_effect["condition"] = "lasting_state.has_simulacrum_channels == true"
        repair_hook = "lasting_state.has_simulacrum_repairs == true"
        tool["effects"] = [e for e in tool["effects"] if e.get("condition") != repair_hook]
        tool["effects"].append(_when(repair_hook, [_call("observe_srd_simulacrum_repairs", {})]))
        changed[name] = tool
    return bind_death_consumers(_replace_tools(text, changed))


# Qualified facade entries retain their exact forwarding/render/drain effects.
# All source retirement belongs on the actual post-prevention receiving branch.
_PACKET_CORES = {
    "apply_srd_qualifying_damage": "execute_srd_qualified_scalar_packet",
    "apply_srd_qualifying_composite_damage": "execute_srd_qualified_composite_packet",
    "apply_srd_prismatic_damage": "execute_srd_qualified_scalar_packet",
    "apply_srd_prismatic_composite_damage": "execute_srd_qualified_composite_packet",
    "apply_srd_dream_wake_damage": "execute_srd_qualified_scalar_packet",
}
_SIM_DEATH_GUARDS = {
    "target.created_spell_id == 'simulacrum'",
    "target.hp == 0 and target.created_spell_id == 'simulacrum'",
}
_MONSTER_ZERO = "target.hp == 0 and target.creature_kind == 'monster'"


def _is_packet_delegate(tool, core, name):
    """Accept only the actual forwarding facade, including its optional final Dream drain."""
    effects = tool["effects"]
    if not any(effect.get("call") == core for effect in effects):
        return False
    expected = {
        "call": core,
        "params": {
            **{
                p["name"]: "{_" + p["name"] + "_id}"
                if p.get("resolve") in {"entity", "participant"}
                else "{" + p["name"] + "}"
                for p in tool["parameters"]
            },
            "render_damage_snapshot": "prismatic" not in name,
        },
        "export_context": "*",
    }
    drain = {
        "condition": "lasting_state.has_dream_spells == true",
        "on_true": [{"call": "drain_srd_dream_wakes", "params": {}}],
    }
    if effects != [expected] and (
        name == "apply_srd_dream_wake_damage" or effects != [expected, drain]
    ):
        raise ValueError("Simulacrum cannot discard a changed qualified packet facade.")
    return True


def bind_death_consumers(text):
    """Retire at each genuine native zero-HP owner, never by expanding a facade."""
    tools = _load_runtime(text)["tools"]
    names = {"apply_srd_damage", "apply_srd_composite_damage", *_PACKET_CORES}
    names.update(core for core in _PACKET_CORES.values() if core in tools)
    changed = {}
    for name in sorted(names):
        if name not in tools:
            continue
        original = tools[name]
        expected_core = _PACKET_CORES.get(name)
        if expected_core is not None and _is_packet_delegate(original, expected_core, name):
            if expected_core not in tools:
                raise ValueError("Simulacrum requires its actual delegated packet implementation.")
            continue
        packet = deepcopy(original)
        for effects in _lists(packet["effects"]):
            effects[:] = [e for e in effects if e.get("condition") not in _SIM_DEATH_GUARDS]
        zero = [
            e
            for effects in _lists(packet["effects"])
            for e in effects
            if e.get("condition") == _MONSTER_ZERO
        ]
        if len(zero) != 1:
            raise ValueError("Simulacrum requires one genuine native monster zero-HP receiver.")
        if not any(
            e.get("call") == "record_srd_actual_death"
            for effects in _lists(zero[0]["on_true"])
            for e in effects
        ):
            raise ValueError(
                "Simulacrum requires the actual post-prevention native death receiver."
            )
        zero[0]["on_true"].append(
            _when(
                "target.hp == 0 and target.created_spell_id == 'simulacrum'",
                [
                    _call(
                        "end_srd_simulacrum_source",
                        {
                            "target_state": "{target}",
                            "reason": "zero_hit_points",
                        },
                    ),
                ],
            )
        )
        if packet != original:
            changed[name] = packet
    return _replace_tools(text, changed) if changed else text
