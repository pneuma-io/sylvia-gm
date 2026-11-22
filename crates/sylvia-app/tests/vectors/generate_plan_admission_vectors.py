"""Generate reference vectors for sylvia_app::plan_spatial_admission and plan_network_admission.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_plan_admission_vectors.py

Inputs:
- every shipped Campaign Plan that binds the SRD Ruleset, against the SRD record's own
  `ui_surfaces` (the Rust test also checks its manifest gives the same surfaces), with the test
  suite's tactical scene and network graph authored as an object-source field or a state source,
  and mutations of them;
- the network-graph test fixture against its two surfaces and variants, with mutations;
- an offset-prop scene for `plan_spatial_warnings`, with mutations of its geometry and grid.
Every case runs all three functions. Outcomes are "ok" (with the warnings) or the exception
class, message and diagnostics.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from sylvia_gm.application.plan_network_admission import validate_plan_networks  # noqa: E402
from sylvia_gm.application.plan_spatial_admission import (  # noqa: E402
    plan_spatial_warnings,
    validate_plan_spatial_scenes,
)
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.package_library import SurfaceDeclaration, manifest_from_record  # noqa: E402
from tests.test_plan_network_admission import _fixture, _graph  # noqa: E402
from tests.test_tactical_grid_ui import _scene  # noqa: E402

OUTPUT = Path(__file__).with_name("plan_admission_vectors.json")
SRD = "rulesets/srd-lite/package-record.json"
rng = random.Random(20261122)
JUNK = [None, True, False, 0, 1, 1.5, -1, "", " x", "x", "operator", "device", "map", "missing", "console",
        "gate", "blocking", "passable", "box", [], ["x"], [0, 0], [999, 999], {}, {"k": 1}, {"visible": True},
        "y" * 129]
NUMBERS = [0, 0.25, 0.5, 1, 1.2, 1.5, 2, 2.4, 2.5, 3, 3.6, 4, 4.5, 5.5, 6, 7.9, 8]


def outcome(call):
    try:
        result = call()
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics]]
    return ["ok"] if result is None else ["ok", result]


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def variants(document, count, prefixes, junk=JUNK):
    paths = [(path, value) for path, value in leaves(document)
             if any(path[:len(prefix)] == prefix and len(path) > len(prefix) for prefix in prefixes)]
    yield [], document
    for _ in range(count):
        path, value = rng.choice(paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            edits = [["delete", [*path, rng.choice(list(value))], None]]
        elif isinstance(value, list) and value and rng.random() < 0.2:
            edits = [["delete", [*path, len(value) - 1], None]]
        else:
            edits = [["set", list(path), rng.choice(junk)]]
        try:
            yield edits, apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue


def declared(specs):
    return [SurfaceDeclaration(identity=item["identity"], capability=item["capability"],
                               renderer=item["renderer"], source=item["source"]) for item in specs]


def spec_of(surface):
    source = None if surface.source is None else thaw_json_value(surface.source)
    return {"identity": surface.identity, "capability": surface.capability, "renderer": surface.renderer,
            "source": source}


def main() -> None:
    srd = manifest_from_record(json.loads((ROOT / SRD).read_text(encoding="utf-8")), format_version=4)
    ns = srd.package_id
    srd_surfaces = [spec_of(item) for item in srd.ui_surfaces]
    map_id = next(item["identity"] for item in srd_surfaces if item["renderer"] == "coordinate_map")
    network = {"identity": "srd-network", "capability": f"{ns}.network", "renderer": "network_graph",
               "source": {"kind": "state", "namespace": ns, "key": "state.graph"}}
    state_map = [dict(item, source={"kind": "state", "namespace": ns, "key": "state.surface"})
                 if item["identity"] == map_id else item for item in srd_surfaces]
    network_fields = dict(network, source={"kind": "object", "fields": {
        "graph": {"kind": "state", "namespace": ns, "key": "state.graph"},
        "label": {"kind": "literal", "value": "Dock"}}})
    fixture_payload, fixture_surfaces, _ = _fixture()
    fixture_specs = [spec_of(item) for item in fixture_surfaces]
    warn_scene = _scene()
    crate = copy.deepcopy(warn_scene["geometry"][0])
    crate.update(id="crate", volume={"kind": "box", "min": [2.4, 2.4, 0], "max": [3.6, 3.6, 1.2]},
                 occupies_cells=True)
    wall = dict(copy.deepcopy(crate), id="wall", volume={"kind": "box", "min": [5, 1, 0], "max": [5.5, 4.5, 2]})
    warn_scene["geometry"] = [crate, wall]
    warn_surface = {"identity": "map", "capability": "example.map", "renderer": "tactical_grid",
                    "source": {"kind": "state", "namespace": "example", "key": "state"}}
    many = copy.deepcopy(warn_scene)
    many["geometry"] = [dict(copy.deepcopy(crate), id=f"crate-{index}", volume={
        "kind": "box", "min": [0.5 + (index % 7), 0.5 + (index // 7) * 2, 0],
        "max": [1.5 + (index % 7), 1.5 + (index // 7) * 2, 1]}) for index in range(20)]
    surfaces = {
        "srd": srd_surfaces,
        "srd-network": [*srd_surfaces, network],
        "srd-state": [*state_map, network],
        "srd-network-fields": [*srd_surfaces, network_fields],
        "fixture": fixture_specs,
        "fixture-network-only": fixture_specs[:1],
        "fixture-coordinate": [fixture_specs[0], dict(fixture_specs[1], renderer="coordinate_map")],
        "fixture-unsourced": [dict(fixture_specs[0], source=None), dict(fixture_specs[1], source=None)],
        "fixture-object-map": [fixture_specs[0], dict(fixture_specs[1], source={"kind": "object", "fields": {
            "scene": {"kind": "state", "namespace": "example", "key": "state.map.scene"}}})],
        "warn": [warn_surface],
        "warn-twice": [warn_surface, dict(warn_surface, identity="map-2", renderer="coordinate_map")],
    }
    srd_graph = _graph()
    srd_graph["nodes"]["console"]["physical_binding"] = {"surface_id": map_id, "entity_id": "operator"}
    value = ["initial_state", "mechanics", 0, "value"]
    setups = {
        "plain": [],
        "scene": [["set", [*value, "spatial_scene"], _scene()], ["set", [*value, "graph"], srd_graph]],
        "surface": [["set", [*value, "surface"], {"scene": _scene()}], ["set", [*value, "graph"], srd_graph]],
    }
    payloads = {
        "fixture": fixture_payload,
        "warn": {"initial_state": {"mechanics": [{"namespace": "example", "key": "state",
                                                  "value": {"scene": warn_scene}}]}},
        "many": {"initial_state": {"mechanics": [{"namespace": "example", "key": "state",
                                                  "value": {"scene": many}}]}},
    }
    cases = []

    def record(base, setup, surface_set, edits, document):
        declarations = declared(surfaces[surface_set])
        before = copy.deepcopy(document)
        case = {**base, "setup": setup, "surfaces": surface_set, "edits": edits,
                "spatial": outcome(lambda: validate_plan_spatial_scenes(document, declarations)),
                "network": outcome(lambda: validate_plan_networks(document, declarations)),
                "warnings": outcome(lambda: plan_spatial_warnings(document, declarations))}
        assert document == before, "admission must not change the Plan"
        cases.append(case)

    plans = []
    for path in sorted(glob.glob(str(ROOT / "campaign-plans/*/plan.json"))):
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
        if (plan["bindings"].get("ruleset") or {}).get("id") != ns:
            continue
        assert plan["initial_state"]["mechanics"][0]["key"] == "state"
        plans.append((str(Path(path).relative_to(ROOT)).replace(chr(92), "/"), plan))
    for index, (relative, plan) in enumerate(plans):
        base = {"plan": relative, "payload": None}
        heavy = index == 0
        record(base, "plain", "srd", [], plan)
        record(base, "plain", "srd-network", [], plan)
        for setup, surface_set, count in [("scene", "srd-network", 220), ("surface", "srd-state", 120),
                                          ("scene", "srd-network-fields", 30), ("scene", "srd", 20)]:
            document = apply(plan, setups[setup])
            prefixes = [tuple([*value, key]) for key in ("spatial_scene", "surface", "graph")]
            for edits, varied in variants(document, count if heavy else 0, prefixes):
                record(base, setup, surface_set, edits, varied)
    fixture_value = ("initial_state", "mechanics", 0, "value")
    for surface_set, count in [("fixture", 320), ("fixture-network-only", 40), ("fixture-coordinate", 40),
                               ("fixture-unsourced", 5), ("fixture-object-map", 40)]:
        for edits, varied in variants(fixture_payload, count, [fixture_value]):
            record({"plan": None, "payload": "fixture"}, "plain", surface_set, edits, varied)
    graph = [*fixture_value, "graph"]
    targeted = [
        [["set", [*graph, "links", "cable", "target"], "missing"]],
        [["set", [*graph, "links", "cable", "source"], []]],
        [["set", [*graph, "nodes", "gate", "group_id"], "missing"]],
        [["set", [*graph, "nodes", "gate", "group_id"], []]],
        [["set", [*graph, "nodes", "console", "physical_binding", "surface_id"], "missing"]],
        [["set", [*graph, "nodes", "console", "physical_binding", "surface_id"], ["map"]]],
        [["set", [*graph, "nodes", "console", "physical_binding", "entity_id"], "missing"]],
        [["set", [*graph, "nodes", "console", "physical_binding", "entity_id"], {"id": "device"}]],
        [["set", [*graph, "nodes", "gate", "visible"], "false"]],
        [["set", [*graph, "nodes", "gate", "functions", "open", "enabled"], "yes"]],
        [["set", [*graph, "nodes", "gate", "private_secret"], "NEVER_ECHO_THIS"]],
        [["set", [*graph, "nodes", "gate", "functions"], None]],
        [["delete", [*graph, "nodes", "gate", "functions"], None]],
        [["set", [*graph, "nodes", " gate"], {"visible": True}]],
        [["set", [*graph, "nodes", "g" * 129], {"visible": True}]],
        [["set", [*graph, "groups"], {"g": {"visible": False, "label": "G"}}],
         ["set", [*graph, "nodes", "gate", "group_id"], "g"]],
        [["set", [*graph, "nodes", "gate", "functions"], {f"f{n}": {"label": "F", "enabled": True,
                                                                     "visible": False} for n in range(65)}]],
        [["delete", [*fixture_value, "map"], None]],
        [["delete", [*fixture_value, "graph"], None]],
        [["set", graph, None]],
        [["set", graph, []]],
        [["set", [*fixture_value, "map", "scene", "entities"], "device"]],
        [["set", [*fixture_value, "map", "scene", "entities"], [{"id": 1}, "device", {"id": "other"}]]],
        [["set", [*fixture_value, "map", "scene"], None]],
    ]
    for surface_set in ["fixture", "fixture-network-only", "fixture-coordinate", "fixture-object-map"]:
        for edits in targeted:
            record({"plan": None, "payload": "fixture"}, "plain", surface_set, edits, apply(fixture_payload, edits))
    scene_path = ("initial_state", "mechanics", 0, "value", "scene")
    for surface_set, count in [("warn", 160), ("warn-twice", 20)]:
        document = payloads["warn"]
        geometry = [(tuple([*scene_path, "geometry", index, "volume", bound]), axis)
                    for index in range(2) for bound in ("min", "max") for axis in range(3)]
        record({"plan": None, "payload": "warn"}, "plain", surface_set, [], document)
        for _ in range(count):
            choice = rng.random()
            if choice < 0.6:
                edits = [["set", [*path, axis], rng.choice(NUMBERS)] for path, axis in rng.sample(geometry, 2)]
            elif choice < 0.75:
                edits = [["set", [*scene_path, "map", "grid", "cell_size"], rng.choice([0.5, 1, 1.5, 2, 3])]]
            elif choice < 0.85:
                edits = [["set", [*scene_path, "map", "grid", "origin"], rng.choice([[0, 0], [0.25, 0.5], [1, 1]])]]
            else:
                field, junk = rng.choice([("occupies_cells", [False, True]), ("movement", ["passable", "blocking"]),
                                          ("active", [False, True])])
                edits = [["set", [*scene_path, "geometry", rng.randrange(2), field], rng.choice(junk)]]
            record({"plan": None, "payload": "warn"}, "plain", surface_set, edits, apply(document, edits))
    record({"plan": None, "payload": "many"}, "plain", "warn", [], payloads["many"])
    record({"plan": None, "payload": "many"}, "plain", "warn-twice", [], payloads["many"])
    vectors = {"python": sys.version.split()[0], "srd": SRD, "surfaces": surfaces, "setups": setups,
               "payloads": payloads, "cases": cases}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n"
    assert str(ROOT) not in text and str(ROOT).replace(chr(92), "/") not in text
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    kinds = {}
    for case in cases:
        for name in ("spatial", "network", "warnings"):
            result = case[name]
            key = name + ": " + ("ok" if result[0] == "ok" and len(result) == 1 else
                                 f"ok {len(result[1])}" if result[0] == "ok" else result[0] + " " + result[1][:50])
            kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


if __name__ == "__main__":
    main()
