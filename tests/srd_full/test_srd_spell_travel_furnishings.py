"""Created Mansion furnishings use actual dimensions, identity and departure smoke."""

from copy import deepcopy

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_initial_fine as initial
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@pytest.fixture
def furnishing_scope(monkeypatch):
    monkeypatch.setenv(
        "FC14_DWELLING_RUNTIME", ".scratch/mansion-furnishings-composition/runtime.yaml"
    )
    yield from dwelling.dwelling_scope.__wrapped__(monkeypatch)


FURNITURE = [
    {
        "id": "table",
        "offset": [5, 5, 0],
        "extent": [2, 2, 3],
        "material": "wood",
        "appearance": "a plain oak table",
    },
    {
        "id": "statue",
        "offset": [8, 8, 0],
        "extent": [1, 1, 5],
        "material": "stone",
        "appearance": "a carved owl",
    },
]


def _body(state, identity):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == identity
    )


def test_actual_furnishings_preserve_paid_source_geometry_and_omitted_intent(furnishing_scope):
    state, _ = _tk_case()
    dwelling._install(state, furnishings=deepcopy(FURNITURE))
    source, room = dwelling._source(state), dwelling._room(state)
    assert source["furnishings"] == FURNITURE
    for intent in FURNITURE:
        body = _body(state, intent["id"])
        assert body["position"] == [
            room["shape"]["min"][axis] + intent["offset"][axis] for axis in range(3)
        ]
        assert body["footprint"]["half_extents"] == [value * 0.5 for value in intent["extent"][:2]]
        assert body["height"] == intent["extent"][2]
        assert body["metadata"]["plane"] == source["room_id"]
        assert body["metadata"]["is_object"] is True
        assert body["metadata"]["mansion_creation"]["source_id"] == source["id"]
    omitted, _ = _tk_case()
    dwelling._install(omitted)
    assert dwelling._source(omitted)["furnishings"] == []
    assert len(omitted[f"{SRD}.state"]["spatial_scene"]["entities"]) == 5


@pytest.mark.parametrize("defect", ["room", "overlap", "identity"])
def test_actual_invalid_furnishing_intent_is_atomic_before_creation(furnishing_scope, defect):
    state, _ = _tk_case()
    intent = deepcopy(FURNITURE)
    if defect == "room":
        intent[0]["extent"][2] = 11
    elif defect == "overlap":
        intent[1]["offset"] = intent[0]["offset"]
    else:
        intent[0]["id"] = "hero"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        dwelling._install(state, furnishings=intent)
    assert state == before


def test_committed_departure_smokes_only_created_furniture_and_same_room_movement_retains_it(
    furnishing_scope,
):
    state, _ = _tk_case()
    dwelling._install(state, furnishings=deepcopy(FURNITURE))
    source, room = deepcopy(dwelling._source(state)), dwelling._room(state)
    destination = [
        room["shape"]["min"][0] + 4,
        room["shape"]["min"][1] + 5,
        room["shape"]["min"][2],
    ]
    budget._run(
        "move_srd_travel_subject",
        state,
        subject="table",
        destination=destination,
        plane=source["room_id"],
    )
    assert _body(state, "table")["active"] is True
    assert "mansion_smoke" not in _body(state, "table")["metadata"]
    budget._run(
        "move_srd_travel_subject", state, subject="table", destination=[15, 0, 0], plane="material"
    )
    body = _body(state, "table")
    assert body["active"] is False and body["movement"] == "passable"
    assert body["metadata"]["mansion_smoke"]["position"] == [15, 0, 0]
    before = deepcopy(state)
    # An inactive smoke remnant cannot produce another callback mutation.
    budget._run("retire_srd_departed_mansion_furnishing", state, subject="table")
    assert state == before
    budget._run(
        "move_srd_travel_subject",
        state,
        subject="orb",
        destination=destination,
        plane=source["room_id"],
    )
    budget._run(
        "move_srd_travel_subject", state, subject="orb", destination=[20, 0, 0], plane="material"
    )
    assert _body(state, "orb")["active"] is True
    assert "mansion_smoke" not in _body(state, "orb")["metadata"]
    assert dwelling._source(state) == source


def _furnished_case(spell):
    state, data, params = dwelling._mansion_case(spell)
    params["mansion_furnishings"] = deepcopy(FURNITURE)
    return state, data, params


def test_twenty_furnishing_supported_batch_creates_actual_disjoint_bodies(furnishing_scope):
    state, _ = _tk_case()
    intents = [
        {
            "id": f"chair-{index}",
            "offset": [1 + 2 * (index % 5), 1 + 2 * (index // 5), 0],
            "extent": [0.5, 0.5, 1],
            "material": "wood",
            "appearance": "a small chair",
        }
        for index in range(20)
    ]
    dwelling._install(state, furnishings=intents)
    assert len(dwelling._source(state)["furnishings"]) == 20
    assert all(_body(state, row["id"])["active"] is True for row in intents)


def test_genuine_paid_completed_minute_creation_reopen_retry_and_invalid_intent(
    furnishing_scope, tmp_path, monkeypatch
):
    monkeypatch.setattr(public, "_spell_case", _furnished_case)
    actual = initial._dice(monkeypatch)
    path = tmp_path / "furnishings.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "magnificent_mansion")
        mechanics = StatelessMcpMechanics(owner)
        for name, arguments, key in (
            ("begin_srd_spell_cast", _begin(params), "begin"),
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
                "ready",
            ),
        ):
            result = mechanics.execute_action(
                dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            )
            assert result.status is ActionStatus.COMMITTED, result.result
        before = owner.snapshot("casting")
        invalid = deepcopy(FURNITURE)
        invalid[0]["extent"][2] = 11
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(
                dwelling._command(
                    params | {"mansion_furnishings": invalid}, before.revision, key="invalid"
                )
            )
        assert owner.snapshot("casting") == before
        command = dwelling._command(params, before.revision, key="paid")
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert dwelling._source(saved.state)["created_tick"] == 20
        assert dwelling._source(saved.state)["furnishings"] == FURNITURE
        assert (
            _body(saved.state, "table")["metadata"]["mansion_creation"]["source_id"] == "paid-house"
        )
        assert mechanics.execute_action(command) == receipt and owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert _body(owner.snapshot("casting").state, "statue")["active"] is True
        assert actual.consumed == []
