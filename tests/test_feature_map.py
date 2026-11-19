"""The feature map resolves: its contract sections, test selectors and gate registrations exist.

docs/testing/feature-map.json links player-facing features to the contract sections that define
them and to existing checks. A renamed or misspelled test, a moved heading, or a mapped check that
the supported-journey gate never runs fails here and says what to change.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path

import pytest
from scripts.check_supported_journeys import PYTHON_JOURNEYS, UI_JOURNEY

ROOT = Path(__file__).resolve().parents[1]
MAP = "docs/testing/feature-map.json"
PLANNED_FEATURES = (
    "Start a campaign", "Take a player action", "Resolve a player choice",
    "Stop and resume a GM reply", "Reopen a campaign", "Build and use content",
    "Arrange play panels", "Configure a provider",
)
STALE = (f"Update {MAP} to the check's current name, or remove the entry if that protection "
         "moved elsewhere.")
FEATURE_ID = re.compile(r"^[a-z]+\.[a-z]+(?:-[a-z]+)*$")


def _anchors(path: Path) -> set[str]:
    """GitHub-style anchors for every Markdown heading outside code fences."""

    anchors, fenced = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (heading := re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)):
            anchors.add(re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-"))
    return anchors


def _python_tests(path: Path) -> set[str]:
    """Test functions a selector can name: module-level, or Class::method in a Test class."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.update(f"{node.name}::{item.name}" for item in node.body
                         if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef))
    return names


def _check_problem(root: Path, selector: str, journeys: dict[str, list[str]],
                   ui: list[str]) -> str | None:
    """Why one selector does not name a check that exists and that the gate runs."""

    path_text, separator, name = selector.partition("::")
    path = root / path_text
    if not separator or not name or not path_text.startswith("tests/"):
        return "is not tests/<file>::<test name>"
    if not path.is_file():
        return f"names {path_text}, which does not exist. {STALE}"
    if path.suffix == ".py":
        if name not in _python_tests(path):
            return f"names {name}, which {path_text} does not define. {STALE}"
        gated = [item for items in journeys.values() for item in items]
        if not any(item in (path_text, selector) or item.startswith(selector + "[")
                   for item in gated):
            return ("is not run by the supported-journey gate. Register it in a group in "
                    "scripts/check_supported_journeys.py, or map a check that the gate runs.")
        return None
    if path.suffix == ".cjs":
        if path_text not in ui:
            return ("is not run by the supported-journey gate. Add it to the ui-controls group "
                    "in scripts/check_supported_journeys.py.")
        pattern = r"test\(\s*[`'\"][^`'\"]*" + re.escape(name) + r"[`'\"]"
        if not re.search(pattern, path.read_text(encoding="utf-8")):
            return f"names a test titled {name!r}, which {path_text} does not define. {STALE}"
        return None
    return "is neither a Python nor a JavaScript test file"


def feature_map_problems(
    root: Path, data: dict[str, object],
    journeys: dict[str, list[str]] = PYTHON_JOURNEYS, ui: list[str] = UI_JOURNEY,
) -> list[str]:
    """Every way the map fails to resolve, each with the entry to fix."""

    problems = []
    if data.get("schema") != 1:
        problems.append(f"{MAP} must declare schema 1")
    kinds = data.get("check_kinds")
    kinds = kinds if isinstance(kinds, dict) else {}
    features = data.get("features")
    if not isinstance(features, list) or not features:
        return [*problems, f"{MAP} must list its features"]
    seen_ids: set[object] = set()
    seen_names: set[object] = set()
    for feature in features:
        label = f"{MAP} feature {feature.get('id')!r}"
        if not isinstance(feature.get("id"), str) or not FEATURE_ID.match(feature["id"]):
            problems.append(f"{label}: an id is a lower-case area.name-with-hyphens")
        if feature.get("id") in seen_ids or feature.get("name") in seen_names:
            problems.append(f"{label}: ids and names must be unique")
        seen_ids.add(feature.get("id"))
        seen_names.add(feature.get("name"))
        status, promises = feature.get("status"), feature.get("promises", [])
        if status == "unmapped":
            if promises:
                problems.append(f"{label}: an unmapped feature lists no promises")
            continue
        if status != "mapped" or not promises:
            problems.append(f"{label}: status is mapped (with promises) or unmapped")
            continue
        for number, promise in enumerate(promises, start=1):
            where = f"{label} promise {number}"
            if not promise.get("promise") or not promise.get("contracts"):
                problems.append(f"{where}: state the promise and the contract sections it cites")
            if not promise.get("checks"):
                problems.append(f"{where}: map at least one existing check")
            for reference in promise.get("contracts", []):
                document, _, anchor = reference.partition("#")
                if not (root / document).is_file() or not document.endswith(".md"):
                    problems.append(f"{where}: {reference} names a missing document")
                elif anchor not in _anchors(root / document):
                    problems.append(
                        f"{where}: {reference} names no heading in {document}; update the "
                        "anchor to the section's current heading"
                    )
            for check in promise.get("checks", []):
                if check.get("kind") not in kinds:
                    problems.append(f"{where}: check kind {check.get('kind')!r} is not one of "
                                    f"{sorted(kinds)}")
                selector = str(check.get("selector"))
                if problem := _check_problem(root, selector, journeys, ui):
                    problems.append(f"{where}: {selector} {problem}")
    return problems


def _load() -> dict[str, object]:
    return json.loads((ROOT / MAP).read_text(encoding="utf-8"))


def test_feature_map_resolves() -> None:
    problems = feature_map_problems(ROOT, _load())
    assert not problems, "\n".join(problems)


def test_feature_map_names_the_planned_features() -> None:
    features = {feature["name"]: feature for feature in _load()["features"]}
    assert set(PLANNED_FEATURES) <= set(features)
    assert features["Stop and resume a GM reply"]["status"] == "mapped"


def _stop_and_resume(data: dict[str, object]) -> dict[str, object]:
    return next(feature for feature in data["features"]
                if feature["id"] == "play.stop-and-resume")


def _misspell_python(data: dict[str, object]) -> None:
    check = _stop_and_resume(data)["promises"][0]["checks"][1]
    check["selector"] = check["selector"].replace("manual_retry", "manual_retyr")


def _misspell_javascript(data: dict[str, object]) -> None:
    check = _stop_and_resume(data)["promises"][0]["checks"][-1]
    check["selector"] = check["selector"].replace("manual Retry", "manual Retyr")


def _ungated(data: dict[str, object]) -> None:
    _stop_and_resume(data)["promises"][1]["checks"].append({
        "kind": "owner-transport",
        "selector": "tests/test_alpha_f6e_owner_session_transport.py::"
                    "test_stop_overtakes_blocked_manual_retry_and_discards_retry_prose",
    })


def _moved_heading(data: dict[str, object]) -> None:
    contracts = _stop_and_resume(data)["promises"][0]["contracts"]
    contracts[0] = contracts[0].replace("#73-idempotency", "#73-idempotence")


def _unknown_kind(data: dict[str, object]) -> None:
    _stop_and_resume(data)["promises"][0]["checks"][0]["kind"] = "installed"


def _duplicate(data: dict[str, object]) -> None:
    data["features"].append(copy.deepcopy(data["features"][0]))


def _no_checks(data: dict[str, object]) -> None:
    _stop_and_resume(data)["promises"][2]["checks"] = []


@pytest.mark.parametrize(
    "change,expected",
    [
        (_misspell_python, "manual_retyr_narrates_stored_result_without_rerunning_mechanic, which"),
        (_misspell_javascript, "names a test titled 'manual Retyr retains"),
        (_ungated, "is not run by the supported-journey gate"),
        (_moved_heading, "#73-idempotence names no heading"),
        (_unknown_kind, "check kind 'installed' is not one of"),
        (_duplicate, "ids and names must be unique"),
        (_no_checks, "map at least one existing check"),
    ],
    ids=["python-selector", "javascript-title", "ungated", "heading", "kind", "duplicate",
         "no-checks"],
)
def test_feature_map_rejects_each_stale_entry(change, expected: str) -> None:
    data = _load()
    change(data)
    problems = feature_map_problems(ROOT, data)
    assert any(expected in line for line in problems), problems
