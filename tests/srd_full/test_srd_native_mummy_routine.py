"""Source Mummy mixed action uses genuine counts, saves, saved attacks and typed receivers."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.srd_native_mummy_routine import extend_runtime
from scripts.srd_full.srd_parameter_declarations import compact_runtime, restore_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_native_wight_rise import (
    DEPENDENCIES,
    _fresh_native_attack_window,
    rise_owner,
)
from tests.srd_full.test_srd_spell_summoning import SRD, _created_undead_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mummy_runtime():
    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    raw = (DEPENDENCIES / "wight-public-parent-runtime.yaml").read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "00a7de081d9880701c9bd84f339b10b53ff7eb99a945fc5e39f19b5e325099d5"
    )
    text = extend_runtime(restore_runtime(raw.decode()))
    assert extend_runtime(text) == text
    text = _fresh_native_attack_window(text)
    expanded = _load_runtime(text)
    text = compact_runtime(text)
    assert _load_runtime(restore_runtime(text)) == expanded
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="native-mummy-routine")
    return expanded, text.encode()


@pytest.fixture
def mummy_owner(monkeypatch, mummy_runtime):
    return rise_owner.__wrapped__(monkeypatch, mummy_runtime)


def _ogre(state, identity, position):
    """Use the printed Ogre receiver rather than an invented high-HP damage fixture."""
    row = json.loads(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/non-beast-form-index.json").read_text()
    )["forms"]["ogre"]
    assert (
        row["source_stat_block_sha256"]
        == "3a6c0e2ecb23b5c24721c296b795b400cb6c41edd877b96a0315d3202f4f2a86"
    )
    _run(
        "prepare_srd_creature",
        state,
        creature_id=identity,
        name=identity,
        source="SRD5.2.1 Ogre p312; receiving only, native attacks not selected.",
        creature_type=row["creature_type"],
        armor_class=row["armor_class"],
        hit_points=row["hit_points"],
        speed=row["walk_speed"],
        size_rank=row["size_rank"],
        initiative_modifier=row["initiative_modifier"],
        proficiency_bonus=2,
        **{a + "_score": value for a, value in row["ability_scores"].items()},
        **{a + "_save": value for a, value in row["saving_throw_modifiers"].items()},
    )
    scene = _entity(identity, position, participant_ref=identity, creature_type="giant")
    scene.update(footprint={"kind": "box", "half_extents": [5, 5]}, height=10)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(scene)


def _case():
    state, data, _ = _created_undead_case(1, 9)
    _run(
        "prepare_srd_source_mummy",
        state,
        creature_id="native_mummy",
        corpse_state="guard",
        source_caster="hero",
        other_languages=["elvish", "infernal"],
    )
    _run("update_srd_spell_force_scene", state, force_id="guard", position=[5, 0, 0], active=False)
    _run(
        "create_srd_source_actor_scene",
        state,
        caster_state="hero",
        summon_id="native_mummy",
        spell="create_undead",
        range_feet=10,
        half_extent=2.5,
        creature_type="undead",
        created_actor_position=[5, 0, 0],
        created_actor_height=6,
    )
    _ogre(state, "ogre_a", [15, 0, 0])
    _ogre(state, "ogre_b", [5, 12.5, 0])
    _run(
        "command_srd_animated_undead",
        state,
        caster_state="hero",
        actor_ids=["native_mummy"],
        order_kind="specific",
        action="attack",
    )
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "native_mummy", "ogre_a", "ogre_b"],
    )
    for _ in range(4):
        actor = state[f"{SRD}.combat"]["current_actor"]
        _run("begin_combat_turn", state, actor=actor, participant_data=data)
        if actor == "native_mummy":
            return state, data
        _run("end_combat_turn", state)
        _run("advance_group_combat_encounter_turn", state)
    pytest.fail("The real source Mummy did not reach its own initiative turn.")


def _fist(state, data, target, *, use_multiattack=True):
    relation = _run(
        "qualify_srd_created_actor_attack_geometry",
        state,
        actor_state="native_mummy",
        target_state=target,
    ).result["context"]
    return _run(
        "begin_srd_creature_attack",
        state,
        participant_data=data,
        actor="native_mummy",
        target=target,
        attack_name="Rotting Fist",
        attack_modifier=5,
        damage_expression="d10",
        damage_modifier=3,
        damage_type="bludgeoning",
        attack_mode="melee",
        normal_range=5,
        long_range=5,
        attack_distance=relation["_created_attack_distance"],
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
        advantage=False,
        disadvantage=False,
        use_multiattack=use_multiattack,
        attack_uses_weapon=False,
    )


def test_real_mixed_mummy_glare_then_two_saved_fists_reopen_and_finite_counts(mummy_owner):
    state, data = _case()
    assert state[f"{SRD}.entities"]["native_mummy"]["multiattack"] == [
        {"attack": "Rotting Fist", "count": 2},
        {"attack": "Dreadful Glare", "count": 1},
    ]
    dice._rng.seed(31)
    glare = _run(
        "use_srd_native_mummy_glare",
        state,
        actor_state="native_mummy",
        target_state="ogre_a",
        use_multiattack=True,
    ).result["context"]
    assert glare["_mummy_glare_outcome"] == "failure"
    assert glare["_mummy_glare_save_roll"]["results"] == [1]
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 2
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "use_srd_native_mummy_glare",
            state,
            actor_state="native_mummy",
            target_state="ogre_a",
            use_multiattack=True,
        )
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="same real native Mummy action"):
        _fist(state, data, "ogre_a", use_multiattack=False)
    assert state == before

    for count, target in ((1, "ogre_a"), (0, "ogre_b")):
        dice._rng.seed(5)
        started = _fist(state, data, target).result["context"]
        identity = started["_pending_action_id"]
        saved_roll = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][identity]["attack_roll"])
        assert saved_roll["results"] == [20]
        assert state[f"{SRD}.combat"]["attacks_remaining"] == count
        hp = state[f"{SRD}.entities"][target]["hp"]
        state = json.loads(json.dumps(state))
        completed = _run("complete_srd_pending_action", state, pending_action_id=identity)
        assert completed.result["context"]["_attack_roll"] == saved_roll
        victim = state[f"{SRD}.entities"][target]
        assert 0 < victim["hp"] < hp and victim["hit_point_recovery_blocked"] is True
        source_id = victim["mummy_rot_source_ids"][0]
        source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert source["native_attack"] == "Rotting Fist" and source["save_dc"] is None
        assert source["native_body_source"] == "native_mummy"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _fist(state, data, "ogre_a")
    assert state == before


def test_standalone_glare_retains_original_one_action_and_no_extra_fists(mummy_owner):
    state, data = _case()
    _run("use_srd_native_mummy_glare", state, actor_state="native_mummy", target_state="ogre_a")
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert state[f"{SRD}.combat"].get("attacks_remaining", 0) == 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _fist(state, data, "ogre_a")
    assert state == before
