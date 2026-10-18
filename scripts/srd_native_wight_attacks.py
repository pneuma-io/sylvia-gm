"""Printed Wight weapons, mixed Multiattack and live scene Sunlight Sensitivity."""

from copy import deepcopy

if __package__:
    from .srd_attack_damage_components import _printed_component
    from .srd_native_wight_actions import WIGHT_BLOCK, _validate_source, _wight_body_tool
    from .srd_spell_summoning import _choose, _created_undead_additional_profiles, _walk
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
    from .srd_timed_senses import _loop, _optional, _replace_tools
else:
    from srd_attack_damage_components import _printed_component
    from srd_native_wight_actions import WIGHT_BLOCK, _validate_source, _wight_body_tool
    from srd_spell_summoning import _choose, _created_undead_additional_profiles, _walk
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
    from srd_timed_senses import _loop, _optional, _replace_tools

NATIVE = (
    "actor.created_spell_id == 'create_undead' and actor.created_form_id == 'wight' "
    "and actor.form_attack_in_progress != true"
)
WEAPONS = ("Necrotic Sword", "Necrotic Bow")


def _sunlight_tools():
    """Read actual current sunlight facts; no time, brightness or creation-origin inference."""
    fact = _tool(
        "Validate an actual admitted categorical natural-sunlight fact.",
        [_parameter("fact", "boolean")],
        [_set("_wight_natural_fact", "fact")],
    )
    sunlight = _tool(
        "Qualify this actual native Wight's current point against live sunlight sources/facts.",
        [_entity("actor_state"), _optional("lasting_state", resolve="entity", default="state")],
        [
            _call("qualify_srd_native_wight_body", {"actor_state": "{_actor_state_id}"}),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_actor_state_id}"},
                ("_info_scene_entity", "_info_position"),
            ),
            _require(
                "_info_scene_entity.metadata.plane != null and "
                "_info_scene_entity.metadata.lighting != null",
                "Sunlight Sensitivity needs the actual actor's admitted plane and lighting.",
            ),
            _set("_wight_sunlight_origin", "_info_position"),
            _set("_wight_sunlight_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "resolve_srd_information_light",
                {
                    "point": "{_wight_sunlight_origin}",
                    "plane": "{_wight_sunlight_plane}",
                    "ambient": "{_info_scene_entity.metadata.lighting}",
                    "lasting_state": "{_lasting_state_id}",
                },
                ("_information_sunlight",),
            ),
            _set("_wight_sunlight", "_information_sunlight"),
            _when(
                "_wight_sunlight != true",
                [
                    _set("_wight_zone_sunlight", None),
                    _set("_wight_sunlight_zones", "lasting_state.spatial_scene.zones"),
                    _loop(
                        "_wight_sunlight_zones",
                        "_wight_sunlight_zone",
                        [
                            _when(
                                "_wight_sunlight_zone.metadata.active != false and "
                                "_wight_sunlight_zone.metadata.plane == _wight_sunlight_plane and "
                                "_wight_sunlight_zone.metadata.natural_sunlight != null",
                                [
                                    _call(
                                        "srd_information_segment_interval",
                                        {
                                            "start": "{_wight_sunlight_origin}",
                                            "end": "{_wight_sunlight_origin}",
                                            "volume": "{_wight_sunlight_zone.shape}",
                                        },
                                        ("_segment_hit",),
                                    ),
                                    _when(
                                        "_segment_hit == true",
                                        [
                                            _call(
                                                "validate_srd_native_wight_sunlight_fact",
                                                {
                                                    "fact": "{_wight_sunlight_zone.metadata."
                                                    "natural_sunlight}",
                                                },
                                                ("_wight_natural_fact",),
                                            ),
                                            _require(
                                                "_wight_zone_sunlight == null or "
                                                "_wight_zone_sunlight == _wight_natural_fact",
                                                "Containing same-plane zones disagree on "
                                                "natural sunlight.",
                                            ),
                                            _set("_wight_zone_sunlight", "_wight_natural_fact"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _require(
                        "_wight_zone_sunlight != null",
                        "Admit actual natural sunlight in a containing same-plane zone; "
                        "brightness and world time do not decide it.",
                    ),
                    _set("_wight_sunlight", "_wight_zone_sunlight"),
                ],
            ),
        ],
    )
    return {
        "validate_srd_native_wight_sunlight_fact": fact,
        "qualify_srd_native_wight_sunlight": sunlight,
    }


def _spend_tool():
    """Retain one actual action's two shared weapon uses and at most one Life Drain."""
    return _tool(
        "Spend the authentic Wight action's mixed native attack allowance.",
        [
            _entity("actor"),
            _parameter("attack_name", "enum", enum_values=[*WEAPONS, "Life Drain"]),
            _optional("use_multiattack", "boolean", False),
        ],
        [
            _call("qualify_srd_native_wight_weapon_action", {"actor_state": "{_actor_id}"}),
            _when(
                "combat.attacks_remaining == null or combat.attacks_remaining == 0",
                [
                    _call(
                        "spend_srd_action", {"magic_action": False, "control_action_kind": "attack"}
                    ),
                    _set("combat.attacks_remaining", 1),
                    _set("combat.multiattack_remaining", []),
                    _set("combat.wight_multiattack_active", "use_multiattack"),
                    _set("combat.wight_life_drain_used", False),
                    _when("use_multiattack == true", [_set("combat.attacks_remaining", 2)]),
                ],
            ),
            _require(
                "use_multiattack == combat.wight_multiattack_active",
                "Finish this same printed Wight attack action.",
            ),
            _when(
                "attack_name == 'Life Drain'",
                [
                    _require(
                        "combat.wight_life_drain_used != true",
                        "Only one weapon attack can be replaced by Life Drain.",
                    ),
                    _set("combat.wight_life_drain_used", True),
                ],
            ),
            _set("combat.attacks_remaining", "combat.attacks_remaining - 1"),
            _call("refresh_srd_attack_availability", {}),
            _call("finish_srd_control_attack", {"actor_state": "{_actor_id}"}),
        ],
    )


def _weapon_damage(tools):
    """Roll the printed second die and delegate one whole typed packet to its real owner."""
    profiles = _created_undead_additional_profiles()["wight"]["attacks"]
    for name in WEAPONS:
        assert profiles[name]["extra_damage_expression"] == "d8"
        assert profiles[name]["extra_damage_type"] == "necrotic"
    result = _tool(
        "Receive the source Wight weapon's actual primary and Necrotic components.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _optional("lasting_state", resolve="entity", default="state"),
            _parameter("attack_name", "enum", enum_values=list(WEAPONS)),
            _parameter("amount", "integer"),
            _parameter("critical_hit", "boolean"),
            _optional("adjustment", "integer", 0),
            _optional("witness_state_ids", "array", None, items={"type": "string"}),
            _optional("unallocated_roll_penalty", "integer", 0, minimum=0),
            _optional(
                "minor_elementals_damage_type",
                "enum",
                "fire",
                enum_values=["acid", "cold", "fire", "lightning"],
            ),
        ],
        [
            _call("qualify_srd_native_wight_body", {"actor_state": "{_actor_state_id}"}),
            _set("_wight_weapon_type", "'slashing'"),
            _when("attack_name == 'Necrotic Bow'", [_set("_wight_weapon_type", "'piercing'")]),
            {
                "dice": {"expression": "d8", "critical": "critical_hit"},
                "store_as": "_wight_weapon_necrotic_roll",
            },
            _set("_wight_weapon_necrotic_amount", "_wight_weapon_necrotic_roll.total"),
            _set("_wight_weapon_necrotic_amount", "max(0, _wight_weapon_necrotic_amount)"),
            _set("_wight_weapon_primary_amount", "max(0, amount + adjustment)"),
            _call(
                "apply_srd_qualifying_composite_damage",
                {
                    "target": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "component_1_amount": "{_wight_weapon_primary_amount}",
                    "component_1_damage_type": "{_wight_weapon_type}",
                    "component_2_amount": "{_wight_weapon_necrotic_amount}",
                    "component_2_damage_type": "necrotic",
                    "source_state_id": "{_actor_state_id}",
                    "critical_hit": "{critical_hit}",
                    "game_damage_roll": True,
                    "actual_attack_hit": True,
                    "observe_attack_components": True,
                    "witness_state_ids": "{witness_state_ids}",
                    "minor_elementals_damage_type": "{minor_elementals_damage_type}",
                },
            ),
            _set("_hp_lost", "max(0, _hp_before - _hp_after)"),
        ],
    )
    _printed_component(result, "_wight_weapon_necrotic_amount", "0", "_wight_weapon_roll_carry")
    exports = {
        name
        for e in _walk(tools["resolve_adjudicated_creature_attack"]["effects"])
        if e.get("call") == "apply_srd_qualifying_damage"
        for name in e.get("export_context", {}).values()
    } - {"_hp_lost"}
    exports.update(
        (
            "_hp_before",
            "_hp_after",
            "_component_1_effective_damage",
            "_component_2_effective_damage",
        )
    )
    call = next(
        e for e in result["effects"] if e.get("call") == "apply_srd_qualifying_composite_damage"
    )
    call["export_context"] = {name: name for name in sorted(exports)}
    return result


def _ability_check(tools):
    """Derive the native body's current ability or printed skill total before the real D20 owner."""
    exports = tools["unbound_d20_test"]["effects"][0]["export_context"]
    result = _tool(
        "Resolve this actual Wight's native ability check from saved source statistics.",
        [
            _entity("actor_state"),
            _parameter(
                "ability",
                "enum",
                enum_values=[
                    "strength",
                    "dexterity",
                    "constitution",
                    "intelligence",
                    "wisdom",
                    "charisma",
                ],
            ),
            _optional("skill", "enum", None, enum_values=["perception", "stealth"]),
            _parameter("target_number", "integer"),
            _optional("advantage", "boolean", False),
            _optional("disadvantage", "boolean", False),
        ],
        [
            _call("qualify_srd_native_wight_body", {"actor_state": "{_actor_state_id}"}),
            _require(
                "skill == null or (skill == 'perception' and ability == 'wisdom') or "
                "(skill == 'stealth' and ability == 'dexterity')",
                "Use the actual printed native skill's ability.",
            ),
            _set("_wight_check_modifier", "(actor_state.ability_scores[ability] - 10) // 2"),
            _when(
                "skill == 'perception'",
                [
                    _require(
                        "actor_state.skill_check_modifiers.perception == 3",
                        "Retain Wight's printed Perception proficiency.",
                    ),
                    _set("_wight_check_modifier", "_wight_check_modifier + 2"),
                ],
            ),
            _when(
                "skill == 'stealth'",
                [
                    _require(
                        "actor_state.skill_check_modifiers.stealth == 4",
                        "Retain Wight's printed Stealth proficiency.",
                    ),
                    _set("_wight_check_modifier", "_wight_check_modifier + 2"),
                ],
            ),
            _call(
                "d20_test",
                {
                    "actor": None,
                    "actor_state": "{_actor_state_id}",
                    "kind": "ability_check",
                    "ability": "{ability}",
                    "skill": "{skill}",
                    "ability_modifier": "{_wight_check_modifier}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{target_number}",
                    "advantage": "{advantage}",
                    "disadvantage": "{disadvantage}",
                },
            ),
        ],
    )
    result["effects"][-1]["export_context"] = deepcopy(exports)
    result["metadata"]["result_card"] = deepcopy(
        tools["unbound_d20_test"]["metadata"]["result_card"]
    )
    return result


def extend_runtime(text):
    """Add only the native fixed-body routines alongside the separately owned Life Drain."""
    _validate_source()
    assert _created_undead_additional_profiles()["wight"]["source_stat_block_sha256"] == WIGHT_BLOCK
    tools = _load_runtime(text)["tools"]
    changed = _sunlight_tools() | {
        "qualify_srd_native_wight_weapon_action": _wight_body_tool(True),
        "spend_srd_native_wight_attack": _spend_tool(),
        "apply_srd_native_wight_weapon_damage": _weapon_damage(tools),
        "check_srd_native_wight_ability": _ability_check(tools),
    }
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def bind_native_wight_attack_consumers(text):
    """Bind final real action/roll owners; shared save and packet kernels remain unchanged."""
    tools = _load_runtime(text)["tools"]
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    if "qualify_srd_native_wight_weapon_action" in str(attack["effects"]):
        return text
    guard = [
        p
        for p in attack["pre_conditions"]
        if p["error"] == "The selected created Undead native attack routine remains unsupported."
    ]
    if len(guard) != 1:
        raise ValueError("Preserve the actual closed native Undead attack boundary.")
    guard[0]["condition"] += " or actor.created_form_id == 'wight'"
    attack["pre_conditions"].append(
        {
            "condition": f"not ({NATIVE}) or attack_name == 'Necrotic Sword' or "
            "attack_name == 'Necrotic Bow'",
            "error": "Select a printed native Wight weapon attack.",
        }
    )
    for name, row in _created_undead_additional_profiles()["wight"]["attacks"].items():
        attack["pre_conditions"].append(
            {
                "condition": f"not ({NATIVE}) or attack_name != '{name}' or ("
                f"attack_modifier == {row['attack_modifier']} and "
                f"damage_expression == '{row['damage_expression']}' and "
                f"damage_modifier == {row['damage_modifier']} and "
                f"damage_type == '{row['damage_type']}' and "
                f"attack_mode == '{row['kind']}' and "
                f"normal_range == {row['normal_range']} and "
                f"long_range == {row['long_range']} and attack_uses_weapon == true)",
                "error": "Use this Wight's exact printed native weapon attack profile.",
            }
        )
    attack["effects"].insert(
        0,
        _when(
            NATIVE,
            [
                _call("qualify_srd_native_wight_weapon_action", {"actor_state": "{_actor_id}"}),
                _require(
                    "actor.spell_divine_favor != true",
                    "The Wight's secondary weapon component with Divine Favor remains unsupported.",
                ),
                _call(
                    "resolve_srd_scene_relation",
                    {
                        "actor_state": "{_actor_id}",
                        "target_state": "{_target_id}",
                    },
                    ("_scene_can_see",),
                ),
                _require(
                    "actor_can_see_target == _scene_can_see",
                    "Use the native actor's actual admitted sight relation.",
                ),
            ],
        ),
    )
    roll_modes = [
        i
        for i, e in enumerate(attack["effects"])
        if e.get("set", {}).get("field") == "_effective_advantage"
    ]
    if len(roll_modes) != 1:
        raise ValueError("Bind Sunlight Sensitivity before the actual attack roll mode.")
    attack["effects"].insert(
        roll_modes[0],
        _when(
            NATIVE,
            [
                _call(
                    "qualify_srd_native_wight_sunlight",
                    {"actor_state": "{_actor_id}"},
                    ("_wight_sunlight",),
                ),
                _when("_wight_sunlight == true", [_set("_condition_disadvantage", True)]),
            ],
        ),
    )
    candidates = [
        e
        for e in _walk(attack["effects"])
        if e.get("call") == "apply_srd_qualifying_damage"
        and "game_damage_roll" in e.get("params", {})
    ]
    if not candidates:
        raise ValueError("Bind only the actual received native hit packet calls.")
    for effect in candidates:
        original = deepcopy(effect)
        params = original["params"]
        call = _call(
            "apply_srd_native_wight_weapon_damage",
            {
                "actor_state": "{_actor_id}",
                "target_state": "{_target_id}",
                "attack_name": "{attack_name}",
                "amount": params["amount"],
                "adjustment": params.get("adjustment", 0),
                "critical_hit": params["critical_hit"],
                "unallocated_roll_penalty": params.get("unallocated_roll_penalty", 0),
                "witness_state_ids": params.get("witness_state_ids", "{witness_state_ids}"),
                "minor_elementals_damage_type": "{minor_elementals_damage_type}",
            },
        )
        call["export_context"] = deepcopy(original.get("export_context", {}))
        call["export_context"].update(
            {
                name: name
                for name in (
                    "_wight_weapon_necrotic_roll",
                    "_wight_weapon_necrotic_amount",
                    "_component_1_effective_damage",
                    "_component_2_effective_damage",
                )
            }
        )
        effect.clear()
        effect.update(_choose(NATIVE, [call], [original]))
    attack["metadata"]["result_card"]["details"] += [
        {
            "label": "Weapon Necrotic dice",
            "value": {"path": "context._wight_weapon_necrotic_roll.results"},
            "optional": True,
        },
        {
            "label": "Weapon Necrotic before defenses",
            "value": {"path": "context._wight_weapon_necrotic_amount"},
            "optional": True,
        },
    ]
    spend = deepcopy(tools["spend_srd_creature_attack"])
    spend["effects"] = [
        _choose(
            NATIVE,
            [
                _call(
                    "spend_srd_native_wight_attack",
                    {
                        "actor": "{_actor_id}",
                        "attack_name": "{attack_name}",
                        "use_multiattack": "{use_multiattack}",
                    },
                ),
            ],
            spend["effects"],
        )
    ]
    drain = deepcopy(tools["use_srd_native_wight_life_drain"])
    drain["description"] = (
        "Use native Life Drain alone or replace one actual Multiattack weapon use."
    )
    drain["parameters"].append(_optional("use_multiattack", "boolean", False))
    spends = [e for e in drain["effects"] if e.get("call") == "spend_srd_action"]
    if len(spends) != 1 or spends[0]["params"] != {"magic_action": False}:
        raise ValueError("Preserve Life Drain's genuine standalone action boundary.")
    spends[0].update(
        call="spend_srd_creature_attack",
        params={
            "actor": "{_actor_state_id}",
            "attack_name": "Life Drain",
            "use_multiattack": "{use_multiattack}",
        },
    )
    d20 = deepcopy(tools["d20_test"])
    branches = [
        e
        for e in _walk(d20["effects"])
        if e.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
    ]
    if len(branches) != 1:
        raise ValueError("Reuse the actual non-save D20 condition branch without save overhead.")
    branches[0]["on_true"].append(
        _when(
            "actor_state.created_spell_id == 'create_undead' and "
            "actor_state.created_form_id == 'wight' and "
            "actor_state.form_statistics_replaced != true",
            [
                _call(
                    "qualify_srd_native_wight_sunlight",
                    {"actor_state": "{_actor_state_id}"},
                    ("_wight_sunlight",),
                ),
                _when("_wight_sunlight == true", [_set("_condition_disadvantage", True)]),
            ],
        )
    )
    finish = deepcopy(tools["finish_srd_wight_source_actor"])
    finish["effects"].append(
        _call(
            "record_srd_creature_skill_checks",
            {
                "target_state": "{_actor_state_id}",
                "skill_checks": [
                    {"skill": "perception", "modifier": 3},
                    {"skill": "stealth", "modifier": 4},
                ],
            },
        )
    )
    return _replace_tools(
        text,
        {
            "resolve_adjudicated_creature_attack": attack,
            "spend_srd_creature_attack": spend,
            "use_srd_native_wight_life_drain": drain,
            "d20_test": d20,
            "finish_srd_wight_source_actor": finish,
        },
    )
