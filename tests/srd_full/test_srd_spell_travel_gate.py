"""Gate pays for actual paired circular openings and authentic concentration lifetime."""

import json
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
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _point
from tests.srd_full.test_srd_spell_travel import _case as travel_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

actual_fall_scope = fall.actual_fall_scope


@pytest.fixture
def gate_scope(actual_fall_scope, monkeypatch):
    monkeypatch.setattr(fall, "RUNTIME_PATH", Path(".scratch/gate-composition/runtime.yaml"))
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(
            row for row in record["tools"] if row["name"] == "traverse_srd_demiplane_door"
        )
        declaration = deepcopy(template)
        declaration["name"] = "inspect_srd_gate"
        declaration["input_schema"] = _tool_schema(
            fall._runtime()[0]["tools"]["inspect_srd_gate"], tool_name="inspect_srd_gate"
        )
        record["tools"].append(declaration)
        payload = dict(primary.payloads)
        data = json.loads(payload["content/content-declarations.json"])
        inclusion = next(
            row
            for row in data["inclusions"]
            if "traverse_srd_demiplane_door" in row.get("links", {}).get("tools", [])
        )
        inclusion["links"]["tools"] = sorted(
            set(inclusion["links"]["tools"]) | {"inspect_srd_gate"}
        )
        payload["content/content-declarations.json"] = canonical_json_bytes(data)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)


def _case(spell="gate", **changes):
    state, data, params = travel_case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(
        _entity(
            "other-plane-anchor",
            [100, 0, 10],
            plane="ethereal",
            planar_anchor=True,
            is_object=False,
            participant_ref=None,
        )
        | {"movement": "passable", "visibility": "clear", "height": 0.001}
    )
    params.update(
        gate_id="opening",
        gate_destination_subject="other-plane-anchor",
        travel_destination=[30, 0, 10],
        gate_diameter=5,
        gate_front="up",
        gate_depiction_depth=0.25,
    )
    params.update(changes)
    return state, data, params


def _install(state, **changes):
    return budget._run(
        "install_srd_gate",
        state,
        caster_state="hero",
        slot_level=9,
        **(
            {
                "source_id": "opening",
                "destination_subject": "other-plane-anchor",
                "point": [30, 0, 10],
                "diameter": 5,
                "front": "up",
                "depiction_depth": 0.25,
            }
            | changes
        ),
    )


def _source(state):
    return state[f"{SRD}.state"]["gate_sources"][-1]


def _body(state, identity):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == identity
    )


@pytest.mark.parametrize(("diameter", "front"), [(5, "up"), (20, "down")])
def test_actual_circular_openings_keep_pair_plane_diameter_and_no_traveler_move(
    gate_scope, diameter, front
):
    state, _, _ = _case()
    origins = {
        body["id"]: deepcopy(body["position"])
        for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]
    }
    _install(state, diameter=diameter, front=front)
    source = _source(state)
    assert source["active"] and source["expires_tick"] - source["created_tick"] == 20
    assert source["cast_id"] == "" and source["source_caster_id"] is None
    assert source["prevention"] is None
    for body_id, point, plane, peer in [
        ("opening:near", [30, 0, 10], "material", "opening:far"),
        ("opening:far", [100, 0, 10], "ethereal", "opening:near"),
    ]:
        body = _body(state, body_id)
        assert body["position"] == point and body["footprint"] == {
            "kind": "circle",
            "radius": diameter / 2,
        }
        assert body["height"] == 0.25 and body["metadata"]["plane"] == plane
        assert body["metadata"]["gate_portal"]["peer_id"] == peer
        assert body["metadata"]["gate_portal"]["front"] == front
        assert (
            body["metadata"]["is_object"] is False and body["metadata"]["participant_ref"] is None
        )
    assert all(_point(state, identity) == point for identity, point in origins.items())
    budget._run("inspect_srd_gate", state, caster_state="hero", source_id="opening")
    assert not any("traverse_srd_gate" in name for name in fall._runtime()[0]["tools"])


def test_circle_occupancy_uses_actual_round_edge_and_plane_default(gate_scope):
    state, _, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "outside-circle",
            "map_id": "information",
            "volume": {"kind": "box", "min": [39, 8, 10], "max": [40, 9, 11]},
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    _install(state, diameter=20)
    assert _source(state)["active"]
    state, _, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "inside-circle",
            "map_id": "information",
            "volume": {"kind": "box", "min": [39, 0, 10], "max": [40, 1, 11]},
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="occupied"):
        _install(state, diameter=20)
    assert state == before
    scene["geometry"][0]["metadata"]["plane"] = "ethereal"
    _install(state, diameter=20)
    assert _source(state)["active"]


@pytest.mark.parametrize(
    "defect", ["same_plane", "physical_anchor", "range", "occupied", "bounds", "blind"]
)
def test_invalid_actual_gate_intent_rejects_before_slot_material_or_scene_mutation(
    gate_scope, defect
):
    state, data, params = _case()
    anchor = _body(state, "other-plane-anchor")
    if defect == "same_plane":
        anchor["metadata"]["plane"] = "material"
    if defect == "physical_anchor":
        anchor["metadata"]["is_object"] = True
    if defect == "range":
        params["travel_destination"] = [61, 0, 10]
    if defect == "occupied":
        params["travel_destination"] = [5, 0, 0]
    if defect == "bounds":
        anchor["position"] = [29999, 0, 10]
    if defect == "blind":
        state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_actual_concentration_clock_and_voluntary_end_retire_exact_paired_bodies_once(gate_scope):
    state, _, _ = _case()
    _install(state)
    positions = {
        identity: deepcopy(_body(state, identity)) for identity in ["opening:near", "opening:far"]
    }
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert _source(state)["active"] and _body(state, "opening:near")["active"]
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert not _source(state)["active"]
    for identity in positions:
        body = _body(state, identity)
        assert not body["active"] and body["position"] == positions[identity]["position"]
        assert (
            body["footprint"] == positions[identity]["footprint"]
            and body["metadata"]["is_object"] is False
        )
    before = deepcopy(state)
    budget._run("end_srd_gate_source", state, source_id="opening")
    assert state == before
    state, _, _ = _case()
    _install(state)
    budget._run("end_concentration", state, caster_state="hero")
    assert not _source(state)["active"] and not _body(state, "opening:far")["active"]


def test_actual_ruler_prevention_is_paid_without_fake_portal_or_slot_retry(gate_scope):
    state, data, params = _case()
    fact = {
        "ruler_name": "The Keeper",
        "domain": "the admitted far anchor domain",
        "prevents": True,
    }
    _body(state, "other-plane-anchor")["metadata"]["gate_prevention"] = fact
    caster = state[f"{SRD}.entities"]["hero"]
    before = deepcopy(caster)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert (
        _source(state)["prevented"]
        and not _source(state)["active"]
        and _source(state)["prevention"] == fact
    )
    assert not any(
        body["id"].startswith("opening:")
        for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]
    )
    caster = state[f"{SRD}.entities"]["hero"]
    assert caster["resources"]["spell_slot_9"] == before["resources"]["spell_slot_9"] - 1
    assert (
        caster["spell_materials"] == before["spell_materials"]
        and caster["inventory"] == before["inventory"]
    )


def test_genuine_paid_gate_saved_reopen_retry_and_expiry_retains_portals_and_diamond(
    gate_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _case)
    path = tmp_path / "gate.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "gate")
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot("casting")
        hero = before.state[f"{SRD}.entities"]["hero"]
        command = dwelling._command(
            params, before.revision, "cast_spell_outside_combat", "paid-gate"
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        after = saved.state[f"{SRD}.entities"]["hero"]
        assert after["resources"]["spell_slot_9"] == hero["resources"]["spell_slot_9"] - 1
        assert (
            after["inventory"] == hero["inventory"]
            and after["spell_materials"] == hero["spell_materials"]
        )
        assert (
            saved.state[f"{SRD}.state"]["lasting_tick"]
            == before.state[f"{SRD}.state"]["lasting_tick"]
        )
        assert mechanics.execute_action(command) == receipt and owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        inspection = mechanics.execute_action(
            dwelling._command(
                {"caster_state": "hero", "source_id": "opening"},
                saved.revision,
                "inspect_srd_gate",
                "inspect",
            )
        )
        assert inspection.status is ActionStatus.COMMITTED
        current = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(
                dwelling._command(
                    params, current.revision, "cast_spell_outside_combat", "repeat-identity"
                )
            )
        assert owner.snapshot("casting") == current
        command = dwelling._command(
            {"elapsed_rounds": 10}, current.revision, "advance_srd_lasting_time", "expire"
        )
        expired = mechanics.execute_action(command)
        assert expired.status is ActionStatus.COMMITTED, expired.result
        settled = owner.snapshot("casting")
        assert (
            not _source(settled.state)["active"]
            and not _body(settled.state, "opening:near")["active"]
        )
        assert mechanics.execute_action(command) == expired and owner.snapshot("casting") == settled
        assert settled.state[f"{SRD}.entities"]["hero"]["resources"] == after["resources"]
