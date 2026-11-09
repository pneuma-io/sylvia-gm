"""Caller-transaction SQLite helpers for the sole F3 authority store."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any, cast

from sylvia_gm.domain.authority import (
    JsonValue,
    PackageDependency,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    TurnStatus,
    owned_json_value,
    owned_payload,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, SchemaIdentityError
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    ActivationCommand,
    ActivationResult,
    ActivationStatus,
    CandidateStatus,
    PackageCandidate,
    PackageLockAvailability,
    PackageLockRecord,
    PackageManifest,
    PackageStagingRecord,
    ParticipantArtifactDeclaration,
    ReclamationResult,
    StatePredicate,
    SurfaceDeclaration,
    ToolDeclaration,
    activation_payload_sha256,
    canonical_json_bytes,
    closure_sha256,
    manifest_from_record,
    parse_campaign_setting_knowledge_bytes,
    parse_canonical_json_bytes,
    parse_content_declarations_bytes,
    resolve_exact_closure,
    validate_campaign_setting_knowledge_links,
    validate_content_declaration_links,
)
from sylvia_gm.infrastructure import sqlite_campaign, sqlite_draft_history, sqlite_state_ownership
from sylvia_gm.infrastructure.package_archive import AdmittedPackageArchive

COMPATIBLE_UPDATE_AUTHORITY = "campaign-compatible-update.v1"


def _compatible_migration_identity(migration_id: str) -> bool:
    token = migration_id.removeprefix("compatible-update-")
    return (token != migration_id and len(token) == 64
            and all(c in "0123456789abcdef" for c in token))


MIGRATION_3_NAME = "f3_sqlite_package_library"
MIGRATION_3_SQL = """
ALTER TABLE package_dependencies ADD COLUMN dependency_namespace TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE campaigns ADD COLUMN current_lock_id TEXT;
ALTER TABLE actions ADD COLUMN lock_id TEXT;
ALTER TABLE campaign_revisions ADD COLUMN activation_id TEXT;

CREATE TABLE package_blobs (
    blob_sha256 TEXT PRIMARY KEY,
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0)
);
CREATE TABLE package_blob_chunks (
    blob_sha256 TEXT NOT NULL,
    chunk_order INTEGER NOT NULL CHECK (chunk_order >= 0),
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    chunk_sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (blob_sha256, chunk_order),
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256) ON DELETE CASCADE
);
CREATE TABLE package_manifests (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary', 'dependency')),
    canonical_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    compatibility_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('published', 'reclaimed')),
    PRIMARY KEY (package_id, version, content_sha256),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE package_manifest_files (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    file_order INTEGER NOT NULL CHECK (file_order >= 0),
    portable_path TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    blob_sha256 TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, file_order),
    UNIQUE (package_id, version, content_sha256, portable_path),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE,
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256)
);
CREATE TABLE package_exact_dependencies (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    dependency_order INTEGER NOT NULL CHECK (dependency_order >= 0),
    dependency_namespace TEXT NOT NULL,
    dependency_kind TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    dependency_version TEXT NOT NULL,
    dependency_content_sha256 TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, dependency_order),
    UNIQUE (package_id, version, content_sha256, dependency_namespace),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE,
    FOREIGN KEY (dependency_id, dependency_version, dependency_content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256)
);
CREATE TABLE package_capabilities (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    capability_order INTEGER NOT NULL CHECK (capability_order >= 0),
    capability TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, capability_order),
    UNIQUE (package_id, version, content_sha256, capability),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_tools (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    tool_order INTEGER NOT NULL CHECK (tool_order >= 0),
    tool_name TEXT NOT NULL,
    capability TEXT NOT NULL,
    input_schema_json TEXT NOT NULL,
    state_predicates_json TEXT NOT NULL,
    turn_statuses_json TEXT NOT NULL,
    writers_json TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, tool_order),
    UNIQUE (package_id, version, content_sha256, tool_name),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_prompt_fragments (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    surface_order INTEGER NOT NULL CHECK (surface_order >= 0),
    fragment_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, surface_order),
    UNIQUE (package_id, version, content_sha256, fragment_id),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_ui_surfaces (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    surface_order INTEGER NOT NULL CHECK (surface_order >= 0),
    surface_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, surface_order),
    UNIQUE (package_id, version, content_sha256, surface_id),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_staging (
    install_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('staging', 'published', 'failed')),
    manifest_json TEXT NOT NULL,
    report_json TEXT NOT NULL
);
CREATE TABLE package_staging_chunks (
    install_id TEXT NOT NULL,
    file_order INTEGER NOT NULL CHECK (file_order >= 0),
    chunk_order INTEGER NOT NULL CHECK (chunk_order >= 0),
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    portable_path TEXT NOT NULL,
    chunk_sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (install_id, file_order, chunk_order),
    FOREIGN KEY (install_id) REFERENCES package_staging(install_id) ON DELETE CASCADE
);
CREATE TABLE package_candidates (
    candidate_id TEXT PRIMARY KEY,
    install_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('validated', 'activated', 'failed')),
    closure_sha256 TEXT NOT NULL,
    report_json TEXT NOT NULL,
    FOREIGN KEY (install_id) REFERENCES package_staging(install_id)
);
CREATE TABLE package_candidate_memberships (
    candidate_id TEXT NOT NULL,
    closure_order INTEGER NOT NULL CHECK (closure_order >= 0),
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (candidate_id, closure_order),
    UNIQUE (candidate_id, package_id),
    FOREIGN KEY (candidate_id) REFERENCES package_candidates(candidate_id) ON DELETE CASCADE,
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256)
);
CREATE TABLE campaign_locks (
    lock_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    closure_sha256 TEXT NOT NULL,
    availability TEXT NOT NULL CHECK (availability IN ('available', 'legacy_unavailable')),
    predecessor_lock_id TEXT,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (predecessor_lock_id) REFERENCES campaign_locks(lock_id)
);
CREATE TABLE campaign_lock_memberships (
    lock_id TEXT NOT NULL,
    closure_order INTEGER NOT NULL CHECK (closure_order >= 0),
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary', 'dependency')),
    PRIMARY KEY (lock_id, closure_order),
    UNIQUE (lock_id, package_id),
    FOREIGN KEY (lock_id) REFERENCES campaign_locks(lock_id) ON DELETE CASCADE,
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE package_activations (
    activation_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    old_lock_id TEXT NOT NULL,
    new_lock_id TEXT NOT NULL,
    migration_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('committed', 'failed')),
    resulting_revision INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES package_candidates(candidate_id),
    FOREIGN KEY (old_lock_id) REFERENCES campaign_locks(lock_id),
    FOREIGN KEY (new_lock_id) REFERENCES campaign_locks(lock_id)
);
CREATE TABLE package_read_leases (
    lease_id TEXT PRIMARY KEY,
    blob_sha256 TEXT NOT NULL,
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256)
);
CREATE TABLE package_recovery_roots (
    root_id TEXT PRIMARY KEY,
    blob_sha256 TEXT NOT NULL,
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256)
);
"""


def _dump(value: object) -> str:
    return str(canonical_json_bytes(value).decode("utf-8"))


def _parse(value: str) -> object:
    return parse_canonical_json_bytes(value.encode("utf-8"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _legacy_lock_id(campaign_id: str) -> str:
    return "legacy-lock-" + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:32]


def _lock_id(campaign_id: str, revision: int, closure: str) -> str:
    value = _sha(
        _dump({"campaign_id": campaign_id, "revision": revision, "closure": closure}).encode()
    )
    return f"lock-{value[:40]}"


def migrate_legacy_campaigns(connection: sqlite3.Connection) -> None:
    """Preserve schema-1/2 rows as explicit metadata-only unavailable locks."""

    campaigns = tuple(
        connection.execute("SELECT campaign_id, revision FROM campaigns ORDER BY campaign_id")
    )
    for campaign in campaigns:
        campaign_id = str(campaign["campaign_id"])
        rows = tuple(
            connection.execute(
                "SELECT package_id, version, sha256, role, load_order "
                "FROM campaign_package_memberships WHERE campaign_id = ? ORDER BY load_order",
                (campaign_id,),
            )
        )
        representation = [
            {
                "id": str(row["package_id"]),
                "version": str(row["version"]),
                "content_sha256": str(row["sha256"]),
                "role": str(row["role"]),
                "load_order": int(row["load_order"]),
            }
            for row in rows
        ]
        closure = _sha(canonical_json_bytes(representation))
        lock_id = _legacy_lock_id(campaign_id)
        connection.execute(
            "INSERT INTO campaign_locks VALUES (?, ?, ?, 'legacy_unavailable', NULL, 0)",
            (lock_id, campaign_id, closure),
        )
        for row in rows:
            connection.execute(
                "INSERT INTO campaign_lock_memberships VALUES (?, ?, ?, ?, ?, ?)",
                (
                    lock_id,
                    int(row["load_order"]),
                    str(row["package_id"]),
                    str(row["version"]),
                    str(row["sha256"]),
                    str(row["role"]),
                ),
            )
        connection.execute(
            "UPDATE campaigns SET current_lock_id = ? WHERE campaign_id = ?",
            (lock_id, campaign_id),
        )
        connection.execute(
            "UPDATE actions SET lock_id = ? WHERE campaign_id = ?",
            (lock_id, campaign_id),
        )


def clear_stale_read_leases(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM package_read_leases")


def begin_staging(
    connection: sqlite3.Connection,
    install_id: str,
    manifest_json: str,
) -> None:
    existing = connection.execute(
        "SELECT status, manifest_json FROM package_staging WHERE install_id = ?", (install_id,)
    ).fetchone()
    if existing is not None:
        if str(existing["manifest_json"]) != manifest_json:
            raise IdempotencyConflict("Install identity payload does not match")
        if str(existing["status"]) != "staging":
            raise IdempotencyConflict("Install identity is already terminal")
        connection.execute("DELETE FROM package_staging_chunks WHERE install_id = ?", (install_id,))
        return
    connection.execute(
        "INSERT INTO package_staging VALUES (?, 'staging', ?, ?)",
        (install_id, manifest_json, _dump({"code": "staging", "label": "Package staging"})),
    )


def append_staging_chunk(
    connection: sqlite3.Connection,
    *,
    install_id: str,
    file_order: int,
    chunk_order: int,
    package_id: str,
    version: str,
    content_sha256: str,
    portable_path: str,
    chunk: bytes,
) -> None:
    connection.execute(
        "INSERT INTO package_staging_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            install_id,
            file_order,
            chunk_order,
            package_id,
            version,
            content_sha256,
            portable_path,
            _sha(chunk),
            chunk,
        ),
    )


def _exact_rows(
    connection: sqlite3.Connection, query: str, parameters: tuple[object, ...]
) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in connection.execute(query, parameters))


def _insert_or_compare_blob(
    connection: sqlite3.Connection,
    blob_sha256: str,
    chunks: tuple[bytes, ...],
) -> None:
    byte_count = sum(map(len, chunks))
    if _sha(b"".join(chunks)) != blob_sha256:
        raise CommandRejected("Staged package blob hash does not match", code="blob_hash_mismatch")
    existing = connection.execute(
        "SELECT byte_count, chunk_count FROM package_blobs WHERE blob_sha256 = ?",
        (blob_sha256,),
    ).fetchone()
    expected_chunks = tuple(
        (order, len(chunk), _sha(chunk), chunk) for order, chunk in enumerate(chunks)
    )
    if existing is not None:
        stored = _exact_rows(
            connection,
            "SELECT chunk_order, byte_count, chunk_sha256, data FROM package_blob_chunks "
            "WHERE blob_sha256 = ? ORDER BY chunk_order",
            (blob_sha256,),
        )
        if (
            int(existing["byte_count"]) != byte_count
            or int(existing["chunk_count"]) != len(chunks)
            or stored != expected_chunks
        ):
            raise CommandRejected(
                "Immutable package blob conflicts with stored bytes",
                code="package_identity_conflict",
            )
        return
    connection.execute(
        "INSERT INTO package_blobs VALUES (?, ?, ?)",
        (blob_sha256, byte_count, len(chunks)),
    )
    for order, chunk in enumerate(chunks):
        connection.execute(
            "INSERT INTO package_blob_chunks VALUES (?, ?, ?, ?, ?)",
            (blob_sha256, order, len(chunk), _sha(chunk), chunk),
        )


def _manifest_record(manifest: PackageManifest) -> Mapping[str, object]:
    parsed = _parse(manifest.canonical_json)
    if not isinstance(parsed, Mapping):
        raise CommandRejected("Package manifest is invalid", code="manifest_invalid")
    return cast(Mapping[str, object], parsed)


def _insert_or_compare_manifest(connection: sqlite3.Connection, manifest: PackageManifest) -> None:
    identity = manifest.identity
    capabilities_json = _dump(manifest.capabilities)
    connection.execute(
        "INSERT OR IGNORE INTO packages(package_id, version, sha256, capabilities_json) "
        "VALUES (?, ?, ?, ?)",
        (*identity, capabilities_json),
    )
    package = connection.execute(
        "SELECT capabilities_json FROM packages WHERE package_id = ? "
        "AND version = ? AND sha256 = ?",
        identity,
    ).fetchone()
    if package is None or str(package["capabilities_json"]) != capabilities_json:
        raise CommandRejected(
            "Package identity conflicts with immutable metadata", code="package_identity_conflict"
        )
    for order, dependency in enumerate(manifest.dependencies):
        connection.execute(
            "INSERT OR IGNORE INTO package_dependencies "
            "(package_id, version, sha256, dependency_order, dependency_id, "
            "dependency_version, dependency_sha256, dependency_kind, dependency_namespace) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *identity,
                order,
                dependency.package_id,
                dependency.version,
                dependency.content_sha256,
                dependency.kind,
                dependency.namespace,
            ),
        )
    stored_legacy_dependencies = _exact_rows(
        connection,
        "SELECT dependency_order, dependency_id, dependency_version, dependency_sha256, "
        "dependency_kind, dependency_namespace FROM package_dependencies "
        "WHERE package_id = ? AND version = ? AND sha256 = ? ORDER BY dependency_order",
        identity,
    )
    expected_legacy_dependencies = tuple(
        (
            order,
            dep.package_id,
            dep.version,
            dep.content_sha256,
            dep.kind,
            dep.namespace,
        )
        for order, dep in enumerate(manifest.dependencies)
    )
    if stored_legacy_dependencies != expected_legacy_dependencies:
        raise CommandRejected(
            "Package dependency metadata conflicts", code="package_identity_conflict"
        )

    existing = connection.execute(
        "SELECT role, canonical_json, metadata_json, provenance_json, compatibility_json, status "
        "FROM package_manifests WHERE package_id = ? AND version = ? AND content_sha256 = ?",
        identity,
    ).fetchone()
    expected = (
        manifest.role.value,
        manifest.canonical_json,
        _dump(manifest.metadata),
        _dump(manifest.provenance),
        _dump(manifest.compatibility),
        "published",
    )
    if existing is not None:
        if tuple(existing) != expected:
            raise CommandRejected(
                "Package manifest identity conflicts", code="package_identity_conflict"
            )
        sqlite_campaign.insert_or_compare_declarations(connection, manifest)
        return
    connection.execute(
        "INSERT INTO package_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published')",
        (*identity, *expected[:-1]),
    )
    for order, capability in enumerate(manifest.capabilities):
        connection.execute(
            "INSERT INTO package_capabilities VALUES (?, ?, ?, ?, ?)",
            (*identity, order, capability),
        )
    for order, tool in enumerate(manifest.tools):
        connection.execute(
            "INSERT INTO package_tools VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *identity,
                order,
                tool.name,
                tool.capability,
                _dump(tool.input_schema),
                _dump(
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
                _dump(tool.turn_statuses),
                _dump(tool.writers),
            ),
        )
    for order, surface in enumerate(manifest.prompt_fragments):
        connection.execute(
            "INSERT INTO package_prompt_fragments VALUES (?, ?, ?, ?, ?, ?)",
            (*identity, order, surface.identity, surface.capability),
        )
    for order, surface in enumerate(manifest.ui_surfaces):
        connection.execute(
            "INSERT INTO package_ui_surfaces VALUES (?, ?, ?, ?, ?, ?)",
            (*identity, order, surface.identity, surface.capability),
        )
    sqlite_campaign.insert_or_compare_declarations(connection, manifest)


def publish_archive(
    connection: sqlite3.Connection,
    admitted: AdmittedPackageArchive,
    install_id: str,
) -> PackageCandidate:
    staging = connection.execute(
        "SELECT status, manifest_json FROM package_staging WHERE install_id = ?", (install_id,)
    ).fetchone()
    if staging is None or str(staging["status"]) != "staging":
        raise CommandRejected("Package staging is unavailable", code="staging_unavailable")
    manifests = admitted.manifests
    for manifest in manifests:
        capabilities_json = _dump(manifest.capabilities)
        connection.execute(
            "INSERT OR IGNORE INTO packages(package_id, version, sha256, capabilities_json) "
            "VALUES (?, ?, ?, ?)",
            (*manifest.identity, capabilities_json),
        )
        row = connection.execute(
            "SELECT capabilities_json FROM packages WHERE package_id = ? "
            "AND version = ? AND sha256 = ?",
            manifest.identity,
        ).fetchone()
        if row is None or str(row["capabilities_json"]) != capabilities_json:
            raise CommandRejected(
                "Package identity conflicts with immutable metadata",
                code="package_identity_conflict",
            )
    for manifest in manifests:
        _insert_or_compare_manifest(connection, manifest)
    file_order = 0
    expected_chunked_file_orders: set[int] = set()
    for manifest in manifests:
        for manifest_file in manifest.files:
            rows = tuple(
                connection.execute(
                    "SELECT chunk_order, chunk_sha256, data FROM package_staging_chunks "
                    "WHERE install_id = ? AND file_order = ? ORDER BY chunk_order",
                    (install_id, file_order),
                )
            )
            if tuple(int(row["chunk_order"]) for row in rows) != tuple(range(len(rows))):
                raise CommandRejected("Staged chunk order is incomplete", code="staging_invalid")
            chunks = tuple(bytes(row["data"]) for row in rows)
            if any(
                _sha(chunk) != str(row["chunk_sha256"])
                for row, chunk in zip(rows, chunks, strict=True)
            ):
                raise CommandRejected("Staged chunk hash is invalid", code="staging_invalid")
            if sum(map(len, chunks)) != manifest_file.byte_count:
                raise CommandRejected("Staged file byte count is invalid", code="staging_invalid")
            if manifest_file.byte_count:
                expected_chunked_file_orders.add(file_order)
            _insert_or_compare_blob(connection, manifest_file.sha256, chunks)
            existing_file = connection.execute(
                "SELECT portable_path, byte_count, blob_sha256 FROM package_manifest_files "
                "WHERE package_id = ? AND version = ? AND content_sha256 = ? AND file_order = ?",
                (*manifest.identity, manifest.files.index(manifest_file)),
            ).fetchone()
            expected_file = (manifest_file.path, manifest_file.byte_count, manifest_file.sha256)
            if existing_file is None:
                connection.execute(
                    "INSERT INTO package_manifest_files VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*manifest.identity, manifest.files.index(manifest_file), *expected_file),
                )
            elif tuple(existing_file) != expected_file:
                raise CommandRejected(
                    "Package file metadata conflicts", code="package_identity_conflict"
                )
            file_order += 1
        expected_dependencies = tuple(
            (
                order,
                dependency.namespace,
                dependency.kind,
                dependency.package_id,
                dependency.version,
                dependency.content_sha256,
            )
            for order, dependency in enumerate(manifest.dependencies)
        )
        existing_dependencies = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT dependency_order, dependency_namespace, dependency_kind, dependency_id, "
                "dependency_version, dependency_content_sha256 "
                "FROM package_exact_dependencies WHERE package_id = ? AND version = ? "
                "AND content_sha256 = ? ORDER BY dependency_order",
                manifest.identity,
            )
        )
        if existing_dependencies:
            if existing_dependencies != expected_dependencies:
                raise CommandRejected(
                    "Package dependency metadata conflicts",
                    code="package_identity_conflict",
                )
        else:
            for expected_dependency in expected_dependencies:
                connection.execute(
                    "INSERT INTO package_exact_dependencies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*manifest.identity, *expected_dependency),
                )
    staged_file_orders = {
        int(row[0])
        for row in connection.execute(
            "SELECT DISTINCT file_order FROM package_staging_chunks "
            "WHERE install_id = ? ORDER BY file_order",
            (install_id,),
        )
    }
    if staged_file_orders != expected_chunked_file_orders:
        raise CommandRejected("Staged file set is incomplete", code="staging_invalid")
    package_lock = admitted.package_lock
    closure = closure_sha256(package_lock)
    candidate_id = f"candidate-{install_id}"
    report = owned_payload({"code": "validated", "label": "Package candidate validated"})
    connection.execute(
        "INSERT INTO package_candidates VALUES (?, ?, 'validated', ?, ?)",
        (candidate_id, install_id, closure, _dump(report)),
    )
    by_identity = {manifest.identity: manifest for manifest in manifests}
    for entry in package_lock.entries:
        if (entry.package_id, entry.version, entry.sha256) not in by_identity:
            raise CommandRejected("Candidate closure is incomplete", code="closure_invalid")
        connection.execute(
            "INSERT INTO package_candidate_memberships VALUES (?, ?, ?, ?, ?)",
            (candidate_id, entry.load_order, entry.package_id, entry.version, entry.sha256),
        )
    connection.execute(
        "UPDATE package_staging SET status = 'published', report_json = ? WHERE install_id = ?",
        (_dump(report), install_id),
    )
    return PackageCandidate(
        candidate_id,
        install_id,
        CandidateStatus.VALIDATED,
        closure,
        package_lock,
        report,
    )


@lru_cache(maxsize=64)
def _manifest_from_json(canonical_json: str) -> PackageManifest:
    """Reuse immutable parsing by exact bytes; callers still read current database rows."""

    return manifest_from_record(_parse(canonical_json))


def _manifest_from_identity(
    connection: sqlite3.Connection, identity: tuple[str, str, str]
) -> PackageManifest:
    row = connection.execute(
        "SELECT canonical_json, status FROM package_manifests "
        "WHERE package_id = ? AND version = ? AND content_sha256 = ?",
        identity,
    ).fetchone()
    if row is None or str(row["status"]) != "published":
        raise CommandRejected("Exact package manifest is unavailable", code="package_unavailable")
    return _manifest_from_json(str(row["canonical_json"]))


def exact_manifest(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str],
) -> PackageManifest:
    """Return one immutable published manifest for read-only package inspection."""

    return _manifest_from_identity(connection, identity)


def primary_manifest_for_lock(
    connection: sqlite3.Connection,
    lock_id: str,
) -> PackageManifest | None:
    """Return the exact primary manifest for a lock, or ``None`` for no-rules."""

    read_lock_record(connection, lock_id)
    rows = tuple(
        connection.execute(
            "SELECT package_id, version, content_sha256 FROM campaign_lock_memberships "
            "WHERE lock_id = ? AND role = 'primary' ORDER BY closure_order",
            (lock_id,),
        )
    )
    member_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM campaign_lock_memberships WHERE lock_id = ?",
            (lock_id,),
        ).fetchone()[0]
    )
    if not rows and member_count == 0:
        return None
    if len(rows) != 1:
        raise CommandRejected(
            "Campaign package lock does not have one exact primary package",
            code="package_lock_invalid",
        )
    row = rows[0]
    manifest = _manifest_from_identity(
        connection,
        (str(row["package_id"]), str(row["version"]), str(row["content_sha256"])),
    )
    if manifest.role is not PackageRole.PRIMARY:
        raise CommandRejected(
            "Campaign package lock primary role conflicts with its manifest",
            code="package_lock_invalid",
        )
    return manifest


def candidate_participant_declarations(
    connection: sqlite3.Connection, candidate_id: str
) -> tuple[ParticipantArtifactDeclaration, ...]:
    rows = tuple(
        connection.execute(
            "SELECT package_id, version, content_sha256 FROM package_candidate_memberships "
            "WHERE candidate_id = ? ORDER BY closure_order",
            (candidate_id,),
        )
    )
    if not rows:
        raise CommandRejected("Package candidate is unavailable", code="candidate_unavailable")
    declarations: list[ParticipantArtifactDeclaration] = []
    for row in rows:
        declarations.extend(
            _manifest_from_identity(
                connection,
                (str(row["package_id"]), str(row["version"]), str(row["content_sha256"])),
            ).participant_artifacts
        )
    return tuple(declarations)


def exact_participant_declarations(
    connection: sqlite3.Connection, identity: tuple[str, str, str]
) -> tuple[ParticipantArtifactDeclaration, ...]:
    return cast(
        tuple[ParticipantArtifactDeclaration, ...],
        _manifest_from_identity(connection, identity).participant_artifacts,
    )


def _package_lock_from_membership_rows(
    connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
) -> PackageLock:
    entries: list[PackageLockEntry] = []
    for row in rows:
        identity = (str(row["package_id"]), str(row["version"]), str(row["content_sha256"]))
        manifest_row = connection.execute(
            "SELECT 1 FROM package_manifests WHERE package_id = ? AND version = ? "
            "AND content_sha256 = ? AND status = 'published'",
            identity,
        ).fetchone()
        if manifest_row is not None:
            manifest = _manifest_from_identity(connection, identity)
            dependencies = tuple(
                PackageDependency(
                    dep.package_id,
                    dep.version,
                    dep.content_sha256,
                    dep.kind,
                    dep.namespace,
                )
                for dep in manifest.dependencies
            )
            capabilities = manifest.capabilities
        else:
            capabilities_value = _parse(
                str(
                    connection.execute(
                        "SELECT capabilities_json FROM packages WHERE package_id = ? "
                        "AND version = ? AND sha256 = ?",
                        identity,
                    ).fetchone()[0]
                )
            )
            capabilities = tuple(cast(Sequence[str], capabilities_value))
            dependencies = tuple(
                PackageDependency(
                    str(dep["dependency_id"]),
                    str(dep["dependency_version"]),
                    str(dep["dependency_sha256"]),
                    str(dep["dependency_kind"]),
                    str(dep["dependency_namespace"]),
                )
                for dep in connection.execute(
                    "SELECT * FROM package_dependencies WHERE package_id = ? AND version = ? "
                    "AND sha256 = ? ORDER BY dependency_order",
                    identity,
                )
            )
        entries.append(
            PackageLockEntry(
                identity[0],
                identity[1],
                identity[2],
                PackageRole(str(row["role"])),
                int(row["closure_order"]),
                dependencies,
                capabilities,
            )
        )
    return PackageLock(tuple(entries))


def read_candidate(connection: sqlite3.Connection, candidate_id: str) -> PackageCandidate:
    row = connection.execute(
        "SELECT * FROM package_candidates WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise CommandRejected("Package candidate is unavailable", code="candidate_unavailable")
    memberships = tuple(
        connection.execute(
            "SELECT closure_order, package_id, version, content_sha256, "
            "CASE WHEN closure_order = 0 THEN 'primary' ELSE 'dependency' END AS role "
            "FROM package_candidate_memberships WHERE candidate_id = ? ORDER BY closure_order",
            (candidate_id,),
        )
    )
    lock = _package_lock_from_membership_rows(connection, memberships)
    return PackageCandidate(
        candidate_id=str(row["candidate_id"]),
        install_id=str(row["install_id"]),
        status=CandidateStatus(str(row["status"])),
        closure_sha256=str(row["closure_sha256"]),
        package_lock=lock,
        validation_report=cast(Mapping[str, JsonValue], _parse(str(row["report_json"]))),
    )


def list_candidates(connection: sqlite3.Connection) -> tuple[PackageCandidate, ...]:
    return tuple(
        read_candidate(connection, str(row["candidate_id"]))
        for row in connection.execute(
            "SELECT candidate_id FROM package_candidates ORDER BY rowid DESC"
        )
    )


def read_staging(connection: sqlite3.Connection, install_id: str) -> PackageStagingRecord:
    row = connection.execute(
        "SELECT status FROM package_staging WHERE install_id = ?", (install_id,)
    ).fetchone()
    if row is None:
        raise CommandRejected("Package staging is unavailable", code="staging_unavailable")
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM package_staging_chunks WHERE install_id = ?", (install_id,)
        ).fetchone()[0]
    )
    return PackageStagingRecord(install_id, str(row["status"]), count)


def verify_published_lock(connection: sqlite3.Connection, package_lock: PackageLock) -> None:
    if not package_lock.entries:
        return
    manifests = tuple(
        _manifest_from_identity(connection, (entry.package_id, entry.version, entry.sha256))
        for entry in package_lock.entries
    )
    resolved = resolve_exact_closure(manifests)
    if resolved != package_lock:
        raise CommandRejected(
            "Package lock does not match published exact closure", code="package_unavailable"
        )
    for manifest in manifests:
        for file in manifest.files:
            verify_blob(connection, file.sha256)


def insert_campaign_lock(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    package_lock: PackageLock,
    revision: int,
    predecessor_lock_id: str | None,
    availability: PackageLockAvailability = PackageLockAvailability.AVAILABLE,
) -> PackageLockRecord:
    if availability is PackageLockAvailability.AVAILABLE:
        verify_published_lock(connection, package_lock)
    closure = closure_sha256(package_lock)
    lock_id = _lock_id(campaign_id, revision, closure)
    connection.execute(
        "INSERT INTO campaign_locks VALUES (?, ?, ?, ?, ?, ?)",
        (lock_id, campaign_id, closure, availability.value, predecessor_lock_id, revision),
    )
    for entry in package_lock.entries:
        connection.execute(
            "INSERT INTO campaign_lock_memberships VALUES (?, ?, ?, ?, ?, ?)",
            (
                lock_id,
                entry.load_order,
                entry.package_id,
                entry.version,
                entry.sha256,
                entry.role.value,
            ),
        )
    return PackageLockRecord(
        lock_id,
        campaign_id,
        closure,
        package_lock,
        availability,
        predecessor_lock_id,
        revision,
    )


def read_lock_record(connection: sqlite3.Connection, lock_id: str) -> PackageLockRecord:
    row = connection.execute(
        "SELECT * FROM campaign_locks WHERE lock_id = ?", (lock_id,)
    ).fetchone()
    if row is None:
        raise CommandRejected("Campaign package lock is unavailable", code="package_unavailable")
    memberships = tuple(
        connection.execute(
            "SELECT closure_order, package_id, version, content_sha256, role "
            "FROM campaign_lock_memberships WHERE lock_id = ? ORDER BY closure_order",
            (lock_id,),
        )
    )
    lock = _package_lock_from_membership_rows(connection, memberships)
    return PackageLockRecord(
        lock_id=lock_id,
        campaign_id=str(row["campaign_id"]),
        closure_sha256=str(row["closure_sha256"]),
        package_lock=lock,
        availability=PackageLockAvailability(str(row["availability"])),
        predecessor_lock_id=(
            None if row["predecessor_lock_id"] is None else str(row["predecessor_lock_id"])
        ),
        created_revision=int(row["created_revision"]),
    )


def current_lock_record(connection: sqlite3.Connection, campaign_id: str) -> PackageLockRecord:
    row = connection.execute(
        "SELECT current_lock_id FROM campaigns WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    if row is None or row["current_lock_id"] is None:
        raise CommandRejected("Campaign package lock is unavailable", code="package_unavailable")
    return read_lock_record(connection, str(row["current_lock_id"]))


def compatible_binding_entries(
    connection: sqlite3.Connection,
    campaign_id: str,
    source_identity: tuple[str, str, str],
) -> tuple[PackageLockEntry, ...]:
    """Follow this campaign's committed compatible transitions to one exact source.

    Retained membership, package names and another campaign's receipt alone do
    not resolve a binding. Return entries from the current lock back to source.
    """
    current = current_lock_record(connection, campaign_id)
    entry = next(
        (e for e in current.package_lock.entries if e.package_id == source_identity[0]), None,
    )
    if entry is None:
        return ()
    entries: list[PackageLockEntry] = []
    seen: set[str] = set()
    while current.lock_id not in seen:
        seen.add(current.lock_id)
        identity = (entry.package_id, entry.version, entry.sha256)
        entries.append(entry)
        if identity == source_identity:
            return tuple(entries)
        if current.predecessor_lock_id is None:
            return ()
        previous = read_lock_record(connection, current.predecessor_lock_id)
        if (previous.campaign_id != campaign_id
                or previous.created_revision >= current.created_revision):
            return ()
        prior = next(
            (e for e in previous.package_lock.entries if e.package_id == entry.package_id), None,
        )
        if prior is None or prior.role != entry.role:
            return ()
        if (prior.package_id, prior.version, prior.sha256) != identity:
            receipt = connection.execute(
                "SELECT * FROM package_activations WHERE campaign_id = ? "
                "AND old_lock_id = ? AND new_lock_id = ? AND status = 'committed'",
                (campaign_id, previous.lock_id, current.lock_id),
            ).fetchone()
            if receipt is None:
                return ()
            report = _parse(str(receipt["result_json"]))
            if (
                not _compatible_migration_identity(str(receipt["migration_id"]))
                or not isinstance(report, Mapping)
                or report.get("authority") != COMPATIBLE_UPDATE_AUTHORITY
                or _activation_result_from_row(receipt).resulting_revision
                != current.created_revision
            ):
                return ()
        current, entry = previous, prior
    return ()


def participant_owner_is_current(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    source_identity: tuple[str, str, str],
    actor_identity: tuple[str, str, str],
    kind: str,
    schema_id: str,
    schema_sha256: str,
) -> bool:
    """Require current exact authority and unchanged character contracts along lineage."""
    entries = compatible_binding_entries(connection, campaign_id, source_identity)
    if not entries or (
        entries[0].package_id, entries[0].version, entries[0].sha256
    ) != actor_identity:
        return False
    declaration = next(
        (item for item in exact_participant_declarations(connection, source_identity)
         if item.kind == kind and item.schema_id == schema_id
         and item.schema_sha256 == schema_sha256), None,
    )
    return declaration is not None and all(
        declaration in exact_participant_declarations(
            connection, (e.package_id, e.version, e.sha256),
        )
        for e in entries
    )


def lock_is_ancestor(connection: sqlite3.Connection, campaign_id: str, lock_id: str) -> bool:
    """Follow the current lock's authenticated predecessor chain, never detached history."""
    current = current_lock_record(connection, campaign_id)
    seen: set[str] = set()
    while current.lock_id not in seen:
        seen.add(current.lock_id)
        if current.campaign_id != campaign_id:
            return False
        if current.lock_id == lock_id:
            return True
        if current.predecessor_lock_id is None:
            return False
        previous = read_lock_record(connection, current.predecessor_lock_id)
        if previous.created_revision >= current.created_revision:
            return False
        current = previous
    return False


def lock_lineage(connection: sqlite3.Connection, campaign_id: str) -> tuple[PackageLockRecord, ...]:
    return tuple(
        read_lock_record(connection, str(row["lock_id"]))
        for row in connection.execute(
            "SELECT lock_id FROM campaign_locks WHERE campaign_id = ? ORDER BY created_revision",
            (campaign_id,),
        )
    )


def verify_blob(connection: sqlite3.Connection, blob_sha256: str) -> tuple[bytes, ...]:
    row = connection.execute(
        "SELECT byte_count, chunk_count FROM package_blobs WHERE blob_sha256 = ?",
        (blob_sha256,),
    ).fetchone()
    if row is None:
        raise CommandRejected("Exact package blob is unavailable", code="package_unavailable")
    chunks = tuple(
        bytes(chunk["data"])
        for chunk in connection.execute(
            "SELECT * FROM package_blob_chunks WHERE blob_sha256 = ? ORDER BY chunk_order",
            (blob_sha256,),
        )
    )
    if (
        len(chunks) != int(row["chunk_count"])
        or sum(map(len, chunks)) != int(row["byte_count"])
        or _sha(b"".join(chunks)) != blob_sha256
    ):
        raise CommandRejected("Exact package blob is corrupt", code="package_corrupt")
    return chunks


def create_read_lease(
    connection: sqlite3.Connection, lease_id: str, blob_sha256: str
) -> tuple[bytes, ...]:
    chunks = verify_blob(connection, blob_sha256)
    connection.execute("INSERT INTO package_read_leases VALUES (?, ?)", (lease_id, blob_sha256))
    return chunks


def remove_read_lease(connection: sqlite3.Connection, lease_id: str) -> None:
    connection.execute("DELETE FROM package_read_leases WHERE lease_id = ?", (lease_id,))


def declared_tool(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str],
) -> ToolDeclaration | None:
    row = connection.execute(
        "SELECT * FROM package_tools WHERE package_id = ? AND version = ? "
        "AND content_sha256 = ? AND tool_name = ?",
        identity,
    ).fetchone()
    if row is None:
        return None
    predicates_value = _parse(str(row["state_predicates_json"]))
    return ToolDeclaration(
        name=str(row["tool_name"]),
        capability=str(row["capability"]),
        input_schema=cast(Mapping[str, JsonValue], _parse(str(row["input_schema_json"]))),
        state_predicates=tuple(
            StatePredicate(
                str(item["namespace"]),
                str(item["key"]),
                owned_json_value(item["equals"]),
                bool(item.get("negated", False)),
            )
            for item in cast(Sequence[Mapping[str, object]], predicates_value)
        ),
        turn_statuses=tuple(cast(Sequence[str], _parse(str(row["turn_statuses_json"])))),
        writers=tuple(cast(Sequence[str], _parse(str(row["writers_json"])))),
    )


def surfaces_for_lock(
    connection: sqlite3.Connection,
    package_lock: PackageLock,
) -> tuple[tuple[SurfaceDeclaration, ...], tuple[SurfaceDeclaration, ...]]:
    prompts: list[SurfaceDeclaration] = []
    ui: list[SurfaceDeclaration] = []
    for entry in package_lock.entries:
        identity = (entry.package_id, entry.version, entry.sha256)
        row = connection.execute(
            "SELECT canonical_json FROM package_manifests "
            "WHERE package_id = ? AND version = ? AND content_sha256 = ?",
            identity,
        ).fetchone()
        if row is None:
            raise SchemaIdentityError("Current package surface manifest is unavailable")
        manifest = _manifest_from_json(str(row["canonical_json"]))
        prompts.extend(manifest.prompt_fragments)
        ui.extend(manifest.ui_surfaces)
    return tuple(prompts), tuple(ui)


def _activation_result_from_row(row: sqlite3.Row) -> ActivationResult:
    campaign_id = str(row["campaign_id"])
    activation_id = str(row["activation_id"])
    status = ActivationStatus(str(row["status"]))
    old_lock_id = str(row["old_lock_id"])
    new_lock_id = str(row["new_lock_id"])
    resulting_revision = int(row["resulting_revision"])
    payload_sha256 = str(row["payload_sha256"])
    expected_report = {
        "campaign_id": campaign_id,
        "activation_id": activation_id,
        "status": status.value,
        "old_lock_id": old_lock_id,
        "new_lock_id": new_lock_id,
        "resulting_revision": resulting_revision,
        "payload_sha256": payload_sha256,
    }
    report = _parse(str(row["result_json"]))
    if isinstance(report, Mapping) and "authority" in report:
        if (report["authority"] != COMPATIBLE_UPDATE_AUTHORITY
                or not _compatible_migration_identity(str(row["migration_id"]))):
            raise CommandRejected("Stored activation authority is invalid",
                                  code="activation_result_corrupt")
        expected_report["authority"] = COMPATIBLE_UPDATE_AUTHORITY
    if not isinstance(report, Mapping) or dict(report) != expected_report:
        raise CommandRejected(
            "Stored activation result is inconsistent",
            code="activation_result_corrupt",
        )
    return ActivationResult(
        campaign_id=campaign_id,
        activation_id=activation_id,
        status=status,
        old_lock_id=old_lock_id,
        new_lock_id=new_lock_id,
        resulting_revision=resulting_revision,
        payload_sha256=payload_sha256,
        replayed=True,
        resolution="durable_replay",
    )


def activation_hit(
    connection: sqlite3.Connection, activation_id: str
) -> tuple[str, ActivationResult] | None:
    row = connection.execute(
        "SELECT * FROM package_activations WHERE activation_id = ?",
        (activation_id,),
    ).fetchone()
    if row is None:
        return None
    result = _activation_result_from_row(row)
    return result.payload_sha256, result


def _new_activation_state_identity(flat_key: str, package_lock: PackageLock) -> tuple[str, str]:
    """Recover a new relational key from one exact package ID, never punctuation."""

    matches = tuple(
        entry.package_id
        for entry in package_lock.entries
        if flat_key.startswith(entry.package_id + ".")
    )
    if len(matches) != 1:
        raise CommandRejected(
            "Migrated state identity has undeclared or ambiguous package ownership",
            code="migration_invalid",
        )
    namespace = matches[0]
    key = flat_key[len(namespace) + 1 :]
    if not key:
        raise CommandRejected("Migrated state identity is invalid", code="migration_invalid")
    return namespace, key


def commit_activation(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    activation_id: str,
    candidate_id: str,
    payload_sha256: str,
    expected_revision: int,
    expected_lock_id: str,
    migration_id: str,
    migrated_state: Mapping[str, JsonValue],
    compatible_update: bool = False,
) -> ActivationResult:
    if (migration_id.startswith("compatible-update-") != compatible_update
            or compatible_update and not _compatible_migration_identity(migration_id)):
        raise CommandRejected("Compatible update authority is reserved for reviewed adoption",
                              code="reserved_migration_identity")
    existing = activation_hit(connection, activation_id)
    if existing is not None:
        if existing[0] != payload_sha256:
            raise IdempotencyConflict("Activation identity payload does not match")
        return existing[1]
    campaign = connection.execute(
        "SELECT revision, current_lock_id FROM campaigns WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    if campaign is None:
        raise CommandRejected("Campaign is unavailable")
    if (
        int(campaign["revision"]) != expected_revision
        or str(campaign["current_lock_id"]) != expected_lock_id
    ):
        raise CommandRejected("Activation authority changed", code="stale_revision")
    if (
        connection.execute(
            "SELECT 1 FROM turns WHERE campaign_id = ? AND status = ? "
            "AND NOT EXISTS (SELECT 1 FROM turns WHERE campaign_id = ? "
            "AND status = 'open') LIMIT 1",
            (campaign_id, TurnStatus.COMPLETED.value, campaign_id),
        ).fetchone()
        is None
    ):
        raise CommandRejected("Activation requires a completed TURN")
    candidate = read_candidate(connection, candidate_id)
    if candidate.status not in (CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED):
        raise CommandRejected("Candidate is not validated", code="candidate_unavailable")
    verify_published_lock(connection, candidate.package_lock)
    old = read_lock_record(connection, expected_lock_id)
    resulting_revision = expected_revision + 1
    new = insert_campaign_lock(
        connection,
        campaign_id=campaign_id,
        package_lock=candidate.package_lock,
        revision=resulting_revision,
        predecessor_lock_id=old.lock_id,
    )
    prior_state_rows = tuple(
        connection.execute(
            "SELECT namespace, state_key FROM campaign_state WHERE campaign_id = ?",
            (campaign_id,),
        )
    )
    prior_flat_keys: dict[str, tuple[str, str]] = {}
    for row in prior_state_rows:
        relational_key = (str(row["namespace"]), str(row["state_key"]))
        flat_key = f"{relational_key[0]}.{relational_key[1]}"
        if flat_key in prior_flat_keys:
            raise CommandRejected("Migrated state identity is ambiguous", code="migration_invalid")
        prior_flat_keys[flat_key] = relational_key
    prior_owners = {
        str(row["namespace"]): (
            None if row["owner_package_id"] is None else str(row["owner_package_id"])
        )
        for row in connection.execute(
            "SELECT namespace, owner_package_id FROM campaign_state_namespace_owners "
            "WHERE campaign_id = ?",
            (campaign_id,),
        )
    }
    migrated_rows: list[tuple[str, str, JsonValue]] = []
    migrated_owners: dict[str, str | None] = {}
    for flat_key, value in sorted(migrated_state.items()):
        if "." not in flat_key:
            raise CommandRejected("Migrated state identity is invalid", code="migration_invalid")
        preserved_key = prior_flat_keys.get(flat_key)
        if preserved_key is None:
            namespace, key = _new_activation_state_identity(flat_key, candidate.package_lock)
        else:
            namespace, key = preserved_key
        owner = sqlite_state_ownership.activation_owner(
            candidate.package_lock,
            namespace,
            prior_owners.get(namespace),
        )
        if namespace in migrated_owners and migrated_owners[namespace] != owner:
            raise CommandRejected("Migrated state ownership is ambiguous", code="migration_invalid")
        migrated_owners[namespace] = owner
        migrated_rows.append((namespace, key, value))
    connection.execute("DELETE FROM campaign_state WHERE campaign_id = ?", (campaign_id,))
    connection.execute(
        "DELETE FROM campaign_state_namespace_owners WHERE campaign_id = ?", (campaign_id,)
    )
    connection.execute(
        "UPDATE campaigns SET revision = ?, current_lock_id = ?, lock_hash = ? "
        "WHERE campaign_id = ?",
        (
            resulting_revision,
            new.lock_id,
            _lock_legacy_representation(new.package_lock),
            campaign_id,
        ),
    )
    for namespace, owner in sorted(migrated_owners.items()):
        connection.execute(
            "INSERT INTO campaign_state_namespace_owners VALUES (?, ?, ?)",
            (campaign_id, namespace, owner),
        )
    for namespace, key, value in migrated_rows:
        connection.execute(
            "INSERT INTO campaign_state VALUES (?, ?, ?, ?)",
            (campaign_id, namespace, key, _dump(value)),
        )
    offset = resulting_revision * 100000
    existing_identities = {
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            "SELECT package_id, version, sha256 FROM campaign_package_memberships "
            "WHERE campaign_id = ?",
            (campaign_id,),
        )
    }
    for entry in new.package_lock.entries:
        identity = (entry.package_id, entry.version, entry.sha256)
        if identity not in existing_identities:
            connection.execute(
                "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, ?, ?)",
                (campaign_id, *identity, entry.role.value, offset + entry.load_order),
            )
    result = ActivationResult(
        campaign_id,
        activation_id,
        ActivationStatus.COMMITTED,
        old.lock_id,
        new.lock_id,
        resulting_revision,
        payload_sha256,
    )
    connection.execute(
        "INSERT INTO package_activations VALUES (?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?)",
        (
            activation_id,
            campaign_id,
            payload_sha256,
            candidate_id,
            old.lock_id,
            new.lock_id,
            migration_id,
            resulting_revision,
            _dump(
                {
                    "campaign_id": campaign_id,
                    "activation_id": activation_id,
                    "status": "committed",
                    "old_lock_id": old.lock_id,
                    "new_lock_id": new.lock_id,
                    "resulting_revision": resulting_revision,
                    "payload_sha256": payload_sha256,
                    **({"authority": COMPATIBLE_UPDATE_AUTHORITY} if compatible_update else {}),
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO campaign_revisions(campaign_id, revision, action_id, activation_id) "
        "VALUES (?, ?, NULL, ?)",
        (campaign_id, resulting_revision, activation_id),
    )
    connection.execute(
        "UPDATE package_candidates SET status = 'activated' WHERE candidate_id = ?",
        (candidate_id,),
    )
    return result


def _lock_legacy_representation(package_lock: PackageLock) -> str:
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


def add_recovery_root(connection: sqlite3.Connection, root_id: str, blob_sha256: str) -> None:
    connection.execute("INSERT INTO package_recovery_roots VALUES (?, ?)", (root_id, blob_sha256))


def remove_recovery_root(connection: sqlite3.Connection, root_id: str) -> None:
    connection.execute("DELETE FROM package_recovery_roots WHERE root_id = ?", (root_id,))


def reclaim(connection: sqlite3.Connection) -> ReclamationResult:
    campaign_lock_roots = int(
        connection.execute("SELECT COUNT(*) FROM campaign_locks").fetchone()[0]
    )
    candidate_roots = int(
        connection.execute(
            "SELECT COUNT(*) FROM package_candidates WHERE status IN ('validated','activated')"
        ).fetchone()[0]
    )
    activation_roots = int(
        connection.execute("SELECT COUNT(*) FROM package_activations").fetchone()[0]
    )
    staging_roots = int(
        connection.execute(
            "SELECT COUNT(*) FROM package_staging WHERE status = 'staging'"
        ).fetchone()[0]
    )
    read_roots = int(connection.execute("SELECT COUNT(*) FROM package_read_leases").fetchone()[0])
    recovery_roots = int(
        connection.execute("SELECT COUNT(*) FROM package_recovery_roots").fetchone()[0]
    )
    rooted_packages = {
        (str(row[0]), str(row[1]), str(row[2]))
        for query in (
            "SELECT package_id, version, content_sha256 FROM campaign_lock_memberships",
            "SELECT package_id, version, content_sha256 FROM package_candidate_memberships "
            "WHERE candidate_id IN (SELECT candidate_id FROM package_candidates "
            "WHERE status IN ('validated','activated'))",
        )
        for row in connection.execute(query)
    }
    rooted_blobs = {
        str(row[0])
        for identity in rooted_packages
        for row in connection.execute(
            "SELECT blob_sha256 FROM package_manifest_files WHERE package_id = ? "
            "AND version = ? AND content_sha256 = ?",
            identity,
        )
    }
    rooted_blobs.update(
        str(row[0]) for row in connection.execute("SELECT blob_sha256 FROM package_read_leases")
    )
    rooted_blobs.update(
        str(row[0]) for row in connection.execute("SELECT blob_sha256 FROM package_recovery_roots")
    )
    all_blobs = {str(row[0]) for row in connection.execute("SELECT blob_sha256 FROM package_blobs")}
    deletable = all_blobs - rooted_blobs
    for blob in sorted(deletable):
        connection.execute("DELETE FROM package_blobs WHERE blob_sha256 = ?", (blob,))
    return ReclamationResult(
        len(deletable),
        campaign_lock_roots,
        candidate_roots,
        activation_roots,
        staging_roots,
        read_roots,
        recovery_roots,
    )


def verify_package_authority(connection: sqlite3.Connection) -> None:
    """Reconcile all F3 immutable bytes, closure, lineage, and safe record invariants."""

    invalid = "Authority stored package integrity does not match F3"
    try:
        for row in connection.execute("SELECT * FROM package_staging"):
            parsed = _parse(str(row["manifest_json"]))
            if not isinstance(parsed, Mapping) or frozenset(parsed) != frozenset(
                {"format", "packages"}
            ):
                raise ValueError(invalid)
            format_version = parsed["format"]
            packages = parsed["packages"]
            if (
                not isinstance(format_version, int)
                or isinstance(format_version, bool)
                or format_version not in (1, 2, 3, 4)
                or not isinstance(packages, Sequence)
                or isinstance(packages, (str, bytes, bytearray))
            ):
                raise ValueError(invalid)
            staging_manifests = tuple(
                manifest_from_record(item, format_version=format_version) for item in packages
            )
            file_ledger = tuple(
                (
                    manifest.identity,
                    manifest_file.path,
                    manifest_file.byte_count,
                    manifest_file.sha256,
                )
                for manifest in staging_manifests
                for manifest_file in manifest.files
            )
            expected_chunked_file_orders = {
                file_order
                for file_order, (_identity, _path, byte_count, _sha256) in enumerate(file_ledger)
                if byte_count > 0
            }
            report = _parse(str(row["report_json"]))
            if not isinstance(report, Mapping) or frozenset(report) != frozenset({"code", "label"}):
                raise ValueError(invalid)
            observed_file_orders: set[int] = set()
            next_chunk_orders: dict[int, int] = {}
            staged_byte_counts: dict[int, int] = {}
            staged_sha256s: dict[int, Any] = {}
            for chunk in connection.execute(
                "SELECT file_order, chunk_order, package_id, version, content_sha256, "
                "portable_path, chunk_sha256, data FROM package_staging_chunks "
                "WHERE install_id = ? ORDER BY file_order, chunk_order",
                (row["install_id"],),
            ):
                file_order = int(chunk["file_order"])
                if file_order < 0 or file_order >= len(file_ledger):
                    raise ValueError(invalid)
                identity, portable_path, byte_count, _file_sha256 = file_ledger[file_order]
                if (
                    byte_count == 0
                    or (
                        str(chunk["package_id"]),
                        str(chunk["version"]),
                        str(chunk["content_sha256"]),
                    )
                    != identity
                    or str(chunk["portable_path"]) != portable_path
                    or int(chunk["chunk_order"]) != next_chunk_orders.get(file_order, 0)
                ):
                    raise ValueError(invalid)
                data = bytes(chunk["data"])
                if not data or _sha(data) != str(chunk["chunk_sha256"]):
                    raise ValueError(invalid)
                digest = staged_sha256s.setdefault(file_order, hashlib.sha256())
                digest.update(data)
                observed_file_orders.add(file_order)
                next_chunk_orders[file_order] = next_chunk_orders.get(file_order, 0) + 1
                staged_byte_counts[file_order] = staged_byte_counts.get(file_order, 0) + len(data)
            unfinished = str(row["status"]) == "staging"
            if unfinished:
                # Staging commits each verified chunk separately. An interrupted
                # import owns only a prefix and must remain reopenable for retry.
                expected_prefix = set(
                    sorted(expected_chunked_file_orders)[: len(observed_file_orders)]
                )
                if observed_file_orders != expected_prefix or report["code"] != "staging":
                    raise ValueError(invalid)
            elif observed_file_orders != expected_chunked_file_orders:
                raise ValueError(invalid)
            for file_order in observed_file_orders:
                _identity, _path, byte_count, file_sha256 = file_ledger[file_order]
                if (
                    unfinished
                    and file_order == max(observed_file_orders)
                    and staged_byte_counts[file_order] < byte_count
                ):
                    continue
                digest = staged_sha256s[file_order]
                if (
                    staged_byte_counts[file_order] != byte_count
                    or digest.hexdigest() != file_sha256
                ):
                    raise ValueError(invalid)
        for row in connection.execute("SELECT blob_sha256 FROM package_blobs"):
            verify_blob(connection, str(row["blob_sha256"]))
        manifests: dict[tuple[str, str, str], PackageManifest] = {}
        for row in connection.execute("SELECT * FROM package_manifests WHERE status = 'published'"):
            manifest = _manifest_from_json(str(row["canonical_json"]))
            if manifest.identity != (
                str(row["package_id"]),
                str(row["version"]),
                str(row["content_sha256"]),
            ):
                raise ValueError(invalid)
            if (
                _parse(str(row["metadata_json"])) != manifest.metadata
                or _parse(str(row["provenance_json"])) != manifest.provenance
                or _parse(str(row["compatibility_json"])) != manifest.compatibility
            ):
                raise ValueError(invalid)
            file_rows = tuple(
                connection.execute(
                    "SELECT * FROM package_manifest_files WHERE package_id = ? AND version = ? "
                    "AND content_sha256 = ? ORDER BY file_order",
                    manifest.identity,
                )
            )
            if tuple(int(item["file_order"]) for item in file_rows) != tuple(range(len(file_rows))):
                raise ValueError(invalid)
            if tuple(
                (str(item["portable_path"]), int(item["byte_count"]), str(item["blob_sha256"]))
                for item in file_rows
            ) != tuple((item.path, item.byte_count, item.sha256) for item in manifest.files):
                raise ValueError(invalid)
            for item in manifest.files:
                verified_chunks = verify_blob(connection, item.sha256)
                if item.path == manifest.content_declarations_path:
                    declarations = parse_content_declarations_bytes(b"".join(verified_chunks))
                    validate_content_declaration_links(manifest, declarations)
                if item.path == CAMPAIGN_SETTING_KNOWLEDGE_PATH:
                    knowledge = parse_campaign_setting_knowledge_bytes(b"".join(verified_chunks))
                    validate_campaign_setting_knowledge_links(manifest, knowledge)
            manifests[manifest.identity] = manifest
        for candidate_row in connection.execute("SELECT candidate_id FROM package_candidates"):
            candidate = read_candidate(connection, str(candidate_row["candidate_id"]))
            if closure_sha256(candidate.package_lock) != candidate.closure_sha256:
                raise ValueError(invalid)
            if candidate.status in (CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED):
                verify_published_lock(connection, candidate.package_lock)
        for lock_row in connection.execute("SELECT lock_id FROM campaign_locks"):
            lock = read_lock_record(connection, str(lock_row["lock_id"]))
            if closure_sha256(lock.package_lock) != lock.closure_sha256:
                raise ValueError(invalid)
            if lock.availability is PackageLockAvailability.AVAILABLE:
                verify_published_lock(connection, lock.package_lock)
            if lock.predecessor_lock_id == lock.lock_id:
                raise ValueError(invalid)
        for campaign in connection.execute(
            "SELECT campaign_id, current_lock_id, revision, lifecycle, writer, lock_hash "
            "FROM campaigns"
        ):
            if campaign["current_lock_id"] is None:
                campaign_id = str(campaign["campaign_id"])
                if (
                    not sqlite_draft_history.has_only_draft_lifecycle_history(
                        connection, campaign_id, int(campaign["revision"])
                    )
                    or (str(campaign["lifecycle"]), str(campaign["writer"]))
                    not in {
                        ("active", "builder"),
                        ("archived", "none"),
                        ("tombstoned", "none"),
                    }
                    or str(campaign["lock_hash"]) != "unbound"
                    or connection.execute(
                        "SELECT 1 FROM campaign_locks WHERE campaign_id = ? LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    is not None
                    or connection.execute(
                        "SELECT 1 FROM turns WHERE campaign_id = ? LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    is not None
                ):
                    raise ValueError(invalid)
                continue
            current = read_lock_record(connection, str(campaign["current_lock_id"]))
            if current.campaign_id != str(campaign["campaign_id"]):
                raise ValueError(invalid)
        for activation in connection.execute("SELECT * FROM package_activations"):
            result = _activation_result_from_row(activation)
            old = read_lock_record(connection, result.old_lock_id)
            new = read_lock_record(connection, result.new_lock_id)
            candidate = read_candidate(connection, str(activation["candidate_id"]))
            if result.resulting_revision < 1:
                raise ValueError(invalid)
            request = ActivationCommand(
                campaign_id=result.campaign_id,
                candidate_id=str(activation["candidate_id"]),
                activation_id=result.activation_id,
                expected_revision=result.resulting_revision - 1,
                expected_lock_id=result.old_lock_id,
                migration_id=str(activation["migration_id"]),
            )
            revision = connection.execute(
                "SELECT action_id, activation_id FROM campaign_revisions "
                "WHERE campaign_id = ? AND revision = ?",
                (result.campaign_id, result.resulting_revision),
            ).fetchone()
            campaign = connection.execute(
                "SELECT revision FROM campaigns WHERE campaign_id = ?",
                (result.campaign_id,),
            ).fetchone()
            if (
                result.status is not ActivationStatus.COMMITTED
                or activation_payload_sha256(request) != result.payload_sha256
                or new.predecessor_lock_id != old.lock_id
                or old.campaign_id != result.campaign_id
                or new.campaign_id != result.campaign_id
                or new.created_revision != result.resulting_revision
                or candidate.package_lock != new.package_lock
                or candidate.closure_sha256 != new.closure_sha256
                or revision is None
                or revision["action_id"] is not None
                or str(revision["activation_id"]) != result.activation_id
                or campaign is None
                or int(campaign["revision"]) < result.resulting_revision
            ):
                raise ValueError(invalid)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError(invalid)
    except CommandRejected as error:
        raise SchemaIdentityError(invalid) from error
    except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
        raise SchemaIdentityError(invalid) from error
