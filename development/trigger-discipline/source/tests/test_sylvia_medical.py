"""Keep authored medical states compatible with actual treatment transitions."""

from copy import deepcopy

import pytest
from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state,
)
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.application.schema_admission import validate_parameters

from tests import test_sylvia_player_controls as support
from tests.test_sylvia_character_contract import _authored_state


@pytest.fixture(scope="module", autouse=True)
def compiled_runtime():
    support.PlayerControlRuntimeTests.setUpClass()


def _place_casualty(game, casualty, cell):
    """Apply the same placement fields supplied when a Character File joins a map."""
    casualty["position_cell"] = cell
    casualty["center"] = [cell[0] + 0.5, cell[1] + 0.5, 0.9]
    casualty["muzzle"] = [cell[0] + 0.91, cell[1] + 0.5, 1.4]
    world = game.state[f"{support.builder.PACKAGE_ID}.state"]
    token = next(item for item in world["spatial_scene"]["entities"] if item["id"] == casualty["id"])
    token["position"] = [cell[0] + 0.5, cell[1] + 0.5, 0]


def _treat(game, parameters, **overrides):
    """Pass explicit synthetic treatment numbers rather than accepting runtime defaults."""
    prefix = f"medicine.{parameters['action'].lower()}"
    return game.execute("treat_casualty", {
        "issue_id": "casualty-condition-1", "check_mode": "ROLL",
        "function_recovery": "NOT_PLAUSIBLE",
        "ap_cost": game.parameters.values[f"{prefix}_ap"],
        "difficulty": game.parameters.values[f"{prefix}_difficulty"],
        **parameters, **overrides,
    })


@pytest.mark.parametrize("condition, action", [
    ("ACTIVE", "STABILIZE"),
    ("FAILED", "RESUSCITATION"),
])
def test_authored_condition_can_be_treated_and_result_remains_valid(condition, action):
    participant = support.builder._conformance_character()["artifact"]
    declaration = support.builder._character_declaration(
        support.builder._character_schema(participant["specialties"])
    )
    authored = _authored_state(declaration)
    authored["life_threatening"] = {
        "state": condition, "within_reversible_window": True,
    }
    casualty = materialize_initial_character_state(
        participant, authored,
        entity_state_schema=declaration["entity_state_schema"],
        initial_state_calculations=declaration["initial_state_calculations"],
    )
    game = support.PlayerControlRuntimeTests()
    game.setUp()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    entities[participant["id"]] = casualty
    _place_casualty(game, casualty, [2, 3])
    medic = entities["vesper"]
    # Explicit synthetic values guarantee success without selecting campaign balance.
    medic.update(medical_kit_capable=True, resuscitation_kit_capable=True, medicine_bonus=100)
    before = deepcopy(casualty)
    before_ap = medic["ap"]
    _treat(game, {
        "actor": "vesper", "target": participant["id"], "action": action,
    })
    updated = game.state[f"{namespace}.entities"]
    expected = deepcopy(before)
    expected["life_threatening"]["state"] = "STABILIZED"
    expected["battlefield_treatments"] = ["casualty-condition-1"]
    assert updated[participant["id"]] == expected
    assert updated["vesper"]["ap"] < before_ap
    character = deepcopy(updated[participant["id"]])
    for field in ("position_cell", "center", "muzzle"):
        character.pop(field)
    validate_parameters(character, declaration["entity_state_schema"])


@pytest.mark.parametrize("reason", ["far_away", "blocked", "inactive", "downed", "no_kit", "no_ap"])
def test_rejected_treatment_preserves_casualty_and_does_not_reuse_previous_success(reason):
    game = support.PlayerControlRuntimeTests()
    game.setUp()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    world = game.state[f"{namespace}.state"]
    medic, casualty = entities["vesper"], entities["hex"]
    _place_casualty(game, casualty, [2, 3])
    medic.update(medical_kit_capable=True, medicine_bonus=100)
    casualty["life_threatening"].update(state="ACTIVE")
    world["last_medical"] = {"success": True, "status": "TREATED"}
    if reason == "far_away":
        _place_casualty(game, casualty, [2, 6])
    elif reason == "blocked":
        wall = deepcopy(world["spatial_scene"]["geometry"][1])
        wall.update(id="medical-barrier", volume={
            "kind": "box", "min": [1, 3, 0], "max": [4, 3.1, 3],
        })
        world["spatial_scene"]["geometry"].append(wall)
    elif reason == "inactive":
        medic["active"] = False
    elif reason == "downed":
        medic["downed"] = True
    elif reason == "no_kit":
        medic["medical_kit_capable"] = False
    else:
        medic["ap"] = 0
    before = deepcopy(entities)
    _treat(game, {"actor": "vesper", "target": "hex", "action": "STABILIZE"})
    assert game.state[f"{namespace}.entities"] == before
    assert game.state[f"{namespace}.state"]["last_medical"]["success"] is False


def test_failed_check_spends_attempt_cost_without_worsening_the_condition():
    game = support.PlayerControlRuntimeTests()
    game.setUp()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    medic, casualty = entities["vesper"], entities["hex"]
    medic.update(medical_kit_capable=True, medicine_bonus=-100)
    casualty["life_threatening"].update(state="ACTIVE")
    before_casualty, before_ap = deepcopy(casualty), medic["ap"]
    _treat(game, {"actor": "vesper", "target": "hex", "action": "STABILIZE"})
    updated = game.state[f"{namespace}.entities"]
    result = game.state[f"{namespace}.state"]["last_medical"]
    assert updated["hex"] == before_casualty
    assert updated["vesper"]["ap"] == before_ap - game.parameters.values["medicine.stabilize_ap"]
    assert result["status"] == "FAILED_CHECK"
    assert result["success"] is False


def test_expired_resuscitation_window_cannot_be_overridden_by_skill():
    game = support.PlayerControlRuntimeTests()
    game.setUp()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    entities["vesper"].update(resuscitation_kit_capable=True, medicine_bonus=100)
    entities["hex"]["life_threatening"].update(state="FAILED", within_reversible_window=False)
    before = deepcopy(entities)
    _treat(game, {"actor": "vesper", "target": "hex", "action": "RESUSCITATION"})
    assert game.state[f"{namespace}.entities"] == before
    assert game.state[f"{namespace}.state"]["last_medical"]["success"] is False


def _combat_medicine_game():
    """Give a synthetic field medic the actual ENTRY and an injured nearby casualty."""
    game = support.PlayerControlRuntimeTests()
    game.setUp()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    entities["vesper"].update(
        class_id="CORPSMAN", medical_kit_capable=True, medicine_bonus=100,
        specialties=["specialty.field_medic.entry.combat_medicine"],
    )
    entities["hex"].update(res=4, max_res=10, hp=5)
    return game


def _field_care(game, **overrides):
    return game.execute("treat_casualty", {
        "actor": "vesper", "target": "hex", "action": "BATTLEFIELD_CARE",
        "issue_id": "forearm-trauma-instance-1", "ap_cost": 1, "difficulty": 14,
        "check_mode": "ROLL", "function_recovery": "PLAUSIBLE", **overrides,
    })


def test_combat_medicine_restores_one_res_once_per_casualty_issue_across_medics():
    game = _combat_medicine_game()
    namespace = support.builder.PACKAGE_ID
    before = deepcopy(game.state[f"{namespace}.entities"]["hex"])
    result = _field_care(game)
    summary = {item["label"]: item["value"] for item in result["result_card"]["summary"]}
    assert summary == {"Outcome": "Treatment completed", "RES restored": 1, "AP spent": 1}
    entities = game.state[f"{namespace}.entities"]
    assert entities["hex"]["res"] == 5
    assert entities["hex"]["hp"] == before["hp"]
    assert entities["hex"]["life_threatening"] == before["life_threatening"]
    assert entities["hex"]["battlefield_treatments"] == ["forearm-trauma-instance-1"]
    second = entities["hostile_alpha"]
    second.update(
        side="allies", class_id="CORPSMAN", medical_kit_capable=True, medicine_bonus=100,
        specialties=["specialty.field_medic.entry.combat_medicine"],
    )
    _place_casualty(game, second, [3, 3])
    _field_care(game, actor="hostile_alpha")
    assert game.state[f"{namespace}.entities"]["hex"]["res"] == 5
    assert game.state[f"{namespace}.state"]["last_medical"]["res_restored"] == 0
    _field_care(game, actor="hostile_alpha", issue_id="different-injury-instance-2")
    assert game.state[f"{namespace}.entities"]["hex"]["res"] == 6


def test_failed_treatment_does_not_consume_the_first_successful_treatment():
    game = _combat_medicine_game()
    namespace = support.builder.PACKAGE_ID
    game.state[f"{namespace}.entities"]["vesper"]["medicine_bonus"] = -100
    _field_care(game)
    entities = game.state[f"{namespace}.entities"]
    assert entities["hex"]["res"] == 4
    assert not entities["hex"].get("battlefield_treatments")
    entities["vesper"]["medicine_bonus"] = 100
    _field_care(game)
    assert game.state[f"{namespace}.entities"]["hex"]["res"] == 5


@pytest.mark.parametrize("boundary", ["ordinary_medic", "not_plausible", "full_res"])
def test_first_success_without_recovery_cannot_be_banked_for_a_later_bonus(boundary):
    game = _combat_medicine_game()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    overrides = {}
    if boundary == "ordinary_medic":
        entities["vesper"]["specialties"] = []
    elif boundary == "not_plausible":
        overrides["function_recovery"] = "NOT_PLAUSIBLE"
    else:
        entities["hex"]["res"] = entities["hex"]["max_res"]
    before_res = entities["hex"]["res"]
    _field_care(game, **overrides)
    entities = game.state[f"{namespace}.entities"]
    assert entities["hex"]["res"] == before_res
    entities["hex"]["res"] = 4
    entities["vesper"]["specialties"] = ["specialty.field_medic.entry.combat_medicine"]
    _field_care(game)
    assert game.state[f"{namespace}.entities"]["hex"]["res"] == 4


def test_missing_recovery_maximum_rejects_without_spending_or_claiming_treatment():
    game = _combat_medicine_game()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    entities["hex"].pop("max_res")
    before = deepcopy(entities)
    _field_care(game)
    assert game.state[f"{namespace}.entities"] == before
    assert game.state[f"{namespace}.state"]["last_medical"]["status"] == "MISSING_RES_MAXIMUM"


def test_certain_stabilization_shares_the_same_recovery_receipt_as_field_care():
    game = _combat_medicine_game()
    namespace = support.builder.PACKAGE_ID
    game.state[f"{namespace}.entities"]["hex"]["life_threatening"]["state"] = "ACTIVE"
    _field_care(game, action="STABILIZE", check_mode="CERTAIN", ap_cost=2)
    entities = game.state[f"{namespace}.entities"]
    result = game.state[f"{namespace}.state"]["last_medical"]
    assert entities["hex"]["life_threatening"]["state"] == "STABILIZED"
    assert entities["hex"]["res"] == 5
    assert entities["vesper"]["ap"] == 4
    assert "roll" not in result
    _field_care(game)
    assert game.state[f"{namespace}.entities"]["hex"]["res"] == 5


@pytest.mark.parametrize("boundary", ["empty_id", "long_id", "full_history"])
def test_invalid_or_exhausted_issue_identity_does_not_spend_resources(boundary):
    game = _combat_medicine_game()
    namespace = support.builder.PACKAGE_ID
    entities = game.state[f"{namespace}.entities"]
    overrides = {}
    if boundary == "full_history":
        entities["hex"]["battlefield_treatments"] = [f"injury-{i}" for i in range(256)]
    else:
        overrides["issue_id"] = "" if boundary == "empty_id" else "x" * 129
    before = deepcopy(entities)
    if boundary == "empty_id":
        state_before = deepcopy(game.state)
        with pytest.raises(PackageRuntimeError):
            _field_care(game, **overrides)
        assert game.state == state_before
        return
    _field_care(game, **overrides)
    assert game.state[f"{namespace}.entities"] == before
    assert game.state[f"{namespace}.state"]["last_medical"]["status"] == "INVALID_ISSUE_OR_TREATMENT_HISTORY"


def test_other_side_rejection_cannot_display_the_previous_treatment_success():
    game = _combat_medicine_game()
    namespace = support.builder.PACKAGE_ID
    _field_care(game)
    game.state[f"{namespace}.state"]["active_side"] = "hostiles"
    before = deepcopy(game.state[f"{namespace}.entities"])
    result = _field_care(game)
    assert game.state[f"{namespace}.entities"] == before
    summary = {item["label"]: item["value"] for item in result["result_card"]["summary"]}
    assert summary == {"Outcome": "Other team's turn", "RES restored": 0, "AP spent": 0}
