"""Public expiry regressions; run only against a normally admitted successor.

The existing information suite already tests the exact Eye deadline, dismissal,
upcasting, incapacitation and nonconcentration animal communication. These paired
controls prove that the expiry hook preserves later/current unrelated sources.
No effect/source injection, forced dice, postcreation state edits or private calls.
"""
from tests.srd_revised_native_fixture import native_utility_case, SRD
from tests.srd_revised_addition_classes import druid_factory
from tests.test_srd_revised_information import _records, _proxy, _summary, _retry, _write


def _advance_to(case, tick):
    remaining = tick - case.lasting()["lasting_tick"]
    assert remaining > 0 and remaining % 2 == 0
    return case.act("advance_srd_lasting_time", elapsed_rounds=int(remaining / 2))


def _cleared_concentration(case, actor="hero"):
    # Assert every real concentration header field retired by the unchanged end
    # owner, including retained schema fields that may be absent in a new save.
    current = case.entities()[actor]
    fields = ("spell_id", "effect_family", "target_state_id", "target_state_ids",
        "strength_target_state_ids", "dexterity_target_state_ids",
        "intelligence_target_state_ids", "wisdom_target_state_ids",
        "charisma_target_state_ids", "selected_skill", "selected_damage_type",
        "started_tick", "expires_tick", "older_caster_state_id",
        "newer_caster_state_id", "sequence", "save_modifier")
    for field in fields:
        assert current.get("concentration_" + field) is None, (field, current.get("concentration_" + field))


def test_eye_recast_survives_old_deadline_then_actual_new_expiry_clears_every_header(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "eye-expiry-recast", spell="arcane_eye", level=9)
    cast, cast_command = case.cast(information_position=[5, 0, 0], information_lighting="bright")
    first = dict(_records(case, "arcane_eye")[0])
    case.act("advance_srd_lasting_time", elapsed_rounds=100)
    replaced, replaced_command = case.cast(information_position=[5, 0, 0], information_lighting="bright")
    current = dict(_records(case, "arcane_eye")[0])
    assert current["instance_id"] != first["instance_id"]
    assert current["started_tick"] > first["started_tick"]
    assert current["expires_tick"] - current["started_tick"] == 1200
    _retry(case, replaced, replaced_command)
    at_old_deadline, old_command = _advance_to(case, first["expires_tick"])
    assert _records(case, "arcane_eye") == [current]
    assert _proxy(case)["active"] is True
    hero = case.entities()["hero"]
    assert hero["concentration_spell_id"] == "arcane_eye"
    assert hero["concentration_started_tick"] == current["started_tick"]
    assert hero["concentration_expires_tick"] == current["expires_tick"]
    _retry(case, at_old_deadline, old_command)
    expired, expiry_command = _advance_to(case, current["expires_tick"])
    assert not _records(case, "arcane_eye")
    eye = next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == current["proxy_id"])
    assert eye["active"] is False
    _cleared_concentration(case)
    case.reject("query_srd_information_spell", {"caster_state": "hero", "spell": "arcane_eye"})
    _retry(case, expired, expiry_command)
    _retry(case, cast, cast_command)
    _write(case, "eye-expiry-recast-evidence.json", first_source=first, replacement_source=current,
        final_eye=eye, actual_tick=case.lasting()["lasting_tick"], all_concentration_headers_cleared=True)


def test_eye_old_deadline_preserves_actual_replacement_information_concentration(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "eye-expiry-detect-replacement", spell="arcane_eye", level=9,
        extra_spells=("detect_magic",))
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    old = dict(_records(case, "arcane_eye")[0])
    _advance_to(case, old["expires_tick"] - 4)
    replacement, replacement_command = case.cast(spell="detect_magic", slot_resource="spell_slot_1", material_item_ids=[])
    current = dict(_records(case, "detect_magic")[0])
    assert not _records(case, "arcane_eye") and current["expires_tick"] > old["expires_tick"]
    _retry(case, replacement, replacement_command)
    advanced, command = _advance_to(case, old["expires_tick"])
    assert _records(case, "detect_magic") == [current]
    hero = case.entities()["hero"]
    assert hero["concentration_spell_id"] == "detect_magic"
    assert hero["concentration_effect_family"] == "information_spell"
    assert hero["concentration_started_tick"] == current["started_tick"]
    assert hero["concentration_expires_tick"] == current["expires_tick"]
    eye = next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == old["proxy_id"])
    assert eye["active"] is False
    query, query_command = case.act("query_srd_information_spell", caster_state="hero", spell="detect_magic")
    _retry(case, query, query_command)
    _retry(case, advanced, command)
    _write(case, "eye-expiry-detect-replacement-evidence.json", old_source=old,
        replacement_source=current, query_card=query.result["result_card"], actual_tick=case.lasting()["lasting_tick"])


def test_eye_expiry_preserves_other_complete_caster_nonconcentration_communication(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "eye-expiry-other-nonconcentration", spell="arcane_eye", level=9,
        additional_characters=(("listener", "speak_with_animals", 1, druid_factory),),
        positions={"hero": [0, 0, 0], "guard": [5, 0, 0], "other": [-5, 0, 0],
            "extra": [20, 10, 0], "listener": [0, 5, 0]},
        entity_changes={"guard": {"creature_type": "beast", "languages": []}},
        scene_entity_changes={"guard": {"metadata": {"participant_ref": "guard", "plane": "material",
            "lighting": "bright", "creature_type": "beast"}}})
    acquisition = case.acquisition["additional_characters"]["listener"]
    assert acquisition["verified_saved_state"] is True
    assert acquisition["requested_spell_sources"]["speak_with_animals"] == ["druidic"]
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    eye_source = dict(_records(case, "arcane_eye")[0])
    _advance_to(case, eye_source["expires_tick"] - 20)
    slots_before = dict(case.entities()["listener"]["resources"])
    communication, communication_command = case.cast(spell="speak_with_animals", caster="listener",
        caster_state="listener", target="listener", spell_grant="druidic", slot_resource="spell_slot_1",
        material_component_available=False, material_item_ids=[])
    source = dict(_records(case, "speak_with_animals")[0])
    assert source["caster"] == source["target"] == "listener" and source["concentration"] is False
    assert source["expires_tick"] - source["started_tick"] == 200
    assert case.entities()["listener"]["resources"] == slots_before | {"spell_slot_1": slots_before["spell_slot_1"] - 1}
    _retry(case, communication, communication_command)
    expired, expiry_command = _advance_to(case, eye_source["expires_tick"])
    _cleared_concentration(case)
    _cleared_concentration(case, "listener")
    assert not _records(case, "arcane_eye") and _records(case, "speak_with_animals") == [source]
    eye = next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == eye_source["proxy_id"])
    assert eye["active"] is False
    talk, talk_command = case.act("communicate_srd_spell", listener_state="listener", speaker_state="guard")
    assert _summary(talk)["Understands"] is True and _summary(talk)["Can reply"] is True
    _retry(case, talk, talk_command)
    _retry(case, expired, expiry_command)
    _write(case, "eye-expiry-other-nonconcentration-evidence.json", eye_source=eye_source,
        preserved_source=source, complete_listener_acquisition=acquisition, communication=_summary(talk))


def test_eye_actual_fractional_group_clock_start_and_exact_saved_deadline(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "eye-expiry-fractional-group-clock", spell="arcane_eye", level=9)
    started, started_command = case.act("start_group_combat_encounter", combatants=["hero", "guard", "other", "extra"])
    case.act("begin_combat_turn")
    order = case.snapshot().state[SRD + ".combat"]["order"]
    assert set(order) == {"hero", "guard", "other", "extra"}
    # One actual unforced turn transition among four combatants is half a tick.
    # End this ordinary practice encounter while no turn is active, so ending it
    # does not silently add a second turn and erase the fractional starting tick.
    case.act("end_combat_turn")
    case.act("advance_group_combat_encounter_turn")
    fractional_start = case.lasting()["lasting_tick"]
    assert fractional_start % 1 == .5
    ended, ended_command = case.act("end_combat_encounter", end_reason="fighting_stopped")
    assert case.snapshot().state[SRD + ".combat"]["encounter_active"] is False
    assert case.lasting()["lasting_tick"] == fractional_start
    _retry(case, ended, ended_command)
    cast, cast_command = case.cast(information_position=[5, 0, 0], information_lighting="bright")
    source = dict(_records(case, "arcane_eye")[0])
    assert source["started_tick"] == fractional_start
    assert source["expires_tick"] == fractional_start + 1200
    assert case.entities()["hero"]["concentration_started_tick"] == fractional_start
    assert case.entities()["hero"]["concentration_expires_tick"] == source["expires_tick"]
    case.act("advance_srd_lasting_time", elapsed_rounds=599)
    assert case.lasting()["lasting_tick"] == source["expires_tick"] - 2
    assert _proxy(case)["active"] is True
    query, query_command = case.act("query_srd_information_spell", caster_state="hero", spell="arcane_eye")
    _retry(case, query, query_command)
    expired, expiry_command = case.act("advance_srd_lasting_time", elapsed_rounds=1)
    assert case.lasting()["lasting_tick"] == source["expires_tick"]
    assert not _records(case, "arcane_eye")
    _cleared_concentration(case)
    eye = next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == source["proxy_id"])
    assert eye["active"] is False
    _retry(case, expired, expiry_command)
    _retry(case, cast, cast_command)
    _retry(case, started, started_command)
    _write(case, "eye-expiry-fractional-group-clock-evidence.json", actual_initiative_order=order,
        source=source, actual_tick=case.lasting()["lasting_tick"], final_eye=eye)
