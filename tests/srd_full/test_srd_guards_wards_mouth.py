"""Ward mouths deliver real saved trigger messages under exact group lifetime."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_guards_wards import PROFILES, extend_runtime
from scripts.srd_full.srd_guards_wards_mouth import bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

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
    local_native = ROOT / ".scratch/secure-material-parent-v1/actual-runtime.yaml"
    default_runtime = (
        local_native
        if local_native.exists()
        else ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    )
    runtime_path = Path(os.environ.get("FC14_GUARDS_MOUTH_RUNTIME", str(default_runtime)))
    text = runtime_path.read_text()
    text = bind_runtime(extend_runtime(text))
    assert bind_runtime(text) == text
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    pending = [
        "record_srd_object_access",
        "cast_srd_guards_wards",
        "end_srd_guards_wards_effect",
        "advance_srd_lasting_clock",
        "resolve_srd_spell_trigger",
        "apply_srd_trigger_spell",
        "speak_srd_guards_wards_password",
    ]
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "call" in item:
                    pending.append(item["call"])
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    closure = runtime | {"tools": selected}
    assert not (errors := validate_ruleset_data(closure)), errors
    return closure, yaml.dump(closure, Dumper=_RuntimeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, raw = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="c" * 64,
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
def _owner(monkeypatch):
    monkeypatch.setattr(objects, "_mechanic", _mechanic)


def _mouth(anchor="door", **choices):
    return {
        "object_id": anchor,
        "words": ["Stand", "back"],
        "description": "An approaching creature speaks aloud.",
        "events": ["audible"],
        "delivery_seconds": 1,
        "repeat": True,
    } | choices


def _state():
    state = objects._state()
    objects._access(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    door = objects._object(state)
    door["metadata"]["is_object"] = True
    bell = deepcopy(door)
    bell.update(id="bell", position=[15, 0, 0])
    bell["metadata"].pop("srd_arcane_locks", None)
    bell["metadata"].update(name="Synthetic brass bell", closable=False)
    scene["entities"].append(bell)
    scene["zones"] = [
        {
            "id": "actual-room-light",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **scene["map"]["bounds"]},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    return state


def _ward(state, **choices):
    context = objects._run(
        "cast_srd_guards_wards",
        state,
        caster_state="hero",
        slot_level=6,
        origin=[0, 0, 0],
        caster_can_touch_origin=True,
        other_effect="magic_mouth",
        mouths=[_mouth(), _mouth("bell", words=["Go", "away"])],
        password="silver threshold",
        **choices,
    )
    return context["_new_guards_wards_source_id"]


def _event(state, tid, actor="guard"):
    return objects._run(
        "resolve_srd_spell_trigger",
        state,
        trigger_id=tid,
        event_actor=actor,
        event_kind="audible",
        fictional_match=True,
    )


def test_two_actual_ward_mouths_deliver_saved_messages_and_repeat_after_reopen():
    state = _state()
    sid = _ward(state)
    parent = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    ids = parent["mouth_trigger_ids"]
    assert len(ids) == 2 and parent["other"] == "magic_mouth"
    rows = [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("instance") in ids]
    assert all(
        r["spell"] == "guards_and_wards"
        and r["spell_level"] == 6
        and r["guards_wards_source_serial"] == parent["serial"]
        for r in rows
    )
    assert _event(state, ids[0])["_trigger_result"] == "Stand back"
    state = json.loads(json.dumps(state))
    assert _event(state, ids[0])["_trigger_fired"] is False
    assert _event(state, ids[1])["_trigger_result"] == "Go away"
    objects._run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    assert _event(state, ids[0])["_trigger_result"] == "Stand back"


def test_exact_other_group_ending_preserves_ordinary_mouth_and_three_ward_groups():
    state = _state()
    sid = _ward(state)
    independent = objects._run(
        "apply_srd_trigger_spell",
        state,
        caster_state="hero",
        spell="magic_mouth",
        slot_level=2,
        save_dc=15,
        trigger_anchor="bell",
        trigger_anchor_kind="object",
        trigger_events=["audible"],
        mouth_words=["Independent"],
        mouth_repeat=True,
    )["_trigger_id"]
    objects._run("end_srd_guards_wards_effect", state, source_id=sid, group="other")
    parent = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    assert parent["groups"] == {"corridors": True, "doors": True, "stairs": True, "other": False}
    assert parent["installed"] and parent["mouth_trigger_ids"] == []
    assert _event(state, independent)["_trigger_result"] == "Independent"
    assert state[f"{SRD}.state"]["has_spell_triggers"] is True


def test_password_and_actual_parent_expiry_prevent_ward_mouth_delivery():
    state = _state()
    sid = _ward(state)
    ids = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["mouth_trigger_ids"][:]
    objects._run(
        "speak_srd_guards_wards_password",
        state,
        actor_state="guard",
        source_id=sid,
        password="silver threshold",
    )
    assert _event(state, ids[0])["_trigger_fired"] is False
    assert len(state[f"{SRD}.state"]["turn_effects"]) == 2
    objects._run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=28800)
    assert not state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"]
    assert not any(r.get("instance") in ids for r in state[f"{SRD}.state"]["turn_effects"])


def test_invalid_second_real_location_rolls_back_the_entire_parent_install():
    state = _state()
    next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "bell")[
        "position"
    ] = [60, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="inside the ward"):
        _ward(state)
    assert state == before


def test_caster_is_affected_unless_explicitly_designated_immune():
    state = _state()
    sid = _ward(state)
    tid = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["mouth_trigger_ids"][0]
    assert _event(state, tid, actor="hero")["_trigger_result"] == "Stand back"
    immune = _state()
    sid = _ward(immune, designated_creatures=["hero"])
    tid = immune[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["mouth_trigger_ids"][0]
    assert _event(immune, tid, actor="hero")["_trigger_fired"] is False


def test_object_sight_is_qualified_before_the_parent_creates_its_own_fog():
    runtime, _ = _runtime()
    effects = runtime["tools"]["cast_srd_guards_wards"]["effects"]
    qualification = next(
        i for i, e in enumerate(effects) if "qualify_srd_guarded_magic_mouth_location" in str(e)
    )
    installation = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field")
        == "lasting_state.spell_guards_wards_sources.{_new_guards_wards_source_id}"
    )
    assert qualification < installation
    state = _state()
    sid = _ward(state, corridors=[{"min": [0, 0, 0], "max": [20, 5, 5]}])
    parent = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    assert parent["mouth_qualified_object_ids"] == ["door", "bell"]
    assert len(parent["mouth_trigger_ids"]) == 2
