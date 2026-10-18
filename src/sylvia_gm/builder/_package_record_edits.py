"""Apply explicit package-record replacements to an owned package snapshot."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.builder._package_materializer import (
    _bounded_json_shape,
    _reject_constant,
    _reject_reparse_chain,
    _unique_object,
)
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic

_RECORD_DIRECTORY = "package-records"
_RECORD_BYTE_LIMIT = 2 * 1024 * 1024
_DERIVED_RECORD_KEYS = frozenset({"files", "content_sha256"})


def _read_record(workspace: Path, path: Path) -> dict[str, object]:
    """Read one bounded regular JSON record after rejecting reparse points."""

    try:
        _reject_reparse_chain(workspace, path)
        if not path.is_file() or path.stat().st_size > _RECORD_BYTE_LIMIT:
            raise ValueError("Package record file is unavailable or too large")
        with path.open("rb") as source:
            raw = source.read(_RECORD_BYTE_LIMIT + 1)
        if len(raw) > _RECORD_BYTE_LIMIT:
            raise ValueError("Package record file is too large")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _bounded_json_shape(value)
        if not isinstance(value, dict):
            raise ValueError("Package record must be an object")
        return cast(dict[str, object], thaw_json_value(value))
    except CommandRejected:
        raise
    except json.JSONDecodeError as error:
        raise CommandRejected(
            "Builder package record contains invalid JSON",
            code="package_record_json_invalid",
            diagnostics=(
                RulesetDiagnostic(
                    code="package_record_json_invalid",
                    phase="package_record",
                    file=path.relative_to(workspace).as_posix(),
                    message=f"Invalid JSON syntax at line {error.lineno}, column {error.colno}.",
                    guidance="Read nearby lines and repair the JSON syntax with a bounded edit.",
                ),
            ),
        ) from error
    except (
        OSError,
        UnicodeError,
        RecursionError,
        TypeError,
        ValueError,
    ) as error:
        raise CommandRejected("Builder package record is invalid") from error


def _editable_record(
    original: NormalizedPackageDraft,
    replacement: dict[str, object],
) -> dict[str, object]:
    """Validate a complete replacement and remove materializer-owned fields."""

    original_record = dict(original.record)
    editable_keys = frozenset(original_record) - _DERIVED_RECORD_KEYS
    supplied_keys = frozenset(replacement)
    if supplied_keys not in {editable_keys, editable_keys | _DERIVED_RECORD_KEYS}:
        raise CommandRejected("Builder package record fields do not match the saved package")
    replacement = {
        key: value for key, value in replacement.items() if key not in _DERIVED_RECORD_KEYS
    }
    for key in ("id", "role", "content_kind"):
        if replacement.get(key) != original_record.get(key):
            raise CommandRejected(f"Builder package record cannot change {key}")
    return replacement


def _dependencies(record: dict[str, object]) -> list[dict[str, object]]:
    value = record.get("dependencies")
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CommandRejected("Builder package dependencies are invalid")
    return cast(list[dict[str, object]], value)


def _rebind_dependencies(
    records: dict[str, dict[str, object]],
    originals: Mapping[str, NormalizedPackageDraft],
) -> None:
    """Bind every dependency to its supplied local draft without guessing rewires."""

    for package_id, record in records.items():
        original_by_namespace = {
            dependency.get("namespace"): dependency
            for dependency in _dependencies(dict(originals[package_id].record))
        }
        rebound: list[dict[str, object]] = []
        for dependency in _dependencies(record):
            dependency_id = dependency.get("id")
            dependency_version = dependency.get("version")
            namespace = dependency.get("namespace")
            if not isinstance(dependency_id, str) or dependency_id not in records:
                raise CommandRejected("Builder package dependency is absent from the saved copy")
            target_version = records[dependency_id].get("version")
            if not isinstance(target_version, str):
                raise CommandRejected("Builder package dependency target version is invalid")
            original = original_by_namespace.get(namespace)
            unchanged_identity = (
                isinstance(original, dict)
                and dependency_id == original.get("id")
                and dependency_version == original.get("version")
            )
            if dependency_version != target_version:
                if not unchanged_identity:
                    raise CommandRejected(
                        "Builder package dependency version does not match its supplied draft"
                    )
                dependency_version = target_version
            rebound.append(
                {
                    key: value
                    for key, value in dependency.items()
                    if key not in {"version", "content_sha256"}
                }
                | {"version": dependency_version}
            )
        record["dependencies"] = rebound


def _retained_package_ids(records: Mapping[str, dict[str, object]]) -> set[str]:
    primaries = [item for item in records.values() if item.get("role") == "primary"]
    settings = [item for item in records.values() if item.get("content_kind") == "campaign_setting"]
    if len(primaries) != 1 or len(settings) > 1:
        raise CommandRejected("Owned package copy has invalid primary or Setting roots")
    roots = {cast(str, primaries[0]["id"]), *(cast(str, item["id"]) for item in settings)}
    reached: set[str] = set()
    pending = list(roots)
    while pending:
        package_id = pending.pop()
        if package_id in reached:
            continue
        reached.add(package_id)
        pending.extend(
            cast(str, dependency["id"]) for dependency in _dependencies(records[package_id])
        )
    return reached


def apply_package_record_edits(
    workspace: Path,
    draft_root: Path,
    drafts: tuple[NormalizedPackageDraft, ...],
) -> tuple[NormalizedPackageDraft, ...]:
    """Apply whole-record edits and retain the exact reachable owned closure."""

    workspace = workspace.resolve()
    if not draft_root.is_absolute():
        draft_root = workspace / draft_root
    if not drafts or any(draft.format_version != 4 for draft in drafts):
        raise CommandRejected("Package record edits require one format-4 saved package copy")
    originals: dict[str, NormalizedPackageDraft] = {}
    for draft in drafts:
        package_id = draft.record.get("id")
        if not isinstance(package_id, str) or package_id in originals:
            raise CommandRejected("Owned package copy has invalid package identities")
        originals[package_id] = draft

    try:
        _reject_reparse_chain(workspace, draft_root)
        record_root = draft_root / _RECORD_DIRECTORY
        if not record_root.exists() and not record_root.is_symlink():
            records = {
                package_id: cast(dict[str, object], thaw_json_value(dict(draft.record)))
                for package_id, draft in originals.items()
            }
        else:
            _reject_reparse_chain(workspace, record_root)
            if not record_root.is_dir():
                raise CommandRejected("Builder package-records path must be a directory")
            expected_paths = {f"{package_id}.json": package_id for package_id in originals}
            if len({name.casefold() for name in expected_paths}) != len(expected_paths):
                raise CommandRejected("Owned package identities collide as record paths")
            for entry in record_root.iterdir():
                if entry.name not in expected_paths:
                    raise CommandRejected(
                        "Builder package-records directory contains an unknown entry"
                    )
            records = {}
            for package_id, draft in originals.items():
                path = record_root / f"{package_id}.json"
                if path.exists() or path.is_symlink():
                    records[package_id] = _editable_record(draft, _read_record(workspace, path))
                else:
                    records[package_id] = cast(
                        dict[str, object], thaw_json_value(dict(draft.record))
                    )

        for record in records.values():
            record.pop("files", None)
            record.pop("content_sha256", None)
        _rebind_dependencies(records, originals)
        retained = _retained_package_ids(records)
        return tuple(
            NormalizedPackageDraft(records[package_id], draft.payloads, 4)
            for package_id, draft in originals.items()
            if package_id in retained
        )
    except CommandRejected:
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise CommandRejected("Builder package record edits are invalid") from error


__all__ = ["apply_package_record_edits"]
