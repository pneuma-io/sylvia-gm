"""Authentic occupied-body recovery spends host Hit Dice and uses its physical Constitution."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from jar._compiled.__wrapped__(monkeypatch)


def _occupied(*, pools=False):
    state, data, params = jar._case()
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    host["constitution_modifier"] = (
        jar._host_artifact(data)["abilities"]["constitution"] - 10
    ) // 2
    if pools:
        # Each actual validated fixture actor is a single-class Wizard with the printed d6.
        # The saved host has previously spent2 dice; its current body keeps that independent pool.
        for entity, remaining in ((hero, data["level"]), (host, data["level"] - 2)):
            entity["resources"]["hit_die"] = remaining
            entity["hit_dice_pools"] = [
                {"class_id": "wizard", "size": 6, "remaining": remaining, "maximum": data["level"]}
            ]
    original = deepcopy(hero)
    jar._cast(state, data, params)
    original["constitution_modifier"] = (data["abilities"]["constitution"] - 10) // 2
    jar._possess(state, data)
    return state, data, original


def _wound_and_rest(state, data):
    jar._run(
        "apply_srd_damage",
        state,
        data,
        target="guard",
        amount=30,
        adjustment=0,
        damage_type="force",
    )
    jar._run("begin_srd_rest", state, data, target_state="hero", kind="short", activity="light")
    jar._run("advance_srd_lasting_time", state, data, elapsed_rounds=600)
    jar._run("complete_srd_rest", state, data, target_state="hero")


def test_actual_pc_host_short_rest_rolls_native_die_and_con_then_original_return(monkeypatch):
    state, data, original = _occupied()
    _wound_and_rest(state, data)
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    assert hero["constitution_modifier"] == 4
    before = hero["resources"]["hit_die"]
    monkeypatch.setattr(dice, "_rng", Random(4))
    expected = Random(4)
    rolled = expected.randint(1, 6)
    result = jar._run("take_srd_short_rest_hit_die", state, data, target_state="hero")
    context = result.result["context"]
    assert context["_rest_die"] == 6 and context["_short_rest_hit_die_roll"]["modifier"] == 4
    assert context["_rest_hp_restored"] == rolled + 4
    assert dice._rng.getstate() == expected.getstate()
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    assert hero["hp"] == host["hp"] == 30 + rolled + 4
    assert hero["resources"]["hit_die"] == host["resources"]["hit_die"] == before - 1
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="container")
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="body")
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    assert hero["constitution_modifier"] == original["constitution_modifier"]
    assert hero["resources"]["hit_die"] == original["resources"]["hit_die"]
    assert host["resources"]["hit_die"] == before - 1 and host["hp"] == 30 + rolled + 4


def test_real_host_class_pool_spend_restore_stays_independent_of_abandoned_body():
    state, data, original = _occupied(pools=True)
    _wound_and_rest(state, data)
    jar._run(
        "take_srd_short_rest_hit_die", state, data, target_state="hero", hit_die_class="wizard"
    )
    for name in ("hero", "guard"):
        entity = state[f"{SRD}.entities"][name]
        assert entity["hit_dice_pools"][0]["remaining"] == data["level"] - 3
        assert entity["resources"]["hit_die"] == data["level"] - 3
    jar._run("restore_srd_resource", state, data, target="hero", resource="hit_die", amount=1)
    jar._run("spend_srd_resource", state, data, target="hero", resource="hit_die", amount=1)
    for name in ("hero", "guard"):
        entity = state[f"{SRD}.entities"][name]
        assert entity["hit_dice_pools"][0]["remaining"] == data["level"] - 3
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="container")
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="body")
    assert state[f"{SRD}.entities"]["hero"]["hit_dice_pools"] == original["hit_dice_pools"]
    assert state[f"{SRD}.entities"]["guard"]["hit_dice_pools"][0]["remaining"] == data["level"] - 3


def test_actual_long_rest_restores_only_the_current_body_hit_dice_pool_and_hp():
    state, data, original = _occupied(pools=True)
    _wound_and_rest(state, data)
    jar._run(
        "take_srd_short_rest_hit_die", state, data, target_state="hero", hit_die_class="wizard"
    )
    jar._run("begin_srd_rest", state, data, target_state="hero", kind="long", activity="sleep")
    jar._run("advance_srd_lasting_time", state, data, elapsed_rounds=4800)
    jar._run("complete_srd_rest", state, data, target_state="hero")
    for name in ("hero", "guard"):
        entity = state[f"{SRD}.entities"][name]
        assert entity["resources"]["hit_die"] == data["level"]
        assert entity["hit_dice_pools"][0]["remaining"] == data["level"]
        assert entity["hp"] == entity["max_hp"] == 60
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="container")
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="body")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == original["hp"]
    assert hero["hit_dice_pools"] == original["hit_dice_pools"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 60


def _bandit():
    return {
        "creature_id": "native-bandit",
        "name": "Bandit",
        "source": "SRD5.2.1 p261 Bandit",
        "creature_type": "humanoid",
        "armor_class": 12,
        "hit_points": 11,
        "speed": 30,
        "size_rank": 2,
        "initiative_modifier": 1,
        "proficiency_bonus": 2,
        "strength_save": 0,
        "dexterity_save": 1,
        "constitution_save": 1,
        "intelligence_save": 0,
        "wisdom_save": 0,
        "charisma_save": 0,
        "strength_score": 11,
        "dexterity_score": 12,
        "constitution_score": 12,
        "intelligence_score": 10,
        "wisdom_score": 10,
        "charisma_score": 10,
        "native_hit_dice_count": 2,
        "native_hit_die_size": 8,
    }


def _native_case():
    state, data, params = jar._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    actual = deepcopy(next(row for row in scene["entities"] if row["id"] == "guard"))
    actual["id"] = "native-bandit"
    actual["metadata"]["participant_ref"] = "native-bandit"
    scene["entities"].append(actual)
    mage = deepcopy(actual)
    mage["id"] = "mage"
    mage["position"] = [-20, 0, 0]
    mage["metadata"]["participant_ref"] = "mage"
    scene["entities"].append(mage)
    return state, data, params


def test_real_prepared_bandit_consumes_printed_native_d8_then_preserves_original_body(monkeypatch):
    corpus = (
        json.loads((jar.budget.ROOT / "content/corpus/monsters.json").read_text())
        if (jar.budget.ROOT / "content/corpus/monsters.json").exists()
        else json.loads(Path("rulesets/dnd-srd-5.2.1/content/corpus/monsters.json").read_text())
    )
    page = next(row for row in corpus["page_records"] if row["page"] == 261)
    assert page["text_sha256"] == "642a12db56a7882c044c8384ae584dd4ce65eb4103676647fd4db380e3ebe793"
    assert "Bandit\nMedium or Small Humanoid" in page["text"] and "HP 11 (2d8 + 2)" in page["text"]
    state, data, params = _native_case()
    original_hd = state[f"{SRD}.entities"]["hero"]["resources"]["hit_die"]
    jar._run("prepare_srd_creature", state, data, **_bandit())
    jar._cast(state, data, params)
    jar._run(
        "project_srd_magic_jar_soul", state, data, caster_state="hero", host_state="native-bandit"
    )
    jar._run(
        "apply_srd_damage",
        state,
        data,
        target="native-bandit",
        amount=6,
        adjustment=0,
        damage_type="force",
    )
    jar._run("begin_srd_rest", state, data, target_state="hero", kind="short", activity="light")
    jar._run("advance_srd_lasting_time", state, data, elapsed_rounds=600)
    jar._run("complete_srd_rest", state, data, target_state="hero")
    monkeypatch.setattr(dice, "_rng", Random(4))
    result = jar._run("take_srd_short_rest_hit_die", state, data, target_state="hero")
    context = result.result["context"]
    assert context["_rest_die"] == 8 and context["_short_rest_hit_die_roll"]["modifier"] == 1
    assert context["_rest_hp_restored"] == Random(4).randint(1, 8) + 1
    host = state[f"{SRD}.entities"]["native-bandit"]
    assert host["resources"]["hit_die"] == 1 and host["native_hit_dice"]["count"] == 2
    assert state[f"{SRD}.entities"]["hero"]["native_hit_dice"] == host["native_hit_dice"]
    assert host["hp"] == state[f"{SRD}.entities"]["hero"]["hp"]
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="container")
    jar._run("return_srd_magic_jar_soul", state, data, caster_state="hero", destination="body")
    assert state[f"{SRD}.entities"]["hero"]["resources"]["hit_die"] == original_hd
    assert state[f"{SRD}.entities"]["hero"]["native_hit_dice"] is None
    assert state[f"{SRD}.entities"]["native-bandit"]["resources"]["hit_die"] == 1


def test_native_hit_dice_pair_and_existing_body_cannot_be_replaced():
    state, data, _ = _native_case()
    for changes in ({"native_hit_die_size": None}, {"native_hit_die_size": 5}):
        before = deepcopy(state)
        with pytest.raises(MechanicsRejected, match="original printed native Hit Dice"):
            jar._run("prepare_srd_creature", state, data, **(_bandit() | changes))
        assert state == before
    jar._run("prepare_srd_creature", state, data, **_bandit())
    before = deepcopy(state)
    with pytest.raises(MechanicsRejected):
        jar._run("prepare_srd_creature", state, data, **(_bandit() | {"native_hit_dice_count": 10}))
    assert state == before


def test_public_paid_jar_native_body_rest_reopens_and_retries(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(jar.binding, "PUBLIC_TOOLS", jar.binding.PUBLIC_TOOLS | jar.PUBLIC_TOOLS)
    monkeypatch.setattr(jar.public_casting, "bundled_srd_archive", jar.binding._archive)
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda _spell, **kwargs: _native_case())
    path = tmp_path / "native-rest.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "magic_jar")
        created, creation = _act(owner, "prepare_srd_creature", **_bandit())
        assert created.status is ActionStatus.COMMITTED, created.result
        begin, _ = _act(owner, "begin_srd_spell_cast", **jar._begin(params))
        assert begin.status is ActionStatus.COMMITTED, begin.result
        progressed, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progressed.status is ActionStatus.COMMITTED, progressed.result
        cast, casting = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        possessed, possession = _act(
            owner, "project_srd_magic_jar_soul", caster_state="hero", host_state="native-bandit"
        )
        assert possessed.status is ActionStatus.COMMITTED, possessed.result
        prepared, _ = _prepare_mage(owner, [{"spell": "magic_missile", "uses_per_day": 1}])
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        missiles = params | {
            "caster_state": "mage",
            "spell": "magic_missile",
            "target": "native-bandit",
            "material_item_ids": [],
            "projectile_targets": ["native-bandit", "guard", "guard"],
        }
        missiles.pop("caster")
        missiles.pop("slot_resource")
        damaged, damaging = _act(owner, "cast_spell_outside_combat", **missiles)
        assert damaged.status is ActionStatus.COMMITTED, damaged.result
        for name, choices in (
            ("begin_srd_rest", {"target_state": "hero", "kind": "short", "activity": "light"}),
            ("advance_srd_lasting_time", {"elapsed_rounds": 600}),
            ("complete_srd_rest", {"target_state": "hero"}),
        ):
            receipt, _ = _act(owner, name, **choices)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        spent, spending = _act(owner, "take_srd_short_rest_hit_die", target_state="hero")
        assert spent.status is ActionStatus.COMMITTED, spent.result
        rolled = owner.snapshot("casting")
        assert rolled.state[f"{SRD}.entities"]["native-bandit"]["resources"]["hit_die"] == 1
        for command, expected in (
            (creation, created),
            (casting, cast),
            (possession, possessed),
            (damaging, damaged),
            (spending, spent),
        ):
            assert owner.execute_action(command) == expected and owner.snapshot("casting") == rolled
        for destination in ("container", "body"):
            receipt, _ = _act(
                owner, "return_srd_magic_jar_soul", caster_state="hero", destination=destination
            )
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["native-bandit"]["resources"]["hit_die"] == 1
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["hit_die"] == 17
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
