"""Paid bead retention, genuine caster-end growth and once-only saved Sphere detonations."""

import json
from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_delayed_blast_fireball import (
    DRAIN,
    PROFILE,
    PUBLIC_TOOLS,
    QUEUE,
    SPELL,
    bind_runtime_consumers,
    schema,
)
from scripts.srd_full.srd_retained_spell_attacks import _lists

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
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
from tests.srd_full.test_srd_sunbeam import Rolls


@lru_cache
def _runtime():
    parent = deepcopy(_source_runtime()[0])
    runtime = deepcopy(parent)
    runtime["definitions"]["spells"][SPELL] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"][SPELL] = schema()
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ].append(SPELL)
    for name in ("cast_spell", "cast_spell_outside_combat"):
        for sequence in _lists(runtime["tools"][name]["effects"]):
            for effect in sequence:
                if effect.get("set", {}).get("field") == "_spell_time_span_ids":
                    effect["set"]["value"].append("'" + SPELL + "'")
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
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case(slot=7):
    state, data, params = _source_case(SPELL)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        row["metadata"]["plane"] = "material"
        if row["id"] == "hero":
            row["position"] = [0, 0, 0]
        elif row["id"] == "guard":
            row["position"] = [30, 0, 0]
        else:
            row["position"] = [40, 2, 0]
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(creature_type="humanoid", turns_started=5)
    entities["guard"].update(hp=1000, max_hp=1000, temp_hp=0, creature_type="humanoid")
    entities["guard"]["saving_throw_modifiers"]["dexterity"] = -30
    entities["other"].update(hp=1000, max_hp=1000, temp_hp=0, creature_type="humanoid")
    entities["other"]["saving_throw_modifiers"]["dexterity"] = 30
    params.update(slot_resource=f"spell_slot_{slot}", delayed_blast_origin=[30, 5, 2])
    return state, data, params


def _sources(state):
    return [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("kind") == SPELL]


def _cast(state, data, params):
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result


def _event(state, source_id):
    return state[f"{SRD}.state"]["delayed_blast_detonations"][source_id]


@pytest.mark.parametrize("slot", [7, 9])
def test_paid_bead_no_initial_damage_then_one_shared_scaled_fire_dex_packet(monkeypatch, slot):
    state, data, params = _case(slot)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _cast(state, data, params)
    source = deepcopy(_sources(state)[0])
    assert source["origin"] == [30, 5, 2] and source["plane"] == "material"
    assert source["growth_dice"] == 0 and source["slot_level"] == slot
    assert rolls.sides == [] and state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    result = _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero").result
    count = 12 + slot - 7
    assert rolls.sides == [6] * count + [20, 20]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - count * 3
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000 - count * 3 // 2
    assert _sources(state) == [] and _event(state, source["source_id"])["status"] == "resolved"
    assert result["context"]["_dbf_damage_roll"]["results"] == [3] * count
    assert result["result_card"]


def test_growth_only_actual_creating_caster_end_once_and_no_exploration_growth():
    state, data, params = _case()
    _cast(state, data, params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _sources(state)[0]["growth_dice"] == 0
    state[f"{SRD}.combat"]["encounter_active"] = True
    for actor, phase, turn in (
        ("guard", "end", 1),
        ("hero", "start", 5),
        ("hero", "end", 5),
        ("hero", "end", 5),
    ):
        _run(
            "process_srd_turn_effects",
            state,
            lasting_state="state",
            boundary_actor=actor,
            boundary_phase=phase,
            boundary_turn=turn,
        )
    assert _sources(state)[0]["growth_dice"] == 1
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 6
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="hero",
        boundary_phase="end",
        boundary_turn=6,
    )
    assert _sources(state)[0]["growth_dice"] == 2
    result = _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero").result
    assert result["context"]["_dbf_damage_roll"]["results"] == [3] * 14


def test_source_expiry_queue_is_authentic_no_damage_or_range_recheck_and_once(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    source = deepcopy(_sources(state)[0])
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _sources(state) == [] and rolls.sides == []
    event = _event(state, source["source_id"])
    assert event["status"] == "queued" and event["record"] == source
    # The accepted ending survives both current range and the creating caster's death.
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero")
    row["position"] = [1000, 0, 0]
    row["metadata"]["plane"] = "ethereal"
    state[f"{SRD}.entities"]["hero"].update(hp=0, is_alive=False)
    _run(DRAIN, state)
    assert rolls.sides == [6] * 12 + [20, 20]
    before, faces = deepcopy(state), deepcopy(rolls.sides)
    _run(DRAIN, state)
    assert state == before and rolls.sides == faces
    assert _event(state, source["source_id"])["status"] == "resolved"


def test_wrong_plane_body_sphere_and_half_fire_resistance_from_actual_packet():
    state, data, params = _case()
    _cast(state, data, params)
    state[f"{SRD}.entities"]["guard"]["damage_resistances"] = ["fire"]
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "other")
    row["metadata"]["plane"] = "ethereal"
    _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 982
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000


def test_paid_ray_penalty_once_before_both_saves_preserves_shared_raw_roll(monkeypatch):
    from tests.srd_full.test_srd_flame_blade_objects import _enfeeble

    state, data, params = _case()
    _cast(state, data, params)
    _enfeeble(state, data, position=(80, 0, 0))

    class RayRolls(Rolls):
        def randint(self, low, high):
            if high == 8:
                self.sides.append(high)
                return 1
            return super().randint(low, high)

    rolls = RayRolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero").result[
        "context"
    ]
    assert context["_dbf_damage_roll"]["results"] == [3] * 12
    assert context["_dbf_raw_damage"] == 36 and context["_dbf_damage_total"] == 35
    assert rolls.sides == [6] * 12 + [8, 20, 20]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 965
    assert state[f"{SRD}.entities"]["other"]["hp"] == 983


def test_paid_point_cast_accepts_no_creature_target_and_authentic_unknown_queue_is_empty():
    state, data, params = _case()
    params.pop("target")
    _cast(state, data, params)
    assert _sources(state)[0]["origin"] == params["delayed_blast_origin"]
    before = deepcopy(state)
    _run(QUEUE, state, caster_state="hero", source_id="not-a-paid-source")
    assert state == before


def test_rejected_point_is_atomic_and_final_emission_preserves_actual_roll_packet():
    state, data, params = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="beyond150"):
        _cast(state, data, params | {"delayed_blast_origin": [200, 0, 0]})
    assert state == before
    runtime, encoded = _runtime()
    rebound = yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(rebound["tools"], changed)
    rebound["tools"].update(changed)
    assert rebound == runtime
    assert DRAIN not in str(runtime["tools"][QUEUE]["effects"])


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
            "id": "delayed-blast-bead-source-detonation",
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
            "evidence": {"tests": ["paid-dbfireball-source-end-detonation-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_paid_archived_bead_reopens_authentic_detonation_retries_without_cost_or_dice(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    measured = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not measured or measured[-1] is not budget:
            measured.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "dbf.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner,
            SPELL,
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved = owner.snapshot("casting")
        assert rolls.sides == []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "detonate_srd_delayed_blast_fireball", caster_state="hero"))
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(b.max_operations == 10000 for b in measured)
    print("DBF paid/source-end2-target max operations:", max(b.operations for b in measured))
