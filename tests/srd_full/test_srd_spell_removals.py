"""Dismissal uses actual source identity, no-action costs, and later projections."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_progressive_afflictions import _add_spell
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_source_instances import _case, _cast, _records


@pytest.mark.parametrize(
    "spell,extra",
    [
        ("charm_person", {"targets": ["guard", "other"], "slot_resource": "spell_slot_2"}),
        ("aid", {"targets": ["guard", "other"]}),
        ("heroism", {"targets": ["guard", "other"], "slot_resource": "spell_slot_2"}),
        ("fly", {"targets": ["guard", "other"], "slot_resource": "spell_slot_4"}),
    ],
)
def test_dismiss_whole_cast_ends_both_targets_without_spending_budget(monkeypatch, spell, extra):
    state, data, params = _case(monkeypatch, spell)
    _cast(state, data, params | extra)
    hero = deepcopy(state[f"{SRD}.entities"]["hero"])
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert not _records(state)
    current = state[f"{SRD}.entities"]["hero"]
    for key in ("resources", "actions_taken", "bonus_actions_taken", "reaction_available"):
        assert current.get(key) == hero.get(key)
    if spell in {"heroism", "fly"}:
        assert current["concentration_spell_id"] is None
    if spell == "aid":
        assert state[f"{SRD}.entities"]["guard"]["max_hp"] == 20
    if spell == "charm_person":
        assert "charmed" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    if spell == "fly":
        assert state[f"{SRD}.entities"]["guard"]["spell_flying_speed"] == 0


@pytest.mark.parametrize("spell", ["charm_person", "darkvision", "barkskin"])
def test_exact_dismissal_preserves_independent_same_tick_cast(monkeypatch, spell):
    state, data, params = _case(monkeypatch, spell)
    _cast(state, data, params | {"target": "guard", "targets": ["guard"]})
    _cast(state, data, params | {"target": "other", "targets": ["other"]})
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    records = _records(state)
    assert len(records) == 1 and records[0]["cast_id"] == "srd_cast:2"


@pytest.mark.parametrize(
    "invalid", ["wrong_owner", "incapacitated", "petrified", "expired", "legacy"]
)
def test_invalid_dismissal_rejects_atomically_without_grouping_legacy(monkeypatch, invalid):
    state, data, params = _case(monkeypatch, "charm_person")
    _cast(state, data, params | {"targets": ["guard"]})
    caster = "hero"
    if invalid == "wrong_owner":
        caster = "other"
    elif invalid in {"incapacitated", "petrified"}:
        state[f"{SRD}.entities"]["hero"]["conditions"].append(invalid)
    elif invalid == "expired":
        state[f"{SRD}.state"]["lasting_tick"] += 1200
    else:
        for source in state[f"{SRD}.state"]["timed_condition_sources"].values():
            source.pop("cast_id", None)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("dismiss_srd_spell", state, caster_state=caster, cast_id="srd_cast:1")
    assert state == before


def test_dismissal_preserves_unowned_condition_and_other_caster_source(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    state[f"{SRD}.entities"]["guard"]["conditions"].append("charmed")
    _cast(state, data, params | {"targets": ["guard"]})
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_dismissal_ends_exact_concentration_after_every_target_saved_successfully(monkeypatch):
    state, data, params = _case(monkeypatch, "hypnotic_pattern")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 30
    _cast(state, data, params | {"targets": ["guard"]})
    assert not _records(state)
    hero = deepcopy(state[f"{SRD}.entities"]["hero"])
    assert hero["concentration_spell_id"] == "hypnotic_pattern"
    assert hero["concentration_cast_id"] == "srd_cast:1"
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"] == hero["resources"]


def test_greater_restoration_actual_cast_selects_one_same_condition_effect(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    _cast(state, data, params | {"targets": ["guard"]})
    _cast(state, data, params | {"targets": ["guard"]})
    target = state[f"{SRD}.entities"]["guard"]
    first, second = target["timed_condition_source_ids"]
    cure_data, cure = _add_spell(state, "greater_restoration")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one exact condition"):
        _cast(state, cure_data, cure | {"recovery_choice": "charmed"})
    assert state == before
    _cast(state, cure_data, cure | {"recovery_choice": "charmed", "recovery_source_id": first})
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not sources[first]["installed"] and sources[second]["installed"]
    assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


def test_greater_restoration_unowned_choice_preserves_tracked_effect(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    state[f"{SRD}.entities"]["guard"]["conditions"].append("charmed")
    _cast(state, data, params | {"targets": ["guard"]})
    cure_data, cure = _add_spell(state, "greater_restoration")
    _cast(
        state,
        cure_data,
        cure
        | {
            "recovery_choice": "charmed",
            "recovery_source_id": "unowned",
        },
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["timed_condition_unowned_charmed"] is False
    assert "charmed" in target["conditions"] and len(_records(state)) == 1


def test_exact_cure_of_suppressed_charm_restores_only_other_source_after_dismissal(monkeypatch):
    state, data, params = _case(monkeypatch, "charm_person")
    _cast(state, data, params | {"targets": ["guard"]})
    _cast(state, data, params | {"targets": ["guard"]})
    first, second = state[f"{SRD}.entities"]["guard"]["timed_condition_source_ids"]
    ward_data, ward = _add_spell(state, "mind_blank")
    _cast(state, ward_data, ward)
    assert "charmed" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    cure_data, cure = _add_spell(state, "greater_restoration")
    _cast(state, cure_data, cure | {"recovery_choice": "charmed", "recovery_source_id": first})
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not sources[first]["installed"] and sources[second]["installed"]
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:3")
    assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not sources[first]["installed"]


def test_actual_dispel_preserves_instantaneous_ray_of_frost_consequence(monkeypatch):
    state, data, params = _case(monkeypatch, "ray_of_frost")
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000)
    _cast(state, data, params)
    effects = deepcopy(state[f"{SRD}.state"]["turn_effects"])
    assert effects and effects[0]["kind"] == "speed_reduction"
    dispel_data, dispel = _add_spell(state, "dispel_magic")
    _cast(state, dispel_data, dispel)
    assert state[f"{SRD}.state"]["turn_effects"] == effects
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


def test_actual_guiding_bolt_dispel_uses_real_upcast_level(monkeypatch):
    state, data, params = _case(monkeypatch, "guiding_bolt")
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000)
    _cast(state, data, params | {"slot_resource": "spell_slot_4"})
    effect = state[f"{SRD}.state"]["turn_effects"][0]
    assert effect["spell_level"] == 4 and effect["cast_id"] == "srd_cast:1"
    dispel_data, dispel = _add_spell(state, "dispel_magic")
    _cast(state, dispel_data, dispel | {"slot_resource": "spell_slot_4"})
    assert not state[f"{SRD}.state"]["turn_effects"]


def test_dismiss_ten_target_cast_with_full_effect_pool_stays_within_runtime_cap(monkeypatch):
    state, data, params = _case(monkeypatch, "water_breathing")
    entities = state[f"{SRD}.entities"]
    targets = ["guard", "other"] + [f"breather_{index}" for index in range(8)]
    for actor in targets[2:]:
        entities[actor] = deepcopy(entities["guard"])
    _cast(state, data, params | {"targets": targets})
    for actor in targets:
        state[f"{SRD}.entities"][actor].update(creature_kind="npc", constitution_score=10)
        _run("set_srd_respiration", state, target_state=actor, medium="water")
        assert state[f"{SRD}.entities"][actor]["respiration_can_breathe"] is True
    unrelated = [
        {
            "spell": "darkvision",
            "kind": "timed_senses",
            "target": "unrelated",
            "caster": "hero",
            "concentration": False,
            "expires_tick": 10000,
        }
        for _ in range(118)
    ]
    state[f"{SRD}.state"]["turn_effects"] += unrelated
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:1")
    assert state[f"{SRD}.state"]["turn_effects"] == unrelated
    assert all(
        state[f"{SRD}.entities"][actor]["respiration_can_breathe"] is False for actor in targets
    )


def test_public_cast_reopen_dismissal_and_retry_keep_exact_saved_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "dismissal.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = _create_spell_campaign(owner, "darkvision")
        cast_receipt, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast_receipt.status is ActionStatus.COMMITTED, cast_receipt.result
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.state"]["turn_effects"][0]
        cast_id = source["cast_id"]
        assert source["source_caster_id"] == "hero"
        assert owner.execute_action(cast_command) == cast_receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        receipt, command = _act(owner, "dismiss_srd_spell", caster_state="hero", cast_id=cast_id)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        settled = owner.snapshot("casting")
        assert not settled.state[f"{SRD}.state"]["turn_effects"]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == settled.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == settled.state
