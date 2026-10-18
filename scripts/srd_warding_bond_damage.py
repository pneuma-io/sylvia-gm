"""Reflect an actual Bond recipient's typed packet through one receiving lifecycle."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit, _parameter, _set, _tool, _when
    from .srd_attack_damage_components import typed_component_mitigation
    from .srd_timed_actor_defenses import _entity, _load_runtime, _require
else:
    from srd_actor_spell_modifiers import _call, _emit, _parameter, _set, _tool, _when
    from srd_attack_damage_components import typed_component_mitigation
    from srd_timed_actor_defenses import _entity, _load_runtime, _require


DEALT_CALLBACKS = {
    "end_srd_invisibility_damage_source",
    "end_srd_mislead_damage_source",
    "end_srd_sanctuary_damage_source",
}
BOND_OBSERVER = "_effective_damage > 0 and lasting_state.has_spell_wards == true"


def _without_bond(effects):
    """Remove only this composer's prior joins before a family regenerates its qualifier."""
    result = []
    for original in effects:
        if original.get("condition") == BOND_OBSERVER and "_bond_pending_reflection" in str(
            original
        ):
            continue
        effect = deepcopy(original)
        if "_bond_pending_reflection" in str(original):
            suffix = f") or ({BOND_OBSERVER})"
            if effect.get("condition", "").startswith("(") and effect["condition"].endswith(suffix):
                effect["condition"] = effect["condition"][1 : -len(suffix)]
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _without_bond(effect[branch])
        if "for_each" in effect:
            effect["for_each"]["do"] = _without_bond(effect["for_each"]["do"])
        if "observe_base_damage == true" in effect.get("condition", ""):
            suffix = " or lasting_state.has_spell_wards == true"
            if effect["condition"].endswith(suffix):
                effect["condition"] = effect["condition"][: -len(suffix)]
        if "condition" in effect and not effect.get("on_true") and not effect.get("on_false"):
            continue
        result.append(effect)
    return result


def _receiving_only(effects):
    """Keep damage-received consequences without another Bond or caster-dealt event."""
    result = []
    for original in _without_bond(effects):
        if original.get("call") in {
            *DEALT_CALLBACKS,
            "apply_srd_reflected_warding_bond_damage",
            "drain_srd_dream_wakes",
        }:
            continue
        effect = deepcopy(original)
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _receiving_only(effect[branch])
        if "for_each" in effect:
            effect["for_each"]["do"] = _receiving_only(effect["for_each"]["do"])
        if "condition" in effect and not effect.get("on_true") and not effect.get("on_false"):
            continue
        result.append(effect)
    return result


def _push(kind, amount):
    return {
        "list_push": {
            "list": "_bond_damage_components",
            "value": {"damage_type": kind, "amount": amount},
        }
    }


def _receiving_target(value):
    """Forward the resolved receiving actor's ID through inherited raw-target calls."""
    if isinstance(value, str):
        return "{_target_id}" if value == "{target}" else value
    if isinstance(value, list):
        return [_receiving_target(item) for item in value]
    if isinstance(value, dict):
        return {key: _receiving_target(item) for key, item in value.items()}
    return value


def _receiving_fortitude(effects):
    """Feed the inherited lethal save only the receiver's genuine mitigated Radiant receipt."""
    effects = deepcopy(effects)

    def edit(rows):
        for index, effect in enumerate(rows):
            if effect.get("call") == "resolve_srd_created_undead_fortitude":
                argument = effect["params"]["radiant_damage"].strip("{}")
                for prior in rows[:index]:
                    if prior.get("set", {}).get("field") == argument:
                        prior["set"]["value"] = "_bond_has_radiant_damage"
                        break
                else:
                    effect["params"]["radiant_damage"] = "{_bond_has_radiant_damage}"
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    edit(effect[branch])
            if "for_each" in effect:
                edit(effect["for_each"]["do"])

    edit(effects)
    return effects


def _receiving_start(effects):
    """Select a supported complete receiving boundary, never an arbitrary nested marker."""
    flat = [
        i for i, effect in enumerate(effects) if effect.get("set", {}).get("field") == "_hp_before"
    ]
    wrapped = [
        i
        for i, effect in enumerate(effects)
        if effect.get("set", {}).get("field") == "_tp_received_object"
    ]
    if len(flat) + len(wrapped) != 1:
        raise ValueError("Warding Bond needs one supported complete receiving boundary.")
    if flat:
        start = flat[0]
        if effects[start] != _set("_hp_before", "target.hp"):
            raise ValueError("Warding Bond needs the native flat HP initializer.")
        return start
    start = wrapped[0]
    initializer = _set(
        "_tp_received_object",
        "target.true_polymorph_object_source_id != null and target.is_object == true",
    )
    if effects[start] != initializer or start + 1 >= len(effects):
        raise ValueError("Warding Bond needs the actual True Polymorph initializer.")
    receiving = effects[start + 1]
    exports = (
        "_hp_before",
        "_hp_after",
        "_temp_hp_before",
        "_temp_hp_after",
        "_hp_lost",
        "_is_alive_after",
        "_object_destroyed",
    )
    object_call = _call(
        "receive_srd_true_polymorph_object_damage",
        {"target": "{target}", "amount": "{_effective_damage}"},
        exports,
    )
    ordinary = receiving.get("on_false", [])
    if (
        set(receiving) != {"condition", "on_true", "on_false"}
        or receiving.get("condition") != "_tp_received_object == true"
        or receiving.get("on_true") != [object_call]
        or not ordinary
        or ordinary[0] != _set("_hp_before", "target.hp")
        or sum(e.get("set", {}).get("field") == "_hp_before" for e in ordinary) != 1
    ):
        raise ValueError("Warding Bond needs both exact True Polymorph receiving branches.")
    # Keep initializer, complete conditional and every later receiving effect together.
    return start


def _reflection_tool(tools):
    """Mitigate the saved post-recipient types, then reuse one unchanged composite HP suffix."""
    owner = tools["apply_srd_composite_damage"]
    first = next(
        i
        for i, e in enumerate(owner["effects"])
        if e.get("set", {}).get("field") == "_component_1_effective_damage"
    )
    hp = _receiving_start(owner["effects"])
    kinds = next(
        p["enum_values"] for p in owner["parameters"] if p["name"] == "component_1_damage_type"
    )
    # Recast-on-either makes connected pairs disjoint; this receiving leaf also has no reflector.
    effects = [
        _set("_bond_reflection_source", "state.spell_ward_sources[bond_source_id]"),
        _require(
            "_bond_reflection_source != null and _bond_reflection_source.mode == 'bond' "
            "and _bond_reflection_source.caster_state_id == _target_id",
            "Select the actual captured Bond source and its caster.",
        ),
        _set("source_state_id", "_target_id"),
        _set("critical_hit", False),
        _set("witness_state_ids", None),
        *deepcopy(owner["effects"][:first]),
        _set("_incoming_damage", 0),
        _set("_effective_damage", 0),
        _set("_bond_radiant_damage", 0),
        _set("_bond_damage_types", []),
        {
            "for_each": {
                "collection": "damage_components",
                "as": "_bond_component",
                "do": [
                    _when(
                        "_bond_component.amount > 0",
                        [
                            _when(
                                "_bond_component.damage_type not in _bond_damage_types",
                                [
                                    {
                                        "list_push": {
                                            "list": "_bond_damage_types",
                                            "value": "_bond_component.damage_type",
                                        }
                                    }
                                ],
                            ),
                        ],
                    )
                ],
            }
        },
        {
            "for_each": {
                "collection": "_bond_damage_types",
                "as": "_bond_damage_type",
                "do": [
                    _set("_bond_current_amount", 0),
                    {
                        "for_each": {
                            "collection": "damage_components",
                            "as": "_bond_component",
                            "do": [
                                _when(
                                    "_bond_component.damage_type == _bond_damage_type",
                                    [
                                        _set(
                                            "_bond_current_amount",
                                            "_bond_current_amount + _bond_component.amount",
                                        )
                                    ],
                                )
                            ],
                        }
                    },
                    *typed_component_mitigation(
                        tools, "_bond_", "_bond_current_amount", "_bond_damage_type"
                    ),
                    _when(
                        "_bond_damage_type == 'radiant'",
                        [
                            _set(
                                "_bond_radiant_damage",
                                "_bond_radiant_damage + _bond_effective_damage",
                            )
                        ],
                    ),
                    _set("_incoming_damage", "_incoming_damage + _bond_current_amount"),
                    _set("_effective_damage", "_effective_damage + _bond_effective_damage"),
                ],
            }
        },
        _set("_bond_has_radiant_damage", "_bond_radiant_damage > 0"),
        *_receiving_fortitude(_receiving_only(owner["effects"][hp:])),
    ]
    return _tool(
        "Receive an actual saved Warding Bond packet without another roll or dealt-damage event.",
        [
            _entity("target"),
            _parameter("bond_source_id"),
            _parameter(
                "damage_components",
                "array",
                min_items=1,
                max_items=5,
                items={
                    "type": "object",
                    "properties": {
                        "damage_type": {"type": "string", "enum": kinds},
                        "amount": {"type": "integer", "minimum": 0},
                    },
                    "required": ["damage_type", "amount"],
                    "additionalProperties": False,
                },
            ),
        ],
        _receiving_target(effects),
        [
            {
                "condition": "target.is_alive == true",
                "error": "The actual Bond caster must be alive to receive damage.",
            }
        ],
    )


def _receipt(tool, composite, qualified):
    """Retain actual type amounts while every successful optional producer still owns its locals."""
    effects = [_set("_bond_damage_components", [])]
    if composite:
        effects += [
            _push("component_1_damage_type", "_component_1_effective_damage"),
            _push("component_2_damage_type", "_component_2_effective_damage"),
        ]
    else:
        effects.append(_set("_bond_base_damage", "_effective_damage"))
    if qualified:
        branch = next(
            e for e in tool["effects"] if "observe_base_damage == true" in e.get("condition", "")
        )
        minor = next(
            e for e in branch["on_true"] if "roll_srd_conjure_minor_damage_component" in str(e)
        )
        curse = next(e for e in branch["on_true"] if "roll_srd_curse_damage_component" in str(e))
        for source, active, kind, amount in (
            (
                minor,
                "_conjure_minor_qualifies == true",
                "_conjure_minor_type",
                "_minor_effective_damage",
            ),
            (curse, "_curse_extra_amount > 0", "'necrotic'", "_curse_effective_damage"),
        ):
            component = [_push(kind, amount)]
            if not composite:
                component.insert(0, _set("_bond_base_damage", f"_bond_base_damage - {amount}"))
            effects.append(_when(source["condition"], [_when(active, component)]))
    if not composite:
        effects.append(_push("damage_type", "_bond_base_damage"))
    return effects


def _source_receipt(tool, composite, qualified):
    return [
        _set("_bond_pending_reflection", False),
        _call(
            "check_srd_warding_bond_damage",
            {"target_state": "{target}"},
            ("_ward_bond_effective", "_warding_bond_source_id", "_warding_bond_caster_state_id"),
        ),
        _when(
            "_ward_bond_effective == true",
            [
                _set("_bond_receipt_source_id", "_warding_bond_source_id"),
                _set("_bond_receipt_caster_id", "_warding_bond_caster_state_id"),
                *_receipt(tool, composite, qualified),
                _set("_bond_pending_reflection", True),
            ],
        ),
    ]


def compose_warding_bond_damage(tools, changed):
    """Fold receipt/reflection into existing qualifier and completed observer boundaries."""
    if "check_srd_warding_bond_damage" not in tools:
        return
    changed["apply_srd_reflected_warding_bond_damage"] = _reflection_tool(tools)
    for name in (
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
    ):
        tool = deepcopy(changed.get(name, tools[name]))
        tool["effects"] = _without_bond(tool["effects"])
        qualified = "qualifying" in name
        composite = "composite" in name
        if qualified:
            branch = next(
                e
                for e in tool["effects"]
                if "observe_base_damage == true" in e.get("condition", "")
            )
            branch["condition"] += " or lasting_state.has_spell_wards == true"
            branch["on_true"].append(
                _when(
                    "lasting_state.has_spell_wards == true and _effective_damage > 0",
                    _source_receipt(tool, composite, True),
                )
            )
        after = next(
            e for e in reversed(tool["effects"]) if "end_srd_invisibility_damage_source" in str(e)
        )
        after["condition"] = f"({after['condition']}) or ({BOND_OBSERVER})"
        body = [] if qualified else _source_receipt(tool, composite, False)
        body.append(
            _when(
                "_bond_pending_reflection == true",
                [
                    _call(
                        "apply_srd_reflected_warding_bond_damage",
                        {
                            "target": "{_bond_receipt_caster_id}",
                            "bond_source_id": "{_bond_receipt_source_id}",
                            "damage_components": "{_bond_damage_components}",
                        },
                    )
                ],
            )
        )
        after["on_true"].append(_when(BOND_OBSERVER, body))
        changed[name] = tool


def bind_runtime_consumers(text):
    """Refresh the receiving lifecycle after final Fortitude, Dream and body-source consumers."""
    tools = _load_runtime(text)["tools"]
    if "check_srd_warding_bond_damage" not in tools:
        return text
    text = text.replace("procedures: {}\r\n", "procedures: {}\n", 1)
    return _emit(text, {"apply_srd_reflected_warding_bond_damage": _reflection_tool(tools)})
