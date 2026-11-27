"""Generate ruleset_test_runtime_facts vectors for sylvia_app::package_runtime from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_ruleset_test_runtime_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a runtime text and a `required` list of tool names, recorded as
`interfaces/ruleset_test_worker.py` `run_archive` reads them through `_parse_runtime`:
`required_preconditions` (sorted, since Python's is a set), `requires_handoff`, or the class
of what was raised. Python iterates `required` as a `set`, so with several missing names the
one named in a `KeyError` follows string hashing; every case here misses at most one name.

The runtimes are Riichi Mahjong as shipped (kept once under `runtimes`) and the
signal-and-static fixture, as shipped and edited: `model_decision_scope` set to a full scope in place of the fixture's `model_decision_boundary`, an empty scope, a list and null
(the executable layer refuses all but the full scope and null), and every tool's
`pre_conditions` emptied, removed, or one tool given a condition. Neither shipped runtime
declares a decision scope.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import zipfile
from pathlib import Path

import yaml

from sylvia_gm.application.package_runtime import PackageRuntimeError, _parse_runtime

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "ruleset_test_runtime_vectors.json"
FIXTURE = ROOT / "tests/fixtures/rulesets/signal-and-static/runtime/ruleset.yaml"
RIICHI = ROOT / "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"
RIICHI_MEMBER = "packages/pneuma.riichi-mahjong/runtime/ruleset.yaml"

os.environ.pop("SYLVIA_RUNTIME_CACHE_DIR", None)


def riichi_runtime() -> bytes:
    with zipfile.ZipFile(RIICHI) as archive:
        return archive.read(RIICHI_MEMBER)


def dump(document) -> bytes:
    return yaml.dump(document, Dumper=yaml.CSafeDumper, sort_keys=False, allow_unicode=True).encode("utf-8")


def outcome(package_id: str, runtime_bytes: bytes, required: list[str]) -> dict:
    try:
        runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
        names = set(required)
        preconditions = {name for name in names if runtime.tools[name].pre_conditions}
        return {
            "required_preconditions": sorted(preconditions),
            "requires_handoff": runtime.model_decision_scope is not None,
        }
    except PackageRuntimeError as error:
        return {"raise": type(error).__name__}
    except Exception as error:  # the worker reports only the class
        return {"raise": type(error).__name__}


def declare_scope(document: dict) -> None:
    """The five state references of a side decision scope, replacing the boundary."""
    document.pop("model_decision_boundary", None)
    namespace = "sylvia-gm.conformance.signal-static"
    document["model_decision_scope"] = {
        field: {"namespace": namespace, "key": "state." + key}
        for field, key in {
            "decision_owner": "decision_owner",
            "active_side": "active_side",
            "eligible_actors": "eligible_actor_ids",
            "focused_actor": "focused_actor",
            "actor_entities": "entities",
        }.items()
    }


def variants(document: dict) -> list[tuple[str, dict]]:
    out = [("as shipped", document)]
    for label, change in [
        ("scope declared", declare_scope),
        ("scope empty", lambda d: (d.pop("model_decision_boundary", None), d.__setitem__("model_decision_scope", {}))),
        ("scope a list", lambda d: (d.pop("model_decision_boundary", None), d.__setitem__("model_decision_scope", []))),
        ("scope null", lambda d: d.__setitem__("model_decision_scope", None)),
    ]:
        edited = copy.deepcopy(document)
        change(edited)
        out.append((label, edited))
    tools = document.get("tools") or {}
    if isinstance(tools, dict) and tools:
        emptied = copy.deepcopy(document)
        for definition in emptied["tools"].values():
            if isinstance(definition, dict):
                definition["pre_conditions"] = []
        out.append(("no pre_conditions", emptied))
        nulled = copy.deepcopy(document)
        for definition in nulled["tools"].values():
            if isinstance(definition, dict):
                definition.pop("pre_conditions", None)
        out.append(("pre_conditions missing", nulled))
        first = next(iter(tools))
        one = copy.deepcopy(emptied)
        one["tools"][first]["pre_conditions"] = [{"condition": "true", "error": "Never refused"}]
        out.append(("one pre_condition", one))
    return out


def required_sets(tool_names: list[str]) -> list[tuple[str, list[str]]]:
    return [
        ("every tool", tool_names),
        ("none", []),
        ("the first two", tool_names[:2]),
        ("one missing", tool_names[:1] + ["not_a_declared_tool"]),
    ]


def main() -> int:
    cases = []
    riichi = riichi_runtime()
    names = [str(name) for name in yaml.load(riichi.decode("utf-8"), Loader=yaml.CSafeLoader)["tools"]]
    for required_label, required in required_sets(names):
        cases.append({
            "package_id": "pneuma.riichi-mahjong",
            "label": f"Riichi as shipped; {required_label}",
            "runtime_ref": "riichi",
            "required": required,
            "expected": outcome("pneuma.riichi-mahjong", riichi, required),
        })
    shipped = FIXTURE.read_bytes()
    document = yaml.load(shipped.decode("utf-8"), Loader=yaml.CSafeLoader)
    names = [str(name) for name in (document.get("tools") or {})]
    for label, variant in variants(document):
        runtime_bytes = shipped if label == "as shipped" else dump(variant)
        for required_label, required in required_sets(names):
            cases.append({
                "package_id": "sylvia-gm.conformance.signal-static",
                "label": f"fixture {label}; {required_label}",
                "runtime": runtime_bytes.decode("utf-8"),
                "required": required,
                "expected": outcome("sylvia-gm.conformance.signal-static", runtime_bytes, required),
            })
    OUTPUT.write_text(json.dumps({"runtimes": {"riichi": riichi.decode("utf-8")}, "cases": cases}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(cases)} cases -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
