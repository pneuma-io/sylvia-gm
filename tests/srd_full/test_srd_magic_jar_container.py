"""A genuinely destroyed prepared container resolves the exact Jar and its physical bodies."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_magic_jar_container import bind_container_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_magic_jar_circle import _npc
from tests.srd_full.test_srd_npc_spell_casting import _act

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from jar._compiled.__wrapped__(monkeypatch)


def _case():
    state, data, params = jar._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    actual = next(e for e in scene["entities"] if e["id"] == "hero")
    attacker = deepcopy(actual)
    attacker.update(id="jar-breaker", position=[0, -10, 0])
    attacker["metadata"]["participant_ref"] = "jar-breaker"
    scene["entities"].append(attacker)
    _container(state)["metadata"]["lighting"] = "bright"
    return state, data, params


def _container(state):
    return next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "reliquary"
    )


def _prepare(state, data, durability="fragile"):
    result = jar._run(
        "prepare_srd_spell_object",
        state,
        data,
        object_id="reliquary",
        material="crystal",
        size="tiny",
        durability=durability,
    )
    assert result.result["status"] == "committed"
    return result


def _attacker(state, data, spell):
    return jar._run("prepare_srd_creature", state, data, **_npc("jar-breaker", spell, "humanoid"))


def _burst(params):
    result = params | {
        "caster_state": "jar-breaker",
        "spell": "sorcerous_burst",
        "target": None,
        "targets": None,
        "attack_targets": None,
        "material_item_ids": [],
        "object_target_id": "reliquary",
        "selected_damage_type": "fire",
    }
    result.pop("caster")
    result.pop("slot_resource")
    return result


def _flame(params):
    result = _burst(params) | {
        "spell": "produce_flame",
        "object_target_id": "",
        "target": "jar-breaker",
    }
    result.pop("targets")
    result.pop("attack_targets")
    return result


def test_container_binder_repeats_without_duplicating_destruction_or_burst_dice():
    original = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_container_consumers(original)
    second = bind_container_consumers(first)
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    assert yaml.load(first, Loader=loader) == yaml.load(second, Loader=loader)


@pytest.mark.parametrize("possess", [False, True])
def test_actual_burst_destroyed_container_returns_exact_living_souls_and_body(possess, monkeypatch):
    state, data, params = _case()
    _prepare(state, data)
    _attacker(state, data, "sorcerous_burst")
    original = deepcopy(state[f"{SRD}.entities"]["hero"])
    jar._cast(state, data, params)
    source = deepcopy(jar._source(state))
    if possess:
        assert jar._possess(state, data).result["context"]["_jar_possessed"] is True
    monkeypatch.setattr(dice, "_rng", Random(0))
    result = jar._run("cast_spell_outside_combat", state, data, **_burst(params))
    context = result.result["context"]
    assert context["_spell_attack"]["total"] == 13
    assert context["_spell_damage_amount"] == 2
    assert _container(state)["metadata"]["srd_spell_object"]["destroyed"] is True
    assert jar._source(state)["installed"] is False
    assert jar._source(state)["ending"] == "container_destroyed"
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    assert hero["magic_jar_role"] is None and hero["is_alive"] is True
    assert hero["hp"] == original["hp"]
    assert hero.get("current_body_source_id") == original.get("current_body_source_id")
    assert state[f"{SRD}.entities"][source["body_subject"]]["magic_jar_role"] == "retired_body"
    if possess:
        assert host["magic_jar_role"] is None and "incapacitated" not in host["conditions"]
        assert host["is_alive"] is True and host["hp"] == 60
    assert state[f"{SRD}.entities"]["jar-breaker"]["spell_uses_remaining"]["sorcerous_burst"] == 2


def test_real_abandoned_body_death_then_container_destruction_kills_the_same_caster(monkeypatch):
    state, data, params = _case()
    _prepare(state, data)
    _attacker(state, data, "sorcerous_burst")
    jar._cast(state, data, params)
    source = deepcopy(jar._source(state))
    jar._run(
        "apply_srd_damage",
        state,
        data,
        target=source["body_subject"],
        amount=100,
        adjustment=0,
        damage_type="force",
    )
    assert state[f"{SRD}.entities"][source["body_subject"]]["is_alive"] is False
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is True
    monkeypatch.setattr(dice, "_rng", Random(0))
    jar._run("cast_spell_outside_combat", state, data, **_burst(params))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is False and hero["death_tick"] is not None
    assert "dead" in hero["conditions"] and jar._source(state)["installed"] is False


def test_nonlethal_real_object_damage_keeps_source_and_soul_in_container(monkeypatch):
    state, data, params = _case()
    _prepare(state, data, "resilient")
    _attacker(state, data, "sorcerous_burst")
    jar._cast(state, data, params)
    monkeypatch.setattr(dice, "_rng", Random(1))
    jar._run("cast_spell_outside_combat", state, data, **_burst(params))
    assert _container(state)["metadata"]["srd_spell_object"]["hp"] > 0
    assert jar._source(state)["installed"] is True
    assert state[f"{SRD}.entities"]["hero"]["magic_jar_role"] == "container"


def test_real_unbound_object_destruction_does_not_create_or_end_a_jar(monkeypatch):
    state, data, params = _case()
    _prepare(state, data)
    _attacker(state, data, "sorcerous_burst")
    monkeypatch.setattr(dice, "_rng", Random(0))
    jar._run("cast_spell_outside_combat", state, data, **_burst(params))
    assert _container(state)["metadata"]["srd_spell_object"]["destroyed"] is True
    assert "srd_magic_jar" not in _container(state)["metadata"]
    assert state[f"{SRD}.entities"]["hero"].get("magic_jar_role") is None


def test_destroying_an_unrelated_object_preserves_the_actual_active_jar(monkeypatch):
    state, data, params = _case()
    jar._cast(state, data, params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    other = deepcopy(_container(state))
    other.update(id="unbound-vessel", position=[4, -3, 0])
    other["metadata"] = {"is_object": True, "plane": "material", "lighting": "bright"}
    scene["entities"].append(other)
    jar._run(
        "prepare_srd_spell_object",
        state,
        data,
        object_id="unbound-vessel",
        material="crystal",
        size="tiny",
        durability="fragile",
    )
    _attacker(state, data, "sorcerous_burst")
    source = deepcopy(jar._source(state))
    monkeypatch.setattr(dice, "_rng", Random(0))
    jar._run(
        "cast_spell_outside_combat",
        state,
        data,
        **(_burst(params) | {"object_target_id": "unbound-vessel"}),
    )
    assert jar._source(state) == source
    assert state[f"{SRD}.entities"]["hero"]["magic_jar_role"] == "container"


def test_destroying_one_real_container_preserves_an_independent_casters_source(monkeypatch):
    state, data, params = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    second = deepcopy(next(e for e in scene["entities"] if e["id"] == "hero"))
    second.update(id="second-soul", position=[10, 0, 0])
    second["metadata"]["participant_ref"] = "second-soul"
    vessel = deepcopy(_container(state))
    vessel.update(id="second-reliquary", position=[11, 0, 0])
    vessel["metadata"] = {"is_object": True, "plane": "material", "lighting": "bright"}
    scene["entities"].extend([second, vessel])
    second_params = _npc("second-soul", "magic_jar", "humanoid")
    jar._run("prepare_srd_creature", state, data, **second_params)
    _prepare(state, data)
    _attacker(state, data, "sorcerous_burst")
    jar._cast(state, data, params)
    jar._run(
        "record_srd_magic_jar_container",
        state,
        data,
        caster_state="second-soul",
        object_id="second-reliquary",
        material_item_id=second_params["spell_materials"][0]["id"],
    )
    choices = params | {
        "caster_state": "second-soul",
        "magic_jar_container": "second-reliquary",
        "material_item_ids": [second_params["spell_materials"][0]["id"]],
    }
    choices.pop("caster")
    choices.pop("slot_resource")
    jar._run("begin_srd_spell_cast", state, data, **jar._begin(choices))
    jar._run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state="second-soul",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    jar._run("cast_spell_outside_combat", state, data, **choices)
    assert state[f"{SRD}.state"]["magic_jar_container_ids"] == ["reliquary", "second-reliquary"]
    sources = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    before = deepcopy(next(e for e in sources if e["id"] == "second-reliquary"))
    monkeypatch.setattr(dice, "_rng", Random(0))
    jar._run("cast_spell_outside_combat", state, data, **_burst(params))
    sources = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    assert next(e for e in sources if e["id"] == "second-reliquary") == before
    assert state[f"{SRD}.entities"]["second-soul"]["magic_jar_role"] == "container"
    assert state[f"{SRD}.state"]["has_magic_jar_sources"] is True


def test_public_paid_jar_actual_flame_destroys_container_reopens_returns_and_retries(
    tmp_path, monkeypatch
):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(jar.binding, "PUBLIC_TOOLS", jar.binding.PUBLIC_TOOLS | jar.PUBLIC_TOOLS)
    monkeypatch.setattr(jar.public_casting, "bundled_srd_archive", jar.binding._archive)
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda _spell, **kwargs: _case())
    original_drafts = bundled_packages._srd_drafts

    def selected_drafts():
        primary, reference = original_drafts()
        runtime = yaml.load(
            (jar.budget.ROOT / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader
        )
        record = deepcopy(primary.record)
        record["tools"] = [t for t in record["tools"] if t["name"] in runtime["tools"]]
        payloads = dict(primary.payloads)
        path = record["content_declarations"]
        declarations = json.loads(payloads[path])
        for inclusion in declarations["inclusions"]:
            links = inclusion.get("links", {})
            if "tools" in links:
                links["tools"] = [name for name in links["tools"] if name in runtime["tools"]]
        payloads[path] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payloads, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", selected_drafts)
    original_create = AuthorityOwner.create_campaign

    def add_actual_host(owner, command):
        hero = command.participants[0]
        host = replace(hero, participant_id="guard", data=jar._host_artifact(hero.data))
        return original_create(owner, replace(command, participants=(*command.participants, host)))

    monkeypatch.setattr(AuthorityOwner, "create_campaign", add_actual_host)
    path = tmp_path / "jar-container.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "magic_jar")
        prepared, preparation = _act(
            owner,
            "prepare_srd_spell_object",
            object_id="reliquary",
            material="crystal",
            size="tiny",
            durability="fragile",
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
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
            owner,
            "project_srd_magic_jar_soul",
            caster_state="hero",
            host_state="guard",
            host_character="guard",
        )
        assert possessed.status is ActionStatus.COMMITTED, possessed.result
        assert possessed.result["context"]["_jar_possessed"] is True
        mage, _ = _act(
            owner, "prepare_srd_creature", **_npc("jar-breaker", "produce_flame", "humanoid")
        )
        assert mage.status is ActionStatus.COMMITTED, mage.result
        flame, flame_cast = _act(owner, "cast_spell_outside_combat", **_flame(params))
        assert flame.status is ActionStatus.COMMITTED, flame.result
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        monkeypatch.setattr(dice, "_rng", Random(0))
        destroyed, destruction = _act(
            owner,
            "hurl_srd_produce_flame",
            caster_state="jar-breaker",
            object_id="reliquary",
        )
        assert destroyed.status is ActionStatus.COMMITTED, destroyed.result
        final = owner.snapshot("casting")
        container = _container(final.state)
        assert container["metadata"]["srd_spell_object"]["hp"] == 0
        assert container["metadata"]["srd_magic_jar"]["installed"] is False
        assert container["active"] is False
        assert final.state[f"{SRD}.entities"]["hero"]["hp"] == 40
        assert final.state[f"{SRD}.entities"]["hero"]["magic_jar_role"] is None
        assert final.state[f"{SRD}.entities"]["guard"]["magic_jar_role"] is None
        for command, receipt in (
            (preparation, prepared),
            (casting, cast),
            (possession, possessed),
            (flame_cast, flame),
            (destruction, destroyed),
        ):
            assert owner.execute_action(command) == receipt and owner.snapshot("casting") == final
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
