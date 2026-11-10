from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import write_csv, write_json
from .fixtures import map_contracts
from .parameters import ParameterSet
from .probes import all_exact_probes
from .provenance import provenance_record
from .rng import derive_seed
from .scenario_runtime import EXACT_RESULT_KEYS
from .scenarios import scenario_registry
from .simulation import (
    DuelSpec,
    duel_pair_chunk,
    merge_duel_pair_chunks,
    paired_mirrored_duels,
    progression_sweep,
)
from .specialties import specialty_coverage
from .tactical import (
    TeamScenarioSpec,
    default_team_spec,
    merge_team_pair_chunks,
    paired_mirrored_team_scenarios,
    team_pair_chunk,
)


def _chunk_ranges(total: int, chunk_count: int) -> list[tuple[int, int]]:
    chunks = min(total, chunk_count)
    base, remainder = divmod(total, chunks)
    result: list[tuple[int, int]] = []
    start = 0
    for index in range(chunks):
        count = base + (1 if index < remainder else 0)
        result.append((start, count))
        start += count
    return result


def _collect_findings(value: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        own = value.get("findings")
        if isinstance(own, list):
            findings.extend(item for item in own if isinstance(item, dict))
        for key, child in value.items():
            if key != "findings":
                findings.extend(_collect_findings(child))
    elif isinstance(value, list):
        for child in value:
            findings.extend(_collect_findings(child))
    return findings


STRUCTURAL_FINDING_CODES = {
    "STRUCTURAL FAILURE",
    "DEAD NODE",
    "PERMISSION PERK",
    "BASELINE DUPLICATION",
    "DEPENDENCY FAILURE",
    "AMBIGUOUS NODE",
    "UNIMPLEMENTABLE SPEC GAP",
}
NUMERIC_FINDING_CODES = {
    "DIRECTIONAL_FAILURE",
    "TRAINING_TALENT_CONFLICT",
    "GENERAL DOMINANCE",
    "BOUNDARY PRESSURE",
}


def _categorize_findings(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    categorized: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {"STRUCTURAL_SPEC": [], "NUMERIC_BALANCE": [], "INVARIANT_RUNTIME": []}
    for finding in findings:
        code = finding.get("code", "UNCLASSIFIED")
        category = "STRUCTURAL_SPEC" if code in STRUCTURAL_FINDING_CODES else ("NUMERIC_BALANCE" if code in NUMERIC_FINDING_CODES else "INVARIANT_RUNTIME")
        item = {**finding, "category": category}
        categorized.append(item)
        groups[category].append(item)
    return categorized, groups


def build_baseline(
    parameters: ParameterSet,
    *,
    master_seed: int,
    runs: int,
    progression_runs: int | None = None,
    team_runs: int | None = None,
    objective_overrides: dict[str, dict[str, Any]] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    if workers <= 0:
        raise ValueError("workers must be positive")
    exact = all_exact_probes(parameters)
    specialty = specialty_coverage(parameters)
    effective_progression_runs = progression_runs if progression_runs is not None else runs
    effective_team_runs = runs if team_runs is None else team_runs
    team_specs = {
        "OBJECTIVE_SUPPORT": TeamScenarioSpec(),
        "COMBAT_POSTURES": TeamScenarioSpec(
            classes_a=("BREACHER", "SHARPSHOOTER", "RIFLEMAN", "TECH"),
            classes_b=("BREACHER", "SHARPSHOOTER", "RIFLEMAN", "TECH"),
            policies_a=("POLICY_DIRECT", "POLICY_COVER_FIRST", "POLICY_PRESSURE", "POLICY_VOLUME"),
            policies_b=("POLICY_DIRECT", "POLICY_COVER_FIRST", "POLICY_PRESSURE", "POLICY_VOLUME"),
            weapons_a=("shotgun", "precision_rifle", "service_rifle", "lmg"),
            weapons_b=("shotgun", "precision_rifle", "service_rifle", "lmg"),
        ),
    }
    objective_specs = {
        scenario_id: default_team_spec(scenario_id)
        for scenario_id in ("TEAM-EXTRACT-4V4-V1", "TEAM-BREACH-4V4-V1", "TEAM-RECON-HACK-4V4-V1")
    }
    objective_scenarios: dict[str, dict[str, Any]] = {}
    overrides = objective_overrides or {}
    unknown_overrides = sorted(set(overrides) - set(objective_specs))
    if unknown_overrides:
        raise ValueError(f"unknown mixed-objective override IDs: {', '.join(unknown_overrides)}")
    for scenario_id in objective_specs:
        if scenario_id not in overrides:
            continue
        result = overrides[scenario_id]
        if result.get("scenario_id") != scenario_id:
            raise ValueError(f"objective override identity mismatch for {scenario_id}")
        if result.get("parameter_set_hash") != parameters.hash:
            raise ValueError(f"objective override parameter hash mismatch for {scenario_id}")
        if result.get("master_seed") != master_seed:
            raise ValueError(f"objective override master seed mismatch for {scenario_id}")
        if not result.get("sample_replay"):
            raise ValueError(f"objective override lacks deterministic sample replay for {scenario_id}")
        objective_scenarios[scenario_id] = result

    if workers == 1:
        monte_carlo = paired_mirrored_duels(
            DuelSpec(), parameters, master_seed, runs
        )
        progression = progression_sweep(
            parameters,
            master_seed,
            effective_progression_runs,
        )
        team_variants = {
            variant: paired_mirrored_team_scenarios(
                spec,
                parameters,
                master_seed,
                effective_team_runs,
            )
            for variant, spec in team_specs.items()
        }
        for scenario_id, spec in objective_specs.items():
            if scenario_id not in objective_scenarios:
                objective_scenarios[scenario_id] = paired_mirrored_team_scenarios(
                    spec,
                    parameters,
                    master_seed,
                    effective_team_runs,
                )
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            duel_futures = [
                pool.submit(
                    duel_pair_chunk,
                    DuelSpec(),
                    parameters,
                    master_seed,
                    start,
                    count,
                    capture_replay=start == 0,
                )
                for start, count in _chunk_ranges(runs, workers)
            ]
            progression_futures = {
                level: [
                    pool.submit(
                        duel_pair_chunk,
                        DuelSpec(level=level),
                        parameters,
                        derive_seed(master_seed, "progression", level),
                        start,
                        count,
                        capture_replay=False,
                    )
                    for start, count in _chunk_ranges(
                        effective_progression_runs,
                        workers,
                    )
                ]
                for level in (1, 5, 10, 15, 20)
            }
            team_futures = {
                variant: [
                    pool.submit(
                        team_pair_chunk,
                        spec,
                        parameters,
                        master_seed,
                        start,
                        count,
                        capture_replay=start == 0,
                    )
                    for start, count in _chunk_ranges(
                        effective_team_runs,
                        workers,
                    )
                ]
                for variant, spec in team_specs.items()
            }
            objective_futures = {
                scenario_id: [
                    pool.submit(
                        team_pair_chunk,
                        spec,
                        parameters,
                        master_seed,
                        start,
                        count,
                        capture_replay=start == 0,
                    )
                    for start, count in _chunk_ranges(
                        effective_team_runs,
                        workers,
                    )
                ]
                for scenario_id, spec in objective_specs.items()
                if scenario_id not in objective_scenarios
            }
            monte_carlo = merge_duel_pair_chunks(
                DuelSpec(),
                parameters,
                master_seed,
                [future.result() for future in duel_futures],
            )
            progression = {
                "scenario_family": "PROGRESSION",
                "levels": [
                    {
                        "level": level,
                        "outcome_share": result["subject_outcome_share"],
                        "terminal_time_seconds": result[
                            "terminal_time_seconds"
                        ],
                        "qualification": result["qualification"],
                    }
                    for level, result in (
                        (
                            level,
                            merge_duel_pair_chunks(
                                DuelSpec(level=level),
                                parameters,
                                derive_seed(master_seed, "progression", level),
                                [
                                    future.result()
                                    for future in progression_futures[level]
                                ],
                            ),
                        )
                        for level in (1, 5, 10, 15, 20)
                    )
                ],
            }
            team_variants = {
                variant: merge_team_pair_chunks(
                    team_specs[variant],
                    parameters,
                    master_seed,
                    [future.result() for future in team_futures[variant]],
                )
                for variant in team_specs
            }
            for scenario_id in objective_specs:
                if scenario_id in objective_futures:
                    objective_scenarios[scenario_id] = merge_team_pair_chunks(
                        objective_specs[scenario_id],
                        parameters,
                        master_seed,
                        [
                            future.result()
                            for future in objective_futures[scenario_id]
                        ],
                    )
    team_control = team_variants["OBJECTIVE_SUPPORT"]
    team = {
        "scenario_family": "MIXED-TEAM-OBJECTIVES",
        "results": [
            {
                "scenario_id": "TEAM-CONTROL-4V4-V1",
                "status": "IMPLEMENTED",
                "qualification": team_control["qualification"],
                "outcome_share": team_control["subject_outcome_share"],
                "terminal_time_seconds": team_control["terminal_time_seconds"],
                "paired_objective_seconds_effect": team_control["paired_objective_seconds_effect"],
                "paired_hp_damage_effect": team_control["paired_hp_damage_effect"],
                "policy_variants": {
                    variant: {
                        "policies": sorted(set(result["sample_replay"]["policies"].values())) if result["sample_replay"] else [],
                        "qualification": result["qualification"],
                        "outcome_share": result["subject_outcome_share"],
                        "terminal_time_seconds": result["terminal_time_seconds"],
                    }
                    for variant, result in team_variants.items()
                },
            },
            *[
                {
                    "scenario_id": scenario_id,
                    "status": "IMPLEMENTED",
                    "qualification": scenario_result["qualification"],
                    "outcome_share": scenario_result["subject_outcome_share"],
                    "terminal_time_seconds": scenario_result["terminal_time_seconds"],
                }
                for scenario_id, scenario_result in objective_scenarios.items()
            ],
        ],
        "team_control": team_control,
        "team_control_variants": team_variants,
        "objective_scenarios": objective_scenarios,
    }
    scenario_coverage = [definition.to_dict() for definition in scenario_registry().values()]
    scenario_execution_index: dict[str, dict[str, Any]] = {}
    for definition in scenario_registry().values():
        if definition.scenario_id in EXACT_RESULT_KEYS:
            evidence = f"exact.{EXACT_RESULT_KEYS[definition.scenario_id]}"
        elif definition.scenario_id == "SPECIALTY-COUNTERFACTUAL-V1":
            evidence = (
                "specialty_coverage.counterfactuals/scenario_matrix/"
                "objective_scenario_matrix"
            )
        elif definition.scenario_id == "MIRROR-DUEL-V1":
            evidence = "monte_carlo"
        elif definition.scenario_id == "TEAM-CONTROL-4V4-V1":
            evidence = "mixed_team_objectives.team_control_variants"
        elif definition.scenario_id.startswith("TEAM-"):
            evidence = f"mixed_team_objectives.objective_scenarios.{definition.scenario_id}"
        else:
            evidence = "mixed_team_objectives.results"
        blocked = definition.support_status in {"UNRESOLVED_SOURCE", "AMBIGUOUS_SOURCE", "MISSING_DEPENDENCY", "DEFERRED_OUT_OF_SCOPE"}
        scenario_execution_index[definition.scenario_id] = {
            "scenario_id": definition.scenario_id,
            "family": definition.family,
            "map_id": definition.map_id,
            "support_status": definition.support_status,
            "execution_status": "FORMALLY_BLOCKED" if blocked else "EXECUTED",
            "qualification": (
                "STRUCTURAL_FAILURE"
                if blocked
                else "EXACT_SPECIALTY_OBJECTIVES_AND_STRUCTURAL_COUNTERFACTUALS"
                if definition.scenario_id == "SPECIALTY-COUNTERFACTUAL-V1"
                else "MONTE_CARLO"
                if definition.executor in {"duel_monte_carlo", "team_objective"}
                else "EXACT"
            ),
            "evidence_path": evidence,
            "unsupported_dependencies": list(definition.unsupported_dependencies),
        }
    provenance = provenance_record(parameters.hash)
    findings = _collect_findings(exact) + specialty["findings"] + monte_carlo["findings"] + _collect_findings(team)
    findings.extend(
        {
            "code": "UNIMPLEMENTABLE SPEC GAP",
            "severity": "REVIEW",
            "scenario_id": scenario["scenario_id"],
            "evidence": dependency,
        }
        for scenario in scenario_coverage
        for dependency in scenario["unsupported_dependencies"]
    )
    findings, finding_groups = _categorize_findings(findings)
    invariants = {
        "source_drift": len(provenance["source_drift"]) == 0,
        "all_maps_declared": len(map_contracts()) == 7,
        "all_scenarios_versioned": all(item["version"] for item in scenario_coverage),
        "all_26_specialties_accounted": specialty["specialty_count"] == 26 and not specialty["missing_specialties"],
        "all_specialty_nodes_accounted": specialty["node_count"] == 364,
        "all_specialty_objective_roles_executed": (
            len(specialty["objective_scenario_matrix"]) == 26 * 5 * 3
            and all(
                item["control_metrics"]["terminal_reason"]
                and item["candidate_metrics"]["terminal_reason"]
                and item["control_metrics"]["event_log_hash"]
                != "NOT_CAPTURED"
                and item["candidate_metrics"]["event_log_hash"]
                != "NOT_CAPTURED"
                for item in specialty["objective_scenario_matrix"]
            )
        ),
        "explicit_unresolved_surfaced": specialty["coverage_counts"].get("UNRESOLVED_SOURCE", 0) == 0,
        "mirror_neutral": bool(monte_carlo["mirror_neutral"]),
        "team_control_mirror_neutral": all(bool(result["mirror_neutral"]) for result in team_variants.values()),
        "team_control_runtime_clean": all(not result["failures"] for result in team_variants.values()),
        "mixed_objective_runtime_clean": all(not result["failures"] for result in objective_scenarios.values()),
        "all_scenarios_executed_or_formally_blocked": all(
            item["execution_status"] in {"EXECUTED", "FORMALLY_BLOCKED"}
            for item in scenario_execution_index.values()
        ),
    }
    return {
        "schema_version": "1.0.0",
        "result_id": f"TD-v0.1-{parameters.hash[:12]}-{master_seed}",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_label": "v0.1",
        "provenance": provenance,
        "parameters": {"hash": parameters.hash, "values": parameters.values, "movement_from_seed": parameters.movement_from_seed()},
        "run_configuration": {
            "master_seed": master_seed,
            "paired_mirrored_runs": runs,
            "progression_runs_per_level": progression_runs if progression_runs is not None else runs,
            "team_control_paired_mirrored_runs_per_policy_variant": effective_team_runs,
            "objective_paired_mirrored_runs": {
                scenario_id: result["paired_mirrored_runs"] for scenario_id, result in objective_scenarios.items()
            },
            "objective_override_ids": sorted(overrides),
            "worker_processes": workers,
        },
        "invariants": invariants,
        "maps": map_contracts(),
        "scenario_coverage": scenario_coverage,
        "scenario_execution_index": scenario_execution_index,
        "exact": exact,
        "monte_carlo": monte_carlo,
        "progression": progression,
        "specialty_coverage": specialty,
        "mixed_team_objectives": team,
        "findings": findings,
        "finding_groups": finding_groups,
        "diagnostics": {
            "structural_spec_finding_count": len(finding_groups["STRUCTURAL_SPEC"]),
            "numeric_balance_finding_count": len(finding_groups["NUMERIC_BALANCE"]),
            "invariant_runtime_finding_count": len(finding_groups["INVARIANT_RUNTIME"]),
            "general_dominance_count": sum(item["code"] == "GENERAL DOMINANCE" for item in findings),
            "boundary_pressure_count": sum(item["code"] == "BOUNDARY PRESSURE" for item in findings),
        },
    }


def artifact_rows(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    check_rows = [{key: value for key, value in row.items() if key != "enumeration"} for row in result["exact"]["check_curves"]["rows"]]
    fire_rows = [{key: value for key, value in row.items() if key != "outcomes"} for row in result["exact"]["fire_range"]["rows"]]
    class_rows = result["exact"]["classes"]["rows"]
    specialty_rows = []
    for specialty, data in result["specialty_coverage"]["specialties"].items():
        for node in data["nodes"]:
            specialty_rows.append(
                {
                    "specialty": specialty,
                    "class_id": data["class_id"],
                    "node_id": node["node"]["node_id"],
                    "tier": node["node"]["tier"],
                    "name": node["node"]["name"],
                    "source_path": node["node"]["source_path"],
                    "source_commit": node["node"]["source_commit"],
                    "source_sha256": node["node"]["source_sha256"],
                    "state": node["state"],
                    "mechanic_id": node["mechanic_id"],
                    "counterfactual_id": node["counterfactual_id"],
                    "parameter_ids": node["parameter_ids"],
                    "unsupported_dependencies": node["unsupported_dependencies"],
                    "findings": node["findings"],
                }
            )
    team_rows = [
        {
            "scenario_id": item["scenario_id"],
            "status": item["status"],
            "qualification": item.get("qualification"),
            "outcome_share": item.get("outcome_share", {}).get("estimate"),
            "terminal_seconds": item.get("terminal_time_seconds", {}).get("estimate"),
            "finding": item.get("finding"),
        }
        for item in result["mixed_team_objectives"]["results"]
    ]
    scenario_rows = list(result["scenario_execution_index"].values())
    return {
        "check-curves.csv": check_rows,
        "fire-range.csv": fire_rows,
        "class-progression.csv": class_rows,
        "specialty-coverage.csv": specialty_rows,
        "specialty-scenarios.csv": result["specialty_coverage"]["scenario_matrix"],
        "specialty-objectives.csv": result["specialty_coverage"][
            "objective_scenario_matrix"
        ],
        "team-objectives.csv": team_rows,
        "scenario-execution.csv": scenario_rows,
    }


def write_baseline_artifacts(output_dir: Path, result: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "baseline": output_dir / "baseline.json",
        "exact": output_dir / "exact-results.json",
        "monte_carlo": output_dir / "monte-carlo-results.json",
        "coverage": output_dir / "specialty-coverage.json",
        "findings": output_dir / "findings.json",
        "parameters": output_dir / "parameter-set.json",
        "parameter_metadata": output_dir / "parameter-metadata.json",
        "provenance": output_dir / "provenance.json",
        "scenarios": output_dir / "scenario-results.json",
        "replay": output_dir / "replays" / "sample-duel.json",
        "team_replay": output_dir / "replays" / "sample-team-control.json",
        "team_combat_replay": output_dir / "replays" / "sample-team-control-combat-postures.json",
        "team_extract_replay": output_dir / "replays" / "sample-team-extract.json",
        "team_breach_replay": output_dir / "replays" / "sample-team-breach.json",
        "team_recon_hack_replay": output_dir / "replays" / "sample-team-recon-hack.json",
    }
    write_json(paths["baseline"], result)
    write_json(paths["exact"], result["exact"])
    write_json(paths["monte_carlo"], {"duel": result["monte_carlo"], "team_control_variants": result["mixed_team_objectives"]["team_control_variants"]})
    write_json(paths["coverage"], result["specialty_coverage"])
    write_json(paths["findings"], result["findings"])
    write_json(paths["parameters"], result["parameters"]["values"])
    write_json(paths["parameter_metadata"], result["parameters"])
    write_json(paths["provenance"], result["provenance"])
    write_json(paths["scenarios"], {"execution_index": result["scenario_execution_index"], "mixed_team_objectives": result["mixed_team_objectives"]["objective_scenarios"]})
    write_json(paths["replay"], result["monte_carlo"]["sample_replay"])
    write_json(paths["team_replay"], result["mixed_team_objectives"]["team_control"]["sample_replay"])
    write_json(paths["team_combat_replay"], result["mixed_team_objectives"]["team_control_variants"]["COMBAT_POSTURES"]["sample_replay"])
    write_json(paths["team_extract_replay"], result["mixed_team_objectives"]["objective_scenarios"]["TEAM-EXTRACT-4V4-V1"]["sample_replay"])
    write_json(paths["team_breach_replay"], result["mixed_team_objectives"]["objective_scenarios"]["TEAM-BREACH-4V4-V1"]["sample_replay"])
    write_json(paths["team_recon_hack_replay"], result["mixed_team_objectives"]["objective_scenarios"]["TEAM-RECON-HACK-4V4-V1"]["sample_replay"])
    for filename, rows in artifact_rows(result).items():
        write_csv(output_dir / filename, rows)
    return {key: str(path) for key, path in paths.items()}
