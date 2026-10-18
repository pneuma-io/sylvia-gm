"""Both encounter starts retain named Initiative order and the actual dice behind it."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import SRD, _combat_case


@pytest.mark.parametrize("group", [False, True])
def test_initiative_card_preserves_every_creature_roll_and_order(monkeypatch, group):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _combat_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(name="Budget Hero", initiative_modifier=2)
    entities["guard"].update(initiative_modifier=1)
    entities["ally"] = deepcopy(entities["guard"])
    entities["ally"].update(name="Ally", initiative_modifier=0)
    tool = "start_group_combat_encounter" if group else "start_combat_encounter_entities"
    params = {"combatants": ["hero", "guard", "ally"]} if group else {
        "first_actor": "hero", "second_actor": "guard", "first_surprised": False,
        "second_surprised": False, "tie_winner": "second",
    }
    result = _run(tool, state, participant_data=data, **params).result
    card = result.get("result_card")
    assert card is not None
    assert card["title"] == "Initiative"
    expected = [("Budget Hero", 15), ("Guard", 15), ("Ally", 2)] if group else [
        ("Guard", 15), ("Budget Hero", 15),
    ]
    assert [(row["label"], row["value"]) for row in card["summary"]] == expected
    detail_rows = card["details"]
    for index, (name, total) in enumerate(expected):
        values = {row["label"]: row["value"] for row in detail_rows[index * 4:index * 4 + 4]}
        assert values["Creature"] == name
        assert sum(values["D20 results"]) + values["Modifier"] == values["Total"] == total
    before = deepcopy(card)
    entities["hero"].update(name="Changed", initiative_modifier=99)
    assert card == before
