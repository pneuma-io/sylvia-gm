"""Actual dwelling construction, guest access and queued return use shared scene owners."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.spatial import SpatialEngine
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_initial_fine as initial
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@pytest.fixture
def dwelling_scope(monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(os.environ.get("FC14_DWELLING_RUNTIME", str(fall.RUNTIME_PATH)))
    )
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(
            row for row in record["tools"] if row["name"] == "traverse_srd_demiplane_door"
        )
        names = {"traverse_srd_dwelling_door", "operate_srd_mansion_door"}
        for name in sorted(names):
            declaration = deepcopy(template)
            declaration["name"] = name
            declaration["input_schema"] = _tool_schema(
                fall._runtime()[0]["tools"][name], tool_name=name
            )
            record["tools"].append(declaration)
        payloads = dict(primary.payloads)
        path = "content/content-declarations.json"
        declarations = json.loads(payloads[path])
        inclusion = next(
            row
            for row in declarations["inclusions"]
            if "traverse_srd_demiplane_door" in row.get("links", {}).get("tools", [])
        )
        inclusion["links"]["tools"] = sorted(set(inclusion["links"]["tools"]) | names)
        payloads[path] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payloads, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _install(state, **intent):
    return budget._run(
        "install_srd_magnificent_mansion",
        state,
        caster_state="hero",
        slot_level=7,
        **({"dwelling_id": "house", "point": [5, 10, 0], "designated": ["guard"]} | intent),
    )


def _source(state):
    return state[f"{SRD}.state"]["extradimensional_sources"][-1]


def _room(state):
    return next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["zones"]
        if row["id"] == _source(state)["room_id"]
    )


def _enter(state, actor="hero"):
    source = _source(state)
    room = _room(state)
    if _point(state, actor) != [5, 9, 0]:
        budget._run(
            "move_srd_scene_creature", state, actor_state=actor, destination=[5, 9, 0], mode="walk"
        )
    destination = [
        room["shape"]["min"][0] + 1.5,
        room["shape"]["min"][1] + (5 if actor == "hero" else 6.25),
        room["shape"]["min"][2],
    ]
    budget._run(
        "traverse_srd_dwelling_door",
        state,
        actor_state=actor,
        source_id=source["id"],
        door_subject=source["outer_id"],
        destination=destination,
    )
    return destination


@pytest.mark.parametrize(
    "cells",
    [
        [[0, 0, 0]],
        [[0, 0, 0], [1, 0, 0], [2, 0, 0]],
        [[x, y, 0] for x in range(10) for y in range(5)],
    ],
    ids=["one", "three", "fifty"],
)
def test_actual_rectangular_cube_plan_creates_source_room_faces_and_printed_door(
    dwelling_scope, monkeypatch, cells
):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    _install(state, floor_cells=cells)
    source, room = _source(state), _room(state)
    assert source["expires_tick"] == source["created_tick"] + 28800
    assert source["designated"] == ["guard"] and source["is_open"] is True
    extent = [room["shape"]["max"][axis] - room["shape"]["min"][axis] for axis in range(3)]
    assert extent[0] * extent[1] * extent[2] == len(cells) * 1000
    faces = [
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]
        if row["metadata"].get("dwelling_source_id") == source["id"]
    ]
    assert len(faces) == 6 and all(row["active"] for row in faces)
    assert sum(row["metadata"]["ground"] is True for row in faces) == 1
    for identity in (source["outer_id"], source["inner_id"]):
        portal = next(
            row
            for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == identity
        )
        assert portal["metadata"]["aperture_width"] == 5
        assert portal["height"] == 10 and portal["movement"] == "passable"
    assert actual.consumed == []


@pytest.mark.parametrize(
    "intent",
    [
        {"point": [5, 0, 0]},
        {"point": [350, 0, 0]},
        {"floor_cells": [[0, 0, 0], [2, 0, 0]]},
        {"floor_cells": [[0, 0]]},
        {"floor_cells": [[0, 0, 0], [0, 0, 0]]},
        {"designated": ["orb"]},
    ],
)
def test_invalid_door_plan_or_guest_rejects_without_a_source_or_scene_change(
    dwelling_scope, monkeypatch, intent
):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _install(state, **intent)
    assert state == before and actual.consumed == []


def test_caster_and_designated_guest_use_actual_door_and_no_action_close_blocks_entry(
    dwelling_scope, monkeypatch
):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    _install(state)
    source = _source(state)
    tick = state[f"{SRD}.state"]["lasting_tick"]
    caster_destination = _enter(state)
    assert _point(state, "hero") == caster_destination
    assert (
        next(
            row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
        )["metadata"]["plane"]
        == source["room_id"]
    )
    budget._run(
        "operate_srd_mansion_door",
        state,
        caster_state="hero",
        source_id=source["id"],
        is_open=False,
    )
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
    budget._run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[5, 9, 0], mode="walk"
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="closed or has ended"):
        _enter(state, "guard")
    assert state == before
    budget._run(
        "operate_srd_mansion_door", state, caster_state="hero", source_id=source["id"], is_open=True
    )
    destination = _enter(state, "guard")
    assert _point(state, "guard") == destination
    assert actual.consumed == []


def test_raw_retirement_queues_then_nearest_real_return_settles_once(dwelling_scope, monkeypatch):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    _install(state)
    _enter(state)
    source = deepcopy(_source(state))
    inside = _point(state, "hero")
    budget._run("end_srd_extradimensional_source", state, source_id=source["id"])
    assert _point(state, "hero") == inside
    assert _source(state)["return_pending"] is True and _source(state)["active"] is False
    budget._run("settle_srd_extradimensional_returns", state)
    after = deepcopy(state)
    hero = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero"
    )
    assert hero["metadata"]["plane"] == "material"
    assert hero["position"][0] == pytest.approx(source["origin"][0] - source["door_depth"] * 0.5)
    assert _source(state)["return_pending"] is False
    assert _room(state)["metadata"]["active"] is False
    assert all(
        not row["active"]
        for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]
        if row["metadata"].get("dwelling_source_id") == source["id"]
    )
    budget._run("settle_srd_extradimensional_returns", state)
    assert state == after and actual.consumed == []


def test_actual_clock_retires_at_twenty_four_hours_and_defers_existing_window(
    dwelling_scope,
    monkeypatch,
):
    state, _ = _tk_case()
    initial._dice(monkeypatch)
    _install(state)
    _enter(state)
    inside = _point(state, "hero")
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=14399)
    assert _source(state)["active"] is True
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _source(state)["active"] is False and _source(state)["return_pending"] is True
    assert _point(state, "hero") == inside
    budget._run("settle_srd_extradimensional_returns", state)
    assert _source(state)["return_pending"] is False
    assert _point(state, "hero")[2] == 0


def test_actual_room_body_boundary_and_nondesignated_guest_are_atomic(dwelling_scope, monkeypatch):
    state, _ = _tk_case()
    initial._dice(monkeypatch)
    _install(state, designated=[])
    _enter(state)
    source = _source(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="source's room bounds"):
        budget._run(
            "move_srd_travel_subject",
            state,
            subject="hero",
            destination=[8, 0, -5],
            plane=source["room_id"],
            teleport=False,
        )
    assert state == before
    budget._run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[5, 9, 0], mode="walk"
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="initial guests"):
        _enter(state, "guard")
    assert state == before


@pytest.mark.parametrize(
    "origin,suppressed",
    [
        ([0, -5, 0], True),
        ([5, 20, 0], True),
        ([15.5, 10, 0], True),
        ([16, 10, 0], False),
    ],
)
def test_actual_magic_zone_caster_or_door_overlap_preserves_source_and_control(
    dwelling_scope,
    monkeypatch,
    origin,
    suppressed,
):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    guard = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "guard"
    )
    guard["position"] = origin
    _install(state)
    budget._run(
        "cast_srd_spell_magic_zone",
        state,
        caster_state="guard",
        spell="antimagic_field",
        slot_level=8,
        concentration_save_modifier=0,
        save_dc=15,
    )
    before = deepcopy(state)
    source = deepcopy(_source(state))
    if suppressed:
        with pytest.raises(RulesetPreconditionRejected, match="suppresses this dwelling"):
            budget._run(
                "operate_srd_mansion_door",
                state,
                caster_state="hero",
                source_id=source["id"],
                is_open=False,
            )
        assert state == before
        budget._run("end_concentration", state, caster_state="guard")
    budget._run(
        "operate_srd_mansion_door",
        state,
        caster_state="hero",
        source_id=source["id"],
        is_open=False,
    )
    assert _source(state) == source | {"is_open": False}
    assert actual.consumed == []


def _mansion_case(_spell):
    state, data = _tk_case("magnificent_mansion")
    _, _, parameters = _case("magnificent_mansion")
    parameters.update(
        mansion_id="paid-house", mansion_designated=["guard"], travel_destination=[5, 10, 0]
    )
    return state, data, parameters


def test_canonical_access_cannot_desynchronize_source_door_and_ordinary_object_still_opens(
    dwelling_scope,
    monkeypatch,
):
    state, _ = _tk_case()
    initial._dice(monkeypatch)
    orb = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "orb"
    )
    orb["position"] = [8, 0, 0]
    _install(state)
    budget._run(
        "move_srd_scene_creature", state, actor_state="hero", destination=[5, 9, 0], mode="walk"
    )
    source = deepcopy(_source(state))
    before = deepcopy(state)
    for tool, parameters in (
        (
            "operate_srd_locked_object",
            {"actor_state": "hero", "object_id": source["outer_id"], "operation": "close"},
        ),
        (
            "record_srd_object_access",
            {
                "object_id": source["outer_id"],
                "object_kind": "door",
                "closed": True,
                "locks": [],
                "stuck": False,
                "barred": False,
            },
        ),
    ):
        with pytest.raises(RulesetPreconditionRejected, match="source-owned dwelling"):
            budget._run(tool, state, **parameters)
        assert state == before
    budget._run(
        "record_srd_object_access",
        state,
        object_id="orb",
        object_kind="container",
        closed=True,
        locks=[],
        stuck=False,
        barred=False,
    )
    budget._run(
        "operate_srd_locked_object", state, actor_state="guard", object_id="orb", operation="open"
    )
    orb = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "orb"
    )
    assert orb["metadata"]["srd_object_access"]["closed"] is False
    assert _source(state) == source


def test_shared_nearest_placement_is_bounded_by_actual_mansion_room(dwelling_scope, monkeypatch):
    state, _ = _tk_case()
    initial._dice(monkeypatch)
    _install(state)
    source, room = _source(state), _room(state)
    result = budget._run(
        "resolve_srd_travel_placement",
        state,
        subject="hero",
        destination=[8, 0, -5],
        plane=source["room_id"],
        excluded=[],
    )
    evidence = next(
        row for row in result.result["spatial_evidence"] if row["operation"] == "placement"
    )
    assert evidence["query"]["containing_zone_id"] == room["id"]
    placement = (
        SpatialEngine(state[f"{SRD}.state"]["spatial_scene"])
        .query({"kind": "placement", **dict(evidence["query"])})
        .as_payload()
    )
    assert placement["position"] == [room["shape"]["max"][0] - 0.4, 0, -5]
    assert placement["distance"] > 0


def _command(parameters, revision=0, tool="cast_spell_outside_combat", key="mansion"):
    return ActionCommand(
        campaign_id="casting",
        action_id=key,
        idempotency_key=key,
        owner_id="casting-owner",
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id="casting-turn",
        package_id=SRD,
        tool_name=tool,
        parameters=parameters,
    )


def test_genuine_minute_cast_source_entry_no_action_control_reopens_and_retries(
    dwelling_scope,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(public, "_spell_case", _mansion_case)
    actual = initial._dice(monkeypatch)
    path = tmp_path / "mansion.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "magnificent_mansion")
        mechanics = StatelessMcpMechanics(owner)
        for tool, facts, key in (
            ("begin_srd_spell_cast", _begin(parameters), "begin-minute"),
            (
                "continue_srd_spell_cast",
                {
                    "caster_state": "hero",
                    "elapsed_rounds": 10,
                    "uninterrupted": True,
                    "verbal_component_available": True,
                    "somatic_component_available": True,
                    "material_component_available": True,
                },
                "minute",
            ),
        ):
            receipt = mechanics.execute_action(
                _command(facts, owner.snapshot("casting").revision, tool, key)
            )
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        ready = owner.snapshot("casting")
        assert ready.state[f"{SRD}.state"]["lasting_tick"] == 20
        assert ready.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 3
        command = _command(parameters, ready.revision)
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        source = _source(saved.state)
        assert source["created_tick"] == 20 and source["expires_tick"] == 28820
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        room = _room(saved.state)
        destination = [room["shape"]["min"][0] + 1.5, 0, room["shape"]["min"][2]]
        for tool, facts, key in (
            (
                "move_srd_scene_creature",
                {"actor_state": "hero", "destination": [5, 9, 0], "mode": "walk"},
                "approach",
            ),
            (
                "traverse_srd_dwelling_door",
                {
                    "actor_state": "hero",
                    "source_id": source["id"],
                    "door_subject": source["outer_id"],
                    "destination": destination,
                },
                "enter",
            ),
            (
                "operate_srd_mansion_door",
                {"caster_state": "hero", "source_id": source["id"], "is_open": False},
                "close",
            ),
        ):
            command = _command(facts, owner.snapshot("casting").revision, tool, key)
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            after = owner.snapshot("casting")
            assert mechanics.execute_action(command) == receipt
            assert owner.snapshot("casting") == after
        assert _point(after.state, "hero") == destination
        assert _source(after.state)["is_open"] is False
        assert after.state[f"{SRD}.state"]["lasting_tick"] == 20
        assert actual.consumed == []
