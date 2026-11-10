from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from .fixtures import benchmark_actor, build_map
from .model import KnowledgeState, Position, Side
from .parameters import ParameterSet
from .probes import all_exact_probes
from .rng import derive_seed
from .scenarios import ScenarioDefinition, scenario_registry
from .simulation import DuelSpec, paired_mirrored_duels
from .specialties import specialty_coverage
from .tactical import TeamScenarioSpec, default_team_spec, paired_mirrored_team_scenarios, unsupported_mixed_team_findings


EXACT_RESULT_KEYS: dict[str, str] = {
    "CHECK-CURVE-V1": "check_curves",
    "FIRE-SINGLE-RANGE-V1": "fire_range",
    "FIRE-HANDLING-TIGHT-V1": "handling",
    "FIRE-AUTO-STRING-V1": "auto_strings",
    "PROTECTION-ARMOR-V1": "armor",
    "PROJECTILE-MATERIAL-V1": "materials",
    "COVER-PARTIAL-V1": "cover",
    "COVER-DESTRUCTION-V1": "cover",
    "SUPPRESS-CROSSING-V1": "suppression",
    "OW-CROSSING-V1": "overwatch",
    "TRIGGER-SIMULTANEOUS-V1": "triggers",
    "CQC-MANEUVER-V1": "cqc",
    "CQC-CONTACT-FIRE-V1": "handling",
    "GRENADE-PLACEMENT-V1": "explosives",
    "GRENADE-WARNING-V1": "explosives",
    "BLAST-COVER-V1": "explosives",
    "SMOKE-LOS-V1": "explosives",
    "MED-STABILIZE-V1": "medical",
    "MED-RESUSCITATE-V1": "medical",
    "STEALTH-INFO-V1": "knowledge",
    "DRONE-RECON-V1": "drones",
    "DRONE-ARMED-V1": "drones",
    "TECH-DEPLOY-V1": "drones",
    "HACK-LOCAL-V1": "hacking",
    "HACK-NETWORK-V1": "hacking",
    "SOCIAL-NO-LEVERAGE-V1": "social",
    "SOCIAL-LEVERAGE-V1": "social",
    "SOCIAL-IMPOSSIBLE-V1": "social",
    "CLASS-BASELINES-V1": "classes",
}


def _semantic_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_scenario_id(*, scenario_id: str | None = None, family: str | None = None) -> str:
    registry = scenario_registry()
    if scenario_id:
        normalized = scenario_id.upper()
        if normalized not in registry:
            raise ValueError(f"unknown scenario {scenario_id}")
        if family and registry[normalized].family != family.upper():
            raise ValueError(f"scenario {normalized} is not in family {family.upper()}")
        return normalized
    if not family:
        raise ValueError("scenario ID or family is required")
    matches = [item.scenario_id for item in registry.values() if item.family == family.upper()]
    if not matches:
        raise ValueError(f"unknown scenario family {family}")
    if len(matches) != 1:
        raise ValueError(f"scenario family {family} is ambiguous: {sorted(matches)}")
    return matches[0]


def _scenario_record(
    definition: ScenarioDefinition,
    parameters: ParameterSet,
    *,
    master_seed: int,
    runs: int,
    level: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    grid = build_map(definition.map_id, parameters)
    def unoccupied_position(*, reverse: bool) -> Position:
        xs = range(grid.width - 1, -1, -1) if reverse else range(grid.width)
        preferred_y = grid.height // 2
        ys = sorted(range(grid.height), key=lambda value: (abs(value - preferred_y), value))
        for x in xs:
            for y in ys:
                if not grid.occupied((x, y)):
                    return Position(x, y)
        raise ValueError(f"map fixture {grid.map_id} has no unoccupied actor cell")

    actor_a = benchmark_actor(
        str(filters.get("class_a", "RIFLEMAN")),
        level,
        Side.A,
        unoccupied_position(reverse=False),
        parameters,
        weapon_id=str(filters.get("weapon_a", "service_rifle")),
    )
    actor_b = benchmark_actor(
        str(filters.get("class_b", "RIFLEMAN")),
        level,
        Side.B,
        unoccupied_position(reverse=True),
        parameters,
        weapon_id=str(filters.get("weapon_b", "service_rifle")),
    )
    specialty_name = filters.get("specialty")
    actor_a_build_id = f"{actor_a.fixture_id}:BASELINE-V1"
    if specialty_name:
        specialty_data = specialty_coverage(parameters)
        try:
            build = specialty_data["designated_builds"][str(specialty_name)][
                str(level)
            ]
        except KeyError as error:
            raise ValueError(
                f"unknown specialty build {specialty_name} at level {level}"
            ) from error
        if build["class_id"] != actor_a.class_id:
            raise ValueError(
                f"specialty {specialty_name} belongs to {build['class_id']}, "
                f"not {actor_a.class_id}"
            )
        actor_a.specialty_nodes.update(build["node_ids"])
        actor_a_build_id = str(build["build_id"])
    actor_b_build_id = f"{actor_b.fixture_id}:BASELINE-V1"
    actor_a.target_knowledge[actor_b.actor_id] = KnowledgeState.CONFIRMED
    actor_b.target_knowledge[actor_a.actor_id] = KnowledgeState.CONFIRMED

    def actor_fixture(
        actor: Any,
        policy_id: str,
        build_id: str,
    ) -> dict[str, Any]:
        weapon = actor.weapon
        return {
            "fixture_id": actor.fixture_id,
            "build_id": build_id,
            "side": actor.side.value,
            "level": actor.level,
            "class_id": actor.class_id,
            "specialty_nodes": sorted(actor.specialty_nodes),
            "skill": actor.skill,
            "aptitude": actor.aptitude,
            "hp": {"current": actor.hp, "maximum": actor.max_hp},
            "res": {"current": actor.res, "maximum": actor.max_res},
            "ap": {"current": actor.ap, "maximum": actor.max_ap},
            "equipment": {
                "weapon_id": weapon.weapon_id if weapon else None,
                "ammunition": weapon.total_rounds() if weapon else 0,
                "chamber": weapon.chamber if weapon else None,
                "current_recoil": weapon.current_recoil if weapon else 0,
            },
            "posture": actor.stance.value,
            "position": asdict(actor.position),
            "target_knowledge": {
                target_id: knowledge.value
                for target_id, knowledge in sorted(actor.target_knowledge.items())
            },
            "policy_id": policy_id,
        }
    map_geometry = {
        "map_id": grid.map_id,
        "width": grid.width,
        "height": grid.height,
        "full_blockers": sorted(grid.full_blockers),
        "covers": {
            cover_id: {
                "cells": sorted(cover.cells),
                "height_m": cover.height_m,
                "material_resistance": cover.material_resistance,
                "cover_hp": cover.cover_hp,
                "facing_degrees": cover.facing_degrees,
            }
            for cover_id, cover in sorted(grid.covers.items())
        },
    }
    return {
        "scenario_id": definition.scenario_id,
        "scenario_version": definition.version,
        "family": definition.family,
        "map_id": definition.map_id,
        "fixture_ids": sorted(definition.fixture_ids),
        "level": level,
        "filters": {key: value for key, value in filters.items() if value is not None},
        "parameter_set_hash": parameters.hash,
        "master_seed": master_seed,
        "paired_mirrored_runs": runs,
        "exact_enumeration": definition.executor not in {"duel_monte_carlo", "team_objective"},
        "input_state": {
            "map_fixture_id": definition.map_id,
            "geometry_fixture_id": definition.fixture_ids[0],
            "map_geometry": map_geometry,
            "actors": {
                actor_a.actor_id: actor_fixture(
                    actor_a,
                    str(filters.get("policy_a", "POLICY_FIRE_DISCIPLINE")),
                    actor_a_build_id,
                ),
                actor_b.actor_id: actor_fixture(
                    actor_b,
                    str(filters.get("policy_b", "POLICY_FIRE_DISCIPLINE")),
                    actor_b_build_id,
                ),
            },
            "serialized_from": "scenario_definition_and_benchmark_fixtures",
            "environmental_state": f"{definition.scenario_id}:STATE",
            "team_turn": {
                "active_side": "A",
                "initiative_model": "TEAM_TURN",
                "elapsed_seconds": 0,
            },
        },
        "terminal_conditions": list(definition.terminal_conditions),
        "support_status": definition.support_status,
        "unsupported_dependencies": list(definition.unsupported_dependencies),
    }


def _exact_case_records(
    record: dict[str, Any],
    measurements: dict[str, Any],
) -> list[dict[str, Any]]:
    if record["scenario_id"] == "SPECIALTY-COUNTERFACTUAL-V1":
        matrix = measurements.get("scenario_matrix", [])
        return [
            {
                "case_id": str(item["scenario_id"]),
                "fixture_id": item["scenario_fixture"]["fixture_id"],
                "build_id": (
                    f"{item['scenario_fixture']['actor']['character_id']}:"
                    f"{_semantic_hash(item['scenario_fixture']['actor']['specialty_nodes'])[:12]}"
                ),
                "actor": deepcopy(item["scenario_fixture"]["actor"]),
                "opposition": deepcopy(item["scenario_fixture"]["opposition"]),
                "input_state_locator": (
                    f"result.measurements.scenario_matrix[{index}].scenario_fixture"
                ),
                "result_locator": (
                    f"result.measurements.scenario_matrix[{index}].scenario_execution"
                ),
            }
            for index, item in enumerate(matrix)
        ]

    cases = measurements.get("case_records")
    if not isinstance(cases, list) or not cases:
        raise ValueError(
            f"exact probe for {record['scenario_id']} did not publish concrete case inputs"
        )
    published = deepcopy(cases)
    for case in published:
        case.pop("result_collection", None)
        case.pop("filter_by_level", None)
        case.pop("filter_by_class", None)
    return published


def _execution_output(
    record: dict[str, Any],
    execution_kind: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    sample = result.get("sample_replay")
    if isinstance(sample, dict):
        record["fixture_ids"] = sorted(
            set(record["fixture_ids"]) | set(sample.get("fixture_ids", []))
        )
        record["replay_locator"] = "result.sample_replay"
        record["raw_event_log_locator"] = (
            "result.sample_replay.events" if "events" in sample else None
        )
        record["sample_event_log_hash"] = sample.get("event_log_hash")
        record["sample_final_state_hash"] = sample.get("final_state_hash")
        record["derived_seed_provenance"] = sample.get("rng") or {
            "master_seed": record["master_seed"],
            "mode": "EXACT_ENUMERATION",
        }
        if isinstance(sample.get("initial_state"), dict):
            record["input_state"] = {
                "map_fixture_id": record["map_id"],
                "geometry_fixture_id": record["input_state"][
                    "geometry_fixture_id"
                ],
                "map_geometry": deepcopy(
                    sample.get(
                        "map_geometry",
                        record["input_state"]["map_geometry"],
                    )
                ),
                "scenario_state": deepcopy(sample["initial_state"]),
                "serialized_from": "result.sample_replay.initial_state",
            }
        else:
            record["input_state"]["probe_state_locator"] = (
                "result.measurements"
            )
            record["input_state"]["probe_result_hash"] = result.get(
                "result_hash"
            )
    else:
        record["replay_locator"] = "result.sample_replay.replay_spec"
        record["raw_event_log_locator"] = None
        record["derived_seed_provenance"] = {
            "master_seed": record["master_seed"],
            "mode": "EXACT_ENUMERATION",
        }
    record["raw_metrics_locator"] = "result.measurements"
    return {
        "scenario_record": record,
        "execution_kind": execution_kind,
        "result": result,
    }


def _filter_rows(value: Any, *, level: int, class_id: str | None) -> Any:
    if not isinstance(value, dict):
        return value
    filtered = dict(value)
    rows = value.get("rows")
    if isinstance(rows, list):
        selected = []
        for row in rows:
            if not isinstance(row, dict):
                selected.append(row)
                continue
            if "level" in row and row["level"] != level:
                continue
            if class_id and "class_id" in row and row["class_id"] != class_id:
                continue
            selected.append(row)
        filtered["rows"] = selected
    case_groups = value.get("scenario_case_records")
    if isinstance(case_groups, dict):
        filtered["scenario_case_records"] = {
            scenario_id: [
                deepcopy(record)
                for record in records
                if (
                    not record.get("filter_by_level")
                    or record.get("level") == level
                )
                and (
                    not record.get("filter_by_class")
                    or not class_id
                    or record.get("class_id") == class_id
                )
            ]
            for scenario_id, records in case_groups.items()
        }
    return filtered


def _partial_filter(scenario_id: str, result: dict[str, Any]) -> dict[str, Any]:
    filtered = dict(result)
    rows = result.get("rows")
    if not isinstance(rows, list):
        if scenario_id == "COVER-PARTIAL-V1":
            filtered = {
                "probe": result["probe"],
                "exact": result["exact"],
                "routes": result["routes"],
                "exposure_offsets": result["exposure_offsets"],
                "specialty_exposure_reduction": result[
                    "specialty_exposure_reduction"
                ],
                "findings": result["findings"],
            }
        elif scenario_id == "COVER-DESTRUCTION-V1":
            filtered = {
                "probe": result["probe"],
                "exact": result["exact"],
                "cover_destruction_matrix": result[
                    "cover_destruction_matrix"
                ],
                "findings": result["findings"],
            }
        elif scenario_id == "GRENADE-PLACEMENT-V1":
            filtered = {"probe": result["probe"], "placement": result["placement"]}
        elif scenario_id == "GRENADE-WARNING-V1":
            filtered = {"probe": result["probe"], "warning": result["warning"]}
        elif scenario_id == "BLAST-COVER-V1":
            filtered = {"probe": result["probe"], "blast_snapshot": result["blast_snapshot"]}
        elif scenario_id == "SMOKE-LOS-V1":
            filtered = {"probe": result["probe"], "smoke": result["smoke"]}
    elif scenario_id == "DRONE-ARMED-V1":
        filtered = {
            "probe": result["probe"],
            "exact": result["exact"],
            "armed_combat": result["armed_combat"],
        }
    elif scenario_id == "DRONE-RECON-V1":
        filtered["rows"] = [row for row in rows if "drone" in row]
    elif scenario_id == "TECH-DEPLOY-V1":
        filtered["rows"] = [row for row in rows if "device" in row]
    elif scenario_id == "MED-STABILIZE-V1":
        filtered["rows"] = [row for row in rows if row.get("action") == "STABILIZE"]
    elif scenario_id == "MED-RESUSCITATE-V1":
        filtered["rows"] = [row for row in rows if row.get("action") == "RESUSCITATION"]
    elif scenario_id == "SOCIAL-NO-LEVERAGE-V1":
        filtered["rows"] = [row for row in rows if row.get("plausible") and row.get("leverage") is None]
    elif scenario_id == "SOCIAL-LEVERAGE-V1":
        filtered["rows"] = [row for row in rows if row.get("plausible") and row.get("leverage") is not None]
    elif scenario_id == "SOCIAL-IMPOSSIBLE-V1":
        filtered["rows"] = [row for row in rows if not row.get("plausible")]
    case_groups = result.get("scenario_case_records", {})
    records = deepcopy(case_groups.get(scenario_id, []))
    collection_counts: dict[str, int] = {}
    for record in records:
        collection = str(record["result_collection"])
        index = collection_counts.get(collection, 0)
        collection_counts[collection] = index + 1
        if collection == "singleton":
            record["result_locator"] = "result.measurements"
        else:
            record["result_locator"] = (
                f"result.measurements.{collection}[{index}]"
            )
    filtered["case_records"] = records
    return filtered


def _missing_result(definition: ScenarioDefinition, partial: dict[str, Any] | None = None) -> dict[str, Any]:
    findings = [
        {
            "code": "UNIMPLEMENTABLE SPEC GAP",
            "severity": "REVIEW",
            "scenario_id": definition.scenario_id,
            "evidence": dependency,
        }
        for dependency in definition.unsupported_dependencies
    ]
    return {
        "scenario_id": definition.scenario_id,
        "status": "MISSING_DEPENDENCY",
        "qualification": "STRUCTURAL_FAILURE",
        "partial_measurements": partial,
        "findings": findings,
    }


def execute_scenario(
    scenario_id: str,
    parameters: ParameterSet,
    *,
    master_seed: int,
    runs: int,
    level: int,
    class_a: str = "RIFLEMAN",
    class_b: str = "RIFLEMAN",
    policy_a: str = "POLICY_FIRE_DISCIPLINE",
    policy_b: str = "POLICY_FIRE_DISCIPLINE",
    weapon_a: str = "service_rifle",
    weapon_b: str = "service_rifle",
    specialty: str | None = None,
    classes_a: tuple[str, ...] | None = None,
    classes_b: tuple[str, ...] | None = None,
    policies_a: tuple[str, ...] | None = None,
    policies_b: tuple[str, ...] | None = None,
    weapons_a: tuple[str, ...] | None = None,
    weapons_b: tuple[str, ...] | None = None,
    run_index: int | None = None,
) -> dict[str, Any]:
    if runs <= 0:
        raise ValueError("runs must be positive")
    if run_index is not None and run_index < 0:
        raise ValueError("run index must be non-negative")
    definition = scenario_registry().get(scenario_id.upper())
    if definition is None:
        raise ValueError(f"unknown scenario {scenario_id}")
    scenario_id = definition.scenario_id
    effective_seed = derive_seed(master_seed, f"{scenario_id}:run", run_index) if run_index is not None else master_seed
    effective_runs = 1 if run_index is not None else runs
    filters: dict[str, Any] = {
        "class_a": class_a.upper(),
        "class_b": class_b.upper(),
        "policy_a": policy_a.upper(),
        "policy_b": policy_b.upper(),
        "weapon_a": weapon_a,
        "weapon_b": weapon_b,
        "specialty": specialty.upper() if specialty else None,
        "run_index": run_index,
    }
    team_spec: TeamScenarioSpec | None = None
    if scenario_id.startswith("TEAM-"):
        defaults = default_team_spec(scenario_id, level)
        team_spec = TeamScenarioSpec(
            scenario_id=scenario_id,
            level=level,
            classes_a=classes_a or defaults.classes_a,
            classes_b=classes_b or defaults.classes_b,
            policies_a=policies_a or defaults.policies_a,
            policies_b=policies_b or defaults.policies_b,
            weapons_a=weapons_a or defaults.weapons_a,
            weapons_b=weapons_b or defaults.weapons_b,
        )
        filters.update(
            {
                "classes_a": list(team_spec.classes_a),
                "classes_b": list(team_spec.classes_b),
                "policies_a": list(team_spec.policies_a),
                "policies_b": list(team_spec.policies_b),
                "weapons_a": list(team_spec.weapons_a),
                "weapons_b": list(team_spec.weapons_b),
            }
        )
    record = _scenario_record(
        definition,
        parameters,
        master_seed=effective_seed,
        runs=effective_runs,
        level=level,
        filters=filters,
    )

    if scenario_id == "MIRROR-DUEL-V1":
        result = paired_mirrored_duels(
            DuelSpec(
                level=level,
                class_a=class_a.upper(),
                class_b=class_b.upper(),
                policy_a=policy_a.upper(),
                policy_b=policy_b.upper(),
                weapon_a=weapon_a,
                weapon_b=weapon_b,
            ),
            parameters,
            effective_seed,
            effective_runs,
        )
        return _execution_output(record, "MONTE_CARLO", result)

    if scenario_id.startswith("TEAM-"):
        assert team_spec is not None
        result = paired_mirrored_team_scenarios(team_spec, parameters, effective_seed, effective_runs)
        return _execution_output(record, "MONTE_CARLO", result)

    if scenario_id == "SPECIALTY-COUNTERFACTUAL-V1":
        coverage = specialty_coverage(parameters)
        if specialty:
            selected = coverage["specialties"].get(specialty.upper())
            if selected is None:
                raise ValueError(f"unknown specialty {specialty}")
            measurements: dict[str, Any] = {
                "specialty": specialty.upper(),
                **selected,
                "counterfactuals": [item for item in coverage["counterfactuals"] if item["specialty"] == specialty.upper()],
                "scenario_matrix": [item for item in coverage["scenario_matrix"] if item["specialty"] == specialty.upper()],
            }
        else:
            measurements = coverage
        result = {"scenario_id": scenario_id, "status": "IMPLEMENTED", "qualification": "EXACT", "measurements": measurements, "findings": measurements.get("findings", [])}
        result_hash = _semantic_hash(result)
        result["result_hash"] = result_hash
        result["sample_replay"] = {
            "result_hash": result_hash,
            "replay_spec": {
                "kind": "scenario",
                "scenario_id": scenario_id,
                "parameter_values": parameters.values,
                "master_seed": effective_seed,
                "runs": effective_runs,
                "level": level,
                "filters": filters,
            },
        }
        requested_actor_selection = deepcopy(record["input_state"]["actors"])
        exact_cases = _exact_case_records(record, measurements)
        record["input_state"] = {
            "map_fixture_id": record["map_id"],
            "geometry_fixture_id": record["input_state"]["geometry_fixture_id"],
            "map_geometry": record["input_state"]["map_geometry"],
            "exact_case_count": len(exact_cases),
            "exact_case_records": exact_cases,
            "requested_actor_selection": requested_actor_selection,
            "serialized_from": "result.measurements.scenario_fixture_records",
        }
        return _execution_output(record, "STRUCTURAL_COUNTERFACTUAL", result)

    key = EXACT_RESULT_KEYS.get(scenario_id)
    if key is None:
        raise RuntimeError(f"scenario {scenario_id} has no registered executor")
    measurements = _partial_filter(scenario_id, _filter_rows(all_exact_probes(parameters)[key], level=level, class_id=class_a.upper()))
    if definition.support_status == "MISSING_DEPENDENCY":
        result = _missing_result(definition, measurements)
    else:
        result = {
            "scenario_id": scenario_id,
            "status": definition.support_status,
            "qualification": "EXACT",
            "measurements": measurements,
            "findings": measurements.get("findings", []),
        }
    result_hash = _semantic_hash(result)
    result["result_hash"] = result_hash
    result["sample_replay"] = {
        "result_hash": result_hash,
        "replay_spec": {
            "kind": "scenario",
            "scenario_id": scenario_id,
            "parameter_values": parameters.values,
            "master_seed": effective_seed,
            "runs": effective_runs,
            "level": level,
            "filters": filters,
        },
    }
    requested_actor_selection = deepcopy(record["input_state"]["actors"])
    exact_cases = _exact_case_records(record, measurements)
    record["input_state"] = {
        "map_fixture_id": record["map_id"],
        "geometry_fixture_id": record["input_state"]["geometry_fixture_id"],
        "map_geometry": record["input_state"]["map_geometry"],
        "exact_case_count": len(exact_cases),
        "exact_case_records": exact_cases,
        "requested_actor_selection": requested_actor_selection,
        "serialized_from": "result.measurements.exact_case_records",
    }
    return _execution_output(record, "EXACT", result)


def replay_exact_scenario(replay_spec: dict[str, Any], parameters: ParameterSet) -> dict[str, Any]:
    filters = replay_spec.get("filters", {})
    return execute_scenario(
        replay_spec["scenario_id"],
        parameters,
        master_seed=int(replay_spec["master_seed"]),
        runs=int(replay_spec["runs"]),
        level=int(replay_spec["level"]),
        class_a=filters.get("class_a", "RIFLEMAN"),
        class_b=filters.get("class_b", "RIFLEMAN"),
        policy_a=filters.get("policy_a", "POLICY_FIRE_DISCIPLINE"),
        policy_b=filters.get("policy_b", "POLICY_FIRE_DISCIPLINE"),
        weapon_a=filters.get("weapon_a", "service_rifle"),
        weapon_b=filters.get("weapon_b", "service_rifle"),
        specialty=filters.get("specialty"),
        classes_a=tuple(filters["classes_a"]) if filters.get("classes_a") else None,
        classes_b=tuple(filters["classes_b"]) if filters.get("classes_b") else None,
        policies_a=tuple(filters["policies_a"]) if filters.get("policies_a") else None,
        policies_b=tuple(filters["policies_b"]) if filters.get("policies_b") else None,
        weapons_a=tuple(filters["weapons_a"]) if filters.get("weapons_a") else None,
        weapons_b=tuple(filters["weapons_b"]) if filters.get("weapons_b") else None,
        run_index=None,
    )
