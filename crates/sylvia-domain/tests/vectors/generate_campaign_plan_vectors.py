"""Generate reference vectors for sylvia_domain::campaign_plan from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_campaign_plan_vectors.py

Inputs, read by the Rust test from their repository paths:
- every shipped Campaign Plan (`campaign-plans/*/plan.json` with its `plan.md`), validated
  against its own bindings and closure, with the Setting paths and knowledge identities it
  references as the admitted Setting;
- the same Plans against changed expectations (another binding, closure, a missing path or
  knowledge identity) and changed readable renderings;
- No Ruleset variants of three Plans (bindings null, a small synthetic world);
- mutations of three Plans across every field (stored as edits), and of their starting party
  through `parse_starting_party_policy`.
For each call it records Python's outcome: the result's projection as `json.dumps` writes it
(or that text's SHA-256 when it is longer than 1,000 characters), or the exception class,
message and, for the field and reference errors, their diagnostics.
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

from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.campaign_plan import (  # noqa: E402
    PackageBinding,
    parse_starting_party_policy,
    validate_campaign_plan,
)

OUTPUT = Path(__file__).with_name("campaign_plan_vectors.json")
MUTATED = ["campaign-plans/riichi-quick-test/plan.json", "campaign-plans/dead-inventory/plan.json",
           "campaign-plans/the-quarry-turns/plan.json"]
rng = random.Random(20261124)

JUNK = [None, True, False, 0, 1, -1, 2.0, 2.5, "", " ", "x", "x ", "a/b", "../up", "C:/abs", "/abs", "\\\\srv\\x",
        "a" * 161, "ongoing", "finite", "compatible", "fixed", "sylvia-gm-campaign-plan", [], [1], ["x"], ["x", "x"],
        [[1]], {}, {"id": "x"}, {"id": "x", "path": "world.md"}, {"k": 1}, "\u00e9", "a\x00b"]
EXTRA_KEYS = ["extra", "API_KEY", "Password", "bad key!", "x" * 70]


def outcome(call, project):
    try:
        result = call()
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None)
        if diagnostics is not None:
            return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics]]
        return [type(error).__name__, str(error)]
    text = json.dumps(project(result))
    return ["ok", text] if len(text) <= 1000 else ["ok-sha256", hashlib.sha256(text.encode("utf-8")).hexdigest()]


def project_plan(validated):
    return {
        "content_sha256": validated.content_sha256,
        "artifact_id": validated.artifact_id,
        "title": validated.title,
        "markdown_sha256": hashlib.sha256(validated.readable_markdown.encode("utf-8")).hexdigest(),
        "paths": list(validated.referenced_setting_paths),
        "payload_sha256": hashlib.sha256(json.dumps(thaw_json_value(validated.payload)).encode("utf-8")).hexdigest(),
    }


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


def expand(value):
    """`{"$repeat": [text, count]}` stands for `text * count`, to keep long values out of the file."""
    if isinstance(value, dict) and set(value) == {"$repeat"}:
        text, count = value["$repeat"]
        return text * count
    return value


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        value = expand(value)
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def mutations(document, count, prefix=()):
    paths = [(path, value) for path, value in leaves(document) if path and path[:len(prefix)] == prefix]
    edits = []
    for _ in range(count):
        path, value = rng.choice(paths)
        choice = rng.random()
        if isinstance(value, dict) and value and choice < 0.25:
            edits.append([["delete", [*path, rng.choice(list(value))], None]])
        elif isinstance(value, dict) and choice < 0.35:
            edits.append([["set", [*path, rng.choice(EXTRA_KEYS)], rng.choice(JUNK)]])
        elif isinstance(value, list) and value and choice < 0.45:
            edits.append([["set", [*path, 0], copy.deepcopy(value[-1])]])
        elif isinstance(value, list) and len(value) > 1 and choice < 0.5:
            edits.append([["delete", [*path, len(value) - 1], None]])
        else:
            edits.append([["set", list(path), rng.choice(JUNK)]])
    for _ in range(count // 5):
        group = rng.sample(edits, 2)
        edits.append([edit for single in group for edit in single])
    return edits


def variants(document, count, prefix=()):
    for edits in mutations(document, count, prefix):
        try:
            yield edits, apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue


def referenced_paths(plan):
    """Every Setting path the Plan names: the admitted Setting for its own validation."""
    paths = set()
    party = plan.get("starting_party", {})
    if isinstance(party.get("player_character", {}).get("character"), dict):
        paths.add(party["player_character"]["character"]["path"])
    paths.update(item["path"] for item in party.get("required_companions", []))
    paths.add(plan["starting_location"]["path"])
    paths.update(item["source_ref"]["path"] for item in plan["initial_state"]["entities"])
    for items in plan["setting_references"].values():
        paths.update(item["path"] for item in items)
    for act in plan["structure"]:
        paths.update(act["setting_refs"])
    return sorted(paths)


def knowledge_ids(plan):
    return sorted(item["setting_fact_id"] for item in plan["secrets"] if isinstance(item, dict) and "setting_fact_id" in item)


def binding(value):
    return None if value is None else PackageBinding(value["id"], value["version"], value["content_sha256"])


def expectation(plan):
    bindings = plan["bindings"]
    return {"ruleset": bindings["ruleset"], "setting": bindings["campaign_setting"],
            "closure": bindings["package_closure_sha256"], "paths": referenced_paths(plan),
            "knowledge": knowledge_ids(plan)}


def call(document, markdown, expect):
    return lambda: validate_campaign_plan(
        document, readable_markdown=markdown, expected_ruleset=binding(expect["ruleset"]),
        expected_setting=binding(expect["setting"]), expected_closure_sha256=expect["closure"],
        setting_paths=frozenset(expect["paths"]), setting_knowledge_ids=frozenset(expect["knowledge"]))


NO_RULESET = {
    "bindings": {"ruleset": None, "campaign_setting": None, "package_closure_sha256": "0" * 64},
    "starting_party": {"player_character": {"mode": "compatible"}, "required_companions": [],
                       "custom_companions": {"minimum": 0, "maximum": 2}},
    "starting_location": {"id": "lantern-table", "path": "entities/lantern-table"},
    "setting_references": {"creatures": [], "factions": [], "items": [{"id": "old-key", "path": "entities/old-key"}],
                           "locations": [{"id": "lantern-table", "path": "entities/lantern-table"}], "lore": [],
                           "npcs": [{"id": "keeper", "path": "entities/keeper"}]},
    "initial_state": {"facts": [], "entities": [
        {"id": "lantern-table", "kind": "place", "source_ref": {"id": "lantern-table", "path": "entities/lantern-table"},
         "state": {"name": "The Lantern Table", "visibility": "player_known", "description": "A quiet corner."}},
        {"id": "old-key", "kind": "item", "source_ref": {"id": "old-key", "path": "entities/old-key"},
         "state": {"name": "Old key", "known_by": ["keeper"], "purpose": "Opens the back room."}},
        {"id": "keeper", "kind": "person", "source_ref": {"id": "keeper", "path": "entities/keeper"},
         "state": {"name": "Keeper", "visibility": "gm_private", "known_by": []}},
    ]},
}


def no_ruleset_plan(plan):
    document = {**plan, **copy.deepcopy(NO_RULESET)}
    document["structure"] = [{**act, "setting_refs": []} for act in document["structure"]]
    document["secrets"] = [item for item in document["secrets"] if not (isinstance(item, dict) and "setting_fact_id" in item)]
    return document


NO_RULESET_EXPECT = {"ruleset": None, "setting": None, "closure": "0" * 64, "paths": [], "knowledge": []}


def targeted(plan, expect):
    """(edits, expectation) pairs for the paths the shipped Plans leave unused."""
    ruleset = expect["ruleset"]["id"] if expect["ruleset"] else "none"
    act = plan["structure"][0]["id"]
    objective = plan["objectives"][0]["id"]
    path = expect["paths"][0]
    secret = {"id": "s1", "fact": "A hidden truth.", "known_by": [], "reveal": {"condition": "Later.", "from_act": act}}
    known = {"id": "k1", "setting_fact_id": "k1", "reveal": {"condition": "Later.", "from_act": act}}
    knowing = {**expect, "knowledge": ["k1"]}
    seat = {"$participant": "id"}
    slot = {"entity_state": {"name": {"$participant": "name"}, "seat": "east"},
            "append": [{"namespace": ruleset, "key": "seats.east", "value": seat}]}
    mech = {"namespace": ruleset, "key": "score", "value": 1}
    entity = {"id": "lamp", "kind": "item", "source_ref": {"id": "lamp", "path": path}, "state": {"name": "Lamp"}}
    entity_state = ["initial_state", "entities", 0, "state"]
    ref = {"id": "r", "path": path}
    cases = [
        ([["set", ["secrets"], [secret]]], expect),
        ([["set", ["secrets"], ["A plain secret.", secret]]], expect),
        ([["set", ["secrets"], [secret, secret]]], expect),
        ([["set", ["secrets"], [{**secret, "reveal": {"condition": "Later.", "from_act": "nope"}}]]], expect),
        ([["set", ["secrets"], [secret]]], {**expect, "knowledge": ["s1"]}),
        ([["set", ["secrets"], [known]]], knowing),
        ([["set", ["secrets"], [known]]], expect),
        ([["set", ["secrets"], [{**known, "setting_fact_id": "k2"}]]], {**expect, "knowledge": ["k2"]}),
        ([["set", ["secrets"], [{"id": "x"}]]], expect),
        ([["set", ["secrets"], [{**secret, "reveal": {"condition": "Later."}}]]], expect),
        ([["set", ["secrets"], [" padded "]]], expect),
        ([["set", ["initial_state", "mechanics"], [mech]]], expect),
        ([["set", ["initial_state", "mechanics"], [mech, mech]]], expect),
        ([["set", ["initial_state", "mechanics"], [{**mech, "namespace": "other.package"}]]], expect),
        ([["set", ["initial_state", "mechanics"], [{**mech, "namespace": ruleset + ".sub"}]]], expect),
        ([["set", ["initial_state", "mechanics"], [{**mech, "value": {"hidden_reasoning": 1}}]]], expect),
        ([["set", ["initial_state", "mechanics"], [{**mech, "key": " bad"}]]], expect),
        ([["set", ["initial_state", "mechanics"], "x"]], expect),
        ([["set", ["initial_state", "mechanics"], [mech] * 257]], expect),
        ([["set", ["initial_state", "party_placements"], [slot]]], expect),
        ([["set", ["initial_state", "party_placements"], []]], expect),
        ([["set", ["initial_state", "party_placements"], [slot] * 65]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "entity_state": {"$participant": "id"}}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "append": "x"}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "append": slot["append"] * 17}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "append": [{**slot["append"][0], "namespace": "other"}]}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "append": [{**slot["append"][0], "key": "seats..east"}]}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "append": [{**slot["append"][0], "value": {"scratchpad": 1}}]}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "entity_state": {"traceback": 1}}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "entity_state": {"a": [{"$participant": "age"}]}}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{**slot, "entity_state": {"a": {"$participant": "id", "b": 1}}}]]], expect),
        ([["set", ["initial_state", "party_placements"], [{"entity_state": {}}]]], expect),
        ([["set", ["structure"], [plan["structure"][0], plan["structure"][0]]]], expect),
        ([["set", ["structure", 0, "setting_refs"], ["missing.md"]]], expect),
        ([["set", ["structure", 0, "setting_refs"], ["../escape.md"]]], expect),
        ([["set", ["structure", 0, "objectives"], [objective, objective]]], expect),
        ([["set", ["structure", 0, "objectives"], ["no-such-objective"]]], expect),
        ([["set", ["structure", 0, "objectives"], []]], expect),
        ([["set", ["objectives"], [plan["objectives"][0], plan["objectives"][0]]]], expect),
        ([["set", ["objectives"], []]], expect),
        ([["set", ["starting_party", "required_companions"], [ref, ref]]], expect),
        ([["set", ["starting_party", "player_character"], {"mode": "fixed", "character": ref}]], expect),
        ([["set", ["starting_party", "player_character"], {"mode": "fixed"}]], expect),
        ([["set", ["starting_party", "custom_companions"], {"minimum": 3, "maximum": 2}]], expect),
        ([["set", ["starting_party", "custom_companions"], {"minimum": 0, "maximum": 65}]], expect),
        ([["set", ["starting_party", "custom_companions"], {"minimum": False, "maximum": 1}]], expect),
        ([["set", ["setting_references", "lore"], [ref] * 520]], expect),
        ([["set", ["setting_references", "lore"], "x"]], expect),
        ([["set", ["setting_references", "lore"], [{"id": "r", "path": "missing.md"}]]], expect),
        ([["set", ["setting_references", "lore"], ["x"]]], expect),
        ([["set", ["starting_location"], "x"]], expect),
        ([["set", ["bindings", "ruleset", "content_sha256"], "G" * 64]], expect),
        ([["set", ["bindings", "ruleset", "content_sha256"], "a" * 63]], expect),
        ([["set", ["initial_state", "entities"], [entity]]], expect),
        ([["set", ["initial_state", "entities"], [entity]], ["set", [*entity_state, "API_Key"], 1]], expect),
        ([["set", ["initial_state", "mechanics"], [{**mech, "value": {"nested": {"Refresh_Token": "x"}}}]]], expect),
        ([["set", ["initial_state", "entities"], [entity]], ["set", [*entity_state, "notes"], ["fine", "C:\\x"]]], expect),
        ([["set", ["initial_state", "entities"], [entity]], ["set", [*entity_state, "notes"], "\\\\server\\share"]], expect),
        ([["set", ["initial_state", "entities"], [entity]], ["set", [*entity_state, "notes"], "/etc/passwd"]], expect),
        ([["set", ["initial_state", "entities"], [entity]], ["set", [*entity_state, "blob"], {"$repeat": ["x", 600 * 1024]}]], expect),
        ([["set", ["initial_state", "mechanics"], [mech]],
          ["set", ["initial_state", "mechanics", 0, "value"], {"$repeat": ["x", 600 * 1024]}]], expect),
        ([["set", ["starting_party", "required_companions"], [ref] * 65]], expect),
        ([["set", ["initial_state", "entities"], [entity] * 129]], expect),
        ([["set", ["initial_state", "facts"], ["A fact."] * 256]], expect),
        ([["set", ["initial_state", "facts"], ["A fact."] * 257]], expect),
        ([["set", ["secrets"], ["A plain secret."] * 256]], expect),
        ([["set", ["secrets"], ["A plain secret."] * 257]], expect),
        ([["set", ["structure"], [plan["structure"][0]] * 65]], expect),
        ([["set", ["structure"], []]], expect),
        ([["set", ["initial_state", "entities"], [entity]], ["set", [*entity_state, "\u017fecret_Key"], 1]], expect),
        ([["set", ["revision"], 0]], expect),
        ([["set", ["revision"], True]], expect),
        ([["set", ["format_version"], 2.0]], expect),
        ([["set", ["title"], "t" * 160]], expect),
        ([["set", ["title"], "t" * 161]], expect),
        ([["set", ["tone"], "\u00e9" * 500]], expect),
        ([["set", ["tone"], "\u00e9" * 501]], expect),
        ([["set", ["opening_direction"], "Begin."]], expect),
        ([["set", ["initial_state", "facts"], ["A fact.", {"id": "f1", "fact": "Known.", "known_by": ["you"]}]]], expect),
        ([["set", ["initial_state", "facts"], [{"id": "f1", "fact": "Known.", "known_by": []}] * 2]], expect),
        ([["set", ["initial_state", "entities"], plan["initial_state"]["entities"] * 2]], expect),
    ]
    return cases


def plan_cases(path, plan, markdown):
    cases = []
    expect = expectation(plan)
    args = lambda edits=(), md=None, ex=None: {"plan": path, "edits": list(edits), "markdown": md,  # noqa: E731
                                               "expect": ex}
    cases.append({"fn": "validate", **args(), "outcome": outcome(call(plan, markdown, expect), project_plan)})
    if path not in MUTATED:
        return cases
    changed = []
    if expect["ruleset"] is not None:
        other = {**expect["ruleset"], "version": "9.9.9"}
        changed += [{**expect, "ruleset": other}, {**expect, "setting": None}, {**expect, "ruleset": None, "setting": None}]
    changed += [{**expect, "closure": "f" * 64}]
    if expect["paths"]:
        changed += [{**expect, "paths": expect["paths"][1:]}, {**expect, "paths": expect["paths"][:-1]}]
    changed += [{**expect, "knowledge": []}, {**expect, "knowledge": ["act-1", "one-match"]}]
    for ex in changed:
        cases.append({"fn": "validate", **args(ex=ex), "outcome": outcome(call(plan, markdown, ex), project_plan)})
    for md in ["", "   ", "a\x00b", "  padded  ", "x" * (512 * 1024), "x" * (512 * 1024 + 1), "\u00e9" * (300 * 1024)]:
        cases.append({"fn": "validate", **args(md={"text": md} if len(md) < 1000 else {"repeat": [md[0], len(md)]}),
                      "outcome": outcome(call(plan, md, expect), project_plan)})
    for edits, ex in targeted(plan, expect):
        try:
            document = apply(plan, edits)
        except IndexError:
            continue  # a Plan without entities has no entity state to edit
        cases.append({"fn": "validate", **args(edits, ex=None if ex is expect else ex),
                      "outcome": outcome(call(document, markdown, ex), project_plan)})
    base = no_ruleset_plan(plan)
    for edits, document in [([], base), *variants(base, 60)]:
        cases.append({"fn": "validate", "plan": path, "no_ruleset": True, "edits": edits, "markdown": None,
                      "expect": NO_RULESET_EXPECT,
                      "outcome": outcome(call(document, markdown, NO_RULESET_EXPECT), project_plan)})
    for edits, document in variants(plan, 400):
        cases.append({"fn": "validate", **args(edits), "outcome": outcome(call(document, markdown, expect), project_plan)})
    for edits, document in variants(plan, 120, ("starting_party",)):
        cases.append({"fn": "parse_starting_party_policy", **args(edits),
                      "outcome": outcome(lambda: parse_starting_party_policy(document["starting_party"]),
                                         lambda policy: policy.as_payload())})
    return cases


def main() -> None:
    paths = sorted(str(Path(p).relative_to(ROOT)).replace("\\", "/")
                   for p in glob.glob(str(ROOT / "campaign-plans/*/plan.json")))
    cases = []
    expectations = {}
    for path in paths:
        plan = json.loads((ROOT / path).read_text(encoding="utf-8"))
        markdown = (ROOT / path).with_name("plan.md").read_text(encoding="utf-8")
        expectations[path] = expectation(plan)
        cases += plan_cases(path, plan, markdown)
    vectors = {"python": sys.version.split()[0], "plans": paths, "expectations": expectations,
               "no_ruleset_fields": NO_RULESET, "cases": cases}
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
