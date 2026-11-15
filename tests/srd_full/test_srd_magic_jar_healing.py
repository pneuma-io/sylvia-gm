"""Healing an actual occupied body follows one caster and preserves its independent HP state."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_magic_jar_healing import HEALING_OWNER, bind_healing_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_magic_jar_circle import _npc
from tests.srd_full.test_srd_npc_spell_casting import _act

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from jar._compiled.__wrapped__(monkeypatch)


def _case(*, temp_hp=(0, 0)):
    state, data, params = jar._case()
    for actor, amount in zip(("hero", "guard"), temp_hp, strict=True):
        state[f"{SRD}.entities"][actor]["temp_hp"] = amount
    scene = state[f"{SRD}.state"]["spatial_scene"]
    healer = deepcopy(next(e for e in scene["entities"] if e["id"] == "guard"))
    healer.update(id="jar-healer", position=[3, -2, 0])
    healer["metadata"]["participant_ref"] = "jar-healer"
    scene["entities"].append(healer)
    return state, data, params


def _occupied(*, temp_hp=(0, 0), extra_spells=()):
    state, data, params = _case(temp_hp=temp_hp)
    caster = _npc("jar-healer", "cure_wounds", "humanoid")
    caster["spells"].extend({"spell": spell, "uses_per_day": 3} for spell in extra_spells)
    jar._run("prepare_srd_creature", state, data, **caster)
    jar._cast(state, data, params)
    assert jar._possess(state, data).result["context"]["_jar_possessed"] is True
    jar._run(
        "apply_srd_damage",
        state,
        data,
        target="guard",
        amount=12,
        adjustment=0,
        damage_type="force",
    )
    return state, data, params


def _cure(params, target="guard"):
    choices = params | {
        "caster_state": "jar-healer",
        "spell": "cure_wounds",
        "target": target,
        "material_item_ids": [],
        "target_distance": 3 if target == "guard" else 4,
        "caster_can_touch_target": True,
    }
    choices.pop("caster")
    choices.pop("slot_resource")
    return choices


def test_actual_host_target_healing_updates_the_one_occupied_hp_owner(monkeypatch):
    state, data, params = _occupied()
    monkeypatch.setattr(dice, "_rng", Random(1))
    jar._run("cast_spell_outside_combat", state, data, **_cure(params))
    entities = state[f"{SRD}.entities"]
    assert entities["hero"]["hp"] == entities["guard"]["hp"] == 57
    assert "incapacitated" in entities["guard"]["conditions"]


def test_actual_heal_clamps_current_body_hp_preserves_temporary_hp_and_return(monkeypatch):
    state, data, params = _occupied(temp_hp=(6, 9))
    monkeypatch.setattr(dice, "_rng", Random(1))
    jar._run("cast_spell_outside_combat", state, data, **_cure(params))
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    assert hero["hp"] == host["hp"] == 60
    assert hero["temp_hp"] == 0 and host["temp_hp"] == 9
    for destination in ("container", "body"):
        jar._run(
            "return_srd_magic_jar_soul", state, data, caster_state="hero", destination=destination
        )
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    assert hero["hp"] == 40 and host["hp"] == 60
    assert hero["temp_hp"] == 0 and host["temp_hp"] == 9


def test_healing_an_unrelated_actor_does_not_mutate_the_occupied_hp_owner(monkeypatch):
    state, data, params = _occupied()
    original = deepcopy(state[f"{SRD}.entities"]["hero"])
    monkeypatch.setattr(dice, "_rng", Random(1))
    jar._run("cast_spell_outside_combat", state, data, **_cure(params, "jar-healer"))
    assert state[f"{SRD}.entities"]["hero"] == original


@pytest.mark.parametrize("body_target", ["guard", "hero"])
def test_actual_chill_touch_on_current_body_blocks_host_healing(monkeypatch, body_target):
    state, data, params = _occupied(temp_hp=(0, 20), extra_spells=("chill_touch",))
    monkeypatch.setattr(dice, "_rng", Random(0))
    hit = jar._run(
        "cast_spell_outside_combat",
        state,
        data,
        **(_cure(params, body_target) | {"spell": "chill_touch"}),
    )
    assert hit.result["context"]["_spell_attack_outcome"] == "success"
    entities = state[f"{SRD}.entities"]
    assert entities[body_target]["hit_point_recovery_blocked"] is True
    hp = entities["hero"]["hp"]
    monkeypatch.setattr(dice, "_rng", Random(1))
    jar._run("cast_spell_outside_combat", state, data, **_cure(params))
    assert state[f"{SRD}.entities"]["hero"]["hp"] == hp
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    if body_target == "guard":
        for destination in ("container", "body"):
            jar._run(
                "return_srd_magic_jar_soul",
                state,
                data,
                caster_state="hero",
                destination=destination,
            )
        jar._run(
            "apply_srd_damage",
            state,
            data,
            target="hero",
            amount=12,
            adjustment=0,
            damage_type="force",
        )
        monkeypatch.setattr(dice, "_rng", Random(1))
        jar._run("cast_spell_outside_combat", state, data, **_cure(params, "hero"))
        assert state[f"{SRD}.entities"]["hero"]["hp"] == 37
        assert state[f"{SRD}.entities"]["hero"].get("hit_point_recovery_blocked") is not True


def test_actual_beacon_on_host_maximizes_healing_without_moving_its_source(monkeypatch):
    state, data, params = _occupied(extra_spells=("beacon_of_hope",))
    jar._run(
        "cast_spell_outside_combat",
        state,
        data,
        **(_cure(params) | {"spell": "beacon_of_hope", "targets": ["guard"]}),
    )
    entities = state[f"{SRD}.entities"]
    assert entities["guard"]["spell_beacon_of_hope"] is True
    sources = deepcopy(state[f"{SRD}.state"]["turn_effects"])
    monkeypatch.setattr(dice, "_rng", Random(1))
    jar._run("cast_spell_outside_combat", state, data, **_cure(params))
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 60
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 60
    assert state[f"{SRD}.state"]["turn_effects"] == sources
    for destination in ("container", "body"):
        jar._run(
            "return_srd_magic_jar_soul", state, data, caster_state="hero", destination=destination
        )
    jar._run(
        "apply_srd_damage", state, data, target="hero", amount=12, adjustment=0, damage_type="force"
    )
    monkeypatch.setattr(dice, "_rng", Random(1))
    jar._run("cast_spell_outside_combat", state, data, **_cure(params, "hero"))
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 37
    assert state[f"{SRD}.entities"]["guard"]["spell_beacon_of_hope"] is True


def test_container_soul_cannot_be_targeted_for_hp_healing(monkeypatch):
    state, data, params = _case()
    jar._run("prepare_srd_creature", state, data, **_npc("jar-healer", "cure_wounds", "humanoid"))
    jar._cast(state, data, params)
    original = deepcopy(state)
    monkeypatch.setattr(dice, "_rng", Random(1))
    with pytest.raises(RulesetPreconditionRejected, match="soul is in the container"):
        jar._run("cast_spell_outside_combat", state, data, **_cure(params, "hero"))
    assert state == original


def test_healing_binder_retains_final_owner_callbacks_and_repeats():
    text = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    first = bind_healing_consumers(text)
    second = bind_healing_consumers(first)
    data = yaml.load(first, Loader=loader)
    assert data == yaml.load(second, Loader=loader)
    delegated = data["tools"][HEALING_OWNER]
    fallback = data["tools"]["restore_hit_points"]["effects"][0]["on_false"]
    assert "jar_healing_host.hit_point_recovery_blocked" in str(delegated["effects"])
    assert "jar_healing_host" not in str(fallback)
    assert "observe_srd_time_stop_creature_effect" in str(delegated["effects"])


def test_public_paid_jar_and_cure_real_roll_host_healing_reopen_retry_and_return(
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
    path = tmp_path / "jar-healing.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "magic_jar")
        begin, _ = _act(owner, "begin_srd_spell_cast", **jar._begin(params))
        assert begin.status is ActionStatus.COMMITTED, begin.result
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
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
        choices = _npc("jar-healer", "cure_wounds", "humanoid")
        choices["spells"].append({"spell": "magic_missile", "uses_per_day": 3})
        mage, _ = _act(owner, "prepare_srd_creature", **choices)
        assert mage.status is ActionStatus.COMMITTED, mage.result
        missile = _cure(params) | {"spell": "magic_missile", "projectile_targets": ["guard"] * 3}
        damage, damaging = _act(owner, "cast_spell_outside_combat", **missile)
        assert damage.status is ActionStatus.COMMITTED, damage.result
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        before_hp = saved.state[f"{SRD}.entities"]["hero"]["hp"]
        monkeypatch.setattr(dice, "_rng", Random(1))
        healed, healing = _act(owner, "cast_spell_outside_combat", **_cure(params))
        assert healed.status is ActionStatus.COMMITTED, healed.result
        after = owner.snapshot("casting")
        hp = min(60, before_hp + 9)
        assert after.state[f"{SRD}.entities"]["hero"]["hp"] == hp
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == hp
        assert "incapacitated" in after.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert (
            after.state[f"{SRD}.entities"]["jar-healer"]["spell_uses_remaining"]["cure_wounds"] == 2
        )
        for command, receipt in (
            (casting, cast),
            (possession, possessed),
            (damaging, damage),
            (healing, healed),
        ):
            assert owner.execute_action(command) == receipt and owner.snapshot("casting") == after
        for destination in ("container", "body"):
            returned, _ = _act(
                owner, "return_srd_magic_jar_soul", caster_state="hero", destination=destination
            )
            assert returned.status is ActionStatus.COMMITTED, returned.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["hero"]["hp"] == 40
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == hp
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
