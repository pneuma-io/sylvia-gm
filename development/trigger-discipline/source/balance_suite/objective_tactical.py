from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, TYPE_CHECKING

from .engine import EventEngine
from .fixtures import benchmark_actor, build_map, network_site_nodes
from .geometry import actual_difficulty, cover_damage, shortest_path
from .model import ConditionState, KnowledgeState, LifeThreateningCondition, Position, Side, SimulationState, SuppressionZone
from .parameters import ParameterSet
from .rules import apply_damage, attack_values_for_weapon, cycle_weapon, fire_action, movement_rate, resolve_cqc_maneuver
from .specialty_runtime import (
    AUTODRONES_TACTICAL_AUTONOMY,
    BREAKER_TACTICAL_BREACHING,
    DIPLOMAT_NEGOTIATING_POSITION,
    ENFORCER_WEAPON_RETENTION,
    FIELD_ENGINEER_FIELD_WORK,
    FIELD_MEDIC_COMBAT_MEDICINE,
    FPV_DRONES_MANUAL_PILOT,
    LOCAL_HACKER_SERVICE_ACCESS,
    HANDLER_WORK_A_CONTACT,
    ICON_RECOGNITION,
    NETWORKER_MAP_THE_LINKS,
    RESUSCITATOR_RESUSCITATION,
    SHOCK_ASSAULT_MOMENTUM,
    TRAUMA_TRAUMA_CARE,
    has_node,
    record_effect,
)
from .support import (
    casualty_movement,
    intrusion_action,
    medical_action,
    repairable_forearm_injury,
    resolve_social_attempt,
)

if TYPE_CHECKING:
    from .tactical import TeamScenarioSpec


MAP_BY_SCENARIO = {
    "TEAM-EXTRACT-4V4-V1": "MAP_COMPOUND_80",
    "TEAM-BREACH-4V4-V1": "MAP_BREACH_30",
    "TEAM-RECON-HACK-4V4-V1": "MAP_NETWORK_SITE_40",
    "TEAM-SOCIAL-4V4-V1": "MAP_OPEN_100",
}


# Objective scenarios deliberately use authored encounter placements rather than
# the generic map-edge spawn rectangles.  Keeping the complete fixture here makes
# those choices part of replayable repository data instead of hidden runner state.
OBJECTIVE_SPAWNS = {
    "TEAM-EXTRACT-4V4-V1": {
        "attacker": (Position(34, 23), Position(34, 25), Position(34, 27), Position(35, 25)),
        "defender": (Position(19, 20), Position(19, 24), Position(19, 28), Position(19, 32)),
    },
    "TEAM-BREACH-4V4-V1": {
        "attacker": (Position(6, 8), Position(6, 10), Position(6, 12), Position(5, 10)),
        "defender": (Position(24, 6), Position(24, 9), Position(24, 12), Position(26, 10)),
    },
    "TEAM-RECON-HACK-4V4-V1": {
        "attacker": (Position(5, 12), Position(5, 15), Position(5, 18), Position(6, 15)),
        "defender": (Position(34, 9), Position(34, 13), Position(34, 17), Position(34, 21)),
    },
    "TEAM-SOCIAL-4V4-V1": {
        "attacker": (Position(40, 25), Position(38, 28), Position(38, 22), Position(36, 25)),
        "defender": (Position(45, 25), Position(47, 28), Position(47, 22), Position(49, 25)),
    },
}


def _other(side: Side) -> Side:
    return Side.B if side is Side.A else Side.A


def _side_metrics() -> dict[str, Any]:
    return {
        "ap_by_action": {},
        "movement_m": 0.0,
        "exposure_m": 0.0,
        "shots": 0,
        "rounds": 0,
        "connections": 0,
        "res_damage": 0,
        "hp_damage": 0,
        "cover_damage": 0,
        "medical_actions": 0,
        "hacking_actions": 0,
        "deployment_actions": 0,
        "drone_actions": 0,
        "engineering_actions": 0,
        "cqc_attempts": 0,
        "cqc_successes": 0,
        "objective_seconds": 0.0,
        "objective_actions": 0,
        "suppression_setups": 0,
        "social_actions": 0,
        "social_setup_ap": 0,
        "social_leverage_steps": 0,
        "counteroffers_presented": 0,
        "injury_recovery_steps": 0,
        "resuscitation_progress_ap": 0,
        "foregone_fire_ap": 0,
    }


def _add_ap(metrics: dict[str, Any], actor_id: str, action: str, amount: int) -> None:
    if amount <= 0:
        return
    actor = metrics["by_actor"][actor_id]
    side = actor["side"]
    table = metrics["by_side"][side]["ap_by_action"]
    table[action] = table.get(action, 0) + amount
    actor["ap_spent"] += amount
    actor["ap_by_action"][action] = actor["ap_by_action"].get(action, 0) + amount


def _build_state(spec: "TeamScenarioSpec", parameters: ParameterSet) -> tuple[SimulationState, Any, dict[str, str], Side]:
    attacker = spec.opening_side
    defender = _other(attacker)
    map_id = MAP_BY_SCENARIO[spec.scenario_id]
    grid = build_map(map_id, parameters)
    policies: dict[str, str] = {}
    actors = {}

    placement = OBJECTIVE_SPAWNS[spec.scenario_id]
    attacker_positions = placement["attacker"]
    defender_positions = placement["defender"]

    for side, classes, policy_ids, weapons, specialty_nodes, initial_magazine_rounds, positions in (
        (Side.A, spec.classes_a, spec.policies_a, spec.weapons_a, spec.specialty_nodes_a, spec.initial_magazine_rounds_a, attacker_positions if attacker is Side.A else defender_positions),
        (Side.B, spec.classes_b, spec.policies_b, spec.weapons_b, spec.specialty_nodes_b, spec.initial_magazine_rounds_b, attacker_positions if attacker is Side.B else defender_positions),
    ):
        for index, (class_id, policy_id, weapon_id, actor_specialty_nodes, initial_magazine_rounds, position) in enumerate(zip(classes, policy_ids, weapons, specialty_nodes, initial_magazine_rounds, positions, strict=True), start=1):
            actor = benchmark_actor(class_id, spec.level, side, position, parameters, actor_suffix=str(index), weapon_id=weapon_id)
            actor.specialty_nodes.update(actor_specialty_nodes)
            if initial_magazine_rounds is not None and actor.weapon is not None:
                actor.weapon.magazine_rounds = min(
                    actor.weapon.magazine_capacity,
                    max(0, initial_magazine_rounds),
                )
            actors[actor.actor_id] = actor
            policies[actor.actor_id] = policy_id

    state = SimulationState(spec.scenario_id, spec.scenario_version, actors, covers=grid.covers, active_side=attacker)
    if spec.scenario_id == "TEAM-EXTRACT-4V4-V1":
        casualty = next(
            (actor for actor in reversed(list(actors.values())) if actor.side is attacker and actor.class_id != "CORPSMAN"),
            next(actor for actor in actors.values() if actor.side is attacker),
        )
        casualty.position = Position(35, 25)
        casualty.downed = True
        casualty.hp = 0
        casualty.conditions = [LifeThreateningCondition("EXTRACT-BLEED", "hemorrhage", ConditionState.ACTIVE, 18, "hemorrhage control", 36)]
        medical_fixture: dict[str, Any] | None = None
        if spec.objective_fixture_id == "SPECIALTY-RESUSCITATOR-ENTRY":
            casualty.conditions = [
                LifeThreateningCondition(
                    "EXTRACT-CIRCULATORY-FAILURE",
                    "reversible circulatory arrest",
                    ConditionState.FAILED,
                    1,
                    "continuous circulation-restoring intervention",
                    2,
                )
            ]
            medical_fixture = {
                "kind": "RESUSCITATION",
                "condition_id": "EXTRACT-CIRCULATORY-FAILURE",
                "capable_intervention": True,
                "equipment_available": True,
                "progress_ap": 0,
                "started_before_expiry": False,
                "window_held": False,
                "treatment_complete": False,
            }
        elif spec.objective_fixture_id == "SPECIALTY-TRAUMA-ENTRY":
            casualty.conditions = [
                LifeThreateningCondition(
                    "EXTRACT-CONTROLLED-HEMORRHAGE",
                    "controlled hemorrhage",
                    ConditionState.STABILIZED,
                    18,
                    "hemorrhage control",
                    36,
                )
            ]
            casualty.hp = max(1, casualty.max_hp - 4)
            medical_fixture = {
                "kind": "TRAUMA",
                "injury_id": "EXTRACT-TREATABLE-SERIOUS-WOUND",
                "serious": True,
                "medically_treatable": True,
                "equipment_available": True,
                "working_conditions_appropriate": True,
                "recovery_steps": 0,
                "treatment_complete": False,
            }
        elif spec.objective_fixture_id == "SPECIALTY-FIELD_MEDIC-ENTRY":
            injury = repairable_forearm_injury(parameters)
            casualty.position = Position(34, 24)
            casualty.downed = False
            casualty.hp = max(1, casualty.max_hp - 4)
            casualty.res = max(0, casualty.max_res - 2)
            casualty.conditions = [
                LifeThreateningCondition(
                    "EXTRACT-CONTROLLED-FOREARM-TRAUMA",
                    "controlled repairable forearm trauma",
                    ConditionState.STABILIZED,
                    18,
                    "battlefield forearm treatment",
                    36,
                )
            ]
            medical_fixture = {
                "kind": "COMBAT_MEDICINE",
                "injury": asdict(injury),
                "medically_plausible_immediate_function": True,
                "equipment_available": True,
                "treated_injury_ids": [],
                "treatment_complete": False,
            }
        state.objective = {
            "kind": "EXTRACT",
            "attacker_side": attacker.value,
            "casualty_id": casualty.actor_id,
            "extraction": asdict(Position(15, 25)),
            "distance_m": 20.0,
            "stabilized": False,
            "fixture_id": spec.objective_fixture_id,
            "medical_fixture": medical_fixture,
            "carrier_id": None,
            "lifted": False,
            "complete": False,
            "team_turn_index": 0,
            "policy_state": {"flank_complete": []},
            "assault_momentum": {},
        }
    elif spec.scenario_id == "TEAM-BREACH-4V4-V1":
        retention_fixture: dict[str, Any] | None = None
        shock_fixture: dict[str, Any] | None = None
        if spec.objective_fixture_id == "SPECIALTY-ENFORCER-ENTRY":
            enforcer = next(
                actor
                for actor in actors.values()
                if actor.side is attacker and actor.class_id == "BREACHER"
            )
            opponent = next(
                actor for actor in actors.values() if actor.side is defender
            )
            enforcer.position = Position(21, 10)
            opponent.position = Position(22, 10)
            retention_fixture = {
                "kind": "WEAPON_RETENTION",
                "subject_id": enforcer.actor_id,
                "opponent_id": opponent.actor_id,
                "held_weapon_id": (
                    enforcer.weapon.weapon_id if enforcer.weapon else None
                ),
                "ground_weapon": None,
                "complete": False,
            }
        elif spec.objective_fixture_id == "SPECIALTY-SHOCK-ENTRY":
            shock = next(
                actor
                for actor in actors.values()
                if actor.side is attacker and actor.class_id == "BREACHER"
            )
            pressured = next(
                actor for actor in actors.values() if actor.side is defender
            )
            shock.position = Position(17, 10)
            pressured.position = Position(24, 10)
            shock_fixture = {
                "kind": "ASSAULT_MOMENTUM",
                "subject_id": shock.actor_id,
                "target_id": pressured.actor_id,
                "phase": "SUPPRESSION",
                "complete": False,
            }
        state.objective = {
            "kind": "BREACH",
            "attacker_side": attacker.value,
            "door_id": "BREACH_DOOR",
            "objective": asdict(Position(24, 10)),
            "hold_seconds": 0.0,
            "required_hold_seconds": 8.0,
            "debris_crossed": [],
            "fixture_id": spec.objective_fixture_id,
            "retention_fixture": retention_fixture,
            "shock_fixture": shock_fixture,
            "complete": False,
            "team_turn_index": 0,
            "policy_state": {"flank_complete": []},
            "assault_momentum": {},
        }
    elif spec.scenario_id == "TEAM-SOCIAL-4V4-V1":
        state.objective = {
            "kind": "SOCIAL",
            "attacker_side": attacker.value,
            "fixture_id": spec.objective_fixture_id,
            "counterpart_id": next(
                actor.actor_id
                for actor in actors.values()
                if actor.side is defender and actor.class_id == "INFLUENCER"
            ),
            "interaction_position": asdict(Position(45, 25)),
            "plausible_request": True,
            "counterpart_has_authority": True,
            "counterpart_capable": True,
            "reputation_recognized": True,
            "reputation_relevant": True,
            "negotiation_open": True,
            "counteroffers_remaining": 0,
            "social_complete": False,
            "complete": False,
            "team_turn_index": 0,
            "policy_state": {"flank_complete": []},
            "assault_momentum": {},
        }
    else:
        state.network = network_site_nodes()
        technical_fixture: dict[str, Any] | None = None
        if spec.objective_fixture_id == "SPECIALTY-AUTODRONES-ENTRY":
            technical_fixture = {
                "kind": "AUTONOMOUS_DRONE",
                "device_id": "OBJECTIVE-AUTODRONE-1",
                "position": asdict(Position(5, 15)),
                "durability": int(
                    parameters.values["equipment.autodrone.durability"]
                ),
                "movement_m_per_ap": float(
                    parameters.values["equipment.autodrone.move_m_per_ap"]
                ),
                "actions_per_turn": int(
                    parameters.values["equipment.autodrone.actions_per_turn"]
                ),
                "signal_available": True,
                "standing_order": None,
                "operation_complete": False,
            }
        elif spec.objective_fixture_id == "SPECIALTY-FPV_DRONES-ENTRY":
            technical_fixture = {
                "kind": "FPV_DRONE",
                "device_id": "OBJECTIVE-FPV-1",
                "position": asdict(Position(5, 15)),
                "destination": asdict(Position(18, 15)),
                "durability": int(
                    parameters.values["equipment.fpv_drone.durability"]
                ),
                "battery_s": float(
                    parameters.values["equipment.fpv_drone.battery_s"]
                ),
                "signal_available": True,
                "direct_control": False,
                "operation_complete": False,
            }
        elif spec.objective_fixture_id == "SPECIALTY-FIELD_ENGINEER-ENTRY":
            technical_fixture = {
                "kind": "FIELD_RECONFIGURATION",
                "device_id": "FIELD-SENSOR-RELAY-NODE-1",
                "already_deployed": True,
                "ordinary_deployment": False,
                "tools_available": True,
                "physical_access": True,
                "compatible_equipment": True,
                "configuration": {"sensor_orientation": "NORTH"},
                "declared_configuration": {"sensor_orientation": "EAST"},
                "capabilities": {
                    "sensor_capability": "UNCHANGED",
                    "range": "UNCHANGED",
                    "power": "UNCHANGED",
                    "network_privilege": "UNCHANGED",
                },
                "operation_complete": False,
            }
        state.objective = {
            "kind": "RECON_HACK",
            "attacker_side": attacker.value,
            "physical_objective": asdict(Position(36, 15)),
            "knowledge": KnowledgeState.UNAWARE.value,
            "hack_step": 0,
            "hack_sequence": ["GET_IN:NET40-A", "PIVOT:NET40-B", "PIVOT:NET40-C", "ESCALATE:NET40-C", "OPERATE:NET40-C"],
            "network_complete": False,
            "fixture_id": spec.objective_fixture_id,
            "technical_fixture": technical_fixture,
            "complete": False,
            "team_turn_index": 0,
            "policy_state": {"flank_complete": []},
            "assault_momentum": {},
        }
    return state, grid, policies, attacker


def _record_move(
    engine: EventEngine,
    grid: Any,
    actor: Any,
    goal: Position,
    metrics: dict[str, Any],
    *,
    rate_multiplier: float = 1.0,
    casualty: Any | None = None,
    known_enemy: Position | None = None,
    policy_id: str | None = None,
    ap_cost: int = 1,
    specialty_node_id: str | None = None,
) -> bool:
    if ap_cost < 0:
        raise ValueError("movement AP cost cannot be negative")
    if actor.ap < ap_cost or actor.downed:
        return False
    base_rate = movement_rate(actor.stance, actor.burden, actor.class_id, None, engine.parameters) * rate_multiplier
    base_path = shortest_path(actor.position, goal, grid, base_rate)
    closing_distance = None
    if known_enemy is not None and base_path and base_path[-1].distance_to(known_enemy) < actor.position.distance_to(known_enemy):
        closing_distance = actor.position.distance_to(known_enemy)
    rate = movement_rate(actor.stance, actor.burden, actor.class_id, closing_distance, engine.parameters) * rate_multiplier
    path = shortest_path(actor.position, goal, grid, rate)
    if not path:
        return False
    before = actor.position

    def resolve(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], emitted: dict[str, Any]) -> None:
        actor.ap -= ap_cost
        actor.position = path[-1]
        if casualty is not None:
            casualty.position = actor.position
        distance = sum(left.distance_to(right) for left, right in zip((before, *path[:-1]), path, strict=True))
        metrics["by_side"][actor.side.value]["movement_m"] += distance
        metrics["by_actor"][actor.actor_id]["movement_m"] += distance
        previous = before
        exposure_distance = 0.0
        for position in path:
            step = previous.distance_to(position)
            if any(
                enemy.side is not actor.side
                and not enemy.downed
                and grid.line_of_sight(enemy.position, position, actor.stance)
                and grid.exposure(enemy.position, position, actor.stance) != "none"
                for enemy in engine.state.actors.values()
            ):
                exposure_distance += step
            previous = position
        metrics["by_side"][actor.side.value]["exposure_m"] += exposure_distance
        _add_ap(metrics, actor.actor_id, "MOVE", ap_cost)
        stages.append({"stage": "GRID MOVEMENT", "from": asdict(before), "to": asdict(actor.position), "path": [asdict(item) for item in path], "rate_m_per_ap": rate, "exposure_m": exposure_distance})
        if specialty_node_id is not None and ap_cost != 1:
            effect = record_effect(
                metrics,
                actor_id=actor.actor_id,
                node_id=specialty_node_id,
                action="MOVE",
                field="ap_cost",
                before=1,
                after=ap_cost,
            )
            stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
        emitted.update({"distance_m": distance, "closing_on_known_enemy": closing_distance is not None})
        return None

    engine.record(
        actor.actor_id,
        "MOVE",
        {"goal": asdict(goal), "casualty_id": casualty.actor_id if casualty else None, "policy_id": policy_id},
        resolve,
    )
    return True


def _record_fire(engine: EventEngine, grid: Any, shooter: Any, target: Any, metrics: dict[str, Any]) -> bool:
    if shooter.downed or target.downed or shooter.weapon is None:
        return False
    if "SEMI AUTO" in shooter.weapon.fire_modes:
        mode, requested = "SEMI AUTO", 1
        estimated_ap = engine.parameters.values["fire.semi_single.ap"]
    elif "SINGLE" in shooter.weapon.fire_modes:
        mode, requested = "SINGLE", 1
        estimated_ap = engine.parameters.values["fire.semi_single.ap"]
    elif "3-round BURST" in shooter.weapon.fire_modes:
        mode, requested = "BURST", 3
        estimated_ap = engine.parameters.values["fire.burst_3.ap"]
    else:
        mode, requested = "FULL AUTO", min(5, shooter.weapon.total_rounds())
        estimated_ap = max(1, math.ceil(requested / engine.parameters.values["fire.full_auto.rounds_per_ap"]))
    if shooter.ap < estimated_ap or requested <= 0:
        return False
    exposure = grid.exposure(shooter.position, target.position, target.stance)
    difficulty = actual_difficulty(engine.parameters.values["check.difficulty.standard"], exposure, engine.parameters)
    if difficulty is None or not grid.line_of_sight(shooter.position, target.position, target.stance):
        return False
    sequence = engine.event_count

    def resolve(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], emitted: dict[str, Any]) -> str | None:
        stream = engine.rng.stream(f"event:{sequence}")
        result = fire_action(
            shooter.weapon,
            mode,
            requested,
            stream.d20("ATTACK ROLL"),
            shooter.skill,
            shooter.aptitude,
            difficulty,
            shooter.position.distance_to(target.position),
            exposure,
            engine.parameters,
        )
        if result.ap_cost > shooter.ap:
            return "insufficient AP"
        shooter.ap -= result.ap_cost
        side_metrics = metrics["by_side"][shooter.side.value]
        side_metrics["shots"] += 1
        side_metrics["rounds"] += result.rounds_fired
        side_metrics["connections"] += result.target_connections
        for index in range(result.target_connections):
            damage = result.attack.effective_damage
            target_pen = result.attack.effective_target_pen
            routed = apply_damage(target, damage, target_pen)
            side_metrics["res_damage"] += routed.res_damage
            side_metrics["hp_damage"] += routed.total_hp_damage
            metrics["by_actor"][shooter.actor_id]["damage"] += routed.res_damage + routed.total_hp_damage
            if target.downed:
                break
        _add_ap(metrics, shooter.actor_id, "FIRE", result.ap_cost)
        stages.append({"stage": "ATTACK", "exposure": exposure, "actual_difficulty": difficulty, "result": result.to_dict()})
        emitted.update({"connections": result.target_connections, "target_downed": target.downed})
        return None

    entry = engine.record(shooter.actor_id, "FIRE", {"target_id": target.actor_id, "exposure": exposure}, resolve)
    return entry.rejected_reason is None


def _record_cycle(engine: EventEngine, actor: Any, metrics: dict[str, Any], policy_id: str) -> bool:
    if actor.downed or actor.weapon is None or not actor.weapon.needs_cycle:
        return False
    cycle_ap = int(engine.parameters.values[f"equipment.{actor.weapon.weapon_id}.cycle_ap"])
    if actor.ap < cycle_ap:
        return False

    def resolve(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], emitted: dict[str, Any]) -> str | None:
        if not cycle_weapon(actor.weapon):
            return "weapon did not require cycling"
        actor.ap -= cycle_ap
        _add_ap(metrics, actor.actor_id, "CYCLE", cycle_ap)
        stages.append({"stage": "MANUAL CYCLE", "ap_spent": cycle_ap, "chamber": actor.weapon.chamber})
        emitted["chamber"] = actor.weapon.chamber
        return None

    entry = engine.record(actor.actor_id, "CYCLE", {"weapon_id": actor.weapon.weapon_id, "policy_id": policy_id}, resolve)
    return entry.rejected_reason is None


def _visible_enemies(engine: EventEngine, grid: Any, actor: Any, enemy_side: Side) -> list[Any]:
    return sorted(
        (
            enemy
            for enemy in engine.state.actors.values()
            if enemy.side is enemy_side
            and not enemy.downed
            and grid.line_of_sight(actor.position, enemy.position, enemy.stance)
            and grid.exposure(actor.position, enemy.position, enemy.stance) != "none"
        ),
        key=lambda enemy: (actor.position.distance_to(enemy.position), enemy.actor_id),
    )


def _protected_from(actor: Any, enemies: list[Any], grid: Any) -> bool:
    if not enemies:
        return False
    protected = sum(grid.exposure(enemy.position, actor.position, actor.stance) in {"none", "quarter", "half"} for enemy in enemies)
    return protected >= math.ceil(len(enemies) / 2)


def _cover_destination(actor: Any, enemies: list[Any], grid: Any, parameters: ParameterSet) -> Position | None:
    if not enemies:
        return None
    occupied = set(grid.full_blockers)
    for cover in grid.covers.values():
        if cover.active:
            occupied.update(cover.cells)
    geometry = set(occupied)
    candidates: set[tuple[int, int]] = set()
    for x, y in geometry:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            cell = (x + dx, y + dy)
            if 0 <= cell[0] < grid.width and 0 <= cell[1] < grid.height and cell not in occupied:
                candidates.add(cell)
    ranked: list[tuple[float, int, int, Position]] = []
    for x, y in candidates:
        position = Position(x, y)
        protection = sum(grid.exposure(enemy.position, position, actor.stance) in {"none", "quarter", "half"} for enemy in enemies)
        if protection == 0:
            continue
        ranked.append((actor.position.distance_to(position), -protection, x, position))
    for _distance, _protection, _x, position in sorted(ranked, key=lambda item: (item[0], item[1], item[2], item[3].y)):
        rate = movement_rate(actor.stance, actor.burden, actor.class_id, None, parameters)
        if shortest_path(actor.position, position, grid, rate):
            return position
    return None


def _flank_destination(actor: Any, target: Any, grid: Any) -> Position:
    index = max(0, int(actor.actor_id.rsplit("_", 1)[-1]) - 1)
    offset = -7 if index % 2 == 0 else 7
    return Position(
        max(0, min(grid.width - 1, round((actor.position.x + target.position.x) / 2))),
        max(0, min(grid.height - 1, round(target.position.y + offset))),
    )


def _policy_action(
    engine: EventEngine,
    grid: Any,
    actor: Any,
    enemy_side: Side,
    objective: Position,
    metrics: dict[str, Any],
    policy_id: str,
) -> bool:
    if actor.ap <= 0 or actor.downed:
        return False
    if _record_cycle(engine, actor, metrics, policy_id):
        return True
    visible = _visible_enemies(engine, grid, actor, enemy_side)
    target = visible[0] if visible else None
    if _use_assault_momentum(
        engine,
        grid,
        actor,
        metrics,
        policy_id,
    ):
        return True
    if (
        policy_id == "POLICY_ASSAULT_MOMENTUM"
        and target is not None
        and has_node(actor, SHOCK_ASSAULT_MOMENTUM)
        and not any(
            zone.owner_id == actor.actor_id
            for zone in engine.state.suppression.values()
        )
        and _record_shock_suppression(
            engine,
            grid,
            actor,
            target,
            metrics,
            policy_id,
        )
    ):
        return True
    if policy_id == "POLICY_OBJECTIVE" and actor.position.distance_to(objective) > 2.5:
        return _record_move(engine, grid, actor, objective, metrics, policy_id=policy_id)
    if policy_id == "POLICY_COVER_FIRST" and target is not None and not _protected_from(actor, visible, grid):
        cover_goal = _cover_destination(actor, visible, grid, engine.parameters)
        if cover_goal is not None and _record_move(engine, grid, actor, cover_goal, metrics, policy_id=policy_id):
            return True
    if policy_id == "POLICY_PRESSURE" and target is not None:
        close_envelope = float(engine.parameters.values["class.breacher.close_envelope_m"])
        if actor.position.distance_to(target.position) > close_envelope:
            return _record_move(engine, grid, actor, target.position, metrics, known_enemy=target.position, policy_id=policy_id)
    if policy_id == "POLICY_FLANK" and target is not None:
        completed = engine.state.objective["policy_state"]["flank_complete"]
        if actor.actor_id not in completed:
            flank_goal = _flank_destination(actor, target, grid)
            if actor.position.distance_to(flank_goal) <= 1.5:
                completed.append(actor.actor_id)
            elif _record_move(engine, grid, actor, flank_goal, metrics, policy_id=policy_id):
                return True
    if target is not None and _record_fire(engine, grid, actor, target, metrics):
        return True
    return _record_move(engine, grid, actor, objective, metrics, policy_id=policy_id)


def _medical_entry_action(
    engine: EventEngine,
    carrier: Any,
    casualty: Any,
    attacker: Side,
    metrics: dict[str, Any],
) -> bool:
    fixture = engine.state.objective.get("medical_fixture")
    if not fixture or fixture.get("treatment_complete"):
        return False
    side_metrics = metrics["by_side"][attacker.value]
    if fixture["kind"] == "COMBAT_MEDICINE":
        ordinary_ap = max(
            1,
            int(engine.parameters.values["medicine.field_treatment_ap"])
            - (
                int(
                    engine.parameters.values[
                        "class.corpsman.medicine_ap_discount"
                    ]
                )
                if carrier.class_id == "CORPSMAN"
                else 0
            ),
        )
        injury = fixture["injury"]
        injury_id = str(injury["injury_id"])
        if carrier.ap < ordinary_ap:
            return False
        sequence = engine.event_count
        combat_medicine = has_node(carrier, FIELD_MEDIC_COMBAT_MEDICINE)

        def treat_forearm(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            roll = engine.rng.stream(f"event:{sequence}").d20("COMBAT MEDICINE")
            difficulty = int(engine.parameters.values["check.difficulty.standard"])
            total = (
                roll
                + carrier.skill
                + carrier.aptitude
                + (
                    int(
                        engine.parameters.values[
                            "class.corpsman.medicine_check_bonus"
                        ]
                    )
                    if carrier.class_id == "CORPSMAN"
                    else 0
                )
            )
            success = total >= difficulty
            hp_before = casualty.hp
            res_before = casualty.res
            injury_before = dict(injury)
            carrier.ap -= ordinary_ap
            side_metrics["medical_actions"] += 1
            side_metrics["objective_actions"] += 1
            _add_ap(metrics, carrier.actor_id, "COMBAT MEDICINE", ordinary_ap)

            distinct = injury_id not in fixture["treated_injury_ids"]
            res_restored = 0
            if success:
                fixture["treatment_complete"] = True
                if distinct:
                    fixture["treated_injury_ids"].append(injury_id)
                if (
                    combat_medicine
                    and distinct
                    and fixture["medically_plausible_immediate_function"]
                ):
                    res_restored = min(
                        int(
                            engine.parameters.values[
                                "specialty.field_medic.combat_medicine.res_restore"
                            ]
                        ),
                        casualty.max_res - casualty.res,
                    )
                    casualty.res += res_restored

            stages.append(
                {
                    "stage": "ELIGIBLE BATTLEFIELD TREATMENT",
                    "injury_id": injury_id,
                    "injury_before": injury_before,
                    "injury_after": dict(injury),
                    "hp_before": hp_before,
                    "hp_after": casualty.hp,
                    "res_before": res_before,
                    "res_after": casualty.res,
                    "res_restored": res_restored,
                    "medically_plausible_immediate_function": fixture[
                        "medically_plausible_immediate_function"
                    ],
                    "distinct_treatment_consumed": bool(success and distinct),
                    "roll": roll,
                    "total": total,
                    "difficulty": difficulty,
                    "success": success,
                    "ap_spent": ordinary_ap,
                }
            )
            if combat_medicine and res_restored:
                effect = record_effect(
                    metrics,
                    actor_id=carrier.actor_id,
                    node_id=FIELD_MEDIC_COMBAT_MEDICINE,
                    action="COMBAT MEDICINE",
                    field="res",
                    before=res_before,
                    after=casualty.res,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            emitted["treatment_success"] = success
            emitted["res_restored"] = res_restored
            return None

        entry = engine.record(
            carrier.actor_id,
            "COMBAT MEDICINE",
            {"casualty_id": casualty.actor_id, "injury_id": injury_id},
            treat_forearm,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "TRAUMA":
        ordinary_ap = max(
            1,
            int(engine.parameters.values["medicine.field_treatment_ap"])
            - (
                int(
                    engine.parameters.values[
                        "class.corpsman.medicine_ap_discount"
                    ]
                )
                if carrier.class_id == "CORPSMAN"
                else 0
            ),
        )
        if carrier.ap < ordinary_ap:
            return False
        sequence = engine.event_count
        trauma_care = has_node(carrier, TRAUMA_TRAUMA_CARE)

        def treat_trauma(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            roll = engine.rng.stream(f"event:{sequence}").d20("TRAUMA CARE")
            difficulty = int(engine.parameters.values["check.difficulty.standard"])
            total = (
                roll
                + carrier.skill
                + carrier.aptitude
                + (
                    int(
                        engine.parameters.values[
                            "class.corpsman.medicine_check_bonus"
                        ]
                    )
                    if carrier.class_id == "CORPSMAN"
                    else 0
                )
            )
            success = total >= difficulty
            carrier.ap -= ordinary_ap
            side_metrics["medical_actions"] += 1
            side_metrics["objective_actions"] += 1
            _add_ap(metrics, carrier.actor_id, "TRAUMA CARE", ordinary_ap)
            stages.append(
                {
                    "stage": "SERIOUS INJURY TREATMENT",
                    "injury_id": fixture["injury_id"],
                    "serious": fixture["serious"],
                    "medically_treatable": fixture["medically_treatable"],
                    "equipment_available": fixture["equipment_available"],
                    "working_conditions_appropriate": fixture[
                        "working_conditions_appropriate"
                    ],
                    "roll": roll,
                    "total": total,
                    "difficulty": difficulty,
                    "success": success,
                    "ap_spent": ordinary_ap,
                }
            )
            if success:
                recovery_steps = (
                    int(
                        engine.parameters.values[
                            "specialty.trauma.trauma_care.injury_recovery_steps"
                        ]
                    )
                    if trauma_care
                    else 0
                )
                fixture["recovery_steps"] = recovery_steps
                fixture["treatment_complete"] = True
                side_metrics["injury_recovery_steps"] += recovery_steps
                if trauma_care:
                    effect = record_effect(
                        metrics,
                        actor_id=carrier.actor_id,
                        node_id=TRAUMA_TRAUMA_CARE,
                        action="TRAUMA CARE",
                        field="injury_recovery_steps",
                        before=0,
                        after=recovery_steps,
                    )
                    stages.append(
                        {"stage": "SPECIALTY EFFECT", "effects": [effect]}
                    )
            emitted["treatment_success"] = success
            emitted["injury_recovery_steps"] = fixture["recovery_steps"]
            return None

        entry = engine.record(
            carrier.actor_id,
            "TRAUMA CARE",
            {
                "casualty_id": casualty.actor_id,
                "injury_id": fixture["injury_id"],
            },
            treat_trauma,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "RESUSCITATION":
        condition = casualty.conditions[0]
        ordinary_ap = max(
            1,
            int(engine.parameters.values["medicine.resuscitation_ap"])
            - (
                int(
                    engine.parameters.values[
                        "class.corpsman.medicine_ap_discount"
                    ]
                )
                if carrier.class_id == "CORPSMAN"
                else 0
            ),
        )
        if carrier.ap < 1:
            return False
        resuscitator = has_node(carrier, RESUSCITATOR_RESUSCITATION)
        sequence = engine.event_count

        def continue_resuscitation(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            elapsed_before = condition.elapsed_seconds
            progress_before = int(fixture["progress_ap"])
            if progress_before == 0:
                fixture["started_before_expiry"] = (
                    condition.reversible_window_seconds is not None
                    and condition.elapsed_seconds
                    < condition.reversible_window_seconds
                )
                fixture["window_held"] = bool(
                    resuscitator
                    and fixture["started_before_expiry"]
                    and fixture["capable_intervention"]
                )
            carrier.ap -= 1
            fixture["progress_ap"] = progress_before + 1
            condition.elapsed_seconds += float(
                engine.parameters.values["fixture.ap_seconds"]
            )
            side_metrics["objective_actions"] += 1
            side_metrics["resuscitation_progress_ap"] += 1
            _add_ap(metrics, carrier.actor_id, "RESUSCITATION", 1)
            stages.append(
                {
                    "stage": "CONTINUOUS RESUSCITATION",
                    "condition_id": condition.condition_id,
                    "progress_ap_before": progress_before,
                    "progress_ap_after": fixture["progress_ap"],
                    "required_ap": ordinary_ap,
                    "elapsed_seconds_before": elapsed_before,
                    "elapsed_seconds_after": condition.elapsed_seconds,
                    "reversible_window_seconds": condition.reversible_window_seconds,
                    "started_before_expiry": fixture["started_before_expiry"],
                    "window_held": fixture["window_held"],
                    "continuous_care": True,
                }
            )
            if resuscitator and progress_before == 0 and fixture["window_held"]:
                effect = record_effect(
                    metrics,
                    actor_id=carrier.actor_id,
                    node_id=RESUSCITATOR_RESUSCITATION,
                    action="RESUSCITATION",
                    field="reversible_window_held",
                    before=False,
                    after=True,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            window_expired = bool(
                condition.reversible_window_seconds is not None
                and condition.elapsed_seconds >= condition.reversible_window_seconds
            )
            if (
                fixture["progress_ap"] < ordinary_ap
                and window_expired
                and not fixture["window_held"]
            ):
                engine.state.terminal = True
                engine.state.terminal_reason = (
                    f"{attacker.value} resuscitation window expired during treatment"
                )
                emitted["reversible_path_lost"] = True
                return None
            if fixture["progress_ap"] == ordinary_ap:
                roll = engine.rng.stream(f"event:{sequence}").d20("RESUSCITATION")
                result = medical_action(
                    condition,
                    "RESUSCITATION",
                    roll=roll,
                    skill=carrier.skill,
                    aptitude=carrier.aptitude,
                    parameters=engine.parameters,
                    corpsman=carrier.class_id == "CORPSMAN",
                    capable_intervention=fixture["capable_intervention"],
                    within_window=(not window_expired or fixture["window_held"]),
                )
                side_metrics["medical_actions"] += 1
                stages.append(
                    {
                        "stage": "RESUSCITATION RESOLUTION",
                        "result": asdict(result),
                        "cumulative_ap_spent": ordinary_ap,
                    }
                )
                fixture["treatment_complete"] = result.success
                engine.state.objective["stabilized"] = result.success
                if not result.success:
                    engine.state.terminal = True
                    engine.state.terminal_reason = (
                        f"{attacker.value} resuscitation attempt failed"
                    )
                emitted["resuscitation_success"] = result.success
            return None

        entry = engine.record(
            carrier.actor_id,
            "RESUSCITATION",
            {
                "casualty_id": casualty.actor_id,
                "condition_id": condition.condition_id,
                "progress_ap": fixture["progress_ap"],
            },
            continue_resuscitation,
        )
        return entry.rejected_reason is None
    return False


def _extract_action(engine: EventEngine, grid: Any, attacker: Side, policies: dict[str, str], metrics: dict[str, Any]) -> bool:
    state = engine.state
    objective = state.objective
    casualty = state.actors[objective["casualty_id"]]
    extraction = Position(**objective["extraction"])
    carrier = state.actors.get(objective.get("carrier_id"))
    if carrier is None or carrier.downed:
        role_pool = [
            actor
            for actor in state.actors.values()
            if actor.side is attacker
            and actor.actor_id != casualty.actor_id
            and policies[actor.actor_id] in {"POLICY_SUPPORT", "POLICY_OBJECTIVE"}
        ]
        candidates = [actor for actor in role_pool if not actor.downed]
        if not role_pool:
            candidates = [
                actor
                for actor in state.actors.values()
                if actor.side is attacker and not actor.downed and actor.actor_id != casualty.actor_id
            ]
        carrier = min(
            candidates,
            key=lambda actor: (
                actor.class_id != "CORPSMAN",
                policies[actor.actor_id] != "POLICY_SUPPORT",
                actor.position.distance_to(casualty.position),
                actor.actor_id,
            ),
            default=None,
        )
        objective["carrier_id"] = carrier.actor_id if carrier else None
        objective["lifted"] = False
    if carrier is None:
        return False
    if carrier.ap <= 0 or objective["complete"]:
        return False
    if carrier.position.distance_to(casualty.position) > 1.5:
        return _record_move(engine, grid, carrier, casualty.position, metrics, policy_id=policies[carrier.actor_id])
    medical_fixture = objective.get("medical_fixture")
    if medical_fixture and not medical_fixture.get("treatment_complete"):
        return _medical_entry_action(
            engine,
            carrier,
            casualty,
            attacker,
            metrics,
        )
    condition = casualty.conditions[0]
    if condition.state is ConditionState.ACTIVE:
        sequence = engine.event_count

        def treat(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], emitted: dict[str, Any]) -> str | None:
            stream = engine.rng.stream(f"event:{sequence}")
            result = medical_action(
                condition,
                "STABILIZE",
                roll=stream.d20("STABILIZE"),
                skill=carrier.skill,
                aptitude=carrier.aptitude,
                parameters=engine.parameters,
                corpsman=carrier.class_id == "CORPSMAN",
                capable_intervention=True,
            )
            if result.ap_spent > carrier.ap:
                return "insufficient AP for stabilization"
            carrier.ap -= result.ap_spent
            metrics["by_side"][attacker.value]["medical_actions"] += 1
            metrics["by_side"][attacker.value]["objective_actions"] += 1
            _add_ap(metrics, carrier.actor_id, "STABILIZE", result.ap_spent)
            stages.append({"stage": "MEDICAL", "result": asdict(result)})
            emitted["stabilized"] = result.success
            objective["stabilized"] = result.success
            return None

        entry = engine.record(carrier.actor_id, "STABILIZE", {"casualty_id": casualty.actor_id, "policy_id": policies[carrier.actor_id]}, treat)
        return entry.rejected_reason is None
    objective["stabilized"] = True
    movement = casualty_movement("CARRY", current_movement_m_per_ap=movement_rate(carrier.stance, carrier.burden, carrier.class_id, None, engine.parameters), parameters=engine.parameters)
    if not objective["lifted"]:
        if carrier.ap < movement.setup_ap:
            return False

        def lift(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], _emitted: dict[str, Any]) -> None:
            carrier.ap -= movement.setup_ap
            carrier.casualty_mode = "CARRY"
            carrier.casualty_id = casualty.actor_id
            casualty.carried_by = carrier.actor_id
            objective["lifted"] = True
            _add_ap(metrics, carrier.actor_id, "LIFT CASUALTY", movement.setup_ap)
            stages.append({"stage": "CARRY SETUP", "result": asdict(movement)})
            return None

        entry = engine.record(carrier.actor_id, "LIFT CASUALTY", {"casualty_id": casualty.actor_id, "policy_id": policies[carrier.actor_id]}, lift)
        return entry.rejected_reason is None
    moved = _record_move(
        engine,
        grid,
        carrier,
        extraction,
        metrics,
        rate_multiplier=movement.movement_m_per_ap / max(0.001, movement_rate(carrier.stance, carrier.burden, carrier.class_id, None, engine.parameters)),
        casualty=casualty,
        policy_id=policies[carrier.actor_id],
    )
    if moved:
        metrics["by_side"][attacker.value]["objective_actions"] += 1
        if casualty.position.distance_to(extraction) <= 1.0:
            objective["complete"] = True
            state.terminal = True
            state.terminal_reason = f"{attacker.value} extracted stabilized casualty"
    return moved


def _breach_objective_actor(state: SimulationState, attacker: Side, policies: dict[str, str]) -> Any | None:
    actors = [actor for actor in state.actors.values() if actor.side is attacker and not actor.downed]
    return min(
        actors,
        key=lambda actor: (
            policies[actor.actor_id] != "POLICY_OBJECTIVE",
            policies[actor.actor_id] != "POLICY_DIRECT",
            actor.actor_id,
        ),
        default=None,
    )


def _weapon_retention_entry_action(
    engine: EventEngine,
    attacker: Side,
    metrics: dict[str, Any],
) -> bool:
    fixture = engine.state.objective.get("retention_fixture")
    if not fixture or fixture.get("complete"):
        return False
    enforcer = engine.state.actors[fixture["subject_id"]]
    opponent = engine.state.actors[fixture["opponent_id"]]
    cost = int(engine.parameters.values["cqc.action_ap"])
    if (
        enforcer.downed
        or opponent.downed
        or opponent.ap < cost
        or enforcer.weapon is None
        or opponent.position.distance_to(enforcer.position) > 1.5
    ):
        return False
    weapon_retention = has_node(enforcer, ENFORCER_WEAPON_RETENTION)
    offset = (
        int(
            engine.parameters.values[
                "specialty.enforcer.weapon_retention.opponent_difficulty_offset"
            ]
        )
        if weapon_retention
        else 0
    )
    sequence = engine.event_count

    def attempt_disarm(
        stages: list[dict[str, Any]],
        _windows: list[dict[str, Any]],
        emitted: dict[str, Any],
    ) -> None:
        result = resolve_cqc_maneuver(
            "DISARM",
            engine.rng.stream(f"event:{sequence}").d20("DISARM HELD FIREARM"),
            opponent.skill,
            opponent.aptitude,
            enforcer.skill,
            enforcer.aptitude,
            engine.parameters,
            defender_situational=offset,
        )
        opponent.ap -= cost
        fixture["complete"] = True
        side_metrics = metrics["by_side"][opponent.side.value]
        side_metrics["cqc_attempts"] += 1
        side_metrics["objective_actions"] += 1
        _add_ap(metrics, opponent.actor_id, "DISARM", cost)
        if result.check.success:
            side_metrics["cqc_successes"] += 1
            fixture["ground_weapon"] = {
                "position": asdict(enforcer.position),
                "state": asdict(enforcer.weapon),
            }
            enforcer.weapon = None
        stages.append(
            {
                "stage": "INCOMING FIREARM DISARM",
                "maneuver": result.maneuver,
                "held_weapon_id": fixture["held_weapon_id"],
                "check": result.check.to_dict(),
                "declared_result": result.declared_result,
                "weapon_left_control": result.check.success,
                "weapon_transferred_to_attacker": False,
                "ground_weapon": fixture["ground_weapon"],
            }
        )
        if weapon_retention:
            effect = record_effect(
                metrics,
                actor_id=enforcer.actor_id,
                node_id=ENFORCER_WEAPON_RETENTION,
                action="RESIST DISARM",
                field="incoming_disarm_difficulty",
                before=result.check.difficulty - offset,
                after=result.check.difficulty,
            )
            stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
        emitted["disarm_success"] = result.check.success
        return None

    entry = engine.record(
        opponent.actor_id,
        "DISARM HELD FIREARM",
        {
            "target_id": enforcer.actor_id,
            "held_weapon_id": fixture["held_weapon_id"],
        },
        attempt_disarm,
    )
    return entry.rejected_reason is None


def _breach_action(engine: EventEngine, grid: Any, attacker: Side, policies: dict[str, str], metrics: dict[str, Any]) -> bool:
    state = engine.state
    objective = state.objective
    if _weapon_retention_entry_action(engine, attacker, metrics):
        return True
    shock_fixture = objective.get("shock_fixture")
    if shock_fixture and not shock_fixture.get("complete"):
        shock = state.actors[shock_fixture["subject_id"]]
        target = state.actors[shock_fixture["target_id"]]
        if shock_fixture["phase"] == "SUPPRESSION":
            if _record_shock_suppression(
                engine,
                grid,
                shock,
                target,
                metrics,
                policies[shock.actor_id],
            ):
                shock_fixture["phase"] = "ADVANCE"
                return True
            return False
        before = shock.position
        moved = _use_assault_momentum(
            engine,
            grid,
            shock,
            metrics,
            policies[shock.actor_id],
        )
        shock_fixture["complete"] = moved
        if not moved and shock.position == before:
            metrics["unsupported_events"].append(
                {
                    "actor_id": shock.actor_id,
                    "action": "ASSAULT MOMENTUM ADVANCE",
                    "reason": "fixture failed to produce movement toward the pressured position",
                }
            )
        return moved
    door = state.covers[objective["door_id"]]
    actors = [actor for actor in state.actors.values() if actor.side is attacker and not actor.downed]
    breacher = next((actor for actor in actors if actor.class_id == "BREACHER"), actors[0] if actors else None)
    if breacher is None:
        return False
    door_position = Position(14, 9)
    if door.active:
        if breacher.position.distance_to(door_position) > 8:
            return _record_move(engine, grid, breacher, door_position, metrics, policy_id=policies[breacher.actor_id])
        if breacher.weapon is not None and breacher.weapon.needs_cycle:
            return _record_cycle(engine, breacher, metrics, policies[breacher.actor_id])
        ordinary_cost = int(engine.parameters.values["fire.semi_single.ap"])
        cost = ordinary_cost
        tactical_breaching = has_node(breacher, BREAKER_TACTICAL_BREACHING)
        if tactical_breaching:
            cost = max(
                1,
                ordinary_cost
                - int(
                    engine.parameters.values[
                        "specialty.breaker.tactical_breaching.ap_discount"
                    ]
                ),
            )
        if breacher.ap < cost or breacher.weapon is None:
            return False
        damage, _target_pen, _material_pen, _range = attack_values_for_weapon(breacher.weapon.weapon_id, breacher.position.distance_to(door_position), engine.parameters)
        sequence = engine.event_count

        def breach(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], emitted: dict[str, Any]) -> str | None:
            stream = engine.rng.stream(f"event:{sequence}")
            fired = fire_action(breacher.weapon, "SEMI AUTO" if "SEMI AUTO" in breacher.weapon.fire_modes else "SINGLE", 1, stream.d20("BARRIER ACTION RECORD"), breacher.skill, breacher.aptitude, 10, breacher.position.distance_to(door_position), "full", engine.parameters, legitimate=False)
            if fired.rounds_fired <= 0:
                return "breaching weapon could not fire"
            before = door.cover_hp
            applied = cover_damage(damage, door.material_resistance)
            door.cover_hp = max(0, (door.cover_hp or 0) - applied)
            if door.cover_hp == 0:
                door.active = False
            breacher.ap -= cost
            metrics["by_side"][attacker.value]["shots"] += 1
            metrics["by_side"][attacker.value]["rounds"] += fired.rounds_fired
            metrics["by_side"][attacker.value]["cover_damage"] += applied
            metrics["by_side"][attacker.value]["objective_actions"] += 1
            _add_ap(metrics, breacher.actor_id, "BREACH", cost)
            stages.append({"stage": "BARRIER IMPACT", "automatic_stationary_barrier_hit": True, "damage": damage, "material_resistance": door.material_resistance, "cover_damage": applied, "hp_before": before, "hp_after": door.cover_hp, "destroyed_passable": door.destroyed_passable})
            if tactical_breaching and cost != ordinary_cost:
                effect = record_effect(
                    metrics,
                    actor_id=breacher.actor_id,
                    node_id=BREAKER_TACTICAL_BREACHING,
                    action="BREACH",
                    field="ap_cost",
                    before=ordinary_cost,
                    after=cost,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            emitted["door_destroyed"] = not door.active
            return None

        entry = engine.record(breacher.actor_id, "BREACH", {"door_id": door.cover_id, "method": breacher.weapon.weapon_id, "policy_id": policies[breacher.actor_id]}, breach)
        return entry.rejected_reason is None
    goal = Position(**objective["objective"])
    actor = _breach_objective_actor(state, attacker, policies)
    if actor is None or actor.ap <= 0:
        return False
    if _use_assault_momentum(
        engine,
        grid,
        actor,
        metrics,
        policies[actor.actor_id],
    ):
        return True
    if actor.position.distance_to(goal) <= 2.5:
        return False
    visible = _visible_enemies(engine, grid, actor, _other(attacker))
    if visible and actor.ap == actor.max_ap and _record_fire(engine, grid, actor, visible[0], metrics):
        return True
    if door.minor_debris and not door.debris_cleared and actor.actor_id not in objective["debris_crossed"] and actor.position.x < 15:
        extra = engine.parameters.values["breach.minor_debris.crossing_extra_ap"]
        if actor.ap <= extra:
            return False

        def debris(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], _emitted: dict[str, Any]) -> None:
            actor.ap -= extra
            objective["debris_crossed"].append(actor.actor_id)
            _add_ap(metrics, actor.actor_id, "CROSS DEBRIS", extra)
            stages.append({"stage": "MINOR DEBRIS", "extra_ap": extra})
            return None

        entry = engine.record(actor.actor_id, "CROSS DEBRIS", {"door_id": door.cover_id, "policy_id": policies[actor.actor_id]}, debris)
        return entry.rejected_reason is None
    return _record_move(engine, grid, actor, goal, metrics, policy_id=policies[actor.actor_id])


def _technical_entry_action(
    engine: EventEngine,
    grid: Any,
    attacker: Side,
    metrics: dict[str, Any],
) -> bool:
    """Exercise an authored TECH Entry fixture before the shared recon objective."""
    fixture = engine.state.objective.get("technical_fixture")
    if not fixture or fixture.get("operation_complete"):
        return False
    actors = sorted(
        (
            actor
            for actor in engine.state.actors.values()
            if actor.side is attacker and actor.class_id == "TECH" and not actor.downed
        ),
        key=lambda actor: (
            not bool(
                actor.specialty_nodes
                & {
                    AUTODRONES_TACTICAL_AUTONOMY,
                    FIELD_ENGINEER_FIELD_WORK,
                    FPV_DRONES_MANUAL_PILOT,
                }
            ),
            actor.actor_id,
        ),
    )
    if not actors:
        metrics["unsupported_events"].append(
            {
                "action": "TECHNICAL ENTRY FIXTURE",
                "reason": "fixture has no capable TECH operator",
            }
        )
        fixture["operation_complete"] = True
        return False
    operator = actors[0]
    if fixture["kind"] == "FIELD_RECONFIGURATION":
        requirements_met = all(
            bool(fixture[field])
            for field in (
                "already_deployed",
                "tools_available",
                "physical_access",
                "compatible_equipment",
            )
        ) and not fixture["ordinary_deployment"]
        if not requirements_met:
            metrics["unsupported_events"].append(
                {
                    "actor_id": operator.actor_id,
                    "action": "RECONFIGURE RELAY",
                    "reason": "physical reconfiguration requires a deployed compatible device, tools, and access",
                }
            )
            fixture["operation_complete"] = True
            return False
        ordinary_ap = int(
            engine.parameters.values["engineering.field_reconfiguration_ap"]
        )
        field_work = has_node(operator, FIELD_ENGINEER_FIELD_WORK)
        ap_discount = (
            int(
                engine.parameters.values[
                    "specialty.field_engineer.field_work.ap_discount"
                ]
            )
            if field_work
            else 0
        )
        action_ap = max(1, ordinary_ap - ap_discount)
        if operator.ap < action_ap:
            return False
        before = dict(fixture["configuration"])
        after = dict(fixture["declared_configuration"])
        capabilities_before = dict(fixture["capabilities"])

        def reconfigure_relay(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            operator.ap -= action_ap
            fixture["configuration"] = after
            fixture["operation_complete"] = True
            side_metrics = metrics["by_side"][attacker.value]
            side_metrics["engineering_actions"] += 1
            side_metrics["objective_actions"] += 1
            side_metrics["foregone_fire_ap"] += action_ap
            _add_ap(metrics, operator.actor_id, "RECONFIGURE RELAY", action_ap)
            stages.append(
                {
                    "stage": "PHYSICAL RELAY RECONFIGURATION",
                    "device_id": fixture["device_id"],
                    "orientation_before": before["sensor_orientation"],
                    "orientation_after": after["sensor_orientation"],
                    "capabilities_before": capabilities_before,
                    "capabilities_after": dict(fixture["capabilities"]),
                    "already_deployed": fixture["already_deployed"],
                    "ordinary_deployment": fixture["ordinary_deployment"],
                    "tools_available": fixture["tools_available"],
                    "physical_access": fixture["physical_access"],
                    "compatible_equipment": fixture["compatible_equipment"],
                    "ap_spent": action_ap,
                }
            )
            if field_work:
                effect = record_effect(
                    metrics,
                    actor_id=operator.actor_id,
                    node_id=FIELD_ENGINEER_FIELD_WORK,
                    action="RECONFIGURE RELAY",
                    field="ap_cost",
                    before=ordinary_ap,
                    after=action_ap,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            emitted["configuration"] = dict(fixture["configuration"])
            return None

        entry = engine.record(
            operator.actor_id,
            "RECONFIGURE RELAY",
            {
                "device_id": fixture["device_id"],
                "configuration_before": before,
                "configuration_after": after,
            },
            reconfigure_relay,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "AUTONOMOUS_DRONE":
        command_ap = 1
        if operator.ap < command_ap:
            return False
        tactical_autonomy = has_node(operator, AUTODRONES_TACTICAL_AUTONOMY)
        conditional_responses = 1 if tactical_autonomy else 0

        def assign_order(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            operator.ap -= command_ap
            fixture["standing_order"] = {
                "task": "OBSERVE_AND_REPORT",
                "trigger": "SENSOR_CONFIRMS_DECLARED_HOSTILE",
                "response": "REPORT_POSITION",
                "conditional_responses": conditional_responses,
                "sensor_bound": True,
                "may_improvise": False,
            }
            fixture["operation_complete"] = True
            side_metrics = metrics["by_side"][attacker.value]
            side_metrics["drone_actions"] += 1
            side_metrics["objective_actions"] += 1
            side_metrics["foregone_fire_ap"] += command_ap
            _add_ap(metrics, operator.actor_id, "ASSIGN STANDING ORDER", command_ap)
            stages.append(
                {
                    "stage": "PHYSICAL AUTONOMOUS DRONE",
                    "device_id": fixture["device_id"],
                    "position": fixture["position"],
                    "signal_available": fixture["signal_available"],
                    "standing_order": fixture["standing_order"],
                }
            )
            if tactical_autonomy:
                effect = record_effect(
                    metrics,
                    actor_id=operator.actor_id,
                    node_id=AUTODRONES_TACTICAL_AUTONOMY,
                    action="ASSIGN STANDING ORDER",
                    field="conditional_responses",
                    before=0,
                    after=conditional_responses,
                )
                stages.append({"stage": "SPECIALTY EFFECT", "effects": [effect]})
            emitted["standing_order_active"] = True
            emitted["conditional_responses"] = conditional_responses
            return None

        entry = engine.record(
            operator.actor_id,
            "ASSIGN STANDING ORDER",
            {
                "device_id": fixture["device_id"],
                "command_ap": command_ap,
                "signal_available": fixture["signal_available"],
            },
            assign_order,
        )
        return entry.rejected_reason is None

    if fixture["kind"] == "FPV_DRONE":
        start = Position(**fixture["position"])
        destination = Position(**fixture["destination"])
        if not fixture["signal_available"] or not grid.line_of_sight(start, destination):
            metrics["unsupported_events"].append(
                {
                    "actor_id": operator.actor_id,
                    "action": "MANUAL PILOT",
                    "reason": "fixture lacks signal or a physically open flight path",
                }
            )
            fixture["operation_complete"] = True
            return False
        manual_pilot = has_node(operator, FPV_DRONES_MANUAL_PILOT)
        ordinary_rate = float(
            engine.parameters.values["equipment.fpv_drone.move_m_per_ap"]
        )
        movement_bonus = (
            float(
                engine.parameters.values[
                    "specialty.fpv_drones.manual_pilot.movement_m_per_ap_bonus"
                ]
            )
            if manual_pilot
            else 0.0
        )
        effective_rate = ordinary_rate + movement_bonus
        operator_ap = math.ceil(start.distance_to(destination) / effective_rate)
        if operator.ap < operator_ap:
            return False
        ordinary_difficulty = int(
            engine.parameters.values["check.difficulty.standard"]
        )
        difficulty_offset = (
            int(
                engine.parameters.values[
                    "specialty.fpv_drones.manual_pilot.difficulty_offset"
                ]
            )
            if manual_pilot
            else 0
        )
        effective_difficulty = ordinary_difficulty + difficulty_offset
        sequence = engine.event_count

        def manual_control(
            stages: list[dict[str, Any]],
            _windows: list[dict[str, Any]],
            emitted: dict[str, Any],
        ) -> None:
            roll = engine.rng.stream(f"event:{sequence}").d20("MANUAL PILOT CHECK")
            operator.ap -= operator_ap
            fixture["position"] = asdict(destination)
            fixture["direct_control"] = True
            fixture["operator_id"] = operator.actor_id
            fixture["operation_complete"] = True
            side_metrics = metrics["by_side"][attacker.value]
            side_metrics["drone_actions"] += 1
            side_metrics["objective_actions"] += 1
            side_metrics["foregone_fire_ap"] += operator_ap
            _add_ap(metrics, operator.actor_id, "MANUAL PILOT", operator_ap)
            stages.append(
                {
                    "stage": "PHYSICAL FPV FLIGHT",
                    "device_id": fixture["device_id"],
                    "position_before": asdict(start),
                    "position_after": asdict(destination),
                    "distance_m": start.distance_to(destination),
                    "movement_m_per_ap": effective_rate,
                    "operator_ap_spent": operator_ap,
                    "roll": roll,
                    "difficulty": effective_difficulty,
                    "precise_control_success": (
                        roll + operator.skill + operator.aptitude
                        >= effective_difficulty
                    ),
                }
            )
            if manual_pilot:
                effects = [
                    record_effect(
                        metrics,
                        actor_id=operator.actor_id,
                        node_id=FPV_DRONES_MANUAL_PILOT,
                        action="MANUAL PILOT",
                        field="movement_m_per_ap",
                        before=ordinary_rate,
                        after=effective_rate,
                    ),
                    record_effect(
                        metrics,
                        actor_id=operator.actor_id,
                        node_id=FPV_DRONES_MANUAL_PILOT,
                        action="MANUAL PILOT",
                        field="difficulty",
                        before=ordinary_difficulty,
                        after=effective_difficulty,
                    ),
                ]
                stages.append({"stage": "SPECIALTY EFFECT", "effects": effects})
            emitted["drone_position"] = asdict(destination)
            emitted["operator_ap_spent"] = operator_ap
            return None

        entry = engine.record(
            operator.actor_id,
            "MANUAL PILOT",
            {
                "device_id": fixture["device_id"],
                "destination": asdict(destination),
                "direct_control": True,
            },
            manual_control,
        )
        return entry.rejected_reason is None
    return False


def _recon_hack_actions(engine: EventEngine, grid: Any, attacker: Side, metrics: dict[str, Any]) -> None:
    state = engine.state
    objective = state.objective
    actors = [actor for actor in state.actors.values() if actor.side is attacker and not actor.downed]
    if not actors:
        return
    hacker = next((actor for actor in actors if actor.class_id == "HACKER"), actors[0])
    scout = next((actor for actor in actors if actor.actor_id != hacker.actor_id and actor.class_id in {"TECH", "GHOST"}), next((actor for actor in actors if actor.actor_id != hacker.actor_id), hacker))
    physical_objective = Position(**objective["physical_objective"])
    if objective["knowledge"] != KnowledgeState.CONFIRMED.value and scout.ap > 0:
        observation_position = Position(30, 15)
        if scout.position.distance_to(observation_position) > 1.5:
            _record_move(engine, grid, scout, observation_position, metrics)
        elif grid.line_of_sight(scout.position, physical_objective):

            def observe(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], emitted: dict[str, Any]) -> None:
                scout.ap -= 1
                objective["knowledge"] = KnowledgeState.CONFIRMED.value
                metrics["by_side"][attacker.value]["objective_actions"] += 1
                _add_ap(metrics, scout.actor_id, "OBSERVE", 1)
                stages.append({"stage": "TARGET KNOWLEDGE", "state": KnowledgeState.CONFIRMED.value, "sensor_position": asdict(scout.position), "objective_position": asdict(physical_objective)})
                emitted["knowledge"] = KnowledgeState.CONFIRMED.value
                return None

            engine.record(scout.actor_id, "OBSERVE", {"objective": asdict(physical_objective)}, observe)
    sequence_items = objective["hack_sequence"]
    if objective["hack_step"] >= len(sequence_items) or hacker.ap <= 0:
        return
    action, node_id = sequence_items[objective["hack_step"]].split(":", 1)
    node = state.network[node_id]
    if action == "GET_IN" and hacker.position.distance_to(node.physical_position or hacker.position) > 1.5:
        _record_move(engine, grid, hacker, node.physical_position or hacker.position, metrics)
        return
    authorized = action == "OPERATE" and node.privilege >= node.required_privilege
    hard = action in {"ESCALATE", "FOOTHOLD"} or (node.access_difficulty or 0) >= int(engine.parameters.values["hacking.hard_intrusion_difficulty"])
    required_ap = (
        int(engine.parameters.values["hacking.authorized_function_ap"])
        if authorized
        else max(
            1,
            int(engine.parameters.values["hacking.hard_intrusion_ap" if hard else "hacking.standard_intrusion_ap"])
            - (int(engine.parameters.values["class.hacker.intrusion_ap_discount"]) if hacker.class_id == "HACKER" else 0),
        )
    )
    if hacker.ap < required_ap:
        return
    sequence = engine.event_count
    has_local_service_access = has_node(hacker, LOCAL_HACKER_SERVICE_ACCESS)
    has_networker_map_links = has_node(hacker, NETWORKER_MAP_THE_LINKS)

    def hack(stages: list[dict[str, Any]], _windows: list[dict[str, Any]], emitted: dict[str, Any]) -> str | None:
        stream = engine.rng.stream(f"event:{sequence}")
        discovered_before = sum(item.discovered for item in state.network.values())
        result = intrusion_action(
            state.network,
            node_id,
            action,
            roll=stream.d20(f"{action} CHECK"),
            skill=hacker.skill,
            aptitude=hacker.aptitude,
            parameters=engine.parameters,
            valid_route=True,
            authorized=authorized,
            hacker=hacker.class_id == "HACKER",
            local_hacker=has_local_service_access,
            networker=has_networker_map_links,
            stay_quiet_roll=None if authorized else stream.d20("STAY QUIET"),
        )
        if result.ap_spent > hacker.ap:
            return "insufficient AP for intrusion"
        hacker.ap -= result.ap_spent
        metrics["by_side"][attacker.value]["hacking_actions"] += 1
        metrics["by_side"][attacker.value]["objective_actions"] += 1
        metrics["by_side"][attacker.value]["foregone_fire_ap"] += result.ap_spent
        _add_ap(metrics, hacker.actor_id, action, result.ap_spent)
        stages.append({"stage": "INTRUSION", "result": asdict(result)})
        applied_effects: list[dict[str, Any]] = []
        if (
            action == "GET_IN"
            and has_local_service_access
            and node.local_access
            and result.difficulty is not None
        ):
            offset = int(
                engine.parameters.values[
                    "specialty.local_hacker.service_access.difficulty_offset"
                ]
            )
            applied_effects.append(
                record_effect(
                    metrics,
                    actor_id=hacker.actor_id,
                    node_id=LOCAL_HACKER_SERVICE_ACCESS,
                    action=action,
                    field="difficulty",
                    before=result.difficulty - offset,
                    after=result.difficulty,
                )
            )
        discovered_after = sum(item.discovered for item in state.network.values())
        if (
            has_networker_map_links
            and result.success
            and action in {"GET_IN", "PIVOT"}
            and discovered_after > discovered_before
        ):
            applied_effects.append(
                record_effect(
                    metrics,
                    actor_id=hacker.actor_id,
                    node_id=NETWORKER_MAP_THE_LINKS,
                    action=action,
                    field="network_nodes_discovered",
                    before=discovered_before,
                    after=discovered_after,
                )
            )
        if applied_effects:
            stages.append(
                {
                    "stage": "SPECIALTY EFFECT",
                    "effects": applied_effects,
                }
            )
        emitted.update({"success": result.success, "detection": result.detection_after})
        if result.success:
            objective["hack_step"] += 1
            if action == "OPERATE":
                objective["network_complete"] = True
        return None

    entry = engine.record(hacker.actor_id, action, {"node_id": node_id, "objective_step": objective["hack_step"]}, hack)
    if entry.rejected_reason:
        metrics["illegal_actions"] += 1
        metrics["unsupported_events"].append({"actor_id": hacker.actor_id, "action": action, "reason": entry.rejected_reason})
        hacker.ap = 0
    if objective["network_complete"] and objective["knowledge"] == KnowledgeState.CONFIRMED.value:
        objective["complete"] = True
        state.terminal = True
        state.terminal_reason = f"{attacker.value} completed recon and network interaction"


def _objective_suppression_cells(center: Position, grid: Any) -> set[tuple[int, int]]:
    return {
        (x, y)
        for x in range(max(0, round(center.x) - 3), min(grid.width, round(center.x) + 4))
        for y in range(max(0, round(center.y) - 3), min(grid.height, round(center.y) + 4))
        if Position(x, y).distance_to(center) <= 2.5
    }


def _record_shock_suppression(
    engine: EventEngine,
    grid: Any,
    actor: Any,
    target: Any,
    metrics: dict[str, Any],
    policy_id: str,
) -> bool:
    if (
        actor.weapon is None
        or actor.ap < int(engine.parameters.values["suppression.setup_ap"])
        or not grid.line_of_sight(actor.position, target.position, target.stance)
    ):
        return False
    if "FULL AUTO" in actor.weapon.fire_modes:
        mode = "FULL AUTO"
    elif "3-round BURST" in actor.weapon.fire_modes:
        mode = "BURST"
    else:
        return False
    requested = int(engine.parameters.values["suppression.rounds_per_5m_zone"])
    sequence = engine.event_count

    def suppress(
        stages: list[dict[str, Any]],
        _windows: list[dict[str, Any]],
        emitted: dict[str, Any],
    ) -> str | None:
        stream = engine.rng.stream(f"event:{sequence}")
        commitment = fire_action(
            actor.weapon,
            mode,
            requested,
            stream.d20("SUPPRESSION COMMITMENT"),
            actor.skill,
            actor.aptitude,
            int(engine.parameters.values["check.difficulty.standard"])
            + int(engine.parameters.values["suppression.attack_difficulty_modifier"]),
            actor.position.distance_to(target.position),
            "none",
            engine.parameters,
            legitimate=False,
        )
        if commitment.rounds_fired <= 0:
            return "no ammunition could be committed"
        cost = int(engine.parameters.values["suppression.setup_ap"])
        actor.ap -= cost
        zone_id = f"SUPPRESS:{actor.actor_id}:{sequence}"
        capacity = max(
            1,
            commitment.rounds_fired
            // int(
                engine.parameters.values[
                    "suppression.exposure_capacity_round_divisor"
                ]
            ),
        )
        cells = _objective_suppression_cells(target.position, grid)
        engine.state.suppression[zone_id] = SuppressionZone(
            zone_id,
            actor.actor_id,
            cells,
            commitment.rounds_fired,
            capacity,
            capacity,
            _other(actor.side),
        )
        engine.state.objective["assault_momentum"][actor.actor_id] = {
            "target_id": target.actor_id,
            "position": asdict(target.position),
            "zone_id": zone_id,
        }
        side_metrics = metrics["by_side"][actor.side.value]
        side_metrics["shots"] += 1
        side_metrics["rounds"] += commitment.rounds_fired
        side_metrics["suppression_setups"] += 1
        _add_ap(metrics, actor.actor_id, "SUPPRESS", cost)
        stages.append(
            {
                "stage": "SUPPRESSION COMMITMENT",
                "result": commitment.to_dict(),
                "zone_id": zone_id,
                "target_position_occupied": (
                    (round(target.position.x), round(target.position.y)) in cells
                ),
            }
        )
        emitted["assault_momentum_ready"] = True
        return None

    entry = engine.record(
        actor.actor_id,
        "SUPPRESS",
        {"target_id": target.actor_id, "policy_id": policy_id},
        suppress,
    )
    return entry.rejected_reason is None


def _use_assault_momentum(
    engine: EventEngine,
    grid: Any,
    actor: Any,
    metrics: dict[str, Any],
    policy_id: str,
) -> bool:
    momentum = engine.state.objective["assault_momentum"].get(actor.actor_id)
    if momentum is None:
        return False
    goal = Position(**momentum["position"])
    before_distance = actor.position.distance_to(goal)
    assault_momentum = has_node(actor, SHOCK_ASSAULT_MOMENTUM)
    cost = 1
    if assault_momentum:
        cost = max(
            0,
            1
            - int(
                engine.parameters.values[
                    "specialty.shock.assault_momentum.ap_discount"
                ]
            ),
        )
    moved = _record_move(
        engine,
        grid,
        actor,
        goal,
        metrics,
        known_enemy=goal,
        policy_id=policy_id,
        ap_cost=cost,
        specialty_node_id=(
            SHOCK_ASSAULT_MOMENTUM if assault_momentum else None
        ),
    )
    del engine.state.objective["assault_momentum"][actor.actor_id]
    return moved and actor.position.distance_to(goal) < before_distance


def _social_action(
    engine: EventEngine,
    attacker: Side,
    metrics: dict[str, Any],
) -> bool:
    state = engine.state
    objective = state.objective
    if objective["social_complete"] or state.terminal:
        return False
    actor = next(
        (
            candidate
            for candidate in state.actors.values()
            if candidate.side is attacker
            and candidate.class_id == "INFLUENCER"
            and not candidate.downed
        ),
        None,
    )
    if actor is None or actor.ap <= 0:
        return False
    fixture_id = str(objective["fixture_id"])
    sequence = engine.event_count

    def resolve(
        stages: list[dict[str, Any]],
        _windows: list[dict[str, Any]],
        emitted: dict[str, Any],
    ) -> str | None:
        stream = engine.rng.stream(f"event:{sequence}")
        side_metrics = metrics["by_side"][attacker.value]
        effects: list[dict[str, Any]] = []
        social_result = None
        action_cost = 0

        if fixture_id == "SPECIALTY-DIPLOMAT-ENTRY":
            objective["negotiation_open"] = False
            counteroffers = (
                int(
                    engine.parameters.values[
                        "specialty.diplomat.negotiating_position.counteroffers"
                    ]
                )
                if has_node(actor, DIPLOMAT_NEGOTIATING_POSITION)
                else 0
            )
            objective["counteroffers_remaining"] = counteroffers
            if counteroffers:
                effects.append(
                    record_effect(
                        metrics,
                        actor_id=actor.actor_id,
                        node_id=DIPLOMAT_NEGOTIATING_POSITION,
                        action="MATERIALLY CHANGED COUNTEROFFER",
                        field="counteroffers_after_rejection",
                        before=0,
                        after=counteroffers,
                    )
                )
                objective["negotiation_open"] = True
                objective["counteroffers_remaining"] -= 1
                side_metrics["counteroffers_presented"] += 1
                social_result = resolve_social_attempt(
                    plausible=True,
                    relevant_fact=True,
                    leverage="weak",
                    roll=stream.d20("CHANGED COUNTEROFFER"),
                    skill=actor.skill,
                    aptitude=actor.aptitude,
                    difficulty=int(engine.parameters.values["check.difficulty.standard"]),
                    parameters=engine.parameters,
                    influencer=True,
                )
                action_cost = social_result.setup_ap
        elif fixture_id == "SPECIALTY-HANDLER-ENTRY":
            ordinary_cost = max(
                0,
                int(engine.parameters.values["social.fact_to_leverage_setup_ap"])
                - int(
                    engine.parameters.values[
                        "class.influencer.leverage_setup_ap_discount"
                    ]
                ),
            )
            action_cost = ordinary_cost
            leverage = "weak"
            if has_node(actor, HANDLER_WORK_A_CONTACT):
                action_cost = max(
                    0,
                    ordinary_cost
                    - int(
                        engine.parameters.values[
                            "specialty.handler.work_a_contact.ap_discount"
                        ]
                    ),
                )
                leverage_order = ("weak", "strong", "decisive")
                leverage = leverage_order[
                    min(
                        len(leverage_order) - 1,
                        int(
                            engine.parameters.values[
                                "specialty.handler.work_a_contact.leverage_steps"
                            ]
                        ),
                    )
                ]
                if action_cost != ordinary_cost:
                    effects.append(
                        record_effect(
                            metrics,
                            actor_id=actor.actor_id,
                            node_id=HANDLER_WORK_A_CONTACT,
                            action="CONTACT REQUEST",
                            field="ap_cost",
                            before=ordinary_cost,
                            after=action_cost,
                        )
                    )
                effects.append(
                    record_effect(
                        metrics,
                        actor_id=actor.actor_id,
                        node_id=HANDLER_WORK_A_CONTACT,
                        action="CONTACT REQUEST",
                        field="relationship_leverage",
                        before="WEAK",
                        after=leverage.upper(),
                    )
                )
                side_metrics["social_leverage_steps"] += int(
                    engine.parameters.values[
                        "specialty.handler.work_a_contact.leverage_steps"
                    ]
                )
            social_result = resolve_social_attempt(
                plausible=bool(objective["plausible_request"]),
                relevant_fact=True,
                leverage=leverage,
                roll=stream.d20("CONTACT REQUEST"),
                skill=actor.skill,
                aptitude=actor.aptitude,
                difficulty=int(engine.parameters.values["check.difficulty.standard"]),
                parameters=engine.parameters,
                influencer=True,
            )
        elif fixture_id == "SPECIALTY-ICON-ENTRY":
            ordinary_cost = max(
                0,
                int(engine.parameters.values["social.fact_to_leverage_setup_ap"])
                - int(
                    engine.parameters.values[
                        "class.influencer.leverage_setup_ap_discount"
                    ]
                ),
            )
            action_cost = ordinary_cost
            if (
                has_node(actor, ICON_RECOGNITION)
                and objective["reputation_recognized"]
                and objective["reputation_relevant"]
            ):
                action_cost = 0
                effects.append(
                    record_effect(
                        metrics,
                        actor_id=actor.actor_id,
                        node_id=ICON_RECOGNITION,
                        action="REPUTATION LEVERAGE",
                        field="setup_ap",
                        before=ordinary_cost,
                        after=action_cost,
                    )
                )
            social_result = resolve_social_attempt(
                plausible=True,
                relevant_fact=True,
                leverage="weak",
                roll=stream.d20("REPUTATION INFLUENCE"),
                skill=actor.skill,
                aptitude=actor.aptitude,
                difficulty=int(engine.parameters.values["check.difficulty.standard"]),
                parameters=engine.parameters,
                influencer=True,
            )
        else:
            return f"unknown social objective fixture {fixture_id}"

        if action_cost > actor.ap:
            return "insufficient AP for social objective"
        actor.ap -= action_cost
        side_metrics["social_actions"] += 1
        side_metrics["social_setup_ap"] += action_cost
        side_metrics["objective_actions"] += 1
        _add_ap(metrics, actor.actor_id, "SOCIAL OBJECTIVE", action_cost)
        stages.append(
            {
                "stage": "BOUNDED SOCIAL ATTEMPT",
                "fixture_id": fixture_id,
                "result": asdict(social_result) if social_result else None,
                "contact_agency_and_capability_preserved": bool(
                    objective["counterpart_capable"]
                ),
            }
        )
        if effects:
            stages.append({"stage": "SPECIALTY EFFECT", "effects": effects})
        objective["social_complete"] = bool(
            social_result and social_result.success
        )
        objective["complete"] = objective["social_complete"]
        state.terminal = True
        state.terminal_reason = (
            f"{attacker.value} completed bounded social objective"
            if objective["complete"]
            else "bounded social objective did not secure agreement"
        )
        emitted.update(
            {
                "success": objective["complete"],
                "fixture_id": fixture_id,
            }
        )
        return None

    entry = engine.record(
        actor.actor_id,
        "SOCIAL OBJECTIVE",
        {
            "fixture_id": fixture_id,
            "counterpart_id": objective["counterpart_id"],
        },
        resolve,
    )
    return entry.rejected_reason is None


def _control_breach_hold(state: SimulationState, attacker: Side, parameters: ParameterSet, metrics: dict[str, Any]) -> None:
    if state.objective["kind"] != "BREACH" or state.covers[state.objective["door_id"]].active:
        return
    goal = Position(**state.objective["objective"])
    attackers = [actor for actor in state.actors.values() if actor.side is attacker and not actor.downed and actor.position.distance_to(goal) <= 2.5]
    defenders = [actor for actor in state.actors.values() if actor.side is _other(attacker) and not actor.downed and actor.position.distance_to(goal) <= 2.5]
    if attackers and not defenders:
        seconds = parameters.values["combat.ap.pool"] * parameters.values["fixture.ap_seconds"]
        state.objective["hold_seconds"] += seconds
        metrics["by_side"][attacker.value]["objective_seconds"] += seconds
        if state.objective["hold_seconds"] >= state.objective["required_hold_seconds"]:
            state.objective["complete"] = True
            state.terminal = True
            state.terminal_reason = f"{attacker.value} held defender objective for 8 seconds"
    else:
        state.objective["hold_seconds"] = 0.0


def _primary_actor_ids(state: SimulationState, scenario_id: str, attacker: Side, policies: dict[str, str]) -> set[str]:
    if scenario_id == "TEAM-EXTRACT-4V4-V1":
        return {state.objective["carrier_id"]} if state.objective.get("carrier_id") else set()
    if scenario_id == "TEAM-BREACH-4V4-V1":
        door = state.covers[state.objective["door_id"]]
        if door.active:
            breacher = next(
                (actor for actor in state.actors.values() if actor.side is attacker and not actor.downed and actor.class_id == "BREACHER"),
                None,
            )
            return {breacher.actor_id} if breacher else set()
        objective_actor = _breach_objective_actor(state, attacker, policies)
        return {objective_actor.actor_id} if objective_actor else set()
    if scenario_id == "TEAM-RECON-HACK-4V4-V1":
        actors = [actor for actor in state.actors.values() if actor.side is attacker and not actor.downed]
        hacker = next((actor for actor in actors if actor.class_id == "HACKER"), actors[0] if actors else None)
        scout = next(
            (actor for actor in actors if actor is not hacker and actor.class_id in {"TECH", "GHOST"}),
            next((actor for actor in actors if actor is not hacker), None),
        )
        return {actor.actor_id for actor in (hacker, scout) if actor is not None}
    if scenario_id == "TEAM-SOCIAL-4V4-V1":
        actor = next(
            (
                candidate
                for candidate in state.actors.values()
                if candidate.side is attacker
                and candidate.class_id == "INFLUENCER"
                and not candidate.downed
            ),
            None,
        )
        return {actor.actor_id} if actor else set()
    return set()


def _tactical_goal(state: SimulationState) -> Position:
    if state.objective["kind"] == "EXTRACT":
        return state.actors[state.objective["casualty_id"]].position
    if state.objective["kind"] == "BREACH":
        return Position(**state.objective["objective"])
    if state.objective["kind"] == "SOCIAL":
        return Position(**state.objective["interaction_position"])
    return Position(**state.objective["physical_objective"])


def run_objective_team_scenario(spec: "TeamScenarioSpec", parameters: ParameterSet, seed: int, *, capture_events: bool = False) -> dict[str, Any]:
    state, grid, policies, attacker = _build_state(spec, parameters)
    defender = _other(attacker)
    engine = EventEngine(state, parameters, seed, capture_events)
    metrics = {
        "by_side": {"A": _side_metrics(), "B": _side_metrics()},
        "by_actor": {
            actor_id: {"side": actor.side.value, "class_id": actor.class_id, "policy_id": policies[actor_id], "ap_spent": 0, "ap_by_action": {}, "movement_m": 0.0, "damage": 0, "objective_seconds": 0.0}
            for actor_id, actor in state.actors.items()
        },
        "illegal_actions": 0,
        "invariant_failures": [],
        "unsupported_events": [],
        "specialty_effects": [],
    }
    initial_state = state.to_dict()
    for turn in range(spec.max_team_turns):
        state.objective["team_turn_index"] = turn
        for actor in state.actors.values():
            if not actor.downed:
                actor.ap = actor.max_ap
        for acting_side in (attacker, defender):
            enemy_side = _other(acting_side)
            action_guard = 0
            while not state.terminal:
                capable = [
                    actor
                    for actor in sorted(state.actors.values(), key=lambda item: item.actor_id)
                    if actor.side is acting_side and not actor.downed and actor.ap > 0
                ]
                if not capable:
                    break
                before = engine.event_count
                if acting_side is attacker:
                    if spec.scenario_id == "TEAM-EXTRACT-4V4-V1":
                        _extract_action(engine, grid, attacker, policies, metrics)
                    elif spec.scenario_id == "TEAM-BREACH-4V4-V1":
                        _breach_action(engine, grid, attacker, policies, metrics)
                    elif spec.scenario_id == "TEAM-SOCIAL-4V4-V1":
                        _social_action(engine, attacker, metrics)
                    else:
                        if not _technical_entry_action(
                            engine,
                            grid,
                            attacker,
                            metrics,
                        ):
                            _recon_hack_actions(engine, grid, attacker, metrics)
                primary_ids = _primary_actor_ids(state, spec.scenario_id, attacker, policies) if acting_side is attacker else set()
                goal = _tactical_goal(state)
                for actor in capable:
                    if actor.actor_id in primary_ids or actor.ap <= 0 or actor.downed or state.terminal:
                        continue
                    _policy_action(engine, grid, actor, enemy_side, goal, metrics, policies[actor.actor_id])
                action_guard += engine.event_count - before
                if engine.event_count == before:
                    break
                if action_guard > 128:
                    metrics["invariant_failures"].append(f"{spec.scenario_id} {acting_side.value} action guard")
                    break
        _control_breach_hold(state, attacker, parameters, metrics)
        if state.terminal:
            break
    if not state.terminal:
        state.terminal = True
        state.terminal_reason = "max team turns"
    winner = attacker.value if state.objective.get("complete") else defender.value
    for side in Side:
        actors = [actor for actor in state.actors.values() if actor.side is side]
        side_metrics = metrics["by_side"][side.value]
        side_metrics["actors_downed"] = sum(actor.downed for actor in actors)
        side_metrics["actors_dead"] = 0
        side_metrics["remaining_hp"] = sum(actor.hp for actor in actors)
        side_metrics["remaining_res"] = sum(actor.res for actor in actors)
        side_metrics["ammunition_remaining"] = sum(actor.weapon.total_rounds() for actor in actors if actor.weapon)
        side_metrics["knowledge_confirmed"] = int(state.objective.get("knowledge") == KnowledgeState.CONFIRMED.value and side is attacker)
        side_metrics["network_objective_complete"] = int(bool(state.objective.get("network_complete")) and side is attacker)
        side_metrics["social_objective_complete"] = int(
            bool(state.objective.get("social_complete")) and side is attacker
        )
        side_metrics["network_nodes_discovered"] = (
            sum(node.discovered for node in state.network.values())
            if side is attacker
            else 0
        )
        side_metrics["network_nodes_compromised"] = (
            sum(node.compromised for node in state.network.values())
            if side is attacker
            else 0
        )
        side_metrics["network_footholds"] = (
            sum(node.foothold for node in state.network.values())
            if side is attacker
            else 0
        )
    return {
        "scenario_id": spec.scenario_id,
        "scenario_version": spec.scenario_version,
        "map_id": grid.map_id,
        "fixture_ids": [actor.fixture_id for actor in sorted(state.actors.values(), key=lambda item: item.actor_id)],
        "builds": {actor.actor_id: {"class_id": actor.class_id, "level": actor.level, "specialty_nodes": sorted(actor.specialty_nodes)} for actor in state.actors.values()},
        "equipment": {actor.actor_id: actor.weapon.weapon_id if actor.weapon else None for actor in state.actors.values()},
        "policies": policies,
        "opening_side": spec.opening_side.value,
        "attacker_side": attacker.value,
        "initial_state": initial_state,
        "seed": seed,
        "parameter_set_hash": parameters.hash,
        "winner_side": winner,
        "terminal_reason": state.terminal_reason,
        "elapsed_seconds": (state.objective["team_turn_index"] + 1) * parameters.values["combat.ap.pool"] * parameters.values["fixture.ap_seconds"],
        "event_count": engine.event_count,
        "event_log_hash": engine.log_hash() if capture_events else "NOT_CAPTURED",
        "final_state_hash": state.semantic_hash() if capture_events else "NOT_CAPTURED",
        "metrics": metrics,
        "objective": state.objective,
        "final_actors": {actor.actor_id: {"hp": actor.hp, "res": actor.res, "ap": actor.ap, "downed": actor.downed, "position": asdict(actor.position), "ammunition": actor.weapon.total_rounds() if actor.weapon else 0} for actor in state.actors.values()},
        "rng": engine.rng.provenance(),
        "replay_spec": {"kind": "team", "spec": {**asdict(spec), "opening_side": spec.opening_side.value}, "seed": seed, "parameter_values": parameters.values},
        **({"events": [entry.to_dict() for entry in engine.log]} if capture_events else {}),
    }
