"""A distant supported scene target cannot be brought into bow range by caller facts."""

from random import Random

from sylvia_gm import dice
from tests.lite_fixture_archive import archive_closed_case
from tests.lite_imported_fixture import ImportedCase


def test_lite_physical_out_of_range_target_rejects_forged_near_distance(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    scene = {
        "format": 1,
        "map": {
            "id": "lite",
            "revision": 0,
            "bounds": {"min": [-100, -100, -100], "max": [400, 100, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
            "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
        },
        "entities": [
            {
                "id": identity,
                "map_id": "lite",
                "position": point,
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {
                    "participant_ref": identity,
                    "plane": "material",
                    "lighting": "bright",
                    "creature_type": "humanoid",
                },
            }
            for identity, point in (("hero", [0, 0, 0]), ("guard", [321, 0, 0]))
        ],
        "geometry": [],
        "zones": [],
        "terrain": [],
    }
    case = ImportedCase(
        tmp_path / "physical-range.sqlite3",
        "light",
        entity_changes={"hero": {"inventory": ["shortbow"], "ammunition": {"arrow": 3}}},
        initial_lasting_changes={"spatial_scene": scene},
    )
    params = {
        "actor": "hero",
        "target": "guard",
        "attack_sequence": "primary",
        "use_graze_on_miss": False,
        "weapon": "shortbow",
        "attack_mode": "ranged",
        "attack_ability": "dexterity",
        "wielding_two_handed": True,
        "mounted": False,
        "circumstantial_modifier": 0,
        "advantage": False,
        "disadvantage": False,
        "actor_can_see_target": True,
        "target_can_see_actor": True,
        "target_is_charmer": False,
        "fear_source_in_sight": False,
    }
    try:
        case.start()
        before = case.snapshot()
        case.reject(
            "resolve_combat_attack", params | {"attack_distance": 50}, match="actual weapon target"
        )
        after = case.snapshot()
        assert after.state == before.state and after.revision == before.revision
        assert case.entities()["hero"]["ammunition"]["arrow"] == 3
        case.reject("resolve_combat_attack", params | {"attack_distance": 321})
        case.reopen()
        assert case.snapshot().state == before.state
    finally:
        archive_closed_case(case)


def test_lite_fractional_saved_long_range_controls_the_actual_roll(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    case = ImportedCase(
        tmp_path / "fractional-range.sqlite3",
        "light",
        positions={"hero": [0, 0, 0], "guard": [81.3, 0, 0]},
        entity_changes={"hero": {"inventory": ["shortbow"], "ammunition": {"arrow": 3}}},
    )
    try:
        case.start()
        receipt, command = case.act(
            "resolve_combat_attack",
            actor="hero",
            target="guard",
            attack_sequence="primary",
            use_graze_on_miss=False,
            weapon="shortbow",
            attack_mode="ranged",
            attack_ability="dexterity",
            wielding_two_handed=True,
            mounted=False,
            circumstantial_modifier=0,
            advantage=False,
            disadvantage=False,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            attack_distance=50,
        )
        assert receipt.result["context"]["_attack_roll_mode"] == "Disadvantage"
        assert len(receipt.result["context"]["_attack_roll"]["results"]) == 2
        assert case.entities()["hero"]["ammunition"]["arrow"] == 2
        from tests.test_srd_lite_foundation import VARIANT_ID

        assert "" not in case.snapshot().state[VARIANT_ID + ".combat"]
        saved = case.snapshot()
        case.reopen()
        assert case.execute(command) == receipt and case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_lite_npc_melee_uses_saved_range_before_spending_the_action(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    case = ImportedCase(
        tmp_path / "npc-range.sqlite3",
        "light",
        positions={"hero": [0, 0, 0], "guard": [20, 0, 0]},
        entity_changes={"hero": {"initiative_modifier": 0}, "guard": {"initiative_modifier": 20}},
    )
    try:
        case.act(
            "start_combat_encounter_entities",
            first_actor="guard",
            second_actor="hero",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        case.act("begin_combat_turn")
        from tests.test_srd_lite_foundation import VARIANT_ID

        assert case.snapshot().state[VARIANT_ID + ".combat"]["active_actor"] == "guard"
        case.reject(
            "resolve_adjudicated_creature_attack",
            {
                "target": "hero",
                "attack_name": "Practice spear",
                "attack_modifier": 3,
                "damage_expression": "d6",
                "damage_modifier": 1,
                "damage_type": "piercing",
                "advantage": False,
                "disadvantage": False,
                "attack_mode": "melee",
                "attack_distance": 5,
                "normal_range": 5,
                "long_range": 5,
                "actor_can_see_target": True,
                "target_can_see_actor": True,
                "target_is_charmer": False,
                "fear_source_in_sight": False,
            },
            match="actual weapon target",
        )
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is True
        saved = case.snapshot()
        case.reopen()
        assert case.snapshot().state == saved.state
    finally:
        archive_closed_case(case)
