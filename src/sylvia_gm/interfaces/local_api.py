"""F6 local API + stream surfaces — the client-facing command and stream bus.

The Tauri desktop host and any attached second
instance route every command through this surface.  It holds **no** database
handle — it is a thin serialiser over :class:`LocalOwnerSupervisor`, enforcing
the "no client database access" boundary: clients send commands and render
projections; the owner is the sole writer.

Stream tokens
-------------
Narration prose is delivered to clients via a *listener* registry rather than
a long-lived socket held by the owner.  Each attached narration attempt has
zero or more :class:`StreamListener` callbacks; the owner's emit path pushes
tokens through the supervisor, which fans them out to listeners.  When a
listener disconnects (its callback raises ``ListenerGone``) the supervisor
starts the disconnect-grace window; reconnect within the grace reattaches the
listener and replays the provisional stream.

This module is transport-agnostic by design so the Tauri shell can expose it
over its own IPC while the in-process tests exercise it directly.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sylvia_gm.domain.authority import (
    CampaignConfirmation,
    CampaignConstructionBegun,
    CampaignConstructionCompleted,
    CanonicalEvent,
    GuidedCampaignDraft,
    GuidedCreationSubflow,
    NarrationInterruption,
    NarrationStream,
    Participant,
    PlayerProjection,
    ProseChannel,
    RetryNarrationInput,
    StateValue,
)
from sylvia_gm.domain.session import BuilderDMHandoff, CampaignPlan
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor

logger = logging.getLogger(__name__)


class ListenerGone(Exception):  # noqa: N818 - signal, not an error condition
    """Raised by a ``StreamListener`` to signal its client stream dropped."""


StreamListener = Callable[[ProseChannel, str], None]
StreamTokenEmit = Callable[["LocalCommandBus", str, str, ProseChannel, str], None]


@dataclass
class _StreamSink:
    """One registered client listener for a narration attempt."""

    listener: StreamListener
    alive: bool = True


@dataclass(frozen=True)
class HandshakeStatus:
    """Projection of the supervisor handshake state for the UI."""

    verified: bool
    role: str
    reason: str


@dataclass(frozen=True)
class ReconnectedStream:
    """Canonical reconnect projection plus the owner attachment generation."""

    canonical: tuple[CanonicalEvent, ...]
    provisional: tuple[PlayerProjection, ...]
    high_water: int
    attachment_generation: int


class LocalCommandBus:
    """The client-facing command + stream surface backed by the supervisor.

    Construction binds the bus to one supervisor that has already acquired
    the owner.  The bus is thread-safe; the supervisor serialises command
    admission on per-campaign lanes.
    """

    def __init__(self, supervisor: LocalOwnerSupervisor) -> None:
        self._supervisor = supervisor
        self._sinks_lock = threading.Lock()
        self._sinks: dict[tuple[str, str], list[_StreamSink]] = {}

    # ------------------------------------------------------------------
    # Handshake surface
    # ------------------------------------------------------------------

    def handshake_status(self) -> HandshakeStatus:
        verified = self._supervisor.handshake_verified
        # Role/reason are derived from the verified flag plus supervisor state.
        role = "owner" if verified else "pending"
        reason = "launch handshake verified" if verified else "handshake pending"
        return HandshakeStatus(verified=verified, role=role, reason=reason)

    def present_handshake_response(self, response_hex: str) -> None:
        """Called by the owner process to present its ``/handshake`` token."""

        self._supervisor.complete_handshake(response_hex)

    # ------------------------------------------------------------------
    # Install Package + lifecycle (application commands only)
    # ------------------------------------------------------------------

    def install_package(self, source: BinaryIO, *, install_id: str) -> object:
        """Route Install-Package through the application owner — no DB handle."""

        return self._supervisor.install_package_archive(source, install_id=install_id)

    def package_candidate(self, candidate_id: str) -> object:
        """Resolve an opaque durable package candidate inside the owner."""

        return self._supervisor.package_candidate(candidate_id)

    def package_candidates(self) -> object:
        """Project admitted package candidates through the verified owner."""

        return self._supervisor.package_candidates()

    def inspect_package_candidate(self, candidate_id: str) -> object:
        """Project one candidate's static identity, support, tools, and file tree."""

        return self._supervisor.inspect_package_candidate(candidate_id)

    def package_candidate_text(
        self,
        candidate_id: str,
        package_id: str,
        portable_path: str,
    ) -> object:
        """Read a selected admitted text file through the verified owner."""

        return self._supervisor.package_candidate_text(
            candidate_id,
            package_id,
            portable_path,
        )

    def compare_package_candidates(self, left_id: str, right_id: str) -> object:
        """Compare two installed package candidates without campaign access."""

        return self._supervisor.compare_package_candidates(left_id, right_id)

    def export_package_candidate(self, candidate_id: str, target: BinaryIO) -> object:
        """Export one exact installed package closure."""

        return self._supervisor.export_package_candidate(candidate_id, target)

    def confirm_new_campaign(
        self,
        *,
        operation_id: str,
        display_name: str,
        package_lock: object,
        turn_id: str,
        package_candidate_id: str | None = None,
        builder_session_id: str | None = None,
        builder_provider_id: str | None = None,
        builder_model_id: str | None = None,
        builder_selected_skill: str = "BUILD CAMPAIGN PLAN",
        participants: tuple[Participant, ...] = (),
        initial_state: tuple[StateValue, ...] = (),
    ) -> CampaignConfirmation:
        return self._supervisor.confirm_new_campaign(
            operation_id=operation_id,
            display_name=display_name,
            package_lock=package_lock,
            turn_id=turn_id,
            package_candidate_id=package_candidate_id,
            builder_session_id=builder_session_id,
            builder_provider_id=builder_provider_id,
            builder_model_id=builder_model_id,
            builder_selected_skill=builder_selected_skill,
            participants=participants,
            initial_state=initial_state,
        )

    def create_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        display_name: str,
        builder_session_id: str,
        builder_provider_id: str,
        builder_model_id: str,
    ) -> GuidedCampaignDraft:
        return self._supervisor.create_guided_campaign_draft(
            operation_id=operation_id,
            display_name=display_name,
            builder_session_id=builder_session_id,
            builder_provider_id=builder_provider_id,
            builder_model_id=builder_model_id,
        )

    def navigate_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        builder_session_id: str,
        expected_construction_revision: int,
        target_step: str,
    ) -> GuidedCampaignDraft:
        """Route saved Interview navigation through the verified owner."""
        return self._supervisor.navigate_guided_campaign_draft(
            operation_id=operation_id, campaign_id=campaign_id,
            builder_session_id=builder_session_id,
            expected_construction_revision=expected_construction_revision,
            target_step=target_step,
        )

    def advance_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        builder_session_id: str,
        expected_construction_revision: int,
        current_step: str,
        visible_answer: str,
        answer: object,
    ) -> GuidedCampaignDraft:
        return self._supervisor.advance_guided_campaign_draft(
            operation_id=operation_id,
            campaign_id=campaign_id,
            builder_session_id=builder_session_id,
            expected_construction_revision=expected_construction_revision,
            current_step=current_step,
            visible_answer=visible_answer,
            answer=answer,
        )

    def bind_guided_campaign_basis(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        display_name: str,
        package_lock: object,
        turn_id: str,
        builder_session_id: str,
        package_candidate_id: str | None = None,
        participants: tuple[Participant, ...] = (),
        initial_state: tuple[StateValue, ...] = (),
    ) -> CampaignConfirmation:
        return self._supervisor.bind_guided_campaign_basis(
            operation_id=operation_id,
            campaign_id=campaign_id,
            display_name=display_name,
            package_lock=package_lock,
            turn_id=turn_id,
            builder_session_id=builder_session_id,
            package_candidate_id=package_candidate_id,
            participants=participants,
            initial_state=initial_state,
        )

    def start_guided_creation_subflow(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        expected_construction_revision: int,
        purpose: str,
        visible_answer: str,
        child_session_id: str,
        trigger_message_id: str | None = None,
    ) -> GuidedCreationSubflow:
        return self._supervisor.start_guided_creation_subflow(
            operation_id=operation_id,
            campaign_id=campaign_id,
            parent_session_id=parent_session_id,
            expected_construction_revision=expected_construction_revision,
            purpose=purpose,
            visible_answer=visible_answer,
            child_session_id=child_session_id,
            trigger_message_id=trigger_message_id,
        )

    def return_guided_creation_subflow(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        child_session_id: str,
        expected_construction_revision: int,
        use_result: bool,
    ) -> GuidedCreationSubflow:
        return self._supervisor.return_guided_creation_subflow(
            operation_id=operation_id,
            campaign_id=campaign_id,
            parent_session_id=parent_session_id,
            child_session_id=child_session_id,
            expected_construction_revision=expected_construction_revision,
            use_result=use_result,
        )

    def begin_campaign_construction(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        lane: str,
        expected_revision: int,
        canonical_input: object,
    ) -> CampaignConstructionBegun:
        return self._supervisor.begin_campaign_construction(
            operation_id=operation_id,
            campaign_id=campaign_id,
            lane=lane,
            expected_revision=expected_revision,
            canonical_input=canonical_input,
        )

    def complete_campaign_construction(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        canonical_plan: object,
    ) -> object:
        return self._supervisor.complete_campaign_construction(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            canonical_plan=canonical_plan,
        )

    def finalize_campaign_construction(
        self,
        *,
        campaign_id: str,
        dm_provider_id: str,
        dm_model_id: str,
        plan_artifact_text: str,
        structured_plan: object | None = None,
        model_guidance_override: bool = False,
    ) -> tuple[CampaignConstructionCompleted, CampaignPlan, BuilderDMHandoff]:
        return self._supervisor.finalize_campaign_construction(
            campaign_id=campaign_id,
            dm_provider_id=dm_provider_id,
            dm_model_id=dm_model_id,
            plan_artifact_text=plan_artifact_text,
            structured_plan=structured_plan,
            model_guidance_override=model_guidance_override,
        )

    def model_guidance(
        self,
        *,
        campaign_id: str,
        model_id: str,
        override_accepted: bool = False,
    ) -> object:
        return self._supervisor.model_guidance(
            campaign_id=campaign_id,
            model_id=model_id,
            override_accepted=override_accepted,
        )

    def catalog(self, campaign_id: str) -> object:
        """Project the campaign's exact admitted executable tool catalog."""

        return self._supervisor.catalog(campaign_id)

    def snapshot(self, campaign_id: str) -> object:
        """Render one campaign projection for the client (read-only view)."""

        return self._supervisor.snapshot(campaign_id)

    # ------------------------------------------------------------------
    # F6C — player-facing query + campaign command surfaces
    # The renderer calls these; the bus holds no database handle and no
    # secrets.  Projections are authoritative — the frontend renders them
    # verbatim, never inventing client-side truth.
    # ------------------------------------------------------------------

    def campaign_index(self) -> object:
        """Project the coherent campaign index (the no-rules campaign rail)."""

        return self._supervisor.campaign_index()

    def player_projection(self, campaign_id: str) -> object:
        """Render the authoritative player-safe projection.

        Returns the chronological TALK, player-safe collapsed THINK, and
        collapsed TOOL outcome cards, plus any active RETRY projection.
        Private diagnostics are Inspector-only and never appear here.
        """

        return self._supervisor.player_projection(campaign_id)

    def create_campaign(self, command: object) -> object:
        """Route one Campaign-Create command through the application owner.

        A no-rules campaign is a campaign whose package lock carries no
        entries; the owner is the sole writer and no ruleset is assumed.
        """

        return self._supervisor.create_campaign(command)

    def create_agent_session(
        self,
        *,
        campaign_id: str,
        role: object,
        provider_id: str,
        model_id: str,
        session_id: str,
    ) -> object:
        """Route top-level session creation without exposing authority state."""

        return self._supervisor.create_agent_session(
            campaign_id=campaign_id,
            role=role,
            provider_id=provider_id,
            model_id=model_id,
            session_id=session_id,
        )

    def stop_agent_session(self, session_id: str) -> object:
        """Stop one top-level session through the verified owner."""

        return self._supervisor.stop_agent_session(session_id)

    def resume_agent_session(self, session_id: str) -> object:
        """Explicitly resume one stopped top-level writer session."""

        return self._supervisor.resume_agent_session(session_id)

    def execute_action(self, command: object) -> object:
        """Route one Action-Execute command through the application owner."""

        return self._supervisor.execute_action(command)

    def resolve_mechanics_choice(self, command: object) -> object:
        """Resolve one durable package-authored choice through the owner."""

        return self._supervisor.resolve_mechanics_choice(command)

    def open_turn(self, campaign_id: str, turn_id: str) -> None:
        self._supervisor.open_turn(campaign_id, turn_id)

    def complete_turn(self, campaign_id: str, turn_id: str) -> None:
        self._supervisor.complete_turn(campaign_id, turn_id)

    def rename_campaign(
        self, *, operation_id: str, campaign_id: str, display_name: str, expected_revision: int
    ) -> object:
        return self._supervisor.rename_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            display_name=display_name,
            expected_revision=expected_revision,
        )

    def archive_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        designated_session_id: str | None = None,
    ) -> object:
        return self._supervisor.archive_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            designated_session_id=designated_session_id,
        )

    def restore_archived_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        session_id: str | None = None,
    ) -> object:
        return self._supervisor.restore_archived_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            session_id=session_id,
        )

    def trash_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        designated_session_id: str | None = None,
    ) -> object:
        return self._supervisor.trash_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            designated_session_id=designated_session_id,
        )

    def restore_trashed_campaign(
        self, *, operation_id: str, campaign_id: str, expected_revision: int
    ) -> object:
        return self._supervisor.restore_trashed_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
        )

    def purge_campaign(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        confirmation: str,
    ) -> object:
        return self._supervisor.purge_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            confirmation=confirmation,
        )

    def backup_database(self, target: Path) -> None:
        self._supervisor.backup_database(target)

    def export_campaign(
        self, operation_id: str, campaign_id: str, target: Path, *, include_thinking: bool = False,
    ) -> object:
        return self._supervisor.export_campaign(
            operation_id, campaign_id, target, include_thinking=include_thinking,
        )

    def recover_campaign(self, *, recovery_id: str, source: Path) -> object:
        return self._supervisor.recover_campaign(recovery_id=recovery_id, source=source)

    def complete_narration_attempt(self, campaign_id: str, attempt_id: str) -> object:
        """Commit the current safe prose buffer and return the canonical events."""

        return self._supervisor.complete_narration_attempt(campaign_id, attempt_id)

    def retryable_interruption(self, campaign_id: str, *, failed_attempt_id: str) -> object:
        """Project one durable retry basis (read-only)."""

        return self._supervisor.retryable_interruption(
            campaign_id, failed_attempt_id=failed_attempt_id
        )

    def manual_retry(
        self,
        *,
        campaign_id: str,
        interruption_id: str,
        retry_id: str,
        narrator: Callable[[RetryNarrationInput, NarrationStream], None],
        cancel: Callable[[], None] = lambda: None,
    ) -> object:
        """Route one manual narration retry through the application owner.

        No automatic retry/fallback: the renderer calls this only when the
        player explicitly asks for a retry.
        """

        return self._supervisor.manual_retry(
            campaign_id=campaign_id,
            interruption_id=interruption_id,
            retry_id=retry_id,
            narrator=narrator,
            cancel=cancel,
        )

    # ------------------------------------------------------------------
    # Narration stream attach / reconnect / interrupt
    # ------------------------------------------------------------------

    def attach_stream(
        self,
        *,
        campaign_id: str,
        attempt_id: str,
        turn_id: str,
        cancel: Callable[[], None],
        listener: StreamListener,
        last_action_id: str | None = None,
    ) -> None:
        """Begin a narration attempt and register one client stream listener.

        The supervisor arms the disconnect-grace contract; tokens emitted by
        the owner are fanned out to ``listener`` via :meth:`emit_token`.
        """

        self._supervisor.attach_narration_attempt(
            campaign_id=campaign_id,
            attempt_id=attempt_id,
            turn_id=turn_id,
            cancel=cancel,
            last_action_id=last_action_id,
        )
        with self._sinks_lock:
            sinks = self._sinks.setdefault((campaign_id, attempt_id), [])
            sinks.append(_StreamSink(listener=listener, alive=True))

    def emit_token(
        self, campaign_id: str, attempt_id: str, channel: ProseChannel, token: str
    ) -> None:
        """Fan one narration token out to every live listener for the attempt.

        When a listener raises :class:`ListenerGone` (its client stream
        dropped) it is marked dead and the disconnect-grace window is
        started.  When all listeners for an attempt are dead the supervisor
        is told to start the grace window too.
        """

        with self._sinks_lock:
            sinks = self._sinks.get((campaign_id, attempt_id))
            if not sinks:
                return
            any_alive = False
            for sink in sinks:
                if not sink.alive:
                    continue
                try:
                    sink.listener(channel, token)
                    any_alive = True
                except ListenerGone:
                    sink.alive = False
                    logger.debug("Stream listener gone for %s/%s", campaign_id, attempt_id)
                except Exception:  # pragma: no cover - listener bugs must not kill narration
                    logger.warning("Stream listener raised", exc_info=True)
                    sink.alive = False
            if not any_alive:
                # All listeners gone — begin the disconnect grace window.
                self._supervisor.signal_disconnect(campaign_id, attempt_id)

    def reconnect_stream(
        self,
        *,
        campaign_id: str,
        attempt_id: str,
        acknowledged_sequence: int,
        listener: StreamListener,
        expected_generation: int | None = None,
    ) -> ReconnectedStream:
        """Reattach within the grace window and replay the canonical sequence.

        On success the provisional (incomplete) stream tokens are replayed to
        ``listener``; the committed canonical events are returned for the
        client to re-render.  If the grace elapsed the supervisor raises
        ``CommandRejected`` and the stream is torn down.
        """

        replay, attachment_generation = self._supervisor.reconnect_narration(
            campaign_id,
            attempt_id,
            acknowledged_sequence=acknowledged_sequence,
            expected_generation=expected_generation,
        )
        with self._sinks_lock:
            sinks = self._sinks.setdefault((campaign_id, attempt_id), [])
            sinks.append(_StreamSink(listener=listener, alive=True))
        # Replay the provisional tokens to the reattached listener.
        for projection in replay.provisional:
            token = getattr(projection, "text", None) or getattr(projection, "payload", "")
            channel = _projection_channel(projection)
            if token:
                with contextlib.suppress(ListenerGone):
                    listener(channel, str(token))
        return ReconnectedStream(
            canonical=replay.canonical,
            provisional=replay.provisional,
            high_water=replay.high_water,
            attachment_generation=attachment_generation,
        )

    def disconnect_stream(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> float:
        """Report a dropped desktop stream and arm grace-period cancellation."""

        return self._supervisor.signal_disconnect(
            campaign_id,
            attempt_id,
            expected_generation=expected_generation,
        )

    def heartbeat_stream(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> tuple[float, int]:
        """Renew the owner-observed renderer attachment lease."""

        return self._supervisor.heartbeat_narration(
            campaign_id,
            attempt_id,
            expected_generation=expected_generation,
        )

    def narration_attempt_status(self, campaign_id: str, attempt_id: str) -> str:
        """Return the owner-projected lifecycle of one narration attempt."""

        return self._supervisor.narration_attempt_status(campaign_id, attempt_id)

    def heartbeat_provider_stream(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> tuple[float, int]:
        """Renew one attached Builder/provider stream lease."""

        return self._supervisor.heartbeat_provider_work(
            campaign_id,
            attempt_id,
            expected_generation=expected_generation,
        )

    def disconnect_provider_stream(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> float:
        """Arm disconnect grace for one attached Builder/provider stream."""

        return self._supervisor.disconnect_provider_work(
            campaign_id,
            attempt_id,
            expected_generation=expected_generation,
        )

    def reconnect_provider_stream(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> int:
        """Reattach within grace and return the next attachment generation."""

        return self._supervisor.reconnect_provider_work(
            campaign_id,
            attempt_id,
            expected_generation=expected_generation,
        )

    def provider_stream_status(self, campaign_id: str, attempt_id: str) -> str:
        """Project one Builder/provider stream lifecycle."""

        return self._supervisor.provider_work_status(campaign_id, attempt_id)

    def interrupt_provider_stream(self, campaign_id: str, attempt_id: str) -> None:
        """Cancel only the live Builder/provider attempt, preserving its session."""

        self._supervisor.interrupt_provider_work(campaign_id, attempt_id)

    def interrupt_stream(self, campaign_id: str, attempt_id: str) -> NarrationInterruption:
        """Immediately cancel a narration attempt and drop its listeners."""

        interruption = self._supervisor.interrupt_narration(campaign_id, attempt_id)
        with self._sinks_lock:
            self._sinks.pop((campaign_id, attempt_id), None)
        return interruption

    def close(self) -> None:
        """Drop all listeners; the supervisor is shut down separately."""

        with self._sinks_lock:
            self._sinks.clear()


def _projection_channel(projection: object) -> ProseChannel:
    """Best-effort mapping of one projection kind to its prose channel."""

    kind = getattr(projection, "kind", None)
    if kind is not None:
        as_str = str(kind)
        if as_str == "think":
            return ProseChannel.SAFE_THINK
        if as_str == "talk":
            return ProseChannel.TALK
    return ProseChannel.TALK


__all__ = (
    "HandshakeStatus",
    "ListenerGone",
    "LocalCommandBus",
    "StreamListener",
)
