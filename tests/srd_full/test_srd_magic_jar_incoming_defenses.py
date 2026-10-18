"""Actual occupied-body armor reaches incoming saved attacks without moving spell sources."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_magic_jar_incoming_defenses import (
    ARMOR_OWNER,
    bind_incoming_defense_consumers,
)

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests import test_srd_magic_jar_equipment as equipment
from tests.srd_full.test_srd_magic_jar_circle import _npc
from tests.srd_full.test_srd_npc_spell_casting import _act

SRD = jar.SRD
PLATE = {"inventory_item": "host-plate", "armor_type": "plate_armor", "magic_bonus": 0}
SHIELD = {"inventory_item": "host-shield", "armor_type": "shield", "magic_bonus": 0}


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from jar._compiled.__wrapped__(monkeypatch)


def _armor(state, data, target, base):
    return jar._run(
        "resolve_srd_effective_armor_class",
        state,
        data,
        target_state=target,
        lasting_state="state",
        base_armor_class=base,
    ).result["context"]["_effective_armor_class"]


@pytest.mark.parametrize("target", ["hero", "guard"])
def test_incoming_body_armor_uses_exact_host_gear_and_retained_caster_training(target):
    state, data, _ = equipment._occupied(worn_armor=PLATE, worn_shield=SHIELD)
    original = deepcopy(state[f"{SRD}.entities"])
    assert _armor(state, data, target, 99) == 18
    # Actual Wizard training gives no shield bonus; stale body/native AC is not an input verdict.
    assert state[f"{SRD}.entities"] == original
    for destination in ("container", "body"):
        jar._run(
            "return_srd_magic_jar_soul", state, data, caster_state="hero", destination=destination
        )
    assert _armor(state, data, "hero", 12) == 12
    assert state[f"{SRD}.entities"]["guard"]["worn_armor"] == PLATE
    assert state[f"{SRD}.entities"]["hero"].get("worn_armor") is None


@pytest.mark.parametrize("armored", [False, True])
def test_actual_mage_armor_source_uses_body_dexterity_and_armor_limit(armored):
    leather = {"inventory_item": "host-leather", "armor_type": "leather_armor", "magic_bonus": 0}
    state, data, params = equipment._case(worn_armor=leather if armored else None)
    data["spellcasting_grants"][0]["spells"].append("mage_armor")
    data["wizard_spellbook"].append("mage_armor")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("mage_armor")
    mage_state, _, mage = jar.cards._spell_case("mage_armor")
    supply = deepcopy(mage_state[f"{SRD}.entities"]["hero"]["spell_materials"][0])
    supply["id"] = "actual-mage-armor-leather"
    state[f"{SRD}.entities"]["hero"]["spell_materials"].append(supply)
    state[f"{SRD}.entities"]["hero"]["inventory"].append(supply["inventory_item"])
    mage.update(target="hero", material_item_ids=[supply["id"]])
    jar._run("cast_spell_outside_combat", state, data, **mage)
    actual = next(
        e for e in state[f"{SRD}.state"]["turn_effects"] if e.get("spell") == "mage_armor"
    )
    before = deepcopy(actual)
    jar._cast(state, data, params)
    assert jar._possess(state, data).result["context"]["_jar_possessed"] is True
    assert _armor(state, data, "hero", 99) == (10 if armored else 12)
    # Equipment qualification does not retire or rewrite the independently saved spell receipt.
    after = next(e for e in state[f"{SRD}.state"]["turn_effects"] if e.get("spell") == "mage_armor")
    assert after == before


def test_final_incoming_binder_repeats_and_keeps_recipient_specific_defense_callbacks():
    text = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_incoming_defense_consumers(text)
    runtime = yaml.load(first, Loader=yaml.CSafeLoader)
    assert runtime == yaml.load(bind_incoming_defense_consumers(first), Loader=yaml.CSafeLoader)
    armor = str(runtime["tools"][ARMOR_OWNER])
    for owner in (
        "resolve_srd_reaction_shield",
        "resolve_srd_spell_ward_benefits",
        "resolve_srd_concentration_armor_class_source",
    ):
        assert owner in armor
    assert "jar_armor_host.worn_armor" in str(
        runtime["tools"]["resolve_srd_magic_jar_armor_defenses"]
    )


def _public_fixture(monkeypatch):
    """Reuse the existing paid Jar archive fixture with only this actual AC owner composed."""
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    assert parent
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
    monkeypatch.setattr(jar.binding, "PUBLIC_TOOLS", jar.binding.PUBLIC_TOOLS | jar.PUBLIC_TOOLS)
    monkeypatch.setattr(jar.public_casting, "bundled_srd_archive", jar.binding._archive)
    monkeypatch.setattr(
        jar.public_casting,
        "_spell_case",
        lambda _spell, **kwargs: equipment._case(worn_armor=PLATE, worn_shield=SHIELD),
    )
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


def test_public_paid_possession_incoming_body_armor_saved_attack_reopens_retries_and_returns(
    tmp_path, monkeypatch
):
    _public_fixture(monkeypatch)
    database = tmp_path / "jar-incoming-defense.db"
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
        npc.update(initiative_modifier=10)
        prepared, _ = _act(owner, "prepare_srd_creature", **npc)
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
        monkeypatch.setattr(dice, "_rng", Random(5))
        encounter, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="jar-victim",
            second_actor="hero",
            tie_winner="first",
            first_surprised=False,
            second_surprised=False,
        )
        assert encounter.status is ActionStatus.COMMITTED, encounter.result
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        combat = owner.snapshot("casting").state[f"{SRD}.combat"]
        assert combat["active_actor"] == "jar-victim", combat
        monkeypatch.setattr(dice, "_rng", Random(0))
        incoming, attacking = _act(
            owner,
            "begin_srd_creature_attack",
            target="guard",
            attack_name="Admitted club attack",
            attack_modifier=0,
            damage_expression="d4",
            damage_modifier=0,
            damage_type="bludgeoning",
            advantage=False,
            disadvantage=False,
            attack_distance=5,
            attack_mode="melee",
            normal_range=5,
            long_range=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
        )
        assert incoming.status is ActionStatus.COMMITTED, incoming.result
        event_id = incoming.result["context"]["_pending_action_id"]
        saved = owner.snapshot("casting")
        event = saved.state[f"{SRD}.state"]["pending_srd_actions"][event_id]
        assert event["attack_roll"]["total"] == 13
        assert event["attack_outcome"] == "miss"
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        original_roll = deepcopy(event["attack_roll"])
        original_hp = saved.state[f"{SRD}.entities"]["hero"]["hp"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(attacking) == incoming and owner.snapshot("casting") == saved
        completed, completing = _act(
            owner, "complete_srd_pending_action", pending_action_id=event_id
        )
        assert completed.status is ActionStatus.COMMITTED, completed.result
        assert completed.result["context"]["_attack_roll"] == original_roll
        assert completed.result["context"]["_attack_outcome"] == "miss"
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["hp"] == original_hp
        ended, _ = _act(owner, "end_combat_encounter", end_reason="fighting_stopped")
        assert ended.status is ActionStatus.COMMITTED, ended.result
        for destination in ("container", "body"):
            returned, _ = _act(
                owner, "return_srd_magic_jar_soul", caster_state="hero", destination=destination
            )
            assert returned.status is ActionStatus.COMMITTED, returned.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["hero"].get("worn_armor") is None
        assert final.state[f"{SRD}.entities"]["guard"]["worn_armor"] == PLATE
        for command, receipt in ((attacking, incoming), (completing, completed)):
            assert owner.execute_action(command) == receipt and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
