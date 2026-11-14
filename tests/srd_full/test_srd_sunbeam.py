"""Paid Sunbeam sources, real shared Lines, caster-start Blind, sunlight and saved later uses."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_retained_spell_attacks import _lists
from scripts.srd_full.srd_sunbeam import PROFILE, PUBLIC_TOOLS, bind_runtime_consumers, schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE, _source_runtime
from tests.srd_full.test_srd_spell_illumination import _case as _source_case
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    parent = deepcopy(_source_runtime()[0])
    runtime = deepcopy(parent)
    runtime["definitions"]["spells"]["sunbeam"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["sunbeam"] = schema()
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ].append("sunbeam")
    # The actual common paid allocator remains Root-owned; add only this selected source ID.
    for name in ("cast_spell", "cast_spell_outside_combat"):
        for sequence in _lists(runtime["tools"][name]["effects"]):
            for effect in sequence:
                if effect.get("set", {}).get("field") == "_spell_time_span_ids":
                    effect["set"]["value"].append("'sunbeam'")
    text = bind_runtime_consumers(yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False))
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(runtime["tools"], changed)
    runtime["tools"].update(changed)
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    assert (
        runtime["tools"]["resolve_spell_area_save_damage_target"]
        == (parent["tools"]["resolve_spell_area_save_damage_target"])
    )
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


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
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
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
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


class Rolls(Random):
    def __init__(self, save=10, damage=3):
        super().__init__(0)
        self.save, self.damage, self.sides = save, damage, []

    def randint(self, _low, high):
        self.sides.append(high)
        return self.save if high == 20 else min(self.damage, high)


def _case(slot=6):
    state, data, params = _source_case("sunbeam")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for row in scene["entities"]:
        row["metadata"]["lighting"] = "darkness"
        row["facing"] = [1, 0]
        if row["id"] == "hero":
            row["position"] = [0, 0, 0]
        elif row["id"] == "guard":
            row["position"] = [30, 0, 0]
        else:
            row["position"] = [90, 90, 0]
    scene["entities"].append(
        _entity(
            "saved",
            [40, 2, 0],
            participant_ref="saved",
            creature_type="humanoid",
            plane="material",
            lighting="darkness",
        )
    )
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(creature_type="humanoid", turns_started=5)
    entities["guard"].update(hp=1000, max_hp=1000, temp_hp=0, creature_type="undead")
    entities["guard"]["saving_throw_modifiers"]["constitution"] = -30
    entities["saved"] = deepcopy(entities["other"])
    entities["saved"].update(name="Saved", hp=1000, max_hp=1000, temp_hp=0, creature_type="ooze")
    entities["saved"]["saving_throw_modifiers"]["constitution"] = 30
    params.update(
        slot_resource=f"spell_slot_{slot}",
        target="hero",
        sunbeam_height=5,
        sunbeam_mote_position=[0, 0, 8],
    )
    return state, data, params


def _cast(state, data, params):
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result


def _use(state, **choices):
    return _run("use_srd_sunbeam", state, caster_state="hero", sunbeam_height=5, **choices).result


def _sources(state):
    return [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("kind") == "sunbeam"]


@pytest.mark.parametrize("slot", [6, 9])
def test_paid_source_initial_shared6d8_actual_con_fail_blind_and_success_half(monkeypatch, slot):
    state, data, params = _case(slot)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _cast(state, data, params)
    source = _sources(state)[0]
    assert source["caster"] == source["target"] == "hero"
    assert source["slot_level"] == slot and source["mote_offset"] == [0, 0, 8]
    assert source["source_id"] == state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    assert rolls.sides == [8] * 6 + [20, 20]  # no inherited undead/ooze disadvantage
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 982
    assert state[f"{SRD}.entities"]["saved"]["hp"] == 991
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "blinded" not in state[f"{SRD}.entities"]["saved"]["conditions"]
    assert result["context"]["_sunbeam_damage_roll"]["results"] == [3] * 6
    assert any("next turn" in str(r) for r in result["result_card"]["details"])


def test_actual_diagonal_line_box_intersection_height_and_plane_without_target_verdict():
    state, data, params = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    rows = {r["id"]: r for r in scene["entities"]}
    rows["guard"]["position"] = [20, 20, 0]
    rows["saved"]["position"] = [20, -20, 0]
    _cast(state, data, params | {"sunbeam_direction": [1, 1]})
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 982
    assert state[f"{SRD}.entities"]["saved"]["hp"] == 1000
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "guard")
    row["position"][2] = 20
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "saved")
    row["position"], row["metadata"]["plane"] = [40, 40, 0], "ethereal"
    result = _use(state, sunbeam_direction=[1, 1])
    assert result["context"]["_srd_area_targets"] == []
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 982


@pytest.mark.parametrize("native", [False, True])
def test_blind_waits_for_actual_next_caster_start_preserves_native_and_survives_source_end(native):
    state, data, params = _case()
    entities, lasting = state[f"{SRD}.entities"], state[f"{SRD}.state"]
    if native:
        entities["guard"]["conditions"].append("blinded")
    state[f"{SRD}.combat"]["encounter_active"] = True
    # Initial cast uses the actual combat Magic action instead of a second budget.
    state[f"{SRD}.combat"].update(
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        phase="turn_active",
        action_available=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    lasting = state[f"{SRD}.state"]
    condition_id = lasting["sunbeam_blind_source_ids"][0]
    assert lasting["timed_condition_sources"][condition_id]["sunbeam_caster_turn"] == 5
    _run("end_concentration", state, caster_state="hero")
    assert _sources(state) == [] and state[f"{SRD}.state"]["has_sunbeam_sources"] is False
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="start",
        boundary_turn=6,
    )
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 6
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="hero",
        boundary_phase="start",
        boundary_turn=6,
    )
    assert state[f"{SRD}.state"]["timed_condition_sources"][condition_id]["installed"] is False
    assert ("blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]) is native
    assert state[f"{SRD}.state"]["has_sunbeam_blinds"] is False


def test_later_magic_action_keeps_original_source_no_slot_current_line_and_clock_expiry():
    state, data, params = _case()
    _cast(state, data, params)
    source = deepcopy(_sources(state)[0])
    actor = state[f"{SRD}.entities"]["hero"]
    slots = {key: value for key, value in actor.items() if key.startswith("spell_slot_")}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual exploration clock"):
        _use(state)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _use(state, sunbeam_direction=[0, 1])
    assert _sources(state) == [source]
    assert slots == {
        key: value
        for key, value in state[f"{SRD}.entities"]["hero"].items()
        if key.startswith("spell_slot_")
    }
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert _sources(state) == []
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="No active paid"):
        _use(state)
    assert state == before


def test_actual_mote_sunlight30_bright60_dim_current_anchor_plane_and_stale_body():
    state, data, params = _case()
    _cast(state, data, params)
    for point, plane, light, sunlight in (
        ([30, 0, 8], "material", "bright", True),
        ([60, 0, 8], "material", "dim", True),
        ([61, 0, 8], "material", "darkness", False),
        ([0, 0, 8], "ethereal", "darkness", False),
    ):
        result = _run(
            "resolve_srd_information_light", state, point=point, plane=plane, ambient="darkness"
        ).result["context"]
        assert (result["_information_light"], result["_information_sunlight"]) == (light, sunlight)
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero")
    row["position"] = [20, 0, 0]
    result = _run(
        "resolve_srd_information_light",
        state,
        point=[80, 0, 8],
        plane="material",
        ambient="darkness",
    ).result["context"]
    assert result["_information_light"] == "dim" and result["_information_sunlight"] is True
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero")
    row["height"] = 10
    with pytest.raises(RulesetPreconditionRejected, match="Changed caster body"):
        _run(
            "resolve_srd_information_light",
            state,
            point=[20, 0, 8],
            plane="material",
            ambient="darkness",
        )


@pytest.mark.parametrize(
    "changes",
    [{"sunbeam_height": 0}, {"sunbeam_mote_position": [0, 0, 1]}, {"sunbeam_direction": [0, 0, 1]}],
)
def test_unsupported_unprinted_geometry_rejects_atomic_paid_cast_before_any_roll(
    monkeypatch, changes
):
    state, data, params = _case()
    before, rolls = deepcopy(state), Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state, data, params | changes)
    assert state == before and rolls.sides == []


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    names = PUBLIC_TOOLS | {"cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"}
    for name in names:
        record["tools"] = [row for row in record["tools"] if row["name"] != name]
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "input_schema": _tool_schema(runtime["tools"][name]),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
        )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "sunbeam-retained-source-line",
            "kind": "ruleset_runtime",
            "family": "spells",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": sorted(names),
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["paid-sunbeam-source-line-reopen-magic-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_actual_paid_archived_initial_line_reopens_later_magic_and_exact_retries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    database, receipts = tmp_path / "sunbeam.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "sunbeam", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "advance_srd_lasting_time", elapsed_rounds=1))
        receipts.append(_act(owner, "use_srd_sunbeam", caster_state="hero", sunbeam_height=5))
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces


def test_repeat_emission_unchanged_native_roll_packet_and_measured_later_cost(monkeypatch):
    runtime, encoded = _runtime()
    rebound = yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(rebound["tools"], changed)
    rebound["tools"].update(changed)
    assert rebound == runtime
    state, data, params = _case()
    _cast(state, data, params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    measured = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not measured or measured[-1] is not budget:
            measured.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    assert _use(state)["result_card"]
    assert all(b.max_operations == 10000 for b in measured)
    print("Sunbeam later2-target operations:", max(b.operations for b in measured))
