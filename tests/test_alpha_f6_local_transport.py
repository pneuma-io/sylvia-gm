"""Exact F6 local transport and reconnect acceptance probes.

Probes mapped to the F6 gates::

    G-INTERRUPT  — disconnect grace → cancel after the deadline; committed
                   mechanics preserved, incomplete prose discarded.
    G-RECOVER    — reconnect within the grace window replays the canonical
                   sequence and the provisional (incomplete) stream tokens.
    journey      — open owner → install package → create campaign → execute
                   action → attach narration → emit provisional → disconnect →
                   reconnect/expire → stop.

Tests use only synthetic archives, temporary SQLite databases, and in-process
supervisor instances — no sockets, no Tauri shell.  A frozen clock drives the
disconnect-grace timing deterministically.
"""

from __future__ import annotations

import hashlib
import io
import threading
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.domain.authority import (
    LifecycleState,
    MechanicContext,
    MechanicOutcome,
    NarrationInterruption,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    ProseChannel,
    StateMutation,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
)
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import ListenerGone, LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor

SYNTHETIC_SHA = "a" * 64


# --------------------------------------------------------------------------
# Synthetic package + campaign fixtures (mirrors F2/F4 shapes)
# --------------------------------------------------------------------------


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount"],
        "properties": {"amount": {"type": "integer", "minimum": 1}},
    }


def _increment(calls: list[int]):  # type: ignore[no-untyped-def]
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


def _f6_archive() -> tuple[str, bytes]:
    payload = canonical_json_bytes({"fixture": "synthetic-f6-transport"})
    draft: dict[str, object] = {
        "id": "synthetic.clockwork",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Synthetic F6 transport fixture"},
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
                "name": "increment",
                "capability": "synthetic.counter",
                "input_schema": _schema(),
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
            }
        ],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "payload/f6t.json",
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
        archive.writestr("packages/synthetic.clockwork/payload/f6t.json", payload)
    return root, stream.getvalue()


def _lock(sha: str = SYNTHETIC_SHA) -> PackageLock:
    return PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="1.0.0",
                sha256=sha,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(),
                capabilities=("synthetic.counter",),
            ),
        )
    )


def _action(
    campaign_id: str,
    *,
    action_id: str,
    owner_id: str,
    revision: int = 0,
    tool_name: str = "increment",
) -> ActionCommand:
    return ActionCommand(
        campaign_id=campaign_id,
        action_id=action_id,
        idempotency_key=f"idem-{action_id}",
        owner_id=owner_id,
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id="turn-f6",
        package_id="synthetic.clockwork",
        tool_name=tool_name,
        parameters={"amount": 1},
    )


class CountingCanceller:
    def __init__(self) -> None:
        self.count = 0
        self._lock = threading.Lock()

    def __call__(self) -> None:
        with self._lock:
            self.count += 1


class FrozenClock:
    def __init__(self, now: float = 0.0) -> None:
        self._now = now
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, delta: float) -> None:
        with self._lock:
            self._now += delta


# --------------------------------------------------------------------------
# Journey helper — stands up the supervisor, package, campaign, action
# --------------------------------------------------------------------------


def _stand_up(
    database: Path,
    *,
    disconnect_grace_seconds: float = 5.0,
    attachment_lease_seconds: float = 5.0,
    clock: object | None = None,
) -> tuple[LocalOwnerSupervisor, LocalCommandBus, str, str, str]:
    """Open supervisor+owner, install package, register tool, create campaign,
    execute one committed action.  Returns (supervisor, bus, owner_id,
    campaign_id, action_id)."""

    supervisor = LocalOwnerSupervisor(
        disconnect_grace_seconds=disconnect_grace_seconds,
        attachment_lease_seconds=attachment_lease_seconds,
        initial_attachment_lease_seconds=attachment_lease_seconds,
        clock=FrozenClock(0.0) if clock is None else clock,  # type: ignore[arg-type]
    )
    cred = supervisor.acquire_owner(database, "owner-f6-transport")
    supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
    _root, archive = _f6_archive()
    supervisor.install_package_archive(io.BytesIO(archive), install_id="f6-transport-install")
    # Register the tool against the installed package version.
    calls: list[int] = []
    owner_owner = supervisor._owner  # type: ignore[union-attr]
    assert owner_owner is not None
    package_sha = _root
    owner_owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=package_sha,
            name="increment",
            capability="synthetic.counter",
            input_schema=_schema(),
            execute=_increment(calls),
        )
    )
    campaign_id = owner_owner.create_campaign(
        CampaignCreate(
            display_name="F6 transport journey",
            writer=WriterRole.DM,
            package_lock=_lock(sha=package_sha),
            turn_id="turn-f6",
            campaign_id="campaign-f6-transport",
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    ).campaign_id
    receipt = owner_owner.execute_action(
        _action(campaign_id, action_id="action-f6-1", owner_id="owner-f6-transport")
    )
    bus = LocalCommandBus(supervisor)
    return supervisor, bus, "owner-f6-transport", campaign_id, receipt.action_id


# --------------------------------------------------------------------------
# G-ORDER — wrong owner / wrong writer rejected at the application layer
# --------------------------------------------------------------------------


class TestOrderGates:
    def test_wrong_owner_command_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "order.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        cred = supervisor.acquire_owner(db, "owner-real")
        supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
        try:
            owner = supervisor._owner  # type: ignore[union-attr]
            _root, archive = _f6_archive()
            owner.install_package_archive(io.BytesIO(archive), install_id="order-install")
            owner.register_tool(
                CatalogTool(
                    package_id="synthetic.clockwork",
                    package_version="1.0.0",
                    package_sha256=_root,
                    name="increment",
                    capability="synthetic.counter",
                    input_schema=_schema(),
                    execute=_increment([]),
                )
            )
            campaign_id = owner.create_campaign(
                CampaignCreate(
                    display_name="F6 order",
                    writer=WriterRole.DM,
                    package_lock=_lock(sha=_root),
                    turn_id="turn-f6",
                    campaign_id="campaign-order",
                    initial_state=(StateValue("clockwork", "counter", 0),),
                )
            ).campaign_id
            from sylvia_gm.domain.errors import WrongOwner

            with pytest.raises(WrongOwner):
                owner.execute_action(
                    _action(
                        campaign_id,
                        action_id="action-wrong-owner",
                        owner_id="owner-imposter",
                    )
                )
        finally:
            supervisor.shutdown()

    def test_snapshot_projection_is_read_only(self, tmp_path: Path) -> None:
        db = tmp_path / "snapshot-ro.db"
        supervisor, bus, _owner_id, campaign_id, _action_id = _stand_up(db)
        try:
            snapshot = bus.snapshot(campaign_id)
            assert snapshot.lifecycle is LifecycleState.ACTIVE
            assert snapshot.revision == 1
        finally:
            supervisor.shutdown()


# --------------------------------------------------------------------------
# Stream attach + fan-out (G-RECOVER provisional replay)
# --------------------------------------------------------------------------


class TestStreamAttachAndFanOut:
    def test_emit_token_fans_out_to_live_listener(self, tmp_path: Path) -> None:
        db = tmp_path / "fanout.db"
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(db)
        try:
            received: list[tuple[str, str]] = []
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                received.append((channel.value, token))

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-fanout",
                turn_id="turn-f6",
                cancel=canceller,
                listener=listener,
                last_action_id=action_id,
            )
            owner = supervisor._owner  # type: ignore[union-attr]
            owner.emit_narration_token(campaign_id, "attempt-fanout", ProseChannel.TALK, "Hello ")
            bus.emit_token(campaign_id, "attempt-fanout", ProseChannel.TALK, "Hello ")
            owner.emit_narration_token(campaign_id, "attempt-fanout", ProseChannel.TALK, "world.")
            bus.emit_token(campaign_id, "attempt-fanout", ProseChannel.TALK, "world.")
            assert received == [("talk", "Hello "), ("talk", "world.")]
        finally:
            supervisor.shutdown()

    def test_dead_listener_triggers_disconnect_grace(self, tmp_path: Path) -> None:
        db = tmp_path / "grace.db"
        clock = FrozenClock(0.0)
        supervisor = LocalOwnerSupervisor(
            disconnect_grace_seconds=5.0,
            clock=clock,
        )
        cred = supervisor.acquire_owner(db, "owner-grace")
        supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
        try:
            _root, archive = _f6_archive()
            supervisor.install_package_archive(io.BytesIO(archive), install_id="grace-install")
            owner = supervisor._owner  # type: ignore[union-attr]
            owner.register_tool(
                CatalogTool(
                    package_id="synthetic.clockwork",
                    package_version="1.0.0",
                    package_sha256=_root,
                    name="increment",
                    capability="synthetic.counter",
                    input_schema=_schema(),
                    execute=_increment([]),
                )
            )
            campaign_id = owner.create_campaign(
                CampaignCreate(
                    display_name="F6 grace",
                    writer=WriterRole.DM,
                    package_lock=_lock(sha=_root),
                    turn_id="turn-f6",
                    campaign_id="campaign-grace",
                    initial_state=(StateValue("clockwork", "counter", 0),),
                )
            ).campaign_id
            receipt = owner.execute_action(
                _action(campaign_id, action_id="action-grace-1", owner_id="owner-grace")
            )
            bus = LocalCommandBus(supervisor)
            canceller = CountingCanceller()

            def dead_listener(channel: ProseChannel, token: str) -> None:
                raise ListenerGone()

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-grace",
                turn_id="turn-f6",
                cancel=canceller,
                listener=dead_listener,
                last_action_id=receipt.action_id,
            )
            # Emit a token — the listener drops, triggering disconnect grace.
            bus.emit_token(campaign_id, "attempt-grace", ProseChannel.TALK, "dropped")
            # The grace window is now open; the attempt is disconnected.
            deadline = supervisor._leases[(campaign_id, "attempt-grace")].deadline  # type: ignore[index]
            assert deadline is not None
            # Reconnect within the grace window should succeed.
            replay, _attachment_generation = supervisor.reconnect_narration(
                campaign_id, "attempt-grace", acknowledged_sequence=0
            )
            assert replay.high_water >= 1
        finally:
            supervisor.shutdown()


# --------------------------------------------------------------------------
# G-INTERRUPT — disconnect grace → cancel; committed mechanics preserved
# --------------------------------------------------------------------------


class TestInterruptAndRecover:
    def test_initial_renderer_attachment_allows_slow_local_prefill(self, tmp_path: Path) -> None:
        clock = FrozenClock(0.0)
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(
            tmp_path / "slow-prefill-attachment.db",
            disconnect_grace_seconds=5.0,
            attachment_lease_seconds=5.0,
            clock=clock,
        )
        supervisor._initial_attachment_lease_seconds = 40.0
        try:
            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-slow-prefill",
                turn_id="turn-f6",
                cancel=lambda: None,
                listener=lambda _channel, _token: None,
                last_action_id=action_id,
            )
            with supervisor._leases_lock:
                lease = supervisor._leases[(campaign_id, "attempt-slow-prefill")]
                assert lease.attachment_deadline == 40.0

            clock.advance(20.0)
            renewed_deadline, generation = supervisor.heartbeat_narration(
                campaign_id,
                "attempt-slow-prefill",
                expected_generation=None,
            )

            assert renewed_deadline == 25.0
            assert generation == 1
        finally:
            supervisor.shutdown()
            bus.close()

    def test_missing_renderer_heartbeat_arms_grace_and_cancels(self, tmp_path: Path) -> None:
        clock = FrozenClock(0.0)
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(
            tmp_path / "attachment-expiry.db",
            disconnect_grace_seconds=5.0,
            attachment_lease_seconds=5.0,
            clock=clock,
        )
        cancelled = threading.Event()
        try:
            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-attachment-expiry",
                turn_id="turn-f6",
                cancel=cancelled.set,
                listener=lambda _channel, _token: None,
                last_action_id=action_id,
            )
            with supervisor._leases_lock:
                lease = supervisor._leases[(campaign_id, "attempt-attachment-expiry")]
                assert lease.attachment_deadline is not None
                attachment_deadline = lease.attachment_deadline
            clock.advance(5.0)
            supervisor._on_attachment_expired(
                campaign_id,
                "attempt-attachment-expiry",
                attachment_deadline,
            )
            assert (
                bus.narration_attempt_status(campaign_id, "attempt-attachment-expiry")
                == "disconnected"
            )

            clock.advance(5.0)
            supervisor._on_grace_expired(lease)

            assert cancelled.is_set()
            assert (
                bus.narration_attempt_status(campaign_id, "attempt-attachment-expiry")
                == "interrupted"
            )
            retry_basis = bus.retryable_interruption(
                campaign_id,
                failed_attempt_id="attempt-attachment-expiry",
            )
            assert retry_basis is not None
            assert retry_basis.failed_attempt_id == "attempt-attachment-expiry"
        finally:
            supervisor.shutdown()

    def test_fast_renderer_reattach_replays_while_attempt_is_active(self, tmp_path: Path) -> None:
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(
            tmp_path / "active-reattach.db"
        )
        try:
            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-active-reattach",
                turn_id="turn-f6",
                cancel=lambda: None,
                listener=lambda _channel, _token: None,
                last_action_id=action_id,
            )

            replay = bus.reconnect_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-active-reattach",
                acknowledged_sequence=0,
                listener=lambda _channel, _token: None,
            )

            assert replay.high_water >= 1
            assert bus.narration_attempt_status(campaign_id, "attempt-active-reattach") == "active"
        finally:
            supervisor.shutdown()

    def test_stale_grace_timer_cannot_cancel_after_successful_reconnect(
        self, tmp_path: Path
    ) -> None:
        clock = FrozenClock(0.0)
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(
            tmp_path / "stale-grace.db", clock=clock
        )
        canceller = CountingCanceller()
        try:
            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-stale-grace",
                turn_id="turn-f6",
                cancel=canceller,
                listener=lambda _channel, _token: None,
                last_action_id=action_id,
            )
            bus.disconnect_stream(campaign_id, "attempt-stale-grace")
            with supervisor._leases_lock:
                stale_lease = supervisor._leases[(campaign_id, "attempt-stale-grace")]
            clock.advance(1.0)
            bus.reconnect_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-stale-grace",
                acknowledged_sequence=0,
                listener=lambda _channel, _token: None,
            )

            supervisor._on_grace_expired(stale_lease)

            assert canceller.count == 0
            assert bus.narration_attempt_status(campaign_id, "attempt-stale-grace") == "active"
        finally:
            supervisor.shutdown()

    def test_reconnect_replays_canonical_and_provisional(self, tmp_path: Path) -> None:
        db = tmp_path / "reconnect.db"
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(db)
        try:
            received: list[str] = []
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                received.append(token)

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-reconnect",
                turn_id="turn-f6",
                cancel=canceller,
                listener=listener,
                last_action_id=action_id,
            )
            owner = supervisor._owner  # type: ignore[union-attr]
            # Emit a provisional token (incomplete prose).
            owner.emit_narration_token(
                campaign_id, "attempt-reconnect", ProseChannel.TALK, "partial..."
            )
            bus.emit_token(campaign_id, "attempt-reconnect", ProseChannel.TALK, "partial...")
            # Disconnect the client.
            supervisor.signal_disconnect(campaign_id, "attempt-reconnect")
            # Reconnect within the grace window.
            replay = bus.reconnect_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-reconnect",
                acknowledged_sequence=0,
                listener=listener,
            )
            # The committed canonical events (action committed + mutation +
            # consequence) are replayed.
            assert len(replay.canonical) >= 3
            # The provisional token is replayed to the reattached listener.
            assert "partial..." in received
        finally:
            supervisor.shutdown()

    def test_stop_cancels_and_preserves_committed_mechanics(self, tmp_path: Path) -> None:
        db = tmp_path / "stop.db"
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(db)
        try:
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                pass

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-stop",
                turn_id="turn-f6",
                cancel=canceller,
                listener=listener,
                last_action_id=action_id,
            )
            # Emit incomplete provisional prose.
            owner = supervisor._owner  # type: ignore[union-attr]
            owner.emit_narration_token(campaign_id, "attempt-stop", ProseChannel.TALK, "unfinished")
            bus.emit_token(campaign_id, "attempt-stop", ProseChannel.TALK, "unfinished")
            # Stop — interrupt immediately.
            interruption = bus.interrupt_stream(campaign_id, "attempt-stop")
            assert isinstance(interruption, NarrationInterruption)
            assert interruption.failed_attempt_id == "attempt-stop"
            # The committed mechanics (counter mutation) are preserved.
            snapshot = bus.snapshot(campaign_id)
            assert snapshot.revision == 1
            assert snapshot.state.get("clockwork.counter") == 1
            # The incomplete prose is discarded — no TALK event in canonical.
            talk_events = [event for event in snapshot.events if event.event_type.value == "talk"]
            assert talk_events == []
        finally:
            supervisor.shutdown()

    def test_grace_expiry_cancels_narration(self, tmp_path: Path) -> None:
        db = tmp_path / "expiry.db"
        clock = FrozenClock(0.0)
        supervisor = LocalOwnerSupervisor(
            disconnect_grace_seconds=1.0,
            clock=clock,
        )
        cred = supervisor.acquire_owner(db, "owner-expiry")
        supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
        try:
            _root, archive = _f6_archive()
            supervisor.install_package_archive(io.BytesIO(archive), install_id="expiry-install")
            owner = supervisor._owner  # type: ignore[union-attr]
            owner.register_tool(
                CatalogTool(
                    package_id="synthetic.clockwork",
                    package_version="1.0.0",
                    package_sha256=_root,
                    name="increment",
                    capability="synthetic.counter",
                    input_schema=_schema(),
                    execute=_increment([]),
                )
            )
            campaign_id = owner.create_campaign(
                CampaignCreate(
                    display_name="F6 expiry",
                    writer=WriterRole.DM,
                    package_lock=_lock(sha=_root),
                    turn_id="turn-f6",
                    campaign_id="campaign-expiry",
                    initial_state=(StateValue("clockwork", "counter", 0),),
                )
            ).campaign_id
            receipt = owner.execute_action(
                _action(campaign_id, action_id="action-expiry-1", owner_id="owner-expiry")
            )
            bus = LocalCommandBus(supervisor)
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                pass

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-expiry",
                turn_id="turn-f6",
                cancel=canceller,
                listener=listener,
                last_action_id=receipt.action_id,
            )
            # Signal disconnect, then advance the clock past the grace window.
            supervisor.signal_disconnect(campaign_id, "attempt-expiry")
            clock.advance(2.0)
            # Expire the narration after the grace.
            interruption = owner.expire_narration(campaign_id, "attempt-expiry", now=clock())
            assert interruption.failed_attempt_id == "attempt-expiry"
            # The canceller was invoked by the supervisor's grace timer OR the
            # explicit expire; either way the narration is now terminal.
            snapshot = bus.snapshot(campaign_id)
            assert snapshot.revision == 1  # committed mechanics preserved
        finally:
            supervisor.shutdown()

    def test_reconnect_after_grace_elapsed_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "late-reconnect.db"
        clock = FrozenClock(0.0)
        supervisor = LocalOwnerSupervisor(
            disconnect_grace_seconds=1.0,
            clock=clock,
        )
        cred = supervisor.acquire_owner(db, "owner-late")
        supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
        try:
            _root, archive = _f6_archive()
            supervisor.install_package_archive(io.BytesIO(archive), install_id="late-install")
            owner = supervisor._owner  # type: ignore[union-attr]
            owner.register_tool(
                CatalogTool(
                    package_id="synthetic.clockwork",
                    package_version="1.0.0",
                    package_sha256=_root,
                    name="increment",
                    capability="synthetic.counter",
                    input_schema=_schema(),
                    execute=_increment([]),
                )
            )
            campaign_id = owner.create_campaign(
                CampaignCreate(
                    display_name="F6 late",
                    writer=WriterRole.DM,
                    package_lock=_lock(sha=_root),
                    turn_id="turn-f6",
                    campaign_id="campaign-late",
                    initial_state=(StateValue("clockwork", "counter", 0),),
                )
            ).campaign_id
            receipt = owner.execute_action(
                _action(campaign_id, action_id="action-late-1", owner_id="owner-late")
            )
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                pass

            owner.start_narration_attempt(
                campaign_id=campaign_id,
                attempt_id="attempt-late",
                turn_id="turn-f6",
                cancel=canceller,
                last_action_id=receipt.action_id,
            )
            owner.disconnect_narration(campaign_id, "attempt-late", now=clock())
            clock.advance(2.0)  # past the 1s grace
            with pytest.raises(CommandRejected):
                owner.reconnect_narration(
                    campaign_id,
                    "attempt-late",
                    now=clock(),
                    acknowledged_sequence=0,
                )
        finally:
            supervisor.shutdown()

    def test_stale_renderer_disconnect_cannot_cancel_reconnected_attachment(
        self, tmp_path: Path
    ) -> None:
        clock = FrozenClock(0.0)
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(
            tmp_path / "attachment-generation.db",
            clock=clock,
        )
        try:
            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-generation",
                turn_id="turn-f6",
                cancel=CountingCanceller(),
                listener=lambda _channel, _token: None,
                last_action_id=action_id,
            )
            _deadline_one, generation_one = supervisor.heartbeat_narration(
                campaign_id,
                "attempt-generation",
                expected_generation=None,
            )
            supervisor.signal_disconnect(
                campaign_id,
                "attempt-generation",
                expected_generation=generation_one,
            )
            _replay, generation_two = supervisor.reconnect_narration(
                campaign_id,
                "attempt-generation",
                acknowledged_sequence=0,
                expected_generation=generation_one,
            )
            _deadline_two, renewed_generation = supervisor.heartbeat_narration(
                campaign_id,
                "attempt-generation",
                expected_generation=generation_two,
            )
            assert generation_two > generation_one
            assert renewed_generation == generation_two

            with pytest.raises(CommandRejected):
                supervisor.signal_disconnect(
                    campaign_id,
                    "attempt-generation",
                    expected_generation=generation_one,
                )
            assert (
                supervisor.narration_attempt_status(campaign_id, "attempt-generation") == "active"
            )
        finally:
            supervisor.shutdown()
            bus.close()


# --------------------------------------------------------------------------
# Manual retry surface (touched journey gate)
# --------------------------------------------------------------------------


class TestManualRetryJourney:
    def test_retryable_interruption_is_projected_after_stop(self, tmp_path: Path) -> None:
        db = tmp_path / "retry.db"
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(db)
        try:
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                pass

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-retry",
                turn_id="turn-f6",
                cancel=canceller,
                listener=listener,
                last_action_id=action_id,
            )
            bus.interrupt_stream(campaign_id, "attempt-retry")
            owner = supervisor._owner  # type: ignore[union-attr]
            retryable = owner.retryable_interruption(campaign_id, failed_attempt_id="attempt-retry")
            assert retryable is not None
            assert retryable.failed_attempt_id == "attempt-retry"
        finally:
            supervisor.shutdown()


# --------------------------------------------------------------------------
# Shutdown cancels all live narration leases
# --------------------------------------------------------------------------


class TestSupervisorShutdown:
    def test_shutdown_cancels_pending_narration(self, tmp_path: Path) -> None:
        db = tmp_path / "shutdown.db"
        supervisor, bus, _owner_id, campaign_id, action_id = _stand_up(db)
        canceller = CountingCanceller()

        def listener(channel: ProseChannel, token: str) -> None:
            pass

        bus.attach_stream(
            campaign_id=campaign_id,
            attempt_id="attempt-shutdown",
            turn_id="turn-f6",
            cancel=canceller,
            listener=listener,
            last_action_id=action_id,
        )
        supervisor.shutdown()
        assert canceller.count == 1
        # Shutdown must durably interrupt before releasing persistence ownership.
        with AuthorityOwner.open(
            db, "owner-after-shutdown", disconnect_grace_seconds=5.0
        ) as reopened:
            retryable = reopened.retryable_interruption(
                campaign_id, failed_attempt_id="attempt-shutdown"
            )
            assert retryable is not None
            assert retryable.retryable is True
        bus.close()
