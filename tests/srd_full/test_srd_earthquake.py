"""Earthquake's paid flat-floor tremor reaches actual saves, turn receipts and terrain costs."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_magic_jar as jar
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    root = os.environ.get("FC14_EARTHQUAKE_RUNTIME_ROOT")
    assert root, "Select the genuine Earthquake-composed native owner closure"
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
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    yield
    budget._mechanic.cache_clear()


def _run(tool_name, state, data, **params):
    return budget._run(tool_name, state, participant_data=data, **params)


def _target(state, actor="hero"):
    return state[f"{SRD}.entities"][actor]


def _prepare_target(state, data, actor, dexterity, point):
    _run(
        "prepare_srd_creature",
        state,
        data,
        creature_id=actor,
        name=actor,
        source=f"Admitted native Humanoid: AC12, HP40, Speed30, DEXsave{dexterity}; Blur1/day.",
        armor_class=12,
        hit_points=40,
        speed=30,
        size_rank=2,
        creature_type="humanoid",
        initiative_modifier=0,
        dexterity_save=dexterity,
        constitution_save=2,
        spellcasting_ability="wisdom",
        spellcasting_modifier=0,
        spell_attack_bonus=2,
        spell_save_dc=10,
        spells=[{"spell": "blur", "uses_per_day": 1}],
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(actor, point, participant_ref=actor, creature_type="humanoid")
    )


def _case(*, combat=False, self_inside=False):
    state, data, params = cards._spell_case("earthquake")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    floor = {
        "id": "quake-floor",
        "map_id": "information",
        "volume": {"kind": "box", "min": [-600, -600, -5], "max": [600, 600, 0]},
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "occupies_cells": True,
        "metadata": {"ground": True, "plane": "material"},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    }
    scene = _scene(
        _entity("hero", [150 if self_inside else 0, -10, 0], participant_ref="hero"),
        geometry=[floor],
        zones=[
            {
                "id": "ambient",
                "map_id": "information",
                "shape": {"kind": "box", "min": [-600, -600, -10], "max": [600, 600, 100]},
                "metadata": {"plane": "material", "lighting": "bright"},
            }
        ],
    )
    state[f"{SRD}.state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": scene}
    _prepare_target(state, data, "victim", -10, [150, 0, 0])
    _prepare_target(state, data, "successful", 30, [160, 10, 0])
    state[f"{SRD}.combat"].update(encounter_active=combat, turn_active=combat)
    params.update(target="hero", earthquake_point=[150, 0, 0])
    return state, data, params


def _source(state):
    return list(state[f"{SRD}.state"]["persistent_spell_areas"].values())[-1]


def _cast(state, data, params):
    name = (
        "cast_spell"
        if state[f"{SRD}.combat"]["encounter_active"]
        else ("cast_spell_outside_combat")
    )
    return _run(name, state, data, **params)


def _cost(state, data, start, end, *, difficult=False, airborne=False):
    return _run(
        "srd_information_movement_cost",
        state,
        data,
        start=start,
        end=end,
        plane="material",
        base_difficult=difficult,
        ignore_ground_environments=airborne,
    ).result["context"]["_route_cost"]


def test_paid_tremor_uses_real_saves_and_breaks_actual_target_concentration():
    state, data, params = _case()
    _run(
        "cast_spell_outside_combat",
        state,
        data,
        spell="blur",
        caster_state="victim",
        target="victim",
        target_distance=0,
        caster_can_see_target=True,
        target_willing=True,
        all_targets_willing=True,
        caster_can_touch_all_targets=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    assert _target(state, "victim")["concentration_spell_id"] == "blur"
    slots = _target(state)["resources"]["spell_slot_8"]
    cast = _cast(state, data, params)
    source = _source(state)
    assert source["spell_level"] == 8 and source["cast_id"]
    assert source["source_caster_id"] == "hero" and source["active"] is True
    assert _target(state)["resources"]["spell_slot_8"] == slots - 1
    rows = source["last_event_rows"]
    assert {r["target"]: r["outcome"] for r in rows} == {
        "victim": "failure",
        "successful": "success",
    }
    assert rows[0]["concentration_broken"] is True
    assert "prone" in _target(state, "victim")["conditions"]
    assert "prone" not in _target(state, "successful")["conditions"]
    assert _target(state, "victim")["concentration_spell_id"] is None
    assert cast.result["context"]["_casting_profile"]["save_dc"] == source["save_dc"]
    assert cast.result["status"] == "committed"


def test_initial_tremor_still_resolves_other_creatures_after_caster_breaks_own_concentration():
    state, data, params = _case(self_inside=True)
    _cast(state, data, params)
    source = _source(state)
    assert {r["target"] for r in source["last_event_rows"]} == {
        "hero",
        "victim",
        "successful",
    }
    assert source["active"] is False and _target(state)["concentration_spell_id"] is None
    assert "prone" in _target(state)["conditions"]
    assert "prone" in _target(state, "victim")["conditions"]
    assert _cost(state, data, [140, 0, 0], [150, 0, 0]) == pytest.approx(10)


def test_actual_caster_end_repeats_once_and_other_boundaries_do_not():
    state, data, params = _case(combat=True)
    _cast(state, data, params)
    source = _source(state)
    initial = deepcopy(source["last_event_rows"])
    _run(
        "process_srd_turn_effects",
        state,
        data,
        lasting_state="state",
        boundary_actor="victim",
        boundary_phase="end",
        boundary_turn=1,
    )
    assert _source(state)["last_event_rows"] == initial
    _run("end_combat_turn", state, data)
    source = _source(state)
    assert source["last_end_turn"] >= 0 and source["last_event_rows"] != initial
    before = deepcopy(state)
    _run(
        "process_srd_turn_effects",
        state,
        data,
        lasting_state="state",
        boundary_actor="hero",
        boundary_phase="end",
        boundary_turn=source["last_end_turn"],
    )
    assert _source(state)["last_event_rows"] == source["last_event_rows"]
    assert _target(state, "victim")["conditions"] == _target(before, "victim")["conditions"]


def test_ground_circle_cost_and_real_expiry_preserve_independent_difficult_terrain():
    state, data, params = _case()
    _cast(state, data, params)
    assert _cost(state, data, [40, 0, 0], [60, 0, 0]) == pytest.approx(30)
    assert _cost(state, data, [140, 0, 0], [150, 0, 0], difficult=True) == pytest.approx(20)
    assert _cost(state, data, [220, 80, 0], [230, 80, 0]) == pytest.approx(10)
    assert _cost(state, data, [140, 0, 10], [150, 0, 10], airborne=True) == pytest.approx(10)
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=9)
    assert _source(state)["active"] is True
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=1)
    assert _source(state)["active"] is False
    assert _cost(state, data, [140, 0, 0], [150, 0, 0]) == pytest.approx(10)
    assert _cost(state, data, [140, 0, 0], [150, 0, 0], difficult=True) == pytest.approx(20)


@pytest.mark.parametrize("invalid", ["range", "floor", "unseen", "fissures"])
def test_unsupported_or_illegal_inputs_reject_before_cast_cost(invalid):
    state, data, params = _case()
    if invalid == "range":
        params["earthquake_point"] = [501, 0, 0]
    elif invalid == "floor":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["volume"]["max"][0] = 200
    elif invalid == "unseen":
        state[f"{SRD}.state"]["spatial_scene"]["zones"][0]["metadata"]["lighting"] = "darkness"
    else:
        params[f"earthquake_{invalid}"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state, data, params)
    assert state == before


def test_actual_airborne_and_other_plane_bodies_are_not_ground_targets():
    state, data, params = _case()
    _prepare_target(state, data, "airborne", -10, [160, -10, 20])
    _prepare_target(state, data, "other-plane", -10, [160, -20, 0])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][-1]["metadata"]["plane"] = "astral"
    _cast(state, data, params)
    assert {r["target"] for r in _source(state)["last_event_rows"]} == {
        "victim",
        "successful",
    }
    assert _target(state, "airborne")["conditions"] == []
    assert _target(state, "other-plane")["conditions"] == []


def test_admitted_target_bound_uses_actual_prepared_creatures_within_unchanged_budget():
    state, data, params = _case()
    for index in range(6):
        _prepare_target(state, data, f"crowd-{index}", -10, [110 + index * 2, -20, 0])
    _cast(state, data, params)
    assert len(_source(state)["last_event_rows"]) == 8


def test_ninth_actual_ground_target_rejects_before_cost_and_source_creation():
    state, data, params = _case()
    for index in range(7):
        _prepare_target(state, data, f"crowd-{index}", -10, [110 + index * 2, -20, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="eight"):
        _cast(state, data, params)
    assert state == before


def test_two_real_casts_keep_independent_terrain_when_one_concentration_ends():
    state, data, params = _case()
    _cast(state, data, params)
    first = _source(state)["id"]
    supplies = deepcopy(_target(state)["spell_materials"])
    _run(
        "prepare_srd_creature",
        state,
        data,
        creature_id="second-caster",
        name="Second caster",
        source="Admitted native caster: Earthquake1/day at level8, DC18, actual fractured rock.",
        armor_class=12,
        hit_points=40,
        speed=30,
        size_rank=2,
        creature_type="humanoid",
        initiative_modifier=0,
        dexterity_save=0,
        constitution_save=2,
        spellcasting_ability="wisdom",
        spellcasting_modifier=4,
        spell_attack_bonus=10,
        spell_save_dc=18,
        spells=[{"spell": "earthquake", "uses_per_day": 1, "casting_level": 8}],
        inventory=[s["inventory_item"] for s in supplies],
        spell_materials=supplies,
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("second-caster", [0, -40, 0], participant_ref="second-caster")
    )
    choices = {k: v for k, v in params.items() if k not in {"caster", "slot_resource"}}
    _cast(state, data, choices | {"caster_state": "second-caster"})
    second = _source(state)["id"]
    assert first != second and _target(state, "second-caster")["spell_uses_remaining"] == {
        "earthquake": 0,
    }
    _run("end_srd_concentration_node", state, data, source_state="hero", target_state="hero")
    sources = state[f"{SRD}.state"]["persistent_spell_areas"]
    assert sources[first]["active"] is False and sources[second]["active"] is True
    assert _cost(state, data, [140, 0, 0], [150, 0, 0]) == pytest.approx(20)
    _run(
        "end_srd_concentration_node",
        state,
        data,
        source_state="second-caster",
        target_state="second-caster",
    )
    assert _cost(state, data, [140, 0, 0], [150, 0, 0]) == pytest.approx(10)
    assert "prone" in _target(state, "victim")["conditions"]


def _public_fixture(monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent, "Select the intact admitted public package dependency"
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda *_a, **_k: _case())
    drafts = bundled_packages._srd_drafts

    def selected_drafts():
        primary, reference = drafts()
        runtime = yaml.load(
            (budget.ROOT / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader
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

    def archive():
        primary, reference = bundled_packages._srd_drafts()
        content = (budget.ROOT / "runtime/ruleset.yaml").read_bytes()
        runtime = yaml.load(content, Loader=yaml.CSafeLoader)
        record = deepcopy(primary.record)
        for declaration in record["tools"]:
            declaration["input_schema"] = _tool_schema(
                runtime["tools"][declaration["name"]], tool_name=declaration["name"]
            )
        payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": content}
        return bundled_packages._archive(
            (
                NormalizedPackageDraft(record, payloads, primary.format_version),
                reference,
            )
        )

    monkeypatch.setattr(jar.public_casting, "bundled_srd_archive", archive)


def test_public_paid_tremor_reopens_moves_expires_and_retries_without_replaying_saves(
    tmp_path,
    monkeypatch,
):
    _public_fixture(monkeypatch)
    database = tmp_path / "earthquake.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "earthquake")
        original = owner.snapshot("casting")
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = _source(saved.state)
        assert source["cast_id"] and source["spell_level"] == 8
        assert _target(saved.state)["resources"]["spell_slot_8"] == (
            _target(original.state)["resources"]["spell_slot_8"] - 1
        )
        assert "prone" in _target(saved.state, "victim")["conditions"]
        assert owner.execute_action(command) == cast and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        moved, move_command = _act(
            owner,
            "move_srd_scene_creature",
            actor_state="successful",
            destination=[170, 10, 0],
            mode="walk",
            difficult_terrain=False,
        )
        assert moved.status is ActionStatus.COMMITTED, moved.result
        moving = owner.snapshot("casting")
        assert _target(moving.state, "successful")["scene_movement_spent"] == pytest.approx(20)
        assert owner.execute_action(move_command) == moved and owner.snapshot("casting") == moving
        elapsed, clock_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=10)
        assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
        final = owner.snapshot("casting")
        assert _source(final.state)["active"] is False
        assert _source(final.state)["last_event_rows"] == source["last_event_rows"]
        assert owner.execute_action(clock_command) == elapsed and owner.snapshot("casting") == final
        assert owner.execute_action(command) == cast and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
