"""Source-paid setup and genuine HP damage release one deferred self spell."""

import hashlib
import importlib.util
import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_actor_spell_modifiers import _call, _when
from scripts.srd_full.srd_contingency_spell import (
    CHOICES,
    FAMILY,
    POOL,
    PROFILE,
    SOURCE_SHA256,
    SPELL,
    extend_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests.srd_full import test_srd_solid_spell_walls as force

ROOT, SRD = force.ROOT, force.SRD
BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-spell/ruleset.yaml")
)
SOURCE_PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-spell/srd_spell_source_instances.py")
)


def _header():
    corpus = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_text())
    page = next(p for p in corpus["page_records"] if p["page"] == 119)
    assert page["text_sha256"] == SOURCE_SHA256
    lines = [(119, line) for line in page["text"].splitlines()]
    start = next(i for i, (_, line) in enumerate(lines) if line.strip() == "Contingency")
    return {"name": "Contingency", **force._casting_requirements(lines, start + 1, SPELL)}


class NoAliases(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
    def ignore_aliases(self, data):
        return True


def _test_extended_capture(tools):
    """Model the proposed narrow Root capture hunk on the actual existing begin owner."""
    begin_name = "begin_srd_spell_cast_internal"
    begin = tools[begin_name]
    begin["parameters"] += deepcopy(list(CHOICES))
    public = tools["begin_srd_spell_cast"]
    public["parameters"] += deepcopy(list(CHOICES))
    for node in force._source_parent()._walk(public["effects"]):
        if node.get("call") == begin_name:
            node["params"].update({p["name"]: "{" + p["name"] + "}" for p in CHOICES})
    prepare = next(
        e for e in tools["cast_spell"]["effects"] if "prepare_srd_contingency_cast" in str(e)
    )
    index = next(
        i for i, e in enumerate(begin["effects"]) if e.get("call") == "resolve_srd_spell_materials"
    )
    begin["effects"].insert(index, deepcopy(prepare))
    for node in force._source_parent()._walk(begin["effects"]):
        if node.get("set", {}).get("field") == "caster_state.extended_casting":
            node["set"]["value"]["contingency_choices"] = {p["name"]: p["name"] for p in CHOICES}


@lru_cache
def _runtime():
    raw = BASE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    runtime = _load_runtime(raw.decode())
    runtime["definitions"]["spells"][SPELL] = PROFILE
    runtime["definitions"]["spell_casting"][SPELL] = _header()
    for tool in runtime["tools"].values():
        for p in tool["parameters"]:
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(set(p["enum_values"]) | {SPELL})
    text = extend_runtime(yaml.dump(runtime, Dumper=NoAliases, sort_keys=False))
    repeated = extend_runtime(text)
    if repeated != text:
        first_tools, next_tools = _load_runtime(text)["tools"], _load_runtime(repeated)["tools"]
        differing = {
            name: (first_tools[name], tool)
            for name, tool in next_tools.items()
            if first_tools[name] != tool
        }
        (ROOT / ".scratch/contingency-repeat-delta.json").write_text(
            json.dumps(differing, indent=2)
        )
        pytest.fail(f"Repeated Contingency emitter changed tools: {list(differing)}")
    spec = importlib.util.spec_from_file_location(
        "scripts._contingency_source_parent", SOURCE_PARENT
    )
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    source.SUPPORTED_FAMILIES.add(FAMILY)
    source.INSTALLERS.add("install_srd_contingency_source")
    text = source.extend_runtime(text)
    runtime = _load_runtime(text)
    _test_extended_capture(runtime["tools"])
    # The genuine completed typed packet witnesses; Root joins this receiving observer late.
    damage = runtime["tools"]["apply_srd_qualifying_damage"]
    damage["effects"].append(
        _when(
            "target.has_contingency == true",
            [
                _call(
                    "observe_srd_contingency_damage",
                    {"target_state": "{target}", "hp_before": "{_hp_before}"},
                )
            ],
        )
    )
    runtime["tools"]["cast_spell_outside_combat"] = deepcopy(runtime["tools"]["cast_spell"])
    selected, pending = (
        {},
        [
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_cast",
            "continue_srd_spell_cast",
            "prepare_srd_contingency_cast",
            "install_srd_contingency_source",
            "apply_srd_qualifying_damage",
            "check_srd_contingency_source",
            "end_srd_contingency_source",
            "drain_srd_contingency_releases",
            "advance_srd_lasting_time",
            "resolve_srd_respiration_interval",
            "set_srd_respiration",
            "record_srd_control_held_items",
            "drop_srd_control_held_items",
        ],
    )
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "call" in node:
                    pending.append(node["call"])
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in node:
                        node[kind]["scene"] = re.sub(
                            r"^lasting_state\.", "state.", node[kind]["scene"]
                        )
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    runtime["tools"] = selected
    assert not (errors := validate_ruleset_data(runtime)), errors
    return yaml.dump(runtime, Dumper=NoAliases, sort_keys=False).encode(), runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return force.materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="c" * 64,
        runtime_bytes=raw,
        declaration=force.ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=force.owned_payload(
                force._tool_schema(runtime["tools"][name], tool_name=name)
            ),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    from sylvia_gm.application import package_runtime as package

    original = package.execute_tool

    def checked(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print("NATIVE", args[0], result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package, "execute_tool", checked)
    monkeypatch.setattr(force.objects, "_mechanic", _mechanic)
    monkeypatch.setattr(force.actions, "_mechanic", _mechanic)


def _state(monkeypatch, child="false_life", slot=2):
    monkeypatch.setattr(force.cards, "_casting_headers", lambda: {SPELL: _header()})
    monkeypatch.setattr(force.cards, "_spell_definitions", lambda: {SPELL: PROFILE})
    state, data, params = force.cards._spell_case(SPELL)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=30, max_hp=30, temp_hp=0)
    hero["prepared_spells"].append(child)
    data["spellcasting_grants"][0]["spells"].append(child)
    data["wizard_spellbook"].append(child)
    child_parts = _runtime()[1]["definitions"]["spell_casting"][child]["material_parts"]
    child_materials = [
        {
            "id": f"child_{i}",
            "inventory_item": f"actual_{child}_{i}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": 1,
            "value_cp": part["minimum_value_cp"],
        }
        for i, part in enumerate(child_parts)
    ]
    hero["spell_materials"] += child_materials
    hero["inventory"] += [item["inventory_item"] for item in child_materials]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, phase="inactive")
    params.update(
        target=None,
        contingent_spell=child,
        contingent_slot_resource=f"spell_slot_{slot}",
        contingent_hp_threshold=20,
        contingent_material_item_ids=[item["id"] for item in child_materials],
    )
    return state, data, params


def _cast(monkeypatch, child="false_life", slot=2):
    state, data, params = _state(monkeypatch, child, slot)
    sid = _finish_setup(state, data, params)
    return state, data, params, sid


def _finish_setup(state, data, params):
    slot = int(params["contingent_slot_resource"].split("_")[-1])
    parent_before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"]
    child_before = state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"]
    begin_params = {
        p["name"]: params[p["name"]]
        for p in _runtime()[1]["tools"]["begin_srd_spell_cast"]["parameters"]
        if p["name"] in params
    }
    force.actions._run("begin_srd_spell_cast", state, participant_data=data, **begin_params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == parent_before
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == child_before
    force.objects._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    force.actions._run("cast_spell", state, participant_data=data, **params)
    sid = state[f"{SRD}.entities"]["hero"]["contingency_source_id"]
    return sid


def _damage(state, amount):
    result = force.objects._run(
        "apply_srd_qualifying_damage",
        state,
        target="hero",
        amount=amount,
        adjustment=0,
        damage_type="slashing",
        source_state_id="guard",
    )
    # This is the proposed outer-action join, after the completed packet returned.
    force.objects._run("drain_srd_contingency_releases", state)
    return result


def test_named_setup_pays_two_real_slots_and_defers_effect(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    hero = state[f"{SRD}.entities"]["hero"]
    record = state[f"{SRD}.state"][POOL][sid]
    assert hero["resources"]["spell_slot_6"] == 2
    assert hero["resources"]["spell_slot_2"] == 2
    assert hero["temp_hp"] == 0
    assert record["installed"] is True and record["status"] == "armed"
    assert record["child"]["slot_level"] == 2
    assert record["expires_tick"] - record["started_tick"] == 288000
    assert hero["spell_materials"][0]["quantity"] == 3
    assert record["source_sha256"] == SOURCE_SHA256


def test_actual_hp_damage_releases_saved_false_life_once_without_cost(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    state, _, _, sid = _cast(monkeypatch)
    reopened = json.loads(json.dumps(state))
    _damage(reopened, 10)
    hero = reopened[f"{SRD}.entities"]["hero"]
    record = reopened[f"{SRD}.state"][POOL][sid]
    assert hero["hp"] == 20 and 11 <= hero["temp_hp"] <= 17
    assert record["status"] == "released" and record["installed"] is False
    assert record["trigger_hp_before"] == 30 and record["trigger_hp_after"] == 20
    assert hero["resources"] == state[f"{SRD}.entities"]["hero"]["resources"]
    monkeypatch.setattr(
        dice, "roll", lambda *a, **kw: pytest.fail("Already released source rerolled")
    )
    before = deepcopy(reopened)
    force.objects._run(
        "observe_srd_contingency_damage", reopened, target_state="hero", hp_before=30
    )
    assert reopened == before


def test_temp_hp_only_damage_does_not_trigger(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=20, temp_hp=10)
    _damage(state, 5)
    assert hero["hp"] == 20
    assert state[f"{SRD}.state"][POOL][sid]["installed"] is True


def test_chosen_spell_grant_and_slot_failure_roll_back_setup(monkeypatch):
    state, data, params = _state(monkeypatch)
    data["spellcasting_grants"][0]["spells"].remove("false_life")
    before = deepcopy(state)
    with pytest.raises(force.RulesetPreconditionRejected, match="grant|prepared|source"):
        force.actions._run(
            "prepare_srd_contingency_cast",
            state,
            participant_data=data,
            **{
                p["name"]: params[p["name"]]
                for p in _runtime()[1]["tools"]["prepare_srd_contingency_cast"]["parameters"]
                if p["name"] in params
            },
        )
    assert state == before


def test_real_recast_ends_only_prior_contingency_and_pays_new_setup(monkeypatch):
    state, data, params, prior = _cast(monkeypatch)
    current = _finish_setup(state, data, params)
    records = state[f"{SRD}.state"][POOL]
    assert current != prior
    assert records[prior]["installed"] is False and records[prior]["end_reason"] == "recast"
    assert records[current]["installed"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 1
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 1
    assert state[f"{SRD}.state"]["contingency_caster_ids"] == ["hero"]


def test_actual_expiry_clock_retires_source_and_preserves_existing_temp_hp(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    record = state[f"{SRD}.state"][POOL][sid]
    # Boundary fixture starts one genuine round before the printed ten-day endpoint.
    state[f"{SRD}.state"]["lasting_tick"] = record["expires_tick"] - 2
    state[f"{SRD}.entities"]["hero"]["temp_hp"] = 7
    force.objects._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"][POOL][sid]["installed"] is False
    assert state[f"{SRD}.entities"]["hero"]["has_contingency"] is False
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == 7


def test_saved_damage_queue_reopens_before_exactly_one_release(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    force.objects._run(
        "apply_srd_qualifying_damage",
        state,
        target="hero",
        amount=10,
        adjustment=0,
        damage_type="slashing",
        source_state_id="guard",
    )
    record = state[f"{SRD}.state"][POOL][sid]
    assert record["installed"] is False and record["status"] == "queued"
    assert state[f"{SRD}.state"]["contingency_release_queue"] == [sid]
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == 0
    reopened = json.loads(json.dumps(state))
    force.objects._run("drain_srd_contingency_releases", reopened)
    assert reopened[f"{SRD}.state"][POOL][sid]["status"] == "released"
    assert reopened[f"{SRD}.entities"]["hero"]["temp_hp"] >= 11
    before = deepcopy(reopened)
    monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Saved release rerolled"))
    force.objects._run("drain_srd_contingency_releases", reopened)
    assert reopened == before


def test_water_breathing_releases_only_self_and_real_respiration(monkeypatch):
    state, data, _, sid = _cast(monkeypatch, "water_breathing", 3)
    force.actions._run(
        "set_srd_respiration",
        state,
        participant_data=data,
        target_state="hero",
        target_character="hero",
        medium="water",
    )
    hero = state[f"{SRD}.entities"]["hero"]
    breath_before = hero["respiration_breath_ticks"]
    _damage(state, 10)
    assert state[f"{SRD}.state"][POOL][sid]["status"] == "released"
    assert hero["resources"]["spell_slot_3"] == 2
    force.objects._run("advance_srd_lasting_time", state, elapsed_rounds=50)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["respiration_breath_ticks"] == breath_before
    assert hero["respiration_exhaustion"] == 0
    assert state[f"{SRD}.entities"]["guard"].get("timed_senses") in (None, [])
    assert state[f"{SRD}.entities"]["other"].get("timed_senses") in (None, [])


def test_actual_statuette_drop_retires_source_and_return_does_not_rearm(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    record = state[f"{SRD}.state"][POOL][sid]
    item = record["statuette_item"]
    force.objects._run(
        "record_srd_control_held_items",
        state,
        target_state="hero",
        held_items=[item],
        held_attack_names=[],
    )
    force.objects._run("drop_srd_control_held_items", state, target_state="hero")
    assert item not in state[f"{SRD}.entities"]["hero"]["inventory"]
    # Root must attach this exact callback to real item-removal/transfer owners.
    force.objects._run("check_srd_contingency_source", state, target_state="hero")
    assert state[f"{SRD}.state"][POOL][sid]["status"] == "ended"
    state[f"{SRD}.entities"]["hero"]["inventory"].append(item)
    _damage(state, 10)
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == 0


@pytest.mark.parametrize("fault", ["no_child_slot", "underpriced_statuette"])
def test_setup_rejects_actual_slot_or_material_shortfall_before_payment(monkeypatch, fault):
    state, data, params = _state(monkeypatch)
    hero = state[f"{SRD}.entities"]["hero"]
    if fault == "no_child_slot":
        hero["resources"]["spell_slot_2"] = 0
    else:
        hero["spell_materials"][0]["value_cp"] = 149999
    before = deepcopy(state)
    begin_params = {
        p["name"]: params[p["name"]]
        for p in _runtime()[1]["tools"]["begin_srd_spell_cast"]["parameters"]
        if p["name"] in params
    }
    with pytest.raises(force.RulesetPreconditionRejected):
        force.actions._run("begin_srd_spell_cast", state, participant_data=data, **begin_params)
    assert state == before
