"""Portable synthetic fixture packs, verified before a run opens their copied save."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .contracts import Setup, digest, read_json, write_json
from .fixtures import copy_save, seed


def inventory(directory: Path) -> dict[str, str]:
    """Hash only the closed save and its authored files; reject links and unexpected files."""
    files = {}
    for path in [directory, *sorted(directory.rglob("*"))]:
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Fixture packs cannot contain filesystem links")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            if relative != "campaign.sqlite3" and relative.split("/")[0] not in {
                "campaigns",
                "participants",
            }:
                raise ValueError(f"Unexpected fixture file: {relative}")
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "campaign.sqlite3" not in files:
        raise ValueError("Fixture pack has no closed campaign database")
    return files


def freeze(output: Path, identity: str, source: dict[str, Any]) -> dict[str, Any]:
    """Generate once through Sylvia, then publish a self-contained, immutable local pack."""
    Setup.safe_id(identity)
    if output.exists():
        raise ValueError("Fixture output already exists; never replace a frozen pack")
    output.mkdir(parents=True)
    generated, ids = seed(output / "preparation")
    copied = output / "save" / "campaign.sqlite3"
    copy_save(generated, copied)
    descriptor = {
        "schema_version": 1,
        "id": identity,
        "recipe_version": 3,
        "source": source,
        "ids": ids,
        "observation": read_json(output / "preparation" / "seed.json")["observation"],
        "files": inventory(copied.parent),
    }
    # Preparation holds only this command's synthetic save and private audit, never user data.
    preparation = (output / "preparation").resolve()
    if preparation.parent != output.resolve():
        raise ValueError("Preparation directory escaped the selected fixture output")
    shutil.rmtree(preparation)
    descriptor["sha256"] = digest(descriptor)
    write_json(output / "fixture.json", descriptor)
    return descriptor


def verify(directory: Path, source: dict[str, Any]) -> dict[str, Any]:
    """Detect edited bytes or incompatible app code before starting any provider."""
    if directory.is_symlink() or getattr(directory.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("Fixture packs cannot be filesystem links")
    descriptor = read_json(directory / "fixture.json")
    if not isinstance(descriptor, dict):
        raise ValueError("Fixture descriptor must be an object")
    if descriptor.get("schema_version") != 1 or descriptor.get("recipe_version") not in {1, 2, 3}:
        raise ValueError("Unsupported fixture pack version")
    specification = {k: v for k, v in descriptor.items() if k != "sha256"}
    if digest(specification) != descriptor.get("sha256"):
        raise ValueError("Fixture descriptor identity mismatch")
    if descriptor["source"]["application_sha256"] != source["application_sha256"]:
        raise ValueError("Fixture pack requires the same Sylvia application and content bytes")
    if inventory(directory / "save") != descriptor["files"]:
        raise ValueError("Frozen fixture file identity mismatch")
    return descriptor
