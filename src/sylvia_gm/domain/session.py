"""Rules-neutral ordinary Builder/DM session domain values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from sylvia_gm.domain.authority import (
    SafePayload,
    WriterRole,
    canonical_payload_is_safe,
    owned_payload,
    thaw_json_value,
    validate_authority_identity,
)
from sylvia_gm.domain.model_capability import ModelGuidanceDecision

BUILDER_SKILLS = (
    "BUILD RULESET",
    "BUILD CAMPAIGN SETTING",
    "BUILD CAMPAIGN PLAN",
)


class SessionStatus(StrEnum):
    """Lifecycle of one ordinary agent session."""

    ACTIVE = "active"
    STOPPED = "stopped"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    HANDOFF_CLOSED = "handoff_closed"


class PlanValidationOutcome(StrEnum):
    """Deterministic Campaign Plan validation result admitted by F4."""

    VALID = "valid"


class PlanAcceptanceStatus(StrEnum):
    """Only accepted plans become Campaign authority."""

    ACCEPTED = "accepted"


@dataclass(frozen=True)
class BuilderDMHandoff:
    """Stored result of one atomic existing-campaign Builder-to-DM transition."""

    handoff_id: str
    campaign_id: str
    builder_session_id: str
    dm_session_id: str
    package_lock_id: str
    package_candidate_id: str | None
    plan_id: str
    plan_revision: int
    plan_content_sha256: str
    expected_revision: int
    resulting_campaign_revision: int
    chronology_ordinal: int
    completed_turn_id: str
    model_guidance: ModelGuidanceDecision
    payload_sha256: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.handoff_id, "Builder-to-DM handoff identity"),
            (self.campaign_id, "Campaign identity"),
            (self.builder_session_id, "Builder session identity"),
            (self.dm_session_id, "DM session identity"),
            (self.package_lock_id, "Handoff package lock identity"),
            (self.plan_id, "Handoff Campaign Plan identity"),
            (self.completed_turn_id, "Handoff completed TURN identity"),
        ):
            validate_authority_identity(value, label)
        if self.package_candidate_id is not None:
            validate_authority_identity(
                self.package_candidate_id, "Handoff package candidate identity"
            )
        validate_authority_identity(
            self.plan_content_sha256,
            "Handoff Campaign Plan content SHA-256",
            sha256=True,
        )
        validate_authority_identity(
            self.payload_sha256,
            "Builder-to-DM handoff payload SHA-256",
            sha256=True,
        )
        if (
            self.plan_revision <= 0
            or self.expected_revision < 0
            or self.resulting_campaign_revision != self.expected_revision + 1
            or self.chronology_ordinal <= 0
        ):
            raise ValueError("Builder-to-DM handoff revision or chronology is invalid")


def builder_dm_handoff_payload_sha256(
    *,
    handoff_id: str,
    campaign_id: str,
    builder_session_id: str,
    dm_session_id: str,
    dm_provider_id: str,
    dm_model_id: str,
    expected_writer: WriterRole,
    expected_revision: int,
    expected_package_lock_id: str,
    expected_package_candidate_id: str | None,
    plan_id: str,
    expected_plan_revision: int,
    expected_plan_content_sha256: str,
    completed_turn_id: str,
    model_guidance_sha256: str,
) -> str:
    """Return the stable semantic identity of one handoff request."""

    encoded = json.dumps(
        {
            "handoff_id": handoff_id,
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "dm_session_id": dm_session_id,
            "dm_provider_id": dm_provider_id,
            "dm_model_id": dm_model_id,
            "expected_writer": expected_writer.value,
            "expected_revision": expected_revision,
            "expected_package_lock_id": expected_package_lock_id,
            "expected_package_candidate_id": expected_package_candidate_id,
            "plan_id": plan_id,
            "expected_plan_revision": expected_plan_revision,
            "expected_plan_content_sha256": expected_plan_content_sha256,
            "completed_turn_id": completed_turn_id,
            "model_guidance_sha256": model_guidance_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CampaignPlan:
    """One exact accepted Campaign Plan revision in the sole authority."""

    plan_id: str
    campaign_id: str
    package_lock_id: str
    source_session_id: str
    content_sha256: str
    content: SafePayload
    provenance_evidence_ids: tuple[str, ...]
    validation_outcome: PlanValidationOutcome
    acceptance_status: PlanAcceptanceStatus
    plan_revision: int
    resulting_campaign_revision: int
    payload_sha256: str

    def __post_init__(self) -> None:
        validate_authority_identity(self.plan_id, "Campaign Plan identity")
        validate_authority_identity(self.campaign_id, "Campaign identity")
        validate_authority_identity(self.package_lock_id, "Campaign Plan package lock identity")
        validate_authority_identity(self.source_session_id, "Campaign Plan source session")
        validate_authority_identity(
            self.content_sha256, "Campaign Plan content SHA-256", sha256=True
        )
        validate_authority_identity(
            self.payload_sha256, "Campaign Plan payload SHA-256", sha256=True
        )
        object.__setattr__(self, "content", owned_payload(self.content))
        object.__setattr__(
            self,
            "provenance_evidence_ids",
            tuple(self.provenance_evidence_ids),
        )
        if not self.provenance_evidence_ids or any(
            not isinstance(item, str) or not item for item in self.provenance_evidence_ids
        ):
            raise ValueError("Campaign Plan requires explicit provenance evidence")
        if len(set(self.provenance_evidence_ids)) != len(self.provenance_evidence_ids):
            raise ValueError("Campaign Plan provenance evidence must be unique")
        for evidence_id in self.provenance_evidence_ids:
            validate_authority_identity(evidence_id, "Campaign Plan provenance evidence")
        if self.plan_revision <= 0 or self.resulting_campaign_revision <= 0:
            raise ValueError("Accepted Campaign Plan revisions must be positive")


def campaign_plan_payload_sha256(
    *,
    plan_id: str,
    campaign_id: str,
    source_session_id: str,
    expected_revision: int,
    expected_package_lock_id: str,
    content_sha256: str,
    content: SafePayload,
    provenance_evidence_ids: tuple[str, ...],
    validation_outcome: PlanValidationOutcome,
    acceptance_status: PlanAcceptanceStatus,
) -> str:
    """Return the stable semantic admission identity for one Campaign Plan request."""

    payload = {
        "plan_id": plan_id,
        "campaign_id": campaign_id,
        "source_session_id": source_session_id,
        "expected_revision": expected_revision,
        "expected_package_lock_id": expected_package_lock_id,
        "content_sha256": content_sha256,
        "content": thaw_json_value(owned_payload(content)),
        "provenance_evidence_ids": list(provenance_evidence_ids),
        "validation_outcome": validation_outcome.value,
        "acceptance_status": acceptance_status.value,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SessionRecord:
    """Durable safe metadata for one Builder or DM session."""

    session_id: str
    campaign_id: str | None
    role: WriterRole
    provider_id: str
    model_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    parent_session_id: str | None = None
    selected_skill: str | None = None
    scope: tuple[str, ...] = ()
    reasoning_effort: str | None = None
    reasoning_effort_once: str | None = None

    def __post_init__(self) -> None:
        validate_authority_identity(self.session_id, "Agent session identity")
        for value in (self.reasoning_effort, self.reasoning_effort_once):
            if value is not None and (
                not isinstance(value, str) or not 1 <= len(value) <= 32
                or not value[0].isascii() or not value[0].islower()
                or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in value)
            ):
                raise ValueError("Reasoning preference must be a native setting name")
        if self.campaign_id is not None:
            validate_authority_identity(self.campaign_id, "Campaign identity")
        validate_authority_identity(self.provider_id, "Provider identity")
        validate_authority_identity(self.model_id, "Model identity")
        if self.parent_session_id is not None:
            validate_authority_identity(self.parent_session_id, "parent_session_id")
            if not self.scope or len(self.scope) > 8 or any(not item for item in self.scope):
                raise ValueError("Child sessions require one to eight explicit scope items")
        elif self.scope:
            raise ValueError("Only child sessions carry isolated scope")
        if self.role not in (WriterRole.BUILDER, WriterRole.DM):
            raise ValueError("Agent session role must be Builder or DM")
        if self.campaign_id is None:
            if (
                self.role is not WriterRole.BUILDER
                or self.parent_session_id is not None
                or self.selected_skill is not None
                or self.scope
            ):
                raise ValueError(
                    "Standalone sessions must be top-level general Builder conversations"
                )
            return
        if self.role is WriterRole.BUILDER:
            if self.selected_skill not in BUILDER_SKILLS:
                raise ValueError("Builder session must select exactly one construction skill")
        elif self.selected_skill is not None:
            raise ValueError("DM sessions do not load Builder construction skills")


def agent_session_payload_sha256(record: SessionRecord) -> str:
    """Bind every durable session field to one deterministic startup identity."""

    encoded = json.dumps(
        {
            "session_id": record.session_id,
            "campaign_id": record.campaign_id,
            "role": record.role.value,
            "provider_id": record.provider_id,
            "model_id": record.model_id,
            "status": record.status.value,
            "parent_session_id": record.parent_session_id,
            "selected_skill": record.selected_skill,
            "scope": list(record.scope),
            **({"reasoning_effort": record.reasoning_effort}
               if record.reasoning_effort is not None else {}),
            **({"reasoning_effort_once": record.reasoning_effort_once}
               if record.reasoning_effort_once is not None else {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EvidenceSourceKind(StrEnum):
    USER_PROVIDED = "user_provided"
    OPEN_SOURCE = "open_source"


@dataclass(frozen=True)
class BuilderEvidence:
    """Safe, bounded evidence; never authoritative campaign truth."""

    evidence_id: str
    source_kind: EvidenceSourceKind
    content_sha256: str
    source_locator: str | None
    safe_excerpt: str
    claims: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_authority_identity(self.evidence_id, "evidence_id")
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("content_sha256 must be a lowercase SHA-256 identity")
        if self.source_kind is EvidenceSourceKind.OPEN_SOURCE and not self.source_locator:
            raise ValueError("Open-source evidence requires a source locator")
        if self.source_kind is EvidenceSourceKind.USER_PROVIDED and self.source_locator:
            raise ValueError("User-provided evidence cannot claim an external locator")
        if not self.safe_excerpt or not self.claims or any(not claim for claim in self.claims):
            raise ValueError("Builder evidence requires a safe excerpt and explicit claims")

    def payload(self) -> SafePayload:
        return owned_payload(
            {
                "evidence_id": self.evidence_id,
                "source_kind": self.source_kind.value,
                "content_sha256": self.content_sha256,
                "source_locator": self.source_locator,
                "safe_excerpt": self.safe_excerpt,
                "claims": list(self.claims),
            }
        )


@dataclass(frozen=True)
class ChildEvidence:
    """Structured non-authoritative result returned from one bounded child."""

    evidence_id: str
    child_session_id: str
    parent_session_id: str
    content_sha256: str
    safe_summary: str
    claims: tuple[str, ...]
    authoritative: bool = False

    def __post_init__(self) -> None:
        validate_authority_identity(self.evidence_id, "evidence_id")
        validate_authority_identity(self.child_session_id, "child_session_id")
        validate_authority_identity(self.parent_session_id, "parent_session_id")
        if self.authoritative:
            raise ValueError("Child evidence is never authoritative")
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("content_sha256 must be a lowercase SHA-256 identity")
        if not self.safe_summary or not self.claims or any(not claim for claim in self.claims):
            raise ValueError("Child evidence requires a safe summary and explicit claims")

    def payload(self) -> SafePayload:
        return owned_payload(
            {
                "evidence_id": self.evidence_id,
                "child_session_id": self.child_session_id,
                "content_sha256": self.content_sha256,
                "safe_summary": self.safe_summary,
                "claims": list(self.claims),
                "authoritative": False,
            }
        )


class SessionMessageKind(StrEnum):
    """Only safe durable message kinds admitted to session history."""

    USER = "user"
    SAFE_THINK = "safe_think"
    TALK = "talk"
    STEERING = "steering"
    EVIDENCE = "evidence"
    CANONICAL_REFERENCE = "canonical_reference"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True)
class SessionMessage:
    session_id: str
    sequence: int
    message_id: str
    kind: SessionMessageKind
    content: SafePayload
    canonical_sequence: int | None = None

    def __post_init__(self) -> None:
        validate_authority_identity(self.session_id, "Agent session identity")
        validate_authority_identity(self.message_id, "Agent message identity")
        if self.sequence <= 0:
            raise ValueError("Agent message sequence must be positive")
        if not canonical_payload_is_safe(self.content):
            raise ValueError("Agent message content must be canonical-safe")
        object.__setattr__(self, "content", owned_payload(self.content))
        if (self.kind is SessionMessageKind.CANONICAL_REFERENCE) != (
            self.canonical_sequence is not None
        ):
            raise ValueError("Canonical reference identity is inconsistent")
        if self.canonical_sequence is not None and self.canonical_sequence <= 0:
            raise ValueError("Canonical reference sequence must be positive")


@dataclass(frozen=True)
class SessionContext:
    """Safe resumable context containing no raw/private reasoning."""

    session: SessionRecord
    messages: tuple[SessionMessage, ...] = ()


@dataclass(frozen=True)
class SessionActivityBasis:
    """Saved attempt facts used by the activity indicator, without chat history."""

    session: SessionRecord
    accepted: SessionMessage | None
    terminal: SessionMessage | None
    has_compaction: bool


@dataclass(frozen=True)
class SessionHistoryPage:
    """A bounded display page, distinct from the agent's complete saved context."""

    session: SessionRecord
    messages: tuple[SessionMessage, ...]
    next_before_sequence: int | None
    latest_sequence: int | None
    latest_user_sequence: int | None
    launch_messages: tuple[SessionMessage, ...] = ()
    latest_canonical_sequence: int | None = None
    act_markers: tuple[SafePayload, ...] = ()
    campaign_ending: SafePayload | None = None
    campaign_endings: tuple[SafePayload, ...] = ()


@dataclass(frozen=True)
class BuilderChatRecord:
    """Durable top-level Builder chat metadata independent of campaigns."""

    session: SessionRecord
    display_name: str
    created_ordinal: int

    def __post_init__(self) -> None:
        if self.session.campaign_id is not None or self.session.role is not WriterRole.BUILDER:
            raise ValueError("Builder chat metadata requires a standalone Builder session")
        if not self.display_name or self.display_name != self.display_name.strip():
            raise ValueError("Builder chat display name must be trimmed and nonempty")
        if len(self.display_name) > 120:
            raise ValueError("Builder chat display name is too long")
        if self.created_ordinal <= 0:
            raise ValueError("Builder chat creation ordinal must be positive")


def builder_chat_payload_sha256(record: BuilderChatRecord) -> str:
    """Bind one standalone Builder chat's durable metadata."""

    encoded = json.dumps(
        {
            "session": agent_session_payload_sha256(record.session),
            "display_name": record.display_name,
            "created_ordinal": record.created_ordinal,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
