"""Shared Builder/DM provider loop over the existing F2 session mechanics."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TypeVar, cast

from sylvia_gm.agent._activity import AttemptProgress
from sylvia_gm.agent._builder_question_answers import builder_question_answer_content
from sylvia_gm.agent._runner import AgentLoopError
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool, StatelessMcpMechanics
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
)
from sylvia_gm.domain.authority import (
    ActionReceipt,
    AwaitingChoiceReceipt,
    CanonicalEvent,
    CanonicalReplay,
    ManualRetryResult,
    NarrationInterruption,
    NarrationStream,
    ProseChannel,
    RetryNarrationInput,
    SafePayload,
    WriterRole,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, MechanicsRejected
from sylvia_gm.domain.provider_records import ProviderPrivateRecord
from sylvia_gm.domain.session import (
    BuilderChatRecord,
    SessionContext,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
)
from sylvia_gm.provider._exceptions import ProviderError

_AdmissionResult = TypeVar("_AdmissionResult")
_MAX_PROVISIONAL_SESSION_CHARS = 256_000


def resolve_retry_basis_message(
    context: SessionContext,
    failed_attempt_id: str,
) -> SessionMessage:
    """Resolve a retry chain to the one durable player input or opening message."""

    direct = tuple(
        message
        for message in context.messages
        if message.content.get("attempt_id") == failed_attempt_id
        and message.kind in {SessionMessageKind.USER, SessionMessageKind.EVIDENCE}
        and message.content.get("kind") not in {"session_attempt_terminal", "player_action_response_retry"}
    )
    if len(direct) == 1:
        return direct[0]
    if direct:
        raise CommandRejected("Retry input identity is unavailable or ambiguous")

    terminals = tuple(
        message
        for message in context.messages
        if message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "session_attempt_terminal"
        and message.content.get("attempt_id") == failed_attempt_id
    )
    if len(terminals) != 1:
        raise CommandRejected("Retry input identity is unavailable or ambiguous")
    original_message_id = terminals[0].content.get("message_id")
    if not isinstance(original_message_id, str) or not original_message_id:
        raise CommandRejected("Retry input identity is unavailable or ambiguous")
    original = tuple(
        message
        for message in context.messages
        if message.message_id == original_message_id
        and message.kind in {SessionMessageKind.USER, SessionMessageKind.EVIDENCE}
        and message.content.get("kind") not in {"session_attempt_terminal", "player_action_response_retry"}
    )
    if len(original) != 1:
        raise CommandRejected("Retry input identity is unavailable or ambiguous")
    return original[0]


def _pending_campaign_file_checkpoint(
    context: SessionContext,
    *,
    basis_message_id: str,
) -> bool:
    """Return whether this input has held prose that still needs its GM-screen write."""

    held_sequences = tuple(
        message.sequence
        for message in context.messages
        if message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "provider_held_narration"
        and message.content.get("basis_message_id") == basis_message_id
    )
    if not held_sequences:
        return False
    latest_held = max(held_sequences)
    return not any(
        message.kind is SessionMessageKind.TALK and message.sequence > latest_held
        for message in context.messages
    )


def resolve_interruption_basis(
    context: SessionContext, interruption: NarrationInterruption,
) -> SessionMessage:
    """Prefer atomically accepted replacement input, including recovery before inference."""

    replacement = next((
        message for message in context.messages
        if message.kind is SessionMessageKind.USER
        and message.content.get("attempt_id") == interruption.resolving_retry_id
        and message.content.get("redirect_interruption_id") == interruption.interruption_id
    ), None)
    return replacement or resolve_retry_basis_message(context, interruption.failed_attempt_id)


def _recovery_kind(
    context: SessionContext,
    basis: SessionMessage,
    interruption: NarrationInterruption,
) -> str:
    """Identify the exact phase an interrupted GM turn must resume from."""

    if _pending_campaign_file_checkpoint(context, basis_message_id=basis.message_id):
        return "campaign_file_checkpoint"
    if interruption.last_action_id is not None:
        return "post_mechanics_narration"
    if basis.content.get("kind") == "campaign_start":
        return "opening"
    return "uncommitted_player_action"


def _terminal_recovery_kind(context: SessionContext, failed_attempt_id: str) -> str | None:
    """Read one persisted terminal phase without inferring it from mechanics alone."""

    terminals = tuple(
        message
        for message in context.messages
        if message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "session_attempt_terminal"
        and message.content.get("attempt_id") == failed_attempt_id
    )
    if len(terminals) != 1:
        return None
    value = terminals[0].content.get("recovery_kind")
    return value if isinstance(value, str) and value else None


class ProviderEventKind(StrEnum):
    PRIVATE_REASONING = "private_reasoning"
    PRIVATE_CONTINUATION = "private_continuation"
    SAFE_THINK = "safe_think"
    TALK = "talk"
    ACTION = "action"
    EVIDENCE = "evidence"
    PROSE_COMPLETE = "prose_complete"


class SessionInputKind(StrEnum):
    USER = "user"
    CAMPAIGN_START = "campaign_start"
    BUILDER_START = "builder_start"
    PLAYER_ACTION_RESPONSE = "player_action_response"


@dataclass
class ProviderActionFeedback:
    """Mutable return channel for a privacy-safe rejected provider action."""

    rejection: SafePayload | None = None


def _mechanics_rejection_feedback(error: MechanicsRejected) -> SafePayload:
    """Return the exact bounded correction facts a GM needs after mechanics reject."""

    validation: dict[str, object] = {"field_paths": list(error.field_paths)}
    if error.validation_issues:
        validation["issues"] = [issue.as_payload() for issue in error.validation_issues]
    return cast(
        SafePayload,
        {
            "status": "rejected",
            "code": error.code,
            "reason": str(error),
            "validation": validation,
            "state_changed": error.state_changed,
            "instruction": (
                "Use validation.issues and the current tool schemas to correct every declared "
                "field. If this operation cannot represent the intended action, choose a "
                "different currently exposed operation that can. Do not repeat an identical "
                "rejected call."
            ),
        },
    )


@dataclass(frozen=True)
class ProviderEvent:
    kind: ProviderEventKind
    text: str | None = field(default=None, repr=False)
    action_command: ActionCommand | None = None
    evidence: SafePayload | None = None
    message_id: str | None = None
    action_feedback: ProviderActionFeedback | None = None
    response_blocks: tuple[SafePayload, ...] | None = None
    response_id: str | None = None

    @classmethod
    def private_reasoning(cls, text: str, *, response_id: str | None = None) -> ProviderEvent:
        return cls(ProviderEventKind.PRIVATE_REASONING, text=text, response_id=response_id)

    @classmethod
    def private_continuation(
        cls, payload: Mapping[str, object], *, response_id: str
    ) -> ProviderEvent:
        """Snapshot native protocol data without placing it in public tool evidence."""
        if not response_id:
            raise ValueError("Native continuation requires a response identity")
        return cls(
            ProviderEventKind.PRIVATE_CONTINUATION,
            text=json.dumps(payload, ensure_ascii=False, allow_nan=False),
            response_id=response_id,
        )

    @classmethod
    def safe_think(cls, text: str) -> ProviderEvent:
        return cls(ProviderEventKind.SAFE_THINK, text=text)

    @classmethod
    def talk(
        cls, text: str, *, response_blocks: Sequence[SafePayload] | None = None,
        response_id: str | None = None,
    ) -> ProviderEvent:
        return cls(
            ProviderEventKind.TALK,
            text=text,
            response_blocks=None if response_blocks is None else tuple(response_blocks),
            response_id=response_id,
        )

    @classmethod
    def action(
        cls,
        command: ActionCommand,
        *,
        feedback: ProviderActionFeedback | None = None,
    ) -> ProviderEvent:
        return cls(ProviderEventKind.ACTION, action_command=command, action_feedback=feedback)

    @classmethod
    def tool_evidence(cls, *, message_id: str, payload: SafePayload) -> ProviderEvent:
        return cls(ProviderEventKind.EVIDENCE, evidence=payload, message_id=message_id)

    @classmethod
    def prose_complete(cls) -> ProviderEvent:
        return cls(ProviderEventKind.PROSE_COMPLETE)


@dataclass(frozen=True)
class SessionRunRequest:
    session_id: str
    turn_id: str
    attempt_id: str
    message_id: str
    user_text: str
    ooc_text: str | None = None
    input_kind: SessionInputKind = SessionInputKind.USER
    player_action_id: str | None = None
    automatic_response: bool = False
    redirect_interruption_id: str | None = None
    campaign_ending_choice: SafePayload | None = None
    builder_question_answers: tuple[SafePayload, ...] | None = None


class ProviderControl:
    """Injected cancellation/steering control containing no persistence handle."""

    def __init__(self) -> None:
        self.progress = AttemptProgress()
        self._cancelled = threading.Event()
        self._cancel_lock = threading.Lock()
        self._cancel_count = 0
        self._steering_lock = threading.Lock()
        self._steering_ready = threading.Event()
        self._steering: list[str] = []
        self._steering_closed = False
        self._status_hint: str | None = None

    def set_status_hint(self, text: str | None) -> None:
        """Expose application progress only; callers never pass model-generated text here."""
        self._status_hint = text
        self.progress.update("compacting" if text else "working")

    @property
    def status_hint(self) -> str | None:
        return self._status_hint

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def cancellation_count(self) -> int:
        with self._cancel_lock:
            return self._cancel_count

    def admit(self, start: Callable[[], _AdmissionResult]) -> _AdmissionResult:
        """Linearize one non-blocking provider admission against cancellation."""

        with self._cancel_lock:
            if self._cancelled.is_set():
                raise CommandRejected("Provider work was cancelled before admission")
            return start()

    def cancel(self) -> None:
        with self._cancel_lock:
            if self._cancelled.is_set():
                return
            self._cancelled.set()
            self._cancel_count += 1
            self.progress.update("stopping")

    def wait_cancelled(self, timeout: float | None = None) -> bool:
        return self._cancelled.wait(timeout)

    def add_steering(self, text: str) -> None:
        self.admit_steering(text, lambda: None)

    def admit_steering(self, text: str, persist: Callable[[], None]) -> None:
        """Atomically persist and queue steering while the provider still accepts it."""

        with self._steering_lock:
            if self._steering_closed:
                raise CommandRejected("Agent session provider work no longer accepts steering")
            persist()
            self._steering.append(text)
            self._steering_ready.set()

    def take_steering(self) -> tuple[str, ...]:
        with self._steering_lock:
            result = tuple(self._steering)
            self._steering.clear()
            self._steering_ready.clear()
            return result

    def wait_for_steering(self, timeout: float | None = None) -> tuple[str, ...]:
        if not self._steering_ready.wait(timeout):
            return ()
        return self.take_steering()

    def take_steering_or_close(self) -> tuple[str, ...]:
        """Take queued steering or atomically close the live steering window."""

        with self._steering_lock:
            if self._steering:
                result = tuple(self._steering)
                self._steering.clear()
                self._steering_ready.clear()
                return result
            self._steering_closed = True
            self._steering_ready.clear()
            return ()


@dataclass(frozen=True)
class SessionProviderInput:
    session: SessionRecord
    history: SessionContext
    catalog: tuple[ModelMechanicsTool, ...]
    control: ProviderControl
    catalog_supplier: Callable[[str], tuple[ModelMechanicsTool, ...]] | None = None
    knowledge_searcher: (
        Callable[[str, Sequence[str] | None, int, int], Mapping[str, object]] | None
    ) = None
    reference_reader: Callable[[str, str], Mapping[str, object]] | None = None
    narrative_writer: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None
    basis_message_id: str | None = None
    recovery_kind: str | None = None
    retry_action_receipt: ActionReceipt | None = None

    def current_catalog(
        self, catalog_request_id: str = "static-fixture"
    ) -> tuple[ModelMechanicsTool, ...]:
        """Discover the current MCP surface, retaining static fixtures for adapter tests."""

        if self.catalog_supplier is not None:
            return self.catalog_supplier(catalog_request_id)
        return self.catalog


SessionProvider = Callable[[SessionProviderInput], Iterable[ProviderEvent]]
NarrationStarted = Callable[[str, str, Callable[[], None]], None]
NarrationFinished = Callable[[str, str], None]
ProviderWorkStarted = Callable[[str, str, Callable[[], None]], None]
ProviderWorkFinished = Callable[[str, str], None]


@dataclass(frozen=True)
class SessionRunResult:
    canonical: tuple[CanonicalEvent, ...]
    receipts: tuple[ActionReceipt | AwaitingChoiceReceipt, ...] = ()
    interruption: NarrationInterruption | None = None


@dataclass(frozen=True)
class SessionStopResult:
    interruption: NarrationInterruption
    cancellation_count: int


@dataclass
class _ActiveRun:
    campaign_id: str
    attempt_id: str
    control: ProviderControl = field(default_factory=ProviderControl)
    basis_message_id: str | None = None
    _projection_lock: threading.Lock = field(default_factory=threading.Lock)
    _provisional: list[dict[str, object]] = field(default_factory=list)
    _segment_ordinal: int = 0

    def emit_provisional(self, kind: SessionMessageKind, text: str) -> None:
        if kind not in {SessionMessageKind.SAFE_THINK, SessionMessageKind.TALK}:
            raise TypeError("Only safe prose can enter a provisional session projection")
        with self._projection_lock:
            used = sum(len(str(item.get("text", ""))) for item in self._provisional)
            if used + len(text) > _MAX_PROVISIONAL_SESSION_CHARS:
                raise CommandRejected("Agent session provisional output is too large")
            kind_value = kind.value
            if self._provisional and self._provisional[-1].get("kind") == kind_value:
                current = self._provisional[-1]
                current["text"] = str(current.get("text", "")) + text
                return
            self._provisional.append(
                {
                    "kind": kind_value,
                    "text": text,
                    "collapsed_by_default": kind is SessionMessageKind.SAFE_THINK,
                    "provisional": True,
                    "attempt_id": self.attempt_id,
                }
            )

    def provisional_projection(self) -> tuple[SafePayload, ...]:
        with self._projection_lock:
            return tuple(cast(SafePayload, dict(item)) for item in self._provisional)

    def clear_provisional(self) -> None:
        with self._projection_lock:
            self._provisional.clear()

    def provisional_kind(self) -> str | None:
        with self._projection_lock:
            if not self._provisional:
                return None
            return str(self._provisional[-1].get("kind"))

    def take_completed_provisional(
        self,
    ) -> tuple[tuple[str, SessionMessageKind, SafePayload], ...]:
        with self._projection_lock:
            completed: list[tuple[str, SessionMessageKind, SafePayload]] = []
            for projection in self._provisional:
                text = str(projection.get("text", ""))
                if not text:
                    continue
                self._segment_ordinal += 1
                kind = SessionMessageKind(str(projection["kind"]))
                completed.append(
                    (
                        f"{self.attempt_id}-segment-{self._segment_ordinal}",
                        kind,
                        {"text": text},
                    )
                )
            self._provisional.clear()
            return tuple(completed)


class CampaignSessionLoop:
    """One ordinary loop for both Builder and DM roles using F2 APIs verbatim."""

    def __init__(
        self,
        owner: AuthorityOwner,
        *,
        on_narration_started: NarrationStarted | None = None,
        on_narration_finished: NarrationFinished | None = None,
        on_provider_work_started: ProviderWorkStarted | None = None,
        on_provider_work_finished: ProviderWorkFinished | None = None,
    ) -> None:
        self._owner = owner
        self._mechanics = StatelessMcpMechanics(owner)
        self._on_narration_started = on_narration_started or (
            lambda _campaign_id, _attempt_id, _cancel: None
        )
        self._on_narration_finished = on_narration_finished or (
            lambda _campaign_id, _attempt_id: None
        )
        self._on_provider_work_started = on_provider_work_started or (
            lambda _campaign_id, _attempt_id, _cancel: None
        )
        self._on_provider_work_finished = on_provider_work_finished or (
            lambda _campaign_id, _attempt_id: None
        )
        self._active_lock = threading.Lock()
        self._active: dict[str, _ActiveRun] = {}
        self._pending: set[str] = set()
        self._preparing: set[str] = set()
        self._stop_requested: set[str] = set()
        self._last_interruption: dict[str, NarrationInterruption] = {}

    def run(self, request: SessionRunRequest, provider: SessionProvider) -> SessionRunResult:
        with self._active_lock:
            if request.session_id in self._active or request.session_id in self._pending:
                raise CommandRejected("Agent session already has active provider work")
            self._pending.add(request.session_id)
        try:
            with self._owner.agent_session_work(request.session_id) as context:
                return self._run_with_session_work(request, provider, context)
        finally:
            with self._active_lock:
                self._pending.discard(request.session_id)
                self._stop_requested.discard(request.session_id)

    def run_builder(
        self,
        request: SessionRunRequest,
        provider: SessionProvider,
        persist: Callable[[], Iterable[object] | None],
    ) -> tuple[object, ...]:
        """Run persistent Builder chat under Stop/shutdown without gameplay authority."""

        with self._active_lock:
            if request.session_id in self._active or request.session_id in self._pending:
                raise CommandRejected("Agent session already has active provider work")
            self._pending.add(request.session_id)
        try:
            with self._owner.builder_session_work(request.session_id) as context:
                return self._run_builder_with_session_work(request, provider, persist, context)
        finally:
            with self._active_lock:
                self._pending.discard(request.session_id)
                self._stop_requested.discard(request.session_id)

    def run_compaction(self, session_id: str, attempt_id: str, provider: SessionProvider) -> None:
        """Run manual context maintenance under the ordinary session lock, without a game action."""
        with self._active_lock:
            if session_id in self._active or session_id in self._pending:
                raise CommandRejected("Agent session already has active provider work")
            self._pending.add(session_id)
        role = self._owner.session_context(session_id).session.role
        work = (self._owner.builder_session_work if role is WriterRole.BUILDER
                else self._owner.agent_session_work)
        started = False
        try:
            with work(session_id) as context:
                control = ProviderControl()
                active = _ActiveRun(session_id, attempt_id, control)
                with self._active_lock:
                    if session_id in self._stop_requested:
                        raise CommandRejected("Session was stopped before compaction")
                    self._active[session_id] = active
                    self._pending.discard(session_id)
                self._owner.append_agent_message(
                    session_id=session_id, message_id=f"compact-request-{attempt_id}",
                    kind=SessionMessageKind.EVIDENCE,
                    content={"kind": "context_compaction_requested", "attempt_id": attempt_id},
                )
                status = "interrupted"
                try:
                    self._on_provider_work_started(session_id, attempt_id, control.cancel)
                    started = True
                    provider_input = SessionProviderInput(context.session, context, (), control)
                    for event in provider(provider_input):
                        if control.cancelled:
                            raise CommandRejected("Context compaction was cancelled")
                        if (event.kind is not ProviderEventKind.EVIDENCE
                                or event.message_id is None or event.evidence is None
                                or event.evidence.get("kind") != "working_context_checkpoint"):
                            raise CommandRejected("Compaction cannot perform gameplay or narrate")
                        self._owner.append_agent_message(
                            session_id=session_id, message_id=event.message_id,
                            kind=SessionMessageKind.EVIDENCE, content=event.evidence,
                        )
                    status = "completed"
                finally:
                    self._owner.append_agent_message(
                        session_id=session_id, message_id=f"compact-terminal-{attempt_id}",
                        kind=SessionMessageKind.EVIDENCE,
                        content={"kind": "context_compaction_terminal",
                                 "attempt_id": attempt_id, "status": status},
                    )
        finally:
            if started:
                self._on_provider_work_finished(session_id, attempt_id)
            with self._active_lock:
                self._active.pop(session_id, None)
                self._pending.discard(session_id)
                self._stop_requested.discard(session_id)

    def _run_builder_with_session_work(
        self,
        request: SessionRunRequest,
        provider: SessionProvider,
        persist: Callable[[], Iterable[object] | None],
        context: SessionContext,
    ) -> tuple[object, ...]:
        if request.input_kind is SessionInputKind.BUILDER_START:
            if request.user_text or not any(
                message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") in {
                    "builder_launch_context", "import_repair_context"
                }
                for message in context.messages
            ):
                raise CommandRejected("Builder start requires its saved launch context")
            if any(
                message.kind is SessionMessageKind.USER
                or message.content.get("kind") == "builder_start"
                for message in context.messages
            ):
                raise CommandRejected(
                    "Builder chat has already started; send a message to continue"
                )
            input_kind = SessionMessageKind.EVIDENCE
            input_content: SafePayload = {
                "kind": "builder_start", "attempt_id": request.attempt_id
            }
        elif request.input_kind is SessionInputKind.USER:
            input_kind = SessionMessageKind.USER
            input_content = {"text": request.user_text, "attempt_id": request.attempt_id}
            if request.builder_question_answers is not None:
                input_content = {
                    **builder_question_answer_content(
                        context, request.builder_question_answers, request.user_text,
                    ),
                    "attempt_id": request.attempt_id,
                }
        else:
            raise CommandRejected("Builder input kind is unsupported")
        self._owner.append_agent_message(
            session_id=request.session_id,
            message_id=request.message_id,
            kind=input_kind,
            content=input_content,
        )
        provider_input = SessionProviderInput(
            session=context.session,
            history=self._owner.session_context(request.session_id),
            catalog=(),
            control=ProviderControl(),
        )
        active = _ActiveRun(
            # Builder provider work is attached to the durable session, even
            # when that session belongs to a campaign.  The frontend streams,
            # stops, reconnects, and heartbeats by session ID.
            campaign_id=context.session.session_id,
            attempt_id=request.attempt_id,
            control=provider_input.control,
            basis_message_id=request.message_id,
        )
        with self._active_lock:
            if request.session_id in self._stop_requested:
                raise CommandRejected("Agent session was stopped before provider admission")
            self._active[request.session_id] = active
            self._pending.discard(request.session_id)
        provider_work_started = False
        appended: list[object] = []
        provisional_response_id: str | None = None
        native_requirements: dict[str, SafePayload] = {}

        def flush_provisional() -> None:
            for message_id, kind, content in active.take_completed_provisional():
                if kind is SessionMessageKind.TALK and provisional_response_id:
                    content = {**content, "provider_response_id": provisional_response_id}
                    if provisional_response_id in native_requirements:
                        content = {**content, "native_continuation":
                                   native_requirements[provisional_response_id]}
                appended.append(
                    self._owner.append_agent_message(
                        session_id=request.session_id,
                        message_id=message_id,
                        kind=kind,
                        content=content,
                    )
                )

        def record_terminal(status: str, *, reason_code: str | None = None) -> None:
            self._owner.finish_private_provider_attempt(
                request.session_id, request.attempt_id, completed=status == "completed",
            )
            self._owner.append_agent_message(
                session_id=request.session_id,
                message_id=f"builder-terminal-{request.attempt_id}",
                kind=SessionMessageKind.EVIDENCE,
                content={
                    "kind": "builder_attempt_terminal",
                    "attempt_id": request.attempt_id,
                    "status": status,
                    "activity_timing": self._finish_activity(
                        request.session_id, request.attempt_id,
                        "failed" if status == "interrupted" else status,
                    ),
                    **({"reason_code": reason_code} if reason_code else {}),
                },
            )

        try:
            self._on_provider_work_started(
                active.campaign_id, active.attempt_id, active.control.cancel
            )
            provider_work_started = True
            stream = active.control.admit(lambda: provider(provider_input))
            for source_order, event in enumerate(stream):
                active.control.progress.observe()
                if active.control.cancelled and event.kind not in {
                    ProviderEventKind.EVIDENCE,
                    ProviderEventKind.PRIVATE_REASONING,
                    ProviderEventKind.PRIVATE_CONTINUATION,
                }:
                    # Stop fences prose; completed tool receipts and their native
                    # continuation still describe effects that already happened.
                    continue
                if event.kind in {
                    ProviderEventKind.PRIVATE_REASONING,
                    ProviderEventKind.PRIVATE_CONTINUATION,
                }:
                    if event.text:
                        self._owner.append_private_provider_record(ProviderPrivateRecord(
                            request.session_id, request.attempt_id,
                            event.response_id or request.attempt_id, request.turn_id,
                            request.message_id, source_order, provider_input.session.provider_id,
                            provider_input.session.model_id,
                            "continuation" if event.kind is ProviderEventKind.PRIVATE_CONTINUATION
                            else "thinking", event.text,
                        ))
                        if (event.kind is ProviderEventKind.PRIVATE_CONTINUATION
                                and event.response_id):
                            native = json.loads(event.text)
                            native_requirements[event.response_id] = {
                                "provider_id": provider_input.session.provider_id,
                                "model_id": provider_input.session.model_id,
                                "anthropic_required": "anthropic_content" in native,
                                "chat_completion_required": "chat_completion_metadata" in native,
                                "reasoning_required": "reasoning_content" in native,
                            }
                    continue
                if event.kind is ProviderEventKind.SAFE_THINK:
                    if event.text is None:
                        raise TypeError("Safe THINK event requires text")
                    if active.provisional_kind() not in {
                        None,
                        SessionMessageKind.SAFE_THINK.value,
                    }:
                        flush_provisional()
                    active.emit_provisional(SessionMessageKind.SAFE_THINK, event.text)
                    continue
                if event.kind is ProviderEventKind.TALK:
                    if event.text is None:
                        raise TypeError("TALK event requires text")
                    if provisional_response_id != event.response_id:
                        flush_provisional()
                    provisional_response_id = event.response_id
                    if active.provisional_kind() not in {
                        None,
                        SessionMessageKind.TALK.value,
                    }:
                        flush_provisional()
                    active.emit_provisional(SessionMessageKind.TALK, event.text)
                    continue
                if event.kind is ProviderEventKind.PROSE_COMPLETE:
                    flush_provisional()
                    continue
                if event.kind is ProviderEventKind.EVIDENCE:
                    if event.evidence is None or event.message_id is None:
                        raise TypeError("Builder evidence requires payload and message identity")
                    if not active.control.cancelled:
                        flush_provisional()
                    response_id = event.evidence.get("provider_response_id")
                    requirement = native_requirements.get(str(response_id))
                    appended.append(
                        self._owner.append_agent_message(
                            session_id=request.session_id,
                            message_id=event.message_id,
                            kind=SessionMessageKind.EVIDENCE,
                            content={**event.evidence,
                                     **({"native_continuation": requirement}
                                        if requirement else {}),
                                     "activity": {
                                "attempt_id": request.attempt_id,
                                "basis_message_id": request.message_id,
                                "source_order": source_order,
                            }},
                        )
                    )
                    continue
                raise CommandRejected("Builder provider cannot emit gameplay session events")
            if active.control.cancelled:
                raise CommandRejected("Agent session provider work was cancelled")
            try:
                flush_provisional()
                persisted = persist()
                if persisted is not None:
                    appended.extend(tuple(persisted))
            except RuntimeError as error:
                if active.control.cancelled and str(error) == (
                    "Application authority owner is closed"
                ):
                    raise CommandRejected(
                        "Agent session provider work was cancelled during owner shutdown"
                    ) from error
                raise
            record_terminal("completed")
            return tuple(appended)
        except BaseException as error:
            # Shutdown may already have closed storage. The saved input without a
            # terminal record still identifies interrupted work after restart.
            with suppress(Exception):
                loop_error = error if isinstance(error, AgentLoopError) else error.__cause__
                record_terminal(
                    "stopped" if active.control.cancelled else "interrupted",
                    reason_code=(
                        loop_error.reason
                        if isinstance(loop_error, AgentLoopError) and not active.control.cancelled
                        else None
                    ),
                )
            raise
        finally:
            if provider_work_started:
                self._on_provider_work_finished(active.campaign_id, active.attempt_id)
            with self._active_lock:
                self._active.pop(request.session_id, None)

    def builder_activity(
        self, session_id: str, *, attempt_id: str | None = None
    ) -> SafePayload | None:
        """Project live work or its saved outcome, including an unfinished run after restart."""

        with self._active_lock:
            active = self._active.get(session_id)
            pending = session_id in self._pending
        # Read after the live snapshot: terminal evidence is saved before live work is removed.
        if attempt_id is None and active is not None:
            attempt_id = active.attempt_id
        basis = self._owner.session_activity_basis(session_id, attempt_id)
        is_builder = basis.session.role is WriterRole.BUILDER
        if not is_builder and not basis.has_compaction:
            return None
        if attempt_id is None:
            attempt_id = (
                str(basis.accepted.content["attempt_id"]) if basis.accepted is not None else None
            )
        if attempt_id is None:
            return None
        terminal = None if basis.terminal is None else basis.terminal.content
        if terminal is not None:
            return {"attempt_id": attempt_id, "status": terminal["status"]}
        if active is not None and active.attempt_id == attempt_id:
            return {"attempt_id": attempt_id, "status": "active",
                    "status_hint": active.control.status_hint}

        if basis.accepted is None:
            return {"attempt_id": attempt_id, "status": "unavailable"}
        return {
            "attempt_id": attempt_id,
            "status": "active" if pending else "interrupted",
        }

    def activity_progress(self, session_id: str) -> SafePayload | None:
        """Read live owner observations without fetching chat or private provider content."""
        with self._active_lock:
            active = self._active.get(session_id)
        if active is None:
            return None
        return cast(SafePayload, {
            "attempt_id": active.attempt_id,
            "basis_message_id": active.basis_message_id,
            **active.control.progress.snapshot(),
        })

    def _finish_activity(self, session_id: str, attempt_id: str, status: str) -> SafePayload:
        """Freeze timing once, including when Stop and provider unwind share a terminal."""
        with self._active_lock:
            active = self._active.get(session_id)
        if active is None or active.attempt_id != attempt_id:
            return {}
        return cast(SafePayload, active.control.progress.finish(status))

    def set_builder_chat_archived(self, session_id: str, *, archived: bool) -> BuilderChatRecord:
        """Exclude admitted and queued provider work while changing the saved chat lifecycle."""
        with self._active_lock:
            if session_id in self._active or session_id in self._pending:
                raise CommandRejected("Finish or stop this Builder's work before archiving it",
                                      code="builder_chat_work_in_progress")
            return self._owner.set_builder_chat_archived(session_id, archived=archived)

    def context_status_hint(self, session_id: str) -> str | None:
        """Return private-data-free progress for either kind of session."""
        with self._active_lock:
            active = self._active.get(session_id)
        return active.control.status_hint if active is not None else None

    def provisional_session_projection(self, session_id: str) -> tuple[SafePayload, ...]:
        """Return detached, player-safe provisional Builder prose for polling clients."""

        with self._active_lock:
            active = self._active.get(session_id)
        return () if active is None else active.provisional_projection()

    def _run_with_session_work(
        self,
        request: SessionRunRequest,
        provider: SessionProvider,
        context: SessionContext,
    ) -> SessionRunResult:
        campaign_id = context.session.campaign_id
        if campaign_id is None:
            raise CommandRejected("DM sessions require campaign authority")
        if request.campaign_ending_choice is not None:
            if request.input_kind is not SessionInputKind.USER or request.redirect_interruption_id:
                raise CommandRejected("Ending choices require ordinary idle player input")
            request = replace(
                request,
                ooc_text=self._owner.campaign_ending_choice_text(
                    request.session_id, request.message_id, request.campaign_ending_choice,
                ),
            )
        terminal = next(
            (message for message in context.messages
             if message.content.get("attempt_id") == request.attempt_id and (
                 (message.content.get("kind") == "gm_turn_activity"
                  and message.content.get("activity") == "turn_completed")
                 or (message.content.get("kind") == "session_attempt_terminal"
                     and message.content.get("status") == "interrupted"))),
            None,
        )
        if terminal is not None:
            raise IdempotencyConflict("Agent session attempt identity is already terminal")
        snapshot = self._owner.snapshot(campaign_id)
        initial_high_water = snapshot.events[-1].sequence if snapshot.events else 0
        application_event = request.input_kind in {
            SessionInputKind.CAMPAIGN_START, SessionInputKind.PLAYER_ACTION_RESPONSE,
        }
        if application_event and (request.user_text or request.ooc_text):
            raise CommandRejected("Application response requests do not contain player speech")
        if request.input_kind is SessionInputKind.PLAYER_ACTION_RESPONSE:
            if not request.player_action_id:
                raise CommandRejected("A response request needs its saved player action")
            basis = self._owner.player_action_response_basis(
                request.session_id, request.player_action_id, automatic=request.automatic_response,
            )
            if basis["turn_id"] != request.turn_id:
                raise CommandRejected("The saved player action belongs to another turn")
        elif request.player_action_id is not None or request.automatic_response:
            raise CommandRejected("Player action response context requires its application event")
        redirection = None
        if request.redirect_interruption_id is not None:
            if application_event:
                raise CommandRejected("Only player input can redirect stopped narration")
            interrupted = self._owner.narration_interruption(
                campaign_id, request.redirect_interruption_id,
            )
            previous_input = resolve_interruption_basis(context, interrupted)
            redirection = self._owner.redirect_narration(
                session_id=request.session_id,
                message_id=request.message_id,
                interruption_id=request.redirect_interruption_id,
                content={
                    "text": request.user_text,
                    **({"ooc_text": request.ooc_text} if request.ooc_text is not None else {}),
                    "canonical_start_sequence": previous_input.content.get(
                        "canonical_start_sequence", initial_high_water,
                    ),
                    "input_id": request.attempt_id,
                    "attempt_id": request.attempt_id,
                    "turn_id": request.turn_id,
                    "input_kind": request.input_kind.value,
                    "redirect_interruption_id": request.redirect_interruption_id,
                    "redirected_from_message_id": previous_input.message_id,
                },
            )
        else:
            self._append_dm_input(request, application_event, initial_high_water)
        provider_input = SessionProviderInput(
            session=context.session,
            history=self._owner.session_context(request.session_id),
            catalog=(),
            control=ProviderControl(),
            basis_message_id=request.message_id,
            recovery_kind=(
                "post_mechanics_narration"
                if redirection is not None and redirection.last_action_id is not None else None
            ),
            retry_action_receipt=(
                self._owner.saved_action_receipt(campaign_id, redirection.last_action_id)
                if redirection is not None and redirection.last_action_id is not None else None
            ),
            catalog_supplier=lambda catalog_request_id: self._mechanics.list_tools(
                campaign_id,
                purpose="provider_inference",
                discovery_id=catalog_request_id,
                session_id=request.session_id,
            ),
            knowledge_searcher=lambda query, sources, limit, offset: (
                self._mechanics.search_knowledge(
                    campaign_id,
                    query=query,
                    sources=sources,
                    limit=limit,
                    offset=offset,
                    session_id=request.session_id,
                )
            ),
            reference_reader=lambda source, path: self._mechanics.read_reference(
                campaign_id,
                source=source,
                path=path,
                session_id=request.session_id,
            ),
            narrative_writer=lambda parameters: self._mechanics.commit_campaign_file(
                campaign_id,
                session_id=request.session_id,
                basis_message_id=request.message_id,
                parameters=parameters,
                turn_id=request.turn_id,
            ),
        )
        return self._run_dm_provider(
            request, provider, provider_input, initial_high_water, redirection,
        )

    def _append_dm_input(
        self, request: SessionRunRequest, application_event: bool, initial_high_water: int,
    ) -> None:
        """Persist the exact ordinary player input or application response request."""

        self._owner.append_agent_message(
            session_id=request.session_id,
            message_id=request.message_id,
            kind=(
                SessionMessageKind.EVIDENCE
                if application_event
                else SessionMessageKind.USER
            ),
            content=(
                {
                    "kind": request.input_kind.value,
                    "event": request.input_kind.value,
                    **({
                        "player_action_id": request.player_action_id,
                        "player_action_ids": [record["action_id"] for record in
                            self._owner.player_action_context(
                                self._owner.session_context(request.session_id).session.campaign_id)],
                        "automatic_response": request.automatic_response,
                    } if request.input_kind is SessionInputKind.PLAYER_ACTION_RESPONSE else {}),
                    "canonical_start_sequence": initial_high_water,
                    "input_id": request.attempt_id,
                    "attempt_id": request.attempt_id,
                    "turn_id": request.turn_id,
                    "input_kind": request.input_kind.value,
                }
                if application_event
                else {
                    "text": request.user_text,
                    **({"ooc_text": request.ooc_text} if request.ooc_text is not None else {}),
                    **({"campaign_ending_choice": request.campaign_ending_choice}
                       if request.campaign_ending_choice is not None else {}),
                    "canonical_start_sequence": initial_high_water,
                    "input_id": request.attempt_id,
                    "attempt_id": request.attempt_id,
                    "turn_id": request.turn_id,
                    "input_kind": request.input_kind.value,
                }
            ),
        )

    def _run_dm_provider(
        self, request: SessionRunRequest, provider: SessionProvider,
        provider_input: SessionProviderInput, initial_high_water: int,
        redirection: NarrationInterruption | None,
    ) -> SessionRunResult:
        """Run an admitted GM input against its live narration and saved recovery basis."""

        campaign_id = provider_input.session.campaign_id
        assert campaign_id is not None
        active = _ActiveRun(
            campaign_id=campaign_id,
            attempt_id=request.attempt_id,
            control=provider_input.control,
            basis_message_id=request.message_id,
        )
        with self._active_lock:
            if request.session_id in self._stop_requested:
                raise CommandRejected("Agent session was stopped before provider admission")
            self._active[request.session_id] = active
            self._pending.discard(request.session_id)
        receipts: list[ActionReceipt | AwaitingChoiceReceipt] = []
        narration_started = False
        try:
            self._owner.start_narration_attempt(
                campaign_id=active.campaign_id,
                attempt_id=active.attempt_id,
                turn_id=request.turn_id,
                cancel=active.control.cancel,
                last_action_id=(
                    redirection.last_action_id if redirection else request.player_action_id
                ),
                interruption_id=request.redirect_interruption_id,
            )
            narration_started = True
            self._on_narration_started(active.campaign_id, active.attempt_id, active.control.cancel)
            with self._active_lock:
                stopped_before_provider = request.session_id in self._stop_requested
            if stopped_before_provider or active.control.cancelled:
                raise CommandRejected("Agent session was stopped before provider admission")
            stream = active.control.admit(lambda: provider(provider_input))
            for source_order, event in enumerate(stream):
                active.control.progress.observe()
                if active.control.cancelled:
                    raise CommandRejected("Agent session provider work was cancelled")
                if event.kind in {
                    ProviderEventKind.PRIVATE_REASONING,
                    ProviderEventKind.PRIVATE_CONTINUATION,
                }:
                    if event.text:
                        self._owner.append_private_provider_record(ProviderPrivateRecord(
                            request.session_id, request.attempt_id,
                            event.response_id or request.attempt_id, request.turn_id,
                            request.message_id, source_order, provider_input.session.provider_id,
                            provider_input.session.model_id,
                            "continuation" if event.kind is ProviderEventKind.PRIVATE_CONTINUATION
                            else "thinking", event.text,
                        ))
                    continue
                if event.kind is ProviderEventKind.SAFE_THINK:
                    if event.text is None:
                        raise TypeError("Safe THINK event requires text")
                    self._owner.emit_narration_token(
                        active.campaign_id,
                        active.attempt_id,
                        ProseChannel.SAFE_THINK,
                        event.text,
                    )
                elif event.kind is ProviderEventKind.TALK:
                    if event.text is None:
                        raise TypeError("TALK event requires text")
                    self._owner.emit_narration_token(
                        active.campaign_id,
                        active.attempt_id,
                        ProseChannel.TALK,
                        event.text,
                        response_blocks=event.response_blocks,
                    )
                elif event.kind is ProviderEventKind.ACTION:
                    if event.action_command is None:
                        raise TypeError("Action event requires an ActionCommand")
                    self._owner.flush_narration(active.campaign_id, active.attempt_id)
                    try:
                        receipt = self._mechanics.execute_action(event.action_command)
                    except MechanicsRejected as error:
                        if event.action_feedback is None:
                            raise
                        event.action_feedback.rejection = _mechanics_rejection_feedback(error)
                        continue
                    receipts.append(receipt)
                    if isinstance(receipt, AwaitingChoiceReceipt):
                        self._owner.complete_narration_attempt(
                            active.campaign_id, active.attempt_id
                        )
                        self._record_gm_turn_activity(
                            session_id=request.session_id,
                            attempt_id=request.attempt_id,
                            basis_message_id=request.message_id,
                            turn_id=request.turn_id,
                            opening=request.input_kind is SessionInputKind.CAMPAIGN_START,
                        )
                        canonical = self._owner.canonical_replay(
                            active.campaign_id, acknowledged_sequence=initial_high_water
                        ).canonical
                        self._owner.append_session_canonical_references(
                            request.session_id, canonical
                        )
                        return SessionRunResult(
                            canonical=canonical,
                            receipts=tuple(receipts),
                        )
                    self._owner.bind_narration_action(
                        active.campaign_id, active.attempt_id, receipt.action_id
                    )
                    self._record_mechanics_activity(
                        request.session_id, request.attempt_id, request.message_id,
                        source_order, receipt.action_id,
                    )
                elif event.kind is ProviderEventKind.EVIDENCE:
                    if event.evidence is None or event.message_id is None:
                        raise TypeError("Evidence event requires payload and message identity")
                    self._owner.append_agent_message(
                        session_id=request.session_id,
                        message_id=event.message_id,
                        kind=SessionMessageKind.EVIDENCE,
                        content={**event.evidence, "activity": {
                            "attempt_id": request.attempt_id,
                            "basis_message_id": request.message_id,
                            "source_order": source_order,
                        }},
                    )
                else:  # pragma: no cover - closed enum defensive boundary
                    raise TypeError("Unknown provider event")
            if active.control.cancelled:
                raise CommandRejected("Agent session provider work was cancelled")
            self._owner.complete_narration_attempt(active.campaign_id, active.attempt_id)
            self._record_gm_turn_activity(
                session_id=request.session_id,
                attempt_id=request.attempt_id,
                basis_message_id=request.message_id,
                turn_id=request.turn_id,
                opening=request.input_kind is SessionInputKind.CAMPAIGN_START,
            )
            canonical = self._owner.canonical_replay(
                active.campaign_id, acknowledged_sequence=initial_high_water
            ).canonical
            self._owner.append_session_canonical_references(request.session_id, canonical)
            return SessionRunResult(canonical=canonical, receipts=tuple(receipts))
        except BaseException as error:
            if not narration_started:
                raise
            was_cancelled = active.control.cancelled
            try:
                interruption = self._owner.interrupt_narration(
                    active.campaign_id, active.attempt_id
                )
            except RuntimeError as owner_error:
                if was_cancelled and str(owner_error) == "Application authority owner is closed":
                    # Owner shutdown already terminalized this attempt before
                    # entering ``closing``. Do not begin a second authority
                    # operation while close waits for this admitted worker.
                    raise CommandRejected(
                        "Agent session provider work was cancelled during owner shutdown"
                    ) from owner_error
                raise
            with self._active_lock:
                self._last_interruption[request.session_id] = interruption
            self._record_interrupted_terminal(
                request.session_id,
                interruption,
                failure_category=self._failure_category(
                    error,
                    cancelled=was_cancelled,
                ),
            )
            # Committed results belong in chat history even when only narration failed.
            try:
                canonical = self._owner.canonical_replay(
                    active.campaign_id, acknowledged_sequence=initial_high_water
                ).canonical
                self._owner.append_session_canonical_references(request.session_id, canonical)
            except RuntimeError as error:
                if was_cancelled and str(error) == "Application authority owner is closed":
                    raise CommandRejected(
                        "Agent session provider work was cancelled during owner shutdown"
                    ) from error
                raise
            if was_cancelled:
                return SessionRunResult(
                    canonical=canonical,
                    receipts=tuple(receipts),
                    interruption=interruption,
                )
            raise
        finally:
            if narration_started:
                self._on_narration_finished(active.campaign_id, active.attempt_id)
            with self._active_lock:
                self._active.pop(request.session_id, None)

    @staticmethod
    def _failure_category(error: BaseException, *, cancelled: bool) -> str:
        """Return one safe stable category for a recoverable terminal attempt."""

        if cancelled:
            return "user_cancelled"
        if isinstance(error, ProviderError):
            return error.category.value
        if isinstance(error, CommandRejected):
            return error.code
        return "local_adapter_error"

    def _record_interrupted_terminal(
        self,
        session_id: str,
        interruption: NarrationInterruption,
        *,
        failure_category: str,
    ) -> None:
        """Persist the recoverable terminal before Stop can close its owning session."""

        self._owner.finish_private_provider_attempt(
            session_id, interruption.failed_attempt_id, completed=False,
        )

        basis = next(
            (
                message
                for message in self._owner.session_context(session_id).messages
                if message.content.get("attempt_id") == interruption.failed_attempt_id
                and message.kind in {SessionMessageKind.USER, SessionMessageKind.EVIDENCE}
                and message.content.get("kind") != "session_attempt_terminal"
            ),
            None,
        )
        if basis is None:
            raise CommandRejected("Interrupted session input identity is unavailable")
        context = self._owner.session_context(session_id)
        recovery_kind = _recovery_kind(context, basis, interruption)
        self._owner.append_agent_message(
            session_id=session_id,
            message_id=f"terminal-{interruption.failed_attempt_id}",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "session_attempt_terminal",
                "input_id": basis.content.get("input_id", interruption.failed_attempt_id),
                "attempt_id": interruption.failed_attempt_id,
                "message_id": basis.message_id,
                "turn_id": interruption.turn_id,
                "status": "interrupted",
                "failure_category": failure_category,
                "activity_timing": self._finish_activity(
                    session_id, interruption.failed_attempt_id,
                    "stopped" if failure_category == "user_cancelled" else "failed",
                ),
                "recovery_kind": recovery_kind,
                "committed_action_id": interruption.last_action_id,
                "retryable": interruption.retryable,
            },
        )

    def steer(self, session_id: str, text: str, *, message_id: str) -> None:
        active = self._active_run(session_id)

        def persist_steering() -> None:
            for segment_id, kind, content in active.take_completed_provisional():
                self._owner.append_live_steering_message(
                    session_id=session_id,
                    message_id=segment_id,
                    kind=kind,
                    content=content,
                )
            self._owner.append_live_steering_message(
                session_id=session_id,
                message_id=message_id,
                kind=SessionMessageKind.STEERING,
                content={"text": text},
            )

        active.control.admit_steering(
            text,
            persist_steering,
        )

    def retry(
        self,
        session_id: str,
        *,
        retry_id: str,
        narrator: Callable[[RetryNarrationInput, NarrationStream], None],
        provider_factory: Callable[[str], SessionProvider] | None = None,
        interruption_id: str | None = None,
        cancel: Callable[[], None] = lambda: None,
        control: ProviderControl | None = None,
    ) -> ManualRetryResult:
        with self._active_lock:
            if session_id in self._active or session_id in self._pending:
                raise CommandRejected("Agent session already has active provider work")
            self._pending.add(session_id)
        campaign_id: str | None = None
        narration_started = False
        active_control = control or ProviderControl()
        basis_message = None
        retry_failure_cancelled: bool | None = None
        initial_high_water = 0
        try:
            with self._owner.agent_session_work(session_id) as context:
                if interruption_id is None:
                    with self._active_lock:
                        interruption = self._last_interruption.get(session_id)
                    if interruption is None:
                        raise CommandRejected("Agent session has no retryable interruption")
                    interruption_id = interruption.interruption_id
                campaign_id = context.session.campaign_id
                if campaign_id is None:  # agent_session_work enforces this invariant
                    raise CommandRejected("DM sessions require campaign authority")
                snapshot = self._owner.snapshot(campaign_id)
                initial_high_water = snapshot.events[-1].sequence if snapshot.events else 0
                interruption = self._owner.narration_interruption(
                    campaign_id,
                    interruption_id,
                )
                basis_message = resolve_interruption_basis(context, interruption)
                active = _ActiveRun(
                    campaign_id=campaign_id,
                    attempt_id=retry_id,
                    control=active_control,
                    basis_message_id=basis_message.message_id,
                )

                def admit_attempt() -> None:
                    nonlocal narration_started
                    with self._active_lock:
                        if session_id in self._stop_requested:
                            stopped = True
                        else:
                            stopped = False
                            self._active[session_id] = active
                            self._pending.discard(session_id)
                            self._on_narration_started(campaign_id, retry_id, cancel)
                            narration_started = True
                    if stopped:
                        interruption = self._owner.interrupt_narration(campaign_id, retry_id)
                        with self._active_lock:
                            self._last_interruption[session_id] = interruption
                        raise CommandRejected(
                            "Agent session was stopped before Retry provider admission"
                        )

                def run_retry(
                    retry_input: RetryNarrationInput,
                    stream: NarrationStream,
                ) -> None:
                    if (basis_message is not None
                        and basis_message.kind is SessionMessageKind.EVIDENCE
                        and basis_message.content.get("kind") == "player_action_response"):
                        self._owner.append_agent_message(
                            session_id=session_id,
                            message_id=f"player-response-retry-{retry_id}",
                            kind=SessionMessageKind.EVIDENCE,
                            content={"kind": "player_action_response_retry",
                                     "basis_message_id": basis_message.message_id,
                                     "attempt_id": retry_id},
                        )
                    recovery_kind = _terminal_recovery_kind(
                        context,
                        retry_input.failed_attempt_id,
                    )
                    campaign_file_checkpoint = recovery_kind == "campaign_file_checkpoint"
                    uncommitted_player_action = (
                        retry_input.action_receipt is None
                        and basis_message is not None
                        and basis_message.kind is SessionMessageKind.USER
                    )
                    post_mechanics_recovery = (
                        recovery_kind == "post_mechanics_narration"
                        and retry_input.action_receipt is not None
                    )
                    if (
                        not uncommitted_player_action
                        and not campaign_file_checkpoint
                        and not post_mechanics_recovery
                    ):
                        narrator(retry_input, stream)
                        return
                    if provider_factory is None:
                        if campaign_file_checkpoint:
                            raise CommandRejected(
                                "Campaign File checkpoint Retry requires the tool-capable provider"
                            )
                        narrator(retry_input, stream)
                        return
                    assert basis_message is not None
                    if campaign_file_checkpoint:
                        retry_messages = list(context.messages)
                    else:
                        basis_index = context.messages.index(basis_message)
                        retry_messages = list(context.messages[: basis_index + 1])
                        retry_messages.extend(
                            message
                            for message in context.messages[basis_index + 1 :]
                            if message.kind is SessionMessageKind.EVIDENCE
                            and (
                                message.content.get("kind") == "gm_system_contract"
                                or (
                                    message.content.get("kind")
                                    in {"provider_tool_calls", "provider_tool_results"}
                                    and message.content.get("basis_message_id")
                                    == basis_message.message_id
                                )
                            )
                        )
                    retry_context = SessionContext(
                        session=context.session,
                        messages=tuple(retry_messages),
                    )
                    provider_input = SessionProviderInput(
                        session=context.session,
                        history=retry_context,
                        catalog=(),
                        control=active_control,
                        basis_message_id=basis_message.message_id,
                        recovery_kind=recovery_kind,
                        retry_action_receipt=retry_input.action_receipt,
                        catalog_supplier=lambda catalog_request_id: self._mechanics.list_tools(
                            campaign_id,
                            purpose="provider_inference",
                            discovery_id=catalog_request_id,
                            session_id=session_id,
                        ),
                        knowledge_searcher=lambda query, sources, limit, offset: (
                            self._mechanics.search_knowledge(
                                campaign_id,
                                query=query,
                                sources=sources,
                                limit=limit,
                                offset=offset,
                                session_id=session_id,
                            )
                        ),
                        reference_reader=lambda source, path: self._mechanics.read_reference(
                            campaign_id,
                            source=source,
                            path=path,
                            session_id=session_id,
                        ),
                        narrative_writer=lambda parameters: self._mechanics.commit_campaign_file(
                            campaign_id,
                            session_id=session_id,
                            basis_message_id=basis_message.message_id,
                            parameters=parameters,
                            turn_id=retry_input.turn_id,
                        ),
                    )
                    provider = provider_factory(retry_input.turn_id)
                    provider_events = active_control.admit(lambda: provider(provider_input))
                    for source_order, event in enumerate(provider_events):
                        active_control.progress.observe()
                        if active_control.cancelled:
                            raise CommandRejected("Agent session provider work was cancelled")
                        if event.kind in {
                            ProviderEventKind.PRIVATE_REASONING,
                            ProviderEventKind.PRIVATE_CONTINUATION,
                        }:
                            if event.text:
                                self._owner.append_private_provider_record(ProviderPrivateRecord(
                                    session_id, retry_id, event.response_id or retry_id,
                                    retry_input.turn_id, basis_message.message_id, source_order,
                                    provider_input.session.provider_id,
                                    provider_input.session.model_id,
                                    "continuation"
                                    if event.kind is ProviderEventKind.PRIVATE_CONTINUATION
                                    else "thinking", event.text,
                                ))
                            continue
                        if event.kind is ProviderEventKind.SAFE_THINK:
                            if event.text is None:
                                raise TypeError("Safe THINK event requires text")
                            stream.emit(ProseChannel.SAFE_THINK, event.text)
                            continue
                        if event.kind is ProviderEventKind.TALK:
                            if event.text is None:
                                raise TypeError("TALK event requires text")
                            stream.emit(
                                ProseChannel.TALK,
                                event.text,
                                response_blocks=event.response_blocks,
                            )
                            continue
                        if event.kind is ProviderEventKind.ACTION:
                            if event.action_command is None:
                                raise TypeError("Action event requires an ActionCommand")
                            self._owner.flush_narration(campaign_id, retry_id)
                            try:
                                receipt = self._mechanics.execute_action(event.action_command)
                            except MechanicsRejected as error:
                                if event.action_feedback is None:
                                    raise
                                event.action_feedback.rejection = _mechanics_rejection_feedback(
                                    error
                                )
                                continue
                            if isinstance(receipt, AwaitingChoiceReceipt):
                                # A retry can succeed by presenting an uncommitted player choice.
                                stream.emit(ProseChannel.TALK, receipt.choice.prompt)
                                return
                            self._owner.bind_narration_action(
                                campaign_id,
                                retry_id,
                                receipt.action_id,
                            )
                            self._record_mechanics_activity(
                                session_id, retry_id, basis_message.message_id,
                                source_order, receipt.action_id,
                            )
                            continue
                        if event.kind is ProviderEventKind.EVIDENCE:
                            if event.evidence is None or event.message_id is None:
                                raise TypeError(
                                    "Evidence event requires payload and message identity"
                                )
                            self._owner.append_agent_message(
                                session_id=session_id,
                                message_id=event.message_id,
                                kind=SessionMessageKind.EVIDENCE,
                                content={**event.evidence, "activity": {
                                    "attempt_id": retry_id,
                                    "basis_message_id": basis_message.message_id,
                                    "source_order": source_order,
                                }},
                            )
                            continue
                        raise TypeError("Unknown provider event")

                def observe_retry_failure(
                    retry_input: RetryNarrationInput,
                    stream: NarrationStream,
                ) -> None:
                    """Capture whether cancellation caused the Retry failure before cleanup."""

                    nonlocal retry_failure_cancelled
                    try:
                        run_retry(retry_input, stream)
                    except BaseException:
                        retry_failure_cancelled = active_control.cancelled
                        raise

                result = self._owner.manual_retry(
                    campaign_id=campaign_id,
                    interruption_id=interruption_id,
                    retry_id=retry_id,
                    narrator=observe_retry_failure,
                    cancel=cancel,
                    on_attempt_started=admit_attempt,
                )
                assert basis_message is not None
                if not result.replayed:
                    self._record_gm_turn_activity(
                        session_id=session_id,
                        attempt_id=retry_id,
                        basis_message_id=basis_message.message_id,
                        turn_id=interruption.turn_id,
                        opening=basis_message.content.get("kind") == "campaign_start",
                    )
                # Retry can commit a first mechanic as well as narration. Retain the
                # whole new canonical interval, as the failed-retry path already does.
                canonical = (
                    result.canonical if result.replayed else self._owner.canonical_replay(
                        campaign_id, acknowledged_sequence=initial_high_water
                    ).canonical
                )
                self._owner.append_session_canonical_references(session_id, canonical)
                return result
        except BaseException as error:
            with self._active_lock:
                stop_was_requested = session_id in self._stop_requested
            was_cancelled = (
                retry_failure_cancelled
                if retry_failure_cancelled is not None else stop_was_requested
            )
            failure_category = self._failure_category(error, cancelled=was_cancelled)
            terminal_status = "stopped" if failure_category == "user_cancelled" else "failed"
            timing = self._finish_activity(session_id, retry_id, terminal_status)
            if not timing:
                # Stop can win before this Retry is registered as the active run.
                timing = cast(SafePayload, active_control.progress.finish(terminal_status))
            if campaign_id is not None:
                with suppress(Exception):
                    self._owner.finish_private_provider_attempt(
                        session_id, retry_id, completed=False,
                    )
                interruption = self._owner.retryable_interruption(
                    campaign_id, failed_attempt_id=retry_id
                )
                if interruption is not None:
                    with self._active_lock:
                        self._last_interruption[session_id] = interruption
                    if basis_message is not None:
                        self._owner.append_agent_message(
                            session_id=session_id,
                            message_id=f"terminal-{retry_id}",
                            kind=SessionMessageKind.EVIDENCE,
                            content={
                                "kind": "session_attempt_terminal",
                                "input_id": basis_message.content.get("input_id"),
                                "attempt_id": retry_id,
                                "message_id": basis_message.message_id,
                                "turn_id": interruption.turn_id,
                                "status": "interrupted",
                                "failure_category": failure_category,
                                "activity_timing": timing,
                                "recovery_kind": _recovery_kind(
                                    self._owner.session_context(session_id),
                                    basis_message,
                                    interruption,
                                ),
                                "committed_action_id": interruption.last_action_id,
                                "retryable": interruption.retryable,
                            },
                        )
                    canonical = self._owner.canonical_replay(
                        campaign_id, acknowledged_sequence=initial_high_water
                    ).canonical
                    self._owner.append_session_canonical_references(session_id, canonical)
            raise
        finally:
            if narration_started and campaign_id is not None:
                self._on_narration_finished(campaign_id, retry_id)
            with self._active_lock:
                self._active.pop(session_id, None)
                self._pending.discard(session_id)
                self._stop_requested.discard(session_id)

    def disconnect(self, session_id: str, *, now: float) -> float:
        active = self._active_run(session_id)
        return self._owner.disconnect_narration(active.campaign_id, active.attempt_id, now=now)

    def reconnect(
        self,
        session_id: str,
        *,
        now: float,
        acknowledged_sequence: int,
    ) -> CanonicalReplay:
        active = self._active_run(session_id)
        return self._owner.reconnect_narration(
            active.campaign_id,
            active.attempt_id,
            now=now,
            acknowledged_sequence=acknowledged_sequence,
        )

    def expire(self, session_id: str, *, now: float) -> SessionStopResult:
        active = self._active_run(session_id)
        interruption = self._owner.expire_narration(active.campaign_id, active.attempt_id, now=now)
        with self._active_lock:
            self._last_interruption[session_id] = interruption
        return SessionStopResult(
            interruption=interruption,
            cancellation_count=active.control.cancellation_count,
        )

    def _active_run(self, session_id: str) -> _ActiveRun:
        with self._active_lock:
            active = self._active.get(session_id)
        if active is None:
            raise CommandRejected("Agent session has no active provider work")
        return active

    @contextmanager
    def provider_request_work(self, session_id: str) -> Iterator[None]:
        """Reserve selection during request preparation through terminal one-shot consumption."""
        with self._active_lock:
            if (
                session_id in self._preparing or session_id in self._pending
                or session_id in self._active
            ):
                raise CommandRejected("Agent session already has pending or active provider work")
            self._preparing.add(session_id)
        try:
            yield
        finally:
            with self._active_lock:
                self._preparing.discard(session_id)

    def assert_idle(self, session_id: str) -> None:
        """Reject configuration changes while provider work is pending or active."""

        with self._active_lock:
            if (
                session_id in self._active or session_id in self._pending
                or session_id in self._preparing
            ):
                raise CommandRejected(
                    "Agent session settings cannot change during pending or active provider work"
                )

    def stop(self, session_id: str) -> SessionStopResult:
        """Stop one active provider call via the exact F2 interruption path."""

        with self._active_lock:
            active = self._active.get(session_id)
        if active is None:
            raise CommandRejected("Agent session has no active provider work")
        interruption = self._owner.interrupt_narration(active.campaign_id, active.attempt_id)
        with self._active_lock:
            self._last_interruption[session_id] = interruption
        self._record_interrupted_terminal(
            session_id,
            interruption,
            failure_category="user_cancelled",
        )
        self._owner.stop_agent_session(session_id)
        return SessionStopResult(
            interruption=interruption,
            cancellation_count=active.control.cancellation_count,
        )

    def stop_agent_session(self, session_id: str) -> SessionRecord:
        """Linearize Stop Session against pending and active provider work."""

        with self._active_lock:
            active = self._active.get(session_id)
            if active is None and session_id in self._pending:
                self._stop_requested.add(session_id)
            elif active is not None:
                self._stop_requested.add(session_id)
                active.control.cancel()
        if active is not None:
            try:
                interruption = self._owner.interrupt_narration(
                    active.campaign_id, active.attempt_id
                )
            except CommandRejected:
                # Provider completion may win this race; stopping the now-idle
                # session remains valid.
                interruption = None
            if interruption is not None:
                with self._active_lock:
                    self._last_interruption[session_id] = interruption
                self._record_interrupted_terminal(
                    session_id,
                    interruption,
                    failure_category="user_cancelled",
                )
        return self._owner.stop_agent_session(session_id)

    def _record_gm_turn_activity(
        self,
        *,
        session_id: str,
        attempt_id: str,
        basis_message_id: str,
        turn_id: str,
        opening: bool,
    ) -> None:
        """Persist one spoiler-safe card for a completed GM turn."""

        self._owner.finish_private_provider_attempt(session_id, attempt_id, completed=True)

        if self._owner.session_context(session_id).session.role is not WriterRole.DM:
            return
        self._owner.append_agent_message(
            session_id=session_id,
            message_id=f"gm-activity-{attempt_id}",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "gm_turn_activity",
                "activity": "campaign_opened" if opening else "turn_completed",
                "activity_timing": self._finish_activity(session_id, attempt_id, "completed"),
                "attempt_id": attempt_id,
                "basis_message_id": basis_message_id,
                "turn_id": turn_id,
            },
        )

    def _record_mechanics_activity(
        self, session_id: str, attempt_id: str, basis_message_id: str,
        source_order: int, action_id: str,
    ) -> None:
        """Retain execution-time correlation without changing canonical mechanics events."""
        self._owner.append_agent_message(
            session_id=session_id,
            message_id=f"mechanics-activity-{attempt_id}-{action_id}",
            kind=SessionMessageKind.EVIDENCE,
            content={"kind": "mechanics_activity", "action_id": action_id, "activity": {
                "attempt_id": attempt_id, "basis_message_id": basis_message_id,
                "source_order": source_order,
            }},
        )

