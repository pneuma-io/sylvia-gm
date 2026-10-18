"""The Campaign Interview follows the exact content selected by a saved Plan."""

import json
from pathlib import Path

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import capture_launch_references
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.plan_snapshot import retained_plan_snapshot
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan
from sylvia_gm.domain.package_library import canonical_json_bytes


def contribution(owner_kind, identity, prompt):
    return {
        "format_id": "sylvia-gm-interview-contribution",
        "format_version": 1,
        "owner_kind": owner_kind,
        "id": identity,
        "title": f"{identity} questions",
        "questions": [
            {
                "id": "selection",
                "prompt": prompt,
                "type": "choice",
                "required": True,
                "adaptive": False,
                "options": [
                    {"id": "first", "label": "First"},
                    {"id": "second", "label": "Second"},
                ],
                "condition": None,
            }
        ],
    }


def install_signal(owner, *, version, suffix, contributions=True):
    fixtures = Path(__file__).parent / "fixtures"
    roots = (
        fixtures / "rulesets/signal-and-static",
        fixtures / "campaign-settings/signal-and-static",
    )
    records = [json.loads((root / "package-record.json").read_text()) for root in roots]
    for record in records:
        record["version"] = version
    records[0]["dependencies"] = [
        {
            "namespace": "campaign_setting",
            "kind": "campaign_setting",
            "id": records[1]["id"],
            "version": version,
        }
    ]
    drafts = []
    for root, record, owner_kind in zip(
        roots,
        records,
        ("ruleset", "campaign_setting"),
        strict=True,
    ):
        payloads = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and path.name != "package-record.json"
        }
        if contributions:
            payloads["interview-contribution.json"] = canonical_json_bytes(
                contribution(
                    owner_kind,
                    f"{owner_kind}.{suffix}",
                    f"Which {suffix} {owner_kind} option?",
                )
            )
            declarations = json.loads(payloads["content/content-declarations.json"])
            declarations["inclusions"][0]["links"]["artifacts"].append(
                "interview-contribution.json"
            )
            declarations["inclusions"][0]["links"]["artifacts"].sort()
            payloads["content/content-declarations.json"] = canonical_json_bytes(declarations)
        drafts.append(NormalizedPackageDraft(record=record, payloads=payloads))
    with materialize_package_archive(drafts) as archive:
        candidate = owner.install_package_archive(archive.archive, install_id=f"signal-{suffix}")
    return candidate, records[1]["id"]


def new_draft(owner, identity="effective"):
    return owner.create_guided_campaign_draft(
        operation_id=f"create-{identity}",
        display_name="Effective Interview",
        builder_session_id=f"session-{identity}",
        builder_provider_id="test.provider",
        builder_model_id="test.model",
    )


def advance(owner, draft, revision, step, answer, identity=None):
    return owner.advance_guided_campaign_draft(
        operation_id=identity or f"answer-{step}-{revision}",
        campaign_id=draft.campaign_id,
        builder_session_id=draft.builder_session_id,
        expected_construction_revision=revision,
        current_step=step,
        visible_answer=step,
        answer=answer,
    )


def save_plan(storage_root, owner, candidate, suffix):
    authority = campaign_plan_sources(owner, candidate.package_lock)
    payload = json.loads(_campaign_plan_example(authority))
    payload.update(id=f"plan-{suffix}", title=f"Plan {suffix}", premise="Follow the signal.")
    return save_reusable_campaign_plan(
        storage_root,
        operation_id=f"save-plan-{suffix}",
        source_payload=payload,
        source_markdown=f"# Plan {suffix}\n\nFollow the signal.",
        validate_plan=authority.validate,
        package_snapshot=retained_plan_snapshot(owner, payload),
        interview_contribution=contribution(
            "campaign_plan",
            f"campaign_plan.{suffix}",
            f"Which {suffix} plan option?",
        ),
    )


def answer_contribution(owner, draft, revision, step, suffix):
    owner_kind = {
        "ruleset_contribution": "ruleset",
        "setting_contribution": "campaign_setting",
        "plan_contribution": "campaign_plan",
    }[step]
    return advance(
        owner,
        draft,
        revision,
        step,
        {
            "contribution_id": f"{owner_kind}.{suffix}",
            "answers": {"selection": "second"},
        },
    )


def latest_state(owner, session_id):
    return next(
        message.content
        for message in reversed(owner.session_context(session_id).messages)
        if message.content.get("kind") == "campaign_interview_state"
    )


def test_plan_snapshot_questions_replace_current_source_and_all_three_answers_resume(tmp_path):
    database = tmp_path / "effective.db"
    with AuthorityOwner.open(database, "seed") as owner:
        source, setting_id = install_signal(owner, version="0.1.0", suffix="source")
        selected, _ = install_signal(owner, version="0.2.0", suffix="snapshot")
        plan = save_plan(tmp_path, owner, selected, "snapshot")
        draft = new_draft(owner)
        result = advance(owner, draft, 0, "ruleset", {"candidate_id": source.candidate_id})
        assert result.current_step == "setting"
        result = advance(
            owner,
            draft,
            1,
            "setting",
            {
                "candidate_id": source.candidate_id,
                "setting_id": setting_id,
            },
        )
        assert result.current_step == "plan"
        result = advance(owner, draft, 2, "plan", {"plan_id": plan.artifact_id})
        assert result.current_step == "ruleset_contribution"
        ruleset_questions = owner.guided_campaign_contribution(
            builder_session_id=draft.builder_session_id,
            contribution_step="ruleset_contribution",
        )
        assert "snapshot ruleset" in ruleset_questions["questions"][0]["prompt"]
        assert "source ruleset" not in ruleset_questions["questions"][0]["prompt"]
        assert (
            owner.guided_campaign_contribution(
                builder_session_id=draft.builder_session_id,
                contribution_step="setting_contribution",
            )["id"]
            == "campaign_setting.snapshot"
        )

        result = answer_contribution(owner, draft, 3, "ruleset_contribution", "snapshot")
        assert result.current_step == "setting_contribution"
        result = answer_contribution(owner, draft, 4, "setting_contribution", "snapshot")
        assert result.current_step == "plan_contribution"
        result = answer_contribution(owner, draft, 5, "plan_contribution", "snapshot")
        assert result.current_step == "name"
        state = latest_state(owner, draft.builder_session_id)
        contribution_message = next(
            message
            for message in owner.session_context(draft.builder_session_id).messages
            if message.message_id == state["answers"]["plan_contribution"]
        )
        assert len(contribution_message.content["interview_value"]["contribution_sha256"]) == 64

    with AuthorityOwner.open(database, "resume") as owner:
        resumed = latest_state(owner, draft.builder_session_id)
        assert resumed["current_step"] == "name"
        assert set(resumed["answers"]) >= {
            "ruleset_contribution",
            "setting_contribution",
            "plan_contribution",
        }
        replay = answer_contribution(owner, draft, 3, "ruleset_contribution", "snapshot")
        assert replay.current_step == "setting_contribution"
        assert latest_state(owner, draft.builder_session_id) == resumed


def test_reselecting_source_invalidates_snapshot_contribution_answers(tmp_path):
    database = tmp_path / "reselect.db"
    with AuthorityOwner.open(database, "owner") as owner:
        source, setting_id = install_signal(owner, version="0.1.0", suffix="source")
        selected, _ = install_signal(owner, version="0.2.0", suffix="snapshot")
        plan = save_plan(tmp_path, owner, selected, "snapshot")
        draft = new_draft(owner, "reselect")
        revision = 0
        for step, answer in (
            ("ruleset", {"candidate_id": source.candidate_id}),
            ("setting", {"candidate_id": source.candidate_id, "setting_id": setting_id}),
            ("plan", {"plan_id": plan.artifact_id}),
        ):
            revision = advance(owner, draft, revision, step, answer).construction_revision
        for step in ("ruleset_contribution", "setting_contribution", "plan_contribution"):
            revision = answer_contribution(
                owner, draft, revision, step, "snapshot"
            ).construction_revision

        reselected = advance(
            owner,
            draft,
            revision,
            "ruleset",
            {"candidate_id": source.candidate_id},
            identity="reselect-source",
        )
        assert reselected.current_step == "setting"
        state = latest_state(owner, draft.builder_session_id)
        assert set(state["answers"]) == {"ruleset"}


def test_absent_optional_contributions_skip_directly_to_name(tmp_path):
    database = tmp_path / "optional.db"
    with AuthorityOwner.open(database, "owner") as owner:
        candidate, setting_id = install_signal(
            owner,
            version="0.3.0",
            suffix="plain",
            contributions=False,
        )
        authority = campaign_plan_sources(owner, candidate.package_lock)
        payload = json.loads(_campaign_plan_example(authority))
        payload.update(id="plain-plan", title="Plain Plan", premise="Follow the signal.")
        plan = save_reusable_campaign_plan(
            tmp_path,
            operation_id="save-plain-plan",
            source_payload=payload,
            source_markdown="# Plain Plan\n\nFollow the signal.",
            validate_plan=authority.validate,
            package_snapshot=retained_plan_snapshot(owner, payload),
        )
        draft = new_draft(owner, "optional")
        advance(owner, draft, 0, "ruleset", {"candidate_id": candidate.candidate_id})
        advance(
            owner,
            draft,
            1,
            "setting",
            {
                "candidate_id": candidate.candidate_id,
                "setting_id": setting_id,
            },
        )
        result = advance(owner, draft, 2, "plan", {"plan_id": plan.artifact_id})
        assert result.current_step == "name"
        for step in ("ruleset_contribution", "setting_contribution", "plan_contribution"):
            assert (
                owner.guided_campaign_contribution(
                    builder_session_id=draft.builder_session_id,
                    contribution_step=step,
                )
                is None
            )


def test_setting_selection_retains_its_rules_and_builder_sources_after_catalog_refresh(tmp_path):
    with AuthorityOwner.open(tmp_path / "exact-setting.db", "owner") as owner:
        original, setting_id = install_signal(owner, version="0.1.0", suffix="original")
        updated, _ = install_signal(owner, version="0.2.0", suffix="updated")
        draft = new_draft(owner, "setting-only")
        advance(owner, draft, 0, "ruleset", {"candidate_id": updated.candidate_id})
        advance(
            owner,
            draft,
            1,
            "setting",
            {
                "candidate_id": original.candidate_id,
                "setting_id": setting_id,
            },
        )
        result = advance(owner, draft, 2, "plan", {"plan_id": None})
        assert result.current_step == "ruleset_contribution"
        assert (
            owner.guided_campaign_contribution(
                builder_session_id=draft.builder_session_id,
                contribution_step="ruleset_contribution",
            )["id"]
            == "ruleset.original"
        )
        primary = campaign_plan_sources(owner, updated.package_lock).ruleset
        references = capture_launch_references(
            owner,
            tmp_path,
            owner.session_context(draft.builder_session_id),
            [
                {
                    "kind": "ruleset",
                    "id": primary.package_id,
                    "version": primary.version,
                    "content_sha256": primary.content_sha256,
                    "selection_id": updated.candidate_id,
                    "compatible_settings": [
                        {"id": setting_id, "candidate_id": updated.candidate_id}
                    ],
                }
            ],
        )
        assert references["candidate_id"] == original.candidate_id
