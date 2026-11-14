"""Flame Blade uses one genuine prepared-object melee attack and one durability event."""

import re
from copy import deepcopy

if __package__:
    from .srd_flame_blade import _use_tool
    from .srd_retained_spell_attacks import _lists
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _set, _when
    from .srd_timed_senses import _public, _replace_tools
else:
    from srd_flame_blade import _use_tool
    from srd_retained_spell_attacks import _lists
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _set, _when
    from srd_timed_senses import _public, _replace_tools


PUBLIC_TOOLS = {"use_srd_flame_blade_object"}
OBJECT_EXPORTS = (
    "_object_attack_kind",
    "_object_attack_total",
    "_object_effective_armor_class",
    "_object_attack_roll",
    "_object_damage_roll",
    "_object_damage_rolled",
    "_object_damage_adjusted",
    "_object_damage_applied",
    "_object_hp_after",
    "_object_destroyed",
    "_object_can_see",
    "_object_advantage",
    "_object_disadvantage",
)


def _attack_tool(tools):
    """Keep the settled object roll/sight/HP suffix and modify its actual raw Fire producer once."""
    tool = deepcopy(tools["resolve_srd_spell_object_attack"])
    tool["parameters"] += [
        _parameter("casting_ability"),
        _parameter("casting_modifier", "integer"),
    ]
    raw = []
    zero = []
    mitigation = []
    for sequence in _lists(tool["effects"]):
        for index, effect in enumerate(sequence):
            if effect.get("call") == "d20_test":
                effect["params"]["ability"] = "{casting_ability}"
            if (
                effect.get("set", {}).get("field") == "_object_damage_rolled"
                and effect["set"]["value"] != 0
            ):
                raw.append((sequence, index))
                effect["set"]["value"] = "max(0, _object_damage_roll.total + casting_modifier)"
            elif effect.get("set", {}).get("field") == "_object_damage_rolled":
                zero.append((sequence, effect))
            if (
                effect.get("set", {}).get("field") == "_object_damage_applied"
                and effect["set"]["value"] != 0
            ):
                mitigation.append(effect)
    if len(raw) != 1 or len(zero) != 1 or not mitigation:
        raise ValueError(
            "Flame Blade needs the one settled object raw producer and durability suffix."
        )
    if any(
        not isinstance(effect["set"]["value"], str)
        or not re.search(r"\b_object_damage_rolled\b", effect["set"]["value"])
        for effect in mitigation
    ):
        raise ValueError("Flame Blade needs each settled typed suffix to consume its raw amount.")
    if "modify_srd_actor_damage_roll" not in str(tool["effects"]):
        sequence, index = raw[0]
        sequence[index + 1 : index + 1] = [
            _set("_object_damage_adjusted", "_object_damage_rolled"),
            _when(
                "caster_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "modify_srd_actor_damage_roll",
                        {
                            "actor_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "amount": "{_object_damage_rolled}",
                        },
                        ["_actor_modified_damage"],
                    ),
                    _set("_object_damage_adjusted", "_actor_modified_damage"),
                ],
            ),
        ]
        for effect in mitigation:
            effect["set"]["value"] = re.sub(
                r"\b_object_damage_rolled\b", "_object_damage_adjusted", effect["set"]["value"]
            )
    sequence, initializer = zero[0]
    sequence.insert(sequence.index(initializer) + 1, _set("_object_damage_adjusted", 0))
    tool["description"] = (
        "Resolve Flame Blade's melee object hit, Fire dice plus casting modifier and one HP event."
    )
    return tool


def _object_use_tool():
    """Reuse actual source/hand and Magic cost qualification before the one object attack."""
    tool = _use_tool()
    target = next(p for p in tool["parameters"] if p["name"] == "target")
    tool["parameters"][tool["parameters"].index(target)] = _parameter("object_id")
    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            if effect.get("call") == "resolve_srd_flame_blade_attack":
                effect["call"] = "resolve_srd_flame_blade_object_attack"
                effect["params"] = {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "attack_modifier": "{_flame_attack_bonus}",
                    "casting_ability": "{_flame_casting_ability}",
                    "casting_modifier": "{_flame_casting_modifier}",
                    "damage_die": "d6",
                    "dice_count": "{_flame_dice_count}",
                    "damage_type": "fire",
                    "max_range_feet": "{_flame_max_range}",
                }
                effect["export_context"] = {name: name for name in OBJECT_EXPORTS}
    tool["effects"] += [
        _set("_flame_object_target", "object_id"),
        _set("_flame_object_attack_dice", "_object_attack_roll.results"),
        _set("_flame_object_damage_dice", []),
        _when(
            "_object_damage_roll != null",
            [_set("_flame_object_damage_dice", "_object_damage_roll.results")],
        ),
    ]
    return _public(
        tool,
        "combat",
        "Flame Blade",
        [
            ("Object", "_flame_object_target"),
            ("Attack", "_object_attack_kind"),
            ("Attack dice", "_flame_object_attack_dice"),
            ("Attack total", "_object_attack_total"),
            ("Object AC with Cover", "_object_effective_armor_class"),
            ("Fire dice", "_flame_object_damage_dice"),
            ("Raw Fire amount", "_object_damage_rolled"),
            ("Fire after source penalty", "_object_damage_adjusted"),
            ("Actual damage", "_object_damage_applied"),
            ("Object HP after", "_object_hp_after"),
            ("Object destroyed", "_object_destroyed"),
            ("Object seen", "_object_can_see"),
            ("Source", "_flame_source_id"),
        ],
    )


def bind_runtime_consumers(text):
    """Bind after the final object relation/light/Cover owner and before ordinary pending fences."""
    tools = _load_runtime(text)["tools"]
    return _replace_tools(
        text,
        {
            "resolve_srd_flame_blade_object_attack": _attack_tool(tools),
            "use_srd_flame_blade_object": _object_use_tool(),
        },
    )
