"""Generate spatial engine vectors for sylvia_engine::spatial from the frozen Python, per platform.

Run with the snapshot's own interpreter, from the repository root, on each platform:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_spatial_engine_vectors.py

(`.venv/bin/python` on Linux.) Windows writes `spatial_engine_vectors.windows.json`, Linux
`spatial_engine_vectors.linux.json`: `_resolve_circle_route` calls `math.cos`, `math.sin` and
`math.atan2`, which are the platform C library's (the Microsoft runtime on Windows, glibc on
Linux). Deterministic and checked in. Reference tooling for the port, retired with the Python
source.

Each case is a scene and a query, both admitted by Python first (inadmissible random draws are
discarded). The record is `_engine._resolve_<kind>(_engine._compile(scene), query.as_payload())`
or the exception it raises. Placement is S3c's and not here.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import random
import sys
from pathlib import Path

from sylvia_gm.spatial import SpatialValidationError, admit_spatial_query, admit_spatial_scene
from sylvia_gm.spatial import _engine

HERE = Path(__file__).resolve().parent
PLATFORM = "windows" if sys.platform == "win32" else "linux"
OUTPUT = HERE / f"spatial_engine_vectors.{PLATFORM}.json"

_spec = importlib.util.spec_from_file_location("scene_fixtures", HERE / "generate_spatial_scene_vectors.py")
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)

RESOLVERS = {
    "ray": _engine._resolve_ray, "exposure": _engine._resolve_exposure, "path": _engine._resolve_path,
    "crossed_zones": _engine._resolve_zones, "area": _engine._resolve_area,
    "body_proximity": _engine._resolve_body_proximity, "circle_overlap": _engine._resolve_circle_overlap,
    "sweep": _engine._resolve_sweep, "circle_route": _engine._resolve_circle_route,
}


def entity(identity, position, footprint, height=2, active=True, movement="blocking", visibility="clear", metadata=None):
    return {"id": identity, "map_id": "hall", "position": position, "footprint": footprint, "height": height, "facing": [1, 0],
            "active": active, "movement": movement, "visibility": visibility, "metadata": metadata or {}}


def wall(identity, lower, upper, *, occupies=True, visibility="opaque", movement="blocking", active=True, metadata=None):
    return {"id": identity, "map_id": "hall", "volume": {"kind": "box", "min": lower, "max": upper}, "active": active,
            "movement": movement, "occupies_cells": occupies, "visibility": visibility, "metadata": metadata or {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}}


HALL = {
    "format": 1,
    "map": {"id": "hall", "revision": 7, "bounds": {"min": [-5, 2.5, -1], "max": [15, 20.5, 9]},
            "grid": {"cell_size": 0.5, "unit": "m", "origin": [-5, 2.5]},
            "layers": [{"id": "floor", "min_elevation": -1, "max_elevation": 9}]},
    "geometry": [
        wall("w.north", [-5, 19.5, -1], [15, 20.5, 4]), wall("w.mid", [2, 6, -1], [2.5, 15, 3.5], metadata={"material": "stone"}),
        wall("w.glass", [6, 8, -1], [6.2, 14, 3], visibility="clear", metadata={"material": "glass"}),
        wall("w.thin", [9.1, 4, -1], [9.15, 12.3, 2.5], occupies=False),
        wall("w.low", [10, 14, -1], [13, 14.4, 0.2], metadata={"material": "wood"}),
        wall("w.off", [-3, 3, -1], [-2, 4, 2], active=False), wall("w.pass", [12, 3, -1], [13, 5, 2], movement="passable"),
        wall("w.edge", [3.25, 17.25, -1], [3.75, 17.75, 8], occupies=False, metadata={"material": "stone"}),
    ],
    "entities": [
        entity("e.knight", [0, 10, -1], {"kind": "circle", "radius": 0.4}, metadata={"side": "red"}),
        entity("e.ogre", [4, 10, -1], {"kind": "circle", "radius": 0.9}, height=3, metadata={"side": "blue"}),
        entity("e.crate", [8, 16, -1], {"kind": "box", "half_extents": [0.5, 0.5]}, height=1, visibility="opaque"),
        entity("e.wolf", [11.5, 9, -1], {"kind": "circle", "radius": 0.3}, height=1, metadata={"side": "blue"}),
        entity("e.cart", [5, 4, -1], {"kind": "box", "half_extents": [1.2, 0.6]}, height=1.5, movement="passable"),
        entity("e.ghost", [13, 17, -1], {"kind": "circle", "radius": 0.5}, active=False),
        entity("e.bat", [7, 12, 4], {"kind": "circle", "radius": 0.2}, height=0.5, metadata={"side": "red"}),
    ],
    "zones": [
        {"id": "z.pit", "map_id": "hall", "shape": {"kind": "cylinder", "center": [9, 17, -1], "radius": 1.5, "height": 1},
         "metadata": {"hazard": "fall"}},
        {"id": "z.room", "map_id": "hall", "shape": {"kind": "box", "min": [-5, 2.5, -1], "max": [2, 19.5, 9]}, "metadata": {}},
        {"id": "z.aura", "map_id": "hall", "shape": {"kind": "cylinder", "center": [4, 10, -1], "radius": 3, "height": 4},
         "metadata": {"hazard": "fear"}},
    ],
    "terrain": [{"cell": [x, y], "layer_id": "floor", "cost": cost}
                for (x, y, cost) in ((10, 10, 2), (11, 10, 2), (12, 10, 3.5), (10, 11, 0.5), (24, 20, 4), (25, 20, 4))],
}


def pillars(count: int, rng: random.Random) -> dict:
    scene = copy.deepcopy(HALL)
    scene["map"]["id"] = "yard"
    scene["map"]["bounds"] = {"min": [0, 0, 0], "max": [30, 30, 6]}
    scene["map"]["grid"] = {"cell_size": 1.5, "unit": "m", "origin": [0, 0]}
    scene["map"]["layers"] = [{"id": "floor", "min_elevation": 0, "max_elevation": 6}]
    scene["geometry"], scene["zones"], scene["terrain"] = [], [], []
    scene["entities"] = [entity("p.mover", [3, 3, 0], {"kind": "circle", "radius": 0.5}, metadata={"side": "red"})]
    for index in range(count):
        scene["entities"].append(entity(f"p.{index:02d}", [round(rng.uniform(5, 25), 3), round(rng.uniform(5, 25), 3), 0],
                                        {"kind": "circle", "radius": rng.choice([0.5, 1, 1.5])}, height=rng.choice([1, 3]),
                                        metadata={"side": rng.choice(["red", "blue"])}))
    scene["geometry"].append(wall("y.wall", [14, 0, 0], [15, 12, 4]))
    for item in scene["entities"] + scene["geometry"]:
        item["map_id"] = "yard"
    return scene


def run(scene_value: dict, query_value: dict) -> dict | None:
    scene = admit_spatial_scene(scene_value)
    try:
        query = admit_spatial_query(scene, query_value)
    except SpatialValidationError:
        return None
    if query.kind not in RESOLVERS:
        return None
    try:
        result = RESOLVERS[query.kind](_engine._compile(scene), query.as_payload())
    except SpatialValidationError as error:
        return {"error": ["SpatialValidationError", str(error), error.code, error.path]}
    return {"result": result}


# Coordinates on the scene's own boundaries (faces, radii, centres), nudged across the engine's
# 1e-12 tolerance, so the tolerance branches are reached.
SNAPS: list[list[float]] | None = None
NUDGES = [0, 0, 0, 1e-13, -1e-13, 5e-13, -5e-13, 1e-12, -1e-12, 3e-12, -3e-12]


def boundary_values(scene: dict) -> list[list[float]]:
    axes: list[set[float]] = [set(), set(), set()]
    for item in scene["geometry"]:
        for i in range(3):
            axes[i].update((item["volume"]["min"][i], item["volume"]["max"][i]))
    for item in scene["entities"]:
        position, footprint = item["position"], item["footprint"]
        extents = footprint.get("half_extents") or [footprint["radius"]] * 2
        for i in range(2):
            axes[i].update((position[i], position[i] - extents[i], position[i] + extents[i]))
        axes[2].update((position[2], position[2] + item["height"]))
    for item in scene["zones"]:
        shape = item["shape"]
        if shape["kind"] == "box":
            for i in range(3):
                axes[i].update((shape["min"][i], shape["max"][i]))
        else:
            for i in range(2):
                axes[i].update((shape["center"][i], shape["center"][i] - shape["radius"], shape["center"][i] + shape["radius"]))
            axes[2].update((shape["center"][2], shape["center"][2] + shape["height"]))
    radii = sorted({item["footprint"]["radius"] for item in scene["entities"] if "radius" in item["footprint"]})
    for i in range(2):
        faces = [value for item in scene["geometry"] for value in (item["volume"]["min"][i], item["volume"]["max"][i])]
        axes[i].update(face + sign * radius for face in faces for radius in radii for sign in (-1, 1))
    return [sorted(float(value) for value in axis) for axis in axes]


def point(rng, bounds, z=None):
    lower, upper = bounds["min"], bounds["max"]
    values = [round(rng.uniform(lower[i], upper[i]), rng.choice([0, 1, 3, 6])) for i in range(3)]
    if SNAPS is not None and rng.random() < 0.5:
        for i in range(3):
            if rng.random() < 0.7:
                values[i] = rng.choice(SNAPS[i]) + rng.choice(NUDGES)
    if z is not None:
        values[2] = z
    return values


def aligned(rng, start, end):
    """Make the segment (nearly) parallel to an axis plane: one coordinate copied, nudged."""
    end = list(end)
    if rng.random() < 0.35:
        axis = rng.randrange(3)
        end[axis] = start[axis] + rng.choice(NUDGES)
    return end


def selector(rng):
    return rng.choice([{"key": "", "value": "", "missing_matches": False}, {"key": "material", "value": "stone", "missing_matches": False},
                       {"key": "material", "value": "glass", "missing_matches": True}, {"key": "side", "value": "red", "missing_matches": True},
                       {"key": "side", "value": "blue", "missing_matches": False}])


def random_query(rng: random.Random, scene: dict) -> dict:
    map_id, bounds = scene["map"]["id"], scene["map"]["bounds"]
    active = [row["id"] for row in scene["entities"] if row["active"]]
    everyone = [row["id"] for row in scene["entities"]]
    kind = rng.choice(list(RESOLVERS))
    floor = bounds["min"][2]
    if kind == "ray":
        origin = point(rng, bounds)
        return {"kind": "ray", "map_id": map_id, "origin": origin, "destination": aligned(rng, origin, point(rng, bounds)),
                "include": rng.choice([["geometry"], ["entities"], ["geometry", "entities"]]), "max_intersections": rng.randint(1, 6)}
    if kind == "exposure":
        query = {"kind": "exposure", "map_id": map_id, "observer": {"point": point(rng, bounds)},
                 "target": {"entity_id": rng.choice(everyone)}, "samples": {"horizontal": rng.randint(1, 9), "vertical": rng.randint(1, 9)},
                 "include": rng.choice([["geometry"], ["entities"], ["geometry", "entities"]])}
        if rng.random() < 0.5:
            query["metadata_filter"] = selector(rng)
        return query
    if kind == "path":
        counts = [math.ceil((bounds["max"][i] - bounds["min"][i]) / scene["map"]["grid"]["cell_size"]) for i in range(2)]
        query = {"kind": "path", "map_id": map_id, "start": [rng.randrange(counts[0]), rng.randrange(counts[1])],
                 "goal": [rng.randrange(counts[0]), rng.randrange(counts[1])], "allow_diagonal": rng.random() < 0.6,
                 "corner_policy": rng.choice(["allow_cutting", "forbid_cutting"]), "include_entities": rng.random() < 0.7,
                 "max_steps": rng.choice([5, 50, 400, 100000])}
        if rng.random() < 0.4 and query["include_entities"]:
            chosen = rng.choice(active)
            mover = next(row for row in scene["entities"] if row["id"] == chosen)
            query["moving_entity_id"] = mover["id"]
            cell = [math.floor((mover["position"][i] - scene["map"]["grid"]["origin"][i]) / scene["map"]["grid"]["cell_size"]) for i in range(2)]
            if rng.random() < 0.85:
                query["start"] = [min(max(cell[i], 0), counts[i] - 1) for i in range(2)]
        if rng.random() < 0.3:
            query["metadata_filter"] = selector(rng)
        return query
    if kind == "crossed_zones":
        return {"kind": "crossed_zones", "map_id": map_id, "path": [point(rng, bounds) for _ in range(rng.randint(2, 5))],
                "max_crossings": rng.randint(1, 4)}
    if kind == "area":
        shape_kind = rng.choice(["sphere", "cone", "prism", "box", "cylinder"])
        if shape_kind == "sphere":
            shape = {"kind": "sphere", "center": point(rng, bounds), "radius": rng.choice([0.5, 1, 2.5, 6])}
        elif shape_kind == "cone":
            shape = {"kind": "cone", "apex": point(rng, bounds), "direction": [rng.choice([-1, 0, 1, 0.3]) for _ in range(2)] + [rng.choice([0, -0.2, 0.5])],
                     "length": rng.choice([2, 5, 9]), "radius": rng.choice([1, 2, 4])}
            if not any(shape["direction"]):
                shape["direction"][0] = 1
        elif shape_kind == "prism":
            shape = {"kind": "prism", "origin": point(rng, bounds), "direction": [rng.choice([-1, 0, 1, 2]) for _ in range(2)] + [rng.choice([0, 0.5])],
                     "up": rng.choice([[0, 0, 1], [0, 0.2, 1], [1, 0, 1]]), "length": rng.choice([2, 6]), "width": rng.choice([1, 3]),
                     "height": rng.choice([1, 4])}
            if not any(shape["direction"]):
                shape["direction"][0] = 1
        elif shape_kind == "box":
            a, b = point(rng, bounds), point(rng, bounds)
            shape = {"kind": "box", "min": [min(a[i], b[i]) for i in range(3)], "max": [max(a[i], b[i]) + 0.5 for i in range(3)]}
        else:
            shape = {"kind": "cylinder", "center": point(rng, bounds, floor), "radius": rng.choice([0.5, 2]), "height": rng.choice([1, 3])}
        return {"kind": "area", "map_id": map_id, "shape": shape,
                "include": rng.choice([["geometry"], ["entities"], ["zones"], ["geometry", "entities", "zones"]]),
                "max_results": rng.randint(1, 8)}
    moving = rng.choice(active)
    fixed = rng.choice([value for value in active if value != moving] or active)
    destination = point(rng, bounds, rng.choice([floor, floor, floor + 0.5]))
    mover_position = next(row for row in scene["entities"] if row["id"] == moving)["position"]
    destination = aligned(rng, mover_position, destination)
    if kind == "body_proximity":
        return {"kind": "body_proximity", "map_id": map_id, "moving_entity_id": moving, "fixed_entity_id": fixed,
                "destination": destination, "radius": rng.choice([0, 0.5, 1, 3])}
    if kind == "circle_overlap":
        query = {"kind": "circle_overlap", "map_id": map_id, "moving_entity_id": moving, "fixed_entity_id": fixed,
                 "destination": destination, "moving_radius": rng.choice([0.5, 1]), "fixed_radius": rng.choice([0.5, 1.5])}
        if rng.random() < 0.5:
            query["origin"] = point(rng, bounds, floor)
        return query
    if kind == "sweep":
        return {"kind": "sweep", "map_id": map_id, "moving_entity_id": moving, "destination": destination,
                "include_entities": rng.random() < 0.7, "exclude_entity_ids": rng.sample([v for v in active if v != moving], rng.randint(0, 1)),
                "metadata_filter": selector(rng)}
    others = [value for value in active if value != moving]
    circles = [{"entity_id": value, "radius": rng.choice([0.5, 1, 1.5]), "blocking": rng.random() < 0.7}
               for value in rng.sample(others, min(len(others), rng.randint(0, 6)))]
    query = {"kind": "circle_route", "map_id": map_id, "moving_entity_id": moving, "destination": destination,
             "moving_radius": rng.choice([0.5, 1]), "circles": circles, "metadata_filter": selector(rng),
             "max_tests": rng.choice([3, 40, 400])}
    if rng.random() < 0.3:
        origin = next(row for row in scene["entities"] if row["id"] == moving)["position"]
        query["edge_costs"] = [{"start": origin, "end": destination, "cost": 1000}]
    return query


def row_scene() -> dict:
    """Blocking circles almost exactly in line, so a facing support's angle wraps past 2*pi."""
    scene = pillars(0, random.Random(3))
    scene["map"]["id"] = "row"
    scene["geometry"] = []
    scene["entities"] = [entity("r.mover", [3, 10, 0], {"kind": "circle", "radius": 0.5}),
                         entity("r.a", [10, 10, 0], {"kind": "circle", "radius": 1}),
                         entity("r.b", [20, 10 - 1e-13, 0], {"kind": "circle", "radius": 1}),
                         entity("r.c", [15, 10 + 1e-13, 0], {"kind": "circle", "radius": 1})]
    for item in scene["entities"]:
        item["map_id"] = "row"
    return scene


def tie_scene() -> dict:
    """A wall and an entity sharing faces, so a ray meets both at the same parameter."""
    scene = copy.deepcopy(fixtures.QUERY_SCENE)
    scene["geometry"].append({"id": "z.wall", "map_id": "keep", "volume": {"kind": "box", "min": [5, 0, 0], "max": [6, 10, 4]},
                              "active": True, "movement": "blocking", "visibility": "opaque", "metadata": {},
                              "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}})
    scene["entities"].append({"id": "a.ent", "map_id": "keep", "position": [5.5, 5, 0], "footprint": {"kind": "box", "half_extents": [0.5, 0.5]},
                              "height": 2, "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque", "metadata": {}})
    return scene


def targeted_engine(scenes: list[dict]) -> list[tuple[int, dict]]:
    cases = []
    tie, row, yard = scenes.index(TIE), scenes.index(ROW), 3
    for y in (5, 5.25, 4.5, 5.5):
        cases.append((tie, {"kind": "ray", "map_id": "keep", "origin": [0, y, 1], "destination": [10, y, 1],
                            "include": ["geometry", "entities"], "max_intersections": 8}))
        cases.append((tie, {"kind": "exposure", "map_id": "keep", "observer": {"point": [0, y, 1]}, "target": {"entity_id": "elf"},
                            "samples": {"horizontal": 3, "vertical": 2}, "include": ["geometry", "entities"]}))
    # Observers inside opaque bodies.
    for observer in ([5.5, 5, 1], [5.2, 2, 3], [8, 16, 0.5]):
        cases.append((tie, {"kind": "exposure", "map_id": "keep", "observer": {"point": observer}, "target": {"entity_id": "orc"},
                            "samples": {"horizontal": 4, "vertical": 3}, "include": ["geometry", "entities"]}))
    # A box area at the corner of a circle's bounding box, and an upright prism near a circle.
    for minimum in ([5.9, 5.9, 0], [5.85, 5.85, 0], [5.8, 5.8, 0]):
        cases.append((0, {"kind": "area", "map_id": "keep", "shape": {"kind": "box", "min": minimum, "max": [7, 7, 1]},
                          "include": ["entities"], "max_results": 4}))
    for gap in (0, 5e-13, 2e-12, 1e-9, 1e-6, -1e-9):
        cases.append((0, {"kind": "area", "map_id": "keep", "shape": {"kind": "prism", "origin": [3, 5.5, 0], "direction": [1, 0, 0],
                                                                       "up": [0, 0, 1], "length": 2 - gap, "width": 1, "height": 1},
                          "include": ["entities"], "max_results": 4}))
    # A target inside the opaque body the observer also stands in: the segment ends inside it.
    cage = scenes.index(CAGE)
    for observer in ([11, 9, 1], [12.5, 10.5, 0.5], [9.5, 8.5, 2]):
        cases.append((cage, {"kind": "exposure", "map_id": "keep", "observer": {"point": observer}, "target": {"entity_id": "elf"},
                             "samples": {"horizontal": 3, "vertical": 3}, "include": ["geometry"]}))
    # A disk near an upright prism's corner, across the exact-contact and the GJK tolerances.
    corner = scenes.index(CORNER)
    for name in CORNER_DISKS:
        cases.append((corner, {"kind": "area", "map_id": "keep", "shape": {"kind": "prism", "origin": [3, 5.5, 0], "direction": [1, 0, 0],
                                                                            "up": [0, 0, 1], "length": 2, "width": 1, "height": 1},
                               "include": ["entities"], "max_results": 16}))
        break
    # A nearly vertical circle move: horizontal travel squared between 1e-24 and 1e-12.
    for step in (1e-13, 1e-9, 1e-7, 1e-6, 2e-6):
        cases.append((0, {"kind": "circle_overlap", "map_id": "keep", "moving_entity_id": "orc", "fixed_entity_id": "elf",
                          "destination": [5.5 + step, 5.5, 0], "origin": [12, 10.3, 0], "moving_radius": 0.5, "fixed_radius": 1}))
        cases.append((0, {"kind": "circle_overlap", "map_id": "keep", "moving_entity_id": "orc", "fixed_entity_id": "elf",
                          "destination": [12 + step, 10, 1], "origin": [12, 10, 0], "moving_radius": 0.5, "fixed_radius": 1}))
    # Circle routes: sixteen and seventeen blocking circles; an angle that wraps.
    yard_scene = scenes[yard]
    others = [row["id"] for row in yard_scene["entities"] if row["id"] != "p.mover"]
    for count in (15, 16, 17):
        cases.append((yard, {"kind": "circle_route", "map_id": "yard", "moving_entity_id": "p.mover", "destination": [27, 27, 0],
                             "moving_radius": 0.5, "circles": [{"entity_id": value, "radius": 1, "blocking": True} for value in others[:count]],
                             "metadata_filter": NO_FILTER, "max_tests": 400}))
    for radius in (0.5, 1, 1.5):
        cases.append((row, {"kind": "circle_route", "map_id": "row", "moving_entity_id": "r.mover", "destination": [27, 10, 0],
                            "moving_radius": radius, "circles": [{"entity_id": value, "radius": 1, "blocking": True} for value in ("r.a", "r.b", "r.c")],
                            "metadata_filter": NO_FILTER, "max_tests": 600}))
    return cases


def cage_scene() -> dict:
    """An opaque box around the elf, so an observer inside it samples the elf from within."""
    scene = copy.deepcopy(QUERY_FIXTURE)
    scene["geometry"].append({"id": "w.cage", "map_id": "keep", "volume": {"kind": "box", "min": [9, 8, 0], "max": [14, 12, 5]},
                              "active": True, "movement": "blocking", "visibility": "opaque", "metadata": {},
                              "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}})
    return scene


CORNER_DISKS = {}


def corner_scene() -> dict:
    """Disks at distance 0.5 + gap from the prism corner (5, 6), along the diagonal."""
    scene = copy.deepcopy(QUERY_FIXTURE)
    for index, gap in enumerate((-1e-13, 0, 5e-13, 2e-12, 1e-11, 2e-10, 1e-9, 1e-8)):
        offset = (0.5 + gap) / math.sqrt(2)
        name = f"c.disk{index}"
        CORNER_DISKS[name] = gap
        scene["entities"].append({"id": name, "map_id": "keep", "position": [5 + offset, 6 + offset, 0],
                                  "footprint": {"kind": "circle", "radius": 0.5}, "height": 1, "facing": [1, 0], "active": True,
                                  "movement": "blocking", "visibility": "clear", "metadata": {}})
    return scene


NO_FILTER = {"key": "", "value": "", "missing_matches": False}
QUERY_FIXTURE = fixtures.QUERY_SCENE
ROW = row_scene()
TIE = tie_scene()
CAGE = cage_scene()
CORNER = corner_scene()


def exhausted_paths(rng, scenes, cases):
    """Unreachable goals with `max_steps` exactly the region's size: the frontier empties as the
    budget runs out, which is not truncation."""
    added = 0
    while added < 40:
        index = rng.randrange(len(scenes))
        query = random_query(rng, scenes[index])
        if query["kind"] != "path":
            continue
        query["max_steps"] = 100000
        record = run(scenes[index], query)
        if record is None or "result" not in record or record["result"]["found"]:
            continue
        for steps in (record["result"]["expanded"], record["result"]["expanded"] - 1):
            if steps >= 1:
                exact = {**query, "max_steps": steps}
                again = run(scenes[index], exact)
                if again is not None:
                    cases.append({"scene": index, "query": exact, **again})
        added += 1


def main() -> int:
    global SNAPS
    rng = random.Random(4807)
    scenes = [fixtures.QUERY_SCENE, HALL, pillars(10, random.Random(1)), pillars(18, random.Random(2)), ROW, TIE, CAGE, CORNER]
    snaps = [boundary_values(scene) for scene in scenes]
    cases = []
    for query in fixtures.query_targeted():
        record = run(fixtures.QUERY_SCENE, query)
        if record is not None:
            cases.append({"scene": 0, "query": query, **record})
    for index, query in targeted_engine(scenes):
        record = run(scenes[index], query)
        if record is not None:
            cases.append({"scene": index, "query": query, **record})
    exhausted_paths(rng, scenes[:4], cases)
    while len(cases) < 9000:
        index = rng.randrange(len(scenes))
        SNAPS = snaps[index] if rng.random() < 0.6 else None
        query = random_query(rng, scenes[index])
        record = run(scenes[index], query)
        if record is not None:
            cases.append({"scene": index, "query": query, **record})
    SNAPS = None
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "platform": PLATFORM, "scenes": scenes, "cases": cases},
                                   ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    kinds = {}
    for case in cases:
        kinds[case["query"]["kind"]] = kinds.get(case["query"]["kind"], 0) + 1
    print(f"{len(cases)} engine cases ({PLATFORM}): {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
