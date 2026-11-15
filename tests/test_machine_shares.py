"""One complete run split across machines: the plan, a share's receipt, and the merge."""

import importlib.util
import json
import random
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from scripts import journey_pool, merge_machine_shares

from tests.runner_checkouts import install_runs_on_plugin

REPOSITORY = Path(__file__).resolve().parents[1]


def _load_gate():
    path = REPOSITORY / "scripts/check_supported_journeys.py"
    spec = importlib.util.spec_from_file_location("qa_supported_gate_shares", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate():
    return _load_gate()


@pytest.mark.parametrize(("text", "expected"), [("1/1", (1, 1)), ("2/6", (2, 6)), ("64/64", (64, 64))])
def test_a_share_is_written_as_index_over_count(text, expected):
    assert journey_pool.parse_machine_share(text) == expected


@pytest.mark.parametrize(
    "text", ["", "2", "2/", "/6", "0/6", "7/6", "1/0", "1/65", "-1/6", "a/b", "1/2/3", "1.0/2", " 1/2", "²/6"]
)
def test_a_malformed_or_out_of_range_share_is_refused(text):
    with pytest.raises(ValueError, match="machine share"):
        journey_pool.parse_machine_share(text)


def _tasks():
    tasks = [("slow", f"tests/test_slow.py::test_case[{index}]") for index in range(7)]
    tasks += [("fast", f"tests/test_fast.py::test_case_{index}") for index in range(40)]
    timings = {journey_pool.case_key(nodeid): 600.0 + index for index, (_g, nodeid) in enumerate(tasks[:7])}
    timings[journey_pool.case_key("tests/test_fast.py::test_case_3")] = 45.0
    return tasks, timings


@pytest.mark.parametrize("count", [1, 2, 3, 6, 8])
def test_the_plan_depends_only_on_the_cases_and_puts_every_item_in_one_share(count):
    tasks, timings = _tasks()
    plan = journey_pool.machine_share_plan(tasks, timings, count, include_ui=True)
    shuffled = list(tasks)
    random.Random(7).shuffle(shuffled)
    assert journey_pool.machine_share_plan(shuffled, timings, count, include_ui=True) == plan
    assigned = [task for share in plan for task in share["tasks"]]
    assert sorted(assigned) == sorted(tasks) and len(set(assigned)) == len(tasks)
    assert [share["ui_controls"] for share in plan].count(True) == 1
    without_ui = journey_pool.machine_share_plan(tasks, timings, count, include_ui=False)
    assert not any(share["ui_controls"] for share in without_ui)


def test_the_plan_uses_the_existing_balancing_rule_and_reports_each_share_s_longest_case():
    tasks, timings = _tasks()
    plan = journey_pool.machine_share_plan(tasks, timings, 3, include_ui=True)
    items = [*sorted(tasks, key=lambda task: (task[1], task[0])), journey_pool.UI_SHARE_ITEM]
    estimates = {**timings, journey_pool.case_key(journey_pool.UI_SHARE_ITEM[1]): 300.0}
    buckets, loads = journey_pool.balanced_assignments(items, 3, estimates)
    assert [share["tasks"] for share in plan] == [
        [task for task in bucket if task != journey_pool.UI_SHARE_ITEM] for bucket in buckets
    ]
    assert [share["estimated_seconds"] for share in plan] == [round(load, 3) for load in loads]
    assert max(loads) - min(loads) <= 606.0, "no share exceeds another by more than one case"
    assert sorted(share["longest_case_seconds"] for share in plan)[-1] == 606.0


def _checkout(root):
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    install_runs_on_plugin(root)
    tests = root / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_one.py").write_text(
        "import pytest\n\n@pytest.mark.parametrize('value', range(5))\n"
        "def test_one(value):\n    assert value >= 0\n"
    )
    (tests / "test_two.py").write_text("def test_two():\n    assert 2 + 2 == 4\n")
    return {"one": ["tests/test_one.py"], "two": ["tests/test_two.py"]}


def _run_share(root, journeys, destination, share, ui_calls):
    destination.mkdir()

    def run_ui(name, output):
        ui_calls.append((share, name))
        return True, 1.0

    report = {}
    results = journey_pool.run_balanced_gate(
        root, destination, journeys, ["one", "two", "ui-controls"], 2, 0.0, run_ui,
        machine_share=share, share_report=report,
    )
    return results, report


def test_each_machine_runs_only_its_share_and_the_shares_cover_the_run_once(tmp_path, monkeypatch):
    root = tmp_path / "source"
    journeys = _checkout(root)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    ui_calls = []
    ran = []
    digests = set()
    for index in (1, 2, 3):
        destination = tmp_path / f"share-{index}"
        results, report = _run_share(root, journeys, destination, (index, 3), ui_calls)
        assert all(passed for passed, _seconds in results.values()), results
        plan = json.loads((destination / "machine-share.json").read_text())
        assert (plan["index"], plan["count"]) == (index, 3) == (report["index"], report["count"])
        assert len(plan["required_cases"]) == 6 == report["total_required_cases"]
        assert report["assigned_cases"] == len(plan["assigned_cases"])
        digests.add(plan["required_cases_sha256"])
        executed = sorted(
            f"{case.get('classname')}::{case.get('name')}"
            for name in results if name != "ui-controls"
            for case in ET.parse(destination / f"{name}.xml").iter("testcase")
        )
        assert executed == sorted(journey_pool.case_key(nodeid) for nodeid in plan["assigned_cases"])
        ran += plan["assigned_cases"]
        # A group with no case here is named as absent, and is not a failed or empty group.
        assert set(results) | set(plan["absent_groups"]) == {"one", "two", "ui-controls"}
        assert not set(results) & set(plan["absent_groups"])
        assert ("ui-controls" in results) is plan["ui_controls_included"]
    assert len(digests) == 1
    assert sorted(ran) == sorted(
        [f"tests/test_one.py::test_one[{value}]" for value in range(5)] + ["tests/test_two.py::test_two"]
    )
    assert [name for _share, name in ui_calls] == ["ui-controls"]


def test_the_plan_can_be_computed_without_running_anything(tmp_path, monkeypatch):
    root = tmp_path / "source"
    journeys = _checkout(root)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    destination = tmp_path / "plan"
    destination.mkdir()
    report = {}
    results = journey_pool.run_balanced_gate(
        root, destination, journeys, ["one", "two", "ui-controls"], 4, 0.0,
        lambda *args: pytest.fail("the browser tests must not start"),
        machine_share=(1, 4), share_report=report, plan_only=True,
    )
    assert results == {}
    assert report["count"] == 4 and report["total_required_cases"] == 6
    assert sum(share["cases"] for share in report["shares"]) == 6
    assert [share["ui_controls"] for share in report["shares"]].count(True) == 1
    assert not (destination / "attempts").exists() and not list(destination.glob("*.xml"))


def _real_runner(gate, monkeypatch, root, journeys):
    """The real runner and pool on a synthetic checkout; only the browser tests are replaced."""
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(gate, "PYTHON_JOURNEYS", journeys)
    monkeypatch.setattr(gate, "prepare_candidate", lambda environment, source, destination: {})
    monkeypatch.setattr(
        gate, "source_identity", lambda: {"sha256": "same", "head": "abc123", "files": {}}
    )
    monkeypatch.setattr(gate, "_run_guarded", lambda name, output, **kwargs: (True, 1.0))
    monkeypatch.setitem(sys.modules, "journey_pool", journey_pool)


def _main(gate, monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["gate", *map(str, arguments)])
    return gate.main()


@pytest.fixture(scope="module")
def real_runs(tmp_path_factory):
    """Three real share runs and one ordinary run of the same synthetic checkout, made once."""
    base = tmp_path_factory.mktemp("machine-shares")
    gate = _load_gate()
    with pytest.MonkeyPatch.context() as monkeypatch:
        root = base / "source"
        _real_runner(gate, monkeypatch, root, _checkout(root))
        folders = []
        for index in (1, 2, 3):
            folder = base / f"share-{index}"
            assert _main(
                gate, monkeypatch, "--output-dir", folder, "--machine-share", f"{index}/3"
            ) == 0
            folders.append(folder)
        whole = base / "whole"
        assert _main(gate, monkeypatch, "--output-dir", whole) == 0
    return folders, whole


@pytest.fixture
def share_folders(real_runs, tmp_path):
    """Private copies of the receipts, so a test may damage them."""
    folders, whole = real_runs
    copies = []
    for folder in folders:
        copy = tmp_path / "shares" / folder.name
        shutil.copytree(folder, copy, ignore=shutil.ignore_patterns("attempts", "temporary"))
        copies.append(copy)
    return copies, whole


def test_a_share_receipt_says_what_it_is_and_an_ordinary_receipt_is_unchanged(share_folders):
    folders, whole = share_folders
    ordinary = json.loads((whole / "summary.json").read_text())
    assert ordinary["machine_share"] is None
    assert ordinary["complete_gate"] is True and ordinary["gate_kind"] == "complete deterministic"
    assert not (whole / "machine-share.json").exists()
    included = 0
    for index, folder in enumerate(folders, start=1):
        receipt = json.loads((folder / "summary.json").read_text())
        share = receipt["machine_share"]
        assert receipt["passed"] is True and receipt["complete_gate"] is False
        assert receipt["gate_kind"] == f"machine share {index} of 3 of the complete deterministic gate"
        assert (share["index"], share["count"], share["total_required_cases"]) == (index, 3, 6)
        assert receipt["missing_groups"] == [] and receipt["unexpected_groups"] == []
        assert set(receipt["groups"]) | set(share["groups_not_in_this_share"]) == {
            "one", "two", "ui-controls"
        }
        assert receipt["python_case_counts"]["passed"] == share["assigned_cases"]
        included += share["ui_controls_included"]
        for field in ("revision", "source_sha256", "platform", "unchanged_source"):
            assert receipt[field] == ordinary[field]
    assert included == 1


def test_merged_shares_equal_one_ordinary_run(share_folders, tmp_path, capsys):
    folders, whole = share_folders
    output = tmp_path / "merged.json"
    assert merge_machine_shares.main([*map(str, reversed(folders)), "--output", str(output)]) == 0
    merged = json.loads(output.read_text())
    ordinary = json.loads((whole / "summary.json").read_text())
    assert merged["passed"] is True and merged["complete_gate"] is True
    assert merged["gate_kind"] == "complete deterministic, assembled from 3 machine shares"
    assert merged["python_case_counts"] == ordinary["python_case_counts"]
    assert merged["python_case_counts"]["passed"] == 6 == merged["total_required_cases"]
    assert merged["excluded_cases"] == ordinary["excluded_cases"]
    assert [share["index"] for share in merged["machine_shares"]] == [1, 2, 3]
    for field in ("revision", "source_sha256", "platform"):
        assert merged[field] == ordinary[field]
    assert "PASS" in capsys.readouterr().out


def _edit(folder, name, change):
    path = folder / name
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_a_valid_set_with_a_failed_share_merges_as_a_failure(share_folders, tmp_path, capsys):
    folders, _whole = share_folders
    _edit(folders[1], "summary.json", lambda data: data.update(passed=False))
    output = tmp_path / "merged.json"
    assert merge_machine_shares.main([*map(str, folders), "--output", str(output)]) == 1
    assert json.loads(output.read_text())["passed"] is False
    assert "FAIL: share 2" in capsys.readouterr().out


def _drop_last(folders):
    return folders[:2]


def _repeat_first(folders):
    return [folders[0], folders[0], folders[2]]


def _other_revision(folders):
    _edit(folders[2], "summary.json", lambda data: data.update(revision="def456"))


def _other_platform(folders):
    _edit(folders[2], "summary.json", lambda data: data.update(platform="elsewhere"))


def _changed_source(folders):
    _edit(folders[0], "summary.json", lambda data: data.update(unchanged_source=False))


def _still_running(folders):
    _edit(folders[0], "summary.json", lambda data: data.update(status="running"))


def _ordinary_run(folders):
    _edit(folders[0], "summary.json", lambda data: data.update(machine_share=None))


def _other_count(folders):
    def change(data):
        data["machine_share"]["count"] = 4
    _edit(folders[2], "summary.json", change)
    _edit(folders[2], "machine-share.json", lambda data: data.update(count=4))


def _lost_case(folders):
    def change(data):
        data["assigned_cases"] = data["assigned_cases"][1:]
    donor = next(folder for folder in folders
                 if json.loads((folder / "machine-share.json").read_text())["assigned_cases"])
    _edit(donor, "machine-share.json", change)
    def recount(data):
        data["machine_share"]["assigned_cases"] -= 1
    _edit(donor, "summary.json", recount)


def _doubled_case(folders):
    plans = [json.loads((folder / "machine-share.json").read_text()) for folder in folders]
    donor = next(index for index, plan in enumerate(plans) if plan["assigned_cases"])
    receiver = next(index for index in range(3) if index != donor)
    case = plans[donor]["assigned_cases"][0]
    _edit(folders[receiver], "machine-share.json",
          lambda data: data["assigned_cases"].append(case))
    def recount(data):
        data["machine_share"]["assigned_cases"] += 1
    _edit(folders[receiver], "summary.json", recount)


def _other_case_list(folders):
    def change(data):
        data["required_cases"].append("tests/test_three.py::test_three")
    _edit(folders[2], "machine-share.json", change)


def _no_browser_tests(folders):
    def change(data):
        data["machine_share"]["ui_controls_included"] = False
    for folder in folders:
        _edit(folder, "summary.json", change)


def _other_exclusions(folders):
    _edit(folders[1], "summary.json",
          lambda data: data["excluded_cases"].append({"nodeid": "tests/x.py::y", "reason": "r"}))


def _missing_plan_file(folders):
    (folders[1] / "machine-share.json").unlink()


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (_drop_last, "missing \\[3\\]"),
        (_repeat_first, "repeated \\[1\\]"),
        (_other_revision, "differ in revision"),
        (_other_platform, "differ in platform"),
        (_changed_source, "source changed"),
        (_still_running, "not a completed run"),
        (_ordinary_run, "not a machine share"),
        (_other_count, "share of 4"),
        (_lost_case, "do not cover the required cases"),
        (_doubled_case, "repeats a case"),
        (_other_case_list, "recorded SHA-256"),
        (_no_browser_tests, "browser tests must be in exactly one share"),
        (_other_exclusions, "excluded different cases"),
        (_missing_plan_file, "could not be read"),
    ],
    ids=lambda value: value.__name__.strip("_") if callable(value) else None,
)
def test_the_merge_refuses_anything_but_the_complete_set_of_one_run(
    share_folders, tmp_path, capsys, damage, message
):
    copies, _whole = share_folders
    chosen = damage(copies) or copies
    output = tmp_path / "merged.json"
    assert merge_machine_shares.main([*map(str, chosen), "--output", str(output)]) == 2
    assert not output.exists()
    with pytest.raises(merge_machine_shares.ShareSetError, match=message):
        merge_machine_shares.merge(chosen)
    assert "NOT MERGED" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--machine-share", "2/6", "--journey", "gm-turn"],
        ["--machine-share", "2/6", "--selection-manifest", "selection.json"],
        ["--machine-share", "2/6", "--ci"],
        ["--machine-share", "0/6"],
        ["--machine-share", "7/6"],
        ["--machine-share", "six"],
    ],
    ids=["with-journey", "with-selection", "with-ci", "zero", "beyond-count", "not-a-fraction"],
)
def test_the_runner_refuses_a_share_it_cannot_split(gate, tmp_path, monkeypatch, capsys, arguments):
    with pytest.raises(SystemExit) as refused:
        _main(gate, monkeypatch, "--output-dir", tmp_path / "unused", *arguments)
    assert refused.value.code == 2
    assert "machine" in capsys.readouterr().err.lower()
    assert not (tmp_path / "unused").exists()


def test_listing_a_share_prints_the_plan_and_runs_nothing(gate, tmp_path, monkeypatch, capsys):
    root = tmp_path / "source"
    _real_runner(gate, monkeypatch, root, _checkout(root))
    monkeypatch.setattr(gate, "_run_guarded", lambda *args, **kwargs: pytest.fail("nothing may run"))
    assert _main(gate, monkeypatch, "--machine-share", "1/2", "--list") == 0
    printed = capsys.readouterr().out
    assert "2 shares of 6 required Python cases" in printed
    assert "share 1/2:" in printed and "share 2/2:" in printed
    assert printed.count("includes the browser tests") == 1
