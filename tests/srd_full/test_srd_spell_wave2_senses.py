"""Timed senses are consumed by later sight, literal reading and actual respiration."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_timed_senses import PROFILES, validate_profiles

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_public_spell_casting import SRD, _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import _spell_case

ROOT = Path(__file__).resolve().parents[2]


def _cast(spell, target="guard", **choices):
    state, data, params = _spell_case(spell)
    params.update(target=target, **choices)
    _run("cast_spell", state, participant_data=data, **params)
    return state, data, params


def _free(state):
    state[f"{SRD}.combat"].update(
        encounter_active=False, turn_active=False, active_actor=None, phase="exploration"
    )


def _summary(result):
    return {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}


def _sight(state, observer="guard", **scene):
    return _summary(
        _run(
            "resolve_srd_sight",
            state,
            observer_state=observer,
            **({"distance": 20, "lighting": "bright", "obstruction": "clear"} | scene),
        )
    )


@pytest.mark.parametrize("spell", PROFILES)
def test_senses_declarations_match_exact_source_profile_and_execute(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )
    row = declaration["spells"][spell]
    validate_profiles({spell: row["definition"]})
    wrong = deepcopy(row["definition"])
    wrong["duration_rounds"] += 1
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({spell: wrong})
    source = json.loads(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics-source.json").read_text()
    )["spells"][spell]
    assert source["source_unit_id"] == "srd.5.2.1.spells." + spell.replace("_", "-")
    state, _, _ = _cast(spell)
    effect = state[f"{SRD}.state"]["turn_effects"][-1]
    assert effect["kind"] == "timed_senses"
    assert effect["expires_tick"] == row["definition"]["duration_rounds"] * 2
    assert effect["target"] == ("hero" if row["definition"]["range_kind"] == "self" else "guard")
    if spell == "true_seeing":
        assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2


@pytest.mark.parametrize(
    "choices,message",
    [
        ({"target_willing": False}, "willing"),
        ({"caster_can_touch_target": False}, "touch"),
        ({"targets": ["guard", "other"]}, "limit"),
    ],
)
def test_touch_and_willing_targets_fail_atomically(choices, message):
    state, data, params = _spell_case("darkvision")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=message):
        _run("cast_spell", state, participant_data=data, **(params | choices))
    assert state == before


@pytest.mark.parametrize(
    "lighting,distance,can_see,light,gray",
    [
        ("dim", 150, True, "bright", False),
        ("darkness", 150, True, "dim", True),
        ("darkness", 151, False, "darkness", False),
        ("magical_darkness", 20, False, "magical_darkness", False),
    ],
)
def test_darkvision_range_light_and_colors(lighting, distance, can_see, light, gray):
    state, _, _ = _cast("darkvision")
    summary = _sight(state, lighting=lighting, distance=distance)
    assert (summary["Can see"], summary["Light seen"], summary["Colors are gray"]) == (
        can_see,
        light,
        gray,
    )


def test_darkvision_expiry_preserves_larger_native_sense():
    state, _, _ = _cast("darkvision")
    state[f"{SRD}.entities"]["guard"]["darkvision_feet"] = 180
    _free(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    assert _sight(state, lighting="darkness", distance=180)["Can see"] is True
    assert _sight(state, lighting="darkness", distance=181)["Can see"] is False


@pytest.mark.parametrize(
    "scene,visible,ghostly",
    [
        ({"subject_invisible": True, "distance": 10000}, True, False),
        ({"subject_ethereal": True}, True, True),
        ({"subject_invisible": True, "lighting": "darkness"}, False, False),
        ({"subject_invisible": True, "obstruction": "solid"}, False, False),
        ({"subject_ethereal": True, "obstruction": "heavy_obscurement"}, False, False),
    ],
)
def test_see_invisibility_creatures_objects_and_ethereal_scene(scene, visible, ghostly):
    state, _, _ = _cast("see_invisibility")
    summary = _sight(state, "hero", **scene)
    assert summary["Can see"] is visible
    assert summary["Ethereal and ghostly"] is ghostly


def test_see_invisibility_reads_saved_invisible_condition_and_loses_benefit_on_expiry():
    state, _, _ = _cast("see_invisibility")
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["invisible"]
    assert _sight(state, "hero", subject_state="guard")["Can see"] is True
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    assert _sight(state, "hero", subject_state="guard")["Can see"] is False
    state[f"{SRD}.entities"]["hero"]["conditions"] = []
    _free(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _sight(state, "hero", subject_state="guard")["Can see"] is False


def test_truesight_pierces_magic_darkness_and_visual_illusion_without_rolling():
    state, _, _ = _cast("true_seeing")
    summary = _sight(
        state,
        distance=120,
        lighting="magical_darkness",
        subject_invisible=True,
        subject_ethereal=True,
        visual_illusion=True,
        magically_transformed=True,
        test="visual_illusion_save",
        target_number=1000,
    )
    assert summary["Can see"] is True
    assert summary["Illusion transparent"] is True
    assert summary["Visual illusion save succeeds"] is True
    assert summary["True form discerned"] is True
    assert summary["Test outcome"] == "success"
    assert _sight(state, distance=121, lighting="magical_darkness")["Can see"] is False
    assert (
        _sight(state, obstruction="solid", visual_illusion=True)["Visual illusion save succeeds"]
        is False
    )
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["blinded"]
    assert _sight(state, visual_illusion=True)["Visual illusion save succeeds"] is False


def test_truesight_expiry_preserves_native_and_real_blur_attack_bypass():
    state, _, _ = _cast("true_seeing", target="hero")
    state[f"{SRD}.entities"]["guard"].update(
        has_timed_actor_defenses=True,
        concentration_spell_id="blur",
        concentration_effect_family="timed_actor_defense",
        concentration_started_tick=0,
        concentration_target_state_id="guard",
        concentration_target_state_ids=["guard"],
        concentration_expires_tick=20,
        concentration_save_modifier=0,
    )
    state[f"{SRD}.state"]["turn_effects"].append(
        {
            "kind": "timed_actor_defense",
            "spell": "blur",
            "caster": "guard",
            "target": "guard",
            "started_tick": 0,
            "expires_tick": 20,
            "concentration": True,
            "magnitude": 0,
            "owner": "__time_only__",
            "phase": "time",
            "turn": 1000000000,
            "dice_count": 0,
            "damage_die": "",
            "damage_type": "",
            "label": "blur",
        }
    )

    def attack(distance):
        result = _run(
            "resolve_srd_attack_conditions",
            state,
            actor_state="hero",
            target_state="guard",
            attack_distance=distance,
            actor_can_see_target=True,
            target_can_see_actor=True,
            fear_source_in_sight=False,
        )
        return result.result["context"]

    assert attack(120)["_condition_disadvantage"] is False
    assert attack(121)["_condition_disadvantage"] is True
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 180
    _free(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _sight(state, "hero", distance=180, lighting="magical_darkness")["Can see"] is True
    assert state[f"{SRD}.entities"]["hero"]["truesight_feet"] == 180


@pytest.mark.parametrize(
    "channel,extra",
    [
        ("heard", {}),
        ("signed", {}),
        ("written", {"touching_surface": True, "pages": 2}),
    ],
)
def test_literal_comprehension_and_written_reading_time(channel, extra):
    state, _, _ = _cast("comprehend_languages")
    _free(state)
    result = _summary(
        _run(
            "comprehend_srd_language",
            state,
            actor_state="hero",
            channel=channel,
            perceived=True,
            **extra,
        )
    )
    assert result["Literal meaning understood"] is True
    assert result["Reading rounds"] == (20 if channel == "written" else 0)
    assert state[f"{SRD}.state"]["lasting_tick"] == (40 if channel == "written" else 0)


@pytest.mark.parametrize(
    "choices,message",
    [
        ({"perceived": False}, "actually"),
        ({"symbols_or_secret_message": True}, "does not decode"),
        ({"channel": "written", "touching_surface": False}, "touch"),
    ],
)
def test_language_rejects_missing_perception_surface_or_secret_decoding(choices, message):
    state, _, _ = _cast("comprehend_languages")
    _free(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=message):
        _run(
            "comprehend_srd_language",
            state,
            **({"actor_state": "hero", "channel": "heard", "perceived": True} | choices),
        )
    assert state == before


def test_written_reading_cannot_outlast_effect_or_skip_active_combat_time():
    state, _, _ = _cast("comprehend_languages")
    with pytest.raises(RulesetPreconditionRejected, match="actual clock"):
        _run(
            "comprehend_srd_language",
            state,
            actor_state="hero",
            channel="written",
            perceived=True,
            touching_surface=True,
        )
    _free(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=591)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="expires"):
        _run(
            "comprehend_srd_language",
            state,
            actor_state="hero",
            channel="written",
            perceived=True,
            touching_surface=True,
        )
    assert state == before


def test_written_combat_interval_uses_real_time_same_surface_and_uninterrupted_reading():
    state, _, _ = _cast("comprehend_languages")
    facts = {
        "actor_state": "hero",
        "channel": "written",
        "perceived": True,
        "touching_surface": True,
        "surface_id": "inscribed_wall",
        "pages": 1,
    }
    start = _summary(_run("comprehend_srd_language", state, reading_phase="start", **facts))
    assert start["Literal meaning understood"] is False
    assert state[f"{SRD}.entities"]["hero"]["language_reader"]["due_tick"] == 20
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None
    for additions, message in [({}, "not elapsed"), ({"surface_id": "other_wall"}, "same written")]:
        with pytest.raises(RulesetPreconditionRejected, match=message):
            _run("comprehend_srd_language", state, reading_phase="finish", **(facts | additions))
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    with pytest.raises(RulesetPreconditionRejected, match="interrupted"):
        _run("comprehend_srd_language", state, reading_phase="finish", uninterrupted=False, **facts)
    assert (
        _summary(_run("comprehend_srd_language", state, reading_phase="finish", **facts))[
            "Literal meaning understood"
        ]
        is True
    )
    _run("comprehend_srd_language", state, reading_phase="start", **facts)
    _run("comprehend_srd_language", state, reading_phase="cancel", **facts)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    with pytest.raises(RulesetPreconditionRejected, match="No uninterrupted"):
        _run("comprehend_srd_language", state, reading_phase="finish", **facts)


def test_darkvision_dim_perception_has_real_d20_disadvantage_and_blindness_auto_fails():
    state, _, _ = _cast("darkvision")
    result = _run(
        "resolve_srd_sight",
        state,
        observer_state="guard",
        distance=20,
        lighting="darkness",
        obstruction="clear",
        test="perception",
        npc_modifier=2,
        target_number=10,
    ).result["context"]
    assert result["_effective_disadvantage"] is True
    assert result["_d20_test"]["results"]
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["blinded"]
    assert _sight(state, test="perception")["Test outcome"] == "failure"


def test_water_breathing_maximum_targets_range_and_duplicate_validation():
    state, data, params = _spell_case("water_breathing")
    entities = state[f"{SRD}.entities"]
    for index in range(11):
        entities[f"water_{index}"] = deepcopy(entities["guard"])
    targets = [f"water_{index}" for index in range(10)]
    for invalid, _message in [
        ({"targets": targets + ["water_10"]}, "limit"),
        ({"targets": ["guard", "guard"]}, "twice"),
        ({"targets": targets, "targets_max_distance": 31}, "range"),
        ({"targets": targets, "all_targets_willing": False}, "willing"),
    ]:
        before = deepcopy(state)
        with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
            _run("cast_spell", state, participant_data=data, **(params | invalid))
        assert state == before
    _run(
        "cast_spell",
        state,
        participant_data=data,
        **(params | {"targets": targets, "targets_max_distance": 30}),
    )
    assert len(state[f"{SRD}.state"]["turn_effects"]) == 10


def test_water_breathing_expiry_suffocation_and_recovery_preserve_other_exhaustion():
    state, _, _ = _cast("water_breathing")
    _free(state)
    creature = state[f"{SRD}.entities"]["guard"]
    creature.update(constitution_score=8, exhaustion_level=2, conditions=["exhaustion"])
    assert (
        _summary(_run("set_srd_respiration", state, target_state="guard", medium="water"))[
            "Can breathe"
        ]
        is True
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=14406)
    creature = state[f"{SRD}.entities"]["guard"]
    assert creature["respiration_can_breathe"] is False
    assert creature["respiration_breath_ticks"] == 0
    assert creature["exhaustion_level"] == 3
    assert creature["respiration_exhaustion"] == 1
    _run("set_srd_respiration", state, target_state="guard", medium="air")
    creature = state[f"{SRD}.entities"]["guard"]
    assert creature["exhaustion_level"] == 2
    assert creature["respiration_exhaustion"] == 0


def test_water_breathing_retains_air_and_native_water_beyond_expiry_and_cannot_cure_choking():
    state, _, _ = _cast("water_breathing")
    _free(state)
    creature = state[f"{SRD}.entities"]["guard"]
    creature.update(constitution_score=10, breathes_water=True)
    _run("set_srd_respiration", state, target_state="guard", medium="air")
    assert state[f"{SRD}.entities"]["guard"]["respiration_can_breathe"] is True
    _run("set_srd_respiration", state, target_state="guard", medium="water")
    _run("advance_srd_lasting_time", state, elapsed_rounds=14401)
    assert state[f"{SRD}.entities"]["guard"]["respiration_can_breathe"] is True
    _run("set_srd_respiration", state, target_state="guard", medium="water", choking=True)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.entities"]["guard"]["exhaustion_level"] == 1


def test_suffocation_real_turn_exhaustion_death_and_external_reduction_bookkeeping():
    state, data, _ = _cast("water_breathing")
    creature = state[f"{SRD}.entities"]["hero"]
    creature.update(constitution_score=8, exhaustion_level=4, conditions=["exhaustion"])
    _run(
        "set_srd_respiration",
        state,
        participant_data=data,
        target_state="hero",
        target_character="hero",
        medium="no_air",
        choking=True,
    )
    _run("end_combat_turn", state, participant_data=data)
    assert state[f"{SRD}.entities"]["hero"]["exhaustion_level"] == 5
    _run("reduce_srd_exhaustion", state, target="hero", levels=1)
    assert state[f"{SRD}.entities"]["hero"]["respiration_exhaustion"] == 0
    state[f"{SRD}.combat"].update(turn_active=True, phase="turn_active")
    _run("end_combat_turn", state, participant_data=data)
    state[f"{SRD}.combat"].update(turn_active=True, phase="turn_active")
    _run("end_combat_turn", state, participant_data=data)
    assert state[f"{SRD}.entities"]["hero"]["is_alive"] is False
    assert "dead" in state[f"{SRD}.entities"]["hero"]["conditions"]


def test_ten_water_breathers_each_receive_later_expiry_consequences_with_existing_cap():
    state, data, params = _spell_case("water_breathing")
    entities = state[f"{SRD}.entities"]
    targets = [f"swimmer_{index}" for index in range(10)]
    for target in targets:
        entities[target] = deepcopy(entities["guard"])
        entities[target]["constitution_score"] = 8
    _run("cast_spell", state, participant_data=data, **(params | {"targets": targets}))
    _free(state)
    for target in targets:
        _run("set_srd_respiration", state, target_state=target, medium="water")
    assert state[f"{SRD}.state"]["respiration_targets"] == targets
    _run("advance_srd_lasting_time", state, elapsed_rounds=14406)
    for target in targets:
        assert state[f"{SRD}.entities"][target]["exhaustion_level"] == 1
        assert state[f"{SRD}.entities"][target]["respiration_can_breathe"] is False


def test_character_breath_capacity_uses_character_file_over_mutable_score():
    state, data, _ = _cast("water_breathing", target="hero")
    data["abilities"]["constitution"] = 8
    state[f"{SRD}.entities"]["hero"]["constitution_score"] = 30
    _run(
        "set_srd_respiration",
        state,
        participant_data=data,
        target_state="hero",
        target_character="hero",
        medium="water",
    )
    assert state[f"{SRD}.entities"]["hero"]["respiration_capacity_ticks"] == 10


def test_senses_dismissal_is_free_source_owned_and_blocked_by_incapacitation():
    state, _, _ = _cast("darkvision")
    state[f"{SRD}.entities"]["guard"]["darkvision_feet"] = 60
    _run(
        "install_srd_timed_senses",
        state,
        spell="darkvision",
        caster_state="other",
        target_state="guard",
    )
    action_before = state[f"{SRD}.combat"]["action_available"]
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["incapacitated"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Incapacitated"):
        _run("dismiss_srd_senses_spell", state, caster_state="hero", spell="darkvision")
    assert state == before
    state[f"{SRD}.entities"]["hero"]["conditions"] = []
    _run("dismiss_srd_senses_spell", state, caster_state="hero", spell="darkvision")
    assert state[f"{SRD}.combat"]["action_available"] is action_before
    assert _sight(state, distance=150, lighting="darkness")["Can see"] is True
    _run("dismiss_srd_senses_spell", state, caster_state="other", spell="darkvision")
    assert _sight(state, distance=150, lighting="darkness")["Can see"] is False
    assert _sight(state, distance=60, lighting="darkness")["Can see"] is True


def test_dismissing_water_breathing_truncates_underwater_cover_at_actual_tick():
    state, _, _ = _cast("water_breathing")
    _free(state)
    state[f"{SRD}.entities"]["guard"]["constitution_score"] = 8
    _run("set_srd_respiration", state, target_state="guard", medium="water")
    _run("advance_srd_lasting_time", state, elapsed_rounds=20)
    _run("dismiss_srd_senses_spell", state, caster_state="hero", spell="water_breathing")
    assert state[f"{SRD}.entities"]["guard"]["respiration_can_breathe"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=6)
    assert state[f"{SRD}.entities"]["guard"]["exhaustion_level"] == 1


def test_generic_movement_cannot_bypass_hypnotic_patterns_actual_speed_zero_target():
    state, _, _ = _cast("darkvision")
    state[f"{SRD}.entities"]["guard"].update(
        movement_remaining={"ground": 30},
        distance_travelled={"ground": 0},
        timed_control_speed_zero=True,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Hypnotic Pattern"):
        _run(
            "spend_srd_movement",
            state,
            target="guard",
            mode="ground",
            distance=1,
            difficult_terrain=False,
        )
    assert state == before
    state[f"{SRD}.entities"]["guard"]["timed_control_speed_zero"] = False
    _run(
        "spend_srd_movement",
        state,
        target="guard",
        mode="ground",
        distance=1,
        difficult_terrain=False,
    )
    assert state[f"{SRD}.entities"]["guard"]["movement_remaining"]["ground"] == 29


def test_public_saved_written_interval_reopens_before_clock_and_finish(tmp_path):
    database = tmp_path / "reader.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = _create_spell_campaign(owner, "comprehend_languages")
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="cast",
            idempotency_key="cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics.execute_action(command)
        facts = {
            "actor_state": "hero",
            "channel": "written",
            "perceived": True,
            "touching_surface": True,
            "surface_id": "public_inscription",
            "pages": 2,
        }
        command = replace(
            command,
            action_id="start",
            idempotency_key="start",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="comprehend_srd_language",
            parameters=facts | {"reading_phase": "start"},
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["language_reader"]["due_tick"] == 40
        assert mechanics.execute_action(command) == receipt
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        advance = replace(
            command,
            action_id="time",
            idempotency_key="time",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 20},
        )
        mechanics.execute_action(advance)
        finish = replace(
            command,
            action_id="finish",
            idempotency_key="finish",
            expected_revision=owner.snapshot("casting").revision,
            parameters=facts | {"reading_phase": "finish"},
        )
        receipt = mechanics.execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED
        assert receipt.result["context"]["_language_literal_understood"] is True
        after = owner.snapshot("casting")
        assert mechanics.execute_action(finish) == receipt
        assert owner.snapshot("casting").state == after.state


@pytest.mark.parametrize(
    "spell,tool,params,label,expected",
    [
        (
            "see_invisibility",
            "resolve_srd_sight",
            {
                "observer_state": "hero",
                "distance": 30,
                "lighting": "bright",
                "obstruction": "clear",
                "subject_invisible": True,
            },
            "Can see",
            True,
        ),
        (
            "comprehend_languages",
            "comprehend_srd_language",
            {
                "actor_state": "hero",
                "channel": "written",
                "perceived": True,
                "touching_surface": True,
            },
            "Literal meaning understood",
            True,
        ),
        (
            "water_breathing",
            "set_srd_respiration",
            {"target_state": "guard", "medium": "water"},
            "Can breathe",
            True,
        ),
    ],
)
def test_public_senses_discovery_reopen_retry_and_later_consumer(
    tmp_path, spell, tool, params, label, expected
):
    database = tmp_path / (spell + ".db")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner, spell, target_overrides={"constitution_score": 8}
        )
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="cast",
            idempotency_key="cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        declaration = next(t for t in mechanics.list_tools("casting") if t.name == tool)
        assert "lasting_state" not in declaration.input_schema["properties"]
        command = replace(
            command,
            action_id="consume",
            idempotency_key="consume",
            expected_revision=saved.revision,
            tool_name=tool,
            parameters=params,
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        values = {row["label"]: row["value"] for row in receipt.result["result_card"]["summary"]}
        assert values[label] is expected
        after = owner.snapshot("casting")
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == after.state
