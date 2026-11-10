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
    value = dict(value)
    payload = json.loads(value["request_json"])
    flow = payload.get("character_flow")
    if flow is not None:
        publications = list(payload.get("publications", flow.get("publications", ())))
        for row in connection.execute(
            "SELECT request_json FROM image_requests WHERE session_id=? AND "
            "json_extract(request_json, '$.character_flow.id')=?",
            (value["session_id"], flow["id"]),
        ):
            for publication in json.loads(row["request_json"]).get("publications", ()):
                if publication not in publications:
                    publications.append(publication)
        payload["publications"] = publications
        flow["publications"] = publications
        value["request_json"] = canonical_json_bytes(payload).decode()
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
    value = json.loads(current["request_json"])
    value.update(adopted=True, adopted_frame=dict(frame))
    connection.execute(
        "UPDATE image_requests SET request_json=? WHERE operation_id=?",
        (canonical_json_bytes(value).decode(), operation_id),
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
            current = request(connection, operation_id)
            if current is None or current["status"] != "pending":
                return False
            previous = json.loads(current["request_json"])
            value = dict(value)
            if "publications" in previous:
                value["publications"] = previous["publications"]
                if "character_flow" in value:
                    value["character_flow"]["publications"] = previous["publications"]
            return (
                connection.execute(
                    "UPDATE image_requests SET request_json=? WHERE operation_id=? AND "
                    "status='pending'",
                    (canonical_json_bytes(dict(value)).decode(), operation_id),
                ).rowcount
                == 1
            )

    def bind_publication(
        self,
        operation_ids: list[str],
        artifact_id: str,
        content_sha256: str,
    ) -> None:
        """Retain an exact published source's relationship to its already-requested artwork."""
        publication = {"artifact_id": artifact_id, "content_sha256": content_sha256}
        with self._transaction() as connection:
            identities = set(operation_ids)
            for identity in operation_ids:
                row = request(connection, identity)
                flow = json.loads(row["request_json"]).get("character_flow") if row else None
                if row is not None and flow is not None:
                    identities.update(
                        item["operation_id"]
                        for item in connection.execute(
                            "SELECT operation_id FROM image_requests WHERE session_id=? AND "
                            "json_extract(request_json, '$.character_flow.id')=?",
                            (row["session_id"], flow["id"]),
                        )
                    )
            for identity in identities:
                row = request(connection, identity)
                if row is None:
                    continue
                value = json.loads(row["request_json"])
                publications = value.setdefault("publications", [])
                if publication not in publications:
                    publications.append(publication)
                if "character_flow" in value:
                    value["character_flow"]["publications"] = publications
                connection.execute(
                    "UPDATE image_requests SET request_json=? WHERE operation_id=?",
                    (canonical_json_bytes(value).decode(), identity),
                )

    def publication_operations(self, artifact_id: str, content_sha256: str) -> list[str]:
        """Find only artwork explicitly associated with this exact local character source."""
        with self._transaction() as connection:
            return [
                row["operation_id"]
                for row in connection.execute(
                    "SELECT operation_id FROM image_requests WHERE EXISTS "
                    "(SELECT 1 FROM json_each(request_json, '$.publications') WHERE "
                    "json_extract(value, '$.artifact_id')=? AND "
                    "json_extract(value, '$.content_sha256')=?)",
                    (artifact_id, content_sha256),
                )
            ]

    def published_visuals(self, artifact_id: str, content_sha256: str) -> dict[str, Any] | None:
        """Project adopted art for one exact owner-published source, including later stages."""
        from sylvia_gm.domain.image_art import character_visuals_from_selections

        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT image_requests.* FROM image_requests WHERE status='succeeded' AND EXISTS "
                "(SELECT 1 FROM json_each(request_json, '$.publications') WHERE "
                "json_extract(value, '$.artifact_id')=? AND "
                "json_extract(value, '$.content_sha256')=?) ORDER BY image_requests.rowid",
                (artifact_id, content_sha256),
            ).fetchall()
            selected = []
            for row in rows:
                value = json.loads(row["request_json"])
                current = connection.execute(
                    "SELECT * FROM image_selections WHERE operation_id=?", (row["operation_id"],)
                ).fetchone()
                if current is None and not value.get("adopted"):
                    continue
                selected.append(
                    {
                        "target_id": artifact_id,
                        "purpose": row["purpose"],
                        "operation_id": row["operation_id"],
                        "sha256": row["result_sha256"],
                        "frame": json.loads(current["frame_json"])
                        if current is not None
                        else value.get("adopted_frame", {"x": 0.5, "y": 0.35, "zoom": 1}),
                        "metadata": json.loads(current["metadata_json"])
                        if current is not None
                        else value["metadata"],
                    }
                )
            return character_visuals_from_selections(selected, artifact_id)

    def publication_progress(self, artifact_id: str, content_sha256: str) -> dict[str, Any] | None:
        """Expose the linked generation's status without making it actionable in another scope."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM image_requests WHERE EXISTS "
                "(SELECT 1 FROM json_each(request_json, '$.publications') WHERE "
                "json_extract(value, '$.artifact_id')=? AND "
                "json_extract(value, '$.content_sha256')=?) ORDER BY rowid DESC LIMIT 1",
                (artifact_id, content_sha256),
            ).fetchone()
            if row is None:
                return None
            value = json.loads(row["request_json"])
            return {
                "status": row["status"],
                "adopted": value.get("adopted", False),
                "has_flow": "character_flow" in value,
                "source_session_id": row["session_id"],
                "source_campaign_id": row["campaign_id"],
            }

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
                row["purpose"]: row
                for row in selections(connection, scope_id)
                if row["target_id"] == target_id
            }
            for purpose in ("full_body", "reference_sheet", "portrait"):
                previous = existing.get(purpose)
                if purpose not in visuals:
                    continue
                if previous is not None and not (
                    previous["metadata"].get("needs_refresh")
                    and (
                        previous["metadata"].get("source_operation_id")
                        or previous["metadata"].get("source_path")
                    )
                    and purpose not in visuals.get("missing", ())
                ):
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
                        if dependency in visuals
                    },
                    "needs_refresh": purpose in visuals.get("missing", ()),
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
                    "request_json": canonical_json_bytes(
                        {"source": "campaign_asset"
                         if session_id == campaign_id else "character_file",
                         "metadata": metadata}
                    ).decode(),
                }
                admit(connection, copied)
                if image is not None and not valid_original:
                    complete(connection, identity, status="succeeded", image=image)
                else:
                    connection.execute(
                        "UPDATE image_requests SET result_sha256=? WHERE operation_id=?",
                        (reference["sha256"], identity),
                    )
                if previous is not None and previous["operation_id"] == identity:
                    connection.execute(
                        "UPDATE image_selections SET metadata_json=? WHERE scope_id=? AND "
                        "target_id=? AND purpose=?",
                        (canonical_json_bytes(metadata).decode(), scope_id, target_id, purpose),
                    )
                select(
                    connection,
                    scope_id=scope_id,
                    operation_id=identity,
                    expected_sha256=previous["sha256"] if previous is not None else None,
                    frame=previous["frame"]
                    if previous is not None
                    else visuals.get("frame", {"x": 0.5, "y": 0.35, "zoom": 1}),
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
            row = connection.execute(
                "SELECT operation_id FROM image_selections WHERE scope_id=? "
                "AND target_id=? AND purpose='portrait'",
                (scope_id, target_id),
            ).fetchone()
            saved = request(connection, row["operation_id"])
            assert saved is not None
            value = json.loads(saved["request_json"])
            value["adopted_frame"] = dict(frame)
            connection.execute(
                "UPDATE image_requests SET request_json=? WHERE operation_id=?",
                (canonical_json_bytes(value).decode(), row["operation_id"]),
            )

    def image(self, digest: str) -> bytes:
        from sylvia_gm.infrastructure import sqlite_packages

        with self._transaction() as connection:
            data = b"".join(sqlite_packages.verify_blob(connection, digest))
            return data

    def retained_character_image(
        self, *, campaign_id: str | None, session_id: str,
        target_id: str, purpose: str, digest: str,
    ) -> bytes | None:
        """Reuse a character's completed local bytes even when its source package is absent."""
        from sylvia_gm.infrastructure import sqlite_packages

        where, key = ("campaign_id", campaign_id) if campaign_id else ("session_id", session_id)
        with self._transaction() as connection:
            retained = connection.execute(
                f"SELECT 1 FROM image_requests WHERE {where}=? AND target_id=? AND purpose=? "
                "AND status='succeeded' AND result_sha256=? LIMIT 1",
                (key, target_id, purpose, digest),
            ).fetchone()
            if retained is None:
                return None
            return b"".join(sqlite_packages.verify_blob(connection, digest))
