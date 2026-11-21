"""Generate reference vectors for sylvia_domain::panel_layout from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_panel_layout_vectors.py

The repository's only shipped layouts belong to private packages, so every layout and surface
here is synthetic: a small surface list (including a surface with no renderer and a repeated
identity), a valid base layout, mutations of it stored as edits, raw byte variants, and layouts
at the depth, node and title limits.
For each case it records Python's outcome: the canonical JSON of the parsed layout, or the
exception class and message (plus diagnostics for `ContentShapeError`).
The output is deterministic (fixed seed) and checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.domain.content_validation import ContentShapeError  # noqa: E402
from sylvia_gm.domain.package_library import SurfaceDeclaration, canonical_json_bytes  # noqa: E402
from sylvia_gm.domain.panel_layout import parse_panel_layout  # noqa: E402

OUTPUT = Path(__file__).with_name("panel_layout_vectors.json")
rng = random.Random(20261121)

# (identity, renderer, allowed placements). "notes" has no renderer, so it is not a panel;
# "dup" appears twice and the later declaration wins.
SURFACES = {
    "small": [
        ["map", "coordinate_map", ["docked", "window"]],
        ["log", "table", ["docked"]],
        ["dice", "table", ["window"]],
        ["sheet", "table", ["docked", "window"]],
        ["notes", None, ["docked", "window"]],
        ["dup", "table", ["docked"]],
        ["dup", "table", ["window"]],
        ["zeta", "table", ["docked", "window"]],
    ],
    "wide": [[f"p{index:03d}", "table", ["docked", "window"]] for index in range(300)],
}

BASE = {
    "version": 1,
    "sidebar": {
        "kind": "split", "id": "side", "axis": "column", "ratio": 0.5,
        "first": {"kind": "tabs", "id": "top", "panels": ["map", "log"], "active": "log"},
        "second": {"kind": "tabs", "id": "bottom", "panels": ["sheet"], "active": "sheet"},
    },
    "windows": [
        {"id": "w1", "title": "Dice tray", "tree": {"kind": "tabs", "id": "wt", "panels": ["dice", "dup"],
                                                    "active": "dice"}},
    ],
    "hidden": ["zeta"],
}

JUNK = [None, True, 0, 1, 0.5, 0.05, 0.95, 2, "", "x", "map", "log", "dice", "sheet", "notes", "dup", "zeta",
        "unknown", "tabs", "split", "row", "column", "sidebar", "docked", "window", "a" * 97, "\u00e9", " a ",
        [], ["map"], ["map", "map"], ["dice"], ["notes"], {}, {"kind": "tabs"}]


def surfaces(name):
    result = []
    for identity, renderer, placements in SURFACES[name]:
        result.append(SurfaceDeclaration(identity, "cap", renderer=renderer, allowed_placements=tuple(placements)))
    return result


def outcome(data: bytes, surface_set: str):
    try:
        parsed = parse_panel_layout(data, surfaces(surface_set))
        return ["ok", canonical_json_bytes(parsed).decode("utf-8")]
    except ContentShapeError as error:
        return ["ContentShapeError", str(error), [item.as_payload() for item in error.diagnostics]]
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error)]


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
        if not path:
            document = copy.deepcopy(value)
            continue
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def mutations(document):
    edits = []
    for path, value in leaves(document):
        if isinstance(value, dict):
            for key in value:
                edits.append([["delete", [*path, key], None]])
        if path:
            for junk in rng.sample(JUNK, 8):
                edits.append([["set", list(path), junk]])
    for _ in range(60):
        group = rng.sample(edits, 2)
        edits.append([edit for single in group for edit in single])
    return edits


def chain(depth):
    """A sidebar split `depth` levels deep, ending in two tab groups."""
    node = {"kind": "tabs", "id": "leaf", "panels": ["p000"], "active": "p000"}
    for level in range(depth):
        node = {"kind": "split", "id": f"s{level}", "axis": "row", "ratio": 0.5, "first": node,
                "second": {"kind": "tabs", "id": f"t{level}", "panels": [f"p{level + 1:03d}"],
                           "active": f"p{level + 1:03d}"}}
    return node


def many_windows(count):
    return [{"id": f"w{index}", "title": "W", "tree": {"kind": "tabs", "id": f"n{index}", "panels": [f"p{index:03d}"],
                                                       "active": f"p{index:03d}"}} for index in range(count)]


def node_windows(single):
    """36 windows of seven-node trees, then `single` one-node windows: 252 + `single` tree nodes."""
    panels = iter(f"p{index:03d}" for index in range(300))
    names = iter(f"n{index}" for index in range(400))

    def tabs():
        panel = next(panels)
        return {"kind": "tabs", "id": next(names), "panels": [panel], "active": panel}

    def split(first, second):
        return {"kind": "split", "id": next(names), "axis": "row", "ratio": 0.5, "first": first, "second": second}

    windows = [{"id": f"w{index}", "title": "W", "tree": split(split(tabs(), tabs()), split(tabs(), tabs()))}
               for index in range(36)]
    windows += [{"id": f"x{index}", "title": "W", "tree": tabs()} for index in range(single)]
    return windows


def documents():
    """Whole documents beyond the base's mutations: the limits."""
    result = []
    for depth in (15, 16, 17, 19, 20):
        result.append(("wide", {"version": 1, "sidebar": chain(depth), "windows": [], "hidden": []}))
    for count in (63, 64, 65):
        result.append(("wide", {"version": 1, "sidebar": None, "windows": many_windows(count), "hidden": []}))
    for single in (4, 5):
        result.append(("wide", {"version": 1, "sidebar": None, "windows": node_windows(single), "hidden": []}))
    for title in ("a" * 256, "a" * 257, "\u00e9" * 128, "\u00e9" * 129, "\u20ac" * 85, "\u20ac" * 86, "a b", " a"):
        document = copy.deepcopy(BASE)
        document["windows"][0]["title"] = title
        result.append(("small", document))
    empty = {"version": 1, "sidebar": None, "windows": [], "hidden": []}
    result.append(("small", empty))
    result.append(("small", {**empty, "hidden": ["map", "log", "dice", "sheet", "dup", "zeta"]}))
    result.append(("small", {**empty, "hidden": ["notes"]}))
    result.append(("small", {**empty, "windows": [{"id": "w", "title": "T", "tree": None}]}))
    result.append(("small", {**empty, "windows": [{"id": "w", "title": "T", "tree": BASE["windows"][0]["tree"]},
                                                  {"id": "w", "title": "U", "tree": BASE["sidebar"]["second"]}]}))
    split_once = copy.deepcopy(BASE)
    split_once["sidebar"]["second"] = None
    result.append(("small", split_once))
    return result


def raw_variants():
    base = json.dumps(BASE)
    return [
        json.dumps(BASE, indent=1),
        base,
        base.replace('"version": 1', '"version": 1, "version": 1'),
        "{",
        "[]",
        "null",
        canonical_json_bytes(BASE).decode() + " ",
        canonical_json_bytes({**BASE, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}).decode(),
        json.dumps({**BASE, "hidden": ["zeta"] + [" " * (64 * 1024)]}, separators=(",", ":"), sort_keys=True),
        json.dumps([[[[[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]]]]),
        json.dumps({"version": 1, "sidebar": None, "windows": [], "hidden": [str(i) for i in range(4100)]},
                   separators=(",", ":"), sort_keys=True),
    ]


def main() -> None:
    cases = []
    base_bytes = canonical_json_bytes(BASE)
    cases.append({"surfaces": "small", "edits": [], "raw": None, "outcome": outcome(base_bytes, "small")})
    for edits in mutations(BASE):
        try:
            document = apply(BASE, edits)
        except (KeyError, IndexError, TypeError):
            continue
        cases.append({"surfaces": "small", "edits": edits, "raw": None,
                      "outcome": outcome(canonical_json_bytes(document), "small")})
    for surface_set, document in documents():
        cases.append({"surfaces": surface_set, "edits": [["set", [], document]], "raw": None,
                      "outcome": outcome(canonical_json_bytes(document), surface_set)})
    for raw in raw_variants():
        cases.append({"surfaces": "small", "edits": [], "raw": raw, "outcome": outcome(raw.encode("utf-8"), "small")})
    vectors = {"python": sys.version.split()[0], "surfaces": SURFACES, "base": BASE, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8",
                      newline="\n")
    kinds = {}
    for case in cases:
        key = case["outcome"][0] if case["outcome"][0] != "ValueError" else case["outcome"][1]
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases", kinds)


if __name__ == "__main__":
    main()
