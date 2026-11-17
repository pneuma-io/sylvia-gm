"""Stored canonical JSON decoded before is reused without changing any saved row or read."""

from __future__ import annotations

import itertools
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import FrozenJsonDict, FrozenJsonList, WriterRole
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import sqlite_authority
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore
from tests.test_alpha_f4_generic_sessions import _clockwork_campaign


def _typed(value: object) -> object:
    """A strict form of a decoded value: 1, 1.0 and True, or a list and a tuple, differ."""
    if isinstance(value, FrozenJsonDict):
        return ("object", tuple((key, _typed(item)) for key, item in value.items()))
    if isinstance(value, FrozenJsonList):
        return ("array", tuple(_typed(item) for item in value))
    return (type(value).__name__, value)


def _saved_rows(database: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = sqlite3.connect(database)
    try:
        tables = [
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: sorted(
                (tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')), key=repr,
            )
            for table in tables
        }
    finally:
        connection.close()


def _play_scripted_session(
    database: Path, monkeypatch: pytest.MonkeyPatch, *, reuse: bool,
) -> list[tuple[str, str]]:
    """One scripted GM session in the previous request assembly; every snapshot is recorded."""
    from sylvia_gm.agent._activity import AttemptProgress
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest

    monkeypatch.delenv("SYLVIA_GM_CONTEXT_MODE", raising=False)
    # Saved activity timing is wall-clock data; a fixed clock makes two runs comparable.
    monkeypatch.setattr(AttemptProgress.__init__, "__defaults__", (lambda: 0.0,))
    # Generated identities (for example a saved interruption) are numbered in order instead.
    numbers = itertools.count(1)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(numbers)))
    sqlite_authority._CANONICAL_JSON.clear()
    fresh = sqlite_authority._decode_canonical_json
    parsed: list[int] = []
    if reuse:
        reused = sqlite_authority._parse_canonical_json
        decodes: list[int] = []

        def counted(value: str) -> object:
            decodes.append(len(value))
            return fresh(value)

        monkeypatch.setattr(sqlite_authority, "_decode_canonical_json", counted)

        def checked(value: str) -> object:
            result = reused(value)
            assert _typed(result) == _typed(fresh(value))
            parsed.append(len(value))
            return result

        monkeypatch.setattr(sqlite_authority, "_parse_canonical_json", checked)
    else:
        def decoded(value: str) -> object:
            parsed.append(len(value))
            return fresh(value)

        monkeypatch.setattr(sqlite_authority, "_parse_canonical_json", decoded)
    records: list[tuple[str, str]] = []
    read_snapshot = SQLiteAuthorityStore._read_snapshot
    session_context = SQLiteAuthorityStore.session_context

    def recorded_snapshot(self, campaign_id, **options):  # type: ignore[no-untyped-def]
        result = read_snapshot(self, campaign_id, **options)
        records.append(("snapshot", repr(result)))
        return result

    def recorded_context(self, session_id):  # type: ignore[no-untyped-def]
        result = session_context(self, session_id)
        records.append(("session", repr(result)))
        return result

    monkeypatch.setattr(SQLiteAuthorityStore, "_read_snapshot", recorded_snapshot)
    monkeypatch.setattr(SQLiteAuthorityStore, "session_context", recorded_context)
    calls: list[int] = []
    with AuthorityOwner.open(database, "owner", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id="campaign", writer=WriterRole.DM,
                            turn_id="turn", calls=calls)
        owner.create_agent_session(campaign_id="campaign", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic", session_id="gm")
        entry = owner.current_package_lock("campaign").package_lock.entries[0]
        loop = CampaignSessionLoop(owner)

        def advancing(steps: int, index: int):  # type: ignore[no-untyped-def]
            def provider(_input: object) -> object:
                yield ProviderEvent.safe_think(f"Check the clock before round {index}.")
                yield ProviderEvent.action(ActionCommand(
                    campaign_id="campaign", action_id=f"advance-{index}",
                    idempotency_key=f"advance-{index}", owner_id="owner", writer=WriterRole.DM,
                    expected_revision=owner.snapshot("campaign").revision, turn_id="turn",
                    package_id=entry.package_id, tool_name="advance",
                    parameters={"steps": steps},
                ))
                yield ProviderEvent.talk(f"The clock moves on in round {index}.")
            return provider

        def failing(_input: object) -> object:
            raise RuntimeError("Synthetic failure before mechanics")
            yield

        for index in range(4):
            loop.run(SessionRunRequest(
                session_id="gm", turn_id="turn", attempt_id=f"attempt-{index}",
                message_id=f"input-{index}", user_text=f"Advance the clock, round {index}.",
            ), advancing(index + 1, index))
            owner.append_agent_message(
                session_id="gm", message_id=f"evidence-{index}", kind=SessionMessageKind.EVIDENCE,
                content={"kind": "scripted_probe", "round": index, "ratio": index / 4,
                         "flags": [True, None, 1, 1.0], "text": "x" * 200},
            )
            owner.session_context("gm")
        with pytest.raises(RuntimeError, match="before mechanics"):
            loop.run(SessionRunRequest(
                session_id="gm", turn_id="turn", attempt_id="attempt-failed",
                message_id="input-failed", user_text="Advance again.",
            ), failing)
        loop.retry("gm", retry_id="retry", narrator=lambda *_: None,
                   provider_factory=lambda _: advancing(5, 99))
        owner.snapshot("campaign")
        owner.session_context("gm")
    with AuthorityOwner.open(database, "reopened") as owner:
        owner.snapshot("campaign")
        owner.session_context("gm")
    assert calls == [1, 2, 3, 4, 5]
    assert len(parsed) > 100
    if reuse:
        # Most stored values were decoded before; only new and changed ones decode again.
        assert len(decodes) < len(parsed) // 2
    return records


def test_scripted_session_saves_and_reads_exactly_the_same_with_reused_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = tmp_path / "fresh" / "campaign.sqlite3"
    reused = tmp_path / "reused" / "campaign.sqlite3"
    fresh.parent.mkdir()
    reused.parent.mkdir()
    fresh_records = _play_scripted_session(fresh, monkeypatch, reuse=False)
    monkeypatch.undo()
    reused_records = _play_scripted_session(reused, monkeypatch, reuse=True)
    assert sum(kind == "snapshot" for kind, _ in fresh_records) > 10
    assert sum(kind == "session" for kind, _ in fresh_records) > 10
    assert reused_records == fresh_records
    assert _saved_rows(reused) == _saved_rows(fresh)


def test_identical_input_returns_the_same_deeply_frozen_value() -> None:
    reuse = sqlite_authority._CanonicalJsonReuse(maximum_characters=10_000)
    text = (
        '{"a":[1,1.0,-0.5,1e+300,true,false,null,"été",[[],{}],{"b":[2.25]}],'
        '"z":{"y":{"x":[0]}}}'
    )
    first = reuse.decode(text)
    assert reuse.decode(text) is first
    assert _typed(first) == _typed(sqlite_authority._decode_canonical_json(text))

    def frozen(value: object) -> None:
        if isinstance(value, FrozenJsonDict):
            with pytest.raises(TypeError):
                value["new"] = 1  # type: ignore[index]
            with pytest.raises(TypeError):
                value.update({"new": 1})
            with pytest.raises(TypeError):
                value.pop("a")
            for item in value.values():
                frozen(item)
        elif isinstance(value, FrozenJsonList):
            with pytest.raises(TypeError):
                value.append(1)
            with pytest.raises(TypeError):
                value[0:0] = [1]  # type: ignore[index]
            with pytest.raises(TypeError):
                value += [1]
            for item in value:
                frozen(item)
        else:
            assert value is None or type(value) in (str, int, float, bool)

    frozen(first)
    assert isinstance(first, FrozenJsonDict)


@pytest.mark.parametrize("text", [
    '{ "a": 1 }',          # not the canonical spelling
    '{"b":1,"a":2}',       # keys out of order
    '{"a":1,"a":1}',       # duplicate member
    '{"a":NaN}',           # non-standard constant
    '{"a":1e999}',         # overflows to a non-finite number
    '{"a":',               # not JSON
])
def test_an_input_that_fails_fails_every_time(
    text: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reuse = sqlite_authority._CanonicalJsonReuse(maximum_characters=10_000)
    attempts: list[str] = []
    fresh = sqlite_authority._decode_canonical_json

    def counted(value: str) -> object:
        attempts.append(value)
        return fresh(value)

    monkeypatch.setattr(sqlite_authority, "_decode_canonical_json", counted)
    for expected in (1, 2, 3):
        with pytest.raises((ValueError, TypeError)):
            reuse.decode(text)
        assert len(attempts) == expected
    assert reuse.decode('{"a":1}') == {"a": 1}


@pytest.mark.parametrize(("left", "right"), [
    ('{"a":1,"b":2}', '{"a":2,"b":1}'),     # same length and characters, other order
    ('{"a":1}', '{"a":2}'),                 # same length
    ('{"v":10}', '{"v":100}'),              # prefix of a longer input
    ('[1]', '[1.0]'),                       # equal numbers of different types
    ('[true]', '[1]'),
])
def test_different_inputs_never_share_a_result(left: str, right: str) -> None:
    reuse = sqlite_authority._CanonicalJsonReuse(maximum_characters=10_000)
    for text in (left, right, left, right):
        assert _typed(reuse.decode(text)) == _typed(sqlite_authority._decode_canonical_json(text))


def test_kept_inputs_stay_within_the_bound_least_recently_used_first() -> None:
    charge = sqlite_authority._CanonicalJsonReuse.ENTRY_CHARGE
    reuse = sqlite_authority._CanonicalJsonReuse(maximum_characters=3 * (charge + 10))
    texts = [f'{{"k":"{index:0>2}"}}' for index in range(5)]  # ten characters each
    assert all(len(text) == 10 for text in texts)
    first = reuse.decode(texts[0])
    reuse.decode(texts[1])
    reuse.decode(texts[2])
    assert reuse.decode(texts[0]) is first  # texts[0] is now the most recently used
    reuse.decode(texts[3])  # evicts texts[1]
    assert reuse._charged == 3 * (charge + 10)
    assert set(reuse._values) == {texts[0], texts[2], texts[3]}
    assert reuse.decode(texts[0]) is first
    oversized = '{"k":"' + "x" * (4 * charge) + '"}'
    assert reuse.decode(oversized) == {"k": "x" * (4 * charge)}
    assert oversized not in reuse._values
    assert reuse._charged <= 3 * (charge + 10)
    reuse.clear()
    assert reuse._charged == 0 and not reuse._values


def test_concurrent_decodes_always_return_the_fresh_value() -> None:
    reuse = sqlite_authority._CanonicalJsonReuse(maximum_characters=40 * 230)
    texts = [f'{{"index":{index},"values":[{index},{index}.5,"t{index}"]}}' for index in range(120)]
    expected = {text: _typed(sqlite_authority._decode_canonical_json(text)) for text in texts}
    failures: list[BaseException] = []

    def work(offset: int) -> None:
        try:
            for round_ in range(30):
                for text in texts[offset::3] + texts[::-7]:
                    assert _typed(reuse.decode(text)) == expected[text], (round_, text)
        except BaseException as error:  # pragma: no cover - reported below
            failures.append(error)

    threads = [threading.Thread(target=work, args=(offset % 3,)) for offset in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not failures, failures
    assert reuse._charged == sum(size for size, _ in reuse._values.values())
    assert reuse._charged <= 40 * 230
