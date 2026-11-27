"""Record Python's Builder Ruleset draft tools as ruleset_tool_vectors.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_ruleset_tool_vectors.py

Each case writes a fresh workspace (its `ruleset-draft/` files) and calls the real handler of
`ruleset_draft_check_tool(workspace)` or `ruleset_test_tool(workspace, cancelled=...)`, recording
its result or the exception it raised. The test tool's worker is replaced by the stand-in script
of `generate_ruleset_test_vectors.py`, following the protocol the Rust test's stand-in follows;
`--echo` proves the tool sends the exact materialized archive.

Two runs give identical bytes, and no recorded value holds a path, so one file serves both
platforms. Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from generate_ruleset_test_vectors import STAND_IN, raised  # noqa: E402

from sylvia_gm.agent._ruleset_draft_check_tool import ruleset_draft_check_tool  # noqa: E402
from sylvia_gm.agent._ruleset_test_tool import ruleset_test_tool  # noqa: E402
from sylvia_gm.application import ruleset_testing  # noqa: E402
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE as SOURCE  # noqa: E402
from sylvia_gm.domain._content_schema_ruleset_tests import EXAMPLE as TESTS  # noqa: E402

OUTPUT = Path(__file__).with_name("ruleset_tool_vectors.json")
NL = chr(10)
DRAFT = "ruleset-draft"
COMPACT = f"{DRAFT}/ruleset-source.yaml"


def source(**changes) -> str:
    document = {**deepcopy(SOURCE), "tests": deepcopy(TESTS), **changes}
    return yaml.safe_dump(document, sort_keys=False)


VALID = {COMPACT: source()}
WITHOUT_TESTS = {COMPACT: yaml.safe_dump({k: v for k, v in deepcopy(SOURCE).items() if k != "tests"},
                                         sort_keys=False)}
BAD_TESTS = {COMPACT: source(tests={"scenarios": [{"id": "bad"}]})}
CHECK_CASES = [
    ("arguments", VALID, {"anything": 1}),
    ("empty_workspace", {}, {}),
    ("valid", VALID, {}),
    ("valid_without_tests", WITHOUT_TESTS, {}),
    ("source_not_yaml", {COMPACT: "kind: [unclosed"}, {}),
    ("source_not_a_mapping", {COMPACT: "- 1" + NL + "- 2" + NL}, {}),
    ("invalid_tests", BAD_TESTS, {}),
    ("undeclared_field", {COMPACT: source(tools=[])}, {}),
]

PASSED = {"status": "passed", "closure_sha256": "c", "scenarios": [{"id": "coin-heads", "status": "passed"}],
          "coverage": {}}
MIXED = {"status": "failed", "message": "worker text", "scenarios": [
    {"id": "a", "status": "passed"}, {"id": "b", "status": "failed"}, {"id": "c", "status": "error"}]}
TEST_CASES = [
    ("arguments", VALID, {"x": None}, {}),
    ("empty_workspace", {}, {}, {}),
    ("invalid_tests", BAD_TESTS, {}, {}),
    ("passed", VALID, {}, {"payload": json.dumps(PASSED)}),
    ("mixed_keeps_message_place", VALID, {}, {"payload": json.dumps(MIXED)}),
    ("no_scenarios", VALID, {}, {"payload": json.dumps({"status": "missing_tests"})}),
    ("worker_failed", VALID, {}, {"payload": json.dumps(PASSED), "exit": 1}),
    ("exact_archive", VALID, {}, {"echo": True}),
    ("cancelled", VALID, {}, {"hang": True, "cancel": True}),
]


def write(workspace: Path, files: dict) -> None:
    for relative, text in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def call(handler, params):
    try:
        return handler(deepcopy(params), None)
    except Exception as error:  # noqa: BLE001 - recorded
        return raised(error)


def check_vectors():
    cases = []
    for name, files, params in CHECK_CASES:
        with tempfile.TemporaryDirectory() as scratch:
            workspace = Path(scratch)
            write(workspace, files)
            result = call(ruleset_draft_check_tool(workspace).handler, params)
        cases.append({"name": name, "files": files, "params": params, "result": result})
    return cases


def test_vectors():
    cases = []
    original = ruleset_testing.subprocess.Popen
    with tempfile.TemporaryDirectory() as tools:
        stand_in = Path(tools) / "stand_in.py"
        stand_in.write_text(STAND_IN, encoding="utf-8")
        for name, files, params, worker in TEST_CASES:
            extra = []
            if "payload" in worker:
                payload = Path(tools) / f"{name}.payload"
                payload.write_text(worker["payload"], encoding="utf-8")
                extra += ["--payload", str(payload)]
            if "exit" in worker:
                extra += ["--exit", str(worker["exit"])]
            extra += [f"--{flag}" for flag in ("hang", "echo") if worker.get(flag)]

            def popen(command, *args, _extra=tuple(extra), **kwargs):
                assert command[1:3] == ["-m", "sylvia_gm.interfaces.ruleset_test_worker"], command
                return original([command[0], "-I", str(stand_in), *_extra, *command[3:]], *args, **kwargs)

            ruleset_testing.subprocess.Popen = popen
            try:
                with tempfile.TemporaryDirectory() as scratch:
                    workspace = Path(scratch)
                    write(workspace, files)
                    tool = ruleset_test_tool(workspace, cancelled=lambda _w=worker: bool(_w.get("cancel")))
                    result = call(tool.handler, params)
            finally:
                ruleset_testing.subprocess.Popen = original
            cases.append({"name": name, "files": files, "params": params, "worker": worker, "result": result})
    return cases


def main() -> None:
    output = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "check_ruleset_draft": check_vectors(),
        "test_ruleset_draft": test_vectors(),
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, {key: len(value) for key, value in output.items() if isinstance(value, list)})


if __name__ == "__main__":
    main()
