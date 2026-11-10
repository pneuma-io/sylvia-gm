"""Framework acceptance at the real owner boundary, using no model connection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sylviabench.__main__ import main
from sylviabench.contracts import (
    Check,
    Result,
    Setup,
    digest,
    read_json,
    scenarios,
    setups,
    write_json,
)
from sylviabench.report import load_results, report, score
from sylviabench.runner import execute, run, source_identity


@pytest.fixture(scope="module")
def experiment(tmp_path_factory):
    """One admitted seed serves all representative journeys; any live resolver use fails."""
    from sylvia_gm.interfaces import owner_process

    with pytest.MonkeyPatch.context() as patch:

        def forbidden(*_args, **_kwargs):
            raise AssertionError("Offline benchmarking must not construct a configured provider")

        patch.setattr(owner_process, "ConfiguredProviderResolver", forbidden)
        output = tmp_path_factory.mktemp("sylviabench") / "selftest"
        selected = [s for s in setups() if s.id in {"good", "bad"}]
        rows = run(output, selected, scenarios())
        yield output, rows


def test_real_owner_good_and_bad_cases_fail_for_the_intended_reason(experiment):
    output, rows = experiment
    assert len(rows) == 6
    for scenario in scenarios():
        good = next(r for r in rows if r.scenario_id == scenario.id and r.setup.id == "good")
        bad = next(r for r in rows if r.scenario_id == scenario.id and r.setup.id == "bad")
        assert good.outcome == "pass", good.model_dump()
        assert bad.outcome == "fail", bad.model_dump()
        assert all(c.status == "pass" for c in good.checks)
        assert next(c for c in bad.checks if c.id == scenario.expected_bad_check).status == "fail"
        assert good.execution_kind == bad.execution_kind == "framework_selftest"
        assert good.observed_model is None and good.usage is None
    healing = next(r for r in rows if r.setup.id == "good" and r.scenario_id == "T1")
    proof = next(c.evidence for c in healing.checks if c.id == "healing_matches_receipt")
    assert proof["after_hp"] > proof["before_hp"]
    assert proof["dice_total"] == sum(proof["dice"])
    assert len(proof["dice"]) == 2
    assert main(["replay", str(output)]) == 0


def test_recovery_preserves_first_failure_and_stops_after_two_retries(experiment, tmp_path):
    output, rows = experiment
    ids = {k: read_json(output / "seed.json")[k] for k in ("campaign", "session")}
    scenario = next(s for s in scenarios() if s.id == "T3")
    for behavior, expected_attempts, outcome in (
        ("recover", 2, "pass"),
        ("stuck", 3, "incomplete"),
    ):
        setup = next(s for s in setups() if s.id == behavior)
        result = execute(
            scenario,
            setup,
            0,
            tmp_path / behavior,
            output / "seed/campaign.sqlite3",
            ids,
            rows[0].suite_id,
            rows[0].source,
            retries=2,
        )
        assert len(result.attempts) == expected_attempts, result.model_dump()
        assert not result.attempts[0].completed
        assert result.outcome == outcome, result.model_dump()
        if behavior == "recover":
            assert result.attempts[-1].completed
        else:
            assert "stuck_turn" in result.session_breakers
            assert all(c.status == "not_evaluated" for c in result.checks)
        assert len(read_json(tmp_path / behavior / "attempts.json")) == expected_attempts


def test_resume_is_offline_and_refuses_changed_source(experiment, monkeypatch):
    output, original = experiment
    from sylviabench import runner

    monkeypatch.setattr(runner, "seed", lambda *_: pytest.fail("Completed runs must not rebuild"))
    resumed = run(
        output, [s for s in setups() if s.id in {"good", "bad"}], scenarios(), resume=True
    )
    assert sorted(r.run_id for r in resumed) == sorted(r.run_id for r in original)
    changed = {**source_identity(), "commit": "different-source"}
    monkeypatch.setattr(runner, "source_identity", lambda: changed)
    with pytest.raises(ValueError, match="Resume identity mismatch"):
        run(output, [s for s in setups() if s.id in {"good", "bad"}], scenarios(), resume=True)


def test_report_is_standalone_escapes_narration_and_keeps_missing_story(experiment, tmp_path):
    output, rows = experiment
    path = report(output)
    text = path.read_text(encoding="utf-8")
    assert "Framework self-test" in text and "Untested" in text
    assert "<script" not in text and "http://" not in text and "https://" not in text
    summary = read_json(output / "summary.json")
    assert summary["setups"]["good"]["headline"] is None
    assert summary["setups"]["good"]["categories"]["story"] is None
    categories = {s.id: s.category for s in scenarios()}
    doubled = [*rows, *rows]
    assert score(doubled, categories)["category_95pct_bands"]["rules"] is not None
    clone = tmp_path / "report"
    clone.mkdir()
    write_json(clone / "manifest.json", read_json(output / "manifest.json"))
    write_json(clone / "seed.json", read_json(output / "seed.json"))
    for index, row in enumerate(rows):
        data = row.model_dump()
        data["checks"][0]["reason"] = "<script>alert('unsafe')</script>"
        write_json(clone / "results" / f"{index}.json", data)
    escaped = report(clone).read_text(encoding="utf-8")
    assert "&lt;script&gt;" in escaped and "<script>" not in escaped


def test_tampered_result_is_rejected(experiment, tmp_path):
    output, rows = experiment
    write_json(tmp_path / "manifest.json", read_json(output / "manifest.json"))
    write_json(tmp_path / "seed.json", read_json(output / "seed.json"))
    data = rows[0].model_dump()
    data["scenario_sha256"] = "different-fixture"
    write_json(tmp_path / "results/tampered.json", data)
    with pytest.raises(ValueError, match="Mismatched"):
        load_results(tmp_path)


def test_unknown_checks_and_execution_kind_cannot_become_a_pass(experiment):
    _output, rows = experiment
    data = next(row.model_dump() for row in rows if row.outcome == "pass")
    data["checks"][0]["status"] = "not_evaluated"
    with pytest.raises(ValidationError):
        Result.model_validate(data)
    data["execution_kind"] = "model_run"
    with pytest.raises(ValidationError):
        Result.model_validate(data)
    with pytest.raises(ValidationError):
        Check(id="missing", status="pass", reason="No evidence")


def test_setup_validation_and_live_opt_in_happen_before_owner_work(tmp_path, monkeypatch):
    for identity in ("../escape", "a/b", "", "C:\\data"):
        with pytest.raises(ValidationError):
            Setup(id=identity, name="Invalid")
    model = Setup(
        id="model",
        name="Explicit future model",
        kind="model",
        provider_id="selected",
        model_id="exact/model",
        owner_config="not-read.yaml",
    )
    from sylviabench import runner

    monkeypatch.setattr(runner, "source_identity", lambda: pytest.fail("No source work needed"))
    with pytest.raises(ValueError, match="allow-model-calls"):
        run(tmp_path / "model", [model], scenarios())
    assert not (tmp_path / "model").exists()
    assert main(["run", "--setup", "missing", "--output", str(tmp_path / "missing")]) == 2
    with pytest.raises(ValidationError):
        Setup(id="bad", name="Bad", owner_config="must-not-read.yaml")


def test_duplicate_json_keys_and_changed_manifest_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"id":1,"id":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        read_json(path)
    write_json(tmp_path / "manifest.json", {"suite_id": "wrong", "schema_version": 1})
    with pytest.raises(ValueError, match="Manifest identity"):
        load_results(tmp_path)
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_an_unanswered_turn_cannot_pass_using_the_previous_narration():
    from sylviabench.checks import evaluate

    fact = "Use a blue lantern at the west gate."
    unchanged = {"talks": [fact], "facts": [{"text": fact, "visibility": "player_known"}]}
    scenario = next(s for s in scenarios() if s.id == "T3")
    checks = evaluate(
        scenario,
        {
            "before": unchanged,
            "after": unchanged,
            "saved": unchanged,
            "reopened": unchanged,
        },
    )
    assert all(check.status == "not_evaluated" for check in checks)
