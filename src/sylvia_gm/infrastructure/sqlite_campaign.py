"""Caller-transaction helpers for the additive F5 participant authority migration."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from typing import cast

from sylvia_gm.application.schema_admission import validate_json_value
from sylvia_gm.domain.authority import (
    JsonValue,
    Participant,
    ParticipantArtifact,
    ParticipantValidationStatus,
    SafePayload,
    validate_authority_identity,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    PackageManifest,
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
    manifest_from_record,
    parse_canonical_json_bytes,
)
from sylvia_gm.infrastructure.participant_history import participant_data_history

MIGRATION_5_NAME = "f5_campaign_lifecycle_recovery"
PREDECESSOR_MIGRATION_5_SQL = """
ALTER TABLE participants ADD COLUMN package_sha256 TEXT;
ALTER TABLE participants ADD COLUMN schema_id TEXT;
ALTER TABLE participants ADD COLUMN schema_sha256 TEXT;
ALTER TABLE participants ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'quarantined'
    CHECK (validation_status IN ('validated', 'quarantined'));

CREATE TABLE package_participant_artifacts (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    artifact_order INTEGER NOT NULL CHECK (artifact_order >= 0),
    kind TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, artifact_order),
    UNIQUE (package_id, version, content_sha256, kind),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE participant_quarantines (
    campaign_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    PRIMARY KEY (campaign_id, participant_id),
    FOREIGN KEY (campaign_id, participant_id)
        REFERENCES participants(campaign_id, participant_id) ON DELETE CASCADE
);
CREATE TABLE participant_adaptations (
    operation_id TEXT PRIMARY KEY,
    payload_sha256 TEXT NOT NULL,
    source_campaign_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_package_id TEXT NOT NULL,
    source_package_version TEXT NOT NULL,
    source_package_sha256 TEXT,
    source_schema_id TEXT,
    source_schema_sha256 TEXT,
    source_data_sha256 TEXT NOT NULL,
    destination_kind TEXT NOT NULL,
    destination_package_id TEXT NOT NULL,
    destination_package_version TEXT NOT NULL,
    destination_package_sha256 TEXT NOT NULL,
    destination_schema_id TEXT NOT NULL,
    destination_schema_sha256 TEXT NOT NULL,
    data_json TEXT NOT NULL,
    FOREIGN KEY (
        destination_package_id, destination_package_version,
        destination_package_sha256, destination_kind
    ) REFERENCES package_participant_artifacts(
        package_id, version, content_sha256, kind
    )
);
ALTER TABLE participants ADD COLUMN adaptation_id TEXT
    REFERENCES participant_adaptations(operation_id);
"""

CAMPAIGN_IDENTITY_EXTENSION_NAME = "f5_probe_01_stable_campaign_identity"
PREDECESSOR_MIGRATION_5_SHA256 = "49d01f74743bd178ab23848c4493dda234bf51a26ad7115236c2356714c48802"
CAMPAIGN_IDENTITY_DELTA_SQL = """
ALTER TABLE campaign_revisions ADD COLUMN construction_operation_id TEXT;
CREATE TABLE authority_schema_extensions (
    extension_name TEXT PRIMARY KEY,
    extension_sha256 TEXT NOT NULL,
    predecessor_migration_name TEXT NOT NULL,
    predecessor_sql_sha256 TEXT NOT NULL
);
CREATE TABLE campaign_operations (
    operation_id TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    semantic_payload_json TEXT NOT NULL,
    semantic_payload_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    basis_turn_id TEXT NOT NULL,
    basis_lock_id TEXT NOT NULL,
    basis_revision INTEGER NOT NULL CHECK (basis_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 0),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, basis_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (basis_lock_id) REFERENCES campaign_locks(lock_id)
);
CREATE TABLE campaign_constructions (
    campaign_id TEXT PRIMARY KEY,
    confirmation_operation_id TEXT NOT NULL UNIQUE,
    authority_turn_id TEXT NOT NULL,
    authority_lock_id TEXT NOT NULL,
    confirmation_revision INTEGER NOT NULL CHECK (confirmation_revision = 0),
    lane TEXT CHECK (lane IN ('quick_start', 'customize')),
    canonical_input_json TEXT,
    begin_operation_id TEXT UNIQUE,
    begin_revision INTEGER CHECK (begin_revision > 0),
    canonical_plan_json TEXT,
    completion_operation_id TEXT UNIQUE,
    completion_revision INTEGER CHECK (completion_revision > 0),
    canonical_representation_sha256 TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (confirmation_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (begin_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (completion_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (campaign_id, authority_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (authority_lock_id) REFERENCES campaign_locks(lock_id),
    CHECK (
        (lane IS NULL AND canonical_input_json IS NULL AND begin_operation_id IS NULL
            AND begin_revision IS NULL AND canonical_plan_json IS NULL
            AND completion_operation_id IS NULL AND completion_revision IS NULL
            AND canonical_representation_sha256 IS NULL)
        OR
        (lane IS NOT NULL AND canonical_input_json IS NOT NULL AND begin_operation_id IS NOT NULL
            AND begin_revision IS NOT NULL AND canonical_plan_json IS NULL
            AND completion_operation_id IS NULL AND completion_revision IS NULL
            AND canonical_representation_sha256 IS NULL)
        OR
        (lane IS NOT NULL AND canonical_input_json IS NOT NULL AND begin_operation_id IS NOT NULL
            AND begin_revision IS NOT NULL AND canonical_plan_json IS NOT NULL
            AND completion_operation_id IS NOT NULL AND completion_revision IS NOT NULL
            AND canonical_representation_sha256 IS NOT NULL)
    )
);
"""
CAMPAIGN_IDENTITY_DELTA_SHA256 = hashlib.sha256(
    CAMPAIGN_IDENTITY_DELTA_SQL.strip().encode("utf-8")
).hexdigest()
MIGRATION_5_SQL = PREDECESSOR_MIGRATION_5_SQL + CAMPAIGN_IDENTITY_DELTA_SQL

LIFECYCLE_EXTENSION_NAME = "f5_reversible_lifecycle_v3"
LIFECYCLE_EXTENSION_SQL = """
ALTER TABLE campaign_revisions ADD COLUMN lifecycle_operation_id TEXT;

CREATE TABLE campaign_lifecycle_state (
    campaign_id TEXT PRIMARY KEY,
    retained_active_writer TEXT,
    designated_session_id TEXT,
    tombstone_prior_lifecycle TEXT,
    tombstone_prior_writer TEXT,
    tombstone_prior_session_id TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (designated_session_id) REFERENCES agent_sessions(session_id),
    FOREIGN KEY (tombstone_prior_session_id) REFERENCES agent_sessions(session_id),
    CHECK (retained_active_writer IS NULL OR retained_active_writer IN ('builder', 'dm')),
    CHECK (
        tombstone_prior_lifecycle IS NULL
        OR tombstone_prior_lifecycle IN ('active', 'archived')
    ),
    CHECK (tombstone_prior_writer IS NULL OR tombstone_prior_writer IN ('none', 'builder', 'dm'))
);

CREATE TABLE campaign_lifecycle_transitions (
    operation_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    basis_revision INTEGER NOT NULL,
    resulting_revision INTEGER NOT NULL,
    from_lifecycle TEXT NOT NULL,
    to_lifecycle TEXT NOT NULL,
    from_writer TEXT NOT NULL,
    to_writer TEXT NOT NULL,
    designated_session_id TEXT,
    FOREIGN KEY (operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (designated_session_id) REFERENCES agent_sessions(session_id),
    UNIQUE (campaign_id, resulting_revision),
    CHECK (
        operation_kind IN (
            'rename_campaign', 'archive_campaign', 'restore_campaign', 'trash_campaign'
        )
    ),
    CHECK (resulting_revision = basis_revision + 1),
    CHECK (from_lifecycle IN ('active', 'archived', 'tombstoned')),
    CHECK (to_lifecycle IN ('active', 'archived', 'tombstoned')),
    CHECK (from_writer IN ('none', 'builder', 'dm')),
    CHECK (to_writer IN ('none', 'builder', 'dm'))
);

INSERT INTO campaign_lifecycle_state
    (campaign_id, retained_active_writer, designated_session_id,
     tombstone_prior_lifecycle, tombstone_prior_writer, tombstone_prior_session_id)
SELECT campaign_id,
       CASE WHEN lifecycle = 'active' THEN writer ELSE NULL END,
       NULL, NULL, NULL, NULL
FROM campaigns;
""".strip()
LIFECYCLE_EXTENSION_SHA256 = hashlib.sha256(LIFECYCLE_EXTENSION_SQL.encode("utf-8")).hexdigest()

PURGE_EXTENSION_NAME = "f5_campaign_purge_v1"
PURGE_CAMPAIGN_OPERATIONS_SQL = """
CREATE TABLE campaign_operations (
    operation_id TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    semantic_payload_json TEXT NOT NULL,
    semantic_payload_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    basis_turn_id TEXT,
    basis_lock_id TEXT,
    basis_revision INTEGER NOT NULL CHECK (basis_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 0),
    FOREIGN KEY (campaign_id, basis_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (basis_lock_id) REFERENCES campaign_locks(lock_id),
    CHECK (
        (operation_kind = 'purge_campaign'
            AND basis_turn_id IS NULL AND basis_lock_id IS NULL)
        OR
        (operation_kind != 'purge_campaign'
            AND basis_turn_id IS NOT NULL AND basis_lock_id IS NOT NULL)
    )
)
""".strip()
PURGE_EXTENSION_SQL = "\n".join(
    (
        "PRAGMA defer_foreign_keys = ON;",
        "CREATE TEMP TABLE campaign_operations_purge_shadow AS "
        "SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, "
        "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, "
        "basis_revision, resulting_revision FROM campaign_operations;",
        "DROP TABLE campaign_operations;",
        PURGE_CAMPAIGN_OPERATIONS_SQL + ";",
        "INSERT INTO campaign_operations "
        "(operation_id, operation_kind, campaign_id, semantic_payload_json, "
        "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, "
        "basis_revision, resulting_revision) SELECT operation_id, operation_kind, "
        "campaign_id, semantic_payload_json, semantic_payload_sha256, result_json, "
        "basis_turn_id, basis_lock_id, basis_revision, resulting_revision "
        "FROM campaign_operations_purge_shadow;",
        "DROP TABLE campaign_operations_purge_shadow;",
    )
)
PURGE_EXTENSION_SHA256 = hashlib.sha256(PURGE_EXTENSION_SQL.encode("utf-8")).hexdigest()


def apply_lifecycle_extension(connection: sqlite3.Connection) -> None:
    """Apply the frozen reversible-lifecycle delta exactly once in the caller transaction."""

    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(campaign_revisions)")}
    if "lifecycle_operation_id" in columns:
        return
    for statement in LIFECYCLE_EXTENSION_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


def apply_purge_extension(connection: sqlite3.Connection) -> None:
    """Rebuild the receipt relation without renaming its referenced table."""

    columns = {
        str(row[1]): bool(row[3])
        for row in connection.execute("PRAGMA table_info(campaign_operations)")
    }
    if columns.get("basis_turn_id") is False and columns.get("basis_lock_id") is False:
        return
    before = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, "
            "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, "
            "basis_revision, resulting_revision FROM campaign_operations "
            "ORDER BY operation_id"
        )
    )
    connection.execute("PRAGMA defer_foreign_keys = ON")
    connection.execute(
        "CREATE TEMP TABLE campaign_operations_purge_shadow AS "
        "SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, "
        "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, "
        "basis_revision, resulting_revision FROM campaign_operations"
    )
    connection.execute("DROP TABLE campaign_operations")
    connection.execute(PURGE_CAMPAIGN_OPERATIONS_SQL)
    connection.execute(
        "INSERT INTO campaign_operations "
        "(operation_id, operation_kind, campaign_id, semantic_payload_json, "
        "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, "
        "basis_revision, resulting_revision) SELECT operation_id, operation_kind, "
        "campaign_id, semantic_payload_json, semantic_payload_sha256, result_json, "
        "basis_turn_id, basis_lock_id, basis_revision, resulting_revision "
        "FROM campaign_operations_purge_shadow"
    )
    connection.execute("DROP TABLE campaign_operations_purge_shadow")
    after = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, "
            "semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, "
            "basis_revision, resulting_revision FROM campaign_operations "
            "ORDER BY operation_id"
        )
    )
    if after != before:
        raise ValueError("Campaign operation receipt rebuild changed existing rows")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Campaign purge extension has invalid foreign keys")
    if tuple(str(row[0]).casefold() for row in connection.execute("PRAGMA integrity_check")) != (
        "ok",
    ):
        raise ValueError("Campaign purge extension failed integrity verification")


def _parse_mapping(value: str) -> Mapping[str, object]:
    parsed = parse_canonical_json_bytes(value.encode("utf-8"))
    if not isinstance(parsed, Mapping):
        raise ValueError("Stored canonical JSON must be an object")
    return cast(Mapping[str, object], parsed)


def adaptation_payload_sha256(
    *,
    source_campaign_id: str,
    participant_id: str,
    destination_package_id: str,
    destination_package_version: str,
    destination_package_sha256: str,
    destination_kind: str,
    destination_schema_id: str,
    destination_schema_sha256: str,
    data: SafePayload,
) -> str:
    """Hash the complete semantic adaptation request for durable replay fencing."""

    payload = {
        "source_campaign_id": source_campaign_id,
        "participant_id": participant_id,
        "destination_package_id": destination_package_id,
        "destination_package_version": destination_package_version,
        "destination_package_sha256": destination_package_sha256,
        "destination_kind": destination_kind,
        "destination_schema_id": destination_schema_id,
        "destination_schema_sha256": destination_schema_sha256,
        "data": data,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def artifact_from_adaptation_row(row: sqlite3.Row) -> ParticipantArtifact:
    """Reconstruct the exact destination artifact represented by durable provenance."""

    return ParticipantArtifact(
        participant_id=str(row["participant_id"]),
        kind=str(row["destination_kind"]),
        package_id=str(row["destination_package_id"]),
        package_version=str(row["destination_package_version"]),
        data=cast(SafePayload, _parse_mapping(str(row["data_json"]))),
        package_sha256=str(row["destination_package_sha256"]),
        schema_id=str(row["destination_schema_id"]),
        schema_sha256=str(row["destination_schema_sha256"]),
        validation_status=ParticipantValidationStatus.VALIDATED,
        adaptation_id=str(row["operation_id"]),
    )


def insert_or_compare_declarations(
    connection: sqlite3.Connection, manifest: PackageManifest
) -> None:
    """Persist immutable declaration children when migration 5 is present."""

    if "package_participant_artifacts" not in {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }:
        return
    expected = tuple(
        (
            order,
            declaration.kind,
            declaration.schema_id,
            declaration.schema_sha256,
            canonical_json_bytes(declaration.schema).decode("utf-8"),
        )
        for order, declaration in enumerate(manifest.participant_artifacts)
    )
    stored = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT artifact_order, kind, schema_id, schema_sha256, schema_json "
            "FROM package_participant_artifacts WHERE package_id = ? AND version = ? "
            "AND content_sha256 = ? ORDER BY artifact_order",
            manifest.identity,
        )
    )
    if stored:
        if stored != expected:
            raise ValueError("Immutable participant declarations conflict")
        return
    for row in expected:
        connection.execute(
            "INSERT INTO package_participant_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*manifest.identity, *row),
        )


def _published_manifests(connection: sqlite3.Connection) -> tuple[PackageManifest, ...]:
    return tuple(
        manifest_from_record(_parse_mapping(str(row[0])))
        for row in connection.execute(
            "SELECT canonical_json FROM package_manifests "
            "WHERE status = 'published' ORDER BY package_id, version, content_sha256"
        )
    )


def migrate_v4_participants(connection: sqlite3.Connection) -> None:
    """Backfill only uniquely reachable exact revision-0 declaration proof."""

    for manifest in _published_manifests(connection):
        insert_or_compare_declarations(connection, manifest)
    for row in tuple(
        connection.execute(
            "SELECT campaign_id, participant_id, kind, package_id, package_version, data_json "
            "FROM participants ORDER BY campaign_id, participant_id"
        )
    ):
        campaign_id = str(row["campaign_id"])
        participant_id = str(row["participant_id"])
        package_id = str(row["package_id"])
        package_version = str(row["package_version"])
        content_rows = tuple(
            connection.execute(
                "SELECT DISTINCT membership.content_sha256 "
                "FROM campaign_locks AS lock "
                "JOIN campaign_lock_memberships AS membership ON membership.lock_id = lock.lock_id "
                "WHERE lock.campaign_id = ? AND lock.created_revision = 0 "
                "AND membership.package_id = ? AND membership.version = ?",
                (campaign_id, package_id, package_version),
            )
        )
        declaration_row = None
        package_sha256 = None
        if len(content_rows) == 1:
            package_sha256 = str(content_rows[0][0])
            declaration_row = connection.execute(
                "SELECT schema_id, schema_sha256, schema_json "
                "FROM package_participant_artifacts WHERE package_id = ? AND version = ? "
                "AND content_sha256 = ? AND kind = ?",
                (package_id, package_version, package_sha256, str(row["kind"])),
            ).fetchone()
        validated = False
        if declaration_row is not None:
            data = _parse_mapping(str(row["data_json"]))
            schema = _parse_mapping(str(declaration_row["schema_json"]))
            try:
                validate_json_value(
                    cast(JsonValue, data), schema, label=f"Participant {participant_id}"
                )
            except CommandRejected:
                pass
            else:
                connection.execute(
                    "UPDATE participants SET package_sha256 = ?, schema_id = ?, "
                    "schema_sha256 = ?, validation_status = 'validated' "
                    "WHERE campaign_id = ? AND participant_id = ?",
                    (
                        package_sha256,
                        str(declaration_row["schema_id"]),
                        str(declaration_row["schema_sha256"]),
                        campaign_id,
                        participant_id,
                    ),
                )
                validated = True
        if validated:
            connection.execute(
                "DELETE FROM participant_quarantines WHERE campaign_id = ? AND participant_id = ?",
                (campaign_id, participant_id),
            )
        else:
            connection.execute(
                "UPDATE participants SET package_sha256 = ?, schema_id = NULL, "
                "schema_sha256 = NULL, validation_status = 'quarantined' "
                "WHERE campaign_id = ? AND participant_id = ?",
                (package_sha256, campaign_id, participant_id),
            )
            connection.execute(
                "INSERT INTO participant_quarantines(campaign_id, participant_id, reason_code) "
                "VALUES (?, ?, 'exact_schema_proof_unavailable')",
                (campaign_id, participant_id),
            )


def declarations_for_manifest(
    manifest: PackageManifest,
) -> tuple[ParticipantArtifactDeclaration, ...]:
    return manifest.participant_artifacts


def verify_f5_campaign_authority(connection: sqlite3.Connection) -> None:
    """Verify normalized declarations and exact participant/quarantine consistency."""

    for manifest in _published_manifests(connection):
        expected = tuple(
            (
                order,
                declaration.kind,
                declaration.schema_id,
                declaration.schema_sha256,
                canonical_json_bytes(declaration.schema).decode("utf-8"),
            )
            for order, declaration in enumerate(manifest.participant_artifacts)
        )
        stored = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT artifact_order, kind, schema_id, schema_sha256, schema_json "
                "FROM package_participant_artifacts WHERE package_id = ? AND version = ? "
                "AND content_sha256 = ? ORDER BY artifact_order",
                manifest.identity,
            )
        )
        if stored != expected:
            raise ValueError("Stored participant declaration authority is invalid")
    adaptations: dict[str, ParticipantArtifact] = {}
    for row in connection.execute("SELECT * FROM participant_adaptations ORDER BY operation_id"):
        operation_id = str(row["operation_id"])
        for value, label in (
            (operation_id, "Adaptation operation identity"),
            (str(row["source_campaign_id"]), "Adaptation source campaign identity"),
            (str(row["participant_id"]), "Adaptation participant identity"),
            (str(row["source_kind"]), "Adaptation source kind"),
            (str(row["source_package_id"]), "Adaptation source package identity"),
            (str(row["source_package_version"]), "Adaptation source package version"),
            (str(row["destination_kind"]), "Adaptation destination kind"),
            (str(row["destination_package_id"]), "Adaptation destination package identity"),
            (
                str(row["destination_package_version"]),
                "Adaptation destination package version",
            ),
            (str(row["destination_schema_id"]), "Adaptation destination schema identity"),
        ):
            validate_authority_identity(value, label)
        for column, label in (
            ("payload_sha256", "Adaptation payload SHA-256"),
            ("source_data_sha256", "Adaptation source data SHA-256"),
            ("destination_package_sha256", "Adaptation destination package SHA-256"),
            ("destination_schema_sha256", "Adaptation destination schema SHA-256"),
        ):
            validate_authority_identity(str(row[column]), label, sha256=True)
        for column, label in (
            ("source_package_sha256", "Adaptation source package SHA-256"),
            ("source_schema_id", "Adaptation source schema identity"),
            ("source_schema_sha256", "Adaptation source schema SHA-256"),
        ):
            if row[column] is not None:
                validate_authority_identity(
                    str(row[column]), label, sha256=column.endswith("sha256")
                )
        source = connection.execute(
            "SELECT * FROM participants WHERE campaign_id = ? AND participant_id = ?",
            (str(row["source_campaign_id"]), str(row["participant_id"])),
        ).fetchone()
        if source is None:
            raise ValueError("Stored participant adaptation source is unavailable")
        source_data = cast(SafePayload, _parse_mapping(str(source["data_json"])))
        source_identity = (
            str(source["kind"]),
            str(source["package_id"]),
            str(source["package_version"]),
            None if source["package_sha256"] is None else str(source["package_sha256"]),
            None if source["schema_id"] is None else str(source["schema_id"]),
            None if source["schema_sha256"] is None else str(source["schema_sha256"]),
            hashlib.sha256(canonical_json_bytes(source_data)).hexdigest(),
        )
        recorded_source_identity = (
            str(row["source_kind"]),
            str(row["source_package_id"]),
            str(row["source_package_version"]),
            None if row["source_package_sha256"] is None else str(row["source_package_sha256"]),
            None if row["source_schema_id"] is None else str(row["source_schema_id"]),
            None if row["source_schema_sha256"] is None else str(row["source_schema_sha256"]),
            str(row["source_data_sha256"]),
        )
        artifact = artifact_from_adaptation_row(row)
        expected_payload_sha256 = adaptation_payload_sha256(
            source_campaign_id=str(row["source_campaign_id"]),
            participant_id=artifact.participant_id,
            destination_package_id=artifact.package_id,
            destination_package_version=artifact.package_version,
            destination_package_sha256=artifact.package_sha256 or "",
            destination_kind=artifact.kind,
            destination_schema_id=artifact.schema_id or "",
            destination_schema_sha256=artifact.schema_sha256 or "",
            data=artifact.data,
        )
        declaration = connection.execute(
            "SELECT schema_id, schema_sha256, schema_json "
            "FROM package_participant_artifacts WHERE package_id = ? AND version = ? "
            "AND content_sha256 = ? AND kind = ?",
            (
                artifact.package_id,
                artifact.package_version,
                artifact.package_sha256,
                artifact.kind,
            ),
        ).fetchone()
        if (
            source_identity[:-1] != recorded_source_identity[:-1]
            or recorded_source_identity[-1] not in {
                hashlib.sha256(canonical_json_bytes(value)).hexdigest()
                for value in participant_data_history(
                    connection, str(row["source_campaign_id"]),
                    str(row["participant_id"]), source_data,
                )
            }
            or str(row["payload_sha256"]) != expected_payload_sha256
            or declaration is None
            or str(declaration["schema_id"]) != artifact.schema_id
            or str(declaration["schema_sha256"]) != artifact.schema_sha256
        ):
            raise ValueError("Stored participant adaptation authority is invalid")
        validate_json_value(
            cast(JsonValue, artifact.data),
            _parse_mapping(str(declaration["schema_json"])),
            label=f"Participant {artifact.participant_id}",
        )
        adaptations[operation_id] = artifact
    quarantines = {
        (str(row["campaign_id"]), str(row["participant_id"])): str(row["reason_code"])
        for row in connection.execute("SELECT * FROM participant_quarantines")
    }
    seen_quarantines: set[tuple[str, str]] = set()
    participant_rows = connection.execute(
        "SELECT * FROM participants ORDER BY campaign_id, participant_id"
    )
    for row in participant_rows:
        identity = (str(row["campaign_id"]), str(row["participant_id"]))
        data = cast(SafePayload, _parse_mapping(str(row["data_json"])))
        participant = Participant(
            participant_id=identity[1],
            kind=str(row["kind"]),
            package_id=str(row["package_id"]),
            package_version=str(row["package_version"]),
            data=data,
            package_sha256=(None if row["package_sha256"] is None else str(row["package_sha256"])),
            schema_id=None if row["schema_id"] is None else str(row["schema_id"]),
            schema_sha256=(None if row["schema_sha256"] is None else str(row["schema_sha256"])),
            validation_status=ParticipantValidationStatus(str(row["validation_status"])),
            adaptation_id=(None if row["adaptation_id"] is None else str(row["adaptation_id"])),
        )
        if participant.validation_status is ParticipantValidationStatus.QUARANTINED:
            if (
                participant.schema_id is not None
                or participant.schema_sha256 is not None
                or participant.adaptation_id is not None
            ):
                raise ValueError("Quarantined participant claims schema proof")
            if identity not in quarantines or not quarantines[identity]:
                raise ValueError("Quarantined participant lacks evidence")
            seen_quarantines.add(identity)
            continue
        declaration = connection.execute(
            "SELECT schema_id, schema_sha256, schema_json FROM package_participant_artifacts "
            "WHERE package_id = ? AND version = ? AND content_sha256 = ? AND kind = ?",
            (
                participant.package_id,
                participant.package_version,
                participant.package_sha256,
                participant.kind,
            ),
        ).fetchone()
        lock_membership = connection.execute(
            "SELECT 1 FROM campaign_locks AS lock "
            "JOIN campaign_lock_memberships AS membership ON membership.lock_id = lock.lock_id "
            "WHERE lock.campaign_id = ? AND lock.predecessor_lock_id IS NULL "
            "AND membership.package_id = ? AND membership.version = ? "
            "AND membership.content_sha256 = ?",
            (
                identity[0],
                participant.package_id,
                participant.package_version,
                participant.package_sha256,
            ),
        ).fetchone()
        if (
            declaration is None
            or lock_membership is None
            or str(declaration["schema_id"]) != participant.schema_id
            or str(declaration["schema_sha256"]) != participant.schema_sha256
            or identity in quarantines
        ):
            raise ValueError("Validated participant exact authority is invalid")
        validate_json_value(
            cast(JsonValue, data),
            _parse_mapping(str(declaration["schema_json"])),
            label=f"Participant {participant.participant_id}",
        )
        if participant.adaptation_id is not None:
            adaptation = adaptations.get(participant.adaptation_id)
            if adaptation is None or any(
                left != right
                for left, right in (
                    (participant.participant_id, adaptation.participant_id),
                    (participant.kind, adaptation.kind),
                    (participant.package_id, adaptation.package_id),
                    (participant.package_version, adaptation.package_version),
                    (participant.package_sha256, adaptation.package_sha256),
                    (participant.schema_id, adaptation.schema_id),
                    (participant.schema_sha256, adaptation.schema_sha256),
                    (True, adaptation.data in participant_data_history(
                        connection, identity[0], participant.participant_id, participant.data,
                    )),
                )
            ):
                raise ValueError("Participant adaptation provenance does not match")
    if seen_quarantines != set(quarantines):
        raise ValueError("Participant quarantine evidence is orphaned")
