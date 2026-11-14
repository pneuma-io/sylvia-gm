"""Actual cast identities survive target fan-out without grouping independent casts."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_reaction_spells import _mage
from tests.srd_full.test_srd_scene_relations import _state as _scene_state
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


def _case(monkeypatch, spell):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target]["saving_throw_modifiers"]["wisdom"] = -100
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result


def _records(state):
    lasting = state[f"{SRD}.state"]
    return lasting.get("turn_effects", []) + [
        source
        for source in lasting.get("timed_condition_sources", {}).values()
        if source.get("installed")
    ]


@pytest.mark.parametrize(
    "spell,extra",
    [
        ("charm_person", {"targets": ["guard", "other"], "slot_resource": "spell_slot_2"}),
        ("aid", {"targets": ["guard", "other"]}),
        ("heroism", {"targets": ["guard", "other"], "slot_resource": "spell_slot_2"}),
        ("fly", {"targets": ["guard", "other"], "slot_resource": "spell_slot_4"}),
        ("longstrider", {"targets": ["guard", "other"], "slot_resource": "spell_slot_2"}),
    ],
)
def test_actual_multi_target_cast_owns_one_source_identity(monkeypatch, spell, extra):
    state, data, params = _case(monkeypatch, spell)
    result = _cast(state, data, params | extra)
    records = _records(state)
    assert len(records) == 2
    assert len({record["cast_id"] for record in records}) == 1
    assert {record["source_caster_id"] for record in records} == {"hero"}
    assert all(record["ongoing_duration"] is True for record in records)
    assert "srd_cast:1" in str(result["result_card"])


@pytest.mark.parametrize("spell", ["charm_person", "darkvision", "barkskin"])
def test_same_tick_casts_are_distinct_and_cost_real_slots(monkeypatch, spell):
    state, data, params = _case(monkeypatch, spell)
    first = params | {"target": "guard", "targets": ["guard"]}
    second = params | {"target": "other", "targets": ["other"]}
    _cast(state, data, first)
    _cast(state, data, second)
    records = _records(state)
    assert len(records) == 2
    assert {record["cast_id"] for record in records} == {"srd_cast:1", "srd_cast:2"}
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 1


def test_illegal_cast_rolls_back_identity_and_casting_cost(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state, data, params | {"targets": ["guard"], "targets_max_distance": 100})
    assert state == before


def test_instantaneous_spell_does_not_allocate_a_duration_identity(monkeypatch):
    state, data, params = _case(monkeypatch, "magic_missile")
    _cast(state, data, params)
    assert state[f"{SRD}.state"].get("spell_source_serial") is None


@pytest.mark.parametrize("constitution_save,countered", [(0, True), (30, False)])
def test_saved_begin_counterspell_completion_allocates_only_effective_cast(
    monkeypatch,
    constitution_save,
    countered,
):
    state, data, params = _case(monkeypatch, "counterspell")
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="mage",
        current_actor="mage",
    )
    _mage(state, "charm_person", constitution_save)
    scene = deepcopy(_scene_state()[f"{SRD}.state"])
    scene["spatial_scene"]["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["spatial_scene"]["entities"][1]["metadata"]["participant_ref"] = "mage"
    state[f"{SRD}.state"] = scene
    begun = _run(
        "begin_srd_spell_action",
        state,
        spell="charm_person",
        caster_state="mage",
        target="other",
        targets=["other"],
        target_distance=5,
        targets_max_distance=5,
        caster_can_see_target=True,
        caster_can_see_all_targets=True,
        all_targets_have_clear_path=True,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    trigger = begun.result["context"]["_pending_action_id"]
    assert state[f"{SRD}.state"].get("spell_source_serial") is None
    assert not _records(state)
    _run("cast_spell", state, participant_data=data, **(params | {"reaction_trigger_id": trigger}))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=trigger)
    records = _records(state)
    if countered:
        assert not records and state[f"{SRD}.state"].get("spell_source_serial") is None
    else:
        assert records and {record["cast_id"] for record in records} == {"srd_cast:1"}
        assert state[f"{SRD}.state"]["spell_source_serial"] == 1
