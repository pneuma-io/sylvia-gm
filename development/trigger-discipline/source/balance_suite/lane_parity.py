"""Bounded differential fixtures; no cohorts, optimizer, provider or canonical writes."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import (
    materialize_package_mechanic,
    _parse_runtime,
    _runtime_parameter_schema,
)
from sylvia_gm.domain.authority import MechanicContext, MechanicsChoiceResolution, thaw_json_value
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_ruleset import builder

from .engine import EventEngine
from .fixtures import weapon_state
from .geometry import GridMap
from .model import ActorState, CoverObject, Position, Side, Stance
from .parameters import default_manifest
from .policies import OverwatchSelection, PolicyDecision, choose_action
from . import overwatch, tactical

NS = builder.PACKAGE_ID
RUNTIME_SHA = "20a424e01b82bda8c55c97354911e5b63e63a46fe591e53c860e3946c077063f"


class Oracle:
    """Execute the pinned package bytes through Sylvia's real mechanic materializer."""

    def __init__(self, package):
        archive = Path(package).read_bytes()
        self.package_sha256 = hashlib.sha256(archive).hexdigest()
        with zipfile.ZipFile(package) as z:
            self.runtime = z.read(f"packages/{NS}/runtime/ruleset.yaml")
            self.state = json.loads(z.read(f"packages/{NS}/conformance/scenario.json"))[
                "initial_state"
            ]
        assert hashlib.sha256(self.runtime).hexdigest() == RUNTIME_SHA
        self.parsed = _parse_runtime(package_id=NS, runtime_bytes=self.runtime)
        self.sequence = 0

    def execute(self, tool, parameters, *, roll=10, order=()):
        self.sequence += 1
        mechanic = materialize_package_mechanic(
            package_id=NS,
            package_version="0.1.30",
            package_sha256=self.package_sha256,
            declaration=ToolDeclaration(
                name=tool,
                capability="combat",
                input_schema=_runtime_parameter_schema(self.parsed.tools[tool]),
                state_predicates=(),
                turn_statuses=("open",),
                writers=("dm",),
            ),
            runtime_bytes=self.runtime,
        )
        resolution = None
        while True:
            with patch.object(dice._rng, "randint", return_value=roll):
                outcome = mechanic(
                    MechanicContext(
                        campaign_id="lab-parity-v1",
                        turn_id="turn",
                        action_id=f"event-{self.sequence}",
                        state=self.state,
                        parameters=parameters,
                        choice_resolution=resolution,
                    )
                )
            if outcome.choice is None:
                for mutation in outcome.mutations:
                    self.state[f"{mutation.namespace}.{mutation.key}"] = thaw_json_value(
                        mutation.value
                    )
                return thaw_json_value(outcome.result)
            choice = outcome.choice
            if choice.choice_id == "overwatch-window-order":
                owner = next(
                    (
                        owner
                        for owner in order
                        if any(o.label.startswith(owner + " (") for o in choice.options)
                    ),
                    None,
                )
                if owner is None:
                    return {"pending": asdict(choice)}
                option = next(
                    o.option_id for o in choice.options if o.label.startswith(owner + " (")
                )
            else:
                option = "DEEP_HIT"
            resolution = MechanicsChoiceResolution(choice.choice_id, option, choice)


def locate(oracle, actor_id, cell):
    actor = oracle.state[NS + ".entities"][actor_id]
    body = next(
        e for e in oracle.state[NS + ".state"]["spatial_scene"]["entities"] if e["id"] == actor_id
    )
    dx, dy = cell[0] - actor["position_cell"][0], cell[1] - actor["position_cell"][1]
    for key in ("center", "muzzle"):
        actor[key][0] += dx
        actor[key][1] += dy
    actor["position_cell"] = list(cell)
    body["position"][0] += dx
    body["position"][1] += dy


def paired_fixture(package, fixture):
    oracle = Oracle(package)
    world, entities = oracle.state[NS + ".state"], oracle.state[NS + ".entities"]
    keep = {"vesper", "hex", "hostile_alpha"}
    world["spatial_scene"]["entities"] = [
        e for e in world["spatial_scene"]["entities"] if e["id"] in keep
    ]
    world["spatial_scene"]["geometry"] = deepcopy(fixture.get("geometry", []))
    world["spatial_scene"]["zones"] = []
    world["spatial_scene"]["map"]["bounds"]["max"][0] = fixture.get("map_width", 60)
    world["turn_roster"] = sorted(keep)
    for actor_id, position in fixture.get("positions", {}).items():
        locate(oracle, actor_id, position)
    if fixture.get("elevations"):
        board = world["spatial_scene"]["map"]
        board["bounds"]["max"][2] = 6
        board["layers"].append({"id": "upper", "min_elevation": 3, "max_elevation": 6})
        for actor_id, elevation in fixture["elevations"].items():
            body = next(e for e in world["spatial_scene"]["entities"] if e["id"] == actor_id)
            delta = elevation - body["position"][2]
            body["position"][2] = elevation
            entities[actor_id]["center"][2] += delta
            entities[actor_id]["muzzle"][2] += delta
    for actor_id, values in fixture.get("actor_values", {}).items():
        entities[actor_id].update(values)
    if fixture.get("magazine") is not None:
        entities["vesper"]["weapon"]["magazine"]["rounds"] = fixture["magazine"]
    params = default_manifest().seed_set()
    assert params.hash == "a7cad644191f421b98014f62202b16e6f536a8cb68596deb98d33f069e45bb1e"
    actors = {}
    for actor_id in sorted(keep):
        data = entities[actor_id]
        weapon = weapon_state(data["weapon"]["id"], params)
        weapon.magazine_rounds = data["weapon"]["magazine"]["rounds"]
        weapon.chamber = data["weapon"]["chamber"]
        weapon.current_recoil = data["weapon"]["current_recoil"]
        weapon.needs_cycle = data["weapon"]["needs_cycle"]
        weapon.cycle_ap = data["weapon"].get("cycle_ap")
        weapon.location, weapon.controlled_by = (
            data["weapon"]["location"],
            data["weapon"]["controlled_by"],
        )
        body = next(e for e in world["spatial_scene"]["entities"] if e["id"] == actor_id)
        actors[actor_id] = ActorState(
            actor_id,
            Side.A if data["side"] == "allies" else Side.B,
            data["class_id"],
            data["level"],
            fixture["id"] + ":" + actor_id,
            data["skills"]["marksmanship"],
            data["aptitudes"]["precision"],
            data["hp"],
            data["hp"],
            data["res"],
            data["res"],
            data["ap"],
            data["max_ap"],
            Position(*data["position_cell"]),
            stance=Stance(data["stance"]),
            weapon=weapon,
            facing=tuple(body["facing"]),
            elevation=body["position"][2],
        )
        actors[actor_id].muzzle_offset = tuple(
            data["muzzle"][i] - data["center"][i] for i in range(2)
        )
        actors[actor_id].specialty_nodes = set(data["specialties"])
        actors[actor_id].active = data["active"]
    state, grid, _ = tactical._build_control_state(tactical.TeamScenarioSpec(), params)
    state.actors = actors
    state.scenario_id, state.scenario_version = fixture["id"], "1"
    grid = GridMap(
        world["spatial_scene"]["map"]["id"],
        fixture.get("map_width", 60),
        60,
        host_scene=deepcopy(world["spatial_scene"]),
    )
    state.covers = grid.covers
    for item in grid.host_scene["geometry"]:
        metadata = item["metadata"]
        if "material_resistance" in metadata:
            volume = item["volume"]
            cover = CoverObject(
                item["id"],
                set(),
                volume["max"][2],
                metadata["material_resistance"],
                metadata.get("cover_hp"),
                active=item["active"],
            )
            state.covers[cover.cover_id] = cover
    for key in ("aim_ap", "flank_complete", "moved_this_turn", "last_action", "last_fire_weapon"):
        state.objective[key] = {
            a: False if "complete" in key or "moved" in key else 0 if key == "aim_ap" else None
            for a in actors
        }
    for key in ("attacked_targets_this_turn", "quick_solution_targets_this_turn"):
        state.objective[key] = {a: [] for a in actors}
    state.objective["hold_seconds"] = {"A": 0.0, "B": 0.0}
    state.objective["actor_objectives"] = {
        a: Position(*fixture.get("known_approach", [5, 2])) for a in actors
    }
    engine = EventEngine(state, params, fixture.get("seed", 5801))
    metrics = tactical._blank_metrics(state)
    return oracle, engine, grid, metrics


def decision(action, *, selection=None, destination=None, mode=None, order=(), budget=1):
    return PolicyDecision(
        action,
        None,
        mode,
        0,
        Position(*destination) if destination else None,
        "OVERWATCH-LAB-PARITY-V1 explicit fixture",
        selection,
        tuple(order),
        budget,
    )


def lab_action(engine, grid, metrics, actor, command, identity, roll=10):
    from .rng import RandomStream

    with patch.object(RandomStream, "d20", return_value=roll):
        return tactical._execute_decision(
            engine,
            grid,
            {a: "POLICY_PREPARED_FIRE" for a in engine.state.actors},
            actor,
            command,
            metrics,
            operation_id=identity,
        )


def setup_pair(oracle, engine, grid, metrics, actor, selection, identity):
    body = next(
        e for e in oracle.state[NS + ".state"]["spatial_scene"]["entities"] if e["id"] == actor
    )
    oracle.execute(
        "prepare_overwatch_lane",
        {
            "actor": actor,
            "lane": [
                {
                    "pattern": selection.pattern,
                    "direction": selection.direction,
                    "extent": selection.extent,
                    "half_width": (selection.width - 1) // 2,
                    "origin": body["position"],
                    "facing": body["facing"],
                }
            ],
        },
    )
    # Drive the actual policy chooser as well as its decision executor.
    engine.state.objective["overwatch_policy"][actor] = asdict(selection)
    observation = tactical._policy_observation(actor, engine.state, grid)
    command = choose_action("POLICY_PREPARED_FIRE", observation)
    assert command.action == "OVERWATCH" and command.overwatch == selection
    entry = lab_action(engine, grid, metrics, actor, command, identity)
    assert entry.rejected_reason is None, entry.rejected_reason
    watch = next(p for p in engine.state.prepared.values() if p.owner_id == actor)
    td_watch = oracle.state[NS + ".state"]["prepared"]["overwatchers"][actor]
    td_zone = next(
        z
        for z in oracle.state[NS + ".state"]["spatial_scene"]["zones"]
        if z["id"] == td_watch["zone_id"]
    )
    assert watch.lane_zone["shape"] == td_zone["shape"]
    return command


def projection(oracle, engine):
    expected, actual = {}, {}
    world = oracle.state[NS + ".state"]
    for actor_id, unit in engine.state.actors.items():
        data = oracle.state[NS + ".entities"][actor_id]
        expected[actor_id] = {k: data[k] for k in ("hp", "res", "ap", "downed", "stance")}
        actual[actor_id] = {
            "hp": unit.hp,
            "res": unit.res,
            "ap": unit.ap,
            "downed": unit.downed,
            "stance": unit.stance.value,
        }
        expected[actor_id]["position"] = data["center"]
        actual[actor_id]["position"] = overwatch.point(unit)
        for side, weapon in ((expected, data["weapon"]), (actual, unit.weapon)):
            side[actor_id]["magazine"] = (
                weapon["magazine"]["rounds"] if isinstance(weapon, dict) else weapon.magazine_rounds
            )
            side[actor_id]["chamber"] = (
                weapon["chamber"] if isinstance(weapon, dict) else weapon.chamber
            )
            side[actor_id]["recoil"] = (
                weapon["current_recoil"] if isinstance(weapon, dict) else weapon.current_recoil
            )
            side[actor_id]["needs_cycle"] = (
                weapon["needs_cycle"] if isinstance(weapon, dict) else weapon.needs_cycle
            )
        td_watch = world["prepared"].get("overwatchers", {}).get(actor_id)
        lab_watch = next(
            (p for p in engine.state.prepared.values() if p.owner_id == actor_id), None
        )
        if td_watch:
            expected[actor_id]["watch"] = [td_watch["active"], td_watch["shots"]]
            actual[actor_id]["watch"] = [lab_watch.active, lab_watch.shots_remaining]
    expected["cover"] = {
        g["id"]: [g["active"], g["metadata"].get("cover_hp")]
        for g in world["spatial_scene"]["geometry"]
    }
    actual["cover"] = {
        key: [cover.active, cover.cover_hp] for key, cover in engine.state.covers.items()
    }
    return expected, actual


def run_fixture(package, fixture, output):
    oracle, engine, grid, metrics = paired_fixture(package, fixture)
    selections = fixture.get("watches", {"vesper": fixture.get("selection")})
    for index, (actor, values) in enumerate(selections.items()):
        selection = OverwatchSelection(**values, fixture_id=fixture["id"])
        setup_pair(oracle, engine, grid, metrics, actor, selection, f"setup-{index}")
    checks = []
    for index, step in enumerate(fixture.get("steps", [])):
        actor = step.get("actor", "hostile_alpha")
        action = step["action"]
        engine.state.active_side = engine.state.actors[actor].side
        oracle.state[NS + ".state"]["active_side"] = (
            "allies" if engine.state.active_side is Side.A else "hostiles"
        )
        if action == "MOVE":
            command = decision(
                "MOVE_OBJECTIVE",
                destination=step["destination"],
                order=step.get("order", ()),
                budget=step.get("budget", 6),
            )
            oracle_result = oracle.execute(
                "move_actor",
                {"actor": actor, "destination": step["destination"]},
                roll=step.get("roll", 10),
                order=step.get("order", ()),
            )
            entry = lab_action(
                engine, grid, metrics, actor, command, f"step-{index}", step.get("roll", 10)
            )
            assert entry.rejected_reason is None, entry.rejected_reason
            observed = [
                {k: r[k] for k in ("owner", "point", "kind")}
                for w in entry.trigger_windows
                for r in w.get("reactions", [])
            ]
            shot_count = len(oracle_result.get("shot_effects", {}).get("shots", []))
            expected_reactions = (
                oracle.state[NS + ".state"]["last_trigger"].get("reactions", [])
                if shot_count
                else []
            )
            assert observed == expected_reactions, (observed, expected_reactions)
        elif action == "STANCE":
            oracle.execute("change_stance", {"actor": actor, "stance": step["stance"]})
            entry = lab_action(
                engine,
                grid,
                metrics,
                actor,
                decision("STANCE", mode=step["stance"]),
                f"step-{index}",
            )
            assert entry.rejected_reason is None
        elif action == "CANCEL":
            oracle.execute("cancel_overwatch_lane", {"actor": actor})
            lab_action(engine, grid, metrics, actor, decision("CANCEL_OVERWATCH"), f"step-{index}")
        elif action == "REPLACE":
            selection = OverwatchSelection(**step["selection"], fixture_id=fixture["id"])
            setup_pair(oracle, engine, grid, metrics, actor, selection, f"step-{index}")
        elif action == "TURN":
            next_side = step["side"]
            oracle.execute("end_allies_turn" if next_side == "B" else "end_hostiles_turn", {})
            tactical._expire_owner_preparations(engine.state, Side(next_side))
            for a in engine.state.actors.values():
                a.ap = a.max_ap if a.side.value == next_side else 0
        else:
            raise ValueError("unsupported fixture step: " + action)
        expected, actual = projection(oracle, engine)
        checks.append({"step": index, "expected": expected, "actual": actual})
        assert actual == expected, json.dumps(checks[-1], indent=2)
    output.write_text(
        json.dumps(
            {
                "fixture": fixture,
                "checks": checks,
                "lab_event_log_hash": engine.log_hash(),
                "lab_final_state_hash": engine.state.semantic_hash(),
                "lab_events": [e.to_dict() for e in engine.log],
                "parameter_hash": engine.parameters.hash,
                "runtime_sha256": RUNTIME_SHA,
                "package_sha256": hashlib.sha256(Path(package).read_bytes()).hexdigest(),
                "oracle_final_state_sha256": hashlib.sha256(
                    json.dumps(oracle.state, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    return {
        "fixture_id": fixture["id"],
        "status": "PASS",
        "comparisons": len(checks),
        "event_log_hash": engine.log_hash(),
        "final_state_hash": engine.state.semantic_hash(),
    }


def run(package, fixtures, output):
    output.mkdir(parents=True, exist_ok=False)
    parameters = default_manifest().seed_set()
    (output / "parameter-set.json").write_text(
        json.dumps({"hash": parameters.hash, "values": parameters.values}, indent=2) + "\n"
    )
    (output / "execution-contract.json").write_text(
        json.dumps(
            {
                "package": {
                    "path": str(package),
                    "sha256": hashlib.sha256(Path(package).read_bytes()).hexdigest(),
                    "bytes": package.stat().st_size,
                },
                "runtime_sha256": RUNTIME_SHA,
                "parameter_hash": parameters.hash,
                "policy_fixture": {
                    "path": str(fixtures),
                    "sha256": hashlib.sha256(fixtures.read_bytes()).hexdigest(),
                },
                "entry": [
                    "balance_suite.__main__",
                    "balance_suite.cli",
                    "balance_suite.runner",
                    "balance_suite.tactical._execute_decision",
                ],
                "geometry": "sylvia_gm.spatial._grid_lane and SpatialEngine exposure_path/exposure/ray/path",
                "dice_policy": "Each named step fixes the same d20 on both implementations; default 10. Seeded replay is tested separately.",
                "critical_policy": "DEEP_HIT is an explicit test choice, not a canonical preference.",
                "scope": "finite adoption only; no tuning, cohorts, native or release qualification",
            },
            indent=2,
        )
        + "\n"
    )
    records = []
    for fixture in json.loads(fixtures.read_text(encoding="utf-8"))["fixtures"]:
        try:
            result = run_fixture(package, fixture, output / (fixture["id"] + ".json"))
        except Exception as error:
            result = {
                "fixture_id": fixture["id"],
                "status": "FAIL",
                "error": type(error).__name__,
                "detail": str(error),
            }
        records.append(result)
        (output / "results.json").write_text(json.dumps(records, indent=2) + "\n")
    return {
        "passed": sum(r["status"] == "PASS" for r in records),
        "failed": sum(r["status"] == "FAIL" for r in records),
        "records": records,
    }
