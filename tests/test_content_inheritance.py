"""A Setting carries its exact Ruleset without adding a world to that Ruleset."""

import io
import json
from dataclasses import replace

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import (
    _bundled_setting_root,
    _package_draft,
    _srd_drafts,
)
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.authority import PackageRole, WriterRole


def snapshot_drafts():
    rules, reference = _srd_drafts()
    setting = _package_draft(_bundled_setting_root())
    return tuple(
        NormalizedPackageDraft(
            {**draft.record, "content_kind": kind, **overrides},
            draft.payloads,
            4,
        )
        for draft, kind, overrides in (
            (rules, "ruleset", {}),
            (reference, "support", {}),
            (
                setting,
                "campaign_setting",
                {
                    "dependencies": [
                        {
                            "namespace": "ruleset_snapshot",
                            "kind": "ruleset_snapshot",
                            "id": rules.record["id"],
                            "version": rules.record["version"],
                        }
                    ]
                },
            ),
        )
    )


def test_setting_contains_unchanged_ruleset_and_survives_install_reopen_export(tmp_path):
    drafts = snapshot_drafts()
    with (
        materialize_package_archive(drafts[:2]) as rules,
        materialize_package_archive(drafts) as setting,
    ):
        assert rules.manifests[0].identity == setting.manifests[0].identity
        assert not any(d.kind == "campaign_setting" for d in setting.manifests[0].dependencies)
        assert setting.package_lock.entries[0].role is PackageRole.PRIMARY
        archive = setting.archive.read()
        expected = setting.package_lock
    database = tmp_path / "snapshots.db"
    with AuthorityOwner.open(database, "install-snapshot") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(archive), install_id="setting-snapshot"
        )
        assert candidate.package_lock == expected
        candidate_id = candidate.candidate_id
    with AuthorityOwner.open(database, "reopen-snapshot") as owner:
        assert owner.package_candidate(candidate_id).package_lock == expected
        exported = io.BytesIO()
        owner.export_package_candidate(candidate_id, exported)
        assert exported.getvalue() == archive


def test_campaign_with_contained_ruleset_survives_portable_export_and_recovery(tmp_path):
    portable = tmp_path / "contained-campaign.sylvia-gm-campaign"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        with materialize_package_archive(snapshot_drafts()) as source:
            candidate = owner.install_package_archive(source.archive, install_id="snapshot")
        owner.create_campaign(CampaignCreate(
            campaign_id="contained-campaign", display_name="Synthetic contained campaign",
            package_lock=candidate.package_lock, writer=WriterRole.BUILDER, turn_id="setup",
        ))
        owner.complete_turn("contained-campaign", "setup")
        before = owner.snapshot("contained-campaign")
        owner.export_campaign(
            operation_id="export", campaign_id="contained-campaign", target=portable,
        )
    with AuthorityOwner.open(tmp_path / "recovered.db", "recovery") as owner:
        owner.recover_campaign(recovery_id="recover", source=portable)
        after = owner.snapshot("contained-campaign")
        assert after.package_lock == before.package_lock
        assert after.revision == before.revision


def test_customizing_a_snapshot_does_not_change_its_source():
    source = snapshot_drafts()
    changed_files = dict(source[0].payloads)
    changed_files["prompts/gm-operating-guide.md"] += (
        b"\nUse this world's explicit custom guidance.\n"
    )
    changed = (replace(source[0], payloads=changed_files), *source[1:])
    with (
        materialize_package_archive(source) as before,
        materialize_package_archive(changed) as after,
    ):
        assert before.manifests[0].identity != after.manifests[0].identity
        setting_id = source[2].record["id"]
        assert next(m.identity for m in before.manifests if m.package_id == setting_id) != next(
            m.identity for m in after.manifests if m.package_id == setting_id
        )
    with materialize_package_archive(source) as reopened:
        assert reopened.manifests[0].identity == before.manifests[0].identity


def test_new_ruleset_cannot_declare_a_setting():
    rules, reference, setting = snapshot_drafts()
    reversed_rules = replace(
        rules,
        record={
            **rules.record,
            "dependencies": [
                *rules.record["dependencies"],
                {
                    "namespace": "campaign_setting",
                    "kind": "campaign_setting",
                    "id": setting.record["id"],
                    "version": setting.record["version"],
                },
            ],
        },
    )
    orphan_setting = replace(setting, record={**setting.record, "dependencies": []})
    with pytest.raises(ValueError, match="Ruleset.*Setting"):
        materialize_package_archive((reversed_rules, reference, orphan_setting))


def test_setting_snapshot_requires_its_exact_ruleset_bytes():
    rules, reference, setting = snapshot_drafts()
    record = json.loads(json.dumps(setting.record))
    record["dependencies"][0]["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="dependency hash"):
        materialize_package_archive((rules, reference, replace(setting, record=record)))


def test_picker_and_builder_use_setting_copy_without_installing_source(tmp_path):
    from sylvia_gm.application.builder_references import capture_launch_references
    from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
    from tests.test_builder_references import open_character_interview

    rules, reference, setting = snapshot_drafts()
    second = replace(setting, record={**setting.record, "id": "pneuma.second-world"})
    with AuthorityOwner.open(tmp_path / "picker.db", "snapshot-picker") as owner:
        with materialize_package_archive((rules, reference, setting)) as first_archive:
            first = owner.install_package_archive(first_archive.archive, install_id="first-world")
        with materialize_package_archive((rules, reference, second)) as second_archive:
            other = owner.install_package_archive(second_archive.archive, install_id="second-world")
        catalog = _campaign_creation_catalog(tmp_path, owner)
        choice = next(item for item in catalog if item.get("kind") == "ruleset")
        settings = {item["id"]: item for item in choice["compatible_settings"]}
        assert settings[setting.record["id"]]["candidate_id"] == first.candidate_id
        assert settings[second.record["id"]]["candidate_id"] == other.candidate_id
        draft = open_character_interview(
            owner,
            choice["selection_id"],
            second.record["id"],
            None,
        )
        references = capture_launch_references(
            owner,
            tmp_path,
            owner.session_context(draft.builder_session_id),
            catalog,
        )
        assert references["candidate_id"] == other.candidate_id
        assert (
            next(p for p in references["packages"] if p["prefix"] == "setting")["id"]
            == (second.record["id"])
        )
        assert len(owner.package_candidates()) == 2


def test_plan_retains_its_copies_after_new_source_ruleset_is_installed(tmp_path):
    from sylvia_gm.agent._builder_session import _campaign_plan_example
    from sylvia_gm.application.builder_references import capture_launch_references
    from sylvia_gm.application.campaign_library_selection import resolve_campaign_library_selection
    from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
    from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan
    from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
    from tests.test_builder_references import open_character_interview

    rules, reference, setting = snapshot_drafts()
    with AuthorityOwner.open(tmp_path / "plan-copies.db", "plan-copies") as owner:
        with materialize_package_archive((rules, reference, setting)) as archive:
            original = owner.install_package_archive(archive.archive, install_id="plan-world")
        sources = campaign_plan_sources(owner, original.package_lock)
        plan = save_reusable_campaign_plan(
            tmp_path,
            operation_id="original-plan",
            source_payload=json.loads(_campaign_plan_example(sources)),
            source_markdown="# An inherited adventure",
            validate_plan=sources.validate,
        )
        updated = replace(rules, record={**rules.record, "version": "9.0.0"})
        with materialize_package_archive((updated, reference)) as archive:
            newer = owner.install_package_archive(archive.archive, install_id="updated-source")
        catalog = _campaign_creation_catalog(tmp_path, owner)
        choice = next(item for item in catalog if item.get("kind") == "ruleset")
        assert choice["version"] == "9.0.0"
        assert choice["compatible_settings"][0]["ruleset"]["version"] == rules.record["version"]
        draft = open_character_interview(
            owner,
            newer.candidate_id,
            setting.record["id"],
            plan.artifact_id,
        )
        references = capture_launch_references(
            owner,
            tmp_path,
            owner.session_context(draft.builder_session_id),
            catalog,
        )
        assert references["candidate_id"] == original.candidate_id
        selected = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=newer.candidate_id,
            setting_id=setting.record["id"],
            plan_id=plan.artifact_id,
        )
        assert selected.candidate == original
        assert selected.sources.ruleset == sources.ruleset
