from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .artifacts import read_json, write_json
from .optimizer import optimize, optimize_objective
from .parameters import ParameterError, ParameterSet, default_manifest
from .probes import all_exact_probes
from .provenance import fingerprint_hash, immutable_sources, protected_fingerprint, provenance_record, repo_root, source_drift
from .regression import compare_results
from .replay import ReplayMismatch, replay_artifact
from .reports import render_html
from .runner import build_baseline, write_baseline_artifacts
from .scenario_runtime import EXACT_RESULT_KEYS, execute_scenario, resolve_scenario_id
from .scenarios import scenario_registry, validate_scenarios
from .specialties import specialty_coverage


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _parameters(path: str | None) -> ParameterSet:
    manifest = default_manifest()
    if not path:
        return manifest.seed_set()
    return manifest.load_set(read_json(_path(path)))


def _summary(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _tuple4(value: str | None, label: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if len(items) != 4:
        raise ValueError(f"{label} must be a comma-separated list of exactly four values")
    return items


def command_validate(args: argparse.Namespace) -> int:
    parameters = _parameters(args.parameters)
    coverage = specialty_coverage(parameters)
    drift = source_drift()
    scenario_errors = validate_scenarios()
    fingerprint = protected_fingerprint()
    objective_scenarios = coverage["objective_scenario_matrix"]
    objective_scenarios_complete = (
        len(objective_scenarios) == 26 * 5 * 3
        and all(
            item["control_metrics"]["terminal_reason"]
            and item["candidate_metrics"]["terminal_reason"]
            and item["control_metrics"]["event_log_hash"] != "NOT_CAPTURED"
            and item["candidate_metrics"]["event_log_hash"] != "NOT_CAPTURED"
            for item in objective_scenarios
        )
    )
    result = {
        "valid": (
            not drift
            and not scenario_errors
            and coverage["specialty_count"] == 26
            and coverage["node_count"] == 364
            and objective_scenarios_complete
        ),
        "balance_status": (
            "STRUCTURAL FAILURE" if coverage["findings"] else "READY"
        ),
        "parameter_count": len(parameters.manifest.specs),
        "parameter_hash": parameters.hash,
        "scenario_count": len(scenario_registry()),
        "scenario_errors": scenario_errors,
        "specialty_count": coverage["specialty_count"],
        "specialty_node_count": coverage["node_count"],
        "coverage_counts": coverage["coverage_counts"],
        "executable_specialty_counterfactuals": len(coverage["counterfactuals"]),
        "specialty_objective_scenario_count": len(objective_scenarios),
        "specialty_objective_scenarios_complete": objective_scenarios_complete,
        "specialty_finding_count": len(coverage["findings"]),
        "rejected_parameter_binding_count": len(
            coverage["rejected_parameter_bindings"]
        ),
        "deferred_parameter_binding_count": len(
            coverage["deferred_parameter_bindings"]
        ),
        "source_drift": drift,
        "protected_file_count": len(fingerprint),
        "protected_fingerprint_hash": fingerprint_hash(fingerprint),
    }
    if args.output:
        write_json(_path(args.output), result)
    print(_summary(result))
    return 0 if result["valid"] else 1


def command_coverage(args: argparse.Namespace) -> int:
    result = specialty_coverage(_parameters(args.parameters))
    if args.specialty:
        selected = result["specialties"].get(args.specialty.upper())
        if selected is None:
            raise ValueError(f"unknown specialty {args.specialty}")
        result = {"specialty": args.specialty.upper(), **selected}
    if args.output:
        write_json(_path(args.output), result)
    print(_summary({key: result[key] for key in result if key in {"specialty_count", "node_count", "coverage_counts", "missing_specialties"}} or result))
    return 0


def command_exact(args: argparse.Namespace) -> int:
    parameters = _parameters(args.parameters)
    result = all_exact_probes(parameters)
    if args.family:
        direct_key = args.family.lower().replace("-", "_")
        if direct_key in result:
            selected_key = direct_key
        else:
            family_matches = {
                EXACT_RESULT_KEYS[definition.scenario_id]
                for definition in scenario_registry().values()
                if definition.family == args.family.upper() and definition.scenario_id in EXACT_RESULT_KEYS
            }
            if len(family_matches) != 1:
                raise ValueError(f"unknown or ambiguous exact family {args.family}; choose a declared scenario family or one of {sorted(result)}")
            selected_key = family_matches.pop()
        result = {selected_key: result[selected_key]}
    if args.output:
        write_json(_path(args.output), {"provenance": provenance_record(parameters.hash), "exact": result})
    print(_summary({"parameter_hash": parameters.hash, "families": sorted(result), "output": args.output}))
    return 0


def command_simulate(args: argparse.Namespace) -> int:
    parameters = _parameters(args.parameters)
    selected = resolve_scenario_id(
        scenario_id=args.scenario or ("MIRROR-DUEL-V1" if not args.family else None),
        family=args.family,
    )
    result = execute_scenario(
        selected,
        parameters,
        master_seed=args.seed,
        runs=args.runs,
        level=args.level,
        class_a=args.class_a.upper(),
        class_b=args.class_b.upper(),
        policy_a=args.policy_a.upper(),
        policy_b=args.policy_b.upper(),
        weapon_a=args.weapon_a,
        weapon_b=args.weapon_b,
        specialty=args.specialty,
        classes_a=_tuple4(args.classes_a, "--classes-a"),
        classes_b=_tuple4(args.classes_b, "--classes-b"),
        policies_a=_tuple4(args.policies_a, "--policies-a"),
        policies_b=_tuple4(args.policies_b, "--policies-b"),
        weapons_a=_tuple4(args.weapons_a, "--weapons-a"),
        weapons_b=_tuple4(args.weapons_b, "--weapons-b"),
        run_index=args.run_index,
    )
    payload = {"provenance": provenance_record(parameters.hash), "scenario_execution": result}
    if args.output:
        write_json(_path(args.output), payload)
    scenario_result = result["result"]
    print(
        _summary(
            {
                "scenario_id": selected,
                "execution_kind": result["execution_kind"],
                "status": scenario_result.get("status", "EXECUTED"),
                "qualification": scenario_result.get("qualification"),
                "outcome_share": scenario_result.get("subject_outcome_share"),
                "output": args.output,
            }
        )
    )
    return 0


def command_baseline(args: argparse.Namespace) -> int:
    parameters = _parameters(args.parameters)
    objective_overrides: dict[str, dict[str, Any]] = {}
    for path in args.objective_result or []:
        payload = read_json(_path(path))
        execution = payload.get("scenario_execution", payload)
        objective = execution.get("result", execution)
        scenario_id = objective.get("scenario_id")
        if not isinstance(scenario_id, str):
            raise ValueError(f"objective result has no scenario_id: {path}")
        if scenario_id in objective_overrides:
            raise ValueError(f"duplicate objective result for {scenario_id}")
        objective_overrides[scenario_id] = objective
    result = build_baseline(
        parameters,
        master_seed=args.seed,
        runs=args.runs,
        progression_runs=args.progression_runs,
        team_runs=args.team_runs,
        objective_overrides=objective_overrides,
        workers=args.workers,
    )
    output_dir = _path(args.output)
    paths = write_baseline_artifacts(output_dir, result)
    render_html(result, output_dir / "report.html")
    print(_summary({"result_id": result["result_id"], "invariants": result["invariants"], "coverage_counts": result["specialty_coverage"]["coverage_counts"], "findings": len(result["findings"]), "artifacts": {**paths, "report": str(output_dir / 'report.html')}}))
    return 0 if all(result["invariants"].values()) else 2


def command_replay(args: argparse.Namespace) -> int:
    artifact = read_json(_path(args.artifact))
    result = replay_artifact(artifact, default_manifest())
    if args.output:
        write_json(_path(args.output), result)
    print(_summary({"replay_match": result["replay_match"], "comparisons": result["comparisons"], "output": args.output}))
    return 0


def command_optimize(args: argparse.Namespace) -> int:
    parameters = _parameters(args.parameters)
    result = (
        optimize_objective(
            parameters,
            args.objective_scenario,
            args.parameter or [],
            max_candidates=args.max_candidates,
            master_seed=args.seed,
            pairs=args.pairs,
        )
        if args.objective_scenario
        else optimize(parameters, args.parameter or [], args.max_candidates)
    )
    payload = {"provenance": provenance_record(parameters.hash), "optimization": result}
    if args.output:
        write_json(_path(args.output), payload)
    if args.parameter_output and result.get("best_parameter_values") is not None:
        write_json(_path(args.parameter_output), result["best_parameter_values"])
    best = result.get("best") or {}
    print(
        _summary(
            {
                "status": result["status"],
                "scenario_id": result.get("scenario_id"),
                "qualification": result.get("qualification"),
                "best": {
                    key: best.get(key)
                    for key in (
                        "candidate_index",
                        "updates",
                        "parameter_hash",
                        "objective",
                        "objective_outcome_share",
                        "normalized_seed_movement",
                    )
                    if key in best
                },
                "findings": result.get("findings", []),
                "output": args.output,
                "parameter_output": args.parameter_output,
            }
        )
    )
    return 0 if result["status"] == "CANDIDATE" else 3


def command_regress(args: argparse.Namespace) -> int:
    result = compare_results(read_json(_path(args.accepted)), read_json(_path(args.current)))
    if args.output:
        write_json(_path(args.output), result)
    print(_summary({"shared_numeric_metrics": result["shared_numeric_metrics"], "changed_metric_count": len(result["changed_metrics"]), "output": args.output}))
    return 0


def command_report(args: argparse.Namespace) -> int:
    result = read_json(_path(args.input))
    output = _path(args.output)
    render_html(result, output)
    print(_summary({"report": str(output), "source": str(_path(args.input))}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trigger-balance", description="Deterministic Trigger Discipline balance and structural test suite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def parameter_option(command: argparse.ArgumentParser) -> None:
        command.add_argument("--parameters", help="complete validated parameter-set JSON; defaults to v0.1 seeds")

    validate = subparsers.add_parser("validate", help="validate provenance, manifest, scenarios, source drift, and coverage accounting")
    parameter_option(validate); validate.add_argument("--output"); validate.set_defaults(func=command_validate)
    coverage = subparsers.add_parser("coverage", help="report all specialty node coverage and formal gaps")
    parameter_option(coverage); coverage.add_argument("--specialty"); coverage.add_argument("--output"); coverage.set_defaults(func=command_coverage)
    exact = subparsers.add_parser("exact", help="run exact finite probability and deterministic rule probes")
    parameter_option(exact); exact.add_argument("--family"); exact.add_argument("--output"); exact.set_defaults(func=command_exact)
    simulate = subparsers.add_parser("simulate", help="execute any declared exact, structural, duel, or tactical scenario")
    parameter_option(simulate); simulate.add_argument("--scenario"); simulate.add_argument("--family"); simulate.add_argument("--runs", type=int, default=1000); simulate.add_argument("--run-index", type=int); simulate.add_argument("--seed", type=int, default=20260824); simulate.add_argument("--level", type=int, choices=[1,5,10,15,20], default=10); simulate.add_argument("--class-a", default="RIFLEMAN"); simulate.add_argument("--class-b", default="RIFLEMAN"); simulate.add_argument("--policy-a", default="POLICY_FIRE_DISCIPLINE"); simulate.add_argument("--policy-b", default="POLICY_FIRE_DISCIPLINE"); simulate.add_argument("--weapon-a", default="service_rifle"); simulate.add_argument("--weapon-b", default="service_rifle"); simulate.add_argument("--specialty"); simulate.add_argument("--classes-a", help="team scenario: four comma-separated class IDs"); simulate.add_argument("--classes-b", help="team scenario: four comma-separated class IDs"); simulate.add_argument("--policies-a", help="team scenario: four comma-separated policy IDs"); simulate.add_argument("--policies-b", help="team scenario: four comma-separated policy IDs"); simulate.add_argument("--weapons-a", help="team scenario: four comma-separated weapon IDs"); simulate.add_argument("--weapons-b", help="team scenario: four comma-separated weapon IDs"); simulate.add_argument("--output"); simulate.set_defaults(func=command_simulate)
    baseline = subparsers.add_parser("baseline", help="evaluate v0.1 without optimization and generate all artifacts/report")
    parameter_option(baseline); baseline.add_argument("--runs", type=int, default=10000); baseline.add_argument("--progression-runs", type=int); baseline.add_argument("--team-runs", type=int, help="paired/mirrored TEAM-CONTROL and non-overridden objective runs; defaults to --runs"); baseline.add_argument("--objective-result", action="append", help="reuse a validated objective simulation artifact with the same seed and parameter hash"); baseline.add_argument("--workers", type=int, default=1, help="independent deterministic simulation worker processes"); baseline.add_argument("--seed", type=int, default=20260824); baseline.add_argument("--output", default="balance-results/v0.1"); baseline.set_defaults(func=command_baseline)
    replay = subparsers.add_parser("replay", help="replay a recorded run and verify semantic hashes")
    replay.add_argument("--artifact", required=True); replay.add_argument("--output"); replay.set_defaults(func=command_replay)
    optimize_parser = subparsers.add_parser("optimize", help="manifest-only transparent constrained grid search")
    parameter_option(optimize_parser); optimize_parser.add_argument("--parameter", action="append"); optimize_parser.add_argument("--objective-scenario", choices=("TEAM-EXTRACT-4V4-V1", "TEAM-BREACH-4V4-V1", "TEAM-RECON-HACK-4V4-V1"), help="evaluate candidate values against a mixed-team objective automaticity review"); optimize_parser.add_argument("--pairs", type=int, default=100, help="paired/mirrored objective runs per candidate"); optimize_parser.add_argument("--seed", type=int, default=20260824); optimize_parser.add_argument("--max-candidates", type=int, default=1000); optimize_parser.add_argument("--output"); optimize_parser.add_argument("--parameter-output", help="write the complete best candidate parameter set for reuse as --parameters"); optimize_parser.set_defaults(func=command_optimize)
    regress = subparsers.add_parser("regress", help="compare current results to a named accepted baseline")
    regress.add_argument("--accepted", required=True); regress.add_argument("--current", required=True); regress.add_argument("--output"); regress.set_defaults(func=command_regress)
    report = subparsers.add_parser("report", help="regenerate HTML from stored raw baseline JSON without simulation")
    report.add_argument("--input", required=True); report.add_argument("--output", required=True); report.set_defaults(func=command_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = repo_root()
    try:
        with immutable_sources(root):
            return int(args.func(args))
    except (ParameterError, ValueError, ReplayMismatch, RuntimeError) as error:
        print(json.dumps({"error": type(error).__name__, "message": str(error)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
