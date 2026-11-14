"""One paid bones-to-Skeleton journey through native control, attack and receiving owners."""

import json
from random import Random

import pytest
from scripts.srd_full.srd_animated_skeletons import extend_runtime, source_profile
from scripts.srd_full.srd_parameter_declarations import compact_runtime, restore_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.runtime._schema_validator import validate_ruleset
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_native_wight_rise import DEPENDENCIES, _victim, rise_owner
from tests.srd_full.test_srd_spell_summoning import (
    SRD,
    _animated_case,
    _finish_familiar_cast,
    _zombie_slam,
)
from tests.srd_full.test_srd_spell_wave3_information import _entity


@pytest.fixture(scope="module")
def skeleton_runtime():
    text = restore_runtime((DEPENDENCIES / "wight-public-parent-runtime.yaml").read_text())
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    runtime = _load_runtime(text)
    text = compact_runtime(text)
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="animated-skeleton")
    return runtime, text.encode()


@pytest.fixture
def skeleton_owner(monkeypatch, skeleton_runtime):
    return rise_owner.__wrapped__(monkeypatch, skeleton_runtime)


def test_paid_bones_creation_retains_source_gear_command_attack_and_vulnerability(
    monkeypatch,
    skeleton_owner,
):
    state, data, parameters = _animated_case(animated_bodies=[])
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    scene[1]["position"] = [80, 0, 0]
    bones = _entity("bones_one", [5, 0, 0], participant_ref=None)
    bones["footprint"] = {"kind": "box", "half_extents": [1, 1]}
    bones["height"] = 1
    bones["metadata"].pop("creature_type", None)
    bones["metadata"].update(is_object=True, humanoid_bones=True, bones_origin_size_rank=1)
    scene.append(bones)
    parameters["animated_bones"] = [
        {
            "bones_scene_id": "bones_one",
            "individual_id": "skeleton_one",
            "other_language": "draconic",
            "appearance_height": 6,
        }
    ]
    _finish_familiar_cast(state, data, parameters)
    source = source_profile()
    actor = state[f"{SRD}.entities"]["skeleton_one"]
    assert actor["hp"] == actor["max_hp"] == source["hit_points"]
    assert actor["armor_class"] == source["armor_class"]
    assert actor["initiative_modifier"] == source["initiative_modifier"]
    assert actor["ability_scores"] == source["ability_scores"]
    assert actor["condition_immunities"] == source["condition_immunities"]
    assert actor["inventory"] == ["Shortbow", "Shortsword"]
    assert actor["created_understood_languages"] == ["common", "draconic"]
    assert actor["languages"] == []
    assert actor["created_body_source_state_id"] is None
    assert actor["created_bone_source_scene_id"] == "bones_one"
    record = state[f"{SRD}.state"]["created_actor_records"]["skeleton_one"]
    assert record["source_block_sha256"] == actor["created_source_block_sha256"]
    assert record["body_source"] is None and record["bone_source_scene_id"] == "bones_one"
    assert "bones_one" not in state[f"{SRD}.entities"]
    consumed = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "bones_one"
    )
    assert consumed["active"] is False
    assert consumed["metadata"]["bones_consumed_by"] == "skeleton_one"
    state = json.loads(json.dumps(state))
    _victim(state, "skeleton_target")
    target_scene = state[f"{SRD}.state"]["spatial_scene"]["entities"][-1]
    target_scene["position"] = [15, 0, 0]
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "skeleton_one", "skeleton_target"],
    )
    commanded = False
    for _ in range(6):
        identity = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=identity, participant_data=data)
        if identity == "hero":
            _run(
                "command_srd_animated_undead",
                state,
                caster_state="hero",
                actor_ids=["skeleton_one"],
                order_kind="specific",
                action="attack",
            )
            commanded = True
        elif identity == "skeleton_one" and commanded:
            break
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    else:
        pytest.fail("The printed Skeleton did not receive its actual commanded native turn.")
    monkeypatch.setattr(dice, "_rng", Random(5))
    result = _zombie_slam(
        state,
        actor="skeleton_one",
        target="skeleton_target",
        attack_name="Shortsword",
        attack_modifier=5,
        damage_expression="d6",
        damage_modifier=3,
        damage_type="piercing",
        attack_uses_weapon=True,
    ).result["context"]
    assert result["_damage_applied"] > 0
    _run(
        "apply_srd_qualifying_damage",
        state,
        target="skeleton_one",
        amount=4,
        adjustment=0,
        damage_type="bludgeoning",
        critical_hit=False,
        source_state_id="",
    )
    assert state[f"{SRD}.entities"]["skeleton_one"]["hp"] == 5
