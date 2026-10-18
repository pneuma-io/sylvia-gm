"""Deterministic archives for first-party declarative packages bundled with Sylvia GM."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.campaign_plan import (
    PackageBinding,
    ValidatedCampaignPlan,
    validate_campaign_plan,
)
from sylvia_gm.domain.interview_contribution import (
    InterviewContribution,
    parse_interview_contribution_bytes,
)
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
    parse_campaign_setting_knowledge_bytes,
)

_SRD_PACKAGE_ID = "sylvia.srd-lite.bg3"
_SRD_REFERENCE_PACKAGE_ID = "pneuma.srd.5.2.1.reference"
_SEVERED_LANDS_PACKAGE_ID = "pneuma.severed-lands"
_SRD_SHARED_NOTICE_FILES = (
    "ATTRIBUTION.md",
    "LICENSE",
    "COPYRIGHT",
    "LICENSING-STATUS.md",
    "SYLVIA-ADDITIONAL-PERMISSIONS.md",
    "MIT-PREVIOUS-GRANT.txt",
)
_TEXT_PACKAGE_SUFFIXES = frozenset({".json", ".md", ".yaml", ".yml"})


@dataclass(frozen=True)
class BundledRiftAuthority:
    """Exact package identities and Setting paths accepted by Rift Campaign Plans."""

    ruleset: PackageBinding
    setting: PackageBinding
    closure_sha256: str
    setting_paths: frozenset[str]
    setting_knowledge_ids: frozenset[str]


def _bundled_srd_root() -> Path:
    """Resolve the SRD package in a source checkout or a PyInstaller sidecar."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str):
        candidate = Path(frozen_root) / "rulesets" / "srd-lite"
        if candidate.is_dir():
            return candidate
    candidate = Path(__file__).resolve().parents[3] / "rulesets" / "srd-lite"
    if not candidate.is_dir():
        raise RuntimeError("The bundled D&D SRD package is unavailable")
    return candidate


def _bundled_setting_root() -> Path:
    """Resolve the first-party Severed Lands package in source and frozen builds."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str):
        candidate = Path(frozen_root) / "campaign-settings" / "severed-lands"
        if candidate.is_dir():
            return candidate
    candidate = Path(__file__).resolve().parents[3] / "campaign-settings" / "severed-lands"
    if not candidate.is_dir():
        raise RuntimeError("The bundled Severed Lands package is unavailable")
    return candidate


def _bundled_rift_plan_root() -> Path:
    """Resolve the checked-in structured Rift Campaign Plan."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str):
        candidate = Path(frozen_root) / "campaign-plans" / "the-rift-awakening"
        if candidate.is_dir():
            return candidate
    candidate = Path(__file__).resolve().parents[3] / "campaign-plans" / "the-rift-awakening"
    if not candidate.is_dir():
        raise RuntimeError("The bundled Rift Campaign Plan is unavailable")
    return candidate


def _package_payload(root: Path) -> dict[str, bytes]:
    """Read one checked-in package with checkout-independent text bytes."""

    source_names = (
        "campaigns",
        "characters",
        "companions",
        "content",
        "dm-tools",
        "factions",
        "locations",
        "lore",
        "npcs",
        "prompts",
        "runtime",
        "support",
        "provenance",
    )
    paths = [
        path
        for source_name in source_names
        for source_root in (root / source_name,)
        if source_root.is_dir()
        for path in source_root.rglob("*")
        if path.is_file()
    ]
    paths.extend(
        path for path in root.iterdir() if path.is_file() and path.name != "package-record.json"
    )
    payload: dict[str, bytes] = {}
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        value = path.read_bytes()
        if path.suffix.casefold() in _TEXT_PACKAGE_SUFFIXES:
            value = value.replace(b"\r\n", b"\n")
        payload[path.relative_to(root).as_posix()] = value
    return payload


def _package_draft(
    root: Path, *, dependencies: list[dict[str, str]] | None = None
) -> NormalizedPackageDraft:
    """Adapt one trusted checked-in source tree to the shared normalized draft interface."""

    draft = json.loads((root / "package-record.json").read_text(encoding="utf-8"))
    if dependencies is not None:
        draft["dependencies"] = dependencies
    return NormalizedPackageDraft(
        draft,
        _package_payload(root),
        4 if "content_kind" in draft else 3,
    )


def _srd_drafts() -> tuple[NormalizedPackageDraft, NormalizedPackageDraft]:
    """Adapt one checked-in SRD source tree into gameplay and exact-reference packages."""

    root = _bundled_srd_root()
    reference_record = json.loads(
        (root / "reference-package-record.json").read_text(encoding="utf-8")
    )
    reference_paths = [root / "content" / "reference-content-declarations.json"]
    reference_paths.extend(root / name for name in _SRD_SHARED_NOTICE_FILES)
    reference_paths.append(root / "content" / "corpus-index.json")
    reference_paths.extend(sorted((root / "content" / "corpus").glob("*.json")))
    reference_payload = {
        path.relative_to(root).as_posix(): (
            path.read_bytes()
            if path.name == "MIT-PREVIOUS-GRANT.txt"
            else path.read_bytes().replace(b"\r\n", b"\n")
        )
        for path in reference_paths
    }
    reference = NormalizedPackageDraft(reference_record, reference_payload, 4)
    primary_record = json.loads((root / "package-record.json").read_text(encoding="utf-8"))
    primary_record["dependencies"] = [
        {
            "namespace": "rules_reference",
            "kind": "rules_reference",
            "id": _SRD_REFERENCE_PACKAGE_ID,
            "version": str(reference_record["version"]),
        },
    ]
    # Lite is the active launch package; preserve all authored payloads. Original
    # source corpus bytes stay in the non-executable exact-reference dependency.
    primary_payload = _package_payload(root)
    for source_path in reference_payload:
        if source_path not in _SRD_SHARED_NOTICE_FILES:
            primary_payload.pop(source_path, None)
    primary_payload.pop("reference-package-record.json", None)
    primary_payload.pop("source-payload-manifest.json", None)
    return NormalizedPackageDraft(primary_record, primary_payload, 4), reference


def _archive(drafts: tuple[NormalizedPackageDraft, ...]) -> bytes:
    """Reuse bundled bytes only while every source record and payload byte agrees."""

    key = tuple(
        (
            draft.format_version,
            canonical_json_bytes(draft.record),
            tuple(sorted(draft.payloads.items())),
        )
        for draft in drafts
    )
    return _exact_bundled_archive(key)


@lru_cache(maxsize=4)
def _exact_bundled_archive(key: tuple) -> bytes:
    """Bound retained immutable inputs; each changed draft still gets full admission."""

    drafts = tuple(
        NormalizedPackageDraft(json.loads(record), dict(payloads), version)
        for version, record, payloads in key
    )
    with materialize_package_archive(drafts) as materialized:
        return materialized.archive.read()


def _rift_drafts() -> tuple[
    NormalizedPackageDraft,
    NormalizedPackageDraft,
    NormalizedPackageDraft,
]:
    """Return the trusted Rift Setting with its contained Ruleset snapshot."""

    ruleset, reference = _srd_drafts()
    setting = _package_draft(
        _bundled_setting_root(),
        dependencies=[
            {
                "namespace": "ruleset_snapshot",
                "kind": "ruleset_snapshot",
                "id": _SRD_PACKAGE_ID,
                "version": str(ruleset.record["version"]),
            }
        ],
    )
    setting_payloads = dict(setting.payloads)
    party_template_path = "campaigns/the-rift-awakening/party-template.json"
    party_template = json.loads(
        (Path(__file__).resolve().parents[1] / "campaign" / "the-rift-awakening.json").read_text(
            encoding="utf-8"
        )
    )
    setting_payloads[party_template_path] = canonical_json_bytes(party_template)
    declarations = json.loads(setting_payloads["content/content-declarations.json"])
    declarations["inclusions"].append(
        {
            "id": "severed-lands-authored-setting-03",
            "kind": "campaign_setting",
            "family": "campaign-setting",
            "disposition": "included_nonmechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": [party_template_path],
                "capabilities": [],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["severed-lands-package-composition"]},
        }
    )
    setting_payloads["content/content-declarations.json"] = canonical_json_bytes(declarations)
    setting = NormalizedPackageDraft(setting.record, setting_payloads, 4)
    return ruleset, reference, setting


def bundled_srd_archive() -> bytes:
    """Materialize the checked-in SRD package as one deterministic Format-4 archive."""

    return _archive(_srd_drafts())


def bundled_srd_version() -> str:
    """Return the bundled SRD's immutable semantic version without materializing its archive."""

    record = json.loads((_bundled_srd_root() / "package-record.json").read_text(encoding="utf-8"))
    version = record.get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("The bundled D&D SRD package version is unavailable")
    return version


def bundled_rift_archive() -> bytes:
    """Return Severed Lands with its exact contained D&D SRD snapshot."""

    return _archive(_rift_drafts())


@lru_cache(maxsize=1)
def bundled_rift_authority() -> BundledRiftAuthority:
    """Reuse immutable bundled identities while each caller validates its own saved content."""

    ruleset_draft, reference_draft, setting_draft = _rift_drafts()
    setting_files = setting_draft.payloads
    setting_knowledge = parse_campaign_setting_knowledge_bytes(
        setting_files[CAMPAIGN_SETTING_KNOWLEDGE_PATH]
    )
    with materialize_package_archive(
        (ruleset_draft, reference_draft, setting_draft)
    ) as materialized:
        ruleset = next(
            item for item in materialized.manifests if item.package_id == _SRD_PACKAGE_ID
        )
        setting = next(
            item for item in materialized.manifests if item.package_id == _SEVERED_LANDS_PACKAGE_ID
        )
        return BundledRiftAuthority(
            ruleset=PackageBinding(*ruleset.identity),
            setting=PackageBinding(*setting.identity),
            closure_sha256=materialized.closure_sha256,
            setting_paths=frozenset(setting_files),
            setting_knowledge_ids=frozenset(item.identity for item in setting_knowledge.facts),
        )


def bundled_rift_participant_declaration() -> ParticipantArtifactDeclaration:
    """Return the exact participant schema materialized into the bundled Rift Ruleset."""

    with materialize_package_archive(_rift_drafts()) as materialized:
        ruleset = next(
            item for item in materialized.manifests if item.package_id == _SRD_PACKAGE_ID
        )
        declarations = tuple(
            item for item in ruleset.participant_artifacts if item.kind == "player_character"
        )
        if len(declarations) != 1:
            raise RuntimeError("The bundled D&D participant schema is unavailable")
        return declarations[0]


def bundled_rift_campaign_plan() -> ValidatedCampaignPlan:
    """Load and validate the checked-in structured Rift plan against bundled packages."""

    authority = bundled_rift_authority()
    root = _bundled_rift_plan_root()
    return validate_campaign_plan(
        json.loads((root / "plan.json").read_text(encoding="utf-8")),
        readable_markdown=(root / "plan.md").read_text(encoding="utf-8"),
        expected_ruleset=authority.ruleset,
        expected_setting=authority.setting,
        expected_closure_sha256=authority.closure_sha256,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )


def bundled_rift_interview_contribution() -> InterviewContribution | None:
    """Load the Rift Plan's optional immutable Campaign Interview questions."""

    path = _bundled_rift_plan_root() / "interview-contribution.json"
    if not path.exists():
        return None
    return parse_interview_contribution_bytes(
        path.read_bytes(),
        expected_owner_kind="campaign_plan",
    )


def bundled_campaign_content_catalog() -> tuple[dict[str, object], ...]:
    """Return caller-owned choices from the process-static bundled content."""

    return deepcopy(_bundled_campaign_content_catalog())


@lru_cache(maxsize=1)
def _bundled_campaign_content_catalog() -> tuple[dict[str, object], ...]:
    """Validate bundled records once; installed or authored library content is not cached here."""

    from sylvia_gm.application.bundled_campaign_plans import bundled_plan_catalog

    with materialize_package_archive(_srd_drafts()) as materialized:
        ruleset_record = next(
            item for item in materialized.manifests if item.package_id == _SRD_PACKAGE_ID
        )
    ruleset_draft, reference_draft, setting_draft = _rift_drafts()
    with materialize_package_archive(
        (ruleset_draft, reference_draft, setting_draft)
    ) as materialized:
        setting_record = next(
            item for item in materialized.manifests if item.package_id == _SEVERED_LANDS_PACKAGE_ID
        )
    return (
        {
            "kind": "ruleset",
            "id": ruleset_record.package_id,
            "version": ruleset_record.version,
            "title": "D&D 5e SRD 5.2.1",
        },
        {
            "kind": "campaign_setting",
            "id": setting_record.package_id,
            "version": setting_record.version,
            "title": "Severed Lands",
        },
        *bundled_plan_catalog(),
    )


def bundled_rift_source_catalog(query: str = "") -> tuple[str, ...]:
    """List bounded virtual source paths available to Campaign Plan Builder."""

    authority = bundled_rift_authority()
    values = (
        "campaign-plan/example/plan.json",
        "campaign-plan/example/plan.md",
        "ruleset/identity.json",
    ) + tuple(f"setting/{path}" for path in sorted(authority.setting_paths))
    needle = query.strip().casefold()
    return tuple(path for path in values if not needle or needle in path.casefold())[:512]


def read_bundled_rift_source(path: str) -> str:
    """Read one package-relative Builder source without exposing a machine path."""

    if path in {"campaign-plan/example/plan.json", "campaign-plan/example/plan.md"}:
        name = path.rsplit("/", 1)[-1]
        return (_bundled_rift_plan_root() / name).read_text(encoding="utf-8")
    if path == "ruleset/identity.json":
        record = _srd_drafts()[0].record
        authority = bundled_rift_authority()
        return json.dumps(
            {
                "binding": authority.ruleset.as_payload(),
                "campaign_setting": authority.setting.as_payload(),
                "package_closure_sha256": authority.closure_sha256,
                "capabilities": record["capabilities"],
                "tools": [tool["name"] for tool in cast(list[dict[str, object]], record["tools"])],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    prefix = "setting/"
    if not path.startswith(prefix):
        raise ValueError("Campaign source path is unavailable")
    relative = path.removeprefix(prefix)
    authority = bundled_rift_authority()
    if relative not in authority.setting_paths:
        raise ValueError("Campaign source path is unavailable")
    value = _rift_drafts()[2].payloads[relative].decode("utf-8")
    if len(value) > 64 * 1024:
        raise ValueError("Campaign source is too large for one Builder read")
    return value
