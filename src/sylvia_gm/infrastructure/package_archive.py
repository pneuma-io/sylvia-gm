"""Strict bounded portable F3 package archive admission."""

from __future__ import annotations

import hashlib
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO, cast

from sylvia_gm.application.schema_admission import validate_json_schema
from sylvia_gm.domain.authority import PackageRole, thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT,
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    CONTENT_DECLARATION_BYTE_LIMIT,
    ENGINE_FEATURE_PANEL_WORKSPACES,
    GAMEPLAY_SUPPORT_BYTE_LIMIT,
    GAMEPLAY_SUPPORT_PATH,
    PackageFile,
    PackageManifest,
    manifest_from_record,
    parse_campaign_setting_knowledge_bytes,
    parse_canonical_json_bytes,
    parse_content_declarations_bytes,
    parse_gameplay_support_bytes,
    resolve_exact_closure,
    validate_campaign_setting_knowledge_links,
    validate_content_declaration_links,
    validate_portable_path,
)
from sylvia_gm.domain.panel_layout import (
    PANEL_LAYOUT_BYTE_LIMIT,
    PANEL_LAYOUT_PATH,
    parse_panel_layout,
)
from sylvia_gm.infrastructure.zip_bounds import preflight_zip_directory, validate_zip_compression

PACKAGE_CHUNK_LIMIT = 64 * 1024
# Match retained Plan snapshots and repair's existing safe-content envelope. Desktop package
# selection keeps its smaller transport limit; direct owner callers need an independent bound.
PACKAGE_ARCHIVE_BYTE_LIMIT = 64 * 1024 * 1024
PACKAGE_MEMBER_BYTE_LIMIT = 64 * 1024 * 1024
PACKAGE_EXPANDED_BYTE_LIMIT = 128 * 1024 * 1024
PACKAGE_MEMBER_COUNT_LIMIT = 4096
_MANIFEST_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveFile:
    manifest: PackageManifest
    file: PackageFile
    member_name: str


class AdmittedPackageArchive:
    """One validated spooled archive; callers stream exact files in bounded chunks."""

    def __init__(
        self,
        spool: BinaryIO,
        format_version: int,
        manifests: tuple[PackageManifest, ...],
        files: tuple[ArchiveFile, ...],
    ) -> None:
        self._spool = spool
        self.format_version = format_version
        self.manifests = manifests
        self.files = files
        self.package_lock = resolve_exact_closure(manifests)
        # The spool is already admitted and privately owned. Share its read-only
        # ZIP directory instead of reparsing every member for every streamed file.
        self._archive = zipfile.ZipFile(spool, "r")
        self._closed = False

    def iter_file_chunks(self, archive_file: ArchiveFile) -> Iterator[bytes]:
        if self._closed:
            raise RuntimeError("Admitted package archive is closed")
        with self._archive.open(archive_file.member_name, "r") as member:
            while True:
                chunk = member.read(PACKAGE_CHUNK_LIMIT)
                if not chunk:
                    return
                yield chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._archive.close()
        finally:
            self._spool.close()

    def __enter__(self) -> AdmittedPackageArchive:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _read_bounded(source: BinaryIO, destination: BinaryIO) -> None:
    """Spool at most the supported input size, including callers without a file stat."""
    total = 0
    while True:
        chunk = source.read(min(PACKAGE_CHUNK_LIMIT, PACKAGE_ARCHIVE_BYTE_LIMIT - total + 1))
        if not chunk:
            return
        if not isinstance(chunk, bytes):
            raise CommandRejected("Package source did not provide bytes", code="archive_invalid")
        total += len(chunk)
        if total > PACKAGE_ARCHIVE_BYTE_LIMIT:
            raise CommandRejected("Package archive is too large", code="archive_limit")
        destination.write(chunk)


def _new_spool() -> BinaryIO:
    """Create a binary spool whose ownership is transferred to the caller."""

    return cast(
        BinaryIO,
        tempfile.SpooledTemporaryFile(max_size=PACKAGE_CHUNK_LIMIT * 4, mode="w+b"),
    )


def _read_member(archive: zipfile.ZipFile, name: str, *, maximum: int | None = None) -> bytes:
    result = bytearray()
    with archive.open(name, "r") as member:
        while True:
            chunk = member.read(PACKAGE_CHUNK_LIMIT)
            if not chunk:
                break
            result.extend(chunk)
            if maximum is not None and len(result) > maximum:
                raise CommandRejected("Package manifest is too large", code="manifest_invalid")
    return bytes(result)


def _normalized_name(name: str) -> str:
    validate_portable_path(name)
    normalized = unicodedata.normalize("NFC", name)
    if normalized != name:
        raise ValueError("Archive member name is not normalized")
    return normalized.casefold()


def admit_package_archive(source: BinaryIO) -> AdmittedPackageArchive:
    """Validate one source without retaining a caller path or raw diagnostic text."""

    spool = _new_spool()
    try:
        _read_bounded(source, spool)
        preflight_zip_directory(spool, maximum_members=PACKAGE_MEMBER_COUNT_LIMIT)
        with zipfile.ZipFile(spool, "r") as archive:
            infos = tuple(archive.infolist())
            if not infos:
                raise ValueError("Archive is empty")
            names: dict[str, zipfile.ZipInfo] = {}
            expanded = 0
            for info in infos:
                validate_zip_compression(info)
                if info.is_dir():
                    raise ValueError("Archive directories are not portable members")
                if (info.external_attr >> 16) & 0o170000 not in {0, stat.S_IFREG}:
                    raise ValueError("Archive members must be regular files")
                if info.flag_bits & 1:
                    raise ValueError("Encrypted package members are unsupported")
                expanded += info.file_size
                if (
                    info.file_size > PACKAGE_MEMBER_BYTE_LIMIT
                    or expanded > PACKAGE_EXPANDED_BYTE_LIMIT
                ):
                    raise CommandRejected("Package contents are too large", code="archive_limit")
                normalized = _normalized_name(info.filename)
                if normalized in names:
                    raise ValueError("Archive member aliases collide")
                names[normalized] = info
            manifest_info = names.get("manifest.json")
            if manifest_info is None:
                raise ValueError("Archive manifest is missing")
            manifest_bytes = _read_member(archive, manifest_info.filename, maximum=_MANIFEST_LIMIT)
            parsed = parse_canonical_json_bytes(manifest_bytes)
            if not isinstance(parsed, Mapping):
                raise ValueError("Archive manifest must be an object")
            raw_format = parsed.get("format")
            if (
                frozenset(parsed) != frozenset({"format", "packages"})
                or not isinstance(raw_format, int)
                or isinstance(raw_format, bool)
                or raw_format not in (1, 2, 3, 4)
            ):
                raise ValueError("Archive manifest format is unsupported")
            format_version = raw_format
            try:
                validate_content_shape(
                    "package_archive", thaw_json_value(parsed), file="manifest.json"
                )
            except ContentShapeError as error:
                raise CommandRejected(
                    "Package manifest fields are invalid", code="manifest_invalid",
                    diagnostics=error.diagnostics,
                ) from error
            packages_value = parsed["packages"]
            if not isinstance(packages_value, Sequence) or isinstance(
                packages_value, (str, bytes, bytearray)
            ):
                raise ValueError("Archive packages must be an array")
            manifests = tuple(
                manifest_from_record(value, format_version=format_version)
                for value in packages_value
            )
            for manifest in manifests:
                for state_schema in manifest.state_schemas:
                    validate_json_schema(state_schema.schema, label="Starting-state schema")
                for declaration in manifest.participant_artifacts:
                    validate_json_schema(
                        declaration.schema,
                        label=f"Participant artifact {declaration.kind}",
                    )
            resolve_exact_closure(manifests)
            expected_names = {"manifest.json"}
            files: list[ArchiveFile] = []
            for manifest in manifests:
                manifest_files: dict[str, tuple[PackageFile, zipfile.ZipInfo]] = {}
                for file in manifest.files:
                    member_name = f"packages/{manifest.package_id}/{file.path}"
                    expected_names.add(_normalized_name(member_name))
                    indexed_info = names.get(_normalized_name(member_name))
                    if indexed_info is None:
                        raise ValueError("Indexed package file is absent")
                    if indexed_info.file_size != file.byte_count:
                        raise ValueError("Indexed package file size does not match")
                    digest = hashlib.sha256()
                    count = 0
                    with archive.open(indexed_info.filename, "r") as member:
                        while True:
                            chunk = member.read(PACKAGE_CHUNK_LIMIT)
                            if not chunk:
                                break
                            count += len(chunk)
                            if count > file.byte_count:
                                raise ValueError("Indexed package file is larger than declared")
                            digest.update(chunk)
                    if count != file.byte_count or digest.hexdigest() != file.sha256:
                        raise ValueError("Indexed package file hash does not match")
                    files.append(ArchiveFile(manifest, file, indexed_info.filename))
                    manifest_files[file.path] = (file, indexed_info)
                if manifest.content_declarations_path is not None:
                    declaration_file = manifest_files.get(manifest.content_declarations_path)
                    if declaration_file is None:
                        raise ValueError("Package content declarations are unavailable")
                    declaration_bytes = _read_member(
                        archive,
                        declaration_file[1].filename,
                        maximum=CONTENT_DECLARATION_BYTE_LIMIT,
                    )
                    try:
                        declarations = parse_content_declarations_bytes(declaration_bytes)
                        validate_content_declaration_links(manifest, declarations)
                    except ValueError as error:
                        raise CommandRejected(
                            "Package content declarations are invalid",
                            code="package_content_invalid",
                            diagnostics=(
                                RulesetDiagnostic(
                                    code="package_content_invalid",
                                    phase="archive",
                                    message=str(error),
                                    file=manifest.content_declarations_path,
                                    package_id=manifest.package_id,
                                    guidance=(
                                        "Edit this package's content declarations. "
                                        "Link retained local artifacts and remove links "
                                        "to absent files. Save canonical JSON: sorted "
                                        "keys, compact separators, no trailing newline."
                                    ),
                                ),
                            ),
                        ) from error
                knowledge_file = manifest_files.get(CAMPAIGN_SETTING_KNOWLEDGE_PATH)
                if knowledge_file is not None:
                    knowledge_bytes = _read_member(
                        archive,
                        knowledge_file[1].filename,
                        maximum=CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT,
                    )
                    try:
                        knowledge = parse_campaign_setting_knowledge_bytes(knowledge_bytes)
                        validate_campaign_setting_knowledge_links(manifest, knowledge)
                    except ValueError as error:
                        raise CommandRejected(
                            "Campaign Setting knowledge is invalid",
                            code="package_knowledge_invalid",
                            diagnostics=(
                                RulesetDiagnostic(
                                    code="package_knowledge_invalid",
                                    phase="archive",
                                    message=str(error),
                                    file=CAMPAIGN_SETTING_KNOWLEDGE_PATH,
                                    package_id=manifest.package_id,
                                    guidance=(
                                        "Edit setting-knowledge.json in the setting draft. "
                                        "Save canonical JSON: sorted keys, compact separators, "
                                        "no trailing newline. Reference only files included "
                                        "in this package."
                                    ),
                                ),
                            ),
                        ) from error
                support_file = manifest_files.get(GAMEPLAY_SUPPORT_PATH)
                if support_file is not None:
                    support_bytes = _read_member(
                        archive,
                        support_file[1].filename,
                        maximum=GAMEPLAY_SUPPORT_BYTE_LIMIT,
                    )
                    parse_gameplay_support_bytes(support_bytes)
                layout_file = manifest_files.get(PANEL_LAYOUT_PATH)
                if layout_file is not None:
                    if manifest.role is not PackageRole.PRIMARY or (
                        ENGINE_FEATURE_PANEL_WORKSPACES not in manifest.compatibility["requires"]
                    ):
                        raise ValueError("Panel layouts require a supporting primary Ruleset")
                    layout_bytes = _read_member(
                        archive,
                        layout_file[1].filename,
                        maximum=PANEL_LAYOUT_BYTE_LIMIT,
                    )
                    parse_panel_layout(layout_bytes, manifest.ui_surfaces)
            if set(names) != expected_names:
                raise ValueError("Archive contains unindexed or extra members")
        spool.seek(0)
        return AdmittedPackageArchive(
            spool,
            format_version,
            manifests,
            tuple(files),
        )
    except CommandRejected:
        spool.close()
        raise
    except (
        OSError, ValueError, TypeError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile
    ) as error:
        spool.close()
        raise CommandRejected(
            "Package archive admission failed",
            code="archive_invalid",
            diagnostics=(
                RulesetDiagnostic(
                    code="archive_invalid",
                    phase="archive",
                    message=str(error),
                    file="manifest.json",
                ),
            ),
        ) from error
    except BaseException:
        spool.close()
        raise
