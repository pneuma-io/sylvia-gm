"""Minor cantrips consume actual scene facts, source limits and durable casting costs."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import (
    SPELL_DEFINITIONS_END,
    SPELL_DEFINITIONS_START,
    _replace_generated_block,
    refresh_exploration_runtime,
)
from scripts.srd_full.srd_spell_minor_world import (
    PROFILES,
    PUBLIC_TOOLS,
    SOURCE_PAGES,
    bind_d20_runtime,
    extend_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_result_cards as spell_cards
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

SRD = objects.SRD


@lru_cache
def _runtime_bytes():
    text = bind_d20_runtime(extend_runtime(objects._runtime_bytes().decode()))
    runtime = _load_runtime(text)
    definitions = runtime["definitions"]["spells"] | PROFILES
    text = _replace_generated_block(
        text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        [
            "    " + spell + ": " + json.dumps(profile)
            for spell, profile in sorted(definitions.items())
        ],
    )
    runtime = _load_runtime(text)
    changed = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | set(PROFILES))
                changed[name] = tool
    return refresh_exploration_runtime(_replace_tools(text, changed)).encode()


@lru_cache
def _mechanic(name):
    runtime = _load_runtime(_runtime_bytes().decode())
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.1.0",
        package_sha256="a" * 64,
        runtime_bytes=_runtime_bytes(),
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _run(name, state, **parameters):
    result = _mechanic(name)(
        MechanicContext(
            campaign_id="minor-world",
            turn_id="turn",
            action_id=name,
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload({}),
        )
    )
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result.result["context"]


def _subject(state, kind, **changes):
    objects._object(state).update(height=0.5, footprint={"kind": "box", "half_extents": [0.4, 0.4]})
    return _run(
        "record_srd_minor_world_subject",
        state,
        object_id="door",
        kind=kind,
        nonliving=changes.pop("nonliving", True),
        **changes,
    )


def _cast(state, spell, mode, **changes):
    return _run(
        "cast_spell_minor_world_spell",
        state,
        **{
            "caster_state": "hero",
            "spell": spell,
            "object_id": "door",
            "minor_mode": mode,
            "minor_object_id": "door",
            "minor_point": [3, 0, 0],
            **changes,
        },
    )


def _effects(state):
    return [r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "minor_world_spell"]


def _reject(name, state, **parameters):
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _run(name, state, **parameters)
    assert state == before


def test_actual_source_pages_and_fixed_minor_world_quantities():
    corpus = json.loads((objects.ROOT / "content/corpus/spells.json").read_text())
    pages = {p["page"]: p["text_sha256"] for p in corpus["page_records"]}
    assert {p: pages[p] for p in SOURCE_PAGES} == SOURCE_PAGES
    assert PROFILES["druidcraft"]["forecast_hours"] == 24
    assert PROFILES["elementalism"]["water_cups"] == 1
    assert PROFILES["prestidigitation"]["maximum_noninstantaneous_effects"] == 3
    assert PROFILES["thaumaturgy"]["maximum_voice_multiplier"] == 3


def test_bloom_and_actual_fire_kind_do_not_restore_object_hp_or_reprepare_flame():
    state = objects._state()
    _subject(state, "flower", nonliving=False)
    _cast(state, "druidcraft", "bloom")
    assert objects._object(state)["metadata"]["srd_bloomed"] is True
    _reject("record_srd_minor_world_subject", state, object_id="door", kind="torch", nonliving=True)
    state = objects._state()
    _subject(state, "lamp", lit=False, exposed=True)
    _reject(
        "cast_spell_minor_world_spell",
        state,
        caster_state="hero",
        spell="druidcraft",
        minor_mode="fire_play",
        minor_object_id="door",
        minor_point=[3, 0, 0],
    )
    _cast(state, "elementalism", "beckon_fire")
    assert objects._object(state)["metadata"]["srd_ordinary_flame"]["burning"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert objects._object(state)["metadata"]["srd_ordinary_flame"]["burning"] is True
    assert _effects(state) == []


def test_weather_uses_actual_local_next_day_forecast_and_one_round_source_lifetime():
    state = objects._state()
    _run(
        "record_srd_local_weather_forecast",
        state,
        caster_state="hero",
        forecast="Synthetic rain, then clearing",
    )
    _cast(
        state,
        "druidcraft",
        "weather_sensor",
        minor_description="Caller cannot replace the forecast",
    )
    record = _effects(state)[0]
    assert record["description"] == "Synthetic rain, then clearing"
    assert (
        state[f"{SRD}.state"]["srd_last_minor_world_event"]["description"] == record["description"]
    )
    assert record["expires_tick"] - record["started_tick"] == 2
    assert record["dispel_ongoing"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _effects(state) == []
    _reject(
        "cast_spell_minor_world_spell",
        state,
        caster_state="hero",
        spell="druidcraft",
        minor_mode="weather_sensor",
        minor_point=[3, 0, 0],
    )


def test_water_uses_actual_capacity_one_cup_evaporation_and_source_consumption():
    state = objects._state()
    _subject(state, "other")
    _run(
        "record_srd_object_container",
        state,
        object_id="door",
        capacity_gallons=0.125,
        water_gallons=0.0625,
        is_open=True,
    )
    _cast(state, "elementalism", "beckon_water", minor_water_mode="cup")
    record = _effects(state)[0]
    assert record["water_gallons"] == 0.0625
    assert record["expires_tick"] - record["started_tick"] == 20
    _reject(
        "cast_spell_minor_world_spell",
        state,
        caster_state="hero",
        spell="elementalism",
        minor_mode="beckon_water",
        minor_object_id="door",
        minor_point=[3, 0, 0],
        minor_water_mode="cup",
    )
    _run("consume_srd_minor_world_water", state, object_id="door", source_id=record["source_id"])
    assert _effects(state) == []
    _cast(state, "elementalism", "beckon_water", minor_water_mode="cup")
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _effects(state) == []
    assert objects._object(state)["metadata"]["srd_object_container"]["water_gallons"] == 0.0625


def test_area_air_respects_actual_held_open_and_locked_access_and_five_foot_cube():
    state = objects._state()
    _subject(state, "door", held_open=True)
    _run(
        "record_srd_object_access",
        state,
        object_id="door",
        object_kind="door",
        closed=False,
        locks=[],
        stuck=False,
        barred=False,
    )
    _cast(state, "elementalism", "beckon_air")
    assert objects._object(state)["metadata"]["srd_object_access"]["closed"] is False
    state = objects._state()
    _subject(state, "door")
    _run(
        "record_srd_object_access",
        state,
        object_id="door",
        object_kind="door",
        closed=False,
        locks=[],
        stuck=False,
        barred=False,
    )
    _cast(state, "elementalism", "beckon_air")
    assert objects._object(state)["metadata"]["srd_object_access"]["closed"] is True
    assert objects._object(state)["movement"] == "blocking"
    _cast(state, "thaumaturgy", "invisible_hand", minor_operation="open")
    assert objects._object(state)["metadata"]["srd_object_access"]["closed"] is False
    assert objects._object(state)["movement"] == "passable"


def test_sculpt_and_sensation_enforce_different_printed_cube_and_volume_limits():
    state = objects._state()
    _subject(state, "water")
    _cast(state, "elementalism", "sculpt_element", minor_description="Crude fish")
    assert _effects(state)[0]["expires_tick"] == 1200
    assert _effects(state)[0]["dispel_ongoing"] is False
    objects._object(state)["footprint"]["half_extents"] = [0.6, 0.1]
    _reject(
        "cast_spell_minor_world_spell",
        state,
        caster_state="hero",
        spell="elementalism",
        minor_mode="sculpt_element",
        minor_object_id="door",
        minor_point=[3, 0, 0],
    )
    _cast(state, "prestidigitation", "minor_sensation", minor_operation="chill")
    assert len(_effects(state)) == 2
    assert _effects(state)[-1]["dispel_ongoing"] is True
    _cast(state, "prestidigitation", "clean_soil", minor_operation="soil")
    assert objects._object(state)["metadata"]["srd_minor_cleanliness"] == "soil"


def test_source_owned_max_three_exact_retirement_and_booming_voice_eligibility():
    state = objects._state()
    _subject(state, "torch", lit=True)
    _cast(state, "thaumaturgy", "altered_eyes")
    _cast(state, "thaumaturgy", "booming_voice")
    voice = _effects(state)[-1]
    assert voice["object_id"] == ""
    assert voice["output_id"] == ""
    hero = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )
    assert voice["origin"] == hero["position"]
    _cast(state, "thaumaturgy", "fire_play", minor_operation="color", minor_color="violet")
    assert len(_effects(state)) == 3
    _reject(
        "cast_spell_minor_world_spell",
        state,
        caster_state="hero",
        spell="thaumaturgy",
        minor_mode="tremors",
        minor_point=[3, 0, 0],
    )
    first = _effects(state)[0]["source_id"]
    _cast(state, "thaumaturgy", "tremors", minor_retire_source_id=first)
    assert len(_effects(state)) == 3
    assert first not in {r["source_id"] for r in _effects(state)}
    selected = _effects(state)[0]["source_id"]
    context = _run("read_srd_minor_world_source", state, source_id=selected)
    assert context["_minor_world_source"]["spell_level"] == 0
    assert context["_minor_world_source"]["caster"] == "hero"
    for actor, ability, skill, expected in [
        ("hero", "charisma", "intimidation", True),
        ("hero", "charisma", "persuasion", False),
        ("other", "charisma", "intimidation", False),
        ("hero", "strength", "intimidation", False),
    ]:
        context = _run(
            "check_srd_minor_world_d20_advantage",
            state,
            actor_state=actor,
            kind="ability_check",
            ability=ability,
            skill=skill,
        )
        assert context["_minor_world_advantage"] is expected
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _effects(state) == []
    context = _run(
        "check_srd_minor_world_d20_advantage",
        state,
        actor_state="hero",
        kind="ability_check",
        ability="charisma",
        skill="intimidation",
    )
    assert context["_minor_world_advantage"] is False


def test_minor_emission_keeps_its_paid_cast_dispatch_and_turn_hook_unique():
    emitted = _runtime_bytes().decode()
    first = _load_runtime(extend_runtime(emitted))
    second = _load_runtime(extend_runtime(extend_runtime(emitted)))
    for name in (
        "cast_spell",
        "process_srd_turn_effects",
        "cast_spell_object_content_spell",
        "place_srd_created_supply_container",
    ):
        assert first["tools"][name] == second["tools"][name]
    assert (
        _load_runtime(bind_d20_runtime(emitted))["tools"]["d20_test"]
        == _load_runtime(bind_d20_runtime(bind_d20_runtime(emitted)))["tools"]["d20_test"]
    )


def test_minor_creation_actual_hand_fiction_and_real_caster_next_turn_only():
    state = objects._state()
    _run(
        "record_srd_hand_sized_form",
        state,
        caster_state="hero",
        form_id="top",
        fiction="Tiny wooden spinning top fitting the hero's actual hand",
        kind="trinket",
        extent=[0.2, 0.2, 0.2],
    )
    _reject(
        "cast_spell_minor_world_spell",
        state,
        caster_state="hero",
        spell="prestidigitation",
        minor_mode="minor_creation",
        minor_point=[3, 0, 0],
        minor_form_id="unknown",
        minor_output_id="top",
    )
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True, active_actor="hero")
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 4
    _cast(state, "prestidigitation", "minor_creation", minor_form_id="top", minor_output_id="top")
    record = _effects(state)[0]
    assert record["turn"] == 5
    top = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "top")
    assert top["metadata"]["srd_minor_creation"]["damage_capable"] is False
    assert top["metadata"]["srd_minor_creation"]["monetary_worth_gp"] == 0
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="hero",
        boundary_phase="end",
        boundary_turn=4,
    )
    assert len(_effects(state)) == 1
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="other",
        boundary_phase="end",
        boundary_turn=8,
    )
    assert len(_effects(state)) == 1
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="hero",
        boundary_phase="end",
        boundary_turn=5,
    )
    assert _effects(state) == []
    assert (
        next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "top")[
            "active"
        ]
        is False
    )


@pytest.mark.parametrize(
    "face,axis,sign",
    [
        ("east", 0, 1),
        ("west", 0, -1),
        ("north", 1, 1),
        ("south", 1, -1),
        ("up", 2, 1),
        ("down", 2, -1),
    ],
)
def test_source_cube_faces_use_actual_bounds_and_exclude_outside_flames(face, axis, sign):
    state = objects._state()
    _subject(state, "lamp", lit=False, exposed=True)
    origin = [3, 0, 3]
    position = list(origin)
    position[axis] += sign * 4
    objects._object(state)["position"] = position
    _cast(state, "elementalism", "beckon_fire", minor_point=origin, minor_cube_face=face)
    assert objects._object(state)["metadata"]["srd_ordinary_flame"]["burning"] is True
    geometry = _effects(state)[0]["geometry"]
    assert geometry["kind"] == "cube"
    assert geometry["face"] == face
    assert geometry["max"][axis] - geometry["min"][axis] == 5
    assert geometry["min" if sign > 0 else "max"][axis] == origin[axis]
    assert state[f"{SRD}.state"]["srd_last_minor_world_event"]["geometry"] == geometry
    state = objects._state()
    _subject(state, "lamp", lit=False, exposed=True)
    position[axis] = origin[axis] + sign * 6
    objects._object(state)["position"] = position
    _cast(state, "elementalism", "beckon_fire", minor_point=origin, minor_cube_face=face)
    assert objects._object(state)["metadata"]["srd_ordinary_flame"]["burning"] is False


def test_cube_consumes_actual_holder_position_and_partial_object_intersection():
    state = objects._state()
    _subject(state, "lamp", lit=False, exposed=True)
    row = objects._object(state)
    row["position"] = [90, 0, 0]
    row["metadata"]["carried_by"] = "guard"
    _cast(state, "elementalism", "beckon_fire")
    assert objects._object(state)["position"] == [3, 2, 0]
    assert objects._object(state)["metadata"]["srd_ordinary_flame"]["burning"] is True
    state = objects._state()
    _subject(state, "lamp", lit=False, exposed=True)
    objects._object(state)["position"] = [5.7, 0, 0]
    _cast(state, "elementalism", "beckon_fire")
    assert objects._object(state)["metadata"]["srd_ordinary_flame"]["burning"] is True


def test_earth_ground_squares_persist_and_sculpt_fire_requires_actual_burning_source():
    state = objects._state()
    _cast(state, "elementalism", "beckon_earth", minor_description="Thin sand")
    _cast(state, "elementalism", "beckon_earth", minor_point=[8, 0, 0], minor_earth_material="sand")
    ground = deepcopy(state[f"{SRD}.state"]["srd_minor_earth_shrouds"])
    assert len(ground) == 2
    assert len({record["source_id"] for record in ground}) == 2
    assert ground[0]["square_side_feet"] == 5
    assert ground[0]["area_square_feet"] == 25
    assert ground[0]["caster"] == "hero"
    assert [record["material"] for record in ground] == ["dirt", "sand"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert state[f"{SRD}.state"]["srd_minor_earth_shrouds"] == ground
    state = objects._state()
    _subject(state, "fire", lit=False, exposed=True)
    _reject(
        "cast_spell_minor_world_spell",
        state,
        caster_state="hero",
        spell="elementalism",
        object_id="door",
        minor_mode="sculpt_element",
        minor_object_id="door",
    )
    state = objects._state()
    _subject(state, "fire", lit=True, exposed=True)
    _cast(state, "elementalism", "sculpt_element", minor_description="Crude dragon")
    assert _effects(state)[0]["expires_tick"] == 1200


def test_temporary_cup_shares_capacity_and_partial_destruction_with_water_and_food():
    state = objects._state()
    _subject(state, "other")
    _run(
        "record_srd_object_container",
        state,
        object_id="door",
        capacity_gallons=0.125,
        water_gallons=0.03125,
        is_open=True,
    )
    _cast(state, "elementalism", "beckon_water", minor_water_mode="cup")

    def water(mode, quantity):
        return _run(
            "cast_spell_object_content_spell",
            state,
            caster_state="hero",
            object_id="door",
            spell="create_or_destroy_water",
            slot_level=1,
            object_mode=mode,
            object_quantity=quantity,
        )

    water("create_water", 1)
    assert objects._object(state)["metadata"]["srd_object_container"]["water_gallons"] == 0.0625
    assert (
        _run("read_srd_minor_world_water", state, object_id="door")["_minor_water_present"]
        == 0.0625
    )
    water("destroy_water", 0.03125)
    assert _effects(state)[0]["water_gallons"] == 0.03125
    _run(
        "place_srd_created_supply_container",
        state,
        caster_state="hero",
        object_id="door",
        food_remaining=45,
        water_remaining=30,
    )
    assert objects._object(state)["metadata"]["srd_created_supply"]["water_gallons"] == 0.03125
    water("destroy_water", 0.0625)
    assert _effects(state) == []
    metadata = objects._object(state)["metadata"]
    assert metadata["srd_created_supply"]["water_gallons"] == 0
    assert metadata["srd_object_container"]["water_gallons"] == 0.0625


@lru_cache
def _test_archive():
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    runtime = _load_runtime(_runtime_bytes().decode())
    existing = {t["name"] for t in record["tools"]}
    public = PUBLIC_TOOLS | objects.PUBLIC_TOOLS
    for name in existing | public:
        if name not in runtime["tools"]:
            continue
        schema = _tool_schema(runtime["tools"][name], tool_name=name)
        if name in existing:
            next(t for t in record["tools"] if t["name"] == name)["input_schema"] = schema
        else:
            record["tools"].append(
                {
                    "name": name,
                    "capability": "dnd.srd.5.2.1.spells",
                    "input_schema": schema,
                    "writers": ["dm"],
                    "turn_statuses": ["open"],
                    "state_predicates": [],
                }
            )
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": _runtime_bytes()}
    content = json.loads(payload["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "synthetic-minor-world-fixture",
            "kind": "ruleset_runtime",
            "family": "synthetic-minor-world-fixture",
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
                "tools": sorted(public),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["synthetic-minor-world-public-cast"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def _create_minor_campaign(owner, monkeypatch, *, hero_spell="fire_bolt"):
    state = objects._state()
    objects._object(state).update(height=0.5, footprint={"kind": "box", "half_extents": [0.4, 0.4]})
    create = objects.public_casting._create_spell_campaign
    definitions = spell_cards._spell_definitions() | PROFILES

    def create_selected_spell(actual_owner, _spell):
        return create(actual_owner, hero_spell)

    with (
        patch.object(objects, "_test_archive", _test_archive),
        patch.object(objects, "_state", lambda: deepcopy(state)),
        patch.object(objects.public_casting, "_create_spell_campaign", create_selected_spell),
        patch.object(spell_cards, "_spell_definitions", lambda: definitions),
    ):
        return objects._create_object_campaign(owner, monkeypatch)


@pytest.mark.parametrize(
    "spell,mode,kind,operation",
    [
        ("druidcraft", "bloom", "flower", "light"),
        ("elementalism", "beckon_fire", "lamp", "light"),
        ("prestidigitation", "clean_soil", "other", "clean"),
        ("thaumaturgy", "booming_voice", "other", "light"),
    ],
)
def test_real_public_minor_cast_card_actual_costs_and_saved_reopen_retry(
    tmp_path, monkeypatch, spell, mode, kind, operation
):
    database = tmp_path / (spell + ".db")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_minor_campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(owner, [{"spell": spell, "uses_per_day": 0}])
        assert prepared.status is ActionStatus.COMMITTED
        admitted, _ = _act(
            owner,
            "record_srd_minor_world_subject",
            object_id="door",
            kind=kind,
            nonliving=kind != "flower",
            lit=False,
            exposed=True,
        )
        assert admitted.status is ActionStatus.COMMITTED
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell=spell,
            minor_mode=mode,
            minor_object_id="door",
            minor_point=[3, 0, 0],
            minor_operation=operation,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert (
            receipt.result["result_card"]["title"]
            == _load_runtime(_runtime_bytes().decode())["definitions"]["spell_casting"][spell][
                "name"
            ]
        )
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][spell] == 0
        if spell == "druidcraft":
            assert objects._object(saved.state)["metadata"]["srd_bloomed"] is True
        elif spell == "elementalism":
            assert objects._object(saved.state)["metadata"]["srd_ordinary_flame"]["burning"] is True
        elif spell == "prestidigitation":
            assert objects._object(saved.state)["metadata"]["srd_minor_cleanliness"] == "clean"
        else:
            assert len(_effects(saved.state)) == 1
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "cast_spell_outside_combat",
                caster_state="mage",
                spell=spell,
                minor_mode=mode,
                minor_point=[3, 0, 0],
                advantage=True,
                damage=999,
            )
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved


def test_public_minor_creation_spends_real_action_and_expires_at_actual_next_caster_end(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(5))
    database = tmp_path / "minor-next-turn.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_minor_campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(owner, [{"spell": "prestidigitation", "uses_per_day": 0}])
        assert prepared.status is ActionStatus.COMMITTED
        admitted, _ = _act(
            owner,
            "record_srd_hand_sized_form",
            caster_state="mage",
            form_id="top",
            fiction="Wooden top fitting this actual mage's hand",
            kind="trinket",
            extent=[0.2, 0.2, 0.2],
        )
        assert admitted.status is ActionStatus.COMMITTED
        started, _ = _act(
            owner, "start_group_combat_encounter", combatants=["mage", "hero", "guard"]
        )
        assert started.status is ActionStatus.COMMITTED
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "mage"
        receipt, command = _act(
            owner,
            "cast_spell",
            caster_state="mage",
            spell="prestidigitation",
            minor_mode="minor_creation",
            minor_form_id="top",
            minor_output_id="top",
            minor_point=[3, 0, 0],
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        cast = owner.snapshot("casting")
        assert cast.state[f"{SRD}.combat"]["action_available"] is False
        assert cast.state[f"{SRD}.entities"]["mage"]["spell_slot_cast_this_turn"] is False
        assert (
            _effects(cast.state)[0]["turn"]
            == cast.state[f"{SRD}.entities"]["mage"]["turns_started"] + 1
        )
        with pytest.raises(MechanicsRejected, match="action budget"):
            _act(
                owner,
                "cast_spell",
                caster_state="mage",
                spell="prestidigitation",
                minor_mode="sensory_effect",
                minor_point=[3, 0, 0],
                verbal_component_available=True,
                somatic_component_available=True,
            )
        assert owner.snapshot("casting") == cast
        ended, _ = _act(owner, "end_combat_turn")
        assert ended.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert len(_effects(saved.state)) == 1
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved
        other_turns = []
        for _ in range(3):
            advanced, _ = _act(reopened, "advance_group_combat_encounter_turn")
            assert advanced.status is ActionStatus.COMMITTED
            begun, _ = _act(reopened, "begin_combat_turn")
            assert begun.status is ActionStatus.COMMITTED
            actor = reopened.snapshot("casting").state[f"{SRD}.combat"]["active_actor"]
            if actor == "mage":
                break
            other_turns.append(actor)
            ended, _ = _act(reopened, "end_combat_turn")
            assert ended.status is ActionStatus.COMMITTED
            assert len(_effects(reopened.snapshot("casting").state)) == 1
        assert set(other_turns) == {"hero", "guard"}
        assert reopened.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "mage"
        ended, end_command = _act(reopened, "end_combat_turn")
        assert ended.status is ActionStatus.COMMITTED
        final = reopened.snapshot("casting")
        assert _effects(final.state) == []
        top = next(
            row
            for row in final.state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == "top"
        )
        assert top["active"] is False
        assert top["metadata"]["srd_minor_creation"]["damage_capable"] is False
        assert top["metadata"]["srd_minor_creation"]["monetary_worth_gp"] == 0
        assert reopened.execute_action(end_command) == ended
        assert reopened.snapshot("casting") == final


def test_real_public_booming_voice_drives_actual_intimidation_dice_and_card_on_reopen(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(4))
    database = tmp_path / "booming-check.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_minor_campaign(owner, monkeypatch, hero_spell="thaumaturgy")
        cast, _ = _act(
            owner,
            "cast_spell_outside_combat",
            **{
                **parameters,
                "minor_mode": "booming_voice",
            },
        )
        assert cast.status is ActionStatus.COMMITTED
        checks = {
            "actor": "hero",
            "actor_state": "hero",
            "ability": "charisma",
            "kind": "ability_check",
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": 12,
        }
        ordinary, _ = _act(owner, "d20_test", skill="persuasion", **checks)
        assert len(ordinary.result["context"]["_d20_test"]["results"]) == 1
        receipt, command = _act(owner, "d20_test", skill="intimidation", **checks)
        assert receipt.status is ActionStatus.COMMITTED
        assert len(receipt.result["context"]["_d20_test"]["results"]) == 2
        assert receipt.result["context"]["_condition_advantage"] is True
        assert receipt.result["result_card"]["title"] == "Intimidation"
        assert receipt.result["result_card"]["details"]
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved
        advanced, _ = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=10)
        assert advanced.status is ActionStatus.COMMITTED
        expired, _ = _act(reopened, "d20_test", skill="intimidation", **checks)
        assert len(expired.result["context"]["_d20_test"]["results"]) == 1
        assert _effects(reopened.snapshot("casting").state) == []


def test_paid_public_water_shares_temporary_cup_capacity_and_reopens_without_repaying(
    tmp_path, monkeypatch
):
    database = tmp_path / "minor-water-shared.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_minor_campaign(owner, monkeypatch)
        parts = spell_cards._casting_headers()["create_or_destroy_water"]["material_parts"]
        materials = [
            {
                "id": "water-sand",
                "inventory_item": "water-sand",
                "kind": "specific",
                "components": [parts[0]["id"]],
                "quantity": parts[0]["quantity"],
                "value_cp": 0,
            }
        ]
        prepared, _ = _prepare_mage(
            owner,
            [
                {"spell": "elementalism", "uses_per_day": 0},
                {"spell": "create_or_destroy_water", "uses_per_day": 1},
            ],
            inventory=["water-sand"],
            spell_materials=materials,
        )
        assert prepared.status is ActionStatus.COMMITTED
        admitted, _ = _act(
            owner, "record_srd_minor_world_subject", object_id="door", kind="other", nonliving=True
        )
        assert admitted.status is ActionStatus.COMMITTED
        admitted, _ = _act(
            owner,
            "record_srd_object_container",
            object_id="door",
            capacity_gallons=0.125,
            water_gallons=0.03125,
            is_open=True,
        )
        assert admitted.status is ActionStatus.COMMITTED
        cup, _ = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="elementalism",
            minor_mode="beckon_water",
            minor_water_mode="cup",
            minor_object_id="door",
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert cup.status is ActionStatus.COMMITTED
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="create_or_destroy_water",
            object_target_id="door",
            object_mode="create_water",
            object_quantity=1,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            material_item_ids=["water-sand"],
        )
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _effects(saved.state)[0]["water_gallons"] == 0.0625
        assert (
            objects._object(saved.state)["metadata"]["srd_object_container"]["water_gallons"]
            == 0.0625
        )
        assert (
            saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][
                "create_or_destroy_water"
            ]
            == 0
        )
        assert receipt.result["result_card"]["title"] == "Create or Destroy Water"
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved


def test_public_temporary_creation_after_combat_exit_uses_existing_exploration_clock(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(5))
    database = tmp_path / "minor-combat-exit.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_minor_campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(owner, [{"spell": "prestidigitation", "uses_per_day": 0}])
        assert prepared.status is ActionStatus.COMMITTED
        admitted, _ = _act(
            owner,
            "record_srd_hand_sized_form",
            caster_state="mage",
            form_id="top",
            fiction="Wooden top fitting this mage's hand",
            kind="trinket",
            extent=[0.2, 0.2, 0.2],
        )
        assert admitted.status is ActionStatus.COMMITTED
        started, _ = _act(
            owner, "start_group_combat_encounter", combatants=["mage", "hero", "guard"]
        )
        assert started.status is ActionStatus.COMMITTED
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "mage"
        receipt, _ = _act(
            owner,
            "cast_spell",
            caster_state="mage",
            spell="prestidigitation",
            minor_mode="minor_creation",
            minor_form_id="top",
            minor_output_id="top",
            minor_point=[3, 0, 0],
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED
        ended, _ = _act(owner, "end_combat_turn")
        assert ended.status is ActionStatus.COMMITTED
        exited, _ = _act(owner, "end_combat_encounter", end_reason="fighting_stopped")
        assert exited.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.combat"]["encounter_active"] is False
        assert len(_effects(saved.state)) == 1
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        advanced, command = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=2)
        assert advanced.status is ActionStatus.COMMITTED
        final = reopened.snapshot("casting")
        assert _effects(final.state) == []
        assert (
            next(
                row
                for row in final.state[f"{SRD}.state"]["spatial_scene"]["entities"]
                if row["id"] == "top"
            )["active"]
            is False
        )
        assert reopened.execute_action(command) == advanced
        assert reopened.snapshot("casting") == final
