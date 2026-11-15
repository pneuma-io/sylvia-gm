"""Real shared mutation and clock boundaries enforce saved ward lifetimes."""

import pytest
import yaml

from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_triggers as wards

SRD = wards.SRD


@pytest.fixture(autouse=True)
def _canonical_wards(monkeypatch):
    wards._runtime.cache_clear()
    wards._mechanic.cache_clear()
    actual = yaml.load(
        (wards.ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes(),
        Loader=yaml.CSafeLoader,
    )
    assert "apply_srd_trigger_spell" in actual["tools"]
    monkeypatch.setattr(budget, "_mechanic", wards._mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: actual["definitions"]["spells"])
    yield
    wards._runtime.cache_clear()
    wards._mechanic.cache_clear()


def _move(state, subject, destination):
    budget._run(
        "move_srd_travel_subject", state, subject=subject, destination=destination, plane="material"
    )


def test_actual_object_move_retires_glyph_before_it_can_return():
    state, _, _, identity = wards._cast("glyph_of_warding")
    _move(state, "orb", [15, 0, 0])
    assert wards._source(state)["instance"] == identity
    _move(state, "orb", [15.01, 0, 0])
    assert state[f"{SRD}.state"]["has_spell_triggers"] is False
    _move(state, "orb", [5, 0, 0])
    assert not any(
        row.get("kind") == "spell_trigger" for row in state[f"{SRD}.state"]["turn_effects"]
    )


def test_actual_holder_move_retires_glyph_without_moving_object_proxy():
    state, _, _, _ = wards._cast("glyph_of_warding")
    wards._entry(state, "orb")["metadata"]["carried_by"] = "hero"
    _move(state, "hero", [16, 0, 0])
    assert wards._entry(state, "orb")["position"] == [5, 0, 0]
    assert state[f"{SRD}.state"]["has_spell_triggers"] is False


def test_actual_clock_retires_alarm_at_the_exact_eight_hour_boundary():
    state, _, _, identity = wards._cast("alarm")
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=4799)
    assert wards._source(state)["instance"] == identity
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["has_spell_triggers"] is False
    assert not any(
        row.get("kind") == "spell_trigger" for row in state[f"{SRD}.state"]["turn_effects"]
    )


def test_actual_magic_mouth_move_preserves_source_and_uses_current_anchor():
    state, _, _, identity = wards._cast("magic_mouth")
    _move(state, "orb", [35, 0, 0])
    assert wards._source(state)["instance"] == identity
    assert wards._event(state, identity, event="visual")["_trigger_fired"] is True


def test_symbol_actual_committed_entry_and_same_turn_end_apply_only_once():
    from tests import test_srd_symbol_triggers as symbols

    state, identity = symbols._symbol()
    for name in ("guard", "other"):
        state[f"{SRD}.entities"][name].update(hp=1000, max_hp=1000)
    _move(state, "other", [5, 70, 0])
    wards._event(state, identity, event="open")
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True)
    before = state[f"{SRD}.entities"]["other"]["hp"]
    _move(state, "other", [5, 5, 0])
    after = state[f"{SRD}.entities"]["other"]["hp"]
    assert 10 <= before - after <= 100
    budget._run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="other",
        boundary_phase="end",
        boundary_turn=1,
    )
    assert state[f"{SRD}.entities"]["other"]["hp"] == after
    state[f"{SRD}.combat"]["turn_sequence"] += 1
    budget._run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="other",
        boundary_phase="end",
        boundary_turn=2,
    )
    assert state[f"{SRD}.entities"]["other"]["hp"] < after


def test_symbol_existing_public_sleep_action_uses_actual_contact_before_payment():
    from copy import deepcopy

    from sylvia_gm.domain.errors import RulesetPreconditionRejected
    from tests import test_srd_symbol_triggers as symbols

    state, identity = symbols._symbol("sleep")
    wards._event(state, identity, event="open")
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        active_actor_can_act=True,
        action_available=True,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual same-plane contact"):
        budget._run(
            "shake_srd_sleeping_creature",
            state,
            actor_state="guard",
            target_state="other",
            target_distance=0,
        )
    assert state == before
    _move(state, "guard", [5, 4, 0])
    budget._run(
        "shake_srd_sleeping_creature",
        state,
        actor_state="guard",
        target_state="other",
        target_distance=1,
    )
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert "unconscious" not in state[f"{SRD}.entities"]["other"]["conditions"]
