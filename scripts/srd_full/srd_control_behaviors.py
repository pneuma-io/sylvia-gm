"""SRD control spells reuse durable condition sources and consume real turn budgets."""

import re
from copy import deepcopy

import yaml


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _set(field, value):
    return {"set": {"field": field, "value": value}}


def _when(condition, effects, otherwise=None):
    result = {"condition": condition, "on_true": effects}
    if otherwise is not None:
        result["on_false"] = otherwise
    return result


def _require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def _call(name, params, exports=()):
    if not exports:
        exports = {
            "check_srd_target_condition_immunity": (
                "_source_condition_immune",
                "_source_condition_save_advantage",
                "_source_condition_needs_source",
            ),
            "d20_test": ("_d20_test", "_outcome"),
            "install_srd_timed_condition_source": ("_timed_condition_source_id",),
            "start_srd_condition_concentration": ("_condition_concentration_sequence",),
            "resolve_srd_control_behavior_save": (
                "_control_save_roll",
                "_control_save_outcome",
                "_control_save_immune",
                "_control_save_label",
                "_control_save_detail",
            ),
        }.get(name, ())
    result = {"call": name, "params": params}
    if exports:
        result["export_context"] = {key: key for key in exports}
    return result


def _param(name, kind="string", **extra):
    return {"name": name, "type": kind, "required": True, **extra}


def _entity(name):
    return _param(name, resolve="entity")


def _lasting():
    return _param("lasting_state", resolve="entity", required=False, default="state")


def _each(collection, name, effects):
    return {"for_each": {"collection": collection, "as": name, "do": effects}}


def _push(collection, value):
    return {"list_push": {"list": collection, "value": value}}


def _tool(description, parameters, effects, preconditions=(), *, public=False):
    return {
        "description": description,
        "parameters": parameters,
        "pre_conditions": list(preconditions),
        "effects": effects,
        "metadata": {
            "category": "combat" if public else "internal",
            "visible_to_player": public,
            "costs_action": False,
        },
    }


CONTROL_PROFILES = {
    "sleep": (1, "distance", 60, False, 5, 0, 8, "sleep", "incapacitated"),
    "fear": (3, "self", 0, False, 30, 0, 8, "fear", "frightened"),
    "confusion": (4, "distance", 90, False, 10, 5, 8, "confusion", "none"),
    "enthrall": (2, "distance", 60, True, 0, 0, 8, "enthrall", "none"),
    "irresistible_dance": (6, "distance", 30, True, 0, 0, 1, "dance", "charmed"),
}


def validate_definition(spell, definition):
    keys = (
        "base_level",
        "range_kind",
        "range_feet",
        "requires_sight",
        "area_feet",
        "area_per_slot_level",
        "base_targets",
        "behavior",
        "condition",
    )
    if (
        spell not in CONTROL_PROFILES
        or tuple(definition.get(key) for key in keys) != CONTROL_PROFILES[spell]
        or definition.get("casting_time") != "action"
        or definition.get("duration_rounds") != 10
        or definition.get("concentration") is not True
        or definition.get("save_ability") != "wisdom"
    ):
        raise SystemExit(f"Incoherent control behavior definition: {spell}")


def _schema():
    properties = {
        "casting_time": {"type": "string", "enum": ["action"]},
        "base_level": {"type": "integer", "minimum": 1, "maximum": 9},
        "range_kind": {"type": "string", "enum": ["self", "distance"]},
        "range_feet": {"type": "integer", "minimum": 0, "maximum": 90},
        "requires_sight": {"type": "boolean"},
        "area_feet": {"type": "integer", "minimum": 0, "maximum": 30},
        "area_per_slot_level": {"type": "integer", "minimum": 0, "maximum": 5},
        "base_targets": {"type": "integer", "minimum": 1, "maximum": 8},
        "behavior": {"type": "string", "enum": ["sleep", "fear", "confusion", "enthrall", "dance"]},
        "condition": {"type": "string", "enum": ["none", "incapacitated", "frightened", "charmed"]},
        "duration_rounds": {"type": "integer", "enum": [10]},
        "concentration": {"type": "boolean", "enum": [True]},
        "save_ability": {"type": "string", "enum": ["wisdom"]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _source_loop(effects):
    return [
        _set("_control_source_ids", []),
        _when(
            "target_state.control_behavior_source_ids != null",
            [
                _set("_control_source_ids", "target_state.control_behavior_source_ids"),
            ],
        ),
        _each(
            "_control_source_ids",
            "_control_source_id",
            [
                _set(
                    "_control_source", "lasting_state.timed_condition_sources[_control_source_id]"
                ),
                *effects,
            ],
        ),
    ]


def _tools():
    tools = {}
    tools["resolve_srd_control_behavior_save"] = _tool(
        "Roll the Wisdom save, keeping spell-specific automatic successes distinct from immunity.",
        [
            _entity("target_state"),
            _param("behavior"),
            _param("condition"),
            _param("save_dc", "integer"),
            _param("source_state_id"),
            _param("automatic_success", "boolean", required=False, default=False),
            _param("forego", "boolean", required=False, default=False),
        ],
        [
            _set("_control_save_immune", False),
            _set("_control_save_advantage", False),
            _set("_control_save_roll", None),
            _set("_control_save_outcome", "'success'"),
            _set("_control_save_label", "target_state.name"),
            _set("_control_save_detail", "'Automatic success. No roll.'"),
            _when(
                "condition != 'none'",
                [
                    _call(
                        "check_srd_target_condition_immunity",
                        {
                            "target_state": "{_target_state_id}",
                            "condition": "{condition}",
                            "source_state_id": "{source_state_id}",
                        },
                    ),
                    _set("_control_save_immune", "_source_condition_immune"),
                    _set("_control_save_advantage", "_source_condition_save_advantage"),
                ],
            ),
            _when(
                "automatic_success != true and not (behavior == 'sleep' and "
                "(target_state.does_not_sleep == true or "
                "(target_state.condition_immunities != null "
                "and 'exhaustion' in target_state.condition_immunities)))",
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
                            "target_number": "{save_dc}",
                            "advantage": "{_control_save_advantage}",
                            "disadvantage": False,
                            "forego_save": "{forego}",
                            "save_avoids_condition": "none",
                        },
                        ("_d20_test", "_outcome", "_adjusted_total", "_combined_modifier"),
                    ),
                    _set("_control_save_roll", "_d20_test"),
                    _set("_control_save_outcome", "_outcome"),
                    _set("_control_save_detail", "'Save foregone; failure. No roll.'"),
                    _when(
                        "_d20_test != null",
                        [
                            _call(
                                "record_srd_end_turn_save",
                                {
                                    "label": "{target_state.name}",
                                    "value": "Wisdom save: dice {_d20_test.results}; selected "
                                    "{_d20_test.total} + "
                                    "({_combined_modifier}) = {_adjusted_total}; DC "
                                    "{save_dc}; {_outcome}.",
                                },
                                ("_recorded_save_row",),
                            ),
                            _set("_control_save_detail", "_recorded_save_row.value"),
                        ],
                    ),
                ],
            ),
        ],
    )
    tools["install_srd_control_behavior_source"] = _tool(
        "Install one bounded source and save its stage and target-turn deadline.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("spell"),
            _param("condition"),
            _param("behavior"),
            _param("save_dc", "integer"),
            _param("concentration_sequence", "integer"),
        ],
        [
            *[
                _when(
                    f"target_state.timed_condition_unowned_{condition} == null",
                    [
                        _set(
                            f"target_state.timed_condition_unowned_{condition}",
                            f"'{condition}' in target_state.conditions",
                        ),
                    ],
                )
                for condition in ("frightened", "unconscious")
            ],
            _call(
                "install_srd_timed_condition_source",
                {
                    "target_state": "{_target_state_id}",
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "spell": "{spell}",
                    "condition": "{condition}",
                    "save_dc": "{save_dc}",
                    "duration_rounds": 10,
                    "repeat_save_timing": "none",
                    "save_ability": "wisdom",
                    "control_profile": "{behavior}",
                    "concentration_sequence": "{concentration_sequence}",
                },
            ),
            _set("_control_end_turn", 1),
            _when(
                "target_state.turns_started != null",
                [
                    _set("_control_end_turn", "target_state.turns_started + 1"),
                ],
            ),
            _set(
                "lasting_state.timed_condition_sources.{_timed_condition_source_id}.next_end_turn",
                "_control_end_turn",
            ),
            _set(
                "lasting_state.timed_condition_sources.{_timed_condition_source_id}.stage",
                "'first'",
            ),
            _set(
                "lasting_state.timed_condition_sources.{_timed_condition_source_id}.caster_visible",
                None,
            ),
            _set(
                "lasting_state.timed_condition_sources.{_timed_condition_source_id}.last_end_turn",
                -1,
            ),
            _when(
                "behavior == 'dance_brief'",
                [
                    _set(
                        "lasting_state.timed_condition_sources.{_timed_conditio"
                        "n_source_id}.expires_tick",
                        "lasting_state.lasting_tick + 4",
                    ),
                ],
            ),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
        ],
    )
    tools["cast_srd_control_behavior"] = _tool(
        "Resolve Sleep, Fear, Confusion, Enthrall and "
        "Irresistible Dance from fixed spell profiles.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("spell"),
            _param("slot_level", "integer"),
            _param("save_dc", "integer"),
            _param("concentration_save_modifier", "integer"),
            _param(
                "targets",
                "entity_id_list",
                resolve="entity_list",
                min_items=1,
                max_items=64,
                unique_items=True,
            ),
            _param("targets_max_distance", "integer"),
            _param("caster_can_see_all_targets", "boolean"),
            _param("all_targets_have_clear_path", "boolean"),
            _param(
                "forego_save_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=8,
                unique_items=True,
            ),
            _param(
                "fighting_caster_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=8,
                unique_items=True,
            ),
            _param(
                "sleepless_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=8,
                unique_items=True,
            ),
        ],
        [
            _set("_control_definition", "definitions.spells[spell]"),
            _require(
                "len(targets) <= _control_definition.base_targets",
                "Too many control spell targets.",
            ),
            _require(
                "(_control_definition.range_kind == 'self' and targets_max_distance <= "
                "_control_definition.area_feet) or (_control_definition.range_kind == 'distance' "
                "and targets_max_distance <= _control_definition.range_feet)",
                "The selected control targets exceed the spell's stated range.",
            ),
            _require(
                "_control_definition.requires_sight != true or caster_can_see_all_targets == true",
                "This control spell requires caster sight of its selected targets.",
            ),
            _require("all_targets_have_clear_path == true", "A clear spell path is required."),
            _each(
                "sleepless_targets",
                "_control_fact_target",
                [
                    _require(
                        "_control_fact_target in targets",
                        "A sleepless fact must name a selected target.",
                    ),
                ],
            ),
            _each(
                "fighting_caster_targets",
                "_control_fact_target",
                [
                    _require(
                        "_control_fact_target in targets",
                        "A fighting fact must name a selected target.",
                    ),
                ],
            ),
            _each(
                "targets",
                "_control_target",
                [
                    _call(
                        "validate_srd_timed_condition_target",
                        {
                            "target_state": "{_control_target}",
                            "save_ability": "wisdom",
                            "required_creature_type": "any",
                        },
                    ),
                ],
            ),
            _call(
                "start_srd_condition_concentration",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "targets": "{targets}",
                    "spell": "{spell}",
                    "duration_rounds": 10,
                    "save_modifier": "{concentration_save_modifier}",
                },
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _set("_control_save_rolls", []),
            _set("_control_save_outcomes", []),
            _set(
                "_control_area_feet",
                "_control_definition.area_feet + max(0, slot_level - "
                "_control_definition.base_level) * _control_definition.area_per_slot_level",
            ),
            _each(
                "targets",
                "_control_target",
                [
                    _set(
                        "_control_auto_success",
                        "(_control_definition.behavior == 'sleep' and "
                        "_control_target in sleepless_targets) or (_control_definition.behavior == "
                        "'enthrall' and _control_target in fighting_caster_targets)",
                    ),
                    _set("_control_forego", "_control_target in forego_save_targets"),
                    _call(
                        "resolve_srd_control_behavior_save",
                        {
                            "target_state": "{_control_target}",
                            "source_state_id": "{_caster_state_id}",
                            "behavior": "{_control_definition.behavior}",
                            "condition": "{_control_definition.condition}",
                            "save_dc": "{save_dc}",
                            "automatic_success": "{_control_auto_success}",
                            "forego": "{_control_forego}",
                        },
                    ),
                    _when(
                        "_control_save_roll != null",
                        [
                            _push("_control_save_rolls", "_control_save_roll"),
                        ],
                        [_push("_control_save_rolls", None)],
                    ),
                    _push("_control_save_outcomes", "_control_save_outcome"),
                    _set("_control_installed_behavior", "_control_definition.behavior"),
                    _set("_control_installed_condition", "_control_definition.condition"),
                    _set(
                        "_control_install",
                        "_control_save_outcome == 'failure' and _control_save_immune != true",
                    ),
                    _when(
                        "_control_definition.behavior == 'fear' and "
                        "_control_save_outcome == 'failure'",
                        [
                            _call(
                                "drop_srd_control_held_items", {"target_state": "{_control_target}"}
                            ),
                        ],
                    ),
                    _when(
                        "_control_definition.behavior == 'dance' and "
                        "_control_save_outcome == 'success'",
                        [
                            _set("_control_install", True),
                            _set("_control_installed_behavior", "'dance_brief'"),
                            _set("_control_installed_condition", "'none'"),
                        ],
                    ),
                    _when(
                        "_control_install == true",
                        [
                            _call(
                                "install_srd_control_behavior_source",
                                {
                                    "target_state": "{_control_target}",
                                    "caster_state": "{_caster_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "spell": "{spell}",
                                    "condition": "{_control_installed_condition}",
                                    "behavior": "{_control_installed_behavior}",
                                    "save_dc": "{save_dc}",
                                    "concentration_sequence": "{_condition_concentration_sequence}",
                                },
                            ),
                        ],
                    ),
                    _push(
                        "_spell_summary_rows",
                        {"label": "_control_save_label", "value": "_control_save_outcome"},
                    ),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "_control_save_label",
                            "value": "_control_save_detail",
                        },
                    ),
                ],
            ),
        ],
    )
    tools["drop_srd_control_held_items"] = _tool(
        "Drop saved held objects, preserving a durable "
        "ground-item record and disabling their attacks.",
        [_entity("target_state")],
        [
            _set("_control_held_items", []),
            _when(
                "target_state.held_items != null",
                [
                    _set("_control_held_items", "target_state.held_items"),
                ],
            ),
            _set("_control_dropped_items", []),
            _when(
                "target_state.dropped_items != null",
                [
                    _set("_control_dropped_items", "target_state.dropped_items"),
                ],
            ),
            _each(
                "_control_held_items",
                "_control_held_item",
                [
                    _push("_control_dropped_items", "_control_held_item"),
                ],
            ),
            _set("target_state.dropped_items", "_control_dropped_items"),
            _set("_control_inventory", []),
            _set("_control_kept_inventory", []),
            _when(
                "target_state.inventory != null",
                [
                    _set("_control_inventory", "target_state.inventory"),
                ],
            ),
            _each(
                "_control_inventory",
                "_control_inventory_item",
                [
                    _when(
                        "_control_inventory_item not in _control_held_items",
                        [
                            _push("_control_kept_inventory", "_control_inventory_item"),
                        ],
                    ),
                ],
            ),
            _set("target_state.inventory", "_control_kept_inventory"),
            _when(
                "target_state.held_attack_names != null",
                [
                    _set(
                        "target_state.control_dropped_attack_names",
                        "target_state.held_attack_names",
                    ),
                ],
            ),
            _set("target_state.held_attack_names", []),
            _set("target_state.held_items", []),
            _set("target_state.control_held_items_dropped", "len(_control_held_items) > 0"),
        ],
    )
    tools["resolve_srd_control_behavior_conditions"] = _tool(
        "Select newest same-spell control sources and project "
        "their owned conditions before suppression.",
        [_entity("target_state"), _lasting()],
        [
            _set("_control_index", []),
            _set("_control_spells", []),
            _set("target_state.control_behavior_by_spell", {}),
            _set("target_state.control_behavior_source_ids", []),
            _set("_control_selected_ids", []),
            _set("target_state.has_control_behavior_sources", False),
            _when(
                "target_state.timed_condition_source_ids != null",
                [
                    _set("_control_index", "target_state.timed_condition_source_ids"),
                ],
            ),
            _each(
                "_control_index",
                "_control_index_id",
                [
                    _set(
                        "_control_index_source",
                        "lasting_state.timed_condition_sources[_control_index_id]",
                    ),
                    _when(
                        "_control_index_source.installed == true and "
                        "(_control_index_source.control_profile == 'sleep' or "
                        "_control_index_source.control_profile == 'fear' or "
                        "_control_index_source.control_profile == 'confusion' or "
                        "_control_index_source.control_profile == 'enthrall' or "
                        "_control_index_source.control_profile == 'dance' or "
                        "_control_index_source.control_profile == 'dance_brief')",
                        [
                            _set("_control_newest", True),
                            _when(
                                "_control_index_source.spell_id in _control_spells",
                                [
                                    _set(
                                        "_control_previous",
                                        "target_state.control_behavior_by_spell[_control_index_"
                                        "source.spell_id]",
                                    ),
                                    _set(
                                        "_control_previous_source",
                                        "lasting_state.timed_condition_sources[_control_previous]",
                                    ),
                                    _set(
                                        "_control_newest",
                                        "_control_index_source.sequence > "
                                        "_control_previous_source.sequence",
                                    ),
                                ],
                                [_push("_control_spells", "_control_index_source.spell_id")],
                            ),
                            _when(
                                "_control_newest == true",
                                [
                                    _set(
                                        "target_state.control_behavior_by_spell.{_control_index"
                                        "_source.spell_id}",
                                        "_control_index_id",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _each(
                "_control_spells",
                "_control_spell",
                [
                    _set(
                        "_control_selected_id",
                        "target_state.control_behavior_by_spell[_control_spell]",
                    ),
                    _push("_control_selected_ids", "_control_selected_id"),
                ],
            ),
            _set("target_state.control_behavior_source_ids", "_control_selected_ids"),
            _set("target_state.has_control_behavior_sources", "len(_control_spells) > 0"),
            _set("_control_frightened", False),
            _set("_control_unconscious", False),
            *_source_loop(
                [
                    _when(
                        "_control_source.control_profile == 'fear'",
                        [_set("_control_frightened", True)],
                    ),
                    _when(
                        "_control_source.control_profile == 'sleep' and "
                        "_control_source.stage == 'sleeping'",
                        [
                            _set("_control_unconscious", True),
                        ],
                    ),
                ]
            ),
            *[
                _when(
                    f"target_state.timed_condition_unowned_{condition} != null",
                    [
                        {"remove_condition": {"target": "target_state", "condition": condition}},
                        _when(
                            f"target_state.timed_condition_unowned_{condition} == true or "
                            f"_control_{condition} == true",
                            [
                                {
                                    "apply_condition": {
                                        "target": "target_state",
                                        "condition": condition,
                                    }
                                },
                            ],
                        ),
                    ],
                )
                for condition in ("frightened", "unconscious")
            ],
        ],
    )
    tools["resolve_srd_control_behavior_projection"] = _tool(
        "Project active control consumers after condition "
        "suppression, preserving independent sources.",
        [_entity("target_state"), _lasting()],
        [
            *[
                _set("target_state." + field, value)
                for field, value in (
                    ("control_fear_active", False),
                    ("control_confusion_active", False),
                    ("control_dance_active", False),
                    ("control_dancing", False),
                    ("control_enthrall_penalty", 0),
                )
            ],
            *_source_loop(
                [
                    _when(
                        "_control_source.suppressed != true",
                        [
                            _when(
                                "_control_source.control_profile == 'fear' and "
                                "'frightened' in target_state.conditions",
                                [
                                    _set("target_state.control_fear_active", True),
                                ],
                            ),
                            _when(
                                "_control_source.control_profile == 'confusion'",
                                [
                                    _set("target_state.control_confusion_active", True),
                                ],
                            ),
                            _when(
                                "_control_source.control_profile == 'dance' and "
                                "'charmed' in target_state.conditions",
                                [
                                    _set("target_state.control_dance_active", True),
                                    _set("target_state.control_dancing", True),
                                ],
                            ),
                            _when(
                                "_control_source.control_profile == 'dance_brief'",
                                [
                                    _set("target_state.control_dancing", True),
                                ],
                            ),
                            _when(
                                "_control_source.control_profile == 'enthrall'",
                                [
                                    _set("target_state.control_enthrall_penalty", 10),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
            _when(
                "target_state.control_confusion_active != true",
                [
                    _set("target_state.control_confusion_mode", "'free'"),
                    _set("target_state.control_confusion_pending", False),
                ],
            ),
            _when(
                "target_state.control_confusion_active == true",
                [
                    _when(
                        "target_state.control_confusion_mode == null",
                        [
                            _set("target_state.control_confusion_mode", "'free'"),
                        ],
                    ),
                    _set("target_state.bonus_action_available", False),
                    _set("target_state.reaction_available", False),
                ],
            ),
            _when(
                "target_state.control_fear_active != true",
                [
                    _set("target_state.control_fear_pending", False),
                ],
            ),
            _when(
                "target_state.timed_control_speed_zero == true",
                [
                    _set("_control_hypnotic_active", False),
                    _set(
                        "_control_other_incapacitated",
                        "target_state.timed_condition_unowned_incapacitated == true",
                    ),
                    _set("_control_original_index", []),
                    _when(
                        "target_state.timed_condition_source_ids != null",
                        [
                            _set(
                                "_control_original_index", "target_state.timed_condition_source_ids"
                            ),
                        ],
                    ),
                    _each(
                        "_control_original_index",
                        "_control_original_id",
                        [
                            _set(
                                "_control_original",
                                "lasting_state.timed_condition_sources[_control_original_id]",
                            ),
                            _when(
                                "_control_original.installed == true and "
                                "_control_original.suppressed != true "
                                "and _control_original.control_profile == 'hypnotic' "
                                "and 'charmed' in target_state.conditions",
                                [
                                    _set("_control_hypnotic_active", True),
                                ],
                            ),
                            _when(
                                "_control_original.installed == true and "
                                "_control_original.suppressed != true and "
                                "(_control_original.condition == 'incapacitated' or "
                                "_control_original.control_profile == 'laughter')",
                                [
                                    _set("_control_other_incapacitated", True),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_control_hypnotic_active != true and _control_other_incapacitated != true",
                        [
                            {
                                "remove_condition": {
                                    "target": "target_state",
                                    "condition": "incapacitated",
                                }
                            },
                        ],
                    ),
                    _set("target_state.timed_control_speed_zero", "_control_hypnotic_active"),
                ],
            ),
            _call("refresh_srd_movement_budget", {"actor_state": "{_target_state_id}"}),
        ],
    )
    # Suppressing a newer source must expose an older unsuppressed copy of the same spell.
    conditions = tools["resolve_srd_control_behavior_conditions"]
    split = next(
        index
        for index, effect in enumerate(conditions["effects"])
        if effect.get("set", {}).get("field") == "_control_frightened"
    )
    selection = deepcopy(conditions["effects"][:split])
    for effect in _walk(selection):
        if effect.get("condition", "").startswith("_control_index_source.installed == true"):
            effect["condition"] += (
                " and (include_suppressed == true or _control_index_source.suppressed != true)"
            )
    tools["resolve_srd_control_behavior_sources"] = _tool(
        "Choose the newest available source per spell without retiring overlapping lifetimes.",
        [_entity("target_state"), _lasting(), _param("include_suppressed", "boolean")],
        selection,
    )
    conditions["effects"] = [
        _call(
            "resolve_srd_control_behavior_sources",
            {
                "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
                "include_suppressed": True,
            },
        ),
        *conditions["effects"][split:],
    ]
    tools["resolve_srd_control_behavior_projection"]["effects"][:0] = [
        _set("_control_retained_sources", "target_state.has_control_behavior_sources == true"),
        _call(
            "resolve_srd_control_behavior_sources",
            {
                "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
                "include_suppressed": False,
            },
        ),
        _set("target_state.has_control_behavior_sources", "_control_retained_sources"),
    ]
    tools.update(_turn_tools())
    tools.update(_budget_tools())
    return tools


def _turn_tools():
    tools = {}
    tools["start_srd_control_behavior_turn"] = _tool(
        "Roll Confusion behavior once at turn start and require"
        " the applicable route/target resolution.",
        [_entity("target_state"), _lasting()],
        [
            _set("_control_turn_rows", []),
            _when(
                "target_state.control_dancing == true",
                [
                    _set("combat.movement_spent", "combat.movement_remaining"),
                    _set("combat.movement_remaining", 0),
                ],
            ),
            _when(
                "target_state.control_fear_active == true",
                [
                    _set("target_state.control_fear_pending", True),
                    _set("target_state.control_fear_facts_known", False),
                    _push(
                        "_control_turn_rows",
                        {"label": "'Fear'", "value": "'Resolve safest route and source sight.'"},
                    ),
                ],
            ),
            _when(
                "target_state.control_confusion_active == true",
                [
                    _set("target_state.bonus_action_available", False),
                    _set("target_state.reaction_available", False),
                    {"dice": {"expression": "1d10"}, "store_as": "_control_behavior_roll"},
                    _set("target_state.control_confusion_roll", "_control_behavior_roll.total"),
                    _set("target_state.control_confusion_mode", "'free'"),
                    _set("target_state.control_confusion_pending", False),
                    _set("target_state.control_confusion_target", None),
                    _set("target_state.control_confusion_attack_done", False),
                    _push(
                        "_control_turn_rows",
                        {"label": "'Confusion d10'", "value": "_control_behavior_roll.total"},
                    ),
                    _when(
                        "_control_behavior_roll.total == 1",
                        [
                            _set("target_state.control_confusion_mode", "'move'"),
                            _set("target_state.control_confusion_pending", True),
                            {"dice": {"expression": "1d4"}, "store_as": "_control_direction_roll"},
                            _set(
                                "target_state.control_confusion_direction",
                                "_control_direction_roll.total",
                            ),
                            _push(
                                "_control_turn_rows",
                                {
                                    "label": "'Direction: 1 north, 2 east, 3 south, 4 west'",
                                    "value": "_control_direction_roll.total",
                                },
                            ),
                        ],
                    ),
                    _when(
                        "_control_behavior_roll.total >= 2 and _control_behavior_roll.total <= 6",
                        [
                            _set("target_state.control_confusion_mode", "'idle'"),
                            _set("combat.movement_remaining", 0),
                        ],
                    ),
                    _when(
                        "_control_behavior_roll.total == 7 or _control_behavior_roll.total == 8",
                        [
                            _set("target_state.control_confusion_mode", "'attack'"),
                            _set("target_state.control_confusion_pending", True),
                            _set("combat.movement_remaining", 0),
                        ],
                    ),
                ],
            ),
            _call("refresh_srd_movement_budget", {"actor_state": "{_target_state_id}"}),
        ],
    )
    tools["check_srd_control_action"] = _tool(
        "Reject actions that violate the saved Fear/Confusion "
        "turn, including substituted Magic actions.",
        [_entity("actor_state"), _param("action_kind")],
        [
            _require(
                "actor_state.control_fear_pending != true", "Resolve the required Fear route first."
            ),
            _require(
                "actor_state.control_confusion_pending != true",
                "Resolve the rolled Confusion behavior first.",
            ),
            _require(
                "actor_state.control_fear_active != true or "
                "actor_state.control_fear_can_flee != true "
                "or action_kind == 'fear_dash'",
                "Fear requires the Dash action and flight by the safest route.",
            ),
            _require(
                "actor_state.control_confusion_active != true or "
                "actor_state.control_confusion_mode == 'free' "
                "or (actor_state.control_confusion_mode == 'attack' and action_kind == 'attack')",
                "The rolled Confusion behavior forbids this action.",
            ),
            _require(
                "actor_state.control_confusion_active != true or "
                "actor_state.control_confusion_mode != 'attack' "
                "or actor_state.control_confusion_attack_done != true",
                "Confusion permits only one melee attack.",
            ),
        ],
    )
    tools["resolve_srd_confused_turn"] = _tool(
        "Resolve the rolled direction or randomly choose "
        "exactly one creature currently within melee reach.",
        [
            _entity("actor_state"),
            _param(
                "melee_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=8,
                unique_items=True,
            ),
            _param("movement_feet", "integer", required=False, default=0, minimum=0, maximum=1000),
            _param("path_direction", "integer", required=False, default=0, minimum=0, maximum=4),
        ],
        [
            _require(
                "actor_state.control_confusion_pending == true",
                "There is no pending Confusion behavior.",
            ),
            _when(
                "actor_state.control_confusion_mode == 'move'",
                [
                    _require(
                        "path_direction == actor_state.control_confusion_direction",
                        "Confusion movement must follow its rolled direction: 1"
                        " north, 2 east, 3 south, 4 west.",
                    ),
                    _require(
                        "movement_feet <= combat.movement_remaining",
                        "The declared route exceeds available movement.",
                    ),
                    _set("actor_state.control_confusion_movement", "movement_feet"),
                    _set(
                        "combat.movement_spent", "combat.movement_spent + combat.movement_remaining"
                    ),
                    _set("combat.movement_remaining", 0),
                    _set("actor_state.control_confusion_pending", False),
                ],
            ),
            _when(
                "actor_state.control_confusion_mode == 'attack'",
                [
                    _require(
                        "_actor_state_id not in melee_targets",
                        "The random melee target must be another creature.",
                    ),
                    _set("_control_target_count", "len(melee_targets)"),
                    _when(
                        "_control_target_count > 0",
                        [
                            {
                                "dice": {"expression": "1d{_control_target_count}"},
                                "store_as": "_control_target_roll",
                            },
                            _set("_control_target_index", "_control_target_roll.total - 1"),
                            _set(
                                "actor_state.control_confusion_target",
                                "melee_targets[_control_target_index]",
                            ),
                        ],
                        [
                            _set("combat.action_available", False),
                            _set("combat.attack_available", False),
                        ],
                    ),
                    _set("actor_state.control_confusion_pending", False),
                ],
            ),
        ],
        [
            {
                "condition": "combat.turn_active == true and combat.active_actor == "
                "_actor_state_id",
                "error": "Resolve only the affected creature's current turn.",
            }
        ],
        public=True,
    )
    tools["resolve_srd_fear_turn"] = _tool(
        "Record current Fear source visibility and spend its "
        "mandatory Dash and away movement, or record no safe "
        "route.",
        [
            _entity("actor_state"),
            _lasting(),
            _param("can_flee", "boolean"),
            _param("safe_route", "boolean"),
            _param("moves_away", "boolean"),
            _param("flee_distance", "integer", minimum=0, maximum=1000),
            _param(
                "visible_fear_casters",
                "entity_id_list",
                resolve="entity_list",
                default=[],
                required=False,
                max_items=8,
                unique_items=True,
            ),
        ],
        [
            _require(
                "actor_state.control_fear_pending == true",
                "The current Fear turn has already been resolved.",
            ),
            _require(
                "can_flee != true or (safe_route == true and moves_away"
                " == true and flee_distance > 0)",
                "Fear movement requires a safe route away from its source.",
            ),
            _require(
                "can_flee == true or flee_distance == 0",
                "No-route Fear resolution cannot invent movement.",
            ),
            _set("_control_fear_ids", "actor_state.control_behavior_source_ids"),
            _set("_control_fear_casters", []),
            _each(
                "_control_fear_ids",
                "_control_fear_id",
                [
                    _set(
                        "_control_fear_source",
                        "lasting_state.timed_condition_sources[_control_fear_id]",
                    ),
                    _when(
                        "_control_fear_source.control_profile == 'fear'",
                        [
                            _push("_control_fear_casters", "_control_fear_source.caster_state_id"),
                            _set(
                                "lasting_state.timed_condition_sources.{_control_fear_i"
                                "d}.caster_visible",
                                "_control_fear_source.caster_state_id in visible_fear_casters",
                            ),
                        ],
                    ),
                ],
            ),
            _each(
                "visible_fear_casters",
                "_control_visible_caster",
                [
                    _require(
                        "_control_visible_caster in _control_fear_casters",
                        "Fear sight facts must name an active Fear source's caster.",
                    ),
                ],
            ),
            _set("actor_state.control_fear_visible", "len(visible_fear_casters) > 0"),
            _set("actor_state.control_fear_facts_known", True),
            _set("actor_state.control_fear_can_flee", "can_flee"),
            _set("actor_state.control_fear_pending", False),
            _when(
                "can_flee == true",
                [
                    _call(
                        "spend_srd_action",
                        {"magic_action": False, "control_action_kind": "fear_dash"},
                    ),
                    _call("apply_srd_dash", {"actor_state": "{_actor_state_id}"}),
                    _require(
                        "flee_distance <= combat.movement_remaining",
                        "The safe route exceeds the Dash movement allowance.",
                    ),
                    _set("combat.movement_spent", "combat.movement_spent + flee_distance"),
                    _set("combat.movement_remaining", "combat.movement_remaining - flee_distance"),
                    _set("actor_state.control_fear_distance", "flee_distance"),
                ],
            ),
        ],
        [
            {
                "condition": "combat.turn_active == true and combat.active_actor == "
                "_actor_state_id "
                "and actor_state.control_fear_active == true",
                "error": "Fear flight requires the affected creature's active turn.",
            }
        ],
        public=True,
    )
    tools["end_srd_control_behavior_turn"] = _tool(
        "Resolve source-owned Sleep transition, conditional "
        "Fear saves, Confusion saves and brief Dance expiry "
        "once.",
        [_entity("target_state"), _lasting()],
        [
            _set("_control_end_rows", []),
            _require(
                "target_state.control_confusion_pending != true",
                "Resolve the pending Confusion behavior before ending the turn.",
            ),
            _require(
                "target_state.control_fear_pending != true",
                "Resolve the pending Fear flight before ending the turn.",
            ),
            _require(
                "target_state.control_confusion_active != true or "
                "target_state.control_confusion_mode != 'attack' "
                "or target_state.control_confusion_target == null or "
                "target_state.control_confusion_attack_done == true",
                "Make Confusion's one chosen melee attack before ending the turn.",
            ),
            *_source_loop(
                [
                    _when(
                        "_control_source.last_end_turn != target_state.turns_started",
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_control_source"
                                "_id}.last_end_turn",
                                "target_state.turns_started",
                            ),
                            _set(
                                "_control_end_save",
                                "_control_source.control_profile == 'confusion'",
                            ),
                            _when(
                                "_control_source.control_profile == 'fear' and "
                                "_control_source.suppressed != true",
                                [
                                    _require(
                                        "_control_source.caster_visible != null",
                                        "Record whether the Fear source is in sight before its "
                                        "end-turn save.",
                                    ),
                                    _set(
                                        "_control_end_save",
                                        "_control_source.caster_visible == false",
                                    ),
                                ],
                            ),
                            _when(
                                "_control_source.control_profile == 'sleep' and "
                                "_control_source.stage == 'first' "
                                "and target_state.turns_started >= _control_source.next_end_turn",
                                [
                                    _set("_control_end_save", True),
                                ],
                            ),
                            _when(
                                "_control_source.control_profile == 'dance_brief' and "
                                "target_state.turns_started >= _control_source.next_end_turn",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "lasting_state": "{_lasting_state_id}",
                                            "source_id": "{_control_source_id}",
                                        },
                                    ),
                                ],
                            ),
                            _when(
                                "_control_end_save == true",
                                [
                                    _call(
                                        "resolve_srd_control_behavior_save",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "behavior": "{_control_source.control_profile}",
                                            "condition": "{_control_source.condition}",
                                            "save_dc": "{_control_source.save_dc}",
                                            "source_state_id": "{_control_source.caster_state_id}",
                                        },
                                    ),
                                    _set(
                                        "target_state.last_control_behavior_save",
                                        "_control_save_roll",
                                    ),
                                    _set(
                                        "target_state.last_control_behavior_outcome",
                                        "_control_save_outcome",
                                    ),
                                    _push(
                                        "_control_end_rows",
                                        {
                                            "label": "_control_source.spell_id",
                                            "value": "_control_save_detail",
                                        },
                                    ),
                                    _when(
                                        "_control_save_outcome == 'success'",
                                        [
                                            _call(
                                                "retire_srd_timed_condition_source",
                                                {
                                                    "lasting_state": "{_lasting_state_id}",
                                                    "source_id": "{_control_source_id}",
                                                },
                                            ),
                                        ],
                                        [
                                            _when(
                                                "_control_source.control_profile == 'sleep'",
                                                [
                                                    _set(
                                                        "lasting_state.timed_condition_sources.{_control_source"
                                                        "_id}.condition",
                                                        "'unconscious'",
                                                    ),
                                                    _set(
                                                        "lasting_state.timed_condition_sources.{_control_source"
                                                        "_id}.stage",
                                                        "'sleeping'",
                                                    ),
                                                    _set(
                                                        "target_state.timed_condition_unowned_prone",
                                                        True,
                                                    ),
                                                    {
                                                        "apply_condition": {
                                                            "target": "target_state",
                                                            "condition": "prone",
                                                        }
                                                    },
                                                    _call(
                                                        "drop_srd_control_held_items",
                                                        {"target_state": "{_target_state_id}"},
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
        ],
    )
    tools["collect_srd_irresistible_dance"] = _tool(
        "Spend a real action and repeat the active Dance source's Wisdom save to collect yourself.",
        [_entity("actor_state"), _lasting()],
        [
            _set("_control_collect_rows", []),
            _call("spend_srd_action", {"magic_action": False}),
            _set("_control_dance_ids", "actor_state.control_behavior_source_ids"),
            _each(
                "_control_dance_ids",
                "_control_dance_id",
                [
                    _set(
                        "_control_dance_source",
                        "lasting_state.timed_condition_sources[_control_dance_id]",
                    ),
                    _when(
                        "_control_dance_source.control_profile == 'dance' and "
                        "_control_dance_source.suppressed != true",
                        [
                            _call(
                                "resolve_srd_control_behavior_save",
                                {
                                    "target_state": "{_actor_state_id}",
                                    "behavior": "dance",
                                    "condition": "charmed",
                                    "save_dc": "{_control_dance_source.save_dc}",
                                    "source_state_id": "{_control_dance_source.caster_state_id}",
                                },
                            ),
                            _set("actor_state.last_control_behavior_save", "_control_save_roll"),
                            _push(
                                "_control_collect_rows",
                                {"label": "_control_save_label", "value": "_control_save_detail"},
                            ),
                            _when(
                                "_control_save_outcome == 'success'",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "lasting_state": "{_lasting_state_id}",
                                            "source_id": "{_control_dance_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_actor_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
        ],
        [
            {
                "condition": "combat.turn_active == true and combat.active_actor == "
                "_actor_state_id "
                "and actor_state.control_dance_active == true and "
                "combat.active_actor_can_act == true",
                "error": "Collect yourself only on your own active turn under "
                "the Charmed Dance effect.",
            }
        ],
        public=True,
    )
    tools["shake_srd_sleeping_creature"] = _tool(
        "Spend an action within 5 feet to end only Sleep "
        "sources, including its initial Incapacitated stage.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _lasting(),
            _param("target_distance", "integer", minimum=0),
        ],
        [
            _set("_control_sleep_found", False),
            *_source_loop(
                [
                    _when(
                        "_control_source.control_profile == 'sleep'",
                        [_set("_control_sleep_found", True)],
                    ),
                ]
            ),
            _require("_control_sleep_found == true", "There is no Sleep source to shake away."),
            _call("spend_srd_action", {"magic_action": False}),
            *_source_loop(
                [
                    _when(
                        "_control_source.control_profile == 'sleep'",
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "lasting_state": "{_lasting_state_id}",
                                    "source_id": "{_control_source_id}",
                                },
                            ),
                        ],
                    ),
                ]
            ),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
        ],
        [
            {
                "condition": "combat.turn_active == true and combat.active_actor == "
                "_actor_state_id "
                "and combat.active_actor_can_act == true and _actor_state_id != _target_state_id "
                "and target_distance <= 5",
                "error": "An acting creature within 5 feet must spend an action "
                "to shake the target.",
            }
        ],
        public=True,
    )
    tools["read_srd_passive_perception"] = _tool(
        "Calculate actual Passive Perception from the supplied base and active Enthrall penalty.",
        [_entity("target_state"), _param("base_passive_perception", "integer")],
        [
            _set("_passive_perception", "base_passive_perception"),
            _when(
                "target_state.control_enthrall_penalty != null",
                [
                    _set(
                        "_passive_perception",
                        "base_passive_perception - target_state.control_enthrall_penalty",
                    ),
                ],
            ),
            _set("target_state.last_passive_perception", "_passive_perception"),
        ],
        public=True,
    )
    tools["record_srd_control_held_items"] = _tool(
        "Record current held objects and their named attacks "
        "from admitted inventory/source facts before resolving "
        "control.",
        [
            _entity("target_state"),
            _param("held_items", "array", items={"type": "string"}, max_items=8, unique_items=True),
            _param(
                "held_attack_names",
                "array",
                items={"type": "string"},
                required=False,
                default=[],
                max_items=8,
                unique_items=True,
            ),
        ],
        [
            _require(
                "len(held_attack_names) <= len(held_items)",
                "Every held attack must correspond to a held object.",
            ),
            _set("target_state.held_items", "held_items"),
            _set("target_state.held_attack_names", "held_attack_names"),
        ],
        public=True,
    )
    for name in ("collect_srd_irresistible_dance", "shake_srd_sleeping_creature"):
        tools[name]["metadata"]["costs_action"] = True
    tools["read_srd_passive_perception"]["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Passive Perception",
        "summary": [{"label": "Score", "value": {"path": "context._passive_perception"}}],
        "details": [{"label": "Base", "value": {"path": "parameters.base_passive_perception"}}],
    }
    tools["collect_srd_irresistible_dance"]["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Irresistible Dance",
        "summary": [{"label": "Action spent", "value": 1}],
        "details": {"path": "context._control_collect_rows"},
    }
    tools["shake_srd_sleeping_creature"]["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Sleep ends",
        "summary": [
            {"label": "Target", "value": {"path": "context.target_state.name"}},
            {"label": "Action spent", "value": 1},
        ],
    }
    return tools


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _consumers(tools):
    changed = {}
    beginning = deepcopy(tools["begin_combat_turn"])
    beginning["effects"] = [
        effect
        for effect in beginning["effects"]
        if "start_srd_control_behavior_turn" not in str(effect)
    ]
    if "start_srd_control_behavior_turn" not in str(beginning["effects"]):
        beginning["effects"].append(
            _when(
                "actor.has_control_behavior_sources == true",
                [
                    _call(
                        "start_srd_control_behavior_turn",
                        {"target_state": "{_actor_id}"},
                        ("_control_turn_rows",),
                    ),
                    _each(
                        "_control_turn_rows",
                        "_control_turn_row",
                        [_push("_turn_effect_rows", "_control_turn_row")],
                    ),
                ],
            )
        )
    changed["begin_combat_turn"] = beginning
    ending = deepcopy(tools["resolve_srd_timed_condition_end_turn"])
    ending["effects"] = [
        effect for effect in ending["effects"] if "end_srd_control_behavior_turn" not in str(effect)
    ]
    if "end_srd_control_behavior_turn" not in str(ending["effects"]):
        ending["effects"].append(
            _when(
                "target_state.has_control_behavior_sources == true",
                [
                    _call(
                        "end_srd_control_behavior_turn",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ("_control_end_rows",),
                    ),
                    _each(
                        "_control_end_rows",
                        "_control_end_row",
                        [_push("_timed_condition_save_details", "_control_end_row")],
                    ),
                    _when(
                        "len(_control_end_rows) > 0",
                        [_set("_timed_condition_card_title", "'End-turn saves'")],
                    ),
                ],
            )
        )
    changed["resolve_srd_timed_condition_end_turn"] = ending
    action = deepcopy(tools["spend_srd_action"])
    if not any(p["name"] == "control_action_kind" for p in action["parameters"]):
        action["parameters"].append(_param("control_action_kind", required=False, default="other"))
        action["effects"].insert(
            0,
            _call(
                "check_srd_active_control_action",
                {
                    "action_kind": "{control_action_kind}",
                    "actor_state": "{combat.active_actor}",
                },
            ),
        )
    for effect in _walk(action["effects"]):
        if effect.get("call") == "check_srd_active_control_action":
            effect["params"]["actor_state"] = "{combat.active_actor}"
    changed["spend_srd_action"] = action
    for name in ("spend_srd_attack", "spend_srd_creature_attack"):
        spending = deepcopy(tools[name])
        for effect in _walk(spending["effects"]):
            if effect.get("call") == "spend_srd_action":
                effect["params"]["control_action_kind"] = "attack"
        if "finish_srd_control_attack" not in str(spending["effects"]):
            spending["effects"].append(
                _call(
                    "finish_srd_control_attack",
                    {
                        "actor_state": "{_actor_id}",
                    },
                )
            )
        if name == "spend_srd_creature_attack" and "control_confusion_active" not in str(
            spending["pre_conditions"]
        ):
            spending["pre_conditions"].append(
                {
                    "condition": "actor.control_confusion_active != true or "
                    "actor.control_confusion_mode != 'attack' or "
                    "use_multiattack == false",
                    "error": "Confusion requires one melee attack, not Multiattack.",
                }
            )
            spending["pre_conditions"].append(
                {
                    "condition": "actor.control_dropped_attack_names == null or "
                    "attack_name not in actor.control_dropped_attack_names "
                    "or (actor.held_attack_names != null and attack_name in"
                    " actor.held_attack_names)",
                    "error": "The held object used by this attack has been dropped.",
                }
            )
        changed[name] = spending
    movement = deepcopy(tools["refresh_srd_movement_budget"])
    if "control_dancing" not in str(movement["effects"]):
        clause = next(e for e in movement["effects"] if e.get("condition"))
        effects = clause["on_true"]
        index = next(
            i
            for i, e in enumerate(effects)
            if e.get("set", {}).get("field") == "combat.movement_remaining"
        )
        effects.insert(
            index,
            _when(
                "actor_state.control_dancing == true or "
                "(actor_state.control_confusion_active == true and "
                "(actor_state.control_confusion_mode == 'idle' or "
                "actor_state.control_confusion_mode == 'attack'))",
                [
                    _set("_movement_speed", 0),
                ],
            ),
        )
    changed["refresh_srd_movement_budget"] = movement
    for name in ("spend_combat_movement", "spend_srd_movement"):
        moving = deepcopy(tools[name])
        if "control_dancing" not in str(moving["pre_conditions"]):
            moving["pre_conditions"].append(
                {
                    "condition": "actor_state.control_dancing != true and "
                    "actor_state.control_fear_active != true and "
                    "(actor_state.control_confusion_active != true or "
                    "actor_state.control_confusion_mode == 'free')",
                    "error": "Resolve the spell's required movement instead of voluntary movement.",
                }
            )
        changed[name] = moving
    for name in ("spend_srd_bonus_action", "consume_srd_reaction"):
        spending = deepcopy(tools[name])
        if "control_confusion_active" not in str(spending.get("pre_conditions", [])):
            spending.setdefault("pre_conditions", []).append(
                {
                    "condition": "actor_state.control_confusion_active != true",
                    "error": "Confusion prevents Bonus Actions and Reactions.",
                }
            )
        changed[name] = spending
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    if not any(p["name"] == "attack_kind" for p in attack["parameters"]):
        attack["parameters"].append(_param("attack_kind", required=False, default="unknown"))
        attack["effects"].append(
            _when(
                "actor_state.control_confusion_active == true and "
                "actor_state.control_confusion_mode == 'attack'",
                [
                    _require(
                        "attack_kind == 'melee' and actor_state.control_confusion_pending != true "
                        "and _target_state_id == actor_state.control_confusion_target",
                        "Confusion requires a melee attack against its rolled "
                        "creature within reach.",
                    ),
                ],
            )
        )
        attack["effects"].append(
            _when(
                "actor_state.control_fear_active == true",
                [
                    _require(
                        "actor_state.control_fear_facts_known == true",
                        "Resolve current Fear source visibility first.",
                    ),
                    _when(
                        "actor_state.control_fear_visible == true",
                        [_set("_condition_disadvantage", True)],
                    ),
                ],
            )
        )
        attack["effects"].append(
            _when(
                "actor_state.control_dance_active == true", [_set("_condition_disadvantage", True)]
            )
        )
        attack["effects"].append(
            _when("target_state.control_dance_active == true", [_set("_condition_advantage", True)])
        )
    changed["resolve_srd_attack_conditions"] = attack
    removal = deepcopy(tools["remove_srd_timed_condition_effect"])
    next(p for p in removal["parameters"] if p["name"] == "condition")["enum_values"] = list(
        dict.fromkeys(
            [
                *next(p for p in removal["parameters"] if p["name"] == "condition")["enum_values"],
                "frightened",
                "unconscious",
            ],
        )
    )
    changed["remove_srd_timed_condition_effect"] = removal
    condition = deepcopy(tools["apply_srd_condition"])
    for selected in ("frightened", "unconscious"):
        field = "target.timed_condition_unowned_" + selected
        if field not in str(condition["effects"]):
            condition["effects"].append(_when(f"condition == '{selected}'", [_set(field, True)]))
    changed["apply_srd_condition"] = condition
    condition = deepcopy(tools["remove_srd_condition"])
    if "timed_condition_unowned_frightened" not in str(condition["effects"]):
        condition["effects"].append(
            _when(
                "condition == 'frightened' or condition == 'unconscious'",
                [
                    _set("target.timed_condition_unowned_{condition}", False),
                    _call(
                        "remove_srd_timed_condition_effect",
                        {
                            "target_state": "{target}",
                            "lasting_state": "state",
                            "condition": "{condition}",
                        },
                    ),
                ],
            )
        )
    changed["remove_srd_condition"] = condition
    damage = deepcopy(tools["resolve_srd_control_after_damage"])
    if "'sleep'" not in str(damage["effects"]):
        loop = next(e["for_each"] for e in damage["effects"] if "for_each" in e)
        loop["do"].append(
            _when(
                "_control_damage_source.installed == true and "
                "_control_damage_source.control_profile == 'sleep'",
                [
                    _call(
                        "retire_srd_timed_condition_source",
                        {
                            "lasting_state": "{_lasting_state_id}",
                            "source_id": "{_control_damage_source_id}",
                        },
                    ),
                ],
            )
        )
    changed["resolve_srd_control_after_damage"] = damage
    return changed


def _budget_tools():
    return {
        "check_srd_active_control_action": _tool(
            "Gate shared action spending only when the active actor owns control behavior sources.",
            [_param("action_kind"), _entity("actor_state")],
            [
                _when(
                    "actor_state.has_control_behavior_sources == true",
                    [
                        _call(
                            "check_srd_control_action",
                            {"actor_state": "{_actor_state_id}", "action_kind": "{action_kind}"},
                        ),
                    ],
                )
            ],
        ),
        "finish_srd_control_attack": _tool(
            "Limit Confusion's Attack action to its one actual "
            "attack despite class or Multiattack grants.",
            [_entity("actor_state")],
            [
                _when(
                    "actor_state.control_confusion_active == true and "
                    "actor_state.control_confusion_mode == 'attack'",
                    [
                        _set("actor_state.control_confusion_attack_done", True),
                        _set("combat.attacks_remaining", 0),
                        _set("combat.multiattack_remaining", []),
                        _set("combat.attack_available", False),
                    ],
                )
            ],
        ),
    }


def _render_tool(name, tool):
    rendered = yaml.dump({name: tool}, Dumper=_Dumper, sort_keys=False, width=105)
    return "".join("  " + line if line.strip() else line for line in rendered.splitlines(True))


def _patch_roll_consumers(text):
    """Keep shared public-tool anchors while inserting only the owned roll-consumer clauses."""
    pattern = r"(?ms)^  d20_test:[^\n]*\n.*?(?=^  [A-Za-z_][^\n]*:\s*)"
    match = re.search(pattern, text)
    if not match:
        raise ValueError("Missing D20 consumer")
    block = match.group()
    if "control_enthrall_penalty" not in block:
        needle = "      - set:\n          field: _condition_advantage\n"
        insertion = _when(
            "actor_state != null and kind == 'ability_check' and ability == 'wisdom' "
            "and skill == 'perception' and actor_state.control_enthrall_penalty != null",
            [
                _set("_modifier", "_modifier - actor_state.control_enthrall_penalty"),
            ],
        )
        rendered = yaml.dump([insertion], Dumper=_Dumper, sort_keys=False, width=105)
        block = block.replace(
            needle, "".join("      " + line for line in rendered.splitlines(True)) + needle, 1
        )
        needle = "      - condition: forego_save == true or _automatic_failure_reason != 'none'\n"
        insertion = _when(
            "actor_state != null",
            [
                _when(
                    "actor_state.control_dance_active == true and (kind == 'attack_roll' or "
                    "(kind == 'saving_throw' and ability == 'dexterity'))",
                    [
                        _set("_effective_disadvantage", True),
                    ],
                ),
                _when(
                    "actor_state.control_fear_active == true and (kind == "
                    "'ability_check' or kind == 'attack_roll')",
                    [
                        _require(
                            "actor_state.control_fear_facts_known == true",
                            "Resolve current Fear source visibility first.",
                        ),
                        _when(
                            "actor_state.control_fear_visible == true",
                            [_set("_effective_disadvantage", True)],
                        ),
                    ],
                ),
            ],
        )
        rendered = yaml.dump([insertion], Dumper=_Dumper, sort_keys=False, width=105)
        block = block.replace(
            needle, "".join("      " + line for line in rendered.splitlines(True)) + needle, 1
        )
        text = text[: match.start()] + block + text[match.end() :]
    for name, attack_kind in (
        ("resolve_opportunity_attack", "melee"),
        ("resolve_srd_unarmed_effect", "melee"),
        ("resolve_combat_attack", "{attack_mode}"),
        ("resolve_combat_attack_followup", "{attack_mode}"),
        ("resolve_adjudicated_creature_attack", "{attack_mode}"),
    ):
        pattern = rf"(?ms)^  {name}:[^\n]*\n.*?(?=^  [A-Za-z_][^\n]*:\s*)"
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"Missing actual attack consumer: {name}")
        block = match.group()
        pattern = (
            r"(?ms)(- call: resolve_srd_attack_conditions\n.*?)([ ]+)"
            r"fear_source_in_sight: [^\n]+\n(?:[ ]+attack_kind: [^\n]+\n)*"
        )
        for call in list(re.finditer(pattern, block))[::-1]:
            replacement = re.sub(r"(?m)^[ ]+attack_kind: [^\n]+\n", "", call.group())
            replacement += call.group(2) + f"attack_kind: '{attack_kind}'\n"
            block = block[: call.start()] + replacement + block[call.end() :]
        text = text[: match.start()] + block + text[match.end() :]
    return text


def extend_runtime(text):
    """Compose authored control behavior tools without rewriting unrelated runtime blocks."""
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = _tools()
    changed.update(_consumers(tools))
    install = deepcopy(tools["install_srd_timed_condition_source"])
    for parameter in install["parameters"]:
        if parameter["name"] == "condition":
            parameter["enum_values"] = list(
                dict.fromkeys(
                    [*parameter["enum_values"], "none", "frightened", "unconscious"],
                )
            )
        if parameter["name"] == "control_profile":
            parameter["enum_values"] = list(
                dict.fromkeys(
                    [
                        *parameter["enum_values"],
                        "sleep",
                        "fear",
                        "confusion",
                        "enthrall",
                        "dance",
                        "dance_brief",
                    ],
                )
            )
    changed["install_srd_timed_condition_source"] = install
    projection = deepcopy(tools["refresh_srd_timed_condition_target"])
    suppressions = [
        effect
        for effect in projection["effects"]
        if effect.get("call") == "resolve_srd_timed_actor_condition_suppression"
    ]
    for name in (
        "resolve_srd_control_behavior_conditions",
        "resolve_srd_control_behavior_projection",
    ):
        projection["effects"] = [
            effect for effect in projection["effects"] if effect.get("call") != name
        ]
    projection["effects"] = [
        effect
        for effect in projection["effects"]
        if effect.get("call") != "resolve_srd_timed_actor_condition_suppression"
    ]
    projection["effects"] += [
        _call(
            "resolve_srd_control_behavior_conditions",
            {
                "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
            },
        ),
        *suppressions,
        _call(
            "resolve_srd_control_behavior_projection",
            {
                "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
            },
        ),
    ]
    changed["refresh_srd_timed_condition_target"] = projection
    cast = deepcopy(tools["cast_spell"])
    for effect in _walk(cast["effects"]):
        setting = effect.get("set", {})
        if (
            setting.get("field") == "_control_harmful_spell_families"
            and "'timed_control_behavior'" not in setting["value"]
        ):
            setting["value"].append("'timed_control_behavior'")
        if (
            effect.get("condition", "").startswith(
                "_spell_definition.family == 'multi_target_timed_control'"
            )
            and "_control_harm_targets" in str(effect)
            and "timed_control_behavior" not in effect["condition"]
        ):
            effect["condition"] += " or _spell_definition.family == 'timed_control_behavior'"
    if not any(parameter["name"] == "sleepless_targets" for parameter in cast["parameters"]):
        cast["parameters"].append(
            _param(
                "sleepless_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=8,
                unique_items=True,
                description="Selected Sleep targets whose admitted creature/species"
                " traits say they do not sleep.",
            )
        )
    branch = _when(
        "_spell_definition.family == 'timed_control_behavior'",
        [
            _call(
                "cast_srd_control_behavior",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                    "slot_level": "{_slot_level}",
                    "save_dc": "{_casting_profile.save_dc}",
                    "concentration_save_modifier": "{_casting_profile.concentration_save_modifier}",
                    "targets": "{targets}",
                    "targets_max_distance": "{targets_max_distance}",
                    "caster_can_see_all_targets": "{caster_can_see_all_targets}",
                    "all_targets_have_clear_path": "{all_targets_have_clear_path}",
                    "forego_save_targets": "{_forego_save_targets}",
                    "fighting_caster_targets": "{fighting_caster_targets}",
                    "sleepless_targets": "{sleepless_targets}",
                },
                (
                    "_spell_summary_rows",
                    "_spell_detail_rows",
                    "_control_save_rolls",
                    "_control_save_outcomes",
                ),
            ),
        ],
    )

    def insert_dispatch(value):
        if isinstance(value, list):
            for index, effect in enumerate(value):
                if (
                    isinstance(effect, dict)
                    and effect.get("condition")
                    == "_spell_definition.family == 'multi_target_timed_control'"
                    and "cast_spell_multi_target_timed_control" in str(effect)
                ):
                    if not any(
                        e.get("condition") == branch["condition"]
                        for e in value
                        if isinstance(e, dict)
                    ):
                        value.insert(index, branch)
                    return True
                if insert_dispatch(effect):
                    return True
        elif isinstance(value, dict):
            return any(insert_dispatch(child) for child in value.values())
        return False

    if not insert_dispatch(cast["effects"]):
        raise ValueError("Missing authored control family dispatch anchor")
    changed["cast_spell"] = cast
    for name, tool in changed.items():
        rendered = _render_tool(name, tool)
        if name == "cast_spell":
            begin = rendered.index("    - name: spell\n")
            end = rendered.index("    - name:", begin + 1)
            parameter = rendered[begin:end]
            enum_begin = parameter.index("      enum_values:\n") + len("      enum_values:\n")
            enum_end = parameter.index("      required:", enum_begin)
            parameter = (
                parameter[:enum_begin]
                + "        # BEGIN GENERATED SPELL SELECTOR\n"
                + "".join(
                    f"          - {value}\n"
                    for value in next(p for p in cast["parameters"] if p["name"] == "spell")[
                        "enum_values"
                    ]
                )
                + "        # END GENERATED SPELL SELECTOR\n"
                + parameter[enum_end:]
            )
            rendered = rendered[:begin] + parameter + rendered[end:]
            rendered = rendered.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
        pattern = (
            rf"(?ms)^  {re.escape(name)}:[^\n]*\n.*?"
            r"(?=^  [A-Za-z_][^\n]*:\s*|^procedures:|^definition_row_schemas:)"
        )
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _, value=rendered: value, text, count=1)
        else:
            text = text.replace("procedures: {}\n", rendered + "\nprocedures: {}\n", 1)
    rendered = yaml.dump({"timed_control_behavior": _schema()}, Dumper=_Dumper, sort_keys=False)
    rendered = "".join("      " + line for line in rendered.splitlines(True))
    if "      timed_control_behavior:" not in text:
        text = text.replace(
            "      multi_target_timed_control:", rendered + "\n      multi_target_timed_control:", 1
        )
    return _patch_roll_consumers(text)
