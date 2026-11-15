"""Levitate qualifies saved bodies and weights before an actual source cast can pay."""

from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point, _TravelDice, dice


def _levitate_case(spell="misty_step"):
    state, data, _ = _case(spell)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [
        {
            "id": "ground",
            "map_id": "information",
            "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": True,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        },
    ]
    return state, data


def _loose_object(state, weight=500, *, loose=True):
    _run(
        "record_srd_object_utility_facts",
        state,
        object_id="orb",
        weight_pounds=weight,
        manufactured=True,
        metal=False,
        object_kind="object",
    )
    _run("record_srd_levitate_object_looseness", state, object_id="orb", loose=loose)


@pytest.mark.parametrize("subject", ["hero", "guard", "orb"])
def test_levitate_preflight_uses_actual_selected_body_without_position_mutation(subject):
    state, _ = _levitate_case()
    if subject == "orb":
        _loose_object(state)
    before = deepcopy(state)
    result = _run("validate_srd_levitate_cast", state, caster_state="hero", subject=subject)
    assert state == before
    assert result.result["context"]["_levitate_destination"] == [*_point(state, subject)[:2], 20]
    sweep = next(
        row["query"] for row in result.result["spatial_evidence"] if row["operation"] == "sweep"
    )
    assert sweep["moving_entity_id"] == subject


@pytest.mark.parametrize("weight", [500.1, 501])
def test_levitate_actual_object_weight_above_printed_limit_rejects_without_mutation(weight):
    state, _ = _levitate_case()
    _loose_object(state, weight)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="five hundred"):
        _run("validate_srd_levitate_cast", state, caster_state="hero", subject="orb")
    assert state == before


@pytest.mark.parametrize("defect", ["fixed", "held", "plane", "distant", "ceiling", "round"])
def test_levitate_preflight_rejects_actual_disqualifications_without_payment_or_motion(defect):
    state, _ = _levitate_case()
    _loose_object(state, loose=defect != "fixed")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    orb = next(row for row in scene["entities"] if row["id"] == "orb")
    if defect == "held":
        orb["metadata"]["carried_by"] = "hero"
    elif defect == "plane":
        orb["metadata"]["plane"] = "ethereal"
    elif defect == "distant":
        orb["position"] = [61, 0, 0]
    elif defect == "ceiling":
        scene["geometry"].append(
            {
                "id": "ceiling",
                "map_id": "information",
                "volume": {"kind": "box", "min": [20, -5, 8], "max": [30, 5, 10]},
                "active": True,
                "movement": "blocking",
                "occupies_cells": True,
                "visibility": "opaque",
                "metadata": {"plane": "material"},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
    elif defect == "round":
        orb["footprint"] = {"kind": "circle", "radius": 0.4}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("validate_srd_levitate_cast", state, caster_state="hero", subject="orb")
    assert state == before


def test_levitate_loose_object_qualification_cannot_rewrite_an_existing_physical_fact():
    state, _ = _levitate_case()
    _loose_object(state, loose=False)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="physical qualification"):
        _run("record_srd_levitate_object_looseness", state, object_id="orb", loose=True)
    assert state == before


@pytest.mark.parametrize("defect", ["blind", "ground", "save_modifier"])
def test_levitate_prepayment_requires_actual_sight_ground_and_unwilling_save_facts(defect):
    state, _ = _levitate_case()
    if defect == "blind":
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    elif defect == "ground":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["metadata"]["ground"] = False
    else:
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("validate_srd_levitate_cast", state, caster_state="hero", subject="guard",
             all_targets_willing=defect != "save_modifier")
    assert state == before


def _install(state, subject="guard", **changes):
    return _run("install_srd_levitate", state, caster_state="hero", subject=subject,
                slot_level=2, save_dc=15, concentration_modifier=3,
                all_targets_willing=changes.pop("all_targets_willing", True), **changes)


@pytest.mark.parametrize("subject", ["hero", "guard", "orb"])
def test_levitate_actual_initial_rise_exact_expiry_gentle_ground_and_repeat(subject):
    state, _ = _levitate_case()
    if subject == "orb":
        _loose_object(state)
    hp = state[f"{SRD}.entities"]["hero"]["hp"]
    origin = _point(state, subject)
    _install(state, subject)
    assert _point(state, subject) == [*origin[:2], 20]
    source = state[f"{SRD}.state"]["levitate_sources"][-1]
    assert source["subject"] == subject and source["active"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=99)
    assert _point(state, subject)[2] == 20
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _point(state, subject) == origin
    assert state[f"{SRD}.state"]["levitate_sources"][-1]["settled"] is True
    assert state[f"{SRD}.entities"]["hero"]["hp"] == hp
    after = deepcopy(state)
    _run("settle_srd_levitate_returns", state)
    assert state == after


@pytest.mark.parametrize("roll, affected", [(1, True), (20, False)])
def test_levitate_actual_constitution_save_controls_rise_independently_of_immunity(
    monkeypatch, roll, affected
):
    state, _ = _levitate_case()
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"]["constitution"] = 0
    target["condition_immunities"] = ["restrained", "incapacitated"]
    actual = _TravelDice([(20, roll)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _install(state, all_targets_willing=False)
    assert (_point(state, "guard")[2] == 20) is affected
    assert actual.consumed == [(20, roll)]
    target = state[f"{SRD}.entities"]["guard"]
    assert target["last_levitate_save_outcome"] == ("failure" if affected else "success")


def test_levitate_suspension_rejects_ordinary_walking_and_forged_private_destination():
    state, _ = _levitate_case()
    _install(state, "hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="suspended Levitate"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 20],
             mode="walk")
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="exact saved"):
        _run("move_srd_travel_subject", state, subject="hero", destination=[5, 0, 20],
             plane="material", teleport=False,
             levitate_source_id=state[f"{SRD}.state"]["levitate_sources"][-1]["id"])
    assert state == before


@pytest.mark.parametrize("defect", ["ground", "occupied", "ceiling"])
def test_levitate_gentle_return_requires_actual_later_support_and_clear_body_path(defect):
    state, _ = _levitate_case()
    _install(state, "hero")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if defect == "ground":
        scene["geometry"][0]["metadata"]["ground"] = False
    elif defect == "occupied":
        next(row for row in scene["entities"] if row["id"] == "guard")["position"] = [0, 0, 0]
    else:
        roof = deepcopy(scene["geometry"][0])
        roof.update(id="roof", volume={"kind": "box", "min": [-2, -2, 8], "max": [2, 2, 10]})
        roof["metadata"]["ground"] = False
        scene["geometry"].append(roof)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert state == before


def test_levitate_voluntary_end_queues_and_settles_once_without_damage_or_movement_cost():
    state, _ = _levitate_case()
    _install(state)
    hero = state[f"{SRD}.entities"]["hero"]
    before_hp, spent = hero["hp"], hero.get("scene_movement_spent")
    _run("end_srd_concentration_voluntarily", state, caster_state="hero")
    assert _point(state, "guard") == [5, 0, 0]
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == before_hp and hero.get("scene_movement_spent") == spent
    assert "prone" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["levitate_sources"][-1]["settled"] is True


def test_levitate_raw_retirement_only_marks_return_for_outer_completed_action():
    state, _ = _levitate_case()
    _install(state)
    _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
    assert _point(state, "guard")[2] == 20
    source = state[f"{SRD}.state"]["levitate_sources"][-1]
    assert source["return_pending"] is True and source["active"] is False
    assert state[f"{SRD}.state"]["has_levitate_pending_returns"] is True
    _run("settle_srd_levitate_returns", state)
    assert _point(state, "guard") == [5, 0, 0]


def test_levitate_new_same_caster_concentration_settles_exact_prior_body_only():
    state, _ = _levitate_case()
    _loose_object(state)
    _install(state)
    prior = state[f"{SRD}.state"]["levitate_sources"][-1]["id"]
    _install(state, "orb", rise=10)
    assert _point(state, "guard") == [5, 0, 0]
    assert _point(state, "orb") == [25, 0, 10]
    records = state[f"{SRD}.state"]["levitate_sources"]
    assert records[0]["id"] == prior and records[0]["settled"] is True
    assert records[1]["active"] is True and records[1]["sequence"] != records[0]["sequence"]


def _height(state, change, source_id=None):
    return _run("adjust_srd_levitate_height", state, actor_state="hero",
                source_id=source_id or state[f"{SRD}.state"]["levitate_sources"][-1]["id"],
                altitude_change=change)


@pytest.mark.parametrize("subject", ["hero", "guard", "orb"])
def test_levitate_height_control_uses_real_body_turn_allowance_and_self_movement(subject):
    state, _ = _levitate_case()
    if subject == "orb":
        _loose_object(state)
    _install(state, subject)
    _height(state, 12)
    _height(state, -8)
    assert _point(state, subject)[2] == 24
    source = state[f"{SRD}.state"]["levitate_sources"][-1]
    assert source["altitude_spent"] == 20 and source["movement_destination"] is None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="twenty feet"):
        _height(state, 1)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    if subject == "hero":
        assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 20
    _height(state, 20)
    assert _point(state, subject)[2] == 44


def test_levitate_other_body_height_uses_actual_magic_action_and_new_caster_turn():
    state, data = _levitate_case()
    _install(state)
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    state[f"{SRD}.combat"]["encounter_active"] = True
    _height(state, 10)
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert _point(state, "guard")[2] == 30
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="action remaining"):
        _height(state, 5)
    assert state == before
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    _height(state, -20)
    assert _point(state, "guard")[2] == 10


@pytest.mark.parametrize("defect", ["caster", "range", "plane", "ceiling", "occupied"])
def test_levitate_height_actual_preflight_rejections_preserve_source_and_costs(defect):
    state, _ = _levitate_case()
    _install(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if defect == "caster":
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["incapacitated"]
    elif defect == "range":
        next(row for row in scene["entities"] if row["id"] == "guard")["position"][0] = 61
    elif defect == "plane":
        body = next(row for row in scene["entities"] if row["id"] == "guard")
        body["metadata"]["plane"] = "ethereal"
    elif defect == "ceiling":
        ceiling = deepcopy(scene["geometry"][0])
        ceiling.update(id="ceiling", volume={"kind": "box", "min": [4, -2, 27], "max": [6, 2, 29]})
        ceiling["metadata"]["ground"] = False
        scene["geometry"].append(ceiling)
    else:
        next(row for row in scene["entities"] if row["id"] == "orb")["position"] = [5, 0, 30]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _height(state, 10)
    assert state == before


def test_levitate_suspended_self_can_use_genuine_paid_misty_step_without_walking():
    state, data = _levitate_case()
    _install(state, "hero")
    _, _, params = _case("misty_step", travel_destination=[20, 0, 20])
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _point(state) == [20, 0, 20]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    source = state[f"{SRD}.state"]["levitate_sources"][-1]
    assert source["active"] is True
    _height(state, 5)
    assert _point(state) == [20, 0, 25]


def test_public_levitate_paid_rise_reopens_then_gently_returns_once_with_exact_retry(
    tmp_path, monkeypatch
):
    import tests.srd_full.test_srd_public_spell_casting as public

    def actual_case(_spell):
        state, data = _levitate_case("levitate")
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = 0
        _, _, params = _case("levitate")
        params.pop("targets", None)
        params.update(levitate_subject="guard", levitate_rise=20, all_targets_willing=False)
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", actual_case)
    actual = _TravelDice([(20, 1)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "levitate.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "levitate")
        command = ActionCommand(
            campaign_id="casting", action_id="levitate", idempotency_key="levitate",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD, tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(replace(command, action_id="missing-subject",
                                            idempotency_key="missing-subject",
                                            parameters={**params, "levitate_subject": "missing"}))
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").revision == before.revision
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
        assert _point(saved.state, "guard") == [5, 0, 20]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert saved.state[f"{SRD}.entities"]["guard"]["last_levitate_save_outcome"] == "failure"
        assert actual.consumed == [(20, 1)]
        hp = saved.state[f"{SRD}.entities"]["guard"]["hp"]
        movement = saved.state[f"{SRD}.entities"]["guard"].get("scene_movement_spent")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        before_expiry = replace(command, action_id="before-expiry", idempotency_key="before-expiry",
                                expected_revision=saved.revision,
                                tool_name="advance_srd_lasting_time",
                                parameters={"elapsed_rounds": 99})
        mechanics.execute_action(before_expiry)
        assert _point(owner.snapshot("casting").state, "guard")[2] == 20
        ending = replace(before_expiry, action_id="ending", idempotency_key="ending",
                         expected_revision=owner.snapshot("casting").revision,
                         parameters={"elapsed_rounds": 1})
        receipt = mechanics.execute_action(ending)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(ending) == receipt
        after = owner.snapshot("casting")
        assert _point(after.state, "guard") == [5, 0, 0]
        assert after.state[f"{SRD}.state"]["levitate_sources"][-1]["settled"] is True
        target = after.state[f"{SRD}.entities"]["guard"]
        assert target["hp"] == hp and target.get("scene_movement_spent") == movement
        assert "prone" not in target["conditions"]
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        moved = mechanics.execute_action(replace(
            command, action_id="later-move", idempotency_key="later-move",
            expected_revision=after.revision, tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [8, 0, 0], "mode": "walk"},
        ))
        assert moved.status is ActionStatus.COMMITTED
        assert _point(owner.snapshot("casting").state, "guard") == [8, 0, 0]


def test_public_levitate_real_height_control_reopens_retries_and_ends_gently(tmp_path, monkeypatch):
    import tests.srd_full.test_srd_public_spell_casting as public

    def actual_case(_spell):
        state, data = _levitate_case("levitate")
        _, _, params = _case("levitate")
        params.pop("targets", None)
        params.update(levitate_subject="hero", levitate_rise=20, all_targets_willing=True)
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", actual_case)
    database = tmp_path / "levitate-control.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "levitate")
        cast = ActionCommand(
            campaign_id="casting", action_id="levitate", idempotency_key="levitate",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD, tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        mechanics.execute_action(cast)
        current = owner.snapshot("casting")
        source_id = current.state[f"{SRD}.state"]["levitate_sources"][-1]["id"]
        control = replace(cast, action_id="control", idempotency_key="control",
                          expected_revision=current.revision,
                          tool_name="adjust_srd_levitate_height",
                          parameters={"actor_state": "hero", "source_id": source_id,
                                      "altitude_change": 12})
        receipt = mechanics.execute_action(control)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(control) == receipt
        saved = owner.snapshot("casting")
        assert _point(saved.state) == [0, 0, 32]
        assert saved.state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 12
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="twenty feet"):
            mechanics.execute_action(replace(
                control, action_id="too-much", idempotency_key="too-much",
                expected_revision=before.revision,
                parameters={**control.parameters, "altitude_change": 9},
            ))
        assert owner.snapshot("casting").state == before.state
        smaller = replace(control, action_id="remaining", idempotency_key="remaining",
                          expected_revision=before.revision,
                          parameters={**control.parameters, "altitude_change": -8})
        receipt = mechanics.execute_action(smaller)
        assert mechanics.execute_action(smaller) == receipt
        current = owner.snapshot("casting")
        assert _point(current.state) == [0, 0, 24]
        assert current.state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 20
        ending = replace(cast, action_id="end", idempotency_key="end",
                         expected_revision=current.revision,
                         tool_name="end_concentration",
                         parameters={"caster_state": "hero"})
        receipt = mechanics.execute_action(ending)
        assert mechanics.execute_action(ending) == receipt
        after = owner.snapshot("casting")
        assert _point(after.state) == [0, 0, 0]
        assert after.state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 20
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
