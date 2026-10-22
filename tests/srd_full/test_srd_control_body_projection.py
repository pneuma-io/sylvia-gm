"""A real Gaseous Form projection preserves typed control state for later movement."""

import os
from pathlib import Path

import pytest

from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_result_cards import SRD, _casting_headers
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    runtime_root = os.environ.get("FC14_SOURCE_RUNTIME_ROOT")
    if runtime_root:
        monkeypatch.setattr(budget, "ROOT", Path(runtime_root))
    budget._mechanic.cache_clear()
    yield
    budget._mechanic.cache_clear()


def test_actual_gaseous_projection_without_prior_control_allows_real_scene_movement():
    state, data, params = _information_case("mind_blank")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(prepared_spells=["gaseous_form"])
    data["spellcasting_grants"][0]["spells"] = ["gaseous_form"]
    data["wizard_spellbook"] = ["gaseous_form"]
    materials = [
        {
            "id": f"gas_material_{i}",
            "inventory_item": f"gas_material_{i}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"],
            "value_cp": part["minimum_value_cp"],
        }
        for i, part in enumerate(_casting_headers()["gaseous_form"]["material_parts"])
    ]
    hero["spell_materials"] = materials
    hero["inventory"] += [item["inventory_item"] for item in materials]
    target = state[f"{SRD}.entities"]["guard"]
    target.update(
        ability_scores=dict.fromkeys(data["abilities"], 12),
        size_rank=2,
        languages=["common"],
        passive_perception=11,
    )
    assert "has_control_behavior_sources" not in target
    params.update(
        spell="gaseous_form",
        slot_resource="spell_slot_3",
        material_item_ids=[item["id"] for item in materials],
    )
    budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["has_control_behavior_sources"] is False
    assert target["form_current_source_id"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    actual = next(e for e in scene["entities"] if e["id"] == "guard")
    start = actual["position"]
    destination = [start[0] + 5, start[1], start[2]]
    result = budget._run(
        "move_srd_scene_creature", state, actor_state="guard", destination=destination, mode="fly"
    )
    assert result.result["status"] == "committed"
    actual = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard"
    )
    assert actual["position"] == destination
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 5
    assert state[f"{SRD}.entities"]["guard"]["has_control_behavior_sources"] is False
