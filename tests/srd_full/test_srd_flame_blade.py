"""Paid Flame Blade sources occupy actual free hands and power genuine later Magic attacks."""

import json
from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_flame_blade import PROFILE, PUBLIC_TOOLS, bind_runtime_consumers
from scripts.srd_full.srd_runtime_defaults import restore_tool_defaults
from scripts.srd_full.srd_scene_hand_occupancy import extend_runtime as emit_hands
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

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
from tests import test_srd_retained_spell_attack_consumers as retained
from tests import test_srd_retained_spell_attacks as attacks
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE, _mage_spec
from tests.srd_full.test_srd_spell_illumination import _case as _source_case
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    runtime = deepcopy(retained._runtime()[0])
    runtime["definitions"]["spells"]["flame_blade"] = PROFILE
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        parameter = next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")
        if "flame_blade" not in parameter["enum_values"]:
            parameter["enum_values"].append("flame_blade")
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = bind_runtime_consumers(emit_hands(text))
    runtime = _load_runtime(text)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(runtime["tools"], changed)
    runtime["tools"].update(changed)
    expected = restore_tool_defaults(deepcopy(retained._runtime()[0]))
    assert runtime["tools"]["d20_test"] == expected["tools"]["d20_test"]
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
    monkeypatch.setattr(dice, "_rng", attacks.Rolls())
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case(slot=2):
    state, data, params = _source_case("flame_blade")
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0, armor_class=1)
    state[f"{SRD}.entities"]["hero"].update(hp=100, max_hp=100, temp_hp=0)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        row["metadata"]["lighting"] = "bright"
    params.update(slot_resource=f"spell_slot_{slot}", flame_blade_hand=0)
    params.pop("target", None)
    _run("record_srd_control_held_items", state, target_state="hero", held_items=[])
    _run("record_srd_scene_hand_occupancy", state, actor_state="hero", hand_capacity=2, holdings=[])
    return state, data, params


def _source(state):
    return next(r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "flame_blade")


def _cast(state, data, params):
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result


def _use(state, data, target="guard"):
    return _run(
        "use_srd_flame_blade",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        target=target,
    ).result


@pytest.mark.parametrize("slot", [2, 5])
def test_real_paid_bonus_action_has_no_attack_and_retained_magic_uses_original_slot(
    monkeypatch, slot
):
    state, data, params = _case(slot)
    rolls = attacks.Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    paid = _cast(state, data, params)
    assert rolls.sides == []
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    record = deepcopy(_source(state))
    assert record["expires_tick"] == 200 and record["hand_index"] == 0
    assert (
        _run("qualify_srd_scene_hand_occupancy", state, actor_state="hero").result["context"][
            "_scene_free_hand_count"
        ]
        == 1
    )
    result = _use(state, data)
    context = result["context"]
    assert context["_flame_attack_kind"] == "hit"
    assert context["_flame_damage_rolled"] == 3 * (slot + 1) + 4
    assert context["_flame_damage_applied"] == context["_flame_damage_rolled"]
    assert rolls.sides == [20] + [6] * (slot + 1)
    assert _source(state) == record
    assert paid["result_card"] and result["result_card"]


def test_occupied_hand_rejects_paid_cast_atomically_and_chosen_other_hand_is_real():
    state, data, params = _case()
    state[f"{SRD}.entities"]["hero"]["inventory"].append("Torch")
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["Torch"])
    _run(
        "record_srd_scene_hand_occupancy",
        state,
        actor_state="hero",
        hand_capacity=2,
        holdings=[{"inventory_item": "Torch", "hand_indices": [0]}],
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual free hand"):
        _cast(state, data, params)
    assert state == before
    _cast(state, data, params | {"flame_blade_hand": 1})
    assert (
        _run("qualify_srd_scene_hand_occupancy", state, actor_state="hero").result["context"][
            "_scene_free_hand_count"
        ]
        == 0
    )


def test_real_combat_bonus_cast_and_later_magic_spend_each_actual_budget_once():
    state, data, params = _case()
    state[f"{SRD}.combat"] = budget._state()[f"{SRD}.combat"]
    state[f"{SRD}.entities"]["hero"]["bonus_action_available"] = True
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert _use(state, data)["context"]["_flame_damage_applied"] == 13
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="action"):
        _use(state, data)
    assert state == before


def test_release_and_real_bonus_reevoke_do_not_reset_original_deadline_or_pay_again():
    state, data, params = _case()
    _cast(state, data, params)
    record = deepcopy(_source(state))
    _run("release_srd_flame_blade", state, caster_state="hero")
    assert _source(state)["blade_manifested"] is False
    assert (
        _run("qualify_srd_scene_hand_occupancy", state, actor_state="hero").result["context"][
            "_scene_free_hand_count"
        ]
        == 2
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Evoke the actual blade"):
        _use(state, data)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=7)
    _run("evoke_srd_flame_blade", state, caster_state="hero", hand_index=1)
    assert _source(state)["expires_tick"] == record["expires_tick"]
    assert _source(state)["cast_id"] == record["cast_id"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    assert _source(state)["hand_index"] == 1


@pytest.mark.parametrize("mode", ["critical", "miss", "resistance"])
def test_native_roll_modifier_once_and_actual_fire_mitigation(monkeypatch, mode):
    state, data, params = _case()
    _cast(state, data, params)
    if mode == "resistance":
        state[f"{SRD}.entities"]["guard"]["damage_resistances"] = ["fire"]
    rolls = attacks.Rolls(attack=20 if mode == "critical" else 1 if mode == "miss" else 10)
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _use(state, data)["context"]
    assert context["_flame_damage_rolled"] == (
        22 if mode == "critical" else 0 if mode == "miss" else 13
    )
    assert context["_flame_damage_applied"] == (
        22 if mode == "critical" else 0 if mode == "miss" else 6
    )
    assert rolls.sides == [20] + (
        [6] * 6 if mode == "critical" else [] if mode == "miss" else [6] * 3
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 100


def test_actual_body_change_requires_release_and_current_anatomy_requalification():
    state, data, params = _case()
    _cast(state, data, params)
    body = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero")
    body["height"] += 1
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Hand anatomy"):
        _use(state, data)
    assert state == before
    _run("release_srd_flame_blade", state, caster_state="hero")
    _run("record_srd_scene_hand_occupancy", state, actor_state="hero", hand_capacity=2, holdings=[])
    _run("evoke_srd_flame_blade", state, caster_state="hero", hand_index=1)
    assert _use(state, data)["context"]["_flame_damage_applied"] == 13


def test_actual_mirror_diversion_precedes_fire_dice_and_casting_modifier(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    _run("prepare_srd_creature", state, **_mage_spec())
    scene = state[f"{SRD}.state"]["spatial_scene"]
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
    rolls = attacks.Rolls(attack=20, damage=6)
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _use(state, data, target="mage")["context"]
    assert context["_mirror_redirected"] is True
    assert context["_flame_damage_rolled"] == context["_flame_damage_applied"] == 0
    assert rolls.sides == [20, 6, 6, 6]
    assert _source(state)["blade_manifested"] is True


def test_actual_original_clock_and_concentration_retire_blade_and_free_its_hand():
    state, data, params = _case()
    _cast(state, data, params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert not any(r.get("spell") == "flame_blade" for r in state[f"{SRD}.state"]["turn_effects"])
    assert (
        _run("qualify_srd_scene_hand_occupancy", state, actor_state="hero").result["context"][
            "_scene_free_hand_count"
        ]
        == 2
    )


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    names = PUBLIC_TOOLS | {
        "record_srd_scene_hand_occupancy",
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
    }
    for name in names:
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
            "id": "flame-blade-real-source-hand-consumer",
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
            "evidence": {"tests": ["paid-flame-blade-hand-magic-release-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda r: r["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_paid_public_flame_blade_hand_magic_release_reevoke_reopens_and_retries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "flame-blade.db"
    rolls = attacks.Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner,
            "flame_blade",
            target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0},
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(
            _act(owner, "use_srd_flame_blade", caster="hero", caster_state="hero", target="guard")
        )
        receipts.append(_act(owner, "release_srd_flame_blade", caster_state="hero"))
        receipts.append(_act(owner, "evoke_srd_flame_blade", caster_state="hero", hand_index=1))
        saved = owner.snapshot("casting")
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
        with pytest.raises(MechanicsRejected, match="real exploration clock"):
            _act(owner, "use_srd_flame_blade", caster="hero", caster_state="hero", target="guard")
        assert owner.snapshot("casting") == saved


def test_repeated_emission_current_native_cards_and_scalar_cap_are_preserved(monkeypatch):
    runtime, encoded = _runtime()
    emitted = bind_runtime_consumers(encoded.decode())
    repeated = _load_runtime(emitted)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(repeated["tools"], changed)
    repeated["tools"].update(changed)
    assert repeated == runtime
    parameters = _tool_schema(runtime["tools"]["use_srd_flame_blade"])["properties"]
    assert set(parameters) == {"caster_state", "caster", "target", "lasting_state"}
    state, data, params = _case()
    _cast(state, data, params)
    budgets = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    assert _use(state, data)["context"]["_flame_damage_applied"] == 13
    assert budgets and all(b.max_operations == 10000 for b in budgets)
    assert max(b.operations for b in budgets) < 10000
    print("Actual Flame Blade scalar operations:", max(b.operations for b in budgets))
