"""Ordinary expedition supplies preserve inactive legacy weapon sets and budgets."""
from copy import deepcopy

import pytest

from tests.test_saved_weapon_sets import fixture, prepare
from tests.test_srd_action_budget import SRD, _run


@pytest.mark.parametrize("missing", ["set", "slot"])
def test_expedition_quantity_update_preserves_untracked_inactive_weapon_set(missing):
    state, data = fixture()
    prepare(state, "melee", "dagger", "scimitar")
    prepare(state, "ranged", "shortbow")
    hero = state[f"{SRD}.entities"]["hero"]
    if missing == "set":
        del hero["weapon_copy_refs"]["ranged"]
    else:
        del hero["weapon_copy_refs"]["ranged"]["main_hand"]
    hero["hp"] = 17
    hero["resources"]["action_surge"] = 0
    before = deepcopy(state)
    inventory = hero["inventory"] + ["100_gp", "signal_record_copy", "rations"]
    quantities = [{"inventory_item": item, "quantity": 6 if item == "rations" else 1}
                  for item in inventory]
    result = _run("update_srd_carried_inventory", state, updates=[{
        "participant_id": "hero",
        "fields": {"inventory": inventory, "inventory_quantities": quantities},
    }])
    assert result.result["status"] == "committed"
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["inventory"] == inventory
    assert hero["inventory_quantities"]["rations"] == 6
    assert hero["inventory_quantities"]["100_gp"] == 1
    previous = before[f"{SRD}.entities"]["hero"]
    assert hero["weapon_sets"] == previous["weapon_sets"]
    assert hero["weapon_copy_refs"] == previous["weapon_copy_refs"]
    assert hero["hp"] == previous["hp"] == 17
    assert hero["resources"] == previous["resources"]
    assert state[f"{SRD}.combat"] == before[f"{SRD}.combat"]
