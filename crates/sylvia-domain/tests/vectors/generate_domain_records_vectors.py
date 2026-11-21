"""Generate reference vectors for sylvia_domain's smaller domain modules from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_domain_records_vectors.py

Modules: `mechanics_choices`, `ruleset_tests`, `campaign_sequel`, `interview_contribution` and
`inventory`'s holdings (`campaign_ending` and
`no_ruleset_plan` are in `generate_plan_reading_vectors.py`). Inputs:
- three shipped Campaign Plans, which the Rust test reads from their paths, and mutations of
  their ending, structure, objectives and secrets (stored as edits), for `prepare_story`;
- synthetic saved choices, scenario suites, sequel reviews and content, an Interview
  Contribution and its answers, and a small inventory with transfers, each with mutations.
For each call it records Python's outcome: the result as `json.dumps` writes it (or that
text's SHA-256 when it is longer than 1,000 characters), or the
exception class and message (plus diagnostics for `ContentShapeError`).
The output is deterministic (fixed seed) and checked in, so the Rust tests need no Python.
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

from sylvia_gm.domain import campaign_sequel, interview_contribution, inventory, mechanics_choices  # noqa: E402
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.content_validation import ContentShapeError  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.domain.ruleset_tests import parse_ruleset_tests, validate_ruleset_tests  # noqa: E402

OUTPUT = Path(__file__).with_name("domain_records_vectors.json")
PLANS = ["campaign-plans/riichi-quick-test/plan.json", "campaign-plans/dead-inventory/plan.json",
         "campaign-plans/the-quarry-turns/plan.json"]
PLAN_FIELDS = [("ending",), ("structure",), ("objectives",), ("secrets",)]
rng = random.Random(20261122)

JUNK = [None, True, False, 0, 1, 2.5, "", " ", "x", "finite", "ongoing", "resolved", "after", "ending:x", "act-1",
        [], [1], ["x"], ["a", "a"], [[1]], {}, {"id": "x"}, {"k": 1}, "\u00e9"]


def plain(value):
    return thaw_json_value(value)


def outcome(call, project=lambda value: value):
    try:
        result = call()
    except ContentShapeError as error:
        return ["ContentShapeError", str(error), [item.as_payload() for item in error.diagnostics]]
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


def mutations(document, count, prefixes=None):
    paths = [(path, value) for path, value in leaves(document) if path
             and (prefixes is None or any(path[:len(prefix)] == prefix for prefix in prefixes))]
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


def variants(document, count, prefixes=None):
    """The base and its mutations that still apply, as (edits, document)."""
    yield [], document
    for edits in mutations(document, count, prefixes):
        try:
            yield edits, apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue


# --- mechanics_choices -----------------------------------------------------------------

CHOICE = {"choice_id": "pick", "prompt": "Which way?", "options": [{"option_id": "left", "label": "Left"},
                                                                   {"option_id": "right", "label": "Right"}]}
# Each answer holds its own copy: an edit to one request must not reach the other.
ANSWERS = [{"resolution_id": "r1", "request": copy.deepcopy(CHOICE), "option_id": "left", "session_id": None},
           {"resolution_id": "r2", "request": copy.deepcopy(CHOICE), "option_id": "right", "session_id": "s1"}]


def choice_cases():
    cases = []
    project_choice = lambda choice: plain(mechanics_choices.choice_payload(choice))  # noqa: E731
    project_answers = lambda answers: [plain(mechanics_choices.answer_payload(a)) for a in answers]  # noqa: E731
    for edits, document in variants(CHOICE, 150):
        cases.append({"fn": "choice_from_payload", "args": [{"base": "choice", "edits": edits}],
                      "outcome": outcome(lambda: mechanics_choices.choice_from_payload(document), project_choice)})
    extra = [None, "x", [], ANSWERS * 32, ANSWERS * 31 + ANSWERS[:1],
             [{**ANSWERS[0], "resolution_id": "r"}, {**ANSWERS[1], "resolution_id": "r"}]]
    for value in extra:
        cases.append({"fn": "answers_from_payload", "args": [{"inline": value, "edits": []}],
                      "outcome": outcome(lambda: mechanics_choices.answers_from_payload(value), project_answers)})
    for edits, document in variants(ANSWERS, 200):
        cases.append({"fn": "answers_from_payload", "args": [{"base": "answers", "edits": edits}],
                      "outcome": outcome(lambda: mechanics_choices.answers_from_payload(document), project_answers)})
    return cases


# --- ruleset_tests ---------------------------------------------------------------------

SUITE = {"format": 1, "scenarios": [
    {"id": "first", "description": "Opening", "covers": ["start"],
     "initial_state": [{"namespace": "pneuma.test", "key": "score", "value": 1}, {"key": "score", "value": 2}],
     "participants": [{"id": "hero", "kind": "player_character", "schema_id": "sheet", "data": {}}],
     "rolls": [{"sides": 6, "value": 6}, {"sides": 20, "value": 1}],
     "steps": [{"kind": "assert", "expect": [{"subject": "state", "path": ["score", 0], "equals": None}]}]},
    {"id": "second", "description": "Again", "covers": ["start", "end"], "initial_state": [], "participants": [],
     "rolls": [], "steps": [{"kind": "assert", "expect": [{"subject": "control", "path": [], "equals": False}]}]},
]}


def deep(levels):
    value = 1
    for _ in range(levels):
        value = [value]
    return value


def ruleset_test_cases():
    cases = []
    project = lambda suite: suite  # noqa: E731
    for edits, document in variants(SUITE, 250):
        data = canonical_json_bytes(document).decode("utf-8")
        cases.append({"fn": "parse_ruleset_tests", "args": [{"base": "suite", "edits": edits}],
                      "outcome": outcome(lambda: parse_ruleset_tests(data.encode("utf-8")), project)})
    duplicate_state = copy.deepcopy(SUITE)
    duplicate_state["scenarios"][0]["initial_state"].append({"namespace": "pneuma.test", "key": "score", "value": 3})
    duplicate_participant = copy.deepcopy(SUITE)
    duplicate_participant["scenarios"][0]["participants"].append(SUITE["scenarios"][0]["participants"][0])
    duplicate_scenario = copy.deepcopy(SUITE)
    duplicate_scenario["scenarios"][1]["id"] = "first"
    high_roll = copy.deepcopy(SUITE)
    high_roll["scenarios"][1]["rolls"] = [{"sides": 4, "value": 5}]
    nested = copy.deepcopy(SUITE)
    for levels in (57, 58, 59, 60, 100):
        nested["scenarios"][0]["initial_state"][0]["value"] = deep(levels)
        cases.append({"fn": "validate_ruleset_tests", "args": [{"inline": copy.deepcopy(nested), "edits": []}],
                      "outcome": outcome(lambda: validate_ruleset_tests(copy.deepcopy(nested)), project)})
    for count in (249_990, 250_001):
        wide = copy.deepcopy(SUITE)
        wide["scenarios"][0]["initial_state"][0]["value"] = [0] * count
        cases.append({"fn": "validate_ruleset_tests", "args": [{"suite_zeros": count}],
                      "outcome": outcome(lambda: validate_ruleset_tests(wide), project)})
    for document in (duplicate_state, duplicate_participant, duplicate_scenario, high_roll, [], "x", None):
        cases.append({"fn": "validate_ruleset_tests", "args": [{"inline": document, "edits": []}],
                      "outcome": outcome(lambda: validate_ruleset_tests(document), project)})
    base = json.dumps(SUITE)
    raws = [base, json.dumps(SUITE, indent=2), base.replace('"format": 1', '"format": 1, "format": 1'), "{", "[]",
            "\ufeff" + base, base.replace('"value": 6', '"value": 6.0'),
            base.replace('"value": 1}', '"value": 12345678901234567890}', 1)]
    for raw in raws:
        cases.append({"fn": "parse_ruleset_tests", "args": [{"raw": raw}],
                      "outcome": outcome(lambda: parse_ruleset_tests(raw.encode("utf-8")), project)})
    # Non-finite floats: Python reads them, then the size walk refuses the first one it meets.
    deep_list = "[" * 70 + "1" + "]" * 70
    non_finite = [
        base.replace('"value": 6}', '"value": NaN}', 1),
        base.replace('"value": 6}', '"value": Infinity}', 1),
        base.replace('"value": 6}', '"value": -Infinity}', 1),
        base.replace('"value": 6}', '"value": 1e999}', 1),
        base.replace('"value": 6}', '"value": -1.5E+400}', 1),
        base.replace('"value": 6}', '"value": 1.7976931348623159e308}', 1),
        base.replace('"value": 6}', '"value": 1.7976931348623157e308}', 1),
        base.replace('"value": 6}', '"value": 1e-999}', 1),
        base.replace('"value": 6}', '"value": 01e999}', 1),
        base.replace('"value": 6}', '"value": -NaN}', 1),
        base.replace('"value": 6}', '"value": NaNx}', 1),
        base.replace('"value": 6}', '"value": [NaN, "NaN", "Infinity"]}', 1),
        base.replace('"value": 6}', '"value": NaN}', 1).replace('"format": 1', '"format": 1, "format": 1'),
        base.replace('"value": 6}', '"value": {"NaN": 1}}', 1),
        base.replace('"description": "Opening"', '"description": "Opening NaN Infinity \\u0000non-finite-0"', 1)
            .replace('"value": 6}', '"value": NaN}', 1),
        '{"format": 1, "scenarios": [], "a": [NaN, ' + deep_list + ']}',
        '{"format": 1, "scenarios": [], "a": [' + deep_list + ', NaN]}',
        "NaN",
        "[NaN]",
    ]
    for raw in non_finite:
        cases.append({"fn": "parse_ruleset_tests", "args": [{"raw": raw}],
                      "outcome": outcome(lambda: parse_ruleset_tests(raw.encode("utf-8")), project)})
    # docs/rust-port/differences.md row 4: Rust refuses this nesting when it reads the JSON.
    raw = base.replace('"equals": null', '"equals": ' + "[" * 130 + "]" * 130, 1)
    cases.append({"fn": "parse_ruleset_tests", "args": [{"raw": raw}], "difference": "row 4",
                  "outcome": outcome(lambda: parse_ruleset_tests(raw.encode("utf-8")), project)})
    for count in (2 * 1024 * 1024, 2 * 1024 * 1024 + 1):
        cases.append({"fn": "parse_ruleset_tests", "args": [{"raw_spaces": count}],
                      "outcome": outcome(lambda: parse_ruleset_tests(b" " * count), project)})
    cases.append({"fn": "parse_ruleset_tests", "args": [{"raw_hex": "ff" + base.encode().hex()}],
                  "outcome": outcome(lambda: parse_ruleset_tests(b"\xff" + base.encode()), project)})
    return cases


# --- campaign_sequel -----------------------------------------------------------------------

def plan_cases(path, plan):
    """`prepare_story` on a shipped Plan, an ongoing variant and mutations of its story fields."""
    cases = []
    project_story = lambda result: [plain(result[0]), dict(result[1])]  # noqa: E731
    last = len(plan["structure"]) - 1
    ongoing = [["set", ["ending"], {"mode": "ongoing"}], ["set", ["structure", last, "objectives"], []]]
    # The mapped identity is "adventure-2-<8 hex>:" (21 characters) plus the old one, at most 160.
    long_ids = [[["set", ["structure", 0, "id"], text]] for text in ("a" * 139, "a" * 140, "\u00e9" * 139, "\u00e9" * 140)]
    bounded = [(edits, apply(plan, edits)) for edits in long_ids]
    bases = [([], plan), (ongoing, apply(plan, ongoing)), *bounded, *variants(plan, 220, PLAN_FIELDS)]
    for edits, document in bases:
        cases.append({"fn": "prepare_story", "args": [{"plan": path, "edits": edits}, {"inline": 2, "edits": []}],
                      "outcome": outcome(lambda: campaign_sequel.prepare_story(document, 2), project_story)})
    return cases


REVIEW = {"connection": " It follows the flood. ", "timeline": "after", "reviewed": True}
CONTENT = {"campaign_plan_markdown": "# The next part", "sequel": {
    "request": {"review": {"connection": "It follows the flood.", "timeline": "after", "reviewed": True}},
    "preview": {"number": 2}, "identity_mapping": {"act-1": "adventure-2-abcdef12:act-1"}}}


def sequel_cases():
    cases = []
    for edits, document in variants(REVIEW, 80):
        cases.append({"fn": "review_payload", "args": [{"base": "review", "edits": edits}],
                      "outcome": outcome(lambda: campaign_sequel.review_payload(document), plain)})
    long_note = {**REVIEW, "connection": "x" * 4000}
    longer_note = {**REVIEW, "connection": "\u00e9" * 4001}
    for document in (long_note, longer_note, {**REVIEW, "reviewed": 1}, {**REVIEW, "extra": 1}):
        cases.append({"fn": "review_payload", "args": [{"inline": document, "edits": []}],
                      "outcome": outcome(lambda: campaign_sequel.review_payload(document), plain)})
    mappings = [[["a", "b"]], [["a", "b"], ["a", "c"]], ["ab", "cd"], [["a"]], [[1, 2]], [[True, 1], [1, 2]], [1], "ab",
                [[["x"], 1]], [{"k": 1, "l": 2}], None, 5, {}, [], [["a", 1.5]], [[None, "x"]]]
    for edits, document in variants(CONTENT, 120):
        for fn, call, project in (("sequel_markdown", campaign_sequel.sequel_markdown, lambda text: text),
                                  ("sequel_preparation", campaign_sequel.sequel_preparation,
                                   lambda value: None if value is None else plain(value))):
            cases.append({"fn": fn, "args": [{"base": "content", "edits": edits}],
                          "outcome": outcome(lambda: call(document), project)})
    for mapping in mappings:
        document = copy.deepcopy(CONTENT)
        document["sequel"]["identity_mapping"] = mapping
        cases.append({"fn": "sequel_markdown", "args": [{"inline": document, "edits": []}],
                      "outcome": outcome(lambda: campaign_sequel.sequel_markdown(document))})
    for value in ({}, [], "x", None, {"a": 1}):
        cases.append({"fn": "json_object", "args": [{"inline": value, "edits": []}],
                      "outcome": outcome(lambda: campaign_sequel.json_object(value), plain)})
    return cases


# --- interview_contribution --------------------------------------------------------------

CONTRIBUTION = {
    "format_id": "sylvia-gm-interview-contribution", "format_version": 1, "owner_kind": "ruleset", "id": "table-setup",
    "title": "Table setup", "questions": [
        {"id": "length", "prompt": "How long should the match be?", "type": "choice", "required": True,
         "adaptive": False, "options": [{"id": "east", "label": "East only"}, {"id": "south", "label": "East and South"}],
         "condition": None},
        {"id": "rivals", "prompt": "Who sits across from you?", "type": "free_text", "required": False, "adaptive": True,
         "options": [], "condition": {"question_id": "length", "equals": "south"}},
        {"id": "mood", "prompt": "What mood fits the evening?", "type": "free_text", "required": True, "adaptive": False,
         "options": [], "condition": None}]}
ANSWER = {"contribution_id": "table-setup", "answers": {"length": "south", "rivals": "Old friends", "mood": "Calm"}}


def interview_cases():
    cases = []
    project = lambda c: [plain(c.as_payload()), c.question_text()]  # noqa: E731
    for edits, document in variants(CONTRIBUTION, 200):
        data = canonical_json_bytes(document)
        for owner in ("ruleset", "campaign_plan"):
            cases.append({"fn": "parse_interview_contribution_bytes",
                          "args": [{"base": "contribution", "edits": edits}, {"inline": owner, "edits": []}],
                          "outcome": outcome(lambda: interview_contribution.parse_interview_contribution_bytes(
                              data, expected_owner_kind=owner), project)})
    for raw in ("", "{", " " * (32 * 1024 + 1), json.dumps(CONTRIBUTION)):
        cases.append({"fn": "parse_interview_contribution_bytes", "args": [{"raw": raw}, {"inline": "ruleset", "edits": []}],
                      "outcome": outcome(lambda: interview_contribution.parse_interview_contribution_bytes(
                          raw.encode(), expected_owner_kind="ruleset"), project)})
    cases.append({"fn": "parse_interview_contribution_bytes",
                  "args": [{"base": "contribution", "edits": []}, {"inline": "builder", "edits": []}],
                  "outcome": outcome(lambda: interview_contribution.parse_interview_contribution_bytes(
                      canonical_json_bytes(CONTRIBUTION), expected_owner_kind="builder"), project)})
    contribution = interview_contribution.parse_interview_contribution_bytes(
        canonical_json_bytes(CONTRIBUTION), expected_owner_kind="ruleset")
    extra = [
        {"contribution_id": "table-setup", "answers": {"length": "east", "mood": "Calm"}},
        {"contribution_id": "table-setup", "answers": {"length": "east", "rivals": "x", "mood": "Calm"}},
        {"contribution_id": "table-setup", "answers": {"length": None, "mood": "Calm"}},
        {"contribution_id": "table-setup", "answers": {"length": "south", "rivals": None, "mood": "Calm"}},
        {"contribution_id": "table-setup", "answers": {"length": "south", "mood": "x" * 2000}},
        {"contribution_id": "table-setup", "answers": {"length": "south", "mood": "\u00e9" * 2001}},
        {"contribution_id": "table-setup", "answers": {"length": "south", "mood": "a\x00b"}},
        {"contribution_id": "table-setup", "answers": {"length": "south", "mood": " Calm"}},
        {"contribution_id": "table-setup", "answers": {"length": "south", "extra": "x", "mood": "Calm"}},
        [], "x", None,
    ]
    for value in extra:
        cases.append({"fn": "validate_interview_contribution_answers", "args": [{"inline": value, "edits": []}],
                      "outcome": outcome(lambda: interview_contribution.validate_interview_contribution_answers(
                          contribution, value), plain)})
    for edits, document in variants(ANSWER, 150):
        cases.append({"fn": "validate_interview_contribution_answers", "args": [{"base": "answer", "edits": edits}],
                      "outcome": outcome(lambda: interview_contribution.validate_interview_contribution_answers(
                          contribution, document), plain)})
    return cases


# --- inventory -----------------------------------------------------------------------------

ENTITIES = ["hero", "keeper", "chest"]
HOLDINGS = [
    {"holding_id": "h-coins", "item_id": "silver", "name": "Silver coins", "kind": "currency", "stackable": True,
     "quantity": 30, "owner_id": "hero", "holder_id": "hero", "visibility": "player_known", "known_by": ["hero"]},
    {"holding_id": "h-key", "item_id": "old-key", "name": "Old key", "kind": "item", "stackable": False,
     "quantity": 1, "owner_id": "keeper", "holder_id": "hero", "visibility": "player_known", "known_by": []},
    {"holding_id": "h-secret", "item_id": "map", "name": "Hidden map", "kind": "item", "stackable": False,
     "quantity": 1, "owner_id": "keeper", "holder_id": "chest", "visibility": "gm_private", "known_by": ["keeper"]},
    {"holding_id": "h-empty", "item_id": "silver", "name": "Silver coins", "kind": "currency", "stackable": True,
     "quantity": 0, "owner_id": "keeper", "holder_id": "keeper", "visibility": "player_known", "known_by": ["hero"]},
]
TRANSFERS = [
    ("h-coins", "h-purse", 10, "keeper", None), ("h-coins", "h-coins", 30, "keeper", None),
    ("h-coins", "h-coins", 29, "keeper", None), ("h-coins", "h-empty", 5, "keeper", "keeper"),
    ("h-coins", "h-empty", 5, "keeper", None), ("h-key", "h-key", 1, "keeper", "keeper"),
    ("h-key", "h-key2", 1, "hero", None), ("h-coins", "h-key", 1, "hero", "keeper"),
    ("h-coins", "h-x", 31, "hero", None), ("missing", "h-x", 1, "hero", None), ("h-coins", "h-x", 0, "hero", None),
    ("h-coins", "h-x", True, "hero", None), ("h-coins", "h-x", 1.0, "hero", None), ("h-coins", " h", 1, "hero", None),
    ("h-coins", "h-x", 1, "hero", ""), ("h-secret", "h-loot", 1, "hero", "hero"), ("h-coins", "h-x", 1, "nobody", None),
    ("h-coins", "h-empty", 5, "keeper", "hero"),
]
VISIBLE = {"hero": "The Hero", "keeper": "Keeper"}


def transfer_case(base, edits, holdings, transfer, entity_ids, project):
    source, destination, quantity, holder, owner = transfer
    return {"fn": "transfer_holding",
            "args": [{base[0]: base[1], "edits": edits}, {"inline": list(transfer), "edits": []}],
            "outcome": outcome(lambda: inventory.transfer_holding(
                holdings, source_id=source, destination_id=destination, quantity=quantity, holder_id=holder,
                entity_ids=entity_ids, owner_id=owner), project)}


def inventory_cases():
    cases = []
    project = lambda holdings: [plain(item) for item in holdings]  # noqa: E731
    entity_ids = frozenset(ENTITIES)
    for edits, document in variants(HOLDINGS, 300):
        cases.append({"fn": "validate_holdings", "args": [{"base": "holdings", "edits": edits}],
                      "outcome": outcome(lambda: inventory.validate_holdings(document, entity_ids=entity_ids), project)})
        for character in ("hero", "keeper", "chest") if not edits else ("hero",):
            cases.append({"fn": "player_inventory",
                          "args": [{"base": "holdings", "edits": edits}, {"inline": character, "edits": []}],
                          "outcome": outcome(lambda: inventory.player_inventory(
                              document, visible_entities=VISIBLE, character_id=character), plain)})
    big = [{**HOLDINGS[0], "quantity": 2**53 - 1}, {**HOLDINGS[3], "quantity": 2**53 - 1}]
    singles = (big, [{**HOLDINGS[0], "quantity": 2**53}], [{**HOLDINGS[0], "quantity": 1.0}],
               [{**HOLDINGS[0], "quantity": True}], [{**HOLDINGS[1], "quantity": 2}],
               [HOLDINGS[1], {**HOLDINGS[1], "holding_id": "h-key-copy"}],
               [HOLDINGS[0], {**HOLDINGS[0], "holding_id": "h-coins-2"}],
               [{**HOLDINGS[0], "name": "x" * 300}], [{**HOLDINGS[0], "name": "x" * 301}],
               [{**HOLDINGS[0], "name": "\u00e9" * 300}], [{**HOLDINGS[0], "name": "\u00e9" * 301}],
               [{**HOLDINGS[0], "kind": "currency", "stackable": False}], [{**HOLDINGS[0], "known_by": ["a", "a"]}])
    for holdings in singles:
        cases.append({"fn": "validate_holdings", "args": [{"inline": holdings, "edits": []}],
                      "outcome": outcome(lambda: inventory.validate_holdings(holdings, entity_ids=entity_ids), project)})
    full = [HOLDINGS[0], {**HOLDINGS[3], "quantity": 2**53 - 1}]
    cases.append(transfer_case(("inline", full), [], full, ("h-coins", "h-empty", 30, "keeper", None), entity_ids, project))
    for transfer in TRANSFERS:
        cases.append(transfer_case(("base", "holdings"), [], HOLDINGS, transfer, entity_ids, project))
    for edits, document in variants(HOLDINGS, 100):
        cases.append(transfer_case(("base", "holdings"), edits, document, rng.choice(TRANSFERS), entity_ids, project))
    return cases


def main() -> None:
    cases = choice_cases() + ruleset_test_cases() + sequel_cases() + interview_cases() + inventory_cases()
    for path in PLANS:
        plan = json.loads((ROOT / path).read_text(encoding="utf-8"))
        cases += plan_cases(path, plan)
    bases = {"choice": CHOICE, "answers": ANSWERS, "suite": SUITE, "review": REVIEW, "content": CONTENT,
             "contribution": CONTRIBUTION, "answer": ANSWER, "holdings": HOLDINGS}
    vectors = {"python": sys.version.split()[0], "bases": bases, "entity_ids": ENTITIES,
               "visible_entities": VISIBLE, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8",
                      newline="\n")
    kinds = {}
    for case in cases:
        key = (case["fn"], case["outcome"][0])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases")
    for key in sorted(kinds):
        print(" ", key, kinds[key])


if __name__ == "__main__":
    main()
