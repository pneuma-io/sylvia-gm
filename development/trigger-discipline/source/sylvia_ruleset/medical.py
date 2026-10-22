"""Declared medical treatment and the settled COMBAT MEDICINE recovery boundary."""

from collections.abc import Callable
from typing import Any

COMBAT_MEDICINE = "specialty.field_medic.entry.combat_medicine"


def specification(actor: dict[str, Any], param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Expose GM-adjudicated treatment facts while keeping costs and first recovery authoritative."""
    return {
        "capability": "trigger-discipline.medical",
        "description": (
            "Treat one actual injury or condition with clear adjacent physical access and a capable kit. "
            "Use its stable issue_id for every medic and repeat treatment; changing labels does not create a new injury. "
            "The GM establishes diagnosis, intervention, explicit AP cost/difficulty and whether immediate function recovery "
            "is medically plausible. Use CERTAIN only when no check is warranted. STABILIZE/RESUSCITATION use the represented "
            "life-threatening condition; BATTLEFIELD_CARE treats another actual injury without erasing it. "
            "The first successful treatment is saved on the casualty. COMBAT MEDICINE restores at most 1 RES on that "
            "first success when plausible, capped by the casualty's explicit max_res. No HP, injury removal or automatic "
            "specialty discount is supplied. Consult the current Setting/rules for unresolved treatment numbers."
        ),
        "parameters": [
            actor, param("target", "string", resolve="entity"),
            param("action", "enum", enum=("STABILIZE", "RESUSCITATION", "BATTLEFIELD_CARE")),
            param("issue_id", "string"),
            param("ap_cost", "integer", minimum=0),
            param("difficulty", "integer", minimum=1, maximum=60),
            param("check_mode", "enum", enum=("ROLL", "CERTAIN")),
            param("function_recovery", "enum", enum=("PLAUSIBLE", "NOT_PLAUSIBLE")),
        ],
        "effects": _effects(),
        "costs_action": True,
    }


def _successful_treatment() -> list[dict[str, Any]]:
    """Consume the first success even at full RES or when a different medic treats next."""
    return [
        {"set": {"field": "state.last_medical.success", "value": True}},
        {"set": {"field": "state.last_medical.status", "value": "'TREATED'"}},
        {"set": {"field": "{_target_id}.life_threatening.state", "value": "'STABILIZED'"},
         "condition": "action != 'BATTLEFIELD_CARE'"},
        {"condition": "_first_treatment == true", "on_true": [
            {"list_push": {"list": "_treated_issues", "value": "issue_id"}},
            {"set": {"field": "{_target_id}.battlefield_treatments", "value": "_treated_issues"}},
        ], "on_false": []},
        {"condition": "_combat_recovery == true", "on_true": [
            {"set": {"field": "_res_restored", "value": "max(0, min(1, target.max_res - target.res))"}},
            {"increment": {"field": "{_target_id}.res", "amount": "_res_restored"}},
            {"set": {"field": "state.last_medical.res_restored", "value": "_res_restored"}},
        ], "on_false": []},
    ]


def _attempt() -> list[dict[str, Any]]:
    """Resolve warranted uncertainty once; failed attempts spend AP without inventing deterioration."""
    return [
        {"set": {"field": "_medical_success", "value": "check_mode == 'CERTAIN'"}},
        {"condition": "check_mode == 'ROLL'", "on_true": [
            {"dice": {"expression": "d20"}, "store_as": "_medicine"},
            {"set": {"field": "_medicine_total", "value": "_medicine.total + actor.skills.medicine + actor.aptitudes.reason + actor.medicine_bonus"}},
            {"set": {"field": "_medical_success", "value": "_medicine_total >= difficulty"}},
            {"set": {"field": "state.last_medical.roll", "value": "_medicine.total"}},
            {"set": {"field": "state.last_medical.total", "value": "_medicine_total"}},
            {"set": {"field": "state.last_medical.difficulty", "value": "difficulty"}},
        ], "on_false": []},
        {"decrement": {"field": "{_actor_id}.ap", "amount": "ap_cost"}},
        {"set": {"field": "state.last_medical.ap_spent", "value": "ap_cost"}},
        {"set": {"field": "state.last_medical.status", "value": "'FAILED_CHECK'"}},
        {"condition": "_medical_success == true", "on_true": _successful_treatment(), "on_false": []},
    ]


def _effects() -> list[dict[str, Any]]:
    """Check current capability, resources and physical access before treating a declared issue."""
    return [
        {"set": {"field": "state.last_medical", "value": {}}},
        *[{"set": {"field": f"state.last_medical.{key}", "value": value}}
          for key, value in (
              ("actor", "_actor_id"), ("target", "_target_id"), ("action", "action"),
              ("issue_id", "issue_id"), ("check_mode", "check_mode"),
              ("success", False), ("res_restored", 0),
              ("status", "'MISSING_CONDITION_INTERVENTION_WINDOW_OR_AP'"),
          )],
        {"set": {"field": "_treated_issues", "value": []}},
        {"set": {"field": "_treated_issues", "value": "target.battlefield_treatments"},
         "condition": "'battlefield_treatments' in target"},
        {"set": {"field": "_first_treatment", "value": "issue_id not in _treated_issues"}},
        {"set": {"field": "_combat_recovery", "value":
            f"_first_treatment == true and '{COMBAT_MEDICINE}' in actor.specialties and function_recovery == 'PLAUSIBLE'"}},
        {"set": {"field": "state.last_medical.first_treatment", "value": "_first_treatment"}},
        {"set": {"field": "_eligible", "value": False}},
        {"set": {"field": "_eligible", "value": True}, "condition":
            "(action == 'BATTLEFIELD_CARE' or (action == 'STABILIZE' and target.life_threatening.state == 'ACTIVE')) and actor.medical_kit_capable == true"},
        {"set": {"field": "_eligible", "value": True}, "condition":
            "action == 'RESUSCITATION' and target.life_threatening.state == 'FAILED' and target.life_threatening.within_reversible_window == true and actor.resuscitation_kit_capable == true"},
        {"set": {"field": "state.last_medical.status", "value": "'INVALID_ISSUE_OR_TREATMENT_HISTORY'"}, "condition":
            "len(issue_id) < 1 or len(issue_id) > 128 or len(_treated_issues) > 256 or (_first_treatment == true and len(_treated_issues) >= 256)"},
        {"condition": (
            "_eligible == true and actor.ap >= ap_cost and actor.active == true and actor.downed == false and target.active == true "
            "and len(issue_id) >= 1 and len(issue_id) <= 128 and len(_treated_issues) <= 256 "
            "and (_first_treatment == false or len(_treated_issues) < 256)"
        ), "on_true": [
            {"set": {"field": "state.last_medical.status", "value": "'MISSING_RES_MAXIMUM'"}},
            {"condition": "_combat_recovery == false or (target.max_res != null and target.max_res >= 0)", "on_true": [
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_medical_snapshot"},
                {"spatial_query": {"scene": "state.spatial_scene", "operation": "path", "snapshot": "{_medical_snapshot}",
                    "query": {"map_id": "{map_id}", "start": "{actor.position_cell}", "goal": "{target.position_cell}",
                              "allow_diagonal": True, "corner_policy": "forbid_cutting", "include_entities": False,
                              "max_steps": 2}}, "store_as": "_medical_reach"},
                {"set": {"field": "state.last_medical.status", "value": "'OUT_OF_REACH'"}},
                {"condition": "_medical_reach.found == true and _medical_reach.cost <= 1.5", "on_true": _attempt(), "on_false": []},
            ], "on_false": []},
        ], "on_false": []},
    ]
