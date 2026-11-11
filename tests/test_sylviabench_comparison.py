"""Offline batch aggregation preserves lineage and rejects mismatched challenges or repeats."""

import pytest
from sylviabench.comparison import comparison
from sylviabench.contracts import Result, Setup, digest, read_json, scenarios, write_json


def batch(directory, identity, *, source_change=None, policy_change=None, database="save"):
    setup = Setup(id=identity, name=identity)
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
    # This is a synthetic unexecuted record, explicitly incomplete and not model evidence.
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
        execution_kind="framework_selftest",
        setup=setup,
        started_at="2026-01-01T00:00:00Z",
        elapsed_seconds=0.0,
        attempts=[],
        checks=[
            {"id": c, "status": "not_evaluated", "reason": "No execution"} for c in scenario.checks
        ],
        outcome="incomplete",
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
