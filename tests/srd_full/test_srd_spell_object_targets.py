"""Object rules cross the real package/scene/dice/persistence seam without a spell claim."""

import json
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_object_targets import (
    ATTACK_EXPORTS,
    INTRINSIC_IMMUNITIES,
    MATERIAL_AC,
    OBJECT_HP,
    OBJECT_RULE_PAGES,
    OBJECT_SOURCE,
    PUBLIC_TOOLS,
    extend_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _call, _load_runtime, _parameter, _tool
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, StateValue, owned_payload
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_public_spell_casting as public_casting
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_scene_relations import _state as _scene_state

SRD = "wotc.srd.5.2.1"
ROOT = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1"
FIXED_ATTACK = "synthetic_fixed_object_attack"


class _RecordedRandom(Random):
    """Record actual random draws without choosing a die value or attack verdict."""

    def __init__(self, seed):
        super().__init__(seed)
        self.draws = []

    def randint(self, a, b):
        result = super().randint(a, b)
        self.draws.append((a, b, result))
        return result


class _NoAttackReroll(_RecordedRandom):
    """Detect an attack roll during a rejected action or durable replay."""

    def randint(self, a, b):
        assert (a, b) != (1, 20), "The object attack was rolled again"
        return super().randint(a, b)


@pytest.fixture(autouse=True)
def _keep_actual_rng_seed(monkeypatch):
    """Observe seeded real dice instead of the authority action's generated seed."""
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


@lru_cache
def _runtime_bytes():
    """Augment immutable package bytes; the fixed wrapper exists only in this test."""
    text = extend_runtime((ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8"))
    wrapper = _tool(
        "Synthetic fixture: one fixed source attack; this is not an admitted spell.",
        [_parameter("object_id")],
        [
            _call(
                "resolve_srd_spell_object_attack",
                {
                    "caster_state": "hero",
                    "object_id": "{object_id}",
                    "attack_modifier": 10,
                    "damage_die": "d8",
                    "dice_count": 1,
                    "damage_type": "fire",
                    "max_range_feet": 60,
                },
                ATTACK_EXPORTS,
            )
        ],
    )
    return _replace_tools(text, {FIXED_ATTACK: wrapper}).encode()


@lru_cache
def _mechanic(name):
    runtime = _load_runtime(_runtime_bytes().decode())
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.1.0",
        package_sha256="a" * 64,
        runtime_bytes=_runtime_bytes(),
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.d20-test",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _state():
    state = _scene_state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1].update(id="crate", position=[10, 0, 0])
    scene["entities"][1]["metadata"] = {
        "plane": "material",
        "lighting": "bright",
        "fiction": "Synthetic wooden crate",
    }
    state[f"{SRD}.combat"].update(encounter_active=False, pending_action=None)
    return state


def _object(state):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "crate")


def _run(name, state, **parameters):
    result = _mechanic(name)(
        MechanicContext(
            campaign_id="objects",
            turn_id="turn",
            action_id=name,
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload({}),
        )
    )
    if result.result["status"] == "committed":
        for mutation in result.mutations:
            state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result.result["context"]


def _prepare(state, **changes):
    return _run(
        "prepare_srd_spell_object",
        state,
        **{
            "object_id": "crate",
            "material": "wood",
            "size": "large",
            "durability": "resilient",
            **changes,
        },
    )


def _attack(state, **changes):
    return _run(
        "resolve_srd_spell_object_attack",
        state,
        **{
            "caster_state": "hero",
            "object_id": "crate",
            "attack_modifier": 10,
            "damage_die": "d8",
            "dice_count": 1,
            "damage_type": "fire",
            "max_range_feet": 60,
            **changes,
        },
    )


def _reject(name, state, **parameters):
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _run(name, state, **parameters)
    assert state == before


def test_object_rule_values_and_source_pages_are_the_pinned_tables():
    corpus = json.loads((ROOT / "content/corpus/rules_glossary.json").read_text())
    actual = {p["page"]: p["text_sha256"] for p in corpus["page_records"]}
    assert {p: actual[p] for p in OBJECT_RULE_PAGES} == OBJECT_RULE_PAGES
    assert MATERIAL_AC == {
        "cloth": 11,
        "paper": 11,
        "rope": 11,
        "crystal": 13,
        "glass": 13,
        "ice": 13,
        "wood": 15,
        "stone": 17,
        "iron": 19,
        "steel": 19,
        "mithral": 21,
        "adamantine": 23,
    }
    assert OBJECT_HP == {
        "tiny": {"fragile": 2, "resilient": 5},
        "small": {"fragile": 3, "resilient": 10},
        "medium": {"fragile": 4, "resilient": 18},
        "large": {"fragile": 5, "resilient": 27},
    }
    assert INTRINSIC_IMMUNITIES == ("poison", "psychic")
    runtime = _load_runtime(_runtime_bytes().decode())
    schema = _tool_schema(runtime["tools"]["prepare_srd_spell_object"])
    assert set(schema["properties"]) == {"object_id", "material", "size", "durability"}
    assert {"prepare_srd_spell_object"} == PUBLIC_TOOLS


@pytest.mark.parametrize(
    "material,size,durability,ac,hp",
    [
        ("paper", "tiny", "fragile", 11, 2),
        ("glass", "small", "resilient", 13, 10),
        ("stone", "medium", "fragile", 17, 4),
        ("adamantine", "large", "resilient", 23, 27),
    ],
)
def test_classification_derives_stats_and_preserves_actual_scene_and_creatures(
    material, size, durability, ac, hp
):
    state = _state()
    before = deepcopy(state)
    result = _prepare(state, material=material, size=size, durability=durability)
    profile = _object(state)["metadata"]["srd_spell_object"]
    assert (
        profile
        == result["_object_profile"]
        == {
            "kind": "srd_spell_object",
            "source": OBJECT_SOURCE,
            "material": material,
            "size": size,
            "durability": durability,
            "armor_class": ac,
            "hp": hp,
            "max_hp": hp,
            "destroyed": False,
        }
    )
    assert state[f"{SRD}.entities"] == before[f"{SRD}.entities"]
    assert {k: v for k, v in _object(state).items() if k != "metadata"} == {
        k: v for k, v in _object(before).items() if k != "metadata"
    }
    assert _object(state)["metadata"]["fiction"] == "Synthetic wooden crate"
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == 1
    _reject(
        "prepare_srd_spell_object",
        state,
        object_id="crate",
        material=material,
        size=size,
        durability=durability,
    )


@pytest.mark.parametrize("change", ["missing", "creature", "inactive", "huge", "combat"])
def test_unsupported_object_admission_is_atomic(change):
    state = _state()
    params = {"object_id": "crate", "material": "wood", "size": "large", "durability": "resilient"}
    if change == "missing":
        params["object_id"] = "absent"
    elif change == "creature":
        params["object_id"] = "hero"
    elif change == "inactive":
        _object(state)["active"] = False
    elif change == "huge":
        params["size"] = "huge"
    elif change == "combat":
        state[f"{SRD}.combat"]["encounter_active"] = True
    _reject("prepare_srd_spell_object", state, **params)


@pytest.mark.parametrize("seed,kind,count", [(1, "hit", 1), (5, "critical_hit", 2), (2, "miss", 0)])
def test_real_d20_and_damage_are_persisted_without_creature_consequences(
    monkeypatch, seed, kind, count
):
    state = _state()
    _prepare(state)
    creatures = deepcopy(state[f"{SRD}.entities"])
    rng = _RecordedRandom(seed)
    monkeypatch.setattr(dice, "_rng", rng)
    result = _attack(state)
    assert result["_object_attack_kind"] == kind
    assert len([d for d in rng.draws if d[:2] == (1, 20)]) == 1
    assert len([d for d in rng.draws if d[:2] == (1, 8)]) == count
    damage = sum(d[2] for d in rng.draws if d[:2] == (1, 8))
    assert result["_object_damage_rolled"] == damage
    assert result["_object_damage_applied"] == damage
    assert _object(state)["metadata"]["srd_spell_object"]["hp"] == 27 - damage
    assert state[f"{SRD}.entities"] == creatures
    assert "crate" not in creatures


@pytest.mark.parametrize("damage_type", ["poison", "psychic"])
def test_intrinsic_immunity_uses_real_hit_and_damage_dice(monkeypatch, damage_type):
    state = _state()
    _prepare(state)
    before = deepcopy(state)
    monkeypatch.setattr(dice, "_rng", Random(1))
    result = _attack(state, damage_type=damage_type)
    assert result["_object_attack_kind"] == "hit"
    assert result["_object_damage_rolled"] > 0
    assert result["_object_damage_applied"] == 0
    assert state == before


def test_zero_hp_marks_only_the_actual_section_and_cannot_be_reset_or_hit(monkeypatch):
    state = _state()
    _prepare(state, material="paper", size="tiny", durability="fragile")
    before = deepcopy(state)
    monkeypatch.setattr(dice, "_rng", Random(5))
    result = _attack(state)
    assert result["_object_attack_kind"] == "critical_hit"
    assert result["_object_hp_after"] == 0
    assert result["_object_damage_applied"] == 2
    assert _object(state)["metadata"]["srd_spell_object"]["destroyed"] is True
    assert state[f"{SRD}.entities"] == before[f"{SRD}.entities"]
    assert _object(state)["active"] is True  # Collapse/remnants are a GM geometry decision.
    assert _object(state)["position"] == _object(before)["position"]
    with pytest.raises(RulesetPreconditionRejected, match="already destroyed"):
        _attack(state)
    _reject(
        "prepare_srd_spell_object",
        state,
        object_id="crate",
        material="wood",
        size="large",
        durability="resilient",
    )


@pytest.mark.parametrize(
    "failure", ["range", "plane", "total_cover", "source", "ac", "magical_adjustment"]
)
def test_real_geometry_and_source_validation_reject_before_any_roll(monkeypatch, failure):
    state = _state()
    _prepare(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if failure == "range":
        _object(state)["position"] = [70, 0, 0]
    elif failure == "plane":
        _object(state)["metadata"]["plane"] = "ethereal"
    elif failure == "total_cover":
        wall = _geometry("wall", [4, -50, -50], [6, 50, 50])
        wall["map_id"] = "relation"
        scene["geometry"] = [wall]
    elif failure == "source":
        _object(state)["metadata"]["srd_spell_object"]["source"] = "unowned magical source"
    elif failure == "ac":
        _object(state)["metadata"]["srd_spell_object"]["armor_class"] = 1
    else:
        _object(state)["metadata"]["srd_spell_object"]["resistances"] = ["fire"]
    before = deepcopy(state)
    rng = _NoAttackReroll(0)
    monkeypatch.setattr(dice, "_rng", rng)
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state)
    assert rng.draws == []
    assert state == before


@pytest.mark.parametrize("side,cover_bonus", [(False, 2), (True, 5)])
def test_actual_partial_cover_changes_object_ac_and_real_hit(side, cover_bonus, monkeypatch):
    state = _state()
    _prepare(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    walls = [_geometry("low-wall", [4, -50, -50], [6, 50, 1.6])]
    if side:
        walls.append(_geometry("side-wall", [4, -50, -50], [6, 0.01, 50]))
    for wall in walls:
        wall["map_id"] = "relation"
    scene["geometry"] = walls
    relation = _run(
        "resolve_srd_spell_object_relation",
        state,
        caster_state="hero",
        object_id="crate",
        max_range_feet=60,
    )
    assert relation["_object_cover_bonus"] == cover_bonus
    assert relation["_object_effective_armor_class"] == 15 + cover_bonus
    assert relation["_object_can_see"] is True
    before = deepcopy(state)
    rng = _RecordedRandom(1)
    monkeypatch.setattr(dice, "_rng", rng)
    result = _attack(state)
    assert rng.draws == [(1, 20, 5)]
    assert result["_object_attack_total"] == 15
    assert result["_object_attack_kind"] == "miss"
    assert result["_object_damage_roll"] is None
    assert state == before


@pytest.mark.parametrize("obscured", ["dark", "blinded", "invisible"])
def test_unseen_actual_object_has_disadvantage_and_native_senses_apply(monkeypatch, obscured):
    state = _state()
    _prepare(state)
    if obscured == "dark":
        _object(state)["metadata"]["lighting"] = "darkness"
    elif obscured == "blinded":
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    else:
        _object(state)["metadata"]["invisible"] = True
    rng = _RecordedRandom(0)
    monkeypatch.setattr(dice, "_rng", rng)
    result = _attack(state)
    assert result["_object_can_see"] is False
    assert result["_object_disadvantage"] is True
    assert len([d for d in rng.draws if d[:2] == (1, 20)]) == 2
    state[f"{SRD}.entities"]["hero"]["blindsight_feet"] = 30
    relation = _run(
        "resolve_srd_spell_object_relation",
        state,
        caster_state="hero",
        object_id="crate",
        max_range_feet=60,
    )
    assert relation["_object_can_see"] is True


def test_actual_carried_object_follows_holder_and_damage_uses_fresh_scene_revision(monkeypatch):
    state = _state()
    _prepare(state)
    _object(state)["metadata"]["carried_by"] = "hero"
    hero = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "hero")
    hero["position"] = [25, 5, 0]
    before_revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    rng = _RecordedRandom(1)
    monkeypatch.setattr(dice, "_rng", rng)
    result = _attack(state)
    assert result["_object_attack_kind"] == "hit"
    assert _object(state)["position"] == [25, 5, 0]
    assert (
        _object(state)["metadata"]["srd_spell_object"]["hp"]
        == 27 - result["_object_damage_applied"]
    )
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == before_revision + 2


@lru_cache
def _test_archive():
    """Materialize a test-owned package closure, without a canonical build or source edits."""
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    runtime = _load_runtime(_runtime_bytes().decode())
    for name in ("prepare_srd_spell_object", FIXED_ATTACK):
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.d20-test",
                "input_schema": _tool_schema(runtime["tools"][name], tool_name=name),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
        )
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": _runtime_bytes()}
    content = json.loads(payload["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "synthetic-object-attack-fixture",
            "kind": "ruleset_runtime",
            "family": "synthetic-object-fixture",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.d20-test"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": ["prepare_srd_spell_object", FIXED_ATTACK],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["synthetic-object-package-persistence"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def test_public_fixed_fixture_uses_actual_package_and_survives_reopen_retry(tmp_path, monkeypatch):
    database = tmp_path / "object.db"
    scene = _state()[f"{SRD}.state"]["spatial_scene"]
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    rng = _RecordedRandom(1)
    monkeypatch.setattr(dice, "_rng", rng)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        create = owner.create_campaign

        def admit_scene(command):
            initial = list(command.initial_state)
            for index, item in enumerate(initial):
                if item.namespace == SRD and item.key == "state":
                    initial[index] = replace(item, value=item.value | {"spatial_scene": scene})
                    break
            else:
                initial.append(StateValue(SRD, "state", {"spatial_scene": scene}))
            return create(replace(command, initial_state=tuple(initial)))

        with patch.object(owner, "create_campaign", side_effect=admit_scene):
            public_casting._create_spell_campaign(owner, "fire_bolt")
        pristine = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "prepare_srd_spell_object",
                object_id="crate",
                material="wood",
                size="large",
                durability="resilient",
                armor_class=1,
                hp=999,
                immunities=["fire"],
            )
        assert owner.snapshot("casting") == pristine
        prepared, prepare_command = _act(
            owner,
            "prepare_srd_spell_object",
            object_id="crate",
            material="wood",
            size="large",
            durability="resilient",
        )
        assert prepared.status is ActionStatus.COMMITTED
        classified = owner.snapshot("casting")
        assert owner.execute_action(prepare_command) == prepared
        assert owner.snapshot("casting") == classified
        with pytest.raises(MechanicsRejected):
            _act(owner, FIXED_ATTACK, object_id="crate", hit=True, damage=999)
        with pytest.raises(CommandRejected, match="unavailable from the canonical catalog"):
            _act(
                owner,
                "resolve_srd_spell_object_attack",
                caster_state="hero",
                object_id="crate",
                attack_modifier=100,
                damage_die="d8",
                dice_count=100,
                damage_type="fire",
                max_range_feet=60,
            )
        assert owner.snapshot("casting") == classified
        receipt, attack_command = _act(owner, FIXED_ATTACK, object_id="crate")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        result = receipt.result["context"]
        assert result["_object_attack_kind"] == "hit"
        damage = sum(d[2] for d in rng.draws if d[:2] == (1, 8))
        assert result["_object_damage_applied"] == damage > 0
        assert saved.state[f"{SRD}.entities"] == classified.state[f"{SRD}.entities"]
        assert "crate" not in saved.state[f"{SRD}.entities"]
        assert _object(saved.state)["metadata"]["srd_spell_object"]["hp"] == 27 - damage
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "prepare_srd_spell_object",
                object_id="crate",
                material="paper",
                size="tiny",
                durability="fragile",
            )
        assert owner.snapshot("casting") == saved
    forbidden_rolls = _NoAttackReroll(99)
    monkeypatch.setattr(dice, "_rng", forbidden_rolls)
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(attack_command) == receipt
        assert reopened.execute_action(prepare_command) == prepared
        assert reopened.snapshot("casting") == saved
        assert forbidden_rolls.draws == []
