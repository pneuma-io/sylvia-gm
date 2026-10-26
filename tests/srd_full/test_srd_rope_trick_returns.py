"""Actual paid Rope Trick expiry opens the existing saved drop/Feather opportunity."""

import os
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_mansion_returns import bind_runtime_consumers as bind_mansion
from scripts.srd_full.srd_mirage_support import extend_runtime as emit_support
from scripts.srd_full.srd_rope_trick_returns import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_rope as rope
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_mansion_returns import ACTUAL, ACTUAL_SHA, _closure
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point, _TravelDice, dice

PATH = Path(
    os.environ.get(
        "FC14_ROPE_RETURN_RUNTIME",
        str(
            Path(__file__).resolve().parents[3]
            / "fc14-spells-20260930-wave8/portals/.scratch/rope32-runtime-immutable/runtime.yaml"
        ),
    )
)
EXPECTED_SHA = os.environ.get(
    "FC14_ROPE_RETURN_RUNTIME_SHA",
    "e3c4858d2b42efd1b4aba54ae570ca55a53cb87f83b6eab9b8b894fe0ad1a813",
)


@lru_cache
def _runtime():
    encoded = PATH.read_bytes()
    assert EXPECTED_SHA and sha256(encoded).hexdigest() == EXPECTED_SHA
    runtime = _load_runtime(encoded)
    actual_bytes = ACTUAL.read_bytes()
    assert sha256(actual_bytes).hexdigest() == ACTUAL_SHA
    actual = _load_runtime(actual_bytes)
    roots = {
        "move_srd_travel_subject",
        "check_srd_secure_area_movement",
        "check_srd_hallow_movement",
        "resolve_srd_forbiddance_event",
        "project_srd_body_movement_at_tick",
        "project_srd_animal_messenger_pace",
    }
    preserved = {
        name: deepcopy(tool)
        for name, tool in runtime["tools"].items()
        if name
        in {
            "advance_srd_lasting_clock",
            "resolve_srd_travel_placement",
            "resolve_srd_travel_geometry_face_placement",
            "resolve_srd_travel_entity_face_placement",
            "resolve_srd_object_content_geometry",
        }
        or "dwelling" in name
        or "extradimensional" in name
        or "mansion" in name
        or "rope_trick" in name
        or "rope_length" in name
    }
    room_guard = next(
        effect
        for effect in runtime["tools"]["move_srd_travel_subject_node"]["effects"]
        if effect.get("call") == "check_srd_dwelling_room_bounds"
    )
    runtime["tools"].update(
        {name: deepcopy(actual["tools"][name]) for name in _closure(actual["tools"], roots)}
    )
    runtime["tools"].update(preserved)
    # These pure owning leaves are selected from exact Wave32 source, not absent289 bytecode.
    support = _load_runtime(emit_support("tools:\nprocedures: {}\n"))["tools"]
    for name in ("qualify_srd_mirage_support_flight", "qualify_srd_mirage_magic_flight_source"):
        runtime["tools"][name] = support[name]
    node = runtime["tools"]["move_srd_travel_subject_node"]
    if "check_srd_dwelling_room_bounds" not in str(node["effects"]):
        boundary = next(
            i for i, effect in enumerate(node["effects"]) if "spatial_mutation" in effect
        )
        node["effects"].insert(boundary, deepcopy(room_guard))
    originals = {
        name: deepcopy(runtime["tools"][name])
        for name in (
            "move_srd_travel_subject",
            "move_srd_travel_subject_node",
            "end_srd_extradimensional_source",
        )
    }
    text = bind_runtime_consumers(
        bind_mansion(yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False))
    )
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    for name, original in originals.items():
        assert runtime["tools"][name] == original
    return runtime, text.encode()


@pytest.fixture
def returns_scope(monkeypatch):
    monkeypatch.setattr(fall, "_runtime", _runtime)
    monkeypatch.setattr(
        fall,
        "EXTRA_PUBLIC_TOOLS",
        frozenset(
            {
                "record_srd_rope_length",
                "climb_srd_rope_trick",
                "traverse_srd_rope_trick",
                "manipulate_srd_rope_trick_rope",
                "operate_srd_mansion_door",
                "traverse_srd_dwelling_door",
            }
        ),
    )
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _paid_case(_spell):
    state, data = rope._rope_case(length=30, spell="rope_trick")
    _, _, parameters = _case("rope_trick")
    parameters.update(rope_trick_rope="rope", rope_trick_id="rope-space")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("feather_fall")
    data["spellcasting_grants"][0]["spells"].append("feather_fall")
    data["wizard_spellbook"].append("feather_fall")
    supplies, _, _ = _case("feather_fall")
    for lot in supplies[f"{SRD}.entities"]["hero"]["spell_materials"]:
        saved = deepcopy(lot)
        saved["id"] = "feather_" + saved["id"]
        hero["spell_materials"].append(saved)
        if saved["inventory_item"] not in hero["inventory"]:
            hero["inventory"].append(saved["inventory_item"])
    return state, data, parameters


def _act(owner, tool, parameters, key):
    command = dwelling._command(parameters, owner.snapshot("casting").revision, tool, key)
    receipt = StatelessMcpMechanics(owner).execute_action(command)
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    saved = owner.snapshot("casting")
    assert StatelessMcpMechanics(owner).execute_action(command) == receipt
    assert owner.snapshot("casting") == saved
    return receipt, command


def _enter_paid(owner):
    _, _, parameters = public._create_spell_campaign(owner, "rope_trick")
    _act(owner, "cast_spell_outside_combat", parameters, "paid-rope")
    source = deepcopy(rope._source(owner.snapshot("casting").state))
    _act(
        owner,
        "move_srd_scene_creature",
        {"actor_state": "hero", "destination": [1, 0, 0]},
        "approach",
    )
    _act(
        owner,
        "climb_srd_rope_trick",
        {
            "actor_state": "hero",
            "source_id": source["id"],
            "destination_height": 10,
        },
        "first-climb",
    )
    _act(owner, "advance_srd_lasting_time", {"elapsed_rounds": 1}, "next-movement-round")
    body = rope._body(owner.snapshot("casting").state, "hero")
    _act(
        owner,
        "climb_srd_rope_trick",
        {
            "actor_state": "hero",
            "source_id": source["id"],
            "destination_height": source["origin"][2] - body["height"],
        },
        "opening-climb",
    )
    inner = next(
        row
        for row in owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == source["inner_id"]
    )
    _act(
        owner,
        "traverse_srd_rope_trick",
        {
            "actor_state": "hero",
            "source_id": source["id"],
            "door_subject": source["outer_id"],
            "destination": list(inner["position"]),
        },
        "enter",
    )
    assert (
        rope._body(owner.snapshot("casting").state, "hero")["metadata"]["plane"]
        == source["room_id"]
    )
    for operation in ("pull", "drop"):
        _act(
            owner,
            "manipulate_srd_rope_trick_rope",
            {"actor_state": "hero", "source_id": source["id"], "operation": operation},
            operation + "-rope",
        )
        assert rope._source(owner.snapshot("casting").state)["rope_pulled"] is (operation == "pull")
        actual_rope = next(
            row
            for row in owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == source["rope_id"]
        )
        assert actual_rope["metadata"]["plane"] == (
            source["room_id"] if operation == "pull" else source["origin_plane"]
        )
    return source


@pytest.mark.parametrize("protected", [False, True])
def test_paid_rope_expiry_real_return_saved_fall_reopen_retry_and_landing(
    returns_scope,
    tmp_path,
    monkeypatch,
    protected,
):
    monkeypatch.setattr(public, "_spell_case", _paid_case)
    actual = _TravelDice([] if protected else [(6, 4), (6, 5)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    path = tmp_path / "rope-return.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        source = _enter_paid(owner)
        before = owner.snapshot("casting")
        tick = before.state[f"{SRD}.state"]["lasting_tick"]
        rounds = (source["expires_tick"] - tick) / 2
        assert rounds == int(rounds)
        expiry, expiry_command = _act(
            owner, "advance_srd_lasting_time", {"elapsed_rounds": int(rounds)}, "expiry"
        )
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        body = rope._body(saved.state, "hero")
        assert body["metadata"]["plane"] == source["origin_plane"]
        assert body["position"][2] + body["height"] == pytest.approx(source["origin"][2])
        assert hero["scene_fall"]["status"] == "pending"
        assert hero["resources"]["spell_slot_2"] == 2
        trigger = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        event = saved.state[f"{SRD}.state"]["pending_srd_actions"][trigger]
        assert event["kind"] == "scene_fall" and event["status"] == "open"
        assert event["context"]["rope_trick_return_receipts"][0]["source_id"] == source["id"]
        assert actual.consumed == []
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(expiry_command) == expiry
        assert owner.snapshot("casting") == saved
        if protected:
            _, _, feather = _case("feather_fall")
            feather.update(
                targets=["hero"],
                reaction_trigger_id=trigger,
                material_item_ids=[
                    lot["id"] for lot in hero["spell_materials"] if lot["id"].startswith("feather_")
                ],
            )
            feather.pop("target", None)
            _act(owner, "cast_srd_reaction_spell", feather, "paid-feather")
            assert (
                owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"][
                    "spell_slot_1"
                ]
                == 2
            )
        landing, landing_command = _act(
            owner, "complete_srd_pending_action", {"pending_action_id": trigger}, "complete-fall"
        )
        after = owner.snapshot("casting")
        if protected:
            assert after.state[f"{SRD}.entities"]["hero"]["scene_fall"]["status"] == "descending"
            _act(owner, "advance_srd_lasting_time", {"elapsed_rounds": 1}, "descent")
            after = owner.snapshot("casting")
        assert _point(after.state, "hero")[2] == 0
        actor = after.state[f"{SRD}.entities"]["hero"]
        assert actor["scene_fall"]["status"] == "landed"
        assert actor["hp"] == hero["hp"] - (0 if protected else 9)
        assert ("prone" in actor["conditions"]) is (not protected)
        assert after.state[f"{SRD}.state"].get("rope_trick_pending_drops", []) == []
        final = after
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert StatelessMcpMechanics(owner).execute_action(landing_command) == landing
        assert owner.snapshot("casting") == final
        _act(owner, "advance_srd_lasting_time", {"elapsed_rounds": 1}, "later")
        assert _point(owner.snapshot("casting").state, "hero") == _point(final.state, "hero")
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["hp"] == actor["hp"]


def test_source_return_leaf_rejects_ordinary_call_without_receipt(returns_scope):
    state, _ = rope._rope_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Only actual queued Rope Trick"):
        budget._run(
            "return_srd_rope_trick_subject",
            state,
            subject="hero",
            destination=[1, 0, 4],
            plane="material",
        )
    assert state == before
