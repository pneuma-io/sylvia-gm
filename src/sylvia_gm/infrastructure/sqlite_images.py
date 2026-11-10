"""Image request receipts and selected art in the existing authority database.

Image bytes use the existing immutable, hash-verified content blobs. Request admission precedes
remote execution. Reopening never resubmits an admitted request whose remote outcome is unknown.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from sylvia_gm.domain.package_library import canonical_json_bytes

MIGRATION_13_NAME = "image_generation_art"
MIGRATION_13_SQL = """
CREATE TABLE image_requests (
    operation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    campaign_id TEXT,
    target_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (
        purpose IN ('portrait', 'full_body', 'reference_sheet', 'map_underlay')),
    request_sha256 TEXT NOT NULL,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('proposed', 'pending', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    error TEXT,
    result_sha256 TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (result_sha256) REFERENCES package_blobs(blob_sha256)
);
CREATE TABLE image_selections (
    scope_id TEXT NOT NULL,
    campaign_id TEXT,
    target_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    frame_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (scope_id, target_id, purpose),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (operation_id) REFERENCES image_requests(operation_id),
    FOREIGN KEY (sha256) REFERENCES package_blobs(blob_sha256)
);
""".strip()
MIGRATION_13_SHA256 = hashlib.sha256(MIGRATION_13_SQL.encode()).hexdigest()
TABLES = frozenset({"image_requests", "image_selections"})


def apply_migration(connection: sqlite3.Connection) -> None:
    """Add art records atomically in the authority store's migration transaction."""
    for statement in MIGRATION_13_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


def request(connection: sqlite3.Connection, operation_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM image_requests WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def admit(connection: sqlite3.Connection, value: Mapping[str, Any]) -> dict[str, Any]:
    """Idempotently admit one exact request without any provider execution."""
    previous = request(connection, value["operation_id"])
    if previous is not None:
        if (
            previous["request_sha256"] != value["request_sha256"]
            or previous["session_id"] != value["session_id"]
        ):
            raise ValueError("Image operation identity conflicts with its saved request")
        return previous
    connection.execute(
        "INSERT INTO image_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
        tuple(
            value[key]
            for key in (
                "operation_id",
                "session_id",
                "campaign_id",
                "target_id",
                "purpose",
                "request_sha256",
                "request_json",
                "status",
            )
        ),
    )
    saved = request(connection, value["operation_id"])
    assert saved is not None
    return saved


def complete(
    connection: sqlite3.Connection,
    operation_id: str,
    *,
    status: str,
    image: bytes | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Commit validated bytes and the result receipt together; cancellation wins late replies."""
    from sylvia_gm.infrastructure import sqlite_packages

    current = request(connection, operation_id)
    if current is None:
        raise ValueError("Image operation is unavailable")
    if current["status"] != "pending":
        return current
    digest = None
    if image is not None and status == "succeeded":
        digest = hashlib.sha256(image).hexdigest()
        chunks = tuple(image[offset : offset + 65536] for offset in range(0, len(image), 65536))
        sqlite_packages._insert_or_compare_blob(connection, digest, chunks)
    connection.execute(
        "UPDATE image_requests SET status = ?, error = ?, result_sha256 = ? WHERE operation_id = ?",
        (status, error, digest, operation_id),
    )
    saved = request(connection, operation_id)
    assert saved is not None
    return saved


def selections(connection: sqlite3.Connection, scope_id: str) -> list[dict[str, Any]]:
    result = []
    for row in connection.execute(
        "SELECT * FROM image_selections WHERE scope_id = ? ORDER BY target_id, purpose",
        (scope_id,),
    ):
        value = dict(row)
        value["frame"] = json.loads(value.pop("frame_json"))
        value["metadata"] = json.loads(value.pop("metadata_json"))
        result.append(value)
    return result


def select(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    operation_id: str,
    expected_sha256: str | None,
    frame: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare and swap one selection, preserving the previous asset on stale adoption."""
    current = request(connection, operation_id)
    if current is None or current["status"] != "succeeded" or not current["result_sha256"]:
        raise ValueError("Image result is unavailable")
    previous = connection.execute(
        "SELECT sha256, operation_id FROM image_selections WHERE scope_id = ? "
        "AND target_id = ? AND purpose = ?",
        (scope_id, current["target_id"], current["purpose"]),
    ).fetchone()
    if previous is not None and previous["operation_id"] == operation_id:
        return next(
            row for row in selections(connection, scope_id) if row["operation_id"] == operation_id
        )
    if (previous["sha256"] if previous is not None else None) != expected_sha256:
        raise ValueError("Image selection changed; review the current image before adopting")
    connection.execute(
        "INSERT INTO image_selections VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(scope_id, target_id, purpose) DO UPDATE SET "
        "operation_id=excluded.operation_id, "
        "sha256=excluded.sha256, frame_json=excluded.frame_json, "
        "metadata_json=excluded.metadata_json",
        (
            scope_id,
            current["campaign_id"],
            current["target_id"],
            current["purpose"],
            operation_id,
            current["result_sha256"],
            canonical_json_bytes(dict(frame)).decode(),
            canonical_json_bytes(dict(metadata)).decode(),
        ),
    )
    return next(
        row
        for row in selections(connection, scope_id)
        if row["target_id"] == current["target_id"] and row["purpose"] == current["purpose"]
    )


class ImageStore:
    """Use only the existing authority store's connection and transaction boundary."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        ensure_schema: Callable[[sqlite3.Connection], None],
    ) -> None:
        self._connect = connect
        self._ensure_schema = ensure_schema

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_schema(connection)
            yield connection
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            return request(connection, operation_id)

    def list_requests(self, *, campaign_id: str | None, session_id: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            where, key = ("campaign_id", campaign_id) if campaign_id else ("session_id", session_id)
            return [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM image_requests WHERE {where} = ? ORDER BY rowid DESC LIMIT 128",
                    (key,),
                )
            ]

    def list_selections(self, scope_id: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return selections(connection, scope_id)

    def admit(self, value: Mapping[str, Any]) -> dict[str, Any]:
        with self._transaction() as connection:
            return admit(connection, value)

    def approve(self, operation_id: str) -> bool:
        """Claim the sole dispatch before leaving the transaction for remote work."""
        with self._transaction() as connection:
            return (
                connection.execute(
                    "UPDATE image_requests SET status='pending' WHERE operation_id=? AND "
                    "status='proposed'",
                    (operation_id,),
                ).rowcount
                == 1
            )

    def finish(
        self,
        operation_id: str,
        *,
        status: str,
        image: bytes | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            return complete(connection, operation_id, status=status, image=image, error=error)

    def bind_inputs(self, operation_id: str, value: Mapping[str, Any]) -> bool:
        """Bind the exact selected references before dispatch; a prior Cancel prevents execution."""
        with self._transaction() as connection:
            return (
                connection.execute(
                    "UPDATE image_requests SET request_json=? WHERE operation_id=? AND "
                    "status='pending'",
                    (canonical_json_bytes(dict(value)).decode(), operation_id),
                ).rowcount
                == 1
            )

    def attach_character_visuals(
        self,
        *,
        scope_id: str,
        campaign_id: str | None,
        session_id: str,
        target_id: str,
        visuals: Mapping[str, Any],
        assets: Mapping[str, bytes] | None = None,
        name: str | None = None,
    ) -> None:
        """Bind retained receipts or verified package assets without calling a provider."""
        from uuid import NAMESPACE_URL, uuid5

        from sylvia_gm.application.image_hooks import ImageData

        with self._transaction() as connection:
            existing = {
                row["purpose"]
                for row in selections(connection, scope_id)
                if row["target_id"] == target_id
            }
            for purpose in ("full_body", "reference_sheet", "portrait"):
                if purpose in existing or purpose not in visuals:
                    continue
                reference = visuals[purpose]
                metadata = {
                    "appearance": visuals["appearance"],
                    "source_operation_id": reference.get("operation_id"),
                    "source_path": reference.get("path"),
                    "target_name": name,
                    "references": {
                        dependency: visuals[dependency]["sha256"]
                        for dependency in (
                            ()
                            if purpose == "full_body"
                            else ("full_body",)
                            if purpose == "reference_sheet"
                            else ("full_body", "reference_sheet")
                        )
                    },
                }
                original = request(connection, reference.get("operation_id", ""))
                valid_original = (
                    original is not None
                    and original["status"] == "succeeded"
                    and original["result_sha256"] == reference["sha256"]
                    and original["purpose"] == purpose
                )
                image = (assets or {}).get(reference.get("path", ""))
                if not valid_original and image is None:
                    continue
                if image is not None:
                    _ = ImageData(image).dimensions
                    if hashlib.sha256(image).hexdigest() != reference["sha256"]:
                        raise ValueError("Character artwork does not match its saved identity")
                if original is None or not valid_original:
                    original = {
                        "purpose": purpose,
                        "request_json": canonical_json_bytes(
                            {
                                "source": "package_asset",
                                "metadata": metadata,
                            }
                        ).decode(),
                        "status": "pending",
                    }
                identity = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"sylvia-character-art:{scope_id}:{target_id}:{purpose}:{reference['sha256']}",
                    )
                )
                copied = {
                    **original,
                    "operation_id": identity,
                    "campaign_id": campaign_id,
                    "session_id": session_id,
                    "target_id": target_id,
                    "request_sha256": hashlib.sha256(identity.encode()).hexdigest(),
                }
                admit(connection, copied)
                if image is not None and not valid_original:
                    complete(connection, identity, status="succeeded", image=image)
                else:
                    connection.execute(
                        "UPDATE image_requests SET result_sha256=? WHERE operation_id=?",
                        (reference["sha256"], identity),
                    )
                select(
                    connection,
                    scope_id=scope_id,
                    operation_id=identity,
                    expected_sha256=None,
                    frame=visuals.get("frame", {"x": 0.5, "y": 0.35, "zoom": 1}),
                    metadata=metadata,
                )

    def import_portrait(
        self,
        *,
        scope_id: str,
        campaign_id: str | None,
        session_id: str,
        target_id: str,
        image: bytes,
        appearance: str,
        name: str,
    ) -> None:
        """Adopt an imported card's image once, preserving any newer selected portrait."""
        from uuid import NAMESPACE_URL, uuid5

        from sylvia_gm.application.image_hooks import ImageData
        from sylvia_gm.domain.image_art import PortraitFrame

        asset = ImageData(image)
        _ = asset.dimensions
        identity = str(
            uuid5(NAMESPACE_URL, f"sylvia-card-art:{scope_id}:{target_id}:{asset.sha256}")
        )
        with self._transaction() as connection:
            if any(
                row["target_id"] == target_id and row["purpose"] == "portrait"
                for row in selections(connection, scope_id)
            ):
                return
            metadata = {"appearance": appearance, "source": "character_card", "target_name": name}
            admit(
                connection,
                {
                    "operation_id": identity,
                    "session_id": session_id,
                    "campaign_id": campaign_id,
                    "target_id": target_id,
                    "purpose": "portrait",
                    "request_sha256": hashlib.sha256(identity.encode()).hexdigest(),
                    "request_json": canonical_json_bytes({"metadata": metadata}).decode(),
                    "status": "pending",
                },
            )
            complete(connection, identity, status="succeeded", image=image)
            select(
                connection,
                scope_id=scope_id,
                operation_id=identity,
                expected_sha256=None,
                frame=PortraitFrame().as_payload(),
                metadata=metadata,
            )

    def cancel(self, operation_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE image_requests SET status='cancelled' WHERE operation_id=? AND "
                "status IN ('pending','proposed')",
                (operation_id,),
            )
            return request(connection, operation_id)

    def interrupt_pending(self) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE image_requests SET status='interrupted' WHERE status='pending'"
            )

    def adopt(self, **values: Any) -> dict[str, Any]:
        with self._transaction() as connection:
            return select(connection, **values)

    def frame(self, scope_id: str, target_id: str, digest: str, frame: Mapping[str, Any]) -> None:
        with self._transaction() as connection:
            changed = connection.execute(
                "UPDATE image_selections SET frame_json=? WHERE scope_id=? AND target_id=? "
                "AND purpose='portrait' AND sha256=?",
                (canonical_json_bytes(dict(frame)).decode(), scope_id, target_id, digest),
            ).rowcount
            if changed != 1:
                raise ValueError("The selected portrait changed before framing was saved")

    def image(self, digest: str) -> bytes:
        from sylvia_gm.infrastructure import sqlite_packages

        with self._transaction() as connection:
            data = b"".join(sqlite_packages.verify_blob(connection, digest))
            return data
