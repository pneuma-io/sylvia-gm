"""Package-owned lasting actor defenses and their later mechanical consumers."""

import re
from copy import deepcopy

import yaml
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations


def _load_runtime(text):
    """Read safe YAML and restore established defaults expected by the source emitters."""
    if __package__:
        from .srd_generator_yaml import clear_generator_yaml_reuse, load_generator_yaml
        from .srd_runtime_defaults import restore_tool_defaults
    else:
        from srd_generator_yaml import clear_generator_yaml_reuse, load_generator_yaml
        from srd_runtime_defaults import restore_tool_defaults

    try:
        runtime = load_generator_yaml(text)
        return restore_tool_defaults(resolve_parameter_declarations(runtime))
    except BaseException:
        clear_generator_yaml_reuse()
        raise


class _RuntimeDumper(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
    """Render independent tool fragments without document-wide anchor collisions."""

    def ignore_aliases(self, data):
        return True


def _set(field, value):
    return {"set": {"field": field, "value": value}}


def _when(condition, effects):
    return {"condition": condition, "on_true": effects}


def _require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def _parameter(name, kind="string", **extra):
    return {"name": name, "type": kind, "required": True, **extra}


def _tool(description, parameters, effects, pre_conditions=()):
    if re.search(r"\bstate\.", str(effects)):
        if not any(parameter["name"] == "lasting_state" for parameter in parameters):
            parameters.append(
                {
                    "name": "lasting_state",
                    "type": "string",
                    "resolve": "entity",
                    "required": False,
                    "default": "state",
                }
            )

        def bind_lasting(value):
            if isinstance(value, str):
                return re.sub(r"\bstate\.", "lasting_state.", value)
            if isinstance(value, list):
                return [bind_lasting(child) for child in value]
            if isinstance(value, dict):
                result = {}
                for key, child in value.items():
                    if key in {"spatial_query", "spatial_mutation"} and isinstance(child, dict):
                        result[key] = {
                            field: re.sub(r"^lasting_state\.", "state.", item)
                            if field == "scene" and isinstance(item, str)
                            else bind_lasting(item)
                            for field, item in child.items()
                        }
                    else:
                        result[key] = bind_lasting(child)
                return result
            return value

        effects = bind_lasting(effects)
    return {
        "description": description,
        "parameters": parameters,
        "pre_conditions": list(pre_conditions),
        "effects": effects,
        "metadata": {"category": "internal", "visible_to_player": False, "costs_action": False},
    }


def _call(name, params, exports=()):
    result = {"call": name, "params": params}
    if exports:
        result["export_context"] = {key: key for key in exports}
    return result


def _entity(name):
    return _parameter(name, resolve="entity")


def _collect_unique(destination, source, item):
    """Copy a definition's values to an iterable local and collect nonstacking benefits."""
    collection = destination + "_values"
    return [
        _set(collection, source),
        {
            "for_each": {
                "collection": collection,
                "as": item,
                "do": [
                    _when(
                        f"{item} not in {destination}",
                        [{"list_push": {"list": destination, "value": item}}],
                    )
                ],
            }
        },
    ]


def _defense_schema():
    properties = {
        "casting_time": {"type": "string", "enum": ["action"]},
        "base_level": {"type": "integer", "minimum": 1, "maximum": 9},
        "range_kind": {"type": "string", "enum": ["touch", "self"]},
        "range_feet": {"type": "integer", "minimum": 0, "maximum": 0},
        "requires_willing": {"type": "boolean"},
        "duration_rounds": {"type": "integer", "minimum": 1, "maximum": 100000},
        "concentration": {"type": "boolean"},
        "base_targets": {"type": "integer", "minimum": 1, "maximum": 8},
        "targets_per_slot_level": {"type": "integer", "minimum": 0, "maximum": 1},
        "base_ac": {"type": "integer", "minimum": 0, "maximum": 13},
        "attack_disadvantage": {"type": "boolean"},
        "perception_bypasses": {
            "type": "array",
            "items": {"type": "string", "enum": ["blindsight", "truesight"]},
            "uniqueItems": True,
        },
        "source_creature_types": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "condition_immunities": {
            "type": "array",
            "items": {"type": "string", "enum": ["charmed", "frightened"]},
            "uniqueItems": True,
        },
        "damage_immunities": {
            "type": "array",
            "items": {"type": "string", "enum": ["psychic"]},
            "uniqueItems": True,
        },
        "start_turn_temporary_hp": {"type": "boolean"},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


DEFENSE_EXPORTS = (
    "_actor_defense_ac",
    "_actor_defense_attack_disadvantage",
    "_actor_defense_damage_immunities",
    "_actor_defense_condition_immunities",
    "_actor_defense_source_types",
    "_actor_defense_temporary_hp",
)


def _tools():
    active = _tool(
        "Check one lasting actor-defense record against its "
        "clock and exact concentration instance.",
        [
            _entity("source_state"),
            _entity("lasting_state"),
            _parameter("spell"),
            _parameter("started_tick", "number"),
            _parameter("concentration", "boolean"),
            _parameter("expires_tick", "number"),
            _entity("target_state"),
        ],
        [
            _set(
                "_actor_defense_active",
                "lasting_state.lasting_tick != null and "
                "lasting_state.lasting_tick + 0.000000001 < expires_tick",
            ),
            _when(
                "concentration == true",
                [
                    _set(
                        "_actor_defense_active",
                        "_actor_defense_active == true and "
                        "source_state.concentration_spell_id == spell and "
                        "source_state.concentration_effect_family == 'timed_actor_defense' and "
                        "source_state.concentration_started_tick == started_tick and "
                        "source_state.concentration_target_state_ids != null and "
                        "_target_state_id in source_state.concentration_target_state_ids",
                    ),
                    _set(
                        "_actor_defense_active",
                        "_actor_defense_active == true and source_state.is_alive == true and "
                        "not ('incapacitated' in "
                        "source_state.conditions or 'paralyzed' in source_state.conditions or "
                        "'petrified' in source_state.conditions or 'stunned' in "
                        "source_state.conditions or 'unconscious' in source_state.conditions)",
                    ),
                ],
            ),
        ],
    )
    resolve = _tool(
        "Resolve active, nonstacking actor defenses for later attacks, damage and conditions.",
        [_entity("target_state"), _entity("lasting_state")],
        [
            _set("_actor_defense_ac", 0),
            _set("_actor_defense_attack_disadvantage", False),
            _set("_actor_defense_damage_immunities", []),
            _set("_actor_defense_condition_immunities", []),
            _set("_actor_defense_source_types", []),
            _set("_actor_defense_temporary_hp", 0),
            _set("_actor_defense_effects", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_actor_defense_effects", "lasting_state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_actor_defense_effects",
                    "as": "_defense_effect",
                    "do": [
                        _when(
                            "_defense_effect.kind == 'timed_actor_defense' and "
                            "_defense_effect.target == _target_state_id",
                            [
                                _call(
                                    "check_srd_timed_actor_defense_active",
                                    {
                                        "source_state": "{_defense_effect.caster}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "spell": "{_defense_effect.spell}",
                                        "started_tick": "{_defense_effect.started_tick}",
                                        "concentration": "{_defense_effect.concentration}",
                                        "expires_tick": "{_defense_effect.expires_tick}",
                                        "target_state": "{_target_state_id}",
                                    },
                                    ["_actor_defense_active"],
                                ),
                                _when(
                                    "_actor_defense_active == true",
                                    [
                                        {
                                            "table_lookup": {
                                                "table": "definitions.spells",
                                                "key": "_defense_effect.spell",
                                                "required": True,
                                            },
                                            "store_as": "_defense_profile",
                                        },
                                        _when(
                                            "_defense_profile.base_ac > 0 and "
                                            "target_state.worn_armor == null",
                                            [
                                                _call(
                                                    "validate_srd_actor_defense_target",
                                                    {
                                                        "target_state": "{_target_state_id}",
                                                        "spell": "{_defense_effect.spell}",
                                                        "caster_state": "{_defense_effect.caster}",
                                                        "target_character": "{_defense_effect.targe"
                                                        "t_character}",
                                                    },
                                                    ["_defense_target_ac"],
                                                ),
                                                _set(
                                                    "_actor_defense_ac",
                                                    "max(_actor_defense_ac, _defense_target_ac)",
                                                ),
                                            ],
                                        ),
                                        _when(
                                            "_defense_profile.attack_disadvantage == true",
                                            [_set("_actor_defense_attack_disadvantage", True)],
                                        ),
                                        _when(
                                            "len(_defense_profile.source_creature_types) > 0",
                                            [
                                                _set(
                                                    "_actor_defense_source_types",
                                                    "_defense_profile.source_creature_types",
                                                )
                                            ],
                                        ),
                                        _when(
                                            "len(_defense_profile.source_creature_types) == 0",
                                            _collect_unique(
                                                "_actor_defense_condition_immunities",
                                                "_defense_profile.condition_immunities",
                                                "_defense_condition",
                                            ),
                                        ),
                                        *_collect_unique(
                                            "_actor_defense_damage_immunities",
                                            "_defense_profile.damage_immunities",
                                            "_defense_damage_type",
                                        ),
                                        _when(
                                            "_defense_profile.start_turn_temporary_hp == true",
                                            [
                                                _set(
                                                    "_actor_defense_temporary_hp",
                                                    "max(_actor_defense_temporary_hp, "
                                                    "_defense_effect.magnitude)",
                                                )
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
    attack = _tool(
        "Read active attack defenses, respecting protected source types and perception exceptions.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _parameter("attack_distance", "integer", minimum=0),
        ],
        [
            _set("_actor_defense_attack_disadvantage", False),
            _set("_actor_attack_defense_effects", []),
            _when(
                "state.turn_effects != null",
                [_set("_actor_attack_defense_effects", "state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_actor_attack_defense_effects",
                    "as": "_attack_defense",
                    "do": [
                        _when(
                            "_attack_defense.kind == 'timed_actor_defense' and "
                            "_attack_defense.target == _target_state_id",
                            [
                                _call(
                                    "check_srd_timed_actor_defense_active",
                                    {
                                        "source_state": "{_attack_defense.caster}",
                                        "lasting_state": "state",
                                        "spell": "{_attack_defense.spell}",
                                        "started_tick": "{_attack_defense.started_tick}",
                                        "concentration": "{_attack_defense.concentration}",
                                        "expires_tick": "{_attack_defense.expires_tick}",
                                        "target_state": "{_target_state_id}",
                                    },
                                    ["_actor_defense_active"],
                                ),
                                _when(
                                    "_actor_defense_active == true",
                                    [
                                        {
                                            "table_lookup": {
                                                "table": "definitions.spells",
                                                "key": "_attack_defense.spell",
                                                "required": True,
                                            },
                                            "store_as": "_attack_defense_profile",
                                        },
                                        _set(
                                            "_defense_attack_applies",
                                            "_attack_defense_profile.attack_disadvantage == true",
                                        ),
                                        _when(
                                            "len(_attack_defense_profile.source_creature_types) "
                                            "> 0",
                                            [
                                                _require(
                                                    "actor_state.creature_type != null",
                                                    "Record the attacking creature's type before "
                                                    "resolving its source-qualified defense.",
                                                ),
                                                _set(
                                                    "_defense_attack_applies",
                                                    "_defense_attack_applies == true and "
                                                    "actor_state.creature_type in "
                                                    "_attack_defense_profile.source_creature_types",
                                                ),
                                            ],
                                        ),
                                        _when(
                                            "'blindsight' in "
                                            "_attack_defense_profile.perception_bypasses and "
                                            "actor_state.blindsight_feet != null and "
                                            "actor_state.blindsight_feet "
                                            ">= attack_distance",
                                            [_set("_defense_attack_applies", False)],
                                        ),
                                        _when(
                                            "'truesight' in _attack_defense_profile.perception_"
                                            "bypasses and "
                                            "'blinded' not in actor_state.conditions and "
                                            "actor_state.truesight_feet != null and "
                                            "actor_state.truesight_feet "
                                            ">= attack_distance",
                                            [_set("_defense_attack_applies", False)],
                                        ),
                                        _when(
                                            "_defense_attack_applies == true",
                                            [_set("_actor_defense_attack_disadvantage", True)],
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
    immunity = _tool(
        "Derive permanent and active source-qualified "
        "condition immunity and existing-effect save "
        "Advantage.",
        [
            _entity("target_state"),
            _parameter("condition"),
            {"name": "source_state_id", "type": "string", "required": False, "default": ""},
        ],
        [
            _call(
                "resolve_srd_timed_actor_defenses",
                {"target_state": "{_target_state_id}", "lasting_state": "state"},
                DEFENSE_EXPORTS,
            ),
            _set(
                "_source_condition_immune",
                "(target_state.condition_immunities != null and "
                "condition in target_state.condition_immunities) or "
                "condition in _actor_defense_condition_immunities",
            ),
            _set("_source_condition_save_advantage", False),
            _set(
                "_source_condition_needs_source",
                "len(_actor_defense_source_types) > 0 and "
                "(condition == 'charmed' or condition == 'frightened') and source_state_id == ''",
            ),
            _when(
                "len(_actor_defense_source_types) > 0 and source_state_id != '' and "
                "(condition == 'charmed' or condition == 'frightened')",
                [
                    _call(
                        "check_srd_timed_actor_defense_source_type",
                        {
                            "source_state": "{source_state_id}",
                            "source_types": "{_actor_defense_source_types}",
                        },
                        ["_actor_defense_source_matches"],
                    ),
                    _when(
                        "_actor_defense_source_matches == true",
                        [
                            _set("_source_condition_immune", True),
                            _set("_defense_prior_condition_sources", []),
                            _when(
                                "target_state.timed_condition_source_ids != null",
                                [
                                    _set(
                                        "_defense_prior_condition_sources",
                                        "target_state.timed_condition_source_ids",
                                    )
                                ],
                            ),
                            {
                                "for_each": {
                                    "collection": "_defense_prior_condition_sources",
                                    "as": "_defense_prior_id",
                                    "do": [
                                        _when(
                                            "state.timed_condition_sources != null and "
                                            "_defense_prior_id in state.timed_condition_sources",
                                            [
                                                _set(
                                                    "_defense_prior_source",
                                                    "state.timed_condition_"
                                                    "sources[_defense_prior"
                                                    "_id]",
                                                ),
                                                _when(
                                                    "_defense_prior_source.installed == true and "
                                                    "_defense_prior_source."
                                                    "condition == condition"
                                                    " and "
                                                    "_defense_prior_source.caster_state_id == "
                                                    "source_state_id",
                                                    [
                                                        _set(
                                                            "_source_condition_save_advantage", True
                                                        )
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
            ),
        ],
    )
    source_type = _tool(
        "Compare the causing creature's authoritative type with a defense's fixed type list.",
        [_entity("source_state"), _parameter("source_types", "array", items={"type": "string"})],
        [
            _require(
                "source_state.creature_type != null",
                "Record the causing creature's type before resolving its source-qualified defense.",
            ),
            _set("_actor_defense_source_matches", "source_state.creature_type in source_types"),
        ],
    )
    retire = _tool(
        "Permanently end armor-sensitive spell formulas when their target dons armor.",
        [_entity("target_state")],
        [
            _set("_defense_retained_effects", []),
            _set("_defense_retiring_effects", []),
            _when(
                "state.turn_effects != null",
                [_set("_defense_retiring_effects", "state.turn_effects")],
            ),
            {
                "for_each": {
                    "collection": "_defense_retiring_effects",
                    "as": "_retiring_defense",
                    "do": [
                        _set("_retire_actor_defense", False),
                        _when(
                            "_retiring_defense.kind == 'timed_actor_defense' and "
                            "_retiring_defense.target == _target_state_id",
                            [
                                {
                                    "table_lookup": {
                                        "table": "definitions.spells",
                                        "key": "_retiring_defense.spell",
                                        "required": True,
                                    },
                                    "store_as": "_retiring_profile",
                                },
                                _set(
                                    "_retire_actor_defense",
                                    "_retiring_profile.base_ac > 0 and "
                                    "target_state.worn_armor != null",
                                ),
                            ],
                        ),
                        _when(
                            "_retire_actor_defense == false",
                            [
                                {
                                    "list_push": {
                                        "list": "_defense_retained_effects",
                                        "value": "_retiring_defense",
                                    }
                                }
                            ],
                        ),
                    ],
                }
            },
            _set("state.turn_effects", "_defense_retained_effects"),
        ],
    )
    turn = _tool(
        "Grant active definition-owned Temporary HP at a protected creature's actual turn start.",
        [_entity("target_state")],
        [
            _call(
                "resolve_srd_timed_actor_defenses",
                {"target_state": "{_target_state_id}", "lasting_state": "state"},
                DEFENSE_EXPORTS,
            ),
            _when(
                "_actor_defense_temporary_hp > 0",
                [
                    _set(
                        "target_state.temp_hp",
                        "max(target_state.temp_hp, _actor_defense_temporary_hp)",
                    )
                ],
            ),
        ],
    )
    return {
        "check_srd_timed_actor_defense_active": active,
        "resolve_srd_timed_actor_defenses": resolve,
        "resolve_srd_timed_actor_attack_defenses": attack,
        "check_srd_target_condition_immunity": immunity,
        "check_srd_timed_actor_defense_source_type": source_type,
        "retire_srd_armored_actor_defenses": retire,
        "start_srd_timed_actor_defense_turn": turn,
    }


def _cast_tool():
    return _tool(
        "Install source-defined timed actor defenses with actual touch, willingness and scaling.",
        [
            _parameter("spell"),
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("slot_level", "integer", minimum=1, maximum=9),
            {"name": "caster", "type": "string", "resolve": "participant", "required": False},
            {
                "name": "target_character",
                "type": "string",
                "resolve": "participant",
                "required": False,
            },
            _parameter("spellcasting_modifier", "integer"),
            {"name": "concentration_save_modifier", "type": "integer", "required": False},
            {
                "name": "targets",
                "type": "array",
                "items": {"type": "string"},
                "required": False,
                "default": [],
            },
            _parameter("target_willing", "boolean"),
            _parameter("caster_can_touch_target", "boolean"),
            _parameter("all_targets_willing", "boolean"),
            _parameter("caster_can_touch_all_targets", "boolean"),
        ],
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_defense_cast_profile",
            },
            _set("_defense_cast_targets", []),
            _when(
                "_defense_cast_profile.range_kind == 'self'",
                [{"list_push": {"list": "_defense_cast_targets", "value": "_caster_state_id"}}],
            ),
            _when(
                "_defense_cast_profile.range_kind == 'touch'",
                [
                    _when(
                        "len(targets) == 0",
                        [
                            _require(
                                "caster_can_touch_target == true",
                                "The caster must touch this creature.",
                            ),
                            _require(
                                "_defense_cast_profile.requires_willing != true or "
                                "target_willing == true",
                                "The touched creature must be willing.",
                            ),
                            {
                                "list_push": {
                                    "list": "_defense_cast_targets",
                                    "value": "_target_state_id",
                                }
                            },
                        ],
                    ),
                    _when(
                        "len(targets) > 0",
                        [
                            _require(
                                "caster_can_touch_all_targets == true and "
                                "all_targets_willing == true",
                                "Every target must be willing and within touch.",
                            ),
                            _set("_defense_cast_targets", "targets"),
                        ],
                    ),
                ],
            ),
            _require(
                "len(_defense_cast_targets) >= 1 and len(_defense_cast_targets) <= "
                "_defense_cast_profile.base_targets + (slot_level - "
                "_defense_cast_profile.base_level) * _defense_cast_profile.targets_per_slot_level",
                "The spell targets more creatures than its selected slot allows.",
            ),
            _set("_defense_distinct_targets", []),
            {
                "for_each": {
                    "collection": "_defense_cast_targets",
                    "as": "_defense_cast_target",
                    "do": [
                        _require(
                            "_defense_cast_target not in _defense_distinct_targets",
                            "An actor-defense target cannot be selected twice.",
                        ),
                        {
                            "list_push": {
                                "list": "_defense_distinct_targets",
                                "value": "_defense_cast_target",
                            }
                        },
                        _call(
                            "validate_srd_actor_defense_target",
                            {
                                "target_state": "{_defense_cast_target}",
                                "spell": "{spell}",
                                "caster": "{_caster_id}",
                                "caster_state": "{_caster_state_id}",
                                "target_character": "{_target_character_id}",
                            },
                        ),
                    ],
                }
            },
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when(
                "_defense_cast_profile.concentration == true",
                [
                    _require(
                        "concentration_save_modifier != null",
                        "A concentration defense needs "
                        "the caster's authoritative Constitution saving-throw modifier.",
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
                            )
                        ],
                    ),
                    _set("caster_state.concentration_spell_id", "spell"),
                    _set("caster_state.concentration_effect_family", "'timed_actor_defense'"),
                    _set("caster_state.concentration_target_state_id", "_defense_cast_targets[0]"),
                    _set("caster_state.concentration_target_state_ids", "_defense_cast_targets"),
                    _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                    _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
                    _set(
                        "caster_state.concentration_expires_tick",
                        "state.lasting_tick + _defense_cast_profile.duration_rounds * 2",
                    ),
                ],
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            {
                "for_each": {
                    "collection": "_defense_cast_targets",
                    "as": "_defense_cast_target",
                    "do": [
                        _call(
                            "install_srd_timed_actor_defense",
                            {
                                "target_state": "{_defense_cast_target}",
                                "spell": "{spell}",
                                "caster": "{_caster_id}",
                                "caster_state": "{_caster_state_id}",
                                "target_character": "{_target_character_id}",
                                "spellcasting_modifier": "{spellcasting_modifier}",
                            },
                        ),
                        _call(
                            "record_srd_spell_target_result",
                            {
                                "target": "{_defense_cast_target}",
                                "brief": "{spell} defense active",
                                "detail": "Defense lasts {_defense_cast_profile.duration_roun"
                                "ds} rounds; "
                                "later attacks, damage and condition effects use "
                                "its recorded rules.",
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


def _target_tools():
    parameters = [
        _entity("target_state"),
        _parameter("spell"),
        _entity("caster_state"),
        {"name": "caster", "type": "string", "resolve": "participant", "required": False},
        {"name": "target_character", "type": "string", "resolve": "participant", "required": False},
    ]
    validate = _tool(
        "Validate exact actor-defense targets and derive "
        "Mage Armor from authoritative creature Dexterity.",
        parameters,
        [
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_defense_target_profile",
            },
            _require(
                "target_state.is_alive == true", "A lasting actor defense needs a living creature."
            ),
            _set("_defense_target_ac", 0),
            _set("_defense_target_character", None),
            _when(
                "_defense_target_profile.base_ac > 0",
                [
                    _require(
                        "target_state.worn_armor == null",
                        "Mage Armor cannot target a creature wearing armor.",
                    ),
                    _set("_defense_target_dexterity", None),
                    _set("_defense_target_shield_trained", False),
                    _when(
                        "target_state.equipment_profile != null",
                        [
                            _set(
                                "_defense_target_dexterity",
                                "target_state.equipment_profile.dexterity",
                            ),
                            _set(
                                "_defense_target_shield_trained",
                                "'shield' in target_state.equipment_profile.armor_training",
                            ),
                        ],
                    ),
                    _when(
                        "caster != null and _target_state_id == _caster_state_id",
                        [
                            _set("_defense_target_dexterity", "caster.data.abilities.dexterity"),
                            _set("_defense_target_character", "_caster_id"),
                            _call(
                                "resolve_srd_equipment",
                                {"actor": "{_caster_id}", "actor_state": "{_target_state_id}"},
                                ["_equipment_training"],
                            ),
                            _set(
                                "_defense_target_shield_trained", "'shield' in _equipment_training"
                            ),
                        ],
                    ),
                    _when(
                        "target_character != null",
                        [
                            _require(
                                "target_character.participant_id == _target_state_id and "
                                "target_character.validation_status == 'validated' and "
                                "target_character.package_id == 'wotc.srd.5.2.1' and "
                                "target_character.kind == 'player_character'",
                                "The target Character File must match this creature and ruleset.",
                            ),
                            _set(
                                "_defense_target_dexterity",
                                "target_character.data.abilities.dexterity",
                            ),
                            _set("_defense_target_character", "_target_character_id"),
                            _call(
                                "resolve_srd_equipment",
                                {
                                    "actor": "{_target_character_id}",
                                    "actor_state": "{_target_state_id}",
                                },
                                ["_equipment_training"],
                            ),
                            _set(
                                "_defense_target_shield_trained", "'shield' in _equipment_training"
                            ),
                        ],
                    ),
                    _require(
                        "_defense_target_dexterity != null",
                        "Mage Armor needs the target's "
                        "validated Character File or saved NPC equipment ability profile.",
                    ),
                    _set(
                        "_defense_target_ac",
                        "_defense_target_profile.base_ac + (_defense_target_dexterity - 10) // 2",
                    ),
                    _when(
                        "target_state.worn_shield != null and "
                        "_defense_target_shield_trained == true",
                        [
                            _set(
                                "_defense_target_ac",
                                "_defense_target_ac + "
                                "definitions.srd_armor[target_state.worn_shield.arm"
                                "or_type].base_ac + "
                                "target_state.worn_shield.magic_bonus",
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    install = _tool(
        "Persist one bounded defense record after the full target set was validated.",
        parameters + [_parameter("spellcasting_modifier", "integer")],
        [
            _call(
                "validate_srd_actor_defense_target",
                {
                    "target_state": "{_target_state_id}",
                    "spell": "{spell}",
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "target_character": "{_target_character_id}",
                },
                ["_defense_target_ac", "_defense_target_character"],
            ),
            {
                "table_lookup": {"table": "definitions.spells", "key": "spell", "required": True},
                "store_as": "_defense_install_profile",
            },
            _set("_defense_install_magnitude", "_defense_target_ac"),
            _when(
                "_defense_install_profile.start_turn_temporary_hp == true",
                [_set("_defense_install_magnitude", "max(0, spellcasting_modifier)")],
            ),
            _set("_actor_installed_defenses", []),
            _set("_actor_prior_defenses", []),
            _when(
                "state.turn_effects != null", [_set("_actor_prior_defenses", "state.turn_effects")]
            ),
            {
                "for_each": {
                    "collection": "_actor_prior_defenses",
                    "as": "_prior_actor_defense",
                    "do": [
                        _when(
                            "not (_prior_actor_defense.kind == 'timed_actor_defense' and "
                            "_prior_actor_defense.spell == spell and "
                            "_prior_actor_defense.caster == "
                            "_caster_state_id and _prior_actor_defense.target == _target_state_id)",
                            [
                                {
                                    "list_push": {
                                        "list": "_actor_installed_defenses",
                                        "value": "_prior_actor_defense",
                                    }
                                }
                            ],
                        ),
                    ],
                }
            },
            _require(
                "len(_actor_installed_defenses) < 128",
                "Resolve existing lasting effects before adding more.",
            ),
            {
                "list_push": {
                    "list": "_actor_installed_defenses",
                    "value": {
                        "spell": "spell",
                        "label": "spell",
                        "caster": "_caster_state_id",
                        "target": "_target_state_id",
                        "kind": "'timed_actor_defense'",
                        "target_character": "_defense_target_character",
                        "owner": "'__time_only__'",
                        "phase": "'time'",
                        "turn": 1000000000,
                        "started_tick": "state.lasting_tick",
                        "expires_tick": "state.lasting_tick + "
                        "_defense_install_profile.duration_rounds * 2",
                        "concentration": "_defense_install_profile.concentration",
                        "magnitude": "_defense_install_magnitude",
                        "dice_count": 0,
                        "damage_die": "''",
                        "damage_type": "''",
                    },
                }
            },
            _set("state.turn_effects", "_actor_installed_defenses"),
        ],
    )
    record_push = install["effects"][-2]
    record = record_push["list_push"]["value"]
    record["target_character"] = None
    install["effects"][-2:-2] = [
        _set("_actor_defense_record", record),
        _when(
            "_defense_target_character != null",
            [
                _set(
                    "_actor_defense_character_binding",
                    {"target_character": "_defense_target_character"},
                ),
                _set(
                    "_actor_defense_record",
                    "merge(_actor_defense_record, _actor_defense_character_binding)",
                ),
            ],
        ),
    ]
    record_push["list_push"]["value"] = "_actor_defense_record"
    install["effects"].append(_set("target_state.has_timed_actor_defenses", True))
    return {
        "validate_srd_actor_defense_target": validate,
        "install_srd_timed_actor_defense": install,
    }


def extend_runtime(text):
    "Compose defense declarations at the package"
    "generator, retaining other authored runtime text."
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = _tools() | _target_tools() | {"cast_spell_timed_actor_defense": _cast_tool()}
    cast = deepcopy(tools["cast_spell"])
    if not any(p["name"] == "target_character" for p in cast["parameters"]):
        cast["parameters"].append(
            {
                "name": "target_character",
                "type": "string",
                "resolve": "participant",
                "required": False,
                "description": "Mage Armor target's validated "
                "Character File, when the target is another character.",
            }
        )

    def remove_prior_dispatch(value):
        if isinstance(value, dict):
            for child in value.values():
                remove_prior_dispatch(child)
        elif isinstance(value, list):
            value[:] = [
                child
                for child in value
                if not (
                    isinstance(child, dict)
                    and child.get("condition")
                    == "_spell_definition.family == 'timed_actor_defense'"
                )
            ]
            for child in value:
                remove_prior_dispatch(child)

    remove_prior_dispatch(cast["effects"])
    if "cast_spell_timed_actor_defense" not in str(cast["effects"]):
        branch = _when(
            "_spell_definition.family == 'timed_actor_defense'",
            [
                _call(
                    "cast_spell_timed_actor_defense",
                    {
                        "spell": "{spell}",
                        "caster": "{_caster_id}",
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_scalar_target_id}",
                        "target_character": "{_target_character_id}",
                        "slot_level": "{_slot_level}",
                        "spellcasting_modifier": "{_casting_profile.modifier}",
                        "concentration_save_modifier": "{_casting_profile.conc"
                        "entration_save_modifie"
                        "r}",
                        "targets": "{_defense_dispatch_targets}",
                        "target_willing": "{target_willing}",
                        "caster_can_touch_target": "{_caster_can_touch_target}",
                        "all_targets_willing": "{all_targets_willing}",
                        "caster_can_touch_all_targets": "{caster_can_touch_all_targets}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
            ],
        )
        # Dispatch shares the existing nested export/append convention.
        branch["on_true"][0]["export_context"] = {
            "_nested_spell_summary_rows": "_spell_summary_rows",
            "_nested_spell_detail_rows": "_spell_detail_rows",
        }
        for suffix in ("summary", "detail"):
            branch["on_true"].append(
                {
                    "for_each": {
                        "collection": f"_nested_spell_{suffix}_rows",
                        "as": "_nested_spell_row",
                        "do": [
                            {
                                "list_push": {
                                    "list": f"_spell_{suffix}_rows",
                                    "value": "_nested_spell_row",
                                }
                            }
                        ],
                    }
                }
            )
        branch["on_true"][0:0] = [
            _set("_defense_dispatch_targets", []),
            _when("targets != null", [_set("_defense_dispatch_targets", "targets")]),
        ]
        last_family = max(
            i
            for i, effect in enumerate(cast["effects"])
            if effect.get("condition", "").startswith("_spell_definition.family ==")
        )
        cast["effects"].insert(last_family + 1, branch)
    changed["cast_spell"] = cast

    def refresh_dispatch(value):
        if isinstance(value, dict):
            if value.get("call") == "cast_spell_timed_actor_defense":
                value["params"]["concentration_save_modifier"] = (
                    "{_casting_profile.concentration_save_modifier}"
                )
            for item in value.values():
                refresh_dispatch(item)
        elif isinstance(value, list):
            for item in value:
                refresh_dispatch(item)

    refresh_dispatch(cast["effects"])

    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    if "resolve_srd_timed_actor_attack_defenses" not in str(attack["effects"]):
        attack["effects"].extend(
            [
                _call(
                    "resolve_srd_timed_actor_attack_defenses",
                    {
                        "actor_state": "{_actor_state_id}",
                        "target_state": "{_target_state_id}",
                        "attack_distance": "{attack_distance}",
                    },
                    ["_actor_defense_attack_disadvantage"],
                ),
                _set(
                    "_condition_disadvantage",
                    "_condition_disadvantage == true or _actor_defense_attack_disadvantage == true",
                ),
            ]
        )
    changed["resolve_srd_attack_conditions"] = attack

    ac = deepcopy(tools["resolve_srd_effective_armor_class"])
    if "resolve_srd_timed_actor_defenses" not in str(ac["effects"]):
        ac["effects"].insert(
            1,
            _call(
                "resolve_srd_timed_actor_defenses",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                ["_actor_defense_ac"],
            ),
        )
        ac["effects"].insert(
            2, _set("_effective_armor_class", "max(_effective_armor_class, _actor_defense_ac)")
        )
    changed["resolve_srd_effective_armor_class"] = ac

    damage = deepcopy(tools["apply_srd_damage"])
    if "resolve_srd_timed_actor_defenses" not in str(damage["effects"]):
        insertion = next(
            i
            for i, e in enumerate(damage["effects"])
            if e.get("condition") == "damage_type in target.damage_immunities"
        )
        damage["effects"][insertion:insertion] = [
            _call(
                "resolve_srd_timed_actor_defenses",
                {"target_state": "{target}", "lasting_state": "state"},
                ["_actor_defense_damage_immunities"],
            ),
            _when(
                "damage_type in _actor_defense_damage_immunities", [_set("_effective_damage", 0)]
            ),
        ]
    changed["apply_srd_damage"] = damage

    composite = deepcopy(tools["apply_srd_composite_damage"])
    if "resolve_srd_timed_actor_defenses" not in str(composite["effects"]):
        composite["effects"].insert(
            0,
            _call(
                "resolve_srd_timed_actor_defenses",
                {"target_state": "{target}", "lasting_state": "state"},
                ["_actor_defense_damage_immunities"],
            ),
        )
        for component in (1, 2):
            insertion = next(
                i
                for i, e in enumerate(composite["effects"])
                if e.get("condition")
                == f"component_{component}_damage_type in target.damage_immunities"
            )
            composite["effects"].insert(
                insertion,
                _when(
                    f"component_{component}_damage_type in _actor_defense_damage_immunities",
                    [_set(f"_component_{component}_effective_damage", 0)],
                ),
            )
    changed["apply_srd_composite_damage"] = composite

    condition = deepcopy(tools["apply_srd_condition"])
    if "check_srd_target_condition_immunity" not in str(condition["effects"]):
        condition["parameters"].append(
            {"name": "source_state_id", "type": "string", "required": False, "default": ""}
        )
        condition["effects"][0:0] = [
            _call(
                "check_srd_target_condition_immunity",
                {
                    "target_state": "{target}",
                    "condition": "{condition}",
                    "source_state_id": "{source_state_id}",
                },
                ["_source_condition_immune", "_source_condition_needs_source"],
            ),
            _require(
                "_source_condition_needs_source != true",
                "Identify the causing creature for a source-qualified condition defense.",
            ),
            _require(
                "_source_condition_immune != true",
                "This creature is immune to the selected condition.",
            ),
        ]
    changed["apply_srd_condition"] = condition

    save = deepcopy(tools["resolve_srd_condition_saving_throw"])
    if "check_srd_target_condition_immunity" not in str(save["effects"]):
        save["parameters"].append(
            {"name": "source_state_id", "type": "string", "required": False, "default": ""}
        )
        save["effects"][0:0] = [
            _call(
                "check_srd_target_condition_immunity",
                {
                    "target_state": "{_target_id}",
                    "condition": "{condition}",
                    "source_state_id": "{source_state_id}",
                },
                ["_source_condition_save_advantage", "_source_condition_needs_source"],
            ),
            _require(
                "_source_condition_needs_source != true",
                "Identify the causing creature for a source-qualified condition defense.",
            ),
        ]
        next(e for e in save["effects"] if e.get("call") == "d20_test")["params"]["advantage"] = (
            "{_source_condition_save_advantage}"
        )
    changed["resolve_srd_condition_saving_throw"] = save

    process = deepcopy(tools["process_srd_turn_effects"])
    if "start_srd_timed_actor_defense_turn" not in str(process["effects"]):
        process["effects"].append(
            _when(
                "boundary_phase == 'start' and boundary_actor != ''",
                [_call("start_srd_timed_actor_defense_turn", {"target_state": "{boundary_actor}"})],
            )
        )
    changed["process_srd_turn_effects"] = process

    equipment = deepcopy(tools["change_srd_equipment"])
    if "retire_srd_armored_actor_defenses" not in str(equipment["effects"]):
        equipment["effects"].append(
            _call("retire_srd_armored_actor_defenses", {"target_state": "{_actor_state_id}"})
        )
    changed["change_srd_equipment"] = equipment

    def gate_consumer(tool, helper, predicate, following=0):
        effects = tool["effects"]
        for index, effect in enumerate(effects):
            if effect.get("call") == helper:
                guarded = effects[index : index + following + 1]
                effects[index : index + following + 1] = [_when(predicate, guarded)]
                return

    gate_consumer(
        attack,
        "resolve_srd_timed_actor_attack_defenses",
        "target_state.has_timed_actor_defenses == true",
        1,
    )
    gate_consumer(
        ac, "resolve_srd_timed_actor_defenses", "target_state.has_timed_actor_defenses == true", 1
    )
    gate_consumer(
        damage, "resolve_srd_timed_actor_defenses", "target.has_timed_actor_defenses == true", 1
    )
    gate_consumer(
        condition,
        "check_srd_target_condition_immunity",
        "target.has_timed_actor_defenses == true",
        2,
    )
    gate_consumer(
        save, "check_srd_target_condition_immunity", "target.has_timed_actor_defenses == true", 1
    )
    gate_consumer(
        composite, "resolve_srd_timed_actor_defenses", "target.has_timed_actor_defenses == true"
    )
    for component_effect in composite["effects"]:
        clause = component_effect.get("condition", "")
        if "in _actor_defense_damage_immunities" in clause and not clause.startswith("target."):
            component_effect["condition"] = "target.has_timed_actor_defenses == true and " + clause

    # Ordinary damage pays the existing immunity check once; active defenses retain both consumers.
    for index, effect in enumerate(damage["effects"][:-1]):
        if effect.get("condition") == "target.has_timed_actor_defenses == true":
            following = damage["effects"][index + 1]
            if following.get("condition") == "damage_type in target.damage_immunities":
                damage["effects"][index : index + 2] = [
                    _when(
                        "target.has_timed_actor_defenses == true or "
                        "damage_type in target.damage_immunities",
                        [effect, following],
                    )
                ]
                break

    for name, tool in changed.items():
        rendered = yaml.dump({name: tool}, Dumper=_RuntimeDumper, sort_keys=False, width=105)
        rendered = "".join(
            "  " + line if line.strip() else line for line in rendered.splitlines(True)
        )
        if name == "cast_spell":
            spell_start = rendered.index("    - name: spell\n")
            spell_end = rendered.index("    - name:", spell_start + 1)
            spell_parameter = rendered[spell_start:spell_end]
            enum_start = spell_parameter.index("      enum_values:\n") + len("      enum_values:\n")
            enum_end = spell_parameter.find("      required:", enum_start)
            spell_parameter = (
                spell_parameter[:enum_start]
                + "        # BEGIN GENERATED SPELL SELECTOR\n"
                + "".join(
                    f"          - {row}\n"
                    for row in next(p for p in cast["parameters"] if p["name"] == "spell")[
                        "enum_values"
                    ]
                )
                + "        # END GENERATED SPELL SELECTOR\n"
                + spell_parameter[enum_end:]
            )
            rendered = rendered[:spell_start] + spell_parameter + rendered[spell_end:]
        pattern = (
            rf"(?ms)^  {re.escape(name)}:\n.*?"
            r"(?=^  [A-Za-z_][^\n]*:\s*|^procedures:|^definition_row_schemas:)"
        )
        if re.search(pattern, text):
            # Keep the public alias sharing the exact same authoritative casting implementation.
            if name == "cast_spell":
                rendered = rendered.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
                text = re.sub(
                    r"(?ms)^  cast_spell: &srd_cast_spell\n.*?(?=^  [A-Za-z_][^\n]*:\s*)",
                    lambda _, replacement=rendered: replacement,
                    text,
                    count=1,
                )
            else:
                text = re.sub(pattern, lambda _, replacement=rendered: replacement, text, count=1)
        elif name == "cast_spell":
            rendered = rendered.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
            text = re.sub(
                r"(?ms)^  cast_spell: &srd_cast_spell\n.*?(?=^  [A-Za-z_][^\n]*:\s*)",
                lambda _, replacement=rendered: replacement,
                text,
                count=1,
            )
        else:
            text = text.replace("procedures: {}\n", rendered + "\nprocedures: {}\n", 1)
    schema = _defense_schema()
    rendered_schema = yaml.dump(
        {"timed_actor_defense": schema},
        Dumper=_RuntimeDumper,
        sort_keys=False,
    )
    rendered_schema = "".join("      " + line for line in rendered_schema.splitlines(True))
    marker = "      single_target_timed_armor_class_floor:"
    if "      timed_actor_defense:" not in text:
        text = text.replace(marker, rendered_schema + "\n" + marker, 1)
    return text
