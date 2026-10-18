"""Deterministically materialize normalized package drafts through one admitted seam."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO, cast

from sylvia_gm.domain.authority import PackageLock, thaw_json_value
from sylvia_gm.domain.package_library import (
    PackageManifest,
    canonical_json_bytes,
    closure_sha256,
    content_root_for_record,
    manifest_from_record,
    participant_schema_sha256,
    resolve_exact_closure,
    validate_portable_path,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class NormalizedPackageDraft:
    """One draft record and its already-ingested, portable payload files."""

    record: Mapping[str, object]
    payloads: Mapping[str, bytes]
    format_version: int = 3


def snapshot_record(record, *, primary, settings):
    """Remove derived claims and normalize legacy ownership for an editable owned copy."""
    result = dict(record)
    result.pop("content_sha256", None)
    result.pop("files", None)
    if "content_kind" in record:
        result["dependencies"] = [
            {key: value for key, value in dep.items() if key != "content_sha256"}
            for dep in record["dependencies"]
        ]
        return result
    identity = result["id"]
    setting_ids = {item.package_id for item in settings}
    result["content_kind"] = (
        "ruleset"
        if identity == primary.package_id
        else "campaign_setting"
        if identity in setting_ids
        else "support"
    )
    dependencies = [
        {key: value for key, value in dep.items() if key != "content_sha256"}
        for dep in result["dependencies"]
        if dep["id"] not in setting_ids
    ]
    if identity in setting_ids:
        dependencies = [dep for dep in dependencies if dep["id"] != primary.package_id]
        dependencies.append(
            {
                "namespace": "ruleset_snapshot",
                "kind": "ruleset_snapshot",
                "id": primary.package_id,
                "version": primary.version,
            }
        )
    result["dependencies"] = dependencies
    return result


@dataclass
class MaterializedPackageArchive:
    """One deterministic, pre-admitted archive whose stream is caller-owned while open."""

    archive: BinaryIO
    manifests: tuple[PackageManifest, ...]
    package_lock: PackageLock
    closure_sha256: str

    @property
    def manifest(self) -> PackageManifest:
        """Return the only manifest when the caller supplied one package draft."""

        if len(self.manifests) != 1:
            raise ValueError("A multi-package archive has no singular manifest")
        return self.manifests[0]

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> MaterializedPackageArchive:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _participant_artifact_hashes(record: dict[str, object]) -> None:
    """Derive the identity of all declared participant and starting-state requirements."""

    raw_artifacts = record.get("participant_artifacts")
    if raw_artifacts is None:
        return
    if not isinstance(raw_artifacts, list):
        raise ValueError("Package participant artifacts must be an array")
    projected: list[object] = []
    for raw in raw_artifacts:
        if not isinstance(raw, dict) or not isinstance(raw.get("schema"), dict):
            raise ValueError("Package participant artifact schema is invalid")
        expected = participant_schema_sha256(
            raw["schema"],
            entity_state_schema=raw.get("entity_state_schema"),
            initial_state_calculations=raw.get("initial_state_calculations", ()),
            initial_state_validation=raw.get("initial_state_validation"),
        )
        supplied = raw.get("schema_sha256")
        if supplied is not None and supplied != expected:
            raise ValueError("Package participant artifact schema hash is invalid")
        projected.append({**raw, "schema_sha256": expected})
    record["participant_artifacts"] = projected


def _zip_member(name: str, value: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    """Return one deterministic regular-file ZIP member."""

    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, value


def materialize_package_archive(
    drafts: Sequence[NormalizedPackageDraft],
) -> MaterializedPackageArchive:
    """Complete, archive, and pre-admit one normalized exact package closure."""

    if not drafts:
        raise ValueError("Package materialization requires at least one draft")
    format_versions = {draft.format_version for draft in drafts}
    if len(format_versions) != 1 or not format_versions <= {1, 2, 3, 4}:
        raise ValueError("Package drafts require one supported archive format")
    format_version = next(iter(format_versions))
    completed: list[tuple[dict[str, object], dict[str, bytes]]] = []
    package_ids: set[str] = set()
    for draft in drafts:
        record = cast(dict[str, object], thaw_json_value(dict(draft.record)))
        package_id = record.get("id")
        if not isinstance(package_id, str) or not package_id or package_id in package_ids:
            raise ValueError("Package materialization requires unique package identities")
        package_ids.add(package_id)
        payloads: dict[str, bytes] = {}
        folded_paths: set[str] = set()
        for path, value in sorted(draft.payloads.items()):
            validate_portable_path(path)
            folded = path.casefold()
            if folded in folded_paths or not isinstance(value, bytes):
                raise ValueError("Package payload files are invalid")
            folded_paths.add(folded)
            payloads[path] = value
        if not payloads:
            raise ValueError("Package payload files are empty")
        _participant_artifact_hashes(record)
        record["files"] = [
            {
                "path": path,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
            for path, value in payloads.items()
        ]
        completed.append((record, payloads))

    records_by_id = {cast(str, record["id"]): record for record, _ in completed}
    completing: set[str] = set()
    completed_ids: set[str] = set()

    def complete_record(package_id: str) -> dict[str, object]:
        """Resolve local exact dependencies before hashing their referring draft."""

        if package_id in completed_ids:
            return records_by_id[package_id]
        if package_id in completing:
            raise ValueError("Package draft dependencies contain a cycle")
        completing.add(package_id)
        record = records_by_id[package_id]
        dependencies = record.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError("Package dependencies must be an array")
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                raise ValueError("Package dependency is invalid")
            dependency_id = dependency.get("id")
            if not isinstance(dependency_id, str):
                raise ValueError("Package dependency identity is invalid")
            local = records_by_id.get(dependency_id)
            if local is None:
                continue
            dependency_record = complete_record(dependency_id)
            if dependency.get("version") != dependency_record.get("version"):
                raise ValueError("Local package dependency version does not match its draft")
            expected = cast(str, dependency_record["content_sha256"])
            supplied = dependency.get("content_sha256")
            if supplied is not None and supplied != expected:
                raise ValueError("Local package dependency hash does not match its draft")
            dependency["content_sha256"] = expected
        record["content_sha256"] = content_root_for_record(record)
        completing.remove(package_id)
        completed_ids.add(package_id)
        return record

    for record, _ in completed:
        complete_record(cast(str, record["id"]))

    manifests = tuple(
        manifest_from_record(record, format_version=format_version) for record, _ in completed
    )
    package_lock = resolve_exact_closure(manifests)
    order = {entry.package_id: entry.load_order for entry in package_lock.entries}
    manifests = tuple(sorted(manifests, key=lambda item: order[item.package_id]))
    completed.sort(key=lambda item: order[cast(str, item[0]["id"])])
    closure = closure_sha256(package_lock)
    manifest_bytes = canonical_json_bytes(
        {"format": format_version, "packages": [record for record, _ in completed]}
    )
    # The returned MaterializedPackageArchive owns this stream and closes it on exit.
    archive = cast(
        BinaryIO,
        tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b"),  # noqa: SIM115
    )
    try:
        with zipfile.ZipFile(archive, "w") as output:
            info, value = _zip_member("manifest.json", manifest_bytes)
            output.writestr(info, value)
            for record, payloads in completed:
                package_id = cast(str, record["id"])
                for path, payload in payloads.items():
                    info, value = _zip_member(f"packages/{package_id}/{path}", payload)
                    output.writestr(info, value)
        archive.seek(0)
        with admit_package_archive(archive) as admitted:
            if admitted.package_lock != package_lock:
                raise ValueError("Materialized package lock changed during pre-admission")
        archive.seek(0)
        return MaterializedPackageArchive(archive, manifests, package_lock, closure)
    except BaseException:
        archive.close()
        raise


__all__ = [
    "MaterializedPackageArchive",
    "NormalizedPackageDraft",
    "materialize_package_archive",
]
