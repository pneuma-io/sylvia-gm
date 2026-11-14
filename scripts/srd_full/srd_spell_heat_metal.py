"""Heat Metal retains the actual manufactured object, paid source and typed Fire exposure."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_spell_object_content import (
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _now,
    )
    from .srd_spell_object_utilities import FACT_EXPORTS, _fact_guard
    from .srd_spell_supplies import _effect_lists
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools, _RuntimeDumper
else:
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_spell_object_content import (
        _held_object_geometry,
        _lookup_object,
        _metadata_patch,
        _now,
    )
    from srd_spell_object_utilities import FACT_EXPORTS, _fact_guard
    from srd_spell_supplies import _effect_lists
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _push, _replace_tools, _RuntimeDumper


SOURCE = {"heat_metal": ([139, 140], "Heat Metal", "Hellish Rebuke")}
SOURCE_PAGES = {
    139: "79e0cf2a1d0ca3dd02e3489ba1d9c90e38e49ae87fadf9269d5b196cbd9406c3",
    140: "c4cc04077382284fa9e258d0ef6aaa6ad59de10aa7206a4fe69971fc334bdd45",
}
PROFILES = {
    "heat_metal": {
        "family": "heat_metal_spell",
        "base_level": 2,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 60,
        "concentration": True,
        "duration_rounds": 10,
        "base_dice_count": 2,
        "damage_die": "d8",
        "damage_type": "fire",
        "save_ability": "constitution",
        "dice_per_extra_slot": 1,
    }
}
PUBLIC_TOOLS = {"reheat_srd_metal"}
CAST_CHOICES = [_optional("heated_object_id", default="")]


def validate_profiles(definitions):
    for spell, profile in definitions.items():
        if profile.get("family") == "heat_metal_spell" and profile != PROFILES.get(spell):
            raise ValueError(f"{spell} does not match its pinned Heat Metal profile")


def _schema():
    profile = PROFILES["heat_metal"]
    return {
        "type": "object",
        "properties": {
            name: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for name, value in profile.items()
            if name != "family"
        },
        "required": sorted(set(profile) - {"family"}),
        "additionalProperties": False,
    }


def _read_source():
    return [
        *_now(),
        _set("_heat_records", []),
        _when("state.turn_effects != null", [_set("_heat_records", "state.turn_effects")]),
        _set("_heat_source", None),
        _loop(
            "_heat_records",
            "_heat_candidate",
            [
                _when(
                    "_heat_candidate.kind == 'heat_metal_spell' and "
                    "_heat_candidate.source_id == source_id",
                    [
                        _require(
                            "_heat_source == null", "The saved Heat Metal source is ambiguous."
                        ),
                        _set("_heat_source", "_heat_candidate"),
                    ],
                ),
            ],
        ),
        _require(
            "_heat_source != null and _heat_source.spell == 'heat_metal' and "
            f"_heat_source.source_sha256 == {SOURCE_PAGES[139]!r} and "
            "_heat_source.spell_level >= 2 and _heat_source.spell_level <= 9",
            "Select one actual source-qualified Heat Metal casting.",
        ),
    ]


def _caster_source_guard():
    return [
        _require(
            "_heat_source.caster == _caster_state_id and "
            "caster_state.concentration_spell_id == 'heat_metal' and "
            "caster_state.heat_metal_source_id == source_id and "
            "caster_state.concentration_effect_family == 'heat_metal_spell' and "
            "_object_now < _heat_source.expires_tick",
            "The actual Heat Metal concentration has ended.",
        ),
    ]


def _held_contact():
    """Use actual carried/worn object facts; nearby creatures are not invented physical contacts."""
    return [
        _set("_heat_holder", "''"),
        _set("_heat_can_drop", False),
        _when(
            "_object_scene.metadata.carried_by != null",
            [
                _set("_heat_holder", "_object_scene.metadata.carried_by"),
                _set("_heat_can_drop", True),
            ],
        ),
        _when(
            "_object_scene.metadata.worn_by != null",
            [
                _require("_heat_holder == ''", "One object cannot have two physical holders."),
                _set("_heat_holder", "_object_scene.metadata.worn_by"),
            ],
        ),
    ]


def _retain_source():
    return [
        _set("_heat_kept", []),
        _loop(
            "_heat_records",
            "_heat_candidate",
            [
                _when(
                    "_heat_candidate.kind != 'heat_metal_spell' or "
                    "_heat_candidate.source_id != _heat_source.source_id",
                    [_push("_heat_kept", "_heat_candidate")],
                ),
            ],
        ),
        _push("_heat_kept", "_heat_source"),
        _set("state.turn_effects", "_heat_kept"),
    ]


def _mapped_call(name, params, exports):
    """Map parent context names to the actual nested tool exports."""
    effect = _call(name, params)
    effect["export_context"] = exports
    return effect


def _damage_tool():
    """Roll once, apply the actual caster's raw modifier once, then observe typed base Fire."""
    return _tool_scene(
        "Resolve one actual Heat Metal event against its actual physical holder.",
        [_entity("caster_state"), _parameter("source_id"), _parameter("object_id")],
        [
            *_read_source(),
            *_caster_source_guard(),
            _require(
                "object_id == _heat_source.object_id", "Use the actual retained heated object."
            ),
            *_lookup_object(),
            *_held_object_geometry("{_caster_state_id}"),
            *_fact_guard(),
            _require(
                "_utility_facts.manufactured == true and _utility_facts.metal == true",
                "Heat Metal requires an actual manufactured metal object.",
            ),
            *_held_contact(),
            _set(
                "_heat_damage_row", {"label": "'Heat Metal'", "value": "'No actual held contact'"}
            ),
            _set("_heat_roll", None),
            _when(
                "_heat_holder != ''",
                [
                    _call(
                        "refresh_srd_actor_spell_projections",
                        {
                            "lasting_state": "state",
                        },
                    ),
                    {
                        "dice": {"expression": "{_heat_source.spell_level}d8"},
                        "store_as": "_heat_roll",
                    },
                    _set("_heat_raw", "_heat_roll.total"),
                    _when(
                        "caster_state.has_actor_spell_modifiers == true",
                        [
                            _mapped_call(
                                "modify_srd_actor_raw_damage_roll",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "lasting_state": "state",
                                    "amount": "{_heat_raw}",
                                },
                                {"_heat_raw": "_ray_raw_damage"},
                            ),
                        ],
                    ),
                    _set("_heat_penalty", "max(0, -_heat_raw)"),
                    _set("_heat_amount", "max(0, _heat_raw)"),
                    _call(
                        "apply_srd_qualifying_damage",
                        {
                            "target": "{_heat_holder}",
                            "amount": "{_heat_amount}",
                            "adjustment": 0,
                            "damage_type": "fire",
                            "source_state_id": "{_caster_state_id}",
                            "witness_state_ids": [],
                            "unallocated_roll_penalty": "{_heat_penalty}",
                            "game_damage_roll": True,
                            "observe_base_damage": True,
                        },
                        ["_source_base_effective_damage", "_effective_damage", "_is_alive_after"],
                    ),
                    _set(
                        "_heat_damage_row",
                        {
                            "label": "'Heat Metal'",
                            "value": "_source_base_effective_damage",
                        },
                    ),
                    _when(
                        "_source_base_effective_damage > 0 and _is_alive_after == true",
                        [
                            _call(
                                "resolve_srd_heat_metal_grip",
                                {
                                    "target_state": "{_heat_holder}",
                                    "caster_state": "{_caster_state_id}",
                                    "source_id": "{source_id}",
                                    "object_id": "{object_id}",
                                    "can_drop": "{_heat_can_drop}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _grip_tools():
    grip = _tool_scene(
        "After actual Fire exposure, roll Constitution and drop the source object if possible.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("source_id"),
            _parameter("object_id"),
            _parameter("can_drop", "boolean"),
        ],
        [
            *_read_source(),
            _mapped_call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "ability": "constitution",
                    "kind": "saving_throw",
                    "ability_modifier": "{target_state.saving_throw_modifiers.constitution}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{_heat_source.save_dc}",
                    "advantage": False,
                    "disadvantage": False,
                    "forego_save": False,
                },
                {"_heat_save_outcome": "_outcome"},
            ),
            _set("_heat_dropped", False),
            _when(
                "_heat_save_outcome == 'failure' and can_drop == true",
                [
                    _call(
                        "drop_srd_heated_object",
                        {
                            "target_state": "{_target_state_id}",
                            "object_id": "{object_id}",
                        },
                    ),
                    _set("_heat_dropped", True),
                ],
            ),
            _when(
                "_heat_dropped != true",
                [
                    _set("_heat_grips", []),
                    _when(
                        "target_state.srd_heat_metal_grips != null",
                        [_set("_heat_grips", "target_state.srd_heat_metal_grips")],
                    ),
                    _set("_heat_grip_kept", []),
                    _loop(
                        "_heat_grips",
                        "_heat_grip",
                        [
                            _when(
                                "_heat_grip.source_id != source_id",
                                [_push("_heat_grip_kept", "_heat_grip")],
                            ),
                        ],
                    ),
                    _set("_heat_next_turn", 1),
                    _when(
                        "caster_state.turns_started != null",
                        [_set("_heat_next_turn", "caster_state.turns_started + 1")],
                    ),
                    _push(
                        "_heat_grip_kept",
                        {
                            "source_id": "source_id",
                            "caster": "_caster_state_id",
                            "source_sha256": repr(SOURCE_PAGES[139]),
                            "expires_caster_turn": "_heat_next_turn",
                            "expires_tick": "_object_now + 2",
                        },
                    ),
                    _set("target_state.srd_heat_metal_grips", "_heat_grip_kept"),
                ],
            ),
        ],
    )
    drop = _tool_scene(
        "Release only the actual heated object and its exact saved inventory identity.",
        [_entity("target_state"), _parameter("object_id")],
        [
            *_lookup_object(),
            *_fact_guard(),
            _require(
                "_object_scene.metadata.carried_by == _target_state_id and "
                "_object_scene.metadata.worn_by == null",
                "Only this actual held object can drop.",
            ),
            *_metadata_patch({"carried_by": None, "worn_by": None}),
            _when(
                "_utility_facts.inventory_item != ''",
                [
                    _set("_heat_inventory", []),
                    _set("_heat_kept_inventory", []),
                    _when(
                        "target_state.inventory != null",
                        [_set("_heat_inventory", "target_state.inventory")],
                    ),
                    _loop(
                        "_heat_inventory",
                        "_heat_item",
                        [
                            _when(
                                "_heat_item != _utility_facts.inventory_item",
                                [_push("_heat_kept_inventory", "_heat_item")],
                            ),
                        ],
                    ),
                    _set("target_state.inventory", "_heat_kept_inventory"),
                    _set("_heat_held", []),
                    _set("_heat_kept_held", []),
                    _when(
                        "target_state.held_items != null",
                        [_set("_heat_held", "target_state.held_items")],
                    ),
                    _loop(
                        "_heat_held",
                        "_heat_item",
                        [
                            _when(
                                "_heat_item != _utility_facts.inventory_item",
                                [_push("_heat_kept_held", "_heat_item")],
                            ),
                        ],
                    ),
                    _set("target_state.held_items", "_heat_kept_held"),
                    _set("_heat_dropped_items", []),
                    _when(
                        "target_state.dropped_items != null",
                        [_set("_heat_dropped_items", "target_state.dropped_items")],
                    ),
                    _push("_heat_dropped_items", "_utility_facts.inventory_item"),
                    _set("target_state.dropped_items", "_heat_dropped_items"),
                ],
            ),
        ],
    )
    disadvantage = _tool_scene(
        "Read actual retained Heat Metal grip penalties for Attack Rolls and ability checks only.",
        [_entity("actor_state"), _parameter("kind")],
        [
            *_now(),
            _set("_heat_disadvantage", False),
            _when(
                "kind == 'attack_roll' or kind == 'ability_check'",
                [
                    _set("_heat_grips", []),
                    _when(
                        "actor_state.srd_heat_metal_grips != null",
                        [_set("_heat_grips", "actor_state.srd_heat_metal_grips")],
                    ),
                    _loop(
                        "_heat_grips",
                        "_heat_grip",
                        [
                            _require(
                                f"_heat_grip.source_sha256 == {SOURCE_PAGES[139]!r}",
                                "The grip penalty needs its actual Fire exposure source.",
                            ),
                            _call(
                                "check_srd_heat_metal_grip_clock",
                                {
                                    "caster_state": "{_heat_grip.caster}",
                                    "expires_caster_turn": "{_heat_grip.expires_caster_turn}",
                                    "expires_tick": "{_heat_grip.expires_tick}",
                                },
                                ["_heat_grip_active"],
                            ),
                            _when("_heat_grip_active == true", [_set("_heat_disadvantage", True)]),
                        ],
                    ),
                ],
            ),
        ],
    )
    clock = _tool_scene(
        "Expire the penalty at the actual caster's next turn, or one elapsed round outside combat.",
        [
            _entity("caster_state"),
            _parameter("expires_caster_turn", "integer"),
            _parameter("expires_tick", "number", minimum=0),
        ],
        [
            *_now(),
            _set("_heat_grip_active", False),
            _when(
                "combat.encounter_active == true and (caster_state.turns_started == null or "
                "caster_state.turns_started < expires_caster_turn)",
                [_set("_heat_grip_active", True)],
            ),
            _when(
                "combat.encounter_active != true and _object_now < expires_tick",
                [_set("_heat_grip_active", True)],
            ),
        ],
    )
    return {
        "resolve_srd_heat_metal_grip": grip,
        "drop_srd_heated_object": drop,
        "check_srd_heat_metal_disadvantage": disadvantage,
        "check_srd_heat_metal_grip_clock": clock,
    }


def _casting_tool():
    return _tool_scene(
        "Retain actual manufactured metal and casting statistics, then heat once.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=2, maximum=9),
            _parameter("save_dc", "integer"),
            _parameter("concentration_modifier", "integer"),
            _parameter("object_id"),
        ],
        [
            *_now(),
            _call(
                "resolve_srd_object_utility_facts",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 60,
                    "requires_sight": True,
                },
                FACT_EXPORTS,
            ),
            _require(
                "_utility_facts.manufactured == true and _utility_facts.metal == true",
                "Choose a visible actual manufactured metal object.",
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
            _set("_heat_serial", 1),
            _when(
                "caster_state.srd_heat_metal_serial != null",
                [_set("_heat_serial", "caster_state.srd_heat_metal_serial + 1")],
            ),
            _set("caster_state.srd_heat_metal_serial", "_heat_serial"),
            _call(
                "format_srd_information_projection",
                {"message": "{_caster_state_id}:heat_metal:{_heat_serial}"},
                ["_info_projection_message"],
            ),
            _set("_heat_id", "_info_projection_message"),
            _set("_heat_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [_set("_heat_turn", "caster_state.turns_started")],
            ),
            _set(
                "_heat_source",
                {
                    "kind": "'heat_metal_spell'",
                    "spell": "'heat_metal'",
                    "source_id": "_heat_id",
                    "source_sha256": repr(SOURCE_PAGES[139]),
                    "spell_level": "slot_level",
                    "caster": "_caster_state_id",
                    "object_id": "object_id",
                    "target": "_caster_state_id",
                    "owner": "_caster_state_id",
                    "phase": "'never'",
                    "turn": 0,
                    "save_dc": "save_dc",
                    "started_tick": "_object_now",
                    "expires_tick": "_object_now + 20",
                    "last_heated_turn": "_heat_turn",
                    "dispel_ongoing": True,
                },
            ),
            _set("_heat_records", []),
            _when("state.turn_effects != null", [_set("_heat_records", "state.turn_effects")]),
            *_retain_source(),
            _set("caster_state.concentration_spell_id", "'heat_metal'"),
            _set("caster_state.concentration_effect_family", "'heat_metal_spell'"),
            _set("caster_state.heat_metal_source_id", "_heat_id"),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_target_state_ids", []),
            _set("caster_state.concentration_started_tick", "_object_now"),
            _set("caster_state.concentration_expires_tick", "_object_now + 20"),
            _set("caster_state.concentration_save_modifier", "concentration_modifier"),
            _call(
                "resolve_srd_heat_metal_damage",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{_heat_id}",
                    "object_id": "{object_id}",
                },
                ["_heat_damage_row", "_heat_roll"],
            ),
            _set("_heat_summary", [{"label": "'Object'", "value": "object_id"}]),
            _set("_heat_details", ["_heat_damage_row"]),
            _when(
                "_heat_roll != null",
                [
                    _push(
                        "_heat_details", {"label": "'Actual dice'", "value": "_heat_roll.results"}
                    ),
                ],
            ),
        ],
    )


def _reheat_tool():
    return _tool_scene(
        "Spend a Bonus Action on a later caster turn to reheat the retained object in range.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source(),
            *_caster_source_guard(),
            _require(
                "combat.encounter_active == true and combat.turn_active == true and "
                "combat.active_actor == _caster_state_id and "
                "caster_state.turns_started > _heat_source.last_heated_turn",
                "Repeat heating is available only on a later actual caster turn.",
            ),
            _call(
                "check_srd_heated_object_range",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{_heat_source.object_id}",
                },
            ),
            _call(
                "consume_srd_bonus_action",
                {"actor_state": "{_caster_state_id}", "bonus_action": "heat_metal"},
            ),
            _set("_heat_source_change", {"last_heated_turn": "caster_state.turns_started"}),
            _set("_heat_source", "merge(_heat_source, _heat_source_change)"),
            *_retain_source(),
            _call(
                "resolve_srd_heat_metal_damage",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{source_id}",
                    "object_id": "{_heat_source.object_id}",
                },
                ["_heat_damage_row"],
            ),
        ],
    )


def _lifecycle_tools():
    range_tool = _tool_scene(
        "Recheck the retained object's range and plane; repeat heating needs no new sight.",
        [_entity("caster_state"), _parameter("object_id")],
        [
            *_lookup_object(),
            *_held_object_geometry("{_caster_state_id}"),
            _set("_heat_object", "_object_scene"),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _require(
                "_heat_object.metadata.plane == _info_scene_entity.metadata.plane",
                "The retained heated object must share the caster's plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_info_scene_entity.position}",
                    "end": "{_heat_object.position}",
                },
                ["_scene_distance"],
            ),
            _require("_scene_distance <= 60", "The retained heated object is beyond sixty feet."),
        ],
    )
    retire = _tool_scene(
        "Retire this caster's exact Heat Metal source; preserve earned grip penalties until due.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source(),
            _require("_heat_source.caster == _caster_state_id", "Use the actual source owner."),
            _set("_heat_kept", []),
            _loop(
                "_heat_records",
                "_heat_candidate",
                [
                    _when(
                        "_heat_candidate.kind != 'heat_metal_spell' or "
                        "_heat_candidate.source_id != source_id",
                        [_push("_heat_kept", "_heat_candidate")],
                    ),
                ],
            ),
            _set("state.turn_effects", "_heat_kept"),
            _when(
                "caster_state.heat_metal_source_id == source_id",
                [_set("caster_state.heat_metal_source_id", None)],
            ),
        ],
    )
    refresh_one = _tool_scene(
        "Expire one real Heat Metal source when its concentration or source deadline ends.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source(),
            _when(
                "_object_now >= _heat_source.expires_tick or "
                "caster_state.concentration_spell_id != 'heat_metal' or "
                "caster_state.heat_metal_source_id != source_id",
                [
                    {
                        "condition": "caster_state.concentration_spell_id == 'heat_metal' and "
                        "caster_state.concentration_effect_family == 'heat_metal_spell' and "
                        "caster_state.heat_metal_source_id == source_id",
                        "on_true": [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_caster_state_id}",
                                },
                            )
                        ],
                        "on_false": [
                            _call(
                                "retire_srd_heat_metal_source",
                                {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                            )
                        ],
                    },
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Refresh only stored actual Heat Metal sources without projecting an object as a creature.",
        [],
        [
            _set("_heat_pending", []),
            _when("state.turn_effects != null", [_set("_heat_pending", "state.turn_effects")]),
            _loop(
                "_heat_pending",
                "_heat_source_record",
                [
                    _when(
                        "_heat_source_record.kind == 'heat_metal_spell'",
                        [
                            _call(
                                "refresh_srd_heat_metal_source",
                                {
                                    "caster_state": "{_heat_source_record.caster}",
                                    "source_id": "{_heat_source_record.source_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    source_check = _tool_scene(
        "Qualify one actual current Heat Metal concentration for an exact source callback.",
        [_entity("caster_state"), _parameter("source_id")],
        [*_read_source(), *_caster_source_guard()],
    )
    source_read = _tool_scene(
        "Read the actual ongoing Heat Metal casting selected on its retained scene object.",
        [_parameter("object_id"), _parameter("source_id")],
        [
            *_read_source(),
            _require(
                "object_id == _heat_source.object_id",
                "Select the actual heated object and its exact casting.",
            ),
            _call(
                "check_srd_heat_metal_source",
                {"caster_state": "{_heat_source.caster}", "source_id": "{source_id}"},
                ["_heat_source"],
            ),
            _set("_heat_metal_source", "_heat_source"),
        ],
    )
    source_end = _tool_scene(
        "End only this actual Heat Metal concentration through the existing owner.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_read_source(),
            *_caster_source_guard(),
            _call(
                "end_srd_concentration_node",
                {"source_state": "{_caster_state_id}", "target_state": "{_caster_state_id}"},
            ),
        ],
    )
    return {
        "check_srd_heated_object_range": range_tool,
        "retire_srd_heat_metal_source": retire,
        "refresh_srd_heat_metal_source": refresh_one,
        "refresh_srd_heat_metal_sources": refresh,
        "check_srd_heat_metal_source": source_check,
        "read_srd_heat_metal_source": source_read,
        "end_srd_heat_metal_source": source_end,
    }


def extend_runtime(text):
    """Bind paid casting and exact concentration cleanup before final damage and reactions."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "resolve_srd_heat_metal_damage": _damage_tool(),
        **_grip_tools(),
        **_lifecycle_tools(),
        "cast_spell_heat_metal_spell": _casting_tool(),
        "reheat_srd_metal": _reheat_tool(),
    }
    public = changed["reheat_srd_metal"]
    public["metadata"]["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Heat Metal",
        "summary": [{"label": "Damage", "value": {"path": "context._heat_damage_row.value"}}],
    }
    changed["reheat_srd_metal_node"] = deepcopy(public)
    public["parameters"] = [p for p in public["parameters"] if p["name"] != "lasting_state"]
    public["effects"] = [
        _call(
            "reheat_srd_metal_node",
            {
                "caster_state": "{_caster_state_id}",
                "source_id": "{source_id}",
                "lasting_state": "state",
            },
            ["_heat_damage_row"],
        )
    ]
    cast = deepcopy(tools["cast_spell"])
    prior = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in prior]
    for effects in list(_effect_lists(cast["effects"])):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'heat_metal_spell'" not in e.get("condition", "")
        ]
    resolution = next(
        e
        for e in _effect_lists(cast["effects"])
        if any(
            "_spell_definition.family == 'information_spell'" in x.get("condition", "") for x in e
        )
    )
    resolution.append(
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'heat_metal_spell'",
            [
                {
                    "call": "cast_spell_heat_metal_spell",
                    "params": {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "concentration_modifier": "{_casting_profile.concentration_save_modifier}",
                        "object_id": "{heated_object_id}",
                    },
                    "export_context": {
                        "_spell_summary_rows": "_heat_summary",
                        "_spell_detail_rows": "_heat_details",
                    },
                },
            ],
        )
    )
    changed["cast_spell"] = cast
    process = deepcopy(tools["process_srd_turn_effects"])
    process["effects"] = [
        e for e in process["effects"] if e.get("call") != "refresh_srd_heat_metal_sources"
    ]
    process["effects"].insert(0, _call("refresh_srd_heat_metal_sources", {}))
    changed["process_srd_turn_effects"] = process
    end = deepcopy(tools["end_srd_concentration_node"])
    end["effects"] = [
        e
        for e in end["effects"]
        if e.get("condition") != "source_state.concentration_effect_family == 'heat_metal_spell'"
    ]
    end["effects"].insert(
        0,
        _when(
            "source_state.concentration_effect_family == 'heat_metal_spell'",
            [
                _call(
                    "retire_srd_heat_metal_source",
                    {
                        "caster_state": "{_source_state_id}",
                        "source_id": "{source_state.heat_metal_source_id}",
                    },
                ),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = end
    text = _replace_tools(text, changed)
    if "      heat_metal_spell:" not in text:
        schema = yaml.dump({"heat_metal_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text


def bind_d20_runtime(text):
    """Consume actual earned grip Disadvantage before the shared D20 owner's final normalization."""
    tools = _load_runtime(text)["tools"]
    d20 = deepcopy(tools["d20_test"])
    d20["effects"] = [
        e
        for e in d20["effects"]
        if e.get("call") != "check_srd_heat_metal_disadvantage"
        and e.get("condition") != "_heat_disadvantage == true"
        and not any(
            child.get("call") == "check_srd_heat_metal_disadvantage"
            for child in e.get("on_true", [])
        )
    ]
    index = next(
        i
        for i, e in enumerate(d20["effects"])
        if e.get("set", {}).get("field") == "_effective_advantage"
    )
    d20["effects"][index:index] = [
        _when(
            "actor_state != null and actor_state.srd_heat_metal_grips != null and "
            "(kind == 'attack_roll' or kind == 'ability_check')",
            [
                _call(
                    "check_srd_heat_metal_disadvantage",
                    {"actor_state": "{_actor_state_id}", "kind": "{kind}"},
                    ["_heat_disadvantage"],
                ),
                _when("_heat_disadvantage == true", [_set("_condition_disadvantage", True)]),
            ],
        ),
    ]
    result = _replace_tools(text, {"d20_test": d20})
    if "*d20_result_card" in result and "&d20_result_card" not in result:
        start = result.index("  d20_test:")
        index = result.index("      result_card:", start)
        result = result[:index] + result[index:].replace(
            "      result_card:", "      result_card: &d20_result_card", 1
        )
    return result
