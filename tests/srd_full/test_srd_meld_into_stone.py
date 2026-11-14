"""Meld casts and actual stone/geometry consumers own the full merge and exit lifecycle."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_meld_into_stone import PROFILE, PUBLIC_TOOLS, extend_runtime
from scripts.srd_full.srd_meld_stone_consumers import bind_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_magic_jar_circle import _npc
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    root = os.environ.get("FC14_MELD_RUNTIME_ROOT")
    assert root, "Select the actual source-composed Meld fixture"
    monkeypatch.setenv("FC14_JAR_RUNTIME_ROOT", root)
    yield from jar._compiled.__wrapped__(monkeypatch)


def _case(*, surface=False):
    state, data, params = jar.cards._spell_case("meld_into_stone")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    body = _entity("hero", [-0.4, 0, 0], participant_ref="hero", creature_type="humanoid")
    body["height"] = 4
    objects = [
        body,
        _entity("guard", [-8, -5, 0], participant_ref="guard"),
        _entity("other", [-10, 0, 0], participant_ref="other"),
    ]
    geometry = []
    if surface:
        geometry.append(
            {
                "id": "stone",
                "map_id": "information",
                "volume": {"kind": "box", "min": [0, -2, 0], "max": [4, 2, 4]},
                "active": True,
                "movement": "blocking",
                "occupies_cells": True,
                "visibility": "opaque",
                "metadata": {"material": "stone", "plane": "material"},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
    else:
        stone = _entity("stone", [2, 0, 0], is_object=True, material="stone")
        stone.update(footprint={"kind": "box", "half_extents": [2, 2]}, height=4)
        objects.append(stone)
    state[f"{SRD}.state"]["spatial_scene"] = _scene(*objects, geometry=geometry)
    params.update(target="hero", meld_stone_subject="stone", target_distance=0)
    return state, data, params


def _run(tool_name, state, data, **params):
    return jar._run(tool_name, state, data, **params)


def _source(state):
    hero = state[f"{SRD}.entities"]["hero"]
    return state[f"{SRD}.state"]["meld_stone_sources"][hero["meld_stone_source_id"]]


def _body(state):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "hero")


@pytest.mark.parametrize("surface", [False, True])
def test_actual_paid_merge_uses_body_and_stone_geometry_then_five_foot_exit(surface):
    state, data, params = _case(surface=surface)
    hero = state[f"{SRD}.entities"]["hero"]
    gear, hp, slots = deepcopy(hero["inventory"]), hero["hp"], hero["resources"]["spell_slot_3"]
    _run("cast_spell_outside_combat", state, data, **params)
    source = deepcopy(_source(state))
    assert source["cast_id"] and source["source_caster_id"] == "hero"
    assert source["id"] in state[f"{SRD}.state"]["meld_stone_source_ids"]
    assert source["expires_tick"] - source["started_tick"] == 9600
    assert _body(state)["position"] == [0.4, 0, 0]
    assert _body(state)["metadata"]["plane"] == "material"
    assert _body(state)["visibility"] == "clear" and _body(state)["movement"] == "passable"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == slots - 1
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="leave|Leave"):
        _run(
            "move_srd_travel_subject",
            state,
            data,
            subject="hero",
            destination=[-5, 0, 0],
            plane="material",
        )
    assert state == before
    if not surface:
        with pytest.raises(RulesetPreconditionRejected, match="containment"):
            _run(
                "move_srd_travel_subject",
                state,
                data,
                subject="stone",
                destination=[8, 0, 0],
                plane="material",
            )
        assert state == before
    _run("leave_srd_meld_into_stone", state, data, caster_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["scene_movement_spent"] == 5
    assert hero["meld_stone_source_id"] is None and hero["inventory"] == gear and hero["hp"] == hp
    assert "prone" not in hero["conditions"]
    assert _body(state)["position"] == source["entry"]
    assert _body(state)["visibility"] == "opaque" and _body(state)["movement"] == "blocking"
    assert state[f"{SRD}.state"]["meld_stone_sources"][source["id"]]["installed"] is False


@pytest.mark.parametrize("invalid", ["small", "not_stone", "not_touching"])
def test_illegal_actual_stone_inputs_reject_without_cast_cost_or_mutation(invalid):
    state, data, params = _case()
    stone = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "stone"
    )
    if invalid == "small":
        stone["height"] = 2
    elif invalid == "not_stone":
        stone["metadata"]["material"] = "wood"
    else:
        _body(state)["position"] = [-10, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, data, **params)
    assert state == before


def test_merged_hearing_uses_actual_disadvantage_and_outside_sight_fails():
    state, data, params = _case()
    _run("cast_spell_outside_combat", state, data, **params)
    hearing = _run(
        "d20_test",
        state,
        data,
        actor="hero",
        actor_state="hero",
        ability="wisdom",
        skill="perception",
        kind="ability_check",
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
        requires_hearing=True,
    )
    assert hearing.result["context"]["_effective_disadvantage"] is True
    sight_check = _run(
        "d20_test",
        state,
        data,
        actor="hero",
        actor_state="hero",
        ability="wisdom",
        skill="perception",
        kind="ability_check",
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
        requires_sight=True,
    )
    assert sight_check.result["context"]["_automatic_failure_reason"] == "melded_outside_sight"
    assert sight_check.result["context"]["_outcome"] == "failure"
    assert sight_check.result["context"]["_d20_test"] is None
    seeing = _run(
        "resolve_srd_sight",
        state,
        data,
        observer_state="hero",
        subject_state="guard",
        distance=5,
        lighting="bright",
        obstruction="clear",
    )
    assert seeing.result["context"]["_sight_can_see"] is False
    seen = _run(
        "resolve_srd_sight",
        state,
        data,
        observer_state="guard",
        subject_state="hero",
        distance=5,
        lighting="bright",
        obstruction="clear",
    )
    assert seen.result["context"]["_sight_can_see"] is False


def test_expiry_returns_actual_body_without_destruction_damage_or_prone():
    state, data, params = _case()
    _run("cast_spell_outside_combat", state, data, **params)
    source, hp = deepcopy(_source(state)), state[f"{SRD}.entities"]["hero"]["hp"]
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=4799)
    assert _source(state)["installed"] is True
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=1)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["meld_stone_source_id"] is None and hero["hp"] == hp
    assert "prone" not in hero["conditions"] and _body(state)["position"] == source["entry"]


def test_real_self_spell_cost_and_unrelated_condition_survive_merge_exit():
    state, data, params = _case()
    data["spellcasting_grants"][0]["spells"].append("cure_wounds")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("cure_wounds")
    _run("apply_srd_condition", state, data, target="hero", condition="poisoned")
    _run("cast_spell_outside_combat", state, data, **params)
    before = deepcopy(state)
    heal = params | {"spell": "cure_wounds", "slot_resource": "spell_slot_1"}
    _run("cast_spell_outside_combat", state, data, **heal)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_1"] == (
        before[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] - 1
    )
    assert _source(state)["installed"] is True and "poisoned" in hero["conditions"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="merged caster"):
        _run("cast_spell_outside_combat", state, data, **(heal | {"target": "guard"}))
    assert state == before
    _run("leave_srd_meld_into_stone", state, data, caster_state="hero")
    assert "poisoned" in state[f"{SRD}.entities"]["hero"]["conditions"]


def _prepare_actor(state, data, actor, spell):
    npc = _npc(actor, spell, "humanoid")
    npc["spells"][0]["casting_level"] = 0 if spell == "sorcerous_burst" else 4
    _run("prepare_srd_creature", state, data, **npc)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(actor, [-8, 0, 0], participant_ref=actor, creature_type="humanoid")
    )


def test_actual_stone_damage_is_harmless_until_complete_destruction():
    state, data, params = _case()
    _run(
        "prepare_srd_spell_object",
        state,
        data,
        object_id="stone",
        material="stone",
        size="medium",
        durability="resilient",
    )
    _prepare_actor(state, data, "breaker", "sorcerous_burst")
    _run("cast_spell_outside_combat", state, data, **params)
    hp = state[f"{SRD}.entities"]["hero"]["hp"]
    burst = params | {
        "caster_state": "breaker",
        "spell": "sorcerous_burst",
        "target": None,
        "object_target_id": "stone",
        "selected_damage_type": "fire",
        "sorcerous_burst_extra_dice": False,
    }
    for p in ("caster", "slot_resource", "meld_stone_subject"):
        burst.pop(p)
    _run("cast_spell_outside_combat", state, data, **burst)
    assert _source(state)["installed"] is True and state[f"{SRD}.entities"]["hero"]["hp"] == hp
    source_id = _source(state)["id"]
    for _ in range(5):
        if state[f"{SRD}.entities"]["hero"]["meld_stone_source_id"] is None:
            break
        dice._rng = Random(0)
        _run("cast_spell_outside_combat", state, data, **burst)
    source = state[f"{SRD}.state"]["meld_stone_sources"][source_id]
    assert source["installed"] is False and source["ended_reason"] == "destroyed_stone"
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 0 and "prone" in hero["conditions"]


@pytest.mark.parametrize("occupied", [False, True])
def test_actual_stone_shape_no_fit_expels_with_rolled_force_and_prone(occupied):
    state, data, params = _case()
    _run(
        "record_srd_object_material",
        state,
        data,
        object_id="stone",
        material="stone_or_crystal",
        raw_stock=False,
    )
    _prepare_actor(state, data, "sculptor", "stone_shape")
    next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "sculptor")[
        "position"
    ] = [2, -2.4, 0]
    _run("cast_spell_outside_combat", state, data, **params)
    source_id = _source(state)["id"]
    entry = deepcopy(_source(state)["entry"])
    if occupied:
        _run(
            "move_srd_travel_subject",
            state,
            data,
            subject="other",
            destination=entry,
            plane="material",
        )
    hp = state[f"{SRD}.entities"]["hero"]["hp"]
    shape = params | {
        "caster_state": "sculptor",
        "spell": "stone_shape",
        "target": "hero",
        "object_target_id": "stone",
        "object_extent": [2, 2, 1],
        "object_form": "A shallow stone slab.",
        "material_item_ids": [
            m["id"] for m in _npc("sculptor", "stone_shape", "humanoid")["spell_materials"]
        ],
    }
    for p in ("caster", "slot_resource", "meld_stone_subject"):
        shape.pop(p)
    dice._rng = Random(0)
    _run("cast_spell_outside_combat", state, data, **shape)
    source = state[f"{SRD}.state"]["meld_stone_sources"][source_id]
    assert source["installed"] is False and source["ended_reason"] == "partial_stone"
    hero = state[f"{SRD}.entities"]["hero"]
    assert "prone" in hero["conditions"] and hero["hp"] == max(0, hp - 21)
    if occupied:
        assert _body(state)["position"] != source["entry"]
    else:
        assert _body(state)["position"] == source["entry"]


def _public_fixture(monkeypatch):
    """Install only the complete real owner closure used by this selected public journey."""
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(jar.binding, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    monkeypatch.setattr(jar.public_casting, "bundled_srd_archive", jar.binding._archive)
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda *_a, **_k: _case())
    drafts = bundled_packages._srd_drafts

    def selected_drafts():
        primary, reference = drafts()
        runtime = yaml.load(
            (jar.budget.ROOT / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader
        )
        record = deepcopy(primary.record)
        record["tools"] = [t for t in record["tools"] if t["name"] in runtime["tools"]]
        payloads = dict(primary.payloads)
        path = record["content_declarations"]
        declarations = json.loads(payloads[path])
        for inclusion in declarations["inclusions"]:
            links = inclusion.get("links", {})
            if "tools" in links:
                links["tools"] = [n for n in links["tools"] if n in runtime["tools"]]
        payloads[path] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payloads, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", selected_drafts)


def test_public_paid_merge_read_exit_saved_reopens_and_retries(tmp_path, monkeypatch):
    _public_fixture(monkeypatch)
    database = tmp_path / "meld-stone.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "meld_into_stone")
        before = owner.snapshot("casting")
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = deepcopy(_source(saved.state))
        assert source["cast_id"] and source["source_caster_id"] == "hero"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == (
            before.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] - 1
        )
        assert owner.execute_action(command) == cast and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        reading, _ = _act(owner, "read_srd_meld_into_stone", caster_state="hero")
        assert reading.status is ActionStatus.COMMITTED, reading.result
        leaving, leave_command = _act(owner, "leave_srd_meld_into_stone", caster_state="hero")
        assert leaving.status is ActionStatus.COMMITTED, leaving.result
        final = owner.snapshot("casting")
        assert _body(final.state)["position"] == source["entry"]
        assert final.state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 5
        assert owner.execute_action(leave_command) == leaving and owner.snapshot("casting") == final
        assert owner.execute_action(command) == cast and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final


def test_family_and_final_consumer_composition_repeats_exactly():
    text = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_consumers(extend_runtime(text))
    second = bind_consumers(extend_runtime(first))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)
    assert PROFILE["duration_rounds"] == 4800
