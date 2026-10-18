"""Bounded local source ingestion for canonical Builder sessions.

Selected host paths are consumed only inside the owner process.  The durable
Builder workspace and all public projections use opaque source identities, so
original paths and basenames never enter provider context or renderer state.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import LimitReachedError, PyPdfError

from sylvia_gm.domain.errors import CommandRejected

MAX_SOURCE_FILES = 8
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_CHARS = 2_000_000
MAX_PDF_PAGES = 512
SOURCE_DIRECTORY = ".builder-sources"
PURGE_PENDING_DIRECTORY = ".builder-sources-purge-pending"

_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".json", ".yaml", ".yml", ".csv"})
_PDF_EXTENSION = ".pdf"


@dataclass(frozen=True, slots=True)
class IngestedBuilderSource:
    """One path-free source projection backed by an opaque workspace file."""

    source_id: str
    media_type: str
    content_sha256: str
    byte_count: int
    char_count: int
    page_count: int | None
    workspace_path: str

    def safe_projection(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "media_type": self.media_type,
            "content_sha256": self.content_sha256,
            "byte_count": self.byte_count,
            "char_count": self.char_count,
            "page_count": self.page_count,
            "workspace_path": self.workspace_path,
        }


@dataclass(frozen=True, slots=True)
class IngestedBuilderSourceBatch:
    """One atomically published, rollback-capable ingestion batch."""

    sources: tuple[IngestedBuilderSource, ...]
    batch_path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class BuilderSourcePurgeStage:
    """Recoverable filesystem half of a campaign purge."""

    workspace: Path
    campaign_id: str
    operation_id: str
    operation_path: Path
    sources_path: Path
    moved_this_call: bool
    created_this_call: bool


def _is_link_or_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError as error:
        raise CommandRejected("Selected Builder source is unavailable") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _read_regular_source(path: Path, remaining_bytes: int) -> bytes:
    if _is_link_or_reparse(path):
        raise CommandRejected("Selected Builder source cannot be a link or reparse point")
    try:
        with path.open("rb") as stream:
            details = os.fstat(stream.fileno())
            if not stat.S_ISREG(details.st_mode):
                raise CommandRejected("Selected Builder source is not a regular file")
            if details.st_size < 0 or details.st_size > remaining_bytes:
                raise CommandRejected("Selected Builder sources exceed the import byte limit")
            payload = stream.read(remaining_bytes + 1)
            if len(payload) > remaining_bytes or stream.read(1):
                raise CommandRejected("Selected Builder sources exceed the import byte limit")
            return payload
    except CommandRejected:
        raise
    except OSError as error:
        raise CommandRejected("Selected Builder source is unavailable") from error


def _extract_pdf(payload: bytes) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            raise CommandRejected("Encrypted PDF sources are not supported")
        page_count = len(reader.pages)
        if page_count == 0 or page_count > MAX_PDF_PAGES:
            raise CommandRejected("PDF source exceeds the supported page bounds")
        parts: list[str] = []
        extracted_chars = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            extracted_chars += len(text)
            if extracted_chars > MAX_EXTRACTED_CHARS:
                raise CommandRejected("Builder sources exceed the extracted-text limit")
            parts.append(text)
    except CommandRejected:
        raise
    except (LimitReachedError, RecursionError) as error:
        raise CommandRejected("PDF source exceeds the supported parsing limits") from error
    except (PyPdfError, OSError, ValueError, TypeError) as error:
        raise CommandRejected("Selected PDF source is malformed or unsupported") from error
    extracted = "\n\n".join(parts)
    if not extracted.strip():
        raise CommandRejected("PDF source has no extractable text layer")
    return extracted, page_count


def _extract_source(path: Path, payload: bytes) -> tuple[str, str, int | None]:
    extension = path.suffix.casefold()
    if extension in _TEXT_EXTENSIONS:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CommandRejected("Text Builder source is not valid UTF-8") from error
        if not text.strip():
            raise CommandRejected("Text Builder source is empty or unusable")
        return text, "text/plain; charset=utf-8", None
    if extension == _PDF_EXTENSION:
        text, page_count = _extract_pdf(payload)
        return text, "application/pdf", page_count
    raise CommandRejected("Selected Builder source type is not supported")


def _source_identity(media_type: str, payload: bytes) -> tuple[str, str]:
    content_sha256 = hashlib.sha256(payload).hexdigest()
    identity = hashlib.sha256(
        media_type.encode("utf-8") + b"\x00" + bytes.fromhex(content_sha256)
    ).hexdigest()[:24]
    return f"source-{identity}", content_sha256


def ingest_builder_sources(
    workspace: Path,
    source_paths: Sequence[Path],
    *,
    batch_id: str | None = None,
) -> IngestedBuilderSourceBatch:
    """Extract one all-or-nothing bounded batch into an opaque workspace subtree."""

    if not 1 <= len(source_paths) <= MAX_SOURCE_FILES:
        raise CommandRejected("Select between one and eight Builder source files")

    resolved_inputs: list[Path] = []
    seen_inputs: set[str] = set()
    for source in source_paths:
        selected = Path(source).expanduser()
        if _is_link_or_reparse(selected):
            raise CommandRejected("Selected Builder source cannot be a link or reparse point")
        resolved = selected.resolve(strict=False)
        portable_key = str(resolved).casefold()
        if portable_key in seen_inputs:
            raise CommandRejected("Builder source selection contains a duplicate path")
        seen_inputs.add(portable_key)
        resolved_inputs.append(resolved)

    extracted_sources: list[tuple[str, str, str, int, int, int | None, str]] = []
    consumed_bytes = 0
    extracted_chars = 0
    seen_source_ids: set[str] = set()
    for source in resolved_inputs:
        payload = _read_regular_source(source, MAX_SOURCE_BYTES - consumed_bytes)
        consumed_bytes += len(payload)
        text, media_type, page_count = _extract_source(source, payload)
        extracted_chars += len(text)
        if extracted_chars > MAX_EXTRACTED_CHARS:
            raise CommandRejected("Builder sources exceed the extracted-text limit")
        source_id, content_sha256 = _source_identity(media_type, payload)
        if source_id in seen_source_ids:
            raise CommandRejected("Builder source selection contains duplicate content")
        seen_source_ids.add(source_id)
        extracted_sources.append(
            (
                source_id,
                media_type,
                content_sha256,
                len(payload),
                len(text),
                page_count,
                text,
            )
        )

    if batch_id is None:
        batch_identity = hashlib.sha256(
            b"\x00".join(
                item[2].encode("ascii")
                for item in sorted(extracted_sources, key=lambda row: row[0])
            )
        ).hexdigest()[:24]
    else:
        batch_identity = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:24]
    batch_name = f"batch-{batch_identity}"
    prepared: list[tuple[IngestedBuilderSource, str]] = []
    for (
        source_id,
        media_type,
        content_sha256,
        byte_count,
        char_count,
        page_count,
        text,
    ) in extracted_sources:
        prepared.append(
            (
                IngestedBuilderSource(
                    source_id=source_id,
                    media_type=media_type,
                    content_sha256=content_sha256,
                    byte_count=byte_count,
                    char_count=char_count,
                    page_count=page_count,
                    workspace_path=f"{SOURCE_DIRECTORY}/{batch_name}/{source_id}.txt",
                ),
                text,
            )
        )

    workspace_path = Path(workspace)
    try:
        workspace_path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise CommandRejected("Builder source workspace is unavailable") from error
    if _is_link_or_reparse(workspace_path):
        raise CommandRejected("Builder workspace cannot be a link or reparse point")
    workspace_root = workspace_path.resolve()
    source_root = workspace_root / SOURCE_DIRECTORY
    staging_root: Path | None = None
    try:
        source_root.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse(source_root):
            raise CommandRejected("Builder source workspace cannot be a link or reparse point")
        destination_root = source_root / batch_name
        if destination_root.exists():
            if _is_link_or_reparse(destination_root) or not destination_root.is_dir():
                raise CommandRejected("Builder source batch identity is unsafe")
            expected = {
                f"{source.source_id}.txt": text.encode("utf-8") for source, text in prepared
            }
            actual_entries = tuple(destination_root.iterdir())
            if (
                {entry.name for entry in actual_entries} != set(expected)
                or any(
                    not entry.is_file() or _is_link_or_reparse(entry) for entry in actual_entries
                )
                or any(entry.read_bytes() != expected[entry.name] for entry in actual_entries)
            ):
                raise CommandRejected("Builder source ingestion identity was reused differently")
            return IngestedBuilderSourceBatch(
                sources=tuple(source for source, _text in prepared),
                batch_path=destination_root,
                created=False,
            )

        staging_root = Path(tempfile.mkdtemp(dir=source_root, prefix=f".{batch_name}-"))
        for prepared_source, text in prepared:
            destination = staging_root / f"{prepared_source.source_id}.txt"
            with destination.open("xb") as stream:
                stream.write(text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(staging_root, destination_root)
        staging_root = None
    except CommandRejected:
        raise
    except OSError as error:
        raise CommandRejected("Builder source workspace is unavailable") from error
    finally:
        if staging_root is not None:
            shutil.rmtree(staging_root, ignore_errors=True)

    return IngestedBuilderSourceBatch(
        sources=tuple(source for source, _text in prepared),
        batch_path=destination_root,
        created=True,
    )


def rollback_builder_source_batch(batch: IngestedBuilderSourceBatch) -> None:
    """Remove only a batch newly published by the failed owner command."""

    if not batch.created or not batch.batch_path.exists():
        return
    if _is_link_or_reparse(batch.batch_path):
        raise CommandRejected("Builder source batch could not be rolled back safely")
    try:
        shutil.rmtree(batch.batch_path)
        source_root = batch.batch_path.parent
        if source_root.exists() and not any(source_root.iterdir()):
            source_root.rmdir()
    except OSError as error:
        raise CommandRejected("Builder source batch could not be rolled back") from error


def _purge_operation_name(operation_id: str) -> str:
    return "purge-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]


def _purge_marker(campaign_id: str, operation_id: str) -> dict[str, object]:
    return {
        "format": "sylvia-gm-builder-source-purge",
        "version": 1,
        "campaign_id": campaign_id,
        "operation_id": operation_id,
    }


def _read_purge_marker(operation_path: Path) -> tuple[str, str]:
    marker_path = operation_path / "purge.json"
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CommandRejected("Builder source purge marker is invalid") from error
    if (
        not isinstance(payload, dict)
        or payload.get("format") != "sylvia-gm-builder-source-purge"
        or payload.get("version") != 1
        or not isinstance(payload.get("campaign_id"), str)
        or not isinstance(payload.get("operation_id"), str)
        or set(payload) != {"format", "version", "campaign_id", "operation_id"}
    ):
        raise CommandRejected("Builder source purge marker is invalid")
    return str(payload["campaign_id"]), str(payload["operation_id"])


def _remove_empty_purge_parents(operation_path: Path) -> None:
    pending_root = operation_path.parent
    if pending_root.exists() and not any(pending_root.iterdir()):
        pending_root.rmdir()


def _cleanup_abandoned_purge_journals(pending_root: Path) -> None:
    """Remove only unpublished temporary journal directories."""

    if not pending_root.exists():
        return
    for entry in tuple(pending_root.iterdir()):
        if not (entry.name.startswith(".purge-") and entry.name.endswith(".tmp")):
            continue
        if _is_link_or_reparse(entry) or not entry.is_dir():
            raise CommandRejected("Builder source purge temporary journal is unsafe")
        try:
            shutil.rmtree(entry)
        except OSError as error:
            raise CommandRejected("Builder source purge journal could not be recovered") from error


def stage_builder_sources_for_purge(
    workspace: Path,
    *,
    campaign_id: str,
    operation_id: str,
) -> BuilderSourcePurgeStage:
    """Quarantine sources under an operation-bound journal before SQLite commit."""

    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(workspace_path):
        raise CommandRejected("Builder workspace cannot be purged safely")
    workspace_root = workspace_path.resolve()
    source_root = workspace_root / SOURCE_DIRECTORY
    pending_root = workspace_root / PURGE_PENDING_DIRECTORY
    operation_path = pending_root / _purge_operation_name(operation_id)
    sources_path = operation_path / "sources"
    created_this_call = False
    moved_this_call = False
    staging_operation: Path | None = None
    try:
        pending_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(pending_root):
            raise CommandRejected("Builder source purge quarantine is unsafe")
        _cleanup_abandoned_purge_journals(pending_root)
        other_operations = tuple(
            entry for entry in pending_root.iterdir() if entry != operation_path
        )
        if other_operations:
            raise CommandRejected("Another Builder source purge requires reconciliation")
        if operation_path.exists():
            if _is_link_or_reparse(operation_path) or not operation_path.is_dir():
                raise CommandRejected("Builder source purge operation is unsafe")
            marker_campaign_id, marker_operation_id = _read_purge_marker(operation_path)
            if marker_campaign_id != campaign_id or marker_operation_id != operation_id:
                raise CommandRejected("Builder source purge identity was reused differently")
        else:
            staging_operation = Path(
                tempfile.mkdtemp(
                    dir=pending_root,
                    prefix=f".{_purge_operation_name(operation_id)}-",
                    suffix=".tmp",
                )
            )
            marker_bytes = json.dumps(
                _purge_marker(campaign_id, operation_id),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            with (staging_operation / "purge.json").open("xb") as marker_stream:
                marker_stream.write(marker_bytes)
                marker_stream.flush()
                os.fsync(marker_stream.fileno())
            os.replace(staging_operation, operation_path)
            staging_operation = None
            created_this_call = True

        if sources_path.exists() and source_root.exists():
            raise CommandRejected("Builder source purge has conflicting filesystem state")
        if source_root.exists():
            if _is_link_or_reparse(source_root):
                raise CommandRejected("Builder source workspace cannot be purged safely")
            os.replace(source_root, sources_path)
            moved_this_call = True
    except CommandRejected:
        if created_this_call and not moved_this_call:
            shutil.rmtree(operation_path, ignore_errors=True)
            _remove_empty_purge_parents(operation_path)
        raise
    except OSError as error:
        if created_this_call and not moved_this_call:
            shutil.rmtree(operation_path, ignore_errors=True)
            _remove_empty_purge_parents(operation_path)
        raise CommandRejected("Builder source workspace could not be quarantined") from error
    finally:
        if staging_operation is not None:
            shutil.rmtree(staging_operation, ignore_errors=True)
    return BuilderSourcePurgeStage(
        workspace=workspace_root,
        campaign_id=campaign_id,
        operation_id=operation_id,
        operation_path=operation_path,
        sources_path=sources_path,
        moved_this_call=moved_this_call,
        created_this_call=created_this_call,
    )


def restore_builder_sources_after_failed_purge(stage: BuilderSourcePurgeStage) -> None:
    """Restore a quarantine created by a purge whose SQLite mutation failed."""

    source_root = stage.workspace / SOURCE_DIRECTORY
    if stage.sources_path.exists() and source_root.exists():
        raise CommandRejected("Builder source purge could not be rolled back safely")
    try:
        if stage.sources_path.exists():
            if _is_link_or_reparse(stage.sources_path):
                raise CommandRejected("Builder source purge could not be rolled back safely")
            os.replace(stage.sources_path, source_root)
        if stage.operation_path.exists():
            shutil.rmtree(stage.operation_path)
        _remove_empty_purge_parents(stage.operation_path)
    except CommandRejected:
        raise
    except OSError as error:
        raise CommandRejected("Builder source purge could not be rolled back") from error


def finalize_builder_source_purge(stage: BuilderSourcePurgeStage) -> None:
    """Delete a quarantined source subtree after the SQLite purge commits."""

    if not stage.operation_path.exists():
        return
    if _is_link_or_reparse(stage.operation_path):
        raise CommandRejected("Builder source purge quarantine is unsafe")
    try:
        shutil.rmtree(stage.operation_path)
        _remove_empty_purge_parents(stage.operation_path)
    except OSError as error:
        raise CommandRejected("Builder source workspace could not be purged") from error


def finalize_existing_builder_source_purge(
    workspace: Path,
    *,
    campaign_id: str,
    operation_id: str,
) -> None:
    """Finalize only the quarantine created by this exact prior purge operation."""

    operation_path = Path(workspace) / PURGE_PENDING_DIRECTORY / _purge_operation_name(operation_id)
    if not operation_path.exists():
        return
    marker_campaign_id, marker_operation_id = _read_purge_marker(operation_path)
    if marker_campaign_id != campaign_id or marker_operation_id != operation_id:
        raise CommandRejected("Builder source purge identity was reused differently")
    finalize_builder_source_purge(
        BuilderSourcePurgeStage(
            workspace=Path(workspace),
            campaign_id=campaign_id,
            operation_id=operation_id,
            operation_path=operation_path,
            sources_path=operation_path / "sources",
            moved_this_call=False,
            created_this_call=False,
        )
    )


def reconcile_builder_source_purges(
    storage_root: Path,
    live_campaign_ids: Collection[str],
    purge_receipt_exists: Callable[[str, str], bool],
) -> None:
    """Resolve operation-bound purge journals against durable SQLite receipts."""

    campaigns_root = Path(storage_root) / "campaigns"
    if not campaigns_root.exists():
        return
    if _is_link_or_reparse(campaigns_root):
        raise CommandRejected("Campaign workspace root is unsafe")
    for workspace in campaigns_root.iterdir():
        if not workspace.is_dir() or _is_link_or_reparse(workspace):
            continue
        pending_root = workspace / PURGE_PENDING_DIRECTORY
        if not pending_root.exists():
            continue
        if _is_link_or_reparse(pending_root):
            raise CommandRejected("Builder source purge quarantine is unsafe")
        _cleanup_abandoned_purge_journals(pending_root)
        for operation_path in tuple(pending_root.iterdir()):
            if not operation_path.is_dir() or _is_link_or_reparse(operation_path):
                raise CommandRejected("Builder source purge operation is unsafe")
            campaign_id, operation_id = _read_purge_marker(operation_path)
            if campaign_id != workspace.name:
                raise CommandRejected("Builder source purge marker names the wrong campaign")
            stage = BuilderSourcePurgeStage(
                workspace=workspace,
                campaign_id=campaign_id,
                operation_id=operation_id,
                operation_path=operation_path,
                sources_path=operation_path / "sources",
                moved_this_call=False,
                created_this_call=False,
            )
            if purge_receipt_exists(operation_id, campaign_id):
                finalize_builder_source_purge(stage)
            elif campaign_id in live_campaign_ids:
                restore_builder_sources_after_failed_purge(stage)
            else:
                raise CommandRejected("Builder source purge outcome cannot be reconciled")
