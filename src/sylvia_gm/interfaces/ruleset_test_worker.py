"""Dedicated local worker for disposable Ruleset scenarios, without application credentials."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
from pathlib import Path

from sylvia_gm.agent._ruleset_test_coverage import summarize_coverage
from sylvia_gm.agent._ruleset_test_runner import run_scenario
from sylvia_gm.application.package_runtime import PACKAGE_RUNTIME_PATH, _parse_runtime
from sylvia_gm.domain._content_schema_ruleset_tests import RULESET_TESTS_PATH
from sylvia_gm.domain.package_library import closure_sha256
from sylvia_gm.domain.ruleset_tests import parse_ruleset_tests
from sylvia_gm.infrastructure.package_archive import admit_package_archive


def run_archive(archive: bytes, root: Path) -> dict:
    """Admit the exact package and run its own saved cases in fresh campaign stores."""
    with admit_package_archive(io.BytesIO(archive)) as admitted:
        primary = next(
            manifest for manifest in admitted.manifests if manifest.role.value == "primary"
        )
        files = [
            item
            for item in admitted.files
            if item.manifest.package_id == primary.package_id
            and item.file.path == RULESET_TESTS_PATH
        ]
        if len(files) != 1:
            return {
                "status": "missing_tests",
                "scenarios": [],
                "message": "The Ruleset has no saved test scenarios.",
            }
        suite = parse_ruleset_tests(b"".join(admitted.iter_file_chunks(files[0])))
        identity = closure_sha256(admitted.package_lock)
        required = {tool.name for tool in primary.tools}
        required_rejections = {
            tool.name for tool in primary.tools if tool.input_schema.get("properties")
        }
        runtime_file = next(
            item for item in admitted.files
            if item.manifest.package_id == primary.package_id
            and item.file.path == PACKAGE_RUNTIME_PATH
        )
        runtime = _parse_runtime(
            package_id=primary.package_id,
            runtime_bytes=b"".join(admitted.iter_file_chunks(runtime_file)),
        )
        required_preconditions = {
            name for name in required if runtime.tools[name].pre_conditions
        }
        requires_handoff = runtime.model_decision_scope is not None
    results = []
    for scenario in suite["scenarios"]:
        with tempfile.TemporaryDirectory(prefix="scenario-", dir=root) as temporary:
            results.append(run_scenario(archive, scenario, Path(temporary)))
    coverage = summarize_coverage(
        required, results, suite["scenarios"], required_rejections=required_rejections,
        required_preconditions=required_preconditions, requires_handoff=requires_handoff,
    )
    status = "passed" if all(item["status"] == "passed" for item in results) else "failed"
    if status == "passed" and any(coverage[key] for key in (
        "missing_actions", "unproven_claims", "missing_rejections", "missing_interaction",
        "missing_preconditions", "missing_handoff",
    )):
        status = "incomplete_coverage"
    return {
        "status": status,
        "closure_sha256": identity,
        "scenarios": results,
        "coverage": coverage,
    }


def main(argv: list[str] | None = None) -> int:
    """Write only structured scenario results; raw execution diagnostics stay out of the UI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.archive.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("Scenario package exceeds limit")
        result = run_archive(args.archive.read_bytes(), args.result.parent)
    except Exception as error:
        result = {"status": "error", "scenarios": [], "error_type": type(error).__name__}
    args.result.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
