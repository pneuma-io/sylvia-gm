from __future__ import annotations

from dataclasses import asdict, dataclass
import itertools
import math
from typing import Any, Iterable

from .fixtures import weapon_state
from .parameters import Authority, ParameterError, ParameterSet
from .probes import class_baseline_probe, exact_attack_distribution
from .tactical import default_team_spec, paired_mirrored_team_scenarios


OBJECTIVE_AUTOMATICITY_REVIEW_BAND = (0.05, 0.95)


def objective_automaticity_failure(interval: dict[str, float]) -> bool:
    """Require the full confidence interval to sit in an automatic outcome tail."""
    low, high = OBJECTIVE_AUTOMATICITY_REVIEW_BAND
    return float(interval["high"]) <= low or float(interval["low"]) >= high


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_index: int
    updates: dict[str, Any]
    parameter_hash: str
    objective: tuple[float, ...]
    canonical_violations: tuple[str, ...]
    directional_failures: tuple[str, ...]
    dominance_flags: tuple[str, ...]
    specialization_failures: tuple[str, ...]
    normalized_seed_movement: float

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["objective"] = list(self.objective)
        return result


def _domain_values(spec: Any) -> list[Any]:
    if spec.value_type == "integer":
        return list(range(spec.minimum, spec.maximum + 1))
    if spec.value_type == "float":
        midpoint = (spec.minimum + spec.maximum) / 2
        return sorted({spec.minimum, spec.seed, midpoint, spec.maximum})
    raise ParameterError(f"transparent grid search supports scalar integer/float IDs, not {spec.parameter_id}:{spec.value_type}")


def candidate_updates(parameters: ParameterSet, parameter_ids: list[str], max_candidates: int) -> Iterable[dict[str, Any]]:
    if not parameter_ids:
        parameter_ids = ["hit_quality.clean_margin"]
    for parameter_id in parameter_ids:
        if parameter_id not in parameters.manifest.specs:
            raise ParameterError(f"unknown optimizer parameter {parameter_id}")
        if parameters.manifest.specs[parameter_id].authority is not Authority.TUNABLE:
            raise ParameterError(f"optimizer cannot mutate non-TUNABLE ID {parameter_id}")
    domains = [_domain_values(parameters.manifest.specs[parameter_id]) for parameter_id in parameter_ids]
    total = math.prod(len(domain) for domain in domains)
    if total > max_candidates:
        raise ParameterError(f"candidate grid has {total} values, exceeding --max-candidates {max_candidates}")
    for values in itertools.product(*domains):
        yield dict(zip(parameter_ids, values, strict=True))


def _normalized_movement(candidate: ParameterSet, updates: dict[str, Any]) -> float:
    movement = 0.0
    for parameter_id, value in updates.items():
        spec = candidate.manifest.specs[parameter_id]
        span = spec.maximum - spec.minimum
        movement += abs(value - spec.seed) / span if span else 0.0
    return movement


def evaluate_candidate(index: int, candidate: ParameterSet, updates: dict[str, Any]) -> CandidateEvaluation:
    canonical_violations: list[str] = []
    directional_failures: list[str] = []
    dominance_flags: list[str] = []
    specialization_failures: list[str] = []
    try:
        candidate.manifest.validate_values(candidate.values, require_complete=True)
    except ParameterError as error:
        canonical_violations.append(str(error))
    if not canonical_violations:
        close_shotgun = exact_attack_distribution("shotgun", 5, 4, 1, candidate.values["check.difficulty.standard"], "none", 1, candidate)
        close_rifle = exact_attack_distribution("service_rifle", 5, 4, 1, candidate.values["check.difficulty.standard"], "none", 1, candidate)
        shotgun_output = close_shotgun["expected_res_damage"] + close_shotgun["expected_hp_damage"]
        rifle_output = close_rifle["expected_res_damage"] + close_rifle["expected_hp_damage"]
        if shotgun_output <= rifle_output:
            directional_failures.append("SHOTGUN does not exceed SERVICE RIFLE in intended close-band output")
        class_result = class_baseline_probe(candidate)
        specialization_failures.extend(finding["subject"] for finding in class_result["findings"])
        cross_role: dict[str, list[str]] = {}
        rifle = weapon_state("service_rifle", candidate)
        rifle_close = exact_attack_distribution("service_rifle", 5, 4, 1, candidate.values["check.difficulty.standard"], "none", 1, candidate)
        rifle_close_output = rifle_close["expected_hp_damage"] + rifle_close["expected_res_damage"]
        rifle_handling = candidate.values["equipment.service_rifle.handling"] * candidate.values["cqc.contact_handling_multiplier"]
        rifle_tight = exact_attack_distribution("service_rifle", 1, 4, 1, candidate.values["check.difficulty.standard"] + rifle_handling, "none", 1, candidate)
        rifle_sustain = rifle.magazine_capacity / candidate.values["equipment.service_rifle.reload_ap"]
        for weapon in ("pistol", "smg", "service_rifle", "shotgun", "precision_rifle"):
            state = weapon_state(weapon, candidate)
            close = exact_attack_distribution(weapon, 5, 4, 1, candidate.values["check.difficulty.standard"], "none", 1, candidate)
            close_output = close["expected_hp_damage"] + close["expected_res_damage"]
            handling = candidate.values[f"equipment.{weapon}.handling"] * candidate.values["cqc.contact_handling_multiplier"]
            tight = exact_attack_distribution(weapon, 1, 4, 1, candidate.values["check.difficulty.standard"] + handling, "none", 1, candidate)
            sustain = state.magazine_capacity / candidate.values[f"equipment.{weapon}.reload_ap"]
            advantages = []
            if close_output > rifle_close_output * 1.25:
                advantages.append("close-range output")
            if tight["hit_rate"] > rifle_tight["hit_rate"] + 0.25:
                advantages.append("tight-space hit probability")
            if sustain > rifle_sustain * 1.25 and "FULL AUTO" in state.fire_modes:
                advantages.append("sustained automatic ammunition economy")
            cross_role[weapon] = advantages
        dominance_flags.extend(weapon for weapon, advantages in cross_role.items() if len(advantages) >= 3)
    movement = _normalized_movement(candidate, updates)
    objective = (
        float(len(canonical_violations)),
        float(len(directional_failures)),
        float(len(dominance_flags)),
        float(len(specialization_failures)),
        movement,
    )
    return CandidateEvaluation(
        index,
        updates,
        candidate.hash,
        objective,
        tuple(canonical_violations),
        tuple(directional_failures),
        tuple(dominance_flags),
        tuple(specialization_failures),
        movement,
    )


def optimize(parameters: ParameterSet, parameter_ids: list[str], max_candidates: int = 1000) -> dict[str, Any]:
    history: list[CandidateEvaluation] = []
    rejected: list[dict[str, Any]] = []
    for updates in candidate_updates(parameters, parameter_ids, max_candidates):
        try:
            candidate = parameters.with_updates(updates, optimizer=True)
        except ParameterError as error:
            rejected.append({"updates": updates, "reason": str(error)})
            continue
        history.append(evaluate_candidate(len(history), candidate, updates))
    if not history:
        return {
            "status": "STRUCTURAL FAILURE",
            "failure": {
                "smallest_reproducible_constraints": ["manifest bounds/couplings reject every candidate"],
                "best_parameter_set": None,
                "exhausted_bounds": parameter_ids,
                "conflict_type": "mathematical/manifest",
                "replay": "exact manifest validation",
                "human_change_categories": ["review admitted bounds or coupling constraints (proposal only)"],
            },
            "history": [],
            "rejected": rejected,
        }
    history.sort(key=lambda item: (item.objective, item.candidate_index))
    best = history[0]
    best_parameter_values = parameters.with_updates(best.updates, optimizer=True).values
    status = "CANDIDATE" if best.objective[:4] == (0.0, 0.0, 0.0, 0.0) else "STRUCTURAL FAILURE"
    boundary_flags = []
    top_count = max(1, math.ceil(len(history) * 0.3))
    top = history[:top_count]
    for parameter_id in parameter_ids or ["hit_quality.clean_margin"]:
        spec = parameters.manifest.specs[parameter_id]
        at_min = sum(item.updates.get(parameter_id) == spec.minimum for item in top)
        at_max = sum(item.updates.get(parameter_id) == spec.maximum for item in top)
        best_value = best.updates.get(parameter_id)
        if best_value in {spec.minimum, spec.maximum} or at_min / top_count > 0.3 or at_max / top_count > 0.3:
            boundary_flags.append(
                {
                    "code": "BOUNDARY PRESSURE",
                    "parameter_id": parameter_id,
                    "best_value": best_value,
                    "minimum": spec.minimum,
                    "maximum": spec.maximum,
                    "top_fraction_at_min": at_min / top_count,
                    "top_fraction_at_max": at_max / top_count,
                }
            )
    dominance_findings = [
        {
            "code": "GENERAL DOMINANCE",
            "subject": weapon,
            "evidence": "best candidate exceeds the 25% review threshold across close-range output, constrained handling, and sustained automatic economy",
            "candidate_index": best.candidate_index,
            "parameter_hash": best.parameter_hash,
        }
        for weapon in best.dominance_flags
    ]
    result: dict[str, Any] = {
        "status": status,
        "strategy": "transparent exhaustive scalar grid with lexicographic objectives",
        "objective_order": ["canonical correctness", "directional behavior", "no general dominance", "meaningful specialization", "minimum normalized seed movement"],
        "parameter_ids": parameter_ids or ["hit_quality.clean_margin"],
        "mutation_authority": "TUNABLE manifest IDs only",
        "non_manifest_mutation_count": 0,
        "best": best.to_dict(),
        "best_parameter_values": best_parameter_values,
        "history": [item.to_dict() for item in history],
        "rejected": rejected,
        "findings": dominance_findings + boundary_flags,
    }
    if status == "STRUCTURAL FAILURE":
        exhausted_bounds = [
            parameter_id
            for parameter_id in (parameter_ids or ["hit_quality.clean_margin"])
            if best.updates.get(parameter_id) in {
                parameters.manifest.specs[parameter_id].minimum,
                parameters.manifest.specs[parameter_id].maximum,
            }
        ]
        result["failure"] = {
            "smallest_reproducible_constraints": list(best.canonical_violations + best.directional_failures + best.dominance_flags + best.specialization_failures),
            "best_parameter_set": {"evaluation": best.to_dict(), "values": best_parameter_values},
            "exhausted_bounds": exhausted_bounds,
            "conflict_type": "mathematical/procedural or fixture-related",
            "replay": "candidate history exact probe identities",
            "human_change_categories": ["review source constraint, parameter admission, or fixture intent (proposal only)"],
        }
    return result


def optimize_objective(
    parameters: ParameterSet,
    scenario_id: str,
    parameter_ids: list[str],
    *,
    max_candidates: int = 1000,
    master_seed: int = 20260824,
    pairs: int = 100,
) -> dict[str, Any]:
    """Search a declared objective without treating an asymmetric scenario as 50/50.

    The broad review band detects near-automatic and near-impossible fixtures. It
    is a testing usability gate, not a player mechanic or a target win rate.
    Canonical/directional/dominance/specialization failures remain lexicographically
    more important than this gate.
    """
    if scenario_id not in {"TEAM-EXTRACT-4V4-V1", "TEAM-BREACH-4V4-V1", "TEAM-RECON-HACK-4V4-V1"}:
        raise ParameterError(f"objective optimization requires a mixed-team objective scenario, got {scenario_id}")
    if not parameter_ids:
        raise ParameterError("objective optimization requires at least one explicit --parameter")
    if pairs <= 0:
        raise ParameterError("objective optimization pairs must be positive")
    low, high = OBJECTIVE_AUTOMATICITY_REVIEW_BAND
    history: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate_index, updates in enumerate(candidate_updates(parameters, parameter_ids, max_candidates)):
        try:
            candidate = parameters.with_updates(updates, optimizer=True)
        except ParameterError as error:
            rejected.append({"updates": updates, "reason": str(error)})
            continue
        guard = evaluate_candidate(candidate_index, candidate, updates)
        tactical = paired_mirrored_team_scenarios(
            default_team_spec(scenario_id),
            candidate,
            master_seed,
            pairs,
            capture_replay=False,
        )
        outcome_interval = tactical["subject_outcome_share"]
        estimate = float(outcome_interval["estimate"])
        automatic = objective_automaticity_failure(outcome_interval)
        distance_to_band = max(low - float(outcome_interval["high"]), float(outcome_interval["low"]) - high, 0.0)
        objective = (
            *guard.objective[:4],
            float(automatic),
            distance_to_band,
            guard.normalized_seed_movement,
        )
        history.append(
            {
                **guard.to_dict(),
                "objective": list(objective),
                "objective_outcome_share": outcome_interval,
                "terminal_time_seconds": tactical["terminal_time_seconds"],
                "automaticity_review_failure": automatic,
                "distance_to_review_band": distance_to_band,
                "failures": tactical["failures"],
                "sample_replay": tactical["sample_replay"],
            }
        )
    if not history:
        return {
            "status": "STRUCTURAL FAILURE",
            "scenario_id": scenario_id,
            "review_band": {"exclusive_low": low, "exclusive_high": high, "meaning": "automaticity review, not a 50/50 target"},
            "failure": {
                "smallest_reproducible_constraints": ["manifest bounds/couplings reject every candidate"],
                "best_parameter_set": None,
                "exhausted_bounds": parameter_ids,
                "conflict_type": "mathematical/manifest",
                "replay": "exact manifest validation",
                "human_change_categories": ["review admitted bounds or coupling constraints (proposal only)"],
            },
            "history": [],
            "rejected": rejected,
        }
    history.sort(key=lambda item: (tuple(item["objective"]), item["candidate_index"]))
    best = history[0]
    best_parameters = parameters.with_updates(best["updates"], optimizer=True)
    best["sample_replay"] = paired_mirrored_team_scenarios(
        default_team_spec(scenario_id),
        best_parameters,
        master_seed,
        1,
        capture_replay=True,
    )["sample_replay"]
    core_failure = any(best[key] for key in ("canonical_violations", "directional_failures", "dominance_flags", "specialization_failures"))
    status = "STRUCTURAL FAILURE" if core_failure or best["automaticity_review_failure"] or best["failures"] else "CANDIDATE"
    boundary_findings: list[dict[str, Any]] = []
    top = history[: max(1, math.ceil(len(history) * 0.3))]
    for parameter_id in parameter_ids:
        spec = parameters.manifest.specs[parameter_id]
        best_value = best["updates"].get(parameter_id)
        at_min = sum(item["updates"].get(parameter_id) == spec.minimum for item in top)
        at_max = sum(item["updates"].get(parameter_id) == spec.maximum for item in top)
        if best_value in {spec.minimum, spec.maximum} or at_min / len(top) > 0.3 or at_max / len(top) > 0.3:
            boundary_findings.append(
                {
                    "code": "BOUNDARY PRESSURE",
                    "parameter_id": parameter_id,
                    "best_value": best_value,
                    "minimum": spec.minimum,
                    "maximum": spec.maximum,
                    "top_fraction_at_min": at_min / len(top),
                    "top_fraction_at_max": at_max / len(top),
                }
            )
    findings = boundary_findings
    if best["automaticity_review_failure"]:
        findings.insert(
            0,
            {
                "code": "OBJECTIVE AUTOMATICITY",
                "severity": "FAIL",
                "scenario_id": scenario_id,
                "outcome_share": best["objective_outcome_share"],
                "review_band": {"exclusive_low": low, "exclusive_high": high},
            },
        )
    result: dict[str, Any] = {
        "status": status,
        "strategy": "transparent exhaustive manifest grid with common paired/mirrored seeds",
        "objective_order": [
            "canonical correctness",
            "directional behavior",
            "no general dominance",
            "meaningful specialization",
            "objective automaticity review",
            "minimum normalized seed movement",
        ],
        "scenario_id": scenario_id,
        "review_band": {"exclusive_low": low, "exclusive_high": high, "meaning": "automaticity review, not a 50/50 target"},
        "master_seed": master_seed,
        "paired_mirrored_runs_per_candidate": pairs,
        "qualification": "FINAL" if pairs >= 50000 else ("ORDINARY" if pairs >= 10000 else "SMOKE_NON_QUALIFYING"),
        "parameter_ids": parameter_ids,
        "mutation_authority": "TUNABLE manifest IDs only",
        "non_manifest_mutation_count": 0,
        "best": best,
        "best_parameter_values": best_parameters.values,
        "history": history,
        "rejected": rejected,
        "findings": findings,
    }
    if status == "STRUCTURAL FAILURE":
        constraints = list(best["canonical_violations"] + best["directional_failures"] + best["dominance_flags"] + best["specialization_failures"])
        if best["automaticity_review_failure"]:
            constraints.append(
                f"{scenario_id} outcome interval [{best['objective_outcome_share']['low']:.6f}, {best['objective_outcome_share']['high']:.6f}] remains wholly outside the ({low}, {high}) automaticity review band"
            )
        result["failure"] = {
            "smallest_reproducible_constraints": constraints,
            "best_parameter_set": {"evaluation": best, "values": best_parameters.values},
            "exhausted_bounds": [
                parameter_id
                for parameter_id in parameter_ids
                if best["updates"].get(parameter_id) in {parameters.manifest.specs[parameter_id].minimum, parameters.manifest.specs[parameter_id].maximum}
            ],
            "conflict_type": "fixture/tactical procedure or values outside the admitted manifest surface",
            "replay": best["sample_replay"],
            "human_change_categories": [
                "review objective fixture/composition intent",
                "admit missing tactical subsystem only through canonical design",
                "review parameter bounds (proposal only)",
            ],
        }
    return result
