"""Rope length and ceiling qualifications consume the actual admitted objects and planes."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_initial_fine as initial
from tests.srd_full.test_srd_spell_travel import SRD, _case
from tests.srd_full.test_srd_spell_travel_dwelling import _command
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@pytest.fixture
def rope_scope(monkeypatch):
    monkeypatch.setattr(
        fall,
        "RUNTIME_PATH",
        Path(os.environ.get("FC14_ROPE_RUNTIME", ".scratch/rope-composition/runtime.yaml")),
    )
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(
            row for row in record["tools"] if row["name"] == "traverse_srd_demiplane_door"
        )
        names = {"record_srd_rope_length", "traverse_srd_rope_trick", "climb_srd_rope_trick"}
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


def _rope_case(length=10, *, ceilings=(), spell="misty_step"):
    state, parameters = _tk_case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    rope = deepcopy(next(row for row in scene["entities"] if row["id"] == "orb"))
    rope.update(
        id="rope",
        position=[1, 0, 0],
        height=0.1,
        footprint={"kind": "box", "half_extents": [0.1, 0.1]},
        movement="passable",
    )
    rope["metadata"] = {"is_object": True, "plane": "material", "fiction": "An actual rope"}
    scene["entities"].append(rope)
    for name, height, plane in ceilings:
        scene["geometry"].append(
            {
                "id": name,
                "map_id": scene["map"]["id"],
                "volume": {"kind": "box", "min": [-2, -2, height], "max": [4, 3, height + 0.5]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"plane": plane},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
    budget._run("record_srd_rope_length", state, object_id="rope", length_feet=length)
    return state, parameters


def _install(state, **intent):
    return budget._run(
        "install_srd_rope_trick",
        state,
        caster_state="hero",
        slot_level=2,
        rope_id="rope",
        dwelling_id="rope-space",
        **intent,
    )


def _source(state):
    return next(
        row
        for row in state[f"{SRD}.state"]["extradimensional_sources"]
        if row["id"] == "rope-space"
    )


@pytest.mark.parametrize(
    ("length", "ceilings", "height", "ceiling_id"),
    [
        (10, (), 10, ""),
        (10, (("real-ceiling", 6, "material"),), 6, "real-ceiling"),
        (10, (("other-plane", 3, "ethereal"), ("real-ceiling", 6, "material")), 6, "real-ceiling"),
        (10, (("high-ceiling", 14, "material"),), 10, ""),
        (75, (("real-ceiling", 6, "material"),), 6, "real-ceiling"),
    ],
)
def test_rope_uses_full_actual_length_or_first_same_plane_ceiling(
    rope_scope,
    length,
    ceilings,
    height,
    ceiling_id,
):
    state, _ = _rope_case(length, ceilings=ceilings)
    _install(state)
    source = _source(state)
    assert source["spell"] == "rope_trick"
    assert source["hung_height"] == height
    assert source["ceiling_id"] == ceiling_id
    assert source["origin"] == [1, 0, height]
    assert source["expires_tick"] - source["created_tick"] == 1200
    scene = state[f"{SRD}.state"]["spatial_scene"]
    rope = next(row for row in scene["entities"] if row["id"] == "rope")
    assert rope["position"] == [1, 0, 0]
    assert rope["height"] == height
    assert rope["metadata"]["rope_trick_source_id"] == source["id"]
    portal = next(row for row in scene["entities"] if row["id"] == source["outer_id"])
    assert portal["footprint"]["half_extents"] == [1.5, 2.5]
    assert portal["metadata"]["invisible"] is True
    assert portal["metadata"]["aperture_face"] == "up"


@pytest.mark.parametrize("invalid", ["far", "other-plane", "held", "repeat"])
def test_rope_invalid_actual_facts_or_reused_identity_reject_atomically(rope_scope, invalid):
    state, _ = _rope_case()
    rope = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "rope"
    )
    if invalid == "far":
        rope["position"] = [10, 0, 0]
    elif invalid == "other-plane":
        rope["metadata"]["plane"] = "ethereal"
    elif invalid == "held":
        rope["metadata"]["carried_by"] = "guard"
    else:
        _install(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _install(state)
    assert state == before


def test_rope_physical_length_is_admitted_once(rope_scope):
    state, _ = _rope_case(12)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already admitted"):
        budget._run("record_srd_rope_length", state, object_id="rope", length_feet=50)
    assert state == before


def _body(state, identity):
    return next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["metadata"].get("participant_ref") == identity
    )


def _enter(state, actor="hero"):
    source, body = _source(state), _body(state, actor)
    budget._run(
        "move_srd_scene_creature", state, actor_state=actor, mode="walk", destination=[1, 0, 0]
    )
    budget._run(
        "climb_srd_rope_trick",
        state,
        actor_state=actor,
        source_id=source["id"],
        destination_height=source["origin"][2] - body["height"],
    )
    portal = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == source["inner_id"]
    )
    budget._run(
        "traverse_srd_rope_trick",
        state,
        actor_state=actor,
        source_id=source["id"],
        door_subject=source["outer_id"],
        destination=portal["position"],
    )


def test_actual_climb_then_horizontal_entry_and_exit_preserves_source(rope_scope):
    state, _ = _rope_case(10)
    _install(state)
    before = deepcopy(state)
    source = deepcopy(_source(state))
    inner = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == source["inner_id"]
    )
    with pytest.raises(RulesetPreconditionRejected, match="Climb"):
        budget._run(
            "traverse_srd_rope_trick",
            state,
            actor_state="hero",
            source_id=source["id"],
            door_subject=source["outer_id"],
            destination=inner["position"],
        )
    assert state == before
    _enter(state)
    assert _body(state, "hero")["metadata"]["plane"] == source["room_id"]
    with pytest.raises(RulesetPreconditionRejected, match="Mansion-specific"):
        budget._run(
            "traverse_srd_dwelling_door",
            state,
            actor_state="hero",
            source_id=source["id"],
            door_subject=source["inner_id"],
            destination=[1, 0, 8.2],
        )
    height = _body(state, "hero")["height"]
    budget._run(
        "traverse_srd_rope_trick",
        state,
        actor_state="hero",
        source_id=source["id"],
        door_subject=source["inner_id"],
        destination=[1, 0, source["origin"][2] - height],
    )
    assert _body(state, "hero")["metadata"]["plane"] == source["origin_plane"]
    assert _source(state) == source


@pytest.mark.parametrize("native", [False, True])
def test_actual_rope_climb_uses_shared_speed_and_printed_extra_cost(rope_scope, native):
    state, _ = _rope_case()
    if native:
        state[f"{SRD}.entities"]["hero"]["climb_speed"] = 30
    _install(state)
    budget._run(
        "move_srd_scene_creature", state, actor_state="hero", mode="walk", destination=[1, 0, 0]
    )
    spent = state[f"{SRD}.entities"]["hero"]["scene_movement_spent"]
    height = _source(state)["origin"][2] - _body(state, "hero")["height"]
    budget._run(
        "climb_srd_rope_trick",
        state,
        actor_state="hero",
        source_id="rope-space",
        destination_height=height,
    )
    assert _body(state, "hero")["position"] == [1, 0, height]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] - spent == (
        height if native else height * 2
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="vertical"):
        budget._run(
            "climb_srd_rope_trick",
            state,
            actor_state="hero",
            source_id="rope-space",
            destination_height=10,
        )
    assert state == before


def _paid_rope_case(_spell):
    state, data = _rope_case(spell="rope_trick")
    _, _, parameters = _case("rope_trick")
    parameters.update(rope_trick_rope="rope", rope_trick_id="rope-space")
    return state, data, parameters


def test_genuine_paid_rope_cast_climb_entry_reopen_retry_and_exact_hour_queue(
    rope_scope,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(public, "_spell_case", _paid_rope_case)
    actual = initial._dice(monkeypatch)
    path = tmp_path / "rope.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "rope_trick")
        mechanics = StatelessMcpMechanics(owner)
        invalid = _command(parameters | {"rope_trick_rope": "absent"}, key="invalid-rope")
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(invalid)
        assert owner.snapshot("casting").state == before.state
        command = _command(parameters, key="paid-rope")
        try:
            receipt = mechanics.execute_action(command)
        except MechanicsRejected as error:
            pytest.fail(
                f"{error.code}: {error}; fields={error.field_paths}; "
                f"issues={error.validation_issues}"
            )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        source = deepcopy(_source(saved.state))
        assert source["created_tick"] == 0 and source["expires_tick"] == 1200
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        inner = next(
            row
            for row in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == source["inner_id"]
        )
        for tool, facts, key in (
            (
                "move_srd_scene_creature",
                {"actor_state": "hero", "destination": [1, 0, 0], "mode": "walk"},
                "approach",
            ),
            (
                "climb_srd_rope_trick",
                {
                    "actor_state": "hero",
                    "source_id": source["id"],
                    "destination_height": 10 - _body(saved.state, "hero")["height"],
                },
                "climb",
            ),
            (
                "traverse_srd_rope_trick",
                {
                    "actor_state": "hero",
                    "source_id": source["id"],
                    "door_subject": source["outer_id"],
                    "destination": list(inner["position"]),
                },
                "enter",
            ),
        ):
            command = _command(facts, owner.snapshot("casting").revision, tool, key)
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            after = owner.snapshot("casting")
            assert mechanics.execute_action(command) == receipt
            assert owner.snapshot("casting") == after
        assert _body(after.state, "hero")["metadata"]["plane"] == source["room_id"]
        assert _source(after.state) == source
        for rounds, active in ((599, True), (1, False)):
            command = _command(
                {"elapsed_rounds": rounds},
                owner.snapshot("casting").revision,
                "advance_srd_lasting_time",
                f"clock-{rounds}",
            )
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            after = owner.snapshot("casting")
            assert _source(after.state)["active"] is active
            assert mechanics.execute_action(command) == receipt
            assert owner.snapshot("casting") == after
        assert _source(after.state)["return_pending"] is True
        assert _body(after.state, "hero")["metadata"]["plane"] == source["room_id"]
        assert actual.consumed == []


@pytest.mark.parametrize("barrier", ["large", "eight"])
def test_actual_rope_creature_size_and_eight_occupants_cannot_bypass_entry(rope_scope, barrier):
    state, _ = _rope_case()
    if barrier == "large":
        state[f"{SRD}.entities"]["hero"]["size_rank"] = 3
    else:
        scene = state[f"{SRD}.state"]["spatial_scene"]
        for index in range(8):
            identity = f"guest-{index}"
            state[f"{SRD}.entities"][identity] = deepcopy(state[f"{SRD}.entities"]["guard"])
            body = deepcopy(_body(state, "guard"))
            body["id"] = identity
            body["position"] = [40 + index, 30, 0]
            body["metadata"]["participant_ref"] = identity
            scene["entities"].append(body)
    _install(state)
    source = _source(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    inner = next(row for row in scene["entities"] if row["id"] == source["inner_id"])
    if barrier == "eight":
        room = next(row for row in scene["zones"] if row["id"] == source["room_id"])
        for index in range(8):
            budget._run(
                "move_srd_travel_subject",
                state,
                subject=f"guest-{index}",
                destination=[room["shape"]["min"][0] + 1 + index, 3, room["shape"]["min"][2]],
                plane=source["room_id"],
                teleport=False,
            )
    budget._run(
        "move_srd_scene_creature", state, actor_state="hero", mode="walk", destination=[1, 0, 0]
    )
    budget._run(
        "climb_srd_rope_trick",
        state,
        actor_state="hero",
        source_id=source["id"],
        destination_height=source["origin"][2] - _body(state, "hero")["height"],
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Medium|eight"):
        budget._run(
            "traverse_srd_rope_trick",
            state,
            actor_state="hero",
            source_id=source["id"],
            door_subject=source["outer_id"],
            destination=inner["position"],
        )
    assert state == before
