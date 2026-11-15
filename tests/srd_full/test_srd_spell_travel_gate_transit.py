"""Gate fronts use actual body clearance, paid movement and two-plane point sight."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_travel_gate_transit import (
    _approach_node,
    _view_node,
    emit_late_gate_geometry,
)

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_gate as gate
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _point
from tests.srd_full.test_srd_spell_wave3_information import _wall

gate_scope = gate.gate_scope
actual_fall_scope = gate.actual_fall_scope


@pytest.fixture
def transit_scope(gate_scope, monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/gate-transit-composition/runtime.yaml")
    )
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(row for row in record["tools"] if row["name"] == "inspect_srd_gate")
        for name in ("traverse_srd_gate", "look_through_srd_gate"):
            declaration = deepcopy(template)
            declaration["name"] = name
            declaration["input_schema"] = _tool_schema(
                fall._runtime()[0]["tools"][name], tool_name=name
            )
            record["tools"].append(declaration)
        payload = dict(primary.payloads)
        data = json.loads(payload["content/content-declarations.json"])
        inclusion = next(
            row
            for row in data["inclusions"]
            if "inspect_srd_gate" in row.get("links", {}).get("tools", [])
        )
        inclusion["links"]["tools"] = sorted(
            set(inclusion["links"]["tools"]) | {"traverse_srd_gate", "look_through_srd_gate"}
        )
        payload["content/content-declarations.json"] = canonical_json_bytes(data)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)


def _case(spell="gate", **changes):
    state, data, params = gate._case(spell, **changes)
    gate._body(state, "hero")["position"] = [30, 0, 20]
    return state, data, params


def _installed(front="up"):
    state, _, _ = _case()
    if front == "down":
        gate._body(state, "hero")["position"] = [30, 0, 0]
    gate._install(state, front=front)
    return state


def _transit(state, **changes):
    return budget._run(
        "traverse_srd_gate",
        state,
        **(
            {
                "actor_state": "hero",
                "source_id": "opening",
                "portal_id": "opening:near",
                "entry_point": [30, 0, 10.25],
                "arrival_point": [101, 0, 10.25],
            }
            | changes
        ),
    )


def _view(state, **changes):
    return budget._run(
        "look_through_srd_gate",
        state,
        **(
            {
                "observer_state": "hero",
                "source_id": "opening",
                "portal_id": "opening:near",
                "point": [100, 0, 20],
            }
            | changes
        ),
    ).result["context"]


def _solid(state, *, plane="material", x=30, movement="blocking", low=14, high=15):
    solid = _wall(1)
    solid.update(
        id="portal-ray-blocker",
        volume={"kind": "box", "min": [x - 1, -1, low], "max": [x + 1, 1, high]},
        movement=movement,
        metadata={"plane": plane},
    )
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(solid)


@pytest.mark.parametrize("front", ["up", "down"])
def test_real_full_body_front_approach_spends_actual_movement_and_relocates_once(
    transit_scope, front
):
    state = _installed(front)
    source = deepcopy(gate._source(state))
    portals = {
        identity: deepcopy(gate._body(state, identity))
        for identity in ("opening:near", "opening:far")
    }
    entry, arrival, cost = (
        ([30, 0, 10.25], [101, 0, 10.25], 9.75) if front == "up" else ([30, 0, 4], [101, 0, 10], 4)
    )
    result = _transit(state, entry_point=entry, arrival_point=arrival)
    assert _point(state) == arrival and gate._body(state, "hero")["metadata"]["plane"] == "ethereal"
    assert result.result["context"]["_route_cost"] == pytest.approx(cost)
    assert gate._source(state) == source
    assert all(gate._body(state, identity) == body for identity, body in portals.items())
    assert state[f"{SRD}.state"]["gate_transit_receipt"] is None
    assert (
        budget._run("inspect_srd_gate", state, caster_state="hero", source_id="opening").result[
            "context"
        ]["_gate_transit_supported"]
        is True
    )


@pytest.mark.parametrize(
    "defect",
    ["entry_corner", "exit_corner", "back", "nonzero", "occupied", "wall", "forged_pair", "curve"],
)
def test_invalid_actual_circular_transit_is_atomic_before_movement(transit_scope, defect):
    state = _installed()
    choices = {}
    if defect == "entry_corner":
        choices["entry_point"] = [32.2, 0, 10.25]
    elif defect == "exit_corner":
        choices["arrival_point"] = [102.2, 0, 10.25]
    elif defect == "back":
        gate._body(state, "hero")["position"] = [30, 0, 0]
    elif defect == "nonzero":
        choices["arrival_point"] = [101, 0, 11]
    elif defect == "occupied":
        gate._body(state, "guard").update(position=[101, 0, 10.25])
        gate._body(state, "guard")["metadata"]["plane"] = "ethereal"
    elif defect == "wall":
        _solid(state)
    elif defect == "forged_pair":
        gate._body(state, "opening:far")["metadata"]["gate_portal"]["peer_id"] = "orb"
    elif defect == "curve":
        gate._body(state, "hero")["footprint"] = {"kind": "circle", "radius": 0.4}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _transit(state, **choices)
    assert state == before


def test_near_approach_ignores_other_plane_solid_but_preserves_real_movement_budget(transit_scope):
    state = _installed()
    _solid(state, plane="ethereal")
    _transit(state)
    assert _point(state) == [101, 0, 10.25]
    state = _installed()
    budget._run(
        "move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 45], mode="walk"
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="movement|Movement|speed|Speed"):
        _transit(state)
    assert state == before


@pytest.mark.parametrize(("plane", "x"), [("material", 30), ("ethereal", 100)])
def test_real_two_segment_sight_includes_each_actual_plane_blocker(transit_scope, plane, x):
    state = _installed()
    seen = _view(state)
    assert seen["_gate_view_can_see"] is True and seen["_gate_view_plane"] == "ethereal"
    assert seen["_gate_view_distance"] == pytest.approx(19.5)
    _solid(state, plane=plane, x=x)
    assert _view(state)["_gate_view_can_see"] is False
    state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["metadata"]["plane"] = "prison"
    assert _view(state)["_gate_view_can_see"] is True


def test_actual_two_plane_ambient_obscuration_senses_and_front_view_limits(transit_scope):
    state = _installed()
    state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    assert _view(state)["_gate_view_can_see"] is False
    state = _installed()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "near-obscured",
            "map_id": "information",
            "shape": {"kind": "box", "min": [29, -1, 14], "max": [31, 1, 15]},
            "metadata": {"plane": "material", "obstruction": "heavy_obscurement"},
        }
    )
    assert _view(state)["_gate_view_can_see"] is False
    scene["zones"][-1]["metadata"]["plane"] = "prison"
    assert _view(state)["_gate_view_can_see"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="perpendicular"):
        _view(state, point=[101, 0, 20])
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="front"):
        _view(state, point=[100, 0, 0])
    assert state == before
    scene["zones"] = [
        zone for zone in scene["zones"] if zone["metadata"].get("plane") != "ethereal"
    ]
    with pytest.raises(RulesetPreconditionRejected, match="ambient"):
        _view(state)


def test_late_capture_keeps_the_actual_movement_prefix_and_rejects_missing_boundary(transit_scope):
    runtime, encoded = fall._runtime()
    tools = runtime["tools"]
    owner = tools["move_srd_scene_creature_environment_segment"]
    boundary = next(i for i, row in enumerate(owner["effects"]) if "spatial_mutation" in row)
    approach = _approach_node(tools, capture=True)
    assert (
        approach["parameters"] == owner["parameters"]
        and approach["pre_conditions"] == owner["pre_conditions"]
    )
    assert approach["effects"] == owner["effects"][:boundary]
    assert emit_late_gate_geometry(encoded.decode()).encode() == encoded
    invalid = deepcopy(tools)
    invalid["move_srd_scene_creature_environment_segment"]["effects"].pop(boundary)
    with pytest.raises(ValueError, match="one canonical"):
        _approach_node(invalid, capture=True)
    assert _approach_node(tools)["effects"][0]["require"]["condition"] == "false"
    assert _view_node(tools)["effects"][0]["require"]["condition"] == "false"


def test_actual_darkvision_uses_the_sum_of_both_plane_segments(transit_scope):
    state = _installed()
    budget._run(
        "cast_spell_timed_senses",
        state,
        spell="darkvision",
        caster_state="hero",
        target_state="hero",
        target_willing=True,
        all_targets_willing=True,
        caster_can_touch_target=True,
        caster_can_touch_all_targets=True,
        target_distance=0,
        targets_max_distance=0,
    )
    gate._body(state, "hero")["position"] = [30, 0, 90]
    for zone in state[f"{SRD}.state"]["spatial_scene"]["zones"]:
        if zone["metadata"].get("plane") == "ethereal":
            zone["metadata"]["lighting"] = "darkness"
    near = _view(state, point=[100, 0, 90])
    assert near["_gate_view_distance"] == 159.5
    assert near["_gate_view_can_see"] is False
    within = _view(state, point=[100, 0, 80])
    assert within["_gate_view_distance"] == 149.5
    assert within["_gate_view_can_see"] is True


def test_near_magical_darkness_preserves_actual_truesight(transit_scope):
    state = _installed()
    for zone in state[f"{SRD}.state"]["spatial_scene"]["zones"]:
        if zone["metadata"].get("plane") == "material":
            zone["metadata"]["lighting"] = "magical_darkness"
    assert _view(state)["_gate_view_can_see"] is False
    budget._run(
        "cast_spell_timed_senses",
        state,
        spell="true_seeing",
        caster_state="hero",
        target_state="hero",
        target_willing=True,
        all_targets_willing=True,
        caster_can_touch_target=True,
        caster_can_touch_all_targets=True,
        target_distance=0,
        targets_max_distance=0,
    )
    assert _view(state)["_gate_view_can_see"] is True


@pytest.mark.parametrize(
    ("plane", "x", "expected"),
    [("material", 35, "suppression"), ("ethereal", 105, "admitted box subject")],
)
def test_actual_antimagic_on_either_frame_rejects_then_source_end_restores(
    transit_scope, plane, x, expected
):
    state = _installed()
    gate._body(state, "guard").update(position=[x, 0, 10])
    gate._body(state, "guard")["metadata"]["plane"] = plane
    budget._run(
        "cast_srd_spell_magic_zone",
        state,
        caster_state="guard",
        spell="antimagic_field",
        slot_level=8,
        concentration_save_modifier=0,
        save_dc=10,
    )
    source = deepcopy(gate._source(state))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=expected):
        _transit(state)
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match=expected):
        _view(state)
    assert state == before
    budget._run("end_concentration", state, caster_state="guard")
    assert gate._source(state) == source
    assert _view(state)["_gate_view_can_see"] is True
    _transit(state)
    assert _point(state) == [101, 0, 10.25]


def test_genuine_paid_gate_look_transit_reopen_retry_and_expiry(
    transit_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _case)
    path = tmp_path / "gate-transit.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "gate")
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot("casting")
        command = dwelling._command(
            params, before.revision, "cast_spell_outside_combat", "paid-gate"
        )
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(command) == paid and owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        look = dwelling._command(
            {
                "observer_state": "hero",
                "source_id": "opening",
                "portal_id": "opening:near",
                "point": [100, 0, 20],
            },
            saved.revision,
            "look_through_srd_gate",
            "actual-vista",
        )
        assert mechanics.execute_action(look).status is ActionStatus.COMMITTED
        current = owner.snapshot("casting")
        invalid = dwelling._command(
            {
                "actor_state": "hero",
                "source_id": "opening",
                "portal_id": "opening:near",
                "entry_point": [32.2, 0, 10.25],
                "arrival_point": [101, 0, 10.25],
            },
            current.revision,
            "traverse_srd_gate",
            "invalid-fit",
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(invalid)
        assert owner.snapshot("casting") == current
        command = dwelling._command(
            {
                "actor_state": "hero",
                "source_id": "opening",
                "portal_id": "opening:near",
                "entry_point": [30, 0, 10.25],
                "arrival_point": [101, 0, 10.25],
            },
            current.revision,
            "traverse_srd_gate",
            "actual-crossing",
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        crossed = owner.snapshot("casting")
        assert (
            _point(crossed.state) == [101, 0, 10.25]
            and gate._body(crossed.state, "hero")["metadata"]["plane"] == "ethereal"
        )
        assert mechanics.execute_action(command) == receipt and owner.snapshot("casting") == crossed
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == crossed
        mechanics = StatelessMcpMechanics(owner)
        command = dwelling._command(
            {"elapsed_rounds": 10}, crossed.revision, "advance_srd_lasting_time", "expire-gate"
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        ended = owner.snapshot("casting")
        assert not gate._source(ended.state)["active"] and _point(ended.state) == [101, 0, 10.25]
        assert mechanics.execute_action(command) == receipt and owner.snapshot("casting") == ended
        hero = ended.state[f"{SRD}.entities"]["hero"]
        initial = before.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_9"] == initial["resources"]["spell_slot_9"] - 1
        assert (
            hero["inventory"] == initial["inventory"]
            and hero["spell_materials"] == initial["spell_materials"]
        )
        assert (
            gate._source(ended.state)["created_tick"] == gate._source(saved.state)["created_tick"]
        )
