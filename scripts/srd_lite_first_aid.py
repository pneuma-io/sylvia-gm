"""Admit ordinary Help/Medicine using the retained SRD stabilization owner."""

from copy import deepcopy

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _tool, _when

ACTION = "stabilize_srd_creature"
EXPORTS = (
    "_stabilization_check",
    "_stabilization_total",
    "_stabilization_outcome",
    "_stable_after",
    "_stable_recovery_hours_remaining_after",
    "_conditions_after",
)


def expose_first_aid(runtime, record):
    """Add a public Action wrapper without changing the native check or life-cycle effect."""
    if ACTION in runtime["tools"] or any(row["name"] == ACTION for row in record["tools"]):
        raise ValueError("Ordinary first aid must be composed exactly once")
    effects = [
        _require(
            "actor_state.is_alive == true and actor_state.hp > 0",
            "A dead or unconscious creature cannot administer first aid.",
        ),
        _require(
            "not ('incapacitated' in actor_state.conditions or "
            "'paralyzed' in actor_state.conditions or "
            "'petrified' in actor_state.conditions or 'stunned' in actor_state.conditions or "
            "'unconscious' in actor_state.conditions)",
            "An incapacitated creature cannot take the Help action.",
        ),
        _require("_actor_state_id != _target_id", "A creature cannot stabilize itself."),
        _require(
            "target.is_alive == true and target.hp == 0 and target.stable == false",
            "First aid needs a living, unstable creature at 0 Hit Points.",
        ),
    ]
    effects += [
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _actor_state_id",
                    "First aid requires the helper's active combat turn.",
                ),
                _call("spend_srd_action", {"magic_action": False}),
            ],
        ),
        _call(
            "stabilize_character",
            {
                "actor_state": "{_actor_state_id}",
                "target": "{_target_id}",
                "medicine_modifier": "{medicine_modifier}",
                "advantage": "{advantage}",
                "disadvantage": "{disadvantage}",
            },
            EXPORTS,
        ),
    ]
    tool = _tool(
        "Take the Help action to attempt ordinary first aid on a living, unstable creature at 0 HP "
        "able to receive physical first aid. Use the helper's actual Wisdom (Medicine) modifier; "
        "the native DC 10 check determines stabilization. Stabilization grants no HP and leaves "
        "Unconscious until recovery. "
        "This operation does not consume a Healer's Kit or simulate a healing spell.",
        [
            _entity("actor_state"),
            _entity("target"),
            _parameter("medicine_modifier", "integer"),
            _parameter("advantage", "boolean", required=False, default=False),
            _parameter("disadvantage", "boolean", required=False, default=False),
        ],
        effects,
    )
    tool["metadata"] = {
        "category": "recovery",
        "costs_action": True,
        "result_card": deepcopy(runtime["tools"]["stabilize_character"]["metadata"]["result_card"]),
    }
    runtime["tools"][ACTION] = tool
    record["tools"].append(
        {
            "name": ACTION,
            "capability": "dnd.srd.5.2.1.damage-healing-core",
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
            "input_schema": _tool_schema(tool, tool_name=ACTION),
        }
    )
    return ACTION
