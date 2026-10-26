"""Clone owns actual material vessels, maturation, logical identity and replacement bodies."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_body_soul_spells import PUBLIC_TOOLS, extend_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_planar_binding as binding
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_illusion_removals import _prepare
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_shared_source_adoption import _case as _source_case

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    root = os.environ.get("FC14_BODY_RUNTIME_ROOT")
    if root:
        monkeypatch.setattr(budget, "ROOT", Path(root))
    budget._mechanic.cache_clear()
    monkeypatch.setattr(
        cards,
        "_spell_definitions",
        lambda: budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())[
            "definitions"
        ]["spells"],
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    yield
    budget._mechanic.cache_clear()


def _case(*, interior=(5, 5, 6), vessel="clone-vessel"):
    state, data, params = _source_case("blink")
    materials = _prepare(state, data, "clone")
    params.update(
        spell="clone",
        target="hero",
        slot_resource="spell_slot_8",
        material_item_ids=materials,
        body_vessel_id=vessel,
    )
    state[f"{SRD}.entities"]["hero"].update(hp=40, max_hp=40)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero = next(e for e in scene["entities"] if e["id"] == "hero")
    hero["footprint"] = {"kind": "box", "half_extents": [2.5, 2.5]}
    hero["height"] = 6
    scene["entities"].append(
        {
            "id": vessel,
            "map_id": scene["map"]["id"],
            "position": [3, 0, 0],
            "footprint": {"kind": "box", "half_extents": [3, 3]},
            "height": 6,
            "facing": [1, 0],
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "metadata": {"is_object": True, "plane": "material"},
        }
    )
    budget._run(
        "record_srd_body_vessel",
        state,
        caster_state="hero",
        object_id=vessel,
        material_item_id=materials[1],
        interior_feet=list(interior),
        sealed=True,
    )
    budget._run("record_srd_soul_disposition", state, target_state="hero", free=True, willing=True)
    return state, data, params


def _cast(state, data, params):
    budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=600,
        uninterrupted=True,
    )
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _body(state):
    return next(
        e["metadata"]["srd_clone_body"]
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["metadata"].get("srd_clone_body")
    )


def _mature(state):
    for _ in range(17):
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=100000)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=28000)


def _kill(state, data):
    return budget._run(
        "apply_srd_damage",
        state,
        participant_data=data,
        target="hero",
        amount=10000,
        adjustment=0,
        damage_type="force",
    )


def test_actual_hour_material_cost_bound_vessel_and_maturation():
    state, data, params = _case()
    hero = state[f"{SRD}.entities"]["hero"]
    slot = hero["resources"]["spell_slot_8"]
    quantities = [m["quantity"] for m in hero["spell_materials"]]
    _cast(state, data, params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_8"] == slot - 1
    assert [m["quantity"] for m in hero["spell_materials"]] == [quantities[0] - 1, quantities[1]]
    body = _body(state)
    assert body["matures_tick"] - body["started_tick"] == 3456000
    assert body["mature"] is False
    assert body["dispel_ongoing"] is False
    _mature(state)
    assert _body(state)["mature"] is True


def test_actual_bound_vessel_too_small_rejects_atomically():
    state, data, params = _case(interior=(4, 5, 6))
    budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=600,
        uninterrupted=True,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="hold the creature"):
        budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_opening_vessel_retires_only_exact_physical_clone():
    state, data, params = _case()
    _cast(state, data, params)
    budget._run(
        "operate_srd_clone_vessel",
        state,
        actor_state="hero",
        object_id="clone-vessel",
        operation="open",
    )
    assert _body(state)["active"] is False
    budget._run(
        "operate_srd_clone_vessel",
        state,
        actor_state="hero",
        object_id="clone-vessel",
        operation="close",
    )
    assert _body(state)["active"] is False


def test_death_before_maturity_does_not_transfer_when_body_later_matures():
    state, data, params = _case()
    _cast(state, data, params)
    _kill(state, data)
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is False
    _mature(state)
    budget._run("settle_srd_clone_transfer", state, target_state="hero")
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is False
    assert _body(state)["transferred"] is False


def test_actual_death_transfers_same_individual_without_equipment():
    state, data, params = _case()
    _cast(state, data, params)
    _mature(state)
    hero = state[f"{SRD}.entities"]["hero"]
    inventory = deepcopy(hero["inventory"])
    individuals = set(state[f"{SRD}.entities"])
    _kill(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is True
    assert hero["hp"] == hero["max_hp"]
    assert hero["inventory"] == []
    assert hero["current_body_source_id"] == _body(state)["source_id"]
    assert _body(state)["transferred"] is True
    prior = hero["prior_body_sources"]["hero"]
    assert prior["inventory"] == inventory
    assert prior["logical_actor_id"] == "hero"
    assert prior["soul_elsewhere"] is True
    assert set(state[f"{SRD}.entities"]) == individuals
    assert next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "hero")[
        "position"
    ] == [3, 0, 0]


@pytest.mark.parametrize("free,willing", [(False, True), (True, False)])
def test_actual_unavailable_soul_does_not_transfer(free, willing):
    state, data, params = _case()
    _cast(state, data, params)
    _mature(state)
    budget._run(
        "record_srd_soul_disposition", state, target_state="hero", free=free, willing=willing
    )
    _kill(state, data)
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is False


def test_prior_animated_body_ownership_retained_independently():
    state, data, params = _case()
    _cast(state, data, params)
    _mature(state)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["body_animated_actor_id"] = "source-zombie"
    hero["body_available"] = False
    _kill(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    prior = hero["prior_body_sources"]["hero"]
    assert prior["body_animated_actor_id"] == "source-zombie"
    assert prior["body_available"] is False
    assert hero["body_available"] is True
    result = budget._run(
        "read_srd_prior_body_source", state, target_state="hero", body_source_id="hero"
    )
    assert result.result["context"]["_prior_body"] == prior


def test_actual_source_zombie_body_consumption_survives_clone_transfer():
    runtime = budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())
    if "prepare_srd_source_zombie" not in runtime["tools"]:
        pytest.skip("Requires the admitted source Zombie creation owner.")
    state, data, params = _case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(
        creature_type="humanoid",
        living_creature_type="humanoid",
        size_rank=2,
        body_available=True,
        vital_parts_present=True,
    )
    _cast(state, data, params)
    _mature(state)
    budget._run("record_srd_soul_disposition", state, target_state="hero", free=False, willing=True)
    _kill(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    actual_death_tick = hero["death_tick"]
    assert actual_death_tick is not None and hero["is_alive"] is False
    budget._run(
        "prepare_srd_source_zombie",
        state,
        participant_data=data,
        creature_id="source-zombie",
        corpse_state="hero",
        source_caster="other",
        other_language="draconic",
    )
    hero = state[f"{SRD}.entities"]["hero"]
    zombie = deepcopy(state[f"{SRD}.entities"]["source-zombie"])
    created_source = deepcopy(state[f"{SRD}.state"]["created_actor_records"]["source-zombie"])
    assert hero["body_animated_actor_id"] == "source-zombie" and hero["body_available"] is False
    assert zombie["created_body_source_state_id"] == "hero"
    assert created_source["body_source"] == "hero"
    budget._run("record_srd_soul_disposition", state, target_state="hero", free=True, willing=True)
    budget._run("settle_srd_clone_transfer", state, target_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is True and hero["current_body_source_id"] == _body(state)["source_id"]
    assert hero["body_available"] is True and hero["body_animated_actor_id"] is None
    prior = hero["prior_body_sources"]["hero"]
    assert prior["body_available"] is False and prior["body_animated_actor_id"] == "source-zombie"
    assert prior["death_tick"] == actual_death_tick and prior["death_creature_type"] == "humanoid"
    assert state[f"{SRD}.entities"]["source-zombie"] == zombie
    assert state[f"{SRD}.state"]["created_actor_records"]["source-zombie"] == created_source
    outcome = budget._run(
        "read_srd_prior_body_source", state, target_state="hero", body_source_id="hero"
    )
    assert outcome.result["context"]["_prior_body"] == prior


def test_emitter_stability_preserves_compiled_owners():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    once = yaml.load(extend_runtime(text), Loader=yaml.CSafeLoader)
    twice = yaml.load(extend_runtime(extend_runtime(text)), Loader=yaml.CSafeLoader)
    assert once == twice


def test_actual_consumed_original_body_cannot_be_freed_by_body_fact_declaration():
    state, data, params = _case()
    _kill(state, data)
    state[f"{SRD}.entities"]["hero"].update(
        body_animated_actor_id="source-zombie", body_available=False
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="animated corpse"):
        budget._run(
            "record_srd_deceased_body",
            state,
            target_state="hero",
            death_tick=0,
            old_age=False,
            soul_willing=True,
            body_available=True,
            vital_parts_present=True,
            living_creature_type="humanoid",
            name="Original mage",
        )
    assert state == before


def test_public_clone_paid_cast_reopen_mature_actual_death_and_retries(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(binding, "PUBLIC_TOOLS", binding.PUBLIC_TOOLS | PUBLIC_TOOLS)
    monkeypatch.setattr(public_casting, "bundled_srd_archive", binding._archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell, **kwargs: _case())
    database = tmp_path / "clone.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "clone")
        receipt, _ = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        assert owner.execute_action(command) == cast
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for _ in range(17):
            receipt, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=100000)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=28000)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        mature = owner.snapshot("casting")
        assert _body(mature.state)["mature"] is True
        receipt, _ = _prepare_mage(owner, [{"spell": "power_word_kill", "uses_per_day": 1}])
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        kill_params = params | {
            "caster_state": "mage",
            "spell": "power_word_kill",
            "target": "hero",
            "material_item_ids": [],
        }
        kill_params.pop("caster")
        kill_params.pop("slot_resource")
        killed, command = _act(owner, "cast_spell_outside_combat", **kill_params)
        assert killed.status is ActionStatus.COMMITTED, killed.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["is_alive"] is True
        assert _body(saved.state)["transferred"] is True
        assert saved.state[f"{SRD}.entities"]["hero"]["inventory"] == []
        assert owner.execute_action(command) == killed
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipt, command = _act(
            owner, "read_srd_clone_body", source_id=_body(saved.state)["source_id"]
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        after = owner.snapshot("casting")
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == after
