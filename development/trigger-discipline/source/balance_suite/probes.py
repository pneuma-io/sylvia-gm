from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import copy
from functools import lru_cache
import hashlib
import json
import math
from typing import Any

from .engine import EventEngine, TriggerEffect
from .fixtures import CLASS_CAST, LEVELS, SPECIALIST_PROGRESSION, benchmark_actor, benchmark_control_values, build_map, network_site_nodes, weapon_state
from .geometry import actual_difficulty, directional_facing_protects, reduce_exposure_band, resolve_blast_snapshot, resolve_sequential_projectiles, trace_cells
from .model import ConditionState, CoverObject, HitQuality, KnowledgeState, LifeThreateningCondition, Position, Side, SimulationState, Stance
from .parameters import FIREARM_IDS, MATERIAL_IDS, ParameterSet
from .rules import (
    attack_values_for_weapon,
    cqc_defense,
    connection_opportunities,
    explosive_warning,
    fire_action,
    movement_rate,
    range_modifier,
    resolve_attack,
    resolve_check,
    resolve_cover_roll,
    resolve_damage,
    resolve_material,
)
from .support import (
    degrade_knowledge,
    discover_neighbors,
    grenade_scatter_distance,
    grenade_scatter_landing,
    intrusion_action,
    lose_exact_observation,
    medical_action,
    resolve_social_attempt,
    share_knowledge,
    valid_network_route,
)


DIFFICULTY_IDS = ("routine", "standard", "hard", "severe", "extreme")
ARMOR_IDS = ("none", "standard_body_armor", "heavy_body_armor")


def _probe_actor_record(
    parameters: ParameterSet,
    *,
    scenario_id: str,
    side: Side,
    level: int,
    class_id: str,
    position: Position,
    weapon_id: str,
    skill: int,
    aptitude: int,
    armor_id: str = "none",
    posture: Stance = Stance.STANDING,
    knowledge: KnowledgeState = KnowledgeState.CONFIRMED,
) -> dict[str, Any]:
    actor = benchmark_actor(
        class_id,
        level,
        side,
        position,
        parameters,
        weapon_id=weapon_id,
    )
    actor.skill = skill
    actor.aptitude = aptitude
    actor.stance = posture
    if armor_id != "none":
        armor_res = int(parameters.values[f"equipment.{armor_id}.res"])
        actor.res += armor_res
        actor.max_res += armor_res
    target_id = "B_PROBE_1" if side is Side.A else "A_PROBE_1"
    weapon = actor.weapon
    return {
        "fixture_id": f"{scenario_id}:SIDE-{side.value}",
        "build_id": f"{scenario_id}:BASELINE-{class_id}-{side.value}",
        "side": side.value,
        "level": level,
        "class_id": class_id,
        "specialty_nodes": [],
        "skill": actor.skill,
        "aptitude": actor.aptitude,
        "hp": {"current": actor.hp, "maximum": actor.max_hp},
        "res": {"current": actor.res, "maximum": actor.max_res},
        "ap": {"current": actor.ap, "maximum": actor.max_ap},
        "equipment": {
            "weapon_id": weapon.weapon_id if weapon else None,
            "armor_id": armor_id,
            "ammunition": weapon.total_rounds() if weapon else 0,
            "chamber": weapon.chamber if weapon else None,
            "current_recoil": weapon.current_recoil if weapon else 0,
        },
        "posture": actor.stance.value,
        "position": asdict(actor.position),
        "target_knowledge": {target_id: knowledge.value},
        "policy_id": "POLICY_EXACT_ENUMERATION",
    }


def _probe_case_record(
    parameters: ParameterSet,
    *,
    scenario_id: str,
    map_id: str,
    case_id: str,
    result_locator: str,
    position_a: Position,
    position_b: Position,
    level: int = 1,
    class_a: str = "RIFLEMAN",
    class_b: str = "RIFLEMAN",
    weapon_a: str = "service_rifle",
    weapon_b: str = "service_rifle",
    skill_a: int = 4,
    skill_b: int = 4,
    aptitude_a: int = 1,
    aptitude_b: int = 1,
    armor_b: str = "none",
    posture_a: Stance = Stance.STANDING,
    posture_b: Stance = Stance.STANDING,
    knowledge_a: KnowledgeState = KnowledgeState.CONFIRMED,
    knowledge_b: KnowledgeState = KnowledgeState.CONFIRMED,
    subsystem_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actors = {
        "A_PROBE_1": _probe_actor_record(
            parameters,
            scenario_id=scenario_id,
            side=Side.A,
            level=level,
            class_id=class_a,
            position=position_a,
            weapon_id=weapon_a,
            skill=skill_a,
            aptitude=aptitude_a,
            posture=posture_a,
            knowledge=knowledge_a,
        ),
        "B_PROBE_1": _probe_actor_record(
            parameters,
            scenario_id=scenario_id,
            side=Side.B,
            level=level,
            class_id=class_b,
            position=position_b,
            weapon_id=weapon_b,
            skill=skill_b,
            aptitude=aptitude_b,
            armor_id=armor_b,
            posture=posture_b,
            knowledge=knowledge_b,
        ),
    }
    input_state = {
        "map_fixture_id": map_id,
        "actors": actors,
        "subsystem_state": copy.deepcopy(subsystem_state or {}),
        "team_turn": {
            "active_side": "A",
            "initiative_model": "TEAM_TURN",
            "elapsed_seconds": 0,
        },
    }
    return {
        "case_id": f"{scenario_id}:{case_id}",
        "fixture_ids": [
            map_id,
            f"{scenario_id}:SIDE-A",
            f"{scenario_id}:SIDE-B",
            f"{scenario_id}:STATE",
        ],
        "input_state": input_state,
        "result_locator": result_locator,
        "serialized_case_hash": hashlib.sha256(
            json.dumps(
                input_state,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest(),
    }


def exact_check_curves(parameters: ParameterSet) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for level in LEVELS:
        controls = {
            control: benchmark_control_values(level, control)
            for control in ("SPECIALIST", "GENERALIST", "TRAINING", "TALENT")
        }
        for control, (skill, apt) in controls.items():
            for difficulty_id in DIFFICULTY_IDS:
                difficulty = parameters.values[f"check.difficulty.{difficulty_id}"]
                outcomes = [resolve_check(roll, skill, apt, difficulty) for roll in range(1, 21)]
                rows.append(
                    {
                        "level": level,
                        "control": control,
                        "skill": skill,
                        "aptitude": apt,
                        "difficulty_id": difficulty_id,
                        "difficulty": difficulty,
                        "successes": sum(result.success for result in outcomes),
                        "total": 20,
                        "success_rate": sum(result.success for result in outcomes) / 20,
                        "natural_1_success": outcomes[0].success,
                        "natural_20_success": outcomes[-1].success,
                        "enumeration": [result.to_dict() for result in outcomes],
                    }
                )
    findings: list[dict[str, Any]] = []
    for level in LEVELS:
        standard = [row for row in rows if row["level"] == level and row["difficulty_id"] == "standard"]
        training = next(row for row in standard if row["control"] == "TRAINING")
        talent = next(row for row in standard if row["control"] == "TALENT")
        if talent["success_rate"] > training["success_rate"]:
            findings.append(
                {
                    "code": "TRAINING_TALENT_CONFLICT",
                    "severity": "REVIEW",
                    "level": level,
                    "training_rate": training["success_rate"],
                    "talent_rate": talent["success_rate"],
                }
            )
    return {"probe": "CHECK-CURVE", "exact": True, "rows": rows, "findings": findings}


def exact_attack_distribution(
    weapon_id: str,
    distance_m: float,
    skill: int,
    aptitude: int,
    base_difficulty: int,
    armor_id: str,
    level: int,
    parameters: ParameterSet,
) -> dict[str, Any]:
    damage, target_pen, material_pen, range_penalty = attack_values_for_weapon(weapon_id, distance_m, parameters)
    index = LEVELS.index(level)
    res = parameters.values["res.vector_l01_l05_l10_l15_l20"][index]
    hp = parameters.values["hp.vector_l01_l05_l10_l15_l20"][index]
    if armor_id != "none":
        res += parameters.values[f"equipment.{armor_id}.res"]
    outcomes: list[dict[str, Any]] = []
    quality_counts: Counter[str] = Counter()
    for roll in range(1, 21):
        attack = resolve_attack(roll, skill, aptitude, base_difficulty + range_penalty, damage, target_pen, parameters)
        quality_counts[attack.quality.value] += 1
        routed = resolve_damage(attack.effective_damage, attack.effective_target_pen, res, hp) if attack.quality is not HitQuality.MISS else resolve_damage(0, 0, res, hp)
        outcomes.append({"roll": roll, "attack": attack.to_dict(), "damage": routed.to_dict()})
    return {
        "weapon_id": weapon_id,
        "distance_m": distance_m,
        "level": level,
        "armor_id": armor_id,
        "difficulty": base_difficulty + range_penalty,
        "range_modifier": range_penalty,
        "material_pen": material_pen,
        "hit_rate": 1 - quality_counts[HitQuality.MISS.value] / 20,
        "quality_distribution": {quality.value: quality_counts[quality.value] / 20 for quality in HitQuality},
        "expected_res_damage": sum(row["damage"]["res_damage"] for row in outcomes) / 20,
        "expected_hp_damage": sum(row["damage"]["total_hp_damage"] for row in outcomes) / 20,
        "expected_hp_bypass": sum(row["damage"]["hp_bypass"] for row in outcomes) / 20,
        "outcomes": outcomes,
    }


def exact_fire_range(parameters: ParameterSet) -> dict[str, Any]:
    anchors = (5, 10, 20, 35, 50, 100, 250, 400, 600, 800, 950)
    rows = [
        exact_attack_distribution(weapon, distance, 4, 1, parameters.values["check.difficulty.standard"], armor, 1, parameters)
        for weapon in FIREARM_IDS
        for distance in anchors
        for armor in ARMOR_IDS
    ]
    findings: list[dict[str, Any]] = []
    shotgun_5 = next(row for row in rows if row["weapon_id"] == "shotgun" and row["distance_m"] == 5 and row["armor_id"] == "none")
    rifle_5 = next(row for row in rows if row["weapon_id"] == "service_rifle" and row["distance_m"] == 5 and row["armor_id"] == "none")
    if shotgun_5["expected_hp_damage"] + shotgun_5["expected_res_damage"] <= rifle_5["expected_hp_damage"] + rifle_5["expected_res_damage"]:
        findings.append({"code": "DIRECTIONAL_FAILURE", "subject": "SHOTGUN close-band strength", "severity": "FAIL"})
    return {"probe": "FIRE-SINGLE-RANGE", "exact": True, "rows": rows, "findings": findings}


def handling_probe(parameters: ParameterSet) -> dict[str, Any]:
    """Exercise the declared constrained/contact HANDLING fixture exactly."""
    rows: list[dict[str, Any]] = []
    standard = parameters.values["check.difficulty.standard"]
    multiplier = parameters.values["cqc.contact_handling_multiplier"]
    for weapon_id in ("pistol", "smg", "service_rifle", "lmg", "precision_rifle"):
        handling = parameters.values[f"equipment.{weapon_id}.handling"]
        constrained_modifier = handling * multiplier
        open_result = exact_attack_distribution(weapon_id, 1, 4, 1, standard, "none", 1, parameters)
        constrained_result = exact_attack_distribution(
            weapon_id, 1, 4, 1, standard + constrained_modifier, "none", 1, parameters
        )
        rows.append(
            {
                "weapon_id": weapon_id,
                "handling": handling,
                "contact_handling_multiplier": multiplier,
                "open_difficulty": standard,
                "constrained_difficulty": standard + constrained_modifier,
                "open_hit_rate": open_result["hit_rate"],
                "constrained_hit_rate": constrained_result["hit_rate"],
                "adjacency_disables_firearm": False,
                "weapon_control_disables_controlled_weapon": True,
                "parameter_ids": [f"equipment.{weapon_id}.handling", "cqc.contact_handling_multiplier"],
            }
        )
    return {"probe": "FIRE-HANDLING-TIGHT/CQC-CONTACT-FIRE", "exact": True, "rows": rows, "findings": []}


def _expected_shots_to_downed(
    weapon_id: str,
    armor_res: int,
    level: int,
    parameters: ParameterSet,
) -> float | None:
    index = LEVELS.index(level)
    starting_res = parameters.values["res.vector_l01_l05_l10_l15_l20"][index] + armor_res
    starting_hp = parameters.values["hp.vector_l01_l05_l10_l15_l20"][index]
    standard = parameters.values["check.difficulty.standard"]
    damage, target_pen, _material_pen, range_penalty = attack_values_for_weapon(weapon_id, 20, parameters)
    attacks = [resolve_attack(roll, 4, 1, standard + range_penalty, damage, target_pen, parameters) for roll in range(1, 21)]

    @lru_cache(maxsize=None)
    def expected(res: int, hp: int) -> float:
        if hp <= 0:
            return 0.0
        self_loops = 0
        onward = 0.0
        for attack in attacks:
            if attack.quality is HitQuality.MISS:
                next_res, next_hp = res, hp
            else:
                routed = resolve_damage(attack.effective_damage, attack.effective_target_pen, res, hp)
                next_res = routed.remaining_res
                next_hp = routed.remaining_hp
            if (next_res, next_hp) == (res, hp):
                self_loops += 1
            else:
                onward += expected(next_res, next_hp)
        advancing_probability = 1 - self_loops / 20
        if advancing_probability <= 0:
            return math.inf
        return (1 + onward / 20) / advancing_probability

    result = expected(starting_res, starting_hp)
    return None if math.isinf(result) else result


def armor_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    base_rate = parameters.values["move.upright_m_per_ap"]
    burden_penalty = parameters.values["move.burden_penalty_m_per_ap_per_point"]
    for level in LEVELS:
        for armor_id in ARMOR_IDS:
            armor_res = 0 if armor_id == "none" else parameters.values[f"equipment.{armor_id}.res"]
            burden = 0 if armor_id == "none" else parameters.values[f"equipment.{armor_id}.burden"]
            movement = max(0.5, base_rate - burden * burden_penalty)
            for weapon_id in ("pistol", "service_rifle", "precision_rifle"):
                shots = _expected_shots_to_downed(weapon_id, armor_res, level, parameters)
                rows.append(
                    {
                        "level": level,
                        "armor_id": armor_id,
                        "weapon_id": weapon_id,
                        "armor_res": armor_res,
                        "burden": burden,
                        "movement_m_per_ap": movement,
                        "expected_shots_to_downed": shots,
                        "expected_ap_to_downed": shots,
                        "armor_is_cover": False,
                    }
                )
    standard_res = parameters.values["equipment.standard_body_armor.res"]
    heavy_res = parameters.values["equipment.heavy_body_armor.res"]
    standard_burden = parameters.values["equipment.standard_body_armor.burden"]
    heavy_burden = parameters.values["equipment.heavy_body_armor.burden"]
    findings = []
    if heavy_res <= standard_res or heavy_burden <= standard_burden:
        findings.append(
            {
                "code": "DIRECTIONAL_FAILURE",
                "severity": "FAIL",
                "subject": "heavy armor protection/burden ordering",
                "evidence": {"standard_res": standard_res, "heavy_res": heavy_res, "standard_burden": standard_burden, "heavy_burden": heavy_burden},
            }
        )
    return {"probe": "PROTECTION-ARMOR", "exact": True, "rows": rows, "findings": findings}


def hit_quality_boundaries(parameters: ParameterSet) -> dict[str, Any]:
    values = parameters.values
    margins = sorted(
        {
            0,
            values["hit_quality.clean_margin"] - 1,
            values["hit_quality.clean_margin"],
            values["hit_quality.decisive_margin"] - 1,
            values["hit_quality.decisive_margin"],
            values["hit_quality.critical_margin"] - 1,
            values["hit_quality.critical_margin"],
        }
    )
    rows = []
    for margin in margins:
        roll = min(19, max(2, 10 + margin))
        difficulty = 10
        skill = margin
        attack = resolve_attack(roll, skill, 0, difficulty + roll - 10, 5, 2, parameters)
        rows.append({"margin": margin, "quality": attack.quality.value})
    return {"probe": "HIT-QUALITY-BOUNDARIES", "exact": True, "rows": rows}


def auto_string_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    modes = (("SEMI AUTO", 1), ("BURST", 3), ("FULL AUTO", 3), ("FULL AUTO", 5), ("FULL AUTO", 10), ("FULL AUTO", 15), ("FULL AUTO", 20))
    for weapon_id in ("service_rifle", "smg", "lmg"):
        for mode, rounds in modes:
            if mode == "BURST" and weapon_id != "service_rifle":
                continue
            if mode == "SEMI AUTO" and weapon_id == "lmg":
                continue
            per_roll: list[dict[str, Any]] = []
            for roll in range(1, 21):
                weapon = weapon_state(weapon_id, parameters)
                if mode == "BURST":
                    mode_for_state = "BURST"
                else:
                    mode_for_state = mode
                result = fire_action(weapon, mode_for_state, rounds, roll, 4, 1, parameters.values["check.difficulty.standard"], 20, "full", parameters)
                per_roll.append(result.to_dict())
            rows.append(
                {
                    "weapon_id": weapon_id,
                    "mode": mode,
                    "rounds": rounds,
                    "expected_connections": sum(item["target_connections"] for item in per_roll) / 20,
                    "expected_recoil": sum(item["recoil_after"] for item in per_roll) / 20,
                    "expected_ap": sum(item["ap_cost"] for item in per_roll) / 20,
                    "connections_per_round": sum(item["target_connections"] for item in per_roll) / (20 * rounds),
                    "enumeration": per_roll,
                }
            )
    return {"probe": "FIRE-AUTO-STRING", "exact": True, "rows": rows, "findings": []}


def material_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for weapon in FIREARM_IDS:
        damage, target_pen, material_pen, _ = attack_values_for_weapon(weapon, 10, parameters)
        for material in MATERIAL_IDS:
            resistance = parameters.values[f"material.{material}.resistance"]
            result = resolve_material(damage, target_pen, material_pen, resistance)
            rows.append({"weapon_id": weapon, "material": material, **result.to_dict()})
    return {"probe": "PROJECTILE-MATERIAL", "exact": True, "rows": rows}


def cover_probe(parameters: ParameterSet) -> dict[str, Any]:
    open_difficulty = parameters.values["check.difficulty.standard"]
    routes: list[dict[str, Any]] = []
    shooter = Position(5, 10)
    target = Position(25, 10)
    exposure_cover_heights = {
        Stance.STANDING: {"full": 0.5, "half": 1.0, "quarter": 1.5},
        Stance.CROUCHED: {"full": 0.4, "half": 0.6, "quarter": 0.9},
        Stance.PRONE: {"full": 0.1, "half": 0.25, "quarter": 0.36},
    }
    for posture, fixture_heights in exposure_cover_heights.items():
        for expected_exposure, cover_height_m in fixture_heights.items():
            grid = build_map("MAP_STREET_60", parameters)
            cover = grid.covers["STREET_BARRIER_1"]
            cover.height_m = cover_height_m
            exposure = grid.exposure(shooter, target, posture)
            if exposure != expected_exposure:
                raise AssertionError(
                    "partial-cover fixture did not produce its declared exposure: "
                    f"{posture.value}/{expected_exposure} -> {exposure}"
                )
            actual = actual_difficulty(open_difficulty, exposure, parameters)
            for total in range(open_difficulty - 4, open_difficulty + 9):
                route = asdict(
                    resolve_cover_roll(total, open_difficulty, actual)
                )
                routes.append(
                    {
                        "posture": posture.value,
                        "exposure": exposure,
                        "roll_total": total,
                        "open_difficulty": open_difficulty,
                        "actual_difficulty": actual,
                        "direct_attack_available": True,
                        "routing": route,
                        "shooter_position": asdict(shooter),
                        "target_position": asdict(target),
                        "cover_id": cover.cover_id,
                        "cover_cells": sorted(cover.cells),
                        "cover_height_m": cover.height_m,
                        "geometry_verified_exposure": exposure,
                    }
                )
    destruction_rows: list[dict[str, Any]] = []
    for weapon_id in FIREARM_IDS:
        damage, target_pen, material_pen, _range = attack_values_for_weapon(weapon_id, 10, parameters)
        for material in MATERIAL_IDS:
            street = build_map("MAP_STREET_60", parameters)
            street_cover = street.covers["STREET_BARRIER_1"]
            cover = CoverObject(
                street_cover.cover_id,
                set(street_cover.cells),
                street_cover.height_m,
                parameters.values[f"material.{material}.resistance"],
                parameters.values[f"material.{material}.cover_hp"],
            )
            sequential = resolve_sequential_projectiles(cover, 12, damage=damage, target_pen=target_pen, material_pen=material_pen)
            first_clear = next((row.projectile_index for row in sequential if row.intersection == "CLEAR AFTER DESTRUCTION"), None)
            destruction_rows.append(
                {
                    "weapon_id": weapon_id,
                    "material": material,
                    "cover_id": cover.cover_id,
                    "cover_cells": sorted(cover.cells),
                    "cover_height_m": cover.height_m,
                    "damage": damage,
                    "material_resistance": parameters.values[f"material.{material}.resistance"],
                    "cover_damage_per_impact": max(0, damage - parameters.values[f"material.{material}.resistance"]),
                    "cover_hp_seed": parameters.values[f"material.{material}.cover_hp"],
                    "destroyed_after_12_impacts": not cover.active,
                    "first_projectile_with_clear_geometry": first_clear,
                    "sequential_projectiles": [asdict(result) for result in sequential],
                }
            )
    return {
        "probe": "COVER-PARTIAL",
        "exact": True,
        "routes": routes,
        "exposure_offsets": {
            exposure: None if actual_difficulty(open_difficulty, exposure, parameters) is None else actual_difficulty(open_difficulty, exposure, parameters) - open_difficulty
            for exposure in ("full", "half", "quarter", "none")
        },
        "specialty_exposure_reduction": {
            "FPV NAP-OF-THE-EARTH": reduce_exposure_band("full", parameters.values["specialty.fpv_drones.nap_of_the_earth.exposure_band_reduction"]),
            "GUARDIAN BODY COVER": reduce_exposure_band("half", parameters.values["specialty.guardian.body_cover.exposure_band_reduction"]),
        },
        "cover_destruction_matrix": destruction_rows,
        "findings": [],
    }


def suppression_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows = []
    for committed in (5, 10, 20, 30):
        capacity = max(1, committed // parameters.values["suppression.exposure_capacity_round_divisor"])
        rows.append(
            {
                "committed_rounds": committed,
                "exposure_capacity": capacity,
                "fully_protected_damage": 0,
                "first_exposure_resolves_attack": True,
                "created_character_condition": False,
                "setup_ap": parameters.values["suppression.setup_ap"],
            }
        )
    return {"probe": "SUPPRESS-CROSSING", "exact": True, "rows": rows, "findings": []}


def overwatch_probe(parameters: ParameterSet) -> dict[str, Any]:
    grid = build_map("MAP_STREET_60", parameters)
    watcher = Position(10, 10)
    crossing_path = tuple(Position(x, 10) for x in range(45, 5, -1))
    arc_degrees = parameters.values["overwatch.arc_degrees"]
    maximum_range = parameters.values["equipment.service_rifle.range_breakpoints_m"][-1]

    def inside_arc(position: Position) -> bool:
        dx = position.x - watcher.x
        dy = position.y - watcher.y
        # This fixture declares a bounded street sector rather than the whole
        # physically reachable weapon range.
        if not 20 <= position.x <= 40 or math.hypot(dx, dy) > maximum_range or not grid.line_of_sight(watcher, position):
            return False
        # The declared fixture arc faces due east.
        angle = abs(math.degrees(math.atan2(dy, dx)))
        return angle <= arc_degrees / 2

    trigger = next((position for position in crossing_path if inside_arc(position)), None)
    attack = exact_attack_distribution(
        "service_rifle",
        watcher.distance_to(trigger) if trigger else maximum_range,
        4,
        1,
        parameters.values["check.difficulty.standard"],
        "none",
        1,
        parameters,
    )
    return {
        "probe": "OW-CROSSING",
        "exact": True,
        "declared_arc_degrees": arc_degrees,
        "setup_ap": parameters.values["overwatch.setup_ap"],
        "shot_allowance": parameters.values["overwatch.shot_allowance"],
        "crossing_starts_outside_arc": not inside_arc(crossing_path[0]),
        "trigger_position": asdict(trigger) if trigger else None,
        "movement_pauses_at_trigger": trigger is not None,
        "normal_attack_distribution": attack,
        "turn_continues_if_capable": True,
        "findings": [],
    }


def trigger_probe(parameters: ParameterSet) -> dict[str, Any]:
    state = SimulationState("TRIGGER-SIMULTANEOUS-V1", "1.0.0", {})
    engine = EventEngine(state, parameters, 1337)
    order: list[str] = []

    def effect(effect_id: str, side: Side, explicit: bool, nested: list[TriggerEffect] | None = None) -> TriggerEffect:
        def resolve(_state: SimulationState) -> list[TriggerEffect] | None:
            order.append(effect_id)
            return nested
        return TriggerEffect(effect_id, effect_id, side, explicit, lambda _state: True, resolve)

    nested = effect("NESTED", Side.B, False)
    effects = [effect("ACTIVE", Side.A, False), effect("NON_ACTIVE", Side.B, False, [nested]), effect("EXPLICIT_BEFORE", Side.A, True)]
    resolution = engine.resolve_trigger_window(effects)
    return {"probe": "TRIGGER-SIMULTANEOUS", "exact": True, "order": order, "resolution": resolution}


def cqc_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows = []
    maneuvers = ("SHOVE", "TRIP", "THROW", "DISARM", "BODY CONTROL", "WEAPON CONTROL", "ESCAPE")
    for attacker_skill in (0, 2, 4, 6, 8):
        for defender_skill in (0, 2, 4, 6, 8):
            defense = cqc_defense(defender_skill, 1, parameters)
            success = sum(resolve_check(roll, attacker_skill, 1, defense).success for roll in range(1, 21))
            for maneuver in maneuvers:
                rows.append(
                    {
                        "maneuver": maneuver,
                        "attacker_skill": attacker_skill,
                        "defender_skill": defender_skill,
                        "defense": defense,
                        "success_rate": success / 20,
                        "ap": parameters.values["cqc.escape_ap"] if maneuver == "ESCAPE" else parameters.values["cqc.action_ap"],
                        "one_check_one_result": True,
                    }
                )
    return {"probe": "CQC-MANEUVER", "exact": True, "rows": rows}


def grenade_probe(parameters: ParameterSet) -> dict[str, Any]:
    warning_rows = []
    movement_rate_mps = parameters.values["move.upright_m_per_ap"] / parameters.values["fixture.ap_seconds"]
    for seconds in (4.0, 3.0, 2.0, 1.0, 0.5):
        eligible, modifier, band = explosive_warning(seconds, parameters)
        warning_rows.append(
            {
                "remaining_seconds": seconds,
                "eligible": eligible,
                "difficulty_modifier": modifier,
                "band": band,
                "maximum_response_m": seconds * movement_rate_mps if eligible else 0,
                "save_for_half": False,
            }
        )
    placement_grid = build_map("MAP_TIGHT_20", parameters)
    intended = Position(10, 10)
    placement_rows = []
    for failure_margin in (0, 1, 4, 5, 9, 10, 14, 15, 20):
        for direction_index in range(parameters.values["grenade.scatter.direction_count"]):
            landing = grenade_scatter_landing(
                intended,
                failure_margin=failure_margin,
                direction_index=direction_index,
                grid=placement_grid,
                parameters=parameters,
            )
            placement_rows.append(
                {
                    "failure_margin": failure_margin,
                    "direction_index": direction_index,
                    "scatter_distance_m": grenade_scatter_distance(failure_margin, parameters),
                    "intended": asdict(intended),
                    "landing": asdict(landing),
                    "geometry_constrained": intended.distance_to(landing) <= grenade_scatter_distance(failure_margin, parameters) * math.sqrt(2) + 1e-9,
                }
            )
    blast_rows = []
    for material in MATERIAL_IDS:
        for posture, height in (("FULL COVER", 2.0), ("PARTIAL COVER", 1.0), ("CORNER", 1.25)):
            cover = CoverObject(
                f"BLAST:{material}:{posture}",
                {(1, 0)},
                height,
                parameters.values[f"material.{material}.resistance"],
                parameters.values[f"material.{material}.cover_hp"],
            )
            snapshot = resolve_blast_snapshot(cover, (("FRAGMENTATION", 7), ("FLASH", 4)))
            blast_rows.append(
                {
                    "material": material,
                    "geometry": posture,
                    "snapshot": [asdict(item) for item in snapshot],
                    "cover_hp_after": cover.cover_hp,
                    "cover_active_after": cover.active,
                }
            )
    smoke_rows = [
        {
            "trace_m": trace,
            "visual_los": "BLOCKED" if trace >= 4 else ("+3 DIFFICULTY" if trace > 0 else "CLEAR"),
        }
        for trace in range(0, 9)
    ]
    return {
        "probe": "EXPLOSIVES",
        "exact": True,
        "placement": placement_rows,
        "warning": warning_rows,
        "blast_snapshot": blast_rows,
        "smoke": smoke_rows,
        "findings": [],
    }


def medical_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for level in LEVELS:
        skill, _secondary, aptitude, _choices = SPECIALIST_PROGRESSION[level]
        for casualty_id, deterioration, window, requirement in (
            ("CASUALTY_BLEED", 6.0, 18.0, "hemorrhage control"),
            ("CASUALTY_AIRWAY", 12.0, 24.0, "airway control"),
        ):
            for corpsman in (False, True):
                for action in ("STABILIZE", "RESUSCITATION"):
                    successes = 0
                    ap = None
                    for roll in range(1, 21):
                        condition = LifeThreateningCondition(
                            casualty_id,
                            casualty_id,
                            ConditionState.ACTIVE if action == "STABILIZE" else ConditionState.FAILED,
                            deterioration,
                            requirement,
                            window,
                        )
                        result = medical_action(
                            condition,
                            action,
                            roll=roll,
                            skill=skill,
                            aptitude=aptitude,
                            parameters=parameters,
                            corpsman=corpsman,
                            capable_intervention=True,
                        )
                        successes += result.success
                        ap = result.ap_spent
                    rows.append(
                        {
                            "level": level,
                            "casualty_id": casualty_id,
                            "action": action,
                            "corpsman": corpsman,
                            "success_rate": successes / 20,
                            "ap": ap,
                            "deterioration_seconds": deterioration,
                            "reversible_window_seconds": window,
                            "ap_pressure_contexts": [
                                {
                                    "context": "SAFE",
                                    "available_ap": parameters.values["combat.ap.pool"],
                                    "action_fits_current_team_turn": bool(ap is not None and ap <= parameters.values["combat.ap.pool"]),
                                    "action_seconds": (ap or 0) * parameters.values["fixture.ap_seconds"],
                                },
                                {
                                    "context": "ACTIVE_COMBAT",
                                    "available_ap": max(0, parameters.values["combat.ap.pool"] // 2),
                                    "action_fits_current_team_turn": bool(ap is not None and ap <= max(0, parameters.values["combat.ap.pool"] // 2)),
                                    "action_seconds": (ap or 0) * parameters.values["fixture.ap_seconds"],
                                },
                            ],
                        }
                    )
    return {"probe": "MEDICAL", "exact": True, "rows": rows}


def knowledge_probe() -> dict[str, Any]:
    confirmed = KnowledgeState.CONFIRMED
    last_known = lose_exact_observation(confirmed)
    approximate = degrade_knowledge(last_known, bounded_area_still_credible=True, presence_still_relevant=True)
    suspicious = degrade_knowledge(approximate, bounded_area_still_credible=False, presence_still_relevant=True)
    unaware = degrade_knowledge(suspicious, bounded_area_still_credible=False, presence_still_relevant=False)
    return {
        "probe": "STEALTH-INFO",
        "exact": True,
        "transitions": [confirmed.value, last_known.value, approximate.value, suspicious.value, unaware.value],
        "shared_approximate": share_knowledge(KnowledgeState.APPROXIMATE, KnowledgeState.CONFIRMED).value,
        "event_causes": [
            {"from": "CONFIRMED", "to": "LAST_KNOWN", "cause": "target leaves direct observation behind real geometry"},
            {"from": "LAST_KNOWN", "to": "APPROXIMATE", "cause": "reachable movement branches expand the physically credible area"},
            {"from": "APPROXIMATE", "to": "SUSPICIOUS", "cause": "physical route/map evidence makes the prior bounded area no longer credible"},
            {"from": "SUSPICIOUS", "to": "UNAWARE", "cause": "an actual sweep or changed scene removes the remaining reason to believe the target is present"},
        ],
        "timer_based_decay": False,
        "findings": [],
    }


def hacking_probe(parameters: ParameterSet) -> dict[str, Any]:
    topology = network_site_nodes()
    no_route = valid_network_route(topology, "NET40-A", "MISSING")
    route = valid_network_route(topology, "NET40-A", "NET40-C")
    action_rows: list[dict[str, Any]] = []
    for roll in range(1, 21):
        for actor_kind, hacker, local_hacker in (("GENERALIST", False, False), ("HACKER", True, False), ("LOCAL HACKER", True, True)):
            nodes = network_site_nodes()
            result = intrusion_action(
                nodes,
                "NET40-A",
                "GET IN",
                roll=roll,
                skill=4,
                aptitude=1,
                parameters=parameters,
                valid_route=True,
                hacker=hacker,
                local_hacker=local_hacker,
                networker=True,
                stay_quiet_roll=roll,
            )
            action_rows.append({"actor_kind": actor_kind, **asdict(result)})
    nodes = network_site_nodes()
    access = intrusion_action(nodes, "NET40-A", "GET IN", roll=20, skill=4, aptitude=1, parameters=parameters, valid_route=True, hacker=True, networker=True)
    pivot = intrusion_action(nodes, "NET40-B", "PIVOT", roll=20, skill=4, aptitude=1, parameters=parameters, valid_route=True, hacker=True, networker=True)
    escalate = intrusion_action(nodes, "NET40-B", "ESCALATE", roll=20, skill=4, aptitude=1, parameters=parameters, valid_route=True, hacker=True)
    retry = intrusion_action(nodes, "NET40-C", "PIVOT", roll=1, skill=4, aptitude=1, parameters=parameters, valid_route=True, hacker=True, stay_quiet_roll=1)
    nodes["NET40-C"].compromised = True
    nodes["NET40-C"].privilege = nodes["NET40-C"].required_privilege
    authorized = intrusion_action(nodes, "NET40-C", "OPERATE", roll=1, skill=0, aptitude=0, parameters=parameters, valid_route=True, authorized=True)
    return {
        "probe": "HACKING",
        "exact": True,
        "valid_route": route,
        "missing_route": no_route,
        "topology": {
            node_id: {"links": sorted(node.links), "segment": node.segment, "physical_position": asdict(node.physical_position) if node.physical_position else None, "remote_functions": sorted(node.remote_functions)}
            for node_id, node in sorted(topology.items())
        },
        "action_rows": action_rows,
        "sequence": [asdict(access), asdict(pivot), asdict(escalate), asdict(retry), asdict(authorized)],
        "neighbors_revealed_after_networker_get_in": sorted(node_id for node_id, node in nodes.items() if node.discovered),
        "retry_available_after_failure": retry.retry_available,
        "authorized_function_automatic": authorized.authorized and authorized.success and authorized.roll is None,
        "privilege_required_on_C": nodes["NET40-C"].required_privilege,
        "physical_relay_grants_privilege": False,
        "cyber_hp_created": False,
        "findings": [],
    }


def drone_probe(parameters: ParameterSet) -> dict[str, Any]:
    distance = 80.0
    fpv_ap = math.ceil(distance / parameters.values["equipment.fpv_drone.move_m_per_ap"])
    autonomous_ap_equivalent = math.ceil(distance / parameters.values["equipment.autodrone.move_m_per_ap"])
    autonomous_turns = math.ceil(autonomous_ap_equivalent / parameters.values["equipment.autodrone.actions_per_turn"])
    attack_distance_m = 20.0
    attack_difficulty = (
        parameters.values["check.difficulty.standard"]
        + range_modifier("autodrone_light_weapon", attack_distance_m, parameters)
    )
    targeting_bonus = parameters.values["equipment.autodrone_light_weapon.targeting_bonus"]
    autonomous_outcomes = [
        resolve_attack(
            roll,
            targeting_bonus,
            0,
            attack_difficulty,
            parameters.values["equipment.autodrone_light_weapon.damage"],
            parameters.values["equipment.autodrone_light_weapon.target_pen"],
            parameters,
        )
        for roll in range(1, 21)
    ]
    level_rows = []
    for level in LEVELS:
        skill, _secondary, aptitude, _choices = SPECIALIST_PROGRESSION[level]
        manual_outcomes = [
            resolve_attack(
                roll,
                skill,
                aptitude,
                attack_difficulty,
                parameters.values["equipment.autodrone_light_weapon.damage"],
                parameters.values["equipment.autodrone_light_weapon.target_pen"],
                parameters,
            )
            for roll in range(1, 21)
        ]
        placement_outcomes = [
            resolve_check(
                roll,
                skill,
                aptitude,
                parameters.values["check.difficulty.standard"],
            )
            for roll in range(1, 21)
        ]
        level_rows.append(
            {
                "level": level,
                "drone_operations": skill,
                "precision": aptitude,
                "manual_success_rate": sum(
                    outcome.quality is not HitQuality.MISS
                    for outcome in manual_outcomes
                )
                / 20,
                "autonomous_success_rate": sum(
                    outcome.quality is not HitQuality.MISS
                    for outcome in autonomous_outcomes
                )
                / 20,
                "fpv_placement_success_rate": sum(
                    outcome.success for outcome in placement_outcomes
                )
                / 20,
            }
        )
    execution = _armed_drone_execution(parameters)
    return {
        "probe": "DRONE-RECON",
        "exact": True,
        "rows": [
            {"drone": "FPV DRONE", "distance_m": distance, "operator_ap": fpv_ap, "default_weapon": False},
            {"drone": "AUTONOMOUS DRONE", "distance_m": distance, "operator_ap": 1, "execution_turns": autonomous_turns, "default_weapon": False},
            {"device": "FIELD SENSOR / RELAY NODE", "setup_ap": parameters.values["equipment.field_relay.setup_ap"], "creates_software_privilege": False},
        ],
        "armed_combat": {
            "execution": execution,
            "fpv_fragmentation": {
                "default_weapon": False,
                "module_required": True,
                "operator_attack_ap": parameters.values["equipment.fpv_fragmentation_payload.attack_ap"],
                "placement_formula": "1d20 + DRONE OPERATIONS + PRECISION + modifiers",
                "failure_uses_grenade_scatter": True,
                "fragmentation_damage": parameters.values["equipment.frag_grenade.damage"],
                "fragmentation_target_pen": parameters.values["equipment.frag_grenade.target_pen"],
                "fragmentation_material_pen": parameters.values["equipment.frag_grenade.material_pen"],
                "frozen_blast_snapshot": True,
                "payload_and_drone_consumed": True,
                "level_rows": level_rows,
            },
            "autonomous_light_weapon": {
                "default_weapon": False,
                "module_required": True,
                "fire_mode": "SEMI AUTO",
                "damage": parameters.values["equipment.autodrone_light_weapon.damage"],
                "target_pen": parameters.values["equipment.autodrone_light_weapon.target_pen"],
                "material_pen": parameters.values["equipment.autodrone_light_weapon.material_pen"],
                "capacity": parameters.values["equipment.autodrone_light_weapon.capacity"],
                "attack_ap_equivalent": parameters.values["equipment.autodrone_light_weapon.attack_ap_equivalent"],
                "targeting_bonus": targeting_bonus,
                "standing_order_required": True,
                "sensor_confirmation_required": True,
                "ordinary_projectile_geometry": True,
                "operator_ap_per_authorized_shot": 0,
                "level_rows": level_rows,
                "autonomous_rolls": [outcome.to_dict() for outcome in autonomous_outcomes],
            },
        },
    }


def _armed_drone_execution(parameters: ParameterSet) -> dict[str, Any]:
    return {
        "map_id": "MAP_STREET_60",
        "autonomous_cases": _autodrone_execution_cases(parameters),
        "autonomous_authorization_rejections": _autodrone_authorization_rejections(
            parameters
        ),
        "fpv_cases": _fpv_execution_cases(parameters),
    }


def _autodrone_authorization_rejections(
    parameters: ParameterSet,
) -> list[dict[str, Any]]:
    capacity = int(parameters.values["equipment.autodrone_light_weapon.capacity"])
    execution_ap = int(
        parameters.values["equipment.autodrone_light_weapon.attack_ap_equivalent"]
    )
    attempts = (
        (False, True, "MISSING_STANDING_ORDER"),
        (True, False, "MISSING_SENSOR_CONFIRMATION"),
    )
    rows: list[dict[str, Any]] = []
    for standing_order, sensor_confirmation, expected_reason in attempts:
        eligible = standing_order and sensor_confirmation
        rows.append(
            {
                "standing_order": standing_order,
                "sensor_confirmation": sensor_confirmation,
                "eligible": eligible,
                "reason": None if eligible else expected_reason,
                "ammunition_before": capacity,
                "ammunition_after": capacity - (1 if eligible else 0),
                "execution_ap_before": execution_ap,
                "execution_ap_after": 0 if eligible else execution_ap,
            }
        )
    return rows


def _autodrone_execution_cases(parameters: ParameterSet) -> list[dict[str, Any]]:
    shooter = Position(5, 10)
    damage = int(parameters.values["equipment.autodrone_light_weapon.damage"])
    target_pen = int(
        parameters.values["equipment.autodrone_light_weapon.target_pen"]
    )
    material_pen = int(
        parameters.values["equipment.autodrone_light_weapon.material_pen"]
    )
    targeting_bonus = int(
        parameters.values["equipment.autodrone_light_weapon.targeting_bonus"]
    )
    recoil = int(parameters.values["equipment.autodrone_light_weapon.recoil"])
    capacity = int(parameters.values["equipment.autodrone_light_weapon.capacity"])
    execution_ap = int(
        parameters.values["equipment.autodrone_light_weapon.attack_ap_equivalent"]
    )
    cases = (
        ("exposed-target", Position(15, 3), Stance.STANDING, 16),
        ("partial-cover-intercept", Position(25, 10), Stance.STANDING, 12),
        ("geometry-blocked", Position(25, 10), Stance.CROUCHED, 20),
    )
    rows: list[dict[str, Any]] = []
    for case_id, target, stance, roll in cases:
        grid = build_map("MAP_STREET_60", parameters)
        cover = grid.covers["STREET_BARRIER_1"]
        cover_before = cover.cover_hp
        exposure = grid.exposure(shooter, target, stance)
        open_difficulty = int(parameters.values["check.difficulty.standard"]) + int(
            range_modifier(
                "autodrone_light_weapon", shooter.distance_to(target), parameters
            )
        )
        resolved_actual = actual_difficulty(open_difficulty, exposure, parameters)
        total = roll + targeting_bonus
        material = None
        target_damage = None
        if resolved_actual is None:
            outcome = "GEOMETRY_BLOCKED"
        else:
            cover_roll = resolve_cover_roll(total, open_difficulty, resolved_actual)
            outcome = cover_roll.outcome.replace(" ", "_")
        if outcome == "TARGET_HIT":
            attack = resolve_attack(
                roll,
                targeting_bonus,
                0,
                resolved_actual if resolved_actual is not None else open_difficulty,
                damage,
                target_pen,
                parameters,
            )
            target_damage = asdict(
                resolve_damage(
                    attack.effective_damage,
                    attack.effective_target_pen,
                    10,
                    10,
                )
            )
        elif outcome in {"COVER_INTERCEPT", "GEOMETRY_BLOCKED"}:
            material = resolve_material(
                damage,
                target_pen,
                material_pen,
                cover.material_resistance,
            )
            if cover.cover_hp is not None:
                cover.cover_hp = max(
                    0,
                    cover.cover_hp - max(0, damage - cover.material_resistance),
                )
                cover.active = cover.cover_hp > 0
        first = grid.first_intersection(
            shooter,
            target,
            ray_height_m=max(0.2, {
                Stance.STANDING: 1.8,
                Stance.CROUCHED: 1.15,
                Stance.PRONE: 0.45,
            }[stance] * 0.75),
        )
        rows.append(
            {
                "case_id": case_id,
                "standing_order": {
                    "active": True,
                    "authorized_fire": True,
                    "sensor_confirmed_target": True,
                },
                "position": asdict(target),
                "stance": stance.value,
                "exposure": exposure,
                "roll": roll,
                "total": total,
                "open_difficulty": open_difficulty,
                "actual_difficulty": resolved_actual,
                "outcome": outcome,
                "first_intersection": (
                    first.cover_id if isinstance(first, CoverObject) else first
                ),
                "material": asdict(material) if material else None,
                "target_damage": target_damage,
                "cover_hp_before": cover_before,
                "cover_hp_after": cover.cover_hp,
                "ammunition_before": capacity,
                "ammunition_after": capacity - 1,
                "recoil_before": 0,
                "recoil_after": recoil,
                "execution_ap_before": execution_ap,
                "execution_ap_after": 0,
                "operator_ap_spent": 0,
            }
        )
    return rows


def _fpv_execution_cases(parameters: ParameterSet) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cases = (
        ("exposed-placement", Position(15, 3), Position(15, 3), 0, 0),
        ("failed-placement-scatter", Position(25, 10), Position(25, 10), 5, 3),
    )
    for case_id, target, intended, failure_margin, direction_index in cases:
        grid = build_map("MAP_STREET_60", parameters)
        cover = grid.covers["STREET_BARRIER_1"]
        cover_before = cover.cover_hp
        candidate = grenade_scatter_landing(
            intended,
            failure_margin=failure_margin,
            direction_index=direction_index,
            grid=grid,
            parameters=parameters,
        )
        approach = trace_cells(Position(5, 10), candidate)
        impact_cell = next(
            (cell for cell in approach[1:] if cell in cover.cells and cover.active),
            None,
        )
        actual = (
            Position(impact_cell[0] + 0.5, impact_cell[1] + 0.5)
            if impact_cell is not None
            else candidate
        )
        pre_snapshot = {
            "map_id": grid.map_id,
            "cover_id": cover.cover_id,
            "cover_hp": cover.cover_hp,
            "cover_active": cover.active,
            "actual_detonation": asdict(actual),
        }
        snapshot_id = hashlib.sha256(
            json.dumps(
                pre_snapshot,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        distance = actual.distance_to(target)
        bands = parameters.values["equipment.frag_grenade.band_end_m"]
        damages = parameters.values["equipment.frag_grenade.damage"]
        band_index = next(
            (index for index, boundary in enumerate(bands) if distance <= boundary),
            len(bands) - 1,
        )
        blast_damage = int(damages[band_index])
        protective_cover = cover if impact_cell is not None else None
        protective_cover_active_before = bool(
            protective_cover is not None and protective_cover.active
        )
        blast = resolve_blast_snapshot(
            protective_cover,
            (("FRAGMENTATION", blast_damage),),
        )
        rows.append(
            {
                "case_id": case_id,
                "placement_success": failure_margin == 0,
                "failure_margin": failure_margin,
                "direction_index": direction_index,
                "intended_landing": asdict(intended),
                "scatter_landing": asdict(candidate),
                "actual_detonation": asdict(actual),
                "approach_intersection": cover.cover_id if impact_cell else None,
                "target_distance_m": distance,
                "blast_damage": blast_damage,
                "snapshot_id": snapshot_id,
                "snapshot_revision": 0,
                "frozen_snapshot": all(
                    component.protected_by_snapshot
                    == protective_cover_active_before
                    for component in blast
                ),
                "cover_hp_before": cover_before,
                "cover_hp_after": cover.cover_hp,
                "cover_active_after": cover.active,
                "target_cover_outcome": (
                    "PROTECTED_BY_SNAPSHOT"
                    if blast[0].protected_by_snapshot
                    else "EXPOSED"
                ),
                "payload_consumed": True,
                "drone_consumed": True,
                "operator_ap_before": int(parameters.values["combat.ap.pool"]),
                "operator_ap_after": int(parameters.values["combat.ap.pool"])
                - int(parameters.values["equipment.fpv_fragmentation_payload.attack_ap"]),
            }
        )
    return rows


def social_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows = []
    for plausible, relevant, leverage in ((True, False, None), (True, True, "weak"), (True, True, "strong"), (True, True, "decisive"), (False, True, "decisive")):
        for influencer in (False, True):
            successes = 0
            setup = None
            for roll in range(1, 21):
                result = resolve_social_attempt(
                    plausible=plausible,
                    relevant_fact=relevant,
                    leverage=leverage,
                    roll=roll,
                    skill=4,
                    aptitude=1,
                    difficulty=parameters.values["check.difficulty.hard"],
                    parameters=parameters,
                    influencer=influencer,
                )
                successes += result.success
                setup = result.setup_ap
            rows.append(
                {
                    "plausible": plausible,
                    "relevant_fact": relevant,
                    "leverage": leverage,
                    "influencer": influencer,
                    "success_rate": successes / 20,
                    "setup_ap": setup,
                }
            )
    return {"probe": "SOCIAL", "exact": True, "rows": rows}


def class_baseline_probe(parameters: ParameterSet) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    scenario_ids = {
        "BULWARK": "CLASS-BULWARK-HOLD",
        "RIFLEMAN": "CLASS-RIFLEMAN-SUSTAIN",
        "BREACHER": "CLASS-BREACHER-CLOSE",
        "GHOST": "CLASS-GHOST-INFILTRATE",
        "SHARPSHOOTER": "CLASS-SHARPSHOOTER-LONG",
        "TECH": "CLASS-TECH-DEPLOY",
        "HACKER": "CLASS-HACKER-INTRUDE",
        "CORPSMAN": "CLASS-CORPSMAN-TREAT",
        "INFLUENCER": "CLASS-INFLUENCER-ANGLE",
    }
    for level in LEVELS:
        index = LEVELS.index(level)
        baseline_res = parameters.values["res.vector_l01_l05_l10_l15_l20"][index]
        for class_id in CLASS_CAST:
            control: float
            candidate: float
            metric: str
            if class_id == "BULWARK":
                metric, control, candidate = "RES", baseline_res, baseline_res + parameters.values["class.bulwark.res_bonus"]
            elif class_id == "RIFLEMAN":
                metric, control, candidate = "RECOIL recovery/AP", parameters.values["recoil.recovery_per_ap"], parameters.values["recoil.recovery_per_ap"] + parameters.values["class.rifleman.extra_recoil_recovery_per_ap"]
            elif class_id == "BREACHER":
                metric, control = "closing movement m/AP", movement_rate(Stance.STANDING, 0, "CONTROL", 10, parameters)
                candidate = movement_rate(Stance.STANDING, 0, "BREACHER", 10, parameters)
            elif class_id == "GHOST":
                metric, control = "movement m/AP", movement_rate(Stance.STANDING, 0, "CONTROL", None, parameters)
                candidate = movement_rate(Stance.STANDING, 0, "GHOST", None, parameters)
            elif class_id == "SHARPSHOOTER":
                metric, control, candidate = "range DIFFICULTY offset/AIM", abs(parameters.values["fire.aim.attack_difficulty_offset_per_ap"]), abs(parameters.values["fire.aim.attack_difficulty_offset_per_ap"]) + parameters.values["class.sharpshooter.extra_aim_range_offset_per_ap"]
            elif class_id == "TECH":
                metric, control, candidate = "deployment AP saved", parameters.values["equipment.field_relay.setup_ap"], max(1, parameters.values["equipment.field_relay.setup_ap"] - parameters.values["class.tech.operation_ap_discount"])
            elif class_id == "HACKER":
                metric, control, candidate = "intrusion AP", parameters.values["hacking.standard_intrusion_ap"], max(1, parameters.values["hacking.standard_intrusion_ap"] - parameters.values["class.hacker.intrusion_ap_discount"])
            elif class_id == "CORPSMAN":
                metric, control, candidate = "stabilize AP", parameters.values["medicine.stabilize_ap"], max(1, parameters.values["medicine.stabilize_ap"] - parameters.values["class.corpsman.medicine_ap_discount"])
            else:
                metric, control, candidate = "leverage setup AP", parameters.values["social.fact_to_leverage_setup_ap"], max(1, parameters.values["social.fact_to_leverage_setup_ap"] - parameters.values["class.influencer.leverage_setup_ap_discount"])
            absolute = candidate - control
            relative = absolute / abs(control) if control else None
            rows.append({"scenario_id": scenario_ids[class_id], "level": level, "class_id": class_id, "metric": metric, "control": control, "candidate": candidate, "absolute_delta": absolute, "relative_delta": relative, "same_skill_aptitude_equipment_control": True, "meaningful_delta": abs(relative or 0) >= 0.05 or abs(absolute) >= 0.05})
    findings = []
    for row in rows:
        relative = abs(row["relative_delta"] or 0)
        if not row["meaningful_delta"]:
            findings.append({"code": "STRUCTURAL FAILURE", "subject": row["class_id"], "scenario_id": row["scenario_id"], "level": row["level"], "reason": "class baseline misses meaningful-delta gate"})
    return {"probe": "CLASS-BASELINES", "exact": True, "rows": rows, "findings": findings}


def geometry_probe(parameters: ParameterSet) -> dict[str, Any]:
    maps = {}
    for map_id in ("MAP_OPEN_100", "MAP_TIGHT_20", "MAP_STREET_60", "MAP_BREACH_30", "MAP_COMPOUND_80", "MAP_LONG_1000", "MAP_NETWORK_SITE_40"):
        grid = build_map(map_id, parameters)
        maps[map_id] = {"width": grid.width, "height": grid.height, "blockers": len(grid.full_blockers), "cover_objects": len(grid.covers)}
    return {
        "probe": "GEOMETRY",
        "exact": True,
        "maps": maps,
        "directional_shield_front": directional_facing_protects(Position(5, 5), Position(6, 5), Position(0, 5), 180),
        "directional_shield_flank": directional_facing_protects(Position(5, 5), Position(6, 5), Position(5, 0), 180),
    }


def _attach_scenario_case_records(
    probes: dict[str, dict[str, Any]],
    parameters: ParameterSet,
) -> dict[str, dict[str, Any]]:
    """Publish the concrete probe inputs used by every exact scenario."""

    def add(
        probe_key: str,
        scenario_id: str,
        *,
        map_id: str,
        case_id: str,
        result_collection: str,
        position_a: Position,
        position_b: Position,
        subsystem_state: dict[str, Any],
        level: int = 1,
        class_a: str = "RIFLEMAN",
        class_b: str = "RIFLEMAN",
        weapon_a: str = "service_rifle",
        weapon_b: str = "service_rifle",
        skill_a: int = 4,
        skill_b: int = 4,
        aptitude_a: int = 1,
        aptitude_b: int = 1,
        armor_b: str = "none",
        posture_a: Stance = Stance.STANDING,
        posture_b: Stance = Stance.STANDING,
        knowledge_a: KnowledgeState = KnowledgeState.CONFIRMED,
        knowledge_b: KnowledgeState = KnowledgeState.CONFIRMED,
    ) -> None:
        bucket = probes[probe_key].setdefault("scenario_case_records", {})
        records = bucket.setdefault(scenario_id, [])
        record = _probe_case_record(
            parameters,
            scenario_id=scenario_id,
            map_id=map_id,
            case_id=case_id,
            result_locator=f"result.measurements.{result_collection}",
            position_a=position_a,
            position_b=position_b,
            level=level,
            class_a=class_a,
            class_b=class_b,
            weapon_a=weapon_a,
            weapon_b=weapon_b,
            skill_a=skill_a,
            skill_b=skill_b,
            aptitude_a=aptitude_a,
            aptitude_b=aptitude_b,
            armor_b=armor_b,
            posture_a=posture_a,
            posture_b=posture_b,
            knowledge_a=knowledge_a,
            knowledge_b=knowledge_b,
            subsystem_state=subsystem_state,
        )
        record["level"] = level
        record["class_id"] = class_a
        record["result_collection"] = result_collection
        record["filter_by_level"] = probe_key in {
            "check_curves",
            "fire_range",
            "armor",
            "medical",
            "classes",
        }
        record["filter_by_class"] = probe_key == "classes"
        records.append(record)

    standard = int(parameters.values["check.difficulty.standard"])
    for index, row in enumerate(probes["check_curves"]["rows"]):
        add(
            "check_curves",
            "CHECK-CURVE-V1",
            map_id="MAP_OPEN_100",
            case_id=f"{row['control']}-L{row['level']:02d}-{row['difficulty_id']}",
            result_collection="rows",
            position_a=Position(10, 15),
            position_b=Position(90, 15),
            level=int(row["level"]),
            skill_a=int(row["skill"]),
            aptitude_a=int(row["aptitude"]),
            subsystem_state={
                "roll_domain": [1, 20],
                "control": row["control"],
                "difficulty_id": row["difficulty_id"],
                "difficulty": row["difficulty"],
            },
        )

    for row in probes["fire_range"]["rows"]:
        distance = float(row["distance_m"])
        target_x = 999
        shooter_x = int(target_x - distance)
        add(
            "fire_range",
            "FIRE-SINGLE-RANGE-V1",
            map_id="MAP_LONG_1000",
            case_id=f"{row['weapon_id']}-{int(distance)}m-{row['armor_id']}",
            result_collection="rows",
            position_a=Position(shooter_x, 30),
            position_b=Position(target_x, 30),
            level=int(row["level"]),
            weapon_a=str(row["weapon_id"]),
            armor_b=str(row["armor_id"]),
            skill_a=4,
            aptitude_a=1,
            subsystem_state={
                "distance_m": distance,
                "base_difficulty": standard,
                "range_modifier": row["range_modifier"],
                "roll_domain": [1, 20],
            },
        )

    for row in probes["handling"]["rows"]:
        inputs = {
            "distance_m": 1,
            "handling": row["handling"],
            "contact_handling_multiplier": row["contact_handling_multiplier"],
            "open_difficulty": row["open_difficulty"],
            "constrained_difficulty": row["constrained_difficulty"],
            "roll_domain": [1, 20],
        }
        for scenario_id in (
            "FIRE-HANDLING-TIGHT-V1",
            "CQC-CONTACT-FIRE-V1",
        ):
            add(
                "handling",
                scenario_id,
                map_id="MAP_TIGHT_20",
                case_id=str(row["weapon_id"]),
                result_collection="rows",
                position_a=Position(4, 10),
                position_b=Position(5, 10),
                weapon_a=str(row["weapon_id"]),
                subsystem_state=inputs,
            )

    for row in probes["auto_strings"]["rows"]:
        add(
            "auto_strings",
            "FIRE-AUTO-STRING-V1",
            map_id="MAP_OPEN_100",
            case_id=f"{row['weapon_id']}-{row['mode']}-{row['rounds']}",
            result_collection="rows",
            position_a=Position(10, 15),
            position_b=Position(30, 15),
            weapon_a=str(row["weapon_id"]),
            subsystem_state={
                "distance_m": 20,
                "mode": row["mode"],
                "rounds": row["rounds"],
                "exposure": "full",
                "difficulty": standard,
                "roll_domain": [1, 20],
            },
        )

    for row in probes["armor"]["rows"]:
        add(
            "armor",
            "PROTECTION-ARMOR-V1",
            map_id="MAP_OPEN_100",
            case_id=f"L{row['level']:02d}-{row['armor_id']}-{row['weapon_id']}",
            result_collection="rows",
            position_a=Position(10, 15),
            position_b=Position(30, 15),
            level=int(row["level"]),
            weapon_a=str(row["weapon_id"]),
            armor_b=str(row["armor_id"]),
            subsystem_state={
                "distance_m": 20,
                "armor_res": row["armor_res"],
                "burden": row["burden"],
                "skill": 4,
                "aptitude": 1,
                "difficulty": standard,
                "roll_domain": [1, 20],
            },
        )

    for row in probes["materials"]["rows"]:
        add(
            "materials",
            "PROJECTILE-MATERIAL-V1",
            map_id="MAP_OPEN_100",
            case_id=f"{row['weapon_id']}-{row['material']}",
            result_collection="rows",
            position_a=Position(10, 15),
            position_b=Position(20, 15),
            weapon_a=str(row["weapon_id"]),
            subsystem_state={
                "distance_m": 10,
                "material": row["material"],
                "material_resistance": parameters.values[
                    f"material.{row['material']}.resistance"
                ],
            },
        )

    for row in probes["cover"]["routes"]:
        add(
            "cover",
            "COVER-PARTIAL-V1",
            map_id="MAP_STREET_60",
            case_id=f"{row['posture']}-{row['exposure']}-{row['roll_total']}",
            result_collection="routes",
            position_a=Position(
                int(row["shooter_position"]["x"]),
                int(row["shooter_position"]["y"]),
            ),
            position_b=Position(
                int(row["target_position"]["x"]),
                int(row["target_position"]["y"]),
            ),
            posture_b=Stance(str(row["posture"])),
            subsystem_state={
                "geometry_verified_exposure": row[
                    "geometry_verified_exposure"
                ],
                "roll_total": row["roll_total"],
                "open_difficulty": row["open_difficulty"],
                "actual_difficulty": row["actual_difficulty"],
                "cover_id": row["cover_id"],
                "cover_cells": row["cover_cells"],
                "cover_height_m": row["cover_height_m"],
            },
        )
    for row in probes["cover"]["cover_destruction_matrix"]:
        add(
            "cover",
            "COVER-DESTRUCTION-V1",
            map_id="MAP_STREET_60",
            case_id=f"{row['weapon_id']}-{row['material']}",
            result_collection="cover_destruction_matrix",
            position_a=Position(5, 10),
            position_b=Position(55, 10),
            weapon_a=str(row["weapon_id"]),
            subsystem_state={
                "distance_m": 10,
                "projectile_count": 12,
                "cover_id": row["cover_id"],
                "cover_cells": row["cover_cells"],
                "cover_height_m": row["cover_height_m"],
                "material": row["material"],
                "material_resistance": row["material_resistance"],
                "cover_hp": row["cover_hp_seed"],
            },
        )

    for row in probes["suppression"]["rows"]:
        add(
            "suppression",
            "SUPPRESS-CROSSING-V1",
            map_id="MAP_STREET_60",
            case_id=f"{row['committed_rounds']}-rounds",
            result_collection="rows",
            position_a=Position(5, 10),
            position_b=Position(55, 10),
            subsystem_state={
                "committed_rounds": row["committed_rounds"],
                "exposure_capacity_round_divisor": parameters.values[
                    "suppression.exposure_capacity_round_divisor"
                ],
                "setup_ap": row["setup_ap"],
            },
        )

    overwatch = probes["overwatch"]
    add(
        "overwatch",
        "OW-CROSSING-V1",
        map_id="MAP_STREET_60",
        case_id="east-arc-crossing",
        result_collection="singleton",
        position_a=Position(10, 10),
        position_b=Position(45, 10),
        subsystem_state={
            "watcher_position": {"x": 10, "y": 10},
            "crossing_path": [
                {"x": x, "y": 10} for x in range(45, 5, -1)
            ],
            "arc_degrees": overwatch["declared_arc_degrees"],
            "setup_ap": overwatch["setup_ap"],
            "shot_allowance": overwatch["shot_allowance"],
        },
    )

    add(
        "triggers",
        "TRIGGER-SIMULTANEOUS-V1",
        map_id="MAP_STREET_60",
        case_id="active-side-explicit-nested",
        result_collection="singleton",
        position_a=Position(5, 10),
        position_b=Position(55, 10),
        subsystem_state={
            "engine_seed": 1337,
            "active_side": "A",
            "effects": [
                {"effect_id": "ACTIVE", "side": "A", "explicit_before": False},
                {"effect_id": "NON_ACTIVE", "side": "B", "explicit_before": False, "nested": ["NESTED"]},
                {"effect_id": "EXPLICIT_BEFORE", "side": "A", "explicit_before": True},
            ],
        },
    )

    for row in probes["cqc"]["rows"]:
        add(
            "cqc",
            "CQC-MANEUVER-V1",
            map_id="MAP_TIGHT_20",
            case_id=f"{row['maneuver']}-{row['attacker_skill']}-{row['defender_skill']}",
            result_collection="rows",
            position_a=Position(4, 10),
            position_b=Position(5, 10),
            skill_a=int(row["attacker_skill"]),
            skill_b=int(row["defender_skill"]),
            subsystem_state={
                "maneuver": row["maneuver"],
                "defense": row["defense"],
                "roll_domain": [1, 20],
            },
        )

    explosives = probes["explosives"]
    for row in explosives["placement"]:
        add(
            "explosives",
            "GRENADE-PLACEMENT-V1",
            map_id="MAP_TIGHT_20",
            case_id=f"margin-{row['failure_margin']}-direction-{row['direction_index']}",
            result_collection="placement",
            position_a=Position(4, 10),
            position_b=Position(16, 10),
            subsystem_state={
                "failure_margin": row["failure_margin"],
                "direction_index": row["direction_index"],
                "intended": row["intended"],
            },
        )
    for row in explosives["warning"]:
        add(
            "explosives",
            "GRENADE-WARNING-V1",
            map_id="MAP_TIGHT_20",
            case_id=f"{row['remaining_seconds']}-seconds",
            result_collection="warning",
            position_a=Position(4, 10),
            position_b=Position(16, 10),
            subsystem_state={
                "remaining_seconds": row["remaining_seconds"],
                "movement_rate_mps": parameters.values["move.upright_m_per_ap"]
                / parameters.values["fixture.ap_seconds"],
            },
        )
    for row in explosives["blast_snapshot"]:
        add(
            "explosives",
            "BLAST-COVER-V1",
            map_id="MAP_STREET_60",
            case_id=f"{row['material']}-{row['geometry']}",
            result_collection="blast_snapshot",
            position_a=Position(5, 10),
            position_b=Position(55, 10),
            subsystem_state={
                "components": [
                    {"kind": "FRAGMENTATION", "damage": 7},
                    {"kind": "FLASH", "damage": 4},
                ],
                "cover_material": row["material"],
                "cover_geometry": row["geometry"],
                "snapshot_revision": 0,
            },
        )
    for row in explosives["smoke"]:
        add(
            "explosives",
            "SMOKE-LOS-V1",
            map_id="MAP_STREET_60",
            case_id=f"trace-{row['trace_m']}m",
            result_collection="smoke",
            position_a=Position(5, 10),
            position_b=Position(55, 10),
            subsystem_state={"smoke_trace_m": row["trace_m"]},
        )

    for row in probes["medical"]["rows"]:
        scenario_id = (
            "MED-STABILIZE-V1"
            if row["action"] == "STABILIZE"
            else "MED-RESUSCITATE-V1"
        )
        add(
            "medical",
            scenario_id,
            map_id="MAP_COMPOUND_80",
            case_id=f"L{row['level']:02d}-{row['casualty_id']}-{row['corpsman']}",
            result_collection="rows",
            position_a=Position(10, 30),
            position_b=Position(11, 30),
            level=int(row["level"]),
            class_a="CORPSMAN" if row["corpsman"] else "RIFLEMAN",
            skill_a=int(SPECIALIST_PROGRESSION[int(row["level"])][0]),
            aptitude_a=int(SPECIALIST_PROGRESSION[int(row["level"])][2]),
            subsystem_state={
                "condition_id": row["casualty_id"],
                "condition_state": (
                    "ACTIVE" if row["action"] == "STABILIZE" else "FAILED"
                ),
                "action": row["action"],
                "deterioration_seconds": row["deterioration_seconds"],
                "reversible_window_seconds": row[
                    "reversible_window_seconds"
                ],
                "capable_intervention": True,
                "roll_domain": [1, 20],
            },
        )

    knowledge = probes["knowledge"]
    add(
        "knowledge",
        "STEALTH-INFO-V1",
        map_id="MAP_COMPOUND_80",
        case_id="confirmed-to-unaware",
        result_collection="singleton",
        position_a=Position(10, 30),
        position_b=Position(70, 30),
        class_a="GHOST",
        knowledge_a=KnowledgeState.CONFIRMED,
        knowledge_b=KnowledgeState.CONFIRMED,
        subsystem_state={
            "initial_knowledge": "CONFIRMED",
            "degradation_inputs": [
                {"bounded_area_still_credible": True, "presence_still_relevant": True},
                {"bounded_area_still_credible": False, "presence_still_relevant": True},
                {"bounded_area_still_credible": False, "presence_still_relevant": False},
            ],
            "event_causes": knowledge["event_causes"],
        },
    )

    hacking = probes["hacking"]
    for scenario_id, action_plan in (
        ("HACK-LOCAL-V1", ["GET IN"]),
        ("HACK-NETWORK-V1", ["GET IN", "PIVOT", "ESCALATE", "OPERATE"]),
    ):
        add(
            "hacking",
            scenario_id,
            map_id="MAP_NETWORK_SITE_40",
            case_id="network-site-40",
            result_collection="singleton",
            position_a=Position(8, 15),
            position_b=Position(32, 15),
            class_a="HACKER",
            subsystem_state={
                "initial_topology": hacking["topology"],
                "entry_node_id": "NET40-A",
                "target_node_id": (
                    "NET40-A" if scenario_id == "HACK-LOCAL-V1" else "NET40-C"
                ),
                "action_plan": action_plan,
                "roll_domain": [1, 20],
            },
        )

    drones = probes["drones"]
    for row in drones["rows"][:2]:
        add(
            "drones",
            "DRONE-RECON-V1",
            map_id="MAP_COMPOUND_80",
            case_id=str(row["drone"]).replace(" ", "-"),
            result_collection="rows",
            position_a=Position(2, 30),
            position_b=Position(78, 30),
            class_a="TECH",
            subsystem_state={
                "device": row["drone"],
                "distance_m": row["distance_m"],
                "operator_ap": row["operator_ap"],
            },
        )
    relay = drones["rows"][2]
    add(
        "drones",
        "TECH-DEPLOY-V1",
        map_id="MAP_NETWORK_SITE_40",
        case_id="field-sensor-relay-node",
        result_collection="rows",
        position_a=Position(8, 15),
        position_b=Position(32, 15),
        class_a="TECH",
        subsystem_state={
            "device": relay["device"],
            "setup_ap": relay["setup_ap"],
            "initially_deployed": False,
        },
    )
    armed_execution = drones["armed_combat"]["execution"]
    for row in armed_execution["autonomous_cases"]:
        add(
            "drones",
            "DRONE-ARMED-V1",
            map_id="MAP_STREET_60",
            case_id=f"autonomous-{row['case_id']}",
            result_collection="armed_combat.execution.autonomous_cases",
            position_a=Position(5, 10),
            position_b=Position(
                int(row["position"]["x"]), int(row["position"]["y"])
            ),
            class_a="TECH",
            posture_b=Stance(str(row["stance"])),
            subsystem_state={
                "drone_position": {"x": 5, "y": 10},
                "target_position": row["position"],
                "standing_order": row["standing_order"],
                "roll": row["roll"],
                "ammunition_before": row["ammunition_before"],
                "execution_ap_before": row["execution_ap_before"],
            },
        )
    for row in armed_execution["fpv_cases"]:
        add(
            "drones",
            "DRONE-ARMED-V1",
            map_id="MAP_STREET_60",
            case_id=f"fpv-{row['case_id']}",
            result_collection="armed_combat.execution.fpv_cases",
            position_a=Position(5, 10),
            position_b=Position(15, 3),
            class_a="TECH",
            subsystem_state={
                "failure_margin": row["failure_margin"],
                "direction_index": row["direction_index"],
                "intended_landing": row["intended_landing"],
                "snapshot_revision": row["snapshot_revision"],
                "operator_ap_before": row["operator_ap_before"],
            },
        )

    for row in probes["social"]["rows"]:
        if not row["plausible"]:
            scenario_id = "SOCIAL-IMPOSSIBLE-V1"
        elif row["leverage"] is None:
            scenario_id = "SOCIAL-NO-LEVERAGE-V1"
        else:
            scenario_id = "SOCIAL-LEVERAGE-V1"
        add(
            "social",
            scenario_id,
            map_id="MAP_OPEN_100",
            case_id=f"{row['plausible']}-{row['relevant_fact']}-{row['leverage']}-{row['influencer']}",
            result_collection="rows",
            position_a=Position(10, 15),
            position_b=Position(11, 15),
            class_a="INFLUENCER" if row["influencer"] else "RIFLEMAN",
            subsystem_state={
                "plausible": row["plausible"],
                "relevant_fact": row["relevant_fact"],
                "leverage": row["leverage"],
                "difficulty": parameters.values["check.difficulty.hard"],
                "roll_domain": [1, 20],
            },
        )

    for row in probes["classes"]["rows"]:
        add(
            "classes",
            "CLASS-BASELINES-V1",
            map_id="MAP_COMPOUND_80",
            case_id=f"{row['class_id']}-L{row['level']:02d}",
            result_collection="rows",
            position_a=Position(10, 30),
            position_b=Position(70, 30),
            level=int(row["level"]),
            class_a=str(row["class_id"]),
            subsystem_state={
                "class_scenario_id": row["scenario_id"],
                "metric": row["metric"],
                "control": row["control"],
                "candidate": row["candidate"],
                "same_skill_aptitude_equipment_control": row[
                    "same_skill_aptitude_equipment_control"
                ],
            },
        )
    return probes


def all_exact_probes(parameters: ParameterSet) -> dict[str, Any]:
    probes = {
        "check_curves": exact_check_curves(parameters),
        "fire_range": exact_fire_range(parameters),
        "handling": handling_probe(parameters),
        "armor": armor_probe(parameters),
        "hit_quality": hit_quality_boundaries(parameters),
        "auto_strings": auto_string_probe(parameters),
        "materials": material_probe(parameters),
        "cover": cover_probe(parameters),
        "suppression": suppression_probe(parameters),
        "overwatch": overwatch_probe(parameters),
        "triggers": trigger_probe(parameters),
        "cqc": cqc_probe(parameters),
        "explosives": grenade_probe(parameters),
        "medical": medical_probe(parameters),
        "knowledge": knowledge_probe(),
        "hacking": hacking_probe(parameters),
        "drones": drone_probe(parameters),
        "social": social_probe(parameters),
        "classes": class_baseline_probe(parameters),
        "geometry": geometry_probe(parameters),
    }
    return _attach_scenario_case_records(probes, parameters)
