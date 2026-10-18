"""Resolve Interview questions from the selected Plan's saved world and rules."""

from __future__ import annotations

from sylvia_gm.application.launch_compatibility import require_active_ruleset

import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.bundled_campaign_plans import bundled_plan, bundled_setting_snapshot
from sylvia_gm.application.bundled_packages import (
    bundled_rift_archive,
    bundled_rift_authority,
)
from sylvia_gm.application.campaign_plan_sources import validate_retained_campaign_plan
from sylvia_gm.application.reusable_plans import load_reusable_campaign_plan
from sylvia_gm.domain.authority import PackageRole, SafePayload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import PackageCandidate, campaign_setting_entries

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


@dataclass(frozen=True)
class InterviewContent:
    candidate: PackageCandidate | None
    setting_id: str | None
    plan_contribution: SafePayload | None = None


def resolve_interview_content(
    owner: AuthorityOwner,
    storage_root: Path | None,
    selections: Mapping[str, Mapping[str, object]],
) -> InterviewContent:
    """Prefer contained Plan bytes, then the exact selected Setting, then the source Ruleset."""
    ruleset_id = selections.get("ruleset", {}).get("candidate_id")
    setting_answer = selections.get("setting", {})
    setting_id = setting_answer.get("setting_id")
    plan_id = selections.get("plan", {}).get("plan_id")
    if ruleset_id is None:
        if setting_id is not None or plan_id is not None:
            raise CommandRejected("Selected world content requires its Ruleset")
        return InterviewContent(None, None)
    if not isinstance(ruleset_id, str) or not ruleset_id:
        raise CommandRejected("Selected Ruleset identity is invalid")
    try:
        selected = owner.package_candidate(ruleset_id)
    except CommandRejected as error:
        if error.code != "candidate_unavailable":
            raise
        selected = None
        logical_ruleset = ruleset_id
    else:
        logical_ruleset = next(
            entry.package_id for entry in selected.package_lock.entries
            if entry.role is PackageRole.PRIMARY
        )
    require_active_ruleset(logical_ruleset)
    contribution = None
    snapshot = None
    if plan_id is not None:
        builtin = bundled_plan(plan_id) if isinstance(plan_id, str) else None
        if builtin is not None:
            plan = builtin.plan
            contribution = builtin.interview_contribution
            snapshot = builtin.package_snapshot
        else:
            if storage_root is None or not isinstance(plan_id, str):
                raise CommandRejected("Selected Campaign Plan library is unavailable")
            saved = load_reusable_campaign_plan(
                storage_root, plan_id,
                validate_plan=lambda payload, text: validate_retained_campaign_plan(
                    owner, payload, text,
                ),
            )
            plan = saved.plan
            snapshot = saved.package_snapshot
            contribution = saved.interview_contribution
        bindings = plan.payload["bindings"]
        if (
            bindings["ruleset"]["id"] != logical_ruleset
            or bindings["campaign_setting"]["id"] != setting_id
        ):
            raise CommandRejected("The selected Plan belongs to different content")
        expected = bindings["package_closure_sha256"]
        candidate = next(
            (item for item in owner.package_candidates() if item.closure_sha256 == expected), None,
        )
        if candidate is None and snapshot is not None:
            candidate = owner.install_package_archive(
                io.BytesIO(snapshot),
                install_id=f"interview-snapshot-{hashlib.sha256(snapshot).hexdigest()[:24]}",
            )
        if candidate is None or candidate.closure_sha256 != expected:
            raise CommandRejected("Selected Campaign Plan snapshot is unavailable")
        return InterviewContent(candidate, str(setting_id), contribution)

    exact_candidate = setting_answer.get("candidate_id")
    candidate = owner.package_candidate(exact_candidate) if isinstance(exact_candidate, str) else (
        selected
    )
    if setting_id is not None:
        if not isinstance(setting_id, str):
            raise CommandRejected("Selected Setting identity is invalid")
        if candidate is None:
            bundled = bundled_rift_authority()
            if (
                logical_ruleset == bundled.ruleset.package_id
                and setting_id == bundled.setting.package_id
            ):
                snapshot = bundled_rift_archive()
                candidate = next(
                    (item for item in owner.package_candidates()
                     if item.closure_sha256 == bundled.closure_sha256), None,
                )
                if candidate is None:
                    candidate = owner.install_package_archive(
                        io.BytesIO(snapshot),
                        install_id=f"interview-snapshot-{hashlib.sha256(snapshot).hexdigest()[:24]}",
                    )
        if candidate is None or not any(item.package_id == setting_id for item in campaign_setting_entries(candidate.package_lock)):
            snapshot = bundled_setting_snapshot(logical_ruleset, setting_id)
            if snapshot is not None:
                candidate = owner.install_package_archive(io.BytesIO(snapshot),
                    install_id=f"interview-snapshot-{hashlib.sha256(snapshot).hexdigest()[:24]}")
        if candidate is None or not any(
            item.package_id == setting_id
            for item in campaign_setting_entries(candidate.package_lock)
        ):
            raise CommandRejected("Selected Setting snapshot is unavailable; refresh its choices")
    if candidate is not None and not any(
        entry.role is PackageRole.PRIMARY and entry.package_id == logical_ruleset
        for entry in candidate.package_lock.entries
    ):
        raise CommandRejected("The selected Setting belongs to another Ruleset")
    return InterviewContent(candidate, setting_id)
