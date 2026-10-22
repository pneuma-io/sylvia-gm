from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .engine import EventEngine
from .fixtures import benchmark_actor, build_map
from .geometry import shortest_step_toward
from .model import HitQuality, KnowledgeState, Position, Side, SimulationState
from .parameters import ParameterSet
from .policies import KnownTarget, PolicyObservation, choose_action
from .rng import derive_seed
from .rules import apply_damage, fire_action, movement_rate, recover_recoil
from .statistics import mean_interval, wilson_interval


@dataclass(frozen=True, slots=True)
class DuelSpec:
    scenario_id: str = "MIRROR-DUEL-V1"
    scenario_version: str = "1.0.0"
    level: int = 10
    class_a: str = "RIFLEMAN"
    class_b: str = "RIFLEMAN"
    policy_a: str = "POLICY_FIRE_DISCIPLINE"
    policy_b: str = "POLICY_FIRE_DISCIPLINE"
    weapon_a: str = "service_rifle"
    weapon_b: str = "service_rifle"
    max_team_turns: int = 30

    def mirrored(self) -> "DuelSpec":
        return DuelSpec(
            self.scenario_id,
            self.scenario_version,
            self.level,
            self.class_b,
            self.class_a,
            self.policy_b,
            self.policy_a,
            self.weapon_b,
            self.weapon_a,
            self.max_team_turns,
        )


def _observation(state: SimulationState, actor_id: str, target_id: str, policy_id: str) -> PolicyObservation:
    actor = state.actors[actor_id]
    target = state.actors[target_id]
    knowledge = actor.target_knowledge.get(target_id, KnowledgeState.UNAWARE)
    position = target.position if knowledge is KnowledgeState.CONFIRMED else None
    distance = actor.position.distance_to(target.position) if position else None
    weapon = actor.weapon
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
        (KnownTarget(target.actor_id, knowledge, position, distance, True, target.downed),),
        target.position,
        False,
        False,
        {"policy_id": policy_id},
    )


def _connection_damage(result: Any, connection_index: int, parameters: ParameterSet) -> tuple[int, int]:
    damage = result.attack.effective_damage
    target_pen = result.attack.effective_target_pen
    if result.attack.quality is HitQuality.CRITICAL_HIT and connection_index > 0:
        if result.attack.critical_effect == "DEVASTATING HIT":
            damage -= parameters.values["critical.devastating_damage"]
        elif result.attack.critical_effect == "DEEP HIT":
            target_pen -= parameters.values["critical.deep_target_pen"]
    return max(0, damage), max(0, target_pen)


def run_duel(spec: DuelSpec, parameters: ParameterSet, seed: int, *, capture_events: bool = False) -> dict[str, Any]:
    actor_a = benchmark_actor(spec.class_a, spec.level, Side.A, Position(10, 15), parameters, weapon_id=spec.weapon_a)
    actor_b = benchmark_actor(spec.class_b, spec.level, Side.B, Position(90, 15), parameters, weapon_id=spec.weapon_b)
    actor_a.target_knowledge[actor_b.actor_id] = KnowledgeState.CONFIRMED
    actor_b.target_knowledge[actor_a.actor_id] = KnowledgeState.CONFIRMED
    state = SimulationState(spec.scenario_id, spec.scenario_version, {actor_a.actor_id: actor_a, actor_b.actor_id: actor_b})
    state.objective = {"aim_ap": {actor_a.actor_id: 0, actor_b.actor_id: 0}, "policy_decisions": []}
    engine = EventEngine(state, parameters, seed, capture_events)
    policies = {actor_a.actor_id: spec.policy_a, actor_b.actor_id: spec.policy_b}
    opponents = {actor_a.actor_id: actor_b.actor_id, actor_b.actor_id: actor_a.actor_id}
    grid = build_map("MAP_OPEN_100", parameters)
    initial_state = {
        "active_side": state.active_side.value,
        "actors": {
            actor.actor_id: {
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
                "policy_id": policies[actor.actor_id],
            }
            for actor in (actor_a, actor_b)
        },
    }
    metrics = {
        "shots": {"A": 0, "B": 0},
        "rounds": {"A": 0, "B": 0},
        "connections": {"A": 0, "B": 0},
        "res_damage": {"A": 0, "B": 0},
        "hp_damage": {"A": 0, "B": 0},
        "movement_m": {"A": 0.0, "B": 0.0},
        "illegal_actions": 0,
    }

    for turn_number in range(spec.max_team_turns):
        side = Side.A if turn_number % 2 == 0 else Side.B
        state.active_side = side
        actor = actor_a if side is Side.A else actor_b
        target = state.actors[opponents[actor.actor_id]]
        if actor.downed or target.downed:
            break
        actor.ap = actor.max_ap
        action_guard = 0
        while actor.ap > 0 and not actor.downed and not target.downed:
            action_guard += 1
            if action_guard > actor.max_ap * 4:
                state.terminal = True
                state.terminal_reason = "infinite-loop guard"
                metrics["illegal_actions"] += 1
                break
            policy_id = policies[actor.actor_id]
            observation = _observation(state, actor.actor_id, target.actor_id, policy_id)
            decision = choose_action(policy_id, observation)
            state.objective["policy_decisions"].append({"actor_id": actor.actor_id, "decision": asdict(decision)})
            if decision.action == "END":
                actor.ap = 0
                break
            event_index = engine.event_count
            choices = {"policy_id": policy_id, **asdict(decision)}

            def resolver(stages: list[dict[str, Any]], trigger_windows: list[dict[str, Any]], emitted: dict[str, Any]) -> str | None:
                del trigger_windows
                stream = engine.rng.stream(f"event:{event_index}")
                if decision.action in {"AIM", "RECOVER", "MOVE_OBJECTIVE", "FLANK", "MOVE_COVER"} and actor.ap < 1:
                    return "insufficient AP"
                if decision.action == "AIM":
                    actor.ap -= 1
                    state.objective["aim_ap"][actor.actor_id] = min(
                        parameters.values["fire.aim.max_ap_on_one_solution"], state.objective["aim_ap"][actor.actor_id] + 1
                    )
                    state.elapsed_seconds += 1
                    stages.append({"stage": "AIM", "target": target.actor_id, "aim_ap": state.objective["aim_ap"][actor.actor_id]})
                    return None
                if decision.action == "RECOVER":
                    recovered = recover_recoil(actor.weapon, 1, parameters, rifleman=actor.class_id == "RIFLEMAN")
                    actor.ap -= 1
                    state.elapsed_seconds += 1
                    stages.append({"stage": "RECOIL RECOVERY", "recovered": recovered, "remaining": actor.weapon.current_recoil})
                    emitted["recoil_recovered"] = recovered
                    return None
                if decision.action in {"MOVE_OBJECTIVE", "FLANK", "MOVE_COVER"}:
                    before = actor.position
                    rate = movement_rate(actor.stance, actor.burden, actor.class_id, before.distance_to(target.position), parameters)
                    if decision.action == "FLANK":
                        flank_goal = Position(target.position.x, 5 if actor.position.y >= 15 else 25)
                        actor.position = shortest_step_toward(actor.position, flank_goal, rate, grid)
                    else:
                        actor.position = shortest_step_toward(actor.position, target.position, rate, grid)
                    moved = before.distance_to(actor.position)
                    actor.ap -= 1
                    state.elapsed_seconds += 1
                    metrics["movement_m"][side.value] += moved
                    stages.append({"stage": "MOVEMENT", "from": asdict(before), "to": asdict(actor.position), "distance_m": moved})
                    return None
                if decision.action in {"FIRE", "SUPPRESS"}:
                    if actor.weapon is None:
                        return "no weapon"
                    mode = decision.mode or "SEMI AUTO"
                    requested = decision.rounds
                    estimated_ap = parameters.values["suppression.setup_ap"] if decision.action == "SUPPRESS" else (
                        parameters.values["fire.semi_single.ap"] if mode == "SEMI AUTO" else max(1, math.ceil(requested / parameters.values["fire.full_auto.rounds_per_ap"]))
                    )
                    if actor.ap < estimated_ap:
                        actor.ap = 0
                        return None
                    roll = stream.d20("ATTACK ROLL")
                    base_difficulty = parameters.values["check.difficulty.standard"]
                    if decision.action == "SUPPRESS":
                        base_difficulty += parameters.values["suppression.attack_difficulty_modifier"]
                    result = fire_action(
                        actor.weapon,
                        mode,
                        requested,
                        roll,
                        actor.skill,
                        actor.aptitude,
                        base_difficulty,
                        actor.position.distance_to(target.position),
                        "full",
                        parameters,
                        aim_ap=0 if decision.action == "SUPPRESS" else state.objective["aim_ap"][actor.actor_id],
                        sharpshooter=actor.class_id == "SHARPSHOOTER",
                    )
                    actual_ap = parameters.values["suppression.setup_ap"] if decision.action == "SUPPRESS" else result.ap_cost
                    actor.ap -= actual_ap
                    state.elapsed_seconds += actual_ap
                    state.objective["aim_ap"][actor.actor_id] = 0
                    metrics["shots"][side.value] += 1
                    metrics["rounds"][side.value] += result.rounds_fired
                    metrics["connections"][side.value] += result.target_connections
                    damage_stages = []
                    for connection_index in range(result.target_connections):
                        damage, target_pen = _connection_damage(result, connection_index, parameters)
                        damage_result = apply_damage(target, damage, target_pen)
                        metrics["res_damage"][side.value] += damage_result.res_damage
                        metrics["hp_damage"][side.value] += damage_result.total_hp_damage
                        damage_stages.append(damage_result.to_dict())
                        if target.downed:
                            break
                    stages.extend(
                        [
                            {"stage": "FIRING SOLUTION", "difficulty": result.attack.check.difficulty, "aim_ap": 0 if decision.action == "SUPPRESS" else state.objective["aim_ap"][actor.actor_id]},
                            {"stage": "ATTACK ROLL", "result": result.to_dict()},
                            {"stage": "PROJECTILE / RES / HP", "connections": damage_stages},
                        ]
                    )
                    emitted.update({"rounds": result.rounds_fired, "connections": result.target_connections, "target_downed": target.downed})
                    return None
                return f"unsupported policy action {decision.action}"

            entry = engine.record(actor.actor_id, decision.action, choices, resolver)
            if entry.rejected_reason:
                metrics["illegal_actions"] += 1
                actor.ap = 0
        if state.terminal_reason == "infinite-loop guard":
            break
        if actor_a.downed or actor_b.downed:
            state.terminal = True
            state.terminal_reason = "one side DOWNED"
            break
    if not state.terminal:
        state.terminal = True
        state.terminal_reason = "max team turns"
    winner_side = None
    if actor_a.downed and not actor_b.downed:
        winner_side = "B"
    elif actor_b.downed and not actor_a.downed:
        winner_side = "A"
    elif actor_a.hp + actor_a.res != actor_b.hp + actor_b.res:
        winner_side = "A" if actor_a.hp + actor_a.res > actor_b.hp + actor_b.res else "B"
    result = {
        "scenario_id": spec.scenario_id,
        "scenario_version": spec.scenario_version,
        "fixture_ids": [actor_a.fixture_id, actor_b.fixture_id],
        "policies": [spec.policy_a, spec.policy_b],
        "seed": seed,
        "parameter_set_hash": parameters.hash,
        "map_id": grid.map_id,
        "map_geometry": {"width": grid.width, "height": grid.height, "full_blockers": [], "covers": {}},
        "initial_state": initial_state,
        "winner_side": winner_side,
        "terminal_reason": state.terminal_reason,
        "elapsed_seconds": state.elapsed_seconds,
        "final_state_hash": state.semantic_hash(),
        "event_log_hash": engine.log_hash(),
        "event_count": engine.event_count,
        "metrics": metrics,
        "final_actors": {actor_id: {"hp": actor.hp, "res": actor.res, "downed": actor.downed, "position": asdict(actor.position)} for actor_id, actor in state.actors.items()},
        "rng": engine.rng.provenance(),
        "replay_spec": {"kind": "duel", "spec": asdict(spec), "seed": seed, "parameter_values": parameters.values},
    }
    if capture_events:
        result["events"] = [entry.to_dict() for entry in engine.log]
    return result


def duel_pair_chunk(
    spec: DuelSpec,
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
    subject_wins = 0.0
    draws = 0
    terminal_times: list[float] = []
    hp_damage_effects: list[float] = []
    sample_replay: dict[str, Any] | None = None
    for pair_index in range(pair_start, pair_start + pairs):
        seed = derive_seed(master_seed, "paired-mirror", pair_index)
        first = run_duel(spec, parameters, seed, capture_events=capture_replay and pair_index == 0)
        second = run_duel(spec.mirrored(), parameters, seed, capture_events=False)
        if pair_index == 0 and capture_replay:
            sample_replay = first
        for result, subject_side in ((first, "A"), (second, "B")):
            if result["winner_side"] is None:
                subject_wins += 0.5
                draws += 1
            elif result["winner_side"] == subject_side:
                subject_wins += 1
            terminal_times.append(result["elapsed_seconds"])
        first_subject = first["metrics"]["hp_damage"]["A"]
        second_subject = second["metrics"]["hp_damage"]["B"]
        first_control = first["metrics"]["hp_damage"]["B"]
        second_control = second["metrics"]["hp_damage"]["A"]
        hp_damage_effects.append(((first_subject + second_subject) - (first_control + second_control)) / 2)
    return {
        "pair_start": pair_start,
        "pairs": pairs,
        "subject_wins": subject_wins,
        "draws": draws,
        "terminal_times": terminal_times,
        "hp_damage_effects": hp_damage_effects,
        "sample_replay": sample_replay,
    }


def merge_duel_pair_chunks(
    spec: DuelSpec,
    parameters: ParameterSet,
    master_seed: int,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(chunks, key=lambda item: item["pair_start"])
    if not ordered or ordered[0]["pair_start"] != 0:
        raise ValueError("duel pair chunks must begin at pair zero")
    expected_start = 0
    for chunk in ordered:
        if chunk["pair_start"] != expected_start:
            raise ValueError("duel pair chunks must be contiguous and non-overlapping")
        expected_start += chunk["pairs"]
    pairs = expected_start
    subject_wins = sum(chunk["subject_wins"] for chunk in ordered)
    draws = sum(chunk["draws"] for chunk in ordered)
    terminal_times = [value for chunk in ordered for value in chunk["terminal_times"]]
    hp_damage_effects = [value for chunk in ordered for value in chunk["hp_damage_effects"]]
    sample_replay = next(
        (chunk["sample_replay"] for chunk in ordered if chunk["sample_replay"]),
        None,
    )
    total = pairs * 2
    interval = wilson_interval(subject_wins, total)
    neutral = abs(interval.estimate - 0.5) <= 0.02 or (interval.low <= 0.5 <= interval.high and interval.high - interval.low <= 0.04)
    return {
        "scenario_id": spec.scenario_id,
        "scenario_version": spec.scenario_version,
        "parameter_set_hash": parameters.hash,
        "master_seed": master_seed,
        "derived_seed_scheme": "sha256(master, paired-mirror, pair_index)",
        "paired_mirrored_runs": pairs,
        "total_trials": total,
        "qualification": "FINAL" if pairs >= 50000 else ("ORDINARY" if pairs >= 10000 else "SMOKE_NON_QUALIFYING"),
        "subject_outcome_share": interval.to_dict(),
        "draws": draws,
        "mirror_neutral": neutral if spec.class_a == spec.class_b and spec.policy_a == spec.policy_b else None,
        "terminal_time_seconds": mean_interval(terminal_times).to_dict(),
        "paired_hp_damage_effect": mean_interval(hp_damage_effects).to_dict(),
        "sample_replay": sample_replay,
        "findings": [] if neutral or spec.class_a != spec.class_b else [{"code": "MIRROR BIAS", "severity": "FAIL", "outcome_share": interval.to_dict()}],
    }


def paired_mirrored_duels(
    spec: DuelSpec,
    parameters: ParameterSet,
    master_seed: int,
    pairs: int,
    *,
    capture_replay: bool = True,
) -> dict[str, Any]:
    chunk = duel_pair_chunk(
        spec,
        parameters,
        master_seed,
        0,
        pairs,
        capture_replay=capture_replay,
    )
    return merge_duel_pair_chunks(spec, parameters, master_seed, [chunk])


def progression_sweep(parameters: ParameterSet, master_seed: int, pairs_per_level: int) -> dict[str, Any]:
    rows = []
    for level in (1, 5, 10, 15, 20):
        result = paired_mirrored_duels(DuelSpec(level=level), parameters, derive_seed(master_seed, "progression", level), pairs_per_level, capture_replay=False)
        rows.append(
            {
                "level": level,
                "outcome_share": result["subject_outcome_share"],
                "terminal_time_seconds": result["terminal_time_seconds"],
                "qualification": result["qualification"],
            }
        )
    return {"scenario_family": "PROGRESSION", "levels": rows}
