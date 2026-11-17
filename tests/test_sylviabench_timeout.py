"""A transport cutoff preserves the first failed attempt without inventing a safe retry."""

import json
import sqlite3
from contextlib import closing

from sylviabench import runner
from sylviabench.contracts import read_json, scenarios, setups


def test_caller_timeout_is_journalled_and_does_not_retry_an_uncertain_owner(tmp_path, monkeypatch):
    database = tmp_path / "seed/campaign.sqlite3"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("create table synthetic (value)")
        connection.commit()
    baseline = {"talks": [], "mechanics": []}
    monkeypatch.setattr(runner, "observe", lambda *_args, **_kwargs: baseline)

    class Authority:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def update_agent_session_model(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(runner.AuthorityOwner, "open", lambda *_args: Authority())
    calls = []

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def request(self, method, *_args, **_kwargs):
            calls.append(method)
            if method == "display_snapshot":
                return {"turns": {}, "revision": 0}
            if method == "submit_session_input":
                raise TimeoutError("Owner command exceeded its configured timeout")
            return {}

    monkeypatch.setattr(runner, "OwnerClient", Client)
    directory = tmp_path / "run"
    result = runner.execute(
        next(s for s in scenarios() if s.id == "T2"),
        setups()[0],
        0,
        directory,
        database,
        {"campaign": "campaign", "session": "session"},
        "suite",
        {},
        2,
    )
    assert len(result.attempts) == 1
    assert result.attempts[0].number == 0 and not result.attempts[0].completed
    assert read_json(directory / "attempts.json") == [result.attempts[0].model_dump()]
    assert result.outcome == "error" and result.failure_cause == "benchmark_fault"
    assert result.session_breakers == []
    assert "retryable_interruption" not in calls and "manual_retry_session" not in calls
    assert "TimeoutError" in json.dumps(result.model_dump())
