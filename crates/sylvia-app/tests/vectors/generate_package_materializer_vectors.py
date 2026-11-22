"""Generate reference vectors for sylvia_app::package_materializer (builder/_package_materializer.py).

Run with the snapshot's own interpreter, from the repository root, on Windows or Linux:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_package_materializer_vectors.py
    .venv/bin/python -I crates/sylvia-app/tests/vectors/generate_package_materializer_vectors.py

The output is the same on both (a link is a junction on Windows and a symbolic link elsewhere;
no case needs a case-sensitive file system: the colliding names are `ß` and `ss`, which
casefold alike), so one file serves both, and rerunning on either must leave it unchanged.

Each script runs in a fresh workspace: file steps set up the Builder's `ruleset-draft/`, then
`materialize`, `export` and `install_id` steps record their outcomes. A materialized archive is
recorded by its SHA-256, length and closure; a failure by its class, text, diagnostics and code.
File contents are written from `bases`: the small Ruleset source of the ruleset-source vectors,
its compiled descriptor, and its compiled payloads.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from generate_ruleset_source_vectors import RIICHI, SMALL  # noqa: E402

from sylvia_gm.builder._package_materializer import (  # noqa: E402
    export_ruleset_candidate,
    materialize_ruleset_draft,
    ruleset_install_id,
)
from sylvia_gm.builder._ruleset_source import compile_ruleset_source  # noqa: E402

OUTPUT = Path(__file__).with_name("package_materializer_vectors.json")
NEWLINE = chr(10)
DRAFT = "ruleset-draft"

SMALL_SOURCE = json.dumps(SMALL)
COMPILED = compile_ruleset_source(SMALL_SOURCE.encode("utf-8")).draft
DESCRIPTOR = {key: value for key, value in COMPILED.record.items() if key != "content_kind"}
PAYLOADS = {path: data.decode("utf-8") for path, data in COMPILED.payloads.items()}


def content(spec) -> bytes:
    if "text" in spec:
        return spec["text"].encode("utf-8")
    if "descriptor" in spec:
        return json.dumps(apply(DESCRIPTOR, spec["descriptor"])).encode("utf-8")
    if "payload" in spec:
        return PAYLOADS[spec["payload"]].encode("utf-8")
    if "riichi" in spec:
        return (ROOT / RIICHI).read_bytes()
    if "small" in spec:
        return json.dumps(apply(SMALL, spec["small"])).encode("utf-8")
    if "latin1" in spec:
        return spec["latin1"].encode("latin-1")
    text, count = spec["repeat"]
    return (text * count).encode("utf-8")


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


def make_link(path: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(path))
    else:
        os.symlink(target, path, target_is_directory=True)


def error_outcome(error: Exception) -> list:
    diagnostics = getattr(error, "diagnostics", None) or ()
    return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics],
            getattr(error, "code", None)]


def run(base: Path, steps: list) -> list:
    workspace = base / "workspace"
    workspace.mkdir()
    last = None
    recorded = []
    for step in steps:
        op = step["op"]
        outcome = None
        path = workspace / step["path"] if "path" in step else None
        if op == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content(step["content"]))
        elif op == "payloads":
            for name, data in PAYLOADS.items():
                if name not in step.get("skip", []):
                    target = path / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data.encode("utf-8"))
        elif op == "mkdir":
            path.mkdir(parents=True, exist_ok=True)
        elif op == "remove":
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
        elif op == "link":
            path.parent.mkdir(parents=True, exist_ok=True)
            make_link(path, workspace / step["target"])
        elif op == "fill":
            path.mkdir(parents=True, exist_ok=True)
            for index in range(step["count"]):
                if step.get("directories"):
                    (path / f"d{index:04d}").mkdir()
                else:
                    (path / f"f{index:04d}.txt").write_bytes(b"x")
        elif op == "materialize":
            try:
                last = materialize_ruleset_draft(workspace)
                last.archive.seek(0)
                data = last.archive.read()
                last.archive.seek(0)
                outcome = ["ok", hashlib.sha256(data).hexdigest(), len(data), last.closure_sha256]
            except Exception as error:  # every class is behaviour
                last, outcome = None, error_outcome(error)
        elif op == "export" and last is None:
            outcome = ["skipped"]
        elif op == "export":
            try:
                outcome = ["ok", export_ruleset_candidate(workspace, last)]
            except Exception as error:
                outcome = error_outcome(error)
        else:
            raise AssertionError(op)
        recorded.append({**step, "outcome": outcome})
    return recorded


def scripts() -> dict:
    source = f"{DRAFT}/ruleset-source.yaml"
    descriptor = f"{DRAFT}/package.json"
    files = f"{DRAFT}/files"
    draft = [{"op": "write", "path": descriptor, "content": {"descriptor": []}}, {"op": "payloads", "path": files}]

    def descriptor_case(edits):
        return [{"op": "write", "path": descriptor, "content": {"descriptor": edits}}, {"op": "payloads", "path": files},
                {"op": "materialize"}]

    raw_descriptor = json.dumps(DESCRIPTOR)
    result = {
        "compact": [{"op": "write", "path": source, "content": {"small": []}}, {"op": "materialize"}, {"op": "export"},
                    {"op": "export"}, {"op": "write", "path": f"{DRAFT}/output/x.txt", "content": {"text": "x"}},
                    {"op": "materialize"}, {"op": "export"}],
        "riichi": [{"op": "write", "path": source, "content": {"riichi": True}}, {"op": "materialize"}, {"op": "export"}],
        "compact_invalid": [{"op": "write", "path": source, "content": {"text": "{"}}, {"op": "materialize"}],
        "compact_value_error": [{"op": "write", "path": source, "content": {"small": [
            ["set", ["public_actions", 0, "configure", "amount"], [1]]]}}, {"op": "materialize"}],
        "compact_too_large": [{"op": "write", "path": source, "content": {"repeat": ["#", 2 * 1024 * 1024 + 1]}},
                              {"op": "materialize"}],
        "compact_beside_descriptor": [*draft, {"op": "write", "path": source, "content": {"small": []}},
                                      {"op": "materialize"}],
        "descriptor": [*draft, {"op": "materialize"}, {"op": "export"}],
        "no_draft": [{"op": "materialize"}],
        "no_descriptor": [{"op": "mkdir", "path": DRAFT}, {"op": "materialize"}],
        "no_files": [{"op": "write", "path": descriptor, "content": {"descriptor": []}}, {"op": "materialize"}],
        "empty_files": [{"op": "write", "path": descriptor, "content": {"descriptor": []}},
                        {"op": "mkdir", "path": files}, {"op": "materialize"}],
        "no_runtime": [{"op": "write", "path": descriptor, "content": {"descriptor": []}},
                       {"op": "payloads", "path": files, "skip": ["runtime/ruleset.yaml"]}, {"op": "materialize"}],
        "no_declarations": [{"op": "write", "path": descriptor, "content": {"descriptor": []}},
                            {"op": "payloads", "path": files, "skip": ["content/content-declarations.json"]},
                            {"op": "materialize"}],
        "declarations_not_text": descriptor_case([["set", ["content_declarations"], 5]]),
        "descriptor_role": descriptor_case([["set", ["role"], "dependency"]]),
        "descriptor_extra": descriptor_case([["set", ["extra"], 1]]),
        "descriptor_missing": descriptor_case([["delete", ["tools"], None]]),
        "descriptor_no_state": descriptor_case([["delete", ["state_schemas"], None]]),
        "descriptor_schema_hash": descriptor_case([["set", ["participant_artifacts", 0, "schema_sha256"], "0" * 64]]),
        "descriptor_list": [{"op": "write", "path": descriptor, "content": {"text": "[]"}}, {"op": "payloads", "path": files},
                            {"op": "materialize"}],
        "descriptor_duplicate": [{"op": "write", "path": descriptor,
                                  "content": {"text": raw_descriptor[:-1] + ', "role": "primary"}'}},
                                 {"op": "payloads", "path": files}, {"op": "materialize"}],
        "descriptor_nan": [{"op": "write", "path": descriptor, "content": {"text": raw_descriptor.replace(
            '"host_contract": 1', '"host_contract": NaN')}}, {"op": "payloads", "path": files}, {"op": "materialize"}],
        "descriptor_overflow": [{"op": "write", "path": descriptor, "content": {"text": raw_descriptor.replace(
            '"host_contract": 1', '"host_contract": 1e999')}}, {"op": "payloads", "path": files}, {"op": "materialize"}],
        "descriptor_deep": descriptor_case([["set", ["metadata", "deep"], json.loads("[" * 24 + "]" * 24)]]),
        "descriptor_deep_ok": descriptor_case([["set", ["metadata", "deep"], json.loads("[" * 21 + "]" * 21)]]),
        "descriptor_latin1": [{"op": "write", "path": descriptor, "content": {"latin1": "{" + chr(0xff) + "}"}},
                              {"op": "payloads", "path": files}, {"op": "materialize"}],
        "descriptor_too_large": [{"op": "write", "path": descriptor, "content": {"repeat": [" ", 2 * 1024 * 1024 + 1]}},
                                 {"op": "payloads", "path": files}, {"op": "materialize"}],
        "scenarios_invalid": [*draft, {"op": "write", "path": f"{files}/tests/scenarios.json", "content": {"text": "{}"}},
                              {"op": "materialize"}],
        "scenarios_not_json": [*draft, {"op": "write", "path": f"{files}/tests/scenarios.json", "content": {"text": "{"}},
                               {"op": "materialize"}],
        "collision": [*draft, {"op": "write", "path": f"{files}/notes/" + chr(0xdf) + ".txt", "content": {"text": "a"}},
                      {"op": "write", "path": f"{files}/notes/ss.txt", "content": {"text": "b"}}, {"op": "materialize"}],
        "too_deep": [*draft, {"op": "write", "path": f"{files}/" + "/".join("d" for _ in range(12)) + "/x.txt",
                              "content": {"text": "x"}}, {"op": "materialize"}],
        "deep_ok": [*draft, {"op": "write", "path": f"{files}/" + "/".join("d" for _ in range(11)) + "/x.txt",
                             "content": {"text": "x"}}, {"op": "materialize"}],
        "whitespace_name": [*draft, {"op": "write", "path": f"{files}/ lead.txt", "content": {"text": "x"}},
                            {"op": "materialize"}],
        "too_many_files": [*draft, {"op": "fill", "path": f"{files}/many", "count": 253}, {"op": "materialize"}],
        "files_at_limit": [*draft, {"op": "fill", "path": f"{files}/many", "count": 252}, {"op": "materialize"}],
        "too_many_entries": [*draft, {"op": "fill", "path": f"{files}/dirs", "count": 1020, "directories": True},
                             {"op": "materialize"}],
        "linked_entry": [*draft, {"op": "mkdir", "path": "elsewhere"},
                         {"op": "write", "path": "elsewhere/x.txt", "content": {"text": "x"}},
                         {"op": "link", "path": f"{files}/linked", "target": "elsewhere"}, {"op": "materialize"}],
        "linked_files": [{"op": "write", "path": descriptor, "content": {"descriptor": []}},
                         {"op": "payloads", "path": "elsewhere"}, {"op": "link", "path": files, "target": "elsewhere"},
                         {"op": "materialize"}],
        "linked_draft": [{"op": "write", "path": "elsewhere/package.json", "content": {"descriptor": []}},
                         {"op": "payloads", "path": "elsewhere/files"}, {"op": "link", "path": DRAFT, "target": "elsewhere"},
                         {"op": "materialize"}],
        "linked_output": [{"op": "write", "path": source, "content": {"small": []}}, {"op": "materialize"},
                          {"op": "mkdir", "path": "elsewhere"},
                          {"op": "link", "path": f"{DRAFT}/output", "target": "elsewhere"}, {"op": "export"}],
        "output_is_file": [{"op": "write", "path": source, "content": {"small": []}}, {"op": "materialize"},
                           {"op": "write", "path": f"{DRAFT}/output", "content": {"text": "x"}}, {"op": "export"}],
    }
    return result


def main() -> None:
    results = {}
    for name, steps in scripts().items():
        base = Path(tempfile.mkdtemp(prefix="sylvia-materializer-")).resolve()
        try:
            results[name] = run(base, steps)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    install_ids = [[session, closure, ruleset_install_id(session, closure)]
                   for session, closure in (("s", "c"), ("", ""), ("session-1", "a" * 64), (chr(0xe9), chr(0x1f600)))]
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "riichi": RIICHI, "small": SMALL,
               "descriptor": DESCRIPTOR, "payloads": PAYLOADS, "scripts": results, "install_ids": install_ids}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NEWLINE
    for marker in (tempfile.gettempdir(), os.path.expanduser("~")):
        assert marker not in text and marker.replace(chr(92), "/") not in text, "a machine path leaked"
    OUTPUT.write_text(text, encoding="utf-8", newline=NEWLINE)
    for name, steps in results.items():
        print(f"{name:28}", [step["outcome"][0] if step["outcome"][0] == "ok" else
                             (step["outcome"][3] or step["outcome"][0]) for step in steps if step["outcome"]])


if __name__ == "__main__":
    main()
