"""Saved medium transitions and installation facts release the actual paid child once."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_contingency_water_trigger import CHOICE, HP, WATER

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_contingency_combined_public as combined
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act

ROOT = Path(__file__).resolve().parents[2]
PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_WATER_RUNTIME",
        ROOT / ".scratch/contingency-water-v1/selected-runtime.yaml",
    )
)
SRD = combined.SRD


@pytest.fixture(scope="module")
def actual_archive():
    original = combined.PROOF
    combined.PROOF = PROOF
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    archive = combined._selected_archive()
    yield archive
    combined.PROOF = original
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()


def _water_sources(snapshot):
    return [
        effect
        for effect in snapshot.state[f"{SRD}.state"]["turn_effects"]
        if effect.get("kind") == "timed_senses" and effect.get("spell") == "water_breathing"
    ]


@pytest.mark.parametrize("already_water", [False, True])
def test_paid_water_trigger_install_or_actual_transition_releases_once_after_reopen(
    tmp_path,
    monkeypatch,
    actual_archive,
    already_water,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state, data, parameters, materials = combined._case(monkeypatch)
    parameters[CHOICE] = WATER
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    database = tmp_path / f"contingency-water-{already_water}.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        if already_water:
            # An independent genuine paid Water Breathing keeps the real caster
            # alive through the ten-minute underwater parent casting.
            water = {
                key: value for key, value in parameters.items() if not key.startswith("contingent_")
            }
            water.update(
                spell="water_breathing",
                slot_resource="spell_slot_3",
                target="hero",
                target_distance=0,
                target_willing=True,
                material_item_ids=materials["water_breathing"],
            )
            prior, _ = _act(owner, "cast_spell_outside_combat", **water)
            assert prior.status is ActionStatus.COMMITTED, prior.result
        initial, _ = _act(
            owner,
            "set_srd_respiration",
            target_state="hero",
            target_character="hero",
            medium="water" if already_water else "air",
        )
        assert initial.status is ActionStatus.COMMITTED, initial.result
        before = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        begin = {
            p["name"]: parameters[p["name"]]
            for p in combined._runtime()["tools"]["begin_srd_spell_cast"]["parameters"]
            if p["name"] in parameters
        }
        started, _ = _act(owner, "begin_srd_spell_cast", **begin)
        assert started.status is ActionStatus.COMMITTED, started.result
        saved_choices = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"][
            "extended_casting"
        ]["contingency_choices"]
        assert saved_choices[CHOICE] == WATER
        progressed, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progressed.status is ActionStatus.COMMITTED, progressed.result
        ready = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="original Contingency trigger"):
            _act(owner, "cast_spell_outside_combat", **(parameters | {CHOICE: HP}))
        assert owner.snapshot("casting") == ready
        parent_receipt, parent_command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert parent_receipt.status is ActionStatus.COMMITTED, parent_receipt.result
        installed = owner.snapshot("casting")
        hero = installed.state[f"{SRD}.entities"]["hero"]
        source_id = hero["contingency_source_id"]
        parent = installed.state[f"{SRD}.state"]["contingency_sources"][source_id]
        assert parent["trigger"] == WATER
        assert hero["resources"]["spell_slot_6"] == before["resources"]["spell_slot_6"] - 1
        assert hero["resources"]["spell_slot_3"] == before["resources"]["spell_slot_3"] - 1
        if already_water:
            # Root selected this state-worded installation check as a bounded
            # interpretation; the printed text does not separately settle it.
            assert parent["status"] == "released" and parent["installed"] is False
            assert "Trigger fulfilled at setup" in str(parent_receipt.result["result_card"])
        else:
            assert parent["status"] == "armed" and parent["installed"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(parent_command) == parent_receipt
        assert owner.snapshot("casting") == installed
        transition, transition_command = _act(
            owner,
            "set_srd_respiration",
            target_state="hero",
            target_character="hero",
            medium="water",
        )
        assert transition.status is ActionStatus.COMMITTED, transition.result
        released = owner.snapshot("casting")
        source = released.state[f"{SRD}.state"]["contingency_sources"][source_id]
        hero = released.state[f"{SRD}.entities"]["hero"]
        assert source["status"] == "released" and source["installed"] is False
        assert source["trigger_medium"] == "water"
        assert (
            source["trigger_tick"] == source["started_tick"]
            if already_water
            else (source["trigger_tick"] >= source["started_tick"])
        )
        assert "trigger_hp_before" not in source and "trigger_hp_after" not in source
        assert hero["has_contingency"] is False
        assert released.state[f"{SRD}.state"]["contingency_release_queue"] == []
        assert hero["respiration_water_until"] > released.state[f"{SRD}.state"]["lasting_tick"]
        assert transition.result["context"]["_respiration_can_breathe"] is True
        resources, sources = deepcopy(hero["resources"]), deepcopy(_water_sources(released))
        repeated, repeated_command = _act(
            owner,
            "set_srd_respiration",
            target_state="hero",
            target_character="hero",
            medium="water",
        )
        assert repeated.status is ActionStatus.COMMITTED, repeated.result
        repeated_state = owner.snapshot("casting")
        assert repeated_state.state[f"{SRD}.entities"]["hero"]["resources"] == resources
        assert _water_sources(repeated_state) == sources
        assert repeated_state.state[f"{SRD}.state"]["contingency_sources"][source_id] == source
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Saved retry rerolled"))
        for command, receipt in [
            (parent_command, parent_receipt),
            (transition_command, transition),
            (repeated_command, repeated),
        ]:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == repeated_state
