"""Disintegrate observes prevention and the actual physical body before soul remapping."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts import srd_disintegrate_bodies as bodies
from scripts import srd_dream_spell as dream
from scripts.srd_full.srd_runtime_defaults import restore_tool_defaults
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_magic_jar as jar
from tests import test_srd_simulacrum as sim
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

SRD = jar.SRD
PARENT = Path(
    os.environ.get(
        "FC14_DISINTEGRATE_RUNTIME",
        str(Path(__file__).parents[1] / ".scratch/simulacrum-fixture/runtime/ruleset.yaml"),
    )
)


class Ones:
    def randint(self, _first, _last):
        return 1


@lru_cache
def _runtime():
    """Follow production Astral, Dream, packet-factoring and physical-body receiving order."""
    parent = Path(
        os.environ.get(
            "FC14_DISINTEGRATE_PARENT",
            str(Path(__file__).parents[1] / ".scratch/disintegrate-parent-v1"),
        )
    )
    source = parent / "scripts/srd_qualified_packet_implementations.py"
    import hashlib

    manifest = json.loads((parent / "manifest.json").read_text())
    assert (
        hashlib.sha256(source.read_bytes()).hexdigest()
        == manifest["files"]["scripts/srd_qualified_packet_implementations.py"]
    )
    spec = spec_from_file_location("scripts.disintegrate_parent_packets", source)
    packets = module_from_spec(spec)
    spec.loader.exec_module(packets)
    original = _load_runtime(PARENT.read_text())
    for fragment in ("object-material-tools.yaml", "recovery-tools.yaml", "jar-support-tools.yaml"):
        path = parent / fragment
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["files"][fragment]
        for name, tool in yaml.load(path.read_text(), Loader=yaml.CSafeLoader).items():
            original["tools"].setdefault(name, tool)
    restore_tool_defaults(original)
    source = Path(__file__).resolve().parents[2] / "scripts/srd_astral_projection.py"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "d9f817753ae73db745d1158bb9de8b12c02ce315834e0e1a662e020a3fd49a64"
    )
    spec = spec_from_file_location("scripts.disintegrate_astral_live", source)
    astral = module_from_spec(spec)
    spec.loader.exec_module(astral)
    assert hashlib.sha256(Path(dream.__file__).read_bytes()).hexdigest() == (
        "efa927c4ea0bfae18675e414cc4be6b6b001f3121f122d79f46eac56abd55b98"
    )
    text = astral.bind_runtime_consumers(
        yaml.dump(original, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    original = _load_runtime(dream.bind_runtime_consumers(text))
    base, drain = packets._drain(original["tools"]["apply_srd_qualifying_damage"])
    fields = packets.PRESENTATION_FIELDS
    prism = packets._without_presentation_snapshot(base, fields)
    core = "execute_srd_qualified_scalar_packet"
    original["tools"][core] = packets._canonical(base, fields)
    original["tools"]["apply_srd_qualifying_damage"] = packets._delegate(base, core, True, drain)
    original["tools"]["apply_srd_prismatic_damage"] = packets._delegate(prism, core, False, drain)
    original["tools"]["apply_srd_dream_wake_damage"] = packets._delegate(base, core, True, [])
    text = bodies.bind_runtime_consumers(
        yaml.dump(original, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = _load_runtime(text)
    assert _load_runtime(bodies.bind_runtime_consumers(text)) == runtime
    native_guard = f"({bodies.PC_DEATH}) and target.astral_projection_role != 'form'"
    dust_guard = f"({bodies.PC_DISINTEGRATE}) and target.astral_projection_role != 'form'"
    for name in ("apply_srd_damage", core):
        before = [
            e
            for effects in bodies._lists(original["tools"][name]["effects"])
            for e in effects
            if e.get("condition") == native_guard
        ]
        after = [
            e
            for effects in bodies._lists(runtime["tools"][name]["effects"])
            for e in effects
            if e.get("condition") == dust_guard
        ]
        assert len(before) == len(after) == 1
    # The current scalar factory is genuine; its unrelated facade effects remain exact.
    for name in bodies.SCALAR_FACADES:
        assert runtime["tools"][name]["effects"][1:] == original["tools"][name]["effects"][1:]
        assert runtime["tools"][name]["effects"][0]["export_context"] == "*"
    return runtime, text


@pytest.fixture(scope="module")
def _compiled_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("disintegrate-owner")
    (root / "runtime").mkdir()
    (root / "runtime/ruleset.yaml").write_text(_runtime()[1])
    (root / "package-record.json").write_text(json.dumps({"id": SRD, "version": "0.3.1"}))
    return root


@pytest.fixture(autouse=True)
def _compiled(monkeypatch, _compiled_root):
    monkeypatch.setenv("FC14_JAR_RUNTIME_ROOT", str(_compiled_root))
    yield from jar._compiled.__wrapped__(monkeypatch)


def _case():
    state, data, params = jar.cards._spell_case("disintegrate")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    host = state[f"{SRD}.entities"]["guard"]
    host.update(hp=40, max_hp=100, creature_kind="character", creature_type="humanoid")
    host["saving_throw_modifiers"]["dexterity"] = -1  # The actual host artifact has DEX8.
    params.update(target="guard", target_distance=5)
    return state, data, params


def _cast(state, data, params):
    return jar._run("cast_spell_outside_combat", state, data, **params)


def _gear(state, data):
    _admit_scene(state)
    admitted = state[f"{SRD}.state"]["spatial_scene"]
    next(e for e in admitted["entities"] if e["id"] == "guard")["position"] = [5, 0, 0]
    scene = jar._entity("mundane-coat", [5, 0, 0], is_object=True, carried_by="guard")
    magic = jar._entity("magic-ring", [5, 0, 0], is_object=True, carried_by="guard")
    for item in (scene, magic):
        item["map_id"] = admitted["map"]["id"]
        admitted["entities"].append(item)
    for name, magical in (("mundane-coat", False), ("magic-ring", True)):
        jar._run(
            "record_srd_object_material",
            state,
            data,
            object_id=name,
            material="vegetable",
            raw_stock=False,
            magical=magical,
        )
    state[f"{SRD}.entities"]["guard"].update(
        inventory=["mundane-coat", "magic-ring", "unclassified keepsake"],
        held_items=["magic-ring"],
        held_attack_names=[],
    )


def test_paid_failure_kills_pc_below_massive_threshold_and_preserves_magic_items(monkeypatch):
    state, data, params = _case()
    _gear(state, data)
    monkeypatch.setattr(dice, "_rng", Ones())
    slot = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"]
    result = _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 0 and target["is_alive"] is False
    assert target["death_tick"] is not None
    assert target["body_disintegrated"] is True and target["body_available"] is False
    assert target["disintegrated_by_caster"] == "hero"
    assert target["inventory"] == ["magic-ring", "unclassified keepsake"], {
        k: v for k, v in result.result["context"].items() if k.startswith("_dust")
    } | {"removed": target.get("disintegrated_items")}
    assert target["disintegrated_items"] == ["mundane-coat"]
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    assert next(e for e in scene if e["id"] == "mundane-coat")["active"] is False
    assert next(e for e in scene if e["id"] == "magic-ring")["active"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == slot - 1
    assert result.result["context"]["_spell_save_outcome"] == "failure"
    # Historical body narration cannot override this authentic cause.
    jar._run(
        "record_srd_deceased_body",
        state,
        data,
        target_state="guard",
        death_tick=target["death_tick"],
        old_age=False,
        soul_willing=True,
        body_available=True,
        vital_parts_present=True,
        living_creature_type="humanoid",
        name="Actual host",
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="disintegrated body"):
        jar._run(
            "resolve_srd_revival",
            state,
            data,
            caster_state="hero",
            target_state="guard",
            spell="revivify",
        )
    assert state == before
    restored = jar._run(
        "resolve_srd_revival",
        state,
        data,
        caster_state="hero",
        target_state="guard",
        spell="true_resurrection",
    )
    assert restored.result["context"]["_revival_succeeded"] is True
    assert state[f"{SRD}.entities"]["guard"]["body_disintegrated"] is False
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is True


def test_real_death_ward_prevents_dust_then_next_failed_cast_destroys_body(monkeypatch):
    state, data, params = _case()
    ward = jar.cards._spell_case("death_ward")[2]
    jar._run(
        "prepare_srd_creature",
        state,
        data,
        **(
            sim.awaken._native_params(creature_type="humanoid")
            | {
                "creature_id": "warden",
                "name": "Actual ward caster",
                "spellcasting_ability": "wisdom",
                "spellcasting_modifier": 0,
                "spell_attack_bonus": 2,
                "spell_save_dc": 10,
                "spells": [{"spell": "death_ward", "uses_per_day": 1}],
            }
        ),
    )
    ward.pop("caster")
    ward.pop("slot_resource")
    ward.update(caster_state="warden", target="guard", target_distance=5, material_item_ids=[])
    _cast(state, data, ward)
    monkeypatch.setattr(dice, "_rng", Ones())
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 1 and target["is_alive"] is True
    assert target.get("body_disintegrated") is not True
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["body_disintegrated"] is True and target["hp"] == 0


def test_actual_occupied_host_dusts_before_jar_restores_the_caster_body(monkeypatch):
    state, data, params = jar._case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -1
    jar._cast(state, data, params)
    assert jar._possess(state, data).result["context"]["_jar_possessed"] is True
    supply_state, _, spell = jar.cards._spell_case("disintegrate")
    supplies = supply_state[f"{SRD}.entities"]["hero"]["spell_materials"]
    spell.pop("caster")
    spell.pop("slot_resource")
    jar._run(
        "prepare_srd_creature",
        state,
        data,
        **(
            sim.awaken._native_params(score=20, creature_type="humanoid")
            | {
                "creature_id": "raycaster",
                "name": "Actual ray caster",
                "spellcasting_ability": "intelligence",
                "spellcasting_modifier": 5,
                "spell_attack_bonus": 7,
                "spell_save_dc": 15,
                "spells": [{"spell": "disintegrate", "uses_per_day": 1}],
                "spell_materials": supplies,
                "inventory": [lot["inventory_item"] for lot in supplies],
            }
        ),
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    raycaster = jar._entity("raycaster", [10, 0, 0], participant_ref="raycaster")
    raycaster["map_id"] = scene["map"]["id"]
    scene["entities"].append(raycaster)
    # One actual RNG supplies the failed ray save, sufficient damage and successful Jar escape.
    monkeypatch.setattr(dice, "_rng", Random(4))
    spell.update(caster_state="raycaster", target="guard", target_distance=5)
    _cast(state, data, spell)
    host = state[f"{SRD}.entities"]["guard"]
    assert host["hp"] == 0 and host["is_alive"] is False
    assert host["body_disintegrated"] is True and host["disintegrated_body_source_id"] == "guard"
    caster = state[f"{SRD}.entities"]["hero"]
    assert caster["magic_jar_role"] == "container"
    assert caster["hp"] == 40 and caster.get("body_disintegrated") is not True


def test_unattributed_force_zero_remains_ordinary_pc_death_saves(monkeypatch):
    state, data, _ = _case()
    monkeypatch.setattr(dice, "_rng", Ones())
    jar._run(
        "apply_srd_qualifying_damage",
        state,
        data,
        target="guard",
        amount=50,
        adjustment=0,
        damage_type="force",
        source_state_id="hero",
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 0 and target["is_alive"] is True
    assert target.get("body_disintegrated") is not True
    assert "unconscious" in target["conditions"]


def test_unattributed_massive_force_preserves_native_death_without_dust(monkeypatch):
    state, data, _ = _case()
    monkeypatch.setattr(dice, "_rng", Ones())
    target = state[f"{SRD}.entities"]["guard"]
    amount = target["hp"] + target["temp_hp"] + target["max_hp"]
    result = jar._run(
        "apply_srd_qualifying_damage",
        state,
        data,
        target="guard",
        amount=amount,
        adjustment=0,
        damage_type="force",
        source_state_id="hero",
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 0 and target["is_alive"] is False
    assert target["death_tick"] is not None
    assert "dead" in target["conditions"]
    assert target.get("body_disintegrated") is not True
    assert result.result["context"]["_remaining_damage"] == target["max_hp"]


def test_public_paid_pc_dust_and_saved_magic_gear_reopen_and_retry(tmp_path, monkeypatch):
    from tests import test_srd_plant_growth as growth

    source = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-disintegrate-bodies/dnd-srd-5.2.1")
    )
    monkeypatch.setenv("FC14_SOURCE_PACKAGE_ROOT", str(source))
    monkeypatch.setattr(growth, "PUBLIC_TOOLS", set())
    growth._public_fixture(monkeypatch)
    monkeypatch.setattr(jar.public_casting, "ROOT", source.parents[1])

    def public_case(_spell):
        state, data, params = _case()
        _gear(state, data)
        return state, data, params

    monkeypatch.setattr(jar.public_casting, "_spell_case", public_case)
    original_create = AuthorityOwner.create_campaign

    def add_actual_host(owner, command):
        hero = command.participants[0]
        host = replace(hero, participant_id="guard", data=jar._host_artifact(hero.data))
        return original_create(owner, replace(command, participants=(*command.participants, host)))

    monkeypatch.setattr(AuthorityOwner, "create_campaign", add_actual_host)
    monkeypatch.setattr(dice, "_rng", Ones())
    body_calls = []
    original_call = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(name, effect, *args, **kwargs):
        if name == bodies.BODY_CALL:
            body_calls.append(effect)
        return original_call(name, effect, *args, **kwargs)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    database = tmp_path / "disintegrate.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(
            owner, "disintegrate", target_overrides={"hp": 40, "max_hp": 100}
        )
        initial = owner.snapshot("casting")
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["guard"]
        assert body["hp"] == 0 and body["is_alive"] is False
        assert body["body_disintegrated"] is True and body["body_available"] is False
        assert body["inventory"] == ["magic-ring", "unclassified keepsake"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == (
            initial.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] - 1
        )
        assert owner.execute_action(command) == cast
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == cast
        assert owner.snapshot("casting") == saved
    assert len(body_calls) == 1
