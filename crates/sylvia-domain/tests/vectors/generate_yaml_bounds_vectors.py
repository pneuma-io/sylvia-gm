"""Generate reference vectors for sylvia_domain::yaml_bounds from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_yaml_bounds_vectors.py

Each YAML document is composed with PyYAML's pure and C safe loaders and checked by
`validate_yaml_graph` under five limit sets: the two callers' own limits and three small ones.
Documents: hand-written shapes (aliases, recursive aliases, merges, non-text keys, a shared node
at different depths, wide and deep collections, "billion laughs") and random nested documents
with anchors. A subset is also loaded through a loader whose `construct_document` runs the check,
as `_BoundedRuntimeLoader` does, to show the hook's outcome.
For each case it records Python's outcome: "ok", the exception class and message, or the class of
a composer error. The output is deterministic (fixed seed) and checked in.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from sylvia_gm.application._yaml_bounds import validate_yaml_graph  # noqa: E402

OUTPUT = Path(__file__).with_name("yaml_bounds_vectors.json")
rng = random.Random(20261125)

LIMITS = {
    "runtime": {"maximum_depth": 64, "maximum_nodes": 501_000, "maximum_object_members": 25_000,
                "count_mapping_keys": False},
    "source": {"maximum_depth": 64, "maximum_nodes": 250_000, "maximum_object_members": None,
               "count_mapping_keys": True},
    "small": {"maximum_depth": 4, "maximum_nodes": 20, "maximum_object_members": 3, "count_mapping_keys": True},
    "small-values": {"maximum_depth": 4, "maximum_nodes": 20, "maximum_object_members": 3,
                     "count_mapping_keys": False},
    "tight": {"maximum_depth": 3, "maximum_nodes": 8, "maximum_object_members": None, "count_mapping_keys": False},
}
LOADERS = {"pure": yaml.SafeLoader, "c": yaml.CSafeLoader}


def nested(levels, inner="1"):
    return "[" * levels + inner + "]" * levels


def laughs(levels, width):
    lines = ["a0: &a0 [x]"]
    for level in range(1, levels + 1):
        lines.append(f"a{level}: &a{level} [" + ", ".join([f"*a{level - 1}"] * width) + "]")
    return "\n".join(lines) + "\n"


HANDWRITTEN = [
    "x\n", "1\n", "a: 1\n", "[1, 2, 3]\n", "{a: 1, b: 2, c: 3}\n", "{a: 1, b: 2, c: 3, d: 4}\n",
    nested(2), nested(3), nested(4), nested(5), nested(63), nested(64), nested(65),
    "a: &x [1, 2]\nb: *x\nc: *x\n",
    "a: &x [[[1]]]\nb: [[[*x]]]\n",
    "a: &x [[1]]\nb: [*x]\nc: [[*x]]\n",
    "&a [*a]\n", "&a {x: *a}\n", "&a [1, [2, *a]]\n",
    "base: &b {x: 1, y: 2}\nchild:\n  <<: *b\n  z: 3\n",
    "base: &b {x: 1}\nchild: {<<: [*b, *b], z: 3}\n",
    "1: a\n", "null: x\n", "? [1]\n: x\n", "? {a: 1}\n: x\n", "!!str 1: x\n", "=: x\n", "true: y\n",
    "[" + ", ".join(["x"] * 25) + "]\n",
    "{" + ", ".join(f"k{i}: {i}" for i in range(25)) + "}\n",
    laughs(3, 3), laughs(4, 4), laughs(6, 9), laughs(9, 9),
    "a: [&x 1, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x, *x]\n",
    "- &s {a: [1, 2]}\n- *s\n- [*s, *s]\n",
    "a: !!set {x, y}\nb: !!omap [{p: 1}, {q: 2}]\n",
    "{a: [b, {c: [d, {e: [f]}]}]}\n",
    "[a, [b, [c, [d]]]]\n",
    "k: &v [1]\n" + "".join(f"k{i}: *v\n" for i in range(12)),
    "- [&a [1], *a]\n- [*a, [*a]]\n",
]


def random_value(depth):
    roll = rng.random()
    if depth >= 6 or roll < 0.3:
        return rng.choice([1, "x", True, None, 2.5, "long text"])
    if roll < 0.65:
        return [random_value(depth + 1) for _ in range(rng.randint(0, 5))]
    return {f"k{index}": random_value(depth + 1) for index in range(rng.randint(0, 5))}


def random_document():
    text = yaml.safe_dump(random_value(0), sort_keys=False)
    if rng.random() < 0.5 and "\n- " in text:
        # Anchor one block sequence item and alias it later.
        lines = text.split("\n")
        anchors = [i for i, line in enumerate(lines) if line.startswith("- ") and not line.startswith("- -")]
        if len(anchors) > 1:
            first, later = anchors[0], anchors[-1]
            lines[first] = lines[first].replace("- ", "- &r ", 1)
            indent = lines[later].index("- ")
            lines[later] = " " * indent + "- *r"
            text = "\n".join(lines)
    return text


def check(text, loader, limits):
    try:
        node = yaml.compose(text, Loader=loader)
    except yaml.YAMLError as error:
        return ["compose", type(error).__name__]
    if node is None:
        return ["empty"]
    try:
        validate_yaml_graph(node, **limits)
    except Exception as error:
        return [type(error).__name__, str(error)]
    return ["ok"]


class BoundedLoader(yaml.CSafeLoader):
    def construct_document(self, node):
        validate_yaml_graph(node, **LIMITS["small-values"])
        return super().construct_document(node)


def load(text):
    try:
        value = yaml.load(text, Loader=BoundedLoader)
    except Exception as error:
        return [type(error).__name__, str(error) if not isinstance(error, yaml.YAMLError) else None]
    try:
        return ["ok", json.dumps(value)]
    except (TypeError, ValueError):
        return None


def main() -> None:
    documents = HANDWRITTEN + [random_document() for _ in range(150)]
    cases = []
    for text in documents:
        outcomes = {f"{loader}/{name}": check(text, LOADERS[loader], limits)
                    for loader in LOADERS for name, limits in LIMITS.items()}
        case = {"text": text, "outcomes": outcomes}
        loaded = load(text)
        if loaded is not None:
            case["load"] = loaded
        cases.append(case)
    vectors = {"python": sys.version.split()[0], "pyyaml": yaml.__version__, "limits": LIMITS, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8",
                      newline="\n")
    kinds = {}
    for case in cases:
        for outcome in case["outcomes"].values():
            key = outcome[1] if len(outcome) > 1 else outcome[0]
            kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "documents")
    for key in sorted(kinds, key=str):
        print(" ", key, kinds[key])


if __name__ == "__main__":
    main()
