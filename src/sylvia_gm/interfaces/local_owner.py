"""F6 local owner supervisor — the desktop ownership layer.

The desktop supervisor launches and supervises exactly one local owner process
(the ``AuthorityOwner``), mediating client connections and enforcing the
ownership, handoff, interrupt, and recovery gates.

Responsibilities
----------------
* **G-OWNER** — At most one owner per database.  The second-instance decision
  (become-owner vs attach-client vs exit-with-recovery) is resolved before any
  database access.
* **G-RECOVER** — On disconnect/crash the committed mechanics are preserved and
  incomplete prose is discarded; reconnect replays the canonical sequence.
* **G-INTERRUPT** — Disconnect grace → cancel after the deadline; the
  supervisor owns the grace timer and calls ``expire_narration``.

Clients never touch the database.  They send commands and render projections,
routed through :mod:`sylvia_gm.interfaces.local_api`.

This module is the ownership *spine* — it owns the ``AuthorityOwner`` lifetime
and the launch handshake.  It deliberately stays free of HTTP/transport wiring
so it can be exercised in-process by the F6 acceptance tests and hosted by
the Tauri Rust shell.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import (
    CampaignConfirmation,
    CampaignConstructionBegun,
    CampaignConstructionCompleted,
    CanonicalReplay,
    GuidedCampaignDraft,
    GuidedCreationSubflow,
    NarrationInterruption,
    NarrationStream,
    Participant,
    RetryNarrationInput,
    StateValue,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import BuilderDMHandoff, CampaignPlan
from sylvia_gm.infrastructure.launch_handshake import (
    LaunchHandshake,
    OwnerCredential,
    derive_owner_response,
)
from sylvia_gm.infrastructure.owner_process_probe import (
    OwnerProbeResult,
    probe_owner_live,
    write_recover_hint,
)

logger = logging.getLogger(__name__)

#: Default disconnect grace in seconds before an interrupted narration is cancelled.
DEFAULT_DISCONNECT_GRACE_SECONDS = 30.0
# The renderer heartbeats every two seconds, but a busy WebView can occasionally
# delay one beat by several seconds. Page unload still signals disconnect
# immediately; this lease is only the fallback for a genuinely vanished renderer.
DEFAULT_ATTACHMENT_LEASE_SECONDS = 15.0
# A fresh local inference can spend substantial time ingesting a 131k context before its
# first token. Give the renderer enough time to win the initial attachment race; once its
# first heartbeat is observed, the normal short lease applies.
DEFAULT_INITIAL_ATTACHMENT_LEASE_SECONDS = 120.0


@dataclass(frozen=True)
class OwnerLaunchDecision:
    """The resolved role this desktop instance takes on startup."""

    #: ``owner`` — we became the sole owner and opened the authority.
    #: ``client`` — another owner is live; attach via the local API.
    #: ``recover`` — the lock is held but the port is dead; surface recovery.
    role: str

    #: Diagnostic reason for the UI (never contains secrets).
    reason: str

    #: The owner credential, only when ``role == "owner"``.
    credential: OwnerCredential | None = None

    #: The probe result for the second-instance decision.
    probe: OwnerProbeResult | None = None


@dataclass
class NarrationLease:
    """One live narration attempt tracked by the supervisor's interrupt timer."""

    campaign_id: str
    attempt_id: str
    turn_id: str
    cancel: Callable[[], None]
    deadline: float | None = field(default=None)
    timer: threading.Timer | None = field(default=None, repr=False)
    attachment_deadline: float | None = field(default=None)
    attachment_timer: threading.Timer | None = field(default=None, repr=False)
    attachment_generation: int = field(default=1)
    attachment_claimed: bool = field(default=False)


@dataclass
class ProviderWorkLease:
    """One attached, cancellable non-narration provider stream."""

    campaign_id: str
    attempt_id: str
    cancel: Callable[[], None]
    deadline: float | None = field(default=None)
    timer: threading.Timer | None = field(default=None, repr=False)
    attachment_deadline: float | None = field(default=None)
    attachment_timer: threading.Timer | None = field(default=None, repr=False)
    attachment_generation: int = field(default=1)
    attachment_claimed: bool = field(default=False)


class LocalOwnerSupervisor:
    """Owns one ``AuthorityOwner`` plus the launch/interrupt lifecycle.

    Lifecycle::

        decision = supervisor.decide(database_path, owner_alive_probe=...)
        if decision.role == "owner":
            supervisor.acquire_owner(database_path, owner_id, ...)
            ... # issue commands, attach narrations
            supervisor.shutdown()

    The supervisor is thread-safe; commands route through the underlying
    owner, which serialises on per-campaign lanes.
    """

    def __init__(
        self,
        *,
        disconnect_grace_seconds: float = DEFAULT_DISCONNECT_GRACE_SECONDS,
        attachment_lease_seconds: float = DEFAULT_ATTACHMENT_LEASE_SECONDS,
        initial_attachment_lease_seconds: float = DEFAULT_INITIAL_ATTACHMENT_LEASE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(disconnect_grace_seconds, (int, float))
            or isinstance(disconnect_grace_seconds, bool)
            or not math.isfinite(disconnect_grace_seconds)
            or disconnect_grace_seconds <= 0
        ):
            raise ValueError("Disconnect grace must be positive and finite")
        if (
            not isinstance(attachment_lease_seconds, (int, float))
            or isinstance(attachment_lease_seconds, bool)
            or not math.isfinite(attachment_lease_seconds)
            or attachment_lease_seconds <= 0
        ):
            raise ValueError("Attachment lease must be positive and finite")
        if (
            not isinstance(initial_attachment_lease_seconds, (int, float))
            or isinstance(initial_attachment_lease_seconds, bool)
            or not math.isfinite(initial_attachment_lease_seconds)
            or initial_attachment_lease_seconds < attachment_lease_seconds
        ):
            raise ValueError(
                "Initial attachment lease must be finite and at least the renewal lease"
            )
        self._disconnect_grace_seconds = float(disconnect_grace_seconds)
        self._attachment_lease_seconds = float(attachment_lease_seconds)
        self._initial_attachment_lease_seconds = float(initial_attachment_lease_seconds)
        self._clock = clock
        self._handshake_lock = threading.Lock()
        self._leases_lock = threading.Lock()
        self._leases: dict[tuple[str, str], NarrationLease] = {}
        self._provider_work_lock = threading.Lock()
        self._provider_work: dict[tuple[str, str], ProviderWorkLease] = {}
        self._owner: AuthorityOwner | None = None
        self._credential: OwnerCredential | None = None
        self._pending_handshake: LaunchHandshake | None = None
        self._handshake_verified = False
        self._owner_lock_held = False
        self._shutdown = False
        self._lifecycle_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Launch decision (second-instance behaviour)
    # ------------------------------------------------------------------

    def decide(
        self,
        database_path: Path,
        *,
        owner_alive_probe: Callable[[Path], bool] | None = None,
    ) -> OwnerLaunchDecision:
        """Resolve whether to become owner, attach as client, or surface recovery.

        ``owner_alive_probe`` lets the caller check the local API port without
        the supervisor importing HTTP.  When it reports the owner alive (or is
        omitted and the lock is held) the decision is ``client``; when the lock
        is held but the port is dead the decision is ``recover``; otherwise we
        become the owner.
        """

        probe = probe_owner_live(database_path)
        owner_alive = False
        if probe.owner_live and owner_alive_probe is not None:
            try:
                owner_alive = bool(owner_alive_probe(database_path))
            except Exception:  # pragma: no cover - probe failures mean recover
                logger.warning("Owner alive probe raised; treating as dead", exc_info=True)
                owner_alive = False
        if not probe.owner_live:
            return OwnerLaunchDecision(
                role="owner",
                reason="no live owner detected; acquiring ownership",
                credential=None,
                probe=probe,
            )
        if owner_alive:
            return OwnerLaunchDecision(
                role="client",
                reason="owner already live; attaching as client",
                probe=probe,
            )
        return OwnerLaunchDecision(
            role="recover",
            reason="owner lock held but owner unreachable; stale process or crash",
            probe=probe,
        )

    # ------------------------------------------------------------------
    # Owner acquisition + handshake
    # ------------------------------------------------------------------

    def acquire_owner(
        self,
        database_path: Path,
        owner_id: str,
    ) -> OwnerCredential:
        """Open the authority owner and arm the launch handshake.

        Raises ``OwnerConflict`` (via the underlying store) if the OS lock is
        already held — surfacing the G-OWNER boundary violation.  On success
        returns the per-launch credential to inject into the owner process's
        ephemeral handshake channel.
        """

        with self._lifecycle_lock:
            if self._owner is not None:
                raise RuntimeError("Supervisor already owns a process")
            if self._shutdown:
                raise RuntimeError("Supervisor has been shut down")
            owner = AuthorityOwner.open(
                database_path,
                owner_id,
                disconnect_grace_seconds=self._disconnect_grace_seconds,
            )
            # The store acquired the OS lock — remember to release on failure.
            self._owner_lock_held = True
            self._owner = owner
        # Issue the handshake *after* the lock is held so a concurrent second
        # instance sees the owner as live before we expose any credential.
        with self._handshake_lock:
            handshake = LaunchHandshake()
            issued = handshake.issue()
            self._credential = issued
            self._handshake_verified = False
            # Stash the verifier so complete_handshake can consume it.
            self._pending_handshake = handshake
        return issued

    def supervisor_response_token(self) -> str:
        """The response token the owner process exposes on ``/handshake``.

        The supervisor derives this from the issued secret and injects it into
        the owner process's ephemeral channel.  The owner then serves only this
        token — never the raw secret — so a client can confirm authenticity.
        """

        with self._handshake_lock:
            if self._credential is None:
                raise RuntimeError("No credential issued; acquire the owner first")
            return self._credential.expected_response_hex

    def complete_handshake(self, presented_response: str) -> None:
        """Verify the owner process presented the expected handshake token.

        Called once the supervisor's transport observes the owner's
        ``/handshake`` response.  On mismatch the supervisor refuses to route
        commands — the owner may be a stray process that grabbed the port.
        """

        with self._handshake_lock:
            handshake = self._pending_handshake
            if handshake is None:
                raise RuntimeError("No pending handshake to complete")
            handshake.verify(presented_response)
            self._handshake_verified = True
            self._pending_handshake = None
            # The secret is already erased inside the handshake; drop our copy too.
            self._credential = None

    @property
    def handshake_verified(self) -> bool:
        return self._handshake_verified

    def application_owner(self) -> AuthorityOwner:
        """Return the verified owner to trusted in-process application services.

        This is a composition-root seam for the owner process's canonical
        session loop.  Transport clients never receive this object or a
        persistence handle.
        """

        return self._require_verified_owner()

    # ------------------------------------------------------------------
    # Command routing — thin façade over AuthorityOwner
    # ------------------------------------------------------------------

    def _require_verified_owner(self) -> AuthorityOwner:
        with self._lifecycle_lock:
            if self._owner is None:
                raise RuntimeError("Supervisor does not own a process")
            if not self._handshake_verified:
                raise CommandRejected(
                    "Owner launch handshake has not been verified",
                    code="handshake_unverified",
                )
            if self._shutdown:
                raise RuntimeError("Supervisor has been shut down")
            return self._owner

    def install_package_archive(self, source: BinaryIO, *, install_id: str) -> object:
        """Route one Install-Package command through the application owner.

        Clients call this surface; they never receive the SQLite handle.
        """

        owner = self._require_verified_owner()
        return owner.install_package_archive(source, install_id=install_id)

    def package_candidate(self, candidate_id: str) -> object:
        """Resolve one durable package candidate without exposing its store."""

        owner = self._require_verified_owner()
        return owner.package_candidate(candidate_id)

    def package_candidates(self) -> object:
        """Project admitted durable package candidates without exposing the store."""

        owner = self._require_verified_owner()
        return owner.package_candidates()

    def inspect_package_candidate(self, candidate_id: str) -> object:
        """Project static metadata and the admitted file tree for one candidate."""

        owner = self._require_verified_owner()
        return owner.inspect_package_candidate(candidate_id)

    def package_candidate_text(
        self,
        candidate_id: str,
        package_id: str,
        portable_path: str,
    ) -> object:
        """Read one admitted, bounded text file without exposing storage paths."""

        owner = self._require_verified_owner()
        return owner.package_candidate_text(candidate_id, package_id, portable_path)

    def compare_package_candidates(self, left_id: str, right_id: str) -> object:
        """Compare exact installed package identities and file hashes."""

        owner = self._require_verified_owner()
        return owner.compare_package_candidates(left_id, right_id)

    def export_package_candidate(self, candidate_id: str, target: BinaryIO) -> object:
        """Export one exact installed package closure to a caller-owned stream."""

        owner = self._require_verified_owner()
        return owner.export_package_candidate(candidate_id, target)

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
        """Confirm one Builder-owned campaign identity from canonical user input."""

        owner = self._require_verified_owner()
        return owner.confirm_new_campaign(
            operation_id=operation_id,
            display_name=display_name,
            package_lock=package_lock,  # type: ignore[arg-type]
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
        owner = self._require_verified_owner()
        return owner.create_guided_campaign_draft(
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
        owner = self._require_verified_owner()
        return owner.navigate_guided_campaign_draft(
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
        owner = self._require_verified_owner()
        return owner.advance_guided_campaign_draft(
            operation_id=operation_id,
            campaign_id=campaign_id,
            builder_session_id=builder_session_id,
            expected_construction_revision=expected_construction_revision,
            current_step=current_step,
            visible_answer=visible_answer,
            answer=answer,  # type: ignore[arg-type]
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
        owner = self._require_verified_owner()
        return owner.bind_guided_campaign_basis(
            operation_id=operation_id,
            campaign_id=campaign_id,
            display_name=display_name,
            package_lock=package_lock,  # type: ignore[arg-type]
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
        owner = self._require_verified_owner()
        return owner.start_guided_creation_subflow(
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
        owner = self._require_verified_owner()
        return owner.return_guided_creation_subflow(
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
        owner = self._require_verified_owner()
        return owner.begin_campaign_construction(
            operation_id=operation_id,
            campaign_id=campaign_id,
            lane=lane,
            expected_revision=expected_revision,
            canonical_input=canonical_input,  # type: ignore[arg-type]
        )

    def complete_campaign_construction(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        expected_revision: int,
        canonical_plan: object,
    ) -> object:
        owner = self._require_verified_owner()
        return owner.complete_campaign_construction(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            canonical_plan=canonical_plan,  # type: ignore[arg-type]
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
        owner = self._require_verified_owner()
        return owner.finalize_campaign_construction(
            campaign_id=campaign_id,
            dm_provider_id=dm_provider_id,
            dm_model_id=dm_model_id,
            plan_artifact_text=plan_artifact_text,
            structured_plan=structured_plan,  # type: ignore[arg-type]
            model_guidance_override=model_guidance_override,
        )

    def model_guidance(
        self,
        *,
        campaign_id: str,
        model_id: str,
        override_accepted: bool = False,
    ) -> object:
        owner = self._require_verified_owner()
        return owner.model_guidance(
            campaign_id=campaign_id,
            model_id=model_id,
            override_accepted=override_accepted,
        )

    def catalog(self, campaign_id: str) -> object:
        """Project the exact admitted tool catalog for one campaign."""

        owner = self._require_verified_owner()
        return owner.catalog(campaign_id)

    def snapshot(self, campaign_id: str) -> object:
        owner = self._require_verified_owner()
        return owner.snapshot(campaign_id)

    # ------------------------------------------------------------------
    # F6C — player-facing query + campaign command surfaces
    # Every method delegates to the AuthorityOwner; the supervisor holds
    # no extra state.  The bus renders these as projections; the renderer
    # never receives the SQLite handle.
    # ------------------------------------------------------------------

    def campaign_index(self) -> object:
        """Project the coherent campaign index through the normal owner lifecycle."""

        owner = self._require_verified_owner()
        return owner.campaign_index()

    def player_projection(self, campaign_id: str) -> object:
        """Render the authoritative player-safe projection (no private events)."""

        owner = self._require_verified_owner()
        return owner.player_projection(campaign_id)

    def create_campaign(self, command: object) -> object:
        """Route one Campaign-Create command through the application owner."""

        owner = self._require_verified_owner()
        return owner.create_campaign(command)  # type: ignore[arg-type]

    def create_agent_session(
        self,
        *,
        campaign_id: str,
        role: object,
        provider_id: str,
        model_id: str,
        session_id: str,
    ) -> object:
        """Route one top-level agent-session creation through the owner."""

        owner = self._require_verified_owner()
        return owner.create_agent_session(
            campaign_id=campaign_id,
            role=role,  # type: ignore[arg-type]
            provider_id=provider_id,
            model_id=model_id,
            session_id=session_id,
        )

    def stop_agent_session(self, session_id: str) -> object:
        """Stop one authoritative Builder/DM session without exposing state."""

        owner = self._require_verified_owner()
        return owner.stop_agent_session(session_id)

    def resume_agent_session(self, session_id: str) -> object:
        """Explicitly resume one stopped top-level writer session."""

        owner = self._require_verified_owner()
        return owner.resume_agent_session(session_id)

    def execute_action(self, command: object) -> object:
        """Route one Action-Execute command through the application owner."""

        owner = self._require_verified_owner()
        return owner.execute_action(command)  # type: ignore[arg-type]

    def resolve_mechanics_choice(self, command: object) -> object:
        """Resolve one durable rules choice through the verified application owner."""

        owner = self._require_verified_owner()
        return owner.resolve_mechanics_choice(command)  # type: ignore[arg-type]

    def open_turn(self, campaign_id: str, turn_id: str) -> None:
        owner = self._require_verified_owner()
        owner.open_turn(campaign_id, turn_id)

    def complete_turn(self, campaign_id: str, turn_id: str) -> None:
        owner = self._require_verified_owner()
        owner.complete_turn(campaign_id, turn_id)

    def rename_campaign(
        self, *, operation_id: str, campaign_id: str, display_name: str, expected_revision: int
    ) -> object:
        owner = self._require_verified_owner()
        return owner.rename_campaign(
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
        owner = self._require_verified_owner()
        return owner.archive_campaign(
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
        owner = self._require_verified_owner()
        return owner.restore_archived_campaign(
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
        owner = self._require_verified_owner()
        return owner.trash_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            designated_session_id=designated_session_id,
        )

    def restore_trashed_campaign(
        self, *, operation_id: str, campaign_id: str, expected_revision: int
    ) -> object:
        owner = self._require_verified_owner()
        return owner.restore_trashed_campaign(
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
        owner = self._require_verified_owner()
        return owner.purge_campaign(
            operation_id=operation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            confirmation=confirmation,
        )

    def backup_database(self, target: Path) -> None:
        owner = self._require_verified_owner()
        owner.backup_database(target)

    def export_campaign(
        self, operation_id: str, campaign_id: str, target: Path, *, include_thinking: bool = False,
    ) -> object:
        owner = self._require_verified_owner()
        return owner.export_campaign(
            operation_id, campaign_id, target, include_thinking=include_thinking,
        )

    def recover_campaign(self, *, recovery_id: str, source: Path) -> object:
        owner = self._require_verified_owner()
        return owner.recover_campaign(recovery_id=recovery_id, source=source)

    def complete_narration_attempt(self, campaign_id: str, attempt_id: str) -> object:
        """Commit the current safe prose buffer and return the canonical events."""

        owner = self._require_verified_owner()
        return owner.complete_narration_attempt(campaign_id, attempt_id)

    def retryable_interruption(self, campaign_id: str, *, failed_attempt_id: str) -> object:
        """Project one durable retry basis without exposing persistence."""

        owner = self._require_verified_owner()
        return owner.retryable_interruption(campaign_id, failed_attempt_id=failed_attempt_id)

    def manual_retry(
        self,
        *,
        campaign_id: str,
        interruption_id: str,
        retry_id: str,
        narrator: Callable[[RetryNarrationInput, NarrationStream], None],
        cancel: Callable[[], None] = lambda: None,
    ) -> object:
        """Route one manual narration retry through the application owner."""

        owner = self._require_verified_owner()
        return owner.manual_retry(
            campaign_id=campaign_id,
            interruption_id=interruption_id,
            retry_id=retry_id,
            narrator=narrator,
            cancel=cancel,
        )

    # ------------------------------------------------------------------
    # Narration attach / disconnect grace / cancel (G-INTERRUPT, G-RECOVER)
    # ------------------------------------------------------------------

    def attach_narration_attempt(
        self,
        *,
        campaign_id: str,
        attempt_id: str,
        turn_id: str,
        cancel: Callable[[], None],
        last_action_id: str | None = None,
    ) -> None:
        """Begin one narration attempt and arm the disconnect-grace contract.

        The supervisor records the lease and delegates to the owner.  The
        provided ``cancel`` is the provider/stream cancellation handle; the
        supervisor calls it once the disconnect grace deadline elapses.
        """

        owner = self._require_verified_owner()
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id=attempt_id,
            turn_id=turn_id,
            cancel=cancel,
            last_action_id=last_action_id,
        )
        self.track_narration_attempt(campaign_id, attempt_id, cancel, turn_id=turn_id)

    def track_narration_attempt(
        self,
        campaign_id: str,
        attempt_id: str,
        cancel: Callable[[], None],
        *,
        turn_id: str = "",
    ) -> None:
        """Track an authority attempt that was started by the canonical session loop."""

        if not callable(cancel):
            raise TypeError("Narration cancellation handle must be callable")
        lease = NarrationLease(
            campaign_id=campaign_id,
            attempt_id=attempt_id,
            turn_id=turn_id,
            cancel=cancel,
            deadline=None,
            timer=None,
        )
        with self._lifecycle_lock:
            if self._shutdown:
                shutdown_rejected = True
            else:
                shutdown_rejected = False
                with self._leases_lock:
                    previous = self._leases.pop((campaign_id, attempt_id), None)
                    if previous is not None:
                        if previous.timer is not None:
                            previous.timer.cancel()
                        if previous.attachment_timer is not None:
                            previous.attachment_timer.cancel()
                    self._leases[(campaign_id, attempt_id)] = lease
                    self._arm_attachment_timer(lease)
        if shutdown_rejected:
            try:
                cancel()
            except Exception:  # pragma: no cover - fail closed despite a bad callback
                logger.warning(
                    "Narration cancellation raised during rejected lease publication",
                    exc_info=True,
                )
            raise CommandRejected("Owner shutdown blocks narration-work admission")

    def release_narration_attempt(self, campaign_id: str, attempt_id: str) -> None:
        """Release timers after provider work reaches a terminal owner state."""

        with self._leases_lock:
            lease = self._leases.pop((campaign_id, attempt_id), None)
        if lease is None:
            return
        if lease.timer is not None:
            lease.timer.cancel()
        if lease.attachment_timer is not None:
            lease.attachment_timer.cancel()

    def track_provider_work(
        self,
        campaign_id: str,
        attempt_id: str,
        cancel: Callable[[], None],
    ) -> None:
        """Track non-narration provider work for authoritative owner shutdown."""

        if not callable(cancel):
            raise TypeError("Provider-work cancellation handle must be callable")
        lease = ProviderWorkLease(campaign_id, attempt_id, cancel)
        with self._lifecycle_lock:
            if self._shutdown:
                raise CommandRejected("Owner shutdown blocks provider-work admission")
            with self._provider_work_lock:
                self._provider_work[(campaign_id, attempt_id)] = lease
                self._arm_provider_attachment_timer(lease)

    def release_provider_work(self, campaign_id: str, attempt_id: str) -> None:
        """Release one terminal non-narration provider lease."""

        with self._provider_work_lock:
            lease = self._provider_work.pop((campaign_id, attempt_id), None)
        if lease is not None:
            if lease.timer is not None:
                lease.timer.cancel()
            if lease.attachment_timer is not None:
                lease.attachment_timer.cancel()

    def _provider_lease(self, campaign_id: str, attempt_id: str) -> ProviderWorkLease:
        lease = self._provider_work.get((campaign_id, attempt_id))
        if lease is None:
            raise CommandRejected("Provider stream attachment is unavailable")
        return lease

    @staticmethod
    def _require_provider_generation(
        lease: ProviderWorkLease,
        expected_generation: int | None,
    ) -> None:
        if expected_generation is None:
            if lease.attachment_generation != 1 or lease.attachment_claimed:
                raise CommandRejected("Provider attachment generation is required")
            return
        if expected_generation != lease.attachment_generation:
            raise CommandRejected("Provider attachment generation is stale")

    def _arm_provider_attachment_timer(self, lease: ProviderWorkLease) -> None:
        if lease.attachment_timer is not None:
            lease.attachment_timer.cancel()
        duration = (
            self._attachment_lease_seconds
            if lease.attachment_claimed
            else self._initial_attachment_lease_seconds
        )
        deadline = self._clock() + duration
        lease.attachment_deadline = deadline
        timer = threading.Timer(
            duration,
            self._on_provider_attachment_expired,
            args=(lease.campaign_id, lease.attempt_id, deadline),
        )
        timer.daemon = True
        lease.attachment_timer = timer
        timer.start()

    def _on_provider_attachment_expired(
        self,
        campaign_id: str,
        attempt_id: str,
        expected_deadline: float,
    ) -> None:
        with self._provider_work_lock:
            lease = self._provider_work.get((campaign_id, attempt_id))
            if lease is None or lease.attachment_deadline != expected_deadline:
                return
            lease.attachment_deadline = None
            lease.attachment_timer = None
            if lease.deadline is not None:
                return
            lease.deadline = self._clock() + self._disconnect_grace_seconds
            self._arm_provider_grace_timer(lease)

    def _arm_provider_grace_timer(self, lease: ProviderWorkLease) -> None:
        if lease.timer is not None:
            lease.timer.cancel()
        delay = max(0.0, (lease.deadline or 0.0) - self._clock())
        timer = threading.Timer(delay, self._on_provider_grace_expired, args=(lease,))
        timer.daemon = True
        lease.timer = timer
        timer.start()

    def _on_provider_grace_expired(self, lease: ProviderWorkLease) -> None:
        with self._provider_work_lock:
            current = self._provider_work.get((lease.campaign_id, lease.attempt_id))
            if current is not lease or lease.deadline is None:
                return
            lease.timer = None
            lease.cancel()

    def heartbeat_provider_work(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> tuple[float, int]:
        with self._provider_work_lock:
            lease = self._provider_lease(campaign_id, attempt_id)
            if lease.deadline is not None:
                raise CommandRejected("Provider stream is disconnected")
            self._require_provider_generation(lease, expected_generation)
            lease.attachment_claimed = True
            self._arm_provider_attachment_timer(lease)
            assert lease.attachment_deadline is not None
            return lease.attachment_deadline, lease.attachment_generation

    def disconnect_provider_work(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> float:
        with self._provider_work_lock:
            lease = self._provider_lease(campaign_id, attempt_id)
            self._require_provider_generation(lease, expected_generation)
            lease.deadline = self._clock() + self._disconnect_grace_seconds
            lease.attachment_deadline = None
            if lease.attachment_timer is not None:
                lease.attachment_timer.cancel()
                lease.attachment_timer = None
            self._arm_provider_grace_timer(lease)
            return lease.deadline

    def reconnect_provider_work(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> int:
        with self._provider_work_lock:
            lease = self._provider_lease(campaign_id, attempt_id)
            self._require_provider_generation(lease, expected_generation)
            if lease.deadline is None or self._clock() > lease.deadline:
                raise CommandRejected("Provider stream reconnect grace has elapsed")
            lease.deadline = None
            if lease.timer is not None:
                lease.timer.cancel()
                lease.timer = None
            lease.attachment_generation += 1
            lease.attachment_claimed = False
            self._arm_provider_attachment_timer(lease)
            return lease.attachment_generation

    def provider_work_status(self, campaign_id: str, attempt_id: str) -> str:
        with self._provider_work_lock:
            lease = self._provider_work.get((campaign_id, attempt_id))
            if lease is None:
                return "completed"
            return "disconnected" if lease.deadline is not None else "active"

    def interrupt_provider_work(self, campaign_id: str, attempt_id: str) -> None:
        with self._provider_work_lock:
            lease = self._provider_lease(campaign_id, attempt_id)
            lease.cancel()

    def heartbeat_narration(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> tuple[float, int]:
        """Renew one renderer attachment lease owned by the supervisor."""

        with self._leases_lock:
            lease = self._leases.get((campaign_id, attempt_id))
            if lease is None or lease.deadline is not None:
                raise CommandRejected("Narration attachment is unavailable")
            if expected_generation is None:
                if lease.attachment_claimed:
                    raise CommandRejected("Narration attachment generation is required")
                lease.attachment_claimed = True
            elif expected_generation != lease.attachment_generation:
                raise CommandRejected("Narration attachment generation is stale")
            else:
                lease.attachment_claimed = True
            status = self._require_verified_owner().narration_attempt_status(
                campaign_id, attempt_id
            )
            if status != "active":
                raise CommandRejected("Narration attempt is not actively attached")
            self._arm_attachment_timer(lease)
            assert lease.attachment_deadline is not None
            return lease.attachment_deadline, lease.attachment_generation

    def _arm_attachment_timer(self, lease: NarrationLease) -> None:
        """Arm or renew the owner-observed renderer attachment deadline."""

        if lease.attachment_timer is not None:
            lease.attachment_timer.cancel()
        duration = (
            self._attachment_lease_seconds
            if lease.attachment_claimed
            else self._initial_attachment_lease_seconds
        )
        deadline = self._clock() + duration
        lease.attachment_deadline = deadline
        timer = threading.Timer(
            duration,
            self._on_attachment_expired,
            args=(lease.campaign_id, lease.attempt_id, deadline),
        )
        timer.daemon = True
        lease.attachment_timer = timer
        timer.start()

    def _on_attachment_expired(
        self, campaign_id: str, attempt_id: str, expected_deadline: float
    ) -> None:
        """Convert a missed renderer heartbeat into the normal disconnect grace."""

        with self._leases_lock:
            lease = self._leases.get((campaign_id, attempt_id))
            if lease is None or lease.attachment_deadline != expected_deadline:
                return
            lease.attachment_deadline = None
            lease.attachment_timer = None
            if lease.deadline is not None:
                return
            try:
                deadline = self._require_verified_owner().disconnect_narration(
                    campaign_id, attempt_id, now=self._clock()
                )
            except CommandRejected:
                self._leases.pop((campaign_id, attempt_id), None)
                return
            lease.deadline = deadline
            self._arm_grace_timer(lease)

    def signal_disconnect(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        expected_generation: int | None = None,
    ) -> float:
        """A client stream dropped — start the disconnect grace window.

        Returns the deadline (monotonic seconds).  The supervisor records it
        and arms a timer that will cancel the narration after the grace
        elapses, preserving committed mechanics and discarding incomplete prose.
        """

        with self._leases_lock:
            lease = self._leases.get((campaign_id, attempt_id))
            if expected_generation is not None:
                if lease is None or expected_generation != lease.attachment_generation:
                    raise CommandRejected("Narration attachment generation is stale")
                lease.attachment_claimed = True
            owner = self._require_verified_owner()
            deadline = owner.disconnect_narration(campaign_id, attempt_id, now=self._clock())
            if lease is None:
                lease = NarrationLease(
                    campaign_id=campaign_id,
                    attempt_id=attempt_id,
                    turn_id="",
                    cancel=lambda: None,
                )
                self._leases[(campaign_id, attempt_id)] = lease
            lease.deadline = deadline
            lease.attachment_deadline = None
            if lease.attachment_timer is not None:
                lease.attachment_timer.cancel()
                lease.attachment_timer = None
            self._arm_grace_timer(lease)
        return deadline

    def _arm_grace_timer(self, lease: NarrationLease) -> None:
        """Arm (or re-arm) the wall-clock cancellation timer for one lease."""

        if lease.timer is not None:
            lease.timer.cancel()
        delay = max(0.0, (lease.deadline or 0.0) - self._clock())
        timer = threading.Timer(delay, self._on_grace_expired, args=(lease,))
        timer.daemon = True
        lease.timer = timer
        timer.start()

    def _on_grace_expired(self, lease: NarrationLease) -> None:
        """Expire the current grace lease through owner authority exactly once."""

        with self._leases_lock:
            current = self._leases.get((lease.campaign_id, lease.attempt_id))
            if current is not lease or lease.deadline is None:
                return
            lease.timer = None
            try:
                owner = self._require_verified_owner()
                owner.expire_narration(lease.campaign_id, lease.attempt_id, now=self._clock())
            except Exception:  # pragma: no cover - idempotent expiry
                logger.debug("Grace-expire narration expiry raised", exc_info=True)

    def reconnect_narration(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        acknowledged_sequence: int,
        expected_generation: int | None = None,
    ) -> tuple[CanonicalReplay, int]:
        """Reconnect within the grace window and replay the canonical sequence.

        On success the provisional (incomplete) stream tokens are replayed
        alongside the committed canonical events.  If the grace elapsed the
        owner raises ``CommandRejected`` and the lease is torn down.
        """

        with self._leases_lock:
            lease = self._leases.get((campaign_id, attempt_id))
            if lease is None:
                raise CommandRejected("Narration attachment lease is unavailable")
            if expected_generation is not None and (
                not lease.attachment_claimed or expected_generation != lease.attachment_generation
            ):
                raise CommandRejected("Narration attachment generation is stale")
            owner = self._require_verified_owner()
            replay = owner.reconnect_narration(
                campaign_id,
                attempt_id,
                now=self._clock(),
                acknowledged_sequence=acknowledged_sequence,
            )
            lease.deadline = None
            if lease.timer is not None:
                lease.timer.cancel()
                lease.timer = None
            lease.attachment_generation += 1
            lease.attachment_claimed = False
            self._arm_attachment_timer(lease)
            attachment_generation = lease.attachment_generation
        return replay, attachment_generation

    def narration_attempt_status(self, campaign_id: str, attempt_id: str) -> str:
        """Return the verified owner's narration-attempt lifecycle projection."""

        return self._require_verified_owner().narration_attempt_status(campaign_id, attempt_id)

    def interrupt_narration(self, campaign_id: str, attempt_id: str) -> NarrationInterruption:
        """Immediately cancel a narration attempt (Stop)."""

        owner = self._require_verified_owner()
        interruption = owner.interrupt_narration(campaign_id, attempt_id)
        with self._leases_lock:
            lease = self._leases.pop((campaign_id, attempt_id), None)
        if lease is not None and lease.timer is not None:
            lease.timer.cancel()
        if lease is not None and lease.attachment_timer is not None:
            lease.attachment_timer.cancel()
        return interruption

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Cancel all narration leases and close the owner process."""

        with self._lifecycle_lock:
            if self._shutdown:
                return
            self._shutdown = True
        # Snapshot and detach timers first so no callback races shutdown.  Then
        # interrupt through owner authority before ``owner.close()`` waits for
        # live provider operations; the authority path invokes each provider
        # cancellation handle and durably records the retry basis.
        with self._leases_lock:
            leases = tuple(self._leases.values())
            for lease in leases:
                if lease.timer is not None:
                    lease.timer.cancel()
                if lease.attachment_timer is not None:
                    lease.attachment_timer.cancel()
            self._leases.clear()
        with self._provider_work_lock:
            provider_work = tuple(self._provider_work.values())
            for provider_lease in provider_work:
                if provider_lease.timer is not None:
                    provider_lease.timer.cancel()
                if provider_lease.attachment_timer is not None:
                    provider_lease.attachment_timer.cancel()
            self._provider_work.clear()
        for provider_lease in provider_work:
            try:
                provider_lease.cancel()
            except Exception:  # pragma: no cover - continue closing safely
                logger.warning("Provider-work cancellation raised during shutdown", exc_info=True)
        owner = self._owner
        if owner is not None:
            for lease in leases:
                try:
                    owner.interrupt_narration(lease.campaign_id, lease.attempt_id)
                except CommandRejected:
                    # Completion may have won after the lease snapshot.  Its
                    # terminal owner state needs no shutdown cancellation.
                    logger.debug(
                        "Narration reached a terminal state during shutdown",
                        exc_info=True,
                    )
                except Exception:  # pragma: no cover - continue closing safely
                    logger.warning("Narration interruption raised during shutdown", exc_info=True)
        self._owner = None
        if owner is not None:
            try:
                owner.close()
            except Exception:  # pragma: no cover - close must not raise
                logger.warning("Owner close raised during shutdown", exc_info=True)
        self._owner_lock_held = False
        self._credential = None
        self._handshake_verified = False

    # ------------------------------------------------------------------
    # Recovery — stale owner (crash)
    # ------------------------------------------------------------------

    @staticmethod
    def write_recovery_hint(database_path: Path, *, message: str | None = None) -> Path:
        """Surface a recovery instruction for a stale (crashed) owner."""

        hint = message or (
            "A previous Sylvia GM owner process may have crashed while holding "
            "the ownership lock. Restart the owner process, or if the problem "
            "persists, remove the stale lock file and relaunch."
        )
        return write_recover_hint(database_path, hint)

    # ------------------------------------------------------------------
    # Context-manager convenience
    # ------------------------------------------------------------------

    @contextmanager
    def own(
        self,
        database_path: Path,
        owner_id: str,
    ) -> Iterator[LocalOwnerSupervisor]:
        """Context-manager wrapper that acquires the owner and shuts down on exit.

        Performs the launch handshake round-trip via the single-process in-test
        path: the supervisor derives the response token itself and immediately
        verifies it, so tests exercise the exact verify code without a socket.
        """

        acquired = self.acquire_owner(database_path, owner_id)
        # In-process: present the token we just derived to the verifier.
        response = derive_owner_response(acquired.secret_hex)
        self.complete_handshake(response)
        try:
            yield self
        finally:
            self.shutdown()


__all__ = (
    "DEFAULT_DISCONNECT_GRACE_SECONDS",
    "LocalOwnerSupervisor",
    "NarrationLease",
    "OwnerLaunchDecision",
)
