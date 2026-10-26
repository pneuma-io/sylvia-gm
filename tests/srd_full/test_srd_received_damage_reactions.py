"""Real damage queues one completed receiving witness without opening a Reaction."""

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_received_damage_reactions import QUEUE, bind_receiving_events

from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _case as _scene_case

NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-received-damage-reactions/ruleset.yaml")
)


@lru_cache
def _parent():
    encoded = NATIVE.read_bytes()
    assert sha256(encoded).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    return yaml.load(encoded, Loader=yaml.CSafeLoader), encoded


@lru_cache
def _runtime():
    text = bind_receiving_events(_parent()[1].decode())
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
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
def _owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case(temp=0, hp=100):
    state, data, _ = _scene_case("shield")
    actors = state[f"{SRD}.entities"]
    actors["hero"].update(creature_kind="character", prepared_spells=["hellish_rebuke"])
    actors["guard"].update(
        creature_kind="monster",
        prepared_spells=["hellish_rebuke"],
        hp=hp,
        max_hp=100,
        temp_hp=temp,
    )
    return state, data


def _events(state):
    owner = state[f"{SRD}.state"]
    return [owner["pending_srd_actions"][key] for key in owner["pending_srd_received_damage_ids"]]


@pytest.mark.parametrize("composite,temp,hp", [(False, 5, 100), (True, 0, 100), (False, 0, 2)])
def test_real_scalar_composite_and_overkill_keep_fresh_packet_and_exact_receipt(
    composite, temp, hp
):
    state, _ = _case(temp, hp)
    params = {"target": "guard", "source_state_id": "hero"}
    if composite:
        params.update(
            component_1_amount=5,
            component_1_damage_type="fire",
            component_2_amount=3,
            component_2_damage_type="cold",
        )
    else:
        params.update(amount=8, adjustment=0, damage_type="fire")
    result = _run(
        "apply_srd_composite_damage" if composite else "apply_srd_damage", state, **params
    )
    assert result.result["context"]["_effective_damage"] == 8
    (event,) = _events(state)
    assert event["kind"] == "received_damage" and event["status"] == "queued"
    assert event["actor_state_id"] == "hero" and event["target_state_id"] == "guard"
    receipt = event["received_damage"]
    assert receipt == {
        "effective_damage": 8,
        "hp_before": hp,
        "temp_hp_before": temp,
        "hp_after": max(0, hp - max(0, 8 - temp)),
        "temp_hp_after": max(0, temp - 8),
        "created_tick": 0,
    }
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []
    assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is True
    assert receipt["effective_damage"] == 8  # overkill never replaces the owning amount


@pytest.mark.parametrize(
    "reason", ["zero", "immune", "unknown", "object", "unprepared", "spell_force", "object_type"]
)
def test_ineligible_packets_do_not_create_damage_reaction_identity(reason):
    state, _ = _case()
    source, amount = "hero", 8
    if reason == "zero":
        amount = 0
    elif reason == "immune":
        state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["fire"]
    elif reason == "unknown":
        source = "unidentified"
    elif reason == "object":
        source = "orb"
    elif reason in {"spell_force", "object_type"}:
        metadata = state[f"{SRD}.state"]["spatial_scene"]["entities"][0]["metadata"]
        if reason == "spell_force":
            metadata["spell_force"] = True
        else:
            metadata["creature_type"] = "object"
    else:
        state[f"{SRD}.entities"]["guard"]["prepared_spells"] = []
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        source_state_id=source,
        amount=amount,
        adjustment=0,
        damage_type="fire",
    )
    assert not state[f"{SRD}.state"].get("pending_srd_received_damage_ids")


def test_emission_is_append_only_idempotent_and_queue_has_no_damage_or_reaction_edge():
    parent = _parent()[0]
    bound, encoded = _runtime()
    assert bind_receiving_events(encoded.decode()) == encoded.decode()
    for name in ("apply_srd_damage", "apply_srd_composite_damage", "apply_srd_qualifying_damage"):
        original = deepcopy(parent["tools"][name])
        filtered = [
            e
            for e in bound["tools"][name]["effects"]
            if QUEUE not in str(e) and "_received_temp_hp_before" not in str(e)
        ]
        assert filtered == original["effects"]
        assert original["parameters"] == bound["tools"][name]["parameters"]
    for name in (QUEUE, "queue_srd_creature_received_damage_reaction"):
        text = str(bound["tools"][name]["effects"])
        assert "apply_srd_" not in text and "cast_spell" not in text
        assert "open_srd_" not in text
