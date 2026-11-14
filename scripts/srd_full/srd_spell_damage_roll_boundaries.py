"""Preserve actual pre-half and saved later spell game rolls at the shared typed packet."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _set, _when
    from .srd_attack_damage_components import _lists
else:
    from srd_actor_spell_modifiers import _set, _when
    from srd_attack_damage_components import _lists


def compose_spell_damage_roll_boundaries(tools, changed):
    """Keep a missed initial roll and a genuine stored spell event on the single-roll contract."""
    attack = deepcopy(changed.get("cast_spell_attack_damage", tools["cast_spell_attack_damage"]))
    if "_miss_damage_roll_divisor" not in str(attack["effects"]):
        for effects in list(_lists(attack["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if effect.get("call") != "apply_srd_qualifying_damage":
                    continue
                effects[index:index] = [
                    _set("_miss_damage_roll_divisor", 1),
                    _when(
                        "_spell_attack_outcome != 'success' and miss_damage == 'half'",
                        [_set("_miss_damage_roll_divisor", 2)],
                    ),
                    _set("_miss_damage_roll_raw", "_spell_damage.total + flat_damage"),
                    _set("_miss_damage_roll_clamped", "max(0, _miss_damage_roll_raw)"),
                    _set(
                        "_miss_damage_roll_base",
                        "_miss_damage_roll_clamped // _miss_damage_roll_divisor",
                    ),
                    _set("_miss_damage_roll_carry", "max(0, -_miss_damage_roll_raw)"),
                    _set(
                        "_miss_damage_roll_remainder",
                        "_miss_damage_roll_clamped % _miss_damage_roll_divisor",
                    ),
                ]
                effect["params"].update(
                    amount="{_miss_damage_roll_base}",
                    unallocated_roll_penalty="{_miss_damage_roll_carry}",
                    damage_roll_divisor="{_miss_damage_roll_divisor}",
                    damage_roll_base_remainder="{_miss_damage_roll_remainder}",
                    game_damage_roll=True,
                )
        changed["cast_spell_attack_damage"] = attack

    delayed = deepcopy(
        changed.get("resolve_srd_delayed_spell_damage", tools["resolve_srd_delayed_spell_damage"])
    )
    if "_delayed_damage_roll_carry" not in str(delayed["effects"]):
        for effects in list(_lists(delayed["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if effect.get("call") != "apply_srd_qualifying_damage":
                    continue
                effects.insert(
                    index, _set("_delayed_damage_roll_carry", "max(0, -_delayed_roll.total)")
                )
                effect["params"].update(
                    unallocated_roll_penalty="{_delayed_damage_roll_carry}",
                    game_damage_roll=True,
                )
        changed["resolve_srd_delayed_spell_damage"] = delayed
