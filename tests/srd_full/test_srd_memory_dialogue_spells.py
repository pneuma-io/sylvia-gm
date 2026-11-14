"""Named memory, corpse and truth spells prove authentic later source consumers."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_memory_spells import PROFILES, validate_profiles

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_illusion_removals import _prepare
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    fixture = os.environ.get("FC14_MEMORY_RUNTIME_ROOT")
    if fixture:
        monkeypatch.setattr(budget, "ROOT", Path(fixture))
    budget._mechanic.cache_clear()
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: budget.ROOT)
    bundled_packages.bundled_srd_archive.cache_clear()
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(
        cards,
        "_spell_definitions",
        lambda: budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())[
            "definitions"
        ]["spells"],
    )
    yield
    budget._mechanic.cache_clear()
    bundled_packages.bundled_srd_archive.cache_clear()


def _case(spell="modify_memory", *, tick=200, slot=None):
    state, data, params = _information_case(spell)
    lasting = state[f"{SRD}.state"]
    lasting["lasting_tick"] = tick
    scene = lasting["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["entities"].append(_entity("other", [40, 20, 0], participant_ref="other"))
    scene["zones"].append(
        {
            "id": "memory-ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for actor in ("hero", "guard", "other"):
        saved = state[f"{SRD}.entities"][actor]
        saved.update(hp=100, max_hp=100, temp_hp=0, languages=["common"])
        saved.setdefault("saving_throw_modifiers", dict.fromkeys(data["abilities"], 0))
        saved["saving_throw_modifiers"]["wisdom"] = -100
        saved["saving_throw_modifiers"]["charisma"] = -100
    params.update(
        memory_event_id="door",
        dialogue_position=[10, 0, 0],
        slot_resource=f"spell_slot_{slot or PROFILES[spell]['base_level']}",
    )
    budget._run(
        "record_srd_life_event",
        state,
        target_state="guard",
        event_id="door",
        start_tick=0,
        end_tick=100,
        description="The door was blue.",
    )
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    result = budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["result_card"] is not None
    return result


def _source(state, spell):
    return next(
        (key, record)
        for key, record in state[f"{SRD}.state"]["timed_condition_sources"].items()
        if record.get("installed") and record.get("spell_id") == spell
    )


def _memory_cast():
    state, data, params = _case()
    _cast(state, data, params)
    source_id, _ = _source(state, "modify_memory")
    return state, data, params, source_id


def _describe(state, source_id, language="common"):
    return budget._run(
        "describe_srd_modified_memory",
        state,
        caster_state="hero",
        target_state="guard",
        source_id=source_id,
        description="The door was red.",
        language=language,
    )


def _reject(state, name, **params):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run(name, state, **params)
    assert state == before


def test_actual_memory_description_is_deferred_until_exact_spell_ends():
    state, _, _, source_id = _memory_cast()
    guard = state[f"{SRD}.entities"]["guard"]
    assert {"charmed", "incapacitated"} <= set(guard["conditions"])
    assert guard["memory_unaware"] is True
    _describe(state, source_id)
    assert (
        state[f"{SRD}.entities"]["guard"]["life_events"]["door"]["perceived_description"]
        == "The door was blue."
    )
    budget._run("end_concentration", state, caster_state="hero")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["life_events"]["door"]["perceived_description"] == "The door was red."
    assert "charmed" not in guard["conditions"] and "incapacitated" not in guard["conditions"]
    assert not guard["memory_unaware"]


@pytest.mark.parametrize("finish", [False, True])
def test_actual_damage_discards_even_a_finished_memory_description(finish):
    state, _, _, source_id = _memory_cast()
    if finish:
        _describe(state, source_id)
    budget._run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1,
        damage_type="force",
        adjustment=0,
        source_state_id="hero",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 99
    assert guard["life_events"]["door"]["perceived_description"] == "The door was blue."
    assert "charmed" not in guard["conditions"]


def test_actual_other_spell_targeting_discards_finished_memory_before_new_effect():
    state, data, params, source_id = _memory_cast()
    _describe(state, source_id)
    params.update(
        spell="magic_missile",
        slot_resource="spell_slot_1",
        material_item_ids=_prepare(state, data, "magic_missile"),
    )
    _cast(state, data, params)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["life_events"]["door"]["perceived_description"] == "The door was blue."
    assert "charmed" not in guard["conditions"]


def test_memory_description_requires_actual_hearing_and_selected_language():
    state, _, _, source_id = _memory_cast()
    _reject(
        state,
        "describe_srd_modified_memory",
        caster_state="hero",
        target_state="guard",
        source_id=source_id,
        description="Red door.",
        language="infernal",
    )
    budget._run("apply_srd_condition", state, target="guard", condition="deafened")
    _reject(
        state,
        "describe_srd_modified_memory",
        caster_state="hero",
        target_state="guard",
        source_id=source_id,
        description="Red door.",
        language="common",
    )


def _corpse_case():
    state, data, params = _case("speak_with_dead")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        is_alive=False,
        hp=0,
        death_tick=100,
        death_was_undead=False,
        body_mouth_present=True,
        soul_willing=False,
    )
    return state, data, params


def test_actual_corpse_questions_are_lifetime_bounded_and_five_only():
    state, data, params = _corpse_case()
    _cast(state, data, params)
    source_id, _ = _source(state, "speak_with_dead")
    for number in range(5):
        budget._run(
            "ask_srd_dead_question",
            state,
            caster_state="hero",
            target_state="guard",
            source_id=source_id,
            question=f"Question {number}",
            answer="The door was blue.",
            language="common",
            answer_kind="truth",
            knowledge_event_ids=["door"],
        )
    _reject(
        state,
        "ask_srd_dead_question",
        caster_state="hero",
        target_state="guard",
        source_id=source_id,
        question="A sixth question?",
        answer="Blue.",
        language="common",
        answer_kind="truth",
        knowledge_event_ids=["door"],
    )
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is False
    assert state[f"{SRD}.entities"]["guard"]["soul_willing"] is False


@pytest.mark.parametrize("invalid", ["mouth", "undead", "recent"])
def test_invalid_corpse_is_unaffected_but_actual_casting_cost_is_spent(invalid):
    state, data, params = _corpse_case()
    guard = state[f"{SRD}.entities"]["guard"]
    guard[
        {
            "mouth": "body_mouth_present",
            "undead": "death_was_undead",
            "recent": "last_speak_dead_tick",
        }[invalid]
    ] = {"mouth": False, "undead": True, "recent": 199}[invalid]
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    assert not any(
        r.get("installed")
        for r in state[f"{SRD}.state"].get("timed_condition_sources", {}).values()
    )


def test_zone_actual_start_turn_records_save_and_prevents_only_deliberate_lie_inside():
    state, data, params = _case("zone_of_truth")
    _cast(state, data, params)
    source_id, source = _source(state, "zone_of_truth")
    assert source["truth_outcomes"] == []
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="guard")
    budget._run("begin_combat_turn", state, actor="guard")
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["truth_outcomes"][0]["event"] == "start"
    assert source["truth_failed_ids"] == ["guard"]
    _reject(
        state,
        "speak_srd_truth_zone",
        target_state="guard",
        statement="A known lie.",
        statement_kind="deliberate_lie",
    )
    budget._run(
        "speak_srd_truth_zone",
        state,
        target_state="guard",
        statement="Perhaps.",
        statement_kind="evasion",
    )
    budget._run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    budget._run(
        "speak_srd_truth_zone",
        state,
        target_state="guard",
        statement="A known lie.",
        statement_kind="deliberate_lie",
    )


def test_profile_variations_cannot_change_fixed_radius_duration_or_level():
    validate_profiles(PROFILES)
    for spell, profile in PROFILES.items():
        with pytest.raises(SystemExit):
            validate_profiles({spell: profile | {"duration_rounds": 1}})


@pytest.mark.parametrize("finish", [False, True])
def test_actual_duration_settles_only_a_completed_memory(finish):
    state, _, _, source_id = _memory_cast()
    if finish:
        _describe(state, source_id)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=10)
    result = budget._run("recall_srd_life_event", state, target_state="guard", event_id="door")
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    rows = {r["label"]: r["value"] for r in result.result["result_card"]["summary"]}
    assert rows["Memory"] == ("The door was red." if finish else "The door was blue.")
    assert not state[f"{SRD}.state"]["has_memory_spells"]


@pytest.mark.parametrize("spell", ["remove_curse", "greater_restoration"])
def test_actual_named_recovery_restores_completed_true_memory(spell):
    state, _, params, source_id = _memory_cast()
    _describe(state, source_id)
    budget._run("end_concentration", state, caster_state="hero")
    _, data, _ = cards._spell_case(spell)
    materials = _prepare(state, data, spell)
    _cast(
        state,
        data,
        params
        | {
            "spell": spell,
            "slot_resource": f"spell_slot_{cards._casting_headers()[spell]['level']}",
            "material_item_ids": materials,
            "recovery_choice": "modified_memory",
        },
    )
    event = state[f"{SRD}.entities"]["guard"]["life_events"]["door"]
    assert not event["modified"] and event["perceived_description"] == event["true_description"]


@pytest.mark.parametrize("mode", ["erase", "recall", "create"])
def test_source_owned_memory_modes_preserve_original_truth(mode):
    state, data, params = _case()
    if mode == "create":
        budget._run(
            "record_srd_life_event",
            state,
            target_state="guard",
            event_id="window",
            start_tick=100,
            end_tick=150,
            description="",
            window_only=True,
        )
        params["memory_event_id"] = "window"
    _cast(state, data, params | {"memory_mode": mode})
    source_id, _ = _source(state, "modify_memory")
    _describe(state, source_id)
    budget._run("end_concentration", state, caster_state="hero")
    event = state[f"{SRD}.entities"]["guard"]["life_events"][params["memory_event_id"]]
    expected = (
        "" if mode == "erase" else "The door was blue." if mode == "recall" else "The door was red."
    )
    assert event["perceived_description"] == expected
    assert event["true_description"] == ("" if mode == "create" else "The door was blue.")


@pytest.mark.parametrize(
    "slot,lookback", [(5, 28800), (6, 201600), (7, 864000), (8, 10512000), (9, 20000000)]
)
def test_actual_slot_lookback_accepts_boundary_event_and_rejects_older(slot, lookback):
    state, data, params = _case(tick=lookback, slot=slot)
    _cast(state, data, params)
    assert _source(state, "modify_memory")[1]["spell_level"] == slot
    if slot < 9:
        old, data, params = _case(tick=lookback + 1, slot=slot)
        _reject(old, "cast_spell_outside_combat", participant_data=data, **params)


def test_memory_unawareness_reaches_actual_sight_and_surrounding_perception():
    state, _, _, _ = _memory_cast()
    result = budget._run(
        "resolve_srd_sight",
        state,
        observer_state="guard",
        distance=5,
        lighting="bright",
        obstruction="clear",
    )
    rows = {r["label"]: r["value"] for r in result.result["result_card"]["summary"]}
    assert rows["Can see"] is False
    _reject(state, "read_srd_passive_perception", target_state="guard", base_passive_perception=15)
    _reject(
        state,
        "d20_test",
        actor_state="guard",
        ability="wisdom",
        kind="ability_check",
        skill="perception",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    )


def test_zone_actual_pass_through_entry_records_once_and_is_visible_to_caster():
    state, data, params = _case("zone_of_truth")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [-10, 0, 0]
    state[f"{SRD}.entities"]["guard"]["speed"] = 50
    _cast(state, data, params)
    source_id, _ = _source(state, "zone_of_truth")
    budget._run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert [r["event"] for r in source["truth_outcomes"]] == ["entry"]
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 40
    result = budget._run(
        "read_srd_truth_zone_outcomes",
        state,
        caster_state="hero",
        source_id=source["truth_area_id"],
    )
    assert result.result["result_card"]["details"][0] == {
        "label": "guard",
        "value": "failure",
        "emphasis": False,
    }
    assert type(source["truth_outcomes"][0]["turn"]) is int
    budget._run(
        "speak_srd_truth_zone",
        state,
        target_state="guard",
        statement="A lie outside.",
        statement_kind="deliberate_lie",
    )


def test_zone_later_success_does_not_end_original_failed_source_restriction():
    state, data, params = _case("zone_of_truth")
    _cast(state, data, params)
    source_id, _ = _source(state, "zone_of_truth")
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="guard")
    budget._run("begin_combat_turn", state, actor="guard")
    budget._run("end_combat_turn", state)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["charisma"] = 100
    state[f"{SRD}.combat"].update(current_actor="guard", turn_active=False)
    budget._run("begin_combat_turn", state, actor="guard")
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert [r["outcome"] for r in source["truth_outcomes"]] == ["failure", "success"]
    _reject(
        state,
        "speak_srd_truth_zone",
        target_state="guard",
        statement="A known lie.",
        statement_kind="deliberate_lie",
    )


def test_zone_actual_caster_outcomes_are_readable_after_more_than_one_card_page():
    state, data, params = _case("zone_of_truth")
    _cast(state, data, params)
    source_id, _ = _source(state, "zone_of_truth")
    for _ in range(33):
        state[f"{SRD}.combat"].update(
            encounter_active=True, turn_active=False, current_actor="guard"
        )
        budget._run("begin_combat_turn", state, actor="guard")
        budget._run("end_combat_turn", state)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert len(source["truth_outcomes"]) == 33
    first = budget._run(
        "read_srd_truth_zone_outcomes",
        state,
        caster_state="hero",
        source_id=source["truth_area_id"],
    )
    last = budget._run(
        "read_srd_truth_zone_outcomes",
        state,
        caster_state="hero",
        source_id=source["truth_area_id"],
        outcome_offset=32,
    )
    assert len(first.result["result_card"]["details"]) == 128
    assert len(last.result["result_card"]["details"]) == 4


def test_expired_truth_zone_preserves_known_outcomes_when_timed_slot_is_reused():
    state, data, params = _case("zone_of_truth")
    _cast(state, data, params)
    timed_id, source = _source(state, "zone_of_truth")
    area_id = source["truth_area_id"]
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="guard")
    budget._run("begin_combat_turn", state, actor="guard")
    budget._run("end_combat_turn", state)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=100, witness_state_ids=["hero"])
    old = state[f"{SRD}.state"]["persistent_spell_areas"][area_id]
    assert old["active"] is False and old["truth_outcomes"][0]["outcome"] == "failure"
    _, data, _ = cards._spell_case("modify_memory")
    params.update(
        spell="modify_memory",
        slot_resource="spell_slot_5",
        material_item_ids=_prepare(state, data, "modify_memory"),
    )
    _cast(state, data, params)
    new_id, new = _source(state, "modify_memory")
    assert new_id == timed_id and new["cast_id"] != area_id
    result = budget._run(
        "read_srd_truth_zone_outcomes", state, caster_state="hero", source_id=area_id
    )
    assert result.result["result_card"]["details"][0] == {
        "label": "guard",
        "value": "failure",
        "emphasis": False,
    }


def test_invalid_memory_corpse_spends_cast_cost_without_rolling_or_altering_memory():
    state, data, params = _case()
    state[f"{SRD}.entities"]["guard"].update(is_alive=False, hp=0, death_tick=100)
    result = _cast(state, data, params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    assert not any(
        r.get("installed")
        for r in state[f"{SRD}.state"].get("timed_condition_sources", {}).values()
    )
    assert all(r["label"] != "Save roll" for r in result.result["result_card"]["details"])


def test_self_spell_does_not_target_the_unrelated_memory_subject():
    state, data, params, source_id = _memory_cast()
    _describe(state, source_id)
    _cast(
        state,
        data,
        params
        | {
            "spell": "false_life",
            "slot_resource": "spell_slot_1",
            "material_item_ids": _prepare(state, data, "false_life"),
        },
    )
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["installed"] and not source["memory_aborted"]


def test_memory_fighting_advantage_uses_actual_saved_hostile_pair():
    state, data, params = _case()
    budget._run("record_srd_hostile_actor_pair", state, actor_state="hero", target_state="guard")
    result = _cast(state, data, params)
    rows = {r["label"]: r["value"] for r in result.result["result_card"]["details"]}
    assert rows["Save roll"] == 14  # The actual first two seeded dice are 13 and 14.


def test_memory_retirement_preserves_independent_incapacitation():
    state, data, params = _case()
    budget._run("apply_srd_condition", state, target="guard", condition="incapacitated")
    _cast(state, data, params)
    source_id, _ = _source(state, "modify_memory")
    _describe(state, source_id)
    budget._run("end_concentration", state, caster_state="hero")
    guard = state[f"{SRD}.entities"]["guard"]
    assert "incapacitated" in guard["conditions"]
    assert "charmed" not in guard["conditions"] and not guard["memory_unaware"]


def test_actual_mind_blank_prevents_memory_charm_without_refunding_cast():
    state, data, params = _case()
    _cast(
        state,
        data,
        params
        | {
            "spell": "mind_blank",
            "slot_resource": "spell_slot_8",
            "material_item_ids": _prepare(state, data, "mind_blank"),
        },
    )
    params["material_item_ids"] = _prepare(state, data, "modify_memory")
    result = _cast(state, data, params)
    assert not any(
        source.get("dialogue_profile") == "memory" and source.get("installed")
        for source in state[f"{SRD}.state"].get("timed_condition_sources", {}).values()
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    assert result.result["result_card"]["details"][0]["value"] == "Target immune to Charmed."


def test_corpse_failed_targeting_refreshes_actual_ten_day_deadline():
    state, data, params = _corpse_case()
    state[f"{SRD}.entities"]["guard"]["body_mouth_present"] = False
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["guard"]["last_speak_dead_tick"] == 200
    state[f"{SRD}.entities"]["guard"]["body_mouth_present"] = True
    state[f"{SRD}.state"]["lasting_tick"] = 300
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["guard"]["last_speak_dead_tick"] == 300
    assert not state[f"{SRD}.state"].get("timed_condition_sources")
    state[f"{SRD}.state"]["lasting_tick"] = 288300
    _cast(state, data, params)
    assert _source(state, "speak_with_dead")[1]["started_tick"] == 288300


def test_actual_corpse_deadline_rejects_later_question_and_postdeath_event():
    state, data, params = _corpse_case()
    _cast(state, data, params)
    source_id, _ = _source(state, "speak_with_dead")
    _reject(
        state,
        "record_srd_life_event",
        target_state="guard",
        event_id="future",
        start_tick=101,
        end_tick=102,
        description="A post-death event.",
    )
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=100, witness_state_ids=["hero"])
    _reject(
        state,
        "ask_srd_dead_question",
        caster_state="hero",
        target_state="guard",
        source_id=source_id,
        question="A late question?",
        answer="Blue.",
        language="common",
        answer_kind="truth",
        knowledge_event_ids=["door"],
    )


def test_memory_emitter_rebuild_is_semantically_stable():
    import yaml
    from scripts.srd_full.srd_memory_spells import extend_runtime
    from scripts.srd_full.srd_reaction_spells import normalize_pending_spell_guards

    text = normalize_pending_spell_guards((budget.ROOT / "runtime/ruleset.yaml").read_text())
    first = extend_runtime(text)
    second = extend_runtime(first)
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def test_public_memory_cast_reopen_description_end_and_retry_preserve_true_event(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    database = tmp_path / "memory-dialogue.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "modify_memory")
        receipt, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        source_id, source = _source(saved.state, "modify_memory")
        assert source["cast_id"] and source["spell_level"] == 5
        assert owner.execute_action(cast_command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        description, command = _act(
            owner,
            "describe_srd_modified_memory",
            caster_state="hero",
            target_state="guard",
            source_id=source_id,
            description="The door was red.",
            language="common",
        )
        assert description.status is ActionStatus.COMMITTED, description.result
        described = owner.snapshot("casting")
        assert (
            described.state[f"{SRD}.entities"]["guard"]["life_events"]["door"][
                "perceived_description"
            ]
            == "The door was blue."
        )
        assert owner.execute_action(command) == description
        assert owner.snapshot("casting").state == described.state
        ended, end_command = _act(owner, "end_concentration", caster_state="hero")
        assert ended.status is ActionStatus.COMMITTED, ended.result
        settled = owner.snapshot("casting")
        event = settled.state[f"{SRD}.entities"]["guard"]["life_events"]["door"]
        assert event["perceived_description"] == "The door was red."
        assert event["true_description"] == "The door was blue."
        assert owner.execute_action(end_command) == ended
        assert owner.execute_action(cast_command) == receipt
        assert owner.snapshot("casting").state == settled.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == settled.state
        recall, _ = _act(owner, "recall_srd_life_event", target_state="guard", event_id="door")
        assert recall.status is ActionStatus.COMMITTED, recall.result
        rows = {r["label"]: r["value"] for r in recall.result["result_card"]["summary"]}
        assert rows["Memory"] == "The door was red."
