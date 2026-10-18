"""Retain the legacy shared Builder workspace before detaching its saved chat."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PureWindowsPath

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes

_RECEIPT = ".sylvia-legacy-workspace.json"


def _is_link_or_reparse(path: Path) -> bool:
    attributes = path.lstat()
    return path.is_symlink() or bool(
        getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _builder_workspace_path(storage_root: Path, session_id: str) -> Path:
    if (
        not session_id or session_id.endswith((".", " "))
        or PureWindowsPath(session_id).is_reserved()
        or any(mark in session_id for mark in ("/", "\\", ":"))
    ):
        raise CommandRejected("Builder workspace identity is not a directory name")
    root = storage_root.resolve()
    target = root / "builder-chats" / session_id
    if not target.resolve().is_relative_to(root):
        raise CommandRejected("Builder workspace path leaves application storage")
    for ancestor in (target, *target.parents):
        if ancestor == root:
            break
        if os.path.lexists(ancestor) and _is_link_or_reparse(ancestor):
            raise CommandRejected("Builder workspace deletion cannot follow linked directories")
    return target


def remove_builder_workspace(storage_root: Path, *, session_id: str) -> None:
    """Permanently remove one validated standalone Builder workspace."""

    target = _builder_workspace_path(storage_root, session_id)
    if not os.path.lexists(target):
        return
    for directory, names, files in os.walk(target, followlinks=False):
        for name in (*names, *files):
            item = Path(directory) / name
            if _is_link_or_reparse(item):
                raise CommandRejected("Builder workspace deletion cannot follow linked files")
    try:
        shutil.rmtree(target)
    except OSError as error:
        raise CommandRejected(
            "Some files could not be removed. The chat is still saved; retry deletion.",
            code="builder_workspace_cleanup_incomplete",
        ) from error


def retain_legacy_workspace(storage_root: Path, *, campaign_id: str, session_id: str) -> Path:
    """Publish one complete independent copy; a retry never replaces already retained files."""
    if any(
        value in {".", ".."} or "/" in value or "\\" in value or ":" in value
        for value in (campaign_id, session_id)
    ):
        raise CommandRejected("Builder workspace migration identity is not a directory name")
    root = storage_root.resolve()
    source = root / "campaigns" / campaign_id
    target = root / "builder-chats" / session_id
    for path in (source, target):
        if not path.resolve().is_relative_to(root):
            raise CommandRejected("Builder workspace migration path leaves application storage")
        for ancestor in (path, *path.parents):
            if ancestor == root:
                break
            if ancestor.exists() and (
                ancestor.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                if hasattr(ancestor.lstat(), "st_file_attributes")
                else ancestor.is_symlink()
            ):
                raise CommandRejected(
                    "Builder workspace migration cannot follow linked directories"
                )
    receipt = {"format": 1, "campaign_id": campaign_id, "session_id": session_id}
    if target.exists():
        try:
            marker = target / _RECEIPT
            attributes = marker.lstat()
            if (
                marker.is_symlink()
                or (
                    getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                )
                or attributes.st_size > 4096
            ):
                raise CommandRejected("Builder migration receipt is invalid")
            if json.loads(marker.read_bytes()) == receipt:
                return target
        except (OSError, ValueError):
            pass
        raise CommandRejected("Builder migration found an existing independent workspace")
    if source.exists():
        for directory, names, files in os.walk(source, followlinks=False):
            for name in (*names, *files):
                item = Path(directory) / name
                attributes = item.lstat()
                if item.is_symlink() or (
                    getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise CommandRejected("Builder workspace migration cannot follow linked files")
        if (source / _RECEIPT).exists():
            raise CommandRejected("Builder migration receipt conflicts with an existing file")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".legacy-builder-", dir=target.parent) as temporary:
        staged = Path(temporary) / "workspace"
        if source.exists():
            shutil.copytree(source, staged)
        else:
            staged.mkdir()
        (staged / _RECEIPT).write_bytes(canonical_json_bytes(receipt))
        staged.rename(target)
    return target
