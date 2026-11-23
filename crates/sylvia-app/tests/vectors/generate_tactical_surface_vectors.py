"""Generate vectors for sylvia-app::tactical_grid's projections and selection binding,
sylvia-app::piece_table and sylvia-app::map_segment_preview from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_tactical_surface_vectors.py [--record]

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case calls one function: `project_tactical_grid` (`grid`), `project_coordinate_map`
(`coordinate`), `coordinate_actor_reference` (`actor_reference`),
`resolve_tactical_action_parameters` (`tactical`), `project_piece_table` (`piece_table`),
`resolve_piece_action_parameters` (`piece_action`) or `segment_preview` (`segment`). The record
is `{"ok": result}` or `{"error": [class, str(error)]}`.

Three sources of inputs:

- **Targeted cases** on the spatial query scene with public displays: every projection rule,
  every selection source and refusal, owner paths, occupied space, piece tables and segments.
- **Seeded random cases**: mutated surface data, selections and piece tables.
- **Recorded calls** from Python's own surface tests (`--record`): this file runs as a pytest
  plugin and keeps the arguments each function received; the default run replays the kept
  arguments, so regenerating does not rerun pytest. A call whose arguments hold a lone surrogate
  is skipped: a Rust string cannot hold one (differences row 3).

Every input is frozen (`owned_json_value`) before the call, as the owner's surface data, actions
and projections are, except an occupied-space declaration, which is thawed as its shipped
`literal` source is. Large values are kept once, by SHA-256, in `documents` (`{"$doc": digest}`).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "tactical_surface_vectors.json"
RECORDING_ENV = "SYLVIA_SURFACE_RECORDING"
RECORDED_TESTS = [
    "tests/test_coordinate_actor_references.py", "tests/test_coordinate_map_ui.py",
    "tests/test_coordinate_occupied_space.py", "tests/test_map_segment_preview.py", "tests/test_piece_table.py",
    "tests/test_public_zone_display_anchor.py", "tests/test_public_zone_display_geometry.py",
    "tests/test_riichi_tile_display.py", "tests/test_spatial_preview.py",
    "tests/test_srd_lite_pending_area_preview.py", "tests/test_srd_lite_persistent_spell_map.py",
    "tests/test_retained_area_feedback_986.py", "tests/test_retained_scene_area_1132.py",
    "tests/test_player_action_preflight.py", "tests/test_equipment_inventory_actions.py",
    "tests/test_riichi_mahjong.py",
]
PER_FUNCTION = 60
DOCUMENT_BYTES = 512
DOCUMENTS: dict[str, object] = {}

FUNCTIONS = {
    "grid": ("tactical_grid", "project_tactical_grid"),
    "coordinate": ("tactical_grid", "project_coordinate_map"),
    "actor_reference": ("tactical_grid", "coordinate_actor_reference"),
    "tactical": ("tactical_grid", "resolve_tactical_action_parameters"),
    "piece_table": ("piece_table", "project_piece_table"),
    "piece_action": ("piece_table", "resolve_piece_action_parameters"),
    "segment": ("map_segment_preview", "segment_preview"),
}
# The parameters of each function, positional then keyword.
PARAMETERS = {
    "grid": ["value"], "coordinate": ["value", "actor_reference"], "actor_reference": ["actions"],
    "tactical": ["action", "selection", "projection", "authoritative_scene"],
    "piece_table": ["value", "viewer_ids", "action_ids"],
    "piece_action": ["action", "selection", "projected_table"], "segment": ["surface", "data", "selection"],
}


def plain(value: object) -> object:
    """A JSON copy of a frozen or plain value (sets become sorted lists)."""
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [plain(item) for item in value]
    return value


# ── Recording (pytest plugin) ─────────────────────────────────────────────────────────────


def pytest_configure(config):  # noqa: ARG001 - pytest hook
    """Keep the arguments of every surface function call while Python's tests run."""
    import importlib
    import inspect

    import sylvia_gm.application.authority as authority

    path = os.environ[RECORDING_ENV]
    for function, (module_name, name) in FUNCTIONS.items():
        module = importlib.import_module(f"sylvia_gm.application.{module_name}")
        original = getattr(module, name)
        signature = inspect.signature(original)

        def recording(*args, __original=original, __signature=signature, __function=function, **kwargs):
            bound = __signature.bind(*args, **kwargs)
            bound.apply_defaults()
            try:
                arguments = {key: plain(value) for key, value in bound.arguments.items()}
                line = json.dumps({"function": __function, "arguments": arguments}, ensure_ascii=True, allow_nan=False)
            except (TypeError, ValueError):
                line = None
            if line is not None:
                with open(path, "a", encoding="utf-8") as sink:
                    sink.write(line + "\n")
            return __original(*args, **kwargs)

        for target in (module, authority, importlib.import_module("sylvia_gm.application.map_segment_preview")):
            if getattr(target, name, None) is original:
                setattr(target, name, recording)


def record() -> list[dict]:
    sink = HERE / "tactical_surface_recording.jsonl"
    sink.unlink(missing_ok=True)
    basetemp = ROOT / ".surface-recording-tmp"
    environment = {**os.environ, RECORDING_ENV: str(sink), "PYTHONPATH": str(HERE)}
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "generate_tactical_surface_vectors",
                    "-p", "no:cacheprovider", f"--basetemp={basetemp}", *RECORDED_TESTS],
                   cwd=ROOT, env=environment, check=False)
    kept: dict[str, list[dict]] = {}
    seen = set()
    for line in sink.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        try:
            json.dumps(item, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            continue  # A lone surrogate, which a Rust string cannot hold (differences row 3).
        identity = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        group = kept.setdefault(item["function"], [])
        if len(group) < PER_FUNCTION:
            group.append({"label": f"recorded {item['function']} {len(group)}", **item})
    sink.unlink(missing_ok=True)
    shutil.rmtree(basetemp, ignore_errors=True)
    return [item for function in sorted(kept) for item in kept[function]]


# ── Running one case ──────────────────────────────────────────────────────────────────────


def stored(value: object) -> object:
    """A value with each large object or list in it kept once in `documents`, at any depth."""
    if isinstance(value, dict):
        value = {key: stored(item) for key, item in value.items()}
    elif isinstance(value, list):
        value = [stored(item) for item in value]
    else:
        return value
    text = json.dumps(value, sort_keys=True, ensure_ascii=True)
    if len(text) <= DOCUMENT_BYTES:
        return value
    digest = hashlib.sha256(text.encode()).hexdigest()
    DOCUMENTS[digest] = value
    return {"$doc": digest}


def loaded(value: object) -> object:
    """The value a `stored` form stands for."""
    if isinstance(value, dict) and set(value) == {"$doc"}:
        return loaded(DOCUMENTS[value["$doc"]])
    if isinstance(value, dict):
        return {key: loaded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [loaded(item) for item in value]
    return value


def run(case: dict) -> dict:
    import importlib

    from sylvia_gm.domain.authority import owned_json_value, thaw_json_value

    function = case["function"]
    module_name, name = FUNCTIONS[function]
    target = getattr(importlib.import_module(f"sylvia_gm.application.{module_name}"), name)
    arguments = {key: loaded(value) for key, value in case["arguments"].items()}
    frozen = {}
    for key, value in arguments.items():
        if key in ("viewer_ids", "action_ids"):
            frozen[key] = frozenset(value)
        else:
            frozen[key] = owned_json_value(value)
    for key in ("value", "data"):
        if function in ("coordinate", "segment") and key in frozen:
            frozen[key] = literal_declaration(frozen[key])
    positional = [frozen[key] for key in PARAMETERS[function] if key in frozen and key in POSITIONAL[function]]
    keywords = {key: frozen[key] for key in PARAMETERS[function] if key in frozen and key not in POSITIONAL[function]}
    try:
        result = target(*positional, **keywords)
    except Exception as error:  # noqa: BLE001 - every exception Python lets through
        return {"error": [type(error).__name__, str(error)]}
    return {"ok": thaw_json_value(result) if result is not None else None}


def literal_declaration(data: object) -> object:
    """Thaw an occupied-space declaration, as its shipped `literal` surface source is.

    `_project_occupied_space` checks `isinstance(widths, (list, tuple))`, which a frozen list
    fails, so Python declares the space only for a literal's thawed widths (SRD Lite's case);
    from state it is unavailable (differences row NEXT).
    """
    from sylvia_gm.domain.authority import thaw_json_value

    space = data.get("occupied_space") if isinstance(data, Mapping) else None
    if not isinstance(space, Mapping) or "declaration" not in space:
        return data
    return {**data, "occupied_space": {**space, "declaration": thaw_json_value(space["declaration"])}}


POSITIONAL = {
    "grid": {"value"}, "coordinate": {"value"}, "actor_reference": {"actions"},
    "tactical": {"action", "selection", "projection"}, "piece_table": {"value"},
    "piece_action": {"action", "selection", "projected_table"}, "segment": {"surface", "data", "selection"},
}


# ── Scenes and surface data ───────────────────────────────────────────────────────────────


def shown(label: str, kind: str = "creature", **extra) -> dict:
    return {"player": {"label": label, "kind": kind, **extra}}


def arena_scene() -> dict:
    """The spatial query scene with public displays, actor references and anchored zones."""
    sys.path.insert(0, str(ROOT / "crates/sylvia-engine/tests/vectors"))
    from generate_spatial_scene_vectors import QUERY_SCENE

    scene = copy.deepcopy(QUERY_SCENE)
    metadata = {
        "orc": {**shown("Orc", state="ready", stance="prone", side="foe", color="red", icon="axe",
                        interactive=True, height_label="tall", material_label="hide"), "actor_id": "pc.orc", "size": 2},
        "elf": {**shown("Elf", side="ally"), "actor_id": "pc.elf"},
        "cart": {**shown("Cart", "object", interactive=False), "actor_id": "pc.cart"},
        "ghost": {**shown("Ghost"), "actor_id": "pc.elf"},
    }
    for item in scene["entities"]:
        item["metadata"] = {**item["metadata"], **metadata[item["id"]]}
    scene["geometry"][0]["metadata"] = shown("Wall", "wall", material_label="stone")
    scene["zones"][0]["metadata"] = {**shown("Pool", "hazard"), "display_anchor": "orc"}
    scene["zones"][1]["metadata"] = shown("Dais", "area", display_geometry={
        "kind": "cone", "origin": [1, 1, 0], "direction": [1, 0, 0], "length": 3, "angle_degrees": 60})
    scene["zones"].append({"id": "mist", "map_id": "keep", "shape": {"kind": "box", "min": [16, 0, 0], "max": [20, 3, 2]},
                           "metadata": {**shown("Mist", "area"), "display_anchor": "ghost"}})
    scene["zones"].append({"id": "glow", "map_id": "keep", "shape": {"kind": "box", "min": [5, 12, 0], "max": [7, 14, 2]},
                           "metadata": shown("Glow", "area", display_geometry={"kind": "sphere", "center": [6, 13, 1], "radius": 1})})
    return scene


OBJECTIVES = [
    {"id": "o1", "label": "  Hold the gate ", "status": "active", "position": [1, 2, 0], "visible": True},
    {"id": "o2", "label": "Hidden", "status": "active", "position": [1, 2, 0], "visible": False},
    {"id": "o1", "label": "Duplicate", "status": "complete", "position": [1, 2, 0], "visible": True},
    {"id": "o3", "label": "Done", "status": "complete", "position": [3.5, 2, 1], "visible": True},
    {"id": "o4", "label": "Lost", "status": "failed", "position": [3, 2], "visible": True},
    {"id": "o11", "label": "Fallen", "status": "failed", "position": [4, 4, 0.5], "visible": True},
    {"id": "o5", "label": "Odd", "status": "pending", "position": [3, 2, 0], "visible": True},
    {"id": "o6", "label": "Extra", "status": "active", "position": [3, 2, 0], "visible": True, "note": 1},
    {"id": "", "label": "No id", "status": "active", "position": [3, 2, 0], "visible": True},
    {"id": "o7", "label": "   ", "status": "active", "position": [3, 2, 0], "visible": True},
    {"id": "o8", "label": "x" * 257, "status": "active", "position": [3, 2, 0], "visible": True},
    {"id": "o9", "label": "Bool", "status": "active", "position": [True, 2, 0], "visible": True},
    {"id": "o10", "label": "Truthy", "status": "active", "position": [3, 2, 0], "visible": 1},
    "not an objective",
]


def arena_data(**changes) -> dict:
    data = {"scene": arena_scene(), "objectives": OBJECTIVES,
            "selection": {"actor": "orc", "zone": "pool", "hover": [1, 2], "path": [[1, 1], [2, 2]]},
            "preview": {"map_revision": 3, "path_cost": 4.5, "los": True, "exposure": 0.25,
                        "crossed_zones": ["pool", "dais", "pool"]}}
    data.update(changes)
    return data


OCCUPIED = {"declaration": {"convention": "5e sizes", "widths": [2.5, 5, 10, 15.5], "category_field": "size"},
            "actors": {"pc.orc": {"size": 2}, "pc.elf": {"size": 0}, "pc.cart": {"size": True}}}


def targeted() -> list[dict]:
    cases: list[dict] = []

    def case(label, function, **arguments):
        cases.append({"label": label, "function": function,
                      "arguments": {key: stored(value) for key, value in arguments.items()}})

    from sylvia_gm.application.tactical_grid import player_known_spatial_scene, project_coordinate_map, project_tactical_grid
    from sylvia_gm.domain.authority import thaw_json_value

    # project_tactical_grid
    case("grid arena", "grid", value=arena_data())
    for label, value in (("list", [arena_data()]), ("null", None), ("text", "grid")):
        case(f"grid source {label}", "grid", value=value)
    case("grid without a scene", "grid", value={"objectives": []})
    case("grid invalid scene", "grid", value={"scene": {"map": {}}})
    case("grid scene not a mapping", "grid", value={"scene": [1, 2]})
    footprint_scene = arena_scene()
    footprint_scene["entities"][1]["footprint"] = "box"
    case("grid footprint text", "grid", value={"scene": footprint_scene})
    bad_displays = {
        "extra field": {"player": {"label": "X", "kind": "k", "secret": 1}},
        "blank label": {"player": {"label": " ", "kind": "k"}},
        "padded label": {"player": {"label": " X", "kind": "k"}},
        "long label": {"player": {"label": "é" * 129, "kind": "k"}},
        "long kind": {"player": {"label": "X", "kind": "k" * 65}},
        "kind 64": {"player": {"label": "X", "kind": "k" * 64}},
        "label 256 bytes": {"player": {"label": "é" * 128, "kind": "k"}},
        "state number": {"player": {"label": "X", "kind": "k", "state": 1}},
        "state blank": {"player": {"label": "X", "kind": "k", "state": ""}},
        "state null": {"player": {"label": "X", "kind": "k", "state": None}},
        "interactive text": {"player": {"label": "X", "kind": "k", "interactive": "yes"}},
        "player list": {"player": ["X"]},
        "no player": {"label": "X"},
        "anchor on geometry": {"player": {"label": "X", "kind": "k"}, "display_anchor": "orc"},
        "display geometry on geometry": {"player": {"label": "X", "kind": "k", "display_geometry": {"kind": "cone"}}},
    }
    for label, metadata in bad_displays.items():
        scene = arena_scene()
        scene["geometry"][1]["metadata"] = metadata
        scene["entities"][1]["metadata"] = metadata
        case(f"grid display {label}", "grid", value={"scene": scene})
    zone_metadata = {
        "anchor hidden entity": {**shown("Z", "area"), "display_anchor": "pillar"},
        "anchor number": {**shown("Z", "area"), "display_anchor": 3},
        "anchor null": {**shown("Z", "area"), "display_anchor": None},
        "geometry prism": shown("Z", "area", display_geometry={
            "kind": "prism", "base": {"kind": "box", "min": [1, 1, 0], "max": [3, 3, 1]}}),
        "geometry box": shown("Z", "area", display_geometry={"kind": "box", "min": [1, 1, 0], "max": [3, 3, 1]}),
        "geometry invalid cone": shown("Z", "area", display_geometry={"kind": "cone", "origin": [1, 1, 0]}),
        "geometry outside": shown("Z", "area", display_geometry={
            "kind": "cone", "origin": [99, 1, 0], "direction": [1, 0, 0], "length": 3, "angle_degrees": 60}),
        "geometry text": shown("Z", "area", display_geometry="cone"),
    }
    for label, metadata in zone_metadata.items():
        scene = arena_scene()
        scene["zones"][1]["metadata"] = metadata
        case(f"grid zone {label}", "grid", value={"scene": scene})
    objectives_cases = {
        "not a list": {"o": 1}, "text": "objectives", "257": [OBJECTIVES[0]] * 257, "256": [OBJECTIVES[3]] * 256,
        "dict status": [{**OBJECTIVES[0], "status": {"a": 1}}],
        "list status": [OBJECTIVES[3], {**OBJECTIVES[0], "status": ["active"]}],
        "dict status after a bad label": [{**OBJECTIVES[0], "label": " ", "status": {"a": 1}}],
        "dict status after a duplicate": [OBJECTIVES[3], {**OBJECTIVES[3], "status": {"a": 1}}],
        "number status": [{**OBJECTIVES[0], "status": 1}],
    }
    for label, objectives in objectives_cases.items():
        case(f"grid objectives {label}", "grid", value=arena_data(objectives=objectives))
    previews = {
        "float revision": {"map_revision": 3.0, "path_cost": 0, "los": False, "exposure": 1},
        "true revision": {"map_revision": True, "path_cost": 1},
        "stale": {"map_revision": 2, "path_cost": 1},
        "missing revision": {"path_cost": 1},
        "negative cost": {"map_revision": 3, "path_cost": -1, "exposure": 1.5, "los": 1},
        "bool cost": {"map_revision": 3, "path_cost": True, "exposure": False},
        "null cost": {"map_revision": 3, "path_cost": None, "exposure": None},
        "text cost": {"map_revision": 3, "path_cost": "4", "exposure": "0.5"},
        "zones not text": {"map_revision": 3, "crossed_zones": ["pool", 1]},
        "zones text": {"map_revision": 3, "crossed_zones": "pool"},
        "257 zones": {"map_revision": 3, "crossed_zones": [f"z{i}" for i in range(257)]},
        "256 zones": {"map_revision": 3, "crossed_zones": [f"z{i % 7}" for i in range(256)]},
        "not a mapping": ["map_revision", 3],
    }
    for label, preview in previews.items():
        case(f"grid preview {label}", "grid", value=arena_data(preview=preview))
    case("grid selection not a mapping", "grid", value=arena_data(selection=["actor"]))
    case("grid selection every field", "grid", value=arena_data(selection={
        key: key for key in ("actor", "zone", "device", "destination", "path", "target", "aim_point", "area",
                             "orientation", "declared_action", "node", "piece")}))

    # project_coordinate_map
    for label, reference in (("no reference", None), ("actor_id", "actor_id"), ("unknown field", "missing"),
                             ("size field", "size")):
        case(f"coordinate {label}", "coordinate", value=arena_data(), actor_reference=reference)
    for label, enabled in (("enabled", True), ("disabled", False), ("enabled one", 1), ("enabled null", None)):
        case(f"coordinate {label}", "coordinate", value={**arena_data(), "enabled": enabled}, actor_reference=None)
    case("coordinate not a mapping", "coordinate", value="map", actor_reference="actor_id")
    references = {
        "shared by a hidden body": {"pillar": None, "cart": "pc.orc"},
        "padded": {"orc": " pc.orc"}, "empty": {"orc": ""}, "number": {"orc": 7}, "long": {"orc": "r" * 257},
        "256 bytes": {"orc": "r" * 256},
    }
    for label, changes in references.items():
        data = arena_data()
        for item in data["scene"]["entities"]:
            if item["id"] in changes and changes[item["id"]] is not None:
                item["metadata"]["actor_id"] = changes[item["id"]]
        if label == "shared by a hidden body":
            data["scene"]["entities"].append({"id": "spy", "map_id": "keep", "position": [17, 12, 0],
                                              "footprint": {"kind": "circle", "radius": 0.5}, "height": 2, "facing": [1, 0],
                                              "active": True, "movement": "blocking", "visibility": "clear",
                                              "metadata": {"actor_id": "pc.elf"}})
        case(f"coordinate reference {label}", "coordinate", value=data, actor_reference="actor_id")
    occupied = {
        "declared": OCCUPIED,
        "extra key": {**OCCUPIED, "x": 1},
        "declaration extra key": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "x": 1}},
        "widths empty": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": []}},
        "widths 33": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": [1] * 33}},
        "widths 32": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": [1, 2, 3] + [4] * 29}},
        "width zero": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": [0, 5, 10]}},
        "width too large": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": [5, 1000001, 10]}},
        "width a million": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": [5, 1000000, 1e6]}},
        "width bool": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": [5, True, 10]}},
        "widths text": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "widths": "5"}},
        "field empty": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "category_field": ""}},
        "field number": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "category_field": 1}},
        "convention empty": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "convention": ""}},
        "convention 128": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "convention": "é" * 128}},
        "convention 129": {**OCCUPIED, "declaration": {**OCCUPIED["declaration"], "convention": "c" * 129}},
        "actors a list": {**OCCUPIED, "actors": [OCCUPIED["actors"]]},
        "rank out of range": {**OCCUPIED, "actors": {"pc.orc": {"size": 4}, "pc.elf": {"size": -1}}},
        "rank float": {**OCCUPIED, "actors": {"pc.orc": {"size": 2.0}, "pc.elf": {"size": "0"}}},
        "actor not a mapping": {**OCCUPIED, "actors": {"pc.orc": 2, "pc.elf": None}},
        "not a mapping": ["declaration"],
    }
    for label, space in occupied.items():
        for reference in ("actor_id", None):
            case(f"coordinate occupied {label} {reference}", "coordinate",
                 value=arena_data(occupied_space=space), actor_reference=reference)

    # coordinate_actor_reference
    actor_action = {"id": "a1", "label": "Move", "intent": "move", "params": {}, "bindings": {"who": "actor"}}
    for label, actions in {
        "none": [], "not a list": {"a": 1}, "text": "actions", "one": [{**actor_action, "actor_reference": "actor_id"}],
        "consistent": [{**actor_action, "actor_reference": "actor_id"}, {**actor_action, "id": "a2", "actor_reference": "actor_id"}],
        "disagree": [{**actor_action, "actor_reference": "actor_id"}, {**actor_action, "id": "a2", "actor_reference": "other"}],
        "one without": [{**actor_action, "actor_reference": "actor_id"}, {**actor_action, "id": "a2"}],
        "all without": [actor_action, {**actor_action, "id": "a2"}],
        "non-actor binding ignored": [{**actor_action, "actor_reference": "actor_id"},
                                      {**actor_action, "id": "a2", "bindings": {"t": "target"}, "actor_reference": "x"}],
        "no bindings ignored": [{"id": "a3", "label": "Wait", "intent": "wait", "params": {}, "actor_reference": "x"},
                                {**actor_action, "actor_reference": "actor_id"}],
        "non-mapping action": ["action", {**actor_action, "actor_reference": "actor_id"}],
    }.items():
        case(f"actor reference {label}", "actor_reference", actions=actions)

    # resolve_tactical_action_parameters
    data = arena_data()
    projection = thaw_json_value(project_tactical_grid(data))
    full_scene = data["scene"]
    known_scene = thaw_json_value(player_known_spatial_scene(full_scene))

    def tactical(label, bindings, selection, *, params=None, scene=full_scene, projection_value=projection, **extra):
        action = {"id": "act", "label": "Act", "intent": "act", "params": params if params is not None else {"mode": "x"},
                  "bindings": bindings, **extra}
        case(f"tactical {label}", "tactical", action=action, selection=selection, projection=projection_value,
             authoritative_scene=scene)

    sel = lambda **values: {"map_revision": 3, **values}  # noqa: E731
    tactical("actor", {"who": "actor"}, sel(actor="orc"))
    tactical("actor hidden body", {"who": "actor"}, sel(actor="pillar"))
    tactical("actor unknown", {"who": "actor"}, sel(actor="nobody"))
    tactical("actor number", {"who": "actor"}, sel(actor=1))
    tactical("actor inactive ghost", {"who": "actor"}, sel(actor="ghost"))
    tactical("target and device", {"t": "target", "d": "device"}, sel(target="elf", device="cart"))
    tactical("zone", {"z": "zone"}, sel(zone="pool"))
    tactical("zone unknown", {"z": "zone"}, sel(zone="orc"))
    for label, destination in (("cell", [4, 5]), ("origin", [0, 0]), ("last cell", [19, 15]), ("row 16", [1, 16]),
                               ("column 20", [20, 1]), ("negative", [-1, 2]), ("float", [1.0, 2]), ("bool", [True, 2]),
                               ("three", [1, 2, 3]), ("text", "4,5"), ("huge", [2 ** 63, 1])):
        tactical(f"destination {label}", {"to": "destination"}, sel(destination=destination))
    for label, point in (("inside", [1.5, 2, 0]), ("edge", [20, 15.5, 10]), ("outside", [20.5, 1, 0]),
                         ("below", [1, 1, -0.1]), ("two", [1, 1]), ("integers", [1, 1, 1])):
        tactical(f"aim point {label}", {"at": "aim_point"}, sel(aim_point=point))
    for label, orientation in (("east", [1, 0]), ("zero", [0, 0]), ("float zero", [0.0, -0.0]), ("tiny", [5e-324, 0]),
                               ("three", [1, 0, 0]), ("bool", [True, 0])):
        tactical(f"orientation {label}", {"face": "orientation"}, sel(orientation=orientation))
    tactical("declared action", {"what": "declared_action"}, sel(declared_action="act"))
    tactical("declared action other", {"what": "declared_action"}, sel(declared_action="other"))
    areas = {
        "box": {"kind": "box", "min": [1, 1, 0], "max": [3, 3, 1]},
        "box outside": {"kind": "box", "min": [1, 1, 0], "max": [30, 3, 1]},
        "box extra": {"kind": "box", "min": [1, 1, 0], "max": [3, 3, 1], "x": 1},
        "cylinder": {"kind": "cylinder", "center": [5, 5, 0], "radius": 2, "height": 1.5},
        "cylinder zero radius": {"kind": "cylinder", "center": [5, 5, 0], "radius": 0, "height": 1},
        "cylinder bool height": {"kind": "cylinder", "center": [5, 5, 0], "radius": 1, "height": True},
        "cylinder missing height": {"kind": "cylinder", "center": [5, 5, 0], "radius": 1},
        "cylinder extra": {"kind": "cylinder", "center": [5, 5, 0], "radius": 1, "height": 1, "x": 1},
        "sphere": {"kind": "sphere", "center": [5, 5, 0], "radius": 1},
        "text": "box",
    }
    for label, area in areas.items():
        tactical(f"area {label}", {"where": "area"}, sel(area=area))
    route = {"route": "path", "who": "actor", "to": "destination"}
    tactical("path owner route", route, sel(actor="orc", destination=[9, 5]))
    tactical("path to the start", route, sel(actor="orc", destination=[5, 5]))
    tactical("path one step", route, sel(actor="orc", destination=[6, 5]))
    tactical("path blocked", route, sel(actor="orc", destination=[2, 4]))
    tactical("path through the known scene", route, sel(actor="orc", destination=[2, 4]), scene=known_scene)
    tactical("path around the cart", route, sel(actor="elf", destination=[6, 9]))
    tactical("path without a scene", route, sel(actor="orc", destination=[9, 5]), scene=None)
    tactical("path actor number", route, sel(actor=3, destination=[9, 5]))
    tactical("path actor unknown", route, sel(actor="nobody", destination=[9, 5]))
    tactical("path actor hidden body", route, sel(actor="pillar", destination=[9, 5]))
    tactical("path destination float", route, sel(actor="orc", destination=[9.0, 5]))
    tactical("path destination outside", route, sel(actor="orc", destination=[40, 5]))
    tactical("path destination text", route, sel(actor="orc", destination="9,5"))
    tactical("path scene invalid", route, sel(actor="orc", destination=[9, 5]), scene={"map": 1})
    footprint = copy.deepcopy(full_scene)
    footprint["entities"][1]["footprint"] = "box"
    tactical("path scene footprint text", route, sel(actor="orc", destination=[9, 5]), scene=footprint)
    edge = copy.deepcopy(full_scene)
    edge["entities"][0]["position"] = [20, 15.5, 0]
    tactical("path start off the grid", route, sel(actor="orc", destination=[9, 5]), scene=edge)
    tactical("path only", {"route": "path"}, sel())
    tactical("stale revision", {"who": "actor"}, {"map_revision": 2, "actor": "orc"})
    tactical("float revision", {"who": "actor"}, {"map_revision": 3.0, "actor": "orc"})
    tactical("missing revision", {"who": "actor"}, {"actor": "orc"})
    tactical("extra input", {"who": "actor"}, sel(actor="orc", zone="pool"))
    tactical("missing input", {"who": "actor", "z": "zone"}, sel(actor="orc"))
    tactical("path given by the client", {"route": "path"}, sel(path=[[1, 1], [2, 2]]))
    tactical("binds a fixed parameter", {"who": "actor"}, sel(actor="orc"), params={"who": "x"})
    tactical("no bindings", {}, sel())
    tactical("selection not a mapping", {"who": "actor"}, ["orc"])
    tactical("params not a mapping", {"who": "actor"}, sel(actor="orc"), params=[])
    tactical("node not tactical", {"n": "node"}, sel(node="orc"))
    tactical("order of bindings", {"z": "zone", "who": "actor", "to": "destination"},
             sel(zone="pool", actor="elf", destination=[1, 1]), params={"a": 1, "b": [1, 2]})
    coordinate = thaw_json_value(project_coordinate_map(data, actor_reference="actor_id"))
    for label, bindings, selection, extra in (
        ("reference", {"who": "actor"}, sel(actor="orc"), {"actor_reference": "actor_id"}),
        ("reference ambiguous", {"who": "actor"}, sel(actor="elf"), {"actor_reference": "actor_id"}),
        ("reference other field", {"who": "actor"}, sel(actor="orc"), {"actor_reference": "size"}),
        ("target reference", {"who": "actor", "t": "target"}, sel(actor="orc", target="cart"),
         {"actor_reference": "actor_id", "target_reference": "actor_id"}),
        ("target reference ambiguous", {"who": "actor", "t": "target"}, sel(actor="orc", target="elf"),
         {"actor_reference": "actor_id", "target_reference": "actor_id"}),
        ("target without reference", {"who": "actor", "t": "target"}, sel(actor="orc", target="elf"),
         {"actor_reference": "actor_id"}),
        ("aim point with reference", {"who": "actor", "at": "aim_point"}, sel(actor="orc", aim_point=[1, 1, 0]),
         {"actor_reference": "actor_id"}),
    ):
        tactical(f"coordinate {label}", bindings, selection, projection_value=coordinate, **extra)
    tactical("reference without a coordinate projection", {"who": "actor"}, sel(actor="orc"),
             actor_reference="actor_id")

    # project_piece_table
    for label, table in piece_tables().items():
        for viewers in (["east"], []):
            case(f"piece table {label} {viewers}", "piece_table", value=table, viewer_ids=viewers,
                 action_ids=["discard", "call"])
    from sylvia_gm.application.piece_table import project_piece_table

    projected = thaw_json_value(project_piece_table(TABLE, viewer_ids=frozenset({"east"}),
                                                    action_ids=frozenset({"discard", "call"})))
    piece_action = {"id": "discard", "label": "Discard", "intent": "discard", "params": {"mode": "tsumogiri"},
                    "bindings": {"tile": "piece"}}
    for label, action, selection in (
        ("advertised", piece_action, {"piece": "m1"}),
        ("other action", {**piece_action, "id": "call"}, {"piece": "m1"}),
        ("call piece", {**piece_action, "id": "call"}, {"piece": "p5"}),
        ("unknown piece", piece_action, {"piece": "zz"}),
        ("concealed piece", piece_action, {"piece": "w1"}),
        ("blank piece", piece_action, {"piece": " "}),
        ("number piece", piece_action, {"piece": 3}),
        ("extra selection", piece_action, {"piece": "m1", "x": 1}),
        ("selection list", piece_action, ["m1"]),
        ("two bindings", {**piece_action, "bindings": {"tile": "piece", "b": "piece"}}, {"piece": "m1"}),
        ("other source", {**piece_action, "bindings": {"tile": "actor"}}, {"piece": "m1"}),
        ("fixed overlap", {**piece_action, "params": {"tile": "x"}}, {"piece": "m1"}),
        ("params list", {**piece_action, "params": []}, {"piece": "m1"}),
        ("no id", {key: value for key, value in piece_action.items() if key != "id"}, {"piece": "s9"}),
    ):
        case(f"piece action {label}", "piece_action", action=action, selection=selection, projected_table=projected)

    # segment_preview
    segment_action = {"id": "shoot", "label": "Shoot", "intent": "shoot", "params": {},
                      "bindings": {"who": "actor", "t": "target"}, "spatial_preview": "segment"}
    aim_action = {**segment_action, "bindings": {"who": "actor", "at": "aim_point"}}
    known = {**data, "scene": known_scene}
    for renderer in ("tactical_grid", "coordinate_map"):
        for label, action, selection in (
            ("target", segment_action, {"actor": "orc", "target": "elf"}),
            ("target behind the wall", segment_action, {"actor": "elf", "target": "orc"}),
            ("same body", segment_action, {"actor": "orc", "target": "orc"}),
            ("aim point", aim_action, {"actor": "orc", "aim_point": [1.25, 7, 0.5]}),
            ("aim point outside", aim_action, {"actor": "orc", "aim_point": [40, 7, 0.5]}),
            ("aim point text", aim_action, {"actor": "orc", "aim_point": "here"}),
            ("aim point null", aim_action, {"actor": "orc", "aim_point": None}),
            ("aim point missing", aim_action, {"actor": "orc"}),
            ("ghost actor", segment_action, {"actor": "ghost", "target": "elf"}),
            ("hidden target", segment_action, {"actor": "orc", "target": "pillar"}),
            ("unknown actor", segment_action, {"actor": "nobody", "target": "elf"}),
            ("not a segment", {**segment_action, "spatial_preview": "path"}, {"actor": "orc", "target": "elf"}),
            ("no preview", {key: value for key, value in segment_action.items() if key != "spatial_preview"},
             {"actor": "orc", "target": "elf"}),
            ("selection list", segment_action, ["orc"]),
        ):
            case(f"segment {renderer} {label}", "segment", surface={"id": "map", "renderer": renderer, "action": action},
                 data=known, selection=selection)
    case("segment invalid map", "segment", surface={"id": "map", "renderer": "tactical_grid", "action": segment_action},
         data={"scene": {"map": 1}}, selection={"actor": "orc", "target": "elf"})
    case("segment disabled coordinate map", "segment",
         surface={"id": "map", "renderer": "coordinate_map", "action": segment_action},
         data={**known, "enabled": False}, selection={"actor": "orc", "target": "elf"})
    far = copy.deepcopy(known)
    far["scene"]["entities"][2]["position"] = [19.75, 15.25, 3.333]
    case("segment rounding", "segment", surface={"id": "map", "renderer": "tactical_grid", "action": segment_action},
         data=far, selection={"actor": "orc", "target": "elf"})
    return cases


TABLE = {
    "format": 1, "title": "Riichi", "layout": "four_sided",
    "status": [{"label": "Round", "value": "East 1"}, {"label": "Wall", "value": "70"}],
    "areas": [
        {"id": "east", "label": "East", "position": "bottom", "badge": "Dealer", "score": "25000", "active": True,
         "groups": [
             {"id": "east.hand", "label": "Hand", "visibility": "owner", "owner": "east", "show_count": True, "role": "rack",
              "source": "self", "pieces": [
                  {"id": "m1", "label": "1m", "action_id": "discard", "face": {"family": "mahjong", "kind": 0},
                   "flags": ["drawn", "latest"], "emphasis": "accent", "detail": "Man 1"},
                  {"id": "m5r", "label": "5m", "face": {"family": "mahjong", "kind": 4, "red": True}},
                  {"id": "s9", "label": "9s", "faces": [{"family": "mahjong", "kind": 26, "red": False}]},
              ]},
             {"id": "east.pond", "label": "Pond", "visibility": "public", "owner": "nobody at all", "role": "pond",
              "pieces": [{"id": "p5", "label": "5p", "action_id": "call", "flags": ["sideways"]}]},
         ]},
        {"id": "west", "label": "West", "position": "top",
         "groups": [{"id": "west.hand", "label": "Hand", "visibility": "owner", "owner": "west", "show_count": True,
                     "source": "self", "role": "rack", "pieces": [{"id": "w1", "label": "?", "secret": "anything"}] * 13}]},
        {"id": "north", "label": "North", "position": "right", "active": False,
         "groups": [{"id": "north.hand", "label": "Hand", "visibility": "owner", "owner": "north", "pieces": [1, 2]}]},
    ],
}


def piece_tables() -> dict[str, object]:
    def changed(path, value):
        table = copy.deepcopy(TABLE)
        target = table
        for key in path[:-1]:
            target = target[key]
        if value is DELETE:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return table

    hand = ("areas", 0, "groups", 0)
    piece = (*hand, "pieces", 0)
    return {
        "full": TABLE, "not a mapping": [TABLE], "extra field": changed(("x",), 1),
        "format float": changed(("format",), 1.0), "format bool": changed(("format",), True),
        "format 2": changed(("format",), 2), "no title": changed(("title",), DELETE),
        "title control": changed(("title",), "Ri\tichi"), "title 257 bytes": changed(("title",), "x" * 257),
        "title 256 bytes": changed(("title",), "é" * 128), "title padded": changed(("title",), " Riichi "),
        "no layout": changed(("layout",), DELETE), "layout other": changed(("layout",), "square"),
        "no status": changed(("status",), DELETE), "status null": changed(("status",), None),
        "status 17": changed(("status",), [{"label": "a", "value": "b"}] * 17),
        "status extra": changed(("status",), [{"label": "a", "value": "b", "c": 1}]),
        "status number": changed(("status",), [{"label": "a", "value": 1}]),
        "no areas": changed(("areas",), DELETE), "areas 6": changed(("areas",), [TABLE["areas"][1]] * 6),
        "area duplicate id": changed(("areas", 1, "id"), "east"),
        "area duplicate position": changed(("areas", 1, "position"), "bottom"),
        "area position other": changed(("areas", 1, "position"), "middle"),
        "area position number": changed(("areas", 1, "position"), 1),
        "area active text": changed(("areas", 2, "active"), "no"),
        "area badge blank": changed(("areas", 0, "badge"), ""),
        "area extra": changed(("areas", 0, "note"), "x"),
        "group visibility other": changed((*hand, "visibility"), "private"),
        "group owner blank": changed((*hand, "owner"), " "),
        "group show count text": changed((*hand, "show_count"), "yes"),
        "group show count null": changed((*hand, "show_count"), None),
        "group no show count": changed((*hand, "show_count"), DELETE),
        "group role other": changed((*hand, "role"), "hand"),
        "group source other": changed((*hand, "source"), "behind"),
        "concealed source other": changed(("areas", 1, "groups", 0, "source"), "behind"),
        "concealed role other": changed(("areas", 1, "groups", 0, "role"), "hand"),
        "group extra": changed((*hand, "secret"), 1),
        "group pieces text": changed((*hand, "pieces"), "m1"),
        "group 257 pieces": changed((*hand, "pieces"), [{"id": f"x{i}", "label": "x"} for i in range(257)]),
        "1025 pieces": changed(("areas",), many_pieces(1)),
        "1024 pieces": changed(("areas",), many_pieces(0)),
        "65 groups": changed(("areas",), [{"id": f"a{a}", "label": "A", "position": position, "groups": [
            {"id": f"g{a}.{g}", "label": "G", "visibility": "public", "pieces": []} for g in range(13)]}
            for a, position in enumerate(["top", "left", "center", "right", "bottom"])]),
        "64 groups": changed(("areas",), [{"id": f"a{a}", "label": "A", "position": position, "groups": [
            {"id": f"g{a}.{g}", "label": "G", "visibility": "public", "pieces": []} for g in range(13 if a else 12)]}
            for a, position in enumerate(["top", "left", "center", "right", "bottom"])]),
        "piece duplicate id": changed((*hand, "pieces", 1, "id"), "m1"),
        "piece extra": changed((*piece, "secret"), 1),
        "piece detail blank": changed((*piece, "detail"), ""),
        "piece unknown action": changed((*piece, "action_id"), "riichi"),
        "piece flags duplicate": changed((*piece, "flags"), ["drawn", "drawn"]),
        "piece flags other": changed((*piece, "flags"), ["glowing"]),
        "piece flags 7": changed((*piece, "flags"), ["drawn", "sideways", "stacked", "back", "claimed", "latest", "drawn"]),
        "piece flags text": changed((*piece, "flags"), "drawn"),
        "piece emphasis other": changed((*piece, "emphasis"), "loud"),
        "face other family": changed((*piece, "face"), {"family": "chess", "kind": 0}),
        "face kind 34": changed((*piece, "face"), {"family": "mahjong", "kind": 34}),
        "face kind 33": changed((*piece, "face"), {"family": "mahjong", "kind": 33}),
        "face kind float": changed((*piece, "face"), {"family": "mahjong", "kind": 1.0}),
        "face kind bool": changed((*piece, "face"), {"family": "mahjong", "kind": True}),
        "face red 5s": changed((*piece, "face"), {"family": "mahjong", "kind": 22, "red": True}),
        "face red 1m": changed((*piece, "face"), {"family": "mahjong", "kind": 0, "red": True}),
        "face red null": changed((*piece, "face"), {"family": "mahjong", "kind": 4, "red": None}),
        "face extra": changed((*piece, "face"), {"family": "mahjong", "kind": 4, "back": True}),
        "faces 5": changed((*piece, "faces"), [{"family": "mahjong", "kind": 1}] * 5),
        "faces 4": changed((*piece, "faces"), [{"family": "mahjong", "kind": 1}] * 4),
    }


DELETE = object()


def many_pieces(extra: int) -> list[dict]:
    """Four public groups of 256 pieces, and a fifth area with `extra` more."""
    areas = [{"id": f"a{a}", "label": "A", "position": position, "groups": [
        {"id": f"g{a}", "label": "G", "visibility": "public", "pieces": [{"id": f"p{i}", "label": "p"} for i in range(256)]}]}
        for a, position in enumerate(["top", "left", "center", "right"])]
    areas.append({"id": "a4", "label": "A", "position": "bottom", "groups": [
        {"id": "last", "label": "G", "visibility": "public", "pieces": [{"id": "x", "label": "p"}] * extra}]})
    return areas


# ── Seeded random cases ───────────────────────────────────────────────────────────────────


def mutate(rng: random.Random, value: object, depth: int = 0) -> object:
    """Replace, drop or retype one nested field."""
    leaves = [None, True, False, 0, 1, -1, 2.5, 3, "", " ", "x", "orc", "pool", [], {}, [1, 2], [1, 2, 0], "é" * 200]
    if isinstance(value, dict) and value and rng.random() < 0.85:
        key = rng.choice(sorted(value))
        result = dict(value)
        roll = rng.random()
        if roll < 0.15:
            del result[key]
        elif roll < 0.4 or depth > 5:
            result[key] = rng.choice(leaves)
        else:
            result[key] = mutate(rng, value[key], depth + 1)
        return result
    if isinstance(value, list) and value and rng.random() < 0.85:
        index = rng.randrange(len(value))
        result = list(value)
        if rng.random() < 0.2:
            del result[index]
        else:
            result[index] = mutate(rng, value[index], depth + 1) if rng.random() < 0.6 else rng.choice(leaves)
        return result
    return rng.choice(leaves)


def random_cases(count: int) -> list[dict]:
    from sylvia_gm.application.tactical_grid import project_coordinate_map, project_tactical_grid
    from sylvia_gm.domain.authority import thaw_json_value

    rng = random.Random(20261124)
    data = arena_data(occupied_space=OCCUPIED)
    projection = thaw_json_value(project_tactical_grid(data))
    coordinate = thaw_json_value(project_coordinate_map(data, actor_reference="actor_id"))
    sources = ["actor", "target", "device", "zone", "destination", "path", "aim_point", "orientation", "declared_action",
               "area"]
    good = {"actor": ["orc", "elf", "cart", "ghost", "pillar"], "target": ["elf", "orc"], "device": ["cart"],
            "zone": ["pool", "dais", "mist"], "destination": [[4, 5], [9, 5], [0, 0], [19, 15], [20, 1]],
            "aim_point": [[1, 1, 0], [19.5, 15, 9.5]], "orientation": [[1, 0], [0, 0], [-1, 2.5]],
            "declared_action": ["act"], "area": [{"kind": "box", "min": [1, 1, 0], "max": [3, 3, 1]},
                                                  {"kind": "cylinder", "center": [5, 5, 0], "radius": 2, "height": 1}]}
    cases = []
    for index in range(count):
        kind = rng.choice(["grid", "coordinate", "tactical", "tactical", "piece_table", "segment"])
        label = f"random {kind} {index}"
        if kind == "grid":
            value = {**data, **{key: mutate(rng, data[key]) for key in rng.sample(["objectives", "selection", "preview"], 2)}}
            if rng.random() < 0.3:
                value["scene"] = mutate(rng, data["scene"])
            cases.append({"label": label, "function": "grid", "arguments": {"value": stored(value)}})
        elif kind == "coordinate":
            value = {**data, "occupied_space": mutate(rng, OCCUPIED)}
            if rng.random() < 0.4:
                value["scene"] = mutate(rng, data["scene"])
            cases.append({"label": label, "function": "coordinate",
                          "arguments": {"value": stored(value), "actor_reference": rng.choice(["actor_id", None, "size"])}})
        elif kind == "tactical":
            chosen = rng.sample(sources, rng.randint(1, 3))
            if "path" in chosen and rng.random() < 0.85:
                chosen += [source for source in ("actor", "destination") if source not in chosen]
            bindings = {f"p{position}": source for position, source in enumerate(chosen)}
            selection = {"map_revision": 3}
            for source in chosen:
                if source == "path":
                    continue
                selection[source] = rng.choice(good[source]) if rng.random() < 0.75 else mutate(rng, rng.choice(good[source]))
            if rng.random() < 0.1:
                selection = mutate(rng, selection)
            reference = rng.random() < 0.3
            action = {"id": "act", "label": "Act", "intent": "act", "params": {"mode": "x"}, "bindings": bindings}
            if reference and "actor" in chosen:
                action["actor_reference"] = "actor_id"
            cases.append({"label": label, "function": "tactical", "arguments": {
                "action": action, "selection": selection, "projection": stored(coordinate if reference else projection),
                "authoritative_scene": stored(data["scene"]) if rng.random() < 0.9 else None}})
        elif kind == "piece_table":
            cases.append({"label": label, "function": "piece_table", "arguments": {
                "value": stored(mutate(rng, TABLE)), "viewer_ids": rng.choice([["east"], ["east", "west"], []]),
                "action_ids": ["discard", "call"]}})
        else:
            selection = {"actor": rng.choice(good["actor"]), "target": rng.choice(good["target"]),
                         "aim_point": rng.choice(good["aim_point"])}
            if rng.random() < 0.3:
                selection = mutate(rng, selection)
            bindings = rng.choice([{"who": "actor", "t": "target"}, {"who": "actor", "at": "aim_point"}])
            action = {"id": "shoot", "label": "Shoot", "intent": "shoot", "params": {}, "bindings": bindings,
                      "spatial_preview": "segment"}
            cases.append({"label": label, "function": "segment", "arguments": {
                "surface": {"id": "map", "renderer": rng.choice(["tactical_grid", "coordinate_map"]), "action": action},
                "data": stored(data), "selection": selection}})
    return cases


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    if "--record" in sys.argv:
        recorded = record()
        for item in recorded:
            item["arguments"] = {key: stored(value) for key, value in item["arguments"].items()}
    else:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
        DOCUMENTS.update(previous.get("documents", {}))
        recorded = [{**{key: value for key, value in item.items() if key not in ("expected", "arguments")},
                     "arguments": {key: stored(loaded(value)) for key, value in item["arguments"].items()}}
                    for item in previous.get("cases", []) if item["label"].startswith("recorded ")]
    cases = targeted() + random_cases(1200) + recorded
    for item in cases:
        item["expected"] = run(item)
    used = set()

    def collect(value):
        if isinstance(value, dict) and set(value) == {"$doc"}:
            if value["$doc"] not in used:
                used.add(value["$doc"])
                collect(DOCUMENTS[value["$doc"]])
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for item in cases:
        for value in item["arguments"].values():
            collect(value)
    documents = {digest: DOCUMENTS[digest] for digest in sorted(used)}
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "documents": documents, "cases": cases},
                                   ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii"))
    counts: dict[str, int] = {}
    for item in cases:
        outcome = "ok" if "ok" in item["expected"] else item["expected"]["error"][0]
        key = f"{item['function']} {outcome}"
        counts[key] = counts.get(key, 0) + 1
    print(len(cases), "cases,", len(recorded), "recorded;", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
