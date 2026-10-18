"""Retained spell attacks reuse the actual attack, damage, action and source owners."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _replace_tools
else:
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _replace_tools


PUBLIC_TOOLS = {"use_srd_vampiric_touch"}
PROFILE = {
    "family": "retained_spell_attack",
    "casting_time": "action",
    "base_level": 3,
    "range_kind": "self",
    "range_feet": 0,
    "concentration": True,
    "duration_rounds": 10,
    "base_dice": 3,
    "damage_die": "d6",
    "damage_type": "necrotic",
}
FLAME_BLADE_PROFILE = PROFILE | {
    "casting_time": "bonus_action",
    "base_level": 2,
    "duration_rounds": 100,
    "damage_type": "fire",
}
PROFILES = {"vampiric_touch": PROFILE, "flame_blade": FLAME_BLADE_PROFILE}
SOURCE_EXPORTS = (
    "_retained_source",
    "_retained_source_id",
    "_retained_casting_ability",
    "_retained_attack_bonus",
    "_retained_casting_modifier",
    "_retained_dice_count",
    "_retained_max_range",
)
ATTACK_EXPORTS = (
    "_retained_attack",
    "_retained_attack_kind",
    "_retained_attack_total",
    "_retained_armor_class",
    "_retained_damage",
    "_retained_damage_rolled",
    "_retained_damage_applied",
    "_retained_necrotic_damage",
    "_retained_hp_before",
    "_retained_hp_after",
    "_retained_temp_before",
    "_retained_temp_after",
    "_retained_healing_due",
    "_retained_healing_applied",
    "_retained_caster_hp_before",
    "_retained_caster_hp_after",
    "_retained_attack_dice",
    "_retained_damage_dice",
    "_retained_target",
    "_retained_source_id",
    "_mirror_redirected",
    "_mirror_dice",
    "_mirror_source_id",
    "_mirror_remaining",
)


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def schema():
    """Admit the exact selected retained attack profiles; each row is validated below."""
    properties = {}
    for name, value in PROFILE.items():
        if name == "family":
            continue
        kind = (
            "boolean"
            if isinstance(value, bool)
            else "integer"
            if isinstance(value, int)
            else "string"
        )
        properties[name] = {
            "type": kind,
            "enum": list(dict.fromkeys(profile[name] for profile in PROFILES.values())),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "retained_spell_attack" and (
            definition != PROFILES.get(spell)
        ):
            raise ValueError("Retained attack source profile differs: " + spell)


def _record_tool():
    """A record is live only for its exact concentration instance and actual clock."""
    return _tool(
        "Qualify one saved retained spell attack source without refreshing a sight verdict.",
        [_entity("caster_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _set(
                "_retained_live",
                "record.kind == 'retained_spell_attack' and record.active == true and "
                "record.caster == _caster_state_id and "
                "state.lasting_tick != null and state.lasting_tick < record.expires_tick and "
                "caster_state.concentration_spell_id == record.spell and "
                "caster_state.concentration_effect_family == 'retained_spell_attack' and "
                "caster_state.concentration_started_tick == record.created_tick",
            ),
            _set(
                "_retained_live",
                "_retained_live == true and "
                "caster_state.retained_attack_source_id == record.source_id and "
                "caster_state.is_alive == true and caster_state.hp > 0 and not "
                "('incapacitated' in caster_state.conditions or 'paralyzed' in "
                "caster_state.conditions or 'petrified' in caster_state.conditions or "
                "'stunned' in caster_state.conditions or 'unconscious' in caster_state.conditions)",
            ),
            _set("_retained_active", "_retained_live == true and record.suppressed != true"),
        ],
    )


def _install_tool():
    """The successful paid cast installs one source; it never pays or casts again."""
    return _tool(
        "Install Vampiric Touch from the actual successful paid cast's source facts.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=3, maximum=9),
            _parameter(
                "casting_ability", "enum", enum_values=["intelligence", "wisdom", "charisma"]
            ),
            _parameter("concentration_save_modifier", "integer"),
            _optional("spell_cast_id", default="", model_hidden=True),
        ],
        [
            _require("caster_state.is_alive == true", "The spell needs its living caster."),
            _require(
                "spell_cast_id != '' and caster_state.last_spell_source_cast_id == spell_cast_id "
                "and caster_state.last_spell_source_spell == 'vampiric_touch'",
                "Only the actual committed cast may stamp this retained source.",
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
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
            _set("_retained_source_id", "spell_cast_id"),
            _set("caster_state.retained_attack_source_id", "_retained_source_id"),
            _set("caster_state.concentration_spell_id", "'vampiric_touch'"),
            _set("caster_state.concentration_cast_id", "spell_cast_id"),
            _set("caster_state.concentration_effect_family", "'retained_spell_attack'"),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_target_state_ids", []),
            {
                "list_push": {
                    "list": "caster_state.concentration_target_state_ids",
                    "value": "_caster_state_id",
                }
            },
            _set("caster_state.concentration_started_tick", "state.lasting_tick"),
            _set("caster_state.concentration_expires_tick", "state.lasting_tick + 20"),
            _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
            _set("_retained_records", []),
            _when("state.turn_effects != null", [_set("_retained_records", "state.turn_effects")]),
            _require("len(_retained_records) < 128", "Resolve existing source records first."),
            {
                "list_push": {
                    "list": "_retained_records",
                    "value": {
                        "kind": "retained_spell_attack",
                        "spell": "vampiric_touch",
                        "source_id": "_retained_source_id",
                        "caster": "_caster_state_id",
                        "target": "_caster_state_id",
                        "active": True,
                        "suppressed": False,
                        "created_tick": "state.lasting_tick",
                        "expires_tick": "state.lasting_tick + 20",
                        "slot_level": "slot_level",
                        "casting_ability": "casting_ability",
                        "cast_id": "spell_cast_id",
                        "source_caster_id": "_caster_state_id",
                        "spell_level": "slot_level",
                        "ongoing_duration": True,
                    },
                }
            },
            _set("state.turn_effects", "_retained_records"),
            _set("state.has_retained_spell_attacks", True),
        ],
    )


def _prepare_tool():
    """Use original ability/slot choices and the actual caster's current statistics."""
    return _tool(
        "Prepare the exact living Vampiric Touch source's current melee attack statistics.",
        [_entity("caster_state"), _optional("caster", resolve="participant")],
        [
            _set("_retained_source", None),
            _set("_retained_records", []),
            _when(
                "state.has_retained_spell_attacks == true and state.turn_effects != null",
                [
                    _set("_retained_records", "state.turn_effects"),
                ],
            ),
            _loop(
                "_retained_records",
                "_retained_candidate",
                [
                    _when(
                        "_retained_candidate.kind == 'retained_spell_attack' and "
                        "_retained_candidate.spell == 'vampiric_touch' and "
                        "_retained_candidate.caster == _caster_state_id and "
                        "_retained_candidate.source_id == caster_state.retained_attack_source_id",
                        [
                            _call(
                                "check_srd_retained_spell_attack_record",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_retained_candidate}",
                                },
                                ["_retained_active"],
                            ),
                            _when(
                                "_retained_active == true",
                                [_set("_retained_source", "_retained_candidate")],
                            ),
                        ],
                    ),
                ],
            ),
            _require("_retained_source != null", "No active saved Vampiric Touch source remains."),
            _set("_retained_source_id", "_retained_source.source_id"),
            _set("_retained_casting_ability", "_retained_source.casting_ability"),
            {
                "condition": "caster != null",
                "on_true": [
                    _require(
                        "caster.validation_status == 'validated' and "
                        "caster.package_id == 'sylvia.srd-lite.bg3' and "
                        "caster.participant_id == _caster_state_id",
                        "Use the caster's own validated Character File.",
                    ),
                    _require(
                        "caster.data.level >= 1 and caster.data.level <= 20 and "
                        "caster.data.abilities[_retained_casting_ability] >= 3 and "
                        "caster.data.abilities[_retained_casting_ability] <= 20",
                        "The caster needs actual level and casting ability statistics.",
                    ),
                    _set(
                        "_retained_casting_modifier",
                        "(caster.data.abilities[_retained_casting_ability] - 10) // 2",
                    ),
                    _set(
                        "_retained_attack_bonus",
                        "_retained_casting_modifier + 2 + ((caster.data.level - 1) // 4)",
                    ),
                ],
                "on_false": [
                    _require(
                        "caster_state.creature_kind == 'monster' and "
                        "caster_state.spellcasting_profile.attack_bonus != null and "
                        "caster_state.spellcasting_profile.modifier != null",
                        "A creature caster needs its prepared spellcasting profile.",
                    ),
                    _set(
                        "_retained_casting_modifier", "caster_state.spellcasting_profile.modifier"
                    ),
                    _set(
                        "_retained_attack_bonus", "caster_state.spellcasting_profile.attack_bonus"
                    ),
                ],
            },
            _set("_retained_dice_count", "_retained_source.slot_level"),
            _set("_retained_max_range", 5),
            _when(
                "caster_state.melee_reach != null",
                [_set("_retained_max_range", "caster_state.melee_reach")],
            ),
            _require(
                "_retained_max_range > 0", "The actual caster must have positive melee reach."
            ),
        ],
    )


def _refresh_tool():
    """Refresh only this source family at actual lifecycle boundaries, preserving other rows."""
    return _tool(
        "Retire expired or concentration-ended retained attacks and derive the owning index.",
        [],
        [
            _set("_retained_records", []),
            _set("_retained_kept", []),
            _set("_retained_any", False),
            _when("state.turn_effects != null", [_set("_retained_records", "state.turn_effects")]),
            _loop(
                "_retained_records",
                "_retained_record",
                [
                    {
                        "condition": "_retained_record.kind == 'retained_spell_attack'",
                        "on_true": [
                            _call(
                                "check_srd_retained_spell_attack_record",
                                {
                                    "caster_state": "{_retained_record.caster}",
                                    "record": "{_retained_record}",
                                },
                                ["_retained_live"],
                            ),
                            _when(
                                "_retained_live == true",
                                [
                                    {
                                        "list_push": {
                                            "list": "_retained_kept",
                                            "value": "_retained_record",
                                        }
                                    },
                                    _set("_retained_any", True),
                                ],
                            ),
                        ],
                        "on_false": [
                            {"list_push": {"list": "_retained_kept", "value": "_retained_record"}},
                        ],
                    },
                ],
            ),
            _set("state.turn_effects", "_retained_kept"),
            _set("state.has_retained_spell_attacks", "_retained_any == true"),
        ],
    )


def _retire_tool():
    """Exact removal deletes the owning row; the common concentration owner keeps its boundary."""
    return _tool(
        "Retire one exact retained attack source without retiring unrelated source rows.",
        [_parameter("source_id")],
        [
            _set("_retained_retired", False),
            _set("_retained_records", []),
            _set("_retained_kept", []),
            _when("state.turn_effects != null", [_set("_retained_records", "state.turn_effects")]),
            _loop(
                "_retained_records",
                "_retained_record",
                [
                    {
                        "condition": "_retained_record.kind == 'retained_spell_attack' and "
                        "_retained_record.source_id == source_id",
                        "on_true": [
                            _set("_retained_retired", True),
                        ],
                        "on_false": [
                            {"list_push": {"list": "_retained_kept", "value": "_retained_record"}},
                        ],
                    },
                ],
            ),
            _set("state.turn_effects", "_retained_kept"),
            _call("refresh_srd_retained_spell_attacks", {}),
        ],
    )


def _finish_removal_tool():
    """End concentration only for this removed source, retaining an independent newer cast."""
    return _tool(
        "Finish one actual removed retained source through its existing concentration owner.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _when(
                "caster_state.concentration_effect_family == 'retained_spell_attack' and "
                "caster_state.concentration_cast_id == source_id and "
                "caster_state.retained_attack_source_id == source_id",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                        },
                    ),
                ],
            ),
            _call("retire_srd_retained_spell_attack_source", {"source_id": "{source_id}"}),
        ],
    )


def _attack_tool(tools):
    """Clone the settled creature attack branch, preserving its real dice and cover owners."""
    parent = tools["hurl_srd_produce_flame_node"]
    if "_produce_defer_hit" in str(parent["effects"]):
        raise ValueError(
            "Emit retained attacks before Produce Flame's source-specific pending binder."
        )
    branches = [e["on_true"] for e in parent["effects"] if e.get("condition") == "target != null"]
    if len(branches) != 1:
        raise ValueError("Retained melee attacks need the actual settled creature attack branch.")

    def rename(value):
        if isinstance(value, str):
            return (
                value.replace("_produce_flame_", "_retained_")
                .replace("_produce_", "_retained_")
                .replace("Produce Flame", "Vampiric Touch")
            )
        if isinstance(value, list):
            return [rename(child) for child in value]
        if isinstance(value, dict):
            return {key: rename(child) for key, child in value.items()}
        return value

    effects = rename(deepcopy(branches[0]))
    damage_calls = []
    for sequence in _lists(effects):
        for effect in sequence:
            if effect.get("call") == "resolve_srd_attack_conditions":
                effect["params"]["attack_kind"] = "melee"
            if effect.get("set", {}).get("field") == "_retained_disadvantage" and (
                "_retained_nearby_disadvantage" in str(effect)
            ):
                effect["set"]["value"] = "_condition_disadvantage == true"
            if effect.get("store_as") == "_retained_damage":
                effect["dice"]["expression"] = "{_retained_dice_count}d6"
            if effect.get("call") in {"apply_srd_damage", "apply_srd_qualifying_damage"}:
                effect["call"] = "apply_srd_qualifying_damage"
                effect["params"].update(
                    damage_type="necrotic",
                    game_damage_roll=True,
                    actual_attack_hit=True,
                    observe_damage_type="necrotic",
                )
                effect.setdefault("export_context", {})["_source_observed_type_damage"] = (
                    "_source_observed_type_damage"
                )
                damage_calls.append(effect)
            if "require" in effect and "60 feet" in effect["require"]["error"]:
                effect["require"]["error"] = (
                    "The creature must be within the caster's actual reach on the same plane."
                )
    if len(damage_calls) != 1:
        raise ValueError("Retained melee attacks need one settled scalar damage packet.")
    for sequence in _lists(effects):
        for index, effect in enumerate(sequence):
            if effect is damage_calls[0]:
                sequence.insert(
                    index + 1, _set("_retained_necrotic_damage", "_source_observed_type_damage")
                )
                break
    # A later final graph may already own Mirror. The primitive native branch predates that join.
    if "resolve_srd_mirror_image_hit" not in str(effects):
        hits = [
            e
            for sequence in _lists(effects)
            for e in sequence
            if e.get("condition") == "_retained_attack_kind != 'miss'"
        ]
        if len(hits) != 1:
            raise ValueError("Retained melee attacks need one real hit branch before damage dice.")
        hits[0]["on_true"] = [
            _call(
                "resolve_srd_mirror_image_hit",
                {"target_state": "{_target_id}", "attacker_state": "{_caster_state_id}"},
                ["_mirror_redirected", "_mirror_dice", "_mirror_source_id", "_mirror_remaining"],
            ),
            _when("_mirror_redirected != true", hits[0]["on_true"]),
        ]
    prefix = [
        _set("_retained_source_id", "source_id"),
        _set("_mirror_redirected", False),
        _set("_mirror_dice", []),
        _set("_mirror_source_id", "''"),
        _set("_mirror_remaining", 0),
        _set("_retained_necrotic_damage", 0),
        _set("_retained_fear_visible", False),
        _require(
            "target.creature_kind != null", "Vampiric Touch targets a creature, never an object."
        ),
        _when(
            "'frightened' in caster_state.conditions",
            [
                _call("resolve_srd_control_fear_visibility", {"actor_state": "{_caster_state_id}"}),
                _require(
                    "caster_state.control_fear_facts_known == true",
                    "Resolve the caster's actual Fear source first.",
                ),
                _set("_retained_fear_visible", "caster_state.control_fear_visible == true"),
            ],
        ),
    ]
    suffix = [
        _set("_retained_caster_hp_before", "caster_state.hp"),
        _set("_retained_healing_due", "_retained_necrotic_damage // 2"),
        _when(
            "_retained_healing_due > 0",
            [
                _call(
                    "restore_hit_points",
                    {"target": "{_caster_state_id}", "amount": "{_retained_healing_due}"},
                ),
            ],
        ),
        _set("_retained_caster_hp_after", "caster_state.hp"),
        _set(
            "_retained_healing_applied",
            "max(0, _retained_caster_hp_after - _retained_caster_hp_before)",
        ),
        _when(
            "_retained_attack_kind != 'miss' and _mirror_redirected != true and "
            "(target.spell_fire_shield == 'warm' or target.spell_fire_shield == 'chill')",
            [
                _call(
                    "resolve_srd_fire_shield_hit",
                    {
                        "defender_state": "{_target_id}",
                        "attacker_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "shield_mode": "{target.spell_fire_shield}",
                        "attack_kind": "melee",
                        "attack_distance": "{_retained_attack_distance}",
                        "attack_outcome": "{_retained_attack_kind}",
                    },
                ),
            ],
        ),
        _set("_retained_caster_hp_after", "caster_state.hp"),
        _set("_retained_attack_dice", "_retained_attack.results"),
        _set("_retained_damage_dice", []),
        _when(
            "_retained_damage != null", [_set("_retained_damage_dice", "_retained_damage.results")]
        ),
    ]
    return _tool_scene(
        "Resolve one source-owned Vampiric Touch attack and actual Necrotic-base healing.",
        [
            _entity("caster_state"),
            _entity("target"),
            *[
                _parameter(name, "integer")
                for name in (
                    "_retained_attack_bonus",
                    "_retained_dice_count",
                    "_retained_max_range",
                )
            ],
            _parameter("_retained_casting_ability"),
            _parameter("source_id"),
        ],
        prefix + effects + suffix,
    )


def _use_tool(*, initial=False):
    effects = [
        _when("caster == null", [_set("_caster_id", None)]),
        _call(
            "prepare_srd_retained_spell_attack_source",
            {"caster_state": "{_caster_state_id}", "caster": "{_caster_id}"},
            SOURCE_EXPORTS,
        ),
    ]
    if not initial:
        effects += [
            {
                "condition": "combat.encounter_active == true",
                "on_true": [
                    _require(
                        "combat.turn_active == true and combat.active_actor == _caster_state_id "
                        "and caster_state.turns_started != null and "
                        "caster_state.retained_attack_used_turn != caster_state.turns_started",
                        "Use Vampiric Touch's later Magic action once on each caster turn.",
                    ),
                ],
                "on_false": [
                    _require(
                        "caster_state.retained_attack_used_tick == null or "
                        "state.lasting_tick >= caster_state.retained_attack_used_tick + 2",
                        "Advance the real exploration clock before using Vampiric Touch again.",
                    ),
                ],
            },
            _call(
                "spend_spell_casting_budget",
                {
                    "caster_state": "{_caster_state_id}",
                    "casting_time": "action",
                    "slot_resource": "no_slot",
                },
            ),
        ]
    effects += [
        _call(
            "resolve_srd_vampiric_touch_attack",
            {
                "caster_state": "{_caster_state_id}",
                "target": "{_target_id}",
                **{
                    ("source_id" if name == "_retained_source_id" else name): "{" + name + "}"
                    for name in SOURCE_EXPORTS
                    if name not in {"_retained_source", "_retained_casting_modifier"}
                },
            },
            ATTACK_EXPORTS,
        ),
        _set("caster_state.retained_attack_used_tick", "state.lasting_tick"),
        _set("caster_state.retained_attack_used_turn", None),
        _when(
            "caster_state.turns_started != null",
            [_set("caster_state.retained_attack_used_turn", "caster_state.turns_started")],
        ),
    ]
    tool = _tool(
        "Take the living source's Vampiric Touch melee spell attack.",
        [_entity("caster_state"), _optional("caster", resolve="participant"), _entity("target")],
        effects,
    )
    if initial:
        return tool
    return _public(
        tool,
        "combat",
        "Vampiric Touch",
        [
            ("Target", "_retained_target"),
            ("Attack", "_retained_attack_kind"),
            ("Attack dice", "_retained_attack_dice"),
            ("Attack total", "_retained_attack_total"),
            ("Target AC", "_retained_armor_class"),
            ("Damage dice", "_retained_damage_dice"),
            ("Necrotic damage", "_retained_necrotic_damage"),
            ("All damage", "_retained_damage_applied"),
            ("Target HP after", "_retained_hp_after"),
            ("Target Temp HP after", "_retained_temp_after"),
            ("HP restored", "_retained_healing_applied"),
            ("Caster HP after", "_retained_caster_hp_after"),
            ("Mirror Image diverted", "_mirror_redirected"),
            ("Source", "_retained_source_id"),
        ],
    )


def _cast_tool():
    """Dispatch only after the ordinary owner has validated and paid the actual cast."""
    return _tool(
        "Install the committed source and resolve Vampiric Touch's initial melee attack.",
        [
            _entity("caster_state"),
            _optional("caster", resolve="participant"),
            _entity("target"),
            _parameter("slot_level", "integer", minimum=3, maximum=9),
            _parameter(
                "casting_ability", "enum", enum_values=["intelligence", "wisdom", "charisma"]
            ),
            _parameter("concentration_save_modifier", "integer"),
            _optional("spell_cast_id", default="", model_hidden=True),
        ],
        [
            _when("caster == null", [_set("_caster_id", None)]),
            _call(
                "install_srd_vampiric_touch",
                {
                    "caster_state": "{_caster_state_id}",
                    "slot_level": "{slot_level}",
                    "casting_ability": "{casting_ability}",
                    "concentration_save_modifier": "{concentration_save_modifier}",
                    "spell_cast_id": "{spell_cast_id}",
                },
            ),
            _call(
                "cast_srd_vampiric_touch_attack",
                {
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "target": "{_target_id}",
                },
                ATTACK_EXPORTS,
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_target_id}",
                    "brief": "{_retained_attack_kind}: Necrotic {_retained_necrotic_damage}; "
                    "caster HP restored {_retained_healing_applied}",
                    "detail": "Attack {_retained_attack_total} vs AC {_retained_armor_class}; "
                    "{_retained_attack_kind}. Necrotic damage {_retained_necrotic_damage}; "
                    "all damage {_retained_damage_applied}. Target HP {_retained_hp_after}, "
                    "Temp HP {_retained_temp_after}. "
                    "Caster HP restored {_retained_healing_applied}.",
                },
                ["_spell_target_summary", "_spell_target_detail"],
            ),
            {"list_push": {"list": "_spell_summary_rows", "value": "_spell_target_summary"}},
            {"list_push": {"list": "_spell_detail_rows", "value": "_spell_target_detail"}},
        ],
    )


def extend_runtime(text):
    """Add the bounded source and retained action after actual attack consumers are settled."""
    tools = _load_runtime(text)["tools"]
    required = {
        "hurl_srd_produce_flame_node",
        "apply_srd_qualifying_damage",
        "resolve_srd_mirror_image_hit",
    }
    if missing := required - tools.keys():
        raise ValueError("Retained spell attacks need owning tools: " + ", ".join(sorted(missing)))
    changed = {
        "check_srd_retained_spell_attack_record": _record_tool(),
        "refresh_srd_retained_spell_attacks": _refresh_tool(),
        "retire_srd_retained_spell_attack_source": _retire_tool(),
        "finish_srd_retained_spell_attack_removal": _finish_removal_tool(),
        "install_srd_vampiric_touch": _install_tool(),
        "prepare_srd_retained_spell_attack_source": _prepare_tool(),
        "resolve_srd_vampiric_touch_attack": _attack_tool(tools),
        "cast_srd_vampiric_touch_attack": _use_tool(initial=True),
        "cast_srd_vampiric_touch": _cast_tool(),
        "use_srd_vampiric_touch": _use_tool(),
    }
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def bind_runtime_consumers(text):
    """Join the paid cast and actual clock/concentration/removal boundaries without reallocating."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        calls = [e.get("call") for sequence in _lists(tool["effects"]) for e in sequence]
        if "cast_srd_vampiric_touch" in calls or "cast_spell" in calls:
            continue
        anchors = [
            i
            for i, e in enumerate(tool["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        ]
        if len(anchors) != 1:
            raise ValueError("Retained attacks need the final successful paid cast-card boundary.")
        tool["effects"].insert(
            anchors[0],
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'retained_spell_attack'",
                [
                    _call(
                        "cast_srd_vampiric_touch",
                        {
                            "caster_state": "{_caster_state_id}",
                            "caster": "{_caster_id}",
                            "target": "{_target_id}",
                            "slot_level": "{_slot_level}",
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "casting_ability": "{_casting_profile.ability}",
                            "concentration_save_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                        },
                        ATTACK_EXPORTS + ("_spell_summary_rows", "_spell_detail_rows"),
                    )
                ],
            ),
        )
        changed[name] = tool
    name = "advance_srd_lasting_clock"
    clock = deepcopy(tools[name])
    if "refresh_srd_retained_spell_attacks" not in str(clock["effects"]):
        anchors = [
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        ]
        if len(anchors) != 1:
            raise ValueError("Retained source refresh needs the actual clock assignment.")
        clock["effects"].insert(
            anchors[0] + 1,
            _when(
                "lasting_state.has_retained_spell_attacks == true",
                [
                    _call(
                        "refresh_srd_retained_spell_attacks",
                        {"lasting_state": "{_lasting_state_id}"},
                    )
                ],
            ),
        )
        changed[name] = clock
    name = "end_srd_concentration_node"
    end = deepcopy(tools[name])
    if "retire_srd_retained_spell_attack_source" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'retained_spell_attack' and "
                "source_state.retained_attack_source_id != null",
                [
                    _call(
                        "retire_srd_retained_spell_attack_source",
                        {
                            "source_id": "{source_state.retained_attack_source_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            ),
        )
        changed[name] = end
    for name in ("process_srd_turn_effects", "refresh_srd_removed_spell_target"):
        tool = deepcopy(tools[name])
        if "refresh_srd_retained_spell_attacks" in str(tool["effects"]):
            continue
        effects = []
        if name == "refresh_srd_removed_spell_target":
            effects.append(
                _loop(
                    "removed_effects",
                    "_retained_removed",
                    [
                        _when(
                            "_retained_removed.kind == 'retained_spell_attack' and "
                            "_retained_removed.target == _target_state_id",
                            [
                                _call(
                                    "finish_srd_retained_spell_attack_removal",
                                    {
                                        "caster_state": "{_retained_removed.caster}",
                                        "source_id": "{_retained_removed.source_id}",
                                        "lasting_state": "{_lasting_state_id}",
                                    },
                                )
                            ],
                        )
                    ],
                )
            )
        effects.append(
            _call("refresh_srd_retained_spell_attacks", {"lasting_state": "{_lasting_state_id}"})
        )
        tool["effects"].append(_when("lasting_state.has_retained_spell_attacks == true", effects))
        changed[name] = tool
    name = "dispel_srd_target_sources"
    dispel = deepcopy(tools[name])
    if "finish_srd_retained_spell_attack_removal" not in str(dispel["effects"]):
        matches = [
            (sequence, i)
            for sequence in _lists(dispel["effects"])
            for i, e in enumerate(sequence)
            if e.get("set", {}).get("field") == "_dispel_remove"
            and e["set"].get("value") == "_dispel_succeeded"
        ]
        if len(matches) != 1:
            raise ValueError(
                "Retained Dispel needs the actual successful turn-effect check boundary."
            )
        sequence, index = matches[0]
        sequence.insert(
            index + 1,
            _when(
                "_dispel_succeeded == true and _dispel_effect.kind == 'retained_spell_attack'",
                [
                    _call(
                        "finish_srd_retained_spell_attack_removal",
                        {
                            "caster_state": "{_dispel_effect.caster}",
                            "source_id": "{_dispel_effect.source_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            ),
        )
        changed[name] = dispel
    return _replace_tools(text, changed) if changed else text
