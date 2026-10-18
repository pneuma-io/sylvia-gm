"""Validate the Plan's edited snapshot without installing it or saving a Plan."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.campaign_library_selection import read_required_plan_characters
from sylvia_gm.application.campaign_plan_sources import CampaignPlanToolAuthority
from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.plan_spatial_admission import plan_spatial_warnings
from sylvia_gm.application.setting_character_requirements import (
    CHARACTER_REQUIREMENTS_PATH,
    parse_setting_character_requirements,
)
from sylvia_gm.builder._plan_snapshot_materializer import materialize_plan_snapshot_draft
from sylvia_gm.domain.authority import PackageRole, SafePayload
from sylvia_gm.domain.campaign_plan import PackageBinding, parse_starting_party_policy
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import campaign_setting_entries
from sylvia_gm.infrastructure.package_archive import admit_package_archive

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def check_plan_draft(
    owner: AuthorityOwner,
    workspace: Path,
    *,
    candidate_id: str,
    expected_authority: CampaignPlanToolAuthority,
    require_complete_characters: bool = False,
) -> SafePayload:
    """Use publication and party validators against the draft's actual contained package bytes."""

    with (
        materialize_plan_snapshot_draft(
            owner, workspace, candidate_id=candidate_id, expected_authority=expected_authority
        ) as materialized,
        admit_package_archive(materialized.archive) as admitted,
    ):
        validate_admitted_package_runtimes(admitted)
        (setting,) = campaign_setting_entries(admitted.package_lock)
        primary = next(
            entry for entry in admitted.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        manifest = next(item for item in admitted.manifests if item.role is PackageRole.PRIMARY)
        setting_files = {
            item.file.path: item
            for item in admitted.files
            if item.manifest.identity == (setting.package_id, setting.version, setting.sha256)
        }

        def read_setting(path: str) -> str:
            archive_file = setting_files.get(path)
            if archive_file is None or archive_file.file.byte_count > 256 * 1024:
                raise ValueError("Required Character File is unavailable or oversized")
            return b"".join(admitted.iter_file_chunks(archive_file)).decode("utf-8")

        sources = CampaignPlanToolAuthority(
            ruleset=PackageBinding(primary.package_id, primary.version, primary.sha256),
            setting=PackageBinding(setting.package_id, setting.version, setting.sha256),
            closure_sha256=materialized.closure_sha256,
            setting_paths=frozenset(setting_files),
            read_setting=read_setting,
            setting_character_schema=(
                parse_setting_character_requirements(read_setting(CHARACTER_REQUIREMENTS_PATH))
                if CHARACTER_REQUIREMENTS_PATH in setting_files
                else None
            ),
        )
        ready = True
        character_errors: list[dict[str, str]] = []
        character_message = "Required Character Files pass the exact snapshot requirements."
        try:
            read_required_plan_characters(
                sources,
                parse_starting_party_policy(materialized.plan.payload["starting_party"]),
                primary,
                manifest.participant_artifacts,
                require_complete=True,
            )
        except CommandRejected as error:
            if require_complete_characters:
                raise
            ready = False
            character_message = str(error)
            character_errors = [item.as_payload() for item in error.diagnostics[:16]]
        return {
            "published": False,
            "state_changed": False,
            "closure_sha256": materialized.closure_sha256,
            "checked": ["plan", "package", "references", "runtime", "required_characters"],
            "required_characters_ready": ready,
            "character_message": character_message,
            "character_validation_errors": character_errors,
            "authoring_warnings": plan_spatial_warnings(
                materialized.plan.payload, manifest.ui_surfaces,
            ),
        }
