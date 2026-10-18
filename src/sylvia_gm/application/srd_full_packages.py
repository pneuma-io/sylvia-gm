"""Explicit development materialization for the retained SRD Full source package.

The Alpha bundle and campaign defaults continue to use SRD Lite. Import this module
explicitly to inspect or qualify Full; it does not register or activate a package.
"""

from __future__ import annotations

import json
from pathlib import Path

from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)

SRD_FULL_PACKAGE_ID = "wotc.srd.5.2.1"
SRD_FULL_REFERENCE_PACKAGE_ID = "wotc.srd.5.2.1.reference"


def _source_root() -> Path:
    """Resolve the development source in a repository checkout."""
    return Path(__file__).resolve().parents[3] / "rulesets" / "dnd-srd-5.2.1"


def srd_full_drafts() -> tuple[NormalizedPackageDraft, NormalizedPackageDraft]:
    """Adapt one checked-in SRD source tree into gameplay and exact-reference packages."""

    root = _source_root()
    reference_record = json.loads(
        (root / "reference-package-record.json").read_text(encoding="utf-8")
    )
    reference_paths = [root / "content" / "reference-content-declarations.json"]
    reference_paths.append(root / "ATTRIBUTION.md")
    reference_paths.append(root / "content" / "corpus-index.json")
    reference_paths.extend(sorted((root / "content" / "corpus").glob("*.json")))
    reference_payload = {
        path.relative_to(root).as_posix(): path.read_bytes().replace(b"\r\n", b"\n")
        for path in reference_paths
    }
    reference = NormalizedPackageDraft(reference_record, reference_payload, 4)
    primary_record = json.loads(
        (root / "package-record.json").read_text(encoding="utf-8")
    )
    primary_record["dependencies"] = [
        {
            "namespace": "rules_reference",
            "kind": "rules_reference",
            "id": SRD_FULL_REFERENCE_PACKAGE_ID,
            "version": str(reference_record["version"]),
        },
    ]
    primary_paths = (
        root / "ATTRIBUTION.md",
        root / "content" / "content-declarations.json",
        root / "content" / "advancement-policy.schema.json",
        root / "prompts" / "gm-operating-guide.md",
        root / "runtime" / "ruleset.yaml",
        root / "support" / "gameplay-resolution.json",
    )
    primary_payload = {
        path.relative_to(root).as_posix(): path.read_bytes().replace(b"\r\n", b"\n")
        for path in primary_paths
    }
    return NormalizedPackageDraft(primary_record, primary_payload, 4), reference


def srd_full_archive() -> bytes:
    """Pre-admit the explicit Full closure through the current generic materializer."""
    with materialize_package_archive(srd_full_drafts()) as materialized:
        return materialized.archive.read()
