"""Printed Rotting Fist uses the final typed receiver and its genuine death boundary."""

from copy import deepcopy

if __package__:
    from .srd_attack_damage_components import _printed_component
    from .srd_native_mummy_actions import _native_body_tool
    from .srd_native_receiving_boundary import native_receiving_boundary
    from .srd_native_undead_curses import MUMMY_BLOCK, _validate_sources
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
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_attack_damage_components import _printed_component
    from srd_native_mummy_actions import _native_body_tool
    from srd_native_receiving_boundary import native_receiving_boundary
    from srd_native_undead_curses import MUMMY_BLOCK, _validate_sources
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
    from srd_timed_senses import _optional, _replace_tools


def _actual_composite_body(tools, *, composite=True):
    """Expand only the actual shared packet's exact presentation delegate when present."""
    owner = "apply_srd_qualifying_composite_damage" if composite else "apply_srd_qualifying_damage"
    core = (
        "execute_srd_qualified_composite_packet"
        if composite
        else "execute_srd_qualified_scalar_packet"
    )
    packet = deepcopy(tools[owner])
    if packet["effects"][0].get("call") == core:
        if __package__:
            from .srd_qualified_packet_implementations import _delegate, _expanded_core
        else:
            from srd_qualified_packet_implementations import _delegate, _expanded_core
        if packet["effects"][0] != _delegate(packet, core, True, [])["effects"][0]:
            raise ValueError("Restore only the actual qualified packet's exact named delegate.")
        packet["effects"] = _expanded_core(tools[core], True) + packet["effects"][1:]
    return packet


def _dust_effects():
    """Stamp the original physical body before actual death/Clone archives its provenance."""
    return [
        _set("_mummy_fist_dust", True),
        _set("target.body_available", False),
        _set("target.vital_parts_present", False),
        _when(
            "lasting_state.native_mummy_dust_bodies == null",
            [
                _set("lasting_state.native_mummy_dust_bodies", {}),
            ],
        ),
        _require(
            "len(lasting_state.native_mummy_dust_bodies) < 128 or "
            "_mummy_fist_target_body in lasting_state.native_mummy_dust_bodies",
            "Resolve retained native dust bodies before recording more.",
        ),
        _set(
            "lasting_state.native_mummy_dust_bodies.{_mummy_fist_target_body}",
            {
                "target": "_mummy_fist_target_id",
                "body_source_id": "_mummy_fist_target_body",
                "attacker": "lasting_state.timed_condition_sources["
                "_mummy_rot_source_id].native_body_source",
                "native_attack": "'Rotting Fist'",
                "native_body_block_sha256": repr(MUMMY_BLOCK),
                "tick": "lasting_state.lasting_tick",
            },
        ),
    ]


def _fist_packet_tool(tools):
    """Add only native curse and Dust to the passed completed receiving lifecycle."""
    packet = _actual_composite_body(tools)
    boundary = native_receiving_boundary(packet["effects"])
    effects = boundary.ordinary
    death = deepcopy(boundary.still_zero)
    # Both branches retain the original death callbacks; surviving Ward/Fortitude runs neither.
    boundary.still_zero[0:0] = [_when("_hp_before > 0 and _effective_damage > 0", _dust_effects())]
    monster_index = effects.index(boundary.monster_zero)
    boundary.massive_zero["condition"] += " and _mummy_fist_dust != true"
    effects.insert(
        monster_index + 1,
        _when(
            "target.creature_kind == 'character' and target.hp == 0 and "
            "target.is_alive == true and _hp_before > 0 and _effective_damage > 0 and "
            "target.astral_projection_role != 'form'",
            _dust_effects() + death,
        ),
    )
    effects[boundary.hp_before_index + 1 : boundary.hp_before_index + 1] = [
        _call(
            "install_srd_native_mummy_rot",
            {
                "actor_state": "{source_state_id}",
                "target_state": "{target}",
                "lasting_state": "{_lasting_state_id}",
            },
            ("_mummy_rot_source_id",),
        ),
        _set(
            "_mummy_fist_target_id",
            "lasting_state.timed_condition_sources[_mummy_rot_source_id].target_state_id",
        ),
        _set("_mummy_fist_target_body", "_mummy_fist_target_id"),
        _when(
            "target.created_body_source_id != null",
            [
                _set("_mummy_fist_target_body", "target.created_body_source_id"),
            ],
        ),
    ]
    packet["effects"][0:0] = [
        _call("qualify_srd_native_mummy_body", {"actor_state": "{source_state_id}"}),
        _require(
            "target.creature_type != null and target.is_object != true and "
            "target.form_statistics_replaced != true and target.form_cloud != true and "
            "target.magic_jar_role == null and target.current_body_source_id == null",
            "Join actual replaced, occupied or transferred bodies before native Fist Dust.",
        ),
        _set("_mummy_fist_dust", False),
    ]
    packet["pre_conditions"].append(
        {
            "condition": "source_state_id != null and len(source_state_id) > 0 and "
            "component_1_damage_type == 'bludgeoning' and "
            "component_2_damage_type == 'necrotic' and actual_attack_hit == true",
            "error": "Use the actual printed native Fist typed attack producer.",
        }
    )
    # The actual NPC card expects this scalar receipt; retain its exact owning calculation.
    if not any(e.get("set", {}).get("field") == "_hp_lost" for e in _walk(packet["effects"])):
        losses = [
            e
            for e in _walk(_actual_composite_body(tools, composite=False)["effects"])
            if e.get("set", {}).get("field") == "_hp_lost"
        ]
        if losses != [_set("_hp_lost", "max(0, _hp_before - _hp_after)")]:
            raise ValueError("Preserve the actual scalar HP-loss receipt used by native cards.")
        packet["effects"].append(deepcopy(losses[0]))
    packet["description"] = "Receive the actual native Mummy Fist with printed curse and Dust."
    packet["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    packet.pop("narrative", None)
    return packet


def _fist_damage_tool(tools):
    """Roll the printed secondary component and spend only the existing raw-roll debt."""
    attack = _created_undead_additional_profiles()["mummy"]["attacks"]["Rotting Fist"]
    assert (
        attack["damage_expression"],
        attack["damage_modifier"],
        attack["damage_type"],
        attack["extra_damage_expression"],
        attack["extra_damage_type"],
    ) == ("d10", 3, "bludgeoning", "3d6", "necrotic")
    tool = _tool(
        "Receive printed Fist secondary dice through one actual native packet.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _optional("lasting_state", resolve="entity", default="state"),
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
            _call("qualify_srd_native_mummy_body", {"actor_state": "{_actor_state_id}"}),
            {
                "dice": {
                    "expression": attack["extra_damage_expression"],
                    "critical": "critical_hit",
                },
                "store_as": "_mummy_fist_necrotic_roll",
            },
            _set("_mummy_fist_necrotic_amount", "_mummy_fist_necrotic_roll.total"),
            _set("_mummy_fist_necrotic_amount", "max(0, _mummy_fist_necrotic_amount)"),
            _set("_mummy_fist_bludgeoning_amount", "max(0, amount + adjustment)"),
            _call(
                "apply_srd_qualifying_composite_damage",
                {
                    "target": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    "component_1_amount": "{_mummy_fist_bludgeoning_amount}",
                    "component_1_damage_type": "bludgeoning",
                    "component_2_amount": "{_mummy_fist_necrotic_amount}",
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
        ],
    )
    _printed_component(tool, "_mummy_fist_necrotic_amount", "0", "_mummy_fist_roll_carry")
    receiving = tool["effects"][-1]
    assert receiving["call"] == "apply_srd_qualifying_composite_damage"
    receiving["call"] = "apply_srd_native_mummy_fist_packet"
    exports = {
        source
        for effect in _walk(tools["resolve_adjudicated_creature_attack"]["effects"])
        if effect.get("call") == "apply_srd_qualifying_damage"
        for source in effect.get("export_context", {}).values()
    }
    exports.update(
        (
            "_mummy_rot_source_id",
            "_mummy_fist_dust",
            "_component_1_effective_damage",
            "_component_2_effective_damage",
        )
    )
    receiving["export_context"] = {name: name for name in sorted(exports)}
    return tool


def extend_runtime(text):
    """Compose private native Fist owners; public creation/actions stay separately qualified."""
    _validate_sources()
    tools = _load_runtime(text)["tools"]
    body = _native_body_tool()
    if "qualify_srd_native_mummy_body" in tools and tools["qualify_srd_native_mummy_body"] != body:
        raise ValueError("Preserve the genuine Mummy native body owner.")
    changed = {
        "qualify_srd_native_mummy_body": body,
        "apply_srd_native_mummy_fist_packet": _fist_packet_tool(tools),
        "apply_srd_native_mummy_fist_damage": _fist_damage_tool(tools),
    }
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def bind_native_mummy_fist_attack_consumers(text):
    """Bind the real NPC roll/action owner without opening the unfinished Multiattack routine."""
    tools = _load_runtime(text)["tools"]
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    native = (
        "actor.created_spell_id == 'create_undead' and actor.created_form_id == 'mummy' "
        "and actor.form_attack_in_progress != true"
    )
    if "qualify_srd_native_mummy_fist_action" in str(attack["effects"]):
        return text
    guards = [
        p
        for p in attack["pre_conditions"]
        if p["error"] == "The selected created Undead native attack routine remains unsupported."
    ]
    if len(guards) != 1:
        raise ValueError("Extend only the actual closed native Undead attack boundary.")
    guards[0]["condition"] += " or actor.created_form_id == 'mummy'"
    attack["pre_conditions"].append(
        {
            "condition": "actor.created_spell_id != 'create_undead' or "
            "actor.created_form_id != 'mummy' or actor.form_attack_in_progress == true or "
            "(attack_name == 'Rotting Fist' and "
            "attack_modifier == 5 and damage_expression == 'd10' and damage_modifier == 3 "
            "and damage_type == 'bludgeoning' and attack_mode == 'melee' and "
            "normal_range == 5 and long_range == 5 and attack_uses_weapon != true and "
            "use_multiattack != true)",
            "error": "Use the actual printed single native Fist; "
            "its full Multiattack remains closed.",
        }
    )
    attack["effects"].insert(
        0,
        _when(
            native,
            [
                _call("qualify_srd_native_mummy_fist_action", {"actor_state": "{_actor_id}"}),
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
    candidates = [
        effect
        for effect in _walk(attack["effects"])
        if effect.get("call") == "apply_srd_qualifying_damage"
        and "game_damage_roll" in effect.get("params", {})
    ]
    if not candidates:
        raise ValueError("Fist needs the actual final NPC's real received-hit packet call.")
    for effect in candidates:
        original = deepcopy(effect)
        call = _call(
            "apply_srd_native_mummy_fist_damage",
            {
                "actor_state": "{_actor_id}",
                "target_state": "{_target_id}",
                "lasting_state": original["params"].get("lasting_state", "state"),
                "amount": original["params"]["amount"],
                "adjustment": original["params"].get("adjustment", 0),
                "critical_hit": original["params"]["critical_hit"],
                "unallocated_roll_penalty": original["params"].get("unallocated_roll_penalty", 0),
                "witness_state_ids": original["params"].get(
                    "witness_state_ids", "{witness_state_ids}"
                ),
                "minor_elementals_damage_type": "{minor_elementals_damage_type}",
            },
        )
        call["export_context"] = deepcopy(original.get("export_context", {}))
        call["export_context"].update(
            {
                name: name
                for name in (
                    "_mummy_fist_necrotic_roll",
                    "_mummy_fist_necrotic_amount",
                    "_mummy_rot_source_id",
                    "_mummy_fist_dust",
                    "_component_1_effective_damage",
                    "_component_2_effective_damage",
                )
            }
        )
        effect.clear()
        effect.update(_choose(native + " and attack_name == 'Rotting Fist'", [call], [original]))
    details = attack["metadata"]["result_card"]["details"]
    details += [
        {
            "label": "Fist Necrotic dice",
            "value": {"path": "context._mummy_fist_necrotic_roll.results"},
            "optional": True,
        },
        {
            "label": "Fist Necrotic before defenses",
            "value": {"path": "context._mummy_fist_necrotic_amount"},
            "optional": True,
        },
        {"label": "Fist Dust", "value": {"path": "context._mummy_fist_dust"}, "optional": True},
    ]
    return _replace_tools(
        text,
        {
            "qualify_srd_native_mummy_fist_action": _native_body_tool(require_command=True),
            "resolve_adjudicated_creature_attack": attack,
        },
    )
