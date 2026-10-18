"""Stage and fully validate editable copies of safely readable failed imports."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast

from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.package_materialization import (
    MaterializedPackageArchive,
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.plan_bundle import PLAN_BUNDLE_BYTE_LIMIT, _read_bundle
from sylvia_gm.application.plan_snapshot import PLAN_SNAPSHOT_FILENAME, validate_plan_snapshot
from sylvia_gm.application.reusable_plans import (
    REUSABLE_PLAN_FORMAT_ID,
    REUSABLE_PLAN_FORMAT_VERSION,
    load_reusable_campaign_plan,
)
from sylvia_gm.builder._package_materializer import _reject_reparse_chain
from sylvia_gm.domain.authority import PackageRole, SafePayload
from sylvia_gm.domain.campaign_plan import ValidatedCampaignPlan
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.interview_contribution import parse_interview_contribution_bytes
from sylvia_gm.domain.package_library import (
    campaign_setting_entries,
    canonical_json_bytes,
    validate_portable_path,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from sylvia_gm.infrastructure.zip_bounds import preflight_zip_directory, validate_zip_compression

RepairKind = Literal["package", "plan"]

_DRAFT_NAME = "repair-draft"
_MANIFEST_LIMIT = 2 * 1024 * 1024
_SOURCE_BYTE_LIMIT = max(PLAN_BUNDLE_BYTE_LIMIT, 64 * 1024 * 1024)
_EXPANDED_BYTE_LIMIT = 128 * 1024 * 1024
_MEMBER_BYTE_LIMIT = 64 * 1024 * 1024
_MEMBER_COUNT_LIMIT = 4096
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_PLAN_ID_PATTERN = re.compile(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}")
_PLAN_FILES = frozenset({"record.json", "plan.json", "plan.md", "interview-contribution.json"})


@dataclass(frozen=True)
class RepairSourceInspection:
    """Safe public facts about one repairable selected source."""

    kind: RepairKind
    source_sha256: str
    file_name: str
    diagnostics: tuple[RulesetDiagnostic, ...]
    identity: str | None


@dataclass(frozen=True)
class StagedRepairCopy:
    """The fixed editable location populated after the player selects Fix."""

    kind: RepairKind
    source_sha256: str
    draft_root: Path


@dataclass
class MaterializedRepairDraft:
    """One pre-admitted repaired artifact whose archive is owned while open."""

    kind: RepairKind
    archive: BinaryIO
    closure_sha256: str
    plan: ValidatedCampaignPlan | None = None
    interview_contribution: SafePayload | None = None
    setup_id: str | None = None

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> MaterializedRepairDraft:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass(frozen=True)
class _SafeArchive:
    sha256: str
    files: Mapping[str, bytes]


def _read_selected_source(source_path: Path) -> tuple[str, bytes]:
    """Read one selected regular file with a hard bound and compute its source fence."""

    path = Path(source_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Repair source must be one regular file")
    value = bytearray()
    with path.open("rb") as source:
        while True:
            chunk = source.read(64 * 1024)
            if not chunk:
                break
            value.extend(chunk)
            if len(value) > _SOURCE_BYTE_LIMIT:
                raise ValueError("Repair source is oversized")
    if not value:
        raise ValueError("Repair source is empty")
    data = bytes(value)
    return hashlib.sha256(data).hexdigest(), data


def _folded_portable_name(name: str) -> str:
    try:
        validate_portable_path(name)
    except ValueError as error:
        raise ValueError("Archive member path is unsafe or non-portable") from error
    if unicodedata.normalize("NFC", name) != name:
        raise ValueError("Archive member name is not normalized")
    return name.casefold()


def _read_safe_zip(data: bytes, *, source_sha256: str) -> _SafeArchive:
    """Read bounded regular ZIP members into memory without extracting paths."""

    try:
        raw = io.BytesIO(data)
        preflight_zip_directory(raw, maximum_members=_MEMBER_COUNT_LIMIT)
        archive = zipfile.ZipFile(raw, "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Repair source is not a readable ZIP archive") from error
    with archive:
        infos = tuple(archive.infolist())
        if not infos or len(infos) > _MEMBER_COUNT_LIMIT:
            raise ValueError("Repair archive member count is unsafe")
        aliases: set[str] = set()
        expanded = 0
        for info in infos:
            validate_zip_compression(info)
            folded = _folded_portable_name(info.filename)
            file_type = (info.external_attr >> 16) & 0o170000
            if (
                info.is_dir()
                or info.flag_bits & 1
                or file_type not in {0, stat.S_IFREG}
                or folded in aliases
                or info.file_size > _MEMBER_BYTE_LIMIT
            ):
                raise ValueError("Repair archive contains an unsafe member")
            aliases.add(folded)
            expanded += info.file_size
            if expanded > _EXPANDED_BYTE_LIMIT:
                raise ValueError("Repair archive expands beyond its safe limit")
        files: dict[str, bytes] = {}
        for info in infos:
            with archive.open(info, "r") as member:
                value = member.read(info.file_size + 1)
            if len(value) != info.file_size:
                raise ValueError("Repair archive member size is invalid")
            files[info.filename] = value
    return _SafeArchive(source_sha256, files)


def _diagnostic(error: BaseException, *, file_name: str) -> tuple[RulesetDiagnostic, ...]:
    diagnostics = getattr(error, "diagnostics", ())
    if diagnostics and all(isinstance(item, RulesetDiagnostic) for item in diagnostics):
        return tuple(diagnostics[:16])
    message = str(error).strip() or "Imported content has invalid format data"
    return (
        RulesetDiagnostic(
            code="import_format_invalid",
            phase="import",
            message=message[:500],
            file=file_name,
            guidance="Repair the staged copy, then check it again before publishing.",
        ),
    )


def _plan_identity(files: Mapping[str, bytes]) -> str | None:
    raw = files.get("record.json")
    if raw is None or len(raw) > 64 * 1024:
        return None
    try:
        value = _json_object(raw, label="Campaign Plan record")
    except ValueError:
        return None
    identity = value.get("artifact_id")
    return identity if isinstance(identity, str) else None


def _validate_plan_source(files: Mapping[str, bytes]) -> None:
    """Run the existing complete Plan loader in a disposable library."""

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in sorted(files.items()):
            info = _zip_info(name)
            archive.writestr(info, value)
    identity, closed_files = _read_bundle(io.BytesIO(output.getvalue()))
    with admit_package_archive(io.BytesIO(closed_files[PLAN_SNAPSHOT_FILENAME])) as admitted:
        validate_admitted_package_runtimes(admitted)
    with tempfile.TemporaryDirectory(prefix=".import-repair-inspect-") as temporary:
        root = Path(temporary)
        target = root / "campaign-plans" / identity
        target.mkdir(parents=True)
        for name, value in closed_files.items():
            (target / name).write_bytes(value)
        load_reusable_campaign_plan(root, identity)


def inspect_repair_source(source_path: Path) -> RepairSourceInspection:
    """Confirm archive safety and return bounded format diagnostics without staging a copy."""

    source_hash, data = _read_selected_source(Path(source_path))
    safe = _read_safe_zip(data, source_sha256=source_hash)
    kind: RepairKind = (
        "plan" if Path(source_path).name.lower().endswith(".sylvia-gm-plan") else "package"
    )
    identity: str | None = None
    snapshot = safe.files.get(PLAN_SNAPSHOT_FILENAME) if kind == "plan" else None
    if snapshot is not None:
        _read_safe_zip(snapshot, source_sha256=hashlib.sha256(snapshot).hexdigest())
    try:
        if kind == "package":
            with admit_package_archive(io.BytesIO(data)) as admitted:
                validate_admitted_package_runtimes(admitted)
                identity = admitted.manifests[0].package_id if admitted.manifests else None
        else:
            identity = _plan_identity(safe.files)
            _validate_plan_source(safe.files)
    except (CommandRejected, KeyError, OSError, UnicodeError, ValueError) as error:
        return RepairSourceInspection(
            kind=kind,
            source_sha256=source_hash,
            file_name=Path(source_path).name,
            diagnostics=_diagnostic(error, file_name=Path(source_path).name),
            identity=identity,
        )
    raise ValueError("Selected content is already valid and does not need format repair")


def _write_staged_files(target: Path, files: Mapping[str, bytes]) -> None:
    for name, value in files.items():
        destination = target.joinpath(*name.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)


def stage_repair_copy(
    source_path: Path,
    expected_sha256: str,
    workspace: Path,
    *,
    expected_kind: RepairKind,
    bound_source_sha256: str | None = None,
) -> StagedRepairCopy:
    """Create one editable copy, or retain its edits on a proven idempotent replay."""

    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("Repair source hash is invalid")
    if bound_source_sha256 is not None and bound_source_sha256 != expected_sha256:
        raise ValueError("Repair workspace bound source differs")
    source_hash, data = _read_selected_source(Path(source_path))
    if source_hash != expected_sha256:
        raise ValueError("Selected repair source changed")
    safe = _read_safe_zip(data, source_sha256=source_hash)
    actual_kind: RepairKind = (
        "plan" if Path(source_path).name.lower().endswith(".sylvia-gm-plan") else "package"
    )
    if actual_kind != expected_kind:
        raise ValueError("Selected repair source kind changed")
    if actual_kind == "plan" and PLAN_SNAPSHOT_FILENAME in safe.files:
        _read_safe_zip(
            safe.files[PLAN_SNAPSHOT_FILENAME],
            source_sha256=hashlib.sha256(safe.files[PLAN_SNAPSHOT_FILENAME]).hexdigest(),
        )

    workspace = Path(workspace)
    target = workspace / _DRAFT_NAME
    workspace.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(workspace, workspace)
    if target.exists() or target.is_symlink():
        _reject_reparse_chain(workspace, target)
        if bound_source_sha256 is None:
            raise ValueError("Existing repair workspace has no proven bound source")
        return StagedRepairCopy(actual_kind, source_hash, target)

    temporary = Path(tempfile.mkdtemp(prefix=".repair-stage-", dir=workspace))
    try:
        if actual_kind == "package":
            _write_staged_files(temporary, safe.files)
        else:
            outer = dict(safe.files)
            snapshot = outer.pop(PLAN_SNAPSHOT_FILENAME, None)
            _write_staged_files(temporary, outer)
            if snapshot is not None:
                nested = _read_safe_zip(
                    snapshot, source_sha256=hashlib.sha256(snapshot).hexdigest()
                )
                _write_staged_files(temporary / "snapshot", nested.files)
        os.replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return StagedRepairCopy(actual_kind, source_hash, target)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON object contains a duplicate field")
        value[key] = item
    return value


def _json_object(value: bytes, *, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} JSON is invalid") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be an object")
    return parsed


def _read_draft_files(workspace: Path, root: Path) -> dict[str, bytes]:
    """Read only bounded regular files contained by the fixed repair draft."""

    if root.is_symlink() or not root.is_dir():
        raise ValueError("Repair draft is missing")
    _reject_reparse_chain(workspace, workspace)
    _reject_reparse_chain(workspace, root)
    files: dict[str, bytes] = {}
    aliases: set[str] = set()
    total = 0
    entries = 0
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            entries += 1
            if entries > _MEMBER_COUNT_LIMIT:
                raise ValueError("Repair draft has too many entries")
            _reject_reparse_chain(workspace, current_path / directory)
        for name in names:
            entries += 1
            if entries > _MEMBER_COUNT_LIMIT:
                raise ValueError("Repair draft has too many entries")
            path = current_path / name
            _reject_reparse_chain(workspace, path)
            if path.is_symlink() or not path.is_file():
                raise ValueError("Repair draft contains an unsafe file")
            relative = path.relative_to(root).as_posix()
            folded = _folded_portable_name(relative)
            if folded in aliases:
                raise ValueError("Repair draft paths collide")
            aliases.add(folded)
            value = bytearray()
            with path.open("rb") as source:
                while True:
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    value.extend(chunk)
                    if len(value) > _MEMBER_BYTE_LIMIT:
                        raise ValueError("Repair draft file is oversized")
            total += len(value)
            if total > _EXPANDED_BYTE_LIMIT or len(files) >= _MEMBER_COUNT_LIMIT:
                raise ValueError("Repair draft is oversized")
            files[relative] = bytes(value)
    return files


def _package_drafts(files: Mapping[str, bytes]) -> tuple[NormalizedPackageDraft, ...]:
    manifest_bytes = files.get("manifest.json")
    if manifest_bytes is None or len(manifest_bytes) > _MANIFEST_LIMIT:
        raise ValueError("Repair draft manifest is missing or oversized")
    manifest = _json_object(manifest_bytes, label="Package manifest")
    if set(manifest) != {"format", "packages"}:
        extra = sorted(set(manifest) - {"format", "packages"})
        missing = sorted({"format", "packages"} - set(manifest))
        raise CommandRejected(
            "Package manifest fields are invalid",
            diagnostics=tuple(
                RulesetDiagnostic(
                    code="content_field_invalid",
                    phase="import",
                    file="manifest.json",
                    path=f"$.{field}",
                    message=(
                        "Remove this undeclared field"
                        if field in extra
                        else "Add this required field"
                    ),
                )
                for field in (extra + missing)[:16]
            ),
        )
    raw_format = manifest.get("format")
    packages = manifest.get("packages")
    if not isinstance(raw_format, int) or isinstance(raw_format, bool):
        raise ValueError("Package manifest format is invalid")
    if not isinstance(packages, list) or not packages:
        raise ValueError("Package manifest packages are invalid")
    package_ids = {
        item.get("id")
        for item in packages
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    claimed: set[str] = {"manifest.json"}
    drafts: list[NormalizedPackageDraft] = []
    for record_value in packages:
        if not isinstance(record_value, dict):
            raise ValueError("Package record is invalid")
        package_id = record_value.get("id")
        if not isinstance(package_id, str) or not package_id:
            raise ValueError("Package identity is invalid")
        prefix = f"packages/{package_id}/"
        payloads = {
            name.removeprefix(prefix): value
            for name, value in files.items()
            if name.startswith(prefix)
        }
        claimed.update(f"{prefix}{name}" for name in payloads)
        record = dict(record_value)
        record.pop("files", None)
        record.pop("content_sha256", None)
        dependencies = record.get("dependencies")
        if isinstance(dependencies, list):
            record["dependencies"] = [
                {
                    key: item
                    for key, item in dependency.items()
                    if key != "content_sha256" or dependency.get("id") not in package_ids
                }
                if isinstance(dependency, dict)
                else dependency
                for dependency in dependencies
            ]
        drafts.append(NormalizedPackageDraft(record, payloads, raw_format))
    if set(files) != claimed:
        raise ValueError("Repair draft contains files outside its package records")
    return tuple(drafts)


def _materialize_packages(files: Mapping[str, bytes]) -> MaterializedPackageArchive:
    materialized = materialize_package_archive(_package_drafts(files))
    try:
        with admit_package_archive(materialized.archive) as admitted:
            validate_admitted_package_runtimes(admitted)
        materialized.archive.seek(0)
        return materialized
    except BaseException:
        materialized.close()
        raise


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _plan_result(files: Mapping[str, bytes]) -> MaterializedRepairDraft:
    if any(name not in _PLAN_FILES and not name.startswith("snapshot/") for name in files):
        raise ValueError("Repair Plan contains files outside its fixed draft")
    required = {"record.json", "plan.json", "plan.md"}
    if not set(files) >= required:
        raise ValueError("Repair Plan files are incomplete")
    snapshot_files = {
        name.removeprefix("snapshot/"): value
        for name, value in files.items()
        if name.startswith("snapshot/")
    }
    if not snapshot_files:
        raise ValueError("Repair Plan snapshot is missing")

    packages = _materialize_packages(snapshot_files)
    try:
        snapshot = packages.archive.read()
        packages.archive.seek(0)
        payload = _json_object(files["plan.json"], label="Campaign Plan")
        bindings = payload.get("bindings")
        if not isinstance(bindings, dict):
            raise ValueError("Campaign Plan bindings are invalid")
        ruleset = bindings.get("ruleset")
        setting = bindings.get("campaign_setting")
        if not isinstance(ruleset, dict) or not isinstance(setting, dict):
            raise ValueError("Campaign Plan package bindings are invalid")
        primary = next(item for item in packages.manifests if item.role is PackageRole.PRIMARY)
        settings = campaign_setting_entries(packages.package_lock)
        matched_settings = tuple(
            item
            for item in settings
            if item.package_id == setting.get("id") and item.version == setting.get("version")
        )
        if (ruleset.get("id"), ruleset.get("version")) != primary.identity[:2]:
            raise ValueError("Campaign Plan Ruleset identity does not match its snapshot")
        if len(matched_settings) != 1:
            raise ValueError("Campaign Plan Setting identity does not match its snapshot")
        ruleset["content_sha256"] = primary.content_sha256
        setting["content_sha256"] = matched_settings[0].sha256
        bindings["package_closure_sha256"] = packages.closure_sha256
        markdown = files["plan.md"].decode("utf-8")
        validated = validate_plan_snapshot(snapshot, payload, markdown)

        contribution_bytes = files.get("interview-contribution.json", b"")
        contribution = (
            None
            if not contribution_bytes
            else parse_interview_contribution_bytes(
                contribution_bytes, expected_owner_kind="campaign_plan"
            )
        )
        record = _json_object(files["record.json"], label="Campaign Plan record")
        identity = record.get("artifact_id")
        if (
            record.get("format_id") != REUSABLE_PLAN_FORMAT_ID
            or record.get("format_version") != REUSABLE_PLAN_FORMAT_VERSION
            or not isinstance(identity, str)
            or _PLAN_ID_PATTERN.fullmatch(identity) is None
        ):
            raise ValueError("Campaign Plan record identity or format is invalid")
        if record.get("package_snapshot") != PLAN_SNAPSHOT_FILENAME:
            raise ValueError("Campaign Plan snapshot filename is invalid")
        canonical_plan = canonical_json_bytes(validated.payload)
        canonical_contribution = (
            b"" if contribution is None else canonical_json_bytes(contribution.as_payload())
        )
        record["plan_content_sha256"] = validated.content_sha256
        record["package_snapshot_sha256"] = hashlib.sha256(snapshot).hexdigest()
        record["content_sha256"] = hashlib.sha256(
            canonical_plan + markdown.encode("utf-8") + canonical_contribution + snapshot
        ).hexdigest()
        closed_files = {
            "record.json": canonical_json_bytes(record),
            "plan.json": canonical_plan,
            "plan.md": markdown.encode("utf-8"),
            PLAN_SNAPSHOT_FILENAME: snapshot,
        }
        if contribution is not None:
            closed_files["interview-contribution.json"] = canonical_contribution

        with tempfile.TemporaryDirectory(prefix=".import-repair-check-") as temporary:
            storage = Path(temporary)
            target = storage / "campaign-plans" / identity
            target.mkdir(parents=True)
            for name, value in closed_files.items():
                (target / name).write_bytes(value)
            loaded = load_reusable_campaign_plan(storage, identity)

        archive = cast(
            BinaryIO,
            tempfile.SpooledTemporaryFile(  # noqa: SIM115 - returned stream owns lifetime
                max_size=4 * 1024 * 1024, mode="w+b"
            ),
        )
        try:
            archive.write(snapshot)
            archive.seek(0)
            return MaterializedRepairDraft(
                kind="plan",
                archive=archive,
                closure_sha256=packages.closure_sha256,
                plan=loaded.plan,
                interview_contribution=loaded.interview_contribution,
                setup_id=loaded.setup_id,
            )
        except BaseException:
            archive.close()
            raise
    finally:
        packages.close()


def materialize_repair_draft(
    workspace: Path,
    *,
    expected_kind: RepairKind,
) -> MaterializedRepairDraft:
    """Materialize through the existing admission and runtime validators without publishing."""

    workspace = Path(workspace)
    files = _read_draft_files(workspace, workspace / _DRAFT_NAME)
    if expected_kind == "plan":
        return _plan_result(files)
    if expected_kind != "package":
        raise ValueError("Repair kind is unsupported")
    package = _materialize_packages(files)
    return MaterializedRepairDraft(
        kind="package",
        archive=package.archive,
        closure_sha256=package.closure_sha256,
    )


__all__ = [
    "MaterializedRepairDraft",
    "RepairSourceInspection",
    "StagedRepairCopy",
    "inspect_repair_source",
    "materialize_repair_draft",
    "stage_repair_copy",
]
