"""Selected built-in adventures and their exact retained Setting snapshots."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from sylvia_gm.application.bundled_packages import _rift_drafts
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.reusable_plans import (
    CampaignPlanValidator,
    ReusableCampaignPlan,
    load_reusable_campaign_plan,
)
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.campaign_plan import PackageBinding, ValidatedCampaignPlan, parse_starting_party_policy
from sylvia_gm.domain.interview_contribution import parse_interview_contribution_bytes
from sylvia_gm.domain.package_library import canonical_json_bytes


@dataclass(frozen=True)
class PlanSources:
    """One exact package snapshot shared by Plans with the same authored additions."""

    snapshot: bytes
    bindings: dict
    validated_plan: ValidatedCampaignPlan | None = None


@lru_cache(maxsize=8)
def plan_sources(additions: tuple[tuple[str, bytes], ...] = ()) -> PlanSources:
    """Keep Plan-owned Character Files in its contained Setting, never the base source."""
    rules, reference, setting = _rift_drafts()
    if additions:
        payloads = dict(setting.payloads)
        declarations = json.loads(payloads["content/content-declarations.json"])
        inclusion = {
            "id": "severed-lands-plan-characters",
            "kind": "campaign_setting",
            "family": "campaign-setting",
            "disposition": "included_nonmechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                key: [] for key in (
                    "artifacts", "capabilities", "mechanics", "procedures",
                    "prompt_fragments", "schemas", "state_namespaces", "tools", "ui_surfaces",
                )
            },
            "evidence": {"tests": ["severed-lands-campaign-collection"]},
        }
        for path, data in additions:
            payloads[path] = data
            inclusion["links"]["artifacts"].append(path)
        declarations["inclusions"].append(inclusion)
        payloads["content/content-declarations.json"] = canonical_json_bytes(declarations)
        setting = NormalizedPackageDraft(setting.record, payloads, 4)
    with materialize_package_archive((rules, reference, setting)) as result:
        primary = next(m for m in result.manifests if m.role is PackageRole.PRIMARY)
        world = next(m for m in result.manifests if m.package_id == "pneuma.severed-lands")
        return PlanSources(result.archive.read(), {
            "ruleset": PackageBinding(*primary.identity).as_payload(),
            "campaign_setting": PackageBinding(*world.identity).as_payload(),
            "package_closure_sha256": result.closure_sha256,
        })


def sources_for(root: Path) -> PlanSources:
    """Use the retained portable snapshot or this public Plan's authored Setting files."""
    snapshot_path = root / "setting.sylvia-gm-package"
    if snapshot_path.is_file():
        snapshot = snapshot_path.read_bytes()
        plan = validate_plan_snapshot(
            snapshot, json.loads((root / "plan.json").read_text(encoding="utf-8")),
            (root / "plan.md").read_text(encoding="utf-8"),
        )
        return PlanSources(snapshot, deepcopy(dict(plan.payload["bindings"])), plan)
    additions = root / "setting-files"
    return plan_sources(tuple(
        (p.relative_to(additions).as_posix(), p.read_bytes().replace(b"\r\n", b"\n"))
        for p in sorted(additions.rglob("*")) if p.is_file()
    ))


def bundled_plans_root() -> Path:
    """Use frozen content exclusively in a packaged owner; never fall back to the checkout."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    return (Path(frozen_root) if isinstance(frozen_root, str)
            else Path(__file__).resolve().parents[3]) / "campaign-plans"


def _contained_plan_path(root: Path, path: Path) -> Path:
    """Reject escaped paths before reading or selecting their contents."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Bundled Plan file is missing or outside the selected collection")
    return path


def read_plan_catalogue(root: Path) -> tuple[dict, ...]:
    """Validate the explicit public inventory used by both packaging and the chooser."""
    catalogue = _contained_plan_path(root, root / "catalogue.json")
    rows = json.loads(catalogue.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise ValueError("Bundled Campaign Plan catalogue is invalid")
    identities = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", row["id"],
        ) is None or row["id"] in identities:
            raise ValueError("Bundled Campaign Plan identity is invalid or duplicated")
        if any(not isinstance(row.get(key), str) or not row[key].strip()
               for key in ("title", "pitch", "format")):
            raise ValueError("Bundled Campaign Plan public description is invalid")
        identities.add(row["id"])
    return tuple(rows)


def bundled_plan_files(root: Path) -> tuple[Path, ...]:
    """Package only selected Plans and their explicitly referenced additional Character Files."""
    files = [root / "catalogue.json"]
    catalogue = read_plan_catalogue(root)
    for row in catalogue:
        directory = root / row["id"]
        files.extend((directory / "plan.json", directory / "plan.md"))
        snapshot = _contained_plan_path(root, directory / "setting.sylvia-gm-package")
        if snapshot.is_file():
            files.append(snapshot)
        contribution = _contained_plan_path(root, directory / "interview-contribution.json")
        if contribution.is_file():
            files.append(contribution)
        plan_path = _contained_plan_path(root, directory / "plan.json")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        party = parse_starting_party_policy(plan["starting_party"])
        references = list(party.required_companions)
        if party.fixed_player_character is not None:
            references.append(party.fixed_player_character)
        for reference in references:
            relative = PurePosixPath(reference.path)
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
                raise ValueError("Bundled Plan character path is invalid")
            addition = _contained_plan_path(root, directory / "setting-files" / relative)
            if addition.is_file():
                files.append(addition)
    legacy = _contained_plan_path(root, root / "the-rift-awakening")
    if "the-rift-awakening" not in {row["id"] for row in catalogue} and legacy.is_dir():
        files.extend((legacy / "plan.json", legacy / "plan.md"))
        for name in ("setting.sylvia-gm-package", "interview-contribution.json"):
            retained = _contained_plan_path(root, legacy / name)
            if retained.is_file():
                files.append(retained)
    for path in files:
        _contained_plan_path(root, path)
        if not path.is_file():
            raise ValueError("Bundled Plan file is missing or outside the selected collection")
    return tuple(files)


@lru_cache(maxsize=1)
def _catalogue() -> tuple[dict, ...]:
    return read_plan_catalogue(bundled_plans_root())


@lru_cache(maxsize=256)
def _bundled_plan(plan_id: str) -> ReusableCampaignPlan | None:
    """Validate one inventoried adventure against the snapshot shipped with it."""
    public = next((row for row in _catalogue() if row["id"] == plan_id), None)
    if public is None:
        # Retain exact access for older saved setup references without adding a
        # seventeenth choice to the current public collection.
        if plan_id != "the-rift-awakening" or not (bundled_plans_root() / plan_id / "plan.json").is_file():
            return None
        legacy = json.loads((bundled_plans_root() / plan_id / "plan.json").read_text(encoding="utf-8"))
        public = {"id": plan_id, "title": legacy["title"], "pitch": "The original Rift Awakening campaign."}
    root = bundled_plans_root() / plan_id
    sources = sources_for(root)
    plan = sources.validated_plan or validate_plan_snapshot(
        sources.snapshot, json.loads((root / "plan.json").read_text(encoding="utf-8")),
        (root / "plan.md").read_text(encoding="utf-8"),
    )
    if plan.payload["id"] != plan_id or plan.title != public["title"]:
        raise ValueError("Bundled Campaign Plan does not match its catalogue")
    contribution_path = root / "interview-contribution.json"
    contribution = parse_interview_contribution_bytes(
        contribution_path.read_bytes(), expected_owner_kind="campaign_plan",
    ).as_payload() if contribution_path.is_file() else None
    return ReusableCampaignPlan(
        artifact_id=plan_id, title=plan.title, summary=public["pitch"],
        content_sha256=plan.content_sha256, plan=plan, interview_contribution=contribution,
        setup_id="the-rift-awakening" if plan_id == "the-rift-awakening" else None,
        package_snapshot=sources.snapshot,
    )


def bundled_plan(plan_id: str) -> ReusableCampaignPlan | None:
    """Return caller-owned Plan data so one request cannot alter later catalogue reads."""
    return deepcopy(_bundled_plan(plan_id))


def selected_campaign_plan(
    storage_root: Path, plan_id: str, *, validate_plan: CampaignPlanValidator | None = None,
) -> ReusableCampaignPlan:
    """Resolve a built-in or saved selection without copying built-ins into the user's library."""
    selected = bundled_plan(plan_id)
    return selected if selected is not None else load_reusable_campaign_plan(
        storage_root, plan_id, validate_plan=validate_plan,
    )


def bundled_plan_catalog() -> tuple[dict, ...]:
    """Project public catalogue descriptions instead of private GM premises."""
    rows = []
    for public in _catalogue():
        plan = bundled_plan(public["id"])
        assert plan is not None
        row = dict(plan.catalog_payload())
        row.pop("reusable", None)
        row["bundled"] = True
        row["interview_contribution"] = plan.interview_contribution
        row["browsing"] = {key: deepcopy(public[key]) for key in (
            "format", "acts", "character_mode", "protagonist", "recommended_level",
        ) if key in public}
        rows.append(row)
    return tuple(rows)


def bundled_setting_snapshot(ruleset_id: str, setting_id: str) -> bytes | None:
    """Resolve a world offered by the public bundled Plans without inventing a binding."""
    for row in bundled_plan_catalog():
        if row["ruleset"]["id"] == ruleset_id and row["campaign_setting"]["id"] == setting_id:
            selected = bundled_plan(row["id"])
            if selected is not None:
                return selected.package_snapshot
    return None


def known_builtin_public_summary(plan: ValidatedCampaignPlan) -> str | None:
    """Give exact retained story copies the public hook, without rewriting their pins.

    Package bindings may change between retained copies. Every other Plan field
    and the complete GM Markdown must match; edited stories keep their metadata.
    """
    known = bundled_plan(str(plan.payload.get("id", "")))
    if known is None or known.plan.readable_markdown != plan.readable_markdown:
        return None
    original = {key: value for key, value in known.plan.payload.items() if key != "bindings"}
    retained = {key: value for key, value in plan.payload.items() if key != "bindings"}
    if canonical_json_bytes(original) != canonical_json_bytes(retained):
        return None
    return known.summary
