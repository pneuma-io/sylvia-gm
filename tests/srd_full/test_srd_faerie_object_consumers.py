"""Paid Faerie Fire reaches genuine object attacks through actual source sight."""

import ast
import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from re import sub
from unittest.mock import patch

import pytest
import yaml
from scripts import (
    srd_actor_spell_modifiers,
    srd_scene_relations,
    srd_timed_actor_defenses,
    srd_timed_senses,
)
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_illumination_object_consumers import bind_runtime_consumers
from scripts.srd_full.srd_spell_illumination import _casting_tool

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity as scene_entity
from tests.srd_full.test_srd_spell_wave3_information import _scene, _wall

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-faerie-object-consumers/input-1"))
STARRY = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-faerie-object-consumers/input-2")
)


def _source_functions(filename, expected, names, namespace):
    """Execute only exact immutable owning constructors, without writing peer bytecode."""
    payload = (STARRY / filename).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == expected
    tree = ast.parse(payload)
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), namespace)


@lru_cache
def _runtime(bound=True):
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    namespace = {
        "deepcopy": deepcopy,
        "sub": sub,
        "_call": srd_actor_spell_modifiers._call,
        "_tool_scene": srd_scene_relations._tool_scene,
        "POSITION_EXPORTS": srd_scene_relations.POSITION_EXPORTS,
        "_entity": srd_timed_actor_defenses._entity,
        "_parameter": srd_timed_actor_defenses._parameter,
        "_require": srd_timed_actor_defenses._require,
        "_set": srd_timed_actor_defenses._set,
        "_when": srd_timed_actor_defenses._when,
        "_push": srd_timed_senses._push,
    }
    _source_functions(
        "srd_starry_wisp_objects.py",
        "0d35416010bb939fb8c22e90821971db09f28a2abba585a9f2f2b989e76613d2",
        ["_lists", "_bind", "compose_starry_object"],
        namespace,
    )
    _source_functions(
        "srd_spell_light_damage_riders.py",
        "3c27af6d440a6ac52da80496cb788a10d0896a45971128e3e11fe7fa64682d47",
        ["_starry_light_tool"],
        namespace,
    )
    changed = {"install_srd_starry_wisp_light": namespace["_starry_light_tool"]()}
    namespace["compose_starry_object"](runtime["tools"], changed)
    runtime["tools"].update(changed)
    # Apply only targetless v10's exact scalar parameter; retain the parent's
    # source/removal adapters and normalized object-source array representation.
    authored = _casting_tool()
    actual = runtime["tools"]["cast_spell_illumination_spell"]
    index = next(i for i, p in enumerate(actual["parameters"]) if p["name"] == "target_state")
    actual["parameters"][index] = deepcopy(
        next(p for p in authored["parameters"] if p["name"] == "target_state")
    )
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    if bound:
        text = bind_runtime_consumers(text)
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name, bound=True):
    runtime, payload = _runtime(bound)
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


class ActualDiceSequence:
    def __init__(self, d20=(1, 20)):
        self.d20 = iter(d20)

    def randint(self, low, high):
        assert low == 1
        return next(self.d20) if high == 20 else 1


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _case():
    state, data, params = cards._spell_case("faerie_fire")
    data["spellcasting_grants"][0]["spells"].append("starry_wisp")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("starry_wisp")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            scene_entity("hero", [0, 0, 0], participant_ref="hero"),
            scene_entity("mage", [0, 10, 0], participant_ref="mage"),
            scene_entity("cargo", [20, 0, 0], is_object=True),
        ),
    }
    params.pop("target", None)
    params["illumination_position"] = [10, -10, 0]
    params["illumination_color"] = "blue"
    return state, data, params


def _outlined():
    state, data, params = _case()
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="cargo",
        material="wood",
        size="medium",
        durability="resilient",
    )
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    records = state[f"{SRD}.state"]["turn_effects"]
    assert any(r.get("spell") == "faerie_fire" and r["source"] == "cargo" for r in records)
    return state, data, params


def _starry_params():
    _, _, params = cards._spell_case("starry_wisp")
    params.pop("target", None)
    params["object_target_id"] = "cargo"
    return params


def _rolls(monkeypatch, d20=(1, 20)):
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20))
    rolls = []
    original = _effect_executor.dice_roll

    def record(*args, **kwargs):
        result = original(*args, **kwargs)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", record)
    for name in ("advantage", "disadvantage"):
        owner = getattr(dice, name)

        def observe(*args, _owner=owner, **kwargs):
            result = _owner(*args, **kwargs)
            rolls.append(result)
            return result

        monkeypatch.setattr(dice, name, observe)
    return rolls


def _object(state):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "cargo"
    )


def _relation(state):
    return _run(
        "resolve_srd_spell_object_relation",
        state,
        caster_state="hero",
        object_id="cargo",
        max_range_feet=60,
    ).result["context"]


def _attack(state, data):
    return _run(
        "cast_spell_outside_combat", state, participant_data=data, **_starry_params()
    ).result


def test_paid_outline_advantage_reaches_real_starry_object_d20_and_critical_damage(monkeypatch):
    state, data, _ = _outlined()
    assert _relation(state)["_object_can_see"] is True
    rolls = _rolls(monkeypatch)
    result = _attack(state, data)
    assert [(r.sides, r.count) for r in rolls] == [(20, 2), (8, 8)]
    assert sorted(rolls[0].results) == [1, 20]
    assert _object(state)["metadata"]["srd_spell_object"]["hp"] == 10
    assert "critical" in str(result["result_card"]).lower()


@pytest.mark.parametrize("expiry", ["clock", "concentration"])
def test_source_retirement_removes_actual_outline_attack_advantage(monkeypatch, expiry):
    state, data, _ = _outlined()
    if expiry == "clock":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    else:
        _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
    rolls = _rolls(monkeypatch, (10,))
    _attack(state, data)
    assert rolls[0].count == 1


def _fog(state):
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Fog mage",
        source="Synthetic prepared caster: AC12 HP30 Speed30 Int3 attack7 DC15.",
        armor_class=12,
        hit_points=30,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=0,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        inventory=[],
        spell_materials=[],
        spells=[{"spell": "fog_cloud", "uses_per_day": 2}],
    )
    _run(
        "cast_spell_outside_combat",
        state,
        spell="fog_cloud",
        caster_state="mage",
        environment_origin=[20, 0, 0],
        verbal_component_available=True,
        somatic_component_available=True,
    )


@pytest.mark.parametrize(
    "sense,reach,count",
    [("none", 0, 2), ("blindsight", 20, 2), ("blindsight", 19, 2), ("truesight", 120, 2)],
)
def test_real_fog_retains_outline_but_only_reaching_blindsight_can_see(
    monkeypatch, sense, reach, count
):
    state, data, _ = _outlined()
    _fog(state)
    if sense != "none":
        state[f"{SRD}.entities"]["hero"][f"{sense}_feet"] = reach
    seeing = sense == "blindsight" and reach >= 20
    assert _relation(state)["_object_can_see"] is seeing
    rolls = _rolls(monkeypatch)
    _attack(state, data)
    assert rolls[0].count == count
    assert _object(state)["metadata"]["srd_spell_object"]["hp"] == (10 if seeing else 18)


@pytest.mark.parametrize("reach,seeing", [(0, False), (19, False), (20, True)])
def test_native_blindness_preserves_real_outline_and_reaching_blindsight(
    monkeypatch, reach, seeing
):
    state, data, _ = _outlined()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["conditions"].append("blinded")
    hero["blindsight_feet"] = reach
    assert _relation(state)["_object_can_see"] is seeing
    rolls = _rolls(monkeypatch)
    _attack(state, data)
    # The actual D20 owner's native Blinded Disadvantage remains independent
    # from the object sight result; a seen outline cancels it with Advantage.
    assert rolls[0].count == (1 if seeing else 2)
    assert _object(state)["metadata"]["srd_spell_object"]["hp"] == 18


def test_actual_fog_removal_restores_retained_outline_without_recasting(monkeypatch):
    state, data, _ = _outlined()
    _fog(state)
    assert _relation(state)["_object_can_see"] is False
    _run("end_srd_concentration_node", state, source_state="mage", target_state="mage")
    assert _relation(state)["_object_can_see"] is True
    rolls = _rolls(monkeypatch)
    _attack(state, data)
    assert rolls[0].count == 2 and _object(state)["metadata"]["srd_spell_object"]["hp"] == 10


def test_source_outline_light_and_invisible_suppression_are_retained_in_darkness(monkeypatch):
    state, data, _ = _outlined()
    _object(state)["metadata"].update(lighting="darkness", invisible=True)
    relation = _relation(state)
    assert relation["_object_can_see"] is True and relation["_object_faerie"] is True
    assert relation["_object_invisible"] is False and relation["_object_light"] == "dim"
    rolls = _rolls(monkeypatch)
    _attack(state, data)
    assert rolls[0].count == 2


def test_partial_physical_cover_keeps_actual_exposure_samples_and_outline_advantage(monkeypatch):
    state, data, _ = _outlined()
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1, low_y=-0.1, high_y=0.1)]
    rolls = _rolls(monkeypatch)
    assert _relation(state)["_object_can_see"] is True
    _attack(state, data)
    assert rolls[0].count == 2


def test_object_binder_is_idempotent_and_never_changes_shared_d20():
    before, _ = _runtime(False)
    after, payload = _runtime()
    assert before["tools"]["d20_test"] == after["tools"]["d20_test"]
    assert bind_runtime_consumers(payload.decode()).encode() == payload
    assert set(after["tools"]) - set(before["tools"]) == {
        "resolve_srd_illumination_object_obscurement"
    }


@lru_cache
def _archive():
    with patch.object(
        bundled_packages, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1"
    ):
        primary, reference = bundled_packages._srd_drafts()
    runtime, payload = _runtime()
    record = deepcopy(primary.record)
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": payload}
    for name in ("help-action", "unarmed-strike", "weapon-catalog"):
        path = f"content/{name}.source.json"
        payloads[path] = (PARENT / "rulesets/dnd-srd-5.2.1" / path).read_bytes()
    return bundled_packages._archive(
        (
            NormalizedPackageDraft(record, payloads, 4),
            reference,
        )
    )


def test_actual_archive_paid_outline_object_attack_reopen_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "ROOT", PARENT)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    _rolls(monkeypatch)
    database = tmp_path / "faerie-object.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "faerie_fire")
        preparation, _ = _act(
            owner,
            "prepare_srd_spell_object",
            object_id="cargo",
            material="wood",
            size="medium",
            durability="resilient",
        )
        assert preparation.status is ActionStatus.COMMITTED
        cast, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        attack, command = _act(reopened, "cast_spell_outside_combat", **_starry_params())
        assert attack.status is ActionStatus.COMMITTED
        saved = reopened.snapshot("casting")
        assert _object(saved.state)["metadata"]["srd_spell_object"]["hp"] == 10
        assert reopened.execute_action(command) == attack
        assert reopened.snapshot("casting") == saved
