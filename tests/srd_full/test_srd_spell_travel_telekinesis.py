"""Telekinesis resolves actual saves, targets, caster-turn grip lifetime and real clearance."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_levitate import _levitate_case


def _tk_case(spell="misty_step"):
    state, data = _levitate_case(spell)
    state[f"{SRD}.entities"]["guard"].update(size_rank=2)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["strength"] = 0
    state[f"{SRD}.entities"]["hero"].setdefault("saving_throw_modifiers", {})["strength"] = 0
    return state, data


def _install(state, subject="guard", destination=None):
    return _run(
        "install_srd_telekinesis",
        state,
        caster_state="hero",
        subject=subject,
        destination=destination or [5, 0, 20],
        slot_level=5,
        save_dc=15,
        concentration_modifier=3,
    )


def _rolls(monkeypatch, values):
    actual = _TravelDice([(20, v) for v in values])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return actual


@pytest.mark.parametrize("roll, moved", [(1, True), (20, False)])
def test_telekinesis_actual_strength_save_and_immunity_independent_movement(
    monkeypatch, roll, moved
):
    state, _ = _tk_case()
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["restrained"]
    actual = _rolls(monkeypatch, [roll])
    _install(state)
    assert (_point(state, "guard")[2] == 20) is moved
    assert actual.consumed == [(20, roll)]
    target = state[f"{SRD}.entities"]["guard"]
    assert "restrained" not in target["conditions"]
    assert target["last_telekinesis_save_outcome"] == ("failure" if moved else "success")
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "telekinesis"


def test_telekinesis_actual_failed_grip_restrained_suspension_and_exact_release(monkeypatch):
    state, _ = _tk_case()
    _rolls(monkeypatch, [1])
    _install(state)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert source["suspended"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="suspended Telekinesis"):
        _run(
            "move_srd_travel_subject",
            state,
            subject="guard",
            destination=[10, 0, 20],
            plane="material",
            teleport=False,
        )
    assert state == before
    _run("release_srd_telekinesis_grip", state, source_id=source["id"])
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    result = _run("prepare_srd_telekinesis_releases", state)
    assert result.result["context"]["_tk_release_targets"] == ["guard"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert _point(state, "guard") == [5, 0, 20]
    assert state[f"{SRD}.state"]["has_telekinesis_pending_releases"] is True
    after = deepcopy(state)
    _run("release_srd_telekinesis_grip", state, source_id=source["id"])
    assert state == after


@pytest.mark.parametrize(
    "defect",
    [
        "huge_limit",
        "range",
        "distance",
        "blind",
        "wall",
        "occupied",
        "curved",
        "plane",
        "save",
        "ground",
    ],
)
def test_telekinesis_preflight_actual_disqualifications_reject_without_mutation(defect):
    state, _ = _tk_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    destination = [5, 0, 20]
    body = next(e for e in scene["entities"] if e["id"] == "guard")
    if defect == "huge_limit":
        state[f"{SRD}.entities"]["guard"]["size_rank"] = 5
    elif defect == "range":
        body["position"] = [70, 0, 0]
        destination = [70, 0, 20]
    elif defect == "distance":
        destination = [5, 0, 31]
    elif defect == "blind":
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    elif defect == "wall":
        roof = deepcopy(scene["geometry"][0])
        roof.update(id="roof", volume={"kind": "box", "min": [3, -2, 8], "max": [7, 2, 10]})
        roof["metadata"]["ground"] = False
        scene["geometry"].append(roof)
    elif defect == "occupied":
        destination = _point(state, "hero")
    elif defect == "curved":
        body["footprint"] = {"kind": "circle", "radius": 0.4}
    elif defect == "plane":
        body["metadata"]["plane"] = "ethereal"
    elif defect == "save":
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["strength"] = None
    elif defect == "ground":
        scene["geometry"][0]["metadata"]["ground"] = False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "validate_srd_telekinesis_cast",
            state,
            caster_state="hero",
            subject="guard",
            destination=destination,
        )
    assert state == before


def test_telekinesis_unheld_huge_object_auto_moves_without_weight_limit_or_save(monkeypatch):
    state, _ = _tk_case()
    actual = _rolls(monkeypatch, [])
    _run("record_srd_telekinesis_object_size", state, object_id="orb", size="huge")
    _install(state, "orb", [25, 0, 20])
    assert _point(state, "orb") == [25, 0, 20]
    assert actual.consumed == []
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    assert source["creature_id"] is None
    assert source["restraint_id"] is None


@pytest.mark.parametrize("size", ["gargantuan", "missing"])
def test_telekinesis_actual_object_size_must_be_admitted_and_within_printed_limit(size):
    state, _ = _tk_case()
    if size != "missing":
        _run("record_srd_telekinesis_object_size", state, object_id="orb", size=size)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Huge-or-smaller"):
        _run(
            "validate_srd_telekinesis_cast",
            state,
            caster_state="hero",
            subject="orb",
            destination=[25, 0, 20],
        )
    assert state == before


def test_telekinesis_ground_grip_expires_exactly_but_concentration_ability_remains(monkeypatch):
    state, _ = _tk_case()
    _rolls(monkeypatch, [1])
    _install(state, destination=[5, 10, 0])
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["telekinesis_sources"][-1]["active"] is True
    assert not state[f"{SRD}.state"].get("has_telekinesis_pending_releases", False)


def test_telekinesis_later_turn_repeated_actual_save_and_target_switch(monkeypatch):
    state, _ = _tk_case()
    _rolls(monkeypatch, [1, 1])
    _install(state, destination=[5, 10, 0])
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="later turn"):
        _run(
            "use_srd_telekinesis",
            state,
            caster_state="hero",
            source_id=source["id"],
            subject="guard",
            destination=[5, 15, 0],
        )
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run(
        "use_srd_telekinesis",
        state,
        caster_state="hero",
        source_id=source["id"],
        subject="guard",
        destination=[5, 20, 0],
    )
    assert _point(state, "guard") == [5, 20, 0]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("held_as", ["carried", "worn"])
@pytest.mark.parametrize("roll", [1, 20])
def test_telekinesis_actual_holder_save_controls_canonical_detachment(monkeypatch, held_as, roll):
    state, _ = _tk_case()
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    orb["position"] = [5, 0, 0]
    _run("record_srd_telekinesis_object_size", state, object_id="orb", size="small")
    _run(
        "record_srd_object_utility_facts",
        state,
        object_id="orb",
        weight_pounds=10000,
        manufactured=True,
        metal=True,
        object_kind="object",
        inventory_item="iron_orb",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    guard["inventory"] = ["iron_orb", "other_item"]
    guard["held_items"] = ["iron_orb"] if held_as == "carried" else []
    _run("record_srd_object_holder", state, actor_state="guard", object_id="orb", held_as=held_as)
    actual = _rolls(monkeypatch, [roll])
    _install(state, "orb", [5, 10, 0])
    orb = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    assert actual.consumed == [(20, roll)]
    guard = state[f"{SRD}.entities"]["guard"]
    if roll == 1:
        assert orb["position"] == [5, 10, 0]
        assert orb["metadata"]["carried_by"] is None and orb["metadata"]["worn_by"] is None
        assert guard["inventory"] == ["other_item"] and guard["held_items"] == []
    else:
        assert orb["position"] == [5, 0, 0]
        assert orb["metadata"]["carried_by" if held_as == "carried" else "worn_by"] == "guard"
        assert guard["inventory"] == ["iron_orb", "other_item"]


def test_telekinesis_same_target_renewal_and_successful_save_release_at_real_caster_turn(
    monkeypatch,
):
    state, data = _tk_case()
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    state[f"{SRD}.combat"]["encounter_active"] = True
    actual = _rolls(monkeypatch, [1, 1, 20])
    _install(state)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    _run("end_combat_turn", state, actor="hero")
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    _run(
        "use_srd_telekinesis",
        state,
        caster_state="hero",
        source_id=source["id"],
        subject="guard",
        destination=[5, 0, 25],
    )
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert _point(state, "guard") == [5, 0, 25]
    assert not state[f"{SRD}.state"].get("has_telekinesis_pending_releases", False)
    _run("end_combat_turn", state, actor="hero")
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    _run(
        "use_srd_telekinesis",
        state,
        caster_state="hero",
        source_id=source["id"],
        subject="guard",
        destination=[5, 0, 30],
    )
    assert _point(state, "guard") == [5, 0, 25]
    _run("end_combat_turn", state, actor="hero")
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["has_telekinesis_pending_releases"] is True
    assert actual.consumed == [(20, 1), (20, 1), (20, 20)]


@pytest.mark.parametrize("native", [False, True])
def test_telekinesis_exact_release_preserves_native_and_other_source_restraint(monkeypatch, native):
    state, _ = _tk_case()
    if native:
        state[f"{SRD}.entities"]["guard"]["conditions"] = ["restrained"]
    else:
        _run(
            "install_srd_timed_condition_source",
            state,
            target_state="guard",
            caster_state="hero",
            lasting_state="state",
            spell="entangle",
            condition="restrained",
            save_dc=15,
            duration_rounds=100,
            repeat_save_timing="none",
        )
    _rolls(monkeypatch, [1])
    _install(state, destination=[5, 10, 0])
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    _run("release_srd_telekinesis_grip", state, source_id=source["id"])
    _run("prepare_srd_telekinesis_releases", state)
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_public_telekinesis_paid_cast_later_magic_reopens_retries_and_expires(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    import tests.srd_full.test_srd_public_spell_casting as public
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests.srd_full.test_srd_spell_travel import _case

    def actual_case(_spell):
        state, data = _tk_case("telekinesis")
        _, _, params = _case("telekinesis")
        params.pop("targets", None)
        params.update(telekinesis_subject="guard", telekinesis_destination=[5, 10, 0])
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", actual_case)
    actual = _rolls(monkeypatch, [1, 20])
    database = tmp_path / "telekinesis-public.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "telekinesis")
        cast = ActionCommand(
            campaign_id="casting",
            action_id="telekinesis",
            idempotency_key="telekinesis",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(cast) == receipt
        saved = owner.snapshot("casting")
        assert _point(saved.state, "guard") == [5, 10, 0]
        assert "restrained" in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
        source_id = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]["id"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        clock = replace(
            cast,
            action_id="one-round",
            idempotency_key="one-round",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 1},
        )
        receipt = mechanics.execute_action(clock)
        assert mechanics.execute_action(clock) == receipt
        current = owner.snapshot("casting")
        assert "restrained" not in current.state[f"{SRD}.entities"]["guard"]["conditions"]
        later = replace(
            cast,
            action_id="later",
            idempotency_key="later",
            expected_revision=current.revision,
            tool_name="use_srd_telekinesis",
            parameters={
                "caster_state": "hero",
                "source_id": source_id,
                "subject": "guard",
                "destination": [5, 20, 0],
            },
        )
        receipt = mechanics.execute_action(later)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(later) == receipt
        current = owner.snapshot("casting")
        assert _point(current.state, "guard") == [5, 10, 0]
        assert actual.consumed == [(20, 1), (20, 20)]
        assert current.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        expiry = replace(
            clock,
            action_id="expiry",
            idempotency_key="expiry",
            expected_revision=current.revision,
            parameters={"elapsed_rounds": 99},
        )
        receipt = mechanics.execute_action(expiry)
        assert mechanics.execute_action(expiry) == receipt
        current = owner.snapshot("casting")
        assert current.state[f"{SRD}.state"]["telekinesis_sources"][-1]["active"] is False
        before = current.state
        with pytest.raises(MechanicsRejected, match="live Telekinesis"):
            mechanics.execute_action(
                replace(
                    later,
                    action_id="expired",
                    idempotency_key="expired",
                    expected_revision=current.revision,
                )
            )
        assert owner.snapshot("casting").state == before
