"""Select explainable PR checks; explicit release qualification keeps the full suite."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
from pathlib import Path, PurePosixPath

if __package__:
    from .check_supported_journeys import PYTHON_JOURNEYS, UI_JOURNEY
else:
    from check_supported_journeys import PYTHON_JOURNEYS, UI_JOURNEY

CORE = [
    "tests/test_private_provider_records.py",
    "tests/test_protected_provider_settings.py",
    "tests/test_runtime_procedure_failures.py",
]
HARNESS = [
    "tests/test_ci_selection.py",
    "tests/test_ci_diagnostics.py",
    "tests/test_journey_pool.py",
    "tests/test_supported_journey_gate.py",
    "tests/test_admitted_fixture_isolation.py",
    "tests/test_journey_progress.py",
]


def reverse_test_dependents(paths, root):
    """Follow static test imports so editing an exported helper also tests its consumers."""
    reverse = {}
    for path in (root / "tests").glob("*.py"):
        module = "tests." + path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            dependencies = []
            if isinstance(node, ast.Import):
                dependencies = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = module.split(".")[: -node.level]
                    base = ".".join([*parent, *([base] if base else [])])
                dependencies = [base, *(base + "." + item.name for item in node.names)]
            for dependency in dependencies:
                if dependency.startswith("tests."):
                    reverse.setdefault(dependency, set()).add(module)
    affected = {path.removesuffix(".py").replace("/", ".") for path in paths}
    pending = list(affected)
    while pending:
        for consumer in reverse.get(pending.pop(), ()):
            if consumer not in affected:
                affected.add(consumer)
                pending.append(consumer)
    return {module.replace(".", "/") + ".py" for module in affected}


def _disjoint(groups):
    """A whole-module selector subsumes its individual selectors, across all groups."""
    modules = {item for group in groups.values() for item in group if "::" not in item}
    used = set()
    result = {}
    for name, items in groups.items():
        chosen = []
        for item in items:
            if item in used or ("::" in item and item.split("::")[0] in modules):
                continue
            used.add(item)
            chosen.append(item)
        if chosen:
            result[name] = chosen
    return result


def select_checks(paths, *, source, base, diff_complete=True, full=False, root=None):
    """Unknown inputs broaden checks; affected coverage never claims release acceptance."""
    paths = sorted(set(paths or []))
    plan = {
        "schema": 1,
        "mode": "full" if full else "affected",
        "source": source,
        "base": base,
        "diff_complete": diff_complete,
        "changed_paths": paths,
        "broad_fallback": False,
        "python": {},
        "ui": [],
        "native": False,
        "documents": [],
        "reasons": [],
    }
    groups = {}
    ui = set()

    def add(*names):
        for name in names:
            groups.setdefault(name, list(PYTHON_JOURNEYS[name]))

    def broad(path, reason):
        groups.update({name: list(items) for name, items in PYTHON_JOURNEYS.items()})
        ui.update(UI_JOURNEY)
        plan["native"] = True
        plan["broad_fallback"] = not full
        plan["reasons"].append(
            {"path": path, "selection": "all supported checks", "reason": reason}
        )

    if full:
        broad(None, "Explicit complete combined-candidate qualification")
    elif not diff_complete or not paths:
        broad(None, "Missing, incomplete or empty diff cannot select zero work")
    else:
        for path in paths:
            if (
                "\\" in path
                or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts
            ):
                broad(path, "Unrecognised path shape")
                continue
            reason = ""
            if path.startswith(".github/") or path in {
                "scripts/select_checks.py",
                "scripts/stage_journey_diagnostics.py",
                "scripts/benchmark_journeys.py",
                "scripts/journey_progress.py",
                "scripts/journey_pool.py",
                "scripts/check_supported_journeys.py",
                "scripts/journey_case_timings.json",
                "scripts/check_document_links.py",
                "tests/admitted_library_fixture.py",
                "tests/lite_fixture_archive.py",
            }:
                groups["ci-harness"] = list(HARNESS)
                reason = "Selection, fail-closed runner, diagnostics and fixture isolation"
            elif path.startswith("src-tauri/frontend/"):
                ui.update(UI_JOURNEY)
                reason = "Shipped frontend: JavaScript invariants and rendered browser journeys"
            elif path.startswith("src-tauri/") or path.startswith("scripts/build_"):
                add("release-integrity", "interruption-and-recovery")
                ui.update(UI_JOURNEY)
                plan["native"] = True
                reason = "Native ownership/storage and release assembly boundaries"
            elif path.startswith(("licenses/", "legal/")) or path in {
                "LICENSE",
                "COPYRIGHT",
                "LICENSING-STATUS.md",
                "SYLVIA-ADDITIONAL-PERMISSIONS.md",
                "dependency-notices.txt",
                "docs/launch-compatibility.md",
            }:
                add("release-integrity")
                reason = "Touched policy, exact notices, exclusions and copy"
            elif path in {"README.md", "AGENTS.md", "CONTEXT.md"} or path.startswith("docs/"):
                groups.setdefault(
                    "copy-and-links",
                    ["tests/test_sylvia_gm_identity.py", "tests/test_release_identity_gate.py"],
                )
                reason = "Documentation links and product naming"
            elif path.startswith(("rulesets/", "campaign-settings/", "campaign-plans/")):
                groups["core-invariants"] = list(CORE)
                add(
                    "bundled-content",
                    "campaign-play",
                    "builder-and-admission",
                    "interruption-and-recovery",
                )
                if path.startswith("rulesets/"):
                    add(
                        "launch-lite-compatibility",
                        "lite-admitted-qa",
                        "mechanics-and-state",
                        "generic-procedures",
                    )
                if path.endswith(".md"):
                    add("release-integrity")
                reason = "Actual package admission/closures, supported mechanics and Plan lifecycle"
            elif path.startswith("src/sylvia_gm/agent/skills/"):
                groups["core-invariants"] = list(CORE)
                add(
                    "provider-contracts",
                    "campaign-play",
                    "builder-and-admission",
                    "interruption-and-recovery",
                    "release-integrity",
                )
                reason = "Shipped agent instructions affect provider tools, gameplay and Builder"
            elif path.startswith("src/sylvia_gm/provider/"):
                groups["core-invariants"] = list(CORE)
                add("provider-contracts", "interruption-and-recovery", "campaign-play")
                reason = "Provider privacy, recovery and composed session boundaries"
            elif path.startswith("src/sylvia_gm/ruleset/"):
                groups["core-invariants"] = list(CORE)
                add(
                    "generic-procedures",
                    "mechanics-and-state",
                    "campaign-play",
                    "interruption-and-recovery",
                    "launch-lite-compatibility",
                    "lite-admitted-qa",
                )
                reason = "Shared rules execution and supported package dependents"
            elif path in {
                "src/sylvia_gm/application/bundled_packages.py",
                "src/sylvia_gm/application/bundled_campaign_plans.py",
            }:
                groups["core-invariants"] = list(CORE)
                add(
                    "bundled-content",
                    "release-integrity",
                    "campaign-play",
                    "builder-and-admission",
                    "interruption-and-recovery",
                )
                if path.endswith("bundled_packages.py"):
                    add("launch-lite-compatibility", "lite-admitted-qa")
                reason = (
                    "Shipped package/Plan materialization, exact closures and release consumers"
                )
            elif path.startswith("src/sylvia_gm/application/") and not any(
                word in path for word in ("authority", "storage", "transaction")
            ):
                groups["core-invariants"] = list(CORE)
                add(
                    "builder-and-admission",
                    "campaign-play",
                    "interruption-and-recovery",
                    "provider-contracts",
                )
                reason = "Application unit/composed journeys and durable owner boundaries"
            elif path.startswith("tests/") and path.endswith(".py"):
                if Path(path).name.startswith("test_"):
                    groups.setdefault("changed-tests", []).append(path)
                reason = "Changed Python tests and statically mapped helper consumers"
            elif path.startswith("tests/") and path.endswith((".test.cjs", ".browser.cjs")):
                ui.add(path)
                ui.update(item for item in UI_JOURNEY if item.endswith(".test.cjs"))
                reason = "Changed browser/JavaScript test and pure JavaScript invariants"
            elif path.startswith("scripts/check_"):
                add("release-integrity")
                reason = "Release CLI/source identity and exclusions"
            else:
                broad(path, "Unknown or cross-cutting dependency: conservative broad fallback")
                continue
            if path.endswith(".md"):
                plan["documents"].append(path)
            plan["reasons"].append({"path": path, "selection": reason, "reason": reason})
        test_paths = [path for path in paths if path.startswith("tests/") and path.endswith(".py")]
        if test_paths:
            try:
                affected = reverse_test_dependents(
                    test_paths, root or Path(__file__).resolve().parents[1]
                )
                consumers = [
                    item
                    for group in PYTHON_JOURNEYS.values()
                    for item in group
                    if item.split("::")[0] in affected
                ]
                if consumers:
                    groups["affected-test-consumers"] = consumers
                    plan["reasons"].append(
                        {
                            "path": test_paths,
                            "selection": consumers,
                            "reason": "Reverse import dependents of edited tests/helpers",
                        }
                    )
                elif any(not Path(path).name.startswith("test_") for path in test_paths):
                    broad(test_paths, "Shared test helper has no mapped supported consumers")
            except (OSError, SyntaxError, UnicodeError):
                broad(test_paths, "Test import graph unavailable: conservative broad fallback")
    plan["python"] = _disjoint(groups)
    plan["ui"] = sorted(ui)
    if full:
        # Preserve release selectors and their established ordering exactly.
        plan["python"] = {name: list(items) for name, items in PYTHON_JOURNEYS.items()}
        plan["ui"] = list(UI_JOURNEY)
    return plan


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def load_selection(path, *, revision, root):
    """Reject stale, empty or unsafe manifests before starting required checks."""
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("schema") != 1 or plan.get("mode") not in ("affected", "full"):
        raise ValueError("Unsupported selection manifest")
    if not revision or plan.get("source") != revision:
        raise ValueError("Selection source differs from the checked-out revision")
    groups, ui = plan.get("python"), plan.get("ui")
    if not isinstance(groups, dict) or not isinstance(ui, list) or not (groups or ui):
        raise ValueError("Selection cannot omit all work")
    for name, items in groups.items():
        if (
            not isinstance(name, str)
            or not name.replace("-", "").isalnum()
            or not isinstance(items, list)
            or not items
        ):
            raise ValueError("Invalid selected group")
    for item in [item for items in groups.values() for item in items] + ui:
        if (
            not isinstance(item, str)
            or not item.startswith("tests/")
            or "\\" in item
            or ".." in PurePosixPath(item).parts
        ):
            raise ValueError("Unsafe test selector")
        path = root / item.split("::")[0]
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Selected required test is missing or outside source")
    if any(not item.split("::")[0].endswith(".py") for items in groups.values() for item in items):
        raise ValueError("Python selection contains a non-Python test")
    if any(not item.endswith((".test.cjs", ".browser.cjs")) for item in ui):
        raise ValueError("UI selection contains a non-JavaScript test")
    if _disjoint(groups) != groups or len(ui) != len(set(ui)):
        raise ValueError("Selected checks overlap")
    if plan["mode"] == "full" and (
        groups != PYTHON_JOURNEYS or ui != UI_JOURNEY or not plan.get("native")
    ):
        raise ValueError("Full qualification cannot omit established required checks")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    source = git("rev-parse", "HEAD").decode().strip()
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
    base = event.get("pull_request", {}).get("base", {}).get("sha") or event.get("before")
    complete, paths, error = True, [], None
    try:
        if not base or set(base) == {"0"}:
            raise ValueError("No trustworthy event base")
        base = git("merge-base", base, source).decode().strip()
        raw = git("diff", "--name-only", "--no-renames", "-z", base, source)
        paths = [item.decode("utf-8", errors="strict") for item in raw.split(b"\0") if item]
    except (ValueError, OSError, subprocess.CalledProcessError, UnicodeError) as problem:
        complete, error = False, str(problem)
    plan = select_checks(paths, source=source, base=base, diff_complete=complete, full=args.full)
    plan["diff_error"] = error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(
                f"native={str(plan['native']).lower()}\nui={str(bool(plan['ui'])).lower()}\nmode={plan['mode']}\n"
            )
    print(
        json.dumps(
            {key: plan[key] for key in ("mode", "source", "base", "broad_fallback", "reasons")},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
