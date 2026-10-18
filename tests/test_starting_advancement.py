"""Starting levels queue normal campaign-local progression; reusable cards remain base level."""
import pytest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.application.campaign_library_selection import materialize_library_party, resolve_campaign_library_selection
from sylvia_gm.application.reusable_participants import save_reusable_participant, load_reusable_participant
from tests.test_reusable_participants import _participant, _participant_authority, _starting_state


def test_level_one_cards_queue_the_plan_start_for_player_and_companion_without_mutation(tmp_path):
    package, declaration = _participant_authority()
    setting = bundled_rift_authority().setting
    saved = tuple(save_reusable_participant(
        tmp_path, operation_id=role, role=role, summary="A synthetic fighter.",
        authored_profile="A voluntary exhibition fighter.", participant=_participant(role),
        entity_state=_starting_state(), package=package, declaration=declaration,
        campaign_setting=setting,
    ) for role in ("player_character", "companion"))
    with AuthorityOwner.open(tmp_path / "campaign.db", "test") as owner:
        selection = resolve_campaign_library_selection(owner, tmp_path,
            candidate_id=package.package_id, setting_id=setting.package_id, plan_id="the-sparring-debt")
        party, state, _ = materialize_library_party(owner, selection, operation_id="start",
            player_character_id=saved[0].artifact_id, companion_ids=(saved[1].artifact_id,))
        entities = next(row.value for row in state if row.key == "entities")
        for participant, original in zip(party, saved, strict=True):
            assert participant.data["level"] == original.participant["level"] == 1
            entity = entities[participant.participant_id]
            assert entity["initial_advancement_queue"] == [
                {"from_level": 1, "target_level": 2, "intent": "level_up_srd_character"}]
            assert entity["initial_advancement_target"] == entity["advancement_target"] == 2
            assert entity["hp"] == original.entity_state["hp"]
            assert entity["resources"] == original.entity_state["resources"]
    for original in saved:
        assert load_reusable_participant(tmp_path, original.artifact_id, package=package,
            declaration=declaration, campaign_setting=setting) == original
