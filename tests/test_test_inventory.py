"""Every executable test file belongs to the gate or the shrinking triage inventory."""

from pathlib import Path

from scripts.check_supported_journeys import PYTHON_JOURNEYS, UI_JOURNEY
from scripts.test_inventory import JAVASCRIPT_HARNESSES, UNASSIGNED_JAVASCRIPT, UNASSIGNED_PYTHON


def test_all_test_files_are_accounted_for():
    root = Path(__file__).resolve().parents[1]
    python = {path.relative_to(root).as_posix() for path in (root / "tests").glob("test_*.py")}
    javascript = {
        path.relative_to(root).as_posix()
        for path in (root / "tests").glob("*.cjs")
    }
    assigned_python = {
        path.split("::")[0] for group in PYTHON_JOURNEYS.values() for path in group
    }
    for actual, assigned, pending in (
        (python, assigned_python, UNASSIGNED_PYTHON),
        (
            javascript,
            set(UI_JOURNEY) | {
                helper for helper, owner in JAVASCRIPT_HARNESSES.items()
                if owner in assigned_python
            },
            UNASSIGNED_JAVASCRIPT,
        ),
    ):
        assert len(pending) == len(set(pending)), "Duplicate triage entries"
        assert not assigned.intersection(pending), "A test cannot be both assigned and pending"
        assert actual == assigned.union(pending), {
            "unaccounted": sorted(actual - assigned.union(pending)),
            "missing": sorted(assigned.union(pending) - actual),
        }


def test_platform_specific_cases_use_runs_on_not_a_conditional_skip():
    import re

    root = Path(__file__).resolve().parents[1]
    conditional = re.compile(r"mark\s*\.\s*skip" + "if")
    sources = [root / "conftest.py"] + [
        path for path in (root / "tests").rglob("*.py") if "node_modules" not in path.parts
    ]
    offenders = sorted(
        path.relative_to(root).as_posix()
        for path in sources
        if conditional.search(path.read_text(encoding="utf-8", errors="replace"))
    )
    assert not offenders, (
        "The local gate fails every skipped required case, so a conditional skip can only "
        "fail it. Mark platform-specific cases with @pytest.mark.runs_on(<platforms>, "
        "reason=...) instead: " + ", ".join(offenders)
    )
