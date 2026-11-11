from __future__ import annotations

from typing import Any


ANCHOR_ESTABLISH_STRONGPOINT = "specialty.anchor.entry.establish_strongpoint"
ARSENAL_FIGHTING_LOAD = "specialty.arsenal.entry.fighting_load"
AUTODRONES_TACTICAL_AUTONOMY = (
    "specialty.autodrones.entry.tactical_autonomy"
)
BREAKER_TACTICAL_BREACHING = "specialty.breaker.entry.tactical_breaching"
DIPLOMAT_NEGOTIATING_POSITION = "specialty.diplomat.entry.negotiating_position"
ENFORCER_WEAPON_RETENTION = "specialty.enforcer.entry.weapon_retention"
EVADER_BREAK_CONTACT = "specialty.evader.entry.break_contact"
FIELD_MEDIC_COMBAT_MEDICINE = "specialty.field_medic.entry.combat_medicine"
FIELD_ENGINEER_FIELD_WORK = "specialty.field_engineer.entry.field_work"
FPV_DRONES_MANUAL_PILOT = "specialty.fpv_drones.entry.manual_pilot"
FLANKER_ANGLE_ADVANTAGE = "specialty.flanker.entry.angle_advantage"
GUNNER_SUSTAINED_FIRE_DISCIPLINE = (
    "specialty.gunner.entry.sustained_fire_discipline"
)
HANDLER_WORK_A_CONTACT = "specialty.handler.entry.work_a_contact"
ICON_RECOGNITION = "specialty.icon.entry.recognition"
GUARDIAN_INTERPOSE = "specialty.guardian.entry.interpose"
INFILTRATOR_APPROACH_PLAN = "specialty.infiltrator.entry.approach_plan"
LOCAL_HACKER_SERVICE_ACCESS = "specialty.local_hacker.entry.service_access"
NETWORKER_MAP_THE_LINKS = "specialty.networker.entry.map_the_links"
OPERATOR_WEAPON_MANIPULATION = "specialty.operator.entry.weapon_manipulation"
RESUSCITATOR_RESUSCITATION = "specialty.resuscitator.entry.resuscitation"
SENTINEL_PREPARED_SIGHTLINE = "specialty.sentinel.entry.prepared_sightline"
SHOCK_ASSAULT_MOMENTUM = "specialty.shock.entry.assault_momentum"
SHIELD_BEARER_SHIELD_TRAINING = "specialty.shield_bearer.entry.shield_training"
SNAPSHOT_QUICK_SOLUTION = "specialty.snapshot.entry.quick_solution"
SNIPER_DEEP_AIM = "specialty.sniper.entry.deep_aim"
TRAUMA_TRAUMA_CARE = "specialty.trauma.entry.trauma_care"


def has_node(actor: Any, node_id: str) -> bool:
    return node_id in actor.specialty_nodes


def record_effect(
    metrics: dict[str, Any],
    *,
    actor_id: str,
    node_id: str,
    action: str,
    field: str,
    before: Any,
    after: Any,
) -> dict[str, Any]:
    if before == after:
        raise ValueError(
            f"specialty effect {node_id} did not change {field}: {before!r}"
        )
    effect = {
        "actor_id": actor_id,
        "node_id": node_id,
        "action": action,
        "field": field,
        "before": before,
        "after": after,
    }
    metrics["specialty_effects"].append(effect)
    return effect
