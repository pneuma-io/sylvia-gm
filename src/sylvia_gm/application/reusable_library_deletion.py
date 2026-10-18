"""Safe, retryable removal of one exact reusable-library directory."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def is_link_or_reparse(path: Path) -> bool:
    """Recognize symbolic links and Windows junctions before traversing an entry."""

    attributes = path.lstat()
    return path.is_symlink() or bool(
        getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def validated_library_entry(
    storage_root: Path, *, library_name: str, artifact_id: str
) -> Path | None:
    """Resolve one entry without following a linked library root or entry."""

    resolved_storage = storage_root.resolve()
    library_root = storage_root / library_name
    target = library_root / artifact_id
    if not os.path.lexists(target):
        return None
    for path in (library_root, target):
        if is_link_or_reparse(path) or not path.is_dir():
            raise ValueError("Reusable library deletion cannot follow linked directories")
        if not path.resolve().is_relative_to(resolved_storage):
            raise ValueError("Reusable library deletion leaves application storage")
    return target


def validate_owned_files(target: Path, owned_names: frozenset[str]) -> None:
    """Reject unexpected files, directories, links, and Windows reparse points."""

    for item in target.iterdir():
        if item.name not in owned_names or is_link_or_reparse(item) or not item.is_file():
            raise ValueError("Reusable library entry contains an unsafe path")


def delete_exact_library_entry(
    storage_root: Path,
    *,
    library_name: str,
    artifact_id: str,
    owned_names: frozenset[str],
) -> bool:
    """Remove owned files while retaining record.json as the retry fence until last."""

    target = validated_library_entry(
        storage_root, library_name=library_name, artifact_id=artifact_id
    )
    if target is None:
        return False
    validate_owned_files(target, owned_names)
    original_files = {
        name: (target / name).read_bytes()
        for name in owned_names
        if os.path.lexists(target / name)
    }
    try:
        for name in sorted(owned_names - {"record.json"}):
            path = target / name
            if os.path.lexists(path):
                path.unlink()
        (target / "record.json").unlink()
        target.rmdir()
    except OSError as error:
        restore_errors = []
        for name, value in original_files.items():
            path = target / name
            if not os.path.lexists(path):
                try:
                    path.write_bytes(value)
                except OSError as restore_error:
                    restore_errors.append(restore_error)
        if restore_errors:
            raise OSError(
                "Some saved content could not be removed or restored; the entry is unavailable"
            ) from error
        raise OSError(
            "Some saved content could not be removed; the original entry was restored for retry"
        ) from error
    return True
