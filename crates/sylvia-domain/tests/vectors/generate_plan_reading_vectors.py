"""Generate reference vectors for sylvia_domain::campaign_ending and ::no_ruleset_plan.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_plan_reading_vectors.py

Inputs:
- every shipped Campaign Plan (`campaign-plans/*/plan.json`), which the Rust test reads from its
  path: its intended endings, each ending's progress identity and required items, its story
  items, `validate_ending`, and `check_ending_progress` for progress built from its own acts,
  objectives and endings;
- an ongoing variant and mutations of three of them (ending, structure, objectives, bindings,
  party and entities, stored as edits);
- No Ruleset Plans: three shipped Plans with their bindings, party and initial state replaced
  by a synthetic No Ruleset world, and mutations of each.
For each call it records Python's outcome: the result as `json.dumps` writes it (or that text's
SHA-256 when it is longer than 1,000 characters), or the exception class and message.
The output is deterministic (fixed seed) and checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import glob
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.domain import campaign_ending, no_ruleset_plan  # noqa: E402

OUTPUT = Path(__file__).with_name("plan_reading_vectors.json")
MUTATED = ["campaign-plans/riichi-quick-test/plan.json", "campaign-plans/dead-inventory/plan.json",
           "campaign-plans/the-quarry-turns/plan.json"]
PLAN_FIELDS = [("ending",), ("structure",), ("objectives",), ("bindings",), ("starting_party",),
               ("initial_state", "entities")]
rng = random.Random(20261123)

JUNK = [None, True, False, 0, 1, 2.5, "", " ", "x", "finite", "ongoing", "resolved", "ending:x", "act-1",
        "entities/x", "gm_private", "player_known", [], [1], ["x"], ["a", "a"], [[1]], {}, {"id": "x"}, {"k": 1},
        "\u00e9"]


def outcome(call, project=lambda value: value):
    try:
        result = call()
    except Exception as error:  # every class is behaviour: ValueError, TypeError, KeyError...
        return [type(error).__name__, str(error)]
    text = json.dumps(project(result))
    # Long results are kept as the SHA-256 of their text.
    return ["ok", text] if len(text) <= 1000 else ["ok-sha256", hashlib.sha256(text.encode("utf-8")).hexdigest()]


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


def mutations(document, count, prefixes):
    paths = [(path, value) for path, value in leaves(document)
             if path and any(path[:len(prefix)] == prefix for prefix in prefixes)]
    edits = []
    for _ in range(count):
        path, value = rng.choice(paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            edits.append([["delete", [*path, rng.choice(list(value))], None]])
        elif isinstance(value, list) and value and rng.random() < 0.3:
            edits.append([["set", [*path, 0], copy.deepcopy(value[-1])]])
        else:
            edits.append([["set", list(path), rng.choice(JUNK)]])
    for _ in range(count // 5):
        group = rng.sample(edits, 2)
        edits.append([edit for single in group for edit in single])
    return edits


def variants(document, count, prefixes):
    for edits in mutations(document, count, prefixes):
        try:
            yield edits, apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue


def ending_items(plan):
    """Each intended ending's progress identity and required items."""
    return [[campaign_ending.ending_progress_id(ending), sorted(campaign_ending.required_ending_items(plan, ending))]
            for ending in campaign_ending.intended_endings(plan)]


def progress_sets(plan):
    """(progress, updates) pairs for check_ending_progress, from the Plan's own identities."""
    acts = [item.get("id") for item in plan.get("structure", ()) if isinstance(item, dict)]
    objectives = [item.get("id") for item in plan.get("objectives", ()) if isinstance(item, dict)]
    endings = [f"ending:{item.get('id')}" for item in (plan.get("ending") or {}).get("outcomes", ())
               if isinstance(item, dict)]
    resolve = lambda ids: [{"plan_item_id": i, "status": "resolved"} for i in ids]  # noqa: E731
    first = endings[:1]
    return [
        ([], []),
        ([], resolve(first)),
        (resolve(acts + objectives), resolve(first)),
        (resolve(acts), resolve(objectives + first)),
        (resolve(acts + objectives), resolve(endings + ["ending:extra"])),
        (resolve(acts + objectives), resolve(endings[:2])),
        (resolve(acts + objectives + first), [{"plan_item_id": first[0] if first else "x", "status": "active"}]),
        ([], [{"plan_item_id": "ending:undeclared", "status": "active"}]),
        ([], resolve(acts[-1:])),
        ([], [{"plan_item_id": ["x"], "status": "resolved"}]),
        ([], [{"status": "resolved"}]),
        ([], ["x"]),
        ([{"status": "resolved"}], resolve(first)),
        ([], [{"plan_item_id": 1, "status": "resolved"}]),
    ]


def plan_calls(args, plan, progress):
    cases = [
        {"fn": "intended_endings", "args": args, "outcome": outcome(lambda: campaign_ending.intended_endings(plan), list)},
        {"fn": "ending_items", "args": args, "outcome": outcome(lambda: ending_items(plan))},
        {"fn": "required_story_items", "args": args,
         "outcome": outcome(lambda: campaign_ending.required_story_items(plan), sorted)},
        {"fn": "validate_ending", "args": args, "outcome": outcome(lambda: campaign_ending.validate_ending(plan))},
    ]
    for done, updates in progress:
        cases.append({"fn": "check_ending_progress",
                      "args": args + [{"inline": done}, {"inline": updates}],
                      "outcome": outcome(lambda: campaign_ending.check_ending_progress(plan, done, updates))})
    return cases


def ending_cases(paths):
    cases = []
    for path in paths:
        plan = json.loads((ROOT / path).read_text(encoding="utf-8"))
        sets = progress_sets(plan)
        cases += plan_calls([{"plan": path, "edits": []}], plan, sets)
        for done, updates in sets:
            cases.append({"fn": "resolved_progress", "args": [{"inline": done + updates}],
                          "outcome": outcome(lambda: campaign_ending.resolved_progress(done + updates), sorted)})
        if path not in MUTATED:
            continue
        last = len(plan["structure"]) - 1
        ongoing_edits = [["set", ["ending"], {"mode": "ongoing"}], ["set", ["structure", last, "objectives"], []]]
        cases += plan_calls([{"plan": path, "edits": ongoing_edits}], apply(plan, ongoing_edits), sets)
        open_edits = [["set", ["ending", "outcomes", 0, "requires_all_acts"], False]]
        cases += plan_calls([{"plan": path, "edits": open_edits}], apply(plan, open_edits), sets)
        for edits, document in variants(plan, 220, PLAN_FIELDS):
            cases += plan_calls([{"plan": path, "edits": edits}], document, rng.sample(sets, 2))
    return cases


NO_RULESET_FIELDS = {
    "bindings": {"ruleset": None, "campaign_setting": None, "package_closure_sha256": "0" * 64},
    "starting_party": {"player_character": {"mode": "create"}, "required_companions": []},
    "initial_state": {"facts": [], "entities": [
        {"id": "lantern-table", "kind": "place", "source_ref": {"id": "lantern-table", "path": "entities/lantern-table"},
         "state": {"name": "The Lantern Table", "visibility": "player_known", "description": "A quiet corner."}},
        {"id": "old-key", "kind": "item", "source_ref": {"id": "old-key", "path": "entities/old-key"},
         "state": {"name": "Old key", "known_by": ["hero", "keeper"], "purpose": "Opens the back room.", "weight": 1}},
        {"id": "keeper", "kind": "person", "source_ref": {"id": "keeper", "path": "entities/keeper"},
         "state": {"name": "Keeper", "visibility": "gm_private", "known_by": []}},
    ]},
}


def no_ruleset_cases(paths):
    cases = []
    project_records = lambda records: [[identity, payload] for identity, payload in records]  # noqa: E731
    prefixes = [("bindings",), ("starting_party",), ("initial_state",)]
    for path in paths:
        plan = json.loads((ROOT / path).read_text(encoding="utf-8"))
        cases.append({"fn": "is_no_ruleset_plan", "args": [{"plan": path, "edits": []}],
                      "outcome": outcome(lambda: no_ruleset_plan.is_no_ruleset_plan(plan))})
        base = {**plan, **copy.deepcopy(NO_RULESET_FIELDS)}
        name = ["initial_state", "entities", 0, "state", "name"]
        names = ["a" * 300, "a" * 301, "\u00e9" * 300, "\u00e9" * 301, " Keeper", "Keeper ", "\u00a0Keeper", "Keeper \u00a0", "\u00a0"]
        boundaries = [[["set", name, value]] for value in names]
        boundaries += [[["set", ["initial_state", "entities", 1, "state", "known_by"], ["hero", " x"]]],
                       [["delete", ["initial_state", "entities", 0, "state", "visibility"], None]],
                       [["set", ["initial_state", "entities", 0, "id"], "entities/x"]],
                       [["set", ["initial_state", "entities", 0, "source_ref", "path"], "entities/keeper"]]]
        explicit = [(edits, apply(base, edits)) for edits in boundaries]
        for edits, document in [([], base), *explicit, *variants(base, 250, prefixes)]:
            args = [{"no_ruleset": path, "edits": edits}]
            cases.append({"fn": "is_no_ruleset_plan", "args": args,
                          "outcome": outcome(lambda: no_ruleset_plan.is_no_ruleset_plan(document))})
            cases.append({"fn": "validate_no_ruleset_world", "args": args,
                          "outcome": outcome(lambda: no_ruleset_plan.validate_no_ruleset_world(document), sorted)})
            cases.append({"fn": "no_ruleset_world_records", "args": args,
                          "outcome": outcome(lambda: no_ruleset_plan.no_ruleset_world_records(document),
                                             project_records)})
    return cases


def main() -> None:
    paths = sorted(str(Path(p).relative_to(ROOT)).replace("\\", "/")
                   for p in glob.glob(str(ROOT / "campaign-plans/*/plan.json")))
    cases = ending_cases(paths) + no_ruleset_cases(MUTATED)
    vectors = {"python": sys.version.split()[0], "plans": paths, "no_ruleset_fields": NO_RULESET_FIELDS,
               "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8",
                      newline="\n")
    kinds = {}
    for case in cases:
        key = (case["fn"], case["outcome"][0])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(paths), "plans,", len(cases), "cases")
    for key in sorted(kinds):
        print(" ", key, kinds[key])


if __name__ == "__main__":
    main()
