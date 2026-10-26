"""Installed-source consequence proofs; the public after-hit producer remains separate."""

import json
from copy import deepcopy
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_spell_damage_riders import bind_runtime_consumers

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_damage_riders as owners
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run


@pytest.fixture(autouse=True)
def _runtime(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", owners._mechanic)
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: owners._runtime()[0]["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(0))


def _install(state, spell, source_id, *, caster="hero", target="guard", slot=1):
    """Use the real installer on a committed-cast identity fixture, without claiming payment."""
    actor = state[f"{SRD}.entities"][caster]
    actor["last_spell_source_cast_id"] = source_id
    actor["last_spell_source_spell"] = spell
    _run(
        "install_srd_attack_rider_source",
        state,
        caster_state=caster,
        target_state=target,
        spell=spell,
        spell_cast_id=source_id,
        slot_level=slot,
        save_dc=15,
        concentration_save_modifier=2,
        rider_ability="strength",
    )
    return state[f"{SRD}.state"]["attack_rider_sources"][source_id]


def _start(state, actor="guard"):
    _run("end_combat_turn", state, actor=state[f"{SRD}.combat"]["active_actor"])
    # The Initiative fixture selects the authoritative current actor; begin owns its counter.
    state[f"{SRD}.combat"]["current_actor"] = actor
    return _run("begin_combat_turn", state, actor=actor).result["context"]


@pytest.mark.parametrize("immune", [False, True])
def test_searing_real_start_turn_deals_original_slot_fire_before_save_even_when_immune(
    monkeypatch, immune
):
    state, _, _ = owners._case("searing_smite", slot=3)
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"]["constitution"] = 100
    if immune:
        target["damage_immunities"] = ["fire"]
    source = _install(state, "searing_smite", "searing_fixture", slot=3)
    assert source["concentration"] is False
    before = target["hp"] + target["temp_hp"]
    at_save = []
    actual = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, context, executor):
        if body == "d20_test" and "_searing_damage_roll" in context.vars:
            target_state = context.vars["target_state"]
            at_save.append(target_state["hp"] + target_state["temp_hp"])
        return actual(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    context = _start(state)
    assert any(row.get("kind") == "Searing Smite" for row in context["_turn_effect_rows"]), {
        "rows": context["_turn_effect_rows"],
        "source": state[f"{SRD}.state"]["attack_rider_sources"]["searing_fixture"],
        "retained": state[f"{SRD}.state"]["turn_effects"],
        "actor_turn": state[f"{SRD}.entities"]["guard"].get("turns_started"),
    }
    row = next(row for row in context["_turn_effect_rows"] if row.get("kind") == "Searing Smite")
    assert row["damage_roll"]["count"] == 3  # Later burning does not repeat initial critical dice.
    assert row["detail"].endswith("success")
    assert at_save == [before - row["value"]]
    assert row["value"] == (0 if immune else row["damage_roll"]["total"])
    assert context["_turn_effect_damage_applied"] == row["value"]
    assert state[f"{SRD}.state"]["attack_rider_sources"]["searing_fixture"]["installed"] is False


def test_searing_saved_source_reopens_and_cannot_replay_the_same_actual_turn():
    state, _, _ = owners._case("searing_smite", slot=2)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = -100
    _install(state, "searing_smite", "searing_fixture", slot=2)
    context = _start(state)
    assert context["_turn_effect_damage_applied"] > 0
    state = json.loads(json.dumps(state))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual new start turn"):
        _run(
            "resolve_srd_searing_smite_turn",
            state,
            target_state="guard",
            source_id="searing_fixture",
        )
    assert state == before
    second = _start(state)
    row = next(row for row in second["_turn_effect_rows"] if row.get("kind") == "Searing Smite")
    assert row["damage_roll"]["count"] == 2 and row["value"] > 0


def test_suppressed_searing_consumes_start_receipt_and_clock_or_removed_effect_ends_source():
    state, _, _ = owners._case("searing_smite")
    _install(state, "searing_smite", "searing_fixture")
    lasting = state[f"{SRD}.state"]
    meta = next(
        effect for effect in lasting["turn_effects"] if effect.get("source_id") == "searing_fixture"
    )
    meta["suppressed"] = True
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    context = _start(state)
    assert context["_turn_effect_damage_applied"] == 0
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before
    meta = next(
        effect
        for effect in state[f"{SRD}.state"]["turn_effects"]
        if effect.get("source_id") == "searing_fixture"
    )
    meta["suppressed"] = False
    with pytest.raises(RulesetPreconditionRejected, match="actual new start turn"):
        _run(
            "resolve_srd_searing_smite_turn",
            state,
            target_state="guard",
            source_id="searing_fixture",
        )
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert state[f"{SRD}.state"]["attack_rider_sources"]["searing_fixture"]["installed"] is False
    _install(state, "searing_smite", "removed_fixture")
    state[f"{SRD}.state"]["turn_effects"] = [
        effect
        for effect in state[f"{SRD}.state"]["turn_effects"]
        if effect.get("source_id") != "removed_fixture"
    ]
    _run("refresh_srd_attack_riders", state)
    assert state[f"{SRD}.state"]["attack_rider_sources"]["removed_fixture"]["installed"] is False


def test_actual_recovery_selector_ends_one_suppressed_hex_then_all_without_other_conditions():
    state, _, _ = owners._case("hex")
    _install(state, "hex", "hex_one")
    _install(state, "hex", "hex_two", caster="other")
    target = state[f"{SRD}.entities"]["guard"]
    target["conditions"] = ["poisoned"]
    for effect in state[f"{SRD}.state"]["turn_effects"]:
        if effect.get("source_id") == "hex_one":
            effect["suppressed"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Choose the one installed curse"):
        _run("remove_srd_one_recovery_curse", state, target_state="guard")
    assert state == before
    _run("remove_srd_one_recovery_curse", state, target_state="guard", source_id="hex_one")
    sources = state[f"{SRD}.state"]["attack_rider_sources"]
    assert sources["hex_one"]["installed"] is False and sources["hex_two"]["installed"] is True
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == ["poisoned"]
    _run("remove_srd_recovery_curses", state, target_state="guard")
    assert state[f"{SRD}.state"]["attack_rider_sources"]["hex_two"]["installed"] is False
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == ["poisoned"]


def test_consequence_binder_is_idempotent_and_retains_unowned_start_and_recovery_effects():
    runtime, encoded = owners._runtime()
    assert yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader) == runtime
    original = owners._source_runtime()[0]["tools"]
    begin = runtime["tools"]["begin_combat_turn"]
    retained = [
        effect for effect in begin["effects"] if "start_srd_damage_rider_turn" not in str(effect)
    ]
    assert retained == original["begin_combat_turn"]["effects"]
    all_curses = runtime["tools"]["remove_srd_recovery_curses"]
    assert all_curses["effects"][:-1] == original["remove_srd_recovery_curses"]["effects"]
    assert all_curses["parameters"] == original["remove_srd_recovery_curses"]["parameters"]
