"""Source-owned orders and prescribed activities consume the existing combat budgets."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call,
        _Dumper,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _render_tool,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
else:
    from srd_control_behaviors import (
        _call,
        _Dumper,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _render_tool,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )


# Level, range, targets, per-slot targets, base duration, concentration, behavior, type.
ORDER_PROFILES = {
    "command": (1, 60, 1, 1, 0, False, "command", "any"),
    "compulsion": (4, 30, 12, 0, 10, True, "compulsion", "any"),
    "dominate_beast": (4, 60, 1, 0, 10, True, "dominate", "beast"),
    "dominate_person": (5, 60, 1, 0, 10, True, "dominate", "humanoid"),
    "dominate_monster": (8, 60, 1, 0, 600, True, "dominate", "any"),
    "suggestion": (2, 30, 1, 0, 4800, True, "suggestion", "any"),
    "mass_suggestion": (6, 60, 12, 0, 14400, False, "suggestion", "any"),
    "geas": (5, 60, 1, 0, 432000, False, "geas", "any"),
}
ORDER_KEYS = (
    "base_level",
    "range_feet",
    "base_targets",
    "targets_per_slot_level",
    "duration_rounds",
    "concentration",
    "behavior",
    "required_creature_type",
)
PUBLIC_TOOLS = {
    "designate_srd_compulsion_direction",
    "issue_srd_dominated_order",
    "resolve_srd_control_order",
    "resolve_srd_control_activity",
    "violate_srd_geas",
    "command_srd_dominated_reaction",
}
SAVE_EXPORTS = (
    "_control_save_roll",
    "_control_save_outcome",
    "_control_save_immune",
    "_control_save_label",
    "_control_save_detail",
)


def validate_definition(spell, definition):
    if (
        spell not in ORDER_PROFILES
        or tuple(definition.get(key) for key in ORDER_KEYS) != ORDER_PROFILES[spell]
        or definition.get("casting_time") != ("1_minute" if spell == "geas" else "action")
        or definition.get("save_ability") != "wisdom"
    ):
        raise SystemExit(f"Incoherent control-order definition: {spell}")


def _schema():
    props = {
        "casting_time": {"type": "string", "enum": ["action", "1_minute"]},
        "base_level": {"type": "integer", "minimum": 1, "maximum": 8},
        "range_feet": {"type": "integer", "minimum": 30, "maximum": 60},
        "base_targets": {"type": "integer", "minimum": 1, "maximum": 12},
        "targets_per_slot_level": {"type": "integer", "minimum": 0, "maximum": 1},
        "duration_rounds": {"type": "integer", "minimum": 0, "maximum": 432000},
        "concentration": {"type": "boolean"},
        "behavior": {
            "type": "string",
            "enum": ["command", "compulsion", "dominate", "suggestion", "geas"],
        },
        "required_creature_type": {"type": "string", "enum": ["any", "beast", "humanoid"]},
        "save_ability": {"type": "string", "enum": ["wisdom"]},
    }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _orders_loop(effects, actor="target_state"):
    return [
        _set("_order_ids", []),
        _when(
            f"{actor}.control_order_source_ids != null",
            [_set("_order_ids", f"{actor}.control_order_source_ids")],
        ),
        _each(
            "_order_ids",
            "_order_id",
            [_set("_order", "lasting_state.timed_condition_sources[_order_id]"), *effects],
        ),
    ]


def _source(source_id="source_id"):
    return [
        _set("_order", f"lasting_state.timed_condition_sources[{source_id}]"),
        _require(
            "_order != null and _order.installed == true and _order.suppressed != true",
            "This order has no active unsuppressed source.",
        ),
        _call(
            "check_srd_selected_order_source",
            {
                "source_id": "{" + source_id + "}",
                "target_state": "{_order.target_state_id}",
            },
        ),
    ]


def _retire(source_id="_order_id"):
    return _call(
        "retire_srd_timed_condition_source",
        {"lasting_state": "{_lasting_state_id}", "source_id": "{" + source_id + "}"},
    )


def _refresh(actor="_target_state_id"):
    return _call(
        "refresh_srd_timed_condition_target",
        {"target_state": "{" + actor + "}", "lasting_state": "{_lasting_state_id}"},
    )


def _actor_tools():
    derive = _tool(
        "Derive fixed control facts from the admitted character, retaining their source.",
        [_entity("target_state"), _param("actor", resolve="participant")],
        [
            _set("target_state.control_actor_species", "actor.data.species"),
            _when("actor.data.species == 'elf'", [_set("target_state.does_not_sleep", True)]),
            _set("_actor_perception", "floor((actor.data.abilities.wisdom - 10) * 0.5) + 0"),
            _when(
                (
                    "actor.data.skill_proficiencies != null and 'perception' in "
                    "actor.data.skill_proficiencies"
                ),
                [
                    _set(
                        "_actor_perception",
                        "_actor_perception + 2 + floor((actor.data.level - 1) * 0.25)",
                    )
                ],
            ),
            _set("target_state.base_passive_perception", "10 + _actor_perception"),
            _when(
                "actor.data.languages != null",
                [_set("target_state.languages", "actor.data.languages")],
            ),
        ],
    )
    projection = _tool(
        "Refresh participant-derived control facts without treating carried objects as held.",
        [_entity("target_state")],
        [
            _when(
                "target_state.creature_kind == 'character'",
                [
                    _call(
                        "derive_srd_control_character_facts",
                        {"target_state": "{_target_state_id}", "actor": "{_target_state_id}"},
                    )
                ],
            ),
        ],
    )
    fighting = _tool(
        "Derive fighting from committed source-qualified hostility in this encounter.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _param(
                "caster_allies",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=64,
                unique_items=True,
            ),
            _param("fallback", "boolean", required=False, default=False),
        ],
        [
            _set("_order_fighting", "fallback"),
            _when(
                "target_state.fighting_actor_ids != null",
                [
                    _set("_order_fighting", "_caster_state_id in target_state.fighting_actor_ids"),
                    _each(
                        "caster_allies",
                        "_order_ally",
                        [
                            _when(
                                "_order_ally in target_state.fighting_actor_ids",
                                [_set("_order_fighting", True)],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    eligible = _tool(
        (
            "Derive Sleep and Enthrall exceptions from the saved actor before "
            "consulting scene fallback facts."
        ),
        [
            _entity("target_state"),
            _entity("caster_state"),
            _param("behavior"),
            _param("fallback", "boolean"),
            _param(
                "caster_allies",
                "entity_id_list",
                resolve="entity_list",
                default=[],
                required=False,
                max_items=64,
            ),
        ],
        [
            _call("refresh_srd_control_actor_facts", {"target_state": "{_target_state_id}"}),
            _set("_control_actor_auto_success", "fallback"),
            _when(
                "behavior == 'sleep' and target_state.does_not_sleep == true",
                [_set("_control_actor_auto_success", True)],
            ),
            _when(
                "behavior == 'enthrall'",
                [
                    _call(
                        "resolve_srd_control_fighting",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "fallback": "{fallback}",
                            "caster_allies": "{caster_allies}",
                        },
                        ("_order_fighting",),
                    ),
                    _set("_control_actor_auto_success", "_order_fighting"),
                ],
            ),
        ],
    )
    return {
        "derive_srd_control_character_facts": derive,
        "refresh_srd_control_actor_facts": projection,
        "resolve_srd_control_fighting": fighting,
        "resolve_srd_control_actor_eligibility": eligible,
    }


def _cast_tool():
    return _tool(
        "Resolve eight control-order spells from fixed source profiles and admitted actor facts.",
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
                default=[],
                required=False,
                max_items=64,
            ),
            _param(
                "fighting_caster_targets",
                "entity_id_list",
                resolve="entity_list",
                default=[],
                required=False,
                max_items=64,
            ),
            _param(
                "caster_allies",
                "entity_id_list",
                resolve="entity_list",
                default=[],
                required=False,
                max_items=64,
            ),
            _param(
                "command",
                "enum",
                enum_values=["approach", "drop", "flee", "grovel", "halt"],
                default="halt",
                required=False,
            ),
            _param(
                "activity_words",
                "array",
                items={"type": "string"},
                default=[],
                required=False,
                max_items=25,
            ),
            _param("activity_achievable", "boolean", default=False, required=False),
            _param("activity_harmful", "boolean", default=True, required=False),
            _param("geas_command", default="", required=False),
            _param("geas_suicidal", "boolean", default=False, required=False),
        ],
        [
            _set("_order_definition", "definitions.spells[spell]"),
            _each(
                "activity_words",
                "_activity_word",
                [
                    _require(
                        (
                            "len(_activity_word) > 0 and ' ' not in _activity_word and '\\n' not "
                            "in _activity_word and '\\t' not in _activity_word and '\\r' not in "
                            "_activity_word"
                        ),
                        "Supply each activity word as one token, with at most 25 tokens.",
                    )
                ],
            ),
            _call("refresh_srd_control_actor_facts", {"target_state": "{_caster_state_id}"}),
            _set(
                "_order_targets_limit",
                (
                    "_order_definition.base_targets + max(0, slot_level - "
                    "_order_definition.base_level) * "
                    "_order_definition.targets_per_slot_level"
                ),
            ),
            _require(
                "len(targets) <= _order_targets_limit", "Too many source-defined control targets."
            ),
            _require(
                (
                    "targets_max_distance <= _order_definition.range_feet and "
                    "caster_can_see_all_targets == true and all_targets_have_clear_path "
                    "== true"
                ),
                "Control orders require visible targets within range and a clear spell path.",
            ),
            _require(
                (
                    "_order_definition.behavior != 'suggestion' or (len(activity_words) "
                    "> 0 and activity_achievable == true and activity_harmful != true)"
                ),
                (
                    "Suggestion requires at most 25 words describing achievable activity "
                    "without obvious harm to targets or allies."
                ),
            ),
            _require(
                "_order_definition.behavior != 'geas' or len(geas_command) > 0",
                "Geas requires its actual verbal command.",
            ),
            _set("_order_duration", "max(1, _order_definition.duration_rounds)"),
            _when(
                "spell == 'dominate_beast' or spell == 'dominate_person'",
                [
                    _when(
                        "slot_level == _order_definition.base_level + 1",
                        [_set("_order_duration", 100)],
                    ),
                    _when(
                        "slot_level == _order_definition.base_level + 2",
                        [_set("_order_duration", 600)],
                    ),
                    _when(
                        "slot_level >= _order_definition.base_level + 3",
                        [_set("_order_duration", 4800)],
                    ),
                ],
            ),
            _when(
                "spell == 'dominate_monster' and slot_level == 9", [_set("_order_duration", 4800)]
            ),
            _when(
                "spell == 'mass_suggestion'",
                [
                    _when("slot_level == 7", [_set("_order_duration", 144000)]),
                    _when("slot_level == 8", [_set("_order_duration", 432000)]),
                    _when("slot_level == 9", [_set("_order_duration", 5270400)]),
                ],
            ),
            _when("spell == 'geas' and slot_level >= 7", [_set("_order_duration", 5256000)]),
            _set("_condition_concentration_sequence", 0),
            _when(
                "_order_definition.concentration == true",
                [
                    _call(
                        "start_srd_condition_concentration",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "targets": "{targets}",
                            "spell": "{spell}",
                            "duration_rounds": "{_order_duration}",
                            "save_modifier": "{concentration_save_modifier}",
                        },
                    )
                ],
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _set("_order_save_rolls", []),
            _each(
                "targets",
                "_order_target",
                [
                    _call(
                        "validate_srd_timed_condition_target",
                        {
                            "target_state": "{_order_target}",
                            "save_ability": "wisdom",
                            "required_creature_type": "any",
                        },
                    ),
                    _call("refresh_srd_control_actor_facts", {"target_state": "{_order_target}"}),
                    _call(
                        "resolve_srd_control_order_eligibility",
                        {
                            "target_state": "{_order_target}",
                            "caster_state": "{_caster_state_id}",
                            "required_creature_type": "{_order_definition.required_creature_type}",
                            "needs_language": "{_order_definition.behavior}",
                        },
                        ("_order_ineligible",),
                    ),
                    _set("_order_condition", "'charmed'"),
                    _when(
                        "_order_definition.behavior == 'command'",
                        [_set("_order_condition", "'none'")],
                    ),
                    _set("_order_fallback_fighting", "_order_target in fighting_caster_targets"),
                    _call(
                        "resolve_srd_control_fighting",
                        {
                            "target_state": "{_order_target}",
                            "caster_state": "{_caster_state_id}",
                            "caster_allies": "{caster_allies}",
                            "fallback": "{_order_fallback_fighting}",
                        },
                        ("_order_fighting",),
                    ),
                    _set(
                        "_order_advantage",
                        "_order_definition.behavior == 'dominate' and _order_fighting == true",
                    ),
                    _set("_order_forego", "_order_target in forego_save_targets"),
                    _call(
                        "resolve_srd_control_behavior_save",
                        {
                            "target_state": "{_order_target}",
                            "source_state_id": "{_caster_state_id}",
                            "behavior": "{_order_definition.behavior}",
                            "condition": "{_order_condition}",
                            "save_dc": "{save_dc}",
                            "automatic_success": "{_order_ineligible}",
                            "forego": "{_order_forego}",
                            "source_advantage": "{_order_advantage}",
                        },
                        SAVE_EXPORTS,
                    ),
                    _when(
                        "_control_save_roll != null",
                        [_push("_order_save_rolls", "_control_save_roll")],
                        [_push("_order_save_rolls", None)],
                    ),
                    _push(
                        "_spell_summary_rows",
                        {"label": "_control_save_label", "value": "_control_save_outcome"},
                    ),
                    _push(
                        "_spell_detail_rows",
                        {"label": "_control_save_label", "value": "_control_save_detail"},
                    ),
                    _when(
                        (
                            "_control_save_outcome == 'failure' and _control_save_immune != true "
                            "and not (_order_definition.behavior == 'geas' and geas_suicidal == "
                            "true)"
                        ),
                        [
                            _call(
                                "install_srd_timed_condition_source",
                                {
                                    "target_state": "{_order_target}",
                                    "caster_state": "{_caster_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "spell": "{spell}",
                                    "condition": "{_order_condition}",
                                    "save_dc": "{save_dc}",
                                    "duration_rounds": "{_order_duration}",
                                    "repeat_save_timing": "none",
                                    "save_ability": "wisdom",
                                    "control_profile": "{_order_definition.behavior}",
                                    "concentration_sequence": "{_condition_concentration_sequence}",
                                    "caster_allies": "{caster_allies}",
                                },
                            ),
                            _call(
                                "initialize_srd_control_order_source",
                                {
                                    "source_id": "{_timed_condition_source_id}",
                                    "target_state": "{_order_target}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "command": "{command}",
                                    "activity_words": "{activity_words}",
                                    "geas_command": "{geas_command}",
                                    "indefinite": "{slot_level}",
                                },
                            ),
                            _refresh("_order_target"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _source_tools():
    init = _tool(
        "Initialize owned next-turn orders and prescribed activity after source installation.",
        [
            _param("source_id"),
            _entity("target_state"),
            _lasting(),
            _param("command"),
            _param("activity_words", "array", items={"type": "string"}),
            _param("geas_command", required=False, default=""),
            _param("indefinite", "integer"),
        ],
        [
            _set("_order_next_turn", 1),
            _when(
                "target_state.turns_started != null",
                [_set("_order_next_turn", "target_state.turns_started + 1")],
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.order_next_turn",
                "_order_next_turn",
            ),
            _set("lasting_state.timed_condition_sources.{source_id}.order_kind", None),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.started_tick",
                "lasting_state.lasting_tick",
            ),
            _when(
                "lasting_state.timed_condition_sources[source_id].control_profile == 'command'",
                [_set("lasting_state.timed_condition_sources.{source_id}.order_kind", "command")],
            ),
            _when(
                "lasting_state.timed_condition_sources[source_id].control_profile == 'suggestion'",
                [
                    _set(
                        "lasting_state.timed_condition_sources.{source_id}.order_kind", "'activity'"
                    )
                ],
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.activity_words", "activity_words"
            ),
            _set("lasting_state.timed_condition_sources.{source_id}.geas_command", "geas_command"),
            _set("lasting_state.timed_condition_sources.{source_id}.order_pending", False),
            _set("lasting_state.timed_condition_sources.{source_id}.order_complete", False),
            _set("lasting_state.timed_condition_sources.{source_id}.geas_last_damage_day", -1),
            _when(
                (
                    "lasting_state.timed_condition_sources[source_id].control_profile == "
                    "'geas' and indefinite == 9"
                ),
                [_set("lasting_state.timed_condition_sources.{source_id}.expires_tick", None)],
            ),
            _when(
                "lasting_state.timed_condition_sources[source_id].control_profile == 'command'",
                [_set("lasting_state.timed_condition_sources.{source_id}.expires_tick", None)],
            ),
        ],
    )
    eligibility = _tool(
        (
            "Check source creature type and actual hearing/language eligibility "
            "without rolling invalid targets."
        ),
        [
            _entity("target_state"),
            _entity("caster_state"),
            _param("required_creature_type"),
            _param("needs_language"),
        ],
        [
            _set(
                "_order_ineligible",
                (
                    "required_creature_type != 'any' and target_state.creature_type != "
                    "required_creature_type"
                ),
            ),
            _when(
                "needs_language == 'suggestion' or needs_language == 'geas'",
                [
                    _call(
                        "resolve_srd_spell_language",
                        {
                            "listener_state": "{_target_state_id}",
                            "speaker_state": "{_caster_state_id}",
                            "channel": "speech",
                            "perceived": True,
                        },
                        ("_language_understands",),
                    ),
                    _set(
                        "_order_ineligible",
                        (
                            "_order_ineligible == true or _language_understands != true or "
                            "'deafened' in target_state.conditions"
                        ),
                    ),
                ],
            ),
        ],
    )
    project = _tool(
        "Project every active source's order without deleting suppressed or overlapping records.",
        [_entity("target_state"), _lasting()],
        [
            _set("_order_selected", []),
            _set("target_state.control_order_movement_pending", False),
            _set("target_state.has_control_order_sources", False),
            _set("_order_candidates", []),
            _when(
                "target_state.control_behavior_source_ids != null",
                [_set("_order_candidates", "target_state.control_behavior_source_ids")],
            ),
            _each(
                "_order_candidates",
                "_order_candidate",
                [
                    _set(
                        "_order_source", "lasting_state.timed_condition_sources[_order_candidate]"
                    ),
                    _when(
                        (
                            "_order_source.control_profile == 'command' or "
                            "_order_source.control_profile == 'compulsion' or "
                            "_order_source.control_profile == 'dominate' or "
                            "_order_source.control_profile == 'suggestion' or "
                            "_order_source.control_profile == 'geas'"
                        ),
                        [
                            _push("_order_selected", "_order_candidate"),
                            _when(
                                (
                                    "(_order_source.order_pending == true and "
                                    "(_order_source.control_profile == 'compulsion' or "
                                    "_order_source.order_kind == 'approach' or "
                                    "_order_source.order_kind "
                                    "== 'flee' or _order_source.order_kind == 'move')) or "
                                    "(_order_source.control_profile == 'command' and "
                                    "_order_source.order_kind == 'halt' and "
                                    "target_state.turns_started "
                                    "== _order_source.order_next_turn)"
                                ),
                                [_set("target_state.control_order_movement_pending", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _set("target_state.control_order_source_ids", "_order_selected"),
            _set("target_state.has_control_order_sources", "len(_order_selected) > 0"),
        ],
    )
    selected = _tool(
        "Require the effective newest unsuppressed same-spell order source.",
        [_param("source_id"), _entity("target_state")],
        [
            _require(
                "target_state.control_order_source_ids != null and "
                "source_id in target_state.control_order_source_ids",
                "Only the effective same-spell control source can supply an order.",
            )
        ],
    )
    return {
        "initialize_srd_control_order_source": init,
        "check_srd_selected_order_source": selected,
        "resolve_srd_control_order_eligibility": eligibility,
        "refresh_srd_control_order_projection": project,
    }


def _turn_tools():
    start = _tool(
        "Apply next-turn commands and require active compelled orders to be performed.",
        [_entity("target_state"), _lasting()],
        [
            _set("target_state.control_order_blocks_bonus", False),
            *_orders_loop(
                [
                    _when(
                        (
                            "_order.control_profile == 'command' and target_state.turns_started "
                            ">= _order.order_next_turn"
                        ),
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_order_id}.order_pending",
                                True,
                            ),
                            _when(
                                "_order.order_kind == 'drop' or _order.order_kind == 'grovel'",
                                [
                                    _when(
                                        "_order.order_kind == 'drop'",
                                        [
                                            _call(
                                                "drop_srd_control_held_items",
                                                {"target_state": "{_target_state_id}"},
                                            )
                                        ],
                                    ),
                                    _when(
                                        "_order.order_kind == 'grovel'",
                                        [
                                            _set(
                                                "target_state.timed_condition_unowned_prone", True
                                            ),
                                            {
                                                "apply_condition": {
                                                    "target": "target_state",
                                                    "condition": "prone",
                                                }
                                            },
                                        ],
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_order_id}.order_pending",
                                        False,
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_order_id}.order_complete",
                                        True,
                                    ),
                                    _set("target_state.control_order_ends_turn", True),
                                ],
                            ),
                            _when(
                                "_order.order_kind == 'halt'",
                                [
                                    _set("combat.action_available", False),
                                    _set("combat.attack_available", False),
                                    _set("target_state.bonus_action_available", False),
                                    _set("target_state.control_order_blocks_bonus", True),
                                    _set(
                                        "combat.movement_spent",
                                        "combat.movement_spent + combat.movement_remaining",
                                    ),
                                    _set("combat.movement_remaining", 0),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_order_id}.order_pending",
                                        False,
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{_order_id}.order_complete",
                                        True,
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        (
                            "_order.control_profile == 'compulsion' and _order.order_direction "
                            "!= null and target_state.turns_started >= _order.order_next_turn"
                        ),
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_order_id}.order_pending",
                                True,
                            )
                        ],
                    ),
                    _when(
                        (
                            "(_order.control_profile == 'dominate' or _order.control_profile == "
                            "'suggestion') and _order.order_kind != null and "
                            "_order.order_complete != true"
                        ),
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_order_id}.order_pending",
                                True,
                            )
                        ],
                    ),
                ]
            ),
            _when(
                "target_state.control_order_ends_turn == true",
                [
                    _set("target_state.control_order_ends_turn", False),
                    _call("end_combat_turn", {"actor": "{_target_state_id}"}),
                ],
            ),
            _call(
                "refresh_srd_control_order_projection",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
        ],
    )
    end = _tool(
        "Require performed orders and retire Command after the affected creature's next turn.",
        [_entity("target_state"), _lasting()],
        [
            *_orders_loop(
                [
                    _require(
                        "_order.order_pending != true",
                        "Perform the active source-owned order before ending this turn.",
                    ),
                    _when(
                        (
                            "_order.control_profile == 'command' and target_state.turns_started "
                            ">= _order.order_next_turn"
                        ),
                        [_retire()],
                    ),
                ]
            ),
            _refresh(),
        ],
    )
    action = _tool(
        "Only actions consistent with an active prescribed order may spend the shared action.",
        [_entity("actor_state"), _lasting(), _param("action_kind")],
        [
            *_orders_loop(
                [
                    _require(
                        (
                            "_order.order_pending != true or action_kind == 'control_order' or "
                            "(_order.control_profile == 'dominate' and _order.order_kind == "
                            "'attack' and action_kind == 'attack')"
                        ),
                        "Perform the active source-owned order before substituting another action.",
                    ),
                ],
                actor="actor_state",
            ),
        ],
    )
    attack = _tool(
        "Enforce the target of each outstanding dominated Attack order on an actual attack.",
        [_entity("actor_state"), _entity("target_state"), _lasting()],
        [
            *_orders_loop(
                [
                    _when(
                        "_order.order_pending == true and _order.control_profile == 'dominate'",
                        [
                            _require(
                                (
                                    "_order.order_kind == 'attack' and _target_state_id == "
                                    "_order.order_target_state_id"
                                ),
                                "The dominated creature must attack its ordered creature.",
                            )
                        ],
                    ),
                ],
                actor="actor_state",
            ),
        ],
    )
    finish = _tool(
        "Complete a single-attack dominated order only after its actual attack resolves.",
        [_entity("actor_state"), _lasting()],
        [
            *_orders_loop(
                [
                    _when(
                        (
                            "_order.order_pending == true and _order.control_profile == "
                            "'dominate' and _order.order_kind == 'attack'"
                        ),
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_order_id}.order_pending",
                                False,
                            ),
                            _set(
                                "lasting_state.timed_condition_sources.{_order_id}.order_complete",
                                True,
                            ),
                        ],
                    ),
                ],
                actor="actor_state",
            ),
        ],
    )
    return {
        "start_srd_control_order_turn": start,
        "end_srd_control_order_turn": end,
        "check_srd_control_order_action": action,
        "check_srd_control_order_attack": attack,
        "finish_srd_control_order_attack": finish,
    }


def _public_tools():
    direction = _tool(
        "Spend the caster's Bonus Action to designate any nonzero horizontal direction.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("direction", "array", items={"type": "number"}, min_items=3, max_items=3),
        ],
        [
            _require(
                (
                    "combat.turn_active == true and combat.active_actor == "
                    "_caster_state_id and caster_state.concentration_spell_id == "
                    "'compulsion'"
                ),
                "Designate Compulsion direction only on its concentrating caster's active turn.",
            ),
            _require(
                (
                    "direction[2] == 0 and direction[0] * direction[0] + direction[1] * "
                    "direction[1] > 0"
                ),
                "Compulsion's direction must be nonzero and horizontal.",
            ),
            _require(
                "caster_state.bonus_action_available == true and "
                "caster_state.control_confusion_active != true and "
                "caster_state.control_order_blocks_bonus != true and "
                "not ('incapacitated' in caster_state.conditions or 'paralyzed' in "
                "caster_state.conditions or 'stunned' in caster_state.conditions or "
                "'petrified' in caster_state.conditions or "
                "'unconscious' in caster_state.conditions)",
                "The concentrating caster must have a legal available Bonus Action.",
            ),
            _set("caster_state.bonus_action_available", False),
            _set("caster_state.last_bonus_action", "'compulsion_direction'"),
            _set("_compelled_targets", "caster_state.concentration_target_state_ids"),
            _each(
                "_compelled_targets",
                "_compelled_target",
                [
                    _call(
                        "set_srd_compulsion_target_direction",
                        {
                            "target_state": "{_compelled_target}",
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "direction": "{direction}",
                        },
                    )
                ],
            ),
        ],
        public=True,
    )
    set_direction = _tool(
        "Update this caster's exact active Compulsion source for the target's next turn.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("direction", "array", items={"type": "number"}),
        ],
        [
            *_orders_loop(
                [
                    _when(
                        (
                            "_order.control_profile == 'compulsion' and _order.caster_state_id "
                            "== _caster_state_id"
                        ),
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_order_id}.order_direction",
                                "direction",
                            ),
                            _set("_compelled_next", 1),
                            _when(
                                "target_state.turns_started != null",
                                [_set("_compelled_next", "target_state.turns_started + 1")],
                            ),
                            _set(
                                "lasting_state.timed_condition_sources.{_order_id}.order_next_turn",
                                "_compelled_next",
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    issue = _tool(
        "Issue a concrete telepathic order on the caster's turn without spending an action.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param(
                "order_kind", "enum", enum_values=["attack", "move", "fetch", "defend", "activity"]
            ),
            _param("order_target_state", resolve="entity", required=False, default=None),
            _param("order_description", required=False, default=""),
        ],
        [
            *_source(),
            _require(
                (
                    "_order.control_profile == 'dominate' and _order.caster_state_id == "
                    "_caster_state_id and _order.target_state_id == _target_state_id and "
                    "'charmed' in target_state.conditions"
                ),
                "Only this Dominate source's charmer may issue an order.",
            ),
            _require(
                "combat.turn_active == true and combat.active_actor == _caster_state_id",
                "Issue telepathic orders on your turn.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ("_scene_same_plane",),
            ),
            _require(
                "_scene_same_plane == true", "Dominate's telepathic link requires the same plane."
            ),
            _require(
                "order_kind != 'attack' or order_target_state != null",
                "An Attack order must name its actual target.",
            ),
            _set("lasting_state.timed_condition_sources.{source_id}.order_kind", "order_kind"),
            _set("lasting_state.timed_condition_sources.{source_id}.order_pending", True),
            _set("lasting_state.timed_condition_sources.{source_id}.order_complete", False),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.order_description",
                "order_description",
            ),
            _when(
                "order_target_state != null",
                [
                    _set(
                        "lasting_state.timed_condition_sources.{source_id}.order_target_state_id",
                        "_order_target_state_id",
                    )
                ],
                [
                    _set(
                        "lasting_state.timed_condition_sources.{source_id}.order_target_state_id",
                        None,
                    )
                ],
            ),
        ],
        public=True,
    )
    resolve = _tool(
        "Perform prescribed movement through the admitted scene and shared movement budget.",
        [
            _entity("actor_state"),
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("destination", "array", items={"type": "number"}, min_items=3, max_items=3),
            _param(
                "mode",
                "enum",
                enum_values=["walk", "fly", "climb", "ceiling"],
                default="walk",
                required=False,
            ),
            _param("safe_route", "boolean"),
            _param("shortest_route", "boolean"),
            _param("blocked_route", "boolean", required=False, default=False),
        ],
        [
            *_source(),
            _require(
                (
                    "combat.turn_active == true and combat.active_actor == "
                    "_actor_state_id and _order.target_state_id == _actor_state_id and "
                    "_order.order_pending == true"
                ),
                "Perform only the affected creature's outstanding order on its active turn.",
            ),
            _require(
                "_order.caster_state_id == _caster_state_id and safe_route == true",
                "The supplied route must be safe and belong to this source.",
            ),
            _require(
                "(_order.control_profile == 'command' and (_order.order_kind == 'approach' "
                "or _order.order_kind == 'flee')) or _order.control_profile == 'compulsion' "
                "or (_order.control_profile == 'dominate' and _order.order_kind == 'move')",
                "This order must be resolved by its actual action or Attack owner.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_actor_state_id}", "target_state": "{_caster_state_id}"},
                ("_scene_distance", "_scene_actor_position", "_scene_target_position"),
            ),
            _set("_order_before_distance", "_scene_distance"),
            _require(
                (
                    "_order.control_profile != 'command' or _order.order_kind != "
                    "'approach' or shortest_route == true"
                ),
                "Approach requires the shortest direct route.",
            ),
            _when(
                "_order.control_profile == 'command' and _order.order_kind == 'flee'",
                [
                    _call(
                        "spend_srd_action",
                        {"magic_action": False, "control_action_kind": "control_order"},
                    ),
                    _call("apply_srd_dash", {"actor_state": "{_actor_state_id}"}),
                ],
            ),
            _when(
                "_order.control_profile == 'compulsion'",
                [
                    _require(
                        (
                            "(destination[0] - _scene_actor_position[0]) * "
                            "_order.order_direction[0] + (destination[1] - "
                            "_scene_actor_position[1]) * _order.order_direction[1] >= 0 and "
                            "destination[2] == _scene_actor_position[2]"
                        ),
                        "Compulsion movement must follow the designated horizontal direction.",
                    )
                ],
            ),
            _set("actor_state.control_order_moving", True),
            _call(
                "move_srd_scene_creature",
                {
                    "actor_state": "{_actor_state_id}",
                    "destination": "{destination}",
                    "mode": "{mode}",
                    "difficult_terrain": False,
                },
            ),
            _set("actor_state.control_order_moving", False),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_actor_state_id}", "target_state": "{_caster_state_id}"},
                ("_scene_distance",),
            ),
            _when(
                "_order.control_profile == 'command' and _order.order_kind == 'approach'",
                [
                    _require(
                        "_scene_distance < _order_before_distance or blocked_route == true",
                        "Approach must actually move closer to its source.",
                    ),
                    _require(
                        (
                            "_scene_distance <= 5 or combat.movement_remaining == 0 or "
                            "blocked_route == true"
                        ),
                        (
                            "Approach must use its direct route until within 5 feet or movement "
                            "is exhausted."
                        ),
                    ),
                ],
            ),
            _when(
                "_order.control_profile == 'command' and _order.order_kind == 'flee'",
                [
                    _require(
                        "_scene_distance > _order_before_distance or blocked_route == true",
                        "Flee must actually move away from its source.",
                    )
                ],
            ),
            _require(
                (
                    "(_order.control_profile != 'compulsion' and _order.order_kind != 'flee') "
                    "or combat.movement_remaining "
                    "== 0 or blocked_route == true"
                ),
                "Prescribed movement uses as much movement as the safe route permits.",
            ),
            _set("lasting_state.timed_condition_sources.{source_id}.order_pending", False),
            _set("lasting_state.timed_condition_sources.{source_id}.order_complete", True),
            _when(
                "_order.control_profile == 'compulsion'",
                [
                    _call(
                        "resolve_srd_control_behavior_save",
                        {
                            "target_state": "{_actor_state_id}",
                            "source_state_id": "{_caster_state_id}",
                            "behavior": "compulsion",
                            "condition": "charmed",
                            "save_dc": "{_order.save_dc}",
                        },
                        SAVE_EXPORTS,
                    ),
                    _when("_control_save_outcome == 'success'", [_retire("source_id")]),
                    _refresh("_actor_state_id"),
                ],
            ),
            _when(
                (
                    "_order.control_profile == 'command' and (_order.order_kind == "
                    "'flee' or (_order.order_kind == 'approach' and _scene_distance <= "
                    "5))"
                ),
                [_call("end_combat_turn", {"actor": "{_actor_state_id}"})],
            ),
        ],
        public=True,
    )
    activity = _tool(
        "Spend the actual task action, preserving activity until its completion is adjudicated.",
        [
            _entity("actor_state"),
            _lasting(),
            _param("source_id"),
            _param("completed", "boolean"),
            _param("completion_evidence"),
            _param("task_kind", "enum", enum_values=["work", "fetch", "defend"]),
        ],
        [
            *_source(),
            _require(
                (
                    "_order.target_state_id == _actor_state_id and "
                    "(_order.control_profile == 'suggestion' or _order.control_profile "
                    "== 'dominate')"
                ),
                "The prescribed activity must belong to this creature and source.",
            ),
            _require(
                "len(completion_evidence) > 0",
                "Record the actual task action and any completion evidence.",
            ),
            _require(
                (
                    "_order.control_profile != 'dominate' or _order.order_kind == "
                    "'activity' or _order.order_kind == 'fetch' or _order.order_kind == "
                    "'defend'"
                ),
                "Use the actual Attack or movement owner for that dominated order.",
            ),
            _call(
                "spend_srd_action", {"magic_action": False, "control_action_kind": "control_order"}
            ),
            _when("task_kind == 'defend'", [_set("actor_state.dodging", True)]),
            _set("lasting_state.timed_condition_sources.{source_id}.order_pending", False),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.activity_last_evidence",
                "completion_evidence",
            ),
            _when(
                "completed == true",
                [
                    _set("lasting_state.timed_condition_sources.{source_id}.order_complete", True),
                    _when("_order.control_profile == 'suggestion'", [_retire("source_id")]),
                    _refresh("_actor_state_id"),
                ],
            ),
        ],
        [
            {
                "condition": (
                    "combat.turn_active == true and combat.active_actor == "
                    "_actor_state_id and combat.active_actor_can_act == true"
                ),
                "error": "Perform task actions on the affected creature's active turn.",
            }
        ],
        public=True,
    )
    geas = _tool(
        "Apply Geas's real Psychic damage at most once per source on each lasting-clock day.",
        [
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("directly_counter", "boolean"),
            _param("action_evidence"),
        ],
        [
            *_source(),
            _require(
                (
                    "_order.control_profile == 'geas' and _order.target_state_id == "
                    "_target_state_id and 'charmed' in target_state.conditions"
                ),
                "Only an active Charmed Geas source can punish its target.",
            ),
            _require(
                "directly_counter == true and len(action_evidence) > 0",
                "Geas requires an actual action directly contrary to its saved command.",
            ),
            _set(
                "_geas_day", "floor((lasting_state.lasting_tick + 0.000000001) * 28800 ** -1) + 0"
            ),
            _set("_geas_damage", 0),
            _when(
                "_order.geas_last_damage_day != _geas_day",
                [
                    {"dice": {"expression": "5d10"}, "store_as": "_geas_roll"},
                    _set("_geas_damage", "_geas_roll.total"),
                    _call(
                        "apply_srd_damage",
                        {
                            "target": "{_target_state_id}",
                            "amount": "{_geas_damage}",
                            "adjustment": 0,
                            "damage_type": "psychic",
                            "source_state_id": "{_order.caster_state_id}",
                        },
                    ),
                    _set(
                        "lasting_state.timed_condition_sources.{source_id}.geas_last_damage_day",
                        "_geas_day",
                    ),
                    _set(
                        "lasting_state.timed_condition_sources.{source_id}.geas_last_evidence",
                        "action_evidence",
                    ),
                    _set("target_state.last_geas_damage_roll", "_geas_roll"),
                ],
            ),
        ],
        public=True,
    )
    reaction = _tool(
        "Authorize a legal commanded Reaction by spending only the Dominate caster's Reaction.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("pending_action_id"),
        ],
        [
            *_source(),
            _require(
                (
                    "_order.control_profile == 'dominate' and _order.caster_state_id == "
                    "_caster_state_id and _order.target_state_id == _target_state_id and "
                    "'charmed' in target_state.conditions"
                ),
                "Only this active Dominate caster can command its target's Reaction.",
            ),
            _require(
                (
                    "lasting_state.pending_srd_action_stack != null and "
                    "len(lasting_state.pending_srd_action_stack) > 0 and "
                    "lasting_state.pending_srd_action_stack[-1] == pending_action_id"
                ),
                "Command only a Reaction to the current pending parent event.",
            ),
            _require(
                "target_state.reaction_available == true",
                "The commanded creature needs its own available Reaction.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ("_scene_same_plane",),
            ),
            _require(
                "_scene_same_plane == true", "Dominate's telepathic link requires the same plane."
            ),
            _call(
                "consume_srd_reaction",
                {
                    "actor_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "reaction": "dominate_command",
                },
            ),
            _set(
                "lasting_state.pending_srd_actions.{pending_action_id}.dominated_reaction_authorizations.{_target_state_id}",
                {
                    "caster_state_id": "_caster_state_id",
                    "source_id": "source_id",
                    "consumed": False,
                },
            ),
        ],
        public=True,
    )
    cure = _tool(
        "Remove only Geas sources when an actual named restoration spell resolves.",
        [_entity("target_state"), _lasting()],
        [
            _set("_order_cure_sources", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_order_cure_sources", "target_state.timed_condition_source_ids")],
            ),
            _each(
                "_order_cure_sources",
                "_order_id",
                [
                    _set("_order", "lasting_state.timed_condition_sources[_order_id]"),
                    _when(
                        "_order.installed == true and _order.control_profile == 'geas'", [_retire()]
                    ),
                ],
            ),
            _refresh(),
        ],
    )
    geas["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "inline",
        "title": "Geas",
        "summary": [{"label": "Psychic damage rolled", "value": {"path": "context._geas_damage"}}],
    }
    return {
        "designate_srd_compulsion_direction": direction,
        "set_srd_compulsion_target_direction": set_direction,
        "issue_srd_dominated_order": issue,
        "resolve_srd_control_order": resolve,
        "resolve_srd_control_activity": activity,
        "violate_srd_geas": geas,
        "command_srd_dominated_reaction": reaction,
        "remove_srd_geas_sources": cure,
    }


def _consumers(tools):
    """Attach active orders to existing actual budgets and committed damage owners."""
    changed = {}
    beginning = deepcopy(tools["begin_combat_turn"])
    beginning["effects"] = [
        e for e in beginning["effects"] if "start_srd_control_order_turn" not in str(e)
    ]
    beginning["effects"].append(
        _when(
            "actor.has_control_order_sources == true",
            [_call("start_srd_control_order_turn", {"target_state": "{_actor_id}"})],
        )
    )
    changed["begin_combat_turn"] = beginning
    ending = deepcopy(tools["resolve_srd_timed_condition_end_turn"])
    ending["effects"] = [e for e in ending["effects"] if "end_srd_control_order_turn" not in str(e)]
    ending["effects"].insert(
        0,
        _when(
            "target_state.has_control_order_sources == true",
            [
                _call(
                    "end_srd_control_order_turn",
                    {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
                )
            ],
        ),
    )
    for effect in _walk(ending["effects"]):
        if effect.get("for_each", {}).get("as") == "_timed_condition_end_turn_source_id":
            effect["for_each"]["do"] = [
                e
                for e in effect["for_each"]["do"]
                if "_timed_condition_end_turn_source.control_profile == 'command'" not in str(e)
            ]
            effect["for_each"]["do"].append(
                _when(
                    "_timed_condition_end_turn_source.installed == true and "
                    "_timed_condition_end_turn_source.control_profile == 'command' and "
                    "target_state.turns_started >= "
                    "_timed_condition_end_turn_source.order_next_turn",
                    [_retire("_timed_condition_end_turn_source_id")],
                )
            )
    changed["resolve_srd_timed_condition_end_turn"] = ending
    action = deepcopy(tools["check_srd_active_control_action"])
    action["effects"] = [
        e for e in action["effects"] if "check_srd_control_order_action" not in str(e)
    ]
    action["effects"].append(
        _when(
            "actor_state.has_control_order_sources == true",
            [
                _call(
                    "check_srd_control_order_action",
                    {"actor_state": "{_actor_state_id}", "action_kind": "{action_kind}"},
                )
            ],
        )
    )
    changed["check_srd_active_control_action"] = action
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    attack["effects"] = [
        e for e in attack["effects"] if "check_srd_control_order_attack" not in str(e)
    ]
    attack["effects"].append(
        _when(
            "actor_state.has_control_order_sources == true",
            [
                _call(
                    "check_srd_control_order_attack",
                    {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                )
            ],
        )
    )
    changed["resolve_srd_attack_conditions"] = attack
    for name, actor in (
        ("resolve_combat_attack", "_actor_state_id"),
        ("resolve_combat_attack_followup", "_actor_state_id"),
        ("resolve_adjudicated_creature_attack", "_actor_id"),
    ):
        actual = deepcopy(tools[name])
        actual["effects"] = [
            e
            for e in actual["effects"]
            if "finish_srd_control_order_attack" not in str(e)
            and "finish_srd_active_control_order_attack" not in str(e)
        ]
        actual["effects"].append(
            _call("finish_srd_active_control_order_attack", {"actor_state": "{" + actor + "}"})
        )
        changed[name] = actual
    changed["finish_srd_active_control_order_attack"] = _tool(
        "Finish an actual ordered attack only when control orders are active.",
        [_entity("actor_state")],
        [
            _when(
                "actor_state.has_control_order_sources == true",
                [_call("finish_srd_control_order_attack", {"actor_state": "{_actor_state_id}"})],
            )
        ],
    )
    for name in ("spend_combat_movement", "spend_srd_movement"):
        moving = deepcopy(tools[name])
        actor = "actor_state" if name == "spend_combat_movement" else "target"
        for precondition in moving["pre_conditions"]:
            if "control_fear_active" in precondition["condition"]:
                precondition["condition"] = (
                    f"{actor}.control_order_moving == true or ({actor}.control_dancing != true "
                    f"and {actor}.control_fear_active != true and "
                    f"({actor}.control_confusion_active "
                    f"!= true or {actor}.control_confusion_mode == 'free'))"
                )
        moving["pre_conditions"] = [
            p for p in moving["pre_conditions"] if "control_order_source" not in p["condition"]
        ]
        moving["pre_conditions"].append(
            {
                "condition": (
                    f"{actor}.has_control_order_sources != true or "
                    f"{actor}.control_order_moving == true or "
                    f"{actor}.control_order_movement_pending != true"
                ),
                "error": "Perform prescribed movement through the source-owned order resolver.",
            }
        )
        changed[name] = moving
    bonus = deepcopy(tools["spend_srd_bonus_action"])
    if "control_order_blocks_bonus" not in str(bonus["pre_conditions"]):
        bonus["pre_conditions"].append(
            {
                "condition": "actor_state.control_order_blocks_bonus != true",
                "error": "Command Halt prevents Bonus Actions on this turn.",
            }
        )
    changed["spend_srd_bonus_action"] = bonus
    damage = deepcopy(tools["resolve_srd_control_after_damage"])
    loop = next(e["for_each"]["do"] for e in damage["effects"] if "for_each" in e)
    loop[:] = [
        e
        for e in loop
        if "_control_damage_source.control_profile == 'dominate'" not in e.get("condition", "")
    ]
    for effect in _walk(loop):
        if (
            effect.get("condition", "").startswith(
                "_control_damage_source.installed == true and "
                "(_control_damage_source.control_profile == 'animal_charm'"
            )
            and "suggestion" not in effect["condition"]
        ):
            effect["condition"] = (
                effect["condition"][:-1]
                + " or _control_damage_source.control_profile == 'suggestion')"
            )
    loop.append(
        _when(
            (
                "_control_damage_source.installed == true and "
                "_control_damage_source.suppressed != true and "
                "_control_damage_source.control_profile == 'dominate' and "
                "_control_damage_source_id in target_state.control_order_source_ids"
            ),
            [
                _call(
                    "resolve_srd_control_behavior_save",
                    {
                        "target_state": "{_target_state_id}",
                        "source_state_id": "{_control_damage_source.caster_state_id}",
                        "behavior": "dominate",
                        "condition": "charmed",
                        "save_dc": "{_control_damage_source.save_dc}",
                    },
                    SAVE_EXPORTS,
                ),
                _set("target_state.last_control_damage_save_roll", "_control_save_roll"),
                _set("target_state.last_control_damage_save_outcome", "_control_save_outcome"),
                _when("_control_save_outcome == 'success'", [_retire("_control_damage_source_id")]),
            ],
        )
    )
    changed["resolve_srd_control_after_damage"] = damage
    passive = deepcopy(tools["read_srd_passive_perception"])
    for parameter in passive["parameters"]:
        if parameter["name"] == "base_passive_perception":
            parameter.update(required=False, default=10)
    passive["effects"].insert(
        0, _call("refresh_srd_control_actor_facts", {"target_state": "{_target_state_id}"})
    )
    for effect in _walk(passive["effects"]):
        setting = effect.get("set", {})
        if isinstance(setting.get("value"), str):
            setting["value"] = setting["value"].replace(
                "base_passive_perception", "_control_base_passive"
            )
    passive["effects"].insert(1, _set("_control_base_passive", "base_passive_perception"))
    passive["effects"].insert(
        2,
        _when(
            "target_state.base_passive_perception != null",
            [_set("_control_base_passive", "target_state.base_passive_perception")],
        ),
    )
    changed["read_srd_passive_perception"] = passive
    passive["metadata"]["result_card"]["details"][0]["value"]["path"] = (
        "context._control_base_passive"
    )
    friendly = deepcopy(tools["cast_spell_multi_target_timed_control"])
    for effect in _walk(friendly["effects"]):
        loop = effect.get("for_each", {})
        if loop.get("as") != "_timed_condition_target_id":
            continue
        effects = loop["do"]
        if not any(e.get("set", {}).get("field") == "_control_save_advantage" for e in effects):
            continue
        effects[:] = [e for e in effects if "resolve_srd_control_fighting" not in str(e)]
        index = next(
            i
            for i, e in enumerate(effects)
            if e.get("set", {}).get("field") == "_control_save_advantage"
        )
        effects.insert(
            index + 1,
            _when(
                "control_profile == 'friendly_charm'",
                [
                    _call(
                        "resolve_srd_control_fighting",
                        {
                            "target_state": "{_timed_condition_target_id}",
                            "caster_state": "{_caster_state_id}",
                            "caster_allies": "{caster_allies}",
                            "fallback": "{_control_save_advantage}",
                        },
                        ("_order_fighting",),
                    ),
                    _set("_control_save_advantage", "_order_fighting"),
                ],
            ),
        )
    changed["cast_spell_multi_target_timed_control"] = friendly
    behavior = deepcopy(tools["cast_srd_control_behavior"])
    behavior["parameters"].append(
        _param(
            "caster_allies",
            "entity_id_list",
            resolve="entity_list",
            default=[],
            required=False,
            max_items=64,
        )
    )
    for effect in _walk(behavior["effects"]):
        if (
            "for_each" in effect
            and effect["for_each"].get("as") == "_control_target"
            and "_control_auto_success" in str(effect)
        ):
            effects = effect["for_each"]["do"]
            index = next(
                i
                for i, e in enumerate(effects)
                if e.get("set", {}).get("field") == "_control_auto_success"
            )
            effects[index + 1 : index + 1] = [
                _call(
                    "resolve_srd_control_actor_eligibility",
                    {
                        "target_state": "{_control_target}",
                        "caster_state": "{_caster_state_id}",
                        "behavior": "{_control_definition.behavior}",
                        "fallback": "{_control_auto_success}",
                        "caster_allies": "{caster_allies}",
                    },
                    ("_control_actor_auto_success",),
                ),
                _set("_control_auto_success", "_control_actor_auto_success"),
            ]
    changed["cast_srd_control_behavior"] = behavior
    held = deepcopy(tools["record_srd_control_held_items"])
    held["effects"].insert(
        0,
        _each(
            "held_items",
            "_held_item",
            [
                _require(
                    "target_state.inventory != null and _held_item in target_state.inventory",
                    "Held objects must exist in this actor's admitted inventory.",
                )
            ],
        ),
    )
    changed["record_srd_control_held_items"] = held
    return changed


def extend_runtime(text):
    """Compose order records through the existing source, suppression, turn and damage owners."""
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = _actor_tools() | _source_tools() | _turn_tools() | _public_tools()
    for name in PUBLIC_TOOLS:
        implementation = changed[name]
        internal_name = name + "_internal"
        changed[internal_name] = deepcopy(implementation)
        changed[internal_name]["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        public_parameters = [
            p for p in implementation["parameters"] if p["name"] != "lasting_state"
        ]
        params = {
            p["name"]: "{" + ("_" + p["name"] + "_id" if p.get("resolve") else p["name"]) + "}"
            for p in public_parameters
        }
        params["lasting_state"] = "state"
        changed[name] = {
            "description": implementation["description"],
            "parameters": public_parameters,
            "effects": [
                _call(
                    internal_name, params, ("_geas_damage",) if name == "violate_srd_geas" else ()
                )
            ],
            "metadata": implementation["metadata"],
        }
    changed.update(_consumers(tools))
    changed["cast_srd_control_orders"] = _cast_tool()
    save = deepcopy(tools["resolve_srd_control_behavior_save"])
    save["parameters"].append(_param("source_advantage", "boolean", default=False, required=False))
    for effect in _walk(save["effects"]):
        if effect.get("set", {}).get("field") == "_control_save_advantage":
            effect["set"]["value"] = (
                "source_advantage == true or _source_condition_save_advantage == true"
                if effect["set"]["value"] == "_source_condition_save_advantage"
                else "source_advantage"
            )
    save["effects"].insert(
        0, _call("refresh_srd_control_actor_facts", {"target_state": "{_target_state_id}"})
    )
    save_index = next(
        i
        for i, e in enumerate(save["effects"])
        if e.get("condition", "").startswith("automatic_success")
    )
    save["effects"].insert(
        save_index,
        _when(
            "target_state.has_timed_actor_defenses == true and (behavior == 'command' or "
            "behavior == 'compulsion' or behavior == 'dominate' or behavior == 'suggestion' "
            "or behavior == 'geas' or behavior == 'confusion')",
            [
                _call(
                    "check_srd_mind_blank_information",
                    {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "state",
                        "query_kind": "mind_control",
                    },
                    ("_mind_blank_information_blocked",),
                ),
                _when(
                    "_mind_blank_information_blocked == true", [_set("_control_save_immune", True)]
                ),
            ],
        ),
    )
    changed["resolve_srd_control_behavior_save"] = save
    select = deepcopy(tools["resolve_srd_control_behavior_sources"])
    select["effects"].insert(
        0,
        _set(
            "_control_spell_profiles",
            [
                "'sleep'",
                "'fear'",
                "'confusion'",
                "'enthrall'",
                "'dance'",
                "'dance_brief'",
                "'command'",
                "'compulsion'",
                "'dominate'",
                "'suggestion'",
                "'geas'",
            ],
        ),
    )
    for effect in _walk(select["effects"]):
        if effect.get("condition", "").startswith("_control_index_source.installed == true"):
            effect["condition"] = (
                "_control_index_source.installed == true and "
                "_control_index_source.control_profile in _control_spell_profiles "
                "and (include_suppressed == true or _control_index_source.suppressed != true)"
            )
    changed["resolve_srd_control_behavior_sources"] = select
    install = deepcopy(tools["install_srd_timed_condition_source"])
    for parameter in install["parameters"]:
        if parameter["name"] == "duration_rounds":
            parameter["maximum"] = 10000000
        if parameter["name"] == "control_profile":
            parameter["enum_values"] = list(
                dict.fromkeys(
                    [
                        *parameter["enum_values"],
                        "command",
                        "compulsion",
                        "dominate",
                        "suggestion",
                        "geas",
                    ]
                )
            )
    changed["install_srd_timed_condition_source"] = install
    projection = deepcopy(tools["resolve_srd_control_behavior_projection"])
    projection["effects"].append(
        _call(
            "refresh_srd_control_order_projection",
            {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
        )
    )
    changed["resolve_srd_control_behavior_projection"] = projection
    cast = deepcopy(tools["cast_spell"])

    def remove_dispatch(value):
        if isinstance(value, list):
            value[:] = [
                e
                for e in value
                if not (
                    isinstance(e, dict)
                    and e.get("condition") == "_spell_definition.family == 'timed_control_orders'"
                )
            ]
            for e in value:
                remove_dispatch(e)
        elif isinstance(value, dict):
            for e in value.values():
                remove_dispatch(e)

    remove_dispatch(cast["effects"])
    for effect in _walk(cast["effects"]):
        if effect.get("call") == "cast_srd_control_behavior":
            effect["params"]["caster_allies"] = "{caster_allies}"
        setting = effect.get("set", {})
        if (
            setting.get("field") == "_control_harmful_spell_families"
            and "'timed_control_orders'" not in setting["value"]
        ):
            setting["value"].append("'timed_control_orders'")
        if (
            effect.get("condition", "").startswith(
                "_spell_definition.family == 'multi_target_timed_control'"
            )
            and "_control_harm_targets" in str(effect)
            and "timed_control_orders" not in effect["condition"]
        ):
            effect["condition"] += " or _spell_definition.family == 'timed_control_orders'"
    for parameter in changed["cast_srd_control_orders"]["parameters"]:
        if parameter["name"] in {
            "command",
            "activity_words",
            "activity_achievable",
            "activity_harmful",
            "geas_command",
            "geas_suicidal",
        } and not any(p["name"] == parameter["name"] for p in cast["parameters"]):
            cast["parameters"].append(deepcopy(parameter))
    dispatch = _when(
        "_spell_definition.family == 'timed_control_orders'",
        [
            _call(
                "cast_srd_control_orders",
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
                    "caster_allies": "{caster_allies}",
                    "command": "{command}",
                    "activity_words": "{activity_words}",
                    "activity_achievable": "{activity_achievable}",
                    "activity_harmful": "{activity_harmful}",
                    "geas_command": "{geas_command}",
                    "geas_suicidal": "{geas_suicidal}",
                },
                ("_spell_summary_rows", "_spell_detail_rows", "_order_save_rolls"),
            )
        ],
    )

    def insert(value):
        if isinstance(value, list):
            for i, e in enumerate(value):
                if (
                    isinstance(e, dict)
                    and e.get("condition") == "_spell_definition.family == 'timed_control_behavior'"
                ):
                    value.insert(i, dispatch)
                    return True
                if insert(e):
                    return True
        elif isinstance(value, dict):
            return any(insert(v) for v in value.values())
        return False

    if not insert(cast["effects"]):
        raise ValueError("Missing control dispatch")
    changed["cast_spell"] = cast
    for name, tool in changed.items():
        rendered = _render_tool(name, tool)
        if name == "cast_spell":
            rendered = rendered.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
            start = rendered.index(
                "      enum_values:\n", rendered.index("    - name: spell\n")
            ) + len("      enum_values:\n")
            end = rendered.index("      required:", start)
            rendered = (
                rendered[:start]
                + "        # BEGIN GENERATED SPELL SELECTOR\n"
                + "".join(
                    f"          - {v}\n"
                    for v in next(p for p in cast["parameters"] if p["name"] == "spell")[
                        "enum_values"
                    ]
                )
                + "        # END GENERATED SPELL SELECTOR\n"
                + rendered[end:]
            )
        pattern = (
            rf"(?ms)^  {re.escape(name)}:[^\n]*\n.*?"
            r"(?=^  [A-Za-z_][^\n]*:\s*|^procedures:|^definition_row_schemas:)"
        )
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _, v=rendered: v, text, count=1)
        else:
            text = text.replace("procedures: {}\n", rendered + "\nprocedures: {}\n", 1)
    if "      timed_control_orders:" not in text:
        schema = yaml.dump({"timed_control_orders": _schema()}, Dumper=_Dumper, sort_keys=False)
        text = text.replace(
            "      timed_control_behavior:",
            "".join("      " + line for line in schema.splitlines(True))
            + "\n      timed_control_behavior:",
            1,
        )
    return text
