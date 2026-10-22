"""Rituals use real casting time, concentration, components and saved public actions."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_public_spell_casting import SRD, _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import _spell_case


def _ritual(*, combat=False):
    state, data, parameters = _spell_case("water_walk")
    parameters.update(casting_mode="ritual", slot_resource="ritual", targets=["guard"])
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    if not combat:
        state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    return state, data, parameters


def _begin(parameters):
    names = {
        "spell",
        "caster",
        "caster_state",
        "casting_mode",
        "slot_resource",
        "verbal_component_available",
        "somatic_component_available",
        "material_component_available",
        "material_item_ids",
        "spell_grant",
        "spellbook_readable",
    }
    return {name: value for name, value in parameters.items() if name in names}


def _external_slow(state, data):
    """Apply another creature's actual Slow without ending it when Hero begins casting."""
    from tests.srd_full.test_srd_reaction_spells import _mage

    combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _mage(state, "slow")
    slow_state, _, slow_parameters = _spell_case("slow")
    entities = state[f"{SRD}.entities"]
    entities["mage"]["spell_materials"] = deepcopy(
        slow_state[f"{SRD}.entities"]["hero"]["spell_materials"])
    entities["mage"]["inventory"] = [
        m["inventory_item"] for m in entities["mage"]["spell_materials"]]
    entities["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    slow_parameters.pop("caster")
    slow_parameters.pop("slot_resource")
    slow_parameters.update(caster_state="mage", targets=["hero"], forego_save_targets=["hero"])
    _run("cast_spell_outside_combat", state, **slow_parameters)
    state[f"{SRD}.combat"] = combat
    assert state[f"{SRD}.entities"]["hero"]["spell_slow"] is True


@pytest.mark.parametrize("ritual,combat", [(False, True), (True, False)])
def test_slow_failed_actual_longer_attempt_spends_costs_once_and_creates_no_channel(
    monkeypatch, ritual, combat,
):
    state, data, parameters = _ritual(combat=combat)
    if not ritual:
        state, data, parameters = _spell_case("foresight")
        state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    resource = parameters["slot_resource"] if not ritual else "spell_slot_3"
    _external_slow(state, data)
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters)).result
    hero = state[f"{SRD}.entities"]["hero"]
    assert result["context"]["_slow_cast_failed"] is True
    assert result["context"]["_slow_cast_failure_roll"]["results"] == [1]
    assert hero.get("extended_casting") is None
    assert hero.get("concentration_spell_id") is None
    assert hero["resources"][resource] == (3 if ritual else 2)
    assert state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True
    assert "Slow: Somatic gestures failed" in str(result["result_card"])
    if combat:
        assert state[f"{SRD}.combat"]["action_available"] is False
        assert state[f"{SRD}.combat"]["normal_action_spent"] is True


def test_started_slow_gesture_survives_expiry_and_cannot_reroll_at_ritual_completion(monkeypatch):
    state, data, parameters = _ritual()
    _external_slow(state, data)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    hero = state[f"{SRD}.entities"]["hero"]
    saved_roll = deepcopy(hero["extended_casting"]["slow_cast"]["roll"])
    assert saved_roll["results"] == [4]
    _run("continue_srd_spell_cast", state, caster_state="hero", elapsed_rounds=100,
         uninterrupted=True, verbal_component_available=True,
         somatic_component_available=True, material_component_available=True)
    assert state[f"{SRD}.entities"]["hero"]["spell_slow"] is False
    monkeypatch.setattr(dice, "_rng", Random(2))
    result = _run("cast_spell", state, participant_data=data, **parameters).result
    assert result["context"]["_slow_cast_failure_roll"] == saved_roll
    assert state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3


def _long_reaction_case():
    """Place the actual prepared reacting creature in the admitted casting scene."""
    from tests.srd_full.test_srd_reaction_spells import _mage
    from tests.srd_full.test_srd_scene_relations import _state

    state, data, parameters = _ritual(combat=True)
    _mage(state, "counterspell")
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    scene["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["entities"][1]["metadata"]["participant_ref"] = "mage"
    state[f"{SRD}.state"]["spatial_scene"] = scene
    return state, data, parameters


@pytest.mark.parametrize("seed,countered", [(8, True), (5, False)])
def test_counterspell_longer_attempt_interrupts_only_failed_save_without_early_spell(
    monkeypatch, seed, countered,
):
    from tests.srd_full.test_srd_reaction_spells import _cast_parameters

    state, data, parameters = _long_reaction_case()
    begun = _run("begin_srd_extended_spell_action", state, participant_data=data,
                 **_begin(parameters)).result
    event_id = begun["context"]["_pending_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["kind"] == "spell_cast" and event["owner_kind"] == "extended_casting"
    assert event["components"] == ["verbal", "somatic", "material"]
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("cast_srd_reaction_spell", state, **_cast_parameters("counterspell", event_id))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    hero = state[f"{SRD}.entities"]["hero"]
    assert (hero["extended_casting"] is None) is countered
    if not countered:
        assert hero["extended_casting"]["progress_rounds"] == 1
        assert hero["extended_casting"]["window_action_id"] is None
    assert hero["resources"]["spell_slot_3"] == 3
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["counterspell"] == 1
    assert state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []


def test_counterspell_later_casting_round_consumes_its_actual_magic_action(monkeypatch):
    from tests.srd_full.test_srd_reaction_spells import _cast_parameters

    state, data, parameters = _long_reaction_case()
    begun = _run("begin_srd_extended_spell_action", state, participant_data=data,
                 **_begin(parameters)).result
    _run("complete_srd_pending_action", state,
         pending_action_id=begun["context"]["_pending_action_id"])
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    progress = _run("continue_srd_extended_spell_action", state, caster_state="hero",
                    verbal_component_available=True, somatic_component_available=True,
                    material_component_available=True).result
    event_id = progress["context"]["_pending_action_id"]
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("cast_srd_reaction_spell", state, **_cast_parameters("counterspell", event_id))
    _run("complete_srd_pending_action", state, pending_action_id=event_id)
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
    assert state[f"{SRD}.state"]["extended_casting_sources"] == []


def test_public_longer_cast_window_reopens_and_retries_before_actual_counterspell(
    tmp_path, monkeypatch,
):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act
    from tests.srd_full.test_srd_reaction_spells import _cast_parameters

    state, data, parameters = _long_reaction_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, parameters))
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "long-cast-window.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_spell_campaign(owner, "water_walk")
        begun, command = _act(owner, "begin_srd_extended_spell_action", **_begin(parameters))
        assert begun.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert owner.execute_action(command) == begun
        assert owner.snapshot("casting").state == saved.state
        event_id = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        countered, counter_command = _act(
            owner, "cast_srd_reaction_spell", **_cast_parameters("counterspell", event_id))
        assert countered.status is ActionStatus.COMMITTED
        assert owner.execute_action(counter_command) == countered
        done, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=event_id)
        assert done.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
        assert owner.execute_action(finish_command) == done
        assert owner.snapshot("casting").state == final.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == final.state


def test_ritual_requires_100_rounds_then_casts_at_base_level_without_slot_or_second_action():
    state, data, parameters = _ritual()
    before = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["extended_casting"]["required_rounds"] == 100
    assert hero["extended_casting"]["progress_rounds"] == 0
    assert hero["concentration_spell_id"] == "water_walk"
    rejected = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Start and continue"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == rejected
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=99,
        uninterrupted=True,
    )
    with pytest.raises(RulesetPreconditionRejected, match="Start and continue"):
        _run("cast_spell", state, participant_data=data, **parameters)
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        uninterrupted=True,
    )
    assert state[f"{SRD}.state"]["lasting_tick"] == 200
    result = _run("cast_spell", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"] == before
    assert hero["extended_casting"] is None
    assert hero["concentration_spell_id"] is None
    assert state[f"{SRD}.state"]["extended_casting_sources"] == []
    assert state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is True
    assert "no spell slot spent" in str(result.result["result_card"])


@pytest.mark.parametrize(
    "changed",
    [
        {"casting_mode": "ordinary"},
        {"slot_resource": "spell_slot_3"},
        {"spell": "fly", "slot_resource": "ritual", "casting_mode": "ritual"},
    ],
)
def test_ritual_cannot_bypass_the_source_tag_or_resource_contract(changed):
    state, data, parameters = _ritual()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters | changed))
    assert state == before


def test_combat_casting_requires_each_turn_magic_action_and_cannot_accelerate():
    state, data, parameters = _ritual(combat=True)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="same turn"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    assert state == before
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"] == 2
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    _run("end_combat_turn", state, actor="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["concentration_spell_id"] is None
    assert hero["extended_casting"] is None
    assert state[f"{SRD}.state"]["extended_casting_sources"] == []
    assert hero["resources"]["spell_slot_3"] == 3


def test_casting_continuation_checks_current_components_without_exposing_package_clock():
    state, data, parameters = _ritual()
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Verbal component"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            uninterrupted=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    assert state == before
    # Public tool schemas cannot select an alternate package clock.
    record = json.loads(
        (Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/package-record.json").read_text()
    )
    declarations = {t["name"]: t["input_schema"] for t in record["tools"]}
    for name in ("begin_srd_spell_cast", "continue_srd_spell_cast"):
        assert "lasting_state" not in declarations[name]["properties"]


def test_starting_material_validation_retains_priced_consumed_components():
    state, data, parameters = _spell_case("stoneskin")
    hero = state[f"{SRD}.entities"]["hero"]
    before = deepcopy(hero)
    _run(
        "resolve_srd_spell_materials",
        state,
        caster_state="hero",
        spell="stoneskin",
        item_ids=parameters["material_item_ids"],
        materials_available=True,
        components_waived=False,
        target_count=1,
        permitted_focus_kinds=[],
        validate_only=True,
    )
    assert state[f"{SRD}.entities"]["hero"] == before
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2


@pytest.mark.parametrize("book_present,ritual", [(True, True), (False, True), (True, False)])
def test_ritual_adept_grants_an_unprepared_book_spell_only_as_a_wizard_ritual(book_present, ritual):
    state, data, _ = _spell_case("mage_armor")
    if book_present:
        data["wizard_spellbook"].append("comprehend_languages")
    before = deepcopy(state)
    if book_present and ritual:
        result = _run(
            "validate_srd_spell_grants",
            state,
            participant_data=data,
            caster="hero",
            spell="comprehend_languages",
            ritual=ritual,
        )
        assert result.result["context"]["_grant_ability"] == "intelligence"
        assert result.result["context"]["_ritual_book_required"] is True
    else:
        with pytest.raises(RulesetPreconditionRejected, match="saved spellcasting grant"):
            _run(
                "validate_srd_spell_grants",
                state,
                participant_data=data,
                caster="hero",
                spell="comprehend_languages",
                ritual=ritual,
            )
    assert state == before


@pytest.mark.parametrize("spell", ["comprehend_languages", "water_breathing"])
def test_ritual_senses_reach_the_consumer_and_start_duration_after_casting(spell):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    parameters.update(casting_mode="ritual", slot_resource="ritual", spellbook_readable=True)
    if spell == "comprehend_languages":
        data["spellcasting_grants"][0]["spells"] = ["mage_armor"]
        data["wizard_spellbook"].append("mage_armor")
        state[f"{SRD}.entities"]["hero"]["prepared_spells"] = ["mage_armor"]
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="reading"):
            _run(
                "begin_srd_spell_cast",
                state,
                participant_data=data,
                **(_begin(parameters) | {"spellbook_readable": False}),
            )
        assert state == before
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        spellbook_readable=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    effect = state[f"{SRD}.state"]["turn_effects"][-1]
    assert effect["expires_tick"] == 200 + (1200 if spell == "comprehend_languages" else 28800)
    assert state[f"{SRD}.entities"]["hero"]["resources"] == {
        f"spell_slot_{level}": 3 for level in range(1, 10)
    }
    target = "hero" if spell == "comprehend_languages" else "guard"
    senses = _run("resolve_srd_timed_senses", state, target_state=target)
    field = "_sense_comprehension" if spell == "comprehend_languages" else "_sense_water_breathing"
    assert senses.result["context"][field] is True


@pytest.mark.parametrize("interruption", ["damage", "condition", "dismiss", "elapsed"])
def test_interruption_ends_the_cast_without_spending_its_spell_slot(interruption):
    state, data, parameters = _ritual()
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    if interruption == "damage":
        state[f"{SRD}.entities"]["hero"]["concentration_save_modifier"] = -100
        _run("apply_srd_damage", state, target="hero", amount=1, adjustment=0, damage_type="force")
    elif interruption == "condition":
        _run("apply_srd_condition", state, target="hero", condition="stunned")
    elif interruption == "dismiss":
        _run("end_concentration", state, caster_state="hero")
    else:
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["concentration_spell_id"] is None
    assert hero["extended_casting"] is None
    assert hero["resources"]["spell_slot_3"] == 3
    with pytest.raises(RulesetPreconditionRejected, match="no uninterrupted"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            uninterrupted=True,
        )


def test_public_ritual_survives_reopen_and_retries_without_repeating_time_or_effect(tmp_path):
    database = tmp_path / "ritual.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "water_walk")
        parameters.update(casting_mode="ritual", slot_resource="ritual", targets=["guard"])
        begin = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(begin)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(begin) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        mechanics = StatelessMcpMechanics(reopened)
        assert reopened.snapshot("casting").state == saved.state
        continuation = replace(
            begin,
            action_id="continue",
            idempotency_key="continue",
            expected_revision=saved.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 100,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        progress = mechanics.execute_action(continuation)
        assert mechanics.execute_action(continuation) == progress
        ready = reopened.snapshot("casting")
        assert ready.state[f"{SRD}.state"]["lasting_tick"] == 200
        finish = replace(
            begin,
            action_id="finish",
            idempotency_key="finish",
            expected_revision=ready.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        result = mechanics.execute_action(finish)
        assert result.status is ActionStatus.COMMITTED
        finished = reopened.snapshot("casting")
        assert mechanics.execute_action(finish) == result
        assert reopened.snapshot("casting").state == finished.state
        assert finished.state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is True
        assert finished.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
