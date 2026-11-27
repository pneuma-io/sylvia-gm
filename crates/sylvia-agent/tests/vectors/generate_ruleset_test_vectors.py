"""Record Python's Ruleset test worker pieces as ruleset_test_vectors.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_ruleset_test_vectors.py

Every expected value is what Python's own code returns or raises:

- `checked_outcomes`, `summarize_coverage`, `_assertions`, `ScenarioDice` and `_failure_message`
  on hand-built inputs;
- `RulesetTestProvider`, observed through `observe` around a scripted driver that discovers the
  catalog, emits tool evidence and streams requests (callable-tools records built by the context
  ledger's own `callable_record`);
- `run_scenario` on the example Ruleset's real archive, noting whether each scenario reached
  `create_campaign` (the Rust port stops there for now);
- `run_ruleset_tests` with its worker replaced by a stand-in script that follows the protocol
  the Rust test's stand-in follows (`--payload FILE`, `--exit CODE`, `--hang`, `--echo`).

Two runs give identical bytes. Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from sylvia_gm.agent import _ruleset_test_runner as runner  # noqa: E402
from sylvia_gm.agent._context_ledger import callable_record  # noqa: E402
from sylvia_gm.agent._provider_session import _tool_alias  # noqa: E402
from sylvia_gm.agent._ruleset_test_coverage import checked_outcomes, summarize_coverage  # noqa: E402
from sylvia_gm.agent._ruleset_test_provider import RulesetTestProvider, ScenarioScriptError  # noqa: E402
from sylvia_gm.agent._session_loop import ProviderEvent  # noqa: E402
from sylvia_gm.application import authority as authority_module  # noqa: E402
from sylvia_gm.application import ruleset_testing  # noqa: E402
from sylvia_gm.application.package_materialization import materialize_package_archive  # noqa: E402
from sylvia_gm.builder._ruleset_source import compile_ruleset_source  # noqa: E402
from sylvia_gm.domain import errors  # noqa: E402
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE as SOURCE  # noqa: E402
from sylvia_gm.domain._content_schema_ruleset_tests import EXAMPLE  # noqa: E402

OUTPUT = Path(__file__).with_name("ruleset_test_vectors.json")
NL = chr(10)


def raised(error: BaseException) -> dict:
    return {"raised": type(error).__name__, "message": str(error)}


# ── Coverage ──────────────────────────────────────────────────────────────────────────────


def tool_result(tool, status, **extra):
    return {"tool": tool, "result": {"status": status, **extra}}


def assertion(subject, path, equals=None):
    return {"subject": subject, "path": path, "equals": equals}


COMMITTED = {"status": "committed", "result": {"tool": "attack", "context": {"roll": 4}}}
CHECKED_CASES = [
    ("state_covers_committed", [assertion("state", ["x"])], COMMITTED,
     [tool_result("move", "action_committed"), tool_result("look", "rejected")]),
    ("state_without_path", [assertion("state", [])], COMMITTED, [tool_result("move", "action_committed")]),
    ("receipt_context", [assertion("receipt", ["result", "context", "roll"])], COMMITTED, []),
    ("receipt_context_too_short", [assertion("receipt", ["result", "context"])], COMMITTED, []),
    ("receipt_not_committed", [assertion("receipt", ["result", "context", "roll"])],
     {"status": "rejected", "result": {"tool": "attack"}}, []),
    ("control_path", [assertion("control", ["owner"])], COMMITTED, []),
    ("control_empty_receipt", [assertion("control", ["owner"])], {}, []),
    ("control_no_receipt", [assertion("control", ["owner"])], None, []),
    ("receipt_without_tool", [assertion("control", ["owner"])], {"status": "committed", "result": {}}, []),
    ("tool_result_committed", [assertion("tool_results", [0, "result", "result", "total"])], None,
     [tool_result("roll", "action_committed")]),
    ("tool_result_committed_short", [assertion("tool_results", [0, "result", "result"])], None,
     [tool_result("roll", "action_committed")]),
    ("tool_result_rejected", [assertion("tool_results", [1, "result", "status"])], None,
     [tool_result("roll", "action_committed"), tool_result("cast", "rejected")]),
    ("tool_result_precondition", [assertion("tool_results", [0, "result", "code"])], None,
     [tool_result("cast", "action_failed", code="ruleset_precondition_failed")]),
    ("tool_result_wrong_key", [assertion("tool_results", [0, "result", "message"])], None,
     [tool_result("cast", "rejected")]),
    ("tool_result_out_of_range", [assertion("tool_results", [3, "result", "status"]),
                                  assertion("tool_results", [-1, "result", "status"])], None,
     [tool_result("cast", "rejected")]),
    ("tool_result_not_result", [assertion("tool_results", [0, "tool", "status"])], None,
     [tool_result("cast", "rejected")]),
    ("tool_result_bool_index", [assertion("tool_results", [True, "result", "status"]),
                                assertion("tool_results", [0.0, "result", "status"])], None,
     [tool_result("cast", "rejected")]),
    ("tool_result_huge_index", [assertion("tool_results", [2 ** 63, "result", "status"])], None,
     [tool_result("cast", "rejected")]),
    ("receipt_and_tool_committed", [assertion("state", ["hp"])],
     {"status": "committed", "result": {"tool": "heal"}}, [tool_result("heal", "action_committed"),
                                                          tool_result("bless", "action_committed")]),
    ("mixed", [assertion("tool_results", [0, "result", "status"]), assertion("state", ["hp"]),
               assertion("available_tools", [0])], None,
     [tool_result("b", "rejected", code="ruleset_precondition_failed"), tool_result("a", "action_committed")]),
]


def step(actions=(), rejections=(), preconditions=None, phases=None):
    coverage = {"actions": list(actions), "rejections": list(rejections)}
    if preconditions is not None:
        coverage["preconditions"] = list(preconditions)
    if phases is not None:
        coverage["handoff_phases"] = list(phases)
    return {"coverage": coverage}


SUMMARY_CASES = [
    ("empty", {"a"}, [], [], None, None, False),
    ("one_checked", {"a", "b"}, [{"status": "passed", "steps": [step(["a"])]}],
     [{"id": "s1", "covers": ["a"]}], None, None, False),
    ("failed_scenario_counts_nothing", {"a"},
     [{"status": "failed", "steps": [step(["a"])]}], [{"id": "s1", "covers": ["a"]}], None, None, False),
    ("unproven_claim", {"a", "b"}, [{"status": "passed", "steps": [step(["a"])]}],
     [{"id": "s1", "covers": ["a", "b", "c"]}], {"a"}, None, False),
    ("interaction_and_rejections", {"a", "b", "c"},
     [{"status": "passed", "steps": [step(["a", "b"], ["c"], ["c", "z"]), step(["c"])]},
      {"status": "passed", "steps": [step(["b"], ["a"])]}],
     [{"id": "s1", "covers": ["a", "b", "c"]}, {"id": "s2", "covers": ["b", "a"]}],
     {"a", "c", "d"}, {"c", "e"}, False),
    ("handoff", {"a"},
     [{"status": "passed", "steps": [step(["a"], phases=["held"]), step(phases=["released", "held"])]},
      {"status": "passed", "steps": [step(["a"], phases=["held"])]}],
     [{"id": "h1", "covers": ["a"]}, {"id": "h2", "covers": ["a"]}], None, None, True),
    ("handoff_missing", {"a"}, [{"status": "passed", "steps": [step(["a"], phases=["released"])]}],
     [{"id": "h1", "covers": ["a"]}], set(), set(), True),
    ("undeclared_observed", {"a", "b"}, [{"status": "passed", "steps": [step(["a", "b"], ["b"])]}],
     [{"id": "s1", "covers": ["a"]}], {"b"}, {"a"}, False),
    ("unicode_order", {"é", "z", "Z", "a"}, [{"status": "passed", "steps": [step(["é", "Z"])]}],
     [{"id": "s1", "covers": ["é", "Z", "a"]}], None, None, False),
]


def coverage_vectors():
    checked = [
        {"name": name, "assertions": assertions, "receipt": receipt, "tool_results": results,
         "expected": checked_outcomes(assertions, receipt, results)}
        for name, assertions, receipt, results in CHECKED_CASES
    ]
    summaries = []
    for name, required, results, scenarios, rejections, preconditions, handoff in SUMMARY_CASES:
        summaries.append({
            "name": name, "required": sorted(required), "results": results, "scenarios": scenarios,
            "required_rejections": None if rejections is None else sorted(rejections),
            "required_preconditions": None if preconditions is None else sorted(preconditions),
            "requires_handoff": handoff,
            "expected": summarize_coverage(
                required, results, scenarios, required_rejections=rejections,
                required_preconditions=preconditions, requires_handoff=handoff),
        })
    return checked, summaries


# ── Assertions, dice and failure messages ─────────────────────────────────────────────────

OBSERVATIONS = {
    "state": {"hp": 3, "ratio": 0.5, "flag": True, "items": [1, {"k": "v"}], "none": None, "one": 1,
              "nested": {"list": [1, True, 1.0]}},
    "receipt": {"status": "committed", "result": {"tool": "attack"}},
    "tool_results": [tool_result("roll", "action_committed", result={"total": 7})],
    "control": None,
    "available_tools": ["attack", "roll"],
}
ASSERTION_CASES = [
    ("exact", [assertion("state", ["hp"], 3), assertion("state", ["ratio"], 0.5)]),
    ("int_is_not_float", [assertion("state", ["hp"], 3.0), assertion("state", ["ratio"], 0)]),
    ("bool_is_not_int", [assertion("state", ["flag"], 1), assertion("state", ["one"], True)]),
    ("nested_python_equality", [assertion("state", ["nested"], {"list": [True, 1.0, 1]}),
                                assertion("state", ["items"], [1.0, {"k": "v"}])]),
    ("missing_is_not_null", [assertion("state", ["absent"], None), assertion("state", ["none"], None)]),
    ("list_index", [assertion("state", ["items", 1, "k"], "v"), assertion("state", ["items", 2], 1),
                    assertion("state", ["items", -1], 1), assertion("state", ["items", True], 1)]),
    ("key_on_list_and_index_on_dict", [assertion("state", ["items", "0"], 1), assertion("state", [0], 3)]),
    ("whole_subject", [assertion("control", [], None), assertion("available_tools", [], ["attack", "roll"]),
                       assertion("receipt", ["result"], {"tool": "attack"})]),
    ("tool_results", [assertion("tool_results", [0, "result", "result", "total"], 7),
                      assertion("tool_results", [0, "tool"], "cast")]),
    ("through_scalar", [assertion("state", ["hp", "x"], 1), assertion("control", ["owner"], "gm")]),
]


def assertion_vectors():
    cases = [{"name": name, "expected": expected,
              "failures": runner._assertions(expected, OBSERVATIONS)} for name, expected in ASSERTION_CASES]
    try:
        runner._assertions([assertion("unknown", [], 1)], OBSERVATIONS)
    except KeyError as error:
        unknown = raised(error)
    return {"observations": OBSERVATIONS, "cases": cases, "unknown_subject": unknown["raised"]}


DICE_CASES = [
    ("exact", [{"sides": 20, "value": 13}, {"sides": 6, "value": 1}], [[1, 20], [1, 6]]),
    ("table_draw", [{"sides": 4, "value": 4}], [[0, 3]]),
    ("too_many", [{"sides": 6, "value": 2}], [[1, 6], [1, 6], [1, 6]]),
    ("wrong_size", [{"sides": 8, "value": 2}, {"sides": 6, "value": 3}], [[1, 6], [1, 6]]),
    ("unused", [{"sides": 6, "value": 2}, {"sides": 6, "value": 5}], [[1, 6]]),
    ("none", [], []),
]


def dice_vectors():
    cases = []
    for name, rolls, calls in DICE_CASES:
        dice = runner.ScenarioDice(deepcopy(rolls))
        outcomes = []
        for low, high in calls:
            try:
                outcomes.append({"value": dice.randint(low, high)})
            except ScenarioScriptError as error:
                outcomes.append(raised(error))
        try:
            dice.assert_finished()
            finished = None
        except ScenarioScriptError as error:
            finished = raised(error)
        cases.append({"name": name, "rolls": rolls, "calls": calls, "outcomes": outcomes,
                      "failure": dice.failure, "finished": finished})
    return cases


def exception(name: str, code):
    cls = getattr(errors, name, None) or {
        "ValueError": ValueError, "KeyError": KeyError, "RuntimeError": RuntimeError,
        "TypeError": TypeError, "StopIteration": StopIteration,
    }[name]
    error = cls.__new__(cls)
    Exception.__init__(error, "private runtime detail")
    if code is not None:
        error.code = code
    return error


FAILURE_CASES = [
    ("CommandRejected", "command_rejected"), ("CommandRejected", "archive_invalid"),
    ("MechanicsRejected", "mechanics_rejected"), ("RulesetPreconditionRejected", "ruleset_precondition_failed"),
    ("SchemaIdentityError", "schema_identity_mismatch"), ("StaleRevision", "stale_revision"),
    ("WrongOwner", "wrong_owner"), ("WrongWriter", "wrong_writer"), ("OwnerConflict", "owner_conflict"),
    ("IdempotencyConflict", "idempotency_conflict"),
    ("CommandRejected", "Upper_case"), ("CommandRejected", "9starts_with_digit"),
    ("CommandRejected", "a" * 80), ("CommandRejected", "a" * 81), ("CommandRejected", "has-dash"),
    ("CommandRejected", "é"), ("CommandRejected", ""),
    ("ValueError", "ignored_code"), ("KeyError", "ignored_code"), ("RuntimeError", None),
    ("TypeError", None), ("StopIteration", None),
]


def failure_vectors():
    cases = [{"class": name, "code": code, "message": runner._failure_message(exception(name, code))}
             for name, code in FAILURE_CASES]
    script = runner._failure_message(ScenarioScriptError("The scripted operation is not uniquely available."))
    return {"raised": cases, "script": {"message": "The scripted operation is not uniquely available.",
                                        "expected": script}}


# ── The scripted provider ─────────────────────────────────────────────────────────────────


def mechanics_tool(name, package="example.rules", capability=None):
    return {
        "package_id": package, "package_version": "1.0.0", "package_sha256": "a" * 64, "name": name,
        "capability": capability or f"mechanics.{name}", "input_schema": {"type": "object"},
        "parameter_derivations": None, "description": f"{name} tool", "category": "mechanic",
        "model_behavior": "optional",
    }


def as_tool(record):
    return SimpleNamespace(**record)


def alias(record):
    return _tool_alias(as_tool(record))


def definition(name):
    return {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}


@dataclass(frozen=True)
class FakeInput:
    """The fields `observe` uses of `SessionProviderInput`."""

    catalog: tuple = ()
    catalog_supplier: Any = field(default=None)

    def current_catalog(self, catalog_request_id: str = "static-fixture"):
        if self.catalog_supplier is not None:
            return self.catalog_supplier(catalog_request_id)
        return self.catalog


def chunk_record(chunk):
    return {"content": chunk.content, "tool_call_delta": chunk.tool_call_delta,
            "finish_reason": chunk.finish_reason}


async def collect(provider, messages, tools):
    return [chunk_record(chunk) async for chunk in provider.generate_stream(messages, tools)]


def run_provider_case(case):
    owners = list(case["owners"])
    provider = RulesetTestProvider(case["replies"], decision_owner=lambda: owners.pop(0))
    outputs = []

    def driver(provider_input):
        for op in case["ops"]:
            if op["op"] == "discover":
                outputs.append([tool.name for tool in provider_input.current_catalog(op["id"])])
            elif op["op"] == "evidence":
                outputs.append("emitted")
                yield ProviderEvent.tool_evidence(message_id="scenario-message", payload=op["payload"])
            elif op["op"] == "stream":
                try:
                    outputs.append(asyncio.run(collect(provider, op["messages"], op["tools"])))
                except ScenarioScriptError as error:
                    outputs.append(raised(error))
            elif op["op"] == "generate":
                try:
                    asyncio.run(provider.generate(op["messages"]))
                except ScenarioScriptError as error:
                    outputs.append(raised(error))
            elif op["op"] == "finish":
                try:
                    provider.assert_finished()
                    outputs.append("finished")
                except ScenarioScriptError as error:
                    outputs.append(raised(error))

    observed = provider.observe(driver)
    try:
        for _event in observed(FakeInput(catalog=tuple(as_tool(tool) for tool in case["catalog"]))):
            pass
        terminal = None
    except Exception as error:  # noqa: BLE001 - recorded
        terminal = raised(error)
    return {**case, "outputs": outputs, "terminal": terminal, "requests": provider.requests,
            "tool_results": provider.tool_results, "position": provider.position,
            "failure": provider.failure}


def provider_vectors():
    roll, cast = mechanics_tool("roll"), mechanics_tool("cast")
    disposition = mechanics_tool("update_campaign_file", package="sylvia.narrative",
                                 capability="narrative_state.update_campaign_file")
    other_disposition = mechanics_tool("update_campaign_file", package="other.narrative")
    sent = [definition(alias(roll)), definition(alias(cast)), definition("unknown_tool")]
    with_file = [*sent, definition(alias(disposition))]

    def record(offered_names, tools=sent):
        return callable_record(tools, [definition(name) for name in offered_names])

    def messages(*records):
        return [{"role": "system", "content": "Play."}, *({"role": "user", "content": text} for text in records)]

    discover = {"op": "discover", "id": "discovery-1"}
    tool_reply = {"kind": "tool", "tool": "roll", "arguments": {"dice": "1d20", "note": "é"},
                  "expect_request": {"decision_owner": "gm", "available_tools": ["roll"]}}
    narration = {"kind": "narration", "text": "The coin lands heads."}
    cases = [
        {"name": "tool_reply", "replies": [tool_reply], "owners": ["gm"], "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "messages": messages(), "tools": sent}, {"op": "finish"}]},
        {"name": "before_discovery", "replies": [narration], "owners": ["none"], "catalog": [roll],
         "ops": [{"op": "stream", "messages": messages(), "tools": sent}, {"op": "finish"}]},
        {"name": "narration_without_disposition", "replies": [narration], "owners": ["none"],
         "catalog": [roll], "ops": [discover, {"op": "stream", "messages": messages(), "tools": None}]},
        {"name": "narration_through_disposition", "replies": [narration], "owners": ["player"],
         "catalog": [roll, cast, disposition],
         "ops": [discover, {"op": "stream", "messages": messages(), "tools": with_file}]},
        {"name": "ambiguous_disposition", "replies": [narration], "owners": ["gm"],
         "catalog": [roll, disposition, other_disposition],
         "ops": [discover, {"op": "stream", "messages": messages(),
                            "tools": [*with_file, definition(alias(other_disposition))]}, {"op": "finish"}]},
        {"name": "not_uniquely_available", "replies": [{"kind": "tool", "tool": "cast", "arguments": {}},
                                                       narration], "owners": ["gm", "gm"],
         "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "messages": messages(), "tools": [definition(alias(roll))]},
                 {"op": "stream", "messages": messages(), "tools": sent}, {"op": "finish"}]},
        {"name": "owner_differs", "replies": [{**narration, "expect_request": {"decision_owner": "player"}}],
         "owners": ["gm"], "catalog": [roll], "ops": [discover, {"op": "stream", "messages": messages(),
                                                                  "tools": sent}]},
        {"name": "expected_unavailable", "replies": [{**narration, "expect_request": {
            "available_tools": ["roll", "cast"]}}], "owners": ["gm"], "catalog": [roll],
         "ops": [discover, {"op": "stream", "messages": messages(), "tools": sent}]},
        {"name": "withheld_available", "replies": [{**narration, "expect_request": {
            "unavailable_tools": ["missing", "roll"]}}], "owners": ["gm"], "catalog": [roll],
         "ops": [discover, {"op": "stream", "messages": messages(), "tools": sent}]},
        {"name": "script_ended", "replies": [], "owners": ["gm"], "catalog": [roll],
         "ops": [discover, {"op": "stream", "messages": messages(), "tools": sent}, {"op": "finish"}]},
        {"name": "unused_replies", "replies": [narration, narration], "owners": ["gm"], "catalog": [roll],
         "ops": [discover, {"op": "stream", "messages": messages(), "tools": sent}, {"op": "finish"},
                 {"op": "finish"}]},
        {"name": "record_none_callable", "replies": [tool_reply], "owners": ["gm"], "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "messages": messages(record([])), "tools": sent}]},
        {"name": "record_only", "replies": [{"kind": "tool", "tool": "cast", "arguments": {"x": 1},
                                             "expect_request": {"unavailable_tools": ["roll"]}}],
         "owners": ["gm"], "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "messages": messages(record([alias(cast)])), "tools": sent}]},
        {"name": "record_except", "replies": [tool_reply], "owners": ["gm"], "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "tools": with_file,
                            "messages": messages(record([alias(roll), alias(cast), "unknown_tool"], with_file))}]},
        {"name": "record_every", "replies": [tool_reply], "owners": ["gm"], "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "tools": sent,
                            "messages": messages(record([alias(roll), alias(cast), "unknown_tool"]))}]},
        {"name": "record_latest_applies", "replies": [tool_reply], "owners": ["gm"], "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "tools": sent,
                            "messages": messages(record([alias(roll)]), record([]))}]},
        {"name": "record_in_list_content", "replies": [tool_reply], "owners": ["gm"], "catalog": [roll, cast],
         "ops": [discover, {"op": "stream", "tools": sent, "messages": [
             {"role": "user", "content": [{"type": "text", "text": record([alias(roll)])}]}]}]},
        {"name": "evidence", "replies": [], "owners": [], "catalog": [roll, cast],
         "ops": [discover, {"op": "evidence", "payload": {"kind": "provider_tool_results", "results": [
             {"name": alias(roll), "content": json.dumps({"status": "action_committed", "action_id": "a1"})},
             {"name": "unknown_tool", "content": "[1, 2]"}]}},
             {"op": "evidence", "payload": {"kind": "tool_calls", "results": "ignored"}},
             {"op": "evidence", "payload": {"kind": "provider_tool_results", "results": []}}]},
        {"name": "evidence_invalid_json", "replies": [], "owners": [], "catalog": [roll],
         "ops": [discover, {"op": "evidence", "payload": {"kind": "provider_tool_results", "results": [
             {"name": alias(roll), "content": "{"}]}}, {"op": "finish"}]},
        {"name": "generate", "replies": [], "owners": [], "catalog": [],
         "ops": [{"op": "generate", "messages": messages()}]},
    ]
    return [run_provider_case(case) for case in cases]


# ── run_scenario ──────────────────────────────────────────────────────────────────────────


def example_archive(source=None) -> bytes:
    source = deepcopy(SOURCE if source is None else source)
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    with materialize_package_archive((compiled.draft,)) as candidate:
        candidate.archive.seek(0)
        return candidate.archive.read()


VISITOR = {
    "kind": "player_character", "schema_id": "example.visitor",
    "schema": {"$id": "example.visitor", "type": "object", "properties": {"name": {"type": "string"}},
               "required": ["name"], "additionalProperties": False},
    "entity_state_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "initial_state_calculations": [],
}


def scenario_vectors():
    plain = example_archive()
    with_visitor = example_archive({**deepcopy(SOURCE), "participant_artifacts": [VISITOR]})
    base = deepcopy(EXAMPLE["scenarios"][0])

    def visitor(**changes):
        return {"id": "visitor", "kind": "player_character", "schema_id": "example.visitor",
                "data": {"name": "Visitor"}, **changes}

    cases = [
        ("valid", plain, base),
        ("undeclared_schema", plain, {**base, "participants": [visitor()]}),
        ("declared_participant", with_visitor, {**base, "participants": [visitor()]}),
        ("participant_bad_id", with_visitor, {**base, "participants": [visitor(id="Bad Id!")]}),
        ("participant_data_not_object", with_visitor, {**base, "participants": [visitor(data=[1])]}),
        ("not_an_archive", b"not a package", base),
    ]
    reached = []
    original = authority_module.AuthorityOwner.create_campaign

    def create_campaign(self, command):
        reached.append(True)
        return original(self, command)

    authority_module.AuthorityOwner.create_campaign = create_campaign
    archives, vectors = {}, []
    try:
        for name, archive, scenario in cases:
            key = {id(plain): "plain", id(with_visitor): "with_visitor"}.get(id(archive), name)
            archives[key] = base64.b64encode(archive).decode("ascii")
            reached.clear()
            with tempfile.TemporaryDirectory() as directory:
                result = runner.run_scenario(archive, deepcopy(scenario), Path(directory))
            vectors.append({"name": name, "archive": key, "scenario": scenario, "result": result,
                            "reached_campaign": bool(reached)})
    finally:
        authority_module.AuthorityOwner.create_campaign = original
    return archives, vectors


# ── run_ruleset_tests with a stand-in worker ──────────────────────────────────────────────

STAND_IN = """
import hashlib, json, sys, time
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
archive, result = value("--archive"), value("--result")
if "--hang" in args:
    time.sleep(60)
if "--echo" in args:
    with open(archive, "rb") as source:
        digest = hashlib.sha256(source.read()).hexdigest()
    with open(result, "w", encoding="utf-8") as out:
        out.write(json.dumps({"status": "passed", "archive_sha256": digest}))
payload = value("--payload")
if payload is not None:
    with open(payload, "rb") as source, open(result, "wb") as out:
        out.write(source.read())
sys.exit(int(value("--exit") or 0))
"""

LIMIT = 4 * 1024 * 1024
WORKER_CASES = [
    ("passed", {"payload": json.dumps({"status": "passed", "scenarios": [{"id": "s"}], "coverage": {}})}),
    ("every_status", {"payload": json.dumps({"status": "incomplete_coverage", "extra": [1, 2.5, None]})}),
    ("exit_code", {"payload": json.dumps({"status": "passed"}), "exit": 3}),
    ("no_result", {}),
    ("at_limit", {"payload": json.dumps({"status": "failed"}).ljust(LIMIT)}),
    ("over_limit", {"payload": json.dumps({"status": "failed"}).ljust(LIMIT + 1)}),
    ("not_utf8", {"payload_base64": base64.b64encode(b"\xff{}").decode("ascii")}),
    ("not_json", {"payload": "{"}),
    ("not_object", {"payload": "[1]"}),
    ("unknown_status", {"payload": json.dumps({"status": "unknown"})}),
    ("missing_status", {"payload": "{}"}),
    ("status_not_text", {"payload": json.dumps({"status": 1})}),
    ("status_list", {"payload": json.dumps({"status": ["passed"]})}),
    ("status_object", {"payload": json.dumps({"status": {"a": 1}})}),
    ("echo", {"echo": True}),
    ("timed_out", {"hang": True, "timeout": 0.5}),
    ("cancelled", {"hang": True, "cancel": True}),
    ("timeout_not_positive", {"timeout": 0}),
    ("archive_over_limit", {"archive_size": 128 * 1024 * 1024 + 1}),
]


def worker_vectors(archive: bytes):
    vectors = []
    with tempfile.TemporaryDirectory() as scratch:
        stand_in = Path(scratch) / "stand_in.py"
        stand_in.write_text(STAND_IN, encoding="utf-8")
        original = ruleset_testing.subprocess.Popen
        for name, case in WORKER_CASES:
            extra = []
            if "payload" in case or "payload_base64" in case:
                payload = Path(scratch) / f"{name}.payload"
                data = (case["payload"].encode("utf-8") if "payload" in case
                        else base64.b64decode(case["payload_base64"]))
                payload.write_bytes(data)
                extra += ["--payload", str(payload)]
            if "exit" in case:
                extra += ["--exit", str(case["exit"])]
            if case.get("hang"):
                extra.append("--hang")
            if case.get("echo"):
                extra.append("--echo")

            def popen(command, *args, _extra=tuple(extra), **kwargs):
                assert command[1:3] == ["-m", "sylvia_gm.interfaces.ruleset_test_worker"], command
                return original([command[0], "-I", str(stand_in), *_extra, *command[3:]], *args, **kwargs)

            ruleset_testing.subprocess.Popen = popen
            data = b"\0" * case["archive_size"] if "archive_size" in case else archive
            try:
                outcome = ruleset_testing.run_ruleset_tests(
                    io.BytesIO(data), timeout_seconds=case.get("timeout", 60.0),
                    cancelled=(lambda: True) if case.get("cancel") else None)
            except Exception as error:  # noqa: BLE001 - recorded
                outcome = raised(error)
            finally:
                ruleset_testing.subprocess.Popen = original
            recorded = {key: value for key, value in case.items() if key != "payload"}
            if "payload" in case and len(case["payload"]) > 1000:
                recorded["payload_padded"] = [case["payload"].rstrip(), len(case["payload"])]
            elif "payload" in case:
                recorded["payload"] = case["payload"]
            vectors.append({"name": name, **recorded, "outcome": outcome})
    return vectors


def main() -> None:
    checked, summaries = coverage_vectors()
    archives, scenarios = scenario_vectors()
    output = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "checked_outcomes": checked,
        "summarize_coverage": summaries,
        "assertions": assertion_vectors(),
        "dice": dice_vectors(),
        "failure_message": failure_vectors(),
        "provider": provider_vectors(),
        "archives": archives,
        "run_scenario": scenarios,
        "run_ruleset_tests": worker_vectors(base64.b64decode(archives["plain"])),
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, {key: len(value) for key, value in output.items() if isinstance(value, list)})


if __name__ == "__main__":
    main()
