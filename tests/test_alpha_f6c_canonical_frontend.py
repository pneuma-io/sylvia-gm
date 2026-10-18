"""Exact F6C canonical frontend tracer-bullet acceptance probes.

Probes mapped to the F6C gates::

    G-OWNER       — the bus routes Campaign-Create through the supervisor;
                    the renderer never opens the database.
    G-WRITER      — execute_action routes through the verified owner only.
    G-ORDER       — the campaign index is a read-only projection.
    G-PRIVACY     — player_projection emits TALK, collapsed THINK, and
                    collapsed TOOL cards; private canonical events
                    (STATE_MUTATION, CONSEQUENCE, ACTION_COMMITTED) and
                    Inspector traces never appear in the player surface.
    G-INTERRUPT   — Stop (interrupt_stream) cancels the narration through
                    the authority command; incomplete prose is discarded.
    G-RECOVER     — reconnect_stream replays the canonical sequence and the
                    provisional tokens; manual_retry routes through the owner.
    G-NEUTRAL-SURF — the no-rules campaign rail and player projection carry no
                     D&D/TTRPG iconography or assumptions; all surfaces are
                     ruleset-neutral.

The tracer bullet also probes the canonical frontend artefacts (app.html,
app.css, app.js) for the UI-001 frame and the renderer-secretless contract.

Tests use only synthetic archives, temporary SQLite databases, and in-process
supervisor instances — no sockets, no Tauri shell.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.domain.authority import (
    CanonicalEventType,
    LifecycleState,
    MechanicContext,
    MechanicOutcome,
    NarrationInterruption,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    PlayerProjection,
    PlayerProjectionKind,
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
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor


def test_tauri_build_runs_owner_sidecar_builder_from_repository_root() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    configuration = json.loads(
        (repository_root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )

    assert configuration["build"]["beforeBuildCommand"] == (
        "uv run --locked --all-extras python src-tauri/scripts/build_owner_sidecar.py"
    )
    assert configuration["bundle"]["externalBin"] == ["binaries/sylvia-gm-owner"]
    assert configuration["bundle"]["icon"] == ["icons/icon.ico"]
    assert (repository_root / "src-tauri" / "icons" / "icon.ico").is_file()


# --------------------------------------------------------------------------
# Synthetic package + campaign fixtures (mirrors F6's shapes)
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


def _f6c_archive() -> tuple[str, bytes]:
    import hashlib

    payload = canonical_json_bytes({"fixture": "synthetic-f6c"})
    draft: dict[str, object] = {
        "id": "synthetic.f6c",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Synthetic F6C fixture"},
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
                "path": "payload/f6c.json",
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
        archive.writestr("packages/synthetic.f6c/payload/f6c.json", payload)
    return root, stream.getvalue()


def _lock(sha: str = "f" * 64) -> PackageLock:
    return PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.f6c",
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
    sha: str = "f" * 64,
) -> ActionCommand:
    return ActionCommand(
        campaign_id=campaign_id,
        action_id=action_id,
        idempotency_key=f"idem-{action_id}",
        owner_id=owner_id,
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id="turn-f6c",
        package_id="synthetic.f6c",
        tool_name="increment",
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


def _stand_up(database: Path) -> tuple[LocalOwnerSupervisor, LocalCommandBus, str]:
    """Open supervisor+owner, install package, register tool, create campaign,
    execute one committed action.  Returns (supervisor, bus, campaign_id)."""

    supervisor = LocalOwnerSupervisor(
        disconnect_grace_seconds=5.0,
        clock=FrozenClock(0.0),
    )
    cred = supervisor.acquire_owner(database, "owner-f6c")
    supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
    _root, archive = _f6c_archive()
    supervisor.install_package_archive(io.BytesIO(archive), install_id="f6c-install")
    owner = supervisor._owner  # type: ignore[union-attr]
    assert owner is not None
    calls: list[int] = []
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.f6c",
            package_version="1.0.0",
            package_sha256=_root,
            name="increment",
            capability="synthetic.counter",
            input_schema=_schema(),
            execute=_increment(calls),
        )
    )
    campaign_id = owner.create_campaign(
        CampaignCreate(
            display_name="F6C campaign",
            writer=WriterRole.DM,
            package_lock=_lock(sha=_root),
            turn_id="turn-f6c",
            campaign_id="campaign-f6c",
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    ).campaign_id
    owner.execute_action(_action(campaign_id, action_id="action-f6c-1", owner_id="owner-f6c"))
    return supervisor, LocalCommandBus(supervisor), campaign_id


# --------------------------------------------------------------------------
# G-NEUTRAL-SURF — no-rules campaign through application commands
# --------------------------------------------------------------------------


class TestNoRulesCampaignSurfaces:
    def test_create_no_rules_campaign_through_command_bus(self, tmp_path: Path) -> None:
        """A no-rules campaign (empty package lock) is created through the bus."""

        db = tmp_path / "norules.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        cred = supervisor.acquire_owner(db, "owner-norules")
        supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
        try:
            bus = LocalCommandBus(supervisor)
            command = CampaignCreate(
                display_name="No-Rules Campaign",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn-norules",
                campaign_id="campaign-norules",
                initial_state=(),
                lifecycle=LifecycleState.ACTIVE,
            )
            record = bus.create_campaign(command)
            assert record is not None
            assert record.campaign_id == "campaign-norules"
            # The campaign carries no package entries — no ruleset assumed.
            snapshot = bus.snapshot("campaign-norules")
            assert snapshot.package_lock.entries == ()
            assert snapshot.lifecycle is LifecycleState.ACTIVE
        finally:
            supervisor.shutdown()

    def test_campaign_index_projects_through_bus(self, tmp_path: Path) -> None:
        """The campaign index is a read-only projection available on the bus."""

        db = tmp_path / "index.db"
        supervisor, bus, _campaign_id = _stand_up(db)
        try:
            index = bus.campaign_index()
            assert len(index) >= 1
            entry = index[0]
            assert entry.campaign_id == "campaign-f6c"
            assert entry.lifecycle == "active"
            assert entry.writer == "dm"
            assert entry.ruleset_id == "synthetic.f6c"
            assert entry.ruleset_label == "Synthetic F6C fixture"
            assert entry.campaign_setting_id is None
            assert entry.campaign_setting_label == "No campaign setting"
            assert entry.session_activity_ordinal == 0
            # No D&D/TTRPG terminology in the projection fields.
            for field in ("display_name", "ruleset_summary", "package_summary"):
                value = getattr(entry, field)
                assert "d&d" not in str(value).lower()
                assert "cairn" not in str(value).lower()
        finally:
            supervisor.shutdown()


# --------------------------------------------------------------------------
# G-PRIVACY — player projection emits only player-safe surfaces
# --------------------------------------------------------------------------


class TestPlayerProjectionPrivacy:
    def test_projection_has_talk_think_tool_no_private_events(self, tmp_path: Path) -> None:
        """player_projection emits TALK, collapsed THINK, and collapsed TOOL cards;
        private canonical events (STATE_MUTATION, CONSEQUENCE, ACTION_COMMITTED)
        never appear."""

        db = tmp_path / "projection.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            projections = bus.player_projection(campaign_id)
            assert len(projections) > 0
            # TALK is always present (the committed narration has a TALK event).
            # THINK and TOOL_CARD are collapsed by default.
            for p in projections:
                assert isinstance(p, PlayerProjection)
                assert p.kind in {
                    PlayerProjectionKind.TALK,
                    PlayerProjectionKind.THINK,
                    PlayerProjectionKind.TOOL_CARD,
                    PlayerProjectionKind.RETRY,
                    PlayerProjectionKind.PENDING_TOOL,
                }
            # No private canonical event types appear in the player projection.
            # The projection never surfaces STATE_MUTATION, CONSEQUENCE,
            # ACTION_COMMITTED, ACTION_DECLARED, ACTION_FAILED.
            # (We verify by checking the snapshot's canonical events are a
            # superset and none of the private types appear as projections.)
            snapshot = bus.snapshot(campaign_id)
            private_types = {
                CanonicalEventType.STATE_MUTATION,
                CanonicalEventType.CONSEQUENCE,
                CanonicalEventType.ACTION_COMMITTED,
                CanonicalEventType.ACTION_DECLARED,
                CanonicalEventType.ACTION_FAILED,
                CanonicalEventType.TOOL_CALL,
            }
            for event in snapshot.events:
                if event.event_type in private_types:
                    # This event must not have a matching player projection.
                    matching = [p for p in projections if p.sequence == event.sequence]
                    assert not matching, f"Private event {event.event_type} leaked into projection"
        finally:
            supervisor.shutdown()

    def test_think_and_tool_cards_are_collapsed_by_default(self, tmp_path: Path) -> None:
        """THINK and TOOL_CARD projections are collapsed_by_default=True."""

        db = tmp_path / "collapsed.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            projections = bus.player_projection(campaign_id)
            think_cards = [p for p in projections if p.kind == PlayerProjectionKind.THINK]
            tool_cards = [p for p in projections if p.kind == PlayerProjectionKind.TOOL_CARD]
            for p in think_cards:
                assert p.collapsed_by_default is True
            for p in tool_cards:
                assert p.collapsed_by_default is True
            # TALK is not collapsed.
            for p in projections:
                if p.kind == PlayerProjectionKind.TALK:
                    assert p.collapsed_by_default is False
            tool_cards = [p for p in projections if p.kind == PlayerProjectionKind.TOOL_CARD]
            assert len(tool_cards) == 1
            assert tool_cards[0].safe_payload["tool_name"] == "increment"
            assert tool_cards[0].safe_payload["parameters"] == {"amount": 1}
        finally:
            supervisor.shutdown()

    def test_inspector_traces_not_in_player_projection(self, tmp_path: Path) -> None:
        """Private diagnostics are Inspector-only — never in player_projection."""

        db = tmp_path / "inspector.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            owner = supervisor._owner  # type: ignore[union-attr]
            assert owner is not None
            # Record a private inspector trace.
            from sylvia_gm.domain.authority import InspectorTraceSink

            owner.record_inspector_trace(
                owner_session_id=owner._owner_session_id,
                opted_in=True,
                sink=InspectorTraceSink.INSPECTOR,
                payload={"secret": "diagnostic", "token": "hidden"},
            )
            # The player projection must not include the trace.
            projections = bus.player_projection(campaign_id)
            for p in projections:
                assert "secret" not in str(p.safe_payload)
                assert "token" not in str(p.safe_payload)
                assert "diagnostic" not in str(p.text or "")
            # The inspector traces are available only through the owner.
            traces = owner.inspector_traces(owner._owner_session_id)
            assert len(traces) == 1
            assert traces[0]["secret"] == "diagnostic"
        finally:
            supervisor.shutdown()


# --------------------------------------------------------------------------
# G-INTERRUPT + G-RECOVER — streaming, Stop, Retry, reconnect through bus
# --------------------------------------------------------------------------


class TestStreamingStopRetryRecover:
    def test_stop_cancels_through_authority_command(self, tmp_path: Path) -> None:
        """Stop invokes interrupt_stream (authority command); incomplete prose
        is discarded and committed mechanics preserved."""

        db = tmp_path / "stop-f6c.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                pass

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-stop",
                turn_id="turn-f6c",
                cancel=canceller,
                listener=listener,
            )
            owner = supervisor._owner  # type: ignore[union-attr]
            assert owner is not None
            # Emit provisional incomplete prose.
            owner.emit_narration_token(
                campaign_id, "attempt-stop", ProseChannel.TALK, "unfinished..."
            )
            bus.emit_token(campaign_id, "attempt-stop", ProseChannel.TALK, "unfinished...")
            # Stop through the bus.
            interruption = bus.interrupt_stream(campaign_id, "attempt-stop")
            assert isinstance(interruption, NarrationInterruption)
            assert interruption.failed_attempt_id == "attempt-stop"
            assert interruption.retryable is True
            # The committed mechanics are preserved (counter = 1).
            snapshot = bus.snapshot(campaign_id)
            assert snapshot.revision == 1
            assert snapshot.state.get("clockwork.counter") == 1
        finally:
            supervisor.shutdown()

    def test_manual_retry_routes_through_bus(self, tmp_path: Path) -> None:
        """manual_retry is invoked through the bus (authority command), not
        client-side. No automatic retry."""

        db = tmp_path / "retry-f6c.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                pass

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-retry",
                turn_id="turn-f6c",
                cancel=canceller,
                listener=listener,
            )
            owner = supervisor._owner  # type: ignore[union-attr]
            assert owner is not None
            owner.emit_narration_token(campaign_id, "attempt-retry", ProseChannel.TALK, "partial")
            bus.emit_token(campaign_id, "attempt-retry", ProseChannel.TALK, "partial")
            interruption = bus.interrupt_stream(campaign_id, "attempt-retry")
            # The bus exposes the retryable interruption projection.
            retry_basis = bus.retryable_interruption(campaign_id, failed_attempt_id="attempt-retry")
            assert retry_basis is not None
            assert retry_basis.interruption_id == interruption.interruption_id
            # Manual retry through the bus — no automatic fallback.
            retry_result = bus.manual_retry(
                campaign_id=campaign_id,
                interruption_id=interruption.interruption_id,
                retry_id="retry-manual",
                narrator=lambda _input, stream: stream.emit(
                    ProseChannel.TALK, "Retried narration."
                ),
            )
            assert retry_result is not None
            assert retry_result.replayed is False
            # The retry's canonical events include a TALK.
            talk_events = [
                e for e in retry_result.canonical if e.event_type is CanonicalEventType.TALK
            ]
            assert len(talk_events) >= 1
        finally:
            supervisor.shutdown()

    def test_reconnect_replays_canonical_and_provisional(self, tmp_path: Path) -> None:
        """reconnect_stream replays the canonical sequence and provisional tokens."""

        db = tmp_path / "reconnect-f6c.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            received: list[str] = []
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                received.append(token)

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-reconnect",
                turn_id="turn-f6c",
                cancel=canceller,
                listener=listener,
            )
            owner = supervisor._owner  # type: ignore[union-attr]
            assert owner is not None
            owner.emit_narration_token(
                campaign_id, "attempt-reconnect", ProseChannel.TALK, "streaming..."
            )
            bus.emit_token(campaign_id, "attempt-reconnect", ProseChannel.TALK, "streaming...")
            # Disconnect.
            supervisor.signal_disconnect(campaign_id, "attempt-reconnect")
            # Reconnect within the grace window.
            replay = bus.reconnect_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-reconnect",
                acknowledged_sequence=0,
                listener=listener,
            )
            assert replay.canonical is not None
            assert replay.high_water >= 1
            assert replay.attachment_generation > 0
            # The provisional token is replayed.
            assert "streaming..." in received
        finally:
            supervisor.shutdown()

    def test_no_automatic_retry_on_failure(self, tmp_path: Path) -> None:
        """When a narration is interrupted, the bus does not auto-retry;
        the player must invoke manual_retry explicitly."""

        db = tmp_path / "no-auto.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            canceller = CountingCanceller()

            def listener(channel: ProseChannel, token: str) -> None:
                pass

            bus.attach_stream(
                campaign_id=campaign_id,
                attempt_id="attempt-no-auto",
                turn_id="turn-f6c",
                cancel=canceller,
                listener=listener,
            )
            owner = supervisor._owner  # type: ignore[union-attr]
            assert owner is not None
            owner.emit_narration_token(
                campaign_id, "attempt-no-auto", ProseChannel.TALK, "incomplete"
            )
            bus.emit_token(campaign_id, "attempt-no-auto", ProseChannel.TALK, "incomplete")
            bus.interrupt_stream(campaign_id, "attempt-no-auto")
            # No retry attempt is created automatically.
            projections = bus.player_projection(campaign_id)
            retry_projections = [p for p in projections if p.kind == PlayerProjectionKind.RETRY]
            # A RETRY projection is available (for the player to act on),
            # but no automatic retry attempt has been started.
            assert len(retry_projections) >= 1
            # There is no live narration attempt for this campaign.
            live_attempts = [
                a
                for a in owner._attempts.values()
                if a.campaign_id == campaign_id and not a.terminal
            ]
            assert len(live_attempts) == 0
        finally:
            supervisor.shutdown()


# --------------------------------------------------------------------------
# G-OWNER / G-WRITER — bus routes through the verified supervisor only
# --------------------------------------------------------------------------


class TestBusRoutingGates:
    def test_stopped_session_remains_projected_for_lifecycle_controls(self, tmp_path: Path) -> None:
        db = tmp_path / "lifecycle-projection.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        credential = supervisor.acquire_owner(db, "owner-lifecycle-projection")
        supervisor.complete_handshake(derive_owner_response(credential.secret_hex))
        bus = LocalCommandBus(supervisor)
        try:
            campaign_id = "campaign-lifecycle-projection"
            session_id = "session-lifecycle-projection"
            turn_id = "turn-lifecycle-projection"
            bus.create_campaign(
                CampaignCreate(
                    display_name="Lifecycle projection",
                    writer=WriterRole.DM,
                    package_lock=PackageLock(entries=()),
                    turn_id=turn_id,
                    campaign_id=campaign_id,
                )
            )
            bus.create_agent_session(
                campaign_id=campaign_id,
                role=WriterRole.DM,
                provider_id="synthetic-provider",
                model_id="synthetic-model",
                session_id=session_id,
            )
            bus.stop_agent_session(session_id)
            bus.complete_turn(campaign_id, turn_id)

            entry = bus.campaign_index()[0]
            assert entry.playable is False
            assert entry.session_id == session_id
            assert entry.session_status == "stopped"
            assert entry.turn_id == turn_id
            assert entry.turn_status == "completed"
        finally:
            supervisor.shutdown()

    def test_create_campaign_blocked_without_verified_handshake(self, tmp_path: Path) -> None:
        db = tmp_path / "unverified-f6c.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        supervisor.acquire_owner(db, "owner-unverified")
        try:
            bus = LocalCommandBus(supervisor)
            command = CampaignCreate(
                display_name="Blocked",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn-blocked",
                campaign_id="campaign-blocked",
            )
            with pytest.raises(CommandRejected, match="handshake"):
                bus.create_campaign(command)
        finally:
            supervisor.shutdown()

    def test_execute_action_blocked_without_verified_handshake(self, tmp_path: Path) -> None:
        db = tmp_path / "unverified-action.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        supervisor.acquire_owner(db, "owner-unverified-action")
        try:
            bus = LocalCommandBus(supervisor)
            with pytest.raises(CommandRejected, match="handshake"):
                bus.execute_action(
                    ActionCommand(
                        campaign_id="x",
                        action_id="a",
                        idempotency_key="i",
                        owner_id="owner-unverified-action",
                        writer=WriterRole.DM,
                        expected_revision=0,
                        turn_id="t",
                        package_id="p",
                        tool_name="n",
                        parameters={},
                    )
                )
        finally:
            supervisor.shutdown()

    def test_player_projection_blocked_without_verified_handshake(self, tmp_path: Path) -> None:
        db = tmp_path / "unverified-proj.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        supervisor.acquire_owner(db, "owner-unverified-proj")
        try:
            bus = LocalCommandBus(supervisor)
            with pytest.raises(CommandRejected, match="handshake"):
                bus.player_projection("any")
        finally:
            supervisor.shutdown()


# --------------------------------------------------------------------------
# Renderer-secretless contract + UI-001 frame — canonical artefact probes
# --------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "src-tauri" / "frontend"


def _renderer_runtime_text(path: Path) -> str:
    """Return executable/rendered source with explanatory comments removed.

    Security and neutrality assertions target values that can reach the
    renderer, not documentation that names the prohibited category while
    explaining why it is absent.
    """

    content = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        return re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", content)


# Secret patterns that must never appear in the renderer artefacts.
SECRET_PATTERNS = [
    "bearer ",
    "authorization:",
    "secret_hex",
    "expected_response_hex",
    "database",
    ".db",
    "sqlite",
    "8700",
    "8701",
    "8702",
]


class TestRendererSecretless:
    def test_secret_patterns_absent_from_frontend_files(self) -> None:
        """No bearer token, port, database path, or secret appears in the
        renderer artefacts (app.html, app.css, app.js)."""

        assert FRONTEND_DIR.exists(), f"Frontend directory missing: {FRONTEND_DIR}"
        artefacts = list(FRONTEND_DIR.glob("app.*"))
        assert len(artefacts) >= 3, f"Expected app.html, app.css, app.js; got {artefacts}"
        for path in artefacts:
            content = _renderer_runtime_text(path).lower()
            for pattern in SECRET_PATTERNS:
                assert pattern.lower() not in content, (
                    f"Secret pattern '{pattern}' found in {path.name}"
                )

    def test_no_remote_content_loading(self) -> None:
        """The renderer never loads remote content (no http/https src)."""

        for path in FRONTEND_DIR.glob("app.*"):
            content = path.read_text(encoding="utf-8").lower()
            # The bridge name and local asset references are allowed; remote URLs are not.
            assert "http://" not in content or "__sylviabridge" in content
            assert "https://" not in content

    def test_no_dnd_ttrpg_assumptions_in_frontend(self) -> None:
        """No D&D/TTRPG iconography or assumptions in the canonical frontend."""

        dnd_patterns = [
            "d&d",
            "dnd",
            "ttrpg",
            "cairn",
            "srd",
            "initiative",
            "hit points",
            "hp bar",
            "armor class",
            "spell slot",
            "saving throw",
            "ability check",
        ]
        for path in FRONTEND_DIR.glob("app.*"):
            content = _renderer_runtime_text(path).lower()
            for pattern in dnd_patterns:
                assert pattern not in content, f"D&D/TTRPG pattern '{pattern}' found in {path.name}"


class TestUI001Frame:
    def test_sidebar_brand_and_builder_session_labels_match_product_language(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")

        assert '<span class="portrait-label">Sylvia</span>' in html
        assert '<span class="portrait-sub">Game Master</span>' in html
        assert (
            "display: block"
            in css.split(".portrait-frame .portrait-label {", maxsplit=1)[1].split("}", maxsplit=1)[
                0
            ]
        )
        assert (
            "text-transform: uppercase"
            not in css.split(".portrait-frame .portrait-sub {", maxsplit=1)[1].split(
                "}", maxsplit=1
            )[0]
        )
        assert '<h2 id="builder-launcher-title">Builder Sessions</h2>' in html
        assert "No Builder sessions yet." in html
        assert "No Builder chats yet." not in html

    def test_home_has_a_functional_heading_without_a_greeting_slogan(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")

        home_header = html.split('<header class="home-header">', maxsplit=1)[1].split(
            "</header>", maxsplit=1
        )[0]
        assert '<h1 class="home-kicker">Home</h1>' in home_header
        assert "Welcome back." not in html

    def test_existing_player_character_is_selected_without_a_rename_field(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        assert 'id="rift-player-name"' not in html
        assert "Player Character name" not in html
        assert "player_name" not in js
        start_rift_command = rust.split("fn start_rift_campaign(", maxsplit=1)[1].split(
            "fn start_guided_campaign(", maxsplit=1
        )[0]
        assert "player_name" not in start_rift_command

    def test_specific_requests_has_no_manual_customization_button(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")

        specific_requests = html.split('data-step-id="specific_requests"', maxsplit=1)[1].split(
            'data-step-id="review"', maxsplit=1
        )[0]
        review = html.split('data-step-id="review"', maxsplit=1)[1].split(
            '<div class="construction-actions">', maxsplit=1
        )[0]
        assert "Open Campaign Customization" not in specific_requests
        assert 'data-start-creation-subflow="campaign_customization"' not in specific_requests
        assert "Keep Building" in review
        assert 'data-start-creation-subflow="campaign_customization"' in review

        advance = js.split("async function advanceConstructionInterview()", maxsplit=1)[1]
        advance = advance.split("async function startCreationSubflow(", maxsplit=1)[0]
        assert "currentStep === 'specific_requests'" in advance
        assert "!captured.value.no_additional_requests" in advance
        assert "await startCreationSubflow('campaign_customization', captured.visible)" in advance

    def test_talk_markdown_preserves_structure_and_rejects_unsafe_markup(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        source = (
            "# Arrival\n\n"
            "The **door** opens.\nSecond line.\n\n"
            "- One\n- Two\n\n"
            "<script>alert(1)</script>\n\n"
            "[bad](javascript:alert(1))"
        )
        script = (
            "global.window={};"
            "global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            "process.stdout.write(window.SylviaGMFrontend.renderNarrationMarkdown("
            f"{json.dumps(source)}));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        rendered = completed.stdout

        assert "<h1>Arrival</h1>" in rendered
        assert "<p>The <strong>door</strong> opens.<br>Second line.</p>" in rendered
        assert "<ul><li>One</li><li>Two</li></ul>" in rendered
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
        assert "<script>" not in rendered
        assert 'href="javascript:' not in rendered

    def test_talk_markdown_renders_pipe_tables_and_keeps_cells_safe(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        source = (
            "| Ability | Base | Adjustment | Final | Modifier |\n"
            "| :--- | ---: | --- | ---: | ---: |\n"
            "| Strength | 10 | +2 | **12** | +1 |\n"
            "| Notes | two \\| choices | [safe](https://example.com) | "
            "<script>alert(1)</script> | [bad](javascript:alert(1)) |\n\n"
            "Name | Detail\n"
            "--- | ---\n"
            "Feature | `one|two`"
        )
        script = (
            "global.window={};"
            "global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            "process.stdout.write(window.SylviaGMFrontend.renderNarrationMarkdown("
            f"{json.dumps(source)}));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        rendered = completed.stdout

        assert rendered.startswith('<div class="markdown-table-wrap"><table>')
        assert '<thead><tr><th scope="col">Ability</th><th scope="col">Base</th>' in rendered
        assert "<td>Strength</td><td>10</td><td>+2</td><td><strong>12</strong></td>" in rendered
        assert "<td>Notes</td><td>two | choices</td>" in rendered
        assert 'href="https://example.com"' in rendered
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
        assert "<script>" not in rendered
        assert 'href="javascript:' not in rendered
        assert '<th scope="col">Name</th><th scope="col">Detail</th>' in rendered
        assert "<td>Feature</td><td><code>one|two</code></td>" in rendered

        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        table_styles = css.split(".markdown-table-wrap", maxsplit=1)[1].split(
            ".story-card.think", maxsplit=1
        )[0]
        assert "overflow-x: auto" in table_styles

    def test_talk_markdown_does_not_treat_pipe_prose_or_fenced_tables_as_tables(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        source = (
            "Choose north | south when the road forks.\n\n"
            "```markdown\n"
            "Name | Score\n"
            "--- | ---\n"
            "Nessa | 12\n"
            "```"
        )
        script = (
            "global.window={};"
            "global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            "process.stdout.write(window.SylviaGMFrontend.renderNarrationMarkdown("
            f"{json.dumps(source)}));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )

        assert completed.stdout == (
            "<p>Choose north | south when the road forks.</p>"
            '<pre><code class="language-markdown">Name | Score\n--- | ---\nNessa | 12</code></pre>'
        )

    def test_talk_markdown_normalizes_common_latex_arrows(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        source = (
            "Rift $\\rightarrow$ breach; \\rightarrow warning; "
            "$\\leftarrow$ retreat; \\leftarrow safety; "
            "$\\leftrightarrow$ flux; \\leftrightarrow passage. "
            "A $5 toll, C:\\\\Rift\\\\notes, and \\ordinary text stay literal."
        )
        script = (
            "global.window={};"
            "global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            "process.stdout.write(window.SylviaGMFrontend.renderNarrationMarkdown("
            f"{json.dumps(source)}));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )

        assert completed.stdout == (
            "<p>Rift → breach; → warning; ← retreat; ← safety; ↔ flux; ↔ passage. "
            "A $5 toll, C:\\\\Rift\\\\notes, and \\ordinary text stay literal.</p>"
        )

    def test_campaign_index_failure_is_not_rendered_as_an_empty_library(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        refresh = js.split("async function refreshCampaignIndex()", maxsplit=1)[1].split(
            "function renderCampaignRail()", maxsplit=1
        )[0]
        rail = js.split("function renderCampaignRail()", maxsplit=1)[1].split(
            "function panelLayout()", maxsplit=1
        )[0]

        assert "campaignIndexStatus" in refresh
        assert "campaignIndexRequestId: 0" in js
        assert "const requestId = ++state.campaignIndexRequestId;" in refresh
        assert refresh.count("if (requestId !== state.campaignIndexRequestId) return;") == 2
        assert "state.campaigns = []" not in refresh.split("catch (e)", maxsplit=1)[1]
        assert "Loading campaigns" in rail
        assert "Campaign list could not be loaded" in rail
        assert "Retry" in rail
        assert "No campaigns yet" in rail

    def test_talk_keeps_one_identity_and_typography_through_commit(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        script = (
            "global.window={};"
            "global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            "const api=window.SylviaGMFrontend;"
            "process.stdout.write(JSON.stringify({"
            "provisional:api.narrationProjectionIdentity({"
            "kind:'talk',provisional:true,attempt_id:'attempt-1'}),"
            "committed:api.narrationProjectionIdentity({"
            "kind:'talk',sequence:7,attempt_id:'attempt-1'})}));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        identities = json.loads(completed.stdout)
        assert identities == {
            "provisional": "narration-attempt-1-talk",
            "committed": "narration-attempt-1-talk",
        }

        js = _renderer_runtime_text(frontend)
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        projection = js.split("function renderProjection(p,", maxsplit=1)[1].split(
            "function selectedCampaignPlan()", maxsplit=1
        )[0]
        provisional = css.split(".story-card.provisional {", maxsplit=1)[1].split("}", maxsplit=1)[
            0
        ]
        assert "renderResponseBlocks(payload.response_blocks, p.text || '')" in projection
        assert "opacity: 0.7" not in provisional
        assert "font-style: italic" not in provisional

    def test_html_has_despilled_portrait(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        portrait = FRONTEND_DIR / "assets" / "sylvia-portrait.png"
        assert "despilled-portrait" in html
        assert "portrait-col" in html
        assert "portrait-frame" in html
        assert "Game Master" in html
        assert "Session Guide" not in html
        assert portrait.read_bytes().hex()[:16]
        assert hashlib.sha256(portrait.read_bytes()).hexdigest() == (
            "fafdA1d16d452fb0e5525a936fb2ee0cc06b478b082f8973ebeb9cc7c151abc2".lower()
        )
        portrait_css = css.split(".portrait-frame .despilled-portrait {", maxsplit=1)[1]
        portrait_css = portrait_css.split("}", maxsplit=1)[0]
        assert "object-fit: contain" in portrait_css
        assert "border:" not in portrait_css
        assert "border-radius" not in portrait_css

    def test_html_has_central_chronology(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        assert "chronology" in html
        assert "chronology-col" in html
        assert "story-card" in js  # chronology cards are rendered dynamically

    def test_html_has_declarative_ruleset_panel_host(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        assert "right-sidebar" in html
        assert "sidebar-resize-handle" in html
        assert 'id="ruleset-panel-host"' in html
        assert 'id="ruleset-panels"' in html
        assert "right-sidebar" in css
        assert "sidebar-resize-handle" in css
        assert ".ruleset-panel-group" in css
        assert ".ruleset-panel-tabs" in css
        assert ".panel-hp-bar" in css
        assert "invoke('campaign_panels'" in js
        assert "function renderRulesetPanelProjection(" in js
        assert "function renderPartyPanel(" in js
        assert "function renderEncounterPanel(" in js
        assert "Array.isArray(combat.order)" in js
        assert "function renderInventoryPanel(" in js
        assert "function renderObjectivesPanel(" in js
        assert "data-panel-action-id" in js
        assert "execute_panel_action" in js
        assert "document.querySelectorAll('[data-panel-action-id]')" in js
        assert "Use the package mechanic" not in js
        assert "PANEL_LAYOUT_KEY" in js
        assert "sidebar_width" in js
        assert "panel_sizes" in js
        assert "data-panel-size-id" in js
        assert "resize: vertical" in css

    def test_dedicated_settings_replaces_the_crowded_operations_rail(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")

        rail = html[
            html.index('class="right-sidebar"') : html.index("<!-- Dedicated settings surface")
        ]
        settings = html[html.index("<!-- Dedicated settings surface") :]
        assert 'id="open-settings-btn"' in rail
        for relocated_control in (
            "model-choice",
            "install-package-btn",
            "rename-campaign-btn",
            "archive-campaign-btn",
            "restore-campaign-btn",
            "trash-campaign-btn",
            "purge-campaign-btn",
            "export-campaign-btn",
            "recover-campaign-btn",
        ):
            assert f'id="{relocated_control}"' not in rail
            assert f'id="{relocated_control}"' in settings
        for tab in ("models", "appearance", "packages", "campaign-data", "about"):
            assert f'data-settings-tab="{tab}"' in settings
            assert f'data-settings-panel="{tab}"' in settings
        assert 'role="dialog"' in settings
        assert 'aria-modal="true"' in settings
        assert 'role="tablist"' in settings
        assert settings.count('role="tabpanel"') == 6
        assert ".settings-overlay[hidden]" in css
        assert "VALID_SETTINGS_TABS" in js
        assert "function openSettings(" in js
        assert "function closeSettings(" in js
        assert "event.key === 'Escape'" in js
        assert "event.key !== 'Tab'" in js
        assert "element.offsetParent !== null" in js
        assert 'id="lifecycle-actions" hidden' in rail
        assert ".lifecycle-actions[hidden]" in css
        assert ".lifecycle-actions .btn[hidden]" in css
        assert "$('lifecycle-actions').hidden = !(canStop || canResume || canFinalize)" in js
        assert "control.hidden = !available" in js
        assert "Subscription" not in settings
        assert "Account" not in settings

    def test_css_has_session_themes(self) -> None:
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        # Three colour-only themes scoped to the central chronology.
        assert '[data-theme="slate"]' in css or ":root" in css
        assert '[data-theme="sylvia"]' in css
        assert '[data-theme="sylvia-dark"]' in css

    def test_js_has_authority_command_routing(self) -> None:
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        # The frontend routes through the bridge, not through local truth.
        assert "__sylviaGMBridge" in js or "sylviaGMBridge" in js
        assert "player_projection" in js
        assert "campaign_index" in js
        assert "confirm_new_campaign" in js
        assert "begin_campaign_construction" in js
        assert "finalize_campaign_construction" in js
        assert "create_no_rules_campaign" not in js
        assert "create_campaign_from_candidate" not in js
        assert "execute_action" not in js
        assert "interrupt_stream" in js
        assert "interrupt_provider_stream" in js
        assert "steer_session" in js
        assert "manual_retry_session" in js
        assert "reconnect_stream" in js
        assert "designatedSessionId" in js
        assert "entry.sessions" in js
        assert "session_context" in js
        assert "activeSessionRole" in js
        assert "renderDMSession" in js
        assert "message.kind === 'evidence'" in js
        assert "canonical_reference" in js
        assert "disconnect_stream" in js
        assert "disconnect_provider_stream" in js
        assert "pagehide" in js
        assert "restoreDisconnectedStream" in js
        assert "narration_attempt_status" in js
        assert "provider_stream_status" in js
        assert "stream_heartbeat" in js
        assert "provider_stream_heartbeat" in js
        assert "reconnect_provider_stream" in js
        assert "session_id: state.activeSessionId" in js
        assert "sessionActivities: new Map()" in js
        assert "persistDisconnectedActivities" in js
        assert "activity.sessionRole === 'builder' ||" in js
        assert "hint.attachment_generation != null" in js
        assert "startStreamHeartbeat" in js
        assert "stopStreamHeartbeat" in js
        assert "activity.attachmentGeneration = replay.attachment_generation" in js
        assert "activity.restored" in js
        assert "setInterval(() => { void poll(); }, 500)" in js
        assert "turn-dm-${Date.now()}" not in js
        assert "result.handoff.dm_session_id" in js

    def test_purge_clears_selected_campaign_frame(self) -> None:
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        purge = js[js.index("async function purgeSelectedCampaign") :]
        purge = purge[: purge.index("async function createNoRulesCampaign")]
        assert "clearCampaignFrame();" in purge

    def test_html_exposes_construction_and_lifecycle_controls(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        for control_id in (
            "new-campaign-btn",
            "install-package-btn",
            "construction-panel",
            "finalize-construction-btn",
            "rename-campaign-btn",
            "archive-campaign-btn",
            "restore-campaign-btn",
            "trash-campaign-btn",
            "purge-campaign-btn",
        ):
            assert f'id="{control_id}"' in html

    def test_campaign_and_conversation_text_controls_disable_saved_form_suggestions(
        self,
    ) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

        for control_id in (
            "player-input",
            "construction-name",
            "construction-boundaries",
            "construction-preferences",
            "construction-premise",
        ):
            control = re.search(
                rf'<(?:input|textarea)\b[^>]*\bid="{control_id}"[^>]*>',
                html,
            )
            assert control is not None
            assert 'autocomplete="off"' in control.group(0)
        assert '`<textarea rows="3" maxlength="2000" autocomplete="off" `' in js

    def test_local_developer_ui_shows_safe_mechanics_diagnostics_without_full_audit(
        self,
    ) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

        assert "Privacy-safe operation rejection diagnostics are always available" in html
        assert "safe operation diagnostics" in js
        assert "safe_mechanics_diagnostics" in js
        assert "state.adminAuditTraces = [...safeDiagnostics, ...traces]" in js

    def test_guided_rift_interview_uses_the_owner_bridge(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        rust = (Path(__file__).resolve().parents[1] / "src-tauri" / "src" / "lib.rs").read_text(
            encoding="utf-8"
        )

        for control_id in (
            "construction-template",
            "construction-package",
            "construction-setting",
            "construction-boundaries",
            "construction-preferences",
            "construction-no-requests",
            "save-reusable-plan-btn",
            "rift-archetype",
            "rift-companions",
            "rift-companion-policy",
            "rift-create-player",
            "rift-create-companion",
        ):
            assert f'id="{control_id}"' in html
        assert "rift_template" in js
        assert "start_rift_campaign" in js
        assert "companion_ids: companionIds" in js
        assert "function applyStartingPartyPolicy()" in js
        assert "function enforceCompanionMaximum()" in js
        assert "input.dataset.required !== 'true'" in js
        assert "item.campaign_ready === false" in js
        assert "input.dataset.profileRequired" in js
        assert "Complete Character File first" in js
        assert "Create or Complete Player Character" in html
        assert "Create or Complete Companion" in html
        assert "playerPolicy.mode === 'fixed'" in js
        assert "policy.custom_companions?.minimum" in js
        assert "create_guided_campaign_draft" in js
        assert "advance_guided_campaign_draft" in js
        assert "guided_campaign_contribution" in js
        assert "campaign_creation_catalog" in js
        assert "save_reusable_campaign_plan" in js

        deterministic_boundary_path = (
            "currentStep === 'specific_requests' &&\n        !captured.value.no_additional_requests"
        )
        assert deterministic_boundary_path in js
        assert "hardBoundaries" not in js
        assert "I want to start a new campaign." not in js
        assert "await finalizeSelectedConstruction();" in js
        assert "friendlyCommandError(\n        'Construction could not start'" in js
        assert '"rift_template"' in rust
        assert '"start_rift_campaign"' in rust
        assert '"guided_campaign_contribution"' in rust
        assert '"save_reusable_campaign_plan"' in rust
        assert "rift_template," in rust
        assert "start_rift_campaign," in rust

    def test_navigation_restores_guided_interview_before_chronology_clear(self) -> None:
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

        show_home = js.split("function showHome()", maxsplit=1)[1].split("function ", maxsplit=1)[0]
        open_campaign = js.split("async function openCampaign", maxsplit=1)[1].split(
            "function updateLifecycleControls", maxsplit=1
        )[0]
        assert "hideConstructionPanel(false);" in show_home
        assert open_campaign.index("hideConstructionPanel(false);") < open_campaign.index(
            "await renderPlayerProjection(campaignId, openingSessionId);"
        )

    def test_guided_campaign_resume_selects_the_highest_construction_revision(self) -> None:
        frontend = (FRONTEND_DIR / "app.js").resolve().as_posix()
        messages = [
            {
                "sequence": 17,
                "kind": "evidence",
                "content": {
                    "kind": "campaign_interview_state",
                    "construction_revision": 8,
                    "current_step": "ready",
                },
            },
            {
                "sequence": 3,
                "kind": "evidence",
                "content": {
                    "kind": "campaign_interview_state",
                    "construction_revision": 1,
                    "current_step": "ruleset",
                },
            },
        ]
        script = (
            "global.window={};"
            "global.document={addEventListener(){}};"
            f"require({json.dumps(frontend)});"
            "const result=window.SylviaGMFrontend.latestCampaignInterviewState("
            f"{json.dumps(messages)});"
            "process.stdout.write(JSON.stringify(result));"
        )

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )

        assert json.loads(completed.stdout) == messages[0]["content"]

    @pytest.mark.parametrize("selection", ["sylvia.srd-lite.bg3", "candidate-older-install"])
    @pytest.mark.parametrize("contained_snapshot", [False, True])
    def test_campaign_compatibility_executes_against_owner_catalog_bindings(
        self, selection: str, contained_snapshot: bool
    ) -> None:
        frontend = (FRONTEND_DIR / "app.js").resolve().as_posix()
        catalog = [
            {
                "kind": "ruleset",
                "id": "sylvia.srd-lite.bg3",
                "version": "0.3.0-alpha05",
                "content_sha256": "a" * 64,
                "selection_id": "sylvia.srd-lite.bg3",
                "selection_aliases": ["candidate-older-install"],
                "compatible_settings": [
                    {
                        "id": "pneuma.severed-lands",
                        "version": "1.0.0-alpha.2",
                        "content_sha256": "b" * 64,
                    }
                ],
            },
            {
                "kind": "campaign_setting",
                "id": "pneuma.severed-lands",
                "version": "1.0.0-alpha.2",
                "content_sha256": "b" * 64,
                "title": "Severed Lands",
            },
            {
                "kind": "campaign_plan",
                "id": "the-rift-awakening",
                "title": "The Rift Awakening",
                "ruleset": {
                    "id": "sylvia.srd-lite.bg3",
                    "version": "0.3.0-alpha05",
                    "content_sha256": "a" * 64,
                },
                "campaign_setting": {
                    "id": "pneuma.severed-lands",
                    "version": "1.0.0-alpha.2",
                    "content_sha256": "b" * 64,
                },
            },
            {
                "kind": "campaign_plan",
                "id": "plan.wrong-setting",
                "title": "Wrong Plan",
                "ruleset": {
                    "id": "sylvia.srd-lite.bg3",
                    "version": "0.3.0-alpha05",
                    "content_sha256": "a" * 64,
                },
                "campaign_setting": {
                    "id": "setting.other",
                    "version": "2.0.0",
                    "content_sha256": "c" * 64,
                },
            },
        ]
        if contained_snapshot:
            catalog[0]["compatible_settings"][0]["ruleset"] = dict(catalog[2]["ruleset"])
            catalog[0]["version"] = "9.0.0"
            catalog[0]["content_sha256"] = "d" * 64
            # An older Plan keeps its Setting copy while sharing the logical world choice.
            catalog[2]["campaign_setting"]["version"] = "1.0.0-alpha.1"
            catalog[2]["campaign_setting"]["content_sha256"] = "e" * 64
        script = (
            "global.window={};"
            "global.document={addEventListener(){}};"
            f"require({json.dumps(frontend)});"
            "const result=window.SylviaGMFrontend.resolveCampaignCreationOptions("
            f"{json.dumps(catalog)},{json.dumps(selection)},'pneuma.severed-lands');"
            "process.stdout.write(JSON.stringify({"
            "ruleset:result.ruleset,settings:result.settings,"
            "plans:result.plans.map((item)=>item.id)}));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        assert result["ruleset"]["id"] == "sylvia.srd-lite.bg3"
        assert [item["id"] for item in result["settings"]] == ["pneuma.severed-lands"]
        assert result["plans"] == ["the-rift-awakening"]

    def test_campaign_selector_has_no_cross_artifact_rift_shortcut(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        forbidden_fake_ruleset = "The Rift " + "Ruleset"
        forbidden_fake_setting = "The Rift Awakening " + "setting"

        assert forbidden_fake_ruleset not in html
        assert forbidden_fake_ruleset not in js
        assert forbidden_fake_setting not in html
        assert forbidden_fake_setting not in js
        assert "ruleset_selection: 'the-rift-awakening'" not in js
        assert "setting_id: 'the-rift-awakening'" not in js

    def test_native_package_picker_is_wired_without_renderer_file_bytes(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        assert "Install Package…" in html
        assert "invoke('install_package_file'" in js
        assert "archiveBase64" not in js
        assert "FileDialog::new()" in rust
        assert '"install_package_file"' in rust
        assert "source_path" in rust

    def test_installed_ruleset_browser_reads_static_files_and_exports_through_host(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        for identity in (
            "ruleset-candidate-select",
            "ruleset-package-list",
            "ruleset-file-list",
            "ruleset-file-preview",
            "ruleset-export-btn",
            "ruleset-compare-select",
            "ruleset-compare-result",
        ):
            assert f'id="{identity}"' in html
        assert "Installed rulesets" in html
        assert "Gameplay resolution" in html
        assert "invoke('inspect_package_candidate'" in js
        assert "invoke('package_candidate_text'" in js
        assert "invoke('compare_package_candidates'" in js
        assert "invoke('export_package_candidate_file'" in js
        assert "archiveBase64" not in js
        for command in (
            "inspect_package_candidate",
            "package_candidate_text",
            "compare_package_candidates",
            "export_package_candidate_file",
        ):
            assert f"fn {command}(" in rust
            assert command in rust.split("tauri::generate_handler![", maxsplit=1)[1]
        exporter = rust.split("fn export_package_candidate_file(", maxsplit=1)[1]
        exporter = exporter.split("#[tauri::command]", maxsplit=1)[0]
        assert ".sylvia-gm-package" in exporter
        assert "artifact_path" not in js

    def test_builder_source_picker_and_web_permission_are_private_and_explicit(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        assert 'id="attach-builder-sources-btn"' in html
        assert 'id="allow-public-web"' in html
        assert "for the next message only" in html
        assert 'aria-pressed="false"' in html
        assert "invoke('ingest_builder_sources'" in js
        assert (
            "allow_public_web: !builderStart && sessionRole === 'builder' "
            "&& webResearchPermission()"
        ) in js
        assert "setWebResearchPermission(false)" in js
        assert "webResearchPermission()" in js
        assert "fn ingest_builder_sources(" in rust
        picker = rust.split("fn ingest_builder_sources(", maxsplit=1)[1]
        picker = picker.split("#[tauri::command]", maxsplit=1)[0]
        assert ".pick_files()" in picker
        assert '"ingest_builder_sources"' in picker
        assert "file_name" not in picker
        assert "openCampaign(item.dataset.campaignId, item.dataset.sessionId || null)" in js

    def test_session_submission_maps_internal_fields_to_tauri_arguments(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        bridge = js.split("submit_session_input: (command) =>", maxsplit=1)[1]
        bridge = bridge.split("ingest_builder_sources:", maxsplit=1)[0]

        assert "invoke('submit_session_input', {" in bridge
        for tauri_name, internal_name in (
            ("sessionId", "session_id"),
            ("turnId", "turn_id"),
            ("attemptId", "attempt_id"),
            ("messageId", "message_id"),
            ("userText", "user_text"),
            ("allowPublicWeb", "allow_public_web"),
        ):
            assert f"{tauri_name}: command.{internal_name}" in bridge
        assert "invoke('submit_session_input', command)" not in bridge
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")
        submit = rust.split("async fn submit_session_input(", maxsplit=1)[1].split(
            "#[tauri::command]", maxsplit=1
        )[0]
        assert "ooc_text: Option<String>" in submit
        assert '"ooc_text": ooc_text' in submit

    def test_output_limit_is_required_for_save_but_not_model_discovery(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        editor = (
            "function providerEditorCommand("
            + js.split("function providerEditorCommand(", maxsplit=1)[1].split(
                "function renderDiscoveredProviderModels(", maxsplit=1
            )[0]
        )
        integer = (
            "function positiveIntegerOrNull("
            + js.split("function positiveIntegerOrNull(", maxsplit=1)[1].split(
                "function formatTokenCapacity(", maxsplit=1
            )[0]
        )
        script = (
            "const fields = {}; const $ = id => fields[id] ||= {value: ''};"
            "const state = {providerCapacity: {}};"
            "const privateProviderHeaders = () => ({});"
            + integer
            + editor
            + "const discovery = providerEditorCommand({requireOutputLimit: false});"
            "let saveError = ''; try {providerEditorCommand();} catch(e) {saveError=e.message;}"
            "$('provider-output-limit').value = '131072';"
            "const save = providerEditorCommand();"
            "process.stdout.write(JSON.stringify({discovery:discovery.max_output_tokens,"
            "saveError, output:save.max_output_tokens}));"
        )
        result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
        assert json.loads(result.stdout) == {
            "discovery": None,
            "saveError": "Enter the output token limit supported by your provider",
            "output": 131_072,
        }

    def test_html_exposes_safe_configured_model_selector(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        assert 'id="model-choice"' in html
        assert 'id="chat-model-choice"' in html
        assert 'id="chat-model-picker-btn"' in html
        assert 'id="chat-model-search"' in html
        assert 'id="chat-model-results"' in html
        assert 'data-chat-model-mode="current"' in html
        assert 'data-chat-model-mode="once"' in html
        assert 'data-chat-model-mode="global"' in html
        assert 'aria-label="Model for this chat"' in html
        assert 'id="model-status"' in html
        assert "invoke('model_choices')" in js
        assert "invoke('provider_settings')" in js
        assert "invoke('discover_provider_models'" in js
        assert "invoke('save_provider_settings'" in js
        assert "'remove_provider_settings'" in js
        assert 'id="provider-preset"' in html
        assert 'id="provider-credential" type="password"' in html
        assert "LM Studio" not in html  # presets come from the owner, not renderer defaults
        assert "Choose a service" in html
        assert "Refresh models &amp; context" in html
        assert "Choose a model" in html
        assert "Advanced settings" in html
        assert "Server address" in html
        assert "API format" in html
        assert "Context capacity" in html
        assert "Model maximum" in html
        assert "Loaded runtime" in html
        assert "Optional Sylvia cap" in html
        assert "Effective capacity" in html
        assert "Output token limit" in html
        assert "Includes reasoning, tool arguments and narration" in html
        assert 'inputmode="numeric" value="4096"' not in html
        assert "max_output_tokens || 4096" not in js
        discovery = js.split("async function discoverSelectedProviderModels(", maxsplit=1)[1]
        discovery = discovery.split("function selectDiscoveredProviderModel(", maxsplit=1)[0]
        assert "providerEditorCommand({ requireOutputLimit: false })" in discovery
        assert "Save connection" in html
        assert "API keys are saved on this computer and sent to your" in html
        assert "selected service for authentication" in html
        assert (
            "Messages and relevant campaign content are sent to your selected model service" in html
        )
        assert "Request transport" not in html
        assert "owner-local" not in html
        assert "OAuth sign-in is post-Alpha" not in html
        assert 'id="detect-local-providers-btn"' in html
        assert 'id="provider-transport"' in html
        assert 'id="provider-headers"' in html
        assert 'id="provider-model-search"' in html
        assert 'id="provider-discovered-models"' in html
        assert 'id="validate-provider-btn"' in html
        assert 'id="switch-current-model-btn"' in html
        assert 'id="switch-once-model-btn"' in html
        assert 'id="switch-global-model-btn"' in html
        assert "invoke('detect_local_providers')" in js
        assert "invoke('validate_provider_model'" in js
        assert "Ready for Builder and Game Master" in js
        assert "This model can chat, but it could not use agent tools" in js
        assert "Server connected" in js
        assert "API key accepted" in js
        assert "Models found" in js
        assert "invoke('switch_session_model'" in js
        assert "$('chat-model-choice').addEventListener('change'" in js
        assert "renderChatModelPicker" in js
        assert "state.chatModelMode" in js
        assert "invoke('set_default_model'" in js
        model_switch = js.split("async function switchSelectedSessionModel(mode)", maxsplit=1)[1]
        model_switch = model_switch.split("function clearModelGuidanceWarning", maxsplit=1)[0]
        assert model_switch.index("await refreshBuilderChats()") < model_switch.index(
            "await renderBuilderSession(sessionId)"
        )
        assert "provider-credential').value = ''" in js
        assert "localStorage.setItem('provider" not in js
        assert "providerId: command.provider_id" in js
        assert "modelId: command.model_id" in js
        create_draft_bridge = js.split("create_guided_campaign_draft: (command)", maxsplit=1)[1]
        create_draft_bridge = create_draft_bridge.split(
            "advance_guided_campaign_draft: (command)", maxsplit=1
        )[0]
        assert "builderSessionId: command.builder_session_id" not in create_draft_bridge
        assert "provider_id: selected.provider_id" in js
        assert "model_id: selected.model_id" in js
        assert "builder_session_id: `session-builder-${nonce}`" not in js
        assert "'finalize_campaign_construction'" in js
        assert "complete_campaign_construction" not in js
        assert "execute_action" not in js
        assert "invoke('open_turn'" not in js
        assert "complete_turn" not in js
        assert "Campaign Plan ready" in js
        assert "Campaign Plan rejected" in js
        assert "preview_campaign_plan" in js
        assert "use_campaign_plan" in js
        discovery_presentation = js.split(
            "if (toolName === 'discover_campaign_content')", maxsplit=1
        )[1].split("if (toolName === 'prepare_campaign_setup')", maxsplit=1)[0]
        assert "plans.length === 1" in discovery_presentation
        assert "actions: directSetup ? ['configure_campaign'] : []" in discovery_presentation
        assert "campaignPlanId: directSetup ? String(directSetup.id || '') : ''" in (
            discovery_presentation
        )
        assert "setupId: directSetup ? String(directSetup.setup_id || '') : ''" in (
            discovery_presentation
        )
        assert 'data-campaign-plan-id="${escapeHtml(presentation.campaignPlanId || \'\')}"' in js
        assert "wireBuilderResultActions" in js
        assert "JSON.stringify(content.result || content" not in js
        assert "async function finalizeSelectedConstruction(modelGuidanceOverride = false)" in js
        assert "model_guidance: (campaignId, modelId, overrideAccepted)" in js
        assert "'model_guidance'" in js
        assert 'id="model-guidance-warning"' in html
        assert 'id="continue-model-guidance-btn"' in html
        assert "Continue anyway" in html

    def test_admin_full_audit_is_required_for_testing_and_memory_only(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        audit_html = (FRONTEND_DIR / "audit.html").read_text(encoding="utf-8")
        audit_js = _renderer_runtime_text(FRONTEND_DIR / "audit.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        assert 'id="settings-tab-admin"' in html
        assert 'id="toggle-admin-audit-btn"' in html
        assert 'id="admin-audit-traces"' in html
        assert 'id="admin-audit-search"' in html
        assert 'id="copy-admin-audit-btn"' in html
        assert 'id="open-admin-audit-btn"' in html
        assert "Open live inspector" in html
        assert "complete request sent to the model" in html
        assert "including private reasoning and tool calls" in html
        assert "Enable Full Audit before" in html
        assert "every test run" in html
        assert "disappear when Full Audit is disabled or Sylvia restarts" in html
        assert "invoke('admin_full_audit_status')" in js
        assert "invoke('admin_full_audit_record'" in js
        assert "invoke('open_admin_audit_window')" in js
        assert "state.bridge.admin_full_audit_record(index)" in js
        assert "invoke('set_admin_full_audit'" in js
        assert "trace && trace.input_stage" in js
        assert "wireRequest.model" in js
        assert "renderAdminAuditTraces" in js
        assert "copyVisibleAdminAudit" in js
        assert "navigator.clipboard.writeText" in js
        assert "fn admin_full_audit_status(" in rust
        assert "fn admin_full_audit_record(" in rust
        assert "async fn open_admin_audit_window(" in rust
        assert "fn set_admin_full_audit(" in rust
        assert '"admin-audit"' in rust
        assert 'WebviewUrl::App("audit.html".into())' in rust
        assert "trusted_audit_reader(&window)?" in rust
        assert 'id="auto-follow"' in audit_html
        assert 'id="pause-btn"' in audit_html
        assert "invoke('admin_full_audit_status')" in audit_js
        assert "invoke('admin_full_audit_record'" in audit_js
        assert "admin-full-audit-changed" in audit_js
        assert "JSON.stringify(record, null, 2)" in audit_js
        assert "private reasoning" in audit_js
        assert "set_admin_full_audit" not in audit_js
        assert "export_admin_audit" not in audit_js
        assert "replay" not in audit_js
        export = rust.split("fn export_admin_audit_file(", maxsplit=1)[1].split(
            "pub fn run()", maxsplit=1
        )[0]
        assert '"export_admin_full_audit"' in export
        assert 'state.request("admin_full_audit"' not in export
        assert "guidance.warning_required" in js
        assert "renderPersistedModelGuidance" in js
        assert "result.plan.plan_revision" in js
        assert "result.handoff.resulting_campaign_revision" in js
        assert "Admit Plan &amp; Start DM" in html
        assert "Builder remains available" in js
        campaign_start = js.split("async function finalizeSelectedConstruction", maxsplit=1)[
            1
        ].split("function selectedIndexEntry", maxsplit=1)[0]
        assert "const openingAttemptId = result.dm_attempt_id" in campaign_start
        assert "sessionRole: 'dm', campaignId: openingCampaignId" in campaign_start
        assert "startStreamHeartbeat(openingActivity, true)" in campaign_start
        assert "stopStreamHeartbeat(openingActivity)" in campaign_start
        assert "handoffCommitted ? 'Opening narration was interrupted'" in js
        assert ": 'Game Master could not start'" in js
        assert "content.kind === 'campaign_construction_brief'" in js
        assert "friendlyCommandError('Session request rejected', e)" in js
        retry = js.split("async function retry()", maxsplit=1)[1].split(
            "function signalActiveStreamDisconnect", maxsplit=1
        )[0]
        assert "if (activityIsWorking(activity))" in retry
        assert 'id="resume-session-btn"' in html
        assert "invoke('resume_agent_session'" in js
        assert "async function resumeSelectedSession()" in js

    def test_left_sidebar_starts_general_builder_chats_without_campaigns(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        left_sidebar = html[
            html.index('<div class="portrait-col">') : html.index("<!-- Central chronology -->")
        ]
        assert 'id="new-builder-chat-btn"' in left_sidebar
        assert 'id="builder-chat-rail"' in left_sidebar
        assert 'id="builder-skill"' not in left_sidebar
        assert "async function startBuilderChat(options = {})" in js
        builder_start = js[
            js.index("async function startBuilderChat(options = {})") : js.index(
                "async function beginConstruction()"
            )
        ]
        assert "create_builder_chat" in builder_start
        assert "confirm_new_campaign" not in builder_start
        assert "begin_campaign_construction" not in builder_start
        assert "builderSkill: command.builder_skill" not in js
        assert "fn builder_chat_index" in rust
        assert "fn create_builder_chat" in rust
        assert "builder_skill: String" not in rust

    def test_home_is_the_boot_destination_with_shelves_and_contextual_builder_launch(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")

        for control_id in (
            "home-btn",
            "home-view",
            "home-new-campaign-btn",
            "home-build-btn",
            "home-settings-btn",
            "home-recent",
            "campaign-shelves",
        ):
            assert f'id="{control_id}"' in html
        assert "await restoreDisconnectedStream();\n    showHome();" in js
        assert ".session-frame.home-active .right-sidebar" in css
        assert ".session-frame.home-active .portrait-frame" not in css
        assert ".session-frame.home-active .home-brand" not in css
        assert ".session-frame.home-active .session-context" not in css
        assert ".campaign-shelf-items" in css
        assert "grid-template-columns: repeat(auto-fit, minmax(220px, 240px));" in css
        assert "justify-content: start;" in css
        assert ".builder-stream-spinner" in css
        assert "function renderHome()" in js
        assert "campaign.ruleset_label" in js
        assert "campaign.campaign_setting_label" in js
        assert "async function startCampaignBuilder()" in js
        assert "I want to start a new campaign." not in js
        assert "create_guided_campaign_draft" in js
        assert "advance_guided_campaign_draft" in js
        assert "Campaign Draft · choose a Ruleset" in js
        assert "home-quick-start-btn" not in html
        assert "Quick Start is coming soon" not in js
        assert 'id="open-campaign-setup-btn"' in html
        assert "Configure Campaign" in html
        assert "updateCampaignSetupControl" in js
        assert "chat && chat.display_name === 'New Campaign'" in js
        assert "showConstructionPanel('the-rift-awakening')" in js
        assert "void startCampaignBuilder();" in js

    def test_first_home_tutorial_uses_replaceable_transparent_sprites(self) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")

        for control_id in (
            "home-tutorial",
            "tutorial-spotlight",
            "tutorial-sylvia",
            "tutorial-skip-btn",
            "tutorial-next-btn",
            "replay-home-tutorial-btn",
        ):
            assert f'id="{control_id}"' in html
        assert "const HOME_TUTORIAL_KEY = 'sylvia-gm-home-tutorial-v1';" in js
        assert "targetId: 'home-new-campaign-btn'" in js
        assert "targetId: 'home-build-btn'" in js
        assert "targetId: 'home-settings-btn'" in js
        assert "preferredPlacements: ['right', 'below', 'left', 'above']" in js
        assert "preferredPlacements: ['left', 'below', 'right', 'above']" in js
        assert "preferredPlacements: ['below', 'left', 'right', 'above']" in js
        assert "roomByPlacement[candidate] >= requiredRoom[candidate]" in js
        assert "const sideGuideWidth = Math.min(420" in js
        assert '.tutorial-guide[data-placement="right"]' in css
        assert '.tutorial-guide[data-placement="above"]' in css
        assert '.tutorial-guide[data-placement="below"]' in css
        assert "all the goodies you currently have installed" in js
        assert "maybeStartHomeTutorial();" in js
        assert "closeHomeTutorial('skipped')" in js
        assert "closeHomeTutorial('completed')" in js
        assert "box-shadow: 0 0 0 9999px" in css
        assert "@media (prefers-reduced-motion: reduce)" in css

        sprite_dir = FRONTEND_DIR / "assets" / "tutorial"
        for filename in (
            "sylvia-wave.png",
            "sylvia-point-left.png",
            "sylvia-point-right.png",
            "sylvia-point-up.png",
            "sylvia-point-down.png",
        ):
            image = (sprite_dir / filename).read_bytes()
            assert image[:8] == b"\x89PNG\r\n\x1a\n"
            assert image[25] == 6  # PNG RGBA colour type; sprites retain an alpha channel.

    def test_provider_settings_are_owner_controlled_and_never_persisted_in_browser_storage(
        self,
    ) -> None:
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        for control in (
            "provider-endpoint",
            "provider-credential",
            "provider-model-id",
            "discover-provider-models-btn",
            "save-provider-btn",
            "configured-provider-list",
        ):
            assert f'id="{control}"' in html
        for command in (
            "provider_settings",
            "discover_provider_models",
            "save_provider_settings",
            "remove_provider_settings",
        ):
            assert f"'{command}'" in js
            assert command in rust
        assert "credential_configured" in js
        assert "providerSettings" in js
        assert "Remove this connection and its saved API key?" in js
        assert "localStorage.setItem('sylvia-gm-theme'" in js
        assert "localStorage.setItem('provider" not in js
        assert "No configured models" in js
        create_section = js.split("async function createNoRulesCampaign()", maxsplit=1)[1]
        create_section = create_section.split(
            "async function exportSelectedCampaign()", maxsplit=1
        )[0]
        assert "preview.synthetic" not in create_section

    def test_new_campaign_remains_available_without_a_configured_model(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        refresh = js.split("async function refreshModelChoices()", maxsplit=1)[1]
        refresh = refresh.split("function chatModelModeLabel()", maxsplit=1)[0]

        assert "deterministicCampaignButtons" in refresh
        assert "$('new-campaign-btn'), $('home-new-campaign-btn')" in refresh
        enable = "deterministicCampaignButtons.forEach((button) => { button.disabled = false; });"
        assert refresh.index(enable) < refresh.index("if (!state.modelChoices.length)")
        assert "modelRequiredCreationButtons.forEach" in refresh

    def test_guided_draft_controls_and_warning_are_visible_only_when_relevant(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")

        assert "indexEntry.availability !== 'draft'" in js
        assert "Campaign Drafts" in js
        assert "Under construction" in js
        assert ".construction-actions .btn[hidden] { display: none; }" in css
        assert ".model-guidance-warning[hidden] { display: none; }" in css
        warning = css.split(".model-guidance-warning {", maxsplit=1)[1].split("}", maxsplit=1)[0]
        assert "position: fixed" in warning

    def test_session_chrome_preserves_errors_titles_warnings_and_per_chat_drafts(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")

        friendly_error = js.split("function friendlyCommandError", maxsplit=1)[1].split(
            "window.SylviaGMFrontend", maxsplit=1
        )[0]
        assert ".slice(0, 240)" not in friendly_error
        assert "sessionDrafts: new Map()" in js
        assert "saveSelectedSessionDraft()" in js
        assert "restoreSelectedSessionDraft()" in js
        assert js.count("restoreSelectedSessionDraft();") >= 2

        handoff = js.split("async function finalizeSelectedConstruction", maxsplit=1)[1].split(
            "function selectedIndexEntry", maxsplit=1
        )[0]
        override = handoff.split("if (modelGuidanceOverride)", maxsplit=1)[1].split(
            "} else {", maxsplit=1
        )[0]
        assert override.count("clearModelGuidanceWarning();") == 2
        assert override.rindex("clearModelGuidanceWarning();") < handoff.index(
            "state.bridge.finalize_campaign_construction("
        )

        title = css.split(".session-topbar .session-title {", maxsplit=1)[1].split("}", maxsplit=1)[
            0
        ]
        assert "min-width: 0" in title
        assert "text-overflow: ellipsis" in title
        assert "overflow: hidden" in title

    def test_committed_handoff_reports_an_opening_interruption_not_start_failure(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        campaign_start = js.split("async function finalizeSelectedConstruction", maxsplit=1)[
            1
        ].split("function selectedIndexEntry", maxsplit=1)[0]

        assert "let handoffCommitted = false;" in js
        assert "handoffCommitted = true;" in js
        assert (
            campaign_start.index("handoffCommitted = true;")
            < campaign_start.index("hideConstructionPanel(false);")
            < campaign_start.index("startProjectionPolling(")
        )
        assert (
            "handoffCommitted ? 'Opening narration was interrupted' : 'Game Master could not start'"
        ) in js

    def test_campaign_construction_owner_calls_do_not_block_the_renderer_thread(self) -> None:
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")

        assert "async fn owner_call_async(" in rust
        assert "tauri::async_runtime::spawn_blocking" in rust
        for command in (
            "start_rift_campaign",
            "start_guided_campaign",
            "begin_campaign_construction",
            "finalize_campaign_construction",
        ):
            body = rust.split(f"async fn {command}(", maxsplit=1)[1].split(
                "#[tauri::command]", maxsplit=1
            )[0]
            assert "owner_call_async(" in body

    def test_campaign_confirmation_and_handoff_are_single_flight(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        confirmation = js.split("async function beginConstruction()", maxsplit=1)[1].split(
            "async function finalizeSelectedConstruction", maxsplit=1
        )[0]
        handoff = js.split("async function finalizeSelectedConstruction", maxsplit=1)[1].split(
            "function selectedIndexEntry", maxsplit=1
        )[0]

        assert "campaignConfirmationPending: false" in js
        assert "if (state.campaignConfirmationPending) return;" in confirmation
        assert confirmation.index("state.campaignConfirmationPending = true;") < (
            confirmation.index("await state.bridge.start_rift_campaign")
        )
        assert "state.campaignConfirmationPending = false;" in confirmation
        assert "campaignHandoffPending: false" in js
        assert "if (state.campaignHandoffPending) return;" in handoff
        assert handoff.index("state.campaignHandoffPending = true;") < handoff.index(
            "await state.bridge.model_guidance"
        )
        assert "state.campaignHandoffPending = false;" in handoff
        assert "control.disabled = true" in confirmation
        assert "control.disabled = true" in handoff

    def test_blank_opening_and_opening_retry_use_specific_recoverable_states(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        dm_render = js.split("async function renderDMSession(campaignId,", maxsplit=1)[1].split(
            "async function renderPlayerProjection", maxsplit=1
        )[0]
        retry_card = js.split("if (kind === 'retry')", maxsplit=1)[1].split(
            "return '';", maxsplit=1
        )[0]

        assert "Game Master is opening the campaign" in dm_render
        assert "opening narration is still in progress" in dm_render
        assert "Campaign opening narration was interrupted" in retry_card
        assert "The campaign is saved" in retry_card
        assert 'data-action="manual-retry"' in retry_card

    def test_dm_tool_loop_is_grouped_into_one_spoiler_safe_activity_card(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        dm_render = js.split("async function renderDMSession(campaignId,", maxsplit=1)[1].split(
            "async function renderPlayerProjection", maxsplit=1
        )[0]

        assert "content.kind === 'gm_turn_activity'" in js
        assert "Starting scene prepared" in js
        assert "Turn resolved" in js
        assert "Private GM notes remain hidden" in js
        assert "Ruleset outcomes appear separately" in js
        assert "content.kind === 'gm_system_contract'" in dm_render
        assert "content.kind === 'ready_to_begin_reference'" in dm_render
        assert "startsWith('provider_')" in dm_render

    def test_ruleset_result_cards_use_the_safe_projected_operation_details(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        dm_render = js.split("async function renderDMSession(campaignId,", maxsplit=1)[1].split(
            "async function renderPlayerProjection", maxsplit=1
        )[0]
        presentation = js.split("function toolPresentation(content)", maxsplit=1)[1].split(
            "function renderProjection", maxsplit=1
        )[0]

        assert "...projectedPayload" in dm_render
        assert "recordedCheckPresentation" in presentation
        assert "skillCheckCardMarkup" not in presentation
        assert "Completed and saved" not in presentation
        assert "Operation completed" in presentation

    def test_skill_check_cards_render_player_safe_results_and_calculations(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        payloads = [
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "perception", "target_number": 12},
                "result": {
                    "context": {
                        "_adjusted_total": 19,
                        "_outcome": "success",
                        "_modifier": 2,
                        "_guidance_bonus": 0,
                        "_lasting_d20_modifier": 0,
                        "_exhaustion_penalty": 0,
                        "_effective_advantage": False,
                        "_effective_disadvantage": False,
                        "_d20_test": {"results": [17], "total": 17, "modifier": 2},
                        "actor": {"data": {"name": "Mira Sol"}},
                    }
                },
            },
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "survival", "target_number": 14},
                "result": {
                    "context": {
                        "_adjusted_total": 3,
                        "_outcome": "failure",
                        "_modifier": 2,
                        "_d20_test": {"results": [1], "total": 1, "modifier": 2},
                        "actor": {"data": {"name": "Mira Sol"}},
                    }
                },
            },
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "insight", "target_number": 15},
                "result": {
                    "context": {
                        "_adjusted_total": 22,
                        "_outcome": "success",
                        "_modifier": 2,
                        "_guidance_bonus": 2,
                        "_lasting_d20_modifier": 1,
                        "_exhaustion_penalty": 0,
                        "_effective_advantage": True,
                        "_effective_disadvantage": False,
                        "_d20_test": {"results": [4, 17], "total": 17, "modifier": 2},
                        "actor": {"data": {"name": "Mira Sol"}},
                    }
                },
            },
        ]
        script = (
            "global.window={};global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            f"const payloads={json.dumps(payloads)};"
            "process.stdout.write(JSON.stringify(payloads.map((payload,index)=>"
            "window.SylviaGMFrontend.toolResultMarkup(payload,"
            "` data-chronology-key=\"check-${index}\"`))));"
        )
        completed = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        success, failure, advantage = json.loads(completed.stdout)

        assert "result-card-compact" in success
        assert "Mira Sol" in success
        assert "Perception check" in success
        assert "Success" in success
        assert 'Total</span><span class="result-card-value">19</span>' in success
        assert 'DC</span><span class="result-card-value">12</span>' in success
        assert 'Roll</span><span class="result-card-value">17</span>' in success
        assert "result-card-compact" in failure
        assert "Failure" in failure
        assert 'Total</span><span class="result-card-value">3</span>' in failure
        assert 'D20 results</span><span class="result-card-value">4, 17</span>' in advantage
        assert 'Roll mode</span><span class="result-card-value">Advantage</span>' in advantage
        assert 'Guidance</span><span class="result-card-value">2</span>' in advantage
        assert 'Ongoing effects</span><span class="result-card-value">1</span>' in advantage
        assert 'Total</span><span class="result-card-value">22</span>' in advantage
        assert 'result-equation' not in advantage

    def test_skill_check_cards_handle_mismatch_missing_rolls_and_untrusted_text(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        payloads = [
            {
                "tool_name": "d20_test",
                "parameters": {
                    "actor": "rift-private-identifier",
                    "skill": "stealth",
                    "target_number": None,
                },
                "result": {
                    "context": {
                        "_adjusted_total": 25,
                        "_outcome": "success",
                        "_modifier": 99,
                        "_guidance_bonus": 3,
                        "_lasting_d20_modifier": 0,
                        "_exhaustion_penalty": 1,
                        "_effective_disadvantage": True,
                        "_d20_test": {"results": [18, 7], "total": 7, "modifier": 2},
                    }
                },
            },
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "perception", "target_number": None},
                "result": {
                    "context": {
                        "_outcome": "failure",
                        "_automatic_failure_reason": "cannot_see",
                        "_d20_test": {"results": [], "total": None},
                    }
                },
            },
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "<img src=x onerror=alert(1)>", "target_number": 10},
                "result": {
                    "context": {
                        "_adjusted_total": 10,
                        "_outcome": "success",
                        "_modifier": 0,
                        "_d20_test": {"results": [10], "total": 10, "modifier": 0},
                        "actor": {"data": {"name": "<script>bad()</script>"}},
                    }
                },
            },
        ]
        script = (
            "global.window={};global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            f"const payloads={json.dumps(payloads)};"
            "process.stdout.write(JSON.stringify(payloads.map((payload)=>"
            "window.SylviaGMFrontend.toolResultMarkup(payload,''))));"
        )
        completed = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        mismatch, automatic, escaped = json.loads(completed.stdout)

        assert 'D20 results</span><span class="result-card-value">18, 7</span>' in mismatch
        assert 'Roll mode</span><span class="result-card-value">Disadvantage</span>' in mismatch
        assert 'Total</span><span class="result-card-value">25</span>' in mismatch
        assert "= 25" not in mismatch
        assert 'Modifier</span><span class="result-card-value">2</span>' in mismatch
        assert "99" not in mismatch
        assert "DC 0" not in mismatch
        assert "rift-private-identifier" not in mismatch
        assert "Automatic failure" in automatic
        assert "Roll 0" not in automatic
        assert "Total</span><strong>0</strong>" not in automatic
        assert "&lt;script&gt;bad()&lt;/script&gt;" in escaped
        assert "&lt;Img Src=X Onerror=Alert(1)&gt; check" in escaped
        assert "<script>" not in escaped

    def test_removed_widgets_have_no_remaining_styles(self) -> None:
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")

        assert "skill-check-" not in css
        assert "tool-card" not in css
        assert ".result-card-compact" in css
        assert ".result-card-inline" in css

    def test_skill_check_cards_do_not_infer_rejected_or_missing_results(self) -> None:
        frontend = FRONTEND_DIR / "app.js"
        payloads = [
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "perception", "target_number": 12},
                "result": {
                    "status": "rejected",
                    "context": {
                        "_adjusted_total": 21,
                        "_d20_test": {"results": [19], "total": 19, "modifier": 2},
                    },
                },
            },
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "insight", "target_number": 13},
                "result": {
                    "context": {
                        "_outcome": "success",
                        "_adjusted_total": 15,
                        "_effective_advantage": True,
                        "_d20_test": {"results": [3, 14], "total": None, "modifier": 1},
                    }
                },
            },
            {
                "tool_name": "d20_test",
                "parameters": {"skill": "survival", "target_number": 11},
                "result": {
                    "context": {
                        "_outcome": "failure",
                        "_adjusted_total": 9,
                        "_effective_advantage": True,
                        "_effective_disadvantage": True,
                        "_d20_test": {"results": [5, 8], "total": 8, "modifier": 1},
                    }
                },
            },
        ]
        script = (
            "global.window={};global.document={addEventListener(){}};"
            f"require({json.dumps(str(frontend))});"
            f"const payloads={json.dumps(payloads)};"
            "process.stdout.write(JSON.stringify(payloads.map((payload)=>"
            "window.SylviaGMFrontend.toolResultMarkup(payload,''))));"
        )
        completed = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        rejected, missing_selected, cancelled = json.loads(completed.stdout)

        assert "Check not resolved" in rejected
        assert "Resolved" not in rejected
        assert "Roll 19" not in rejected
        assert "Total</span><strong>21</strong>" not in rejected
        assert 'D20 results</span><span class="result-card-value">3, 14</span>' in missing_selected
        assert 'Roll mode</span><span class="result-card-value">Advantage</span>' in missing_selected
        assert "selected null" not in missing_selected
        assert 'Total</span><span class="result-card-value">15</span>' in missing_selected
        assert 'D20 results</span><span class="result-card-value">5, 8</span>' in cancelled
        assert 'Advantage and disadvantage cancel' in cancelled
    def test_turn_lifecycle_keeps_send_locked_until_terminal_or_recovery(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        submit = js.split("async function submitInput(text,", maxsplit=1)[1].split(
            "function startProjectionPolling", maxsplit=1
        )[0]
        dm_render = js.split("async function renderDMSession(campaignId,", maxsplit=1)[1].split(
            "async function renderPlayerProjection", maxsplit=1
        )[0]
        retry_card = js.split("if (kind === 'retry')", maxsplit=1)[1].split(
            "return '';", maxsplit=1
        )[0]

        assert "refreshSessionInputAdmission" in js
        assert (
            submit.index("activity = beginSessionActivity(activity, command.attempt_id);")
            < submit.index("refreshActivityControls(activity);")
            < submit.index("state.bridge.submit_session_input(command)")
        )
        assert "session_attempt_terminal" in dm_render
        assert "safe_payload: { ...terminal, ...(projection.safe_payload || {}) }" in dm_render
        assert "payload.saved_changes === true" in retry_card
        assert "recovery_kind" in retry_card
        assert "provider_empty_completion" in js
        assert "Boolean(activity && activity.retryBasis)" in js

    def test_preflight_rejection_keeps_the_ready_interview_visible(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        campaign_start = js.split("async function finalizeSelectedConstruction", maxsplit=1)[
            1
        ].split("function selectedIndexEntry", maxsplit=1)[0]
        failure = campaign_start.rsplit("} catch (e) {", maxsplit=1)[1]

        assert "if (handoffCommitted && selectedActivityMatches(openingActivity)) {" in failure
        assert failure.index(
            "if (handoffCommitted && selectedActivityMatches(openingActivity)) {"
        ) < failure.index("await refreshSelectedCampaign()")

    def test_streaming_polls_authoritative_projection_only_while_pending(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")
        assert "startProjectionPolling" in js
        assert "stopProjectionPolling" in js
        assert "player_projection" in js
        assert "context.provisional" in js
        stop = js.split("async function stop()", maxsplit=1)[1]
        stop = stop.split("async function retry()", maxsplit=1)[0]
        assert "interrupt_provider_stream" in stop
        assert "stop_agent_session" not in stop
        assert "async fn submit_session_input(" in rust
        assert "async fn manual_retry_session(" in rust
        assert "async fn start_dm_session(" in rust

    def test_periodic_index_refresh_does_not_starve_stream_heartbeats(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        periodic = js.split("async function init()", maxsplit=1)[1].split(
            "function wireControls()", maxsplit=1
        )[0]

        assert periodic.index("if (anySessionIsWorking()) return;") < periodic.index(
            "void refreshNavigationIndexes();"
        )

    def test_periodic_navigation_refreshes_never_overlap(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        periodic = js.split("async function init()", maxsplit=1)[1].split(
            "function wireControls()", maxsplit=1
        )[0]
        refresh = js.split("async function refreshNavigationIndexes()", maxsplit=1)[1]
        refresh = refresh.split("function renderBuilderChatRail()", maxsplit=1)[0]

        assert "}, 15000);" in periodic
        assert "if (state.navigationRefreshBusy) return;" in refresh
        assert "state.navigationRefreshBusy = true;" in refresh
        assert "await refreshCampaignIndex();" in refresh
        assert "await refreshBuilderChats();" in refresh
        assert "finally" in refresh
        assert "state.navigationRefreshBusy = false;" in refresh

    def test_panel_actions_use_a_fresh_authoritative_revision(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        handler = js.split("container.querySelectorAll('[data-panel-action-id]')", maxsplit=1)[1]
        handler = handler.split("container.querySelectorAll('[data-panel-size-id]')", maxsplit=1)[0]

        assert "await state.bridge.snapshot(campaignId)" in handler
        assert "expected_revision: actionSnapshot.revision" in handler
        assert handler.index("await state.bridge.snapshot(campaignId)") < handler.index(
            "await state.bridge.execute_panel_action("
        )

    def test_projection_polling_preserves_stable_chronology_dom_and_scroll(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        helper = js.split("function updateChronology(", maxsplit=1)[1]
        helper = helper.split("async function renderBuilderSession(", maxsplit=1)[0]

        assert "container.innerHTML === html" in helper
        assert "container.scrollHeight - container.scrollTop - container.clientHeight" in helper
        assert "container.scrollTop = container.scrollHeight" in helper
        assert "story-card.expanded" in helper
        assert "data-chronology-key" in helper

        for start, end in (
            ("async function renderBuilderSession(", "async function renderDMSession("),
            ("async function renderDMSession(", "async function renderPlayerProjection("),
            ("async function renderPlayerProjection(", "function wireMechanicsChoiceControls("),
        ):
            renderer = js.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]
            assert "updateChronology(container," in renderer
            assert "container.innerHTML =" not in renderer

    def test_durable_mechanics_choice_is_owner_projected_and_explicitly_resolved(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        rust = (FRONTEND_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")
        css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")

        assert "invoke('resolve_mechanics_choice'" in js
        assert "kind === 'mechanics_choice'" in js
        assert 'data-action="resolve-mechanics-choice"' in js
        assert "wireMechanicsChoiceControls(container)" in js
        assert "fn resolve_mechanics_choice(" in rust
        assert "resolve_mechanics_choice," in rust
        assert ".mechanics-choice-card" in css
        assert "Math.random" not in js

    def test_js_no_client_side_truth_generation(self) -> None:
        """The renderer never invents state; it only renders projections."""

        js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
        # No Math.random dice rolling in the canonical frontend (the old
        # product-ui-v5 mockup had a dice roller; the canonical frontend
        # delegates all mechanics to the owner).
        assert "Math.random" not in js
        # Player text must not be disguised as a fabricated package action.
        assert "expected_revision: 0" not in js
        assert "package_id: ''" not in js
        assert "tool_name: ''" not in js
        assert "owner-local" not in js

    def test_js_no_automatic_retry(self) -> None:
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        assert "retry" in js
        # The renderer may poll for bridge readiness, but must never schedule
        # retry itself. Retry is reached only through the explicit UI action.
        assert (
            re.search(
                r"set(?:timeout|interval)\s*\(\s*(?:\(\)\s*=>\s*)?retry\b",
                js,
                flags=re.IGNORECASE,
            )
            is None
        )

    def test_controls_do_not_depend_on_inline_handlers(self) -> None:
        """CSP-safe controls use listeners, including generated rail rows."""
        html = (FRONTEND_DIR / "app.html").read_text(encoding="utf-8")
        js = _renderer_runtime_text(FRONTEND_DIR / "app.js")
        assert re.search(r"\bon[a-z]+\s*=", html, flags=re.IGNORECASE) is None
        assert re.search(r"\bon[a-z]+\s*=", js, flags=re.IGNORECASE) is None


# --------------------------------------------------------------------------
# G-NEUTRAL-SURF — no D&D/TTRPG assumptions in projections or surfaces
# --------------------------------------------------------------------------


class TestNeutralSurface:
    def test_campaign_index_has_no_dnd_terminology(self, tmp_path: Path) -> None:
        db = tmp_path / "neutral.db"
        supervisor, bus, _ = _stand_up(db)
        try:
            index = bus.campaign_index()
            for entry in index:
                assert "d&d" not in entry.display_name.lower()
                assert "cairn" not in entry.display_name.lower()
                assert "initiative" not in entry.display_name.lower()
        finally:
            supervisor.shutdown()

    def test_player_projection_text_has_no_dnd_terminology(self, tmp_path: Path) -> None:
        db = tmp_path / "neutral-proj.db"
        supervisor, bus, campaign_id = _stand_up(db)
        try:
            projections = bus.player_projection(campaign_id)
            dnd_terms = ["d&d", "dnd", "cairn", "initiative", "hit points", "armor class"]
            for p in projections:
                text = str(p.text or "").lower()
                payload = str(p.safe_payload).lower()
                for term in dnd_terms:
                    assert term not in text, f"D&D term '{term}' in projection text"
                    assert term not in payload, f"D&D term '{term}' in projection payload"
        finally:
            supervisor.shutdown()
