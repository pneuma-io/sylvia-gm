"""Non-dice action cards report the committed budget or condition, without invented rolls."""

from copy import deepcopy

import pytest

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import SRD, _combat_case


@pytest.mark.parametrize("operation,choices,title", [
    ("dash_combat_action", {}, "Dash"),
    ("spend_combat_movement", {"distance": 5, "difficult_terrain": True}, "Movement"),
    ("take_dodge_action", {}, "Dodge"),
    ("take_disengage_action", {}, "Disengage"),
    ("spend_srd_bonus_action", {"bonus_action": "second_wind"}, "Bonus Action spent"),
    ("spend_srd_reaction", {"reaction": "opportunity_attack"}, "Reaction spent"),
    ("help_srd_attack_action", {"ally_state": "ally", "target_state": "guard",
                                "target_distance": 5}, "Help attack"),
    ("help_srd_ability_check_action", {"ally_state": "ally", "skill": "athletics"}, "Help check"),
    ("end_combat_encounter", {"end_reason": "fighting_stopped"}, "Combat ended"),
])
def test_non_dice_action_has_current_card_with_committed_effect(operation, choices, title):
    state, data = _combat_case()
    data["skill_proficiencies"] = ["athletics"]
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(name="Budget Hero", available_bonus_actions=["second_wind"],
                            available_reactions=["opportunity_attack"])
    entities["ally"] = deepcopy(entities["guard"])
    entities["ally"]["name"] = "Ally"
    state[f"{SRD}.combat"]["movement_spent"] = 0
    parameters = choices if operation in {"end_combat_encounter", "spend_combat_movement"} else {
        "actor": "hero", "actor_state": "hero", **choices,
    }
    result = _run(operation, state, participant_data=data, **parameters).result
    card = result.get("result_card")
    assert card is not None
    assert card["title"] == title and card["footprint"] == "inline"
    assert card.get("equation") is None
    rows = {row["label"]: row["value"] for row in card["summary"]}
    after = state[f"{SRD}.entities"]
    combat = state[f"{SRD}.combat"]
    if operation == "dash_combat_action":
        assert rows["Movement remaining (feet)"] == combat["movement_remaining"] == 60
    elif operation == "spend_combat_movement":
        assert rows["Moved (feet)"] == 5 and rows["Movement spent (feet)"] == 10
        assert rows["Movement remaining (feet)"] == combat["movement_remaining"] == 20
    elif operation == "take_dodge_action":
        assert after["hero"]["dodging"] is True
    elif operation == "take_disengage_action":
        assert combat["disengaged_actor"] == "hero"
    elif operation == "spend_srd_bonus_action":
        assert after["hero"]["bonus_action_available"] is False
        assert rows["Effect"] == "Bonus Action spent; ability effects resolved separately"
    elif operation == "spend_srd_reaction":
        assert after["hero"]["reaction_available"] is False
        assert rows["Effect"] == "Reaction spent; ability effects resolved separately"
    elif operation == "help_srd_attack_action":
        assert rows["Ally"] == "Ally" and rows["Against"] == "Guard"
        assert after["ally"]["help_attack_target"] == "guard"
    elif operation == "help_srd_ability_check_action":
        assert rows["Skill or tool"] == "Athletics"
        assert after["ally"]["help_ability_skill"] == "athletics"
    else:
        assert rows["Reason"] == "Fighting stopped"
        assert combat["encounter_active"] is False
