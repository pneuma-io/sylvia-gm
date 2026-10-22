"""Two real paid Emanation producers attribute later packets to their creating stop."""

# ruff: noqa: E402

import hashlib
import importlib.util
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import scripts
import yaml

import tests

ROOT = Path(__file__).parents[1]
FROZEN = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-time-stop-retained-damage/input-1")
)
BASELINE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-time-stop-retained-damage/input-2"))
PARENT = FROZEN / "excluded-parent"
DEPENDENCIES = {
    FROZEN / "scripts/srd_time_stop_effects.py": (
        "411abda228cc08b2b1e08e335264754c2045b13f4e04df6a25e96151cbbc9c9e"
    ),
    FROZEN / "tests/test_srd_time_stop_effects.py": (
        "f88bc5912f07c2c87db6c3bf2669e0a55088fddc8ae933c27b01b360ad8d0b63"
    ),
    PARENT / "selected-runtime.yaml": (
        "9b921d42b8e5dfc58d970aa205155dafd4d93f09195a429741ea8cade163868a"
    ),
    PARENT / "scripts/srd_time_stop.py": (
        "3042dd7eaf29543094a316d17c8bdb2e5601dd13d73a4d55bc63afdd7a8754f9"
    ),
    BASELINE / "scripts/srd_conjured_presences.py": (
        "61bd99f8217c0ee47a8bbcd77d8ca5670448815bdb3efffc645c2d6497639b7e"
    ),
    BASELINE / "scripts/srd_celestial_lights.py": (
        "7121925b703ecde31e0ad9968c836e8df5fef7b1884482d3a38cc93cf076c603"
    ),
}
for path, digest in DEPENDENCIES.items():
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path
scripts.__path__ = [str(PARENT / "scripts"), str(BASELINE / "scripts"), *scripts.__path__]
tests.__path__ = [str(BASELINE / "tests"), *tests.__path__]


def _import(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


effects = _import("scripts.srd_full.srd_time_stop_effects", FROZEN / "scripts/srd_time_stop_effects.py")
owned = _import(
    "scripts.srd_full.srd_time_stop_retained_damage", ROOT / "scripts/srd_time_stop_retained_damage.py"
)
base = _import("tests._time_stop_retained_base", FROZEN / "tests/test_srd_time_stop_effects.py")
base.PARENT = PARENT

from scripts import srd_celestial_lights as celestial
from scripts import srd_conjured_presences as presence
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run


@lru_cache
def _runtime():
    original = yaml.load((PARENT / "selected-runtime.yaml").read_text(), Loader=yaml.CSafeLoader)
    base.restore_tool_defaults(original)
    text = yaml.dump(original, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = effects.bind_source_consumers(text)
    text = effects.bind_received_effects(text)
    text = base.refresh_exploration_runtime(text)
    before = _load_runtime(text)
    bound = owned.bind_runtime_consumers(text)
    runtime = _load_runtime(bound)
    assert _load_runtime(owned.bind_runtime_consumers(bound)) == runtime
    assert _load_runtime(owned.restore_runtime(bound)) == before
    expected_changed = {name for name in owned.PRODUCERS if name in before["tools"]}
    assert {
        name for name in before["tools"] if before["tools"][name] != runtime["tools"][name]
    } == expected_changed
    for name in expected_changed:
        restored = deepcopy(runtime["tools"][name])
        owned._remove_observation(restored)
        assert restored == before["tools"][name]
    runtime["definitions"]["spells"].update(base.stop.PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    selected = {
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_time_stop_turn",
        "end_srd_time_stop_turn",
        "move_srd_scene_creature",
        "apply_srd_damage",
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
            input_schema=owned_payload(base._tool_schema(runtime["tools"][name], tool_name=name)),
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
    monkeypatch.setattr(dice, "_rng", base.Rolls())


def _bard_sources(data, spell):
    """The admitted Lore Bard grant supplies both real spells without invalid multiclass levels."""
    data.update(
        classes=[{"id": "bard", "level": 17}],
        level=17,
        subclasses={"bard": "college_of_lore"},
        wizard_spellbook=[],
        spellcasting_grants=[
            {"source": "magical_discoveries", "ability": "charisma", "spells": ["time_stop", spell]}
        ],
    )
    data["abilities"]["charisma"] = 18


@pytest.mark.parametrize(
    "spell,damage,count",
    [
        ("spirit_guardians", "radiant", 3),
        ("conjure_woodland_beings", "force", 5),
    ],
)
def test_paid_retained_hazard_caster_movement_ends_only_its_creating_stop(spell, damage, count):
    state, data, stop_params = base._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero_body = next(e for e in scene["entities"] if e["id"] == "hero")
    hero_body["metadata"]["alignment_ethic"] = "good"
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -100
    hazard = base._grant(state, data, spell)
    _bard_sources(data, spell)
    hazard.pop("target", None)
    if spell == "spirit_guardians":
        hazard.update(
            environment_unaffected_targets=["other"], environment_spirit_appearance="angelic"
        )
    else:
        hazard.update(environment_force_targets=["guard"], environment_include_origin=False)
    stop = base._start(state, data, stop_params)
    receipt, tick = stop["cast_id"], state[f"{SRD}.state"]["lasting_tick"]
    _run("cast_spell", state, participant_data=data, **hazard)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["created_time_stop_id"] == receipt
    assert state[f"{SRD}.state"]["time_stop_source"]["active"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert not state[f"{SRD}.state"]["time_stop_source"]["spell_action_active"]
    result = _run(
        "move_srd_scene_creature",
        state,
        participant_data=data,
        actor_state="hero",
        destination=[30, 0, 0],
    )
    stopped = state[f"{SRD}.state"]["time_stop_source"]
    assert not stopped["active"] and not stopped["ending"]
    assert stopped["end_reason"] == "other_creature"
    key = "last_spirit_guardians_save" if spell == "spirit_guardians" else "last_woodland_save"
    saved = state[f"{SRD}.entities"]["guard"][key]
    assert saved["raw_damage"]["count"] == count
    assert (
        source.get("damage_type", _runtime()[0]["definitions"]["spells"][spell]["damage_type"])
        == damage
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - count
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
    assert source["active"]
    assert result.result["context"]["_scene_movement_cost"] == 30
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_" + ("3" if count == 3 else "4")]
        == 2
    )


def test_older_guardians_positive_delivery_and_unattributed_packet_preserve_stop():
    state, data, stop_params = base._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(e for e in scene["entities"] if e["id"] == "hero")["metadata"]["alignment_ethic"] = "good"
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -100
    hazard = base._grant(state, data, "spirit_guardians", environment_unaffected_targets=["other"])
    _bard_sources(data, "spirit_guardians")
    hazard.pop("target", None)
    _run("cast_spell_outside_combat", state, participant_data=data, **hazard)
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["created_time_stop_id"] is None
    base._start(state, data, stop_params)
    _run(
        "move_srd_scene_creature",
        state,
        participant_data=data,
        actor_state="hero",
        destination=[30, 0, 0],
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 997
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
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 995
    assert state[f"{SRD}.state"]["time_stop_source"]["active"]


def test_conjured_creation_and_each_fresh_receipt_keep_original_producer_bodies():
    runtime = _load_runtime((PARENT / "selected-runtime.yaml").read_text())
    initial = presence._celestial()
    runtime["tools"].update(
        {
            "install_srd_conjured_presence": presence._install(),
            "resolve_srd_celestial_initial_lights": initial,
            "resolve_srd_celestial_event_lights": celestial._event_batch(initial),
            "attack_srd_conjured_fey": presence._fey_attack(),
        }
    )
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    bound = owned.bind_runtime_consumers(text)
    assert _load_runtime(owned.restore_runtime(bound)) == runtime
    assert _load_runtime(owned.bind_runtime_consumers(bound)) == _load_runtime(bound)
    tools = _load_runtime(bound)["tools"]
    for name, receipt in (
        ("resolve_srd_black_tentacles_event", "_black_tentacles_effective_damage"),
        ("apply_srd_conjure_elemental_damage", "_elemental_applied_damage"),
    ):
        callbacks = [
            n
            for nodes in effects._lists(tools[name]["effects"])
            for n in nodes
            if n.get("call") == owned.OBSERVER
        ]
        assert (
            len(callbacks) == 1
            and callbacks[0]["params"]["effective_damage"] == "{" + receipt + "}"
        )
    # Existing Time Stop and final packet owners, schemas, definitions and sources are untouched.
    for name in (
        runtime["tools"].keys() - owned.PRODUCERS.keys() - {"install_srd_conjured_presence"}
    ):
        assert tools[name] == runtime["tools"][name]
    for key in runtime.keys() - {"tools"}:
        assert _load_runtime(bound)[key] == runtime[key]
