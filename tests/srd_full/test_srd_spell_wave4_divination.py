"""Shared divination owners enforce actual saved clocks, messages and later consequences."""

from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_divination_spells import PROFILES, validate_profiles

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin, _external_slow
from tests.srd_full.test_srd_spell_wave3_information import SRD, _free, _wall
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case

ROOT = Path(__file__).resolve().parents[2]


def _case(spell, **choices):
    state, data, params = _information_case(spell)
    _free(state)
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(
        data["abilities"], 30
    )
    params.update(
        message_words=["The", "gate", "opens", "tonight"],
        divination_event_tick=500,
        divination_subject="the gate opens",
        divination_subject_met=True,
        divination_subject_familiar=True,
        divination_known_beyond_barrier=True,
        divination_gm_answer="The gate opens tonight.",
        divination_subject_famous=True,
    )
    params.update(choices)
    if spell == "identify":
        params.update(divination_subject="orb", divination_touch_uninterrupted=True)
        orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
        orb["position"] = [5, 0, 0]
        orb["metadata"].update(
            identify_properties="a magic light",
            identify_how_to_use="speak the word",
            identify_requires_attunement=False,
            identify_charges=3,
            created_by_spell="continual_flame",
        )
    if spell in {"mind_spike", "scrying", "detect_thoughts", "animal_messenger"}:
        state[f"{SRD}.entities"]["guard"].update(hp=500, max_hp=500, temp_hp=0)
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"].update(wisdom=-30, charisma=-30)
    if spell == "animal_messenger":
        state[f"{SRD}.entities"]["guard"].update(
            creature_type="beast", size_rank=0, challenge_rating_eighths=0
        )
        params.update(
            divination_visited_location=True,
            divination_recipient_description="the human guard",
            divination_destination=[40, 0, 0],
        )
    return state, data, params


def _cast(spell, **choices):
    state, data, params = _case(spell, **choices)
    if PROFILES[spell]["casting_time"] not in {"action", "bonus_action"}:
        begin = _begin(params)
        if spell == "identify":
            begin.update(
                divination_subject=params["divination_subject"], divination_touch_uninterrupted=True
            )
        _run("begin_srd_spell_cast", state, participant_data=data, **begin)
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=100 if spell in {"legend_lore", "scrying"} else 10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            divination_touch_uninterrupted=spell == "identify",
        )
    result = _run("cast_spell", state, participant_data=data, **params)
    return state, data, params, result


@pytest.mark.parametrize("tool", ["begin_srd_spell_cast", "begin_srd_extended_spell_action"])
def test_failed_slow_identify_attempt_cannot_create_a_touch_channel(monkeypatch, tool):
    state, data, params = _case("identify")
    _external_slow(state, data)
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    beginning = _begin(params)
    beginning.update(divination_subject="orb", divination_touch_uninterrupted=True)
    result = _run(tool, state, participant_data=data, **beginning).result
    hero = state[f"{SRD}.entities"]["hero"]
    assert result["context"]["_slow_cast_failed"] is True
    assert hero.get("extended_casting") is None
    assert hero["resources"]["spell_slot_1"] == 2
    assert not state[f"{SRD}.state"].get("pending_srd_action_stack")


def test_identify_casting_reaction_windows_preserve_touch_and_later_progress():
    state, data, params = _case("identify")
    beginning = _begin(params)
    beginning.update(divination_subject="orb", divination_touch_uninterrupted=True)
    _run("begin_srd_extended_spell_action", state, participant_data=data, **beginning)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["extended_casting"]["identify_subject"] == "orb"
    assert hero["extended_casting"]["progress_rounds"] == 0
    event = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    _run("complete_srd_pending_action", state, pending_action_id=event)
    _run("continue_srd_extended_spell_action", state, caster_state="hero", elapsed_rounds=1,
         uninterrupted=True, verbal_component_available=True,
         somatic_component_available=True, material_component_available=True,
         divination_touch_uninterrupted=True)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["extended_casting"]["identify_subject"] == "orb"
    assert hero["extended_casting"]["progress_rounds"] == 1
    assert hero.get("last_identify") is None
    assert hero["resources"]["spell_slot_1"] == 3


def test_slow_failed_detect_thoughts_spends_slot_without_installing_effect(monkeypatch):
    state, data, params = _case("detect_thoughts")
    _external_slow(state, data)
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _run("cast_spell", state, participant_data=data, **params).result
    hero = state[f"{SRD}.entities"]["hero"]
    assert result["context"]["_slow_cast_failed"] is True
    assert hero["resources"]["spell_slot_2"] == 2
    assert hero.get("concentration_spell_id") is None
    assert not any(record.get("spell") == "detect_thoughts"
                   for record in state[f"{SRD}.state"]["turn_effects"])


@pytest.mark.parametrize(
    "spell",
    [
        "identify",
        "scrying",
        "detect_thoughts",
        "mind_spike",
        "animal_messenger",
        "augury",
        "divination",
        "legend_lore",
        "commune_with_nature",
    ],
)
def test_source_leaf_casts_execute_actual_family(spell):
    state, _, _, _ = _cast(spell)
    if PROFILES[spell]["concentration"]:
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == spell
    if spell == "identify":
        assert any(
            "charges: 3" in x
            for x in state[f"{SRD}.entities"]["hero"]["last_identify"]["information"]
        )


def test_identify_actual_movement_interrupts_touch_and_leaves_slot_unspent():
    state, data, params = _case("identify")
    begin = _begin(params) | {"divination_subject": "orb", "divination_touch_uninterrupted": True}
    _run("begin_srd_spell_cast", state, participant_data=data, **begin)
    before_slot = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 5, 0])
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == before_slot
    with pytest.raises(RulesetPreconditionRejected, match="longer casting"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            divination_touch_uninterrupted=True,
        )


def test_thought_probe_requires_a_later_turn_then_target_action_can_end_it():
    state, _, _, _ = _cast(
        "detect_thoughts",
        divination_thought_mode="read",
        divination_gm_answer="The gate worries me.",
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="later turn"):
        _run(
            "activate_srd_detect_thoughts",
            state,
            caster_state="hero",
            target_state="guard",
            mode="probe",
            content="The gate is dangerous.",
        )
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run(
        "activate_srd_detect_thoughts",
        state,
        caster_state="hero",
        target_state="guard",
        mode="probe",
        content="Fear of the gate.",
    )
    assert state[f"{SRD}.entities"]["guard"]["detect_thoughts_probe_known"] is True
    state[f"{SRD}.entities"]["guard"]["skill_modifiers"] = {"arcana": 30}
    _run("contest_srd_detect_thoughts", state, caster_state="hero", target_state="guard")
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_mind_spike_consumes_actual_position_and_specific_invisible_exception():
    from tests.srd_full.test_srd_spell_wave3_information import _summary

    state, _, _, _ = _cast("mind_spike")
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["invisible"]
    state[f"{SRD}.entities"]["guard"]["hidden"] = True
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [80, 0, 0]
    result = _summary(
        _run("query_srd_mind_spike", state, caster_state="hero", target_state="guard")
    )
    assert result["Position"] == [80, 0, 0]
    sight = {
        "observer_state": "hero",
        "subject_state": "guard",
        "distance": 80,
        "lighting": "bright",
        "obstruction": "clear",
    }
    assert _summary(_run("resolve_srd_sight", state, **sight))["Can see"] is True
    state[f"{SRD}.entities"]["other"].update(conditions=[], is_alive=True)
    sight["observer_state"] = "other"
    assert _summary(_run("resolve_srd_sight", state, **sight))["Can see"] is False
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "ethereal"
    with pytest.raises(RulesetPreconditionRejected, match="tracking"):
        _run("query_srd_mind_spike", state, caster_state="hero", target_state="guard")


def test_scrying_actual_ten_minutes_follow_sight_hearing_and_cleanup():
    from tests.srd_full.test_srd_spell_wave3_information import _summary

    state, _, _, _ = _cast("scrying")
    record = _record(state, "scrying")
    assert record["started_tick"] == 200
    scene = state[f"{SRD}.state"]["spatial_scene"]
    guard = next(x for x in scene["entities"] if x["id"] == "guard")
    guard["position"] = [100, 0, 0]
    guard["metadata"]["audible_to"] = [record["proxy_id"]]
    result = _summary(_run("read_srd_scrying_sensor", state, caster_state="hero"))
    assert any("guard" in x for x in result["Sight"])
    assert any("guard" in x for x in result["Hearing"])
    sensor = next(
        x
        for x in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if x["id"] == record["proxy_id"]
    )
    assert sensor["position"] == [100, 0, 0]
    assert sensor["metadata"]["intangible"] is True
    _run("dismiss_srd_divination_spell", state, caster_state="hero", spell="scrying")
    sensor = next(
        x
        for x in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if x["id"] == record["proxy_id"]
    )
    assert sensor["active"] is False


def test_animal_messenger_real_travel_credit_delivery_and_expired_return():
    state, _, _, _ = _cast("animal_messenger")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    scene["entities"].append(_entity("other", [40, 0, 0], participant_ref="other"))
    with pytest.raises(RulesetPreconditionRejected, match="real time"):
        _run(
            "advance_srd_animal_messenger",
            state,
            target_state="guard",
            destination=[40, 0, 0],
            recipient_state="other",
            recipient_matches_description=True,
        )
    _run("advance_srd_lasting_time", state, elapsed_rounds=3)
    _run(
        "advance_srd_animal_messenger",
        state,
        target_state="guard",
        destination=[40, 0, 0],
        recipient_state="other",
        recipient_matches_description=True,
    )
    assert state[f"{SRD}.entities"]["other"]["received_animal_message"]["mimics_caster"] is True
    state2, _, _, _ = _cast("animal_messenger")
    _run("advance_srd_lasting_time", state2, elapsed_rounds=14400)
    assert state2[f"{SRD}.entities"]["guard"]["animal_messenger"]["message_lost"] is True
    _run("advance_srd_lasting_time", state2, elapsed_rounds=3)
    _run("advance_srd_animal_messenger", state2, target_state="guard", destination=[0, 0, 0])
    assert state2[f"{SRD}.entities"]["guard"]["animal_messenger"]["phase"] == "returned"


def test_contact_failure_uses_pc_intelligence_save_and_exact_recovery_preserves_other_source(
    monkeypatch,
):
    from random import Random

    from sylvia_gm import dice

    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _case("contact_other_plane")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=500, max_hp=500, temp_hp=0)
    data["saving_throw_proficiencies"] = ["intelligence", "wisdom"]
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    result = _run("cast_spell", state, participant_data=data, **params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert "incapacitated" in hero["conditions"]
    assert 464 <= hero["hp"] <= 494
    _run(
        "install_srd_timed_condition_source",
        state,
        target_state="hero",
        lasting_state="state",
        spell="synthetic_unrelated",
        condition="incapacitated",
        save_dc=15,
        duration_rounds=0,
        repeat_save_timing="none",
        save_ability="wisdom",
    )
    _run("clear_srd_contact_other_plane", state, target_state="hero")
    assert "incapacitated" in state[f"{SRD}.entities"]["hero"]["conditions"]
    sources = state[f"{SRD}.state"]["timed_condition_sources"].values()
    assert any(x["installed"] and x["spell_id"] == "synthetic_unrelated" for x in sources)
    assert not any(x["installed"] and x["spell_id"] == "contact_other_plane" for x in sources)
    assert result.result["status"] == "committed"


def test_contact_other_plane_successful_long_rest_owns_actual_cure():
    state, _, _ = _case("contact_other_plane")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=50, max_hp=50, resource_maxima={}, resources={})
    _run(
        "install_srd_timed_condition_source",
        state,
        target_state="hero",
        lasting_state="state",
        spell="contact_other_plane",
        condition="incapacitated",
        save_dc=15,
        duration_rounds=0,
        repeat_save_timing="none",
        save_ability="intelligence",
    )
    _run("begin_srd_rest", state, target_state="hero", kind="long", activity="sleep")
    with pytest.raises(RulesetPreconditionRejected, match="eight hours"):
        _run("complete_srd_rest", state, target_state="hero")
    assert "incapacitated" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    _run("complete_srd_rest", state, target_state="hero")
    assert "incapacitated" not in state[f"{SRD}.entities"]["hero"]["conditions"]


def test_nature_actual_range_qualifies_only_selected_category_and_requires_prevalence_fact():
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    state, data, params = _case(
        "commune_with_nature",
        divination_subject="",
        divination_nature_facts=["prevalent_mineral", "water", "portals"],
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].extend(
        [
            _entity(
                "world_fact",
                [100, 0, 0],
                nature_prevalent_category="mineral",
                nature_prevalent_kind="quartz",
                nature_region_qualified=True,
            ),
            _entity("lake", [15841, 0, 0], body_of_water=True),
            _entity("portal", [100, 0, 0], planar_portal=True),
        ]
    )
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    facts = state[f"{SRD}.entities"]["hero"]["last_nature_divination"]["facts"]
    assert "prevalent_mineral: quartz" in facts
    assert any("portals: portal" in x for x in facts)
    assert not any("lake" in x for x in facts)
    assert not any("fiend" in x for x in facts)


def test_scrying_successful_save_blocks_this_caster_for_actual_24_hours():
    state, data, params = _case("scrying")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 30
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["guard"]["scrying_save_blocks"]["hero"] == 29000
    assert not state[f"{SRD}.state"]["turn_effects"]
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    with pytest.raises(RulesetPreconditionRejected, match="24 hours"):
        _run("cast_spell", state, participant_data=data, **params)


@pytest.mark.parametrize(
    "spell,later", [("sending", "reply_srd_spell_message"), ("scrying", "read_srd_scrying_sensor")]
)
def test_public_casting_reopens_retries_and_consumes_source(tmp_path, monkeypatch, spell, later):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / f"divination-{spell}.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, spell)
        mechanics = StatelessMcpMechanics(owner)
        advertised = mechanics.list_tools("casting")
        assert later in {t.name for t in advertised}
        assert (
            "lasting_state"
            not in next(t.input_schema for t in advertised if t.name == later)["properties"]
        )
        if spell == "scrying":
            begin, begin_command = _act(owner, "begin_srd_spell_cast", **_begin(params))
            assert begin.status is ActionStatus.COMMITTED
            assert mechanics.execute_action(begin_command) == begin
            saved = owner.snapshot("casting")
        else:
            receipt, command = _act(owner, "cast_spell_outside_combat", **params)
            assert receipt.status is ActionStatus.COMMITTED
            assert mechanics.execute_action(command) == receipt
            saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        if spell == "scrying":
            assert mechanics.execute_action(begin_command) == begin
            continued, continued_command = _act(
                owner,
                "continue_srd_spell_cast",
                caster_state="hero",
                elapsed_rounds=100,
                uninterrupted=True,
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
            )
            assert continued.status is ActionStatus.COMMITTED
            assert mechanics.execute_action(continued_command) == continued
            assert owner.snapshot("casting").state[f"{SRD}.state"]["lasting_tick"] == 200
            receipt, command = _act(owner, "cast_spell_outside_combat", **params)
            assert receipt.status is ActionStatus.COMMITTED
            assert mechanics.execute_action(command) == receipt
            choices = {"caster_state": "hero"}
        else:
            assert mechanics.execute_action(command) == receipt
            received = saved.state[f"{SRD}.entities"]["guard"]["received_spell_message"]
            choices = {
                "recipient_state": "guard",
                "sender_state": "hero",
                "message_id": received["id"],
                "reply_words": ["Received"],
            }
        consequence, consequence_command = _act(owner, later, **choices)
        assert consequence.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert mechanics.execute_action(consequence_command) == consequence
        assert owner.snapshot("casting").state == after.state


def _record(state, spell):
    return next(
        x
        for x in state[f"{SRD}.state"]["turn_effects"]
        if x.get("spell") == spell and x.get("kind") == "divination_spell"
    )


@pytest.mark.parametrize("spell", PROFILES)
def test_divination_source_profiles_reject_changed_fixed_constants(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )
    profile = declaration["spells"][spell]["definition"]
    validate_profiles({spell: profile})
    bad = deepcopy(profile)
    bad["range_feet"] += 1
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({spell: bad})


def test_commune_question_limit_and_actual_expiry_preserve_rejected_state():
    state, _, _, _ = _cast("commune")
    for question in range(3):
        _run(
            "ask_srd_divination_question",
            state,
            caster_state="hero",
            spell="commune",
            question=f"Is gate {question} open?",
            answer_words=["yes"],
            answer_is_correct=True,
        )
    assert _record(state, "commune")["questions_used"] == 3
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="limit"):
        _run(
            "ask_srd_divination_question",
            state,
            caster_state="hero",
            spell="commune",
            question="Another?",
            answer_words=["no"],
            answer_is_correct=True,
        )
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    with pytest.raises(RulesetPreconditionRejected, match="interval"):
        _run(
            "ask_srd_divination_question",
            state,
            caster_state="hero",
            spell="commune",
            question="Too late?",
            answer_words=["no"],
            answer_is_correct=True,
        )


def test_question_answers_require_actual_token_boundaries_and_source_exceptions():
    state, _, _, _ = _cast("commune")
    for answer in (["yes no"], ["yes\u00a0no"], ["definitely"], ["yes", "probably"]):
        with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
            _run(
                "ask_srd_divination_question",
                state,
                caster_state="hero",
                spell="commune",
                question="Safe?",
                answer_words=answer,
                answer_is_correct=True,
            )
    _run(
        "ask_srd_divination_question",
        state,
        caster_state="hero",
        spell="commune",
        question="Safe?",
        answer_words=["Proceed", "carefully"],
        answer_reason="misleading",
        answer_is_correct=True,
    )
    assert _record(state, "commune")["questions_used"] == 1


def test_sending_actual_reply_block_and_same_clock_window():
    state, _, _, _ = _cast("sending")
    guard = state[f"{SRD}.entities"]["guard"]
    received = guard["received_spell_message"]
    assert received["meaning_understood"] is True
    _run(
        "reply_srd_spell_message",
        state,
        recipient_state="guard",
        sender_state="hero",
        message_id=received["id"],
        reply_words=["Received"],
        block_sending=True,
    )
    assert state[f"{SRD}.entities"]["guard"]["sending_blocks"]["hero"] == 9600
    assert state[f"{SRD}.entities"]["hero"]["received_spell_reply"]["words"] == ["Received"]
    with pytest.raises(RulesetPreconditionRejected, match="already"):
        _run(
            "reply_srd_spell_message",
            state,
            recipient_state="guard",
            sender_state="hero",
            message_id=received["id"],
            reply_words=["Again"],
        )
    state2, _, _, _ = _cast("sending")
    received2 = state2[f"{SRD}.entities"]["guard"]["received_spell_message"]
    _run("advance_srd_lasting_time", state2, elapsed_rounds=1)
    with pytest.raises(RulesetPreconditionRejected, match="window"):
        _run(
            "reply_srd_spell_message",
            state2,
            recipient_state="guard",
            sender_state="hero",
            message_id=received2["id"],
            reply_words=["Late"],
        )


@pytest.mark.parametrize("thickness,delivered", [(0.1, True), (0.99, True), (1, False)])
def test_message_metal_threshold_is_one_foot_not_detects_one_inch(thickness, delivered):
    state, data, params = _case("message")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(thickness, "metal")]
    _run("cast_spell", state, participant_data=data, **params)
    assert (
        state[f"{SRD}.entities"]["guard"].get("received_spell_message") is not None
    ) is delivered


def test_sending_word_cap_cannot_be_forged_with_space_inside_an_item():
    for words in (["word"] * 26, ["one two"]):
        state, data, params = _case("sending", message_words=words)
        before = deepcopy(state)
        with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
            _run("cast_spell", state, participant_data=data, **params)
        assert state == before


def test_telepathic_bond_later_communication_language_distance_plane_and_expiry():
    targets = ["hero", "guard"]
    state, _, _, _ = _cast("telepathic_bond", targets=targets)
    assert _record(state, "telepathic_bond")["members"] == ["hero", "guard"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [10000, 0, 0]
    _run(
        "communicate_srd_telepathic_bond",
        state,
        caster_state="hero",
        sender_state="guard",
        recipient_state="hero",
        communication="The gate is open.",
    )
    assert state[f"{SRD}.entities"]["hero"]["received_bond_communication"]["sender"] == "guard"
    scene["entities"][1]["metadata"]["plane"] = "ethereal"
    with pytest.raises(RulesetPreconditionRejected, match="another plane"):
        _run(
            "communicate_srd_telepathic_bond",
            state,
            caster_state="hero",
            sender_state="guard",
            recipient_state="hero",
            communication="Across planes",
        )
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    with pytest.raises(RulesetPreconditionRejected, match="ended"):
        _run(
            "communicate_srd_telepathic_bond",
            state,
            caster_state="hero",
            sender_state="guard",
            recipient_state="hero",
            communication="Expired",
        )


def test_message_dismissal_closes_only_exact_one_round_reply_source():
    state, data, params, _ = _cast("message")
    first = _record(state, "message")
    first_message = state[f"{SRD}.entities"]["guard"]["received_spell_message"]["id"]
    _run("cast_spell", state, participant_data=data, **params)
    second_message = state[f"{SRD}.entities"]["guard"]["received_spell_message"]["id"]
    _run(
        "dismiss_srd_divination_spell",
        state,
        caster_state="hero",
        spell="message",
        instance_id=first["instance_id"],
    )
    with pytest.raises(RulesetPreconditionRejected, match="already"):
        _run(
            "reply_srd_spell_message",
            state,
            recipient_state="guard",
            sender_state="hero",
            message_id=first_message,
            reply_words=["Late"],
        )
    _run(
        "reply_srd_spell_message",
        state,
        recipient_state="guard",
        sender_state="hero",
        message_id=second_message,
        reply_words=["Received"],
    )
    assert state[f"{SRD}.entities"]["hero"]["received_spell_reply"]["words"] == ["Received"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert not [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "message"]


def test_animal_messenger_uses_admitted_npc_size_and_challenge_rating():
    state, data, params = _case("animal_messenger")
    guard = state[f"{SRD}.entities"]["guard"]
    guard["challenge_rating_eighths"] = 1
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["guard"].get("animal_messenger") is None
    state2, data2, params2 = _case("animal_messenger")
    state2[f"{SRD}.entities"]["guard"]["size_rank"] = 1
    before = deepcopy(state2)
    with pytest.raises(RulesetPreconditionRejected, match="Tiny"):
        _run("cast_spell", state2, participant_data=data2, **params2)
    assert state2 == before


@pytest.mark.parametrize("creator", [None, "fabricate"])
def test_identify_mundane_object_does_not_require_magic_item_properties(creator):
    state, data, params = _case("identify")
    orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    for key in tuple(orb["metadata"]):
        if key.startswith("identify_") or key == "created_by_spell":
            del orb["metadata"][key]
    if creator:
        orb["metadata"]["created_by_spell"] = creator
    begin = _begin(params) | {"divination_subject": "orb", "divination_touch_uninterrupted": True}
    _run("begin_srd_spell_cast", state, participant_data=data, **begin)
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        divination_touch_uninterrupted=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    information = state[f"{SRD}.entities"]["hero"]["last_identify"]["information"]
    assert information == (["Created by: fabricate"] if creator else [])


def test_scrying_sensor_public_observation_uses_invisibility_and_luminous_orb():
    from tests.srd_full.test_srd_spell_wave3_information import _summary

    state, _, _, _ = _cast("scrying")
    record = _record(state, "scrying")

    def observe():
        return _summary(
            _run(
                "observe_srd_information_proxy",
                state,
                observer_state="guard",
                proxy_id=record["proxy_id"],
            )
        )

    assert observe()["Visible"] is False
    state[f"{SRD}.entities"]["guard"]["see_invisible"] = True
    assert observe()["Luminous fist-size orb"] is True
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["blinded"]
    assert observe()["Visible"] is False


def test_repeated_divination_has_separate_spell_counters_and_long_rest_epoch(monkeypatch):
    import sylvia_gm.application.package_runtime as runtime

    actual = runtime.execute_tool
    answers = []

    def observe(*args, **kwargs):
        result = actual(*args, **kwargs)
        if args[0] == "check_srd_repeated_divination":
            answers.append(result.context["_divination_no_answer"])
        return result

    monkeypatch.setattr(runtime, "execute_tool", observe)
    state, _, _ = _case("augury")
    _run("check_srd_repeated_divination", state, caster_state="hero", spell="augury")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["divination_repetitions"]["augury"] = 4
    _run("check_srd_repeated_divination", state, caster_state="hero", spell="augury")
    assert answers[-1] is True
    _run("check_srd_repeated_divination", state, caster_state="hero", spell="divination")
    assert state[f"{SRD}.entities"]["hero"]["divination_repetitions"]["divination"] == 1
    state[f"{SRD}.entities"]["hero"]["last_long_rest_tick"] = 20
    _run("check_srd_repeated_divination", state, caster_state="hero", spell="augury")
    assert answers[-1] is False
    assert state[f"{SRD}.entities"]["hero"]["divination_repetitions"]["divination"] == 0
