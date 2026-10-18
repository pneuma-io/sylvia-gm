"""Validate a Plan against the package bytes it carries, without installing source content."""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import TYPE_CHECKING
from functools import lru_cache

from sylvia_gm.application.plan_network_admission import validate_plan_networks
from sylvia_gm.application.plan_spatial_admission import validate_plan_spatial_scenes
from sylvia_gm.application.plan_state_admission import validate_plan_state
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.campaign_plan import (
    PackageBinding,
    ValidatedCampaignPlan,
    validate_campaign_plan,
)
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    campaign_setting_entries,
    closure_sha256,
    parse_campaign_setting_knowledge_bytes,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner

PLAN_SNAPSHOT_FILENAME = "setting.sylvia-gm-package"
PLAN_SNAPSHOT_BYTE_LIMIT = 64 * 1024 * 1024


def retained_plan_snapshot(owner: AuthorityOwner, payload: object) -> bytes:
    """Capture the Plan's original packages; current source versions do not replace them."""
    bindings = payload.get("bindings") if isinstance(payload, Mapping) else None
    expected = bindings.get("package_closure_sha256") if isinstance(bindings, Mapping) else None
    candidate = next(
        (item for item in owner.package_candidates() if item.closure_sha256 == expected), None,
    )
    if candidate is None:
        from sylvia_gm.application.bundled_packages import (
            bundled_rift_archive,
            bundled_rift_authority,
        )

        if expected != bundled_rift_authority().closure_sha256:
            raise ValueError("Campaign Plan retained packages are unavailable")
        return bundled_rift_archive()
    with io.BytesIO() as exported:
        owner.export_package_candidate(candidate.candidate_id, exported)
        return exported.getvalue()


def validate_plan_snapshot(
    package_snapshot: bytes,
    payload: object,
    readable_markdown: str,
) -> ValidatedCampaignPlan:
    """Require the exact Setting, mechanics and referenced world files inside one saved copy."""
    if not isinstance(package_snapshot, bytes) or not 0 < len(package_snapshot) <= (
        PLAN_SNAPSHOT_BYTE_LIMIT
    ):
        raise ValueError("Campaign Plan package snapshot is missing or too large")
    bindings = payload.get("bindings") if isinstance(payload, Mapping) else None
    setting_binding = bindings.get("campaign_setting") if isinstance(bindings, Mapping) else None
    if not isinstance(setting_binding, Mapping):
        raise ValueError("Campaign Plan Setting binding is missing")
    primary, settings, expected_closure = _admitted_snapshot_facts(package_snapshot)
    matching = [item for item in settings if item[0].package_id == setting_binding.get("id")]
    if len(matching) != 1:
        raise ValueError("Campaign Plan snapshot must contain its exact Setting")
    setting, paths, knowledge_ids = matching[0]
    validated = validate_campaign_plan(
        payload, readable_markdown=readable_markdown,
        expected_ruleset=PackageBinding(*primary.identity),
        expected_setting=PackageBinding(setting.package_id, setting.version, setting.sha256),
        expected_closure_sha256=expected_closure, setting_paths=paths,
        setting_knowledge_ids=knowledge_ids,
    )
    # Cache only successful immutable archive facts. Every Plan and every changed
    # payload still undergoes all structural, state, spatial and network checks.
    validate_plan_spatial_scenes(validated.payload, primary.ui_surfaces)
    validate_plan_networks(validated.payload, primary.ui_surfaces)
    validate_plan_state(validated.payload, primary)
    return validated


@lru_cache(maxsize=4)
def _admitted_snapshot_facts(package_snapshot: bytes):
    """Read exact bounded archive bytes once; retain no handles or temporary files."""
    with admit_package_archive(io.BytesIO(package_snapshot)) as admitted:
        primary = next(item for item in admitted.manifests if item.role is PackageRole.PRIMARY)
        settings = []
        for setting in campaign_setting_entries(admitted.package_lock):
            files = tuple(item for item in admitted.files if item.manifest.package_id == setting.package_id)
            paths = frozenset(item.file.path for item in files)
            knowledge_file = next((item for item in files if item.file.path == CAMPAIGN_SETTING_KNOWLEDGE_PATH), None)
            knowledge_ids = frozenset()
            if knowledge_file is not None:
                knowledge = parse_campaign_setting_knowledge_bytes(b"".join(admitted.iter_file_chunks(knowledge_file)))
                knowledge_ids = frozenset(item.identity for item in knowledge.facts)
            settings.append((setting, paths, knowledge_ids))
        return primary, tuple(settings), closure_sha256(admitted.package_lock)
