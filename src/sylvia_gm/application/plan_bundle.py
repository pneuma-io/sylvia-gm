"""Portable reusable Plans containing their complete Setting and Ruleset snapshot."""

from __future__ import annotations

import hashlib
import io
import re
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO

from sylvia_gm.application.plan_snapshot import PLAN_SNAPSHOT_BYTE_LIMIT, PLAN_SNAPSHOT_FILENAME
from sylvia_gm.application.reusable_library_deletion import (
    validate_owned_files,
    validated_library_entry,
)
from sylvia_gm.application.reusable_plans import (
    _LIBRARY_WRITE_LOCK,
    ReusableCampaignPlan,
    load_reusable_campaign_plan,
)
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_BYTE_LIMIT
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.package_library import parse_canonical_json_bytes
from sylvia_gm.infrastructure.zip_bounds import preflight_zip_directory, validate_zip_compression

_MEMBER_LIMITS = {
    "record.json": 64 * 1024,
    "plan.json": CAMPAIGN_PLAN_BYTE_LIMIT,
    "plan.md": CAMPAIGN_PLAN_BYTE_LIMIT,
    "interview-contribution.json": 128 * 1024,
    PLAN_SNAPSHOT_FILENAME: PLAN_SNAPSHOT_BYTE_LIMIT,
}
PLAN_BUNDLE_BYTE_LIMIT = sum(_MEMBER_LIMITS.values()) + 64 * 1024
_REQUIRED_MEMBERS = frozenset(_MEMBER_LIMITS) - {"interview-contribution.json"}


def export_plan_bundle(
    storage_root: Path,
    artifact_id: str,
    *,
    expected_content_sha256: str,
    target: BinaryIO,
) -> dict[str, object]:
    """Export only validated reusable content, excluding Builder chat and campaign history."""
    with _LIBRARY_WRITE_LOCK:
        root = validated_library_entry(
            storage_root,
            library_name="campaign-plans",
            artifact_id=artifact_id,
        )
        if root is None:
            raise ValueError("The selected Campaign Plan is unavailable")
        validate_owned_files(root, frozenset(_MEMBER_LIMITS))
        plan = load_reusable_campaign_plan(storage_root, artifact_id)
        if plan.content_sha256 != expected_content_sha256:
            raise ValueError("The selected Campaign Plan changed; refresh its library entry")
        if plan.package_snapshot is None:
            raise ValueError("The Campaign Plan needs its retained package snapshot before export")
        with io.BytesIO() as output:
            with zipfile.ZipFile(output, "w") as bundle:
                for name in sorted(_MEMBER_LIMITS):
                    path = root / name
                    if path.is_file():
                        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        info.create_system = 3
                        info.external_attr = 0o100644 << 16
                        bundle.writestr(info, path.read_bytes())
            data = output.getvalue()
        target.write(data)
    return {
        "artifact_id": plan.artifact_id,
        "content_sha256": plan.content_sha256,
        "byte_count": len(data),
        "archive_sha256": hashlib.sha256(data).hexdigest(),
    }


def _read_bundle(source: BinaryIO) -> tuple[str, dict[str, bytes]]:
    """Read a closed set of bounded regular files without extracting archive paths."""
    data = source.read(PLAN_BUNDLE_BYTE_LIMIT + 1)
    if len(data) > PLAN_BUNDLE_BYTE_LIMIT:
        raise ValueError("Campaign Plan archive is too large")
    raw = io.BytesIO(data)
    preflight_zip_directory(raw, maximum_members=len(_MEMBER_LIMITS))
    with zipfile.ZipFile(raw) as bundle:
        infos = bundle.infolist()
        names = [item.filename for item in infos]
        if (
            len(set(names)) != len(names)
            or not set(names) >= _REQUIRED_MEMBERS
            or not set(names) <= set(_MEMBER_LIMITS)
        ):
            raise ValueError("Campaign Plan archive members are invalid")
        files = {}
        for info in infos:
            validate_zip_compression(info)
            maximum = _MEMBER_LIMITS[info.filename]
            file_type = (info.external_attr >> 16) & 0o170000
            if (
                info.is_dir()
                or info.flag_bits & 1
                or info.file_size > maximum
                or file_type not in {0, 0o100000}
            ):
                raise ValueError("Campaign Plan archive member is unsafe or too large")
            with bundle.open(info) as member:
                value = member.read(maximum + 1)
            if len(value) != info.file_size or len(value) > maximum:
                raise ValueError("Campaign Plan archive member size is invalid")
            files[info.filename] = value
    record = parse_canonical_json_bytes(files["record.json"])
    validate_content_shape("portable_plan_bundle_record", record, file="record.json")
    identity = record.get("artifact_id") if isinstance(record, Mapping) else None
    if (
        not isinstance(identity, str)
        or re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", identity) is None
        or record.get("format_version") != 3
    ):
        raise ValueError("Campaign Plan archive record is invalid")
    return identity, files


def import_plan_bundle(
    storage_root: Path,
    source: BinaryIO,
    *,
    install_snapshot: Callable[[bytes, str], None],
) -> ReusableCampaignPlan:
    """Validate the whole Plan before importing its packages and exposing its library entry."""
    artifact_id, files = _read_bundle(source)
    storage_root.mkdir(parents=True, exist_ok=True)
    with (
        _LIBRARY_WRITE_LOCK,
        tempfile.TemporaryDirectory(prefix=".plan-import-", dir=storage_root) as temporary,
    ):
        staged_storage = Path(temporary)
        staged = staged_storage / "campaign-plans" / artifact_id
        staged.mkdir(parents=True)
        for name, value in files.items():
            (staged / name).write_bytes(value)
        validated = load_reusable_campaign_plan(staged_storage, artifact_id)
        if validated.package_snapshot is None:
            raise ValueError("Campaign Plan archive has no package snapshot")
        existing = validated_library_entry(
            storage_root,
            library_name="campaign-plans",
            artifact_id=artifact_id,
        )
        if existing is not None:
            current = load_reusable_campaign_plan(storage_root, artifact_id)
            if current.content_sha256 != validated.content_sha256:
                raise ValueError("Campaign Plan archive conflicts with an existing entry")
        install_snapshot(
            validated.package_snapshot,
            str(validated.plan.payload["bindings"]["package_closure_sha256"]),
        )
        if existing is None:
            destination = storage_root / "campaign-plans" / artifact_id
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged.rename(destination)
        return load_reusable_campaign_plan(storage_root, artifact_id)
