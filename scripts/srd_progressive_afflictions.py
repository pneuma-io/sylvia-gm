"""Progressive spell effects retain source counters, exact conditions, and later cures."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call as _shared_call,
    )
    from .srd_control_behaviors import (
        _Dumper,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_progressive_saves import progressive_save_tool
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call as _shared_call,
    )
    from srd_control_behaviors import (
        _Dumper,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_progressive_saves import progressive_save_tool
    from srd_timed_senses import _replace_tools


PROFILES = {
    "contagion": (5, "action", "touch", 5, "constitution", False),
    "divine_word": (7, "bonus_action", "distance", 30, "charisma", False),
    "flesh_to_stone": (6, "action", "distance", 60, "constitution", True),
    "imprisonment": (9, "1_minute", "distance", 30, "wisdom", False),
}
PROFILE_KEYS = (
    "base_level",
    "casting_time",
    "range_kind",
    "range_feet",
    "save_ability",
    "concentration",
)
SOURCE_PROFILES = (
    "contagion",
    "flesh_stone",
    "flesh_slow",
    "divine_word",
    "divine_banish",
    "imprisonment",
)
PUBLIC_TOOLS = {"observe_srd_imprisonment_trigger", "record_srd_imprisonment_statuette"}


def _call(name, params, exports=()):
    value = _shared_call(name, params, exports)
    if isinstance(exports, dict):
        value["export_context"] = exports
    return value


def validate_definition(spell, definition):
    if spell not in PROFILES or tuple(definition.get(k) for k in PROFILE_KEYS) != PROFILES[spell]:
        raise SystemExit(f"Incoherent progressive spell: {spell}")


def _schema():
    values = {
        "base_level": {"type": "integer", "minimum": 5, "maximum": 9},
        "casting_time": {"type": "string", "enum": ["action", "bonus_action", "1_minute"]},
        "range_kind": {"type": "string", "enum": ["touch", "distance"]},
        "range_feet": {"type": "integer", "minimum": 5, "maximum": 60},
        "save_ability": {"type": "string", "enum": ["constitution", "charisma", "wisdom"]},
        "concentration": {"type": "boolean"},
    }
    return {
        "type": "object",
        "properties": values,
        "required": list(values),
        "additionalProperties": False,
    }


def _refresh():
    return _call(
        "refresh_srd_timed_condition_target",
        {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
    )


def _retire(source="_progressive_id"):
    return _call(
        "retire_srd_timed_condition_source",
        {"source_id": "{" + source + "}", "lasting_state": "{_lasting_state_id}"},
    )


def _loop(effects):
    return [
        _set("_progressive_ids", []),
        _when(
            "target_state.timed_condition_source_ids != null",
            [_set("_progressive_ids", "target_state.timed_condition_source_ids")],
        ),
        _each(
            "_progressive_ids",
            "_progressive_id",
            [
                _set(
                    "_progressive_source", "lasting_state.timed_condition_sources[_progressive_id]"
                ),
                _when(
                    "_progressive_source != null and _progressive_source.installed == true", effects
                ),
            ],
        ),
    ]


def _actual_save(ability, dc, prefix, *, condition="none", automatic=False):
    return [
        _set(prefix + "_roll", None),
        _set(prefix + "_outcome", "'success'"),
        _set(prefix + "_total", None),
        _when(
            automatic if isinstance(automatic, str) else str(automatic).lower() + " != true",
            [
                _require(
                    "target_state.saving_throw_modifiers[" + ability + "] != null",
                    "The target needs its actual saving-throw modifier.",
                ),
                _set(
                    "_progressive_save_modifier",
                    "target_state.saving_throw_modifiers[" + ability + "]",
                ),
                _call(
                    "d20_test",
                    {
                        "actor_state": "{_target_state_id}",
                        "ability": ability.strip("'")
                        if ability.startswith("'")
                        else "{" + ability + "}",
                        "kind": "saving_throw",
                        "ability_modifier": "{_progressive_save_modifier}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "none",
                        "circumstantial_modifier": 0,
                        "target_number": "{" + dc + "}",
                        "advantage": False,
                        "disadvantage": False,
                        "forego_save": False,
                        "save_avoids_condition": condition,
                    },
                    {
                        prefix + "_roll": "_d20_test",
                        prefix + "_outcome": "_outcome",
                        prefix + "_total": "_adjusted_total",
                    },
                ),
            ],
        ),
    ]


def _install(condition, profile, duration, *, concentration=0, spell=None, ability=None):
    return _call(
        "install_srd_timed_condition_source",
        {
            "target_state": "{_target_state_id}",
            "caster_state": "{_caster_state_id}",
            "lasting_state": "{_lasting_state_id}",
            "spell": "{spell}" if spell is None else spell,
            "condition": condition,
            "control_profile": profile,
            "duration_rounds": duration,
            "repeat_save_timing": "none",
            "save_ability": "{save_ability}" if ability is None else ability,
            "save_dc": "{save_dc}",
            "concentration_sequence": concentration
            if isinstance(concentration, int)
            else "{" + concentration + "}",
            "actual_spell_level": "{slot_level}",
        },
        ["_timed_condition_source_id"],
    )


def _source_set(field, value):
    return _set(
        "lasting_state.timed_condition_sources.{_timed_condition_source_id}." + field, value
    )


def _projection_tools():
    effects = [
        _set("_progressive_owned", []),
        _set("_progressive_restrained", False),
        _set("_progressive_petrified", False),
        _set("_progressive_poisoned", False),
        _set("_progressive_deafened", False),
        _set("_progressive_abilities", []),
        _set("target_state.imprisoned", False),
        _set("target_state.imprisonment_no_teleport", False),
        _set("target_state.imprisonment_no_planar_travel", False),
        _set("target_state.imprisonment_cannot_move", False),
        _set("target_state.imprisonment_sealed", False),
        _set("target_state.imprisonment_cannot_awaken", False),
        _set("target_state.imprisonment_divination_blocked", False),
    ]
    effects += _loop(
        [
            _when(
                "_progressive_source.suppressed != true",
                [
                    *[
                        _when(
                            "_progressive_source.condition == '"
                            + condition
                            + "'"
                            + (
                                " and _progressive_source.control_profile != 'contagion'"
                                if condition == "poisoned"
                                else ""
                            ),
                            [_set("_progressive_" + condition, True)],
                        )
                        for condition in ("restrained", "petrified", "poisoned", "deafened")
                    ],
                ],
            ),
            _when(
                (
                    "_progressive_source.control_profile == "
                    "'contagion' or _progressive_source.control_profi"
                    "le == 'flesh_stone' or "
                    "_progressive_source.control_profile == "
                    "'flesh_slow' or _progressive_source.control_prof"
                    "ile == 'divine_word' or "
                    "_progressive_source.control_profile == "
                    "'divine_banish' or "
                    "_progressive_source.control_profile == "
                    "'imprisonment'"
                ),
                [
                    _push("_progressive_owned", "_progressive_id"),
                    _when(
                        "_progressive_source.suppressed != true",
                        [
                            _when(
                                "_progressive_source.condition == 'restrained'",
                                [_set("_progressive_restrained", True)],
                            ),
                            _when(
                                "_progressive_source.condition == 'petrified'",
                                [_set("_progressive_petrified", True)],
                            ),
                            _when(
                                "_progressive_source.condition == 'deafened'",
                                [_set("_progressive_deafened", True)],
                            ),
                            _when(
                                "_progressive_source.control_profile == 'contagion'",
                                [
                                    _call(
                                        "check_srd_target_condition_immunity",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "condition": "poisoned",
                                            "source_state_id": (
                                                "{_progressive_source.caster_state_id}"
                                            ),
                                        },
                                        ["_source_condition_immune"],
                                    ),
                                    _when(
                                        "_source_condition_immune != true",
                                        [
                                            _set("_progressive_poisoned", True),
                                            _when(
                                                (
                                                    "_progressive_source.contagion_ability not in "
                                                    "_progressive_abilities"
                                                ),
                                                [
                                                    _push(
                                                        ("_progressive_abilities"),
                                                        "_progressive_source.contagion_ability",
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_progressive_source.control_profile == 'flesh_slow' and "
                                "target_state.spell_freedom_of_movement != true",
                                [_set("target_state.timed_control_speed_zero", True)],
                            ),
                        ],
                    ),
                    _when(
                        "_progressive_source.control_profile == 'imprisonment'",
                        [
                            _set("target_state.imprisoned", True),
                            _set("target_state.imprisonment_no_teleport", True),
                            _set("target_state.imprisonment_divination_blocked", True),
                            _when(
                                "_progressive_source.imprisonment_mode == 'chaining'",
                                [_set("target_state.imprisonment_cannot_move", True)],
                            ),
                            _when(
                                "_progressive_source.imprisonment_mode == 'hedged_prison'",
                                [_set("target_state.imprisonment_no_planar_travel", True)],
                            ),
                            _when(
                                (
                                    "_progressive_source.imprisonment_mode == "
                                    "'burial' or _progressive_source.imprisonment_mod"
                                    "e == 'minimus'"
                                ),
                                [_set("target_state.imprisonment_sealed", True)],
                            ),
                            _when(
                                "_progressive_source.imprisonment_mode == 'slumber'",
                                [_set("target_state.imprisonment_cannot_awaken", True)],
                            ),
                        ],
                    ),
                ],
            ),
        ]
    )
    for condition in ("restrained", "petrified", "poisoned", "deafened"):
        field = "target_state.timed_condition_unowned_" + condition
        effects += [
            {"remove_condition": {"target": "target_state", "condition": condition}},
            _when(
                field + " == true or _progressive_" + condition + " == true",
                [{"apply_condition": {"target": "target_state", "condition": condition}}],
            ),
        ]
    effects += [
        _set("target_state.contagion_disadvantage_abilities", "_progressive_abilities"),
        _set("target_state.progressive_source_ids", "_progressive_owned"),
        _set("target_state.has_progressive_sources", "len(_progressive_owned) > 0"),
    ]
    return {
        "resolve_srd_progressive_projection": _tool(
            (
                "Project exact progressive sources after "
                "immunity suppression without losing independent "
                "conditions."
            ),
            [_entity("target_state"), _lasting()],
            effects,
        )
    }


def _turn_tools():
    return {
        "resolve_srd_progressive_save": progressive_save_tool(),
        "prepare_all_srd_progressive_lifetimes": _tool(
            "Settle progressive target lifetimes before caster concentration expiry.",
            [_lasting()],
            [
                _set("_progressive_lifetime_targets", "lasting_state.progressive_target_ids"),
                _each(
                    "_progressive_lifetime_targets",
                    "_progressive_lifetime_target",
                    [
                        _call(
                            "prepare_srd_progressive_lifetimes",
                            {
                                "target_state": "{_progressive_lifetime_target}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "end_srd_progressive_turn": _tool(
            "Make each unfinished source's actual cumulative save once at its target turn end.",
            [_entity("target_state"), _lasting()],
            [
                _set("_progressive_turn_rows", []),
                *_loop(
                    [
                        _when(
                            (
                                "target_state.is_alive == true and "
                                "target_state.turns_started != null and "
                                "_progressive_source.progressive_saved_turn != "
                                "target_state.turns_started and "
                                "((_progressive_source.control_profile == "
                                "'contagion' and _progressive_source.contagion_lo"
                                "cked != true) or "
                                "(_progressive_source.control_profile == "
                                "'flesh_stone' and _progressive_source.condition "
                                "== 'restrained'))"
                            ),
                            [
                                _set(
                                    (
                                        "lasting_state.timed_condition_sources.{_progress"
                                        "ive_id}.progressive_saved_turn"
                                    ),
                                    "target_state.turns_started",
                                ),
                                _call(
                                    "resolve_srd_progressive_save",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "source_id": "{_progressive_id}",
                                        "lasting_state": "{_lasting_state_id}",
                                    },
                                    [
                                        "_progressive_save_roll",
                                        "_progressive_save_outcome",
                                        "_progressive_save_total",
                                        "_progressive_successes",
                                        "_progressive_failures",
                                    ],
                                ),
                                _set(
                                    (
                                        "lasting_state.timed_condition_sources.{_progress"
                                        "ive_id}.last_repeat_save"
                                    ),
                                    "_progressive_save_roll",
                                ),
                                _set(
                                    (
                                        "lasting_state.timed_condition_sources.{_progress"
                                        "ive_id}.last_repeat_outcome"
                                    ),
                                    "_progressive_save_outcome",
                                ),
                                _push(
                                    "_progressive_turn_rows",
                                    {
                                        "label": "_progressive_source.spell_id",
                                        "value": (
                                            "'Save {_progressive_save_outcome}: "
                                            "{_progressive_save_total}; successes "
                                            "{_progressive_successes}, failures "
                                            "{_progressive_failures}.'"
                                        ),
                                    },
                                ),
                                _when("_progressive_successes >= 3", [_retire()]),
                                _when(
                                    (
                                        "_progressive_failures >= 3 and "
                                        "_progressive_source.control_profile == "
                                        "'contagion'"
                                    ),
                                    [
                                        _set(
                                            (
                                                "lasting_state.timed_condition_sources.{_progress"
                                                "ive_id}.contagion_locked"
                                            ),
                                            True,
                                        )
                                    ],
                                ),
                                _when(
                                    (
                                        "_progressive_failures >= 3 and "
                                        "_progressive_source.control_profile == "
                                        "'flesh_stone'"
                                    ),
                                    [
                                        _set(
                                            (
                                                "lasting_state.timed_condition_sources.{_progress"
                                                "ive_id}.condition"
                                            ),
                                            "'petrified'",
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ]
                ),
                _refresh(),
            ],
        ),
        "prepare_srd_progressive_lifetimes": _tool(
            (
                "Detach fully concentrated stone before generic "
                "duration pruning and expire the initial success "
                "at caster turn start."
            ),
            [_entity("target_state"), _lasting()],
            _loop(
                [
                    _when(
                        (
                            "_progressive_source.control_profile == "
                            "'flesh_stone' and "
                            "_progressive_source.concentration_sequence > 0 "
                            "and _progressive_source.expires_tick != null "
                            "and lasting_state.lasting_tick >= "
                            "_progressive_source.expires_tick"
                        ),
                        [
                            _call(
                                "check_srd_condition_concentration",
                                {
                                    "caster_state": "{_progressive_source.caster_state_id}",
                                    "sequence": "{_progressive_source.concentration_sequence}",
                                },
                                ["_condition_concentration_active"],
                            ),
                            _when(
                                "_condition_concentration_active == true",
                                [
                                    _set(
                                        (
                                            "lasting_state.timed_condition_sources.{_progress"
                                            "ive_id}.condition"
                                        ),
                                        "'petrified'",
                                    ),
                                    _set(
                                        (
                                            "lasting_state.timed_condition_sources.{_progress"
                                            "ive_id}.concentration_sequence"
                                        ),
                                        0,
                                    ),
                                    _set(
                                        (
                                            "lasting_state.timed_condition_sources.{_progress"
                                            "ive_id}.expires_tick"
                                        ),
                                        None,
                                    ),
                                    _set(
                                        (
                                            "lasting_state.timed_condition_sources.{_progress"
                                            "ive_id}.flesh_permanent"
                                        ),
                                        True,
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_progressive_id}.dispel_ongoing",
                                        False,
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_progressive_id}.ongoing_duration",
                                        False,
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_progressive_source.control_profile == 'flesh_slow'",
                        [
                            _call(
                                "expire_srd_flesh_success",
                                {
                                    "caster_state": "{_progressive_source.caster_state_id}",
                                    "source_id": "{_progressive_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            )
                        ],
                    ),
                ]
            ),
        ),
        "expire_srd_flesh_success": _tool(
            "End only this successful-save Speed zero source at the caster's next actual turn.",
            [_entity("caster_state"), _lasting(), _param("source_id")],
            [
                _set("_flesh_success", "lasting_state.timed_condition_sources[source_id]"),
                _when(
                    (
                        "caster_state.turns_started != null and "
                        "caster_state.turns_started > "
                        "_flesh_success.caster_turn"
                    ),
                    [_retire("source_id")],
                ),
            ],
        ),
        "check_srd_contagion_cure": _tool(
            (
                "Roll the real Constitution save before an "
                "effect ends any active Contagion Poisoned "
                "source."
            ),
            [_entity("target_state"), _lasting()],
            [
                _set("_contagion_cure_allowed", True),
                *_loop(
                    [
                        _when(
                            (
                                "_progressive_source.control_profile == "
                                "'contagion' and 'poisoned' in "
                                "target_state.conditions"
                            ),
                            [
                                *_actual_save(
                                    "'constitution'",
                                    "_progressive_source.save_dc",
                                    "_contagion_cure",
                                    condition="poisoned",
                                ),
                                _when(
                                    "_contagion_cure_outcome != 'success'",
                                    [_set("_contagion_cure_allowed", False)],
                                ),
                            ],
                        ),
                    ]
                ),
            ],
        ),
        "clear_srd_magical_contagion": _tool(
            (
                "True Resurrection ends every exact Contagion "
                "source before its ordinary condition cures."
            ),
            [_entity("target_state"), _lasting()],
            [
                *_loop([_when("_progressive_source.control_profile == 'contagion'", [_retire()])]),
                _refresh(),
            ],
        ),
    }


def _target_tools():
    effects = [
        _set("_progressive_note", "'Successful save; unaffected.'"),
        _require(
            (
                "target_state.is_alive == true and "
                "target_state.hp != null and "
                "target_state.conditions != null"
            ),
            "Select a living creature with authoritative Hit Points and conditions.",
        ),
        _set(
            "_progressive_auto",
            "(spell == 'flesh_to_stone' and target_state.creature_type == 'construct') or "
            "(spell == 'imprisonment' and target_state.imprisonment_immune_until != null and "
            "lasting_state.lasting_tick < target_state.imprisonment_immune_until)",
        ),
        *_actual_save(
            "save_ability", "save_dc", "_progressive_initial", automatic="_progressive_auto != true"
        ),
        _when(
            "spell == 'contagion' and _progressive_initial_outcome != 'success'",
            [
                {"dice": {"expression": "11d8"}, "store_as": "_contagion_damage"},
                _call(
                    "apply_srd_qualifying_damage",
                    {
                        "target": "{_target_state_id}",
                        "amount": "{_contagion_damage.total}",
                        "adjustment": 0,
                        "damage_type": "necrotic",
                        "source_state_id": "{_caster_state_id}",
                        "source_damage_kind": "spell",
                    },
                    ["_effective_damage"],
                ),
                _install("poisoned", "contagion", 100800),
                _source_set("dispel_ongoing", True),
                _source_set("ongoing_duration", True),
                _source_set("contagion_ability", "chosen_ability"),
                _source_set("progressive_successes", 0),
                _source_set("progressive_failures", 0),
                _source_set("contagion_locked", False),
                _set(
                    "_progressive_note",
                    (
                        "'Failed save: 11d8 Necrotic damage, Poisoned "
                        "and chosen-ability save Disadvantage; "
                        "cumulative turn-end saves.'"
                    ),
                ),
            ],
        ),
        _when(
            "spell == 'flesh_to_stone'",
            [
                _when(
                    "_progressive_initial_outcome == 'success'",
                    [
                        _install("none", "flesh_slow", 1),
                        _source_set("dispel_ongoing", True),
                        _source_set("ongoing_duration", True),
                        _source_set("concentration_sequence", 0),
                        _source_set("expires_tick", None),
                        _source_set("caster_turn", 0),
                        _when(
                            "caster_state.turns_started != null",
                            [_source_set("caster_turn", "caster_state.turns_started")],
                        ),
                        _set(
                            "_progressive_note",
                            "'Successful save: Speed zero until the caster starts its next turn.'",
                        ),
                    ],
                    [
                        _install(
                            "restrained", "flesh_stone", 10, concentration="concentration_sequence"
                        ),
                        _source_set("dispel_ongoing", True),
                        _source_set("ongoing_duration", True),
                        _source_set("progressive_successes", 0),
                        _source_set("progressive_failures", 1),
                        _source_set("flesh_permanent", False),
                        _set(
                            "_progressive_note",
                            (
                                "'Restrained; cumulative Constitution saves, "
                                "initial failure counted. Full concentration "
                                "makes Petrified permanent.'"
                            ),
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "spell == 'divine_word' and _progressive_initial_outcome != 'success'",
            [
                _call(
                    "resolve_srd_divine_word_failure",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{slot_level}",
                        "save_dc": "{save_dc}",
                    },
                    ["_progressive_note"],
                )
            ],
        ),
        _when(
            "spell == 'imprisonment' and _progressive_auto != true",
            [
                _set("_imprisonment_failed", "_progressive_initial_outcome != 'success'"),
                _call(
                    "resolve_srd_imprisonment_outcome",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{slot_level}",
                        "save_dc": "{save_dc}",
                        "save_failed": "{_imprisonment_failed}",
                        "mode": "{imprisonment_mode}",
                        "trigger": "{imprisonment_trigger}",
                        "component_id": "{imprisonment_component_id}",
                        "demiplane": "{imprisonment_demiplane}",
                    },
                    ["_progressive_note"],
                ),
            ],
        ),
        _when(
            "spell == 'imprisonment' and _progressive_auto == true",
            [
                _set(
                    "_progressive_note",
                    "'Unaffected by the existing 24-hour Imprisonment immunity.'",
                )
            ],
        ),
        _refresh(),
    ]
    return {
        "resolve_srd_progressive_target": _tool(
            (
                "Resolve an actual initial save, then install "
                "the spell's source-owned later consequence."
            ),
            [
                _entity("target_state"),
                _entity("caster_state"),
                _lasting(),
                _param("spell"),
                _param("slot_level", "integer"),
                _param("save_dc", "integer"),
                _param("save_ability"),
                _param("concentration_sequence", "integer"),
                _param("chosen_ability"),
                _param("imprisonment_mode"),
                _param("imprisonment_trigger", required=False, default=""),
                _param("imprisonment_component_id", required=False, default=""),
                _param("imprisonment_demiplane", required=False, default=""),
            ],
            effects,
        )
    }


def _casting_tools():
    optional = [
        _param(
            "chosen_ability",
            "enum",
            enum_values=[
                "none",
                "strength",
                "dexterity",
                "constitution",
                "intelligence",
                "wisdom",
                "charisma",
            ],
            required=False,
            default="none",
        ),
        _param(
            "imprisonment_mode",
            "enum",
            enum_values=["none", "burial", "chaining", "hedged_prison", "minimus", "slumber"],
            required=False,
            default="none",
        ),
        _param("imprisonment_trigger", required=False, default=""),
        _param("imprisonment_trigger_observable", "boolean", required=False, default=False),
        _param("imprisonment_trigger_likely_decade", "boolean", required=False, default=False),
        _param("imprisonment_component_id", required=False, default=""),
        _param("imprisonment_demiplane", required=False, default=""),
    ]
    effect = [
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        _require(
            "len(targets) >= 1 and (spell == 'divine_word' or len(targets) == 1)",
            "Select one creature, or the Divine Word creatures in range.",
        ),
        _require(
            "spell != 'contagion' or chosen_ability != 'none'",
            "Choose Contagion's affected saving-throw ability.",
        ),
        _require(
            (
                "spell != 'imprisonment' or (imprisonment_mode "
                "!= 'none' and imprisonment_trigger != '' and "
                "imprisonment_trigger_observable == true and "
                "imprisonment_trigger_likely_decade == true and "
                "imprisonment_component_id in material_item_ids)"
            ),
            (
                "Imprisonment needs a chosen mode, actual used "
                "statuette, and an observable ending trigger "
                "judged likely within a decade."
            ),
        ),
        _require(
            "imprisonment_mode != 'hedged_prison' or imprisonment_demiplane != ''",
            "Name the actual prison demiplane.",
        ),
        _set("_progressive_concentration", 0),
        _when(
            "spell == 'flesh_to_stone'",
            [
                _call(
                    "start_srd_condition_concentration",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "targets": "{targets}",
                        "lasting_state": "{_lasting_state_id}",
                        "duration_rounds": 10,
                        "save_modifier": "{concentration_save_modifier}",
                        "actual_spell_level": "{slot_level}",
                    },
                    ["_condition_concentration_sequence"],
                ),
                _set("_progressive_concentration", "_condition_concentration_sequence"),
            ],
        ),
        _each(
            "targets",
            "_progressive_target_id",
            [
                _call(
                    "qualify_srd_progressive_target",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_progressive_target_id}",
                        "spell": "{spell}",
                    },
                ),
                _when(
                    "spell == 'imprisonment'",
                    [
                        _call(
                            "qualify_srd_imprisonment_statuette",
                            {
                                "caster_state": "{_caster_state_id}",
                                "target_state": "{_progressive_target_id}",
                                "material_id": "{imprisonment_component_id}",
                            },
                        )
                    ],
                ),
                _call(
                    "resolve_srd_progressive_target",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_progressive_target_id}",
                        "spell": "{spell}",
                        "slot_level": "{slot_level}",
                        "save_dc": "{save_dc}",
                        "save_ability": "{save_ability}",
                        "concentration_sequence": "{_progressive_concentration}",
                        "chosen_ability": "{chosen_ability}",
                        "imprisonment_mode": "{imprisonment_mode}",
                        "imprisonment_trigger": "{imprisonment_trigger}",
                        "imprisonment_component_id": "{imprisonment_component_id}",
                        "imprisonment_demiplane": "{imprisonment_demiplane}",
                    },
                    [
                        "_progressive_note",
                        "_progressive_initial_roll",
                        "_progressive_initial_outcome",
                        "_progressive_initial_total",
                    ],
                ),
                _push(
                    "_spell_summary_rows",
                    {"label": "_progressive_target_id", "value": "_progressive_note"},
                ),
                _call(
                    "record_srd_spell_target_result",
                    {
                        "target": "{_progressive_target_id}",
                        "brief": "{_progressive_note}",
                        "detail": (
                            "Save {_progressive_initial_outcome}: "
                            "{_progressive_initial_total}; roll {_progressive_initial_roll}."
                        ),
                    },
                    ["_spell_target_detail"],
                ),
                _push("_spell_detail_rows", "_spell_target_detail"),
            ],
        ),
    ]
    return optional, {
        "cast_srd_progressive_affliction": _tool(
            (
                "Resolve paid progressive casts through actual "
                "scene targets, saves and later source owners."
            ),
            [
                _entity("caster_state"),
                _lasting(),
                _param("spell"),
                _param("slot_level", "integer"),
                _param("save_dc", "integer"),
                _param("save_ability"),
                _param("concentration_save_modifier", "integer"),
                _param(
                    "targets",
                    "entity_id_list",
                    resolve="entity_list",
                    unique_items=True,
                    min_items=1,
                    max_items=8,
                ),
                _param("material_item_ids", "array", items={"type": "string"}),
                *deepcopy(optional),
            ],
            effect,
        ),
        "qualify_srd_progressive_target": _tool(
            "Derive range, plane, path, and required sight from the admitted scene.",
            [_entity("target_state"), _entity("caster_state"), _param("spell"), _lasting()],
            [
                _set("_progressive_range", 30),
                _when("spell == 'contagion'", [_set("_progressive_range", 5)]),
                _when("spell == 'flesh_to_stone'", [_set("_progressive_range", 60)]),
                _call(
                    "resolve_srd_scene_geometry_relation",
                    {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                    ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
                ),
                _require(
                    (
                        "_scene_same_plane == true and _scene_distance "
                        "<= _progressive_range and _scene_line_of_sight "
                        "== true"
                    ),
                    "The admitted target must be in range on this plane with a clear path.",
                ),
                _when(
                    "spell == 'flesh_to_stone' or spell == 'imprisonment'",
                    [
                        _call(
                            "resolve_srd_scene_relation",
                            {
                                "actor_state": "{_caster_state_id}",
                                "target_state": "{_target_state_id}",
                            },
                            ["_scene_can_see"],
                        ),
                        _require(
                            "_scene_can_see == true", "The caster must actually see this creature."
                        ),
                    ],
                ),
                _when(
                    (
                        "spell == 'divine_word' and "
                        "(target_state.creature_type == 'celestial' or "
                        "target_state.creature_type == 'elemental' or "
                        "target_state.creature_type == 'fey' or "
                        "target_state.creature_type == 'fiend')"
                    ),
                    [
                        _require(
                            "target_state.home_plane != null and target_state.home_plane != ''",
                            "Record this creature's actual plane of origin.",
                        )
                    ],
                ),
            ],
        ),
    }


def _planar_tools():
    tools = {}
    tools["change_srd_progressive_plane"] = _tool(
        "Change the exact admitted scene subject's plane while retaining its unrelated metadata.",
        [
            _entity("target_state"),
            _lasting(),
            _param("state", resolve="entity", required=False, default="state"),
            _param("plane"),
        ],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_target_state_id}"},
                ["_info_scene_entity"],
            ),
            _set("_progressive_plane_patch", {"plane": "plane"}),
            _set(
                "_progressive_plane_metadata",
                "merge(_info_scene_entity.metadata, _progressive_plane_patch)",
            ),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{state.spatial_scene.map.revision}",
                    "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                    "patch": {"metadata": "{_progressive_plane_metadata}"},
                },
                "store_as": "_progressive_plane_mutation",
            },
        ],
    )
    tools["resolve_srd_divine_word_failure"] = _tool(
        "Apply the real Hit Point threshold consequences and exact failed-save plane return ban.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("slot_level", "integer"),
            _param("save_dc", "integer"),
        ],
        [
            _set("spell", "'divine_word'"),
            _set("save_ability", "'charisma'"),
            _set("_progressive_note", "'Failed save; no Hit Point threshold consequence.'"),
            _when(
                "target_state.hp <= 20",
                [
                    _call(
                        "consume_srd_death_prevention",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_death_prevented"],
                    ),
                    _when(
                        "_death_prevented == true",
                        [_set("_progressive_note", "'Death Ward prevents instant death.'")],
                        [
                            _set("_divine_concentration_target", None),
                            _when(
                                "target_state.concentration_target_state_id != null",
                                [
                                    _set(
                                        "_divine_concentration_target",
                                        "target_state.concentration_target_state_id",
                                    ),
                                ],
                            ),
                            _set("target_state.is_alive", False),
                            {"apply_condition": {"target": "target_state", "condition": "dead"}},
                            _call(
                                "record_srd_actual_death", {"target_state": "{_target_state_id}"}
                            ),
                            _when(
                                "_divine_concentration_target != null",
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_target_state_id}",
                                            "target_state": "{_divine_concentration_target}",
                                        },
                                    )
                                ],
                            ),
                            _set("_progressive_note", "'Failed save: the target dies.'"),
                        ],
                    ),
                ],
            ),
            _when(
                "target_state.hp > 20 and target_state.hp <= 50",
                [
                    _set("_divine_duration", 10),
                    _when("target_state.hp <= 40", [_set("_divine_duration", 100)]),
                    _when("target_state.hp <= 30", [_set("_divine_duration", 600)]),
                    _install(
                        "deafened",
                        "divine_word",
                        "{_divine_duration}",
                        spell="divine_word",
                        ability="charisma",
                    ),
                    _source_set("dispel_ongoing", False),
                    _when(
                        "target_state.hp <= 40",
                        [
                            _install(
                                "blinded",
                                "divine_word",
                                "{_divine_duration}",
                                spell="divine_word",
                                ability="charisma",
                            ),
                            _source_set("dispel_ongoing", False),
                        ],
                    ),
                    _when(
                        "target_state.hp <= 30",
                        [
                            _install(
                                "stunned",
                                "divine_word",
                                "{_divine_duration}",
                                spell="divine_word",
                                ability="charisma",
                            ),
                            _source_set("dispel_ongoing", False),
                        ],
                    ),
                    _set(
                        "_progressive_note",
                        (
                            "'Failed save: timed Divine Word conditions from "
                            "actual current Hit Points.'"
                        ),
                    ),
                ],
            ),
            _when(
                (
                    "target_state.creature_type == 'celestial' or "
                    "target_state.creature_type == 'elemental' or "
                    "target_state.creature_type == 'fey' or "
                    "target_state.creature_type == 'fiend'"
                ),
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ["_info_scene_entity"],
                    ),
                    _set("_divine_origin_plane", "_info_scene_entity.metadata.plane"),
                    _when(
                        "_divine_origin_plane != target_state.home_plane",
                        [
                            _call(
                                "change_srd_progressive_plane",
                                {
                                    "target_state": "{_target_state_id}",
                                    "plane": "{target_state.home_plane}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                            _install(
                                "none",
                                "divine_banish",
                                14400,
                                spell="divine_word",
                                ability="charisma",
                            ),
                            _source_set("dispel_ongoing", False),
                            _source_set("banned_plane", "_divine_origin_plane"),
                            _set(
                                "_progressive_note",
                                (
                                    "'Failed save: returned to origin plane and "
                                    "barred from this plane for 24 hours; Hit Point "
                                    "consequences also apply.'"
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    tools["resolve_srd_imprisonment_outcome"] = _tool(
        "Save the chosen prison and observable ending trigger, or the successful-save immunity.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("slot_level", "integer"),
            _param("save_dc", "integer"),
            _param("save_failed", "boolean"),
            _param("mode"),
            _param("trigger"),
            _param("component_id"),
            _param("demiplane", required=False, default=""),
        ],
        [
            _set("spell", "'imprisonment'"),
            _set("save_ability", "'wisdom'"),
            _set(
                "_progressive_note",
                "'Successful save: unaffected and immune to Imprisonment for 24 hours.'",
            ),
            _when(
                "save_failed == false",
                [
                    _set(
                        "target_state.imprisonment_immune_until",
                        "lasting_state.lasting_tick + 28800",
                    )
                ],
                [
                    _when(
                        "mode == 'hedged_prison'",
                        [
                            _call(
                                "change_srd_progressive_plane",
                                {"target_state": "{_target_state_id}", "plane": "{demiplane}"},
                            )
                        ],
                    ),
                    _set("_imprisonment_condition", "'none'"),
                    _when("mode == 'chaining'", [_set("_imprisonment_condition", "'restrained'")]),
                    _when("mode == 'slumber'", [_set("_imprisonment_condition", "'unconscious'")]),
                    _install(
                        "{_imprisonment_condition}",
                        "imprisonment",
                        0,
                        spell="imprisonment",
                        ability="wisdom",
                    ),
                    _source_set("imprisonment_mode", "mode"),
                    _source_set("imprisonment_trigger", "trigger"),
                    _source_set("imprisonment_component_id", "component_id"),
                    _call(
                        "qualify_srd_imprisonment_statuette",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_target_state_id}",
                            "material_id": "{component_id}",
                        },
                        ["_statuette_item"],
                    ),
                    _source_set("imprisonment_component_item", "_statuette_item"),
                    _when(
                        "mode == 'chaining'",
                        [
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_target_state_id}"},
                                ["_info_scene_entity"],
                            ),
                            _source_set("imprisonment_prison_entity_id", "_info_scene_entity.id"),
                        ],
                    ),
                    _source_set("dispel_ongoing", False),
                    _set(
                        "_progressive_note",
                        (
                            "'Imprisoned until the recorded observable "
                            "trigger or qualifying level-9 Dispel Magic on "
                            "its prison or actual statuette.'"
                        ),
                    ),
                ],
            ),
        ],
    )
    tools["check_srd_imprisonment_divination"] = _tool(
        "Return the exact subject's effective source-owned Divination protection.",
        [_entity("target_state"), _lasting()],
        [
            _when("target_state.has_progressive_sources == true", [_refresh()]),
            _set(
                "_imprisonment_divination_blocked",
                "target_state.imprisonment_divination_blocked == true",
            ),
        ],
    )
    tools["check_srd_progressive_travel"] = _tool(
        (
            "Reject teleportation, movement of rooted "
            "chains, warded planar travel, and an active "
            "Divine Word return ban."
        ),
        [
            _entity("target_state"),
            {**_lasting(), "model_hidden": True},
            _param("destination_plane"),
            _param("teleport", "boolean", required=False, default=True),
        ],
        [
            _when(
                "target_state.has_progressive_sources == true",
                [
                    _refresh(),
                    _require(
                        "target_state.imprisonment_cannot_move != true",
                        "Imprisonment's chains prevent movement by any means.",
                    ),
                    _require(
                        "teleport != true or target_state.imprisonment_no_teleport != true",
                        "An imprisoned creature cannot teleport.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ["_info_scene_entity"],
                    ),
                    _require(
                        (
                            "target_state.imprisonment_no_planar_travel != "
                            "true or destination_plane == "
                            "_info_scene_entity.metadata.plane"
                        ),
                        "The Hedged Prison is warded against planar travel.",
                    ),
                    *_loop(
                        [
                            _require(
                                (
                                    "_progressive_source.control_profile != "
                                    "'divine_banish' or destination_plane != "
                                    "_progressive_source.banned_plane"
                                ),
                                (
                                    "Divine Word prevents return to this plane for "
                                    "24 hours except by Wish."
                                ),
                            )
                        ]
                    ),
                ],
            ),
        ],
    )
    tools["observe_srd_imprisonment_trigger"] = _tool(
        (
            "After the GM observes the saved ending action, "
            "end exactly the selected Imprisonment source."
        ),
        [
            _entity("target_state"),
            {**_lasting(), "model_hidden": True},
            _param("source_id"),
            _param("observed_trigger"),
            _param("trigger_observed", "boolean"),
        ],
        [
            _set("_imprisonment_source", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                (
                    "_imprisonment_source.installed == true and "
                    "_imprisonment_source.target_state_id == "
                    "_target_state_id and "
                    "_imprisonment_source.control_profile == "
                    "'imprisonment'"
                ),
                "Select this creature's active prison source.",
            ),
            _require(
                (
                    "trigger_observed == true and observed_trigger "
                    "== _imprisonment_source.imprisonment_trigger"
                ),
                "Observe the exact ending trigger chosen when this spell was cast.",
            ),
            _retire("source_id"),
            _refresh(),
        ],
        public=True,
    )
    return tools


def _imprisonment_recovery_tools():
    "Keep target-specific statuette provenance and exact prison/component Dispel together."
    tools = {}
    tools["resolve_srd_imprisonment_statuette_lot"] = _tool(
        "Resolve one possessed, priced Imprisonment statuette from actual material lots.",
        [_entity("caster_state"), _param("material_id")],
        [
            _set("_statuette_index", None),
            _set("_statuette_item", None),
            _set("_statuette_target", None),
            _set("_statuette_cursor", 0),
            _set("_statuette_lots", []),
            _when(
                "caster_state.spell_materials != null",
                [_set("_statuette_lots", "caster_state.spell_materials")],
            ),
            _each(
                "_statuette_lots",
                "_statuette_lot",
                [
                    _when(
                        "_statuette_lot.id == material_id",
                        [
                            _require(
                                "_statuette_index == null", "The material lot ID is ambiguous."
                            ),
                            _require(
                                "_statuette_lot.quantity >= 1 and "
                                "_statuette_lot.value_cp >= 500000 "
                                "and 'imprisonment.1' in _statuette_lot.components and "
                                "_statuette_lot.inventory_item in caster_state.inventory",
                                "Use the actual possessed statuette worth at least 5,000 GP.",
                            ),
                            _set("_statuette_index", "_statuette_cursor"),
                            _set("_statuette_item", "_statuette_lot.inventory_item"),
                            _when(
                                "caster_state.imprisonment_statuettes != null and "
                                "caster_state.imprisonment_statuettes[material_id] != null",
                                [
                                    _set(
                                        "_statuette_target",
                                        "caster_state.imprisonment_statuettes[material_id]",
                                    )
                                ],
                            ),
                        ],
                    ),
                    _set("_statuette_cursor", "_statuette_cursor + 1"),
                ],
            ),
            _require("_statuette_index != null", "Select an actual saved material lot."),
        ],
    )
    tools["qualify_srd_imprisonment_statuette"] = _tool(
        "Require the used statuette to depict this actual target.",
        [_entity("caster_state"), _entity("target_state"), _param("material_id")],
        [
            _call(
                "resolve_srd_imprisonment_statuette_lot",
                {"caster_state": "{_caster_state_id}", "material_id": "{material_id}"},
                ["_statuette_item", "_statuette_target"],
            ),
            _require(
                "_statuette_target == _target_state_id",
                "The used Imprisonment statuette must depict the selected creature.",
            ),
        ],
    )
    tools["record_srd_imprisonment_statuette"] = _tool(
        "Record which actual creature a possessed Imprisonment statuette depicts.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _param("material_id"),
            _param("depicts_target", "boolean"),
        ],
        [
            _require("depicts_target == true", "Record the statuette's actual depicted creature."),
            _call(
                "resolve_srd_imprisonment_statuette_lot",
                {"caster_state": "{_caster_state_id}", "material_id": "{material_id}"},
                ["_statuette_index"],
            ),
            _set("caster_state.imprisonment_statuettes.{material_id}", "_target_state_id"),
        ],
        public=True,
    )
    tools["qualify_srd_imprisonment_component_dispel"] = _tool(
        "Target the exact used component while its original owner still possesses it.",
        [
            _entity("caster_state"),
            _entity("component_owner"),
            _param("source_id"),
            _param("component_id"),
            _lasting(),
        ],
        [
            _set("_component_source", "lasting_state.timed_condition_sources[source_id]"),
            _call(
                "resolve_srd_imprisonment_statuette_lot",
                {"caster_state": "{_component_owner_id}", "material_id": "{component_id}"},
                ["_statuette_item"],
            ),
            _require(
                "component_id == _component_source.imprisonment_component_id and "
                "_statuette_item == _component_source.imprisonment_component_item",
                "Dispel must target the exact statuette used by this source.",
            ),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_component_owner_id}"},
                ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
            ),
            _require(
                "_scene_same_plane == true and _scene_distance <= 120 and "
                "_scene_line_of_sight == true",
                "The actual used component is outside Dispel Magic's range or clear path.",
            ),
        ],
    )
    tools["dispel_srd_imprisonment_source"] = _tool(
        "Only a level-9 Dispel Magic targeting this prison or used component ends this source.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("slot_level", "integer"),
            _param("choice"),
            _param("item_id"),
        ],
        [
            _set("_prison_source", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                "_prison_source.installed == true and "
                "_prison_source.control_profile == 'imprisonment' and "
                "_prison_source.target_state_id == _target_state_id",
                "Select this creature's exact active Imprisonment source.",
            ),
            _when(
                "choice == 'imprisonment_component'",
                [
                    _call(
                        "qualify_srd_imprisonment_component_dispel",
                        {
                            "caster_state": "{_caster_state_id}",
                            "component_owner": "{_prison_source.caster_state_id}",
                            "source_id": "{source_id}",
                            "component_id": "{item_id}",
                        },
                    ),
                ],
                [
                    _require(
                        "choice == 'imprisonment_prison' and "
                        "_prison_source.imprisonment_mode == 'chaining' and "
                        "item_id == source_id",
                        "Select Chaining's actual anchored scene prison; other prison geometry "
                        "must be admitted by its owning physical mode.",
                    ),
                    _call(
                        "resolve_srd_scene_geometry_relation",
                        {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                        [
                            "_scene_target_entity_id",
                            "_scene_distance",
                            "_scene_same_plane",
                            "_scene_line_of_sight",
                        ],
                    ),
                    _require(
                        "_prison_source.imprisonment_prison_entity_id == _scene_target_entity_id "
                        "and _scene_same_plane == true "
                        "and _scene_distance <= 120 and _scene_line_of_sight == true",
                        "The anchored prison is outside Dispel Magic's range or clear path.",
                    ),
                ],
            ),
            _set("_dispel_succeeded", "slot_level == 9"),
            _set(
                "_dispel_checks",
                [
                    {
                        "source": "source_id",
                        "spell": "'imprisonment'",
                        "ended": "_dispel_succeeded",
                        "roll": None,
                    }
                ],
            ),
            _when("_dispel_succeeded == true", [_retire("source_id"), _refresh()]),
        ],
    )
    return tools


def extend_runtime(text):
    "Compose after recovery and before final casting/reaction wrappers, preserving their owners."
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    optional, cast_tools = _casting_tools()
    changed = (
        _projection_tools()
        | _turn_tools()
        | _target_tools()
        | cast_tools
        | _planar_tools()
        | _imprisonment_recovery_tools()
    )
    for name in ("install_srd_timed_condition_source", "remove_srd_timed_condition_effect"):
        t = deepcopy(tools[name])
        for p in t["parameters"]:
            if p["name"] == "condition":
                p["enum_values"] = list(
                    dict.fromkeys([*p["enum_values"], "restrained", "petrified"])
                )
            if p["name"] == "control_profile":
                p["enum_values"] = list(dict.fromkeys([*p["enum_values"], *SOURCE_PROFILES]))
        if name.startswith("install"):
            t["effects"] = [
                e
                for e in t["effects"]
                if "target_state.has_progressive_sources" not in str(e)
                and "lasting_state.progressive_target_ids" not in str(e)
            ]
            t["effects"].insert(
                0,
                _when(
                    (
                        "control_profile == 'contagion' or "
                        "control_profile == 'flesh_stone' or "
                        "control_profile == 'flesh_slow' or "
                        "control_profile == 'divine_word' or "
                        "control_profile == 'divine_banish' or "
                        "control_profile == 'imprisonment'"
                    ),
                    [
                        _set("target_state.has_progressive_sources", True),
                        _set("_progressive_registered", []),
                        _when(
                            "lasting_state.progressive_target_ids != null",
                            [
                                _set(
                                    "_progressive_registered",
                                    "lasting_state.progressive_target_ids",
                                )
                            ],
                        ),
                        _when(
                            "_target_state_id not in _progressive_registered",
                            [_push("_progressive_registered", "_target_state_id")],
                        ),
                        _set("lasting_state.progressive_target_ids", "_progressive_registered"),
                    ],
                ),
            )
            for condition in ("restrained", "petrified"):
                field = "target_state.timed_condition_unowned_" + condition
                if any(e.get("condition") == field + " == null" for e in t["effects"]):
                    continue
                t["effects"].insert(
                    0,
                    _when(
                        field + " == null",
                        [_set(field, "'" + condition + "' in target_state.conditions")],
                    ),
                )
        changed[name] = t
    refresh = deepcopy(tools["refresh_srd_timed_condition_target"])
    refresh["effects"] = [e for e in refresh["effects"] if "srd_progressive" not in str(e)]
    refresh["effects"].insert(
        0,
        _when(
            "target_state.has_progressive_sources == true",
            [
                _call(
                    "prepare_srd_progressive_lifetimes",
                    {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                )
            ],
        ),
    )
    refresh["effects"].append(
        _when(
            "target_state.has_progressive_sources == true",
            [
                _call(
                    "resolve_srd_progressive_projection",
                    {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                )
            ],
        )
    )
    changed["refresh_srd_timed_condition_target"] = refresh
    end_owned = deepcopy(tools["remove_srd_condition_concentration"])
    for node in _walk(end_owned["effects"]):
        if node.get("condition", "").endswith(
            "_condition_record.caster_state_id == _caster_state_id"
        ):
            node["condition"] += " and _condition_record.concentration_sequence > 0"
    changed["remove_srd_condition_concentration"] = end_owned
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    clock["effects"] = [
        e for e in clock["effects"] if "prepare_all_srd_progressive_lifetimes" not in str(e)
    ]
    tick_index = next(
        i
        for i, e in enumerate(clock["effects"])
        if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
    )
    clock["effects"].insert(
        tick_index + 1,
        _when(
            "lasting_state.progressive_target_ids != null",
            [
                _call(
                    "prepare_all_srd_progressive_lifetimes",
                    {"lasting_state": "{_lasting_state_id}"},
                )
            ],
        ),
    )
    changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(tools["resolve_srd_timed_condition_end_turn"])
    end["effects"] = [e for e in end["effects"] if "end_srd_progressive_turn" not in str(e)]
    end["effects"].append(
        _when(
            "target_state.has_progressive_sources == true",
            [
                _call(
                    "end_srd_progressive_turn",
                    {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                    ["_progressive_turn_rows"],
                ),
                _each(
                    "_progressive_turn_rows",
                    "_progressive_turn_row",
                    [_push("_timed_condition_save_details", "_progressive_turn_row")],
                ),
            ],
        )
    )
    changed["resolve_srd_timed_condition_end_turn"] = end
    removal = deepcopy(tools["remove_srd_condition"])
    ordinary = []
    for effect in removal["effects"]:
        if effect.get("condition") == "_contagion_cure_allowed == true":
            ordinary.extend(effect["on_true"])
        elif effect.get("set", {}).get("field") != "_contagion_cure_allowed" and (
            "check_srd_contagion_cure" not in str(effect)
        ):
            ordinary.append(effect)
    for condition in ("restrained", "petrified"):
        marker = "target.timed_condition_unowned_" + condition
        if marker not in str(ordinary):
            ordinary.append(
                _when(
                    "condition == '" + condition + "'",
                    [
                        _set(marker, False),
                        _call(
                            "remove_srd_timed_condition_effect",
                            {
                                "target_state": "{target}",
                                "lasting_state": "{_lasting_state_id}",
                                "condition": condition,
                            },
                        ),
                    ],
                )
            )
    removal["effects"] = [
        _set("_contagion_cure_allowed", True),
        _when(
            "condition == 'poisoned' and target.has_progressive_sources == true",
            [
                _call(
                    "check_srd_contagion_cure",
                    {"target_state": "{target}", "lasting_state": "{_lasting_state_id}"},
                    ["_contagion_cure_allowed"],
                )
            ],
        ),
        _when("_contagion_cure_allowed == true", ordinary),
    ]
    changed["remove_srd_condition"] = removal
    roll = deepcopy(tools["d20_test"])
    roll["effects"] = [
        e
        for e in roll["effects"]
        if not (
            "contagion_disadvantage_abilities" in e.get("condition", "")
            and e.get("on_true") == [_set("disadvantage", True)]
        )
    ]
    for node in _walk(roll["effects"]):
        setting = node.get("set", {})
        if (
            setting.get("field") == "_effective_disadvantage"
            and isinstance(setting.get("value"), str)
            and setting["value"].startswith("disadvantage == true")
        ):
            setting["value"] = setting["value"].split(
                " or (actor_state.has_progressive_sources", 1
            )[0] + (
                " or (actor_state.has_progressive_sources == true and kind == 'saving_throw' "
                "and actor_state.contagion_disadvantage_abilities != null and ability in "
                "actor_state.contagion_disadvantage_abilities and "
                "'poisoned' in actor_state.conditions)"
            )
    changed["d20_test"] = roll
    revival = deepcopy(tools["resolve_srd_revival"])
    if "clear_srd_magical_contagion" not in str(revival["effects"]):

        def insert_true_resurrection(value):
            if isinstance(value, list):
                for i, node in enumerate(value):
                    if (
                        isinstance(node, dict)
                        and node.get("set", {}).get("field") == "target_state.is_alive"
                        and node["set"]["value"] is True
                    ):
                        value.insert(
                            i + 1,
                            _when(
                                "spell == 'true_resurrection'",
                                [
                                    _call(
                                        "clear_srd_magical_contagion",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    )
                                ],
                            ),
                        )
                        return True
                    if insert_true_resurrection(node):
                        return True
            elif isinstance(value, dict):
                return any(insert_true_resurrection(v) for v in value.values())
            return False

        if not insert_true_resurrection(revival["effects"]):
            raise ValueError("Missing actual revival branch")
    changed["resolve_srd_revival"] = revival
    recovery = deepcopy(tools["cast_srd_recovery"])
    for node in _walk(recovery["effects"]):
        if node.get("condition") == "spell == 'dispel_magic'" and any(
            effect.get("call") == "dispel_srd_target_sources" for effect in node.get("on_true", [])
        ):
            node["on_true"] = [
                _when(
                    "recovery_choice == 'imprisonment_prison' or "
                    "recovery_choice == 'imprisonment_component'",
                    [
                        _call(
                            "dispel_srd_imprisonment_source",
                            {
                                "caster_state": "{_caster_state_id}",
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                                "source_id": "{recovery_source_id}",
                                "slot_level": "{slot_level}",
                                "choice": "{recovery_choice}",
                                "item_id": "{recovery_item}",
                            },
                            ["_dispel_checks"],
                        )
                    ],
                    node["on_true"],
                )
            ]
            break
    changed["cast_srd_recovery"] = recovery
    changed["refresh_srd_progressive_turn_targets"] = _tool(
        "Settle successful Flesh to Stone saves when the actual caster starts its next turn.",
        [_lasting()],
        [
            _set("_progressive_turn_targets", "lasting_state.progressive_target_ids"),
            _each(
                "_progressive_turn_targets",
                "_progressive_turn_target",
                [
                    _call(
                        "refresh_srd_timed_condition_target",
                        {
                            "target_state": "{_progressive_turn_target}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    beginning = deepcopy(tools["begin_combat_turn"])
    beginning["effects"] = [
        e for e in beginning["effects"] if "refresh_srd_progressive_turn_targets" not in str(e)
    ]
    if not any(p["name"] == "lasting_state" for p in beginning["parameters"]):
        beginning["parameters"].append({**_lasting(), "model_hidden": True})
    turn_index = next(
        i
        for i, e in enumerate(beginning["effects"])
        if e.get("set", {}).get("field") == "actor.turns_started"
    )
    beginning["effects"].insert(
        turn_index + 1,
        _when(
            "lasting_state.progressive_target_ids != null",
            [
                _call(
                    "refresh_srd_progressive_turn_targets", {"lasting_state": "{_lasting_state_id}"}
                ),
            ],
        ),
    )
    changed["begin_combat_turn"] = beginning
    cast = deepcopy(tools["cast_spell"])
    cast["parameters"] = [
        p for p in cast["parameters"] if p["name"] not in {p["name"] for p in optional}
    ]
    cast["parameters"].extend(optional)
    dispatch = _when(
        "_spell_cast_failed != true and _spell_definition.family == 'progressive_affliction'",
        [
            _set("_progressive_cast_targets", "targets"),
            _when(
                "_target_id != null and (targets == null or len(targets) == 0)",
                [_set("_progressive_cast_targets", ["_target_id"])],
            ),
            _call(
                "cast_srd_progressive_affliction",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                    "slot_level": "{_slot_level}",
                    "save_dc": "{_casting_profile.save_dc}",
                    "save_ability": "{_spell_definition.save_ability}",
                    "concentration_save_modifier": "{_casting_profile.concentration_save_modifier}",
                    "targets": "{_progressive_cast_targets}",
                    "material_item_ids": "{material_item_ids}",
                    **{p["name"]: "{" + p["name"] + "}" for p in optional},
                },
                ["_spell_summary_rows", "_spell_detail_rows"],
            ),
        ],
    )

    def insert(value):
        if isinstance(value, list):
            value[:] = [
                e
                for e in value
                if not (
                    isinstance(e, dict)
                    and "cast_srd_progressive_affliction" in str(e)
                    and "family == 'progressive_affliction'" in e.get("condition", "")
                )
            ]
            for i, e in enumerate(value):
                if (
                    isinstance(e, dict)
                    and "family == 'recovery'" in e.get("condition", "")
                    and "cast_srd_recovery" in str(e)
                ):
                    value.insert(i, dispatch)
                    return True
                if insert(e):
                    return True
        elif isinstance(value, dict):
            return any(insert(v) for v in value.values())
        return False

    if not insert(cast["effects"]):
        raise ValueError("Missing recovery dispatch for progressive casts")
    changed["cast_spell"] = cast
    text = _replace_tools(text, changed)
    # The ordinary D20 result card is reused by the existing skill/check public tool.
    start = text.index("  d20_test:")
    block = text[start:]
    if "&d20_result_card" not in text:
        block = block.replace("    result_card:\n", "    result_card: &d20_result_card\n", 1)
        text = text[:start] + block
    schema = yaml.dump({"progressive_affliction": _schema()}, Dumper=_Dumper, sort_keys=False)
    if "      progressive_affliction:" not in text:
        text = text.replace(
            "      recovery:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      recovery:",
            1,
        )
    return text
