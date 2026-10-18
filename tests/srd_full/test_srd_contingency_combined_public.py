"""Current paid Source, completed damage, deferred child and occupied Jar body compose."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from tests.srd_full.full_package_fixture import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.srd_full import test_srd_public_spell_casting as public_casting
from tests.srd_full import test_srd_solid_spell_walls as schema_owner
from tests.srd_full import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_npc_spell_casting import _act

ROOT = Path(__file__).resolve().parents[2]
SRD = "wotc.srd.5.2.1"
PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_COMBINED_RUNTIME",
        ROOT / ".scratch/contingency-combined-v1/selected-runtime.yaml",
    )
)
PUBLIC = {
    "cast_spell_outside_combat",
    "begin_srd_spell_cast",
    "continue_srd_spell_cast",
    "record_srd_magic_jar_container",
    "project_srd_magic_jar_soul",
    "return_srd_magic_jar_soul",
    "set_srd_respiration",
    "advance_srd_lasting_time",
}


@lru_cache
def _runtime():
    return _load_runtime(PROOF.read_text())


@lru_cache
def _selected_archive():
    """Admit this selected authentic closure, not the incomplete full package candidate."""
    primary, reference = _srd_drafts()
    runtime = _runtime()
    record = deepcopy(primary.record)
    record["ui_surfaces"] = []
    record["tools"] = [
        {
            "name": name,
            "capability": "dnd.srd.5.2.1.spells",
            "input_schema": schema_owner._tool_schema(runtime["tools"][name], tool_name=name),
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
        }
        for name in sorted(PUBLIC)
    ]
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": PROOF.read_bytes()}
    content = json.loads(payloads["content/content-declarations.json"])
    for inclusion in content["inclusions"]:
        links = inclusion.get("links", {})
        links["tools"] = [name for name in links.get("tools", []) if name in PUBLIC]
        links["ui_surfaces"] = []
    content["inclusions"].append(
        {
            "id": "selected-contingency-jar-composition",
            "kind": "ruleset_runtime",
            "family": "contingency-jar-proof",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": sorted(PUBLIC),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["paid-contingency-hp-waterbreathing-jar-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def _case(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(cards, "_casting_headers", lambda: runtime["definitions"]["spell_casting"])
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    state, data, parameters = cards._spell_case("contingency")
    spells = ["contingency", "water_breathing", "magic_jar", "magic_missile"]
    data["spellcasting_grants"][0]["spells"] = spells[:]
    data["wizard_spellbook"] = spells[:]
    entities = state[f"{SRD}.entities"]
    hero = entities["hero"]
    hero.update(hp=40, max_hp=40, temp_hp=0, prepared_spells=spells[:])
    material_ids = {"contingency": parameters["material_item_ids"]}
    for spell in ["water_breathing", "magic_jar"]:
        material_ids[spell] = []
        for index, part in enumerate(
            runtime["definitions"]["spell_casting"][spell]["material_parts"]
        ):
            item = {
                "id": f"{spell}_component_{index}",
                "inventory_item": f"{spell}_inventory_{index}",
                "kind": "specific",
                "components": [part["id"]],
                "quantity": part["quantity"] * 3,
                "value_cp": part["minimum_value_cp"],
            }
            hero["spell_materials"].append(item)
            hero["inventory"].append(item["inventory_item"])
            material_ids[spell].append(item["id"])
    for name in ["hero", "guard"]:
        entities[name].update(
            creature_kind="character",
            creature_type="humanoid",
            hit_die_size=6,
            hit_dice_pools=None,
        )
        entities[name]["resources"]["hit_die"] = data["level"]
        entities[name].setdefault("resource_maxima", {})["hit_die"] = data["level"]
    entities["guard"].update(hp=60, max_hp=60, temp_hp=0)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, phase="exploration")
    scene = {
        "format": 1,
        "map": {
            "id": "contingency-jar",
            "revision": 0,
            "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
            "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
        },
        "entities": [
            {
                "id": name,
                "map_id": "contingency-jar",
                "position": position,
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"participant_ref": name, "plane": "material", "lighting": "bright"},
            }
            for name, position in [("hero", [0, 0, 0]), ("guard", [5, 0, 0])]
        ],
        "geometry": [],
        "zones": [],
        "terrain": [],
    }
    scene["entities"].append(
        {
            "id": "reliquary",
            "map_id": scene["map"]["id"],
            "position": [3, 0, 0],
            "footprint": {"kind": "box", "half_extents": [0.5, 0.5]},
            "height": 1,
            "facing": [1, 0],
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "metadata": {"is_object": True, "plane": "material"},
        }
    )
    state[f"{SRD}.state"]["spatial_scene"] = scene
    parameters.pop("target")
    parameters.update(
        contingent_spell="water_breathing",
        contingent_slot_resource="spell_slot_3",
        contingent_hp_threshold=39,
        contingent_material_item_ids=material_ids["water_breathing"],
    )
    return state, data, parameters, material_ids


def _cast(owner, parameters, rounds):
    begin = {
        parameter["name"]: parameters[parameter["name"]]
        for parameter in _runtime()["tools"]["begin_srd_spell_cast"]["parameters"]
        if parameter["name"] in parameters
    }
    receipt, _ = _act(owner, "begin_srd_spell_cast", **begin)
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    receipt, _ = _act(
        owner,
        "continue_srd_spell_cast",
        caster_state="hero",
        elapsed_rounds=rounds,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    return receipt, command


def _source(state):
    return next(
        body
        for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if body["id"] == "reliquary"
    )["metadata"]["srd_magic_jar"]


def test_paid_contingency_hp_outer_release_and_paid_jar_host_reopen_retry(tmp_path, monkeypatch):
    from sylvia_gm.application import package_runtime

    execute = package_runtime.execute_tool

    def checked(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("NATIVE", args[0], result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", checked)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state, data, parameters, material_ids = _case(monkeypatch)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _selected_archive)
    database = tmp_path / "contingency-jar.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        create = owner.create_campaign

        def with_host(command):
            caster = command.participants[0]
            host_data = deepcopy(caster.data)
            host_data.update(
                name="Actual host",
                saving_throw_proficiencies=[],
                abilities={
                    "strength": 20,
                    "dexterity": 8,
                    "constitution": 18,
                    "intelligence": 10,
                    "wisdom": 10,
                    "charisma": 3,
                },
            )
            host = replace(caster, participant_id="guard", data=host_data)
            return create(replace(command, participants=(caster, host)))

        with patch.object(owner, "create_campaign", side_effect=with_host):
            public_casting._create_spell_campaign(
                owner,
                "contingency",
                target_overrides={"hp": 60, "max_hp": 60, "temp_hp": 0},
            )
        hero_before = deepcopy(owner.snapshot("casting").state[f"{SRD}.entities"]["hero"])
        parent_receipt, parent_command = _cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        hero = armed.state[f"{SRD}.entities"]["hero"]
        source_id = hero["contingency_source_id"]
        assert armed.state[f"{SRD}.state"]["contingency_sources"][source_id]["status"] == "armed"
        assert hero["resources"]["spell_slot_6"] == hero_before["resources"]["spell_slot_6"] - 1
        assert hero["resources"]["spell_slot_3"] == hero_before["resources"]["spell_slot_3"] - 1
        _act(
            owner,
            "set_srd_respiration",
            target_state="hero",
            target_character="hero",
            medium="water",
        )
        missile = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "magic_missile",
            "target": "hero",
            "slot_resource": "spell_slot_1",
            "target_distance": 0,
            "material_item_ids": [],
        }
        release_receipt, release_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert release_receipt.status is ActionStatus.COMMITTED, release_receipt.result
        released = owner.snapshot("casting")
        source = released.state[f"{SRD}.state"]["contingency_sources"][source_id]
        hero = released.state[f"{SRD}.entities"]["hero"]
        assert source["status"] == "released" and source["installed"] is False
        assert source["trigger_hp_before"] > source["trigger_hp_after"] >= hero["hp"]
        assert released.state[f"{SRD}.state"]["contingency_release_queue"] == []
        assert hero["resources"]["spell_slot_3"] == hero_before["resources"]["spell_slot_3"] - 1
        assert hero["resources"]["spell_slot_1"] == hero_before["resources"]["spell_slot_1"] - 1
        children = [
            effect
            for effect in released.state[f"{SRD}.state"]["turn_effects"]
            if effect.get("kind") == "timed_senses" and effect.get("spell") == "water_breathing"
        ]
        assert len(children) == 1 and children[0]["target"] == "hero"
        assert children[0]["cast_id"] and children[0]["cast_id"] != source_id
        assert children[0]["spell_level"] == 3
        assert children[0]["expires_tick"] - children[0]["started_tick"] == 28800
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(parent_command) == parent_receipt
        assert owner.execute_action(release_command) == release_receipt
        assert owner.snapshot("casting") == released
        respiration_before = released.state[f"{SRD}.entities"]["hero"]["respiration_breath_ticks"]
        elapsed, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
        hero = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        assert hero["respiration_breath_ticks"] == respiration_before
        _act(
            owner, "set_srd_respiration", target_state="hero", target_character="hero", medium="air"
        )
        bound, _ = _act(
            owner,
            "record_srd_magic_jar_container",
            caster_state="hero",
            object_id="reliquary",
            material_item_id=material_ids["magic_jar"][0],
        )
        assert bound.status is ActionStatus.COMMITTED, bound.result
        jar = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "magic_jar",
            "slot_resource": "spell_slot_6",
            "material_item_ids": material_ids["magic_jar"],
            "magic_jar_container": "reliquary",
        }
        jar_receipt, jar_command = _cast(owner, jar, 10)
        jar_paid = owner.snapshot("casting")
        assert _source(jar_paid.state)["cast_id"]
        assert jar_paid.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 1
        possession, possession_command = _act(
            owner,
            "project_srd_magic_jar_soul",
            caster_state="hero",
            host_state="guard",
            host_character="guard",
        )
        assert possession.status is ActionStatus.COMMITTED, possession.result
        possessed = owner.snapshot("casting")
        hero = possessed.state[f"{SRD}.entities"]["hero"]
        assert hero["magic_jar_role"] == "possessed" and hero["hp"] == 60
        assert hero["ability_scores"]["strength"] == 20
        assert hero["ability_scores"]["intelligence"] == data["abilities"]["intelligence"]
        host_damage, host_damage_command = _act(
            owner,
            "cast_spell_outside_combat",
            **(missile | {"target": "guard", "target_distance": 0}),
        )
        assert host_damage.status is ActionStatus.COMMITTED, host_damage.result
        settled = owner.snapshot("casting")
        hero, host = (settled.state[f"{SRD}.entities"][name] for name in ["hero", "guard"])
        assert 0 < hero["hp"] < 60 and hero["hp"] == host["hp"]
        assert (
            settled.state[f"{SRD}.state"]["contingency_sources"][source_id]["status"] == "released"
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(
            dice, "roll", lambda *a, **kw: pytest.fail("Exact saved retry rerolled")
        )
        for command, receipt in [
            (jar_command, jar_receipt),
            (possession_command, possession),
            (host_damage_command, host_damage),
        ]:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == settled
