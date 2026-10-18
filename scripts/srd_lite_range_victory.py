"""Qualify retained weapon range and encounter endings from saved combat facts."""

from copy import deepcopy

from .srd_control_behaviors import _entity, _param
from .srd_lite_attacks import _effect_names
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when


def bind_weapon_scene_facts(runtime):
    """Preserve declared input checks, then use the saved fractional distance before costs."""
    tools = runtime["tools"]
    for declaration in runtime.get("parameter_declarations", {}).values():
        if declaration.get("name") == "attack_distance" and declaration.get("type") == "integer":
            declaration["type"] = "number"
    for tool in tools.values():
        tool["parameters"] = [
            {**p, "type": "number"}
            if isinstance(p, dict)
            and p.get("name") == "attack_distance"
            and p.get("type") == "integer"
            else p
            for p in tool["parameters"]
        ]
    prefix = [
        _call(
            "resolve_srd_scene_relation_node",
            {
                "actor_state": "{_actor_state_id}",
                "target_state": "{_target_id}",
                "lasting_state": "{_lasting_state_id}",
            },
            ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight", "_scene_can_see"),
        ),
        _require(
            "_scene_same_plane == true and _scene_line_of_sight == true and ((attack_mode == 'melee' and (('reach' in weapon_info.properties and _scene_distance <= actor_state.melee_reach + 5) or ('reach' not in weapon_info.properties and _scene_distance <= actor_state.melee_reach))) or (attack_mode != 'melee' and _scene_distance <= weapon_info.long_range))",
            "The actual weapon target is beyond range or lacks a clear path on this plane.",
        ),
        _set("_lite_weapon_distance", "_scene_distance"),
        _set("_lite_weapon_actor_sees", "_scene_can_see"),
        _call(
            "resolve_srd_scene_relation_node",
            {
                "actor_state": "{_target_id}",
                "target_state": "{_actor_state_id}",
                "lasting_state": "{_lasting_state_id}",
            },
            ("_scene_can_see",),
        ),
        _set("_lite_weapon_target_sees", "_scene_can_see"),
    ]
    owners = ("resolve_srd_settled_weapon_attack", "resolve_combat_attack_followup")
    for name in owners:
        tool = tools[name]
        if "actual weapon target" in str(tool["effects"]):
            raise ValueError("Weapon scene qualification was already installed")
        tool["effects"] = deepcopy(prefix) + _effect_names(
            tool["effects"],
            {
                "attack_distance": "_lite_weapon_distance",
                "actor_can_see_target": "_lite_weapon_actor_sees",
                "target_can_see_actor": "_lite_weapon_target_sees",
            },
        )
    npc = tools["resolve_adjudicated_creature_attack"]
    npc_prefix = deepcopy(prefix)
    # NPC ranges remain the existing declared stat-block/adaptation values; saved
    # geometry qualifies the actual target before the shared Multiattack budget.
    npc_prefix[1]["require"]["condition"] = (
        "_scene_same_plane == true and _scene_line_of_sight == true and "
        "_scene_distance <= long_range and "
        "(attack_mode != 'melee' or _scene_distance <= normal_range)"
    )
    for effect in npc_prefix:
        if "call" in effect:
            effect["params"] = {
                key: value.replace("_actor_state_id", "_actor_id")
                for key, value in effect["params"].items()
            }
    npc["effects"] = npc_prefix + _effect_names(
        npc["effects"],
        {
            "attack_distance": "_lite_weapon_distance",
            "actor_can_see_target": "_lite_weapon_actor_sees",
            "target_can_see_actor": "_lite_weapon_target_sees",
        },
    )
    return [*owners, "resolve_adjudicated_creature_attack"]


def bind_victory_boundary(runtime):
    """Reject a claimed defeat while recorded opponents can still fight; keep agreed stops."""
    tools = runtime["tools"]
    standing = "creature.is_alive == true and creature.hp > 0"
    tools["read_srd_lite_standing_actor"] = _tool(
        "Read a recorded encounter creature's ability to remain standing.",
        [_entity("creature")],
        [_set("_lite_actor_standing", standing)],
    )
    tools["check_srd_lite_defeated_opponents"] = _tool(
        "Check the recorded hostile relationships of a standing combatant.",
        [_entity("creature"), _param("combatant_ids", "array", items={"type": "string"})],
        [
            _set("_lite_checked_defeated", "not (" + standing + ")"),
            _when(
                standing + " and creature.fighting_actor_ids != null",
                [
                    {
                        "for_each": {
                            "collection": "creature.fighting_actor_ids",
                            "as": "_lite_opponent",
                            "do": [
                                _when(
                                    "_lite_opponent in combatant_ids",
                                    [
                                        _call(
                                            "read_srd_lite_standing_actor",
                                            {"creature": "{_lite_opponent}"},
                                            ("_lite_actor_standing",),
                                        ),
                                        _require(
                                            "_lite_actor_standing != true",
                                            "Recorded opponents are still standing; "
                                    "use fighting_stopped for an agreed stop.",
                                        ),
                                    ],
                                ),
                            ],
                        }
                    }
                ],
            ),
        ],
    )
    prefix = _when(
        "end_reason == 'one_side_defeated'",
        [
            _set("_lite_encounter_defeated", False),
            _set("_lite_combatants", ["combat.order_first", "combat.order_second"]),
            _when(
                "combat.group_order == true",
                [
                    _set("_lite_combatants", []),
                    {
                        "for_each": {
                            "collection": "_lite_ending_group_order",
                            "as": "_lite_order_entry",
                            "do": [
                                _set("_lite_order_actor", "_lite_order_entry.entity_id"),
                                _when(
                                    "_lite_order_actor == null",
                                    [_set("_lite_order_actor", "_lite_order_entry.id")],
                                ),
                                _when(
                                    "_lite_order_actor == null",
                                    [_set("_lite_order_actor", "_lite_order_entry")],
                                ),
                                {
                                    "list_push": {
                                        "list": "_lite_combatants",
                                        "value": "_lite_order_actor",
                                    }
                                },
                            ],
                        }
                    },
                ],
            ),
            {
                "for_each": {
                    "collection": "_lite_combatants",
                    "as": "_lite_combatant",
                    "do": [
                        _call(
                            "check_srd_lite_defeated_opponents",
                            {
                                "creature": "{_lite_combatant}",
                                "combatant_ids": "{_lite_combatants}",
                            },
                            ("_lite_checked_defeated",),
                        ),
                        _when(
                            "_lite_checked_defeated == true",
                            [_set("_lite_encounter_defeated", True)],
                        ),
                    ],
                }
            },
            _require(
                "_lite_encounter_defeated == true",
                "No recorded encounter creature has been defeated; "
                "use fighting_stopped for an agreed stop.",
            ),
        ],
    )
    ending = tools["end_combat_encounter"]["effects"]
    ending.insert(0, prefix)
    ending.insert(0, _set("_lite_ending_group_order", "combat.order"))
    for effect in ending:
        if effect.get("condition") == "combat.group_order == true":
            for nested in effect.get("on_true", []):
                if nested.get("for_each", {}).get("collection") == "combat.order":
                    nested["for_each"]["collection"] = "_lite_ending_group_order"
    return ["end_combat_encounter"]


def bind_range_and_victory(runtime):
    return bind_weapon_scene_facts(runtime) + bind_victory_boundary(runtime)
