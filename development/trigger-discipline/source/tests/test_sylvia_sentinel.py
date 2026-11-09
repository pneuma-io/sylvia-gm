"""An explicit SECOND TRIGGER allowance uses the existing prepared-shot resolver."""

from copy import deepcopy

import pytest

from tests import test_sylvia_player_controls as support


@pytest.fixture
def game():
    support.PlayerControlRuntimeTests.setUpClass()
    value = support.PlayerControlRuntimeTests()
    value.setUp()
    actor = value.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    actor["specialties"] = ["specialty.sentinel.tier_2.second_trigger"]
    value.state["pneuma-io.trigger-discipline.entities"]["hostile_alpha"]["hp"] = 100
    return value


def prepare(game, **changes):
    return game.execute("prepare_sentinel_overwatch", {
        "actor": "vesper", "zone_id": "overwatch-lane", "shot_allowance": 2, **changes,
    })


def test_retained_watch_consumes_real_ammunition_and_recoil_for_each_valid_trigger(game):
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    before = deepcopy(entities["vesper"])
    result = prepare(game)
    assert result["context"]["_sentinel_status"] == "PREPARED"
    prepared_actor = game.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    assert prepared_actor["ap"] == before["ap"] - 2
    game.execute("end_allies_turn", {})
    for destination, remaining in (([7, 0], 1), ([7, 3], 0)):
        game.execute("resolve_trigger_crossing", {
            "actor": "hostile_alpha", "overwatcher": "vesper", "suppressor": "hex",
            "destination": destination, "first_effect": "OVERWATCH",
            "overwatch_critical_effect": "DEEP_HIT",
            "suppression_critical_effect": "DEVASTATING_HIT",
        })
        world = game.state["pneuma-io.trigger-discipline.state"]
        assert world["last_trigger"]["overwatch"]["status"] == "RESOLVED"
        assert world["prepared"]["overwatch"]["shots"] == remaining
        assert world["prepared"]["overwatch"]["active"] == bool(remaining)
    after = game.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    assert after["weapon"]["magazine"]["rounds"] == before["weapon"]["magazine"]["rounds"] - 2
    assert after["weapon"]["current_recoil"] > before["weapon"]["current_recoil"]


@pytest.mark.parametrize("reason", ["unselected", "empty", "cost", "zone", "turn"])
def test_rejected_preparation_preserves_actor_and_existing_watch(game, reason):
    actor = game.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    changes = {}
    if reason == "unselected":
        actor["specialties"] = []
    elif reason == "empty":
        actor["weapon"]["chamber"] = "EMPTY"
    elif reason == "cost":
        actor["ap"] = 0
    elif reason == "zone":
        changes["zone_id"] = "missing-zone"
    else:
        game.state["pneuma-io.trigger-discipline.state"]["active_side"] = "hostiles"
    before = deepcopy(game.state)
    result = prepare(game, **changes)
    assert result["context"]["_sentinel_status"] == "PREPARATION_REJECTED"
    assert game.state["pneuma-io.trigger-discipline.entities"] == before[
        "pneuma-io.trigger-discipline.entities"
    ]
    assert game.state["pneuma-io.trigger-discipline.state"]["prepared"] == before[
        "pneuma-io.trigger-discipline.state"
    ]["prepared"]


@pytest.mark.parametrize("lost", ["downed", "full_cover"])
def test_retained_trigger_requires_current_capability_and_exposure(game, lost):
    prepare(game)
    game.execute("end_allies_turn", {})
    world = game.state["pneuma-io.trigger-discipline.state"]
    if lost == "downed":
        game.state["pneuma-io.trigger-discipline.entities"]["vesper"].update(downed=True, hp=0)
    else:
        # A physical screen between the watcher and the actual zone crossing.
        screen = deepcopy(world["spatial_scene"]["geometry"][0])
        screen.update(id="new-opaque-screen", occupies_cells=False)
        screen["volume"] = {"kind": "box", "min": [4, 0, 0], "max": [4.1, 5, 3]}
        world["spatial_scene"]["geometry"].append(screen)
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"]["vesper"])
    game.execute("resolve_trigger_crossing", {
        "actor": "hostile_alpha", "overwatcher": "vesper", "suppressor": "hex",
        "destination": [7, 0], "first_effect": "OVERWATCH",
        "overwatch_critical_effect": "DEEP_HIT",
        "suppression_critical_effect": "DEVASTATING_HIT",
    })
    assert game.state["pneuma-io.trigger-discipline.entities"]["vesper"] == before
    prepared = game.state["pneuma-io.trigger-discipline.state"]["prepared"]["overwatch"]
    assert prepared["shots"] == 2


def sightline(game, **changes):
    return game.execute("prepare_sentinel_sightline", {
        "actor": "vesper", "target": "hostile_alpha", "aim_point": [9.5, 2.5, 0.9],
        "zone_id": "overwatch-lane", "aim_ap": 1, "ap_cost": 2, **changes,
    })


def test_combined_sightline_pays_one_explicit_reduced_cost_and_stores_real_aim(game):
    actor = game.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    actor["specialties"] = ["specialty.sentinel.entry.prepared_sightline"]
    before = deepcopy(actor)
    result = sightline(game)
    after = game.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    assert result["context"]["_sentinel_status"] == "PREPARED"
    assert after["ap"] == before["ap"] - 2
    assert after["aim"] == {"target": "hostile_alpha", "point": [9.5, 2.5, 0.9], "ap": 1}
    assert after["weapon"] == before["weapon"]
    watch = game.state["pneuma-io.trigger-discipline.state"]["prepared"]["overwatch"]
    assert watch["active"] and watch["shots"] == 1
    assert watch["stationary_origin"] == before["position_cell"]


@pytest.mark.parametrize("reason", [
    "unselected", "no_discount", "cost", "downed", "zone", "cover", "retention_unselected",
])
def test_combined_sightline_rejects_without_partial_aim_or_preparation(game, reason):
    actor = game.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    actor["specialties"] = ["specialty.sentinel.entry.prepared_sightline"]
    changes = {}
    if reason == "unselected":
        actor["specialties"] = []
    elif reason == "no_discount":
        changes["ap_cost"] = 3
    elif reason == "cost":
        actor["ap"] = 0
    elif reason == "downed":
        actor["downed"] = True
    elif reason == "zone":
        changes["zone_id"] = "absent"
    elif reason == "retention_unselected":
        changes["shot_allowance"] = 2
    else:
        world = game.state["pneuma-io.trigger-discipline.state"]
        screen = deepcopy(world["spatial_scene"]["geometry"][0])
        screen.update(id="new-opaque-screen", occupies_cells=False)
        screen["volume"] = {"kind": "box", "min": [4, 0, 0], "max": [4.1, 5, 3]}
        world["spatial_scene"]["geometry"].append(screen)
    before = deepcopy(game.state)
    result = sightline(game, **changes)
    assert result["context"]["_sentinel_status"] == "PREPARATION_REJECTED"
    assert game.state["pneuma-io.trigger-discipline.entities"] == before[
        "pneuma-io.trigger-discipline.entities"
    ]
    assert game.state["pneuma-io.trigger-discipline.state"]["prepared"] == before[
        "pneuma-io.trigger-discipline.state"
    ]["prepared"]


def test_leaving_sightline_and_returning_does_not_restore_preparation(game):
    game.state["pneuma-io.trigger-discipline.entities"]["vesper"]["specialties"] = [
        "specialty.sentinel.entry.prepared_sightline",
    ]
    sightline(game)
    for destination in ([3, 2], [2, 2]):
        game.execute("move_actor", {"actor": "vesper", "destination": destination})
    watch = game.state["pneuma-io.trigger-discipline.state"]["prepared"]["overwatch"]
    assert watch["active"] is False and watch["shots"] == 0
    assert game.state["pneuma-io.trigger-discipline.entities"]["vesper"]["aim"]["ap"] == 0


def test_prepared_shot_uses_and_consumes_the_ordinary_target_specific_aim(game):
    game.state["pneuma-io.trigger-discipline.entities"]["vesper"]["specialties"] = [
        "specialty.sentinel.entry.prepared_sightline",
    ]
    sightline(game)
    game.execute("end_allies_turn", {})
    result = game.execute("resolve_trigger_crossing", {
        "actor": "hostile_alpha", "overwatcher": "vesper", "suppressor": "hex",
        "destination": [7, 0], "first_effect": "OVERWATCH",
        "overwatch_critical_effect": "DEEP_HIT",
        "suppression_critical_effect": "DEVASTATING_HIT",
    })
    assert result["context"]["_trigger_overwatch_first_aim_offset"] == (
        game.parameters.values["fire.aim.attack_difficulty_offset_per_ap"]
    )
    assert game.state["pneuma-io.trigger-discipline.entities"]["vesper"]["aim"]["ap"] == 0


def test_combined_sightline_retains_selected_second_trigger_without_extra_setup(game):
    actor = game.state["pneuma-io.trigger-discipline.entities"]["vesper"]
    actor["specialties"].append("specialty.sentinel.entry.prepared_sightline")
    before_ap = actor["ap"]
    result = sightline(game, shot_allowance=2)
    assert result["context"]["_sentinel_status"] == "PREPARED"
    assert game.state["pneuma-io.trigger-discipline.entities"]["vesper"]["ap"] == before_ap - 2
    watch = game.state["pneuma-io.trigger-discipline.state"]["prepared"]["overwatch"]
    assert watch["shots"] == 2 and watch["stationary_origin"] == [2, 2]
