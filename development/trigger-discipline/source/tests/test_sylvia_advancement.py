"""One earned level uses actual canonical prerequisites and preserves unrelated state."""

from copy import deepcopy

import pytest

from sylvia_gm.application.package_runtime import PackageRuntimeError
from tests import test_sylvia_player_controls as support


@pytest.fixture
def game():
    support.PlayerControlRuntimeTests.setUpClass()
    value = support.PlayerControlRuntimeTests()
    value.setUp()
    actor = value.state["pneuma-io.trigger-discipline.entities"]["hex"]
    actor.update(level=1, specialties=["specialty.networker.entry.map_the_links"])
    value.participants = {"hex": {
        "package_id": support.builder.PACKAGE_ID,
        "data": {key: deepcopy(actor[key]) for key in
                 ("id", "name", "side", "class_id", "level", "skills", "aptitudes", "specialties")},
    }}
    return value


def advance(game, **changes):
    return game.execute("advance_character", {
        "actor": "hex", "character": "hex", "expected_level": 1,
        "milestone_id": "recovered-dock-evidence",
        "milestone_confirmed": True, "specialty_id": "specialty.networker.tier_1.pivot",
        **changes,
    })


def test_earned_choice_advances_once_without_healing_or_equipment_changes(game):
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    result = advance(game)
    after = game.state["pneuma-io.trigger-discipline.entities"]
    expected = deepcopy(before)
    expected["hex"]["level"] = 2
    expected["hex"]["specialties"].append("specialty.networker.tier_1.pivot")
    expected["hex"]["advancement_milestones"] = ["recovered-dock-evidence"]
    assert after == expected
    assert result["context"]["_card_advancement_status"] == "ADVANCED"
    assert result["context"]["_card_advancement_level"] == 2
    assert game.participants["hex"]["data"]["level"] == 2
    assert game.participants["hex"]["data"]["specialties"] == expected["hex"]["specialties"]
    advance(game)
    assert game.state["pneuma-io.trigger-discipline.entities"] == expected
    result = game.state["pneuma-io.trigger-discipline.state"]["last_advancement"]
    assert result["status"] == "ALREADY_RECORDED"


def test_empty_milestone_is_rejected_by_the_runtime_without_state_changes(game):
    before = deepcopy(game.state)
    with pytest.raises(PackageRuntimeError):
        advance(game, milestone_id="")
    assert game.state == before


@pytest.mark.parametrize("changes", [
    {"specialty_id": "specialty.networker.tier_2.foothold"},
    {"specialty_id": "specialty.sentinel.entry.prepared_sightline"},
    {"specialty_id": "specialty.networker.entry.map_the_links"},
    {"expected_level": 2}, {"milestone_confirmed": False},
])
def test_invalid_advance_leaves_the_character_and_equipment_untouched(game, changes):
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    participant_before = deepcopy(game.participants)
    advance(game, **changes)
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assert game.participants == participant_before


def test_mismatched_live_character_cannot_advance_another_actor(game):
    game.participants["other"] = deepcopy(game.participants["hex"])
    before = deepcopy(game.state)
    participants_before = deepcopy(game.participants)
    advance(game, character="other")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before[
        "pneuma-io.trigger-discipline.entities"
    ]
    assert game.participants == participants_before
