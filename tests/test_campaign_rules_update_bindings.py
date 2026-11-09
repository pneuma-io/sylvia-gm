"""Accepted Plans keep immutable provenance while active reads follow explicit updates."""

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._package_snapshots import retained_package_drafts
from sylvia_gm.domain.authority import MechanicOutcome, StateMutation, StateValue, WriterRole
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_FORMAT_VERSION
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    ActivationCommand,
    canonical_json_bytes,
)
from tests.test_builder_library import install_character_packages
from tests.test_campaign_rules_update import adopt


def install_variant(owner, drafts, version, *, change=None):
    updated = []
    for draft in drafts:
        record = deepcopy(draft.record)
        record["version"] = version
        for dependency in record["dependencies"]:
            dependency["version"] = version
        payloads = dict(draft.payloads)
        if change:
            change(record, payloads)
        updated.append(replace(draft, record=record, payloads=payloads))
    with materialize_package_archive(tuple(updated)) as archive:
        return owner.install_package_archive(archive.archive, install_id=f"variant-{version}")


@pytest.fixture
def plan_setup(tmp_path):
    database = tmp_path / "accepted-plan.db"
    with AuthorityOwner.open(database, "plan-update") as owner:
        seed, setting_id = install_character_packages(owner)
        drafts = retained_package_drafts(owner, seed.candidate_id, include_setting=True)

        def add_references(record, payloads):
            path = (
                "rules/guide.md" if record["role"] == "primary" else CAMPAIGN_SETTING_KNOWLEDGE_PATH
            )
            if record["role"] == "primary":
                payloads[path] = b"# Signal guide\n\nSynthetic retained reference.\n"
            else:
                payloads[path] = canonical_json_bytes(
                    {
                        "format_id": "sylvia-gm-campaign-setting-knowledge",
                        "format_version": 1,
                        "facts": [
                            {
                                "id": "relay.lamp",
                                "fact": "The relay has a blue lamp.",
                                "known_by": [],
                                "source_path": "locations/relay.md",
                            }
                        ],
                        "private_references": [],
                    }
                )
            declarations = json.loads(payloads[record["content_declarations"]])
            links = declarations["inclusions"][0]["links"]["artifacts"]
            links.append(path)
            links.sort()
            payloads[record["content_declarations"]] = canonical_json_bytes(declarations)

        old = install_variant(owner, drafts, "1.0.0", change=add_references)
        primary = next(e for e in old.package_lock.entries if e.role.value == "primary")
        setting = next(e for e in old.package_lock.entries if e.package_id == setting_id)
        state = {"resources": {"charge": 8}}
        confirmation = owner.confirm_new_campaign(
            operation_id="confirm",
            display_name="Synthetic accepted Plan",
            package_lock=old.package_lock,
            turn_id="construction",
            package_candidate_id=old.candidate_id,
            builder_session_id="builder",
            builder_provider_id="fixture",
            builder_model_id="fixture",
            participants=(),
            initial_state=(StateValue(primary.package_id, "state", state),),
        )
        campaign_id = confirmation.campaign_id
        owner.begin_campaign_construction(
            operation_id="begin",
            campaign_id=campaign_id,
            lane="quick_start",
            expected_revision=0,
            canonical_input={"premise": "Disposable reference fixture"},
        )
        lock = owner.current_package_lock(campaign_id)
        _, _, handoff = owner.finalize_campaign_construction(
            campaign_id=campaign_id,
            dm_provider_id="fixture",
            dm_model_id="fixture",
            plan_artifact_text="# Synthetic accepted Plan",
            model_guidance_override=True,
            structured_plan={
                "format_id": "sylvia-gm-campaign-plan",
                "format_version": CAMPAIGN_PLAN_FORMAT_VERSION,
                "bindings": {
                    "ruleset": {
                        "id": primary.package_id,
                        "version": primary.version,
                        "content_sha256": primary.sha256,
                    },
                    "campaign_setting": {
                        "id": setting.package_id,
                        "version": setting.version,
                        "content_sha256": setting.sha256,
                    },
                    "package_closure_sha256": lock.closure_sha256,
                },
                "initial_state": {
                    "mechanics": [{"namespace": primary.package_id, "key": "state", "value": state}]
                },
            },
        )
        for turn, status in owner.snapshot(campaign_id).turns.items():
            if status.value == "open":
                owner.complete_turn(campaign_id, turn)
        original = retained_package_drafts(owner, old.candidate_id, include_setting=True)
        new = install_variant(owner, original, "2.0.0")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="other",
                display_name="Untouched synthetic campaign",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="other-turn",
                initial_state=(StateValue(primary.package_id, "state", state),),
            )
        )
        owner.complete_turn("other", "other-turn")
        yield SimpleNamespace(
            owner=owner,
            database=database,
            campaign_id=campaign_id,
            session_id=handoff.dm_session_id,
            old=old,
            new=new,
            drafts=original,
        )


def assert_plan_consumers(owner, fixture):
    for source, path in (("ruleset", "rules/guide.md"), ("campaign_setting", "locations/relay.md")):
        reference = owner.read_campaign_reference(fixture.campaign_id, source=source, path=path)
        assert reference["content"]
        current = next(
            e
            for e in owner.snapshot(fixture.campaign_id).package_lock.entries
            if e.package_id == reference["package"]["id"]
        )
        assert reference["package"]["version"] == current.version
        assert reference["package"]["content_sha256"] == current.sha256
        query = "retained reference" if source == "ruleset" else "relay"
        search = owner.search_campaign_knowledge(
            fixture.campaign_id, query=query, sources=(source,)
        )
        assert search["matches"]
    catalog = owner.campaign_knowledge_catalog(fixture.session_id)
    assert any(fact["id"] == "relay.lamp" for fact in catalog["secrets"])


def test_real_accepted_plan_reads_survive_adoption_play_recovery_and_reopen(plan_setup):
    fixture, owner = plan_setup, plan_setup.owner
    before = owner.snapshot(fixture.campaign_id)
    other = owner.snapshot("other")
    accepted = owner.accepted_campaign_plan(fixture.campaign_id)
    source = owner.inspect_package_candidate(fixture.old.candidate_id)
    assert_plan_consumers(owner, fixture)
    preview = owner.preview_campaign_rules_update(fixture.campaign_id, fixture.new.candidate_id)
    assert preview["compatible"], preview["conflicts"]
    adopt(owner, preview)
    assert_plan_consumers(owner, fixture)
    assert owner.snapshot(fixture.campaign_id).state == before.state
    primary = next(e for e in fixture.new.package_lock.entries if e.role.value == "primary")
    declaration = owner._store.exact_package_manifest(
        primary.package_id,
        primary.version,
        primary.sha256,
    ).tools[0]
    owner.register_tool(
        CatalogTool(
            package_id=primary.package_id,
            package_version=primary.version,
            package_sha256=primary.sha256,
            name=declaration.name,
            capability=declaration.capability,
            input_schema=declaration.input_schema,
            execute=lambda context: MechanicOutcome(
                result={},
                mutations=(
                    StateMutation(
                        primary.package_id,
                        "state",
                        {"resources": {"charge": 9}},
                    ),
                ),
            ),
        )
    )
    owner.open_turn(fixture.campaign_id, "later-play")
    played = owner.execute_action(
        ActionCommand(
            campaign_id=fixture.campaign_id,
            action_id="play-after-update",
            idempotency_key="play",
            owner_id="plan-update",
            writer=WriterRole.DM,
            expected_revision=owner.snapshot(fixture.campaign_id).revision,
            turn_id="later-play",
            package_id=primary.package_id,
            tool_name=declaration.name,
            parameters={"skill": "intrusion", "pressure": 0},
        )
    )
    assert played.status.value == "committed", played
    owner.complete_turn(fixture.campaign_id, "later-play")
    progress = owner.snapshot(fixture.campaign_id)
    assert progress.state[f"{primary.package_id}.state"]["resources"]["charge"] == 9
    assert_plan_consumers(owner, fixture)
    recovery = owner.preview_campaign_rules_update(fixture.campaign_id, fixture.old.candidate_id)
    assert recovery["compatible"], recovery["conflicts"]
    adopt(owner, recovery, "recover-original")
    assert_plan_consumers(owner, fixture)
    assert owner.snapshot(fixture.campaign_id).state == progress.state
    assert owner.snapshot(fixture.campaign_id).actions == progress.actions
    assert owner.snapshot("other") == other
    assert owner.accepted_campaign_plan(fixture.campaign_id) == accepted
    assert (
        owner.inspect_package_candidate(fixture.old.candidate_id)["packages"] == source["packages"]
    )
    owner.close()
    with AuthorityOwner.open(fixture.database, "plan-reopen") as reopened:
        assert_plan_consumers(reopened, fixture)
        assert reopened.accepted_campaign_plan(fixture.campaign_id) == accepted
        assert reopened.snapshot("other") == other


@pytest.mark.parametrize("removed", ["fact", "fact-source", "fact-audience", "content", "link"])
def test_structured_content_removals_explain_conflicts_and_retain_exact_authority(
    plan_setup, removed
):
    fixture, owner = plan_setup, plan_setup.owner

    def remove(record, payloads):
        if record["role"] == "primary":
            return
        if removed.startswith("fact"):
            data = json.loads(payloads[CAMPAIGN_SETTING_KNOWLEDGE_PATH])
            if removed == "fact":
                data["facts"] = []
            elif removed == "fact-source":
                data["facts"][0]["source_path"] = "characters/operator.md"
            else:
                data["facts"][0]["known_by"] = ["public"]
            payloads[CAMPAIGN_SETTING_KNOWLEDGE_PATH] = canonical_json_bytes(data)
        else:
            path = record["content_declarations"]
            data = json.loads(payloads[path])
            if removed == "content":
                data["inclusions"][0]["id"] = "replacement-setting"
            else:
                replacement = deepcopy(data["inclusions"][0])
                replacement["id"] = "replacement-reference"
                replacement["links"]["artifacts"] = ["characters/operator.md"]
                data["inclusions"][0]["links"]["artifacts"].remove("characters/operator.md")
                data["inclusions"].append(replacement)
                data["inclusions"].sort(key=lambda item: item["id"])
            payloads[path] = canonical_json_bytes(data)

    changed = install_variant(owner, fixture.drafts, "3.0.0", change=remove)
    before = owner.snapshot(fixture.campaign_id)
    preview = owner.preview_campaign_rules_update(fixture.campaign_id, changed.candidate_id)
    assert not preview["compatible"] and preview["conflicts"]
    with pytest.raises(CommandRejected, match="conflicts"):
        adopt(owner, preview)
    assert owner.snapshot(fixture.campaign_id) == before
    assert_plan_consumers(owner, fixture)


def test_actual_lite_level_up_after_update_retains_earned_progress_on_recovery(tmp_path):
    from tests.test_srd_advancement import build, command, create, resolve

    with AuthorityOwner.open(tmp_path / "lite-level-up.db", "test") as owner:
        create(owner)
        before = owner.snapshot("advancement")
        old = next(c for c in owner.package_candidates() if c.package_lock == before.package_lock)
        drafts = retained_package_drafts(owner, old.candidate_id, include_setting=True)
        new = install_variant(owner, drafts, "2.0.0")
        owner.complete_turn("advancement", "turn")
        preview = owner.preview_campaign_rules_update("advancement", new.candidate_id)
        assert preview["compatible"], preview["conflicts"]
        adopt(owner, preview)
        owner.open_turn("advancement", "after-update")
        award = owner.execute_action(
            replace(
                command(
                    owner,
                    "award_srd_advancement",
                    {
                        "award_id": "rescue",
                        "xp": 0,
                        "evidence": "The synthetic rescue was resolved.",
                    },
                    "earned-after-update",
                ),
                turn_id="after-update",
            )
        )
        assert award.status.value == "committed", award
        waiting = owner.execute_action(
            replace(
                command(owner, "level_up_srd_character", build(owner), "level-after-update"),
                turn_id="after-update",
            )
        )
        applied = resolve(owner, waiting)
        assert applied.status.value == "committed", applied
        progress = owner.snapshot("advancement")
        assert progress.participants[0].data["level"] == 2
        assert progress.participants[0].package_version == before.participants[0].package_version
        owner.complete_turn("advancement", "after-update")
        recovery = owner.preview_campaign_rules_update("advancement", old.candidate_id)
        assert recovery["compatible"], recovery["conflicts"]
        adopt(owner, recovery, "recover-lite")
        assert owner.snapshot("advancement").participants == progress.participants
        assert owner.snapshot("advancement").actions == progress.actions


def test_generic_activation_and_other_campaign_history_do_not_confer_compatible_authority(
    plan_setup,
):
    fixture, owner = plan_setup, plan_setup.owner
    primary = next(e for e in fixture.old.package_lock.entries if e.role.value == "primary")
    before = owner.snapshot(fixture.campaign_id)
    owner.activate_package(
        ActivationCommand(
            fixture.campaign_id,
            fixture.new.candidate_id,
            "generic-migration",
            before.revision,
            owner.current_package_lock(fixture.campaign_id).lock_id,
            "arbitrary-migration",
        ),
        lambda state: state,
    )
    assert (
        owner._store.compatible_package_binding(
            fixture.campaign_id,
            (primary.package_id, primary.version, primary.sha256),
        )
        is None
    )
    assert (
        owner._store.compatible_package_binding(
            "other",
            (primary.package_id, "2.0.0", "0" * 64),
        )
        is None
    )
    with pytest.raises(CommandRejected, match="binding is stale"):
        owner.read_campaign_reference(fixture.campaign_id, source="ruleset", path="rules/guide.md")
    with pytest.raises(CommandRejected, match="binding is stale"):
        owner.campaign_knowledge_catalog(fixture.session_id)


def test_generic_activation_cannot_spoof_the_compatible_update_marker(plan_setup):
    fixture, owner = plan_setup, plan_setup.owner

    def remove_fact(record, payloads):
        if CAMPAIGN_SETTING_KNOWLEDGE_PATH in payloads:
            knowledge = json.loads(payloads[CAMPAIGN_SETTING_KNOWLEDGE_PATH])
            knowledge["facts"] = []
            payloads[CAMPAIGN_SETTING_KNOWLEDGE_PATH] = canonical_json_bytes(knowledge)

    unsafe = install_variant(owner, fixture.drafts, "3.0.0", change=remove_fact)
    before = owner.snapshot(fixture.campaign_id)
    preview = owner.preview_campaign_rules_update(fixture.campaign_id, unsafe.candidate_id)
    assert not preview["compatible"]
    command = ActivationCommand(
        fixture.campaign_id,
        unsafe.candidate_id,
        "spoof-compatible-marker",
        before.revision,
        owner.current_package_lock(fixture.campaign_id).lock_id,
        f"compatible-update-{preview['confirmation']}",
    )
    calls = []

    def migrate(state):
        calls.append("called")
        return state

    with pytest.raises(CommandRejected, match="reserved"):
        owner.activate_package(command, migrate)
    assert not calls
    assert owner.snapshot(fixture.campaign_id) == before
    assert owner._store.activation_hit(command.activation_id) is None


def test_marker_only_legacy_receipt_never_confers_compatible_binding_authority(plan_setup):
    fixture, owner = plan_setup, plan_setup.owner
    preview = owner.preview_campaign_rules_update(fixture.campaign_id, fixture.new.candidate_id)
    receipt = adopt(owner, preview)
    assert_plan_consumers(owner, fixture)
    connection = owner._store._connect()
    try:
        stored = connection.execute(
            "SELECT result_json FROM package_activations WHERE activation_id = ?",
            (receipt.activation_id,),
        ).fetchone()
        result = json.loads(stored["result_json"])
        assert result.pop("authority") == "campaign-compatible-update.v1"
        connection.execute(
            "UPDATE package_activations SET result_json = ? WHERE activation_id = ?",
            (canonical_json_bytes(result).decode(), receipt.activation_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(CommandRejected, match="binding is stale"):
        owner.read_campaign_reference(fixture.campaign_id, source="ruleset", path="rules/guide.md")
    owner.close()
    with AuthorityOwner.open(fixture.database, "legacy-marker-reopen") as reopened:
        assert reopened.snapshot(fixture.campaign_id).revision == receipt.resulting_revision
        with pytest.raises(CommandRejected, match="binding is stale"):
            reopened.campaign_knowledge_catalog(fixture.session_id)


def test_owner_stamped_compatible_authority_survives_backup_export_and_recovery(plan_setup):
    fixture, owner = plan_setup, plan_setup.owner
    preview = owner.preview_campaign_rules_update(fixture.campaign_id, fixture.new.candidate_id)
    adopt(owner, preview)
    expected = owner.snapshot(fixture.campaign_id)
    assert_plan_consumers(owner, fixture)
    archive = fixture.database.parent / "compatible-update.sylvia-gm-campaign"
    backup = fixture.database.parent / "compatible-update-backup.sqlite3"
    owner.export_campaign("export-compatible", fixture.campaign_id, archive)
    owner.backup_database(backup)
    owner.close()
    with AuthorityOwner.open(backup, "compatible-backup") as restored:
        assert restored.snapshot(fixture.campaign_id) == expected
        assert_plan_consumers(restored, fixture)
    with AuthorityOwner.open(
        fixture.database.parent / "recovered.sqlite3", "compatible-recovery"
    ) as recovered:
        recovered.recover_campaign(recovery_id="recover-compatible", source=archive)
        assert recovered.snapshot(fixture.campaign_id) == expected
        assert_plan_consumers(recovered, fixture)
        assert adopt(recovered, preview).replayed
