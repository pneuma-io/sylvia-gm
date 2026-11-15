"""Magic Jar uses the actual named Circle's chosen source type, geometry and lifetime."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_result_cards import _casting_headers

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from jar._compiled.__wrapped__(monkeypatch)


def _materials(spell):
    return [
        {
            "id": f"{spell}-supplies-{i}",
            "inventory_item": f"{spell}-supplies-{i}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"] * 3,
            "value_cp": part["minimum_value_cp"],
        }
        for i, part in enumerate(_casting_headers()[spell]["material_parts"])
    ]


def _npc(actor, spell, creature_type):
    materials = _materials(spell)
    return {
        "creature_id": actor,
        "name": actor,
        "source": f"Synthetic {creature_type} spellcaster: actual saved native abilities,5d8,DC16.",
        "creature_type": creature_type,
        "armor_class": 12,
        "hit_points": 25,
        "speed": 30,
        "size_rank": 2,
        "initiative_modifier": 0,
        "strength_score": 11,
        "dexterity_score": 14,
        "constitution_score": 12,
        "intelligence_score": 18,
        "wisdom_score": 12,
        "charisma_score": 16,
        "strength_save": 0,
        "dexterity_save": 2,
        "constitution_save": 1,
        "intelligence_save": 4,
        "wisdom_save": 1,
        "charisma_save": 3,
        "native_hit_dice_count": 5,
        "native_hit_die_size": 8,
        "spellcasting_ability": "intelligence",
        "spellcasting_modifier": 4,
        "spell_attack_bonus": 8,
        "spell_save_dc": 16,
        "inventory": [m["inventory_item"] for m in materials],
        "spell_materials": materials,
        "spells": [{"spell": spell, "uses_per_day": 3}],
    }


def _case(*, host_position=(5, 0, 0)):
    state, data, params = jar._case(bind=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    actual = next(e for e in scene["entities"] if e["id"] == "hero")
    for actor, position in (("soul-caster", [0, 0, 0]), ("ward-caster", [0, -4, 0])):
        body = deepcopy(actual)
        body.update(id=actor, position=position)
        body["metadata"]["participant_ref"] = actor
        scene["entities"].append(body)
    next(e for e in scene["entities"] if e["id"] == "guard")["position"] = list(host_position)
    scene["geometry"].append(
        {
            "id": "floor",
            "map_id": scene["map"]["id"],
            "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": False,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True, "material": "stone"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    params.pop("caster")
    params.pop("slot_resource")
    params["caster_state"] = "soul-caster"
    params["material_item_ids"] = [m["id"] for m in _materials("magic_jar")]
    return state, data, params


def _circle_params(params, *, reverse=False, chosen="fiend", origin=(5, 0, 0)):
    return params | {
        "caster_state": "ward-caster",
        "spell": "magic_circle",
        "magic_jar_container": "",
        "target": "hero",
        "material_item_ids": [m["id"] for m in _materials("magic_circle")],
        "secure_area_origin": list(origin),
        "secure_area_types": [chosen],
        "secure_area_reverse": reverse,
        "caster_can_see_origin": True,
    }


def _paid(state, data, params):
    caster = params["caster_state"]
    jar._run("begin_srd_spell_cast", state, data, **_begin(params))
    jar._run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state=caster,
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return jar._run("cast_spell_outside_combat", state, data, **params)


def _installed(*, causing_type="fiend", host_position=(5, 0, 0), reverse=False, chosen="fiend"):
    state, data, params = _case(host_position=host_position)
    for actor, spell, kind in (
        ("soul-caster", "magic_jar", causing_type),
        ("ward-caster", "magic_circle", "humanoid"),
    ):
        jar._run("prepare_srd_creature", state, data, **_npc(actor, spell, kind))
    jar._run(
        "record_srd_magic_jar_container",
        state,
        data,
        caster_state="soul-caster",
        object_id="reliquary",
        material_item_id=params["material_item_ids"][0],
    )
    _paid(state, data, _circle_params(params, reverse=reverse, chosen=chosen))
    _paid(state, data, params)
    return state, data, params


def _project(state, data):
    return jar._run(
        "project_srd_magic_jar_soul",
        state,
        data,
        caster_state="soul-caster",
        host_state="guard",
        host_character="guard",
    )


def test_real_circle_blocks_selected_causing_type_without_save_but_spends_magic_action():
    state, data, _ = _installed()
    before = dice._rng.getstate()
    tick = state[f"{SRD}.state"]["lasting_tick"]
    result = _project(state, data)
    assert result.result["context"]["_jar_possessed"] is False
    assert result.result["context"]["_d20_test"] is None
    assert dice._rng.getstate() == before
    assert state[f"{SRD}.state"]["lasting_tick"] == tick + 2
    assert jar._source(state)["mode"] == "container"
    assert jar._source(state)["resisted_hosts"] == []


@pytest.mark.parametrize("causing_type,chosen", [("humanoid", "fiend"), ("fiend", "undead")])
def test_circle_does_not_invent_protection_against_an_unselected_causing_type(causing_type, chosen):
    state, data, _ = _installed(causing_type=causing_type, chosen=chosen)
    result = _project(state, data)
    assert result.result["context"]["_d20_test"] is not None
    assert result.result["context"]["_jar_possessed"] is True


@pytest.mark.parametrize(
    "reverse,host_position,blocked",
    [(False, (20, 0, 0), False), (True, (20, 0, 0), True), (True, (5, 0, 0), False)],
)
def test_normal_and_reversed_circle_use_actual_container_and_host_positions(
    reverse, host_position, blocked
):
    state, data, _ = _installed(reverse=reverse, host_position=host_position)
    result = _project(state, data)
    assert result.result["context"]["_jar_possessed"] is not blocked


def test_real_circle_expiry_restores_the_actual_possession_save_path():
    state, data, _ = _installed()
    _project(state, data)
    jar._run("advance_srd_lasting_time", state, data, elapsed_rounds=600)
    result = _project(state, data)
    assert result.result["context"]["_jar_possessed"] is True
    assert all(
        not s["installed"] for s in state[f"{SRD}.state"]["spell_secure_area_sources"].values()
    )


def test_exact_circle_removal_preserves_another_actual_casts_possession_protection():
    state, data, params = _installed()
    first = next(iter(state[f"{SRD}.state"]["spell_secure_area_sources"]))
    _paid(state, data, _circle_params(params))
    second = next(reversed(state[f"{SRD}.state"]["spell_secure_area_sources"]))
    assert first != second
    jar._run("end_srd_secure_area_source", state, data, source_id=first)
    assert _project(state, data).result["context"]["_jar_possessed"] is False
    jar._run("end_srd_secure_area_source", state, data, source_id=second)
    assert _project(state, data).result["context"]["_jar_possessed"] is True


def test_public_paid_circle_and_jar_reopen_real_costs_block_expiry_and_cached_retry(
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
        # The focused fragment omits unrelated public roots with no incoming calls.
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
    path = tmp_path / "circle-jar.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "magic_jar")
        for actor, spell, kind in (
            ("soul-caster", "magic_jar", "fiend"),
            ("ward-caster", "magic_circle", "humanoid"),
        ):
            receipt, _ = _act(owner, "prepare_srd_creature", **_npc(actor, spell, kind))
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(
            owner,
            "record_srd_magic_jar_container",
            caster_state="soul-caster",
            object_id="reliquary",
            material_item_id=params["material_item_ids"][0],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        paid_commands = []
        for choices in (_circle_params(params), params):
            receipt, _ = _act(owner, "begin_srd_spell_cast", **_begin(choices))
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            receipt, _ = _act(
                owner,
                "continue_srd_spell_cast",
                caster_state=choices["caster_state"],
                elapsed_rounds=10,
                uninterrupted=True,
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
            )
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            receipt, command = _act(owner, "cast_spell_outside_combat", **choices)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            paid_commands.append((command, receipt))
        saved = owner.snapshot("casting")
        ward = saved.state[f"{SRD}.entities"]["ward-caster"]
        soul = saved.state[f"{SRD}.entities"]["soul-caster"]
        assert ward["spell_uses_remaining"]["magic_circle"] == 2
        assert ward["spell_materials"][0]["quantity"] == 2
        assert soul["spell_uses_remaining"]["magic_jar"] == 2
        assert soul["spell_materials"][0]["quantity"] == 3
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        blocked, block_command = _act(
            owner,
            "project_srd_magic_jar_soul",
            caster_state="soul-caster",
            host_state="guard",
            host_character="guard",
        )
        assert blocked.status is ActionStatus.COMMITTED, blocked.result
        assert blocked.result["context"]["_jar_possessed"] is False
        paused = owner.snapshot("casting")
        for command, receipt in [*paid_commands, (block_command, blocked)]:
            assert owner.execute_action(command) == receipt and owner.snapshot("casting") == paused
        advanced, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=600)
        assert advanced.status is ActionStatus.COMMITTED, advanced.result
        possessed, command = _act(
            owner,
            "project_srd_magic_jar_soul",
            caster_state="soul-caster",
            host_state="guard",
            host_character="guard",
        )
        assert possessed.status is ActionStatus.COMMITTED, possessed.result
        assert possessed.result["context"]["_jar_possessed"] is True
        final = owner.snapshot("casting")
        assert owner.execute_action(command) == possessed and owner.snapshot("casting") == final
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
