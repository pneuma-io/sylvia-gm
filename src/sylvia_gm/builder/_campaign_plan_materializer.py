"""Validate the fixed Builder Campaign Plan draft without scanning unrelated workspace data."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sylvia_gm.builder._package_materializer import _reject_reparse_chain
from sylvia_gm.domain.campaign_plan import (
    CAMPAIGN_PLAN_BYTE_LIMIT,
    PackageBinding,
    ValidatedCampaignPlan,
    validate_campaign_plan,
)
from sylvia_gm.domain.package_library import canonical_json_bytes

_DRAFT_DIRECTORY = "campaign-plan-draft"
_PLAN_FILE = "plan.json"
_READABLE_FILE = "plan.md"


@dataclass(frozen=True)
class CampaignPlanDraft:
    """One validated Builder artifact ready for durable publication evidence."""

    plan: ValidatedCampaignPlan
    plan_path: str = f"{_DRAFT_DIRECTORY}/{_PLAN_FILE}"
    readable_path: str = f"{_DRAFT_DIRECTORY}/{_READABLE_FILE}"


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys so the model cannot publish ambiguous meaning."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Campaign Plan JSON contains a duplicate key")
        result[key] = value
    return result


def _bounded_text(path: Path, label: str) -> str:
    """Read one regular UTF-8 draft file under the Campaign Plan byte ceiling."""

    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is unavailable")
    with path.open("rb") as source:
        value = source.read(CAMPAIGN_PLAN_BYTE_LIMIT + 1)
    if len(value) > CAMPAIGN_PLAN_BYTE_LIMIT:
        raise ValueError(f"{label} is too large")
    return value.decode("utf-8")


def materialize_campaign_plan_draft(
    workspace: Path,
    *,
    expected_ruleset: PackageBinding,
    expected_setting: PackageBinding,
    expected_closure_sha256: str,
    setting_paths: frozenset[str],
    setting_knowledge_ids: frozenset[str] = frozenset(),
) -> CampaignPlanDraft:
    """Validate exactly campaign-plan-draft/plan.json and plan.md."""

    draft = workspace / _DRAFT_DIRECTORY
    plan_text = _bounded_text(draft / _PLAN_FILE, "Campaign Plan structured artifact")
    readable = _bounded_text(draft / _READABLE_FILE, "Campaign Plan readable rendering")
    try:
        payload = json.loads(
            plan_text,
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Campaign Plan JSON constant {value} is invalid")
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Campaign Plan JSON is invalid") from error
    return CampaignPlanDraft(
        validate_campaign_plan(
            payload,
            readable_markdown=readable,
            expected_ruleset=expected_ruleset,
            expected_setting=expected_setting,
            expected_closure_sha256=expected_closure_sha256,
            setting_paths=setting_paths,
            setting_knowledge_ids=setting_knowledge_ids,
        )
    )


def publish_campaign_plan_draft(
    workspace: Path,
    *,
    expected_ruleset: PackageBinding,
    expected_setting: PackageBinding,
    expected_closure_sha256: str,
    setting_paths: frozenset[str],
    setting_knowledge_ids: frozenset[str] = frozenset(),
) -> CampaignPlanDraft:
    """Validate a draft, then publish the exact canonical files used by DM handoff."""

    draft = materialize_campaign_plan_draft(
        workspace,
        expected_ruleset=expected_ruleset,
        expected_setting=expected_setting,
        expected_closure_sha256=expected_closure_sha256,
        setting_paths=setting_paths,
        setting_knowledge_ids=setting_knowledge_ids,
    )
    return publish_campaign_plan_files(workspace, draft.plan)


def publish_campaign_plan_files(workspace: Path, plan: ValidatedCampaignPlan) -> CampaignPlanDraft:
    """Write canonical handoff files only after the complete Plan has been validated."""
    outputs = (
        (workspace / "campaign-plan.json", canonical_json_bytes(plan.payload)),
        (workspace / "campaign-plan.md", plan.readable_markdown.encode("utf-8")),
    )
    for target, content in outputs:
        _reject_reparse_chain(
            workspace, target if target.exists() or target.is_symlink() else target.parent,
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=workspace, prefix=".plan-save-", delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(content)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return CampaignPlanDraft(
        plan=plan,
        plan_path="campaign-plan.json",
        readable_path="campaign-plan.md",
    )
