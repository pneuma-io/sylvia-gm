"""Falling and Slow Fall share damage, Reaction, condition and persistence rules."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_unarmed_creatures import _case


def _fall_case(level=4):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": level}], level=level)
    state[f"{SRD}.entities"]["hero"].update(
        name="Monk", hp=500, max_hp=500, temp_hp=0, reaction_available=True, conditions=[],
        death_successes=0, death_failures=0, stable=False,
        stable_recovery_hours_remaining=0,
    )
    return state, data


def _fall(state, data, **parameters):
    return _run("resolve_srd_fall", state, participant_data=data,
                target_state="hero", **parameters)


@pytest.mark.parametrize("feet,dice_count", [(9.5, 0), (10, 1), (29.5, 2), (200, 20), (1000, 20)])
def test_falling_rolls_only_full_ten_feet_and_caps_damage(feet, dice_count):
    state, data = _fall_case()
    result = _fall(state, data, distance=feet)
    context = result.result["context"]
    assert context["_fall_dice"] == dice_count
    damage = context["_fall_raw_damage"]
    assert dice_count <= damage <= dice_count * 6
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 500 - damage
    assert ("prone" in hero["conditions"]) is (damage > 0)
    assert hero["reaction_available"] is True


def test_slow_fall_reduces_before_resistance_and_temporary_hp():
    state, data = _fall_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(damage_resistances=["bludgeoning"], temp_hp=5)
    result = _fall(state, data, distance=200, reaction="slow_fall")
    raw = result.result["context"]["_fall_raw_damage"]
    effective = max(0, raw - 20) // 2
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 500 - max(0, effective - 5)
    assert hero["temp_hp"] == max(0, 5 - effective)
    assert hero["reaction_available"] is False
    assert hero["last_reaction"] == "slow_fall"
    assert "Slow Fall" in str(result.result["result_card"])


def test_slow_fall_can_prevent_all_damage_and_new_prone():
    state, data = _fall_case()
    _fall(state, data, distance=30, reaction="slow_fall")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 500
    assert "prone" not in hero["conditions"]
    assert hero["reaction_available"] is False


@pytest.mark.parametrize("npc", [False, True])
@pytest.mark.parametrize("skill,seed,success", [("athletics", 0, True), ("acrobatics", 1, False)])
def test_liquid_landing_uses_saved_check_and_spends_one_reaction(
    monkeypatch, npc, skill, seed, success,
):
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _fall_case()
    data["abilities"].update(strength=14, dexterity=14)
    data["skill_proficiencies"] = ["athletics"]
    target = "guard" if npc else "hero"
    state[f"{SRD}.entities"][target].update(
        reaction_available=True, skill_check_modifiers={"athletics": 2, "acrobatics": 1},
        hp=500, max_hp=500, temp_hp=0,
    )
    result = _run("resolve_srd_fall", state, participant_data=data, target_state=target,
                  distance=50, landing="liquid", reaction="water_" + skill)
    context = result.result["context"]
    assert context["_fall_check_outcome"] == ("success" if success else "failure")
    expected_modifier = (2 if skill == "athletics" else 1) if npc else (
        4 if skill == "athletics" else 2)
    assert context["_fall_check_modifier"] == expected_modifier
    assert context["_fall_check_total"] == context["_fall_check_roll"]["total"] + expected_modifier
    expected_damage = context["_fall_raw_damage"] // 2 if success else context["_fall_raw_damage"]
    after = state[f"{SRD}.entities"][target]
    assert after["hp"] == 500 - expected_damage
    assert after["reaction_available"] is False
    assert "DC 15" in str(result.result["result_card"])


@pytest.mark.parametrize("problem", ["level", "spent", "incapacitated", "dry_landing", "npc_skill"])
def test_invalid_reaction_rejects_without_damage_or_spending(problem):
    state, data = _fall_case(3 if problem == "level" else 4)
    target = "hero"
    reaction, landing = "slow_fall", "ground"
    if problem == "spent":
        state[f"{SRD}.entities"]["hero"]["reaction_available"] = False
    elif problem == "incapacitated":
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["stunned"]
    elif problem == "dry_landing":
        reaction = "water_athletics"
    elif problem == "npc_skill":
        target, reaction, landing = "guard", "water_athletics", "liquid"
        state[f"{SRD}.entities"][target]["reaction_available"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("resolve_srd_fall", state, participant_data=data, target_state=target,
             distance=30, landing=landing, reaction=reaction)
    assert state == before


@pytest.mark.parametrize("defense", ["immunity", "temporary_hp", "prone_immunity"])
def test_landing_prone_depends_on_damage_taken_not_hp_lost(defense):
    state, data = _fall_case()
    hero = state[f"{SRD}.entities"]["hero"]
    if defense == "immunity":
        hero["damage_immunities"] = ["bludgeoning"]
    elif defense == "temporary_hp":
        hero["temp_hp"] = 100
    else:
        hero["condition_immunities"] = ["prone"]
    _fall(state, data, distance=30)
    hero = state[f"{SRD}.entities"]["hero"]
    assert ("prone" in hero["conditions"]) is (defense == "temporary_hp")
    if defense != "prone_immunity":
        assert hero["hp"] == 500


@pytest.mark.parametrize("combat", [False, True])
def test_reaction_recovers_with_exploration_time_but_never_mid_combat_turn(combat):
    state, data = _fall_case()
    state[f"{SRD}.combat"].update(encounter_active=combat, turn_active=combat)
    _fall(state, data, distance=10, reaction="slow_fall")
    with pytest.raises(RulesetPreconditionRejected):
        _fall(state, data, distance=10, reaction="slow_fall")
    if combat:
        state[f"{SRD}.state"]["lasting_tick"] += 2
        with pytest.raises(RulesetPreconditionRejected):
            _fall(state, data, distance=10, reaction="slow_fall")
    else:
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
        assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is True
        assert _fall(state, data, distance=10, reaction="slow_fall").result["status"] == "committed"


def test_adding_missing_npc_skill_facts_preserves_combat_state_and_rejects_rewrites():
    state, _ = _fall_case()
    npc = state[f"{SRD}.entities"]["guard"]
    npc.update(hp=17, reaction_available=False)
    before = deepcopy(npc)
    _run("record_srd_creature_skill_checks", state, target_state="guard",
         skill_checks=[{"skill": "athletics", "modifier": 2}])
    assert state[f"{SRD}.entities"]["guard"] == {
        **before, "skill_check_modifiers": {"athletics": 2},
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("record_srd_creature_skill_checks", state, target_state="guard",
             skill_checks=[{"skill": "acrobatics", "modifier": 1},
                           {"skill": "athletics", "modifier": 99}])
    assert state == before


def test_public_falls_preserve_npc_facts_damage_and_reactions_after_reopen(tmp_path):
    import io
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from sylvia_gm.application.bundled_packages import bundled_srd_archive
    from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
    from tests.srd_full.test_srd_second_wind import _installed_fighter

    database = tmp_path / "falling.db"
    state, data = _fall_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)

    def act(owner, tool_name, **parameters):
        revision = owner.snapshot("falling").revision
        command = ActionCommand(
            campaign_id="falling", action_id=f"{tool_name}-{revision}",
            idempotency_key=f"{tool_name}-{revision}", owner_id="test", writer=WriterRole.DM,
            expected_revision=revision, turn_id="turn", package_id=SRD,
            tool_name=tool_name, parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        return command, receipt

    with AuthorityOwner.open(database, "test") as owner:
        package = owner.install_package_archive(io.BytesIO(bundled_srd_archive()), install_id="srd")
        actor = _installed_fighter(package.package_lock, fighter=True)
        actor = replace(actor, data={**actor.data, **data})
        owner.create_campaign(CampaignCreate(
            campaign_id="falling", display_name="Synthetic falling", writer=WriterRole.DM,
            package_lock=package.package_lock, turn_id="turn", participants=(actor,),
            initial_state=tuple(StateValue(SRD, key, state[f"{SRD}.{key}"])
                                for key in ("combat", "entities", "state")),
        ))
        act(owner, "prepare_srd_creature", creature_id="diver", name="Diver",
            source="Synthetic stat block", armor_class=12, hit_points=100, speed=30,
            size_rank=2, initiative_modifier=1)
        act(owner, "resolve_srd_fall", target_state="diver", distance=10)
        wounded_hp = owner.snapshot("falling").state[f"{SRD}.entities"]["diver"]["hp"]
        act(owner, "record_srd_creature_skill_checks", target_state="diver",
            skill_checks=[{"skill": "athletics", "modifier": 2}])
        assert owner.snapshot("falling").state[f"{SRD}.entities"]["diver"]["hp"] == wounded_hp
        npc_action, npc_receipt = act(owner, "resolve_srd_fall", target_state="diver",
                                     distance=30, landing="liquid", reaction="water_athletics")
        monk_action, monk_receipt = act(owner, "resolve_srd_fall", target_state="hero",
                                       distance=30, reaction="slow_fall")
        saved = owner.snapshot("falling")
        assert saved.state[f"{SRD}.entities"]["diver"]["reaction_available"] is False
        assert saved.state[f"{SRD}.entities"]["hero"]["hp"] == 500
        assert saved.state[f"{SRD}.entities"]["hero"]["reaction_available"] is False
        assert monk_receipt.result["result_card"]["title"] == "Falling"
        assert set(saved.state[f"{SRD}.state"]["reaction_recovery_targets"]) == {
            "hero", "diver",
        }
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("falling").state == saved.state
        assert owner.execute_action(npc_action) == npc_receipt
        assert owner.execute_action(monk_action) == monk_receipt
        assert owner.snapshot("falling").state == saved.state
        time_action, time_receipt = act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        recovered = owner.snapshot("falling")
        for creature in ("hero", "diver"):
            assert recovered.state[f"{SRD}.entities"][creature]["reaction_available"] is True
            assert (
                recovered.state[f"{SRD}.entities"][creature]["hp"]
                == saved.state[f"{SRD}.entities"][creature]["hp"]
            )
        assert recovered.state[f"{SRD}.state"]["reaction_recovery_targets"] == []
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("falling").state == recovered.state
        assert owner.execute_action(time_action) == time_receipt
        assert owner.snapshot("falling").state == recovered.state
