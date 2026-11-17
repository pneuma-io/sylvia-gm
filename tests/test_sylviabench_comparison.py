"""Offline batch aggregation preserves lineage and rejects mismatched challenges or repeats."""

import pytest
from sylviabench.__main__ import main
from sylviabench.attribution import attribute, load_attributions, pending
from sylviabench.comparison import comparison
from sylviabench.contracts import Result, Setup, digest, read_json, scenarios, write_json
from sylviabench.report import load_results, report


def batch(
    directory,
    identity,
    *,
    source_change=None,
    policy_change=None,
    database="save",
    model=False,
    outcome="incomplete",
    breakers=(),
):
    setup = (
        Setup(
            id=identity,
            name=identity,
            kind="model",
            provider_id="selected",
            model_id="exact/model",
            owner_config="never-read.yaml",
        )
        if model
        else Setup(id=identity, name=identity)
    )
    scenario = scenarios()[2]
    source = {"commit": identity, "application_sha256": "app", "framework_sha256": "frame"}
    source.update(source_change or {})
    specification = {
        "schema_version": 1,
        "source": source,
        "setups": [setup.model_dump()],
        "scenarios": [scenario.model_dump()],
        "repetitions": 1,
        "retries": 2,
        "randomness_policy": "paired",
        "provider_requests_per_run": 16,
        "fixture_pack_sha256": "pack",
    }
    specification.update(policy_change or {})
    manifest = {**specification, "suite_id": digest(specification)}
    write_json(directory / "manifest.json", manifest)
    write_json(
        directory / "seed.json",
        {
            "database_sha256": database,
            "fixture_pack_sha256": specification["fixture_pack_sha256"],
            "observation": {},
        },
    )
    # These are synthetic unexecuted records, never model evidence. The first check carries
    # the failure when one is requested.
    statuses = {
        "pass": ["pass"] * len(scenario.checks),
        "fail": ["fail"] + ["pass"] * (len(scenario.checks) - 1),
    }.get(outcome, ["not_evaluated"] * len(scenario.checks))
    row = Result(
        run_id=identity,
        suite_id=manifest["suite_id"],
        source=source,
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        scenario_sha256=digest(scenario.model_dump()),
        fixture_sha256=database,
        category=scenario.category,
        repetition=0,
        execution_kind="model_run" if model else "framework_selftest",
        setup=setup,
        started_at="2026-01-01T00:00:00Z",
        elapsed_seconds=0.0,
        attempts=[{"turn": 0, "number": 0, "elapsed_seconds": 0.0, "completed": True}]
        if outcome in {"pass", "fail"}
        else [],
        checks=[
            {
                "id": c,
                "status": status,
                "reason": "Synthetic record",
                "evidence": {} if status == "not_evaluated" else {"synthetic": True},
            }
            for c, status in zip(scenario.checks, statuses, strict=True)
        ],
        outcome=outcome,
        failure_cause=None if outcome == "pass" or not model else "unknown",
        failure_evidence=None if outcome == "pass" or not model else "Synthetic record",
        session_breakers=list(breakers),
        observations={
            "randomness": {"policy": "paired"},
            "challenge": {
                "baseline_sha256": digest({}),
                "player_messages_sha256": digest(scenario.player_messages),
            },
        },
    )
    write_json(directory / "results/run.json", row.model_dump())
    return directory


def test_compatible_batches_render_together_without_rewriting_results(tmp_path):
    first, second = batch(tmp_path / "first", "first"), batch(tmp_path / "second", "second")
    originals = [
        (p, p.read_bytes()) for directory in (first, second) for p in directory.rglob("*.json")
    ]
    output = tmp_path / "combined"
    report = comparison([first, second], output)
    text = report.read_text(encoding="utf-8")
    assert "Framework self-test" in text and "2 of 2 planned runs" in text
    summary = read_json(output / "summary.json")
    assert set(summary["setups"]) == {"first", "second"}
    descriptor = read_json(output / "comparison.json")
    assert [s["suite_id"] for s in descriptor["inputs"]] == [
        read_json(p / "manifest.json")["suite_id"] for p in (first, second)
    ]
    assert comparison([first, second], output) == report
    assert all(p.read_bytes() == original for p, original in originals)


@pytest.mark.parametrize(
    "change",
    [
        {"source_change": {"framework_sha256": "changed"}},
        {"policy_change": {"retries": 1}},
        {"policy_change": {"fixture_pack_sha256": "different"}},
        {"policy_change": {"owner_reply_timeout_seconds": 330}},
    ],
)
def test_changed_execution_policy_is_not_pooled(tmp_path, change):
    first = batch(tmp_path / "first", "first")
    second = batch(tmp_path / "second", "second", **change)
    with pytest.raises(ValueError, match="policy mismatch"):
        comparison([first, second], tmp_path / "output")


def test_duplicate_setup_and_starting_database_are_rejected(tmp_path):
    first = batch(tmp_path / "first", "same")
    duplicate = batch(tmp_path / "duplicate", "same")
    with pytest.raises(ValueError, match="Repeated setup"):
        comparison([first, duplicate], tmp_path / "duplicate-output")
    different = batch(tmp_path / "different", "different", database="other-save")
    with pytest.raises(ValueError, match="database mismatch"):
        comparison([first, different], tmp_path / "different-output")
    with pytest.raises(ValueError, match="Duplicate comparison"):
        comparison([first, first], tmp_path / "same-input")


def test_legacy_provider_activity_without_attempt_timing_stays_unknown(tmp_path):
    first, second = batch(tmp_path / "first", "first"), batch(tmp_path / "second", "second")
    path = first / "results/run.json"
    row = read_json(path)
    row["outcome"] = "error"
    row["observations"].update(
        {
            "execution_error": "TimeoutError",
            "provider": {
                "requests": [
                    {
                        "stage": "gm",
                        "tool_count": 1,
                        "messages_sha256": "messages",
                        "tools_sha256": "tools",
                    }
                ]
            },
        }
    )
    write_json(path, row)
    output = tmp_path / "comparison"
    text = comparison([first, second], output).read_text(encoding="utf-8")
    summary = read_json(output / "summary.json")["setups"]["first"]
    assert summary["missing_attempt_records"] == 1
    assert summary["median_turn_seconds"] is None
    assert "Attempt details are unavailable for 1" in text


def memory_score(directory, setup):
    return read_json(directory / "summary.json")["setups"][setup]["categories"]["memory"]


def test_model_failure_counts_only_after_a_recorded_review(tmp_path):
    directory = batch(tmp_path / "models", "model-a", model=True, outcome="fail")
    original = (directory / "results/run.json").read_bytes()
    report(directory)
    assert memory_score(directory, "model-a") is None
    _manifest, rows = load_results(directory)
    assert [row["run_id"] for row in pending(rows, {})] == ["model-a"]
    attribute(
        directory, rows, "model-a", "gm_behavior", "The answer named a red flag.", "Blind review"
    )
    text = report(directory).read_text(encoding="utf-8")
    assert memory_score(directory, "model-a") == 0.0
    assert read_json(directory / "summary.json")["reviewed_causes"] == {"model-a": "gm_behavior"}
    assert "Reviewed cause: gm_behavior, decided by Blind review" in text
    assert (directory / "results/run.json").read_bytes() == original
    assert pending(rows, load_attributions(directory, rows)) == []


def test_reviews_cannot_invent_erase_or_outlive_a_recorded_failure(tmp_path):
    failed = batch(tmp_path / "failed", "model-a", model=True, outcome="fail")
    rows = load_results(failed)[1]
    for cause, evidence, match in (
        ("scripted_driver", "Injected.", "self-test only"),
        ("gm_behavior", " ", "named reviewer"),
    ):
        with pytest.raises(ValueError, match=match):
            attribute(failed, rows, "model-a", cause, evidence, "Reviewer")
    with pytest.raises(ValueError, match="recorded in this batch"):
        attribute(failed, rows, "another-run", "gm_behavior", "Evidence.", "Reviewer")
    assert not (failed / "attributions").exists()
    attribute(failed, rows, "model-a", "sylvia_bug", "The tool refused a valid cast.", "Reviewer")
    report(failed)
    assert memory_score(failed, "model-a") is None, "A technical cause keeps the category untested"
    with pytest.raises(ValueError, match="already has"):
        attribute(failed, rows, "model-a", "gm_behavior", "Second thoughts.", "Reviewer")
    changed = read_json(failed / "results/run.json")
    changed["elapsed_seconds"] = 1.0
    write_json(failed / "results/run.json", changed)
    with pytest.raises(ValueError, match="does not match"):
        report(failed)

    for name, options in (
        ("passed", {"model": True, "outcome": "pass"}),
        ("scripted", {"outcome": "incomplete"}),
    ):
        directory = batch(tmp_path / name, name, **options)
        with pytest.raises(ValueError, match="did not pass"):
            attribute(
                directory, load_results(directory)[1], name, "gm_behavior", "Evidence.", "Reviewer"
            )
    stalled = batch(tmp_path / "stalled", "stalled", model=True)
    with pytest.raises(ValueError, match="failed check or a stuck turn"):
        attribute(
            stalled, load_results(stalled)[1], "stalled", "gm_behavior", "Evidence.", "Reviewer"
        )
    stuck = batch(tmp_path / "stuck", "stuck", model=True, breakers=["stuck_turn"])
    attribute(
        stuck, load_results(stuck)[1], "stuck", "gm_behavior", "Three attempts never ended.", "R"
    )
    report(stuck)
    assert memory_score(stuck, "stuck") == 0.0


def test_comparison_reads_each_batch_reviews_without_copying_them(tmp_path):
    failed = batch(tmp_path / "failed", "failed", model=True, outcome="fail")
    passed = batch(tmp_path / "passed", "passed", model=True, outcome="pass")
    attribute(failed, load_results(failed)[1], "failed", "gm_behavior", "Wrong signal.", "Reviewer")
    output = tmp_path / "combined"
    comparison([failed, passed], output)
    assert memory_score(output, "failed") == 0.0
    assert memory_score(output, "passed") == 100.0
    assert not (output / "attributions").exists()


def test_attribute_command_lists_waiting_failures_and_needs_a_complete_decision(tmp_path, capsys):
    directory = batch(tmp_path / "models", "model-a", model=True, outcome="fail")
    assert main(["attribute", str(directory)]) == 0
    assert '"run_id": "model-a"' in capsys.readouterr().out
    command = ["attribute", str(directory), "--run", "model-a", "--cause", "gm_behavior"]
    assert main(command) == 2
    assert not (directory / "attributions").exists()
    assert main([*command, "--evidence", "Wrong signal.", "--reviewer", "Blind review"]) == 0
    assert (directory / "attributions/model-a.json").is_file()
    capsys.readouterr()
    assert main(["attribute", str(directory)]) == 0
    assert capsys.readouterr().out.strip() == "[]"
