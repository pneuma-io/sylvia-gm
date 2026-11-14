"""Progressive afflictions use real casts, later saves, exact cures, and saved scene effects."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_recovery import _cast as _recovery_cast
from tests.srd_full.test_srd_spell_recovery import _control_source
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene, _turn


def _case(monkeypatch, spell, *, modifier=-100, hp=1000):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [3, 0, 0]
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=hp, max_hp=1000, temp_hp=0, creature_type="humanoid")
    target["saving_throw_modifiers"].update(
        constitution=modifier, charisma=modifier, wisdom=modifier
    )
    params.update(targets=["guard"])
    if spell == "contagion":
        params["chosen_ability"] = "wisdom"
    if spell == "imprisonment":
        _run(
            "record_srd_imprisonment_statuette",
            state,
            caster_state="hero",
            target_state="guard",
            material_id=params["material_item_ids"][0],
            depicts_target=True,
        )
        params.update(
            imprisonment_mode="chaining",
            imprisonment_trigger="Offer the blue flower",
            imprisonment_trigger_observable=True,
            imprisonment_trigger_likely_decade=True,
            imprisonment_component_id=params["material_item_ids"][0],
        )
    return state, data, params


def _add_spell(state, spell):
    supplies, data, params = _spell_case(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    new_hero = supplies[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"] = [spell]
    hero["spell_slot_cast_this_turn"] = False
    for lot in new_hero["spell_materials"]:
        lot["id"] = "cure_" + lot["id"]
        hero["spell_materials"].append(lot)
        hero["inventory"].append(lot["inventory_item"])
    params["material_item_ids"] = [lot["id"] for lot in new_hero["spell_materials"]]
    return data, params


def _cast(state, data, params):
    if params["spell"] == "imprisonment":
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result


def _source(state, profile=None):
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    for source_id in state[f"{SRD}.entities"]["guard"].get("timed_condition_source_ids", []):
        source = sources[source_id]
        if source["installed"] and (profile is None or source["control_profile"] == profile):
            return source_id, source
    raise AssertionError("No installed source")


@pytest.mark.parametrize("modifier,failed", [(-100, True), (100, False)])
def test_contagion_actual_initial_save_damage_and_paid_no_effect(monkeypatch, modifier, failed):
    state, data, params = _case(monkeypatch, "contagion", modifier=modifier)
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert ("poisoned" in target["conditions"]) is failed
    assert (target["hp"] < 1000) is failed
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    if failed:
        _, source = _source(state)
        assert source["expires_tick"] - source["started_tick"] == 201600
        assert source["progressive_failures"] == source["progressive_successes"] == 0
        assert target["contagion_disadvantage_abilities"] == ["wisdom"]


def test_contagion_chosen_save_disadvantage_and_cure_veto_use_actual_dice(monkeypatch):
    state, data, params = _case(monkeypatch, "contagion")
    _cast(state, data, params)
    source_id, _ = _source(state)
    result = _run(
        "d20_test",
        state,
        actor_state="guard",
        ability="wisdom",
        kind="saving_throw",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    ).result
    assert result["context"]["_d20_test"]["count"] == 2
    _run("remove_srd_condition", state, target="guard", condition="poisoned")
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = 100
    _run("remove_srd_condition", state, target="guard", condition="poisoned")
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]


@pytest.mark.parametrize("successes", [True, False])
def test_contagion_real_target_turns_reach_three_cumulative_results(monkeypatch, successes):
    state, data, params = _case(monkeypatch, "contagion")
    _cast(state, data, params)
    source_id, _ = _source(state)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = (
        100 if successes else -100
    )
    for _ in range(3):
        _turn(state)
        _run("end_combat_turn", state)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["progressive_successes" if successes else "progressive_failures"] == 3
    assert source["installed"] is not successes
    if not successes:
        assert source["contagion_locked"]
        last = deepcopy(source["last_repeat_save"])
        _turn(state)
        _run("end_combat_turn", state)
        assert (
            state[f"{SRD}.state"]["timed_condition_sources"][source_id]["last_repeat_save"] == last
        )
        state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
        _run("advance_srd_lasting_time", state, elapsed_rounds=100000)
        _run("advance_srd_lasting_time", state, elapsed_rounds=800)
        assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]


def test_flesh_failed_save_counts_initial_failure_then_three_failures_petrify(monkeypatch):
    state, data, params = _case(monkeypatch, "flesh_to_stone")
    _cast(state, data, params)
    source_id, source = _source(state)
    assert source["progressive_failures"] == 1
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    for _ in range(2):
        _turn(state)
        _run("end_combat_turn", state)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["progressive_failures"] == 3 and source["condition"] == "petrified"
    assert "petrified" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_srd_concentration_voluntarily", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]


def test_flesh_full_concentration_becomes_permanent_then_actual_greater_cures(monkeypatch):
    state, data, params = _case(monkeypatch, "flesh_to_stone")
    _cast(state, data, params)
    source_id, _ = _source(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["installed"] and source["flesh_permanent"] and source["expires_tick"] is None
    assert source["concentration_sequence"] == 0 and source["condition"] == "petrified"
    assert source["dispel_ongoing"] is False and source["ongoing_duration"] is False
    data, dispel = _add_spell(state, "dispel_magic")
    dispel["slot_resource"] = "spell_slot_9"
    _cast(state, data, dispel)
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    data, cure = _add_spell(state, "greater_restoration")
    cure["recovery_choice"] = "petrified"
    _cast(state, data, cure)
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    assert "petrified" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


def test_flesh_successful_construct_save_expires_at_actual_caster_next_turn(monkeypatch):
    state, data, params = _case(monkeypatch, "flesh_to_stone")
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "construct"
    random_state = dice._rng.getstate()
    result = _cast(state, data, params)
    source_id, _ = _source(state, "flesh_slow")
    assert state[f"{SRD}.entities"]["guard"]["timed_control_speed_zero"]
    assert "roll" in result["context"]["_spell_detail_rows"][0]["value"]
    assert dice._rng.getstate() == random_state
    unrelated = _control_source(state, spell="charm_monster", condition="charmed")
    _turn(state, actor="hero", data=data)
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not sources[source_id]["installed"] and sources[unrelated]["installed"]
    assert not state[f"{SRD}.entities"]["guard"]["timed_control_speed_zero"]
    _run("end_srd_concentration_voluntarily", state, caster_state="hero")
    assert state[f"{SRD}.state"]["timed_condition_sources"][unrelated]["installed"]


@pytest.mark.parametrize(
    "hp,conditions",
    [
        (20, {"dead"}),
        (30, {"blinded", "deafened", "stunned"}),
        (40, {"blinded", "deafened"}),
        (50, {"deafened"}),
        (51, set()),
    ],
)
def test_divine_word_actual_hp_thresholds(monkeypatch, hp, conditions):
    state, data, params = _case(monkeypatch, "divine_word", hp=hp)
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert set(target["conditions"]) == conditions
    assert target["is_alive"] is (hp > 20)
    if hp > 20 and hp <= 50:
        duration = 600 if hp <= 30 else (100 if hp <= 40 else 10)
        _run("advance_srd_lasting_time", state, elapsed_rounds=duration)
        assert state[f"{SRD}.entities"]["guard"]["conditions"] == []


def test_divine_word_actual_plane_return_ban_and_expiry(monkeypatch):
    state, data, params = _case(monkeypatch, "divine_word", hp=100)
    state[f"{SRD}.entities"]["guard"].update(creature_type="fiend", home_plane="abyss")
    _cast(state, data, params)
    assert (
        next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard")[
            "metadata"
        ]["plane"]
        == "abyss"
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Divine Word"):
        _run(
            "check_srd_progressive_travel",
            state,
            target_state="guard",
            destination_plane="material",
        )
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    _run("check_srd_progressive_travel", state, target_state="guard", destination_plane="material")


def test_imprisonment_actual_long_cast_component_chain_and_exact_observable_end(monkeypatch):
    state, data, params = _case(monkeypatch, "imprisonment")
    _cast(state, data, params)
    source_id, source = _source(state)
    assert (
        source["expires_tick"] is None
        and source["imprisonment_component_id"] == params["material_item_ids"][0]
    )
    assert state[f"{SRD}.entities"]["guard"]["imprisonment_cannot_move"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    with pytest.raises(RulesetPreconditionRejected, match="chains"):
        _run(
            "check_srd_progressive_travel",
            state,
            target_state="guard",
            destination_plane="material",
            teleport=False,
        )
    _run(
        "observe_srd_imprisonment_trigger",
        state,
        target_state="guard",
        source_id=source_id,
        observed_trigger=params["imprisonment_trigger"],
        trigger_observed=True,
    )
    assert not state[f"{SRD}.entities"]["guard"]["imprisoned"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("choice", ["imprisonment_prison", "imprisonment_component"])
@pytest.mark.parametrize("level", [8, 9])
def test_imprisonment_named_dispel_targets_actual_prison_or_used_statuette(
    monkeypatch, choice, level
):
    state, data, params = _case(monkeypatch, "imprisonment")
    _cast(state, data, params)
    source_id, _ = _source(state)
    unrelated = _control_source(state, spell="charm_monster", condition="charmed")
    data, dispel = _add_spell(state, "dispel_magic")
    dispel.update(
        slot_resource=f"spell_slot_{level}",
        recovery_choice=choice,
        recovery_source_id=source_id,
        recovery_item=source_id if choice.endswith("prison") else params["material_item_ids"][0],
    )
    result = _cast(state, data, dispel)
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert sources[source_id]["installed"] is (level != 9)
    assert sources[unrelated]["installed"]
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{level}"] == (
        1 if level == 9 else 2
    )
    assert result["context"]["_spell_detail_rows"]


def test_imprisonment_existing_immunity_spends_cast_without_new_roll_or_deadline(monkeypatch):
    state, data, params = _case(monkeypatch, "imprisonment", modifier=100)
    _cast(state, data, params)
    original = state[f"{SRD}.entities"]["guard"]["imprisonment_immune_until"]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -100
    random_state = dice._rng.getstate()
    result = _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["imprisonment_immune_until"] == original and not target.get("imprisoned", False)
    assert "roll" in result["context"]["_spell_detail_rows"][0]["value"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 1
    assert dice._rng.getstate() == random_state


def test_imprisonment_wrong_target_statuette_rejects_without_committed_cost(monkeypatch):
    state, data, params = _case(monkeypatch, "imprisonment")
    state[f"{SRD}.entities"]["hero"]["imprisonment_statuettes"][params["material_item_ids"][0]] = (
        "other"
    )
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="must depict"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_actual_true_resurrection_ends_contagion_without_poison_cure_veto(monkeypatch):
    state, data, params = _case(monkeypatch, "contagion")
    _cast(state, data, params)
    source_id, _ = _source(state)
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=9999,
        adjustment=0,
        damage_type="force",
        critical_hit=False,
    )
    _run(
        "record_srd_deceased_body",
        state,
        target_state="guard",
        death_tick=0,
        old_age=False,
        soul_willing=True,
        body_available=True,
        vital_parts_present=True,
        living_creature_type="humanoid",
        name="Guard",
    )
    data, cure = _add_spell(state, "true_resurrection")
    _recovery_cast(state, data, cure)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["is_alive"] and target["hp"] == target["max_hp"]
    assert "poisoned" not in target["conditions"]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]


def test_public_contagion_reopens_and_actual_expiry_retry_preserves_committed_result(
    tmp_path, monkeypatch
):
    import tests.srd_full.test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    original_case = public_casting._spell_case

    def scene_case(spell):
        state, data, params = original_case(spell)
        _admit_scene(state)
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [3, 0, 0]
        return state, data, params

    monkeypatch.setattr(public_casting, "_spell_case", scene_case)
    database = tmp_path / "progressive.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = _create_spell_campaign(
            owner,
            "contagion",
            target_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "saving_throw_modifiers": dict.fromkeys(
                    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
                    -100,
                ),
            },
        )
        params.update(targets=["guard"], chosen_ability="wisdom")
        receipt, command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert "poisoned" in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
        source_id = saved.state[f"{SRD}.entities"]["guard"]["progressive_source_ids"][0]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        for rounds in (100000, 800):
            receipt, command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=rounds)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        settled = owner.snapshot("casting")
        source = settled.state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert not source["installed"]
        assert "poisoned" not in settled.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == settled.state
