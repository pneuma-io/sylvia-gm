"""Source-owned afflictions exercise actual later turn, action, D20 and damage owners."""

from copy import deepcopy
from random import Random

import pytest
from scripts.srd_full.srd_control_afflictions import PROFILES

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene, _turn


def _case(monkeypatch, spell, **choices):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for actor in ("hero", "guard", "other"):
        entity = state[f"{SRD}.entities"][actor]
        entity.update(hp=1000, max_hp=1000, temp_hp=5, size="medium", languages=["common"])
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["wisdom"] = -100
    parameters.update(targets=["guard"], **choices)
    if spell == "antipathy_sympathy":
        parameters.setdefault("affinity_kind", "humanoid")
        _admit_scene(state)
        state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["position"] = [200, 0, 0]
    return state, data, parameters


def _cast(monkeypatch, spell, **choices):
    state, data, parameters = _case(monkeypatch, spell, **choices)
    if spell == "antipathy_sympathy":
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters, result.result


def _source(state, actor="guard"):
    target = state[f"{SRD}.entities"][actor]
    source_id = target["control_affliction_source_ids"][0]
    return source_id, state[f"{SRD}.state"]["timed_condition_sources"][source_id]


def _reject(state, tool, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(tool, state, **parameters)
    assert state == before


@pytest.mark.parametrize("spell", PROFILES)
def test_every_profile_casts_with_real_cost_and_source_or_anchor(monkeypatch, spell):
    state, _, parameters, result = _cast(monkeypatch, spell)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"][parameters["slot_resource"]] == 2
    assert result["result_card"]["title"]
    if spell == "antipathy_sympathy":
        anchor = state[f"{SRD}.entities"]["guard"]
        record = state[f"{SRD}.state"]["affinity_casts"][anchor["last_affinity_cast_id"]]
        assert record["anchor_id"] == "guard" and record["expires_tick"] > 0
    else:
        assert _source(state)[1]["installed"] is True


@pytest.mark.parametrize(
    "mode,condition",
    [("asleep", "unconscious"), ("panicked", "frightened"), ("sickened", "poisoned")],
)
def test_eyebite_modes_project_real_conditions_and_damage_wakes_only_sleep(
    monkeypatch, mode, condition
):
    state, _, _, _ = _cast(monkeypatch, "eyebite", eyebite_mode=mode)
    target = state[f"{SRD}.entities"]["guard"]
    assert condition in target["conditions"]
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1,
        adjustment=0,
        damage_type="force",
        source_state_id="other",
    )
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 4
    assert (condition in state[f"{SRD}.entities"]["guard"]["conditions"]) == (mode != "asleep")


def test_eyebite_retarget_spends_magic_and_success_immunity_survives_later_turn(monkeypatch):
    state, data, _, _ = _cast(monkeypatch, "eyebite", eyebite_mode="sickened")
    _admit_scene(state)
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 100
    _turn(state, "hero", data)
    _run(
        "retarget_srd_eyebite",
        state,
        caster_state="hero",
        target_state="other",
        eyebite_mode="panicked",
    )
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert state[f"{SRD}.entities"]["hero"]["eyebite_saved_success_ids"] == ["other"]
    _turn(state, "hero", data)
    _reject(
        state,
        "retarget_srd_eyebite",
        caster_state="hero",
        target_state="other",
        eyebite_mode="asleep",
    )


def test_eyebite_retarget_inherits_original_deadline_and_concentration_cleanup(monkeypatch):
    state, data, _, _ = _cast(monkeypatch, "eyebite", eyebite_mode="sickened")
    _admit_scene(state)
    expiry = _source(state)[1]["expires_tick"]
    _turn(state, "hero", data)
    _run(
        "retarget_srd_eyebite",
        state,
        caster_state="hero",
        target_state="other",
        eyebite_mode="sickened",
    )
    assert _source(state, "other")[1]["expires_tick"] == expiry
    _run("end_concentration", state, caster_state="hero")
    assert all(
        "poisoned" not in state[f"{SRD}.entities"][n]["conditions"] for n in ("guard", "other")
    )


def test_eyebite_asleep_honors_saved_no_sleep_trait_after_paid_cast(monkeypatch):
    state, data, parameters = _case(monkeypatch, "eyebite", eyebite_mode="asleep")
    state[f"{SRD}.entities"]["guard"]["does_not_sleep"] = True
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert "unconscious" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


def test_later_independent_poison_survives_eyebite_concentration_end(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "eyebite", eyebite_mode="sickened")
    _run("apply_srd_condition", state, target="guard", condition="poisoned")
    _run("end_concentration", state, caster_state="hero")
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("remove_srd_condition", state, target="guard", condition="poisoned")
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_panicked_eyebite_spends_dash_and_scene_movement_then_releases_at_hidden_sixty_feet(
    monkeypatch,
):
    state, _, _, _ = _cast(monkeypatch, "eyebite", eyebite_mode="panicked")
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [
        {
            "id": "wall",
            "map_id": "orders",
            "active": True,
            "volume": {"kind": "box", "min": [10, -10, -10], "max": [11, 10, 10]},
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    ]
    source_id, _ = _source(state)
    _turn(state)
    _reject(state, "end_combat_turn")
    _run(
        "resolve_srd_affliction_movement",
        state,
        actor_state="guard",
        source_state="hero",
        source_id=source_id,
        destination=[80, 0, 0],
        safe_route=True,
        shortest_route=True,
    )
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    guard = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard"
    )
    assert guard["position"] == [80, 0, 0]
    assert "frightened" not in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("spell", ["phantasmal_killer", "weird"])
def test_nightmares_repeat_real_psychic_damage_then_success_releases_source(monkeypatch, spell):
    state, _, _, _ = _cast(monkeypatch, spell)
    target = state[f"{SRD}.entities"]["guard"]
    initial_hp = target["hp"]
    _turn(state)
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < initial_hp
    source_id, source = _source(state)
    assert source["damage_dice"] == (4 if spell == "phantasmal_killer" else 5)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _turn(state)
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False


def test_phantasmal_killer_d20_disadvantage_and_upcast_damage(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "phantasmal_killer", slot_resource="spell_slot_6")
    assert _source(state)[1]["damage_dice"] == 6
    out = _run(
        "d20_test",
        state,
        actor_state="guard",
        kind="ability_check",
        ability="strength",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
        advantage=False,
        disadvantage=False,
    )
    assert len(out.result["context"]["_d20_test"]["results"]) == 2


def test_phantasmal_success_halves_damage_and_ends_concentration(monkeypatch):
    state, data, parameters = _case(monkeypatch, "phantasmal_killer")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000
    assert state[f"{SRD}.entities"]["guard"]["control_affliction_source_ids"] == []


@pytest.mark.parametrize("mode", ["ability", "attacks", "dodge", "damage"])
def test_curse_choices_have_distinct_source_fields(monkeypatch, mode):
    state, _, _, _ = _cast(monkeypatch, "bestow_curse", curse_mode=mode, curse_ability="dexterity")
    assert _source(state)[1]["curse_mode"] == mode
    assert _source(state)[1]["curse_ability"] == "dexterity"


def test_curse_ability_changes_real_saves_and_checks_only_selected_ability(monkeypatch):
    state, _, _, _ = _cast(
        monkeypatch, "bestow_curse", curse_mode="ability", curse_ability="dexterity"
    )
    for ability, count in [("dexterity", 2), ("wisdom", 1)]:
        out = _run(
            "d20_test",
            state,
            actor_state="guard",
            kind="saving_throw",
            ability=ability,
            ability_modifier=0,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=10,
            advantage=False,
            disadvantage=False,
        )
        assert len(out.result["context"]["_d20_test"]["results"]) == count


def test_curse_forced_dodge_spends_actual_action_at_later_turn(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "bestow_curse", curse_mode="dodge")
    _turn(state)
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert state[f"{SRD}.entities"]["guard"]["dodging"] is True
    assert state[f"{SRD}.entities"]["guard"]["last_curse_turn_save"] is not None


@pytest.mark.parametrize(
    "level,rounds,concentration",
    [
        (4, 100, True),
        (5, 4800, False),
        (6, 4800, False),
        (7, 14400, False),
        (8, 14400, False),
        (9, None, False),
    ],
)
def test_curse_upcast_exact_duration_and_concentration(monkeypatch, level, rounds, concentration):
    state, _, _, _ = _cast(monkeypatch, "bestow_curse", slot_resource=f"spell_slot_{level}")
    source = _source(state)[1]
    assert source["expires_tick"] == (source["started_tick"] + 2 * rounds if rounds else None)
    assert (
        state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is not None
    ) == concentration


def _affinity(monkeypatch, mode="antipathy"):
    state, data, _, _ = _cast(monkeypatch, "antipathy_sympathy", affinity_mode=mode)
    _admit_scene(state)
    anchor = state[f"{SRD}.entities"]["guard"]
    cast_id = anchor["last_affinity_cast_id"]
    _run("expose_srd_affinity", state, target_state="other", anchor_state="guard", cast_id=cast_id)
    return state, data, cast_id


@pytest.mark.parametrize("mode,condition", [("antipathy", "frightened"), ("sympathy", "charmed")])
def test_affinity_exposure_uses_actual_anchor_and_condition_source(monkeypatch, mode, condition):
    state, _, cast_id = _affinity(monkeypatch, mode)
    source = _source(state, "other")[1]
    assert source["caster_state_id"] == "guard"
    assert source["affliction_cast_id"] == cast_id
    assert condition in state[f"{SRD}.entities"]["other"]["conditions"]


def test_affinity_success_immunity_expires_after_exact_minute(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "antipathy_sympathy")
    _admit_scene(state)
    cast_id = state[f"{SRD}.entities"]["guard"]["last_affinity_cast_id"]
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 100
    params = {"target_state": "other", "anchor_state": "guard", "cast_id": cast_id}
    _run("expose_srd_affinity", state, **params)
    until = state[f"{SRD}.state"]["affinity_casts"][cast_id]["saved_until"]["other"]
    state[f"{SRD}.state"]["lasting_tick"] = until - 0.01
    _reject(state, "expose_srd_affinity", **params)
    state[f"{SRD}.state"]["lasting_tick"] = until
    _run("expose_srd_affinity", state, **params)


@pytest.mark.parametrize("mode,endpoint", [("antipathy", [80, 0, 0]), ("sympathy", [25, 0, 0])])
def test_affinity_real_movement_consumes_speed_and_sympathy_cannot_leave_anchor(
    monkeypatch,
    mode,
    endpoint,
):
    state, _, cast_id = _affinity(monkeypatch, mode)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["position"] = [50, 0, 0]
    source_id, _ = _source(state, "other")
    _turn(state, "other")
    _reject(state, "end_combat_turn")
    _run(
        "resolve_srd_affliction_movement",
        state,
        actor_state="other",
        source_state="guard",
        source_id=source_id,
        destination=endpoint,
        safe_route=True,
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert scene["entities"][2]["position"] == endpoint
    assert state[f"{SRD}.combat"]["movement_remaining"] == (5 if mode == "sympathy" else 0)
    if mode == "sympathy":
        _reject(state, "move_srd_scene_creature", actor_state="other", destination=[30, 0, 0])
        _run("end_combat_turn", state)
        _turn(state, "other")
        assert state[f"{SRD}.entities"]["other"]["affliction_movement_pending"] is False
    else:
        assert state[f"{SRD}.state"]["affinity_casts"][cast_id]["saved_until"] == {}


def test_affinity_invalid_gargantuan_anchor_commits_casting_cost(monkeypatch):
    state, data, parameters = _case(monkeypatch, "antipathy_sympathy")
    state[f"{SRD}.entities"]["guard"]["size"] = "gargantuan"
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=600,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    assert "last_affinity_cast_id" not in state[f"{SRD}.entities"]["guard"]


@pytest.mark.parametrize("spell", ["fire_bolt", "magic_missile"])
def test_curse_damage_rider_uses_actual_spell_damage_not_environment(monkeypatch, spell):
    state, data, _, _ = _cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1,
        adjustment=0,
        damage_type="fire",
        source_state_id="hero",
    )
    assert "last_curse_damage_roll" not in state[f"{SRD}.entities"]["guard"]
    _, _, parameters = _spell_case(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append(spell)
    data["wizard_spellbook"].append(spell)
    data["spellcasting_grants"][0]["spells"].append(spell)
    state[f"{SRD}.entities"]["guard"]["armor_class"] = 1
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert len(state[f"{SRD}.entities"]["guard"]["last_curse_damage_roll"]["results"]) == 1


@pytest.mark.parametrize("spell,mode", [("phantasmal_killer", None), ("bestow_curse", "dodge")])
def test_public_affliction_reopens_retries_and_changes_actual_later_turn(
    tmp_path, monkeypatch, spell, mode
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / f"{spell}.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            spell,
            target_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "saving_throw_modifiers": dict.fromkeys(
                    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
                    -100,
                ),
            },
        )
        choices = {"targets": ["guard"]}
        if mode:
            choices["curse_mode"] = mode
        receipt, command = _act(owner, "cast_spell_outside_combat", **(parameters | choices))
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        receipt, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="guard",
            second_actor="other",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(owner, "begin_combat_turn")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        if mode:
            assert owner.snapshot("casting").state[f"{SRD}.combat"]["normal_action_spent"] is True
        else:
            hp = owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"]
            receipt, command = _act(owner, "end_combat_turn")
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            assert owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"] < hp
            after = owner.snapshot("casting").state
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting").state == after
