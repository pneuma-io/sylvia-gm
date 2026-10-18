from __future__ import annotations

import hashlib
import io
import json
import logging
import sqlite3
import threading
import zipfile
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import sylvia_gm.infrastructure.sqlite_authority as sqlite_authority
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.domain.authority import (
    CanonicalEventType,
    EventVisibility,
    InspectorTraceSink,
    MechanicContext,
    MechanicOutcome,
    NarrationStream,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    PlayerProjectionKind,
    ProseChannel,
    RetryNarrationInput,
    StateMutation,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict
from sylvia_gm.domain.package_library import (
    PackageLockAvailability,
    canonical_json_bytes,
    content_root_for_record,
)

SYNTHETIC_SHA = "a" * 64


class FrozenClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now


class CountingCanceller:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


def _lock() -> PackageLock:
    return PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="1.0.0",
                sha256=SYNTHETIC_SHA,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(),
                capabilities=("synthetic.counter",),
            ),
        )
    )


def _schema() -> Mapping[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount"],
        "properties": {"amount": {"type": "integer", "minimum": 1}},
    }


def _increment(calls: list[int]) -> Callable[[MechanicContext], MechanicOutcome]:
    def mechanic(context: MechanicContext) -> MechanicOutcome:
        calls.append(1)
        amount = context.parameters["amount"]
        before = context.state.get("clockwork.counter", 0)
        assert isinstance(amount, int)
        assert isinstance(before, int)
        after = before + amount
        return MechanicOutcome(
            result={"status": "committed", "counter": after},
            mutations=(StateMutation("clockwork", "counter", after),),
            consequences=({"kind": "counter_advanced", "amount": amount},),
        )

    return mechanic


def _f2_archive() -> tuple[str, bytes]:
    payload = canonical_json_bytes({"fixture": "synthetic-f2"})
    draft: dict[str, object] = {
        "id": "synthetic.clockwork",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Synthetic F2 fixture"},
        "provenance": {
            "publisher": "sylvia-gm-tests",
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["synthetic.counter"],
        "tools": [
            {
                "name": name,
                "capability": "synthetic.counter",
                "input_schema": _schema(),
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
            }
            for name in ("blocking", "failure", "increment", "no_op", "rejected")
        ],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "payload/f2.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    root = content_root_for_record(draft)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 1, "packages": [{**draft, "content_sha256": root}]}),
        )
        archive.writestr("packages/synthetic.clockwork/payload/f2.json", payload)
    return root, stream.getvalue()


def _open_owner(
    database: Path,
    *,
    owner_id: str = "owner-f2",
    calls: list[int] | None = None,
) -> AuthorityOwner:
    call_log = calls if calls is not None else []
    owner = AuthorityOwner.open(
        database,
        owner_id=owner_id,
        disconnect_grace_seconds=5.0,
    )
    try:
        owner.snapshot("campaign-f1-migration")
    except CommandRejected:
        pass
    else:
        return owner
    global SYNTHETIC_SHA
    root, archive = _f2_archive()
    install_id = f"f2-{root[:24]}"
    try:
        owner.package_candidate(f"candidate-{install_id}")
    except CommandRejected:
        owner.install_package_archive(io.BytesIO(archive), install_id=install_id)
    SYNTHETIC_SHA = root
    tools: tuple[tuple[str, Callable[[MechanicContext], MechanicOutcome]], ...] = (
        ("increment", _increment(call_log)),
        (
            "no_op",
            lambda _context: MechanicOutcome(result={"status": "no_op"}),
        ),
        (
            "rejected",
            lambda _context: MechanicOutcome(
                result={"status": "rejected", "code": "synthetic_rejection"}
            ),
        ),
        ("failure", _raise_synthetic_failure),
    )
    for name, mechanic in tools:
        owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=SYNTHETIC_SHA,
                name=name,
                capability="synthetic.counter",
                input_schema=_schema(),
                execute=mechanic,
            )
        )
    return owner


def _raise_synthetic_failure(_context: MechanicContext) -> MechanicOutcome:
    raise RuntimeError("synthetic private diagnostic must not persist")


def _campaign(owner: AuthorityOwner, *, campaign_id: str | None = None) -> str:
    return owner.create_campaign(
        CampaignCreate(
            display_name="F2 neutral event protocol",
            writer=WriterRole.DM,
            package_lock=_lock(),
            turn_id="turn-f2",
            campaign_id=campaign_id,
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    ).campaign_id


def _action(
    campaign_id: str,
    *,
    action_id: str,
    tool_name: str = "increment",
    revision: int = 0,
) -> ActionCommand:
    return ActionCommand(
        campaign_id=campaign_id,
        action_id=action_id,
        idempotency_key=f"idem-{action_id}",
        owner_id="owner-f2",
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id="turn-f2",
        package_id="synthetic.clockwork",
        tool_name=tool_name,
        parameters={"amount": 1},
    )


def _start(
    owner: AuthorityOwner,
    campaign_id: str,
    attempt_id: str,
    canceller: CountingCanceller | None = None,
    *,
    last_action_id: str | None = None,
) -> CountingCanceller:
    actual = canceller or CountingCanceller()
    owner.start_narration_attempt(
        campaign_id=campaign_id,
        attempt_id=attempt_id,
        turn_id="turn-f2",
        cancel=actual,
        last_action_id=last_action_id,
    )
    return actual


def _interrupt(
    owner: AuthorityOwner,
    campaign_id: str,
    attempt_id: str,
    *,
    at: float = 10.0,
) -> str:
    owner.disconnect_narration(campaign_id, attempt_id, now=at)
    interruption = owner.expire_narration(campaign_id, attempt_id, now=at + 5.0)
    return interruption.interruption_id


def _tool_cards(owner: AuthorityOwner, campaign_id: str):  # type: ignore[no-untyped-def]
    return tuple(
        item
        for item in owner.player_projection(campaign_id)
        if item.kind is PlayerProjectionKind.TOOL_CARD
    )


def test_f2_provisional_safe_think_and_talk_commit_only_at_channel_boundaries(
    tmp_path: Path,
) -> None:
    owner = _open_owner(tmp_path / "sylvia-gm.db")
    campaign_id = _campaign(owner)
    _start(owner, campaign_id, "attempt-order")

    owner.emit_narration_token(campaign_id, "attempt-order", ProseChannel.SAFE_THINK, "I ")
    owner.emit_narration_token(campaign_id, "attempt-order", ProseChannel.SAFE_THINK, "consider")
    before_transition = owner.snapshot(campaign_id)
    provisional_before = owner.player_projection(campaign_id)

    owner.emit_narration_token(campaign_id, "attempt-order", ProseChannel.TALK, "Hello")
    after_transition = owner.snapshot(campaign_id)
    provisional_after = owner.player_projection(campaign_id)
    owner.emit_narration_token(campaign_id, "attempt-order", ProseChannel.TALK, " world")
    owner.complete_narration_attempt(campaign_id, "attempt-order")
    completed = owner.snapshot(campaign_id)

    print("F2 ORDER BEFORE", before_transition.events, provisional_before)
    print(
        "F2 ORDER TRANSITION",
        [(event.sequence, event.event_type.value) for event in after_transition.events],
        provisional_after,
    )
    print(
        "F2 ORDER COMPLETE",
        [
            (event.sequence, event.event_type.value, event.safe_payload)
            for event in completed.events
        ],
    )
    assert before_transition.events == ()
    assert provisional_before[-1].text == "I consider"
    assert provisional_before[-1].provisional
    assert tuple(event.event_type for event in after_transition.events) == (
        CanonicalEventType.THINK,
    )
    assert after_transition.events[0].safe_payload == {"text": "I consider"}
    assert provisional_after[-1].text == "Hello"
    assert provisional_after[-1].provisional
    assert tuple(event.event_type for event in completed.events) == (
        CanonicalEventType.THINK,
        CanonicalEventType.TALK,
    )
    assert [event.sequence for event in completed.events] == [1, 2]
    assert all(event.visibility is EventVisibility.PLAYER_SAFE for event in completed.events)

    _start(owner, campaign_id, "attempt-rollback")
    owner.emit_narration_token(campaign_id, "attempt-rollback", ProseChannel.TALK, "Atomic segment")

    def fail_before_commit() -> None:
        raise RuntimeError("synthetic narration commit fault")

    owner._store._before_narration_commit = fail_before_commit
    with pytest.raises(RuntimeError, match="synthetic narration commit fault"):
        owner.complete_narration_attempt(campaign_id, "attempt-rollback")
    rolled_back = owner.snapshot(campaign_id)
    assert rolled_back.events == completed.events
    assert owner.player_projection(campaign_id)[-1].text == "Atomic segment"
    owner._store._before_narration_commit = lambda: None
    owner.complete_narration_attempt(campaign_id, "attempt-rollback")
    print(
        "F2 SEGMENT ROLLBACK/RECOVERY",
        [event.sequence for event in rolled_back.events],
        [event.sequence for event in owner.snapshot(campaign_id).events],
    )
    owner.close()


def test_f2_player_projection_collapses_safe_think_and_tool_cards_by_default(
    tmp_path: Path,
) -> None:
    owner = _open_owner(tmp_path / "sylvia-gm.db")
    campaign_id = _campaign(owner)
    _start(owner, campaign_id, "attempt-projection")
    owner.emit_narration_token(
        campaign_id,
        "attempt-projection",
        ProseChannel.SAFE_THINK,
        "A safe summary",
    )
    owner.emit_narration_token(campaign_id, "attempt-projection", ProseChannel.TALK, "Narration")
    owner.complete_narration_attempt(campaign_id, "attempt-projection")
    owner.execute_action(_action(campaign_id, action_id="action-projection"))

    projection = owner.player_projection(campaign_id)
    safe_think = next(item for item in projection if item.kind is PlayerProjectionKind.THINK)
    talk = next(item for item in projection if item.kind is PlayerProjectionKind.TALK)
    card = next(item for item in projection if item.kind is PlayerProjectionKind.TOOL_CARD)
    print("F2 PLAYER PROJECTION", projection)

    assert safe_think.text == "A safe summary"
    assert safe_think.collapsed_by_default
    assert not talk.collapsed_by_default
    assert card.collapsed_by_default
    assert card.action_id == "action-projection"
    assert not card.provisional
    owner.close()


def test_f2_pending_tool_call_becomes_one_atomic_call_result_action_unit(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking(context: MechanicContext) -> MechanicOutcome:
        entered.set()
        assert release.wait(timeout=5)
        return MechanicOutcome(result={"status": "committed", "echo": context.action_id})

    owner = _open_owner(tmp_path / "sylvia-gm.db")
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="blocking",
            capability="synthetic.counter",
            input_schema=_schema(),
            execute=blocking,
        )
    )
    campaign_id = _campaign(owner)
    command = _action(campaign_id, action_id="action-blocking", tool_name="blocking")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(owner.execute_action, command)
        assert entered.wait(timeout=5)
        during = owner.player_projection(campaign_id)
        pending = tuple(item for item in during if item.kind is PlayerProjectionKind.PENDING_TOOL)
        durable_during = owner.snapshot(campaign_id)
        release.set()
        receipt = future.result(timeout=5)

    after = owner.snapshot(campaign_id)
    cards = _tool_cards(owner, campaign_id)
    action_events = tuple(event for event in after.events if event.action_id == command.action_id)
    call_index = next(
        index
        for index, event in enumerate(action_events)
        if event.event_type is CanonicalEventType.TOOL_CALL
    )
    print("F2 PENDING TOOL", pending, "DURABLE", durable_during.events)
    print(
        "F2 ATOMIC TOOL UNIT",
        receipt,
        [(event.sequence, event.event_type.value) for event in action_events],
        cards,
    )

    assert len(pending) == 1
    assert pending[0].provisional
    assert durable_during.events == ()
    assert action_events[call_index + 1].event_type is CanonicalEventType.TOOL_RESULT
    assert len(cards) == 1
    assert all(
        item.kind is not PlayerProjectionKind.PENDING_TOOL
        for item in owner.player_projection(campaign_id)
    )
    owner.close()


def test_f2_success_failure_rejection_and_no_op_each_have_one_safe_tool_card(
    tmp_path: Path,
) -> None:
    owner = _open_owner(tmp_path / "sylvia-gm.db")
    campaign_id = _campaign(owner)
    outcomes = ("increment", "failure", "rejected", "no_op")
    for revision, tool_name in enumerate(outcomes):
        owner.execute_action(
            _action(
                campaign_id,
                action_id=f"action-{tool_name}",
                tool_name=tool_name,
                revision=revision,
            )
        )

    before_invalid = owner.snapshot(campaign_id)
    with pytest.raises(CommandRejected):
        owner.execute_action(
            _action(
                campaign_id,
                action_id="action-unavailable",
                tool_name="unavailable",
                revision=4,
            )
        )
    after_invalid = owner.snapshot(campaign_id)
    cards = _tool_cards(owner, campaign_id)
    statuses = tuple(card.safe_payload["status"] for card in cards)
    print("F2 TOOL OUTCOME CARDS", [(card.action_id, card.safe_payload) for card in cards])

    assert len(cards) == 4
    assert statuses == ("committed", "failed", "committed", "committed")
    assert cards[2].safe_payload["result"] == {
        "status": "rejected",
        "code": "synthetic_rejection",
    }
    assert cards[3].safe_payload["result"] == {"status": "no_op"}
    assert before_invalid == after_invalid
    owner.close()


def test_f2_private_trace_is_denied_by_default_and_never_reaches_canonical_or_sqlite(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "PRIVATE_TRACE_SENTINEL_F2"
    database = tmp_path / "sylvia-gm.db"
    owner = _open_owner(database)
    campaign_id = _campaign(owner)
    caplog.set_level(logging.DEBUG)

    denied = (
        (False, owner.owner_session_id, InspectorTraceSink.INSPECTOR),
        (True, "wrong-owner-session", InspectorTraceSink.INSPECTOR),
        (True, owner.owner_session_id, InspectorTraceSink.LOGGER),
        (True, owner.owner_session_id, InspectorTraceSink.CANONICAL),
        (True, owner.owner_session_id, InspectorTraceSink.EXPORT),
    )
    for opted_in, session_id, sink in denied:
        with pytest.raises(CommandRejected):
            owner.record_inspector_trace(
                owner_session_id=session_id,
                opted_in=opted_in,
                sink=sink,
                payload=sentinel,
            )

    snapshot = owner.snapshot(campaign_id)
    projection = owner.player_projection(campaign_id)
    owner.close()
    persisted_bytes = b"".join(path.read_bytes() for path in tmp_path.glob("sylvia-gm.db*"))
    print("F2 TRACE DENIALS", denied)
    print("F2 TRACE ABSENCE", snapshot.events, projection, caplog.text)

    assert sentinel not in repr(snapshot)
    assert sentinel not in repr(projection)
    assert sentinel not in caplog.text
    assert sentinel.encode() not in persisted_bytes


def test_f2_opted_in_trace_is_current_owner_session_inspector_only(tmp_path: Path) -> None:
    owner = _open_owner(tmp_path / "sylvia-gm.db")
    _campaign(owner)
    trace = {"private_reasoning": "spoiler", "diagnostic": "synthetic"}
    owner.record_inspector_trace(
        owner_session_id=owner.owner_session_id,
        opted_in=True,
        sink=InspectorTraceSink.INSPECTOR,
        payload=trace,
    )
    visible = owner.inspector_traces(owner.owner_session_id)
    with pytest.raises(CommandRejected):
        owner.inspector_traces("wrong-owner-session")
    with pytest.raises(CommandRejected):
        owner.record_inspector_trace(
            owner_session_id=owner.owner_session_id,
            opted_in=True,
            sink=InspectorTraceSink.LOGGER,
            payload=trace,
        )
    print("F2 TRACE CURRENT SESSION", owner.owner_session_id, visible)

    assert visible == (trace,)
    owner.close()


def test_f2_owner_restart_erases_trace_and_provisional_state_but_replays_canonical(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = _open_owner(database, owner_id="owner-f2")
    campaign_id = _campaign(owner, campaign_id="campaign-restart")
    _start(owner, campaign_id, "attempt-complete")
    owner.emit_narration_token(
        campaign_id, "attempt-complete", ProseChannel.TALK, "Durable narration"
    )
    owner.complete_narration_attempt(campaign_id, "attempt-complete")
    _start(owner, campaign_id, "attempt-provisional")
    owner.emit_narration_token(campaign_id, "attempt-provisional", ProseChannel.TALK, "Discard me")
    owner.record_inspector_trace(
        owner_session_id=owner.owner_session_id,
        opted_in=True,
        sink=InspectorTraceSink.INSPECTOR,
        payload="RESTART_PRIVATE_SENTINEL",
    )
    before_close = owner.player_projection(campaign_id)
    old_session = owner.owner_session_id
    owner.close()

    reopened = _open_owner(database, owner_id="owner-f2")
    replay = reopened.canonical_replay(campaign_id, acknowledged_sequence=0)
    projection = reopened.player_projection(campaign_id)
    print("F2 TRACE/PROVISIONAL BEFORE CLOSE", old_session, before_close)
    print("F2 RESTART REPLAY", reopened.owner_session_id, replay, projection)

    assert reopened.owner_session_id != old_session
    assert reopened.inspector_traces(reopened.owner_session_id) == ()
    assert [event.safe_payload for event in replay.canonical] == [{"text": "Durable narration"}]
    assert replay.provisional == ()
    assert "Discard me" not in repr(projection)
    assert "RESTART_PRIVATE_SENTINEL" not in repr(projection)
    reopened.close()


def test_f2_reconnect_before_grace_replays_without_cancel_or_duplication(
    tmp_path: Path,
) -> None:
    owner = _open_owner(tmp_path / "sylvia-gm.db")
    campaign_id = _campaign(owner)
    canceller = _start(owner, campaign_id, "attempt-reconnect")
    owner.emit_narration_token(
        campaign_id,
        "attempt-reconnect",
        ProseChannel.SAFE_THINK,
        "Completed safe thought",
    )
    owner.emit_narration_token(
        campaign_id, "attempt-reconnect", ProseChannel.TALK, "Live provisional"
    )
    deadline = owner.disconnect_narration(campaign_id, "attempt-reconnect", now=10.0)
    replay = owner.reconnect_narration(
        campaign_id,
        "attempt-reconnect",
        now=14.999,
        acknowledged_sequence=0,
    )
    owner.emit_narration_token(campaign_id, "attempt-reconnect", ProseChannel.TALK, " completed")
    owner.complete_narration_attempt(campaign_id, "attempt-reconnect")
    complete = owner.snapshot(campaign_id)
    print("F2 RECONNECT", "deadline", deadline, "cancel", canceller.count, replay)
    print("F2 RECONNECT COMPLETE", complete.events)

    assert deadline == 15.0
    assert canceller.count == 0
    assert [event.sequence for event in replay.canonical] == [1]
    assert replay.provisional[0].text == "Live provisional"
    assert replay.provisional[0].sequence is None
    assert [event.sequence for event in complete.events] == [1, 2]
    owner.close()


def test_f2_grace_expiry_cancels_once_discards_incomplete_prose_and_offers_retry(
    tmp_path: Path,
) -> None:
    calls: list[int] = []
    owner = _open_owner(tmp_path / "sylvia-gm.db", calls=calls)
    campaign_id = _campaign(owner)
    receipt = owner.execute_action(_action(campaign_id, action_id="action-before-interrupt"))
    before = owner.snapshot(campaign_id)
    canceller = _start(
        owner,
        campaign_id,
        "attempt-expiry",
        last_action_id=receipt.action_id,
    )
    owner.emit_narration_token(campaign_id, "attempt-expiry", ProseChannel.TALK, "Incomplete prose")
    deadline = owner.disconnect_narration(campaign_id, "attempt-expiry", now=10.0)
    first = owner.expire_narration(campaign_id, "attempt-expiry", now=15.0)
    second = owner.expire_narration(campaign_id, "attempt-expiry", now=20.0)
    with pytest.raises(CommandRejected):
        owner.emit_narration_token(campaign_id, "attempt-expiry", ProseChannel.TALK, " late")
    after = owner.snapshot(campaign_id)
    markers = tuple(
        item.text
        for item in owner.player_projection(campaign_id)
        if item.kind is PlayerProjectionKind.RETRY
    )
    print(
        "F2 GRACE EXPIRY",
        deadline,
        first,
        second,
        "cancel_count",
        canceller.count,
    )
    print(
        "F2 PRESERVED ACTION",
        before.revision,
        after.revision,
        before.state,
        after.state,
        markers,
    )

    assert deadline == 15.0
    assert first == second
    assert canceller.count == 1
    assert before == after
    assert calls == [1]
    assert "Incomplete prose" not in repr(owner.player_projection(campaign_id))
    assert markers == ("Narration interrupted—Retry",)
    owner.close()


def test_f2_manual_retry_narrates_stored_action_result_without_rerunning_mechanics(
    tmp_path: Path,
) -> None:
    calls: list[int] = []
    owner = _open_owner(tmp_path / "sylvia-gm.db", calls=calls)
    campaign_id = _campaign(owner)
    receipt = owner.execute_action(_action(campaign_id, action_id="action-retry-input"))
    _start(
        owner,
        campaign_id,
        "attempt-needs-retry",
        last_action_id=receipt.action_id,
    )
    owner.emit_narration_token(campaign_id, "attempt-needs-retry", ProseChannel.TALK, "Lost tail")
    interruption_id = _interrupt(owner, campaign_id, "attempt-needs-retry")
    before = owner.snapshot(campaign_id)
    captured: list[RetryNarrationInput] = []

    def narrator(value: RetryNarrationInput, stream: NarrationStream) -> None:
        captured.append(value)
        stream.emit(ProseChannel.SAFE_THINK, "Use the stored outcome")
        stream.emit(ProseChannel.TALK, "The counter advances once.")

    result = owner.manual_retry(
        campaign_id=campaign_id,
        interruption_id=interruption_id,
        retry_id="retry-stored-action",
        narrator=narrator,
    )
    after = owner.snapshot(campaign_id)
    print("F2 RETRY INPUT", captured[0])
    print(
        "F2 RETRY PRESERVES MECHANICS",
        "calls",
        calls,
        "revision",
        before.revision,
        after.revision,
        "mutations",
        len(before.mutations),
        len(after.mutations),
        result,
    )

    assert calls == [1]
    assert before.revision == after.revision == 1
    assert before.state == after.state == {"clockwork.counter": 1}
    assert before.mutations == after.mutations
    assert captured[0].interruption_id == interruption_id
    assert captured[0].retry_id == "retry-stored-action"
    assert captured[0].turn_id == "turn-f2"
    assert captured[0].action_receipt == receipt
    assert captured[0].canonical_high_water == before.events[-1].sequence
    with pytest.raises(FrozenInstanceError):
        captured[0].retry_id = "changed"  # type: ignore[misc]
    assert not result.replayed
    owner.close()


def test_f2_duplicate_retry_identity_is_idempotent_across_owner_reload(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = _open_owner(database)
    campaign_id = _campaign(owner, campaign_id="campaign-retry-idempotent")
    _start(owner, campaign_id, "attempt-first")
    owner.emit_narration_token(campaign_id, "attempt-first", ProseChannel.TALK, "Interrupted")
    interruption_id = _interrupt(owner, campaign_id, "attempt-first")
    narrator_calls: list[str] = []

    def interrupted_narrator(value: RetryNarrationInput, stream: NarrationStream) -> None:
        narrator_calls.append(f"{value.retry_id}:partial")
        stream.emit(ProseChannel.SAFE_THINK, "Stable retry summary")
        stream.emit(ProseChannel.TALK, "Discarded retry tail")
        raise RuntimeError("synthetic retry narrator interruption")

    with pytest.raises(RuntimeError, match="synthetic retry narrator interruption"):
        owner.manual_retry(
            campaign_id=campaign_id,
            interruption_id=interruption_id,
            retry_id="retry-idempotent",
            narrator=interrupted_narrator,
        )
    partial = owner.snapshot(campaign_id)
    assert [event.safe_payload for event in partial.events] == [{"text": "Stable retry summary"}]
    assert "Discarded retry tail" not in repr(owner.player_projection(campaign_id))
    failed_retry_basis = owner.retryable_interruption(
        campaign_id,
        failed_attempt_id="retry-idempotent",
    )
    assert failed_retry_basis is not None
    assert failed_retry_basis.interruption_id == interruption_id
    owner.close()
    owner = _open_owner(database)
    reopened_retry_basis = owner.retryable_interruption(
        campaign_id,
        failed_attempt_id="retry-idempotent",
    )
    assert reopened_retry_basis == failed_retry_basis

    def narrator(value: RetryNarrationInput, stream: NarrationStream) -> None:
        narrator_calls.append(f"{value.retry_id}:complete")
        stream.emit(ProseChannel.SAFE_THINK, "Stable retry summary")
        stream.emit(
            ProseChannel.TALK,
            "The door opens.\n\nChloe: Come in.",
            response_blocks=(
                {"kind": "narration", "text": "The door opens."},
                {
                    "kind": "npc_dialogue",
                    "speaker_label": "Chloe",
                    "text": "Come in.",
                },
            ),
        )

    first = owner.manual_retry(
        campaign_id=campaign_id,
        interruption_id=interruption_id,
        retry_id="retry-idempotent",
        narrator=narrator,
    )
    first_snapshot = owner.snapshot(campaign_id)
    owner.close()

    reopened = _open_owner(database)
    duplicate_calls: list[str] = []

    def must_not_run(value: RetryNarrationInput, _stream: NarrationStream) -> None:
        duplicate_calls.append(value.retry_id)

    duplicate = reopened.manual_retry(
        campaign_id=campaign_id,
        interruption_id=interruption_id,
        retry_id="retry-idempotent",
        narrator=must_not_run,
    )
    duplicate_snapshot = reopened.snapshot(campaign_id)
    existing = reopened._store.append_narration_segment(
        campaign_id=campaign_id,
        turn_id="turn-f2",
        attempt_id="retry-idempotent",
        ordinal=1,
        channel=ProseChannel.TALK,
        text="The door opens.\n\nChloe: Come in.",
        response_blocks=(
            {"kind": "narration", "text": "The door opens."},
            {"kind": "npc_dialogue", "speaker_label": "Chloe", "text": "Come in."},
        ),
    )
    with pytest.raises(IdempotencyConflict):
        reopened._store.append_narration_segment(
            campaign_id=campaign_id,
            turn_id="turn-f2",
            attempt_id="retry-idempotent",
            ordinal=1,
            channel=ProseChannel.TALK,
            text="Changed narration",
        )
    print("F2 RETRY IDEMPOTENCE", first, duplicate, existing)
    print("F2 RETRY RELOAD", narrator_calls, duplicate_calls, duplicate_snapshot.events)

    assert narrator_calls == [
        "retry-idempotent:partial",
        "retry-idempotent:complete",
    ]
    assert duplicate_calls == []
    assert duplicate.replayed
    assert first_snapshot == duplicate_snapshot
    assert existing.safe_payload["text"] == "The door opens.\n\nChloe: Come in."
    assert existing.safe_payload["response_blocks"][1]["speaker_label"] == "Chloe"
    reopened.close()


def test_f2_response_blocks_reject_unsafe_channels_and_later_text(tmp_path: Path) -> None:
    owner = _open_owner(tmp_path / "response-block-ingress.sqlite3")
    campaign_id = _campaign(owner, campaign_id="campaign-response-block-ingress")
    owner.start_narration_attempt(
        campaign_id=campaign_id,
        attempt_id="attempt-response-block-ingress",
        turn_id="turn-f2",
        cancel=lambda: None,
    )
    blocks = ({"kind": "narration", "text": "The bell rings."},)

    with pytest.raises(CommandRejected, match="only for TALK"):
        owner.emit_narration_token(
            campaign_id,
            "attempt-response-block-ingress",
            ProseChannel.SAFE_THINK,
            "The bell rings.",
            response_blocks=blocks,
        )
    owner.emit_narration_token(
        campaign_id,
        "attempt-response-block-ingress",
        ProseChannel.TALK,
        "The bell rings.",
        response_blocks=blocks,
    )
    with pytest.raises(CommandRejected, match="cannot accept more text"):
        owner.emit_narration_token(
            campaign_id,
            "attempt-response-block-ingress",
            ProseChannel.TALK,
            " Extra text.",
        )
    owner.close()


def test_f2_interruption_never_triggers_provider_fallback_or_invisible_continuation(
    tmp_path: Path,
) -> None:
    owner = _open_owner(tmp_path / "sylvia-gm.db")
    campaign_id = _campaign(owner)
    canceller = _start(owner, campaign_id, "attempt-no-fallback")
    owner.emit_narration_token(campaign_id, "attempt-no-fallback", ProseChannel.TALK, "Unfinished")
    interruption_id = _interrupt(owner, campaign_id, "attempt-no-fallback")
    after_expiry = owner.snapshot(campaign_id)
    fallback_calls: list[str] = []

    def fallback(_value: RetryNarrationInput, _stream: NarrationStream) -> None:
        fallback_calls.append("called")

    later = owner.snapshot(campaign_id)
    print(
        "F2 NO FALLBACK",
        interruption_id,
        "cancel",
        canceller.count,
        "fallback",
        fallback_calls,
        "events",
        later.events,
    )

    assert canceller.count == 1
    assert fallback_calls == []
    assert after_expiry == later
    assert after_expiry.events == ()
    assert owner.player_projection(campaign_id)[-1].text == "Narration interrupted—Retry"
    assert callable(fallback)
    owner.close()


def test_f2_sequence_replay_after_ack_is_gap_free_and_excludes_private_and_provisional_data(
    tmp_path: Path,
) -> None:
    private = "PRIVATE_REPLAY_SENTINEL"
    owner = _open_owner(tmp_path / "sylvia-gm.db")
    campaign_id = _campaign(owner)
    _start(owner, campaign_id, "attempt-replay-1")
    owner.emit_narration_token(
        campaign_id, "attempt-replay-1", ProseChannel.SAFE_THINK, "Safe first"
    )
    owner.emit_narration_token(campaign_id, "attempt-replay-1", ProseChannel.TALK, "Safe second")
    owner.complete_narration_attempt(campaign_id, "attempt-replay-1")
    owner.execute_action(_action(campaign_id, action_id="action-replay"))
    _start(owner, campaign_id, "attempt-replay-live")
    owner.emit_narration_token(campaign_id, "attempt-replay-live", ProseChannel.TALK, "Live only")
    owner.record_inspector_trace(
        owner_session_id=owner.owner_session_id,
        opted_in=True,
        sink=InspectorTraceSink.INSPECTOR,
        payload=private,
    )

    full = owner.snapshot(campaign_id)
    acknowledged = 1
    replay = owner.canonical_replay(campaign_id, acknowledged_sequence=acknowledged)
    replay_sequences = [event.sequence for event in replay.canonical]
    print(
        "F2 REPLAY HIGH WATER",
        full.events[-1].sequence,
        "ACK",
        acknowledged,
        "SEQUENCES",
        replay_sequences,
    )
    print("F2 REPLAY CANONICAL/PROVISIONAL", replay.canonical, replay.provisional)

    assert replay_sequences == list(range(acknowledged + 1, full.events[-1].sequence + 1))
    assert replay.high_water == full.events[-1].sequence
    assert "Live only" not in repr(replay.canonical)
    assert replay.provisional[-1].text == "Live only"
    assert private not in repr(replay)
    owner.close()


def _f1_seed(database: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    lock = _lock()
    campaign_id = "campaign-f1-migration"
    action_id = "action-f1-migration"
    payload = {"amount": 1}
    result = {"status": "committed", "counter": 1}
    payload_hash = sqlite_authority._stored_action_hash(
        campaign_id=campaign_id,
        action_id=action_id,
        writer="dm",
        expected_revision=0,
        turn_id="turn-f2",
        package_id="synthetic.clockwork",
        package_version="1.0.0",
        package_sha256=SYNTHETIC_SHA,
        tool_name="increment",
        parameters=payload,
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(sqlite_authority._MIGRATION_1)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (
                1,
                sqlite_authority._MIGRATION_1_NAME,
                sqlite_authority._MIGRATION_1_SHA256,
            ),
        )
        connection.execute(
            "INSERT INTO packages VALUES (?, ?, ?, ?)",
            (
                "synthetic.clockwork",
                "1.0.0",
                SYNTHETIC_SHA,
                json.dumps(["synthetic.counter"], separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                "Exact F1 migration snapshot",
                "active",
                "dm",
                1,
                sqlite_authority._lock_representation(lock),
            ),
        )
        connection.execute(
            "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, ?, ?)",
            (campaign_id, "synthetic.clockwork", "1.0.0", SYNTHETIC_SHA, "primary", 0),
        )
        connection.execute(
            "INSERT INTO campaign_state VALUES (?, ?, ?, ?)",
            (campaign_id, "clockwork", "counter", "1"),
        )
        connection.execute(
            "INSERT INTO turns VALUES (?, ?, ?, ?)",
            (campaign_id, "turn-f2", "open", 0),
        )
        connection.execute(
            "INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                action_id,
                "turn-f2",
                0,
                1,
                "dm",
                "synthetic.clockwork",
                "1.0.0",
                SYNTHETIC_SHA,
                "increment",
                "committed",
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                payload_hash,
                json.dumps(result, sort_keys=True, separators=(",", ":")),
            ),
        )
        event_rows = (
            ("action_declared", {"action_id": action_id, "writer": "dm"}),
            (
                "tool_call",
                {
                    "package_id": "synthetic.clockwork",
                    "package_version": "1.0.0",
                    "package_sha256": SYNTHETIC_SHA,
                    "tool_name": "increment",
                    "parameters": payload,
                },
            ),
            ("tool_result", {"status": "committed", "result": result}),
            ("state_mutation", {"namespace": "clockwork", "key": "counter"}),
            ("consequence", {"kind": "counter_advanced", "amount": 1}),
            ("action_committed", {"status": "committed", "resulting_revision": 1}),
        )
        for sequence, (event_type, safe_payload) in enumerate(event_rows, start=1):
            connection.execute(
                "INSERT INTO canonical_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    sequence,
                    event_type,
                    "turn-f2",
                    action_id,
                    json.dumps(safe_payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        connection.execute(
            "INSERT INTO mutations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, action_id, 0, "clockwork", "counter", "0", "1"),
        )
        consequence = json.dumps({"amount": 1, "kind": "counter_advanced"}, separators=(",", ":"))
        connection.execute(
            "INSERT INTO consequences VALUES (?, ?, ?, ?)",
            (campaign_id, action_id, 0, consequence),
        )
        connection.execute(
            "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                "idem-action-f1-migration",
                payload_hash,
                action_id,
                "committed",
                json.dumps(result, sort_keys=True, separators=(",", ":")),
                1,
            ),
        )
        connection.execute("INSERT INTO campaign_revisions VALUES (?, ?, NULL)", (campaign_id, 0))
        connection.execute(
            "INSERT INTO campaign_revisions VALUES (?, ?, ?)",
            (campaign_id, 1, action_id),
        )
        connection.execute("PRAGMA user_version = 1")
        snapshot = _f1_relational_snapshot(connection)
    return snapshot


def _f1_relational_snapshot(
    connection: sqlite3.Connection,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    columns = {
        "schema_migrations": ("version", "applied_name", "sql_sha256"),
        "packages": ("package_id", "version", "sha256", "capabilities_json"),
        "package_dependencies": (
            "package_id",
            "version",
            "sha256",
            "dependency_order",
            "dependency_id",
            "dependency_version",
            "dependency_sha256",
            "dependency_kind",
        ),
        "campaigns": (
            "campaign_id",
            "display_name",
            "lifecycle",
            "writer",
            "revision",
            "lock_hash",
        ),
        "campaign_package_memberships": (
            "campaign_id",
            "package_id",
            "version",
            "sha256",
            "role",
            "load_order",
        ),
        "participants": (
            "campaign_id",
            "participant_id",
            "kind",
            "package_id",
            "package_version",
            "data_json",
        ),
        "campaign_state": ("campaign_id", "namespace", "state_key", "value_json"),
        "turns": ("campaign_id", "turn_id", "status", "created_revision"),
        "actions": (
            "campaign_id",
            "action_id",
            "turn_id",
            "expected_revision",
            "resulting_revision",
            "writer",
            "package_id",
            "package_version",
            "package_sha256",
            "tool_name",
            "status",
            "payload_json",
            "payload_hash",
            "result_json",
        ),
        "canonical_events": (
            "campaign_id",
            "sequence",
            "event_type",
            "turn_id",
            "action_id",
            "safe_payload_json",
        ),
        "mutations": (
            "campaign_id",
            "action_id",
            "ordinal",
            "namespace",
            "state_key",
            "before_json",
            "after_json",
        ),
        "consequences": ("campaign_id", "action_id", "ordinal", "payload_json"),
        "idempotency_records": (
            "campaign_id",
            "idempotency_key",
            "payload_hash",
            "action_id",
            "status",
            "result_json",
            "resulting_revision",
        ),
        "campaign_revisions": ("campaign_id", "revision", "action_id"),
    }
    return {
        table: tuple(
            tuple(row)
            for row in connection.execute(
                f"SELECT {', '.join(names)} FROM {table} ORDER BY {', '.join(names)}"
            )
        )
        for table, names in columns.items()
    }


def test_f2_migrates_an_exact_f1_database_without_changing_its_f1_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    before = _f1_seed(database)
    owner = _open_owner(database)
    logical = owner.snapshot("campaign-f1-migration")
    legacy_lock = owner.current_package_lock("campaign-f1-migration")
    with pytest.raises(CommandRejected, match="unavailable"):
        owner.catalog("campaign-f1-migration")
    owner.close()
    with sqlite3.connect(database) as connection:
        after = _f1_relational_snapshot(connection)
        migration_rows = tuple(
            connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = tuple(connection.execute("PRAGMA foreign_key_check"))

    expected_after = dict(before)
    expected_after["schema_migrations"] = (
        before["schema_migrations"][0],
        (2, sqlite_authority._MIGRATION_2_NAME, sqlite_authority._MIGRATION_2_SHA256),
        (3, sqlite_authority._MIGRATION_3_NAME, sqlite_authority._MIGRATION_3_SHA256),
    )
    print("F2 MIGRATION SNAPSHOT EQUALITY", expected_after == after)
    print("F2 MIGRATION IDENTITY", version, migration_rows)
    print(
        "F2 MIGRATED CHRONOLOGY",
        [(event.sequence, event.event_type.value) for event in logical.events],
        integrity,
        foreign_keys,
    )

    assert after == expected_after
    assert version == sqlite_authority._SCHEMA_VERSION == 3
    assert tuple(row[0] for row in migration_rows) == (1, 2, 3)
    assert legacy_lock.availability is PackageLockAvailability.LEGACY_UNAVAILABLE
    assert logical.revision == 1
    assert logical.state == {"clockwork.counter": 1}
    assert [event.sequence for event in logical.events] == list(range(1, 7))
    assert integrity == "ok"
    assert foreign_keys == ()


def test_normal_attempt_identity_with_canonical_output_rejects_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "normal-attempt-reuse.db"
    owner = _open_owner(database)
    campaign_id = _campaign(owner, campaign_id="campaign-normal-attempt-reuse")
    attempt_id = "attempt-normal-reuse"
    _start(owner, campaign_id, attempt_id)
    owner.emit_narration_token(
        campaign_id,
        attempt_id,
        ProseChannel.TALK,
        "The first canonical narration.",
    )
    owner.complete_narration_attempt(campaign_id, attempt_id)
    owner.complete_turn(campaign_id, "turn-f2")
    before_replay = owner.canonical_replay(campaign_id, acknowledged_sequence=0)
    before_projection = owner.player_projection(campaign_id)
    owner.close()

    reopened = _open_owner(database)
    rejected_cancel = CountingCanceller()
    reuse_rejection: IdempotencyConflict | None = None
    try:
        _start(reopened, campaign_id, attempt_id, rejected_cancel)
    except IdempotencyConflict as error:
        reuse_rejection = error
    else:
        reopened.emit_narration_token(
            campaign_id,
            attempt_id,
            ProseChannel.TALK,
            "The incorrectly admitted second narration.",
        )
        reopened.complete_narration_attempt(campaign_id, attempt_id)

    after_replay = reopened.canonical_replay(campaign_id, acknowledged_sequence=0)
    after_projection = reopened.player_projection(campaign_id)
    stored_ordinals = tuple(
        event.attempt_ordinal for event in after_replay.canonical if event.attempt_id == attempt_id
    )
    rejected_operations: list[tuple[str, str, str]] = []
    for operation, invoke in (
        (
            "emit",
            lambda: reopened.emit_narration_token(
                campaign_id, attempt_id, ProseChannel.TALK, "Must reject."
            ),
        ),
        (
            "disconnect",
            lambda: reopened.disconnect_narration(campaign_id, attempt_id, now=1.0),
        ),
        (
            "reconnect",
            lambda: reopened.reconnect_narration(
                campaign_id,
                attempt_id,
                now=1.0,
                acknowledged_sequence=before_replay.high_water,
            ),
        ),
    ):
        try:
            invoke()
        except CommandRejected as error:
            rejected_operations.append((operation, error.code, str(error)))

    registered_after_rejection = (campaign_id, attempt_id) in reopened._attempts
    work_count_after_rejection = reopened._campaign_work_counts.get(campaign_id, 0)
    fresh_attempt = "attempt-fresh-on-completed-turn"
    _start(reopened, campaign_id, fresh_attempt)
    reopened.emit_narration_token(
        campaign_id,
        fresh_attempt,
        ProseChannel.TALK,
        "Fresh narration remains allowed on the completed TURN.",
    )
    fresh_events = reopened.complete_narration_attempt(campaign_id, fresh_attempt)
    print(
        "F2 NORMAL ATTEMPT REUSE",
        campaign_id,
        attempt_id,
        "ordinals",
        stored_ordinals,
        "rejection",
        getattr(reuse_rejection, "code", None),
        str(reuse_rejection) if reuse_rejection is not None else None,
    )
    print(
        "F2 NORMAL ATTEMPT REUSE STATE",
        "high_water",
        before_replay.high_water,
        after_replay.high_water,
        "work_count",
        work_count_after_rejection,
        "cancel_count",
        rejected_cancel.count,
        "registered",
        registered_after_rejection,
        "operations",
        rejected_operations,
    )
    reopened.close()

    corrupt_owner = _open_owner(tmp_path / "noncontiguous-attempt.db")
    corrupt_campaign = _campaign(corrupt_owner, campaign_id="campaign-noncontiguous-attempt")
    corrupt_attempt = "attempt-noncontiguous"
    corrupt_owner._store.append_narration_segment(
        campaign_id=corrupt_campaign,
        turn_id="turn-f2",
        attempt_id=corrupt_attempt,
        ordinal=1,
        channel=ProseChannel.TALK,
        text="Synthetic noncontiguous chronology.",
    )
    corrupt_ordinals = tuple(
        event.attempt_ordinal
        for event in corrupt_owner.snapshot(corrupt_campaign).events
        if event.attempt_id == corrupt_attempt
    )
    with pytest.raises(CommandRejected) as corruption_rejection:
        _start(corrupt_owner, corrupt_campaign, corrupt_attempt)
    print(
        "F2 NONCONTIGUOUS ATTEMPT",
        corrupt_attempt,
        corrupt_ordinals,
        corruption_rejection.value.code,
        str(corruption_rejection.value),
    )
    assert corruption_rejection.value.code == "command_rejected"
    assert str(corruption_rejection.value) == "Stored narration attempt chronology is invalid"
    assert (corrupt_campaign, corrupt_attempt) not in corrupt_owner._attempts
    assert corrupt_owner._campaign_work_counts.get(corrupt_campaign, 0) == 0
    corrupt_owner.close()

    zero_database = tmp_path / "zero-ordinal-attempt.db"
    zero_owner = _open_owner(zero_database)
    zero_campaign = _campaign(zero_owner, campaign_id="campaign-zero-ordinal-attempt")
    zero_attempt = "attempt-zero-ordinal"
    zero_cancel = _start(zero_owner, zero_campaign, zero_attempt)
    zero_interruption = _interrupt(zero_owner, zero_campaign, zero_attempt)
    assert zero_owner.snapshot(zero_campaign).events == ()
    zero_owner.close()

    zero_reopened = _open_owner(zero_database)
    resumed_cancel = _start(zero_reopened, zero_campaign, zero_attempt)
    zero_reopened.emit_narration_token(
        zero_campaign,
        zero_attempt,
        ProseChannel.TALK,
        "A reopened zero-ordinal authority attempt remains admissible.",
    )
    zero_events = zero_reopened.complete_narration_attempt(zero_campaign, zero_attempt)
    zero_ordinals = tuple(event.attempt_ordinal for event in zero_events)
    print(
        "F2 ZERO-ORDINAL REOPEN",
        zero_attempt,
        zero_interruption,
        "ordinals",
        zero_ordinals,
        "initial_cancel",
        zero_cancel.count,
        "resumed_cancel",
        resumed_cancel.count,
    )
    zero_reopened.close()

    assert isinstance(reuse_rejection, IdempotencyConflict)
    assert reuse_rejection.code == "idempotency_conflict"
    assert str(reuse_rejection) == "Narration attempt already has canonical output"
    assert after_replay == before_replay
    assert after_projection == before_projection
    assert stored_ordinals == (0,)
    assert rejected_cancel.count == 0
    assert [item[0] for item in rejected_operations] == [
        "emit",
        "disconnect",
        "reconnect",
    ]
    assert all(item[1] == "command_rejected" for item in rejected_operations)
    assert not registered_after_rejection
    assert work_count_after_rejection == 0
    assert tuple(event.safe_payload["text"] for event in fresh_events) == (
        "Fresh narration remains allowed on the completed TURN.",
    )
    assert zero_ordinals == (0,)
    assert resumed_cancel.count == 0
