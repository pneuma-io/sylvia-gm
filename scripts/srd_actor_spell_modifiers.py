"""Source-owned actor spell modifiers, condition suppression, and their consumers."""

import re
from copy import deepcopy

import yaml

if __package__:
    from . import srd_timed_actor_defenses as defense
    from .srd_runtime_fragments import _ToolFragments
else:
    import srd_timed_actor_defenses as defense
    from srd_runtime_fragments import _ToolFragments

DEFENSE_EXPORTS = defense.DEFENSE_EXPORTS
_RuntimeDumper = defense._RuntimeDumper
_entity = defense._entity
_parameter = defense._parameter
_require = defense._require
_set = defense._set
_tool = defense._tool


def _when(condition, effects, otherwise=None):
    effect = defense._when(condition, effects)
    if otherwise is not None:
        effect["on_false"] = otherwise
    return effect


def _call(name, parameters, exports=()):
    effect = {"call": name, "params": parameters}
    if exports:
        effect["export_context"] = (
            exports if isinstance(exports, dict) else {name: name for name in exports}
        )
    return effect


def _emit(text, changed):
    if not changed:
        return text
    fragments = _ToolFragments(text, any_suffix=True)
    for name, tool in changed.items():
        tool["effects"] = _tool("", tool["parameters"], tool["effects"])["effects"]
        for parameter in tool["parameters"]:
            if parameter["name"] == "lasting_state" and parameter.get("default") == "state":
                parameter["model_hidden"] = True
        _normalize_call_expressions(tool)
        fragment = yaml.dump({name: tool}, Dumper=_RuntimeDumper, sort_keys=False, width=100)
        fragment = "".join(
            "  " + line if line.strip() else line for line in fragment.splitlines(True)
        )
        prior = fragments.prior(name)
        anchor = re.search(r"      result_card: &(\w+)", prior) if prior else None
        if anchor:
            fragment = fragment.replace(
                "      result_card:\n", f"      result_card: &{anchor[1]}\n", 1
            )
        if name == "cast_spell":
            fragment = fragment.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
            start = fragment.index("    - name: spell\n")
            end = fragment.index("    - name:", start + 1)
            parameter = fragment[start:end]
            enum_start = parameter.index("      enum_values:\n") + len("      enum_values:\n")
            enum_end = parameter.index("      required:", enum_start)
            choices = next(p["enum_values"] for p in tool["parameters"] if p["name"] == "spell")
            parameter = (
                parameter[:enum_start]
                + "        # BEGIN GENERATED SPELL SELECTOR\n"
                + "".join(f"          - {choice}\n" for choice in choices)
                + "        # END GENERATED SPELL SELECTOR\n"
                + parameter[enum_end:]
            )
            fragment = fragment[:start] + parameter + fragment[end:]
        fragments.replace(name, fragment)
    return fragments.render()


def _normalize_call_expressions(tool):
    "Evaluate call expressions explicitly; runtime parameter templates accept field paths only."
    sequence = 0

    def edit(effects):
        nonlocal sequence
        result = []
        for effect in effects:
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = edit(effect[branch])
            if "for_each" in effect:
                effect["for_each"]["do"] = edit(effect["for_each"]["do"])
            setters = []
            if "call" in effect:
                for key, value in effect.get("params", {}).items():
                    if isinstance(value, str) and re.fullmatch(r"\{[^{}]+\}", value):
                        expression = value[1:-1]
                        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", expression):
                            sequence += 1
                            field = f"_actor_call_expression_{sequence}"
                            setters.append(_set(field, expression))
                            effect["params"][key] = "{" + field + "}"
            if setters:
                result.extend(
                    [_when(effect["condition"], setters)] if "condition" in effect else setters
                )
            result.append(effect)
        return result

    tool["effects"] = edit(tool["effects"])


MODIFIER_EXPORTS = (
    "_actor_modifier_immunities",
    "_actor_modifier_wisdom_advantage",
    "_actor_modifier_death_advantage",
    "_actor_modifier_maximum_healing",
    "_actor_modifier_freedom",
    "_actor_modifier_strength_disadvantage",
    "_actor_modifier_damage_penalty",
    "_actor_modifier_sanctuary_dc",
    "_actor_modifier_sanctuary_source",
)


def _modifier_schema():
    properties = {
        "casting_time": {"type": "string", "enum": ["action", "bonus_action", "1_minute"]},
        "base_level": {"type": "integer", "minimum": 0, "maximum": 9},
        "range_kind": {"type": "string", "enum": ["touch", "distance", "self"]},
        "range_feet": {"type": "integer", "minimum": 0, "maximum": 1000},
        "requires_willing": {"type": "boolean"},
        "duration_rounds": {"type": "integer", "minimum": 1, "maximum": 14400},
        "concentration": {"type": "boolean"},
        "base_targets": {"type": "integer", "minimum": 1, "maximum": 64},
        "targets_per_slot_level": {"type": "integer", "minimum": 0, "maximum": 1},
        "profile": {
            "type": "string",
            "enum": [
                "calm",
                "beacon",
                "freedom",
                "enfeeblement",
                "sanctuary",
                "foresight",
                "regenerate",
                "glibness",
                "divine_favor",
                "magic_weapon",
                "shillelagh",
                "enlarge_reduce",
                "haste",
                "slow",
                "fire_shield",
            ],
        },
        "save_ability": {"type": "string", "enum": ["none", "charisma", "constitution", "wisdom"]},
        "required_creature_type": {"type": "string", "enum": ["any", "humanoid"]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _modifier_tools():
    active = _tool(
        "Read one actor modifier's exact concentration instance and expiry.",
        [
            _entity("target_state"),
            _entity("source_state"),
            _entity("lasting_state"),
            _parameter("spell"),
            _parameter("started_tick", "number"),
            _parameter("expires_tick", "number"),
            _parameter("concentration", "boolean"),
        ],
        [
            _set(
                "_actor_modifier_active",
                (
                    "target_state.is_alive == true and lasting_state.la"
                    "sting_tick != null and lasting_state.lasting_tick "
                    "+ 0.000000001 < expires_tick"
                ),
            ),
            _when(
                "concentration == true",
                [
                    _set(
                        "_actor_modifier_active",
                        (
                            "_actor_modifier_active == true and source_state.co"
                            "ncentration_spell_id == spell and source_state.con"
                            "centration_effect_family == 'timed_actor_modifier'"
                            " and source_state.concentration_started_tick == st"
                            "arted_tick and source_state.concentration_target_s"
                            "tate_ids != null and _target_state_id in source_st"
                            "ate.concentration_target_state_ids"
                        ),
                    ),
                    _set(
                        "_actor_modifier_active",
                        (
                            "_actor_modifier_active == true and source_state.is"
                            "_alive == true and not ('incapacitated' in source_"
                            "state.conditions or 'paralyzed' in source_state.co"
                            "nditions or 'petrified' in source_state.conditions"
                            " or 'stunned' in source_state.conditions or 'uncon"
                            "scious' in source_state.conditions)"
                        ),
                    ),
                ],
            ),
        ],
    )
    resolve = _tool(
        "Derive active nonstacking actor modifiers from source-owned records.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_actor_modifier_immunities", []),
            *[_set(name, False) for name in MODIFIER_EXPORTS[1:6]],
            _set("_actor_modifier_damage_penalty", False),
            _set("_actor_modifier_sanctuary_dc", 0),
            _set("_actor_modifier_sanctuary_source", None),
            _set("_actor_modifier_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_actor_modifier_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_actor_modifier_records",
                    "as": "_actor_modifier_record",
                    "do": [
                        _when(
                            (
                                "_actor_modifier_record.kind == 'actor_spell_modifi"
                                "er' and _actor_modifier_record.target == _target_s"
                                "tate_id"
                            ),
                            [
                                _call(
                                    "check_srd_actor_spell_modifier_active",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "source_state": "{_actor_modifier_record.caster}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "spell": "{_actor_modifier_record.spell}",
                                        "started_tick": "{_actor_modifier_record.started_tick}",
                                        "expires_tick": "{_actor_modifier_record.expires_tick}",
                                        "concentration": "{_actor_modifier_record.concentration}",
                                    },
                                    ["_actor_modifier_active"],
                                ),
                                _when(
                                    "_actor_modifier_active == true",
                                    [
                                        _when(
                                            ("_actor_modifier_record.mode == 'calm_immunity'"),
                                            [
                                                _set(
                                                    "_actor_modifier_immunities",
                                                    ["charmed", "frightened"],
                                                )
                                            ],
                                        ),
                                        _when(
                                            "_actor_modifier_record.mode == 'beacon'",
                                            [
                                                _set("_actor_modifier_wisdom_advantage", True),
                                                _set("_actor_modifier_death_advantage", True),
                                                _set("_actor_modifier_maximum_healing", True),
                                            ],
                                        ),
                                        _when(
                                            "_actor_modifier_record.mode == 'freedom'",
                                            [_set("_actor_modifier_freedom", True)],
                                        ),
                                        _when(
                                            (
                                                "_actor_modifier_record.mode == "
                                                "'enfeeblement_failed'"
                                            ),
                                            [
                                                _set(
                                                    ("_actor_modifier_strength_disadvantage"), True
                                                ),
                                                _set("_actor_modifier_damage_penalty", True),
                                            ],
                                        ),
                                        _when(
                                            ("_actor_modifier_record.mode == 'sanctuary'"),
                                            [
                                                _when(
                                                    (
                                                        "_actor_modifier_record.save_"
                                                        "dc >= "
                                                        "_actor_modifier_sanctuary_dc"
                                                    ),
                                                    [
                                                        _set(
                                                            ("_actor_modifier_sanctuary_source"),
                                                            "_actor_modifier_record",
                                                        ),
                                                    ],
                                                ),
                                                _set(
                                                    "_actor_modifier_sanctuary_dc",
                                                    (
                                                        "max(_actor_modifier_sanctuar"
                                                        "y_dc, "
                                                        "_actor_modifier_record.save_"
                                                        "dc)"
                                                    ),
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                }
            },
        ],
    )
    install = _tool(
        "Install one fixed actor spell record without replacing unrelated effects.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _entity("lasting_state"),
            _parameter("spell"),
            _parameter("mode"),
            _parameter("save_dc", "integer"),
            _parameter("duration_rounds", "integer"),
            _parameter("concentration", "boolean"),
            {
                "name": "toward_ids",
                "type": "array",
                "required": False,
                "default": [],
                "items": {"type": "string"},
            },
            {
                "name": "ally_ids",
                "type": "array",
                "required": False,
                "default": [],
                "items": {"type": "string"},
            },
        ],
        [
            _set("_modifier_retained_records", []),
            _set("_modifier_existing_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_modifier_existing_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_modifier_existing_records",
                    "as": "_modifier_existing_record",
                    "do": [
                        _when(
                            (
                                "not (_modifier_existing_record.kind == 'actor_spel"
                                "l_modifier' and _modifier_existing_record.spell =="
                                " spell and _modifier_existing_record.caster == _ca"
                                "ster_state_id and _modifier_existing_record.target"
                                " == _target_state_id)"
                            ),
                            [
                                {
                                    "list_push": {
                                        "list": "_modifier_retained_records",
                                        "value": "_modifier_existing_record",
                                    }
                                },
                            ],
                        ),
                    ],
                }
            },
            _require(
                "len(_modifier_retained_records) < 128",
                ("Resolve existing lasting effects before adding another actor modifier."),
            ),
            {
                "list_push": {
                    "list": "_modifier_retained_records",
                    "value": {
                        "spell": "spell",
                        "label": "spell",
                        "kind": "'actor_spell_modifier'",
                        "caster": "_caster_state_id",
                        "target": "_target_state_id",
                        "mode": "mode",
                        "owner": "'__time_only__'",
                        "phase": "'time'",
                        "turn": 1000000000,
                        "started_tick": "lasting_state.lasting_tick",
                        "expires_tick": "lasting_state.lasting_tick + duration_rounds * 2",
                        "concentration": "concentration",
                        "save_dc": "save_dc",
                        "toward_ids": "toward_ids",
                        "ally_ids": "ally_ids",
                        "magnitude": 0,
                        "dice_count": 0,
                        "damage_die": "''",
                        "damage_type": "''",
                    },
                }
            },
            _set("lasting_state.turn_effects", "_modifier_retained_records"),
            _set("target_state.has_actor_spell_modifiers", True),
            _call(
                "register_srd_actor_spell_projection_target",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _call(
                "refresh_srd_timed_condition_target",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
        ],
    )
    return {
        "check_srd_actor_spell_modifier_active": active,
        "resolve_srd_actor_spell_modifiers": resolve,
        "install_srd_actor_spell_modifier": install,
    }


def _cast_modifier_tool():
    return _tool(
        (
            "Validate fixed actor spell targeting, resolve saves, and install "
            "actual later modifiers."
        ),
        [
            _entity("caster_state"),
            _entity("lasting_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            {"name": "concentration_save_modifier", "type": "integer", "required": False},
            _parameter("targets", "array", items={"type": "string"}),
            _parameter("targets_max_distance", "integer"),
            _parameter("all_targets_willing", "boolean"),
            _parameter("caster_can_touch_all_targets", "boolean"),
            _parameter("all_targets_in_area", "boolean"),
            _parameter("origin_distance", "integer"),
            _parameter("calm_indifference_targets", "array", items={"type": "string"}),
            _parameter("calm_indifferent_toward", "array", items={"type": "string"}),
            _parameter("calm_allies", "array", items={"type": "string"}),
            _parameter("forego_save_targets", "array", items={"type": "string"}),
        ],
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_modifier_cast_profile",
            },
            _require(
                (
                    "len(targets) >= 1 and len(targets) <= _modifier_ca"
                    "st_profile.base_targets + (slot_level - _modifier_"
                    "cast_profile.base_level) * _modifier_cast_profile."
                    "targets_per_slot_level"
                ),
                "The actor spell's target count exceeds the selected slot.",
            ),
            _require(
                (
                    "(_modifier_cast_profile.range_kind == 'touch' and "
                    "caster_can_touch_all_targets == true) or (_modifie"
                    "r_cast_profile.range_kind == 'distance' and (_modi"
                    "fier_cast_profile.profile == 'calm' or targets_max"
                    "_distance <= _modifier_cast_profile.range_feet))"
                ),
                "Selected actor spell targets must satisfy its fixed range.",
            ),
            _require(
                ("_modifier_cast_profile.requires_willing != true or all_targets_willing == true"),
                "Every touched creature must be willing.",
            ),
            _require(
                (
                    "_modifier_cast_profile.profile != 'calm' or (all_t"
                    "argets_in_area == true and origin_distance <= _mod"
                    "ifier_cast_profile.range_feet)"
                ),
                (
                    "Calm Emotions needs the Humanoids in its chosen 20"
                    "-foot-radius Sphere and an origin within 60 feet."
                ),
            ),
            _set("_modifier_distinct_targets", []),
            {
                "for_each": {
                    "collection": "targets",
                    "as": "_modifier_cast_target",
                    "do": [
                        _require(
                            "_modifier_cast_target not in _modifier_distinct_targets",
                            "Select an actor spell target only once.",
                        ),
                        {
                            "list_push": {
                                "list": "_modifier_distinct_targets",
                                "value": "_modifier_cast_target",
                            }
                        },
                        _call(
                            "validate_srd_actor_modifier_target",
                            {
                                "target_state": "{_modifier_cast_target}",
                                "profile": "{_modifier_cast_profile.profile}",
                                "save_ability": "{_modifier_cast_profile.save_ability}",
                            },
                        ),
                    ],
                }
            },
            {
                "for_each": {
                    "collection": "calm_indifference_targets",
                    "as": "_calm_choice_target",
                    "do": [
                        _require(
                            (
                                "_modifier_cast_profile.profile == 'calm' and _calm"
                                "_choice_target in targets"
                            ),
                            "A Calm Emotions choice must name one selected target.",
                        ),
                    ],
                }
            },
            _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)]),
            _when(
                "_modifier_cast_profile.concentration == true",
                [
                    _require(
                        "concentration_save_modifier != null",
                        (
                            "The concentrating caster needs an authoritative Co"
                            "nstitution save modifier."
                        ),
                    ),
                    _when(
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
                    _set("caster_state.concentration_spell_id", "spell"),
                    _set("caster_state.concentration_effect_family", "'timed_actor_modifier'"),
                    _set("caster_state.concentration_target_state_id", "targets[0]"),
                    _set("caster_state.concentration_target_state_ids", "targets"),
                    _set("caster_state.concentration_started_tick", "lasting_state.lasting_tick"),
                    _set(
                        "caster_state.concentration_expires_tick",
                        ("lasting_state.lasting_tick + _modifier_cast_profile.duration_rounds * 2"),
                    ),
                    _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
                ],
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            {
                "for_each": {
                    "collection": "targets",
                    "as": "_modifier_cast_target",
                    "do": [
                        _call(
                            "resolve_srd_actor_modifier_target_cast",
                            {
                                "target_state": "{_modifier_cast_target}",
                                "caster_state": "{_caster_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                                "spell": "{spell}",
                                "save_dc": "{save_dc}",
                                "indifferent": (
                                    "{_modifier_cast_target in calm_indifference_targets}"
                                ),
                                "toward_ids": "{calm_indifferent_toward}",
                                "ally_ids": "{calm_allies}",
                                "forego_save": "{_modifier_cast_target in forego_save_targets}",
                            },
                            ["_modifier_target_outcome"],
                        ),
                        _call(
                            "record_srd_spell_target_result",
                            {
                                "target": "{_modifier_cast_target}",
                                "brief": "{spell}: {_modifier_target_outcome}",
                                "detail": (
                                    "{spell}: {_modifier_target_outcome}; "
                                    "source-owned "
                                    "effect lasts "
                                    "{_modifier_cast_profile.duration_roun"
                                    "ds} rounds unless its printed ending condition "
                                    "app"
                                    "lies."
                                ),
                            },
                            ["_spell_target_summary", "_spell_target_detail"],
                        ),
                        {
                            "list_push": {
                                "list": "_spell_summary_rows",
                                "value": "_spell_target_summary",
                            }
                        },
                        {
                            "list_push": {
                                "list": "_spell_detail_rows",
                                "value": "_spell_target_detail",
                            }
                        },
                    ],
                }
            },
        ],
    )


def _target_cast_tools():
    validate = _tool(
        ("Require authoritative living-creature facts before installing an actor modifier."),
        [_entity("target_state"), _parameter("profile"), _parameter("save_ability")],
        [
            _require("target_state.is_alive == true", "Actor modifiers need a living creature."),
            _require(
                "profile != 'calm' or target_state.creature_type != null",
                "Calm Emotions needs each target's authoritative creature type.",
            ),
            _require(
                (
                    "save_ability == 'none' or (target_state.saving_thr"
                    "ow_modifiers != null and target_state.saving_throw"
                    "_modifiers[save_ability] != null)"
                ),
                ("Actor spell saves need the target's authoritative saving throw modifier."),
            ),
        ],
    )
    target = _tool(
        "Resolve one source-defined actor spell save and its chosen fixed effect.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _entity("lasting_state"),
            _parameter("spell"),
            _parameter("save_dc", "integer"),
            _parameter("indifferent", "boolean"),
            _parameter("toward_ids", "array", items={"type": "string"}),
            _parameter("ally_ids", "array", items={"type": "string"}),
            _parameter("forego_save", "boolean"),
        ],
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_modifier_target_profile",
            },
            _set("_modifier_target_outcome", "'active'"),
            _set("_modifier_install_mode", "_modifier_target_profile.profile"),
            _set(
                "_modifier_target_eligible",
                (
                    "_modifier_target_profile.required_creature_type =="
                    " 'any' or target_state.creature_type == _modifier_"
                    "target_profile.required_creature_type"
                ),
            ),
            _when(
                "_modifier_target_eligible != true",
                [_set("_modifier_target_outcome", "'not affected: creature type'")],
            ),
            _set("_modifier_cast_save_outcome", "'failure'"),
            _when(
                (
                    "_modifier_target_eligible == true and _modifier_ta"
                    "rget_profile.save_ability != 'none'"
                ),
                [
                    _call(
                        "d20_test",
                        {
                            "actor_state": "{_target_state_id}",
                            "ability": "{_modifier_target_profile.save_ability}",
                            "kind": "saving_throw",
                            "ability_modifier": (
                                "{target_state.saving_throw_modifiers[_modifier_tar"
                                "get_profile.save_ability]}"
                            ),
                            "proficiency_bonus": 0,
                            "proficiency_factor": "none",
                            "circumstantial_modifier": 0,
                            "target_number": "{save_dc}",
                            "advantage": False,
                            "disadvantage": False,
                            "forego_save": "{forego_save}",
                        },
                        {"_modifier_cast_save_outcome": "_outcome"},
                    ),
                    _set("_modifier_target_outcome", "_modifier_cast_save_outcome"),
                ],
            ),
            _when(
                "_modifier_target_profile.profile == 'calm'",
                [
                    _set("_modifier_install_mode", "'calm_immunity'"),
                    _when(
                        "indifferent == true",
                        [_set("_modifier_install_mode", "'calm_indifference'")],
                    ),
                ],
            ),
            _when(
                "_modifier_target_profile.profile == 'enfeeblement'",
                [
                    _set("_modifier_install_mode", "'enfeeblement_failed'"),
                    _when(
                        "_modifier_cast_save_outcome == 'success'",
                        [_set("_modifier_install_mode", "'enfeeblement_success'")],
                    ),
                ],
            ),
            _when(
                (
                    "_modifier_target_eligible == true and (_modifier_c"
                    "ast_save_outcome == 'failure' or _modifier_target_"
                    "profile.profile == 'enfeeblement')"
                ),
                [
                    _call(
                        "install_srd_actor_spell_modifier",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "spell": "{spell}",
                            "mode": "{_modifier_install_mode}",
                            "save_dc": "{save_dc}",
                            "duration_rounds": "{_modifier_target_profile.duration_rounds}",
                            "concentration": "{_modifier_target_profile.concentration}",
                            "toward_ids": "{toward_ids}",
                            "ally_ids": "{ally_ids}",
                        },
                    ),
                    _when(
                        "_modifier_install_mode == 'enfeeblement_success'",
                        [
                            _call(
                                "install_srd_turn_effect",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "spell": "{spell}",
                                    "effect_label": "Ray of Enfeeblement",
                                    "kind": "next_attack_disadvantage",
                                    "expiry_owner": "caster",
                                    "expiry_phase": "start",
                                    "magnitude": 0,
                                    "dice_count": 0,
                                    "damage_die": "d8",
                                    "damage_type": "necrotic",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "validate_srd_actor_modifier_target": validate,
        "resolve_srd_actor_modifier_target_cast": target,
    }


def _projection_tools():
    register = _tool(
        ("Index targets whose actor spells must reproject after expiry or concentration loss."),
        [_entity("target_state"), _entity("lasting_state")],
        [
            _when(
                "lasting_state.actor_spell_projection_target_ids == null",
                [_set("lasting_state.actor_spell_projection_target_ids", [])],
            ),
            _set("_actor_projection_ids", "lasting_state.actor_spell_projection_target_ids"),
            _when(
                "_target_state_id not in _actor_projection_ids",
                [
                    _require(
                        "len(_actor_projection_ids) < 128",
                        ("Resolve existing actor spell effects before adding more targets."),
                    ),
                    {"list_push": {"list": "_actor_projection_ids", "value": "_target_state_id"}},
                    _set(
                        "lasting_state.actor_spell_projection_target_ids", "_actor_projection_ids"
                    ),
                ],
            ),
        ],
    )
    suppression = _tool(
        ("Suppress effective conditions while preserving independent unowned and timed sources."),
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_actor_defense_condition_immunities", []),
            _when(
                "target_state.has_timed_actor_defenses == true",
                [
                    _call(
                        "resolve_srd_timed_actor_defenses",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        DEFENSE_EXPORTS,
                    )
                ],
            ),
        ],
    )
    suppression["effects"].extend(
        [
            _set("_actor_modifier_immunities", []),
            _set("_actor_modifier_freedom", False),
            _set("_actor_modifier_wisdom_advantage", False),
            _set("_actor_modifier_strength_disadvantage", False),
            _when(
                "target_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "resolve_srd_actor_spell_modifiers",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        MODIFIER_EXPORTS,
                    ),
                ],
            ),
            _set("target_state.spell_freedom_of_movement", "_actor_modifier_freedom"),
            _set("target_state.spell_beacon_of_hope", "_actor_modifier_wisdom_advantage"),
            _set("target_state.spell_ray_of_enfeeblement", "_actor_modifier_strength_disadvantage"),
        ]
    )
    for condition in ("charmed", "frightened"):
        unowned = f"target_state.timed_condition_unowned_{condition}"
        owned = f"target_state.timed_condition_{condition}"
        prior = f"target_state.actor_spell_suppressed_{condition}"
        suppression["effects"].extend(
            [
                _set(
                    "_suppress_actor_condition",
                    (
                        f"'{condition}' in _actor_defense_condition_immunities "
                        f"or '{condition}' in _actor_modifier_immunities"
                    ),
                ),
                _when(
                    "_suppress_actor_condition == true",
                    [
                        _when(
                            f"{unowned} == null",
                            [
                                _set(
                                    unowned,
                                    f"'{condition}' in target_state.conditions and {owned} != true",
                                )
                            ],
                        ),
                        {"remove_condition": {"target": "target_state", "condition": condition}},
                    ],
                ),
                _when(
                    (
                        f"_suppress_actor_condition != true and {prior} == true "
                        f"and ({unowned} == true or {owned} == true)"
                    ),
                    [{"apply_condition": {"target": "target_state", "condition": condition}}],
                ),
                _set(prior, "_suppress_actor_condition"),
                _set(
                    f"target_state.actor_spell_underlying_{condition}",
                    f"_suppress_actor_condition == true and ({unowned} == true or {owned} == true)",
                ),
            ]
        )
    suppression["effects"].append(
        _when(
            "target_state.actor_spell_suppressed_charmed == true",
            [
                _set("target_state.timed_charm_caster_ids", []),
                _set("target_state.timed_friendly_caster_ids", []),
            ],
        )
    )
    suppression["effects"].extend(
        [
            _set("_actor_suppression_source_ids", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [
                    _set(
                        "_actor_suppression_source_ids", "target_state.timed_condition_source_ids"
                    ),
                ],
            ),
            {
                "for_each": {
                    "collection": "_actor_suppression_source_ids",
                    "as": "_actor_suppression_source_id",
                    "do": [
                        _set(
                            "_actor_suppression_source",
                            ("lasting_state.timed_condition_sources[_actor_suppression_source_id]"),
                        ),
                        _when(
                            (
                                "_actor_suppression_source != null and _actor_suppr"
                                "ession_source.installed == true"
                            ),
                            [
                                _set(
                                    (
                                        "lasting_state.timed_condition_sources.{_acto"
                                        "r_supp"
                                        "ression_source_id}.suppressed"
                                    ),
                                    (
                                        "(_actor_suppression_source.condition == "
                                        "'charmed' "
                                        "and "
                                        "target_state.actor_spell_suppressed_charmed "
                                        "=="
                                        " true) or "
                                        "(_actor_suppression_source.condition == "
                                        "'frightened' and "
                                        "target_state.actor_spell_suppress"
                                        "ed_frightened == true)"
                                    ),
                                ),
                            ],
                        ),
                    ],
                }
            },
        ]
    )
    refresh = _tool(
        ("Reproject indexed actor spell targets after duration or concentration changes."),
        [_entity("lasting_state")],
        [
            _set("_actor_projection_targets", []),
            _when(
                "lasting_state.actor_spell_projection_target_ids != null",
                [
                    _set(
                        "_actor_projection_targets",
                        "lasting_state.actor_spell_projection_target_ids",
                    )
                ],
            ),
            {
                "for_each": {
                    "collection": "_actor_projection_targets",
                    "as": "_actor_projection_target",
                    "do": [
                        _call(
                            "refresh_srd_timed_condition_target",
                            {
                                "target_state": "{_actor_projection_target}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        )
                    ],
                }
            },
        ],
    )
    return {
        "register_srd_actor_spell_projection_target": register,
        "resolve_srd_timed_actor_condition_suppression": suppression,
        "refresh_srd_actor_spell_projections": refresh,
    }


def extend_runtime(text):
    """Compose modifier owners and idempotent hooks over other spell family generators."""
    runtime = defense._load_runtime(text)
    tools = runtime["tools"]
    for effect in tools["cast_spell"]["effects"]:
        clause = effect.get("condition", "")
        prefix = "_spell_cast_failed != true and ("
        if clause.startswith(prefix) and clause.endswith(")"):
            effect["condition"] = clause[len(prefix) : -1]
    changed = _projection_tools()
    changed["check_srd_actor_spell_light"] = _actor_spell_light_tool()
    changed.update(_modifier_tools())
    changed.update(_target_cast_tools())
    changed["cast_spell_timed_actor_modifier"] = _cast_modifier_tool()
    changed.update(_consumer_tools())
    _compose_consumers(tools, changed)
    _compose_healing_owners(tools, changed)
    _compose_remaining_damage_owners(tools, changed)
    _compose_projectile_damage_owner(tools, changed)
    _compose_freedom_owners(tools, changed)
    changed.update(_sanctuary_pending_tools())
    _compose_sanctuary_damage_owners(tools, changed)
    _compose_calm_owners(tools, changed)
    _compose_advanced_actor_modifiers(tools, changed)
    _compose_foresight_consumers(tools, changed)
    _compose_fire_shield_consumers(tools, changed)
    _compose_regeneration_consumers(tools, changed)
    _compose_divine_favor_consumers(tools, changed)
    _compose_weapon_spell_consumers(tools, changed)
    _compose_temporal_spell_consumers(tools, changed)
    _compose_slow_repeat_save(tools, changed)
    _compose_size_spell_consumers(tools, changed)
    _compose_mind_control_suppression(changed)
    _gate_advanced_projection(changed)
    project = deepcopy(tools["refresh_srd_timed_condition_target"])
    hook = _call(
        "resolve_srd_timed_actor_condition_suppression",
        {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
    )
    project["effects"] = [
        effect
        for effect in project["effects"]
        if effect.get("call") != "resolve_srd_timed_actor_condition_suppression"
    ]
    index = next(
        (
            i
            for i, effect in enumerate(project["effects"])
            if effect.get("call") == "resolve_srd_control_behavior_projection"
        ),
        len(project["effects"]),
    )
    project["effects"].insert(index, hook)
    changed["refresh_srd_timed_condition_target"] = project
    install = deepcopy(tools["install_srd_timed_actor_defense"])
    if "register_srd_actor_spell_projection_target" not in str(install["effects"]):
        install["effects"].extend(
            [
                _call(
                    "register_srd_actor_spell_projection_target",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                ),
                _call(
                    "refresh_srd_timed_condition_target",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                ),
            ]
        )
    changed["install_srd_timed_actor_defense"] = install
    for name in (
        "process_srd_turn_effects",
        "end_srd_concentration_node",
        "end_srd_concentration_voluntarily",
    ):
        tool = deepcopy(changed.get(name, tools[name]))
        if (
            name == "end_srd_concentration_node"
            and "retire_srd_actor_spell_concentration" not in str(tool["effects"])
        ):
            tool["effects"].insert(
                0,
                _when(
                    "state.actor_spell_projection_target_ids != null",
                    [
                        _call(
                            "retire_srd_actor_spell_concentration",
                            {"lasting_state": "state", "caster_state_id": "{_source_state_id}"},
                        )
                    ],
                ),
            )
        if "refresh_srd_actor_spell_projections" not in str(tool["effects"]):
            # The index is absent on ordinary campaigns, retaining the inexpensive inactive path.
            tool["effects"].append(
                _when(
                    "state.actor_spell_projection_target_ids != null",
                    [_call("refresh_srd_actor_spell_projections", {"lasting_state": "state"})],
                )
            )
        changed[name] = tool

    text = _emit(text, changed)
    schema = yaml.dump(
        {"timed_actor_modifier": _modifier_schema()}, Dumper=_RuntimeDumper, sort_keys=False
    )
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      timed_actor_modifier:" not in text:
        text = text.replace(
            "      timed_actor_defense:", schema + "\n      timed_actor_defense:", 1
        )
    else:
        text = re.sub(
            r"(?ms)^      timed_actor_modifier:\n.*?(?=^      [a-zA-Z_][^\n]*:\n)",
            lambda _: schema,
            text,
            count=1,
        )
    return text


def _consumer_tools():
    retire = _tool(
        ("Retire one caster's concentration actor records before reprojecting saved targets."),
        [_entity("lasting_state"), _parameter("caster_state_id")],
        [
            _set("_actor_retained_records", []),
            _set("_actor_retire_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_actor_retire_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_actor_retire_records",
                    "as": "_actor_retire_record",
                    "do": [
                        _when(
                            (
                                "not ((_actor_retire_record.kind == 'timed_actor_de"
                                "fense' or _actor_retire_record.kind == 'actor_spel"
                                "l_modifier') and _actor_retire_record.caster == ca"
                                "ster_state_id and _actor_retire_record.concentrati"
                                "on == true)"
                            ),
                            [
                                {
                                    "list_push": {
                                        "list": "_actor_retained_records",
                                        "value": "_actor_retire_record",
                                    }
                                },
                            ],
                        ),
                    ],
                }
            },
            _set("lasting_state.turn_effects", "_actor_retained_records"),
        ],
    )
    sanctuary_end = _tool(
        (
            "End every Sanctuary on a creature that makes an at"
            "tack roll, casts a spell, or deals damage."
        ),
        [_entity("actor_state"), _entity("lasting_state")],
        [
            _set("_sanctuary_kept_records", []),
            _set("_sanctuary_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_sanctuary_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_sanctuary_records",
                    "as": "_sanctuary_record",
                    "do": [
                        _when(
                            (
                                "not (_sanctuary_record.kind == 'actor_spell_modifi"
                                "er' and _sanctuary_record.mode == 'sanctuary' and "
                                "_sanctuary_record.target == _actor_state_id)"
                            ),
                            [
                                {
                                    "list_push": {
                                        "list": "_sanctuary_kept_records",
                                        "value": "_sanctuary_record",
                                    }
                                },
                            ],
                        ),
                    ],
                }
            },
            _set("lasting_state.turn_effects", "_sanctuary_kept_records"),
        ],
    )
    sanctuary = _tool(
        (
            "Resolve the attacker's Wisdom save before a target"
            "ed attack; unresolved retargeting fails clearly."
        ),
        [_entity("actor_state"), _entity("target_state"), _entity("lasting_state")],
        [
            _call(
                "resolve_srd_actor_spell_modifiers",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ["_actor_modifier_sanctuary_dc"],
            ),
            _when(
                "_actor_modifier_sanctuary_dc > 0",
                [
                    _require(
                        (
                            "actor_state.saving_throw_modifiers != null and act"
                            "or_state.saving_throw_modifiers.wisdom != null"
                        ),
                        (
                            "Sanctuary needs the attacker's authoritative Wisdo"
                            "m saving throw modifier."
                        ),
                    ),
                    _call(
                        "d20_test",
                        {
                            "actor_state": "{_actor_state_id}",
                            "ability": "wisdom",
                            "kind": "saving_throw",
                            "ability_modifier": "{actor_state.saving_throw_modifiers.wisdom}",
                            "proficiency_bonus": 0,
                            "proficiency_factor": "none",
                            "circumstantial_modifier": 0,
                            "target_number": "{_actor_modifier_sanctuary_dc}",
                            "advantage": False,
                            "disadvantage": False,
                        },
                        {"_sanctuary_save_outcome": "_outcome"},
                    ),
                    _require(
                        "_sanctuary_save_outcome == 'success'",
                        (
                            "Sanctuary prevents targeting this creature. This b"
                            "oundary cannot yet commit losing the attack or cho"
                            "osing another target; resolve that choice before r"
                            "etrying."
                        ),
                    ),
                ],
            ),
        ],
    )
    repeat = _tool(
        "Resolve one Ray of Enfeeblement target's end-turn Constitution saving throw.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _entity("lasting_state"),
            _parameter("save_dc", "integer"),
            _parameter("started_tick", "number"),
        ],
        [
            _call(
                "check_srd_actor_spell_modifier_active",
                {
                    "target_state": "{_target_state_id}",
                    "source_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "spell": "ray_of_enfeeblement",
                    "started_tick": "{started_tick}",
                    "expires_tick": "{caster_state.concentration_expires_tick}",
                    "concentration": True,
                },
                ["_actor_modifier_active"],
            ),
            _when(
                "_actor_modifier_active == true",
                [
                    _require(
                        (
                            "target_state.saving_throw_modifiers != null and ta"
                            "rget_state.saving_throw_modifiers.constitution != "
                            "null"
                        ),
                        ("Ray of Enfeeblement needs the target's authoritative Constitution save."),
                    ),
                    _call(
                        "d20_test",
                        {
                            "actor_state": "{_target_state_id}",
                            "ability": "constitution",
                            "kind": "saving_throw",
                            "ability_modifier": (
                                "{target_state.saving_throw_modifiers.constitution}"
                            ),
                            "proficiency_bonus": 0,
                            "proficiency_factor": "none",
                            "circumstantial_modifier": 0,
                            "target_number": "{save_dc}",
                            "advantage": False,
                            "disadvantage": False,
                        },
                        {"_enfeeblement_repeat_outcome": "_outcome"},
                    ),
                    _when(
                        "_enfeeblement_repeat_outcome == 'success'",
                        [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    penalty = _tool(
        (
            "Subtract one Ray of Enfeeblement d8 from one alrea"
            "dy modified damage roll, before mitigation."
        ),
        [_entity("actor_state"), _entity("lasting_state"), _parameter("amount", "integer")],
        [
            _set("_actor_modified_damage", "max(0, amount)"),
            _call(
                "resolve_srd_actor_spell_modifiers",
                {
                    "target_state": "{_actor_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ["_actor_modifier_damage_penalty"],
            ),
            _when(
                "_actor_modifier_damage_penalty == true",
                [
                    {"dice": {"expression": "d8"}, "store_as": "_enfeeblement_penalty_roll"},
                    _set(
                        "_actor_modified_damage",
                        "max(0, amount - _enfeeblement_penalty_roll.total)",
                    ),
                ],
            ),
        ],
    )
    return {
        "retire_srd_actor_spell_concentration": retire,
        "end_srd_sanctuary_for_actor": sanctuary_end,
        "check_srd_sanctuary_target": sanctuary,
        "repeat_srd_enfeeblement_save": repeat,
        "modify_srd_actor_damage_roll": penalty,
        "check_srd_mind_blank_information": _mind_blank_information_tool(),
    }


MIND_BLANK_QUERY_KINDS = (
    "sensing_emotions",
    "sensing_alignment",
    "reading_thoughts",
    "magical_location",
    "remote_observation",
    "information_gathering",
    "mind_control",
)


def _compose_healing_owners(tools, changed):
    "Pass a roll's possible maximum to the shared HP owner, preserving mixed recipients."
    restore = deepcopy(changed.get("restore_hit_points", tools["restore_hit_points"]))
    if not any(p["name"] == "maximum_amount" for p in restore["parameters"]):
        restore["parameters"].append(
            {"name": "maximum_amount", "type": "integer", "required": False, "default": None}
        )
        restore["effects"].insert(0, _set("_beacon_healing_amount", "amount"))
        restore["effects"].insert(
            1,
            _when(
                "target.spell_beacon_of_hope == true and maximum_amount != null",
                [_set("_beacon_healing_amount", "max(amount, maximum_amount)")],
            ),
        )
        for effect in _walk_effects(restore["effects"]):
            if "heal" in effect:
                effect["heal"]["amount"] = "_beacon_healing_amount"
    changed["restore_hit_points"] = restore
    for parameter in restore["parameters"]:
        if parameter["name"] == "amount":
            parameter["minimum"] = 0
    for clause in restore.get("pre_conditions", []):
        if clause["condition"] == "amount > 0":
            clause["condition"] += (
                " or (target.spell_beacon_of_hope == true and maximum_amount "
                "!= null and maximum_amount > 0)"
            )
    shared = deepcopy(
        changed.get(
            "restore_shared_spell_healing_target", tools["restore_shared_spell_healing_target"]
        )
    )
    if not any(p["name"] == "maximum_amount" for p in shared["parameters"]):
        shared["parameters"].append(
            {"name": "maximum_amount", "type": "integer", "required": False, "default": None}
        )
        for effect in _walk_effects(shared["effects"]):
            if effect.get("call") == "restore_hit_points":
                effect["params"]["maximum_amount"] = "{maximum_amount}"
    changed["restore_shared_spell_healing_target"] = shared
    for effect in _walk_effects(shared["effects"]):
        if effect.get("condition") == "amount > 0":
            effect["condition"] += (
                " or (target.spell_beacon_of_hope == true and maximum_amount "
                "!= null and maximum_amount > 0)"
            )
    rest = deepcopy(
        changed.get("take_srd_short_rest_hit_die", tools["take_srd_short_rest_hit_die"])
    )
    if "_beacon_hit_die_maximum" not in str(rest["effects"]):
        for effects in list(_effect_lists(rest["effects"])):
            for index, effect in enumerate(list(effects)):
                if effect.get("set", {}).get("field") == "_short_rest_healing":
                    effects.insert(
                        index + 1,
                        _when(
                            "target_state.spell_beacon_of_hope == true",
                            [
                                _set(
                                    "_beacon_hit_die_maximum",
                                    (
                                        "max(1, _short_rest_hit_die_roll.count * "
                                        "_short_rest_hit_die_roll.sides + "
                                        "_short_rest_hit_die_roll.modifier)"
                                    ),
                                ),
                                _set("_short_rest_healing", "_beacon_hit_die_maximum"),
                            ],
                        ),
                    )
    changed["take_srd_short_rest_hit_die"] = rest
    for name, original in tools.items():
        tool = deepcopy(changed.get(name, original))
        effects = list(_walk_effects(tool.get("effects", [])))
        rolls = {e["store_as"] for e in effects if "dice" in e and e.get("store_as")}
        if not rolls:
            continue
        setters = {e["set"]["field"]: e["set"]["value"] for e in effects if "set" in e}

        def maximum(expression, depth=0, *, rolls=rolls, setters=setters):
            if not isinstance(expression, str) or depth > 8:
                return str(expression), False
            found = False
            for roll in rolls:
                if roll + ".total" in expression:
                    expression = expression.replace(
                        roll + ".total", f"({roll}.count * {roll}.sides)"
                    )
                    found = True
            if found:
                return expression, True
            if expression in setters and setters[expression] != expression:
                return maximum(setters[expression], depth + 1)
            return expression, False

        mutated = False
        for effect in effects:
            if effect.get("call") not in (
                "restore_hit_points",
                "restore_shared_spell_healing_target",
            ):
                continue
            if "maximum_amount" in effect.get("params", {}):
                continue
            amount = effect["params"]["amount"]
            if not isinstance(amount, str):
                continue
            expression, rolled = maximum(amount.strip("{}"))
            if rolled:
                effect["params"]["maximum_amount"] = "{" + expression + "}"
                mutated = True
        if mutated:
            changed[name] = tool


def _compose_remaining_damage_owners(tools, changed):
    """Transform uncovered damage rolls once at their producer, before target saves."""
    for name, original in tools.items():
        tool = deepcopy(changed.get(name, original))
        if "_ray_raw_damage" in str(tool.get("effects", [])):
            for effects in _effect_lists(tool["effects"]):
                for index, effect in enumerate(effects):
                    if index == 0 or "modify_srd_actor_raw_damage_roll" not in str(effect):
                        continue
                    previous = effects[index - 1]
                    if "dice" in previous and previous.get("condition"):
                        clause = previous["condition"]
                        if clause not in effect.get("condition", ""):
                            effect["condition"] = f"({effect['condition']}) and ({clause})"
            changed[name] = tool
        if "modify_srd_actor_damage_roll" in str(
            tool.get("effects", [])
        ) or "_ray_raw_damage" in str(tool.get("effects", [])):
            continue
        parameters = {p["name"] for p in tool.get("parameters", [])}
        source = next(
            (
                p
                for p in ("caster_state", "actor_state", "actor", "source_state_id")
                if p in parameters
            ),
            None,
        )
        if source is None:
            continue
        if source == "source_state_id":
            source_ref = "{source_state_id}"
            # A delayed caster can also be the loaded target: lookup resolves its ID to a dict.
            source_gate = (
                "source_state_id != null and len(source_state_id) > 0"
                if name == "resolve_srd_delayed_spell_damage"
                else "source_state_id != null and source_state_id != ''"
            )
        else:
            source_ref = "{_" + source + "_id}"
            source_gate = source + ".spell_ray_of_enfeeblement == true"
        roll_names = []
        for effect in _walk_effects(tool.get("effects", [])):
            variable = effect.get("store_as", "")
            if "dice" in effect and any(
                tag in variable
                for tag in ("damage", "delayed_roll", "projectile_roll", "breath_roll")
            ):
                roll_names.append(variable)
        if not roll_names:
            continue
        # Each dart has a separate damage roll even when its dice are rolled together.
        projectiles = name == "cast_spell_automatic_force_projectiles"

        def edit(
            effects,
            *,
            roll_names=roll_names,
            projectiles=projectiles,
            source_gate=source_gate,
            source_ref=source_ref,
            name=name,
        ):
            result = []
            for effect in effects:
                for branch in ("on_true", "on_false"):
                    if branch in effect:
                        effect[branch] = edit(effect[branch])
                if "for_each" in effect:
                    effect["for_each"]["do"] = edit(effect["for_each"]["do"])
                result.append(effect)
                roll = effect.get("store_as")
                if "dice" in effect and roll in roll_names and not projectiles:
                    gate = source_gate
                    if "condition" in effect:
                        gate = f"({source_gate}) and ({effect['condition']})"
                    result.append(
                        _when(
                            gate,
                            [
                                _call(
                                    "modify_srd_actor_raw_damage_roll",
                                    {
                                        "actor_state": source_ref,
                                        "lasting_state": "state",
                                        "amount": "{" + roll + ".total}",
                                    },
                                    ["_ray_raw_damage"],
                                ),
                                _set(
                                    roll,
                                    {
                                        "sides": roll + ".sides",
                                        "count": roll + ".count",
                                        "results": roll + ".results",
                                        "modifier": roll + ".modifier",
                                        "total": "_ray_raw_damage",
                                    },
                                ),
                            ],
                        )
                    )
                if effect.get("call") == "apply_srd_damage":
                    params = effect["params"]
                    amount = params["amount"]
                    adjustment = params.get("adjustment", 0)
                    if isinstance(amount, str):
                        a = amount.strip("{}")
                        b = (
                            adjustment.strip("{}")
                            if isinstance(adjustment, str)
                            else str(adjustment)
                        )
                        params["amount"] = "{max(0, " + a + " + " + b + ")}"
                        params["adjustment"] = 0
                    if name == "resolve_adjudicated_creature_attack":
                        params["source_state_id"] = source_ref
                if effect.get("call") == "resolve_spell_area_composite_damage_target":
                    for key in ("component_1_total", "component_2_total"):
                        effect["params"][key] = (
                            "{max(0, " + effect["params"][key].strip("{}") + ")}"
                        )
                if "call" in effect and "damage_total" in effect.get("params", {}):
                    value = effect["params"]["damage_total"]
                    if isinstance(value, str) and any(
                        roll + ".total" in value for roll in roll_names
                    ):
                        effect["params"]["damage_total"] = "{max(0, " + value.strip("{}") + ")}"
            return result

        if projectiles:
            continue  # Per-dart handling below owns its flat bonus and distribution.
        tool["effects"] = edit(tool["effects"])
        changed[name] = tool
    changed["modify_srd_actor_raw_damage_roll"] = _tool(
        ("Subtract Ray of Enfeeblement once from a raw roll, leaving flat bonuses for its owner."),
        [_entity("actor_state"), _entity("lasting_state"), _parameter("amount", "integer")],
        [
            _set("_ray_raw_damage", "amount"),
            _when(
                "actor_state.spell_ray_of_enfeeblement == true",
                [
                    {"dice": {"expression": "d8"}, "store_as": "_ray_raw_penalty"},
                    _set("_ray_raw_damage", "amount - _ray_raw_penalty.total"),
                ],
            ),
        ],
    )


def _compose_projectile_damage_owner(tools, changed):
    """Each Magic Missile dart retains its own d8 subtraction and minimum-zero damage."""
    name = "cast_spell_automatic_force_projectiles"
    tool = deepcopy(changed.get(name, tools[name]))
    if "_ray_projectile_results" in str(tool["effects"]):
        return
    for effects in _effect_lists(tool["effects"]):
        for index, effect in enumerate(list(effects)):
            if "dice" not in effect or effect.get("store_as") != "_spell_damage":
                continue
            effects[index + 1 : index + 1] = [
                _set("_ray_projectile_results", "_spell_damage.results"),
                _when(
                    "caster_state.spell_ray_of_enfeeblement == true",
                    [
                        _set("_ray_projectile_original", "_spell_damage.results"),
                        _set("_ray_projectile_results", []),
                        _set("_ray_projectile_penalties", []),
                        _set("_ray_projectile_total", 0),
                        {
                            "for_each": {
                                "collection": "_ray_projectile_original",
                                "as": "_ray_projectile_die",
                                "do": [
                                    {
                                        "dice": {"expression": "d8"},
                                        "store_as": "_ray_projectile_penalty",
                                    },
                                    _set(
                                        "_ray_projectile_damage",
                                        (
                                            "max(0, _ray_projectile_die + "
                                            "flat_damage_per_projectile - "
                                            "_ray_projectile_penalty.total) - "
                                            "flat_damage_per_projectile"
                                        ),
                                    ),
                                    {
                                        "list_push": {
                                            "list": "_ray_projectile_results",
                                            "value": "_ray_projectile_damage",
                                        }
                                    },
                                    {
                                        "list_push": {
                                            "list": "_ray_projectile_penalties",
                                            "value": "_ray_projectile_penalty.total",
                                        }
                                    },
                                    _set(
                                        "_ray_projectile_total",
                                        ("_ray_projectile_total + _ray_projectile_damage"),
                                    ),
                                ],
                            }
                        },
                        _set(
                            "_spell_damage",
                            {
                                "count": "_spell_damage.count",
                                "sides": "_spell_damage.sides",
                                "results": "_spell_damage.results",
                                "modifier": "_spell_damage.modifier",
                                "total": "_ray_projectile_total",
                            },
                        ),
                    ],
                ),
            ]
    for effect in _walk_effects(tool["effects"]):
        if (
            "set" in effect
            and isinstance(effect["set"]["value"], str)
            and "_spell_damage.results[" in effect["set"]["value"]
        ):
            effect["set"]["value"] = effect["set"]["value"].replace(
                "_spell_damage.results[", "_ray_projectile_results["
            )
        if effect.get("call") == "apply_srd_damage":
            params = effect["params"]
            amount, adjust = params["amount"], params["adjustment"]
            a = amount.strip("{}") if isinstance(amount, str) else str(amount)
            b = adjust.strip("{}") if isinstance(adjust, str) else str(adjust)
            params["amount"] = "{max(0, " + a + " + " + b + ")}"
            params["adjustment"] = 0
    changed[name] = tool


def _mind_blank_information_tool():
    """Guard magical information about an admitted subject, rather than a spell school."""
    return _tool(
        ("Check whether a saved Mind Blank prevents this magical operation about its subject."),
        [
            _entity("target_state"),
            _entity("lasting_state"),
            {
                "name": "query_kind",
                "type": "enum",
                "enum_values": list(MIND_BLANK_QUERY_KINDS),
                "required": True,
            },
        ],
        [
            _set("_mind_blank_information_blocked", False),
            _when(
                (
                    "target_state.has_timed_actor_defenses == true and "
                    "lasting_state.turn_effects != null"
                ),
                [
                    _set("_mind_blank_records", "lasting_state.turn_effects"),
                    {
                        "for_each": {
                            "collection": "_mind_blank_records",
                            "as": "_mind_blank_record",
                            "do": [
                                _when(
                                    (
                                        "_mind_blank_record.kind == "
                                        "'timed_actor_defense' "
                                        "and _mind_blank_record.spell == 'mind_blank' "
                                        ""
                                        "and "
                                        "_mind_blank_record.target == "
                                        "_target_state_id"
                                    ),
                                    [
                                        _call(
                                            "check_srd_timed_actor_defense_active",
                                            {
                                                "source_state": "{_mind_blank_record.caster}",
                                                "target_state": "{_target_state_id}",
                                                "lasting_state": "{_lasting_state_id}",
                                                "spell": "mind_blank",
                                                "started_tick": "{_mind_blank_record.started_tick}",
                                                "expires_tick": "{_mind_blank_record.expires_tick}",
                                                "concentration": (
                                                    "{_mind_blank_record.concentration}"
                                                ),
                                            },
                                            ["_actor_defense_active"],
                                        ),
                                        _when(
                                            "_actor_defense_active == true",
                                            [_set("_mind_blank_information_blocked", True)],
                                        ),
                                    ],
                                ),
                            ],
                        }
                    },
                ],
            ),
        ],
    )


def _compose_freedom_owners(tools, changed):
    """Separate spell-owned restrictions from independent mundane conditions."""
    suppression = changed["resolve_srd_timed_actor_condition_suppression"]
    for effect in _walk_effects(suppression["effects"]):
        if effect.get("set", {}).get("field", "").endswith(".suppressed"):
            effect["set"]["value"] += (
                " or (target_state.spell_freedom_of_movement == true and "
                "_actor_suppression_source.spell_id != null and "
                "(_actor_suppression_source.condition == 'paralyzed' or "
                "_actor_suppression_source.condition == 'restrained'))"
            )
    for condition in ("paralyzed", "restrained"):
        suppression["effects"].extend(
            [
                _when(
                    f"target_state.timed_condition_unowned_{condition} == null",
                    [
                        _set(
                            f"target_state.timed_condition_unowned_{condition}",
                            f"'{condition}' in target_state.conditions",
                        ),
                    ],
                ),
                _when(
                    "target_state.spell_freedom_of_movement == true and "
                    f"(target_state.timed_condition_unowned_{condition} != true or "
                    f"target_state.unowned_{condition}_is_magical == true)",
                    [
                        {"remove_condition": {"target": "target_state", "condition": condition}},
                    ],
                ),
                _when(
                    "target_state.spell_freedom_of_movement != true and "
                    f"target_state.timed_condition_unowned_{condition} == true and "
                    f"target_state.unowned_{condition}_is_magical == true",
                    [
                        {"apply_condition": {"target": "target_state", "condition": condition}},
                    ],
                ),
            ]
        )
    immunity = deepcopy(
        changed.get(
            "check_srd_target_condition_immunity", tools["check_srd_target_condition_immunity"]
        )
    )
    if not any(p["name"] == "source_is_magical" for p in immunity["parameters"]):
        immunity["parameters"].append(
            {"name": "source_is_magical", "type": "boolean", "required": False, "default": False}
        )
        immunity["effects"].append(
            _when(
                (
                    "target_state.spell_freedom_of_movement == true and "
                    "source_is_magical == true and (condition == 'paralyzed' or "
                    "condition == 'restrained')"
                ),
                [_set("_source_condition_immune", True)],
            )
        )
    changed["check_srd_target_condition_immunity"] = immunity
    for name in (
        "resolve_srd_timed_condition_initial_save",
        "resolve_srd_timed_condition_repeat_save",
    ):
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        for effect in _walk_effects(tool["effects"]):
            if effect.get("call") == "check_srd_target_condition_immunity":
                effect["params"]["source_is_magical"] = True
        changed[name] = tool
    apply = deepcopy(changed.get("apply_srd_condition", tools["apply_srd_condition"]))
    if not any(p["name"] == "source_is_magical" for p in apply["parameters"]):
        apply["parameters"].append(
            {"name": "source_is_magical", "type": "boolean", "required": False, "default": False}
        )
        for effect in _walk_effects(apply["effects"]):
            if effect.get("call") == "check_srd_target_condition_immunity":
                effect["params"]["source_is_magical"] = "{source_is_magical}"
    changed["apply_srd_condition"] = apply
    for condition in ("paralyzed", "restrained", "grappled"):
        if f"target.unowned_{condition}_is_magical" not in str(apply["effects"]):
            apply["effects"].append(
                _when(
                    f"condition == '{condition}'",
                    [
                        _set(f"target.unowned_{condition}_is_magical", "source_is_magical"),
                        _set(f"target.timed_condition_unowned_{condition}", True),
                    ],
                )
            )
    unarmed = deepcopy(
        changed.get("resolve_srd_unarmed_effect", tools["resolve_srd_unarmed_effect"])
    )
    if "target_state.unowned_grappled_is_magical" not in str(unarmed["effects"]):
        for effects in _effect_lists(unarmed["effects"]):
            for index, effect in enumerate(list(effects)):
                if effect.get("set", {}).get("field") == "target_state.grappled_by":
                    effects.insert(
                        index + 1, _set("target_state.unowned_grappled_is_magical", False)
                    )
    changed["resolve_srd_unarmed_effect"] = unarmed
    movement = deepcopy(
        changed.get("refresh_srd_movement_budget", tools["refresh_srd_movement_budget"])
    )
    for effect in _walk_effects(movement["effects"]):
        if effect.get("condition") == "actor_state.spell_speed_reduction != null":
            effect["condition"] += " and actor_state.spell_freedom_of_movement != true"
        if "actor_state.timed_control_speed_zero == true" in effect.get("condition", ""):
            effect["condition"] = re.sub(
                r"\(*actor_state.timed_control_speed_zero == true"
                r"(?:\s+and actor_state.spell_freedom_of_movement != true|\))*",
                (
                    "(actor_state.timed_control_speed_zero == true and "
                    "actor_state.spell_freedom_of_movement != true)"
                ),
                effect["condition"],
            )
    changed["refresh_srd_movement_budget"] = movement
    spend = deepcopy(changed.get("spend_combat_movement", tools["spend_combat_movement"]))
    for clause in spend.get("pre_conditions", []):
        if "actor_state.timed_control_speed_zero != true" in clause["condition"]:
            clause["condition"] = re.sub(
                r"\(*actor_state.timed_control_speed_zero != true"
                r"(?:\s+or actor_state.spell_freedom_of_movement == true|\))*",
                (
                    "(actor_state.timed_control_speed_zero != true or "
                    "actor_state.spell_freedom_of_movement == true)"
                ),
                clause["condition"],
            )
    changed["spend_combat_movement"] = spend
    changed["escape_srd_nonmagical_restraint"] = _tool(
        (
            "Spend five feet of movement to escape a saved nonmagical "
            "restraint or Grapple under Freedom of Movement."
        ),
        [
            _entity("actor_state"),
            {
                "name": "restraint",
                "type": "enum",
                "enum_values": ["grappled", "restrained"],
                "required": True,
            },
        ],
        [
            _require(
                ("combat.turn_active == true and combat.active_actor == _actor_state_id"),
                "Only the active creature can spend its movement.",
            ),
            _require(
                "actor_state.spell_freedom_of_movement == true",
                "This escape requires active Freedom of Movement.",
            ),
            _require("restraint in actor_state.conditions", "The selected restraint is absent."),
            _require(
                ("actor_state.is_alive == true and 'incapacitated' not in actor_state.conditions"),
                "The creature must be able to act.",
            ),
            _require(
                (
                    "(restraint == 'grappled' and "
                    "actor_state.unowned_grappled_is_magical == false) or "
                    "(restraint == 'restrained' and "
                    "actor_state.timed_condition_unowned_restrained == true and "
                    "actor_state.unowned_restrained_is_magical == false)"
                ),
                "Only an admitted nonmagical restraint can be escaped this way.",
            ),
            _require("combat.movement_remaining >= 5", "Five feet of movement must remain."),
            _set("combat.movement_remaining", "combat.movement_remaining - 5"),
            _set("combat.movement_spent", "combat.movement_spent + 5"),
            {"remove_condition": {"target": "actor_state", "condition": "{restraint}"}},
            _when(
                "restraint == 'grappled'",
                [
                    _set("actor_state.grappled_by", None),
                    _set("actor_state.grapple_escape_dc", None),
                ],
            ),
            _when(
                "restraint == 'restrained'",
                [_set("actor_state.timed_condition_unowned_restrained", False)],
            ),
        ],
    )
    changed["escape_srd_nonmagical_restraint"]["metadata"] = {
        "category": "combat",
        "costs_action": False,
    }


def _sanctuary_pending_tools():
    "Retain a ward's actual save on the owning operation; choosing a target never rerolls it."
    leaf = _tool(
        ("Resolve Sanctuary targeting for one saved pending attack or targeted damaging spell."),
        [
            _entity("actor_state"),
            _entity("target_state"),
            _entity("lasting_state"),
            _parameter("pending_action_id"),
            {
                "name": "operation_kind",
                "type": "enum",
                "enum_values": [
                    "weapon_attack",
                    "spell_attack",
                    "targeted_damage_spell",
                    "area_spell",
                ],
                "required": True,
            },
        ],
        [
            _require(
                (
                    "lasting_state.pending_srd_actions != null and "
                    "lasting_state.pending_srd_actions[pending_action_id] != null"
                ),
                "The owning pending operation is absent.",
            ),
            _set("_sanctuary_pending", "lasting_state.pending_srd_actions[pending_action_id]"),
            _require(
                "_sanctuary_pending.actor_state_id == _actor_state_id",
                "Sanctuary must use the pending operation's admitted actor.",
            ),
            _require(
                (
                    "_sanctuary_pending.status != 'completed' and "
                    "_sanctuary_pending.status != 'lost' and "
                    "_sanctuary_pending.status != 'cancelled'"
                ),
                "This pending operation has already ended.",
            ),
            _set("_sanctuary_target_allowed", True),
            _set("_sanctuary_choice_required", False),
            _set("_sanctuary_save", None),
            _when(
                (
                    "operation_kind != 'area_spell' and "
                    "target_state.has_actor_spell_modifiers == true"
                ),
                [
                    _call(
                        "resolve_srd_actor_spell_modifiers",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_actor_modifier_sanctuary_dc", "_actor_modifier_sanctuary_source"],
                    ),
                    _when(
                        "_actor_modifier_sanctuary_dc > 0",
                        [
                            _set("_sanctuary_saved_saves", []),
                            _when(
                                "_sanctuary_pending.sanctuary_saves != null",
                                [
                                    _set(
                                        "_sanctuary_saved_saves",
                                        "_sanctuary_pending.sanctuary_saves",
                                    )
                                ],
                            ),
                            {
                                "for_each": {
                                    "collection": "_sanctuary_saved_saves",
                                    "as": "_sanctuary_cached_save",
                                    "do": [
                                        _when(
                                            (
                                                "_sanctuary_cached_save.target_state_"
                                                "id == "
                                                "_target_state_id and "
                                                "_sanctuary_cached_save.caster_state_"
                                                "id == "
                                                "_actor_modifier_sanctuary_source.cas"
                                                "ter and "
                                                "_sanctuary_cached_save.started_tick "
                                                "== "
                                                "_actor_modifier_sanctuary_source.sta"
                                                "rted_tic"
                                                "k"
                                            ),
                                            [_set("_sanctuary_save", "_sanctuary_cached_save")],
                                        ),
                                    ],
                                }
                            },
                            _when(
                                "_sanctuary_save == null",
                                [
                                    _require(
                                        (
                                            "actor_state.saving_throw_modifiers != "
                                            "null and "
                                            "actor_state.saving_throw_modifiers.wisdo"
                                            "m != "
                                            "null"
                                        ),
                                        ("The attacker needs its authoritative Wisdom save."),
                                    ),
                                    _call(
                                        "d20_test",
                                        {
                                            "actor_state": "{_actor_state_id}",
                                            "ability": "wisdom",
                                            "kind": "saving_throw",
                                            "ability_modifier": (
                                                "{actor_state.saving_throw_modifiers.wisdom}"
                                            ),
                                            "proficiency_bonus": 0,
                                            "proficiency_factor": "none",
                                            "circumstantial_modifier": 0,
                                            "target_number": "{_actor_modifier_sanctuary_dc}",
                                            "advantage": False,
                                            "disadvantage": False,
                                        },
                                        {
                                            "_sanctuary_outcome": "_outcome",
                                            "_sanctuary_roll": "_d20_test",
                                            "_sanctuary_total": "_adjusted_total",
                                        },
                                    ),
                                    _set(
                                        "_sanctuary_save",
                                        {
                                            "target_state_id": "_target_state_id",
                                            "caster_state_id": (
                                                "_actor_modifier_sanctuary_source.caster"
                                            ),
                                            "started_tick": (
                                                "_actor_modifier_sanctuary_source.started_tick"
                                            ),
                                            "dc": "_actor_modifier_sanctuary_dc",
                                            "raw": "_sanctuary_roll.total",
                                            "results": "_sanctuary_roll.results",
                                            "total": "_sanctuary_total",
                                            "outcome": "_sanctuary_outcome",
                                        },
                                    ),
                                    _require(
                                        "len(_sanctuary_saved_saves) < 64",
                                        (
                                            "The pending operation's targeting "
                                            "choices "
                                            "are exhausted."
                                        ),
                                    ),
                                    {
                                        "list_push": {
                                            "list": "_sanctuary_saved_saves",
                                            "value": "_sanctuary_save",
                                        }
                                    },
                                    _set(
                                        (
                                            "lasting_state.pending_srd_actions.{pendi"
                                            "ng_actio"
                                            "n_id}.sanctuary_saves"
                                        ),
                                        "_sanctuary_saved_saves",
                                    ),
                                ],
                            ),
                            _when(
                                "_sanctuary_save.outcome != 'success'",
                                [
                                    _set("_sanctuary_target_allowed", False),
                                    _set("_sanctuary_choice_required", True),
                                    _set(
                                        (
                                            "lasting_state.pending_srd_actions.{pendi"
                                            "ng_actio"
                                            "n_id}.stage"
                                        ),
                                        "'awaiting_sanctuary_choice'",
                                    ),
                                    _set(
                                        (
                                            "lasting_state.pending_srd_actions.{pendi"
                                            "ng_actio"
                                            "n_id}.sanctuary_rejected_target_state_id"
                                        ),
                                        "_target_state_id",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    decide = _tool(
        (
            "Choose to lose a saved Sanctuary-blocked operation or ask "
            "its owning validator to retarget it."
        ),
        [
            _entity("lasting_state"),
            _parameter("pending_action_id"),
            {
                "name": "decision",
                "type": "enum",
                "enum_values": ["lose_operation", "retarget"],
                "required": True,
            },
            {"name": "new_target_state_id", "type": "string", "required": False, "default": None},
        ],
        [
            _require(
                (
                    "lasting_state.pending_srd_actions != null and "
                    "lasting_state.pending_srd_actions[pending_action_id] != null"
                ),
                "The saved pending operation is absent.",
            ),
            _set(
                "_sanctuary_decision_pending",
                "lasting_state.pending_srd_actions[pending_action_id]",
            ),
            _require(
                (
                    "_sanctuary_decision_pending.stage == "
                    "'awaiting_sanctuary_choice' and "
                    "_sanctuary_decision_pending.status != 'completed' and "
                    "_sanctuary_decision_pending.status != 'lost'"
                ),
                "The saved operation is not awaiting a Sanctuary choice.",
            ),
            _when(
                "decision == 'lose_operation'",
                [
                    _set(
                        (
                            "lasting_state.pending_srd_actions.{pending_action_id}.sa"
                            "nctu"
                            "ary_decision"
                        ),
                        "'lose_operation'",
                    ),
                    _set(
                        "lasting_state.pending_srd_actions.{pending_action_id}.stage",
                        "'sanctuary_lost'",
                    ),
                ],
                [
                    _require(
                        (
                            "new_target_state_id != null and new_target_state_id != "
                            "_sanctuary_decision_pending.sanctuary_rejected_target_st"
                            "ate_"
                            "id"
                        ),
                        "Choose a different target.",
                    ),
                    _set(
                        (
                            "lasting_state.pending_srd_actions.{pending_action_id}.sa"
                            "nctu"
                            "ary_decision"
                        ),
                        "'retarget'",
                    ),
                    _set(
                        (
                            "lasting_state.pending_srd_actions.{pending_action_id}.sa"
                            "nctu"
                            "ary_retarget_state_id"
                        ),
                        "new_target_state_id",
                    ),
                    _set(
                        "lasting_state.pending_srd_actions.{pending_action_id}.stage",
                        "'sanctuary_retarget_validation'",
                    ),
                ],
            ),
            _set("_sanctuary_retarget_required", "decision == 'retarget'"),
            _set("_sanctuary_operation_lost", "decision == 'lose_operation'"),
        ],
    )
    decide["metadata"] = {"category": "spells", "costs_action": False}
    return {"check_srd_sanctuary_targeting": leaf, "decide_srd_sanctuary_target": decide}


def _compose_sanctuary_damage_owners(tools, changed):
    "Atomic calls retain the save guard; pending callbacks consume the saved targeting decision."
    atomic = changed["check_srd_sanctuary_target"]
    original = atomic["effects"]
    atomic["effects"] = [_set("_sanctuary_bypass", False)]
    for field in ("pending_srd_targeting_id", "pending_srd_completion_id"):
        atomic["effects"].append(
            _when(
                f"actor_state.{field} != null",
                [
                    _require(
                        "lasting_state.pending_srd_actions != null and "
                        f"lasting_state.pending_srd_actions[actor_state.{field}] != null",
                        "The pending Sanctuary bypass has no owning event.",
                    ),
                    _set(
                        "_sanctuary_bypass_event",
                        f"lasting_state.pending_srd_actions[actor_state.{field}]",
                    ),
                    _require(
                        (
                            "_sanctuary_bypass_event.actor_state_id == "
                            "_actor_state_id "
                            "and _sanctuary_bypass_event.status != 'lost' and "
                            "_sanctuary_bypass_event.status != 'cancelled'"
                        ),
                        ("The pending Sanctuary bypass belongs to another or ended operation."),
                    ),
                    _set("_sanctuary_bypass", True),
                ],
            )
        )
    atomic["effects"].append(_when("_sanctuary_bypass != true", original))
    for name in (
        "cast_spell_single_target_save_damage",
        "cast_spell_instantaneous_threshold_death_damage",
        "cast_spell_automatic_force_projectiles",
    ):
        tool = deepcopy(changed.get(name, tools[name]))
        if "check_srd_sanctuary_target" not in str(tool["effects"]):
            tool["effects"].insert(
                0,
                _when(
                    "target != null and target.has_actor_spell_modifiers == true",
                    [
                        _call(
                            "check_srd_sanctuary_target",
                            {
                                "actor_state": "{_caster_state_id}",
                                "target_state": "{_target_id}",
                                "lasting_state": "state",
                            },
                        ),
                    ],
                ),
            )
        changed[name] = tool
    # Dealt damage ends the source creature's ward, including delayed and passive damage.
    changed["end_srd_sanctuary_damage_source"] = _tool(
        "End Sanctuary when the admitted source actually deals damage.",
        [_entity("source_state"), _entity("lasting_state")],
        [
            _when(
                "source_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "end_srd_sanctuary_for_actor",
                        {
                            "actor_state": "{_source_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            )
        ],
    )
    resolve = changed["resolve_srd_actor_spell_modifiers"]
    for effect in _walk_effects(resolve["effects"]):
        if effect.get("condition") == "_actor_modifier_record.mode == 'sanctuary'":
            effect["on_true"].append(_set("lasting_state.has_sanctuary", True))
    changed["refresh_srd_actor_spell_projections"]["effects"].insert(
        0, _set("lasting_state.has_sanctuary", False)
    )
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        tool = deepcopy(changed.get(name, tools[name]))
        if "end_srd_sanctuary_damage_source" not in str(tool["effects"]):
            branch = next(
                e
                for e in tool["effects"]
                if e.get("condition", "").startswith("target.concentration_spell_id != null")
            )
            branch["condition"] += " or (_effective_damage > 0 and state.has_sanctuary == true)"
            branch["on_true"].append(
                _when(
                    (
                        "_effective_damage > 0 and state.has_sanctuary == true and "
                        "source_state_id != null and source_state_id != ''"
                    ),
                    [
                        _call(
                            "end_srd_sanctuary_damage_source",
                            {"source_state": "{source_state_id}", "lasting_state": "state"},
                        ),
                    ],
                )
            )
        changed[name] = tool


def _compose_calm_owners(tools, changed):
    "Project temporary indifference over saved relationships and end it at actual damage."
    relationship = _tool(
        (
            "Record the scene's creature relationship used by hostility "
            "and witnessed-ally consequences."
        ),
        [
            _entity("actor_state"),
            _entity("toward_state"),
            {
                "name": "attitude",
                "type": "enum",
                "enum_values": ["hostile", "indifferent", "friendly"],
                "required": True,
            },
            {"name": "is_ally", "type": "boolean", "required": False, "default": False},
        ],
        [
            _require(
                "_actor_state_id != _toward_state_id",
                "Record a relationship toward another creature.",
            ),
            _when("actor_state.attitudes == null", [_set("actor_state.attitudes", {})]),
            _set("actor_state.attitudes.{_toward_state_id}", "attitude"),
            _set("_relationship_allies", []),
            _set("_relationship_old_allies", []),
            _when(
                "actor_state.ally_state_ids != null",
                [_set("_relationship_old_allies", "actor_state.ally_state_ids")],
            ),
            {
                "for_each": {
                    "collection": "_relationship_old_allies",
                    "as": "_relationship_ally",
                    "do": [
                        _when(
                            "_relationship_ally != _toward_state_id",
                            [
                                {
                                    "list_push": {
                                        "list": "_relationship_allies",
                                        "value": "_relationship_ally",
                                    }
                                }
                            ],
                        ),
                    ],
                }
            },
            _when(
                "is_ally == true",
                [{"list_push": {"list": "_relationship_allies", "value": "_toward_state_id"}}],
            ),
            _set("actor_state.ally_state_ids", "_relationship_allies"),
        ],
    )
    relationship["metadata"] = {"category": "social", "costs_action": False}
    changed["record_srd_creature_relationship"] = relationship
    changed["validate_srd_calm_hostility"] = _tool(
        ("Require the saved subject to be currently hostile toward this selected creature."),
        [_entity("target_state"), _entity("toward_state")],
        [
            _require(
                (
                    "target_state.attitudes != null and "
                    "target_state.attitudes[_toward_state_id] == 'hostile'"
                ),
                ("Calm Emotions indifference requires an admitted currently hostile relationship."),
            )
        ],
    )
    read = _tool(
        ("Read effective attitude, preserving the underlying relationship during Calm Emotions."),
        [_entity("actor_state"), _entity("toward_state")],
        [
            _set("_creature_attitude", "'indifferent'"),
            _when(
                (
                    "actor_state.attitudes != null and "
                    "actor_state.attitudes[_toward_state_id] != null"
                ),
                [_set("_creature_attitude", "actor_state.attitudes[_toward_state_id]")],
            ),
            _when(
                (
                    "actor_state.actor_spell_indifferent_toward != null and "
                    "_toward_state_id in "
                    "actor_state.actor_spell_indifferent_toward"
                ),
                [_set("_creature_attitude", "'indifferent'")],
            ),
        ],
    )
    read["metadata"] = {"category": "social", "costs_action": False}
    changed["read_srd_creature_attitude"] = read
    project = _tool(
        ("Project active Calm Emotions indifference without overwriting underlying attitudes."),
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_calm_records", []),
            _set("_calm_toward", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_calm_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_calm_records",
                    "as": "_calm_record",
                    "do": [
                        _when(
                            (
                                "_calm_record.kind == 'actor_spell_modifier' and "
                                "_calm_record.mode == 'calm_indifference' and "
                                "_calm_record.target == _target_state_id"
                            ),
                            [
                                _call(
                                    "check_srd_actor_spell_modifier_active",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "source_state": "{_calm_record.caster}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "spell": "calm_emotions",
                                        "started_tick": "{_calm_record.started_tick}",
                                        "expires_tick": "{_calm_record.expires_tick}",
                                        "concentration": True,
                                    },
                                    ["_actor_modifier_active"],
                                ),
                                _when(
                                    "_actor_modifier_active == true",
                                    [
                                        _set("lasting_state.has_calm_indifference", True),
                                        _set("_calm_record_toward", "_calm_record.toward_ids"),
                                        {
                                            "for_each": {
                                                "collection": "_calm_record_toward",
                                                "as": "_calm_toward_id",
                                                "do": [
                                                    _when(
                                                        ("_calm_toward_id not in _calm_toward"),
                                                        [
                                                            {
                                                                "list_push": {
                                                                    "list": "_calm_toward",
                                                                    "value": "_calm_toward_id",
                                                                }
                                                            }
                                                        ],
                                                    ),
                                                ],
                                            }
                                        },
                                    ],
                                ),
                            ],
                        ),
                    ],
                }
            },
            _set("target_state.actor_spell_indifferent_toward", "_calm_toward"),
        ],
    )
    changed["project_srd_calm_indifference"] = project
    changed["resolve_srd_timed_actor_condition_suppression"]["effects"].append(
        _when(
            "target_state.has_actor_spell_modifiers == true",
            [
                _call(
                    "project_srd_calm_indifference",
                    {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                )
            ],
        )
    )
    cast = changed["cast_spell"]
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if e.get("require", {}).get("condition") != "len(calm_indifference_targets) == 0"
        ]
    target_cast = changed["resolve_srd_actor_modifier_target_cast"]
    target_cast["effects"].insert(1, _set("_calm_saved_allies", "ally_ids"))
    target_cast["effects"].insert(
        2,
        _when(
            "indifferent == true",
            [
                _require(
                    "len(toward_ids) > 0",
                    "Choose at least one currently hostile creature for indifference.",
                ),
                {
                    "for_each": {
                        "collection": "toward_ids",
                        "as": "_calm_selected_hostile",
                        "do": [
                            _call(
                                "validate_srd_calm_hostility",
                                {
                                    "target_state": "{_target_state_id}",
                                    "toward_state": "{_calm_selected_hostile}",
                                },
                            ),
                        ],
                    }
                },
                _set("_calm_saved_allies", []),
                _when(
                    "target_state.ally_state_ids != null",
                    [_set("_calm_saved_allies", "target_state.ally_state_ids")],
                ),
            ],
        ),
    )
    for effect in _walk_effects(target_cast["effects"]):
        if effect.get("call") == "install_srd_actor_spell_modifier":
            # Indifference uses the subject's admitted allies, rather than the caster's selection.
            effect["params"]["ally_ids"] = "{_calm_saved_allies}"
    harm = deepcopy(changed.get("validate_srd_charmer_harm", tools["validate_srd_charmer_harm"]))
    if "actor_spell_indifferent_toward" not in str(harm["effects"]):
        harm["effects"].append(
            _require(
                (
                    "actor_state.actor_spell_indifferent_toward == null or "
                    "_target_state_id not in "
                    "actor_state.actor_spell_indifferent_toward"
                ),
                (
                    "Calm Emotions makes this creature indifferent toward the "
                    "selected creature; it cannot initiate a hostile action "
                    "against it."
                ),
            )
        )
    changed["validate_srd_charmer_harm"] = harm
    after = _tool(
        ("End indifference on actual damage or an admitted witnessing subject's ally damage."),
        [
            _entity("victim_state"),
            _entity("lasting_state"),
            _parameter("damage_taken", "integer"),
            {
                "name": "witness_state_ids",
                "type": "array",
                "items": {"type": "string"},
                "required": False,
                "default": None,
            },
        ],
        [
            _set("_calm_damage_kept", []),
            _set("_calm_damage_records", "lasting_state.turn_effects"),
            {
                "for_each": {
                    "collection": "_calm_damage_records",
                    "as": "_calm_damage_record",
                    "do": [
                        _set("_calm_damage_ends", False),
                        _when(
                            (
                                "damage_taken > 0 and _calm_damage_record.kind == "
                                "'actor_spell_modifier' and _calm_damage_record.mode "
                                "== "
                                "'calm_indifference'"
                            ),
                            [
                                _set(
                                    "_calm_damage_ends",
                                    "_calm_damage_record.target == _victim_state_id",
                                ),
                                _when(
                                    "_victim_state_id in _calm_damage_record.ally_ids",
                                    [
                                        _require(
                                            "witness_state_ids != null",
                                            (
                                                "Identify the creatures that actually "
                                                ""
                                                ""
                                                "witnessed this ally's damage while "
                                                "Calm "
                                                "Emotions is active."
                                            ),
                                        ),
                                        _set(
                                            "_calm_damage_ends",
                                            (
                                                "_calm_damage_ends == true or "
                                                "_calm_damage_record.target in "
                                                "witness_state_ids"
                                            ),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "_calm_damage_ends != true",
                            [
                                {
                                    "list_push": {
                                        "list": "_calm_damage_kept",
                                        "value": "_calm_damage_record",
                                    }
                                }
                            ],
                        ),
                    ],
                }
            },
            _set("lasting_state.turn_effects", "_calm_damage_kept"),
            _call("refresh_srd_actor_spell_projections", {"lasting_state": "{_lasting_state_id}"}),
        ],
    )
    changed["resolve_srd_calm_after_damage"] = after
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        tool = deepcopy(changed.get(name, tools[name]))
        if not any(p["name"] == "witness_state_ids" for p in tool["parameters"]):
            tool["parameters"].append(
                {
                    "name": "witness_state_ids",
                    "type": "array",
                    "items": {"type": "string"},
                    "required": False,
                    "default": None,
                }
            )
            branch = next(
                e
                for e in tool["effects"]
                if e.get("condition", "").startswith("target.concentration_spell_id != null")
            )
            branch["condition"] += (
                " or (_effective_damage > 0 and state.has_calm_indifference == true)"
            )
            branch["on_true"].append(
                _when(
                    "_effective_damage > 0 and state.has_calm_indifference == true",
                    [
                        _call(
                            "resolve_srd_calm_after_damage",
                            {
                                "victim_state": "{target}",
                                "lasting_state": "state",
                                "damage_taken": "{_effective_damage}",
                                "witness_state_ids": "{witness_state_ids}",
                            },
                        ),
                    ],
                )
            )
        changed[name] = tool
    changed["refresh_srd_actor_spell_projections"]["effects"].insert(
        0, _set("lasting_state.has_calm_indifference", False)
    )
    # Forward one declared witnessing fact set through every affected operation owner.
    owners = {"apply_srd_damage", "apply_srd_composite_damage"}
    pending = True
    while pending:
        pending = False
        for name, original in tools.items():
            tool = deepcopy(changed.get(name, original))
            calls = [e for e in _walk_effects(tool.get("effects", [])) if e.get("call") in owners]
            if not calls or name in owners:
                continue
            owners.add(name)
            pending = True
            if not any(p["name"] == "witness_state_ids" for p in tool["parameters"]):
                tool["parameters"].append(
                    {
                        "name": "witness_state_ids",
                        "type": "array",
                        "items": {"type": "string"},
                        "required": False,
                        "default": None,
                    }
                )
            for call in calls:
                call["params"]["witness_state_ids"] = "{witness_state_ids}"
            changed[name] = tool


def _compose_mind_control_suppression(changed):
    "Suppress explicitly directing spell sources while preserving their original lifetime."
    tool = changed["resolve_srd_timed_actor_condition_suppression"]
    tool["effects"][0:0] = [
        _set("_actor_mind_control_suppressed", False),
        _when(
            (
                "target_state.has_timed_actor_defenses == true and "
                "target_state.timed_condition_source_ids != null and "
                "len(target_state.timed_condition_source_ids) > 0"
            ),
            [
                _call(
                    "check_srd_mind_blank_information",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "query_kind": "mind_control",
                    },
                    {"_actor_mind_control_suppressed": "_mind_blank_information_blocked"},
                ),
            ],
        ),
    ]
    for effects in _effect_lists(tool["effects"]):
        for index, effect in enumerate(list(effects)):
            if not effect.get("set", {}).get("field", "").endswith(".suppressed"):
                continue
            original = effect["set"]["value"]
            effects[index : index + 1] = [
                _set("_actor_source_suppressed", original),
                _when(
                    (
                        "_actor_mind_control_suppressed == true and "
                        "(_actor_suppression_source.control_profile == 'command' or "
                        "_actor_suppression_source.control_profile == 'compulsion' "
                        "or _actor_suppression_source.control_profile == 'dominate' "
                        "or _actor_suppression_source.control_profile == "
                        "'suggestion' or _actor_suppression_source.control_profile "
                        "== 'geas' or _actor_suppression_source.control_profile == "
                        "'confusion')"
                    ),
                    [_set("_actor_source_suppressed", True)],
                ),
                _set(effect["set"]["field"], "_actor_source_suppressed"),
            ]


def _walk_effects(effects):
    for effect in effects:
        yield effect
        for branch in ("on_true", "on_false"):
            yield from _walk_effects(effect.get(branch, []))
        if "for_each" in effect:
            yield from _walk_effects(effect["for_each"]["do"])


def _compose_consumers(tools, changed):
    "Add cheap flag-gated consumers to existing action owners without changing core execution."
    cast = deepcopy(tools["cast_spell"])
    extra_parameters = [
        {"name": "all_targets_in_area", "type": "boolean", "required": False, "default": False},
        {
            "name": "calm_indifference_targets",
            "type": "array",
            "required": False,
            "default": [],
            "items": {"type": "string"},
        },
        {
            "name": "calm_indifferent_toward",
            "type": "array",
            "required": False,
            "default": [],
            "items": {"type": "string"},
        },
        {
            "name": "calm_allies",
            "type": "array",
            "required": False,
            "default": [],
            "items": {"type": "string"},
        },
    ]
    cast["parameters"] += [
        p for p in extra_parameters if p["name"] not in {q["name"] for q in cast["parameters"]}
    ]
    # The attitude option lacks an owning hostility/witness consumer in this wave.
    branch = _when(
        "_spell_definition.family == 'timed_actor_modifier'",
        [
            _set("_modifier_dispatch_targets", []),
            _set("_modifier_forego_targets", []),
            _when(
                "forego_save_targets != null",
                [_set("_modifier_forego_targets", "forego_save_targets")],
            ),
            _set("_modifier_dispatch_distance", "targets_max_distance"),
            _set("_modifier_dispatch_willing", "all_targets_willing"),
            _set("_modifier_dispatch_touch", "caster_can_touch_all_targets"),
            _when(
                "targets != null",
                [_set("_modifier_dispatch_targets", "targets")],
                [
                    _require(
                        "_scalar_target_supplied == true",
                        "This actor spell needs a selected creature.",
                    ),
                    {
                        "list_push": {
                            "list": "_modifier_dispatch_targets",
                            "value": "_scalar_target_id",
                        }
                    },
                    _set("_modifier_dispatch_distance", "target_distance"),
                    _set("_modifier_dispatch_willing", "target_willing"),
                    _set("_modifier_dispatch_touch", "_caster_can_touch_target"),
                ],
            ),
            _require(
                "len(calm_indifference_targets) == 0",
                (
                    "Calm Emotions indifference needs hostility and wit"
                    "nessed ally damage consumers; choose condition sup"
                    "pression in this boundary."
                ),
            ),
            _call(
                "cast_spell_timed_actor_modifier",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "spell": "{spell}",
                    "slot_level": "{_slot_level}",
                    "save_dc": "{_casting_profile.save_dc}",
                    "concentration_save_modifier": "{_casting_profile.concentration_save_modifier}",
                    "targets": "{_modifier_dispatch_targets}",
                    "targets_max_distance": "{_modifier_dispatch_distance}",
                    "all_targets_willing": "{_modifier_dispatch_willing}",
                    "caster_can_touch_all_targets": "{_modifier_dispatch_touch}",
                    "all_targets_in_area": "{all_targets_in_area}",
                    "origin_distance": "{origin_distance}",
                    "calm_indifference_targets": "{calm_indifference_targets}",
                    "calm_indifferent_toward": "{calm_indifferent_toward}",
                    "calm_allies": "{calm_allies}",
                    "forego_save_targets": "{_modifier_forego_targets}",
                },
                {
                    "_nested_spell_summary_rows": "_spell_summary_rows",
                    "_nested_spell_detail_rows": "_spell_detail_rows",
                },
            ),
            *[
                {
                    "for_each": {
                        "collection": f"_nested_spell_{name}_rows",
                        "as": "_nested_spell_row",
                        "do": [
                            {
                                "list_push": {
                                    "list": f"_spell_{name}_rows",
                                    "value": "_nested_spell_row",
                                }
                            },
                        ],
                    }
                }
                for name in ("summary", "detail")
            ],
        ],
    )
    cast["effects"] = [
        e
        for e in cast["effects"]
        if e.get("condition") != "_spell_definition.family == 'timed_actor_modifier'"
    ]
    index = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition", "").startswith("_spell_definition.family ==")
    )
    cast["effects"].insert(index, branch)
    end_ward = _when(
        "caster_state.has_actor_spell_modifiers == true",
        [
            _call(
                "end_srd_sanctuary_for_actor",
                {"actor_state": "{_caster_state_id}", "lasting_state": "state"},
            ),
        ],
    )
    if "end_srd_sanctuary_for_actor" not in str(cast["effects"]):
        cast["effects"].insert(index, end_ward)
    changed["cast_spell"] = cast
    test = deepcopy(tools["d20_test"])
    test["effects"] = [
        effect
        for effect in test["effects"]
        if effect.get("condition")
        != ("actor_state != null and actor_state.has_actor_spell_modifiers == true")
    ]
    for effect in test["effects"]:
        field = effect.get("set", {}).get("field")
        if field == "_effective_advantage" and "spell_beacon_of_hope" not in effect["set"]["value"]:
            effect["set"]["value"] += (
                " or (actor_state != null and kind == 'saving_throw"
                "' and ability == 'wisdom' and actor_state.spell_be"
                "acon_of_hope == true)"
            )
        if (
            field == "_effective_disadvantage"
            and "spell_ray_of_enfeeblement" not in effect["set"]["value"]
        ):
            effect["set"]["value"] += (
                " or (actor_state != null and ability == 'strength'"
                " and actor_state.spell_ray_of_enfeeblement == true"
                ")"
            )
        if (
            effect.get("condition") == "actor_state != null and kind == 'attack_roll'"
            and "consume_srd_next_attack_disadvantage" in str(effect)
            and "end_srd_sanctuary_for_actor" not in str(effect)
        ):
            effect["on_true"].append(
                _when(
                    "actor_state.has_actor_spell_modifiers == true",
                    [
                        _call(
                            "end_srd_sanctuary_for_actor",
                            {"actor_state": "{_actor_state_id}", "lasting_state": "state"},
                        )
                    ],
                )
            )
    changed["d20_test"] = test
    death = deepcopy(tools["death_saving_throw"])
    if "_actor_modifier_death_advantage" not in str(death["effects"]):
        index = next(i for i, e in enumerate(death["effects"]) if "dice" in e)
        death["effects"][index:index] = [
            _set("_actor_modifier_death_advantage", False),
            _when(
                "target.has_actor_spell_modifiers == true",
                [
                    _call(
                        "resolve_srd_actor_spell_modifiers",
                        {"target_state": "{_target_id}", "lasting_state": "state"},
                        ["_actor_modifier_death_advantage"],
                    )
                ],
            ),
        ]
        next(e for e in death["effects"] if "dice" in e)["dice"]["advantage"] = (
            "_actor_modifier_death_advantage == true"
        )
    changed["death_saving_throw"] = death
    heal = deepcopy(tools["cast_spell_healing"])
    if "_actor_modifier_maximum_healing" not in str(heal["effects"]):
        index = (
            next(
                i
                for i, e in enumerate(heal["effects"])
                if e.get("set", {}).get("field") == "_spell_healing_amount"
            )
            + 1
        )
        heal["effects"].insert(
            index,
            _when(
                "target.has_actor_spell_modifiers == true",
                [
                    _call(
                        "resolve_srd_actor_spell_modifiers",
                        {"target_state": "{_target_id}", "lasting_state": "state"},
                        ["_actor_modifier_maximum_healing"],
                    ),
                    _when(
                        "_actor_modifier_maximum_healing == true",
                        [
                            _set("_spell_healing_die_max", 4),
                            *[
                                _when(
                                    f"healing_die == 'd{side}'",
                                    [_set("_spell_healing_die_max", side)],
                                )
                                for side in (6, 8, 10, 12)
                            ],
                            _set(
                                "_spell_healing_amount",
                                (
                                    "max(0, _spell_healing_dice * "
                                    "_spell_healing_die_ma"
                                    "x + _spell_healing_modifier)"
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        )
    changed["cast_spell_healing"] = heal
    immunity = deepcopy(tools["check_srd_target_condition_immunity"])
    if "_actor_modifier_immunities" not in str(immunity["effects"]):
        immunity["effects"].append(
            _when(
                "target_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "resolve_srd_actor_spell_modifiers",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_actor_modifier_immunities"],
                    ),
                    _set(
                        "_source_condition_immune",
                        (
                            "_source_condition_immune == true or condition in _"
                            "actor_modifier_immunities"
                        ),
                    ),
                ],
            )
        )
    changed["check_srd_target_condition_immunity"] = immunity
    remove = deepcopy(tools["remove_srd_condition"])
    if "timed_condition_unowned_frightened" not in str(remove["effects"]):
        remove["effects"].insert(
            0,
            _when(
                "condition == 'frightened'",
                [
                    _set("target.timed_condition_unowned_frightened", False),
                    _set("target.actor_spell_underlying_frightened", False),
                ],
            ),
        )
    changed["remove_srd_condition"] = remove
    for name in ("choose_srd_self_restoration", "validate_srd_self_restoration_choice"):
        tool = deepcopy(tools[name])
        for effect in [*tool.get("pre_conditions", []), *_walk_effects(tool["effects"])]:
            text = effect.get("condition", "")
            if "actor_spell_underlying" in text:
                continue
            for condition in ("charmed", "frightened"):
                text = text.replace(
                    f"'{condition}' in actor_state.conditions",
                    f"('{condition}' in actor_state.conditions or "
                    f"actor_state.actor_spell_underlying_{condition} == true)",
                )
            text = text.replace(
                "condition in actor_state.conditions",
                "(condition in actor_state.conditions or "
                "(condition == 'charmed' and "
                "actor_state.actor_spell_underlying_charmed == true) or "
                "(condition == 'frightened' and "
                "actor_state.actor_spell_underlying_frightened == true))",
            )
            if "condition" in effect:
                effect["condition"] = text
        changed[name] = tool
    for name in ("apply_srd_condition", "resolve_srd_timed_condition_initial_save"):
        tool = deepcopy(tools[name])
        for e in _walk_effects(tool["effects"]):
            if "has_timed_actor_defenses == true" in e.get("condition", ""):
                field = "target" if name == "apply_srd_condition" else "target"
                clauses = e["condition"].replace("(", "").replace(")", "").split(" or ")
                if all(
                    clause
                    in {
                        f"{field}.has_timed_actor_defenses == true",
                        f"{field}.has_actor_spell_modifiers == true",
                    }
                    for clause in clauses
                ):
                    e["condition"] = (
                        f"{field}.has_timed_actor_defenses == true "
                        f"or {field}.has_actor_spell_modifiers == true"
                    )
                elif f"{field}.has_actor_spell_modifiers == true" not in e["condition"]:
                    e["condition"] = e["condition"].replace(
                        f"{field}.has_timed_actor_defenses == true",
                        f"({field}.has_timed_actor_defenses == true "
                        f"or {field}.has_actor_spell_modifiers == true)",
                    )
        changed[name] = tool
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    if "check_srd_sanctuary_target" not in str(attack["effects"]):
        attack["effects"].insert(
            0,
            _when(
                "target_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "check_srd_sanctuary_target",
                        {
                            "actor_state": "{_actor_state_id}",
                            "target_state": "{_target_state_id}",
                            "lasting_state": "state",
                        },
                    )
                ],
            ),
        )
        attack["effects"].append(
            _when(
                "actor_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "end_srd_sanctuary_for_actor",
                        {"actor_state": "{_actor_state_id}", "lasting_state": "state"},
                    )
                ],
            )
        )
    changed["resolve_srd_attack_conditions"] = attack
    move = deepcopy(tools["spend_combat_movement"])
    for e in move["effects"]:
        if (
            e.get("condition", "").startswith("difficult_terrain == true and")
            and "spell_freedom" not in e["condition"]
        ):
            e["condition"] += " and actor_state.spell_freedom_of_movement != true"
    changed["spend_combat_movement"] = move
    mode = next(p for p in move["parameters"] if p["name"] == "movement_kind")
    if "swim" not in mode["enum_values"]:
        mode["enum_values"].append("swim")
        index = next(
            i
            for i, e in enumerate(move["effects"])
            if e.get("set", {}).get("field") == "_mobility_refresh_speed"
        )
        move["effects"].insert(
            index,
            _when(
                "movement_kind == 'swim'",
                [
                    _require(
                        (
                            "actor_state.spell_freedom_of_movement == true or ("
                            "actor_state.swim_speed != null and actor_state.swi"
                            "m_speed > 0)"
                        ),
                        "Swimming in this mode needs a Swim Speed.",
                    ),
                ],
            ),
        )
        move["effects"][index + 1]["set"]["value"] += (
            " or movement_kind == 'swim' or combat.movement_mode == 'swim'"
        )
    budget = deepcopy(tools["refresh_srd_movement_budget"])
    if "combat.movement_mode == 'swim'" not in str(budget["effects"]):
        branch = budget["effects"][0]["on_true"]
        index = next(
            i
            for i, e in enumerate(branch)
            if e.get("set", {}).get("field") == "_movement_speed"
            and e["set"]["value"] == "actor_state.speed"
        )
        branch.insert(index + 1, _swim_budget())
    changed["refresh_srd_movement_budget"] = budget
    _compose_damage_consumers(tools, changed)
    process = deepcopy(tools["process_srd_turn_effects"])
    if "repeat_srd_enfeeblement_save" not in str(process["effects"]):
        process["effects"].append(
            _when(
                (
                    "boundary_phase == 'end' and boundary_actor != '' a"
                    "nd state.actor_spell_projection_target_ids != null"
                ),
                [
                    _set("_actor_repeat_records", []),
                    _when(
                        "lasting_state.turn_effects != null",
                        [_set("_actor_repeat_records", "lasting_state.turn_effects")],
                    ),
                    {
                        "for_each": {
                            "collection": "_actor_repeat_records",
                            "as": "_actor_repeat_record",
                            "do": [
                                _when(
                                    (
                                        "_actor_repeat_record.kind == "
                                        "'actor_spell_modifier"
                                        "' and _actor_repeat_record.mode == "
                                        "'enfeeblement_f"
                                        "ailed' and _actor_repeat_record.target == "
                                        "boundary"
                                        "_actor"
                                    ),
                                    [
                                        _call(
                                            "repeat_srd_enfeeblement_save",
                                            {
                                                "target_state": "{boundary_actor}",
                                                "caster_state": "{_actor_repeat_record.caster}",
                                                "lasting_state": "{_lasting_state_id}",
                                                "save_dc": "{_actor_repeat_record.save_dc}",
                                                "started_tick": (
                                                    "{_actor_repeat_record.started_tick}"
                                                ),
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        }
                    },
                ],
            )
        )
    changed["process_srd_turn_effects"] = process


def _compose_damage_consumers(tools, changed):
    """Apply a roll's d8 penalty once at its owner, before saves and typed mitigation."""
    for name in (
        "resolve_combat_attack",
        "resolve_srd_followup_weapon_attack",
        "resolve_opportunity_attack",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        if "modify_srd_actor_damage_roll" not in str(tool["effects"]):

            def edit(effects):
                result = []
                for effect in effects:
                    for key in ("on_true", "on_false"):
                        if key in effect:
                            effect[key] = edit(effect[key])
                    if "for_each" in effect:
                        effect["for_each"]["do"] = edit(effect["for_each"]["do"])
                    if effect.get("call") == "apply_srd_damage" and effect.get("params", {}).get(
                        "amount"
                    ) in ("{_styled_weapon_damage}", "{_damage_roll.total}"):
                        original = effect["params"]["amount"][1:-1]
                        adjustment = effect["params"]["adjustment"]
                        adjust_expression = (
                            adjustment[1:-1] if isinstance(adjustment, str) else str(adjustment)
                        )
                        hook = _when(
                            "actor_state.has_actor_spell_modifiers == true",
                            [
                                _call(
                                    "modify_srd_actor_damage_roll",
                                    {
                                        "actor_state": "{_actor_state_id}",
                                        "lasting_state": "state",
                                        "amount": "{" + original + " + " + adjust_expression + "}",
                                    },
                                    ["_actor_modified_damage"],
                                ),
                            ],
                        )
                        if "condition" in effect:
                            hook["condition"] += " and (" + effect["condition"] + ")"
                        setup = [
                            _set("_actor_modified_damage", original + " + " + adjust_expression),
                            hook,
                        ]
                        result += (
                            [_when(effect["condition"], setup)] if "condition" in effect else setup
                        )
                        effect["params"]["amount"] = "{max(0, _actor_modified_damage)}"
                        effect["params"]["adjustment"] = 0
                    result.append(effect)
                return result

            tool["effects"] = edit(tool["effects"])
        if "_actor_modifier_strength_disadvantage" not in str(tool["effects"]):
            index = next(
                (
                    i
                    for i, e in enumerate(tool["effects"])
                    if e.get("set", {}).get("field") == "_effective_disadvantage"
                ),
                None,
            )
            if index is not None:
                tool["effects"].insert(
                    index,
                    _when(
                        (
                            "actor_state.has_actor_spell_modifiers == true and "
                            "attack_ability == 'strength'"
                        ),
                        [
                            _call(
                                "resolve_srd_actor_spell_modifiers",
                                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
                                ["_actor_modifier_strength_disadvantage"],
                            ),
                            _set(
                                "_condition_disadvantage",
                                (
                                    "_condition_disadvantage == true or "
                                    "_actor_modifier"
                                    "_strength_disadvantage == true"
                                ),
                            ),
                        ],
                    ),
                )
        changed[name] = tool
    single = deepcopy(tools["cast_spell_single_target_save_damage"])
    if "modify_srd_actor_damage_roll" not in str(single["effects"]):
        for effects in [
            single["effects"],
            *[
                e[b]
                for e in _walk_effects(single["effects"])
                for b in ("on_true", "on_false")
                if b in e
            ],
        ]:
            index = next(
                (
                    i
                    for i, e in enumerate(effects)
                    if e.get("set", {}).get("field") == "_spell_damage_amount"
                    and e["set"]["value"] == "_spell_damage.total + flat_damage"
                ),
                None,
            )
            if index is not None:
                effects.insert(
                    index + 1,
                    _when(
                        "caster_state.has_actor_spell_modifiers == true",
                        [
                            _call(
                                "modify_srd_actor_damage_roll",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "lasting_state": "state",
                                    "amount": "{_spell_damage_amount}",
                                },
                                {"_spell_damage_amount": "_actor_modified_damage"},
                            ),
                        ],
                    ),
                )
                break
    changed["cast_spell_single_target_save_damage"] = single
    area = deepcopy(tools["cast_spell_instantaneous_area_save_damage"])
    if "modify_srd_actor_damage_roll" not in str(area["effects"]):
        owner = next(
            effects
            for effects in _effect_lists(area["effects"])
            if any(
                e.get("set", {}).get("field") == "_spell_damage_total"
                and e["set"]["value"] == "_spell_damage.total"
                for e in effects
            )
        )
        index = next(
            i
            for i, e in enumerate(owner)
            if e.get("set", {}).get("field") == "_spell_damage_total"
            and e["set"]["value"] == "_spell_damage.total"
        )
        owner.insert(
            index + 1,
            _when(
                "caster_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "modify_srd_actor_damage_roll",
                        {
                            "actor_state": "{_caster_state_id}",
                            "lasting_state": "state",
                            "amount": "{_spell_damage_total}",
                        },
                        {"_spell_damage_total": "_actor_modified_damage"},
                    ),
                ],
            ),
        )
        for e in _walk_effects(area["effects"]):
            if e.get("call") == "resolve_spell_area_save_damage_target":
                e["params"]["damage_total"] = "{_spell_damage_total}"
    changed["cast_spell_instantaneous_area_save_damage"] = area


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _effect_lists(effect[key])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def _swim_budget():
    return _when(
        "combat.movement_mode == 'swim'",
        [
            _set("_movement_speed", 0),
            _when(
                "actor_state.swim_speed != null",
                [_set("_movement_speed", "actor_state.swim_speed")],
            ),
            _when(
                "actor_state.spell_freedom_of_movement == true",
                [_set("_movement_speed", "max(_movement_speed, actor_state.speed)")],
            ),
        ],
    )


PUBLIC_TOOLS = {
    "escape_srd_nonmagical_restraint",
    "record_srd_creature_relationship",
    "read_srd_creature_attitude",
    "decide_srd_sanctuary_target",
    "resolve_srd_magical_truth",
    "record_srd_severed_body_part",
    "choose_srd_haste_action",
    "record_srd_actor_base_size",
    "read_srd_carried_item_size",
}


def refresh_exploration_runtime(text):
    "Derive exploration casting from the final public cast so its parameters and owners agree."
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    changed = {"cast_spell": deepcopy(runtime["tools"]["cast_spell"])}
    _compose_slow_cast_failure(runtime["tools"], changed)
    changed["cast_spell_outside_combat"] = deepcopy(changed["cast_spell"])
    for name in ("record_srd_control_held_items", "drop_srd_control_held_items"):
        tool = deepcopy(runtime["tools"][name])
        if "retire_srd_released_shillelagh" not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    "target_state.has_actor_weapon_modifiers == true",
                    [
                        _call(
                            "retire_srd_released_shillelagh",
                            {"actor_state": "{_target_state_id}", "lasting_state": "state"},
                        ),
                        _call(
                            "refresh_srd_timed_condition_target",
                            {"target_state": "{_target_state_id}", "lasting_state": "state"},
                        ),
                    ],
                )
            )
        changed[name] = tool
    project = deepcopy(runtime["tools"]["refresh_srd_timed_condition_target"])
    project["effects"] = [
        e
        for e in project["effects"]
        if not (
            e.get("condition") == "target_state.haste_lethargy == true"
            and "project_srd_haste_lethargy" in str(e)
        )
    ]
    project["effects"].append(
        _when(
            "target_state.haste_lethargy == true",
            [
                _call(
                    "project_srd_haste_lethargy",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                ),
            ],
        )
    )
    changed["refresh_srd_timed_condition_target"] = project
    for name in ("record_srd_control_held_items", "drop_srd_control_held_items"):
        owner = changed[name]
        if "retire_srd_released_sized_items" not in str(owner["effects"]):
            owner["effects"].append(
                _when(
                    "target_state.actor_size_projected == true",
                    [
                        _call(
                            "retire_srd_released_sized_items",
                            {
                                "actor_state": "{_target_state_id}",
                                "lasting_state": "state",
                            },
                        ),
                        _call(
                            "refresh_srd_timed_condition_target",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "state",
                            },
                        ),
                    ],
                )
            )
    return _emit(text, changed)


def _gate_advanced_projection(changed):
    "Keep unused advanced profiles off ordinary condition subjects and retain expired cleanup."
    tool = changed["resolve_srd_timed_actor_condition_suppression"]
    initializers = [
        e for e in tool["effects"] if e.get("set", {}).get("field", "").startswith("_advanced_")
    ]
    tool["effects"] = [e for e in tool["effects"] if e not in initializers]
    tail = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "target_state.spell_foresight"
    )
    advanced = tool["effects"][tail:]
    tool["effects"][tail:] = [_when("target_state.has_actor_spell_modifiers == true", advanced)]
    tool["effects"].insert(0, _when("target_state.has_actor_spell_modifiers == true", initializers))


def _compose_advanced_actor_modifiers(tools, changed):
    """Derive source-owned actor profiles at the existing projection and D20 seams."""
    fields = {
        "foresight": "_advanced_foresight",
        "glibness": "_advanced_glibness",
        "divine_favor": "_advanced_divine_favor",
        "regenerate": "_advanced_regenerate",
        "haste": "_advanced_haste",
        "slow": "_advanced_slow",
        "magic_weapon": "_advanced_magic_weapon",
        "shillelagh": "_advanced_shillelagh",
    }
    resolve = changed["resolve_srd_actor_spell_modifiers"]
    resolve["effects"][0:0] = [_set(v, False) for v in fields.values()] + [
        _set("_advanced_fire_shield", "'none'"),
        _set("_advanced_enlarge_mode", "'none'"),
    ]
    active = next(
        e
        for e in _walk_effects(resolve["effects"])
        if e.get("condition") == "_actor_modifier_active == true"
    )
    for mode, field in fields.items():
        active["on_true"].append(
            _when(f"_actor_modifier_record.mode == '{mode}'", [_set(field, True)])
        )
    active["on_true"].extend(
        [
            _when(
                "_actor_modifier_record.mode == 'foresight'",
                [_set("_actor_modifier_death_advantage", True)],
            ),
            _when(
                "_actor_modifier_record.mode == 'fire_shield_warm'",
                [_set("_advanced_fire_shield", "'warm'")],
            ),
            _when(
                "_actor_modifier_record.mode == 'fire_shield_chill'",
                [_set("_advanced_fire_shield", "'chill'")],
            ),
            _when(
                (
                    "_actor_modifier_record.mode == 'enlarge' or "
                    "_actor_modifier_record.mode == 'reduce'"
                ),
                [_set("_advanced_enlarge_mode", "_actor_modifier_record.mode")],
            ),
        ]
    )
    suppression = changed["resolve_srd_timed_actor_condition_suppression"]
    for effect in _walk_effects(suppression["effects"]):
        if effect.get("call") == "resolve_srd_actor_spell_modifiers":
            effect["export_context"].update(
                {v: v for v in fields.values()}
                | {
                    "_advanced_fire_shield": "_advanced_fire_shield",
                    "_advanced_enlarge_mode": "_advanced_enlarge_mode",
                }
            )
    suppression["effects"][0:0] = [_set(v, False) for v in fields.values()] + [
        _set("_advanced_fire_shield", "'none'"),
        _set("_advanced_enlarge_mode", "'none'"),
    ]
    suppression["effects"].extend(
        [_set("target_state.spell_" + mode, variable) for mode, variable in fields.items()]
    )
    suppression["effects"].extend(
        [
            _set(
                "target_state.has_actor_weapon_modifiers",
                "_advanced_magic_weapon == true or _advanced_shillelagh == true",
            ),
            _set("target_state.spell_fire_shield", "_advanced_fire_shield"),
            _set("target_state.spell_enlarge_mode", "_advanced_enlarge_mode"),
            _set("_advanced_advantage_keys", []),
            _set("_advanced_disadvantage_keys", []),
            _when(
                "_advanced_foresight == true",
                [{"list_push": {"list": "_advanced_advantage_keys", "value": "'all'"}}],
            ),
            _when(
                "target_state.spell_beacon_of_hope == true",
                [
                    {
                        "list_push": {
                            "list": "_advanced_advantage_keys",
                            "value": "'saving_throw:wisdom'",
                        }
                    }
                ],
            ),
            _when(
                "_advanced_haste == true",
                [
                    {
                        "list_push": {
                            "list": "_advanced_advantage_keys",
                            "value": "'saving_throw:dexterity'",
                        }
                    }
                ],
            ),
            _when(
                "_advanced_enlarge_mode == 'enlarge'",
                [
                    {
                        "list_push": {
                            "list": "_advanced_advantage_keys",
                            "value": "'ability_check:strength'",
                        }
                    },
                    {
                        "list_push": {
                            "list": "_advanced_advantage_keys",
                            "value": "'saving_throw:strength'",
                        }
                    },
                ],
            ),
            _when(
                "_advanced_enlarge_mode == 'reduce'",
                [
                    {
                        "list_push": {
                            "list": "_advanced_disadvantage_keys",
                            "value": "'ability_check:strength'",
                        }
                    },
                    {
                        "list_push": {
                            "list": "_advanced_disadvantage_keys",
                            "value": "'saving_throw:strength'",
                        }
                    },
                ],
            ),
            _when(
                "target_state.spell_ray_of_enfeeblement == true",
                [{"list_push": {"list": "_advanced_disadvantage_keys", "value": "'strength'"}}],
            ),
            _set("target_state.spell_d20_advantage_keys", "_advanced_advantage_keys"),
            _set("target_state.spell_d20_disadvantage_keys", "_advanced_disadvantage_keys"),
        ]
    )
    cast = changed["cast_spell"]
    cast["parameters"].append(
        {
            "name": "actor_spell_choice",
            "type": "enum",
            "enum_values": ["warm", "chill", "enlarge", "reduce"],
            "required": False,
            "default": "warm",
        }
    ) if not any(p["name"] == "actor_spell_choice" for p in cast["parameters"]) else None
    dispatch = next(
        e
        for e in cast["effects"]
        if e.get("condition") == "_spell_definition.family == 'timed_actor_modifier'"
    )
    for effect in _walk_effects(dispatch["on_true"]):
        if effect.get("require", {}).get("error") == "This actor spell needs a selected creature.":
            effect["require"]["condition"] = (
                "_spell_definition.range_kind == 'self' or _scalar_target_supplied == true"
            )
    call_index = next(
        i
        for i, e in enumerate(dispatch["on_true"])
        if e.get("call") == "cast_spell_timed_actor_modifier"
    )
    dispatch["on_true"].insert(
        call_index,
        _when(
            "_spell_definition.range_kind == 'self'",
            [
                _set("_modifier_dispatch_targets", ["_caster_state_id"]),
                _set("_modifier_dispatch_distance", 0),
                _set("_modifier_dispatch_willing", True),
                _set("_modifier_dispatch_touch", True),
            ],
        ),
    )
    for name in (
        "cast_spell_timed_actor_modifier",
        "resolve_srd_actor_modifier_target_cast",
        "install_srd_actor_spell_modifier",
    ):
        changed[name]["parameters"].extend(
            [
                {"name": "effect_choice", "type": "string", "required": False, "default": "warm"},
                {"name": "casting_modifier", "type": "integer", "required": False, "default": 0},
                {
                    "name": "casting_ability",
                    "type": "string",
                    "required": False,
                    "default": "wisdom",
                },
                {"name": "casting_level", "type": "integer", "required": False, "default": 1},
            ]
        )
    for tool_name in (
        "cast_spell",
        "cast_spell_timed_actor_modifier",
        "resolve_srd_actor_modifier_target_cast",
    ):
        for effect in _walk_effects(changed[tool_name]["effects"]):
            if effect.get("call") in (
                "cast_spell_timed_actor_modifier",
                "resolve_srd_actor_modifier_target_cast",
                "install_srd_actor_spell_modifier",
            ):
                for key in (
                    "effect_choice",
                    "casting_modifier",
                    "casting_ability",
                    "casting_level",
                ):
                    expr = key
                    if tool_name == "cast_spell":
                        expr = {
                            "effect_choice": "actor_spell_choice",
                            "casting_modifier": "_casting_profile.modifier",
                            "casting_ability": "_casting_profile.ability",
                            "casting_level": "_casting_profile.scaling_level",
                        }[key]
                    effect["params"][key] = "{" + expr + "}"
    cast_modifier = changed["cast_spell_timed_actor_modifier"]
    for effect in _walk_effects(cast_modifier["effects"]):
        req = effect.get("require", {})
        if req.get("error") == "Selected actor spell targets must satisfy its fixed range.":
            req["condition"] = (
                "_modifier_cast_profile.range_kind == 'self' or (" + req["condition"] + ")"
            )
    target_cast = changed["resolve_srd_actor_modifier_target_cast"]
    target_cast["effects"].insert(
        2,
        _when(
            "_modifier_target_profile.profile == 'fire_shield'",
            [
                _require(
                    "effect_choice == 'warm' or effect_choice == 'chill'",
                    "Choose a warm or chill Fire Shield.",
                ),
            ],
        ),
    )
    install_call_list = next(
        es
        for es in _effect_lists(target_cast["effects"])
        if any(e.get("call") == "install_srd_actor_spell_modifier" for e in es)
    )
    install_call_index = next(
        i
        for i, e in enumerate(install_call_list)
        if e.get("call") == "install_srd_actor_spell_modifier"
    )
    install_call_list.insert(
        install_call_index,
        _when(
            "_modifier_target_profile.profile == 'fire_shield'",
            [
                _when(
                    "effect_choice == 'warm'",
                    [_set("_modifier_install_mode", "'fire_shield_warm'")],
                    [_set("_modifier_install_mode", "'fire_shield_chill'")],
                )
            ],
        ),
    )
    install = changed["install_srd_actor_spell_modifier"]
    for effect in _walk_effects(install["effects"]):
        if effect.get("condition", "").startswith("not (_modifier_existing_record.kind"):
            effect["condition"] = (
                "not (_modifier_existing_record.kind == 'actor_spell_modifier' and "
                "_modifier_existing_record.spell == spell and "
                "_modifier_existing_record.caster == _caster_state_id and "
                "(_modifier_existing_record.target == _target_state_id or spell == "
                "'foresight' or spell == 'magic_weapon' or spell == 'shillelagh'))"
            )
        if "list_push" in effect and isinstance(effect["list_push"].get("value"), dict):
            record = effect["list_push"]["value"]
            record.update(
                casting_ability="casting_ability",
                casting_modifier="casting_modifier",
                casting_level="casting_level",
            )
    install["effects"].append(
        _call("refresh_srd_actor_spell_projections", {"lasting_state": "{_lasting_state_id}"})
    )
    # Kind/ability maps avoid runtime string concatenation and keep expressions below its limit.
    abilities = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
    kinds = ("ability_check", "saving_throw", "attack_roll")
    advantages = {a: dict.fromkeys(kinds, False) for a in abilities}
    disadvantages = {a: dict.fromkeys(kinds, False) for a in abilities}
    for k in ("ability_check", "saving_throw"):
        advantages["strength"][k] = "_advanced_enlarge_mode == 'enlarge'"
        disadvantages["strength"][k] = (
            "_advanced_enlarge_mode == 'reduce' or target_state.spell_ray_of_enfeeblement == true"
        )
    disadvantages["strength"]["attack_roll"] = "target_state.spell_ray_of_enfeeblement == true"
    advantages["dexterity"]["saving_throw"] = "_advanced_haste == true"
    advantages["wisdom"]["saving_throw"] = "target_state.spell_beacon_of_hope == true"
    suppression["effects"].extend(
        [
            _set(
                "target_state.spell_d20_advantages",
                {a: {"kinds": v} for a, v in advantages.items()},
            ),
            _set(
                "target_state.spell_d20_disadvantages",
                {a: {"kinds": v} for a, v in disadvantages.items()},
            ),
        ]
    )
    test = changed["d20_test"]
    for effect in _walk_effects(test["effects"]):
        field = effect.get("set", {}).get("field")
        if field == "_effective_advantage" and isinstance(effect["set"]["value"], str):
            value = effect["set"]["value"].replace("[ability][kind]", "[ability].kinds[kind]")
            value = re.sub(
                (
                    " or \\(actor_state != null and kind == 'saving_throw' and ability "
                    ""
                    ""
                    "== 'wisdom' and actor_state.spell_beacon_of_hope == true\\)"
                ),
                "",
                value,
            )
            value = re.sub(
                (
                    " or \\(actor_state != null and "
                    "actor_state.spell_d20_advantage_keys "
                    "!= null and .*?\\)\\)"
                ),
                "",
                value,
            )
            if "spell_d20_advantages" not in value:
                value += (
                    " or actor_state.spell_foresight == true or "
                    "(actor_state.spell_d20_advantages != null and "
                    "actor_state.spell_d20_advantages[ability].kinds[kind] == true)"
                )
            effect["set"]["value"] = value
        if field == "_effective_disadvantage" and isinstance(effect["set"]["value"], str):
            value = effect["set"]["value"].replace("[ability][kind]", "[ability].kinds[kind]")
            value = re.sub(
                (
                    " or \\(actor_state != null and ability == 'strength' and "
                    "actor_state.spell_ray_of_enfeeblement == true\\)"
                ),
                "",
                value,
            )
            value = re.sub(
                (
                    " or \\(actor_state != null and "
                    "actor_state.spell_d20_disadvantage_keys != null and .*?\\)\\)"
                ),
                "",
                value,
            )
            if "spell_d20_disadvantages" not in value:
                value += (
                    " or (actor_state.spell_d20_disadvantages != null and "
                    "actor_state.spell_d20_disadvantages[ability].kinds[kind] == "
                    "true)"
                )
            effect["set"]["value"] = value
    attack = deepcopy(
        changed.get("resolve_srd_attack_conditions", tools["resolve_srd_attack_conditions"])
    )
    for effect in _walk_effects(attack["effects"]):
        if (
            effect.get("set", {}).get("field") == "_condition_advantage"
            and isinstance(effect["set"]["value"], str)
            and "spell_foresight" not in effect["set"]["value"]
        ):
            effect["set"]["value"] += " or actor_state.spell_foresight == true"
        if (
            effect.get("set", {}).get("field") == "_condition_disadvantage"
            and isinstance(effect["set"]["value"], str)
            and "spell_foresight" not in effect["set"]["value"]
        ):
            effect["set"]["value"] += " or target_state.spell_foresight == true"
    changed["resolve_srd_attack_conditions"] = attack
    if not any(p["name"] == "use_glibness" for p in test["parameters"]):
        test["parameters"].append(
            {"name": "use_glibness", "type": "boolean", "required": False, "default": True}
        )
    if "_glibness_original_number" not in str(test["effects"]):
        for effects in _effect_lists(test["effects"]):
            index = next(
                (
                    i
                    for i, e in enumerate(effects)
                    if e.get("store_as") == "_d20_test" and "dice" in e
                ),
                None,
            )
            if index is not None:
                effects.insert(
                    index + 1,
                    _when(
                        (
                            "actor_state != null and actor_state.spell_glibness == "
                            "true and "
                            "kind == 'ability_check' and ability == 'charisma' and "
                            "use_glibness == true and _d20_test.total < 15"
                        ),
                        [
                            _set("_glibness_original_number", "_d20_test.total"),
                            _set(
                                "_d20_test",
                                {
                                    "count": "_d20_test.count",
                                    "sides": "_d20_test.sides",
                                    "results": "_d20_test.results",
                                    "modifier": "_d20_test.modifier",
                                    "total": 15,
                                },
                            ),
                        ],
                    ),
                )
    ability = deepcopy(changed.get("ability_check", tools["ability_check"]))
    if not any(p["name"] == "use_glibness" for p in ability["parameters"]):
        ability["parameters"].append(
            {"name": "use_glibness", "type": "boolean", "required": False, "default": True}
        )
    for effect in _walk_effects(ability["effects"]):
        if effect.get("call") == "d20_test":
            effect["params"]["use_glibness"] = "{use_glibness}"
    changed["ability_check"] = ability
    truth = _tool(
        "Resolve a magical truth determination about this creature's statement.",
        [_entity("actor_state"), _parameter("statement_is_truthful", "boolean")],
        [
            _set(
                "_magically_detected_truthful",
                "statement_is_truthful or actor_state.spell_glibness == true",
            )
        ],
    )
    truth["metadata"] = {"category": "social", "costs_action": False}
    changed["resolve_srd_magical_truth"] = truth


def _compose_foresight_consumers(tools, changed):
    "Keep all package D20 producers, including Initiative and Help, on the same active source."
    resolve = changed["resolve_srd_actor_spell_modifiers"]
    active = next(
        e
        for e in _walk_effects(resolve["effects"])
        if e.get("condition") == "_actor_modifier_record.mode == 'foresight'"
    )
    active["on_true"].append(_set("lasting_state.has_foresight", True))
    changed["refresh_srd_actor_spell_projections"]["effects"].insert(
        0, _set("lasting_state.has_foresight", False)
    )
    read = _tool(
        "Read this admitted actor's projected Foresight for a D20 owner.",
        [_entity("actor_state")],
        [_set("_foresight_d20_advantage", "actor_state.spell_foresight == true")],
    )
    changed["read_srd_actor_foresight"] = read
    for name in ("start_combat_encounter", "start_combat_encounter_entities"):
        tool = deepcopy(changed.get(name, tools[name]))
        if name == "start_combat_encounter" and "read_srd_actor_foresight" not in str(
            tool["effects"]
        ):
            tool["effects"][0:0] = [
                _call(
                    "read_srd_actor_foresight",
                    {"actor_state": "{" + side + "_actor.participant_id}"},
                    {"_initiative_" + side + "_foresight": "_foresight_d20_advantage"},
                )
                for side in ("first", "second")
            ]
        for effect in _walk_effects(tool["effects"]):
            for side in ("first", "second"):
                if (
                    effect.get("store_as") != "_initiative_" + side + "_roll"
                    or "dice" not in effect
                ):
                    continue
                value = (
                    side + "_actor.spell_foresight"
                    if name.endswith("_entities")
                    else "_initiative_" + side + "_foresight"
                )
                effect["dice"]["advantage"] = value + " == true and " + side + "_surprised != true"
                effect["dice"]["disadvantage"] = (
                    side + "_surprised == true and " + value + " != true"
                )
        changed[name] = tool
    roller = _tool(
        "Roll Initiative for one saved group creature, applying its actual Foresight.",
        [_entity("actor_state")],
        [
            _require(
                (
                    "actor_state.is_alive == true and actor_state.initiative_modifier "
                    ""
                    ""
                    ">= -10 and actor_state.initiative_modifier <= 20"
                ),
                "Initiative needs a living creature's authoritative modifier.",
            ),
            _require(
                (
                    "actor_state.name != null and len(actor_state.name) > 0 and "
                    "len(actor_state.name) <= 200"
                ),
                "Initiative needs its admitted display name.",
            ),
            {
                "dice": {
                    "expression": "d20",
                    "modifier": "actor_state.initiative_modifier",
                    "advantage": "actor_state.spell_foresight == true",
                },
                "store_as": "_actor_initiative_roll",
            },
            _set(
                "_actor_initiative_entry",
                {
                    "entity_id": "_actor_state_id",
                    "initiative_result": (
                        "_actor_initiative_roll.total + " + "_actor_initiative_roll.modifier"
                    ),
                    "roll": "_actor_initiative_roll",
                    "modifier": "_actor_initiative_roll.modifier",
                    "label": "actor_state.name",
                },
            ),
        ],
    )
    changed["roll_srd_actor_group_initiative"] = roller
    group = deepcopy(
        changed.get("start_group_combat_encounter", tools["start_group_combat_encounter"])
    )
    for effects in list(_effect_lists(group["effects"])):
        for index, effect in enumerate(list(effects)):
            if "initiative" not in effect:
                continue
            effects[index : index + 1] = [
                _set("_initiative_order", []),
                {
                    "for_each": {
                        "collection": "_initiative_rollers",
                        "as": "_modifier_initiative_actor",
                        "do": [
                            _call(
                                "roll_srd_actor_group_initiative",
                                {"actor_state": "{_modifier_initiative_actor}"},
                                ["_actor_initiative_entry"],
                            ),
                            {
                                "list_push": {
                                    "list": "_initiative_order",
                                    "value": "_actor_initiative_entry",
                                }
                            },
                        ],
                    }
                },
                {
                    "sort": {
                        "collection": "_initiative_order",
                        "by": "initiative_result",
                        "direction": "desc",
                    }
                },
                _set("combat.order", "_initiative_order"),
                _set("combat.turn_index", -1),
            ]
    changed["start_group_combat_encounter"] = group
    stabilize = deepcopy(changed.get("stabilize_character", tools["stabilize_character"]))
    if not any(p["name"] == "actor_state" for p in stabilize["parameters"]):
        stabilize["parameters"].append(
            {
                "name": "actor_state",
                "type": "string",
                "resolve": "entity",
                "required": False,
                "default": None,
            }
        )
        stabilize["effects"][0:0] = [
            _require(
                "lasting_state.has_foresight != true or actor_state != null",
                ("Identify the creature making the Medicine check while Foresight is active."),
            ),
            _require(
                (
                    "actor_state == null or combat.turn_active != true or "
                    "_actor_state_id == combat.active_actor"
                ),
                "The Medicine check must use the active acting creature.",
            ),
        ]
        stabilize["parameters"].append(
            {
                "name": "lasting_state",
                "type": "string",
                "resolve": "entity",
                "required": False,
                "default": "state",
                "model_hidden": True,
            }
        )
    for effect in _walk_effects(stabilize["effects"]):
        if "dice" in effect:
            effect["dice"]["advantage"] = (
                "(advantage == true or actor_state.spell_foresight == true) and "
                "disadvantage != true"
            )
            effect["dice"]["disadvantage"] = (
                "disadvantage == true and advantage != true and actor_state.spell_foresight != true"
            )
    changed["stabilize_character"] = stabilize

    spell_attack = deepcopy(
        changed.get("cast_spell_attack_damage", tools["cast_spell_attack_damage"])
    )
    for effect in _walk_effects(spell_attack["effects"]):
        if effect.get("set", {}).get("field") == "_spell_attack_disadvantage":
            value = effect["set"]["value"]
            if isinstance(value, str) and "spell_foresight" not in value:
                effect["set"]["value"] += " or target.spell_foresight == true"
    changed["cast_spell_attack_damage"] = spell_attack


def _compose_fire_shield_consumers(tools, changed):
    "Apply Fire Shield at shared resistance and actual melee-hit boundaries, without a Reaction."
    changed["check_srd_actor_spell_light"] = _actor_spell_light_tool()
    hit = _tool(
        "Resolve passive Fire Shield damage from retained actual melee-hit evidence.",
        [
            _entity("defender_state"),
            _entity("attacker_state"),
            _entity("lasting_state"),
            _parameter("shield_mode"),
            _parameter("attack_kind"),
            _parameter("attack_distance", "number"),
            _parameter("attack_outcome"),
            {
                "name": "witness_state_ids",
                "type": "array",
                "required": False,
                "default": None,
                "items": {"type": "string"},
            },
        ],
        [
            _set("_fire_shield_damage_roll", None),
            _set("_fire_shield_damage_applied", 0),
            _when(
                (
                    "(shield_mode == 'warm' or shield_mode == 'chill') and "
                    "attack_kind "
                    "== 'melee' and attack_distance <= 5 and (attack_outcome == 'hit' "
                    ""
                    ""
                    "or attack_outcome == 'critical_hit')"
                ),
                [
                    _set("_fire_shield_damage_type", "'fire'"),
                    _when("shield_mode == 'chill'", [_set("_fire_shield_damage_type", "'cold'")]),
                    {"dice": {"expression": "2d8"}, "store_as": "_fire_shield_damage_roll"},
                    _when(
                        "defender_state.spell_ray_of_enfeeblement == true",
                        [
                            _call(
                                "modify_srd_actor_raw_damage_roll",
                                {
                                    "actor_state": "{_defender_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "amount": "{_fire_shield_damage_roll.total}",
                                },
                                ["_ray_raw_damage"],
                            ),
                            _set(
                                "_fire_shield_damage_roll",
                                {
                                    "count": "_fire_shield_damage_roll.count",
                                    "sides": "_fire_shield_damage_roll.sides",
                                    "results": "_fire_shield_damage_roll.results",
                                    "modifier": "_fire_shield_damage_roll.modifier",
                                    "total": "max(0, _ray_raw_damage)",
                                },
                            ),
                        ],
                    ),
                    _call(
                        "apply_srd_damage",
                        {
                            "target": "{_attacker_state_id}",
                            "amount": "{_fire_shield_damage_roll.total}",
                            "adjustment": 0,
                            "damage_type": "{_fire_shield_damage_type}",
                            "source_state_id": "{_defender_state_id}",
                            "witness_state_ids": "{witness_state_ids}",
                        },
                        {"_fire_shield_damage_applied": "_effective_damage"},
                    ),
                ],
            ),
        ],
    )
    changed["resolve_srd_fire_shield_hit"] = hit
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        tool = deepcopy(changed.get(name, tools[name]))
        for effect in _walk_effects(tool["effects"]):
            clause = effect.get("condition", "")
            if "in target.damage_resistances" not in clause or "spell_fire_shield" in clause:
                continue
            damage_type = (
                "damage_type"
                if name == "apply_srd_damage"
                else (
                    "component_1_damage_type"
                    if "component_1_damage_type" in clause
                    else "component_2_damage_type"
                )
            )
            effect["condition"] = (
                clause[:-1]
                + f" or (target.spell_fire_shield == 'warm' and {damage_type} == 'cold')"
                + f" or (target.spell_fire_shield == 'chill' and {damage_type} == 'fire'))"
            )
        changed[name] = tool
    actors = {
        "resolve_combat_attack": (
            "actor_state",
            "_actor_state_id",
            "target",
            "_target_id",
            "attack_mode",
            "attack_distance",
        ),
        "resolve_combat_attack_followup": (
            "actor_state",
            "_actor_state_id",
            "target",
            "_target_id",
            "attack_mode",
            "attack_distance",
        ),
        "resolve_opportunity_attack": (
            "actor_state",
            "_actor_state_id",
            "target_state",
            "_target_state_id",
            "'melee'",
            "attack_distance",
        ),
        "resolve_adjudicated_creature_attack": (
            "actor",
            "_actor_id",
            "target",
            "_target_id",
            "attack_mode",
            "attack_distance",
        ),
        "resolve_srd_unarmed_effect": (
            "actor_state",
            "_actor_state_id",
            "target_state",
            "_target_state_id",
            "'melee'",
            "distance",
        ),
        "cast_spell_attack_damage": (
            "caster_state",
            "_caster_state_id",
            "target",
            "_target_id",
            "attack_kind",
            "target_distance",
        ),
    }
    for name, (_actor, actor_id, target, target_id, attack_kind, distance) in actors.items():
        tool = deepcopy(changed.get(name, tools[name]))
        if "_attack_target_fire_shield_mode" in str(tool["effects"]):
            changed[name] = tool
            continue
        tool["effects"].insert(
            0, _set("_attack_target_fire_shield_mode", target + ".spell_fire_shield")
        )
        outcome = {
            "cast_spell_attack_damage": "_spell_attack_kind",
            "resolve_srd_unarmed_effect": "_unarmed_attack_kind",
        }.get(name, "_attack_outcome")
        tool["effects"].append(
            _when(
                (
                    "_attack_target_fire_shield_mode == 'warm' or "
                    "_attack_target_fire_shield_mode == 'chill'"
                ),
                [
                    _call(
                        "resolve_srd_fire_shield_hit",
                        {
                            "defender_state": "{" + target_id + "}",
                            "attacker_state": "{" + actor_id + "}",
                            "lasting_state": "state",
                            "shield_mode": "{_attack_target_fire_shield_mode}",
                            "attack_kind": "{" + attack_kind + "}",
                            "attack_distance": "{" + distance + "}",
                            "attack_outcome": "{" + outcome + "}",
                            "witness_state_ids": "{witness_state_ids}",
                        },
                        ["_fire_shield_damage_roll", "_fire_shield_damage_applied"],
                    ),
                ],
            )
        )
        changed[name] = tool


def _compose_regeneration_consumers(tools, changed):
    """Keep Regenerate's healing and regrowth on saved turn/time and body-part owners."""
    sever = _tool(
        "Record a severed body part as an explicit creature fact for later regrowth.",
        [
            _entity("target_state"),
            {
                "name": "lasting_state",
                "type": "string",
                "resolve": "entity",
                "required": False,
                "default": "state",
                "model_hidden": True,
            },
            _parameter("part_id"),
            _parameter("part_name"),
        ],
        [
            _require(
                (
                    "len(part_id) > 0 and len(part_id) <= 80 and '.' not in part_id "
                    "and len(part_name) > 0 and len(part_name) <= 120"
                ),
                "Identify this actual severed body part.",
            ),
            _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)]),
            _when("target_state.body_parts == null", [_set("target_state.body_parts", {})]),
            _require(
                ("part_id in target_state.body_parts or len(target_state.body_parts) < 32"),
                "Only 32 severed body-part facts may be retained.",
            ),
            _require(
                (
                    "part_id not in target_state.body_parts or "
                    "target_state.body_parts[part_id].missing != true"
                ),
                "This body part is already severed.",
            ),
            _set(
                "target_state.body_parts.{part_id}",
                {
                    "name": "part_name",
                    "missing": True,
                    "severed_tick": "lasting_state.lasting_tick",
                    "restored_tick": None,
                },
            ),
            _when("target_state.body_part_ids == null", [_set("target_state.body_part_ids", [])]),
            _set("_body_part_ids", "target_state.body_part_ids"),
            _when(
                "part_id not in _body_part_ids",
                [{"list_push": {"list": "_body_part_ids", "value": "part_id"}}],
            ),
            _set("target_state.body_part_ids", "_body_part_ids"),
        ],
    )
    sever["metadata"] = {"category": "creature-fact", "costs_action": False}
    changed["record_srd_severed_body_part"] = sever
    pulse = _tool(
        ("Resolve Regenerate once per actual turn or elapsed round, preserving its saved source."),
        [
            _entity("target_state"),
            _entity("lasting_state"),
            {"name": "boundary_phase", "type": "string", "required": False, "default": "time"},
            {"name": "boundary_turn", "type": "integer", "required": False, "default": 0},
        ],
        [
            _set("_regenerate_records", []),
            _set("_regenerate_source", None),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_regenerate_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_regenerate_records",
                    "as": "_regenerate_record",
                    "do": [
                        _when(
                            (
                                "_regenerate_record.kind == 'actor_spell_modifier' "
                                "and "
                                "_regenerate_record.mode == 'regenerate' and "
                                "_regenerate_record.target == _target_state_id"
                            ),
                            [
                                _when(
                                    (
                                        "_regenerate_source == null or "
                                        "_regenerate_record.started_tick >= "
                                        "_regenerate_source.started_tick"
                                    ),
                                    [_set("_regenerate_source", "_regenerate_record")],
                                ),
                            ],
                        ),
                    ],
                }
            },
            _when(
                "_regenerate_source != null and target_state.is_alive == true",
                [
                    _set("_regenerate_healing", 0),
                    _set(
                        "_regenerate_until_tick",
                        ("min(lasting_state.lasting_tick, _regenerate_source.expires_tick)"),
                    ),
                    _when(
                        (
                            "boundary_phase == 'start' and lasting_state.lasting_tick "
                            ""
                            "< "
                            "_regenerate_source.expires_tick and "
                            "_regenerate_source.last_heal_turn != boundary_turn"
                        ),
                        [_set("_regenerate_healing", 1)],
                    ),
                    _when(
                        "boundary_phase == 'time' and combat.encounter_active != true",
                        [
                            _set(
                                "_regenerate_healing",
                                (
                                    "max(0, (_regenerate_until_tick - "
                                    "_regenerate_source.last_heal_tick) // 2)"
                                ),
                            )
                        ],
                    ),
                    _when(
                        "_regenerate_healing > 0",
                        [
                            _call(
                                "restore_hit_points",
                                {
                                    "target": "{_target_state_id}",
                                    "amount": "{_regenerate_healing}",
                                    "maximum_amount": "{_regenerate_healing}",
                                },
                            )
                        ],
                    ),
                    _set("_regenerate_part_ids", []),
                    _when(
                        "target_state.body_part_ids != null",
                        [_set("_regenerate_part_ids", "target_state.body_part_ids")],
                    ),
                    {
                        "for_each": {
                            "collection": "_regenerate_part_ids",
                            "as": "_regenerate_part_id",
                            "do": [
                                _set(
                                    "_regenerate_part",
                                    "target_state.body_parts[_regenerate_part_id]",
                                ),
                                _when(
                                    (
                                        "_regenerate_part != null and "
                                        "_regenerate_part.missing == true"
                                    ),
                                    [
                                        _set(
                                            "_regenerate_part_due",
                                            (
                                                "max(_regenerate_part.severed_tick, "
                                                "_regenerate_source.started_tick) + "
                                                "40"
                                            ),
                                        ),
                                        _when(
                                            ("_regenerate_until_tick >= _regenerate_part_due"),
                                            [
                                                _set(
                                                    (
                                                        "target_state.body_parts.{_re"
                                                        "gene"
                                                        "rate_part_id}.missing"
                                                    ),
                                                    False,
                                                ),
                                                _set(
                                                    (
                                                        "target_state.body_parts.{_re"
                                                        "gene"
                                                        "rate_part_id}.restored_tick"
                                                    ),
                                                    "_regenerate_part_due",
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        }
                    },
                    _set("_regenerate_updated_records", []),
                    {
                        "for_each": {
                            "collection": "_regenerate_records",
                            "as": "_regenerate_update",
                            "do": [
                                _when(
                                    (
                                        "_regenerate_update.kind == "
                                        "'actor_spell_modifier' and "
                                        "_regenerate_update.mode == 'regenerate' and "
                                        "_regenerate_update.target == "
                                        "_target_state_id"
                                    ),
                                    [
                                        _set(
                                            "_regenerate_changes",
                                            {"last_heal_tick": "_regenerate_until_tick"},
                                        ),
                                        _when(
                                            "boundary_phase == 'start'",
                                            [
                                                _set(
                                                    "_regenerate_changes",
                                                    {
                                                        "last_heal_tick": "_regenerate_until_tick",
                                                        "last_heal_turn": "boundary_turn",
                                                    },
                                                )
                                            ],
                                        ),
                                        _set(
                                            "_regenerate_update",
                                            ("merge(_regenerate_update, _regenerate_changes)"),
                                        ),
                                    ],
                                ),
                                {
                                    "list_push": {
                                        "list": "_regenerate_updated_records",
                                        "value": "_regenerate_update",
                                    }
                                },
                            ],
                        }
                    },
                    _set("lasting_state.turn_effects", "_regenerate_updated_records"),
                ],
            ),
        ],
    )
    changed["resolve_srd_regeneration_boundary"] = pulse
    install = changed["install_srd_actor_spell_modifier"]
    for effect in _walk_effects(install["effects"]):
        value = effect.get("list_push", {}).get("value")
        if isinstance(value, dict) and "started_tick" in value:
            value.update(last_heal_tick="lasting_state.lasting_tick", last_heal_turn=None)
    install["effects"].append(
        _when(
            "mode == 'regenerate'",
            [
                {"dice": {"expression": "4d8"}, "store_as": "_regenerate_initial_healing_roll"},
                _set("_regenerate_initial_healing", "_regenerate_initial_healing_roll.total + 15"),
                _call(
                    "restore_hit_points",
                    {
                        "target": "{_target_state_id}",
                        "amount": "{_regenerate_initial_healing}",
                        "maximum_amount": 47,
                    },
                ),
                _set("lasting_state.has_regenerate", True),
            ],
        )
    )
    process = deepcopy(changed.get("process_srd_turn_effects", tools["process_srd_turn_effects"]))
    if "resolve_srd_regeneration_boundary" not in str(process["effects"]):
        process["effects"].insert(
            0,
            _when(
                "lasting_state.has_regenerate == true",
                [
                    _set(
                        "_regenerate_boundary_targets",
                        "lasting_state.actor_spell_projection_target_ids",
                    ),
                    {
                        "for_each": {
                            "collection": "_regenerate_boundary_targets",
                            "as": "_regenerate_boundary_target",
                            "do": [
                                _when(
                                    (
                                        "boundary_phase == 'time' or (boundary_phase "
                                        "== 'start' and "
                                        "boundary_actor == "
                                        "_regenerate_boundary_target)"
                                    ),
                                    [
                                        _call(
                                            "resolve_srd_regeneration_boundary",
                                            {
                                                "target_state": "{_regenerate_boundary_target}",
                                                "lasting_state": "{_lasting_state_id}",
                                                "boundary_phase": "{boundary_phase}",
                                                "boundary_turn": "{boundary_turn}",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        }
                    },
                ],
            ),
        )
    changed["process_srd_turn_effects"] = process


def _actor_spell_light_tool():
    "Read exact active Fire Shield light radii; dim radius includes its bright interior."
    return _tool(
        ("Read the admitted actor's active spell light, in feet from its saved position."),
        [_entity("actor_state"), _entity("lasting_state")],
        [
            _set("_actor_spell_bright_light_feet", 0),
            _set("_actor_spell_dim_light_feet", 0),
            _when(
                (
                    "actor_state.has_actor_spell_modifiers == true and "
                    "lasting_state.turn_effects != null"
                ),
                [
                    _set("_actor_light_records", "lasting_state.turn_effects"),
                    {
                        "for_each": {
                            "collection": "_actor_light_records",
                            "as": "_actor_light_record",
                            "do": [
                                _when(
                                    (
                                        "_actor_light_record.kind == "
                                        "'actor_spell_modifier' and "
                                        "_actor_light_record.target == "
                                        "_actor_state_id and "
                                        "(_actor_light_record.mode == "
                                        "'fire_shield_warm' or "
                                        "_actor_light_record.mode == "
                                        "'fire_shield_chill')"
                                    ),
                                    [
                                        _call(
                                            "check_srd_actor_spell_modifier_active",
                                            {
                                                "target_state": "{_actor_state_id}",
                                                "source_state": "{_actor_light_record.caster}",
                                                "lasting_state": "{_lasting_state_id}",
                                                "spell": "{_actor_light_record.spell}",
                                                "started_tick": (
                                                    "{_actor_light_record." + "started_tick}"
                                                ),
                                                "expires_tick": (
                                                    "{_actor_light_record." + "expires_tick}"
                                                ),
                                                "concentration": (
                                                    "{_actor_light_record." + "concentration}"
                                                ),
                                            },
                                            ["_actor_modifier_active"],
                                        ),
                                        _when(
                                            "_actor_modifier_active == true",
                                            [
                                                _set("_actor_spell_bright_light_feet", 10),
                                                _set("_actor_spell_dim_light_feet", 20),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        }
                    },
                ],
            ),
        ],
    )


def _compose_divine_favor_consumers(tools, changed):
    "Add Divine Favor to actual weapon hits in one typed damage event, preserving critical dice."
    damage = _tool(
        "Resolve a weapon hit with Divine Favor's actual Radiant damage roll.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _entity("lasting_state"),
            _parameter("amount", "number"),
            _parameter("adjustment", "number"),
            _parameter("damage_type"),
            _parameter("critical_hit", "boolean"),
            {
                "name": "witness_state_ids",
                "type": "array",
                "required": False,
                "default": None,
                "items": {"type": "string"},
            },
        ],
        [
            {
                "dice": {"expression": "1d4", "critical": "critical_hit"},
                "store_as": "_divine_favor_roll",
            },
            _set("_divine_favor_amount", "_divine_favor_roll.total"),
            _when(
                "actor_state.spell_ray_of_enfeeblement == true",
                [
                    _call(
                        "modify_srd_actor_raw_damage_roll",
                        {
                            "actor_state": "{_actor_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "amount": "{_divine_favor_amount}",
                        },
                        {"_divine_favor_amount": "_ray_raw_damage"},
                    ),
                ],
            ),
            _set("_divine_favor_amount", "max(0, _divine_favor_amount)"),
            _set("_divine_favor_base", "max(0, amount + adjustment)"),
            _call(
                "apply_srd_composite_damage",
                {
                    "target": "{_target_state_id}",
                    "component_1_amount": "{_divine_favor_base}",
                    "component_1_damage_type": "{damage_type}",
                    "component_2_amount": "{_divine_favor_amount}",
                    "component_2_damage_type": "radiant",
                    "critical_hit": "{critical_hit}",
                    "source_state_id": "{_actor_state_id}",
                    "witness_state_ids": "{witness_state_ids}",
                },
                [
                    "_effective_damage",
                    "_hp_before",
                    "_hp_after",
                    "_is_alive_after",
                    "_temp_hp_absorbed",
                    "_incoming_damage",
                    "_is_alive_after",
                ],
            ),
            _set("_hp_lost", "max(0, _hp_before - _hp_after)"),
        ],
    )
    changed["apply_srd_divine_favor_weapon_damage"] = damage
    composite = deepcopy(
        changed.get("apply_srd_composite_damage", tools["apply_srd_composite_damage"])
    )
    if not any(p["name"] == "critical_hit" for p in composite["parameters"]):
        composite["parameters"].append(
            {"name": "critical_hit", "type": "boolean", "required": False, "default": False}
        )
        for effects in list(_effect_lists(composite["effects"])):
            for index, effect in enumerate(list(effects)):
                if effect.get("increment", {}).get("field") == "target.death_failures":
                    effects[index] = _when(
                        "critical_hit == true",
                        [{"increment": {"field": "target.death_failures", "amount": 2}}],
                        [effect],
                    )
    changed["apply_srd_composite_damage"] = composite

    for name, actor, actor_id, target_id in (
        ("resolve_combat_attack", "actor_state", "_actor_state_id", "_target_id"),
        ("resolve_combat_attack_followup", "actor_state", "_actor_state_id", "_target_id"),
        ("resolve_opportunity_attack", "actor_state", "_actor_state_id", "_target_state_id"),
        ("resolve_adjudicated_creature_attack", "actor", "_actor_id", "_target_id"),
    ):
        tool = deepcopy(changed.get(name, tools[name]))
        if "apply_srd_divine_favor_weapon_damage" in str(tool["effects"]):
            changed[name] = tool
            continue
        qualified = actor + (
            ".spell_divine_favor == true and (_attack_outcome == 'hit' or "
            "_attack_outcome == 'critical_hit')"
        )
        if name in ("resolve_opportunity_attack", "resolve_adjudicated_creature_attack"):
            tool["parameters"].append(
                {
                    "name": "attack_uses_weapon",
                    "type": "boolean",
                    "required": False,
                    "default": None,
                }
            )
            tool["effects"].insert(
                0,
                _require(
                    actor + ".spell_divine_favor != true or attack_uses_weapon != null",
                    (
                        "Identify whether the actual attack uses a weapon while "
                        "Divine "
                        "Favor is active."
                    ),
                ),
            )
            qualified += " and attack_uses_weapon == true"
        for effects in list(_effect_lists(tool["effects"])):
            for index, effect in enumerate(list(effects)):
                if effect.get("call") != "apply_srd_damage" or "_graze" in str(
                    effect.get("params", {}).get("amount")
                ):
                    continue
                parameters = effect["params"]
                replacement = _call(
                    "apply_srd_divine_favor_weapon_damage",
                    {
                        "actor_state": "{" + actor_id + "}",
                        "target_state": "{" + target_id + "}",
                        "lasting_state": "state",
                        "amount": parameters["amount"],
                        "adjustment": parameters["adjustment"],
                        "damage_type": parameters["damage_type"],
                        "critical_hit": parameters.get("critical_hit", False),
                        "witness_state_ids": parameters.get("witness_state_ids", None),
                    },
                    effect.get("export_context", {})
                    | {
                        "_divine_favor_roll": "_divine_favor_roll",
                        "_divine_favor_amount": "_divine_favor_amount",
                    },
                )
                branch = _when(qualified, [replacement], [effect])
                if effect.get("condition"):
                    branch = _when(effect["condition"], [branch])
                    effect.pop("condition")
                effects[index] = branch
        changed[name] = tool


WEAPON_SPELL_EXPORTS = (
    "_weapon_spell_bonus",
    "_weapon_spell_ability",
    "_weapon_spell_modifier",
    "_weapon_spell_die",
    "_weapon_spell_magic",
)


def _compose_weapon_spell_consumers(tools, changed):
    "Bind weapon spells to one authoritative inventory copy and the existing holding owner."
    validate = _tool(
        ("Identify one actual inventory weapon; duplicate copies require distinct item authority."),
        [_entity("actor_state"), _parameter("weapon")],
        [
            _require(
                (
                    "actor_state.inventory != null and weapon != null and weapon in "
                    "actor_state.inventory"
                ),
                "Select a weapon from the target's admitted inventory.",
            ),
            {
                "table_lookup": {"table": "definitions.weapons", "key": "weapon", "required": True},
                "store_as": "_spell_weapon_definition",
            },
            _set("_spell_weapon_inventory", "actor_state.inventory"),
            _set("_spell_weapon_copies", 0),
            {
                "for_each": {
                    "collection": "_spell_weapon_inventory",
                    "as": "_spell_weapon_item",
                    "do": [
                        _when(
                            "_spell_weapon_item == weapon",
                            [_set("_spell_weapon_copies", "_spell_weapon_copies + 1")],
                        ),
                    ],
                }
            },
            _require(
                "_spell_weapon_copies == 1",
                ("Duplicate weapon copies need distinct item identities before enchantment."),
            ),
        ],
    )
    changed["validate_srd_inventory_spell_weapon"] = validate
    read = _tool(
        ("Read source-owned bonuses and Shillelagh choices for this exact inventory weapon."),
        [_entity("actor_state"), _entity("lasting_state"), _parameter("weapon")],
        [
            _set("_weapon_spell_bonus", 0),
            _set("_weapon_spell_ability", None),
            _set("_weapon_spell_modifier", 0),
            _set("_weapon_spell_die", None),
            _set("_weapon_spell_magic", False),
            _set("_weapon_spell_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_weapon_spell_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_weapon_spell_records",
                    "as": "_weapon_spell_record",
                    "do": [
                        _when(
                            (
                                "_weapon_spell_record.kind == 'actor_spell_modifier' "
                                "and "
                                "_weapon_spell_record.target == _actor_state_id and "
                                "_weapon_spell_record.weapon == weapon and "
                                "lasting_state.lasting_tick < "
                                "_weapon_spell_record.expires_tick"
                            ),
                            [
                                _when(
                                    "_weapon_spell_record.mode == 'magic_weapon'",
                                    [
                                        _set(
                                            "_weapon_spell_bonus",
                                            (
                                                "max(_weapon_spell_bonus, "
                                                "_weapon_spell_record.weapon_bonus)"
                                            ),
                                        ),
                                        _set("_weapon_spell_magic", True),
                                    ],
                                ),
                                _when(
                                    (
                                        "_weapon_spell_record.mode == 'shillelagh' "
                                        "and "
                                        "actor_state.held_items != null and weapon in "
                                        ""
                                        ""
                                        "actor_state.held_items and "
                                        "actor_state.inventory != null and "
                                        "weapon in actor_state.inventory"
                                    ),
                                    [
                                        _set(
                                            "_weapon_spell_ability",
                                            "_weapon_spell_record.casting_ability",
                                        ),
                                        _set(
                                            "_weapon_spell_modifier",
                                            "_weapon_spell_record.casting_modifier",
                                        ),
                                        _set("_weapon_spell_die", "'1d8'"),
                                        _when(
                                            "_weapon_spell_record.casting_level >= 5",
                                            [_set("_weapon_spell_die", "'1d10'")],
                                        ),
                                        _when(
                                            "_weapon_spell_record.casting_level >= 11",
                                            [_set("_weapon_spell_die", "'1d12'")],
                                        ),
                                        _when(
                                            "_weapon_spell_record.casting_level >= 17",
                                            [_set("_weapon_spell_die", "'2d6'")],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                }
            },
        ],
    )
    changed["read_srd_actor_weapon_spell"] = read
    retire = _tool(
        "End Shillelagh permanently when its caster lets go of the selected weapon.",
        [_entity("actor_state"), _entity("lasting_state")],
        [
            _set("_released_weapon_spell_records", []),
            _set("_retained_weapon_spell_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_released_weapon_spell_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_released_weapon_spell_records",
                    "as": "_released_weapon_spell_record",
                    "do": [
                        _when(
                            (
                                "_released_weapon_spell_record.kind != "
                                "'actor_spell_modifier' or "
                                "_released_weapon_spell_record.mode != 'shillelagh' "
                                "or "
                                "_released_weapon_spell_record.target != "
                                "_actor_state_id or "
                                "(actor_state.held_items != null and "
                                "_released_weapon_spell_record.weapon in "
                                "actor_state.held_items "
                                "and actor_state.inventory != null and "
                                "_released_weapon_spell_record.weapon in "
                                "actor_state.inventory)"
                            ),
                            [
                                {
                                    "list_push": {
                                        "list": "_retained_weapon_spell_records",
                                        "value": "_released_weapon_spell_record",
                                    }
                                },
                            ],
                        ),
                    ],
                }
            },
            _set("lasting_state.turn_effects", "_retained_weapon_spell_records"),
        ],
    )
    changed["retire_srd_released_shillelagh"] = retire
    cast = changed["cast_spell"]
    if not any(p["name"] == "actor_spell_weapon" for p in cast["parameters"]):
        cast["parameters"].append(
            {"name": "actor_spell_weapon", "type": "string", "required": False, "default": None}
        )
    for name in (
        "cast_spell_timed_actor_modifier",
        "resolve_srd_actor_modifier_target_cast",
        "install_srd_actor_spell_modifier",
    ):
        changed[name]["parameters"].append(
            {"name": "weapon", "type": "string", "required": False, "default": None}
        )
    for name in ("resolve_srd_actor_modifier_target_cast", "install_srd_actor_spell_modifier"):
        changed[name]["parameters"].append(
            {"name": "weapon_bonus", "type": "integer", "required": False, "default": 0}
        )
    modifier_cast = changed["cast_spell_timed_actor_modifier"]
    modifier_cast["effects"].insert(0, _set("_magic_weapon_bonus", 1))
    modifier_cast["effects"].insert(1, _when("slot_level >= 3", [_set("_magic_weapon_bonus", 2)]))
    modifier_cast["effects"].insert(2, _when("slot_level >= 6", [_set("_magic_weapon_bonus", 3)]))
    for name in (
        "cast_spell",
        "cast_spell_timed_actor_modifier",
        "resolve_srd_actor_modifier_target_cast",
    ):
        for effect in _walk_effects(changed[name]["effects"]):
            if effect.get("call") in (
                "cast_spell_timed_actor_modifier",
                "resolve_srd_actor_modifier_target_cast",
                "install_srd_actor_spell_modifier",
            ):
                effect["params"]["weapon"] = (
                    "{actor_spell_weapon}" if name == "cast_spell" else "{weapon}"
                )
                if name != "cast_spell":
                    effect["params"]["weapon_bonus"] = (
                        "{_magic_weapon_bonus}"
                        if name == "cast_spell_timed_actor_modifier"
                        else "{weapon_bonus}"
                    )
    target = changed["resolve_srd_actor_modifier_target_cast"]
    target["effects"].insert(
        1,
        _when(
            (
                "_modifier_target_profile.profile == 'magic_weapon' or "
                "_modifier_target_profile.profile == 'shillelagh'"
            ),
            [
                _call(
                    "validate_srd_inventory_spell_weapon",
                    {"actor_state": "{_target_state_id}", "weapon": "{weapon}"},
                ),
                _when(
                    "_modifier_target_profile.profile == 'shillelagh'",
                    [
                        _require(
                            (
                                "_target_state_id == _caster_state_id and (weapon == "
                                "'club' or "
                                "weapon == 'quarterstaff') and "
                                "caster_state.held_items != null and "
                                "weapon in caster_state.held_items"
                            ),
                            (
                                "Shillelagh requires the Club or Quarterstaff you are "
                                ""
                                "actually holding."
                            ),
                        ),
                    ],
                ),
                _when(
                    "_modifier_target_profile.profile == 'magic_weapon'",
                    [
                        _require(
                            (
                                "target_state.intrinsic_magic_weapon_ids == null or "
                                "weapon not in "
                                "target_state.intrinsic_magic_weapon_ids"
                            ),
                            "Magic Weapon requires a nonmagical weapon.",
                        ),
                        _set("_magic_weapon_existing_records", []),
                        _when(
                            "lasting_state.turn_effects != null",
                            [_set("_magic_weapon_existing_records", "lasting_state.turn_effects")],
                        ),
                        {
                            "for_each": {
                                "collection": "_magic_weapon_existing_records",
                                "as": "_magic_weapon_existing",
                                "do": [
                                    _require(
                                        (
                                            "_magic_weapon_existing.kind != "
                                            "'actor_spell_modifier' or "
                                            "_magic_weapon_existing.mode != "
                                            "'magic_weapon' or "
                                            "_magic_weapon_existing.target != "
                                            "_target_state_id or "
                                            "_magic_weapon_existing.weapon != weapon "
                                            "or "
                                            "_magic_weapon_existing.caster == "
                                            "_caster_state_id or "
                                            "lasting_state.lasting_tick >= "
                                            "_magic_weapon_existing.expires_tick"
                                        ),
                                        (
                                            "Another active Magic Weapon source "
                                            "already "
                                            "makes that weapon magical."
                                        ),
                                    ),
                                ],
                            }
                        },
                    ],
                ),
            ],
        ),
    )
    install = changed["install_srd_actor_spell_modifier"]
    for effect in _walk_effects(install["effects"]):
        record = effect.get("list_push", {}).get("value")
        if isinstance(record, dict) and "started_tick" in record:
            record.update(weapon="weapon", weapon_bonus="weapon_bonus")
    install["effects"].append(
        _when(
            "mode == 'magic_weapon' or mode == 'shillelagh'",
            [_set("target_state.has_actor_weapon_modifiers", True)],
        )
    )
    for name in ("resolve_combat_attack", "resolve_combat_attack_followup"):
        tool = deepcopy(changed.get(name, tools[name]))
        if "read_srd_actor_weapon_spell" in str(tool["effects"]):
            changed[name] = tool
            continue
        tool["parameters"].extend(
            [
                {
                    "name": "use_shillelagh_ability",
                    "type": "boolean",
                    "required": False,
                    "default": True,
                },
                {
                    "name": "shillelagh_damage_type",
                    "type": "enum",
                    "enum_values": ["weapon", "force"],
                    "required": False,
                    "default": "weapon",
                },
            ]
        )
        tool["effects"][0:0] = [
            _set("_weapon_spell_bonus", 0),
            _set("_weapon_spell_ability", None),
            _set("_weapon_spell_modifier", 0),
            _set("_weapon_spell_die", None),
            _set("_weapon_spell_magic", False),
            _set("_actual_weapon_attack_ability", "attack_ability"),
            _when(
                "actor_state.has_actor_weapon_modifiers == true",
                [
                    _call(
                        "read_srd_actor_weapon_spell",
                        {
                            "actor_state": "{_actor_state_id}",
                            "lasting_state": "state",
                            "weapon": "{weapon}",
                        },
                        WEAPON_SPELL_EXPORTS,
                    )
                ],
            ),
        ]
        for effects in list(_effect_lists(tool["effects"])):
            for index, effect in enumerate(list(effects)):
                if effect.get("set", {}).get("field") == "_ability_modifier":
                    effects.insert(
                        index + 1,
                        _when(
                            (
                                "_weapon_spell_ability != null and "
                                "use_shillelagh_ability == true "
                                "and attack_mode == 'melee'"
                            ),
                            [
                                _set("_ability_modifier", "_weapon_spell_modifier"),
                                _set("_actual_weapon_attack_ability", "_weapon_spell_ability"),
                            ],
                        ),
                    )
        index = next(
            i
            for i, e in enumerate(tool["effects"])
            if e.get("call") == "resolve_srd_attack_conditions"
        )
        tool["effects"][index:index] = [
            _set("_attack_modifier", "_attack_modifier + _weapon_spell_bonus"),
            _set("_damage_modifier", "_damage_modifier + _weapon_spell_bonus"),
            _when(
                "_weapon_spell_die != null and use_martial_arts_die != true",
                [_set("_damage_expression", "_weapon_spell_die")],
            ),
            _when(
                "_weapon_spell_die != null and shillelagh_damage_type == 'force'",
                [_set("_weapon_damage_type", "'force'")],
            ),
            _set("actor_state.last_attack_weapon_magical", "_weapon_spell_magic"),
        ]
        for effect in _walk_effects(tool["effects"]):
            clause = effect.get("condition", "")
            if "has_actor_spell_modifiers" in clause and "attack_ability == 'strength'" in clause:
                effect["condition"] = clause.replace(
                    "attack_ability == 'strength'", "_actual_weapon_attack_ability == 'strength'"
                )
        changed[name] = tool
    for name, actor in (
        ("resolve_opportunity_attack", "actor_state"),
        ("resolve_adjudicated_creature_attack", "actor"),
    ):
        tool = deepcopy(changed.get(name, tools[name]))
        if "Spell-enhanced inventory" not in str(tool["effects"]):
            tool["effects"].insert(
                0,
                _require(
                    actor + ".has_actor_weapon_modifiers != true",
                    (
                        "Spell-enhanced inventory attacks require the authoritative "
                        "weapon "
                        "attack owner; supplied NPC/opportunity numbers cannot "
                        "identify "
                        "that weapon."
                    ),
                ),
            )
        changed[name] = tool


def _compose_temporal_spell_consumers(tools, changed):
    """Keep temporal spell actions, penalties and aftermath on their existing owners."""
    resolve = changed["resolve_srd_actor_spell_modifiers"]
    resolve["effects"].insert(0, _set("_advanced_haste_sources", []))
    active = next(
        e
        for e in _walk_effects(resolve["effects"])
        if e.get("condition") == "_actor_modifier_active == true"
    )
    active["on_true"].append(
        _when(
            "_actor_modifier_record.mode == 'haste'",
            [
                {
                    "list_push": {
                        "list": "_advanced_haste_sources",
                        "value": {
                            "caster": "_actor_modifier_record.caster",
                            "started_tick": "_actor_modifier_record.started_tick",
                            "expires_tick": "_actor_modifier_record.expires_tick",
                        },
                    }
                },
            ],
        )
    )
    suppression = changed["resolve_srd_timed_actor_condition_suppression"]
    suppression["effects"].insert(0, _set("_advanced_haste_sources", []))
    for e in _walk_effects(suppression["effects"]):
        if e.get("call") == "resolve_srd_actor_spell_modifiers":
            e["export_context"]["_advanced_haste_sources"] = "_advanced_haste_sources"
    suppression["effects"].extend(
        [
            _when(
                "target_state.haste_active_sources != null",
                [
                    _set("_old_haste_sources", "target_state.haste_active_sources"),
                    {
                        "for_each": {
                            "collection": "_old_haste_sources",
                            "as": "_old_haste_source",
                            "do": [
                                _when(
                                    "_old_haste_source not in _advanced_haste_sources",
                                    [
                                        _set("_haste_end_turn", 1),
                                        _when(
                                            "target_state.turns_started != null",
                                            [
                                                _set(
                                                    "_haste_end_turn",
                                                    "target_state.turns_started + 1",
                                                ),
                                            ],
                                        ),
                                        _set("_haste_end_tick", "lasting_state.lasting_tick"),
                                        _when(
                                            ("_old_haste_source.expires_tick < _haste_end_tick"),
                                            [
                                                _set(
                                                    "_haste_end_tick",
                                                    "_old_haste_source.expires_tick",
                                                ),
                                            ],
                                        ),
                                        _set("target_state.haste_lethargy", True),
                                        _set(
                                            "target_state.haste_lethargy_until_turn",
                                            "_haste_end_turn",
                                        ),
                                        _set(
                                            "target_state.haste_lethargy_until_tick",
                                            "_haste_end_tick + 2",
                                        ),
                                    ],
                                ),
                            ],
                        }
                    },
                ],
            ),
            _set("target_state.haste_active_sources", "_advanced_haste_sources"),
            _when(
                ("combat.turn_active == true and combat.active_actor == _target_state_id"),
                [
                    _when(
                        ("_advanced_haste == true and combat.haste_action_granted != true"),
                        [
                            _set("combat.haste_action_granted", True),
                            _set("combat.haste_action_available", True),
                        ],
                    ),
                    _when(
                        "_advanced_haste != true",
                        [
                            _set("combat.haste_action_available", False),
                            _when(
                                "combat.haste_action_selected == true",
                                [
                                    _set("combat.haste_action_selected", False),
                                    _set(
                                        "combat.action_available",
                                        (
                                            "combat.normal_action_spent != true or "
                                            "combat.action_surge_available == true"
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ]
    )
    lethargy = _tool(
        "Project source-owned Haste lethargy after ordinary condition ownership.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _when(
                "target_state.haste_lethargy == true",
                [
                    _when(
                        "combat.encounter_active != true and "
                        "lasting_state.lasting_tick >= "
                        "target_state.haste_lethargy_until_tick",
                        [
                            _set("target_state.haste_lethargy", False),
                        ],
                        [
                            _set("_haste_conditions", "target_state.conditions"),
                            _when(
                                "'incapacitated' not in _haste_conditions",
                                [
                                    {
                                        "list_push": {
                                            "list": "_haste_conditions",
                                            "value": "'incapacitated'",
                                        }
                                    },
                                ],
                            ),
                            _set("target_state.conditions", "_haste_conditions"),
                        ],
                    ),
                    _call("refresh_srd_movement_budget", {"actor_state": "{_target_state_id}"}),
                ],
            ),
        ],
    )
    changed["project_srd_haste_lethargy"] = lethargy

    choose = _tool(
        "Select the source-owned Haste action for the next actual permitted action.",
        [
            _entity("actor_state"),
            {"name": "use_haste", "type": "boolean", "required": True},
        ],
        [
            _require(
                ("combat.turn_active == true and combat.active_actor == _actor_state_id"),
                "Choose an action source only on this creature's active turn.",
            ),
            _require(
                "actor_state.is_alive == true and actor_state.hp > 0 and "
                "not ('incapacitated' in actor_state.conditions or 'paralyzed' in "
                "actor_state.conditions or 'petrified' in actor_state.conditions or "
                "'stunned' in actor_state.conditions or 'unconscious' in "
                "actor_state.conditions)",
                "The creature cannot choose an action source while incapacitated.",
            ),
            _require(
                "combat.attacks_remaining == null or combat.attacks_remaining == 0",
                ("Finish the current Attack action before selecting another action source."),
            ),
            _when(
                "use_haste == true",
                [
                    _require(
                        (
                            "actor_state.spell_haste == true and "
                            "combat.haste_action_available == true"
                        ),
                        "No Haste action remains on this turn.",
                    ),
                    _set("combat.haste_action_selected", True),
                    _set("combat.action_available", True),
                ],
                [
                    _set("combat.haste_action_selected", False),
                    _set(
                        "combat.action_available",
                        (
                            "combat.normal_action_spent != true or "
                            "combat.action_surge_available == true"
                        ),
                    ),
                ],
            ),
            _call("refresh_srd_attack_availability", {}),
        ],
    )
    choose["metadata"] = {
        "category": "combat",
        "costs_action": False,
        "model_behavior": "leaves_player_intent_pending",
    }
    changed["choose_srd_haste_action"] = choose

    spend = deepcopy(changed.get("spend_srd_action", tools["spend_srd_action"]))
    if "combat.haste_action_selected" not in str(spend["effects"]):
        original = [
            e for e in spend["effects"] if e.get("call") != "refresh_srd_attack_availability"
        ]
        spend["effects"] = [
            _when(
                "combat.active_actor_spell_slow == true",
                [
                    _require(
                        (
                            "combat.action_used_this_turn != true and "
                            "combat.bonus_action_used_this_turn != true"
                        ),
                        "Slow permits one Action or Bonus Action on the turn.",
                    ),
                ],
            ),
            _set("combat.last_action_source", "'normal'"),
            _when(
                "combat.haste_action_selected == true",
                [
                    _require(
                        "magic_action != true and (control_action_kind == 'attack' or "
                        ""
                        "control_action_kind == 'dash' or control_action_kind == "
                        "'disengage' or "
                        "control_action_kind == 'hide' or control_action_kind == "
                        "'utilize')",
                        ("Haste permits only one Attack, Dash, Disengage, Hide or Utilize."),
                    ),
                    _require("combat.haste_action_available == true", "The Haste action is spent."),
                    _call(
                        "check_srd_active_control_action",
                        {
                            "action_kind": "{control_action_kind}",
                            "actor_state": "{combat.active_actor}",
                        },
                    ),
                    _set("combat.haste_action_available", False),
                    _set("combat.haste_action_selected", False),
                    _set("combat.last_action_source", "'haste'"),
                    _set(
                        "combat.action_available",
                        (
                            "combat.normal_action_spent != true or "
                            "combat.action_surge_available == true"
                        ),
                    ),
                ],
                original,
            ),
            _set("combat.action_used_this_turn", True),
            _call("refresh_srd_attack_availability", {}),
        ]
        for pre in spend["pre_conditions"]:
            if "normal_action_spent" in pre["condition"]:
                pre["condition"] = (
                    "combat.haste_action_selected == true or (" + pre["condition"] + ")"
                )
    changed["spend_srd_action"] = spend
    for e in _walk_effects(spend["effects"]):
        if (
            e.get("require", {}).get("error")
            == "Haste permits only one Attack, Dash, Disengage, Hide or Utilize."
        ):
            e["require"]["condition"] = (
                "magic_action != true and (control_action_kind == 'attack' or "
                "control_action_kind == 'dash' or control_action_kind == 'disengage' "
                "or "
                "control_action_kind == 'hide' or control_action_kind == 'utilize')"
            )
    bonus = deepcopy(changed.get("consume_srd_bonus_action", tools["consume_srd_bonus_action"]))
    if "combat.bonus_action_used_this_turn" not in str(bonus["effects"]):
        bonus["effects"].insert(
            0,
            _require(
                ("actor_state.spell_slow != true or combat.action_used_this_turn != true"),
                "Slow permits an Action or Bonus Action, not both.",
            ),
        )
        bonus["effects"].append(_set("combat.bonus_action_used_this_turn", True))
    changed["consume_srd_bonus_action"] = bonus
    reaction = deepcopy(changed.get("consume_srd_reaction", tools["consume_srd_reaction"]))
    if "actor_state.spell_slow" not in str(reaction):
        reaction["pre_conditions"].append(
            {
                "condition": "actor_state.spell_slow != true",
                "error": "Slow prevents Reactions.",
            }
        )
    changed["consume_srd_reaction"] = reaction

    begin = deepcopy(changed.get("begin_combat_turn", tools["begin_combat_turn"]))
    if "combat.haste_action_granted" not in str(begin["effects"]):
        begin["effects"].insert(0, _set("combat.haste_action_granted", False))
        begin["effects"].extend(
            [
                _set("combat.haste_action_selected", False),
                _set("combat.haste_action_available", "actor.spell_haste == true"),
                _set("combat.haste_action_granted", "actor.spell_haste == true"),
                _set("combat.active_actor_spell_slow", "actor.spell_slow == true"),
                _set("combat.action_used_this_turn", False),
                _set("combat.bonus_action_used_this_turn", False),
                _set("actor.light_unrestricted_source_weapons", []),
            ]
        )
    changed["begin_combat_turn"] = begin
    suppression["effects"].append(
        _when(
            "combat.turn_active == true and combat.active_actor == _target_state_id",
            [
                _set("combat.active_actor_spell_slow", "_advanced_slow"),
            ],
        )
    )

    attack = deepcopy(changed.get("spend_srd_attack", tools["spend_srd_attack"]))
    if "combat.last_action_source" not in str(attack["effects"]):
        for effects in _effect_lists(attack["effects"]):
            for index, e in enumerate(list(effects)):
                if (
                    e.get("set", {}).get("field") == "combat.attacks_remaining"
                    and e["set"]["value"] == "_attacks_per_action - 1"
                ):
                    effects.insert(
                        index,
                        _when(
                            (
                                "combat.last_action_source == 'haste' or "
                                "combat.active_actor_spell_slow == true"
                            ),
                            [_set("_attacks_per_action", 1)],
                        ),
                    )
    changed["spend_srd_attack"] = attack
    npc = deepcopy(changed.get("spend_srd_creature_attack", tools["spend_srd_creature_attack"]))
    if "actor.spell_slow" not in str(npc):
        npc["pre_conditions"].append(
            {
                "condition": "(actor.spell_slow != true and "
                "combat.haste_action_selected != true) or use_multiattack != true",
                "error": ("Slow and the Haste action permit one ordinary attack, not Multiattack."),
            }
        )
    changed["spend_srd_creature_attack"] = npc
    for name in ("resolve_combat_attack", "resolve_combat_attack_followup"):
        owner = deepcopy(changed.get(name, tools[name]))
        if "light_unrestricted_source_weapons" not in str(owner["effects"]):
            owner["pre_conditions"].append(
                {
                    "condition": (
                        "attack_sequence != 'light_nick' or (actor_state.spell_slow != "
                        "true and actor_state.light_unrestricted_source_weapons != null "
                        "and len(actor_state.light_unrestricted_source_weapons) > 0)"
                    ),
                    "error": (
                        "Nick needs an ordinary unrestricted Attack action; Haste and "
                        "Slow permit one attack only."
                    ),
                }
            )
            index = next(
                i
                for i, e in enumerate(owner["effects"])
                if e.get("call") == "resolve_srd_attack_conditions"
            )
            owner["effects"].insert(
                index,
                _when(
                    (
                        "attack_sequence == 'primary' and 'light' in "
                        "weapon_info.properties and combat.last_action_source != 'haste' "
                        "and actor_state.spell_slow != true"
                    ),
                    [
                        _set("_ordinary_light_sources", []),
                        _when(
                            "actor_state.light_unrestricted_source_weapons != null",
                            [
                                _set(
                                    "_ordinary_light_sources",
                                    "actor_state.light_unrestricted_source_weapons",
                                ),
                            ],
                        ),
                        _when(
                            "weapon not in _ordinary_light_sources",
                            [
                                {
                                    "list_push": {
                                        "list": "_ordinary_light_sources",
                                        "value": "weapon",
                                    }
                                },
                            ],
                        ),
                        _set(
                            "actor_state.light_unrestricted_source_weapons",
                            "_ordinary_light_sources",
                        ),
                    ],
                ),
            )
        changed[name] = owner
    ac = deepcopy(
        changed.get("resolve_srd_effective_armor_class", tools["resolve_srd_effective_armor_class"])
    )
    if "target_state.spell_haste" not in str(ac["effects"]):
        ac["effects"].extend(
            [
                _when(
                    "target_state.spell_haste == true",
                    [_set("_effective_armor_class", "_effective_armor_class + 2")],
                ),
                _when(
                    "target_state.spell_slow == true",
                    [_set("_effective_armor_class", "_effective_armor_class - 2")],
                ),
            ]
        )
    changed["resolve_srd_effective_armor_class"] = ac
    d20 = changed["d20_test"]
    for effects in list(_effect_lists(d20["effects"])):
        effects[:] = [
            e
            for e in effects
            if not (
                "actor_state.spell_slow" in e.get("condition", "")
                and "_slow_save_penalty" in str(e)
            )
        ]
    index = next(
        i for i, e in enumerate(d20["effects"]) if e.get("set", {}).get("field") == "_modifier"
    )
    d20["effects"].insert(
        index + 1,
        _when(
            (
                "actor_state.spell_slow == true and ability == 'dexterity' and kind == "
                "'saving_throw'"
            ),
            [
                _set("_modifier", "_modifier - 2"),
                _set("_slow_save_penalty", 2),
            ],
        ),
    )

    end = deepcopy(changed.get("end_combat_turn", tools["end_combat_turn"]))
    if "actor.haste_lethargy_until_turn" not in str(end["effects"]):
        end["effects"].append(
            _when(
                (
                    "actor.haste_lethargy == true and actor.turns_started >= "
                    "actor.haste_lethargy_until_turn"
                ),
                [
                    _set("actor.haste_lethargy", False),
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_actor_id}", "lasting_state": "state"},
                    ),
                ],
            )
        )
    changed["end_combat_turn"] = end
    for name in (
        "dash_combat_action",
        "disengage_combat_action",
        "take_disengage_action",
        "hide_srd_action",
    ):
        if name not in tools:
            continue
        owner = deepcopy(changed.get(name, tools[name]))
        for e in _walk_effects(owner["effects"]):
            if e.get("call") == "spend_srd_action":
                e["params"]["control_action_kind"] = {
                    "dash_combat_action": "dash",
                    "disengage_combat_action": "disengage",
                    "take_disengage_action": "disengage",
                    "hide_srd_action": "hide",
                }[name]
        changed[name] = owner


def _compose_slow_cast_failure(tools, changed):
    """Retain the actual failed gesture roll while normal casting owns all fixed costs."""
    leaf = _tool(
        "Resolve and retain Slow failure from the actual Somatic casting requirement.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("has_somatic_component", "boolean"),
            {"name": "pending_action_id", "type": "string", "required": False, "default": None},
            {
                "name": "lasting_state",
                "type": "string",
                "resolve": "entity",
                "required": False,
                "default": "state",
                "model_hidden": True,
            },
        ],
        [
            _set("_slow_cast_failed", False),
            _set("_slow_cast_failure_roll", None),
            _set("_slow_cast_cached", False),
            _set("_slow_cast_event", None),
            _when(
                "pending_action_id != null",
                [
                    _require(
                        (
                            "lasting_state.pending_srd_actions != null and "
                            "pending_action_id in lasting_state.pending_srd_actions"
                        ),
                        "This Slow cast attempt needs its owning saved event.",
                    ),
                    _set(
                        "_slow_cast_event", "lasting_state.pending_srd_actions[pending_action_id]"
                    ),
                    _require(
                        (
                            "_slow_cast_event.kind == 'spell_cast' and "
                            "_slow_cast_event.actor_state_id == _caster_state_id and "
                            "_slow_cast_event.parameters.spell == spell"
                        ),
                        "The saved cast belongs to this exact creature and spell.",
                    ),
                    _when(
                        "_slow_cast_event.slow_cast != null",
                        [
                            _set("_slow_cast_cached", True),
                            _set("_slow_cast_failed", "_slow_cast_event.slow_cast.failed"),
                            _set("_slow_cast_failure_roll", "_slow_cast_event.slow_cast.roll"),
                        ],
                    ),
                ],
            ),
            _when(
                (
                    "_slow_cast_cached != true and caster_state.spell_slow == true and "
                    "has_somatic_component == true"
                ),
                [
                    {"dice": {"expression": "1d4"}, "store_as": "_slow_cast_failure_roll"},
                    _set("_slow_cast_failed", "_slow_cast_failure_roll.total == 1"),
                    _when(
                        "pending_action_id != null",
                        [
                            _set(
                                ("lasting_state.pending_srd_actions.{pending_action_id}.slow_cast"),
                                {
                                    "roll": "_slow_cast_failure_roll",
                                    "failed": "_slow_cast_failed",
                                    "spell": "spell",
                                    "caster_state_id": "_caster_state_id",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    changed["resolve_srd_slow_cast_failure"] = leaf
    cast = changed["cast_spell"]
    if "_slow_cast_failure_roll" not in str(cast["effects"]):
        if not any(
            e.get("set", {}).get("field") == "_spell_cast_failed"
            for e in _walk_effects(cast["effects"])
        ):
            cast["effects"].insert(0, _set("_spell_cast_failed", False))
        first_dispatch = next(
            i
            for i, e in enumerate(cast["effects"])
            if "_spell_definition.family ==" in e.get("condition", "")
        )
        cast["effects"][first_dispatch:first_dispatch] = [
            _set("_slow_cast_failure_roll", None),
            _when(
                (
                    "caster_state.spell_slow == true or "
                    "caster_state.pending_srd_completion_id != null"
                ),
                [
                    _set(
                        "_slow_actual_somatic",
                        (
                            "'somatic' in _casting_requirements.components and 'somatic' "
                            "not in _component_exemptions"
                        ),
                    ),
                    _call(
                        "resolve_srd_slow_cast_failure",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "has_somatic_component": "{_slow_actual_somatic}",
                            "pending_action_id": "{caster_state.pending_srd_completion_id}",
                        },
                        ["_slow_cast_failed", "_slow_cast_failure_roll"],
                    ),
                    _when(
                        "_slow_cast_failed == true",
                        [
                            _set("_spell_cast_failed", True),
                            {
                                "list_push": {
                                    "list": "_spell_detail_rows",
                                    "value": {
                                        "label": "'Casting failed'",
                                        "value": (
                                            "'Slow: Somatic gestures failed; casting costs spent.'"
                                        ),
                                    },
                                }
                            },
                        ],
                    ),
                ],
            ),
        ]
        cast["effects"].append(
            _when(
                "_slow_cast_failure_roll != null",
                [
                    {
                        "list_push": {
                            "list": "_spell_card_summary",
                            "value": {
                                "label": "'Slow gesture roll'",
                                "value": "_slow_cast_failure_roll.results",
                            },
                        }
                    },
                ],
            )
        )
    # The ordinary pre-dispatch casting budget already spends the failed attempt's action.
    gesture = next(e for e in cast["effects"] if "resolve_srd_slow_cast_failure" in str(e))
    if "_extended_slow_cast" not in str(gesture):
        current = deepcopy(gesture["on_true"])
        failed_receipt = deepcopy(next(e for e in current
                                      if e.get("condition") == "_slow_cast_failed == true"))
        gesture["condition"] += " or _extended_slow_cast != null"
        gesture["on_true"] = [_when(
            "_extended_slow_cast != null",
            [_set("_slow_cast_failed", "_extended_slow_cast.failed"),
             _set("_slow_cast_failure_roll", "_extended_slow_cast.roll"), failed_receipt],
            current,
        )]
    for e in _walk_effects(cast["effects"]):
        if e.get("condition") == "_slow_cast_failed == true":
            e["on_true"] = [
                owned
                for owned in e["on_true"]
                if owned.get("condition") != "combat.turn_active == true"
            ]
    for e in cast["effects"]:
        clause = e.get("condition", "")
        if (
            "_spell_definition.family ==" in clause
            or "_spell_definition.turn_effect" in clause
            or "_spell_definition.prevents_opportunity_attacks" in clause
        ) and "_spell_cast_failed" not in clause:
            e["condition"] = "_spell_cast_failed != true and (" + clause + ")"


def _compose_size_spell_consumers(tools, changed):
    "Resize admitted creatures and carried items through actual size and attack consumers."
    for name in (
        "cast_spell_timed_actor_modifier",
        "resolve_srd_actor_modifier_target_cast",
        "install_srd_actor_spell_modifier",
    ):
        changed[name]["parameters"].append(
            {"name": "actual_spell_level", "type": "integer", "required": False, "default": -1}
        )
    for name in (
        "cast_spell",
        "cast_spell_timed_actor_modifier",
        "resolve_srd_actor_modifier_target_cast",
    ):
        for e in _walk_effects(changed[name]["effects"]):
            if e.get("call") in (
                "cast_spell_timed_actor_modifier",
                "resolve_srd_actor_modifier_target_cast",
                "install_srd_actor_spell_modifier",
            ):
                e["params"]["actual_spell_level"] = (
                    "{_slot_level}" if name == "cast_spell" else "{actual_spell_level}"
                )
    for e in _walk_effects(changed["install_srd_actor_spell_modifier"]["effects"]):
        value = e.get("list_push", {}).get("value")
        if isinstance(value, dict) and value.get("kind") == "'actor_spell_modifier'":
            value["spell_level"] = "actual_spell_level"
    prepare = _tool(
        "Retain base size from this creature's own validated Character File.",
        [
            {"name": "actor", "type": "string", "resolve": "participant", "required": True},
            _entity("actor_state"),
        ],
        [
            _require(
                (
                    "actor.participant_id == _actor_state_id and actor.validation_status "
                    "== 'validated' and actor.package_id == 'sylvia.srd-lite.bg3' and "
                    "actor.kind == 'player_character'"
                ),
                "Base size needs this creature's own validated Character File.",
            ),
            _require(
                (
                    "actor.data.size_rank != null and actor.data.size_rank >= 0 and "
                    "actor.data.size_rank <= 5"
                ),
                "The Character File needs an admitted size category.",
            ),
            _set("actor_state.actor_base_size_rank", "actor.data.size_rank"),
            _when(
                "actor_state.actor_size_projected != true",
                [
                    _set("actor_state.size_rank", "actor.data.size_rank"),
                ],
            ),
        ],
    )
    prepare["metadata"] = {"category": "creature-fact", "costs_action": False}
    changed["record_srd_actor_base_size"] = prepare
    validate = changed["validate_srd_actor_modifier_target"]
    validate["effects"].append(
        _when(
            "profile == 'enlarge_reduce'",
            [
                _require(
                    (
                        "target_state.size_rank != null and target_state.size_rank >= 0 "
                        "and target_state.size_rank <= 5"
                    ),
                    (
                        "Enlarge/Reduce needs this creature's saved size category; "
                        "prepare a PC from its Character File."
                    ),
                ),
            ],
        )
    )
    cast = changed["cast_spell"]
    dispatch = next(
        e
        for e in cast["effects"]
        if e.get("condition") == "_spell_definition.family == 'timed_actor_modifier'"
    )
    index = next(
        i
        for i, e in enumerate(dispatch["on_true"])
        if e.get("call") == "cast_spell_timed_actor_modifier"
    )
    dispatch["on_true"].insert(
        index,
        _when(
            "_spell_definition.profile == 'enlarge_reduce'",
            [
                _require(
                    (
                        "(targets == null and _caster_can_see_target == true) or (targets "
                        "!= null and caster_can_see_all_targets == true)"
                    ),
                    "Enlarge/Reduce needs sight of its selected creature.",
                ),
                _when(
                    "_modifier_dispatch_willing == true",
                    [
                        _set("_modifier_forego_targets", "_modifier_dispatch_targets"),
                    ],
                ),
            ],
        ),
    )
    target = changed["resolve_srd_actor_modifier_target_cast"]
    mode_index = next(
        i
        for i, e in enumerate(target["effects"])
        if e.get("set", {}).get("field") == "_modifier_install_mode"
    )
    target["effects"].insert(
        mode_index + 1,
        _when(
            "_modifier_target_profile.profile == 'enlarge_reduce'",
            [
                _require(
                    "effect_choice == 'enlarge' or effect_choice == 'reduce'",
                    "Choose Enlarge or Reduce.",
                ),
                _set("_modifier_install_mode", "effect_choice"),
            ],
        ),
    )
    install = changed["install_srd_actor_spell_modifier"]
    install["effects"].insert(
        0,
        _when(
            "mode == 'enlarge' or mode == 'reduce'",
            [
                _when(
                    "target_state.actor_size_projected != true",
                    [
                        _set("target_state.actor_base_size_rank", "target_state.size_rank"),
                    ],
                ),
                _require(
                    (
                        "(mode == 'enlarge' and target_state.actor_base_size_rank < 5) or "
                        "(mode == 'reduce' and target_state.actor_base_size_rank > 0)"
                    ),
                    (
                        "Enlarge/Reduce needs a next size category in the admitted Tiny "
                        "through Gargantuan scale."
                    ),
                ),
            ],
        ),
    )
    for e in _walk_effects(install["effects"]):
        value = e.get("list_push", {}).get("value")
        if isinstance(value, dict) and value.get("kind") == "'actor_spell_modifier'":
            value["scaled_items"] = "_size_initial_items"
    install["effects"].insert(0, _set("_size_initial_items", []))
    install["effects"].insert(
        1,
        _when(
            ("(mode == 'enlarge' or mode == 'reduce') and target_state.inventory != null"),
            [
                _set("_size_initial_items", "target_state.inventory"),
            ],
        ),
    )
    resolve = changed["resolve_srd_actor_spell_modifiers"]
    resolve["effects"].insert(0, _set("_advanced_size_source", None))
    active = next(
        e
        for e in _walk_effects(resolve["effects"])
        if e.get("condition") == "_actor_modifier_active == true"
    )
    active["on_true"].append(
        _when(
            ("_actor_modifier_record.mode == 'enlarge' or _actor_modifier_record.mode == 'reduce'"),
            [
                _set("_advanced_size_source", "_actor_modifier_record"),
            ],
        )
    )
    suppression = changed["resolve_srd_timed_actor_condition_suppression"]
    suppression["effects"].insert(0, _set("_advanced_size_source", None))
    for e in _walk_effects(suppression["effects"]):
        if e.get("call") == "resolve_srd_actor_spell_modifiers":
            e["export_context"]["_advanced_size_source"] = "_advanced_size_source"
    suppression["effects"].append(
        _when(
            "_advanced_size_source != null",
            [
                _set("target_state.actor_size_projected", True),
                _set("target_state.size_category_adjustment", 1),
                _when(
                    "_advanced_size_source.mode == 'reduce'",
                    [_set("target_state.size_category_adjustment", -1)],
                ),
                _set(
                    "target_state.size_rank",
                    ("target_state.actor_base_size_rank + target_state.size_category_adjustment"),
                ),
                _set("target_state.enlarge_scaled_items", "_advanced_size_source.scaled_items"),
            ],
            [
                _when(
                    "target_state.actor_size_projected == true",
                    [
                        _set("target_state.size_rank", "target_state.actor_base_size_rank"),
                        _set("target_state.actor_size_projected", False),
                        _set("target_state.size_category_adjustment", 0),
                        _set("target_state.enlarge_scaled_items", []),
                    ],
                ),
            ],
        )
    )
    read = _tool(
        ("Read a carried item's size category adjustment from its actual resizing source."),
        [
            _entity("actor_state"),
            _parameter("item"),
        ],
        [
            _set("_carried_item_size_adjustment", 0),
            _when(
                (
                    "actor_state.actor_size_projected == true and "
                    "actor_state.enlarge_scaled_items != null and item in "
                    "actor_state.enlarge_scaled_items and actor_state.inventory != null "
                    "and item in actor_state.inventory"
                ),
                [
                    _set("_carried_item_size_adjustment", "actor_state.size_category_adjustment"),
                ],
            ),
        ],
    )
    read["metadata"] = {"category": "equipment", "costs_action": False}
    changed["read_srd_carried_item_size"] = read
    retire = _tool(
        ("Restore dropped or actually thrown items permanently within every resizing source."),
        [
            _entity("actor_state"),
            _entity("lasting_state"),
            {"name": "released_item", "type": "string", "required": False, "default": None},
        ],
        [
            _set("_size_retained_records", []),
            _set("_size_records", "lasting_state.turn_effects"),
            {
                "for_each": {
                    "collection": "_size_records",
                    "as": "_size_record",
                    "do": [
                        _when(
                            (
                                "_size_record.kind == 'actor_spell_modifier' and "
                                "_size_record.target == _actor_state_id and "
                                "(_size_record.mode == 'enlarge' or _size_record.mode == "
                                "'reduce')"
                            ),
                            [
                                _set("_size_kept_items", []),
                                _set("_size_old_items", "_size_record.scaled_items"),
                                {
                                    "for_each": {
                                        "collection": "_size_old_items",
                                        "as": "_size_item",
                                        "do": [
                                            _when(
                                                (
                                                    "_size_item != released_item and "
                                                    "actor_state.inventory != null and "
                                                    "_size_item in actor_state.inventory"
                                                ),
                                                [
                                                    {
                                                        "list_push": {
                                                            "list": "_size_kept_items",
                                                            "value": "_size_item",
                                                        }
                                                    },
                                                ],
                                            ),
                                        ],
                                    }
                                },
                                _set("_size_record", "merge(_size_record, _size_record_updates)"),
                            ],
                        ),
                        {"list_push": {"list": "_size_retained_records", "value": "_size_record"}},
                    ],
                }
            },
            _set("lasting_state.turn_effects", "_size_retained_records"),
        ],
    )
    for effects in _effect_lists(retire["effects"]):
        for i, e in enumerate(list(effects)):
            if e.get("set", {}).get("value") == "merge(_size_record, _size_record_updates)":
                effects.insert(
                    i, _set("_size_record_updates", {"scaled_items": "_size_kept_items"})
                )
    changed["retire_srd_released_sized_items"] = retire
    damage = _tool(
        ("Apply the source's real d4 to an enlarged/reduced weapon or Unarmed Strike hit."),
        [
            _entity("actor_state"),
            _parameter("amount", "number"),
            _parameter("critical_hit", "boolean"),
            {"name": "weapon", "type": "string", "required": False, "default": None},
            {
                "name": "great_weapon_fighting",
                "type": "boolean",
                "required": False,
                "default": False,
            },
        ],
        [
            _set("_size_attack_damage", "amount"),
            _set("_size_attack_roll", None),
            _set("_size_attack_qualifies", "weapon == null"),
            _when(
                "weapon != null",
                [
                    _set(
                        "_size_attack_qualifies",
                        (
                            "actor_state.enlarge_scaled_items != null and weapon in "
                            "actor_state.enlarge_scaled_items and actor_state.inventory "
                            "!= null and weapon in actor_state.inventory"
                        ),
                    ),
                ],
            ),
            _when(
                ("_size_attack_qualifies == true and actor_state.actor_size_projected == true"),
                [
                    {
                        "dice": {
                            "expression": "1d4",
                            "critical": (
                                "critical_hit == true and actor_state.spell_enlarge_mode "
                                "== 'enlarge'"
                            ),
                        },
                        "store_as": "_size_attack_roll",
                    },
                    _set("_size_attack_delta", "_size_attack_roll.total"),
                    _when(
                        "actor_state.spell_enlarge_mode == 'enlarge'",
                        [
                            _when(
                                "great_weapon_fighting == true",
                                [
                                    _set("_size_attack_faces", "_size_attack_roll.results"),
                                    _set("_size_attack_delta", 0),
                                    {
                                        "for_each": {
                                            "collection": "_size_attack_faces",
                                            "as": "_size_attack_face",
                                            "do": [
                                                _set(
                                                    "_size_attack_delta",
                                                    (
                                                        "_size_attack_delta + max(3, "
                                                        "_size_attack_face)"
                                                    ),
                                                ),
                                            ],
                                        }
                                    },
                                ],
                            ),
                            _set("_size_attack_damage", "amount + _size_attack_delta"),
                        ],
                        [
                            _set("_size_attack_damage", "max(1, amount - _size_attack_delta)"),
                        ],
                    ),
                ],
            ),
        ],
    )
    changed["modify_srd_sized_attack_damage"] = damage
    for name in (
        "resolve_combat_attack",
        "resolve_combat_attack_followup",
        "resolve_srd_unarmed_effect",
    ):
        owner = deepcopy(changed.get(name, tools[name]))
        if "modify_srd_sized_attack_damage" not in str(owner["effects"]):
            owner["effects"].insert(0, _set("_size_attack_roll", None))
            owner["pre_conditions"].append(
                {
                    "condition": (
                        "actor_state.actor_size_projected != true or "
                        "actor_state.spell_ray_of_enfeeblement != true"
                    ),
                    "error": (
                        "Simultaneous resizing and Ray damage need a unified ordering "
                        "owner before this attack can resolve."
                    ),
                }
            )
            if name != "resolve_srd_unarmed_effect":
                owner["effects"].insert(
                    0,
                    _when(
                        (
                            "actor_state.actor_size_projected == true and "
                            "actor_state.enlarge_scaled_items != null and weapon in "
                            "actor_state.enlarge_scaled_items"
                        ),
                        [
                            _call(
                                "validate_srd_inventory_spell_weapon",
                                {"actor_state": "{_actor_state_id}", "weapon": "{weapon}"},
                            ),
                        ],
                    ),
                )
            for effects in list(_effect_lists(owner["effects"])):
                for e in list(effects):
                    if e.get("call") not in (
                        "apply_srd_damage",
                        "apply_srd_divine_favor_weapon_damage",
                    ) or "_graze" in str(e):
                        continue
                    params = e["params"]
                    a = params["amount"]
                    b = params.get("adjustment", 0)
                    amount = (
                        (a.strip("{}") if isinstance(a, str) else str(a))
                        + " + "
                        + (b.strip("{}") if isinstance(b, str) else str(b))
                    )
                    before = [
                        _set("_size_final_hit_damage", amount),
                        _when(
                            "actor_state.actor_size_projected == true",
                            [
                                _call(
                                    "modify_srd_sized_attack_damage",
                                    {
                                        "actor_state": "{_actor_state_id}",
                                        "amount": "{_size_final_hit_damage}",
                                        "critical_hit": params.get("critical_hit", False),
                                        "weapon": None
                                        if name == "resolve_srd_unarmed_effect"
                                        else "{weapon}",
                                        "great_weapon_fighting": False,
                                    },
                                    {
                                        "_size_final_hit_damage": "_size_attack_damage",
                                        "_size_attack_roll": "_size_attack_roll",
                                    },
                                ),
                            ],
                        ),
                    ]
                    if name != "resolve_srd_unarmed_effect":
                        call = next(
                            e
                            for e in _walk_effects(before)
                            if e.get("call") == "modify_srd_sized_attack_damage"
                        )
                        expr = (
                            "'great_weapon_fighting' in _style_selected and "
                            "weapon_info.kind == 'melee' and wielding_two_handed == true "
                            "and ('two_handed' in weapon_info.properties or 'versatile' "
                            "in weapon_info.properties)"
                        )
                        before.insert(1, _set("_size_great_weapon_fighting", expr))
                        call["params"]["great_weapon_fighting"] = "{_size_great_weapon_fighting}"
                    if "condition" in e:
                        before = [_when(e["condition"], before)]
                    pos = effects.index(e)
                    effects[pos:pos] = before
                    params["amount"] = "{_size_final_hit_damage}"
                    params["adjustment"] = 0
            if name != "resolve_srd_unarmed_effect":
                owner["effects"].append(
                    _when(
                        ("actor_state.actor_size_projected == true and attack_mode == 'thrown'"),
                        [
                            _call(
                                "retire_srd_released_sized_items",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "lasting_state": "state",
                                    "released_item": "{weapon}",
                                },
                            ),
                            _call(
                                "refresh_srd_timed_condition_target",
                                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
                            ),
                        ],
                    )
                )
        if not any(e.get("set", {}).get("field") == "_size_attack_roll" for e in owner["effects"]):
            owner["effects"].insert(0, _set("_size_attack_roll", None))
        for e in owner["effects"]:
            if "retire_srd_released_sized_items" in str(e):
                e["condition"] = (
                    "actor_state.actor_size_projected == true and attack_mode == 'thrown'"
                )
        changed[name] = owner
    for name in (
        "resolve_srd_unarmed_effect",
        "resolve_unarmed_strike",
        "resolve_unarmed_opportunity_attack",
        "resolve_srd_monk_bonus_unarmed_strike",
    ):
        if name not in tools:
            continue
        owner = deepcopy(changed.get(name, tools[name]))
        for pre in owner.get("pre_conditions", []):
            original = pre.get("condition", "")
            if "actor.data.size_rank" not in original or "actor_size_projected" in original:
                continue
            pre["condition"] = (
                "(actor_state.actor_size_projected == true and ("
                + original.replace("actor.data.size_rank", "actor_state.size_rank")
                + ")) or (actor_state.actor_size_projected != true and ("
                + original
                + "))"
            )
        for e in _walk_effects(owner["effects"]):
            if e.get("call") == "resolve_srd_unarmed_effect":
                e.setdefault("export_context", {})["_size_attack_roll"] = "_size_attack_roll"
        changed[name] = owner
    for name, actor in (
        ("resolve_opportunity_attack", "reactor"),
        ("resolve_adjudicated_creature_attack", "actor"),
    ):
        owner = deepcopy(changed.get(name, tools[name]))
        if "resized weapon identity" not in str(owner.get("pre_conditions", [])):
            owner["pre_conditions"].append(
                {
                    "condition": actor + ".actor_size_projected != true",
                    "error": (
                        "This supplied attack needs actual resized weapon identity before "
                        "resolution."
                    ),
                }
            )
        changed[name] = owner


def _compose_slow_repeat_save(tools, changed):
    "Resolve the active Slow source's printed end-turn save without retiring other sources."
    install = changed["install_srd_actor_spell_modifier"]
    for e in _walk_effects(install["effects"]):
        if e.get("list_push", {}).get("list") == "_modifier_retained_records":
            value = e["list_push"]["value"]
            if isinstance(value, dict) and value.get("kind") == "'actor_spell_modifier'":
                value["last_slow_save_turn"] = -1
    repeat = _tool(
        "Repeat the strongest active Slow save at the end of this target's real turn.",
        [
            _entity("target_state"),
            _entity("lasting_state"),
        ],
        [
            _set("_slow_repeat_roll", None),
            _set("_slow_repeat_outcome", None),
            _set("_slow_repeat_source", None),
            _set("_slow_repeat_records", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_slow_repeat_records", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_slow_repeat_records",
                    "as": "_slow_repeat_candidate",
                    "do": [
                        _when(
                            (
                                "_slow_repeat_candidate.kind == 'actor_spell_modifier' "
                                "and _slow_repeat_candidate.mode == 'slow' and "
                                "_slow_repeat_candidate.target == _target_state_id"
                            ),
                            [
                                _call(
                                    "check_srd_actor_spell_modifier_active",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "source_state": "{_slow_repeat_candidate.caster}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "spell": "{_slow_repeat_candidate.spell}",
                                        "started_tick": "{_slow_repeat_candidate.started_tick}",
                                        "expires_tick": "{_slow_repeat_candidate.expires_tick}",
                                        "concentration": "{_slow_repeat_candidate.concentration}",
                                    },
                                    ["_actor_modifier_active"],
                                ),
                                _when(
                                    (
                                        "_actor_modifier_active == true and "
                                        "(_slow_repeat_source == null or "
                                        "_slow_repeat_candidate.save_dc >= "
                                        "_slow_repeat_source.save_dc)"
                                    ),
                                    [
                                        _set("_slow_repeat_source", "_slow_repeat_candidate"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                }
            },
            _when(
                (
                    "_slow_repeat_source != null and "
                    "_slow_repeat_source.last_slow_save_turn != "
                    "target_state.turns_started"
                ),
                [
                    _call(
                        "d20_test",
                        {
                            "actor_state": "{_target_state_id}",
                            "ability": "wisdom",
                            "kind": "saving_throw",
                            "ability_modifier": "{target_state.saving_throw_modifiers.wisdom}",
                            "proficiency_bonus": 0,
                            "proficiency_factor": "none",
                            "circumstantial_modifier": 0,
                            "target_number": "{_slow_repeat_source.save_dc}",
                            "advantage": False,
                            "disadvantage": False,
                        },
                        {"_slow_repeat_roll": "_d20_test", "_slow_repeat_outcome": "_outcome"},
                    ),
                    _set("_slow_repeat_retained", []),
                    {
                        "for_each": {
                            "collection": "_slow_repeat_records",
                            "as": "_slow_repeat_record",
                            "do": [
                                _when(
                                    (
                                        "_slow_repeat_record.kind == "
                                        "'actor_spell_modifier' and "
                                        "_slow_repeat_record.mode == 'slow' and "
                                        "_slow_repeat_record.target == _target_state_id "
                                        "and _slow_repeat_record.caster == "
                                        "_slow_repeat_source.caster and "
                                        "_slow_repeat_record.started_tick == "
                                        "_slow_repeat_source.started_tick"
                                    ),
                                    [
                                        _when(
                                            "_slow_repeat_outcome != 'success'",
                                            [
                                                _set(
                                                    "_slow_repeat_record_updated",
                                                    (
                                                        "merge(_slow_repeat_record, "
                                                        '{"last_slow_save_turn": '
                                                        "target_state.turns_started})"
                                                    ),
                                                ),
                                                {
                                                    "list_push": {
                                                        "list": "_slow_repeat_retained",
                                                        "value": "_slow_repeat_record_updated",
                                                    }
                                                },
                                            ],
                                        ),
                                    ],
                                    [
                                        {
                                            "list_push": {
                                                "list": "_slow_repeat_retained",
                                                "value": "_slow_repeat_record",
                                            }
                                        },
                                    ],
                                ),
                            ],
                        }
                    },
                    _set("lasting_state.turn_effects", "_slow_repeat_retained"),
                    _call(
                        "refresh_srd_timed_condition_target",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    changed["resolve_srd_slow_end_turn"] = repeat
    end = changed["end_combat_turn"]
    if "resolve_srd_slow_end_turn" not in str(end["effects"]):
        index = next(
            i
            for i, e in enumerate(end["effects"])
            if e.get("set", {}).get("field") == "combat.action_available"
        )
        end["effects"].insert(
            index,
            _when(
                "actor.spell_slow == true",
                [
                    _call(
                        "resolve_srd_slow_end_turn",
                        {"target_state": "{_actor_id}", "lasting_state": "state"},
                        ["_slow_repeat_roll", "_slow_repeat_outcome"],
                    ),
                ],
            ),
        )
