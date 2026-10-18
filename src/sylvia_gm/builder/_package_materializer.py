"""Deterministically materialize one guarded Builder ruleset draft."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Never, cast

from sylvia_gm.application.package_materialization import (
    MaterializedPackageArchive as MaterializedRulesetArchive,
)
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.builder._ruleset_source import (
    COMPACT_RULESET_SOURCE,
    compile_ruleset_source,
)
from sylvia_gm.domain._content_schema_ruleset_tests import RULESET_TESTS_PATH
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import validate_portable_path
from sylvia_gm.domain.ruleset_tests import parse_ruleset_tests

RULESET_DRAFT_DIRECTORY = "ruleset-draft"
RULESET_DRAFT_DESCRIPTOR = "package.json"
RULESET_DRAFT_FILES_DIRECTORY = "files"
RULESET_OUTPUT_DIRECTORY = "output"

_DESCRIPTOR_BYTE_LIMIT = 2 * 1024 * 1024
_PAYLOAD_BYTE_LIMIT = 32 * 1024 * 1024
_PAYLOAD_FILE_LIMIT = 256
_PAYLOAD_ENTRY_LIMIT = 1024
_PAYLOAD_PATH_DEPTH_LIMIT = 12
_JSON_DEPTH_LIMIT = 24
_JSON_NODE_LIMIT = 100_000
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

_FORMAT_3_DRAFT_KEYS = frozenset(
    {
        "id",
        "version",
        "role",
        "metadata",
        "provenance",
        "compatibility",
        "minimum_model_capability_tier",
        "dependencies",
        "capabilities",
        "tools",
        "prompt_fragments",
        "ui_surfaces",
        "participant_artifacts",
        "content_declarations",
    }
)


def _reject_constant(_value: str) -> Never:
    raise ValueError("JSON constants must be finite and standard")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object members must be unique")
        result[key] = value
    return result


def _bounded_json_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        if depth > _JSON_DEPTH_LIMIT:
            raise ValueError("Builder package descriptor is too deeply nested")
        nodes += 1
        if nodes > _JSON_NODE_LIMIT:
            raise ValueError("Builder package descriptor has too many values")
        if isinstance(item, Mapping):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _read_descriptor(path: Path) -> dict[str, object]:
    try:
        if path.stat().st_size > _DESCRIPTOR_BYTE_LIMIT:
            raise ValueError("Builder package descriptor is too large")
        with path.open("rb") as source:
            raw = source.read(_DESCRIPTOR_BYTE_LIMIT + 1)
        if len(raw) > _DESCRIPTOR_BYTE_LIMIT:
            raise ValueError("Builder package descriptor is too large")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _bounded_json_shape(value)
        if not isinstance(value, dict) or frozenset(value) not in (
            _FORMAT_3_DRAFT_KEYS, _FORMAT_3_DRAFT_KEYS | {"state_schemas"},
        ):
            raise ValueError("Builder package descriptor shape is invalid")
        if value.get("role") != "primary":
            raise ValueError("Builder ruleset draft must contain one primary package")
        return cast(dict[str, object], thaw_json_value(value))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise CommandRejected(
            "Builder ruleset descriptor is invalid",
            code="builder_descriptor_invalid",
        ) from error


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as error:
        raise CommandRejected(
            "Builder ruleset payload is unavailable",
            code="builder_payload_invalid",
        ) from error
    return path.is_symlink() or bool(attributes & _REPARSE_POINT)


def _reject_reparse_chain(root: Path, path: Path) -> None:
    current = path
    while True:
        if _is_reparse_point(current):
            raise CommandRejected(
                "Builder ruleset payload cannot contain links",
                code="builder_payload_invalid",
            )
        if current == root:
            return
        if root not in current.parents:
            raise CommandRejected(
                "Builder ruleset payload escaped its draft directory",
                code="builder_payload_invalid",
            )
        current = current.parent


def _read_payloads(files_root: Path) -> dict[str, bytes]:
    try:
        if not files_root.is_dir():
            raise ValueError("Builder ruleset payload directory is missing")
        _reject_reparse_chain(files_root, files_root)
        retained_entries: list[Path] = []
        for entry in files_root.rglob("*"):
            if len(retained_entries) >= _PAYLOAD_ENTRY_LIMIT:
                raise ValueError("Builder ruleset payload has too many entries")
            retained_entries.append(entry)
        entries = sorted(retained_entries, key=lambda item: item.as_posix().casefold())
        payloads: dict[str, bytes] = {}
        folded: set[str] = set()
        total_bytes = 0
        for entry in entries:
            _reject_reparse_chain(files_root, entry)
            if entry.is_dir():
                continue
            if not entry.is_file():
                raise ValueError("Builder ruleset payload contains a special file")
            relative = entry.relative_to(files_root).as_posix()
            validate_portable_path(relative)
            if len(Path(relative).parts) > _PAYLOAD_PATH_DEPTH_LIMIT:
                raise ValueError("Builder ruleset payload path is too deep")
            normalized = relative.casefold()
            if normalized in folded:
                raise ValueError("Builder ruleset payload paths collide")
            folded.add(normalized)
            if len(payloads) >= _PAYLOAD_FILE_LIMIT:
                raise ValueError("Builder ruleset payload has too many files")
            with entry.open("rb") as source:
                value = source.read(_PAYLOAD_BYTE_LIMIT + 1)
            total_bytes += len(value)
            if len(value) > _PAYLOAD_BYTE_LIMIT or total_bytes > _PAYLOAD_BYTE_LIMIT:
                raise ValueError("Builder ruleset payload is too large")
            payloads[relative] = value
        if not payloads:
            raise ValueError("Builder ruleset payload is empty")
        return payloads
    except CommandRejected:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise CommandRejected(
            "Builder ruleset payload is invalid",
            code="builder_payload_invalid",
        ) from error


def _reject_derived_participant_hashes(record: dict[str, object]) -> None:
    """Keep generated schema hashes out of the untrusted Builder descriptor."""

    raw_artifacts = record.get("participant_artifacts")
    if not isinstance(raw_artifacts, list):
        return
    for raw in raw_artifacts:
        if isinstance(raw, dict) and "schema_sha256" in raw:
            raise CommandRejected(
                "Builder participant artifact declaration is invalid",
                code="builder_descriptor_invalid",
            )


def materialize_ruleset_draft(workspace: Path) -> MaterializedRulesetArchive:
    """Build a Ruleset package without attaching a world or acquiring owner authority."""

    workspace = workspace.resolve()
    draft_root = workspace / RULESET_DRAFT_DIRECTORY
    descriptor_path = draft_root / RULESET_DRAFT_DESCRIPTOR
    compact_source_path = draft_root / COMPACT_RULESET_SOURCE
    files_root = draft_root / RULESET_DRAFT_FILES_DIRECTORY
    try:
        _reject_reparse_chain(workspace, draft_root)
        if compact_source_path.is_file():
            _reject_reparse_chain(workspace, compact_source_path)
            source_bytes = compact_source_path.read_bytes()
            if len(source_bytes) > _DESCRIPTOR_BYTE_LIMIT:
                raise CommandRejected(
                    "Builder Ruleset source is too large",
                    code="builder_ruleset_source_invalid",
                )
            compiled = compile_ruleset_source(source_bytes)
            return materialize_package_archive((compiled.draft,))
        _reject_reparse_chain(workspace, descriptor_path)
        record = _read_descriptor(descriptor_path)
        payloads = _read_payloads(files_root)
        if RULESET_TESTS_PATH in payloads:
            parse_ruleset_tests(payloads[RULESET_TESTS_PATH])
        if "runtime/ruleset.yaml" not in payloads:
            raise CommandRejected(
                "Builder ruleset runtime is missing",
                code="builder_payload_invalid",
            )
        declarations_path = record.get("content_declarations")
        if not isinstance(declarations_path, str) or declarations_path not in payloads:
            raise CommandRejected(
                "Builder content declarations are missing",
                code="builder_payload_invalid",
            )
        _reject_derived_participant_hashes(record)
        return materialize_package_archive((
            NormalizedPackageDraft({**record, "content_kind": "ruleset"}, payloads, 4),
        ))
    except ContentShapeError as error:
        raise CommandRejected(
            "Builder Ruleset scenarios are invalid", code="ruleset_tests_invalid",
            diagnostics=error.diagnostics,
        ) from error
    except CommandRejected:
        raise
    except (TypeError, ValueError) as error:
        raise CommandRejected(
            "Builder ruleset draft is invalid",
            code="builder_package_invalid",
            diagnostics=(
                RulesetDiagnostic(
                    code="builder_materialization_invalid",
                    phase="materialization",
                    message=str(error),
                    file=(
                        COMPACT_RULESET_SOURCE
                        if compact_source_path.is_file()
                        else RULESET_DRAFT_DESCRIPTOR
                    ),
                    path="$",
                ),
            ),
        ) from error
    except OSError as error:
        raise CommandRejected(
            "Builder ruleset draft is unavailable",
            code="builder_package_unavailable",
        ) from error


def export_ruleset_candidate(
    workspace: Path,
    materialized: MaterializedRulesetArchive,
) -> str:
    """Persist one admitted archive at a stable workspace-relative artifact path."""

    workspace = workspace.resolve()
    draft_root = workspace / RULESET_DRAFT_DIRECTORY
    output_root = draft_root / RULESET_OUTPUT_DIRECTORY
    try:
        _reject_reparse_chain(workspace, draft_root)
        output_root.mkdir(parents=False, exist_ok=True)
        _reject_reparse_chain(workspace, output_root)
        filename = f"candidate-{materialized.closure_sha256}.sylvia-gm-package"
        target = output_root / filename
        if target.exists():
            _reject_reparse_chain(workspace, target)

        materialized.archive.seek(0)
        value = materialized.archive.read()
        materialized.archive.seek(0)
        if target.is_file() and target.read_bytes() == value:
            return target.relative_to(workspace).as_posix()

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=".candidate-",
                suffix=".tmp",
                dir=output_root,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(value)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return target.relative_to(workspace).as_posix()
    except CommandRejected:
        raise
    except OSError as error:
        raise CommandRejected(
            "Builder ruleset artifact could not be persisted",
            code="builder_artifact_unavailable",
        ) from error


def ruleset_install_id(session_id: str, closure: str) -> str:
    """Derive a stable content-bound publication identity for lost-ack replay."""

    digest = hashlib.sha256(f"{session_id}\0{closure}".encode()).hexdigest()
    return f"builder-{digest}"
