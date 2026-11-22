"""Generate `resolve_surface_source` vectors for sylvia-app::tactical_grid from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_surface_source_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a surface source, a state and participants. A participant is an object with
whichever of `kind`, `participant_id` and `data` the case gives (a missing one reads as `None`
through `getattr`); kinds and identities are text or absent, as real participants' are. The
record is the resolved value or the exception's class and message.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

from sylvia_gm.application.tactical_grid import resolve_surface_source

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "surface_source_vectors.json"

STATE = {
    "pkg.map": {"scene": {"format": 1, "id": "keep"}, "grid": [1, 2], "deep": {"a": {"b": None, "c": [1]}}},
    "pkg.map.scene": "exact wins",
    "pkg.flag": False,
    "pkg.none": None,
    "pkg.": {"": {"": "empty segments"}},
    "pkg.list": [{"a": 1}],
    "other.map": {"scene": 1},
}
PARTICIPANTS = [
    {"kind": "pc", "participant_id": "p1", "data": {"hp": 3, "tags": ["a"]}},
    {"kind": "npc", "participant_id": "n1", "data": {}},
    {"kind": "pc", "participant_id": "p2", "data": {"nested": {"x": 1.5}}},
    {"participant_id": "x1", "data": {"a": 1}},
    {"kind": "pc", "data": {"no": "id"}},
    {"kind": "pc", "participant_id": "p3"},
]


def participant(entry: dict) -> SimpleNamespace:
    return SimpleNamespace(**entry)


def run(source: object, participants: list) -> dict:
    try:
        value = resolve_surface_source(source, state=STATE, participants=[participant(p) for p in participants])
    except Exception as error:  # noqa: BLE001 - every exception Python lets through
        return {"error": [type(error).__name__, str(error)]}
    return {"result": value}


def state(namespace, key):
    return {"kind": "state", "namespace": namespace, "key": key}


TARGETED = [
    None, 1, "state", [], {}, {"kind": None}, {"kind": "unknown"}, {"kind": ["state"]},
    {"kind": "literal", "value": {"a": [1, None]}}, {"kind": "literal", "value": {}}, {"kind": "literal"},
    {"kind": "literal", "value": None}, {"kind": "literal", "value": [1]}, {"kind": "literal", "value": "x"},
    state("pkg", "map"), state("pkg", "map.scene"), state("pkg", "map.scene.id"), state("pkg", "map.grid"),
    state("pkg", "map.grid.0"), state("pkg", "map.deep.a.b"), state("pkg", "map.deep.a.c"), state("pkg", "map.deep.a.b.x"),
    state("pkg", "map.missing"), state("pkg", "missing"), state("pkg", "missing.x"), state("pkg", "flag"), state("pkg", "none"),
    state("pkg", "none.x"), state("pkg", ""), state("pkg", "."), state("pkg", ".."), state("pkg", "list.0"), state("other", "map.scene"),
    state("", "pkg.map"), state(1, "map"), state("pkg", 1), {"kind": "state", "namespace": "pkg"}, {"kind": "state", "key": "map"},
    {"kind": "participants", "kinds": ["pc"]}, {"kind": "participants", "kinds": ["pc", "npc", 3, None]},
    {"kind": "participants", "kinds": []}, {"kind": "participants"}, {"kind": "participants", "kinds": "pc"},
    {"kind": "participants", "kinds": {"pc": 1}},
    {"kind": "keyed_values", "entries": [{"key": {"kind": "literal", "value": {}}, "value": state("pkg", "map")}]},
    {"kind": "keyed_values", "entries": [{"key": state("pkg", "map.scene"), "value": state("pkg", "flag")},
                                          {"key": state("pkg", "map.scene"), "value": state("pkg", "none")},
                                          {"key": state("pkg", "map.scene"), "value": state("pkg", "map.grid")}]},
    {"kind": "keyed_values", "entries": [{"key": state("pkg", "map.deep.a.b"), "value": state("pkg", "map")}, "x", None,
                                          {"value": state("pkg", "map")}, {"key": state("pkg", "map.scene")}]},
    {"kind": "keyed_values", "entries": "x"}, {"kind": "keyed_values"},
    {"kind": "keyed_values", "entries": [7, {"key": state("pkg", "map.scene"), "value": state("pkg", "map.grid")}]},
    {"kind": "keyed_values", "entries": [{"key": {"kind": "object", "fields": {}}, "value": state("pkg", "map")}]},
    {"kind": "object", "fields": {"scene": state("pkg", "map.scene"), "none": None, "lit": {"kind": "literal", "value": {"z": 1}},
                                  "nested": {"kind": "object", "fields": {"p": {"kind": "participants", "kinds": ["npc"]}}}}},
    {"kind": "object", "fields": []}, {"kind": "object"}, {"kind": "object", "fields": {}},
    {"kind": "object", "fields": {"bad": {"kind": "literal", "value": 3}}},
    {"kind": "keyed_values", "entries": [{"key": state("pkg", "map.scene"), "value": {"kind": "literal", "value": [1]}}]},
]
# Text keys after stripping Python's whitespace.
for text in (" ", "\t\n", "　", " ", "\x1c", "​", " a "):
    TARGETED.append({"kind": "keyed_values", "entries": [{"key": {"kind": "object", "fields": {}}, "value": None},
                                                          {"key": state("pkg", "map.scene"), "value": state("pkg", "map.grid")}]})
    STATE[f"pkg.text{len(TARGETED)}"] = text
    TARGETED.append({"kind": "keyed_values", "entries": [{"key": state("pkg", f"text{len(TARGETED)}"), "value": state("pkg", "flag")}]})


def random_source(rng: random.Random, depth: int = 0) -> object:
    kind = rng.choice(["state", "state", "literal", "participants", "keyed_values", "object", "junk"] if depth < 3 else
                      ["state", "literal", "junk"])
    if kind == "state":
        key = ".".join(rng.choice(["map", "scene", "grid", "deep", "a", "b", "c", "0", "", "list", "flag", "none", "x"])
                       for _ in range(rng.randint(1, 4)))
        return state(rng.choice(["pkg", "other", "", "pkg.map"]), key)
    if kind == "literal":
        return {"kind": "literal", "value": rng.choice([{}, {"a": 1}, {"b": [None]}, None, [1], "x"])}
    if kind == "participants":
        return {"kind": "participants", "kinds": rng.sample(["pc", "npc", "x", 1], rng.randint(0, 3))}
    if kind == "keyed_values":
        return {"kind": "keyed_values", "entries": [
            {"key": random_source(rng, depth + 1), "value": random_source(rng, depth + 1)} for _ in range(rng.randint(0, 3))]}
    if kind == "object":
        return {"kind": "object", "fields": {f"f{index}": random_source(rng, depth + 1) for index in range(rng.randint(0, 3))}}
    return rng.choice([None, 1, "x", [], {"kind": "nope"}])


def main() -> int:
    rng = random.Random(4802)
    cases = []
    for source in TARGETED:
        for participants in (PARTICIPANTS, []):
            cases.append({"source": source, "participants": participants, **run(source, participants)})
    for _ in range(1500):
        source = random_source(rng)
        participants = rng.sample(PARTICIPANTS, rng.randint(0, len(PARTICIPANTS)))
        cases.append({"source": source, "participants": participants, **run(source, participants)})
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "state": STATE, "cases": cases}, ensure_ascii=True,
                                   allow_nan=False) + "\n").encode("ascii"))
    print(f"{len(cases)} cases, {sum('error' in case for case in cases)} raise")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
