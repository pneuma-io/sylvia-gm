"""Use the occupied body's actual equipment without replacing the caster's combat grants."""

import re
from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _call, _entity, _param, _require, _set, _tool, _when
    from .srd_magic_jar_consumers import _forward_params, _walk
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _call, _entity, _param, _require, _set, _tool, _when
    from srd_magic_jar_consumers import _forward_params, _walk
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools


EQUIPMENT_OWNER = "resolve_srd_magic_jar_body_equipment"
EQUIPMENT_GUARD = "actor_state.magic_jar_role == 'possessed'"
ATTACK_GUARD = "actor_state.magic_jar_role == 'possessed'"
SHIELD_GUARD = "wielding_two_handed != true or actor_state.worn_shield == null"
HEAVY_GUARD = "actor_state.magic_jar_role == 'possessed' and weapon_info != null"


def _physical_equipment(value):
    """Only carried and worn facts change owner; training and projected ability scores do not."""
    if isinstance(value, str):
        return re.sub(
            r"\bactor_state\.(inventory|worn_armor|worn_shield)\b",
            r"jar_equipment_body.\1",
            value,
        )
    if isinstance(value, list):
        return [_physical_equipment(item) for item in value]
    if isinstance(value, dict):
        return {key: _physical_equipment(item) for key, item in value.items()}
    return value


def _original_effects(tool, condition):
    effects = []
    for node in tool["effects"]:
        effects.extend(
            node["on_false"]
            if node.get("condition") == condition and "on_false" in node
            else [node]
        )
    return effects


def _equipment(tools, changed, name, inner_name):
    original = deepcopy(tools[name])
    original["effects"] = _original_effects(original, EQUIPMENT_GUARD)
    exports = set()

    def collect(node):
        if node.get("call") == name:
            exports.update(node.get("export_context", {}).values())

    _walk(tools, collect)
    delegated = deepcopy(original)
    delegated["parameters"].append(
        _param("jar_equipment_body", resolve="entity", model_hidden=True)
    )
    delegated["effects"] = _physical_equipment(delegated["effects"])
    delegated["pre_conditions"] = _physical_equipment(delegated.get("pre_conditions", []))
    wrapper = deepcopy(original)
    wrapper["effects"] = [
        _when(
            EQUIPMENT_GUARD,
            [
                _call(
                    "read_srd_magic_jar_source",
                    {"caster_state": "{_actor_state_id}"},
                    ("_jar_source",),
                ),
                _call(
                    inner_name,
                    _forward_params(original["parameters"])
                    | {"jar_equipment_body": "{_jar_source.host}"},
                    tuple(sorted(exports)),
                ),
            ],
            original["effects"],
        )
    ]
    changed.update({name: wrapper, inner_name: delegated})


def _armor_conditions(tools, changed):
    """Use actual host armor training penalties without persisting foreign flags on the caster."""
    tool = deepcopy(tools["resolve_srd_attack_conditions"])
    guard = "actor_state.magic_jar_role == 'possessed' and actor_state.creature_kind == 'character'"
    replacement = (
        "((actor_state.magic_jar_role != 'possessed' or "
        "actor_state.creature_kind != 'character') and actor_state.armor_untrained == true "
        "or (actor_state.magic_jar_role == 'possessed' and "
        "actor_state.creature_kind == 'character' and _jar_armor_untrained == true))"
    )
    original = [node for node in tool["effects"] if node.get("condition") != guard]
    effects = []
    for node in original:
        assignment = node.get("set", {})
        if assignment.get("field") == "_condition_disadvantage":
            expression = assignment["value"].replace(
                replacement, "actor_state.armor_untrained == true"
            )
            assignment["value"] = expression
            effects.append(node)
            effects.append(
                _when(
                    guard,
                    [
                        _call(
                            "resolve_srd_equipment",
                            {"actor": "{_actor_state_id}", "actor_state": "{_actor_state_id}"},
                            ("_equipment_untrained_armor",),
                        ),
                        _set("_jar_armor_untrained", "_equipment_untrained_armor"),
                        _set(
                            "_condition_disadvantage",
                            expression.replace(
                                "actor_state.armor_untrained == true",
                                "_jar_armor_untrained == true",
                            ),
                        ),
                    ],
                )
            )
        else:
            effects.append(node)
    tool["effects"] = effects
    changed["resolve_srd_attack_conditions"] = tool


def _holding(tools, changed):
    for name in ("record_srd_control_held_items", "drop_srd_control_held_items"):
        original = deepcopy(tools[name])
        guard = "target_state.magic_jar_role == 'possessed'"
        original["effects"] = _original_effects(original, guard)
        inner_name = name.replace("srd_control", "srd_magic_jar_body")
        inner = deepcopy(original)
        inner["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[inner_name] = inner
        wrapper = deepcopy(original)
        wrapper["effects"] = [
            _when(
                guard,
                [
                    _call(
                        "read_srd_magic_jar_source",
                        {"caster_state": "{_target_state_id}"},
                        ("_jar_source",),
                    ),
                    _call(
                        inner_name,
                        _forward_params(original["parameters"])
                        | {"target_state": "{_jar_source.host}"},
                    ),
                ],
                original["effects"],
            )
        ]
        changed[name] = wrapper


def _weapon_tool():
    return _tool(
        "Qualify a catalog melee weapon actually held by the exact occupied body.",
        [
            _entity("actor_state"),
            _entity("body_state"),
            _param("weapon"),
            _param("attack_mode"),
            _param("attack_sequence"),
            _param("wielding_two_handed", "boolean"),
            _param("other_wielded_weapons", "array", items={"type": "string"}),
            _param("use_martial_arts_die", "boolean"),
        ],
        [
            _require(
                "actor_state.magic_jar_role == 'possessed' and "
                "body_state.magic_jar_role == 'host_soul' and "
                "body_state.magic_jar_caster_id == _actor_state_id and "
                "body_state.magic_jar_source_id == actor_state.magic_jar_source_id",
                "Equipment requires this exact currently occupied body.",
            ),
            _require(
                "body_state.inventory != null and weapon in body_state.inventory and "
                "body_state.held_items != null and weapon in body_state.held_items",
                "The occupied body must actually carry and hold the selected catalog weapon.",
            ),
            _require(
                "attack_mode == 'melee' and attack_sequence == 'primary' and "
                "len(other_wielded_weapons) == 0 and use_martial_arts_die != true",
                "Occupied-body ammunition, extra Light attacks and Martial Arts "
                "need their physical owners.",
            ),
            _require(
                "actor_state.has_actor_weapon_modifiers != true and "
                "body_state.has_actor_weapon_modifiers != true "
                "and actor_state.actor_size_projected != true",
                "Weapon spell grants and resized equipment need "
                "an exact occupied-body item binding.",
            ),
            _require(
                "wielding_two_handed != true or body_state.worn_shield == null",
                "Remove the occupied body's wielded shield before using a weapon in two hands.",
            ),
        ],
    )


def _attacks(tools, changed):
    marker = ATTACK_GUARD
    for name in (
        "resolve_combat_attack",
        "resolve_combat_attack_followup",
        "begin_srd_character_attack",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        tool["effects"] = [n for n in tool["effects"] if n.get("condition") != marker]
        for pre in tool.get("pre_conditions", []):
            if pre["condition"] == SHIELD_GUARD:
                pre["condition"] = (
                    "actor_state.magic_jar_role == 'possessed' or (" + SHIELD_GUARD + ")"
                )
        tool["effects"].insert(
            0,
            _when(
                marker,
                [
                    _call(
                        "read_srd_magic_jar_source",
                        {"caster_state": "{_actor_state_id}"},
                        ("_jar_source",),
                    ),
                    _call(
                        "check_srd_magic_jar_body_weapon",
                        {
                            "actor_state": "{_actor_state_id}",
                            "body_state": "{_jar_source.host}",
                            **{
                                p: "{" + p + "}"
                                for p in (
                                    "weapon",
                                    "attack_mode",
                                    "attack_sequence",
                                    "wielding_two_handed",
                                    "other_wielded_weapons",
                                    "use_martial_arts_die",
                                )
                            },
                        },
                    ),
                ],
            ),
        )
        modified = []
        for node in tool["effects"]:
            modified.append(node)
            if (
                node.get("set", {}).get("field") == "_ability_modifier"
                and node["set"]["value"] == "(actor.data.abilities[attack_ability] - 10) // 2"
            ):
                modified.append(
                    _when(
                        marker,
                        [
                            _set(
                                "_ability_modifier",
                                "(actor_state.ability_scores[attack_ability] - 10) // 2",
                            )
                        ],
                    )
                )
        tool["effects"] = modified
        changed[name] = tool


def _weapon_properties(tools, changed):
    """Read Heavy thresholds from the occupied body; remote Light stock grants no followup."""
    light_prefix = "actor_state.magic_jar_role != 'possessed' and ("

    def project(value):
        if isinstance(value, list):
            return [project(node) for node in value]
        if not isinstance(value, dict):
            return value
        if value.get("condition") == HEAVY_GUARD and "on_false" in value:
            value = value["on_false"][0]
        node = {key: project(item) for key, item in value.items()}
        condition = node.get("condition", "")
        if "'heavy' in weapon_info.properties" in condition and "actor.data.abilities" in condition:
            physical = deepcopy(node)
            physical["condition"] = condition.replace(
                "actor.data.abilities.strength", "actor_state.ability_scores.strength"
            ).replace("actor.data.abilities.dexterity", "actor_state.ability_scores.dexterity")
            return _when(HEAVY_GUARD, [physical], [node])
        if (
            "actor_state.inventory" in condition
            and "_different_light_weapon_available" in str(node.get("on_true", []))
            and not condition.startswith(light_prefix)
        ):
            node["condition"] = light_prefix + condition + ")"
        return node

    for name in (
        "resolve_combat_attack",
        "resolve_combat_attack_followup",
        "begin_srd_character_attack",
        "continue_srd_character_attack_targeting",
    ):
        if name in tools:
            tool = deepcopy(changed.get(name, tools[name]))
            tool["effects"] = project(tool["effects"])
            changed[name] = tool


def bind_equipment_consumers(text):
    """Compose final equipment/holding bodies before reaction and attack continuation extraction."""
    tools = _load_runtime(text)["tools"]
    for name in (
        "resolve_srd_equipment",
        "read_srd_magic_jar_source",
        "record_srd_control_held_items",
        "drop_srd_control_held_items",
    ):
        if name not in tools:
            raise ValueError(f"Magic Jar physical equipment requires {name}")
    changed = {"check_srd_magic_jar_body_weapon": _weapon_tool()}
    _equipment(tools, changed, "resolve_srd_equipment", EQUIPMENT_OWNER)
    _equipment(tools, changed, "derive_srd_martial_arts", "derive_srd_magic_jar_body_martial_arts")
    _armor_conditions(tools, changed)
    _holding(tools, changed)
    _attacks(tools, changed)
    _weapon_properties(tools, changed)
    return _replace_tools(text, changed)
