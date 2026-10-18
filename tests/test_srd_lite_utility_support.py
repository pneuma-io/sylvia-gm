"""Actual admitted paid Darkvision, receiving sight, preparation and saved lifecycle."""

import json
from dataclasses import replace
import pytest
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import IdempotencyConflict, WrongOwner, MechanicsRejected
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics, _run

from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID


def _sight(case, target="hero", distance=40, lighting="darkness"):
    receipt, command = case.act("resolve_srd_sight", observer_state=target,
        distance=distance, lighting=lighting, obstruction="clear")
    return receipt, command


def _context(receipt):
    return receipt.result["context"]


def test_darkvision_receiving_creature_actual_death_removes_saved_grant(tmp_path):
    case = Case(tmp_path / "darkvision-death.db", "darkvision",
        entity_changes={"guard": {"hp": 1, "max_hp": 1}})
    try:
        parameters = case.parameters | {"target": "guard", "targets": ["guard"],
            "target_distance": 5, "spell_grant": "wizard"}
        receipt, command = case.act("cast_spell_outside_combat", **parameters)
        mcp = StatelessMcpMechanics(case.owner)
        original_tool = next(t for t in mcp.list_tools("lite") if t.name == "cast_spell_outside_combat")
        assert len(case.lasting()["lite_utility_effects"]) == 1
        assert case.lasting()["lite_utility_effects"][0]["target"] == "guard"
        case.start()
        attacks = []
        for _ in range(40):
            attack, _ = case.attack()
            attacks.append(thaw_json_value(attack.result["result_card"]))
            if case.entities()["guard"]["is_alive"] is False:
                break
            case.next_turn()
            if case.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] != "hero":
                case.next_turn()
        assert case.entities()["guard"]["is_alive"] is False
        assert not case.lasting()["lite_utility_effects"]
        saved = case.snapshot()
        assert case.execute(command) == receipt
        assert _run(mcp._execute_action(command, original_tool)) == receipt
        assert case.snapshot() == saved
        with pytest.raises(IdempotencyConflict):
            case.execute(replace(command, parameters=command.parameters | {"slot_resource": "spell_slot_3"}))
        with pytest.raises(WrongOwner):
            case.execute(replace(command, owner_id="different-owner"))
        with pytest.raises(IdempotencyConflict):
            _run(mcp._execute_action(replace(command, parameters=command.parameters | {"slot_resource": "spell_slot_3"}), original_tool))
        with pytest.raises(WrongOwner):
            _run(mcp._execute_action(replace(command, owner_id="different-owner"), original_tool))
        with pytest.raises(MechanicsRejected, match="pinned package") as invalid_pin:
            _run(mcp._execute_action(command, replace(original_tool, package_sha256="0" * 64)))
        assert invalid_pin.value.code == "mcp_package_identity_mismatch"
        assert case.snapshot() == saved
        case.reject("cast_spell_outside_combat", parameters, match="canonical catalog")
        assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        (tmp_path / "darkvision-death-observations.json").write_text(json.dumps(
            {"real_attack_cards": attacks, "source_retired_after_actual_death": True}, indent=2))
    finally:
        archive_closed_case(case)


def test_darkvision_paid_pending_cast_real_sight_range_replay_and_unprepare(tmp_path):
    case = Case(tmp_path / "darkvision-paid.db", "darkvision", lighting="darkness",
        participant_changes={"wizard_spellbook": ["darkvision", "false_life"]})
    try:
        receipt, _ = _sight(case)
        assert _context(receipt)["_sight_can_see"] is False
        initial = case.snapshot()
        slots = dict(case.entities()["hero"]["resources"])
        parameters = case.parameters | {"target": "hero", "targets": ["hero"],
            "target_distance": 0, "spell_grant": "wizard"}
        for tool in ("cast_spell_outside_combat", "begin_srd_spell_action"):
            case.reject(tool, parameters | {"target": "extra", "targets": ["extra"], "target_distance": 0}, match="touch|clear")
            case.reject(tool, parameters | {"targets": ["hero", "guard"]}, match="exactly one")
            case.reject(tool, parameters | {"target_willing": False}, match="willing")
            case.reject(tool, parameters | {"verbal_component_available": False}, match="[Vv]erbal")
        case.start()
        case.act("spend_combat_movement", distance=10, difficult_terrain=False)
        begun_receipt, begun_command = case.act("begin_srd_spell_action", **parameters)
        begun = case.snapshot()
        assert begun.state[VARIANT_ID + ".combat"]["action_available"] is False
        assert begun.state[VARIANT_ID + ".combat"]["movement_remaining"] == 20
        assert case.entities()["hero"]["bonus_action_available"] is True
        assert case.entities()["hero"]["resources"] == slots
        assert not case.lasting().get("lite_utility_effects")
        pending = next(iter(case.lasting()["pending_srd_actions"]))
        case.reopen()
        assert case.execute(begun_command) == begun_receipt
        assert case.snapshot() == begun
        cast_receipt, cast_command = case.act("complete_srd_pending_action", pending_action_id=pending)
        saved = case.snapshot()
        expected = slots | {"spell_slot_2": slots["spell_slot_2"] - 1}
        assert case.entities()["hero"]["resources"] == expected
        assert case.entities()["hero"].get("concentration_spell_id") is None
        rows = case.lasting()["lite_utility_effects"]
        assert len(rows) == 1 and rows[0]["target"] == "hero"
        assert rows[0]["caster"] == "hero" and rows[0]["preparation_source"] == "wizard"
        assert rows[0]["cast_id"] and rows[0]["until_long_rest"] is True
        assert cast_receipt.result["result_card"]["title"] == "Darkvision"
        assert "40" in json.dumps(thaw_json_value(cast_receipt.result["result_card"]))
        assert "Long Rest" in json.dumps(thaw_json_value(cast_receipt.result["result_card"]))
        assert "Lasts 0 rounds" not in json.dumps(thaw_json_value(cast_receipt.result["result_card"]))
        assert not any(r.get("spell") == "darkvision" and r.get("kind") == "timed_senses" for r in case.lasting()["turn_effects"])
        for command, receipt in ((begun_command, begun_receipt), (cast_command, cast_receipt)):
            assert case.execute(command) == receipt
            assert case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        for distance, lighting, visible, effective in (
            (40, "darkness", True, "dim"), (41, "darkness", False, "darkness"),
            (40, "magical_darkness", False, "magical_darkness"), (40, "dim", True, "bright"),
        ):
            sight, _ = _sight(case, distance=distance, lighting=lighting)
            assert _context(sight)["_sight_can_see"] is visible
            assert _context(sight)["_sight_light"] == effective
        # Actual preparation removes this caster's grant, without an imported private reducer.
        case.act("end_combat_turn")
        case.act("end_combat_encounter", end_reason="fighting_stopped")
        case.act("begin_srd_rest", target_state="hero", kind="short", activity="light")
        case.act("advance_srd_lasting_time", elapsed_rounds=600)
        case.act("complete_srd_rest", target_state="hero")
        assert len(case.lasting()["lite_utility_effects"]) == 1
        remove_receipt, remove_command = case.act("prepare_srd_spells", caster="hero", caster_state="hero", source="wizard", spells=["false_life"], spellbook_available=True)
        removed = case.snapshot()
        assert case.lasting()["lite_utility_effects"] == []
        sight, _ = _sight(case)
        assert _context(sight)["_sight_can_see"] is False
        later = case.snapshot()
        assert case.execute(cast_command) == cast_receipt
        assert case.execute(remove_command) == remove_receipt
        assert case.snapshot() == later
        case.reopen()
        assert case.snapshot() == later
        assert case.snapshot().package_lock == initial.package_lock
        (case.path.parent / "darkvision-observations.json").write_text(json.dumps({
            "cast_card": thaw_json_value(cast_receipt.result["result_card"]), "source": thaw_json_value(rows[0]),
            "slots_before": slots, "slots_after_cast": expected,
            "public_receipts": [begun_command.action_id, cast_command.action_id, remove_command.action_id],
            "removed_revision": removed.revision,
        }, indent=2), encoding="utf-8")
    finally:
        archive_closed_case(case)


def test_darkvision_upcast_nonstacking_native_sense_preserved_and_genuine_rest(tmp_path):
    case = Case(tmp_path / "darkvision-rest.db", "darkvision", entity_changes={"other": {"darkvision_feet": 80}})
    try:
        native, _ = _sight(case, target="other", distance=80)
        assert _context(native)["_sight_can_see"] is True
        first, first_command = case.cast(target="hero", spell_grant="wizard", slot_resource="spell_slot_4")
        first_row = dict(case.lasting()["lite_utility_effects"][0])
        second, second_command = case.cast(target="hero", spell_grant="wizard", slot_resource="spell_slot_2")
        second_row = dict(case.lasting()["lite_utility_effects"][0])
        assert second_row["cast_id"] != first_row["cast_id"]
        assert len(case.lasting()["lite_utility_effects"]) == 1
        assert first_row["spell_level"] == 4 and second_row["spell_level"] == 2
        at41, _ = _sight(case, distance=41)
        assert _context(at41)["_sight_can_see"] is False
        case.act("begin_srd_rest", target_state="hero", kind="long", activity="sleep")
        case.act("advance_srd_lasting_time", elapsed_rounds=4800)
        assert case.lasting()["lite_utility_effects"] == [second_row]
        rest, rest_command = case.act("complete_srd_rest", target_state="hero")
        rested = case.snapshot()
        assert case.entities()["hero"]["rest_phase"] == "complete"
        assert case.lasting()["lite_utility_effects"] == []
        for receipt, command in ((first, first_command), (second, second_command), (rest, rest_command)):
            assert case.execute(command) == receipt
            assert case.snapshot() == rested
        case.reopen()
        assert case.snapshot() == rested
        sight, _ = _sight(case)
        assert _context(sight)["_sight_can_see"] is False
        native, _ = _sight(case, target="other", distance=80)
        assert _context(native)["_sight_can_see"] is True
        assert case.entities()["other"]["darkvision_feet"] == 80
    finally:
        archive_closed_case(case)


def _animals_case(path, source="druid"):
    return Case(path, "speak_with_animals", casting_source=source, entity_changes={
        "hero": {"languages": ["common"]},
        "guard": {"creature_type": "beast", "languages": ["beast"]},
        "other": {"creature_type": "beast", "languages": ["beast"], "creature_tags": ["shapechanger"]},
        "extra": {"creature_type": "humanoid", "languages": ["unknown"]},
    })


def _speech(case, speaker="guard", **choices):
    return case.act("communicate_srd_spell", listener_state="hero", speaker_state=speaker,
        **({"channel": "speech", "perceived": True, "language": "beast", "purpose": "conversation"} | choices))


def test_animals_exploration_ritual_actual_beast_dialogue_nonstacking_and_unprepare(tmp_path):
    case = _animals_case(tmp_path / "animals-ritual.db")
    try:
        speech = {"listener_state": "hero", "speaker_state": "guard", "language": "beast"}
        case.reject("communicate_srd_spell", speech, match="cannot understand")
        slots = dict(case.entities()["hero"]["resources"])
        params = case.parameters | {"target": "hero", "targets": ["hero"], "target_distance": 0, "spell_grant": "druid"}
        case.reject("cast_spell_outside_combat", params | {"target": "guard", "targets": ["guard"]}, match="self|caster")
        case.reject("cast_spell_outside_combat", params | {"somatic_component_available": False}, match="[Ss]omatic")
        case.act("begin_srd_rest", target_state="hero", kind="long", activity="sleep")
        case.act("advance_srd_lasting_time", elapsed_rounds=4800)
        case.act("complete_srd_rest", target_state="hero")
        initial_tick = case.lasting()["lasting_tick"]
        cast, cast_command = case.act("cast_spell_outside_combat", **(params | {"casting_mode": "ritual", "slot_resource": "ritual"}))
        assert case.entities()["hero"]["resources"] == slots
        assert case.lasting()["lasting_tick"] == initial_tick
        row = case.lasting()["lite_utility_effects"][0]
        assert row["spell"] == "speak_with_animals" and row["target"] == row["caster"] == "hero"
        assert row["preparation_source"] == "druid" and row["cast_id"]
        assert case.entities()["hero"].get("concentration_spell_id") is None
        assert "Long Rest" in json.dumps(thaw_json_value(cast.result["result_card"]))
        spoken, _ = _speech(case)
        assert _context(spoken)["_language_understands"] is True
        assert _context(spoken)["_language_can_reply"] is True
        for choices in ({"perceived": False}, {"channel": "signs"}, {"speaker_state": "extra"}, {"speaker_state": "other"}):
            case.reject("communicate_srd_spell", speech | choices, match="cannot understand")
        case.reject("communicate_srd_spell", speech | {"purpose": "nearby_monsters"}, match="cannot guarantee")
        influenced, _ = _speech(case, purpose="influence", actor="hero", influence_skill="persuasion", target_number=0)
        assert _context(influenced)["_language_influence_total"] is not None
        assert case.entities()["hero"]["resources"] == slots
        second, second_command = case.act("cast_spell_outside_combat", **(params | {"slot_resource": "spell_slot_3"}))
        assert len(case.lasting()["lite_utility_effects"]) == 1
        assert case.lasting()["lite_utility_effects"][0]["spell_level"] == 3
        assert case.entities()["hero"]["resources"] == slots
        saved = case.snapshot()
        case.reopen()
        assert case.snapshot() == saved
        for command, receipt in ((cast_command, cast), (second_command, second)):
            assert case.execute(command) == receipt and case.snapshot() == saved
        case.reject("prepare_srd_spells", {"caster": "hero", "caster_state": "hero", "source": "druid", "spells": ["cure_wounds"], "unprepare_only": True}, match="cannot learn or replace")
        remove, remove_command = case.act("prepare_srd_spells", caster="hero", caster_state="hero", source="druid", spells=[], unprepare_only=True)
        assert case.lasting()["lite_utility_effects"] == []
        assert "speak_with_animals" not in case.entities()["hero"]["prepared_spells"]
        case.reject("cast_spell_outside_combat", params | {"casting_mode": "ritual", "slot_resource": "ritual"}, match="prepared|grant|source|available")
        case.reject("communicate_srd_spell", speech, match="cannot understand")
        after = case.snapshot()
        assert case.execute(remove_command) == remove and case.snapshot() == after
        assert case.execute(second_command) == second and case.snapshot() == after
        (case.path.parent / "animals-ritual-observations.json").write_text(json.dumps({
            "cast_card": thaw_json_value(cast.result["result_card"]), "source": thaw_json_value(row),
            "speech": thaw_json_value(spoken.result), "influence": thaw_json_value(influenced.result),
            "slots": slots, "public_actions": [cast_command.action_id, second_command.action_id, remove_command.action_id],
        }, indent=2), encoding="utf-8")
    finally:
        archive_closed_case(case)


def test_animals_combat_paid_pending_cast_speech_action_and_real_rest(tmp_path):
    case = _animals_case(tmp_path / "animals-paid.db", source="bard")
    try:
        params = case.parameters | {"target": "hero", "targets": ["hero"], "target_distance": 0, "spell_grant": "bard"}
        slots = dict(case.entities()["hero"]["resources"])
        case.start()
        case.reject("begin_srd_spell_action", params | {"casting_mode": "ritual", "slot_resource": "ritual"}, match="free exploration")
        begin, begin_command = case.act("begin_srd_spell_action", **params)
        assert case.entities()["hero"]["resources"] == slots
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
        pending = next(iter(case.lasting()["pending_srd_actions"]))
        cast, cast_command = case.act("complete_srd_pending_action", pending_action_id=pending)
        assert case.entities()["hero"]["resources"] == slots | {"spell_slot_1": slots["spell_slot_1"] - 1}
        assert case.entities()["hero"]["bonus_action_available"] is True
        spoken, _ = _speech(case)
        assert _context(spoken)["_language_can_reply"] is True
        case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard", "language": "beast", "purpose": "influence", "actor": "hero"}, match="[Aa]ction|active")
        case.act("end_combat_turn")
        case.act("end_combat_encounter", end_reason="fighting_stopped")
        case.act("begin_srd_rest", target_state="hero", kind="long", activity="sleep")
        case.act("advance_srd_lasting_time", elapsed_rounds=4800)
        assert len(case.lasting()["lite_utility_effects"]) == 1
        rest, rest_command = case.act("complete_srd_rest", target_state="hero")
        assert case.lasting()["lite_utility_effects"] == []
        rested = case.snapshot()
        case.reopen()
        assert case.snapshot() == rested
        for command, receipt in ((begin_command, begin), (cast_command, cast), (rest_command, rest)):
            assert case.execute(command) == receipt and case.snapshot() == rested
        case.reject("communicate_srd_spell", {"listener_state": "hero", "speaker_state": "guard", "language": "beast"}, match="cannot understand")
    finally:
        archive_closed_case(case)
