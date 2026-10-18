"""Finite source cleanup through real group Initiative and a disclosed lethal fall.

The player body/scene are imported fixtures. The 2-HP NPC is genuinely prepared,
casts normally, then suffers a GM-established 20-foot fall. No forced die, private
consumer, clock injection, instantaneous caster-death rule or map-trigger claim.
"""

from tests.lite_fixture_archive import archive_closed_case
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_lite_foundation import VARIANT_ID


def _combat(case):
    return case.snapshot().state[VARIANT_ID + ".combat"]


def _frost_sources(case):
    return [row for row in case.lasting().get("turn_effects", [])
            if row.get("spell") == "ray_of_frost" and row.get("target") == "hero"]


def _next_group_turn(case):
    case.act("end_combat_turn")
    advanced = case.act("advance_group_combat_encounter_turn")
    begun = case.act("begin_combat_turn")
    return advanced, begun


def _replay(case, history, *, reopen=False):
    saved = case.snapshot()
    for receipt, command in history:
        assert case.execute(command) == receipt
        assert case.snapshot() == saved
    if reopen:
        case.reopen()
        for receipt, command in history:
            assert case.execute(command) == receipt
            assert case.snapshot() == saved


def test_dead_frost_owner_skipped_by_real_group_initiative_expires_at_finite_clock(tmp_path):
    case = ImportedCase(
        tmp_path / "frost-dead-owner-finite-clock.db", "ray_of_frost",
        participant_changes={"level": 1, "classes": [{"id": "wizard", "level": 1}]},
        entity_changes={"hero": {"hp": 1000, "max_hp": 1000, "armor_class": 1,
                                  "speed": 30, "initiative_modifier": 0}},
        positions={"hero": [0, 0, 0], "guard": [20, 0, 0],
                   "frost-owner": [0, 0, 20]},
    )
    try:
        slots = dict(case.entities()["hero"]["resources"])
        lock = case.snapshot().package_lock
        prepare, prepare_command = case.act(
            "prepare_srd_creature", creature_id="frost-owner",
            name="Synthetic finite Frost owner",
            source="GM-authored 2-HP cantrip consumer fixture; not a printed monster.",
            armor_class=10, hit_points=2, speed=30, size_rank=1,
            initiative_modifier=20, constitution_save=0,
            spellcasting_ability="intelligence", spellcasting_modifier=4,
            spell_attack_bonus=30, spell_save_dc=12, cantrip_scaling_level=1,
            spells=[{"spell": "ray_of_frost", "uses_per_day": 0, "casting_level": 0}],
        )
        prepared = case.snapshot()
        assert case.execute(prepare_command) == prepare and case.snapshot() == prepared
        case.act("start_group_combat_encounter", combatants=["frost-owner", "hero", "guard"])
        case.act("begin_combat_turn")
        assert _combat(case)["active_actor"] == "frost-owner"
        order = [entry["entity_id"] for entry in _combat(case)["order"]]
        assert len(order) == 3 and set(order) == {"frost-owner", "hero", "guard"}
        assert order[0] == "frost-owner"
        parameters = {
            "spell": "ray_of_frost", "caster_state": "frost-owner", "target": "hero",
            "casting_mode": "ordinary", "target_distance": 20,
            "caster_can_see_target": True, "caster_can_hear_target": True,
            "caster_can_touch_target": False, "target_can_see_caster": True,
            "cover": "none", "advantage": False, "disadvantage": False,
            "verbal_component_available": True, "somatic_component_available": True,
            "material_component_available": False, "material_item_ids": [],
        }
        hit = None
        for _ in range(8):
            hp_before = case.entities()["hero"]["hp"]
            receipt, command = case.act("cast_spell", **parameters)
            assert receipt.result["result_card"]["title"] == "Ray of Frost"
            assert _combat(case)["action_available"] is False
            _replay(case, [(receipt, command)])
            if case.entities()["hero"]["hp"] < hp_before:
                hit = (receipt, command)
                break
            # A natural-one miss remains a miss. Every creature genuinely starts
            # and ends its turn before this NPC earns another casting Action.
            for _ in range(3):
                _next_group_turn(case)
            assert _combat(case)["active_actor"] == "frost-owner"
        assert hit is not None, "No real Frost hit occurred in eight lawful NPC turns."
        receipt, command = hit
        summary = {row["label"]: row["value"] for row in receipt.result["result_card"]["summary"]}
        assert str(summary["Spell level"]) == "0" and summary["Casting"] == "At will"
        assert "Uses spent" not in summary
        rows = _frost_sources(case)
        assert len(rows) == 1
        source = rows[0]
        assert source["caster"] == source["source_caster_id"] == source["owner"] == "frost-owner"
        assert source["target"] == "hero" and source["phase"] == "start"
        assert source["kind"] == "speed_reduction" and source["magnitude"] == 10
        assert source["lite_offensive_status"] is True and source["combat_qualified"] is True
        assert isinstance(source["cast_id"], str) and source["cast_id"]
        assert source["started_tick"] == case.lasting()["lasting_tick"]
        assert source["expires_tick"] == source["started_tick"] + 2
        serial = case.lasting()["spell_source_serial"]
        assert isinstance(serial, int) and serial > 0
        assert case.entities()["hero"]["spell_speed_reduction"] == 10
        assert case.entities()["hero"]["speed"] == 30
        _replay(case, [hit], reopen=True)

        owner_starts = case.entities()["frost-owner"]["turns_started"]
        clock_at_death = case.lasting()["lasting_tick"]
        fall, fall_command = case.act(
            "resolve_srd_fall", target_state="frost-owner", distance=20,
            landing="ground", reaction="none",
        )
        dead = case.entities()["frost-owner"]
        assert dead["hp"] == 0 and dead["is_alive"] is False
        assert dead["death_tick"] == clock_at_death == case.lasting()["lasting_tick"]
        assert len(fall.result["context"]["_fall_roll"]["results"]) == 2
        card = fall.result["result_card"]
        assert card["title"] == "Falling"
        summary = {row["label"]: row["value"] for row in card["summary"]}
        assert float(summary["Fall (feet)"]) == 20
        assert float(summary["Damage taken"]) >= 2
        assert float(summary["HP lost"]) == 2 and float(summary["HP after"]) == 0
        assert case.lasting()["spell_source_serial"] == serial
        _replay(case, [(fall, fall_command)], reopen=True)

        first_hero = False
        checkpoints = [hit, (fall, fall_command)]
        for _ in range(8):
            advanced, begun = _next_group_turn(case)
            combat = _combat(case)
            assert combat["encounter_active"] is True and combat["group_order"] is True
            assert combat["active_actor"] in {"hero", "guard"}
            assert [entry["entity_id"] for entry in combat["order"]] == order
            assert case.entities()["frost-owner"]["turns_started"] == owner_starts
            assert case.entities()["guard"]["is_alive"] is True
            assert case.entities()["hero"]["is_alive"] is True
            assert case.lasting()["spell_source_serial"] == serial
            if combat["active_actor"] != "hero":
                continue
            if not first_hero:
                first_hero = True
                assert case.lasting()["lasting_tick"] < source["expires_tick"]
                assert _frost_sources(case) == [source]
                assert combat["movement_remaining"] == 20
                continue
            if case.lasting()["lasting_tick"] <= source["expires_tick"] + 0.000000001:
                continue
            # The ordinary SRD combat fallback is strictly past the finite world
            # deadline. The dead owner's missing Start cannot make it indefinite.
            checkpoints.extend([advanced, begun])
            assert not _frost_sources(case)
            assert case.entities()["hero"]["spell_speed_reduction"] == 0
            assert case.entities()["hero"]["speed"] == 30
            assert combat["movement_remaining"] == 30
            movement = case.act("spend_combat_movement", distance=25, difficult_terrain=False)
            assert _combat(case)["movement_remaining"] == 5
            assert _combat(case)["movement_spent"] == 25
            assert _combat(case)["action_available"] is True
            assert case.entities()["hero"]["resources"] == slots
            assert case.lasting()["spell_source_serial"] == serial
            assert case.snapshot().package_lock == lock
            _replay(case, checkpoints + [movement], reopen=True)
            assert not _frost_sources(case)
            print("Frost: actual dead owner skipped; finite clock retired its source and restored real movement.", flush=True)
            break
        else:
            raise AssertionError("The living group did not reach Hero after the saved finite expiry deadline.")
        assert first_hero
    finally:
        archive_closed_case(case)
