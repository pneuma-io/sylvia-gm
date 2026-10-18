"""Admitted Lite death/recovery and ordinary ranged weapon paths, no effect substitutes."""

from random import Random

import pytest

from sylvia_gm import dice
from tests.lite_fixture_archive import archive_closed_case
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_lite_foundation import VARIANT_ID


@pytest.mark.parametrize("kind", ["fatal_save", "natural_twenty", "stable_time"])
def test_lite_death_and_recovery_retain_exact_commits(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    case = ImportedCase(
        tmp_path / "death.sqlite3",
        "light",
        entity_changes={
            "hero": {
                "hp": 0,
                "is_alive": True,
                "conditions": ["unconscious"],
                "stable": kind == "stable_time",
                "death_failures": 2,
                "death_successes": 0,
                "stable_recovery_hours_remaining": 1 if kind == "stable_time" else 0,
                "saving_throw_modifiers": dict.fromkeys(
                    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
                    0,
                ),
            }
        },
    )
    try:
        if kind == "stable_time":
            receipt, command = case.act("advance_stable_recovery", target="hero", elapsed_hours=1)
            assert case.entities()["hero"]["hp"] == 1
            assert "unconscious" not in case.entities()["hero"]["conditions"]
        else:
            case.start()
            monkeypatch.setattr(dice, "_rng", Random(31 if kind == "fatal_save" else 5))
            receipt, command = case.act("death_saving_throw")
            hero = case.entities()["hero"]
            if kind == "fatal_save":
                assert receipt.result["context"]["_death_save"]["total"] == 1
                assert hero["death_failures"] == 3 and hero["hp"] == 0 and hero["is_alive"] is False
                case.act("end_combat_encounter", end_reason="one_side_defeated")
                combat = case.snapshot().state[VARIANT_ID + ".combat"]
                assert combat["encounter_active"] is False and combat["active_actor"] is None
            else:
                assert receipt.result["context"]["_death_save"]["total"] == 20
                assert hero["hp"] == 1 and hero["is_alive"] is True
                assert hero["death_failures"] == hero["death_successes"] == 0
                assert "unconscious" not in hero["conditions"]
        saved = case.snapshot()
        assert case.execute(command) == receipt and case.snapshot() == saved
        case.reopen()
        assert case.execute(command) == receipt and case.snapshot() == saved
    finally:
        archive_closed_case(case)


def test_lite_ranged_weapon_costs_and_range_rejection_are_durable(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    case = ImportedCase(
        tmp_path / "ranged.sqlite3",
        "light",
        positions={"hero": [0, 0, 0], "guard": [50, 0, 0]},
        entity_changes={"hero": {"inventory": ["shortbow"], "ammunition": {"arrow": 3}}},
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
        "attack_distance": 50,
    }
    try:
        case.start()
        case.reject("resolve_combat_attack", params | {"attack_distance": 321})
        receipt, command = case.act("resolve_combat_attack", **params)
        context = receipt.result["context"]
        assert (
            context["_attack_roll"]["sides"] == 20 and 1 <= context["_attack_roll"]["total"] <= 20
        )
        assert context["_attack_outcome"] in {"hit", "critical_hit", "miss"}
        assert case.entities()["hero"]["ammunition"]["arrow"] == 2
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
        case.reject("resolve_combat_attack", params)
        assert "" not in case.snapshot().state[VARIANT_ID + ".combat"]
        saved = case.snapshot()
        case.reopen()
        assert case.execute(command) == receipt and case.snapshot() == saved
    finally:
        archive_closed_case(case)
