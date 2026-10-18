"""Shapechange's retained gear uses the actual form and the native paid attack owner."""

from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.srd_shapechange_weapons import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_information_self_casting as casting
from tests import test_srd_spell_wave6_transformations as forms
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_budget import _attack


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    _, base = casting._runtime()
    text = bind_runtime_consumers(base.decode())
    runtime = _load_runtime(text)
    monkeypatch.setattr(casting, "_runtime", lambda: (runtime, text.encode()))
    casting._mechanic.cache_clear()
    casting._authored.__wrapped__(monkeypatch)
    yield
    casting._mechanic.cache_clear()


def test_paid_shapechange_ape_attacks_with_held_dagger_form_strength_and_retained_proficiency():
    state, data, params = forms._setup(
        "shapechange", transformation_form="ape", transformation_seen_before=True
    )
    data.update(
        level=18,
        classes=[{"id": "wizard", "level": 17}, {"id": "fighter", "level": 1}],
        fighting_styles=[{"source": "fighter", "style": "archery"}],
    )
    data["abilities"]["strength"] = 8
    hero = forms._entity(state, "hero")
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["dagger"])
    forms._cast(state, data, params)
    hero = forms._entity(state, "hero")
    source_id = hero["form_current_source_id"]
    assert hero["resources"]["spell_slot_9"] == 2
    assert hero["form_ability_scores"]["strength"] == 16
    assert hero["held_items"] == ["dagger"] and "dagger" in hero["inventory"]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    _run("record_srd_control_held_items", state, target_state="hero", held_items=[])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="carried and held"):
        _attack(state, data, weapon="dagger")
    assert state == before
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["dagger"])
    result = _attack(state, data, weapon="dagger").result["context"]
    assert result["_ability_modifier"] == result["_damage_modifier"] == 3
    assert result["_weapon_proficiency_modifier"] == 6
    assert result["_attack_modifier"] == 9
    assert result["_style_selected"] == []
    assert result["_martial_arts_available"] is False
    assert result["_weapon_mastery"] is None
    assert result["_shapechange_weapon_source"]["source_id"] == source_id
    assert result["_shapechange_weapon_source"]["page"] == 161
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state, data, weapon="dagger")
    assert state == before


def test_polymorph_keeps_ordinary_weapon_rejection_and_does_not_spend_an_action():
    state, data, params = forms._setup(
        "polymorph",
        transformation_form="ape",
        target="hero",
        target_character="hero",
        forego_save_targets=["hero"],
    )
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["dagger"])
    forms._cast(state, data, params)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="source-defined attacks"):
        _attack(state, data, weapon="dagger")
    assert state == before


def test_binding_repeat_keeps_the_native_damage_and_turn_callbacks():
    runtime, content = casting._runtime()
    assert _load_runtime(bind_runtime_consumers(content.decode())) == runtime
    tool = runtime["tools"]["resolve_combat_attack"]
    assert any(e.get("call") == "spend_srd_attack" for e in tool["effects"])
    assert any(e.get("call") == "finish_srd_active_control_order_attack" for e in tool["effects"])
    assert "apply_srd_qualifying_damage" in str(tool["effects"])
    spend = runtime["tools"]["spend_srd_attack"]
    assert "actor_state.form_statistics_replaced != true" in str(spend["effects"])
    baseline = _load_runtime(
        (Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    )

    def receiving_suffix(owner):
        index = next(
            i
            for i, effect in enumerate(owner["effects"])
            if effect.get("condition", "").startswith("_attack_outcome == 'miss'")
        )
        return owner["effects"][index:]

    for name in ("resolve_combat_attack", "resolve_combat_attack_followup"):
        assert receiving_suffix(runtime["tools"][name]) == receiving_suffix(baseline["tools"][name])
