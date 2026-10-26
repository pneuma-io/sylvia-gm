"""The retained Magic attack permits real Shield before unchanged Mirror and fire damage."""

import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_mirror_image_consumers import _produce, _sense_reach, _wrapper
from scripts.srd_full.srd_produce_flame_reactions import (
    COMPLETE_NODE,
    HURL_NODE,
    PUBLIC_TOOLS,
    _tools,
    bind_runtime_consumers,
)

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
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
from tests.srd_full.test_srd_produce_flame import _flame_case, _records
from tests.srd_full.test_srd_reaction_spells import _cast_parameters
from tests.srd_full.test_srd_spell_wave3_information import _entity

NATIVE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-produce-flame-reactions/input-1"))
SOURCE = NATIVE / "rulesets/dnd-srd-5.2.1"
ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _source_runtime():
    assert sha256((ROOT / "scripts/srd_mirror_image_consumers.py").read_bytes()).hexdigest() == (
        "055be0056bbef6b08685ebf1e96b0f70d27ab545bfe4f941a2147144f5e822f2"
    )
    encoded = (SOURCE / "runtime/ruleset.yaml").read_bytes()
    assert sha256(encoded).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    # Native267 predates this accepted final Mirror leaf. Borrow only its frozen owning
    # hit transform, never re-emit an old whole family or Reaction module in this proof.
    runtime["tools"]["resolve_srd_mirror_image_hit"] = _sense_reach(
        runtime["tools"]["resolve_srd_mirror_image_hit"]
    )
    runtime["tools"]["hurl_srd_produce_flame_node"] = _produce(
        runtime["tools"]["hurl_srd_produce_flame_node"]
    )
    runtime["tools"]["hurl_srd_produce_flame"], _ = _wrapper(
        runtime["tools"]["hurl_srd_produce_flame"], {"hurl_srd_produce_flame_node"}
    )
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _runtime():
    runtime = deepcopy(_source_runtime()[0])
    original_hurl = deepcopy(runtime["tools"]["hurl_srd_produce_flame"])
    runtime["tools"].update(_tools(runtime))
    assert runtime["tools"]["hurl_srd_produce_flame"] == original_hurl
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name, *, bound=True):
    runtime, encoded = _runtime() if bound else _source_runtime()
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
def _actual_owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


class _Rolls(Random):
    def __init__(self, attack=10, mirror=1, forbid_attack=False):
        super().__init__(0)
        self.attack, self.mirror, self.forbid_attack = attack, mirror, forbid_attack
        self.sides = []

    def randint(self, _a, b):
        assert not (self.forbid_attack and b == 20), "Saved attack D20 was repeated"
        self.sides.append(b)
        return self.attack if b == 20 else self.mirror if b == 6 else 1


def _mage_spec():
    return {
        "creature_id": "mage",
        "name": "Defensive Mage",
        "source": "Synthetic source: AC12, HP1000, Speed30, Int+3, attack+7, DC15.",
        "armor_class": 12,
        "hit_points": 1000,
        "speed": 30,
        "initiative_modifier": 0,
        "size_rank": 2,
        "constitution_save": 0,
        "strength_save": 0,
        "dexterity_save": 0,
        "wisdom_save": 0,
        "spellcasting_ability": "intelligence",
        "spellcasting_modifier": 3,
        "spell_attack_bonus": 7,
        "spell_save_dc": 15,
        "spells": [
            {"spell": "shield", "uses_per_day": 2},
            {"spell": "counterspell", "uses_per_day": 2},
            {"spell": "mirror_image", "uses_per_day": 2},
            {"spell": "invisibility", "uses_per_day": 2},
            {"spell": "greater_invisibility", "uses_per_day": 2},
        ],
    }


def _case(*, materials=False):
    state, data, params = _public_case()
    spec = _mage_spec()
    if materials:
        supplies = cards._spell_case("invisibility")[0][f"{SRD}.entities"]["hero"][
            "spell_materials"
        ]
        spec.update(inventory=[m["inventory_item"] for m in supplies], spell_materials=supplies)
    _run("prepare_srd_creature", state, **spec)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data


def _public_case(spell="produce_flame"):
    state, data, params = _flame_case(spell)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [30, 5, 0], participant_ref="mage", creature_type="humanoid")
    )
    return state, data, params


def _begin(state, data, **target):
    return _run(
        "begin_srd_produce_flame_attack",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        **target,
    )


def _finish(state, data, pending):
    return _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=pending
    )


def test_public_schema_accepts_only_same_hurl_intent_and_emission_is_idempotent():
    runtime, encoded = _runtime()
    assert {"begin_srd_produce_flame_attack"} == PUBLIC_TOOLS
    schema = _tool_schema(runtime["tools"]["begin_srd_produce_flame_attack"])
    assert schema == _tool_schema(runtime["tools"]["hurl_srd_produce_flame"])
    hidden = {
        p["name"]
        for p in runtime["tools"]["begin_srd_produce_flame_attack"]["parameters"]
        if p.get("model_hidden")
    }
    assert set(schema["properties"]) - hidden == {"caster", "caster_state", "target", "object_id"}
    assert bind_runtime_consumers(encoded.decode()) == encoded.decode()
    assert _tools(runtime) == {}
    assert HURL_NODE in runtime["tools"] and COMPLETE_NODE in runtime["tools"]
    assert runtime["tools"][HURL_NODE]["parameters"][-1] == {
        "name": "_produce_defer_hit",
        "type": "boolean",
        "required": False,
        "default": False,
        "model_hidden": True,
    }
    incomplete = deepcopy(runtime)
    del incomplete["tools"][COMPLETE_NODE]
    with pytest.raises(ValueError, match="Regenerate the settled hurl"):
        _tools(incomplete)


@pytest.mark.parametrize("attack", [1, 5, 20])
def test_direct_hurl_keeps_native_context_cards_dice_and_mutations(monkeypatch, attack):
    state, data = _case()
    parent_state = deepcopy(state)
    rolls = _Rolls(attack)
    monkeypatch.setattr(dice, "_rng", rolls)
    actual = _run(
        "hurl_srd_produce_flame",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        target="mage",
    )
    parent_rolls = _Rolls(attack)
    monkeypatch.setattr(dice, "_rng", parent_rolls)
    monkeypatch.setattr(budget, "_mechanic", lambda name: _mechanic(name, bound=False))
    parent = _run(
        "hurl_srd_produce_flame",
        parent_state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        target="mage",
    )
    assert actual.result == parent.result
    assert actual.mutations == parent.mutations
    assert state == parent_state and rolls.sides == parent_rolls.sides
    assert not state[f"{SRD}.state"].get("pending_srd_action_stack")


@pytest.mark.parametrize("attack,missed_after_shield", [(5, True), (20, False)])
def test_real_paid_flame_hit_saved_before_shield_then_completes_without_reroll(
    monkeypatch,
    attack,
    missed_after_shield,
):
    state, data = _case()
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
    )
    source = deepcopy(_records(state, "produce_flame"))
    rolls = _Rolls(attack)
    monkeypatch.setattr(dice, "_rng", rolls)
    begun = _begin(state, data, target="mage")
    pending = begun.result["context"]["_pending_action_id"]
    event = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][pending])
    assert event["kind"] == "spell_attack" and event["owner_kind"] == "produce_flame"
    assert event["stage"] == "spell_attack_reaction" and event["spell"] == "produce_flame"
    assert rolls.sides == [20]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert "Pending defensive Reaction" in str(begun.result["result_card"])
    with pytest.raises(RulesetPreconditionRejected, match="Counterspell needs"):
        _run("cast_srd_reaction_spell", state, **_cast_parameters("counterspell", pending))
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["shield"] == 1
    assert state[f"{SRD}.entities"]["mage"]["reaction_available"] is False
    state = json.loads(json.dumps(state))
    continuation = _Rolls(forbid_attack=True)
    monkeypatch.setattr(dice, "_rng", continuation)
    result = _finish(state, data, pending).result["context"]
    assert result["_produce_attack"] == event["attack_roll"]
    assert result["_produce_attack_total"] == event["attack_total"]
    assert result["_produce_armor_class"] == 17
    assert (result["_produce_attack_kind"] == "miss") is missed_after_shield
    assert bool(continuation.sides) is not missed_after_shield
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    assert _records(state, "produce_flame") == source
    with pytest.raises(RulesetPreconditionRejected, match="does not exist"):
        _finish(state, data, pending)


@pytest.mark.parametrize("mirror_face,diverted", [(6, True), (1, False)])
def test_saved_hit_runs_actual_mirror_only_after_defense_before_fire_dice(
    monkeypatch,
    mirror_face,
    diverted,
):
    state, data = _case()
    _run(
        "cast_spell_outside_combat",
        state,
        spell="mirror_image",
        caster_state="mage",
        verbal_component_available=True,
        somatic_component_available=True,
    )
    source = next(
        v
        for v in state[f"{SRD}.state"]["illusion_spell_sources"].values()
        if v["spell"] == "mirror_image" and v["installed"]
    )
    rolls = _Rolls(20, mirror_face)
    monkeypatch.setattr(dice, "_rng", rolls)
    pending = _begin(state, data, target="mage").result["context"]["_pending_action_id"]
    assert rolls.sides == [20]
    assert source["remaining_duplicates"] == 3
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    continuation = _Rolls(mirror=mirror_face, forbid_attack=True)
    monkeypatch.setattr(dice, "_rng", continuation)
    result = _finish(state, data, pending).result["context"]
    assert result["_mirror_redirected"] is diverted
    assert continuation.sides[:3] == [6, 6, 6]
    assert (8 in continuation.sides) is not diverted
    assert (result["_produce_damage_applied"] == 0) is diverted
    if diverted:
        assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000


def test_missed_and_prepared_object_attacks_finish_directly_without_a_pending_window(monkeypatch):
    state, data = _case()
    rolls = _Rolls(1)
    monkeypatch.setattr(dice, "_rng", rolls)
    miss = _begin(state, data, target="mage").result["context"]
    assert miss["_produce_attack_kind"] == "miss" and miss["_pending_action_id"] is None
    assert rolls.sides == [20]
    assert not state[f"{SRD}.state"].get("pending_srd_action_stack")
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="large",
        durability="resilient",
    )
    monkeypatch.setattr(dice, "_rng", _Rolls(20))
    obj = _begin(state, data, object_id="orb").result["context"]
    assert obj["_pending_action_id"] is None and obj["_produce_damage_applied"] > 0
    assert not state[f"{SRD}.state"].get("pending_srd_action_stack")
    assert _records(state, "produce_flame")


@pytest.mark.parametrize("spell", ["invisibility", "greater_invisibility"])
def test_begin_missed_actual_attack_roll_ends_only_ordinary_invisible(monkeypatch, spell):
    state, data = _case(materials=True)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero = next(e for e in scene["entities"] if e["id"] == "hero")
    mage = next(e for e in scene["entities"] if e["id"] == "mage")
    mage["position"] = [hero["position"][0] + 5, *hero["position"][1:]]
    _run(
        "cast_spell_outside_combat",
        state,
        spell=spell,
        caster_state="mage",
        target="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=[m["id"] for m in state[f"{SRD}.entities"]["mage"]["spell_materials"]],
    )
    assert "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]
    mage["position"] = [30, 5, 0]
    monkeypatch.setattr(dice, "_rng", _Rolls(1))
    result = _begin(state, data, target="mage").result["context"]
    assert result["_produce_attack_kind"] == "miss" and result["_pending_action_id"] is None
    assert ("invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]) is (
        spell == "greater_invisibility"
    )
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert _records(state, "produce_flame")


def test_accepted_attack_preserves_source_stats_after_source_expiry(monkeypatch):
    state, data = _case()
    monkeypatch.setattr(dice, "_rng", _Rolls(20))
    pending = _begin(state, data, target="mage").result["context"]["_pending_action_id"]
    event = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][pending])
    state[f"{SRD}.state"]["lasting_tick"] = 1201
    _run("refresh_srd_illumination_spells", state)
    assert not _records(state, "produce_flame")
    data["level"] = 1
    state[f"{SRD}.entities"]["hero"]["is_alive"] = False
    state[f"{SRD}.entities"]["hero"]["hp"] = 0
    monkeypatch.setattr(dice, "_rng", _Rolls(forbid_attack=True))
    result = _finish(state, data, pending).result["context"]
    assert (
        result["_produce_flame_source_instance"]
        == event["context"]["_produce_flame_source_instance"]
    )
    assert result["_produce_flame_dice_count"] == event["context"]["_produce_flame_dice_count"]
    assert len(result["_produce_damage"]["results"]) == 2 * result["_produce_flame_dice_count"]
    assert not _records(state, "produce_flame")


def test_saved_condition_critical_hit_uses_accepted_actual_distance_and_condition(monkeypatch):
    state, data = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero = next(e for e in scene["entities"] if e["id"] == "hero")
    mage = next(e for e in scene["entities"] if e["id"] == "mage")
    mage["position"] = [hero["position"][0] + 5, *hero["position"][1:]]
    state[f"{SRD}.entities"]["mage"]["conditions"] = ["paralyzed"]
    monkeypatch.setattr(dice, "_rng", _Rolls(5))
    pending = _begin(state, data, target="mage").result["context"]["_pending_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][pending]
    assert event["context"]["_condition_critical_hit"] is True
    assert event["attack_roll"]["total"] == 5 and event["attack_outcome"] == "critical_hit"
    state[f"{SRD}.entities"]["mage"]["conditions"] = []
    rolls = _Rolls(forbid_attack=True)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _finish(state, data, pending).result["context"]
    assert result["_produce_attack_kind"] == "critical_hit"
    assert len(result["_produce_damage"]["results"]) == 2 * result["_produce_flame_dice_count"]
    assert 20 not in rolls.sides


def test_begin_and_saved_mirror_completion_use_unchanged_operation_budget(monkeypatch):
    state, data = _case()
    _run(
        "cast_spell_outside_combat",
        state,
        spell="mirror_image",
        caster_state="mage",
        verbal_component_available=True,
        somatic_component_available=True,
    )
    budgets = []
    original = ExecutionBudget.consume_operation

    def observed(active):
        if not budgets or budgets[-1] is not active:
            budgets.append(active)
        return original(active)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", observed)
    monkeypatch.setattr(dice, "_rng", _Rolls(20))
    pending = _begin(state, data, target="mage").result["context"]["_pending_action_id"]
    begin_ops = budgets[-1].operations
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    monkeypatch.setattr(dice, "_rng", _Rolls(mirror=1, forbid_attack=True))
    _finish(state, data, pending)
    finish_ops = budgets[-1].operations
    assert all(b.max_operations == 10_000 for b in budgets)
    assert begin_ops < 10_000 and finish_ops < 10_000
    print(f"Produce Flame accepted begin={begin_ops}, saved Mirror completion={finish_ops} ops")


@lru_cache
def _archive():
    """Publish only the new public wrapper into an actual native package test closure."""
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        if declaration["name"] in runtime["tools"]:
            declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    name = "begin_srd_produce_flame_attack"
    assert not any(d["name"] == name for d in record["tools"])
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
            "id": "produce-flame-saved-defense",
            "kind": "ruleset_runtime",
            "family": "illumination_spell",
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
            "evidence": {"tests": ["produce-flame-saved-shield-public-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_actual_public_paid_flame_begin_shield_reopen_complete_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", _public_case)
    database = tmp_path / "produce-shield.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "produce_flame")
        prepared, _ = _act(owner, "prepare_srd_creature", **_mage_spec())
        assert prepared.status is ActionStatus.COMMITTED
        paid, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        monkeypatch.setattr(dice, "_rng", _Rolls(5))
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "begin_srd_produce_flame_attack",
                caster="hero",
                caster_state="hero",
                target="mage",
                hit=True,
            )
        begun, begin_command = _act(
            owner,
            "begin_srd_produce_flame_attack",
            caster="hero",
            caster_state="hero",
            target="mage",
        )
        assert begun.status is ActionStatus.COMMITTED, begun.result
        pending = begun.result["context"]["_pending_action_id"]
        shield, _ = _act(
            owner,
            "cast_srd_reaction_spell",
            **_cast_parameters("shield", pending),
        )
        assert shield.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        monkeypatch.setattr(dice, "_rng", _Rolls(forbid_attack=True))
        assert reopened.execute_action(begin_command) == begun
        finished, finish_command = _act(
            reopened, "complete_srd_pending_action", pending_action_id=pending
        )
        assert finished.status is ActionStatus.COMMITTED, finished.result
        assert finished.result["context"]["_produce_attack_kind"] == "miss"
        final = reopened.snapshot("casting")
        assert _records(final.state, "produce_flame")
        assert final.state[f"{SRD}.entities"]["mage"]["hp"] == 1000
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert reopened.execute_action(finish_command) == finished
        assert reopened.snapshot("casting") == final
