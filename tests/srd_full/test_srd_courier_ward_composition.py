"""Courier timing retains actual movement observers when Guards and Wards extends routing."""

from copy import deepcopy
from pathlib import Path

from scripts.srd_full.srd_courier_chronology import bind_runtime_consumers
from scripts.srd_full.srd_guards_wards import extend_runtime as extend_wards
from scripts.srd_full.srd_guards_wards_web import bind_runtime_consumers as bind_web
from scripts.srd_full.srd_guards_wards_web import extend_runtime as extend_web
from scripts.srd_full.srd_spell_environment import bind_runtime_consumers as bind_environment
from scripts.srd_full.srd_spell_environment import extend_runtime as extend_environment
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime


def test_actual_guarded_web_route_preserves_courier_aftermath_without_repeating_movement():
    source = (
        Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    ).read_text(encoding="utf-8")
    text = bind_web(bind_environment(extend_web(extend_wards(extend_environment(source)))))
    runtime = _load_runtime(text)
    original = deepcopy(runtime["tools"]["move_srd_scene_creature_node"])
    index = next(
        i
        for i, effect in enumerate(original["effects"])
        if "lasting_state.has_guarded_web_sections == true" in effect.get("condition", "")
    )
    route = original["effects"][index]
    assert any(
        effect.get("set", {}).get("field") == "_ground_route_ids" for effect in route["on_true"]
    )
    emitted = _load_runtime(bind_runtime_consumers(text))["tools"]
    assert emitted["move_srd_scene_creature_node"] == original
    observed = emitted["observe_srd_courier_movement_aftermath"]
    assert observed["effects"] == [
        {"set": {"field": "_scene_movement_distance", "value": "movement_distance"}},
        *original["effects"][index + 1 :],
    ]
    assert original["effects"][index + 1 :]
    assert observed["parameters"][:-1] == original["parameters"]
