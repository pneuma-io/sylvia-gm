"""Read-only Campaign Setting draft validation through publication's admission path."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.infrastructure.package_archive import admit_package_archive

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def check_setting_draft(
    owner: AuthorityOwner,
    workspace: Path,
    *,
    candidate_id: str,
    package_id: str,
    version: str,
    title: str,
) -> SafePayload:
    """Validate one Setting draft without installing it or changing its workspace."""

    with materialize_setting_draft(
        owner,
        workspace,
        candidate_id=candidate_id,
        package_id=package_id,
        version=version,
        title=title,
    ) as materialized:
        with admit_package_archive(materialized.archive) as admitted:
            validate_admitted_package_runtimes(admitted)
        return {
            "closure_sha256": materialized.closure_sha256,
            "checked": ["setting", "contained_ruleset", "package", "references", "runtime"],
            "published": False,
            "state_changed": False,
        }


__all__ = ["check_setting_draft"]
