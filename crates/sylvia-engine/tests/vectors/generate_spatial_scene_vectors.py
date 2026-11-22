"""Generate spatial scene-admission vectors for sylvia_engine::spatial from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_spatial_scene_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a JSON value given to `admit_spatial_scene`. The record is the admitted scene's
payload (in its key order), map id, revision and snapshot id, or the exception's class and
`str(...)` with a `SpatialValidationError`'s code and path.

The corpus: a valid base scene; targeted edits for every check in `_capture_payload` and
`_validate_scene`, with both sides of each limit (depth, node count, identity length, the
coordinate and minimum-size bounds, axis and total cell counts, metadata bytes, layer and object
counts) and the int-or-float spelling of numbers; then a seeded random corpus of edits to the
base scene.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from pathlib import Path

from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.spatial import SpatialValidationError, admit_spatial_scene, mutate_spatial_scene

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "spatial_scene_vectors.json"
MUTATION_OUTPUT = HERE / "spatial_mutation_vectors.json"

BASE = {
    "format": 1,
    "map": {
        "id": "keep",
        "revision": 3,
        "bounds": {"min": [0, 0, 0], "max": [20, 15.5, 10]},
        "grid": {"cell_size": 1, "unit": "ft", "origin": [0, 0]},
        "layers": [{"id": "upper", "min_elevation": 5, "max_elevation": 10},
                   {"id": "ground", "min_elevation": 0, "max_elevation": 5.0}],
    },
    "geometry": [
        {"id": "wall.b", "map_id": "keep", "volume": {"kind": "box", "min": [2, 2, 0], "max": [3, 8, 4]},
         "active": True, "movement": "blocking", "visibility": "opaque", "metadata": {"label": "Wall"},
         "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}},
        {"id": "pillar", "map_id": "keep", "volume": {"kind": "box", "min": [10.5, 4, 0], "max": [11.5, 5, 6.5]},
         "active": True, "movement": "blocking", "occupies_cells": False, "visibility": "clear", "metadata": {},
         "destroyed": {"active": True, "movement": "passable", "visibility": "clear"}},
    ],
    "entities": [
        {"id": "orc", "map_id": "keep", "position": [5.5, 5.5, 0], "footprint": {"kind": "circle", "radius": 0.5},
         "height": 2, "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "clear",
         "metadata": {"name": "Orc", "nested": {"a": [1, 2.5, None]}}},
        {"id": "cart", "map_id": "keep", "position": [8, 9, 0.0], "footprint": {"kind": "box", "half_extents": [1, 0.5]},
         "height": 1.5, "facing": [0, -1], "active": False, "movement": "passable", "visibility": "opaque",
         "metadata": {}},
    ],
    "zones": [
        {"id": "pool", "map_id": "keep", "shape": {"kind": "cylinder", "center": [15, 10, 0], "radius": 2, "height": 0.5},
         "metadata": {"hazard": True}},
        {"id": "dais", "map_id": "keep", "shape": {"kind": "box", "min": [0, 0, 0], "max": [4, 4, 1]}, "metadata": {}},
    ],
    "terrain": [
        {"cell": [3, 1], "layer_id": "ground", "cost": 2},
        {"cell": [0, 1], "layer_id": "ground", "cost": 1.5},
        {"cell": [0, 1], "layer_id": "upper", "cost": 3},
        {"cell": [19, 0], "layer_id": "ground", "cost": 0.000001},
    ],
}


def run(value: object) -> dict:
    try:
        admitted = admit_spatial_scene(value)
    except SpatialValidationError as error:
        return {"error": ["SpatialValidationError", str(error), error.code, error.path]}
    except Exception as error:  # noqa: BLE001 - every exception Python lets through
        return {"error": [type(error).__name__, str(error)]}
    payload = admitted.as_payload()
    if len(json.dumps(payload)) > 200_000:
        # Large scenes are compared by their canonical bytes; small ones keep their key order.
        payload = {"canonical_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}
    return {"payload": payload, "map_id": admitted.map_id, "revision": admitted.geometry_revision,
            "snapshot_id": admitted.snapshot_id, "algorithm_version": admitted.algorithm_version}


def edited(path: list, value: object = None, *, delete: bool = False) -> dict:
    scene = copy.deepcopy(BASE)
    target = scene
    for step in path[:-1]:
        target = target[step]
    if delete:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return scene


def targeted() -> list:
    cases: list = [BASE, None, [], "scene", 1, {}]
    for key in ("format", "map", "geometry", "entities", "zones", "terrain"):
        cases.append(edited([key], delete=True))
    cases.append({**copy.deepcopy(BASE), "extra": 1})
    for value in (2, 1.0, True, False, "1", None, 0):
        cases.append(edited(["format"], value))
    # The map.
    for value in ("", "a" * 128, "a" * 129, "-a", "a-b.c_d:e", "a b", "ä", "a\n", 5, None, "A9"):
        cases.append(edited(["map", "id"], value))
    for value in (0, -1, 2**63 - 1, 2**63, 1.0, True, "3", None):
        cases.append(edited(["map", "revision"], value))
    for field, value in (("min", [0, 0]), ("min", [0, 0, 0, 0]), ("min", "abc"), ("min", [0, 0, "0"]), ("min", [0, True, 0]),
                         ("max", [20, 15.5, 0]), ("max", [0, 15, 10]), ("max", [1000000, 15, 10]), ("max", [1000000.0001, 15, 10]),
                         ("min", [-1000000, 0, 0]), ("min", [-1000001, 0, 0]), ("max", [20, 15.5, -1])):
        cases.append(edited(["map", "bounds", field], value))
    cases.append(edited(["map", "bounds"], {"min": [0, 0, 0]}))
    cases.append(edited(["map", "bounds"], [0, 1]))
    for field, value in (("cell_size", 0), ("cell_size", 0.000001), ("cell_size", 0.0000009), ("cell_size", -1), ("cell_size", "1"),
                         ("cell_size", 0.5), ("cell_size", 3), ("unit", "m"), ("unit", ""), ("unit", 3),
                         ("origin", [0, 0.0]), ("origin", [0.0, 0]), ("origin", [1, 0]), ("origin", [0, 0, 0]), ("origin", [0])):
        cases.append(edited(["map", "grid", field], value))
    cases.append(edited(["map", "grid"], {"cell_size": 1, "unit": "ft"}))
    # Cell counts: 2048 cells on an axis is the limit; 4,000,000 cells in all.
    for upper, size in (([2048, 15.5, 10], 1), ([2049, 15.5, 10], 1), ([2048.5, 15.5, 10], 1), ([20, 2048, 10], 1),
                        ([2048, 1953, 10], 1), ([2048, 1954, 10], 1), ([2000, 2000, 10], 1), ([1024, 1024, 10], 0.5),
                        ([1024, 1024, 10], 0.4999), ([20, 15.5, 10], 0.000001)):
        scene = edited(["map", "bounds", "max"], upper)
        scene["map"]["grid"]["cell_size"] = size
        cases.append(scene)
    for value in ([], [{"id": "g", "min_elevation": 0, "max_elevation": 10}] * 128,
                  [{"id": f"l{index}", "min_elevation": 0, "max_elevation": 10} for index in range(128)],
                  [{"id": f"l{index}", "min_elevation": 0, "max_elevation": 10} for index in range(129)],
                  [{"id": "g", "min_elevation": 0, "max_elevation": 10}, {"id": "g", "min_elevation": 0, "max_elevation": 5}],
                  [{"id": "g", "min_elevation": 5, "max_elevation": 5}], [{"id": "g", "min_elevation": -1, "max_elevation": 5}],
                  [{"id": "g", "min_elevation": 0, "max_elevation": 10.5}], [{"id": "g", "min_elevation": "0", "max_elevation": 5}],
                  [{"id": "g", "min_elevation": 0}], ["g"], [{"id": "z", "min_elevation": 0.0, "max_elevation": 10},
                                                             {"id": "a", "min_elevation": 1, "max_elevation": 2}]):
        cases.append(edited(["map", "layers"], value))
    # Geometry.
    wall = ["geometry", 0]
    for field, value in (("id", "orc"), ("id", "pillar"), ("id", "x" * 129), ("map_id", "other"), ("map_id", None),
                         ("active", 1), ("active", None), ("movement", "solid"), ("movement", "Blocking"), ("occupies_cells", 0),
                         ("visibility", "dark"), ("metadata", []), ("metadata", None), ("metadata", {"a": "x" * 16375}),
                         ("metadata", {"a": "x" * 16376}), ("metadata", {"a": "x" * 16377}), ("metadata", {"é": "ü" * 3000}),
                         ("destroyed", {"active": True, "movement": "passable"}), ("destroyed", {"active": "no", "movement": "passable", "visibility": "clear"}),
                         ("destroyed", {"active": True, "movement": "x", "visibility": "clear"}),
                         ("destroyed", {"active": True, "movement": "passable", "visibility": "x"}), ("destroyed", None),
                         ("volume", {"kind": "box", "min": [2, 2, 0], "max": [2, 8, 4]}), ("volume", {"kind": "cube", "min": [2, 2, 0], "max": [3, 8, 4]}),
                         ("volume", {"kind": "box", "min": [2, 2, 0], "max": [3, 8, 11]}), ("volume", {"kind": "box", "min": [2, 2, 0], "max": [3, 8, 10]}),
                         ("volume", {"kind": "box", "min": [-0.0, 2, 0], "max": [3, 8, 4]}), ("volume", {"kind": "box", "min": [2, 2, 0]}),
                         ("volume", {"kind": "cylinder", "center": [5, 5, 0], "radius": 1, "height": 1}), ("volume", "box")):
        cases.append(edited(wall + [field], value))
    cases.append(edited(wall + ["visibility"], delete=True))
    cases.append(edited(wall + ["occupies_cells"], True))
    # Every scene also counts against the 100,000-node limit, which comes first: geometry
    # (20,000) and terrain (100,000) can't reach their own limits, entities and zones can.
    for key, count in (("geometry", 4000), ("geometry", 20000), ("entities", 5000), ("entities", 5001), ("zones", 5000),
                       ("zones", 5001), ("terrain", 2)):
        cases.append({"recipe": {"copies": key, "count": count}})
    cases.append(edited(["geometry"], "walls"))
    # Entities.
    orc = ["entities", 0]
    for field, value in (("id", "wall.b"), ("id", "pool"), ("map_id", "keep2"), ("position", [0.5, 0.5, 0]), ("position", [0.4, 5, 0]),
                         ("position", [19.5, 15, 0]), ("position", [19.6, 15, 0]), ("position", [5, 5, 8]), ("position", [5, 5, 8.1]),
                         ("position", [5, 5]), ("footprint", {"kind": "circle", "radius": 0}), ("footprint", {"kind": "circle", "radius": 0.000001}),
                         ("footprint", {"kind": "circle"}), ("footprint", {"kind": "square", "radius": 1}), ("footprint", {"radius": 1}),
                         ("footprint", {"kind": "box", "half_extents": [1, 0]}), ("footprint", {"kind": "box", "half_extents": [1, -1]}),
                         ("footprint", {"kind": "box", "half_extents": [1]}), ("footprint", {"kind": "box", "radius": 1}),
                         ("footprint", [1, 2]), ("footprint", "circle"), ("footprint", 3), ("footprint", 1.5), ("footprint", None),
                         ("footprint", True), ("height", 0), ("height", 0.0000009), ("height", 10), ("facing", [0, 0]), ("facing", [0.0, -0.0]),
                         ("facing", [1e-300, 0]), ("facing", [0, 0, 1]), ("active", "true"), ("movement", None), ("visibility", "clear ")):
        cases.append(edited(orc + [field], value))
    cases.append(edited(orc + ["facing"], delete=True))
    # The cart's box footprint is 1 wide and 0.5 deep: its y edge is position y + 0.5.
    for position in ([8, 15, 0], [8, 15.1, 0], [19, 9, 0], [19.1, 9, 0], [1, 0.5, 0], [1, 0.4, 0]):
        cases.append(edited(["entities", 1, "position"], position))
    # Depth: the root is depth 1, so metadata's 21-deep list reaches 24 and 22 deep reaches 25.
    for depth in (19, 20, 21, 22, 30):
        nested: object = 1
        for _ in range(depth):
            nested = [nested]
        cases.append(edited(orc + ["metadata"], {"deep": nested}))
    # Node count: 100,000 values in all is the limit.
    base_nodes = count_nodes(BASE)
    for extra in (100000 - base_nodes - 1, 100000 - base_nodes, 100000 - base_nodes + 1):
        cases.append(edited(orc + ["metadata"], {"name": "Orc", "nested": {"a": [1, 2.5, None]}, "pad": [0] * (extra - 1)}))
    # Zones.
    pool = ["zones", 0]
    for field, value in (("id", "orc"), ("map_id", 1), ("shape", {"kind": "cylinder", "center": [15, 10, 0], "radius": 5, "height": 0.5}),
                         ("shape", {"kind": "cylinder", "center": [15, 10, 0], "radius": 5.5, "height": 0.5}),
                         ("shape", {"kind": "cylinder", "center": [15, 10, 0], "radius": 2, "height": 10}),
                         ("shape", {"kind": "cylinder", "center": [15, 10, 0], "radius": 2, "height": 10.5}),
                         ("shape", {"kind": "cylinder", "center": [15, 10], "radius": 2, "height": 1}),
                         ("shape", {"kind": "cylinder", "center": [15, 10, 0], "radius": 0, "height": 1}),
                         ("shape", {"kind": "sphere", "center": [15, 10, 0], "radius": 2}), ("shape", {"kind": "cylinder", "radius": 2}),
                         ("shape", {"kind": "box", "min": [0, 0, 0], "max": [20, 15.5, 10]}), ("shape", {"kind": "box"}), ("shape", []),
                         ("shape", None), ("metadata", {"x": float("1e300")})):
        cases.append(edited(pool + [field], value))
    # Terrain.
    for value in ([{"cell": [19, 15], "layer_id": "ground", "cost": 1}], [{"cell": [20, 0], "layer_id": "ground", "cost": 1}],
                  [{"cell": [0, 16], "layer_id": "ground", "cost": 1}], [{"cell": [0, 15], "layer_id": "ground", "cost": 1}],
                  [{"cell": [-1, 0], "layer_id": "ground", "cost": 1}], [{"cell": [2048, 0], "layer_id": "ground", "cost": 1}],
                  [{"cell": [0.0, 0], "layer_id": "ground", "cost": 1}], [{"cell": [True, 0], "layer_id": "ground", "cost": 1}],
                  [{"cell": [0, 0], "layer_id": "cellar", "cost": 1}], [{"cell": [0, 0], "layer_id": "ground", "cost": 0}],
                  [{"cell": [0, 0], "layer_id": "ground", "cost": 1}, {"cell": [0, 0], "layer_id": "ground", "cost": 2}],
                  [{"cell": [0, 0], "layer_id": "ground", "cost": 1}, {"cell": [0, 0], "layer_id": "upper", "cost": 2}],
                  [{"cell": [1, 0], "layer_id": "ground", "cost": 1}, {"cell": [0, 1], "layer_id": "ground", "cost": 1},
                   {"cell": [0, 0], "layer_id": "upper", "cost": 1}, {"cell": [0, 0], "layer_id": "ground", "cost": 1}],
                  [{"cell": [0, 0], "layer_id": "ground"}], [{"cell": [0], "layer_id": "ground", "cost": 1}], "x",
                  [{"cell": [0, 0], "layer_id": "ground", "cost": 1}] * 0):
        cases.append(edited(["terrain"], value))
    # Identities are unique across geometry, entities and zones; the first repeat is reported.
    cases.append(edited(["zones", 1, "id"], "wall.b"))
    cases.append(edited(["entities", 1, "id"], "orc"))
    return cases


def count_nodes(value: object) -> int:
    if isinstance(value, dict):
        return 1 + sum(count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(count_nodes(item) for item in value)
    return 1


POOL = [None, True, False, 0, 1, -1, 2, 0.5, 1.0, -0.0, 1e-6, 1e6, 1000000.5, 2048, 3.25, "", "keep", "ground", "upper",
        "box", "circle", "cylinder", "clear", "opaque", "blocking", "passable", "orc", [], [0, 0], [1, 2, 3], [0.5, 0.5, 0],
        {}, {"kind": "box"}, {"a": 1}]


def random_path(rng: random.Random, value: object, path: list) -> list:
    while isinstance(value, (dict, list)) and value and rng.random() < 0.8:
        if isinstance(value, dict):
            key = rng.choice(list(value))
        else:
            key = rng.randrange(len(value))
        path = path + [key]
        value = value[key]
    return path


def build(value: object) -> object:
    """Expand a recipe: `copies` repeats the first item of that list in the base scene `count`
    times, the copies' ids (when the items have ids) `{key[0]}00000`, `{key[0]}00001` and so on,
    and their metadata `{}`."""
    if isinstance(value, dict) and set(value) == {"recipe"}:
        key, count = value["recipe"]["copies"], value["recipe"]["count"]
        first = BASE[key][0]
        extra = {"metadata": {}} if "metadata" in first else {}
        items = [copy.deepcopy(first) | ({"id": f"{key[0]}{index:05d}"} if "id" in first else {}) | extra
                 for index in range(count)]
        return edited([key], items)
    return value


def valid_variant(rng: random.Random) -> object:
    """A scene that should stay admissible: reordered lists, numbers respelled, objects added."""
    scene = copy.deepcopy(BASE)
    for key in ("geometry", "entities", "zones", "terrain"):
        rng.shuffle(scene[key])
    rng.shuffle(scene["map"]["layers"])
    for _ in range(rng.randint(0, 4)):
        kind = rng.choice(["geometry", "entities", "zones", "terrain"])
        if kind == "terrain":
            cell = [rng.randrange(20), rng.randrange(16)]
            if not any(item["cell"] == cell and item["layer_id"] == "ground" for item in scene["terrain"]):
                scene["terrain"].append({"cell": cell, "layer_id": "ground", "cost": rng.choice([1, 2.5, 0.25])})
            continue
        item = copy.deepcopy(rng.choice(BASE[kind]))
        item["id"] = f"{kind[0]}{rng.randrange(10**6)}"
        scene[kind].append(item)
    def respell(value):
        if isinstance(value, dict):
            # Revisions and terrain cells must stay integers.
            return {key: item if key in ("revision", "cell") else respell(item) for key, item in value.items()}
        if isinstance(value, list):
            return [respell(item) for item in value]
        if isinstance(value, int) and not isinstance(value, bool) and rng.random() < 0.3:
            return float(value)
        if isinstance(value, float) and value.is_integer() and rng.random() < 0.3:
            return int(value)
        return value
    scene = respell(scene)
    if rng.random() < 0.3:
        scene = dict(reversed(list(scene.items())))
    return scene


def random_case(rng: random.Random) -> object:
    scene = copy.deepcopy(BASE)
    for _ in range(rng.randint(1, 3)):
        path = random_path(rng, scene, [])
        if not path:
            continue
        target = scene
        for step in path[:-1]:
            target = target[step]
        action = rng.random()
        if action < 0.15 and isinstance(target, dict):
            del target[path[-1]]
        elif action < 0.25 and isinstance(target, list):
            target.append(copy.deepcopy(target[path[-1]]))
        elif action < 0.3 and isinstance(target, dict):
            target["extra"] = 1
        elif action < 0.6 and isinstance(target[path[-1]], (int, float)) and not isinstance(target[path[-1]], bool):
            target[path[-1]] = rng.choice([target[path[-1]] + rng.choice([-1, 1, 0.5, -0.5, 1e-6]), float(target[path[-1]]),
                                           -target[path[-1]], target[path[-1]] * 100])
        else:
            target[path[-1]] = copy.deepcopy(rng.choice(POOL))
    return scene


def sha(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def run_mutation(scene_input: object, request: object) -> dict:
    scene = admit_spatial_scene(scene_input)
    try:
        mutation = mutate_spatial_scene(scene, request)
    except SpatialValidationError as error:
        return {"error": ["SpatialValidationError", str(error), error.code, error.path]}
    except Exception as error:  # noqa: BLE001 - every exception Python lets through
        return {"error": [type(error).__name__, str(error)]}
    advanced = mutation.scene
    return {"payload": mutation.as_payload(), "scene": {"map_id": advanced.map_id, "revision": advanced.geometry_revision,
                                                        "snapshot_id": advanced.snapshot_id,
                                                        "canonical_sha256": sha(advanced.as_payload())}}


def request(patch: object, *, kind: str = "geometry", target: str = "wall.b", revision: object = 3,
            map_id: object = "keep") -> dict:
    return {"map_id": map_id, "expected_revision": revision, "target": {"kind": kind, "id": target}, "patch": patch}


def deep(levels: int) -> object:
    value: object = 1
    for _ in range(levels):
        value = [value]
    return value


GEOMETRY_PATCHES = [
    {"active": False}, {"movement": "passable", "visibility": "clear"}, {"occupies_cells": False},
    {"volume": {"kind": "box", "min": [1, 1, 0], "max": [2, 2, 1.5]}}, {"metadata": {"label": "Broken", "hp": 0}},
    {"metadata": {}, "active": True, "volume": {"kind": "box", "min": [0, 0, 0], "max": [20, 15.5, 10]}},
    {"volume": {"kind": "box", "min": [1, 1, 0], "max": [21, 2, 1]}}, {"volume": {"kind": "box", "min": [1, 1, 0], "max": [1, 2, 1]}},
    {"volume": {"kind": "cylinder", "center": [1, 1, 0], "radius": 1, "height": 1}}, {"active": "yes"}, {"movement": "solid"},
    {"metadata": []}, {"metadata": {"a": "x" * 16377}}, {"id": "new"}, {"map_id": "keep"}, {"destroyed": {}}, {}, [],
    {"active": False, "colour": "red"}, None, "active", {"metadata": {"deep": deep(21)}}, {"metadata": {"deep": deep(22)}},
    {"metadata": {"deep": deep(25)}},
]
ENTITY_PATCHES = [
    {"position": [6.5, 6.5, 0]}, {"position": [6.5, 6.5]}, {"position": [0.1, 6.5, 0]}, {"facing": [0, 1]}, {"facing": [0, 0]},
    {"footprint": {"kind": "box", "half_extents": [0.5, 0.5]}}, {"footprint": [1]}, {"footprint": "circle"}, {"footprint": None},
    {"footprint": {"kind": "circle", "radius": 6}}, {"height": 3.5}, {"height": 0}, {"active": False, "movement": "passable"},
    {"visibility": "opaque"}, {"metadata": {"name": "Orc chief"}}, {"volume": {"kind": "box", "min": [1, 1, 0], "max": [2, 2, 1]}},
    {"occupies_cells": True}, {"id": "orc2"},
]


def mutation_targeted() -> list:
    cases = []
    for patch in GEOMETRY_PATCHES:
        cases.append((BASE, request(patch)))
        cases.append((BASE, request(patch, target="pillar")))
    for patch in ENTITY_PATCHES:
        cases.append((BASE, request(patch, kind="entity", target="orc")))
        cases.append((BASE, request(patch, kind="entity", target="cart")))
    for item in (request({"active": False}, map_id="other"), request({"active": False}, map_id=None),
                 request({"active": False}, revision=2), request({"active": False}, revision=4),
                 request({"active": False}, revision=3.0), request({"active": False}, revision=True),
                 request({"active": False}, revision=-1), request({"active": False}, revision="3"),
                 request({"active": False}, kind="zone", target="pool"), request({"active": False}, kind="entities", target="orc"),
                 request({"active": False}, target="orc"), request({"active": False}, kind="entity", target="wall.b"),
                 request({"active": False}, target="ghost"), request({"active": False}, target="bad id"),
                 request({"active": False}, target=""),
                 {"map_id": "keep", "expected_revision": 3, "target": {"kind": "geometry", "id": "wall.b"}},
                 {"map_id": "keep", "expected_revision": 3, "target": {"kind": "geometry", "id": "wall.b", "x": 1},
                  "patch": {"active": False}},
                 {"map_id": "keep", "expected_revision": 3, "target": ["geometry", "wall.b"], "patch": {"active": False}},
                 {**request({"active": False}), "extra": 1}, None, [], "mutate", {},
                 {**request({"active": False}), "pad": deep(30)}):
        cases.append((BASE, item))
    # Revisions: the first and the last a scene may hold; advancing past the last fails readmission.
    for revision in (0, 2**63 - 2, 2**63 - 1):
        scene = edited(["map", "revision"], revision)
        cases.append((scene, request({"active": False}, revision=revision)))
    # The patched object is re-sorted into place by id; ids don't change.
    many = edited(["geometry"], [copy.deepcopy(BASE["geometry"][0]) | {"id": name} for name in ("c", "a", "b")])
    cases.append((many, request({"active": False}, target="b")))
    cases.append((many, request({"metadata": {"z": 1}, "active": False, "movement": "passable"}, target="a")))
    return cases


def random_patch(rng: random.Random, kind: str) -> dict:
    fields = (["volume", "active", "movement", "occupies_cells", "visibility", "metadata"] if kind == "geometry" else
              ["position", "footprint", "height", "facing", "active", "movement", "visibility", "metadata"])
    patch = {}
    for field in rng.sample(fields, rng.randint(1, 3)):
        source = BASE["geometry"][0] if kind == "geometry" else BASE["entities"][rng.randrange(2)]
        value = copy.deepcopy(source.get(field, True))
        if rng.random() < 0.4:
            value = copy.deepcopy(rng.choice(POOL))
        elif isinstance(value, list):
            value = [item + rng.choice([0, 0.5, -0.5, 1]) if isinstance(item, (int, float)) and not isinstance(item, bool)
                     else item for item in value]
        patch[field] = value
    if rng.random() < 0.1:
        patch[rng.choice(["id", "map_id", "shape", "x"])] = 1
    return patch


def main() -> int:
    mutation_rng = random.Random(4803)
    mutation_inputs = mutation_targeted()
    for _ in range(1500):
        kind = mutation_rng.choice(["geometry", "entity"])
        target = mutation_rng.choice(["wall.b", "pillar"] if kind == "geometry" else ["orc", "cart"])
        mutation_inputs.append((BASE, request(random_patch(mutation_rng, kind), kind=kind, target=target)))
    mutation_cases = [{"scene_input": scene, "request": item, **run_mutation(scene, item)} for scene, item in mutation_inputs]
    MUTATION_OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "cases": mutation_cases}, ensure_ascii=True,
                                            allow_nan=False) + "\n").encode("ascii"))
    applied = sum("payload" in case for case in mutation_cases)
    print(f"{len(mutation_cases)} mutation cases, {applied} applied")
    rng = random.Random(4801)
    inputs = targeted() + [random_case(rng) for _ in range(2500)] + [valid_variant(rng) for _ in range(500)]
    cases = [{"input": value, **run(build(value))} for value in inputs]
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "cases": cases}, ensure_ascii=True, allow_nan=False)
                        + "\n").encode("ascii"))
    admitted = sum("payload" in case for case in cases)
    print(f"{len(cases)} cases, {admitted} admitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
