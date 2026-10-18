import json

import pytest
from scripts.stage_journey_diagnostics import stage_diagnostics


def test_retains_required_small_diagnostics_without_successful_scratch(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    names = [
        "summary.json",
        "selection.json",
        "shared-shard-0.xml",
        "shared-shard-0.log",
        "pool-assignments.json",
        "group-required-cases.json",
        "phase-timings.jsonl",
    ]
    for name in names:
        (raw / name).write_text(
            '{"status":"completed","passed":false}' if name == "summary.json" else "evidence"
        )
    for folder in ("attempts", "temporary", "fixture-store", "venv", "pytest-cache"):
        (raw / folder).mkdir()
        (raw / folder / "bulk.sqlite3").write_bytes(b"scratch")
    (raw / "unselected.sqlite3").write_bytes(b"scratch")
    runner = tmp_path / "runner.log"
    runner.write_text("actual early failure")
    stage = tmp_path / "retained"
    stage_diagnostics(raw, stage, runner_log=runner, exit_code=1)
    assert set(names).issubset({path.name for path in stage.iterdir()})
    assert not any(stage.rglob("*.sqlite3"))
    assert (stage / "runner.log").read_text() == "actual early failure"
    assert (raw / "attempts/bulk.sqlite3").read_bytes() == b"scratch"
    receipt = json.loads((stage / "diagnostic-manifest.json").read_text())
    assert all(row["sha256"] and row["bytes"] for row in receipt["files"])


@pytest.mark.parametrize("summary", [None, "broken json", '{"status":"running","passed":false}'])
def test_early_exception_has_truthful_failure_summary_and_console(tmp_path, summary):
    raw = tmp_path / "raw"
    raw.mkdir()
    if summary is not None:
        (raw / "summary.json").write_text(summary)
    console = tmp_path / "console.log"
    console.write_text("Traceback: collection failed")
    out = tmp_path / "stage"
    stage_diagnostics(raw, out, runner_log=console, exit_code=1)
    final = json.loads((out / "summary.json").read_text())
    assert final["passed"] is False and final["complete_gate"] is False
    assert final["status"] == "incomplete" and final["runner_exit_code"] == 1
    assert (out / "runner.log").read_text().startswith("Traceback")


def test_only_explicit_necessary_failed_evidence_is_retained(tmp_path):
    raw = tmp_path / "raw"
    (raw / "attempts").mkdir(parents=True)
    (raw / "attempts/failed.png").write_bytes(b"specific failure")
    (raw / "attempts/success.sqlite3").write_bytes(b"reproducible scratch")
    stage_diagnostics(raw, tmp_path / "stage", evidence=["attempts/failed.png"], exit_code=1)
    assert (
        tmp_path / "stage/failed-evidence/attempts/failed.png"
    ).read_bytes() == b"specific failure"
    assert not list((tmp_path / "stage").rglob("*.sqlite3"))


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute.txt", "attempts", "missing.txt"])
def test_failed_evidence_cannot_escape_or_select_a_directory(tmp_path, name):
    raw = tmp_path / "raw"
    (raw / "attempts").mkdir(parents=True)
    with pytest.raises(ValueError):
        stage_diagnostics(raw, tmp_path / "stage", evidence=[name], exit_code=1)


def test_refuses_existing_delivery_without_deleting_it(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "old.log").write_text("preserved")
    with pytest.raises(ValueError):
        stage_diagnostics(raw, stage, exit_code=1)
    assert (stage / "old.log").read_text() == "preserved"


def test_final_receipt_covers_late_capacity_selection_and_failure_summary(tmp_path):
    import hashlib

    from scripts.stage_journey_diagnostics import finalize_manifest

    stage = tmp_path / "retained"
    stage.mkdir()
    for name, content in {
        "runner-capacity.json": '{"logical_cpus":2}',
        "selection.json": '{"mode":"affected"}',
        "summary.json": '{"passed":false,"status":"incomplete"}',
    }.items():
        (stage / name).write_text(content)
    (stage / "diagnostic-manifest.json").write_text("outdated receipt")
    receipt = finalize_manifest(stage)
    assert {row["path"] for row in receipt["files"]} == {
        "runner-capacity.json",
        "selection.json",
        "summary.json",
    }
    for row in receipt["files"]:
        raw = (stage / row["path"]).read_bytes()
        assert row["sha256"] == hashlib.sha256(raw).hexdigest()
        assert row["bytes"] == len(raw)
