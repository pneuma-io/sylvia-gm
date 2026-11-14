"""Compose shared damage clauses after spell families emit their owning leaves."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit, _entity, _parameter, _set, _tool, _when
    from .srd_attack_damage_components import (
        compose_attack_roll_allocations,
        compose_minor_attack_damage,
        typed_component_mitigation,
    )
    from .srd_spell_damage_roll_boundaries import compose_spell_damage_roll_boundaries
    from .srd_spell_light_damage_riders import compose_light_damage_riders
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
    from .srd_warding_bond_damage import compose_warding_bond_damage
else:
    from srd_actor_spell_modifiers import _call, _emit, _entity, _parameter, _set, _tool, _when
    from srd_attack_damage_components import (
        compose_attack_roll_allocations,
        compose_minor_attack_damage,
        typed_component_mitigation,
    )
    from srd_spell_damage_roll_boundaries import compose_spell_damage_roll_boundaries
    from srd_spell_light_damage_riders import compose_light_damage_riders
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_senses import _replace_tools
    from srd_warding_bond_damage import compose_warding_bond_damage


def _effect_lists(effects):
    """Visit actual damage outcomes, retaining their branch and target-loop ownership."""
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                yield from _effect_lists(effect[branch])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def _mind_spike_penalty(tool):
    """Refresh Enfeeblement's source and modify the raw roll before a save halves it."""
    if "_mind_spike_enfeeblement_damage" in str(tool["effects"]):
        return False
    for effects in _effect_lists(tool["effects"]):
        for index, effect in enumerate(effects):
            if effect.get("store_as") != "_mind_spike_damage" or "dice" not in effect:
                continue
            effects.insert(
                index + 1,
                _when(
                    "caster_state.has_actor_spell_modifiers == true",
                    [
                        _call(
                            "modify_srd_actor_damage_roll",
                            {
                                "actor_state": "{_caster_state_id}",
                                "lasting_state": "state",
                                "amount": "{_mind_spike_damage.total}",
                            },
                            {"_mind_spike_enfeeblement_damage": "_actor_modified_damage"},
                        ),
                        _set(
                            "_mind_spike_damage",
                            {
                                "count": "_mind_spike_damage.count",
                                "sides": "_mind_spike_damage.sides",
                                "results": "_mind_spike_damage.results",
                                "modifier": "_mind_spike_damage.modifier",
                                "total": "_mind_spike_enfeeblement_damage",
                            },
                        ),
                    ],
                ),
            )
            return True
    raise SystemExit("Mind Spike's owning raw damage roll is absent")


def _curse_outcomes(tool):
    """Attach one exact-source curse rider after each qualifying target damage outcome."""
    if "apply_srd_curse_damage_rider" in str(tool["effects"]):
        return False
    attached = False
    for effects in _effect_lists(tool["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") not in {"apply_srd_damage", "apply_srd_composite_damage"}:
                continue
            source = effect["params"].get("source_state_id")
            target = effect["params"].get("target")
            if source is None or target is None:
                raise SystemExit("Qualifying spell damage must retain its source and target")
            effect.setdefault("export_context", {}).update(
                _spell_consumer_damage="_effective_damage",
                _spell_consumer_alive="_is_alive_after",
            )
            effects.insert(
                index + 1,
                _when(
                    "state.has_affliction_damage_curses == true and "
                    "_spell_consumer_damage > 0 and _spell_consumer_alive == true",
                    [
                        _call(
                            "apply_srd_curse_damage_rider",
                            {"target_state": target, "caster_state": source},
                        )
                    ],
                ),
            )
            attached = True
    if not attached:
        raise SystemExit("Qualifying spell's owning damage outcome is absent")
    return True


def _contact_other_plane_damage(tool):
    """Compose the caster's actual Contact failure roll and source-owned self packet."""
    touched = False
    for effects in _effect_lists(tool["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if (
                effect.get("store_as") == "_contact_damage"
                and "dice" in effect
                and not any(
                    "_contact_enfeeblement_damage" in str(item) for item in effects[index + 1 :]
                )
            ):
                effects.insert(
                    index + 1,
                    _when(
                        "caster_state.has_actor_spell_modifiers == true",
                        [
                            _call(
                                "modify_srd_actor_damage_roll",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "lasting_state": "state",
                                    "amount": "{_contact_damage.total}",
                                },
                                {"_contact_enfeeblement_damage": "_actor_modified_damage"},
                            ),
                            _set(
                                "_contact_damage",
                                {
                                    "count": "_contact_damage.count",
                                    "sides": "_contact_damage.sides",
                                    "results": "_contact_damage.results",
                                    "modifier": "_contact_damage.modifier",
                                    "total": "_contact_enfeeblement_damage",
                                },
                            ),
                        ],
                    ),
                )
                touched = True
            if (
                effect.get("call") == "apply_srd_damage"
                and effect.get("params", {}).get("amount") == "{_contact_damage.total}"
            ):
                effect["call"] = "apply_srd_qualifying_damage"
                touched = True
    return touched


def _curse_roll_tool(tools):
    """Select a refreshed exact caster source and roll one nonstacking damage component."""
    tool = deepcopy(tools["apply_srd_curse_damage_rider"])
    tool["description"] = (
        "Roll the active caster-owned Curse component before the hit's life-cycle transition."
    )
    tool["parameters"].append(_parameter("critical_hit", "boolean", required=False, default=False))
    tool["effects"].insert(0, _set("_curse_extra_amount", 0))
    branch = next(
        e
        for e in tool["effects"]
        if "target_state.has_control_affliction_sources" in e.get("condition", "")
    )
    branch["on_true"].insert(
        0,
        _call(
            "refresh_srd_timed_condition_target",
            {
                "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}",
            },
        ),
    )
    for effects in _effect_lists(tool["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") == "apply_srd_damage":
                effects[index] = _set("_curse_extra_amount", "_curse_extra_roll.total")
            if effect.get("store_as") == "_curse_extra_roll":
                effect["dice"]["critical"] = "critical_hit"
            if "_affliction.curse_mode == 'damage'" in effect.get("condition", ""):
                effect["condition"] += (
                    " and _curse_extra_amount == 0 and _affliction.installed == true "
                    "and _affliction.suppressed != true"
                )
    return tool


def _necrotic_mitigation(tools):
    """Reuse the established typed component's defenses before the shared HP transition."""
    return typed_component_mitigation(tools, "_curse_", "_curse_extra_amount", "'necrotic'")


def _qualifying_damage_tool(tools, name):
    """Add the typed Curse component before one death, Temp HP and concentration event."""
    tool = deepcopy(tools[name])
    tool["parameters"].append(
        _parameter("unallocated_roll_penalty", "integer", required=False, default=0, minimum=0)
    )
    tool["parameters"] += [
        _parameter("damage_roll_divisor", "integer", required=False, default=1, enum_values=[1, 2]),
        _parameter(
            "damage_roll_base_remainder", "integer", required=False, default=0, minimum=0, maximum=1
        ),
        _parameter("game_damage_roll", "boolean", required=False, default=False),
        _parameter("observe_base_damage", "boolean", required=False, default=False),
    ]
    tool["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    tool["description"] = (
        "Resolve an actual attack or caster-owned spell damage packet "
        "including its active Curse component."
    )
    index = next(
        i for i, e in enumerate(tool["effects"]) if e.get("set", {}).get("field") == "_hp_before"
    )
    # Single damage's timed-defense reader is conditional; bind the component projection here too.
    extra = [
        _call(
            "roll_srd_curse_damage_component",
            {
                "target_state": "{target}",
                "caster_state": "{source_state_id}",
                "critical_hit": "{critical_hit}",
            },
            {"_curse_extra_amount": "_curse_extra_amount"},
        ),
        _set(
            "_curse_extra_amount",
            "(max(0, _curse_extra_amount - unallocated_roll_penalty) + "
            "damage_roll_base_remainder) // damage_roll_divisor",
        ),
        _when(
            "_curse_extra_amount > 0",
            [
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
                *_necrotic_mitigation(tools),
                _set("_incoming_damage", "_incoming_damage + _curse_extra_amount"),
                _set("_effective_damage", "_effective_damage + _curse_effective_damage"),
            ],
        ),
    ]
    curse_condition = (
        "(_incoming_damage > 0 or unallocated_roll_penalty > 0 or "
        "game_damage_roll == true) and "
        "state.has_affliction_damage_curses == true and "
        "source_state_id != null and len(source_state_id) > 0"
    )
    tool["effects"].insert(
        index,
        _when(
            f"observe_base_damage == true or ({curse_condition})",
            [
                _set("_source_base_effective_damage", "_effective_damage"),
                _when(curse_condition, extra),
            ],
        ),
    )
    return tool


def _clamped_roll_remainder(tools, changed):
    """Export the remainder from the same actual penalty die before its minimum-zero clamp."""
    tool = deepcopy(tools["modify_srd_actor_damage_roll"])
    if "_actor_unallocated_roll_penalty" in str(tool["effects"]):
        return
    tool["effects"].insert(0, _set("_actor_unallocated_roll_penalty", 0))
    for effects in _effect_lists(tool["effects"]):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("store_as") == "_enfeeblement_penalty_roll":
                effects.insert(
                    index + 1,
                    _set(
                        "_actor_unallocated_roll_penalty",
                        "max(0, _enfeeblement_penalty_roll.total - amount)",
                    ),
                )
    changed["modify_srd_actor_damage_roll"] = tool


def _saved_roll_remainders(tools, changed):
    """Carry actual Contact, Mind Spike and Fire Shield roll boundaries into one packet."""
    owners = (
        ("cast_spell_divination_spell", "_contact_damage", "_contact_roll_remainder", None),
        (
            "cast_srd_mind_spike",
            "_mind_spike_damage",
            "_mind_spike_roll_remainder",
            "_divination_save_success == true",
        ),
        (
            "resolve_srd_fire_shield_hit",
            "_fire_shield_damage_roll",
            "_fire_shield_roll_remainder",
            None,
        ),
    )
    for name, roll, remainder, halves in owners:
        tool = deepcopy(changed.get(name, tools[name]))
        touched = False
        initialized = any(
            e.get("set", {}).get("field") == remainder
            for effects in _effect_lists(tool["effects"])
            for e in effects
        )
        if not initialized:
            for effects in list(_effect_lists(tool["effects"])):
                for index in range(len(effects) - 1, -1, -1):
                    effect = effects[index]
                    if effect.get("store_as") == roll:
                        effects.insert(index, _set(remainder, 0))
                        touched = True
        for effects in list(_effect_lists(tool["effects"])):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                if (
                    effect.get("call") == "modify_srd_actor_damage_roll"
                    and effect.get("params", {}).get("amount") == "{" + roll + ".total}"
                ):
                    exports = effect.setdefault("export_context", {})
                    if remainder not in exports:
                        exports[remainder] = "_actor_unallocated_roll_penalty"
                        touched = True
                if (
                    name == "resolve_srd_fire_shield_hit"
                    and effect.get("call") == "modify_srd_actor_raw_damage_roll"
                    and not any(
                        e.get("set", {}).get("field") == remainder for e in effects[index + 1 :]
                    )
                ):
                    effects.insert(index + 1, _set(remainder, "max(0, -_ray_raw_damage)"))
                    touched = True
                if effect.get("call") == "apply_srd_qualifying_damage" and effect.get(
                    "params", {}
                ).get("amount") in {"{" + roll + ".total}", "{_mind_spike_amount}"}:
                    params = effect["params"]
                    if "game_damage_roll" not in params:
                        params.update(
                            unallocated_roll_penalty="{" + remainder + "}",
                            game_damage_roll=True,
                        )
                        if halves:
                            effects[index:index] = [
                                _set("_curse_damage_roll_divisor", 1),
                                _when(halves, [_set("_curse_damage_roll_divisor", 2)]),
                            ]
                            params["damage_roll_divisor"] = "{_curse_damage_roll_divisor}"
                            params["damage_roll_base_remainder"] = (
                                "{" + roll + ".total % _curse_damage_roll_divisor}"
                            )
                        touched = True
        if touched:
            changed[name] = tool


def _spell_attack_roll_remainder(tools, changed):
    """Carry the actual negative pre-clamp attack roll into its added Curse component."""
    tool = deepcopy(changed.get("cast_spell_attack_damage", tools["cast_spell_attack_damage"]))
    touched = False
    for effects in list(_effect_lists(tool["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get(
                "call"
            ) == "apply_srd_qualifying_damage" and "unallocated_roll_penalty" not in effect.get(
                "params", {}
            ):
                effects.insert(
                    index,
                    _set(
                        "_curse_unallocated_roll_penalty",
                        "max(0, -(_spell_damage_amount + flat_damage))",
                    ),
                )
                effect["params"]["unallocated_roll_penalty"] = "{_curse_unallocated_roll_penalty}"
                touched = True
    if touched:
        changed["cast_spell_attack_damage"] = tool


def _compose_curse_packets(tools, changed):
    """Replace legacy post-hit callbacks at their actual qualifying attack/spell owners."""
    changed["roll_srd_curse_damage_component"] = _curse_roll_tool(tools)
    for original, qualified in (
        ("apply_srd_damage", "apply_srd_qualifying_damage"),
        ("apply_srd_composite_damage", "apply_srd_qualifying_composite_damage"),
    ):
        changed[qualified] = _qualifying_damage_tool(tools, original)
    wrappers = {
        "apply_srd_magic_missile_damage",
        "apply_srd_divine_favor_weapon_damage",
        "apply_srd_form_attack_damage",
    }
    for name, original in tools.items():
        if name in {
            "apply_srd_curse_damage_rider",
            "roll_srd_curse_damage_component",
            "apply_srd_qualifying_damage",
            "apply_srd_qualifying_composite_damage",
        }:
            continue
        tool = deepcopy(changed.get(name, original))
        touched = False
        for effects in _effect_lists(tool["effects"]):
            for index in range(len(effects) - 1, -1, -1):
                effect = effects[index]
                callback = effect.get("call") == "apply_srd_curse_damage_rider" or (
                    "apply_srd_curse_damage_rider" in str(effect)
                    and len(effect.get("on_true", [])) == 1
                    and effect["on_true"][0].get("call") == "apply_srd_curse_damage_rider"
                )
                if callback:
                    owner_names = {
                        "apply_srd_damage",
                        "apply_srd_composite_damage",
                        "apply_srd_magic_missile_damage",
                        "apply_srd_divine_favor_weapon_damage",
                        "apply_srd_form_attack_damage",
                        "apply_srd_qualifying_damage",
                        "apply_srd_qualifying_composite_damage",
                    }
                    owner_index = next(
                        (
                            j
                            for j in range(index - 1, -1, -1)
                            if effects[j].get("call") in owner_names
                        ),
                        None,
                    )
                    if owner_index is None:
                        raise SystemExit(
                            f"Curse callback in {name} has lost its owning damage call"
                        )
                    # A family may install its saved failed-save rider between the damage
                    # and legacy callback; both still belong to this target's outcome.
                    damage = effects[owner_index]
                    if damage["call"] == "apply_srd_damage":
                        damage["call"] = "apply_srd_qualifying_damage"
                    elif damage["call"] == "apply_srd_composite_damage":
                        damage["call"] = "apply_srd_qualifying_composite_damage"
                    del effects[index]
                    touched = True
                elif name in wrappers and effect.get("call") in {
                    "apply_srd_damage",
                    "apply_srd_composite_damage",
                }:
                    effect["call"] = (
                        "apply_srd_qualifying_damage"
                        if effect["call"] == "apply_srd_damage"
                        else "apply_srd_qualifying_composite_damage"
                    )
                    touched = True
        if touched:
            changed[name] = tool


def _compose_invisibility_damage(tools, changed):
    """End source-owned ordinary Invisibility when its actor actually deals damage."""
    if "end_srd_invisibility_for_actor" not in tools:
        return
    changed["end_srd_invisibility_damage_source"] = _tool(
        "Resolve the actual damage source before ending its ordinary Invisibility.",
        [_entity("source_state")],
        [
            _when(
                "source_state.illumination_has_invisibility == true",
                [
                    _call(
                        "end_srd_invisibility_for_actor",
                        {"actor_state": "{_source_state_id}", "event": "damage"},
                    ),
                ],
            )
        ],
    )
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        tool = deepcopy(changed.get(name, tools[name]))
        for effects in _effect_lists(tool["effects"]):
            for effect in effects:
                if "end_srd_invisibility_damage_source" in str(
                    effect
                ) and "source_state_id != ''" in effect.get("condition", ""):
                    effect["condition"] = effect["condition"].replace(
                        "source_state_id != ''", "len(source_state_id) > 0"
                    )
        if "end_srd_invisibility_damage_source" not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    (
                        "state.has_illumination_spells == true and _effective_damage > 0 and "
                        "source_state_id != null and len(source_state_id) > 0"
                    ),
                    [
                        _call(
                            "end_srd_invisibility_damage_source",
                            {"source_state": "{source_state_id}"},
                        )
                    ],
                )
            )
        if tool != tools[name]:
            changed[name] = tool


def _compose_mislead_damage(tools, changed):
    """End only Mislead's exact Invisible grants after the source actually deals damage."""
    if "end_srd_mislead_damage_source" not in tools:
        return
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        tool = deepcopy(changed.get(name, tools[name]))
        if "end_srd_invisibility_for_actor" in tools:
            callbacks = {"end_srd_invisibility_damage_source", "end_srd_mislead_damage_source"}
            observer_calls = [
                {
                    nested.get("call")
                    for effects in _effect_lists([effect])
                    for nested in effects
                    if nested.get("call") is not None
                }
                for effect in tool["effects"]
            ]
            if any(callbacks < calls for calls in observer_calls):
                # An established completed-packet group already owns both dealt callbacks
                # alongside receiving observers. Keep that group through family regeneration.
                tool["effects"] = [
                    effect
                    for effect, calls in zip(tool["effects"], observer_calls, strict=True)
                    if not (
                        calls
                        and calls <= callbacks
                        and "_effective_damage > 0" in effect.get("condition", "")
                    )
                ]
                if tool != tools[name]:
                    changed[name] = tool
                continue
            retained = []
            for effect in tool["effects"]:
                calls = {
                    nested.get("call")
                    for effects in _effect_lists([effect])
                    for nested in effects
                    if nested.get("call") is not None
                }
                if (
                    calls
                    and calls <= callbacks
                    and "_effective_damage > 0" in effect.get("condition", "")
                ):
                    continue
                retained.append(effect)
            tool["effects"] = retained + [
                _when(
                    "_effective_damage > 0 and source_state_id != null and "
                    "len(source_state_id) > 0 and (state.has_illumination_spells == true "
                    "or state.has_illusion_spells == true)",
                    [
                        _when(
                            "state.has_illumination_spells == true",
                            [
                                _call(
                                    "end_srd_invisibility_damage_source",
                                    {"source_state": "{source_state_id}"},
                                )
                            ],
                        ),
                        _when(
                            "state.has_illusion_spells == true",
                            [
                                _call(
                                    "end_srd_mislead_damage_source",
                                    {"caster_state": "{source_state_id}"},
                                )
                            ],
                        ),
                    ],
                )
            ]
            if tool != tools[name]:
                changed[name] = tool
            continue
        if "end_srd_mislead_damage_source" not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    (
                        "state.has_illusion_spells == true and _effective_damage > 0 and "
                        "source_state_id != null and len(source_state_id) > 0"
                    ),
                    [
                        _call(
                            "end_srd_mislead_damage_source",
                            {"caster_state": "{source_state_id}"},
                        )
                    ],
                )
            )
        if tool != tools[name]:
            changed[name] = tool


def _compose_object_damage_source(tools, changed):
    """Observe only an already-hit object's actual persisted damage from its caster."""
    name = "resolve_srd_sorcerous_burst_object_attack"
    if name not in tools:
        return
    callbacks = []
    if "end_srd_invisibility_damage_source" in tools | changed:
        callbacks.append(
            _when(
                "lasting_state.has_illumination_spells == true",
                [
                    _call(
                        "end_srd_invisibility_damage_source",
                        {"source_state": "{_caster_state_id}"},
                    )
                ],
            )
        )
    if "end_srd_mislead_damage_source" in tools:
        callbacks.append(
            _when(
                "lasting_state.has_illusion_spells == true",
                [
                    _call(
                        "end_srd_mislead_damage_source",
                        {"caster_state": "{_caster_state_id}"},
                    )
                ],
            )
        )
    tool = deepcopy(tools[name])
    condition = (
        "_object_damage_applied > 0 and (lasting_state.has_illumination_spells == true "
        "or lasting_state.has_illusion_spells == true)"
    )
    tool["effects"] = [e for e in tool["effects"] if e.get("condition") != condition]
    if callbacks:
        tool["effects"].append(_when(condition, callbacks))
    changed[name] = tool


def extend_runtime(text):
    """Run before reaction continuations clone these corrected damage-producing leaves."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    # Damage only enters the rest owner when one of its exact consumers has work.
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        tool = deepcopy(tools[name])
        for effects in _effect_lists(tool["effects"]):
            for effect in effects:
                if (
                    effect.get("condition") == "_effective_damage > 0"
                    and len(effect.get("on_true", [])) == 1
                    and effect["on_true"][0].get("call") == "interrupt_srd_rest"
                ):
                    effect["condition"] = (
                        "_effective_damage > 0 and (target.spell_preparation_open == true or "
                        "target.short_rest_hit_dice_available == true or target.rest_phase == "
                        "'active' or target.rest_added_unconscious == true)"
                    )
        changed[name] = tool
    mind_spike = deepcopy(tools["cast_srd_mind_spike"])
    if _mind_spike_penalty(mind_spike):
        changed["cast_srd_mind_spike"] = mind_spike
    contact = deepcopy(tools["cast_spell_divination_spell"])
    if _contact_other_plane_damage(contact):
        changed["cast_spell_divination_spell"] = contact
    for name in (
        "resolve_spell_area_composite_damage_target",
        "cast_srd_mind_spike",
        "resolve_srd_fire_shield_hit",
    ):
        tool = deepcopy(changed.get(name, tools[name]))
        if "apply_srd_qualifying_" not in str(tool["effects"]) and _curse_outcomes(tool):
            changed[name] = tool
    _compose_invisibility_damage(tools, changed)
    _compose_mislead_damage(tools, changed)
    _compose_curse_packets(tools | changed, changed)
    _clamped_roll_remainder(tools | changed, changed)
    _saved_roll_remainders(tools | changed, changed)
    _spell_attack_roll_remainder(tools | changed, changed)
    compose_attack_roll_allocations(tools | changed, changed)
    compose_minor_attack_damage(tools | changed, changed)
    compose_spell_damage_roll_boundaries(tools | changed, changed)
    _compose_object_damage_source(tools | changed, changed)
    compose_light_damage_riders(tools | changed, changed)
    compose_warding_bond_damage(tools | changed, changed)
    # Spatial scene paths stay package-owned when forwarding choices through existing producers.
    spatial_tools = {
        name: changed.pop(name)
        for name in list(changed)
        if any(
            "spatial_query" in effect or "spatial_mutation" in effect
            for effects in _effect_lists(changed[name]["effects"])
            for effect in effects
        )
    }
    text = _emit(text, changed)
    if spatial_tools:
        # Normalize first insertion separators as well as replacement, without changing effects.
        return _replace_tools(_replace_tools(text, spatial_tools), spatial_tools)
    return text
