"""Retained spellcasting uses the occupied body's actual supplies and one logical slot."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_magic_jar_materials import MATERIAL_OWNER, bind_material_consumers

from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_npc_spell_casting import _act

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from jar._compiled.__wrapped__(monkeypatch)


def _dust(identity, quantity=2):
    return {
        "id": identity,
        "inventory_item": identity,
        "kind": "specific",
        "components": ["stoneskin.1"],
        "quantity": quantity,
        "value_cp": 10000,
    }


def _case(*, host_quantity=2):
    state, data, params = jar._case()
    data["spellcasting_grants"][0]["spells"].append("stoneskin")
    data["wizard_spellbook"].append("stoneskin")
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    hero["prepared_spells"].append("stoneskin")
    hero["inventory"].append("remote-dust")
    hero["spell_materials"].append(_dust("remote-dust"))
    host["inventory"] = ["host-dust"]
    host["spell_materials"] = [_dust("host-dust", host_quantity)]
    return state, data, params


def _occupied(*, host_quantity=2):
    state, data, params = _case(host_quantity=host_quantity)
    jar._cast(state, data, params)
    assert jar._possess(state, data).result["context"]["_jar_possessed"] is True
    return state, data, params


def _stone(params, material="host-dust"):
    return params | {
        "spell": "stoneskin",
        "slot_resource": "spell_slot_4",
        "target": "hero",
        "targets": ["hero"],
        "target_distance": 0,
        "caster_can_touch_target": True,
        "material_item_ids": [material],
        "material_component_available": True,
    }


def test_remote_original_body_material_is_rejected_before_logical_cast_cost():
    state, data, params = _occupied()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="[Mm]aterial"):
        jar._run("cast_spell_outside_combat", state, data, **_stone(params, "remote-dust"))
    assert state == before


@pytest.mark.parametrize("quantity", [1, 2])
def test_actual_host_supply_spends_once_and_keeps_logical_slot_and_original_inventory(quantity):
    state, data, params = _occupied(host_quantity=quantity)
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    original_materials = deepcopy(hero["spell_materials"])
    original_inventory = deepcopy(hero["inventory"])
    host_resources = deepcopy(host["resources"])
    slots = hero["resources"]["spell_slot_4"]
    cast = jar._run("cast_spell_outside_combat", state, data, **_stone(params))
    assert cast.result["status"] == "committed"
    hero, host = (state[f"{SRD}.entities"][name] for name in ("hero", "guard"))
    assert hero["resources"]["spell_slot_4"] == slots - 1
    assert hero["spell_materials"] == original_materials
    assert hero["inventory"] == original_inventory
    assert host["spell_materials"][0]["quantity"] == quantity - 1
    assert host["inventory"] == (["host-dust"] if quantity > 1 else [])
    assert host["resources"] == host_resources
    for destination in ("container", "body"):
        jar._run(
            "return_srd_magic_jar_soul", state, data, caster_state="hero", destination=destination
        )
    assert state[f"{SRD}.entities"]["guard"]["spell_materials"][0]["quantity"] == quantity - 1
    assert state[f"{SRD}.entities"]["hero"]["inventory"] == original_inventory


def test_actual_validation_only_reads_host_stock_without_mutating_either_body():
    state, data, _ = _occupied()
    before = deepcopy(state)
    checked = jar._run(
        "resolve_srd_spell_materials",
        state,
        data,
        caster_state="hero",
        spell="stoneskin",
        item_ids=["host-dust"],
        materials_available=True,
        components_waived=False,
        target_count=1,
        permitted_focus_kinds=["arcane_focus"],
        validate_only=True,
    )
    assert checked.result["status"] == "committed"
    assert state == before


def test_material_binder_repeats_and_retains_final_ordinary_material_owner():
    text = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_material_consumers(text)
    second = bind_material_consumers(first)
    runtime = yaml.load(first, Loader=yaml.CSafeLoader)
    assert runtime == yaml.load(second, Loader=yaml.CSafeLoader)
    source_body = runtime["tools"][MATERIAL_OWNER]
    assert "jar_material_body.spell_materials" in str(source_body["effects"])
    fallback = runtime["tools"]["resolve_srd_spell_materials"]["effects"][0]["on_false"]
    assert "caster_state.spell_materials" in str(fallback)
    assert "resolve_srd_warding_bond_materials" in str(fallback)


def test_public_actual_host_material_cost_reopens_retries_and_keeps_original_stock(
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
        declarations = json.loads(payloads[record["content_declarations"]])
        for inclusion in declarations["inclusions"]:
            links = inclusion.get("links", {})
            if "tools" in links:
                links["tools"] = [name for name in links["tools"] if name in runtime["tools"]]
        payloads[record["content_declarations"]] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payloads, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", selected_drafts)
    original_create = AuthorityOwner.create_campaign

    def add_actual_host(owner, command):
        hero = command.participants[0]
        host = replace(hero, participant_id="guard", data=jar._host_artifact(hero.data))
        return original_create(owner, replace(command, participants=(*command.participants, host)))

    monkeypatch.setattr(AuthorityOwner, "create_campaign", add_actual_host)
    path = tmp_path / "jar-materials.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
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
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="[Mm]aterial"):
            _act(owner, "cast_spell_outside_combat", **_stone(params, "remote-dust"))
        assert owner.snapshot("casting") == before
        cast, casting = _act(owner, "cast_spell_outside_combat", **_stone(params))
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert saved.state[f"{SRD}.entities"]["guard"]["spell_materials"][0]["quantity"] == 1
        remote = next(
            lot
            for lot in saved.state[f"{SRD}.entities"]["hero"]["spell_materials"]
            if lot["id"] == "remote-dust"
        )
        assert remote["quantity"] == 2
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(casting) == cast and owner.snapshot("casting") == saved
        for destination in ("container", "body"):
            returned, _ = _act(
                owner, "return_srd_magic_jar_soul", caster_state="hero", destination=destination
            )
            assert returned.status is ActionStatus.COMMITTED, returned.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["spell_materials"][0]["quantity"] == 1
        assert (
            final.state[f"{SRD}.entities"]["hero"]["inventory"]
            == (before.state[f"{SRD}.entities"]["hero"]["inventory"])
        )
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
