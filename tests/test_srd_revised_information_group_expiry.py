"""Real bounded group-clock expiry; genuine paid sources, public turns and retry.

Preserve the one-hour duration. Advance599 rounds outside combat, then use four
actual turns among four real combatants to cross the last two ticks. Initiative
is unforced, no saved row/deadline is injected or shortened, and no private tool
is called. Native execution remains parent-owned and pending.
"""
from tests.srd_revised_native_fixture import native_utility_case, SRD
from tests.test_srd_revised_information import _records, _proxy, _retry, _write
from tests.test_srd_revised_information_expiry import _advance_to, _cleared_concentration


def _start_group(case):
    started, command = case.act("start_group_combat_encounter", combatants=["hero", "guard", "other", "extra"])
    case.act("begin_combat_turn")
    combat = case.snapshot().state[SRD + ".combat"]
    assert set(combat["order"]) == {"hero", "guard", "other", "extra"}
    assert combat["active_actor"] in combat["order"] and combat["turn_active"] is True
    return started, command


def _group_step(case):
    before = case.snapshot().state[SRD + ".combat"]
    assert before["turn_active"] is True and before["active_actor"] in before["order"]
    commands = []
    for tool in ("end_combat_turn", "advance_group_combat_encounter_turn", "begin_combat_turn"):
        receipt, command = case.act(tool)
        commands.append((receipt, command))
    return commands


def test_eye_real_one_hour_expiry_in_four_unforced_group_turns(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "eye-expiry-group-four-turns", spell="arcane_eye", level=9)
    cast, cast_command = case.cast(information_position=[5, 0, 0], information_lighting="bright")
    source = dict(_records(case, "arcane_eye")[0])
    assert source["started_tick"] == 0 and source["expires_tick"] == 1200
    elapsed, elapsed_command = case.act("advance_srd_lasting_time", elapsed_rounds=599)
    assert case.lasting()["lasting_tick"] == 1198 and _proxy(case)["active"] is True
    paid_resources = dict(case.entities()["hero"]["resources"])
    _retry(case, elapsed, elapsed_command)
    started, started_command = _start_group(case)
    assert case.lasting()["lasting_tick"] == 1198
    order = case.snapshot().state[SRD + ".combat"]["order"]
    observed_ticks = [case.lasting()["lasting_tick"]]
    for expected in (1198.5, 1199, 1199.5):
        _group_step(case)
        observed_ticks.append(case.lasting()["lasting_tick"])
        assert case.lasting()["lasting_tick"] == expected
        assert _records(case, "arcane_eye") == [source] and _proxy(case)["active"] is True
        assert case.entities()["hero"]["concentration_spell_id"] == "arcane_eye"
    final_step = _group_step(case)
    observed_ticks.append(case.lasting()["lasting_tick"])
    assert case.lasting()["lasting_tick"] == source["expires_tick"] == 1200
    assert not _records(case, "arcane_eye")
    _cleared_concentration(case)
    eye = next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == source["proxy_id"])
    assert eye["active"] is False and case.entities()["hero"]["resources"] == paid_resources
    case.reject("query_srd_information_spell", {"caster_state": "hero", "spell": "arcane_eye"})
    for receipt, command in [*final_step, (cast, cast_command), (started, started_command)]:
        _retry(case, receipt, command)
    _write(case, "eye-expiry-group-four-turns-evidence.json", source=source, initiative_order=order,
        observed_ticks=observed_ticks, actual_turn_transitions=4,
        unchanged_paid_resources=paid_resources, final_eye=eye, concentration_cleared=True)


def test_eye_group_old_deadline_preserves_actual_later_recast(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "eye-expiry-group-recast", spell="arcane_eye", level=9)
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    old = dict(_records(case, "arcane_eye")[0])
    case.act("advance_srd_lasting_time", elapsed_rounds=100)
    replacement, replacement_command = case.cast(information_position=[5, 0, 0], information_lighting="bright")
    current = dict(_records(case, "arcane_eye")[0])
    assert current["instance_id"] != old["instance_id"] and current["started_tick"] == 200
    assert current["expires_tick"] == 1400 and old["expires_tick"] == 1200
    _advance_to(case, old["expires_tick"] - 2)
    started, started_command = _start_group(case)
    paid_resources = dict(case.entities()["hero"]["resources"])
    last = []
    for _ in range(4):
        last = _group_step(case)
    assert case.lasting()["lasting_tick"] == old["expires_tick"]
    assert _records(case, "arcane_eye") == [current] and _proxy(case)["active"] is True
    hero = case.entities()["hero"]
    assert hero["concentration_spell_id"] == "arcane_eye"
    assert hero["concentration_started_tick"] == current["started_tick"]
    assert hero["concentration_expires_tick"] == current["expires_tick"]
    assert hero["resources"] == paid_resources
    for receipt, command in [*last, (replacement, replacement_command), (started, started_command)]:
        _retry(case, receipt, command)
    query, query_command = case.act("query_srd_information_spell", caster_state="hero", spell="arcane_eye")
    _retry(case, query, query_command)
    _write(case, "eye-expiry-group-recast-evidence.json", old_source=old, preserved_source=current,
        actual_tick=case.lasting()["lasting_tick"], query_card=query.result["result_card"],
        unchanged_paid_resources=paid_resources)


def test_eye_group_callback_preserves_other_information_concentration(tmp_path, native_utility_case):
    case = native_utility_case(tmp_path, "eye-expiry-group-other-information", spell="arcane_eye", level=9,
        extra_spells=("detect_magic",))
    case.cast(information_position=[5, 0, 0], information_lighting="bright")
    old = dict(_records(case, "arcane_eye")[0])
    _advance_to(case, old["expires_tick"] - 4)
    replacement, replacement_command = case.cast(spell="detect_magic", slot_resource="spell_slot_1", material_item_ids=[])
    current = dict(_records(case, "detect_magic")[0])
    assert not _records(case, "arcane_eye") and current["expires_tick"] > old["expires_tick"]
    _advance_to(case, old["expires_tick"] - 2)
    started, started_command = _start_group(case)
    paid_resources = dict(case.entities()["hero"]["resources"])
    last = []
    for _ in range(4):
        last = _group_step(case)
    assert case.lasting()["lasting_tick"] == old["expires_tick"]
    assert _records(case, "detect_magic") == [current]
    assert case.entities()["hero"]["concentration_spell_id"] == "detect_magic"
    assert case.entities()["hero"]["concentration_started_tick"] == current["started_tick"]
    assert case.entities()["hero"]["concentration_expires_tick"] == current["expires_tick"]
    assert case.entities()["hero"]["resources"] == paid_resources
    eye = next(e for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == old["proxy_id"])
    assert eye["active"] is False
    for receipt, command in [*last, (replacement, replacement_command), (started, started_command)]:
        _retry(case, receipt, command)
    query, query_command = case.act("query_srd_information_spell", caster_state="hero", spell="detect_magic")
    _retry(case, query, query_command)
    _write(case, "eye-expiry-group-other-information-evidence.json", old_source=old,
        preserved_source=current, actual_tick=case.lasting()["lasting_tick"], query_card=query.result["result_card"],
        unchanged_paid_resources=paid_resources)
