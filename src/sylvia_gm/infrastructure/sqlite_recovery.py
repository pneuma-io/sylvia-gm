"""W12 caller-transaction helpers for exact portable campaign recovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from sylvia_gm.domain.authority import (
    CampaignRecoveryResult,
    canonical_payload_is_safe,
    validate_authority_identity,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, SchemaIdentityError
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure import campaign_archive, private_records_archive

RECOVERY_EXTENSION_NAME = "f5_portable_campaign_recovery_v1"
RECOVERY_EXTENSION_SQL = """
CREATE TABLE campaign_recovery_receipts (
    recovery_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL UNIQUE,
    semantic_payload_json TEXT NOT NULL,
    semantic_payload_sha256 TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL,
    archive_byte_count INTEGER NOT NULL CHECK (archive_byte_count >= 0),
    captured_revision INTEGER NOT NULL CHECK (captured_revision >= 0),
    result_json TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
);
"""
RECOVERY_EXTENSION_SHA256 = hashlib.sha256(
    RECOVERY_EXTENSION_SQL.strip().encode("utf-8")
).hexdigest()

# Every PRIMARY KEY / UNIQUE identity for a relation carried by the frozen W11
# projection, plus the two deterministically reconstructed physical relations.
RELATION_UNIQUE_KEYS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "campaigns": (("campaign_id",),),
    "campaign_lifecycle_state": (("campaign_id",),),
    "campaign_lifecycle_transitions": (
        ("operation_id",),
        ("campaign_id", "resulting_revision"),
    ),
    "campaign_operations": (("operation_id",),),
    "campaign_constructions": (
        ("campaign_id",),
        ("confirmation_operation_id",),
        ("begin_operation_id",),
        ("completion_operation_id",),
    ),
    "campaign_continuity_records": (
        ("campaign_id", "sequence"),
        ("campaign_id", "entry_id"),
        ("operation_id",),
    ),
    "campaign_revisions": (("campaign_id", "revision"),),
    "campaign_package_memberships": (
        ("campaign_id", "package_id", "version", "sha256"),
        ("campaign_id", "load_order"),
    ),
    "participants": (("campaign_id", "participant_id"),),
    "participant_quarantines": (("campaign_id", "participant_id"),),
    "campaign_state_namespace_owners": (("campaign_id", "namespace"),),
    "campaign_state": (("campaign_id", "namespace", "state_key"),),
    "turns": (("campaign_id", "turn_id"),),
    "actions": (("campaign_id", "action_id"),),
    "idempotency_records": (("campaign_id", "idempotency_key"),),
    "pending_mechanics_actions": (
        ("campaign_id",),
        ("campaign_id", "action_id"),
    ),
    "pending_mechanics_action_aliases": (("campaign_id", "idempotency_key"),),
    "mechanics_choice_resolutions": (
        ("campaign_id", "resolution_id"),
        ("campaign_id", "action_id"),
    ),
    "canonical_events": (
        ("campaign_id", "sequence"),
        ("campaign_id", "attempt_id", "attempt_ordinal"),
    ),
    "narration_interruptions": (
        ("campaign_id", "interruption_id"),
        ("campaign_id", "failed_attempt_id"),
        ("campaign_id", "resolving_retry_id"),
    ),
    "mutations": (("campaign_id", "action_id", "ordinal"),),
    "consequences": (("campaign_id", "action_id", "ordinal"),),
    "campaign_locks": (("lock_id",),),
    "campaign_lock_memberships": (
        ("lock_id", "closure_order"),
        ("lock_id", "package_id"),
    ),
    "package_activations": (("activation_id",),),
    "campaign_plans": (
        ("plan_id",),
        ("campaign_id", "plan_revision"),
        ("campaign_id", "resulting_campaign_revision"),
    ),
    "builder_dm_handoffs": (
        ("handoff_id",),
        ("campaign_id",),
        ("dm_session_id",),
    ),
    "agent_sessions": (("session_id",),),
    "agent_session_messages": (
        ("session_id", "sequence"),
        ("session_id", "message_id"),
    ),
    "package_candidates": (("candidate_id",), ("install_id",)),
    "package_candidate_memberships": (
        ("candidate_id", "closure_order"),
        ("candidate_id", "package_id"),
    ),
    "package_staging": (("install_id",),),
    "packages": (("package_id", "version", "sha256"),),
    "package_dependencies": (
        ("package_id", "version", "sha256", "dependency_order"),
        ("package_id", "version", "sha256", "dependency_id"),
    ),
    "package_manifests": (("package_id", "version", "content_sha256"),),
    "package_manifest_files": (
        ("package_id", "version", "content_sha256", "file_order"),
        ("package_id", "version", "content_sha256", "portable_path"),
    ),
    "package_exact_dependencies": (
        ("package_id", "version", "content_sha256", "dependency_order"),
        ("package_id", "version", "content_sha256", "dependency_namespace"),
    ),
    "package_capabilities": (
        ("package_id", "version", "content_sha256", "capability_order"),
        ("package_id", "version", "content_sha256", "capability"),
    ),
    "package_tools": (
        ("package_id", "version", "content_sha256", "tool_order"),
        ("package_id", "version", "content_sha256", "tool_name"),
    ),
    "package_prompt_fragments": (
        ("package_id", "version", "content_sha256", "surface_order"),
        ("package_id", "version", "content_sha256", "fragment_id"),
    ),
    "package_ui_surfaces": (
        ("package_id", "version", "content_sha256", "surface_order"),
        ("package_id", "version", "content_sha256", "surface_id"),
    ),
    "package_participant_artifacts": (
        ("package_id", "version", "content_sha256", "artifact_order"),
        ("package_id", "version", "content_sha256", "kind"),
    ),
    "package_blobs": (("blob_sha256",),),
    "package_blob_chunks": (("blob_sha256", "chunk_order"),),
    "package_staging_chunks": (("install_id", "file_order", "chunk_order"),),
}

_REUSABLE_TABLES = frozenset(
    {
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
        "package_blob_chunks",
        "package_staging_chunks",
    }
)

_PHYSICAL_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "package_blob_chunks": (
        "blob_sha256",
        "chunk_order",
        "byte_count",
        "chunk_sha256",
        "data",
    ),
    "package_staging_chunks": (
        "install_id",
        "file_order",
        "chunk_order",
        "package_id",
        "version",
        "content_sha256",
        "portable_path",
        "chunk_sha256",
        "data",
    ),
}


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    inserted_count: int
    reused_count: int
    new_keys: tuple[tuple[str, tuple[str, ...], tuple[object, ...]], ...]


def apply_recovery_extension(
    connection: sqlite3.Connection,
    *,
    predecessor_name: str,
    predecessor_sha256: str,
) -> None:
    """Apply the exact additive W12 extension inside the caller transaction."""

    connection.execute(RECOVERY_EXTENSION_SQL)
    connection.execute(
        "INSERT INTO authority_schema_extensions "
        "(extension_name, extension_sha256, predecessor_migration_name, predecessor_sql_sha256) "
        "VALUES (?, ?, ?, ?)",
        (
            RECOVERY_EXTENSION_NAME,
            RECOVERY_EXTENSION_SHA256,
            predecessor_name,
            predecessor_sha256,
        ),
    )


def semantic_payload(
    recovery_id: str,
    staged: campaign_archive.StagedCampaignArchive,
) -> tuple[str, str]:
    payload = {
        "operation_id": recovery_id,
        "operation_kind": "recover_campaign",
        "campaign_id": staged.campaign_id,
        "captured_revision": staged.captured_revision,
        "archive_sha256": staged.archive_sha256,
        "archive_byte_count": staged.byte_count,
        "format_id": campaign_archive.FORMAT_ID,
        "format_version": campaign_archive.FORMAT_VERSION,
        "product_id": campaign_archive.PRODUCT_ID,
        "product_generation": campaign_archive.PRODUCT_GENERATION,
        "source_authority_schema_version": staged.manifest["source_authority_schema_version"],
        "source_authority_schema_identity_sha256": staged.manifest[
            "source_authority_schema_identity_sha256"
        ],
        "migration_provenance": staged.manifest["migration_provenance"],
    }
    encoded = canonical_json_bytes(payload)
    return encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest()


def result_json(result: CampaignRecoveryResult) -> str:
    """Encode the exact public result field order without host/path metadata."""

    return json.dumps(
        {
            "campaign_id": result.campaign_id,
            "captured_revision": result.captured_revision,
            "archive_sha256": result.archive_sha256,
            "byte_count": result.byte_count,
        },
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def receipt_result(
    row: sqlite3.Row,
    *,
    semantic_payload_json: str,
    semantic_payload_sha256: str,
) -> CampaignRecoveryResult:
    if (
        str(row["semantic_payload_json"]) != semantic_payload_json
        or str(row["semantic_payload_sha256"]) != semantic_payload_sha256
    ):
        raise IdempotencyConflict("Recovery identity was reused with different archive semantics")
    try:
        value = json.loads(str(row["result_json"]))
        result = CampaignRecoveryResult(
            campaign_id=str(value["campaign_id"]),
            captured_revision=int(value["captured_revision"]),
            archive_sha256=str(value["archive_sha256"]),
            byte_count=int(value["byte_count"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SchemaIdentityError("Recovery receipt result is corrupt") from None
    return result


def lookup_receipt(
    connection: sqlite3.Connection,
    *,
    recovery_id: str,
    semantic_payload_json: str,
    semantic_payload_sha256: str,
) -> CampaignRecoveryResult | None:
    if "campaign_recovery_receipts" not in {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }:
        return None
    row = connection.execute(
        "SELECT * FROM campaign_recovery_receipts WHERE recovery_id = ?",
        (recovery_id,),
    ).fetchone()
    if row is None:
        return None
    return receipt_result(
        row,
        semantic_payload_json=semantic_payload_json,
        semantic_payload_sha256=semantic_payload_sha256,
    )


def verify_recovery_receipts(connection: sqlite3.Connection) -> None:
    """Reconcile every durable transfer receipt with exact campaign authority."""

    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "campaign_recovery_receipts" not in tables:
        return
    invalid = "Authority stored recovery receipt integrity is invalid"
    try:
        for row in connection.execute(
            "SELECT * FROM campaign_recovery_receipts ORDER BY recovery_id"
        ):
            recovery_id = validate_authority_identity(row["recovery_id"], "Recovery identity")
            campaign_id = validate_authority_identity(
                row["campaign_id"], "Recovery campaign identity"
            )
            archive_sha256 = validate_authority_identity(
                row["archive_sha256"], "Recovery archive SHA-256", sha256=True
            )
            payload_json = str(row["semantic_payload_json"])
            payload_sha256 = validate_authority_identity(
                row["semantic_payload_sha256"], "Recovery semantic SHA-256", sha256=True
            )
            payload = json.loads(payload_json)
            if (
                not isinstance(payload, Mapping)
                or not canonical_payload_is_safe(payload)
                or canonical_json_bytes(payload).decode("utf-8") != payload_json
                or hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != payload_sha256
                or set(payload)
                != {
                    "operation_id",
                    "operation_kind",
                    "campaign_id",
                    "captured_revision",
                    "archive_sha256",
                    "archive_byte_count",
                    "format_id",
                    "format_version",
                    "product_id",
                    "product_generation",
                    "source_authority_schema_version",
                    "source_authority_schema_identity_sha256",
                    "migration_provenance",
                }
                or payload["operation_id"] != recovery_id
                or payload["operation_kind"] != "recover_campaign"
                or payload["campaign_id"] != campaign_id
                or type(payload["captured_revision"]) is not int
                or type(payload["archive_byte_count"]) is not int
                or type(payload["format_version"]) is not int
                or type(payload["source_authority_schema_version"]) is not int
                or payload["archive_sha256"] != archive_sha256
                or payload["archive_byte_count"] != int(row["archive_byte_count"])
                or payload["captured_revision"] != int(row["captured_revision"])
                or payload["format_id"] != campaign_archive.FORMAT_ID
                or payload["format_version"] != campaign_archive.FORMAT_VERSION
                or payload["product_id"] != campaign_archive.PRODUCT_ID
                or payload["product_generation"] != campaign_archive.PRODUCT_GENERATION
                or payload["source_authority_schema_version"]
                not in {
                    campaign_archive.PREDECESSOR_SOURCE_AUTHORITY_SCHEMA_VERSION,
                    campaign_archive.PRE_REASONING_SOURCE_AUTHORITY_SCHEMA_VERSION,
                    campaign_archive.PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION,
                    campaign_archive.SOURCE_AUTHORITY_SCHEMA_VERSION,
                }
                or payload["source_authority_schema_identity_sha256"]
                != campaign_archive.source_authority_schema_claim_sha256(
                    int(payload["source_authority_schema_version"])
                )
            ):
                raise ValueError("Recovery semantic payload is invalid")
            campaign_archive.validate_migration_provenance(
                payload["migration_provenance"],
                enforce_declared_order_bytes=False,
                source_schema_version=payload["source_authority_schema_version"],
            )
            result = receipt_result(
                row,
                semantic_payload_json=payload_json,
                semantic_payload_sha256=payload_sha256,
            )
            if (
                result_json(result) != str(row["result_json"])
                or result.campaign_id != campaign_id
                or result.captured_revision != int(row["captured_revision"])
                or result.archive_sha256 != archive_sha256
                or result.byte_count != int(row["archive_byte_count"])
                or result.captured_revision < 0
                or result.byte_count < 0
            ):
                raise ValueError("Recovery result is invalid")
            campaign = connection.execute(
                "SELECT revision FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if campaign is None or int(campaign["revision"]) < result.captured_revision:
                raise ValueError("Recovery receipt campaign authority is missing")
    except (
        sqlite3.Error,
        TypeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        CommandRejected,
    ):
        raise SchemaIdentityError(invalid) from None


def _present_tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    )


def _projected_columns(table: str) -> tuple[str, ...]:
    if table in campaign_archive.TABLE_COLUMNS:
        return campaign_archive.TABLE_COLUMNS[table]
    return _PHYSICAL_COLUMNS[table]


def _row_status(
    connection: sqlite3.Connection,
    table: str,
    row: Mapping[str, object],
) -> str:
    columns = _projected_columns(table)
    projection = ", ".join(f'"{column}"' for column in columns)
    hits: dict[tuple[object, ...], tuple[object, ...]] = {}
    for key_columns in RELATION_UNIQUE_KEYS[table]:
        values = tuple(row[column] for column in key_columns)
        # SQLite UNIQUE permits more than one row when any participating value is NULL.
        if any(value is None for value in values):
            continue
        where = " AND ".join(f'"{column}" = ?' for column in key_columns)
        selected = connection.execute(
            f'SELECT {projection} FROM "{table}" WHERE {where}',
            values,
        ).fetchall()
        if len(selected) > 1:
            raise CommandRejected("Portable recovery collision plan found ambiguous identity")
        if selected:
            projected = tuple(selected[0][column] for column in columns)
            primary = tuple(selected[0][column] for column in RELATION_UNIQUE_KEYS[table][0])
            hits[primary] = projected
    if not hits:
        return "insert"
    expected = tuple(row[column] for column in columns)
    if len(hits) != 1 or next(iter(hits.values())) != expected or table not in _REUSABLE_TABLES:
        raise CommandRejected(
            "Portable recovery collision conflicts with existing authority",
            code="portable_recovery_collision",
        )
    return "reuse"


def _blob_chunk_rows(
    staged: campaign_archive.StagedCampaignArchive,
) -> Iterator[Mapping[str, object]]:
    for blob in staged.iter_rows("package_blobs"):
        blob_sha256 = str(blob["blob_sha256"])
        chunk_order = 0
        byte_count = 0
        for chunk in staged.iter_blob(blob_sha256):
            byte_count += len(chunk)
            yield {
                "blob_sha256": blob_sha256,
                "chunk_order": chunk_order,
                "byte_count": len(chunk),
                "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                "data": chunk,
            }
            chunk_order += 1
        if byte_count != int(cast(Any, blob["byte_count"])) or chunk_order != int(
            cast(Any, blob["chunk_count"])
        ):
            raise CommandRejected(
                "Portable recovery blob reconstruction is invalid",
                code="portable_export_invalid_blob_closure",
            )


def _staging_chunk_rows(
    staged: campaign_archive.StagedCampaignArchive,
) -> Iterator[Mapping[str, object]]:
    for staging in staged.iter_rows("package_staging"):
        try:
            manifest = json.loads(str(staging["manifest_json"]))
            packages = cast(Sequence[Mapping[str, object]], manifest["packages"])
        except (KeyError, TypeError, json.JSONDecodeError):
            raise CommandRejected(
                "Portable recovery staging reconstruction is invalid",
                code="portable_export_invalid_package_graph",
            ) from None
        file_order = 0
        for package in packages:
            files = package.get("files")
            if not isinstance(files, Sequence) or isinstance(files, (str, bytes, bytearray)):
                raise CommandRejected(
                    "Portable recovery staging reconstruction is invalid",
                    code="portable_export_invalid_package_graph",
                )
            for value in files:
                if not isinstance(value, Mapping):
                    raise CommandRejected(
                        "Portable recovery staging reconstruction is invalid",
                        code="portable_export_invalid_package_graph",
                    )
                blob_sha256 = str(value["sha256"])
                byte_count = 0
                chunk_order = 0
                for chunk_order, chunk in enumerate(staged.iter_blob(blob_sha256)):
                    byte_count += len(chunk)
                    yield {
                        "install_id": staging["install_id"],
                        "file_order": file_order,
                        "chunk_order": chunk_order,
                        "package_id": package["id"],
                        "version": package["version"],
                        "content_sha256": package["content_sha256"],
                        "portable_path": value["path"],
                        "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                        "data": chunk,
                    }
                if byte_count != int(cast(Any, value["bytes"])):
                    raise CommandRejected(
                        "Portable recovery staging reconstruction is invalid",
                        code="portable_export_invalid_blob_closure",
                    )
                file_order += 1


def _physical_rows(
    staged: campaign_archive.StagedCampaignArchive,
    table: str,
) -> Iterator[Mapping[str, object]]:
    if table == "package_blob_chunks":
        return _blob_chunk_rows(staged)
    if table == "package_staging_chunks":
        return _staging_chunk_rows(staged)
    raise ValueError("Unknown recovery physical relation")


def plan_recovery(
    connection: sqlite3.Connection,
    staged: campaign_archive.StagedCampaignArchive,
) -> RecoveryPlan:
    """Complete every currently physical collision/reuse decision before writes."""

    if (
        connection.execute(
            "SELECT 1 FROM campaigns WHERE campaign_id = ?", (staged.campaign_id,)
        ).fetchone()
        is not None
    ):
        raise CommandRejected(
            "Portable recovery campaign identity collision: campaign already exists",
            code="portable_recovery_campaign_collision",
        )
    present = _present_tables(connection)
    inserted = 0
    reused = 0
    new_keys: list[tuple[str, tuple[str, ...], tuple[object, ...]]] = []
    for table in campaign_archive.TABLES_IN_ORDER:
        if table not in present:
            for row in staged.iter_rows(table):
                inserted += 1
                keys = RELATION_UNIQUE_KEYS[table][0]
                new_keys.append((table, keys, tuple(row[column] for column in keys)))
            continue
        for row in staged.iter_rows(table):
            status = _row_status(connection, table, row)
            if status == "insert":
                inserted += 1
                keys = RELATION_UNIQUE_KEYS[table][0]
                new_keys.append((table, keys, tuple(row[column] for column in keys)))
            else:
                reused += 1
    for table in _PHYSICAL_COLUMNS:
        if table not in present:
            continue
        for row in _physical_rows(staged, table):
            status = _row_status(connection, table, row)
            if status == "insert":
                inserted += 1
                keys = RELATION_UNIQUE_KEYS[table][0]
                new_keys.append((table, keys, tuple(row[column] for column in keys)))
            else:
                reused += 1
    for row in staged.private_records():
        keys = ("session_id", "attempt_id", "source_order")
        identity = tuple(row[column] for column in keys)
        if "provider_private_records" in present and connection.execute(
            "SELECT 1 FROM provider_private_records WHERE session_id = ? "
            "AND attempt_id = ? AND source_order = ?", identity,
        ).fetchone() is not None:
            raise CommandRejected(
                "Portable recovery private record identity collision",
                code="portable_recovery_private_record_collision",
            )
        inserted += 1
        new_keys.append(("provider_private_records", keys, identity))
    return RecoveryPlan(inserted, reused, tuple(new_keys))


def _insert_row(
    connection: sqlite3.Connection,
    table: str,
    row: Mapping[str, object],
) -> bool:
    if _row_status(connection, table, row) == "reuse":
        return False
    columns = _projected_columns(table)
    names = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    try:
        connection.execute(
            f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
            tuple(row[column] for column in columns),
        )
    except sqlite3.IntegrityError as error:
        raise SchemaIdentityError(f"Portable recovery relation failed: {table}") from error
    return True


def insert_recovery_graph(
    connection: sqlite3.Connection,
    staged: campaign_archive.StagedCampaignArchive,
) -> tuple[int, int]:
    inserted = 0
    reused = 0
    owner_index = campaign_archive.TABLES_IN_ORDER.index("campaign_state_namespace_owners")
    prerequisite_tables = ("campaign_locks", "campaign_lock_memberships")
    provisional_order = (
        campaign_archive.TABLES_IN_ORDER[:owner_index]
        + prerequisite_tables
        + tuple(
            table
            for table in campaign_archive.TABLES_IN_ORDER[owner_index:]
            if table not in prerequisite_tables
        )
    )
    package_prerequisite_tables = (
        "package_blobs",
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
    deferred_tables = package_prerequisite_tables + (
        "campaign_package_memberships",
        "campaign_lifecycle_transitions",
        "campaign_operations",
        "campaign_continuity_records",
        "pending_mechanics_actions",
        "pending_mechanics_action_aliases",
        "mechanics_choice_resolutions",
    )
    insertion_order = tuple(table for table in provisional_order if table not in deferred_tables)
    campaigns_index = insertion_order.index("campaigns") + 1
    package_index = campaigns_index
    for table in (
        "package_candidates",
        "package_staging",
        "package_activations",
        "campaign_locks",
    ):
        if table in insertion_order:
            package_index = min(package_index, insertion_order.index(table))
    insertion_order = (
        insertion_order[:package_index]
        + package_prerequisite_tables
        + ("campaign_package_memberships",)
        + insertion_order[package_index:]
        + (
            "campaign_operations",
            "campaign_continuity_records",
            "campaign_lifecycle_transitions",
            "pending_mechanics_actions",
            "pending_mechanics_action_aliases",
            "mechanics_choice_resolutions",
        )
    )
    for table in insertion_order:
        for row in staged.iter_rows(table):
            if _insert_row(connection, table, row):
                inserted += 1
            else:
                reused += 1
    for table in _PHYSICAL_COLUMNS:
        for row in _physical_rows(staged, table):
            if _insert_row(connection, table, row):
                inserted += 1
            else:
                reused += 1
    for row in staged.private_records():
        fields = private_records_archive.FIELDS
        values = [row[key] for key in fields]
        values[-1] = "interrupted" if row["status"] == "partial" else row["status"]
        connection.execute(
            "INSERT INTO provider_private_records (" + ", ".join(fields)
            + ", agent_session_id, builder_session_id) VALUES ("
            + ", ".join("?" for _ in fields) + ", ?, NULL)",
            (*values, row["session_id"]),
        )
        inserted += 1
    return inserted, reused


def insert_receipt(
    connection: sqlite3.Connection,
    *,
    recovery_id: str,
    semantic_payload_json: str,
    semantic_payload_sha256: str,
    result: CampaignRecoveryResult,
) -> None:
    connection.execute(
        "INSERT INTO campaign_recovery_receipts "
        "(recovery_id, campaign_id, semantic_payload_json, semantic_payload_sha256, "
        "archive_sha256, archive_byte_count, captured_revision, result_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            recovery_id,
            result.campaign_id,
            semantic_payload_json,
            semantic_payload_sha256,
            result.archive_sha256,
            result.byte_count,
            result.captured_revision,
            result_json(result),
        ),
    )


def plan_keys_absent(
    connection: sqlite3.Connection,
    plan: RecoveryPlan,
) -> bool:
    present = _present_tables(connection)
    for table, columns, values in plan.new_keys:
        if table not in present:
            continue
        where = " AND ".join(f'"{column}" = ?' for column in columns)
        if (
            connection.execute(f'SELECT 1 FROM "{table}" WHERE {where}', values).fetchone()
            is not None
        ):
            return False
    return True
