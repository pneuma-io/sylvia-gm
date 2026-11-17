import json
import subprocess
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from scripts import journey_pool
from scripts.check_supported_journeys import PYTHON_JOURNEYS
from scripts.journey_pool import balanced_assignments, partition_reports
from tests.runner_checkouts import install_runs_on_plugin, record_no_platform_exclusions


def test_case_key_keeps_parameter_delimiters_out_of_class_names():
    assert journey_pool.case_key("tests/test_one.py::test_case[tests/other.py::test_other]") == (
        "tests.test_one::test_case[tests/other.py::test_other]"
    )
    assert journey_pool.case_key("tests/test_one.py::TestClass::test_case[a::b]") == (
        "tests.test_one.TestClass::test_case[a::b]"
    )


def test_balanced_pool_keeps_every_case_and_uses_recorded_work():
    tasks = [("group", f"tests/test_case.py::test_{n}") for n in range(5)]
    timings = {f"tests.test_case::test_{n}": value for n, value in enumerate([30, 20, 10, 5, 5])}
    buckets, loads = balanced_assignments(tasks, 2, timings)
    assert sorted(task for bucket in buckets for task in bucket) == sorted(tasks)
    assert max(loads) == 35


def test_module_affinity_preserves_all_cases_and_one_worker_per_module():
    tasks = [
        ("one", "tests/test_a.py::test_1"),
        ("one", "tests/test_a.py::test_2"),
        ("two", "tests/test_b.py::test_3"),
        ("two", "tests/test_c.py::test_4"),
    ]
    buckets, loads = balanced_assignments(tasks, 2, {}, affinity=True)
    assert sorted(item for bucket in buckets for item in bucket) == sorted(tasks)
    assert sum(loads) == 20
    for module in {item[1].split("::")[0] for item in tasks}:
        assert (
            sum(any(item[1].startswith(module + "::") for item in bucket) for bucket in buckets)
            == 1
        )


def test_pool_partition_rejects_missing_duplicate_skipped_and_unknown_cases():
    expected = {"one": {"tests.case::test_one"}, "two": {"tests.case::test_two"}}
    for mode in ["complete", "missing", "duplicate", "skipped", "unknown", "failed_process"]:
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite")
        ET.SubElement(suite, "testcase", classname="tests.case", name="test_one")
        if mode != "missing":
            case = ET.SubElement(suite, "testcase", classname="tests.case", name="test_two")
            if mode == "skipped":
                ET.SubElement(case, "skipped")
        if mode == "duplicate":
            ET.SubElement(suite, "testcase", classname="tests.case", name="test_two")
        if mode == "unknown":
            ET.SubElement(suite, "testcase", classname="tests.case", name="test_other")
        _, passed, _ = partition_reports(expected, [root], mode != "failed_process")
        assert all(passed.values()) is (mode == "complete")


def test_actual_journey_selectors_have_one_owner_group():
    selectors = [selector for values in PYTHON_JOURNEYS.values() for selector in values]
    assert len(selectors) == len(set(selectors))


@pytest.mark.parametrize(
    "second_selector", ["tests/test_shared.py", "tests/test_shared.py::test_one"]
)
def test_overlapping_collected_cases_fail_before_workers_start(
    tmp_path, monkeypatch, second_selector
):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    destination = tmp_path / "evidence"
    destination.mkdir()
    calls = []

    def collect(command, **kwargs):
        calls.append(command)
        assert "--collect-only" in command, "Overlapping cases must not launch test workers"
        record_no_platform_exclusions(kwargs)
        return subprocess.CompletedProcess(command, 0, "tests/test_shared.py::test_one\n", "")

    monkeypatch.setattr(journey_pool, "subprocess", SimpleNamespace(run=collect))
    with pytest.raises(ValueError, match="multiple groups"):
        journey_pool.run_balanced_gate(
            root,
            destination,
            {"one": ["tests/test_shared.py"], "two": [second_selector]},
            ["one", "two"],
            2,
            0.0,
            None,
        )
    assert len(calls) == 2
    assert not (destination / "pool-assignments.json").exists()
    assert not (destination / "attempts").exists()


def test_real_pool_runs_disjoint_collected_cases_once(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    install_runs_on_plugin(root)
    tests = root / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_one.py").write_text("def test_one():\n    assert 1 + 1 == 2\n")
    (tests / "test_two.py").write_text("def test_two():\n    assert 2 + 2 == 4\n")
    destination = tmp_path / "evidence"
    destination.mkdir()
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    results = journey_pool.run_balanced_gate(
        root,
        destination,
        {"one": ["tests/test_one.py"], "two": ["tests/test_two.py"]},
        ["one", "two"],
        2,
        0.0,
        None,
    )
    assert all(passed for passed, _ in results.values())
    assert set(results) == {"one", "two"}
    for name in results:
        cases = list(ET.parse(destination / f"{name}.xml").iter("testcase"))
        assert len(cases) == 1 and not list(cases[0])
    assignments = json.loads((destination / "pool-assignments.json").read_text())
    assert sum(len(bucket) for bucket in assignments["tasks"]) == 2
    assert assignments["qualification_deadline_seconds"] is None
    assert assignments["scheduler"] == "case"
    for path in destination.glob("shared-shard-*-host-timings.jsonl"):
        timings = [json.loads(line) for line in path.read_text().splitlines()]
        assert timings[0]["phase"] == "launch_to_first_case"
        assert timings[0]["seconds"] >= 0
        assert timings[-1]["phase"] == "worker_wall"
        assert timings[-1]["seconds"] >= timings[0]["seconds"]


@pytest.mark.parametrize(
    ("setting", "retained"),
    [(None, False), ("0", False), ("1", True)],
    ids=["default", "explicit-remove", "explicit-keep"],
)
def test_real_pool_retains_failed_scratch_and_releases_successful_case_data(
    tmp_path, monkeypatch, capsys, setting, retained,
):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    install_runs_on_plugin(root)
    tests = root / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_outcomes.py").write_text(
        "def test_pass(tmp_path):\n"
        "    (tmp_path / 'success.sqlite3').write_bytes(b'disposable successful case')\n"
        "def test_failure(tmp_path):\n"
        "    (tmp_path / 'failed.sqlite3').write_bytes(b'preserve failed evidence')\n"
        "    assert False, 'intentional negative runner control'\n"
    )
    destination = tmp_path / "evidence"
    destination.mkdir()
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    # Keeping passed tests' scratch is only ever the caller's explicit choice.
    if setting is None:
        monkeypatch.delenv("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", raising=False)
    else:
        monkeypatch.setenv("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", setting)
    results = journey_pool.run_balanced_gate(
        root,
        destination,
        {"outcomes": ["tests/test_outcomes.py"]},
        ["outcomes"],
        1,
        0.0,
        None,
    )
    assert not results["outcomes"][0]
    kept = list((destination / "attempts").rglob("success.sqlite3"))
    assert len(kept) == (1 if retained else 0)
    failed = list((destination / "attempts").rglob("failed.sqlite3"))
    assert len(failed) == 1 and failed[0].read_bytes() == b"preserve failed evidence"
    assignments = json.loads((destination / "pool-assignments.json").read_text())
    assert assignments["retain_successful_fixtures"] is retained
    cases = list(ET.parse(destination / "outcomes.xml").iter("testcase"))
    assert len(cases) == 2 and sum(case.find("failure") is not None for case in cases) == 1
    # The console names the failed case and the log that holds its full output.
    failed_lines = [line for line in capsys.readouterr().out.splitlines()
                    if line.startswith("FAILED CASE")]
    assert failed_lines == [
        "FAILED CASE tests.test_outcomes::test_failure [failure] AssertionError: "
        "intentional negative runner control assert False "
        "(full output in shared-shard-0.log)"
    ]


def test_report_failures_name_failed_errored_and_unregistered_skipped_cases():
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    ET.SubElement(suite, "testcase", classname="tests.case", name="test_passed")
    failed = ET.SubElement(suite, "testcase", classname="tests.case", name="test_failed")
    ET.SubElement(failed, "failure", message="AssertionError: first line\n  second line")
    errored = ET.SubElement(suite, "testcase", classname="tests.case", name="test_errored")
    ET.SubElement(errored, "error", message="failed on setup")
    skipped = ET.SubElement(suite, "testcase", classname="tests.case", name="test_skipped")
    ET.SubElement(skipped, "skipped", message="not registered")
    assert journey_pool.report_failures(root) == [
        "FAILED CASE tests.case::test_failed [failure] AssertionError: first line second line",
        "FAILED CASE tests.case::test_errored [error] failed on setup",
        "FAILED CASE tests.case::test_skipped [skipped] not registered",
    ]


def test_pool_refuses_an_unknown_retention_setting_before_collecting(tmp_path, monkeypatch):
    monkeypatch.setenv("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", "yes")
    monkeypatch.setattr(journey_pool, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: pytest.fail("An unknown setting must stop before collection"),
    ))
    destination = tmp_path / "evidence"
    with pytest.raises(ValueError, match="must be 0 or 1"):
        journey_pool.run_balanced_gate(
            tmp_path / "source", destination, {"one": ["tests/test_one.py"]}, ["one"], 1, 0.0, None,
        )
    assert not destination.exists()


def test_real_collection_and_workers_import_the_selected_source_before_ambient_packages(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    install_runs_on_plugin(root)
    owned = root / "src/qa_source_binding"
    owned.mkdir(parents=True)
    (owned / "__init__.py").write_text("IDENTITY = 'selected-source'\n")
    foreign = tmp_path / "ambient/qa_source_binding"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("IDENTITY = 'foreign-environment'\n")
    tests = root / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_binding.py").write_text(
        "from qa_source_binding import IDENTITY\n"
        "if IDENTITY != 'selected-source':\n"
        "    raise RuntimeError('Collection imported a foreign source')\n"
        "def test_binding():\n"
        "    assert IDENTITY == 'selected-source'\n"
    )
    destination = tmp_path / "evidence"
    destination.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(foreign.parent))
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    results = journey_pool.run_balanced_gate(
        root,
        destination,
        {"binding": ["tests/test_binding.py"]},
        ["binding"],
        1,
        0.0,
        None,
    )
    assert results["binding"][0]


@pytest.mark.parametrize("include_historical", [False, True])
def test_real_pool_collects_new_behavior_once_and_reports_historical_exclusion(
    tmp_path, monkeypatch, include_historical,
):
    from scripts.check_catalog import PRODUCT_CASE_EXCLUSIONS

    historical = next(iter(PRODUCT_CASE_EXCLUSIONS))
    module, historical_function = historical.split("::")
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    install_runs_on_plugin(root)
    tests = root / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (root / module).write_text(
        "def test_existing_behavior():\n    assert True\n"
        "def test_future_behavior():\n    assert True\n"
        f"def {historical_function}():\n    assert True\n"
    )
    destination = tmp_path / "evidence"
    destination.mkdir()
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    results = journey_pool.run_balanced_gate(
        root, destination, {"gm-turn": [module]}, ["gm-turn"], 1, 0.0, None,
        include_historical=include_historical,
    )
    assert results["gm-turn"][0]
    assignments = json.loads((destination / "pool-assignments.json").read_text())
    executed = [nodeid for bucket in assignments["tasks"] for _, nodeid in bucket]
    assert len(executed) == len(set(executed)) == (3 if include_historical else 2)
    assert module + "::test_future_behavior" in executed
    assert (historical in executed) is include_historical
    excluded = json.loads((destination / "gm-turn-excluded-cases.json").read_text())
    assert excluded == ([] if include_historical else [
        {"nodeid": historical, "reason": PRODUCT_CASE_EXCLUSIONS[historical], "kind": "historical"},
    ])
    cases = list(ET.parse(destination / "gm-turn.xml").iter("testcase"))
    assert len(cases) == len(executed) and all(not list(case) for case in cases)


def test_real_pool_excludes_other_platform_cases_by_exact_id(tmp_path, monkeypatch):
    from tests.runner_checkouts import OTHER_PLATFORM

    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    install_runs_on_plugin(root)
    tests = root / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_marked.py").write_text(
        "import pytest\n"
        f"@pytest.mark.runs_on('{OTHER_PLATFORM}', reason='Elsewhere semantics')\n"
        "def test_elsewhere():\n    raise AssertionError('must never run on this platform')\n"
        "def test_behavior():\n    assert True\n"
    )
    destination = tmp_path / "evidence"
    destination.mkdir()
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    results = journey_pool.run_balanced_gate(
        root, destination, {"marked": ["tests/test_marked.py"]}, ["marked"], 1, 0.0, None,
    )
    assert results["marked"][0]
    assignments = json.loads((destination / "pool-assignments.json").read_text())
    assert [nodeid for bucket in assignments["tasks"] for _, nodeid in bucket] == [
        "tests/test_marked.py::test_behavior",
    ]
    assert json.loads((destination / "marked-excluded-cases.json").read_text()) == [{
        "nodeid": "tests/test_marked.py::test_elsewhere",
        "reason": f"Runs only on {OTHER_PLATFORM}: Elsewhere semantics",
        "kind": "platform",
        "platforms": [OTHER_PLATFORM],
    }]
    cases = list(ET.parse(destination / "marked.xml").iter("testcase"))
    assert [case.get("name") for case in cases] == ["test_behavior"]
    assert not list(cases[0])
