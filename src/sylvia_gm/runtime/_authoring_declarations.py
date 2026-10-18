"""Shared declarations for runtime effect authoring and structural admission."""

from __future__ import annotations

from sylvia_gm.runtime._effect_executor import _COMPATIBILITY_EFFECT_KEYS, _NEUTRAL_EFFECT_KEYS

EFFECT_BODY_FIELDS = {
    "dice": [
        "expression",
        "modifier",
        "advantage",
        "disadvantage",
        "critical",
        "explode",
        "explode_on",
        "max_explosions",
    ],
    "set": ["field", "value"],
    "set_participant": ["participant", "field", "value"],
    "create_entity": ["id", "value"],
    "require": ["condition", "error"],
    "request_choice": ["choice_id", "prompt", "options"],
    "apply_damage": ["target", "amount"],
    "apply_condition": ["target", "condition"],
    "remove_condition": ["target", "condition"],
    "call": ["call", "name", "params"],
    "trigger": ["trigger", "name", "context"],
    "save_throw": ["target", "ability", "dc", "on_save"],
    "heal": ["target", "amount"],
    "decrement": ["field", "amount"],
    "increment": ["field", "amount"],
    "condition": ["condition", "on_true", "on_false"],
    "log": ["level", "message"],
    "table_lookup": ["table", "key", "default", "required", "exact_selector"],
    "spatial_query": ["scene", "operation", "query", "snapshot"],
    "spatial_mutation": ["scene", "map_id", "expected_revision", "target", "patch"],
    "shot_effect": [
        "scene",
        "snapshot",
        "source_id",
        "target_id",
        "origin",
        "destination",
        "delay_ms",
        "miss",
        "contacts",
        "style",
        "target_kind",
    ],
    "for_each": ["collection", "as", "do"],
    "sort": ["collection", "by", "direction"],
    "apply_ability_damage": ["target", "ability", "amount"],
    "count_successes": ["pool", "modifier", "threshold"],
    "reroll": ["source", "predicate", "modifier", "threshold", "consequence"],
    "hunger_dice": ["pool", "hunger", "sides", "modifier", "threshold"],
    "set_flag": ["flag", "set_flag", "value"],
    "advance_time": ["advance_time", "duration"],
    "list_push": ["list", "value"],
    "list_pop": ["list"],
    "initiative": [
        "combatants",
        "expression",
        "modifier_field",
        "modifier_minimum",
        "modifier_maximum",
        "required_truthy_field",
        "label_field",
        "roll",
    ],
    "advance_initiative": ["skip_field", "skip_equals"],
    "random_table": ["table", "roll", "weight_field", "default"],
    "transition_scene": ["scene", "transition_scene"],
    "aoe": ["aoe", "as", "do"],
    "defer": ["tool", "call", "params", "priority", "reason", "context_vars"],
    "narrate": ["narrate", "text", "style", "priority"],
    "register_initiative_trigger": [
        "id",
        "trigger_id",
        "procedure",
        "procedure_name",
        "source",
        "count",
        "repeats",
        "context_data",
    ],
    "clock_create": ["id", "clock_id", "name", "max_segments", "thresholds", "metadata"],
    "clock_tick": ["id", "clock_id", "segments"],
}

SCALAR_EFFECT_BODIES = frozenset({"call", "trigger"})
NEUTRAL_EFFECT_HANDLERS = tuple(sorted(_NEUTRAL_EFFECT_KEYS))
COMPATIBILITY_EFFECT_HANDLERS = tuple(sorted(_COMPATIBILITY_EFFECT_KEYS))

__all__ = [
    "EFFECT_BODY_FIELDS",
    "SCALAR_EFFECT_BODIES",
    "NEUTRAL_EFFECT_HANDLERS",
    "COMPATIBILITY_EFFECT_HANDLERS",
]
