"""Each v2 check must reject a deliberate defect, independently of any model identity."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sylviabench.checks import breakers, evaluate
from sylviabench.contracts import scenarios
from sylviabench.debrief import SURVEY, collect
from sylviabench.fixtures import FACT, SECRET, SRD
from sylviabench.provenance import (
    MAX_REQUESTS,
    BenchmarkRequestLimitError,
    BenchmarkRequestTimeoutError,
    ObservedResolver,
)

from sylvia_gm.provider._base import ProviderStreamChunk
from sylvia_gm.provider._exceptions import RateLimitError


def observation():
    fact = {
        "record_id": "signal",
        "record_kind": "fact",
        "text": FACT,
        "visibility": "player_known",
        "known_by": ["player"],
    }
    secret = {
        "record_id": "password",
        "record_kind": "fact",
        "text": SECRET,
        "visibility": "gm_private",
        "known_by": [],
    }
    before = {
        "player_id": "cleric",
        "talks": ["The courier waits."],
        "state": {
            SRD + ".entities": {
                "cleric": {"hp": 9, "max_hp": 9, "resources": {"spell_slot_1": 2}},
                "courier": {"hp": 19, "max_hp": 40},
            }
        },
        "facts": [fact, secret],
        "reveals": {},
        "compaction_count": 0,
    }
    after = deepcopy(before)
    after["talks"].append("Cure Wounds restores 11 hit points. " + FACT)
    after["state"][SRD + ".entities"]["cleric"]["resources"]["spell_slot_1"] = 1
    after["state"][SRD + ".entities"]["courier"]["hp"] = 30
    reopened = deepcopy(before)
    reopened["compaction_count"] = 1
    return {
        "before": before,
        "saved": deepcopy(before),
        "reopened": reopened,
        "after": after,
        "mechanics": [
            {
                "action_id": "cast",
                "tool_name": "cast_spell_outside_combat",
                "parameters": {
                    "spell": "cure_wounds",
                    "target": "courier",
                    "slot_resource": "spell_slot_1",
                    "caster_state": "cleric",
                },
                "result": {"result_card": "HP restored 11; Healing dice 4, 4; 8 + (3)"},
            }
        ],
    }


@pytest.mark.parametrize("scenario_id", ["T1", "T2", "T3", "T4"])
def test_all_control_baselines_pass(scenario_id):
    scenario = next(s for s in scenarios() if s.id == scenario_id)
    assert all(c.status == "pass" for c in evaluate(scenario, observation()))


@pytest.mark.parametrize(
    "scenario_id,check,defect",
    [
        ("T1", "requested_cast_committed", "wrong_spell"),
        ("T1", "requested_cast_committed", "wrong_target"),
        ("T1", "requested_cast_committed", "wrong_slot"),
        ("T1", "slot_spent", "slot"),
        ("T1", "healing_matches_receipt", "hp"),
        ("T1", "narrated_healing", "number"),
        ("T2", "secret_not_narrated", "literal_leak"),
        ("T2", "secret_not_revealed", "revealed"),
        ("T3", "saved_fact_survives", "lost_fact"),
        ("T3", "answer_recalls_fact", "wrong_signal"),
        ("T4", "compaction_exercised", "no_compaction"),
    ],
)
def test_every_check_has_a_broken_control(scenario_id, check, defect):
    data = observation()
    entities = data["after"]["state"][SRD + ".entities"]
    if defect.startswith("wrong_") and defect != "wrong_signal":
        field = {"wrong_spell": "spell", "wrong_target": "target", "wrong_slot": "slot_resource"}[
            defect
        ]
        data["mechanics"][0]["parameters"][field] = "wrong"
    elif defect == "slot":
        entities["cleric"]["resources"]["spell_slot_1"] = 2
    elif defect == "hp":
        entities["courier"]["hp"] = 31
    elif defect == "number":
        data["after"]["talks"][-1] = "Restoring 12 HP."
    elif defect == "literal_leak":
        data["after"]["talks"][-1] = SECRET
    elif defect == "revealed":
        data["after"]["facts"][1]["visibility"] = "player_known"
    elif defect == "lost_fact":
        data["reopened"]["facts"] = []
    elif defect == "wrong_signal":
        data["after"]["talks"][-1] = "Use a red flag at the east gate."
    else:
        data["reopened"]["compaction_count"] = 0
    scenario = next(s for s in scenarios() if s.id == scenario_id)
    assert next(c for c in evaluate(scenario, data) if c.id == check).status == "fail"


def test_no_numeric_narration_can_pass_without_relaxing_mechanics():
    data = observation()
    data["after"]["talks"][-1] = "His wounds close."
    checks = evaluate(scenarios()[0], data)
    assert checks[-1].status == "not_applicable"
    assert all(c.status in {"pass", "not_applicable"} for c in checks)


def test_service_denials_and_benchmark_limits_are_not_stuck_gm_turns():
    for cause in ("service_failure", "benchmark_fault"):
        assert breakers([], False, retries_exhausted=True, cause=cause) == []
    observer = ObservedResolver(SimpleNamespace())
    observer.failure(RateLimitError("private supplier text", retry_after=3600))
    assert observer.failures[-1]["pause_batch"]
    assert observer.failures[-1]["cause"] == "service_failure"
    observer.failure(BenchmarkRequestLimitError("limit"))
    assert observer.failures[-1]["cause"] == "benchmark_fault"
    assert "private supplier text" not in str(observer.failures)


def test_deadline_bounds_each_request_and_closes_stream():
    closed = []

    class Provider:
        async def generate_stream(self, *_args, **_kwargs):
            try:
                await asyncio.sleep(10)
                yield ProviderStreamChunk(content="late")
            finally:
                closed.append(True)

    class Resolver:
        def __call__(self, _session):
            return Provider()

        def model_choices(self):
            return ()

    observer = ObservedResolver(Resolver())
    observer.request_seconds = 0.01
    provider = observer(SimpleNamespace())

    async def consume():
        return [c async for c in provider.generate_stream([])]

    with pytest.raises(BenchmarkRequestTimeoutError):
        asyncio.run(consume())
    assert closed == [True]
    assert observer.failures[-1]["cause"] == "benchmark_fault"
    observer.requests = [{} for _ in range(MAX_REQUESTS)]
    with pytest.raises(BenchmarkRequestLimitError):
        provider.request([], None, {})


def test_survey_is_neutral_and_old_trials_cannot_receive_a_reveal(tmp_path, monkeypatch):
    from sylviabench import debrief

    assert "scored gameplay has concluded" in SURVEY
    assert all(f"{i}. " in SURVEY for i in range(1, 7))
    assert "not your authored" in SURVEY
    assert "Do not assess this survey reply as gameplay" in SURVEY
    assert "after the tested gameplay" in SURVEY
    monkeypatch.setattr(debrief, "load_results", lambda _: ({"protocol": "old"}, []))
    with pytest.raises(ValueError, match="original trials"):
        collect(tmp_path / "old", tmp_path / "survey")
    assert not (tmp_path / "survey").exists()


def test_copying_a_frozen_wal_database_does_not_write_to_its_directory(tmp_path):
    import sqlite3
    from contextlib import closing

    from sylviabench.fixtures import copy_save
    from sylviabench.packs import inventory

    source = tmp_path / "frozen/campaign.sqlite3"
    source.parent.mkdir()
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("pragma journal_mode=wal")
        connection.execute("create table witness (value integer)")
        connection.execute("insert into witness values (17)")
        connection.commit()
    before = inventory(source.parent)
    target = tmp_path / "run/campaign.sqlite3"
    copy_save(source, target)
    assert inventory(source.parent) == before
    with closing(sqlite3.connect(target)) as copied:
        assert copied.execute("select value from witness").fetchone() == (17,)
    wal = source.with_name(source.name + "-wal")
    wal.write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="checkpoint"):
        copy_save(source, tmp_path / "unsafe/campaign.sqlite3")
    assert not (tmp_path / "unsafe").exists()


def test_survey_rejects_incomplete_batches_and_requires_live_opt_in(tmp_path, monkeypatch):
    from sylviabench import debrief

    model = SimpleNamespace(setup=SimpleNamespace(kind="model"))
    manifest = {
        "protocol": "sylviabench-gameplay-v2",
        "setups": [{}],
        "scenarios": [{}],
        "repetitions": 2,
    }
    monkeypatch.setattr(debrief, "load_results", lambda _: (manifest, [model]))
    with pytest.raises(ValueError, match="entire scored batch"):
        collect(tmp_path / "gameplay", tmp_path / "survey")
    manifest["repetitions"] = 1
    with pytest.raises(ValueError, match="allow-model-calls"):
        collect(tmp_path / "gameplay", tmp_path / "survey")
    assert not (tmp_path / "survey").exists()


def test_batch_rotates_order_and_pauses_before_using_another_setup(tmp_path, monkeypatch):
    import hashlib
    import sqlite3
    from contextlib import closing

    from sylviabench import runner
    from sylviabench.contracts import Attempt, Check, Result, Setup, digest, read_json

    fixture = tmp_path / "fixture"
    save = fixture / "save/campaign.sqlite3"
    save.parent.mkdir(parents=True)
    with closing(sqlite3.connect(save)) as connection:
        connection.execute("create table synthetic (value)")
        connection.commit()
    source = {"application_sha256": "app", "framework_sha256": "frame", "commit": "source"}
    descriptor = {
        "recipe_version": 4,
        "sha256": "pack",
        "ids": {"campaign": "campaign", "session": "session"},
        "observation": {},
    }
    monkeypatch.setattr(runner, "source_identity", lambda: source)
    monkeypatch.setattr(runner, "verify", lambda *_: descriptor)
    monkeypatch.setattr(runner, "configuration", lambda _: None)
    selected = [Setup(id=c, name=c) for c in "abc"]
    scenario = scenarios()[1]
    calls = []
    pause = False

    def execute(
        case, setup, repetition, directory, frozen, ids, suite_id, actual_source, retries, **_kwargs
    ):
        calls.append((setup.id, repetition))
        return Result(
            run_id=directory.name,
            suite_id=suite_id,
            source=actual_source,
            scenario_id=case.id,
            scenario_version=case.version,
            scenario_sha256=digest(case.model_dump()),
            fixture_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
            category=case.category,
            repetition=repetition,
            execution_kind="framework_selftest",
            setup=setup,
            started_at="2026-11-11T00:00:00Z",
            elapsed_seconds=0.0,
            attempts=[Attempt(turn=0, number=0, elapsed_seconds=0.0, completed=False)],
            checks=[
                Check(id=c, status="not_evaluated", reason="Synthetic schedule probe")
                for c in case.checks
            ],
            outcome="incomplete",
            observations={"provider": {"failures": [{"pause_batch": pause}]}},
        )

    monkeypatch.setattr(runner, "execute", execute)
    runner.run(tmp_path / "balanced", selected, [scenario], repetitions=3, fixtures=fixture)
    assert calls == [(c, r) for r, order in enumerate(("abc", "bca", "cab")) for c in order]
    calls.clear()
    pause = True
    with pytest.raises(ValueError, match="Batch paused"):
        runner.run(tmp_path / "paused", selected, [scenario], fixtures=fixture)
    assert calls == [("a", 0)]
    # The run the limit interrupted is evidence, not a result, so resuming retries it.
    assert not list((tmp_path / "paused/results").glob("*.json"))
    assert len(list((tmp_path / "paused/paused").glob("*.json"))) == 1
    assert read_json(tmp_path / "paused/pause.json")["reason"] == "provider_usage_reset"
    calls.clear()
    pause = False
    resumed = runner.run(tmp_path / "paused", selected, [scenario], fixtures=fixture, resume=True)
    assert calls == [("a", 0), ("b", 0), ("c", 0)]
    assert len(resumed) == len(list((tmp_path / "paused/results").glob("*.json"))) == 3
    assert not (tmp_path / "paused/pause.json").exists()
    assert len(list((tmp_path / "paused/paused").glob("*.json"))) == 1


def test_deadline_passing_between_chunks_is_a_benchmark_timeout_not_a_cancellation():
    class Provider:
        async def generate_stream(self, *_args, **_kwargs):
            yield ProviderStreamChunk(content="first")
            await asyncio.sleep(10)
            yield ProviderStreamChunk(content="late")

    class Resolver:
        def __call__(self, _session):
            return Provider()

        def model_choices(self):
            return ()

    observer = ObservedResolver(Resolver())
    observer.request_seconds = 0.05
    provider = observer(SimpleNamespace())
    seen = []

    async def consume():
        async for chunk in provider.generate_stream([]):
            seen.append(chunk.content)
            await asyncio.sleep(0.2)  # The consumer is busy when the deadline passes.

    with pytest.raises(BenchmarkRequestTimeoutError):
        asyncio.run(consume())
    assert seen == ["first"]
    assert observer.failures[-1]["cause"] == "benchmark_fault"


def test_a_tool_result_without_a_matched_action_is_ignored_not_fatal():
    data = observation()
    data["mechanics"].insert(
        0, {"action_id": "orphan", "tool_name": None, "parameters": {}, "result": {}}
    )
    assert all(c.status == "pass" for c in evaluate(scenarios()[0], data))
