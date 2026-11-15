"""Travel preserves actual water exit evidence before the outer fall completion boundary."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from graphlib import TopologicalSorter
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_courier_chronology import _emit
from scripts.srd_full.srd_source_relocation_node import bind_runtime_consumers as split_travel
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_tsunami import _body_tool, _exit_tool, validate_profiles
from scripts.srd_full.srd_tsunami_completion import _effects
from scripts.srd_full.srd_tsunami_completion import bind_runtime as bind_completion
from scripts.srd_full.srd_tsunami_travel import (
    CAPTURE,
    ENTRIES,
    NODE,
    OBSERVE,
    WRAPPER,
    bind_runtime_consumers,
)

from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests import test_srd_tsunami as tsunami
from tests import test_srd_tsunami_source_adoption as source
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _prepare_mage
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice

BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-tsunami-travel/runtime.yaml")
)
BASE_SHA256 = "e3c4858d2b42efd1b4aba54ae570ca55a53cb87f83b6eab9b8b894fe0ad1a813"


@lru_cache
def _static_runtime():
    """Use the immutable actual Travel body; no cast fixture or full family build is needed."""
    encoded = BASE.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == BASE_SHA256
    text = split_travel(encoded.decode().replace("\r\n", "\n"))
    text = _emit(
        text,
        {
            "resolve_srd_tsunami_body_segment": _body_tool(),
            "queue_srd_tsunami_exit": _exit_tool(),
        },
    )
    return text, _load_runtime(text)


def test_actual_travel_graph_and_post_receiving_suffix_are_preserved():
    validate_profiles()
    text, before = _static_runtime()
    result = bind_runtime_consumers(text)
    after = _load_runtime(result)
    original = before["tools"][WRAPPER]
    observed = after["tools"][WRAPPER]
    assert observed["parameters"] == original["parameters"]
    assert observed["metadata"] == original["metadata"]
    assert observed["pre_conditions"] == original["pre_conditions"]
    assert observed["effects"][2] == original["effects"][0]
    assert observed["effects"][4:] == original["effects"][1:]
    assert observed["effects"][1]["on_true"][0]["call"] == CAPTURE
    assert observed["effects"][3]["on_true"][0]["call"] == OBSERVE
    assert observed["effects"][0]["set"] == {"field": ENTRIES, "value": []}
    for name, owner in before["tools"].items():
        if name != WRAPPER:
            assert after["tools"][name] == owner
    assert after["tools"][NODE] == before["tools"][NODE]
    assert bind_runtime_consumers(result) == result
    graph = {
        name: {effect["call"] for effect in _effects(tool["effects"]) if "call" in effect}
        for name, tool in after["tools"].items()
    }
    assert len(list(TopologicalSorter(graph).static_order())) >= len(after["tools"])
    assert not graph[OBSERVE] & {"begin_srd_scene_fall", "settle_srd_tsunami_exits_node"}
    for name in (CAPTURE, OBSERVE, "check_srd_tsunami_travel_source"):
        assert all(parameter["model_hidden"] for parameter in after["tools"][name]["parameters"])
    motion = next(p for p in after["tools"][OBSERVE]["parameters"] if p["name"] == "motion")
    assert motion["from_state"] == "state.spatial_scene"


@pytest.mark.parametrize("fault", ["missing_receipt", "double_node", "raw_mutation"])
def test_unsupported_relocation_shape_rejects_before_rewriting_owners(fault):
    _, original = _static_runtime()
    runtime = deepcopy(original)
    wrapper = runtime["tools"][WRAPPER]
    if fault == "missing_receipt":
        wrapper["effects"][0]["export_context"].pop("_travel_scene_move")
    elif fault == "double_node":
        wrapper["effects"].append(deepcopy(wrapper["effects"][0]))
    else:
        wrapper["effects"].append({"spatial_mutation": {}})
    encoded = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    with pytest.raises(ValueError):
        bind_runtime_consumers(encoded)
    assert _load_runtime(encoded) == runtime


@lru_cache
def _runtime(*, retiring=False, placement=False):
    """Compose actual paid Source and final relocation before new exit/outer completion joins."""
    _, encoded = source._runtime()
    text = split_travel(encoded.decode())
    if retiring or placement:
        # Exercise an authentic source-ending callback at the node's receiving seam.
        # The fixture chooses its timing; actual focus/retirement/exit owners do the work.
        tools = _load_runtime(text)["tools"]
        node = deepcopy(tools[NODE])
        index = next(i for i, effect in enumerate(node["effects"]) if "spatial_mutation" in effect)
        callbacks = [
            {
                "set": {
                    "field": "_tsunami_callback_source",
                    "value": "lasting_state.tsunami_sources[0]",
                }
            },
            {
                "call": "retire_srd_tsunami_focus",
                "params": {
                    "caster_state": "{_tsunami_callback_source.caster}",
                    "source_id": "{_tsunami_callback_source.id}",
                    "sequence": "{_tsunami_callback_source.sequence}",
                },
            },
            {
                "call": "retire_srd_tsunami_source",
                "params": {"source_id": "{_tsunami_callback_source.id}"},
            },
        ]
        if placement:
            callbacks = [
                {
                    "call": "resolve_srd_travel_placement",
                    "params": {
                        "subject": "{subject}",
                        "destination": "{destination}",
                        "plane": "{plane}",
                        "excluded": [],
                    },
                    "export_context": {"_travel_placement": "_travel_placement"},
                },
                {
                    "require": {
                        "condition": "_travel_placement.distance == 0",
                        "error": "Use an actual unoccupied in-bounds endpoint.",
                    }
                },
            ]
        node["effects"][index:index] = callbacks
        text = _emit(text, {NODE: node})
    text = bind_runtime_consumers(text)
    text = bind_completion(text, tsunami.PUBLIC_TOOLS)
    return _load_runtime(text), text.encode()


@pytest.fixture
def travel_scope(monkeypatch, request):
    selected_case = getattr(request, "param", False)

    @lru_cache
    def selected():
        return _runtime(retiring=selected_case is True, placement=selected_case == "placement")

    monkeypatch.setattr(scope, "_runtime", selected)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(tsunami.PUBLIC_TOOLS))
    execute = package_runtime.execute_tool

    def evidence(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("Actual Tsunami Travel failure:", result.tool_name, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", evidence)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _paid_source(monkeypatch):
    state, data, params = source._case()
    slots = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"]
    monkeypatch.setattr(dice, "_rng", _TravelDice([(10, 1)] * 6 + [(20, 20)]))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    tsunami._complete(state, data, params)
    assert tsunami._source(state)["cast_id"].startswith("srd_cast:")
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == slots - 1
    return state, data


@pytest.mark.parametrize(
    "travel_scope", [False, True], indirect=True, ids=["cross-plane", "retired"]
)
def test_paid_source_exit_uses_actual_new_plane_and_preserves_newly_earned_fall(
    travel_scope, monkeypatch, request
):
    state, _ = _paid_source(monkeypatch)
    original = deepcopy(tsunami._source(state))
    retiring = request.node.callspec.params["travel_scope"]
    plane = "material" if retiring else "fire"
    _run(WRAPPER, state, subject="guard", destination=[60, 0, 20], plane=plane)
    saved = state[f"{SRD}.state"]
    exits = [row for row in saved["tsunami_exits"] if row["pending"]]
    assert len(exits) == 1
    assert exits[0]["source_id"] == original["id"]
    assert exits[0]["sequence"] == original["sequence"]
    assert exits[0]["position"] == [60, 0, 20] and exits[0]["plane"] == plane
    assert _point(state, "guard") == [60, 0, 20]
    assert saved.get("pending_srd_action_stack") in (None, [])
    if retiring:
        assert not tsunami._source(state)["active"]
        assert exits[0]["move_revision"] == saved["spatial_scene"]["map"]["revision"]
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    else:
        assert tsunami._source(state)["active"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already earned"):
        _run(WRAPPER, state, subject="guard", destination=[70, 0, 20], plane=plane)
    assert state == before


@pytest.mark.parametrize("travel_scope", ["placement"], indirect=True)
def test_paid_source_failed_motion_is_atomic_and_later_success_queues_only_one_exit(
    travel_scope, monkeypatch
):
    state, _ = _paid_source(monkeypatch)
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _run(WRAPPER, state, subject="guard", destination=[40000, 0, 20], plane="material")
    assert state == before
    _run(WRAPPER, state, subject="guard", destination=[30, 0, 0], plane="material")
    assert not state[f"{SRD}.state"].get("has_tsunami_pending_exits", False)
    _run(WRAPPER, state, subject="guard", destination=[60, 0, 20], plane="material")
    pending = [row for row in state[f"{SRD}.state"]["tsunami_exits"] if row["pending"]]
    assert len(pending) == 1 and pending[0]["position"] == [60, 0, 20]


def test_paid_misty_step_water_exit_automatically_opens_real_fall_reopens_and_retries(
    travel_scope, monkeypatch, tmp_path
):
    def case(_spell="tsunami"):
        state, data, params = source._case()
        scene = state[f"{SRD}.state"]["spatial_scene"]
        mage = deepcopy(next(body for body in scene["entities"] if body["id"] == "guard"))
        mage.update(id="mage", position=[30, 10, 20])
        mage["metadata"]["participant_ref"] = "mage"
        scene["entities"].append(mage)
        scene["geometry"].append(
            {
                "id": "mage-platform",
                "map_id": scene["map"]["id"],
                "volume": {"kind": "box", "min": [27, 7, 19], "max": [33, 13, 20]},
                "active": True,
                "movement": "blocking",
                "occupies_cells": True,
                "visibility": "opaque",
                "metadata": {"plane": "material", "ground": True},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", case)
    faces = [(10, 1)] * 6 + [(20, 20), (20, 20), (6, 2), (6, 3)]
    actual = _TravelDice(faces)
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "tsunami-travel.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "tsunami")
        base = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(params),
        )

        def act(identity, name, parameters):
            command = replace(
                base,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=parameters,
            )
            result = owner.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            snapshot = owner.snapshot("casting")
            assert owner.execute_action(command) == result
            assert owner.snapshot("casting") == snapshot
            return result, snapshot

        prepared, _ = _prepare_mage(
            owner, [{"spell": "misty_step", "uses_per_day": 1}], strength_save=0
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        act("begin", "begin_srd_spell_cast", _begin(params))
        act(
            "minute",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        _, cast = act("water", "cast_spell_outside_combat", params)
        assert tsunami._source(cast.state)["active"]
        moved, moved_snapshot = act(
            "misty",
            "cast_spell_outside_combat",
            {
                "caster_state": "mage",
                "spell": "misty_step",
                "travel_destination": [55, 10, 20],
                "verbal_component_available": True,
                "all_targets_willing": True,
                "material_item_ids": [],
            },
        )
        state = moved_snapshot.state[f"{SRD}.state"]
        trigger = state["pending_srd_action_stack"][-1]
        assert state["pending_srd_actions"][trigger]["kind"] == "scene_fall"
        assert moved.result["context"]["_tsunami_exit_action_id"] == trigger
        assert _point(moved_snapshot.state, "mage") == [55, 10, 20]
        assert (
            moved_snapshot.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["misty_step"]
            == 0
        )
        assert tsunami._source(moved_snapshot.state)["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == moved_snapshot
        _, landed = act("fall", "complete_srd_pending_action", {"pending_action_id": trigger})
        assert _point(landed.state, "mage") == [55, 10, 0]
        assert landed.state[f"{SRD}.entities"]["mage"]["hp"] == 17
        assert "prone" in landed.state[f"{SRD}.entities"]["mage"]["conditions"]
        assert landed.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert actual.consumed == faces
