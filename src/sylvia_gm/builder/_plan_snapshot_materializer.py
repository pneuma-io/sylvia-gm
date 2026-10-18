"""Materialize one Plan-owned copy of its inherited Setting and Ruleset closure."""

from __future__ import annotations

import copy
import io
import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Never, cast

from sylvia_gm.application.campaign_plan_sources import CampaignPlanToolAuthority
from sylvia_gm.application.package_materialization import (
    MaterializedPackageArchive,
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.builder._package_materializer import _read_payloads, _reject_reparse_chain
from sylvia_gm.builder._package_record_edits import apply_package_record_edits
from sylvia_gm.builder._package_snapshots import retained_package_drafts, select_snapshot_sources
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.campaign_plan import (
    CAMPAIGN_PLAN_BYTE_LIMIT,
    CampaignPlanFieldsError,
    ValidatedCampaignPlan,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    campaign_setting_entries,
    closure_sha256,
    validate_portable_path,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner

_DRAFT_DIRECTORY = "campaign-plan-draft"
_REMOVED_FILES = "removed-files.json"
_REMOVAL_LIMIT = 512 * 1024
_REMOVAL_COUNT_LIMIT = 512
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class MaterializedPlanSnapshotDraft:
    """A validated Plan and the exact package archive it owns while this object is open."""

    archive: BinaryIO
    plan: ValidatedCampaignPlan
    closure_sha256: str

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> MaterializedPlanSnapshotDraft:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _reject_constant(_value: str) -> Never:
    raise ValueError("Campaign Plan JSON constants must be finite")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Campaign Plan JSON contains a duplicate key")
        result[key] = value
    return result


def _bounded_regular_bytes(workspace: Path, path: Path, label: str, maximum: int) -> bytes:
    """Read one fixed regular file after rejecting links anywhere in its workspace chain."""

    try:
        _reject_reparse_chain(workspace, path)
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attributes & _REPARSE_POINT or not path.is_file():
            raise ValueError(f"{label} is unavailable")
        with path.open("rb") as source:
            value = source.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError(f"{label} is too large")
        return value
    except CommandRejected:
        raise
    except (OSError, ValueError) as error:
        raise CommandRejected(f"{label} is invalid") from error


def _read_plan(workspace: Path) -> tuple[object, str]:
    root = workspace / _DRAFT_DIRECTORY
    structured = _bounded_regular_bytes(
        workspace, root / "plan.json", "Campaign Plan structured artifact", CAMPAIGN_PLAN_BYTE_LIMIT
    )
    readable = _bounded_regular_bytes(
        workspace, root / "plan.md", "Campaign Plan readable rendering", CAMPAIGN_PLAN_BYTE_LIMIT
    )
    try:
        payload = json.loads(
            structured.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        markdown = readable.decode("utf-8")
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise CommandRejected("Campaign Plan draft is invalid") from error
    return payload, markdown


def _require_original_bindings(payload: object, expected: CampaignPlanToolAuthority) -> None:
    """Reject attempts to use Plan JSON to select a different inherited source closure."""

    bindings = payload.get("bindings") if isinstance(payload, Mapping) else None
    binding_fields = {
        "ruleset",
        "campaign_setting",
        "package_closure_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != binding_fields:
        raise CampaignPlanFieldsError(
            bindings if isinstance(bindings, Mapping) else None,
            binding_fields,
            "Campaign Plan bindings",
            path="$.bindings",
        )
    if (
        bindings.get("ruleset") != expected.ruleset.as_payload()
        or bindings.get("campaign_setting") != expected.setting.as_payload()
        or bindings.get("package_closure_sha256") != expected.closure_sha256
    ):
        raise CommandRejected("Campaign Plan bindings no longer match its launch sources")


def _optional_payloads(workspace: Path, path: Path) -> dict[str, bytes]:
    if not path.exists() and not path.is_symlink():
        return {}
    _reject_reparse_chain(workspace, path)
    try:
        if path.is_dir() and next(path.iterdir(), None) is None:
            return {}
    except OSError as error:
        raise CommandRejected("Campaign Plan inherited-file edits are unavailable") from error
    return _read_payloads(path)


def _removed_paths(workspace: Path, path: Path) -> tuple[str, ...]:
    if not path.exists() and not path.is_symlink():
        return ()
    raw = _bounded_regular_bytes(
        workspace, path, "Campaign Plan removed-files list", _REMOVAL_LIMIT
    )
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise CommandRejected("Campaign Plan removed-files list is invalid") from error
    if (
        not isinstance(value, list)
        or len(value) > _REMOVAL_COUNT_LIMIT
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        raise CommandRejected("Campaign Plan removed-files list is invalid")
    result = cast(list[str], value)
    for item in result:
        prefix, separator, relative = item.partition("/")
        if prefix not in {"setting", "ruleset"} or not separator:
            raise CommandRejected("Campaign Plan removed-files paths require a package prefix")
        validate_portable_path(relative)
    if len({item.casefold() for item in result}) != len(result):
        raise CommandRejected("Campaign Plan removed-files paths collide")
    return tuple(result)


def _apply_plan_file_edits(drafts, setting_changes, ruleset_changes, removals):
    """Apply explicit file changes to the selected owned world and rules."""
    changes = {"campaign_setting": setting_changes, "ruleset": ruleset_changes}
    prefixes = {"campaign_setting": "setting", "ruleset": "ruleset"}
    result = []
    for draft in drafts:
        kind = draft.record["content_kind"]
        if kind not in changes:
            result.append(draft)
            continue
        payloads = dict(draft.payloads)
        removed = {path.split("/", 1)[1] for path in removals
                   if path.startswith(prefixes[kind] + "/")}
        changed = changes[kind]
        if removed - payloads.keys():
            raise CommandRejected("Campaign Plan cannot remove an unknown inherited file")
        if {path.casefold() for path in removed} & {path.casefold() for path in changed}:
            raise CommandRejected("Campaign Plan file cannot be replaced and removed")
        for path in removed:
            del payloads[path]
        payloads.update(changed)
        result.append(NormalizedPackageDraft(draft.record, payloads, 4))
    return tuple(result)


def _rebind_plan_to_snapshot(snapshot: bytes, payload: object) -> object:
    """Replace only derived bindings after the raw draft selected its original authority."""

    if not isinstance(payload, dict):
        raise CommandRejected("Campaign Plan bindings are invalid")
    rebound = copy.deepcopy(payload)
    bindings = rebound.get("bindings")
    if not isinstance(bindings, dict):
        raise CommandRejected("Campaign Plan bindings are invalid")
    with admit_package_archive(io.BytesIO(snapshot)) as admitted:
        primary = next(
            (item for item in admitted.manifests if item.role is PackageRole.PRIMARY), None
        )
        settings = campaign_setting_entries(admitted.package_lock)
        if primary is None or len(settings) != 1:
            raise CommandRejected("Materialized Plan packages are invalid")
        setting = settings[0]
        bindings["ruleset"] = {
            "id": primary.package_id,
            "version": primary.version,
            "content_sha256": primary.content_sha256,
        }
        bindings["campaign_setting"] = {
            "id": setting.package_id,
            "version": setting.version,
            "content_sha256": setting.sha256,
        }
        bindings["package_closure_sha256"] = closure_sha256(admitted.package_lock)
    return rebound


def materialize_plan_snapshot_draft(
    owner: AuthorityOwner,
    workspace: Path,
    *,
    candidate_id: str,
    expected_authority: CampaignPlanToolAuthority,
) -> MaterializedPlanSnapshotDraft:
    """Validate a Plan and return its exact original or newly owned package snapshot."""

    workspace = workspace.resolve()
    payload, readable = _read_plan(workspace)
    _require_original_bindings(payload, expected_authority)
    candidate = owner.package_candidate(candidate_id)
    if candidate.closure_sha256 != expected_authority.closure_sha256:
        raise CommandRejected("Plan launch closure no longer matches its authority")

    root = workspace / _DRAFT_DIRECTORY
    setting_changes = _optional_payloads(workspace, root / "setting-files")
    ruleset_changes = _optional_payloads(workspace, root / "ruleset-files")
    removals = _removed_paths(workspace, root / _REMOVED_FILES)
    has_package_edits = any(
        (root / name).exists() or (root / name).is_symlink()
        for name in ("package-records", "ruleset-source.json", "setting-source.json")
    )
    with io.BytesIO() as exported:
        owner.export_package_candidate(candidate_id, exported)
        exported.seek(0)
        if not setting_changes and not ruleset_changes and not removals and not has_package_edits:
            snapshot = exported.getvalue()
            plan = validate_plan_snapshot(snapshot, payload, readable)
            archive = io.BytesIO(snapshot)
            return MaterializedPlanSnapshotDraft(archive, plan, expected_authority.closure_sha256)
    drafts = retained_package_drafts(owner, candidate_id, include_setting=True)
    drafts = select_snapshot_sources(owner, workspace, root, drafts)
    drafts = _apply_plan_file_edits(drafts, setting_changes, ruleset_changes, removals)
    drafts = apply_package_record_edits(workspace, root, drafts)
    materialized: MaterializedPackageArchive | None = None
    try:
        materialized = materialize_package_archive(drafts)
        snapshot = materialized.archive.read()
        materialized.archive.seek(0)
        rebound_payload = _rebind_plan_to_snapshot(snapshot, payload)
        plan = validate_plan_snapshot(snapshot, rebound_payload, readable)
        return MaterializedPlanSnapshotDraft(
            materialized.archive, plan, materialized.closure_sha256
        )
    except BaseException:
        if materialized is not None:
            materialized.close()
        raise


__all__ = ["MaterializedPlanSnapshotDraft", "materialize_plan_snapshot_draft"]
