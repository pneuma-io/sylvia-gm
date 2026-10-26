from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import itertools
import json
import math
from typing import Any, Iterable

from .specialty_manifest import SPECIALTY_PARAMETER_FAMILIES, SPECIALTY_PARAMETER_ROWS, parameter_id as specialty_parameter_id


class ParameterError(ValueError):
    pass


class Authority(str, Enum):
    FIXED_FIXTURE = "FIXED FIXTURE"
    TUNABLE = "TUNABLE"
    REVIEW_ONLY = "REVIEW ONLY"


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    parameter_id: str
    authority: Authority
    value_type: str
    seed: Any
    minimum: Any = None
    maximum: Any = None
    ordered: str | None = None
    source: str = "Testing/BALANCE PARAMETERS.md"
    constraint: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["authority"] = self.authority.value
        return result


@dataclass(frozen=True, slots=True)
class ParameterSet:
    manifest: "ParameterManifest"
    values: dict[str, Any]
    _hash: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        encoded = json.dumps(self.values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "_hash", hashlib.sha256(encoded.encode("utf-8")).hexdigest())

    @property
    def hash(self) -> str:
        return self._hash

    def with_updates(self, updates: dict[str, Any], *, optimizer: bool = False) -> "ParameterSet":
        unknown = sorted(set(updates) - set(self.manifest.specs))
        if unknown:
            raise ParameterError(f"unknown parameter IDs: {', '.join(unknown)}")
        if optimizer:
            forbidden = sorted(
                parameter_id
                for parameter_id in updates
                if self.manifest.specs[parameter_id].authority is not Authority.TUNABLE
            )
            if forbidden:
                raise ParameterError(f"optimizer cannot mutate non-TUNABLE IDs: {', '.join(forbidden)}")
        merged = dict(self.values)
        merged.update(updates)
        self.manifest.validate_values(merged, require_complete=True)
        return ParameterSet(self.manifest, merged)

    def movement_from_seed(self) -> dict[str, Any]:
        return {
            parameter_id: {"seed": spec.seed, "value": self.values[parameter_id]}
            for parameter_id, spec in self.manifest.specs.items()
            if self.values[parameter_id] != spec.seed
        }


@dataclass(frozen=True, slots=True)
class ParameterManifest:
    specs: dict[str, ParameterSpec]
    version: str = "v0.1"

    def seed_set(self) -> ParameterSet:
        values = {parameter_id: spec.seed for parameter_id, spec in self.specs.items()}
        self.validate_values(values, require_complete=True)
        return ParameterSet(self, values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": "Testing/BALANCE PARAMETERS.md",
            "parameters": [self.specs[key].to_dict() for key in sorted(self.specs)],
        }

    def validate_values(self, values: dict[str, Any], *, require_complete: bool) -> None:
        unknown = sorted(set(values) - set(self.specs))
        missing = sorted(set(self.specs) - set(values)) if require_complete else []
        if unknown:
            raise ParameterError(f"unknown parameter IDs: {', '.join(unknown)}")
        if missing:
            raise ParameterError(f"missing parameter IDs: {', '.join(missing)}")
        for parameter_id, value in values.items():
            _validate_one(self.specs[parameter_id], value)
        if require_complete:
            _validate_couplings(values)

    def load_set(self, payload: dict[str, Any], *, optimizer: bool = False) -> ParameterSet:
        values = payload.get("values", payload)
        if not isinstance(values, dict):
            raise ParameterError("parameter-set payload must contain an object of values")
        if optimizer:
            return self.seed_set().with_updates(values, optimizer=True)
        self.validate_values(values, require_complete=True)
        return ParameterSet(self, dict(values))


def _bounds(spec: ParameterSpec, index: int | None = None) -> tuple[Any, Any]:
    minimum = spec.minimum[index] if index is not None and isinstance(spec.minimum, list) else spec.minimum
    maximum = spec.maximum[index] if index is not None and isinstance(spec.maximum, list) else spec.maximum
    return minimum, maximum


def _validate_scalar(spec: ParameterSpec, value: Any, index: int | None = None) -> None:
    expected_int = spec.value_type in {"integer", "integer_vector"}
    if expected_int and (not isinstance(value, int) or isinstance(value, bool)):
        raise ParameterError(f"{spec.parameter_id} requires an integer")
    if not expected_int and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)):
        raise ParameterError(f"{spec.parameter_id} requires a finite number")
    minimum, maximum = _bounds(spec, index)
    if minimum is not None and value < minimum:
        raise ParameterError(f"{spec.parameter_id} value {value} is below {minimum}")
    if maximum is not None and value > maximum:
        raise ParameterError(f"{spec.parameter_id} value {value} is above {maximum}")


def _validate_one(spec: ParameterSpec, value: Any) -> None:
    if spec.value_type in {"integer", "float"}:
        _validate_scalar(spec, value)
    elif spec.value_type in {"integer_vector", "float_vector"}:
        if not isinstance(value, list) or len(value) != len(spec.seed):
            raise ParameterError(f"{spec.parameter_id} requires a vector of length {len(spec.seed)}")
        for index, item in enumerate(value):
            _validate_scalar(spec, item, index)
        if spec.ordered == "nondecreasing" and any(left > right for left, right in itertools.pairwise(value)):
            raise ParameterError(f"{spec.parameter_id} must be nondecreasing")
        if spec.ordered == "strictly_increasing" and any(left >= right for left, right in itertools.pairwise(value)):
            raise ParameterError(f"{spec.parameter_id} must be strictly increasing")
        if spec.ordered == "nonincreasing" and any(left < right for left, right in itertools.pairwise(value)):
            raise ParameterError(f"{spec.parameter_id} must be nonincreasing")
    elif spec.value_type == "fixed":
        if value != spec.seed:
            raise ParameterError(f"{spec.parameter_id} is a fixed fixture")
    else:
        raise ParameterError(f"unsupported type {spec.value_type} for {spec.parameter_id}")


def _validate_couplings(v: dict[str, Any]) -> None:
    ordered_groups = [
        ["check.difficulty.routine", "check.difficulty.standard", "check.difficulty.hard", "check.difficulty.severe", "check.difficulty.extreme"],
        ["hit_quality.clean_margin", "hit_quality.decisive_margin", "hit_quality.critical_margin"],
    ]
    for group in ordered_groups:
        if any(v[left] >= v[right] for left, right in itertools.pairwise(group)):
            raise ParameterError(f"coupled ordering violated: {' < '.join(group)}")
    if not (v["move.upright_m_per_ap"] > v["move.crouched_m_per_ap"] > v["move.prone_m_per_ap"]):
        raise ParameterError("movement rates must remain upright > crouched > prone")
    if not (
        v["casualty.drag.one_hand_rate_multiplier"]
        < v["casualty.drag.two_hand_rate_multiplier"]
        < v["casualty.carry.rate_multiplier"]
    ):
        raise ParameterError("casualty movement must remain one-hand drag < two-hand drag < carry")
    if v["cover.actual_difficulty_offset.half"] >= v["cover.actual_difficulty_offset.quarter"]:
        raise ParameterError("quarter exposure must remain harder to attack than half exposure")
    if v["hacking.standard_intrusion_difficulty"] >= v["hacking.hard_intrusion_difficulty"]:
        raise ParameterError("hard intrusion must remain harder than standard intrusion")
    if v["hp.vector_l01_l05_l10_l15_l20"][-1] > v["hp.vector_l01_l05_l10_l15_l20"][0] + 4:
        raise ParameterError("L20 HP cannot exceed L1 HP by more than 4")
    if not (
        v["full_auto.quality_bonus.clean"]
        <= v["full_auto.quality_bonus.decisive"]
        <= v["full_auto.quality_bonus.critical"]
    ):
        raise ParameterError("full-auto quality bonuses must be ordered")
    if not (
        v["burst.connections.hit"]
        <= v["burst.connections.clean"]
        <= v["burst.connections.decisive"]
        <= v["burst.connections.critical"]
    ):
        raise ParameterError("burst connections must be nondecreasing by HIT QUALITY")
    if not (
        v["explosive_save.normal_warning_s"]
        > v["explosive_save.hard_warning_s"]
        > v["explosive_save.minimum_warning_s"]
    ):
        raise ParameterError("explosive warning bands must be strictly descending")
    if v["cqc.defense_aptitude_coefficient"] > v["cqc.defense_skill_coefficient"]:
        raise ParameterError("CQC APTITUDE coefficient cannot exceed SKILL coefficient")
    if (
        v["specialty.trauma.salvage.persistent_difficulty_reduction"]
        >= v["injury.repairable_forearm.ordinary_persistent_difficulty"]
    ):
        raise ParameterError("SALVAGE must leave a persistent consequence in the reference injury")
    if (
        v["specialty.operator.one_handed_manipulation.one_handed_surcharge_reduction_ap"]
        > v["fire.one_handed_manipulation_surcharge_ap"]
    ):
        raise ParameterError("ONE-HANDED MANIPULATION cannot reduce more than the baseline surcharge")
    if not (
        v["social.leverage.weak_modifier"]
        > v["social.leverage.strong_modifier"]
        > v["social.leverage.decisive_modifier"]
    ):
        raise ParameterError("social leverage must strengthen from weak to decisive")
    for weapon in FIREARM_IDS:
        if v[f"equipment.{weapon}.target_pen"] > v[f"equipment.{weapon}.damage"]:
            raise ParameterError(f"equipment.{weapon}.target_pen cannot exceed DAMAGE")
    if v["equipment.autodrone_light_weapon.target_pen"] > v["equipment.autodrone_light_weapon.damage"]:
        raise ParameterError("autodrone light-weapon target PEN cannot exceed DAMAGE")
    if v["equipment.autodrone_light_weapon.attack_ap_equivalent"] > v["equipment.autodrone.actions_per_turn"]:
        raise ParameterError("autodrone attack cannot exceed its per-turn execution budget")
    if v["equipment.heavy_body_armor.res"] <= v["equipment.standard_body_armor.res"]:
        raise ParameterError("heavy body armor must remain more protective")
    if v["equipment.heavy_body_armor.burden"] <= v["equipment.standard_body_armor.burden"]:
        raise ParameterError("heavy body armor must remain more burdensome")
    material_res = [v[f"material.{name}.resistance"] for name in MATERIAL_IDS]
    if any(left > right for left, right in itertools.pairwise(material_res)):
        raise ParameterError("material resistance order must remain monotonic")


def _fixed(parameter_id: str, seed: Any, constraint: str = "") -> ParameterSpec:
    return ParameterSpec(parameter_id, Authority.FIXED_FIXTURE, "fixed", seed, constraint=constraint)


def _int(parameter_id: str, seed: int, minimum: int, maximum: int, constraint: str = "") -> ParameterSpec:
    return ParameterSpec(parameter_id, Authority.TUNABLE, "integer", seed, minimum, maximum, constraint=constraint)


def _float(parameter_id: str, seed: float, minimum: float, maximum: float, constraint: str = "") -> ParameterSpec:
    return ParameterSpec(parameter_id, Authority.TUNABLE, "float", seed, minimum, maximum, constraint=constraint)


def _vector(
    parameter_id: str,
    seed: list[int | float],
    minimum: list[int | float] | int | float,
    maximum: list[int | float] | int | float,
    *,
    integer: bool,
    ordered: str,
    constraint: str = "",
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id,
        Authority.TUNABLE,
        "integer_vector" if integer else "float_vector",
        seed,
        minimum,
        maximum,
        ordered,
        constraint=constraint,
    )


FIREARM_SEEDS: dict[str, dict[str, Any]] = {
    "pistol": {"damage": 4, "target_pen": 1, "material_pen": 1, "ranges": [15, 30, 50], "recoil": 1, "capacity": 15, "reload": 2, "handling": 0},
    "smg": {"damage": 4, "target_pen": 1, "material_pen": 1, "ranges": [30, 60, 100], "recoil": 1, "capacity": 30, "reload": 2, "handling": 1},
    "service_rifle": {"damage": 5, "target_pen": 2, "material_pen": 3, "ranges": [120, 250, 400], "recoil": 2, "capacity": 30, "reload": 2, "handling": 2},
    "lmg": {"damage": 5, "target_pen": 2, "material_pen": 3, "ranges": [150, 350, 600], "recoil": 2, "capacity": 100, "reload": 4, "handling": 4},
    "shotgun": {"damage": 7, "target_pen": 1, "material_pen": 1, "ranges": [10, 20, 35], "recoil": 4, "capacity": 8, "reload": 1, "handling": 2},
    "precision_rifle": {"damage": 6, "target_pen": 3, "material_pen": 4, "ranges": [250, 500, 800], "recoil": 3, "capacity": 10, "reload": 2, "handling": 3},
    "manual_precision_rifle": {"damage": 7, "target_pen": 4, "material_pen": 5, "ranges": [300, 650, 1000], "recoil": 4, "capacity": 5, "reload": 2, "handling": 4},
}
FIREARM_IDS = tuple(FIREARM_SEEDS)
MATERIAL_IDS = ("light_interior", "timber", "light_masonry", "reinforced_plate", "reinforced_concrete", "hardened_barrier")


def default_manifest() -> ParameterManifest:
    specs: list[ParameterSpec] = [
        _fixed("fixture.check.formula", "1d20 + SKILL + relevant APTITUDE + modifiers"),
        _fixed("fixture.skill.range", [0, 8]),
        _fixed("fixture.aptitude.range", [0, 3]),
        _fixed("fixture.level.snapshots", [1, 5, 10, 15, 20]),
        _fixed("fixture.specialist.progression", [[1, 4, 2, 1, 1], [5, 5, 3, 1, 5], [10, 6, 4, 2, 10], [15, 7, 5, 2, 15], [20, 8, 6, 3, 20]]),
        _fixed("fixture.natural_1", "automatic failure where canon says so"),
        _fixed("fixture.natural_20.attack", "legitimate attack -> CRITICAL HIT"),
        _fixed("fixture.hit_quality.tiers", ["HIT", "CLEAN HIT", "DECISIVE HIT", "CRITICAL HIT"]),
        _fixed("fixture.ap_seconds", 1),
        _fixed("fixture.reference_equipment.schema", "Testing/REFERENCE EQUIPMENT.md"),
        _fixed("fixture.cover.damage_formula", "max(0, DAMAGE - MATERIAL RESISTANCE)"),
        _fixed("fixture.hacking.network_storage", "authored stable NODE/LINK graph"),
        _int("equipment.shotgun.cycle_ap", 1, 1, 6),
        _int("equipment.manual_precision_rifle.cycle_ap", 1, 1, 6),
        _int("check.difficulty.routine", 10, 8, 12),
        _int("check.difficulty.standard", 14, 12, 16),
        _int("check.difficulty.hard", 18, 16, 20),
        _int("check.difficulty.severe", 22, 20, 24),
        _int("check.difficulty.extreme", 26, 24, 30),
        _int("combat.ap.pool", 6, 5, 8),
        _float("move.upright_m_per_ap", 1.0, 0.1, 6.0),
        _float("move.crouched_m_per_ap", 0.5, 0.05, 4.5),
        _float("move.prone_m_per_ap", 0.25, 0.025, 2.0),
        _int("move.stance_change_ap", 1, 1, 2),
        _float("move.burden_penalty_m_per_ap_per_point", 0.5, 0.25, 1.0),
        _int("casualty.drag.engage_ap", 1, 1, 2),
        _float("casualty.drag.one_hand_rate_multiplier", 0.5, 0.35, 0.65),
        _float("casualty.drag.two_hand_rate_multiplier", 0.65, 0.5, 0.8),
        _int("casualty.carry.lift_ap", 2, 1, 3),
        _float("casualty.carry.rate_multiplier", 0.75, 0.6, 0.9),
        _int("casualty.carry.lower_ap", 1, 1, 2),
        _int("breach.minor_debris.crossing_extra_ap", 1, 1, 2),
        _int("breach.minor_debris.clear_ap", 2, 1, 3),
        _vector("hp.vector_l01_l05_l10_l15_l20", [10, 10, 11, 11, 12], 8, 16, integer=True, ordered="nondecreasing"),
        _vector("res.vector_l01_l05_l10_l15_l20", [6, 8, 10, 12, 14], 4, 24, integer=True, ordered="nondecreasing"),
        _int("class.bulwark.res_bonus", 3, 1, 6),
        _float("class.bulwark.move_delta_m_per_ap", -0.75, -1.5, -0.5),
        _float("class.ghost.move_delta_m_per_ap", 1.0, 0.5, 2.0),
        _float("class.breacher.close_move_delta_m_per_ap", 2.0, 1.0, 3.0),
        _int("class.breacher.close_envelope_m", 12, 6, 20),
        _int("class.rifleman.extra_recoil_recovery_per_ap", 1, 1, 2),
        _int("class.sharpshooter.extra_aim_range_offset_per_ap", 1, 1, 3),
        _int("class.tech.operation_ap_discount", 1, 1, 2),
        _int("class.hacker.intrusion_ap_discount", 1, 1, 2),
        _int("class.corpsman.medicine_ap_discount", 1, 1, 2),
        _int("class.corpsman.medicine_check_bonus", 2, 1, 4),
        _int("class.influencer.leverage_setup_ap_discount", 1, 1, 2),
        _int("class.influencer.leverage_strength_steps", 1, 1, 2),
        _int("fire.aim.attack_difficulty_offset_per_ap", -2, -3, -1),
        _int("fire.aim.max_ap_on_one_solution", 2, 1, 3),
        _int("fire.semi_single.ap", 2, 1, 3),
        _int("fire.burst_3.ap", 2, 1, 3),
        _int("fire.full_auto.rounds_per_ap", 5, 3, 8),
        _int("fire.one_handed_manipulation_surcharge_ap", 2, 1, 3),
        _int("recoil.recovery_per_ap", 1, 1, 2),
        _float("recoil.semi_gain_multiplier", 1.0, 0.5, 1.5),
        _int("recoil.burst_extra_state", 1, 0, 2),
        _int("recoil.full_auto_round_step", 5, 3, 8),
        _float("recoil.burst_attack_difficulty_multiplier", 1.0, 0.5, 1.5),
        _float("recoil.full_auto_attack_difficulty_multiplier", 1.0, 0.5, 1.5),
        _int("recoil.full_auto_extra_difficulty_per_step", 1, 0, 2),
        _int("hit_quality.clean_margin", 5, 3, 7),
        _int("hit_quality.decisive_margin", 10, 7, 14),
        _int("hit_quality.critical_margin", 15, 11, 21),
        _int("hit_quality.clean_target_pen", 1, 1, 2),
        _int("hit_quality.decisive_damage", 1, 1, 3),
        _int("critical.deep_target_pen", 2, 1, 4),
        _int("critical.devastating_damage", 2, 1, 4),
        _int("burst.connections.hit", 1, 1, 3),
        _int("burst.connections.clean", 2, 0, 3),
        _int("burst.connections.decisive", 3, 0, 3),
        _int("burst.connections.critical", 3, 0, 3),
        _int("full_auto.connection_round_step", 5, 3, 8),
        _int("full_auto.quality_bonus.clean", 1, 0, 2),
        _int("full_auto.quality_bonus.decisive", 2, 1, 4),
        _int("full_auto.quality_bonus.critical", 3, 2, 5),
        _fixed("exposure_cap.full", "no extra cap"),
        _int("exposure_cap.half", 3, 2, 6),
        _int("exposure_cap.quarter", 1, 1, 3),
        _fixed("cover.actual_difficulty_offset.full", 0),
        _int("cover.actual_difficulty_offset.half", 2, 1, 3),
        _int("cover.actual_difficulty_offset.quarter", 5, 3, 7),
        _int("overwatch.setup_ap", 2, 1, 3),
        _int("overwatch.arc_degrees", 90, 45, 180),
        _int("overwatch.shot_allowance", 1, 1, 3),
        _int("suppression.setup_ap", 2, 1, 4),
        _int("suppression.rounds_per_5m_zone", 5, 3, 10),
        _int("suppression.exposure_capacity_round_divisor", 10, 5, 15),
        _int("suppression.attack_difficulty_modifier", 4, 2, 6),
        _fixed("suppression.persistence_team_turns", 1),
        _int("cqc.defense_base", 10, 8, 12),
        _float("cqc.defense_skill_coefficient", 1.0, 1.0, 1.5),
        _float("cqc.defense_aptitude_coefficient", 1.0, 0.5, 1.0),
        _int("cqc.action_ap", 2, 1, 3),
        _int("cqc.escape_ap", 2, 1, 3),
        _int("cqc.shove_distance_m", 2, 1, 4),
        _int("cqc.throw_distance_m", 2, 1, 4),
        _float("cqc.contact_handling_multiplier", 1.0, 0.5, 1.5),
        _float("explosive_save.normal_warning_s", 3.0, 2.0, 4.0),
        _float("explosive_save.hard_warning_s", 2.0, 1.5, 3.0),
        _float("explosive_save.minimum_warning_s", 1.0, 0.5, 1.5),
        _int("explosive_save.hard_modifier", 2, 1, 4),
        _int("explosive_save.severe_modifier", 5, 3, 7),
        _vector("grenade.scatter.distance_by_failure_margin_m", [1, 2, 4, 6], [0, 1, 2, 3], [2, 4, 6, 10], integer=True, ordered="strictly_increasing"),
        _fixed("grenade.scatter.direction_count", 8),
        _int("medicine.stabilize_ap", 3, 2, 5),
        _int("medicine.stabilize_difficulty", 14, 12, 20),
        _int("medicine.resuscitation_ap", 6, 4, 10),
        _int("medicine.resuscitation_difficulty", 18, 14, 24),
        _int("medicine.field_treatment_ap", 3, 2, 5),
        _int("hacking.standard_intrusion_ap", 2, 1, 4),
        _int("hacking.hard_intrusion_ap", 3, 2, 5),
        _int("hacking.standard_intrusion_difficulty", 14, 12, 16),
        _int("hacking.hard_intrusion_difficulty", 18, 16, 20),
        _int("hacking.stay_quiet_difficulty", 14, 12, 18),
        _int("hacking.visibility.low_difficulty_offset", -2, -4, -1),
        _fixed("hacking.visibility.standard_difficulty_offset", 0),
        _int("hacking.visibility.high_difficulty_offset", 2, 1, 4),
        _int("hacking.detection.failure_steps", 1, 1, 2),
        _fixed("hacking.retry.lock_without_state_change", False),
        _fixed("hacking.authorized_function_ap", 1),
        _int("social.fact_to_leverage_setup_ap", 2, 1, 4),
        _int("social.leverage.weak_modifier", -2, -3, -1),
        _int("social.leverage.strong_modifier", -4, -6, -3),
        _int("social.leverage.decisive_modifier", -6, -8, -5),
        _int("engineering.field_reconfiguration_ap", 3, 2, 5),
        _int("stealth.break_contact_ap", 2, 2, 4),
        _int("specialty.local_hacker.service_access.difficulty_offset", -2, -4, -1),
        _int("specialty.shield_bearer.shield_training.burden_reduction", 1, 1, 2),
        _int("specialty.shield_bearer.shield_training.handling_reduction", 1, 1, 2),
        _fixed("specialty.fpv_drones.nap_of_the_earth.exposure_band_reduction", 1),
        _int("specialty.fpv_drones.snap_peek.ap_discount", 1, 1, 2),
        _fixed("specialty.guardian.body_cover.exposure_band_reduction", 1),
        _int("specialty.anchor.firing_edge.aim_steps", 1, 1, 2),
        _fixed("specialty.field_engineer.rapid_deployment.ap_discount", 1),
        _int("specialty.field_engineer.power_budget.action_ap", 2, 1, 3),
        _int("specialty.field_engineer.power_budget.transfer_seconds", 300, 120, 600),
        _int("specialty.gunner.fire_through_the_opening.difficulty_offset", -2, -4, -1),
        _int("specialty.gunner.fire_through_the_opening.max_width_m", 2, 1, 3),
        _int("specialty.gunner.fire_superiority.maintenance_ap_discount", 1, 1, 2),
        _float("specialty.evader.slip_the_fire.movement_multiplier", 0.5, 0.35, 0.75),
        _int("specialty.evader.slip_the_fire.difficulty_offset", 2, 1, 4),
        _fixed("specialty.sentinel.hold_under_fire.trigger_priority", 1),
        _int("specialty.sentinel.second_trigger.additional_triggers", 1, 1, 2),
        _int("specialty.sentinel.deep_sector.extra_depth_m", 10, 5, 20),
        _int("specialty.enforcer.heavy_contact.res_damage", 1, 1, 3),
        _int("specialty.shield_bearer.draw_the_burst.extra_capacity_cost", 1, 1, 2),
        _fixed("specialty.field_medic.continue_care.progress_retained", 1),
        _fixed("specialty.anchor.hold_under_fire.dormant_team_turns", 1),
        _int("specialty.anchor.prepared_garrison.ap_discount", 1, 1, 2),
        _int("specialty.guardian.protect_the_operator.ap_discount", 1, 1, 2),
        _fixed("specialty.guardian.last_guard.move_m", 1),
        _int("specialty.shield_bearer.corner_guard.prep_ap", 1, 1, 2),
        _fixed("specialty.shield_bearer.step_together.stance_ap_discount", 1),
        _int("specialty.field_medic.combat_medicine.res_restore", 1, 1, 3),
        _fixed("specialty.field_medic.second_wind.temporary_ap", 1),
        _int("injury.repairable_forearm.ordinary_persistent_difficulty", 4, 3, 6),
        _int("specialty.trauma.salvage.persistent_difficulty_reduction", 2, 1, 3),
        _fixed("specialty.resuscitator.resuscitation.window_hold", 1),
        _fixed("specialty.diplomat.negotiating_position.counteroffers", 1),
        _int("specialty.autodrones.reacquire.search_radius_m", 5, 3, 10),
        _int("specialty.autodrones.machine_squad.additional_drones", 2, 1, 3),
        _int("specialty.autodrones.machine_squad.group_command_ap", 1, 1, 2),
        _int("specialty.autodrones.machine_squad.contingency_count", 1, 1, 2),
    ]

    for specialty, node, family in SPECIALTY_PARAMETER_ROWS:
        value_type, seed, minimum, maximum = SPECIALTY_PARAMETER_FAMILIES[family]
        identifier = specialty_parameter_id(specialty, node, family)
        specs.append(
            _int(identifier, int(seed), int(minimum), int(maximum))
            if value_type == "int"
            else _float(identifier, float(seed), float(minimum), float(maximum))
        )

    for weapon, seed in FIREARM_SEEDS.items():
        specs.extend(
            [
                _int(f"equipment.{weapon}.damage", seed["damage"], max(1, seed["damage"] - 2), min(10, seed["damage"] + 2)),
                _int(f"equipment.{weapon}.target_pen", seed["target_pen"], max(0, seed["target_pen"] - 2), seed["target_pen"] + 2),
                _int(f"equipment.{weapon}.material_pen", seed["material_pen"], max(0, seed["material_pen"] - 2), min(8, seed["material_pen"] + 2)),
                _vector(
                    f"equipment.{weapon}.range_breakpoints_m",
                    seed["ranges"],
                    [max(1, math.ceil(item * 0.7)) for item in seed["ranges"]],
                    [math.floor(item * 1.3) for item in seed["ranges"]],
                    integer=True,
                    ordered="strictly_increasing",
                ),
                _vector(f"equipment.{weapon}.range_modifiers", [0, 2, 5, 8], [-2, 0, 3, 6], [2, 4, 7, 10], integer=True, ordered="nondecreasing"),
                _int(f"equipment.{weapon}.recoil", seed["recoil"], 1, 5),
                _int(f"equipment.{weapon}.capacity", seed["capacity"], max(1, math.floor(seed["capacity"] * 0.5)), math.ceil(seed["capacity"] * 1.5)),
                _int(f"equipment.{weapon}.reload_ap", seed["reload"], 1, 6),
                _int(f"equipment.{weapon}.handling", seed["handling"], 0, 4),
            ]
        )
    specs.extend(
        [
            _int("equipment.standard_body_armor.res", 4, 1, 7),
            _int("equipment.standard_body_armor.burden", 1, 0, 4),
            _int("equipment.heavy_body_armor.res", 7, 4, 10),
            _int("equipment.heavy_body_armor.burden", 3, 0, 4),
            _int("equipment.ballistic_shield.cover_hp", 16, 8, 30),
            _int("equipment.ballistic_shield.material_resistance", 4, 2, 6),
            _int("equipment.ballistic_shield.burden", 4, 0, 4),
            _int("equipment.ballistic_shield.handling", 4, 0, 4),
            _float("equipment.frag_grenade.fuse_s", 4.0, 2.0, 6.0),
            _vector("equipment.frag_grenade.band_end_m", [4, 8, 14, 20], [3, 6, 10, 14], [6, 11, 19, 26], integer=True, ordered="strictly_increasing"),
            _vector("equipment.frag_grenade.damage", [7, 5, 3, 1], [5, 3, 1, 0], [9, 7, 5, 3], integer=True, ordered="nonincreasing"),
            _vector("equipment.frag_grenade.target_pen", [3, 2, 1, 0], [1, 0, 0, 0], [5, 4, 3, 2], integer=True, ordered="nonincreasing"),
            _vector("equipment.frag_grenade.material_pen", [2, 1, 0, 0], [0, 0, 0, 0], [4, 3, 2, 2], integer=True, ordered="nonincreasing"),
            _float("equipment.flash_grenade.fuse_s", 4.0, 2.0, 6.0),
            _vector("equipment.flash_grenade.band_end_m", [4, 8, 12], [3, 6, 8], [6, 11, 16], integer=True, ordered="strictly_increasing"),
            _vector("equipment.flash_grenade.difficulty", [4, 3, 2], [2, 1, 0], [6, 5, 4], integer=True, ordered="nonincreasing"),
            _float("equipment.smoke_grenade.fuse_s", 2.0, 1.0, 3.0),
            _vector("equipment.smoke_grenade.radius_m", [2, 6, 8], [1, 4, 5], [3, 9, 12], integer=True, ordered="strictly_increasing"),
            _float("equipment.smoke_grenade.dense_s", 18.0, 10.8, 27.0),
            _float("equipment.smoke_grenade.dissipation_s", 6.0, 3.6, 9.0),
            _int("equipment.medkit.supplies", 6, 4, 9),
            _int("equipment.trauma_kit.supplies", 12, 7, 18),
            _int("equipment.trauma_kit.setup_ap", 2, 1, 3),
            _int("equipment.fpv_drone.durability", 4, 2, 6),
            _float("equipment.fpv_drone.move_m_per_ap", 12.0, 7.2, 18.0),
            _float("equipment.fpv_drone.battery_s", 300.0, 180.0, 450.0),
            _float("equipment.fpv_drone.signal_urban_m", 300.0, 180.0, 450.0),
            _float("equipment.fpv_drone.signal_open_m", 800.0, 480.0, 1200.0),
            _int("equipment.fpv_fragmentation_payload.attack_ap", 1, 1, 2),
            _int("equipment.autodrone.durability", 8, 5, 12),
            _float("equipment.autodrone.move_m_per_ap", 8.0, 4.8, 12.0),
            _int("equipment.autodrone.actions_per_turn", 2, 1, 3),
            _float("equipment.autodrone.battery_s", 900.0, 540.0, 1350.0),
            _float("equipment.autodrone.signal_urban_m", 500.0, 300.0, 750.0),
            _float("equipment.autodrone.signal_open_m", 1500.0, 900.0, 2250.0),
            _int("equipment.autodrone_light_weapon.damage", 4, 2, 6),
            _int("equipment.autodrone_light_weapon.target_pen", 1, 0, 3),
            _int("equipment.autodrone_light_weapon.material_pen", 1, 0, 3),
            _vector("equipment.autodrone_light_weapon.range_breakpoints_m", [15, 30, 50], [11, 21, 35], [19, 39, 65], integer=True, ordered="strictly_increasing"),
            _vector("equipment.autodrone_light_weapon.range_modifiers", [0, 2, 5, 8], [-2, 0, 3, 6], [2, 4, 7, 10], integer=True, ordered="nondecreasing"),
            _int("equipment.autodrone_light_weapon.recoil", 2, 1, 5),
            _int("equipment.autodrone_light_weapon.capacity", 10, 5, 15),
            _int("equipment.autodrone_light_weapon.reload_ap", 3, 2, 5),
            _int("equipment.autodrone_light_weapon.attack_ap_equivalent", 2, 1, 2),
            _int("equipment.autodrone_light_weapon.targeting_bonus", 4, 2, 5),
            _int("equipment.field_relay.durability", 6, 4, 9),
            _int("equipment.field_relay.setup_ap", 3, 2, 4),
            _float("equipment.field_relay.battery_s", 1200.0, 720.0, 1800.0),
            _float("equipment.field_relay.sensor_radius_m", 25.0, 15.0, 37.5),
            _float("equipment.field_relay.visual_m", 40.0, 24.0, 60.0),
            _float("equipment.field_relay.relay_m", 150.0, 90.0, 225.0),
        ]
    )
    material_seeds = {
        "light_interior": (1, 4, (0, 2), (2, 8)),
        "timber": (2, 8, (1, 3), (4, 14)),
        "light_masonry": (3, 12, (2, 4), (8, 20)),
        "reinforced_plate": (4, 16, (3, 5), (10, 26)),
        "reinforced_concrete": (5, 24, (4, 6), (16, 40)),
        "hardened_barrier": (6, 32, (5, 8), (20, 50)),
    }
    for name, (resistance, hp, mr_range, hp_range) in material_seeds.items():
        specs.append(_int(f"material.{name}.resistance", resistance, *mr_range))
        specs.append(_int(f"material.{name}.cover_hp", hp, *hp_range))
    duplicate_ids = sorted(parameter_id for parameter_id, count in _counts(spec.parameter_id for spec in specs).items() if count > 1)
    if duplicate_ids:
        raise RuntimeError(f"duplicate manifest IDs: {duplicate_ids}")
    manifest = ParameterManifest({spec.parameter_id: spec for spec in specs})
    manifest.seed_set()
    return manifest


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result
