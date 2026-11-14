"""Real later sources, object changes and condition cures end the paid Time Stop once."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml
from scripts import srd_time_stop as stop
from scripts import srd_time_stop_effects as effects
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_runtime_defaults import restore_tool_defaults

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

PARENT = Path(
    os.environ.get(
        "FC14_TIME_STOP_EFFECTS_PARENT",
        str(Path(__file__).parents[1] / ".scratch/time-stop-parent-v1"),
    )
)
SOURCE = Path(
    os.environ.get(
        "FC14_SOURCE_PACKAGE_ROOT",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-source-package-root/dnd-srd-5.2.1"),
    )
)


class Rolls:
    def randint(self, _first, _last):
        return 1


@lru_cache
def _runtime():
    original = yaml.load((PARENT / "selected-runtime.yaml").read_text(), Loader=yaml.CSafeLoader)
    restore_tool_defaults(original)
    text = effects.bind_source_consumers(
        yaml.dump(original, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    text = effects.bind_received_effects(text)
    text = refresh_exploration_runtime(text)
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    assert yaml.load(effects.bind_source_consumers(text), Loader=yaml.CSafeLoader) == runtime
    assert yaml.load(effects.bind_received_effects(text), Loader=yaml.CSafeLoader) == runtime
    runtime["definitions"]["spells"].update(stop.PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    selected = {
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_time_stop_turn",
        "end_srd_time_stop_turn",
        "roll_srd_flaming_sphere",
        "record_srd_object_access",
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_composite_damage",
        "apply_srd_condition",
        "remove_srd_condition",
        "begin_srd_spell_source_instance",
        "cast_srd_time_stop",
        "end_combat_encounter",
    }
    while True:
        reachable = {
            node["call"]
            for name in selected
            for nodes in effects._lists(runtime["tools"][name]["effects"])
            for node in nodes
            if "call" in node
        } - selected
        if not reachable:
            break
        assert not reachable - runtime["tools"].keys()
        selected.update(reachable)
    runtime["tools"] = {name: tool for name, tool in runtime["tools"].items() if name in selected}
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())


def _case():
    state, data, params = cards._spell_case("time_stop")
    params.pop("target")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "floor",
            "map_id": "orders",
            "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": False,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True, "material": "stone"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    for actor, position in (("guard", [30, 0, 0]), ("other", [70, 0, 0])):
        next(e for e in scene["entities"] if e["id"] == actor)["position"] = position
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = -100
    return state, data, params


def _grant(state, data, spell, **choices):
    spell_state, spell_data, params = cards._spell_case(spell)
    caster = state[f"{SRD}.entities"]["hero"]
    caster["prepared_spells"].append(spell)
    for grant in spell_data["spellcasting_grants"]:
        existing = next(
            (g for g in data["spellcasting_grants"] if g["source"] == grant["source"]),
            None,
        )
        if existing is None:
            data["spellcasting_grants"].append(deepcopy(grant))
        else:
            existing["spells"] = sorted(set(existing["spells"] + grant["spells"]))
    data.setdefault("wizard_spellbook", []).append(spell)
    caster["spell_materials"] += deepcopy(spell_state[f"{SRD}.entities"]["hero"]["spell_materials"])
    caster["inventory"] += [
        item["inventory_item"]
        for item in caster["spell_materials"]
        if item["inventory_item"] not in caster["inventory"]
    ]
    return params | choices


def _start(state, data, params):
    paid = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert paid.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    _run("begin_srd_time_stop_turn", state)
    return state[f"{SRD}.state"]["time_stop_source"]


def test_created_sphere_later_real_bonus_contact_uses_exact_current_stop_receipt():
    state, data, params = _case()
    flame = _grant(state, data, "flaming_sphere", environment_origin=[20, 0, 0])
    source = _start(state, data, params)
    receipt = source["cast_id"]
    assert receipt and state[f"{SRD}.entities"]["hero"]["last_spell_source_cast_id"] == receipt
    tick = state[f"{SRD}.state"]["lasting_tick"]
    _run("cast_spell", state, participant_data=data, **flame)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["created_time_stop_id"] == receipt
    assert state[f"{SRD}.state"]["time_stop_source"]["active"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert not state[f"{SRD}.state"]["time_stop_source"]["spell_action_active"]
    _run("roll_srd_flaming_sphere", state, caster_state="hero", area_id="hero", point=[45, 0, 0])
    stopped = state[f"{SRD}.state"]["time_stop_source"]
    assert not stopped["active"] and not stopped["ending"]
    assert stopped["end_reason"] == "other_creature"
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 998
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


def test_older_sphere_and_unattributed_positive_damage_preserve_current_stop():
    state, data, params = _case()
    flame = _grant(state, data, "flaming_sphere", environment_origin=[20, 0, 0])
    _run("cast_spell_outside_combat", state, participant_data=data, **flame)
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["created_time_stop_id"] is None
    _start(state, data, params)
    _run("roll_srd_flaming_sphere", state, caster_state="hero", area_id="hero", point=[45, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 998
    assert state[f"{SRD}.state"]["time_stop_source"]["active"]
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=2,
        adjustment=0,
        damage_type="psychic",
        source_state_id="hero",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 996
    assert state[f"{SRD}.state"]["time_stop_source"]["active"]


def test_paid_knock_on_actual_held_object_ends_and_preserves_actual_unlocked_state():
    state, data, params = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    obj = deepcopy(scene["entities"][1])
    obj.update(id="held", position=[30, 0, 0])
    obj["metadata"] = {
        "is_object": True,
        "plane": "material",
        "lighting": "bright",
        "carried_by": "guard",
    }
    scene["entities"].append(obj)
    _run(
        "record_srd_object_access",
        state,
        object_id="held",
        object_kind="container",
        closed=True,
        locks=["bronze", "brass"],
        stuck=False,
        barred=False,
    )
    knock = _grant(state, data, "knock", object_target_id="held")
    _start(state, data, params)
    _run("cast_spell", state, participant_data=data, **knock)
    stopped = state[f"{SRD}.state"]["time_stop_source"]
    assert not stopped["active"] and not stopped["ending"]
    assert stopped["end_reason"] == "other_held_object"
    obj = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "held")
    assert obj["metadata"]["srd_object_access"]["locks"] == ["brass"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


def test_paid_condition_cure_ends_only_after_actual_removal_and_reuses_paid_identity():
    state, data, params = _case()
    data["classes"].append({"id": "cleric", "level": 3})
    data["level"] = 20
    cure = _grant(state, data, "lesser_restoration", selected_restoration_condition="paralyzed")
    _run("apply_srd_condition", state, target="guard", condition="paralyzed")
    # A real allocated witness is reused by the installer rather than allocated twice.
    allocated = _run(
        "begin_srd_spell_source_instance",
        state,
        caster_state="hero",
        spell="time_stop",
        slot_level=9,
    )
    receipt = allocated.result["context"]["_spell_source_cast_id"]
    before = state[f"{SRD}.state"]["spell_source_serial"]
    _run("cast_srd_time_stop", state, caster_state="hero", spell_cast_id=receipt)
    assert state[f"{SRD}.state"]["spell_source_serial"] == before
    _run("begin_srd_time_stop_turn", state)
    _run("remove_srd_condition", state, target="guard", condition="blinded")
    assert state[f"{SRD}.state"]["time_stop_source"]["active"]
    _run("cast_spell", state, participant_data=data, **cure)
    assert "paralyzed" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not state[f"{SRD}.state"]["time_stop_source"]["active"]
    assert not state[f"{SRD}.state"]["time_stop_source"]["ending"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    state, _, _ = _case()
    unchanged = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="committed cast identity"):
        _run("cast_srd_time_stop", state, caster_state="hero", spell_cast_id="invented")
    assert state == unchanged


def _archive():
    primary, reference = bundled_packages._srd_drafts()
    runtime, encoded = _runtime()
    record = deepcopy(primary.record)
    record["tools"] = [t for t in record["tools"] if t["name"] in runtime["tools"]]
    names = {t["name"] for t in record["tools"]}
    template = deepcopy(next(t for t in record["tools"] if t["name"] == "cast_spell"))
    template["state_predicates"] = []
    for name in stop.PUBLIC_TOOLS | {"roll_srd_flaming_sphere"}:
        if name not in names:
            record["tools"].append(template | {"name": name})
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    declarations = json.loads(primary.payloads["content/content-declarations.json"])
    aggregate = next(
        item for item in declarations["inclusions"] if item["id"] == "srd-5.2.1-gameplay-runtime"
    )
    # This fixture declares its retained reachable closure, not the whole newer package.
    aggregate["links"]["tools"] = sorted(t["name"] for t in record["tools"])
    return bundled_packages._archive(
        (
            NormalizedPackageDraft(
                record,
                dict(primary.payloads)
                | {
                    "runtime/ruleset.yaml": encoded,
                    "content/content-declarations.json": canonical_json_bytes(declarations),
                },
                primary.format_version,
            ),
            reference,
        )
    )


def test_public_created_source_contact_reopens_and_retries_once(tmp_path, monkeypatch):
    def caster_case(spell):
        assert spell == "time_stop"
        state, data, params = _case()
        _grant(state, data, "flaming_sphere")
        return state, data, params

    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: SOURCE)
    monkeypatch.setattr(public, "ROOT", SOURCE.parents[1])
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public, "_spell_case", caster_case)
    database = tmp_path / "time-stop-effects.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "time_stop", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        paid, paid_command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        begun, begin_command = _act(owner, "begin_srd_time_stop_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        flame = cards._spell_case("flaming_sphere")[2] | {"environment_origin": [20, 0, 0]}
        created, created_command = _act(owner, "cast_spell", **flame)
        assert created.status is ActionStatus.COMMITTED, created.result
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert (
            source["created_time_stop_id"]
            == saved.state[f"{SRD}.state"]["time_stop_source"]["cast_id"]
        )
        for receipt, command in (
            (paid, paid_command),
            (begun, begin_command),
            (created, created_command),
        ):
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(created_command) == created
        contact, contact_command = _act(
            owner, "roll_srd_flaming_sphere", caster_state="hero", area_id="hero", point=[45, 0, 0]
        )
        assert contact.status is ActionStatus.COMMITTED, contact.result
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == 998
        assert not after.state[f"{SRD}.state"]["time_stop_source"]["active"]
        assert (
            after.state[f"{SRD}.state"]["lasting_tick"]
            == saved.state[f"{SRD}.state"]["lasting_tick"]
        )
        assert after.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
        assert owner.execute_action(contact_command) == contact
        assert owner.snapshot("casting").state == after.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == after.state
        assert owner.execute_action(contact_command) == contact
        assert owner.snapshot("casting").state == after.state


def test_final_receivers_keep_actual_production_qualified_facades_unchanged():
    spec = spec_from_file_location(
        "scripts.time_stop_packet_parent",
        PARENT / "scripts/srd_qualified_packet_implementations.py",
    )
    factory = module_from_spec(spec)
    spec.loader.exec_module(factory)
    runtime = deepcopy(_runtime()[0])
    native = yaml.load((SOURCE / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader)
    restore_tool_defaults(native)
    originals = {}
    for name, kernel in (
        ("apply_srd_qualifying_damage", "execute_srd_qualified_scalar_packet"),
        ("apply_srd_qualifying_composite_damage", "execute_srd_qualified_composite_packet"),
    ):
        owner = native["tools"][name]
        fields = factory.PRESENTATION_FIELDS - ({"_hp_lost"} if "composite" in name else set())
        base, drain = factory._drain(owner)
        runtime["tools"][kernel] = factory._canonical(base, fields)
        runtime["tools"][name] = factory._delegate(owner, kernel, True, drain)
        originals[name] = deepcopy(runtime["tools"][name])
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    bound = yaml.load(effects.bind_received_effects(text), Loader=yaml.CSafeLoader)
    for name, original in originals.items():
        assert bound["tools"][name] == original
        kernel = original["effects"][0]["call"]
        assert (
            sum(
                node.get("call") == "observe_srd_time_stop_creature_effect"
                for nodes in effects._lists(bound["tools"][kernel]["effects"])
                for node in nodes
            )
            == 1
        )
    assert (
        yaml.load(
            effects.bind_received_effects(
                yaml.dump(bound, Dumper=yaml.CSafeDumper, sort_keys=False)
            ),
            Loader=yaml.CSafeLoader,
        )
        == bound
    )
