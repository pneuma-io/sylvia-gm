"""Publish authored world files with an owned, optionally customized Ruleset copy."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.package_materialization import (
    MaterializedPackageArchive,
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.builder._package_materializer import _read_payloads, _reject_reparse_chain
from sylvia_gm.builder._package_record_edits import apply_package_record_edits
from sylvia_gm.builder._package_snapshots import ruleset_copy as _ruleset_copy
from sylvia_gm.builder._package_snapshots import select_snapshot_sources
from sylvia_gm.domain.package_library import canonical_json_bytes

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def _world_draft(
    *,
    package_id: str,
    version: str,
    title: str,
    payloads: Mapping[str, bytes],
    ruleset: NormalizedPackageDraft,
) -> NormalizedPackageDraft:
    """Index authored world files without inventing mechanics or completion claims."""
    files = dict(payloads)
    source_sha = hashlib.sha256(
        canonical_json_bytes(
            {path: hashlib.sha256(value).hexdigest() for path, value in sorted(files.items())}
        )
    ).hexdigest()
    declarations_path = "content/content-declarations.json"
    if declarations_path not in files:
        files[declarations_path] = canonical_json_bytes(
            {
                "format_id": "sylvia-gm-package-content-declarations",
                "format_version": 1,
                "scope": {
                    "scope_id": f"{package_id}.world",
                    "scope_kind": "bounded_package",
                    "claim_status": "NOT_COMPLETE",
                    "source_boundary_refs": [],
                },
                "source_authorities": [
                    {
                        "id": f"{package_id}.authored-files",
                        "document_id": f"{package_id}.source-file-index",
                        "version": version,
                        "media_type": "application-json",
                        "content_sha256": source_sha,
                        "license_id": "NOASSERTION",
                    }
                ],
                "inclusions": [
                    {
                        "id": f"{package_id}.world",
                        "kind": "campaign_setting",
                        "family": "world",
                        "disposition": "included_nonmechanical",
                        "classifications": ["authored_content"],
                        "sources": [],
                        "links": {
                            "artifacts": sorted(files),
                            "capabilities": [],
                            "mechanics": [],
                            "procedures": [],
                            "prompt_fragments": [],
                            "schemas": [],
                            "state_namespaces": [],
                            "tools": [],
                            "ui_surfaces": [],
                        },
                        "evidence": {"tests": []},
                    }
                ],
                "exclusions": [],
            }
        )
    return NormalizedPackageDraft(
        {
            "id": package_id,
            "version": version,
            "role": "dependency",
            "content_kind": "campaign_setting",
            "metadata": {"label": title},
            "provenance": {"publisher": "Local Builder", "source_sha256": source_sha},
            "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
            "minimum_model_capability_tier": ruleset.record["minimum_model_capability_tier"],
            "dependencies": [
                {
                    "namespace": "ruleset_snapshot",
                    "kind": "ruleset_snapshot",
                    "id": ruleset.record["id"],
                    "version": ruleset.record["version"],
                }
            ],
            "capabilities": [],
            "tools": [],
            "prompt_fragments": [],
            "ui_surfaces": [],
            "participant_artifacts": [],
            "content_declarations": declarations_path,
        },
        files,
        4,
    )


def materialize_setting_draft(
    owner: AuthorityOwner,
    workspace: Path,
    *,
    candidate_id: str,
    package_id: str,
    version: str,
    title: str,
) -> MaterializedPackageArchive:
    """Admit one complete world and its contained rules before anything enters the picker."""
    workspace = workspace.resolve()
    root = workspace / "setting-draft"
    _reject_reparse_chain(workspace, root / "files")
    files = _read_payloads(root / "files")
    drafts = _ruleset_copy(owner, candidate_id)
    drafts = select_snapshot_sources(owner, workspace, root, drafts)
    ruleset = drafts[0]
    replacements = root / "ruleset-files"
    if replacements.exists():
        _reject_reparse_chain(workspace, replacements)
        changed = _read_payloads(replacements)
        ruleset = replace(ruleset, payloads={**ruleset.payloads, **changed})
        drafts = (ruleset, *drafts[1:])
    drafts = apply_package_record_edits(workspace, root, drafts)
    ruleset = drafts[0]
    world = _world_draft(
        package_id=package_id,
        version=version,
        title=title,
        payloads=files,
        ruleset=ruleset,
    )
    return materialize_package_archive((*drafts, world))
