from __future__ import annotations

import unittest
from pathlib import Path
import re

from balance_suite.parameters import Authority, ParameterError, default_manifest


class ParameterManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = default_manifest()
        self.seed = self.manifest.seed_set()

    def test_seed_manifest_is_complete(self) -> None:
        self.assertEqual(len(self.manifest.specs), 597)
        self.assertIs(
            Authority.TUNABLE,
            self.manifest.specs["equipment.shotgun.cycle_ap"].authority,
        )
        self.assertIs(
            Authority.TUNABLE,
            self.manifest.specs["equipment.manual_precision_rifle.cycle_ap"].authority,
        )
        self.assertEqual(set(self.manifest.specs), set(self.seed.values))

    def test_hash_is_deterministic(self) -> None:
        self.assertEqual(self.seed.hash, self.manifest.seed_set().hash)

    def test_unknown_parameter_rejected(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"not.registered": 1}, optimizer=True)

    def test_missing_parameter_rejected(self) -> None:
        values = dict(self.seed.values)
        values.pop("combat.ap.pool")
        with self.assertRaises(ParameterError):
            self.manifest.validate_values(values, require_complete=True)

    def test_integer_type_rejected(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"combat.ap.pool": 6.5}, optimizer=True)

    def test_bounds_are_inclusive_and_enforced(self) -> None:
        self.seed.with_updates({"combat.ap.pool": 5}, optimizer=True)
        self.seed.with_updates({"combat.ap.pool": 8}, optimizer=True)
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"combat.ap.pool": 9}, optimizer=True)

    def test_check_difficulties_must_remain_ordered(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"check.difficulty.routine": 12, "check.difficulty.standard": 12}, optimizer=True)

    def test_movement_rates_must_remain_ordered(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"move.crouched_m_per_ap": 4.0, "move.upright_m_per_ap": 4.0}, optimizer=True)

    def test_hp_progression_cap_is_enforced(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"hp.vector_l01_l05_l10_l15_l20": [8, 9, 10, 11, 13]}, optimizer=True)

    def test_explosive_warning_order_is_enforced(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"explosive_save.hard_warning_s": 3.0, "explosive_save.normal_warning_s": 3.0}, optimizer=True)

    def test_fixed_fixture_is_not_optimizer_mutable(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"fixture.ap_seconds": 2}, optimizer=True)

    def test_armed_drone_modules_have_only_explicit_tunable_values(self) -> None:
        self.assertNotIn("drone.armed_attack_economy", self.manifest.specs)
        expected = {
            "equipment.fpv_fragmentation_payload.attack_ap": 1,
            "equipment.autodrone_light_weapon.damage": 4,
            "equipment.autodrone_light_weapon.target_pen": 1,
            "equipment.autodrone_light_weapon.material_pen": 1,
            "equipment.autodrone_light_weapon.range_breakpoints_m": [15, 30, 50],
            "equipment.autodrone_light_weapon.range_modifiers": [0, 2, 5, 8],
            "equipment.autodrone_light_weapon.recoil": 2,
            "equipment.autodrone_light_weapon.capacity": 10,
            "equipment.autodrone_light_weapon.reload_ap": 3,
            "equipment.autodrone_light_weapon.attack_ap_equivalent": 2,
            "equipment.autodrone_light_weapon.targeting_bonus": 4,
        }
        self.assertEqual(
            expected,
            {parameter_id: self.seed.values[parameter_id] for parameter_id in expected},
        )
        self.assertTrue(
            all(self.manifest.specs[parameter_id].authority is Authority.TUNABLE for parameter_id in expected)
        )

    def test_integer_range_bounds_stay_inside_the_authorized_percentage(self) -> None:
        spec = self.manifest.specs[
            "equipment.autodrone_light_weapon.range_breakpoints_m"
        ]
        self.assertEqual([11, 21, 35], spec.minimum)
        self.assertEqual([19, 39, 65], spec.maximum)
        with self.assertRaises(ParameterError):
            self.seed.with_updates(
                {
                    "equipment.autodrone_light_weapon.range_breakpoints_m": [
                        10,
                        30,
                        50,
                    ]
                },
                optimizer=True,
            )
        with self.assertRaises(ParameterError):
            self.seed.with_updates(
                {
                    "equipment.autodrone_light_weapon.range_breakpoints_m": [
                        15,
                        30,
                        66,
                    ]
                },
                optimizer=True,
            )

    def test_target_pen_cannot_exceed_damage(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"equipment.pistol.damage": 2, "equipment.pistol.target_pen": 3}, optimizer=True)

    def test_heavy_armor_order_is_enforced(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates({"equipment.heavy_body_armor.res": 4, "equipment.standard_body_armor.res": 4}, optimizer=True)

    def test_only_explicitly_admitted_specialty_parameters_exist(self) -> None:
        admitted = {parameter_id for parameter_id in self.manifest.specs if parameter_id.startswith("specialty.")}
        self.assertEqual(len(admitted), 343)
        self.assertIn("specialty.local_hacker.service_access.difficulty_offset", admitted)

    def test_hacking_visibility_bands_are_ordered_and_optimizer_locked_where_fixed(self) -> None:
        self.assertEqual(-2, self.seed.values["hacking.visibility.low_difficulty_offset"])
        self.assertEqual(0, self.seed.values["hacking.visibility.standard_difficulty_offset"])
        self.assertEqual(2, self.seed.values["hacking.visibility.high_difficulty_offset"])
        with self.assertRaises(ParameterError):
            self.seed.with_updates(
                {"hacking.visibility.standard_difficulty_offset": 1},
                optimizer=True,
            )

    def test_salvage_must_leave_a_persistent_fixture_consequence(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates(
                {
                    "injury.repairable_forearm.ordinary_persistent_difficulty": 3,
                    "specialty.trauma.salvage.persistent_difficulty_reduction": 3,
                },
                optimizer=True,
            )

    def test_one_handed_reduction_cannot_exceed_the_baseline_surcharge(self) -> None:
        with self.assertRaises(ParameterError):
            self.seed.with_updates(
                {
                    "fire.one_handed_manipulation_surcharge_ap": 1,
                    "specialty.operator.one_handed_manipulation.one_handed_surcharge_reduction_ap": 2,
                },
                optimizer=True,
            )

    def test_markdown_and_executable_specialty_parameter_ids_match(self) -> None:
        text = Path("Testing/BALANCE PARAMETERS.md").read_text(encoding="utf-8-sig")
        documented = set(re.findall(r"`(specialty\.[a-z0-9_.]+)`", text))
        executable = {parameter_id for parameter_id in self.manifest.specs if parameter_id.startswith("specialty.")}
        self.assertEqual(documented, executable)


if __name__ == "__main__":
    unittest.main()
