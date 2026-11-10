"""Standing-order changes use real operator state and the existing fire resolver."""

from copy import deepcopy

import pytest

from tests import test_sylvia_player_controls as support


@pytest.fixture
def game():
    support.PlayerControlRuntimeTests.setUpClass()
    value = support.PlayerControlRuntimeTests()
    value.setUp()
    entities = value.state["pneuma-io.trigger-discipline.entities"]
    entities["autodrone_alpha"].update(controlled_by="hex", signal_connected=True)
    return value


def assign(game, **changes):
    game.execute(
        "assign_drone_order",
        {
            "actor": "hex",
            "drone": "autodrone_alpha",
            "target": "drone_target",
            "order_id": "watch-the-door",
            "mode": "WATCH",
            "ap_cost": 1,
            **changes,
        },
    )


def test_order_replacement_spends_operator_ap_once_and_preserves_drone_resources(game):
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    before = deepcopy(entities["autodrone_alpha"])
    operator_ap = entities["hex"]["ap"]
    assign(game)
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    expected = deepcopy(before)
    expected["standing_order"] = {
        "id": "watch-the-door",
        "operator_id": "hex",
        "active": True,
        "authorized_fire": False,
        "target": "drone_target",
        "collateral_limit": "NO_UNCONFIRMED_TARGETS",
        "condition_entity_id": "",
    }
    expected["order_receipts"] = [{
        "id": "watch-the-door", "verified": True, "operator_id": "hex",
        "target_id": "drone_target", "mode": "WATCH", "ap_cost": 1,
        "condition_entity_id": "",
    }]
    assert entities["autodrone_alpha"] == expected
    assert entities["hex"]["ap"] == operator_ap - 1
    assign(game)
    assert game.state["pneuma-io.trigger-discipline.entities"] == entities
    assert (
        game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"]["status"]
        == "ALREADY_ASSIGNED"
    )
    assign(game, mode="ENGAGE_CONFIRMED_TARGET")
    assert game.state["pneuma-io.trigger-discipline.entities"] == entities
    assign(game, order_id="engage-the-door", mode="ENGAGE_CONFIRMED_TARGET")
    updated = game.state["pneuma-io.trigger-discipline.entities"]
    assert updated["hex"]["ap"] == operator_ap - 2
    assert updated["autodrone_alpha"]["standing_order"]["authorized_fire"] is True
    assert updated["autodrone_alpha"]["weapon"] == before["weapon"]
    assert (
        updated["autodrone_alpha"]["sensor_confirmed_targets"] == before["sensor_confirmed_targets"]
    )


def test_watch_order_cannot_fire_and_replacement_uses_existing_sensor_and_weapon_guards(game):
    assign(game)
    shot = {"actor": "autodrone_alpha", "target": "drone_target", "aim_point": [31.5, 30.5, 0.9]}
    entities = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    assert game.check("autodrone_fire", shot) is not None


    game.execute("autodrone_fire", shot)
    assert game.state["pneuma-io.trigger-discipline.entities"] == entities
    assign(game, order_id="engage", mode="ENGAGE_CONFIRMED_TARGET")
    assert game.check("autodrone_fire", shot) is None
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    game.execute("autodrone_fire", shot)
    after = game.state["pneuma-io.trigger-discipline.entities"]
    assert after["hex"] == before["hex"]
    assert (
        after["autodrone_alpha"]["weapon"]["magazine"]["rounds"]
        == before["autodrone_alpha"]["weapon"]["magazine"]["rounds"] - 1
    )
    after["autodrone_alpha"]["sensor_confirmed_targets"] = []
    assert game.check("autodrone_fire", shot) is not None


@pytest.mark.parametrize("conflicting", [False, True])
def test_replaced_order_identity_never_spends_again_or_overwrites_current_order(game, conflicting):
    assign(game, order_id="A")
    assign(game, order_id="B", mode="ENGAGE_CONFIRMED_TARGET")
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    assign(game, order_id="A", mode="ENGAGE_CONFIRMED_TARGET" if conflicting else "WATCH")
    after = game.state["pneuma-io.trigger-discipline.entities"]
    assert after["hex"]["ap"] == before["hex"]["ap"]
    assert after["autodrone_alpha"]["standing_order"]["id"] == "B"
    assert after == before
    status = game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"]["status"]
    assert status == ("ORDER_ID_CONFLICT" if conflicting else "ALREADY_ASSIGNED")


@pytest.mark.parametrize("changes", [
    {"ap_cost": 2}, {"actor": "vesper"}, {"target": "hostile_alpha"},
    {"mode": "ENGAGE_CONFIRMED_TARGET"},
])
def test_order_identity_binds_every_execution_input_across_replacement(game, changes):
    assign(game, order_id="A")
    assign(game, order_id="B")
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    assign(game, order_id="A", **changes)
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ORDER_ID_CONFLICT"


def test_full_receipt_history_refuses_new_order_but_does_not_forget_old_ids(game):
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    entities["autodrone_alpha"]["order_receipts"] = [{
        "id": f"order-{index}", "verified": True, "operator_id": "hex",
        "target_id": "drone_target", "mode": "WATCH", "ap_cost": 1,
    } for index in range(256)]
    before = deepcopy(entities)
    assign(game, order_id="new-order")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ORDER_RECEIPT_LIMIT"
    assign(game, order_id="order-0")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ALREADY_ASSIGNED"


def test_ambiguous_duplicate_order_receipts_never_select_one_for_execution(game):
    assign(game, order_id="A")
    assign(game, order_id="B")
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    receipts = entities["autodrone_alpha"]["order_receipts"]
    receipts.append({**receipts[0], "ap_cost": 2})
    before = deepcopy(entities)
    assign(game, order_id="A")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ORDER_HISTORY_UNAVAILABLE"


def test_receipt_schema_enforces_bound_complete_verified_records_and_legacy_reservations():
    from sylvia_ruleset import drone_orders

    from sylvia_gm.application.schema_admission import validate_parameters
    from sylvia_gm.domain.errors import CommandRejected

    schema = drone_orders.state_schemas()[0]["schema"]
    receipt = {"id": "A", "verified": True, "operator_id": "hex", "target_id": "drone_target",
               "mode": "WATCH", "ap_cost": 1}
    validate_parameters({"drone": {"order_receipts": [receipt]}}, schema)
    legacy = {"id": "legacy", "verified": False}
    validate_parameters({"drone": {"order_receipts": [legacy]}}, schema)
    for receipts in ([{"id": "A", "verified": True}], [receipt, receipt],
                     [{**receipt, "id": str(index)} for index in range(257)]):
        with pytest.raises(CommandRejected):
            validate_parameters({"drone": {"order_receipts": receipts}}, schema)


def test_identified_legacy_order_is_reserved_across_replacement(game):
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    entities["autodrone_alpha"]["standing_order"]["id"] = "legacy-A"
    before = deepcopy(entities)
    assign(game, order_id="legacy-A")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assign(game, order_id="B")
    after_b = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    assert after_b["autodrone_alpha"]["order_receipts"][0] == {
        "id": "legacy-A", "verified": False,
    }
    assign(game, order_id="legacy-A")
    assert game.state["pneuma-io.trigger-discipline.entities"] == after_b
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ORDER_HISTORY_UNAVAILABLE"


def test_matching_replay_needs_no_second_live_signal_or_operator_ap(game):
    assign(game, order_id="A")
    assign(game, order_id="B")
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    entities["hex"]["ap"] = 0
    entities["autodrone_alpha"]["signal_connected"] = False
    before = deepcopy(entities)
    assign(game, order_id="A")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ALREADY_ASSIGNED"


@pytest.mark.parametrize("reason", ["signal", "operator", "drone", "downed", "ap", "turn"])
def test_rejected_order_never_changes_operator_or_drone(game, reason):
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    if reason == "signal":
        entities["autodrone_alpha"]["signal_connected"] = False
    if reason == "operator":
        entities["autodrone_alpha"]["controlled_by"] = "someone-else"
    if reason == "drone":
        entities["autodrone_alpha"]["active"] = False
    if reason == "downed":
        entities["hex"]["downed"] = True
    if reason == "ap":
        entities["hex"]["ap"] = 0
    if reason == "turn":
        game.state["pneuma-io.trigger-discipline.state"]["active_side"] = "hostiles"
    before = deepcopy(entities)
    assign(game)
    assert game.state["pneuma-io.trigger-discipline.entities"] == before


def test_conditional_order_uses_current_sensing_and_binds_condition_to_durable_identity(game):
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    entities["hex"]["specialties"] = [
        "specialty.autodrones.entry.tactical_autonomy",
        "specialty.autodrones.tier_1.conditional_order",
    ]
    assign(game, order_id="conditional", mode="ENGAGE_CONFIRMED_TARGET",
           condition_entity_id="vesper")
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ASSIGNED"
    shot = {"actor": "autodrone_alpha", "target": "drone_target", "aim_point": [31.5, 30.5, 0.9]}
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    assert "condition" in game.check("autodrone_fire", shot)
    game.execute("autodrone_fire", shot)
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    game.state["pneuma-io.trigger-discipline.entities"]["autodrone_alpha"][
        "sensor_confirmed_targets"
    ].append("vesper")
    assert game.check("autodrone_fire", shot) is None
    game.execute("autodrone_fire", shot)
    after = game.state["pneuma-io.trigger-discipline.entities"]
    assert after["hex"] == before["hex"]
    assert after["autodrone_alpha"]["weapon"]["magazine"]["rounds"] == 9
    prior = deepcopy(after)
    assign(game, order_id="conditional", mode="ENGAGE_CONFIRMED_TARGET",
           condition_entity_id="hostile_alpha")
    assert game.state["pneuma-io.trigger-discipline.entities"] == prior
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ORDER_ID_CONFLICT"


@pytest.mark.parametrize("reason", ["specialty", "unknown_entity", "watch_mode"])
def test_conditional_assignment_requires_specialty_and_actual_condition_identity(game, reason):
    actor = game.state["pneuma-io.trigger-discipline.entities"]["hex"]
    actor["specialties"] = ["specialty.autodrones.entry.tactical_autonomy",
                            "specialty.autodrones.tier_1.conditional_order"]
    if reason == "specialty":
        actor["specialties"] = []
    before = deepcopy(game.state["pneuma-io.trigger-discipline.entities"])
    assign(game, order_id="conditional", mode="WATCH" if reason == "watch_mode" else
           "ENGAGE_CONFIRMED_TARGET",
           condition_entity_id="missing" if reason == "unknown_entity" else "vesper")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before


def test_pre_extension_receipt_without_condition_field_still_replays_without_spending(game):
    assign(game, order_id="A")
    entities = game.state["pneuma-io.trigger-discipline.entities"]
    entities["autodrone_alpha"]["order_receipts"][0].pop("condition_entity_id")
    before = deepcopy(entities)
    assign(game, order_id="A")
    assert game.state["pneuma-io.trigger-discipline.entities"] == before
    assert game.state["pneuma-io.trigger-discipline.state"]["last_drone_order"][
        "status"] == "ALREADY_ASSIGNED"
