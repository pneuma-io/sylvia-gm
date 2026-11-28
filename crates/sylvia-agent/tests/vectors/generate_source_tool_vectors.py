"""Record Python's Builder source, reference, library, Setting and content tools (B6).

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_source_tool_vectors.py

Every tool is Python's own: `builder_source_tool`, `builder_setting_source_tool`,
`_builder_reference_tool`, `builder_reference_copy_tool`, `library_source_tool`,
`library_source_copy_tool`, `_campaign_setting_tool` and `_campaign_content_tools`. The owner's
objects are scripted stand-ins, built with `generate_character_tool_vectors.py`'s helpers: each
call is recorded and answered with the case's next outcome, a result or an exception of a named
class. References answer from a fixed table (text with non-ASCII and astral characters, so
paging counts code points). Each case records every answer, the callback calls and the workspace
files afterwards. Two runs give identical bytes; no recorded value holds a path.
Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from generate_character_tool_vectors import (  # noqa: E402
    Script,
    call,
    exception,
    expand,
    plain,
    read_files,

    write_files,
)

from sylvia_gm.agent import _builder_session as session  # noqa: E402
from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool  # noqa: E402
from sylvia_gm.agent._builder_source_tool import builder_setting_source_tool, builder_source_tool  # noqa: E402
from sylvia_gm.agent._library_source_copy_tool import library_source_copy_tool  # noqa: E402
from sylvia_gm.agent._library_source_tool import library_source_tool  # noqa: E402
from sylvia_gm.application.builder_references import BuilderReferences  # noqa: E402

OUTPUT = Path(__file__).with_name("source_tool_vectors.json")
NL = chr(10)
E_ACUTE, ROCKET = chr(0xE9), chr(0x1F680)


class Selection:
    def __init__(self, script):
        self.script = script

    def select(self, ruleset_id):
        return self.script.answer({"select": ruleset_id})

    def select_setting(self, *, candidate_id, setting_id):
        return self.script.answer({"select_setting": {"candidate_id": candidate_id, "setting_id": setting_id}})


class Library:
    def __init__(self, script):
        self.script = script

    def candidates(self, *, query):
        return self.script.answer({"candidates": query})

    def files(self, candidate_id):
        return self.script.answer({"files": candidate_id})

    def read(self, candidate_id, path):
        return self.script.answer({"read": [candidate_id, path]})

    def record(self, candidate_id, prefix):
        return self.script.answer({"record": [candidate_id, prefix]})


def references_of(spec, script):
    """The chat's reference source: `None`, or a reader over a fixed table."""
    def source():
        script.calls.append({"references": None})
        if spec is None:
            return None

        def read(path):
            script.calls.append({"read_reference": path})
            content = spec["contents"][path]
            if isinstance(content, dict) and "raise" in content:
                raise exception(content["raise"])
            return content
        return BuilderReferences(paths=tuple(spec["paths"]), read=read)
    return source


# ── The cases ─────────────────────────────────────────────────────────────────────────────

LONG = "Header " + E_ACUTE + ROCKET + " " + "lore " * 30 + "dragon " + "x" * 200 + " dragon end " + "y" * 9000
REFERENCES = {
    "paths": ["ruleset/runtime.yaml", "setting/World.md", "plan/Notes.md", "setting/broken.md", "setting/odd.md",
              "setting/gone.md"],
    "contents": {
        "ruleset/runtime.yaml": "rules: " + E_ACUTE + ROCKET + " dragons" + NL,
        "setting/World.md": LONG,
        "plan/Notes.md": "",
        "setting/broken.md": {"raise": {"class": "CommandRejected", "message": "Too big", "code": "package_file_oversized"}},
        "setting/odd.md": {"raise": {"class": "CommandRejected", "message": "Odd", "code": "something_else"}},
        "setting/gone.md": {"raise": {"class": "OSError", "message": "missing"}},
    },
}


def reference_calls():
    world = "setting/World.md"
    return [
        {"action": "list"},
        {"action": "list", "query": "SETTING"},
        {"action": "list", "offset": 2, "limit": 2},
        {"action": "list", "offset": 50},
        {"action": "list", "query": "q" * 121},
        {"action": "list", "offset": -1},
        {"action": "list", "limit": True},
        {"action": "list", "offset": 1.0},
        {"action": "read", "path": world},
        {"action": "read", "path": world, "offset": 7, "limit": 3},
        {"action": "read", "path": world, "offset": 9000},
        {"action": "read", "path": world, "offset": 99999},
        {"action": "read", "path": world, "limit": 40000},
        {"action": "read", "path": "plan/Notes.md"},
        {"action": "read", "path": "unknown.md"},
        {"action": "read", "path": 5},
        {"action": "delete", "path": world},
        {"action": "find", "path": world, "query": "dragon"},
        {"action": "find", "path": world, "query": "dragon", "limit": 1},
        {"action": "find", "path": world, "query": "dragon", "offset": 260},
        {"action": "find", "path": world, "query": ROCKET},
        {"action": "find", "path": world, "query": "absent"},
        {"action": "find", "path": world, "query": ""},
        {"action": "find", "path": world},
        {"action": "read", "path": "setting/broken.md"},
        {"action": "read", "path": "setting/odd.md"},
        {"action": "find", "path": "setting/gone.md", "query": "x"},
        {"action": "read_many", "pages": [{"path": world, "limit": 10}, {"path": "ruleset/runtime.yaml"}]},
        {"action": "read_many", "pages": [{"path": world, "limit": 10}, {"path": "setting/broken.md", "limit": 5},
                                          {"path": world}]},
        {"action": "read_many", "pages": []},
        {"action": "read_many", "pages": [{"path": world}] * 5},
        {"action": "read_many", "pages": [{"path": world, "limit": 20000}, {"path": world, "limit": 12001}]},
        {"action": "read_many", "pages": [{"path": world, "extra": 1}]},
        {"action": "read_many", "pages": [{"path": "unknown.md"}]},
        {"action": "read_many", "pages": ["not a page"]},
        {"action": "read_many", "pages": [{"path": world, "limit": 0}]},
        {"action": "read_many"},
    ]


COPY_FILES = {"copies/existing.md": "old text" + NL}


def copy_calls():
    return [
        {"source_path": "ruleset/runtime.yaml", "destination_path": "copies/runtime.yaml"},
        {"source_path": "ruleset/runtime.yaml", "destination_path": "copies/existing.md"},
        {"source_path": "plan/Notes.md", "destination_path": "copies/notes.md", "expected_version": None},
        {"source_path": "ruleset/runtime.yaml", "destination_path": "../outside.yaml"},
        {"source_path": "unknown.md", "destination_path": "copies/x.md"},
        {"source_path": "ruleset/runtime.yaml", "destination_path": "  "},
        {"source_path": "ruleset/runtime.yaml", "destination_path": "copies/y.md", "expected_version": 3},
        {"source_path": "ruleset/runtime.yaml", "destination_path": "copies/z.md", "extra": True},
        {"source_path": "setting/broken.md", "destination_path": "copies/b.md"},
        {"source_path": "setting/odd.md", "destination_path": "copies/o.md"},
        {"source_path": "setting/gone.md", "destination_path": "copies/g.md"},
    ]


PACKAGE = {"id": "example.rules", "version": "1.0.0", "label": "Example"}
CANDIDATE = {"candidate_id": "candidate-1", "label": "Example Rules", "kind": "ruleset"}
LIBRARY_OUTCOMES = [
    {"return": [{**CANDIDATE, "candidate_id": f"candidate-{index}"} for index in range(25)]},
    {"return": [CANDIDATE]},
    {"return": {"candidate_id": "candidate-1", "closure_sha256": "a" * 64, "package": PACKAGE,
                "paths": [f"files/{index}.md" for index in range(130)]}},
    {"return": {"candidate_id": "candidate-1", "content": LONG, "closure_sha256": "a" * 64, "package": PACKAGE}},
    {"return": {"candidate_id": "candidate-1", "content": "{}", "closure_sha256": "a" * 64}},
    {"raise": {"class": "CommandRejected", "message": "Unknown candidate", "code": "library_candidate_unknown"}},
    {"raise": {"class": "OSError", "message": "disk"}},
    {"raise": {"class": "TypeError", "message": "wrong"}},
    {"raise": {"class": "StaleRevision", "message": "old"}},
    {"return": {"candidate_id": "candidate-1"}},
    {"return": [{**CANDIDATE, "candidate_id": f"candidate-{index}"} for index in range(25)]},
]


def library_calls():
    return [
        {"action": "candidates"},
        {"action": "candidates", "query": "example", "offset": 0, "limit": 5},
        {"action": "files", "candidate_id": "candidate-1", "offset": 95},
        {"action": "read", "candidate_id": "candidate-1", "path": "files/0.md", "offset": 5, "limit": 10},
        {"action": "record", "candidate_id": "candidate-1", "path": "ruleset"},
        {"action": "read", "candidate_id": "candidate-9", "path": "x"},
        {"action": "read", "candidate_id": "candidate-1", "path": "x"},
        {"action": "read", "candidate_id": "candidate-1", "path": "x"},
        {"action": "files", "candidate_id": "candidate-1"},
        {"action": "files", "candidate_id": "candidate-1"},
        {"action": "files", "query": "x"},
        {"action": "candidates", "query": 5},
        {"action": "candidates", "query": "q" * 121},
        {"action": "files"},
        {"action": "files", "candidate_id": ""},
        {"action": "read", "candidate_id": "candidate-1"},
        {"action": "list", "candidate_id": "candidate-1", "path": "x"},
        {"action": "candidates", "offset": -2},
        {"action": "candidates", "unknown": 1},
        {"action": "candidates", "limit": 30},
    ]


LIBRARY_COPY_OUTCOMES = [
    {"return": {"content": "# Rules" + NL + E_ACUTE, "closure_sha256": "a" * 64, "package": PACKAGE}},
    {"return": {"content": json.dumps({"z": 1, "a": [E_ACUTE, {"k": None}]}), "closure_sha256": "a" * 64,
                "package": PACKAGE}},
    {"return": {"content": "new", "closure_sha256": "b" * 64, "package": PACKAGE}},
    {"return": {"content": "{not json", "closure_sha256": "a" * 64, "package": PACKAGE}},
    {"return": {"content": 5, "closure_sha256": "a" * 64, "package": PACKAGE}},
    {"return": {"content": "x", "closure_sha256": "a" * 64, "package": "not a mapping"}},
    {"raise": {"class": "CommandRejected", "message": "Unknown", "code": "library_candidate_unknown"}},
    {"raise": {"class": "KeyError", "message": "missing"}},
    {"return": {"content": "again", "closure_sha256": "a" * 64, "package": PACKAGE}},
]


def library_copy_calls():
    base = {"candidate_id": "candidate-1", "expected_closure_sha256": "a" * 64, "source_type": "file",
            "source_path": "files/0.md", "destination_path": "copies/rules.md"}
    return [
        base,
        {**base, "source_type": "editable_record", "source_path": "ruleset", "destination_path": "copies/record.json"},
        {**base, "destination_path": "copies/changed.md"},
        {**base, "source_type": "editable_record", "destination_path": "copies/bad.json"},
        {**base, "destination_path": "copies/five.md"},
        {**base, "destination_path": "copies/package.md"},
        {**base, "destination_path": "copies/unknown.md"},
        {**base, "destination_path": "copies/key.md"},
        {**base},
        {**base, "destination_path": "../out.md"},
        {**base, "source_type": "zip"},
        {**base, "expected_closure_sha256": ""},
        {**base, "expected_version": 7},
        {**base, "extra": 1},
        {key: value for key, value in base.items() if key != "source_path"},
    ]


SETTING_OUTCOMES = [
    {"return": {"package_id": "my.setting", "status": "published", "receipt": {"candidate_id": "c-1"}}},
    {"raise": {"class": "CommandRejected", "message": "Setting is invalid because " + "a" * 300,
               "code": "setting_invalid", "diagnostics": [
                   {"code": f"s{index}", "phase": "content", "message": f"problem {index}", "file": "World.md",
                    "path": "$.x", "guidance": "Fix it", "expected": "string"} for index in range(10)]}},
    {"raise": {"class": "ValueError", "message": "plain value problem"}},
    {"raise": {"class": "OSError", "message": "disk"}},
    {"raise": {"class": "StaleRevision", "message": "old"}},
    {"return": {}},
]


def setting_calls():
    good = {"package_id": "  my.setting  ", "version": " 1.0.0", "title": "My Setting "}
    return [good, good, good, good, good, {**good, "title": "   "}, {"package_id": "x", "version": "1"},
            {**good, "extra": 1}, {**good, "version": 1}, good]


CONTENT_ITEMS = [
    {"kind": "campaign_plan", "id": "plan-1", "title": "The Iron Debt", "setup_id": "setup-1",
     "summary": "A heist in Waterdeep" + E_ACUTE},
    {"kind": "campaign_plan", "id": "plan-2", "summary": "No title here", "setup_id": ""},
    {"kind": "ruleset", "id": "srd", "title": "SRD Lite", "tags": ["dragons", "STRASSE"]},
]


def content_calls():
    return [
        ["discover_campaign_content", {}],
        ["discover_campaign_content", {"query": "  iron   DEBT "}],
        ["discover_campaign_content", {"query": "stra" + chr(0xDF) + "e"}],
        ["discover_campaign_content", {"query": "waterdeep" + E_ACUTE}],
        ["discover_campaign_content", {"query": "dragons" + chr(0x2003) + "srd"}],
        ["discover_campaign_content", {"query": "nothing"}],
        ["discover_campaign_content", {"query": "q" * 161}],
        ["discover_campaign_content", {"query": 4}],
        ["discover_campaign_content", {"other": "x"}],
        ["prepare_campaign_setup", {"campaign_plan_id": "plan-1"}],
        ["prepare_campaign_setup", {"campaign_plan_id": "plan-2"}],
        ["prepare_campaign_setup", {"campaign_plan_id": "srd"}],
        ["prepare_campaign_setup", {"campaign_plan_id": ""}],
        ["prepare_campaign_setup", {"campaign_plan_id": "plan-1", "extra": 1}],
    ]


def cases():
    return [
        {"tool": "select_ruleset", "outcomes": [
            {"return": {"success": True, "status": "selected", "ruleset": {"id": "srd"}}},
            {"raise": {"class": "CommandRejected", "message": "Already bound", "code": "builder_source_bound"}},
            {"raise": {"class": "ValueError", "message": "bad"}}],
         "calls": [{"ruleset_id": "srd"}, {"ruleset_id": "other"}, {"ruleset_id": "x"}, {"ruleset_id": ""},
                   {"ruleset_id": 5}, {}, {"ruleset_id": "srd", "extra": 1}]},
        {"tool": "select_setting", "outcomes": [
            {"return": {"success": True, "status": "selected"}}, {"return": {"success": True}},
            {"raise": {"class": "CommandRejected", "message": "Mismatch", "code": "setting_mismatch"}}],
         "calls": [{"candidate_id": "c-1", "setting_id": "world"}, {"candidate_id": None, "setting_id": "world"},
                   {"candidate_id": "c-1", "setting_id": "other"}, {"candidate_id": " ", "setting_id": "world"},
                   {"candidate_id": 3, "setting_id": "world"}, {"setting_id": "world"},
                   {"candidate_id": None, "setting_id": "  "}]},
        {"tool": "references", "references": REFERENCES, "calls": reference_calls()},
        {"tool": "references", "references": None, "calls": [{"action": "list"}]},
        {"tool": "reference_copy", "references": REFERENCES, "files": COPY_FILES, "calls": copy_calls()},
        {"tool": "reference_copy", "references": None, "files": {}, "calls": [
            {"source_path": "x", "destination_path": "y"}]},
        {"tool": "library", "outcomes": LIBRARY_OUTCOMES, "calls": library_calls()},
        {"tool": "library_copy", "outcomes": LIBRARY_COPY_OUTCOMES, "files": {"copies/rules.md": "existing" + NL},
         "calls": library_copy_calls()},
        {"tool": "setting", "checking": False, "outcomes": SETTING_OUTCOMES, "calls": setting_calls()},
        {"tool": "setting", "checking": True, "outcomes": [{"return": {"package_id": "my.setting"}}],
         "calls": [{"package_id": "a", "version": "1", "title": "T"}]},
        {"tool": "content", "items": CONTENT_ITEMS, "refresh": False, "calls": content_calls()},
        {"tool": "content", "items": [], "refresh": True, "outcomes": [{"return": CONTENT_ITEMS}, {"return": []}],
         "calls": [["discover_campaign_content", {"query": "iron"}], ["prepare_campaign_setup",
                                                                      {"campaign_plan_id": "plan-1"}]]},
    ]


def run(compact):
    case = expand(compact)
    script = Script(case.get("outcomes", ()))
    kind = case["tool"]
    with tempfile.TemporaryDirectory() as scratch:
        workspace = Path(scratch)
        write_files(workspace, case.get("files", {}))
        if kind == "select_ruleset":
            tools = [builder_source_tool(Selection(script))]
        elif kind == "select_setting":
            tools = [builder_setting_source_tool(Selection(script))]
        elif kind == "references":
            tools = [session._builder_reference_tool(references_of(case["references"], script))]
        elif kind == "reference_copy":
            tools = [builder_reference_copy_tool(references_of(case["references"], script), workspace)]
        elif kind == "library":
            tools = [library_source_tool(Library(script))]
        elif kind == "library_copy":
            tools = [library_source_copy_tool(Library(script), workspace)]
        elif kind == "setting":
            def publish(**kwargs):
                return script.answer({"publish": kwargs})
            tools = [session._campaign_setting_tool(publish, checking=case["checking"])]
        else:
            def refresh():
                return tuple(script.answer({"refresh": None}))
            tools = list(session._campaign_content_tools(session.CampaignContentToolAuthority(
                items=tuple(case["items"]), refresh=refresh if case["refresh"] else None)))
        by_name = {tool.name: tool for tool in tools}
        answers = []
        for params in case["calls"]:
            if kind == "content":
                name, params = params
                answers.append({"tool": name, **call(by_name[name], params)})
            else:
                answers.append(call(tools[0], params))
        return {**compact, "answers": answers, "callback_calls": script.calls, "files_after": read_files(workspace)}


def main() -> None:
    output = plain({
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "cases": [run(case) for case in cases()],
    })
    OUTPUT.write_text(json.dumps(output, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, len(output["cases"]), "cases")


if __name__ == "__main__":
    main()
