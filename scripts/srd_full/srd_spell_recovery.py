"""Recovery spells remove exact saved sources and revive through the existing HP owners."""

import json
import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call,
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
    from .srd_spell_removal_adoption import SELECTED_KINDS
    from .srd_timed_senses import _public as _public_card
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
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
    from srd_spell_removal_adoption import SELECTED_KINDS
    from srd_timed_senses import _public as _public_card
    from srd_timed_senses import _replace_tools


PROFILES = {
    "remove_curse": (3, "action", "touch", 5),
    "greater_restoration": (5, "action", "touch", 5),
    "dispel_magic": (3, "action", "distance", 120),
    "dispel_evil_and_good": (5, "action", "self", 0),
    "gentle_repose": (2, "action", "touch", 5),
    "revivify": (3, "action", "touch", 5),
    "raise_dead": (5, "1_hour", "touch", 5),
    "reincarnate": (5, "1_hour", "touch", 5),
    "resurrection": (7, "1_hour", "touch", 5),
    "true_resurrection": (9, "1_hour", "touch", 5),
    "prayer_of_healing": (2, "10_minutes", "distance", 30),
    "heroes_feast": (6, "10_minutes", "self", 0),
}
REVIVALS = ("revivify", "raise_dead", "reincarnate", "resurrection", "true_resurrection")
PUBLIC_TOOLS = {
    "record_srd_deceased_body",
    "record_srd_cursed_item",
    "begin_srd_feast_consumption",
    "finish_srd_feast_consumption",
    "apply_srd_ability_score_reduction",
    "use_srd_dispel_evil_and_good",
    "reroll_srd_reincarnate_species",
    "complete_srd_reincarnation",
}


def _public(tool):
    return _public_card(tool, "spells", "Recovery", [("Actor", "_target_state_id")])


def validate_definition(spell, definition):
    keys = ("base_level", "casting_time", "range_kind", "range_feet")
    if spell not in PROFILES or tuple(definition.get(k) for k in keys) != PROFILES[spell]:
        raise SystemExit(f"Incoherent recovery spell: {spell}")


def _schema():
    properties = {
        "base_level": {"type": "integer", "minimum": 2, "maximum": 9},
        "casting_time": {"type": "string", "enum": ["action", "1_hour", "10_minutes"]},
        "range_kind": {"type": "string", "enum": ["touch", "distance", "self"]},
        "range_feet": {"type": "integer", "minimum": 0, "maximum": 120},
    }
    required = list(properties)
    properties.update(
        {
            "duration_rounds": {"type": "integer", "minimum": 1},
            "concentration": {"type": "boolean"},
            "base_ac": {"type": "integer"},
            "attack_disadvantage": {"type": "boolean"},
            "start_turn_temporary_hp": {"type": "boolean"},
        }
    )
    for name in (
        "perception_bypasses",
        "source_creature_types",
        "condition_immunities",
        "damage_immunities",
    ):
        properties[name] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _refresh():
    return _call(
        "refresh_srd_timed_condition_target",
        {
            "target_state": "{_target_state_id}",
            "lasting_state": "{_lasting_state_id}",
        },
    )


def _clock():
    return _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)])


def _end_attunements():
    return [
        _set("target_state.attuned_items", []),
        _when(
            "target_state.cursed_items != null",
            [
                _set("_dead_items", "target_state.cursed_items"),
                _set("_dead_items_kept", []),
                _set("_dead_attunement", {"attuned": False}),
                _each(
                    "_dead_items",
                    "_dead_item",
                    [_push("_dead_items_kept", "merge(_dead_item, _dead_attunement)")],
                ),
                _set("target_state.cursed_items", "_dead_items_kept"),
            ],
        ),
    ]


def _tools():
    tools = {}
    tools["record_srd_actual_death"] = _tool(
        "Capture actual death time and body source once at the committed lethal owner.",
        [_entity("target_state"), _lasting()],
        [
            _clock(),
            _when(
                "target_state.is_alive == false and target_state.death_tick == null",
                [
                    _set("target_state.death_tick", "lasting_state.lasting_tick"),
                    _set("target_state.death_creature_type", "target_state.creature_type"),
                    _set("target_state.death_was_undead", "target_state.creature_type == 'undead'"),
                    _set("target_state.death_protected_ticks", 0),
                    _set("target_state.death_protection_last_tick", "lasting_state.lasting_tick"),
                    *_end_attunements(),
                ],
            ),
        ],
    )
    tools["record_srd_deceased_body"] = _public(
        _tool(
            (
                "Record admitted historical death/body/soul facts; an actual recorded death time "
                "stays authoritative."
            ),
            [
                _entity("target_state"),
                _lasting(),
                _param("death_tick", "number", minimum=0),
                _param("old_age", "boolean"),
                _param("soul_willing", "boolean"),
                _param("body_available", "boolean"),
                _param("vital_parts_present", "boolean"),
                _param("living_creature_type"),
                _param("name"),
            ],
            [
                _clock(),
                _require(
                    "death_tick <= lasting_state.lasting_tick", "Death cannot be in the future."
                ),
                _when(
                    "target_state.death_tick == null",
                    [
                        _set("target_state.death_tick", "death_tick"),
                        _set("target_state.death_creature_type", "target_state.creature_type"),
                        _set(
                            "target_state.death_was_undead",
                            "target_state.creature_type == 'undead'",
                        ),
                        _set("target_state.death_protected_ticks", 0),
                        _set("target_state.death_protection_last_tick", "death_tick"),
                        *_end_attunements(),
                    ],
                ),
                _set("target_state.death_old_age", "old_age"),
                _set("target_state.soul_willing", "soul_willing"),
                _set("target_state.body_available", "body_available"),
                _set("target_state.vital_parts_present", "vital_parts_present"),
                _set("target_state.living_creature_type", "living_creature_type"),
                _set("target_state.deceased_name", "name"),
            ],
            [
                {
                    "condition": "target_state.is_alive == false",
                    "error": "Body facts require a dead creature.",
                }
            ],
        )
    )
    tools["refresh_srd_corpse_protection"] = _tool(
        "Accrue the union of protected time, excluding overlapping Gentle Repose double counting.",
        [_entity("target_state"), _lasting()],
        [
            _clock(),
            _when(
                "target_state.death_tick != null",
                [
                    _when(
                        "target_state.gentle_repose_until != null",
                        [
                            _set(
                                "_protected_end",
                                "min(lasting_state.lasting_tick, target_state.gentle_repose_until)",
                            ),
                            _set(
                                "target_state.death_protected_ticks",
                                "target_state.death_protected_ticks + "
                                "max(0, _protected_end - target_state.death_protection_last_tick)",
                            ),
                        ],
                    ),
                    _set("target_state.death_protection_last_tick", "lasting_state.lasting_tick"),
                    _set(
                        "target_state.corpse_cannot_become_undead",
                        "target_state.gentle_repose_until != null "
                        "and lasting_state.lasting_tick < target_state.gentle_repose_until",
                    ),
                    _set(
                        "target_state.corpse_decay_protected",
                        "target_state.corpse_cannot_become_undead",
                    ),
                ],
            ),
        ],
    )
    tools["record_srd_cursed_item"] = _public(
        _tool(
            "Bind a cursed item's intrinsic curse and attunement to an actual inventory item.",
            [_entity("target_state"), _param("item"), _param("attuned", "boolean"), _lasting()],
            [
                _require(
                    "target_state.inventory != null and item in target_state.inventory",
                    "A cursed item must belong to this actor's saved inventory.",
                ),
                _require(
                    "attuned == false or target_state.is_alive == true",
                    "A dead creature cannot retain Attunement.",
                ),
                _when("target_state.cursed_items == null", [_set("target_state.cursed_items", [])]),
                _set("_items", "target_state.cursed_items"),
                _each(
                    "_items",
                    "_item",
                    [_require("_item.item != item", "This cursed item is already recorded.")],
                ),
                _push("_items", {"item": "item", "intrinsic_curse": True, "attuned": "attuned"}),
                _set("target_state.cursed_items", "_items"),
                _when(
                    "attuned == true",
                    [
                        _when(
                            "target_state.attuned_items == null",
                            [_set("target_state.attuned_items", [])],
                        ),
                        _set("_attunements", "target_state.attuned_items"),
                        _when("item not in _attunements", [_push("_attunements", "item")]),
                        _require(
                            "len(_attunements) <= 3",
                            "This creature already has three attuned magic items.",
                        ),
                        _set("target_state.attuned_items", "_attunements"),
                    ],
                ),
            ],
        )
    )
    tools["apply_srd_ability_score_reduction"] = _public(
        _tool(
            (
                "Apply a source-qualified score reduction to saved effective abilities and their "
                "real D20 consumers."
            ),
            [
                _entity("target_state"),
                _lasting(),
                _param("ability"),
                _param("amount", "integer", minimum=1, maximum=30),
                _param("source_id"),
                _param("target_character", resolve="participant", required=False),
            ],
            [
                _require(
                    (
                        "ability == 'strength' or ability == 'dexterity' or ability == 'constitut"
                        "ion' or ability == 'intelligence' or ability == 'wisdom' or ability == '"
                        "charisma'"
                    ),
                    "Choose an actual ability.",
                ),
                _require(
                    "target_character != null or target_state.ability_scores != null",
                    "A reduction needs authoritative character or NPC scores.",
                ),
                _when(
                    "target_state.recovery_ability_sources == null",
                    [_set("target_state.recovery_ability_sources", [])],
                ),
                _set("_ability_sources", "target_state.recovery_ability_sources"),
                _each(
                    "_ability_sources",
                    "_ability_source",
                    [
                        _require(
                            "_ability_source.source_id != source_id",
                            "This reduction source is already applied.",
                        )
                    ],
                ),
                _when(
                    "target_state.ability_scores == null",
                    [_set("target_state.ability_scores", "target_character.data.abilities")],
                ),
                _set("_ability_before", "target_state.ability_scores[ability]"),
                _set("_ability_after", "max(1, _ability_before - amount)"),
                _push(
                    "_ability_sources",
                    {
                        "source_id": "source_id",
                        "ability": "ability",
                        "amount": "_ability_before - _ability_after",
                    },
                ),
                _set("target_state.recovery_ability_sources", "_ability_sources"),
                _set("target_state.ability_scores.{ability}", "_ability_after"),
                _set("target_state.has_recovery_ability_reductions", True),
                _when(
                    "target_character == null and target_state.saving_throw_modifiers != null",
                    [
                        _set(
                            "target_state.saving_throw_modifiers.{ability}",
                            "target_state.saving_throw_modifiers[ability] + "
                            "(_ability_after - 10) // 2 - (_ability_before - 10) // 2",
                        )
                    ],
                ),
                _when(
                    "target_character == null and target_state.spellcasting_profile != null",
                    [
                        _set(
                            "_ability_delta",
                            "(_ability_after - 10) // 2 - (_ability_before - 10) // 2",
                        ),
                        _when(
                            "target_state.spellcasting_profile.ability == ability",
                            [
                                *[
                                    _set(
                                        "target_state.spellcasting_profile." + field,
                                        "target_state.spellcasting_profile."
                                        + field
                                        + " + _ability_delta",
                                    )
                                    for field in ("modifier", "attack_bonus", "save_dc")
                                ]
                            ],
                        ),
                        _when(
                            "ability == 'constitution'",
                            [
                                _set(
                                    "target_state.spellcasting_profile.concentration_save_modifier",
                                    (
                                        "target_state.spellcasting_profile.concentration_save_mod"
                                        "ifier + _ability_delta"
                                    ),
                                )
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    tools["restore_srd_reduced_ability"] = _tool(
        (
            "End every saved reduction affecting the selected score, retaining other scores and s"
            "ources."
        ),
        [_entity("target_state"), _param("ability"), _lasting()],
        [
            _set("_ability_restore", 0),
            _set("_ability_sources", []),
            _set("_ability_kept", []),
            _when(
                "target_state.recovery_ability_sources != null",
                [_set("_ability_sources", "target_state.recovery_ability_sources")],
            ),
            _each(
                "_ability_sources",
                "_ability_source",
                [
                    _when(
                        "_ability_source.ability == ability",
                        [_set("_ability_restore", "_ability_restore + _ability_source.amount")],
                        [_push("_ability_kept", "_ability_source")],
                    )
                ],
            ),
            _when(
                "_ability_restore > 0",
                [
                    _set("_ability_before", "target_state.ability_scores[ability]"),
                    _set(
                        "target_state.ability_scores.{ability}",
                        "_ability_before + _ability_restore",
                    ),
                    _when(
                        (
                            "target_state.creature_kind != 'character' and target_state.saving_th"
                            "row_modifiers != null"
                        ),
                        [
                            _set(
                                "target_state.saving_throw_modifiers.{ability}",
                                (
                                    "target_state.saving_throw_modifiers[ability] + (target_state"
                                    ".ability_scores[ability] - 10) // 2 - (_ability_before - 10)"
                                    " // 2"
                                ),
                            )
                        ],
                    ),
                ],
            ),
            _when(
                (
                    "_ability_restore > 0 and target_state.creature_kind != 'character' and targe"
                    "t_state.spellcasting_profile != null"
                ),
                [
                    _set(
                        "_ability_delta",
                        (
                            "(target_state.ability_scores[ability] - 10) // 2 - (_ability_before "
                            "- 10) // 2"
                        ),
                    ),
                    _when(
                        "target_state.spellcasting_profile.ability == ability",
                        [
                            *[
                                _set(
                                    "target_state.spellcasting_profile." + field,
                                    "target_state.spellcasting_profile."
                                    + field
                                    + " + _ability_delta",
                                )
                                for field in ("modifier", "attack_bonus", "save_dc")
                            ]
                        ],
                    ),
                    _when(
                        "ability == 'constitution'",
                        [
                            _set(
                                "target_state.spellcasting_profile.concentration_save_modifier",
                                (
                                    "target_state.spellcasting_profile.concentration_save_modifie"
                                    "r + _ability_delta"
                                ),
                            )
                        ],
                    ),
                ],
            ),
            _set("target_state.recovery_ability_sources", "_ability_kept"),
            _set("target_state.has_recovery_ability_reductions", "len(_ability_kept) > 0"),
        ],
    )
    tools["restore_srd_returned_hit_points"] = _tool(
        "Set the revival effect's initial Hit Points and clear only the0HP recovery consequences.",
        [_entity("target_state"), _param("amount", "integer", minimum=1)],
        [
            _require(
                "target_state.is_alive == true and target_state.hp == 0",
                "Initial returned Hit Points require the committed0HP revival transition.",
            ),
            _set("target_state.hp", "min(amount, target_state.max_hp)"),
            {"remove_condition": {"target": "target_state", "condition": "unconscious"}},
            _set("target_state.death_successes", 0),
            _set("target_state.death_failures", 0),
            _set("target_state.stable", False),
            _set("target_state.stable_recovery_hours_remaining", 0),
            _set("target_state.bloodied", "target_state.hp <= target_state.max_hp // 2"),
        ],
    )
    tools["remove_srd_recovery_curses"] = _tool(
        "End exact Geas and curse sources even when suppressed, preserving independent conditions.",
        [_entity("target_state"), _lasting()],
        [
            _set("_recovery_ids", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_recovery_ids", "target_state.timed_condition_source_ids")],
            ),
            _each(
                "_recovery_ids",
                "_recovery_id",
                [
                    _set("_recovery_source", "lasting_state.timed_condition_sources[_recovery_id]"),
                    _when(
                        (
                            "_recovery_source != null and _recovery_source.installed == true and "
                            "(_recovery_source.control_profile == 'curse' or _recovery_source.con"
                            "trol_profile == 'geas')"
                        ),
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "lasting_state": "{_lasting_state_id}",
                                    "source_id": "{_recovery_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _refresh(),
        ],
    )
    tools["clear_srd_all_returned_conditions"] = _tool(
        (
            "True Resurrection ends each actual condition source and resets Exhaustion, preservin"
            "g unrelated magic."
        ),
        [_entity("target_state"), _lasting()],
        [
            _set(
                "_returned_conditions",
                [
                    f"'{c}'"
                    for c in (
                        "blinded",
                        "charmed",
                        "deafened",
                        "frightened",
                        "grappled",
                        "incapacitated",
                        "invisible",
                        "paralyzed",
                        "petrified",
                        "poisoned",
                        "prone",
                        "restrained",
                        "stunned",
                        "unconscious",
                    )
                ],
            ),
            _each(
                "_returned_conditions",
                "_returned_condition",
                [
                    _when(
                        "_returned_condition != 'dead'",
                        [
                            _call(
                                "remove_srd_condition",
                                {
                                    "target": "{_target_state_id}",
                                    "condition": "{_returned_condition}",
                                },
                            )
                        ],
                    )
                ],
            ),
            _set("target_state.exhaustion_level", 0),
            _refresh(),
        ],
    )
    tools["remove_srd_one_recovery_curse"] = _tool(
        "Greater Restoration ends one chosen curse source, including a selected Geas.",
        [_entity("target_state"), _lasting(), _param("source_id", required=False, default="")],
        [
            _set("_curse_ids", []),
            _set("_recovery_ids", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_recovery_ids", "target_state.timed_condition_source_ids")],
            ),
            _each(
                "_recovery_ids",
                "_recovery_id",
                [
                    _set("_recovery_source", "lasting_state.timed_condition_sources[_recovery_id]"),
                    _when(
                        (
                            "_recovery_source != null and _recovery_source.installed == true and "
                            "(_recovery_source.control_profile == 'curse' or _recovery_source.con"
                            "trol_profile == 'geas')"
                        ),
                        [_push("_curse_ids", "_recovery_id")],
                    ),
                ],
            ),
            _set("_selected_curse", "source_id"),
            _when(
                "source_id == '' and len(_curse_ids) == 1",
                [_set("_selected_curse", "_curse_ids[0]")],
            ),
            _require(
                "len(_curse_ids) == 0 or _selected_curse in _curse_ids",
                "Choose the one installed curse that Greater Restoration ends.",
            ),
            _when(
                "len(_curse_ids) > 0",
                [
                    _call(
                        "retire_srd_timed_condition_source",
                        {"lasting_state": "{_lasting_state_id}", "source_id": "{_selected_curse}"},
                    )
                ],
            ),
            _refresh(),
        ],
    )
    tools["resolve_srd_dispel_source_check"] = _tool(
        (
            "End lower-level ongoing spells; roll an actual casting-ability check for every highe"
            "r source."
        ),
        [
            _entity("caster_state"),
            _param("source_level", "integer"),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
            _param("caster", resolve="participant", required=False),
        ],
        [
            _require(
                "source_level >= 0 and source_level <= 9",
                (
                    "Dispel Magic needs the ongoing source's actual casting level; imported unkno"
                    "wn levels cannot be guessed."
                ),
            ),
            _set("_dispel_succeeded", "source_level <= slot_level"),
            _set("_dispel_roll", None),
            _when(
                "source_level > slot_level",
                [
                    _set("_dispel_dc", "10 + source_level"),
                    _call(
                        "d20_test",
                        {
                            "actor": "{_caster_id}",
                            "actor_state": "{_caster_state_id}",
                            "ability": "{casting_ability}",
                            "kind": "ability_check",
                            "ability_modifier": "{casting_modifier}",
                            "proficiency_bonus": 0,
                            "proficiency_factor": "none",
                            "circumstantial_modifier": 0,
                            "target_number": "{_dispel_dc}",
                        },
                        ["_d20_test", "_outcome"],
                    ),
                    _set("_dispel_roll", "_d20_test"),
                    _set("_dispel_succeeded", "_outcome == 'success'"),
                ],
            ),
        ],
    )
    tools["dispel_srd_target_sources"] = _tool(
        (
            "Retire exact ongoing spell sources on this creature, retaining other targets and ins"
            "tant benefits."
        ),
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
            _param("caster", resolve="participant", required=False),
        ],
        [
            _set("_dispel_checks", []),
            _call(
                "dispel_srd_legacy_concentration",
                {
                    "target_state": "{_target_state_id}",
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "caster": "{_caster_id}",
                    "slot_level": "{slot_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                },
                ["_dispel_legacy_checks"],
            ),
            _set("_dispel_checks", "_dispel_legacy_checks"),
            _set("_recovery_ids", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_recovery_ids", "target_state.timed_condition_source_ids")],
            ),
            _each(
                "_recovery_ids",
                "_recovery_id",
                [
                    _set("_recovery_source", "lasting_state.timed_condition_sources[_recovery_id]"),
                    _when(
                        "_recovery_source != null and _recovery_source.installed == true and "
                        "_recovery_source.spell_id != null and "
                        "_recovery_source.spell_id != 'befuddlement' and "
                        "_recovery_source.dispel_ongoing != false",
                        [
                            _set("_dispel_level", -1),
                            _when(
                                "_recovery_source.spell_level != null",
                                [_set("_dispel_level", "_recovery_source.spell_level")],
                            ),
                            _call(
                                "resolve_srd_dispel_source_check",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "caster": "{_caster_id}",
                                    "source_level": "{_dispel_level}",
                                    "slot_level": "{slot_level}",
                                    "casting_modifier": "{casting_modifier}",
                                    "casting_ability": "{casting_ability}",
                                },
                                ["_dispel_succeeded", "_dispel_roll"],
                            ),
                            _push(
                                "_dispel_checks",
                                {
                                    "source": "_recovery_id",
                                    "roll": "_dispel_roll",
                                    "ended": "_dispel_succeeded",
                                },
                            ),
                            _when(
                                "_dispel_succeeded == true",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "source_id": "{_recovery_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_dispel_effects", []),
            _set("_dispel_kept", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_dispel_effects", "lasting_state.turn_effects")],
            ),
            _each(
                "_dispel_effects",
                "_dispel_effect",
                [
                    _set("_dispel_remove", False),
                    _when(
                        (
                            "_dispel_effect.target == _target_state_id and _dispel_effect.spell !"
                            "= null and _dispel_effect.spell != 'heroes_feast' and _dispel_effect"
                            ".expires_tick != null and lasting_state.lasting_tick < _dispel_effec"
                            "t.expires_tick"
                        ),
                        [
                            _set("_dispel_level", -1),
                            _when(
                                "_dispel_effect.spell_level != null",
                                [_set("_dispel_level", "_dispel_effect.spell_level")],
                            ),
                            _call(
                                "resolve_srd_dispel_source_check",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "caster": "{_caster_id}",
                                    "source_level": "{_dispel_level}",
                                    "slot_level": "{slot_level}",
                                    "casting_modifier": "{casting_modifier}",
                                    "casting_ability": "{casting_ability}",
                                },
                                ["_dispel_succeeded", "_dispel_roll"],
                            ),
                            _set("_dispel_remove", "_dispel_succeeded"),
                            _push(
                                "_dispel_checks",
                                {
                                    "spell": "_dispel_effect.spell",
                                    "roll": "_dispel_roll",
                                    "ended": "_dispel_succeeded",
                                },
                            ),
                        ],
                    ),
                    _when("_dispel_remove != true", [_push("_dispel_kept", "_dispel_effect")]),
                ],
            ),
            _set("lasting_state.turn_effects", "_dispel_kept"),
            _when(
                "target_state.hp != null and target_state.max_hp != null",
                [
                    _call(
                        "project_srd_timed_hit_point_bonus",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                    _call(
                        "resolve_srd_timed_actor_defenses",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                    _call("resolve_srd_timed_senses", {"target_state": "{_target_state_id}"}),
                    _call(
                        "project_srd_timed_actor_mobility",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                    _refresh(),
                ],
            ),
        ],
    )
    tools["resolve_srd_revival"] = _tool(
        (
            "Apply source death limits and soul refusal before committing the spell's actual revi"
            "val consequences."
        ),
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("spell"),
            _param(
                "body_position",
                "array",
                items={"type": "number"},
                required=False,
                default=[],
                max_items=3,
            ),
            _param("spoken_name", required=False, default=""),
        ],
        [
            _call(
                "refresh_srd_corpse_protection",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _set("_revival_succeeded", False),
            _set("_revival_eligible", "target_state.is_alive == false"),
            _when(
                "_revival_eligible == true",
                [
                    _require(
                        (
                            "target_state.death_tick != null and target_state.soul_willing != nul"
                            "l and target_state.death_old_age != null and target_state.body_avail"
                            "able != null"
                        ),
                        "Record death/body/soul facts before resolving revival.",
                    ),
                    _set(
                        "_death_elapsed",
                        "max(0, lasting_state.lasting_tick - target_state.death_tick - "
                        "target_state.death_protected_ticks)",
                    ),
                    _set(
                        "_revival_eligible",
                        "target_state.soul_willing == true and target_state.body_available == true",
                    ),
                    _when(
                        "spell == 'revivify'",
                        [
                            _set(
                                "_revival_eligible",
                                "_revival_eligible and _death_elapsed <= 20 and "
                                "target_state.death_old_age == false",
                            )
                        ],
                    ),
                    _when(
                        "spell == 'raise_dead'",
                        [
                            _require(
                                "target_state.vital_parts_present != null",
                                "Raise Dead needs the body's vital-part fact.",
                            ),
                            _set(
                                "_revival_eligible",
                                (
                                    "_revival_eligible and _death_elapsed <= 288000 and target_st"
                                    "ate.death_was_undead == false and target_state.vital_parts_p"
                                    "resent == true"
                                ),
                            ),
                        ],
                    ),
                    _when(
                        "spell == 'resurrection'",
                        [
                            _set(
                                "_revival_eligible",
                                (
                                    "_revival_eligible and _death_elapsed <= 1051200000 and targe"
                                    "t_state.death_was_undead == false and target_state.death_old"
                                    "_age == false"
                                ),
                            )
                        ],
                    ),
                    _when(
                        "spell == 'true_resurrection'",
                        [
                            _set(
                                "_revival_eligible",
                                (
                                    "target_state.soul_willing == true and _death_elapsed <= 2102"
                                    "400000 and target_state.death_old_age == false"
                                ),
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "_revival_eligible == true",
                [
                    _when(
                        "spell == 'true_resurrection' and target_state.body_available == false",
                        [
                            _require(
                                "spoken_name != '' and spoken_name == target_state.deceased_name",
                                (
                                    "Speak the admitted deceased creature's name when creating it"
                                    "s missing body."
                                ),
                            ),
                            _call(
                                "create_srd_returned_body",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                    "position": "{body_position}",
                                },
                            ),
                        ],
                    ),
                    _set("target_state.is_alive", True),
                    {"remove_condition": {"target": "target_state", "condition": "dead"}},
                    _when(
                        "spell == 'resurrection' or spell == 'true_resurrection'",
                        [
                            _set("target_state.severed_body_parts", []),
                            _set("target_state.vital_parts_present", True),
                        ],
                    ),
                    _when(
                        (
                            "spell == 'raise_dead' or spell == 'resurrection' or spell == 'true_r"
                            "esurrection'"
                        ),
                        [
                            _call(
                                "remove_srd_condition",
                                {"target": "{_target_state_id}", "condition": "poisoned"},
                            )
                        ],
                    ),
                    _when(
                        "spell == 'true_resurrection'",
                        [
                            _call(
                                "remove_srd_recovery_curses",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                            _set("target_state.magical_contagions", []),
                            _call(
                                "clear_srd_all_returned_conditions",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                            _require(
                                "target_state.living_creature_type != null",
                                "True Resurrection needs the living form's creature type.",
                            ),
                            _set("target_state.creature_type", "target_state.living_creature_type"),
                        ],
                    ),
                    _set("_revival_hp", 1),
                    _when(
                        "spell == 'resurrection' or spell == 'true_resurrection'",
                        [_set("_revival_hp", "target_state.max_hp")],
                    ),
                    _call(
                        "restore_srd_returned_hit_points",
                        {"target_state": "{_target_state_id}", "amount": "{_revival_hp}"},
                    ),
                    _set("target_state.attuned_items", []),
                    _when(
                        (
                            "target_state.exhaustion_level != null and target_state.exhaustion_le"
                            "vel > 0"
                        ),
                        [
                            _set(
                                "target_state.exhaustion_level", "target_state.exhaustion_level - 1"
                            )
                        ],
                    ),
                    _when(
                        "spell == 'raise_dead' or spell == 'resurrection'",
                        [_set("target_state.revival_penalty", -4)],
                    ),
                    _when(
                        "spell == 'resurrection' and _death_elapsed >= 10512000",
                        [_set("caster_state.resurrection_tax", True)],
                    ),
                    _set("target_state.death_tick", None),
                    _set("target_state.gentle_repose_until", None),
                    _set("target_state.corpse_cannot_become_undead", False),
                    _set("target_state.corpse_decay_protected", False),
                    _set("target_state.death_successes", 0),
                    _set("target_state.death_failures", 0),
                    _set("target_state.stable", False),
                    _refresh(),
                    _set("_revival_succeeded", True),
                ],
            ),
        ],
    )
    tools["cast_srd_recovery"] = _tool(
        "Resolve the selected recovery spell after the public casting owner spends actual costs.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("spell"),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
            _param("save_dc", "integer"),
            _param("concentration_save_modifier", "integer"),
            _param("target_distance", "number"),
            _param("can_touch", "boolean"),
            _param("caster", resolve="participant", required=False),
            _param("recovery_choice", required=False, default="none"),
            _param("recovery_item", required=False, default=""),
            _param("recovery_source_id", required=False, default=""),
            _param(
                "recovery_position",
                "array",
                items={"type": "number"},
                required=False,
                default=[],
                max_items=3,
            ),
            _param("recovery_surface", required=False, default=""),
            _param("recovery_spoken_name", required=False, default=""),
        ],
        [
            _clock(),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _set("_revival_succeeded", False),
            _set("_recovery_definition", "definitions.spells[spell]"),
            _require(
                "_recovery_definition.range_kind == 'self' or spell == 'prayer_of_healing' or "
                "target_distance <= _recovery_definition.range_feet or "
                "(spell == 'true_resurrection' and target_state.body_available == false)",
                "The selected recovery target is beyond this spell's range.",
            ),
            _require(
                "_recovery_definition.range_kind != 'touch' or can_touch == true or "
                "(spell == 'true_resurrection' and target_state.body_available == false)",
                "This recovery spell requires actual touch.",
            ),
            _when(
                "spell == 'dispel_evil_and_good'",
                [
                    _set("_dispel_self", ["_caster_state_id"]),
                    _call(
                        "start_srd_condition_concentration",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "targets": "{_dispel_self}",
                            "spell": "{spell}",
                            "duration_rounds": 10,
                            "save_modifier": "{concentration_save_modifier}",
                        },
                    ),
                    _call(
                        "install_srd_timed_actor_defense",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "caster": "{_caster_id}",
                            "spellcasting_modifier": "{casting_modifier}",
                            "lasting_state": "{_lasting_state_id}",
                            "actual_spell_level": "{slot_level}",
                        },
                    ),
                    _set("caster_state.dispel_evil_save_dc", "save_dc"),
                ],
            ),
            _when(
                "spell == 'prayer_of_healing'",
                [
                    _call(
                        "resolve_srd_prayer_recovery",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "slot_level": "{slot_level}",
                        },
                    )
                ],
            ),
            _when(
                "spell == 'heroes_feast'",
                [
                    _call(
                        "install_srd_feast",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "position": "{recovery_position}",
                            "surface_id": "{recovery_surface}",
                        },
                        ["_feast_id"],
                    )
                ],
            ),
            _when(
                "spell == 'reincarnate'",
                [
                    _call(
                        "begin_srd_reincarnation",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            ),
            _when(
                "spell == 'dispel_magic'",
                [
                    _call(
                        "dispel_srd_target_sources",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "caster": "{_caster_id}",
                            "slot_level": "{slot_level}",
                            "casting_modifier": "{casting_modifier}",
                            "casting_ability": "{casting_ability}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_dispel_checks"],
                    )
                ],
            ),
            _when(
                (
                    "spell == 'remove_curse' or (spell == 'greater_restoration' and recovery_choi"
                    "ce == 'curse')"
                ),
                [
                    _when(
                        "recovery_item == ''",
                        [
                            _when(
                                "spell == 'remove_curse'",
                                [
                                    _call(
                                        "remove_srd_recovery_curses",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    )
                                ],
                                [
                                    _call(
                                        "remove_srd_one_recovery_curse",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                            "source_id": "{recovery_source_id}",
                                        },
                                    )
                                ],
                            )
                        ],
                        [
                            _set("_items", []),
                            _when(
                                "target_state.cursed_items != null",
                                [_set("_items", "target_state.cursed_items")],
                            ),
                            _set("_item_found", False),
                            _set("_cured_items", []),
                            _each(
                                "_items",
                                "_item",
                                [
                                    _when(
                                        "_item.item == recovery_item",
                                        [
                                            _push(
                                                "_cured_items",
                                                {
                                                    "item": "_item.item",
                                                    "intrinsic_curse": "_item.intrinsic_curse",
                                                    "attuned": False,
                                                },
                                            ),
                                            _set("_item_found", True),
                                        ],
                                        [_push("_cured_items", "_item")],
                                    )
                                ],
                            ),
                            _require(
                                "_item_found == true", "Select a recorded cursed inventory item."
                            ),
                            _set("target_state.cursed_items", "_cured_items"),
                            _when(
                                "target_state.attuned_items != null",
                                [
                                    _set("_attunements", "target_state.attuned_items"),
                                    _set("_attunements_kept", []),
                                    _each(
                                        "_attunements",
                                        "_attunement",
                                        [
                                            _when(
                                                "_attunement != recovery_item",
                                                [_push("_attunements_kept", "_attunement")],
                                            )
                                        ],
                                    ),
                                    _set("target_state.attuned_items", "_attunements_kept"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'greater_restoration'",
                [
                    _require(
                        (
                            "recovery_choice == 'exhaustion' or recovery_choice == 'charmed' or r"
                            "ecovery_choice == 'petrified' or recovery_choice == 'curse' or recov"
                            "ery_choice == 'maximum_hp' or recovery_choice == 'contact_other_plan"
                            "e' or recovery_choice == 'strength' or recovery_choice == 'dexterity"
                            "' or recovery_choice == 'constitution' or recovery_choice == 'intell"
                            "igence' or recovery_choice == 'wisdom' or recovery_choice == 'charis"
                            "ma' or recovery_choice == 'befuddlement'"
                        ),
                        "Select the Greater Restoration effect.",
                    ),
                    _when(
                        "recovery_choice == 'exhaustion'",
                        [
                            _call(
                                "reduce_srd_exhaustion",
                                {"target": "{_target_state_id}", "levels": 1},
                            )
                        ],
                    ),
                    _when(
                        "recovery_choice == 'charmed' or recovery_choice == 'petrified'",
                        [
                            _call(
                                "remove_srd_condition",
                                {"target": "{_target_state_id}", "condition": "{recovery_choice}"},
                            )
                        ],
                    ),
                    _when(
                        "recovery_choice == 'maximum_hp'",
                        [
                            _when(
                                (
                                    "target_state.recovery_max_hp_reduction != null and target_st"
                                    "ate.recovery_max_hp_reduction > 0"
                                ),
                                [
                                    _set(
                                        "target_state.max_hp",
                                        (
                                            "target_state.max_hp + target_state.recovery_max_hp_r"
                                            "eduction"
                                        ),
                                    ),
                                    _set("target_state.recovery_max_hp_reduction", 0),
                                ],
                            )
                        ],
                    ),
                    _when(
                        "recovery_choice == 'contact_other_plane'",
                        [
                            _call(
                                "clear_srd_contact_other_plane",
                                {"target_state": "{_target_state_id}"},
                            )
                        ],
                    ),
                    _when(
                        "recovery_choice == 'befuddlement'",
                        [
                            _call(
                                "clear_srd_befuddlement",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "source_id": "{recovery_source_id}",
                                },
                            )
                        ],
                    ),
                    _when(
                        (
                            "recovery_choice == 'strength' or recovery_choice == 'dexterity' or r"
                            "ecovery_choice == 'constitution' or recovery_choice == 'intelligence"
                            "' or recovery_choice == 'wisdom' or recovery_choice == 'charisma'"
                        ),
                        [
                            _call(
                                "restore_srd_reduced_ability",
                                {
                                    "target_state": "{_target_state_id}",
                                    "ability": "{recovery_choice}",
                                },
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'gentle_repose'",
                [
                    _when(
                        "target_state.is_alive == false",
                        [
                            _require(
                                "target_state.death_tick != null",
                                "Record the corpse's death time before preserving it.",
                            ),
                            _call(
                                "refresh_srd_corpse_protection",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                            _set("_repose_until", "lasting_state.lasting_tick + 288000"),
                            _when(
                                "target_state.gentle_repose_until != null",
                                [
                                    _set(
                                        "_repose_until",
                                        "max(_repose_until, target_state.gentle_repose_until)",
                                    )
                                ],
                            ),
                            _set("target_state.gentle_repose_until", "_repose_until"),
                            _set("target_state.gentle_repose_spell_level", "slot_level"),
                            _set("target_state.corpse_cannot_become_undead", True),
                            _set("target_state.corpse_decay_protected", True),
                        ],
                    )
                ],
            ),
            _when(
                (
                    "spell == 'revivify' or spell == 'raise_dead' or spell == 'resurrection' or s"
                    "pell == 'true_resurrection'"
                ),
                [
                    _call(
                        "resolve_srd_revival",
                        {
                            "target_state": "{_target_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "spell": "{spell}",
                            "body_position": "{recovery_position}",
                            "spoken_name": "{recovery_spoken_name}",
                        },
                        ["_revival_succeeded"],
                    )
                ],
            ),
        ],
    )
    tools.update(_rest_tools())
    tools.update(_dispel_evil_tools())
    tools.update(_reincarnation_tools())
    tools.update(_placement_tools())
    tools.update(_legacy_dispel_tools())
    cast = tools["cast_srd_recovery"]
    cast["effects"] += [
        _set("_recovery_outcome", "'Resolved'"),
        _when(
            "spell == 'revivify' or spell == 'raise_dead' or spell == 'resurrection' or "
            "spell == 'true_resurrection'",
            [
                _set("_recovery_outcome", "'Returned to life'"),
                _when("_revival_succeeded != true", [_set("_recovery_outcome", "'Unaffected'")]),
            ],
        ),
        _when(
            "spell == 'gentle_repose' and target_state.is_alive == true",
            [_set("_recovery_outcome", "'Unaffected'")],
        ),
        _when(
            "spell == 'reincarnate'",
            [
                _set("_recovery_outcome", "'Unaffected'"),
                _when(
                    "target_state.reincarnation != null and "
                    "target_state.reincarnation.complete == false",
                    [_set("_recovery_outcome", "'New body choice pending'")],
                ),
            ],
        ),
        _call(
            "record_srd_spell_target_result",
            {
                "target": "{_target_state_id}",
                "brief": "{_recovery_outcome}",
                "detail": "{_recovery_outcome}; selection {recovery_choice}",
            },
            ["_spell_target_summary", "_spell_target_detail"],
        ),
        _when(
            "target_state.hp != null and target_state.max_hp != null",
            [
                _call(
                    "record_srd_spell_target_result",
                    {
                        "target": "{_target_state_id}",
                        "brief": "{_recovery_outcome}",
                        "detail": "{_recovery_outcome}; selection {recovery_choice}; "
                        "Hit Points {target_state.hp}/{target_state.max_hp}; "
                        "conditions {target_state.conditions}",
                    },
                    ["_spell_target_summary", "_spell_target_detail"],
                ),
            ],
        ),
        _when(
            "spell == 'dispel_magic'",
            [
                _call(
                    "record_srd_spell_target_result",
                    {
                        "target": "{_target_state_id}",
                        "brief": "{_recovery_outcome}",
                        "detail": "Actual source checks: {_dispel_checks}",
                    },
                    ["_spell_target_summary", "_spell_target_detail"],
                ),
            ],
        ),
        _push("_spell_summary_rows", "_spell_target_summary"),
        _push("_spell_detail_rows", "_spell_target_detail"),
    ]
    return tools


def _legacy_dispel_tools():
    "Remove a concentration spell from one actual target without erasing its other targets."
    check = {
        "caster_state": "{_caster_state_id}",
        "caster": "{_caster_id}",
        "slot_level": "{slot_level}",
        "casting_modifier": "{casting_modifier}",
        "casting_ability": "{casting_ability}",
    }
    tools = {}
    tools["dispel_srd_legacy_concentration"] = _tool(
        "Visit only this target's saved legacy concentration indexes and actual slot levels.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
            _param("caster", resolve="participant", required=False),
        ],
        [
            _set("_dispel_legacy_checks", []),
            _set("_dispel_casters", []),
            *[
                _when(
                    f"target_state.{field} != null",
                    [
                        _set("_dispel_index", f"target_state.{field}"),
                        _each(
                            "_dispel_index",
                            "_dispel_caster",
                            [
                                _when(
                                    "_dispel_caster not in _dispel_casters",
                                    [_push("_dispel_casters", "_dispel_caster")],
                                )
                            ],
                        ),
                    ],
                )
                for field in (
                    "concentration_d20_modifier_source_ids",
                    "concentration_resistance_source_ids",
                    "concentration_armor_class_source_ids",
                )
            ],
            _when(
                "target_state.guidance_head_caster_state_id != null",
                [
                    _call(
                        "collect_srd_guidance_casters",
                        {
                            "source_state": "{target_state.guidance_head_caster_state_id}",
                            "target_state": "{_target_state_id}",
                        },
                        ["_guidance_casters"],
                    ),
                    _each(
                        "_guidance_casters",
                        "_dispel_caster",
                        [
                            _when(
                                "_dispel_caster not in _dispel_casters",
                                [_push("_dispel_casters", "_dispel_caster")],
                            )
                        ],
                    ),
                ],
            ),
            _each(
                "_dispel_casters",
                "_dispel_caster",
                [
                    _call(
                        "dispel_srd_legacy_source",
                        {
                            "source_state": "{_dispel_caster}",
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            **check,
                        },
                        ["_dispel_legacy_row"],
                    ),
                    _push("_dispel_legacy_checks", "_dispel_legacy_row"),
                ],
            ),
            _when(
                (
                    "target_state.gentle_repose_until != null and lasting_state.lasting_tick < ta"
                    "rget_state.gentle_repose_until"
                ),
                [
                    _call(
                        "resolve_srd_dispel_source_check",
                        {**check, "source_level": "{target_state.gentle_repose_spell_level}"},
                        ["_dispel_succeeded", "_dispel_roll"],
                    ),
                    _push(
                        "_dispel_legacy_checks",
                        {
                            "spell": "'gentle_repose'",
                            "roll": "_dispel_roll",
                            "ended": "_dispel_succeeded",
                        },
                    ),
                    _when(
                        "_dispel_succeeded == true",
                        [
                            _call(
                                "refresh_srd_corpse_protection",
                                {"target_state": "{_target_state_id}"},
                            ),
                            _set("target_state.gentle_repose_until", None),
                            _set("target_state.corpse_decay_protected", False),
                            _set("target_state.corpse_cannot_become_undead", False),
                        ],
                    ),
                ],
            ),
        ],
    )
    tools["collect_srd_guidance_casters"] = _tool(
        "Follow the bounded existing Guidance source links without inventing another index.",
        [_entity("source_state"), _entity("target_state")],
        [
            _set("_guidance_casters", []),
            _set("_guidance_current", "_source_state_id"),
            *[
                _when(
                    "_guidance_current != ''",
                    [
                        _call(
                            "read_srd_guidance_displacement_link",
                            {
                                "source_state": "{_guidance_current}",
                                "target_state": "{_target_state_id}",
                            },
                            ["_guidance_next", "_guidance_selected"],
                        ),
                        _when(
                            "_guidance_selected == true",
                            [_push("_guidance_casters", "_guidance_current")],
                        ),
                        _set("_guidance_current", "_guidance_next"),
                    ],
                )
                for _ in range(8)
            ],
            _require(
                "_guidance_current == ''",
                "Resolve the existing eight-source Guidance index before adding another.",
            ),
        ],
    )
    tools["read_srd_guidance_displacement_link"] = _tool(
        "Read one current target-qualified Guidance link.",
        [_entity("source_state"), _entity("target_state")],
        [
            _set(
                "_guidance_selected",
                (
                    "source_state.concentration_target_state_id == _target_state_id and source_st"
                    "ate.concentration_spell_id == 'guidance'"
                ),
            ),
            _set("_guidance_next", "''"),
            _when(
                "source_state.concentration_older_caster_state_id != null",
                [_set("_guidance_next", "source_state.concentration_older_caster_state_id")],
            ),
        ],
    )
    tools["dispel_srd_legacy_source"] = _tool(
        "Check the actual ongoing source and detach only the selected creature on success.",
        [
            _entity("source_state"),
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
            _param("caster", resolve="participant", required=False),
        ],
        [
            _call(
                "refresh_srd_concentration_source",
                {
                    "source_state": "{_source_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ["_source_concentration_active"],
            ),
            _set(
                "_dispel_legacy_row",
                {
                    "source": "_source_state_id",
                    "active": "_source_concentration_active",
                    "ended": False,
                },
            ),
            _when(
                "_source_concentration_active == true",
                [
                    _set("_dispel_level", -1),
                    _when(
                        "source_state.concentration_spell_level != null",
                        [_set("_dispel_level", "source_state.concentration_spell_level")],
                    ),
                    _call(
                        "resolve_srd_dispel_source_check",
                        {**check, "source_level": "{_dispel_level}"},
                        ["_dispel_succeeded", "_dispel_roll"],
                    ),
                    _set(
                        "_dispel_legacy_row",
                        {
                            "source": "_source_state_id",
                            "active": True,
                            "ended": "_dispel_succeeded",
                            "roll": "_dispel_roll",
                        },
                    ),
                    _when(
                        "_dispel_succeeded == true",
                        [
                            _when(
                                (
                                    "source_state.concentration_effect_family == 'multi_target_co"
                                    "ncentration_d20_modifier' or source_state.concentration_effe"
                                    "ct_family == 'multi_target_concentration_ability_advantage'"
                                ),
                                [
                                    _call(
                                        "remove_srd_concentration_d20_source",
                                        {
                                            "source_state": "{_source_state_id}",
                                            "target_state": "{_target_state_id}",
                                        },
                                    ),
                                    *[
                                        _when(
                                            f"source_state.{field} != null",
                                            [
                                                _set("_dispel_targets", f"source_state.{field}"),
                                                _set("_dispel_retained", []),
                                                _each(
                                                    "_dispel_targets",
                                                    "_dispel_target",
                                                    [
                                                        _when(
                                                            "_dispel_target != _target_state_id",
                                                            [
                                                                _push(
                                                                    "_dispel_retained",
                                                                    "_dispel_target",
                                                                )
                                                            ],
                                                        )
                                                    ],
                                                ),
                                                _set(f"source_state.{field}", "_dispel_retained"),
                                            ],
                                        )
                                        for field in (
                                            "concentration_target_state_ids",
                                            "concentration_strength_target_state_ids",
                                            "concentration_dexterity_target_state_ids",
                                            "concentration_intelligence_target_state_ids",
                                            "concentration_wisdom_target_state_ids",
                                            "concentration_charisma_target_state_ids",
                                        )
                                    ],
                                    _when(
                                        "len(source_state.concentration_target_state_ids) > 0",
                                        [
                                            _set(
                                                "source_state.concentration_target_state_id",
                                                "source_state.concentration_target_state_ids[0]",
                                            )
                                        ],
                                        [
                                            _call(
                                                "end_srd_concentration_node",
                                                {
                                                    "source_state": "{_source_state_id}",
                                                    "target_state": "{_target_state_id}",
                                                },
                                            )
                                        ],
                                    ),
                                ],
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_source_state_id}",
                                            "target_state": "{_target_state_id}",
                                        },
                                    )
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    return tools


def _placement_tools():
    "Use admitted colliders and existing entity mutation for physical recovery placement."
    state = _param("state", resolve="entity", required=False, default="state")
    position = _param("position", "array", items={"type": "number"}, min_items=3, max_items=3)
    tools = {}
    tools["check_srd_recovery_space"] = _tool(
        (
            "Reject occupied admitted space, retaining the chosen supporting surface and returned"
            " actor."
        ),
        [
            state,
            position,
            _param("half_width", "number", minimum=0.01),
            _param("height", "number", minimum=0.01),
            _param("plane"),
            _param("surface_id", required=False, default=""),
            _param("exclude_entity", required=False, default=""),
        ],
        [
            _require(
                "state.spatial_scene != null and state.spatial_scene.map.grid.unit == 'ft'",
                "Physical recovery placement needs the actual admitted scene in feet.",
            ),
            _set(
                "_space_min",
                [
                    "position[0] - half_width",
                    "position[1] - half_width",
                    "position[2] + 0.000000001",
                ],
            ),
            _set(
                "_space_max",
                ["position[0] + half_width", "position[1] + half_width", "position[2] + height"],
            ),
            {
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "area",
                    "query": {
                        "map_id": "{state.spatial_scene.map.id}",
                        "shape": {"kind": "box", "min": "{_space_min}", "max": "{_space_max}"},
                        "include": ["geometry", "entities"],
                        "max_results": 4096,
                    },
                },
                "store_as": "_recovery_space",
            },
            _require(
                "_recovery_space.truncated != true",
                "This placement exceeds the admitted collision query bound.",
            ),
            _set("_space_results", "_recovery_space.results"),
            _each(
                "_space_results",
                "_space_item",
                [
                    _require(
                        "_space_item.metadata.plane != null",
                        "Intersecting scene objects need their actual plane fact.",
                    ),
                    _require(
                        "_space_item.metadata.plane != plane or "
                        "(_space_item.kind == 'geometry' and _space_item.id == surface_id) or "
                        "(_space_item.kind == 'entity' and _space_item.id == exclude_entity)",
                        "The selected recovery space is occupied.",
                    ),
                ],
            ),
            _when(
                "surface_id != ''",
                [
                    _set("_surface_found", False),
                    _set("_surfaces", "state.spatial_scene.geometry"),
                    _each(
                        "_surfaces",
                        "_surface",
                        [
                            _when(
                                "_surface.id == surface_id and _surface.active == true",
                                [
                                    _require(
                                        (
                                            "_surface.volume.kind == 'box' and _surface.metadata."
                                            "plane == plane"
                                        ),
                                        (
                                            "This supporting surface must be an admitted flat box"
                                            " on the caster's plane."
                                        ),
                                    ),
                                    _require(
                                        "_surface.volume.max[2] == position[2] and "
                                        "_surface.volume.min[0] <= position[0] - half_width and "
                                        "_surface.volume.max[0] >= position[0] + half_width and "
                                        "_surface.volume.min[1] <= position[1] - half_width and "
                                        "_surface.volume.max[1] >= position[1] + half_width",
                                        (
                                            "The actual surface must support the whole feast's10f"
                                            "oot footprint."
                                        ),
                                    ),
                                    _set("_surface_found", True),
                                ],
                            )
                        ],
                    ),
                    _require(
                        "_surface_found == true", "Choose an active admitted supporting surface."
                    ),
                ],
            ),
        ],
    )
    tools["create_srd_returned_body"] = _tool(
        (
            "Create the absent body's actual scene placement within10feet without replacing posit"
            "ion authority."
        ),
        [_entity("caster_state"), _entity("target_state"), position, state],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_body_plane", "_info_scene_entity.metadata.plane"),
            _set("_body_caster_position", "_info_position"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_body_caster_position}", "end": "{position}"},
                ["_scene_distance"],
            ),
            _require(
                "_scene_distance <= 10", "True Resurrection creates the new body within10feet."
            ),
            _set("_body_entity", None),
            _set("_body_entities", "state.spatial_scene.entities"),
            _each(
                "_body_entities",
                "_body_candidate",
                [
                    _when(
                        "_body_candidate.id == _target_state_id or "
                        "_body_candidate.metadata.participant_ref == _target_state_id",
                        [
                            _require(
                                "_body_entity == null",
                                "The returned actor's scene identity is ambiguous.",
                            ),
                            _set("_body_entity", "_body_candidate"),
                        ],
                    )
                ],
            ),
            _require(
                "_body_entity != null and _body_entity.footprint.kind == 'box'",
                (
                    "The absent body needs this actor's admitted box footprint; new scene entity "
                    "admission remains separate."
                ),
            ),
            _set(
                "_body_half_width",
                (
                    "max(_body_entity.footprint.half_extents[0], _body_entity.footprint.half_exte"
                    "nts[1])"
                ),
            ),
            _call(
                "check_srd_recovery_space",
                {
                    "position": "{position}",
                    "half_width": "{_body_half_width}",
                    "height": "{_body_entity.height}",
                    "plane": "{_body_plane}",
                    "exclude_entity": "{_body_entity.id}",
                },
            ),
            _set("_body_patch", {"plane": "_body_plane", "body_available": True}),
            _set("_body_metadata", "merge(_body_entity.metadata, _body_patch)"),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{state.spatial_scene.map.revision}",
                    "target": {"kind": "entity", "id": "{_body_entity.id}"},
                    "patch": {
                        "position": "{position}",
                        "active": True,
                        "metadata": "{_body_metadata}",
                    },
                },
                "store_as": "_returned_body_mutation",
            },
            _set("target_state.body_available", True),
        ],
    )
    return tools


def _reincarnation_tools():
    species = (
        "dragonborn",
        "dwarf",
        "elf",
        "gnome",
        "goliath",
        "halfling",
        "human",
        "orc",
        "tiefling",
    )
    tools = {}
    tools["roll_srd_reincarnate_species"] = _tool(
        (
            "Roll the pinned d10 table; a1 remains a saved reroll requirement without an invented"
            " retry limit."
        ),
        [_entity("target_state")],
        [
            {"dice": {"expression": "d10"}, "store_as": "_species_roll"},
            _set("target_state.reincarnation.roll", "_species_roll"),
            _set("target_state.reincarnation.species", None),
            *[
                _when(
                    f"_species_roll.total == {index}",
                    [_set("target_state.reincarnation.species", f"'{name}'")],
                )
                for index, name in enumerate(species, 2)
            ],
        ],
    )
    tools["begin_srd_reincarnation"] = _tool(
        (
            "Commit the paid revival's source-owned new-body choices only for a willing dead Huma"
            "noid within10days."
        ),
        [_entity("target_state"), _entity("caster_state"), _lasting()],
        [
            _call(
                "refresh_srd_corpse_protection",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _when(
                "target_state.is_alive == false",
                [
                    _require(
                        (
                            "target_state.death_tick != null and target_state.soul_willing != nul"
                            "l and target_state.body_available != null"
                        ),
                        "Record this Humanoid's death, remains and willing soul facts.",
                    ),
                    _when(
                        (
                            "target_state.death_creature_type == 'humanoid' and target_state.soul"
                            "_willing == true and target_state.body_available == true and lasting"
                            "_state.lasting_tick - target_state.death_tick - target_state.death_p"
                            "rotected_ticks <= 288000"
                        ),
                        [
                            _require(
                                (
                                    "target_state.reincarnation == null or target_state.reincarna"
                                    "tion.complete == true"
                                ),
                                (
                                    "Complete the source-owned body choices before another Reinca"
                                    "rnate."
                                ),
                            ),
                            _set(
                                "target_state.reincarnation",
                                {
                                    "caster": "_caster_state_id",
                                    "created_tick": "lasting_state.lasting_tick",
                                    "complete": False,
                                },
                            ),
                            _call(
                                "roll_srd_reincarnate_species",
                                {"target_state": "{_target_state_id}"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    tools["reroll_srd_reincarnate_species"] = _public(
        _tool(
            "Continue the actual species table only when the previous source-owned result was1.",
            [_entity("target_state"), _lasting()],
            [
                _require(
                    (
                        "target_state.reincarnation != null and target_state.reincarnation.comple"
                        "te == false and target_state.reincarnation.roll.total == 1"
                    ),
                    "This Reincarnate result does not call for another roll.",
                ),
                _call("roll_srd_reincarnate_species", {"target_state": "{_target_state_id}"}),
            ],
        )
    )
    tools["complete_srd_reincarnation"] = _public(
        _tool(
            (
                "Replace the actual Character File's species and its selected source grants, then"
                " revive in the new body."
            ),
            [
                _entity("target_state"),
                _lasting(),
                _param("target_character", resolve="participant"),
                _param("hit_points", "integer", minimum=1),
                _param("species_override", required=False, default=""),
                _param("lineage", required=False, default=""),
                _param("small", "boolean", required=False, default=False),
                _param("species_skill", required=False, default=""),
                _param("skill_proficiencies", "array", items={"type": "string"}),
                _param("origin_feats", "array", items={"type": "string"}),
                _param("spellcasting_grants", "array", items={"type": "object"}),
            ],
            [
                _require(
                    "target_state.is_alive == false and target_state.reincarnation != null and "
                    "target_state.reincarnation.complete == false",
                    "This creature has no pending new-body choice.",
                ),
                _require(
                    (
                        "target_character.kind == 'player_character' and target_character.validat"
                        "ion_status == 'validated' and target_character.package_id == 'wotc.srd.5"
                        ".2.1' and _target_character_id == _target_state_id"
                    ),
                    "The reincarnated body requires this actor's actual validated Character File.",
                ),
                _set("_body_species", "target_state.reincarnation.species"),
                _when("species_override != ''", [_set("_body_species", "species_override")]),
                _set("_body_species_choices", [f"'{name}'" for name in species]),
                _require(
                    "_body_species in _body_species_choices",
                    "Resolve table rerolls or choose a pinned playable species.",
                ),
                _set("_body_speed", 30),
                _set("_body_darkvision", 60),
                _set("_body_size_rank", 3),
                _when(
                    "_body_species == 'gnome' or _body_species == 'halfling' or "
                    "((_body_species == 'human' or _body_species == 'tiefling') and small == true)",
                    [_set("_body_size_rank", 2)],
                ),
                _when(
                    "_body_species == 'dwarf' or _body_species == 'orc'",
                    [_set("_body_darkvision", 120)],
                ),
                _when(
                    "_body_species == 'goliath'",
                    [_set("_body_speed", 35), _set("_body_darkvision", 0)],
                ),
                _when(
                    "_body_species == 'human' or _body_species == 'halfling'",
                    [_set("_body_darkvision", 0)],
                ),
                _when(
                    "_body_species == 'elf'",
                    [
                        _require(
                            "lineage == 'drow' or lineage == 'high_elf' or lineage == 'wood_elf'",
                            "Choose this Elf's actual lineage.",
                        ),
                        _when("lineage == 'drow'", [_set("_body_darkvision", 120)]),
                        _when("lineage == 'wood_elf'", [_set("_body_speed", 35)]),
                        _require(
                            (
                                "species_skill == 'insight' or species_skill == 'perception' or s"
                                "pecies_skill == 'survival'"
                            ),
                            "Choose the new Elf's Keen Senses proficiency.",
                        ),
                    ],
                ),
                _when(
                    "_body_species == 'human' or _body_species == 'elf'",
                    [
                        _require(
                            "species_skill != '' and species_skill in skill_proficiencies",
                            "Include the selected species proficiency.",
                        )
                    ],
                ),
                _set("_former_grants", "target_character.data.spellcasting_grants"),
                _each(
                    "_former_grants",
                    "_former_grant",
                    [
                        _set("_former_policy", "definitions.spell_grants[_former_grant.source]"),
                        _when(
                            "_former_policy.species == ''",
                            [
                                _require(
                                    "_former_grant in spellcasting_grants",
                                    (
                                        "Reincarnate retains the former class and subclass spell "
                                        "capabilities."
                                    ),
                                )
                            ],
                        ),
                    ],
                ),
                _set("_former_skills", "target_character.data.skill_proficiencies"),
                _each(
                    "_former_skills",
                    "_former_skill",
                    [
                        _require(
                            "_former_skill in skill_proficiencies",
                            "Retain existing skills until their species provenance is recorded.",
                        )
                    ],
                ),
                _set("_former_feats", "target_character.data.origin_feats"),
                _each(
                    "_former_feats",
                    "_former_feat",
                    [
                        _require(
                            "_former_feat in origin_feats",
                            (
                                "Reincarnate retains non-species feats; do not erase unknown prov"
                                "enance."
                            ),
                        )
                    ],
                ),
                {
                    "set_participant": {
                        "participant": "_target_character_id",
                        "field": "species",
                        "value": "_body_species",
                    }
                },
                {
                    "set_participant": {
                        "participant": "_target_character_id",
                        "field": "speed",
                        "value": "_body_speed",
                    }
                },
                {
                    "set_participant": {
                        "participant": "_target_character_id",
                        "field": "size_rank",
                        "value": "_body_size_rank",
                    }
                },
                {
                    "set_participant": {
                        "participant": "_target_character_id",
                        "field": "skill_proficiencies",
                        "value": "skill_proficiencies",
                    }
                },
                {
                    "set_participant": {
                        "participant": "_target_character_id",
                        "field": "origin_feats",
                        "value": "origin_feats",
                    }
                },
                {
                    "set_participant": {
                        "participant": "_target_character_id",
                        "field": "spellcasting_grants",
                        "value": "spellcasting_grants",
                    }
                },
                _call("validate_srd_spell_grants", {"caster": "{_target_character_id}"}),
                _set("target_state.species", "_body_species"),
                _set("target_state.species_lineage", "lineage"),
                _set("target_state.speed", "_body_speed"),
                _set("target_state.darkvision_feet", "_body_darkvision"),
                _set("target_state.size_rank", "_body_size_rank"),
                _set("target_state.does_not_sleep", "_body_species == 'elf'"),
                _set("target_state.creature_type", "'humanoid'"),
                _set("target_state.severed_body_parts", []),
                _set("target_state.vital_parts_present", True),
                _set("_new_prepared", []),
                _each(
                    "spellcasting_grants",
                    "_new_grant",
                    [
                        _set("_new_spells", "_new_grant.spells"),
                        _each(
                            "_new_spells",
                            "_new_spell",
                            [
                                _when(
                                    "_new_spell not in _new_prepared",
                                    [_push("_new_prepared", "_new_spell")],
                                )
                            ],
                        ),
                    ],
                ),
                _set("target_state.prepared_spells", "_new_prepared"),
                _require(
                    "hit_points <= target_state.max_hp",
                    "The admitted Reincarnate HP ruling cannot exceed the new body's maximum.",
                ),
                _set("target_state.is_alive", True),
                {"remove_condition": {"target": "target_state", "condition": "dead"}},
                _call(
                    "restore_srd_returned_hit_points",
                    {"target_state": "{_target_state_id}", "amount": "{hit_points}"},
                ),
                _when(
                    "target_state.exhaustion_level != null and target_state.exhaustion_level > 0",
                    [_set("target_state.exhaustion_level", "target_state.exhaustion_level - 1")],
                ),
                _set("target_state.attuned_items", []),
                _set("target_state.death_tick", None),
                _set("target_state.reincarnation.complete", True),
                _refresh(),
            ],
        )
    )
    return tools


def _dispel_evil_tools():
    tools = {}
    tools["break_srd_qualified_enchantment"] = _tool(
        (
            "Remove only Charmed/Frightened/possession sources caused by the spell's named creatu"
            "re types."
        ),
        [_entity("target_state"), _lasting()],
        [
            _set("_recovery_ids", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_recovery_ids", "target_state.timed_condition_source_ids")],
            ),
            _set("_dispel_types", ["'celestial'", "'elemental'", "'fey'", "'fiend'", "'undead'"]),
            _each(
                "_recovery_ids",
                "_recovery_id",
                [
                    _set("_recovery_source", "lasting_state.timed_condition_sources[_recovery_id]"),
                    _when(
                        (
                            "_recovery_source != null and _recovery_source.installed == true and "
                            "(_recovery_source.condition == 'charmed' or _recovery_source.conditi"
                            "on == 'frightened')"
                        ),
                        [
                            _require(
                                "_recovery_source.caster_state_id != ''",
                                "Break Enchantment needs this condition's actual causing creature.",
                            ),
                            _call(
                                "check_srd_timed_actor_defense_source_type",
                                {
                                    "source_state": "{_recovery_source.caster_state_id}",
                                    "source_types": "{_dispel_types}",
                                },
                                ["_actor_defense_source_matches"],
                            ),
                            _when(
                                "_actor_defense_source_matches == true",
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "source_id": "{_recovery_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "target_state.possession_source_state_id != null",
                [
                    _call(
                        "check_srd_timed_actor_defense_source_type",
                        {
                            "source_state": "{target_state.possession_source_state_id}",
                            "source_types": "{_dispel_types}",
                        },
                        ["_actor_defense_source_matches"],
                    ),
                    _when(
                        "_actor_defense_source_matches == true",
                        [_set("target_state.possession_source_state_id", None)],
                    ),
                ],
            ),
            _refresh(),
        ],
    )
    tools["use_srd_dispel_evil_and_good"] = _public(
        _tool(
            (
                "Spend the concentration spell's later Magic action for Break Enchantment or Dism"
                "issal, ending it early."
            ),
            [
                _entity("caster_state"),
                _entity("target_state"),
                _lasting(),
                _param("choice", "enum", enum_values=["break_enchantment", "dismissal"]),
                _param("target_character", resolve="participant", required=False),
                _param("can_touch", "boolean", required=False, default=False),
            ],
            [
                _clock(),
                _require(
                    (
                        "caster_state.concentration_spell_id == 'dispel_evil_and_good' and lastin"
                        "g_state.lasting_tick < caster_state.concentration_expires_tick and caste"
                        "r_state.is_alive == true and not ('incapacitated' in caster_state.condit"
                        "ions or 'paralyzed' in caster_state.conditions or 'stunned' in caster_st"
                        "ate.conditions or 'petrified' in caster_state.conditions or 'unconscious"
                        "' in caster_state.conditions)"
                    ),
                    "This caster has no available Dispel Evil and Good concentration.",
                ),
                _call(
                    "resolve_srd_scene_relation",
                    {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                    [
                        "_scene_distance",
                        "_scene_same_plane",
                        "_scene_can_see",
                        "_scene_target_entity_id",
                    ],
                ),
                _require(
                    "_scene_same_plane == true and _scene_distance <= 5",
                    "The selected creature must be within5feet.",
                ),
                _require(
                    "choice != 'dismissal' or _scene_can_see == true",
                    "Dismissal needs actual sight of this creature.",
                ),
                _require(
                    "choice != 'break_enchantment' or can_touch == true",
                    "Break Enchantment needs an admitted touch.",
                ),
                _when(
                    "combat.encounter_active == true",
                    [
                        _require(
                            (
                                "combat.turn_active == true and combat.active_actor == _caster_st"
                                "ate_id"
                            ),
                            "Use the caster's active turn.",
                        ),
                        _call("spend_srd_action", {"magic_action": True}),
                    ],
                ),
                _set("_dismissed", False),
                _when(
                    "choice == 'break_enchantment'",
                    [
                        _call(
                            "break_srd_qualified_enchantment",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        )
                    ],
                ),
                _when(
                    "choice == 'dismissal'",
                    [
                        _set(
                            "_dispel_types",
                            ["'celestial'", "'elemental'", "'fey'", "'fiend'", "'undead'"],
                        ),
                        _when(
                            "target_state.creature_type in _dispel_types",
                            [
                                _require(
                                    "target_state.home_plane != null",
                                    "Dismissal needs the creature's admitted home plane.",
                                ),
                                _set(
                                    "_dismissal_modifier",
                                    "target_state.saving_throw_modifiers.charisma",
                                ),
                                _call(
                                    "d20_test",
                                    {
                                        "actor": "{_target_character_id}",
                                        "actor_state": "{_target_state_id}",
                                        "ability": "charisma",
                                        "kind": "saving_throw",
                                        "ability_modifier": "{_dismissal_modifier}",
                                        "proficiency_bonus": 0,
                                        "proficiency_factor": "none",
                                        "circumstantial_modifier": 0,
                                        "target_number": "{caster_state.dispel_evil_save_dc}",
                                    },
                                    ["_d20_test", "_outcome"],
                                ),
                                _when(
                                    "_outcome == 'failure'",
                                    [
                                        _call(
                                            "dismiss_srd_creature_to_home_plane",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "lasting_state": "{_lasting_state_id}",
                                            },
                                            ["_dismissed"],
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _call(
                    "end_srd_concentration_node",
                    {
                        "source_state": "{_caster_state_id}",
                        "target_state": "{caster_state.concentration_target_state_id}",
                    },
                ),
                _when(
                    "combat.encounter_active != true",
                    [
                        _call(
                            "advance_srd_lasting_clock",
                            {"lasting_state": "{_lasting_state_id}", "elapsed_ticks": 2},
                        )
                    ],
                ),
            ],
        )
    )
    tools["dismiss_srd_creature_to_home_plane"] = _tool(
        "Change the actual admitted scene entity's plane, retaining its unrelated metadata.",
        [
            _entity("target_state"),
            _lasting(),
            _param("state", resolve="entity", required=False, default="state"),
        ],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_target_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_dismissed", False),
            _set("_destination_plane", "target_state.home_plane"),
            _when(
                "_info_scene_entity.metadata.plane != target_state.home_plane",
                [
                    _when(
                        "target_state.creature_type == 'fey'",
                        [_set("_destination_plane", "'feywild'")],
                    ),
                    _when(
                        "target_state.creature_type == 'undead'",
                        [_set("_destination_plane", "'shadowfell'")],
                    ),
                    _set("_plane_patch", {"plane": "_destination_plane"}),
                    _set("_plane_metadata", "merge(_info_scene_entity.metadata, _plane_patch)"),
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{state.spatial_scene.map.revision}",
                            "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                            "patch": {"metadata": "{_plane_metadata}"},
                        },
                        "store_as": "_dismissal_mutation",
                    },
                    _set("_dismissed", True),
                ],
            ),
        ],
    )
    return tools


def _rest_tools():
    position = ("_scene_distance", "_scene_same_plane")
    tools = {}
    tools["register_srd_prayer_targets"] = _tool(
        (
            "Save the chosen Prayer participants before casting begins and verify their initial a"
            "ctual range."
        ),
        [
            _entity("caster_state"),
            _lasting(),
            _param("targets", "array", items={"type": "string"}, max_items=5),
        ],
        [
            _require(
                "len(targets) >= 1 and len(targets) <= 5",
                "Prayer chooses one to five creatures at the start.",
            ),
            _set("_prayer_rows", []),
            _set("_prayer_seen", []),
            _each(
                "targets",
                "_prayer_target",
                [
                    _require(
                        "_prayer_target not in _prayer_seen",
                        "Prayer participants must be distinct.",
                    ),
                    _push("_prayer_seen", "_prayer_target"),
                    _call(
                        "qualify_srd_prayer_target",
                        {"caster_state": "{_caster_state_id}", "target_state": "{_prayer_target}"},
                        ["_prayer_in_range"],
                    ),
                    _require(
                        "_prayer_in_range == true",
                        (
                            "Prayer starts with all chosen creatures within30feet in the admitted"
                            " scene."
                        ),
                    ),
                    _push("_prayer_rows", {"target": "_prayer_target", "eligible": True}),
                ],
            ),
            _set("caster_state.prayer_cast_targets", "_prayer_rows"),
            _set("_prayer_casters", []),
            _when(
                "lasting_state.prayer_casters != null",
                [_set("_prayer_casters", "lasting_state.prayer_casters")],
            ),
            _when(
                "_caster_state_id not in _prayer_casters",
                [_push("_prayer_casters", "_caster_state_id")],
            ),
            _set("lasting_state.prayer_casters", "_prayer_casters"),
        ],
    )
    tools["qualify_srd_prayer_target"] = _tool(
        "Use actual admitted caster and target positions for Prayer's ongoing range clause.",
        [_entity("caster_state"), _entity("target_state"), _lasting()],
        [
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                position,
            ),
            _set(
                "_prayer_in_range",
                (
                    "target_state.is_alive == true and _scene_same_plane == true and _scene_dista"
                    "nce <= 30"
                ),
            ),
        ],
    )
    tools["observe_srd_prayer_caster"] = _tool(
        (
            "Irreversibly mark a chosen participant ineligible after leaving range during the act"
            "ual channel."
        ),
        [_entity("caster_state"), _lasting()],
        [
            _when(
                (
                    "caster_state.extended_casting != null and caster_state.extended_casting.spel"
                    "l == 'prayer_of_healing' and caster_state.prayer_cast_targets != null"
                ),
                [
                    _set("_prayer_rows", "caster_state.prayer_cast_targets"),
                    _set("_prayer_next", []),
                    _each(
                        "_prayer_rows",
                        "_prayer_row",
                        [
                            _when(
                                "_prayer_row.eligible == true",
                                [
                                    _call(
                                        "qualify_srd_prayer_target",
                                        {
                                            "caster_state": "{_caster_state_id}",
                                            "target_state": "{_prayer_row.target}",
                                        },
                                        ["_prayer_in_range"],
                                    ),
                                    _push(
                                        "_prayer_next",
                                        {
                                            "target": "_prayer_row.target",
                                            "eligible": "_prayer_in_range",
                                        },
                                    ),
                                ],
                                [_push("_prayer_next", "_prayer_row")],
                            )
                        ],
                    ),
                    _set("caster_state.prayer_cast_targets", "_prayer_next"),
                ],
            )
        ],
    )
    tools["observe_srd_prayer_movement"] = _tool(
        (
            "Check actual movement against every active Prayer channel without another position a"
            "uthority."
        ),
        [_lasting()],
        [
            _when(
                "lasting_state.prayer_casters != null",
                [
                    _set("_prayer_casters", "lasting_state.prayer_casters"),
                    _each(
                        "_prayer_casters",
                        "_prayer_caster",
                        [_call("observe_srd_prayer_caster", {"caster_state": "{_prayer_caster}"})],
                    ),
                ],
            )
        ],
    )
    tools["grant_srd_prayer_recovery"] = _tool(
        (
            "Grant actual Short Rest resources and Hit Die choices, then roll this spell's healin"
            "g once."
        ),
        [_entity("target_state"), _lasting(), _param("slot_level", "integer")],
        [
            _when(
                "target_state.is_alive == true and target_state.prayer_of_healing_used != true",
                [
                    _call(
                        "recover_srd_rest_resources",
                        {"target_state": "{_target_state_id}", "kind": "short"},
                    ),
                    _set("target_state.short_rest_hit_dice_available", True),
                    _set("target_state.short_rest_hit_dice_tick", "lasting_state.lasting_tick"),
                    {"dice": {"expression": "{slot_level}d8"}, "store_as": "_prayer_healing"},
                    _call(
                        "restore_hit_points",
                        {"target": "{_target_state_id}", "amount": "{_prayer_healing.total}"},
                    ),
                    _set("target_state.prayer_of_healing_used", True),
                ],
            )
        ],
    )
    tools["resolve_srd_prayer_recovery"] = _tool(
        (
            "Apply Prayer only to the source-owned creatures that remained eligible throughout ca"
            "sting."
        ),
        [_entity("caster_state"), _lasting(), _param("slot_level", "integer")],
        [
            _require(
                "caster_state.prayer_cast_targets != null",
                "Choose Prayer participants at begin_srd_spell_cast.",
            ),
            _call("observe_srd_prayer_caster", {"caster_state": "{_caster_state_id}"}),
            _set("_prayer_rows", "caster_state.prayer_cast_targets"),
            _each(
                "_prayer_rows",
                "_prayer_row",
                [
                    _when(
                        "_prayer_row.eligible == true",
                        [
                            _call(
                                "grant_srd_prayer_recovery",
                                {
                                    "target_state": "{_prayer_row.target}",
                                    "slot_level": "{slot_level}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            )
                        ],
                    )
                ],
            ),
            _set("caster_state.prayer_cast_targets", None),
        ],
    )
    tools["install_srd_feast"] = _tool(
        (
            "Save a one-hour feast; its instantaneous creation does not grant benefits before con"
            "sumption."
        ),
        [
            _entity("caster_state"),
            _lasting(),
            _param("position", "array", items={"type": "number"}, min_items=3, max_items=3),
            _param("surface_id"),
        ],
        [
            _clock(),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ["_info_position", "_info_scene_entity"],
            ),
            _set("_feast_plane", "_info_scene_entity.metadata.plane"),
            _set(
                "_feast_nearest",
                [
                    "max(position[0] - 5, min(position[0] + 5, _info_position[0]))",
                    "max(position[1] - 5, min(position[1] + 5, _info_position[1]))",
                    "position[2]",
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_info_position}", "end": "{_feast_nearest}"},
                ["_scene_distance"],
            ),
            _require(
                "_scene_distance <= 5 and surface_id != ''",
                "Place the actual10foot feast cube on a surface next to the caster.",
            ),
            _call(
                "check_srd_recovery_space",
                {
                    "position": "{position}",
                    "half_width": 5,
                    "height": 10,
                    "plane": "{_feast_plane}",
                    "surface_id": "{surface_id}",
                },
            ),
            _when(
                "lasting_state.recovery_feast_sequence == null",
                [_set("lasting_state.recovery_feast_sequence", 0)],
            ),
            _set(
                "lasting_state.recovery_feast_sequence", "lasting_state.recovery_feast_sequence + 1"
            ),
            _call(
                "name_srd_saved_feast",
                {"feast_id": "feast_{lasting_state.recovery_feast_sequence}"},
                ["_feast_id"],
            ),
            _when(
                "lasting_state.recovery_feasts == null", [_set("lasting_state.recovery_feasts", {})]
            ),
            _set(
                "lasting_state.recovery_feasts.{_feast_id}",
                {
                    "caster": "_caster_state_id",
                    "created_tick": "lasting_state.lasting_tick",
                    "vanishes_tick": "lasting_state.lasting_tick + 1200",
                    "participants": [],
                    "position": "position",
                    "plane": "_feast_plane",
                    "surface": "surface_id",
                },
            ),
            _set("lasting_state.recovery_feasts.{_feast_id}.consumption_start", None),
            _set("caster_state.last_feast_id", "_feast_id"),
        ],
    )
    tools["name_srd_saved_feast"] = _tool(
        "Capture the owning cast's formatted monotonic feast ID without dotted state keys.",
        [_param("feast_id")],
        [_set("_feast_id", "feast_id")],
    )
    tools["begin_srd_feast_consumption"] = _public(
        _tool(
            (
                "Choose a living consumer while this actual feast remains; benefits require the f"
                "ull hour."
            ),
            [_entity("target_state"), _lasting(), _param("feast_id")],
            [
                _clock(),
                _set("_feast", "lasting_state.recovery_feasts[feast_id]"),
                _require("_feast != null", "Choose an actual saved feast."),
                _require(
                    "lasting_state.lasting_tick < _feast.vanishes_tick",
                    "This feast has already disappeared.",
                ),
                _set("_feast_start", 0),
                _when(
                    "_feast.consumption_start != null",
                    [_set("_feast_start", "_feast.consumption_start")],
                ),
                _when(
                    "_feast.consumption_start == null",
                    [
                        _set("_feast_start", "lasting_state.lasting_tick"),
                        _set(
                            "lasting_state.recovery_feasts.{feast_id}.consumption_start",
                            "lasting_state.lasting_tick",
                        ),
                        _set(
                            "lasting_state.recovery_feasts.{feast_id}.vanishes_tick",
                            "lasting_state.lasting_tick + 1200",
                        ),
                    ],
                ),
                _set("_feast", "lasting_state.recovery_feasts[feast_id]"),
                _require(
                    "lasting_state.lasting_tick == _feast_start",
                    "Choose consumers at the start of this feast's one-hour consumption.",
                ),
                _require(
                    "target_state.is_alive == true",
                    "Only a living creature can consume this feast.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_target_state_id}"},
                    ["_info_position", "_info_scene_entity"],
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _feast.plane",
                    "The consumer must be on the actual feast's plane.",
                ),
                _set(
                    "_feast_nearest",
                    [
                        (
                            "max(_feast.position[0] - 5, min(_feast.position[0] + 5, _info_positi"
                            "on[0]))"
                        ),
                        (
                            "max(_feast.position[1] - 5, min(_feast.position[1] + 5, _info_positi"
                            "on[1]))"
                        ),
                        "_feast.position[2]",
                    ],
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_info_position}", "end": "{_feast_nearest}"},
                    ["_scene_distance"],
                ),
                _require(
                    "_scene_distance <= 5", "This consumer must actually be at the saved feast."
                ),
                _set("_feast_people", "_feast.participants"),
                _require(
                    "len(_feast_people) < 12 and _target_state_id not in _feast_people",
                    "A feast has at most12 distinct consumers.",
                ),
                _push("_feast_people", "_target_state_id"),
                _set("lasting_state.recovery_feasts.{feast_id}.participants", "_feast_people"),
                _set("target_state.feast_consuming", "feast_id"),
                _set("target_state.feast_consumption_tick", "lasting_state.lasting_tick"),
                _set("target_state.feast_consumption_interrupted", False),
                _when(
                    "lasting_state.recovery_feast_consumer_ids == null",
                    [_set("lasting_state.recovery_feast_consumer_ids", [])],
                ),
                _set("_feast_consumers", "lasting_state.recovery_feast_consumer_ids"),
                _when(
                    "_target_state_id not in _feast_consumers",
                    [_push("_feast_consumers", "_target_state_id")],
                ),
                _set("lasting_state.recovery_feast_consumer_ids", "_feast_consumers"),
            ],
        )
    )
    tools["finish_srd_feast_consumption"] = _public(
        _tool(
            (
                "Resolve one actual hour of uninterrupted consumption and retain24hour source ben"
                "efits."
            ),
            [_entity("target_state"), _lasting(), _param("feast_id")],
            [
                _clock(),
                _set("_feast", "lasting_state.recovery_feasts[feast_id]"),
                _require(
                    "_feast != null and target_state.feast_consuming == feast_id and "
                    "target_state.feast_consumption_interrupted != true",
                    "This creature has no uninterrupted feast consumption.",
                ),
                _require(
                    (
                        "target_state.is_alive == true and lasting_state.lasting_tick >= _feast.v"
                        "anishes_tick and target_state.feast_consumption_tick + 1200 <= _feast.va"
                        "nishes_tick"
                    ),
                    "The full one-hour consumption must finish before benefits apply.",
                ),
                {"dice": {"expression": "2d10"}, "store_as": "_feast_hp"},
                _set("_feast_expiry", "_feast.vanishes_tick + 28800"),
                _when(
                    "lasting_state.lasting_tick < _feast.vanishes_tick + 28800",
                    [
                        _call(
                            "install_srd_timed_hit_point_bonus",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                                "caster_state_id": "{_feast.caster}",
                                "spell": "heroes_feast",
                                "duration_rounds": 14400,
                                "hit_point_bonus": "{_feast_hp.total}",
                                "actual_spell_level": 6,
                                "expires_tick": "{_feast_expiry}",
                            },
                        )
                    ],
                ),
                _set("target_state.heroes_feast_until", "_feast.vanishes_tick + 28800"),
                _set("target_state.feast_consuming", None),
                _set("target_state.feast_completed", "feast_id"),
                _set("target_state.feast_hit_point_roll", "_feast_hp"),
                _refresh(),
            ],
        )
    )
    completion = tools["finish_srd_feast_consumption"]
    completion["effects"] = [
        _when("target_state.feast_completed != feast_id", completion["effects"])
    ]
    tools["observe_srd_feast_consumer"] = _tool(
        "Settle a saved uninterrupted feast when actual time reaches the end of consumption.",
        [_entity("target_state"), _lasting()],
        [
            _set("_feast_consumer_pending", False),
            _when(
                "target_state.feast_consuming != null",
                [
                    _set("_feast", "lasting_state.recovery_feasts[target_state.feast_consuming]"),
                    _when(
                        (
                            "target_state.is_alive == true and target_state.feast_consumption_int"
                            "errupted != true"
                        ),
                        [
                            _set("_feast_consumer_pending", True),
                            _when(
                                "lasting_state.lasting_tick >= _feast.vanishes_tick",
                                [
                                    _call(
                                        "finish_srd_feast_consumption_internal",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                            "feast_id": "{target_state.feast_consuming}",
                                        },
                                    ),
                                    _set("_feast_consumer_pending", False),
                                ],
                            ),
                        ],
                        [_set("target_state.feast_consuming", None)],
                    ),
                ],
            ),
        ],
    )
    tools["observe_srd_feast_consumers"] = _tool(
        "Visit only pending feast consumers and remove settled or interrupted entries.",
        [_lasting()],
        [
            _set("_feast_consumers", "lasting_state.recovery_feast_consumer_ids"),
            _set("_feast_pending", []),
            _each(
                "_feast_consumers",
                "_feast_consumer",
                [
                    _call(
                        "observe_srd_feast_consumer",
                        {
                            "target_state": "{_feast_consumer}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_feast_consumer_pending"],
                    ),
                    _when(
                        "_feast_consumer_pending == true",
                        [_push("_feast_pending", "_feast_consumer")],
                    ),
                ],
            ),
            _set("lasting_state.recovery_feast_consumer_ids", "_feast_pending"),
        ],
    )
    tools["resolve_srd_feast_immunity"] = _tool(
        (
            "Project the feast's Poison resistance and condition immunity from its saved benefit "
            "lifetime."
        ),
        [_entity("target_state"), _lasting()],
        [
            _set(
                "_feast_active",
                (
                    "target_state.heroes_feast_until != null and lasting_state.lasting_tick < tar"
                    "get_state.heroes_feast_until"
                ),
            )
        ],
    )
    return tools


def _stamp_cast_levels(tools, changed):
    "Pass actual cast level only down paths that install an ongoing spell source."
    installers = {
        "install_srd_timed_condition_source",
        "install_srd_timed_actor_defense",
        "install_srd_timed_senses",
        "install_srd_timed_actor_mobility",
        "install_srd_timed_hit_point_bonus",
        "install_srd_timed_armor_class_floor",
        "install_srd_timed_speed_bonus",
        "install_srd_timed_death_prevention",
    }
    installers |= {
        name
        for name, tool in tools.items()
        if name.startswith("install_")
        and any(
            e.get("set", {}).get("field") == "caster_state.concentration_spell_id"
            for e in _walk(tool.get("effects", []))
        )
    }
    reached = set(installers)
    while True:
        ancestors = {
            name
            for name, tool in tools.items()
            if name != "cast_srd_recovery"
            and name.startswith(("cast_", "retarget_", "install_"))
            and any(e.get("call") in reached for e in _walk(tool.get("effects", [])))
        }
        next_reached = reached | ancestors
        if next_reached == reached:
            break
        reached = next_reached
    for name in reached:
        tool = changed.get(name, deepcopy(tools[name]))
        if name != "cast_spell" and not any(
            p["name"] == "actual_spell_level" for p in tool["parameters"]
        ):
            tool["parameters"].append(
                _param(
                    "actual_spell_level",
                    "integer",
                    required=False,
                    default=-1,
                    minimum=-1,
                    maximum=9,
                    model_hidden=True,
                )
            )
        for node in _walk(tool.get("effects", [])):
            if node.get("call") in reached and node["call"] not in {
                "cast_spell",
                "cast_srd_recovery",
            }:
                node["params"]["actual_spell_level"] = (
                    "{_slot_level}" if name == "cast_spell" else "{actual_spell_level}"
                )
            for key in ("set", "list_push"):
                record = node.get(key, {}).get("value")
                if (
                    name in installers
                    and isinstance(record, dict)
                    and "spell" in record
                    and "kind" in record
                ):
                    record["spell_level"] = "actual_spell_level"
        if name == "install_srd_timed_condition_source":
            tool["effects"] = [
                e
                for e in tool["effects"]
                if e.get("set", {}).get("field")
                != (
                    "lasting_state.timed_condition_sources.{_allocated_timed_condition_source_id}"
                    ".spell_level"
                )
            ]
            tool["effects"].append(
                _set(
                    (
                        "lasting_state.timed_condition_sources.{_allocated_timed_condition_source"
                        "_id}.spell_level"
                    ),
                    "actual_spell_level",
                )
            )
        elif name in installers and any(
            e.get("set", {}).get("field") == "caster_state.concentration_spell_id"
            for e in _walk(tool.get("effects", []))
        ):
            tool["effects"] = [
                e
                for e in tool["effects"]
                if e.get("set", {}).get("field") != "caster_state.concentration_spell_level"
            ]
            tool["effects"].append(
                _set("caster_state.concentration_spell_level", "actual_spell_level")
            )
        changed[name] = tool


def extend_runtime(text):
    "Compose recovery hooks at real package owners; inactive damage and rolls stay cheap."
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = _tools()
    if "end_srd_transformation_source" in tools:
        dispel = changed["dispel_srd_target_sources"]
        dispel["effects"].insert(
            -1,
            _when(
                "target_state.transformation_source_ids != null",
                [
                    _set("_dispel_transforms", "target_state.transformation_source_ids"),
                    _each(
                        "_dispel_transforms",
                        "_dispel_transform_id",
                        [
                            _set(
                                "_dispel_transform",
                                "lasting_state.transformation_sources[_dispel_transform_id]",
                            ),
                            _when(
                                "_dispel_transform != null and _dispel_transform.installed == true",
                                [
                                    _call(
                                        "resolve_srd_dispel_source_check",
                                        {
                                            "caster_state": "{_caster_state_id}",
                                            "caster": "{_caster_id}",
                                            "source_level": "{_dispel_transform.spell_level}",
                                            "slot_level": "{slot_level}",
                                            "casting_modifier": "{casting_modifier}",
                                            "casting_ability": "{casting_ability}",
                                        },
                                        ["_dispel_succeeded", "_dispel_roll"],
                                    ),
                                    _push(
                                        "_dispel_checks",
                                        {
                                            "source": "_dispel_transform_id",
                                            "ended": "_dispel_succeeded",
                                            "roll": "_dispel_roll",
                                        },
                                    ),
                                    _when(
                                        "_dispel_succeeded == true",
                                        [
                                            _call(
                                                "end_srd_transformation_source",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "source_id": "{_dispel_transform_id}",
                                                    "lasting_state": "{_lasting_state_id}",
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
        )
    if "clear_srd_contact_other_plane" not in tools:
        changed["clear_srd_contact_other_plane"] = _tool(
            (
                "Retire only Contact Other Plane Incapacitated sources; the information family ow"
                "ns casting."
            ),
            [_entity("target_state"), _lasting()],
            [
                _set("_recovery_ids", []),
                _when(
                    "target_state.timed_condition_source_ids != null",
                    [_set("_recovery_ids", "target_state.timed_condition_source_ids")],
                ),
                _each(
                    "_recovery_ids",
                    "_recovery_id",
                    [
                        _set(
                            "_recovery_source",
                            "lasting_state.timed_condition_sources[_recovery_id]",
                        ),
                        _when(
                            "_recovery_source != null and _recovery_source.installed == true and "
                            "_recovery_source.spell_id == 'contact_other_plane'",
                            [
                                _call(
                                    "retire_srd_timed_condition_source",
                                    {
                                        "lasting_state": "{_lasting_state_id}",
                                        "source_id": "{_recovery_id}",
                                    },
                                )
                            ],
                        ),
                    ],
                ),
                _refresh(),
            ],
        )
    owned = {"record_srd_actual_death", "cast_srd_recovery"}

    def strip_owned(value):
        if isinstance(value, list):
            value[:] = [
                node
                for node in value
                if not (
                    isinstance(node, dict)
                    and (
                        node.get("call") in owned
                        or "_spell_definition.family == 'recovery'" in node.get("condition", "")
                        or (
                            "record_srd_actual_death" in str(node)
                            and "death_tick == null" in node.get("condition", "")
                        )
                        or node.get("set", {}).get("field") == "_recovery_roll_penalty"
                        or node.get("condition", "").endswith("revival_penalty != null")
                        or node.get("condition")
                        == "actor_state.has_recovery_ability_reductions == true"
                    )
                )
            ]
            for node in value:
                strip_owned(node)
        elif isinstance(value, dict):
            for node in value.values():
                strip_owned(node)

    for name, original in tools.items():
        if name in changed:
            continue
        tool = deepcopy(original)
        strip_owned(tool.get("effects", []))
        altered = tool != original
        death_targets = {
            "apply_srd_damage": "target",
            "apply_srd_composite_damage": "target",
            "death_saving_throw": "_target_id",
            "gain_srd_exhaustion": "_target_id",
            "cast_spell_instantaneous_threshold_death_damage": "_target_id",
            "project_srd_timed_hit_point_bonus": "_target_state_id",
        }
        if name in death_targets:

            def insert_death(value, target_id=death_targets[name]):
                if isinstance(value, list):
                    for index, node in reversed(list(enumerate(value))):
                        if (
                            isinstance(node, dict)
                            and node.get("apply_condition", {}).get("condition") == "dead"
                        ):
                            value.insert(
                                index + 1,
                                _call(
                                    "record_srd_actual_death",
                                    {"target_state": "{" + target_id + "}"},
                                ),
                            )
                        elif isinstance(node, dict):
                            insert_death(node)
                elif isinstance(value, dict):
                    for node in value.values():
                        insert_death(node)

            insert_death(tool["effects"])
            altered = True
        if name == "cast_spell":
            if not any(
                e.get("set", {}).get("field") == "_spell_cast_failed"
                and e["set"].get("value") is False
                for e in tool["effects"]
            ):
                tool["effects"].insert(0, _set("_spell_cast_failed", False))
            tool["parameters"] = [
                p
                for p in tool["parameters"]
                if p["name"]
                not in {
                    "recovery_choice",
                    "recovery_item",
                    "recovery_source_id",
                    "recovery_position",
                    "recovery_surface",
                    "recovery_spoken_name",
                }
            ]
            tool["parameters"] += [
                _param("recovery_choice", required=False, default="none"),
                _param("recovery_item", required=False, default=""),
                _param("recovery_source_id", required=False, default=""),
                _param(
                    "recovery_position",
                    "array",
                    items={"type": "number"},
                    required=False,
                    default=[],
                    max_items=3,
                ),
                _param("recovery_surface", required=False, default=""),
                _param("recovery_spoken_name", required=False, default=""),
            ]
            tool["pre_conditions"] = [
                p for p in tool["pre_conditions"] if "resurrection_tax" not in p["condition"]
            ]
            tool["pre_conditions"].append(
                {
                    "condition": "caster_state.resurrection_tax != true",
                    "error": "Resurrection's caster must finish a Long Rest before casting.",
                }
            )
            dispatch = _when(
                "_spell_cast_failed != true and _spell_definition.family == 'recovery'",
                [
                    _set("_recovery_target", "_target_id"),
                    _when(
                        "_spell_definition.range_kind == 'self' or spell == 'prayer_of_healing'",
                        [_set("_recovery_target", "_caster_state_id")],
                    ),
                    _call(
                        "cast_srd_recovery",
                        {
                            "target_state": "{_recovery_target}",
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "slot_level": "{_slot_level}",
                            "caster": "{_caster_id}",
                            "casting_modifier": "{_casting_profile.modifier}",
                            "casting_ability": "{_casting_profile.ability}",
                            "save_dc": "{_casting_profile.save_dc}",
                            ("concentration_save_modifier"): (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                            "target_distance": "{target_distance}",
                            "can_touch": "{caster_can_touch_target}",
                            "recovery_choice": "{recovery_choice}",
                            "recovery_item": "{recovery_item}",
                            "recovery_source_id": "{recovery_source_id}",
                            "recovery_position": "{recovery_position}",
                            "recovery_surface": "{recovery_surface}",
                            "recovery_spoken_name": "{recovery_spoken_name}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows", "_revival_succeeded"],
                    ),
                ],
            )
            # Selected source owners qualify real geometry themselves. Do not
            # manufacture a creature recipient or a numeric distance for them.
            selected_call = deepcopy(dispatch["on_true"][-1])
            for parameter in ("target_state", "target_distance"):
                selected_call["params"].pop(parameter)
            ordinary_dispatch = dispatch["on_true"]
            ordinary_dispatch[:0] = [
                _require(
                    "_target_id != null or _spell_definition.range_kind == 'self' "
                    "or spell == 'prayer_of_healing'",
                    "Select an actual ordinary Recovery recipient.",
                ),
                _require(
                    "target_distance != null", "Qualify the ordinary Recovery recipient range."
                ),
            ]
            dispatch["on_true"] = [
                _set("_recovery_source_intents", [repr(kind) for kind in SELECTED_KINDS]),
                _when(
                    "spell == 'dispel_magic' and recovery_choice in _recovery_source_intents",
                    [selected_call],
                    ordinary_dispatch,
                ),
            ]

            def insert_dispatch(value, owned_dispatch=dispatch):
                if isinstance(value, list):
                    for index, effect in enumerate(value):
                        if (
                            isinstance(effect, dict)
                            and "_spell_definition.family == 'timed_control_affliction'"
                            in effect.get("condition", "")
                            and "cast_srd_control_afflictions" in str(effect)
                        ):
                            value.insert(index, owned_dispatch)
                            return True
                        if insert_dispatch(effect):
                            return True
                elif isinstance(value, dict):
                    return any(insert_dispatch(item) for item in value.values())
                return False

            if not insert_dispatch(tool["effects"]):
                raise ValueError("Recovery needs the admitted public family dispatch.")
            altered = True
        if name == "complete_srd_rest":
            tool["effects"] = [
                e for e in tool["effects"] if "target_state.revival_penalty" not in str(e)
            ]
            tool["effects"].append(
                _when(
                    "target_state.rest_kind == 'long'",
                    [
                        _when(
                            "target_state.revival_penalty != null",
                            [
                                _set(
                                    "target_state.revival_penalty",
                                    "min(0, target_state.revival_penalty + 1)",
                                )
                            ],
                        ),
                        _set("target_state.resurrection_tax", False),
                        _set("target_state.prayer_of_healing_used", False),
                    ],
                )
            )
            altered = True
        if name == "d20_test":
            for node in _walk(tool["effects"]):
                setting = node.get("set", {})
                if (
                    setting.get("field") == "_ability_modifier"
                    and setting.get("value") == "(actor.data.abilities[ability] - 10) // 2"
                ):
                    setting["value"] = "(actor.data.abilities[ability] - 10) // 2"
                    node["recovery_ability_owner"] = True

            def ability_hook(value):
                if isinstance(value, list):
                    for index, node in reversed(list(enumerate(value))):
                        if isinstance(node, dict) and node.pop("recovery_ability_owner", False):
                            value.insert(
                                index + 1,
                                _when(
                                    "actor_state.has_recovery_ability_reductions == true",
                                    [
                                        _set(
                                            "_ability_modifier",
                                            "(actor_state.ability_scores[ability] - 10) // 2",
                                        )
                                    ],
                                ),
                            )
                        elif isinstance(node, dict):
                            ability_hook(node)
                elif isinstance(value, dict):
                    for node in value.values():
                        ability_hook(node)

            ability_hook(tool["effects"])
            for node in _walk(tool["effects"]):
                setting = node.get("set", {})
                if setting.get("field") == "_combined_modifier" and isinstance(
                    setting.get("value"), str
                ):
                    setting["value"] = setting["value"].replace(" + _recovery_roll_penalty", "")

            def insert_revival_penalty(value):
                if isinstance(value, list):
                    value[:] = [
                        node
                        for node in value
                        if not (
                            isinstance(node, dict)
                            and node.get("condition")
                            == "actor_state != null and actor_state.revival_penalty != null"
                            and node.get("on_true") in (
                                [_set("_combined_modifier",
                                      "_combined_modifier + actor_state.revival_penalty")],
                                [_set("_adjusted_total",
                                      "_adjusted_total + actor_state.revival_penalty")],
                            )
                        )
                    ]
                    for index, node in reversed(list(enumerate(value))):
                        if not isinstance(node, dict):
                            continue
                        if (
                            node.get("set", {}).get("field") == "_adjusted_total"
                            and node["set"].get("value") is not None
                        ):
                            value.insert(
                                index + 1,
                                _when(
                                    "actor_state != null and actor_state.revival_penalty != null",
                                    [
                                        _set(
                                            "_adjusted_total",
                                            "_adjusted_total + actor_state.revival_penalty",
                                        )
                                    ],
                                ),
                            )
                        else:
                            insert_revival_penalty(node)
                elif isinstance(value, dict):
                    for node in value.values():
                        insert_revival_penalty(node)

            insert_revival_penalty(tool["effects"])
            for node in _walk(tool["effects"]):
                setting = node.get("set", {})
                if (
                    setting.get("field") == "_condition_disadvantage"
                    and setting.get("value") is False
                ):
                    setting["value"] = (
                        "actor_state != null and actor_state.resurrection_tax == true"
                    )
            altered = True
        if name == "death_saving_throw":
            for node in _walk(tool["effects"]):
                setting = node.get("set", {})
                if setting.get("field") == "_death_save_modifier" and isinstance(
                    setting.get("value"), str
                ):
                    setting["value"] = setting["value"].replace(" + _recovery_roll_penalty", "")
            for index, node in reversed(list(enumerate(tool["effects"]))):
                if node.get("set", {}).get("field") == "_death_save_modifier":
                    tool["effects"].insert(
                        index + 1,
                        _when(
                            "target.revival_penalty != null",
                            [
                                _set(
                                    "_death_save_modifier",
                                    "_death_save_modifier + target.revival_penalty",
                                )
                            ],
                        ),
                    )
            altered = True
        if altered:
            changed[name] = tool
    _stamp_cast_levels(tools, changed)
    for name in PUBLIC_TOOLS:
        implementation = changed[name]
        changed[name + "_internal"] = deepcopy(implementation)
        changed[name + "_internal"]["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        params = [p for p in implementation["parameters"] if p["name"] != "lasting_state"]
        forwarded = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in params
        }
        forwarded["lasting_state"] = "state"
        effects = [_call(name + "_internal", forwarded)]
        if "check_srd_no_pending_reaction" in tools:
            effects.insert(0, _call("check_srd_no_pending_reaction", {}))
        changed[name] = _public(_tool(implementation["description"], params, effects))
    text = _replace_tools(text, changed)
    start = text.index("  d20_test:")
    end = start + re.search(r"(?m)^  [a-z_]+:", text[start + 3 :]).start() + 3
    # Restore the shared result-card anchor after rendering this owning D20 tool.
    fragment = text[start:end].replace("result_card:\n", "result_card: &d20_result_card\n", 1)
    text = text[:start] + fragment + text[end:]
    schema_line = "      recovery: " + json.dumps(_schema())
    if "      recovery:" in text:
        text = re.sub(r"(?m)^      recovery:.*$", schema_line, text, count=1)
    else:
        text = text.replace(
            "      timed_control_affliction:", schema_line + "\n      timed_control_affliction:", 1
        )
    return text


def extend_casting_runtime(text):
    "Attach Prayer's participants after the shared long-casting owner has been generated."
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = {}
    begin = deepcopy(tools["begin_srd_spell_cast"])
    begin["parameters"] = [p for p in begin["parameters"] if p["name"] != "prayer_targets"]
    begin["parameters"].append(
        _param(
            "prayer_targets",
            "array",
            items={"type": "string"},
            required=False,
            default=[],
            max_items=5,
        )
    )
    begin["effects"] = [e for e in begin["effects"] if "register_srd_prayer_targets" not in str(e)]
    begin["effects"].append(
        _when(
            "spell == 'prayer_of_healing' and caster_state.extended_casting != null and "
            "caster_state.extended_casting.spell == 'prayer_of_healing'",
            [
                _call(
                    "register_srd_prayer_targets",
                    {"caster_state": "{_caster_state_id}", "targets": "{prayer_targets}"},
                )
            ],
        )
    )
    begin["pre_conditions"] = [
        p for p in begin.get("pre_conditions", []) if "resurrection_tax" not in p["condition"]
    ]
    begin["pre_conditions"].append(
        {
            "condition": "caster_state.resurrection_tax != true",
            "error": "Finish the Resurrection caster's Long Rest before starting a spell.",
        }
    )
    begin["pre_conditions"] = [
        p for p in begin["pre_conditions"] if "befuddled" not in p["condition"]
    ]
    begin["pre_conditions"].append(
        {
            "condition": "caster_state.befuddled != true",
            "error": "Befuddlement prevents starting this spell.",
        }
    )
    changed["begin_srd_spell_cast"] = begin
    move = deepcopy(tools["move_srd_scene_creature"])
    move["effects"] = [
        e
        for e in move["effects"]
        if "observe_srd_prayer_movement" not in str(e)
        and "feast_consumption_interrupted" not in str(e)
    ]
    move["effects"].append(_call("observe_srd_prayer_movement", {}))
    move["effects"].append(
        _when(
            "actor_state.feast_consuming != null",
            [_set("actor_state.feast_consumption_interrupted", True)],
        )
    )
    changed["move_srd_scene_creature"] = move
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    clock["effects"] = [e for e in clock["effects"] if "observe_srd_prayer_movement" not in str(e)]
    clock["effects"].append(
        _when(
            "lasting_state.prayer_casters != null",
            [_call("observe_srd_prayer_movement", {"lasting_state": "{_lasting_state_id}"})],
        )
    )
    clock["effects"] = [e for e in clock["effects"] if "observe_srd_feast_consumers" not in str(e)]
    clock["effects"].append(
        _when(
            "lasting_state.recovery_feast_consumer_ids != null and "
            "len(lasting_state.recovery_feast_consumer_ids) > 0",
            [_call("observe_srd_feast_consumers", {"lasting_state": "{_lasting_state_id}"})],
        )
    )
    changed["advance_srd_lasting_clock"] = clock
    immunity = deepcopy(tools["check_srd_target_condition_immunity"])
    immunity["effects"] = [
        e for e in immunity["effects"] if "resolve_srd_feast_immunity" not in str(e)
    ]
    immunity["effects"].append(
        _when(
            "target_state.heroes_feast_until != null",
            [
                _call(
                    "resolve_srd_feast_immunity",
                    {"target_state": "{_target_state_id}"},
                    ["_feast_active"],
                ),
                _when(
                    (
                        "_feast_active == true and (condition == 'frightened' or condition == 'po"
                        "isoned')"
                    ),
                    [_set("_source_condition_immune", True)],
                ),
            ],
        )
    )
    changed["check_srd_target_condition_immunity"] = immunity
    suppression = deepcopy(tools["resolve_srd_timed_actor_condition_suppression"])
    suppression["effects"] = [
        e for e in suppression["effects"] if "resolve_srd_feast_immunity" not in str(e)
    ]
    # Preserve the original sources. Feast expiry therefore restores ongoing Poisoned/Frightened.
    suppression["effects"].append(
        _when(
            "target_state.heroes_feast_until != null",
            [
                _call(
                    "resolve_srd_feast_immunity",
                    {"target_state": "{_target_state_id}"},
                    ["_feast_active"],
                ),
                _when(
                    "_feast_active == true",
                    [
                        {"remove_condition": {"target": "target_state", "condition": "frightened"}},
                        {"remove_condition": {"target": "target_state", "condition": "poisoned"}},
                    ],
                ),
            ],
        )
    )
    changed["resolve_srd_timed_actor_condition_suppression"] = suppression
    resistance = deepcopy(tools["resolve_srd_concentration_damage_resistance"])
    resistance["effects"] = [
        e for e in resistance["effects"] if "resolve_srd_feast_immunity" not in str(e)
    ]
    resistance["effects"].append(
        _when(
            "target_state.heroes_feast_until != null and damage_type == 'poison'",
            [
                _call(
                    "resolve_srd_feast_immunity",
                    {"target_state": "{_target_state_id}"},
                    ["_feast_active"],
                ),
                _when("_feast_active == true", [_set("_lasting_damage_resistance", True)]),
            ],
        )
    )
    changed["resolve_srd_concentration_damage_resistance"] = resistance
    _compose_feast_consumers(tools, changed)
    _compose_recovery_boundaries(tools, changed)
    return _replace_tools(text, changed)


def _compose_feast_consumers(tools, changed):
    "Keep feast immunity on the final projection and the existing damage resistance gate."
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        tool = deepcopy(changed.get(name, tools[name]))
        for node in _walk(tool["effects"]):
            condition = node.get("condition", "")
            if "target.concentration_resistance_source_ids != null" in condition:
                node["condition"] = (
                    condition.replace(
                        "target.concentration_resistance_source_ids != null",
                        "(target.concentration_resistance_source_ids != null or "
                        "target.heroes_feast_until != null)",
                    )
                    if "target.heroes_feast_until" not in condition
                    else condition
                )
        changed[name] = tool
    for name, original in tools.items():
        if name in changed:
            original = changed[name]
        tool = deepcopy(original)
        altered = False
        for node in _walk(tool.get("effects", [])):
            condition = node.get("condition", "")
            if (
                "check_srd_target_condition_immunity" in str(node.get("on_true", []))
                and "has_timed_actor_defenses" in condition
                and "heroes_feast_until" not in condition
            ):
                match = re.search(r"([a-z_]+)\.has_timed_actor_defenses", condition)
                if match:
                    node["condition"] = (
                        "(" + condition + ") or " + match[1] + ".heroes_feast_until != null"
                    )
                    altered = True
        if altered:
            changed[name] = tool

    # Common suppression owns Frightened. Poisoned is restored independently, then
    # the final projection masks it so affliction consumers cannot reapply it.
    suppression = changed["resolve_srd_timed_actor_condition_suppression"]
    for node in _walk(suppression["effects"]):
        setting = node.get("set", {})
        if setting.get("field") == "_suppress_actor_condition" and "'frightened'" in str(
            setting.get("value")
        ):
            value = setting["value"]
            if "heroes_feast_until" not in value:
                setting["value"] = (
                    "("
                    + value
                    + ") or (target_state.heroes_feast_until != null and "
                    + "lasting_state.lasting_tick < target_state.heroes_feast_until)"
                )
        if setting.get("field", "").endswith("}.suppressed"):
            value = setting["value"]
            if "heroes_feast_until" not in str(value):
                setting["value"] = (
                    "("
                    + value
                    + ") or (_actor_suppression_source.condition == 'poisoned' and "
                    + (
                        "target_state.heroes_feast_until != null and lasting_state.lasting_tick <"
                        " target_state.heroes_feast_until)"
                    )
                )
    projection = deepcopy(
        changed.get(
            "refresh_srd_timed_condition_target", tools["refresh_srd_timed_condition_target"]
        )
    )
    projection["effects"] = [
        e for e in projection["effects"] if "_recovery_feast_projection" not in str(e)
    ]
    projection["effects"].append(
        _when(
            "target_state.heroes_feast_until != null",
            [
                _set(
                    "_recovery_feast_projection",
                    "lasting_state.lasting_tick < target_state.heroes_feast_until",
                ),
                _when(
                    "_recovery_feast_projection == true",
                    [
                        {"remove_condition": {"target": "target_state", "condition": "poisoned"}},
                        {"remove_condition": {"target": "target_state", "condition": "frightened"}},
                    ],
                ),
            ],
        )
    )
    changed["refresh_srd_timed_condition_target"] = projection
    for name, actor in (
        ("check_srd_active_control_action", "actor_state"),
        ("cast_spell", "caster_state"),
    ):
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        # Using an action or casting a spell interrupts an hour of eating. This
        # hook is inert unless this exact actor began a saved feast consumption.
        tool["effects"] = [
            e for e in tool["effects"] if "feast_consumption_interrupted" not in str(e)
        ]
        tool["effects"].append(
            _when(
                actor + ".feast_consuming != null",
                [_set(actor + ".feast_consumption_interrupted", True)],
            )
        )
        changed[name] = tool


def _compose_recovery_boundaries(tools, changed):
    "Keep actual source lifetimes across target death and consume effective casting abilities."
    refresh = deepcopy(
        changed.get(
            "refresh_srd_timed_condition_target", tools["refresh_srd_timed_condition_target"]
        )
    )
    for node in _walk(refresh["effects"]):
        condition = node.get("condition", "")
        if "_timed_condition_source.target_state_id" in condition:
            node["condition"] = condition.replace(" and target_state.is_alive == true", "")
    changed["refresh_srd_timed_condition_target"] = refresh
    hp = deepcopy(
        changed.get("install_srd_timed_hit_point_bonus", tools["install_srd_timed_hit_point_bonus"])
    )
    hp["parameters"] = [p for p in hp["parameters"] if p["name"] != "expires_tick"]
    hp["parameters"].append(
        _param("expires_tick", "number", required=False, default=None, minimum=0, model_hidden=True)
    )
    hp["effects"] = [
        e
        for e in hp["effects"]
        if e.get("set", {}).get("field") != "_recovery_hp_expiry"
        and e.get("condition") != "expires_tick != null"
    ]
    hp["effects"][1:1] = [
        _set("_recovery_hp_expiry", "lasting_state.lasting_tick + duration_rounds * 2"),
        _when("expires_tick != null", [_set("_recovery_hp_expiry", "expires_tick")]),
    ]
    for node in _walk(hp["effects"]):
        record = node.get("list_push", {}).get("value")
        if isinstance(record, dict) and record.get("kind") == "'hit_point_bonus'":
            record["expires_tick"] = "_recovery_hp_expiry"
    changed["install_srd_timed_hit_point_bonus"] = hp
    attack_conditions = deepcopy(
        changed.get("resolve_srd_attack_conditions", tools["resolve_srd_attack_conditions"])
    )
    for node in _walk(attack_conditions["effects"]):
        setting = node.get("set", {})
        if (
            setting.get("field") == "_condition_disadvantage"
            and isinstance(setting.get("value"), str)
            and "resurrection_tax" not in setting["value"]
        ):
            setting["value"] = "(" + setting["value"] + ") or actor_state.resurrection_tax == true"
    changed["resolve_srd_attack_conditions"] = attack_conditions
    for name, original in tools.items():
        tool = deepcopy(changed.get(name, original))
        altered = False

        def hook(value):
            nonlocal altered
            if isinstance(value, list):
                value[:] = [
                    n
                    for n in value
                    if not (
                        isinstance(n, dict)
                        and n.get("condition")
                        == "caster_state.has_recovery_ability_reductions == true"
                    )
                ]
                for index, node in reversed(list(enumerate(value))):
                    if not isinstance(node, dict):
                        continue
                    setting = node.get("set", {})
                    field, expression = setting.get("field"), setting.get("value")
                    if (
                        field in ("_casting_modifier", "_concentration_modifier")
                        and isinstance(expression, str)
                        and "caster.data.abilities" in expression
                    ):
                        ability = (
                            "_casting_ability" if field == "_casting_modifier" else "'constitution'"
                        )
                        value.insert(
                            index + 1,
                            _when(
                                "caster_state.has_recovery_ability_reductions == true",
                                [
                                    _set(
                                        field,
                                        "(caster_state.ability_scores[" + ability + "] - 10) // 2",
                                    )
                                ],
                            ),
                        )
                        altered = True
                    hook(node)
            elif isinstance(value, dict):
                for node in value.values():
                    hook(node)

        hook(tool.get("effects", []))
        if "attack" in name and not any(
            n.get("call") == "d20_test" for n in _walk(tool.get("effects", []))
        ):
            actor_name = "actor_state"
            if any(
                p["name"] == "actor" and p.get("resolve") == "entity"
                for p in tool.get("parameters", [])
            ):
                actor_name = "actor"

            def attack_penalty(value, actor=actor_name):
                nonlocal altered
                if isinstance(value, list):
                    value[:] = [
                        n
                        for n in value
                        if not (
                            isinstance(n, dict)
                            and n.get("condition") == actor + (".revival_penalty != null")
                        )
                    ]
                    for index, node in reversed(list(enumerate(value))):
                        if not isinstance(node, dict):
                            continue
                        setting = node.get("set", {})
                        if setting.get("field") == "_attack_adjusted_total":
                            value.insert(
                                index + 1,
                                _when(
                                    actor + ".revival_penalty != null",
                                    [
                                        _set(
                                            "_attack_adjusted_total",
                                            "_attack_adjusted_total + "
                                            + actor
                                            + ".revival_penalty",
                                        )
                                    ],
                                ),
                            )
                            altered = True
                        else:
                            attack_penalty(node)
                elif isinstance(value, dict):
                    for node in value.values():
                        attack_penalty(node)

            attack_penalty(tool.get("effects", []))
        if name == "death_saving_throw":
            for node in _walk(tool["effects"]):
                roll = node.get("dice", {})
                if roll.get("expression") == "d20":
                    roll["disadvantage"] = "target.resurrection_tax == true"
            altered = True
        if name == "cast_spell_single_target_save_damage":

            def max_hp_hook(value):
                if isinstance(value, list):
                    value[:] = [
                        n
                        for n in value
                        if not (
                            isinstance(n, dict)
                            and (
                                n.get("set", {}).get("field")
                                in ("_recovery_max_hp_before", "target.recovery_max_hp_reduction")
                                or n.get("condition") == "target.recovery_max_hp_reduction == null"
                            )
                        )
                    ]
                    for index, node in reversed(list(enumerate(value))):
                        if (
                            isinstance(node, dict)
                            and node.get("set", {}).get("field") == "target.max_hp"
                        ):
                            value[index : index + 1] = [
                                _set("_recovery_max_hp_before", "target.max_hp"),
                                node,
                                _when(
                                    "target.recovery_max_hp_reduction == null",
                                    [_set("target.recovery_max_hp_reduction", 0)],
                                ),
                                _set(
                                    "target.recovery_max_hp_reduction",
                                    (
                                        "target.recovery_max_hp_reduction + _recovery_max_hp_befo"
                                        "re - target.max_hp"
                                    ),
                                ),
                            ]
                        elif isinstance(node, dict):
                            max_hp_hook(node)
                elif isinstance(value, dict):
                    for node in value.values():
                        max_hp_hook(node)

            max_hp_hook(tool["effects"])
            altered = True
        if altered:
            changed[name] = tool
