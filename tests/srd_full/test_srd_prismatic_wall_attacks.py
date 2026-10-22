"""Actual paid Sorcerous object attacks damage the exposed Wall layer without fake object HP."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import _emit
from scripts.srd_full.srd_prismatic_wall_attacks import bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as actions
from tests import test_srd_prismatic_wall as wall
from tests import test_srd_prismatic_wall_sources as sources
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_result_cards as cards

SRD = objects.SRD


@lru_cache
def _runtime():
    raw, _ = sources._runtime()
    text = bind_runtime(raw.decode())
    assert bind_runtime(text) == text
    runtime = _load_runtime(text)
    # The selected Source proof retains the final ordinary cast under its
    # exploration alias. Its actual cost effects are identical in this graph.
    if "cast_spell" not in runtime["tools"]:
        text = _emit(text, {"cast_spell": deepcopy(runtime["tools"]["cast_spell_outside_combat"])})
        runtime = _load_runtime(text)
    assert not (errors := validate_ruleset_data(runtime)), errors
    return text.encode(), runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="d" * 64,
        runtime_bytes=raw,
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
def _owners(monkeypatch):
    monkeypatch.setattr(objects, "_mechanic", _mechanic)
    monkeypatch.setattr(actions, "_mechanic", _mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _attack(state, source_id, kind="cold", seed=0):
    dice._rng = Random(seed)
    return objects._run(
        "cast_srd_sorcerous_burst_object",
        state,
        caster_state="hero",
        object_id=source_id,
        casting_ability="charisma",
        casting_modifier=4,
        casting_attack_bonus=10,
        casting_scaling_level=17,
        selected_damage_type=kind,
        sorcerous_burst_extra_dice=False,
    )


def _case():
    state, source_id, cast_id = sources._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] = [
        {
            "id": "actual_ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    return state, source_id, cast_id


def test_actual_cold_hits_accumulate_only_on_current_red_layer_across_saved_roundtrip():
    state, source_id, _ = _case()
    first = _attack(state, source_id)
    assert first["_object_attack_kind"] == "hit"
    assert first["_object_damage_rolled"] == 21
    assert first["_object_damage_applied"] == 21
    assert first["_object_hp_before"] is None and first["_object_hp_after"] is None
    assert wall._source(state, source_id)["outer_layer"] == 1
    wrong = _attack(state, source_id, "fire", seed=1)
    assert wrong["_object_damage_rolled"] > 0 and wrong["_object_damage_applied"] == 0
    assert wall._source(state, source_id)["layer_received_damage"] == {"cold": 21}
    state = json.loads(json.dumps(state))
    second = _attack(state, source_id, seed=1)
    assert second["_wall_attack_damage_after"] == 38
    assert second["_wall_attack_layer_ended"] is True
    assert wall._source(state, source_id)["outer_layer"] == 2
    assert "Retained matching damage 21 -> 38" in second["_burst_object_detail"]
    blocked_type = _attack(state, source_id, seed=1)
    assert blocked_type["_object_damage_applied"] == 0
    assert wall._source(state, source_id)["outer_layer"] == 2


def test_actual_fire_hits_damage_only_exposed_blue_and_critical_retains_actual_dice():
    state, source_id, _ = _case()
    # Earlier layer removals are only a fixture: this case proves the genuine
    # exposed-blue Fire producer, not Orange/Yellow/Green destruction.
    for layer in range(1, 5):
        objects._run(
            "retire_srd_prismatic_wall_layer",
            state,
            source_id=source_id,
            expected_layer=layer,
            reason="fixture exposure",
        )
    result = _attack(state, source_id, "fire", seed=5)
    assert result["_object_attack_kind"] == "critical_hit"
    assert len(result["_object_damage_roll"]["results"]) == 8
    assert result["_object_damage_rolled"] >= 25
    assert result["_wall_attack_layer_ended"] is True
    assert wall._source(state, source_id)["outer_layer"] == 6


def test_actual_miss_and_invalid_source_do_not_install_numeric_receipts():
    state, source_id, _ = _case()
    result = _attack(state, source_id, seed=31)
    assert result["_object_attack_kind"] == "miss"
    assert result["_object_damage_roll"] is None
    assert result["_object_damage_applied"] == 0
    assert not wall._source(state, source_id).get("layer_received_damage")
    wall._source(state, source_id)["expires_tick"] = 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state, source_id)
    assert state == before


def test_paid_named_cantrip_uses_actual_profile_action_and_layer_feedback(monkeypatch):
    base, source_id, _ = _case()
    state, data, parameters = cards._spell_case("sorcerous_burst")
    state[f"{SRD}.state"] = deepcopy(base[f"{SRD}.state"])
    state[f"{SRD}.entities"]["hero"]["last_spell_source_cast_id"] = None
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    dice._rng = Random(5)
    result = actions._run(
        "cast_spell",
        state,
        participant_data=data,
        **(
            parameters
            | {
                "target": None,
                "object_target_id": source_id,
                "selected_damage_type": "cold",
                "sorcerous_burst_extra_dice": False,
            }
        ),
    )
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert wall._source(state, source_id)["outer_layer"] == 2
    card = result.result["result_card"]
    assert card is not None and "layer ended True" in str(card)
    assert "Object HP" not in str(card)
    assert source_id in str(card)


def test_source_type_fence_and_ordinary_object_owner_survive_binding():
    _, runtime = _runtime()
    original = sources._runtime()[1]["tools"]["cast_srd_sorcerous_burst_object"]["effects"]
    branch = runtime["tools"]["cast_srd_sorcerous_burst_object"]["effects"][0]
    assert branch["on_false"] == original
    original_attack = sources._runtime()[1]["tools"]["resolve_srd_sorcerous_burst_object_attack"]
    observers = [
        effect
        for effect in original_attack["effects"]
        if "end_srd_invisibility_damage_source" in str(effect)
        and "end_srd_mislead_damage_source" in str(effect)
    ]
    actual_attack = runtime["tools"]["resolve_srd_prismatic_wall_sorcerous_attack"]
    assert actual_attack["effects"][-len(observers) :] == observers
    state, source_id, _ = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        objects._run(
            "resolve_srd_prismatic_wall_sorcerous_attack",
            state,
            caster_state="hero",
            object_id=source_id,
            attack_modifier=10,
            damage_die="d8",
            dice_count=4,
            damage_type="force",
            max_range_feet=120,
            casting_ability="charisma",
            casting_modifier=4,
        )
    assert state == before
