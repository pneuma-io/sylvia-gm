"""Paid Dragon's Breath sources produce real recipient Cones, saves, typed damage and clocks."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_dragon_breath import PROFILE, PUBLIC_TOOLS, bind_runtime_consumers, schema
from scripts.srd_full.srd_retained_spell_attacks import _lists

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
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall


@lru_cache
def _runtime():
    parent, encoded = _source_runtime()
    runtime = deepcopy(parent)
    runtime["definitions"]["spells"]["dragons_breath"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["dragon_breath"] = schema()
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        parameter = next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")
        parameter["enum_values"].append("dragons_breath")
    # Root owns common-source admission. The proof adds this source to the existing
    # paid allocator's selected duration IDs, preserving its actual producer.
    for name in ("cast_spell", "cast_spell_outside_combat"):
        for sequence in _lists(runtime["tools"][name]["effects"]):
            for effect in sequence:
                if effect.get("set", {}).get("field") == "_spell_time_span_ids":
                    effect["set"]["value"].append("'dragons_breath'")
    text = bind_runtime_consumers(yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False))
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(runtime["tools"], changed)
    runtime["tools"].update(changed)
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
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
        self.save, self.damage = save, damage
        self.sides = []

    def randint(self, _low, high):
        self.sides.append(high)
        return self.save if high == 20 else self.damage if high == 6 else 1


def _case(slot=2, damage_type="fire", recipient="guard"):
    state, data, params = _source_case("dragons_breath")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    # Original caster and willing recipient touch; the recipient later breathes
    # from its own saved body, not the original caster's body.
    for row in scene["entities"]:
        row["metadata"]["lighting"] = "bright"
        if row["id"] not in {"hero", "guard"}:
            row["position"] = [100, 100, 0]
        if recipient == "hero" and row["id"] == "guard":
            row["position"] = [100, 100, 0]
        row["facing"] = [1, 0]
    scene["entities"].append(
        _entity(
            "victim",
            [15, 0, 0],
            participant_ref="victim",
            creature_type="humanoid",
            plane="material",
            lighting="bright",
        )
    )
    entities = state[f"{SRD}.entities"]
    entities["hero"]["creature_type"] = "humanoid"
    entities["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    entities["victim"] = deepcopy(entities["other"])
    entities["victim"].update(name="Victim", hp=1000, max_hp=1000, temp_hp=0)
    entities["victim"]["saving_throw_modifiers"]["dexterity"] = -30
    params.update(
        slot_resource=f"spell_slot_{slot}", target=recipient, selected_damage_type=damage_type
    )
    return state, data, params


def _cast(state, data, params):
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result


def _use(state, breather="guard", **choices):
    return _run("use_srd_dragon_breath", state, breather_state=breather, **choices).result


def _sources(state):
    return [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("kind") == "dragon_breath"]


@pytest.mark.parametrize("slot,damage_type", [(2, "fire"), (5, "cold")])
def test_paid_touch_source_and_recipient_magic_use_original_dc_slot_type(
    monkeypatch, slot, damage_type
):
    state, data, params = _case(slot, damage_type)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    paid = _cast(state, data, params)
    assert rolls.sides == []
    source = deepcopy(_sources(state)[0])
    assert source["target"] == "guard" and source["caster"] == "hero"
    assert source["slot_level"] == slot and source["damage_type"] == damage_type
    assert source["save_dc"] == 18 and source["expires_tick"] == source["created_tick"] + 20
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert paid["result_card"]["details"]
    # Current original caster's score cannot replace the paid source DC.
    data["abilities"]["intelligence"] = 8
    result = _use(state)
    ctx = result["context"]
    assert ctx["_srd_area_targets"] == ["victim"]
    assert ctx["_breath_save_dc"] == 18 and ctx["_breath_dice_count"] == slot + 1
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 1000 - (slot + 1) * 3
    assert rolls.sides == [6] * (slot + 1) + [20]
    assert _sources(state) == [source]
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert "failure" in result["result_card"]["details"][0]["value"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="real exploration clock"):
        _use(state)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _use(state)
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 1000 - 2 * (slot + 1) * 3


@pytest.mark.parametrize(
    "change,match",
    [
        ("unwilling", "willing"),
        ("range", "contact"),
        ("plane", "same-plane"),
    ],
)
def test_actual_touch_and_willing_rejections_are_atomic(change, match):
    state, data, params = _case()
    if change == "unwilling":
        params["target_willing"] = False
    elif change == "range":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [50, 0, 0]
    else:
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "ethereal"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=match):
        _cast(state, data, params)
    assert state == before


def test_recipient_current_body_plane_direction_and_success_half_resistance():
    state, data, params = _case()
    _cast(state, data, params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "guard")["position"] = [50, 0, 0]
    victim = next(row for row in scene["entities"] if row["id"] == "victim")
    victim["position"] = [40, 0, 0]
    entities = state[f"{SRD}.entities"]
    entities["victim"]["saving_throw_modifiers"]["dexterity"] = 30
    entities["victim"]["damage_resistances"] = ["fire"]
    result = _use(state, breath_direction=[-1, 0])
    assert result["context"]["_srd_area_targets"] == ["victim"]
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 998  # floor9/2=4, resistance=2
    assert "success" in result["result_card"]["details"][0]["value"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "victim")["metadata"]["plane"] = (
        "ethereal"
    )
    result = _use(state, breath_direction=[-1, 0])
    assert result["context"]["_srd_area_targets"] == []
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 998


def test_real_action_budget_expiry_and_exact_concentration_end():
    state, data, params = _case()
    _cast(state, data, params)
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )
    _use(state)
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="budget"):
        _use(state)
    assert state == before
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _sources(state) == [] and state[f"{SRD}.state"]["has_dragon_breath"] is False
    with pytest.raises(RulesetPreconditionRejected, match="No active"):
        _use(state)
    state, data, params = _case()
    _cast(state, data, params)
    _run("end_concentration", state, caster_state="hero")
    assert _sources(state) == [] and state[f"{SRD}.state"]["has_dragon_breath"] is False


def test_repeated_emission_native_roll_owner_and_actual_scalar_cost(monkeypatch):
    runtime, encoded = _runtime()
    repeated = yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(repeated["tools"], changed)
    repeated["tools"].update(changed)
    assert repeated == runtime
    state, data, params = _case()
    _cast(state, data, params)
    budgets = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    assert _use(state)["result_card"]
    assert all(b.max_operations == 10000 for b in budgets)
    print("Actual Dragon's Breath scalar operations:", max(b.operations for b in budgets))

    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(nodes(key) + nodes(child) for key, child in value.items())
        if isinstance(value, list):
            return 1 + sum(map(nodes, value))
        return 1

    print("Dragon's Breath expanded-node delta:", nodes(runtime) - nodes(_source_runtime()[0]))


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
            "id": "dragons-breath-retained-recipient-cone",
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
            "evidence": {"tests": ["paid-dragon-source-reopen-real-cone-repeat-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_actual_paid_archived_source_reopens_two_magic_actions_and_durable_retries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    database = tmp_path / "dragon.db"
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "dragons_breath")
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "use_srd_dragon_breath", breather_state="guard"))
        receipts.append(_act(owner, "advance_srd_lasting_time", elapsed_rounds=1))
        receipts.append(_act(owner, "use_srd_dragon_breath", breather_state="guard"))
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces


def test_real_breather_ray_penalty_once_before_shared_saves_preserves_raw_dice(monkeypatch):
    from tests.srd_full.test_srd_flame_blade_objects import _enfeeble

    state, data, params = _case(recipient="hero")
    _cast(state, data, params)
    _enfeeble(state, data)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _use(state, breather="hero")
    context = result["context"]
    assert context["_breath_damage_roll"]["total"] == context["_breath_raw_damage"] == 9
    assert context["_breath_damage_total"] == 8  # the genuine Ray d8 is1
    assert rolls.sides.count(8) == 1 and rolls.sides.count(6) == 3
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 992


def test_original_caster_ray_does_not_penalize_independent_recipient_game_roll(monkeypatch):
    from tests.srd_full.test_srd_flame_blade_objects import _enfeeble

    state, data, params = _case()
    _cast(state, data, params)
    _enfeeble(state, data)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _use(state)["context"]
    assert context["_breath_damage_total"] == context["_breath_raw_damage"] == 9
    assert rolls.sides == [6, 6, 6, 20]
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 991


def test_actual_uncategorized_physical_cover_rejects_before_action_roll_or_damage(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    source = deepcopy(_sources(state))
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1, "stone", -0.1, 0.1)]
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="categorical Cover"):
        _use(state)
    assert state == before and rolls.sides == []
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 1000
    assert _sources(state) == source


@pytest.mark.parametrize("damage_type", ["acid", "lightning", "poison"])
def test_selected_original_type_uses_actual_immunity_and_preserves_source(damage_type):
    state, data, params = _case(damage_type=damage_type)
    state[f"{SRD}.entities"]["victim"]["damage_immunities"] = [damage_type]
    _cast(state, data, params)
    source = deepcopy(_sources(state))
    result = _use(state)
    assert result["context"]["_breath_damage_type"] == damage_type
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 1000
    assert "Damage 0" in result["result_card"]["details"][0]["value"]
    assert _sources(state) == source


def test_paid_recast_retires_exact_old_recipient_and_keeps_new_source():
    state, data, params = _case()
    _cast(state, data, params)
    original = _sources(state)[0]["source_id"]
    _cast(
        state,
        data,
        params
        | {"target": "hero", "slot_resource": "spell_slot_5", "selected_damage_type": "acid"},
    )
    sources = _sources(state)
    assert len(sources) == 1 and sources[0]["source_id"] != original
    assert sources[0]["target"] == "hero" and sources[0]["slot_level"] == 5
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="No active"):
        _use(state, breather="guard")
    assert state == before and _sources(state) == sources
