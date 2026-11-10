from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
import math
from typing import Any, Iterable

from .engine import EventEngine, TriggerEffect, movement_with_triggers
from .fixtures import benchmark_actor, build_map
from .geometry import GridMap, actual_difficulty, shortest_path, trace_cells
from .model import HitQuality, KnowledgeState, Position, PreparedAction, Side, SimulationState, Stance, SuppressionZone
from .parameters import ParameterSet
from .policies import KnownTarget, PolicyDecision, PolicyObservation, choose_action
from .rng import derive_seed
from .rules import (
    apply_damage,
    attack_values_for_weapon,
    cycle_weapon,
    fire_action,
    movement_rate,
    recover_recoil,
    reload_magazine,
    resolve_attack,
    resolve_cqc_maneuver,
)
from .specialty_runtime import (
    ANCHOR_ESTABLISH_STRONGPOINT,
    ARSENAL_FIGHTING_LOAD,
    EVADER_BREAK_CONTACT,
    FLANKER_ANGLE_ADVANTAGE,
    GUNNER_SUSTAINED_FIRE_DISCIPLINE,
    GUARDIAN_INTERPOSE,
    INFILTRATOR_APPROACH_PLAN,
    OPERATOR_WEAPON_MANIPULATION,
    SENTINEL_PREPARED_SIGHTLINE,
    SHIELD_BEARER_SHIELD_TRAINING,
    SNAPSHOT_QUICK_SOLUTION,
    SNIPER_DEEP_AIM,
    has_node,
    record_effect,
)
from .statistics import mean_interval, wilson_interval


CONTROL_CLASSES = ("BULWARK", "RIFLEMAN", "GHOST", "CORPSMAN")
CONTROL_POLICIES = ("POLICY_OBJECTIVE", "POLICY_FIRE_DISCIPLINE", "POLICY_FLANK", "POLICY_SUPPORT")
CONTROL_WEAPONS = ("service_rifle", "service_rifle", "smg", "pistol")
_ARC_CACHE: dict[tuple[object, ...], frozenset[tuple[int, int]]] = {}
_ARC_CACHE_LIMIT = 20000


@dataclass(frozen=True, slots=True)
class TeamScenarioSpec:
    scenario_id: str = "TEAM-CONTROL-4V4-V1"
    scenario_version: str = "1.0.0"
    level: int = 10
    classes_a: tuple[str, ...] = CONTROL_CLASSES
    classes_b: tuple[str, ...] = CONTROL_CLASSES
    policies_a: tuple[str, ...] = CONTROL_POLICIES
    policies_b: tuple[str, ...] = CONTROL_POLICIES
    weapons_a: tuple[str, ...] = CONTROL_WEAPONS
    weapons_b: tuple[str, ...] = CONTROL_WEAPONS
    opening_side: Side = Side.A
    max_team_turns: int = 24
    specialty_nodes_a: tuple[tuple[str, ...], ...] = ((), (), (), ())
    specialty_nodes_b: tuple[tuple[str, ...], ...] = ((), (), (), ())
    initial_magazine_rounds_a: tuple[int | None, ...] = (None, None, None, None)
    initial_magazine_rounds_b: tuple[int | None, ...] = (None, None, None, None)
    objective_fixture_id: str = "STANDARD"

    def __post_init__(self) -> None:
        for label, values in (
            ("classes_a", self.classes_a),
            ("classes_b", self.classes_b),
            ("policies_a", self.policies_a),
            ("policies_b", self.policies_b),
            ("weapons_a", self.weapons_a),
            ("weapons_b", self.weapons_b),
            ("specialty_nodes_a", self.specialty_nodes_a),
            ("specialty_nodes_b", self.specialty_nodes_b),
            ("initial_magazine_rounds_a", self.initial_magazine_rounds_a),
            ("initial_magazine_rounds_b", self.initial_magazine_rounds_b),
        ):
            if len(values) != 4:
                raise ValueError(f"{label} must contain exactly four entries")
        if self.scenario_id not in {
            "TEAM-CONTROL-4V4-V1",
            "TEAM-EXTRACT-4V4-V1",
            "TEAM-BREACH-4V4-V1",
            "TEAM-RECON-HACK-4V4-V1",
            "TEAM-SOCIAL-4V4-V1",
        }:
            raise ValueError(f"unknown team scenario {self.scenario_id}")

    def mirrored(self) -> "TeamScenarioSpec":
        return TeamScenarioSpec(
            scenario_id=self.scenario_id,
            scenario_version=self.scenario_version,
            level=self.level,
            classes_a=self.classes_b,
            classes_b=self.classes_a,
            policies_a=self.policies_b,
            policies_b=self.policies_a,
            weapons_a=self.weapons_b,
            weapons_b=self.weapons_a,
            opening_side=(
                self.opening_side
                if self.scenario_id == "TEAM-CONTROL-4V4-V1"
                else _other_side(self.opening_side)
            ),
            max_team_turns=self.max_team_turns,
            specialty_nodes_a=self.specialty_nodes_b,
            specialty_nodes_b=self.specialty_nodes_a,
            initial_magazine_rounds_a=self.initial_magazine_rounds_b,
            initial_magazine_rounds_b=self.initial_magazine_rounds_a,
            objective_fixture_id=self.objective_fixture_id,
        )


def default_team_spec(scenario_id: str, level: int = 10) -> TeamScenarioSpec:
    scenario_id = scenario_id.upper()
    if scenario_id == "TEAM-CONTROL-4V4-V1":
        return TeamScenarioSpec(level=level)
    if scenario_id == "TEAM-EXTRACT-4V4-V1":
        return TeamScenarioSpec(
            scenario_id=scenario_id,
            level=level,
            classes_a=("CORPSMAN", "BULWARK", "RIFLEMAN", "GHOST"),
            classes_b=("RIFLEMAN", "SHARPSHOOTER", "BULWARK", "GHOST"),
            policies_a=("POLICY_SUPPORT", "POLICY_OBJECTIVE", "POLICY_FIRE_DISCIPLINE", "POLICY_FLANK"),
            policies_b=("POLICY_PRESSURE", "POLICY_FIRE_DISCIPLINE", "POLICY_COVER_FIRST", "POLICY_FLANK"),
            weapons_a=("pistol", "service_rifle", "service_rifle", "smg"),
            weapons_b=("service_rifle", "precision_rifle", "service_rifle", "smg"),
        )
    if scenario_id == "TEAM-BREACH-4V4-V1":
        return TeamScenarioSpec(
            scenario_id=scenario_id,
            level=level,
            classes_a=("BREACHER", "RIFLEMAN", "BULWARK", "CORPSMAN"),
            classes_b=("BULWARK", "RIFLEMAN", "SHARPSHOOTER", "CORPSMAN"),
            policies_a=("POLICY_PRESSURE", "POLICY_FIRE_DISCIPLINE", "POLICY_OBJECTIVE", "POLICY_SUPPORT"),
            policies_b=("POLICY_COVER_FIRST", "POLICY_FIRE_DISCIPLINE", "POLICY_DIRECT", "POLICY_SUPPORT"),
            weapons_a=("shotgun", "service_rifle", "service_rifle", "pistol"),
            weapons_b=("service_rifle", "lmg", "precision_rifle", "pistol"),
        )
    if scenario_id == "TEAM-RECON-HACK-4V4-V1":
        return TeamScenarioSpec(
            scenario_id=scenario_id,
            level=level,
            classes_a=("HACKER", "TECH", "GHOST", "RIFLEMAN"),
            classes_b=("HACKER", "TECH", "BULWARK", "RIFLEMAN"),
            policies_a=("POLICY_SUPPORT", "POLICY_OBJECTIVE", "POLICY_FLANK", "POLICY_FIRE_DISCIPLINE"),
            policies_b=("POLICY_SUPPORT", "POLICY_OBJECTIVE", "POLICY_COVER_FIRST", "POLICY_PRESSURE"),
            weapons_a=("service_rifle", "service_rifle", "smg", "service_rifle"),
            weapons_b=("service_rifle", "service_rifle", "service_rifle", "lmg"),
        )
    if scenario_id == "TEAM-SOCIAL-4V4-V1":
        return TeamScenarioSpec(
            scenario_id=scenario_id,
            level=level,
            classes_a=("INFLUENCER", "BULWARK", "RIFLEMAN", "CORPSMAN"),
            classes_b=("INFLUENCER", "BULWARK", "RIFLEMAN", "CORPSMAN"),
            policies_a=("POLICY_SOCIAL", "POLICY_OBJECTIVE", "POLICY_FIRE_DISCIPLINE", "POLICY_SUPPORT"),
            policies_b=("POLICY_SOCIAL", "POLICY_OBJECTIVE", "POLICY_FIRE_DISCIPLINE", "POLICY_SUPPORT"),
            weapons_a=("pistol", "service_rifle", "service_rifle", "pistol"),
            weapons_b=("pistol", "service_rifle", "service_rifle", "pistol"),
        )
    raise ValueError(f"unknown team scenario {scenario_id}")


def _side_key(side: Side) -> str:
    return side.value


def _other_side(side: Side) -> Side:
    return Side.B if side is Side.A else Side.A


def _team_actor_ids(state: SimulationState, side: Side, *, capable_only: bool = False) -> list[str]:
    return sorted(
        actor.actor_id
        for actor in state.actors.values()
        if actor.side is side and (not capable_only or not actor.downed)
    )


def _objective_goal(actor_id: str, side: Side) -> Position:
    index = max(0, int(actor_id.rsplit("_", 1)[-1]) - 1)
    offsets = ((-2, -2), (-2, 2), (2, -2), (2, 2))
    x, y = offsets[index % len(offsets)]
    # Side B uses the opposite corner assignment so mirrored placements remain exact.
    if side is Side.B:
        x, y = -x, -y
    return Position(39 + x, 30 + y)


def _build_control_state(spec: TeamScenarioSpec, parameters: ParameterSet) -> tuple[SimulationState, GridMap, dict[str, str]]:
    grid = build_map("MAP_COMPOUND_80", parameters)
    actors = {}
    policies: dict[str, str] = {}
    spawn_y = (22, 28, 34, 40)
    for side, classes, policy_ids, weapons, specialty_nodes, initial_magazine_rounds in (
        (
            Side.A,
            spec.classes_a,
            spec.policies_a,
            spec.weapons_a,
            spec.specialty_nodes_a,
            spec.initial_magazine_rounds_a,
        ),
        (
            Side.B,
            spec.classes_b,
            spec.policies_b,
            spec.weapons_b,
            spec.specialty_nodes_b,
            spec.initial_magazine_rounds_b,
        ),
    ):
        for index, (class_id, policy_id, weapon_id, actor_specialty_nodes, initial_magazine_rounds) in enumerate(
            zip(classes, policy_ids, weapons, specialty_nodes, initial_magazine_rounds, strict=True),
            start=1,
        ):
            position = Position(6 if side is Side.A else 73, spawn_y[index - 1])
            actor = benchmark_actor(class_id, spec.level, side, position, parameters, actor_suffix=str(index), weapon_id=weapon_id)
            actor.specialty_nodes.update(actor_specialty_nodes)
            if initial_magazine_rounds is not None and actor.weapon is not None:
                actor.weapon.magazine_rounds = min(
                    actor.weapon.magazine_capacity,
                    max(0, initial_magazine_rounds),
                )
            actors[actor.actor_id] = actor
            policies[actor.actor_id] = policy_id
    state = SimulationState(spec.scenario_id, spec.scenario_version, actors, covers=grid.covers, active_side=spec.opening_side)
    state.objective = {
        "kind": "CONTROL",
        "map_id": grid.map_id,
        "zone": {"x_min": 35, "x_max": 44, "y_min": 25, "y_max": 34},
        "required_hold_seconds": 12.0,
        "hold_seconds": {"A": 0.0, "B": 0.0},
        "controller": None,
        "aim_ap": {actor_id: 0 for actor_id in actors},
        "flank_complete": {actor_id: False for actor_id in actors},
        "cover_destination": {},
        "attacked_targets_this_turn": {actor_id: [] for actor_id in actors},
        "quick_solution_targets_this_turn": {actor_id: [] for actor_id in actors},
        "moved_this_turn": {actor_id: False for actor_id in actors},
        "last_action": {actor_id: None for actor_id in actors},
        "last_fire_weapon": {actor_id: None for actor_id in actors},
        "prepared_aim_ap": {},
        "strongpoints": {},
        "fixture_id": spec.objective_fixture_id,
        "entry_fixture": None,
        "team_turn_index": 0,
    }
    subject = next(
        actor
        for actor in actors.values()
        if actor.side is Side.A and actor.actor_id.endswith("_1")
    )
    ally = next(
        actor
        for actor in actors.values()
        if actor.side is Side.A and actor.actor_id.endswith("_2")
    )
    threat = next(
        actor
        for actor in actors.values()
        if actor.side is Side.B and actor.actor_id.endswith("_1")
    )
    if spec.objective_fixture_id == "SPECIALTY-ARSENAL-ENTRY":
        state.objective["entry_fixture"] = {
            "kind": "FIGHTING_LOAD",
            "subject_id": subject.actor_id,
            "firearms": [
                {
                    "weapon_id": subject.weapon.weapon_id if subject.weapon else None,
                    "carriage": "TWO_POINT_SLING",
                    "physically_carried": True,
                    "combat_accessible": True,
                },
                {
                    "weapon_id": "pistol",
                    "carriage": "RETENTION_HOLSTER",
                    "physically_carried": True,
                    "combat_accessible": False,
                },
            ],
            "additional_accessible_firearms": 0,
            "burden_ignored": False,
            "complete": False,
        }
    elif spec.objective_fixture_id == "SPECIALTY-FLANKER-ENTRY":
        subject.position = Position(56, 30)
        ally.position = Position(40, 15)
        threat.position = Position(56, 15)
        state.objective["entry_fixture"] = {
            "kind": "ANGLE_ADVANTAGE",
            "subject_id": subject.actor_id,
            "target_id": threat.actor_id,
            "reference_ally_id": ally.actor_id,
            "target_current_cover_id": "COVER_55_10",
            "complete": False,
        }
    elif spec.objective_fixture_id == "SPECIALTY-GUARDIAN-ENTRY":
        subject.position = Position(42, 28)
        ally.position = Position(40, 30)
        threat.position = Position(47, 30)
        state.objective["entry_fixture"] = {
            "kind": "INTERPOSE",
            "subject_id": subject.actor_id,
            "protected_ally_id": ally.actor_id,
            "threat_id": threat.actor_id,
            "interpose_position": asdict(Position(42, 30)),
            "declared_threat_direction": "EAST",
            "prepared_before_threat": False,
            "complete": False,
        }
    elif spec.objective_fixture_id == "SPECIALTY-INFILTRATOR-ENTRY":
        subject.position = Position(6, 22)
        state.objective["entry_fixture"] = {
            "kind": "APPROACH_PLAN",
            "subject_id": subject.actor_id,
            "approach": [asdict(Position(6, 22)), asdict(Position(12, 22))],
            "observed_threat_ids": sorted(
                actor.actor_id for actor in actors.values() if actor.side is Side.B
            ),
            "route_revision": 1,
            "assessed_route_revision": 1,
            "complete": False,
        }
    elif spec.objective_fixture_id == "SPECIALTY-SHIELD_BEARER-ENTRY":
        state.objective["entry_fixture"] = {
            "kind": "DEPLOYED_SHIELD",
            "subject_id": subject.actor_id,
            "device_id": "BALLISTIC-SHIELD-1",
            "position": asdict(subject.position),
            "facing_degrees": 0.0,
            "cover_hp": int(
                parameters.values["equipment.ballistic_shield.cover_hp"]
            ),
            "material_resistance": int(
                parameters.values[
                    "equipment.ballistic_shield.material_resistance"
                ]
            ),
            "physical_burden": int(
                parameters.values["equipment.ballistic_shield.burden"]
            ),
            "physical_handling": int(
                parameters.values["equipment.ballistic_shield.handling"]
            ),
            "effective_burden": int(
                parameters.values["equipment.ballistic_shield.burden"]
            ),
            "effective_handling": int(
                parameters.values["equipment.ballistic_shield.handling"]
            ),
            "compatible_one_handed_equipment": True,
            "deployed": True,
            "complete": False,
        }
    elif spec.objective_fixture_id in {
        "SPECIALTY-SNIPER-ENTRY",
        "SPECIALTY-SNIPER-ENTRY-MOVED",
    }:
        subject.position = Position(5, 5)
        threat.position = Position(45, 5)
        state.objective["entry_fixture"] = {
            "kind": "FIRING_SOLUTION",
            "subject_id": subject.actor_id,
            "target_id": threat.actor_id,
            "bound_firing_position": asdict(subject.position),
            "bound_target_position": asdict(threat.position),
            "material_change": (
                spec.objective_fixture_id == "SPECIALTY-SNIPER-ENTRY-MOVED"
            ),
            "solution_active": False,
            "complete": False,
        }
    elif spec.objective_fixture_id in {
        "SPECIALTY-EVADER-ENTRY",
        "SPECIALTY-EVADER-ENTRY-OBSERVED",
    }:
        subject.position = Position(15, 28)
        observers = sorted(
            (
                actor
                for actor in actors.values()
                if actor.side is Side.B
            ),
            key=lambda actor: actor.actor_id,
        )
        observer_positions = (
            Position(33, 24),
            Position(33, 25),
            Position(34, 24),
            Position(34, 25),
        )
        for observer, position in zip(
            observers, observer_positions, strict=True
        ):
            observer.position = position
        if spec.objective_fixture_id == "SPECIALTY-EVADER-ENTRY-OBSERVED":
            observers[0].position = Position(33, 23)
        state.objective["entry_fixture"] = {
            "kind": "BREAK_CONTACT",
            "subject_id": subject.actor_id,
            "destination": asdict(Position(15, 33)),
            "exact_observer_ids": [observer.actor_id for observer in observers],
            "complete": False,
        }
    for actor_id in actors:
        state.objective["cover_destination"][actor_id] = _candidate_cover_destination(actor_id, state, grid)
    _refresh_knowledge(state, grid)
    return state, grid, policies


def _in_control_zone(position: Position) -> bool:
    return 35 <= position.x <= 44 and 25 <= position.y <= 34


def _refresh_knowledge(state: SimulationState, grid: GridMap) -> None:
    for observer in state.actors.values():
        _refresh_actor_knowledge(observer.actor_id, state, grid)


def _refresh_actor_knowledge(observer_id: str, state: SimulationState, grid: GridMap) -> None:
    observer = state.actors[observer_id]
    for target in state.actors.values():
        if target.side is observer.side or target.downed:
            continue
        visible = grid.line_of_sight(observer.position, target.position, target.stance) and grid.exposure(observer.position, target.position, target.stance) != "none"
        if visible:
            observer.target_knowledge[target.actor_id] = KnowledgeState.CONFIRMED
            observer.target_last_known[target.actor_id] = target.position
        elif observer.target_knowledge.get(target.actor_id) is KnowledgeState.CONFIRMED:
            observer.target_knowledge[target.actor_id] = KnowledgeState.LAST_KNOWN


def _candidate_cover_destination(actor_id: str, state: SimulationState, grid: GridMap) -> Position | None:
    actor = state.actors[actor_id]
    # Deliberately curated fixture waypoints adjacent to the declared cover lines.
    if actor.side is Side.A:
        candidates = (Position(24, 15), Position(24, 45), Position(34, 23), Position(34, 36))
    else:
        candidates = (Position(56, 15), Position(56, 45), Position(45, 23), Position(45, 36))
    del grid
    # All four waypoints are declared open cells on MAP_COMPOUND_80. Choose the
    # nearest lane deterministically without performing four redundant A* runs.
    return min(candidates, key=lambda candidate: (actor.position.distance_to(candidate), candidate.x, candidate.y))


def _flank_destination(actor_id: str, side: Side) -> Position:
    index = max(0, int(actor_id.rsplit("_", 1)[-1]) - 1)
    return Position(39, 16 if (index + (1 if side is Side.B else 0)) % 2 == 0 else 44)


def _behind_cover(actor_id: str, state: SimulationState, grid: GridMap) -> bool:
    actor = state.actors[actor_id]
    enemies = [enemy for enemy in state.actors.values() if enemy.side is not actor.side and not enemy.downed]
    if not enemies:
        return False
    protected = 0
    for enemy in enemies:
        exposure = grid.exposure(enemy.position, actor.position, actor.stance)
        if exposure in {"none", "quarter", "half"}:
            protected += 1
    return protected >= math.ceil(len(enemies) / 2)


def _policy_observation(actor_id: str, state: SimulationState, grid: GridMap) -> PolicyObservation:
    actor = state.actors[actor_id]
    known: list[KnownTarget] = []
    for target in sorted(state.actors.values(), key=lambda item: item.actor_id):
        if target.side is actor.side:
            continue
        knowledge = actor.target_knowledge.get(target.actor_id, KnowledgeState.UNAWARE)
        position = target.position if knowledge is KnowledgeState.CONFIRMED else actor.target_last_known.get(target.actor_id)
        los = bool(position is not None and knowledge is KnowledgeState.CONFIRMED and grid.line_of_sight(actor.position, target.position, target.stance))
        exposure = grid.exposure(actor.position, target.position, target.stance) if los else "none"
        direct_fire = los and exposure != "none"
        known.append(KnownTarget(target.actor_id, knowledge, position, actor.position.distance_to(position) if position else None, direct_fire, target.downed))
    weapon = actor.weapon
    visible_casualties = [
        ally
        for ally in state.actors.values()
        if ally.side is actor.side
        and ally.actor_id != actor_id
        and ally.downed
        and ally.conditions
        and actor.position.distance_to(ally.position) <= 2
    ]
    closest_distance = min((target.distance_m for target in known if target.distance_m is not None), default=None)
    cover_destination = state.objective["cover_destination"].get(actor_id)
    metadata = {
        "aim_ap": state.objective["aim_ap"][actor_id],
        "flank_complete": state.objective["flank_complete"][actor_id],
        "at_objective": _in_control_zone(actor.position),
        "cover_destination": cover_destination,
        "inside_close_envelope": closest_distance is not None and closest_distance <= state.objective.get("close_envelope_m", 20),
        "suppression_active": any(zone.owner_id == actor_id for zone in state.suppression.values()),
        "overwatch_active": any(
            prep.owner_id == actor_id and prep.active
            for prep in state.prepared.values()
        ),
        "can_overwatch": weapon is not None and not actor.downed,
        "can_establish_strongpoint": (
            has_node(actor, ANCHOR_ESTABLISH_STRONGPOINT)
            and _behind_cover(actor_id, state, grid)
        ),
        "strongpoint_established": actor_id in state.objective["strongpoints"],
        "can_treat": actor.class_id == "CORPSMAN" and bool(visible_casualties),
        "casualty_id": visible_casualties[0].actor_id if visible_casualties else None,
        "can_hack": False,
        "can_deploy": False,
        "can_cqc": any(target.distance_m is not None and target.distance_m <= 1.5 and target.exposed for target in known),
        "needs_cycle": bool(weapon and weapon.needs_cycle),
        "needs_reload": bool(weapon and weapon.chamber != "LIVE" and weapon.magazine_rounds == 0),
        "can_reload": bool(weapon and weapon.reserve_rounds > 0),
        "magazine_rounds": weapon.magazine_rounds if weapon else 0,
    }
    return PolicyObservation(
        actor.actor_id,
        actor.position,
        actor.stance,
        actor.ap,
        actor.hp,
        actor.res,
        weapon.weapon_id if weapon else None,
        weapon.total_rounds() if weapon else 0,
        weapon.current_recoil if weapon else 0,
        weapon.fire_modes if weapon else (),
        tuple(known),
        _objective_goal(actor_id, actor.side),
        bool(visible_casualties),
        _behind_cover(actor_id, state, grid),
        metadata,
    )


def _blank_metrics(state: SimulationState) -> dict[str, Any]:
    sides = {side.value: {} for side in Side}
    return {
        "by_side": {
            side: {
                "ap_by_action": {},
                "movement_m": 0.0,
                "exposure_m": 0.0,
                "cover_seconds": 0.0,
                "exposed_action_seconds": 0.0,
                "shots": 0,
                "rounds": 0,
                "connections": 0,
                "res_damage": 0,
                "hp_damage": 0,
                "reload_ap": 0,
                "cycle_ap": 0,
                "suppression_setups": 0,
                "suppression_exposures": 0,
                "overwatch_setups": 0,
                "overwatch_triggers": 0,
                "cqc_attempts": 0,
                "cqc_successes": 0,
                "medical_actions": 0,
                "accessible_firearms": 0,
                "approach_checks": 0,
                "interpose_triggers": 0,
                "shield_effective_burden": 0,
                "objective_seconds": 0.0,
            }
            for side in sides
        },
        "by_actor": {
            actor_id: {"class_id": actor.class_id, "policy_id": None, "ap_spent": 0, "damage": 0, "movement_m": 0.0, "objective_seconds": 0.0}
            for actor_id, actor in state.actors.items()
        },
        "illegal_actions": 0,
        "invariant_failures": [],
        "unsupported_events": [],
        "specialty_effects": [],
    }


def _add_action_ap(metrics: dict[str, Any], actor_id: str, side: Side, action: str, amount: int) -> None:
    if amount <= 0:
        return
    side_metrics = metrics["by_side"][_side_key(side)]
    side_metrics["ap_by_action"][action] = side_metrics["ap_by_action"].get(action, 0) + amount
    metrics["by_actor"][actor_id]["ap_spent"] += amount


def _execute_control_entry_fixture(
    engine: EventEngine,
    grid: GridMap,
    actor_id: str,
    metrics: dict[str, Any],
) -> bool:
    fixture = engine.state.objective.get("entry_fixture")
    if (
        not fixture
        or fixture.get("complete")
        or fixture.get("subject_id") != actor_id
    ):
        return False
    actor = engine.state.actors[actor_id]
    side_metrics = metrics["by_side"][_side_key(actor.side)]

    if fixture["kind"] == "FIRING_SOLUTION":
        target = engine.state.actors[fixture["target_id"]]
        if (
            actor.weapon is None
            or not grid.line_of_sight(actor.position, target.position, target.stance)
            or grid.exposure(actor.position, target.position, target.stance) == "none"
        ):
            metrics["unsupported_events"].append(
                {
                    "actor_id": actor_id,
                    "action": "BUILD FIRING SOLUTION",
                    "reason": "deep aim fixture lacks a visible target and firing solution",
                }
            )
            fixture["complete"] = True
            return False
        deep_aim = has_node(actor, SNIPER_DEEP_AIM)
        ordinary_aim_ap = 1
        additional_ap = (
            int(
                engine.parameters.values[
                    "specialty.sniper.deep_aim.additional_ap"
                ]
            )
            if deep_aim
            else 0
        )
        total_ap = ordinary_aim_ap + additional_ap
        if actor.ap < total_ap:
            return False
        ordinary_difficulty = int(
            engine.parameters.values["check.difficulty.standard"]
        )
        difficulty_offset = (
            int(
                engine.parameters.values[
                    "specialty.sniper.deep_aim.difficulty_offset"
                ]
            )
            if deep_aim
            else 0
        )

        def build_solution(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            actor.ap -= total_ap
            state = engine.state.objective
            state["aim_ap"][actor_id] = min(
                int(engine.parameters.values["fire.aim.max_ap_on_one_solution"]),
                state["aim_ap"][actor_id] + ordinary_aim_ap,
            )
            bound_firing_position = dict(fixture["bound_firing_position"])
            bound_target_id = str(fixture["target_id"])
            if fixture["material_change"]:
                target.position = Position(target.position.x, target.position.y + 1)
            current_firing_position = asdict(actor.position)
            current_target_position = asdict(target.position)
            solution_active = bool(
                deep_aim
                and current_firing_position == bound_firing_position
                and bound_target_id == target.actor_id
                and current_target_position == fixture["bound_target_position"]
            )
            fixture["solution_active"] = solution_active
            fixture["complete"] = True
            _add_action_ap(
                metrics,
                actor_id,
                actor.side,
                "BUILD FIRING SOLUTION",
                total_ap,
            )
            stages.append(
                {
                    "stage": "TARGET- AND POSITION-BOUND FIRING SOLUTION",
                    "ordinary_aim_ap": ordinary_aim_ap,
                    "deep_aim_additional_ap": additional_ap,
                    "firing_position": current_firing_position,
                    "target_id": target.actor_id,
                    "target_position": current_target_position,
                    "bound_firing_position": bound_firing_position,
                    "bound_target_id": bound_target_id,
                    "bound_target_position": fixture["bound_target_position"],
                    "attack_difficulty": ordinary_difficulty + difficulty_offset,
                    "material_change": fixture["material_change"],
                    "solution_active": solution_active,
                }
            )
            if deep_aim:
                effects = [
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=SNIPER_DEEP_AIM,
                        action="BUILD FIRING SOLUTION",
                        field="additional_ap",
                        before=0,
                        after=additional_ap,
                    ),
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=SNIPER_DEEP_AIM,
                        action="BUILD FIRING SOLUTION",
                        field="attack_difficulty",
                        before=ordinary_difficulty,
                        after=ordinary_difficulty + difficulty_offset,
                    ),
                ]
                stages.append({"stage": "SPECIALTY EFFECT", "effects": effects})
            emitted["solution_active"] = solution_active
            return None

        entry = engine.record(
            actor_id,
            "BUILD FIRING SOLUTION",
            {
                "target_id": target.actor_id,
                "firing_position": asdict(actor.position),
            },
            build_solution,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "BREAK_CONTACT":
        destination = Position(**fixture["destination"])
        observers = [
            engine.state.actors[observer_id]
            for observer_id in fixture["exact_observer_ids"]
        ]
        los_before = {
            observer.actor_id: bool(
                grid.line_of_sight(observer.position, actor.position, actor.stance)
                and grid.exposure(observer.position, actor.position, actor.stance)
                != "none"
            )
            for observer in observers
        }
        los_after = {
            observer.actor_id: bool(
                grid.line_of_sight(observer.position, destination, actor.stance)
                and grid.exposure(observer.position, destination, actor.stance)
                != "none"
            )
            for observer in observers
        }
        if not all(los_before.values()):
            metrics["unsupported_events"].append(
                {
                    "actor_id": actor_id,
                    "action": "BREAK CONTACT",
                    "reason": "fixture does not begin under exact direct observation",
                }
            )
            fixture["complete"] = True
            return False
        if any(los_after.values()):
            metrics["unsupported_events"].append(
                {
                    "actor_id": actor_id,
                    "action": "BREAK CONTACT",
                    "reason": "one or more exact observers retain LOS",
                    "remaining_observer_ids": sorted(
                        observer_id
                        for observer_id, has_los in los_after.items()
                        if has_los
                    ),
                }
            )
            fixture["complete"] = True
            return False
        movement_ap = 1
        movement_rate_m = movement_rate(
            actor.stance,
            actor.burden,
            actor.class_id,
            None,
            engine.parameters,
        )
        path = shortest_path(actor.position, destination, grid, movement_rate_m)
        if not path or path[-1] != destination:
            metrics["unsupported_events"].append(
                {
                    "actor_id": actor_id,
                    "action": "BREAK CONTACT",
                    "reason": "authored LOS-breaking movement is not traversable for one AP",
                }
            )
            fixture["complete"] = True
            return False
        ordinary_ap = int(engine.parameters.values["stealth.break_contact_ap"])
        break_contact = has_node(actor, EVADER_BREAK_CONTACT)
        ap_discount = (
            int(
                engine.parameters.values[
                    "specialty.evader.break_contact.ap_discount"
                ]
            )
            if break_contact
            else 0
        )
        stealth_ap = max(1, ordinary_ap - ap_discount)
        if actor.ap < movement_ap + stealth_ap:
            return False
        ordinary_difficulty = int(
            engine.parameters.values["check.difficulty.standard"]
        )
        difficulty_offset = (
            int(
                engine.parameters.values[
                    "specialty.evader.break_contact.difficulty_offset"
                ]
            )
            if break_contact
            else 0
        )
        difficulty = ordinary_difficulty + difficulty_offset
        sequence = engine.event_count

        def break_observation(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            start = actor.position
            roll = engine.rng.stream(f"event:{sequence}").d20("BREAK CONTACT")
            total = roll + actor.skill + actor.aptitude
            success = total >= difficulty
            actor.position = destination
            actor.ap -= movement_ap + stealth_ap
            fixture["complete"] = True
            distance = sum(
                left.distance_to(right)
                for left, right in zip((start, *path[:-1]), path, strict=True)
            )
            side_metrics["movement_m"] += distance
            metrics["by_actor"][actor_id]["movement_m"] += distance
            _add_action_ap(
                metrics,
                actor_id,
                actor.side,
                "MOVE TO BREAK LOS",
                movement_ap,
            )
            _add_action_ap(
                metrics,
                actor_id,
                actor.side,
                "BREAK CONTACT",
                stealth_ap,
            )
            for observer in observers:
                observer.target_knowledge[actor_id] = (
                    KnowledgeState.APPROXIMATE
                    if success
                    else KnowledgeState.LAST_KNOWN
                )
                observer.target_last_known[actor_id] = start
            stages.append(
                {
                    "stage": "BREAK LOS THEN RE-ENTER UNCERTAINTY",
                    "position_before": asdict(start),
                    "position_after": asdict(destination),
                    "movement_ap": movement_ap,
                    "stealth_ap": stealth_ap,
                    "difficulty_before": ordinary_difficulty,
                    "difficulty_after": difficulty,
                    "observer_los_before": los_before,
                    "observer_los_after": los_after,
                    "every_exact_observer_lost_los": not any(
                        los_after.values()
                    ),
                    "roll": roll,
                    "total": total,
                    "success": success,
                }
            )
            if break_contact:
                effects = [
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=EVADER_BREAK_CONTACT,
                        action="BREAK CONTACT",
                        field="ap_cost",
                        before=ordinary_ap,
                        after=stealth_ap,
                    ),
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=EVADER_BREAK_CONTACT,
                        action="BREAK CONTACT",
                        field="stealth_difficulty",
                        before=ordinary_difficulty,
                        after=difficulty,
                    ),
                ]
                stages.append({"stage": "SPECIALTY EFFECT", "effects": effects})
            emitted["stealth_success"] = success
            return None

        entry = engine.record(
            actor_id,
            "BREAK CONTACT",
            {
                "destination": asdict(destination),
                "exact_observer_ids": fixture["exact_observer_ids"],
            },
            break_observation,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "FIGHTING_LOAD":
        fighting_load = has_node(actor, ARSENAL_FIGHTING_LOAD)

        def configure_load(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            additional = 1 if fighting_load else 0
            fixture["additional_accessible_firearms"] = additional
            fixture["firearms"][1]["combat_accessible"] = bool(additional)
            fixture["complete"] = True
            accessible = sum(
                bool(item["combat_accessible"]) for item in fixture["firearms"]
            )
            side_metrics["accessible_firearms"] += accessible
            stages.append(
                {
                    "stage": "PHYSICAL FIGHTING LOAD",
                    "firearms": fixture["firearms"],
                    "additional_accessible_firearms": additional,
                    "burden_ignored": False,
                }
            )
            if fighting_load:
                effect = record_effect(
                    metrics,
                    actor_id=actor_id,
                    node_id=ARSENAL_FIGHTING_LOAD,
                    action="CONFIGURE FIGHTING LOAD",
                    field="additional_accessible_firearms",
                    before=0,
                    after=additional,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            emitted["accessible_firearms"] = accessible
            return None

        entry = engine.record(
            actor_id,
            "CONFIGURE FIGHTING LOAD",
            {"firearm_count": len(fixture["firearms"])},
            configure_load,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "DEPLOYED_SHIELD":
        shield_training = has_node(actor, SHIELD_BEARER_SHIELD_TRAINING)

        def deploy_shield(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            burden_reduction = (
                int(
                    engine.parameters.values[
                        "specialty.shield_bearer.shield_training.burden_reduction"
                    ]
                )
                if shield_training
                else 0
            )
            handling_reduction = (
                int(
                    engine.parameters.values[
                        "specialty.shield_bearer.shield_training.handling_reduction"
                    ]
                )
                if shield_training
                else 0
            )
            fixture["effective_burden"] = max(
                0, fixture["physical_burden"] - burden_reduction
            )
            fixture["effective_handling"] = max(
                0, fixture["physical_handling"] - handling_reduction
            )
            actor.burden += fixture["effective_burden"]
            fixture["complete"] = True
            side_metrics["shield_effective_burden"] += fixture[
                "effective_burden"
            ]
            stages.append(
                {
                    "stage": "DEPLOYED DIRECTIONAL SHIELD",
                    **{
                        key: fixture[key]
                        for key in (
                            "device_id",
                            "position",
                            "facing_degrees",
                            "cover_hp",
                            "material_resistance",
                            "physical_burden",
                            "physical_handling",
                            "effective_burden",
                            "effective_handling",
                            "compatible_one_handed_equipment",
                            "deployed",
                        )
                    },
                }
            )
            if shield_training:
                effects = [
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=SHIELD_BEARER_SHIELD_TRAINING,
                        action="DEPLOY SHIELD",
                        field="effective_burden",
                        before=fixture["physical_burden"],
                        after=fixture["effective_burden"],
                    ),
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=SHIELD_BEARER_SHIELD_TRAINING,
                        action="DEPLOY SHIELD",
                        field="effective_handling",
                        before=fixture["physical_handling"],
                        after=fixture["effective_handling"],
                    ),
                ]
                stages.append({"stage": "SPECIALTY EFFECT", "effects": effects})
            emitted["effective_burden"] = fixture["effective_burden"]
            emitted["effective_handling"] = fixture["effective_handling"]
            return None

        entry = engine.record(
            actor_id,
            "DEPLOY SHIELD",
            {
                "device_id": fixture["device_id"],
                "facing_degrees": fixture["facing_degrees"],
            },
            deploy_shield,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "APPROACH_PLAN":
        if actor.ap < 1:
            return False
        approach_plan = has_node(actor, INFILTRATOR_APPROACH_PLAN)
        destination = Position(**fixture["approach"][-1])
        rate = movement_rate(
            actor.stance,
            actor.burden,
            actor.class_id,
            None,
            engine.parameters,
        )
        path = shortest_path(actor.position, destination, grid, rate)
        if not path:
            metrics["unsupported_events"].append(
                {
                    "actor_id": actor_id,
                    "action": "FOLLOW APPROACH",
                    "reason": "authored approach is not physically traversable",
                }
            )
            fixture["complete"] = True
            return False
        ordinary_difficulty = int(
            engine.parameters.values["check.difficulty.standard"]
        )
        offset = (
            int(
                engine.parameters.values[
                    "specialty.infiltrator.approach_plan.difficulty_offset"
                ]
            )
            if approach_plan
            else 0
        )
        difficulty = ordinary_difficulty + offset
        sequence = engine.event_count

        def follow_approach(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            before = actor.position
            roll = engine.rng.stream(f"event:{sequence}").d20("APPROACH STEALTH")
            total = roll + actor.skill + actor.aptitude
            success = total >= difficulty
            actor.position = path[-1]
            actor.ap -= 1
            fixture["complete"] = True
            side_metrics["approach_checks"] += 1
            distance = sum(
                left.distance_to(right)
                for left, right in zip((before, *path[:-1]), path, strict=True)
            )
            side_metrics["movement_m"] += distance
            metrics["by_actor"][actor_id]["movement_m"] += distance
            _add_action_ap(metrics, actor_id, actor.side, "FOLLOW APPROACH", 1)
            for observer_id in fixture["observed_threat_ids"]:
                observer = engine.state.actors[observer_id]
                if success:
                    observer.target_knowledge[actor_id] = KnowledgeState.APPROXIMATE
                    observer.target_last_known[actor_id] = before
                else:
                    observer.target_knowledge[actor_id] = KnowledgeState.CONFIRMED
                    observer.target_last_known[actor_id] = actor.position
            stages.append(
                {
                    "stage": "OBSERVED APPROACH",
                    "route": fixture["approach"],
                    "observed_threat_ids": fixture["observed_threat_ids"],
                    "route_revision": fixture["route_revision"],
                    "assessed_route_revision": fixture[
                        "assessed_route_revision"
                    ],
                    "position_before": asdict(before),
                    "position_after": asdict(actor.position),
                    "roll": roll,
                    "total": total,
                    "difficulty": difficulty,
                    "success": success,
                }
            )
            if approach_plan:
                effect = record_effect(
                    metrics,
                    actor_id=actor_id,
                    node_id=INFILTRATOR_APPROACH_PLAN,
                    action="FOLLOW APPROACH",
                    field="stealth_difficulty",
                    before=ordinary_difficulty,
                    after=difficulty,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            emitted["stealth_success"] = success
            return None

        entry = engine.record(
            actor_id,
            "FOLLOW APPROACH",
            {
                "destination": asdict(destination),
                "route_revision": fixture["route_revision"],
            },
            follow_approach,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "ANGLE_ADVANTAGE":
        target = engine.state.actors[fixture["target_id"]]
        reference = engine.state.actors[fixture["reference_ally_id"]]
        cover = engine.state.covers[fixture["target_current_cover_id"]]
        reference_line = set(trace_cells(reference.position, target.position))
        attack_line = set(trace_cells(actor.position, target.position))
        reference_protected = bool(reference_line & cover.cells)
        angle_unprotected = not bool(attack_line & cover.cells)
        legitimate = (
            actor.weapon is not None
            and reference_protected
            and angle_unprotected
            and grid.line_of_sight(actor.position, target.position, target.stance)
        )
        if not legitimate:
            metrics["unsupported_events"].append(
                {
                    "actor_id": actor_id,
                    "action": "ANGLE ADVANTAGE FIRE",
                    "reason": "fixture lacks current-cover-relative firing geometry",
                }
            )
            fixture["complete"] = True
            return False
        cost = int(engine.parameters.values["fire.semi_single.ap"])
        if actor.ap < cost:
            return False
        angle_advantage = has_node(actor, FLANKER_ANGLE_ADVANTAGE)
        ordinary_difficulty = int(
            engine.parameters.values["check.difficulty.standard"]
        )
        offset = (
            int(
                engine.parameters.values[
                    "specialty.flanker.angle_advantage.difficulty_offset"
                ]
            )
            if angle_advantage
            else 0
        )
        sequence = engine.event_count

        def flank_fire(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            exposure = grid.exposure(actor.position, target.position, target.stance)
            result = fire_action(
                actor.weapon,
                "SEMI AUTO" if "SEMI AUTO" in actor.weapon.fire_modes else "SINGLE",
                1,
                engine.rng.stream(f"event:{sequence}").d20("ANGLE ADVANTAGE"),
                actor.skill,
                actor.aptitude,
                ordinary_difficulty + offset,
                actor.position.distance_to(target.position),
                exposure,
                engine.parameters,
            )
            actor.ap -= result.ap_cost
            fixture["complete"] = True
            side_metrics["shots"] += 1
            side_metrics["rounds"] += result.rounds_fired
            side_metrics["connections"] += result.target_connections
            routed = _apply_connections(
                actor_id,
                target.actor_id,
                actor.side,
                result,
                engine.state,
                metrics,
                engine.parameters,
            )
            _add_action_ap(
                metrics,
                actor_id,
                actor.side,
                "ANGLE ADVANTAGE FIRE",
                result.ap_cost,
            )
            stages.extend(
                (
                    {
                        "stage": "TARGET CURRENT COVER",
                        "cover_id": cover.cover_id,
                        "reference_ally_id": reference.actor_id,
                        "reference_line_protected": reference_protected,
                        "attack_line_protected": not angle_unprotected,
                        "valid_firing_line": True,
                    },
                    {"stage": "ATTACK ROLL", "result": result.to_dict()},
                    {"stage": "PROJECTILE / RES / HP", "connections": routed},
                )
            )
            if angle_advantage:
                effect = record_effect(
                    metrics,
                    actor_id=actor_id,
                    node_id=FLANKER_ANGLE_ADVANTAGE,
                    action="ANGLE ADVANTAGE FIRE",
                    field="attack_difficulty",
                    before=result.attack.check.difficulty - offset,
                    after=result.attack.check.difficulty,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            emitted["target_downed"] = target.downed
            return None

        entry = engine.record(
            actor_id,
            "ANGLE ADVANTAGE FIRE",
            {
                "target_id": target.actor_id,
                "target_current_cover_id": cover.cover_id,
            },
            flank_fire,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "INTERPOSE":
        guardian = has_node(actor, GUARDIAN_INTERPOSE)
        ally = engine.state.actors[fixture["protected_ally_id"]]
        threat = engine.state.actors[fixture["threat_id"]]
        destination = Position(**fixture["interpose_position"])
        allowance = (
            float(
                engine.parameters.values[
                    "specialty.guardian.interpose.prepared_movement_m"
                ]
            )
            if guardian
            else 0.0
        )

        def trigger_interpose(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            before = actor.position
            travelled = 0.0
            if guardian:
                fixture["prepared_before_threat"] = True
                path = shortest_path(actor.position, destination, grid, allowance)
                if path:
                    actor.position = path[-1]
                    travelled = sum(
                        left.distance_to(right)
                        for left, right in zip(
                            (before, *path[:-1]), path, strict=True
                        )
                    )
                    side_metrics["movement_m"] += travelled
                    metrics["by_actor"][actor_id]["movement_m"] += travelled
            fixture["complete"] = True
            side_metrics["interpose_triggers"] += 1
            line = set(trace_cells(threat.position, ally.position))
            physically_interposed = (
                round(actor.position.x), round(actor.position.y)
            ) in line and actor.position != ally.position
            stages.append(
                {
                    "stage": "INTERPOSE PREPARATION AND TRIGGER",
                    "protected_ally_id": ally.actor_id,
                    "threat_id": threat.actor_id,
                    "declared_threat_direction": fixture[
                        "declared_threat_direction"
                    ],
                    "prepared_before_threat": fixture[
                        "prepared_before_threat"
                    ],
                    "position_before": asdict(before),
                    "position_after": asdict(actor.position),
                    "movement_m": travelled,
                    "physically_interposed": physically_interposed,
                }
            )
            if guardian:
                effects = [
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=GUARDIAN_INTERPOSE,
                        action="INTERPOSE TRIGGER",
                        field="prepared_before_threat",
                        before=False,
                        after=True,
                    ),
                    record_effect(
                        metrics,
                        actor_id=actor_id,
                        node_id=GUARDIAN_INTERPOSE,
                        action="INTERPOSE TRIGGER",
                        field="prepared_movement_m",
                        before=0.0,
                        after=travelled,
                    ),
                ]
                stages.append({"stage": "SPECIALTY EFFECT", "effects": effects})
            emitted["physically_interposed"] = physically_interposed
            return None

        entry = engine.record(
            actor_id,
            "INTERPOSE TRIGGER",
            {
                "protected_ally_id": ally.actor_id,
                "threat_id": threat.actor_id,
            },
            trigger_interpose,
        )
        return entry.rejected_reason is None
    return False


def _connection_damage(result: Any, connection_index: int, parameters: ParameterSet) -> tuple[int, int]:
    damage = result.attack.effective_damage
    target_pen = result.attack.effective_target_pen
    if result.attack.quality is HitQuality.CRITICAL_HIT and connection_index > 0:
        if result.attack.critical_effect == "DEVASTATING HIT":
            damage -= parameters.values["critical.devastating_damage"]
        elif result.attack.critical_effect == "DEEP HIT":
            target_pen -= parameters.values["critical.deep_target_pen"]
    return max(0, damage), max(0, target_pen)


def _apply_connections(
    actor_id: str,
    target_id: str,
    side: Side,
    fire_result: Any,
    state: SimulationState,
    metrics: dict[str, Any],
    parameters: ParameterSet,
) -> list[dict[str, Any]]:
    target = state.actors[target_id]
    routed: list[dict[str, Any]] = []
    for index in range(fire_result.target_connections):
        damage, target_pen = _connection_damage(fire_result, index, parameters)
        result = apply_damage(target, damage, target_pen)
        routed.append(result.to_dict())
        side_metrics = metrics["by_side"][_side_key(side)]
        side_metrics["res_damage"] += result.res_damage
        side_metrics["hp_damage"] += result.total_hp_damage
        metrics["by_actor"][actor_id]["damage"] += result.res_damage + result.total_hp_damage
        if target.downed:
            break
    return routed


def _resolve_committed_suppression_attack(
    engine: EventEngine,
    zone: SuppressionZone,
    target_id: str,
    grid: GridMap,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    state = engine.state
    owner = state.actors[zone.owner_id]
    target = state.actors[target_id]
    stream = engine.rng.stream(f"suppression:{engine.event_count}:{zone.zone_id}:{target.position.x}:{target.position.y}")
    roll = stream.d20("SUPPRESSION ATTACK")
    damage, target_pen, _material_pen, range_penalty = attack_values_for_weapon(owner.weapon.weapon_id, owner.position.distance_to(target.position), engine.parameters)
    difficulty = engine.parameters.values["check.difficulty.standard"] + range_penalty + owner.weapon.current_recoil + engine.parameters.values["suppression.attack_difficulty_modifier"]
    attack = resolve_attack(roll, owner.skill, owner.aptitude, difficulty, damage, target_pen, engine.parameters)
    routed = []
    if attack.quality is not HitQuality.MISS:
        result = apply_damage(target, attack.effective_damage, attack.effective_target_pen)
        routed.append(result.to_dict())
        side_metrics = metrics["by_side"][_side_key(owner.side)]
        side_metrics["connections"] += 1
        side_metrics["res_damage"] += result.res_damage
        side_metrics["hp_damage"] += result.total_hp_damage
        metrics["by_actor"][owner.actor_id]["damage"] += result.res_damage + result.total_hp_damage
    zone.remaining_capacity -= 1
    metrics["by_side"][_side_key(owner.side)]["suppression_exposures"] += 1
    return {"roll": roll, "attack": attack.to_dict(), "connections": routed, "remaining_capacity": zone.remaining_capacity}


def _resolve_overwatch_attack(
    engine: EventEngine,
    prepared: PreparedAction,
    target_id: str,
    grid: GridMap,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    owner = engine.state.actors[prepared.owner_id]
    target = engine.state.actors[target_id]
    mode = "SINGLE" if "SINGLE" in owner.weapon.fire_modes and "SEMI AUTO" not in owner.weapon.fire_modes else "SEMI AUTO"
    stream = engine.rng.stream(f"overwatch:{engine.event_count}:{prepared.prep_id}:{target.position.x}:{target.position.y}")
    result = fire_action(
        owner.weapon,
        mode,
        1,
        stream.d20("OVERWATCH ATTACK"),
        owner.skill,
        owner.aptitude,
        engine.parameters.values["check.difficulty.standard"],
        owner.position.distance_to(target.position),
        "full",
        engine.parameters,
        aim_ap=int(engine.state.objective["prepared_aim_ap"].get(prepared.prep_id, 0)),
        legitimate=grid.line_of_sight(owner.position, target.position, target.stance),
    )
    side_metrics = metrics["by_side"][_side_key(owner.side)]
    side_metrics["shots"] += 1
    side_metrics["rounds"] += result.rounds_fired
    side_metrics["connections"] += result.target_connections
    side_metrics["overwatch_triggers"] += 1
    routed = _apply_connections(owner.actor_id, target_id, owner.side, result, engine.state, metrics, engine.parameters)
    prepared.shots_remaining -= 1
    if prepared.shots_remaining <= 0:
        prepared.active = False
    return {"result": result.to_dict(), "connections": routed, "shots_remaining": prepared.shots_remaining}


def _movement_trigger_provider(engine: EventEngine, grid: GridMap, metrics: dict[str, Any], actor_id: str) -> list[TriggerEffect]:
    actor = engine.state.actors[actor_id]
    cell = (round(actor.position.x), round(actor.position.y))
    effects: list[TriggerEffect] = []
    for zone in sorted(engine.state.suppression.values(), key=lambda item: item.zone_id):
        if zone.remaining_capacity <= 0 or zone.owner_id not in engine.state.actors:
            continue
        owner = engine.state.actors[zone.owner_id]
        if owner.side is actor.side or owner.downed or cell not in zone.cells:
            continue
        effect_id = f"{zone.zone_id}:EXPOSURE:{actor_id}"
        effects.append(
            TriggerEffect(
                effect_id,
                owner.actor_id,
                owner.side,
                False,
                lambda state, zid=zone.zone_id, aid=actor_id: zid in state.suppression
                and state.suppression[zid].remaining_capacity > 0
                and not state.actors[aid].downed
                and grid.line_of_sight(state.actors[state.suppression[zid].owner_id].position, state.actors[aid].position, state.actors[aid].stance),
                lambda _state, z=zone, aid=actor_id: _resolve_committed_suppression_attack(engine, z, aid, grid, metrics) and None,
            )
        )
    for prepared in sorted(engine.state.prepared.values(), key=lambda item: item.prep_id):
        if not prepared.active or prepared.shots_remaining <= 0 or prepared.owner_id not in engine.state.actors:
            continue
        owner = engine.state.actors[prepared.owner_id]
        if owner.side is actor.side or owner.downed or cell not in prepared.zone_cells:
            continue
        effects.append(
            TriggerEffect(
                f"{prepared.prep_id}:MOVE:{actor_id}",
                owner.actor_id,
                owner.side,
                prepared.explicit_before,
                lambda state, pid=prepared.prep_id, aid=actor_id: pid in state.prepared
                and state.prepared[pid].active
                and state.prepared[pid].shots_remaining > 0
                and not state.actors[aid].downed
                and grid.line_of_sight(state.actors[state.prepared[pid].owner_id].position, state.actors[aid].position, state.actors[aid].stance),
                lambda _state, p=prepared, aid=actor_id: _resolve_overwatch_attack(engine, p, aid, grid, metrics) and None,
            )
        )
    return effects


def _arc_cells(origin: Position, toward: Position, grid: GridMap, arc_degrees: float, maximum_range: float) -> set[tuple[int, int]]:
    key = (grid._cache_signature(), origin.x, origin.y, toward.x, toward.y, arc_degrees, maximum_range)
    cached = _ARC_CACHE.get(key)
    if cached is not None:
        return set(cached)
    direction = math.atan2(toward.y - origin.y, toward.x - origin.x)
    cells: set[tuple[int, int]] = set()
    for x in range(grid.width):
        for y in range(grid.height):
            position = Position(x, y)
            distance = origin.distance_to(position)
            if distance == 0 or distance > maximum_range:
                continue
            angle = math.atan2(position.y - origin.y, position.x - origin.x)
            delta = abs((angle - direction + math.pi) % (2 * math.pi) - math.pi)
            if math.degrees(delta) <= arc_degrees / 2:
                cells.add((x, y))
    if len(_ARC_CACHE) >= _ARC_CACHE_LIMIT:
        _ARC_CACHE.clear()
    _ARC_CACHE[key] = frozenset(cells)
    return cells


def _suppression_cells(center: Position, grid: GridMap) -> set[tuple[int, int]]:
    return {
        (x, y)
        for x in range(max(0, round(center.x) - 3), min(grid.width, round(center.x) + 4))
        for y in range(max(0, round(center.y) - 3), min(grid.height, round(center.y) + 4))
        if Position(x, y).distance_to(center) <= 2.5
    }


def _advance_objective(state: SimulationState, seconds: float, metrics: dict[str, Any]) -> None:
    occupants = {
        side: [actor for actor in state.actors.values() if actor.side is side and not actor.downed and _in_control_zone(actor.position)]
        for side in Side
    }
    controlling = None
    if occupants[Side.A] and not occupants[Side.B]:
        controlling = Side.A
    elif occupants[Side.B] and not occupants[Side.A]:
        controlling = Side.B
    if controlling is None:
        state.objective["controller"] = None
        state.objective["hold_seconds"]["A"] = 0.0
        state.objective["hold_seconds"]["B"] = 0.0
        return
    key = controlling.value
    if state.objective["controller"] != key:
        state.objective["hold_seconds"][key] = 0.0
        state.objective["hold_seconds"][_other_side(controlling).value] = 0.0
    state.objective["controller"] = key
    state.objective["hold_seconds"][key] += seconds
    metrics["by_side"][key]["objective_seconds"] += seconds
    share = seconds / len(occupants[controlling])
    for actor in occupants[controlling]:
        metrics["by_actor"][actor.actor_id]["objective_seconds"] += share
    if state.objective["hold_seconds"][key] >= state.objective["required_hold_seconds"]:
        state.terminal = True
        state.terminal_reason = f"{key} held objective continuously for 12 seconds"


def _execute_decision(
    engine: EventEngine,
    grid: GridMap,
    policies: dict[str, str],
    actor_id: str,
    decision: PolicyDecision,
    metrics: dict[str, Any],
) -> None:
    state = engine.state
    actor = state.actors[actor_id]
    side = actor.side
    event_index = engine.event_count
    policy_id = policies[actor_id]
    choices = {"policy_id": policy_id, **asdict(decision)}

    def resolver(stages: list[dict[str, Any]], windows: list[dict[str, Any]], emitted: dict[str, Any]) -> str | None:
        before_ap = actor.ap
        previous_action = state.objective["last_action"][actor_id]
        stream = engine.rng.stream(f"event:{event_index}")
        if decision.action == "END":
            actor.ap = 0
            stages.append({"stage": "END TEAM PARTICIPATION", "reason": decision.reason})
        elif decision.action == "AIM":
            ordinary_cost = 1
            cost = ordinary_cost
            target_id = decision.target_id
            quick_solution = (
                target_id is not None
                and has_node(actor, SNAPSHOT_QUICK_SOLUTION)
                and target_id
                not in state.objective["attacked_targets_this_turn"][actor_id]
                and target_id
                not in state.objective["quick_solution_targets_this_turn"][actor_id]
            )
            if quick_solution:
                cost = max(
                    0,
                    ordinary_cost
                    - int(
                        engine.parameters.values[
                            "specialty.snapshot.quick_solution.ap_discount"
                        ]
                    ),
                )
            if actor.ap < cost:
                return "insufficient AP"
            actor.ap -= cost
            state.objective["aim_ap"][actor_id] = min(
                engine.parameters.values["fire.aim.max_ap_on_one_solution"],
                state.objective["aim_ap"][actor_id] + 1,
            )
            if quick_solution:
                state.objective["quick_solution_targets_this_turn"][actor_id].append(
                    target_id
                )
                effect = record_effect(
                    metrics,
                    actor_id=actor.actor_id,
                    node_id=SNAPSHOT_QUICK_SOLUTION,
                    action="AIM",
                    field="ap_cost",
                    before=ordinary_cost,
                    after=cost,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            stages.append({"stage": "AIM", "aim_ap": state.objective["aim_ap"][actor_id], "target": decision.target_id})
        elif decision.action == "RECOVER":
            if actor.ap < 1 or actor.weapon is None:
                return "insufficient AP or no weapon"
            recovered = recover_recoil(actor.weapon, 1, engine.parameters, rifleman=actor.class_id == "RIFLEMAN")
            actor.ap -= 1
            stages.append({"stage": "RECOIL RECOVERY", "recovered": recovered, "remaining": actor.weapon.current_recoil})
            emitted["recoil_recovered"] = recovered
        elif decision.action == "ESTABLISH_STRONGPOINT":
            if (
                not has_node(actor, ANCHOR_ESTABLISH_STRONGPOINT)
                or not _behind_cover(actor_id, state, grid)
                or actor_id in state.objective["strongpoints"]
            ):
                return "strongpoint requires the Entry, suitable cover, and no existing strongpoint"
            cost = int(
                engine.parameters.values[
                    "specialty.anchor.establish_strongpoint.action_ap"
                ]
            )
            if actor.ap < cost:
                return "insufficient AP"
            actor.ap -= cost
            state.objective["strongpoints"][actor_id] = asdict(actor.position)
            effect = record_effect(
                metrics,
                actor_id=actor.actor_id,
                node_id=ANCHOR_ESTABLISH_STRONGPOINT,
                action="ESTABLISH_STRONGPOINT",
                field="strongpoint_established",
                before=False,
                after=True,
            )
            stages.extend(
                (
                    {
                        "stage": "STRONGPOINT",
                        "position": asdict(actor.position),
                        "creates_cover": False,
                        "ap_spent": cost,
                    },
                    {"stage": "SPECIALTY EFFECT", "effects": [effect]},
                )
            )
        elif decision.action == "CYCLE":
            if actor.ap < 1 or actor.weapon is None or not actor.weapon.needs_cycle:
                return "cycle is not currently legal"
            ordinary_cost = 1
            cost = ordinary_cost
            if has_node(actor, OPERATOR_WEAPON_MANIPULATION):
                cost = max(
                    1,
                    ordinary_cost
                    - int(
                        engine.parameters.values[
                            "specialty.operator.weapon_manipulation.ap_discount"
                        ]
                    ),
                )
            if actor.ap < cost:
                return "insufficient AP"
            cycle_weapon(actor.weapon)
            actor.ap -= cost
            metrics["by_side"][_side_key(side)]["cycle_ap"] += cost
            stages.append({"stage": "CYCLE", "chamber": actor.weapon.chamber, "magazine_rounds": actor.weapon.magazine_rounds})
        elif decision.action == "RELOAD":
            if actor.weapon is None:
                return "no weapon"
            ordinary_cost = int(
                engine.parameters.values[
                    f"equipment.{actor.weapon.weapon_id}.reload_ap"
                ]
            )
            cost = ordinary_cost
            weapon_manipulation = has_node(
                actor, OPERATOR_WEAPON_MANIPULATION
            )
            if weapon_manipulation:
                cost = max(
                    1,
                    ordinary_cost
                    - int(
                        engine.parameters.values[
                            "specialty.operator.weapon_manipulation.ap_discount"
                        ]
                    ),
                )
            if actor.ap < cost or actor.weapon.reserve_rounds <= 0:
                return "reload prerequisites not met"
            loaded = reload_magazine(actor.weapon, actor.weapon.magazine_capacity)
            actor.ap -= cost
            metrics["by_side"][_side_key(side)]["reload_ap"] += cost
            stages.append({"stage": "RELOAD", "loaded": loaded, "retained_magazines": list(actor.weapon.retained_magazines)})
            if weapon_manipulation and cost != ordinary_cost:
                effect = record_effect(
                    metrics,
                    actor_id=actor.actor_id,
                    node_id=OPERATOR_WEAPON_MANIPULATION,
                    action="RELOAD",
                    field="ap_cost",
                    before=ordinary_cost,
                    after=cost,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
        elif decision.action in {"MOVE_OBJECTIVE", "MOVE_COVER", "FLANK"}:
            if actor.ap < 1:
                return "insufficient AP"
            observation = _policy_observation(actor_id, state, grid)
            if decision.action == "MOVE_COVER":
                goal = observation.metadata["cover_destination"]
            elif decision.action == "FLANK":
                goal = _flank_destination(actor_id, side)
            else:
                goal = decision.destination or _objective_goal(actor_id, side)
            if goal is None:
                return "no legal movement destination"
            nearest_enemy = min(
                (actor.position.distance_to(enemy.position) for enemy in state.actors.values() if enemy.side is not side and not enemy.downed),
                default=None,
            )
            rate = movement_rate(actor.stance, actor.burden, actor.class_id, nearest_enemy, engine.parameters)
            path = shortest_path(actor.position, goal, grid, rate)
            if not path:
                return "no reachable path within one AP"
            before = actor.position
            trigger_result = movement_with_triggers(
                engine,
                actor_id,
                path,
                lambda _state, moving_id: _movement_trigger_provider(engine, grid, metrics, moving_id),
            )
            travelled = 0.0
            exposed = 0.0
            previous = before
            for visited in trigger_result["visited"]:
                position = Position(visited["x"], visited["y"])
                step = previous.distance_to(position)
                travelled += step
                if any(
                    grid.line_of_sight(enemy.position, position, actor.stance)
                    for enemy in state.actors.values()
                    if enemy.side is not side and not enemy.downed
                ):
                    exposed += step
                previous = position
            actor.ap -= 1
            if actor_id in state.objective["strongpoints"]:
                del state.objective["strongpoints"][actor_id]
            state.objective["moved_this_turn"][actor_id] = True
            state.objective["aim_ap"][actor_id] = 0
            if decision.action == "FLANK" and actor.position.distance_to(goal) <= 1.5:
                state.objective["flank_complete"][actor_id] = True
            for zone_id in [zone_id for zone_id, zone in state.suppression.items() if zone.owner_id == actor_id]:
                del state.suppression[zone_id]
            metrics["by_side"][_side_key(side)]["movement_m"] += travelled
            metrics["by_side"][_side_key(side)]["exposure_m"] += exposed
            metrics["by_actor"][actor_id]["movement_m"] += travelled
            windows.extend(trigger_result["trigger_windows"])
            stages.append({"stage": "MOVEMENT", "from": asdict(before), "to": asdict(actor.position), "path": trigger_result["visited"], "distance_m": travelled, "exposure_m": exposed})
        elif decision.action == "FIRE":
            if actor.weapon is None or decision.target_id not in state.actors:
                return "no weapon or target"
            target = state.actors[decision.target_id]
            exposure = grid.exposure(actor.position, target.position, target.stance)
            attack_difficulty = actual_difficulty(engine.parameters.values["check.difficulty.standard"], exposure, engine.parameters)
            legitimate = grid.line_of_sight(actor.position, target.position, target.stance) and attack_difficulty is not None
            mode = decision.mode or "SEMI AUTO"
            requested = decision.rounds or 1
            estimated = engine.parameters.values["fire.semi_single.ap"] if mode in {"SEMI AUTO", "SINGLE"} else (
                engine.parameters.values["fire.burst_3.ap"] if mode == "BURST" else max(1, math.ceil(requested / engine.parameters.values["fire.full_auto.rounds_per_ap"]))
            )
            if actor.ap < estimated:
                actor.ap = 0
                stages.append({"stage": "END", "reason": "not enough AP for selected firing action"})
            elif not legitimate:
                return "no direct firing solution through current geometry"
            else:
                aim_ap = state.objective["aim_ap"][actor_id]
                result = fire_action(
                    actor.weapon,
                    mode,
                    requested,
                    stream.d20("ATTACK ROLL"),
                    actor.skill,
                    actor.aptitude,
                    attack_difficulty,
                    actor.position.distance_to(target.position),
                    exposure,
                    engine.parameters,
                    aim_ap=aim_ap,
                    sharpshooter=actor.class_id == "SHARPSHOOTER",
                )
                sustained_fire = (
                    has_node(actor, GUNNER_SUSTAINED_FIRE_DISCIPLINE)
                    and previous_action == "FIRE"
                    and state.objective["last_fire_weapon"][actor_id]
                    == actor.weapon.weapon_id
                    and result.recoil_before > 0
                    and result.recoil_after > result.recoil_before
                )
                if sustained_fire:
                    ordinary_recoil_after = result.recoil_after
                    recoil_after = max(
                        result.recoil_before,
                        ordinary_recoil_after
                        - int(
                            engine.parameters.values[
                                "specialty.gunner.sustained_fire_discipline.recoil_reduction"
                            ]
                        ),
                    )
                    if recoil_after != ordinary_recoil_after:
                        actor.weapon.current_recoil = recoil_after
                        result = replace(result, recoil_after=recoil_after)
                        effect = record_effect(
                            metrics,
                            actor_id=actor.actor_id,
                            node_id=GUNNER_SUSTAINED_FIRE_DISCIPLINE,
                            action="FIRE",
                            field="recoil_after",
                            before=ordinary_recoil_after,
                            after=recoil_after,
                        )
                        stages.append(
                            {"stage": "SPECIALTY EFFECT", "effects": [effect]}
                        )
                actor.ap -= result.ap_cost
                state.objective["aim_ap"][actor_id] = 0
                if target.actor_id not in state.objective["attacked_targets_this_turn"][actor_id]:
                    state.objective["attacked_targets_this_turn"][actor_id].append(
                        target.actor_id
                    )
                state.objective["last_fire_weapon"][actor_id] = actor.weapon.weapon_id
                side_metrics = metrics["by_side"][_side_key(side)]
                side_metrics["shots"] += 1
                side_metrics["rounds"] += result.rounds_fired
                side_metrics["connections"] += result.target_connections
                routed = _apply_connections(actor_id, target.actor_id, side, result, state, metrics, engine.parameters)
                stages.extend(
                    (
                        {"stage": "FIRING SOLUTION", "distance_m": actor.position.distance_to(target.position), "exposure": exposure, "aim_ap": aim_ap},
                        {"stage": "ATTACK ROLL", "result": result.to_dict()},
                        {"stage": "PROJECTILE / RES / HP", "connections": routed},
                    )
                )
                emitted.update({"rounds": result.rounds_fired, "connections": result.target_connections, "target_downed": target.downed})
        elif decision.action == "SUPPRESS":
            if actor.weapon is None or decision.target_id not in state.actors:
                return "no weapon or target"
            cost = engine.parameters.values["suppression.setup_ap"]
            if actor.ap < cost:
                actor.ap = 0
                stages.append({"stage": "END", "reason": "not enough AP to establish SUPPRESSION"})
            else:
                target = state.actors[decision.target_id]
                if not grid.line_of_sight(actor.position, target.position, target.stance):
                    return "SUPPRESSION requires a real firing line to the declared area"
                requested = engine.parameters.values["suppression.rounds_per_5m_zone"]
                if "FULL AUTO" in actor.weapon.fire_modes:
                    mode = "FULL AUTO"
                elif "3-round BURST" in actor.weapon.fire_modes:
                    mode = "BURST"
                else:
                    return "single-fire suppression commitment requires a multi-shot procedure not admitted by the source"
                commitment = fire_action(
                    actor.weapon,
                    mode,
                    requested,
                    stream.d20("SUPPRESSION COMMITMENT"),
                    actor.skill,
                    actor.aptitude,
                    engine.parameters.values["check.difficulty.standard"] + engine.parameters.values["suppression.attack_difficulty_modifier"],
                    actor.position.distance_to(target.position),
                    "none",
                    engine.parameters,
                    legitimate=False,
                )
                if commitment.rounds_fired <= 0:
                    return "no ammunition could be committed"
                actor.ap -= cost
                zone_id = f"SUPPRESS:{actor_id}:{event_index}"
                capacity = max(1, commitment.rounds_fired // engine.parameters.values["suppression.exposure_capacity_round_divisor"])
                state.suppression[zone_id] = SuppressionZone(
                    zone_id,
                    actor_id,
                    _suppression_cells(target.position, grid),
                    commitment.rounds_fired,
                    capacity,
                    capacity,
                    _other_side(side),
                )
                side_metrics = metrics["by_side"][_side_key(side)]
                side_metrics["shots"] += 1
                side_metrics["rounds"] += commitment.rounds_fired
                side_metrics["suppression_setups"] += 1
                stages.append({"stage": "SUPPRESSION COMMITMENT", "result": commitment.to_dict(), "zone_id": zone_id, "capacity": capacity, "cells": len(state.suppression[zone_id].cells)})
        elif decision.action == "OVERWATCH":
            if actor.weapon is None:
                return "no weapon"
            setup_cost = int(engine.parameters.values["overwatch.setup_ap"])
            ordinary_cost = setup_cost
            prepared_sightline = (
                has_node(actor, SENTINEL_PREPARED_SIGHTLINE)
                and not state.objective["moved_this_turn"][actor_id]
            )
            if prepared_sightline:
                ordinary_cost += 1
            cost = ordinary_cost
            if prepared_sightline:
                cost = max(
                    1,
                    ordinary_cost
                    - int(
                        engine.parameters.values[
                            "specialty.sentinel.prepared_sightline.ap_discount"
                        ]
                    ),
                )
            if actor.ap < cost:
                actor.ap = 0
                stages.append({"stage": "END", "reason": "not enough AP for OVERWATCH"})
            else:
                toward = decision.destination or _objective_goal(actor_id, side)
                arc = engine.parameters.values["overwatch.arc_degrees"]
                maximum_range = engine.parameters.values[f"equipment.{actor.weapon.weapon_id}.range_breakpoints_m"][1]
                prep_id = f"OW:{actor_id}:{event_index}"
                state.prepared[prep_id] = PreparedAction(
                    prep_id,
                    actor_id,
                    "OVERWATCH",
                    _arc_cells(actor.position, toward, grid, arc, maximum_range),
                    engine.parameters.values["overwatch.shot_allowance"],
                    False,
                    True,
                )
                if prepared_sightline:
                    state.objective["prepared_aim_ap"][prep_id] = 1
                    effect = record_effect(
                        metrics,
                        actor_id=actor.actor_id,
                        node_id=SENTINEL_PREPARED_SIGHTLINE,
                        action="OVERWATCH+AIM",
                        field="ap_cost",
                        before=ordinary_cost,
                        after=cost,
                    )
                    stages.append(
                        {"stage": "SPECIALTY EFFECT", "effects": [effect]}
                    )
                actor.ap -= cost
                metrics["by_side"][_side_key(side)]["overwatch_setups"] += 1
                stages.append({"stage": "OVERWATCH", "prep_id": prep_id, "arc_degrees": arc, "zone_cells": len(state.prepared[prep_id].zone_cells), "shots": state.prepared[prep_id].shots_remaining})
        elif decision.action == "CQC":
            if decision.target_id not in state.actors:
                return "no CQC target"
            target = state.actors[decision.target_id]
            cost = engine.parameters.values["cqc.action_ap"]
            if actor.ap < cost or actor.position.distance_to(target.position) > 1.5:
                return "CQC reach/AP prerequisite not met"
            result = resolve_cqc_maneuver("SHOVE", stream.d20("CQC CHECK"), actor.skill, actor.aptitude, target.skill, target.aptitude, engine.parameters)
            actor.ap -= cost
            metrics["by_side"][_side_key(side)]["cqc_attempts"] += 1
            if result.check.success:
                metrics["by_side"][_side_key(side)]["cqc_successes"] += 1
                dx = target.position.x - actor.position.x
                dy = target.position.y - actor.position.y
                magnitude = math.hypot(dx, dy) or 1.0
                goal = Position(round(target.position.x + dx / magnitude * engine.parameters.values["cqc.shove_distance_m"]), round(target.position.y + dy / magnitude * engine.parameters.values["cqc.shove_distance_m"]))
                path = shortest_path(target.position, goal, grid, engine.parameters.values["cqc.shove_distance_m"])
                if path:
                    target.position = path[-1]
            stages.append({"stage": "CQC", "maneuver": result.maneuver, "check": result.check.to_dict(), "declared_result": result.declared_result})
        else:
            return f"unsupported policy action {decision.action}"

        state.objective["last_action"][actor_id] = decision.action
        spent = max(0, before_ap - actor.ap)
        _add_action_ap(metrics, actor_id, side, decision.action, spent)
        elapsed = spent * engine.parameters.values["fixture.ap_seconds"]
        state.elapsed_seconds += elapsed
        if _behind_cover(actor_id, state, grid):
            metrics["by_side"][_side_key(side)]["cover_seconds"] += elapsed
        if any(
            grid.line_of_sight(enemy.position, actor.position, actor.stance)
            for enemy in state.actors.values()
            if enemy.side is not side and not enemy.downed
        ):
            metrics["by_side"][_side_key(side)]["exposed_action_seconds"] += elapsed
        _advance_objective(state, elapsed, metrics)
        emitted["ap_spent"] = spent
        return None

    entry = engine.record(actor_id, decision.action, choices, resolver)
    if entry.rejected_reason:
        metrics["illegal_actions"] += 1
        metrics["unsupported_events"].append({"actor_id": actor_id, "action": decision.action, "reason": entry.rejected_reason})
        actor.ap = 0


def _expire_owner_preparations(state: SimulationState, side: Side) -> None:
    for zone_id in [zone_id for zone_id, zone in state.suppression.items() if state.actors[zone.owner_id].side is side]:
        del state.suppression[zone_id]
    for prep_id in [prep_id for prep_id, prep in state.prepared.items() if state.actors[prep.owner_id].side is side]:
        del state.prepared[prep_id]


def _winner_from_state(state: SimulationState) -> str | None:
    if state.terminal_reason and "held objective" in state.terminal_reason:
        return state.terminal_reason[0]
    alive = {side: len(_team_actor_ids(state, side, capable_only=True)) for side in Side}
    if alive[Side.A] == 0 and alive[Side.B] > 0:
        return "B"
    if alive[Side.B] == 0 and alive[Side.A] > 0:
        return "A"
    hold = state.objective["hold_seconds"]
    if hold["A"] != hold["B"]:
        return "A" if hold["A"] > hold["B"] else "B"
    remaining = {
        side.value: sum(actor.hp + actor.res for actor in state.actors.values() if actor.side is side)
        for side in Side
    }
    if remaining["A"] != remaining["B"]:
        return "A" if remaining["A"] > remaining["B"] else "B"
    return None


def run_team_scenario(spec: TeamScenarioSpec, parameters: ParameterSet, seed: int, *, capture_events: bool = False) -> dict[str, Any]:
    if spec.scenario_id != "TEAM-CONTROL-4V4-V1":
        from .objective_tactical import run_objective_team_scenario

        return run_objective_team_scenario(spec, parameters, seed, capture_events=capture_events)
    state, grid, policies = _build_control_state(spec, parameters)
    initial_state = {
        "active_side": state.active_side.value,
        "actors": {
            actor_id: {
                "fixture_id": actor.fixture_id,
                "side": actor.side.value,
                "level": actor.level,
                "class_id": actor.class_id,
                "specialty_nodes": sorted(actor.specialty_nodes),
                "equipment": actor.weapon.weapon_id if actor.weapon else None,
                "hp": actor.hp,
                "res": actor.res,
                "ap": actor.ap,
                "ammunition": actor.weapon.total_rounds() if actor.weapon else 0,
                "stance": actor.stance.value,
                "position": asdict(actor.position),
                "target_knowledge": {target_id: knowledge.value for target_id, knowledge in sorted(actor.target_knowledge.items())},
                "policy_id": policies[actor_id],
            }
            for actor_id, actor in sorted(state.actors.items())
        },
        "objective": {"kind": "CONTROL", "zone": dict(state.objective["zone"]), "required_hold_seconds": state.objective["required_hold_seconds"]},
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
    engine = EventEngine(state, parameters, seed, capture_events)
    metrics = _blank_metrics(state)
    for actor_id, policy_id in policies.items():
        metrics["by_actor"][actor_id]["policy_id"] = policy_id
    side = spec.opening_side
    for team_turn_index in range(spec.max_team_turns):
        state.active_side = side
        state.objective["team_turn_index"] = team_turn_index
        _expire_owner_preparations(state, side)
        for actor_id in _team_actor_ids(state, side, capable_only=True):
            state.actors[actor_id].ap = state.actors[actor_id].max_ap
            state.objective["attacked_targets_this_turn"][actor_id] = []
            state.objective["quick_solution_targets_this_turn"][actor_id] = []
            state.objective["moved_this_turn"][actor_id] = False
        action_guard = 0
        while not state.terminal:
            capable = [actor_id for actor_id in _team_actor_ids(state, side, capable_only=True) if state.actors[actor_id].ap > 0]
            if not capable:
                break
            made_progress = False
            for actor_id in capable:
                actor = state.actors[actor_id]
                if actor.ap <= 0 or actor.downed or state.terminal:
                    continue
                if _execute_control_entry_fixture(
                    engine,
                    grid,
                    actor_id,
                    metrics,
                ):
                    made_progress = True
                    action_guard += 1
                    continue
                _refresh_actor_knowledge(actor_id, state, grid)
                observation = _policy_observation(actor_id, state, grid)
                decision = choose_action(policies[actor_id], observation)
                before_ap = actor.ap
                before_events = engine.event_count
                _execute_decision(engine, grid, policies, actor_id, decision, metrics)
                made_progress = (
                    made_progress
                    or actor.ap < before_ap
                    or engine.event_count > before_events
                )
                action_guard += 1
                if action_guard > len(capable) * actor.max_ap * 6:
                    state.terminal = True
                    state.terminal_reason = "infinite-loop guard"
                    metrics["invariant_failures"].append("infinite-loop guard")
                    break
            if not made_progress and not state.terminal:
                state.terminal = True
                state.terminal_reason = "deadlock: no legal AP progress"
                metrics["invariant_failures"].append("deadlock: no legal AP progress")
        alive = {candidate: len(_team_actor_ids(state, candidate, capable_only=True)) for candidate in Side}
        if not state.terminal and (alive[Side.A] == 0 or alive[Side.B] == 0):
            state.terminal = True
            state.terminal_reason = "one side eliminated"
        if state.terminal:
            break
        side = _other_side(side)
    if not state.terminal:
        state.terminal = True
        state.terminal_reason = "max team turns"
    winner = _winner_from_state(state)
    for side_value in ("A", "B"):
        side_actors = [actor for actor in state.actors.values() if actor.side.value == side_value]
        metrics["by_side"][side_value]["actors_downed"] = sum(actor.downed for actor in side_actors)
        metrics["by_side"][side_value]["actors_dead"] = 0
        metrics["by_side"][side_value]["remaining_hp"] = sum(actor.hp for actor in side_actors)
        metrics["by_side"][side_value]["remaining_res"] = sum(actor.res for actor in side_actors)
        metrics["by_side"][side_value]["ammunition_remaining"] = sum(actor.weapon.total_rounds() for actor in side_actors if actor.weapon)
        metrics["by_side"][side_value]["actors_in_cover"] = sum(_behind_cover(actor.actor_id, state, grid) for actor in side_actors if not actor.downed)
        metrics["by_side"][side_value]["active_preparations"] = sum(
            state.actors[prep.owner_id].side.value == side_value and prep.active for prep in state.prepared.values()
        )
        metrics["by_side"][side_value]["active_suppression_zones"] = sum(
            state.actors[zone.owner_id].side.value == side_value for zone in state.suppression.values()
        )
        knowledge_counts = {knowledge.value: 0 for knowledge in KnowledgeState}
        for actor in side_actors:
            for knowledge in actor.target_knowledge.values():
                knowledge_counts[knowledge.value] += 1
        metrics["by_side"][side_value]["knowledge_state_counts"] = knowledge_counts
        metrics["by_side"][side_value]["objective_hold_seconds"] = state.objective["hold_seconds"][side_value]
    result = {
        "scenario_id": spec.scenario_id,
        "scenario_version": spec.scenario_version,
        "map_id": grid.map_id,
        "fixture_ids": [state.actors[actor_id].fixture_id for actor_id in sorted(state.actors)],
        "builds": {actor_id: {"class_id": actor.class_id, "level": actor.level, "specialty_nodes": sorted(actor.specialty_nodes)} for actor_id, actor in sorted(state.actors.items())},
        "equipment": {actor_id: actor.weapon.weapon_id if actor.weapon else None for actor_id, actor in sorted(state.actors.items())},
        "policies": policies,
        "opening_side": spec.opening_side.value,
        "initial_state": initial_state,
        "map_geometry": map_geometry,
        "seed": seed,
        "parameter_set_hash": parameters.hash,
        "winner_side": winner,
        "terminal_reason": state.terminal_reason,
        "elapsed_seconds": state.elapsed_seconds,
        "event_count": engine.event_count,
        "event_log_hash": engine.log_hash() if capture_events else "NOT_CAPTURED",
        "final_state_hash": state.semantic_hash() if capture_events else "NOT_CAPTURED",
        "metrics": metrics,
        "objective": state.objective,
        "final_actors": {actor_id: {"hp": actor.hp, "res": actor.res, "ap": actor.ap, "downed": actor.downed, "position": asdict(actor.position), "stance": actor.stance.value, "ammunition": actor.weapon.total_rounds() if actor.weapon else 0} for actor_id, actor in sorted(state.actors.items())},
        "rng": engine.rng.provenance(),
        "replay_spec": {"kind": "team", "spec": {**asdict(spec), "opening_side": spec.opening_side.value}, "seed": seed, "parameter_values": parameters.values},
    }
    if capture_events:
        result["events"] = [entry.to_dict() for entry in engine.log]
    return result


def _numeric_leaves(
    value: dict[str, Any],
    prefix: str = "",
) -> dict[str, float]:
    leaves: dict[str, float] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            leaves.update(_numeric_leaves(child, path))
        elif isinstance(child, (int, float)) and not isinstance(child, bool):
            leaves[path] = float(child)
    return leaves


def team_pair_chunk(
    spec: TeamScenarioSpec,
    parameters: ParameterSet,
    master_seed: int,
    pair_start: int,
    pairs: int,
    *,
    capture_replay: bool = True,
) -> dict[str, Any]:
    if pairs <= 0:
        raise ValueError("pairs must be positive")
    if pair_start < 0:
        raise ValueError("pair start must be non-negative")
    subject_outcomes = 0.0
    draws = 0
    terminal_times: list[float] = []
    objective_effects: list[float] = []
    damage_effects: list[float] = []
    sample_replay = None
    failures: list[dict[str, Any]] = []
    subject_metric_values: dict[str, list[float]] = defaultdict(list)
    control_metric_values: dict[str, list[float]] = defaultdict(list)
    paired_metric_effects: dict[str, list[float]] = defaultdict(list)

    for pair_index in range(pair_start, pair_start + pairs):
        seed = derive_seed(master_seed, f"{spec.scenario_id}:paired-mirror", pair_index)
        first = run_team_scenario(spec, parameters, seed, capture_events=capture_replay and pair_index == 0)
        second = run_team_scenario(spec.mirrored(), parameters, seed, capture_events=False)
        if pair_index == 0 and capture_replay:
            sample_replay = first
        first_subject_metrics = _numeric_leaves(first["metrics"]["by_side"]["A"])
        first_control_metrics = _numeric_leaves(first["metrics"]["by_side"]["B"])
        second_subject_metrics = _numeric_leaves(second["metrics"]["by_side"]["B"])
        second_control_metrics = _numeric_leaves(second["metrics"]["by_side"]["A"])
        for metric in set(first_subject_metrics) | set(first_control_metrics) | set(second_subject_metrics) | set(second_control_metrics):
            first_subject = first_subject_metrics.get(metric, 0.0)
            first_control = first_control_metrics.get(metric, 0.0)
            second_subject = second_subject_metrics.get(metric, 0.0)
            second_control = second_control_metrics.get(metric, 0.0)
            subject_metric_values[metric].extend((first_subject, second_subject))
            control_metric_values[metric].extend((first_control, second_control))
            paired_metric_effects[metric].append(((first_subject - first_control) + (second_subject - second_control)) / 2)
        for result, subject_side in ((first, "A"), (second, "B")):
            if result["winner_side"] is None:
                subject_outcomes += 0.5
                draws += 1
            elif result["winner_side"] == subject_side:
                subject_outcomes += 1
            terminal_times.append(result["elapsed_seconds"])
            if result["metrics"]["invariant_failures"]:
                failures.append({"seed": seed, "subject_side": subject_side, "failures": result["metrics"]["invariant_failures"], "replay": result.get("replay_spec")})
        objective_effects.append(
            (
                first["metrics"]["by_side"]["A"]["objective_seconds"]
                + second["metrics"]["by_side"]["B"]["objective_seconds"]
                - first["metrics"]["by_side"]["B"]["objective_seconds"]
                - second["metrics"]["by_side"]["A"]["objective_seconds"]
            )
            / 2
        )
        damage_effects.append(
            (
                first["metrics"]["by_side"]["A"]["hp_damage"]
                + second["metrics"]["by_side"]["B"]["hp_damage"]
                - first["metrics"]["by_side"]["B"]["hp_damage"]
                - second["metrics"]["by_side"]["A"]["hp_damage"]
            )
            / 2
        )
    return {
        "pair_start": pair_start,
        "pairs": pairs,
        "subject_outcomes": subject_outcomes,
        "draws": draws,
        "terminal_times": terminal_times,
        "objective_effects": objective_effects,
        "damage_effects": damage_effects,
        "sample_replay": sample_replay,
        "failures": failures,
        "subject_metric_values": dict(subject_metric_values),
        "control_metric_values": dict(control_metric_values),
        "paired_metric_effects": dict(paired_metric_effects),
    }


def merge_team_pair_chunks(
    spec: TeamScenarioSpec,
    parameters: ParameterSet,
    master_seed: int,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(chunks, key=lambda item: item["pair_start"])
    if not ordered or ordered[0]["pair_start"] != 0:
        raise ValueError("team pair chunks must begin at pair zero")
    expected_start = 0
    for chunk in ordered:
        if chunk["pair_start"] != expected_start:
            raise ValueError("team pair chunks must be contiguous and non-overlapping")
        expected_start += chunk["pairs"]
    pairs = expected_start
    subject_outcomes = sum(chunk["subject_outcomes"] for chunk in ordered)
    draws = sum(chunk["draws"] for chunk in ordered)
    terminal_times = [value for chunk in ordered for value in chunk["terminal_times"]]
    objective_effects = [value for chunk in ordered for value in chunk["objective_effects"]]
    damage_effects = [value for chunk in ordered for value in chunk["damage_effects"]]
    failures = [value for chunk in ordered for value in chunk["failures"]]
    sample_replay = next(
        (chunk["sample_replay"] for chunk in ordered if chunk["sample_replay"]),
        None,
    )
    metric_ids = sorted(
        {
            metric
            for chunk in ordered
            for metric in chunk["paired_metric_effects"]
        }
    )
    subject_metric_values = {
        metric: [
            value
            for chunk in ordered
            for value in chunk["subject_metric_values"].get(metric, [])
        ]
        for metric in metric_ids
    }
    control_metric_values = {
        metric: [
            value
            for chunk in ordered
            for value in chunk["control_metric_values"].get(metric, [])
        ]
        for metric in metric_ids
    }
    paired_metric_effects = {
        metric: [
            value
            for chunk in ordered
            for value in chunk["paired_metric_effects"].get(metric, [])
        ]
        for metric in metric_ids
    }
    total = pairs * 2
    interval = wilson_interval(subject_outcomes, total)
    neutral = abs(interval.estimate - 0.5) <= 0.02 or (interval.low <= 0.5 <= interval.high and interval.high - interval.low <= 0.04)
    return {
        "scenario_id": spec.scenario_id,
        "scenario_version": spec.scenario_version,
        "map_id": {
            "TEAM-CONTROL-4V4-V1": "MAP_COMPOUND_80",
            "TEAM-EXTRACT-4V4-V1": "MAP_COMPOUND_80",
            "TEAM-BREACH-4V4-V1": "MAP_BREACH_30",
            "TEAM-RECON-HACK-4V4-V1": "MAP_NETWORK_SITE_40",
        }[spec.scenario_id],
        "parameter_set_hash": parameters.hash,
        "master_seed": master_seed,
        "derived_seed_scheme": "sha256(master, scenario_id, paired-mirror, pair_index)",
        "paired_mirrored_runs": pairs,
        "total_trials": total,
        "qualification": "FINAL" if pairs >= 50000 else ("ORDINARY" if pairs >= 10000 else "SMOKE_NON_QUALIFYING"),
        "subject_outcome_share": interval.to_dict(),
        "draws": draws,
        "mirror_neutral": neutral if spec.classes_a == spec.classes_b and spec.policies_a == spec.policies_b and spec.weapons_a == spec.weapons_b else None,
        "terminal_time_seconds": mean_interval(terminal_times).to_dict(),
        "paired_objective_seconds_effect": mean_interval(objective_effects).to_dict(),
        "paired_hp_damage_effect": mean_interval(damage_effects).to_dict(),
        "aggregate_metrics": {
            metric: {
                "subject": mean_interval(subject_metric_values[metric]).to_dict(),
                "control": mean_interval(control_metric_values[metric]).to_dict(),
                "paired_effect": mean_interval(paired_metric_effects[metric]).to_dict(),
            }
            for metric in sorted(paired_metric_effects)
        },
        "sample_replay": sample_replay,
        "failures": failures,
        "findings": (
            ([{"code": "MIRROR BIAS", "severity": "FAIL", "outcome_share": interval.to_dict()}] if not neutral and spec.classes_a == spec.classes_b and spec.policies_a == spec.policies_b else [])
            + ([{"code": "TACTICAL RUNTIME FAILURE", "severity": "FAIL", "count": len(failures), "first": failures[0]}] if failures else [])
        ),
    }


def paired_mirrored_team_scenarios(
    spec: TeamScenarioSpec,
    parameters: ParameterSet,
    master_seed: int,
    pairs: int,
    *,
    capture_replay: bool = True,
) -> dict[str, Any]:
    chunk = team_pair_chunk(
        spec,
        parameters,
        master_seed,
        0,
        pairs,
        capture_replay=capture_replay,
    )
    return merge_team_pair_chunks(spec, parameters, master_seed, [chunk])


def unsupported_mixed_team_findings() -> list[dict[str, Any]]:
    return []
