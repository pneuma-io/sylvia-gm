"""Inventory-checked portable staging; extracting an update never writes to the live copy."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import zipfile
from pathlib import Path

from .manifest import Manifest, UpdateError, bounded_json, payload_path
from .storage import directory_identity, ensure_directory


def verify_artifact(path: Path, manifest: Manifest) -> None:
    """Recheck final bytes at each trust boundary instead of trusting a journal label."""
    directory_identity(path.parent)
    try:
        value = path.lstat()
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_nlink != 1
            or getattr(value, "st_file_attributes", 0) & 0x400
            or value.st_size != manifest.artifact_size
        ):
            raise ValueError("Invalid update artifact")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (value.st_dev, value.st_ino):
                raise ValueError("Changed update artifact")
            count = 0
            while chunk := stream.read(256 * 1024):
                count += len(chunk)
                if count > manifest.artifact_size:
                    raise ValueError("Changed update artifact")
                digest.update(chunk)
        after = path.lstat()
        if (
            count != manifest.artifact_size
            or digest.hexdigest() != manifest.artifact_sha256
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        ):
            raise ValueError("Changed update artifact")
    except (OSError, ValueError) as error:
        raise UpdateError(
            "artifact_mismatch", "The stored update no longer matches the signed release."
        ) from error


def extract_portable(artifact: Path, manifest: Manifest, destination: Path) -> Path:
    if manifest.distribution != "portable":
        raise UpdateError(
            "wrong_distribution", "Only a portable archive can prepare a portable copy."
        )
    verify_artifact(artifact, manifest)
    parent_identity = directory_identity(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise UpdateError("staging_exists", "A previous prepared update needs reconciliation.")
    if (
        shutil.disk_usage(destination.parent).free
        < sum(row.size for row in manifest.files) + 16 * 1024 * 1024
    ):
        raise UpdateError("disk_full", "There is not enough space to prepare the portable update.")
    expected = {row.path: row for row in manifest.files}
    try:
        with zipfile.ZipFile(artifact, "r") as archive:
            entries = archive.infolist()
            names: set[str] = set()
            for entry in entries:
                name = payload_path(entry.filename)
                mode = entry.external_attr >> 16
                if (
                    name in names
                    or name not in expected
                    or entry.is_dir()
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or entry.flag_bits & 1
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or entry.file_size != expected[name].size
                ):
                    raise UpdateError(
                        "unsafe_payload",
                        "The portable archive does not match its signed inventory.",
                    )
                names.add(name)
            if names != expected.keys():
                raise UpdateError(
                    "unsafe_payload", "The portable archive omits signed application files."
                )
            if directory_identity(destination.parent) != parent_identity:
                raise UpdateError("unsafe_staging", "The update staging folder changed.")
            destination.mkdir()
            destination_identity = directory_identity(destination)
            for entry in entries:
                row = expected[entry.filename]
                target = destination / row.path
                ensure_directory(target.parent)
                directory_identity(target.parent)
                digest, count = hashlib.sha256(), 0
                with archive.open(entry) as source, target.open("xb") as output:
                    while chunk := source.read(min(256 * 1024, row.size - count + 1)):
                        count += len(chunk)
                        if count > row.size:
                            raise UpdateError(
                                "payload_mismatch", "An update file exceeds its signed length."
                            )
                        if output.write(chunk) != len(chunk):
                            raise OSError("Incomplete portable staging write")
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if count != row.size or digest.hexdigest() != row.sha256:
                    raise UpdateError(
                        "payload_mismatch", "An update file does not match its signed identity."
                    )
            if directory_identity(destination) != destination_identity:
                raise UpdateError("unsafe_staging", "The prepared portable folder changed.")
        marker = bounded_json((destination / "sylvia-portable.json").read_bytes(), maximum=128)
        if marker != {"version": 1} or type(marker["version"]) is not int:
            raise UpdateError("wrong_distribution", "The portable update marker is incompatible.")
        for name in ("Sylvia GM.exe", "sylvia-gm-owner.exe"):
            with (destination / name).open("rb") as executable:
                if executable.read(2) != b"MZ":
                    raise UpdateError(
                        "invalid_executable",
                        "The portable update has an invalid Windows executable.",
                    )
        verify_artifact(artifact, manifest)
        return destination
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError(
            "prepare_failed",
            "The portable update could not be prepared. The live copy is unchanged.",
        ) from error
