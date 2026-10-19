"""Deterministic normalized SQLite persistence for the F1 authority spine."""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import msvcrt
import os
import secrets
import sqlite3
import stat
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import SpooledTemporaryFile
from types import MappingProxyType
from typing import Any, Never, cast
from uuid import uuid4

from sylvia_gm.application.player_actions import valid_player_action_consequence
from sylvia_gm.application.schema_admission import validate_json_value
from sylvia_gm.domain.authority import (
    ActionReceipt,
    ActionRecord,
    ActionStatus,
    AwaitingChoiceReceipt,
    CampaignConfirmation,
    CampaignConstructionBasis,
    CampaignConstructionBegun,
    CampaignConstructionCompleted,
    CampaignContinuityCommand,
    CampaignContinuityReceipt,
    CampaignContinuityView,
    CampaignDisplaySnapshot,
    CampaignExportResult,
    CampaignLifecycleResult,
    CampaignPurgeResult,
    CampaignRecord,
    CampaignRecoveryResult,
    CampaignSnapshot,
    CanonicalEvent,
    CanonicalEventType,
    ConsequenceRecord,
    EventVisibility,
    GuidedCampaignDraft,
    GuidedCreationSubflow,
    JsonValue,
    LifecycleState,
    MechanicOutcome,
    MechanicsChoiceAnswer,
    MechanicsChoiceOption,
    MechanicsChoiceRequest,
    MechanicsChoiceResolution,
    MutationRecord,
    NarrationInterruption,
    PackageDependency,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    Participant,
    ParticipantArtifact,
    ParticipantValidationStatus,
    PendingMechanicsAction,
    ProseChannel,
    SafePayload,
    StateValue,
    TurnStatus,
    WriterRole,
    canonical_payload_is_safe,
    lifecycle_writer_is_legal,
    mechanic_outcome_is_safe,
    owned_json_value,
    owned_payload,
    response_blocks_text,
    thaw_json_value,
    validate_authority_identity,
    validate_response_blocks,
)
from sylvia_gm.domain.errors import (
    CommandRejected,
    IdempotencyConflict,
    SchemaIdentityError,
    StaleRevision,
    WrongWriter,
)
from sylvia_gm.domain.mechanics_choices import (
    answer_payload,
    answers_from_payload,
    choice_from_payload,
    choice_payload,
)
from sylvia_gm.domain.model_capability import (
    ModelGuidanceDecision,
    model_guidance_payload,
    model_guidance_payload_sha256,
)
from sylvia_gm.domain.package_library import (
    ActivationResult,
    PackageBlobReadReceipt,
    PackageCandidate,
    PackageLockRecord,
    PackageManifest,
    PackageStagingRecord,
    ParticipantArtifactDeclaration,
    ReclamationResult,
    SurfaceDeclaration,
    ToolDeclaration,
    canonical_json_bytes,
)
from sylvia_gm.domain.provider_records import ProviderPrivateRecord
from sylvia_gm.domain.session import (
    BUILDER_SKILLS,
    BuilderChatRecord,
    BuilderDMHandoff,
    BuilderEvidence,
    CampaignPlan,
    ChildEvidence,
    PlanAcceptanceStatus,
    PlanValidationOutcome,
    SessionActivityBasis,
    SessionContext,
    SessionHistoryPage,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
    builder_dm_handoff_payload_sha256,
    campaign_plan_payload_sha256,
)
from sylvia_gm.infrastructure import (
    builder_workspace_migration,
    campaign_archive,
    private_records_archive,
    sqlite_builder_launch,
    sqlite_campaign,
    sqlite_continuity,
    sqlite_draft_history,
    sqlite_draft_lifecycle,
    sqlite_effort,
    sqlite_mechanics_choice,
    sqlite_packages,
    sqlite_reasoning,
    sqlite_recovery,
    sqlite_session,
    sqlite_state_ownership,
)
from sylvia_gm.infrastructure.owner_lock import DatabaseOwnerLock
from sylvia_gm.infrastructure.package_archive import (
    PACKAGE_CHUNK_LIMIT,
    AdmittedPackageArchive,
)
from sylvia_gm.infrastructure.participant_history import participant_data_history

__all__ = ("mechanic_outcome_is_safe",)

_SCHEMA_VERSION = 3
_SESSION_SCHEMA_VERSION = 4
_PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION = 5
_STATE_OWNERSHIP_SCHEMA_VERSION = 6
_PRE_BUILDER_CHAT_SCHEMA_VERSION = 7
_PRE_CONTINUITY_SCHEMA_VERSION = 8
_PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION = 9
_PRE_REASONING_SCHEMA_VERSION = 10
_PRE_EFFORT_SCHEMA_VERSION = 11
_CAMPAIGN_SCHEMA_VERSION = 12
_APPLICATION_OWNER_GATE = object()
_UNBOUND_CAMPAIGN_LOCK_HASH = "unbound"

_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008
_DRIVE_FIXED = 3
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_OFFLINE = 0x00001000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_UNSUPPORTED_BACKUP_ATTRIBUTES = (
    _FILE_ATTRIBUTE_REPARSE_POINT
    | _FILE_ATTRIBUTE_OFFLINE
    | _FILE_ATTRIBUTE_RECALL_ON_OPEN
    | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_INVALID_HANDLE_VALUE = int(ctypes.c_void_p(-1).value or -1)
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE_ACCESS = 0x00010000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_DISPOSITION_INFO = 4
_BACKUP_PAGES_PER_STEP = 16
_BACKUP_TEMP_ATTEMPTS = 128


@dataclass(frozen=True)
class _BackupEnvelope:
    target: Path
    parent_identity: tuple[int, int]
    target_identity: tuple[int, int] | None
    source_identity: tuple[int, int]
    volume_root: str


@dataclass(frozen=True)
class _ClaimedBackupTemporary:
    path: Path
    identity: tuple[int, int]
    parent_identity: tuple[int, int]
    volume_root: str
    attributes: int
    write_guard_handle: int


class _BackupFailure(CommandRejected):
    """Stable path-free failure with optional deterministic cleanup diagnostics."""

    def __init__(
        self,
        phase: str,
        reason: str,
        *,
        supplemental_codes: tuple[str, ...] = (),
    ) -> None:
        super().__init__("Database backup failed", code=f"backup_{phase}_{reason}")
        self.phase = phase
        self.reason = reason
        self.supplemental_codes = supplemental_codes

    def with_supplemental(self, *codes: str) -> _BackupFailure:
        return _BackupFailure(
            self.phase,
            self.reason,
            supplemental_codes=(*self.supplemental_codes, *codes),
        )


class _ExportFailure(CommandRejected):
    """Path-free portable-export failure with deterministic cleanup diagnostics."""

    def __init__(self, code: str, *, supplemental_codes: tuple[str, ...] = ()) -> None:
        super().__init__(f"Portable campaign export failed ({code})", code=code)
        self.supplemental_codes = supplemental_codes

    def with_supplemental(self, *codes: str) -> _ExportFailure:
        return _ExportFailure(
            self.code,
            supplemental_codes=tuple(dict.fromkeys((*self.supplemental_codes, *codes))),
        )


class _PublicationAcknowledgementLostError(Exception):
    """The namespace call raised, so publication may already be complete."""


class _VerifiedPackageBlobReadReceipt(PackageBlobReadReceipt):
    """Compatibility view naming the digest as the verified SHA-256."""

    @property
    def verified_sha256(self) -> str:
        return self.sha256


@dataclass(frozen=True)
class _ExportCapture:
    campaign_id: str
    captured_revision: int
    source_schema_version: int
    source_schema_identity_sha256: str
    migration_provenance: Mapping[str, object]
    rows_by_table: Mapping[str, tuple[Mapping[str, object], ...]]
    authority_bytes: bytes
    blob_sha256s: tuple[str, ...]
    private_bytes: bytes | None = field(default=None, repr=False)


def _new_package_read_spool() -> SpooledTemporaryFile[bytes]:
    """Create a package-read spool whose ownership is transferred to the caller."""

    return SpooledTemporaryFile(max_size=PACKAGE_CHUNK_LIMIT * 4, mode="w+b")


def _handoff_candidate_matches_lock(
    lock: PackageLockRecord, candidate: PackageCandidate | None
) -> bool:
    if lock.availability.value != "available":
        return False
    if not lock.package_lock.entries:
        return candidate is None
    return (
        candidate is not None
        and candidate.status.value == "validated"
        and candidate.package_lock == lock.package_lock
        and candidate.closure_sha256 == lock.closure_sha256
    )


_REQUIRED_TABLES = frozenset(
    {
        "schema_migrations",
        "packages",
        "package_dependencies",
        "campaigns",
        "campaign_package_memberships",
        "participants",
        "campaign_state",
        "turns",
        "actions",
        "canonical_events",
        "narration_interruptions",
        "mutations",
        "consequences",
        "idempotency_records",
        "campaign_revisions",
        "package_blobs",
        "package_blob_chunks",
        "package_manifests",
        "package_manifest_files",
        "package_exact_dependencies",
        "package_capabilities",
        "package_tools",
        "package_prompt_fragments",
        "package_ui_surfaces",
        "package_staging",
        "package_staging_chunks",
        "package_candidates",
        "package_candidate_memberships",
        "campaign_locks",
        "campaign_lock_memberships",
        "package_activations",
        "package_read_leases",
        "package_recovery_roots",
    }
)
_SESSION_TABLES = frozenset(
    {"agent_sessions", "agent_session_messages", "campaign_plans", "builder_dm_handoffs"}
)
_CAMPAIGN_TABLES = frozenset(
    {
        "package_participant_artifacts",
        "participant_adaptations",
        "participant_quarantines",
        "authority_schema_extensions",
        "campaign_operations",
        "campaign_constructions",
        "campaign_lifecycle_state",
        "campaign_lifecycle_transitions",
    }
)
_STATE_OWNERSHIP_TABLES = frozenset({"campaign_state_namespace_owners"})
_MECHANICS_CHOICE_TABLES = frozenset(
    {
        "pending_mechanics_actions",
        "pending_mechanics_action_aliases",
        "mechanics_choice_resolutions",
    }
)
_BUILDER_CHAT_TABLES = frozenset({"standalone_builder_chats", "standalone_builder_chat_messages"})
_CONTINUITY_TABLES = frozenset({"campaign_continuity_records"})

_MIGRATION_1 = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_name TEXT NOT NULL UNIQUE,
    sql_sha256 TEXT NOT NULL
);
CREATE TABLE packages (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    PRIMARY KEY (package_id, version, sha256)
);
CREATE TABLE package_dependencies (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    dependency_order INTEGER NOT NULL CHECK (dependency_order >= 0),
    dependency_id TEXT NOT NULL,
    dependency_version TEXT NOT NULL,
    dependency_sha256 TEXT NOT NULL,
    dependency_kind TEXT NOT NULL,
    PRIMARY KEY (package_id, version, sha256, dependency_order),
    UNIQUE (package_id, version, sha256, dependency_id),
    FOREIGN KEY (package_id, version, sha256)
        REFERENCES packages(package_id, version, sha256),
    FOREIGN KEY (dependency_id, dependency_version, dependency_sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE campaigns (
    campaign_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'archived', 'tombstoned')),
    writer TEXT NOT NULL CHECK (writer IN ('builder', 'dm', 'none')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    lock_hash TEXT NOT NULL
);
CREATE TABLE campaign_package_memberships (
    campaign_id TEXT NOT NULL,
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary', 'dependency')),
    load_order INTEGER NOT NULL CHECK (load_order >= 0),
    PRIMARY KEY (campaign_id, package_id, version, sha256),
    UNIQUE (campaign_id, load_order),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (package_id, version, sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE participants (
    campaign_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, participant_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TABLE campaign_state (
    campaign_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    state_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, namespace, state_key),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TABLE turns (
    campaign_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
    created_revision INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, turn_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TABLE actions (
    campaign_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    resulting_revision INTEGER NOT NULL,
    writer TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('committed', 'failed')),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, action_id),
    FOREIGN KEY (campaign_id, turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (package_id, package_version, package_sha256)
        REFERENCES packages(package_id, version, sha256),
    FOREIGN KEY (campaign_id, package_id, package_version, package_sha256)
        REFERENCES campaign_package_memberships(campaign_id, package_id, version, sha256)
);
CREATE TABLE canonical_events (
    campaign_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'action_declared', 'tool_call', 'tool_result', 'state_mutation',
        'consequence', 'action_committed', 'action_failed'
    )),
    turn_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    safe_payload_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, sequence),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE mutations (
    campaign_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    namespace TEXT NOT NULL,
    state_key TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, action_id, ordinal),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE consequences (
    campaign_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, action_id, ordinal),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE idempotency_records (
    campaign_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    action_id TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    resulting_revision INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, idempotency_key),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE campaign_revisions (
    campaign_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action_id TEXT,
    PRIMARY KEY (campaign_id, revision),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
"""
_MIGRATION_1_NAME = "f1_neutral_authority"
_MIGRATION_1_SHA256 = hashlib.sha256(_MIGRATION_1.strip().encode("utf-8")).hexdigest()

_MIGRATION_2 = """
ALTER TABLE canonical_events RENAME TO canonical_events_f1;
CREATE TABLE canonical_events (
    campaign_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'think', 'talk', 'action_declared', 'tool_call', 'tool_result',
        'state_mutation', 'consequence', 'action_committed', 'action_failed'
    )),
    turn_id TEXT NOT NULL,
    action_id TEXT,
    safe_payload_json TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('internal_safe', 'player_safe')),
    attempt_id TEXT,
    attempt_ordinal INTEGER,
    PRIMARY KEY (campaign_id, sequence),
    UNIQUE (campaign_id, attempt_id, attempt_ordinal),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id),
    CHECK (
        (
            event_type IN ('think', 'talk')
            AND action_id IS NULL
            AND visibility = 'player_safe'
            AND attempt_id IS NOT NULL
            AND attempt_ordinal IS NOT NULL
            AND attempt_ordinal >= 0
        ) OR (
            event_type NOT IN ('think', 'talk')
            AND action_id IS NOT NULL
            AND attempt_id IS NULL
            AND attempt_ordinal IS NULL
            AND (
                (event_type IN ('tool_call', 'tool_result') AND visibility = 'player_safe')
                OR
                (event_type NOT IN ('tool_call', 'tool_result')
                    AND visibility = 'internal_safe')
            )
        )
    )
);
INSERT INTO canonical_events (
    campaign_id, sequence, event_type, turn_id, action_id, safe_payload_json,
    visibility, attempt_id, attempt_ordinal
)
SELECT campaign_id, sequence, event_type, turn_id, action_id, safe_payload_json,
       CASE WHEN event_type IN ('tool_call', 'tool_result')
            THEN 'player_safe' ELSE 'internal_safe' END,
       NULL, NULL
FROM canonical_events_f1;
DROP TABLE canonical_events_f1;
CREATE TABLE narration_interruptions (
    campaign_id TEXT NOT NULL,
    interruption_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    last_action_id TEXT,
    failed_attempt_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('retryable', 'resolved')),
    resolving_retry_id TEXT,
    resolved_sequence INTEGER,
    PRIMARY KEY (campaign_id, interruption_id),
    UNIQUE (campaign_id, failed_attempt_id),
    UNIQUE (campaign_id, resolving_retry_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (campaign_id, last_action_id) REFERENCES actions(campaign_id, action_id),
    FOREIGN KEY (campaign_id, resolved_sequence)
        REFERENCES canonical_events(campaign_id, sequence),
    CHECK (
        (status = 'retryable' AND resolved_sequence IS NULL)
        OR
        (status = 'resolved' AND resolving_retry_id IS NOT NULL
            AND resolved_sequence IS NOT NULL)
    )
);
"""
_MIGRATION_2_NAME = "f2_provisional_event_protocol"
_MIGRATION_2_SHA256 = hashlib.sha256(_MIGRATION_2.strip().encode("utf-8")).hexdigest()
_MIGRATION_3 = sqlite_packages.MIGRATION_3_SQL
_MIGRATION_3_NAME = sqlite_packages.MIGRATION_3_NAME
_MIGRATION_3_SHA256 = hashlib.sha256(_MIGRATION_3.strip().encode("utf-8")).hexdigest()
_MIGRATION_4 = sqlite_session.MIGRATION_4_SQL
_MIGRATION_4_NAME = sqlite_session.MIGRATION_4_NAME
_MIGRATION_4_SHA256 = hashlib.sha256(_MIGRATION_4.strip().encode("utf-8")).hexdigest()
_PREDECESSOR_MIGRATION_5 = sqlite_campaign.PREDECESSOR_MIGRATION_5_SQL
_PREDECESSOR_MIGRATION_5_SHA256 = sqlite_campaign.PREDECESSOR_MIGRATION_5_SHA256
_MIGRATION_5 = sqlite_campaign.MIGRATION_5_SQL
_MIGRATION_5_NAME = sqlite_campaign.MIGRATION_5_NAME
_MIGRATION_5_SHA256 = hashlib.sha256(_MIGRATION_5.strip().encode("utf-8")).hexdigest()
_MIGRATION_6 = sqlite_state_ownership.MIGRATION_6_SQL
_MIGRATION_6_NAME = sqlite_state_ownership.MIGRATION_6_NAME
_MIGRATION_6_SHA256 = sqlite_state_ownership.MIGRATION_6_SHA256
_MIGRATION_7 = sqlite_mechanics_choice.MIGRATION_7_SQL
_MIGRATION_7_NAME = sqlite_mechanics_choice.MIGRATION_7_NAME
_MIGRATION_7_SHA256 = sqlite_mechanics_choice.MIGRATION_7_SHA256
_MIGRATION_8 = sqlite_session.MIGRATION_8_SQL
_MIGRATION_8_NAME = sqlite_session.MIGRATION_8_NAME
_MIGRATION_8_SHA256 = hashlib.sha256(_MIGRATION_8.strip().encode("utf-8")).hexdigest()
_MIGRATION_9_NAME = sqlite_continuity.MIGRATION_9_NAME
_MIGRATION_9_SHA256 = sqlite_continuity.MIGRATION_9_SHA256
_MIGRATION_10_NAME = sqlite_draft_lifecycle.MIGRATION_10_NAME
_MIGRATION_10_SHA256 = sqlite_draft_lifecycle.MIGRATION_10_SHA256
_CAMPAIGN_IDENTITY_DELTA = sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SQL
_CAMPAIGN_IDENTITY_DELTA_SHA256 = sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SHA256
_LIFECYCLE_EXTENSION_NAME = sqlite_campaign.LIFECYCLE_EXTENSION_NAME
_LIFECYCLE_EXTENSION_SHA256 = sqlite_campaign.LIFECYCLE_EXTENSION_SHA256
_CAMPAIGN_IDENTITY_EXTENSION_NAME = sqlite_campaign.CAMPAIGN_IDENTITY_EXTENSION_NAME
_PURGE_EXTENSION_NAME = sqlite_campaign.PURGE_EXTENSION_NAME
_PURGE_EXTENSION_SHA256 = sqlite_campaign.PURGE_EXTENSION_SHA256


def _expected_campaign_extension_rows(
    *, include_recovery: bool, include_purge: bool = False
) -> tuple[tuple[str, str, str, str], ...]:
    rows: tuple[tuple[str, str, str, str], ...] = (
        (
            _CAMPAIGN_IDENTITY_EXTENSION_NAME,
            _CAMPAIGN_IDENTITY_DELTA_SHA256,
            _MIGRATION_5_NAME,
            _PREDECESSOR_MIGRATION_5_SHA256,
        ),
        (
            _LIFECYCLE_EXTENSION_NAME,
            _LIFECYCLE_EXTENSION_SHA256,
            _CAMPAIGN_IDENTITY_EXTENSION_NAME,
            _CAMPAIGN_IDENTITY_DELTA_SHA256,
        ),
    )
    if include_recovery:
        rows += (
            (
                sqlite_recovery.RECOVERY_EXTENSION_NAME,
                sqlite_recovery.RECOVERY_EXTENSION_SHA256,
                _LIFECYCLE_EXTENSION_NAME,
                _LIFECYCLE_EXTENSION_SHA256,
            ),
        )
    if include_purge:
        rows += (
            (
                _PURGE_EXTENSION_NAME,
                _PURGE_EXTENSION_SHA256,
                sqlite_recovery.RECOVERY_EXTENSION_NAME,
                sqlite_recovery.RECOVERY_EXTENSION_SHA256,
            ),
        )
    return tuple(sorted(rows))


def _schema_identity(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(row[0]), " ".join(str(row[1]).split()).casefold())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _expected_schema_identity(
    *,
    include_sessions: bool,
    include_campaign: bool = False,
    include_lifecycle: bool = False,
    include_recovery: bool = False,
    include_purge: bool = False,
    include_state_ownership: bool = False,
    include_mechanics_choice: bool = False,
    include_builder_chats: bool = False,
    include_continuity: bool = False,
    include_draft_lifecycle: bool = False,
    include_reasoning: bool = False,
    include_effort: bool = False,
    campaign_sql: str | None = None,
) -> tuple[tuple[str, str], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_MIGRATION_1)
        connection.executescript(_MIGRATION_2)
        connection.executescript(_MIGRATION_3)
        if include_sessions:
            connection.executescript(_MIGRATION_4)
        if include_campaign:
            connection.executescript(_MIGRATION_5 if campaign_sql is None else campaign_sql)
        if include_lifecycle:
            sqlite_campaign.apply_lifecycle_extension(connection)
        if include_recovery:
            connection.execute(sqlite_recovery.RECOVERY_EXTENSION_SQL)
        if include_purge:
            sqlite_campaign.apply_purge_extension(connection)
        if include_state_ownership:
            connection.executescript(_MIGRATION_6)
        if include_mechanics_choice:
            connection.executescript(_MIGRATION_7)
        if include_builder_chats:
            connection.executescript(_MIGRATION_8)
        if include_continuity:
            connection.executescript(sqlite_continuity.MIGRATION_9_SQL)
        if include_draft_lifecycle:
            sqlite_draft_lifecycle.apply_migration(connection)
        if include_reasoning:
            sqlite_reasoning.apply_migration(connection)
        if include_effort:
            sqlite_effort.apply_migration(connection)
        return _schema_identity(connection)
    finally:
        connection.close()


_EXPECTED_SCHEMA_IDENTITY = _expected_schema_identity(include_sessions=False)
_EXPECTED_SESSION_SCHEMA_IDENTITY = _expected_schema_identity(include_sessions=True)
_EXPECTED_PROBE01_CAMPAIGN_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True, include_campaign=True
)
_EXPECTED_CAMPAIGN_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True, include_campaign=True, include_lifecycle=True
)
_EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
)
_EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_purge=True,
)
_EXPECTED_STATE_OWNERSHIP_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_state_ownership=True,
)
_EXPECTED_PURGE_STATE_OWNERSHIP_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_purge=True,
    include_state_ownership=True,
)
_EXPECTED_MECHANICS_CHOICE_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
)
_EXPECTED_BUILDER_CHAT_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
    include_builder_chats=True,
)
_EXPECTED_CONTINUITY_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
    include_builder_chats=True,
    include_continuity=True,
)
_EXPECTED_PURGE_MECHANICS_CHOICE_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_purge=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
)
_EXPECTED_PURGE_BUILDER_CHAT_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_purge=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
    include_builder_chats=True,
)
_EXPECTED_PURGE_CONTINUITY_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_purge=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
    include_builder_chats=True,
    include_continuity=True,
)
_EXPECTED_DRAFT_LIFECYCLE_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
    include_builder_chats=True,
    include_continuity=True,
    include_draft_lifecycle=True,
)
_EXPECTED_REASONING_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
    include_builder_chats=True,
    include_continuity=True,
    include_draft_lifecycle=True,
    include_reasoning=True,
)
_EXPECTED_EFFORT_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    include_lifecycle=True,
    include_recovery=True,
    include_state_ownership=True,
    include_mechanics_choice=True,
    include_builder_chats=True,
    include_continuity=True,
    include_draft_lifecycle=True,
    include_reasoning=True,
    include_effort=True,
)
_RECOVERY_EXTENSION_SCHEMA_IDENTITIES = frozenset(
    {_EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY, _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY}
)


def _apply_portable_recovery_extension(connection: sqlite3.Connection) -> None:
    sqlite_recovery.apply_recovery_extension(
        connection,
        predecessor_name=_LIFECYCLE_EXTENSION_NAME,
        predecessor_sha256=_LIFECYCLE_EXTENSION_SHA256,
    )


_EXPECTED_PREDECESSOR_CAMPAIGN_SCHEMA_IDENTITY = _expected_schema_identity(
    include_sessions=True,
    include_campaign=True,
    campaign_sql=_PREDECESSOR_MIGRATION_5,
)


def _dump(value: object) -> str:
    return json.dumps(
        thaw_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _lock_representation(package_lock: PackageLock) -> str:
    return _dump(
        [
            {
                "id": entry.package_id,
                "version": entry.version,
                "sha256": entry.sha256,
                "role": entry.role.value,
                "load_order": entry.load_order,
            }
            for entry in package_lock.entries
        ]
    )


def _operation_lock_payload(package_lock: PackageLock) -> list[dict[str, object]]:
    return [
        {
            "package_id": entry.package_id,
            "version": entry.version,
            "sha256": entry.sha256,
            "role": entry.role.value,
            "load_order": entry.load_order,
            "dependencies": [
                {
                    "package_id": dependency.package_id,
                    "version": dependency.version,
                    "sha256": dependency.sha256,
                    "kind": dependency.kind,
                    "namespace": dependency.namespace,
                }
                for dependency in entry.dependencies
            ],
            "capabilities": list(entry.capabilities),
        }
        for entry in package_lock.entries
    ]


def _stored_action_hash(
    *,
    campaign_id: str,
    action_id: str,
    writer: str,
    expected_revision: int,
    turn_id: str,
    package_id: str,
    package_version: str,
    package_sha256: str,
    tool_name: str,
    parameters: SafePayload,
) -> str:
    encoded = _dump(
        {
            "campaign_id": campaign_id,
            "action_id": action_id,
            "writer": writer,
            "expected_revision": expected_revision,
            "turn_id": turn_id,
            "package_id": package_id,
            "package_version": package_version,
            "package_sha256": package_sha256,
            "tool_name": tool_name,
            "parameters": parameters,
        }
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("Canonical JSON object members must be unique")
        result[key] = item
    return result


def _reject_nonstandard_constant(_constant: str) -> Never:
    raise ValueError("Canonical JSON constants must be finite and standard")


def _parse_canonical_json(value: str) -> object:
    parsed = json.loads(
        value,
        object_pairs_hook=_reject_duplicate_members,
        parse_constant=_reject_nonstandard_constant,
    )
    captured = owned_json_value(parsed)
    if value != _dump(captured):
        raise ValueError("Stored JSON is not the exact canonical representation")
    return captured


def _load_json(value: str) -> JsonValue:
    return cast(JsonValue, _parse_canonical_json(value))


def _load_payload(value: str) -> SafePayload:
    parsed = _parse_canonical_json(value)
    if not isinstance(parsed, Mapping):
        raise TypeError("Stored payload must be a canonical JSON object")
    return cast(SafePayload, parsed)


@dataclass(frozen=True)
class ActionBasis:
    campaign: CampaignRecord
    turn_status: TurnStatus
    state: Mapping[str, JsonValue]
    package_lock: PackageLock
    participants: tuple[Participant, ...]

    @property
    def participant_ids(self) -> frozenset[str]:
        return frozenset(participant.participant_id for participant in self.participants)


@dataclass(frozen=True)
class IdempotencyHit:
    payload_hash: str
    receipt: ActionReceipt


@dataclass(frozen=True)
class PendingMechanicsHit:
    payload_hash: str
    pending: PendingMechanicsAction


@dataclass(frozen=True)
class CampaignIndexRecord:
    """One validated campaign graph captured in a coherent read transaction."""

    campaign: CampaignRecord
    package_lock: PackageLock | None
    package_labels: tuple[tuple[str, str], ...]
    session_activity_ordinal: int
    turns: tuple[tuple[str, TurnStatus], ...]
    top_level_sessions: tuple[SessionRecord, ...]
    handoffs: tuple[BuilderDMHandoff, ...]
    interview_name: str | None = None


class SQLiteAuthorityStore:
    """The only adapter that opens the F1 live SQLite database."""

    def __init__(
        self,
        database_path: Path,
        *,
        _application_owner_gate: object | None = None,
    ) -> None:
        if _application_owner_gate is not _APPLICATION_OWNER_GATE:
            raise CommandRejected(
                "SQLite authority may be opened only by the application owner",
                code="application_owner_required",
            )
        self._database_path = database_path.resolve()
        lock_identity = self._database_path.with_name(f"{self._database_path.name}.authority-owner")
        self._owner_lock = DatabaseOwnerLock(lock_identity)
        try:
            self._migrate()
        except BaseException:
            self._owner_lock.close()
            raise
        self._closed = False

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("SQLite authority store is closed")
        connection = sqlite3.connect(
            self._database_path,
            isolation_level=None,
            timeout=30.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _connect_read_only(self) -> sqlite3.Connection:
        """Open existing authority bytes without persistent settings or side effects."""

        if self._closed:
            raise RuntimeError("SQLite authority store is closed")
        connection = sqlite3.connect(
            f"{self._database_path.as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=30.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        return connection

    def campaign_index(self) -> tuple[CampaignIndexRecord, ...]:
        """Read the campaign rail by casefolded name, exact name, then identity."""

        connection = self._connect_read_only()
        try:
            connection.execute("BEGIN")
            session_schema_available = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_sessions'"
                ).fetchone()
                is not None
            )
            campaigns = tuple(
                connection.execute(
                    "SELECT campaign.* FROM campaigns AS campaign "
                    "LEFT JOIN campaign_locks AS current_lock "
                    "ON current_lock.campaign_id = campaign.campaign_id "
                    "AND current_lock.lock_id = campaign.current_lock_id "
                    "ORDER BY campaign.display_name COLLATE NOCASE, "
                    "campaign.display_name, campaign.campaign_id"
                )
            )
            records: list[CampaignIndexRecord] = []
            for row in campaigns:
                campaign = self._campaign_record(row)
                try:
                    package_lock: PackageLock | None = sqlite_packages.current_lock_record(
                        connection, campaign.campaign_id
                    ).package_lock
                except CommandRejected:
                    package_lock = None
                package_labels: tuple[tuple[str, str], ...] = ()
                if package_lock is not None:
                    labels: list[tuple[str, str]] = []
                    for entry in package_lock.entries:
                        manifest = connection.execute(
                            "SELECT metadata_json FROM package_manifests "
                            "WHERE package_id = ? AND version = ? AND content_sha256 = ?",
                            (entry.package_id, entry.version, entry.sha256),
                        ).fetchone()
                        metadata = (
                            json.loads(str(manifest["metadata_json"]))
                            if manifest is not None
                            else {}
                        )
                        label = metadata.get("label") if isinstance(metadata, dict) else None
                        labels.append(
                            (
                                entry.package_id,
                                label.strip()
                                if isinstance(label, str) and label.strip()
                                else entry.package_id,
                            )
                        )
                    package_labels = tuple(labels)
                turns = tuple(
                    (
                        str(turn["turn_id"]),
                        TurnStatus(str(turn["status"])),
                    )
                    for turn in connection.execute(
                        "SELECT turn.turn_id, turn.status FROM turns AS turn "
                        "JOIN campaigns AS campaign "
                        "ON campaign.campaign_id = turn.campaign_id "
                        "WHERE campaign.campaign_id = ? "
                        "ORDER BY turn.created_revision, turn.turn_id",
                        (campaign.campaign_id,),
                    )
                )
                top_level_sessions = (
                    tuple(
                        sqlite_session.session_from_row(session)
                        for session in connection.execute(
                            "SELECT session.* FROM agent_sessions AS session "
                            "JOIN campaigns AS campaign "
                            "ON campaign.campaign_id = session.campaign_id "
                            "WHERE campaign.campaign_id = ? "
                            "AND session.parent_session_id IS NULL "
                            "ORDER BY session.session_id",
                            (campaign.campaign_id,),
                        )
                    )
                    if session_schema_available
                    else ()
                )
                handoffs = (
                    tuple(
                        sqlite_session.handoff_from_row(handoff)
                        for handoff in connection.execute(
                            "SELECT handoff.* FROM builder_dm_handoffs AS handoff "
                            "WHERE handoff.campaign_id = ? ORDER BY handoff.handoff_id",
                            (campaign.campaign_id,),
                        )
                    )
                    if session_schema_available
                    else ()
                )
                session_activity_ordinal = (
                    int(
                        connection.execute(
                            "SELECT COALESCE(MAX(message.rowid), 0) "
                            "FROM agent_session_messages AS message "
                            "JOIN agent_sessions AS session "
                            "ON session.session_id = message.session_id "
                            "WHERE session.campaign_id = ?",
                            (campaign.campaign_id,),
                        ).fetchone()[0]
                    )
                    if session_schema_available
                    else 0
                )
                records.append(
                    CampaignIndexRecord(
                        campaign=campaign,
                        package_lock=package_lock,
                        package_labels=package_labels,
                        session_activity_ordinal=session_activity_ordinal,
                        turns=turns,
                        top_level_sessions=top_level_sessions,
                        handoffs=handoffs,
                        interview_name=next((
                            sqlite_session.interview_name(
                                connection, session.session_id, campaign.campaign_id,
                            )
                            for session in top_level_sessions
                            if session.role is WriterRole.BUILDER
                            and not handoffs
                            and campaign.display_name == "New campaign interview"
                        ), None),
                    )
                )
            connection.commit()
            return tuple(records)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def campaign_recovery_hit(
        self,
        *,
        recovery_id: str,
        staged: campaign_archive.StagedCampaignArchive,
    ) -> CampaignRecoveryResult | None:
        """Perform the side-effect-free receipt lookup before lane acquisition."""

        semantic_json, semantic_sha256 = sqlite_recovery.semantic_payload(recovery_id, staged)
        connection = self._connect_read_only()
        try:
            return sqlite_recovery.lookup_receipt(
                connection,
                recovery_id=recovery_id,
                semantic_payload_json=semantic_json,
                semantic_payload_sha256=semantic_sha256,
            )
        finally:
            connection.close()

    def recover_campaign(
        self,
        *,
        recovery_id: str,
        staged: campaign_archive.StagedCampaignArchive,
    ) -> CampaignRecoveryResult:
        """Recover one staged W11 graph in one BEGIN IMMEDIATE transaction."""

        semantic_json, semantic_sha256 = sqlite_recovery.semantic_payload(recovery_id, staged)
        connection = self._connect()
        plan: sqlite_recovery.RecoveryPlan | None = None
        commit_started = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = self._verify_exact_schema_identity(connection)
            replay = sqlite_recovery.lookup_receipt(
                connection,
                recovery_id=recovery_id,
                semantic_payload_json=semantic_json,
                semantic_payload_sha256=semantic_sha256,
            )
            if replay is not None:
                connection.rollback()
                return replay
            self._verify_stored_authority(connection)
            sqlite_packages.verify_package_authority(connection)
            if version == _CAMPAIGN_SCHEMA_VERSION:
                sqlite_campaign.verify_f5_campaign_authority(connection)
            sqlite_recovery.plan_recovery(connection, staged)
            self._ensure_campaign_schema(connection)
            live_identity = _schema_identity(connection)
            if live_identity != _EXPECTED_EFFORT_SCHEMA_IDENTITY:
                raise SchemaIdentityError("Portable recovery schema ownership is not exact")
            plan = sqlite_recovery.plan_recovery(connection, staged)
            # The migration and every semantic insert share this same transaction.
            connection.execute("PRAGMA defer_foreign_keys = ON")
            inserted_count, reused_count = sqlite_recovery.insert_recovery_graph(connection, staged)
            if inserted_count != plan.inserted_count or reused_count != plan.reused_count:
                raise SchemaIdentityError("Portable recovery collision plan drifted")
            result = CampaignRecoveryResult(
                campaign_id=staged.campaign_id,
                captured_revision=staged.captured_revision,
                archive_sha256=staged.archive_sha256,
                byte_count=staged.byte_count,
            )
            sqlite_recovery.insert_receipt(
                connection,
                recovery_id=recovery_id,
                semantic_payload_json=semantic_json,
                semantic_payload_sha256=semantic_sha256,
                result=result,
            )
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            self._verify_stored_authority(connection)
            sqlite_packages.verify_package_authority(connection)
            sqlite_campaign.verify_f5_campaign_authority(connection)
            sqlite_continuity.verify_authority(connection)
            self._before_recovery_commit()
            commit_started = True
            connection.commit()
            self._after_recovery_commit()
            return result
        except BaseException as error:
            if not commit_started:
                if connection.in_transaction:
                    connection.rollback()
                raise
            # Commit and acknowledgement failures are resolved only from durable
            # read-only authority after closing the uncertain connection.
            with suppress(BaseException):
                connection.close()
            resolution = self._resolve_recovery_commit_ambiguity(
                recovery_id=recovery_id,
                semantic_payload_json=semantic_json,
                semantic_payload_sha256=semantic_sha256,
                staged=staged,
                plan=plan,
            )
            if resolution is not None:
                return resolution
            raise error
        finally:
            with suppress(BaseException):
                connection.close()

    def _resolve_recovery_commit_ambiguity(
        self,
        *,
        recovery_id: str,
        semantic_payload_json: str,
        semantic_payload_sha256: str,
        staged: campaign_archive.StagedCampaignArchive,
        plan: sqlite_recovery.RecoveryPlan | None,
    ) -> CampaignRecoveryResult | None:
        try:
            connection = self._connect_read_only()
            try:
                receipt = sqlite_recovery.lookup_receipt(
                    connection,
                    recovery_id=recovery_id,
                    semantic_payload_json=semantic_payload_json,
                    semantic_payload_sha256=semantic_payload_sha256,
                )
                if receipt is not None:
                    sqlite_recovery.verify_recovery_receipts(connection)
                    return receipt
                campaign_absent = (
                    connection.execute(
                        "SELECT 1 FROM campaigns WHERE campaign_id = ?", (staged.campaign_id,)
                    ).fetchone()
                    is None
                )
                if (
                    campaign_absent
                    and plan is not None
                    and sqlite_recovery.plan_keys_absent(connection, plan)
                ):
                    return None
                raise CommandRejected(
                    "Portable recovery commit outcome is uncertain",
                    code="portable_recovery_commit_outcome_uncertain",
                )
            finally:
                connection.close()
        except CommandRejected:
            raise
        except Exception:
            raise CommandRejected(
                "Portable recovery commit outcome is uncertain",
                code="portable_recovery_commit_outcome_uncertain",
            ) from None

    def _before_recovery_commit(self) -> None:
        """Private deterministic fault seam before recovery commit."""

    def _after_recovery_commit(self) -> None:
        """Private deterministic lost-ack seam after recovery commit."""

    def export_campaign(
        self, campaign_id: str, target: Path, *, include_thinking: bool = False,
    ) -> CampaignExportResult:
        """Publish one deterministic self-contained campaign archive.

        The caller exclusively manages the admitted target directory for this
        call, including Sylvia GM's unpredictable private temporary name.
        """

        self._validate_export_target_lexical(target)
        if type(include_thinking) is not bool:
            raise CommandRejected("Include thinking must be an explicit boolean")
        capture = self._capture_campaign_export(campaign_id, include_thinking=include_thinking)
        envelope = self._validate_export_envelope(target)
        self._after_export_snapshot_captured()
        claimed: _ClaimedBackupTemporary | None = None
        guard_handle: int | None = None
        seal_handle: int | None = None
        result: CampaignExportResult | None = None
        try:
            self._run_export_stage("metadata_spool")
            claimed = self._claim_backup_temporary(envelope)
            guard_handle = claimed.write_guard_handle
            self._export_claim_observer("claimed", claimed)
            non_manifest: list[Mapping[str, object]] = []
            archive: zipfile.ZipFile | None = None
            try:
                archive = zipfile.ZipFile(
                    claimed.path,
                    "w",
                    compression=zipfile.ZIP_STORED,
                    allowZip64=True,
                )
                self._write_export_member(
                    archive,
                    "authority.jsonl",
                    io.BytesIO(capture.authority_bytes),
                    len(capture.authority_bytes),
                )
                non_manifest.append(
                    {
                        "name": "authority.jsonl",
                        "byte_count": len(capture.authority_bytes),
                        "sha256": hashlib.sha256(capture.authority_bytes).hexdigest(),
                    }
                )
                for blob_sha256 in capture.blob_sha256s:
                    byte_count = self._write_export_blob(archive, blob_sha256)
                    non_manifest.append(
                        {
                            "name": f"blobs/{blob_sha256}",
                            "byte_count": byte_count,
                            "sha256": blob_sha256,
                        }
                    )
                if capture.private_bytes is not None:
                    private_name = private_records_archive.MEMBER_NAME
                    self._write_export_member(
                        archive, private_name, io.BytesIO(capture.private_bytes),
                        len(capture.private_bytes),
                    )
                    non_manifest.append({
                        "name": private_name, "byte_count": len(capture.private_bytes),
                        "sha256": hashlib.sha256(capture.private_bytes).hexdigest(),
                    })
                manifest = campaign_archive.make_manifest(
                    source_schema_version=capture.source_schema_version,
                    source_schema_identity_sha256=capture.source_schema_identity_sha256,
                    migration_provenance=capture.migration_provenance,
                    campaign_id=capture.campaign_id,
                    captured_revision=capture.captured_revision,
                    rows_by_table=capture.rows_by_table,
                    non_manifest_members=non_manifest,
                )
                encoded_manifest = campaign_archive.manifest_bytes(manifest)
                campaign_archive.checked_archive_ledger(
                    member_count=len(non_manifest) + 1,
                    authority_record_count=sum(
                        len(capture.rows_by_table[table])
                        for table in campaign_archive.TABLES_IN_ORDER
                    ),
                    single_table_row_count=max(
                        len(capture.rows_by_table[table])
                        for table in campaign_archive.TABLES_IN_ORDER
                    ),
                    manifest_bytes=len(encoded_manifest),
                    authority_jsonl_bytes=len(capture.authority_bytes),
                    single_authority_row_bytes=0,
                    single_text_or_json_value_bytes=0,
                    single_blob_bytes=max(
                        (
                            int(cast(Any, row["byte_count"]))
                            for row in capture.rows_by_table["package_blobs"]
                        ),
                        default=0,
                    ),
                    total_uncompressed_bytes=(
                        len(capture.authority_bytes)
                        + len(encoded_manifest)
                        + sum(int(cast(Any, item["byte_count"])) for item in non_manifest[1:])
                    ),
                )
                self._write_export_member(
                    archive,
                    "manifest.json",
                    io.BytesIO(encoded_manifest),
                    len(encoded_manifest),
                )
                try:
                    archive.close()
                except BaseException:
                    raise _ExportFailure("portable_export_zip_final_close_failed") from None
                archive = None
                self._run_export_stage("zip_final_close")
            except BaseException:
                if archive is not None:
                    with suppress(BaseException):
                        archive.close()
                raise
            self._run_export_stage("seal")
            seal_handle = self._acquire_backup_content_seal(claimed)
            self._run_export_stage("write_handle_close")
            if not self._close_backup_handle(guard_handle):
                raise _ExportFailure("portable_export_write_handle_close_failed")
            guard_handle = None
            self._run_export_stage("flush")
            if not self._flush_backup_handle(seal_handle):
                raise _ExportFailure("portable_export_flush_failed")
            self._run_export_stage("reopen")
            self._run_export_stage("validate")
            campaign_archive.validate_campaign_archive(
                claimed.path,
                expected_campaign_id=campaign_id,
            )
            self._run_export_stage("verifier_close")
            self._run_export_stage("hash")
            digest = hashlib.sha256()
            byte_count = 0
            with claimed.path.open("rb") as archive_source:
                for chunk in campaign_archive.iter_bounded_chunks(
                    archive_source,
                    limit=campaign_archive.LIMITS["total_uncompressed_bytes"],
                ):
                    digest.update(chunk)
                    byte_count += len(chunk)
            if not self._close_backup_handle(seal_handle):
                raise _ExportFailure("portable_export_verifier_close_failed")
            seal_handle = None
            result = CampaignExportResult(
                campaign_id=campaign_id,
                captured_revision=capture.captured_revision,
                archive_sha256=digest.hexdigest(),
                byte_count=byte_count,
            )
            self._export_claim_observer("before_publication", claimed)
            try:
                self._validate_export_claim(claimed, envelope)
            except BaseException:
                raise _ExportFailure("portable_export_namespace_changed") from None
            self._validate_export_envelope(
                envelope.target, expected=envelope, temporary=claimed.path
            )
            self._run_export_stage("move_file_ex")
            try:
                published = campaign_archive.move_export_file(claimed.path, envelope.target)
            except BaseException:
                raise _PublicationAcknowledgementLostError from None
            if not published:
                raise _ExportFailure("portable_export_move_file_ex_failed")
            return result
        except _PublicationAcknowledgementLostError:
            raise _ExportFailure("portable_export_move_file_ex_failed") from None
        except BaseException as error:
            failure = self._as_export_failure(error)
            cleanup_incomplete = False
            if seal_handle is not None and not self._close_backup_handle_safely(seal_handle):
                cleanup_incomplete = True
            if guard_handle is not None and not self._close_backup_handle_safely(guard_handle):
                cleanup_incomplete = True
            if claimed is not None and self._cleanup_owned_backup(claimed):
                cleanup_incomplete = True
            if cleanup_incomplete:
                failure = failure.with_supplemental("portable_export_cleanup_incomplete")
            raise failure from None

    def _validate_export_claim(
        self,
        claimed: _ClaimedBackupTemporary,
        envelope: _BackupEnvelope,
    ) -> None:
        handle = self._open_backup_identity_handle(claimed.path)
        if handle == _INVALID_HANDLE_VALUE:
            raise OSError("identity handle unavailable")
        try:
            self._validate_claimed_backup_temporary(claimed, handle, envelope)
        finally:
            self._close_backup_handle_safely(handle)

    def _write_export_member(
        self,
        archive: zipfile.ZipFile,
        name: str,
        source: Any,
        byte_count: int,
    ) -> None:
        info = campaign_archive.fixed_zip_info(name)
        info.file_size = byte_count
        try:
            with archive.open(info, "w", force_zip64=False) as destination:
                for chunk in campaign_archive.iter_bounded_chunks(source, limit=byte_count):
                    destination.write(chunk)
        except _ExportFailure:
            raise
        except BaseException:
            raise _ExportFailure("portable_export_zip_member_close_failed") from None
        self._run_export_stage("zip_member_close")

    def _write_export_blob(self, archive: zipfile.ZipFile, blob_sha256: str) -> int:
        lease_id = f"export-{uuid4().hex}"
        lease_acquired = False
        spool: Any | None = None
        failure: _ExportFailure | None = None
        byte_count = 0
        try:
            self._run_export_stage("lease_acquire")
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                metadata = connection.execute(
                    "SELECT byte_count, chunk_count FROM package_blobs WHERE blob_sha256 = ?",
                    (blob_sha256,),
                ).fetchone()
                if metadata is None:
                    raise _ExportFailure("portable_export_package_corrupt")
                declared_bytes = int(metadata["byte_count"])
                declared_chunks = int(metadata["chunk_count"])
                if (
                    declared_chunks
                    != (declared_bytes + campaign_archive.STREAM_CHUNK_BYTES - 1)
                    // campaign_archive.STREAM_CHUNK_BYTES
                ):
                    raise _ExportFailure("portable_export_package_corrupt")
                if declared_bytes > campaign_archive.LIMITS["single_blob_bytes"]:
                    raise _ExportFailure("portable_export_limit_single_blob_bytes")
                connection.execute(
                    "INSERT INTO package_read_leases(lease_id, blob_sha256) VALUES (?, ?)",
                    (lease_id, blob_sha256),
                )
                connection.commit()
                lease_acquired = True
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()
            self._run_export_stage("blob_spool_create")
            try:
                spool = _new_package_read_spool()
            except BaseException:
                raise _ExportFailure("portable_export_blob_spool_create_failed") from None
            digest = hashlib.sha256()
            expected_chunks = declared_chunks
            for ordinal in range(expected_chunks):
                reader = self._connect()
                try:
                    reader.execute("BEGIN")
                    row = reader.execute(
                        "SELECT chunk_order, byte_count, chunk_sha256, data "
                        "FROM package_blob_chunks WHERE blob_sha256 = ? AND chunk_order = ?",
                        (blob_sha256, ordinal),
                    ).fetchone()
                    reader.commit()
                finally:
                    if reader.in_transaction:
                        reader.rollback()
                    reader.close()
                if row is None:
                    raise _ExportFailure("portable_export_package_corrupt")
                data = bytes(row["data"])
                if (
                    int(row["chunk_order"]) != ordinal
                    or int(row["byte_count"]) != len(data)
                    or len(data) > campaign_archive.STREAM_CHUNK_BYTES
                    or str(row["chunk_sha256"]) != hashlib.sha256(data).hexdigest()
                ):
                    raise _ExportFailure("portable_export_package_corrupt")
                self._run_export_stage("blob_spool_write")
                spool.write(data)
                if ordinal == 0:
                    self._run_export_stage("blob_spool_rollover")
                    spool.rollover()
                digest.update(data)
                byte_count += len(data)
            if byte_count != declared_bytes or digest.hexdigest() != blob_sha256:
                raise _ExportFailure("portable_export_package_corrupt")
            self._run_export_stage("blob_spool_seek")
            spool.seek(0)
            info = campaign_archive.fixed_zip_info(f"blobs/{blob_sha256}")
            info.file_size = byte_count
            try:
                with archive.open(info, "w", force_zip64=False) as destination:
                    remaining = byte_count
                    while remaining:
                        self._run_export_stage("blob_spool_read")
                        chunk = spool.read(min(campaign_archive.STREAM_CHUNK_BYTES, remaining))
                        if not chunk:
                            raise _ExportFailure("portable_export_package_corrupt")
                        destination.write(chunk)
                        remaining -= len(chunk)
            except _ExportFailure:
                raise
            except BaseException:
                raise _ExportFailure("portable_export_zip_member_close_failed") from None
            self._run_export_stage("zip_member_close")
        except BaseException as error:
            failure = self._as_export_failure(error)
        if spool is not None:
            close_failed = False
            try:
                self._run_export_stage("blob_spool_close")
            except BaseException:
                close_failed = True
            try:
                spool.close()
            except BaseException:
                close_failed = True
            if close_failed:
                code = "portable_export_blob_spool_close_failed"
                if failure is None:
                    failure = _ExportFailure(code)
                else:
                    failure = failure.with_supplemental(code)
        if lease_acquired:
            try:
                self._run_export_stage("lease_release")
                cleanup = self._connect()
                try:
                    cleanup.execute("BEGIN IMMEDIATE")
                    cleanup.execute(
                        "DELETE FROM package_read_leases WHERE lease_id = ?", (lease_id,)
                    )
                    cleanup.commit()
                except BaseException:
                    if cleanup.in_transaction:
                        cleanup.rollback()
                    raise
                finally:
                    cleanup.close()
            except BaseException:
                code = "portable_export_lease_release_failed"
                if failure is None:
                    failure = _ExportFailure(code)
                else:
                    failure = failure.with_supplemental(code)
        if failure is not None:
            raise failure
        return byte_count

    def _capture_campaign_export(
        self, campaign_id: str, *, include_thinking: bool = False,
    ) -> _ExportCapture:
        connection: sqlite3.Connection | None = None
        failure: _ExportFailure | None = None
        capture: _ExportCapture | None = None
        stage = "capture_open"
        try:
            self._export_failure_stage(stage)
            connection = self._connect()
            connection.execute("PRAGMA query_only = ON")
            stage = "capture_begin"
            self._export_failure_stage(stage)
            connection.execute("BEGIN")
            stage = "capture_preflight"
            self._export_failure_stage(stage)
            self._assert_export_source_schema(connection)
            predicates = self._export_relation_predicates(campaign_id)
            preflight = self._preflight_export_relations(connection, predicates)
            participant_where, participant_parameters = predicates["participants"]
            adapted = int(
                connection.execute(
                    'SELECT COUNT(*) FROM "participants" WHERE '
                    f"({participant_where}) AND adaptation_id IS NOT NULL",
                    participant_parameters,
                ).fetchone()[0]
            )
            if adapted:
                raise CommandRejected(
                    "Campaign is not eligible for portable export",
                    code="portable_export_adaptation_ineligible",
                )
            stage = "capture_read"
            self._export_failure_stage(stage)
            rows = self._read_export_rows(connection, predicates)
            stage = "capture_encode"
            self._export_failure_stage(stage)
            output = io.BytesIO()
            record_count = 0
            maximum_row = 0
            maximum_value = 0
            for table in campaign_archive.TABLES_IN_ORDER:
                for row in rows[table]:
                    for value in row.values():
                        if isinstance(value, str):
                            maximum_value = max(maximum_value, len(value.encode("utf-8")))
                    encoded = campaign_archive.canonical_authority_record(table, row)
                    maximum_row = max(maximum_row, len(encoded))
                    if maximum_row > campaign_archive.LIMITS["single_authority_row_bytes"]:
                        raise _ExportFailure("portable_export_limit_single_authority_row_bytes")
                    if maximum_value > campaign_archive.LIMITS["single_text_or_json_value_bytes"]:
                        raise _ExportFailure(
                            "portable_export_limit_single_text_or_json_value_bytes"
                        )
                    if (
                        output.tell() + len(encoded)
                        > campaign_archive.LIMITS["authority_jsonl_bytes"]
                    ):
                        raise _ExportFailure("portable_export_limit_authority_jsonl_bytes")
                    output.write(encoded)
                    record_count += 1
            maximum_table = max(len(rows[table]) for table in campaign_archive.TABLES_IN_ORDER)
            campaign_archive.checked_archive_ledger(
                authority_record_count=record_count,
                single_table_row_count=maximum_table,
                authority_jsonl_bytes=output.tell(),
                single_authority_row_bytes=maximum_row,
                single_text_or_json_value_bytes=maximum_value,
                single_blob_bytes=preflight["single_blob_bytes"],
                member_count=preflight["member_count"],
                total_uncompressed_bytes=preflight["total_uncompressed_bytes"],
            )
            campaign_rows = rows["campaigns"]
            if len(campaign_rows) != 1:
                raise CommandRejected("Campaign is unavailable")
            campaign_row = campaign_rows[0]
            migration_provenance = campaign_archive.validate_migration_provenance(
                {
                    "schema_migrations": [
                        {
                            "version": int(row[0]),
                            "applied_name": str(row[1]),
                            "sql_sha256": str(row[2]),
                        }
                        for row in connection.execute(
                            "SELECT version, applied_name, sql_sha256 "
                            "FROM schema_migrations ORDER BY version"
                        ).fetchall()
                    ],
                    "schema_extensions": [
                        {
                            "extension_name": str(row[0]),
                            "extension_sha256": str(row[1]),
                            "predecessor_migration_name": str(row[2]),
                            "predecessor_sql_sha256": str(row[3]),
                        }
                        for row in connection.execute(
                            "SELECT extension_name, extension_sha256, "
                            "predecessor_migration_name, predecessor_sql_sha256 "
                            "FROM authority_schema_extensions ORDER BY extension_name"
                        ).fetchall()
                    ],
                }
            )
            private_bytes = None
            if include_thinking:
                codec = private_records_archive
                private_rows = connection.execute(
                    "SELECT " + ", ".join("p." + name for name in codec.FIELDS)
                    + " FROM provider_private_records p JOIN agent_sessions s "
                    "ON s.session_id = p.agent_session_id WHERE s.campaign_id = ? "
                    "ORDER BY p.rowid", (campaign_id,),
                )
                private_bytes = codec.encode(
                    (dict(zip(codec.FIELDS, row, strict=True)) for row in private_rows),
                    session_ids={str(row["session_id"]) for row in rows["agent_sessions"]},
                )
            capture = _ExportCapture(
                campaign_id=campaign_id,
                captured_revision=int(cast(Any, campaign_row["revision"])),
                source_schema_version=campaign_archive.SOURCE_AUTHORITY_SCHEMA_VERSION,
                source_schema_identity_sha256=campaign_archive.source_authority_schema_claim_sha256(),
                migration_provenance=migration_provenance,
                rows_by_table={
                    table: tuple(rows[table]) for table in campaign_archive.TABLES_IN_ORDER
                },
                authority_bytes=output.getvalue(),
                private_bytes=private_bytes,
                blob_sha256s=tuple(str(row["blob_sha256"]) for row in rows["package_blobs"]),
            )
            stage = "capture_commit"
            self._export_failure_stage(stage)
            connection.commit()
        except BaseException as error:
            failure = self._as_export_failure(error, default_stage=stage)
            if connection is not None and connection.in_transaction:
                try:
                    self._run_export_stage("capture_rollback")
                    connection.rollback()
                except BaseException:
                    failure = self._with_export_supplemental(
                        failure, "portable_export_capture_rollback_failed"
                    )
        if connection is not None:
            try:
                self._run_export_stage("capture_close")
                connection.close()
            except BaseException:
                if failure is None:
                    failure = _ExportFailure("portable_export_capture_close_failed")
                else:
                    failure = self._with_export_supplemental(
                        failure, "portable_export_capture_close_failed"
                    )
        if failure is not None:
            raise failure
        if capture is None:
            raise _ExportFailure("portable_export_capture_read_failed")
        return capture

    @staticmethod
    def _assert_export_source_schema(connection: sqlite3.Connection) -> None:
        """Admit only exact live physical current authority without migration."""

        if (
            int(connection.execute("PRAGMA user_version").fetchone()[0]) != _CAMPAIGN_SCHEMA_VERSION
            or _schema_identity(connection) != _EXPECTED_EFFORT_SCHEMA_IDENTITY
            or (
                sqlite_state_ownership.trigger_identity(connection)
                != sqlite_state_ownership.EXPECTED_TRIGGER_IDENTITY
            )
        ):
            raise _ExportFailure("portable_export_source_schema_unsupported")

    def _export_relation_predicates(
        self, campaign_id: str
    ) -> dict[str, tuple[str, tuple[object, ...]]]:
        direct = (
            "campaigns",
            "campaign_lifecycle_state",
            "campaign_lifecycle_transitions",
            "campaign_operations",
            "campaign_constructions",
            "campaign_continuity_records",
            "campaign_revisions",
            "campaign_package_memberships",
            "participants",
            "participant_quarantines",
            "campaign_state_namespace_owners",
            "campaign_state",
            "turns",
            "actions",
            "idempotency_records",
            "pending_mechanics_actions",
            "pending_mechanics_action_aliases",
            "mechanics_choice_resolutions",
            "canonical_events",
            "narration_interruptions",
            "mutations",
            "consequences",
            "campaign_locks",
            "package_activations",
            "campaign_plans",
            "builder_dm_handoffs",
            "agent_sessions",
        )
        predicates: dict[str, tuple[str, tuple[object, ...]]] = dict.fromkeys(
            direct, ('"campaign_id" = ?', (campaign_id,))
        )
        predicates["campaign_operations"] = (
            '"campaign_id" = ? AND "operation_kind" != \'purge_campaign\'',
            (campaign_id,),
        )
        selected_candidates = (
            "SELECT candidate_id FROM package_activations "
            "WHERE campaign_id = ? AND candidate_id IS NOT NULL "
            "UNION SELECT package_candidate_id FROM builder_dm_handoffs "
            "WHERE campaign_id = ? AND package_candidate_id IS NOT NULL"
        )
        predicates["campaign_lock_memberships"] = (
            '"lock_id" IN (SELECT lock_id FROM campaign_locks WHERE campaign_id = ?)',
            (campaign_id,),
        )
        predicates["agent_session_messages"] = (
            '"session_id" IN (SELECT session_id FROM agent_sessions WHERE campaign_id = ?)',
            (campaign_id,),
        )
        candidate_predicate = f'"candidate_id" IN ({selected_candidates})'
        predicates["package_candidates"] = (candidate_predicate, (campaign_id, campaign_id))
        predicates["package_candidate_memberships"] = (
            candidate_predicate,
            (campaign_id, campaign_id),
        )
        predicates["package_staging"] = (
            '"install_id" IN (SELECT install_id FROM package_candidates WHERE '
            + candidate_predicate
            + ")",
            (campaign_id, campaign_id),
        )

        def exact_identity(table: str, sha_column: str) -> tuple[str, tuple[object, ...]]:
            where = (
                "(EXISTS (SELECT 1 FROM campaign_lock_memberships AS export_lock_member "
                "JOIN campaign_locks AS export_lock "
                "ON export_lock.lock_id = export_lock_member.lock_id "
                "WHERE export_lock.campaign_id = ? "
                f'AND export_lock_member.package_id = "{table}"."package_id" '
                f'AND export_lock_member.version = "{table}"."version" '
                f'AND export_lock_member.content_sha256 = "{table}"."{sha_column}") '
                "OR EXISTS (SELECT 1 FROM package_candidate_memberships AS export_candidate_member "
                f"WHERE export_candidate_member.candidate_id IN ({selected_candidates}) "
                f'AND export_candidate_member.package_id = "{table}"."package_id" '
                f'AND export_candidate_member.version = "{table}"."version" '
                f'AND export_candidate_member.content_sha256 = "{table}"."{sha_column}"))'
            )
            return where, (campaign_id, campaign_id, campaign_id)

        for table in (
            "package_manifests",
            "package_manifest_files",
            "package_exact_dependencies",
            "package_capabilities",
            "package_tools",
            "package_prompt_fragments",
            "package_ui_surfaces",
            "package_participant_artifacts",
        ):
            predicates[table] = exact_identity(table, "content_sha256")
        for table in ("packages", "package_dependencies"):
            predicates[table] = exact_identity(table, "sha256")
        file_identity, file_parameters = exact_identity("package_manifest_files", "content_sha256")
        predicates["package_blobs"] = (
            '"blob_sha256" IN (SELECT "package_manifest_files"."blob_sha256" '
            'FROM "package_manifest_files" WHERE ' + file_identity + ")",
            file_parameters,
        )
        return {table: predicates[table] for table in campaign_archive.TABLES_IN_ORDER}

    def _preflight_export_relations(
        self,
        connection: sqlite3.Connection,
        predicates: Mapping[str, tuple[str, tuple[object, ...]]],
    ) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        authority_count = 0
        authority_bound = 0
        maximum_row_bound = 0
        maximum_value = 0
        for table in campaign_archive.TABLES_IN_ORDER:
            where, parameters = predicates[table]
            count = int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE {where}', parameters
                ).fetchone()[0]
            )
            if count > campaign_archive.LIMITS["single_table_row_count"]:
                raise _ExportFailure("portable_export_limit_single_table_row_count")
            counts[table] = count
            authority_count += count
            if authority_count > campaign_archive.LIMITS["authority_record_count"]:
                raise _ExportFailure("portable_export_limit_authority_record_count")
            for column in campaign_archive.TABLE_COLUMNS[table]:
                value = connection.execute(
                    f'SELECT MAX(COALESCE(length(CAST("{column}" AS BLOB)), 0)) '
                    f'FROM "{table}" WHERE {where}',
                    parameters,
                ).fetchone()[0]
                bound = int(value or 0)
                if bound > campaign_archive.LIMITS["single_text_or_json_value_bytes"]:
                    raise _ExportFailure("portable_export_limit_single_text_or_json_value_bytes")
                maximum_value = max(maximum_value, bound)
            columns = campaign_archive.TABLE_COLUMNS[table]
            raw_size = " + ".join(
                f'COALESCE(length(CAST("{column}" AS BLOB)), 0)' for column in columns
            )
            largest_raw_row = connection.execute(
                f'SELECT MAX({raw_size}) FROM "{table}" WHERE {where}', parameters,
            ).fetchone()[0]
            if int(largest_raw_row or 0) > campaign_archive.LIMITS["single_authority_row_bytes"]:
                raise _ExportFailure("portable_export_limit_single_authority_row_bytes")
            selected_columns = ", ".join(f'"{column}"' for column in columns)
            for row in connection.execute(
                f'SELECT {selected_columns} FROM "{table}" WHERE {where}', parameters,
            ):
                row_size = campaign_archive.canonical_authority_record_size(
                    table, dict(zip(columns, row, strict=True)),
                )
                if row_size > campaign_archive.LIMITS["single_authority_row_bytes"]:
                    raise _ExportFailure("portable_export_limit_single_authority_row_bytes")
                maximum_row_bound = max(maximum_row_bound, row_size)
                authority_bound += row_size
                if authority_bound > campaign_archive.LIMITS["authority_jsonl_bytes"]:
                    raise _ExportFailure("portable_export_limit_authority_jsonl_bytes")
        blob_where, blob_parameters = predicates["package_blobs"]
        blob_ledger = connection.execute(
            'SELECT COALESCE(MAX("byte_count"), 0), COALESCE(SUM("byte_count"), 0), '
            'COALESCE(SUM(CASE WHEN "byte_count" < 0 OR "chunk_count" < 0 '
            'OR "chunk_count" != (("byte_count" + 65535) / 65536) THEN 1 ELSE 0 END), 0) '
            f'FROM "package_blobs" WHERE {blob_where}',
            blob_parameters,
        ).fetchone()
        single_blob = int(blob_ledger[0])
        blob_total = int(blob_ledger[1])
        if int(blob_ledger[2]):
            raise _ExportFailure("portable_export_package_corrupt")
        member_count = counts["package_blobs"] + 2
        total_bound = authority_bound + campaign_archive.LIMITS["manifest_bytes"] + blob_total
        campaign_archive.checked_archive_ledger(
            member_count=member_count,
            authority_record_count=authority_count,
            single_table_row_count=max(counts.values(), default=0),
            manifest_bytes=campaign_archive.LIMITS["manifest_bytes"],
            authority_jsonl_bytes=authority_bound,
            single_authority_row_bytes=maximum_row_bound,
            single_text_or_json_value_bytes=maximum_value,
            single_blob_bytes=single_blob,
            total_uncompressed_bytes=total_bound,
        )
        if counts["campaigns"] != 1:
            raise CommandRejected("Campaign is unavailable")
        return {
            "member_count": member_count,
            "single_blob_bytes": single_blob,
            "total_uncompressed_bytes": total_bound,
        }

    def _read_export_rows(
        self,
        connection: sqlite3.Connection,
        predicates: Mapping[str, tuple[str, tuple[object, ...]]],
    ) -> dict[str, list[Mapping[str, object]]]:
        rows: dict[str, list[Mapping[str, object]]] = {
            table: [] for table in campaign_archive.TABLES_IN_ORDER
        }
        for table in campaign_archive.TABLES_IN_ORDER:
            where, parameters = predicates[table]
            columns = campaign_archive.TABLE_COLUMNS[table]
            order = campaign_archive.TABLE_KEYS[table]
            query = (
                "SELECT "
                + ", ".join(f'"{column}"' for column in columns)
                + f' FROM "{table}" WHERE {where} ORDER BY '
                + ", ".join(f'"{column}"' for column in order)
            )
            selected = rows[table]
            for source in connection.execute(query, parameters):
                materialized = {column: source[column] for column in columns}
                if table == "agent_session_messages" and materialized["kind"] == "evidence":
                    try:
                        content = json.loads(
                            str(materialized["content_json"]),
                            object_pairs_hook=_reject_duplicate_members,
                            parse_constant=_reject_nonstandard_constant,
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        raise _ExportFailure("portable_export_capture_read_failed") from None
                    if isinstance(content, dict) and "source_locator" in content:
                        content["source_locator"] = None
                        materialized["content_json"] = json.dumps(
                            content,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                selected.append(materialized)
        return rows

    @staticmethod
    def _in_clause(column: str, values: Sequence[object]) -> str:
        if not values:
            return "0"
        return f'"{column}" IN ({", ".join("?" for _ in values)})'

    @staticmethod
    def _identity_clause(
        identities: Iterable[tuple[str, str, str]],
        *,
        sha_column: str,
    ) -> tuple[str, tuple[object, ...]]:
        values = tuple(sorted(identities))
        if not values:
            return "0", ()
        terms = []
        parameters: list[object] = []
        for package_id, version, sha256 in values:
            terms.append(f'(("package_id" = ?) AND ("version" = ?) AND ("{sha_column}" = ?))')
            parameters.extend((package_id, version, sha256))
        return " OR ".join(terms), tuple(parameters)

    def _validate_export_target_lexical(self, target: Path) -> None:
        """Reject unsupported namespaces without touching SQLite or the filesystem."""

        if os.name != "nt" or not isinstance(target, Path):
            raise _ExportFailure("portable_export_destination_unsupported")
        raw = os.fspath(target)
        if raw.startswith(("\\\\", "//", "\\?\\", "\\.\\")):
            raise _ExportFailure("portable_export_destination_unsupported")
        normalized = self._lexical_backup_path(target)
        if ":" in normalized.name:
            raise _ExportFailure("portable_export_destination_unsupported")

    def _validate_export_envelope(
        self,
        target: Path,
        *,
        expected: _BackupEnvelope | None = None,
        temporary: Path | None = None,
    ) -> _BackupEnvelope:
        self._validate_export_target_lexical(target)
        normalized = self._lexical_backup_path(target)
        volume_root = normalized.anchor
        drive_type = self._backup_drive_type(volume_root)
        drive_codes = {
            0: "portable_export_destination_unknown",
            2: "portable_export_destination_removable",
            4: "portable_export_destination_remote",
        }
        if drive_type in drive_codes:
            raise _ExportFailure(drive_codes[drive_type])
        if drive_type != _DRIVE_FIXED:
            raise _ExportFailure("portable_export_destination_unsupported")
        parent = normalized.parent
        parent_attributes = self._backup_file_attributes(parent)
        if parent_attributes is None:
            raise _ExportFailure("portable_export_destination_parent")
        if parent_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise _ExportFailure("portable_export_destination_reparse")
        if not stat.S_ISDIR(self._backup_stat(parent).st_mode):
            raise _ExportFailure("portable_export_destination_parent")
        self._validate_backup_ancestors(parent)
        target_key = self._backup_path_key(normalized)
        protected = self._protected_backup_source_paths()
        protected_keys = {self._backup_path_key(path): path for path in protected}
        if target_key == self._backup_path_key(self._database_path):
            raise _ExportFailure("portable_export_destination_source_alias")
        if target_key in protected_keys:
            raise _ExportFailure("portable_export_destination_source_artifact")
        attributes = self._backup_file_attributes(normalized)
        target_identity: tuple[int, int] | None = None
        if attributes is not None:
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise _ExportFailure("portable_export_destination_reparse")
            target_stat = self._backup_lstat(normalized)
            if stat.S_ISDIR(target_stat.st_mode):
                raise _ExportFailure("portable_export_destination_directory")
            if not stat.S_ISREG(target_stat.st_mode):
                raise _ExportFailure("portable_export_destination_unsupported")
            target_identity = (int(target_stat.st_dev), int(target_stat.st_ino))
            for protected_path in protected:
                if self._backup_path_exists(protected_path) and self._backup_samefile(
                    normalized, protected_path
                ):
                    raise _ExportFailure("portable_export_destination_source_alias")
        if temporary is not None and target_key == self._backup_path_key(temporary):
            raise _ExportFailure("portable_export_destination_source_alias")
        envelope = _BackupEnvelope(
            target=normalized,
            parent_identity=self._backup_identity(parent),
            target_identity=target_identity,
            source_identity=self._backup_identity(self._database_path),
            volume_root=self._backup_path_key(Path(volume_root)),
        )
        if expected is not None and envelope != expected:
            raise _ExportFailure("portable_export_namespace_changed")
        return envelope

    @staticmethod
    def _as_export_failure(
        error: BaseException,
        *,
        default_stage: str | None = None,
    ) -> _ExportFailure:
        if isinstance(error, _ExportFailure):
            return error
        if isinstance(error, CommandRejected):
            return _ExportFailure(error.code)
        stage = "publication" if default_stage is None else default_stage
        return _ExportFailure(f"portable_export_{stage}_failed")

    @staticmethod
    def _with_export_supplemental(
        failure: _ExportFailure,
        code: str,
    ) -> _ExportFailure:
        return failure.with_supplemental(code)

    def _run_export_stage(self, stage: str) -> None:
        try:
            self._export_failure_stage(stage)
        except _ExportFailure:
            raise
        except BaseException:
            raise _ExportFailure(f"portable_export_{stage}_failed") from None

    def _export_failure_stage(self, _stage: str) -> None:
        """Deterministic non-branching observability/fault seam for W11."""

    def _after_export_snapshot_captured(self) -> None:
        """No-op seam after the read transaction and before every file wait."""

    def _export_claim_observer(self, _stage: str, _claimed: object) -> None:
        """No-op identity-bearing seam used to prove cleanup/publication ordering."""

    def _pre_migration_source_path(self, version: int) -> Path:
        return (
            self._database_path.parent
            / "migration-sources"
            / f"{self._database_path.stem}.pre-migration-v{version}.sqlite3"
        )

    def _preserve_pre_migration_source(self, version: int) -> None:
        """Publish one verified ordinary backup before material schema DDL."""

        target = self._pre_migration_source_path(version)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.backup_database(target)

    def backup_database(self, target: Path) -> None:
        """Durably publish one coherent online copy in the supported Windows envelope.

        For the complete call lifetime, the caller must exclusively manage the admitted
        local target directory.  No independent process may open writable, replace,
        rename, link, unlink, truncate, or delete this operation's private temporary
        main file or derived sidecars.  Only this operation's SQLite target connection
        may write the temporary database.  This full-call directory-use and content
        quiescence is a supported precondition; the guard and seal are defense in depth.
        """

        claimed: _ClaimedBackupTemporary | None = None
        guard_handle: int | None = None
        seal_handle: int | None = None
        phase = "target_envelope"
        try:
            envelope = self._validate_backup_envelope(target)
            phase = "temporary_claim"
            claimed = self._claim_backup_temporary(envelope)
            guard_handle = claimed.write_guard_handle
            phase = "online_copy"
            self._copy_online_backup(claimed.path)
            phase = "prepublication_cleanup"
            self._remove_prepublication_sidecars(claimed.path)
            phase = "content_seal"
            seal_handle = self._acquire_backup_content_seal(claimed)
            phase = "write_guard_close"
            if not self._close_backup_handle(guard_handle):
                raise _BackupFailure("write_guard_close", "failed")
            guard_handle = None
            phase = "flush"
            if not self._flush_backup_handle(seal_handle):
                raise _BackupFailure("flush", "failed")
            phase = "verification"
            self._verify_backup_twice(claimed.path)
            phase = "final_envelope"
            self._validate_claimed_backup_temporary(claimed, seal_handle, envelope)
            self._validate_backup_envelope(
                envelope.target,
                expected=envelope,
                temporary=claimed.path,
            )
            self._require_backup_artifacts_absent(
                self._owned_backup_artifacts(claimed.path)[1:],
                phase="final_envelope",
            )
            flags = _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
            phase = "content_seal_close"
            if not self._close_backup_handle(seal_handle):
                raise _BackupFailure("content_seal_close", "failed")
            seal_handle = None
            phase = "publication"
            if not self._move_backup_file(claimed.path, envelope.target, flags):
                raise _BackupFailure("publication", "replace_failed")
            return
        except BaseException as error:
            failure = self._as_backup_failure(error, phase)
            resource_codes: list[str] = []
            if seal_handle is not None and not self._close_backup_handle_safely(seal_handle):
                resource_codes.append("backup_cleanup_content_seal_close_failed")
            if guard_handle is not None and not self._close_backup_handle_safely(guard_handle):
                resource_codes.append("backup_cleanup_write_guard_close_failed")
            if claimed is not None:
                resource_codes.extend(self._cleanup_owned_backup(claimed))
            if resource_codes:
                failure = failure.with_supplemental(*resource_codes)
            raise failure from None

    def _copy_online_backup(self, temporary: Path) -> None:
        source: sqlite3.Connection | None = None
        destination: sqlite3.Connection | None = None
        failure: _BackupFailure | None = None
        phase = "source_open"
        try:
            source = self._open_backup_source()
            phase = "target_open"
            destination = self._open_backup_target(temporary)
            hook_fired = False

            def progress(status: int, remaining: int, total: int) -> None:
                nonlocal hook_fired
                if (
                    not hook_fired
                    and status in {sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE}
                    and total > remaining
                ):
                    hook_fired = True
                    try:
                        self._after_backup_snapshot_started()
                    except BaseException:
                        raise _BackupFailure("snapshot_hook", "failed") from None

            phase = "online_copy"
            source.backup(
                destination,
                pages=_BACKUP_PAGES_PER_STEP,
                progress=progress,
                sleep=0.01,
            )
            if not hook_fired:
                raise _BackupFailure("online_copy", "progress_missing")
        except BaseException as error:
            failure = self._as_backup_failure(error, phase)

        for role, connection in (("target", destination), ("source", source)):
            if connection is None:
                continue
            try:
                connection.close()
            except BaseException:
                code = f"backup_{role}_close_failed"
                if failure is None:
                    failure = _BackupFailure(f"{role}_close", "failed")
                else:
                    failure = failure.with_supplemental(code)
        if failure is not None:
            raise failure from None

    def _after_backup_snapshot_started(self) -> None:
        """Deterministic no-op seam reached once after copied-page progress."""

    def _open_backup_source(self) -> sqlite3.Connection:
        return sqlite3.connect(
            self._backup_read_only_uri(self._database_path),
            uri=True,
            isolation_level=None,
            timeout=30.0,
        )

    def _open_backup_target(self, temporary: Path) -> sqlite3.Connection:
        return sqlite3.connect(temporary, isolation_level=None, timeout=30.0)

    def _open_backup_verifier(self, temporary: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._backup_verifier_uri(temporary),
            uri=True,
            isolation_level=None,
            timeout=30.0,
        )
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _backup_read_only_uri(path: Path) -> str:
        return f"{path.as_uri()}?mode=ro"

    @staticmethod
    def _backup_verifier_uri(path: Path) -> str:
        return f"{path.as_uri()}?mode=ro&immutable=1"

    def _verify_backup_twice(self, temporary: Path) -> None:
        sidecars = self._owned_backup_artifacts(temporary)[1:]
        self._require_backup_artifacts_absent(sidecars, phase="verification")
        for _pass_number in (1, 2):
            connection: sqlite3.Connection | None = None
            failure: _BackupFailure | None = None
            try:
                connection = self._open_backup_verifier(temporary)
                connection.execute("PRAGMA query_only = ON")
                query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
                if query_only != 1:
                    raise _BackupFailure("verification", "query_only_failed")
                self._verify_backup_authority(connection)
            except BaseException as error:
                failure = self._as_backup_failure(error, "verification")
            if connection is not None:
                try:
                    connection.close()
                except BaseException:
                    if failure is None:
                        failure = _BackupFailure("verifier_close", "failed")
                    else:
                        failure = failure.with_supplemental("backup_verifier_close_failed")
            if failure is not None:
                raise failure from None
            self._require_backup_artifacts_absent(sidecars, phase="verification")

    def _verify_backup_authority(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {
            _SCHEMA_VERSION,
            _SESSION_SCHEMA_VERSION,
            _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION,
            _STATE_OWNERSHIP_SCHEMA_VERSION,
            _PRE_BUILDER_CHAT_SCHEMA_VERSION,
            _PRE_CONTINUITY_SCHEMA_VERSION,
            _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
            _PRE_REASONING_SCHEMA_VERSION,
            _PRE_EFFORT_SCHEMA_VERSION,
            _CAMPAIGN_SCHEMA_VERSION,
        }:
            raise SchemaIdentityError("Backup schema identity is unsupported")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        has_recovery_extension = False
        has_purge_extension = False
        if version in (
            _PRE_REASONING_SCHEMA_VERSION, _PRE_EFFORT_SCHEMA_VERSION, _CAMPAIGN_SCHEMA_VERSION,
        ):
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
                | _BUILDER_CHAT_TABLES
                | _CONTINUITY_TABLES
            )
            if version >= _PRE_EFFORT_SCHEMA_VERSION:
                required_tables |= {"provider_private_records"}
            expected_identity = (
                _EXPECTED_EFFORT_SCHEMA_IDENTITY if version == _CAMPAIGN_SCHEMA_VERSION
                else _EXPECTED_REASONING_SCHEMA_IDENTITY if version == _PRE_EFFORT_SCHEMA_VERSION
                else _EXPECTED_DRAFT_LIFECYCLE_SCHEMA_IDENTITY
            )
            has_recovery_extension = True
            has_purge_extension = connection.execute(
                "SELECT 1 FROM authority_schema_extensions WHERE extension_name = ?",
                (_PURGE_EXTENSION_NAME,),
            ).fetchone() is not None
        elif version == _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
                | _BUILDER_CHAT_TABLES
                | _CONTINUITY_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_CONTINUITY_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_CONTINUITY_SCHEMA_IDENTITY
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_CONTINUITY_SCHEMA_IDENTITY
            has_recovery_extension = True
        elif version == _PRE_CONTINUITY_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
                | _BUILDER_CHAT_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_BUILDER_CHAT_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_BUILDER_CHAT_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_BUILDER_CHAT_SCHEMA_IDENTITY
                has_recovery_extension = True
        elif version == _PRE_BUILDER_CHAT_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_MECHANICS_CHOICE_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_MECHANICS_CHOICE_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_MECHANICS_CHOICE_SCHEMA_IDENTITY
                has_recovery_extension = True
        elif version == _STATE_OWNERSHIP_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES | _SESSION_TABLES | _CAMPAIGN_TABLES | _STATE_OWNERSHIP_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_STATE_OWNERSHIP_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_STATE_OWNERSHIP_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_STATE_OWNERSHIP_SCHEMA_IDENTITY
                has_recovery_extension = True
        elif version == _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION:
            required_tables = _REQUIRED_TABLES | _SESSION_TABLES | _CAMPAIGN_TABLES
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            elif live_identity == _EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY
                has_recovery_extension = True
            else:
                expected_identity = _EXPECTED_CAMPAIGN_SCHEMA_IDENTITY
        elif version == _SESSION_SCHEMA_VERSION:
            required_tables = _REQUIRED_TABLES | _SESSION_TABLES
            expected_identity = _EXPECTED_SESSION_SCHEMA_IDENTITY
        else:
            required_tables = _REQUIRED_TABLES
            expected_identity = _EXPECTED_SCHEMA_IDENTITY
        if not required_tables.issubset(tables):
            raise SchemaIdentityError("Backup schema identity does not match its version")
        if _schema_identity(connection) != expected_identity:
            raise SchemaIdentityError("Backup schema structure does not match its version")
        if version in (
            _STATE_OWNERSHIP_SCHEMA_VERSION,
            _PRE_BUILDER_CHAT_SCHEMA_VERSION,
            _PRE_CONTINUITY_SCHEMA_VERSION,
            _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
            _PRE_REASONING_SCHEMA_VERSION,
            _PRE_EFFORT_SCHEMA_VERSION,
            _CAMPAIGN_SCHEMA_VERSION,
        ) and (
            sqlite_state_ownership.trigger_identity(connection)
            != sqlite_state_ownership.EXPECTED_TRIGGER_IDENTITY
        ):
            raise SchemaIdentityError("Backup state-ownership triggers are invalid")
        migration_rows = tuple(
            (int(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
        expected_migrations: tuple[tuple[int, str, str], ...] = (
            (1, _MIGRATION_1_NAME, _MIGRATION_1_SHA256),
            (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
            (3, _MIGRATION_3_NAME, _MIGRATION_3_SHA256),
        )
        if version >= _SESSION_SCHEMA_VERSION:
            expected_migrations += ((4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256),)
        if version in (
            _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION,
            _STATE_OWNERSHIP_SCHEMA_VERSION,
            _PRE_BUILDER_CHAT_SCHEMA_VERSION,
            _PRE_CONTINUITY_SCHEMA_VERSION,
            _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
            _PRE_REASONING_SCHEMA_VERSION,
            _PRE_EFFORT_SCHEMA_VERSION,
            _CAMPAIGN_SCHEMA_VERSION,
        ):
            supported_migrations = {
                (*expected_migrations, (5, _MIGRATION_5_NAME, _MIGRATION_5_SHA256)),
                (
                    *expected_migrations,
                    (5, _MIGRATION_5_NAME, _PREDECESSOR_MIGRATION_5_SHA256),
                ),
            }
            if version in (
                _STATE_OWNERSHIP_SCHEMA_VERSION,
                _PRE_BUILDER_CHAT_SCHEMA_VERSION,
                _PRE_CONTINUITY_SCHEMA_VERSION,
                _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                _PRE_REASONING_SCHEMA_VERSION,
                _PRE_EFFORT_SCHEMA_VERSION,
                _CAMPAIGN_SCHEMA_VERSION,
            ):
                supported_migrations = {
                    (*item, (6, _MIGRATION_6_NAME, _MIGRATION_6_SHA256))
                    for item in supported_migrations
                }
            if version in (
                _PRE_BUILDER_CHAT_SCHEMA_VERSION,
                _PRE_CONTINUITY_SCHEMA_VERSION,
                _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                _PRE_REASONING_SCHEMA_VERSION,
                _PRE_EFFORT_SCHEMA_VERSION,
                _CAMPAIGN_SCHEMA_VERSION,
            ):
                supported_migrations = {
                    (*item, (7, _MIGRATION_7_NAME, _MIGRATION_7_SHA256))
                    for item in supported_migrations
                }
            if version in (
                _PRE_CONTINUITY_SCHEMA_VERSION,
                _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                _PRE_REASONING_SCHEMA_VERSION,
                _PRE_EFFORT_SCHEMA_VERSION,
                _CAMPAIGN_SCHEMA_VERSION,
            ):
                supported_migrations = {
                    (*item, (8, _MIGRATION_8_NAME, _MIGRATION_8_SHA256))
                    for item in supported_migrations
                }
            if version >= _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION:
                supported_migrations = {
                    (*item, (9, _MIGRATION_9_NAME, _MIGRATION_9_SHA256))
                    for item in supported_migrations
                }
            if version in (
            _PRE_REASONING_SCHEMA_VERSION, _PRE_EFFORT_SCHEMA_VERSION, _CAMPAIGN_SCHEMA_VERSION,
        ):
                supported_migrations = {
                    (*item, (10, _MIGRATION_10_NAME, _MIGRATION_10_SHA256))
                    for item in supported_migrations
                }
            if version >= _PRE_EFFORT_SCHEMA_VERSION:
                supported_migrations = {
                    (*item, (11, sqlite_reasoning.MIGRATION_11_NAME,
                             sqlite_reasoning.MIGRATION_11_SHA256))
                    for item in supported_migrations
                }
            if version == _CAMPAIGN_SCHEMA_VERSION:
                supported_migrations = {
                    (
                        *item,
                        (12, sqlite_effort.MIGRATION_12_NAME, sqlite_effort.MIGRATION_12_SHA256),
                    )
                    for item in supported_migrations
                }
            if migration_rows not in supported_migrations:
                raise SchemaIdentityError("Backup migration identity does not match its version")
            extension_rows = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT extension_name, extension_sha256, predecessor_migration_name, "
                    "predecessor_sql_sha256 FROM authority_schema_extensions "
                    "ORDER BY extension_name"
                )
            )
            expected_extensions = _expected_campaign_extension_rows(
                include_recovery=has_recovery_extension,
                include_purge=has_purge_extension,
            )
            if extension_rows != expected_extensions:
                raise SchemaIdentityError("Backup schema extension identity is invalid")
        elif migration_rows != expected_migrations:
            raise SchemaIdentityError("Backup migration identity does not match its version")
        self._verify_stored_authority(connection)
        sqlite_packages.verify_package_authority(connection)
        if version in (
            _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION,
            _STATE_OWNERSHIP_SCHEMA_VERSION,
            _PRE_BUILDER_CHAT_SCHEMA_VERSION,
            _PRE_CONTINUITY_SCHEMA_VERSION,
            _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
            _PRE_REASONING_SCHEMA_VERSION,
            _PRE_EFFORT_SCHEMA_VERSION,
            _CAMPAIGN_SCHEMA_VERSION,
        ):
            sqlite_campaign.verify_f5_campaign_authority(connection)
        if version >= _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION:
            sqlite_continuity.verify_authority(connection)

    def _validate_backup_envelope(
        self,
        target: Path,
        *,
        expected: _BackupEnvelope | None = None,
        temporary: Path | None = None,
    ) -> _BackupEnvelope:
        if os.name != "nt" or not isinstance(target, Path):
            raise _BackupFailure("target_envelope", "unsupported")
        normalized = self._lexical_backup_path(target)
        text = os.fspath(normalized)
        if text.startswith(("\\\\", "//")) or ":" in normalized.name:
            raise _BackupFailure("target_envelope", "unsupported")
        volume_root = normalized.anchor
        if not volume_root or self._backup_drive_type(volume_root) != _DRIVE_FIXED:
            raise _BackupFailure("target_envelope", "volume_unsupported")
        parent = normalized.parent
        parent_attributes = self._backup_file_attributes(parent)
        if parent_attributes is None or not stat.S_ISDIR(self._backup_stat(parent).st_mode):
            raise _BackupFailure("target_envelope", "parent_invalid")
        self._require_supported_backup_attributes(parent_attributes)
        self._validate_backup_ancestors(parent)
        parent_identity = self._backup_identity(parent)

        target_attributes = self._backup_file_attributes(normalized)
        target_identity: tuple[int, int] | None = None
        if target_attributes is not None:
            self._require_supported_backup_attributes(target_attributes)
            target_stat = self._backup_lstat(normalized)
            if not stat.S_ISREG(target_stat.st_mode):
                raise _BackupFailure("target_envelope", "target_not_regular")
            target_identity = (int(target_stat.st_dev), int(target_stat.st_ino))

        destination_lock = normalized.with_name(f"{normalized.name}.owner.lock")
        if self._backup_path_exists(destination_lock):
            raise _BackupFailure("target_envelope", "destination_owner_lock")

        protected = self._protected_backup_source_paths()
        target_key = self._backup_path_key(normalized)
        for protected_path in protected:
            if target_key == self._backup_path_key(protected_path):
                raise _BackupFailure("target_envelope", "source_alias")
            if target_attributes is not None and self._backup_path_exists(protected_path):
                try:
                    if self._backup_samefile(normalized, protected_path):
                        raise _BackupFailure("target_envelope", "source_alias")
                except _BackupFailure:
                    raise
                except BaseException:
                    raise _BackupFailure("target_envelope", "identity_failed") from None

        if temporary is not None:
            if target_key == self._backup_path_key(temporary):
                raise _BackupFailure("target_envelope", "temporary_alias")
            if target_attributes is not None:
                try:
                    if self._backup_samefile(normalized, temporary):
                        raise _BackupFailure("target_envelope", "temporary_alias")
                except _BackupFailure:
                    raise
                except BaseException:
                    raise _BackupFailure("target_envelope", "identity_failed") from None

        source_identity = self._backup_identity(self._database_path)
        envelope = _BackupEnvelope(
            target=normalized,
            parent_identity=parent_identity,
            target_identity=target_identity,
            source_identity=source_identity,
            volume_root=self._backup_path_key(Path(volume_root)),
        )
        if expected is not None and envelope != expected:
            raise _BackupFailure("final_envelope", "identity_drift")
        return envelope

    @staticmethod
    def _lexical_backup_path(path: Path) -> Path:
        return Path(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _backup_path_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def _protected_backup_source_paths(self) -> tuple[Path, ...]:
        source = self._database_path
        return (
            source,
            Path(f"{source}-wal"),
            Path(f"{source}-shm"),
            Path(f"{source}-journal"),
            source.with_name(f"{source.name}.owner.lock"),
            source.with_name(f"{source.name}.authority-owner.owner.lock"),
        )

    def _validate_backup_ancestors(self, parent: Path) -> None:
        current = parent
        while True:
            attributes = self._backup_file_attributes(current)
            if attributes is None:
                raise _BackupFailure("target_envelope", "ancestor_missing")
            self._require_supported_backup_attributes(attributes)
            if current.parent == current:
                return
            current = current.parent

    @staticmethod
    def _require_supported_backup_attributes(attributes: int) -> None:
        if attributes & _UNSUPPORTED_BACKUP_ATTRIBUTES:
            raise _BackupFailure("target_envelope", "attributes_unsupported")

    def _claim_backup_temporary(self, envelope: _BackupEnvelope) -> _ClaimedBackupTemporary:
        for _attempt in range(_BACKUP_TEMP_ATTEMPTS):
            candidate = self._backup_temp_candidate(envelope.target)
            descriptor: int | None = None
            guard_handle: int | None = None
            identity: tuple[int, int] | None = None
            attributes: int | None = None
            try:
                descriptor = self._exclusive_create_backup_temp(candidate)
            except FileExistsError:
                continue
            except BaseException:
                raise _BackupFailure("temporary_claim", "create_failed") from None
            try:
                try:
                    identity = self._backup_descriptor_identity(descriptor)
                except BaseException:
                    raise _BackupFailure("temporary_claim", "identity_failed") from None
                try:
                    descriptor_stat = os.fstat(descriptor)
                    attributes = self._backup_file_attributes(candidate)
                    if attributes is None or not stat.S_ISREG(descriptor_stat.st_mode):
                        raise _BackupFailure("temporary_claim", "object_invalid")
                    self._require_supported_backup_attributes(attributes)
                    if self._backup_identity(candidate.parent) != envelope.parent_identity:
                        raise _BackupFailure("temporary_claim", "parent_identity_drift")
                    if self._backup_path_key(Path(candidate.anchor)) != envelope.volume_root:
                        raise _BackupFailure("temporary_claim", "volume_drift")
                    if identity == self._backup_path_win32_identity(self._database_path):
                        raise _BackupFailure("temporary_claim", "source_alias")
                except _BackupFailure:
                    raise
                except BaseException:
                    raise _BackupFailure("temporary_claim", "validation_failed") from None
                try:
                    guard_handle = self._open_backup_write_guard(candidate)
                except BaseException:
                    raise _BackupFailure("write_guard_open", "failed") from None
                if guard_handle == _INVALID_HANDLE_VALUE:
                    raise _BackupFailure("write_guard_open", "failed")
                try:
                    guard_identity = self._backup_handle_identity(guard_handle)
                except BaseException:
                    raise _BackupFailure("write_guard_identity", "failed") from None
                if guard_identity != identity:
                    raise _BackupFailure("write_guard_identity", "mismatch")
                try:
                    descriptor_closed = self._close_backup_descriptor(descriptor)
                except BaseException:
                    descriptor_closed = False
                if not descriptor_closed:
                    raise _BackupFailure("temporary_claim", "close_failed")
                descriptor = None
                return _ClaimedBackupTemporary(
                    path=candidate,
                    identity=identity,
                    parent_identity=envelope.parent_identity,
                    volume_root=envelope.volume_root,
                    attributes=attributes,
                    write_guard_handle=guard_handle,
                )
            except BaseException as error:
                failure = self._as_backup_failure(error, "temporary_claim")
                cleanup_codes: list[str] = []
                if descriptor is not None:
                    try:
                        descriptor_closed = self._close_backup_descriptor(descriptor)
                    except BaseException:
                        descriptor_closed = False
                    if not descriptor_closed:
                        cleanup_codes.append("backup_cleanup_claim_descriptor_close_failed")
                if (
                    guard_handle is not None
                    and guard_handle != _INVALID_HANDLE_VALUE
                    and not self._close_backup_handle_safely(guard_handle)
                ):
                    cleanup_codes.append("backup_cleanup_write_guard_close_failed")
                if identity is None or attributes is None:
                    cleanup_codes.append("backup_cleanup_main_identity_failed")
                else:
                    partial = _ClaimedBackupTemporary(
                        path=candidate,
                        identity=identity,
                        parent_identity=envelope.parent_identity,
                        volume_root=envelope.volume_root,
                        attributes=attributes,
                        write_guard_handle=_INVALID_HANDLE_VALUE,
                    )
                    cleanup_codes.extend(self._cleanup_owned_backup(partial))
                if cleanup_codes:
                    failure = failure.with_supplemental(*cleanup_codes)
                raise failure from None
        raise _BackupFailure("temporary_claim", "collision_limit")

    def _backup_temp_candidate(self, target: Path) -> Path:
        token = secrets.token_hex(16)
        return target.with_name(f".{target.name}.backup-{token}.tmp")

    @staticmethod
    def _exclusive_create_backup_temp(candidate: Path) -> int:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        return os.open(candidate, flags, 0o600)

    @staticmethod
    def _close_backup_descriptor(descriptor: int) -> bool:
        os.close(descriptor)
        return True

    def _backup_descriptor_identity(self, descriptor: int) -> tuple[int, int]:
        return self._backup_handle_identity(int(msvcrt.get_osfhandle(descriptor)))

    @staticmethod
    def _backup_handle_identity(handle: int) -> tuple[int, int]:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        get_information: Any = kernel32.GetFileInformationByHandle
        get_information.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        get_information.restype = ctypes.c_int
        information = ctypes.create_string_buffer(52)
        if not get_information(ctypes.c_void_p(handle), information):
            raise OSError(ctypes.get_last_error())
        volume_serial = int(ctypes.c_uint32.from_buffer(information, 28).value)
        file_index_high = int(ctypes.c_uint32.from_buffer(information, 44).value)
        file_index_low = int(ctypes.c_uint32.from_buffer(information, 48).value)
        return volume_serial, (file_index_high << 32) | file_index_low

    def _backup_path_win32_identity(self, path: Path) -> tuple[int, int]:
        handle = self._open_backup_identity_handle(path)
        if handle == _INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error())
        failure: BaseException | None = None
        identity: tuple[int, int] | None = None
        try:
            identity = self._backup_handle_identity(handle)
        except BaseException as error:
            failure = error
        if not self._close_backup_handle_safely(handle) and failure is None:
            failure = OSError("identity handle close failed")
        if failure is not None:
            raise failure from None
        if identity is None:
            raise OSError("identity unavailable")
        return identity

    @staticmethod
    def _create_backup_file_handle(path: Path, access: int, share: int) -> int:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file: Any = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        handle = create_file(
            os.fspath(path),
            access,
            share,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        return int(handle) if handle is not None else int(_INVALID_HANDLE_VALUE)

    @classmethod
    def _open_backup_write_guard(cls, path: Path) -> int:
        return cls._create_backup_file_handle(
            path,
            _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        )

    @classmethod
    def _open_backup_content_seal(cls, path: Path) -> int:
        return cls._create_backup_file_handle(
            path,
            _GENERIC_READ | _GENERIC_WRITE,
            _FILE_SHARE_READ,
        )

    @classmethod
    def _open_backup_identity_handle(cls, path: Path) -> int:
        return cls._create_backup_file_handle(
            path,
            _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        )

    def _acquire_backup_content_seal(self, claimed: _ClaimedBackupTemporary) -> int:
        handle: int | None = None
        try:
            handle = self._open_backup_content_seal(claimed.path)
            if handle == _INVALID_HANDLE_VALUE:
                raise _BackupFailure("content_seal_open", "failed")
            try:
                identity = self._backup_handle_identity(handle)
            except BaseException:
                raise _BackupFailure("content_seal_identity", "failed") from None
            if identity != claimed.identity:
                raise _BackupFailure("content_seal_identity", "mismatch")
            return handle
        except BaseException as error:
            failure = self._as_backup_failure(error, "content_seal")
            if (
                handle is not None
                and handle != _INVALID_HANDLE_VALUE
                and not self._close_backup_handle_safely(handle)
            ):
                failure = failure.with_supplemental("backup_cleanup_content_seal_close_failed")
            raise failure from None

    def _validate_claimed_backup_temporary(
        self,
        claimed: _ClaimedBackupTemporary,
        seal_handle: int,
        envelope: _BackupEnvelope,
    ) -> None:
        try:
            seal_identity = self._backup_handle_identity(seal_handle)
            if seal_identity != claimed.identity:
                raise _BackupFailure("final_envelope", "temporary_identity_drift")
            path_identity = self._backup_path_win32_identity(claimed.path)
            if path_identity != claimed.identity or path_identity != seal_identity:
                raise _BackupFailure("final_envelope", "temporary_identity_drift")
            attributes = self._backup_file_attributes(claimed.path)
            if attributes is None or attributes != claimed.attributes:
                raise _BackupFailure("final_envelope", "temporary_attribute_drift")
            self._require_supported_backup_attributes(attributes)
            if not stat.S_ISREG(self._backup_lstat(claimed.path).st_mode):
                raise _BackupFailure("final_envelope", "temporary_not_regular")
            if self._backup_identity(claimed.path.parent) != claimed.parent_identity:
                raise _BackupFailure("final_envelope", "temporary_parent_drift")
            if self._backup_path_key(Path(claimed.path.anchor)) != claimed.volume_root:
                raise _BackupFailure("final_envelope", "temporary_volume_drift")
            forbidden = (
                *self._protected_backup_source_paths(),
                envelope.target,
                envelope.target.with_name(f"{envelope.target.name}.owner.lock"),
            )
            claimed_key = self._backup_path_key(claimed.path)
            for path in forbidden:
                if claimed_key == self._backup_path_key(path):
                    raise _BackupFailure("final_envelope", "temporary_alias")
                if self._backup_path_exists(path) and self._backup_samefile(claimed.path, path):
                    raise _BackupFailure("final_envelope", "temporary_alias")
        except _BackupFailure:
            raise
        except BaseException:
            raise _BackupFailure("final_envelope", "temporary_validation_failed") from None

    def _remove_prepublication_sidecars(self, temporary: Path) -> None:
        for path, role in self._owned_backup_artifacts(temporary)[1:]:
            try:
                if self._backup_path_exists(path):
                    self._unlink_owned_backup_path(path)
            except BaseException:
                raise _BackupFailure("prepublication_cleanup", f"{role}_unlink_failed") from None
            try:
                absent = self._owned_backup_path_absent(path)
            except BaseException:
                absent = False
            if not absent:
                raise _BackupFailure("prepublication_cleanup", f"{role}_absence_failed")

    def _require_backup_artifacts_absent(
        self,
        artifacts: tuple[tuple[Path, str], ...],
        *,
        phase: str,
    ) -> None:
        for path, role in artifacts:
            try:
                absent = self._owned_backup_path_absent(path)
            except BaseException:
                absent = False
            if not absent:
                raise _BackupFailure(phase, f"{role}_present")

    def _cleanup_owned_backup(self, claimed: _ClaimedBackupTemporary) -> tuple[str, ...]:
        codes: list[str] = []
        for path, role in reversed(self._owned_backup_artifacts(claimed.path)[1:]):
            try:
                if self._backup_path_exists(path):
                    self._unlink_owned_backup_path(path)
            except BaseException:
                codes.append(f"backup_cleanup_{role}_unlink_failed")
            try:
                absent = self._owned_backup_path_absent(path)
            except BaseException:
                absent = False
            if not absent:
                codes.append(f"backup_cleanup_{role}_absence_failed")
        codes.extend(self._cleanup_claimed_backup_main(claimed))
        return tuple(codes)

    def _cleanup_claimed_backup_main(self, claimed: _ClaimedBackupTemporary) -> tuple[str, ...]:
        handle: int | None = None
        codes: list[str] = []
        identity_matched = False
        disposition_requested = False
        try:
            try:
                handle = self._open_backup_cleanup_handle(claimed.path)
            except BaseException:
                codes.append("backup_cleanup_main_identity_failed")
            if handle is not None and handle != _INVALID_HANDLE_VALUE:
                try:
                    identity = self._backup_handle_identity(handle)
                except BaseException:
                    codes.append("backup_cleanup_main_identity_failed")
                else:
                    if identity != claimed.identity:
                        codes.append("backup_cleanup_main_identity_drift")
                    else:
                        identity_matched = True
                        try:
                            self._before_backup_main_cleanup_disposition(claimed.path)
                            disposition_requested = self._delete_backup_handle(handle)
                        except BaseException:
                            disposition_requested = False
                        if not disposition_requested:
                            codes.append("backup_cleanup_main_disposition_failed")
            elif handle == _INVALID_HANDLE_VALUE and ctypes.get_last_error() not in {2, 3}:
                codes.append("backup_cleanup_main_identity_failed")
        finally:
            if (
                handle is not None
                and handle != _INVALID_HANDLE_VALUE
                and not self._close_backup_handle_safely(handle)
            ):
                codes.append("backup_cleanup_main_close_failed")
        if identity_matched and disposition_requested:
            try:
                absent = self._owned_backup_path_absent(claimed.path)
            except BaseException:
                absent = False
            if not absent:
                codes.append("backup_cleanup_main_absence_failed")
        return tuple(codes)

    @classmethod
    def _open_backup_cleanup_handle(cls, path: Path) -> int:
        return cls._create_backup_file_handle(
            path,
            _DELETE_ACCESS | _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        )

    @staticmethod
    def _delete_backup_handle(handle: int) -> bool:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        set_information: Any = kernel32.SetFileInformationByHandle
        set_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        set_information.restype = ctypes.c_int
        delete_file = ctypes.c_int(1)
        return bool(
            set_information(
                ctypes.c_void_p(handle),
                _FILE_DISPOSITION_INFO,
                ctypes.byref(delete_file),
                ctypes.sizeof(delete_file),
            )
        )

    def _before_backup_main_cleanup_disposition(self, _path: Path) -> None:
        """Deterministic seam after identity proof and before handle-bound disposition."""

    @classmethod
    def _close_backup_handle_safely(cls, handle: int) -> bool:
        try:
            return cls._close_backup_handle(handle)
        except BaseException:
            return False

    @staticmethod
    def _owned_backup_artifacts(temporary: Path) -> tuple[tuple[Path, str], ...]:
        return (
            (temporary, "main"),
            (Path(f"{temporary}-wal"), "wal"),
            (Path(f"{temporary}-shm"), "shm"),
            (Path(f"{temporary}-journal"), "journal"),
            (temporary.with_name(f"{temporary.name}.owner.lock"), "owner_lock"),
        )

    @staticmethod
    def _backup_path_exists(path: Path) -> bool:
        return os.path.lexists(path)

    @staticmethod
    def _owned_backup_path_absent(path: Path) -> bool:
        return not os.path.lexists(path)

    @staticmethod
    def _unlink_owned_backup_path(path: Path) -> None:
        path.unlink()

    @staticmethod
    def _backup_stat(path: Path) -> os.stat_result:
        return path.stat()

    @staticmethod
    def _backup_lstat(path: Path) -> os.stat_result:
        return path.lstat()

    @staticmethod
    def _backup_samefile(left: Path, right: Path) -> bool:
        return os.path.samefile(left, right)

    def _backup_identity(self, path: Path) -> tuple[int, int]:
        value = self._backup_stat(path)
        return int(value.st_dev), int(value.st_ino)

    @staticmethod
    def _backup_drive_type(volume_root: str) -> int:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        get_drive_type: Any = kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        return int(get_drive_type(volume_root))

    @staticmethod
    def _backup_file_attributes(path: Path) -> int | None:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        get_attributes: Any = kernel32.GetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        attributes = int(get_attributes(os.fspath(path)))
        if attributes == _INVALID_FILE_ATTRIBUTES:
            error = ctypes.get_last_error()
            if error in {2, 3}:
                return None
            raise OSError(error)
        return attributes

    @staticmethod
    def _flush_backup_handle(handle: int) -> bool:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        flush: Any = kernel32.FlushFileBuffers
        flush.argtypes = [ctypes.c_void_p]
        flush.restype = ctypes.c_int
        return bool(flush(ctypes.c_void_p(handle)))

    @staticmethod
    def _close_backup_handle(handle: int) -> bool:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        close: Any = kernel32.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        return bool(close(ctypes.c_void_p(handle)))

    @staticmethod
    def _move_backup_file(source: Path, target: Path, flags: int) -> bool:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        move: Any = kernel32.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        return bool(move(os.fspath(source), os.fspath(target), flags))

    @staticmethod
    def _as_backup_failure(error: BaseException, phase: str) -> _BackupFailure:
        if isinstance(error, _BackupFailure):
            return error
        return _BackupFailure(phase, "failed")

    def campaign_schema_is_active(self) -> bool:
        """Report whether new participant writes are governed by exact F5 proof."""

        connection = self._connect()
        try:
            return (
                int(connection.execute("PRAGMA user_version").fetchone()[0])
                == _CAMPAIGN_SCHEMA_VERSION
            )
        finally:
            connection.close()

    def legacy_participants_are_admitted(
        self,
        package_lock: PackageLock,
        participants: Iterable[Participant],
    ) -> bool:
        """Preserve format-1 participant behavior without weakening formats 2/3."""

        participants = tuple(participants)
        lock_entries = {(entry.package_id, entry.version): entry for entry in package_lock.entries}
        connection = self._connect_read_only()
        try:
            for participant in participants:
                if participant.validation_status is ParticipantValidationStatus.VALIDATED:
                    continue
                entry = lock_entries.get((participant.package_id, participant.package_version))
                if entry is None:
                    return False
                row = connection.execute(
                    "SELECT canonical_json FROM package_manifests WHERE package_id = ? "
                    "AND version = ? AND content_sha256 = ? AND status = 'published'",
                    (entry.package_id, entry.version, entry.sha256),
                ).fetchone()
                if row is None:
                    return False
                manifest = _load_payload(str(row["canonical_json"]))
                if "participant_artifacts" in manifest:
                    return False
            return True
        finally:
            connection.close()

    def package_lock_requires_state_ownership(self, package_lock: PackageLock) -> bool:
        """Return whether an exact lock contains a format-3 declaration package."""

        connection = self._connect_read_only()
        try:
            return any(
                sqlite_state_ownership.package_uses_content_declarations(
                    connection,
                    entry.package_id,
                    entry.version,
                    entry.sha256,
                )
                for entry in package_lock.entries
            )
        finally:
            connection.close()

    def _verify_exact_schema_identity(
        self,
        connection: sqlite3.Connection,
        *,
        version: int | None = None,
    ) -> int:
        """Require the canonical exact schema, migration, and extension identity."""

        if version is None:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (
            _SCHEMA_VERSION,
            _SESSION_SCHEMA_VERSION,
            _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION,
            _STATE_OWNERSHIP_SCHEMA_VERSION,
            _PRE_BUILDER_CHAT_SCHEMA_VERSION,
            _PRE_CONTINUITY_SCHEMA_VERSION,
            _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
            _PRE_REASONING_SCHEMA_VERSION,
            _PRE_EFFORT_SCHEMA_VERSION,
            _CAMPAIGN_SCHEMA_VERSION,
        ):
            raise SchemaIdentityError("Authority schema identity is incomplete")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        has_recovery_extension = False
        has_purge_extension = False
        if version in (
            _PRE_REASONING_SCHEMA_VERSION, _PRE_EFFORT_SCHEMA_VERSION, _CAMPAIGN_SCHEMA_VERSION,
        ):
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
                | _BUILDER_CHAT_TABLES
                | _CONTINUITY_TABLES
            )
            if version >= _PRE_EFFORT_SCHEMA_VERSION:
                required_tables |= {"provider_private_records"}
            expected_identity = (
                _EXPECTED_EFFORT_SCHEMA_IDENTITY if version == _CAMPAIGN_SCHEMA_VERSION
                else _EXPECTED_REASONING_SCHEMA_IDENTITY if version == _PRE_EFFORT_SCHEMA_VERSION
                else _EXPECTED_DRAFT_LIFECYCLE_SCHEMA_IDENTITY
            )
            has_recovery_extension = True
            has_purge_extension = connection.execute(
                "SELECT 1 FROM authority_schema_extensions WHERE extension_name = ?",
                (_PURGE_EXTENSION_NAME,),
            ).fetchone() is not None
        elif version == _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
                | _BUILDER_CHAT_TABLES
                | _CONTINUITY_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_CONTINUITY_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_CONTINUITY_SCHEMA_IDENTITY
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_CONTINUITY_SCHEMA_IDENTITY
            has_recovery_extension = True
        elif version == _PRE_CONTINUITY_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
                | _BUILDER_CHAT_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_BUILDER_CHAT_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_BUILDER_CHAT_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_BUILDER_CHAT_SCHEMA_IDENTITY
                has_recovery_extension = True
        elif version == _PRE_BUILDER_CHAT_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES
                | _SESSION_TABLES
                | _CAMPAIGN_TABLES
                | _STATE_OWNERSHIP_TABLES
                | _MECHANICS_CHOICE_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_MECHANICS_CHOICE_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_MECHANICS_CHOICE_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_MECHANICS_CHOICE_SCHEMA_IDENTITY
                has_recovery_extension = True
        elif version == _STATE_OWNERSHIP_SCHEMA_VERSION:
            required_tables = (
                _REQUIRED_TABLES | _SESSION_TABLES | _CAMPAIGN_TABLES | _STATE_OWNERSHIP_TABLES
            )
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_STATE_OWNERSHIP_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_STATE_OWNERSHIP_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            else:
                expected_identity = _EXPECTED_STATE_OWNERSHIP_SCHEMA_IDENTITY
                has_recovery_extension = True
        elif version == _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION:
            required_tables = _REQUIRED_TABLES | _SESSION_TABLES | _CAMPAIGN_TABLES
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY
                has_recovery_extension = True
                has_purge_extension = True
            elif live_identity == _EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY:
                expected_identity = _EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY
                has_recovery_extension = True
            else:
                expected_identity = _EXPECTED_CAMPAIGN_SCHEMA_IDENTITY
        elif version == _SESSION_SCHEMA_VERSION:
            required_tables = _REQUIRED_TABLES | _SESSION_TABLES
            expected_identity = _EXPECTED_SESSION_SCHEMA_IDENTITY
        else:
            required_tables = _REQUIRED_TABLES
            expected_identity = _EXPECTED_SCHEMA_IDENTITY
        if not required_tables.issubset(tables):
            raise SchemaIdentityError("Authority schema identity does not match its version")
        if _schema_identity(connection) != expected_identity:
            raise SchemaIdentityError("Authority schema structure does not match its version")
        migration_rows = tuple(
            (int(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
        expected_migrations: tuple[tuple[int, str, str], ...] = (
            (1, _MIGRATION_1_NAME, _MIGRATION_1_SHA256),
            (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
            (3, _MIGRATION_3_NAME, _MIGRATION_3_SHA256),
        )
        if version >= _SESSION_SCHEMA_VERSION:
            expected_migrations += ((4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256),)
        if version in (
            _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION,
            _STATE_OWNERSHIP_SCHEMA_VERSION,
            _PRE_BUILDER_CHAT_SCHEMA_VERSION,
            _PRE_CONTINUITY_SCHEMA_VERSION,
            _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
            _PRE_REASONING_SCHEMA_VERSION,
            _PRE_EFFORT_SCHEMA_VERSION,
            _CAMPAIGN_SCHEMA_VERSION,
        ):
            supported_campaign_migrations = {
                (*expected_migrations, (5, _MIGRATION_5_NAME, _MIGRATION_5_SHA256)),
                (
                    *expected_migrations,
                    (5, _MIGRATION_5_NAME, _PREDECESSOR_MIGRATION_5_SHA256),
                ),
            }
            if version in (
                _STATE_OWNERSHIP_SCHEMA_VERSION,
                _PRE_BUILDER_CHAT_SCHEMA_VERSION,
                _PRE_CONTINUITY_SCHEMA_VERSION,
                _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                _PRE_REASONING_SCHEMA_VERSION,
                _PRE_EFFORT_SCHEMA_VERSION,
                _CAMPAIGN_SCHEMA_VERSION,
            ):
                supported_campaign_migrations = {
                    (*item, (6, _MIGRATION_6_NAME, _MIGRATION_6_SHA256))
                    for item in supported_campaign_migrations
                }
            if version in (
                _PRE_BUILDER_CHAT_SCHEMA_VERSION,
                _PRE_CONTINUITY_SCHEMA_VERSION,
                _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                _PRE_REASONING_SCHEMA_VERSION,
                _PRE_EFFORT_SCHEMA_VERSION,
                _CAMPAIGN_SCHEMA_VERSION,
            ):
                supported_campaign_migrations = {
                    (*item, (7, _MIGRATION_7_NAME, _MIGRATION_7_SHA256))
                    for item in supported_campaign_migrations
                }
            if version in (
                _PRE_CONTINUITY_SCHEMA_VERSION,
                _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                _PRE_REASONING_SCHEMA_VERSION,
                _PRE_EFFORT_SCHEMA_VERSION,
                _CAMPAIGN_SCHEMA_VERSION,
            ):
                supported_campaign_migrations = {
                    (*item, (8, _MIGRATION_8_NAME, _MIGRATION_8_SHA256))
                    for item in supported_campaign_migrations
                }
            if version >= _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION:
                supported_campaign_migrations = {
                    (*item, (9, _MIGRATION_9_NAME, _MIGRATION_9_SHA256))
                    for item in supported_campaign_migrations
                }
            if version in (
            _PRE_REASONING_SCHEMA_VERSION, _PRE_EFFORT_SCHEMA_VERSION, _CAMPAIGN_SCHEMA_VERSION,
        ):
                supported_campaign_migrations = {
                    (*item, (10, _MIGRATION_10_NAME, _MIGRATION_10_SHA256))
                    for item in supported_campaign_migrations
                }
            if version >= _PRE_EFFORT_SCHEMA_VERSION:
                supported_campaign_migrations = {
                    (*item, (11, sqlite_reasoning.MIGRATION_11_NAME,
                             sqlite_reasoning.MIGRATION_11_SHA256))
                    for item in supported_campaign_migrations
                }
            if version == _CAMPAIGN_SCHEMA_VERSION:
                supported_campaign_migrations = {
                    (
                        *item,
                        (12, sqlite_effort.MIGRATION_12_NAME, sqlite_effort.MIGRATION_12_SHA256),
                    )
                    for item in supported_campaign_migrations
                }
            if migration_rows not in supported_campaign_migrations:
                raise SchemaIdentityError("Authority migration identity does not match its version")
            extension_rows = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT extension_name, extension_sha256, predecessor_migration_name, "
                    "predecessor_sql_sha256 FROM authority_schema_extensions "
                    "ORDER BY extension_name"
                )
            )
            if extension_rows != _expected_campaign_extension_rows(
                include_recovery=has_recovery_extension,
                include_purge=has_purge_extension,
            ):
                raise SchemaIdentityError("Authority schema extension identity is invalid")
            if (
                version
                in (
                    _STATE_OWNERSHIP_SCHEMA_VERSION,
                    _PRE_BUILDER_CHAT_SCHEMA_VERSION,
                    _PRE_CONTINUITY_SCHEMA_VERSION,
                    _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                    _PRE_REASONING_SCHEMA_VERSION,
                    _PRE_EFFORT_SCHEMA_VERSION,
                    _CAMPAIGN_SCHEMA_VERSION,
                )
                and sqlite_state_ownership.trigger_identity(connection)
                != sqlite_state_ownership.EXPECTED_TRIGGER_IDENTITY
            ):
                raise SchemaIdentityError("Authority state ownership trigger identity is invalid")
        elif migration_rows != expected_migrations:
            raise SchemaIdentityError("Authority migration identity does not match its version")
        return version

    def _migrate(self) -> None:
        if self._database_path.exists() and self._database_path.stat().st_size == 0:
            raise SchemaIdentityError("Authority schema identity is incomplete")
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > _CAMPAIGN_SCHEMA_VERSION:
                raise SchemaIdentityError("Authority schema identity is unsupported")
            if version == _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION:
                self._verify_exact_schema_identity(connection, version=version)
                self._preserve_pre_migration_source(version)
                live_identity = _schema_identity(connection)
                if live_identity not in {
                    _EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY,
                    _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY,
                }:
                    self._upgrade_predecessor_campaign_schema(connection)
                self._upgrade_w12_recovery_extension(connection)
                self._upgrade_state_ownership_schema(connection)
                version = _STATE_OWNERSHIP_SCHEMA_VERSION
            if version == _STATE_OWNERSHIP_SCHEMA_VERSION:
                self._verify_exact_schema_identity(connection, version=version)
                self._preserve_pre_migration_source(version)
                self._upgrade_mechanics_choice_schema(connection)
                version = _PRE_BUILDER_CHAT_SCHEMA_VERSION
            if version == _PRE_BUILDER_CHAT_SCHEMA_VERSION:
                self._verify_exact_schema_identity(connection, version=version)
                self._preserve_pre_migration_source(version)
                self._upgrade_builder_chat_schema(connection)
                version = _PRE_CONTINUITY_SCHEMA_VERSION
            if version == _PRE_CONTINUITY_SCHEMA_VERSION:
                self._verify_exact_schema_identity(connection, version=version)
                self._preserve_pre_migration_source(version)
                self._upgrade_continuity_schema(connection)
                version = _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION
            if version == _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION:
                self._verify_exact_schema_identity(connection, version=version)
                self._preserve_pre_migration_source(version)
                self._upgrade_draft_lifecycle_schema(connection)
                version = _PRE_REASONING_SCHEMA_VERSION
            if version == _PRE_REASONING_SCHEMA_VERSION:
                self._verify_exact_schema_identity(connection, version=version)
                self._preserve_pre_migration_source(version)
                connection.execute("BEGIN IMMEDIATE")
                self._apply_reasoning_migration(connection)
                self._before_reasoning_migration_commit()
                connection.commit()
                version = _PRE_EFFORT_SCHEMA_VERSION
            if version == _PRE_EFFORT_SCHEMA_VERSION:
                self._verify_exact_schema_identity(connection, version=version)
                self._preserve_pre_migration_source(version)
                connection.execute("BEGIN IMMEDIATE")
                self._apply_effort_migration(connection)
                connection.commit()
                version = _CAMPAIGN_SCHEMA_VERSION
            if version == 0:
                connection.executescript("BEGIN IMMEDIATE;\n" + _MIGRATION_1)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_name, sql_sha256) "
                    "VALUES (?, ?, ?)",
                    (1, _MIGRATION_1_NAME, _MIGRATION_1_SHA256),
                )
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
                version = 1
            if version == 1:
                connection.executescript("BEGIN IMMEDIATE;\n" + _MIGRATION_2)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_name, sql_sha256) "
                    "VALUES (?, ?, ?)",
                    (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
                )
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
                version = 2
            if version == 2:
                connection.executescript("BEGIN IMMEDIATE;\n" + _MIGRATION_3)
                sqlite_packages.migrate_legacy_campaigns(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_name, sql_sha256) "
                    "VALUES (?, ?, ?)",
                    (3, _MIGRATION_3_NAME, _MIGRATION_3_SHA256),
                )
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
                version = 3
            if version == _SESSION_SCHEMA_VERSION:
                # Campaign schema is applied lazily by campaign operations.
                pass
            self._verify_exact_schema_identity(connection, version=version)
            sqlite_packages.clear_stale_read_leases(connection)
            self._verify_stored_authority(connection)
            sqlite_packages.verify_package_authority(connection)
            if version == _CAMPAIGN_SCHEMA_VERSION:
                sqlite_campaign.verify_f5_campaign_authority(connection)
                sqlite_continuity.verify_authority(connection)
                # Exclusive owner startup cannot inherit a live provider attempt.
                connection.execute(
                    "UPDATE provider_private_records SET status = 'interrupted' "
                    "WHERE status = 'partial'"
                )
            journal_row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            try:
                journal_mode = journal_row[0] if journal_row is not None else None
            except (IndexError, KeyError, TypeError):
                journal_mode = None
            if journal_mode is None or str(journal_mode).casefold() != "wal":
                raise SchemaIdentityError("Authority schema identity could not be verified")
        except SchemaIdentityError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise SchemaIdentityError("Authority schema identity could not be verified") from error
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            raise SchemaIdentityError("Authority stored integrity could not be verified") from error
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _upgrade_predecessor_campaign_schema(self, connection: sqlite3.Connection) -> None:
        """Converge exact predecessor-v5 and Probe 01 authority to lifecycle v3."""

        live_identity = _schema_identity(connection)
        if live_identity == _EXPECTED_CAMPAIGN_SCHEMA_IDENTITY:
            return
        predecessor_migrations = (
            (1, _MIGRATION_1_NAME, _MIGRATION_1_SHA256),
            (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
            (3, _MIGRATION_3_NAME, _MIGRATION_3_SHA256),
            (4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256),
            (5, _MIGRATION_5_NAME, _PREDECESSOR_MIGRATION_5_SHA256),
        )
        current_migrations = (
            *predecessor_migrations[:-1],
            (5, _MIGRATION_5_NAME, _MIGRATION_5_SHA256),
        )
        migration_rows = tuple(
            (int(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
        connection.execute("BEGIN IMMEDIATE")
        if live_identity == _EXPECTED_PREDECESSOR_CAMPAIGN_SCHEMA_IDENTITY:
            if migration_rows != predecessor_migrations:
                raise SchemaIdentityError("Predecessor campaign schema identity is not exact")
            for statement in _CAMPAIGN_IDENTITY_DELTA.split(";"):
                if statement.strip():
                    connection.execute(statement)
            self._insert_campaign_identity_extension(connection)
        elif live_identity == _EXPECTED_PROBE01_CAMPAIGN_SCHEMA_IDENTITY:
            if migration_rows not in {predecessor_migrations, current_migrations}:
                raise SchemaIdentityError("Probe 01 campaign migration identity is not exact")
            extension = connection.execute(
                "SELECT extension_name, extension_sha256, predecessor_migration_name, "
                "predecessor_sql_sha256 FROM authority_schema_extensions"
            ).fetchone()
            if extension is None or tuple(extension) != (
                _CAMPAIGN_IDENTITY_EXTENSION_NAME,
                _CAMPAIGN_IDENTITY_DELTA_SHA256,
                _MIGRATION_5_NAME,
                _PREDECESSOR_MIGRATION_5_SHA256,
            ):
                raise SchemaIdentityError("Probe 01 campaign extension identity is not exact")
        else:
            raise SchemaIdentityError("Predecessor campaign schema identity is not exact")
        sqlite_campaign.apply_lifecycle_extension(connection)
        connection.execute(
            "INSERT INTO authority_schema_extensions "
            "(extension_name, extension_sha256, predecessor_migration_name, "
            "predecessor_sql_sha256) VALUES (?, ?, ?, ?)",
            (
                _LIFECYCLE_EXTENSION_NAME,
                _LIFECYCLE_EXTENSION_SHA256,
                _CAMPAIGN_IDENTITY_EXTENSION_NAME,
                _CAMPAIGN_IDENTITY_DELTA_SHA256,
            ),
        )
        self._before_campaign_identity_delta_commit()
        connection.commit()

    def _upgrade_w12_recovery_extension(self, connection: sqlite3.Connection) -> None:
        """Advance only exact W11 v5 authority to the additive W12 receipt schema."""

        live_identity = _schema_identity(connection)
        if live_identity in {
            _EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY,
            _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY,
        }:
            return
        if live_identity != _EXPECTED_CAMPAIGN_SCHEMA_IDENTITY:
            raise SchemaIdentityError("W12 predecessor campaign schema identity is not exact")
        connection.execute("BEGIN IMMEDIATE")
        extension_rows = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT extension_name, extension_sha256, predecessor_migration_name, "
                "predecessor_sql_sha256 FROM authority_schema_extensions "
                "ORDER BY extension_name"
            )
        )
        expected_extensions = tuple(
            sorted(
                (
                    (
                        _CAMPAIGN_IDENTITY_EXTENSION_NAME,
                        _CAMPAIGN_IDENTITY_DELTA_SHA256,
                        _MIGRATION_5_NAME,
                        _PREDECESSOR_MIGRATION_5_SHA256,
                    ),
                    (
                        _LIFECYCLE_EXTENSION_NAME,
                        _LIFECYCLE_EXTENSION_SHA256,
                        _CAMPAIGN_IDENTITY_EXTENSION_NAME,
                        _CAMPAIGN_IDENTITY_DELTA_SHA256,
                    ),
                )
            )
        )
        if extension_rows != expected_extensions:
            raise SchemaIdentityError("W12 predecessor extension identity is not exact")
        sqlite_recovery.apply_recovery_extension(
            connection,
            predecessor_name=_LIFECYCLE_EXTENSION_NAME,
            predecessor_sha256=_LIFECYCLE_EXTENSION_SHA256,
        )
        self._before_recovery_extension_commit()
        connection.commit()

    def _upgrade_state_ownership_schema(self, connection: sqlite3.Connection) -> None:
        """Advance only exact v5 authority to deterministic namespace ownership."""

        self._verify_exact_schema_identity(
            connection,
            version=_PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION,
        )
        connection.execute("BEGIN IMMEDIATE")
        sqlite_state_ownership.apply_migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (6, _MIGRATION_6_NAME, _MIGRATION_6_SHA256),
        )
        connection.execute("PRAGMA user_version = 6")
        self._verify_exact_schema_identity(connection, version=_STATE_OWNERSHIP_SCHEMA_VERSION)
        self._before_state_ownership_migration_commit()
        connection.commit()

    def _upgrade_mechanics_choice_schema(self, connection: sqlite3.Connection) -> None:
        """Advance exact schema 6 to durable mechanics-choice reservations."""

        self._verify_exact_schema_identity(
            connection,
            version=_STATE_OWNERSHIP_SCHEMA_VERSION,
        )
        connection.execute("BEGIN IMMEDIATE")
        sqlite_mechanics_choice.apply_migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (7, _MIGRATION_7_NAME, _MIGRATION_7_SHA256),
        )
        connection.execute("PRAGMA user_version = 7")
        self._verify_exact_schema_identity(connection, version=_PRE_BUILDER_CHAT_SCHEMA_VERSION)
        self._before_mechanics_choice_migration_commit()
        connection.commit()

    def _upgrade_builder_chat_schema(self, connection: sqlite3.Connection) -> None:
        """Advance exact schema 7 with campaign-independent Builder conversations."""

        self._verify_exact_schema_identity(
            connection,
            version=_PRE_BUILDER_CHAT_SCHEMA_VERSION,
        )
        connection.execute("BEGIN IMMEDIATE")
        for statement in _MIGRATION_8.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (8, _MIGRATION_8_NAME, _MIGRATION_8_SHA256),
        )
        connection.execute("PRAGMA user_version = 8")
        self._verify_exact_schema_identity(connection, version=_PRE_CONTINUITY_SCHEMA_VERSION)
        self._before_builder_chat_migration_commit()
        connection.commit()

    def _upgrade_continuity_schema(self, connection: sqlite3.Connection) -> None:
        """Advance exact schema 8 with campaign-owned long-term continuity."""

        self._verify_exact_schema_identity(
            connection,
            version=_PRE_CONTINUITY_SCHEMA_VERSION,
        )
        connection.execute("BEGIN IMMEDIATE")
        sqlite_continuity.apply_migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (9, _MIGRATION_9_NAME, _MIGRATION_9_SHA256),
        )
        connection.execute("PRAGMA user_version = 9")
        self._verify_exact_schema_identity(
            connection, version=_PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION
        )
        self._before_continuity_migration_commit()
        connection.commit()

    def _upgrade_draft_lifecycle_schema(self, connection: sqlite3.Connection) -> None:
        """Advance exact schema 9 with unbound Draft lifecycle receipts."""

        self._verify_exact_schema_identity(
            connection, version=_PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION
        )
        connection.execute("BEGIN IMMEDIATE")
        sqlite_draft_lifecycle.apply_migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (10, _MIGRATION_10_NAME, _MIGRATION_10_SHA256),
        )
        connection.execute("PRAGMA user_version = 10")
        self._verify_exact_schema_identity(connection, version=_PRE_REASONING_SCHEMA_VERSION)
        connection.commit()

    def _apply_reasoning_migration(self, connection: sqlite3.Connection) -> None:
        """Advance the exact schema-10 predecessor in the caller-owned transaction."""
        self._verify_exact_schema_identity(connection, version=_PRE_REASONING_SCHEMA_VERSION)
        sqlite_reasoning.apply_migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (11, sqlite_reasoning.MIGRATION_11_NAME, sqlite_reasoning.MIGRATION_11_SHA256),
        )
        connection.execute("PRAGMA user_version = 11")
        self._verify_exact_schema_identity(connection, version=_PRE_EFFORT_SCHEMA_VERSION)

    def _apply_effort_migration(self, connection: sqlite3.Connection) -> None:
        """Add saved reasoning preferences without rewriting old metadata or history."""
        self._verify_exact_schema_identity(connection, version=_PRE_EFFORT_SCHEMA_VERSION)
        sqlite_effort.apply_migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (12, sqlite_effort.MIGRATION_12_NAME, sqlite_effort.MIGRATION_12_SHA256),
        )
        connection.execute("PRAGMA user_version = 12")
        self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)

    def _before_reasoning_migration_commit(self) -> None:
        """Deterministic fault seam for additive private-record migration rollback."""

    def _before_continuity_migration_commit(self) -> None:
        """Private deterministic fault seam for schema-9 migration rollback."""

    def _before_builder_chat_migration_commit(self) -> None:
        """Private deterministic fault seam for schema-8 migration rollback."""

    def _before_mechanics_choice_migration_commit(self) -> None:
        """Private deterministic fault seam for schema-7 migration rollback."""

    def _before_state_ownership_migration_commit(self) -> None:
        """Private deterministic fault seam for schema-6 migration rollback."""

    @staticmethod
    def _insert_campaign_identity_extension(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO authority_schema_extensions "
            "(extension_name, extension_sha256, predecessor_migration_name, "
            "predecessor_sql_sha256) VALUES (?, ?, ?, ?)",
            (
                _CAMPAIGN_IDENTITY_EXTENSION_NAME,
                _CAMPAIGN_IDENTITY_DELTA_SHA256,
                _MIGRATION_5_NAME,
                _PREDECESSOR_MIGRATION_5_SHA256,
            ),
        )

    def _before_campaign_identity_delta_commit(self) -> None:
        """Private deterministic fault seam for exact predecessor-v5 rollback."""

    def _before_recovery_extension_commit(self) -> None:
        """Private deterministic fault seam for additive W12 extension rollback."""

    def _verify_stored_authority(self, connection: sqlite3.Connection) -> None:
        integrity_rows = tuple(
            str(row[0]).casefold() for row in connection.execute("PRAGMA integrity_check")
        )
        if integrity_rows != ("ok",):
            raise SchemaIdentityError("Authority stored integrity does not match F1")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaIdentityError("Authority stored integrity does not match F1")
        self._verify_stored_identities(connection)
        self._verify_session_identities(connection)
        sqlite_recovery.verify_recovery_receipts(connection)
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) in (
            _STATE_OWNERSHIP_SCHEMA_VERSION,
            _PRE_REASONING_SCHEMA_VERSION,
            _PRE_EFFORT_SCHEMA_VERSION,
            _CAMPAIGN_SCHEMA_VERSION,
        ):
            sqlite_state_ownership.verify_authority(connection)

        for package in connection.execute(
            "SELECT package_id, version, sha256, capabilities_json FROM packages "
            "ORDER BY package_id, version, sha256"
        ):
            capabilities_value = _load_json(str(package["capabilities_json"]))
            if (
                not isinstance(capabilities_value, Sequence)
                or isinstance(capabilities_value, (str, bytes, bytearray))
                or not all(isinstance(item, str) for item in capabilities_value)
            ):
                raise SchemaIdentityError("Authority stored integrity does not match F1")
            dependency_rows = tuple(
                connection.execute(
                    "SELECT dependency_order, dependency_id, dependency_version, "
                    "dependency_sha256, dependency_kind, dependency_namespace "
                    "FROM package_dependencies "
                    "WHERE package_id = ? AND version = ? AND sha256 = ? "
                    "ORDER BY dependency_order",
                    (package["package_id"], package["version"], package["sha256"]),
                )
            )
            if tuple(int(row["dependency_order"]) for row in dependency_rows) != tuple(
                range(len(dependency_rows))
            ):
                raise SchemaIdentityError("Authority stored integrity does not match F1")
            PackageLockEntry(
                package_id=str(package["package_id"]),
                version=str(package["version"]),
                sha256=str(package["sha256"]),
                role=PackageRole.DEPENDENCY,
                load_order=0,
                dependencies=tuple(
                    PackageDependency(
                        package_id=str(row["dependency_id"]),
                        version=str(row["dependency_version"]),
                        sha256=str(row["dependency_sha256"]),
                        kind=str(row["dependency_kind"]),
                        namespace=str(row["dependency_namespace"]),
                    )
                    for row in dependency_rows
                ),
                capabilities=tuple(str(item) for item in capabilities_value),
            )

        for campaign in connection.execute(
            "SELECT campaign_id, lifecycle, revision, writer, lock_hash, current_lock_id "
            "FROM campaigns ORDER BY campaign_id"
        ):
            campaign_id = str(campaign["campaign_id"])
            if campaign["current_lock_id"] is None:
                if (
                    str(campaign["lock_hash"]) != _UNBOUND_CAMPAIGN_LOCK_HASH
                    or not sqlite_draft_history.has_only_draft_lifecycle_history(
                        connection, campaign_id, int(campaign["revision"])
                    )
                    or (str(campaign["lifecycle"]), str(campaign["writer"])) not in {
                        ("active", "builder"), ("archived", "none"), ("tombstoned", "none"),
                    }
                    or connection.execute(
                        "SELECT 1 FROM turns WHERE campaign_id = ? LIMIT 1", (campaign_id,)
                    ).fetchone()
                    is not None
                    or connection.execute(
                        "SELECT 1 FROM campaign_locks WHERE campaign_id = ? LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    is not None
                    or connection.execute(
                        "SELECT 1 FROM agent_session_messages AS message "
                        "JOIN agent_sessions AS session ON session.session_id = message.session_id "
                        "WHERE session.campaign_id = ? AND message.kind = 'evidence' "
                        "AND message.content_json LIKE '%campaign_interview_state%' LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    is None
                ):
                    raise SchemaIdentityError("Authority stored integrity does not match F1")
                package_lock = PackageLock(entries=())
            else:
                package_lock = self._read_lock(connection, campaign_id)
                if _lock_representation(package_lock) != str(campaign["lock_hash"]):
                    raise SchemaIdentityError("Authority stored integrity does not match F1")
            self._verify_campaign_authority(
                connection,
                campaign_id=campaign_id,
                current_lifecycle=str(campaign["lifecycle"]),
                current_revision=int(campaign["revision"]),
                current_writer=str(campaign["writer"]),
                package_lock=package_lock,
            )
        if "campaign_operations" in {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }:
            self._verify_construction_authority(connection)

    def _verify_construction_authority(self, connection: sqlite3.Connection) -> None:
        """Reconcile every Probe 01 receipt, basis, construction state, and revision cause."""

        invalid = "Authority stored construction integrity does not match F5 Probe 01"
        try:
            operations = {
                str(row["operation_id"]): row
                for row in connection.execute(
                    "SELECT * FROM campaign_operations ORDER BY operation_id"
                )
            }
            if len(operations) != int(
                connection.execute("SELECT COUNT(*) FROM campaign_operations").fetchone()[0]
            ):
                raise ValueError("Campaign operation identities are duplicated")
            seen_operations: set[str] = set()
            for operation_id, operation in operations.items():
                payload = _load_payload(str(operation["semantic_payload_json"]))
                operation_kind = str(operation["operation_kind"])
                if (
                    payload.get("operation_id") != operation_id
                    or payload.get("operation_kind") != operation_kind
                    or operation_kind
                    not in {
                        "confirm_new_campaign",
                        "begin_campaign_construction",
                        "complete_campaign_construction",
                        "rename_campaign",
                        "archive_campaign",
                        "restore_campaign",
                        "trash_campaign",
                        "purge_campaign",
                        "commit_campaign_continuity",
                    }
                    or hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
                    != str(operation["semantic_payload_sha256"])
                    or not canonical_payload_is_safe(payload)
                ):
                    raise ValueError("Campaign operation semantic identity is invalid")
                result = self._campaign_operation_result(operation)
                if operation_kind == "purge_campaign":
                    campaign_id = str(operation["campaign_id"])
                    if (
                        not isinstance(result, CampaignPurgeResult)
                        or set(payload)
                        != {"operation_id", "operation_kind", "campaign_id", "confirmation"}
                        or payload.get("campaign_id") != campaign_id
                        or payload.get("confirmation") != f"permanently purge {campaign_id}"
                        or operation["basis_turn_id"] is not None
                        or operation["basis_lock_id"] is not None
                        or int(operation["resulting_revision"])
                        != int(operation["basis_revision"]) + 1
                    ):
                        raise ValueError("Campaign purge receipt authority is invalid")
                    seen_operations.add(operation_id)
                    continue
                turn = connection.execute(
                    "SELECT created_revision FROM turns WHERE campaign_id = ? AND turn_id = ?",
                    (str(operation["campaign_id"]), str(operation["basis_turn_id"])),
                ).fetchone()
                lock = connection.execute(
                    "SELECT campaign_id, created_revision FROM campaign_locks WHERE lock_id = ?",
                    (str(operation["basis_lock_id"]),),
                ).fetchone()
                draft_operation = (
                    operation_kind in sqlite_draft_history.DRAFT_LIFECYCLE_KINDS
                    and operation["basis_turn_id"] is None
                    and operation["basis_lock_id"] is None
                    and sqlite_draft_history.has_only_draft_lifecycle_history(
                        connection, str(operation["campaign_id"]),
                        int(operation["resulting_revision"]),
                    )
                )
                if not draft_operation and (
                    turn is None
                    or lock is None
                    or str(lock["campaign_id"]) != str(operation["campaign_id"])
                    or int(turn["created_revision"]) > int(operation["basis_revision"])
                    or int(lock["created_revision"]) > int(operation["basis_revision"])
                    or int(operation["resulting_revision"]) < int(operation["basis_revision"])
                ):
                    raise ValueError("Campaign operation authority basis is invalid")
                if operation_kind == "commit_campaign_continuity":
                    record = connection.execute(
                        "SELECT record_id, kind, status, payload_json, basis_message_ids_json, "
                        "admitted_revision FROM campaign_continuity_records "
                        "WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone()
                    revision = connection.execute(
                        "SELECT continuity_operation_id FROM campaign_revisions "
                        "WHERE campaign_id = ? AND revision = ?",
                        (operation["campaign_id"], operation["resulting_revision"]),
                    ).fetchone()
                    if (
                        not isinstance(result, CampaignContinuityReceipt)
                        or record is None
                        or revision is None
                        or str(revision["continuity_operation_id"]) != operation_id
                        or int(operation["resulting_revision"])
                        != int(operation["basis_revision"]) + 1
                        or result.operation_id != operation_id
                        or result.record_id != str(record["record_id"])
                        or result.kind != str(record["kind"])
                        or payload
                        != {
                            "operation_id": operation_id,
                            "operation_kind": "commit_campaign_continuity",
                            "campaign_id": str(operation["campaign_id"]),
                            "session_id": payload.get("session_id"),
                            "turn_id": str(operation["basis_turn_id"]),
                            "kind": str(record["kind"]),
                            "record_id": str(record["record_id"]),
                            "status": str(record["status"]),
                            "payload": _load_payload(str(record["payload_json"])),
                            "basis_message_ids": _load_json(
                                str(record["basis_message_ids_json"])
                            ),
                        }
                    ):
                        raise ValueError("Campaign continuity receipt authority is invalid")
                    seen_operations.add(operation_id)
                    continue

            for construction in connection.execute(
                "SELECT * FROM campaign_constructions ORDER BY campaign_id"
            ):
                campaign_id = str(construction["campaign_id"])
                campaign = connection.execute(
                    "SELECT display_name FROM campaigns WHERE campaign_id = ?", (campaign_id,)
                ).fetchone()
                if campaign is None:
                    raise ValueError("Campaign construction is orphaned")
                confirmation_id = str(construction["confirmation_operation_id"])
                confirmation = operations.get(confirmation_id)
                if confirmation is None:
                    raise ValueError("Campaign confirmation receipt is unavailable")
                authority_turn_id = str(construction["authority_turn_id"])
                authority_lock_id = str(construction["authority_lock_id"])
                confirmation_revision = int(construction["confirmation_revision"])
                lock_record = sqlite_packages.read_lock_record(connection, authority_lock_id)
                confirmation_payload = _load_payload(str(confirmation["semantic_payload_json"]))
                confirmed_display_name = confirmation_payload.get("display_name")
                validate_authority_identity(
                    confirmed_display_name,
                    "Campaign confirmation display name",
                )
                expected_confirmation_payload: dict[str, JsonValue] = {
                    "operation_id": confirmation_id,
                    "operation_kind": "confirm_new_campaign",
                    "display_name": confirmed_display_name,
                    "package_lock": owned_json_value(
                        _operation_lock_payload(lock_record.package_lock)
                    ),
                    "turn_id": authority_turn_id,
                }
                stored_participants: list[JsonValue] = [
                    {
                        "participant_id": str(row["participant_id"]),
                        "kind": str(row["kind"]),
                        "package_id": str(row["package_id"]),
                        "package_version": str(row["package_version"]),
                        "package_sha256": (
                            None if row["package_sha256"] is None else str(row["package_sha256"])
                        ),
                        "schema_id": (None if row["schema_id"] is None else str(row["schema_id"])),
                        "schema_sha256": (
                            None if row["schema_sha256"] is None else str(row["schema_sha256"])
                        ),
                        "validation_status": str(row["validation_status"]),
                        "adaptation_id": (
                            None if row["adaptation_id"] is None else str(row["adaptation_id"])
                        ),
                        "data": participant_data_history(
                            connection, campaign_id, str(row["participant_id"]),
                            _load_payload(str(row["data_json"])),
                        )[-1],
                    }
                    for row in connection.execute(
                        "SELECT * FROM participants WHERE campaign_id = ? ORDER BY participant_id",
                        (campaign_id,),
                    )
                ]
                if "participants" in confirmation_payload:
                    expected_confirmation_payload["participants"] = stored_participants
                elif stored_participants:
                    raise ValueError("Campaign confirmation participants are unsigned")
                if "initial_state" in confirmation_payload:
                    initial_state = confirmation_payload["initial_state"]
                    if not isinstance(initial_state, Sequence) or isinstance(
                        initial_state, (str, bytes, bytearray)
                    ):
                        raise ValueError("Campaign confirmation initial state is invalid")
                    expected_confirmation_payload["initial_state"] = initial_state
                package_candidate_value = confirmation_payload.get("package_candidate_id")
                if package_candidate_value is not None:
                    if not isinstance(package_candidate_value, str):
                        raise ValueError("Campaign confirmation package candidate is invalid")
                    candidate = sqlite_packages.read_candidate(connection, package_candidate_value)
                    if not _handoff_candidate_matches_lock(lock_record, candidate):
                        raise ValueError("Campaign confirmation package candidate is inexact")
                    expected_confirmation_payload["package_candidate_id"] = package_candidate_value
                builder_value = confirmation_payload.get("builder_session")
                if builder_value is not None:
                    if not isinstance(builder_value, Mapping):
                        raise ValueError("Campaign confirmation Builder session is invalid")
                    builder_session_id = builder_value.get("session_id")
                    builder_provider_id = builder_value.get("provider_id")
                    builder_model_id = builder_value.get("model_id")
                    builder_selected_skill = builder_value.get("selected_skill")
                    if (
                        not isinstance(builder_session_id, str)
                        or not isinstance(builder_provider_id, str)
                        or not isinstance(builder_model_id, str)
                        or not isinstance(builder_selected_skill, str)
                        or builder_selected_skill not in BUILDER_SKILLS
                    ):
                        raise ValueError("Campaign confirmation Builder session is invalid")
                    for value, label in (
                        (builder_session_id, "Campaign confirmation Builder session"),
                        (builder_provider_id, "Campaign confirmation Builder provider"),
                        (builder_model_id, "Campaign confirmation Builder model"),
                    ):
                        validate_authority_identity(value, label)
                    builder_session = connection.execute(
                        "SELECT * FROM agent_sessions WHERE session_id = ?",
                        (builder_session_id,),
                    ).fetchone()
                    if (
                        builder_session is None
                        or str(builder_session["campaign_id"]) != campaign_id
                        or str(builder_session["role"]) != WriterRole.BUILDER.value
                        or builder_session["parent_session_id"] is not None
                        or str(builder_session["selected_skill"]) != builder_selected_skill
                    ):
                        raise ValueError("Campaign confirmation Builder session is invalid")
                    expected_confirmation_payload["builder_session"] = {
                        "session_id": builder_session_id,
                        "provider_id": builder_provider_id,
                        "model_id": builder_model_id,
                        "selected_skill": builder_selected_skill,
                    }
                if (
                    str(confirmation["operation_kind"]) != "confirm_new_campaign"
                    or str(confirmation["campaign_id"]) != campaign_id
                    or str(confirmation["basis_turn_id"]) != authority_turn_id
                    or str(confirmation["basis_lock_id"]) != authority_lock_id
                    or int(confirmation["basis_revision"]) != confirmation_revision
                    or int(confirmation["resulting_revision"]) != confirmation_revision
                    or lock_record.created_revision != confirmation_revision
                    or not sqlite_draft_history.has_only_draft_lifecycle_history(
                        connection, campaign_id, confirmation_revision
                    )
                    or confirmation_payload != expected_confirmation_payload
                ):
                    raise ValueError("Campaign confirmation authority is invalid")
                seen_operations.add(confirmation_id)

                lane_value = construction["lane"]
                begin_value = construction["begin_operation_id"]
                if begin_value is None:
                    if any(
                        construction[column] is not None
                        for column in (
                            "lane",
                            "canonical_input_json",
                            "begin_revision",
                            "canonical_plan_json",
                            "completion_operation_id",
                            "completion_revision",
                            "canonical_representation_sha256",
                        )
                    ):
                        raise ValueError("Unbegun campaign construction has staged authority")
                    continue
                lane = str(lane_value)
                begin_id = str(begin_value)
                begin = operations.get(begin_id)
                canonical_input = _load_payload(str(construction["canonical_input_json"]))
                begin_revision = int(construction["begin_revision"])
                if begin is None:
                    raise ValueError("Campaign construction begin receipt is unavailable")
                begin_payload = _load_payload(str(begin["semantic_payload_json"]))
                begin_result = self._campaign_operation_result(begin)
                if (
                    not isinstance(begin_result, CampaignConstructionBegun)
                    or begin_result.lane != lane
                    or begin_result.resulting_revision != begin_revision
                    or str(begin["operation_kind"]) != "begin_campaign_construction"
                    or str(begin["campaign_id"]) != campaign_id
                    or str(begin["basis_turn_id"]) != authority_turn_id
                    or str(begin["basis_lock_id"]) != authority_lock_id
                    or int(begin["basis_revision"]) != confirmation_revision
                    or int(begin["resulting_revision"]) != begin_revision
                    or begin_revision != confirmation_revision + 1
                    or begin_payload
                    != {
                        "operation_id": begin_id,
                        "operation_kind": "begin_campaign_construction",
                        "campaign_id": campaign_id,
                        "lane": lane,
                        "canonical_input": canonical_input,
                    }
                    or connection.execute(
                        "SELECT construction_operation_id FROM campaign_revisions "
                        "WHERE campaign_id = ? AND revision = ?",
                        (campaign_id, begin_revision),
                    ).fetchone()[0]
                    != begin_id
                ):
                    raise ValueError("Campaign construction begin authority is invalid")
                seen_operations.add(begin_id)

                completion_value = construction["completion_operation_id"]
                if completion_value is None:
                    if any(
                        construction[column] is not None
                        for column in (
                            "canonical_plan_json",
                            "completion_revision",
                            "canonical_representation_sha256",
                        )
                    ):
                        raise ValueError("Incomplete construction has completion authority")
                    continue
                completion_id = str(completion_value)
                completion = operations.get(completion_id)
                canonical_plan = _load_payload(str(construction["canonical_plan_json"]))
                completion_revision = int(construction["completion_revision"])
                representation_sha256 = hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "canonical_input": canonical_input,
                            "canonical_plan": canonical_plan,
                        }
                    )
                ).hexdigest()
                if completion is None:
                    raise ValueError("Campaign construction completion receipt is unavailable")
                completion_payload = _load_payload(str(completion["semantic_payload_json"]))
                completion_result = self._campaign_operation_result(completion)
                if (
                    not isinstance(completion_result, CampaignConstructionCompleted)
                    or str(completion["operation_kind"]) != "complete_campaign_construction"
                    or str(completion["campaign_id"]) != campaign_id
                    or str(completion["basis_turn_id"]) != authority_turn_id
                    or str(completion["basis_lock_id"]) != authority_lock_id
                    or int(completion["basis_revision"]) != begin_revision
                    or int(completion["resulting_revision"]) != completion_revision
                    or completion_revision != begin_revision + 1
                    or completion_payload
                    != {
                        "operation_id": completion_id,
                        "operation_kind": "complete_campaign_construction",
                        "campaign_id": campaign_id,
                        "canonical_plan": canonical_plan,
                    }
                    or str(construction["canonical_representation_sha256"]) != representation_sha256
                    or completion_result.lane != lane
                    or completion_result.canonical_representation_sha256 != representation_sha256
                    or connection.execute(
                        "SELECT construction_operation_id FROM campaign_revisions "
                        "WHERE campaign_id = ? AND revision = ?",
                        (campaign_id, completion_revision),
                    ).fetchone()[0]
                    != completion_id
                ):
                    raise ValueError("Campaign construction completion authority is invalid")
                seen_operations.add(completion_id)

            for transition in connection.execute(
                "SELECT * FROM campaign_lifecycle_transitions "
                "ORDER BY campaign_id, resulting_revision"
            ):
                operation_id = str(transition["operation_id"])
                operation = operations.get(operation_id)
                revision = connection.execute(
                    "SELECT lifecycle_operation_id FROM campaign_revisions "
                    "WHERE campaign_id = ? AND revision = ?",
                    (str(transition["campaign_id"]), int(transition["resulting_revision"])),
                ).fetchone()
                if operation is None or revision is None:
                    raise ValueError("Campaign lifecycle operation authority is unavailable")
                result = self._campaign_operation_result(operation)
                if (
                    not isinstance(result, CampaignLifecycleResult)
                    or str(operation["operation_kind"]) != str(transition["operation_kind"])
                    or str(operation["campaign_id"]) != str(transition["campaign_id"])
                    or int(operation["basis_revision"]) != int(transition["basis_revision"])
                    or int(operation["resulting_revision"]) != int(transition["resulting_revision"])
                    or result.lifecycle.value != str(transition["to_lifecycle"])
                    or result.writer.value != str(transition["to_writer"])
                    or result.session_id
                    != (
                        None
                        if transition["designated_session_id"] is None
                        else str(transition["designated_session_id"])
                    )
                    or str(revision["lifecycle_operation_id"]) != operation_id
                ):
                    raise ValueError("Campaign lifecycle operation authority is invalid")
                seen_operations.add(operation_id)
            if seen_operations != set(operations):
                raise ValueError("Campaign operation receipt is orphaned")
        except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
            raise SchemaIdentityError(invalid) from error

    @staticmethod
    def _verify_stored_identities(connection: sqlite3.Connection) -> None:
        """Re-admit every string-backed canonical identity before publishing an owner."""

        invalid = "Authority stored integrity does not match F1"
        checks: tuple[tuple[str, tuple[tuple[str, bool], ...]], ...] = (
            (
                "SELECT * FROM packages",
                (("package_id", False), ("version", False), ("sha256", True)),
            ),
            (
                "SELECT * FROM package_dependencies",
                (
                    ("package_id", False),
                    ("version", False),
                    ("sha256", True),
                    ("dependency_id", False),
                    ("dependency_version", False),
                    ("dependency_sha256", True),
                    ("dependency_kind", False),
                    ("dependency_namespace", False),
                ),
            ),
            ("SELECT * FROM campaigns", (("campaign_id", False), ("display_name", False))),
            (
                "SELECT * FROM campaign_package_memberships",
                (
                    ("campaign_id", False),
                    ("package_id", False),
                    ("version", False),
                    ("sha256", True),
                    ("role", False),
                ),
            ),
            (
                "SELECT * FROM participants",
                (
                    ("campaign_id", False),
                    ("participant_id", False),
                    ("kind", False),
                    ("package_id", False),
                    ("package_version", False),
                ),
            ),
            (
                "SELECT * FROM campaign_state",
                (("campaign_id", False), ("namespace", False), ("state_key", False)),
            ),
            (
                "SELECT * FROM turns",
                (("campaign_id", False), ("turn_id", False), ("status", False)),
            ),
            (
                "SELECT * FROM actions",
                (
                    ("campaign_id", False),
                    ("action_id", False),
                    ("turn_id", False),
                    ("writer", False),
                    ("package_id", False),
                    ("package_version", False),
                    ("package_sha256", True),
                    ("tool_name", False),
                    ("status", False),
                    ("payload_hash", True),
                ),
            ),
            (
                "SELECT * FROM canonical_events",
                (
                    ("campaign_id", False),
                    ("event_type", False),
                    ("turn_id", False),
                    ("visibility", False),
                ),
            ),
            (
                "SELECT * FROM narration_interruptions",
                (
                    ("campaign_id", False),
                    ("interruption_id", False),
                    ("turn_id", False),
                    ("failed_attempt_id", False),
                    ("status", False),
                ),
            ),
            (
                "SELECT * FROM mutations",
                (
                    ("campaign_id", False),
                    ("action_id", False),
                    ("namespace", False),
                    ("state_key", False),
                ),
            ),
            (
                "SELECT * FROM consequences",
                (("campaign_id", False), ("action_id", False)),
            ),
            (
                "SELECT * FROM idempotency_records",
                (
                    ("campaign_id", False),
                    ("idempotency_key", False),
                    ("payload_hash", True),
                    ("action_id", False),
                    ("status", False),
                ),
            ),
            ("SELECT * FROM campaign_revisions", (("campaign_id", False),)),
        )
        has_construction = "campaign_operations" in {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if has_construction:
            checks += (
                (
                    "SELECT * FROM authority_schema_extensions",
                    (
                        ("extension_name", False),
                        ("extension_sha256", True),
                        ("predecessor_migration_name", False),
                        ("predecessor_sql_sha256", True),
                    ),
                ),
                (
                    "SELECT * FROM campaign_operations",
                    (
                        ("operation_id", False),
                        ("operation_kind", False),
                        ("campaign_id", False),
                        ("semantic_payload_sha256", True),
                    ),
                ),
                (
                    "SELECT * FROM campaign_constructions",
                    (
                        ("campaign_id", False),
                        ("confirmation_operation_id", False),
                        ("authority_turn_id", False),
                        ("authority_lock_id", False),
                    ),
                ),
            )
        has_continuity = "campaign_continuity_records" in {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if has_continuity:
            checks += (
                (
                    "SELECT * FROM campaign_continuity_records",
                    (
                        ("campaign_id", False),
                        ("entry_id", False),
                        ("record_id", False),
                        ("kind", False),
                        ("status", False),
                    ),
                ),
            )
        try:
            for query, columns in checks:
                for row in connection.execute(query):
                    for column, is_sha256 in columns:
                        validate_authority_identity(
                            row[column], "Stored authority identity", sha256=is_sha256
                        )
            nullable_identity_queries: tuple[tuple[str, bool], ...] = (
                ("SELECT action_id AS value FROM campaign_revisions", False),
                ("SELECT activation_id AS value FROM campaign_revisions", False),
                ("SELECT action_id AS value FROM canonical_events", False),
                ("SELECT attempt_id AS value FROM canonical_events", False),
                ("SELECT last_action_id AS value FROM narration_interruptions", False),
                ("SELECT resolving_retry_id AS value FROM narration_interruptions", False),
            )
            if has_construction:
                nullable_identity_queries += (
                    ("SELECT basis_turn_id AS value FROM campaign_operations", False),
                    ("SELECT basis_lock_id AS value FROM campaign_operations", False),
                    (
                        "SELECT construction_operation_id AS value FROM campaign_revisions",
                        False,
                    ),
                    ("SELECT lane AS value FROM campaign_constructions", False),
                    ("SELECT begin_operation_id AS value FROM campaign_constructions", False),
                    (
                        "SELECT completion_operation_id AS value FROM campaign_constructions",
                        False,
                    ),
                    (
                        "SELECT canonical_representation_sha256 AS value "
                        "FROM campaign_constructions",
                        True,
                    ),
                )
            if has_continuity:
                nullable_identity_queries += (
                    (
                        "SELECT continuity_operation_id AS value FROM campaign_revisions",
                        False,
                    ),
                    (
                        "SELECT operation_id AS value FROM campaign_continuity_records",
                        False,
                    ),
                )
            for query, is_sha256 in nullable_identity_queries:
                for row in connection.execute(query):
                    if row["value"] is not None:
                        validate_authority_identity(
                            row["value"], "Stored optional identity", sha256=is_sha256
                        )
        except (KeyError, TypeError, ValueError) as error:
            raise SchemaIdentityError(invalid) from error

    def _verify_campaign_authority(
        self,
        connection: sqlite3.Connection,
        *,
        campaign_id: str,
        current_lifecycle: str,
        current_revision: int,
        current_writer: str,
        package_lock: PackageLock,
    ) -> None:
        """Reject inconsistent revision, action, and canonical chronology authority."""

        invalid = "Authority stored integrity does not match F1"
        try:
            lifecycle = LifecycleState(current_lifecycle)
            writer_role = WriterRole(current_writer)
        except ValueError as error:
            raise SchemaIdentityError(invalid) from error
        if not lifecycle_writer_is_legal(lifecycle, writer_role):
            raise SchemaIdentityError(invalid)
        revision_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(campaign_revisions)")
        }
        has_plan_revisions = "plan_id" in revision_columns
        has_handoff_revisions = "handoff_id" in revision_columns
        has_construction_revisions = "construction_operation_id" in revision_columns
        has_lifecycle_revisions = "lifecycle_operation_id" in revision_columns
        has_continuity_revisions = "continuity_operation_id" in revision_columns
        optional_revision_columns = (
            ("plan_id", has_plan_revisions),
            ("handoff_id", has_handoff_revisions),
            ("construction_operation_id", has_construction_revisions),
            ("lifecycle_operation_id", has_lifecycle_revisions),
            ("continuity_operation_id", has_continuity_revisions),
        )
        revision_select = (
            "SELECT revision, action_id, activation_id, "
            + ", ".join(
                name if present else f"NULL AS {name}"
                for name, present in optional_revision_columns
            )
            + " FROM campaign_revisions "
        )
        revisions = tuple(
            connection.execute(
                revision_select + "WHERE campaign_id = ? ORDER BY revision",
                (campaign_id,),
            )
        )
        if tuple(int(row["revision"]) for row in revisions) != tuple(range(current_revision + 1)):
            raise SchemaIdentityError(invalid)
        if (
            not revisions
            or any(
                value is not None
                for value in (
                    revisions[0]["action_id"],
                    revisions[0]["activation_id"],
                    revisions[0]["plan_id"],
                    revisions[0]["handoff_id"],
                    revisions[0]["construction_operation_id"],
                    revisions[0]["lifecycle_operation_id"],
                    revisions[0]["continuity_operation_id"],
                )
            )
            or any(
                sum(
                    value is not None
                    for value in (
                        row["action_id"],
                        row["activation_id"],
                        row["plan_id"],
                        row["handoff_id"],
                        row["construction_operation_id"],
                        row["lifecycle_operation_id"],
                        row["continuity_operation_id"],
                    )
                )
                != 1
                for row in revisions[1:]
            )
        ):
            raise SchemaIdentityError(invalid)

        actions = tuple(
            connection.execute(
                "SELECT * FROM actions WHERE campaign_id = ? ORDER BY resulting_revision",
                (campaign_id,),
            )
        )
        activations = tuple(
            connection.execute(
                "SELECT * FROM package_activations WHERE campaign_id = ? "
                "AND status = 'committed' ORDER BY resulting_revision",
                (campaign_id,),
            )
        )
        plans = (
            tuple(
                connection.execute(
                    "SELECT * FROM campaign_plans WHERE campaign_id = ? "
                    "ORDER BY resulting_campaign_revision",
                    (campaign_id,),
                )
            )
            if has_plan_revisions
            else ()
        )
        handoffs = (
            tuple(
                connection.execute(
                    "SELECT * FROM builder_dm_handoffs WHERE campaign_id = ? "
                    "ORDER BY resulting_campaign_revision",
                    (campaign_id,),
                )
            )
            if has_handoff_revisions
            else ()
        )
        construction_operations = (
            tuple(
                connection.execute(
                    "SELECT * FROM campaign_operations WHERE campaign_id = ? "
                    "AND operation_kind IN "
                    "('begin_campaign_construction', 'complete_campaign_construction') "
                    "ORDER BY resulting_revision",
                    (campaign_id,),
                )
            )
            if has_construction_revisions
            else ()
        )
        lifecycle_operations = (
            tuple(
                connection.execute(
                    "SELECT * FROM campaign_lifecycle_transitions WHERE campaign_id = ? "
                    "ORDER BY resulting_revision",
                    (campaign_id,),
                )
            )
            if has_lifecycle_revisions
            else ()
        )
        continuity_operations = (
            tuple(
                connection.execute(
                    "SELECT * FROM campaign_operations WHERE campaign_id = ? "
                    "AND operation_kind = 'commit_campaign_continuity' "
                    "ORDER BY resulting_revision",
                    (campaign_id,),
                )
            )
            if has_continuity_revisions
            else ()
        )
        if (
            len(actions)
            + len(activations)
            + len(plans)
            + len(handoffs)
            + len(construction_operations)
            + len(lifecycle_operations)
            + len(continuity_operations)
            != current_revision
        ):
            raise SchemaIdentityError(invalid)
        action_by_id = {str(row["action_id"]): row for row in actions}
        activation_by_id = {str(row["activation_id"]): row for row in activations}
        plan_by_id = {str(row["plan_id"]): row for row in plans}
        handoff_by_id = {str(row["handoff_id"]): row for row in handoffs}
        construction_by_id = {str(row["operation_id"]): row for row in construction_operations}
        lifecycle_by_id = {str(row["operation_id"]): row for row in lifecycle_operations}
        continuity_by_id = {str(row["operation_id"]): row for row in continuity_operations}
        if (
            len(action_by_id) != len(actions)
            or len(activation_by_id) != len(activations)
            or len(plan_by_id) != len(plans)
            or len(handoff_by_id) != len(handoffs)
            or len(construction_by_id) != len(construction_operations)
            or len(lifecycle_by_id) != len(lifecycle_operations)
            or len(continuity_by_id) != len(continuity_operations)
        ):
            raise SchemaIdentityError(invalid)
        for row in revisions[1:]:
            revision = int(row["revision"])
            action_id = row["action_id"]
            activation_id = row["activation_id"]
            plan_id = row["plan_id"]
            handoff_id = row["handoff_id"]
            construction_operation_id = row["construction_operation_id"]
            lifecycle_operation_id = row["lifecycle_operation_id"]
            continuity_operation_id = row["continuity_operation_id"]
            if action_id is not None:
                authority = action_by_id.get(str(action_id))
                resulting = "resulting_revision"
            elif activation_id is not None:
                authority = activation_by_id.get(str(activation_id))
                resulting = "resulting_revision"
            elif plan_id is not None:
                authority = plan_by_id.get(str(plan_id))
                resulting = "resulting_campaign_revision"
            elif handoff_id is not None:
                authority = handoff_by_id.get(str(handoff_id))
                resulting = "resulting_campaign_revision"
            elif construction_operation_id is not None:
                authority = construction_by_id.get(str(construction_operation_id))
                resulting = "resulting_revision"
            elif lifecycle_operation_id is not None:
                authority = lifecycle_by_id.get(str(lifecycle_operation_id))
                resulting = "resulting_revision"
            else:
                authority = continuity_by_id.get(str(continuity_operation_id))
                resulting = "resulting_revision"
            if authority is None or int(authority[resulting]) != revision:
                raise SchemaIdentityError(invalid)

        lineage_entries = tuple(
            entry
            for record in sqlite_packages.lock_lineage(connection, campaign_id)
            for entry in record.package_lock.entries
        )
        package_contexts = {(entry.package_id, entry.version) for entry in lineage_entries}
        turns = tuple(
            connection.execute(
                "SELECT turn_id, status, created_revision FROM turns WHERE campaign_id = ? "
                "ORDER BY created_revision, turn_id",
                (campaign_id,),
            )
        )
        turn_created_revisions = {
            str(turn["turn_id"]): int(turn["created_revision"]) for turn in turns
        }
        unbound_draft = (
            connection.execute(
                "SELECT current_lock_id FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
            is None
            and sqlite_draft_history.has_only_draft_lifecycle_history(
                connection, campaign_id, current_revision
            )
        )
        if (
            (not turns and not unbound_draft)
            or len(turn_created_revisions) != len(turns)
            or sum(str(turn["status"]) == "open" for turn in turns) > 1
            or any(str(turn["status"]) not in {"open", "completed"} for turn in turns)
            or any(
                revision < 0 or revision > current_revision
                for revision in turn_created_revisions.values()
            )
        ):
            raise SchemaIdentityError(invalid)
        for participant in connection.execute(
            "SELECT package_id, package_version, data_json FROM participants "
            "WHERE campaign_id = ? ORDER BY participant_id",
            (campaign_id,),
        ):
            if (
                str(participant["package_id"]),
                str(participant["package_version"]),
            ) not in package_contexts or not canonical_payload_is_safe(
                _load_payload(str(participant["data_json"]))
            ):
                raise SchemaIdentityError(invalid)
        current_state: dict[tuple[str, str], JsonValue] = {}
        for state in connection.execute(
            "SELECT namespace, state_key, value_json FROM campaign_state "
            "WHERE campaign_id = ? ORDER BY namespace, state_key",
            (campaign_id,),
        ):
            value = _load_json(str(state["value_json"]))
            identity = (str(state["namespace"]), str(state["state_key"]))
            current_state[identity] = value
            if not canonical_payload_is_safe({identity[0]: {identity[1]: value}}):
                raise SchemaIdentityError(invalid)

        events = tuple(
            connection.execute(
                "SELECT * FROM canonical_events WHERE campaign_id = ? ORDER BY sequence",
                (campaign_id,),
            )
        )
        if tuple(int(row["sequence"]) for row in events) != tuple(range(1, len(events) + 1)):
            raise SchemaIdentityError(invalid)
        event_payloads: dict[int, SafePayload] = {}
        prose_by_attempt: dict[str, list[sqlite3.Row]] = {}
        action_event_sequences: set[int] = set()
        for event in events:
            sequence = int(event["sequence"])
            payload = _load_payload(str(event["safe_payload_json"]))
            if not canonical_payload_is_safe(payload):
                raise SchemaIdentityError(invalid)
            event_payloads[sequence] = payload
            event_type = CanonicalEventType(str(event["event_type"]))
            visibility = EventVisibility(str(event["visibility"]))
            if event_type in (CanonicalEventType.THINK, CanonicalEventType.TALK):
                response_blocks_valid = "response_blocks" not in payload
                if "response_blocks" in payload and event_type is CanonicalEventType.TALK:
                    try:
                        response_blocks_valid = (
                            response_blocks_text(
                                validate_response_blocks(payload["response_blocks"])
                            )
                            == payload.get("text")
                        )
                    except ValueError:
                        response_blocks_valid = False
                if (
                    event["action_id"] is not None
                    or event["attempt_id"] is None
                    or event["attempt_ordinal"] is None
                    or int(event["attempt_ordinal"]) < 0
                    or visibility is not EventVisibility.PLAYER_SAFE
                    or set(payload) not in ({"text"}, {"text", "response_blocks"})
                    or not isinstance(payload["text"], str)
                    or not payload["text"]
                    or not response_blocks_valid
                    or str(event["turn_id"]) not in turn_created_revisions
                ):
                    raise SchemaIdentityError(invalid)
                prose_by_attempt.setdefault(str(event["attempt_id"]), []).append(event)
            else:
                expected_visibility = (
                    EventVisibility.PLAYER_SAFE
                    if event_type in (CanonicalEventType.TOOL_CALL, CanonicalEventType.TOOL_RESULT)
                    else EventVisibility.INTERNAL_SAFE
                )
                if (
                    event["action_id"] is None
                    or str(event["action_id"]) not in action_by_id
                    or event["attempt_id"] is not None
                    or event["attempt_ordinal"] is not None
                    or visibility is not expected_visibility
                ):
                    raise SchemaIdentityError(invalid)
                action_event_sequences.add(sequence)
        for attempt_events in prose_by_attempt.values():
            ordinals = tuple(int(event["attempt_ordinal"]) for event in attempt_events)
            if ordinals != tuple(range(len(ordinals))):
                raise SchemaIdentityError(invalid)

        lifecycle_state = None
        if has_lifecycle_revisions:
            lifecycle_state = connection.execute(
                "SELECT * FROM campaign_lifecycle_state WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if lifecycle_state is None:
                raise SchemaIdentityError(invalid)

        writer_events: dict[int, tuple[str, sqlite3.Row]] = {}
        for handoff in handoffs:
            revision = int(handoff["resulting_campaign_revision"])
            if revision in writer_events:
                raise SchemaIdentityError(invalid)
            writer_events[revision] = ("handoff", handoff)
        for transition in lifecycle_operations:
            revision = int(transition["resulting_revision"])
            if revision in writer_events:
                raise SchemaIdentityError(invalid)
            writer_events[revision] = ("lifecycle", transition)

        if handoffs:
            initial_writer = WriterRole.BUILDER.value
        elif actions:
            initial_writer = str(actions[0]["writer"])
        elif lifecycle_operations:
            assert lifecycle_state is not None
            retained = lifecycle_state["retained_active_writer"]
            if retained is None:
                raise SchemaIdentityError(invalid)
            initial_writer = str(retained)
        else:
            initial_writer = current_writer
        if initial_writer not in {WriterRole.BUILDER.value, WriterRole.DM.value}:
            raise SchemaIdentityError(invalid)

        writer_by_revision: dict[int, str] = {0: initial_writer}
        writer_cursor = initial_writer
        for revision_number in range(1, current_revision + 1):
            event = writer_events.get(revision_number)
            if event is not None:
                event_kind, row = event
                if event_kind == "handoff":
                    if (
                        int(row["expected_revision"]) != revision_number - 1
                        or writer_cursor != WriterRole.BUILDER.value
                    ):
                        raise SchemaIdentityError(invalid)
                    writer_cursor = WriterRole.DM.value
                else:
                    if (
                        int(row["basis_revision"]) != revision_number - 1
                        or str(row["from_writer"]) != writer_cursor
                        or str(row["to_writer"])
                        not in {
                            WriterRole.BUILDER.value,
                            WriterRole.DM.value,
                            WriterRole.NONE.value,
                        }
                    ):
                        raise SchemaIdentityError(invalid)
                    writer_cursor = str(row["to_writer"])
            writer_by_revision[revision_number] = writer_cursor
        if writer_cursor != current_writer:
            raise SchemaIdentityError(invalid)

        if has_lifecycle_revisions:
            assert lifecycle_state is not None
            retained_writer = lifecycle_state["retained_active_writer"]
            prior_lifecycle = lifecycle_state["tombstone_prior_lifecycle"]
            prior_writer = lifecycle_state["tombstone_prior_writer"]
            if retained_writer is not None and str(retained_writer) not in {
                WriterRole.BUILDER.value,
                WriterRole.DM.value,
            }:
                raise SchemaIdentityError(invalid)
            if lifecycle_operations:
                latest_transition = lifecycle_operations[-1]
                # A later Builder-to-GM handoff can change the writer without another
                # lifecycle transition. The full writer sequence was checked above.
                if str(latest_transition["to_lifecycle"]) != current_lifecycle:
                    raise SchemaIdentityError(invalid)
                previous_lifecycle = LifecycleState.ACTIVE.value
                for transition in lifecycle_operations:
                    if str(transition["from_lifecycle"]) != previous_lifecycle:
                        raise SchemaIdentityError(invalid)
                    previous_lifecycle = str(transition["to_lifecycle"])
            if lifecycle is LifecycleState.ACTIVE:
                if retained_writer != current_writer or prior_lifecycle is not None:
                    raise SchemaIdentityError(invalid)
            elif lifecycle is LifecycleState.ARCHIVED:
                if current_writer != WriterRole.NONE.value or prior_lifecycle is not None:
                    raise SchemaIdentityError(invalid)
            elif (
                prior_lifecycle not in {LifecycleState.ACTIVE.value, LifecycleState.ARCHIVED.value}
                or prior_writer is None
            ):
                raise SchemaIdentityError(invalid)

        mutation_final_values: dict[tuple[str, str], JsonValue] = {}
        for action in actions:
            action_id = str(action["action_id"])
            expected_revision = int(action["expected_revision"])
            resulting_revision = int(action["resulting_revision"])
            writer = str(action["writer"])
            turn_id = str(action["turn_id"])
            package_id = str(action["package_id"])
            package_version = str(action["package_version"])
            package_sha256 = str(action["package_sha256"])
            tool_name = str(action["tool_name"])
            status = ActionStatus(str(action["status"]))
            parameters = _load_payload(str(action["payload_json"]))
            result = _load_payload(str(action["result_json"]))
            payload_hash = str(action["payload_hash"])
            action_lock_id = action["lock_id"]
            exact_action_pin = (
                action_lock_id is not None
                and connection.execute(
                    "SELECT 1 FROM campaign_lock_memberships WHERE lock_id = ? "
                    "AND package_id = ? AND version = ? AND content_sha256 = ?",
                    (str(action_lock_id), package_id, package_version, package_sha256),
                ).fetchone()
                is not None
            )
            if (
                expected_revision != resulting_revision - 1
                or resulting_revision < 1
                or resulting_revision > current_revision
                or str(revisions[resulting_revision]["action_id"]) != action_id
                or writer != writer_by_revision.get(expected_revision)
                or turn_id not in turn_created_revisions
                or expected_revision < turn_created_revisions[turn_id]
                or not exact_action_pin
                or not canonical_payload_is_safe(parameters)
                or not canonical_payload_is_safe(result)
            ):
                raise SchemaIdentityError(invalid)
            if payload_hash != _stored_action_hash(
                campaign_id=campaign_id,
                action_id=action_id,
                writer=writer,
                expected_revision=expected_revision,
                turn_id=turn_id,
                package_id=package_id,
                package_version=package_version,
                package_sha256=package_sha256,
                tool_name=tool_name,
                parameters=parameters,
            ):
                raise SchemaIdentityError(invalid)

            mutations = tuple(
                connection.execute(
                    "SELECT * FROM mutations WHERE campaign_id = ? AND action_id = ? "
                    "ORDER BY ordinal",
                    (campaign_id, action_id),
                )
            )
            consequences = tuple(
                connection.execute(
                    "SELECT * FROM consequences WHERE campaign_id = ? AND action_id = ? "
                    "ORDER BY ordinal",
                    (campaign_id, action_id),
                )
            )
            if tuple(int(row["ordinal"]) for row in mutations) != tuple(
                range(len(mutations))
            ) or tuple(int(row["ordinal"]) for row in consequences) != tuple(
                range(len(consequences))
            ):
                raise SchemaIdentityError(invalid)

            action_events = tuple(
                event
                for event in events
                if event["action_id"] is not None and str(event["action_id"]) == action_id
            )
            terminal = (
                CanonicalEventType.ACTION_COMMITTED
                if status is ActionStatus.COMMITTED
                else CanonicalEventType.ACTION_FAILED
            )
            expected_types = (
                CanonicalEventType.ACTION_DECLARED,
                CanonicalEventType.TOOL_CALL,
                CanonicalEventType.TOOL_RESULT,
                *(CanonicalEventType.STATE_MUTATION for _ in mutations),
                *(CanonicalEventType.CONSEQUENCE for _ in consequences),
                terminal,
            )
            action_sequences = tuple(int(event["sequence"]) for event in action_events)
            if (
                not action_sequences
                or action_sequences
                != tuple(range(action_sequences[0], action_sequences[0] + len(expected_types)))
                or tuple(CanonicalEventType(str(event["event_type"])) for event in action_events)
                != expected_types
                or any(str(event["turn_id"]) != turn_id for event in action_events)
            ):
                raise SchemaIdentityError(invalid)
            if status is ActionStatus.FAILED and (mutations or len(consequences) > 1):
                raise SchemaIdentityError(invalid)

            payloads = [event_payloads[int(event["sequence"])] for event in action_events]
            if (
                payloads[0] != {"action_id": action_id, "writer": writer}
                or payloads[1]
                != {
                    "package_id": package_id,
                    "package_version": package_version,
                    "package_sha256": package_sha256,
                    "tool_name": tool_name,
                    "parameters": parameters,
                }
                or payloads[2] != {"status": status.value, "result": result}
                or payloads[-1]
                != {"status": status.value, "resulting_revision": resulting_revision}
            ):
                raise SchemaIdentityError(invalid)
            for ordinal, mutation in enumerate(mutations):
                before = _load_json(str(mutation["before_json"]))
                after = _load_json(str(mutation["after_json"]))
                namespace = str(mutation["namespace"])
                key = str(mutation["state_key"])
                identity = (namespace, key)
                if (
                    not canonical_payload_is_safe({namespace: {key: before}})
                    or not canonical_payload_is_safe({namespace: {key: after}})
                    or payloads[3 + ordinal] != {"namespace": namespace, "key": key}
                    or (
                        identity in mutation_final_values
                        and mutation_final_values[identity] != before
                    )
                ):
                    raise SchemaIdentityError(invalid)
                mutation_final_values[identity] = after
            consequence_offset = 3 + len(mutations)
            for ordinal, consequence in enumerate(consequences):
                consequence_payload = _load_payload(str(consequence["payload_json"]))
                consequence_kind = consequence_payload.get("kind")
                if consequence_kind is not None:
                    validate_authority_identity(consequence_kind, "Stored consequence kind")
                if (
                    not canonical_payload_is_safe(consequence_payload)
                    or payloads[consequence_offset + ordinal] != consequence_payload
                    or (status is ActionStatus.FAILED and not valid_player_action_consequence(
                        consequence_payload, expected_revision=expected_revision,
                    ))
                ):
                    raise SchemaIdentityError(invalid)

            idempotency_rows = tuple(
                connection.execute(
                    "SELECT payload_hash, status, result_json, resulting_revision "
                    "FROM idempotency_records WHERE campaign_id = ? AND action_id = ? "
                    "ORDER BY idempotency_key",
                    (campaign_id, action_id),
                )
            )
            if not idempotency_rows or any(
                str(row["payload_hash"]) != payload_hash
                or ActionStatus(str(row["status"])) is not status
                or _load_payload(str(row["result_json"])) != result
                or int(row["resulting_revision"]) != resulting_revision
                for row in idempotency_rows
            ):
                raise SchemaIdentityError(invalid)
        if len(action_event_sequences) != sum(
            1 for event in events if event["action_id"] is not None
        ):
            raise SchemaIdentityError(invalid)
        for interruption in connection.execute(
            "SELECT * FROM narration_interruptions WHERE campaign_id = ?",
            (campaign_id,),
        ):
            turn_id = str(interruption["turn_id"])
            last_action_id = interruption["last_action_id"]
            interruption_status = str(interruption["status"])
            retry_id = interruption["resolving_retry_id"]
            resolved_sequence = interruption["resolved_sequence"]
            if (
                turn_id not in turn_created_revisions
                or (
                    last_action_id is not None
                    and (
                        str(last_action_id) not in action_by_id
                        or str(action_by_id[str(last_action_id)]["turn_id"]) != turn_id
                    )
                )
                or interruption_status not in ("retryable", "resolved")
                or (interruption_status == "retryable" and resolved_sequence is not None)
                or (
                    interruption_status == "resolved"
                    and (retry_id is None or resolved_sequence is None)
                )
            ):
                raise SchemaIdentityError(invalid)
            retry_events = tuple(
                event
                for event in events
                if retry_id is not None
                and event["attempt_id"] is not None
                and str(event["attempt_id"]) == str(retry_id)
            )
            if any(str(event["turn_id"]) != turn_id for event in retry_events):
                raise SchemaIdentityError(invalid)
            if resolved_sequence is not None:
                matched = next(
                    (event for event in events if int(event["sequence"]) == int(resolved_sequence)),
                    None,
                )
                if (
                    matched is None
                    or not retry_events
                    or matched["event_type"] != CanonicalEventType.TALK.value
                    or int(resolved_sequence)
                    != max(int(event["sequence"]) for event in retry_events)
                    or str(matched["attempt_id"]) != str(retry_id)
                ):
                    raise SchemaIdentityError(invalid)
        if any(
            identity not in current_state or current_state[identity] != value
            for identity, value in mutation_final_values.items()
        ):
            raise SchemaIdentityError(invalid)

        if any(
            str(row["action_id"]) not in action_by_id
            for row in connection.execute(
                "SELECT action_id FROM idempotency_records WHERE campaign_id = ?",
                (campaign_id,),
            )
        ):
            raise SchemaIdentityError(invalid)

    @staticmethod
    def _verify_session_identities(connection: sqlite3.Connection) -> None:
        """Reconcile the complete F4 session, plan, and handoff authority graph."""

        invalid = "Authority stored integrity does not match F4"
        if "agent_sessions" not in {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }:
            return
        try:
            sessions = {
                record.session_id: record
                for record in (
                    sqlite_session.session_from_row(row)
                    for row in connection.execute(
                        "SELECT * FROM agent_sessions ORDER BY session_id"
                    )
                )
            }
            messages_by_session: dict[str, tuple[SessionMessage, ...]] = {}
            for session in sessions.values():
                campaign = connection.execute(
                    "SELECT lifecycle, writer FROM campaigns WHERE campaign_id = ?",
                    (session.campaign_id,),
                ).fetchone()
                if campaign is None:
                    raise ValueError("Stored session campaign is unavailable")
                if session.status is SessionStatus.ACTIVE:
                    if str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value:
                        raise ValueError("Stored active session authority is invalid")
                    session_is_writer = str(campaign["writer"]) == session.role.value
                    persistent_builder = (
                        session.role is WriterRole.BUILDER
                        and connection.execute(
                            "SELECT 1 FROM builder_dm_handoffs "
                            "WHERE campaign_id = ? AND builder_session_id = ? LIMIT 1",
                            (session.campaign_id, session.session_id),
                        ).fetchone()
                        is not None
                    )
                    if not session_is_writer and not persistent_builder:
                        raise ValueError("Stored active session authority is invalid")
                if session.parent_session_id is not None:
                    if session.parent_session_id == session.session_id:
                        raise ValueError("Stored child session cannot parent itself")
                    parent = sessions[session.parent_session_id]
                    if (
                        parent.parent_session_id is not None
                        or parent.campaign_id != session.campaign_id
                        or parent.role is not WriterRole.BUILDER
                        or session.role is not WriterRole.BUILDER
                    ):
                        raise ValueError("Stored child session authority is invalid")
                messages = tuple(
                    sqlite_session.message_from_row(row)
                    for row in connection.execute(
                        "SELECT * FROM agent_session_messages "
                        "WHERE session_id = ? ORDER BY sequence",
                        (session.session_id,),
                    )
                )
                if tuple(message.sequence for message in messages) != tuple(
                    range(1, len(messages) + 1)
                ):
                    raise ValueError("Stored session message chronology is invalid")
                message_ids = {message.message_id for message in messages}
                if len(message_ids) != len(messages):
                    raise ValueError("Stored session message identity is duplicated")
                for message in messages:
                    if message.kind is SessionMessageKind.CANONICAL_REFERENCE:
                        event = connection.execute(
                            "SELECT event_type, visibility, safe_payload_json "
                            "FROM canonical_events WHERE campaign_id = ? AND sequence = ?",
                            (session.campaign_id, message.canonical_sequence),
                        ).fetchone()
                        if event is None or message.content != owned_payload(
                            {
                                "event_type": str(event["event_type"]),
                                "visibility": str(event["visibility"]),
                                "safe_payload": _load_payload(str(event["safe_payload_json"])),
                            }
                        ):
                            raise ValueError("Stored canonical session reference is invalid")
                if session.status is SessionStatus.REVIEW_REQUIRED and not any(
                    message.kind is SessionMessageKind.HUMAN_REVIEW for message in messages
                ):
                    raise ValueError("Stored review-required session has no review evidence")
                messages_by_session[session.session_id] = messages

            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if "standalone_builder_chats" in tables:
                for chat in sqlite_session.read_builder_chats(connection):
                    session = chat.session
                    if session.session_id in sessions:
                        raise ValueError("Standalone Builder chat identity collides with campaign")
                    messages = tuple(
                        sqlite_session.message_from_row(row)
                        for row in connection.execute(
                            "SELECT * FROM standalone_builder_chat_messages "
                            "WHERE session_id = ? ORDER BY sequence",
                            (session.session_id,),
                        )
                    )
                    if tuple(message.sequence for message in messages) != tuple(
                        range(1, len(messages) + 1)
                    ):
                        raise ValueError("Stored Builder chat message chronology is invalid")
                    if len({message.message_id for message in messages}) != len(messages):
                        raise ValueError("Stored Builder chat message identity is duplicated")
                    if any(
                        message.kind is SessionMessageKind.CANONICAL_REFERENCE
                        or message.canonical_sequence is not None
                        for message in messages
                    ):
                        raise ValueError("Standalone Builder chat cannot reference campaign events")
                    if session.status is SessionStatus.REVIEW_REQUIRED and not any(
                        message.kind is SessionMessageKind.HUMAN_REVIEW for message in messages
                    ):
                        raise ValueError("Review-required Builder chat has no review evidence")

            plans: dict[str, CampaignPlan] = {}
            for campaign_row in connection.execute(
                "SELECT campaign_id FROM campaigns ORDER BY campaign_id"
            ):
                campaign_id = str(campaign_row["campaign_id"])
                plan_rows = tuple(
                    connection.execute(
                        "SELECT * FROM campaign_plans WHERE campaign_id = ? ORDER BY plan_revision",
                        (campaign_id,),
                    )
                )
                if tuple(int(row["plan_revision"]) for row in plan_rows) != tuple(
                    range(1, len(plan_rows) + 1)
                ):
                    raise ValueError("Stored Campaign Plan revisions are not contiguous")
                for row in plan_rows:
                    plan = sqlite_session.plan_from_row(row)
                    if plan.plan_id in plans:
                        raise ValueError("Stored Campaign Plan identity is duplicated")
                    plans[plan.plan_id] = plan
                    source = sessions.get(plan.source_session_id)
                    campaign = connection.execute(
                        "SELECT lifecycle, writer FROM campaigns WHERE campaign_id = ?",
                        (plan.campaign_id,),
                    ).fetchone()
                    handoff = connection.execute(
                        "SELECT builder_session_id, dm_session_id "
                        "FROM builder_dm_handoffs WHERE campaign_id = ?",
                        (plan.campaign_id,),
                    ).fetchone()
                    stopped_source_is_valid = False
                    if source is not None and source.status is SessionStatus.STOPPED:
                        if (
                            campaign is not None
                            and str(campaign["lifecycle"]) == LifecycleState.ACTIVE.value
                        ):
                            stopped_source_is_valid = (
                                str(campaign["writer"]) == WriterRole.BUILDER.value
                                and handoff is None
                            ) or (
                                str(campaign["writer"]) == WriterRole.DM.value
                                and handoff is not None
                            )
                        elif campaign is not None:
                            lifecycle_state = connection.execute(
                                "SELECT designated_session_id, tombstone_prior_session_id "
                                "FROM campaign_lifecycle_state WHERE campaign_id = ?",
                                (plan.campaign_id,),
                            ).fetchone()
                            if lifecycle_state is not None:
                                designated_source = source.session_id
                                # After handoff, archive retains the GM as writer while
                                # stopping the Interview that supplied the saved Plan.
                                if (
                                    handoff is not None
                                    and handoff["builder_session_id"] == source.session_id
                                ):
                                    designated_source = str(handoff["dm_session_id"])
                                if str(campaign["lifecycle"]) == LifecycleState.ARCHIVED.value:
                                    stopped_source_is_valid = (
                                        lifecycle_state["designated_session_id"]
                                        == designated_source
                                    )
                                elif str(campaign["lifecycle"]) == LifecycleState.TOMBSTONED.value:
                                    stopped_source_is_valid = (
                                        lifecycle_state["tombstone_prior_session_id"]
                                        == designated_source
                                    )
                    sequel = plan.content.get("sequel")
                    source_role_valid = source is not None and (
                        (source.role is WriterRole.BUILDER
                         and source.selected_skill == "BUILD CAMPAIGN PLAN" and sequel is None)
                        or (source.role is WriterRole.DM and isinstance(sequel, Mapping)
                            and handoff is not None
                            and handoff["dm_session_id"] == source.session_id)
                    )
                    if (
                        source is None
                        or source.campaign_id != plan.campaign_id
                        or source.parent_session_id is not None
                        or not source_role_valid
                        or (
                            source.status
                            not in {SessionStatus.ACTIVE, SessionStatus.HANDOFF_CLOSED}
                            and not stopped_source_is_valid
                        )
                    ):
                        raise ValueError("Stored Campaign Plan source session is invalid")
                    source_messages = {
                        message.message_id: message
                        for message in messages_by_session[source.session_id]
                    }
                    if any(
                        evidence_id not in source_messages
                        or source_messages[evidence_id].kind is not SessionMessageKind.EVIDENCE
                        for evidence_id in plan.provenance_evidence_ids
                    ):
                        raise ValueError("Stored Campaign Plan provenance is invalid")
                    if hashlib.sha256(_dump(plan.content).encode("utf-8")).hexdigest() != (
                        plan.content_sha256
                    ):
                        raise ValueError("Stored Campaign Plan content identity is invalid")
                    if isinstance(sequel, Mapping):
                        from sylvia_gm.domain.campaign_sequel import digest
                        from sylvia_gm.infrastructure.sqlite_sequel import verify_sequel

                        previous = plans.get(str(sequel.get("previous_plan_id")))
                        if previous is None:
                            raise ValueError("Stored sequel predecessor is unavailable")
                        verify_sequel(plan, previous)
                        evidence = source_messages[plan.provenance_evidence_ids[0]]
                        if (evidence.content != {
                            "kind": "campaign_sequel_adoption", "plan_id": plan.plan_id,
                            "request_sha256": digest(sequel["request"]),
                        } or evidence.sequence != sequel["preview"]["latest_message_sequence"] + 1):
                            raise ValueError("Stored sequel adoption evidence is invalid")
                    expected_plan_payload = campaign_plan_payload_sha256(
                        plan_id=plan.plan_id,
                        campaign_id=plan.campaign_id,
                        source_session_id=plan.source_session_id,
                        expected_revision=plan.resulting_campaign_revision - 1,
                        expected_package_lock_id=plan.package_lock_id,
                        content_sha256=plan.content_sha256,
                        content=plan.content,
                        provenance_evidence_ids=plan.provenance_evidence_ids,
                        validation_outcome=plan.validation_outcome,
                        acceptance_status=plan.acceptance_status,
                    )
                    revision = connection.execute(
                        "SELECT plan_id FROM campaign_revisions "
                        "WHERE campaign_id = ? AND revision = ?",
                        (plan.campaign_id, plan.resulting_campaign_revision),
                    ).fetchone()
                    lock = connection.execute(
                        "SELECT campaign_id FROM campaign_locks WHERE lock_id = ?",
                        (plan.package_lock_id,),
                    ).fetchone()
                    if (
                        expected_plan_payload != plan.payload_sha256
                        or plan.resulting_campaign_revision < plan.plan_revision
                        or revision is None
                        or str(revision["plan_id"]) != plan.plan_id
                        or lock is None
                        or str(lock["campaign_id"]) != plan.campaign_id
                    ):
                        raise ValueError("Stored Campaign Plan authority is invalid")

            handoffs: dict[str, BuilderDMHandoff] = {}
            for row in connection.execute(
                "SELECT * FROM builder_dm_handoffs ORDER BY campaign_id, handoff_id"
            ):
                handoff = sqlite_session.handoff_from_row(row)
                if handoff.handoff_id in handoffs:
                    raise ValueError("Stored Builder-to-DM handoff identity is duplicated")
                handoffs[handoff.handoff_id] = handoff
                builder = sessions.get(handoff.builder_session_id)
                dm = sessions.get(handoff.dm_session_id)
                handoff_plan = plans.get(handoff.plan_id)
                campaign = connection.execute(
                    "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns "
                    "WHERE campaign_id = ?",
                    (handoff.campaign_id,),
                ).fetchone()
                turn = connection.execute(
                    "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                    (handoff.campaign_id, handoff.completed_turn_id),
                ).fetchone()
                revision = connection.execute(
                    "SELECT handoff_id FROM campaign_revisions "
                    "WHERE campaign_id = ? AND revision = ?",
                    (handoff.campaign_id, handoff.resulting_campaign_revision),
                ).fetchone()
                dm_projection_is_valid = False
                if campaign is not None and dm is not None:
                    current_lifecycle = str(campaign["lifecycle"])
                    current_writer = str(campaign["writer"])
                    if current_lifecycle == LifecycleState.ACTIVE.value:
                        dm_projection_is_valid = (
                            current_writer == WriterRole.DM.value
                            and dm.status
                            in {SessionStatus.ACTIVE, SessionStatus.STOPPED}
                        )
                    elif current_writer == WriterRole.NONE.value:
                        lifecycle_state = connection.execute(
                            "SELECT designated_session_id, tombstone_prior_session_id "
                            "FROM campaign_lifecycle_state WHERE campaign_id = ?",
                            (handoff.campaign_id,),
                        ).fetchone()
                        if lifecycle_state is not None and dm.status is SessionStatus.STOPPED:
                            if current_lifecycle == LifecycleState.ARCHIVED.value:
                                dm_projection_is_valid = (
                                    lifecycle_state["designated_session_id"]
                                    == handoff.dm_session_id
                                )
                            elif current_lifecycle == LifecycleState.TOMBSTONED.value:
                                dm_projection_is_valid = (
                                    lifecycle_state["tombstone_prior_session_id"]
                                    == handoff.dm_session_id
                                )
                if (
                    builder is None
                    or builder.campaign_id != handoff.campaign_id
                    or builder.parent_session_id is not None
                    or builder.role is not WriterRole.BUILDER
                    or builder.status not in {SessionStatus.ACTIVE, SessionStatus.STOPPED}
                    or builder.selected_skill != "BUILD CAMPAIGN PLAN"
                    or dm is None
                    or dm.campaign_id != handoff.campaign_id
                    or dm.parent_session_id is not None
                    or dm.role is not WriterRole.DM
                    or not dm_projection_is_valid
                    or handoff_plan is None
                    or handoff_plan.campaign_id != handoff.campaign_id
                    or handoff_plan.source_session_id != handoff.builder_session_id
                    or handoff_plan.package_lock_id != handoff.package_lock_id
                    or handoff_plan.plan_revision != handoff.plan_revision
                    or handoff_plan.content_sha256 != handoff.plan_content_sha256
                    or handoff_plan.resulting_campaign_revision > handoff.expected_revision
                    or campaign is None
                    or int(campaign["revision"]) < handoff.resulting_campaign_revision
                    or not sqlite_packages.lock_is_ancestor(
                        connection, handoff.campaign_id, handoff.package_lock_id,
                    )
                    or turn is None
                    or str(turn["status"]) != TurnStatus.COMPLETED.value
                    or revision is None
                    or str(revision["handoff_id"]) != handoff.handoff_id
                ):
                    raise ValueError("Stored Builder-to-DM handoff relationships are invalid")
                # The immutable handoff certifies its original package basis.
                # Later supported activation preserves it as an ancestor; it
                # must not be rewritten to today's package or model choice.
                handoff_lock = sqlite_packages.read_lock_record(connection, handoff.package_lock_id)
                candidate = (
                    None
                    if handoff.package_candidate_id is None
                    else sqlite_packages.read_candidate(connection, handoff.package_candidate_id)
                )
                if (
                    handoff_lock.lock_id != handoff.package_lock_id
                    or not _handoff_candidate_matches_lock(handoff_lock, candidate)
                ):
                    raise ValueError("Stored Builder-to-DM handoff package basis is invalid")
                primary_manifest = sqlite_packages.primary_manifest_for_lock(
                    connection,
                    handoff.package_lock_id,
                )
                exact_minimum = (
                    None
                    if primary_manifest is None
                    else primary_manifest.minimum_model_capability_tier
                )
                if (
                    handoff.model_guidance.minimum_tier_id != exact_minimum
                    or not handoff.model_guidance.may_continue
                ):
                    raise ValueError("Stored Builder-to-DM model guidance is invalid")
                # The opening records its original model, not today's session choice.
                # Legacy same-provider switches can use the current provider only
                # when it still reproduces the original, unchanged request digest.
                chronology = _load_payload(str(row["safe_payload_json"]))
                opening_provider = chronology.get("dm_provider_id", dm.provider_id)
                if not isinstance(opening_provider, str):
                    raise ValueError("Stored Builder-to-DM provider identity is invalid")
                expected_handoff_payload = builder_dm_handoff_payload_sha256(
                    handoff_id=handoff.handoff_id,
                    campaign_id=handoff.campaign_id,
                    builder_session_id=handoff.builder_session_id,
                    dm_session_id=handoff.dm_session_id,
                    dm_provider_id=opening_provider,
                    dm_model_id=handoff.model_guidance.model_id,
                    expected_writer=WriterRole.BUILDER,
                    expected_revision=handoff.expected_revision,
                    expected_package_lock_id=handoff.package_lock_id,
                    expected_package_candidate_id=handoff.package_candidate_id,
                    plan_id=handoff.plan_id,
                    expected_plan_revision=handoff.plan_revision,
                    expected_plan_content_sha256=handoff.plan_content_sha256,
                    completed_turn_id=handoff.completed_turn_id,
                    model_guidance_sha256=model_guidance_payload_sha256(handoff.model_guidance),
                )
                if expected_handoff_payload != handoff.payload_sha256:
                    raise ValueError("Stored Builder-to-DM handoff payload identity is invalid")

            handoff_builders = {handoff.builder_session_id for handoff in handoffs.values()}
            for session in sessions.values():
                if (
                    session.status is SessionStatus.HANDOFF_CLOSED
                    and session.session_id not in handoff_builders
                ):
                    raise ValueError("Stored handoff-closed session has no handoff authority")
                if (
                    session.parent_session_id is not None
                    and session.status is SessionStatus.COMPLETED
                ):
                    parent_messages = messages_by_session[session.parent_session_id]
                    if not any(
                        message.kind is SessionMessageKind.EVIDENCE
                        and message.content.get("child_session_id") == session.session_id
                        and message.content.get("authoritative") is False
                        for message in parent_messages
                    ):
                        raise ValueError("Stored completed child has no non-authoritative return")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SchemaIdentityError(invalid) from error

    def _ensure_session_schema(self) -> None:
        """Apply additive migration 4 only when the F4 surface is first used."""

        connection = self._connect()
        try:
            connection.executescript("BEGIN IMMEDIATE;")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version in (
                _SESSION_SCHEMA_VERSION,
                _STATE_OWNERSHIP_SCHEMA_VERSION,
                _PRE_REASONING_SCHEMA_VERSION,
                _PRE_EFFORT_SCHEMA_VERSION,
                _CAMPAIGN_SCHEMA_VERSION,
            ):
                connection.rollback()
                return
            if version != _SCHEMA_VERSION:
                raise SchemaIdentityError(
                    "Session schema cannot migrate from this authority version"
                )
            self._preserve_pre_migration_source(version)
            for statement in _MIGRATION_4.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
                (4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256),
            )
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_campaign_schema(self, connection: sqlite3.Connection) -> None:
        """Apply admitted campaign migrations in the caller's transaction.

        Verify the predecessor's exact schema identity, preserve its source,
        and advance through the supported migrations to _CAMPAIGN_SCHEMA_VERSION.
        """

        version = self._verify_exact_schema_identity(connection)
        if version == _CAMPAIGN_SCHEMA_VERSION:
            return
        self._preserve_pre_migration_source(version)
        if version == _PRE_REASONING_SCHEMA_VERSION:
            self._apply_reasoning_migration(connection)
            version = _PRE_EFFORT_SCHEMA_VERSION
        if version == _PRE_EFFORT_SCHEMA_VERSION:
            self._apply_effort_migration(connection)
            return
        if version == _SCHEMA_VERSION:
            for statement in _MIGRATION_4.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
                (4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256),
            )
            connection.execute("PRAGMA user_version = 4")
            version = _SESSION_SCHEMA_VERSION
        if version == _SESSION_SCHEMA_VERSION:
            for statement in _MIGRATION_5.split(";"):
                if statement.strip():
                    connection.execute(statement)
            sqlite_campaign.migrate_v4_participants(connection)
            self._insert_campaign_identity_extension(connection)
            sqlite_campaign.apply_lifecycle_extension(connection)
            connection.execute(
                "INSERT INTO authority_schema_extensions "
                "(extension_name, extension_sha256, predecessor_migration_name, "
                "predecessor_sql_sha256) VALUES (?, ?, ?, ?)",
                (
                    _LIFECYCLE_EXTENSION_NAME,
                    _LIFECYCLE_EXTENSION_SHA256,
                    _CAMPAIGN_IDENTITY_EXTENSION_NAME,
                    _CAMPAIGN_IDENTITY_DELTA_SHA256,
                ),
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
                (5, _MIGRATION_5_NAME, _MIGRATION_5_SHA256),
            )
            connection.execute("PRAGMA user_version = 5")
            version = _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION
        if version == _PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION:
            live_identity = _schema_identity(connection)
            if live_identity == _EXPECTED_CAMPAIGN_SCHEMA_IDENTITY:
                sqlite_recovery.apply_recovery_extension(
                    connection,
                    predecessor_name=_LIFECYCLE_EXTENSION_NAME,
                    predecessor_sha256=_LIFECYCLE_EXTENSION_SHA256,
                )
                live_identity = _schema_identity(connection)
            if live_identity not in {
                _EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY,
                _EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY,
            }:
                raise SchemaIdentityError("State ownership migration predecessor is not exact")
            sqlite_state_ownership.apply_migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
                (6, _MIGRATION_6_NAME, _MIGRATION_6_SHA256),
            )
            connection.execute("PRAGMA user_version = 6")
            version = _STATE_OWNERSHIP_SCHEMA_VERSION
        if version == _STATE_OWNERSHIP_SCHEMA_VERSION:
            sqlite_mechanics_choice.apply_migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
                (7, _MIGRATION_7_NAME, _MIGRATION_7_SHA256),
            )
            connection.execute("PRAGMA user_version = 7")
            version = _PRE_BUILDER_CHAT_SCHEMA_VERSION
        if version == _PRE_BUILDER_CHAT_SCHEMA_VERSION:
            for statement in _MIGRATION_8.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_name, sql_sha256) "
                "VALUES (?, ?, ?)",
                (8, _MIGRATION_8_NAME, _MIGRATION_8_SHA256),
            )
            connection.execute("PRAGMA user_version = 8")
            version = _PRE_CONTINUITY_SCHEMA_VERSION
        if version == _PRE_CONTINUITY_SCHEMA_VERSION:
            sqlite_continuity.apply_migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
                (9, _MIGRATION_9_NAME, _MIGRATION_9_SHA256),
            )
            connection.execute("PRAGMA user_version = 9")
            version = _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION
        if version != _PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION:
            raise SchemaIdentityError("Draft lifecycle migration predecessor is invalid")
        sqlite_draft_lifecycle.apply_migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (10, _MIGRATION_10_NAME, _MIGRATION_10_SHA256),
        )
        connection.execute("PRAGMA user_version = 10")
        self._apply_reasoning_migration(connection)
        self._apply_effort_migration(connection)

    def _ensure_purge_schema(self, connection: sqlite3.Connection) -> None:
        """Install purge authority inside the caller-owned current transaction."""

        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != _CAMPAIGN_SCHEMA_VERSION:
            raise SchemaIdentityError("Campaign purge requires state ownership authority")
        live_identity = _schema_identity(connection)
        if live_identity != _EXPECTED_EFFORT_SCHEMA_IDENTITY:
            raise SchemaIdentityError("Campaign purge predecessor schema identity is not exact")
        if connection.execute(
            "SELECT 1 FROM authority_schema_extensions WHERE extension_name = ?",
            (_PURGE_EXTENSION_NAME,),
        ).fetchone() is not None:
            return
        connection.execute(
            "INSERT INTO authority_schema_extensions "
            "(extension_name, extension_sha256, predecessor_migration_name, "
            "predecessor_sql_sha256) VALUES (?, ?, ?, ?)",
            (
                _PURGE_EXTENSION_NAME,
                _PURGE_EXTENSION_SHA256,
                sqlite_recovery.RECOVERY_EXTENSION_NAME,
                sqlite_recovery.RECOVERY_EXTENSION_SHA256,
            ),
        )
        self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)

    def create_agent_session(self, record: SessionRecord) -> SessionRecord:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if record.reasoning_effort is not None or record.reasoning_effort_once is not None:
                self._ensure_campaign_schema(connection)
            result = sqlite_session.insert_session(connection, record)
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def create_builder_chat(
        self,
        *,
        session_id: str,
        provider_id: str,
        model_id: str,
        display_name: str,
    ) -> BuilderChatRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            result = sqlite_session.insert_builder_chat(
                connection,
                session_id=session_id,
                provider_id=provider_id,
                model_id=model_id,
                display_name=display_name,
            )
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def builder_chat_index(self) -> tuple[BuilderChatRecord, ...]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            result = sqlite_session.read_builder_chats(connection)
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def rename_builder_chat(
        self, session_id: str, display_name: str, *, automatic: bool = False
    ) -> BuilderChatRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            result = sqlite_session.rename_builder_chat(
                connection, session_id, display_name, automatic=automatic
            )
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def delete_builder_chat(self, session_id: str) -> BuilderChatRecord:
        """Delete one standalone chat and workspace before committing the DB outcome."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            result = sqlite_session.delete_builder_chat(connection, session_id)
            builder_workspace_migration.remove_builder_workspace(
                self._database_path.parent, session_id=session_id
            )
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def update_agent_session_reasoning(
        self, session_id: str, *, effort: str | None, once: str | None,
    ) -> SessionRecord:
        """Persist saved and pending effort through the existing session authority."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            record = sqlite_effort.update_preferences(
                connection, session_id, effort=effort, once=once,
            )
            connection.commit()
            return record
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def update_agent_session_model(
        self,
        session_id: str,
        *,
        provider_id: str,
        model_id: str,
    ) -> SessionRecord:
        """Persist one Builder or DM session's configured provider/model pair."""

        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            record = sqlite_session.update_session_model(
                connection,
                session_id,
                provider_id=provider_id,
                model_id=model_id,
            )
            connection.commit()
            return record
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def resume_agent_session(self, session_id: str) -> SessionRecord:
        """Explicitly reactivate the exact stopped top-level writer session."""

        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = sqlite_session.read_session(connection, session_id)
            if current.campaign_id is None:
                if current.parent_session_id is not None:
                    raise CommandRejected("Standalone Builder chat cannot be a child session")
                result = sqlite_session.restore_stopped_session(connection, session_id)
                connection.commit()
                return result
            campaign = connection.execute(
                "SELECT lifecycle, writer, revision FROM campaigns WHERE campaign_id = ?",
                (current.campaign_id,),
            ).fetchone()
            if (
                current.parent_session_id is not None
                or campaign is None
                or str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value
                or str(campaign["writer"]) != current.role.value
            ):
                raise CommandRejected("Stopped session no longer owns active campaign authority")
            result = sqlite_session.restore_stopped_session(connection, session_id)
            if current.role is WriterRole.DM:
                open_turn = connection.execute(
                    "SELECT turn_id FROM turns WHERE campaign_id = ? AND status = 'open'",
                    (current.campaign_id,),
                ).fetchone()
                if open_turn is None:
                    active_turn_id = f"turn-dm-{uuid4().hex}"
                    connection.execute(
                        "INSERT INTO turns(campaign_id, turn_id, status, created_revision) "
                        "VALUES (?, ?, 'open', ?)",
                        (
                            current.campaign_id,
                            active_turn_id,
                            int(campaign["revision"]),
                        ),
                    )
                else:
                    active_turn_id = str(open_turn["turn_id"])
                connection.execute(
                    "UPDATE narration_interruptions SET turn_id = ? "
                    "WHERE campaign_id = ? AND status = 'retryable' "
                    "AND last_action_id IS NULL AND turn_id != ?",
                    (active_turn_id, current.campaign_id, active_turn_id),
                )
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def create_builder_child_session(self, record: SessionRecord) -> SessionRecord:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            captured = sqlite_session.insert_child_session(connection, record)
            connection.commit()
            return captured
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def return_builder_child_evidence(
        self, evidence: ChildEvidence
    ) -> tuple[ChildEvidence, SessionRecord]:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = sqlite_session.return_child_evidence(connection, evidence)
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def campaign_session_contexts(self, campaign_id: str) -> tuple[SessionContext, ...]:
        """Read only the selected campaign's durable session provenance."""

        connection = self._connect_read_only()
        try:
            connection.execute("BEGIN")
            session_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_sessions'"
            ).fetchone()
            if session_table is None:
                connection.rollback()
                return ()
            identities = connection.execute(
                "SELECT session_id FROM agent_sessions WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
            contexts = tuple(
                sqlite_session.read_context(connection, str(row["session_id"]))
                for row in identities
            )
            connection.commit()
            return contexts
        finally:
            connection.close()

    def session_activity_basis(
        self, session_id: str, attempt_id: str | None = None,
    ) -> SessionActivityBasis:
        """Read attempt recovery facts from one consistent saved state."""
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            basis = sqlite_session.read_activity_basis(connection, session_id, attempt_id)
            connection.commit()
            return basis
        finally:
            connection.close()

    def append_private_provider_record(self, record: ProviderPrivateRecord) -> None:
        """Commit private ingress independently of ordinary history and Full Audit capture."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            sqlite_reasoning.append_record(
                connection, session_id=record.session_id, attempt_id=record.attempt_id,
                response_id=record.response_id, turn_id=record.turn_id,
                basis_message_id=record.basis_message_id, source_order=record.source_order,
                provider_id=record.provider_id, model_id=record.model_id,
                kind=record.kind, payload=record.payload,
            )
            connection.commit()
        finally:
            connection.close()

    def finish_private_provider_attempt(
        self, session_id: str, attempt_id: str, *, completed: bool,
    ) -> None:
        """Finish retained chunks without writing a synthetic Thinking record."""
        connection = self._connect()
        try:
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) < 11:
                return
            connection.execute("BEGIN IMMEDIATE")
            sqlite_reasoning.finish_attempt(connection, session_id, attempt_id, completed=completed)
            connection.commit()
        finally:
            connection.close()

    def private_provider_index(self, session_id: str) -> list[dict[str, object]]:
        """Read availability metadata without constructing any private payload."""
        connection = self._connect_read_only()
        try:
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) < 11:
                return []
            return sqlite_reasoning.inspection_index(connection, session_id)
        finally:
            connection.close()

    def inspect_provider_thinking(
        self, session_id: str, attempt_id: str, response_id: str,
    ) -> str | None:
        """Read one deliberately selected response's readable thinking, never native bytes."""
        connection = self._connect_read_only()
        try:
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) < 11:
                return None
            return sqlite_reasoning.read_thinking(connection, session_id, attempt_id, response_id)
        finally:
            connection.close()

    def provider_continuation(
        self, session_id: str, response_id: str, *, provider_id: str, model_id: str,
    ) -> Mapping[str, object] | None:
        """Read native continuation only for the originating model's selected exchange."""
        connection = self._connect_read_only()
        try:
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) < 11:
                return None
            return sqlite_reasoning.read_continuation(
                connection, session_id, response_id, provider_id=provider_id, model_id=model_id,
            )
        finally:
            connection.close()

    def actor_context(
        self, session_id: str, context_id: str, *, provider_id: str, model_id: str,
    ) -> Mapping[str, object] | None:
        """Read one actor-owned private history without reading the GM conversation."""
        connection = self._connect_read_only()
        try:
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) < 11:
                return None
            return sqlite_reasoning.read_actor_context(
                connection, session_id, context_id, provider_id=provider_id, model_id=model_id,
            )
        finally:
            connection.close()

    def session_history_page(
        self, session_id: str, *, before_sequence: int | None = None, limit: int = 50,
    ) -> SessionHistoryPage:
        """Keep page rows and current sequence metadata in one read transaction."""
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            page = sqlite_session.read_history_page(
                connection, session_id, before_sequence=before_sequence, limit=limit,
            )
            connection.commit()
            return page
        finally:
            connection.close()

    def session_context(self, session_id: str) -> SessionContext:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            context = sqlite_session.read_context(connection, session_id)
            connection.commit()
            return context
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def update_agent_session_status(self, session_id: str, status: SessionStatus) -> SessionRecord:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            record = sqlite_session.update_session_status(connection, session_id, status)
            connection.commit()
            return record
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def require_agent_human_review(
        self,
        *,
        session_id: str,
        review_id: str,
        content: SafePayload,
    ) -> SessionRecord:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            record = sqlite_session.require_human_review(
                connection,
                session_id=session_id,
                review_id=review_id,
                content=content,
            )
            connection.commit()
            return record
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def append_agent_message(
        self,
        *,
        session_id: str,
        message_id: str,
        kind: SessionMessageKind,
        content: SafePayload,
        canonical_sequence: int | None = None,
    ) -> SessionMessage:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            message = sqlite_session.append_message(
                connection,
                session_id=session_id,
                message_id=message_id,
                kind=kind,
                content=content,
                canonical_sequence=canonical_sequence,
            )
            connection.commit()
            return message
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def append_canonical_references(
        self, session_id: str, events: Sequence[CanonicalEvent]
    ) -> tuple[SessionMessage, ...]:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            messages = tuple(
                sqlite_session.append_message(
                    connection,
                    session_id=session_id,
                    message_id=f"canonical-{event.sequence}",
                    kind=SessionMessageKind.CANONICAL_REFERENCE,
                    content=owned_payload(
                        {
                            "event_type": event.event_type.value,
                            "visibility": event.visibility.value,
                            "safe_payload": event.safe_payload,
                        }
                    ),
                    canonical_sequence=event.sequence,
                )
                for event in events
            )
            connection.commit()
            return messages
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def campaign_plan_hit(self, plan_id: str) -> CampaignPlan | None:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            return sqlite_session.read_plan(connection, plan_id)
        finally:
            connection.close()

    def accepted_campaign_plan(self, campaign_id: str) -> CampaignPlan | None:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM campaign_plans WHERE campaign_id = ? "
                "AND acceptance_status = 'accepted' ORDER BY plan_revision DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            return None if row is None else sqlite_session.plan_from_row(row)
        finally:
            connection.close()

    def handoff_hit(self, handoff_id: str) -> BuilderDMHandoff | None:
        self._ensure_session_schema()
        connection = self._connect()
        try:
            return sqlite_session.read_handoff(connection, handoff_id)
        finally:
            connection.close()

    def campaign_adventures(self, campaign_id: str) -> tuple[CampaignPlan, ...]:
        """Read the ordered accepted adventure lineage, excluding abandoned Builder revisions."""
        from sylvia_gm.infrastructure.sqlite_sequel import adventure_rows

        connection = self._connect_read_only()
        try:
            return tuple(sqlite_session.plan_from_row(row)
                         for row in adventure_rows(connection, campaign_id))
        finally:
            connection.close()

    def commit_campaign_sequel(self, plan: CampaignPlan) -> CampaignPlan:
        """Use the same durability domain for the reviewed source and its admission receipt."""
        from sylvia_gm.infrastructure.sqlite_sequel import commit_sequel

        connection = self._connect()
        try:
            return commit_sequel(connection, plan)
        finally:
            connection.close()

    def commit_builder_dm_handoff(
        self,
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
        model_guidance: ModelGuidanceDecision,
        payload_sha256: str,
    ) -> BuilderDMHandoff:
        """Commit all handoff authority fields in one SQLite transaction."""

        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = sqlite_session.read_handoff(connection, handoff_id)
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise IdempotencyConflict(
                        "Builder-to-DM handoff identity payload does not match"
                    )
                connection.rollback()
                return existing
            self._handoff_failure_stage("before")
            campaign = connection.execute(
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns "
                "WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise CommandRejected("Campaign is unavailable")
            if (
                str(campaign["writer"]) != expected_writer.value
                or expected_writer is not WriterRole.BUILDER
            ):
                raise WrongWriter("Builder is not the authoritative campaign writer")
            if int(campaign["revision"]) != expected_revision:
                raise StaleRevision("Builder-to-DM handoff expected revision is stale")
            if str(campaign["current_lock_id"]) != expected_package_lock_id:
                raise CommandRejected(
                    "Builder-to-DM handoff package lock is not exact",
                    code="handoff_lock_mismatch",
                )
            primary_manifest = sqlite_packages.primary_manifest_for_lock(
                connection,
                expected_package_lock_id,
            )
            exact_minimum = (
                None if primary_manifest is None else primary_manifest.minimum_model_capability_tier
            )
            if (
                model_guidance.model_id != dm_model_id
                or model_guidance.minimum_tier_id != exact_minimum
                or not model_guidance.may_continue
            ):
                raise CommandRejected(
                    "Builder-to-DM handoff model guidance is not exact or confirmed",
                    code="handoff_model_guidance_mismatch",
                )
            if str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value:
                raise CommandRejected(
                    "Builder-to-DM handoff campaign is not quiescent",
                    code="handoff_lifecycle_not_quiescent",
                )
            current_lock = sqlite_packages.current_lock_record(connection, campaign_id)
            candidate = (
                None
                if expected_package_candidate_id is None
                else sqlite_packages.read_candidate(connection, expected_package_candidate_id)
            )
            if not _handoff_candidate_matches_lock(current_lock, candidate):
                raise CommandRejected(
                    "Builder-to-DM handoff package candidate is not exact and published",
                    code="handoff_candidate_mismatch",
                )
            conflicting_handoff = connection.execute(
                "SELECT handoff_id FROM builder_dm_handoffs WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if conflicting_handoff is not None:
                raise IdempotencyConflict("Campaign already has another Builder-to-DM handoff")
            plan = sqlite_session.read_plan(connection, plan_id)
            latest_plan_revision = connection.execute(
                "SELECT MAX(plan_revision) FROM campaign_plans WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
            if (
                plan is None
                or plan.campaign_id != campaign_id
                or plan.acceptance_status is not PlanAcceptanceStatus.ACCEPTED
                or plan.validation_outcome is not PlanValidationOutcome.VALID
                or plan.plan_revision != expected_plan_revision
                or plan.plan_revision != int(latest_plan_revision or 0)
                or plan.content_sha256 != expected_plan_content_sha256
                or plan.package_lock_id != expected_package_lock_id
                or plan.source_session_id != builder_session_id
                or not plan.provenance_evidence_ids
                or plan.resulting_campaign_revision > expected_revision
            ):
                raise CommandRejected(
                    "Builder-to-DM handoff Campaign Plan is not exact and accepted",
                    code="handoff_plan_mismatch",
                )
            builder = sqlite_session.read_session(connection, builder_session_id)
            if (
                builder.campaign_id != campaign_id
                or builder.role is not WriterRole.BUILDER
                or builder.status is not SessionStatus.ACTIVE
                or builder.parent_session_id is not None
            ):
                raise CommandRejected("Builder handoff session is not authoritative")
            guided_state = connection.execute(
                "SELECT 1 FROM agent_session_messages WHERE session_id = ? "
                "AND kind = 'evidence' AND content_json LIKE '%campaign_interview_state%' LIMIT 1",
                (builder_session_id,),
            ).fetchone()
            ready_message: SessionMessage | None = None
            if guided_state is not None:
                for row in connection.execute(
                    "SELECT * FROM agent_session_messages WHERE session_id = ? "
                    "AND kind = 'user' ORDER BY sequence DESC",
                    (builder_session_id,),
                ):
                    candidate = sqlite_session.message_from_row(row)
                    if (
                        candidate.content.get("interview_step") == "ready"
                        and candidate.content.get("text") == "Ready to Begin"
                    ):
                        ready_message = candidate
                        break
                if ready_message is None:
                    raise CommandRejected(
                        "Guided Campaign handoff requires the genuine Ready to Begin input"
                    )
            active_children = connection.execute(
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? "
                "AND parent_session_id IS NOT NULL "
                "AND status NOT IN ('stopped', 'completed', 'handoff_closed') LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if active_children is not None:
                raise CommandRejected(
                    "Builder-to-DM handoff has active child work",
                    code="handoff_child_work_active",
                )
            blocker = connection.execute(
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? "
                "AND status = 'review_required' LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if blocker is not None:
                raise CommandRejected(
                    "Builder-to-DM handoff has an unresolved human-review blocker",
                    code="handoff_review_blocked",
                )
            other_active = connection.execute(
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? AND status = 'active' "
                "AND session_id != ? LIMIT 1",
                (campaign_id, builder_session_id),
            ).fetchone()
            if other_active is not None:
                raise CommandRejected(
                    "Builder-to-DM handoff has another active session",
                    code="handoff_session_work_active",
                )
            if (
                connection.execute(
                    "SELECT 1 FROM turns WHERE campaign_id = ? AND status != 'completed' LIMIT 1",
                    (campaign_id,),
                ).fetchone()
                is not None
            ):
                raise CommandRejected(
                    "Builder-to-DM handoff has an incomplete TURN",
                    code="handoff_turn_incomplete",
                )
            if (
                connection.execute(
                    "SELECT 1 FROM actions WHERE campaign_id = ? "
                    "AND status NOT IN ('committed', 'failed') LIMIT 1",
                    (campaign_id,),
                ).fetchone()
                is not None
            ):
                raise CommandRejected(
                    "Builder-to-DM handoff has an incomplete action",
                    code="handoff_action_incomplete",
                )
            turn = connection.execute(
                "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                (campaign_id, completed_turn_id),
            ).fetchone()
            if turn is None or str(turn["status"]) != TurnStatus.COMPLETED.value:
                raise CommandRejected(
                    "Builder-to-DM handoff requires a completed TURN",
                    code="handoff_turn_incomplete",
                )
            if (
                connection.execute(
                    "SELECT 1 FROM agent_sessions WHERE session_id = ?",
                    (dm_session_id,),
                ).fetchone()
                is not None
            ):
                raise IdempotencyConflict("DM session identity is already in use")
            expected_payload = builder_dm_handoff_payload_sha256(
                handoff_id=handoff_id,
                campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                dm_session_id=dm_session_id,
                dm_provider_id=dm_provider_id,
                dm_model_id=dm_model_id,
                expected_writer=expected_writer,
                expected_revision=expected_revision,
                expected_package_lock_id=expected_package_lock_id,
                expected_package_candidate_id=expected_package_candidate_id,
                plan_id=plan_id,
                expected_plan_revision=expected_plan_revision,
                expected_plan_content_sha256=expected_plan_content_sha256,
                completed_turn_id=completed_turn_id,
                model_guidance_sha256=model_guidance_payload_sha256(model_guidance),
            )
            if expected_payload != payload_sha256:
                raise IdempotencyConflict("Builder-to-DM handoff payload identity does not match")
            resulting_revision = expected_revision + 1
            chronology = owned_payload(
                {
                    "campaign_id": campaign_id,
                    "builder_session_id": builder_session_id,
                    "dm_session_id": dm_session_id,
                    "dm_provider_id": dm_provider_id,
                    "package_lock_id": expected_package_lock_id,
                    "package_candidate_id": expected_package_candidate_id,
                    "plan_id": plan_id,
                    "resulting_campaign_revision": resulting_revision,
                    "completed_turn_id": completed_turn_id,
                    "model_guidance": model_guidance_payload(model_guidance),
                }
            )
            handoff = BuilderDMHandoff(
                handoff_id=handoff_id,
                campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                dm_session_id=dm_session_id,
                package_lock_id=expected_package_lock_id,
                package_candidate_id=expected_package_candidate_id,
                plan_id=plan_id,
                plan_revision=expected_plan_revision,
                plan_content_sha256=expected_plan_content_sha256,
                expected_revision=expected_revision,
                resulting_campaign_revision=resulting_revision,
                chronology_ordinal=1,
                completed_turn_id=completed_turn_id,
                model_guidance=model_guidance,
                payload_sha256=payload_sha256,
            )
            updated = connection.execute(
                "UPDATE campaigns SET writer = 'dm', revision = ? WHERE campaign_id = ? "
                "AND writer = 'builder' AND revision = ? AND lifecycle = 'active' "
                "AND current_lock_id = ?",
                (
                    resulting_revision,
                    campaign_id,
                    expected_revision,
                    expected_package_lock_id,
                ),
            )
            if updated.rowcount != 1:
                raise StaleRevision("Builder-to-DM handoff revision fence rejected transition")
            if (
                int(connection.execute("PRAGMA user_version").fetchone()[0])
                == _CAMPAIGN_SCHEMA_VERSION
            ):
                lifecycle_updated = connection.execute(
                    "UPDATE campaign_lifecycle_state SET retained_active_writer = 'dm' "
                    "WHERE campaign_id = ? AND retained_active_writer = 'builder' "
                    "AND tombstone_prior_lifecycle IS NULL",
                    (campaign_id,),
                )
                if lifecycle_updated.rowcount != 1:
                    raise SchemaIdentityError("Campaign lifecycle authority is inconsistent")
            sqlite_session.insert_session(
                connection,
                SessionRecord(
                    session_id=dm_session_id,
                    campaign_id=campaign_id,
                    role=WriterRole.DM,
                    provider_id=dm_provider_id,
                    model_id=dm_model_id,
                    status=SessionStatus.ACTIVE,
                ),
            )
            structured_plan = plan.content.get("campaign_plan")
            if isinstance(structured_plan, Mapping):
                sqlite_continuity.seed_initial_state(
                    connection,
                    campaign_id=campaign_id,
                    plan=cast(Mapping[str, object], structured_plan),
                    admitted_revision=resulting_revision,
                )
            if ready_message is not None:
                sqlite_session.append_message(
                    connection,
                    session_id=dm_session_id,
                    message_id=f"ready-reference-{ready_message.message_id}",
                    kind=SessionMessageKind.EVIDENCE,
                    content=owned_payload(
                        {
                            "kind": "ready_to_begin_reference",
                            "source_session_id": builder_session_id,
                            "source_message_id": ready_message.message_id,
                        }
                    ),
                )
            connection.execute(
                "INSERT INTO builder_dm_handoffs "
                "(handoff_id, campaign_id, payload_sha256, builder_session_id, dm_session_id, "
                "package_lock_id, package_candidate_id, plan_id, "
                "plan_revision, plan_content_sha256, expected_revision, "
                "resulting_campaign_revision, chronology_ordinal, completed_turn_id, "
                "safe_payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    handoff.handoff_id,
                    handoff.campaign_id,
                    handoff.payload_sha256,
                    handoff.builder_session_id,
                    handoff.dm_session_id,
                    handoff.package_lock_id,
                    handoff.package_candidate_id,
                    handoff.plan_id,
                    handoff.plan_revision,
                    handoff.plan_content_sha256,
                    handoff.expected_revision,
                    handoff.resulting_campaign_revision,
                    handoff.chronology_ordinal,
                    handoff.completed_turn_id,
                    _dump(chronology),
                ),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id) "
                "VALUES (?, ?, NULL, NULL, NULL, ?)",
                (campaign_id, resulting_revision, handoff_id),
            )
            self._handoff_failure_stage("during")
            connection.commit()
            return handoff
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _handoff_failure_stage(self, stage: str) -> None:
        """Private deterministic fault seam for prewrite and precommit handoff rollback."""

        if stage not in {"before", "during"}:
            raise ValueError("Unknown handoff failure stage")

    def assert_archive_admissible(
        self,
        *,
        campaign_id: str,
        expected_revision: int,
    ) -> CampaignSnapshot:
        """Fence the F4 Archive admission seam without implementing Archive state."""

        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            campaign = connection.execute(
                "SELECT lifecycle, revision FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise CommandRejected("Campaign is unavailable")
            if int(campaign["revision"]) != expected_revision:
                raise StaleRevision("Archive admission expected revision is stale")
            if str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value:
                raise CommandRejected("Archive admission requires an active campaign lifecycle")
            if (
                connection.execute(
                    "SELECT 1 FROM actions WHERE campaign_id = ? "
                    "AND status NOT IN ('committed', 'failed') LIMIT 1",
                    (campaign_id,),
                ).fetchone()
                is not None
            ):
                raise CommandRejected("Archive admission is blocked by incomplete action work")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.snapshot(campaign_id)

    def commit_campaign_plan(
        self,
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
        payload_sha256: str,
    ) -> CampaignPlan:
        """Atomically persist one accepted plan and its single campaign revision."""

        self._ensure_session_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = sqlite_session.read_plan(connection, plan_id)
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise IdempotencyConflict("Campaign Plan identity payload does not match")
                connection.rollback()
                return existing
            campaign = connection.execute(
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns "
                "WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise CommandRejected("Campaign is unavailable")
            if str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value:
                raise CommandRejected("Campaign lifecycle blocks Campaign Plan admission")
            if str(campaign["writer"]) != WriterRole.BUILDER.value:
                raise WrongWriter("Builder is not the authoritative campaign writer")
            if int(campaign["revision"]) != expected_revision:
                raise StaleRevision("Campaign Plan expected revision is stale")
            if str(campaign["current_lock_id"]) != expected_package_lock_id:
                raise CommandRejected(
                    "Campaign Plan expected package lock is not current",
                    code="campaign_plan_lock_mismatch",
                )
            source = sqlite_session.read_session(connection, source_session_id)
            if (
                source.campaign_id != campaign_id
                or source.role is not WriterRole.BUILDER
                or source.status is not SessionStatus.ACTIVE
                or source.selected_skill != "BUILD CAMPAIGN PLAN"
            ):
                raise CommandRejected("Campaign Plan source session is not authoritative")
            evidence_ids = set(provenance_evidence_ids)
            evidence_rows = tuple(
                row
                for row in connection.execute(
                    "SELECT message_id, kind FROM agent_session_messages WHERE session_id = ?",
                    (source_session_id,),
                )
                if str(row["message_id"]) in evidence_ids
            )
            if (
                len(evidence_rows) != len(provenance_evidence_ids)
                or {str(row["message_id"]) for row in evidence_rows} != evidence_ids
                or any(
                    str(row["kind"]) != SessionMessageKind.EVIDENCE.value for row in evidence_rows
                )
            ):
                raise CommandRejected("Campaign Plan provenance evidence is unavailable")
            expected_payload = campaign_plan_payload_sha256(
                plan_id=plan_id,
                campaign_id=campaign_id,
                source_session_id=source_session_id,
                expected_revision=expected_revision,
                expected_package_lock_id=expected_package_lock_id,
                content_sha256=content_sha256,
                content=content,
                provenance_evidence_ids=provenance_evidence_ids,
                validation_outcome=validation_outcome,
                acceptance_status=acceptance_status,
            )
            if expected_payload != payload_sha256:
                raise IdempotencyConflict("Campaign Plan payload identity does not match")
            plan_revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(plan_revision), 0) + 1 FROM campaign_plans "
                    "WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()[0]
            )
            resulting_revision = expected_revision + 1
            plan = CampaignPlan(
                plan_id=plan_id,
                campaign_id=campaign_id,
                package_lock_id=expected_package_lock_id,
                source_session_id=source_session_id,
                content_sha256=content_sha256,
                content=content,
                provenance_evidence_ids=provenance_evidence_ids,
                validation_outcome=validation_outcome,
                acceptance_status=acceptance_status,
                plan_revision=plan_revision,
                resulting_campaign_revision=resulting_revision,
                payload_sha256=payload_sha256,
            )
            connection.execute(
                "INSERT INTO campaign_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.plan_id,
                    plan.campaign_id,
                    plan.package_lock_id,
                    plan.source_session_id,
                    plan.content_sha256,
                    _dump(plan.content),
                    _dump(list(plan.provenance_evidence_ids)),
                    plan.validation_outcome.value,
                    plan.acceptance_status.value,
                    plan.plan_revision,
                    plan.resulting_campaign_revision,
                    plan.payload_sha256,
                ),
            )
            updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? "
                "AND revision = ? AND lifecycle = 'active' AND writer = 'builder'",
                (resulting_revision, campaign_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise StaleRevision("Campaign Plan revision fence rejected admission")
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id) "
                "VALUES (?, ?, NULL, NULL, ?)",
                (campaign_id, resulting_revision, plan_id),
            )
            connection.commit()
            return plan
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _campaign_operation_result(
        row: sqlite3.Row,
    ) -> (
        CampaignConfirmation
        | CampaignConstructionBegun
        | CampaignConstructionCompleted
        | CampaignLifecycleResult
        | CampaignPurgeResult
        | CampaignContinuityReceipt
    ):
        result = _load_payload(str(row["result_json"]))
        kind = str(row["operation_kind"])
        campaign_id = str(row["campaign_id"])
        resulting_revision = int(row["resulting_revision"])
        if (
            result.get("campaign_id") != campaign_id
            or result.get("resulting_revision") != resulting_revision
        ):
            raise SchemaIdentityError("Campaign operation result authority is invalid")
        if kind == "confirm_new_campaign" and set(result) == {
            "campaign_id",
            "resulting_revision",
        }:
            return CampaignConfirmation(campaign_id, resulting_revision)
        if kind == "purge_campaign" and set(result) == {
            "campaign_id",
            "resulting_revision",
        }:
            return CampaignPurgeResult(campaign_id, resulting_revision)
        if kind == "commit_campaign_continuity" and set(result) == {
            "campaign_id",
            "operation_id",
            "record_id",
            "kind",
            "resulting_revision",
        }:
            return CampaignContinuityReceipt(
                campaign_id=campaign_id,
                operation_id=str(result["operation_id"]),
                record_id=str(result["record_id"]),
                kind=str(result["kind"]),
                resulting_revision=resulting_revision,
            )
        if kind in {
            "rename_campaign",
            "archive_campaign",
            "restore_campaign",
            "trash_campaign",
        }:
            if set(result) != {
                "campaign_id",
                "lifecycle",
                "writer",
                "resulting_revision",
                "session_id",
            }:
                raise SchemaIdentityError("Campaign lifecycle result authority is invalid")
            try:
                lifecycle = LifecycleState(str(result["lifecycle"]))
                writer = WriterRole(str(result["writer"]))
            except (KeyError, ValueError) as error:
                raise SchemaIdentityError(
                    "Campaign lifecycle result authority is invalid"
                ) from error
            session_value = result["session_id"]
            if session_value is not None and not isinstance(session_value, str):
                raise SchemaIdentityError("Campaign lifecycle session authority is invalid")
            return CampaignLifecycleResult(
                campaign_id,
                lifecycle,
                writer,
                resulting_revision,
                session_value,
            )
        lane = result.get("lane")
        if not isinstance(lane, str) or lane not in {"quick_start", "customize"}:
            raise SchemaIdentityError("Campaign operation lane authority is invalid")
        if kind == "begin_campaign_construction" and set(result) == {
            "campaign_id",
            "lane",
            "resulting_revision",
        }:
            return CampaignConstructionBegun(campaign_id, lane, resulting_revision)
        representation = result.get("canonical_representation_sha256")
        if (
            kind == "complete_campaign_construction"
            and set(result)
            == {
                "campaign_id",
                "lane",
                "canonical_representation_sha256",
                "resulting_revision",
            }
            and isinstance(representation, str)
        ):
            validate_authority_identity(
                representation, "Canonical construction SHA-256", sha256=True
            )
            return CampaignConstructionCompleted(
                campaign_id,
                lane,
                representation,
                resulting_revision,
            )
        raise SchemaIdentityError("Campaign operation result kind is invalid")

    def _campaign_operation_hit(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        operation_kind: str,
        payload_sha256: str,
    ) -> (
        CampaignConfirmation
        | CampaignConstructionBegun
        | CampaignConstructionCompleted
        | CampaignLifecycleResult
        | CampaignPurgeResult
        | CampaignContinuityReceipt
        | None
    ):
        row = connection.execute(
            "SELECT * FROM campaign_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["operation_kind"]) != operation_kind
            or str(row["semantic_payload_sha256"]) != payload_sha256
        ):
            raise IdempotencyConflict("Campaign operation identity payload does not match")
        return self._campaign_operation_result(row)

    def campaign_operation_hit(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        payload_sha256: str,
    ) -> (
        CampaignConfirmation
        | CampaignConstructionBegun
        | CampaignConstructionCompleted
        | CampaignLifecycleResult
        | CampaignPurgeResult
        | CampaignContinuityReceipt
        | None
    ):
        """Application prelookup before generated identity or mutable-state evaluation."""

        connection = self._connect_read_only()
        try:
            version = self._verify_exact_schema_identity(connection)
            if version in (_SCHEMA_VERSION, _SESSION_SCHEMA_VERSION):
                return None
            return self._campaign_operation_hit(
                connection,
                operation_id=operation_id,
                operation_kind=operation_kind,
                payload_sha256=payload_sha256,
            )
        finally:
            connection.close()

    @staticmethod
    def _insert_campaign_operation(
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        operation_kind: str,
        campaign_id: str,
        semantic_payload: SafePayload,
        payload_sha256: str,
        result: SafePayload,
        basis_turn_id: str | None,
        basis_lock_id: str | None,
        basis_revision: int,
        resulting_revision: int,
    ) -> None:
        connection.execute(
            "INSERT INTO campaign_operations "
            "(operation_id, operation_kind, campaign_id, semantic_payload_json, "
            "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, "
            "basis_revision, resulting_revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                operation_kind,
                campaign_id,
                _dump(semantic_payload),
                payload_sha256,
                _dump(result),
                basis_turn_id,
                basis_lock_id,
                basis_revision,
                resulting_revision,
            ),
        )

    def commit_campaign_continuity(
        self, command: CampaignContinuityCommand
    ) -> CampaignContinuityReceipt:
        """Commit one idempotent, revision-fenced Campaign State continuity change."""

        return self.commit_campaign_continuity_batch((command,))[0]

    def commit_campaign_continuity_batch(
        self,
        commands: Sequence[CampaignContinuityCommand],
        *,
        _connection: sqlite3.Connection | None = None,
    ) -> tuple[CampaignContinuityReceipt, ...]:
        """Commit a complete Campaign File update in one SQLite transaction."""

        admitted = tuple(commands)
        if not admitted:
            raise CommandRejected("Campaign continuity batch is empty")
        first = admitted[0]
        if len(admitted) > 64:
            raise CommandRejected("Campaign continuity batch is too large")
        operation_ids: set[str] = set()
        prepared: list[tuple[CampaignContinuityCommand, SafePayload, str]] = []
        for index, command in enumerate(admitted):
            for value, label in (
                (command.campaign_id, "Campaign continuity campaign"),
                (command.operation_id, "Campaign continuity operation"),
                (command.session_id, "Campaign continuity session"),
                (command.turn_id, "Campaign continuity turn"),
                (command.record_id, "Campaign continuity record"),
            ):
                validate_authority_identity(value, label)
            for message_id in command.basis_message_ids:
                validate_authority_identity(message_id, "Campaign continuity basis message")
            if command.kind not in {"scene", "fact", "entity", "reveal", "plan_progress"}:
                raise CommandRejected("Campaign continuity kind is unsupported")
            if command.status not in {"active", "retired"}:
                raise CommandRejected("Campaign continuity status is unsupported")
            if command.expected_revision < 0 or not canonical_payload_is_safe(command.payload):
                raise CommandRejected("Campaign continuity payload is invalid")
            if (
                command.campaign_id != first.campaign_id
                or command.session_id != first.session_id
                or command.turn_id != first.turn_id
                or command.expected_revision != first.expected_revision + index
            ):
                raise CommandRejected("Campaign continuity batch basis is inconsistent")
            if command.operation_id in operation_ids:
                raise CommandRejected("Campaign continuity batch operation is duplicated")
            operation_ids.add(command.operation_id)
            semantic_payload = owned_payload(
                {
                    "operation_id": command.operation_id,
                    "operation_kind": "commit_campaign_continuity",
                    "campaign_id": command.campaign_id,
                    "session_id": command.session_id,
                    "turn_id": command.turn_id,
                    "kind": command.kind,
                    "record_id": command.record_id,
                    "status": command.status,
                    "payload": command.payload,
                    "basis_message_ids": list(command.basis_message_ids),
                }
            )
            prepared.append(
                (
                    command,
                    semantic_payload,
                    hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest(),
                )
            )

        connection = _connection if _connection is not None else self._connect()
        try:
            if _connection is None:
                connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            hits = tuple(
                self._campaign_operation_hit(
                    connection,
                    operation_id=command.operation_id,
                    operation_kind="commit_campaign_continuity",
                    payload_sha256=payload_sha256,
                )
                for command, _semantic_payload, payload_sha256 in prepared
            )
            if any(hit is not None for hit in hits):
                if not all(isinstance(hit, CampaignContinuityReceipt) for hit in hits):
                    raise SchemaIdentityError("Campaign continuity batch replay is partial")
                if _connection is None:
                    connection.rollback()
                return cast(tuple[CampaignContinuityReceipt, ...], hits)
            campaign = connection.execute(
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns "
                "WHERE campaign_id = ?",
                (first.campaign_id,),
            ).fetchone()
            session = connection.execute(
                "SELECT campaign_id, role, status FROM agent_sessions WHERE session_id = ?",
                (first.session_id,),
            ).fetchone()
            turn = connection.execute(
                "SELECT 1 FROM turns WHERE campaign_id = ? AND turn_id = ?",
                (first.campaign_id, first.turn_id),
            ).fetchone()
            if campaign is None:
                raise CommandRejected("Campaign continuity campaign is unavailable")
            if (
                str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value
                or str(campaign["writer"]) != WriterRole.DM.value
            ):
                raise WrongWriter("Campaign State can be changed only by the active DM")
            if int(campaign["revision"]) != first.expected_revision:
                raise StaleRevision("Campaign continuity expected revision is stale")
            if (
                session is None
                or str(session["campaign_id"]) != first.campaign_id
                or str(session["role"]) != WriterRole.DM.value
                or str(session["status"]) != SessionStatus.ACTIVE.value
                or turn is None
            ):
                raise CommandRejected("Campaign continuity basis is unavailable")
            lock_id = campaign["current_lock_id"]
            if lock_id is None:
                raise CommandRejected("Campaign continuity requires an admitted package lock")
            receipts: list[CampaignContinuityReceipt] = []
            for command, semantic_payload, payload_sha256 in prepared:
                resulting_revision = command.expected_revision + 1
                result_payload = owned_payload(
                    {
                        "campaign_id": command.campaign_id,
                        "operation_id": command.operation_id,
                        "record_id": command.record_id,
                        "kind": command.kind,
                        "resulting_revision": resulting_revision,
                    }
                )
                self._insert_campaign_operation(
                    connection,
                    operation_id=command.operation_id,
                    operation_kind="commit_campaign_continuity",
                    campaign_id=command.campaign_id,
                    semantic_payload=semantic_payload,
                    payload_sha256=payload_sha256,
                    result=result_payload,
                    basis_turn_id=command.turn_id,
                    basis_lock_id=str(lock_id),
                    basis_revision=command.expected_revision,
                    resulting_revision=resulting_revision,
                )
                sqlite_continuity.append_record(
                    connection,
                    campaign_id=command.campaign_id,
                    entry_id=command.operation_id,
                    record_id=command.record_id,
                    kind=command.kind,
                    status=command.status,
                    payload=command.payload,
                    basis_message_ids=command.basis_message_ids,
                    operation_id=command.operation_id,
                    admitted_revision=resulting_revision,
                )
                connection.execute(
                    "INSERT INTO campaign_revisions "
                    "(campaign_id, revision, continuity_operation_id) VALUES (?, ?, ?)",
                    (command.campaign_id, resulting_revision, command.operation_id),
                )
                receipts.append(
                    CampaignContinuityReceipt(
                        campaign_id=command.campaign_id,
                        operation_id=command.operation_id,
                        record_id=command.record_id,
                        kind=command.kind,
                        resulting_revision=resulting_revision,
                    )
                )
            final_revision = first.expected_revision + len(admitted)
            updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ?",
                (final_revision, first.campaign_id, first.expected_revision),
            )
            if updated.rowcount != 1:
                raise StaleRevision("Campaign continuity revision fence rejected admission")
            if _connection is None:
                self._before_commit()
                connection.commit()
            return tuple(receipts)
        except BaseException:
            if _connection is None and connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if _connection is None:
                connection.close()

    def preflight_campaign_purge(
        self,
        *,
        operation_id: str,
        semantic_payload: SafePayload,
        campaign_id: str,
        expected_revision: int,
        confirmation: str,
    ) -> None:
        """Validate the exact purge basis without holding a write transaction."""

        connection = self._connect()
        try:
            campaign = connection.execute(
                "SELECT lifecycle, writer, revision FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise CommandRejected("Campaign is missing or already purged")
            if int(campaign["revision"]) != expected_revision:
                raise StaleRevision("Campaign purge expected revision is stale")
            if (
                str(campaign["lifecycle"]) != LifecycleState.TOMBSTONED.value
                or str(campaign["writer"]) != WriterRole.NONE.value
            ):
                raise CommandRejected("Campaign must be tombstoned before purge")
            if confirmation != (f"permanently purge {campaign_id}") or semantic_payload != {
                "operation_id": operation_id,
                "operation_kind": "purge_campaign",
                "campaign_id": campaign_id,
                "confirmation": confirmation,
            }:
                raise CommandRejected("Campaign purge confirmation is not exact")
            if (
                connection.execute(
                    "SELECT 1 FROM participants AS participant "
                    "JOIN participant_adaptations AS adaptation "
                    "ON adaptation.operation_id = participant.adaptation_id "
                    "WHERE participant.campaign_id != ? "
                    "AND adaptation.source_campaign_id = ? LIMIT 1",
                    (campaign_id, campaign_id),
                ).fetchone()
                is not None
            ):
                raise CommandRejected(
                    "Campaign purge is blocked by surviving adaptation provenance",
                    code="campaign_purge_adaptation_referenced",
                )
        finally:
            connection.close()

    def commit_campaign_purge(
        self,
        *,
        operation_id: str,
        semantic_payload: SafePayload,
        payload_sha256: str,
        campaign_id: str,
        expected_revision: int,
        confirmation: str,
    ) -> CampaignPurgeResult:
        """Delete one tombstoned campaign and retain one content-free receipt atomically."""

        connection = self._connect()
        commit_started = False
        result: CampaignPurgeResult | None = None
        primary_error: BaseException | None = None
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_purge_schema(connection)
                replay = self._campaign_operation_hit(
                    connection,
                    operation_id=operation_id,
                    operation_kind="purge_campaign",
                    payload_sha256=payload_sha256,
                )
                if replay is not None:
                    if not isinstance(replay, CampaignPurgeResult):
                        raise IdempotencyConflict("Campaign operation result kind does not match")
                    connection.rollback()
                    result = replay
                else:
                    campaign = connection.execute(
                        "SELECT lifecycle, writer, revision FROM campaigns WHERE campaign_id = ?",
                        (campaign_id,),
                    ).fetchone()
                    if campaign is None:
                        raise CommandRejected("Campaign is missing or already purged")
                    if int(campaign["revision"]) != expected_revision:
                        raise StaleRevision("Campaign purge expected revision is stale")
                    if (
                        str(campaign["lifecycle"]) != LifecycleState.TOMBSTONED.value
                        or str(campaign["writer"]) != WriterRole.NONE.value
                    ):
                        raise CommandRejected("Campaign must be tombstoned before purge")
                    if confirmation != (f"permanently purge {campaign_id}") or semantic_payload != {
                        "operation_id": operation_id,
                        "operation_kind": "purge_campaign",
                        "campaign_id": campaign_id,
                        "confirmation": confirmation,
                    }:
                        raise CommandRejected("Campaign purge confirmation is not exact")
                    if (
                        connection.execute(
                            "SELECT 1 FROM participants AS participant "
                            "JOIN participant_adaptations AS adaptation "
                            "ON adaptation.operation_id = participant.adaptation_id "
                            "WHERE participant.campaign_id != ? "
                            "AND adaptation.source_campaign_id = ? LIMIT 1",
                            (campaign_id, campaign_id),
                        ).fetchone()
                        is not None
                    ):
                        raise CommandRejected(
                            "Campaign purge is blocked by surviving adaptation provenance",
                            code="campaign_purge_adaptation_referenced",
                        )

                    connection.execute("PRAGMA defer_foreign_keys = ON")
                    for table in (
                        "campaign_continuity_records",
                        "narration_interruptions",
                        "canonical_events",
                        "mutations",
                        "consequences",
                        "idempotency_records",
                        "campaign_revisions",
                        "campaign_lifecycle_transitions",
                        "campaign_constructions",
                        "actions",
                    ):
                        connection.execute(
                            f'DELETE FROM "{table}" WHERE campaign_id = ?',
                            (campaign_id,),
                        )
                    connection.execute(
                        "DELETE FROM campaign_operations WHERE campaign_id = ? "
                        "AND operation_kind != 'purge_campaign'",
                        (campaign_id,),
                    )
                    connection.execute(
                        "DELETE FROM campaign_recovery_receipts WHERE campaign_id = ?",
                        (campaign_id,),
                    )
                    # Delete package-owned state explicitly before the campaign row.
                    # Cascading the campaign first may visit namespace owners before
                    # their state rows, and the ownership trigger correctly rejects
                    # that unsafe order.
                    connection.execute(
                        "DELETE FROM campaign_state WHERE campaign_id = ?",
                        (campaign_id,),
                    )
                    connection.execute(
                        "DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,)
                    )
                    connection.execute(
                        "DELETE FROM participant_adaptations WHERE source_campaign_id = ?",
                        (campaign_id,),
                    )

                    resulting_revision = expected_revision + 1
                    result_payload = owned_payload(
                        {
                            "campaign_id": campaign_id,
                            "resulting_revision": resulting_revision,
                        }
                    )
                    self._insert_campaign_operation(
                        connection,
                        operation_id=operation_id,
                        operation_kind="purge_campaign",
                        campaign_id=campaign_id,
                        semantic_payload=semantic_payload,
                        payload_sha256=payload_sha256,
                        result=result_payload,
                        basis_turn_id=None,
                        basis_lock_id=None,
                        basis_revision=expected_revision,
                        resulting_revision=resulting_revision,
                    )
                    sqlite_packages.reclaim(connection)
                    self._verify_campaign_purge_cleanup(connection, campaign_id)
                    self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
                    self._verify_stored_authority(connection)
                    sqlite_packages.verify_package_authority(connection)
                    sqlite_campaign.verify_f5_campaign_authority(connection)
                    result = CampaignPurgeResult(campaign_id, resulting_revision)
                    self._before_lifecycle_commit()
                    commit_started = True
                    connection.commit()
                    self._after_lifecycle_commit()
            except BaseException as error:
                primary_error = error
                if connection.in_transaction:
                    connection.rollback()
        finally:
            try:
                connection.close()
            except Exception as error:
                if primary_error is None:
                    primary_error = error

        if primary_error is None:
            if result is not None:
                return result
            raise RuntimeError("Campaign purge transaction did not produce an outcome")
        if not commit_started:
            raise primary_error

        resolved_result: CampaignPurgeResult | None = None
        commit_outcome_uncertain = False
        try:
            resolved = self.campaign_operation_hit(
                operation_id=operation_id,
                operation_kind="purge_campaign",
                payload_sha256=payload_sha256,
            )
            if isinstance(resolved, CampaignPurgeResult):
                resolved_result = resolved
            else:
                commit_outcome_uncertain = True
        except Exception:
            commit_outcome_uncertain = True
        if resolved_result is not None:
            return resolved_result
        if commit_outcome_uncertain:
            raise CommandRejected(
                "Campaign purge commit outcome is uncertain",
                code="campaign_purge_commit_outcome_uncertain",
            ) from None
        raise RuntimeError("Campaign purge commit resolution did not produce an outcome")

    @staticmethod
    def _verify_campaign_purge_cleanup(connection: sqlite3.Connection, campaign_id: str) -> None:
        campaign_tables = (
            "actions",
            "agent_sessions",
            "builder_dm_handoffs",
            "campaign_constructions",
            "campaign_continuity_records",
            "campaign_lifecycle_state",
            "campaign_lifecycle_transitions",
            "campaign_locks",
            "campaign_package_memberships",
            "campaign_plans",
            "campaign_recovery_receipts",
            "campaign_revisions",
            "campaign_state",
            "campaigns",
            "canonical_events",
            "consequences",
            "idempotency_records",
            "mutations",
            "narration_interruptions",
            "package_activations",
            "participant_quarantines",
            "participants",
            "turns",
        )
        if any(
            connection.execute(
                f'SELECT 1 FROM "{table}" WHERE campaign_id = ? LIMIT 1', (campaign_id,)
            ).fetchone()
            is not None
            for table in campaign_tables
        ):
            raise SchemaIdentityError("Campaign purge left campaign-local authority")
        if (
            connection.execute(
                "SELECT 1 FROM campaign_operations WHERE campaign_id = ? "
                "AND (operation_kind != 'purge_campaign' OR basis_turn_id IS NOT NULL "
                "OR basis_lock_id IS NOT NULL) LIMIT 1",
                (campaign_id,),
            ).fetchone()
            is not None
            or connection.execute(
                "SELECT 1 FROM participant_adaptations WHERE source_campaign_id = ? LIMIT 1",
                (campaign_id,),
            ).fetchone()
            is not None
        ):
            raise SchemaIdentityError("Campaign purge retained non-receipt authority")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaIdentityError("Campaign purge left invalid foreign keys")
        if tuple(
            str(row[0]).casefold() for row in connection.execute("PRAGMA integrity_check")
        ) != ("ok",):
            raise SchemaIdentityError("Campaign purge failed integrity verification")

    def commit_lifecycle_operation(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        semantic_payload: SafePayload,
        payload_sha256: str,
        campaign_id: str,
        expected_revision: int,
        display_name: str | None = None,
        designated_session_id: str | None = None,
    ) -> CampaignLifecycleResult:
        """Commit one reversible lifecycle transition and receipt in one transaction."""

        connection = self._connect()
        commit_started = False
        old_authority: (
            tuple[
                tuple[object, ...],
                tuple[object, ...],
                tuple[tuple[object, ...], ...],
            ]
            | None
        ) = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            replay = self._campaign_operation_hit(
                connection,
                operation_id=operation_id,
                operation_kind=operation_kind,
                payload_sha256=payload_sha256,
            )
            if replay is not None:
                if not isinstance(replay, CampaignLifecycleResult):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                connection.rollback()
                return replay
            campaign = connection.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            state = connection.execute(
                "SELECT * FROM campaign_lifecycle_state WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if campaign is None or state is None:
                raise CommandRejected("Campaign lifecycle authority is unavailable")
            lifecycle = LifecycleState(str(campaign["lifecycle"]))
            writer = WriterRole(str(campaign["writer"]))
            if int(campaign["revision"]) != expected_revision:
                raise StaleRevision("Campaign lifecycle expected revision is stale")
            old_authority = (
                tuple(
                    campaign[column]
                    for column in (
                        "display_name",
                        "lifecycle",
                        "writer",
                        "revision",
                        "current_lock_id",
                    )
                ),
                tuple(
                    state[column]
                    for column in (
                        "retained_active_writer",
                        "designated_session_id",
                        "tombstone_prior_lifecycle",
                        "tombstone_prior_writer",
                        "tombstone_prior_session_id",
                    )
                ),
                tuple(
                    tuple(row)
                    for row in connection.execute(
                        "SELECT session_id, status, payload_sha256 FROM agent_sessions "
                        "WHERE campaign_id = ? ORDER BY session_id",
                        (campaign_id,),
                    )
                ),
            )
            session_id: str | None = None
            to_lifecycle = lifecycle
            to_writer = writer

            def stopped_session(required_id: str | None) -> str | None:
                if (
                    connection.execute(
                        "SELECT 1 FROM agent_sessions WHERE campaign_id = ? "
                        "AND parent_session_id IS NOT NULL "
                        "AND status IN ('active', 'review_required') LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    is not None
                ):
                    raise CommandRejected("Campaign lifecycle has active child work")
                rows = tuple(
                    connection.execute(
                        "SELECT session_id, status FROM agent_sessions WHERE campaign_id = ? "
                        "AND parent_session_id IS NULL AND role = ? ORDER BY session_id",
                        (campaign_id, writer.value),
                    )
                )
                if len(rows) > 1:
                    raise CommandRejected("Campaign lifecycle session authority is ambiguous")
                if not rows:
                    if required_id is not None:
                        raise CommandRejected("Designated campaign session is not exact")
                    return None
                if required_id is None or str(rows[0]["session_id"]) != required_id:
                    raise CommandRejected("Designated campaign session is not exact")
                for row in connection.execute(
                    "SELECT session_id FROM agent_sessions WHERE campaign_id = ? "
                    "AND parent_session_id IS NULL "
                    "AND status IN ('active', 'review_required') ORDER BY session_id",
                    (campaign_id,),
                ):
                    sqlite_session.update_session_status(
                        connection,
                        str(row["session_id"]),
                        SessionStatus.STOPPED,
                    )
                return str(rows[0]["session_id"])

            if operation_kind == "rename_campaign":
                if display_name is None:
                    raise CommandRejected("Campaign display name is unavailable")
            elif operation_kind in {"archive_campaign", "trash_campaign"}:
                allowed = (
                    (LifecycleState.ACTIVE,)
                    if operation_kind == "archive_campaign"
                    else (LifecycleState.ACTIVE, LifecycleState.ARCHIVED)
                )
                if lifecycle not in allowed:
                    raise CommandRejected("Campaign lifecycle transition is not admissible")
                if lifecycle is LifecycleState.ACTIVE:
                    if (
                        operation_kind == "archive_campaign"
                        and connection.execute(
                            "SELECT 1 FROM actions WHERE campaign_id = ? "
                            "AND status NOT IN ('committed', 'failed') LIMIT 1",
                            (campaign_id,),
                        ).fetchone()
                        is not None
                    ):
                        raise CommandRejected(
                            "Campaign lifecycle is blocked by incomplete action work"
                        )
                    # Open turns waiting for the player survive archive/restore unchanged.
                    # The owner reserves this transition against running campaign work.
                    session_id = stopped_session(designated_session_id)
                else:
                    session_id = (
                        None
                        if state["designated_session_id"] is None
                        else str(state["designated_session_id"])
                    )
                if operation_kind == "archive_campaign":
                    to_lifecycle = LifecycleState.ARCHIVED
                    to_writer = WriterRole.NONE
                    connection.execute(
                        "UPDATE campaign_lifecycle_state SET retained_active_writer = ?, "
                        "designated_session_id = ? WHERE campaign_id = ?",
                        (writer.value, session_id, campaign_id),
                    )
                else:
                    to_lifecycle = LifecycleState.TOMBSTONED
                    to_writer = WriterRole.NONE
                    connection.execute(
                        "UPDATE campaign_lifecycle_state SET tombstone_prior_lifecycle = ?, "
                        "tombstone_prior_writer = ?, tombstone_prior_session_id = ? "
                        "WHERE campaign_id = ?",
                        (lifecycle.value, writer.value, session_id, campaign_id),
                    )
            elif operation_kind == "restore_campaign":
                if lifecycle is LifecycleState.ARCHIVED:
                    retained = state["retained_active_writer"]
                    if retained is None:
                        raise CommandRejected(
                            "Archived campaign has no restorable writer authority"
                        )
                    to_lifecycle = LifecycleState.ACTIVE
                    to_writer = WriterRole(str(retained))
                    session_id = (
                        None
                        if state["designated_session_id"] is None
                        else str(state["designated_session_id"])
                    )
                    if designated_session_id != session_id:
                        raise CommandRejected(
                            "Restore designated session does not match archived campaign"
                        )
                elif lifecycle is LifecycleState.TOMBSTONED:
                    prior = state["tombstone_prior_lifecycle"]
                    prior_writer = state["tombstone_prior_writer"]
                    if prior is None or prior_writer is None:
                        raise CommandRejected("Tombstoned campaign has no restorable prior state")
                    to_lifecycle = LifecycleState(str(prior))
                    to_writer = WriterRole(str(prior_writer))
                    session_id = (
                        None
                        if state["tombstone_prior_session_id"] is None
                        else str(state["tombstone_prior_session_id"])
                    )
                    connection.execute(
                        "UPDATE campaign_lifecycle_state SET tombstone_prior_lifecycle = NULL, "
                        "tombstone_prior_writer = NULL, tombstone_prior_session_id = NULL "
                        "WHERE campaign_id = ?",
                        (campaign_id,),
                    )
                else:
                    raise CommandRejected("active campaign cannot be restored")
                if to_lifecycle is LifecycleState.ACTIVE and session_id is not None:
                    restored_session = sqlite_session.restore_stopped_session(
                        connection, session_id
                    )
                    if (
                        restored_session.campaign_id != campaign_id
                        or restored_session.parent_session_id is not None
                        or restored_session.role is not to_writer
                    ):
                        raise CommandRejected("Designated campaign session cannot be restored")
            else:
                raise CommandRejected("Campaign lifecycle operation kind is invalid")

            resulting_revision = expected_revision + 1
            updated = connection.execute(
                "UPDATE campaigns SET display_name = ?, lifecycle = ?, writer = ?, revision = ? "
                "WHERE campaign_id = ? AND revision = ? AND lifecycle = ? AND writer = ?",
                (
                    str(campaign["display_name"]) if display_name is None else display_name,
                    to_lifecycle.value,
                    to_writer.value,
                    resulting_revision,
                    campaign_id,
                    expected_revision,
                    lifecycle.value,
                    writer.value,
                ),
            )
            if updated.rowcount != 1:
                raise StaleRevision("Campaign lifecycle revision fence rejected transition")
            turn = connection.execute(
                "SELECT turn_id FROM turns WHERE campaign_id = ? "
                "ORDER BY created_revision DESC, turn_id DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            unbound = (
                turn is None and campaign["current_lock_id"] is None
                and str(campaign["lock_hash"]) == _UNBOUND_CAMPAIGN_LOCK_HASH
                and sqlite_draft_history.has_only_draft_lifecycle_history(
                    connection, campaign_id, expected_revision
                )
            )
            if not unbound and (turn is None or campaign["current_lock_id"] is None):
                raise CommandRejected("Campaign lifecycle basis is unavailable")
            result = owned_payload(
                {
                    "campaign_id": campaign_id,
                    "lifecycle": to_lifecycle.value,
                    "writer": to_writer.value,
                    "resulting_revision": resulting_revision,
                    "session_id": session_id,
                }
            )
            self._insert_campaign_operation(
                connection,
                operation_id=operation_id,
                operation_kind=operation_kind,
                campaign_id=campaign_id,
                semantic_payload=semantic_payload,
                payload_sha256=payload_sha256,
                result=result,
                basis_turn_id=None if unbound else str(turn["turn_id"]),
                basis_lock_id=None if unbound else str(campaign["current_lock_id"]),
                basis_revision=expected_revision,
                resulting_revision=resulting_revision,
            )
            connection.execute(
                "INSERT INTO campaign_lifecycle_transitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    campaign_id,
                    operation_kind,
                    expected_revision,
                    resulting_revision,
                    lifecycle.value,
                    to_lifecycle.value,
                    writer.value,
                    to_writer.value,
                    session_id,
                ),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id, "
                "construction_operation_id, lifecycle_operation_id) "
                "VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, ?)",
                (campaign_id, resulting_revision, operation_id),
            )
            lifecycle_result = CampaignLifecycleResult(
                campaign_id, to_lifecycle, to_writer, resulting_revision, session_id
            )
            self._before_lifecycle_commit()
            commit_started = True
            connection.commit()
            self._after_lifecycle_commit()
            return lifecycle_result
        except BaseException as error:
            if connection.in_transaction:
                connection.rollback()
            if commit_started:
                resolved = self.campaign_operation_hit(
                    operation_id=operation_id,
                    operation_kind=operation_kind,
                    payload_sha256=payload_sha256,
                )
                if resolved is not None:
                    if not isinstance(resolved, CampaignLifecycleResult):
                        raise IdempotencyConflict(
                            "Campaign operation result kind does not match"
                        ) from error
                    return resolved
                if old_authority is not None and self._lifecycle_old_authority_is_intact(
                    campaign_id=campaign_id,
                    operation_id=operation_id,
                    old_authority=old_authority,
                ):
                    raise CommandRejected(
                        "Campaign lifecycle commit was confirmed rolled back"
                    ) from error
                raise CommandRejected("Campaign lifecycle commit outcome is uncertain") from error
            raise
        finally:
            connection.close()

    def _lifecycle_old_authority_is_intact(
        self,
        *,
        campaign_id: str,
        operation_id: str,
        old_authority: tuple[
            tuple[object, ...],
            tuple[object, ...],
            tuple[tuple[object, ...], ...],
        ],
    ) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            campaign = connection.execute(
                "SELECT display_name, lifecycle, writer, revision, current_lock_id "
                "FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            state = connection.execute(
                "SELECT retained_active_writer, designated_session_id, "
                "tombstone_prior_lifecycle, tombstone_prior_writer, "
                "tombstone_prior_session_id FROM campaign_lifecycle_state "
                "WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            sessions = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT session_id, status, payload_sha256 FROM agent_sessions "
                    "WHERE campaign_id = ? ORDER BY session_id",
                    (campaign_id,),
                )
            )
            operation = connection.execute(
                "SELECT 1 FROM campaign_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            transition = connection.execute(
                "SELECT 1 FROM campaign_lifecycle_transitions WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            revision = connection.execute(
                "SELECT 1 FROM campaign_revisions WHERE campaign_id = ? "
                "AND lifecycle_operation_id = ?",
                (campaign_id, operation_id),
            ).fetchone()
            return (
                campaign is not None
                and state is not None
                and tuple(campaign) == old_authority[0]
                and tuple(state) == old_authority[1]
                and sessions == old_authority[2]
                and operation is None
                and transition is None
                and revision is None
            )
        except (KeyError, TypeError, ValueError, sqlite3.Error):
            return False
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def _before_lifecycle_commit(self) -> None:
        """Private deterministic fault seam for reversible lifecycle rollback."""

    def _after_lifecycle_commit(self) -> None:
        """Private deterministic lost-acknowledgement seam for lifecycle operations."""

    def confirm_new_campaign(
        self,
        *,
        operation_id: str,
        semantic_payload: SafePayload,
        payload_sha256: str,
        campaign: CampaignRecord,
        package_lock: PackageLock,
        turn_id: str,
        builder_session: SessionRecord | None = None,
        participants: Iterable[Participant] = (),
        initial_state: Iterable[StateValue] = (),
    ) -> CampaignConfirmation:
        """Atomically create campaign, initial basis, construction authority, and receipt."""

        participants = tuple(participants)
        initial_state = tuple(initial_state)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            replay = self._campaign_operation_hit(
                connection,
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                payload_sha256=payload_sha256,
            )
            if replay is not None:
                if not isinstance(replay, CampaignConfirmation):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                connection.rollback()
                return replay
            sqlite_packages.verify_published_lock(connection, package_lock)
            lock_hash = _lock_representation(package_lock)
            connection.execute(
                "INSERT INTO campaigns "
                "(campaign_id, display_name, lifecycle, writer, revision, lock_hash, "
                "current_lock_id) VALUES (?, ?, 'active', 'builder', 0, ?, NULL)",
                (campaign.campaign_id, campaign.display_name, lock_hash),
            )
            connection.execute(
                "INSERT INTO campaign_lifecycle_state "
                "(campaign_id, retained_active_writer) VALUES (?, 'builder')",
                (campaign.campaign_id,),
            )
            if builder_session is not None:
                if (
                    builder_session.campaign_id != campaign.campaign_id
                    or builder_session.role is not WriterRole.BUILDER
                    or builder_session.status is not SessionStatus.ACTIVE
                ):
                    raise CommandRejected(
                        "Confirmed Builder session does not match campaign authority"
                    )
                sqlite_session.insert_session(connection, builder_session)
            lock_record = sqlite_packages.insert_campaign_lock(
                connection,
                campaign_id=campaign.campaign_id,
                package_lock=package_lock,
                revision=0,
                predecessor_lock_id=None,
            )
            connection.execute(
                "UPDATE campaigns SET current_lock_id = ? WHERE campaign_id = ?",
                (lock_record.lock_id, campaign.campaign_id),
            )
            for entry in package_lock.entries:
                connection.execute(
                    "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        campaign.campaign_id,
                        entry.package_id,
                        entry.version,
                        entry.sha256,
                        entry.role.value,
                        entry.load_order,
                    ),
                )
            if any(
                participant.validation_status is not ParticipantValidationStatus.VALIDATED
                for participant in participants
            ):
                raise CommandRejected(
                    "Confirmed campaign participants require exact package and schema proof",
                    code="participant_exact_proof_required",
                )
            if any(
                not self._participant_adaptation_is_admitted(connection, participant)
                for participant in participants
            ):
                raise CommandRejected(
                    "Cross-schema participant reuse requires a durable adaptation operation",
                    code="participant_adaptation_required",
                )
            for participant in participants:
                connection.execute(
                    "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        campaign.campaign_id,
                        participant.participant_id,
                        participant.kind,
                        participant.package_id,
                        participant.package_version,
                        _dump(dict(participant.data)),
                        participant.package_sha256,
                        participant.schema_id,
                        participant.schema_sha256,
                        participant.validation_status.value,
                        participant.adaptation_id,
                    ),
                )
            initial_owners = {
                state_value.namespace: sqlite_state_ownership.initial_owner(
                    package_lock,
                    state_value.namespace,
                )
                for state_value in initial_state
            }
            for namespace, owner_package_id in sorted(initial_owners.items()):
                connection.execute(
                    "INSERT INTO campaign_state_namespace_owners "
                    "(campaign_id, namespace, owner_package_id) VALUES (?, ?, ?)",
                    (campaign.campaign_id, namespace, owner_package_id),
                )
            for state_value in initial_state:
                connection.execute(
                    "INSERT INTO campaign_state VALUES (?, ?, ?, ?)",
                    (
                        campaign.campaign_id,
                        state_value.namespace,
                        state_value.key,
                        _dump(state_value.value),
                    ),
                )
            connection.execute(
                "INSERT INTO turns VALUES (?, ?, 'open', 0)",
                (campaign.campaign_id, turn_id),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id, "
                "construction_operation_id) VALUES (?, 0, NULL, NULL, NULL, NULL, NULL)",
                (campaign.campaign_id,),
            )
            result = owned_payload({"campaign_id": campaign.campaign_id, "resulting_revision": 0})
            self._insert_campaign_operation(
                connection,
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                campaign_id=campaign.campaign_id,
                semantic_payload=semantic_payload,
                payload_sha256=payload_sha256,
                result=result,
                basis_turn_id=turn_id,
                basis_lock_id=lock_record.lock_id,
                basis_revision=0,
                resulting_revision=0,
            )
            connection.execute(
                "INSERT INTO campaign_constructions "
                "(campaign_id, confirmation_operation_id, authority_turn_id, "
                "authority_lock_id, confirmation_revision) VALUES (?, ?, ?, ?, 0)",
                (campaign.campaign_id, operation_id, turn_id, lock_record.lock_id),
            )
            self._before_construction_commit()
            connection.commit()
            return CampaignConfirmation(campaign.campaign_id, 0)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def create_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        campaign: CampaignRecord,
        builder_session: SessionRecord,
    ) -> GuidedCampaignDraft:
        """Create one unbound Draft and its deterministic first interview question."""

        suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        intro_message_id = f"guided-intro-{suffix}"
        state_message_id = f"guided-state-{suffix}"
        state_payload = owned_payload(
            {
                "kind": "campaign_interview_state",
                "version": 1,
                "creation_operation_id": operation_id,
                "phase": "draft",
                "current_step": "ruleset",
                "construction_revision": 0,
                "answers": {},
                "active_subflow": None,
                "current_question_message_id": intro_message_id,
            }
        )
        intro_payload = owned_payload(
            {
                "text": (
                    "Choose a Ruleset for this campaign, or choose No Ruleset for "
                    "narrative-only play."
                ),
                "interview_step": "ruleset",
            }
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            existing = connection.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?",
                (campaign.campaign_id,),
            ).fetchone()
            if existing is not None:
                session = sqlite_session.read_session(connection, builder_session.session_id)
                context = sqlite_session.read_context(connection, builder_session.session_id)
                intro = next(
                    (item for item in context.messages if item.message_id == intro_message_id),
                    None,
                )
                state = next(
                    (item for item in context.messages if item.message_id == state_message_id),
                    None,
                )
                if (
                    self._campaign_record(existing) != campaign
                    or session != builder_session
                    or intro is None
                    or intro.kind is not SessionMessageKind.TALK
                    or dict(intro.content) != dict(intro_payload)
                    or state is None
                    or state.kind is not SessionMessageKind.EVIDENCE
                    or dict(state.content) != dict(state_payload)
                ):
                    raise IdempotencyConflict(
                        "Guided Campaign Draft identity payload does not match"
                    )
                connection.rollback()
                return GuidedCampaignDraft(
                    campaign_id=campaign.campaign_id,
                    builder_session_id=builder_session.session_id,
                    current_step="ruleset",
                    construction_revision=0,
                    resulting_revision=0,
                )
            connection.execute(
                "INSERT INTO campaigns "
                "(campaign_id, display_name, lifecycle, writer, revision, lock_hash, "
                "current_lock_id) VALUES (?, ?, 'active', 'builder', 0, ?, NULL)",
                (campaign.campaign_id, campaign.display_name, _UNBOUND_CAMPAIGN_LOCK_HASH),
            )
            connection.execute(
                "INSERT INTO campaign_lifecycle_state "
                "(campaign_id, retained_active_writer) VALUES (?, 'builder')",
                (campaign.campaign_id,),
            )
            sqlite_session.insert_session(connection, builder_session)
            sqlite_session.append_message(
                connection,
                session_id=builder_session.session_id,
                message_id=intro_message_id,
                kind=SessionMessageKind.TALK,
                content=intro_payload,
            )
            sqlite_session.append_message(
                connection,
                session_id=builder_session.session_id,
                message_id=state_message_id,
                kind=SessionMessageKind.EVIDENCE,
                content=state_payload,
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id, "
                "construction_operation_id) VALUES (?, 0, NULL, NULL, NULL, NULL, NULL)",
                (campaign.campaign_id,),
            )
            self._verify_stored_authority(connection)
            sqlite_packages.verify_package_authority(connection)
            connection.commit()
            return GuidedCampaignDraft(
                campaign_id=campaign.campaign_id,
                builder_session_id=builder_session.session_id,
                current_step="ruleset",
                construction_revision=0,
                resulting_revision=0,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def navigate_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        builder_session_id: str,
        expected_construction_revision: int,
        target_step: str,
        steps: tuple[str, ...],
    ) -> GuidedCampaignDraft:
        """Commit an Interview cursor change with its original answers intact."""
        from sylvia_gm.infrastructure.sqlite_interview_navigation import navigate

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            result = navigate(
                connection, operation_id=operation_id, campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                expected_construction_revision=expected_construction_revision,
                target_step=target_step, steps=steps,
            )
            self._verify_stored_authority(connection)
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def advance_guided_campaign_draft(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        builder_session_id: str,
        expected_construction_revision: int,
        current_step: str,
        visible_answer: str,
        answer: SafePayload,
        preceding_steps: tuple[str, ...],
        next_step: str,
        next_question: str,
    ) -> GuidedCampaignDraft:
        """Append one exact answer, the next Builder question, and the new resume state."""

        suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        answer_message_id = f"guided-answer-{suffix}"
        question_message_id = f"guided-question-{suffix}"
        state_message_id = f"guided-state-{suffix}"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            session = sqlite_session.read_session(connection, builder_session_id)
            if (
                session.campaign_id != campaign_id
                or session.role is not WriterRole.BUILDER
                or session.status is not SessionStatus.ACTIVE
            ):
                raise CommandRejected("Guided Campaign Builder session is unavailable")
            replay_row = connection.execute(
                "SELECT * FROM agent_session_messages WHERE session_id = ? AND message_id = ?",
                (builder_session_id, state_message_id),
            ).fetchone()
            if replay_row is not None:
                replay = sqlite_session.message_from_row(replay_row)
                revision = replay.content.get("construction_revision")
                replay_step = replay.content.get("current_step")
                if (
                    replay.kind is not SessionMessageKind.EVIDENCE
                    or replay.content.get("kind") != "campaign_interview_state"
                    or replay.content.get("last_operation_id") != operation_id
                    or not isinstance(revision, int)
                    or replay_step != next_step
                ):
                    raise IdempotencyConflict(
                        "Guided Campaign answer identity payload does not match"
                    )
                connection.rollback()
                return GuidedCampaignDraft(
                    campaign_id=campaign_id,
                    builder_session_id=builder_session_id,
                    current_step=next_step,
                    construction_revision=revision,
                    resulting_revision=0,
                    answer_message_id=answer_message_id,
                )
            state_rows = tuple(
                connection.execute(
                    "SELECT * FROM agent_session_messages WHERE session_id = ? "
                    "AND kind = 'evidence' ORDER BY sequence DESC",
                    (builder_session_id,),
                )
            )
            current_state: SessionMessage | None = None
            for row in state_rows:
                candidate = sqlite_session.message_from_row(row)
                if candidate.content.get("kind") == "campaign_interview_state":
                    current_state = candidate
                    break
            if current_state is None:
                raise CommandRejected("Guided Campaign Interview State is unavailable")
            state = dict(current_state.content)
            answers_value = state.get("answers")
            if not isinstance(answers_value, Mapping):
                raise SchemaIdentityError("Guided Campaign Interview State is invalid")
            if (
                state.get("phase") != "draft"
                or state.get("construction_revision") != expected_construction_revision
                or state.get("active_subflow") is not None
                or (state.get("current_step") != current_step and current_step not in answers_value)
            ):
                raise StaleRevision("Guided Campaign Interview revision is stale")
            answers = {
                str(key): str(value)
                for key, value in answers_value.items()
                if str(key) in preceding_steps
            }
            retained_value = state.get("subflows")
            retained_subflows = list(retained_value) if isinstance(retained_value, Sequence) else []
            user_content = owned_payload(
                {
                    "text": visible_answer,
                    "interview_step": current_step,
                    "interview_value": answer,
                }
            )
            question_content = owned_payload({"text": next_question, "interview_step": next_step})
            next_revision = expected_construction_revision + 1
            detached_subflows: list[object] = []
            for item in retained_subflows:
                if not isinstance(item, Mapping):
                    detached_subflows.append(item)
                    continue
                resume_step = item.get("resume_step")
                if resume_step is not None and resume_step not in preceding_steps:
                    detached_subflows.append(
                        owned_payload(
                            {
                                **dict(item),
                                "status": "detached",
                                "detached_revision": next_revision,
                            }
                        )
                    )
                else:
                    detached_subflows.append(owned_payload(dict(item)))
            answers[current_step] = answer_message_id
            next_state = owned_payload(
                {
                    **state,
                    "current_step": next_step,
                    "construction_revision": next_revision,
                    "answers": answers,
                    "navigation_frontier": next_step,
                    "subflows": detached_subflows,
                    "last_operation_id": operation_id,
                    "current_question_message_id": question_message_id,
                }
            )
            sqlite_session.append_message(
                connection,
                session_id=builder_session_id,
                message_id=answer_message_id,
                kind=SessionMessageKind.USER,
                content=user_content,
            )
            sqlite_session.append_message(
                connection,
                session_id=builder_session_id,
                message_id=question_message_id,
                kind=SessionMessageKind.TALK,
                content=question_content,
            )
            sqlite_session.append_message(
                connection,
                session_id=builder_session_id,
                message_id=state_message_id,
                kind=SessionMessageKind.EVIDENCE,
                content=next_state,
            )
            self._verify_stored_authority(connection)
            connection.commit()
            return GuidedCampaignDraft(
                campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                current_step=next_step,
                construction_revision=next_revision,
                resulting_revision=0,
                answer_message_id=answer_message_id,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def start_interview_builder_chat(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        expected_construction_revision: int,
        purpose: str,
        expected_step: str,
        visible_answer: str,
        child_session: SessionRecord,
        references: SafePayload | None = None,
    ) -> GuidedCreationSubflow:
        """Create the saved chat and launch snapshot in one owner transaction."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            result = sqlite_builder_launch.launch_from_interview(
                connection,
                operation_id=operation_id,
                campaign_id=campaign_id,
                parent_session_id=parent_session_id,
                expected_construction_revision=expected_construction_revision,
                purpose=purpose,
                expected_step=expected_step,
                visible_answer=visible_answer,
                child_session=child_session,
                references=references,
            )
            self._verify_stored_authority(connection)
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def legacy_builder_contexts(self) -> tuple[SessionContext, ...]:
        """Find former content chats without creating session tables in a fresh database."""
        connection = self._connect_read_only()
        try:
            connection.execute("BEGIN")
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_sessions'"
            ).fetchone() is None:
                return ()
            return sqlite_builder_launch.legacy_contexts(connection)
        finally:
            connection.close()

    def detach_legacy_builder_chat(
        self, *, session_id: str, expected_message_count: int,
        launch_context: SafePayload, display_name: str,
    ) -> None:
        """Commit session relocation after its independent workspace is safely retained."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            sqlite_builder_launch.detach_legacy_chat(
                connection, session_id=session_id, expected_message_count=expected_message_count,
                launch_context=launch_context, display_name=display_name,
            )
            self._verify_stored_authority(connection)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def start_guided_creation_subflow(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        expected_construction_revision: int,
        purpose: str,
        expected_step: str,
        visible_answer: str,
        child_session: SessionRecord,
        trigger_message_id: str | None = None,
    ) -> GuidedCreationSubflow:
        """Persist a linked Builder chat while the parent question remains suspended."""

        suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        generated_trigger_message_id = f"subflow-trigger-{suffix}"
        link_message_id = f"subflow-link-{suffix}"
        intro_message_id = f"subflow-intro-{suffix}"
        state_message_id = f"guided-state-{suffix}"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            replay_row = connection.execute(
                "SELECT * FROM agent_session_messages WHERE session_id = ? AND message_id = ?",
                (parent_session_id, state_message_id),
            ).fetchone()
            if replay_row is not None:
                replay = sqlite_session.message_from_row(replay_row)
                active = replay.content.get("active_subflow")
                if (
                    replay.kind is not SessionMessageKind.EVIDENCE
                    or replay.content.get("kind") != "campaign_interview_state"
                    or replay.content.get("last_operation_id") != operation_id
                    or not isinstance(active, Mapping)
                    or active.get("child_session_id") != child_session.session_id
                    or active.get("purpose") != purpose
                ):
                    raise IdempotencyConflict("Creation Subflow identity payload does not match")
                connection.rollback()
                return GuidedCreationSubflow(
                    campaign_id=campaign_id,
                    parent_session_id=parent_session_id,
                    child_session_id=child_session.session_id,
                    purpose=purpose,
                    current_step=expected_step,
                    status="active",
                    construction_revision=int(replay.content.get("construction_revision", 0)),
                )
            parent = sqlite_session.read_session(connection, parent_session_id)
            parent_context = sqlite_session.read_context(connection, parent_session_id)
            resolved_trigger_message_id = trigger_message_id or generated_trigger_message_id
            if trigger_message_id is not None and not any(
                message.message_id == trigger_message_id and message.kind is SessionMessageKind.USER
                for message in parent_context.messages
            ):
                raise StaleRevision("Creation Subflow trigger answer is unavailable")
            states = tuple(
                message
                for message in parent_context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "campaign_interview_state"
            )
            current_state = states[-1] if states else None
            if (
                parent.campaign_id != campaign_id
                or parent.role is not WriterRole.BUILDER
                or child_session.campaign_id != campaign_id
                or current_state is None
                or current_state.content.get("phase") != "draft"
                or current_state.content.get("current_step") != expected_step
                or current_state.content.get("construction_revision")
                != expected_construction_revision
                or current_state.content.get("active_subflow") is not None
            ):
                raise StaleRevision("Campaign Interview cannot open this Creation Subflow")
            transcript_ids = tuple(
                message.message_id
                for message in parent_context.messages
                if message.kind in (SessionMessageKind.TALK, SessionMessageKind.USER)
            )
            next_revision = expected_construction_revision + 1
            active_subflow = owned_payload(
                {
                    "child_session_id": child_session.session_id,
                    "purpose": purpose,
                    "resume_step": expected_step,
                    "trigger_message_id": resolved_trigger_message_id,
                    "opened_revision": next_revision,
                }
            )
            next_state = owned_payload(
                {
                    **dict(current_state.content),
                    "construction_revision": next_revision,
                    "active_subflow": active_subflow,
                    "last_operation_id": operation_id,
                }
            )
            if trigger_message_id is None:
                sqlite_session.append_message(
                    connection,
                    session_id=parent_session_id,
                    message_id=resolved_trigger_message_id,
                    kind=SessionMessageKind.USER,
                    content=owned_payload(
                        {
                            "text": visible_answer,
                            "interview_step": expected_step,
                            "interview_value": {
                                "action": "create_new",
                                "purpose": purpose,
                                **(
                                    {"requests": visible_answer}
                                    if purpose == "campaign_customization"
                                    else {}
                                ),
                            },
                        }
                    ),
                )
            sqlite_session.insert_session(connection, child_session)
            sqlite_session.append_message(
                connection,
                session_id=child_session.session_id,
                message_id=link_message_id,
                kind=SessionMessageKind.EVIDENCE,
                content=owned_payload(
                    {
                        "kind": "creation_subflow_link",
                        "campaign_id": campaign_id,
                        "parent_session_id": parent_session_id,
                        "purpose": purpose,
                        "resume_step": expected_step,
                        "trigger_message_id": resolved_trigger_message_id,
                        "transcript_message_ids": (
                            list(transcript_ids)
                            if trigger_message_id is not None
                            else [*transcript_ids, resolved_trigger_message_id]
                        ),
                    }
                ),
            )
            sqlite_session.append_message(
                connection,
                session_id=child_session.session_id,
                message_id=intro_message_id,
                kind=SessionMessageKind.TALK,
                content=owned_payload(
                    {
                        "text": (
                            "I will interpret the exact artifact answer, show the material "
                            "meaning clearly, and wait for you to confirm or edit it."
                            if purpose == "adaptive_interpretation"
                            else f"Tell me what you want from this {purpose.replace('_', ' ')}. "
                            "I will build and validate it before you decide whether to use it."
                        )
                    }
                ),
            )
            sqlite_session.append_message(
                connection,
                session_id=parent_session_id,
                message_id=state_message_id,
                kind=SessionMessageKind.EVIDENCE,
                content=next_state,
            )
            self._verify_stored_authority(connection)
            connection.commit()
            return GuidedCreationSubflow(
                campaign_id=campaign_id,
                parent_session_id=parent_session_id,
                child_session_id=child_session.session_id,
                purpose=purpose,
                current_step=expected_step,
                status="active",
                construction_revision=next_revision,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def return_guided_creation_subflow(
        self,
        *,
        operation_id: str,
        campaign_id: str,
        parent_session_id: str,
        child_session_id: str,
        expected_construction_revision: int,
        use_result: bool,
        guided_steps: tuple[str, ...],
        guided_questions: Mapping[str, str],
        next_steps_after_created_result: Mapping[str, str],
    ) -> GuidedCreationSubflow:
        """Return to the suspended question and reference, rather than copy, child work."""

        suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        return_message_id = f"subflow-return-{suffix}"
        question_message_id = f"subflow-question-{suffix}"
        state_message_id = f"guided-state-{suffix}"
        child_receipt_id = f"subflow-receipt-{suffix}"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            replay_row = connection.execute(
                "SELECT * FROM agent_session_messages WHERE session_id = ? AND message_id = ?",
                (parent_session_id, state_message_id),
            ).fetchone()
            if replay_row is not None:
                replay = sqlite_session.message_from_row(replay_row)
                returned = replay.content.get("last_subflow")
                if (
                    replay.kind is not SessionMessageKind.EVIDENCE
                    or replay.content.get("last_operation_id") != operation_id
                    or not isinstance(returned, Mapping)
                    or returned.get("child_session_id") != child_session_id
                    or returned.get("used") is not use_result
                ):
                    raise IdempotencyConflict(
                        "Creation Subflow return identity payload does not match"
                    )
                connection.rollback()
                return GuidedCreationSubflow(
                    campaign_id=campaign_id,
                    parent_session_id=parent_session_id,
                    child_session_id=child_session_id,
                    purpose=str(returned.get("purpose")),
                    current_step=str(replay.content.get("current_step")),
                    status="used" if use_result else "returned",
                    construction_revision=int(replay.content.get("construction_revision", 0)),
                )
            parent_context = sqlite_session.read_context(connection, parent_session_id)
            child = sqlite_session.read_session(connection, child_session_id)
            child_context = sqlite_session.read_context(connection, child_session_id)
            states = tuple(
                message
                for message in parent_context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "campaign_interview_state"
            )
            current_state = states[-1] if states else None
            active = None if current_state is None else current_state.content.get("active_subflow")
            if (
                current_state is None
                or current_state.content.get("phase") != "draft"
                or current_state.content.get("construction_revision")
                != expected_construction_revision
                or not isinstance(active, Mapping)
                or active.get("child_session_id") != child_session_id
                or child.campaign_id != campaign_id
                or child.role is not WriterRole.BUILDER
            ):
                raise StaleRevision("Creation Subflow is no longer active")
            purpose = str(active.get("purpose"))
            resume_step = str(active.get("resume_step"))
            allowed_tools = {
                "ruleset": {"publish_ruleset_candidate"},
                "setting": {"write_file"},
                "plan": {"publish_campaign_plan", "write_file"},
                "player_character": {"publish_participant_artifact"},
                "companion": {"publish_participant_artifact"},
                "campaign_customization": {"publish_campaign_plan", "write_file"},
                "adaptive_interpretation": {"publish_campaign_plan", "write_file"},
            }.get(purpose, set())
            semantic_receipt = next(
                (
                    message
                    for message in reversed(child_context.messages)
                    if message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("tool_name") == "complete_creation_subflow"
                    and isinstance(message.content.get("result"), Mapping)
                ),
                None,
            )
            if use_result and semantic_receipt is not None:
                semantic_result = semantic_receipt.content["result"]
                conflicts = semantic_result.get("boundary_conflicts")
                if semantic_result.get("status") == "blocked" or (
                    isinstance(conflicts, Sequence) and bool(conflicts)
                ):
                    raise CommandRejected(
                        "Creation Subflow conflicts with a hard Boundary; edit the Boundary, "
                        "request, or created content before returning",
                        code="creation_subflow_boundary_conflict",
                    )
            result_message = next(
                (
                    message
                    for message in reversed(child_context.messages)
                    if message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("tool_name") in allowed_tools
                    and isinstance(message.content.get("result"), Mapping)
                    and message.content["result"].get("success") is not False
                    and message.content["result"].get("status") != "rejected"
                ),
                None,
            )
            if use_result and (
                semantic_receipt is None
                or semantic_receipt.content["result"].get("status") != "ready"
            ):
                raise CommandRejected(
                    "Builder has not marked this Creation Subflow semantically complete",
                    code="creation_subflow_semantic_incomplete",
                )
            if (
                use_result
                and result_message is None
                and purpose
                not in {
                    "campaign_customization",
                    "adaptive_interpretation",
                }
            ):
                raise CommandRejected(
                    "Creation Subflow has no validated result to use",
                    code="creation_subflow_result_unavailable",
                )
            selected_result_message = result_message or semantic_receipt
            step_index = guided_steps.index(resume_step)
            next_step = resume_step
            if (
                use_result
                and purpose != "adaptive_interpretation"
                and step_index != len(guided_steps) - 1
            ):
                next_step = next_steps_after_created_result.get(
                    purpose,
                    guided_steps[step_index + 1],
                )
            next_revision = expected_construction_revision + 1
            participant_selection: dict[str, object] | None = None
            if use_result and purpose in {"player_character", "companion"}:
                if selected_result_message is None:
                    raise CommandRejected(
                        "Creation Subflow participant result is unavailable",
                        code="creation_subflow_result_unavailable",
                    )
                selected_payload = selected_result_message.content.get("result")
                artifact_id = (
                    selected_payload.get("id")
                    if isinstance(selected_payload, Mapping)
                    else None
                )
                if (
                    not isinstance(artifact_id, str)
                    or not artifact_id.startswith("participant-")
                ):
                    raise CommandRejected(
                        "Creation Subflow participant result is invalid",
                        code="creation_subflow_result_unavailable",
                    )
                previous_selection: Mapping[str, object] = {}
                answers_value = current_state.content.get("answers")
                previous_message_id = (
                    answers_value.get("participants")
                    if isinstance(answers_value, Mapping)
                    else None
                )
                previous_message = next(
                    (
                        message
                        for message in parent_context.messages
                        if message.message_id == previous_message_id
                    ),
                    None,
                )
                if previous_message is not None:
                    previous_value = previous_message.content.get("interview_value")
                    if isinstance(previous_value, Mapping):
                        previous_selection = previous_value
                previous_archetype = previous_selection.get("archetype_id")
                archetype_id = (
                    artifact_id
                    if purpose == "player_character"
                    else (
                        previous_archetype
                        if isinstance(previous_archetype, str) and previous_archetype
                        else "open-adventurer"
                    )
                )
                companions_value = previous_selection.get("companion_ids")
                companion_ids = (
                    [
                        str(item)
                        for item in companions_value
                        if isinstance(item, str) and item
                    ]
                    if isinstance(companions_value, Sequence)
                    and not isinstance(companions_value, (str, bytes, bytearray))
                    else []
                )
                if purpose == "companion" and artifact_id not in companion_ids:
                    companion_ids.append(artifact_id)
                participant_selection = {
                    "action": "use_subflow_result",
                    "purpose": purpose,
                    "child_session_id": child_session_id,
                    "result_message_id": selected_result_message.message_id,
                    "archetype_id": archetype_id,
                    "companion_ids": companion_ids,
                }
            return_content = owned_payload(
                {
                    "text": ("Use in Campaign & Return" if use_result else "Return Without Using"),
                    "interview_step": resume_step,
                    "interview_value": (
                        participant_selection
                        if participant_selection is not None
                        else {
                            "action": (
                                "use_subflow_result" if use_result else "return_without_using"
                            ),
                            "purpose": purpose,
                            "child_session_id": child_session_id,
                            "result_message_id": (
                                None
                                if selected_result_message is None
                                else selected_result_message.message_id
                            ),
                        }
                    ),
                }
            )
            answers_value = current_state.content.get("answers")
            if not isinstance(answers_value, Mapping):
                raise SchemaIdentityError("Campaign Interview answers are invalid")
            answers = {str(key): str(value) for key, value in answers_value.items()}
            if use_result and resume_step != "review" and purpose != "adaptive_interpretation":
                answers[resume_step] = (
                    str(active.get("trigger_message_id"))
                    if purpose == "campaign_customization"
                    else return_message_id
                )
            retained_value = current_state.content.get("subflows")
            retained = list(retained_value) if isinstance(retained_value, Sequence) else []
            returned = owned_payload(
                {
                    "child_session_id": child_session_id,
                    "purpose": purpose,
                    "resume_step": resume_step,
                    "used": use_result,
                    "status": "used" if use_result else "returned",
                    "result_message_id": (
                        None
                        if selected_result_message is None
                        else selected_result_message.message_id
                    ),
                    "returned_revision": next_revision,
                }
            )
            retained.append(returned)
            next_state = owned_payload(
                {
                    **dict(current_state.content),
                    "current_step": next_step,
                    "construction_revision": next_revision,
                    "answers": answers,
                    "active_subflow": None,
                    "subflows": retained,
                    "last_subflow": returned,
                    "last_operation_id": operation_id,
                    "current_question_message_id": question_message_id,
                }
            )
            sqlite_session.append_message(
                connection,
                session_id=parent_session_id,
                message_id=return_message_id,
                kind=SessionMessageKind.USER,
                content=return_content,
            )
            sqlite_session.append_message(
                connection,
                session_id=parent_session_id,
                message_id=question_message_id,
                kind=SessionMessageKind.TALK,
                content=owned_payload(
                    {"text": guided_questions[next_step], "interview_step": next_step}
                ),
            )
            sqlite_session.append_message(
                connection,
                session_id=child_session_id,
                message_id=child_receipt_id,
                kind=SessionMessageKind.EVIDENCE,
                content=owned_payload(
                    {
                        "kind": "creation_subflow_return",
                        "parent_session_id": parent_session_id,
                        "status": "used" if use_result else "returned",
                        "result_message_id": (
                            None
                            if selected_result_message is None
                            else selected_result_message.message_id
                        ),
                    }
                ),
            )
            sqlite_session.append_message(
                connection,
                session_id=parent_session_id,
                message_id=state_message_id,
                kind=SessionMessageKind.EVIDENCE,
                content=next_state,
            )
            sqlite_session.update_session_status(
                connection, child_session_id, SessionStatus.COMPLETED
            )
            self._verify_stored_authority(connection)
            connection.commit()
            return GuidedCreationSubflow(
                campaign_id=campaign_id,
                parent_session_id=parent_session_id,
                child_session_id=child_session_id,
                purpose=purpose,
                current_step=next_step,
                status="used" if use_result else "returned",
                construction_revision=next_revision,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def bind_guided_campaign_basis(
        self,
        *,
        operation_id: str,
        semantic_payload: SafePayload,
        payload_sha256: str,
        campaign_id: str,
        display_name: str,
        package_lock: PackageLock,
        turn_id: str,
        builder_session_id: str,
        participants: Iterable[Participant] = (),
        initial_state: Iterable[StateValue] = (),
        required_interview_steps: frozenset[str] = frozenset(
            {
                "ruleset",
                "setting",
                "plan",
                "name",
                "participants",
                "boundaries_preferences",
                "specific_requests",
            }
        ),
    ) -> CampaignConfirmation:
        """Attach the exact legacy construction basis to one existing unbound Draft."""

        participants = tuple(participants)
        initial_state = tuple(initial_state)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            replay = self._campaign_operation_hit(
                connection,
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                payload_sha256=payload_sha256,
            )
            if replay is not None:
                if not isinstance(replay, CampaignConfirmation):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                connection.rollback()
                return replay
            campaign = connection.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            session = sqlite_session.read_session(connection, builder_session_id)
            session_context = sqlite_session.read_context(connection, builder_session_id)
            interview_states = tuple(
                message
                for message in session_context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "campaign_interview_state"
            )
            interview_state = interview_states[-1] if interview_states else None
            interview_answers = (
                None if interview_state is None else interview_state.content.get("answers")
            )
            ready_messages = tuple(
                message
                for message in session_context.messages
                if message.kind is SessionMessageKind.USER
                and message.content.get("interview_step") == "ready"
                and message.content.get("text") == "Ready to Begin"
            )
            if (
                campaign is None
                or not sqlite_draft_history.has_only_draft_lifecycle_history(
                    connection, campaign_id, int(campaign["revision"])
                )
                or str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value
                or str(campaign["writer"]) != WriterRole.BUILDER.value
                or campaign["current_lock_id"] is not None
                or str(campaign["lock_hash"]) != _UNBOUND_CAMPAIGN_LOCK_HASH
                or session.campaign_id != campaign_id
                or session.role is not WriterRole.BUILDER
                or interview_state is None
                or interview_state.content.get("current_step") != "review"
                or not isinstance(interview_answers, Mapping)
                or set(interview_answers) != set(required_interview_steps)
                or len(ready_messages) != 1
                or connection.execute(
                    "SELECT 1 FROM turns WHERE campaign_id = ? LIMIT 1", (campaign_id,)
                ).fetchone()
                is not None
                or connection.execute(
                    "SELECT 1 FROM campaign_constructions WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()
                is not None
            ):
                raise CommandRejected("Guided Campaign Draft is no longer unbound")
            confirmation_revision = int(campaign["revision"])
            sqlite_packages.verify_published_lock(connection, package_lock)
            lock_hash = _lock_representation(package_lock)
            lock_record = sqlite_packages.insert_campaign_lock(
                connection,
                campaign_id=campaign_id,
                package_lock=package_lock,
                revision=confirmation_revision,
                predecessor_lock_id=None,
            )
            connection.execute(
                "UPDATE campaigns SET display_name = ?, lock_hash = ?, current_lock_id = ? "
                "WHERE campaign_id = ?",
                (display_name, lock_hash, lock_record.lock_id, campaign_id),
            )
            for entry in package_lock.entries:
                connection.execute(
                    "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        campaign_id,
                        entry.package_id,
                        entry.version,
                        entry.sha256,
                        entry.role.value,
                        entry.load_order,
                    ),
                )
            if any(
                participant.validation_status is not ParticipantValidationStatus.VALIDATED
                for participant in participants
            ):
                raise CommandRejected(
                    "Confirmed campaign participants require exact package and schema proof",
                    code="participant_exact_proof_required",
                )
            if any(
                not self._participant_adaptation_is_admitted(connection, participant)
                for participant in participants
            ):
                raise CommandRejected(
                    "Cross-schema participant reuse requires a durable adaptation operation",
                    code="participant_adaptation_required",
                )
            for participant in participants:
                connection.execute(
                    "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        campaign_id,
                        participant.participant_id,
                        participant.kind,
                        participant.package_id,
                        participant.package_version,
                        _dump(dict(participant.data)),
                        participant.package_sha256,
                        participant.schema_id,
                        participant.schema_sha256,
                        participant.validation_status.value,
                        participant.adaptation_id,
                    ),
                )
            initial_owners = {
                state_value.namespace: sqlite_state_ownership.initial_owner(
                    package_lock, state_value.namespace
                )
                for state_value in initial_state
            }
            for namespace, owner_package_id in sorted(initial_owners.items()):
                connection.execute(
                    "INSERT INTO campaign_state_namespace_owners "
                    "(campaign_id, namespace, owner_package_id) VALUES (?, ?, ?)",
                    (campaign_id, namespace, owner_package_id),
                )
            for state_value in initial_state:
                connection.execute(
                    "INSERT INTO campaign_state VALUES (?, ?, ?, ?)",
                    (
                        campaign_id,
                        state_value.namespace,
                        state_value.key,
                        _dump(state_value.value),
                    ),
                )
            connection.execute(
                "INSERT INTO turns VALUES (?, ?, 'open', ?)",
                (campaign_id, turn_id, confirmation_revision),
            )
            result = owned_payload({
                "campaign_id": campaign_id, "resulting_revision": confirmation_revision,
            })
            self._insert_campaign_operation(
                connection,
                operation_id=operation_id,
                operation_kind="confirm_new_campaign",
                campaign_id=campaign_id,
                semantic_payload=semantic_payload,
                payload_sha256=payload_sha256,
                result=result,
                basis_turn_id=turn_id,
                basis_lock_id=lock_record.lock_id,
                basis_revision=confirmation_revision,
                resulting_revision=confirmation_revision,
            )
            connection.execute(
                "INSERT INTO campaign_constructions "
                "(campaign_id, confirmation_operation_id, authority_turn_id, "
                "authority_lock_id, confirmation_revision) VALUES (?, ?, ?, ?, ?)",
                (campaign_id, operation_id, turn_id, lock_record.lock_id, confirmation_revision),
            )
            self._verify_stored_authority(connection)
            sqlite_packages.verify_package_authority(connection)
            sqlite_campaign.verify_f5_campaign_authority(connection)
            connection.commit()
            return CampaignConfirmation(
                campaign_id=campaign_id, resulting_revision=confirmation_revision,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _construction_basis(
        connection: sqlite3.Connection, campaign_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        campaign = connection.execute(
            "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        construction = connection.execute(
            "SELECT * FROM campaign_constructions WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if campaign is None or construction is None:
            raise CommandRejected("Confirmed campaign construction is unavailable")
        turn = connection.execute(
            "SELECT * FROM turns WHERE campaign_id = ? AND turn_id = ?",
            (campaign_id, str(construction["authority_turn_id"])),
        ).fetchone()
        if turn is None:
            raise CommandRejected("Campaign construction TURN basis is unavailable")
        return campaign, construction, turn

    def campaign_construction_basis(self, campaign_id: str) -> CampaignConstructionBasis:
        """Read the exact owner-only materialization basis for one begun construction."""

        connection = self._connect()
        try:
            # These admitted predecessors predate Interview records. Optional readers
            # need the same absence result as a current campaign without a construction.
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) in (
                _SCHEMA_VERSION, _SESSION_SCHEMA_VERSION,
            ):
                raise CommandRejected("Confirmed campaign construction is unavailable")
            campaign, construction, _turn = self._construction_basis(connection, campaign_id)
            if construction["begin_operation_id"] is None:
                raise CommandRejected("Campaign construction has not begun")
            confirmation = connection.execute(
                "SELECT semantic_payload_json FROM campaign_operations WHERE operation_id = ?",
                (str(construction["confirmation_operation_id"]),),
            ).fetchone()
            if confirmation is None:
                raise CommandRejected("Campaign confirmation receipt is unavailable")
            confirmation_payload = _load_payload(str(confirmation["semantic_payload_json"]))
            builder_value = confirmation_payload.get("builder_session")
            if not isinstance(builder_value, Mapping) or not isinstance(
                builder_value.get("session_id"), str
            ):
                raise CommandRejected("Campaign Builder session basis is unavailable")
            candidate_value = confirmation_payload.get("package_candidate_id")
            if candidate_value is not None and not isinstance(candidate_value, str):
                raise CommandRejected("Campaign package candidate basis is invalid")
            canonical_plan_value = construction["canonical_plan_json"]
            completion_revision_value = construction["completion_revision"]
            representation_value = construction["canonical_representation_sha256"]
            return CampaignConstructionBasis(
                campaign_id=campaign_id,
                display_name=str(campaign["display_name"]),
                lane=str(construction["lane"]),
                canonical_input=_load_payload(str(construction["canonical_input_json"])),
                canonical_plan=(
                    None
                    if canonical_plan_value is None
                    else _load_payload(str(canonical_plan_value))
                ),
                authority_turn_id=str(construction["authority_turn_id"]),
                package_lock_id=str(construction["authority_lock_id"]),
                package_candidate_id=candidate_value,
                builder_session_id=str(builder_value["session_id"]),
                begin_revision=int(construction["begin_revision"]),
                completion_revision=(
                    None if completion_revision_value is None else int(completion_revision_value)
                ),
                canonical_representation_sha256=(
                    None if representation_value is None else str(representation_value)
                ),
            )
        finally:
            connection.close()

    def begin_campaign_construction(
        self,
        *,
        operation_id: str,
        semantic_payload: SafePayload,
        payload_sha256: str,
        campaign_id: str,
        lane: str,
        expected_revision: int,
        canonical_input: SafePayload,
    ) -> CampaignConstructionBegun:
        """Commit one begun lane and its exact basis revision in one transaction."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            replay = self._campaign_operation_hit(
                connection,
                operation_id=operation_id,
                operation_kind="begin_campaign_construction",
                payload_sha256=payload_sha256,
            )
            if replay is not None:
                if not isinstance(replay, CampaignConstructionBegun):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                connection.rollback()
                return replay
            campaign, construction, turn = self._construction_basis(connection, campaign_id)
            confirmation_revision = int(construction["confirmation_revision"])
            if construction["begin_operation_id"] is not None:
                raise CommandRejected("Campaign construction has already begun")
            if str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value:
                raise CommandRejected("Campaign lifecycle blocks construction")
            if str(campaign["writer"]) != WriterRole.BUILDER.value:
                raise WrongWriter("Builder is not the authoritative campaign writer")
            if int(campaign["revision"]) != expected_revision:
                raise StaleRevision("Campaign construction expected revision is stale")
            if (
                expected_revision != confirmation_revision
                or str(campaign["current_lock_id"]) != str(construction["authority_lock_id"])
                or str(turn["status"]) != TurnStatus.OPEN.value
                or int(turn["created_revision"]) != confirmation_revision
            ):
                raise CommandRejected("Campaign construction confirmation basis has drifted")
            resulting_revision = confirmation_revision + 1
            result = owned_payload(
                {
                    "campaign_id": campaign_id,
                    "lane": lane,
                    "resulting_revision": resulting_revision,
                }
            )
            self._insert_campaign_operation(
                connection,
                operation_id=operation_id,
                operation_kind="begin_campaign_construction",
                campaign_id=campaign_id,
                semantic_payload=semantic_payload,
                payload_sha256=payload_sha256,
                result=result,
                basis_turn_id=str(construction["authority_turn_id"]),
                basis_lock_id=str(construction["authority_lock_id"]),
                basis_revision=confirmation_revision,
                resulting_revision=resulting_revision,
            )
            updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ? "
                "AND lifecycle = 'active' AND writer = 'builder' AND current_lock_id = ?",
                (
                    resulting_revision,
                    campaign_id,
                    confirmation_revision,
                    str(construction["authority_lock_id"]),
                ),
            )
            if updated.rowcount != 1:
                raise StaleRevision("Campaign construction revision fence rejected admission")
            connection.execute(
                "UPDATE campaign_constructions SET lane = ?, canonical_input_json = ?, "
                "begin_operation_id = ?, begin_revision = ? WHERE campaign_id = ? "
                "AND begin_operation_id IS NULL",
                (lane, _dump(canonical_input), operation_id, resulting_revision, campaign_id),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id, "
                "construction_operation_id) VALUES (?, ?, NULL, NULL, NULL, NULL, ?)",
                (campaign_id, resulting_revision, operation_id),
            )
            self._before_construction_commit()
            connection.commit()
            return CampaignConstructionBegun(campaign_id, lane, resulting_revision)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def complete_campaign_construction(
        self,
        *,
        operation_id: str,
        semantic_payload: SafePayload,
        payload_sha256: str,
        campaign_id: str,
        expected_revision: int,
        canonical_plan: SafePayload,
    ) -> CampaignConstructionCompleted:
        """Commit the lane-neutral canonical representation on the begun exact basis."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            replay = self._campaign_operation_hit(
                connection,
                operation_id=operation_id,
                operation_kind="complete_campaign_construction",
                payload_sha256=payload_sha256,
            )
            if replay is not None:
                if not isinstance(replay, CampaignConstructionCompleted):
                    raise IdempotencyConflict("Campaign operation result kind does not match")
                connection.rollback()
                return replay
            campaign, construction, turn = self._construction_basis(connection, campaign_id)
            if construction["begin_operation_id"] is None:
                raise CommandRejected("Campaign construction has not begun")
            if construction["completion_operation_id"] is not None:
                raise CommandRejected("Campaign construction is already complete")
            if str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value:
                raise CommandRejected("Campaign lifecycle blocks construction")
            if str(campaign["writer"]) != WriterRole.BUILDER.value:
                raise WrongWriter("Builder is not the authoritative campaign writer")
            if int(campaign["revision"]) != expected_revision:
                raise StaleRevision("Campaign construction expected revision is stale")
            begin_revision = int(construction["begin_revision"])
            if (
                expected_revision != begin_revision
                or str(campaign["current_lock_id"]) != str(construction["authority_lock_id"])
                or str(turn["status"]) != TurnStatus.OPEN.value
                or int(turn["created_revision"]) != int(construction["confirmation_revision"])
            ):
                raise CommandRejected("Campaign construction begin basis has drifted")
            canonical_input = _load_payload(str(construction["canonical_input_json"]))
            representation_sha256 = hashlib.sha256(
                canonical_json_bytes(
                    {"canonical_input": canonical_input, "canonical_plan": canonical_plan}
                )
            ).hexdigest()
            lane = str(construction["lane"])
            resulting_revision = begin_revision + 1
            result = owned_payload(
                {
                    "campaign_id": campaign_id,
                    "lane": lane,
                    "canonical_representation_sha256": representation_sha256,
                    "resulting_revision": resulting_revision,
                }
            )
            self._insert_campaign_operation(
                connection,
                operation_id=operation_id,
                operation_kind="complete_campaign_construction",
                campaign_id=campaign_id,
                semantic_payload=semantic_payload,
                payload_sha256=payload_sha256,
                result=result,
                basis_turn_id=str(construction["authority_turn_id"]),
                basis_lock_id=str(construction["authority_lock_id"]),
                basis_revision=begin_revision,
                resulting_revision=resulting_revision,
            )
            updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ? "
                "AND lifecycle = 'active' AND writer = 'builder' AND current_lock_id = ?",
                (
                    resulting_revision,
                    campaign_id,
                    begin_revision,
                    str(construction["authority_lock_id"]),
                ),
            )
            if updated.rowcount != 1:
                raise StaleRevision("Campaign construction revision fence rejected admission")
            connection.execute(
                "UPDATE campaign_constructions SET canonical_plan_json = ?, "
                "completion_operation_id = ?, completion_revision = ?, "
                "canonical_representation_sha256 = ? WHERE campaign_id = ? "
                "AND completion_operation_id IS NULL",
                (
                    _dump(canonical_plan),
                    operation_id,
                    resulting_revision,
                    representation_sha256,
                    campaign_id,
                ),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id, "
                "construction_operation_id) VALUES (?, ?, NULL, NULL, NULL, NULL, ?)",
                (campaign_id, resulting_revision, operation_id),
            )
            self._before_construction_commit()
            connection.commit()
            return CampaignConstructionCompleted(
                campaign_id,
                lane,
                representation_sha256,
                resulting_revision,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def commit_guided_campaign_start(
        self,
        *,
        completion_id: str,
        completion_semantic_payload: SafePayload,
        completion_payload_sha256: str,
        campaign_id: str,
        expected_begin_revision: int,
        canonical_plan: SafePayload,
        evidence: BuilderEvidence,
        plan_id: str,
        plan_content_sha256: str,
        plan_content: SafePayload,
        plan_payload_sha256: str,
        handoff_id: str,
        dm_session_id: str,
        dm_provider_id: str,
        dm_model_id: str,
        expected_package_candidate_id: str | None,
        model_guidance: ModelGuidanceDecision,
        handoff_payload_sha256: str,
        dm_turn_id: str,
    ) -> tuple[CampaignConstructionCompleted, CampaignPlan, BuilderDMHandoff, str]:
        """Commit guided completion, Plan admission, handoff, and initial DM TURN together."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection, version=_CAMPAIGN_SCHEMA_VERSION)
            existing_handoff = sqlite_session.read_handoff(connection, handoff_id)
            if existing_handoff is not None:
                if existing_handoff.payload_sha256 != handoff_payload_sha256:
                    raise IdempotencyConflict(
                        "Builder-to-DM handoff identity payload does not match"
                    )
                construction = connection.execute(
                    "SELECT * FROM campaign_constructions WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()
                existing_plan = sqlite_session.read_plan(connection, plan_id)
                dm_turn = connection.execute(
                    "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                    (campaign_id, dm_turn_id),
                ).fetchone()
                if (
                    construction is None
                    or existing_plan is None
                    or dm_turn is None
                    or str(dm_turn["status"]) != TurnStatus.OPEN.value
                ):
                    raise SchemaIdentityError("Guided Campaign start replay is incomplete")
                connection.rollback()
                return (
                    CampaignConstructionCompleted(
                        campaign_id=campaign_id,
                        lane=str(construction["lane"]),
                        canonical_representation_sha256=str(
                            construction["canonical_representation_sha256"]
                        ),
                        resulting_revision=int(construction["completion_revision"]),
                    ),
                    existing_plan,
                    existing_handoff,
                    dm_turn_id,
                )

            campaign, construction, construction_turn = self._construction_basis(
                connection, campaign_id
            )
            if (
                construction["begin_operation_id"] is None
                or construction["completion_operation_id"] is not None
                or int(campaign["revision"]) != expected_begin_revision
                or expected_begin_revision != int(construction["begin_revision"])
                or str(campaign["writer"]) != WriterRole.BUILDER.value
                or str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value
                or str(construction_turn["status"]) != TurnStatus.OPEN.value
            ):
                raise CommandRejected("Guided Campaign start basis has drifted")
            builder_session_id = str(
                _load_payload(
                    str(
                        connection.execute(
                            "SELECT semantic_payload_json FROM campaign_operations "
                            "WHERE operation_id = ?",
                            (str(construction["confirmation_operation_id"]),),
                        ).fetchone()[0]
                    )
                )["builder_session"]["session_id"]
            )
            builder = sqlite_session.read_session(connection, builder_session_id)
            if builder.status is not SessionStatus.ACTIVE:
                raise CommandRejected("Guided Campaign Builder session is not active")
            context = sqlite_session.read_context(connection, builder_session_id)
            ready_message = next(
                (
                    message
                    for message in reversed(context.messages)
                    if message.kind is SessionMessageKind.USER
                    and message.content.get("interview_step") == "ready"
                    and message.content.get("text") == "Ready to Begin"
                ),
                None,
            )
            if ready_message is None:
                raise CommandRejected("Guided Campaign start requires genuine Ready input")
            interview_states = tuple(
                message
                for message in context.messages
                if message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind") == "campaign_interview_state"
            )
            current_interview_state = interview_states[-1] if interview_states else None
            if (
                current_interview_state is None
                or current_interview_state.content.get("current_step") != "review"
                or current_interview_state.content.get("active_subflow") is not None
            ):
                raise CommandRejected("Guided Campaign start has unfinished Builder work")
            if (
                connection.execute(
                    "SELECT 1 FROM agent_sessions WHERE campaign_id = ? "
                    "AND parent_session_id IS NOT NULL "
                    "AND status NOT IN ('stopped', 'completed', 'handoff_closed') LIMIT 1",
                    (campaign_id,),
                ).fetchone()
                is not None
            ):
                raise CommandRejected("Guided Campaign start has active delegated child work")
            if (
                connection.execute(
                    "SELECT 1 FROM agent_sessions WHERE campaign_id = ? "
                    "AND status = 'review_required' LIMIT 1",
                    (campaign_id,),
                ).fetchone()
                is not None
            ):
                raise CommandRejected("Guided Campaign start has unresolved review work")

            canonical_input = _load_payload(str(construction["canonical_input_json"]))
            representation_sha256 = hashlib.sha256(
                canonical_json_bytes(
                    {"canonical_input": canonical_input, "canonical_plan": canonical_plan}
                )
            ).hexdigest()
            completion_revision = expected_begin_revision + 1
            completion_result = owned_payload(
                {
                    "campaign_id": campaign_id,
                    "lane": str(construction["lane"]),
                    "canonical_representation_sha256": representation_sha256,
                    "resulting_revision": completion_revision,
                }
            )
            self._insert_campaign_operation(
                connection,
                operation_id=completion_id,
                operation_kind="complete_campaign_construction",
                campaign_id=campaign_id,
                semantic_payload=completion_semantic_payload,
                payload_sha256=completion_payload_sha256,
                result=completion_result,
                basis_turn_id=str(construction["authority_turn_id"]),
                basis_lock_id=str(construction["authority_lock_id"]),
                basis_revision=expected_begin_revision,
                resulting_revision=completion_revision,
            )
            connection.execute(
                "UPDATE campaign_constructions SET canonical_plan_json = ?, "
                "completion_operation_id = ?, completion_revision = ?, "
                "canonical_representation_sha256 = ? WHERE campaign_id = ?",
                (
                    _dump(canonical_plan),
                    completion_id,
                    completion_revision,
                    representation_sha256,
                    campaign_id,
                ),
            )
            connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ?",
                (completion_revision, campaign_id, expected_begin_revision),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id, "
                "construction_operation_id) VALUES (?, ?, NULL, NULL, NULL, NULL, ?)",
                (campaign_id, completion_revision, completion_id),
            )

            sqlite_session.append_message(
                connection,
                session_id=builder_session_id,
                message_id=evidence.evidence_id,
                kind=SessionMessageKind.EVIDENCE,
                content=evidence.payload(),
            )
            expected_plan_payload = campaign_plan_payload_sha256(
                plan_id=plan_id,
                campaign_id=campaign_id,
                source_session_id=builder_session_id,
                expected_revision=completion_revision,
                expected_package_lock_id=str(construction["authority_lock_id"]),
                content_sha256=plan_content_sha256,
                content=plan_content,
                provenance_evidence_ids=(evidence.evidence_id,),
                validation_outcome=PlanValidationOutcome.VALID,
                acceptance_status=PlanAcceptanceStatus.ACCEPTED,
            )
            if expected_plan_payload != plan_payload_sha256:
                raise IdempotencyConflict("Campaign Plan payload identity does not match")
            plan_revision = 1
            plan_resulting_revision = completion_revision + 1
            plan = CampaignPlan(
                plan_id=plan_id,
                campaign_id=campaign_id,
                package_lock_id=str(construction["authority_lock_id"]),
                source_session_id=builder_session_id,
                content_sha256=plan_content_sha256,
                content=plan_content,
                provenance_evidence_ids=(evidence.evidence_id,),
                validation_outcome=PlanValidationOutcome.VALID,
                acceptance_status=PlanAcceptanceStatus.ACCEPTED,
                plan_revision=plan_revision,
                resulting_campaign_revision=plan_resulting_revision,
                payload_sha256=plan_payload_sha256,
            )
            connection.execute(
                "INSERT INTO campaign_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.plan_id,
                    plan.campaign_id,
                    plan.package_lock_id,
                    plan.source_session_id,
                    plan.content_sha256,
                    _dump(plan.content),
                    _dump(list(plan.provenance_evidence_ids)),
                    plan.validation_outcome.value,
                    plan.acceptance_status.value,
                    plan.plan_revision,
                    plan.resulting_campaign_revision,
                    plan.payload_sha256,
                ),
            )
            connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ?",
                (plan_resulting_revision, campaign_id, completion_revision),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id) "
                "VALUES (?, ?, NULL, NULL, ?, NULL)",
                (campaign_id, plan_resulting_revision, plan_id),
            )
            connection.execute(
                "UPDATE turns SET status = 'completed' WHERE campaign_id = ? AND turn_id = ?",
                (campaign_id, str(construction["authority_turn_id"])),
            )

            current_lock = sqlite_packages.current_lock_record(connection, campaign_id)
            candidate = (
                None
                if expected_package_candidate_id is None
                else sqlite_packages.read_candidate(connection, expected_package_candidate_id)
            )
            if not _handoff_candidate_matches_lock(current_lock, candidate):
                raise CommandRejected("Guided Campaign start package candidate is inexact")
            primary = sqlite_packages.primary_manifest_for_lock(
                connection, str(construction["authority_lock_id"])
            )
            minimum = None if primary is None else primary.minimum_model_capability_tier
            if (
                model_guidance.model_id != dm_model_id
                or model_guidance.minimum_tier_id != minimum
                or not model_guidance.may_continue
            ):
                raise CommandRejected("Guided Campaign start model guidance is not admitted")
            expected_handoff_payload = builder_dm_handoff_payload_sha256(
                handoff_id=handoff_id,
                campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                dm_session_id=dm_session_id,
                dm_provider_id=dm_provider_id,
                dm_model_id=dm_model_id,
                expected_writer=WriterRole.BUILDER,
                expected_revision=plan_resulting_revision,
                expected_package_lock_id=str(construction["authority_lock_id"]),
                expected_package_candidate_id=expected_package_candidate_id,
                plan_id=plan_id,
                expected_plan_revision=plan_revision,
                expected_plan_content_sha256=plan_content_sha256,
                completed_turn_id=str(construction["authority_turn_id"]),
                model_guidance_sha256=model_guidance_payload_sha256(model_guidance),
            )
            if expected_handoff_payload != handoff_payload_sha256:
                raise IdempotencyConflict("Builder-to-DM handoff payload identity does not match")
            handoff_revision = plan_resulting_revision + 1
            chronology = owned_payload(
                {
                    "campaign_id": campaign_id,
                    "builder_session_id": builder_session_id,
                    "dm_session_id": dm_session_id,
                    "dm_provider_id": dm_provider_id,
                    "package_lock_id": str(construction["authority_lock_id"]),
                    "package_candidate_id": expected_package_candidate_id,
                    "plan_id": plan_id,
                    "resulting_campaign_revision": handoff_revision,
                    "completed_turn_id": str(construction["authority_turn_id"]),
                    "model_guidance": model_guidance_payload(model_guidance),
                }
            )
            handoff = BuilderDMHandoff(
                handoff_id=handoff_id,
                campaign_id=campaign_id,
                builder_session_id=builder_session_id,
                dm_session_id=dm_session_id,
                package_lock_id=str(construction["authority_lock_id"]),
                package_candidate_id=expected_package_candidate_id,
                plan_id=plan_id,
                plan_revision=plan_revision,
                plan_content_sha256=plan_content_sha256,
                expected_revision=plan_resulting_revision,
                resulting_campaign_revision=handoff_revision,
                chronology_ordinal=1,
                completed_turn_id=str(construction["authority_turn_id"]),
                model_guidance=model_guidance,
                payload_sha256=handoff_payload_sha256,
            )
            connection.execute(
                "UPDATE campaigns SET writer = 'dm', revision = ? WHERE campaign_id = ? "
                "AND writer = 'builder' AND revision = ?",
                (handoff_revision, campaign_id, plan_resulting_revision),
            )
            connection.execute(
                "UPDATE campaign_lifecycle_state SET retained_active_writer = 'dm' "
                "WHERE campaign_id = ? AND retained_active_writer = 'builder'",
                (campaign_id,),
            )
            sqlite_session.insert_session(
                connection,
                SessionRecord(
                    session_id=dm_session_id,
                    campaign_id=campaign_id,
                    role=WriterRole.DM,
                    provider_id=dm_provider_id,
                    model_id=dm_model_id,
                    status=SessionStatus.ACTIVE,
                ),
            )
            structured_plan = plan_content.get("campaign_plan")
            if isinstance(structured_plan, Mapping):
                sqlite_continuity.seed_initial_state(
                    connection,
                    campaign_id=campaign_id,
                    plan=cast(Mapping[str, object], structured_plan),
                    admitted_revision=handoff_revision,
                )
            sqlite_session.append_message(
                connection,
                session_id=dm_session_id,
                message_id=f"ready-reference-{ready_message.message_id}",
                kind=SessionMessageKind.EVIDENCE,
                content=owned_payload(
                    {
                        "kind": "ready_to_begin_reference",
                        "source_session_id": builder_session_id,
                        "source_message_id": ready_message.message_id,
                    }
                ),
            )
            connection.execute(
                "INSERT INTO builder_dm_handoffs "
                "(handoff_id, campaign_id, payload_sha256, builder_session_id, dm_session_id, "
                "package_lock_id, package_candidate_id, plan_id, plan_revision, "
                "plan_content_sha256, expected_revision, resulting_campaign_revision, "
                "chronology_ordinal, completed_turn_id, safe_payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    handoff.handoff_id,
                    handoff.campaign_id,
                    handoff.payload_sha256,
                    handoff.builder_session_id,
                    handoff.dm_session_id,
                    handoff.package_lock_id,
                    handoff.package_candidate_id,
                    handoff.plan_id,
                    handoff.plan_revision,
                    handoff.plan_content_sha256,
                    handoff.expected_revision,
                    handoff.resulting_campaign_revision,
                    handoff.chronology_ordinal,
                    handoff.completed_turn_id,
                    _dump(chronology),
                ),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id) "
                "VALUES (?, ?, NULL, NULL, NULL, ?)",
                (campaign_id, handoff_revision, handoff_id),
            )
            connection.execute(
                "INSERT INTO turns VALUES (?, ?, 'open', ?)",
                (campaign_id, dm_turn_id, handoff_revision),
            )
            self._verify_stored_authority(connection)
            sqlite_packages.verify_package_authority(connection)
            sqlite_campaign.verify_f5_campaign_authority(connection)
            sqlite_continuity.verify_authority(connection)
            connection.commit()
            return (
                CampaignConstructionCompleted(
                    campaign_id=campaign_id,
                    lane=str(construction["lane"]),
                    canonical_representation_sha256=representation_sha256,
                    resulting_revision=completion_revision,
                ),
                plan,
                handoff,
                dm_turn_id,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _before_construction_commit(self) -> None:
        """Private deterministic fault seam for all three Probe 01 transactions."""

    def create_campaign(
        self,
        campaign: CampaignRecord,
        package_lock: PackageLock,
        turn_id: str,
        participants: Iterable[Participant],
        initial_state: Iterable[StateValue],
        *,
        campaign_session: SessionRecord | None = None,
        handoff_id: str | None = None,
    ) -> None:
        participants = tuple(participants)
        initial_state = tuple(initial_state)
        legacy_participants_are_admitted = self.legacy_participants_are_admitted(
            package_lock,
            participants,
        )
        if (campaign_session is None) != (handoff_id is None):
            raise ValueError("Campaign session and handoff must be committed together")
        locked_package_contexts = {
            (entry.package_id, entry.version) for entry in package_lock.entries
        }
        if any(
            (participant.package_id, participant.package_version) not in locked_package_contexts
            for participant in participants
        ):
            raise CommandRejected(
                "Participant package context is not in the campaign lock",
                code="participant_package_unlocked",
            )
        requires_state_ownership = self.package_lock_requires_state_ownership(package_lock)
        if any(not canonical_payload_is_safe(item.data) for item in participants) or any(
            not canonical_payload_is_safe({item.namespace: {item.key: item.value}})
            for item in initial_state
        ):
            raise CommandRejected(
                "Persistence rejected an unsafe campaign payload",
                code="unsafe_canonical_payload",
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if requires_state_ownership:
                self._ensure_campaign_schema(connection)
            has_f5 = (
                int(connection.execute("PRAGMA user_version").fetchone()[0])
                == _CAMPAIGN_SCHEMA_VERSION
            )
            if (
                has_f5
                and any(
                    participant.validation_status is not ParticipantValidationStatus.VALIDATED
                    for participant in participants
                )
                and not legacy_participants_are_admitted
            ):
                raise CommandRejected(
                    "New v5 participants require exact validated package and schema proof",
                    code="participant_exact_proof_required",
                )
            if has_f5 and any(
                not self._participant_adaptation_is_admitted(connection, participant)
                for participant in participants
            ):
                raise CommandRejected(
                    "Cross-schema participant reuse requires a durable adaptation operation",
                    code="participant_adaptation_required",
                )
            sqlite_packages.verify_published_lock(connection, package_lock)
            lock_hash = _lock_representation(package_lock)
            connection.execute(
                "INSERT INTO campaigns "
                "(campaign_id, display_name, lifecycle, writer, revision, lock_hash, "
                "current_lock_id) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    campaign.campaign_id,
                    campaign.display_name,
                    campaign.lifecycle.value,
                    campaign.writer.value,
                    campaign.revision,
                    lock_hash,
                ),
            )
            if has_f5:
                connection.execute(
                    "INSERT INTO campaign_lifecycle_state "
                    "(campaign_id, retained_active_writer) VALUES (?, ?)",
                    (
                        campaign.campaign_id,
                        campaign.writer.value
                        if campaign.lifecycle is LifecycleState.ACTIVE
                        else None,
                    ),
                )
            lock_record = sqlite_packages.insert_campaign_lock(
                connection,
                campaign_id=campaign.campaign_id,
                package_lock=package_lock,
                revision=campaign.revision,
                predecessor_lock_id=None,
            )
            connection.execute(
                "UPDATE campaigns SET current_lock_id = ? WHERE campaign_id = ?",
                (lock_record.lock_id, campaign.campaign_id),
            )
            for entry in package_lock.entries:
                connection.execute(
                    "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        campaign.campaign_id,
                        entry.package_id,
                        entry.version,
                        entry.sha256,
                        entry.role.value,
                        entry.load_order,
                    ),
                )
            for participant in participants:
                if has_f5:
                    connection.execute(
                        "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            campaign.campaign_id,
                            participant.participant_id,
                            participant.kind,
                            participant.package_id,
                            participant.package_version,
                            _dump(dict(participant.data)),
                            participant.package_sha256,
                            participant.schema_id,
                            participant.schema_sha256,
                            participant.validation_status.value,
                            participant.adaptation_id,
                        ),
                    )
                    if participant.validation_status is not ParticipantValidationStatus.VALIDATED:
                        connection.execute(
                            "INSERT INTO participant_quarantines "
                            "(campaign_id, participant_id, reason_code) "
                            "VALUES (?, ?, 'exact_schema_proof_unavailable')",
                            (campaign.campaign_id, participant.participant_id),
                        )
                else:
                    connection.execute(
                        "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            campaign.campaign_id,
                            participant.participant_id,
                            participant.kind,
                            participant.package_id,
                            participant.package_version,
                            _dump(dict(participant.data)),
                        ),
                    )
            if has_f5:
                initial_owners = {
                    state_value.namespace: sqlite_state_ownership.initial_owner(
                        package_lock,
                        state_value.namespace,
                    )
                    for state_value in initial_state
                }
                for namespace, owner_package_id in sorted(initial_owners.items()):
                    connection.execute(
                        "INSERT INTO campaign_state_namespace_owners "
                        "(campaign_id, namespace, owner_package_id) VALUES (?, ?, ?)",
                        (campaign.campaign_id, namespace, owner_package_id),
                    )
            for state_value in initial_state:
                connection.execute(
                    "INSERT INTO campaign_state VALUES (?, ?, ?, ?)",
                    (
                        campaign.campaign_id,
                        state_value.namespace,
                        state_value.key,
                        _dump(state_value.value),
                    ),
                )
            connection.execute(
                "INSERT INTO turns VALUES (?, ?, ?, ?)",
                (campaign.campaign_id, turn_id, TurnStatus.OPEN.value, campaign.revision),
            )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id) VALUES (?, ?, NULL, NULL)",
                (campaign.campaign_id, campaign.revision),
            )
            if campaign_session is not None and handoff_id is not None:
                raise CommandRejected(
                    "F4 handoff applies only to an existing campaign",
                    code="existing_campaign_handoff_only",
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def idempotency_hit(self, campaign_id: str, key: str) -> IdempotencyHit | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_hash, action_id, status, result_json, resulting_revision "
                "FROM idempotency_records WHERE campaign_id = ? AND idempotency_key = ?",
                (campaign_id, key),
            ).fetchone()
            if row is None:
                return None
            return IdempotencyHit(
                payload_hash=str(row["payload_hash"]),
                receipt=ActionReceipt(
                    campaign_id=campaign_id,
                    action_id=str(row["action_id"]),
                    status=ActionStatus(str(row["status"])),
                    result=_load_payload(str(row["result_json"])),
                    resulting_revision=int(row["resulting_revision"]),
                ),
            )
        finally:
            connection.close()

    @staticmethod
    def _pending_from_row(row: sqlite3.Row) -> PendingMechanicsAction:
        descriptor_sha256 = hashlib.sha256(str(row["descriptor_json"]).encode("utf-8")).hexdigest()
        if descriptor_sha256 != str(row["descriptor_sha256"]):
            raise SchemaIdentityError("Stored mechanics choice descriptor hash differs")
        descriptor = _load_payload(str(row["descriptor_json"]))
        if "player_action_origin" in descriptor and not isinstance(
            descriptor["player_action_origin"], Mapping
        ):
            raise SchemaIdentityError("Stored player action origin is invalid")
        try:
            choice = choice_from_payload({key: descriptor[key]
                                          for key in ("choice_id", "prompt", "options")})
            prior_answers = answers_from_payload(descriptor.get("prior_answers"))
            if any(answer.session_id != row["initiating_session_id"] for answer in prior_answers):
                raise ValueError("Saved choice answer session differs")
            if choice.choice_id != row["choice_id"]:
                raise ValueError("Saved choice identity differs")
        except (KeyError, TypeError, ValueError) as error:
            raise SchemaIdentityError("Stored mechanics choice descriptor is invalid") from error
        return PendingMechanicsAction(
            campaign_id=str(row["campaign_id"]),
            action_id=str(row["action_id"]),
            turn_id=str(row["turn_id"]),
            basis_revision=int(row["basis_revision"]),
            writer=WriterRole(str(row["writer"])),
            package_id=str(row["package_id"]),
            package_version=str(row["package_version"]),
            package_sha256=str(row["package_sha256"]),
            lock_id=str(row["lock_id"]),
            tool_name=str(row["tool_name"]),
            parameters=_load_payload(str(row["parameters_json"])),
            payload_hash=str(row["payload_hash"]),
            initiating_session_id=(
                None if row["initiating_session_id"] is None else str(row["initiating_session_id"])
            ),
            choice=choice,
            descriptor_sha256=str(row["descriptor_sha256"]),
            prior_answers=prior_answers,
            player_action_origin=(
                owned_payload(descriptor["player_action_origin"])
                if isinstance(descriptor.get("player_action_origin"), Mapping)
                else None
            ),
        )

    def pending_mechanics_action(self, campaign_id: str) -> PendingMechanicsAction | None:
        connection = self._connect_read_only()
        try:
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'pending_mechanics_actions'"
                ).fetchone()
                is None
            ):
                return None
            row = connection.execute(
                "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            return None if row is None else self._pending_from_row(row)
        finally:
            connection.close()

    def pending_mechanics_hit(
        self, campaign_id: str, idempotency_key: str
    ) -> PendingMechanicsHit | None:
        connection = self._connect_read_only()
        try:
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'pending_mechanics_action_aliases'"
                ).fetchone()
                is None
            ):
                return None
            row = connection.execute(
                "SELECT pending.*, alias.payload_hash AS alias_payload_hash "
                "FROM pending_mechanics_action_aliases AS alias "
                "JOIN pending_mechanics_actions AS pending "
                "ON pending.campaign_id = alias.campaign_id "
                "AND pending.action_id = alias.action_id "
                "WHERE alias.campaign_id = ? AND alias.idempotency_key = ?",
                (campaign_id, idempotency_key),
            ).fetchone()
            if row is None:
                return None
            return PendingMechanicsHit(
                payload_hash=str(row["alias_payload_hash"]),
                pending=self._pending_from_row(row),
            )
        finally:
            connection.close()

    def mechanics_choice_resolution_receipt(
        self,
        *,
        campaign_id: str,
        resolution_id: str,
        action_id: str,
        choice_id: str,
        option_id: str,
        session_id: str | None,
        choice_sequence: int = 0,
    ) -> ActionReceipt | AwaitingChoiceReceipt | None:
        connection = self._connect_read_only()
        try:
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'mechanics_choice_resolutions'"
                ).fetchone()
                is None
            ):
                return None
            row = connection.execute(
                "SELECT resolution.*, action.status, action.result_json "
                "FROM mechanics_choice_resolutions AS resolution "
                "JOIN actions AS action ON action.campaign_id = resolution.campaign_id "
                "AND action.action_id = resolution.action_id "
                "WHERE resolution.campaign_id = ? AND resolution.resolution_id = ?",
                (campaign_id, resolution_id),
            ).fetchone()
            if row is None:
                pending_row = connection.execute(
                    "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()
                if pending_row is not None:
                    pending = self._pending_from_row(pending_row)
                    for index, answer in enumerate(pending.prior_answers):
                        if answer.resolution_id != resolution_id:
                            continue
                        if (index != choice_sequence or pending.action_id != action_id
                                or answer.request.choice_id != choice_id
                                or answer.option_id != option_id
                                or answer.session_id != session_id):
                            raise IdempotencyConflict("Mechanics choice resolution payload differs")
                        following = (pending.prior_answers[index + 1].request
                                     if index + 1 < len(pending.prior_answers) else pending.choice)
                        return AwaitingChoiceReceipt(campaign_id, action_id,
                                                     pending.basis_revision, pending.turn_id,
                                                     following, index + 1)
                for completed in connection.execute(
                    "SELECT action_id, status, result_json, resulting_revision FROM actions "
                    "WHERE campaign_id = ? AND "
                    "json_type(result_json, '$.mechanics_choice_answers') = 'array'",
                    (campaign_id,),
                ):
                    result = _load_payload(str(completed["result_json"]))
                    for index, answer in enumerate(answers_from_payload(
                        result["mechanics_choice_answers"]
                    )):
                        if answer.resolution_id != resolution_id:
                            continue
                        if (index != choice_sequence or str(completed["action_id"]) != action_id
                                or answer.request.choice_id != choice_id
                                or answer.option_id != option_id
                                or answer.session_id != session_id):
                            raise IdempotencyConflict("Mechanics choice resolution payload differs")
                        return ActionReceipt(campaign_id, action_id,
                                             ActionStatus(str(completed["status"])), result,
                                             int(completed["resulting_revision"]))
                return None
            resolution_payload = owned_payload(
                {
                    "campaign_id": campaign_id,
                    "action_id": action_id,
                    "resolution_id": resolution_id,
                    "choice_id": choice_id,
                    "option_id": option_id,
                    "session_id": session_id,
                    **({"choice_sequence": choice_sequence} if choice_sequence else {}),
                }
            )
            if (
                str(row["action_id"]) != action_id
                or str(row["choice_id"]) != choice_id
                or str(row["option_id"]) != option_id
                or (None if row["session_id"] is None else str(row["session_id"])) != session_id
                or str(row["resolution_sha256"])
                != hashlib.sha256(_dump(resolution_payload).encode("utf-8")).hexdigest()
            ):
                raise IdempotencyConflict(
                    "Mechanics choice resolution identity payload does not match"
                )
            return ActionReceipt(
                campaign_id=campaign_id,
                action_id=action_id,
                status=ActionStatus(str(row["status"])),
                result=_load_payload(str(row["result_json"])),
                resulting_revision=int(row["resulting_revision"]),
            )
        finally:
            connection.close()

    def reserve_mechanics_choice(
        self,
        *,
        campaign_id: str,
        action_id: str,
        idempotency_key: str,
        payload_hash: str,
        expected_revision: int,
        writer: WriterRole,
        turn_id: str,
        package_id: str,
        package_version: str,
        package_sha256: str,
        tool_name: str,
        parameters: SafePayload,
        initiating_session_id: str | None,
        choice: MechanicsChoiceRequest,
        player_action_origin: SafePayload | None = None,
    ) -> PendingMechanicsAction:
        descriptor = owned_payload(
            {
                "choice_id": choice.choice_id,
                "prompt": choice.prompt,
                "options": [
                    {"option_id": option.option_id, "label": option.label}
                    for option in choice.options
                ],
            }
        )
        if player_action_origin is not None:
            descriptor = owned_payload(
                {**descriptor, "player_action_origin": player_action_origin}
            )
        descriptor_json = _dump(descriptor)
        descriptor_sha256 = hashlib.sha256(descriptor_json.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            existing_alias = connection.execute(
                "SELECT payload_hash, action_id FROM pending_mechanics_action_aliases "
                "WHERE campaign_id = ? AND idempotency_key = ?",
                (campaign_id, idempotency_key),
            ).fetchone()
            if existing_alias is not None:
                if (
                    str(existing_alias["payload_hash"]) != payload_hash
                    or str(existing_alias["action_id"]) != action_id
                ):
                    raise IdempotencyConflict("Idempotency key payload does not match")
                row = connection.execute(
                    "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ? "
                    "AND action_id = ?",
                    (campaign_id, action_id),
                ).fetchone()
                if row is None:
                    raise SchemaIdentityError("Stored mechanics choice alias is orphaned")
                connection.rollback()
                return self._pending_from_row(row)
            campaign = connection.execute(
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns "
                "WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            turn = connection.execute(
                "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                (campaign_id, turn_id),
            ).fetchone()
            if (
                campaign is None
                or str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value
                or str(campaign["writer"]) != writer.value
                or int(campaign["revision"]) != expected_revision
                or turn is None
                or str(turn["status"]) != TurnStatus.OPEN.value
            ):
                raise StaleRevision("Mechanics choice basis no longer admits reservation")
            has_pending_table = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'pending_mechanics_actions'"
                ).fetchone()
                is not None
            )
            if (
                has_pending_table
                and connection.execute(
                    "SELECT 1 FROM pending_mechanics_actions WHERE campaign_id = ?", (campaign_id,)
                ).fetchone()
                is not None
            ):
                raise CommandRejected(
                    "Campaign is awaiting a mechanics choice",
                    code="mechanics_choice_pending",
                )
            if (
                connection.execute(
                    "SELECT 1 FROM actions WHERE campaign_id = ? AND action_id = ?",
                    (campaign_id, action_id),
                ).fetchone()
                is not None
            ):
                raise IdempotencyConflict("Action identity is already terminal")
            connection.execute(
                "INSERT INTO pending_mechanics_actions VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    action_id,
                    turn_id,
                    expected_revision,
                    writer.value,
                    package_id,
                    package_version,
                    package_sha256,
                    str(campaign["current_lock_id"]),
                    tool_name,
                    _dump(parameters),
                    payload_hash,
                    initiating_session_id,
                    choice.choice_id,
                    descriptor_json,
                    descriptor_sha256,
                ),
            )
            connection.execute(
                "INSERT INTO pending_mechanics_action_aliases VALUES (?, ?, ?, ?)",
                (campaign_id, idempotency_key, action_id, payload_hash),
            )
            connection.commit()
            return self.pending_mechanics_action(campaign_id)  # type: ignore[return-value]
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def action_hit(self, campaign_id: str, action_id: str) -> IdempotencyHit | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_hash, action_id, status, result_json, resulting_revision "
                "FROM actions WHERE campaign_id = ? AND action_id = ?",
                (campaign_id, action_id),
            ).fetchone()
            if row is None:
                return None
            return IdempotencyHit(
                payload_hash=str(row["payload_hash"]),
                receipt=ActionReceipt(
                    campaign_id=campaign_id,
                    action_id=str(row["action_id"]),
                    status=ActionStatus(str(row["status"])),
                    result=_load_payload(str(row["result_json"])),
                    resulting_revision=int(row["resulting_revision"]),
                ),
            )
        finally:
            connection.close()

    def bind_transport_alias(
        self,
        *,
        campaign_id: str,
        action_id: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> ActionReceipt:
        """Atomically reserve a new transport key for a durable action replay."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_session.require_active_campaign(connection, campaign_id)
            existing = connection.execute(
                "SELECT payload_hash, action_id, status, result_json, resulting_revision "
                "FROM idempotency_records WHERE campaign_id = ? AND idempotency_key = ?",
                (campaign_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["payload_hash"]) != payload_hash
                    or str(existing["action_id"]) != action_id
                ):
                    raise IdempotencyConflict("Idempotency key payload does not match")
                connection.rollback()
                return ActionReceipt(
                    campaign_id=campaign_id,
                    action_id=action_id,
                    status=ActionStatus(str(existing["status"])),
                    result=_load_payload(str(existing["result_json"])),
                    resulting_revision=int(existing["resulting_revision"]),
                )
            action = connection.execute(
                "SELECT payload_hash, status, result_json, resulting_revision FROM actions "
                "WHERE campaign_id = ? AND action_id = ?",
                (campaign_id, action_id),
            ).fetchone()
            if action is None or str(action["payload_hash"]) != payload_hash:
                raise IdempotencyConflict("Action identity payload does not match")
            connection.execute(
                "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    idempotency_key,
                    payload_hash,
                    action_id,
                    str(action["status"]),
                    str(action["result_json"]),
                    int(action["resulting_revision"]),
                ),
            )
            connection.commit()
            return ActionReceipt(
                campaign_id=campaign_id,
                action_id=action_id,
                status=ActionStatus(str(action["status"])),
                result=_load_payload(str(action["result_json"])),
                resulting_revision=int(action["resulting_revision"]),
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def action_basis(self, campaign_id: str, turn_id: str) -> ActionBasis:
        connection = self._connect()
        try:
            campaign_row = connection.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if campaign_row is None:
                raise CommandRejected("Campaign is unavailable")
            turn_row = connection.execute(
                "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                (campaign_id, turn_id),
            ).fetchone()
            if turn_row is None:
                raise CommandRejected("TURN is unavailable")
            state = self._read_state(connection, campaign_id)
            has_f5 = (
                int(connection.execute("PRAGMA user_version").fetchone()[0])
                == _CAMPAIGN_SCHEMA_VERSION
            )
            return ActionBasis(
                campaign=self._campaign_record(campaign_row),
                turn_status=TurnStatus(str(turn_row["status"])),
                state=state,
                package_lock=self._read_lock(connection, campaign_id),
                participants=tuple(
                    Participant(
                        participant_id=str(row["participant_id"]),
                        kind=str(row["kind"]),
                        package_id=str(row["package_id"]),
                        package_version=str(row["package_version"]),
                        data=_load_payload(str(row["data_json"])),
                        package_sha256=(
                            None
                            if not has_f5 or row["package_sha256"] is None
                            else str(row["package_sha256"])
                        ),
                        schema_id=(
                            None
                            if not has_f5 or row["schema_id"] is None
                            else str(row["schema_id"])
                        ),
                        schema_sha256=(
                            None
                            if not has_f5 or row["schema_sha256"] is None
                            else str(row["schema_sha256"])
                        ),
                        validation_status=(
                            ParticipantValidationStatus.QUARANTINED
                            if not has_f5
                            else ParticipantValidationStatus(str(row["validation_status"]))
                        ),
                        adaptation_id=(
                            None
                            if not has_f5 or row["adaptation_id"] is None
                            else str(row["adaptation_id"])
                        ),
                    )
                    for row in connection.execute(
                        "SELECT * FROM participants WHERE campaign_id = ? ORDER BY participant_id",
                        (campaign_id,),
                    )
                ),
            )
        finally:
            connection.close()

    def mechanic_mutations_are_owned(
        self,
        *,
        campaign_id: str,
        package_id: str,
        package_version: str,
        package_sha256: str,
        outcome: MechanicOutcome,
    ) -> bool:
        """Preflight captured mutations without granting mechanics ownership choice."""

        if outcome.mutations or outcome.participant_mutations:
            migration_connection = self._connect()
            try:
                migration_connection.execute("BEGIN IMMEDIATE")
                self._ensure_campaign_schema(migration_connection)
                migration_connection.commit()
            except BaseException:
                migration_connection.rollback()
                raise
            finally:
                migration_connection.close()
        connection = self._connect_read_only()
        try:
            for mutation in outcome.mutations:
                sqlite_state_ownership.owner_for_mutation(
                    connection,
                    campaign_id=campaign_id,
                    actor_package_id=package_id,
                    actor_package_version=package_version,
                    actor_package_sha256=package_sha256,
                    namespace=mutation.namespace,
                )
            self._validate_participant_mutations(
                connection, campaign_id, package_id, package_version, package_sha256, outcome,
            )
            return True
        except CommandRejected:
            return False
        finally:
            connection.close()

    @staticmethod
    def _validate_participant_mutations(
        connection: sqlite3.Connection, campaign_id: str, package_id: str,
        package_version: str, package_sha256: str, outcome: MechanicOutcome,
    ) -> None:
        """Require each live replacement to retain its owning exact schema."""
        for mutation in outcome.participant_mutations:
            row = connection.execute(
                "SELECT p.*, d.schema_json FROM participants p "
                "JOIN package_participant_artifacts d ON d.package_id = p.package_id "
                "AND d.version = p.package_version AND d.content_sha256 = p.package_sha256 "
                "AND d.kind = p.kind AND d.schema_id = p.schema_id "
                "AND d.schema_sha256 = p.schema_sha256 "
                "WHERE p.campaign_id = ? AND p.participant_id = ?",
                (campaign_id, mutation.participant_id),
            ).fetchone()
            if row is None or (
                row["package_id"], row["package_version"], row["package_sha256"],
                row["validation_status"],
            ) != (package_id, package_version, package_sha256, "validated"):
                raise CommandRejected(
                    "Live character updates require the owning exact Ruleset and schema",
                    code="participant_mutation_unowned",
                )
            validate_json_value(
                mutation.data, _load_payload(str(row["schema_json"])),
                label=f"Participant {mutation.participant_id}",
            )

    def commit_action(
        self,
        *,
        campaign_id: str,
        action_id: str,
        idempotency_key: str,
        payload_hash: str,
        expected_revision: int,
        writer: WriterRole,
        turn_id: str,
        package_id: str,
        package_version: str,
        package_sha256: str,
        tool_name: str,
        parameters: SafePayload,
        outcome: MechanicOutcome,
        status: ActionStatus,
        choice_resolution_id: str | None = None,
        choice_resolution: MechanicsChoiceResolution | None = None,
        choice_resolution_session_id: str | None = None,
        campaign_file_commands: Sequence[CampaignContinuityCommand] = (),
    ) -> ActionReceipt:
        resolving_choice = choice_resolution_id is not None or choice_resolution is not None
        if resolving_choice != (choice_resolution_id is not None and choice_resolution is not None):
            raise ValueError("Choice resolution identity and payload must be provided together")
        if not canonical_payload_is_safe(parameters) or not mechanic_outcome_is_safe(outcome):
            raise CommandRejected(
                "Persistence rejected an unsafe canonical payload",
                code="unsafe_canonical_payload",
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if outcome.mutations or outcome.participant_mutations or resolving_choice:
                self._ensure_campaign_schema(connection)
            pending_aliases: tuple[str, ...] = ()
            if resolving_choice:
                assert choice_resolution_id is not None
                assert choice_resolution is not None
                prior_resolution = connection.execute(
                    "SELECT action_id, choice_id, option_id, resolution_sha256, "
                    "descriptor_sha256, session_id, resulting_revision "
                    "FROM mechanics_choice_resolutions WHERE campaign_id = ? "
                    "AND resolution_id = ?",
                    (campaign_id, choice_resolution_id),
                ).fetchone()
                resolution_payload = owned_payload(
                    {
                        "campaign_id": campaign_id,
                        "action_id": action_id,
                        "resolution_id": choice_resolution_id,
                        "choice_id": choice_resolution.choice_id,
                        "option_id": choice_resolution.option_id,
                        "session_id": choice_resolution_session_id,
                        **({"choice_sequence": len(choice_resolution.prior_answers)}
                           if choice_resolution.prior_answers else {}),
                    }
                )
                resolution_sha256 = hashlib.sha256(
                    _dump(resolution_payload).encode("utf-8")
                ).hexdigest()
                if prior_resolution is not None:
                    if (
                        str(prior_resolution["action_id"]) != action_id
                        or str(prior_resolution["choice_id"]) != choice_resolution.choice_id
                        or str(prior_resolution["option_id"]) != choice_resolution.option_id
                        or str(prior_resolution["resolution_sha256"]) != resolution_sha256
                        or str(prior_resolution["descriptor_sha256"])
                        != hashlib.sha256(
                            _dump(
                                owned_payload(
                                    {
                                        "choice_id": choice_resolution.expected_request.choice_id,
                                        "prompt": choice_resolution.expected_request.prompt,
                                        "options": [
                                            {
                                                "option_id": option.option_id,
                                                "label": option.label,
                                            }
                                            for option in choice_resolution.expected_request.options
                                        ],
                                    }
                                )
                            ).encode("utf-8")
                        ).hexdigest()
                        or (
                            None
                            if prior_resolution["session_id"] is None
                            else str(prior_resolution["session_id"])
                        )
                        != choice_resolution_session_id
                    ):
                        raise IdempotencyConflict(
                            "Mechanics choice resolution identity payload does not match"
                        )
                    action = connection.execute(
                        "SELECT status, result_json, resulting_revision FROM actions "
                        "WHERE campaign_id = ? AND action_id = ?",
                        (campaign_id, action_id),
                    ).fetchone()
                    if action is None:
                        raise SchemaIdentityError("Stored mechanics choice resolution is orphaned")
                    connection.rollback()
                    return ActionReceipt(
                        campaign_id=campaign_id,
                        action_id=action_id,
                        status=ActionStatus(str(action["status"])),
                        result=_load_payload(str(action["result_json"])),
                        resulting_revision=int(action["resulting_revision"]),
                    )
                pending = connection.execute(
                    "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ? "
                    "AND action_id = ?",
                    (campaign_id, action_id),
                ).fetchone()
                if pending is None:
                    raise CommandRejected(
                        "Mechanics choice reservation is unavailable",
                        code="mechanics_choice_unavailable",
                    )
                stored = self._pending_from_row(pending)
                if (
                    stored.basis_revision != expected_revision
                    or stored.writer is not writer
                    or stored.turn_id != turn_id
                    or stored.package_id != package_id
                    or stored.package_version != package_version
                    or stored.package_sha256 != package_sha256
                    or stored.tool_name != tool_name
                    or stored.parameters != parameters
                    or stored.payload_hash != payload_hash
                    or stored.choice != choice_resolution.expected_request
                    or stored.prior_answers != choice_resolution.prior_answers
                    or stored.choice.choice_id != choice_resolution.choice_id
                    or choice_resolution.option_id
                    not in {option.option_id for option in stored.choice.options}
                    or stored.initiating_session_id != choice_resolution_session_id
                ):
                    raise IdempotencyConflict(
                        "Mechanics choice resolution does not match its reservation"
                    )
                pending_aliases = tuple(
                    str(row["idempotency_key"])
                    for row in connection.execute(
                        "SELECT idempotency_key FROM pending_mechanics_action_aliases "
                        "WHERE campaign_id = ? AND action_id = ? ORDER BY idempotency_key",
                        (campaign_id, action_id),
                    )
                )
                if not pending_aliases:
                    raise SchemaIdentityError(
                        "Stored mechanics choice reservation has no transport identity"
                    )
                idempotency_key = pending_aliases[0]
                if stored.prior_answers:
                    outcome = replace(outcome, result=owned_payload({
                        **outcome.result,
                        "mechanics_choice_answers": [
                            answer_payload(a) for a in stored.prior_answers
                        ],
                    }))
            existing = connection.execute(
                "SELECT payload_hash, action_id, status, result_json, resulting_revision "
                "FROM idempotency_records WHERE campaign_id = ? AND idempotency_key = ?",
                (campaign_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise IdempotencyConflict("Idempotency key payload does not match")
                connection.rollback()
                return ActionReceipt(
                    campaign_id=campaign_id,
                    action_id=str(existing["action_id"]),
                    status=ActionStatus(str(existing["status"])),
                    result=_load_payload(str(existing["result_json"])),
                    resulting_revision=int(existing["resulting_revision"]),
                )
            campaign_row = connection.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if campaign_row is None:
                raise CommandRejected("Campaign is unavailable")
            campaign = self._campaign_record(campaign_row)
            if campaign.lifecycle is not LifecycleState.ACTIVE:
                raise CommandRejected(
                    "Campaign lifecycle blocks mutation", code="lifecycle_inactive"
                )
            if campaign.writer is not writer:
                raise WrongWriter("Command writer is not authoritative")
            if campaign.revision != expected_revision:
                raise StaleRevision("Expected campaign revision is stale")
            turn = connection.execute(
                "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                (campaign_id, turn_id),
            ).fetchone()
            if turn is None or TurnStatus(str(turn["status"])) is not TurnStatus.OPEN:
                raise CommandRejected("TURN does not admit actions")

            for mutation in outcome.mutations:
                owner_package_id, is_new_namespace = sqlite_state_ownership.owner_for_mutation(
                    connection,
                    campaign_id=campaign_id,
                    actor_package_id=package_id,
                    actor_package_version=package_version,
                    actor_package_sha256=package_sha256,
                    namespace=mutation.namespace,
                )
                if is_new_namespace:
                    connection.execute(
                        "INSERT INTO campaign_state_namespace_owners "
                        "(campaign_id, namespace, owner_package_id) VALUES (?, ?, ?)",
                        (campaign_id, mutation.namespace, owner_package_id),
                    )

            self._validate_participant_mutations(
                connection, campaign_id, package_id, package_version, package_sha256, outcome,
            )
            resulting_revision = expected_revision + 1
            connection.execute(
                "INSERT INTO actions "
                "(campaign_id, action_id, turn_id, expected_revision, resulting_revision, writer, "
                "package_id, package_version, package_sha256, tool_name, status, payload_json, "
                "payload_hash, result_json, lock_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    action_id,
                    turn_id,
                    expected_revision,
                    resulting_revision,
                    writer.value,
                    package_id,
                    package_version,
                    package_sha256,
                    tool_name,
                    status.value,
                    _dump(dict(parameters)),
                    payload_hash,
                    _dump(dict(outcome.result)),
                    str(campaign_row["current_lock_id"]),
                ),
            )
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM canonical_events WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()[0]
            )

            def append(event_type: CanonicalEventType, payload: SafePayload) -> None:
                nonlocal sequence
                sequence += 1
                visibility = (
                    EventVisibility.PLAYER_SAFE
                    if event_type
                    in (
                        CanonicalEventType.TOOL_CALL,
                        CanonicalEventType.TOOL_RESULT,
                    )
                    else EventVisibility.INTERNAL_SAFE
                )
                connection.execute(
                    "INSERT INTO canonical_events "
                    "(campaign_id, sequence, event_type, turn_id, action_id, "
                    "safe_payload_json, visibility, attempt_id, attempt_ordinal) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (
                        campaign_id,
                        sequence,
                        event_type.value,
                        turn_id,
                        action_id,
                        _dump(dict(payload)),
                        visibility.value,
                    ),
                )

            append(
                CanonicalEventType.ACTION_DECLARED,
                {"action_id": action_id, "writer": writer.value},
            )
            append(
                CanonicalEventType.TOOL_CALL,
                {
                    "package_id": package_id,
                    "package_version": package_version,
                    "package_sha256": package_sha256,
                    "tool_name": tool_name,
                    "parameters": dict(parameters),
                },
            )
            append(
                CanonicalEventType.TOOL_RESULT,
                {"status": status.value, "result": dict(outcome.result)},
            )
            for ordinal, mutation in enumerate(outcome.mutations):
                previous_row = connection.execute(
                    "SELECT value_json FROM campaign_state "
                    "WHERE campaign_id = ? AND namespace = ? AND state_key = ?",
                    (campaign_id, mutation.namespace, mutation.key),
                ).fetchone()
                before: JsonValue = (
                    None if previous_row is None else _load_json(str(previous_row["value_json"]))
                )
                connection.execute(
                    "INSERT INTO campaign_state VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(campaign_id, namespace, state_key) "
                    "DO UPDATE SET value_json = excluded.value_json",
                    (campaign_id, mutation.namespace, mutation.key, _dump(mutation.value)),
                )
                connection.execute(
                    "INSERT INTO mutations VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        campaign_id,
                        action_id,
                        ordinal,
                        mutation.namespace,
                        mutation.key,
                        _dump(before),
                        _dump(mutation.value),
                    ),
                )
                append(
                    CanonicalEventType.STATE_MUTATION,
                    {"namespace": mutation.namespace, "key": mutation.key},
                )
            participant_consequences = []
            for mutation in outcome.participant_mutations:
                previous = connection.execute(
                    "SELECT data_json FROM participants "
                    "WHERE campaign_id = ? AND participant_id = ?",
                    (campaign_id, mutation.participant_id),
                ).fetchone()
                participant_consequences.append({
                    "kind": "participant_updated", "participant_id": mutation.participant_id,
                    "before": _load_payload(str(previous["data_json"])),
                    "after": mutation.data,
                })
                connection.execute(
                    "UPDATE participants SET data_json = ? "
                    "WHERE campaign_id = ? AND participant_id = ?",
                    (_dump(mutation.data), campaign_id, mutation.participant_id),
                )
            all_consequences = (*outcome.consequences, *participant_consequences)
            for ordinal, consequence in enumerate(all_consequences):
                connection.execute(
                    "INSERT INTO consequences VALUES (?, ?, ?, ?)",
                    (campaign_id, action_id, ordinal, _dump(dict(consequence))),
                )
                append(CanonicalEventType.CONSEQUENCE, consequence)
            terminal_event = (
                CanonicalEventType.ACTION_COMMITTED
                if status is ActionStatus.COMMITTED
                else CanonicalEventType.ACTION_FAILED
            )
            append(
                terminal_event,
                {"status": status.value, "resulting_revision": resulting_revision},
            )
            updated = connection.execute(
                "UPDATE campaigns SET revision = ? "
                "WHERE campaign_id = ? AND revision = ? AND writer = ? AND lifecycle = 'active'",
                (resulting_revision, campaign_id, expected_revision, writer.value),
            )
            if updated.rowcount != 1:
                raise StaleRevision("Campaign revision fence rejected the action")
            connection.execute(
                "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    idempotency_key,
                    payload_hash,
                    action_id,
                    status.value,
                    _dump(dict(outcome.result)),
                    resulting_revision,
                ),
            )
            if resolving_choice:
                assert choice_resolution_id is not None
                assert choice_resolution is not None
                for alias in pending_aliases[1:]:
                    connection.execute(
                        "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            campaign_id,
                            alias,
                            payload_hash,
                            action_id,
                            status.value,
                            _dump(dict(outcome.result)),
                            resulting_revision,
                        ),
                    )
                connection.execute(
                    "INSERT INTO mechanics_choice_resolutions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        campaign_id,
                        choice_resolution_id,
                        action_id,
                        choice_resolution.choice_id,
                        choice_resolution.option_id,
                        resolution_sha256,
                        hashlib.sha256(
                            _dump(
                                owned_payload(
                                    {
                                        "choice_id": choice_resolution.expected_request.choice_id,
                                        "prompt": choice_resolution.expected_request.prompt,
                                        "options": [
                                            {
                                                "option_id": option.option_id,
                                                "label": option.label,
                                            }
                                            for option in choice_resolution.expected_request.options
                                        ],
                                    }
                                )
                            ).encode("utf-8")
                        ).hexdigest(),
                        choice_resolution_session_id,
                        resulting_revision,
                    ),
                )
                connection.execute(
                    "DELETE FROM pending_mechanics_actions WHERE campaign_id = ? AND action_id = ?",
                    (campaign_id, action_id),
                )
            connection.execute(
                "INSERT INTO campaign_revisions "
                "(campaign_id, revision, action_id, activation_id) VALUES (?, ?, ?, NULL)",
                (campaign_id, resulting_revision, action_id),
            )
            if campaign_file_commands:
                if status is not ActionStatus.COMMITTED or any(
                    item.campaign_id != campaign_id
                    or item.turn_id != turn_id
                    or item.expected_revision != resulting_revision + index
                    for index, item in enumerate(campaign_file_commands)
                ):
                    raise CommandRejected("Campaign File mechanics batch is inconsistent")
                self.commit_campaign_continuity_batch(
                    campaign_file_commands, _connection=connection
                )
            self._before_commit()
            connection.commit()
            return ActionReceipt(
                campaign_id=campaign_id,
                action_id=action_id,
                status=status,
                result=outcome.result,
                resulting_revision=resulting_revision,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def append_narration_segment(
        self,
        *,
        campaign_id: str,
        turn_id: str,
        attempt_id: str,
        ordinal: int,
        channel: ProseChannel,
        text: str,
        response_blocks: Sequence[SafePayload] | None = None,
    ) -> CanonicalEvent:
        """Idempotently append one complete player-safe prose segment."""

        for value, label in (
            (campaign_id, "Campaign identity"),
            (turn_id, "TURN identity"),
            (attempt_id, "Narration attempt identity"),
        ):
            validate_authority_identity(value, label)
        if ordinal < 0:
            raise ValueError("Narration ordinal cannot be negative")
        if not isinstance(text, str) or not text:
            raise ValueError("Completed narration text cannot be empty")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            event = self._append_narration_in_connection(
                connection,
                campaign_id=campaign_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                ordinal=ordinal,
                channel=channel,
                text=text,
                response_blocks=response_blocks,
            )
            self._before_narration_commit()
            connection.commit()
            return event
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _append_narration_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        campaign_id: str,
        turn_id: str,
        attempt_id: str,
        ordinal: int,
        channel: ProseChannel,
        text: str,
        response_blocks: Sequence[SafePayload] | None = None,
    ) -> CanonicalEvent:
        sqlite_session.require_active_campaign(connection, campaign_id)
        existing = connection.execute(
            "SELECT * FROM canonical_events WHERE campaign_id = ? "
            "AND attempt_id = ? AND attempt_ordinal = ?",
            (campaign_id, attempt_id, ordinal),
        ).fetchone()
        event_type = (
            CanonicalEventType.THINK
            if channel is ProseChannel.SAFE_THINK
            else CanonicalEventType.TALK
        )
        admitted_blocks: tuple[SafePayload, ...] | None = None
        if response_blocks is not None:
            if event_type is not CanonicalEventType.TALK:
                raise CommandRejected("Response blocks are valid only for TALK")
            try:
                admitted_blocks = validate_response_blocks(response_blocks)
            except ValueError as error:
                raise CommandRejected("Response blocks are not player-safe") from error
            if response_blocks_text(admitted_blocks) != text:
                raise CommandRejected("Response block fallback text does not match")
        payload = owned_payload(
            {
                "text": text,
                **(
                    {"response_blocks": list(admitted_blocks)}
                    if admitted_blocks is not None
                    else {}
                ),
            }
        )
        if existing is not None:
            existing_event = self._event_from_row(existing)
            if (
                existing_event.event_type is not event_type
                or existing_event.turn_id != turn_id
                or existing_event.safe_payload != payload
            ):
                raise IdempotencyConflict("Narration segment identity payload does not match")
            return existing_event
        turn = connection.execute(
            "SELECT 1 FROM turns WHERE campaign_id = ? AND turn_id = ?",
            (campaign_id, turn_id),
        ).fetchone()
        if turn is None:
            raise CommandRejected("TURN is unavailable")
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM canonical_events WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO canonical_events "
            "(campaign_id, sequence, event_type, turn_id, action_id, "
            "safe_payload_json, visibility, attempt_id, attempt_ordinal) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                campaign_id,
                sequence,
                event_type.value,
                turn_id,
                _dump(payload),
                EventVisibility.PLAYER_SAFE.value,
                attempt_id,
                ordinal,
            ),
        )
        return CanonicalEvent(
            sequence=sequence,
            event_type=event_type,
            turn_id=turn_id,
            action_id=None,
            safe_payload=payload,
            visibility=EventVisibility.PLAYER_SAFE,
            attempt_id=attempt_id,
            attempt_ordinal=ordinal,
        )

    def record_interruption(
        self,
        *,
        campaign_id: str,
        interruption_id: str,
        turn_id: str,
        last_action_id: str | None,
        failed_attempt_id: str,
    ) -> NarrationInterruption:
        """Persist only safe retry metadata for one terminal interrupted attempt."""

        for value, label in (
            (campaign_id, "Campaign identity"),
            (interruption_id, "Interruption identity"),
            (turn_id, "TURN identity"),
            (failed_attempt_id, "Failed narration attempt identity"),
        ):
            validate_authority_identity(value, label)
        if last_action_id is not None:
            validate_authority_identity(last_action_id, "Last action identity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_session.require_active_campaign(connection, campaign_id)
            existing = connection.execute(
                "SELECT * FROM narration_interruptions WHERE campaign_id = ? "
                "AND failed_attempt_id = ?",
                (campaign_id, failed_attempt_id),
            ).fetchone()
            if existing is not None:
                record = self._interruption_from_row(existing)
                if (
                    record.interruption_id != interruption_id
                    or record.turn_id != turn_id
                    or record.last_action_id != last_action_id
                ):
                    raise IdempotencyConflict("Interruption identity payload does not match")
                connection.rollback()
                return record
            retry_source = connection.execute(
                "SELECT * FROM narration_interruptions WHERE campaign_id = ? "
                "AND resolving_retry_id = ? AND status = 'retryable'",
                (campaign_id, failed_attempt_id),
            ).fetchone()
            if retry_source is not None:
                source = self._interruption_from_row(retry_source)
                if source.turn_id != turn_id:
                    raise IdempotencyConflict("Retry interruption basis does not match")
                if source.last_action_id != last_action_id:
                    latest = connection.execute(
                        "SELECT turn_id, resulting_revision FROM actions "
                        "WHERE campaign_id = ? AND action_id = ?",
                        (campaign_id, last_action_id),
                    ).fetchone()
                    previous = connection.execute(
                        "SELECT resulting_revision FROM actions "
                        "WHERE campaign_id = ? AND action_id = ?",
                        (campaign_id, source.last_action_id),
                    ).fetchone()
                    if latest is None or latest["turn_id"] != turn_id or (
                        previous is not None
                        and latest["resulting_revision"] <= previous["resulting_revision"]
                    ):
                        raise IdempotencyConflict("Retry requires a later result in the same turn")
                connection.execute(
                    "UPDATE narration_interruptions SET failed_attempt_id = ?, last_action_id = ?, "
                    "resolving_retry_id = NULL WHERE campaign_id = ? AND interruption_id = ?",
                    (failed_attempt_id, last_action_id, campaign_id, source.interruption_id),
                )
                connection.commit()
                return NarrationInterruption(
                    campaign_id=campaign_id,
                    interruption_id=source.interruption_id,
                    turn_id=turn_id,
                    last_action_id=last_action_id,
                    failed_attempt_id=failed_attempt_id,
                    retryable=True,
                )
            connection.execute(
                "INSERT INTO narration_interruptions "
                "(campaign_id, interruption_id, turn_id, last_action_id, "
                "failed_attempt_id, status, resolving_retry_id, resolved_sequence) "
                "VALUES (?, ?, ?, ?, ?, 'retryable', NULL, NULL)",
                (
                    campaign_id,
                    interruption_id,
                    turn_id,
                    last_action_id,
                    failed_attempt_id,
                ),
            )
            connection.commit()
            return NarrationInterruption(
                campaign_id=campaign_id,
                interruption_id=interruption_id,
                turn_id=turn_id,
                last_action_id=last_action_id,
                failed_attempt_id=failed_attempt_id,
                retryable=True,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def interruption(self, campaign_id: str, interruption_id: str) -> NarrationInterruption:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM narration_interruptions "
                "WHERE campaign_id = ? AND interruption_id = ?",
                (campaign_id, interruption_id),
            ).fetchone()
            if row is None:
                raise CommandRejected("Narration interruption is unavailable")
            return self._interruption_from_row(row)
        finally:
            connection.close()

    def interruptions(self, campaign_id: str) -> tuple[NarrationInterruption, ...]:
        connection = self._connect()
        try:
            return tuple(
                self._interruption_from_row(row)
                for row in connection.execute(
                    "SELECT * FROM narration_interruptions WHERE campaign_id = ? ORDER BY rowid",
                    (campaign_id,),
                )
            )
        finally:
            connection.close()

    def redirect_interruption(
        self,
        *,
        session_id: str,
        message_id: str,
        content: SafePayload,
        interruption: NarrationInterruption,
        attempt_id: str,
    ) -> None:
        """Save replacement input and reserve its continuation in one transaction."""

        validate_authority_identity(attempt_id, "Continuation identity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            session = sqlite_session.read_session(connection, session_id)
            if session.campaign_id != interruption.campaign_id:
                raise CommandRejected("Interruption belongs to another campaign")
            row = connection.execute(
                "SELECT * FROM narration_interruptions WHERE campaign_id = ? "
                "AND interruption_id = ?",
                (interruption.campaign_id, interruption.interruption_id),
            ).fetchone()
            if row is None or self._interruption_from_row(row) != interruption:
                raise IdempotencyConflict("Interruption changed before new input was accepted")
            if not interruption.retryable:
                raise CommandRejected("Interruption has already been resolved")
            if connection.execute(
                "SELECT 1 FROM canonical_events WHERE campaign_id = ? AND attempt_id = ?",
                (interruption.campaign_id, attempt_id),
            ).fetchone() is not None:
                raise IdempotencyConflict("Continuation identity already has saved output")
            sqlite_session.append_message(
                connection, session_id=session_id, message_id=message_id,
                kind=SessionMessageKind.USER, content=content,
            )
            connection.execute(
                "UPDATE narration_interruptions SET resolving_retry_id = ? "
                "WHERE campaign_id = ? AND interruption_id = ?",
                (attempt_id, interruption.campaign_id, interruption.interruption_id),
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_retry(
        self, campaign_id: str, interruption_id: str, retry_id: str
    ) -> NarrationInterruption:
        validate_authority_identity(retry_id, "Retry identity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_session.require_active_campaign(connection, campaign_id)
            row = connection.execute(
                "SELECT * FROM narration_interruptions "
                "WHERE campaign_id = ? AND interruption_id = ?",
                (campaign_id, interruption_id),
            ).fetchone()
            if row is None:
                raise CommandRejected("Narration interruption is unavailable")
            record = self._interruption_from_row(row)
            if record.resolving_retry_id not in (None, retry_id):
                raise IdempotencyConflict("Interruption already has a different retry identity")
            if record.resolving_retry_id is None:
                collision = connection.execute(
                    "SELECT 1 FROM canonical_events WHERE campaign_id = ? "
                    "AND attempt_id = ? LIMIT 1",
                    (campaign_id, retry_id),
                ).fetchone()
                if collision is not None and record.failed_attempt_id != retry_id:
                    raise IdempotencyConflict(
                        "Retry identity already belongs to another narration attempt"
                    )
                connection.execute(
                    "UPDATE narration_interruptions SET resolving_retry_id = ? "
                    "WHERE campaign_id = ? AND interruption_id = ?",
                    (retry_id, campaign_id, interruption_id),
                )
            connection.commit()
            return NarrationInterruption(
                campaign_id=record.campaign_id,
                interruption_id=record.interruption_id,
                turn_id=record.turn_id,
                last_action_id=record.last_action_id,
                failed_attempt_id=record.failed_attempt_id,
                retryable=record.retryable,
                resolving_retry_id=retry_id,
                resolved_sequence=record.resolved_sequence,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def append_retry_resolution(
        self,
        *,
        campaign_id: str,
        interruption_id: str,
        retry_id: str,
        turn_id: str,
        ordinal: int,
        text: str,
        response_blocks: Sequence[SafePayload] | None = None,
    ) -> CanonicalEvent:
        """Atomically append final Retry TALK and resolve its interruption."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_session.require_active_campaign(connection, campaign_id)
            row = connection.execute(
                "SELECT * FROM narration_interruptions "
                "WHERE campaign_id = ? AND interruption_id = ?",
                (campaign_id, interruption_id),
            ).fetchone()
            if row is None:
                raise CommandRejected("Narration interruption is unavailable")
            record = self._interruption_from_row(row)
            if record.resolving_retry_id != retry_id:
                raise IdempotencyConflict("Retry identity does not own this interruption")
            if not record.retryable:
                if record.resolved_sequence is None:
                    raise SchemaIdentityError("Resolved Retry has no canonical sequence")
                existing = connection.execute(
                    "SELECT * FROM canonical_events WHERE campaign_id = ? AND sequence = ?",
                    (campaign_id, record.resolved_sequence),
                ).fetchone()
                if existing is None:
                    raise SchemaIdentityError("Resolved Retry chronology is unavailable")
                connection.rollback()
                return self._event_from_row(existing)
            event = self._append_narration_in_connection(
                connection,
                campaign_id=campaign_id,
                turn_id=turn_id,
                attempt_id=retry_id,
                ordinal=ordinal,
                channel=ProseChannel.TALK,
                text=text,
                response_blocks=response_blocks,
            )
            connection.execute(
                "UPDATE narration_interruptions SET status = 'resolved', "
                "resolved_sequence = ? WHERE campaign_id = ? AND interruption_id = ?",
                (event.sequence, campaign_id, interruption_id),
            )
            self._before_narration_commit()
            connection.commit()
            return event
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def resolve_interruption(
        self,
        *,
        campaign_id: str,
        interruption_id: str,
        retry_id: str,
        resolved_sequence: int,
    ) -> NarrationInterruption:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_session.require_active_campaign(connection, campaign_id)
            row = connection.execute(
                "SELECT * FROM narration_interruptions "
                "WHERE campaign_id = ? AND interruption_id = ?",
                (campaign_id, interruption_id),
            ).fetchone()
            if row is None:
                raise CommandRejected("Narration interruption is unavailable")
            record = self._interruption_from_row(row)
            if record.resolving_retry_id != retry_id:
                raise IdempotencyConflict("Retry identity does not own this interruption")
            if not record.retryable:
                if record.resolved_sequence != resolved_sequence:
                    raise IdempotencyConflict("Retry resolution sequence does not match")
                connection.rollback()
                return record
            event = connection.execute(
                "SELECT attempt_id, action_id FROM canonical_events "
                "WHERE campaign_id = ? AND sequence = ?",
                (campaign_id, resolved_sequence),
            ).fetchone()
            if event is None or (
                str(event["attempt_id"]) != retry_id
                and (
                    record.last_action_id is None
                    or str(event["action_id"]) != record.last_action_id
                )
            ):
                raise IdempotencyConflict("Retry resolution does not name retry chronology")
            connection.execute(
                "UPDATE narration_interruptions SET status = 'resolved', "
                "resolved_sequence = ? WHERE campaign_id = ? AND interruption_id = ?",
                (resolved_sequence, campaign_id, interruption_id),
            )
            connection.commit()
            return NarrationInterruption(
                campaign_id=record.campaign_id,
                interruption_id=record.interruption_id,
                turn_id=record.turn_id,
                last_action_id=record.last_action_id,
                failed_attempt_id=record.failed_attempt_id,
                retryable=False,
                resolving_retry_id=retry_id,
                resolved_sequence=resolved_sequence,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def action_receipt(self, campaign_id: str, action_id: str) -> ActionReceipt:
        hit = self.action_hit(campaign_id, action_id)
        if hit is None:
            raise CommandRejected("Stored action result is unavailable")
        return hit.receipt

    @staticmethod
    def _interruption_from_row(row: sqlite3.Row) -> NarrationInterruption:
        return NarrationInterruption(
            campaign_id=str(row["campaign_id"]),
            interruption_id=str(row["interruption_id"]),
            turn_id=str(row["turn_id"]),
            last_action_id=(None if row["last_action_id"] is None else str(row["last_action_id"])),
            failed_attempt_id=str(row["failed_attempt_id"]),
            retryable=str(row["status"]) == "retryable",
            resolving_retry_id=(
                None if row["resolving_retry_id"] is None else str(row["resolving_retry_id"])
            ),
            resolved_sequence=(
                None if row["resolved_sequence"] is None else int(row["resolved_sequence"])
            ),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> CanonicalEvent:
        return CanonicalEvent(
            sequence=int(row["sequence"]),
            event_type=CanonicalEventType(str(row["event_type"])),
            turn_id=str(row["turn_id"]),
            action_id=(None if row["action_id"] is None else str(row["action_id"])),
            safe_payload=_load_payload(str(row["safe_payload_json"])),
            visibility=EventVisibility(str(row["visibility"])),
            attempt_id=(None if row["attempt_id"] is None else str(row["attempt_id"])),
            attempt_ordinal=(
                None if row["attempt_ordinal"] is None else int(row["attempt_ordinal"])
            ),
        )

    def _before_narration_commit(self) -> None:
        """Private deterministic fault seam for complete narration append rollback."""

    def snapshot(self, campaign_id: str) -> CampaignSnapshot:
        return cast(CampaignSnapshot, self._read_snapshot(campaign_id))

    def pending_player_response(self, campaign_id: str) -> SafePayload | None:
        """Read the latest unanswered player action since the last saved GM narration."""
        self._ensure_session_schema()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT a.action_id, a.turn_id, c.payload_json FROM actions a "
                "JOIN consequences c ON c.campaign_id = a.campaign_id "
                "AND c.action_id = a.action_id "
                "WHERE a.campaign_id = ? "
                "AND json_extract(c.payload_json, '$.kind') = 'sylvia_player_action' "
                "AND json_type(c.payload_json, '$.origin') = 'object' "
                "AND EXISTS (SELECT 1 FROM canonical_events e "
                "WHERE e.campaign_id = a.campaign_id AND e.action_id = a.action_id "
                "AND e.sequence > (SELECT COALESCE(MAX(sequence), 0) FROM canonical_events "
                "WHERE campaign_id = a.campaign_id AND event_type = 'talk')) "
                "AND NOT EXISTS (SELECT 1 FROM agent_session_messages m "
                "JOIN agent_sessions s ON s.session_id = m.session_id "
                "WHERE s.campaign_id = a.campaign_id "
                "AND json_extract(m.content_json, '$.kind') = 'player_action_response' "
                "AND json_extract(m.content_json, '$.player_action_id') = a.action_id) "
                "ORDER BY a.resulting_revision DESC, c.ordinal DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if row is None:
                return None
            return owned_payload({
                "action_id": str(row["action_id"]), "turn_id": str(row["turn_id"]),
                "origin": _load_payload(str(row["payload_json"]))["origin"],
            })
        finally:
            connection.close()

    def current_campaign_entities(self, campaign_id: str) -> tuple[SafePayload, ...]:
        """Read only current entity records for panels, not the campaign's action/audit history."""
        connection = self._connect()
        try:
            # An admitted predecessor has no continuity records before migration 9. Reading a
            # panel must not force a write migration merely to return that empty collection.
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) < 9:
                return ()
            return tuple(
                owned_payload({"record_id": row["record_id"], **_load_payload(row["payload_json"])})
                for row in connection.execute(
                    "SELECT record.record_id, record.payload_json "
                    "FROM campaign_continuity_records AS record "
                    "JOIN (SELECT record_id, MAX(sequence) AS sequence "
                    "FROM campaign_continuity_records "
                    "WHERE campaign_id = ? AND kind = 'entity' GROUP BY record_id) AS latest "
                    "ON latest.record_id = record.record_id AND latest.sequence = record.sequence "
                    "WHERE record.campaign_id = ? "
                    "AND record.kind = 'entity' AND record.status = 'active' "
                    "ORDER BY record.record_id", (campaign_id, campaign_id),
                )
            )
        finally:
            connection.close()

    def display_snapshot(
        self, campaign_id: str, *, event_after: int | None = None,
        actions_after_revision: int | None = None,
        action_ids: tuple[str, ...] = (),
    ) -> CampaignDisplaySnapshot:
        """Read current state plus requested event/action history in one transaction."""
        return cast(CampaignDisplaySnapshot, self._read_snapshot(
            campaign_id, display_only=True, event_after=event_after,
            actions_after_revision=actions_after_revision, display_action_ids=action_ids,
        ))

    def _read_snapshot(
        self, campaign_id: str, *, display_only: bool = False,
        event_after: int | None = None, actions_after_revision: int | None = None,
        display_action_ids: tuple[str, ...] = (),
    ) -> CampaignSnapshot | CampaignDisplaySnapshot:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if row is None:
                raise CommandRejected("Campaign is unavailable: missing or purged")
            campaign = self._campaign_record(row)
            has_f5 = (
                int(connection.execute("PRAGMA user_version").fetchone()[0])
                == _CAMPAIGN_SCHEMA_VERSION
            )
            participants = tuple(
                (
                    ParticipantArtifact
                    if has_f5 and item["adaptation_id"] is not None
                    else Participant
                )(
                    participant_id=str(item["participant_id"]),
                    kind=str(item["kind"]),
                    package_id=str(item["package_id"]),
                    package_version=str(item["package_version"]),
                    data=_load_payload(str(item["data_json"])),
                    package_sha256=(
                        None
                        if not has_f5 or item["package_sha256"] is None
                        else str(item["package_sha256"])
                    ),
                    schema_id=(
                        None if not has_f5 or item["schema_id"] is None else str(item["schema_id"])
                    ),
                    schema_sha256=(
                        None
                        if not has_f5 or item["schema_sha256"] is None
                        else str(item["schema_sha256"])
                    ),
                    validation_status=(
                        ParticipantValidationStatus.QUARANTINED
                        if not has_f5
                        else ParticipantValidationStatus(str(item["validation_status"]))
                    ),
                    adaptation_id=(
                        None
                        if not has_f5 or item["adaptation_id"] is None
                        else str(item["adaptation_id"])
                    ),
                )
                for item in connection.execute(
                    "SELECT * FROM participants WHERE campaign_id = ? ORDER BY participant_id",
                    (campaign_id,),
                )
            )
            action_filter = ""
            event_filter = ""
            action_params: tuple[object, ...] = (campaign_id,)
            event_params: tuple[object, ...] = (campaign_id,)
            if display_only:
                action_filter = (
                    " AND (action_id IN (SELECT action_id FROM canonical_events "
                    "WHERE campaign_id = ? AND sequence > ?) OR resulting_revision > ? "
                    "OR action_id IN (SELECT value FROM json_each(?)))"
                )
                action_params = (
                    campaign_id, campaign_id, event_after, actions_after_revision,
                    json.dumps(display_action_ids),
                )
                event_filter = (
                    " AND (sequence > ? OR (event_type = 'tool_call' AND action_id IN "
                    "(SELECT action_id FROM canonical_events WHERE campaign_id = ? "
                    "AND sequence > ?)))"
                )
                event_params = (campaign_id, event_after, campaign_id, event_after)
            actions = tuple(
                ActionRecord(
                    action_id=str(item["action_id"]),
                    turn_id=str(item["turn_id"]),
                    status=ActionStatus(str(item["status"])),
                    expected_revision=int(item["expected_revision"]),
                    resulting_revision=int(item["resulting_revision"]),
                    writer=WriterRole(str(item["writer"])),
                    package_id=str(item["package_id"]),
                    package_version=str(item["package_version"]),
                    package_sha256=str(item["package_sha256"]),
                    tool_name=str(item["tool_name"]),
                    parameters=_load_payload(str(item["payload_json"])),
                    result=_load_payload(str(item["result_json"])),
                )
                for item in (() if display_only and event_after is None
                             and actions_after_revision is None and not display_action_ids
                             else connection.execute(
                    f"SELECT * FROM actions WHERE campaign_id = ?{action_filter} "
                    "ORDER BY resulting_revision", action_params,
                ))
            )
            events = tuple(
                CanonicalEvent(
                    sequence=int(item["sequence"]),
                    event_type=CanonicalEventType(str(item["event_type"])),
                    turn_id=str(item["turn_id"]),
                    action_id=(None if item["action_id"] is None else str(item["action_id"])),
                    safe_payload=_load_payload(str(item["safe_payload_json"])),
                    visibility=EventVisibility(str(item["visibility"])),
                    attempt_id=(None if item["attempt_id"] is None else str(item["attempt_id"])),
                    attempt_ordinal=(
                        None if item["attempt_ordinal"] is None else int(item["attempt_ordinal"])
                    ),
                )
                for item in (() if display_only and event_after is None else connection.execute(
                    f"SELECT * FROM canonical_events WHERE campaign_id = ?{event_filter} "
                    "ORDER BY sequence", event_params,
                ))
            )
            mutations = tuple(
                MutationRecord(
                    action_id=str(item["action_id"]),
                    ordinal=int(item["ordinal"]),
                    namespace=str(item["namespace"]),
                    key=str(item["state_key"]),
                    before=_load_json(str(item["before_json"])),
                    after=_load_json(str(item["after_json"])),
                )
                for item in (() if display_only else connection.execute(
                    "SELECT * FROM mutations WHERE campaign_id = ? ORDER BY action_id, ordinal",
                    (campaign_id,),
                ))
            )
            consequences = tuple(
                ConsequenceRecord(
                    action_id=str(item["action_id"]),
                    ordinal=int(item["ordinal"]),
                    payload=_load_payload(str(item["payload_json"])),
                )
                for item in (() if display_only else connection.execute(
                    "SELECT * FROM consequences WHERE campaign_id = ? ORDER BY action_id, ordinal",
                    (campaign_id,),
                ))
            )
            turns = MappingProxyType(
                {
                    str(item["turn_id"]): TurnStatus(str(item["status"]))
                    for item in connection.execute(
                        "SELECT turn_id, status FROM turns WHERE campaign_id = ? ORDER BY turn_id",
                        (campaign_id,),
                    )
                }
            )
            if display_only:
                display = CampaignDisplaySnapshot(
                    campaign_id=campaign.campaign_id, display_name=campaign.display_name,
                    lifecycle=campaign.lifecycle, writer=campaign.writer,
                    revision=campaign.revision,
                    package_lock=self._read_lock(connection, campaign_id),
                    state=self._read_state(connection, campaign_id), participants=participants,
                    actions=actions, events=events, turns=turns,
                )
                connection.commit()
                return display
            snapshot = CampaignSnapshot(
                campaign_id=campaign.campaign_id,
                display_name=campaign.display_name,
                lifecycle=campaign.lifecycle,
                writer=campaign.writer,
                revision=campaign.revision,
                package_lock=self._read_lock(connection, campaign_id),
                state=self._read_state(connection, campaign_id),
                participants=participants,
                actions=actions,
                events=events,
                mutations=mutations,
                consequences=consequences,
                turns=turns,
                continuity=(
                    sqlite_continuity.read_view(connection, campaign_id)
                    if int(connection.execute("PRAGMA user_version").fetchone()[0])
                    == _CAMPAIGN_SCHEMA_VERSION
                    else CampaignContinuityView()
                ),
            )
            connection.commit()
            return snapshot
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _read_lock(self, connection: sqlite3.Connection, campaign_id: str) -> PackageLock:
        row = connection.execute(
            "SELECT current_lock_id, lock_hash FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if (
            row is not None
            and row["current_lock_id"] is None
            and str(row["lock_hash"]) == _UNBOUND_CAMPAIGN_LOCK_HASH
        ):
            return PackageLock(entries=())
        return sqlite_packages.current_lock_record(connection, campaign_id).package_lock

    def _before_commit(self) -> None:
        """Private deterministic fault-injection seam for atomicity verification."""

    @staticmethod
    def _campaign_record(row: sqlite3.Row) -> CampaignRecord:
        return CampaignRecord(
            campaign_id=str(row["campaign_id"]),
            display_name=str(row["display_name"]),
            lifecycle=LifecycleState(str(row["lifecycle"])),
            writer=WriterRole(str(row["writer"])),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _read_state(connection: sqlite3.Connection, campaign_id: str) -> Mapping[str, JsonValue]:
        return owned_payload(
            {
                f"{row['namespace']}.{row['state_key']}": _load_json(str(row["value_json"]))
                for row in connection.execute(
                    "SELECT namespace, state_key, value_json FROM campaign_state "
                    "WHERE campaign_id = ? ORDER BY namespace, state_key",
                    (campaign_id,),
                )
            }
        )

    def stage_package_archive(self, admitted: AdmittedPackageArchive, install_id: str) -> None:
        validate_authority_identity(install_id, "Package install identity")
        manifest_json = _dump(
            {
                "format": admitted.format_version,
                "packages": [json.loads(item.canonical_json) for item in admitted.manifests],
            }
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_packages.begin_staging(connection, install_id, manifest_json)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        for file_order, archive_file in enumerate(admitted.files):
            for chunk_order, chunk in enumerate(admitted.iter_file_chunks(archive_file)):
                connection = self._connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    sqlite_packages.append_staging_chunk(
                        connection,
                        install_id=install_id,
                        file_order=file_order,
                        chunk_order=chunk_order,
                        package_id=archive_file.manifest.package_id,
                        version=archive_file.manifest.version,
                        content_sha256=archive_file.manifest.content_sha256,
                        portable_path=archive_file.file.path,
                        chunk=chunk,
                    )
                    connection.commit()
                except BaseException:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
                finally:
                    connection.close()

    def publish_package_archive(
        self, admitted: AdmittedPackageArchive, install_id: str
    ) -> PackageCandidate:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._before_package_publication()
            candidate = sqlite_packages.publish_archive(connection, admitted, install_id)
            connection.commit()
            return candidate
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _before_package_publication(self) -> None:
        """Private deterministic publication crash seam."""

    def package_candidate(self, candidate_id: str) -> PackageCandidate:
        connection = self._connect()
        try:
            return sqlite_packages.read_candidate(connection, candidate_id)
        finally:
            connection.close()

    def package_participant_declarations(
        self, candidate_id: str
    ) -> tuple[ParticipantArtifactDeclaration, ...]:
        connection = self._connect()
        try:
            return sqlite_packages.candidate_participant_declarations(connection, candidate_id)
        finally:
            connection.close()

    def exact_participant_declarations(
        self, package_id: str, version: str, content_sha256: str
    ) -> tuple[ParticipantArtifactDeclaration, ...]:
        connection = self._connect()
        try:
            return sqlite_packages.exact_participant_declarations(
                connection, (package_id, version, content_sha256)
            )
        finally:
            connection.close()

    def participant_adaptation_is_admitted(self, participant: Participant) -> bool:
        """Require durable exact provenance only when an identity crosses schemas."""

        if not self.campaign_schema_is_active():
            return participant.adaptation_id is None
        connection = self._connect()
        try:
            return self._participant_adaptation_is_admitted(connection, participant)
        finally:
            connection.close()

    @staticmethod
    def _participant_adaptation_is_admitted(
        connection: sqlite3.Connection, participant: Participant
    ) -> bool:
        target_identity = (
            participant.kind,
            participant.package_id,
            participant.package_version,
            participant.package_sha256,
            participant.schema_id,
            participant.schema_sha256,
        )
        existing_identities = {
            (
                str(row["kind"]),
                str(row["package_id"]),
                str(row["package_version"]),
                None if row["package_sha256"] is None else str(row["package_sha256"]),
                None if row["schema_id"] is None else str(row["schema_id"]),
                None if row["schema_sha256"] is None else str(row["schema_sha256"]),
            )
            for row in connection.execute(
                "SELECT kind, package_id, package_version, package_sha256, schema_id, "
                "schema_sha256 FROM participants WHERE participant_id = ?",
                (participant.participant_id,),
            )
        }
        crosses_schema = any(identity != target_identity for identity in existing_identities)
        if participant.adaptation_id is None:
            return not crosses_schema
        row = connection.execute(
            "SELECT * FROM participant_adaptations WHERE operation_id = ?",
            (participant.adaptation_id,),
        ).fetchone()
        if row is None:
            return False
        adapted = sqlite_campaign.artifact_from_adaptation_row(row)
        return all(
            left == right
            for left, right in (
                (participant.participant_id, adapted.participant_id),
                (participant.kind, adapted.kind),
                (participant.package_id, adapted.package_id),
                (participant.package_version, adapted.package_version),
                (participant.package_sha256, adapted.package_sha256),
                (participant.schema_id, adapted.schema_id),
                (participant.schema_sha256, adapted.schema_sha256),
                (True, participant.data == adapted.data or any(
                    adapted.data in participant_data_history(
                        connection, str(source["campaign_id"]),
                        participant.participant_id, participant.data,
                    )
                    for source in connection.execute(
                        "SELECT campaign_id FROM participants WHERE participant_id = ? "
                        "AND adaptation_id = ? AND data_json = ?",
                        (participant.participant_id, participant.adaptation_id,
                         _dump(participant.data)),
                    )
                )),
            )
        )

    def commit_participant_adaptation(
        self,
        *,
        operation_id: str,
        source_campaign_id: str,
        participant_id: str,
        destination_package_id: str,
        destination_package_version: str,
        destination_package_sha256: str,
        destination_kind: str,
        destination_schema_id: str,
        destination_schema_sha256: str,
        data: SafePayload,
    ) -> ParticipantArtifact:
        """Atomically validate and persist one replay-fenced Builder adaptation."""

        payload_sha256 = sqlite_campaign.adaptation_payload_sha256(
            source_campaign_id=source_campaign_id,
            participant_id=participant_id,
            destination_package_id=destination_package_id,
            destination_package_version=destination_package_version,
            destination_package_sha256=destination_package_sha256,
            destination_kind=destination_kind,
            destination_schema_id=destination_schema_id,
            destination_schema_sha256=destination_schema_sha256,
            data=data,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            existing = connection.execute(
                "SELECT * FROM participant_adaptations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise IdempotencyConflict("Adaptation operation payload does not match")
                connection.rollback()
                return sqlite_campaign.artifact_from_adaptation_row(existing)
            source_campaign = connection.execute(
                "SELECT lifecycle, writer FROM campaigns WHERE campaign_id = ?",
                (source_campaign_id,),
            ).fetchone()
            source = connection.execute(
                "SELECT * FROM participants WHERE campaign_id = ? AND participant_id = ?",
                (source_campaign_id, participant_id),
            ).fetchone()
            if (
                source_campaign is None
                or str(source_campaign["lifecycle"]) != LifecycleState.ACTIVE.value
                or str(source_campaign["writer"]) != WriterRole.BUILDER.value
                or source is None
            ):
                raise CommandRejected(
                    "Participant adaptation requires an active Builder source",
                    code="participant_adaptation_source_unavailable",
                )
            declaration = connection.execute(
                "SELECT schema_id, schema_sha256, schema_json "
                "FROM package_participant_artifacts WHERE package_id = ? AND version = ? "
                "AND content_sha256 = ? AND kind = ?",
                (
                    destination_package_id,
                    destination_package_version,
                    destination_package_sha256,
                    destination_kind,
                ),
            ).fetchone()
            if (
                declaration is None
                or str(declaration["schema_id"]) != destination_schema_id
                or str(declaration["schema_sha256"]) != destination_schema_sha256
            ):
                raise CommandRejected(
                    "Destination package participant schema is unavailable",
                    code="participant_schema_unavailable",
                )
            validate_json_value(
                data,
                cast(Mapping[str, object], _load_payload(str(declaration["schema_json"]))),
                label=f"Participant {participant_id}",
            )
            source_data = _load_payload(str(source["data_json"]))
            source_data_sha256 = hashlib.sha256(canonical_json_bytes(source_data)).hexdigest()
            connection.execute(
                "INSERT INTO participant_adaptations VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    payload_sha256,
                    source_campaign_id,
                    participant_id,
                    str(source["kind"]),
                    str(source["package_id"]),
                    str(source["package_version"]),
                    None if source["package_sha256"] is None else str(source["package_sha256"]),
                    None if source["schema_id"] is None else str(source["schema_id"]),
                    None if source["schema_sha256"] is None else str(source["schema_sha256"]),
                    source_data_sha256,
                    destination_kind,
                    destination_package_id,
                    destination_package_version,
                    destination_package_sha256,
                    destination_schema_id,
                    destination_schema_sha256,
                    _dump(data),
                ),
            )
            stored = connection.execute(
                "SELECT * FROM participant_adaptations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if stored is None:  # pragma: no cover - guarded by the successful insert
                raise RuntimeError("Participant adaptation was not persisted")
            connection.commit()
            return sqlite_campaign.artifact_from_adaptation_row(stored)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def campaign_lifecycle(self, campaign_id: str) -> CampaignSnapshot:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_exact_schema_identity(connection)
            if (
                connection.execute(
                    "SELECT 1 FROM campaigns WHERE campaign_id = ?", (campaign_id,)
                ).fetchone()
                is None
            ):
                raise CommandRejected("Campaign lifecycle authority is unavailable")
            self._ensure_campaign_schema(connection)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        return self.snapshot(campaign_id)

    def package_candidates(self) -> tuple[PackageCandidate, ...]:
        connection = self._connect()
        try:
            return sqlite_packages.list_candidates(connection)
        finally:
            connection.close()

    def package_staging(self, install_id: str) -> PackageStagingRecord:
        connection = self._connect()
        try:
            return sqlite_packages.read_staging(connection, install_id)
        finally:
            connection.close()

    def exact_package_manifest(
        self, package_id: str, version: str, content_sha256: str
    ) -> PackageManifest:
        """Read one exact installed package manifest without exposing storage internals."""

        connection = self._connect()
        try:
            return sqlite_packages.exact_manifest(connection, (package_id, version, content_sha256))
        finally:
            connection.close()

    def current_package_lock(self, campaign_id: str) -> PackageLockRecord:
        connection = self._connect()
        try:
            return sqlite_packages.current_lock_record(connection, campaign_id)
        finally:
            connection.close()

    def primary_package_manifest(self, lock_id: str) -> PackageManifest | None:
        connection = self._connect()
        try:
            return sqlite_packages.primary_manifest_for_lock(connection, lock_id)
        finally:
            connection.close()

    def package_lock_lineage(self, campaign_id: str) -> tuple[PackageLockRecord, ...]:
        connection = self._connect()
        try:
            return sqlite_packages.lock_lineage(connection, campaign_id)
        finally:
            connection.close()

    def consume_package_blob(
        self, blob_sha256: str, consumer: Callable[[bytes], None]
    ) -> PackageBlobReadReceipt:
        validate_authority_identity(blob_sha256, "Package blob SHA-256", sha256=True)
        lease_id = f"read-{uuid4().hex}"
        spool = _new_package_read_spool()
        byte_count = 0
        maximum_chunk = 0
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT byte_count, chunk_count FROM package_blobs WHERE blob_sha256 = ?",
                (blob_sha256,),
            ).fetchone()
            if row is None:
                raise CommandRejected(
                    "Exact package blob is unavailable", code="package_unavailable"
                )
            connection.execute(
                "INSERT INTO package_read_leases VALUES (?, ?)", (lease_id, blob_sha256)
            )
            digest = hashlib.sha256()
            chunk_count = 0
            for chunk_row in connection.execute(
                "SELECT chunk_order, byte_count, chunk_sha256, data FROM package_blob_chunks "
                "WHERE blob_sha256 = ? ORDER BY chunk_order",
                (blob_sha256,),
            ):
                data = bytes(chunk_row["data"])
                if (
                    int(chunk_row["chunk_order"]) != chunk_count
                    or int(chunk_row["byte_count"]) != len(data)
                    or str(chunk_row["chunk_sha256"]) != hashlib.sha256(data).hexdigest()
                    or len(data) > PACKAGE_CHUNK_LIMIT
                ):
                    raise CommandRejected("Exact package blob is corrupt", code="package_corrupt")
                spool.write(data)
                digest.update(data)
                byte_count += len(data)
                maximum_chunk = max(maximum_chunk, len(data))
                chunk_count += 1
            if (
                chunk_count != int(row["chunk_count"])
                or byte_count != int(row["byte_count"])
                or digest.hexdigest() != blob_sha256
            ):
                raise CommandRejected("Exact package blob is corrupt", code="package_corrupt")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            spool.close()
            raise
        finally:
            connection.close()
        try:
            spool.seek(0)
            while True:
                chunk = spool.read(PACKAGE_CHUNK_LIMIT)
                if not chunk:
                    break
                consumer(chunk)
        finally:
            spool.close()
            cleanup = self._connect()
            try:
                cleanup.execute("BEGIN IMMEDIATE")
                sqlite_packages.remove_read_lease(cleanup, lease_id)
                cleanup.commit()
            except BaseException:
                if cleanup.in_transaction:
                    cleanup.rollback()
                raise
            finally:
                cleanup.close()
        return _VerifiedPackageBlobReadReceipt(blob_sha256, byte_count, maximum_chunk, lease_id)

    @staticmethod
    def connection_in_transaction_for_test() -> bool:
        return False

    def add_package_recovery_root(self, root_id: str, blob_sha256: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT 1 FROM package_blobs WHERE blob_sha256 = ?", (blob_sha256,)
                ).fetchone()
                is None
            ):
                manifest = connection.execute(
                    "SELECT canonical_json FROM package_manifests "
                    "WHERE content_sha256 = ? ORDER BY package_id, version LIMIT 1",
                    (blob_sha256,),
                ).fetchone()
                if manifest is not None:
                    record = _load_payload(str(manifest["canonical_json"]))
                    package_root = canonical_json_bytes(
                        {key: value for key, value in record.items() if key != "content_sha256"}
                    )
                    if hashlib.sha256(package_root).hexdigest() != blob_sha256:
                        raise SchemaIdentityError("Package content-root authority is invalid")
                    chunks = tuple(
                        package_root[offset : offset + PACKAGE_CHUNK_LIMIT]
                        for offset in range(0, len(package_root), PACKAGE_CHUNK_LIMIT)
                    )
                    connection.execute(
                        "INSERT INTO package_blobs VALUES (?, ?, ?)",
                        (blob_sha256, len(package_root), len(chunks)),
                    )
                    for chunk_order, chunk in enumerate(chunks):
                        connection.execute(
                            "INSERT INTO package_blob_chunks VALUES (?, ?, ?, ?, ?)",
                            (
                                blob_sha256,
                                chunk_order,
                                len(chunk),
                                hashlib.sha256(chunk).hexdigest(),
                                chunk,
                            ),
                        )
            sqlite_packages.add_recovery_root(connection, root_id, blob_sha256)
            sqlite_packages.verify_package_authority(connection)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def remove_package_recovery_root(self, root_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_packages.remove_recovery_root(connection, root_id)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def reclaim_package_bytes(self) -> ReclamationResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = sqlite_packages.reclaim(connection)
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def open_turn(self, campaign_id: str, turn_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_session.require_active_campaign(connection, campaign_id)
            existing = connection.execute(
                "SELECT turn_id FROM turns WHERE campaign_id = ? AND status = 'open'",
                (campaign_id,),
            ).fetchone()
            if existing is not None:
                raise CommandRejected("Campaign already has an open TURN")
            revision_row = connection.execute(
                "SELECT revision FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if revision_row is None:
                raise CommandRejected("Campaign is unavailable")
            try:
                connection.execute(
                    "INSERT INTO turns(campaign_id, turn_id, status, created_revision) "
                    "VALUES (?, ?, 'open', ?)",
                    (campaign_id, turn_id, int(revision_row["revision"])),
                )
            except sqlite3.IntegrityError as error:
                raise CommandRejected("TURN identity is unavailable") from error
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def current_open_turn(self, campaign_id: str) -> str | None:
        """Return the exact open TURN identity, if one exists."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT turn_id FROM turns WHERE campaign_id = ? AND status = 'open'",
                (campaign_id,),
            ).fetchone()
            return None if row is None else str(row["turn_id"])
        finally:
            connection.close()

    def complete_turn(self, campaign_id: str, turn_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sqlite_session.require_active_campaign(connection, campaign_id)
            has_pending_table = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'pending_mechanics_actions'"
                ).fetchone()
                is not None
            )
            if (
                has_pending_table
                and connection.execute(
                    "SELECT 1 FROM pending_mechanics_actions WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()
                is not None
            ):
                raise CommandRejected(
                    "TURN cannot complete while a mechanics choice is pending",
                    code="mechanics_choice_pending",
                )
            updated = connection.execute(
                "UPDATE turns SET status = 'completed' WHERE campaign_id = ? AND turn_id = ? "
                "AND status = 'open'",
                (campaign_id, turn_id),
            )
            if updated.rowcount != 1:
                raise CommandRejected("TURN cannot be completed")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def turn_is_completed(self, campaign_id: str, turn_id: str) -> bool:
        """Return whether the exact construction TURN already reached its checkpoint."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                (campaign_id, turn_id),
            ).fetchone()
            if row is None:
                raise CommandRejected("TURN identity is unavailable")
            return str(row["status"]) == TurnStatus.COMPLETED.value
        finally:
            connection.close()

    def package_file_paths(
        self,
        package_id: str,
        version: str,
        sha256: str,
        *,
        maximum_files: int,
    ) -> tuple[str, ...]:
        """List exact immutable package paths under an explicit cardinality bound."""

        if maximum_files < 1:
            raise ValueError("Package file limit must be positive")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT portable_path FROM package_manifest_files "
                "WHERE package_id = ? AND version = ? AND content_sha256 = ? "
                "ORDER BY portable_path LIMIT ?",
                (package_id, version, sha256, maximum_files + 1),
            ).fetchall()
            if len(rows) > maximum_files:
                raise CommandRejected(
                    "Package has too many files for this consumer",
                    code="package_file_catalog_oversized",
                )
            return tuple(str(row["portable_path"]) for row in rows)
        finally:
            connection.close()

    def package_file_bytes(
        self,
        package_id: str,
        version: str,
        sha256: str,
        portable_path: str,
        *,
        maximum_bytes: int,
    ) -> bytes | None:
        """Read one exact immutable package file under an explicit memory bound."""

        if maximum_bytes < 0:
            raise ValueError("Package file byte limit cannot be negative")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT byte_count, blob_sha256 FROM package_manifest_files "
                "WHERE package_id = ? AND version = ? AND content_sha256 = ? "
                "AND portable_path = ?",
                (package_id, version, sha256, portable_path),
            ).fetchone()
            if row is None:
                return None
            byte_count = int(row["byte_count"])
            if byte_count > maximum_bytes:
                raise CommandRejected(
                    "Package file exceeds its consumer byte limit",
                    code="package_file_oversized",
                )
            value = b"".join(sqlite_packages.verify_blob(connection, str(row["blob_sha256"])))
            if len(value) != byte_count:
                raise CommandRejected("Exact package blob is corrupt", code="package_corrupt")
            return value
        finally:
            connection.close()

    def declared_package_tool(
        self, package_id: str, version: str, sha256: str, tool_name: str
    ) -> ToolDeclaration | None:
        connection = self._connect()
        try:
            return sqlite_packages.declared_tool(
                connection, (package_id, version, sha256, tool_name)
            )
        finally:
            connection.close()

    def package_catalog_basis(
        self, campaign_id: str
    ) -> tuple[
        PackageLockRecord,
        Mapping[str, JsonValue],
        str,
        str,
        str,
        tuple[tuple[PackageLockEntry, ToolDeclaration], ...],
    ]:
        """Read availability and exact package-bound declarations in one transaction."""
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            lock = sqlite_packages.current_lock_record(connection, campaign_id)
            if lock.availability.value != "available":
                raise CommandRejected("Campaign package bytes are unavailable")
            campaign = connection.execute(
                "SELECT writer, lifecycle FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            # Narration-only turns share a revision; their opaque IDs do not order them.
            # The current open turn owns admission even when an older ID sorts later.
            turn = connection.execute(
                "SELECT status FROM turns WHERE campaign_id = ? "
                "ORDER BY (status = 'open') DESC, created_revision DESC, turn_id DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if campaign is None or turn is None:
                raise CommandRejected("Campaign catalog basis is unavailable")
            state = self._read_state(connection, campaign_id)
            declarations: list[tuple[PackageLockEntry, ToolDeclaration]] = []
            for entry in lock.package_lock.entries:
                for row in connection.execute(
                    "SELECT tool_name FROM package_tools WHERE package_id = ? AND version = ? "
                    "AND content_sha256 = ? ORDER BY tool_order",
                    (entry.package_id, entry.version, entry.sha256),
                ):
                    declaration = sqlite_packages.declared_tool(
                        connection,
                        (entry.package_id, entry.version, entry.sha256, str(row["tool_name"])),
                    )
                    if declaration is not None:
                        declarations.append((entry, declaration))
            connection.commit()
            return (
                lock,
                state,
                str(turn["status"]),
                str(campaign["writer"]),
                str(campaign["lifecycle"]),
                tuple(declarations),
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def package_surfaces_for_current_lock(
        self, campaign_id: str
    ) -> tuple[tuple[SurfaceDeclaration, ...], tuple[SurfaceDeclaration, ...]]:
        connection = self._connect()
        try:
            lock = sqlite_packages.current_lock_record(connection, campaign_id)
            return sqlite_packages.surfaces_for_lock(connection, lock.package_lock)
        finally:
            connection.close()

    def activation_hit(self, activation_id: str) -> tuple[str, ActivationResult] | None:
        connection = self._connect()
        try:
            return sqlite_packages.activation_hit(connection, activation_id)
        finally:
            connection.close()

    def activation_basis(
        self, campaign_id: str, candidate_id: str
    ) -> tuple[int, PackageLockRecord, PackageCandidate, Mapping[str, JsonValue], bool]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            campaign = connection.execute(
                "SELECT revision FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if campaign is None:
                raise CommandRejected("Campaign is unavailable")
            current = sqlite_packages.current_lock_record(connection, campaign_id)
            candidate = sqlite_packages.read_candidate(connection, candidate_id)
            state = self._read_state(connection, campaign_id)
            completed = (
                connection.execute(
                    "SELECT 1 FROM turns WHERE campaign_id = ? AND status = 'completed' LIMIT 1",
                    (campaign_id,),
                ).fetchone()
                is not None
            )
            connection.commit()
            return int(campaign["revision"]), current, candidate, state, completed
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _activation_old_authority_is_intact(
        self,
        *,
        campaign_id: str,
        activation_id: str,
        revision: int,
        lock_id: str,
        state: Mapping[str, JsonValue],
    ) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            campaign = connection.execute(
                "SELECT revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            activation = connection.execute(
                "SELECT 1 FROM package_activations WHERE activation_id = ?",
                (activation_id,),
            ).fetchone()
            current_state = self._read_state(connection, campaign_id)
            return (
                activation is None
                and campaign is not None
                and int(campaign["revision"]) == revision
                and str(campaign["current_lock_id"]) == lock_id
                and current_state == state
            )
        except (KeyError, TypeError, ValueError, sqlite3.Error):
            return False
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def commit_package_activation(
        self,
        *,
        campaign_id: str,
        activation_id: str,
        candidate_id: str,
        payload_sha256: str,
        expected_revision: int,
        expected_lock_id: str,
        migration_id: str,
        migrated_state: Mapping[str, JsonValue],
    ) -> ActivationResult:
        connection = self._connect()
        commit_started = False
        old_authority: tuple[int, str, Mapping[str, JsonValue]] | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_campaign_schema(connection)
            sqlite_session.require_active_campaign(connection, campaign_id)
            campaign = connection.execute(
                "SELECT revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if campaign is None or campaign["current_lock_id"] is None:
                raise CommandRejected("Campaign activation authority is unavailable")
            old_authority = (
                int(campaign["revision"]),
                str(campaign["current_lock_id"]),
                self._read_state(connection, campaign_id),
            )
            result = sqlite_packages.commit_activation(
                connection,
                campaign_id=campaign_id,
                activation_id=activation_id,
                candidate_id=candidate_id,
                payload_sha256=payload_sha256,
                expected_revision=expected_revision,
                expected_lock_id=expected_lock_id,
                migration_id=migration_id,
                migrated_state=migrated_state,
            )
            self._before_activation_commit()
            commit_started = True
            connection.commit()
            try:
                self._after_activation_commit()
            except BaseException as error:
                resolved = self.activation_hit(activation_id)
                if resolved is None or resolved[0] != payload_sha256:
                    raise CommandRejected(
                        "Activation commit outcome is uncertain",
                        code="activation_commit_uncertain",
                    ) from error
                replay = resolved[1]
                return ActivationResult(
                    replay.campaign_id,
                    replay.activation_id,
                    replay.status,
                    replay.old_lock_id,
                    replay.new_lock_id,
                    replay.resulting_revision,
                    replay.payload_sha256,
                    replayed=False,
                    resolution="durable_commit_resolved",
                )
            return result
        except BaseException as error:
            if connection.in_transaction:
                connection.rollback()
            if commit_started:
                resolved = self.activation_hit(activation_id)
                if resolved is not None and resolved[0] == payload_sha256:
                    replay = resolved[1]
                    return ActivationResult(
                        replay.campaign_id,
                        replay.activation_id,
                        replay.status,
                        replay.old_lock_id,
                        replay.new_lock_id,
                        replay.resulting_revision,
                        replay.payload_sha256,
                        replayed=False,
                        resolution="durable_commit_resolved",
                    )
                if old_authority is not None and self._activation_old_authority_is_intact(
                    campaign_id=campaign_id,
                    activation_id=activation_id,
                    revision=old_authority[0],
                    lock_id=old_authority[1],
                    state=old_authority[2],
                ):
                    raise CommandRejected(
                        "Activation commit was confirmed rolled back",
                        code="activation_commit_rolled_back",
                    ) from error
                raise CommandRejected(
                    "Activation commit outcome is uncertain",
                    code="activation_commit_uncertain",
                ) from error
            raise
        finally:
            connection.close()

    def _before_activation_commit(self) -> None:
        """Private deterministic precommit failure seam."""

    def _after_activation_commit(self) -> None:
        """Private deterministic lost-acknowledgement seam."""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._owner_lock.close()

    def advance_mechanics_choice(
        self, pending: PendingMechanicsAction, answer: MechanicsChoiceAnswer,
        next_choice: MechanicsChoiceRequest,
    ) -> AwaitingChoiceReceipt:
        """Save one answer and the next request without committing any mechanical effects."""
        if (len(pending.prior_answers) >= 63 or answer.request != pending.choice
                or answer.session_id != pending.initiating_session_id
                or answer.resolution_id in {a.resolution_id for a in pending.prior_answers}):
            raise IdempotencyConflict("Mechanics choice sequence does not match its reservation")
        descriptor = {**choice_payload(next_choice), "prior_answers": [
            answer_payload(a) for a in (*pending.prior_answers, answer)
        ]}
        if pending.player_action_origin is not None:
            descriptor["player_action_origin"] = pending.player_action_origin
        encoded = _dump(owned_payload(descriptor))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ?",
                (pending.campaign_id,),
            ).fetchone()
            campaign = connection.execute(
                "SELECT revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                (pending.campaign_id,),
            ).fetchone()
            if (row is None or self._pending_from_row(row) != pending or campaign is None
                    or int(campaign["revision"]) != pending.basis_revision
                    or str(campaign["current_lock_id"]) != pending.lock_id):
                raise StaleRevision("Mechanics choice basis has drifted")
            connection.execute(
                "UPDATE pending_mechanics_actions SET choice_id = ?, descriptor_json = ?, "
                "descriptor_sha256 = ? WHERE campaign_id = ? AND action_id = ?",
                (next_choice.choice_id, encoded, digest, pending.campaign_id, pending.action_id),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return AwaitingChoiceReceipt(pending.campaign_id, pending.action_id,
                                     pending.basis_revision, pending.turn_id, next_choice,
                                     len(pending.prior_answers) + 1)



def _open_for_application_owner(database_path: Path) -> SQLiteAuthorityStore:
    """Package-private construction gate used only by application authority."""

    return SQLiteAuthorityStore(
        database_path,
        _application_owner_gate=_APPLICATION_OWNER_GATE,
    )
