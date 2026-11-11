"""Generated panels retain the accepted native AP previews under changed prices."""

from copy import deepcopy

import pytest

from tests import test_sylvia_player_controls as support


@pytest.fixture(scope="module")
def game():
    support.PlayerControlRuntimeTests.setUpClass()
    value = support.PlayerControlRuntimeTests()
    value.setUp()
    return value


@pytest.mark.parametrize(("tool", "params", "expected"), [
    ("change_stance", {"actor": "hex", "stance": "CROUCHED"}, 1),
    ("reload_weapon", {"actor": "hex", "disposition": "RETAIN"}, 2),
    ("recover_recoil", {"actor": "hex", "ap": 2}, 2),
    ("aim", {"attacker": "hex", "target": "hostile_alpha",
             "aim_point": [7.5, 3.5, 0.9], "ap": 2}, 2),
    ("fire_full_auto", {"attacker": "hex", "target": "hostile_alpha",
                        "aim_point": [7.5, 3.5, 0.9], "rounds": 11,
                        "critical_effect": "DEVASTATING_HIT"}, 3),
    ("network_connection", {"actor": "hex", "node_id": "lobby_terminal",
                            "action": "DISCONNECT"}, 0),
])
def test_preview_reads_the_native_debit_without_spending_anything(game, tool, params, expected):
    result = game.preview(tool, params)
    rows = [row["value"] for row in result["preview"]["summary"] if row["label"] == "AP cost"]
    assert rows == [expected]


def test_changed_authored_reload_price_is_projected_without_a_second_price_table(game):
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    before = deepcopy(entities)
    try:
        entities["hex"]["weapon"]["reload_ap"] = 4
        result = game.preview("reload_weapon", {"actor": "hex", "disposition": "RETAIN"})
        assert next(row["value"] for row in result["preview"]["summary"]
                    if row["label"] == "AP cost") == 4
    finally:
        game.state["pneuma-io.trigger-discipline.entities"] = before
