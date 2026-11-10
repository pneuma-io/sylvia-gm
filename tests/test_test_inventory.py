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
