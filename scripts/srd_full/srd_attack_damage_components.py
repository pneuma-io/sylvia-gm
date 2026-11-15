"""Carry one actual attack-roll penalty through its printed typed components."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _parameter, _set, _when
else:
    from srd_actor_spell_modifiers import _call, _parameter, _set, _when


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _printed_component(tool, amount, modifier, carry):
    """Spend the already consumed base penalty on the next raw component, never reroll it."""
    parameter = _parameter(
        "unallocated_roll_penalty", "integer", required=False, default=0, minimum=0
    )
    if parameter["name"] not in {p["name"] for p in tool["parameters"]}:
        tool["parameters"].append(parameter)
    marker = "_attack_component_raw"
    if marker in str(tool["effects"]):
        return
    for effects in list(_lists(tool["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if (
                effect.get("condition") == "actor_state.spell_ray_of_enfeeblement == true"
                and "modify_srd_actor_raw_damage_roll" in str(effect)
            ):
                del effects[index]
            elif effect.get("set", {}).get("field") == amount:
                expression = effect["set"]["value"]
                if isinstance(expression, str) and expression.startswith("max(0,"):
                    effects[index : index + 1] = [
                        _set(marker, amount + " + " + modifier),
                        _set(carry, "max(0, unallocated_roll_penalty - max(0, " + marker + "))"),
                        _set(amount, "max(0, " + marker + " - unallocated_roll_penalty)"),
                    ]
            elif effect.get("call") == "apply_srd_qualifying_composite_damage":
                effect["params"].update(
                    unallocated_roll_penalty="{" + carry + "}", game_damage_roll=True
                )


def compose_attack_roll_allocations(tools, changed):
    """Bind the base owner's remaining penalty to actual hit wrappers and the final packet."""
    wrappers = {
        "apply_srd_form_attack_damage": (
            "_form_extra_damage_amount",
            "_extra_attack.extra_damage_modifier",
            "_form_roll_carry",
        ),
        "apply_srd_divine_favor_weapon_damage": ("_divine_favor_amount", "0", "_favor_roll_carry"),
    }
    for name, fields in wrappers.items():
        if name in tools:
            tool = deepcopy(changed.get(name, tools[name]))
            _printed_component(tool, *fields)
            changed[name] = tool

    for name in (
        "resolve_combat_attack",
        "resolve_combat_attack_followup",
        "resolve_opportunity_attack",
        "resolve_adjudicated_creature_attack",
    ):
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        marker = "_attack_unallocated_roll_penalty"
        if not any(e.get("set", {}).get("field") == marker for e in tool["effects"]):
            tool["effects"].insert(0, _set(marker, 0))
        for effects in list(_lists(tool["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if effect.get("call") == "modify_srd_actor_damage_roll":
                    effect.setdefault("export_context", {})[marker] = (
                        "_actor_unallocated_roll_penalty"
                    )
                elif effect.get("call") == "modify_srd_actor_raw_damage_roll":
                    if not any(
                        e.get("set", {}).get("field") == marker
                        for e in effects[index + 1 : index + 2]
                    ):
                        effects.insert(
                            index + 1,
                            _set(marker, "max(0, -(_ray_raw_damage + damage_modifier))"),
                        )
                elif effect.get("call") in {*wrappers, "apply_srd_qualifying_damage"}:
                    if "_graze" in str(effect):
                        continue
                    effect["params"]["unallocated_roll_penalty"] = "{" + marker + "}"
                    if effect["call"] == "apply_srd_qualifying_damage":
                        effect["params"]["game_damage_roll"] = True
        changed[name] = tool


def typed_component_mitigation(tools, prefix, amount, damage_type):
    """Reuse the real composite owner's defenses for one extra type before its HP transition."""
    effects = tools["apply_srd_composite_damage"]["effects"]
    start = next(
        i
        for i, effect in enumerate(effects)
        if effect.get("set", {}).get("field") == "_component_2_effective_damage"
    )
    end = next(
        i
        for i, effect in enumerate(effects[start:], start)
        if effect.get("set", {}).get("field") == "_incoming_damage"
    )

    def replace(value):
        if isinstance(value, str):
            return (
                value.replace("component_2_damage_type", damage_type)
                .replace("component_2_amount", amount)
                .replace("_component_2_", prefix)
            )
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {
                key.replace("_component_2_", prefix) if isinstance(key, str) else key: replace(item)
                for key, item in value.items()
            }
        return value

    return replace(deepcopy(effects[start:end]))


def _minor_choice():
    return _parameter(
        "minor_elementals_damage_type",
        "enum",
        required=False,
        default="fire",
        enum_values=["acid", "cold", "fire", "lightning"],
    )


def compose_minor_attack_damage(tools, changed):
    """Add the actual source's Minor Elementals component only at an owning attack hit."""
    minor_condition = (
        "actual_attack_hit == true and state.has_spell_environments == true and "
        "source_state_id != null and len(source_state_id) > 0"
    )
    for name in ("apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage"):
        tool = deepcopy(changed.get(name, tools[name]))
        tool["parameters"] += [
            _parameter("actual_attack_hit", "boolean", required=False, default=False),
            _parameter("observe_attack_components", "boolean", required=False, default=False),
            _minor_choice(),
        ]
        branch = next(
            e for e in tool["effects"] if "observe_base_damage == true" in e.get("condition", "")
        )
        branch["condition"] += " or actual_attack_hit == true or observe_attack_components == true"
        curse = branch["on_true"][1]
        for effect in curse["on_true"]:
            if effect.get("set", {}).get("field") == "_curse_extra_amount":
                effect["set"]["value"] = effect["set"]["value"].replace(
                    "unallocated_roll_penalty", "_attack_component_remaining_penalty"
                )
        branch["on_true"][1:1] = [
            _set("_attack_component_remaining_penalty", "unallocated_roll_penalty"),
            _when(
                "actual_attack_hit == true or observe_attack_components == true",
                [
                    _set("_conjure_minor_qualifies", False),
                    _set("_conjure_minor_amount", 0),
                    _set("_conjure_minor_type", "minor_elementals_damage_type"),
                    _set("_conjure_minor_roll", None),
                ],
            ),
            _when(
                minor_condition,
                [
                    _call(
                        "roll_srd_conjure_minor_damage_component",
                        {
                            "caster_state": "{source_state_id}",
                            "target_state": "{target}",
                            "critical_hit": "{critical_hit}",
                            "damage_type": "{minor_elementals_damage_type}",
                        },
                        (
                            "_conjure_minor_qualifies",
                            "_conjure_minor_amount",
                            "_conjure_minor_type",
                            "_conjure_minor_roll",
                        ),
                    ),
                    _when(
                        "_conjure_minor_qualifies == true",
                        [
                            _set("_minor_raw_component", "_conjure_minor_amount"),
                            _set(
                                "_conjure_minor_amount",
                                "max(0, _minor_raw_component - "
                                "_attack_component_remaining_penalty)",
                            ),
                            _set(
                                "_attack_component_remaining_penalty",
                                "max(0, _attack_component_remaining_penalty - "
                                "_minor_raw_component)",
                            ),
                            _when(
                                "target.has_timed_actor_defenses == true",
                                [
                                    _call(
                                        "resolve_srd_timed_actor_defenses",
                                        {"target_state": "{target}", "lasting_state": "state"},
                                        ("_actor_defense_damage_immunities",),
                                    ),
                                ],
                            ),
                            *typed_component_mitigation(
                                tools, "_minor_", "_conjure_minor_amount", "_conjure_minor_type"
                            ),
                            _set("_incoming_damage", "_incoming_damage + _conjure_minor_amount"),
                            _set(
                                "_effective_damage", "_effective_damage + _minor_effective_damage"
                            ),
                        ],
                    ),
                ],
            ),
        ]
        changed[name] = tool

    owners = {
        "resolve_combat_attack",
        "resolve_combat_attack_followup",
        "resolve_opportunity_attack",
        "resolve_adjudicated_creature_attack",
        "cast_spell_attack_damage",
        "resolve_srd_unarmed_effect",
        "apply_srd_multi_attack_damage",
        "apply_srd_divine_favor_weapon_damage",
        "apply_srd_form_attack_damage",
    }
    for name in owners:
        tool = deepcopy(changed.get(name, tools[name]))
        if name == "apply_srd_multi_attack_damage":
            packet = next(
                effect
                for effects in _lists(tool["effects"])
                for effect in effects
                if effect.get("call") == "apply_srd_qualifying_damage"
            )
            tool["effects"] = [packet]
            if "unallocated_roll_penalty" not in {p["name"] for p in tool["parameters"]}:
                tool["parameters"].append(
                    _parameter(
                        "unallocated_roll_penalty", "integer", required=False, default=0, minimum=0
                    )
                )
            packet["params"]["unallocated_roll_penalty"] = "{unallocated_roll_penalty}"
        if "minor_elementals_damage_type" not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(_minor_choice())
        for effects in list(_lists(tool["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if effect.get("call") in {
                    "apply_srd_qualifying_damage",
                    "apply_srd_qualifying_composite_damage",
                }:
                    if "_graze" in str(effect):
                        continue
                    effect["params"].update(
                        actual_attack_hit=True,
                        minor_elementals_damage_type="{minor_elementals_damage_type}",
                        game_damage_roll=True,
                    )
                    if name == "cast_spell_attack_damage":
                        # Acid Arrow deals spell damage on a miss; only its genuine hit earns Minor.
                        if not any(
                            e.get("set", {}).get("field") == "_minor_source_attack_hit"
                            for e in effects[:index]
                        ):
                            effects.insert(
                                index,
                                _set(
                                    "_minor_source_attack_hit", "_spell_attack_outcome == 'success'"
                                ),
                            )
                        effect["params"].update(
                            actual_attack_hit="{_minor_source_attack_hit}",
                            observe_attack_components=True,
                        )
                    exports = effect.setdefault("export_context", {})
                    for field in (
                        "_conjure_minor_qualifies",
                        "_conjure_minor_amount",
                        "_conjure_minor_type",
                        "_conjure_minor_roll",
                    ):
                        exports[field] = field
        changed[name] = tool

    for name, raw, destination in (
        (
            "resolve_spell_multi_attack_target",
            "_spell_damage.total",
            "apply_srd_multi_attack_damage",
        ),
        (
            "resolve_srd_unarmed_effect",
            "_unarmed_base_damage + _unarmed_ability_modifier",
            "apply_srd_qualifying_damage",
        ),
    ):
        tool = deepcopy(changed.get(name, tools[name]))
        for effects in list(_lists(tool["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                call = effects[index]
                if call.get("call") != destination:
                    continue
                if "_minor_producer_roll_carry" not in call["params"].get(
                    "unallocated_roll_penalty", ""
                ):
                    effects.insert(
                        index, _set("_minor_producer_roll_carry", "max(0, -(" + raw + "))")
                    )
                    call["params"]["unallocated_roll_penalty"] = "{_minor_producer_roll_carry}"
                    if name == "resolve_spell_multi_attack_target":
                        effects.insert(
                            index + 1, _set("_minor_multi_base_amount", "max(0, " + raw + ")")
                        )
                        call["params"]["amount"] = "{_minor_multi_base_amount}"
        changed[name] = tool

    # Forward only a choice through the existing producer graph; the hit marker stays private.
    affected = set(owners)
    pending = True
    while pending:
        pending = False
        for name, original in tools.items():
            if name.startswith("complete_srd_"):
                continue
            tool = deepcopy(changed.get(name, original))
            calls = [
                effect
                for effects in _lists(tool["effects"])
                for effect in effects
                if effect.get("call") in affected
            ]
            if not calls:
                continue
            if "minor_elementals_damage_type" not in {p["name"] for p in tool["parameters"]}:
                tool["parameters"].append(_minor_choice())
            for call in calls:
                call["params"]["minor_elementals_damage_type"] = "{minor_elementals_damage_type}"
            changed[name] = tool
            if name not in affected:
                affected.add(name)
                pending = True
