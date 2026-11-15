"""Genuine installed Wall bodies retain ordered saves and independent ray consequences."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import (
    SPELL_DEFINITIONS_END,
    SPELL_DEFINITIONS_START,
    _replace_generated_block,
)
from scripts.srd_full.srd_prismatic_wall import PROFILES, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_spell_object_content as objects

SRD = objects.SRD
ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    base = Path(
        os.environ.get(
            "FC14_PRISMATIC_WALL_RUNTIME", ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
        )
    )
    text = extend_runtime(base.read_text())
    assert extend_runtime(text) == text
    text = bind_runtime_consumers(text)
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    definitions = runtime["definitions"]["spells"] | PROFILES
    text = _replace_generated_block(
        text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        ["    " + key + ": " + json.dumps(row) for key, row in sorted(definitions.items())],
    )
    runtime = _load_runtime(text)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | set(PROFILES))
    assert not (errors := validate_ruleset_data(runtime)), errors
    pending, selected = (
        [
            "record_srd_object_access",
            "cast_srd_prismatic_wall",
            "qualify_srd_prismatic_wall_source",
            "end_srd_prismatic_wall_source",
            "retire_srd_prismatic_wall_layer",
            "refresh_srd_prismatic_walls",
            "resolve_srd_prismatic_wall_contact",
            "reach_into_srd_prismatic_wall",
            "resolve_srd_prismatic_wall_near",
            "resolve_srd_prismatic_indigo_end_turn",
            "resolve_srd_prismatic_violet_start_turn",
            "resolve_srd_prismatic_wall_segment",
            "check_srd_prismatic_wall_light",
            "check_srd_prismatic_wall_spell_segment",
            "check_srd_prismatic_wall_observation",
            "check_srd_prismatic_wall_attack_segment",
            "resolve_srd_prismatic_wall_body_segment",
            "move_srd_travel_subject",
            "settle_srd_prismatic_wall_movements",
            "move_srd_scene_creature_environment_segment",
            "begin_combat_turn",
            "advance_srd_lasting_time",
        ],
        {},
    )
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if "call" in value:
                    pending.append(value["call"])
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    class NoAliases(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    closure = runtime | {"tools": selected}
    return yaml.dump(closure, Dumper=NoAliases, sort_keys=False).encode(), closure


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


def _position(state, actor):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == actor
    )


def _case(**changes):
    state = objects._state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    _position(state, "hero")["position"] = [-10, -5, 0]
    _position(state, "guard")["position"] = [10, -5, 0]
    _position(state, "other")["position"] = [20, -5, 0]
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"].update(
            dexterity=-100, constitution=-100, wisdom=-100
        )
    objects._run(
        "cast_srd_prismatic_wall",
        state,
        **{
            "caster_state": "hero",
            "slot_level": 9,
            "save_dc": 40,
            "origin": [10, 0, 5],
            "length": 20,
            "height": 10,
            **changes,
        },
    )
    return state, state[f"{SRD}.entities"]["hero"]["last_prismatic_wall_cast"]["source_id"]


def _source(state, sid):
    return state[f"{SRD}.state"]["spell_prismatic_wall_sources"][sid]


def test_real_wall_has_exact_centered_body_duration_and_no_effect_if_occupied():
    state, sid = _case()
    record = _source(state, sid)
    assert record["outer_layer"] == 1 and record["layers_remaining"] == 7
    assert record["volume"]["min"] == [0, -1 / 24, 0]
    assert record["volume"]["max"] == [20, 1 / 24, 10]
    assert record["expires_tick"] == 200 and record["armor_class"] == 10
    state[f"{SRD}.entities"]["hero"]["is_alive"] = False
    record["suppressed"] = True
    assert objects._run("qualify_srd_prismatic_wall_source", state, source_id=sid)[
        "_prismatic_wall_antimagic_exempt"
    ]
    _position(state, "guard")["position"] = [10, 0, 0]
    count = len(state[f"{SRD}.state"]["spell_prismatic_wall_sources"])
    objects._run(
        "cast_srd_prismatic_wall",
        state,
        caster_state="hero",
        slot_level=9,
        save_dc=40,
        origin=[10, 0, 5],
        length=20,
        height=10,
    )
    assert len(state[f"{SRD}.state"]["spell_prismatic_wall_sources"]) == count
    assert state[f"{SRD}.entities"]["hero"]["last_prismatic_wall_cast"]["occupied"]


def test_actual_reach_rolls_all_remaining_layers_and_retains_wall_consequences_after_end():
    state, sid = _case()
    objects._run("reach_into_srd_prismatic_wall", state, target_state="guard", source_id=sid)
    target = state[f"{SRD}.entities"]["guard"]
    rows = target["last_prismatic_wall_contact"]["layers"]
    assert [row["layer"] for row in rows] == list(range(1, 8))
    assert all(
        row["raw_damage"]["count"] == 12 and row["raw_damage"]["sides"] == 6 for row in rows[:5]
    )
    assert all(row["outcome"] == "failure" for row in rows)
    assert target["hp"] == 1000 - sum(row["raw_damage"]["total"] for row in rows[:5])
    assert "restrained" in target["conditions"] and "blinded" in target["conditions"]
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    ids = list(target["timed_condition_source_ids"])
    assert len(ids) == 2
    assert all(sources[cid]["spell_id"] == "prismatic_wall" for cid in ids)
    assert all(sources[cid]["prismatic_wall_source_id"] == sid for cid in ids)
    objects._run("end_srd_prismatic_wall_source", state, source_id=sid)
    reopened = json.loads(json.dumps(state))
    for turn in (1, 2, 3):
        reopened[f"{SRD}.entities"]["guard"]["turns_started"] = turn
        objects._run("resolve_srd_prismatic_indigo_end_turn", reopened, target_state="guard")
    assert "petrified" in reopened[f"{SRD}.entities"]["guard"]["conditions"]
    assert not _source(reopened, sid)["installed"]


def test_caster_designees_and_ordered_removal_keep_other_wall_sources_independent():
    state, sid = _case(designated_creatures=["guard"])
    objects._run(
        "resolve_srd_prismatic_wall_contact",
        state,
        target_state="guard",
        caster_state="hero",
        source_id=sid,
    )
    assert state[f"{SRD}.entities"]["guard"]["last_prismatic_wall_contact"]["layers"] == []
    prior = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="order"):
        objects._run(
            "retire_srd_prismatic_wall_layer",
            state,
            source_id=sid,
            expected_layer=6,
            reason="owning source event",
        )
    assert state == prior
    for layer in range(1, 7):
        objects._run(
            "retire_srd_prismatic_wall_layer",
            state,
            source_id=sid,
            expected_layer=layer,
            reason="owning source event",
        )
    assert _source(state, sid)["outer_layer"] == 7
    query = {"start": [10, -10, 5], "end": [10, 10, 5], "plane": "material"}
    assert not objects._run("check_srd_prismatic_wall_spell_segment", state, **query)[
        "_prismatic_wall_spell_blocked"
    ]
    assert objects._run("check_srd_prismatic_wall_observation", state, **query)[
        "_prismatic_wall_observation_blocked"
    ]


def test_exact_wall_surface_light_ranges_and_plane_qualify_without_sunlight():
    state, sid = _case()
    for y, expected in ((100, "bright"), (200, "dim"), (201, None)):
        result = objects._run(
            "check_srd_prismatic_wall_light", state, point=[10, y, 5], plane="material"
        )
        assert result["_prismatic_wall_light"] == expected
        if expected:
            assert result["_prismatic_wall_light_spell_level"] == 9
            assert result["_prismatic_wall_light_source_id"] == sid
    result = objects._run(
        "check_srd_prismatic_wall_light", state, point=[10, 0, 5], plane="ethereal"
    )
    assert result["_prismatic_wall_light"] is None
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=100)
    objects._run("refresh_srd_prismatic_walls", state)
    assert not _source(state, sid)["installed"]


def test_real_ranged_classification_and_body_crossing_keep_layer_order_and_plane():
    state, sid = _case()
    query = {"start": [10, -10, 5], "end": [10, 10, 5], "plane": "material"}
    for magical in (False, True):
        assert objects._run(
            "check_srd_prismatic_wall_attack_segment", state, magical=magical, **query
        )["_prismatic_wall_attack_blocked"]
    objects._run(
        "retire_srd_prismatic_wall_layer",
        state,
        source_id=sid,
        expected_layer=1,
        reason="real producer callback",
    )
    assert not objects._run(
        "check_srd_prismatic_wall_attack_segment", state, magical=False, **query
    )["_prismatic_wall_attack_blocked"]
    assert objects._run("check_srd_prismatic_wall_attack_segment", state, magical=True, **query)[
        "_prismatic_wall_attack_blocked"
    ]
    # The actual body touches even when its centerline stays below the Wall's lower edge.
    body_height = _position(state, "guard")["height"]
    for z, expected in ((1 - body_height, True), (-body_height, False), (-1 - body_height, False)):
        result = objects._run(
            "resolve_srd_prismatic_wall_body_segment",
            state,
            subject="guard",
            source_id=sid,
            start=[10, -10, z],
            end=[10, 10, z],
        )
        assert result["_prismatic_wall_body_hit"] == expected
    objects._run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[10, 5, 0],
        plane="material",
        teleport=False,
    )
    assert "last_prismatic_wall_contact" not in state[f"{SRD}.entities"]["guard"]
    assert state[f"{SRD}.state"]["pending_prismatic_wall_movements"][0]["wall_source_ids"] == [sid]
    state = json.loads(json.dumps(state))
    objects._run("settle_srd_prismatic_wall_movements", state)
    rows = state[f"{SRD}.entities"]["guard"]["last_prismatic_wall_contact"]["layers"]
    assert [row["layer"] for row in rows] == list(range(2, 8))
    assert _position(state, "guard")["position"] == [10, 5, 0]
    settled = deepcopy(state)
    objects._run("settle_srd_prismatic_wall_movements", state)
    assert state == settled


def test_real_start_turn_sees_wall_once_then_exact_blindness_expires_independently():
    state, sid = _case()
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="guard")
    objects._run("begin_combat_turn", state, actor="guard")
    target = state[f"{SRD}.entities"]["guard"]
    assert target["last_prismatic_wall_near"]["outcome"] == "failure"
    assert "blinded" in target["conditions"]
    (condition_id,) = target["timed_condition_source_ids"]
    record = state[f"{SRD}.state"]["timed_condition_sources"][condition_id]
    assert record["expires_tick"] == 20 and record["condition"] == "blinded"
    reopened = json.loads(json.dumps(state))
    prior = deepcopy(reopened)
    objects._run(
        "resolve_srd_prismatic_wall_near",
        reopened,
        target_state="guard",
        source_id=sid,
        starting_turn=True,
    )
    assert reopened == prior
    reopened[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    objects._run("advance_srd_lasting_time", reopened, elapsed_rounds=10)
    assert "blinded" not in reopened[f"{SRD}.entities"]["guard"]["conditions"]
    assert _source(reopened, sid)["installed"]


def test_ordinary_budgeted_move_consumes_real_crossing_and_teleport_does_not():
    state, sid = _case()
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"].update(dexterity=100, constitution=100, wisdom=100)
    result = objects._run(
        "move_srd_scene_creature_environment_segment",
        state,
        actor_state="guard",
        destination=[10, 5, 0],
    )
    assert result["_scene_movement_cost"] == 10
    target = state[f"{SRD}.entities"]["guard"]
    assert target["scene_movement_spent"] == 10
    assert len(target["last_prismatic_wall_contact"]["layers"]) == 7
    assert state[f"{SRD}.state"]["pending_prismatic_wall_movements"] == []
    other = state[f"{SRD}.entities"]["other"]
    other["saving_throw_modifiers"]["constitution"] = 100
    objects._run(
        "move_srd_travel_subject",
        state,
        subject="other",
        destination=[20, 5, 0],
        plane="material",
        teleport=True,
    )
    objects._run("settle_srd_prismatic_wall_movements", state)
    other = state[f"{SRD}.entities"]["other"]
    assert "last_prismatic_wall_contact" not in other
    assert other["hp"] == 1000
    assert _source(state, sid)["installed"]
