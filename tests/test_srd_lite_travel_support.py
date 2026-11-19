"""Public teleport admission, geometry, paid pending action and persisted movement.

SRD 5.2.1 Misty Step teleports up to 30 feet to an unoccupied space the caster can see.
Combat uses the saved coordinates; exploration uses the established destination facts.
"""
import json
from sylvia_gm.domain.authority import thaw_json_value
from tests.test_srd_lite_foundation import Case, VARIANT_ID
from tests.lite_fixture_archive import archive_closed_case


def _params(case, destination):
    return case.parameters | {"target": "hero", "targets": [], "target_distance": 0,
        "spell_grant": "wizard", "travel_destination": destination}


def _position(case):
    return next(e["position"] for e in case.lasting()["spatial_scene"]["entities"] if e["id"] == "hero")


def test_misty_step_exact_scene_geometry_paid_bonus_pending_retry_and_reopen(tmp_path):
    wall = {"id": "opaque_wall", "map_id": "lite", "volume": {"kind": "box", "min": [-1,25,-1], "max": [15,26,8]},
        "active": True, "movement": "blocking", "visibility": "opaque", "occupies_cells": False,
        "metadata": {"material": "stone"}, "destroyed": {"active": False, "movement": "passable", "visibility": "clear"}}
    case = Case(tmp_path / "misty-paid.db", "misty_step", geometry=[wall])
    try:
        slots = dict(case.entities()["hero"]["resources"])
        case.start()
        tool = "begin_srd_spell_action"
        case.reject(tool, _params(case, [31,0,0]), match="range")
        case.reject(tool, _params(case, [5,0,0]), match="unoccupied")
        case.reject(tool, _params(case, [0,28,0]), match="see")
        case.reject(tool, _params(case, [30,0,0]) | {"verbal_component_available": False}, match="[Vv]erbal")
        case.act("spend_combat_movement", distance=10, difficult_terrain=False)
        hp = case.entities()["hero"]["hp"]
        begin, begin_command = case.act("begin_srd_spell_action", **(_params(case, [30,0,0]) | {"somatic_component_available": False}))
        pending = next(iter(case.lasting()["pending_srd_actions"]))
        assert case.entities()["hero"]["resources"] == slots and _position(case) == [0,0,0]
        assert case.entities()["hero"]["bonus_action_available"] is False
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is True
        assert case.snapshot().state[VARIANT_ID + ".combat"]["movement_remaining"] == 20
        begun = case.snapshot()
        case.reopen()
        assert case.execute(begin_command) == begin and case.snapshot() == begun
        cast, cast_command = case.act("complete_srd_pending_action", pending_action_id=pending)
        assert _position(case) == [30,0,0]
        assert case.entities()["hero"]["resources"] == slots | {"spell_slot_2": slots["spell_slot_2"]-1}
        assert case.snapshot().state[VARIANT_ID + ".combat"]["movement_remaining"] == 20
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is True
        assert case.entities()["hero"]["hp"] == hp
        assert case.entities()["guard"]["reaction_available"] is True
        assert case.entities()["hero"].get("concentration_spell_id") is None
        saved = case.snapshot()
        for command, receipt in ((begin_command, begin), (cast_command, cast)):
            assert case.execute(command) == receipt and case.snapshot() == saved
        case.reopen()
        assert case.snapshot() == saved
        case.act("end_combat_turn")
        case.act("end_combat_encounter", end_reason="fighting_stopped")
        case.act("advance_srd_lasting_time", elapsed_rounds=4800)
        assert _position(case) == [30,0,0]
        (case.path.parent / "misty-paid-observations.json").write_text(json.dumps({
            "cast_card": thaw_json_value(cast.result["result_card"]),
            "position": thaw_json_value(_position(case)), "slots_before": slots,
            "movement_remaining": 20, "public_actions": [begin_command.action_id, cast_command.action_id],
        }, indent=2), encoding="utf-8")
    finally:
        archive_closed_case(case)


def _destination(horizontal, elevation, **facts):
    return {"place": "the far ledge", "current_plane": "material", "plane": "material",
            "visible": True, "unoccupied": True, "horizontal_distance_feet": horizontal,
            "elevation_change_feet": elevation} | facts


def test_misty_step_exploration_destination_range_includes_elevation_without_upcast_gain(tmp_path):
    case = Case(tmp_path / "misty-elevation.db", "misty_step")
    try:
        slots = dict(case.entities()["hero"]["resources"])

        def cast(destination, **choices):
            return case.cast(**(choices | {"target": "hero", "targets": [], "target_distance": 0,
                "spell_grant": "wizard", "travel_exploration_destinations": [destination]}))

        def reject(destination, match, **choices):
            case.reject("cast_spell_outside_combat", case.parameters | choices | {
                "target": "hero", "targets": [], "target_distance": 0, "spell_grant": "wizard",
                "travel_exploration_destinations": [destination]}, match=match)

        # A higher slot adds no range, and elevation counts toward the 30 feet.
        reject(_destination(31, 0), "range", slot_resource="spell_slot_4")
        reject(_destination(20, 23), "range")
        reject(_destination(10, 0, visible=False), "see")
        reject(_destination(10, 0, unoccupied=False), "unoccupied")
        reject(_destination(10, 0, plane="ethereal"), "plane")
        assert case.entities()["hero"]["resources"] == slots
        first, first_command = cast(_destination(30, 0), slot_resource="spell_slot_4")
        second, second_command = cast(_destination(18, -24))
        assert case.entities()["hero"]["resources"] == slots | {
            "spell_slot_4": slots["spell_slot_4"]-1, "spell_slot_2": slots["spell_slot_2"]-1}
        saved = case.snapshot()
        case.reopen()
        for command, receipt in ((first_command, first), (second_command, second)):
            assert case.execute(command) == receipt and case.snapshot() == saved
    finally:
        archive_closed_case(case)
