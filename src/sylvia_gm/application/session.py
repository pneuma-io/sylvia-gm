"""Application service for ordinary Builder/DM sessions."""

from __future__ import annotations

import threading
import uuid

from sylvia_gm.domain.authority import CanonicalEvent, SafePayload, WriterRole, owned_payload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import (
    BuilderChatRecord,
    BuilderEvidence,
    ChildEvidence,
    EvidenceSourceKind,
    SessionContext,
    SessionHistoryPage,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
)
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore


class SessionApplication:
    """Session operations backed by the one authoritative SQLite store."""

    def __init__(self, store: SQLiteAuthorityStore) -> None:
        self._store = store
        self._lanes_lock = threading.Lock()
        self._lanes: dict[str, threading.RLock] = {}

    def create_agent_session(
        self,
        *,
        campaign_id: str,
        role: WriterRole,
        provider_id: str,
        model_id: str,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        selected_skill: str | None = None,
    ) -> SessionRecord:
        record = SessionRecord(
            session_id=session_id or f"session-{uuid.uuid4().hex}",
            campaign_id=campaign_id,
            role=role,
            provider_id=provider_id,
            model_id=model_id,
            status=SessionStatus.ACTIVE,
            parent_session_id=parent_session_id,
            selected_skill=selected_skill,
        )
        with self._lane(record.session_id):
            return self._store.create_agent_session(record)

    def create_builder_chat(
        self,
        *,
        provider_id: str,
        model_id: str,
        display_name: str = "New chat",
        session_id: str | None = None,
    ) -> BuilderChatRecord:
        """Create one durable general Builder conversation outside campaigns."""

        identity = session_id or f"session-builder-{uuid.uuid4().hex}"
        with self._lane(identity):
            return self._store.create_builder_chat(
                session_id=identity,
                provider_id=provider_id,
                model_id=model_id,
                display_name=display_name,
            )

    def builder_chat_index(self) -> tuple[BuilderChatRecord, ...]:
        return self._store.builder_chat_index()

    def rename_builder_chat(
        self, session_id: str, display_name: str, *, automatic: bool = False
    ) -> BuilderChatRecord:
        with self._lane(session_id):
            return self._store.rename_builder_chat(session_id, display_name, automatic=automatic)

    def delete_builder_chat(self, session_id: str) -> BuilderChatRecord:
        with self._lane(session_id):
            return self._store.delete_builder_chat(session_id)

    def update_reasoning(
        self, session_id: str, *, effort: str | None, once: str | None,
    ) -> SessionRecord:
        """Save reasoning preferences under the same lane as model and history changes."""
        with self._lane(session_id):
            return self._store.update_agent_session_reasoning(session_id, effort=effort, once=once)

    def update_model(
        self,
        session_id: str,
        *,
        provider_id: str,
        model_id: str,
    ) -> SessionRecord:
        """Persist a model switch without changing session history or lifecycle."""

        with self._lane(session_id):
            return self._store.update_agent_session_model(
                session_id,
                provider_id=provider_id,
                model_id=model_id,
            )

    def create_child(
        self,
        *,
        parent_session_id: str,
        provider_id: str,
        model_id: str,
        selected_skill: str,
        scope: tuple[str, ...],
        session_id: str | None = None,
    ) -> SessionRecord:
        parent = self._store.session_context(parent_session_id).session
        record = SessionRecord(
            session_id=session_id or f"session-{uuid.uuid4()}",
            campaign_id=parent.campaign_id,
            role=WriterRole.BUILDER,
            provider_id=provider_id,
            model_id=model_id,
            status=SessionStatus.ACTIVE,
            parent_session_id=parent_session_id,
            selected_skill=selected_skill,
            scope=scope,
        )
        with self._lane(parent_session_id):
            return self._store.create_builder_child_session(record)

    def return_child_evidence(
        self,
        *,
        child_session_id: str,
        evidence_id: str,
        content_sha256: str,
        safe_summary: str,
        claims: tuple[str, ...],
    ) -> ChildEvidence:
        child = self._store.session_context(child_session_id).session
        if child.parent_session_id is None:
            raise CommandRejected("Only child sessions can return child evidence")
        evidence = ChildEvidence(
            evidence_id=evidence_id,
            child_session_id=child_session_id,
            parent_session_id=child.parent_session_id,
            content_sha256=content_sha256,
            safe_summary=safe_summary,
            claims=claims,
        )
        with self._lane(child.parent_session_id):
            captured, _completed = self._store.return_builder_child_evidence(evidence)
            return captured

    def context(self, session_id: str) -> SessionContext:
        return self._store.session_context(session_id)

    def history_page(
        self, session_id: str, *, before_sequence: int | None = None, limit: int = 50,
    ) -> SessionHistoryPage:
        return self._store.session_history_page(
            session_id, before_sequence=before_sequence, limit=limit,
        )

    def update_status(self, session_id: str, status: SessionStatus) -> SessionRecord:
        with self._lane(session_id):
            return self._store.update_agent_session_status(session_id, status)

    def record_evidence(
        self,
        *,
        session_id: str,
        evidence_id: str,
        source_kind: str,
        content_sha256: str,
        source_locator: str | None,
        safe_excerpt: str,
        claims: tuple[str, ...],
    ) -> BuilderEvidence:
        evidence = BuilderEvidence(
            evidence_id=evidence_id,
            source_kind=EvidenceSourceKind(source_kind),
            content_sha256=content_sha256,
            source_locator=source_locator,
            safe_excerpt=safe_excerpt,
            claims=claims,
        )
        with self._lane(session_id):
            context = self._store.session_context(session_id)
            if context.session.role is not WriterRole.BUILDER:
                raise CommandRejected("Only Builder sessions admit construction evidence")
            if context.session.status is not SessionStatus.ACTIVE:
                raise CommandRejected("Builder session is not active")
            self._store.append_agent_message(
                session_id=session_id,
                message_id=evidence_id,
                kind=SessionMessageKind.EVIDENCE,
                content=evidence.payload(),
            )
        return evidence

    def require_human_review(
        self,
        *,
        session_id: str,
        review_id: str,
        unsupported_capabilities: tuple[str, ...],
        contradictions: tuple[str, ...],
    ) -> SessionRecord:
        if not unsupported_capabilities and not contradictions:
            raise ValueError(
                "Human review requires an explicit unsupported or contradictory reason"
            )
        content = {
            "outcome": "human_review_required",
            "unsupported_capabilities": list(unsupported_capabilities),
            "contradictions": list(contradictions),
        }
        with self._lane(session_id):
            return self._store.require_agent_human_review(
                session_id=session_id,
                review_id=review_id,
                content=owned_payload(content),
            )

    def append_message(
        self,
        *,
        session_id: str,
        message_id: str,
        kind: SessionMessageKind,
        content: SafePayload,
    ) -> SessionMessage:
        with self._lane(session_id):
            return self._store.append_agent_message(
                session_id=session_id,
                message_id=message_id,
                kind=kind,
                content=content,
            )

    def append_canonical_references(
        self, session_id: str, events: tuple[CanonicalEvent, ...]
    ) -> tuple[SessionMessage, ...]:
        with self._lane(session_id):
            return self._store.append_canonical_references(session_id, events)

    def _lane(self, session_id: str) -> threading.RLock:
        with self._lanes_lock:
            return self._lanes.setdefault(session_id, threading.RLock())
