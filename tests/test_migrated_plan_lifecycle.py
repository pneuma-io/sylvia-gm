"""Every migrated authored Plan starts, persists and recovers through ordinary commands.

Only character-provider replies are scripted. No gameplay state or story is injected.
Native Windows input and genuine providers are separate evidence layers.
"""
import json
import pytest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from tests.test_fc_campaign_journey import ROOT, SRD, _start_campaign
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_riichi_quick_test_plan import RIICHI_PLANS

MIGRATED_SRD_PLANS = (
    "a-debt-in-iron",
    "a-light-on-breathing-street",
    "dead-inventory",
    "hold-the-rope",
    "last-cart-through",
    "no-one-left-below",
    "rift-awakening",
    "the-bell-below",
    "the-cartographers-wake",
    "the-company-of-the-lost",
    "the-honest-thief",
    "the-long-way-round",
    "the-map-of-tomorrow",
    "the-quarry-turns",
    "the-rift-awakening",  # Retained legacy alias has its own exact closure.
    "the-scavengers-accord",
    "the-sparring-debt",
)


def test_every_bundled_plan_has_ruleset_specific_lifecycle_coverage():
    plans = {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "campaign-plans").glob("*/plan.json")
    }
    assert set(MIGRATED_SRD_PLANS).isdisjoint(RIICHI_PLANS)
    assert set(plans) == set(MIGRATED_SRD_PLANS) | set(RIICHI_PLANS)
    for slugs, ruleset_id in (
        (MIGRATED_SRD_PLANS, SRD), (RIICHI_PLANS, "pneuma.riichi-mahjong"),
    ):
        for slug in slugs:
            assert plans[slug]["bindings"]["ruleset"]["id"] == ruleset_id, slug


@pytest.mark.parametrize("slug", MIGRATED_SRD_PLANS)
def test_migrated_plan_start_save_reopen_export_recover_and_story_equivalence(tmp_path, slug):
    original = bundled_plan(slug)
    assert original is not None
    assert original.plan.payload["bindings"]["ruleset"]["id"] == SRD
    selected = slug
    database = tmp_path / "campaign.sqlite3"
    # The retained legacy alias remains accessible to saved references. Import its
    # exact portable file to select it through today's ordinary new-campaign chooser.
    if slug == "the-rift-awakening":
        harness = _OwnerHarness(database, None)
        try:
            exported = harness.request("export-legacy", "export_campaign_plan_file", {
                "export_id": "legacy-plan-proof", "plan_id": slug,
                "expected_content_sha256": original.content_sha256,
            })
            imported = harness.request("import-legacy", "install_package_file", {
                "install_id": "legacy-plan-proof", "source_path": exported["artifact_path"],
            })
            selected = imported["id"]
        finally:
            harness.close()
    minimum = original.plan.payload["starting_party"]["custom_companions"]["minimum"]
    state = next(item["value"] for item in original.plan.payload["initial_state"]["mechanics"]
                 if item["key"] == "state")
    starting_level = state.get("advancement", {}).get("starting_level", 2)
    campaign, session, turn = _start_campaign(database, selected, with_companion=minimum > 0,
                                           character_level=starting_level)
    with AuthorityOwner.open(database, "plan-lifecycle-proof") as owner:
        saved = owner.snapshot(campaign)
        accepted = thaw_json_value(owner.accepted_campaign_plan(campaign).content)
        plan = accepted["campaign_plan"]
        # These authored story fields are compared exactly, including completion,
        # rewards, branches and quests wherever this Plan declares them.
        for field in ("structure", "objectives", "premise", "tone", "completion", "rewards",
                      "campaign_completion", "unsupported_mechanics", "setting_refs", "title",
                      "summary", "setting_context"):
            if field in original.plan.payload:
                assert plan[field] == original.plan.payload[field], (slug, field)
        assert saved.participants
        assert any(entry.package_id == SRD for entry in saved.package_lock.entries)
        assert all(entry.package_id != "wotc.srd.5.2.1" for entry in saved.package_lock.entries)
        assert next(row for row in owner.campaign_index() if row.campaign_id == campaign).playable
        archive = tmp_path / "started-campaign.sylvia-gm-campaign"
        owner.export_campaign(operation_id="export-started", campaign_id=campaign, target=archive)
    with AuthorityOwner.open(database, "plan-lifecycle-reopen") as owner:
        assert owner.snapshot(campaign) == saved
        assert thaw_json_value(owner.accepted_campaign_plan(campaign).content) == accepted
    with AuthorityOwner.open(tmp_path / "recovered.sqlite3", "plan-lifecycle-recover") as owner:
        recovered = owner.recover_campaign(recovery_id="recover-started", source=archive)
        restored = owner.snapshot(recovered.campaign_id)
        assert restored.state == saved.state
        assert restored.participants == saved.participants
        assert thaw_json_value(owner.accepted_campaign_plan(recovered.campaign_id).content) == accepted


def test_retained_rift_validates_its_owned_world_without_current_base_world(monkeypatch):
    import hashlib
    import io
    import zipfile

    from sylvia_gm.application import bundled_campaign_plans, bundled_packages

    def unavailable():
        raise AssertionError("A retained Plan must not select the current base Setting")

    monkeypatch.setattr(bundled_packages, "bundled_rift_authority", unavailable)
    bundled_campaign_plans._bundled_plan.cache_clear()
    try:
        validated = bundled_packages.bundled_rift_campaign_plan()
        retained = bundled_plan("the-rift-awakening")
        assert retained.plan == validated
        assert validated.payload["starting_party"]["custom_companions"] == {
            "minimum": 0, "maximum": 5,
        }
        state = next(row["value"] for row in validated.payload["initial_state"]["mechanics"]
                     if row["key"] == "state")
        assert state["advancement"]["method"] == "fixed"
        assert state["advancement"]["maximum_level"] == 1
        with zipfile.ZipFile(io.BytesIO(retained.package_snapshot)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            world = next(p for p in manifest["packages"] if p["content_kind"] == "campaign_setting")
            hashes = {f["path"]: hashlib.sha256(archive.read(
                f"packages/{world['id']}/{f['path']}"
            )).hexdigest() for f in world["files"]}
            assert len(hashes) == 279
            fingerprint = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
            assert hashlib.sha256(fingerprint).hexdigest() == (
                "5624a392bc452246e3dda8f8b916920375b2835d464d97d1c5418b879bfc364e"
            )
        public = bundled_plan("rift-awakening")
        assert public.plan.payload["initial_state"] != validated.payload["initial_state"]
    finally:
        bundled_campaign_plans._bundled_plan.cache_clear()


def test_retained_rift_direct_selection_installs_its_owned_closure(tmp_path):
    from sylvia_gm.interfaces.owner_process import _resolve_rift_library_selection

    retained = bundled_plan("the-rift-awakening")
    database = tmp_path / "retained-selection.sqlite3"
    with AuthorityOwner.open(database, "retained-selection") as owner:
        owner.set_admin_full_audit(True)
        candidate, sources, plan, template = _resolve_rift_library_selection(
            owner, tmp_path, ruleset_selection=SRD,
            setting_id="pneuma.severed-lands", plan_id="the-rift-awakening",
        )
        bindings = retained.plan.payload["bindings"]
        assert candidate.closure_sha256 == bindings["package_closure_sha256"]
        assert plan == retained.plan
        assert sources.ruleset.as_payload() == plan.payload["bindings"]["ruleset"]
        assert template is not None


def test_retained_rift_builder_references_preserve_hidden_owned_sources(tmp_path):
    from sylvia_gm.application.builder_references import (
        capture_content_references,
        reader_for_references,
    )
    from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog

    retained = bundled_plan("the-rift-awakening")
    with AuthorityOwner.open(tmp_path / "retained-builder.sqlite3", "retained-builder") as owner:
        owner.set_admin_full_audit(True)
        catalog = _campaign_creation_catalog(tmp_path, owner, include_participants=False)
        assert not any(row.get("id") == "the-rift-awakening" for row in catalog)
        references = capture_content_references(
            owner, tmp_path, catalog, ruleset_id=SRD,
            setting_id="pneuma.severed-lands", plan_id="the-rift-awakening",
        )
        assert references["plan"]["payload"] == retained.plan.payload
        assert references["plan"]["content_sha256"] == retained.content_sha256
        assert references["plan"]["selection_id"] == "the-rift-awakening"
        candidate = owner.package_candidate(references["candidate_id"])
        bindings = retained.plan.payload["bindings"]
        assert candidate.closure_sha256 == bindings["package_closure_sha256"]
        reader = reader_for_references(owner, references)
        assert reader.read("campaign-plan/plan.md") == retained.plan.readable_markdown
        assert not any(row.get("id") == "the-rift-awakening" for row in catalog)
        with pytest.raises(CommandRejected, match="Plan reference is unavailable"):
            capture_content_references(
                owner, tmp_path, catalog, ruleset_id=SRD,
                setting_id="pneuma.severed-lands", plan_id="unadvertised-plan",
            )


def test_retained_rift_legacy_reusable_copy_resolves_its_exact_available_snapshot(tmp_path):
    from sylvia_gm.application.builder_references import capture_content_references
    from sylvia_gm.application.bundled_packages import bundled_rift_archive_for_closure
    from sylvia_gm.application.campaign_plan_sources import validate_retained_campaign_plan
    from sylvia_gm.application.interview_content import resolve_interview_content
    from sylvia_gm.application.plan_snapshot import retained_plan_snapshot, validate_plan_snapshot
    from sylvia_gm.application.reusable_plans import (
        load_reusable_campaign_plan,
        save_reusable_rift_plan,
    )
    from sylvia_gm.interfaces.owner_process import (
        _campaign_creation_catalog,
        _resolve_rift_library_selection,
    )

    retained = bundled_plan("the-rift-awakening")
    saved = save_reusable_rift_plan(
        tmp_path, operation_id="retained-legacy-copy", title="Retained Rift Copy",
        source_payload=retained.plan.payload,
        source_markdown=retained.plan.readable_markdown,
    )
    assert saved.package_snapshot is None
    record_path = tmp_path / "campaign-plans" / saved.artifact_id / "record.json"
    record_bytes = record_path.read_bytes()
    record = json.loads(record_bytes)
    assert record["format_version"] == 2
    assert saved.plan.payload["bindings"] == retained.plan.payload["bindings"]
    assert load_reusable_campaign_plan(tmp_path, saved.artifact_id) == saved
    with AuthorityOwner.open(tmp_path / "retained-copy.sqlite3", "retained-copy") as owner:
        owner.set_admin_full_audit(True)
        snapshot = retained_plan_snapshot(owner, saved.plan.payload)
        assert validate_plan_snapshot(
            snapshot, saved.plan.payload, saved.plan.readable_markdown,
        ) == saved.plan
        candidate, sources, plan, template = _resolve_rift_library_selection(
            owner, tmp_path, ruleset_selection=SRD,
            setting_id="pneuma.severed-lands", plan_id=saved.artifact_id,
        )
        assert plan == saved.plan
        bindings = retained.plan.payload["bindings"]
        assert candidate.closure_sha256 == bindings["package_closure_sha256"]
        assert sources.setting.as_payload() == saved.plan.payload["bindings"]["campaign_setting"]
        assert template is not None
        unknown = thaw_json_value(saved.plan.payload)
        unknown["bindings"]["package_closure_sha256"] = "0" * 64
        assert bundled_rift_archive_for_closure("0" * 64) is None
        with pytest.raises(ValueError, match="exact packages are unavailable"):
            validate_retained_campaign_plan(owner, unknown, saved.plan.readable_markdown)
        with pytest.raises(ValueError, match="retained packages are unavailable"):
            retained_plan_snapshot(owner, unknown)
    interview_database = tmp_path / "retained-interview.sqlite3"
    with AuthorityOwner.open(interview_database, "retained-interview") as owner:
        owner.set_admin_full_audit(True)
        content = resolve_interview_content(owner, tmp_path, {
            "ruleset": {"candidate_id": SRD},
            "setting": {"setting_id": "pneuma.severed-lands"},
            "plan": {"plan_id": saved.artifact_id},
        })
        assert content.candidate.closure_sha256 == bindings["package_closure_sha256"]
    references_database = tmp_path / "retained-references.sqlite3"
    with AuthorityOwner.open(references_database, "retained-references") as owner:
        owner.set_admin_full_audit(True)
        catalog = _campaign_creation_catalog(tmp_path, owner, include_participants=False)
        references = capture_content_references(
            owner, tmp_path, catalog, ruleset_id=SRD,
            setting_id="pneuma.severed-lands", plan_id=saved.artifact_id,
        )
        assert references["plan"]["payload"] == saved.plan.payload
        candidate = owner.package_candidate(references["candidate_id"])
        assert candidate.closure_sha256 == bindings["package_closure_sha256"]
    assert load_reusable_campaign_plan(tmp_path, saved.artifact_id) == saved
    assert record_path.read_bytes() == record_bytes


def test_retained_rift_saved_campaign_never_rebinds_from_current_builtin(tmp_path, monkeypatch):
    from sylvia_gm.application import bundled_campaign_plans, bundled_packages

    database = tmp_path / "pinned.sqlite3"
    retained = bundled_plan("the-rift-awakening")
    harness = _OwnerHarness(database, None)
    try:
        exported = harness.request("export-pinned", "export_campaign_plan_file", {
            "export_id": "pinned-plan", "plan_id": "the-rift-awakening",
            "expected_content_sha256": retained.content_sha256,
        })
        imported = harness.request("import-pinned", "install_package_file", {
            "install_id": "pinned-plan", "source_path": exported["artifact_path"],
        })
    finally:
        harness.close()
    campaign, _, _ = _start_campaign(database, imported["id"], with_companion=False,
                                    character_level=1)
    archive = tmp_path / "pinned.sylvia-gm-campaign"
    with AuthorityOwner.open(database, "capture-retained") as owner:
        owner.set_admin_full_audit(True)
        saved = owner.snapshot(campaign)
        accepted = thaw_json_value(owner.accepted_campaign_plan(campaign).content)

    def changed_builtin(*_args, **_kwargs):
        raise AssertionError("A saved campaign must use its stored Plan and package pins")

    with AuthorityOwner.open(database, "reopen-retained") as owner:
        owner.set_admin_full_audit(True)
        with monkeypatch.context() as patched:
            patched.setattr(bundled_campaign_plans, "_bundled_plan", changed_builtin)
            patched.setattr(bundled_packages, "bundled_rift_campaign_plan", changed_builtin)
            assert owner.snapshot(campaign) == saved
            assert thaw_json_value(owner.accepted_campaign_plan(campaign).content) == accepted
            owner.export_campaign(
                operation_id="export-pinned", campaign_id=campaign, target=archive,
            )
    with AuthorityOwner.open(tmp_path / "recovered-pinned.sqlite3", "recover-retained") as owner:
        owner.set_admin_full_audit(True)
        restored_id = owner.recover_campaign(
            recovery_id="recover-pinned", source=archive,
        ).campaign_id
        restored = owner.snapshot(restored_id)
        assert restored.package_lock == saved.package_lock
        assert restored.state == saved.state
        assert restored.participants == saved.participants
        assert thaw_json_value(owner.accepted_campaign_plan(restored_id).content) == accepted
