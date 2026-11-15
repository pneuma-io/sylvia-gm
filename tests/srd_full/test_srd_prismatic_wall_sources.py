"""Actual finite cast identity and exact removal preserve independent Wall consequences."""

import importlib.util
import json
import os
import re
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import (
    CASTING_REQUIREMENTS_END,
    CASTING_REQUIREMENTS_START,
    SPELL_DEFINITIONS_END,
    SPELL_DEFINITIONS_START,
    _casting_requirements,
    _replace_generated_block,
)
from scripts.srd_full.srd_actor_spell_modifiers import _emit
from scripts.srd_full.srd_prismatic_wall import PROFILES, extend_runtime
from scripts.srd_full.srd_prismatic_wall_sources import SOURCE_INSTALLER, SOURCE_SPELL, bind_runtime
from scripts.srd_full.srd_spell_removal_adoption import _bind_selected_recovery
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as actions
from tests import test_srd_prismatic_wall as wall
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_result_cards as cards

ROOT = Path(__file__).resolve().parents[2]
SRD = objects.SRD
NATIVE = Path(
    os.environ.get(
        "FC14_PRISMATIC_WALL_RUNTIME",
        ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml",
    )
)
SOURCE_PARENT = Path(
    os.environ.get(
        "FC14_PRISMATIC_WALL_SOURCE_PARENT",
        ROOT / "scripts/srd_spell_source_instances.py",
    )
)


def _header():
    corpus = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_text())
    page = next(p for p in corpus["page_records"] if p["page"] == 155)
    assert page["text_sha256"] == "342899e59dee0efc061250eb63707e22c9dbf0c19c6a228b89f816062058f7ee"
    lines = [(155, line) for line in page["text"].splitlines()]
    start = next(i for i, (_, line) in enumerate(lines) if line.strip() == "Prismatic Wall")
    return {
        "name": lines[start][1].strip(),
        **_casting_requirements(lines, start + 1, SOURCE_SPELL),
    }


def _source_parent():
    if "FC14_PRISMATIC_WALL_SOURCE_PARENT" in os.environ:
        assert sha256(SOURCE_PARENT.read_bytes()).hexdigest() == (
            "bbdf6f178dce12788045d3bae5e755b83055f56a5341e0d6b7c2d3e374b1bfaf"
        )
    spec = importlib.util.spec_from_file_location("scripts._wall_source_parent", SOURCE_PARENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Root owns these exact registration entries; the dependency itself stays immutable.
    module.SPECIFIC_DURATION_OWNERS[SOURCE_SPELL] = SOURCE_INSTALLER
    module.INSTALLERS.add(SOURCE_INSTALLER)
    return module


@lru_cache
def _runtime():
    supplied = os.environ.get("FC14_PRISMATIC_WALL_SOURCES_RUNTIME")
    if supplied:
        raw = Path(supplied).read_bytes()
        runtime = _load_runtime(raw.decode())
        assert runtime["definitions"]["spell_casting"][SOURCE_SPELL] == _header()
        assert _load_runtime(bind_runtime(raw.decode()))["tools"] == runtime["tools"]
        assert not (errors := validate_ruleset_data(runtime)), errors
        return raw, runtime
    if "FC14_PRISMATIC_WALL_RUNTIME" in os.environ:
        assert sha256(NATIVE.read_bytes()).hexdigest() == (
            "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
        )
    text = extend_runtime(NATIVE.read_text())
    runtime = _load_runtime(text)
    definitions = runtime["definitions"]["spells"] | PROFILES
    requirements = runtime["definitions"]["spell_casting"] | {SOURCE_SPELL: _header()}
    for begin, end, data in (
        (SPELL_DEFINITIONS_START, SPELL_DEFINITIONS_END, definitions),
        (CASTING_REQUIREMENTS_START, CASTING_REQUIREMENTS_END, requirements),
    ):
        text = _replace_generated_block(
            text,
            begin,
            end,
            ["    " + key + ": " + json.dumps(row) for key, row in sorted(data.items())],
        )
    runtime = _load_runtime(text)
    changed = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | {SOURCE_SPELL})
                changed[name] = tool
    text = _emit(text, changed)
    text = _source_parent().extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = deepcopy(tools)
    _bind_selected_recovery(changed)
    text = _emit(text, {"cast_srd_recovery": changed["cast_srd_recovery"]})
    text = bind_runtime(text)
    assert bind_runtime(text) == text
    runtime = _load_runtime(text)
    # Normal final exploration emission clones the settled regular cast. Keep
    # that actual alias boundary after Source emission in this selected graph.
    runtime["tools"]["cast_spell_outside_combat"] = deepcopy(runtime["tools"]["cast_spell"])
    roots = [
        "cast_spell_outside_combat",
        "dismiss_srd_spell",
        "cast_srd_recovery",
        "begin_srd_spell_source_instance",
        SOURCE_INSTALLER,
        "retire_srd_prismatic_wall_owned_cast",
        "collect_srd_prismatic_wall_owned_cast",
        "dispel_srd_prismatic_wall_owned_source",
        "reach_into_srd_prismatic_wall",
        "retire_srd_prismatic_wall_layer",
        "advance_srd_lasting_time",
    ]
    selected = {}
    while roots:
        name = roots.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "call" in node:
                    roots.append(node["call"])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)

    # Final Reaction emission restores the literal package scene path after
    # helper state binding. Mirror that exact Wave35 dependency in this owner
    # closure; the whole final Reaction graph remains Root's integration proof.
    for tool in selected.values():
        stack = [tool]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in node:
                        body = node[kind]
                        body["scene"] = re.sub(r"^lasting_state\.", "state.", body["scene"])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)

    class NoAliases(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
        def ignore_aliases(self, data):
            return True

    closure = runtime | {"tools": selected}
    raw = yaml.dump(closure, Dumper=NoAliases, sort_keys=False).encode()
    errors = validate_ruleset_data(closure)
    if errors:
        print("\n".join(errors))
    assert not errors, errors
    (ROOT / ".scratch/prismatic-wall-source-proof-runtime.yaml").write_bytes(raw)
    return raw, closure


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="f" * 64,
        runtime_bytes=raw,
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
    monkeypatch.setattr(objects, "_mechanic", _mechanic)
    monkeypatch.setattr(actions, "_mechanic", _mechanic)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _allocate(state, actor="hero"):
    return objects._run(
        "begin_srd_spell_source_instance",
        state,
        caster_state=actor,
        spell=SOURCE_SPELL,
        slot_level=9,
    )["_spell_source_cast_id"]


def _case():
    state = objects._state()
    wall._position(state, "hero")["position"] = [-10, -5, 0]
    wall._position(state, "guard")["position"] = [10, -5, 0]
    wall._position(state, "other")["position"] = [20, -5, 0]
    cast_id = _allocate(state)
    objects._run(
        SOURCE_INSTALLER,
        state,
        caster_state="hero",
        slot_level=9,
        save_dc=40,
        origin=[10, 0, 5],
        length=20,
        height=10,
        spell_cast_id=cast_id,
        spell_cast_owner="hero",
        actual_spell_level=9,
    )
    source_id = state[f"{SRD}.entities"]["hero"]["last_prismatic_wall_cast"]["source_id"]
    return state, source_id, cast_id


def _dispel(state, source_id, *, slot=3, modifier=0):
    return objects._run(
        "cast_srd_recovery",
        state,
        caster_state="hero",
        spell="dispel_magic",
        slot_level=slot,
        casting_modifier=modifier,
        casting_ability="intelligence",
        save_dc=13,
        concentration_save_modifier=0,
        can_touch=False,
        recovery_choice=SOURCE_SPELL,
        recovery_source_id=source_id,
        recovery_position=[10, 0, 5],
    )


def _expose_violet(state, source_id):
    # These source-owner retirements set up the already-exposed layer only; they
    # are not evidence of genuine destruction-producing spells/attacks.
    for layer in range(1, 7):
        objects._run(
            "retire_srd_prismatic_wall_layer",
            state,
            source_id=source_id,
            expected_layer=layer,
            reason="fixture layer retirement",
        )


def test_real_allocated_owner_is_required_and_regeneration_is_stable():
    state, source_id, cast_id = _case()
    record = wall._source(state, source_id)
    assert record["cast_id"] == cast_id == "srd_cast:1"
    assert record["source_caster_id"] == "hero" and record["spell_level"] == 9
    assert record["ongoing_duration"] is True and record["dispel_ongoing"] is False
    before = deepcopy(state)
    for change in (
        {"spell_cast_owner": "guard"},
        {"spell_cast_id": "srd_cast:99"},
        {"actual_spell_level": 8},
    ):
        with pytest.raises(RulesetPreconditionRejected):
            objects._run(
                SOURCE_INSTALLER,
                state,
                **{
                    "caster_state": "hero",
                    "slot_level": 9,
                    "save_dc": 40,
                    "origin": [10, 0, 5],
                    "length": 20,
                    "height": 10,
                    "spell_cast_id": cast_id,
                    "spell_cast_owner": "hero",
                    "actual_spell_level": 9,
                    **change,
                },
            )
        assert state == before


def test_actual_shared_dismissal_selects_only_owned_cast_and_preserves_independent_consequences():
    state, source_id, cast_id = _case()
    wall._position(state, "guard")["position"] = [10, -5, 0]
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=1000, max_hp=1000, temp_hp=0)
    target["saving_throw_modifiers"].update(dexterity=-100, constitution=-100, wisdom=-100)
    objects._run("reach_into_srd_prismatic_wall", state, target_state="guard", source_id=source_id)
    consequences = deepcopy(state[f"{SRD}.state"]["timed_condition_sources"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        objects._run("dismiss_srd_spell", state, caster_state="guard", cast_id=cast_id)
    assert state == before
    second_cast = _allocate(state)
    objects._run(
        SOURCE_INSTALLER,
        state,
        caster_state="hero",
        slot_level=9,
        save_dc=40,
        origin=[30, 0, 5],
        length=10,
        height=10,
        spell_cast_id=second_cast,
        spell_cast_owner="hero",
        actual_spell_level=9,
    )
    second_id = state[f"{SRD}.entities"]["hero"]["last_prismatic_wall_cast"]["source_id"]
    reopened = json.loads(json.dumps(state))
    context = objects._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id=cast_id)
    assert context["_dismiss_count"] == 1
    assert wall._source(reopened, source_id)["installed"] is False
    assert wall._source(reopened, second_id)["installed"] is True
    assert reopened[f"{SRD}.state"]["timed_condition_sources"] == consequences


def test_unexposed_layers_have_no_effect_or_roll_from_real_selected_dispel():
    state, source_id, _ = _case()
    context = _dispel(state, source_id, slot=9)
    assert context["_dispel_checks"] == [
        {
            "spell": SOURCE_SPELL,
            "source_id": source_id,
            "ended": False,
            "roll": None,
            "dc": 19,
            "no_effect": True,
        }
    ]
    assert wall._source(state, source_id)["layers_remaining"] == 7
    assert "Only the exposed Violet" in str(context["_spell_detail_rows"])


@pytest.mark.parametrize("modifier,ended", [(-100, False), (100, True)])
def test_exposed_violet_consumes_one_actual_source_level_check(modifier, ended):
    state, source_id, _ = _case()
    _expose_violet(state, source_id)
    context = _dispel(state, source_id, modifier=modifier)
    check = context["_dispel_checks"][0]
    assert check["ended"] is ended and check["dc"] == 19
    assert check["roll"]["modifier"] == modifier
    assert check["roll"]["total"] in range(1, 21)
    assert wall._source(state, source_id)["installed"] is not ended


def test_exposed_violet_high_slot_is_automatic_and_expired_source_rejects_without_mutation():
    state, source_id, _ = _case()
    _expose_violet(state, source_id)
    context = _dispel(state, source_id, slot=9)
    assert context["_dispel_checks"][0]["ended"] is True
    assert context["_dispel_checks"][0]["roll"] is None
    state, source_id, cast_id = _case()
    state[f"{SRD}.state"]["lasting_tick"] = 200
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _dispel(state, source_id, slot=9)
    assert state == before
    with pytest.raises(RulesetPreconditionRejected):
        objects._run("dismiss_srd_spell", state, caster_state="hero", cast_id=cast_id)
    assert state == before


@pytest.mark.parametrize("occupied", [False, True])
def test_paid_named_cast_allocates_one_owner_slot_and_shows_retained_reference(
    monkeypatch, occupied
):
    headers = cards._casting_headers() | {SOURCE_SPELL: _header()}
    profiles = cards._spell_definitions() | PROFILES
    monkeypatch.setattr(cards, "_casting_headers", lambda: headers)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: profiles)
    state, data, parameters = cards._spell_case(SOURCE_SPELL)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = deepcopy(objects._state()[f"{SRD}.state"]["spatial_scene"])
    state[f"{SRD}.state"]["spatial_scene"] = scene
    for actor, position in (("hero", [-10, -5, 0]), ("guard", [10, -5, 0]), ("other", [20, -5, 0])):
        wall._position(state, actor)["position"] = position
    if occupied:
        wall._position(state, "guard")["position"] = [10, 0, 0]
    result = actions._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **{
            **parameters,
            "prismatic_wall_origin": [10, 0, 5],
            "prismatic_wall_length": 20,
            "prismatic_wall_height": 10,
        },
    )
    assert result.result["status"] == "committed"
    cast_id = state[f"{SRD}.entities"]["hero"]["last_spell_source_cast_id"]
    source_id = state[f"{SRD}.entities"]["hero"]["last_prismatic_wall_cast"]["source_id"]
    assert cast_id == "srd_cast:1"
    if occupied:
        assert source_id is None
        assert not state[f"{SRD}.state"].get("spell_prismatic_wall_sources")
        assert "spell ends with no effect" in str(result.result["result_card"])
    else:
        assert wall._source(state, source_id)["cast_id"] == cast_id
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    assert cast_id in str(result.result["result_card"])
    reopened = json.loads(json.dumps(state))
    if occupied:
        before = deepcopy(reopened)
        with pytest.raises(RulesetPreconditionRejected):
            objects._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id=cast_id)
        assert reopened == before
    else:
        objects._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id=cast_id)
        assert wall._source(reopened, source_id)["installed"] is False
