"""Mind Spike presents observed loss after typed mitigation and the complete Curse packet."""

from random import Random

import pytest

from sylvia_gm import dice
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_wave3_information import SRD
from tests.srd_full.test_srd_spell_wave4_divination import _case


def _card_values(value, label):
    """Read a displayed row regardless of its section in the shared result card."""
    if isinstance(value, dict):
        if value.get("label") == label:
            yield value["value"]
        for child in value.values():
            yield from _card_values(child, label)
    elif isinstance(value, list):
        for child in value:
            yield from _card_values(child, label)


def _seed(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


@pytest.mark.parametrize("resistant,temp_hp", [(False, 0), (True, 5), (True, 100)])
def test_mind_spike_reports_actual_hp_and_temp_hp_loss(monkeypatch, resistant, temp_hp):
    _seed(monkeypatch)
    state, data, parameters = _case("mind_spike")
    target = state[f"{SRD}.entities"]["guard"]
    target.update(damage_resistances=["psychic"] if resistant else [], temp_hp=temp_hp)
    before_hp = target["hp"]
    before = target["hp"] + target["temp_hp"]
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    after = state[f"{SRD}.entities"]["guard"]
    loss = before - after["hp"] - after["temp_hp"]
    assert loss > 0
    assert list(_card_values(result.result["result_card"], "Divination")) == [loss]
    if temp_hp == 100:
        assert after["hp"] == before_hp
        assert after["temp_hp"] < 100


def test_mind_spike_feedback_includes_actual_typed_curse_packet(monkeypatch):
    _seed(monkeypatch)
    state, data, parameters = _case("mind_spike")
    target = state[f"{SRD}.entities"]["guard"]
    target.update(damage_resistances=["psychic"], temp_hp=8)
    scene_target = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    scene_target["position"] = [5, 0, 0]
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("bestow_curse")
    data["wizard_spellbook"].append("bestow_curse")
    data["spellcasting_grants"][0]["spells"].append("bestow_curse")
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            parameters
            | {
                "spell": "bestow_curse",
                "slot_resource": "spell_slot_5",
                "curse_mode": "damage",
                "targets": ["guard"],
            }
        ),
    )
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") != "bestow_curse"
    before = target["hp"] + target["temp_hp"]
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    after = state[f"{SRD}.entities"]["guard"]
    assert after["last_curse_damage_roll"]["total"] > 0
    loss = before - after["hp"] - after["temp_hp"]
    assert list(_card_values(result.result["result_card"], "Divination")) == [loss]


def test_public_mind_spike_feedback_reopens_and_retry_keeps_actual_damage(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    _seed(monkeypatch)
    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / "mind-spike-feedback.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "mind_spike",
            target_overrides={
                "hp": 500,
                "max_hp": 500,
                "temp_hp": 100,
                "damage_resistances": ["psychic"],
            },
        )
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        target = saved.state[f"{SRD}.entities"]["guard"]
        assert target["hp"] == 500
        loss = 600 - target["hp"] - target["temp_hp"]
        assert list(_card_values(receipt.result["result_card"], "Divination")) == [loss]
        assert loss > 0
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
