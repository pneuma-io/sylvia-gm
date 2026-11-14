"""Actual possession uses host-carried gear and retained caster combat grants."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_magic_jar_equipment import EQUIPMENT_OWNER, bind_equipment_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_magic_jar_circle import _npc
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_reaction_windows import _parameters

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from jar._compiled.__wrapped__(monkeypatch)


def _case(*, worn_armor=None, worn_shield=None):
    state, data, params = jar._case()
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    hero["inventory"].append("dagger")
    hero.update(held_items=["dagger"], held_attack_names=["dagger"])
    hero["initiative_modifier"] = (data["abilities"]["dexterity"] - 10) // 2
    host.update(inventory=["quarterstaff"], held_items=[], held_attack_names=[])
    for slot, item in (("worn_armor", worn_armor), ("worn_shield", worn_shield)):
        host[slot] = item
        if item:
            host["inventory"].append(item["inventory_item"])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    victim = deepcopy(next(e for e in scene["entities"] if e["id"] == "guard"))
    victim.update(id="jar-victim", position=[8, 0, 0])
    victim["metadata"]["participant_ref"] = "jar-victim"
    scene["entities"].append(victim)
    return state, data, params


def _occupied(**choices):
    state, data, params = _case(**choices)
    jar._cast(state, data, params)
    assert jar._possess(state, data).result["context"]["_jar_possessed"] is True
    return state, data, params


def _holding(state, data, items):
    return jar._run(
        "record_srd_control_held_items",
        state,
        data,
        target_state="hero",
        held_items=items,
        held_attack_names=items,
    )


def _combat(state, data):
    npc = _npc("jar-victim", "magic_missile", "humanoid")
    npc.update(armor_class=10, hit_points=1000, initiative_modifier=-10)
    jar._run("prepare_srd_creature", state, data, **npc)
    jar._run(
        "start_combat_encounter_entities",
        state,
        data,
        first_actor="hero",
        second_actor="jar-victim",
        tie_winner="first",
    )
    jar._run("begin_combat_turn", state, data, actor="hero")


def _attack(state, data, **choices):
    params = _parameters() | {"weapon": "quarterstaff", "target": "jar-victim"} | choices
    return jar._run("begin_srd_character_attack", state, data, **params)


def test_remote_original_body_weapon_rejects_before_real_attack_cost():
    state, data, _ = _occupied()
    _combat(state, data)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actually carry and hold"):
        _attack(state, data)
    assert state == before


def test_logical_holding_and_drop_use_exact_host_stock_and_preserve_original_body():
    state, data, _ = _occupied()
    original = deepcopy(state[f"{SRD}.entities"]["hero"])
    _holding(state, data, ["quarterstaff"])
    host = state[f"{SRD}.entities"]["guard"]
    assert host["held_items"] == ["quarterstaff"]
    assert state[f"{SRD}.entities"]["hero"] == original
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="admitted inventory"):
        _holding(state, data, ["dagger"])
    assert state == before
    jar._run("drop_srd_control_held_items", state, data, target_state="hero")
    host = state[f"{SRD}.entities"]["guard"]
    assert host["inventory"] == [] and host["held_items"] == []
    assert host["dropped_items"] == ["quarterstaff"]
    assert state[f"{SRD}.entities"]["hero"] == original
    for destination in ("container", "body"):
        jar._run(
            "return_srd_magic_jar_soul", state, data, caster_state="hero", destination=destination
        )
    assert state[f"{SRD}.entities"]["hero"]["held_items"] == ["dagger"]
    assert state[f"{SRD}.entities"]["guard"]["dropped_items"] == ["quarterstaff"]


def test_actual_host_weapon_roll_uses_host_strength_and_only_caster_proficiency(monkeypatch):
    state, data, _ = _occupied()
    _holding(state, data, ["quarterstaff"])
    _combat(state, data)
    monkeypatch.setattr(dice, "_rng", Random(1))
    hit = _attack(state, data)
    event_id = hit.result["context"]["_pending_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    saved_roll = deepcopy(event["attack_roll"])
    context = event["context"]
    assert context["_ability_modifier"] == 5
    proficient = any(
        p in data.get("weapon_proficiencies", []) for p in ("quarterstaff", "simple_weapons")
    )
    assert context["_weapon_proficiency_modifier"] == (
        2 + (data["level"] - 1) // 4 if proficient else 0
    )
    assert state[f"{SRD}.combat"]["action_available"] is False
    completed = jar._run("complete_srd_pending_action", state, data, pending_action_id=event_id)
    assert completed.result["context"]["_attack_roll"] == saved_roll
    assert state[f"{SRD}.entities"]["jar-victim"]["hp"] < 1000


def test_physical_host_worn_armor_uses_retained_caster_training_and_projected_dexterity():
    armor = {"inventory_item": "host-leather", "armor_type": "leather_armor", "magic_bonus": 0}
    state, data, _ = _occupied(worn_armor=armor)
    result = jar._run("resolve_srd_equipment", state, data, actor="hero", actor_state="hero")
    context = result.result["context"]
    assert context["_equipment_ac"] == 10  # Leather11 + actual host Dexterity8 (-1).
    assert (
        context["_equipment_untrained_armor"] is True
    )  # The retained Wizard lacks armor training.
    assert state[f"{SRD}.entities"]["hero"].get("worn_armor") is None
    assert state[f"{SRD}.entities"]["guard"]["worn_armor"] == armor


@pytest.mark.parametrize("armored", [False, True])
def test_actual_attack_conditions_use_host_armor_penalty_without_copying_flags(armored):
    armor = {"inventory_item": "host-leather", "armor_type": "leather_armor", "magic_bonus": 0}
    state, data, _ = _occupied(worn_armor=armor if armored else None)
    state[f"{SRD}.entities"]["hero"]["armor_untrained"] = not armored
    _holding(state, data, ["quarterstaff"])
    _combat(state, data)
    result = _attack(state, data)
    event = state[f"{SRD}.state"]["pending_srd_actions"][
        result.result["context"]["_pending_action_id"]
    ]
    roll = event["attack_roll"]
    assert roll["count"] == (2 if armored else 1)
    assert roll["total"] == min(roll["results"])
    assert state[f"{SRD}.entities"]["hero"]["armor_untrained"] is (not armored)


def test_actual_host_shield_rejects_two_hand_attack_before_budget():
    shield = {"inventory_item": "host-shield", "armor_type": "shield", "magic_bonus": 0}
    state, data, _ = _occupied(worn_shield=shield)
    _holding(state, data, ["quarterstaff"])
    _combat(state, data)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="occupied body's wielded shield"):
        _attack(state, data, wielding_two_handed=True)
    assert state == before


@pytest.mark.parametrize("host_strength", [8, 20])
def test_heavy_weapon_threshold_uses_actual_host_strength(host_strength):
    state, data, params = _case()
    host = state[f"{SRD}.entities"]["guard"]
    host["inventory"].append("greatsword")
    jar._cast(state, data, params)
    host_abilities = jar._host_artifact(data)["abilities"] | {"strength": host_strength}
    possessed = jar._run(
        "project_srd_magic_jar_soul",
        state,
        data,
        host_overrides={"abilities": host_abilities},
        caster_state="hero",
        host_state="guard",
        host_character="guard",
    )
    assert possessed.result["context"]["_jar_possessed"] is True
    _holding(state, data, ["greatsword"])
    _combat(state, data)
    result = _attack(state, data, weapon="greatsword", wielding_two_handed=True)
    event = state[f"{SRD}.state"]["pending_srd_actions"][
        result.result["context"]["_pending_action_id"]
    ]
    assert event["attack_roll"]["count"] == (2 if host_strength < 13 else 1)
    assert event["context"]["_ability_modifier"] == (host_strength - 10) // 2


def test_original_remote_light_stock_does_not_grant_an_extra_host_attack():
    state, data, _ = _occupied()
    state[f"{SRD}.entities"]["hero"]["inventory"].append("club")
    state[f"{SRD}.entities"]["guard"]["inventory"].append("dagger")
    _holding(state, data, ["dagger"])
    _combat(state, data)
    _attack(state, data, weapon="dagger")
    assert state[f"{SRD}.combat"].get("light_attack_followup_available") is not True
    assert state[f"{SRD}.entities"]["hero"].get("light_extra_attack_available") is not True


def test_equipment_binder_repeats_without_replacing_final_attack_or_release_callbacks():
    text = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_equipment_consumers(text)
    runtime = yaml.load(first, Loader=yaml.CSafeLoader)
    assert runtime == yaml.load(bind_equipment_consumers(first), Loader=yaml.CSafeLoader)
    assert "jar_equipment_body.worn_armor" in str(runtime["tools"][EQUIPMENT_OWNER])
    assert "retire_srd_released_shillelagh" in str(
        runtime["tools"]["record_srd_magic_jar_body_held_items"]
    )
    assert "begin_srd_time_stop_action_effect" in str(
        runtime["tools"]["begin_srd_character_attack"]
    )
    assert "apply_srd_qualifying_damage" in str(runtime["tools"]["complete_srd_character_attack"])


def test_public_paid_jar_host_gear_real_attack_reopens_cost_roll_and_release_retries(
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

    def add_host(owner, command):
        hero = command.participants[0]
        host = replace(hero, participant_id="guard", data=jar._host_artifact(hero.data))
        return original_create(owner, replace(command, participants=(*command.participants, host)))

    monkeypatch.setattr(AuthorityOwner, "create_campaign", add_host)
    database = tmp_path / "jar-equipment.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "magic_jar")
        for name, choices in (
            ("begin_srd_spell_cast", jar._begin(params)),
            (
                "continue_srd_spell_cast",
                {
                    "caster_state": "hero",
                    "elapsed_rounds": 10,
                    "uninterrupted": True,
                    "verbal_component_available": True,
                    "somatic_component_available": True,
                    "material_component_available": True,
                },
            ),
            ("cast_spell_outside_combat", params),
            (
                "project_srd_magic_jar_soul",
                {"caster_state": "hero", "host_state": "guard", "host_character": "guard"},
            ),
        ):
            receipt, _ = _act(owner, name, **choices)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        npc = _npc("jar-victim", "magic_missile", "humanoid")
        npc.update(armor_class=10, hit_points=1000, initiative_modifier=-10)
        prepared, _ = _act(owner, "prepare_srd_creature", **npc)
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        monkeypatch.setattr(dice, "_rng", Random(2))
        initiated, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor="jar-victim",
            tie_winner="first",
            first_surprised=False,
            second_surprised=False,
        )
        assert initiated.status is ActionStatus.COMMITTED, initiated.result
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        attack_choices = _parameters() | {"weapon": "quarterstaff", "target": "jar-victim"}
        attack_choices.pop(
            "actor_state"
        )  # Public actor-state binding follows the actual participant.
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            _act(owner, "begin_srd_character_attack", **attack_choices)
        assert owner.snapshot("casting") == before
        held, holding = _act(
            owner,
            "record_srd_control_held_items",
            target_state="hero",
            held_items=["quarterstaff"],
            held_attack_names=["quarterstaff"],
        )
        assert held.status is ActionStatus.COMMITTED, held.result
        monkeypatch.setattr(dice, "_rng", Random(1))
        attack, attacking = _act(owner, "begin_srd_character_attack", **attack_choices)
        assert attack.status is ActionStatus.COMMITTED, attack.result
        event_id = attack.result["context"]["_pending_action_id"]
        saved = owner.snapshot("casting")
        event = saved.state[f"{SRD}.state"]["pending_srd_actions"][event_id]
        original_roll = deepcopy(event["attack_roll"])
        assert event["context"]["_ability_modifier"] == 5
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(attacking) == attack and owner.snapshot("casting") == saved
        finished, finishing = _act(owner, "complete_srd_pending_action", pending_action_id=event_id)
        assert finished.status is ActionStatus.COMMITTED, finished.result
        after = owner.snapshot("casting")
        assert finished.result["context"]["_attack_roll"] == original_roll
        assert after.state[f"{SRD}.entities"]["jar-victim"]["hp"] < 1000
        released, releasing = _act(
            owner,
            "record_srd_control_held_items",
            target_state="hero",
            held_items=[],
            held_attack_names=[],
        )
        assert released.status is ActionStatus.COMMITTED, released.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["held_items"] == []
        assert "quarterstaff" in final.state[f"{SRD}.entities"]["guard"]["inventory"]
        assert "dagger" in final.state[f"{SRD}.entities"]["hero"]["inventory"]
        assert "quarterstaff" not in final.state[f"{SRD}.entities"]["hero"]["inventory"]
        for command, receipt in (
            (holding, held),
            (attacking, attack),
            (finishing, finished),
            (releasing, released),
        ):
            assert owner.execute_action(command) == receipt and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
