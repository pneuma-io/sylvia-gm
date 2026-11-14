"""Sorcerous Burst reuses the admitted object attack and damage persistence owners."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _entity, _parameter, _set, _tool, _when
    from .srd_spell_damage_consumers import _effect_lists
    from .srd_spell_object_targets import ATTACK_EXPORTS
else:
    from srd_actor_spell_modifiers import _call, _entity, _parameter, _set, _tool, _when
    from srd_spell_damage_consumers import _effect_lists
    from srd_spell_object_targets import ATTACK_EXPORTS


DAMAGE_TYPES = ["acid", "cold", "fire", "lightning", "poison", "psychic", "thunder"]


def burst_extra_dice_limit_parameter():
    """A stopping choice caps optional extra dice; it never supplies rolled outcomes."""
    return _parameter(
        "sorcerous_burst_extra_dice_limit",
        "integer",
        required=False,
        default=-1,
        minimum=-1,
        maximum=100,
    )


def burst_extra_dice_limit_effects():
    """Default to the actual caster's limit, then honor an earlier requested stopping point."""
    return [
        _set("_burst_extra_dice_limit", "max(0, casting_modifier)"),
        _when(
            "sorcerous_burst_extra_dice_limit >= 0",
            [
                _set(
                    "_burst_extra_dice_limit",
                    "min(_burst_extra_dice_limit, sorcerous_burst_extra_dice_limit)",
                )
            ],
        ),
    ]


def _object_attack_tool(tools):
    """Extend only the actual raw damage producer, keeping one object D20 and HP mutation."""
    tool = deepcopy(tools["resolve_srd_spell_object_attack"])
    tool["parameters"] += [
        _parameter("casting_ability"),
        _parameter("casting_modifier", "integer"),
        _parameter("sorcerous_burst_extra_dice", "boolean", required=False, default=True),
        burst_extra_dice_limit_parameter(),
    ]
    for effects in _effect_lists(tool["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") == "d20_test":
                effect["params"]["ability"] = "{casting_ability}"
            if effect.get("store_as") == "_object_damage_roll" and "dice" in effect:
                effect["dice"].update(
                    explode="sorcerous_burst_extra_dice == true",
                    explode_on=8,
                    max_explosions="_burst_extra_dice_limit",
                )
                effects[index:index] = burst_extra_dice_limit_effects()
            if effect.get("set", {}).get("field") == "_object_damage_rolled":
                if effect["set"]["value"] == 0:
                    continue
                effects[index + 1 : index + 1] = [
                    _set("_object_damage_adjusted", "_object_damage_rolled"),
                    _when(
                        "caster_state.has_actor_spell_modifiers == true",
                        [
                            _call(
                                "modify_srd_actor_damage_roll",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "lasting_state": "state",
                                    "amount": "{_object_damage_rolled}",
                                },
                                {"_object_damage_adjusted": "_actor_modified_damage"},
                            ),
                        ],
                    ),
                ]
            if (
                effect.get("set", {}).get("field") == "_object_damage_applied"
                and effect["set"]["value"] != 0
            ):
                effect["set"]["value"] = "min(_object_hp_before, _object_damage_adjusted)"
    tool["description"] = (
        "Resolve Sorcerous Burst's one actual object attack, exploding raw dice and typed HP loss."
    )
    return tool


def _casting_tool():
    """Consume the paid source profile and retain the actual object's native result rows."""
    return _tool(
        "Resolve a paid Sorcerous Burst against an admitted scene object.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("casting_ability"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_attack_bonus", "integer"),
            _parameter("casting_scaling_level", "integer", minimum=1, maximum=20),
            _parameter("selected_damage_type", "enum", enum_values=DAMAGE_TYPES),
            _parameter("sorcerous_burst_extra_dice", "boolean", required=False, default=True),
            burst_extra_dice_limit_parameter(),
            _parameter("advantage", "boolean", required=False, default=False),
            _parameter("disadvantage", "boolean", required=False, default=False),
        ],
        [
            _set("_spell_hit_window_deferred", False),
            _set("_spell_casting_ability", "casting_ability"),
            _set("_spell_effective_damage_type", "selected_damage_type"),
            _set("_spell_damage_dice", 1),
            *[
                _when(
                    f"casting_scaling_level >= {level}",
                    [
                        _set("_spell_damage_dice", "_spell_damage_dice + 1"),
                    ],
                )
                for level in (5, 11, 17)
            ],
            _call(
                "resolve_srd_produce_flame_nearby_enemies",
                {
                    "caster_state": "{_caster_state_id}",
                },
                ("_produce_nearby_disadvantage",),
            ),
            _set(
                "_burst_object_disadvantage",
                ("disadvantage == true or _produce_nearby_disadvantage == true"),
            ),
            _call(
                "resolve_srd_sorcerous_burst_object_attack",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "attack_modifier": "{casting_attack_bonus}",
                    "damage_die": "d8",
                    "dice_count": "{_spell_damage_dice}",
                    "damage_type": "{selected_damage_type}",
                    "max_range_feet": 120,
                    "advantage": "{advantage}",
                    "disadvantage": "{_burst_object_disadvantage}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                    "sorcerous_burst_extra_dice": "{sorcerous_burst_extra_dice}",
                    "sorcerous_burst_extra_dice_limit": "{sorcerous_burst_extra_dice_limit}",
                },
                ATTACK_EXPORTS,
            ),
            _set("_spell_attack", "_object_attack_roll"),
            _set("_spell_attack_kind", "_object_attack_kind"),
            _set("_spell_attack_outcome", "'success'"),
            _when("_object_attack_kind == 'miss'", [_set("_spell_attack_outcome", "'failure'")]),
            _set("_spell_attack_advantage", "_object_advantage"),
            _set("_spell_attack_disadvantage", "_object_disadvantage"),
            _set("_spell_attack_mode", "'ranged'"),
            _set("_spell_damage", "_object_damage_roll"),
            _set("_spell_damage_amount", "_object_damage_applied"),
            _set("_spell_target_armor_class", "_object_effective_armor_class"),
            _set("_burst_object_raw_text", "'No damage roll (miss).'"),
            _when(
                "_object_damage_roll != null",
                [
                    _call(
                        "record_srd_result_text",
                        {"text": "Raw dice {_object_damage_roll.results}."},
                        {"_burst_object_raw_text": "_recorded_result_text"},
                    )
                ],
            ),
            _call(
                "record_srd_result_text",
                {
                    "text": (
                        "Object {object_id}; attack {_object_attack_kind}. D20 "
                        "{_object_attack_roll.results}; total {_object_attack_total} against AC "
                        "{_object_effective_armor_class}. {selected_damage_type} damage "
                        "{_object_damage_applied}; {_burst_object_raw_text} "
                        "Object HP {_object_hp_before} -> {_object_hp_after}."
                    )
                },
                {"_burst_object_detail": "_recorded_result_text"},
            ),
            _set(
                "_spell_summary_rows", [{"label": "object_id", "value": "_object_damage_applied"}]
            ),
            _set("_spell_detail_rows", [{"label": "object_id", "value": "_burst_object_detail"}]),
        ],
    )


def compose_object_burst(tools, changed):
    """Route only an actual object choice in the existing paid spell-attack branch."""
    changed["resolve_srd_sorcerous_burst_object_attack"] = _object_attack_tool(tools)
    changed["cast_srd_sorcerous_burst_object"] = _casting_tool()
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(changed.get(name, tools[name]))
        for effects in list(_effect_lists(tool["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if (
                    effect.get("condition")
                    == "spell == 'sorcerous_burst' and object_target_id != ''"
                ):
                    effects[index : index + 1] = effect["on_false"]
        for effects in list(_effect_lists(tool["effects"])):
            for index, effect in enumerate(effects):
                if effect.get("set", {}).get("field") == "_control_harm_scalar_target" and (
                    effect["set"]["value"] == "_scalar_target_id"
                ):
                    effects[index] = _when(
                        "spell == 'sorcerous_burst' and object_target_id != ''",
                        [_set("_control_harm_scalar_target", None)],
                        [effect],
                    )
                if effect.get("call") != "cast_spell_attack_damage":
                    continue
                if any(row.get("call") == "cast_srd_sorcerous_burst_object" for row in effects):
                    continue
                call = _call(
                    "cast_srd_sorcerous_burst_object",
                    {
                        "caster_state": "{_caster_state_id}",
                        "object_id": "{object_target_id}",
                        "casting_ability": "{_casting_profile.ability}",
                        "casting_modifier": "{_casting_profile.modifier}",
                        "casting_attack_bonus": "{_casting_profile.attack_bonus}",
                        "casting_scaling_level": "{_casting_profile.scaling_level}",
                        "selected_damage_type": "{selected_damage_type}",
                        "sorcerous_burst_extra_dice": "{sorcerous_burst_extra_dice}",
                        "sorcerous_burst_extra_dice_limit": "{sorcerous_burst_extra_dice_limit}",
                        "advantage": "{_spell_advantage}",
                        "disadvantage": "{_spell_disadvantage}",
                    },
                    deepcopy(effect.get("export_context", {})),
                )
                effects[index] = _when(
                    "spell == 'sorcerous_burst' and object_target_id != ''",
                    [
                        {
                            "require": {
                                "condition": (
                                    "target == null and (targets == null or len(targets) == 0) "
                                    "and (attack_targets == null or len(attack_targets) == 0)"
                                ),
                                "error": ("Choose one Sorcerous Burst creature or object target."),
                            }
                        },
                        call,
                    ],
                    [effect],
                )
        changed[name] = tool
