"""Reading a saved session reuses decoded rows without ever changing what a read returns."""

from __future__ import annotations

import itertools
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.session import SessionMessage, SessionMessageKind
from sylvia_gm.infrastructure import sqlite_session
from tests.test_alpha_f4_generic_sessions import _clockwork_campaign


def _decode_every_row(
    connection: sqlite3.Connection, session_id: str,
) -> tuple[SessionMessage, ...]:
    """The full read as it was before decoded rows were reused: decode every saved row."""
    session = sqlite_session.read_session(connection, session_id)
    table = (
        "standalone_builder_chat_messages"
        if session.campaign_id is None
        else "agent_session_messages"
    )
    return tuple(
        sqlite_session.message_from_row(row)
        for row in connection.execute(
            f"SELECT * FROM {table} WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        )
    )


def _thawed(messages: tuple[SessionMessage, ...]) -> list[object]:
    return [
        [m.session_id, m.sequence, m.message_id, m.kind.value, thaw_json_value(m.content),
         m.canonical_sequence]
        for m in messages
    ]


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
    database: Path, monkeypatch: pytest.MonkeyPatch, *, reuse_decoded_rows: bool,
) -> list[object]:
    """One scripted GM session in the previous request assembly; every read is recorded."""
    from sylvia_gm.agent._activity import AttemptProgress
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest

    monkeypatch.delenv("SYLVIA_GM_CONTEXT_MODE", raising=False)
    # Saved activity timing is wall-clock data; a fixed clock makes two runs comparable.
    monkeypatch.setattr(AttemptProgress.__init__, "__defaults__", (lambda: 0.0,))
    # Generated identities (for example a saved interruption) are numbered in order instead.
    numbers = itertools.count(1)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(numbers)))
    sqlite_session._DECODED_MESSAGES.clear()
    current = sqlite_session.read_messages
    reads: list[object] = []

    def recorded(connection: sqlite3.Connection, session_id: str) -> tuple[SessionMessage, ...]:
        if reuse_decoded_rows:
            result = current(connection, session_id)
            # Same connection, same transaction: the reused result is the full decode.
            assert result == _decode_every_row(connection, session_id)
        else:
            result = _decode_every_row(connection, session_id)
        reads.append((session_id, _thawed(result)))
        return result

    monkeypatch.setattr(sqlite_session, "read_messages", recorded)
    calls: list[int] = []
    with AuthorityOwner.open(database, "owner", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id="campaign", writer=WriterRole.DM,
                            turn_id="turn", calls=calls)
        owner.create_agent_session(campaign_id="campaign", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic", session_id="gm")
        entry = owner.current_package_lock("campaign").package_lock.entries[0]
        loop = CampaignSessionLoop(owner)

        def advancing(steps: int, index: int):
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
                content={"kind": "scripted_probe", "round": index, "text": "x" * 200},
            )
            owner.session_context("gm")
        with pytest.raises(RuntimeError, match="before mechanics"):
            loop.run(SessionRunRequest(
                session_id="gm", turn_id="turn", attempt_id="attempt-failed",
                message_id="input-failed", user_text="Advance again.",
            ), failing)
        loop.retry("gm", retry_id="retry", narrator=lambda *_: None,
                   provider_factory=lambda _: advancing(5, 99))
        reads.append(("final", _thawed(owner.session_context("gm").messages)))
    with AuthorityOwner.open(database, "reopened") as owner:
        reads.append(("reopened", _thawed(owner.session_context("gm").messages)))
    assert calls == [1, 2, 3, 4, 5]
    return reads


def test_scripted_session_saves_and_reads_exactly_the_same_with_reused_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = tmp_path / "full" / "campaign.sqlite3"
    reused = tmp_path / "reused" / "campaign.sqlite3"
    full.parent.mkdir()
    reused.parent.mkdir()
    full_reads = _play_scripted_session(full, monkeypatch, reuse_decoded_rows=False)
    monkeypatch.undo()
    reused_reads = _play_scripted_session(reused, monkeypatch, reuse_decoded_rows=True)
    assert len(full_reads) > 20
    assert reused_reads == full_reads
    assert _saved_rows(reused) == _saved_rows(full)


def _chat(connection: sqlite3.Connection, session_id: str = "chat") -> None:
    sqlite_session.insert_builder_chat(
        connection, session_id=session_id, provider_id="test", model_id="test",
        display_name="Chat",
    )


def _append(connection: sqlite3.Connection, message_id: str, text: str,
            session_id: str = "chat") -> SessionMessage:
    return sqlite_session.append_message(
        connection, session_id=session_id, message_id=message_id,
        kind=SessionMessageKind.USER, content={"text": text},
    )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "reads.sqlite3"
    with AuthorityOwner.open(path, "schema-owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="seed")
    sqlite_session._DECODED_MESSAGES.clear()
    return path


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _assert_full(connection: sqlite3.Connection, session_id: str = "chat") -> tuple[
    SessionMessage, ...
]:
    result = sqlite_session.read_messages(connection, session_id)
    assert result == _decode_every_row(connection, session_id)
    return result


def test_a_later_read_decodes_only_the_rows_it_has_not_seen(
    database: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connect(database)
    try:
        _chat(connection)
        for index in range(30):
            _append(connection, f"m{index}", f"text {index}")
        first = _assert_full(connection)
        decoded: list[int] = []
        original = sqlite_session.message_from_row

        def counting(row: sqlite3.Row) -> SessionMessage:
            decoded.append(int(row["sequence"]))
            return original(row)

        monkeypatch.setattr(sqlite_session, "message_from_row", counting)
        _append(connection, "m30", "text 30")
        second = sqlite_session.read_messages(connection, "chat")
        assert decoded == [31]
        assert second[:-1] == first and len(second) == 31
        # Returned messages are shared but deeply immutable.
        with pytest.raises(TypeError):
            second[0].content["text"] = "changed"  # type: ignore[index]
    finally:
        connection.close()


def test_a_rolled_back_row_is_never_returned_and_its_sequence_can_be_reused(
    database: Path,
) -> None:
    connection = _connect(database)
    try:
        _chat(connection)
        _append(connection, "kept", "kept")
        connection.execute("BEGIN IMMEDIATE")
        _append(connection, "abandoned", "rolled back")
        inside = _assert_full(connection)
        assert [m.message_id for m in inside] == ["kept", "abandoned"]
        connection.rollback()
        # No read in between: the next row takes the abandoned row's sequence.
        _append(connection, "replacement", "different text")
        replaced = _assert_full(connection)
        assert [(m.sequence, m.message_id, m.content["text"]) for m in replaced] == [
            (1, "kept", "kept"), (2, "replacement", "different text"),
        ]
    finally:
        connection.close()


def test_deleted_rewritten_and_recreated_rows_are_read_as_saved(database: Path) -> None:
    connection = _connect(database)
    try:
        _chat(connection)
        for index in range(3):
            _append(connection, f"m{index}", f"text {index}")
        _assert_full(connection)
        rolled_back = _connect(database)
        try:
            # Another connection's abandoned write is decoded, then never saved.
            rolled_back.execute("BEGIN IMMEDIATE")
            _append(rolled_back, "abandoned", "rolled back")
            assert len(_assert_full(rolled_back)) == 4
            rolled_back.rollback()
        finally:
            rolled_back.close()
        _append(connection, "m3", "saved instead")
        assert [m.content["text"] for m in _assert_full(connection)] == [
            "text 0", "text 1", "text 2", "saved instead",
        ]
        connection.execute(
            "UPDATE standalone_builder_chat_messages SET content_json = ? "
            "WHERE session_id = 'chat' AND sequence = 2",
            ('{"text":"rewritten"}',),
        )
        assert [m.content["text"] for m in _assert_full(connection)] == [
            "text 0", "rewritten", "text 2", "saved instead",
        ]
        connection.execute(
            "DELETE FROM standalone_builder_chat_messages "
            "WHERE session_id = 'chat' AND sequence = 3"
        )
        assert [m.message_id for m in _assert_full(connection)] == ["m0", "m1", "m3"]
        sqlite_session.delete_builder_chat(connection, "chat")
        with pytest.raises(Exception, match="unavailable"):
            sqlite_session.read_messages(connection, "chat")
        _chat(connection)
        _append(connection, "fresh", "a new chat with the same identity")
        recreated = _assert_full(connection)
        assert [(m.sequence, m.message_id) for m in recreated] == [(1, "fresh")]
    finally:
        connection.close()


def test_a_corrupted_saved_row_still_fails_the_read(database: Path) -> None:
    connection = _connect(database)
    try:
        _chat(connection)
        _append(connection, "m0", "text")
        _assert_full(connection)
        connection.execute(
            "UPDATE standalone_builder_chat_messages SET content_json = ? "
            "WHERE session_id = 'chat'",
            ('{ "text": "text" }',),
        )
        with pytest.raises(ValueError, match="canonical"):
            sqlite_session.read_messages(connection, "chat")
        connection.execute(
            "UPDATE standalone_builder_chat_messages SET content_json = ? "
            "WHERE session_id = 'chat'",
            ('{"text":"text"}',),
        )
        assert [m.kind for m in _assert_full(connection)] == [SessionMessageKind.USER]
        connection.execute(
            "UPDATE standalone_builder_chat_messages SET kind = 'talk' WHERE session_id = 'chat'",
        )
        assert [m.kind for m in _assert_full(connection)] == [SessionMessageKind.TALK]
    finally:
        connection.close()


def test_rows_written_by_another_connection_are_read_as_saved(database: Path) -> None:
    reader = _connect(database)
    writer = _connect(database)
    try:
        _chat(writer)
        _append(writer, "m0", "first")
        assert [m.message_id for m in _assert_full(reader)] == ["m0"]
        _append(writer, "m1", "second")
        writer.execute(
            "UPDATE standalone_builder_chat_messages SET content_json = ? "
            "WHERE session_id = 'chat' AND sequence = 1",
            ('{"text":"first, edited elsewhere"}',),
        )
        assert [m.content["text"] for m in _assert_full(reader)] == [
            "first, edited elsewhere", "second",
        ]
    finally:
        reader.close()
        writer.close()


def test_a_read_during_another_connections_write_sees_only_committed_rows(
    database: Path,
) -> None:
    reader = _connect(database)
    writer = _connect(database)
    try:
        _chat(writer)
        _append(writer, "m0", "committed")
        writer.execute("BEGIN IMMEDIATE")
        _append(writer, "m1", "not yet committed")
        # The writer's own read caches its uncommitted row ...
        assert [m.message_id for m in _assert_full(writer)] == ["m0", "m1"]
        # ... but another connection's read returns only what is committed.
        assert [m.message_id for m in _assert_full(reader)] == ["m0"]
        reader.execute("BEGIN")
        assert [m.message_id for m in _assert_full(reader)] == ["m0"]
        reader.commit()
        # The writer decodes its own row again, then abandons the whole write.
        assert [m.message_id for m in _assert_full(writer)] == ["m0", "m1"]
        writer.rollback()
        writer.execute("BEGIN IMMEDIATE")
        _append(writer, "m1-other", "a different second row")
        writer.commit()
        # No read since the abandoned write: the committed row is the one returned.
        assert [m.message_id for m in _assert_full(reader)] == ["m0", "m1-other"]
        reader.execute("BEGIN")
        assert [m.message_id for m in _assert_full(reader)] == ["m0", "m1-other"]
        _append(writer, "m2", "committed after the read began")
        # A read transaction opened before the commit keeps its snapshot.
        assert [m.message_id for m in _assert_full(reader)] == ["m0", "m1-other"]
        reader.commit()
        assert [m.message_id for m in _assert_full(reader)] == ["m0", "m1-other", "m2"]
    finally:
        reader.close()
        writer.close()


def test_concurrent_reads_and_appends_always_return_the_full_decode(database: Path) -> None:
    setup = _connect(database)
    _chat(setup)
    setup.close()
    failures: list[BaseException] = []
    stop = threading.Event()

    def append_rows() -> None:
        connection = _connect(database)
        try:
            for index in range(60):
                connection.execute("BEGIN IMMEDIATE")
                _append(connection, f"m{index}", f"row {index}")
                if index % 7 == 3:
                    connection.rollback()
                else:
                    connection.commit()
        except BaseException as error:  # pragma: no cover - reported below
            failures.append(error)
        finally:
            stop.set()
            connection.close()

    def read_rows() -> None:
        connection = _connect(database)
        try:
            while not stop.is_set():
                connection.execute("BEGIN")
                _assert_full(connection)
                connection.commit()
        except BaseException as error:  # pragma: no cover - reported below
            failures.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=append_rows)] + [
        threading.Thread(target=read_rows) for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not failures, failures
    final = _connect(database)
    try:
        assert len(_assert_full(final)) == 60 - len([i for i in range(60) if i % 7 == 3])
    finally:
        final.close()


def test_the_decoded_row_store_stays_within_its_bounds() -> None:
    cache = sqlite_session._DecodedMessageCache(maximum_sessions=2, maximum_characters=100)
    cache.put("a", {1: ("key", "a")}, 10)  # type: ignore[dict-item]
    cache.put("b", {1: ("key", "b")}, 10)  # type: ignore[dict-item]
    assert cache.get("a")  # "a" is now the most recently used
    cache.put("c", {1: ("key", "c")}, 10)  # type: ignore[dict-item]
    assert cache.get("b") == {} and cache.get("a") and cache.get("c")
    cache.put("d", {1: ("key", "d")}, 95)  # type: ignore[dict-item]
    assert cache.get("a") == {} and cache.get("c") == {} and cache.get("d")
    cache.put("e", {1: ("key", "e")}, 101)  # type: ignore[dict-item]
    assert cache.get("e") == {} and cache.get("d")
    cache.put("d", {}, 0)
    assert cache.get("d") == {}
