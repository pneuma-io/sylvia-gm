"""Settled milestone and specialty advancement; numerical growth remains explicit."""

from collections.abc import Callable
from typing import Any


def rules(catalog: dict) -> dict[str, dict]:
    """Use the same class/tier prerequisites as normal Character File admission."""
    nodes = [node for node in catalog["nodes"] if not node["unresolved"]]
    return {node["id"]: {
        "class_id": node["class_id"],
        "prerequisites": [prior["id"] for prior in nodes
                          if prior["specialty"] == node["specialty"] and
                          prior["tier"] == node["tier"] - 1],
        "minimum": 0 if node["tier"] == 0 else 1 if node["tier"] == 1 else 2,
    } for node in nodes}


def specification(actor: dict, param: Callable[..., dict], nodes: dict) -> dict[str, Any]:
    """Apply one earned level and one legal choice without healing or resetting equipment."""
    return {
        "capability": "trigger-discipline.character",
        "description": (
            "Record one GM-confirmed earned milestone, advancing one level and adding one "
            "class/prerequisite-legal specialty. Supply the current expected level and a stable "
            "milestone ID. Repeated milestones grant nothing. This preserves skills, aptitudes, "
            "HP, RES, AP, injuries, equipment and ammunition; it supplies no numerical "
            "growth schedule."
        ),
        "parameters": [actor, param("character", "string", resolve="participant"),
                       param("expected_level", "integer", minimum=1, maximum=19),
                       param("milestone_id", "string"), param("milestone_confirmed", "boolean"),
                       param("specialty_id", "enum", enum=tuple(sorted(nodes)))],
        "effects": [
            {"set": {"field": "state.last_advancement", "value": {}}},
            {"set": {"field": "state.last_advancement.status", "value": "'ADVANCEMENT_REJECTED'"}},
            {"set": {"field": "_milestones", "value": []}},
            {"set": {"field": "_milestones", "value": "actor.advancement_milestones"},
             "condition": "actor.advancement_milestones != null"},
            {"set": {"field": "_advance_rule",
                     "value": "definitions.advancement_nodes[specialty_id]"}},
            {"set": {"field": "_advance_choices", "value": "actor.specialties"}},
            {"set": {"field": "_same_live_character", "value": (
                "_character_id == _actor_id and character.data.level == actor.level and "
                "character.data.class_id == actor.class_id and "
                "len(character.data.specialties) == len(actor.specialties)"
            )}},
            {"set": {"field": "_prerequisite_count", "value": 0}},
            {"for_each": {"collection": "_advance_choices", "as": "_prior_choice", "do": [
                {"set": {"field": "_same_live_character", "value": False},
                 "condition": "_prior_choice not in character.data.specialties"},
                {"set": {"field": "_prerequisite_count", "value": "_prerequisite_count + 1"},
                 "condition": "_prior_choice in _advance_rule.prerequisites"},
            ]}},
            {"condition": "milestone_id in _milestones", "on_true": [
                {"set": {"field": "state.last_advancement.status", "value": "'ALREADY_RECORDED'"}},
            ], "on_false": [
                {"condition": (
                    "milestone_confirmed == true and len(milestone_id) >= 1 and "
                    "len(milestone_id) <= 128 and len(_milestones) < 20 and "
                    "actor.level == expected_level and actor.level < 20 and "
                    "_same_live_character == true and "
                    "actor.class_id == _advance_rule.class_id and "
                    "specialty_id not in actor.specialties and "
                    "len(actor.specialties) <= actor.level and "
                    "_prerequisite_count >= _advance_rule.minimum"
                ), "on_true": [
                    {"increment": {"field": "{_actor_id}.level", "amount": 1}},
                    {"list_push": {"list": "_advance_choices", "value": "specialty_id"}},
                    {"set": {"field": "{_actor_id}.specialties", "value": "_advance_choices"}},
                    {"set_participant": {"participant": "_character_id", "field": "level",
                                         "value": "expected_level + 1"}},
                    {"set_participant": {"participant": "_character_id", "field": "specialties",
                                         "value": "_advance_choices"}},
                    {"list_push": {"list": "_milestones", "value": "milestone_id"}},
                    {"set": {"field": "{_actor_id}.advancement_milestones",
                             "value": "_milestones"}},
                    {"set": {"field": "state.last_advancement.level",
                             "value": "expected_level + 1"}},
                    {"set": {"field": "state.last_advancement.status", "value": "'ADVANCED'"}},
                ], "on_false": []},
            ]},
            {"set": {"field": "_advance_rule", "value": None}},
            {"set": {"field": "_advance_choices", "value": None}},
            {"set": {"field": "_milestones", "value": None}},
        ],
        "costs_action": False,
    }
