from __future__ import annotations

import unittest

from balance_suite.fixtures import weapon_state
from balance_suite.model import HitQuality, Stance
from balance_suite.parameters import default_manifest
from balance_suite.probes import drone_probe
from balance_suite.rules import (
    attack_values_for_weapon,
    connection_opportunities,
    cycle_weapon,
    explosive_warning,
    fire_action,
    hit_quality,
    movement_rate,
    recover_recoil,
    reload_magazine,
    resolve_attack,
    resolve_check,
    resolve_cover_roll,
    resolve_cqc_maneuver,
    resolve_damage,
    resolve_material,
    route_material_layers,
)


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parameters = default_manifest().seed_set()

    def test_check_formula(self) -> None:
        result = resolve_check(10, 4, 1, 15)
        self.assertTrue(result.success)
        self.assertEqual(result.total, 15)

    def test_generic_natural_one_is_not_invented_as_auto_failure(self) -> None:
        result = resolve_check(1, 8, 3, 10)
        self.assertTrue(result.success)
        self.assertIn("unresolved", result.natural_rule)

    def test_attack_natural_one_is_automatic_miss(self) -> None:
        result = resolve_attack(1, 8, 3, 5, 5, 2, self.parameters)
        self.assertEqual(result.quality, HitQuality.MISS)

    def test_attack_natural_twenty_is_critical(self) -> None:
        result = resolve_attack(20, 0, 0, 30, 5, 2, self.parameters)
        self.assertEqual(result.quality, HitQuality.CRITICAL_HIT)

    def test_impossible_natural_twenty_does_not_succeed(self) -> None:
        result = resolve_attack(20, 8, 3, 1, 5, 2, self.parameters, legitimate=False)
        self.assertEqual(result.quality, HitQuality.MISS)

    def test_hit_quality_threshold_boundaries(self) -> None:
        v = self.parameters.values
        self.assertEqual(hit_quality(10, v["hit_quality.clean_margin"] - 1, self.parameters, legitimate=True, success=True), HitQuality.HIT)
        self.assertEqual(hit_quality(10, v["hit_quality.clean_margin"], self.parameters, legitimate=True, success=True), HitQuality.CLEAN_HIT)
        self.assertEqual(hit_quality(10, v["hit_quality.decisive_margin"], self.parameters, legitimate=True, success=True), HitQuality.DECISIVE_HIT)
        self.assertEqual(hit_quality(10, v["hit_quality.critical_margin"], self.parameters, legitimate=True, success=True), HitQuality.CRITICAL_HIT)

    def test_critical_target_pen_does_not_change_material_pen(self) -> None:
        attack = resolve_attack(20, 4, 1, 14, 5, 2, self.parameters, critical_choice="DEEP HIT")
        material = resolve_material(5, 2, 3, 3)
        self.assertGreater(attack.effective_target_pen, 2)
        self.assertEqual(material.input_material_pen, 3)

    def test_res_hp_damage_routing_with_bypass_and_overflow(self) -> None:
        result = resolve_damage(7, 2, 3, 10)
        self.assertEqual(result.hp_bypass, 2)
        self.assertEqual(result.res_damage, 3)
        self.assertEqual(result.hp_overflow, 2)
        self.assertEqual(result.total_hp_damage, 4)

    def test_material_stop(self) -> None:
        self.assertEqual(resolve_material(5, 2, 2, 3).outcome, "STOP")

    def test_material_reduce(self) -> None:
        result = resolve_material(5, 3, 3, 3)
        self.assertEqual((result.outcome, result.remaining_damage, result.remaining_target_pen), ("REDUCE", 2, 1))

    def test_material_penetrate(self) -> None:
        result = resolve_material(5, 2, 5, 3)
        self.assertEqual((result.outcome, result.remaining_material_pen), ("PENETRATE", 2))

    def test_material_layers_stop_before_target(self) -> None:
        stages, remaining = route_material_layers(5, 2, 4, [2, 3])
        self.assertIsNone(remaining)
        self.assertEqual(stages[-1].outcome, "STOP")

    def test_cover_open_actual_and_wide_miss_routing(self) -> None:
        self.assertEqual(resolve_cover_roll(18, 14, 18).outcome, "TARGET HIT")
        self.assertEqual(resolve_cover_roll(16, 14, 18).outcome, "COVER INTERCEPT")
        self.assertEqual(resolve_cover_roll(13, 14, 18).outcome, "WIDE MISS")

    def test_connection_opportunities_are_capped_by_rounds(self) -> None:
        result = connection_opportunities("FULL AUTO", 3, HitQuality.CRITICAL_HIT, self.parameters)
        self.assertLessEqual(result, 3)

    def test_recoil_persists_and_recovers(self) -> None:
        weapon = weapon_state("service_rifle", self.parameters)
        fire_action(weapon, "SEMI AUTO", 1, 12, 4, 1, 14, 20, "full", self.parameters)
        self.assertGreater(weapon.current_recoil, 0)
        recovered = recover_recoil(weapon, 1, self.parameters)
        self.assertGreater(recovered, 0)

    def test_armed_drone_probe_uses_real_attack_and_blast_rules(self) -> None:
        probe = drone_probe(self.parameters)
        armed = probe["armed_combat"]
        self.assertTrue(armed["fpv_fragmentation"]["payload_and_drone_consumed"])
        self.assertEqual(
            self.parameters.values["equipment.frag_grenade.damage"],
            armed["fpv_fragmentation"]["fragmentation_damage"],
        )
        self.assertTrue(armed["fpv_fragmentation"]["frozen_blast_snapshot"])
        autonomous = armed["autonomous_light_weapon"]
        self.assertEqual("SEMI AUTO", autonomous["fire_mode"])
        self.assertEqual(10, autonomous["capacity"])
        self.assertEqual(2, autonomous["attack_ap_equivalent"])
        self.assertTrue(autonomous["standing_order_required"])
        self.assertTrue(autonomous["sensor_confirmation_required"])
        self.assertTrue(
            all(
                row["manual_success_rate"] > row["autonomous_success_rate"]
                for row in autonomous["level_rows"]
            )
        )
        execution = armed["execution"]
        self.assertEqual("MAP_STREET_60", execution["map_id"])
        autonomous_cases = {
            item["case_id"]: item for item in execution["autonomous_cases"]
        }
        self.assertEqual(
            {"TARGET_HIT", "COVER_INTERCEPT", "GEOMETRY_BLOCKED"},
            {item["outcome"] for item in autonomous_cases.values()},
        )
        self.assertLess(
            autonomous_cases["partial-cover-intercept"]["cover_hp_after"],
            autonomous_cases["partial-cover-intercept"]["cover_hp_before"],
        )
        self.assertEqual(
            autonomous_cases["exposed-target"]["ammunition_before"] - 1,
            autonomous_cases["exposed-target"]["ammunition_after"],
        )
        self.assertEqual(
            self.parameters.values["equipment.autodrone_light_weapon.recoil"],
            autonomous_cases["exposed-target"]["recoil_after"],
        )
        self.assertEqual(
            {"MISSING_SENSOR_CONFIRMATION", "MISSING_STANDING_ORDER"},
            {
                item["reason"]
                for item in execution["autonomous_authorization_rejections"]
            },
        )
        self.assertTrue(
            all(
                item["ammunition_before"] == item["ammunition_after"]
                and item["execution_ap_before"] == item["execution_ap_after"]
                for item in execution["autonomous_authorization_rejections"]
            )
        )
        fpv_cases = execution["fpv_cases"]
        self.assertTrue(all(item["payload_consumed"] for item in fpv_cases))
        self.assertTrue(all(item["drone_consumed"] for item in fpv_cases))
        self.assertTrue(all(item["frozen_snapshot"] for item in fpv_cases))
        self.assertTrue(
            all(item["actual_detonation"] is not None for item in fpv_cases)
        )
        fpv_by_case = {item["case_id"]: item for item in fpv_cases}
        exposed = fpv_by_case["exposed-placement"]
        self.assertEqual("EXPOSED", exposed["target_cover_outcome"])
        self.assertEqual(exposed["cover_hp_before"], exposed["cover_hp_after"])
        blocked = fpv_by_case["failed-placement-scatter"]
        self.assertEqual("PROTECTED_BY_SNAPSHOT", blocked["target_cover_outcome"])
        self.assertLess(blocked["cover_hp_after"], blocked["cover_hp_before"])

    def test_ammunition_is_conserved_through_fire_and_reload(self) -> None:
        weapon = weapon_state("service_rifle", self.parameters)
        before = weapon.total_rounds()
        result = fire_action(weapon, "FULL AUTO", 5, 12, 4, 1, 14, 20, "full", self.parameters)
        self.assertEqual(before - weapon.total_rounds(), result.rounds_fired)
        after_fire = weapon.total_rounds()
        reload_magazine(weapon, weapon.magazine_capacity)
        self.assertEqual(weapon.total_rounds(), after_fire)

    def test_manual_action_requires_cycle(self) -> None:
        weapon = weapon_state("manual_precision_rifle", self.parameters)
        fire_action(weapon, "SINGLE", 1, 12, 4, 1, 14, 100, "full", self.parameters)
        self.assertTrue(weapon.needs_cycle)
        self.assertTrue(cycle_weapon(weapon))
        self.assertFalse(weapon.needs_cycle)

    def test_shotgun_damage_falls_with_range(self) -> None:
        near = attack_values_for_weapon("shotgun", 5, self.parameters)[0]
        far = attack_values_for_weapon("shotgun", 30, self.parameters)[0]
        self.assertGreater(near, far)

    def test_class_movement_directions(self) -> None:
        control = movement_rate(Stance.STANDING, 0, "CONTROL", None, self.parameters)
        self.assertLess(movement_rate(Stance.STANDING, 0, "BULWARK", None, self.parameters), control)
        self.assertGreater(movement_rate(Stance.STANDING, 0, "GHOST", None, self.parameters), control)

    def test_cqc_one_maneuver_has_only_declared_result(self) -> None:
        result = resolve_cqc_maneuver("TRIP", 20, 4, 1, 2, 1, self.parameters)
        self.assertEqual(result.declared_result["stance_change"], "PRONE")
        self.assertEqual(result.declared_result["forced_movement_m"], 0)
        self.assertEqual(result.free_secondary_effects, ())

    def test_explosive_save_has_no_warning_below_minimum(self) -> None:
        eligible, modifier, _ = explosive_warning(0.5, self.parameters)
        self.assertFalse(eligible)
        self.assertIsNone(modifier)


if __name__ == "__main__":
    unittest.main()
