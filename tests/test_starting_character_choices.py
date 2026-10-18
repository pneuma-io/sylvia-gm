"""Plan placement cannot invalidate a selected Character File's declared choices."""

import json
from dataclasses import replace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_reusable_participants import _participant, _plan_participant_authority, _starting_state
from tests.test_srd_spell_advancement import WIZARD_BOOK, WIZARD_SPELLS


@pytest.mark.parametrize("placement", ["invalid_choices", "injury", "invalid_hp", "invalid_world"])
def test_plan_placement_checks_final_choices_without_resetting_starting_injury(
    tmp_path, placement,
):
    package, declaration, setting = _plan_participant_authority()
    profile = _participant("Vela")
    profile.pop("fighting_styles")
    spells = WIZARD_SPELLS + ["mage_armor"]
    profile.update(level=2, classes=[{"id": "wizard", "level": 2}],
                   wizard_spellbook=WIZARD_BOOK + ["longstrider", "false_life"],
                   spellcasting_grants=[{"source": "wizard", "ability": "intelligence",
                                        "spells": spells}])
    authored = _starting_state()
    authored["prepared_spells"] = spells
    saved = save_reusable_participant(
        tmp_path, operation_id="wizard", role="player_character", summary="An apprentice.",
        authored_profile="An apprentice from the harbor.", participant=profile,
        entity_state=authored, package=package, declaration=declaration, campaign_setting=setting,
    )
    with AuthorityOwner.open(tmp_path / "campaign.db", "test") as owner:
        selection = resolve_campaign_library_selection(
            owner, tmp_path, candidate_id=package.package_id, setting_id=setting.package_id,
            plan_id="last-cart-through",
        )
        payload = json.loads(canonical_json_bytes(selection.plan.plan.payload))
        slots = payload["initial_state"].setdefault("party_placements", [
            {"entity_state": {}, "append": []},
        ])
        slots[0]["entity_state"]["hp"] = 1
        if placement == "invalid_choices":
            slots[0]["entity_state"]["prepared_spells"] = []
        elif placement == "invalid_hp":
            slots[0]["entity_state"]["hp"] = "invalid"
        elif placement == "invalid_world":
            policy = next(item["value"]["advancement"] for item in
                          payload["initial_state"]["mechanics"] if item["key"] == "state")
            policy.update(method="milestone", maximum_level=3, timing="After the rescue.",
                          allocation="Award the named milestone.", milestones=[{
                              "id": "rescued", "target_level": 3, "trigger": "Rescue the driver.",
                              "alternative_outcomes": "No award if the rescue fails.",
                          }])
            slots[0]["append"].append({"namespace": package.package_id,
                                       "key": "state.advancement.milestones", "value": "invalid"})
        changed = selection.sources.validate(payload, selection.plan.plan.readable_markdown)
        selection = replace(selection, plan=replace(selection.plan, plan=changed))
        before = canonical_json_bytes(selection.plan.plan.payload)
        kwargs = {"operation_id": "start", "player_character_id": saved.artifact_id,
                  "companion_ids": ()}
        if placement == "invalid_choices":
            with pytest.raises(CommandRejected, match="Prepared spells"):
                materialize_library_party(owner, selection, **kwargs)
        elif placement == "invalid_hp":
            with pytest.raises(CommandRejected) as rejected:
                materialize_library_party(owner, selection, **kwargs)
            assert rejected.value.code == "character_initial_state_schema_invalid"
        elif placement == "invalid_world":
            with pytest.raises(ContentShapeError) as rejected:
                materialize_library_party(owner, selection, **kwargs)
            diagnostic = rejected.value.diagnostics[0]
            assert diagnostic.code == "plan_starting_state_invalid"
            assert diagnostic.path.endswith(".advancement.milestones[1]")
        else:
            participants, state, _ = materialize_library_party(owner, selection, **kwargs)
            entities = next(item.value for item in state if item.key == "entities")
            actor = entities[participants[0].participant_id]
            assert actor["hp"] == 1
            assert actor["prepared_spells"] == spells
        assert canonical_json_bytes(selection.plan.plan.payload) == before
        assert load_reusable_participant(
            tmp_path, saved.artifact_id, package=package, declaration=declaration,
            campaign_setting=setting,
        ) == saved
