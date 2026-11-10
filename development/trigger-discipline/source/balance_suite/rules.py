from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .model import ActorState, ConditionState, HitQuality, LifeThreateningCondition, Stance, WeaponState
from .parameters import ParameterSet


PROVENANCE = {
    "checks": ("Character/CHECK.md", "Character/NATURAL 1.md", "Character/NATURAL 20.md"),
    "attacks": ("Combat/ATTACK ROLL.md", "Combat/HIT QUALITY.md", "Combat/CRITICAL HIT.md"),
    "projectiles": ("Firearms/PROJECTILE.md", "Firearms/MATERIAL PENETRATION.md", "Combat/PENETRATION.md"),
    "firearm_state": ("Firearms/AMMUNITION.md", "Firearms/MAGAZINE.md", "Firearms/CHAMBER.md", "Firearms/CYCLING.md", "Firearms/RELOAD.md"),
}


@dataclass(frozen=True, slots=True)
class CheckResult:
    roll: int
    total: int
    difficulty: int
    success: bool
    margin: int
    natural_rule: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AttackResult:
    check: CheckResult
    quality: HitQuality
    effective_damage: int
    effective_target_pen: int
    critical_effect: str | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["quality"] = self.quality.value
        return result


@dataclass(frozen=True, slots=True)
class DamageResult:
    input_damage: int
    input_target_pen: int
    hp_bypass: int
    res_damage: int
    hp_overflow: int
    total_hp_damage: int
    remaining_res: int
    remaining_hp: int
    downed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MaterialResult:
    outcome: str
    input_damage: int
    input_target_pen: int
    input_material_pen: int
    resistance: int
    remaining_damage: int
    remaining_target_pen: int
    remaining_material_pen: int
    continues: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CoverRollResult:
    outcome: str
    roll_total: int
    open_difficulty: int
    actual_difficulty: int


@dataclass(frozen=True, slots=True)
class FireActionResult:
    mode: str
    rounds_requested: int
    rounds_fired: int
    ap_cost: int
    attack: AttackResult
    connection_opportunities: int
    target_connections: int
    blocked_connections: int
    recoil_before: int
    recoil_after: int
    ammunition_before: int
    ammunition_after: int

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["attack"]["quality"] = self.attack.quality.value
        return result


@dataclass(frozen=True, slots=True)
class CQCResult:
    maneuver: str
    check: CheckResult
    declared_result: dict[str, Any]
    free_secondary_effects: tuple[str, ...] = ()


def resolve_cqc_maneuver(
    maneuver: str,
    roll: int,
    attacker_skill: int,
    attacker_aptitude: int,
    defender_skill: int,
    defender_aptitude: int,
    parameters: ParameterSet,
    *,
    control_focus: str = "BODY CONTROL",
    defender_situational: int = 0,
) -> CQCResult:
    maneuver = maneuver.upper()
    allowed = {"SHOVE", "TRIP", "THROW", "DISARM", "BODY CONTROL", "WEAPON CONTROL", "ESCAPE"}
    if maneuver not in allowed:
        raise ValueError(f"unknown CQC maneuver {maneuver}")
    defense = cqc_defense(
        defender_skill,
        defender_aptitude,
        parameters,
        situational=defender_situational,
    )
    check = resolve_check(roll, attacker_skill, attacker_aptitude, defense)
    result: dict[str, Any] = {"success": check.success}
    if check.success:
        if maneuver == "SHOVE":
            result.update({"forced_movement_m": parameters.values["cqc.shove_distance_m"], "stance_change": None})
        elif maneuver == "TRIP":
            result.update({"forced_movement_m": 0, "stance_change": "PRONE"})
        elif maneuver == "THROW":
            result.update({"forced_movement_m": parameters.values["cqc.throw_distance_m"], "stance_change": "PRONE"})
        elif maneuver == "DISARM":
            result.update({"held_item_to_ground": True, "item_transferred_to_attacker": False})
        elif maneuver in {"BODY CONTROL", "WEAPON CONTROL"}:
            result.update({"control_created": control_focus if maneuver == "BODY CONTROL" else "WEAPON CONTROL", "global_stun": False})
        elif maneuver == "ESCAPE":
            result.update({"control_ends": True, "control_reversed": False})
    return CQCResult(maneuver, check, result)


def resolve_check(
    roll: int,
    skill: int,
    aptitude: int,
    difficulty: int,
    modifiers: int = 0,
    *,
    natural_one_auto_fails: bool = False,
    natural_twenty_auto_succeeds: bool = False,
    legitimate: bool = True,
) -> CheckResult:
    if roll < 1 or roll > 20:
        raise ValueError("d20 roll must be 1..20")
    total = roll + skill + aptitude + modifiers
    if not legitimate:
        return CheckResult(roll, total, difficulty, False, total - difficulty, "impossible/unwarranted action")
    if roll == 1 and natural_one_auto_fails:
        return CheckResult(roll, total, difficulty, False, total - difficulty, "NATURAL 1 automatic failure")
    if roll == 20 and natural_twenty_auto_succeeds:
        return CheckResult(roll, total, difficulty, True, total - difficulty, "NATURAL 20 automatic success")
    return CheckResult(
        roll,
        total,
        difficulty,
        total >= difficulty,
        total - difficulty,
        "ordinary CHECK arithmetic; non-attack NATURAL 1/20 treatment unresolved by canon",
    )


def hit_quality(roll: int, margin: int, parameters: ParameterSet, *, legitimate: bool, success: bool) -> HitQuality:
    if not legitimate or not success or roll == 1:
        return HitQuality.MISS
    if roll == 20:
        return HitQuality.CRITICAL_HIT
    values = parameters.values
    if margin >= values["hit_quality.critical_margin"]:
        return HitQuality.CRITICAL_HIT
    if margin >= values["hit_quality.decisive_margin"]:
        return HitQuality.DECISIVE_HIT
    if margin >= values["hit_quality.clean_margin"]:
        return HitQuality.CLEAN_HIT
    return HitQuality.HIT


def resolve_attack(
    roll: int,
    skill: int,
    aptitude: int,
    difficulty: int,
    damage: int,
    target_pen: int,
    parameters: ParameterSet,
    *,
    modifiers: int = 0,
    legitimate: bool = True,
    critical_choice: str = "DEVASTATING HIT",
) -> AttackResult:
    check = resolve_check(
        roll,
        skill,
        aptitude,
        difficulty,
        modifiers,
        natural_one_auto_fails=True,
        natural_twenty_auto_succeeds=True,
        legitimate=legitimate,
    )
    quality = hit_quality(roll, check.margin, parameters, legitimate=legitimate, success=check.success)
    effective_damage = damage
    effective_target_pen = target_pen
    if quality in {HitQuality.CLEAN_HIT, HitQuality.DECISIVE_HIT, HitQuality.CRITICAL_HIT}:
        effective_target_pen += parameters.values["hit_quality.clean_target_pen"]
    if quality in {HitQuality.DECISIVE_HIT, HitQuality.CRITICAL_HIT}:
        effective_damage += parameters.values["hit_quality.decisive_damage"]
    critical_effect: str | None = None
    if quality is HitQuality.CRITICAL_HIT:
        if critical_choice == "DEEP HIT":
            effective_target_pen += parameters.values["critical.deep_target_pen"]
            critical_effect = "DEEP HIT"
        elif critical_choice == "DEVASTATING HIT":
            effective_damage += parameters.values["critical.devastating_damage"]
            critical_effect = "DEVASTATING HIT"
        else:
            raise ValueError("critical choice needs an established CONTEXT EFFECT; only DEEP/DEVASTATING are generic")
    return AttackResult(check, quality, max(0, effective_damage), max(0, effective_target_pen), critical_effect)


def resolve_damage(damage: int, target_pen: int, res: int, hp: int) -> DamageResult:
    damage = max(0, damage)
    target_pen = min(max(0, target_pen), damage)
    hp_bypass = min(hp, target_pen)
    hp_after_bypass = max(0, hp - hp_bypass)
    ordinary = damage - target_pen
    res_damage = min(res, ordinary)
    hp_overflow = min(hp_after_bypass, max(0, ordinary - res))
    total_hp_damage = hp_bypass + hp_overflow
    remaining_hp = max(0, hp - total_hp_damage)
    return DamageResult(
        damage,
        target_pen,
        hp_bypass,
        res_damage,
        hp_overflow,
        total_hp_damage,
        max(0, res - res_damage),
        remaining_hp,
        remaining_hp == 0,
    )


def apply_damage(actor: ActorState, damage: int, target_pen: int) -> DamageResult:
    result = resolve_damage(damage, target_pen, actor.res, actor.hp)
    actor.res = result.remaining_res
    actor.hp = result.remaining_hp
    actor.downed = result.downed
    actor.ap = 0 if actor.downed else actor.ap
    return result


def resolve_material(damage: int, target_pen: int, material_pen: int, resistance: int) -> MaterialResult:
    if material_pen < resistance:
        return MaterialResult("STOP", damage, target_pen, material_pen, resistance, 0, 0, 0, False)
    if material_pen == resistance:
        return MaterialResult(
            "REDUCE",
            damage,
            target_pen,
            material_pen,
            resistance,
            damage // 2,
            target_pen // 2,
            0,
            damage // 2 > 0,
        )
    return MaterialResult(
        "PENETRATE",
        damage,
        target_pen,
        material_pen,
        resistance,
        damage,
        target_pen,
        material_pen - resistance,
        damage > 0,
    )


def route_material_layers(
    damage: int, target_pen: int, material_pen: int, resistances: list[int]
) -> tuple[list[MaterialResult], tuple[int, int, int] | None]:
    stages: list[MaterialResult] = []
    current = (damage, target_pen, material_pen)
    for resistance in resistances:
        result = resolve_material(*current, resistance)
        stages.append(result)
        if not result.continues:
            return stages, None
        current = (result.remaining_damage, result.remaining_target_pen, result.remaining_material_pen)
    return stages, current


def resolve_cover_roll(roll_total: int, open_difficulty: int, actual_difficulty: int) -> CoverRollResult:
    if actual_difficulty < open_difficulty:
        raise ValueError("ACTUAL DIFFICULTY cannot be below OPEN DIFFICULTY")
    if roll_total >= actual_difficulty:
        return CoverRollResult("TARGET HIT", roll_total, open_difficulty, actual_difficulty)
    if roll_total >= open_difficulty:
        return CoverRollResult("COVER INTERCEPT", roll_total, open_difficulty, actual_difficulty)
    return CoverRollResult("WIDE MISS", roll_total, open_difficulty, actual_difficulty)


def range_modifier(weapon_id: str, distance_m: float, parameters: ParameterSet) -> int:
    breakpoints = parameters.values[f"equipment.{weapon_id}.range_breakpoints_m"]
    modifiers = parameters.values[f"equipment.{weapon_id}.range_modifiers"]
    for index, breakpoint in enumerate(breakpoints):
        if distance_m <= breakpoint:
            return modifiers[index]
    return modifiers[-1]


def attack_values_for_weapon(weapon_id: str, distance_m: float, parameters: ParameterSet) -> tuple[int, int, int, int]:
    values = parameters.values
    damage = values[f"equipment.{weapon_id}.damage"]
    if weapon_id == "shotgun":
        ranges = values["equipment.shotgun.range_breakpoints_m"]
        if distance_m > ranges[0]:
            damage = max(1, damage - (2 if distance_m <= ranges[1] else 4))
    return (
        damage,
        values[f"equipment.{weapon_id}.target_pen"],
        values[f"equipment.{weapon_id}.material_pen"],
        range_modifier(weapon_id, distance_m, parameters),
    )


def connection_opportunities(mode: str, rounds: int, quality: HitQuality, parameters: ParameterSet) -> int:
    if quality is HitQuality.MISS or rounds <= 0:
        return 0
    if mode in {"SEMI AUTO", "SINGLE"}:
        return 1
    quality_key = {
        HitQuality.HIT: "hit",
        HitQuality.CLEAN_HIT: "clean",
        HitQuality.DECISIVE_HIT: "decisive",
        HitQuality.CRITICAL_HIT: "critical",
    }[quality]
    if mode == "BURST":
        return min(rounds, parameters.values[f"burst.connections.{quality_key}"])
    if mode == "FULL AUTO":
        bonus = 0 if quality is HitQuality.HIT else parameters.values[f"full_auto.quality_bonus.{quality_key}"]
        step = parameters.values["full_auto.connection_round_step"]
        return min(rounds, 1 + (rounds - 1) // step + bonus)
    raise ValueError(f"unsupported fire mode {mode}")


def exposure_connection_cap(exposure: str, rounds: int, parameters: ParameterSet) -> int:
    normalized = exposure.lower()
    if normalized == "full":
        return rounds
    if normalized == "half":
        return parameters.values["exposure_cap.half"]
    if normalized == "quarter":
        return parameters.values["exposure_cap.quarter"]
    if normalized == "none":
        return 0
    raise ValueError(f"unknown exposure {exposure}")


def recoil_gain(mode: str, weapon_recoil: int, rounds: int, parameters: ParameterSet) -> int:
    values = parameters.values
    if mode in {"SEMI AUTO", "SINGLE"}:
        return max(1, round(weapon_recoil * values["recoil.semi_gain_multiplier"]))
    if mode == "BURST":
        return weapon_recoil + values["recoil.burst_extra_state"]
    if mode == "FULL AUTO":
        return weapon_recoil + math.ceil(rounds / values["recoil.full_auto_round_step"]) - 1
    raise ValueError(f"unsupported fire mode {mode}")


def firing_string_modifier(mode: str, weapon_recoil: int, rounds: int, parameters: ParameterSet) -> int:
    values = parameters.values
    if mode in {"SEMI AUTO", "SINGLE"}:
        return 0
    if mode == "BURST":
        return round(weapon_recoil * values["recoil.burst_attack_difficulty_multiplier"])
    steps = max(0, math.ceil(rounds / values["recoil.full_auto_round_step"]) - 1)
    return round(weapon_recoil * values["recoil.full_auto_attack_difficulty_multiplier"]) + (
        steps * values["recoil.full_auto_extra_difficulty_per_step"]
    )


def recover_recoil(weapon: WeaponState, ap: int, parameters: ParameterSet, *, rifleman: bool = False) -> int:
    rate = parameters.values["recoil.recovery_per_ap"]
    if rifleman:
        rate += parameters.values["class.rifleman.extra_recoil_recovery_per_ap"]
    recovered = min(weapon.current_recoil, ap * rate)
    weapon.current_recoil -= recovered
    return recovered


def _consume_rounds(weapon: WeaponState, requested: int) -> int:
    if requested < 0:
        raise ValueError("requested rounds cannot be negative")
    fired = 0
    self_loading = weapon.mechanism not in {"bolt action", "pump action", "lever action"}
    for _ in range(requested):
        if weapon.needs_cycle or weapon.chamber != "LIVE":
            break
        fired += 1
        weapon.chamber = "SPENT"
        if self_loading:
            if weapon.magazine_rounds > 0:
                weapon.magazine_rounds -= 1
                weapon.chamber = "LIVE"
            else:
                weapon.chamber = "EMPTY"
        else:
            weapon.needs_cycle = True
    return fired


def cycle_weapon(weapon: WeaponState) -> bool:
    if weapon.mechanism not in {"bolt action", "pump action", "lever action"} and not weapon.needs_cycle:
        return False
    weapon.needs_cycle = False
    if weapon.magazine_rounds > 0:
        weapon.magazine_rounds -= 1
        weapon.chamber = "LIVE"
    else:
        weapon.chamber = "EMPTY"
    return True


def reload_magazine(weapon: WeaponState, rounds: int) -> int:
    if rounds < 0:
        raise ValueError("rounds cannot be negative")
    if weapon.magazine_rounds:
        weapon.retained_magazines.append(weapon.magazine_rounds)
    load = min(rounds, weapon.reserve_rounds, weapon.magazine_capacity)
    weapon.reserve_rounds -= load
    weapon.magazine_rounds = load
    return load


def fire_action(
    weapon: WeaponState,
    mode: str,
    requested_rounds: int,
    roll: int,
    skill: int,
    aptitude: int,
    base_difficulty: int,
    distance_m: float,
    exposure: str,
    parameters: ParameterSet,
    *,
    aim_ap: int = 0,
    sharpshooter: bool = False,
    legitimate: bool = True,
) -> FireActionResult:
    mode = mode.upper()
    if mode not in weapon.fire_modes and not (mode == "BURST" and "3-round BURST" in weapon.fire_modes):
        raise ValueError(f"{weapon.weapon_id} does not support {mode}")
    if mode == "BURST":
        requested_rounds = 3
    if mode in {"SEMI AUTO", "SINGLE"}:
        requested_rounds = 1
    ammunition_before = weapon.total_rounds()
    recoil_before = weapon.current_recoil
    weapon_recoil = parameters.values[f"equipment.{weapon.weapon_id}.recoil"]
    damage, target_pen, _material_pen, range_penalty = attack_values_for_weapon(weapon.weapon_id, distance_m, parameters)
    aim_ap = min(max(0, aim_ap), parameters.values["fire.aim.max_ap_on_one_solution"])
    aim_offset = aim_ap * parameters.values["fire.aim.attack_difficulty_offset_per_ap"]
    if sharpshooter:
        aim_offset -= min(range_penalty, aim_ap * parameters.values["class.sharpshooter.extra_aim_range_offset_per_ap"])
    rounds_fired = _consume_rounds(weapon, requested_rounds)
    string_modifier = firing_string_modifier(mode, weapon_recoil, max(1, rounds_fired), parameters)
    difficulty = max(1, base_difficulty + range_penalty + recoil_before + string_modifier + aim_offset)
    attack = resolve_attack(roll, skill, aptitude, difficulty, damage, target_pen, parameters, legitimate=legitimate and rounds_fired > 0)
    opportunities = connection_opportunities(mode, rounds_fired, attack.quality, parameters)
    target_connections = min(opportunities, exposure_connection_cap(exposure, rounds_fired, parameters))
    blocked = max(0, opportunities - target_connections)
    if rounds_fired:
        weapon.current_recoil += recoil_gain(mode, weapon_recoil, rounds_fired, parameters)
    ap_cost = {
        "SEMI AUTO": parameters.values["fire.semi_single.ap"],
        "SINGLE": parameters.values["fire.semi_single.ap"],
        "BURST": parameters.values["fire.burst_3.ap"],
        "FULL AUTO": max(1, math.ceil(rounds_fired / parameters.values["fire.full_auto.rounds_per_ap"])),
    }[mode]
    return FireActionResult(
        mode,
        requested_rounds,
        rounds_fired,
        ap_cost,
        attack,
        opportunities,
        target_connections,
        blocked,
        recoil_before,
        weapon.current_recoil,
        ammunition_before,
        weapon.total_rounds(),
    )


def movement_rate(stance: Stance, burden: int, class_id: str, distance_to_known_enemy: float | None, parameters: ParameterSet) -> float:
    key = {
        Stance.STANDING: "move.upright_m_per_ap",
        Stance.CROUCHED: "move.crouched_m_per_ap",
        Stance.PRONE: "move.prone_m_per_ap",
    }[stance]
    rate = parameters.values[key] - burden * parameters.values["move.burden_penalty_m_per_ap_per_point"]
    if class_id == "BULWARK":
        rate += parameters.values["class.bulwark.move_delta_m_per_ap"]
    elif class_id == "GHOST":
        rate += parameters.values["class.ghost.move_delta_m_per_ap"]
    elif (
        class_id == "BREACHER"
        and distance_to_known_enemy is not None
        and distance_to_known_enemy <= parameters.values["class.breacher.close_envelope_m"]
    ):
        rate += parameters.values["class.breacher.close_move_delta_m_per_ap"]
    return max(min(0.5, parameters.values["move.prone_m_per_ap"]), rate)


def cqc_defense(skill: int, aptitude: int, parameters: ParameterSet, situational: int = 0) -> int:
    return round(
        parameters.values["cqc.defense_base"]
        + skill * parameters.values["cqc.defense_skill_coefficient"]
        + aptitude * parameters.values["cqc.defense_aptitude_coefficient"]
        + situational
    )


def explosive_warning(remaining_seconds: float, parameters: ParameterSet) -> tuple[bool, int | None, str]:
    values = parameters.values
    if remaining_seconds < values["explosive_save.minimum_warning_s"]:
        return False, None, "insufficient warning"
    if remaining_seconds < values["explosive_save.hard_warning_s"]:
        return True, values["explosive_save.severe_modifier"], "severe warning"
    if remaining_seconds < values["explosive_save.normal_warning_s"]:
        return True, values["explosive_save.hard_modifier"], "hard warning"
    return True, 0, "normal warning"


def stabilize_condition(condition: LifeThreateningCondition, *, capable_intervention: bool, success: bool) -> bool:
    if condition.state is not ConditionState.ACTIVE or not capable_intervention:
        return False
    if success:
        condition.state = ConditionState.STABILIZED
        return True
    return False


def resuscitate_condition(
    condition: LifeThreateningCondition, *, capable_intervention: bool, within_window: bool, success: bool
) -> bool:
    if condition.state is not ConditionState.FAILED or not capable_intervention or not within_window:
        return False
    if success:
        condition.state = ConditionState.STABILIZED
        return True
    return False
