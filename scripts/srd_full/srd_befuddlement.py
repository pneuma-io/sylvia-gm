"""Befuddlement's persistent rider shares saved conditions and the actual lasting clock."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call as _shared_call
    from .srd_control_behaviors import (
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _when,
    )
else:
    from srd_control_behaviors import _call as _shared_call
    from srd_control_behaviors import (
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _when,
    )


REPEAT_TICKS = 864000  # Thirty days at the package's three-second lasting tick.


def _call(name, params, exports=()):
    """Keep explicit export renaming at this leaf's saved save and immunity boundaries."""
    call = _shared_call(name, params, exports)
    if isinstance(exports, dict):
        call["export_context"] = exports
    return call


def _mind_blank():
    # The engine category is an inference from the printed effect on the target's mind.
    return [
        _set("_befuddlement_immune", False),
        _when(
            "target_state.has_timed_actor_defenses == true",
            [
                _call(
                    "check_srd_mind_blank_information",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "query_kind": "mind_control",
                    },
                    {"_befuddlement_immune": "_mind_blank_information_blocked"},
                )
            ],
        ),
    ]


def _refresh():
    return _call(
        "refresh_srd_timed_condition_target",
        {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
    )


def _tools():
    tools = {}
    tools["resolve_srd_befuddlement_projection"] = _tool(
        "Retain each Befuddlement source while projecting the effective casting restriction.",
        [_entity("target_state"), _lasting()],
        [
            _set("_befuddlement_owned", []),
            _set("_befuddlement_index", []),
            *_mind_blank(),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_befuddlement_index", "target_state.timed_condition_source_ids")],
            ),
            _each(
                "_befuddlement_index",
                "_befuddlement_id",
                [
                    _set(
                        "_befuddlement_source",
                        "lasting_state.timed_condition_sources[_befuddlement_id]",
                    ),
                    _when(
                        "_befuddlement_source != null and _befuddlement_source.installed == true "
                        "and _befuddlement_source.spell_id == 'befuddlement'",
                        [
                            _push("_befuddlement_owned", "_befuddlement_id"),
                            _set(
                                "lasting_state.timed_condition_sources.{_befuddlement_id}.suppressed",
                                "_befuddlement_immune",
                            ),
                        ],
                    ),
                ],
            ),
            _set("target_state.befuddlement_source_ids", "_befuddlement_owned"),
            _set("target_state.has_befuddlement_sources", "len(_befuddlement_owned) > 0"),
            _set(
                "target_state.befuddled",
                "len(_befuddlement_owned) > 0 and _befuddlement_immune != true",
            ),
        ],
    )
    tools["install_srd_befuddlement_source"] = _tool(
        "Install the actual failed-save rider with its original DC and thirty-day deadline.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("save_dc", "integer", minimum=1, maximum=40),
            _param("actual_spell_level", "integer", minimum=8, maximum=9),
        ],
        [
            *_mind_blank(),
            _set("_befuddlement_installed", False),
            _set("_befuddlement_note", "'Mind Blank prevents the persistent effect.'"),
            _when(
                "_befuddlement_immune != true",
                [
                    _set("target_state.has_befuddlement_sources", True),
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "spell": "befuddlement",
                            "condition": "none",
                            "control_profile": "befuddlement",
                            "duration_rounds": 0,
                            "repeat_save_timing": "none",
                            "save_ability": "intelligence",
                            "save_dc": "{save_dc}",
                            "actual_spell_level": "{actual_spell_level}",
                        },
                        ["_timed_condition_source_id"],
                    ),
                    _set(
                        "lasting_state.timed_condition_sources.{_timed_condition_source_id}.next_repeat_tick",
                        f"lasting_state.lasting_tick + {REPEAT_TICKS}",
                    ),
                    _set(
                        "lasting_state.timed_condition_sources.{_timed_condition_source_id}.last_repeat_save",
                        None,
                    ),
                    _set(
                        "lasting_state.timed_condition_sources.{_timed_condition_source_id}.dispel_ongoing",
                        False,
                    ),
                    _set("_befuddlement_targets", []),
                    _when(
                        "lasting_state.befuddlement_target_ids != null",
                        [_set("_befuddlement_targets", "lasting_state.befuddlement_target_ids")],
                    ),
                    _when(
                        "_target_state_id not in _befuddlement_targets",
                        [_push("_befuddlement_targets", "_target_state_id")],
                    ),
                    _set("lasting_state.befuddlement_target_ids", "_befuddlement_targets"),
                    _set("lasting_state.has_befuddlement_sources", True),
                    _set("_befuddlement_installed", True),
                    _set(
                        "_befuddlement_note",
                        "'Cannot cast spells or take the Magic action; next Intelligence save "
                        "in 30 days.'",
                    ),
                ],
            ),
        ],
    )
    tools["clear_srd_befuddlement"] = _tool(
        "End one selected Befuddlement source for Greater Restoration or all for Heal.",
        [
            _entity("target_state"),
            _lasting(),
            _param("source_id", required=False, default=""),
            _param("all_sources", "boolean", required=False, default=False),
        ],
        [
            _call(
                "resolve_srd_befuddlement_projection",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _set("_befuddlement_ids", "target_state.befuddlement_source_ids"),
            _require(
                "source_id == '' or source_id in _befuddlement_ids",
                "Select an installed Befuddlement source on this creature.",
            ),
            _require(
                "all_sources == true or len(_befuddlement_ids) <= 1 or source_id != ''",
                "Choose the one Befuddlement source that Greater Restoration ends.",
            ),
            _set("_befuddlement_selected", "source_id"),
            _when(
                "source_id == '' and len(_befuddlement_ids) == 1",
                [_set("_befuddlement_selected", "_befuddlement_ids[0]")],
            ),
            _each(
                "_befuddlement_ids",
                "_befuddlement_id",
                [
                    _when(
                        "all_sources == true or _befuddlement_id == _befuddlement_selected",
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "lasting_state": "{_lasting_state_id}",
                                    "source_id": "{_befuddlement_id}",
                                },
                            )
                        ],
                    )
                ],
            ),
            _refresh(),
        ],
    )
    tools["resolve_srd_befuddlement_deadline"] = _tool(
        "Settle each source's due Intelligence save once at the actual thirty-day clock boundary.",
        [_entity("target_state"), _lasting()],
        [
            _call(
                "resolve_srd_befuddlement_projection",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _set("_befuddlement_due_ids", "target_state.befuddlement_source_ids"),
            _each(
                "_befuddlement_due_ids",
                "_befuddlement_due_id",
                [
                    _set(
                        "_befuddlement_due_source",
                        "lasting_state.timed_condition_sources[_befuddlement_due_id]",
                    ),
                    _when(
                        "_befuddlement_due_source.installed == true and "
                        "lasting_state.lasting_tick >= _befuddlement_due_source.next_repeat_tick",
                        [
                            # Actual clock steps are shorter than this save interval.
                            _set(
                                "lasting_state.timed_condition_sources.{_befuddlement_due_id}.next_repeat_tick",
                                f"_befuddlement_due_source.next_repeat_tick + {REPEAT_TICKS}",
                            ),
                            _when(
                                "target_state.is_alive == true",
                                [
                                    _require(
                                        "target_state.saving_throw_modifiers.intelligence != null",
                                        "Befuddlement's repeated save needs the creature's actual "
                                        "Intelligence modifier.",
                                    ),
                                    _call(
                                        "d20_test",
                                        {
                                            "actor_state": "{_target_state_id}",
                                            "ability": "intelligence",
                                            "kind": "saving_throw",
                                            "ability_modifier": (
                                                "{target_state.saving_throw_modifiers.intelligence}"
                                            ),
                                            "proficiency_bonus": 0,
                                            "proficiency_factor": "none",
                                            "circumstantial_modifier": 0,
                                            "target_number": "{_befuddlement_due_source.save_dc}",
                                            "advantage": False,
                                            "disadvantage": False,
                                            "forego_save": False,
                                        },
                                        {
                                            "_befuddlement_repeat_roll": "_d20_test",
                                            "_befuddlement_repeat_outcome": "_outcome",
                                            "_befuddlement_repeat_total": "_adjusted_total",
                                        },
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_befuddlement_due_id}.last_repeat_save",
                                        "_befuddlement_repeat_roll",
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_befuddlement_due_id}.last_repeat_outcome",
                                        "_befuddlement_repeat_outcome",
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_befuddlement_due_id}.last_repeat_total",
                                        "_befuddlement_repeat_total",
                                    ),
                                    _when(
                                        "_befuddlement_repeat_outcome == 'success'",
                                        [
                                            _call(
                                                "retire_srd_timed_condition_source",
                                                {
                                                    "lasting_state": "{_lasting_state_id}",
                                                    "source_id": "{_befuddlement_due_id}",
                                                },
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _refresh(),
            _set("_befuddlement_target_active", "target_state.has_befuddlement_sources == true"),
        ],
    )
    tools["settle_srd_befuddlement_deadlines"] = _tool(
        "Visit only saved Befuddlement subjects after the lasting clock actually advances.",
        [_lasting()],
        [
            _set("_befuddlement_registered", "lasting_state.befuddlement_target_ids"),
            _set("_befuddlement_retained", []),
            _each(
                "_befuddlement_registered",
                "_befuddlement_target_id",
                [
                    _call(
                        "resolve_srd_befuddlement_deadline",
                        {
                            "target_state": "{_befuddlement_target_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_befuddlement_target_active"],
                    ),
                    _when(
                        "_befuddlement_target_active == true",
                        [_push("_befuddlement_retained", "_befuddlement_target_id")],
                    ),
                ],
            ),
            _set("lasting_state.befuddlement_target_ids", "_befuddlement_retained"),
            _set("lasting_state.has_befuddlement_sources", "len(_befuddlement_retained) > 0"),
        ],
    )
    return tools


def compose_runtime(tools, changed):
    """Attach the rider to real save damage, source projection, Magic budget and clock owners."""
    changed.update(_tools())
    install = deepcopy(
        changed.get(
            "install_srd_timed_condition_source", tools["install_srd_timed_condition_source"]
        )
    )
    for parameter in install["parameters"]:
        if (
            parameter["name"] == "control_profile"
            and "befuddlement" not in parameter["enum_values"]
        ):
            parameter["enum_values"].append("befuddlement")
    changed["install_srd_timed_condition_source"] = install
    projection = deepcopy(
        changed.get(
            "refresh_srd_timed_condition_target", tools["refresh_srd_timed_condition_target"]
        )
    )
    projection["effects"] = [
        e for e in projection["effects"] if "resolve_srd_befuddlement_projection" not in str(e)
    ]
    projection["effects"].append(
        _when(
            "target_state.has_befuddlement_sources == true",
            [
                _call(
                    "resolve_srd_befuddlement_projection",
                    {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                )
            ],
        )
    )
    changed["refresh_srd_timed_condition_target"] = projection
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [
        e for e in clock["effects"] if "settle_srd_befuddlement_deadlines" not in str(e)
    ]
    clock["effects"].append(
        _when(
            "lasting_state.has_befuddlement_sources == true",
            [_call("settle_srd_befuddlement_deadlines", {"lasting_state": "{_lasting_state_id}"})],
        )
    )
    changed["advance_srd_lasting_clock"] = clock
    family = deepcopy(
        changed.get(
            "cast_spell_single_target_save_damage", tools["cast_spell_single_target_save_damage"]
        )
    )
    if not any(p["name"] == "lasting_state" for p in family["parameters"]):
        family["parameters"].append(_lasting())

    def hook_damage(value):
        if isinstance(value, list):
            value[:] = [
                e
                for e in value
                if not (
                    isinstance(e, dict)
                    and "install_srd_befuddlement_source" in str(e)
                    and e.get("condition", "").startswith("spell == 'befuddlement'")
                )
            ]
            for index, node in reversed(list(enumerate(value))):
                if isinstance(node, dict) and node.get("call") in {
                    "apply_srd_damage", "apply_srd_qualifying_damage"
                }:
                    value.insert(
                        index + 1,
                        _when(
                            "spell == 'befuddlement' and _spell_save_outcome == 'failure'",
                            [
                                _call(
                                    "install_srd_befuddlement_source",
                                    {
                                        "target_state": "{_target_id}",
                                        "caster_state": "{_caster_state_id}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "save_dc": "{_spell_save_dc}",
                                        "actual_spell_level": "{slot_level}",
                                    },
                                    ["_befuddlement_note"],
                                ),
                                _set(
                                    "_save_damage_note",
                                    "_befuddlement_note",
                                ),
                            ],
                        ),
                    )
                elif isinstance(node, dict):
                    hook_damage(node)
        elif isinstance(value, dict):
            for node in value.values():
                hook_damage(node)

    hook_damage(family["effects"])
    changed["cast_spell_single_target_save_damage"] = family
    healing = deepcopy(
        changed.get(
            "cast_spell_instantaneous_restoration", tools["cast_spell_instantaneous_restoration"]
        )
    )
    healing["effects"] = [e for e in healing["effects"] if "clear_srd_befuddlement" not in str(e)]
    healing["effects"].append(
        _when(
            "spell == 'heal'",
            [
                _call(
                    "clear_srd_befuddlement",
                    {"target_state": "{_target_id}", "lasting_state": "state", "all_sources": True},
                )
            ],
        )
    )
    changed["cast_spell_instantaneous_restoration"] = healing
    cast = deepcopy(changed.get("cast_spell", tools["cast_spell"]))
    cast["pre_conditions"] = [
        p for p in cast.get("pre_conditions", []) if "befuddled" not in p["condition"]
    ]
    cast["pre_conditions"].append(
        {
            "condition": "caster_state.befuddled != true",
            "error": "Befuddlement prevents this creature from casting spells.",
        }
    )
    changed["cast_spell"] = cast
    action = deepcopy(
        changed.get("check_srd_active_control_action", tools["check_srd_active_control_action"])
    )
    action["parameters"] = [p for p in action["parameters"] if p["name"] != "magic_action"]
    action["parameters"].append(_param("magic_action", "boolean", required=False, default=False))
    action["effects"] = [e for e in action["effects"] if "Befuddlement" not in str(e)]
    action["effects"].insert(
        0,
        _require(
            "magic_action != true or actor_state == null or actor_state.befuddled != true",
            "Befuddlement prevents this creature from taking the Magic action.",
        ),
    )
    changed["check_srd_active_control_action"] = action
    spend = deepcopy(changed.get("spend_srd_action", tools["spend_srd_action"]))
    def forward_magic_action(value):
        if isinstance(value, list):
            for node in value:
                forward_magic_action(node)
        elif isinstance(value, dict):
            if value.get("call") == "check_srd_active_control_action":
                value["params"]["magic_action"] = "{magic_action}"
            else:
                for node in value.values():
                    forward_magic_action(node)

    forward_magic_action(spend["effects"])
    changed["spend_srd_action"] = spend
