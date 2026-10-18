"""Copy exact library packages and deliberately select another inherited source."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.package_materialization import NormalizedPackageDraft, snapshot_record
from sylvia_gm.builder._package_materializer import (
    _bounded_json_shape,
    _reject_constant,
    _reject_reparse_chain,
    _unique_object,
)
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import CandidateStatus, campaign_setting_entries
from sylvia_gm.infrastructure.package_archive import admit_package_archive

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def _reachable(drafts, roots, *, exclude=()):
    """Select declared dependencies while leaving graph validation to package admission."""
    reached = {}

    def include(identity):
        if identity in reached or identity in exclude:
            return
        if identity not in drafts:
            raise CommandRejected("Inherited package dependency is unavailable")
        draft = drafts[identity]
        reached[identity] = draft
        for dependency in draft.record["dependencies"]:
            include(dependency["id"])

    for root in roots:
        include(root)
    return reached


def retained_package_drafts(
    owner: AuthorityOwner,
    candidate_id: str,
    *,
    include_setting: bool,
) -> tuple[NormalizedPackageDraft, ...]:
    """Copy a retained source, including its entire mechanics dependency closure."""
    candidate = owner.package_candidate(candidate_id)
    settings = campaign_setting_entries(candidate.package_lock)
    primary = next(
        item for item in candidate.package_lock.entries if item.role is PackageRole.PRIMARY
    )
    drafts = {}
    with io.BytesIO() as exported:
        owner.export_package_candidate(candidate_id, exported)
        exported.seek(0)
        with admit_package_archive(exported) as admitted:
            if admitted.format_version < 3:
                raise CommandRejected("This legacy source needs a current package before editing")
            for manifest in admitted.manifests:
                if not include_setting and any(
                    item.package_id == manifest.package_id for item in settings
                ):
                    continue
                record = snapshot_record(
                    json.loads(manifest.canonical_json),
                    primary=primary,
                    settings=settings,
                )
                files = {
                    item.file.path: b"".join(admitted.iter_file_chunks(item))
                    for item in admitted.files
                    if item.manifest.identity == manifest.identity
                }
                drafts[manifest.package_id] = NormalizedPackageDraft(record, files, 4)
    roots = (
        [primary.package_id, *(item.package_id for item in settings)]
        if include_setting
        else [primary.package_id]
    )
    return tuple(_reachable(drafts, roots).values())


def ruleset_copy(owner: AuthorityOwner, candidate_id: str) -> tuple[NormalizedPackageDraft, ...]:
    """Copy the mechanics closure without bringing a source Setting into another world."""
    return retained_package_drafts(owner, candidate_id, include_setting=False)


def _source_selection(owner, workspace, path, *, ruleset_id, setting_id=None):
    if not path.exists() and not path.is_symlink():
        return None
    try:
        _reject_reparse_chain(workspace, path)
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("Source selection is too large")
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        _bounded_json_shape(value)
        if not isinstance(value, dict) or set(value) != {"candidate_id", "expected_closure_sha256"}:
            raise ValueError("Source selection requires an exact candidate and closure")
        if any(not isinstance(item, str) or not item for item in value.values()):
            raise ValueError("Source selection identities are invalid")
        candidate = owner.package_candidate(value["candidate_id"])
        if candidate.status not in {CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED}:
            raise ValueError("Source candidate is unavailable")
        if candidate.closure_sha256 != value["expected_closure_sha256"]:
            raise CommandRejected(
                "Source selection changed",
                code="builder_source_selection_invalid",
                diagnostics=(
                    RulesetDiagnostic(
                        code="source_selection_changed",
                        phase="source_selection",
                        message="The saved source identity does not match the selected candidate.",
                        file=path.name,
                        path="$.expected_closure_sha256",
                        guidance=(
                            "Inspect library sources and use the candidate's current "
                            "closure_sha256, "
                            "or remove this optional file to keep the source inherited at launch."
                        ),
                    ),
                ),
            )
        primary = next(
            item for item in candidate.package_lock.entries if item.role is PackageRole.PRIMARY
        )
        if primary.package_id != ruleset_id:
            raise ValueError("Source update must retain the logical Ruleset")
        if setting_id is not None:
            settings = campaign_setting_entries(candidate.package_lock)
            if len(settings) != 1 or settings[0].package_id != setting_id:
                raise ValueError("Source update must retain the logical Setting")
        return candidate
    except (OSError, UnicodeError, RecursionError) as error:
        raise CommandRejected("Builder source selection could not be read") from error
    except ValueError as error:
        raise CommandRejected(str(error)) from error


def select_snapshot_sources(
    owner: AuthorityOwner,
    workspace: Path,
    draft_root: Path,
    drafts: tuple[NormalizedPackageDraft, ...],
) -> tuple[NormalizedPackageDraft, ...]:
    """Apply explicitly selected source copies before applying the author's retained edits."""
    primary = next(item for item in drafts if item.record["content_kind"] == "ruleset")
    setting = next(
        (item for item in drafts if item.record["content_kind"] == "campaign_setting"), None
    )
    setting_path = draft_root / "setting-source.json"
    if setting is None and (setting_path.exists() or setting_path.is_symlink()):
        raise CommandRejected("This draft has no inherited Setting to update")
    if setting is not None:
        selected = _source_selection(
            owner,
            workspace,
            setting_path,
            ruleset_id=primary.record["id"],
            setting_id=setting.record["id"],
        )
        if selected is not None:
            drafts = retained_package_drafts(owner, selected.candidate_id, include_setting=True)
            setting = next(
                item for item in drafts if item.record["content_kind"] == "campaign_setting"
            )
    selected = _source_selection(
        owner,
        workspace,
        draft_root / "ruleset-source.json",
        ruleset_id=primary.record["id"],
    )
    if selected is None:
        return drafts
    incoming = ruleset_copy(owner, selected.candidate_id)
    if setting is None:
        return incoming
    original = {item.record["id"]: item for item in drafts}
    world = _reachable(original, [setting.record["id"]], exclude=(primary.record["id"],))
    for item in incoming:
        retained = world.get(item.record["id"])
        if retained is not None and retained != item:
            raise CommandRejected("Incoming rules conflict with a Setting-owned dependency")
        world.pop(item.record["id"], None)
    new_primary = next(item for item in incoming if item.record["content_kind"] == "ruleset")
    world[setting.record["id"]] = replace(
        setting,
        record={
            **setting.record,
            "dependencies": [
                {**dep, "version": new_primary.record["version"]}
                if dep["kind"] == "ruleset_snapshot"
                else dep
                for dep in setting.record["dependencies"]
            ],
        },
    )
    return (*incoming, *world.values())
