from __future__ import annotations

from dataclasses import fields
from typing import Any

from .parameters import ParameterManifest
from .simulation import DuelSpec, run_duel
from .model import Side
from .tactical import TeamScenarioSpec, run_team_scenario


class ReplayMismatch(RuntimeError):
    pass


def replay_artifact(artifact: dict[str, Any], manifest: ParameterManifest) -> dict[str, Any]:
    container = artifact.get("scenario_execution") or artifact.get("simulation") or artifact
    result_container = container.get("result", container) if isinstance(container, dict) else container
    replay_spec = (
        artifact.get("replay_spec")
        or artifact.get("sample_replay", {}).get("replay_spec")
        or (result_container.get("replay_spec") if isinstance(result_container, dict) else None)
        or (result_container.get("sample_replay", {}).get("replay_spec") if isinstance(result_container, dict) else None)
    )
    if not replay_spec:
        raise ValueError("artifact has no replay_spec")
    parameters = manifest.load_set(replay_spec["parameter_values"])
    kind = replay_spec.get("kind")
    if kind == "duel":
        allowed = {field.name for field in fields(DuelSpec)}
        unknown = set(replay_spec["spec"]) - allowed
        if unknown:
            raise ValueError(f"unknown duel spec fields: {sorted(unknown)}")
        replayed = run_duel(DuelSpec(**replay_spec["spec"]), parameters, int(replay_spec["seed"]), capture_events=True)
    elif kind == "team":
        allowed = {field.name for field in fields(TeamScenarioSpec)}
        unknown = set(replay_spec["spec"]) - allowed
        if unknown:
            raise ValueError(f"unknown team spec fields: {sorted(unknown)}")
        spec_values = dict(replay_spec["spec"])
        spec_values["opening_side"] = Side(spec_values["opening_side"])
        for key in (
            "classes_a",
            "classes_b",
            "policies_a",
            "policies_b",
            "weapons_a",
            "weapons_b",
            "initial_magazine_rounds_a",
            "initial_magazine_rounds_b",
        ):
            if key in spec_values:
                spec_values[key] = tuple(spec_values[key])
        for key in ("specialty_nodes_a", "specialty_nodes_b"):
            spec_values[key] = tuple(tuple(nodes) for nodes in spec_values[key])
        replayed = run_team_scenario(TeamScenarioSpec(**spec_values), parameters, int(replay_spec["seed"]), capture_events=True)
    elif kind == "scenario":
        # Imported lazily to avoid making the stochastic runtime depend on the
        # replay adapter during normal execution.
        from .scenario_runtime import replay_exact_scenario

        execution = replay_exact_scenario(replay_spec, parameters)
        replayed = execution["result"]
    else:
        raise ValueError(f"unsupported replay kind {kind}")
    expected = (
        artifact.get("sample_replay")
        or (result_container.get("sample_replay") if isinstance(result_container, dict) else None)
        or result_container
    )
    if kind == "scenario":
        comparisons = {
            "result_hash": (expected.get("result_hash"), replayed["result_hash"]),
        }
    else:
        comparisons = {
            "event_log_hash": (expected.get("event_log_hash"), replayed["event_log_hash"]),
            "final_state_hash": (expected.get("final_state_hash"), replayed["final_state_hash"]),
            "winner_side": (expected.get("winner_side"), replayed["winner_side"]),
            "event_count": (expected.get("event_count"), replayed["event_count"]),
        }
    mismatches = {key: {"expected": left, "actual": right} for key, (left, right) in comparisons.items() if left != right}
    result = {
        "replay_match": not mismatches,
        "comparisons": {key: {"expected": left, "actual": right} for key, (left, right) in comparisons.items()},
        "mismatches": mismatches,
        "replayed": replayed,
    }
    if mismatches:
        raise ReplayMismatch(str(mismatches))
    return result
