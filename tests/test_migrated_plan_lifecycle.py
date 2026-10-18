"""Every migrated authored Plan starts, persists and recovers through ordinary commands.

Only character-provider replies are scripted. No gameplay state or story is injected.
Native Windows input and genuine providers are separate evidence layers.
"""
from pathlib import Path
import pytest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.domain.authority import thaw_json_value
from tests.test_fc_campaign_journey import ROOT, _start_campaign
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

PLANS = sorted(path.parent.name for path in (ROOT / "campaign-plans").glob("*/plan.json"))
assert len(PLANS) == 17

@pytest.mark.parametrize("slug", PLANS)
def test_migrated_plan_start_save_reopen_export_recover_and_story_equivalence(tmp_path, slug):
    original = bundled_plan(slug)
    assert original is not None
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
