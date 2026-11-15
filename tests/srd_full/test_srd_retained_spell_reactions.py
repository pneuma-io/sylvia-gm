"""Real saved Vampiric hits retain one roll, cost, source, typed healing and cast tail."""

import json
from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from jsonschema import Draft202012Validator
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_retained_spell_reactions import (
    ATTACK,
    CAST_TAIL,
    CHOICES,
    COMPLETE,
    MARKER,
    _rewrite_choices,
    _tools,
    bind_runtime,
    restore_runtime,
)

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_retained_spell_attacks as source
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import _mage_spec
from tests.srd_full.test_srd_reaction_spells import _cast_parameters
from tests.srd_full.test_srd_spell_wave3_information import _entity

BASE_RUNTIME = source._runtime


@lru_cache
def _runtime(bound=True):
    runtime = deepcopy(BASE_RUNTIME()[0])
    # Root's final selector join is newer than native267; retain its genuine private
    # completion ABI while admitting the same new source key already in paid cast.
    spell = next(
        p
        for p in runtime["tools"]["complete_srd_spell_attack_consequences"]["parameters"]
        if p["name"] == "spell"
    )
    if "vampiric_touch" not in spell["enum_values"]:
        spell["enum_values"].append("vampiric_touch")
    if bound:
        runtime["tools"].update(_tools(runtime))
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name, bound=True):
    runtime, encoded = _runtime(bound)
    return package_runtime.materialize_package_mechanic(
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
    monkeypatch.setattr(dice, "_rng", source.Rolls())
    original = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print(args[0], result.error, result.failure_kind)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case(mage_spec=None):
    state, data, params = source._case()
    _run("prepare_srd_creature", state, **(mage_spec or _mage_spec()))
    state[f"{SRD}.entities"]["mage"]["temp_hp"] = 10
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero = next(e for e in scene["entities"] if e["id"] == "hero")
    scene["entities"].append(
        _entity(
            "mage",
            [hero["position"][0] + 5, hero["position"][1], hero["position"][2]],
            participant_ref="mage",
            creature_type="humanoid",
        )
    )
    params["target"] = "mage"
    return state, data, params


def _finish(state, data, pending):
    return _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=pending
    ).result["context"]


def _state(state):
    return state[f"{SRD}.state"]


def test_shared_tail_retains_exact_ordered_effects_public_schemas_and_inverse():
    original, _ = _runtime(False)
    runtime, encoded = _runtime()
    assert bind_runtime(encoded.decode()) == encoded.decode()
    restored = yaml.load(restore_runtime(encoded.decode()), Loader=yaml.CSafeLoader)
    assert restored == original
    split = next(
        i
        for i, e in enumerate(original["tools"]["cast_spell"]["effects"])
        if e.get("set", {}).get("field") == "_card_slots_after"
    )
    helper = runtime["tools"][CAST_TAIL]
    assert (
        _rewrite_choices(
            helper["effects"][2:], set(helper["metadata"][MARKER + "_choices"]), inverse=True
        )
        == original["tools"]["cast_spell"]["effects"][split:]
    )
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "use_srd_vampiric_touch",
        "complete_srd_pending_action",
    ):
        assert _tool_schema(runtime["tools"][name]) == _tool_schema(original["tools"][name])
    assert ATTACK in runtime["tools"] and COMPLETE in runtime["tools"]


def test_ordinary_paid_cast_keeps_parent_state_card_dice_and_nonprivate_context(monkeypatch):
    state, data, params = source._case()
    parent = deepcopy(state)
    actual_rolls = source.Rolls()
    monkeypatch.setattr(dice, "_rng", actual_rolls)
    actual = _run("cast_spell", state, participant_data=data, **params).result
    parent_rolls = source.Rolls()
    monkeypatch.setattr(dice, "_rng", parent_rolls)
    monkeypatch.setattr(budget, "_mechanic", lambda name: _mechanic(name, False))
    expected = _run("cast_spell", parent, participant_data=data, **params).result
    assert state == parent
    assert actual["result_card"] == expected["result_card"]
    assert actual_rolls.sides == parent_rolls.sides
    assert {k: v for k, v in actual["context"].items() if not k.startswith("_")} == {
        k: v for k, v in expected["context"].items() if not k.startswith("_")
    }


def test_choice_capsule_uses_only_admitted_nonnull_values_and_preserves_literal_references():
    original, _ = _runtime(False)
    runtime, _ = _runtime()
    helper = runtime["tools"][CAST_TAIL]
    choices = set(helper["metadata"][MARKER + "_choices"])
    public = _tool_schema(original["tools"]["cast_spell"])
    assert choices
    for name in choices:
        assert not Draft202012Validator(public["properties"][name]).is_valid(None)
    kept = {p["name"] for p in helper["parameters"]}
    for parameter in original["tools"]["cast_spell"]["parameters"]:
        name = parameter["name"]
        if parameter.get("resolve") in {"entity", "participant"}:
            assert name in kept
        if name in choices:
            assert not parameter.get("from_state") and not parameter.get("model_hidden")
            assert parameter.get("required", True) or parameter.get("default") is not None

    # A choice is a read-only value capsule. Mutating a list parameter would need
    # its original alias owner rather than a copied private map.
    def destinations(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"field", "list"} and isinstance(child, str):
                    yield child
                yield from destinations(child)
        elif isinstance(value, list):
            for child in value:
                yield from destinations(child)

    assert not choices.intersection(destinations(original["tools"]["cast_spell"]["effects"]))
    sample = [
        {"condition": "spell == 'spell' and _record.spell == spell"},
        {"call": "spell", "params": {"reason": "spell", "chosen": "{spell}"}},
        {"set": {"field": "_line", "value": "'spell {spell}'"}},
    ]
    qualified = _rewrite_choices(sample, {"spell"})
    assert qualified[0]["condition"] == (
        CHOICES + ".spell == 'spell' and _record.spell == " + CHOICES + ".spell"
    )
    assert qualified[1]["call"] == qualified[1]["params"]["reason"] == "spell"
    assert qualified[1]["params"]["chosen"] == "{" + CHOICES + ".spell}"
    assert _rewrite_choices(qualified, {"spell"}, inverse=True) == sample


@pytest.mark.parametrize("initial", [True, False])
@pytest.mark.parametrize("attack", [5, 20])
def test_actual_initial_and_later_shield_reopen_keep_one_roll_cost_and_typed_heal(
    monkeypatch, initial, attack
):
    state, data, params = _case()
    if not initial:
        source._cast(state, data, params)
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
        state[f"{SRD}.entities"]["hero"].update(hp=20, temp_hp=0)
    before = deepcopy(state)
    rolls = source.Rolls(attack=attack)
    monkeypatch.setattr(dice, "_rng", rolls)
    if initial:
        pending = _run("begin_srd_spell_action", state, participant_data=data, **params).result[
            "context"
        ]["_pending_action_id"]
        serial = _state(state)["pending_srd_action_sequence"]
        _finish(state, data, pending)
        assert _state(state)["pending_srd_action_sequence"] == serial
    else:
        pending = source._later(state, data, "mage")["context"]["_pending_action_id"]
    event = deepcopy(_state(state)["pending_srd_actions"][pending])
    assert event["kind"] == "spell_attack" and event["stage"] == "spell_attack_reaction"
    assert event["retained_initial"] is initial
    assert rolls.sides == [20]
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 20
    assert state[f"{SRD}.entities"]["mage"]["hp"] == before[f"{SRD}.entities"]["mage"]["hp"]
    source_records = deepcopy(
        [r for r in _state(state)["turn_effects"] if r.get("kind") == "retained_spell_attack"]
    )
    slots = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["shield"] == 1
    state = json.loads(json.dumps(state))
    continuation = source.Rolls(attack=1)
    monkeypatch.setattr(dice, "_rng", continuation)
    result = _finish(state, data, pending)
    assert 20 not in continuation.sides
    assert result["_retained_attack"] == event["attack_roll"]
    assert result["_retained_armor_class"] == 17
    assert result["_retained_attack_kind"] == ("critical_hit" if attack == 20 else "miss")
    assert result["_retained_healing_applied"] == result["_retained_necrotic_damage"] // 2
    assert result["_retained_necrotic_damage"] == (18 if attack == 20 else 0)
    target_before = before[f"{SRD}.entities"]["mage"]
    target_after = state[f"{SRD}.entities"]["mage"]
    assert (
        target_before["hp"]
        + target_before["temp_hp"]
        - target_after["hp"]
        - target_after["temp_hp"]
        == result["_retained_necrotic_damage"]
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"] == slots
    assert [
        r for r in _state(state)["turn_effects"] if r.get("kind") == "retained_spell_attack"
    ] == source_records
    assert not _state(state)["pending_srd_action_stack"]
    final = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="does not exist"):
        _finish(state, data, pending)
    assert state == final


def test_actual_mirror_is_rolled_only_after_initial_shield_and_prevents_damage_healing(monkeypatch):
    state, data, params = _case()
    _run(
        "cast_spell_outside_combat",
        state,
        spell="mirror_image",
        caster_state="mage",
        verbal_component_available=True,
        somatic_component_available=True,
    )
    record = next(
        r
        for r in _state(state)["illusion_spell_sources"].values()
        if r["spell"] == "mirror_image" and r["installed"]
    )
    rolls = source.Rolls(attack=20)
    monkeypatch.setattr(dice, "_rng", rolls)
    pending = _run("begin_srd_spell_action", state, participant_data=data, **params).result[
        "context"
    ]["_pending_action_id"]
    _finish(state, data, pending)
    assert rolls.sides == [20] and record["remaining_duplicates"] == 3
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    continued = source.Rolls(attack=1, damage=6)
    monkeypatch.setattr(dice, "_rng", continued)
    final = _finish(state, data, pending)
    assert final["_mirror_redirected"] is True
    assert continued.sides == [6, 6, 6]
    assert final["_retained_necrotic_damage"] == final["_retained_healing_applied"] == 0
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 20


@pytest.mark.parametrize("initial", [True, False])
def test_actual_fire_shield_waits_for_saved_hit_and_runs_once_after_typed_healing(
    monkeypatch, initial
):
    supplies, _, supplied = cards._spell_case("fire_shield")
    materials = supplies[f"{SRD}.entities"]["hero"]["spell_materials"]
    spec = _mage_spec()
    spec.update(
        creature_hp=1000,
        spellcasting_ability="intelligence",
        casting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        inventory=[m["inventory_item"] for m in materials],
        spell_materials=materials,
    )
    spec["spells"].append({"spell": "fire_shield", "uses_per_day": 2})
    state, data, params = _case(spec)
    if not initial:
        source._cast(state, data, params)
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
        material_item_ids=supplied["material_item_ids"],
    )
    if not initial:
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    state[f"{SRD}.entities"]["hero"].update(hp=20, temp_hp=0)
    before = deepcopy(state)
    rolls = source.Rolls(attack=20)
    monkeypatch.setattr(dice, "_rng", rolls)
    if initial:
        pending = _run("begin_srd_spell_action", state, participant_data=data, **params).result[
            "context"
        ]["_pending_action_id"]
        _finish(state, data, pending)
    else:
        pending = source._later(state, data, "mage")["context"]["_pending_action_id"]
    assert rolls.sides == [20]
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 20
    assert state[f"{SRD}.entities"]["mage"]["hp"] == before[f"{SRD}.entities"]["mage"]["hp"]
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    continued = source.Rolls(attack=1)
    monkeypatch.setattr(dice, "_rng", continued)
    d20_calls = []
    execute = package_runtime.execute_tool

    def record_d20(name, parameters, *args, **kwargs):
        if name == "d20_test":
            d20_calls.append(deepcopy(parameters))
        return execute(name, parameters, *args, **kwargs)

    monkeypatch.setattr(package_runtime, "execute_tool", record_d20)
    result = _finish(state, data, pending)
    assert result["_retained_attack_kind"] == "critical_hit"
    assert result["_retained_necrotic_damage"] == 18
    assert result["_retained_healing_applied"] == 9
    assert continued.sides.count(8) == 2 and continued.sides.count(20) == 1
    assert len(d20_calls) == 1
    assert d20_calls[0]["kind"] == "saving_throw"
    assert d20_calls[0]["ability"] == "constitution"
    assert d20_calls[0]["actor_state"] == "hero"
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 20 + 9 - 2
    assert result["_retained_caster_hp_after"] == 27
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    final = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="does not exist"):
        _finish(state, data, pending)
    assert state == final


def test_actual_public_initial_and_later_shield_completion_reopen_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "_runtime", _runtime)
    source._archive.cache_clear()
    monkeypatch.setattr(public_casting, "bundled_srd_archive", source._archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "retained-shield.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "vampiric_touch")
        monkeypatch.setattr(dice, "_rng", source.Rolls(attack=20))
        begun, begin_command = _act(owner, "begin_srd_spell_action", **params)
        pending = begun.result["context"]["_pending_action_id"]
        accepted, accepted_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=pending
        )
        assert accepted.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _state(saved.state)["pending_srd_actions"][pending]["retained_initial"] is True
        assert owner.execute_action(begin_command) == begun
        assert owner.execute_action(accepted_command) == accepted
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        _act(owner, "cast_srd_reaction_spell", **_cast_parameters("shield", pending))
        rolls = source.Rolls(attack=1)
        monkeypatch.setattr(dice, "_rng", rolls)
        finished, command = _act(owner, "complete_srd_pending_action", pending_action_id=pending)
        assert finished.status is ActionStatus.COMMITTED
        assert 20 not in rolls.sides
        assert "HP restored" in str(finished.result["result_card"])
        settled = owner.snapshot("casting")
        assert owner.execute_action(command) == finished
        assert owner.snapshot("casting") == settled
        _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        monkeypatch.setattr(dice, "_rng", source.Rolls(attack=20))
        later, _ = _act(
            owner, "use_srd_vampiric_touch", caster="hero", caster_state="hero", target="mage"
        )
        later_id = later.result["context"]["_pending_action_id"]
        later_saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == later_saved
        # The earlier Shield still supplies its real AC; this window can be completed
        # without granting a second Reaction to the same actual caster.
        rolls = source.Rolls(attack=1)
        monkeypatch.setattr(dice, "_rng", rolls)
        later_finished, command = _act(
            owner, "complete_srd_pending_action", pending_action_id=later_id
        )
        assert later_finished.status is ActionStatus.COMMITTED and 20 not in rolls.sides
        assert owner.execute_action(command) == later_finished
        assert not _state(owner.snapshot("casting").state)["pending_srd_action_stack"]
