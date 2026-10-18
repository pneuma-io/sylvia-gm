"""Both sides alive cannot end as defeated; a real lethal hit earns victory cleanup."""
from random import Random
from sylvia_gm import dice
from tests.lite_imported_fixture import ImportedCase
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import VARIANT_ID


def test_lite_actual_enemy_defeat_earns_durable_victory(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    case = ImportedCase(tmp_path / "victory.sqlite3", "light", guard_ac=1,
        positions={"hero": [0, 0, 0], "guard": [50, 0, 0]},
        entity_changes={
            "hero": {"inventory": ["shortbow"], "ammunition": {"arrow": 3}},
            "guard": {"hp": 1, "max_hp": 1, "creature_kind": "monster"},
        })
    try:
        case.start()
        case.reject("end_combat_encounter", {"end_reason": "one_side_defeated"})
        monkeypatch.setattr(dice, "_rng", Random(1))
        attack, _ = case.act("resolve_combat_attack", actor="hero", target="guard",
            attack_sequence="primary", use_graze_on_miss=False, weapon="shortbow",
            attack_mode="ranged", attack_ability="dexterity", wielding_two_handed=True,
            mounted=False, circumstantial_modifier=0, advantage=False, disadvantage=False,
            actor_can_see_target=True, target_can_see_actor=True, target_is_charmer=False,
            fear_source_in_sight=False, attack_distance=50)
        assert attack.result["context"]["_attack_outcome"] == "hit"
        assert case.entities()["guard"]["hp"] == 0
        assert case.entities()["guard"]["is_alive"] is False
        assert case.entities()["hero"]["is_alive"] is True
        assert case.entities()["hero"]["hp"] > 0
        ended, command = case.act("end_combat_encounter", end_reason="one_side_defeated")
        saved = case.snapshot()
        combat = saved.state[VARIANT_ID + ".combat"]
        assert combat["encounter_active"] is False and combat["turn_active"] is False
        assert combat["active_actor"] is None and combat["current_actor"] is None
        assert combat["phase"] == "inactive"
        assert combat["last_end_reason"] == "one_side_defeated"
        assert case.entities()["hero"]["fighting_actor_ids"] == []
        assert case.entities()["guard"]["fighting_actor_ids"] == []
        assert case.execute(command) == ended and case.snapshot() == saved
        case.reopen()
        assert case.execute(command) == ended and case.snapshot() == saved
    finally:
        archive_closed_case(case)
