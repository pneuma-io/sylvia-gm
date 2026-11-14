"""Actual final attack hits divert to Mirror Image before creature damage or hit riders."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS
    from .srd_timed_actor_defenses import _call, _load_runtime, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS
    from srd_timed_actor_defenses import _call, _load_runtime, _set, _when
    from srd_timed_senses import _replace_tools


MIRROR_EXPORTS = (
    "_mirror_redirected",
    "_mirror_dice",
    "_mirror_source_id",
    "_mirror_remaining",
)
WEAPON_OWNERS = {
    "resolve_combat_attack": ("actor_state", "target"),
    "resolve_combat_attack_followup": ("actor_state", "target"),
    "resolve_adjudicated_creature_attack": ("actor", "target"),
    "resolve_opportunity_attack": ("actor_state", "target"),
}


def _lists(effects):
    """Visit actual effect branches without interpreting stored parameters or context payloads."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _mirror_call(tool, attacker, target):
    params = {
        "target_state": "{_" + target + "_id}",
        "attacker_state": "{_" + attacker + "_id}",
    }
    if any(p["name"] == "lasting_state" for p in tool["parameters"]):
        params["lasting_state"] = "{_lasting_state_id}"
    return _call("resolve_srd_mirror_image_hit", params, MIRROR_EXPORTS)


def _sense_reach(tool):
    """Use current timed/native senses and real scene distance; positive range is insufficient."""
    tool = deepcopy(tool)
    if "_mirror_target_distance" in str(tool["effects"]):
        return tool
    found = 0
    for effects in list(_lists(tool["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("set", {}).get("field") != "_mirror_bypass":
                continue
            expected = (
                "'blinded' in attacker_state.conditions or _sense_truesight > 0 "
                "or (attacker_state.blindsight_feet != null and "
                "attacker_state.blindsight_feet > 0)"
            )
            if effect["set"]["value"] != expected:
                raise ValueError("Mirror Image's owning sensory exception changed.")
            prefix = []
            for role in ("attacker", "target"):
                prefix += [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_" + role + "_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_mirror_" + role + "_origin", "_info_position"),
                ]
            prefix += [
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_mirror_attacker_origin}", "end": "{_mirror_target_origin}"},
                    ["_scene_distance"],
                ),
                _set("_mirror_target_distance", "_scene_distance"),
                _set("_mirror_blindsight_feet", 0),
                _when(
                    "attacker_state.blindsight_feet != null",
                    [_set("_mirror_blindsight_feet", "attacker_state.blindsight_feet")],
                ),
            ]
            effects[index:index] = prefix
            effect["set"]["value"] = (
                "'blinded' in attacker_state.conditions or "
                "(_sense_truesight > 0 and _mirror_target_distance <= _sense_truesight) or "
                "(_mirror_blindsight_feet > 0 and "
                "_mirror_target_distance <= _mirror_blindsight_feet)"
            )
            found += 1
    if found != 1:
        raise ValueError("Mirror Image needs its exact saved hit sensory boundary.")
    for effects in _lists(tool["effects"]):
        for effect in effects:
            if effect.get("call") in {
                "collect_srd_illusion_source",
                "retire_srd_illusion_source",
                "resolve_srd_timed_senses",
                "srd_information_scene_entity",
            }:
                effect["params"]["lasting_state"] = "{_lasting_state_id}"
    return tool


def _card(tool):
    card = tool.get("metadata", {}).get("result_card")
    if card is None:
        return
    rows = card.setdefault("details", [])
    if not isinstance(rows, list):
        return
    if not any(row.get("label") == "Mirror Image diverted" for row in rows):
        rows += [
            {
                "label": "Mirror Image diverted",
                "value": {"path": "context._mirror_redirected"},
                "optional": True,
                "omit_zero": True,
            },
            {
                "label": "Duplicates remaining",
                "value": {"path": "context._mirror_remaining"},
                "optional": True,
                "omit_zero": True,
            },
        ]


def _defaults(tool):
    """Misses, object attacks and save-only Unarmed choices export an explicit empty result."""
    tool["effects"] = [
        _set("_mirror_redirected", False),
        _set("_mirror_dice", []),
        _set("_mirror_source_id", "''"),
        _set("_mirror_remaining", 0),
        *tool["effects"],
    ]


def _late_riders(tool, *, unarmed=False):
    """Retaliation and Stunning Strike occur only when the creature itself was struck."""
    for effect in tool["effects"]:
        condition = effect.get("condition", "")
        if condition == "use_stunning_strike == true" or condition == (
            "_attack_target_fire_shield_mode == 'warm' or "
            "_attack_target_fire_shield_mode == 'chill'"
        ):
            qualifier = "_mirror_redirected != true"
            if unarmed:
                qualifier = "(effect != 'damage' or _mirror_redirected != true)"
            effect["condition"] = f"({condition}) and {qualifier}"


def _weapon(tool, attacker, target):
    tool = deepcopy(tool)
    if "resolve_srd_mirror_image_hit" in str(tool["effects"]):
        return tool
    branches = [e for e in tool["effects"] if e.get("condition") == "_attack_outcome != 'miss'"]
    if len(branches) != 1:
        raise ValueError("Mirror Image needs the actual final weapon hit branch.")
    branch = branches[0]
    branch["on_true"] = [
        _mirror_call(tool, attacker, target),
        _when("_mirror_redirected != true", branch["on_true"]),
    ]
    _late_riders(tool)
    _card(tool)
    _defaults(tool)
    return tool


def _spell(tool, *, multi=False):
    tool = deepcopy(tool)
    if "resolve_srd_mirror_image_hit" in str(tool["effects"]):
        return tool
    condition = "_spell_attack_outcome == 'success'"
    if not multi:
        condition += " or miss_damage == 'half'"
    found = 0
    for effects in list(_lists(tool["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("condition") != condition or not any(
                e.get("store_as") == "_spell_damage" for e in effect.get("on_true", [])
            ):
                continue
            if multi:
                effect["on_true"] = [
                    _mirror_call(tool, "caster_state", "target"),
                    _when("_mirror_redirected != true", effect["on_true"]),
                ]
            else:
                effects[index:index] = [
                    _set("_mirror_redirected", False),
                    _when(
                        "_spell_attack_outcome == 'success'",
                        [_mirror_call(tool, "caster_state", "target")],
                    ),
                ]
                effect["on_true"] = [_when("_mirror_redirected != true", effect["on_true"])]
            found += 1
    if found != 1:
        raise ValueError("Mirror Image needs the actual single spell hit damage branch.")
    for effects in _lists(tool["effects"]):
        for effect in effects:
            if effect.get("condition") == (
                "_attack_target_fire_shield_mode == 'warm' or "
                "_attack_target_fire_shield_mode == 'chill'"
            ):
                effect["condition"] = "(" + effect["condition"] + ") and _mirror_redirected != true"
        for index in range(len(effects) - 1, -1, -1):
            if effects[index].get("condition") == "_spell_attack_kind == 'critical_hit'" and any(
                e.get("set", {}).get("field") == "_card_attack_label"
                for e in effects[index].get("on_true", [])
            ):
                effects.insert(
                    index + 1,
                    _when(
                        "_mirror_redirected == true",
                        [_set("_card_attack_label", "'Duplicate hit'")],
                    ),
                )
    _card(tool)
    _defaults(tool)
    return tool


def _unarmed(tool):
    tool = deepcopy(tool)
    if "resolve_srd_mirror_image_hit" in str(tool["effects"]):
        return tool
    branches = [
        e
        for e in tool["effects"]
        if e.get("condition") == "effect == 'damage'"
        and any(x.get("call") == "d20_test" for x in e.get("on_true", []))
    ]
    if len(branches) != 1:
        raise ValueError("Mirror Image must bind Unarmed Damage, never Grapple or Shove saves.")
    effects = branches[0]["on_true"]
    split = (
        next(
            i
            for i, e in enumerate(effects)
            if e.get("set", {}).get("field") == "_unarmed_critical_hit"
        )
        + 1
    )
    for effect in effects[split:]:
        if effect.get("condition", "").startswith("_unarmed_success == true"):
            effect["condition"] = "(" + effect["condition"] + ") and _mirror_redirected != true"
    effects[split:split] = [
        _set("_mirror_redirected", False),
        _when("_unarmed_success == true", [_mirror_call(tool, "actor_state", "target_state")]),
    ]
    effects.append(
        _when("_mirror_redirected == true", [_set("_unarmed_result", "'Duplicate hit'")])
    )
    _late_riders(tool, unarmed=True)
    _card(tool)
    _defaults(tool)
    return tool


def _produce(tool):
    tool = deepcopy(tool)
    if "resolve_srd_mirror_image_hit" in str(tool["effects"]):
        return tool
    found = 0
    for effects in list(_lists(tool["effects"])):
        for effect in effects:
            if effect.get("condition") != "_produce_attack_kind != 'miss'":
                continue
            if not any(e.get("store_as") == "_produce_damage" for e in effect["on_true"]):
                continue
            # Produce's critical flag is assigned after dice; its attack kind is already final.
            effect["on_true"] = [
                _mirror_call(tool, "caster_state", "target"),
                _when("_mirror_redirected != true", effect["on_true"]),
            ]
            found += 1
    if found != 1:
        raise ValueError(
            "Mirror Image needs Produce Flame's real creature hit before its damage dice."
        )
    _card(tool)
    _defaults(tool)
    return tool


def _wrapper(tool, child_names):
    """Carry the owning diversion to hit-only form riders without altering native roll outcomes."""
    tool = deepcopy(tool)
    found = False
    for effects in _lists(tool["effects"]):
        for effect in effects:
            if effect.get("call") in child_names:
                exports = effect.setdefault("export_context", {})
                exports.update({name: name for name in MIRROR_EXPORTS})
                found = True
    if found:
        for effects in _lists(tool["effects"]):
            for effect in effects:
                condition = effect.get("condition", "")
                if (
                    (
                        "_attack_outcome == 'hit'" in condition
                        or "_attack_outcome == 'critical_hit'" in condition
                        or "_spell_attack_outcome == 'success'" in condition
                        or "_unarmed_success == true" in condition
                    )
                    and any("call" in e for e in effect.get("on_true", []))
                    and "_mirror_redirected" not in condition
                ):
                    effect["condition"] = "(" + condition + ") and _mirror_redirected != true"
        _card(tool)
    return tool, found


def _tools(runtime):
    tools = runtime["tools"]
    changed = {"resolve_srd_mirror_image_hit": _sense_reach(tools["resolve_srd_mirror_image_hit"])}
    for name, roles in WEAPON_OWNERS.items():
        if name in tools:
            changed[name] = _weapon(tools[name], *roles)
    changed["resolve_srd_unarmed_effect"] = _unarmed(tools["resolve_srd_unarmed_effect"])
    changed["cast_spell_attack_damage"] = _spell(tools["cast_spell_attack_damage"])
    changed["resolve_spell_multi_attack_target"] = _spell(
        tools["resolve_spell_multi_attack_target"], multi=True
    )
    produce_name = "hurl_srd_produce_flame"
    if "hurl_srd_produce_flame_node" in tools:
        produce_name += "_node"
    changed[produce_name] = _produce(tools[produce_name])
    for name in (
        "attack_srd_transformed_form",
        "attack_srd_altered_natural_weapon",
        "resolve_unarmed_strike",
        "resolve_unarmed_opportunity_attack",
        "resolve_monk_bonus_strike",
        "attack_srd_spell_force",
        "hurl_srd_produce_flame",
        "cast_spell",
        "cast_spell_outside_combat",
    ):
        if name in changed or name not in tools:
            continue
        wrapper, found = _wrapper(tools[name], set(changed))
        if found:
            changed[name] = wrapper
    return changed


def bind_runtime_consumers(text):
    """Bind final family hit owners before Reaction clones retain their damage continuations."""
    runtime = _load_runtime(text)
    return _replace_tools(text, _tools(runtime))
