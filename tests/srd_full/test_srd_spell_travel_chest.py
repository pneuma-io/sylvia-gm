"""Actual priced components, ground recall and cumulative daily risk retain the same chest."""

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
from tests.srd_full.test_srd_spell_travel import SRD, _case, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_dwelling import _command
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@pytest.fixture
def chest_scope(monkeypatch):
    monkeypatch.setattr(
        fall,
        "RUNTIME_PATH",
        Path(os.environ.get("FC14_CHEST_RUNTIME", ".scratch/chest-composition/runtime.yaml")),
    )
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(
            row for row in record["tools"] if row["name"] == "traverse_srd_demiplane_door"
        )
        names = {"record_srd_secret_chest_components", "use_srd_secret_chest"}
        for name in sorted(names):
            row = deepcopy(template)
            row["name"] = name
            row["input_schema"] = _tool_schema(fall._runtime()[0]["tools"][name], tool_name=name)
            record["tools"].append(row)
        payloads = dict(primary.payloads)
        path = "content/content-declarations.json"
        declarations = json.loads(payloads[path])
        row = next(
            row
            for row in declarations["inclusions"]
            if "traverse_srd_demiplane_door" in row.get("links", {}).get("tools", [])
        )
        row["links"]["tools"] = sorted(set(row["links"]["tools"]) | names)
        payloads[path] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payloads, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _body(state, identity):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == identity
    )


def _chest_case(_spell="secret_chest"):
    state, data = _tk_case("secret_chest")
    _, _, params = _case("secret_chest")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    model = deepcopy(_body(state, "orb"))
    for identity, position, half, height in (
        ("chest", [4, 1, 0], [1.5, 1], 2),
        ("replica", [0, 2, 0], [0.1, 0.1], 0.2),
        ("ethereal-place", [100, 0, 0], [0.1, 0.1], 0.1),
    ):
        obj = deepcopy(model)
        obj.update(
            id=identity,
            position=position,
            footprint={"kind": "box", "half_extents": half},
            height=height,
            movement="passable",
            visibility="clear",
        )
        obj["metadata"] = {
            "is_object": True,
            "plane": "ethereal" if identity == "ethereal-place" else "material",
            "lighting": "bright",
            "fiction": identity,
        }
        scene["entities"].append(obj)
    for index, identity in enumerate(("chest", "replica"), 1):
        budget._run(
            "record_srd_object_utility_facts",
            state,
            object_id=identity,
            weight_pounds=10,
            manufactured=True,
            metal=True,
            object_kind="container",
            inventory_item=f"secret_chest_material_{index}",
        )
        budget._run(
            "record_srd_object_material",
            state,
            object_id=identity,
            material="precious_metal",
            raw_stock=False,
        )
        budget._run(
            "prepare_srd_spell_object",
            state,
            object_id=identity,
            material="iron",
            size="small" if index == 1 else "tiny",
            durability="resilient",
        )
    budget._run(
        "record_srd_object_container",
        state,
        object_id="chest",
        capacity_gallons=80,
        water_gallons=10,
        is_open=False,
    )
    budget._run(
        "record_srd_secret_chest_components",
        state,
        caster_state="hero",
        chest_id="chest",
        replica_id="replica",
        chest_material_id="material_1",
        replica_material_id="material_2",
    )
    params.update(
        secret_chest_id="chest",
        secret_chest_replica="replica",
        secret_chest_ethereal_anchor="ethereal-place",
    )
    return state, data, params


def _install(state):
    budget._run(
        "install_srd_secret_chest",
        state,
        caster_state="hero",
        slot_level=4,
        chest_id="chest",
        replica_id="replica",
        ethereal_anchor="ethereal-place",
        material_item_ids=["material_1", "material_2"],
    )


def _source(state):
    return state[f"{SRD}.state"]["extradimensional_sources"][-1]


def test_same_actual_chest_and_canonical_water_recall_hide_source_identity(chest_scope):
    state, _, _ = _chest_case()
    quantities = deepcopy(_body(state, "chest")["metadata"]["srd_object_container"])
    _install(state)
    source = deepcopy(_source(state))
    assert _body(state, "chest")["position"] == [100, 0, 0]
    assert _body(state, "chest")["metadata"]["plane"] == "ethereal"
    budget._run(
        "use_srd_secret_chest",
        state,
        actor_state="hero",
        source_id=source["id"],
        operation="recall",
        destination=[0, 4, 0],
    )
    assert _body(state, "chest")["position"] == [0, 4, 0]
    assert _body(state, "chest")["metadata"]["plane"] == "material"
    assert _body(state, "chest")["metadata"]["srd_object_container"] == quantities
    budget._run(
        "use_srd_secret_chest", state, actor_state="hero", source_id=source["id"], operation="hide"
    )
    assert _body(state, "chest")["metadata"]["plane"] == "ethereal"
    assert _source(state) == source


@pytest.mark.parametrize("defect", ["dimensions", "price", "replica", "water", "nested"])
def test_invalid_actual_components_contents_reject_before_source_or_motion(chest_scope, defect):
    state, _, _ = _chest_case()
    if defect == "dimensions":
        _body(state, "chest")["height"] = 3
    elif defect == "price":
        state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["value_cp"] = 499999
    elif defect == "replica":
        _body(state, "replica")["metadata"]["srd_spell_object"]["destroyed"] = True
    elif defect == "water":
        _body(state, "chest")["metadata"]["srd_object_container"]["water_gallons"] = 90
    else:
        row = deepcopy(_body(state, "replica"))
        row.update(id="nested-item", position=[4, 1, 0.2])
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(row)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _install(state)
    assert state == before


@pytest.mark.parametrize("destination", [[0, 4, 1], [0, 6, 0], [5, 0, 0]])
def test_recall_actual_ground_range_and_occupancy_reject_atomically(chest_scope, destination):
    state, _, _ = _chest_case()
    _install(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run(
            "use_srd_secret_chest",
            state,
            actor_state="hero",
            source_id=_source(state)["id"],
            operation="recall",
            destination=destination,
        )
    assert state == before


def test_actual_daily_risk_clock_is_cumulative_once_and_source_end_never_returns_chest(
    chest_scope, monkeypatch
):
    state, _, _ = _chest_case()
    _install(state)
    actual = _TravelDice([(100, 6), (100, 10)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    # The source's real clock drives exact full days; no risk or success value enters a tool.
    for _ in range(60):
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert actual.consumed == [] and _source(state)["active"] is True
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert _source(state)["risk_rolls"] == [{"day": 61, "chance": 5, "roll": 6}]
    budget._run("refresh_srd_secret_chest_sources", state)
    assert actual.consumed == [(100, 6)]
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert _source(state)["active"] is False
    assert _body(state, "chest")["metadata"]["plane"] == "ethereal"
    assert actual.consumed == [(100, 6), (100, 10)]


def test_genuine_paid_chest_cast_recall_ground_hide_reopen_retry(
    chest_scope, tmp_path, monkeypatch
):
    monkeypatch.setattr(public, "_spell_case", _chest_case)
    actual = _TravelDice([])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    path = tmp_path / "chest.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "secret_chest")
        mechanics = StatelessMcpMechanics(owner)
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(
                _command(params | {"secret_chest_replica": "chest"}, key="invalid")
            )
        assert owner.snapshot("casting") == before
        command = _command(params, key="paid")
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert mechanics.execute_action(command) == receipt and owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        source = _source(saved.state)
        for operation in ("recall", "hide"):
            command = _command(
                {
                    "actor_state": "hero",
                    "source_id": source["id"],
                    "operation": operation,
                    "destination": [0, 4, 0],
                },
                owner.snapshot("casting").revision,
                "use_srd_secret_chest",
                operation,
            )
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            after = owner.snapshot("casting")
            assert (
                mechanics.execute_action(command) == receipt and owner.snapshot("casting") == after
            )
            assert _source(after.state) == source
            assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert _body(after.state, "chest")["metadata"]["plane"] == "ethereal"
        assert actual.consumed == []
