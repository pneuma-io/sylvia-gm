"""Vampiric Touch consumes a paid source, real turns, attack dice and Necrotic damage."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_damage_type_receipts import bind_runtime as bind_type_receipts
from scripts.srd_full.srd_retained_spell_attacks import (
    ATTACK_EXPORTS,
    PROFILE,
    PUBLIC_TOOLS,
    extend_runtime,
    schema,
)
from scripts.srd_full.srd_timed_actor_defenses import _call, _when

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE, _mage_spec, _source_runtime
from tests.srd_full.test_srd_spell_illumination import _case as _source_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall


@lru_cache
def _runtime():
    parent, encoded = _source_runtime()
    text = bind_type_receipts(extend_runtime(encoded.decode()))
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"]["vampiric_touch"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["retained_spell_attack"] = (
        schema()
    )
    for name in ("begin_srd_spell_cast_internal", "begin_srd_spell_action"):
        next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ].append("vampiric_touch")
    # Root owns this paid family join; this proof composes only the proposed narrow call.
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = runtime["tools"][name]
        next(p for p in cast["parameters"] if p["name"] == "spell")["enum_values"].append(
            "vampiric_touch"
        )
        branch = _when(
            "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
            "_spell_definition.family == 'retained_spell_attack'",
            [
                _call(
                    "begin_srd_spell_source_instance",
                    {
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                    },
                    ["_spell_source_cast_id"],
                ),
                _call(
                    "cast_srd_vampiric_touch",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "target": "{_target_id}",
                        "slot_level": "{_slot_level}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                        "casting_ability": "{_casting_profile.ability}",
                        "concentration_save_modifier": (
                            "{_casting_profile.concentration_save_modifier}"
                        ),
                    },
                    ATTACK_EXPORTS + ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        )
        index = next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        )
        cast["effects"].insert(index, branch)
    assert parent["tools"]["d20_test"] == runtime["tools"]["d20_test"]
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
            input_schema=owned_payload(_tool_schema(runtime["tools"][name])),
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
    monkeypatch.setattr(dice, "_rng", Random(0))
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


class Rolls:
    def __init__(self, attack=10, damage=3, mirror=1):
        self.attack, self.damage, self.mirror = attack, damage, mirror
        self.sides = []

    def randint(self, _low, high):
        self.sides.append(high)
        return self.attack if high == 20 else self.damage if high == 6 else 1


def _case(slot=3):
    state, data, params = _source_case("vampiric_touch")
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(hp=20, max_hp=100, temp_hp=0)
    entities["guard"].update(hp=1000, max_hp=1000, temp_hp=0, armor_class=1)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for row in scene["entities"]:
        if row["id"] != "hero" and row["id"] != "guard":
            row["position"] = [100, 100, 0]
        row["metadata"]["lighting"] = "bright"
    params["slot_resource"] = f"spell_slot_{slot}"
    return state, data, params


def _cast(state, data, params):
    return _run("cast_spell", state, participant_data=data, **params).result["context"]


def _later(state, data, target="guard"):
    return _run(
        "use_srd_vampiric_touch",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        target=target,
    ).result


@pytest.mark.parametrize("slot", [3, 5, 9])
def test_actual_paid_initial_attack_and_later_clock_uses_do_not_recast_or_spend_slot(
    monkeypatch, slot
):
    state, data, params = _case(slot)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    initial = _cast(state, data, params)
    assert initial["_retained_attack_kind"] == "hit"
    assert len(initial["_retained_damage"]["results"]) == slot
    assert initial["_retained_necrotic_damage"] == slot * 3
    assert initial["_retained_healing_applied"] == slot * 3 // 2
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    record = deepcopy(
        next(
            r
            for r in state[f"{SRD}.state"]["turn_effects"]
            if r.get("kind") == "retained_spell_attack"
        )
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="real exploration clock"):
        _later(state, data)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    state = json.loads(json.dumps(state))
    result = _later(state, data)
    context = result["context"]
    assert context["_retained_source_id"] == record["source_id"]
    assert len(context["_retained_damage"]["results"]) == slot
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert rolls.sides.count(20) == 2
    rows = {r["label"]: r["value"] for r in result["result_card"]["summary"]}
    assert rows["HP restored"] == context["_retained_healing_applied"]
    assert rows["Necrotic damage"] == context["_retained_necrotic_damage"]


@pytest.mark.parametrize(
    "mode", ["miss", "critical", "resisted", "immune", "temp", "blocked_healing"]
)
def test_actual_roll_typed_mitigation_temp_hp_and_healing_block(monkeypatch, mode):
    state, data, params = _case()
    target = state[f"{SRD}.entities"]["guard"]
    caster = state[f"{SRD}.entities"]["hero"]
    if mode == "resisted":
        target["damage_resistances"] = ["necrotic"]
    if mode == "immune":
        target["damage_immunities"] = ["necrotic"]
    if mode == "temp":
        target["temp_hp"] = 100
    if mode == "blocked_healing":
        caster["hit_point_recovery_blocked"] = True
    rolls = Rolls(attack=1 if mode == "miss" else 20 if mode == "critical" else 10)
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _cast(state, data, params)
    expected = (
        0
        if mode in {"miss", "immune"}
        else 18
        if mode == "critical"
        else 4
        if mode == "resisted"
        else 9
    )
    assert context["_retained_necrotic_damage"] == expected
    assert context["_retained_healing_due"] == expected // 2
    assert context["_retained_healing_applied"] == (
        0 if mode == "blocked_healing" else expected // 2
    )
    assert rolls.sides == [20] + (
        [6] * 6 if mode == "critical" else [] if mode == "miss" else [6] * 3
    )


@pytest.mark.parametrize("boundary", ["reach", "plane", "wall", "expiry", "concentration"])
def test_current_source_and_geometry_reject_atomic_later_use(boundary):
    state, data, params = _case()
    _cast(state, data, params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    guard = next(r for r in scene["entities"] if r["id"] == "guard")
    if boundary == "reach":
        guard["position"] = [5.01, 0, 0]
    if boundary == "plane":
        guard["metadata"]["plane"] = "ethereal"
    if boundary == "wall":
        wall = _wall(1)
        wall["volume"] = {"kind": "box", "min": [2, -10, -10], "max": [3, 10, 10]}
        scene["geometry"] = [wall]
    if boundary == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    if boundary == "concentration":
        _run("end_concentration", state, caster_state="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _later(state, data)
    assert state == before


def test_public_intent_and_repeated_emission_are_bounded():
    runtime, encoded = _runtime()
    assert extend_runtime(encoded.decode()) == encoded.decode()
    assert {"use_srd_vampiric_touch"} == PUBLIC_TOOLS
    assert set(_tool_schema(runtime["tools"]["use_srd_vampiric_touch"])["properties"]) == {
        "caster",
        "caster_state",
        "target",
        "lasting_state",
    }


def test_each_real_caster_turn_has_only_one_later_magic_use_even_with_extra_budget():
    state, data, params = _case()
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, current_actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    _cast(state, data, params)
    assert state[f"{SRD}.combat"]["action_available"] is False
    combat = state[f"{SRD}.combat"]
    combat.update(action_available=True, normal_action_spent=False)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="each caster turn"):
        _later(state, data)
    assert state == before
    _run("end_combat_turn", state)
    _run("begin_combat_turn", state, actor="hero")
    assert _later(state, data)["context"]["_retained_damage_applied"] > 0
    assert state[f"{SRD}.combat"]["action_available"] is False


def test_actual_source_retirement_and_refresh_preserve_other_rows_and_suppression():
    state, data, params = _case()
    context = _cast(state, data, params)
    lasting = state[f"{SRD}.state"]
    source = next(r for r in lasting["turn_effects"] if r.get("kind") == "retained_spell_attack")
    source["suppressed"] = True
    _run("refresh_srd_retained_spell_attacks", state)
    assert state[f"{SRD}.state"]["has_retained_spell_attacks"] is True
    assert any(
        r.get("source_id") == context["_retained_source_id"]
        for r in state[f"{SRD}.state"]["turn_effects"]
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _later(state, data)
    assert state == before
    lasting = state[f"{SRD}.state"]
    other = {"kind": "independent_synthetic_source", "spell": "light", "expires_tick": 100}
    lasting["turn_effects"].append(deepcopy(other))
    _run("retire_srd_retained_spell_attack_source", state, source_id=context["_retained_source_id"])
    assert state[f"{SRD}.state"]["has_retained_spell_attacks"] is False
    assert other in state[f"{SRD}.state"]["turn_effects"]


def test_source_caster_current_statistics_and_actual_reach_are_read_at_later_use():
    state, data, params = _case()
    _cast(state, data, params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    data["abilities"]["intelligence"] = 20
    state[f"{SRD}.entities"]["hero"]["melee_reach"] = 10
    next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "guard")[
        "position"
    ] = [10, 0, 0]
    context = _later(state, data)["context"]
    assert context["_retained_attack_total"] - context["_retained_attack"]["total"] == 11


def test_actual_mirror_diversion_precedes_necrotic_dice_and_prevents_healing(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    spec = _mage_spec()
    _run("prepare_srd_creature", state, **spec)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(r for r in scene["entities"] if r["id"] == "guard")["position"] = [100, 0, 0]
    scene["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    _run(
        "cast_spell_outside_combat",
        state,
        spell="mirror_image",
        caster_state="mage",
        verbal_component_available=True,
        somatic_component_available=True,
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    rolls = Rolls(attack=20, damage=6)
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _later(state, data, target="mage")["context"]
    assert context["_mirror_redirected"] is True
    assert context["_retained_necrotic_damage"] == context["_retained_healing_applied"] == 0
    assert rolls.sides == [20, 6, 6, 6]


def test_paid_and_later_scalar_actions_keep_the_unchanged_operation_cap(monkeypatch):
    budgets = []
    actual = ExecutionBudget.__post_init__

    def collect(self):
        actual(self)
        budgets.append(self)

    monkeypatch.setattr(ExecutionBudget, "__post_init__", collect)
    state, data, params = _case()
    _cast(state, data, params)
    assert max(b.operations for b in budgets) < 10000
    budgets.clear()
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    budgets.clear()
    _later(state, data)
    assert budgets and all(b.max_operations == 10000 for b in budgets)
    assert max(b.operations for b in budgets) < 10000
    print("Vampiric later scalar operations:", max(b.operations for b in budgets))


def test_real_melee_fire_shield_retaliation_keeps_fresh_caster_hp_card(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    spec = _mage_spec()
    spec["spells"].append({"spell": "fire_shield", "uses_per_day": 2})
    supplies = cards._spell_case("fire_shield")[0][f"{SRD}.entities"]["hero"]["spell_materials"]
    spec.update(inventory=[m["inventory_item"] for m in supplies], spell_materials=supplies)
    _run("prepare_srd_creature", state, **spec)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    _run(
        "cast_spell_outside_combat",
        state,
        spell="fire_shield",
        caster_state="mage",
        target="mage",
        origin_distance=0,
        actor_spell_choice="warm",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=cards._spell_case("fire_shield")[2]["material_item_ids"],
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    caster = state[f"{SRD}.entities"]["hero"]
    hp = caster["hp"]
    result = _later(state, data, target="mage")
    context = result["context"]
    caster = state[f"{SRD}.entities"]["hero"]
    assert context["_retained_healing_applied"] == 4
    assert caster["hp"] == hp + 4 - 2
    rows = {r["label"]: r["value"] for r in result["result_card"]["summary"]}
    assert rows["Caster HP after"] == caster["hp"]
    assert rolls.sides.count(8) == 2


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        if declaration["name"] in {
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
        }:
            declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    name = "use_srd_vampiric_touch"
    record["tools"] = [d for d in record["tools"] if d["name"] != name]
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
            "id": "vampiric-retained-attack",
            "kind": "ruleset_runtime",
            "family": "retained_spell_attack",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": [name],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["vampiric-paid-retained-public-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda r: r["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_actual_public_paid_cast_and_repeated_later_uses_reopen_without_reroll(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "vampiric.db"
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner,
            "vampiric_touch",
            target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0},
        )
        paid, paid_command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        assert "Necrotic" in str(paid.result["result_card"])
        saved = owner.snapshot("casting")
    attacks = []
    for _ in range(2):
        with AuthorityOwner.open(database, "casting-owner") as owner:
            assert owner.snapshot("casting") == saved
            assert owner.execute_action(paid_command) == paid
            with pytest.raises(MechanicsRejected, match="real exploration clock"):
                _act(
                    owner,
                    "use_srd_vampiric_touch",
                    caster="hero",
                    caster_state="hero",
                    target="guard",
                )
            assert owner.snapshot("casting") == saved
            _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
            result, command = _act(
                owner, "use_srd_vampiric_touch", caster="hero", caster_state="hero", target="guard"
            )
            assert result.status is ActionStatus.COMMITTED
            attacks.append((result, command))
            saved = owner.snapshot("casting")
            assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    assert rolls.sides.count(20) == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A retained attack durable retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll())
        assert owner.execute_action(paid_command) == paid
        for result, command in attacks:
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved
