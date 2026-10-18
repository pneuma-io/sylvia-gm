"""Frozen outbound-only W11 portable campaign archive codec.

This module deliberately has no authority-store or recovery dependency.  It
encodes and validates one already-captured campaign closure and publishes the
closed file through one final Win32 namespace replacement.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import stat
import struct
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, BinaryIO, Never, cast

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    PackageGraphNode,
    canonical_json_bytes,
    manifest_from_record,
    resolve_exact_closure,
    resolve_package_graph,
)
from sylvia_gm.domain.state_ownership import owner_matches_package_lock
from sylvia_gm.infrastructure import private_records_archive
from sylvia_gm.infrastructure.zip_bounds import preflight_zip_directory

FORMAT_ID = "sylvia-gm-campaign-export"
FORMAT_VERSION = 1
PRIVATE_FORMAT_VERSION = 2
PRODUCT_ID = "sylvia-gm"
PRODUCT_GENERATION = "alpha"
PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION = 9
PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION = 10
PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION = 11
SOURCE_AUTHORITY_SCHEMA_VERSION = 12
STREAM_CHUNK_BYTES = 65_536

# W12 freezes a conservative physical ZIP envelope around the W11 logical limits.
RECOVERY_MAX_ARCHIVE_BYTES = 8_608_809_058
RECOVERY_MAX_CENTRAL_DIRECTORY_BYTES = 9_437_184
RECOVERY_MAX_MEMBER_NAME_BYTES = 70

LIMITS: Mapping[str, int] = {
    "member_count": 65_536,
    "authority_record_count": 1_000_000,
    "single_table_row_count": 250_000,
    "manifest_bytes": 16_777_216,
    "authority_jsonl_bytes": 67_108_864,
    "single_authority_row_bytes": 16_777_216,
    "single_text_or_json_value_bytes": 16_777_216,
    "single_blob_bytes": 536_870_912,
    "total_uncompressed_bytes": 8_589_934_592,
}

TABLES_IN_ORDER = (
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
    "canonical_events",
    "narration_interruptions",
    "mutations",
    "consequences",
    "campaign_locks",
    "campaign_lock_memberships",
    "package_activations",
    "campaign_plans",
    "builder_dm_handoffs",
    "agent_sessions",
    "pending_mechanics_actions",
    "pending_mechanics_action_aliases",
    "mechanics_choice_resolutions",
    "agent_session_messages",
    "package_candidates",
    "package_candidate_memberships",
    "package_staging",
    "packages",
    "package_dependencies",
    "package_manifests",
    "package_manifest_files",
    "package_exact_dependencies",
    "package_capabilities",
    "package_tools",
    "package_prompt_fragments",
    "package_ui_surfaces",
    "package_participant_artifacts",
    "package_blobs",
)

TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "campaigns": (
        "campaign_id",
        "display_name",
        "lifecycle",
        "writer",
        "revision",
        "lock_hash",
        "current_lock_id",
    ),
    "campaign_lifecycle_state": (
        "campaign_id",
        "retained_active_writer",
        "designated_session_id",
        "tombstone_prior_lifecycle",
        "tombstone_prior_writer",
        "tombstone_prior_session_id",
    ),
    "campaign_lifecycle_transitions": (
        "operation_id",
        "campaign_id",
        "operation_kind",
        "basis_revision",
        "resulting_revision",
        "from_lifecycle",
        "to_lifecycle",
        "from_writer",
        "to_writer",
        "designated_session_id",
    ),
    "campaign_operations": (
        "operation_id",
        "operation_kind",
        "campaign_id",
        "semantic_payload_json",
        "semantic_payload_sha256",
        "result_json",
        "basis_turn_id",
        "basis_lock_id",
        "basis_revision",
        "resulting_revision",
    ),
    "campaign_constructions": (
        "campaign_id",
        "confirmation_operation_id",
        "authority_turn_id",
        "authority_lock_id",
        "confirmation_revision",
        "lane",
        "canonical_input_json",
        "begin_operation_id",
        "begin_revision",
        "canonical_plan_json",
        "completion_operation_id",
        "completion_revision",
        "canonical_representation_sha256",
    ),
    "campaign_continuity_records": (
        "campaign_id",
        "sequence",
        "entry_id",
        "record_id",
        "kind",
        "status",
        "payload_json",
        "basis_message_ids_json",
        "operation_id",
        "admitted_revision",
    ),
    "campaign_revisions": (
        "campaign_id",
        "revision",
        "action_id",
        "activation_id",
        "plan_id",
        "handoff_id",
        "construction_operation_id",
        "lifecycle_operation_id",
        "continuity_operation_id",
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
        "package_sha256",
        "schema_id",
        "schema_sha256",
        "validation_status",
        "adaptation_id",
    ),
    "participant_quarantines": ("campaign_id", "participant_id", "reason_code"),
    "campaign_state_namespace_owners": (
        "campaign_id",
        "namespace",
        "owner_package_id",
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
        "lock_id",
    ),
    "idempotency_records": (
        "campaign_id",
        "idempotency_key",
        "payload_hash",
        "action_id",
        "status",
        "result_json",
        "resulting_revision",
    ),
    "pending_mechanics_actions": (
        "campaign_id",
        "action_id",
        "turn_id",
        "basis_revision",
        "writer",
        "package_id",
        "package_version",
        "package_sha256",
        "lock_id",
        "tool_name",
        "parameters_json",
        "payload_hash",
        "initiating_session_id",
        "choice_id",
        "descriptor_json",
        "descriptor_sha256",
    ),
    "pending_mechanics_action_aliases": (
        "campaign_id",
        "idempotency_key",
        "action_id",
        "payload_hash",
    ),
    "mechanics_choice_resolutions": (
        "campaign_id",
        "resolution_id",
        "action_id",
        "choice_id",
        "option_id",
        "resolution_sha256",
        "descriptor_sha256",
        "session_id",
        "resulting_revision",
    ),
    "canonical_events": (
        "campaign_id",
        "sequence",
        "event_type",
        "turn_id",
        "action_id",
        "safe_payload_json",
        "visibility",
        "attempt_id",
        "attempt_ordinal",
    ),
    "narration_interruptions": (
        "campaign_id",
        "interruption_id",
        "turn_id",
        "last_action_id",
        "failed_attempt_id",
        "status",
        "resolving_retry_id",
        "resolved_sequence",
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
    "campaign_locks": (
        "lock_id",
        "campaign_id",
        "closure_sha256",
        "availability",
        "predecessor_lock_id",
        "created_revision",
    ),
    "campaign_lock_memberships": (
        "lock_id",
        "closure_order",
        "package_id",
        "version",
        "content_sha256",
        "role",
    ),
    "package_activations": (
        "activation_id",
        "campaign_id",
        "payload_sha256",
        "candidate_id",
        "old_lock_id",
        "new_lock_id",
        "migration_id",
        "status",
        "resulting_revision",
        "result_json",
    ),
    "campaign_plans": (
        "plan_id",
        "campaign_id",
        "package_lock_id",
        "source_session_id",
        "content_sha256",
        "content_json",
        "provenance_json",
        "validation_outcome",
        "acceptance_status",
        "plan_revision",
        "resulting_campaign_revision",
        "payload_sha256",
    ),
    "builder_dm_handoffs": (
        "handoff_id",
        "campaign_id",
        "payload_sha256",
        "builder_session_id",
        "dm_session_id",
        "package_lock_id",
        "package_candidate_id",
        "plan_id",
        "plan_revision",
        "plan_content_sha256",
        "expected_revision",
        "resulting_campaign_revision",
        "chronology_ordinal",
        "completed_turn_id",
        "safe_payload_json",
    ),
    "agent_sessions": (
        "session_id",
        "campaign_id",
        "role",
        "provider_id",
        "model_id",
        "status",
        "parent_session_id",
        "selected_skill",
        "scope_json",
        "payload_sha256",
        "reasoning_effort",
        "reasoning_effort_once",
    ),
    "agent_session_messages": (
        "session_id",
        "sequence",
        "message_id",
        "kind",
        "content_json",
        "canonical_sequence",
    ),
    "package_candidates": ("candidate_id", "install_id", "status", "closure_sha256", "report_json"),
    "package_candidate_memberships": (
        "candidate_id",
        "closure_order",
        "package_id",
        "version",
        "content_sha256",
    ),
    "package_staging": ("install_id", "status", "manifest_json", "report_json"),
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
        "dependency_namespace",
    ),
    "package_manifests": (
        "package_id",
        "version",
        "content_sha256",
        "role",
        "canonical_json",
        "metadata_json",
        "provenance_json",
        "compatibility_json",
        "status",
    ),
    "package_manifest_files": (
        "package_id",
        "version",
        "content_sha256",
        "file_order",
        "portable_path",
        "byte_count",
        "blob_sha256",
    ),
    "package_exact_dependencies": (
        "package_id",
        "version",
        "content_sha256",
        "dependency_order",
        "dependency_namespace",
        "dependency_kind",
        "dependency_id",
        "dependency_version",
        "dependency_content_sha256",
    ),
    "package_capabilities": (
        "package_id",
        "version",
        "content_sha256",
        "capability_order",
        "capability",
    ),
    "package_tools": (
        "package_id",
        "version",
        "content_sha256",
        "tool_order",
        "tool_name",
        "capability",
        "input_schema_json",
        "state_predicates_json",
        "turn_statuses_json",
        "writers_json",
    ),
    "package_prompt_fragments": (
        "package_id",
        "version",
        "content_sha256",
        "surface_order",
        "fragment_id",
        "capability",
    ),
    "package_ui_surfaces": (
        "package_id",
        "version",
        "content_sha256",
        "surface_order",
        "surface_id",
        "capability",
    ),
    "package_participant_artifacts": (
        "package_id",
        "version",
        "content_sha256",
        "artifact_order",
        "kind",
        "schema_id",
        "schema_sha256",
        "schema_json",
    ),
    "package_blobs": ("blob_sha256", "byte_count", "chunk_count"),
}

TABLE_KEYS: Mapping[str, tuple[str, ...]] = {
    "campaigns": ("campaign_id",),
    "campaign_lifecycle_state": ("campaign_id",),
    "campaign_lifecycle_transitions": ("operation_id",),
    "campaign_operations": ("operation_id",),
    "campaign_constructions": ("campaign_id",),
    "campaign_continuity_records": ("campaign_id", "sequence"),
    "campaign_revisions": ("campaign_id", "revision"),
    "campaign_package_memberships": ("campaign_id", "load_order"),
    "participants": ("campaign_id", "participant_id"),
    "participant_quarantines": ("campaign_id", "participant_id"),
    "campaign_state_namespace_owners": ("campaign_id", "namespace"),
    "campaign_state": ("campaign_id", "namespace", "state_key"),
    "turns": ("campaign_id", "turn_id"),
    "actions": ("campaign_id", "action_id"),
    "idempotency_records": ("campaign_id", "idempotency_key"),
    "pending_mechanics_actions": ("campaign_id", "action_id"),
    "pending_mechanics_action_aliases": ("campaign_id", "idempotency_key"),
    "mechanics_choice_resolutions": ("campaign_id", "resolution_id"),
    "canonical_events": ("campaign_id", "sequence"),
    "narration_interruptions": ("campaign_id", "interruption_id"),
    "mutations": ("campaign_id", "action_id", "ordinal"),
    "consequences": ("campaign_id", "action_id", "ordinal"),
    "campaign_locks": ("lock_id",),
    "campaign_lock_memberships": ("lock_id", "closure_order"),
    "package_activations": ("activation_id",),
    "campaign_plans": ("plan_id",),
    "builder_dm_handoffs": ("handoff_id",),
    "agent_sessions": ("session_id",),
    "agent_session_messages": ("session_id", "sequence"),
    "package_candidates": ("candidate_id",),
    "package_candidate_memberships": ("candidate_id", "closure_order"),
    "package_staging": ("install_id",),
    "packages": ("package_id", "version", "sha256"),
    "package_dependencies": ("package_id", "version", "sha256", "dependency_order"),
    "package_manifests": ("package_id", "version", "content_sha256"),
    "package_manifest_files": ("package_id", "version", "content_sha256", "file_order"),
    "package_exact_dependencies": ("package_id", "version", "content_sha256", "dependency_order"),
    "package_capabilities": ("package_id", "version", "content_sha256", "capability_order"),
    "package_tools": ("package_id", "version", "content_sha256", "tool_order"),
    "package_prompt_fragments": ("package_id", "version", "content_sha256", "surface_order"),
    "package_ui_surfaces": ("package_id", "version", "content_sha256", "surface_order"),
    "package_participant_artifacts": ("package_id", "version", "content_sha256", "artifact_order"),
    "package_blobs": ("blob_sha256",),
}

MANIFEST_FIELDS = (
    "format_id",
    "format_version",
    "product_id",
    "product_generation",
    "source_authority_schema_version",
    "source_authority_schema_identity_sha256",
    "migration_provenance",
    "campaign_id",
    "captured_revision",
    "authority_record_count",
    "table_row_counts",
    "blob_count",
    "closure_index",
    "non_manifest_members",
)
CLOSURE_INDEX_FIELDS = (
    "lock_ids",
    "action_ids",
    "candidate_ids",
    "package_identities",
    "blob_sha256s",
    "plan_ids",
    "handoff_ids",
    "session_ids",
)
PACKAGE_IDENTITY_FIELDS = ("package_id", "version", "content_sha256")
NON_MANIFEST_MEMBER_FIELDS = ("name", "byte_count", "sha256")
MIGRATION_PROVENANCE_FIELDS = ("schema_migrations", "schema_extensions")
SCHEMA_MIGRATION_FIELDS = ("version", "applied_name", "sql_sha256")
SCHEMA_EXTENSION_FIELDS = (
    "extension_name",
    "extension_sha256",
    "predecessor_migration_name",
    "predecessor_sql_sha256",
)
EXPECTED_SCHEMA_MIGRATION_PROVENANCE: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "f1_neutral_authority",
        "6c416e59e5d38a3018845a8cb320b24e3bff74a169377735db048c01d4bff8d1",
    ),
    (
        2,
        "f2_provisional_event_protocol",
        "71e1c58545c1f8ce9f8bca24810987648a35c4779bbcdeecff947c8181b1b193",
    ),
    (
        3,
        "f3_sqlite_package_library",
        "58e3af1675b8232a8ef83211f8fe49161ec464a04bbae065db436a909d70ea3f",
    ),
    (
        4,
        "f4_builder_dm_sessions",
        "e0f2766f3f18d160493f4008ac920b79d0079c6475e6f59a970f05615b73e9fb",
    ),
    (
        5,
        "f5_campaign_lifecycle_recovery",
        "ed0c1f6770b9ffe2b1b2c0f3c1b1b4edf1666b0af23f337f46742ec103fd2483",
    ),
    (
        6,
        "f7_relational_state_namespace_ownership",
        "91c930b8b45fde6f03936318c4c0866dc46f9a88c3321d2fd4cef8800643eae6",
    ),
    (
        7,
        "mechanics_choice_reservations",
        "5a15dae72c9a64f4ea22cd80b81ab15a3c71c23ec4e9ed57503da30eff873d53",
    ),
    (
        8,
        "standalone_builder_chats",
        "70c67e3d1c0b8c9371f8a67f924eea3975aa23dab4e47f07488541983c7538d0",
    ),
    (
        9,
        "campaign_state_continuity",
        "6b4c74ea0d67ea324b496f076a16c8976b3b8921c8ec7eed2aa6056497308bee",
    ),
    (
        10,
        "unbound_draft_lifecycle",
        "3c5c44a28eea815f08e4cc126bad75b551f00602dc1965b891aa5cc06c14ce1f",
    ),
)
PRE_REASONING_SCHEMA_MIGRATION_PROVENANCE = EXPECTED_SCHEMA_MIGRATION_PROVENANCE
PREDECESSOR_SCHEMA_MIGRATION_PROVENANCE = EXPECTED_SCHEMA_MIGRATION_PROVENANCE[:-1]
EXPECTED_SCHEMA_MIGRATION_PROVENANCE += (
    (11, "private_provider_records",
     "4fe61f98e20ef40b346e3304942b216fdeb52a5decb383607aec6a44e397cf34"),
)
PRE_EFFORT_SCHEMA_MIGRATION_PROVENANCE = EXPECTED_SCHEMA_MIGRATION_PROVENANCE
EXPECTED_SCHEMA_MIGRATION_PROVENANCE += (
    (12, "session_reasoning_preferences",
     "d1e89bcbf082d1046e7eec8326e9bc2ba3217b1c2439bcc0667c8201ebb6ca28"),
)
EXPECTED_SCHEMA_EXTENSION_PROVENANCE = (
    (
        "f5_portable_campaign_recovery_v1",
        "d871df794682ae39c1d10cecf3378e471c02108ae9784782d4ac3c4693d77b61",
        "f5_reversible_lifecycle_v3",
        "a05b1892abf39629612602f2f07be62048e7a0d6ecd46e337039e89b30266a06",
    ),
    (
        "f5_probe_01_stable_campaign_identity",
        "0c252567c25ccbb3f2b81eb4a3f54e4fdc4e009220eac0e6d61f2f045cb1a7b4",
        "f5_campaign_lifecycle_recovery",
        "49d01f74743bd178ab23848c4493dda234bf51a26ad7115236c2356714c48802",
    ),
    (
        "f5_reversible_lifecycle_v3",
        "a05b1892abf39629612602f2f07be62048e7a0d6ecd46e337039e89b30266a06",
        "f5_probe_01_stable_campaign_identity",
        "0c252567c25ccbb3f2b81eb4a3f54e4fdc4e009220eac0e6d61f2f045cb1a7b4",
    ),
)
PURGE_SCHEMA_EXTENSION_PROVENANCE = (
    "f5_campaign_purge_v1",
    "1de84ac0fe9870ee106630aad69f12281322121cbc549f5f396192f0a384cd3b",
    "f5_portable_campaign_recovery_v1",
    "d871df794682ae39c1d10cecf3378e471c02108ae9784782d4ac3c4693d77b61",
)


def _failure(code: str) -> CommandRejected:
    return CommandRejected("Portable campaign export is invalid", code=code)


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Never:
    raise ValueError("non-finite JSON number")


def _json_bytes(value: object) -> bytes:
    """Encode frozen ABI insertion order without host- or clock-dependent data."""

    return json.dumps(
        value,
        sort_keys=False,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def authority_columns(table: str, source_schema_version: int) -> tuple[str, ...]:
    """Preserve the exact exported session shape of archives made before effort preferences."""
    columns = TABLE_COLUMNS[table]
    if (
        table == "agent_sessions"
        and source_schema_version <= PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION
    ):
        return columns[:-2]
    return columns


def source_authority_schema_claim_sha256(
    source_schema_version: int = SOURCE_AUTHORITY_SCHEMA_VERSION,
) -> str:
    """Hash the frozen portable projection, not the host ``sqlite_master`` text."""

    preimage = {
        "source_authority_schema_version": source_schema_version,
        "tables": [
            {
                "name": table,
                "columns": list(authority_columns(table, source_schema_version)),
                "key": list(TABLE_KEYS[table]),
            }
            for table in TABLES_IN_ORDER
        ],
    }
    return hashlib.sha256(_json_bytes(preimage)).hexdigest()


def canonical_authority_record(table: str, row: Mapping[str, object]) -> bytes:
    return _json_bytes({"table": table, "row": dict(row)}) + b"\n"


def canonical_authority_record_size(table: str, row: Mapping[str, object]) -> int:
    """Count exact JSONL bytes without allocating a whole escaped record during preflight.

    SQLite export rows contain scalar values. String slices bound temporary escaping even
    when a large value consists entirely of control characters or non-ASCII text.
    """
    size = len(canonical_authority_record(table, dict.fromkeys(row)))
    for value in row.values():
        if isinstance(value, str):
            value_size = 2 + sum(
                len(_json_bytes(value[offset:offset + 16_384])) - 2
                for offset in range(0, len(value), 16_384)
            )
        else:
            value_size = len(_json_bytes(value))
        size += value_size - 4  # Replace the structural null placeholder.
    return size


def iter_bounded_chunks(source: BinaryIO, *, limit: int) -> Iterator[bytes]:
    """Read at most one frozen stream chunk per call and never exceed ``limit``."""

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise _failure("portable_export_limit_total_uncompressed_bytes")
    total = 0
    while total < limit:
        chunk = source.read(min(STREAM_CHUNK_BYTES, limit - total))
        if not chunk:
            return
        total += len(chunk)
        yield chunk
    extra = source.read(1)
    if extra:
        raise _failure("portable_export_limit_total_uncompressed_bytes")


def checked_archive_ledger(**values: int) -> Mapping[str, int]:
    """Perform exact Python-integer limit arithmetic before allocation or reads."""

    for name, limit in LIMITS.items():
        value = values.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > limit:
            raise _failure(f"portable_export_limit_{name}")
    return dict(values)


def fixed_zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    info.internal_attr = 0
    info.flag_bits = 0
    info.extra = b""
    info.comment = b""
    return info


def closure_index(rows_by_table: Mapping[str, Sequence[Mapping[str, object]]]) -> dict[str, object]:
    def identities(table: str, column: str) -> list[str]:
        return sorted({str(row[column]) for row in rows_by_table[table]})

    package_identities = sorted(
        {
            (str(row["package_id"]), str(row["version"]), str(row["content_sha256"]))
            for row in rows_by_table["package_manifests"]
        }
    )
    return {
        "lock_ids": identities("campaign_locks", "lock_id"),
        "action_ids": identities("actions", "action_id"),
        "candidate_ids": identities("package_candidates", "candidate_id"),
        "package_identities": [
            {"package_id": package_id, "version": version, "content_sha256": content_sha256}
            for package_id, version, content_sha256 in package_identities
        ],
        "blob_sha256s": identities("package_blobs", "blob_sha256"),
        "plan_ids": identities("campaign_plans", "plan_id"),
        "handoff_ids": identities("builder_dm_handoffs", "handoff_id"),
        "session_ids": identities("agent_sessions", "session_id"),
    }


def _valid_provenance_name(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_]{0,127}", value) is not None


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_migration_provenance(
    value: object,
    *,
    enforce_declared_order_bytes: bool = True,
    source_schema_version: object = SOURCE_AUTHORITY_SCHEMA_VERSION,
) -> Mapping[str, object]:
    """Validate path-free normalized migration and extension lineage."""

    if not isinstance(value, Mapping) or set(value) != set(MIGRATION_PROVENANCE_FIELDS):
        raise _failure("portable_export_invalid_migration_provenance")
    if enforce_declared_order_bytes and tuple(value) != MIGRATION_PROVENANCE_FIELDS:
        raise _failure("portable_export_invalid_manifest_canonical_bytes")
    migrations = value["schema_migrations"]
    extensions = value["schema_extensions"]
    if not isinstance(migrations, list) or not migrations:
        raise _failure("portable_export_invalid_migration_provenance")
    if not isinstance(extensions, list):
        raise _failure("portable_export_invalid_migration_provenance")
    if any(
        not isinstance(item, Mapping)
        or set(item) != set(SCHEMA_MIGRATION_FIELDS)
        or (enforce_declared_order_bytes and tuple(item) != SCHEMA_MIGRATION_FIELDS)
        for item in migrations
    ):
        raise _failure("portable_export_invalid_migration_provenance")
    if any(
        not isinstance(item, Mapping)
        or set(item) != set(SCHEMA_EXTENSION_FIELDS)
        or (enforce_declared_order_bytes and tuple(item) != SCHEMA_EXTENSION_FIELDS)
        for item in extensions
    ):
        raise _failure("portable_export_invalid_migration_provenance")
    versions = [item["version"] for item in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise _failure("portable_export_invalid_migration_provenance")
    if any(
        not _valid_provenance_name(item["applied_name"]) or not _valid_sha256(item["sql_sha256"])
        for item in migrations
    ):
        raise _failure("portable_export_invalid_migration_provenance")
    extension_names = [item["extension_name"] for item in extensions]
    if extension_names != sorted(extension_names) or len(extension_names) != len(
        set(extension_names)
    ):
        raise _failure("portable_export_invalid_migration_provenance")
    if any(
        not _valid_provenance_name(item["extension_name"])
        or not _valid_sha256(item["extension_sha256"])
        or not _valid_provenance_name(item["predecessor_migration_name"])
        or not _valid_sha256(item["predecessor_sql_sha256"])
        for item in extensions
    ):
        raise _failure("portable_export_invalid_migration_provenance")
    migration_rows = tuple(
        (int(item["version"]), str(item["applied_name"]), str(item["sql_sha256"]))
        for item in migrations
    )
    extension_rows = tuple(
        (
            str(item["extension_name"]),
            str(item["extension_sha256"]),
            str(item["predecessor_migration_name"]),
            str(item["predecessor_sql_sha256"]),
        )
        for item in extensions
    )
    allowed_extension_rows = {
        EXPECTED_SCHEMA_EXTENSION_PROVENANCE,
        tuple(sorted((*EXPECTED_SCHEMA_EXTENSION_PROVENANCE, PURGE_SCHEMA_EXTENSION_PROVENANCE))),
    }
    if type(source_schema_version) is not int or source_schema_version not in {
        PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION,
        PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION,
        PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION, SOURCE_AUTHORITY_SCHEMA_VERSION,
    }:
        raise _failure("portable_export_invalid_source_schema")
    expected_migrations = (
        PREDECESSOR_SCHEMA_MIGRATION_PROVENANCE
        if source_schema_version == PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION
        else PRE_REASONING_SCHEMA_MIGRATION_PROVENANCE
        if source_schema_version == PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION
        else PRE_EFFORT_SCHEMA_MIGRATION_PROVENANCE
        if source_schema_version == PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION
        else EXPECTED_SCHEMA_MIGRATION_PROVENANCE
    )
    if migration_rows != expected_migrations or extension_rows not in allowed_extension_rows:
        raise _failure("portable_export_invalid_migration_provenance")
    return cast(Mapping[str, object], value)


def make_manifest(
    *,
    source_schema_version: int,
    source_schema_identity_sha256: str,
    migration_provenance: Mapping[str, object],
    campaign_id: str,
    captured_revision: int,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
    non_manifest_members: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if source_schema_version != SOURCE_AUTHORITY_SCHEMA_VERSION:
        raise _failure("portable_export_invalid_source_schema")
    expected_identity = source_authority_schema_claim_sha256(source_schema_version)
    if source_schema_identity_sha256 != expected_identity:
        raise _failure("portable_export_invalid_source_schema")
    normalized_provenance = validate_migration_provenance(migration_provenance)
    table_counts = {table: len(rows_by_table[table]) for table in TABLES_IN_ORDER}
    return {
        "format_id": FORMAT_ID,
        "format_version": PRIVATE_FORMAT_VERSION if any(
            item["name"] == private_records_archive.MEMBER_NAME for item in non_manifest_members
        ) else FORMAT_VERSION,
        "product_id": PRODUCT_ID,
        "product_generation": PRODUCT_GENERATION,
        "source_authority_schema_version": source_schema_version,
        "source_authority_schema_identity_sha256": source_schema_identity_sha256,
        "migration_provenance": {
            "schema_migrations": [
                dict(item)
                for item in cast(
                    Sequence[Mapping[str, object]],
                    normalized_provenance["schema_migrations"],
                )
            ],
            "schema_extensions": [
                dict(item)
                for item in cast(
                    Sequence[Mapping[str, object]],
                    normalized_provenance["schema_extensions"],
                )
            ],
        },
        "campaign_id": campaign_id,
        "captured_revision": captured_revision,
        "authority_record_count": sum(table_counts.values()),
        "table_row_counts": table_counts,
        "blob_count": len(rows_by_table["package_blobs"]),
        "closure_index": closure_index(rows_by_table),
        "non_manifest_members": [dict(item) for item in non_manifest_members],
    }


def manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    if tuple(manifest) != MANIFEST_FIELDS:
        raise _failure("portable_export_invalid_manifest")
    return _json_bytes(dict(manifest))


def _decode_json(data: bytes, code: str) -> object:
    try:
        if data.startswith(b"\xef\xbb\xbf"):
            raise ValueError("BOM")
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_constant,
        )
        if not _finite_json(value):
            raise ValueError("non-finite")
        return value
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise _failure(code) from None


def _finite_json(value: object) -> bool:
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _finite_json(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_finite_json(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name:
        return False
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return False
    return not bool(re.match(r"^[A-Za-z]:", name))


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    buffer = bytearray()
    with archive.open(info, "r") as member:
        for chunk in iter_bounded_chunks(cast(BinaryIO, member), limit=info.file_size):
            buffer.extend(chunk)
    if len(buffer) != info.file_size:
        raise _failure("portable_export_invalid_member_size")
    return bytes(buffer)


def _validate_zip_metadata(info: zipfile.ZipInfo) -> None:
    if (
        info.date_time != (1980, 1, 1, 0, 0, 0)
        or info.compress_type != zipfile.ZIP_STORED
        or info.create_system != 3
        or info.external_attr != 0o100600 << 16
        or info.internal_attr != 0
        or info.extra != b""
        or info.comment != b""
        or info.flag_bits != 0
    ):
        raise _failure("portable_export_invalid_zip_metadata")


def _rows_from_authority(
    data: bytes,
    *,
    enforce_declared_order_bytes: bool = True,
    source_schema_version: int = SOURCE_AUTHORITY_SCHEMA_VERSION,
) -> tuple[list[dict[str, object]], dict[str, list[dict[str, object]]]]:
    if not data or not data.endswith(b"\n") or b"\r" in data:
        raise _failure("portable_export_invalid_authority_framing")
    records: list[dict[str, object]] = []
    rows_by_table: dict[str, list[dict[str, object]]] = {table: [] for table in TABLES_IN_ORDER}
    last_table = -1
    last_keys: dict[str, tuple[object, ...]] = {}
    for raw_line in data[:-1].split(b"\n"):
        if not raw_line or len(raw_line) + 1 > LIMITS["single_authority_row_bytes"]:
            raise _failure("portable_export_invalid_authority_framing")
        item = _decode_json(raw_line, "portable_export_invalid_authority_framing")
        if not isinstance(item, Mapping) or set(item) != {"table", "row"}:
            raise _failure("portable_export_invalid_authority_schema")
        if enforce_declared_order_bytes and tuple(item) != ("table", "row"):
            raise _failure("portable_export_invalid_authority_canonical_bytes")
        table = item["table"]
        row = item["row"]
        if not isinstance(table, str) or table not in rows_by_table or not isinstance(row, Mapping):
            raise _failure("portable_export_invalid_authority_schema")
        table_index = TABLES_IN_ORDER.index(table)
        if table_index < last_table:
            raise _failure("portable_export_invalid_authority_order")
        last_table = table_index
        expected_columns = authority_columns(table, source_schema_version)
        if set(row) != set(expected_columns):
            raise _failure("portable_export_invalid_authority_schema")
        if enforce_declared_order_bytes and tuple(row) != expected_columns:
            raise _failure("portable_export_invalid_authority_canonical_bytes")
        materialized = {column: row[column] for column in expected_columns}
        if (
            enforce_declared_order_bytes
            and canonical_authority_record(table, materialized) != raw_line + b"\n"
        ):
            raise _failure("portable_export_invalid_authority_canonical_bytes")
        key = tuple(materialized[column] for column in TABLE_KEYS[table])
        previous = last_keys.get(table)
        try:
            if previous is not None and key <= previous:
                raise _failure("portable_export_invalid_authority_order")
        except TypeError:
            raise _failure("portable_export_invalid_authority_order") from None
        last_keys[table] = key
        rows_by_table[table].append(materialized)
        records.append({"table": table, "row": materialized})
    return records, rows_by_table


def _validate_authority_closure(
    rows: Mapping[str, Sequence[Mapping[str, object]]], expected_campaign_id: str
) -> None:
    campaigns = rows["campaigns"]
    if len(campaigns) != 1 or campaigns[0]["campaign_id"] != expected_campaign_id:
        raise _failure("portable_export_invalid_authority_closure")
    revision = int(cast(Any, campaigns[0]["revision"]))
    revisions = rows["campaign_revisions"]
    if not revisions or max(int(cast(Any, row["revision"])) for row in revisions) != revision:
        raise _failure("portable_export_invalid_authority_closure")
    for table in TABLES_IN_ORDER:
        for row in rows[table]:
            if "campaign_id" in row and row["campaign_id"] != expected_campaign_id:
                raise _failure("portable_export_invalid_authority_closure")

    def identities(table: str, column: str) -> set[object]:
        return {row[column] for row in rows[table]}

    parents = {
        "operations": identities("campaign_operations", "operation_id"),
        "turns": identities("turns", "turn_id"),
        "locks": identities("campaign_locks", "lock_id"),
        "actions": identities("actions", "action_id"),
        "activations": identities("package_activations", "activation_id"),
        "plans": identities("campaign_plans", "plan_id"),
        "handoffs": identities("builder_dm_handoffs", "handoff_id"),
        "sessions": identities("agent_sessions", "session_id"),
        "participants": identities("participants", "participant_id"),
        "candidates": identities("package_candidates", "candidate_id"),
        "installs": identities("package_staging", "install_id"),
        "event_sequences": identities("canonical_events", "sequence"),
        "attempts": {
            row["attempt_id"] for row in rows["canonical_events"] if row["attempt_id"] is not None
        }
        | {row["failed_attempt_id"] for row in rows["narration_interruptions"]},
    }

    def require(table: str, column: str, parent: str) -> None:
        if any(
            row[column] is not None and row[column] not in parents[parent] for row in rows[table]
        ):
            raise _failure("portable_export_invalid_authority_closure")

    references = (
        ("campaigns", "current_lock_id", "locks"),
        ("campaign_lifecycle_state", "designated_session_id", "sessions"),
        ("campaign_lifecycle_state", "tombstone_prior_session_id", "sessions"),
        ("campaign_lifecycle_transitions", "operation_id", "operations"),
        ("campaign_lifecycle_transitions", "designated_session_id", "sessions"),
        ("campaign_operations", "basis_turn_id", "turns"),
        ("campaign_operations", "basis_lock_id", "locks"),
        ("campaign_constructions", "confirmation_operation_id", "operations"),
        ("campaign_constructions", "begin_operation_id", "operations"),
        ("campaign_constructions", "completion_operation_id", "operations"),
        ("campaign_constructions", "authority_turn_id", "turns"),
        ("campaign_constructions", "authority_lock_id", "locks"),
        ("campaign_continuity_records", "operation_id", "operations"),
        ("campaign_revisions", "action_id", "actions"),
        ("campaign_revisions", "activation_id", "activations"),
        ("campaign_revisions", "plan_id", "plans"),
        ("campaign_revisions", "handoff_id", "handoffs"),
        ("campaign_revisions", "construction_operation_id", "operations"),
        ("campaign_revisions", "lifecycle_operation_id", "operations"),
        ("campaign_revisions", "continuity_operation_id", "operations"),
        ("participant_quarantines", "participant_id", "participants"),
        ("actions", "turn_id", "turns"),
        ("actions", "lock_id", "locks"),
        ("idempotency_records", "action_id", "actions"),
        ("canonical_events", "turn_id", "turns"),
        ("canonical_events", "action_id", "actions"),
        ("narration_interruptions", "turn_id", "turns"),
        ("narration_interruptions", "last_action_id", "actions"),
        ("narration_interruptions", "failed_attempt_id", "attempts"),
        ("narration_interruptions", "resolving_retry_id", "attempts"),
        ("narration_interruptions", "resolved_sequence", "event_sequences"),
        ("mutations", "action_id", "actions"),
        ("consequences", "action_id", "actions"),
        ("campaign_locks", "predecessor_lock_id", "locks"),
        ("campaign_lock_memberships", "lock_id", "locks"),
        ("package_activations", "candidate_id", "candidates"),
        ("package_activations", "old_lock_id", "locks"),
        ("package_activations", "new_lock_id", "locks"),
        ("campaign_plans", "package_lock_id", "locks"),
        ("campaign_plans", "source_session_id", "sessions"),
        ("builder_dm_handoffs", "builder_session_id", "sessions"),
        ("builder_dm_handoffs", "dm_session_id", "sessions"),
        ("builder_dm_handoffs", "package_lock_id", "locks"),
        ("builder_dm_handoffs", "package_candidate_id", "candidates"),
        ("builder_dm_handoffs", "plan_id", "plans"),
        ("builder_dm_handoffs", "completed_turn_id", "turns"),
        ("agent_sessions", "parent_session_id", "sessions"),
        ("agent_session_messages", "session_id", "sessions"),
        ("agent_session_messages", "canonical_sequence", "event_sequences"),
        ("package_candidate_memberships", "candidate_id", "candidates"),
        ("package_candidates", "install_id", "installs"),
    )
    for table, column, parent in references:
        require(table, column, parent)
    if any(row["adaptation_id"] is not None for row in rows["participants"]):
        raise _failure("portable_export_invalid_authority_closure")


def _canonical_package_json(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


_HandoffPackageClosure = tuple[tuple[tuple[str, str, str], str], ...]


def _validate_handoff_package_basis(
    *,
    handoffs: Sequence[tuple[str, str | None]],
    locks: Mapping[str, tuple[str, str, _HandoffPackageClosure]],
    candidates: Mapping[str, tuple[str, str, _HandoffPackageClosure]],
) -> None:
    """Require every handoff candidate to be the exact validated lock closure."""

    for lock_id, candidate_id in handoffs:
        lock = locks.get(lock_id)
        if lock is None or lock[0] != "available":
            raise _failure("portable_export_invalid_package_graph")
        _availability, lock_digest, lock_closure = lock
        if not lock_closure:
            if candidate_id is not None:
                raise _failure("portable_export_invalid_package_graph")
            continue
        candidate = None if candidate_id is None else candidates.get(candidate_id)
        if (
            candidate is None
            or candidate[0] != "validated"
            or candidate[1] != lock_digest
            or candidate[2] != lock_closure
        ):
            raise _failure("portable_export_invalid_package_graph")


def _validate_manifest_derived_package_graph(
    rows: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Rebuild every exported immutable package relation from canonical manifests."""

    manifests: dict[tuple[str, str, str], Any] = {}
    expected: dict[str, list[dict[str, object]]] = {
        table: []
        for table in (
            "packages",
            "package_dependencies",
            "package_manifests",
            "package_manifest_files",
            "package_exact_dependencies",
            "package_capabilities",
            "package_tools",
            "package_prompt_fragments",
            "package_ui_surfaces",
            "package_participant_artifacts",
        )
    }
    try:
        for stored in rows["package_manifests"]:
            canonical = str(stored["canonical_json"])
            decoded = json.loads(
                canonical,
                object_pairs_hook=_reject_duplicate_members,
                parse_constant=_reject_constant,
            )
            if _canonical_package_json(decoded) != canonical:
                raise ValueError("noncanonical package manifest")
            manifest = manifest_from_record(decoded)
            identity = manifest.identity
            if identity in manifests or identity != (
                str(stored["package_id"]),
                str(stored["version"]),
                str(stored["content_sha256"]),
            ):
                raise ValueError("package identity mismatch")
            manifests[identity] = manifest
            package_id, version, content_sha256 = identity
            expected["packages"].append(
                {
                    "package_id": package_id,
                    "version": version,
                    "sha256": content_sha256,
                    "capabilities_json": _canonical_package_json(manifest.capabilities),
                }
            )
            expected["package_manifests"].append(
                {
                    "package_id": package_id,
                    "version": version,
                    "content_sha256": content_sha256,
                    "role": manifest.role.value,
                    "canonical_json": manifest.canonical_json,
                    "metadata_json": _canonical_package_json(manifest.metadata),
                    "provenance_json": _canonical_package_json(manifest.provenance),
                    "compatibility_json": _canonical_package_json(manifest.compatibility),
                    "status": "published",
                }
            )
            for order, package_file in enumerate(manifest.files):
                expected["package_manifest_files"].append(
                    {
                        "package_id": package_id,
                        "version": version,
                        "content_sha256": content_sha256,
                        "file_order": order,
                        "portable_path": package_file.path,
                        "byte_count": package_file.byte_count,
                        "blob_sha256": package_file.sha256,
                    }
                )
            for order, exact_dependency in enumerate(manifest.dependencies):
                expected["package_dependencies"].append(
                    {
                        "package_id": package_id,
                        "version": version,
                        "sha256": content_sha256,
                        "dependency_order": order,
                        "dependency_id": exact_dependency.package_id,
                        "dependency_version": exact_dependency.version,
                        "dependency_sha256": exact_dependency.content_sha256,
                        "dependency_kind": exact_dependency.kind,
                        "dependency_namespace": exact_dependency.namespace,
                    }
                )
                expected["package_exact_dependencies"].append(
                    {
                        "package_id": package_id,
                        "version": version,
                        "content_sha256": content_sha256,
                        "dependency_order": order,
                        "dependency_namespace": exact_dependency.namespace,
                        "dependency_kind": exact_dependency.kind,
                        "dependency_id": exact_dependency.package_id,
                        "dependency_version": exact_dependency.version,
                        "dependency_content_sha256": exact_dependency.content_sha256,
                    }
                )
            for order, capability in enumerate(manifest.capabilities):
                expected["package_capabilities"].append(
                    {
                        "package_id": package_id,
                        "version": version,
                        "content_sha256": content_sha256,
                        "capability_order": order,
                        "capability": capability,
                    }
                )
            for order, tool in enumerate(manifest.tools):
                expected["package_tools"].append(
                    {
                        "package_id": package_id,
                        "version": version,
                        "content_sha256": content_sha256,
                        "tool_order": order,
                        "tool_name": tool.name,
                        "capability": tool.capability,
                        "input_schema_json": _canonical_package_json(tool.input_schema),
                        "state_predicates_json": _canonical_package_json(
                            [
                                {
                                    "namespace": item.namespace,
                                    "key": item.key,
                                    "equals": item.equals,
                                    **({"negated": True} if item.negated else {}),
                                }
                                for item in tool.state_predicates
                            ]
                        ),
                        "turn_statuses_json": _canonical_package_json(tool.turn_statuses),
                        "writers_json": _canonical_package_json(tool.writers),
                    }
                )
            for table, surfaces in (
                ("package_prompt_fragments", manifest.prompt_fragments),
                ("package_ui_surfaces", manifest.ui_surfaces),
            ):
                for order, surface in enumerate(surfaces):
                    expected[table].append(
                        {
                            "package_id": package_id,
                            "version": version,
                            "content_sha256": content_sha256,
                            "surface_order": order,
                            "fragment_id"
                            if table == "package_prompt_fragments"
                            else "surface_id": surface.identity,
                            "capability": surface.capability,
                        }
                    )
            for order, artifact in enumerate(manifest.participant_artifacts):
                expected["package_participant_artifacts"].append(
                    {
                        "package_id": package_id,
                        "version": version,
                        "content_sha256": content_sha256,
                        "artifact_order": order,
                        "kind": artifact.kind,
                        "schema_id": artifact.schema_id,
                        "schema_sha256": artifact.schema_sha256,
                        "schema_json": _canonical_package_json(artifact.schema),
                    }
                )
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _failure("portable_export_invalid_package_graph") from None

    for table, derived in expected.items():
        derived.sort(key=lambda row: tuple(row[column] for column in TABLE_KEYS[table]))
        if list(rows[table]) != derived:
            raise _failure("portable_export_invalid_package_graph")

    identities = set(manifests)
    roots: set[tuple[str, str, str]] = set()
    for table, sha_column in (
        ("campaign_package_memberships", "sha256"),
        ("campaign_lock_memberships", "content_sha256"),
        ("package_candidate_memberships", "content_sha256"),
    ):
        roots.update(
            (
                str(row["package_id"]),
                str(row["version"]),
                str(row[sha_column]),
            )
            for row in rows[table]
        )
    roots.update(
        (
            str(row["package_id"]),
            str(row["package_version"]),
            str(row["package_sha256"]),
        )
        for row in rows["actions"]
    )
    roots.update(
        (
            str(row["package_id"]),
            str(row["package_version"]),
            str(row["package_sha256"]),
        )
        for row in rows["participants"]
        if row["package_id"] is not None
    )
    reachable: set[tuple[str, str, str]] = set()
    pending = list(roots)
    while pending:
        identity = pending.pop()
        if identity in reachable:
            continue
        reachable_manifest = manifests.get(identity)
        if reachable_manifest is None:
            raise _failure("portable_export_invalid_package_graph")
        reachable.add(identity)
        pending.extend(
            (dependency.package_id, dependency.version, dependency.content_sha256)
            for dependency in reachable_manifest.dependencies
        )
    if reachable != identities:
        raise _failure("portable_export_invalid_package_graph")

    lock_rows = {str(row["lock_id"]): row for row in rows["campaign_locks"]}
    candidate_rows = {str(row["candidate_id"]): row for row in rows["package_candidates"]}
    lock_ids = set(lock_rows)
    candidate_ids = set(candidate_rows)
    staging_ids = {str(row["install_id"]) for row in rows["package_staging"]}
    plan_ids = {str(row["plan_id"]) for row in rows["campaign_plans"]}
    session_ids = {str(row["session_id"]) for row in rows["agent_sessions"]}
    if any(str(row["lock_id"]) not in lock_ids for row in rows["campaign_lock_memberships"]):
        raise _failure("portable_export_invalid_package_graph")
    if any(
        str(row["candidate_id"]) not in candidate_ids
        for row in rows["package_candidate_memberships"]
    ):
        raise _failure("portable_export_invalid_package_graph")
    if any(str(row["install_id"]) not in staging_ids for row in rows["package_candidates"]):
        raise _failure("portable_export_invalid_package_graph")
    if any(
        str(row["candidate_id"]) not in candidate_ids
        or str(row["old_lock_id"]) not in lock_ids
        or str(row["new_lock_id"]) not in lock_ids
        for row in rows["package_activations"]
    ):
        raise _failure("portable_export_invalid_package_graph")
    if any(
        str(row["package_lock_id"]) not in lock_ids
        or str(row["source_session_id"]) not in session_ids
        for row in rows["campaign_plans"]
    ):
        raise _failure("portable_export_invalid_package_graph")
    if any(
        str(row["package_lock_id"]) not in lock_ids
        or (
            row["package_candidate_id"] is not None
            and str(row["package_candidate_id"]) not in candidate_ids
        )
        or str(row["plan_id"]) not in plan_ids
        or str(row["builder_session_id"]) not in session_ids
        or str(row["dm_session_id"]) not in session_ids
        for row in rows["builder_dm_handoffs"]
    ):
        raise _failure("portable_export_invalid_package_graph")

    def validate_owned_closure(
        memberships: Sequence[Mapping[str, object]],
        *,
        owner_column: str,
        sha_column: str,
        role_column: str | None = "role",
    ) -> None:
        owners = {str(row[owner_column]) for row in memberships}
        for owner in owners:
            selected = [row for row in memberships if str(row[owner_column]) == owner]
            try:
                resolve_exact_closure(
                    manifests[_package_identity(row, sha_column)] for row in selected
                )
            except (KeyError, ValueError):
                raise _failure("portable_export_invalid_package_graph") from None
            if (
                role_column is not None
                and any(
                    manifests[
                        (
                            str(row["package_id"]),
                            str(row["version"]),
                            str(row[sha_column]),
                        )
                    ].role.value
                    != row[role_column]
                    for row in selected
                )
            ):
                raise _failure("portable_export_invalid_package_graph")

    validate_owned_closure(
        rows["campaign_lock_memberships"],
        owner_column="lock_id",
        sha_column="content_sha256",
    )
    validate_owned_closure(
        rows["package_candidate_memberships"],
        owner_column="candidate_id",
        sha_column="content_sha256",
        role_column=None,
    )

    def closure_digest(
        selected: Sequence[Mapping[str, object]],
        *,
        order_column: str,
        sha_column: str,
    ) -> str:
        ordered = sorted(selected, key=lambda row: int(cast(Any, row[order_column])))
        representation = [
            {
                "id": str(row["package_id"]),
                "version": str(row["version"]),
                "content_sha256": str(row[sha_column]),
                "role": manifests[
                    (
                        str(row["package_id"]),
                        str(row["version"]),
                        str(row[sha_column]),
                    )
                ].role.value,
                "load_order": order,
            }
            for order, row in enumerate(ordered)
        ]
        if [int(cast(Any, row[order_column])) for row in ordered] != list(range(len(ordered))):
            raise _failure("portable_export_invalid_package_graph")
        return hashlib.sha256(canonical_json_bytes(representation)).hexdigest()

    for lock_id, lock_row in lock_rows.items():
        selected = [
            row for row in rows["campaign_lock_memberships"] if str(row["lock_id"]) == lock_id
        ]
        if str(lock_row["closure_sha256"]) != closure_digest(
            selected, order_column="closure_order", sha_column="content_sha256"
        ):
            raise _failure("portable_export_invalid_package_graph")
    for candidate_id, candidate_row in candidate_rows.items():
        selected = [
            row
            for row in rows["package_candidate_memberships"]
            if str(row["candidate_id"]) == candidate_id
        ]
        if str(candidate_row["closure_sha256"]) != closure_digest(
            selected, order_column="closure_order", sha_column="content_sha256"
        ):
            raise _failure("portable_export_invalid_package_graph")

    materialized_locks: dict[str, tuple[str, str, _HandoffPackageClosure]] = {}
    for lock_id, lock_row in lock_rows.items():
        selected = sorted(
            (row for row in rows["campaign_lock_memberships"] if str(row["lock_id"]) == lock_id),
            key=lambda row: int(cast(Any, row["closure_order"])),
        )
        materialized_locks[lock_id] = (
            str(lock_row["availability"]),
            str(lock_row["closure_sha256"]),
            tuple(
                (
                    (
                        str(row["package_id"]),
                        str(row["version"]),
                        str(row["content_sha256"]),
                    ),
                    str(row["role"]),
                )
                for row in selected
            ),
        )
    materialized_candidates: dict[str, tuple[str, str, _HandoffPackageClosure]] = {}
    for candidate_id, candidate_row in candidate_rows.items():
        selected = sorted(
            (
                row
                for row in rows["package_candidate_memberships"]
                if str(row["candidate_id"]) == candidate_id
            ),
            key=lambda row: int(cast(Any, row["closure_order"])),
        )
        materialized_candidates[candidate_id] = (
            str(candidate_row["status"]),
            str(candidate_row["closure_sha256"]),
            tuple(
                (
                    (
                        str(row["package_id"]),
                        str(row["version"]),
                        str(row["content_sha256"]),
                    ),
                    manifests[
                        (
                            str(row["package_id"]),
                            str(row["version"]),
                            str(row["content_sha256"]),
                        )
                    ].role.value,
                )
                for row in selected
            ),
        )
    _validate_handoff_package_basis(
        handoffs=[
            (
                str(row["package_lock_id"]),
                None if row["package_candidate_id"] is None else str(row["package_candidate_id"]),
            )
            for row in rows["builder_dm_handoffs"]
        ],
        locks=materialized_locks,
        candidates=materialized_candidates,
    )

    if any(
        not any(
            str(member["candidate_id"]) == candidate_id
            for member in rows["package_candidate_memberships"]
        )
        for candidate_id in candidate_ids
    ):
        raise _failure("portable_export_invalid_package_graph")


def _validate_manifest_shape_and_bytes(
    manifest: object,
    encoded: bytes,
    *,
    enforce_declared_order_bytes: bool = True,
) -> Mapping[str, object]:
    if not isinstance(manifest, Mapping) or set(manifest) != set(MANIFEST_FIELDS):
        raise _failure("portable_export_invalid_manifest")
    if enforce_declared_order_bytes and tuple(manifest) != MANIFEST_FIELDS:
        raise _failure("portable_export_invalid_manifest_canonical_bytes")
    if enforce_declared_order_bytes and _json_bytes(dict(manifest)) != encoded:
        raise _failure("portable_export_invalid_manifest_canonical_bytes")
    table_counts = manifest["table_row_counts"]
    closure = manifest["closure_index"]
    declared = manifest["non_manifest_members"]
    validate_migration_provenance(
        manifest["migration_provenance"],
        enforce_declared_order_bytes=enforce_declared_order_bytes,
        source_schema_version=manifest["source_authority_schema_version"],
    )
    if not isinstance(table_counts, Mapping) or set(table_counts) != set(TABLES_IN_ORDER):
        raise _failure("portable_export_invalid_manifest")
    if enforce_declared_order_bytes and tuple(table_counts) != TABLES_IN_ORDER:
        raise _failure("portable_export_invalid_manifest_canonical_bytes")
    if not isinstance(closure, Mapping) or set(closure) != set(CLOSURE_INDEX_FIELDS):
        raise _failure("portable_export_invalid_manifest")
    if enforce_declared_order_bytes and tuple(closure) != CLOSURE_INDEX_FIELDS:
        raise _failure("portable_export_invalid_manifest_canonical_bytes")
    packages = closure["package_identities"]
    if not isinstance(packages, list) or any(
        not isinstance(item, Mapping) or set(item) != set(PACKAGE_IDENTITY_FIELDS)
        for item in packages
    ):
        raise _failure("portable_export_invalid_manifest")
    if enforce_declared_order_bytes and any(
        tuple(item) != PACKAGE_IDENTITY_FIELDS for item in packages
    ):
        raise _failure("portable_export_invalid_manifest_canonical_bytes")
    if not isinstance(declared, list) or any(
        not isinstance(item, Mapping) or set(item) != set(NON_MANIFEST_MEMBER_FIELDS)
        for item in declared
    ):
        raise _failure("portable_export_invalid_manifest")
    if enforce_declared_order_bytes and any(
        tuple(item) != NON_MANIFEST_MEMBER_FIELDS for item in declared
    ):
        raise _failure("portable_export_invalid_manifest_canonical_bytes")
    return cast(Mapping[str, object], manifest)


def _stream_member_digest(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    with archive.open(info, "r") as member:
        for chunk in iter_bounded_chunks(cast(BinaryIO, member), limit=info.file_size):
            digest.update(chunk)
            byte_count += len(chunk)
    if byte_count != info.file_size:
        raise _failure("portable_export_invalid_member_size")
    return byte_count, digest.hexdigest()


_PACKAGE_RELATION_TABLES = (
    "packages",
    "package_dependencies",
    "package_manifests",
    "package_manifest_files",
    "package_exact_dependencies",
    "package_capabilities",
    "package_tools",
    "package_prompt_fragments",
    "package_ui_surfaces",
    "package_participant_artifacts",
)

_REFERENCE_SUMMARIES: Mapping[str, tuple[tuple[str, str], ...]] = {
    "campaigns": (("current_lock_id", "locks"),),
    "campaign_lifecycle_state": (
        ("designated_session_id", "sessions"),
        ("tombstone_prior_session_id", "sessions"),
    ),
    "campaign_lifecycle_transitions": (
        ("operation_id", "operations"),
        ("designated_session_id", "sessions"),
    ),
    "campaign_operations": (("basis_turn_id", "turns"), ("basis_lock_id", "locks")),
    "campaign_constructions": (
        ("confirmation_operation_id", "operations"),
        ("begin_operation_id", "operations"),
        ("completion_operation_id", "operations"),
        ("authority_turn_id", "turns"),
        ("authority_lock_id", "locks"),
    ),
    "campaign_continuity_records": (("operation_id", "operations"),),
    "campaign_revisions": (
        ("action_id", "actions"),
        ("activation_id", "activations"),
        ("plan_id", "plans"),
        ("handoff_id", "handoffs"),
        ("construction_operation_id", "operations"),
        ("lifecycle_operation_id", "operations"),
        ("continuity_operation_id", "operations"),
    ),
    "participant_quarantines": (("participant_id", "participants"),),
    "actions": (("turn_id", "turns"), ("lock_id", "locks")),
    "idempotency_records": (("action_id", "actions"),),
    "canonical_events": (("turn_id", "turns"), ("action_id", "actions")),
    "narration_interruptions": (
        ("turn_id", "turns"),
        ("last_action_id", "actions"),
        ("resolving_retry_id", "attempts"),
        ("resolved_sequence", "event_sequences"),
    ),
    "mutations": (("action_id", "actions"),),
    "consequences": (("action_id", "actions"),),
    "campaign_locks": (("predecessor_lock_id", "locks"),),
    "campaign_lock_memberships": (("lock_id", "locks"),),
    "package_activations": (
        ("candidate_id", "candidates"),
        ("old_lock_id", "locks"),
        ("new_lock_id", "locks"),
    ),
    "campaign_plans": (("package_lock_id", "locks"), ("source_session_id", "sessions")),
    "builder_dm_handoffs": (
        ("builder_session_id", "sessions"),
        ("dm_session_id", "sessions"),
        ("package_lock_id", "locks"),
        ("package_candidate_id", "candidates"),
        ("plan_id", "plans"),
        ("completed_turn_id", "turns"),
    ),
    "agent_sessions": (("parent_session_id", "sessions"),),
    "agent_session_messages": (
        ("session_id", "sessions"),
        ("canonical_sequence", "event_sequences"),
    ),
    "package_candidates": (("install_id", "installs"),),
    "package_candidate_memberships": (("candidate_id", "candidates"),),
}


def _new_parent_summary() -> dict[str, set[object]]:
    return {
        name: set()
        for name in (
            "operations",
            "turns",
            "locks",
            "actions",
            "activations",
            "plans",
            "handoffs",
            "sessions",
            "participants",
            "candidates",
            "installs",
            "event_sequences",
            "attempts",
        )
    }


@dataclass(frozen=True, slots=True)
class _PackageSummary:
    graph: PackageGraphNode
    tool_names: frozenset[str]
    participant_artifacts: Mapping[str, tuple[str, str]]
    canonical_sha256: str
    requires_state_ownership: bool


@dataclass(frozen=True, slots=True)
class _StagingSummary:
    status: str
    report_sha256: str
    packages: tuple[tuple[tuple[str, str, str], str], ...]


@dataclass
class _AuthorityScanSummary:
    byte_count: int = 0
    sha256: str = ""
    record_count: int = 0
    table_counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TABLES_IN_ORDER, 0))
    actual_package_digests: dict[str, Any] = field(
        default_factory=lambda: {table: hashlib.sha256() for table in _PACKAGE_RELATION_TABLES}
    )
    expected_package_digests: dict[str, Any] = field(
        default_factory=lambda: {table: hashlib.sha256() for table in _PACKAGE_RELATION_TABLES}
    )
    expected_package_counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_PACKAGE_RELATION_TABLES, 0)
    )
    last_table_index: int = -1
    canonical_bytes_valid: bool = True
    last_keys: dict[str, tuple[object, ...]] = field(default_factory=dict)
    campaign_ids: set[object] = field(default_factory=set)
    campaign_id: object | None = None
    current_lock_id: str | None = None
    campaign_revision: int | None = None
    maximum_revision: int | None = None
    parents: dict[str, set[object]] = field(default_factory=_new_parent_summary)
    required: dict[str, set[object]] = field(default_factory=_new_parent_summary)
    manifests: dict[tuple[str, str, str], _PackageSummary] = field(default_factory=dict)
    roots: set[tuple[str, str, str]] = field(default_factory=set)
    lock_memberships: dict[str, list[tuple[tuple[str, str, str], str]]] = field(
        default_factory=dict
    )
    lock_membership_orders: dict[str, list[int]] = field(default_factory=dict)
    lock_closure_sha256: dict[str, str] = field(default_factory=dict)
    lock_availability: dict[str, str] = field(default_factory=dict)
    candidate_memberships: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    candidate_membership_orders: dict[str, list[int]] = field(default_factory=dict)
    candidate_closure_sha256: dict[str, str] = field(default_factory=dict)
    candidates: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    staging: dict[str, _StagingSummary] = field(default_factory=dict)
    handoff_package_bases: list[tuple[str, str | None]] = field(default_factory=list)
    action_bindings: set[tuple[str, tuple[str, str, str], str]] = field(default_factory=set)
    participant_bindings: set[tuple[tuple[str, str, str], str, str, str]] = field(
        default_factory=set
    )
    participant_has_adaptation: bool = False
    closure_lock_ids: set[str] = field(default_factory=set)
    closure_action_ids: set[str] = field(default_factory=set)
    closure_candidate_ids: set[str] = field(default_factory=set)
    closure_blob_sha256s: set[str] = field(default_factory=set)
    closure_plan_ids: set[str] = field(default_factory=set)
    closure_handoff_ids: set[str] = field(default_factory=set)
    closure_session_ids: set[str] = field(default_factory=set)
    blob_metadata: dict[str, tuple[object, object]] = field(default_factory=dict)
    file_blob_byte_counts: dict[str, set[object]] = field(default_factory=dict)
    namespace_owners: dict[str, str | None] = field(default_factory=dict)
    state_namespaces: set[str] = field(default_factory=set)


def _package_identity(row: Mapping[str, object], sha_column: str) -> tuple[str, str, str]:
    return str(row["package_id"]), str(row["version"]), str(row[sha_column])


def _iter_manifest_relation_rows(manifest: Any) -> Iterator[tuple[str, dict[str, object]]]:
    package_id, version, content_sha256 = manifest.identity
    yield (
        "packages",
        {
            "package_id": package_id,
            "version": version,
            "sha256": content_sha256,
            "capabilities_json": _canonical_package_json(manifest.capabilities),
        },
    )
    yield (
        "package_manifests",
        {
            "package_id": package_id,
            "version": version,
            "content_sha256": content_sha256,
            "role": manifest.role.value,
            "canonical_json": manifest.canonical_json,
            "metadata_json": _canonical_package_json(manifest.metadata),
            "provenance_json": _canonical_package_json(manifest.provenance),
            "compatibility_json": _canonical_package_json(manifest.compatibility),
            "status": "published",
        },
    )
    for order, package_file in enumerate(manifest.files):
        yield (
            "package_manifest_files",
            {
                "package_id": package_id,
                "version": version,
                "content_sha256": content_sha256,
                "file_order": order,
                "portable_path": package_file.path,
                "byte_count": package_file.byte_count,
                "blob_sha256": package_file.sha256,
            },
        )
    for order, dependency in enumerate(manifest.dependencies):
        yield (
            "package_dependencies",
            {
                "package_id": package_id,
                "version": version,
                "sha256": content_sha256,
                "dependency_order": order,
                "dependency_id": dependency.package_id,
                "dependency_version": dependency.version,
                "dependency_sha256": dependency.content_sha256,
                "dependency_kind": dependency.kind,
                "dependency_namespace": dependency.namespace,
            },
        )
        yield (
            "package_exact_dependencies",
            {
                "package_id": package_id,
                "version": version,
                "content_sha256": content_sha256,
                "dependency_order": order,
                "dependency_namespace": dependency.namespace,
                "dependency_kind": dependency.kind,
                "dependency_id": dependency.package_id,
                "dependency_version": dependency.version,
                "dependency_content_sha256": dependency.content_sha256,
            },
        )
    for order, capability in enumerate(manifest.capabilities):
        yield (
            "package_capabilities",
            {
                "package_id": package_id,
                "version": version,
                "content_sha256": content_sha256,
                "capability_order": order,
                "capability": capability,
            },
        )
    for order, tool in enumerate(manifest.tools):
        yield (
            "package_tools",
            {
                "package_id": package_id,
                "version": version,
                "content_sha256": content_sha256,
                "tool_order": order,
                "tool_name": tool.name,
                "capability": tool.capability,
                "input_schema_json": _canonical_package_json(tool.input_schema),
                "state_predicates_json": _canonical_package_json(
                    [
                        {
                            "namespace": item.namespace,
                            "key": item.key,
                            "equals": item.equals,
                            **({"negated": True} if item.negated else {}),
                        }
                        for item in tool.state_predicates
                    ]
                ),
                "turn_statuses_json": _canonical_package_json(tool.turn_statuses),
                "writers_json": _canonical_package_json(tool.writers),
            },
        )
    for table, surfaces in (
        ("package_prompt_fragments", manifest.prompt_fragments),
        ("package_ui_surfaces", manifest.ui_surfaces),
    ):
        for order, surface in enumerate(surfaces):
            yield (
                table,
                {
                    "package_id": package_id,
                    "version": version,
                    "content_sha256": content_sha256,
                    "surface_order": order,
                    "fragment_id" if table == "package_prompt_fragments" else "surface_id": (
                        surface.identity
                    ),
                    "capability": surface.capability,
                },
            )
    for order, artifact in enumerate(manifest.participant_artifacts):
        yield (
            "package_participant_artifacts",
            {
                "package_id": package_id,
                "version": version,
                "content_sha256": content_sha256,
                "artifact_order": order,
                "kind": artifact.kind,
                "schema_id": artifact.schema_id,
                "schema_sha256": artifact.schema_sha256,
                "schema_json": _canonical_package_json(artifact.schema),
            },
        )


def _record_manifest_summary(summary: _AuthorityScanSummary, row: Mapping[str, object]) -> None:
    try:
        canonical = str(row["canonical_json"])
        decoded = json.loads(
            canonical,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_constant,
        )
        if _canonical_package_json(decoded) != canonical:
            raise ValueError("noncanonical package manifest")
        manifest = manifest_from_record(decoded)
        identity = manifest.identity
        if identity in summary.manifests or identity != _package_identity(row, "content_sha256"):
            raise ValueError("package identity mismatch")
        for table, expected_row in _iter_manifest_relation_rows(manifest):
            summary.expected_package_counts[table] += 1
            summary.expected_package_digests[table].update(
                canonical_authority_record(table, expected_row)
            )
        summary.manifests[identity] = _PackageSummary(
            graph=PackageGraphNode.from_manifest(manifest),
            tool_names=frozenset(tool.name for tool in manifest.tools),
            participant_artifacts=MappingProxyType(
                {
                    artifact.kind: (artifact.schema_id, artifact.schema_sha256)
                    for artifact in manifest.participant_artifacts
                }
            ),
            canonical_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            requires_state_ownership=manifest.content_declarations_path is not None,
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        raise _failure("portable_export_invalid_package_graph") from None


def _record_authority_summary(
    summary: _AuthorityScanSummary,
    raw_line: bytes,
    *,
    expected_campaign_id: str,
    source_schema_version: int,
) -> None:
    if not raw_line or len(raw_line) + 1 > LIMITS["single_authority_row_bytes"]:
        raise _failure("portable_export_invalid_authority_framing")
    item = _decode_json(raw_line, "portable_export_invalid_authority_framing")
    if not isinstance(item, Mapping) or set(item) != {"table", "row"}:
        raise _failure("portable_export_invalid_authority_schema")
    if tuple(item) != ("table", "row"):
        summary.canonical_bytes_valid = False
    table = item["table"]
    row = item["row"]
    if (
        not isinstance(table, str)
        or table not in summary.table_counts
        or not isinstance(row, Mapping)
    ):
        raise _failure("portable_export_invalid_authority_schema")
    table_index = TABLES_IN_ORDER.index(table)
    if table_index < summary.last_table_index:
        raise _failure("portable_export_invalid_authority_order")
    summary.last_table_index = table_index
    expected_columns = authority_columns(table, source_schema_version)
    if set(row) != set(expected_columns):
        raise _failure("portable_export_invalid_authority_schema")
    if tuple(row) != expected_columns:
        summary.canonical_bytes_valid = False
    materialized = {column: row[column] for column in expected_columns}
    encoded = canonical_authority_record(table, materialized)
    if encoded != raw_line + b"\n":
        summary.canonical_bytes_valid = False
    key = tuple(materialized[column] for column in TABLE_KEYS[table])
    previous = summary.last_keys.get(table)
    try:
        if previous is not None and key <= previous:
            raise _failure("portable_export_invalid_authority_order")
    except TypeError:
        raise _failure("portable_export_invalid_authority_order") from None
    summary.last_keys[table] = key
    summary.record_count += 1
    summary.table_counts[table] += 1
    if summary.record_count > LIMITS["authority_record_count"]:
        raise _failure("portable_export_limit_authority_record_count")
    if summary.table_counts[table] > LIMITS["single_table_row_count"]:
        raise _failure("portable_export_limit_single_table_row_count")
    if table in summary.actual_package_digests:
        summary.actual_package_digests[table].update(encoded)
    if "campaign_id" in materialized:
        campaign_id = materialized["campaign_id"]
        summary.campaign_ids.add(campaign_id)
        if campaign_id != expected_campaign_id:
            raise _failure("portable_export_invalid_authority_closure")

    parent_values: tuple[tuple[str, object], ...] = ()
    if table == "campaign_operations":
        parent_values = (("operations", materialized["operation_id"]),)
    elif table == "turns":
        parent_values = (("turns", materialized["turn_id"]),)
    elif table == "campaign_locks":
        parent_values = (("locks", materialized["lock_id"]),)
        lock_id = str(materialized["lock_id"])
        summary.closure_lock_ids.add(lock_id)
        summary.lock_closure_sha256[lock_id] = str(materialized["closure_sha256"])
        summary.lock_availability[lock_id] = str(materialized["availability"])
    elif table == "actions":
        parent_values = (("actions", materialized["action_id"]),)
        summary.closure_action_ids.add(str(materialized["action_id"]))
    elif table == "package_activations":
        parent_values = (("activations", materialized["activation_id"]),)
    elif table == "campaign_plans":
        parent_values = (("plans", materialized["plan_id"]),)
        summary.closure_plan_ids.add(str(materialized["plan_id"]))
    elif table == "builder_dm_handoffs":
        parent_values = (("handoffs", materialized["handoff_id"]),)
        summary.closure_handoff_ids.add(str(materialized["handoff_id"]))
        summary.handoff_package_bases.append(
            (
                str(materialized["package_lock_id"]),
                None
                if materialized["package_candidate_id"] is None
                else str(materialized["package_candidate_id"]),
            )
        )
    elif table == "agent_sessions":
        parent_values = (("sessions", materialized["session_id"]),)
        summary.closure_session_ids.add(str(materialized["session_id"]))
    elif table == "participants":
        parent_values = (("participants", materialized["participant_id"]),)
    elif table == "package_candidates":
        parent_values = (("candidates", materialized["candidate_id"]),)
        summary.closure_candidate_ids.add(str(materialized["candidate_id"]))
    elif table == "package_staging":
        parent_values = (("installs", materialized["install_id"]),)
    elif table == "canonical_events":
        parent_values = (("event_sequences", materialized["sequence"]),)
        if materialized["attempt_id"] is not None:
            parent_values += (("attempts", materialized["attempt_id"]),)
    elif table == "narration_interruptions":
        # An interrupted attempt can fail before producing any canonical event.
        # Its durable interruption row is therefore the root for that attempt ID.
        parent_values = (("attempts", materialized["failed_attempt_id"]),)
    for parent, value in parent_values:
        summary.parents[parent].add(value)
    for column, parent in _REFERENCE_SUMMARIES.get(table, ()):
        value = materialized[column]
        if value is not None:
            summary.required[parent].add(value)

    if table == "campaigns":
        summary.campaign_id = materialized["campaign_id"]
        summary.current_lock_id = (
            None
            if materialized["current_lock_id"] is None
            else str(materialized["current_lock_id"])
        )
        summary.campaign_revision = int(cast(Any, materialized["revision"]))
    elif table == "campaign_revisions":
        revision = int(cast(Any, materialized["revision"]))
        summary.maximum_revision = (
            revision
            if summary.maximum_revision is None
            else max(summary.maximum_revision, revision)
        )
    elif table == "package_manifests":
        _record_manifest_summary(summary, materialized)
    elif table == "campaign_package_memberships":
        summary.roots.add(_package_identity(materialized, "sha256"))
    elif table == "campaign_lock_memberships":
        identity = _package_identity(materialized, "content_sha256")
        summary.roots.add(identity)
        summary.lock_memberships.setdefault(str(materialized["lock_id"]), []).append(
            (identity, str(materialized["role"]))
        )
        summary.lock_membership_orders.setdefault(str(materialized["lock_id"]), []).append(
            int(cast(Any, materialized["closure_order"]))
        )
    elif table == "campaign_state_namespace_owners":
        summary.namespace_owners[str(materialized["namespace"])] = (
            None
            if materialized["owner_package_id"] is None
            else str(materialized["owner_package_id"])
        )
    elif table == "campaign_state":
        summary.state_namespaces.add(str(materialized["namespace"]))
    elif table == "package_candidate_memberships":
        identity = _package_identity(materialized, "content_sha256")
        summary.roots.add(identity)
        candidate_id = str(materialized["candidate_id"])
        summary.candidate_memberships.setdefault(candidate_id, []).append(identity)
        summary.candidate_membership_orders.setdefault(candidate_id, []).append(
            int(cast(Any, materialized["closure_order"]))
        )
    elif table == "package_candidates":
        candidate_id = materialized["candidate_id"]
        install_id = materialized["install_id"]
        status = materialized["status"]
        report_json = materialized["report_json"]
        if not all(
            isinstance(value, str) for value in (candidate_id, install_id, status, report_json)
        ):
            raise _failure("portable_export_invalid_package_graph")
        summary.candidates[candidate_id] = (
            install_id,
            hashlib.sha256(report_json.encode("utf-8")).hexdigest(),
            status,
        )
        summary.candidate_closure_sha256[candidate_id] = str(materialized["closure_sha256"])
    elif table == "package_staging":
        install_id = materialized["install_id"]
        status = materialized["status"]
        manifest_json = materialized["manifest_json"]
        report_json = materialized["report_json"]
        if not all(
            isinstance(value, str) for value in (install_id, status, manifest_json, report_json)
        ):
            raise _failure("portable_export_invalid_package_graph")
        try:
            manifest_value = json.loads(
                manifest_json,
                object_pairs_hook=_reject_duplicate_members,
                parse_constant=_reject_constant,
            )
            if (
                not isinstance(manifest_value, Mapping)
                or tuple(manifest_value) != ("format", "packages")
                or manifest_value.get("format") not in (1, 2, 3, 4)
                or not isinstance(manifest_value.get("packages"), Sequence)
                or isinstance(manifest_value.get("packages"), (str, bytes, bytearray))
                or canonical_json_bytes(manifest_value).decode("utf-8") != manifest_json
            ):
                raise ValueError("staging manifest is not canonical")
            packages: list[tuple[tuple[str, str, str], str]] = []
            for package_record in cast(Sequence[object], manifest_value["packages"]):
                if not isinstance(package_record, Mapping):
                    raise ValueError("staging package record is invalid")
                identity_values = (
                    package_record.get("id"),
                    package_record.get("version"),
                    package_record.get("content_sha256"),
                )
                if not all(isinstance(value, str) for value in identity_values):
                    raise ValueError("staging package identity is invalid")
                identity = cast(tuple[str, str, str], identity_values)
                packages.append(
                    (
                        identity,
                        hashlib.sha256(canonical_json_bytes(package_record)).hexdigest(),
                    )
                )
            summary.staging[install_id] = _StagingSummary(
                status=status,
                report_sha256=hashlib.sha256(report_json.encode("utf-8")).hexdigest(),
                packages=tuple(packages),
            )
        except (KeyError, TypeError, ValueError, RecursionError):
            raise _failure("portable_export_invalid_package_graph") from None
    elif table == "actions" and materialized["package_id"] is not None:
        identity = (
            str(materialized["package_id"]),
            str(materialized["package_version"]),
            str(materialized["package_sha256"]),
        )
        summary.roots.add(identity)
        summary.action_bindings.add(
            (str(materialized["lock_id"]), identity, str(materialized["tool_name"]))
        )
    elif table == "participants":
        if materialized["adaptation_id"] is not None:
            summary.participant_has_adaptation = True
        if materialized["package_id"] is not None:
            identity = (
                str(materialized["package_id"]),
                str(materialized["package_version"]),
                str(materialized["package_sha256"]),
            )
            summary.roots.add(identity)
            summary.participant_bindings.add(
                (
                    identity,
                    str(materialized["kind"]),
                    str(materialized["schema_id"]),
                    str(materialized["schema_sha256"]),
                )
            )
    elif table == "package_blobs":
        blob_sha256 = str(materialized["blob_sha256"])
        summary.closure_blob_sha256s.add(blob_sha256)
        summary.blob_metadata[blob_sha256] = (
            materialized["byte_count"],
            materialized["chunk_count"],
        )
    elif table == "package_manifest_files":
        blob_sha256 = str(materialized["blob_sha256"])
        summary.file_blob_byte_counts.setdefault(blob_sha256, set()).add(materialized["byte_count"])


def _scan_authority_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    expected_campaign_id: str,
    source_schema_version: int,
) -> _AuthorityScanSummary:
    summary = _AuthorityScanSummary()
    digest = hashlib.sha256()
    current_line = bytearray()
    with archive.open(info, "r") as member:
        for chunk in iter_bounded_chunks(cast(BinaryIO, member), limit=info.file_size):
            digest.update(chunk)
            summary.byte_count += len(chunk)
            current_line.extend(chunk)
            while True:
                newline = current_line.find(b"\n")
                if newline < 0:
                    if len(current_line) + 1 > LIMITS["single_authority_row_bytes"]:
                        raise _failure("portable_export_invalid_authority_framing")
                    break
                raw_line = bytes(current_line[:newline])
                del current_line[: newline + 1]
                if b"\r" in raw_line:
                    raise _failure("portable_export_invalid_authority_framing")
                _record_authority_summary(
                    summary,
                    raw_line,
                    expected_campaign_id=expected_campaign_id,
                    source_schema_version=source_schema_version,
                )
    if summary.byte_count != info.file_size:
        raise _failure("portable_export_invalid_member_size")
    if current_line or summary.record_count == 0:
        raise _failure("portable_export_invalid_authority_framing")
    summary.sha256 = digest.hexdigest()
    return summary


def _validate_owned_memberships(
    summary: _AuthorityScanSummary,
    memberships: Mapping[str, Sequence[tuple[str, str, str]]],
    *,
    roles: Mapping[str, Sequence[str]] | None = None,
) -> None:
    for owner, selected in memberships.items():
        try:
            resolve_package_graph(summary.manifests[identity].graph for identity in selected)
        except (KeyError, ValueError):
            raise _failure("portable_export_invalid_package_graph") from None
        if roles is not None and any(
            summary.manifests[identity].graph.role.value != role
            for identity, role in zip(selected, roles[owner], strict=True)
        ):
            raise _failure("portable_export_invalid_package_graph")


def _validate_streamed_authority(
    summary: _AuthorityScanSummary,
    *,
    expected_campaign_id: str,
) -> None:
    if (
        summary.table_counts["campaigns"] != 1
        or summary.campaign_id != expected_campaign_id
        or summary.campaign_revision is None
        or summary.maximum_revision != summary.campaign_revision
    ):
        raise _failure("portable_export_invalid_authority_closure")
    if any(summary.required[parent] - values for parent, values in summary.parents.items()):
        raise _failure("portable_export_invalid_authority_closure")
    if summary.participant_has_adaptation:
        raise _failure("portable_export_invalid_authority_closure")
    for table in _PACKAGE_RELATION_TABLES:
        if (
            summary.table_counts[table] != summary.expected_package_counts[table]
            or summary.actual_package_digests[table].hexdigest()
            != summary.expected_package_digests[table].hexdigest()
        ):
            raise _failure("portable_export_invalid_package_graph")

    identities = set(summary.manifests)
    reachable: set[tuple[str, str, str]] = set()
    pending = list(summary.roots)
    while pending:
        identity = pending.pop()
        if identity in reachable:
            continue
        manifest = summary.manifests.get(identity)
        if manifest is None:
            raise _failure("portable_export_invalid_package_graph")
        reachable.add(identity)
        pending.extend(
            (dependency.package_id, dependency.version, dependency.content_sha256)
            for dependency in manifest.graph.dependencies
        )
    if reachable != identities:
        raise _failure("portable_export_invalid_package_graph")
    if (
        not set(summary.lock_memberships).issubset(summary.closure_lock_ids)
        or set(summary.lock_closure_sha256) != summary.closure_lock_ids
        or set(summary.lock_availability) != summary.closure_lock_ids
        or set(summary.candidate_memberships) != summary.closure_candidate_ids
        or set(summary.candidate_closure_sha256) != summary.closure_candidate_ids
    ):
        raise _failure("portable_export_invalid_package_graph")
    lock_identities = {
        owner: [identity for identity, _role in memberships]
        for owner, memberships in summary.lock_memberships.items()
    }
    lock_roles = {
        owner: [role for _identity, role in memberships]
        for owner, memberships in summary.lock_memberships.items()
    }
    _validate_owned_memberships(summary, lock_identities, roles=lock_roles)
    _validate_owned_memberships(summary, summary.candidate_memberships)
    if summary.current_lock_id is None:
        current_identities: list[tuple[str, str, str]] = []
    else:
        if summary.current_lock_id not in summary.closure_lock_ids:
            raise _failure("portable_export_invalid_authority_closure")
        current_identities = lock_identities.get(summary.current_lock_id, [])
    ownership_required = any(
        summary.manifests[identity].requires_state_ownership for identity in current_identities
    )
    package_ids = tuple(identity[0] for identity in current_identities)
    if ownership_required and set(summary.namespace_owners) != summary.state_namespaces:
        raise _failure("portable_export_invalid_authority_closure")
    if summary.namespace_owners and (
        set(summary.namespace_owners) != summary.state_namespaces
        or any(
            not owner_matches_package_lock(package_ids, namespace, owner)
            for namespace, owner in summary.namespace_owners.items()
        )
    ):
        raise _failure("portable_export_invalid_authority_closure")

    def streamed_closure_digest(
        selected: Sequence[tuple[str, str, str]],
        roles: Sequence[str],
        orders: Sequence[int],
    ) -> str:
        if list(orders) != list(range(len(selected))) or len(roles) != len(selected):
            raise _failure("portable_export_invalid_package_graph")
        representation = [
            {
                "id": identity[0],
                "version": identity[1],
                "content_sha256": identity[2],
                "role": role,
                "load_order": order,
            }
            for order, (identity, role) in enumerate(zip(selected, roles, strict=True))
        ]
        return hashlib.sha256(canonical_json_bytes(representation)).hexdigest()

    for lock_id in summary.closure_lock_ids:
        selected = lock_identities.get(lock_id, [])
        roles = lock_roles.get(lock_id, [])
        orders = summary.lock_membership_orders.get(lock_id, [])
        if summary.lock_closure_sha256[lock_id] != streamed_closure_digest(selected, roles, orders):
            raise _failure("portable_export_invalid_package_graph")
    for candidate_id, selected in summary.candidate_memberships.items():
        roles = [summary.manifests[identity].graph.role.value for identity in selected]
        orders = summary.candidate_membership_orders.get(candidate_id, [])
        if summary.candidate_closure_sha256[candidate_id] != streamed_closure_digest(
            selected, roles, orders
        ):
            raise _failure("portable_export_invalid_package_graph")

    streamed_locks = {
        lock_id: (
            summary.lock_availability[lock_id],
            summary.lock_closure_sha256[lock_id],
            tuple(summary.lock_memberships.get(lock_id, [])),
        )
        for lock_id in summary.closure_lock_ids
    }
    streamed_candidates = {
        candidate_id: (
            summary.candidates[candidate_id][2],
            summary.candidate_closure_sha256[candidate_id],
            tuple(
                (identity, summary.manifests[identity].graph.role.value)
                for identity in summary.candidate_memberships[candidate_id]
            ),
        )
        for candidate_id in summary.closure_candidate_ids
    }
    _validate_handoff_package_basis(
        handoffs=summary.handoff_package_bases,
        locks=streamed_locks,
        candidates=streamed_candidates,
    )

    if len(summary.candidates) != len(summary.staging):
        raise _failure("portable_export_invalid_package_graph")
    selected_installs: set[str] = set()
    for candidate_id, (install_id, report_sha256, _status) in summary.candidates.items():
        if install_id in selected_installs:
            raise _failure("portable_export_invalid_package_graph")
        selected_installs.add(install_id)
        staging = summary.staging.get(install_id)
        memberships = summary.candidate_memberships.get(candidate_id)
        membership_orders = summary.candidate_membership_orders.get(candidate_id)
        if (
            staging is None
            or staging.status != "published"
            or staging.report_sha256 != report_sha256
            or memberships is None
            or membership_orders != list(range(len(memberships)))
            or tuple(identity for identity, _digest in staging.packages) != tuple(memberships)
            or any(
                summary.manifests.get(identity) is None
                or summary.manifests[identity].canonical_sha256 != digest
                for identity, digest in staging.packages
            )
        ):
            raise _failure("portable_export_invalid_package_graph")
    if selected_installs != set(summary.staging):
        raise _failure("portable_export_invalid_package_graph")
    for lock_id, identity, tool_name in summary.action_bindings:
        manifest = summary.manifests.get(identity)
        lock_memberships = summary.lock_memberships.get(lock_id)
        if (
            lock_memberships is None
            or not any(member_identity == identity for member_identity, _role in lock_memberships)
            or manifest is None
            or tool_name not in manifest.tool_names
        ):
            raise _failure("portable_export_invalid_package_graph")
    for identity, kind, schema_id, schema_sha256 in summary.participant_bindings:
        manifest = summary.manifests.get(identity)
        if manifest is None or manifest.participant_artifacts.get(kind) != (
            schema_id,
            schema_sha256,
        ):
            raise _failure("portable_export_invalid_package_graph")


def _streamed_closure_index(summary: _AuthorityScanSummary) -> dict[str, object]:
    return {
        "lock_ids": sorted(summary.closure_lock_ids),
        "action_ids": sorted(summary.closure_action_ids),
        "candidate_ids": sorted(summary.closure_candidate_ids),
        "package_identities": [
            {"package_id": package_id, "version": version, "content_sha256": content_sha256}
            for package_id, version, content_sha256 in sorted(summary.manifests)
        ],
        "blob_sha256s": sorted(summary.closure_blob_sha256s),
        "plan_ids": sorted(summary.closure_plan_ids),
        "handoff_ids": sorted(summary.closure_handoff_ids),
        "session_ids": sorted(summary.closure_session_ids),
    }


def validate_campaign_archive(path: Path, *, expected_campaign_id: str) -> Mapping[str, object]:
    """Boundedly validate one writer output without any recovery/insertion behavior."""

    try:
        archive = zipfile.ZipFile(path, "r", allowZip64=True)
    except BaseException:
        raise _failure("portable_export_invalid_zip") from None
    try:
        infos = archive.infolist()
        if len(infos) > LIMITS["member_count"]:
            raise _failure("portable_export_limit_member_count")
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            raise _failure("portable_export_invalid_duplicate_member")
        casefold_aliases = [name.casefold() for name in names]
        nfc_aliases = [unicodedata.normalize("NFC", name).casefold() for name in names]
        if len(set(casefold_aliases)) != len(casefold_aliases) or len(set(nfc_aliases)) != len(
            nfc_aliases
        ):
            raise _failure("portable_export_invalid_member_alias")
        if any(not _safe_member_name(name) for name in names):
            raise _failure("portable_export_invalid_member_path")
        total = sum(int(info.file_size) for info in infos)
        checked_archive_ledger(member_count=len(infos), total_uncompressed_bytes=total)
        if archive.comment != b"":
            raise _failure("portable_export_invalid_zip_metadata")
        if not names or names[0] != "authority.jsonl" or names[-1] != "manifest.json":
            raise _failure("portable_export_invalid_member_order")
        private_info = next(
            (info for info in infos if info.filename == private_records_archive.MEMBER_NAME), None,
        )
        if private_info is not None and infos[-2] is not private_info:
            raise _failure("portable_export_invalid_member_order")
        blob_infos = infos[1:-2] if private_info is not None else infos[1:-1]
        blob_names = [info.filename for info in blob_infos]
        if blob_names != sorted(blob_names):
            raise _failure("portable_export_invalid_member_order")
        if any(not re.fullmatch(r"blobs/[0-9a-f]{64}", name) for name in blob_names):
            raise _failure("portable_export_invalid_undeclared_member")
        for info in infos:
            _validate_zip_metadata(info)
        if infos[-1].file_size > LIMITS["manifest_bytes"]:
            raise _failure("portable_export_limit_manifest_bytes")
        if infos[0].file_size > LIMITS["authority_jsonl_bytes"]:
            raise _failure("portable_export_limit_authority_jsonl_bytes")
        if any(info.file_size > LIMITS["single_blob_bytes"] for info in blob_infos):
            raise _failure("portable_export_limit_single_blob_bytes")

        encoded_manifest = _read_member(archive, infos[-1])
        decoded_manifest = _decode_json(encoded_manifest, "portable_export_invalid_manifest")
        manifest = _validate_manifest_shape_and_bytes(
            decoded_manifest,
            encoded_manifest,
            enforce_declared_order_bytes=False,
        )
        authority = _scan_authority_member(
            archive,
            infos[0],
            expected_campaign_id=expected_campaign_id,
            source_schema_version=int(cast(Any, manifest["source_authority_schema_version"])),
        )
        if private_info is not None:
            if private_info.file_size > private_records_archive.MAX_BYTES:
                raise _failure("portable_export_invalid_private_records")
            private_records_archive.decode(
                _read_member(archive, private_info), session_ids=authority.closure_session_ids,
            )
        manifest_canonical_bytes_valid = True
        try:
            _validate_manifest_shape_and_bytes(decoded_manifest, encoded_manifest)
        except CommandRejected as error:
            if error.code != "portable_export_invalid_manifest_canonical_bytes":
                raise
            manifest_canonical_bytes_valid = False
        declared = cast(Sequence[Mapping[str, object]], manifest["non_manifest_members"])
        expected_non_manifest = names[:-1]
        declared_names = [str(item["name"]) for item in declared]
        if declared_names != expected_non_manifest:
            raise _failure("portable_export_invalid_undeclared_member")
        info_by_name = {info.filename: info for info in infos}
        streamed_blobs: dict[str, tuple[int, str]] = {}
        for item in declared:
            name = str(item["name"])
            info = info_by_name[name]
            if name == "authority.jsonl":
                byte_count = authority.byte_count
                digest = authority.sha256
            else:
                byte_count, digest = _stream_member_digest(archive, info)
                if name.startswith("blobs/"):
                    streamed_blobs[name.removeprefix("blobs/")] = (byte_count, digest)
            if str(item["sha256"]) != digest:
                raise _failure("portable_export_invalid_member_hash")
            if int(cast(Any, item["byte_count"])) != byte_count:
                raise _failure("portable_export_invalid_member_size")

        _validate_streamed_authority(authority, expected_campaign_id=expected_campaign_id)
        expected_blobs = set(authority.blob_metadata)
        member_blobs = {name.removeprefix("blobs/") for name in blob_names}
        file_blobs = set(authority.file_blob_byte_counts)
        if expected_blobs != member_blobs or expected_blobs != file_blobs:
            raise _failure("portable_export_invalid_blob_closure")
        for blob_sha256, metadata in authority.blob_metadata.items():
            byte_count, streamed_sha256 = streamed_blobs[blob_sha256]
            declared_bytes, declared_chunks = metadata
            if (
                not isinstance(declared_bytes, int)
                or isinstance(declared_bytes, bool)
                or not isinstance(declared_chunks, int)
                or isinstance(declared_chunks, bool)
                or declared_bytes != byte_count
                or declared_chunks != (byte_count + STREAM_CHUNK_BYTES - 1) // STREAM_CHUNK_BYTES
                or streamed_sha256 != blob_sha256
                or authority.file_blob_byte_counts[blob_sha256] != {byte_count}
            ):
                raise _failure("portable_export_invalid_blob_closure")
        if not authority.canonical_bytes_valid:
            raise _failure("portable_export_invalid_authority_canonical_bytes")
        if not manifest_canonical_bytes_valid:
            raise _failure("portable_export_invalid_manifest_canonical_bytes")
        expected_format = PRIVATE_FORMAT_VERSION if private_info is not None else FORMAT_VERSION
        if manifest["format_id"] != FORMAT_ID or manifest["format_version"] != expected_format:
            raise _failure("portable_export_invalid_manifest")
        if (
            manifest["product_id"] != PRODUCT_ID
            or manifest["product_generation"] != PRODUCT_GENERATION
        ):
            raise _failure("portable_export_invalid_manifest")
        if (
            manifest["source_authority_schema_version"]
            not in {
                PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION,
                PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION,
                PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION,
                SOURCE_AUTHORITY_SCHEMA_VERSION,
            }
            or manifest["source_authority_schema_identity_sha256"]
            != source_authority_schema_claim_sha256(
                int(cast(Any, manifest["source_authority_schema_version"]))
            )
        ):
            raise _failure("portable_export_invalid_source_schema")
        if manifest["campaign_id"] != expected_campaign_id:
            raise _failure("portable_export_invalid_authority_closure")
        if int(cast(Any, manifest["captured_revision"])) != authority.campaign_revision:
            raise _failure("portable_export_invalid_authority_closure")
        if (
            manifest["table_row_counts"] != authority.table_counts
            or manifest["authority_record_count"] != authority.record_count
        ):
            raise _failure("portable_export_invalid_authority_closure")
        if manifest["blob_count"] != len(member_blobs):
            raise _failure("portable_export_invalid_blob_closure")
        if manifest["closure_index"] != _streamed_closure_index(authority):
            raise _failure("portable_export_invalid_authority_closure")
        return manifest
    except CommandRejected:
        raise
    except BaseException:
        raise _failure("portable_export_invalid_archive") from None
    finally:
        with suppress(BaseException):
            archive.close()


class PortableRecoveryFailure(CommandRejected):
    """Stable path-free recovery staging failure with cleanup diagnostics."""

    def __init__(
        self,
        code: str,
        *,
        detail: str | None = None,
        supplemental_codes: tuple[str, ...] = (),
    ) -> None:
        message = f"Portable campaign recovery failed ({code})"
        if detail is not None:
            message = f"{message}: {detail}"
        super().__init__(message, code=code)
        self.supplemental_codes = supplemental_codes

    def with_cleanup_failure(self) -> PortableRecoveryFailure:
        return PortableRecoveryFailure(
            self.code,
            supplemental_codes=tuple(
                dict.fromkeys((*self.supplemental_codes, "portable_recovery_cleanup_incomplete"))
            ),
        )


@dataclass(frozen=True, slots=True)
class StagedCampaignArchive:
    """Sealed validated W11 bytes and compact always-disk recovery indexes."""

    campaign_id: str
    captured_revision: int
    archive_sha256: str
    byte_count: int
    manifest: Mapping[str, object]
    _raw_archive: BinaryIO = field(repr=False, compare=False)
    _authority_spool: BinaryIO = field(repr=False, compare=False)
    _blob_spool: BinaryIO = field(repr=False, compare=False)
    _table_ranges: Mapping[str, tuple[int, int, int]] = field(repr=False, compare=False)
    _blob_ranges: Mapping[str, tuple[int, int]] = field(repr=False, compare=False)

    def iter_rows(self, table: str) -> Iterator[Mapping[str, object]]:
        """Read validated rows, adding absent legacy session preferences for current storage."""

        if table not in TABLE_COLUMNS:
            raise ValueError("Recovery requested an unknown frozen table")
        offset, byte_count, expected_count = self._table_ranges.get(table, (0, 0, 0))
        try:
            self._authority_spool.seek(offset)
        except Exception:
            raise PortableRecoveryFailure("portable_recovery_spool_read_failed") from None
        remaining = byte_count
        seen = 0
        while remaining:
            try:
                raw_line = self._authority_spool.readline(
                    min(remaining, LIMITS["single_authority_row_bytes"])
                )
            except Exception:
                raise PortableRecoveryFailure("portable_recovery_spool_read_failed") from None
            if not raw_line or len(raw_line) > remaining or not raw_line.endswith(b"\n"):
                raise PortableRecoveryFailure("portable_export_invalid_authority_framing")
            remaining -= len(raw_line)
            item = _decode_json(raw_line[:-1], "portable_export_invalid_authority_framing")
            if not isinstance(item, Mapping) or item.get("table") != table:
                raise PortableRecoveryFailure("portable_export_invalid_authority_schema")
            row = item.get("row")
            if not isinstance(row, Mapping):
                raise PortableRecoveryFailure("portable_export_invalid_authority_schema")
            if (
                table == "agent_sessions"
                and int(cast(Any, self.manifest["source_authority_schema_version"]))
                <= PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION
            ):
                row = {**row, "reasoning_effort": None, "reasoning_effort_once": None}
            seen += 1
            yield row
        if seen != expected_count:
            raise PortableRecoveryFailure("portable_export_invalid_authority_closure")

    def iter_blob(self, blob_sha256: str) -> Iterator[bytes]:
        """Yield one validated blob in the frozen package chunk size."""

        try:
            offset, byte_count = self._blob_ranges[blob_sha256]
        except KeyError:
            raise PortableRecoveryFailure("portable_export_invalid_blob_closure") from None
        try:
            self._blob_spool.seek(offset)
        except Exception:
            raise PortableRecoveryFailure("portable_recovery_spool_read_failed") from None
        remaining = byte_count
        while remaining:
            try:
                chunk = self._blob_spool.read(min(STREAM_CHUNK_BYTES, remaining))
            except Exception:
                raise PortableRecoveryFailure("portable_recovery_spool_read_failed") from None
            if not chunk:
                raise PortableRecoveryFailure("portable_export_invalid_blob_closure")
            remaining -= len(chunk)
            yield chunk

    def private_records(self) -> tuple[dict[str, object], ...]:
        """Read the optional sealed member only for explicit recovery, not ordinary history."""
        if self.manifest["format_version"] != PRIVATE_FORMAT_VERSION:
            return ()
        self._raw_archive.seek(0)
        try:
            with zipfile.ZipFile(self._raw_archive, "r") as archive:
                info = archive.getinfo(private_records_archive.MEMBER_NAME)
                if info.file_size > private_records_archive.MAX_BYTES:
                    raise PortableRecoveryFailure("portable_export_invalid_private_records")
                return private_records_archive.decode(
                    _read_member(archive, info),
                    session_ids={
                        str(row["session_id"]) for row in self.iter_rows("agent_sessions")
                    },
                )
        finally:
            self._raw_archive.seek(0)

    @property
    def table_counts(self) -> Mapping[str, int]:
        return MappingProxyType({table: values[2] for table, values in self._table_ranges.items()})

    @property
    def blob_sha256s(self) -> tuple[str, ...]:
        return tuple(self._blob_ranges)


def _recovery_zip_preflight(raw: BinaryIO, byte_count: int) -> None:
    """Bound central-directory allocation before constructing ``ZipFile``."""

    tail_bytes = min(byte_count, 65_557 + 56 + 20)
    raw.seek(byte_count - tail_bytes)
    tail = raw.read(tail_bytes)
    eocd_index = tail.rfind(b"PK\x05\x06")
    if eocd_index < 0 or eocd_index + 22 != len(tail):
        raise PortableRecoveryFailure("portable_export_invalid_zip_metadata")
    eocd_offset = byte_count - tail_bytes + eocd_index
    central_end = eocd_offset
    try:
        (
            _signature,
            disk_number,
            central_disk,
            entries_on_disk,
            entry_count,
            central_bytes,
            central_offset,
            comment_bytes,
        ) = struct.unpack_from("<4s4H2LH", tail, eocd_index)
    except struct.error:
        raise PortableRecoveryFailure("portable_export_invalid_zip_metadata") from None
    if (
        disk_number != 0
        or central_disk != 0
        or entries_on_disk != entry_count
        or comment_bytes != 0
    ):
        raise PortableRecoveryFailure("portable_export_invalid_zip_metadata")

    has_zip64_locator = (
        eocd_index >= 20 and tail[eocd_index - 20:eocd_index - 16] == b"PK\x06\x07"
    )
    if (
        has_zip64_locator
        or entry_count == 0xFFFF
        or entries_on_disk == 0xFFFF
        or central_bytes == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    ):
        locator_index = eocd_index - 20
        if locator_index < 0:
            raise PortableRecoveryFailure("portable_export_invalid_zip_metadata")
        locator = tail[locator_index:eocd_index]
        try:
            locator_signature, zip64_disk, zip64_offset, disk_count = struct.unpack(
                "<4sLQL", locator
            )
        except struct.error:
            raise PortableRecoveryFailure("portable_export_invalid_zip_metadata") from None
        if locator_signature != b"PK\x06\x07" or zip64_disk != 0 or disk_count != 1:
            raise PortableRecoveryFailure("portable_export_invalid_zip_metadata")
        raw.seek(zip64_offset)
        record = raw.read(56)
        try:
            (
                zip64_signature,
                record_size,
                _made_by,
                _required,
                disk_number,
                central_disk,
                entries_on_disk,
                entry_count,
                central_bytes,
                central_offset,
            ) = struct.unpack("<4sQ2H2L4Q", record)
        except struct.error:
            raise PortableRecoveryFailure("portable_export_invalid_zip_metadata") from None
        if (
            zip64_signature != b"PK\x06\x06"
            or record_size < 44
            or disk_number != 0
            or central_disk != 0
            or entries_on_disk != entry_count
            or zip64_offset + 12 + record_size != eocd_offset - 20
        ):
            raise PortableRecoveryFailure("portable_export_invalid_zip_metadata")
        central_end = zip64_offset

    if entry_count > LIMITS["member_count"]:
        raise PortableRecoveryFailure("portable_export_limit_member_count")
    if central_bytes > RECOVERY_MAX_CENTRAL_DIRECTORY_BYTES:
        raise PortableRecoveryFailure("portable_recovery_limit_central_directory_bytes")
    if central_offset < 0 or central_bytes < 0 or central_offset + central_bytes != central_end:
        raise PortableRecoveryFailure("portable_export_invalid_zip_metadata")
    try:
        preflight_zip_directory(
            raw, maximum_members=LIMITS["member_count"],
            maximum_directory_bytes=RECOVERY_MAX_CENTRAL_DIRECTORY_BYTES,
        )
    except ValueError:
        raise PortableRecoveryFailure("portable_export_invalid_zip_metadata") from None
    raw.seek(0)


def _discover_recovery_manifest(raw: BinaryIO) -> tuple[str, int]:
    try:
        archive = zipfile.ZipFile(raw, "r", allowZip64=True)
    except BaseException:
        raise PortableRecoveryFailure("portable_export_invalid_zip") from None
    try:
        infos = archive.infolist()
        if not infos or len(infos) > LIMITS["member_count"]:
            raise PortableRecoveryFailure("portable_export_invalid_zip")
        if any(
            len(info.filename.encode("utf-8")) > RECOVERY_MAX_MEMBER_NAME_BYTES for info in infos
        ):
            raise PortableRecoveryFailure("portable_export_invalid_member_path")
        if infos[-1].filename != "manifest.json" or infos[-1].file_size > LIMITS["manifest_bytes"]:
            raise PortableRecoveryFailure("portable_export_invalid_manifest")
        for info in infos:
            _validate_zip_metadata(info)
        encoded = _read_member(archive, infos[-1])
        manifest = _decode_json(encoded, "portable_export_invalid_manifest")
        if not isinstance(manifest, Mapping):
            raise PortableRecoveryFailure("portable_export_invalid_manifest")
        campaign_id = manifest.get("campaign_id")
        captured_revision = manifest.get("captured_revision")
        if (
            not isinstance(campaign_id, str)
            or not campaign_id
            or not isinstance(captured_revision, int)
            or isinstance(captured_revision, bool)
            or captured_revision < 0
        ):
            raise PortableRecoveryFailure("portable_export_invalid_manifest")
        return campaign_id, captured_revision
    finally:
        with suppress(BaseException):
            archive.close()
        raw.seek(0)


def _write_recovery_spool(handle: BinaryIO, data: bytes) -> None:
    try:
        written = handle.write(data)
        if written != len(data):
            raise OSError("short recovery spool write")
    except BaseException:
        raise PortableRecoveryFailure("portable_recovery_spool_write_failed") from None


def _stage_recovery_members(
    raw: BinaryIO,
    authority_spool: BinaryIO,
    blob_spool: BinaryIO,
) -> tuple[Mapping[str, tuple[int, int, int]], Mapping[str, tuple[int, int]]]:
    raw.seek(0)
    archive = zipfile.ZipFile(raw, "r", allowZip64=True)
    table_ranges: dict[str, tuple[int, int, int]] = {}
    blob_ranges: dict[str, tuple[int, int]] = {}
    try:
        infos = archive.infolist()
        with archive.open(infos[0], "r") as authority:
            for raw_line in authority:
                if not raw_line.endswith(b"\n"):
                    raise PortableRecoveryFailure("portable_export_invalid_authority_framing")
                item = _decode_json(raw_line[:-1], "portable_export_invalid_authority_framing")
                if not isinstance(item, Mapping) or not isinstance(item.get("table"), str):
                    raise PortableRecoveryFailure("portable_export_invalid_authority_schema")
                table = str(item["table"])
                start = authority_spool.tell()
                _write_recovery_spool(authority_spool, raw_line)
                previous = table_ranges.get(table)
                if previous is None:
                    table_ranges[table] = (start, len(raw_line), 1)
                else:
                    offset, length, count = previous
                    if start != offset + length:
                        raise PortableRecoveryFailure("portable_export_invalid_authority_order")
                    table_ranges[table] = (offset, length + len(raw_line), count + 1)
        for info in infos[1:-1]:
            if info.filename == private_records_archive.MEMBER_NAME:
                continue
            blob_sha256 = info.filename.removeprefix("blobs/")
            offset = blob_spool.tell()
            byte_count = 0
            with archive.open(info, "r") as member:
                while True:
                    chunk = member.read(STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    _write_recovery_spool(blob_spool, chunk)
                    byte_count += len(chunk)
            blob_ranges[blob_sha256] = (offset, byte_count)
        authority_spool.flush()
        blob_spool.flush()
        authority_spool.seek(0)
        blob_spool.seek(0)
        return MappingProxyType(table_ranges), MappingProxyType(blob_ranges)
    finally:
        archive.close()
        raw.seek(0)


@contextmanager
def stage_campaign_archive(path: Path) -> Iterator[StagedCampaignArchive]:
    """Open once, seal, validate, and spool one W11 archive for W12 recovery."""

    handles: list[BinaryIO] = []
    primary: BaseException | None = None
    try:
        try:
            source = open(path, "rb")  # noqa: SIM115 - centralized cleanup diagnostics
        except BaseException:
            raise PortableRecoveryFailure("portable_recovery_source_open_failed") from None
        with source:
            try:
                source_stat = os.fstat(source.fileno())
            except BaseException:
                raise PortableRecoveryFailure("portable_recovery_source_stat_failed") from None
            if (
                source_stat.st_size < 0
                or not stat.S_ISREG(source_stat.st_mode)
                or source_stat.st_size > RECOVERY_MAX_ARCHIVE_BYTES
            ):
                raise PortableRecoveryFailure("portable_recovery_limit_archive_bytes")
            try:
                raw = cast(
                    BinaryIO,
                    tempfile.TemporaryFile("w+b"),  # noqa: SIM115
                )
            except BaseException:
                raise PortableRecoveryFailure("portable_recovery_spool_create_failed") from None
            handles.append(raw)
            digest = hashlib.sha256()
            byte_count = 0
            while byte_count < RECOVERY_MAX_ARCHIVE_BYTES:
                try:
                    chunk = source.read(
                        min(STREAM_CHUNK_BYTES, RECOVERY_MAX_ARCHIVE_BYTES - byte_count)
                    )
                except BaseException:
                    raise PortableRecoveryFailure("portable_recovery_source_read_failed") from None
                if not chunk:
                    break
                byte_count += len(chunk)
                digest.update(chunk)
                _write_recovery_spool(raw, chunk)
            try:
                overflow = source.read(1)
            except BaseException:
                raise PortableRecoveryFailure("portable_recovery_source_read_failed") from None
            if overflow:
                raise PortableRecoveryFailure("portable_recovery_limit_archive_bytes")
            try:
                raw.flush()
            except BaseException:
                raise PortableRecoveryFailure("portable_recovery_spool_write_failed") from None
        raw.seek(0)
        try:
            _recovery_zip_preflight(raw, byte_count)
            campaign_id, captured_revision = _discover_recovery_manifest(raw)
        except CommandRejected as error:
            if error.code.startswith("portable_export_"):
                raise PortableRecoveryFailure(
                    error.code,
                    detail="Campaign file is invalid or unsupported. Try another export.",
                ) from None
            raise
        try:
            manifest = validate_campaign_archive(
                cast(Path, raw),
                expected_campaign_id=campaign_id,
            )
        except CommandRejected as error:
            raise PortableRecoveryFailure(
                error.code,
                detail="Campaign file is invalid or unsupported. Try another export.",
            ) from None
        raw.seek(0)
        try:
            authority_spool = cast(
                BinaryIO,
                tempfile.TemporaryFile("w+b"),  # noqa: SIM115
            )
            handles.append(authority_spool)
            blob_spool = cast(
                BinaryIO,
                tempfile.TemporaryFile("w+b"),  # noqa: SIM115
            )
            handles.append(blob_spool)
        except BaseException:
            raise PortableRecoveryFailure("portable_recovery_spool_create_failed") from None
        table_ranges, blob_ranges = _stage_recovery_members(raw, authority_spool, blob_spool)
        staged = StagedCampaignArchive(
            campaign_id=campaign_id,
            captured_revision=captured_revision,
            archive_sha256=digest.hexdigest(),
            byte_count=byte_count,
            manifest=MappingProxyType(dict(manifest)),
            _raw_archive=raw,
            _authority_spool=authority_spool,
            _blob_spool=blob_spool,
            _table_ranges=table_ranges,
            _blob_ranges=blob_ranges,
        )
        yield staged
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_failed = False
        for handle in handles:
            try:
                handle.close()
            except BaseException:
                cleanup_failed = True
        if cleanup_failed:
            if primary is None:
                raise PortableRecoveryFailure("portable_recovery_cleanup_incomplete")
            if isinstance(primary, PortableRecoveryFailure):
                primary.supplemental_codes = primary.with_cleanup_failure().supplemental_codes
            else:
                supplemental = tuple(getattr(primary, "supplemental_codes", ()))
                with suppress(BaseException):
                    primary.supplemental_codes = tuple(  # type: ignore[attr-defined]
                        dict.fromkeys((*supplemental, "portable_recovery_cleanup_incomplete"))
                    )


_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008


def _move_file_ex(source: Path, destination: Path, flags: int) -> bool:
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    move: Any = kernel32.MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move.restype = ctypes.c_int
    return bool(move(os.fspath(source), os.fspath(destination), flags))


def move_export_file(source: Path, destination: Path) -> bool:
    """Perform the final same-volume write-through replacement and nothing afterward."""

    return _move_file_ex(
        source,
        destination,
        _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH,
    )
