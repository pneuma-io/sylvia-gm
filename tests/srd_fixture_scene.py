"""Explicit physical initial facts for synthetic Lite mechanics fixtures."""

from copy import deepcopy


def fixture_scene(entities, positions=None):
    positions = positions or {}
    default = {"hero": [0, 0, 0], "guard": [5, 0, 0], "other": [5, 4.5, 0]}
    bodies = []
    for index, (identity, entity) in enumerate(entities.items()):
        point = positions.get(identity, default.get(identity, [5, 4.5 * (index + 1), 0]))
        bodies.append({"id": identity, "map_id": "fixture", "position": deepcopy(point),
            "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 6,
            "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque",
            "metadata": {"participant_ref": identity, "plane": "material", "lighting": "bright",
                         "creature_type": entity.get("creature_type", "humanoid")}})
    return {"format": 1, "map": {"id": "fixture", "revision": 0,
        "bounds": {"min": [-2000, -2000, -100], "max": [2000, 2000, 100]},
        "grid": {"cell_size": 5, "unit": "ft", "origin": [-2000, -2000]},
        "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}]},
        "entities": bodies, "geometry": [], "zones": [], "terrain": []}
