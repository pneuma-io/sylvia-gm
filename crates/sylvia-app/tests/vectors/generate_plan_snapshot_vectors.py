"""Generate reference vectors for sylvia_app::plan_snapshot and its bundled Rift functions.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_plan_snapshot_vectors.py

Inputs:
- `validate_plan_snapshot` on the retained Rift snapshot with the shipped Rift Plan and its
  rendering, with mutations of the Plan (bindings, starting state, an authored scene) and of
  the rendering;
- snapshot bytes that are empty, over 64 MiB, truncated or with one byte changed, and the
  current bundled Rift archive (whose closure the Plan does not pin);
- `_admitted_snapshot_facts` of the retained and current Rift archives;
- `bundled_rift_archive_for_closure` for each kind of pin, and `bundled_rift_campaign_plan`;
- `retained_plan_snapshot` with a fake owner whose export writes `export:<candidate id>`.
Outcomes are "ok" with a result, or the exception class, message and diagnostics. Archive
results are their SHA-256.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from sylvia_gm.application import bundled_packages  # noqa: E402
from sylvia_gm.application.plan_snapshot import (  # noqa: E402
    PLAN_SNAPSHOT_BYTE_LIMIT,
    _admitted_snapshot_facts,
    retained_plan_snapshot,
    validate_plan_snapshot,
)
from tests.test_tactical_grid_ui import _scene  # noqa: E402

OUTPUT = Path(__file__).with_name("plan_snapshot_vectors.json")
RIFT = "campaign-plans/the-rift-awakening"
rng = random.Random(20261123)
JUNK = [None, True, 0, 1, -1, "", "x", "0" * 64, [], ["x"], {}, {"k": 1}]


def outcome(call, shape):
    try:
        result = call()
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics]]
    return ["ok", shape(result)]


def digest(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def validated_shape(plan):
    return {"content_sha256": plan.content_sha256, "artifact_id": plan.artifact_id, "title": plan.title,
            "markdown_sha256": digest(plan.readable_markdown.encode("utf-8")),
            "referenced_setting_paths": list(plan.referenced_setting_paths)}


def facts_shape(facts):
    primary, settings, closure = facts
    return {"primary": list(primary.identity), "closure_sha256": closure,
            "settings": [{"binding": [setting.package_id, setting.version, setting.sha256],
                          "paths": sorted(paths), "knowledge_ids": sorted(knowledge)}
                         for setting, paths, knowledge in settings]}


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


def mutations(document, count, prefix):
    paths = [(path, value) for path, value in leaves(document)
             if path[:len(prefix)] == prefix and len(path) > len(prefix)]
    found = []
    for _ in range(count):
        path, value = rng.choice(paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            edits = [["delete", [*path, rng.choice(list(value))], None]]
        else:
            edits = [["set", list(path), rng.choice(JUNK)]]
        try:
            apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue
        found.append(edits)
    return found


class Candidate:
    def __init__(self, candidate_id, closure_sha256):
        self.candidate_id, self.closure_sha256 = candidate_id, closure_sha256


class Owner:
    def __init__(self, candidates):
        self.candidates = [Candidate(*item) for item in candidates]

    def package_candidates(self):
        return tuple(self.candidates)

    def export_package_candidate(self, candidate_id, target):
        target.write(f"export:{candidate_id}".encode())
        return {}


def main() -> None:
    root = ROOT / RIFT
    retained = (root / "setting.sylvia-gm-package").read_bytes()
    current = bundled_packages.bundled_rift_archive()
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    markdown = (root / "plan.md").read_text(encoding="utf-8")
    pinned = plan["bindings"]["package_closure_sha256"]
    current_closure = bundled_packages.bundled_rift_authority().closure_sha256
    assert pinned != current_closure
    value = ["initial_state", "mechanics", 0, "value"]
    scene = _scene()
    bad_scene = copy.deepcopy(scene)
    bad_scene["map"]["grid"]["origin"] = [999, 999]
    validate = [[]]
    validate += mutations(plan, 50, ("bindings",))
    validate += mutations(plan, 50, ("initial_state",))
    validate += mutations(plan, 30, ())
    validate += [
        [["delete", ["bindings"], None]],
        [["set", ["bindings"], []]],
        [["set", ["bindings", "campaign_setting"], None]],
        [["set", ["bindings", "campaign_setting"], ["pneuma.severed-lands"]]],
        [["delete", ["bindings", "campaign_setting", "id"], None]],
        [["set", ["bindings", "campaign_setting", "id"], "sylvia.srd-lite.bg3"]],
        [["set", ["bindings", "campaign_setting", "id"], 5]],
        [["set", ["bindings", "package_closure_sha256"], current_closure]],
        [["set", ["bindings", "ruleset", "version"], "0.3.28"]],
        [["set", [], []]],
        [["set", [], "plan"]],
        [["set", [*value, "spatial_scene"], scene]],
        [["set", [*value, "spatial_scene"], bad_scene]],
        [["set", [*value, "spatial_scene"], None]],
    ]
    cases = []
    for edits in validate:
        document = apply(plan, edits)
        cases.append({"kind": "validate", "snapshot": {"kind": "retained"}, "edits": edits, "markdown": None,
                      "outcome": outcome(lambda: validate_plan_snapshot(retained, document, markdown),
                                         validated_shape)})
    for replacement in ["", "x", markdown + "\n", markdown.replace("Rift", "rift", 1)]:
        cases.append({"kind": "validate", "snapshot": {"kind": "retained"}, "edits": [], "markdown": replacement,
                      "outcome": outcome(lambda: validate_plan_snapshot(retained, plan, replacement),
                                         validated_shape)})
    size = len(retained)
    specs = [{"kind": "empty"}, {"kind": "oversize"}, {"kind": "current"}]
    specs += [{"kind": "truncate", "length": length} for length in (1, 22, 1000, size // 2, size - 22, size - 1)]
    specs += [{"kind": "flip", "offset": offset} for offset in (0, 4, 30, 200, size // 3, size - 60, size - 22,
                                                               size - 12, size - 2)]
    for spec in specs:
        snapshot = snapshot_bytes(spec, retained, current)
        cases.append({"kind": "validate", "snapshot": spec, "edits": [], "markdown": None,
                      "outcome": outcome(lambda: validate_plan_snapshot(snapshot, plan, markdown),
                                         validated_shape)})
    for spec in [{"kind": "retained"}, {"kind": "current"}]:
        snapshot = snapshot_bytes(spec, retained, current)
        cases.append({"kind": "facts", "snapshot": spec,
                      "outcome": outcome(lambda: _admitted_snapshot_facts.__wrapped__(snapshot), facts_shape)})
    for expected in [None, 5, [pinned], pinned, current_closure, "0" * 64, ""]:
        cases.append({"kind": "rift_closure", "expected": expected, "outcome": outcome(
            lambda: bundled_packages.bundled_rift_archive_for_closure(expected), digest)})
    cases.append({"kind": "rift_plan", "outcome": outcome(bundled_packages.bundled_rift_campaign_plan,
                                                          validated_shape)})
    owners = [[], [["c1", "0" * 64]], [["c1", "0" * 64], ["c2", pinned], ["c3", pinned]],
              [["c1", current_closure]], [["c1", "a" * 64]]]
    payloads = [plan, {}, [], "plan", {"bindings": []}, {"bindings": {}},
                {"bindings": {"package_closure_sha256": pinned}},
                {"bindings": {"package_closure_sha256": current_closure}},
                {"bindings": {"package_closure_sha256": "a" * 64}},
                {"bindings": {"package_closure_sha256": None}},
                {"bindings": {"package_closure_sha256": 5}}]
    for candidates in owners:
        for payload in payloads:
            cases.append({"kind": "retained", "candidates": candidates, "payload": payload, "outcome": outcome(
                lambda: retained_plan_snapshot(Owner(candidates), payload), digest)})
    vectors = {"python": sys.version.split()[0], "rift": RIFT, "limit": PLAN_SNAPSHOT_BYTE_LIMIT,
               "scene": scene, "cases": cases}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n"
    assert str(ROOT) not in text and str(ROOT).replace(chr(92), "/") not in text
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    kinds = {}
    for case in cases:
        result = case["outcome"]
        key = case["kind"] + ": " + ("ok" if result[0] == "ok" else result[0] + " " + result[1][:60])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


def snapshot_bytes(spec, retained, current):
    kind = spec["kind"]
    if kind == "retained":
        return retained
    if kind == "current":
        return current
    if kind == "empty":
        return b""
    if kind == "oversize":
        return bytes(PLAN_SNAPSHOT_BYTE_LIMIT + 1)
    if kind == "truncate":
        return retained[:spec["length"]]
    changed = bytearray(retained)
    changed[spec["offset"]] ^= 0xFF
    return bytes(changed)


if __name__ == "__main__":
    main()
