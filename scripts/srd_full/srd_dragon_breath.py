"""Dragon's Breath retains the paid caster's choices and uses the recipient's real Cone action."""

from copy import deepcopy

if __package__:
    from .srd_retained_spell_attacks import _install_tool, _lists
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _replace_tools
else:
    from srd_retained_spell_attacks import _install_tool, _lists
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _replace_tools


PUBLIC_TOOLS = {"use_srd_dragon_breath"}
PROFILE = {
    "family": "dragon_breath",
    "casting_time": "bonus_action",
    "base_level": 2,
    "range_kind": "touch",
    "range_feet": 0,
    "concentration": True,
    "duration_rounds": 10,
    "requires_willing": True,
    "base_dice": 3,
    "damage_die": "d6",
    "save_ability": "dexterity",
    "success_damage": "half",
    "area_shape": "cone",
    "area_size_feet": 15,
}
TYPES = ["acid", "cold", "fire", "lightning", "poison"]
SOURCE_EXPORTS = ("_breath_source", "_breath_source_id", "_breath_dice_count")


def schema():
    """The admitted definition is the fixed printed source, not a generic damage profile."""
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "string",
                "enum": [value],
            }
            for key, value in PROFILE.items()
            if key != "family"
        },
        "required": [key for key in PROFILE if key != "family"],
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "dragon_breath" and (
            spell != "dragons_breath" or definition != PROFILE
        ):
            raise ValueError("Dragon's Breath differs from the pinned source profile.")


def _push(field, value):
    return {"list_push": {"list": field, "value": value}}


def _rename(value):
    if isinstance(value, str):
        return (
            value.replace("vampiric_touch", "dragons_breath")
            .replace("retained_spell_attack", "dragon_breath")
            .replace("has_dragon_breaths", "has_dragon_breath")
            .replace("retained_attack_source_id", "dragon_breath_source_id")
            .replace("_retained_", "_breath_")
            .replace("Vampiric Touch", "Dragon's Breath")
        )
    if isinstance(value, list):
        return [_rename(child) for child in value]
    if isinstance(value, dict):
        return {key: _rename(child) for key, child in value.items()}
    return value


def _installer():
    """Reuse the existing paid-source installation and exact concentration identity."""
    tool = _rename(_install_tool())
    next(p for p in tool["parameters"] if p["name"] == "slot_level")["minimum"] = 2
    tool["parameters"] += [
        _entity("target"),
        _parameter("save_dc", "integer", minimum=1, maximum=40),
        _parameter("damage_type", "enum", enum_values=TYPES),
    ]
    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            if effect.get("set", {}).get("field") == "caster_state.concentration_target_state_id":
                effect["set"]["value"] = "_target_id"
            if (
                effect.get("list_push", {}).get("list")
                == "caster_state.concentration_target_state_ids"
            ):
                effect["list_push"]["value"] = "_target_id"
            row = effect.get("list_push", {}).get("value")
            if isinstance(row, dict) and row.get("kind") == "dragon_breath":
                row.update(target="_target_id", save_dc="save_dc", damage_type="damage_type")
    return tool


def _record_tool():
    return _tool(
        "Read this actual Dragon's Breath record's caster, concentration, clock and suppression.",
        [_entity("caster_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _set("_breath_live", False),
            _set("_breath_active", False),
            _when(
                "record.kind == 'dragon_breath' and record.spell == 'dragons_breath' "
                "and record.active == true and record.caster == _caster_state_id and "
                "state.lasting_tick != null and state.lasting_tick < record.expires_tick and "
                "caster_state.concentration_spell_id == 'dragons_breath' and "
                "caster_state.concentration_effect_family == 'dragon_breath'",
                [
                    _when(
                        "caster_state.concentration_cast_id == record.source_id and "
                        "caster_state.concentration_started_tick == record.created_tick and "
                        "caster_state.dragon_breath_source_id == record.source_id",
                        [
                            _when(
                                "caster_state.is_alive == true and caster_state.hp > 0 and not "
                                "('incapacitated' in caster_state.conditions or "
                                "'paralyzed' in caster_state.conditions or "
                                "'petrified' in caster_state.conditions or "
                                "'stunned' in caster_state.conditions or "
                                "'unconscious' in caster_state.conditions)",
                                [_set("_breath_live", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_breath_live == true and record.suppressed != true", [_set("_breath_active", True)]
            ),
        ],
    )


def _prepare_tool():
    """Combining the same spell selects the highest original slot, then newest live source."""
    return _tool(
        "Derive the recipient's strongest exact active Dragon's Breath source.",
        [_entity("breather_state")],
        [
            _require(
                "breather_state.is_alive == true and breather_state.hp > 0",
                "Dragon's Breath needs its living recipient.",
            ),
            _set("_breath_source", None),
            _set("_breath_records", []),
            _when(
                "state.has_dragon_breath == true and state.turn_effects != null",
                [_set("_breath_records", "state.turn_effects")],
            ),
            _loop(
                "_breath_records",
                "_breath_candidate",
                [
                    _when(
                        "_breath_candidate.kind == 'dragon_breath' and "
                        "_breath_candidate.target == _breather_state_id",
                        [
                            _call(
                                "check_srd_dragon_breath_record",
                                {
                                    "caster_state": "{_breath_candidate.caster}",
                                    "record": "{_breath_candidate}",
                                },
                                ["_breath_active"],
                            ),
                            _when(
                                "_breath_active == true",
                                [
                                    _when(
                                        "_breath_source == null",
                                        [_set("_breath_source", "_breath_candidate")],
                                    ),
                                    _when(
                                        "_breath_candidate.slot_level > "
                                        "_breath_source.slot_level or "
                                        "(_breath_candidate.slot_level == "
                                        "_breath_source.slot_level and "
                                        "_breath_candidate.created_tick >= "
                                        "_breath_source.created_tick)",
                                        [_set("_breath_source", "_breath_candidate")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "_breath_source != null", "No active paid Dragon's Breath source is available."
            ),
            _set("_breath_source_id", "_breath_source.source_id"),
            _set("_breath_dice_count", "_breath_source.slot_level + 1"),
        ],
    )


def _refresh_tool():
    return _tool(
        "Retire only dead Dragon's Breath sources and derive their existing-record index.",
        [],
        [
            _set("_breath_records", []),
            _set("_breath_kept", []),
            _set("_breath_any", False),
            _when("state.turn_effects != null", [_set("_breath_records", "state.turn_effects")]),
            _loop(
                "_breath_records",
                "_breath_record",
                [
                    {
                        "condition": "_breath_record.kind == 'dragon_breath'",
                        "on_true": [
                            _call(
                                "check_srd_dragon_breath_record",
                                {
                                    "caster_state": "{_breath_record.caster}",
                                    "record": "{_breath_record}",
                                },
                                ["_breath_live"],
                            ),
                            _when(
                                "_breath_live == true",
                                [
                                    _push("_breath_kept", "_breath_record"),
                                    _set("_breath_any", True),
                                ],
                            ),
                        ],
                        "on_false": [_push("_breath_kept", "_breath_record")],
                    }
                ],
            ),
            _set("state.turn_effects", "_breath_kept"),
            _set("state.has_dragon_breath", "_breath_any"),
        ],
    )


def _retire_tool():
    return _tool(
        "Remove one exact Dragon's Breath row without altering independent spell sources.",
        [_parameter("source_id")],
        [
            _set("_breath_kept", []),
            _set("_breath_records", []),
            _when("state.turn_effects != null", [_set("_breath_records", "state.turn_effects")]),
            _loop(
                "_breath_records",
                "_breath_record",
                [
                    _when(
                        "_breath_record.kind != 'dragon_breath' or "
                        "_breath_record.source_id != source_id",
                        [_push("_breath_kept", "_breath_record")],
                    ),
                ],
            ),
            _set("state.turn_effects", "_breath_kept"),
            _call("refresh_srd_dragon_breath", {}),
        ],
    )


def _finish_removal():
    return _tool(
        "End concentration only for this exact removed Dragon's Breath instance.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _when(
                "caster_state.concentration_effect_family == 'dragon_breath' and "
                "caster_state.concentration_cast_id == source_id",
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
            _call("retire_srd_dragon_breath_source", {"source_id": "{source_id}"}),
        ],
    )


def _cast_tool():
    return _tool(
        "Install Dragon's Breath on one willing creature from the successful paid Bonus Action.",
        [
            _entity("caster_state"),
            _entity("target"),
            _parameter("target_willing", "boolean"),
            _parameter("slot_level", "integer", minimum=2, maximum=9),
            _parameter("save_dc", "integer", minimum=1, maximum=40),
            _parameter("damage_type", "enum", enum_values=TYPES),
            _parameter(
                "casting_ability", "enum", enum_values=["intelligence", "wisdom", "charisma"]
            ),
            _parameter("concentration_save_modifier", "integer"),
            _optional("spell_cast_id", default="", model_hidden=True),
        ],
        [
            _require(
                "target.is_alive == true and target.creature_type != 'object' and "
                "target.creature_type != 'spell_force' and target_willing == true",
                "Dragon's Breath needs one actual willing creature.",
            ),
            _when(
                "_caster_state_id != _target_id",
                [
                    _call(
                        "resolve_srd_scene_geometry_relation",
                        {"actor_state": "{_caster_state_id}", "target_state": "{_target_id}"},
                        ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
                    ),
                    _set("_breath_touch_reach", 5),
                    _when(
                        "caster_state.melee_reach != null",
                        [_set("_breath_touch_reach", "caster_state.melee_reach")],
                    ),
                    _require(
                        "_breath_touch_reach > 0 and _scene_distance <= _breath_touch_reach and "
                        "_scene_same_plane == true and _scene_line_of_sight == true",
                        "Dragon's Breath needs same-plane contact within the caster's reach.",
                    ),
                ],
            ),
            _call(
                "install_srd_dragon_breath",
                {
                    "caster_state": "{_caster_state_id}",
                    "target": "{_target_id}",
                    "slot_level": "{slot_level}",
                    "save_dc": "{save_dc}",
                    "damage_type": "{damage_type}",
                    "casting_ability": "{casting_ability}",
                    "concentration_save_modifier": "{concentration_save_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                },
                ["_breath_source_id"],
            ),
            _call(
                "record_srd_spell_area_target_result",
                {
                    "target": "{_target_id}",
                    "brief": "Dragon's Breath: {damage_type}",
                    "detail": "Dragon's Breath source {_breath_source_id}; "
                    "original slot {slot_level}, "
                    "save DC {save_dc}, chosen {damage_type}.",
                },
                ["_spell_summary_rows", "_spell_detail_rows"],
            ),
        ],
    )


def _use_tool():
    parameters = [_entity("breather_state")]
    parameters += [
        _parameter(
            name,
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        )
        for name in ("breath_apex", "breath_direction")
    ]
    parameters.append(_optional("breath_include_origin", "boolean", False))
    effects = [
        _call(
            "prepare_srd_dragon_breath_source",
            {"breather_state": "{_breather_state_id}"},
            SOURCE_EXPORTS,
        ),
        _call(
            "collect_srd_cone_creature_targets",
            {
                "source_state": "{_breather_state_id}",
                "length_feet": 15,
                "apex": "{breath_apex}",
                "direction": "{breath_direction}",
                "include_origin": "{breath_include_origin}",
            },
            ["_srd_cone_targets", "_srd_cone_origin", "_srd_cone_axis", "_srd_cone_source"],
        ),
        _set("_breath_frames", ["_srd_cone_source"]),
        _call(
            "collect_srd_spell_area_cover_targets",
            {
                "source_state": "{_breather_state_id}",
                "targets": "{_srd_cone_targets}",
                "origin": "{_srd_cone_origin}",
                "axis": "{_srd_cone_axis}",
                "source_frames": "{_breath_frames}",
            },
            ["_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"],
        ),
        _require("len(_srd_area_targets) <= 64", "Resolve at most 64 affected creatures."),
        _when(
            "'charmed' in breather_state.conditions",
            [
                _call(
                    "validate_srd_spell_charmer_targets",
                    {"caster_state": "{_breather_state_id}", "targets": "{_srd_area_targets}"},
                ),
            ],
        ),
        _when(
            "combat.encounter_active != true",
            [
                _require(
                    "breather_state.dragon_breath_used_tick == null or "
                    "state.lasting_tick >= breather_state.dragon_breath_used_tick + 2",
                    "Advance the real exploration clock before another Magic action.",
                ),
            ],
        ),
        _call(
            "spend_spell_casting_budget",
            {
                "caster_state": "{_breather_state_id}",
                "casting_time": "action",
                "slot_resource": "no_slot",
            },
        ),
        {"dice": {"expression": "{_breath_dice_count}d6"}, "store_as": "_breath_damage_roll"},
        _set("_breath_damage_dice", "_breath_damage_roll.results"),
        _set("_breath_raw_damage", "_breath_damage_roll.total"),
        _set("_breath_damage_total", "max(0, _breath_raw_damage)"),
        _when(
            "breather_state.has_actor_spell_modifiers == true",
            [
                _call(
                    "modify_srd_actor_damage_roll",
                    {
                        "actor_state": "{_breather_state_id}",
                        "amount": "{_breath_damage_total}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ["_actor_modified_damage"],
                ),
                _set("_breath_damage_total", "_actor_modified_damage"),
            ],
        ),
        _set("_breath_summary_rows", []),
        _set("_breath_detail_rows", []),
        _loop(
            "_srd_area_targets",
            "_breath_target",
            [
                _call(
                    "resolve_spell_area_save_damage_target",
                    {
                        "spell": "dragons_breath",
                        "target": "{_breath_target}",
                        "save_ability": "dexterity",
                        "spell_save_dc": "{_breath_source.save_dc}",
                        "damage_total": "{_breath_damage_total}",
                        "damage_type": "{_breath_source.damage_type}",
                        "success_damage": "half",
                        "construct_save_disadvantage": False,
                        "half_cover_targets": "{_srd_area_half_cover}",
                        "three_quarters_cover_targets": "{_srd_area_three_quarters}",
                        "source_state_id": "{_breather_state_id}",
                        "spell_cast_id": "{_breath_source_id}",
                        "actual_spell_level": "{_breath_source.slot_level}",
                        "spell_cast_owner": "{_breath_source.caster}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
                _loop(
                    "_spell_summary_rows",
                    "_breath_row",
                    [_push("_breath_summary_rows", "_breath_row")],
                ),
                _loop(
                    "_spell_detail_rows",
                    "_breath_row",
                    [_push("_breath_detail_rows", "_breath_row")],
                ),
            ],
        ),
        _set("breather_state.dragon_breath_used_tick", "state.lasting_tick"),
        _set("_breath_damage_type", "_breath_source.damage_type"),
        _set("_breath_save_dc", "_breath_source.save_dc"),
    ]
    tool = _public(
        _tool(
            "Use the recipient's actual retained Dragon's Breath Magic action.", parameters, effects
        ),
        "combat",
        "Dragon's Breath",
        [
            ("Source", "_breath_source_id"),
            ("Chosen type", "_breath_damage_type"),
            ("Original save DC", "_breath_save_dc"),
            ("Damage dice", "_breath_damage_dice"),
            ("Raw amount", "_breath_raw_damage"),
            ("After source penalty", "_breath_damage_total"),
        ],
    )
    tool["metadata"]["result_card"]["details"] = {"path": "context._breath_detail_rows"}
    return tool


def extend_runtime(text):
    """Emit only this source and genuine retained Magic action using settled area owners."""
    tools = _load_runtime(text)["tools"]
    required = {
        "collect_srd_cone_creature_targets",
        "collect_srd_spell_area_cover_targets",
        "resolve_spell_area_save_damage_target",
        "modify_srd_actor_damage_roll",
    }
    if missing := required - tools.keys():
        raise ValueError("Dragon's Breath needs final owning tools: " + ", ".join(sorted(missing)))
    changed = {
        "install_srd_dragon_breath": _installer(),
        "cast_srd_dragon_breath": _cast_tool(),
        "check_srd_dragon_breath_record": _record_tool(),
        "prepare_srd_dragon_breath_source": _prepare_tool(),
        "refresh_srd_dragon_breath": _refresh_tool(),
        "retire_srd_dragon_breath_source": _retire_tool(),
        "finish_srd_dragon_breath_removal": _finish_removal(),
        "use_srd_dragon_breath": _use_tool(),
    }
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )


def bind_runtime_consumers(text):
    """Join successful paid dispatch and existing clock/concentration/removal boundaries."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        calls = [effect.get("call") for sequence in _lists(tool["effects"]) for effect in sequence]
        if "cast_srd_dragon_breath" in calls or "cast_spell" in calls:
            continue
        anchors = [
            i
            for i, effect in enumerate(tool["effects"])
            if effect.get("set", {}).get("field") == "_card_slots_after"
        ]
        if len(anchors) != 1:
            raise ValueError("Dragon's Breath needs the successful paid cast-card boundary.")
        tool["effects"].insert(
            anchors[0],
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'dragon_breath'",
                [
                    _call(
                        "cast_srd_dragon_breath",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target": "{_target_id}",
                            "target_willing": "{target_willing}",
                            "slot_level": "{_slot_level}",
                            "save_dc": "{_casting_profile.save_dc}",
                            "damage_type": "{selected_damage_type}",
                            "casting_ability": "{_casting_profile.ability}",
                            "concentration_save_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                            "spell_cast_id": "{_spell_source_cast_id}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows", "_breath_source_id"],
                    ),
                ],
            ),
        )
        changed[name] = tool
    for name in ("advance_srd_lasting_clock", "process_srd_turn_effects"):
        tool = deepcopy(tools[name])
        if "refresh_srd_dragon_breath" in str(tool["effects"]):
            continue
        guard = _when(
            "lasting_state.has_dragon_breath == true",
            [
                _call("refresh_srd_dragon_breath", {"lasting_state": "{_lasting_state_id}"}),
            ],
        )
        if name == "advance_srd_lasting_clock":
            anchors = [
                i
                for i, effect in enumerate(tool["effects"])
                if effect.get("set", {}).get("field") == "lasting_state.lasting_tick"
            ]
            if len(anchors) != 1:
                raise ValueError("Dragon's Breath needs the actual clock assignment.")
            tool["effects"].insert(anchors[0] + 1, guard)
        else:
            tool["effects"].append(guard)
        changed[name] = tool
    name = "end_srd_concentration_node"
    tool = deepcopy(tools[name])
    if "retire_srd_dragon_breath_source" not in str(tool["effects"]):
        tool["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'dragon_breath' and "
                "source_state.dragon_breath_source_id != null",
                [
                    _call(
                        "retire_srd_dragon_breath_source",
                        {
                            "source_id": "{source_state.dragon_breath_source_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        )
        changed[name] = tool
    name = "refresh_srd_removed_spell_target"
    tool = deepcopy(tools[name])
    if "finish_srd_dragon_breath_removal" not in str(tool["effects"]):
        tool["effects"].append(
            _when(
                "lasting_state.has_dragon_breath == true",
                [
                    _loop(
                        "removed_effects",
                        "_breath_removed",
                        [
                            _when(
                                "_breath_removed.kind == 'dragon_breath' and "
                                "_breath_removed.target == _target_state_id",
                                [
                                    _call(
                                        "finish_srd_dragon_breath_removal",
                                        {
                                            "caster_state": "{_breath_removed.caster}",
                                            "source_id": "{_breath_removed.source_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _call("refresh_srd_dragon_breath", {"lasting_state": "{_lasting_state_id}"}),
                ],
            )
        )
        changed[name] = tool
    return _replace_tools(text, changed) if changed else text
