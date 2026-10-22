"""Source-created Mansion staff perform actual, bounded ordinary object tasks."""

import json
from copy import deepcopy

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
from tests import test_srd_spell_travel_furnishings as furnishings
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_initial_fine as initial
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case

SERVANT = [
    {
        "id": "attendant",
        "index": 100,
        "offset": [7, 2, 0],
        "extent": [2, 2, 6],
        "appearance": "a near-transparent human attendant",
        "attire": "a blue apron",
    }
]
CABINET = [
    {
        "id": "cabinet",
        "offset": [5, 2, 0],
        "extent": [2, 2, 3],
        "material": "wood",
        "appearance": "a plain wooden cabinet",
    }
]


@pytest.fixture
def servant_scope(monkeypatch):
    monkeypatch.setenv("FC14_DWELLING_RUNTIME", ".scratch/mansion-servant-composition/runtime.yaml")
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(
            row for row in record["tools"] if row["name"] == "traverse_srd_demiplane_door"
        )
        declaration = deepcopy(template)
        declaration["name"] = "command_srd_mansion_servant"
        declaration["input_schema"] = _tool_schema(
            fall._runtime()[0]["tools"][declaration["name"]], tool_name=declaration["name"]
        )
        record["tools"].append(declaration)
        payload = dict(primary.payloads)
        path = "content/content-declarations.json"
        data = json.loads(payload[path])
        inclusion = next(
            row
            for row in data["inclusions"]
            if "traverse_srd_demiplane_door" in row.get("links", {}).get("tools", [])
        )
        inclusion["links"]["tools"] = sorted(
            set(inclusion["links"]["tools"]) | {declaration["name"]}
        )
        payload[path] = canonical_json_bytes(data)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)
    yield from dwelling.dwelling_scope.__wrapped__(monkeypatch)


def _access(state, **facts):
    budget._run(
        "record_srd_object_access",
        state,
        object_id="cabinet",
        object_kind="container",
        closed=True,
        locks=facts.get("locks", []),
        stuck=facts.get("stuck", False),
        barred=False,
    )


def _ready(**facts):
    state, _ = _tk_case()
    dwelling._install(state, servants=deepcopy(SERVANT), furnishings=deepcopy(CABINET))
    _access(state, **facts)
    dwelling._enter(state)
    return state


def _command(state, operation="open", caster="hero"):
    return budget._run(
        "command_srd_mansion_servant",
        state,
        caster_state=caster,
        source_id="house",
        servant_id="attendant",
        object_id="cabinet",
        operation=operation,
    )


def test_actual_staff_index_contact_task_and_retirement_preserve_role_and_no_hp(servant_scope):
    state = _ready()
    source = dwelling._source(state)
    body = furnishings._body(state, "attendant")
    assert source["servant_indices"] == list(range(1, 101))
    assert len(source["servants"]) == 1 and body["metadata"]["mansion_servant"]["index"] == 100
    assert body["metadata"]["is_object"] is False and body["metadata"]["participant_ref"] is None
    assert body["metadata"]["near_transparent"] is True
    assert "hp" not in body and "attendant" not in state[f"{SRD}.entities"]
    budget._run(
        "qualify_srd_mansion_servant_invulnerability",
        state,
        source_id="house",
        servant_id="attendant",
    )
    resources = deepcopy(state[f"{SRD}.entities"]["hero"])
    tick = state[f"{SRD}.state"]["lasting_tick"]
    position = deepcopy(body["position"])
    _command(state)
    assert furnishings._body(state, "cabinet")["metadata"]["srd_object_access"]["closed"] is False
    _command(state, "close")
    assert furnishings._body(state, "cabinet")["metadata"]["srd_object_access"]["closed"] is True
    assert [r["operation"] for r in dwelling._source(state)["servant_orders"]] == ["open", "close"]
    assert state[f"{SRD}.entities"]["hero"] == resources
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
    assert furnishings._body(state, "attendant")["position"] == position
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="invulnerable Mansion servant"):
        budget._run(
            "prepare_srd_spell_object_node",
            state,
            object_id="attendant",
            material="wood",
            size="medium",
            durability="resilient",
        )
    assert state == before
    budget._run("end_srd_extradimensional_source", state, source_id="house")
    assert furnishings._body(state, "attendant")["active"] is False
    assert furnishings._body(state, "attendant")["metadata"]["is_object"] is False
    assert furnishings._body(state, "attendant")["position"] == position
    assert dwelling._source(state)["return_pending"] is True


@pytest.mark.parametrize("defect", ["caster", "contact", "lock", "escape"])
def test_actual_staff_boundaries_reject_atomically(servant_scope, defect):
    state = _ready(locks=["padlock"] if defect == "lock" else [])
    if defect == "contact":
        body = furnishings._body(state, "attendant")
        destination = list(body["position"])
        destination[1] += 4
        budget._run(
            "move_srd_travel_subject",
            state,
            subject="attendant",
            destination=destination,
            plane=dwelling._source(state)["room_id"],
        )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        if defect == "escape":
            budget._run(
                "move_srd_travel_subject",
                state,
                subject="attendant",
                destination=[15, 0, 0],
                plane="material",
            )
        else:
            _command(state, caster="guard" if defect == "caster" else "hero")
    assert state == before


def test_actual_servant_does_not_inherit_caster_arcane_lock_exemption(servant_scope):
    state = _ready()
    budget._run(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        spell="arcane_lock",
        slot_level=2,
        object_id="cabinet",
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock"):
        _command(state)
    assert state == before
    budget._run(
        "operate_srd_locked_object",
        state,
        actor_state="hero",
        object_id="cabinet",
        operation="open",
    )
    assert furnishings._body(state, "cabinet")["metadata"]["srd_object_access"]["closed"] is False


@pytest.mark.parametrize("end_kind", ["expiry", "antimagic"])
def test_actual_lifetime_or_magic_suppression_prevents_staff_task(servant_scope, end_kind):
    state = _ready()
    if end_kind == "expiry":
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
        assert furnishings._body(state, "attendant")["active"] is False
    else:
        dwelling._enter(state, "guard")
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
    with pytest.raises(RulesetPreconditionRejected):
        _command(state)
    assert state == before
    if end_kind == "antimagic":
        budget._run("end_concentration", state, caster_state="guard")
        _command(state)
        assert (
            furnishings._body(state, "cabinet")["metadata"]["srd_object_access"]["closed"] is False
        )


def _servant_case(spell):
    state, data, params = dwelling._mansion_case(spell)
    params["mansion_servants"] = deepcopy(SERVANT)
    params["mansion_furnishings"] = deepcopy(CABINET)
    return state, data, params


def test_genuine_paid_staff_creation_command_reopen_retry_has_one_completion(
    servant_scope, tmp_path, monkeypatch
):
    monkeypatch.setattr(public, "_spell_case", _servant_case)
    actual = initial._dice(monkeypatch)
    path = tmp_path / "servant.db"
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
            command = dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            result = mechanics.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
        invalid = deepcopy(params)
        invalid["mansion_servants"][0]["index"] = 101
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(dwelling._command(invalid, before.revision, key="invalid"))
        assert owner.snapshot("casting") == before
        for name, arguments, key in (
            ("cast_spell_outside_combat", params, "paid"),
            (
                "record_srd_object_access",
                {
                    "object_id": "cabinet",
                    "object_kind": "container",
                    "closed": True,
                    "locks": [],
                    "stuck": False,
                    "barred": False,
                },
                "facts",
            ),
        ):
            result = mechanics.execute_action(
                dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            )
            assert result.status is ActionStatus.COMMITTED, result.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        source, room = dwelling._source(saved.state), dwelling._room(saved.state)
        for name, arguments, key in (
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
                    "destination": [room["shape"]["min"][0] + 1.5, 0, room["shape"]["min"][2]],
                },
                "enter",
            ),
            (
                "command_srd_mansion_servant",
                {
                    "caster_state": "hero",
                    "source_id": source["id"],
                    "servant_id": "attendant",
                    "object_id": "cabinet",
                    "operation": "open",
                },
                "task",
            ),
        ):
            command = dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            result = mechanics.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            after = owner.snapshot("casting")
            assert (
                mechanics.execute_action(command) == result and owner.snapshot("casting") == after
            )
        assert (
            furnishings._body(after.state, "cabinet")["metadata"]["srd_object_access"]["closed"]
            is False
        )
        assert len(dwelling._source(after.state)["servant_orders"]) == 1
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert actual.consumed == []
