"""Explicit adoption keeps existing campaigns and reusable source snapshots intact."""

from dataclasses import replace

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.domain.authority import (
    MechanicOutcome,
    Participant,
    ParticipantMutation,
    StateValue,
    WriterRole,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.package_library import content_root_for_record
from tests.test_alpha_f3_package_publication import _clockwork, _install, _surface


def package(version, *, panel=True, change=None):
    record, files = _clockwork(version, payload=b'{"ticks":2}')
    if panel:
        record["ui_surfaces"].append(_surface("journal", "clockwork.counter"))
    if change:
        change(record, files)
    record["content_sha256"] = content_root_for_record(record)
    return record, files


def campaign(owner, old, name="running", *, state=9):
    owner.create_campaign(
        CampaignCreate(
            campaign_id=name,
            display_name="Synthetic observatory",
            writer=WriterRole.DM,
            package_lock=old.package_lock,
            turn_id="turn-one",
            initial_state=(
                StateValue("clockwork", "counter", state),
                StateValue("clockwork", "progress", {"chapter": 3, "notes": ["saved"]}),
            ),
        )
    )
    owner.complete_turn(name, "turn-one")


def adopt(owner, preview, operation="adopt-reviewed"):
    return owner.adopt_campaign_rules_update(
        **{
            key: preview[key]
            for key in (
                "campaign_id",
                "candidate_id",
                "expected_revision",
                "expected_lock_id",
                "confirmation",
            )
        },
        activation_id=operation,
    )


@pytest.fixture
def prepared(tmp_path):
    with AuthorityOwner.open(tmp_path / "campaigns.db", "rules-update-test") as owner:
        old = _install(owner, (_clockwork("1"),), "old")
        new = _install(owner, (package("2"),), "new")
        campaign(owner, old)
        campaign(owner, old, "other", state=42)
        yield owner, old, new


def test_preview_cancel_adopt_retry_and_reopen_preserve_state_source_and_other_campaign(prepared):
    owner, old, new = prepared
    before = owner.snapshot("running")
    other = owner.snapshot("other")
    old_lock = owner.current_package_lock("running")
    source = owner.inspect_package_candidate(old.candidate_id)
    options = owner.campaign_rules_update_options("running")
    assert [x["candidate_id"] for x in options["candidates"]] == [new.candidate_id]
    preview = owner.preview_campaign_rules_update("running", new.candidate_id)
    assert preview["compatible"] and preview["added_panels"] == ("Clockwork",)
    assert owner.preview_campaign_rules_update("running", new.candidate_id) == preview
    assert owner.snapshot("running") == before
    assert owner.current_package_lock("running") == old_lock
    receipt = adopt(owner, preview)
    assert receipt.resulting_revision == before.revision + 1
    assert owner.snapshot("running").state == before.state
    assert owner.snapshot("running").participants == before.participants
    assert owner.snapshot("other") == other
    assert owner.inspect_package_candidate(old.candidate_id) == source
    assert adopt(owner, preview).replayed
    assert owner.package_lock_lineage("running")[0] == old_lock
    assert owner.current_package_lock("running").package_lock == new.package_lock
    database = owner._store._database_path
    owner.close()
    with AuthorityOwner.open(database, "reopened") as reopened:
        assert reopened.snapshot("running").state == before.state
        assert reopened.snapshot("other") == other
        assert adopt(reopened, preview).replayed
        assert reopened.current_package_lock("running").package_lock == new.package_lock


@pytest.mark.parametrize(
    "change",
    [
        lambda r, f: r.update(ui_surfaces=[]),
        lambda r, f: r["ui_surfaces"][0].update(
            source={"kind": "state", "namespace": "clockwork", "key": "different"}
        ),
        lambda r, f: r["tools"][0]["input_schema"]["properties"]["steps"].update(type="string"),
        lambda r, f: r.update(prompt_fragments=[]),
        lambda r, f: r["ui_surfaces"][0].update(title="Renamed panel"),
    ],
)
def test_conflicts_are_explained_and_cannot_be_applied(prepared, change):
    owner, old, _ = prepared
    conflict = _install(owner, (package("bad", change=change),), "conflict")
    before = owner.snapshot("running")
    preview = owner.preview_campaign_rules_update("running", conflict.candidate_id)
    assert not preview["compatible"] and preview["conflicts"]
    with pytest.raises(CommandRejected, match="conflicts"):
        adopt(owner, preview)
    assert owner.snapshot("running") == before
    assert owner.current_package_lock("running").package_lock == old.package_lock


def test_stale_tampered_and_cross_campaign_previews_cannot_apply(prepared):
    owner, old, new = prepared
    preview = owner.preview_campaign_rules_update("running", new.candidate_id)
    with pytest.raises(CommandRejected, match="Preview|preview"):
        adopt(owner, {**preview, "confirmation": "0" * 64})
    cross = {
        **preview,
        "campaign_id": "other",
        "expected_lock_id": owner.current_package_lock("other").lock_id,
    }
    with pytest.raises(CommandRejected):
        adopt(owner, cross)
    owner.rename_campaign(
        operation_id="rename",
        campaign_id="running",
        expected_revision=0,
        display_name="Still the same progress",
    )
    with pytest.raises((CommandRejected, StaleRevision)):
        adopt(owner, preview)
    assert owner.current_package_lock("running").package_lock == old.package_lock


def test_later_open_turn_and_active_work_block_preview_and_activation(prepared):
    owner, old, new = prepared
    reviewed = owner.preview_campaign_rules_update("running", new.candidate_id)
    owner.open_turn("running", "later-turn")
    preview = owner.preview_campaign_rules_update("running", new.candidate_id)
    assert not preview["compatible"] and "Stop Session" in preview["conflicts"][0]["message"]
    with pytest.raises(CommandRejected, match="completed TURN"):
        adopt(owner, reviewed)
    owner.complete_turn("running", "later-turn")
    with owner._campaign_work("running"):
        with pytest.raises(CommandRejected, match="active work"):
            owner.preview_campaign_rules_update("running", new.candidate_id)
        with pytest.raises(CommandRejected, match="active work"):
            adopt(owner, reviewed)
    assert owner.current_package_lock("running").package_lock == old.package_lock


def test_failure_rolls_back_then_retry_reuses_normal_activation_receipt(prepared, monkeypatch):
    owner, old, new = prepared
    preview = owner.preview_campaign_rules_update("running", new.candidate_id)
    before = owner.snapshot("running")
    with monkeypatch.context() as scoped:
        scoped.setattr(
            owner._store,
            "_before_activation_commit",
            lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
        )
        with pytest.raises(RuntimeError, match="synthetic"):
            adopt(owner, preview)
    assert owner.snapshot("running") == before
    assert owner.current_package_lock("running").package_lock == old.package_lock
    adopt(owner, preview)
    assert adopt(owner, preview).replayed
    with pytest.raises(IdempotencyConflict):
        adopt(owner, {**preview, "confirmation": "f" * 64})


def test_recovery_preview_never_restores_old_progress_and_blocks_panel_removal(prepared):
    owner, old, new = prepared
    adopt(owner, owner.preview_campaign_rules_update("running", new.candidate_id))
    before = owner.snapshot("running")
    recovery = owner.preview_campaign_rules_update("running", old.candidate_id)
    assert not recovery["compatible"]  # the added journal would be removed
    with pytest.raises(CommandRejected):
        adopt(owner, recovery, "return-to-old")
    assert owner.snapshot("running") == before
    assert owner.package_candidate(old.candidate_id).package_lock == old.package_lock


def test_actual_lite_snapshot_additive_update_preserves_saved_mechanics(tmp_path):
    from sylvia_gm.application.bundled_packages import _srd_drafts
    from sylvia_gm.application.package_materialization import materialize_package_archive

    drafts = _srd_drafts()
    with AuthorityOwner.open(tmp_path / "lite.db", "lite-update") as owner:
        with materialize_package_archive(drafts) as archive:
            old = owner.install_package_archive(archive.archive, install_id="lite-old")
        primary = next(d for d in drafts if d.record["role"] == "primary")
        guide = "prompts/gm-operating-guide.md"
        changed = replace(
            primary,
            record={**primary.record, "version": "0.3.13-test"},
            payloads={
                **primary.payloads,
                guide: primary.payloads[guide] + b"\nSynthetic additive clarification.\n",
            },
        )
        with materialize_package_archive(
            tuple(changed if d is primary else d for d in drafts)
        ) as archive:
            new = owner.install_package_archive(archive.archive, install_id="lite-new")
        manifest = next(e for e in old.package_lock.entries if e.role.value == "primary")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="lite",
                display_name="Synthetic Lite",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="checkpoint",
                initial_state=(
                    StateValue(
                        manifest.package_id,
                        "state",
                        {
                            "chapter": 3,
                            "advancement": {
                                "method": "fixed",
                                "starting_level": 1,
                                "maximum_level": 1,
                                "recipients": "whole_party",
                                "timing": "No advancement awards.",
                                "allocation": "No advancement awards.",
                            },
                        },
                    ),
                    StateValue(manifest.package_id, "entities", {}),
                    StateValue(manifest.package_id, "combat", {}),
                ),
            )
        )
        owner.complete_turn("lite", "checkpoint")
        before = owner.snapshot("lite")
        preview = owner.preview_campaign_rules_update("lite", new.candidate_id)
        assert preview["compatible"], thaw_json_value(preview["conflicts"])
        adopt(owner, preview)
        assert owner.snapshot("lite").state == before.state


def test_return_to_retained_version_preserves_play_after_adoption(tmp_path):
    from tests.test_alpha_f3_package_activation import _registered_tool

    with AuthorityOwner.open(tmp_path / "recovery.db", "rules-recovery") as owner:
        old_record, old_files = _clockwork("1")
        new_record, new_files = package("2", panel=False)
        old = _install(owner, ((old_record, old_files),), "old")
        new = _install(owner, ((new_record, new_files),), "new")
        owner.register_tool(_registered_tool(old_record))
        owner.register_tool(_registered_tool(new_record))
        campaign(owner, old)
        forward = owner.preview_campaign_rules_update("running", new.candidate_id)
        adopt(owner, forward, "forward")
        owner.open_turn("running", "play-after-update")
        result = owner.execute_action(
            ActionCommand(
                campaign_id="running",
                action_id="advance-after-update",
                idempotency_key="advance",
                owner_id="rules-recovery",
                writer=WriterRole.DM,
                expected_revision=1,
                turn_id="play-after-update",
                package_id="synthetic.clockwork",
                tool_name="advance",
                parameters={"steps": 4},
            )
        )
        assert result.resulting_revision == 2
        assert owner.snapshot("running").state["clockwork.counter"] == 13
        owner.complete_turn("running", "play-after-update")
        before = owner.snapshot("running")
        recovery = owner.preview_campaign_rules_update("running", old.candidate_id)
        assert recovery["compatible"], recovery["conflicts"]
        adopt(owner, recovery, "return-with-current-progress")
        assert owner.snapshot("running").state == before.state
        assert owner.snapshot("running").actions == before.actions
        assert len(owner.package_lock_lineage("running")) == 3
        assert owner.current_package_lock("running").package_lock == old.package_lock
        assert adopt(owner, forward, "forward").replayed
        assert owner.snapshot("running").state["clockwork.counter"] == 13


def test_new_state_validation_rejects_unsafe_saved_values_without_repair(tmp_path):
    from copy import deepcopy

    from sylvia_gm.application.package_materialization import materialize_package_archive
    from tests.test_panel_starting_layout import compile_layout, starting_layout

    original = compile_layout(starting_layout()).draft
    record = deepcopy(original.record)
    record["version"] = "2.0.0"
    record["compatibility"]["requires"].append("starting_state_validation.v1")
    record["state_schemas"] = [{"key": "counter", "schema": {"type": "integer"}, "references": []}]
    changed = replace(original, record=record)

    with AuthorityOwner.open(tmp_path / "unsafe.db", "rules-unsafe") as owner:
        with materialize_package_archive((original,)) as archive:
            old = owner.install_package_archive(archive.archive, install_id="old")
        with materialize_package_archive((changed,)) as archive:
            new = owner.install_package_archive(archive.archive, install_id="new")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="unsafe",
                display_name="Synthetic incompatible state",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="turn",
                initial_state=(StateValue("example.panel-rules", "counter", "invalid saved type"),),
            )
        )
        owner.complete_turn("unsafe", "turn")
        before = owner.snapshot("unsafe")
        preview = owner.preview_campaign_rules_update("unsafe", new.candidate_id)
        assert not preview["compatible"]
        assert "Saved field" in preview["conflicts"][0]["message"]
        assert "invalid saved type" not in str(preview)
        with pytest.raises(CommandRejected):
            adopt(owner, preview)
        assert owner.snapshot("unsafe") == before


def test_owned_setting_snapshot_preserves_characters_source_and_other_campaigns(
    tmp_path, monkeypatch
):
    from copy import deepcopy

    from sylvia_gm.application.package_materialization import materialize_package_archive
    from sylvia_gm.builder._package_snapshots import retained_package_drafts
    from tests.test_builder_library import install_character_packages

    database = tmp_path / "setting.db"
    with AuthorityOwner.open(database, "setting-update") as owner:
        seed, setting_id = install_character_packages(owner)
        current_drafts = retained_package_drafts(owner, seed.candidate_id, include_setting=True)
        with materialize_package_archive(current_drafts) as archive:
            old = owner.install_package_archive(archive.archive, install_id="current-format")
        primary = next(e for e in old.package_lock.entries if e.role.value == "primary")
        manifest = owner._store.exact_package_manifest(
            primary.package_id, primary.version, primary.sha256
        )
        schema = manifest.participant_artifacts[0]
        character = Participant(
            participant_id="operator",
            kind=schema.kind,
            package_id=primary.package_id,
            package_version=primary.version,
            package_sha256=primary.sha256,
            schema_id=schema.schema_id,
            schema_sha256=schema.schema_sha256,
            data={"name": "Synthetic operator", "callsign": "Relay"},
            validation_status="validated",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="setting-campaign",
                display_name="Synthetic owned snapshot",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="checkpoint",
                participants=(character,),
                initial_state=(
                    StateValue(primary.package_id, "state", {"resources": {"charge": 8}}),
                ),
            )
        )
        owner.complete_turn("setting-campaign", "checkpoint")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="other-character",
                display_name="Untouched character campaign",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="other-checkpoint",
                participants=(character,),
            )
        )
        owner.complete_turn("other-character", "other-checkpoint")
        other = owner.snapshot("other-character")
        before = owner.snapshot("setting-campaign")
        source = owner.inspect_package_candidate(old.candidate_id)
        drafts = retained_package_drafts(owner, old.candidate_id, include_setting=True)
        updated = []
        for draft in drafts:
            record = deepcopy(draft.record)
            record["version"] = "2.0.0"
            for dependency in record["dependencies"]:
                dependency["version"] = "2.0.0"
            updated.append(replace(draft, record=record))
        with materialize_package_archive(tuple(updated)) as archive:
            new = owner.install_package_archive(archive.archive, install_id="setting-new")
        unrelated = _install(owner, (_clockwork("9"),), "unrelated")
        choices = owner.campaign_rules_update_options("setting-campaign")["candidates"]
        assert [row["candidate_id"] for row in choices] == [new.candidate_id]
        legacy = owner.preview_campaign_rules_update("setting-campaign", seed.candidate_id)
        assert not legacy["compatible"]
        preview = owner.preview_campaign_rules_update("setting-campaign", new.candidate_id)
        assert preview["compatible"], preview["conflicts"]
        assert setting_id in str(preview["changes"])
        with monkeypatch.context() as rollback:
            rollback.setattr(
                owner._store,
                "_before_activation_commit",
                lambda: (_ for _ in ()).throw(RuntimeError("precommit failed")),
            )
            with pytest.raises(RuntimeError, match="precommit"):
                adopt(owner, preview)
        assert owner.snapshot("setting-campaign") == before
        assert owner.snapshot("other-character") == other
        assert len(owner.package_lock_lineage("setting-campaign")) == 1
        adopt(owner, preview)
        assert owner.snapshot("setting-campaign").state == before.state
        assert owner.snapshot("setting-campaign").participants == before.participants
        assert owner.inspect_package_candidate(old.candidate_id) == source
        active = next(e for e in new.package_lock.entries if e.role.value == "primary")
        outcome = MechanicOutcome(
            result={},
            participant_mutations=(
                ParticipantMutation(
                    "operator",
                    {"name": "Synthetic operator", "callsign": "Updated"},
                ),
            ),
        )

        def owned(campaign_id, entry):
            return owner._store.mechanic_mutations_are_owned(
                campaign_id=campaign_id,
                package_id=entry.package_id,
                package_version=entry.version,
                package_sha256=entry.sha256,
                outcome=outcome,
            )

        assert owned("setting-campaign", active)
        assert not owned("setting-campaign", primary)
        assert not owned("setting-campaign", replace(active, package_id="foreign.rules"))
        assert not owned("setting-campaign", replace(active, sha256="0" * 64))
        assert not owned("other-character", active)
        assert owned("other-character", primary)
        declaration = owner._store.exact_package_manifest(
            active.package_id,
            active.version,
            active.sha256,
        ).tools[0]
        owner.register_tool(
            CatalogTool(
                package_id=active.package_id,
                package_version=active.version,
                package_sha256=active.sha256,
                name=declaration.name,
                capability=declaration.capability,
                input_schema=declaration.input_schema,
                execute=lambda context: MechanicOutcome(
                    result={},
                    participant_mutations=(
                        ParticipantMutation(
                            "operator",
                            {"name": "Synthetic operator", "callsign": "Updated"},
                        ),
                    ),
                ),
            )
        )
        owner.open_turn("setting-campaign", "edit-after-update")
        edited = owner.execute_action(
            ActionCommand(
                campaign_id="setting-campaign",
                action_id="edit-character",
                idempotency_key="edit-character",
                owner_id="setting-update",
                writer=WriterRole.DM,
                expected_revision=owner.snapshot("setting-campaign").revision,
                turn_id="edit-after-update",
                package_id=active.package_id,
                tool_name=declaration.name,
                parameters={"skill": "intrusion", "pressure": 0},
            )
        )
        assert edited.status.value == "committed", edited
        owner.complete_turn("setting-campaign", "edit-after-update")
        assert owner.snapshot("setting-campaign").participants[0].data["callsign"] == "Updated"
        progress = owner.snapshot("setting-campaign")
        recovery = owner.preview_campaign_rules_update("setting-campaign", old.candidate_id)
        assert recovery["compatible"], recovery["conflicts"]
        adopt(owner, recovery, "recover-character-owner")
        assert owned("setting-campaign", primary)
        assert not owned("setting-campaign", active)
        assert owner.snapshot("setting-campaign").participants == progress.participants
        assert owner.snapshot("setting-campaign").actions == progress.actions
        owner.register_tool(
            replace(
                owner._tools[(active.package_id, active.version, active.sha256, declaration.name)],
                package_version=primary.version,
                package_sha256=primary.sha256,
            )
        )
        owner.open_turn("setting-campaign", "edit-after-recovery")
        recovered_edit = owner.execute_action(
            ActionCommand(
                campaign_id="setting-campaign",
                action_id="edit-after-recovery",
                idempotency_key="edit-after-recovery",
                owner_id="setting-update",
                writer=WriterRole.DM,
                expected_revision=owner.snapshot("setting-campaign").revision,
                turn_id="edit-after-recovery",
                package_id=primary.package_id,
                tool_name=declaration.name,
                parameters={"skill": "intrusion", "pressure": 0},
            )
        )
        assert recovered_edit.status.value == "committed", recovered_edit
        owner.complete_turn("setting-campaign", "edit-after-recovery")
        assert owner.snapshot("other-character") == other
        rejected = owner.preview_campaign_rules_update("setting-campaign", unrelated.candidate_id)
        assert not rejected["compatible"]
    with AuthorityOwner.open(database, "setting-reopened") as reopened:
        assert reopened.snapshot("setting-campaign").participants[0].data["callsign"] == "Updated"
        assert reopened.snapshot("setting-campaign").state == before.state
        assert reopened.snapshot("other-character") == other


def test_removed_runtime_record_is_a_conflict_without_state_changes(tmp_path):
    from io import BytesIO

    import yaml

    from tests.test_alpha_f3_package_execution import _archive, _runtime_yaml

    runtime = yaml.safe_load(_runtime_yaml())
    runtime.setdefault("definitions", {})["retained-record"] = {"counter": 1}
    with AuthorityOwner.open(tmp_path / "runtime-removal.db", "removal") as owner:
        old = owner.install_package_archive(
            BytesIO(_archive(runtime_bytes=yaml.safe_dump(runtime).encode())),
            install_id="old-runtime",
        )
        runtime["definitions"].pop("retained-record")
        new = owner.install_package_archive(
            BytesIO(_archive(runtime_bytes=yaml.safe_dump(runtime).encode())),
            install_id="new-runtime",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="runtime",
                display_name="Synthetic retained runtime record",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="turn",
                initial_state=(StateValue("synthetic.clockwork", "state", {"counter": 3}),),
            )
        )
        owner.complete_turn("runtime", "turn")
        before = owner.snapshot("runtime")
        preview = owner.preview_campaign_rules_update("runtime", new.candidate_id)
        assert not preview["compatible"]
        assert any("retained-record" in item["path"] for item in preview["conflicts"])
        with pytest.raises(CommandRejected):
            adopt(owner, preview)
        assert owner.snapshot("runtime") == before
