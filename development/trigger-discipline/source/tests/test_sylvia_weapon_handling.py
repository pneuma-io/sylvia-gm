"""Real runtime checks; AP numbers here are explicit synthetic adjudications."""

from copy import deepcopy

import pytest

from tests import test_sylvia_player_controls as support


@pytest.fixture(scope="module", autouse=True)
def compiled_runtime():
    support.PlayerControlRuntimeTests.setUpClass()


@pytest.fixture
def game():
    runtime = support.PlayerControlRuntimeTests()
    runtime.setUp()
    return runtime


def parts(game):
    namespace = support.builder.PACKAGE_ID
    return game.state[f"{namespace}.entities"]["vesper"], game.state[f"{namespace}.state"]


def handle(game, location="STOWED", ap_cost=1, **overrides):
    actor, _ = parts(game)
    return game.execute("handle_weapon", {
        "actor": "vesper", "weapon_id": actor["weapon"]["id"],
        "location": location, "ap_cost": ap_cost, **overrides,
    })


def test_stow_and_ready_preserve_weapon_but_break_firing_preparation(game):
    actor, state = parts(game)
    actor["aim"] = {"target": "hostile_alpha", "point": [9.5, 2.5, 0.9], "ap": 2}
    for kind in ("overwatch", "suppression"):
        state["prepared"][kind].update(owner="vesper", active=True)
    before = deepcopy(actor)
    handle(game)
    actor, state = parts(game)
    expected = deepcopy(before["weapon"])
    expected["location"] = "STOWED"
    assert actor["weapon"] == expected
    assert actor["ap"] == before["ap"] - 1
    assert actor["aim"] == {"target": None, "point": None, "ap": 0}
    assert all(not state["prepared"][kind]["active"] for kind in ("overwatch", "suppression"))
    assert state["last_equipment"]["status"] == "HANDLED"
    fire = {"attacker": "vesper", "target": "hostile_alpha",
            "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"}
    assert "not ready" in game.check("fire_single", fire)
    handle(game, "READY", 2)
    actor, state = parts(game)
    assert actor["weapon"] == before["weapon"]
    assert actor["ap"] == before["ap"] - 3
    assert game.check("fire_single", fire) is None
    assert not state["prepared"]["overwatch"]["active"]


@pytest.mark.parametrize("reason", ["inactive", "downed", "other_side", "no_ap", "controlled", "ground", "same_location", "wrong_weapon"])
def test_rejected_handling_preserves_actor_and_preparation(game, reason):
    actor, state = parts(game)
    args = {}
    if reason == "inactive":
        actor["active"] = False
    elif reason == "downed":
        actor["downed"] = True
    elif reason == "other_side":
        state["active_side"] = "hostiles"
    elif reason == "no_ap":
        actor["ap"] = 0
    elif reason == "controlled":
        actor["weapon"]["controlled_by"] = "hostile_alpha"
    elif reason == "ground":
        actor["weapon"]["location"] = "GROUND"
    elif reason == "same_location":
        args["location"] = "READY"
    else:
        args["weapon_id"] = "someone-elses-weapon"
    before = deepcopy(actor), deepcopy(state["prepared"])
    handle(game, **args)
    actor, state = parts(game)
    assert (actor, state["prepared"]) == before
    assert state.get("last_equipment", {}).get("status") != "HANDLED"


def test_quick_access_does_not_cancel_another_actors_preparation(game):
    _, state = parts(game)
    state["prepared"]["overwatch"].update(owner="hex", active=True)
    handle(game, "QUICK_ACCESS")
    actor, state = parts(game)
    assert actor["weapon"]["location"] == "QUICK_ACCESS"
    assert state["prepared"]["overwatch"]["active"]


def test_cost_is_required_and_not_exposed_as_an_automatic_player_action(game):
    spec = game.specs["handle_weapon"]
    schema = support.builder._schema(spec["parameters"])
    assert "ap_cost" in schema["required"]
    assert "default" not in schema["properties"]["ap_cost"]
    assert "player_controls" not in support.builder._tool_metadata("handle_weapon", True)
