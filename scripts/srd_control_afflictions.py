"""Durable afflictions share the package's condition, turn, damage and scene owners."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_befuddlement import compose_runtime as compose_befuddlement
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
    from srd_befuddlement import compose_runtime as compose_befuddlement
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


# Level, casting time, range kind, range, targets, duration, concentration, behavior.
PROFILES = {
    "eyebite": (6, "action", "self", 0, 1, 10, True, "eyebite"),
    "antipathy_sympathy": (8, "1_hour", "distance", 60, 1, 144000, False, "affinity"),
    "phantasmal_killer": (4, "action", "distance", 120, 1, 10, True, "nightmare"),
    "weird": (9, "action", "distance", 120, 8, 10, True, "weird"),
    "bestow_curse": (3, "action", "touch", 5, 1, 10, True, "curse"),
}
PROFILE_KEYS = (
    "base_level",
    "casting_time",
    "range_kind",
    "range_feet",
    "base_targets",
    "duration_rounds",
    "concentration",
    "behavior",
)
SOURCE_PROFILES = (
    "eyebite_sleep",
    "eyebite_panic",
    "eyebite_sick",
    "affinity_antipathy",
    "affinity_sympathy",
    "nightmare",
    "weird",
    "curse",
)
PUBLIC_TOOLS = {
    "retarget_srd_eyebite",
    "expose_srd_affinity",
    "resolve_srd_affliction_movement",
    "resolve_srd_fear_turn",
    "resolve_srd_confused_turn",
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
        spell not in PROFILES
        or tuple(definition.get(key) for key in PROFILE_KEYS) != PROFILES[spell]
        or definition.get("save_ability") != "wisdom"
    ):
        raise SystemExit(f"Incoherent control affliction profile: {spell}")


def _schema():
    properties = {
        "base_level": {"type": "integer", "minimum": 3, "maximum": 9},
        "casting_time": {"type": "string", "enum": ["action", "1_hour"]},
        "range_kind": {"type": "string", "enum": ["self", "distance", "touch"]},
        "range_feet": {"type": "integer", "minimum": 0, "maximum": 120},
        "base_targets": {"type": "integer", "minimum": 1, "maximum": 8},
        "duration_rounds": {"type": "integer", "minimum": 10, "maximum": 144000},
        "concentration": {"type": "boolean"},
        "behavior": {
            "type": "string",
            "enum": ["eyebite", "affinity", "nightmare", "weird", "curse"],
        },
        "save_ability": {"type": "string", "enum": ["wisdom"]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _refresh(target="_target_state_id"):
    return _call(
        "refresh_srd_timed_condition_target",
        {
            "target_state": "{" + target + "}",
            "lasting_state": "{_lasting_state_id}",
        },
    )


def _retire(source="_affliction_id"):
    return _call(
        "retire_srd_timed_condition_source",
        {
            "lasting_state": "{_lasting_state_id}",
            "source_id": "{" + source + "}",
        },
    )


def _loop(effects, actor="target_state"):
    return [
        _set("_affliction_ids", []),
        _when(
            f"{actor}.control_affliction_source_ids != null",
            [_set("_affliction_ids", f"{actor}.control_affliction_source_ids")],
        ),
        _each(
            "_affliction_ids",
            "_affliction_id",
            [
                _set("_affliction", "lasting_state.timed_condition_sources[_affliction_id]"),
                *effects,
            ],
        ),
    ]


def _save(target, source, condition, dc, behavior="affliction", forego=False):
    return _call(
        "resolve_srd_control_behavior_save",
        {
            "target_state": target,
            "source_state_id": source,
            "behavior": behavior,
            "condition": condition,
            "save_dc": dc,
            "forego": forego,
        },
        SAVE_EXPORTS,
    )


def _damage(target, caster, dice_count, damage_type="psychic"):
    return [
        {"dice": {"expression": "{" + dice_count + "}d10"}, "store_as": "_affliction_damage_roll"},
        _set("_affliction_damage", "_affliction_damage_roll.total"),
        _when(
            "_affliction_half_damage == true",
            [_set("_affliction_damage", "floor(_affliction_damage * 0.5) + 0")],
        ),
        _call(
            "apply_srd_damage",
            {
                "target": target,
                "amount": "{_affliction_damage}",
                "adjustment": 0,
                "damage_type": damage_type,
                "source_state_id": caster,
            },
            ("_effective_damage", "_is_alive_after"),
        ),
        _when(
            "_effective_damage > 0 and _is_alive_after == true",
            [
                _call(
                    "apply_srd_curse_damage_rider", {"target_state": target, "caster_state": caster}
                )
            ],
        ),
    ]


def _tools():
    tools = {}
    tools["initialize_srd_affliction_source"] = _tool(
        "Save a new affliction's damage, selected curse, casting identity and turn receipt.",
        [
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("cast_id", required=False, default=""),
            _param("damage_dice", "integer"),
            _param("curse_mode", required=False, default="ability"),
            _param("curse_ability", required=False, default="wisdom"),
            _param("duration_rounds", "integer"),
            _param("indefinite", "boolean", required=False, default=False),
        ],
        [
            _set("lasting_state.timed_condition_sources.{source_id}.affliction_cast_id", "cast_id"),
            _set("lasting_state.timed_condition_sources.{source_id}.damage_dice", "damage_dice"),
            _set("lasting_state.timed_condition_sources.{source_id}.curse_mode", "curse_mode"),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.curse_ability", "curse_ability"
            ),
            _set("lasting_state.timed_condition_sources.{source_id}.affliction_last_end_turn", -1),
            _set("lasting_state.timed_condition_sources.{source_id}.affliction_moved_turn", -1),
            _when(
                (
                    "lasting_state.timed_condition_sources[source_id].control_profile == "
                    "'curse' and curse_mode == 'damage'"
                ),
                [_set("lasting_state.has_affliction_damage_curses", True)],
            ),
            _when(
                "indefinite == true",
                [_set("lasting_state.timed_condition_sources.{source_id}.expires_tick", None)],
            ),
            _refresh(),
        ],
    )
    tools["resolve_srd_affliction_projection"] = _tool(
        "Project newest unsuppressed afflictions without discarding independent sources.",
        [_entity("target_state"), _lasting()],
        [
            _set("_affliction_selected", []),
            _set("_affliction_index", []),
            _set("_affliction_poisoned", False),
            _set("target_state.affliction_nightmare", False),
            _set("target_state.affliction_movement_pending", False),
            _when(
                "target_state.control_behavior_source_ids != null",
                [_set("_affliction_index", "target_state.control_behavior_source_ids")],
            ),
            _set("_affliction_profiles", ["'" + p + "'" for p in SOURCE_PROFILES]),
            _each(
                "_affliction_index",
                "_affliction_id",
                [
                    _set("_affliction", "lasting_state.timed_condition_sources[_affliction_id]"),
                    _when(
                        (
                            "_affliction.suppressed != true and _affliction.control_profile in "
                            "_affliction_profiles"
                        ),
                        [
                            _push("_affliction_selected", "_affliction_id"),
                            _when(
                                "_affliction.condition == 'poisoned'",
                                [_set("_affliction_poisoned", True)],
                            ),
                            _when(
                                "_affliction.control_profile == 'nightmare'",
                                [_set("target_state.affliction_nightmare", True)],
                            ),
                            _when(
                                (
                                    "(_affliction.control_profile == 'eyebite_panic' or "
                                    "_affliction.control_profile == 'affinity_antipathy' "
                                    "or _affliction.control_profile == "
                                    "'affinity_sympathy') and "
                                    "_affliction.affliction_moved_turn != "
                                    "target_state.turns_started"
                                ),
                                [
                                    _set("_affliction_needs_move", True),
                                    _when(
                                        "_affliction.control_profile == 'affinity_sympathy'",
                                        [
                                            _call(
                                                "resolve_srd_scene_geometry_relation",
                                                {
                                                    "actor_state": "{_target_state_id}",
                                                    "target_state": "{_affliction.caster_state_id}",
                                                },
                                                ("_scene_distance",),
                                            ),
                                            _set("_affliction_needs_move", "_scene_distance > 5"),
                                        ],
                                    ),
                                    _when(
                                        "_affliction_needs_move == true",
                                        [_set("target_state.affliction_movement_pending", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("target_state.control_affliction_source_ids", "_affliction_selected"),
            _set("target_state.has_control_affliction_sources", "len(_affliction_selected) > 0"),
            _when(
                "target_state.timed_condition_unowned_poisoned != null",
                [
                    {"remove_condition": {"target": "target_state", "condition": "poisoned"}},
                    _when(
                        (
                            "_affliction_poisoned == true or "
                            "target_state.timed_condition_unowned_poisoned == true or "
                            "target_state.turn_effect_poisoned == true"
                        ),
                        [
                            {
                                "apply_condition": {
                                    "target": "target_state",
                                    "condition": "poisoned",
                                }
                            },
                        ],
                    ),
                ],
            ),
        ],
    )
    tools["resolve_srd_affliction_roll"] = _tool(
        "Derive Phantasmal Killer and selected Bestow Curse D20 Disadvantage.",
        [
            _entity("target_state"),
            _lasting(),
            _param("kind"),
            _param("ability", required=False, default=""),
            _param("attack_target_id", required=False, default=""),
        ],
        [
            _set("_affliction_disadvantage", False),
            *_loop(
                [
                    _when(
                        (
                            "_affliction.condition == 'frightened' and (kind == 'ability_check' or "
                            "kind == 'attack_roll')"
                        ),
                        [
                            _call(
                                "resolve_srd_scene_relation",
                                {
                                    "actor_state": "{_target_state_id}",
                                    "target_state": "{_affliction.caster_state_id}",
                                },
                                ("_scene_line_of_sight",),
                            ),
                            _when(
                                "_scene_line_of_sight == true",
                                [_set("_affliction_disadvantage", True)],
                            ),
                        ],
                    ),
                    _when(
                        (
                            "_affliction.control_profile == 'nightmare' and (kind == "
                            "'ability_check' or kind == 'attack_roll')"
                        ),
                        [_set("_affliction_disadvantage", True)],
                    ),
                    _when(
                        "_affliction.control_profile == 'curse'",
                        [
                            _when(
                                (
                                    "_affliction.curse_mode == 'ability' and ability == "
                                    "_affliction.curse_ability and (kind == "
                                    "'ability_check' or kind == 'saving_throw')"
                                ),
                                [_set("_affliction_disadvantage", True)],
                            ),
                            _when(
                                (
                                    "_affliction.curse_mode == 'attacks' and kind == "
                                    "'attack_roll' and attack_target_id == "
                                    "_affliction.caster_state_id"
                                ),
                                [_set("_affliction_disadvantage", True)],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    tools["apply_srd_curse_damage_rider"] = _tool(
        (
            "Apply the selected curse's extra Necrotic damage at actual attack and "
            "spell damage owners."
        ),
        [_entity("target_state"), _entity("caster_state"), _lasting()],
        [
            _when(
                "target_state.has_control_affliction_sources == true",
                [
                    *_loop(
                        [
                            _when(
                                (
                                    "_affliction.control_profile == 'curse' and "
                                    "_affliction.curse_mode == 'damage' and "
                                    "_affliction.caster_state_id == _caster_state_id"
                                ),
                                [
                                    {
                                        "dice": {"expression": "1d8"},
                                        "store_as": "_curse_extra_roll",
                                    },
                                    _call(
                                        "apply_srd_damage",
                                        {
                                            "target": "{_target_state_id}",
                                            "amount": "{_curse_extra_roll.total}",
                                            "adjustment": 0,
                                            "damage_type": "necrotic",
                                            "source_state_id": "{_caster_state_id}",
                                        },
                                    ),
                                    _set(
                                        "target_state.last_curse_damage_roll", "_curse_extra_roll"
                                    ),
                                ],
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )
    tools["start_srd_affliction_turn"] = _tool(
        "Resolve a forced Dodge saving throw and renew source-owned compulsory movement.",
        [_entity("target_state"), _lasting()],
        [
            *_loop(
                [
                    _when(
                        (
                            "_affliction.control_profile == 'curse' and _affliction.curse_mode == "
                            "'dodge'"
                        ),
                        [
                            _save(
                                "{_target_state_id}",
                                "{_affliction.caster_state_id}",
                                "none",
                                "{_affliction.save_dc}",
                            ),
                            _set("target_state.last_curse_turn_save", "_control_save_roll"),
                            _when(
                                (
                                    "_control_save_outcome == 'failure' and "
                                    "combat.active_actor_can_act == true and "
                                    "combat.action_available == true"
                                ),
                                [
                                    _call(
                                        "spend_srd_action",
                                        {
                                            "magic_action": False,
                                            "control_action_kind": "curse_dodge",
                                        },
                                    ),
                                    _set("target_state.dodging", True),
                                    _set("combat.action_taken", "'dodge'"),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
            _call(
                "resolve_srd_affliction_projection",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
        ],
    )
    tools["end_srd_affliction_turn"] = _tool(
        (
            "Repeat saved nightmare/Weird saves and release affinity beyond its "
            "exact 120-foot threshold."
        ),
        [_entity("target_state"), _lasting()],
        [
            _require(
                "target_state.affliction_movement_pending != true",
                "Resolve compulsory affliction movement before ending the turn.",
            ),
            *_loop(
                [
                    _when(
                        "_affliction.affliction_last_end_turn != target_state.turns_started",
                        [
                            _set(
                                (
                                    "lasting_state.timed_condition_sources.{_affliction_id}.a"
                                    "ffliction_last_end_turn"
                                ),
                                "target_state.turns_started",
                            ),
                            _when(
                                (
                                    "_affliction.control_profile == 'nightmare' or "
                                    "_affliction.control_profile == 'weird'"
                                ),
                                [
                                    _save(
                                        "{_target_state_id}",
                                        "{_affliction.caster_state_id}",
                                        "{_affliction.condition}",
                                        "{_affliction.save_dc}",
                                    ),
                                    _set(
                                        "target_state.last_affliction_save_roll",
                                        "_control_save_roll",
                                    ),
                                    _set(
                                        "target_state.last_affliction_save_outcome",
                                        "_control_save_outcome",
                                    ),
                                    _when(
                                        "_control_save_outcome == 'success'",
                                        [
                                            _when(
                                                "_affliction.control_profile == 'nightmare'",
                                                [
                                                    _call(
                                                        "end_srd_affliction_concentration",
                                                        {
                                                            "caster_state": (
                                                                "{_affliction.caster_state_id}"
                                                            ),
                                                            "sequence": (
                                                                "{_affliction.concentration_s"
                                                                "equence}"
                                                            ),
                                                        },
                                                    )
                                                ],
                                            ),
                                            _retire(),
                                        ],
                                        [
                                            _set("_affliction_half_damage", False),
                                            *_damage(
                                                "{_target_state_id}",
                                                "{_affliction.caster_state_id}",
                                                "_affliction.damage_dice",
                                            ),
                                            _set(
                                                "target_state.last_affliction_damage_roll",
                                                "_affliction_damage_roll",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                (
                                    "_affliction.control_profile == 'affinity_antipathy' or "
                                    "_affliction.control_profile == 'affinity_sympathy'"
                                ),
                                [
                                    _call(
                                        "resolve_srd_scene_relation",
                                        {
                                            "actor_state": "{_target_state_id}",
                                            "target_state": "{_affliction.caster_state_id}",
                                        },
                                        ("_scene_distance",),
                                    ),
                                    _when(
                                        "_scene_distance > 120",
                                        [
                                            _call(
                                                "save_srd_affinity_release",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "source_id": "{_affliction_id}",
                                                },
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
            _refresh(),
        ],
    )
    tools["end_srd_affliction_concentration"] = _tool(
        "End only the exact concentration casting whose single nightmare has ended.",
        [_entity("caster_state"), _param("sequence", "integer")],
        [
            _when(
                (
                    "caster_state.concentration_spell_id == 'phantasmal_killer' and "
                    "caster_state.concentration_sequence == sequence"
                ),
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{caster_state.concentration_target_state_id}",
                        },
                    ),
                ],
            )
        ],
    )
    tools["check_srd_control_scene_move"] = _tool(
        (
            "Prevent willing retreat from a nearby Sympathy anchor and willing "
            "approach toward a saved fear source."
        ),
        [
            _entity("actor_state"),
            _lasting(),
            _param("destination", "array", items={"type": "number"}, min_items=3, max_items=3),
        ],
        [
            _set("_control_move_ids", "actor_state.control_behavior_source_ids"),
            _each(
                "_control_move_ids",
                "_control_move_id",
                [
                    _set(
                        "_control_move_source",
                        "lasting_state.timed_condition_sources[_control_move_id]",
                    ),
                    _when(
                        (
                            "_control_move_source.suppressed != true and "
                            "(_control_move_source.condition == 'frightened' or "
                            "_control_move_source.control_profile == 'affinity_sympathy')"
                        ),
                        [
                            _call(
                                "resolve_srd_scene_relation",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "target_state": "{_control_move_source.caster_state_id}",
                                },
                                ("_scene_distance", "_scene_target_position"),
                            ),
                            _set(
                                "_control_destination_distance",
                                (
                                    "((destination[0] - _scene_target_position[0]) ** 2 +"
                                    " (destination[1] - _scene_target_position[1]) ** 2 +"
                                    " (destination[2] - _scene_target_position[2]) ** 2) "
                                    "** 0.5"
                                ),
                            ),
                            _require(
                                (
                                    "_control_move_source.condition != 'frightened' or "
                                    "_control_destination_distance >= _scene_distance"
                                ),
                                (
                                    "A Frightened creature cannot willingly move closer "
                                    "to its actual fear source."
                                ),
                            ),
                            _require(
                                (
                                    "_control_move_source.control_profile != "
                                    "'affinity_sympathy' or _scene_distance > 5 or "
                                    "_control_destination_distance <= _scene_distance"
                                ),
                                (
                                    "Sympathy prevents willing movement away while "
                                    "within5feet of the actual anchor."
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    tools.update(_eyebite_tools())
    tools.update(_affinity_tools())
    tools["cast_srd_control_afflictions"] = _cast_tool()
    tools["resolve_srd_affliction_movement"] = _movement_tool()
    tools.update(_scene_control_tools())
    return tools


def _cast_tool():
    return _tool(
        (
            "Resolve five fixed SRD affliction profiles, preserving casting cost "
            "for ineligible anchors."
        ),
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
                max_items=8,
                unique_items=True,
            ),
            _param("targets_max_distance", "integer"),
            _param("caster_can_see_all_targets", "boolean"),
            _param("all_targets_have_clear_path", "boolean"),
            _param(
                "eyebite_mode",
                "enum",
                enum_values=["asleep", "panicked", "sickened"],
                required=False,
                default="asleep",
            ),
            _param(
                "affinity_mode",
                "enum",
                enum_values=["antipathy", "sympathy"],
                required=False,
                default="antipathy",
            ),
            _param("affinity_kind", required=False, default=""),
            _param(
                "curse_mode",
                "enum",
                enum_values=["ability", "attacks", "dodge", "damage"],
                required=False,
                default="ability",
            ),
            _param(
                "curse_ability",
                "enum",
                enum_values=[
                    "strength",
                    "dexterity",
                    "constitution",
                    "intelligence",
                    "wisdom",
                    "charisma",
                ],
                required=False,
                default="wisdom",
            ),
            _param(
                "forego_save_targets",
                "entity_id_list",
                resolve="entity_list",
                max_items=8,
                required=False,
                default=[],
            ),
        ],
        [
            _set("_affliction_definition", "definitions.spells[spell]"),
            _set("_affliction_range", "_affliction_definition.range_feet"),
            _when("spell == 'eyebite'", [_set("_affliction_range", 60)]),
            _require(
                (
                    "len(targets) <= _affliction_definition.base_targets and "
                    "targets_max_distance <= _affliction_range and "
                    "all_targets_have_clear_path == true"
                ),
                (
                    "Affliction targets exceed their fixed range, target bound or "
                    "clear-path requirement."
                ),
            ),
            _require(
                (
                    "spell == 'bestow_curse' or spell == 'antipathy_sympathy' or spell == "
                    "'weird' or caster_can_see_all_targets == true"
                ),
                "This spell requires sight of its creature target.",
            ),
            _set("_affliction_duration", "_affliction_definition.duration_rounds"),
            _set("_affliction_concentration", "_affliction_definition.concentration"),
            _when(
                "spell == 'bestow_curse'",
                [
                    _when("slot_level == 4", [_set("_affliction_duration", 100)]),
                    _when(
                        "slot_level >= 5",
                        [
                            _set("_affliction_concentration", False),
                            _set("_affliction_duration", 4800),
                        ],
                    ),
                    _when("slot_level >= 7", [_set("_affliction_duration", 14400)]),
                ],
            ),
            _set("_condition_concentration_sequence", 0),
            _when(
                "_affliction_concentration == true",
                [
                    _call(
                        "start_srd_condition_concentration",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "targets": "{targets}",
                            "spell": "{spell}",
                            "duration_rounds": "{_affliction_duration}",
                            "save_modifier": "{concentration_save_modifier}",
                        },
                    )
                ],
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _set("_affliction_save_rolls", []),
            _set("_affliction_first_target", "targets[0]"),
            _when(
                "spell == 'eyebite'",
                [
                    _set("caster_state.eyebite_saved_success_ids", []),
                    _set("caster_state.eyebite_cast_sequence", "_condition_concentration_sequence"),
                    _set("caster_state.eyebite_save_dc", "save_dc"),
                    _set("caster_state.eyebite_last_retarget_turn", -1),
                    _when(
                        "caster_state.turns_started != null",
                        [
                            _set(
                                "caster_state.eyebite_last_retarget_turn",
                                "caster_state.turns_started",
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'antipathy_sympathy'",
                [
                    _require(
                        "len(affinity_kind) > 0 and len(affinity_kind) <= 120",
                        "Specify the kind of creature affected by Antipathy/Sympathy.",
                    ),
                    _call(
                        "install_srd_affinity_anchor",
                        {
                            "caster_state": "{_caster_state_id}",
                            "anchor_state": "{_affliction_first_target}",
                            "affinity_mode": "{affinity_mode}",
                            "affinity_kind": "{affinity_kind}",
                            "save_dc": "{save_dc}",
                        },
                        ("_affinity_cast_id", "_affinity_anchor_valid"),
                    ),
                    _push(
                        "_spell_summary_rows",
                        {"label": "'Antipathy/Sympathy anchor'", "value": "_affinity_anchor_valid"},
                    ),
                ],
                [
                    _each(
                        "targets",
                        "_affliction_target",
                        [
                            _call(
                                "validate_srd_timed_condition_target",
                                {
                                    "target_state": "{_affliction_target}",
                                    "save_ability": "wisdom",
                                    "required_creature_type": "any",
                                },
                            ),
                            _set("_affliction_condition", "'none'"),
                            _set("_affliction_profile", "_affliction_definition.behavior"),
                            _set("_affliction_dice", 0),
                            _when(
                                "spell == 'weird'",
                                [
                                    _set("_affliction_condition", "'frightened'"),
                                    _set("_affliction_dice", 10),
                                ],
                            ),
                            _when(
                                "spell == 'phantasmal_killer'",
                                [_set("_affliction_dice", "4 + slot_level - 4")],
                            ),
                            _when(
                                "spell == 'eyebite'",
                                [
                                    _call(
                                        "select_srd_eyebite_mode",
                                        {"eyebite_mode": "{eyebite_mode}"},
                                        ("_affliction_condition", "_affliction_profile"),
                                    )
                                ],
                            ),
                            _set("_affliction_forego", "_affliction_target in forego_save_targets"),
                            _save(
                                "{_affliction_target}",
                                "{_caster_state_id}",
                                "{_affliction_condition}",
                                "{save_dc}",
                                behavior="{_affliction_profile}",
                                forego="{_affliction_forego}",
                            ),
                            _when(
                                "_control_save_roll != null",
                                [_push("_affliction_save_rolls", "_control_save_roll")],
                                [_push("_affliction_save_rolls", None)],
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
                                "spell == 'eyebite' and _control_save_outcome == 'success'",
                                [
                                    _call(
                                        "remember_srd_eyebite_success",
                                        {
                                            "caster_state": "{_caster_state_id}",
                                            "target_id": "{_affliction_target}",
                                        },
                                    )
                                ],
                            ),
                            _when(
                                "_affliction_dice > 0",
                                [
                                    _set(
                                        "_affliction_half_damage",
                                        "_control_save_outcome == 'success'",
                                    ),
                                    *_damage(
                                        "{_affliction_target}",
                                        "{_caster_state_id}",
                                        "_affliction_dice",
                                    ),
                                ],
                            ),
                            _when(
                                (
                                    "_control_save_outcome == 'failure' and "
                                    "_control_save_immune != true"
                                ),
                                [
                                    _call(
                                        "install_srd_timed_condition_source",
                                        {
                                            "target_state": "{_affliction_target}",
                                            "caster_state": "{_caster_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                            "spell": "{spell}",
                                            "condition": "{_affliction_condition}",
                                            "control_profile": "{_affliction_profile}",
                                            "duration_rounds": "{_affliction_duration}",
                                            "save_dc": "{save_dc}",
                                            "repeat_save_timing": "none",
                                            "save_ability": "wisdom",
                                            "concentration_sequence": (
                                                "{_condition_concentration_sequence}"
                                            ),
                                        },
                                    ),
                                    _when("spell == 'weird'", [_set("_affliction_dice", 5)]),
                                    _set(
                                        "_affliction_indefinite",
                                        "spell == 'bestow_curse' and slot_level == 9",
                                    ),
                                    _call(
                                        "initialize_srd_affliction_source",
                                        {
                                            "target_state": "{_affliction_target}",
                                            "source_id": "{_timed_condition_source_id}",
                                            "damage_dice": "{_affliction_dice}",
                                            "curse_mode": "{curse_mode}",
                                            "curse_ability": "{curse_ability}",
                                            "duration_rounds": "{_affliction_duration}",
                                            "indefinite": "{_affliction_indefinite}",
                                        },
                                    ),
                                    _when(
                                        "_affliction_profile == 'eyebite_sleep'",
                                        [
                                            _call(
                                                "drop_srd_control_held_items",
                                                {"target_state": "{_affliction_target}"},
                                            ),
                                            _call(
                                                "apply_srd_condition",
                                                {
                                                    "target": "{_affliction_target}",
                                                    "condition": "prone",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                (
                                    "spell == 'phantasmal_killer' and _control_save_outcome "
                                    "== 'success'"
                                ),
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_caster_state_id}",
                                            "target_state": "{_affliction_first_target}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _eyebite_tools():
    select = _tool(
        "Select one source-defined Eyebite condition.",
        [_param("eyebite_mode")],
        [
            _set("_affliction_condition", "'unconscious'"),
            _set("_affliction_profile", "'eyebite_sleep'"),
            _when(
                "eyebite_mode == 'panicked'",
                [
                    _set("_affliction_condition", "'frightened'"),
                    _set("_affliction_profile", "'eyebite_panic'"),
                ],
            ),
            _when(
                "eyebite_mode == 'sickened'",
                [
                    _set("_affliction_condition", "'poisoned'"),
                    _set("_affliction_profile", "'eyebite_sick'"),
                ],
            ),
        ],
    )
    remember = _tool(
        "Remember successful saves for this exact Eyebite casting.",
        [_entity("caster_state"), _param("target_id")],
        [
            _set("_eyebite_successes", "caster_state.eyebite_saved_success_ids"),
            _when(
                "target_id not in _eyebite_successes", [_push("_eyebite_successes", "target_id")]
            ),
            _set("caster_state.eyebite_saved_success_ids", "_eyebite_successes"),
        ],
    )
    retarget = _tool(
        (
            "Spend a later Magic action for this Eyebite casting, forbidding "
            "targets that already saved."
        ),
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("eyebite_mode", "enum", enum_values=["asleep", "panicked", "sickened"]),
            _param("forego_save", "boolean", required=False, default=False),
        ],
        [
            _call(
                "refresh_srd_timed_condition_target",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _require(
                (
                    "caster_state.concentration_spell_id == 'eyebite' and "
                    "caster_state.concentration_sequence == "
                    "caster_state.eyebite_cast_sequence and lasting_state.lasting_tick < "
                    "caster_state.concentration_expires_tick"
                ),
                "This Eyebite casting has ended.",
            ),
            _require(
                "_target_state_id not in caster_state.eyebite_saved_success_ids",
                "The creature already succeeded against this exact Eyebite casting.",
            ),
            _require(
                "caster_state.eyebite_last_retarget_turn != caster_state.turns_started",
                "Eyebite can retarget only once on each of your turns.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ("_scene_distance", "_scene_can_see", "_scene_same_plane", "_scene_line_of_sight"),
            ),
            _require(
                (
                    "_scene_same_plane == true and _scene_can_see == true and "
                    "_scene_line_of_sight == true and _scene_distance <= 60"
                ),
                "Eyebite requires a visible creature within 60 feet and a clear path.",
            ),
            _call("spend_srd_action", {"magic_action": True}),
            _set("caster_state.eyebite_last_retarget_turn", "caster_state.turns_started"),
            _call(
                "select_srd_eyebite_mode",
                {"eyebite_mode": "{eyebite_mode}"},
                ("_affliction_condition", "_affliction_profile"),
            ),
            _save(
                "{_target_state_id}",
                "{_caster_state_id}",
                "{_affliction_condition}",
                "{caster_state.eyebite_save_dc}",
                behavior="{_affliction_profile}",
                forego="{forego_save}",
            ),
            _when(
                "_control_save_outcome == 'success'",
                [
                    _call(
                        "remember_srd_eyebite_success",
                        {"caster_state": "{_caster_state_id}", "target_id": "{_target_state_id}"},
                    )
                ],
            ),
            _when(
                "_control_save_outcome == 'failure' and _control_save_immune != true",
                [
                    _set("_eyebite_targets", "caster_state.concentration_target_state_ids"),
                    _when(
                        "_target_state_id not in _eyebite_targets",
                        [_push("_eyebite_targets", "_target_state_id")],
                    ),
                    _set("caster_state.concentration_target_state_ids", "_eyebite_targets"),
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "spell": "eyebite",
                            "condition": "{_affliction_condition}",
                            "control_profile": "{_affliction_profile}",
                            "duration_rounds": 10,
                            "save_dc": "{caster_state.eyebite_save_dc}",
                            "repeat_save_timing": "none",
                            "save_ability": "wisdom",
                            "concentration_sequence": "{caster_state.concentration_sequence}",
                        },
                    ),
                    _set(
                        (
                            "lasting_state.timed_condition_sources.{_timed_condition_source_i"
                            "d}.expires_tick"
                        ),
                        "caster_state.concentration_expires_tick",
                    ),
                    _call(
                        "initialize_srd_affliction_source",
                        {
                            "target_state": "{_target_state_id}",
                            "source_id": "{_timed_condition_source_id}",
                            "damage_dice": 0,
                            "duration_rounds": 10,
                        },
                    ),
                    _when(
                        "eyebite_mode == 'asleep'",
                        [
                            _call(
                                "drop_srd_control_held_items",
                                {"target_state": "{_target_state_id}"},
                            ),
                            _call(
                                "apply_srd_condition",
                                {"target": "{_target_state_id}", "condition": "prone"},
                            ),
                        ],
                    ),
                ],
            ),
            _set("caster_state.last_eyebite_save_roll", "_control_save_roll"),
        ],
        [
            {
                "condition": (
                    "combat.turn_active == true and combat.active_actor == _caster_state_id"
                    " and combat.active_actor_can_act == true"
                ),
                "error": "Retarget Eyebite only during the caster's actual active turn.",
            }
        ],
        public=True,
    )
    return {
        "select_srd_eyebite_mode": select,
        "remember_srd_eyebite_success": remember,
        "retarget_srd_eyebite": retarget,
    }


def _affinity_tools():
    install = _tool(
        (
            "Save the ten-day anchor, chosen creature kind and immunity receipts "
            "for one affinity casting."
        ),
        [
            _entity("caster_state"),
            _entity("anchor_state"),
            _lasting(),
            _param("affinity_mode"),
            _param("affinity_kind"),
            _param("save_dc", "integer"),
        ],
        [
            _set("_affinity_sizes", ["'tiny'", "'small'", "'medium'", "'large'", "'huge'"]),
            _require(
                "anchor_state.size != null",
                "Save the anchor's actual size before selecting Antipathy/Sympathy.",
            ),
            _set("_affinity_anchor_valid", "anchor_state.size in _affinity_sizes"),
            _set("_affinity_cast_id", "''"),
            _when(
                "_affinity_anchor_valid == true",
                [
                    _when(
                        "lasting_state.affinity_serial == null",
                        [
                            _set("lasting_state.affinity_serial", 0),
                            _set("lasting_state.affinity_casts", {}),
                            _set("lasting_state.affinity_cast_ids", []),
                            _set(
                                "lasting_state.affinity_free_ids",
                                [f"'affinity_{i:02}'" for i in range(64)],
                            ),
                        ],
                    ),
                    _call("prune_srd_affinity_anchors", {"lasting_state": "{_lasting_state_id}"}),
                    _require(
                        "len(lasting_state.affinity_cast_ids) < 64",
                        "Resolve expired affinity anchors before adding another casting.",
                    ),
                    {"increment": {"field": "lasting_state.affinity_serial", "amount": 1}},
                    _set("_affinity_free_ids", "lasting_state.affinity_free_ids"),
                    {"list_pop": {"list": "_affinity_free_ids"}, "store_as": "_affinity_cast_id"},
                    _set("lasting_state.affinity_free_ids", "_affinity_free_ids"),
                    _set(
                        "lasting_state.affinity_casts.{_affinity_cast_id}",
                        {
                            "anchor_id": "_anchor_state_id",
                            "creator_id": "_caster_state_id",
                            "kind": "affinity_kind",
                            "mode": "affinity_mode",
                            "save_dc": "save_dc",
                            "expires_tick": "lasting_state.lasting_tick + 288000",
                            "saved_until": {},
                        },
                    ),
                    _set("_affinity_cast_ids", "lasting_state.affinity_cast_ids"),
                    _push("_affinity_cast_ids", "_affinity_cast_id"),
                    _set("lasting_state.affinity_cast_ids", "_affinity_cast_ids"),
                    _set("anchor_state.last_affinity_cast_id", "_affinity_cast_id"),
                ],
            ),
        ],
    )
    expose = _tool(
        "Use actual 120-foot proximity, saved kind facts and per-casting one-minute immunity.",
        [
            _entity("target_state"),
            _entity("anchor_state"),
            _lasting(),
            _param("cast_id"),
            _param("matches_kind", "boolean", required=False, default=False),
            _param("forego_save", "boolean", required=False, default=False),
        ],
        [
            _require(
                "lasting_state.affinity_casts != null and cast_id in lasting_state.affinity_casts",
                "Select an existing affinity casting identity.",
            ),
            _set("_affinity_cast", "lasting_state.affinity_casts[cast_id]"),
            _require(
                (
                    "_affinity_cast != null and _affinity_cast.anchor_id == "
                    "_anchor_state_id and lasting_state.lasting_tick < "
                    "_affinity_cast.expires_tick"
                ),
                "There is no active matching Antipathy/Sympathy anchor.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_target_state_id}", "target_state": "{_anchor_state_id}"},
                ("_scene_distance", "_scene_same_plane"),
            ),
            _require(
                "_scene_same_plane == true and _scene_distance <= 120",
                "Affinity begins only within 120 feet of its actual anchor.",
            ),
            _set("_affinity_matches", "matches_kind"),
            _when(
                "target_state.creature_kind_tags != null",
                [
                    _set(
                        "_affinity_matches",
                        "_affinity_cast.kind in target_state.creature_kind_tags",
                    )
                ],
            ),
            _when(
                "target_state.creature_type == _affinity_cast.kind",
                [_set("_affinity_matches", True)],
            ),
            _require(
                "_affinity_matches == true",
                "The creature does not match this casting's chosen kind.",
            ),
            _require(
                (
                    "_target_state_id not in _affinity_cast.saved_until or "
                    "lasting_state.lasting_tick >= "
                    "_affinity_cast.saved_until[_target_state_id]"
                ),
                "A successful save grants one minute of immunity to this exact affinity casting.",
            ),
            _refresh(),
            _set("_affinity_already", False),
            *_loop(
                [
                    _when(
                        "_affliction.affliction_cast_id == cast_id",
                        [_set("_affinity_already", True)],
                    )
                ]
            ),
            _require(
                "_affinity_already != true",
                "The creature is already affected by this exact affinity casting.",
            ),
            _set("_affliction_condition", "'frightened'"),
            _set("_affliction_profile", "'affinity_antipathy'"),
            _when(
                "_affinity_cast.mode == 'sympathy'",
                [
                    _set("_affliction_condition", "'charmed'"),
                    _set("_affliction_profile", "'affinity_sympathy'"),
                ],
            ),
            _save(
                "{_target_state_id}",
                "{_anchor_state_id}",
                "{_affliction_condition}",
                "{_affinity_cast.save_dc}",
                forego="{forego_save}",
            ),
            _when(
                "_control_save_outcome == 'success'",
                [
                    _set(
                        "lasting_state.affinity_casts.{cast_id}.saved_until.{_target_state_id}",
                        "lasting_state.lasting_tick + 20",
                    )
                ],
            ),
            _when(
                "_control_save_outcome == 'failure' and _control_save_immune != true",
                [
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_anchor_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "spell": "antipathy_sympathy",
                            "condition": "{_affliction_condition}",
                            "control_profile": "{_affliction_profile}",
                            "duration_rounds": 144000,
                            "save_dc": "{_affinity_cast.save_dc}",
                            "repeat_save_timing": "none",
                            "save_ability": "wisdom",
                        },
                    ),
                    _set(
                        (
                            "lasting_state.timed_condition_sources.{_timed_condition_source_i"
                            "d}.expires_tick"
                        ),
                        "_affinity_cast.expires_tick",
                    ),
                    _call(
                        "initialize_srd_affliction_source",
                        {
                            "target_state": "{_target_state_id}",
                            "source_id": "{_timed_condition_source_id}",
                            "cast_id": "{cast_id}",
                            "damage_dice": 0,
                            "duration_rounds": 144000,
                        },
                    ),
                ],
            ),
            _set("target_state.last_affinity_save_roll", "_control_save_roll"),
        ],
        public=True,
    )
    release = _tool(
        "Repeat an affinity save and preserve the successful one-minute immunity receipt.",
        [_entity("target_state"), _lasting(), _param("source_id")],
        [
            _set("_releasing_affinity", "lasting_state.timed_condition_sources[source_id]"),
            _save(
                "{_target_state_id}",
                "{_releasing_affinity.caster_state_id}",
                "{_releasing_affinity.condition}",
                "{_releasing_affinity.save_dc}",
            ),
            _set("target_state.last_affinity_save_roll", "_control_save_roll"),
            _when(
                "_control_save_outcome == 'success'",
                [
                    _set(
                        (
                            "lasting_state.affinity_casts.{_releasing_affinity.affliction_cas"
                            "t_id}.saved_until.{_target_state_id}"
                        ),
                        "lasting_state.lasting_tick + 20",
                    ),
                    _retire("source_id"),
                ],
            ),
        ],
    )
    prune = _tool(
        "Retire expired affinity anchors and reuse their bounded casting identities.",
        [_lasting()],
        [
            _set("_affinity_kept_ids", []),
            _set("_affinity_existing_ids", "lasting_state.affinity_cast_ids"),
            _set("_affinity_free_ids", "lasting_state.affinity_free_ids"),
            _each(
                "_affinity_existing_ids",
                "_affinity_existing_id",
                [
                    _when(
                        (
                            "lasting_state.lasting_tick < "
                            "lasting_state.affinity_casts[_affinity_existing_id].expires_tick"
                        ),
                        [_push("_affinity_kept_ids", "_affinity_existing_id")],
                        [
                            _set("lasting_state.affinity_casts.{_affinity_existing_id}", None),
                            _when(
                                "_affinity_existing_id not in _affinity_free_ids",
                                [_push("_affinity_free_ids", "_affinity_existing_id")],
                            ),
                        ],
                    ),
                ],
            ),
            _set("lasting_state.affinity_cast_ids", "_affinity_kept_ids"),
            _set("lasting_state.affinity_free_ids", "_affinity_free_ids"),
        ],
    )
    return {
        "install_srd_affinity_anchor": install,
        "expose_srd_affinity": expose,
        "save_srd_affinity_release": release,
        "prune_srd_affinity_anchors": prune,
    }


def _movement_tool():
    return _tool(
        (
            "Spend actual compulsory movement through the admitted scene, retaining"
            " route-safety adjudication."
        ),
        [
            _entity("actor_state"),
            _entity("source_state"),
            _lasting(),
            _param("source_id"),
            _param("destination", "array", items={"type": "number"}, min_items=3, max_items=3),
            _param("safe_route", "boolean"),
            _param("shortest_route", "boolean", required=False, default=False),
            _param("blocked", "boolean", required=False, default=False),
            _param("difficult_terrain", "boolean", required=False, default=False),
        ],
        [
            _refresh("_actor_state_id"),
            _set("_moving_affliction", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                (
                    "source_id in actor_state.control_affliction_source_ids and "
                    "_moving_affliction.caster_state_id == _source_state_id and "
                    "_moving_affliction.affliction_moved_turn != actor_state.turns_started"
                ),
                "Select an unresolved active movement source belonging to this actor and source.",
            ),
            _set(
                "_moving_profiles",
                ["'eyebite_panic'", "'affinity_antipathy'", "'affinity_sympathy'"],
            ),
            _require(
                "_moving_affliction.control_profile in _moving_profiles and safe_route == true",
                "This source requires movement through a safe route.",
            ),
            _require(
                "_moving_affliction.control_profile != 'eyebite_panic' or shortest_route == true",
                "Panicked Eyebite requires the shortest safe route.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_actor_state_id}", "target_state": "{_source_state_id}"},
                ("_scene_distance", "_scene_same_plane"),
            ),
            _require(
                "_scene_same_plane == true",
                "This movement requires source and affected creature on the same admitted plane.",
            ),
            _set("_affliction_start_distance", "_scene_distance"),
            _when(
                "_moving_affliction.control_profile == 'eyebite_panic'",
                [
                    _call(
                        "spend_srd_action",
                        {"magic_action": False, "control_action_kind": "affliction_dash"},
                    ),
                    _call("apply_srd_dash", {"actor_state": "{_actor_state_id}"}),
                ],
            ),
            _when(
                "blocked != true",
                [
                    _require(
                        "combat.movement_remaining > 0",
                        "There is no movement remaining for the compelled route.",
                    ),
                    _set("actor_state.control_order_moving", True),
                    _call(
                        "move_srd_scene_creature",
                        {
                            "actor_state": "{_actor_state_id}",
                            "destination": "{destination}",
                            "difficult_terrain": "{difficult_terrain}",
                        },
                        ("_scene_movement_distance", "_scene_movement_cost"),
                    ),
                    _set("actor_state.control_order_moving", False),
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_actor_state_id}", "target_state": "{_source_state_id}"},
                        ("_scene_distance", "_scene_can_see"),
                    ),
                    _require(
                        (
                            "(_moving_affliction.control_profile == 'affinity_sympathy' and "
                            "_scene_distance < _affliction_start_distance) or "
                            "(_moving_affliction.control_profile != 'affinity_sympathy' and "
                            "_scene_distance > _affliction_start_distance)"
                        ),
                        (
                            "The compelled route must approach or retreat from its actual saved "
                            "source."
                        ),
                    ),
                    _require(
                        (
                            "combat.movement_remaining == 0 or (_moving_affliction.control_profile "
                            "== 'affinity_sympathy' and _scene_distance <= 5) or "
                            "(_moving_affliction.control_profile == 'eyebite_panic' and "
                            "_scene_distance >= 60 and _scene_can_see != true)"
                        ),
                        (
                            "Use all available compelled movement unless its source-defined "
                            "stopping condition is reached."
                        ),
                    ),
                    _when(
                        (
                            "_moving_affliction.control_profile == 'eyebite_panic' and "
                            "_scene_distance >= 60 and _scene_can_see != true"
                        ),
                        [_retire("source_id")],
                    ),
                ],
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.affliction_moved_turn",
                "actor_state.turns_started",
            ),
            _refresh("_actor_state_id"),
        ],
        [
            {
                "condition": (
                    "combat.turn_active == true and combat.active_actor == _actor_state_id"
                ),
                "error": "Resolve affliction movement only on the affected creature's actual turn.",
            }
        ],
        public=True,
    )


def _scene_control_tools():
    destination = _param(
        "destination", "array", items={"type": "number"}, required=False, default=[], max_items=3
    )
    fear = _tool(
        (
            "Spend Fear's real Dash and scene movement, deriving source line of "
            "sight at the final position."
        ),
        [
            _entity("actor_state"),
            _lasting(),
            _param("can_flee", "boolean"),
            _param("safe_route", "boolean"),
            deepcopy(destination),
            _param("route_blocked", "boolean", required=False, default=False),
            _param("moves_away", "boolean", required=False, default=True),
            _param("flee_distance", "integer", required=False, default=0, minimum=0, maximum=1000),
            _param(
                "visible_fear_casters",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=8,
            ),
        ],
        [
            _require(
                "actor_state.control_fear_pending == true",
                "The Fear flight for this actual turn is already resolved.",
            ),
            _require(
                "state.spatial_scene != null",
                "Fear flight and sight require the admitted spatial scene.",
            ),
            _require(
                "can_flee != true or (safe_route == true and len(destination) == 3)",
                "Supply a safe route endpoint for actual Fear movement.",
            ),
            _require(
                "can_flee == true or len(destination) == 0",
                "A no-route ruling cannot invent an endpoint.",
            ),
            _set("_fear_ids", "actor_state.control_behavior_source_ids"),
            _set("_fear_casters", []),
            _each(
                "_fear_ids",
                "_fear_id",
                [
                    _set("_fear_source", "lasting_state.timed_condition_sources[_fear_id]"),
                    _when(
                        (
                            "_fear_source.control_profile == 'fear' and _fear_source.suppressed != "
                            "true"
                        ),
                        [
                            _push("_fear_casters", "_fear_source.caster_state_id"),
                            _call(
                                "resolve_srd_scene_relation",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "target_state": "{_fear_source.caster_state_id}",
                                },
                                ("_scene_distance", "_scene_same_plane"),
                            ),
                            _require(
                                "_scene_same_plane == true",
                                "Fear movement requires the admitted source plane.",
                            ),
                            _set("_fear_start_distance", "_scene_distance"),
                        ],
                    ),
                ],
            ),
            _each(
                "visible_fear_casters",
                "_fear_supplied_caster",
                [
                    _require(
                        "_fear_supplied_caster in _fear_casters",
                        "A supplied sight reference must name an actual active Fear caster.",
                    )
                ],
            ),
            _set("actor_state.control_fear_pending", False),
            _set("actor_state.control_fear_can_flee", "can_flee"),
            _when(
                "can_flee == true",
                [
                    _call(
                        "spend_srd_action",
                        {"magic_action": False, "control_action_kind": "fear_dash"},
                    ),
                    _call("apply_srd_dash", {"actor_state": "{_actor_state_id}"}),
                    _set("actor_state.control_order_moving", True),
                    _call(
                        "move_srd_scene_creature",
                        {"actor_state": "{_actor_state_id}", "destination": "{destination}"},
                        ("_scene_movement_distance",),
                    ),
                    _set("actor_state.control_order_moving", False),
                    _require(
                        "combat.movement_remaining == 0 or route_blocked == true",
                        "Use available Fear movement unless the safely reachable route ends.",
                    ),
                    _set("actor_state.control_fear_distance", "_scene_movement_distance"),
                ],
            ),
            _set("_fear_visible", False),
            _each(
                "_fear_ids",
                "_fear_id",
                [
                    _set("_fear_source", "lasting_state.timed_condition_sources[_fear_id]"),
                    _when(
                        (
                            "_fear_source.control_profile == 'fear' and _fear_source.suppressed != "
                            "true"
                        ),
                        [
                            _call(
                                "resolve_srd_scene_relation",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "target_state": "{_fear_source.caster_state_id}",
                                },
                                ("_scene_distance", "_scene_line_of_sight"),
                            ),
                            _require(
                                "can_flee != true or _scene_distance > _fear_start_distance",
                                "Fear movement must actually retreat from the saved caster.",
                            ),
                            _set(
                                "lasting_state.timed_condition_sources.{_fear_id}.caster_visible",
                                "_scene_line_of_sight",
                            ),
                            _when("_scene_line_of_sight == true", [_set("_fear_visible", True)]),
                        ],
                    ),
                ],
            ),
            _set("actor_state.control_fear_visible", "_fear_visible"),
            _set("actor_state.control_fear_facts_known", True),
        ],
        [
            {
                "condition": (
                    "combat.turn_active == true and combat.active_actor == _actor_state_id "
                    "and actor_state.control_fear_active == true"
                ),
                "error": "Resolve Fear only on the affected creature's actual active turn.",
            }
        ],
        public=True,
    )
    candidate = _tool(
        "Derive a real living scene creature within the actor's saved melee reach.",
        [_entity("actor_state"), _entity("target_state"), _param("reach", "integer")],
        [
            _set("_confusion_candidate", False),
            _when(
                (
                    "target_state.is_alive == true and (target_state.creature_kind == "
                    "'character' or target_state.creature_kind == 'monster')"
                ),
                [
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                        ("_scene_distance", "_scene_same_plane"),
                    ),
                    _set(
                        "_confusion_candidate",
                        "_scene_same_plane == true and _scene_distance <= reach",
                    ),
                ],
            ),
        ],
    )
    confusion = _tool(
        (
            "Move in Confusion's rolled compass direction or choose randomly from "
            "actual creatures within reach."
        ),
        [
            _entity("actor_state"),
            _lasting(),
            deepcopy(destination),
            _param("route_blocked", "boolean", required=False, default=False),
            _param(
                "melee_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
                max_items=64,
            ),
            _param("movement_feet", "integer", required=False, default=0, minimum=0, maximum=1000),
            _param("path_direction", "integer", required=False, default=0, minimum=0, maximum=4),
        ],
        [
            _require(
                "actor_state.control_confusion_pending == true",
                "There is no pending rolled Confusion behavior.",
            ),
            _require(
                "state.spatial_scene != null",
                "Confusion movement and reach require the admitted spatial scene.",
            ),
            _when(
                "actor_state.control_confusion_mode == 'move'",
                [
                    _require(
                        "len(destination) == 3",
                        "Supply the actual endpoint for Confusion movement.",
                    ),
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_actor_state_id}", "target_state": "{_actor_state_id}"},
                        ("_scene_actor_position",),
                    ),
                    _set("_confusion_start", "_scene_actor_position"),
                    _set("_confusion_dx", "destination[0] - _confusion_start[0]"),
                    _set("_confusion_dy", "destination[1] - _confusion_start[1]"),
                    _require(
                        "destination[2] == _confusion_start[2]",
                        "The rolled compass direction does not grant vertical movement.",
                    ),
                    _require(
                        (
                            "(actor_state.control_confusion_direction == 1 and _confusion_dx == 0 "
                            "and _confusion_dy > 0) or (actor_state.control_confusion_direction == "
                            "2 and _confusion_dx > 0 and _confusion_dy == 0) or "
                            "(actor_state.control_confusion_direction == 3 and _confusion_dx == 0 "
                            "and _confusion_dy < 0) or (actor_state.control_confusion_direction == "
                            "4 and _confusion_dx < 0 and _confusion_dy == 0)"
                        ),
                        (
                            "The actual scene endpoint must follow Confusion's rolled compass "
                            "direction."
                        ),
                    ),
                    _set("actor_state.control_order_moving", True),
                    _call(
                        "move_srd_scene_creature",
                        {"actor_state": "{_actor_state_id}", "destination": "{destination}"},
                        ("_scene_movement_distance",),
                    ),
                    _set("actor_state.control_order_moving", False),
                    _require(
                        "combat.movement_remaining == 0 or route_blocked == true",
                        (
                            "Confusion uses all movement along its rolled route unless that route "
                            "is blocked."
                        ),
                    ),
                    _set("actor_state.control_confusion_movement", "_scene_movement_distance"),
                    _set("actor_state.control_confusion_pending", False),
                ],
            ),
            _when(
                "actor_state.control_confusion_mode == 'attack'",
                [
                    _set("_confusion_candidates", []),
                    _set("_confusion_scene", "state.spatial_scene.entities"),
                    _require(
                        "len(_confusion_scene) <= 64",
                        (
                            "Resolve a bounded scene of at most64 entries for Confusion's random "
                            "reach selection."
                        ),
                    ),
                    _set("_confusion_reach", 5),
                    _when(
                        "actor_state.melee_reach != null",
                        [_set("_confusion_reach", "actor_state.melee_reach")],
                    ),
                    _each(
                        "_confusion_scene",
                        "_confusion_entry",
                        [
                            _when(
                                (
                                    "_confusion_entry.active == true and "
                                    "_confusion_entry.metadata.participant_ref != null and "
                                    "_confusion_entry.metadata.participant_ref != _actor_state_id"
                                ),
                                [
                                    _call(
                                        "resolve_srd_confusion_candidate",
                                        {
                                            "actor_state": "{_actor_state_id}",
                                            "target_state": (
                                                "{_confusion_entry.metadata.participant_ref}"
                                            ),
                                            "reach": "{_confusion_reach}",
                                        },
                                        ("_confusion_candidate",),
                                    ),
                                    _when(
                                        (
                                            "_confusion_candidate == true and "
                                            "_confusion_entry.metadata.participant_ref "
                                            "not in _confusion_candidates"
                                        ),
                                        [
                                            _push(
                                                "_confusion_candidates",
                                                "_confusion_entry.metadata.participant_ref",
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _each(
                        "melee_targets",
                        "_confusion_supplied",
                        [
                            _require(
                                "_confusion_supplied in _confusion_candidates",
                                "A supplied melee reference lies outside actual scene reach.",
                            )
                        ],
                    ),
                    _set("_confusion_count", "len(_confusion_candidates)"),
                    _when(
                        "_confusion_count > 0",
                        [
                            {
                                "dice": {"expression": "1d{_confusion_count}"},
                                "store_as": "_confusion_target_roll",
                            },
                            _set("_confusion_target_index", "_confusion_target_roll.total - 1"),
                            _set(
                                "actor_state.control_confusion_target",
                                "_confusion_candidates[_confusion_target_index]",
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
                "condition": (
                    "combat.turn_active == true and combat.active_actor == _actor_state_id"
                ),
                "error": "Resolve Confusion only during the affected creature's actual turn.",
            }
        ],
        public=True,
    )
    return {
        "resolve_srd_fear_turn": fear,
        "resolve_srd_confused_turn": confusion,
        "resolve_srd_confusion_candidate": candidate,
    }


def _consumers(tools):
    changed = {}
    fear_visibility = _tool(
        "Derive current Fear source line of sight from the admitted scene at the roll boundary.",
        [_entity("actor_state"), _lasting()],
        [
            _set("actor_state.control_fear_visible", False),
            _set("_fear_visibility_ids", "actor_state.control_behavior_source_ids"),
            _each(
                "_fear_visibility_ids",
                "_fear_visibility_id",
                [
                    _set(
                        "_fear_visibility_source",
                        "lasting_state.timed_condition_sources[_fear_visibility_id]",
                    ),
                    _when(
                        "_fear_visibility_source.control_profile == 'fear' and "
                        "_fear_visibility_source.suppressed != true",
                        [
                            _call(
                                "resolve_srd_scene_geometry_relation",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "target_state": "{_fear_visibility_source.caster_state_id}",
                                },
                                ("_scene_line_of_sight",),
                            ),
                            _when(
                                "_scene_line_of_sight == true",
                                [_set("actor_state.control_fear_visible", True)],
                            ),
                        ],
                    ),
                ],
            ),
            _set("actor_state.control_fear_facts_known", True),
        ],
    )
    changed["resolve_srd_control_fear_visibility"] = fear_visibility
    end_control = deepcopy(tools["end_srd_control_behavior_turn"])
    for effect in _walk(end_control["effects"]):
        if effect.get("condition", "").startswith("_control_source.control_profile == 'fear' and"):
            effect["on_true"] = [
                _call(
                    "resolve_srd_scene_geometry_relation",
                    {
                        "actor_state": "{_target_state_id}",
                        "target_state": "{_control_source.caster_state_id}",
                    },
                    ("_scene_line_of_sight",),
                ),
                _set(
                    "lasting_state.timed_condition_sources.{_control_source_id}.caster_visible",
                    "_scene_line_of_sight",
                ),
                _set("_control_end_save", "_scene_line_of_sight != true"),
            ]
    changed["end_srd_control_behavior_turn"] = end_control
    for name, hook, actor in (
        ("begin_combat_turn", "start_srd_affliction_turn", "actor"),
        ("resolve_srd_timed_condition_end_turn", "end_srd_affliction_turn", "target_state"),
    ):
        t = deepcopy(tools[name])
        t["effects"] = [e for e in t["effects"] if hook not in str(e)]
        insertion = _when(
            f"{actor}.has_control_affliction_sources == true",
            [
                _call(
                    hook,
                    {"target_state": "{_actor_id}" if actor == "actor" else "{_target_state_id}"},
                )
            ],
        )
        if actor == "actor":
            t["effects"].append(insertion)
        else:
            t["effects"].insert(0, insertion)
        changed[name] = t
    check = deepcopy(tools["check_srd_active_control_action"])
    check["effects"].insert(
        0,
        _require(
            (
                "actor_state.affliction_movement_pending != true or action_kind == "
                "'affliction_dash' or action_kind == 'curse_dodge'"
            ),
            "Resolve compulsory affliction movement before another action.",
        ),
    )
    changed["check_srd_active_control_action"] = check
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    attack["effects"].append(
        _when(
            "actor_state.has_control_affliction_sources == true",
            [
                _call(
                    "resolve_srd_affliction_roll",
                    {
                        "target_state": "{_actor_state_id}",
                        "kind": "attack_roll",
                        "attack_target_id": "{_target_state_id}",
                    },
                    ("_affliction_disadvantage",),
                ),
                _set(
                    "_condition_disadvantage",
                    "_condition_disadvantage == true or _affliction_disadvantage == true",
                ),
            ],
        )
    )
    changed["resolve_srd_attack_conditions"] = attack
    d20 = deepcopy(tools["d20_test"])
    index = next(
        i
        for i, e in enumerate(d20["effects"])
        if e.get("condition", "").startswith("forego_save == true")
    )
    d20["effects"].insert(
        index,
        _when(
            "actor_state != null and actor_state.has_control_affliction_sources == true",
            [
                _call(
                    "resolve_srd_affliction_roll",
                    {"target_state": "{_actor_state_id}", "kind": "{kind}", "ability": "{ability}"},
                    ("_affliction_disadvantage",),
                ),
                _set(
                    "_effective_disadvantage",
                    "_effective_disadvantage == true or _affliction_disadvantage == true",
                ),
            ],
        ),
    )
    changed["d20_test"] = d20
    for owner in (attack, d20):
        for effect in _walk(owner["effects"]):
            if effect.get("condition", "").startswith("actor_state.control_fear_active == true"):
                effect["on_true"].insert(
                    0,
                    _call(
                        "resolve_srd_control_fear_visibility", {"actor_state": "{_actor_state_id}"}
                    ),
                )
    damage = deepcopy(tools["resolve_srd_control_after_damage"])
    loop = next(e["for_each"]["do"] for e in damage["effects"] if "for_each" in e)
    loop.append(
        _when(
            (
                "_control_damage_source.installed == true and "
                "_control_damage_source.control_profile == 'eyebite_sleep'"
            ),
            [_retire("_control_damage_source_id")],
        )
    )
    loop.append(
        _when(
            (
                "_control_damage_source.installed == true and "
                "_control_damage_source.suppressed != true and "
                "_control_damage_source.control_profile == 'affinity_sympathy' and "
                "source_state_id == _control_damage_source.caster_state_id"
            ),
            [
                _call(
                    "save_srd_affinity_release",
                    {
                        "target_state": "{_target_state_id}",
                        "source_id": "{_control_damage_source_id}",
                    },
                )
            ],
        )
    )
    changed["resolve_srd_control_after_damage"] = damage
    shake = deepcopy(tools["shake_srd_sleeping_creature"])
    for effect in _walk(shake["effects"]):
        if effect.get("condition") == "_control_source.control_profile == 'sleep'":
            effect["condition"] += " or _control_source.control_profile == 'eyebite_sleep'"
    shake["description"] = "Spend an actual action to shake away Sleep and Eyebite Asleep sources."
    changed["shake_srd_sleeping_creature"] = shake
    # Only the owning attack/spell paths qualify; environmental damage does not trigger this curse.
    for name in (
        "resolve_opportunity_attack",
        "resolve_srd_unarmed_effect",
        "resolve_combat_attack",
        "resolve_combat_attack_followup",
        "resolve_adjudicated_creature_attack",
        "resolve_srd_delayed_spell_damage",
        "cast_spell_automatic_force_projectiles",
        "cast_spell_attack_damage",
        "apply_srd_multi_attack_damage",
        "cast_spell_single_target_save_damage",
        "resolve_spell_area_save_damage_target",
        "cast_spell_instantaneous_threshold_death_damage",
        "violate_srd_geas_internal",
    ):
        t = deepcopy(tools[name])

        def attach(value):
            if isinstance(value, list):
                for i in range(len(value) - 1, -1, -1):
                    e = value[i]
                    if isinstance(e, dict) and e.get("call") in {
                        "apply_srd_damage", "apply_srd_magic_missile_damage",
                        "apply_srd_divine_favor_weapon_damage",
                    }:
                        source = e["params"].get("source_state_id", "{_actor_id}")
                        if e["call"] == "apply_srd_divine_favor_weapon_damage":
                            source = e["params"]["actor_state"]
                        target = e["params"].get("target", e["params"].get("target_state"))
                        e.setdefault("export_context", {})["_affliction_trigger_damage"] = (
                            "_effective_damage"
                        )
                        e["export_context"]["_affliction_trigger_alive"] = "_is_alive_after"
                        value.insert(
                            i + 1,
                            _when(
                                (
                                    "lasting_state.has_affliction_damage_curses == true and "
                                    "_affliction_trigger_damage > 0 and "
                                    "_affliction_trigger_alive == true"
                                ),
                                [
                                    _call(
                                        "apply_srd_curse_damage_rider",
                                        {
                                            "target_state": target,
                                            "caster_state": source,
                                        },
                                    )
                                ],
                            ),
                        )
                    else:
                        attach(e)
            elif isinstance(value, dict):
                for item in value.values():
                    attach(item)

        attach(t["effects"])
        if not any(p["name"] == "lasting_state" for p in t["parameters"]):
            t["parameters"].append({**_lasting(), "model_hidden": True})
        changed[name] = t
    for name in ("spend_combat_movement", "spend_srd_movement"):
        t = deepcopy(tools[name])
        actor = "actor_state" if name == "spend_combat_movement" else "target"
        t["pre_conditions"].append(
            {
                "condition": (
                    f"{actor}.affliction_movement_pending != true "
                    f"or {actor}.control_order_moving == true"
                ),
                "error": "Perform compulsory movement through its saved source owner.",
            }
        )
        changed[name] = t
    scene_move = deepcopy(tools["move_srd_scene_creature"])
    scene_move["effects"] = [
        e for e in scene_move["effects"] if "check_srd_control_scene_move" not in str(e)
    ]
    scene_move["effects"].insert(
        0,
        _when(
            (
                "actor_state.has_control_behavior_sources == true and "
                "actor_state.control_order_moving != true"
            ),
            [
                _call(
                    "check_srd_control_scene_move",
                    {"actor_state": "{_actor_state_id}", "destination": "{destination}"},
                )
            ],
        ),
    )
    changed["move_srd_scene_creature"] = scene_move
    return changed


def extend_runtime(text):
    "Compose afflictions after control orders without changing core execution or scene contracts."
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    # Builds may start from the previous generated runtime; replace, rather than stack, owned hooks.
    owned_calls = {
        "apply_srd_curse_damage_rider",
        "resolve_srd_affliction_roll",
        "resolve_srd_affliction_projection",
        "resolve_srd_control_fear_visibility",
    }

    def strip_owned(value):
        if isinstance(value, list):
            value[:] = [
                e
                for e in value
                if not (
                    isinstance(e, dict)
                    and (
                        e.get("call") in owned_calls
                        or (e.get("condition", "").find("affliction_movement_pending") >= 0)
                        or (
                            "apply_srd_curse_damage_rider" in str(e)
                            and e.get("condition", "").startswith(
                                (
                                    "state.has_affliction_damage_curses == true",
                                    "lasting_state.has_affliction_damage_curses == true",
                                )
                            )
                        )
                        or (
                            e.get("condition", "").startswith(
                                "actor_state.has_control_affliction_sources"
                            )
                            and "resolve_srd_affliction_roll" in str(e)
                        )
                        or (
                            e.get("condition", "").startswith(
                                "actor_state != null and actor_state.has_control_affliction_sources"
                            )
                            and "resolve_srd_affliction_roll" in str(e)
                        )
                        or e.get("condition")
                        == "_spell_definition.family == 'timed_control_affliction'"
                        or e.get("condition", "").find(
                            "_control_damage_source.control_profile == 'eyebite_sleep'"
                        )
                        >= 0
                        or e.get("condition", "").find(
                            "_control_damage_source.control_profile == 'affinity_sympathy'"
                        )
                        >= 0
                    )
                )
            ]
            for e in value:
                strip_owned(e)
        elif isinstance(value, dict):
            for e in value.values():
                strip_owned(e)

    for name, t in tools.items():
        if name.startswith(
            (
                "resolve_srd_affliction",
                "cast_srd_control_afflictions",
                "apply_srd_curse_damage_rider",
            )
        ):
            continue
        strip_owned(t.get("effects", []))
        if name == "cast_spell":
            t["parameters"] = [
                p
                for p in t["parameters"]
                if p["name"]
                not in {
                    "eyebite_mode",
                    "affinity_mode",
                    "affinity_kind",
                    "curse_mode",
                    "curse_ability",
                }
            ]
        if "pre_conditions" in t:
            t["pre_conditions"] = [
                p
                for p in t["pre_conditions"]
                if "affliction_movement_pending" not in p.get("condition", "")
            ]
    changed = _tools() | _consumers(tools)
    select = deepcopy(tools["resolve_srd_control_behavior_sources"])
    profile_field = next(
        e["set"]
        for e in select["effects"]
        if e.get("set", {}).get("field") == "_control_spell_profiles"
    )
    profile_field["value"].extend(
        "'" + p + "'" for p in SOURCE_PROFILES if "'" + p + "'" not in profile_field["value"]
    )
    changed["resolve_srd_control_behavior_sources"] = select
    conditions = deepcopy(tools["resolve_srd_control_behavior_conditions"])
    for effect in _walk(conditions["effects"]):
        if effect.get("condition") == "_control_source.control_profile == 'fear'":
            effect["condition"] = "_control_source.condition == 'frightened'"
        if effect.get("condition", "").startswith("_control_source.control_profile == 'sleep' and"):
            effect["condition"] = "_control_source.condition == 'unconscious'"
    changed["resolve_srd_control_behavior_conditions"] = conditions
    projection = deepcopy(tools["resolve_srd_control_behavior_projection"])
    projection["effects"].append(
        _call(
            "resolve_srd_affliction_projection",
            {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
        )
    )
    changed["resolve_srd_control_behavior_projection"] = projection
    install = deepcopy(tools["install_srd_timed_condition_source"])
    for p in install["parameters"]:
        if p["name"] == "control_profile":
            p["enum_values"] = list(dict.fromkeys([*p["enum_values"], *SOURCE_PROFILES]))
        if p["name"] == "condition" and "poisoned" not in p["enum_values"]:
            p["enum_values"].append("poisoned")
    for condition in ("frightened", "unconscious", "poisoned"):
        field = f"target_state.timed_condition_unowned_{condition}"
        if not any(e.get("condition") == field + " == null" for e in install["effects"]):
            value = f"'{condition}' in target_state.conditions"
            if condition == "poisoned":
                value += " and target_state.turn_effect_poisoned != true"
            install["effects"].insert(0, _when(field + " == null", [_set(field, value)]))
    for e in _walk(install["effects"]):
        if (
            e.get("condition", "").startswith("(('paralyzed' in target_state.conditions")
            and "unconscious" not in e["condition"]
        ):
            e["condition"] = e["condition"].replace(
                "'incapacitated' in target_state.conditions",
                (
                    "'incapacitated' in target_state.conditions or 'unconscious' in "
                    "target_state.conditions"
                ),
            )
    changed["install_srd_timed_condition_source"] = install
    save = deepcopy(tools["resolve_srd_control_behavior_save"])
    for e in _walk(save["effects"]):
        if "target_state.does_not_sleep" in e.get("condition", ""):
            e["condition"] = e["condition"].replace(
                "automatic_success != true and not (behavior == 'sleep' and ",
                "automatic_success != true and not (behavior == 'eyebite_sleep' and "
                "target_state.does_not_sleep == true) and not (behavior == 'sleep' and ",
            )
    changed["resolve_srd_control_behavior_save"] = save
    apply = deepcopy(tools["apply_srd_condition"])
    apply["effects"] = [
        e for e in apply["effects"] if "target.timed_condition_unowned_poisoned" not in str(e)
    ]
    apply["effects"].append(
        _when("condition == 'poisoned'", [_set("target.timed_condition_unowned_poisoned", True)])
    )
    changed["apply_srd_condition"] = apply
    removal = deepcopy(tools["remove_srd_condition"])

    def remove_owned_poison_hook(value):
        if isinstance(value, list):
            value[:] = [
                effect for effect in value
                if not (isinstance(effect, dict)
                        and effect.get("condition") == "condition == 'poisoned'"
                        and "target.timed_condition_unowned_poisoned" in str(effect))
            ]
            for effect in value:
                remove_owned_poison_hook(effect)
        elif isinstance(value, dict):
            for child in value.values():
                remove_owned_poison_hook(child)

    remove_owned_poison_hook(removal["effects"])
    removal["effects"].append(
        _when(
            "condition == 'poisoned'",
            [
                _set("target.timed_condition_unowned_poisoned", False),
                _call(
                    "remove_srd_timed_condition_effect",
                    {"target_state": "{target}", "lasting_state": "state", "condition": "poisoned"},
                ),
            ],
        )
    )
    changed["remove_srd_condition"] = removal
    remove_effect = deepcopy(tools["remove_srd_timed_condition_effect"])
    for p in remove_effect["parameters"]:
        if p["name"] == "condition" and "poisoned" not in p["enum_values"]:
            p["enum_values"].append("poisoned")
    changed["remove_srd_timed_condition_effect"] = remove_effect
    cast = deepcopy(tools["cast_spell"])
    for p in changed["cast_srd_control_afflictions"]["parameters"]:
        if p["name"] in {
            "eyebite_mode",
            "affinity_mode",
            "affinity_kind",
            "curse_mode",
            "curse_ability",
        }:
            cast["parameters"].append(deepcopy(p))
    for e in _walk(cast["effects"]):
        setting = e.get("set", {})
        if setting.get("field") == "_control_harmful_spell_families":
            setting["value"] = list(
                dict.fromkeys([*setting["value"], "'timed_control_affliction'"])
            )
        if e.get("condition", "").startswith(
            "_spell_definition.family == 'multi_target_timed_control'"
        ) and "_control_harm_targets" in str(e):
            e["condition"] = " or ".join(
                dict.fromkeys(
                    [
                        *e["condition"].split(" or "),
                        "_spell_definition.family == 'timed_control_affliction'",
                    ]
                )
            )
    dispatch = _when(
        "_spell_definition.family == 'timed_control_affliction'",
        [
            _call(
                "cast_srd_control_afflictions",
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
                    "eyebite_mode": "{eyebite_mode}",
                    "affinity_mode": "{affinity_mode}",
                    "affinity_kind": "{affinity_kind}",
                    "curse_mode": "{curse_mode}",
                    "curse_ability": "{curse_ability}",
                },
                ("_spell_summary_rows", "_spell_detail_rows", "_affliction_save_rolls"),
            )
        ],
    )

    def insert(value):
        if isinstance(value, list):
            for i, e in enumerate(value):
                if (
                    isinstance(e, dict)
                    and e.get("condition") == "_spell_definition.family == 'timed_control_orders'"
                ):
                    value.insert(i, dispatch)
                    return True
                if insert(e):
                    return True
        elif isinstance(value, dict):
            return any(insert(v) for v in value.values())
        return False

    if not insert(cast["effects"]):
        raise ValueError("Missing control-order dispatch")
    changed["cast_spell"] = cast
    for name in PUBLIC_TOOLS:
        implementation = changed[name]
        internal = name + "_internal"
        changed[internal] = deepcopy(implementation)
        changed[internal]["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        params = [p for p in implementation["parameters"] if p["name"] != "lasting_state"]
        call_params = {
            p["name"]: "{"
            + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
            + "}"
            for p in params
        }
        call_params["lasting_state"] = "state"
        changed[name] = {
            "description": implementation["description"],
            "parameters": params,
            "effects": [_call(internal, call_params)],
            "metadata": implementation["metadata"],
        }
    compose_befuddlement(tools, changed)
    for name, t in changed.items():
        for effect in _walk(t.get("effects", [])):
            if effect.get(
                "call"
            ) == "resolve_srd_scene_relation" and "_scene_can_see" not in effect.get(
                "export_context", {}
            ):
                effect["call"] = "resolve_srd_scene_geometry_relation"
        rendered = _render_tool(name, t)
        if name == "d20_test":
            rendered = rendered.replace(
                "    result_card:\n", "    result_card: &d20_result_card\n", 1
            )
        if name == "cast_spell":
            rendered = rendered.replace("  cast_spell:\n", "  cast_spell: &srd_cast_spell\n", 1)
            start = rendered.index(
                "      enum_values:\n", rendered.index("    - name: spell\n")
            ) + len("      enum_values:\n")
            end = rendered.index("      required:", start)
            values = next(p for p in cast["parameters"] if p["name"] == "spell")["enum_values"]
            rendered = (
                rendered[:start]
                + "        # BEGIN GENERATED SPELL SELECTOR\n"
                + "".join(f"          - {v}\n" for v in values)
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
    if "      timed_control_affliction:" not in text:
        rendered = yaml.dump(
            {"timed_control_affliction": _schema()}, Dumper=_Dumper, sort_keys=False
        )
        text = text.replace(
            "      timed_control_orders:",
            "".join("      " + line for line in rendered.splitlines(True))
            + "\n      timed_control_orders:",
            1,
        )
    return text
