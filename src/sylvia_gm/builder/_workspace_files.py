"""Reject stale Builder file mutations across chats in the application owner process."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from threading import RLock


class StaleWorkspaceFileError(ValueError):
    """The file no longer matches the version the Builder used for its edit."""


class LiteralMatchError(ValueError):
    """An exact edit target did not identify one unique piece of text."""

    def __init__(self, matches: int) -> None:
        self.matches = matches
        if matches == 0:
            message = "old_text was not found. Read the file again and use an exact literal."
        else:
            message = (
                "old_text occurs more than once. Include more surrounding text so it uniquely "
                "identifies the intended correction."
            )
        super().__init__(message)


# All Builder chats execute in one owner process. Serialize compare-and-replace there.
_MUTATION_LOCK = RLock()
MISSING_VERSION = "missing"


def content_version(content: bytes) -> str:
    """Identify the exact bytes read or written, including their original line endings."""
    return hashlib.sha256(content).hexdigest()


def _check_version(path: Path, expected_version: str | None) -> None:
    """Require an observed version for existing files and detect competing creations."""
    try:
        current = content_version(path.read_bytes())
    except FileNotFoundError:
        current = MISSING_VERSION
    if current != (expected_version or MISSING_VERSION):
        raise StaleWorkspaceFileError(
            "File changed or has not been read. Call read_file again, reconcile the latest "
            "content, then retry with its version as expected_version. Ask the user if the "
            "requested changes conflict; do not blindly repeat the old replacement."
        )


def write_text(path: Path, content: str, expected_version: str | None) -> str:
    """Atomically save only if the current file still matches the Builder's observation."""
    data = content.encode("utf-8")
    with _MUTATION_LOCK:
        _check_version(path, expected_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".sylvia-builder-write-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(data)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return content_version(data)


def edit_text(
    path: Path,
    old_text: str,
    new_text: str,
    expected_version: str | None,
) -> tuple[str, int]:
    """Atomically replace one unique UTF-8 literal while preserving all other bytes."""

    old_data = old_text.encode("utf-8")
    new_data = new_text.encode("utf-8")
    if not old_data:
        raise ValueError("old_text must be non-empty")
    with _MUTATION_LOCK:
        _check_version(path, expected_version)
        data = path.read_bytes()
        start = data.find(old_data)
        if start < 0:
            raise LiteralMatchError(0)
        if data.find(old_data, start + 1) >= 0:
            raise LiteralMatchError(2)
        edited = data.replace(old_data, new_data, 1)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".sylvia-builder-edit-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(edited)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return content_version(edited), len(new_data)


def delete_file(path: Path, expected_version: str | None) -> None:
    """Delete only the file version the Builder read, without erasing another chat's edit."""
    with _MUTATION_LOCK:
        _check_version(path, expected_version)
        path.unlink()
