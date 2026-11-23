"""Generate vectors for sylvia-app::package_mechanic's player-action checker and previewer and
sylvia-app::tactical_grid's player-known scene, from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_player_action_check_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source. It shares `generate_package_mechanic_vectors.py`'s runtime table,
patches and replay helpers.

Three parts:

- **scenes:** `player_known_spatial_scene(value, retain_metadata=...)` and
  `player_known_spatial_value(value)` over scene variants with public, hidden, anchored and
  shaped displays.
- **cases:** targeted checks and previews on minimal runtimes: availability, prerequisites,
  checks, calculations, queries, preflights (refusals, failures, nested calls, the player view),
  previews and their cards.
- **recorded:** contexts kept from Python's own player-action, SRD Lite and Riichi tests
  (`--record`, this file as a pytest plugin), replayed.

A check records `{"reason": ...}`, a preview `{"preview": ...}`, materializing
`{"materialize": [class, message]}`. `{"pending": ...}` is where the run reached code the port
refuses by name until its spatial effect handlers land: a spatial effect (its name), the
controls' spatial queries (`queries`) or a spatial engine for a preview (`preflight_path`,
`map_preview`). The generator makes those raise a `BaseException`, which no Python catch-all
catches, as the port's `PortPending` passes through.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "crates/sylvia-engine/tests/vectors"))

import generate_package_mechanic_vectors as mechanic  # noqa: E402

OUTPUT = HERE / "player_action_check_vectors.json"
RECORDING_ENV = "SYLVIA_PLAYER_CHECK_RECORDING"
RECORDED_TESTS = [
    "tests/test_player_action_availability.py", "tests/test_player_action_preflight.py",
    "tests/test_player_actions.py", "tests/test_player_preflight_refusal.py", "tests/test_party_action_control.py",
    "tests/test_equipment_inventory_actions.py", "tests/test_result_card_actions.py",
    "tests/test_srd_movement_preview_route.py", "tests/test_riichi_mahjong.py", "tests/test_action_sequences.py",
]
PER_TOOL = 4


# ── Recording (pytest plugin) ─────────────────────────────────────────────────────────────


def pytest_configure(config):  # noqa: ARG001 - pytest hook
    """Keep a bounded sample of the contexts each player checker and previewer receives."""
    import sylvia_gm.application.authority as authority
    import sylvia_gm.application.package_runtime as pr

    path = os.environ[RECORDING_ENV]
    counts: dict[tuple, int] = {}

    def wrap(kind, original):
        def materialize(**arguments):
            control = original(**arguments)
            runtime = arguments["runtime_bytes"]
            digest = hashlib.sha256(runtime).hexdigest()
            key = (kind, arguments["package_id"], arguments["declaration"].name, digest)

            def call(parameters, state, participants):
                counts[key] = counts.get(key, 0) + 1
                if counts[key] <= 3 * PER_TOOL:
                    with open(path, "a", encoding="utf-8") as sink:
                        sink.write(json.dumps({
                            "kind": kind, "package_id": arguments["package_id"],
                            "declaration": mechanic.declaration_json(arguments["declaration"]),
                            "runtime_sha256": digest,
                            "runtime_hex": runtime.hex() if len(runtime) <= 64 * 1024 else None,
                            "parameters": mechanic._thaw(parameters), "state": mechanic._thaw(state),
                            "participants": mechanic._thaw(participants),
                        }, ensure_ascii=True, allow_nan=False) + "\n")
                return control(parameters, state, participants)

            return call

        return materialize

    for kind, name in (("check", "materialize_player_action_checker"), ("preview", "materialize_player_action_previewer")):
        wrapped = wrap(kind, getattr(pr, name))
        setattr(pr, name, wrapped)
        setattr(authority, name, wrapped)


def record() -> list[dict]:
    sink = HERE / "player_check_recording.jsonl"
    sink.unlink(missing_ok=True)
    basetemp = ROOT / ".player-check-recording-tmp"
    environment = {**os.environ, RECORDING_ENV: str(sink), "PYTHONPATH": str(HERE)}
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "generate_player_action_check_vectors",
                    "-p", "no:cacheprovider", f"--basetemp={basetemp}", *RECORDED_TESTS],
                   cwd=ROOT, env=environment, check=False)
    known = mechanic.known_runtimes()
    kept: dict[str, list[dict]] = {}
    seen = set()
    for line in (sink.read_text(encoding="utf-8").splitlines() if sink.exists() else []):
        item = json.loads(line)
        digest, runtime_hex = item.pop("runtime_sha256"), item.pop("runtime_hex")
        if digest in known:
            item["runtime"] = known[digest]
        elif runtime_hex is not None:
            item["runtime"] = mechanic.stored(bytes.fromhex(runtime_hex))
        else:
            continue
        identity = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        group = kept.setdefault(f"{item['kind']} {item['package_id']} {item['declaration']['name']} {digest}", [])
        if len(group) < PER_TOOL:
            group.append(item)
    sink.unlink(missing_ok=True)
    import shutil

    shutil.rmtree(basetemp, ignore_errors=True)
    return [item for key in sorted(kept) for item in kept[key]]


# ── Running one case ──────────────────────────────────────────────────────────────────────


def install_patches() -> None:
    import sylvia_gm.application.package_runtime as pr

    mechanic.install_patches()


def run(case: dict) -> dict:
    """Materialize and call one checker or previewer as `AuthorityOwner` does."""
    import sylvia_gm.application.package_runtime as pr
    from sylvia_gm.application.player_actions import parse_player_controls
    from sylvia_gm.domain.authority import owned_payload

    data = mechanic.runtime_bytes(case["runtime"])
    materialize = (pr.materialize_player_action_checker if case["kind"] == "check"
                   else pr.materialize_player_action_previewer)
    try:
        control = materialize(package_id=case["package_id"], declaration=mechanic.declaration_of(case["declaration"]),
                              runtime_bytes=data)
    except Exception as error:  # noqa: BLE001 - every exception class is behaviour here
        return {"materialize": [type(error).__name__, str(error)]}
    try:
        result = control(owned_payload(case["parameters"]), owned_payload(case["state"]),
                         owned_payload(case["participants"]))
    except mechanic.PortPending as pending:
        return {"pending": pending.args[0]}
    if case["kind"] == "check":
        return {"reason": result}
    return {"preview": None if result is None else mechanic._thaw(result)}


# ── The player-known scene ────────────────────────────────────────────────────────────────


def scene_cases() -> list[dict]:
    from generate_spatial_scene_vectors import BASE

    from sylvia_gm.application.tactical_grid import player_known_spatial_scene, player_known_spatial_value

    def player(label="Orc", kind="creature", **extra):
        return {"player": {"label": label, "kind": kind, **extra}}

    displays = [
        player(), player(state="hurt", side="foe", interactive=True), player(label=" Orc"), player(label=""),
        player(label="x" * 257), player(label="é" * 128), player(label="é" * 129), player(kind=" k"),
        player(kind="k" * 65), player(state=""), player(state=" s"), player(color=5), player(interactive="yes"),
        player(extra=1), {"player": "Orc"}, {"name": "no player"}, player(height_label="tall", material_label="stone"),
        player(stance="prone", icon="skull"), {**player(), "display_anchor": "orc"}, {**player(), "display_anchor": 5},
        {**player(), "display_anchor": "ghost"},
        player(display_geometry={"kind": "cone", "origin": [5, 5, 0], "direction": [1, 0, 0], "length": 4, "angle": 60}),
        player(display_geometry={"kind": "sphere", "center": [5, 5, 0], "radius": 2}),
        player(display_geometry={"kind": "cone"}), player(display_geometry="cone"),
        player(display_geometry={"kind": "prism", "base": [[0, 0], [2, 0], [2, 2]], "bottom": 0, "top": 3}),
        player(interactive=False),
    ]
    cases = []

    def add(name, value):
        record = {"name": name, "value": value}
        for retain in (False, True):
            try:
                record[f"scene_{retain}"] = {"result": mechanic._thaw(player_known_spatial_scene(value, retain_metadata=retain))}
            except Exception as error:  # noqa: BLE001
                record[f"scene_{retain}"] = {"error": [type(error).__name__, str(error)]}
        try:
            record["value_result"] = {"result": mechanic._thaw(player_known_spatial_value(
                {"outer": [value, {"format": 1.0, "map": 1}], "n": 1, "scene": value}))}
        except Exception as error:  # noqa: BLE001
            record["value_result"] = {"error": [type(error).__name__, str(error)]}
        cases.append(record)

    add("base", BASE)
    for index, display in enumerate(displays):
        scene = copy.deepcopy(BASE)
        scene["entities"][0]["metadata"] = display
        scene["geometry"][0]["metadata"] = display
        scene["zones"][0]["metadata"] = display
        add(f"display {index}", scene)
    # Zone anchors follow active, displayed entities only.
    scene = copy.deepcopy(BASE)
    scene["entities"][0]["metadata"] = player()
    scene["entities"][1]["metadata"] = player(label="Cart")
    scene["zones"][0]["metadata"] = {**player(label="Aura"), "display_anchor": "orc"}
    scene["zones"][1]["metadata"] = {**player(label="Ghost aura"), "display_anchor": "cart"}
    add("anchors", scene)
    add("format true", {**BASE, "format": True})
    add("invalid scene", {**BASE, "map": {"id": "keep"}})
    add("not a scene", {"format": 2, "map": {}, "entities": [], "geometry": [], "zones": [], "terrain": []})
    return cases


# ── Targeted checks and previews ──────────────────────────────────────────────────────────


def targeted() -> list[dict]:
    import yaml
    from generate_spatial_scene_vectors import BASE

    record, base = mechanic.fixture_base()
    package_id = record["id"]
    minimal = {"sylvia_gm_runtime": base["sylvia_gm_runtime"], "tools": {}}
    scene = copy.deepcopy(BASE)
    scene["entities"][0]["metadata"] = {"player": {"label": "Orc", "kind": "creature"}, "speed": 30}
    scene["entities"][1]["metadata"] = {"speed": 99}
    state = {
        f"{package_id}.state": {"counter": 1, "mode": "duel", "scene": scene, "budget": {"moves": 2}},
        f"{package_id}.entities": {"orc": {"hp": 5, "max_hp": 9, "name": "Orc", "map": {"scene": scene}},
                                   "elf": {"hp": 7, "max_hp": 7, "name": "Elf"}},
        f"{package_id}.combat": {"round": 2, "leader": "orc", "scene": scene},
    }
    participants = {"pc": {"package_id": package_id, "data": {"hp": 3, "name": "Pc"}}}
    target = {"name": "target", "type": "string", "resolve": "entity"}
    cases: list[dict] = []

    def document(tools: dict) -> dict:
        edited = copy.deepcopy(minimal)
        edited["tools"].update(copy.deepcopy(tools))
        return mechanic.stored(yaml.safe_dump(edited, sort_keys=False, allow_unicode=True).encode("utf-8"))

    def case(label, controls, *, effects=(), parameters=(target,), pre=None, extra=None, values=None,
             state_value=None, kinds=("check", "preview")):
        tool = {"description": "Synthetic.", "parameters": [dict(p) for p in parameters], "effects": list(effects),
                "metadata": {"player_controls": controls}}
        if pre is not None:
            tool["pre_conditions"] = pre
        tools = {"act": tool, **(extra or {})}
        runtime = document(tools)
        data = {**minimal, "tools": tools}
        try:
            declaration = mechanic.declaration_for(data, "act", package_id)
        except Exception as error:  # noqa: BLE001
            raise SystemExit(f"{label}: the targeted document does not load: {type(error).__name__}: {error}") from error
        for kind in kinds:
            item = {"name": f"{label} {kind}", "kind": kind, "package_id": package_id, "declaration": declaration,
                    "runtime": runtime, "parameters": values if values is not None else {"target": "orc"},
                    "state": state if state_value is None else state_value, "participants": participants}
            cases.append({**item, **run(item)})

    card = {"version": 1, "title": "Move", "summary": [{"label": "Left", "value": {"path": "context._left"}},
                                                         {"label": "Cost", "value": {"path": "context._cost"}, "optional": True},
                                                         {"label": "Who", "value": {"path": "parameters.target"}}]}
    calc = [{"set": {"field": "_left", "value": "state.budget.moves - 1"}},
            {"set": {"field": "_speed", "value": "target.map.scene.entities[0].metadata.speed"}},
            {"condition": "target.hp > 100", "set": {"field": "_never", "value": "1"}}]
    case("available", {"preview": card, "calculations": calc})
    case("unavailable", {"confirmation": "sometimes"})
    case("nothing to preview", {})
    case("checks", {"checks": [{"condition": "target.hp > 3", "reason": "Too weak"},
                               {"condition": "target.hp > 30", "reason": "Not strong enough"}], "preview": card,
                    "calculations": calc})
    case("check error", {"checks": [{"condition": "target.missing.deeper > 1", "reason": "x"}]})
    case("prerequisites", {"preview": card}, pre=[{"condition": "target.hp > 30", "error": "weak"}])
    case("unknown target", {"preview": card}, values={"target": "ghost"})
    case("calculation scalar", {"preview": card, "calculations": calc})
    case("calculation not scalar", {"preview": card, "calculations": [{"set": {"field": "_x", "value": "state.budget"}}]})
    case("calculation clash", {"preview": card, "calculations": [{"set": {"field": "_y", "value": "1"}}],
                               }, parameters=(target, {"name": "_y", "type": "integer", "default": 2}))
    case("calculation error", {"preview": card, "calculations": [{"set": {"field": "_x", "value": "1 / 0"}}]})
    case("calculation hidden entity", {"preview": {"version": 1, "title": "Speed", "summary": [
        {"label": "S", "value": {"path": "context._speed"}}]}, "calculations": [
        {"set": {"field": "_speed", "value": "len(state.scene.entities)"}}]})
    case("calculation scene", {"preview": {"version": 1, "title": "Count", "summary": [
        {"label": "N", "value": {"path": "context._n"}}]}, "calculations": [
        {"set": {"field": "_n", "value": "len(combat.scene.entities)"}}]})
    query = {"store_as": "_route", "spatial_query": {"scene": "state.scene", "operation": "path",
                                                    "query": {"map_id": "keep", "start": [0, 0], "goal": [10, 10],
                                                              "allow_diagonal": True, "corner_policy": "forbid_cutting",
                                                              "include_entities": True, "max_steps": 1000}}}
    route_card = {"version": 1, "title": "Route", "summary": [{"label": "Cost", "value": {"path": "context._cost"}, "optional": True},
                                                              {"label": "N", "value": {"path": "context._steps"}}]}
    route_calc = [{"set": {"field": "_steps", "value": "len(_route.cells)"}}]
    case("queries", {"preview": card, "queries": [query]})
    case("query clash", {"preview": card, "queries": [{**query, "store_as": "_y"}]},
         parameters=(target, {"name": "_y", "type": "integer", "default": 2}))
    case("map preview", {"queries": [query], "map_preview": {"path": "_route"}})
    case("query route", {"preview": route_card, "queries": [query], "calculations": route_calc})
    case("query route too long", {"preview": route_card, "calculations": route_calc,
                                  "queries": [{**query, "spatial_query": {**query["spatial_query"], "query": {
                                      **query["spatial_query"]["query"], "max_steps": 5000}}}]})
    case("query route invalid", {"preview": route_card, "calculations": route_calc,
                                 "queries": [{**query, "spatial_query": {**query["spatial_query"], "query": {
                                     **query["spatial_query"]["query"], "goal": [99, 99]}}}]})
    case("query bad scene", {"preview": route_card, "calculations": route_calc,
                             "queries": [{**query, "spatial_query": {**query["spatial_query"], "scene": "state.absent"}}]})
    # Preflights.
    helper = {"description": "Helper.", "parameters": [target, {"name": "steps", "type": "integer", "default": 1}],
              "effects": [{"set": {"field": "_cost", "value": "steps * 5"}}, {"set": {"field": "_label", "value": "'ok'"}},
                          # Python's evaluator has no exponent literals: an overflowing product is
                          # its way to a non-finite float.
                          {"set": {"field": "_big", "value": "1" + "0" * 200 + ".0 * 1" + "0" * 200 + ".0"}},
                          {"set": {"field": "_nan", "value": "_big - _big"}},
                          {"set": {"field": "_list", "value": [1]}}]}
    refuse = {"description": "Refuse.", "parameters": [target],
              "effects": [{"require": {"condition": "target.hp > 30", "error": "{target.name} cannot reach"}}]}
    unsafe = {"description": "Unsafe.", "parameters": [target], "effects": [{"require": {"condition": "false", "error": "bad\nline"}}]}
    broken = {"description": "Broken.", "parameters": [target], "effects": [{"set": {"field": "_x", "value": "1 / 0"}},
                                                                            {"heal": {"target": "'nobody'", "amount": 1}}]}
    rolls = {"description": "Rolls.", "parameters": [target], "effects": [{"dice": {"expression": "1d6"}, "store_as": "_r"}]}
    chain = {"description": "Chain.", "parameters": [target], "effects": [{"call": "helper", "params": {"target": "target"},
                                                                          "export_context": "*"}]}
    spatial = {"description": "Spatial.", "parameters": [target],
               "effects": [{"spatial_query": {"scene": "state.scene", "operation": "snapshot"}, "store_as": "_snap"}]}
    extra = {"helper": helper, "refuse": refuse, "unsafe": unsafe, "broken": broken, "rolls": rolls, "chain": chain,
             "spatial": spatial}
    for name in ("helper", "refuse", "unsafe", "broken", "rolls", "chain", "spatial"):
        case(f"preflight {name}", {"preview": card, "preflight": [name]}, extra=extra,
             parameters=(target, {"name": "steps", "type": "integer", "default": 3}))
    case("preflight two", {"preview": card, "preflight": ["helper", "chain"]}, extra=extra)
    case("preflight missing", {"preview": card, "preflight": ["absent"]}, extra=extra)
    case("preflight path", {"preview": card, "preflight": ["helper"],
                            "preflight_path": {"scene": "state.scene", "route": "_route", "cost": "_cost"}}, extra=extra)
    # Path and map previews (C3b), on the query scene with public displays.
    from generate_package_mechanic_vectors import arena_scene

    arena = arena_scene()
    arena_state = {**state, f"{package_id}.state": {**state[f"{package_id}.state"], "arena": arena}}
    route_query = {"spatial_query": {"scene": "state.arena", "operation": "circle_route", "query": {
        "map_id": "keep", "moving_entity_id": "orc", "destination": [7, 4, 0], "moving_radius": 0.5,
        "circles": [{"entity_id": "elf", "radius": 0.5, "blocking": True}], "max_tests": 100,
        "metadata_filter": {"key": "", "value": "", "missing_matches": False}}}, "store_as": "_route"}
    router = {"description": "Router.", "parameters": [target],
              "effects": [route_query, {"set": {"field": "_cost", "value": "_route.distance"}}]}
    routes = {"router": router}
    path_card = {"version": 1, "title": "Move", "summary": [{"label": "Cost", "value": {"path": "context._cost"}}]}
    preflight_path = {"scene": "state.arena", "route": "_route", "cost": "_cost"}
    case("path preview", {"preview": path_card, "preflight": ["router"], "preflight_path": preflight_path},
         extra=routes, state_value=arena_state)
    blocked = {"router": {**router, "effects": [route_query, {"set": {"field": "_cost", "value": -1}}]}}
    case("path preview negative cost", {"preflight": ["router"], "preflight_path": preflight_path}, extra=blocked,
         state_value=arena_state)
    unrouted = {"router": {**router, "effects": [{"set": {"field": "_route", "value": {"found": False}}},
                                                 {"set": {"field": "_cost", "value": 1}}]}}
    case("path preview without a route", {"preflight": ["router"], "preflight_path": preflight_path}, extra=unrouted,
         state_value=arena_state)
    for label, points in (("one point", [[1, 1, 0]]), ("two points", [[1, 1, 0], [2, 1.5, 0]]),
                          ("outside bounds", [[1, 1, 0], [99, 1, 0]])):
        made = {"router": {**router, "effects": [
            {"set": {"field": "_route", "value": {"found": True, "truncated": False, "points": points}}},
            {"set": {"field": "_cost", "value": 1.5}}]}}
        case(f"path preview {label}", {"preview": path_card, "preflight": ["router"], "preflight_path": preflight_path},
             extra=made, state_value=arena_state)
    arena_path = {"store_as": "_walk", "spatial_query": {"scene": "state.arena", "operation": "path", "query": {
        "map_id": "keep", "start": [5, 5], "goal": [15, 10], "allow_diagonal": True, "corner_policy": "forbid_cutting",
        "include_entities": True, "moving_entity_id": "orc", "max_steps": 1000}}}
    arena_ray = {"store_as": "_sight", "spatial_query": {"scene": "state.arena", "operation": "ray", "query": {
        "map_id": "keep", "origin": [1, 1, 1], "destination": [12, 10, 1], "include": ["entities", "geometry"],
        "max_intersections": 8}}}
    arena_exposure = {"store_as": "_seen", "spatial_query": {"scene": "state.arena", "operation": "exposure", "query": {
        "map_id": "keep", "observer": {"point": [4, 8, 1]}, "target": {"entity_id": "elf"},
        "samples": {"horizontal": 3, "vertical": 2}, "include": ["entities", "geometry"]}}}
    arena_zones = {"store_as": "_zones", "spatial_query": {"scene": "state.arena", "operation": "crossed_zones", "query": {
        "map_id": "keep", "path": [[0, 0, 0], [15, 10, 0.2]], "max_crossings": 10}}}
    case("map preview path", {"queries": [arena_path], "map_preview": {"path": "_walk"}}, state_value=arena_state)
    case("map preview path and zones", {"queries": [arena_path, arena_zones],
                                        "map_preview": {"path": "_walk", "crossed_zones": "_zones"}}, state_value=arena_state)
    case("map preview ray and exposure", {"queries": [arena_ray, arena_exposure],
                                          "map_preview": {"ray": "_sight", "exposure": "_seen"}}, state_value=arena_state)
    case("map preview mixed maps", {"queries": [arena_ray, {**query, "store_as": "_other"}],
                                    "map_preview": {"ray": "_sight", "path": "_other"}}, state_value=arena_state)
    case("preflight path missing scene", {"preview": card, "preflight": ["helper"],
                                          "preflight_path": {"scene": "state.absent", "route": "_route", "cost": "_cost"}}, extra=extra)
    case("preflight big float", {"preview": {"version": 1, "title": "Big", "summary": [
        {"label": "B", "value": {"path": "context._big"}, "optional": True},
        {"label": "N", "value": {"path": "context._nan", "labels": {"nan": "x"}}, "optional": True},
        {"label": "C", "value": {"path": "context._cost"}}]}, "preflight": ["helper"]}, extra=extra)
    bound = [target, {"name": "mode", "type": "string", "from_state": "state.mode"}]
    case("bound parameters", {"preview": {"version": 1, "title": {"path": "context._mode"}},
                              "calculations": [{"set": {"field": "_mode", "value": "mode"}}]}, parameters=bound)
    case("bound missing", {"preview": card}, parameters=[target, {"name": "gone", "type": "string", "from_state": "state.gone"}])
    case("bad state", {"preview": card}, state_value={**state, f"{package_id}.state": 5})
    # The gaps behind the first sabotage pass.
    case("read through the view", {"preview": {"version": 1, "title": "Seen", "summary": [
        {"label": "N", "value": {"path": "context._n"}}]}, "calculations": [{"set": {"field": "_n", "value": "len(seen)"}}]},
         parameters=[target, {"name": "seen", "type": "array", "items": {"type": "object"}, "from_state": "state.scene.entities"}])
    case("query clash entity variable", {"preview": card, "queries": [{**query, "store_as": "_target_id"}]})
    case("calculation clash entity variable", {"preview": card,
                                               "calculations": [{"set": {"field": "_target_id", "value": "1"}}]})
    huge = "1" + "0" * 200 + ".0"
    case("calculation infinite", {"preview": card, "calculations": [{"set": {"field": "_x", "value": f"{huge} * {huge}"}}]})
    probe = {"description": "Probe.", "parameters": [target],
             "effects": [{"set": {"field": "_seen_bonus", "value": "bonus"}}, {"set": {"field": "_ratio", "value": 0.5}},
                         {"set": {"field": "_cost", "value": 15}}]}
    marker = {"description": "Marker.", "parameters": [], "effects": [{"set": {"field": "combat.mark", "value": 7}}]}
    outer = {"description": "Outer.", "parameters": [target],
             "effects": [{"call": "marker", "params": {}}, {"set": {"field": "_mark", "value": "combat.mark"}}]}
    probes = {"probe": probe, "marker": marker, "outer": outer}
    probe_card = {"version": 1, "title": "Probe", "summary": [
        {"label": "Bonus", "value": {"path": "context._seen_bonus"}}, {"label": "Ratio", "value": {"path": "context._ratio"}},
        {"label": "Cost", "value": {"path": "context._cost"}}, {"label": "Mark", "value": {"path": "context._mark"}, "optional": True}]}
    case("preflight probe", {"preview": probe_card, "preflight": ["probe"],
                             "calculations": [{"set": {"field": "_cost", "value": "99"}}]},
         extra=probes, parameters=(target, {"name": "bonus", "type": "integer", "default": 4}))
    case("preflight shared combat", {"preview": probe_card, "preflight": ["probe", "outer"]}, extra=probes,
         parameters=(target, {"name": "bonus", "type": "integer", "default": 4}))
    case("preflight path only", {"preflight": ["helper"],
                                 "preflight_path": {"scene": "state.scene", "route": "_route", "cost": "_cost"}}, extra=extra)
    case("preflight infinite in card", {"preview": {"version": 1, "title": "Big", "summary": [
        {"label": "B", "value": {"path": "context._big"}}]}, "preflight": ["helper"]}, extra=extra)
    case("card on raw parameters", {"preview": {"version": 1, "title": "Mode", "summary": [
        {"label": "M", "value": {"path": "parameters.mode"}, "optional": True}, {"label": "T", "value": {"path": "parameters.target"}}]}},
         parameters=bound)
    case("invalid scene in view", {"preview": card, "calculations": calc},
         state_value={**state, f"{package_id}.entities": {**state[f"{package_id}.entities"],
                                                           "orc": {"hp": 5, "max_hp": 9, "name": "Orc",
                                                                   "map": {"scene": {**scene, "map": {"id": "x"}}}}}})
    return cases


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    install_patches()
    if "--record" in sys.argv:
        recorded = record()
    else:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
        recorded = previous.get("recorded_inputs", [])
        mechanic.DOCUMENTS.update({digest: previous["documents"][digest] for item in recorded
                                   if (digest := item["runtime"].get("document")) is not None})
    scenes = scene_cases()
    cases = targeted()
    replayed = [run(item) for item in recorded]
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "documents": dict(sorted(mechanic.DOCUMENTS.items())),
                                    "scenes": scenes, "cases": cases, "recorded_inputs": recorded, "recorded": replayed},
                                   ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    kinds: dict[str, int] = {}
    for item in [*cases, *replayed]:
        key = next(k for k in ("materialize", "pending", "reason", "preview") if k in item)
        if key == "reason":
            key = "available" if item["reason"] is None else "refused"
        if key == "preview":
            key = "previewed" if item["preview"] is not None else "no preview"
        kinds[key] = kinds.get(key, 0) + 1
    print(f"{len(scenes)} scenes, {len(cases)} targeted, {len(recorded)} recorded: {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
