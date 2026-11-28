"""Record Python's Ruleset test worker (`interfaces/ruleset_test_worker.py`) as ruleset_worker_vectors.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_ruleset_worker_vectors.py

Each case runs the worker's `main(["--archive", A, "--result", R])` and records the result file's
exact text. The archives are the example Ruleset (`_content_schema_ruleset.EXAMPLE`) compiled
without saved tests, with the example suite (`_content_schema_ruleset_tests.EXAMPLE`), and with
the suite and a declared `model_decision_scope`; bytes that are not an archive; and a missing
archive file.

The Rust port's `run_scenario` stops at `create_campaign` for now (lane 1's B4a): a scenario
that reaches it answers `{"id", "status": "failed", "steps": [], "failures": [{"message":
"CommandRejected (rust_port_pending)"}]}`. So each case records two texts: `python`, the real
result, and `port`, the same worker with `run_scenario` answering that for every scenario that
reaches `create_campaign`. The Rust test checks `port`; once turns are ported it checks
`python`.

Two runs give identical bytes. Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from sylvia_gm.application import authority as authority_module  # noqa: E402
from sylvia_gm.application.package_materialization import materialize_package_archive  # noqa: E402
from sylvia_gm.builder._ruleset_source import compile_ruleset_source  # noqa: E402
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE as SOURCE  # noqa: E402
from sylvia_gm.domain._content_schema_ruleset_tests import EXAMPLE as SUITE  # noqa: E402
from sylvia_gm.interfaces import ruleset_test_worker as worker  # noqa: E402

OUTPUT = Path(__file__).with_name("ruleset_worker_vectors.json")


def archive(tests: bool, scope: bool = False) -> bytes:
    source = deepcopy(SOURCE)
    if tests:
        source["tests"] = deepcopy(SUITE)
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    draft = compiled.draft
    if scope:
        payloads = dict(draft.payloads)
        runtime = yaml.safe_load(payloads["runtime/ruleset.yaml"])
        runtime.pop("model_decision_boundary", None)
        runtime["model_decision_scope"] = {
            field: {"namespace": SOURCE["id"], "key": "state." + key}
            for field, key in {
                "decision_owner": "decision_owner",
                "active_side": "active_side",
                "eligible_actors": "eligible_actor_ids",
                "focused_actor": "focused_actor",
                "actor_entities": "entities",
            }.items()
        }
        payloads["runtime/ruleset.yaml"] = yaml.safe_dump(runtime).encode()
        draft = replace(draft, payloads=payloads)
    with materialize_package_archive((draft,)) as candidate:
        candidate.archive.seek(0)
        return candidate.archive.read()


ORIGINAL_RUN_SCENARIO = worker.run_scenario


def port_run_scenario(archive_bytes, scenario, directory):
    """`run_scenario`, answering as the Rust port does once a scenario reaches `create_campaign`."""
    reached = []
    original = authority_module.AuthorityOwner.create_campaign

    def create_campaign(self, command):
        reached.append(True)
        return original(self, command)

    authority_module.AuthorityOwner.create_campaign = create_campaign
    try:
        result = ORIGINAL_RUN_SCENARIO(archive_bytes, scenario, directory)
    finally:
        authority_module.AuthorityOwner.create_campaign = original
    if reached:
        return {"id": result["id"], "status": "failed", "steps": [],
                "failures": [{"message": "CommandRejected (rust_port_pending)"}]}
    return result


def run(source: bytes | None) -> str:
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        archive_path = folder / "candidate.sylvia-gm-package"
        if source is not None:
            archive_path.write_bytes(source)
        result = folder / "result.json"
        assert worker.main(["--archive", str(archive_path), "--result", str(result)]) == 0
        leftovers = sorted(path.name for path in folder.iterdir() if path.name not in {archive_path.name, result.name})
        assert not leftovers, leftovers
        return result.read_text(encoding="utf-8")


def main() -> None:
    cases = [
        ("no_tests", archive(tests=False)),
        ("example_suite", archive(tests=True)),
        ("example_suite_scope", archive(tests=True, scope=True)),
        ("not_an_archive", b"not a package"),
        ("missing_file", None),
    ]
    vectors = []
    for name, source in cases:
        worker.run_scenario = ORIGINAL_RUN_SCENARIO
        python = run(source)
        worker.run_scenario = port_run_scenario
        port = run(source)
        worker.run_scenario = ORIGINAL_RUN_SCENARIO
        vectors.append({
            "name": name,
            "archive_base64": None if source is None else base64.b64encode(source).decode("ascii"),
            "python": python,
            "port": port,
        })
    OUTPUT.write_text(json.dumps({"cases": vectors}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(vectors)} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
