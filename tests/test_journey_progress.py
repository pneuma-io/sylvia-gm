import json

import pytest
from scripts import journey_progress


def test_timing_keeps_original_result_exception_and_records_real_phase(tmp_path, monkeypatch):
    target = tmp_path / "fixture-timings.jsonl"
    monkeypatch.setitem(journey_progress._TIMING_PATHS, "SYLVIA_JOURNEY_FIXTURE_TIMINGS", str(target))

    @journey_progress.timed("prepare")
    def success():
        return {"exact": 7}

    assert success() == {"exact": 7}
    with pytest.raises(ValueError, match="preserved"), journey_progress.phase("failed prepare"):
        raise ValueError("preserved")
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert [row["phase"] for row in rows] == ["prepare", "failed prepare"]
    assert all(row["seconds"] >= 0 for row in rows)


def test_optional_scenario_retention_does_not_change_or_remove_closed_database(
    tmp_path, monkeypatch
):
    from tests.lite_fixture_archive import archive_closed_database

    database = tmp_path / "closed.sqlite3"
    database.write_bytes(b"unchanged closed synthetic fixture")
    monkeypatch.setenv("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", "0")
    archive_closed_database(database, tmp_path)
    assert database.read_bytes() == b"unchanged closed synthetic fixture"
    assert not list(tmp_path.glob("*.parts.json"))


def test_explicit_fixture_archival_still_verifies_complete_restore(tmp_path, monkeypatch):
    from tests import lite_fixture_archive

    root = tmp_path / "owned"
    work = root / "attempts/case"
    work.mkdir(parents=True)
    monkeypatch.setattr(lite_fixture_archive, "ROOT", root)
    monkeypatch.setenv("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", "1")
    database = work / "closed.sqlite3"
    original = b"synthetic SQLite evidence\0" * 10000
    database.write_bytes(original)
    lite_fixture_archive.archive_closed_database(database, work)
    receipt = json.loads(database.with_suffix(".archive.json").read_text())
    assert receipt["full_restore_digest_verified"] is True
    assert receipt["original_bytes"] == len(original)
    assert not database.exists()
    import hashlib
    import zipfile

    manifest = json.loads(database.with_name(database.name + ".parts.json").read_text())
    restored = b""
    for block in manifest["blocks"]:
        with zipfile.ZipFile(block["archive"]) as archive:
            restored += archive.read(block["member"])
    assert restored == original
    assert receipt["original_sha256"] == hashlib.sha256(original).hexdigest()


def test_worker_progress_does_not_read_environment_inside_guarded_case(tmp_path, monkeypatch):
    import importlib.util
    import os
    from pathlib import Path
    from types import SimpleNamespace

    target = tmp_path / "worker-timings.jsonl"
    fixture_target = tmp_path / "fixture-timings.jsonl"
    monkeypatch.setenv("SYLVIA_JOURNEY_TIMINGS", str(target))
    monkeypatch.setenv("SYLVIA_JOURNEY_FIXTURE_TIMINGS", str(fixture_target))
    spec = importlib.util.spec_from_file_location(
        "guarded_worker_progress", Path(journey_progress.__file__)
    )
    progress = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(progress)

    class NoEnvironment(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("Worker telemetry read the guarded environment")

    progress.pytest_runtest_logstart("synthetic::guarded", None)
    with monkeypatch.context() as guard:
        guard.setattr(os, "environ", NoEnvironment())
        with progress.phase("guarded fixture"):
            pass
        progress.pytest_runtest_logreport(SimpleNamespace(
            nodeid="synthetic::guarded", when="call", duration=0.25,
            outcome="passed", failed=False, skipped=False,
        ))
    assert json.loads(target.read_text())["case"] == "synthetic::guarded"
    assert json.loads(fixture_target.read_text())["case"] == "synthetic::guarded"


def test_failure_receipt_survives_a_console_that_cannot_encode_its_message(monkeypatch):
    import io
    import sys
    from types import SimpleNamespace

    console = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(console, encoding="cp1252"))
    monkeypatch.setitem(journey_progress._TIMING_PATHS, "SYLVIA_JOURNEY_TIMINGS", None)
    journey_progress.pytest_runtest_logreport(SimpleNamespace(
        nodeid="synthetic::arrow", when="call", duration=0.1, outcome="failed",
        failed=True, skipped=False, longrepr="expected cell A → B",
    ))
    sys.stdout.flush()
    assert b"expected cell A \\u2192 B" in console.getvalue()


def test_pool_reports_a_non_cp1252_failure_from_a_parent_without_utf8_mode(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET

    from scripts import journey_pool

    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/journey_case_timings.json").write_text('{"seconds":{}}')
    tests = root / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_arrow.py").write_text(
        "def test_arrow():\n"
        "    assert False, 'intentional negative control: A \\u2192 B'\n"
    )
    destination = tmp_path / "evidence"
    destination.mkdir()
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    results = journey_pool.run_balanced_gate(
        root, destination, {"arrow": ["tests/test_arrow.py"]}, ["arrow"], 1, 0.0, None,
    )
    assert not results["arrow"][0]
    log = (destination / "shared-shard-0.log").read_text(encoding="utf-8")
    assert "INTERNALERROR" not in log
    assert "A → B" in log
    cases = list(ET.parse(destination / "arrow.xml").iter("testcase"))
    assert len(cases) == 1 and cases[0].find("failure") is not None
