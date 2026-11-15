"""Actual source retirement queues support loss before the real fall allocator runs."""

from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import SPELL_SUPPORT_TOOLS
from scripts.srd_full.srd_mirage_support_lifecycle import HANDOFF, bind_runtime_consumers
from scripts.srd_full.srd_reaction_spells import _scene_fall_window
from scripts.srd_full.srd_spell_exile_composition import bind_runtime as bind_action_completion
from scripts.srd_full.srd_spell_trigger_composition import _effect_lists
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_mirage_support as support
from tests import test_srd_spell_result_cards as cards
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_action_budget import SRD, _run


@lru_cache
def _runtime():
    _, content = support._runtime()
    # Use the exact production allocator; the support observer's earlier test
    # contract stays uncalled and proves none of this lifecycle's allocation.
    text = _replace_tools(content.decode(), {"begin_srd_scene_fall": _scene_fall_window()})
    text = bind_runtime_consumers(text)
    return _load_runtime(text), text.encode()


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
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
def _authored(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])


def test_paid_source_retirement_retains_actual_receipt_before_one_real_fall_allocation():
    state, source = support._case(paid=True)
    slots = deepcopy(support._actor(state)["resources"])
    hp = support._actor(state)["hp"]
    _run("retire_srd_mirage_source", state, mirage_id=source, reason="dispel")
    lasting = state[f"{SRD}.state"]
    receipt = deepcopy(lasting["mirage_pending_support_losses"][0])
    assert lasting["has_mirage_pending_support_losses"] is True
    assert receipt["mirage_id"] == source and receipt["body"]["actor_id"] == "hero"
    assert receipt["ground_height"] == 30
    assert not lasting.get("pending_srd_actions")
    assert support._body(state)["position"] == [0, 0, 30]
    assert support._actor(state)["hp"] == hp
    _run(HANDOFF, state)
    lasting = state[f"{SRD}.state"]
    action = lasting["pending_srd_action_stack"][-1]
    event = lasting["pending_srd_actions"][action]
    assert event["kind"] == "scene_fall" and event["status"] == "open"
    assert event["context"]["mirage_support_receipts"] == [receipt]
    assert event["parameters"]["falling_events"][0]["distance"] == 30
    assert lasting["mirage_pending_support_losses"] == []
    assert lasting.get("has_mirage_pending_support_losses", False) is False
    assert support._actor(state)["resources"] == slots
    assert support._actor(state)["hp"] == hp
    saved = deepcopy(state)
    _run(HANDOFF, state)
    assert state == saved


def test_actual_independent_support_settles_receipt_without_a_fall():
    state, source = support._case()
    floor = _geometry("independent-floor", [-5, -5, 25], [5, 5, 30])
    floor["map_id"] = support._scene(state)["map"]["id"]
    floor["metadata"].update(plane="material", ground=True)
    support._scene(state)["geometry"].append(floor)
    _run("retire_srd_mirage_source", state, mirage_id=source, reason="dispel")
    _run(HANDOFF, state)
    lasting = state[f"{SRD}.state"]
    assert not lasting.get("pending_srd_actions")
    assert lasting.get("has_mirage_pending_support_losses", False) is False
    assert support._body(state)["position"] == [0, 0, 30]


def test_missing_actual_landing_ground_rejects_without_dropping_the_receipt():
    state, source = support._case()
    scene = support._scene(state)
    scene["geometry"] = [row for row in scene["geometry"] if row["id"] != "floor"]
    _run("retire_srd_mirage_source", state, mirage_id=source, reason="dispel")
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="landing ground"):
        _run(HANDOFF, state)
    assert state == saved


def test_unhanded_actual_retirement_queue_fences_ordinary_support_and_time_atomically():
    state, source = support._case()
    _run("retire_srd_mirage_source", state, mirage_id=source, reason="dispel")
    saved = deepcopy(state)
    for name, choices in (
        ("require_srd_pending_window_closed", {}),
        ("advance_srd_lasting_time", {"elapsed_rounds": 1}),
    ):
        with pytest.raises(RulesetPreconditionRejected, match="queued Mirage support loss"):
            _run(name, state, **choices)
        assert state == saved
    _run(HANDOFF, state)
    assert state[f"{SRD}.state"]["pending_srd_action_stack"]


def test_retirement_queue_has_no_allocator_and_lifecycle_emission_repeats():
    runtime, content = _runtime()

    for name in (
        "retire_srd_mirage_source",
        "retire_srd_mirage_piece",
        "queue_srd_mirage_support_loss",
    ):
        calls = {
            effect["call"]
            for branch in _effect_lists(runtime["tools"][name]["effects"])
            for effect in branch
            if "call" in effect
        }
        assert "begin_srd_scene_fall" not in calls
    assert _load_runtime(bind_runtime_consumers(content.decode())) == runtime


def test_final_public_completion_preserves_the_real_allocator_without_a_call_cycle():
    _, content = _runtime()
    text = bind_action_completion(content.decode(), SPELL_SUPPORT_TOOLS)
    tools = _load_runtime(text)["tools"]
    marker = "state.has_mirage_pending_support_losses == true"
    for name in ("cast_spell", "cast_spell_outside_combat", "complete_srd_pending_action"):
        assert any(
            effect.get("condition") in {marker, marker.replace("state.", "lasting_state.")}
            and effect.get("on_true", [{}])[0].get("call") == HANDOFF
            for effect in tools[name]["effects"]
        ), name
    assert _load_runtime(bind_action_completion(text, SPELL_SUPPORT_TOOLS))["tools"] == tools
    graph = {
        name: {
            effect["call"]
            for branch in _effect_lists(tool["effects"])
            for effect in branch
            if "call" in effect
        }
        for name, tool in tools.items()
    }
    visited = set()

    def visit(name, ancestors):
        assert name not in ancestors, (name, ancestors)
        if name in visited:
            return
        assert name in graph, name
        for child in graph[name]:
            visit(child, ancestors | {name})
        visited.add(name)

    for name in graph:
        visit(name, set())
