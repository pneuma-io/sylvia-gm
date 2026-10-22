"""A shared control family proves source ownership, later consumers and public persistence."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _mechanic, _run
from tests.srd_full.test_srd_attack_budget import _npc_attack
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case

SPELLS = [
    "animal_friendship",
    "charm_person",
    "charm_monster",
    "hideous_laughter",
    "hypnotic_pattern",
]


def _case(monkeypatch, spell):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _spell_case(spell)
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target].update(hp=100, max_hp=100, temp_hp=10)
        state[f"{SRD}.entities"][target]["saving_throw_modifiers"]["wisdom"] = -100
    if spell == "animal_friendship":
        state[f"{SRD}.entities"]["guard"]["creature_type"] = "beast"
    params["targets"] = ["guard"]
    return state, data, params


def _prepare_creature(state, **parameters):
    """Use real preparation despite its `name` input conflicting with the shared runner."""
    outcome = _mechanic("prepare_srd_creature")(
        MechanicContext(
            campaign_id="budget",
            turn_id="turn",
            action_id="prepare",
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload({}),
        )
    )
    assert outcome.result["status"] == "committed"
    for mutation in outcome.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)


@pytest.mark.parametrize("spell", SPELLS)
def test_control_records_cast_their_fixed_conditions_and_expire(monkeypatch, spell):
    state, data, params = _case(monkeypatch, spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
    target = state[f"{SRD}.entities"]["guard"]
    conditions = {"prone", "incapacitated"} if spell == "hideous_laughter" else {"charmed"}
    if spell == "hypnotic_pattern":
        conditions.add("incapacitated")
        assert target["timed_control_speed_zero"] is True
    assert conditions <= set(target["conditions"])
    assert result["result_card"]["title"].lower().replace(" ", "_") == spell
    duration = {"animal_friendship": 14400, "charm_person": 600, "charm_monster": 600}.get(
        spell, 10
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=duration - 1)
    assert conditions <= set(state[f"{SRD}.entities"]["guard"]["conditions"])
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert conditions.isdisjoint(state[f"{SRD}.entities"]["guard"]["conditions"])
    if spell in ("charm_person", "charm_monster"):
        assert state[f"{SRD}.entities"]["guard"]["known_charm_caster_ids"] == ["hero"]


@pytest.mark.parametrize(
    "spell,invalid_type", [("animal_friendship", "humanoid"), ("charm_person", "beast")]
)
def test_invalid_creature_appears_to_save_without_a_roll_and_still_spends_slot(
    monkeypatch, spell, invalid_type
):
    state, data, params = _case(monkeypatch, spell)
    state[f"{SRD}.entities"]["guard"]["creature_type"] = invalid_type
    result = _run("cast_spell", state, participant_data=data, **params).result
    assert "charmed" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2
    assert "success" in str(result["result_card"])
    assert "No roll." in str(result["result_card"])


def test_control_upcast_and_combat_advantage_are_source_defined(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    params.update(
        targets=["guard", "other"], slot_resource="spell_slot_2", fighting_caster_targets=["guard"]
    )
    result = _run("cast_spell", state, participant_data=data, **params).result
    details = result["result_card"]["details"]
    assert "selected" in str(details)
    assert len(result["context"]["_spell_timed_condition_initial_save_rolls"][0]["results"]) == 2
    assert len(result["context"]["_spell_timed_condition_initial_save_rolls"][1]["results"]) == 1
    assert all("charmed" in state[f"{SRD}.entities"][t]["conditions"] for t in ["guard", "other"])


@pytest.mark.parametrize("source,ends", [("hero", True), ("other", True), ("", False)])
def test_charm_damage_uses_caster_and_saved_allies_even_when_temp_hp_absorbs_it(
    monkeypatch, source, ends
):
    state, data, params = _case(monkeypatch, "charm_person")
    _run("cast_spell", state, participant_data=data, **(params | {"caster_allies": ["other"]}))
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1,
        adjustment=0,
        damage_type="fire",
        source_state_id=source,
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 100
    assert ("charmed" not in target["conditions"]) is ends
    if ends:
        assert target["known_charm_caster_ids"] == ["hero"]


def test_laughter_damage_save_has_advantage_and_end_turn_save_does_not(monkeypatch):
    state, data, params = _case(monkeypatch, "hideous_laughter")
    _run("cast_spell", state, participant_data=data, **params)
    _run("apply_srd_damage", state, target="guard", amount=1, adjustment=0, damage_type="fire")
    target = state[f"{SRD}.entities"]["guard"]
    assert len(target["last_control_damage_save_roll"]["results"]) == 2
    assert {"prone", "incapacitated"} <= set(target["conditions"])
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"]["wisdom"] = 100
    result = _run("end_combat_turn", state).result
    assert len(result["context"]["_timed_condition_end_turn_rolls"][0]["results"]) == 1
    assert {"prone", "incapacitated"}.isdisjoint(state[f"{SRD}.entities"]["guard"]["conditions"])


def test_control_cannot_stand_and_cannot_move_but_shaking_spends_action(monkeypatch):
    state, data, params = _case(monkeypatch, "hideous_laughter")
    _run("cast_spell", state, participant_data=data, **params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("remove_srd_condition", state, target="guard", condition="prone")
    assert state == before
    state, data, params = _case(monkeypatch, "hypnotic_pattern")
    _run("cast_spell", state, participant_data=data, **params)
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "other"
    _run("begin_combat_turn", state)
    _run(
        "shake_srd_creature_from_stupor",
        state,
        actor_state="other",
        target_state="guard",
        can_touch_target=True,
    )
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert {"charmed", "incapacitated"}.isdisjoint(state[f"{SRD}.entities"]["guard"]["conditions"])


def test_charmed_attacks_reject_real_charmer_without_trusting_caller_flag(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    _run("cast_spell", state, participant_data=data, **params)
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Charmer"):
        _npc_attack(
            state,
            "Spear",
            actor="guard",
            target="hero",
            use_multiattack=False,
            target_is_charmer=False,
        )
    assert state == before


def test_charmer_receives_actual_charisma_advantage_toward_affected_target(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    _run("cast_spell", state, participant_data=data, **params)
    result = _run(
        "ability_check",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        ability="charisma",
        skill="persuasion",
        circumstantial_modifier=0,
        target_number=15,
        interaction_target="guard",
    ).result
    assert result["context"]["_effective_advantage"] is True
    assert len(result["context"]["_d20_test"]["results"]) == 2


def test_hypnotic_damage_releases_only_its_source_preserving_independent_charm(monkeypatch):
    state, data, params = _case(monkeypatch, "hypnotic_pattern")
    _run("cast_spell", state, participant_data=data, **params)
    _run("apply_srd_condition", state, target="guard", condition="charmed")
    _run("apply_srd_damage", state, target="guard", amount=1, adjustment=0, damage_type="fire")
    target = state[f"{SRD}.entities"]["guard"]
    assert "charmed" in target["conditions"]
    assert "incapacitated" not in target["conditions"]
    assert target["timed_control_speed_zero"] is False


def test_removing_hypnotic_charm_removes_its_compound_stupor(monkeypatch):
    state, data, params = _case(monkeypatch, "hypnotic_pattern")
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.combat"]["active_actor"] = "guard"
    _run("refresh_srd_movement_budget", state, actor_state="guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    state[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] = 30
    _run("remove_srd_condition", state, target="guard", condition="charmed")
    target = state[f"{SRD}.entities"]["guard"]
    assert {"charmed", "incapacitated"}.isdisjoint(target["conditions"])
    assert target["timed_control_speed_zero"] is False
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    target["spell_speed_reduction"] = 0
    _run("refresh_srd_movement_budget", state, actor_state="guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30


def test_hypnotic_admitted_target_bound_executes_and_rejects_excess_atomically(monkeypatch):
    state, data, params = _case(monkeypatch, "hypnotic_pattern")
    entities = state[f"{SRD}.entities"]
    targets = [f"pattern_{index}" for index in range(9)]
    for target in targets:
        entities[target] = deepcopy(entities["guard"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell", state, participant_data=data, **(params | {"targets": targets}))
    assert state == before
    _run("cast_spell", state, participant_data=data, **(params | {"targets": targets[:8]}))
    assert all(
        {"charmed", "incapacitated"}
        <= set(
            state[f"{SRD}.entities"][target]["conditions"],
        )
        for target in targets[:8]
    )
    assert state[f"{SRD}.entities"][targets[8]]["conditions"] == []


def test_hypnotic_selected_pattern_viewers_do_not_require_caster_sight(monkeypatch):
    state, data, params = _case(monkeypatch, "hypnotic_pattern")
    _run(
        "cast_spell",
        state,
        participant_data=data,
        **(
            params
            | {
                "caster_can_see_all_targets": False,
            }
        ),
    )
    assert {"charmed", "incapacitated"} <= set(
        state[f"{SRD}.entities"]["guard"]["conditions"],
    )


@pytest.mark.parametrize(
    "spell,choices",
    [
        ("magic_missile", {"target": "hero"}),
        (
            "magic_missile",
            {
                "target": "other",
                "projectile_targets": ["other", "hero", "other"],
            },
        ),
        ("fireball", {"targets": ["hero", "other"]}),
        ("scorching_ray", {"attack_targets": ["other", "hero", "other"]}),
    ],
)
def test_charmed_npc_rejects_harmful_scalar_area_and_repeated_ray_casts_atomically(
    monkeypatch, spell, choices
):
    state, data, params = _case(monkeypatch, "charm_monster")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _prepare_creature(
        state,
        creature_id="mage",
        name="Mage",
        source="Synthetic spellcaster",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        creature_type="humanoid",
        constitution_save=0,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        wisdom_save=-10,
        spell_component_exemptions=["verbal", "somatic", "material"],
        spells=[{"spell": spell, "uses_per_day": 1}],
    )
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            params
            | {
                "targets": ["mage"],
                "forego_save_targets": ["mage"],
            }
        ),
    )
    assert state[f"{SRD}.entities"]["mage"]["timed_charm_caster_ids"] == ["hero"]
    cast_params = params | choices | {"spell": spell, "caster_state": "mage"}
    cast_params.pop("caster")
    cast_params.pop("slot_resource")
    if spell == "scorching_ray":
        for key in ("target", "target_distance", "caster_can_see_target", "targets"):
            cast_params.pop(key, None)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Charmer"):
        _run("cast_spell_outside_combat", state, **cast_params)
    assert state == before


def test_public_control_reopens_retries_and_later_expires_without_duplicate_cost(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "control.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = _create_spell_campaign(owner, "charm_person")
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            **(
                params
                | {
                    "targets": ["other"],
                    "forego_save_targets": ["other"],
                    "caster_allies": ["guard"],
                }
            ),
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved
        assert saved[f"{SRD}.entities"]["other"]["timed_friendly_caster_ids"] == ["hero"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        receipt, command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=600)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        after = owner.snapshot("casting").state
        assert "charmed" not in after[f"{SRD}.entities"]["other"]["conditions"]
        assert after[f"{SRD}.entities"]["other"]["known_charm_caster_ids"] == ["hero"]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == after
