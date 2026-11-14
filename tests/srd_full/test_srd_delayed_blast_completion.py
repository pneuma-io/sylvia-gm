"""Actual paid bead sources settle after dismissal, selected Dispel and clock completion."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_delayed_blast_completion import bind_runtime
from scripts.srd_full.srd_delayed_blast_contacts import PUBLIC_TOOLS as CONTACT_TOOLS
from scripts.srd_full.srd_delayed_blast_contacts import bind_runtime_consumers as bind_contacts
from scripts.srd_full.srd_delayed_blast_fireball import DRAIN, PUBLIC_TOOLS, SPELL
from scripts.srd_full.srd_spell_removals import PUBLIC_TOOLS as REMOVAL_TOOLS
from scripts.srd_full.srd_spell_removals import extend_runtime as extend_removals
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_delayed_blast_contacts as contacts
from tests import test_srd_delayed_blast_fireball as bead
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run


@lru_cache
def _runtime():
    parent, _ = bead._runtime()
    text = bind_contacts(yaml.dump(parent, Dumper=_RuntimeDumper, sort_keys=False))
    text = extend_removals(text)
    before = _load_runtime(text)
    public = PUBLIC_TOOLS | CONTACT_TOOLS | REMOVAL_TOOLS | {"advance_srd_lasting_time"}
    text = bind_runtime(text, public)
    runtime = _load_runtime(text)
    assert (
        runtime["tools"]["detonate_srd_delayed_blast_fireball"]
        == (before["tools"]["detonate_srd_delayed_blast_fireball"])
    )
    assert (
        runtime["tools"]["apply_srd_qualifying_damage"]
        == (before["tools"]["apply_srd_qualifying_damage"])
    )
    assert bind_runtime(text, public) == text
    return runtime, text.encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", bead.Rolls())


def test_paid_dismissal_queues_before_deletion_and_outer_completion_detonates():
    state, data, params = bead._case()
    bead._cast(state, data, params)
    source = deepcopy(bead._sources(state)[0])
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id=source["cast_id"])
    event = bead._event(state, source["source_id"])
    assert event["status"] == "resolved" and event["record"]["active"] is True
    assert not bead._sources(state)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 964
    assert state[f"{SRD}.entities"]["other"]["hp"] == 982


def test_paid_selected_bead_dispel_uses_actual_failed_then_successful_check(monkeypatch):
    state, data, params = bead._case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("dispel_magic")
    data["wizard_spellbook"].append("dispel_magic")
    next(g for g in data["spellcasting_grants"] if g["source"] == "wizard")["spells"].append(
        "dispel_magic"
    )
    bead._cast(state, data, params)
    source = deepcopy(bead._sources(state)[0])
    ordinary = dict(params, spell="dispel_magic", slot_resource="spell_slot_3", target="hero")
    bead._cast(state, data, ordinary)
    assert bead._sources(state)[0]["source_id"] == source["source_id"]
    assert state[f"{SRD}.state"].get("delayed_blast_detonations") is None
    params.update(
        spell="dispel_magic",
        slot_resource="spell_slot_3",
        recovery_choice=SPELL,
        recovery_source_id=source["source_id"],
    )
    monkeypatch.setattr(dice, "_rng", bead.Rolls(save=1))
    result = bead._cast(state, data, params)
    assert bead._sources(state)[0]["active"] is True
    assert "False" in str(result["result_card"])
    monkeypatch.setattr(dice, "_rng", bead.Rolls(save=20))
    result = bead._cast(state, data, params)
    event = bead._event(state, source["source_id"])
    assert event["status"] == "resolved" and event["record"]["plane"] == "material"
    assert not bead._sources(state)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 0
    assert source["source_id"] in str(result["result_card"])


def test_actual_clock_expiry_drains_once_after_source_and_concentration_settle():
    state, data, params = bead._case()
    bead._cast(state, data, params)
    source = deepcopy(bead._sources(state)[0])
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    event = deepcopy(bead._event(state, source["source_id"]))
    assert event["status"] == "resolved" and event["record"]["growth_dice"] == 0
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert bead._event(state, source["source_id"]) == event
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    assert state[f"{SRD}.state"]["has_delayed_blast_detonations"] is False
    assert DRAIN in str(_runtime()[0]["tools"]["advance_srd_lasting_time"]["effects"])


def test_selected_dispel_qualifies_and_detonates_current_moved_holder_point(monkeypatch):
    state, data, params = contacts._case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("dispel_magic")
    data["wizard_spellbook"].append("dispel_magic")
    next(g for g in data["spellcasting_grants"] if g["source"] == "wizard")["spells"].append(
        "dispel_magic"
    )
    bead._cast(state, data, params)
    source = deepcopy(bead._sources(state)[0])
    _run(
        "touch_srd_delayed_blast_fireball",
        state,
        actor_state="guard",
        source_id=source["source_id"],
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[35, 5, 0])
    params.update(
        spell="dispel_magic",
        slot_resource="spell_slot_3",
        recovery_choice=SPELL,
        recovery_source_id=source["source_id"],
    )
    monkeypatch.setattr(dice, "_rng", bead.Rolls(save=20))
    bead._cast(state, data, params)
    event = bead._event(state, source["source_id"])
    assert event["status"] == "resolved"
    assert source["origin"] == [30, 5, 2]
    assert event["record"]["origin"] == [35, 5, 2]
