"""A refused firing action cannot roll, consume ammunition or hide an AP spend."""

import unittest
from unittest.mock import patch

from balance_suite import simulation
from balance_suite.parameters import default_manifest
from balance_suite.policies import PolicyDecision


class DuelAffordabilityTests(unittest.TestCase):
    def test_two_ap_modes_refuse_at_one_ap_then_record_a_separate_end(self):
        parameters = default_manifest().seed_set()
        original = simulation.benchmark_actor

        def one_ap_actor(*args, **kwargs):
            actor = original(*args, **kwargs)
            actor.ap = actor.max_ap = 1
            return actor

        for mode, weapon, rounds in (
            ("BURST", "service_rifle", 3),
            ("SINGLE", "manual_precision_rifle", 1),
        ):
            with self.subTest(mode=mode):
                decision = PolicyDecision(
                    "FIRE", "target", mode, rounds, None, "authored firing price"
                )
                with (
                    patch.object(simulation, "benchmark_actor", one_ap_actor),
                    patch.object(simulation, "choose_action", return_value=decision),
                ):
                    result = simulation.run_duel(
                        simulation.DuelSpec(weapon_a=weapon, weapon_b=weapon, max_team_turns=2),
                        parameters,
                        88,
                        capture_events=True,
                    )
                events = result["events"]
                self.assertEqual(4, len(events))
                for refused, ended in zip(events[::2], events[1::2], strict=True):
                    self.assertEqual("insufficient AP", refused["rejected_reason"])
                    self.assertEqual([], refused["random_draws"])
                    self.assertEqual(refused["pre_state_hash"], refused["post_state_hash"])
                    self.assertEqual("END", ended["action"])
                    self.assertEqual(refused["post_state_hash"], ended["pre_state_hash"])
                self.assertEqual({"A": 0, "B": 0}, result["metrics"]["rounds"])


if __name__ == "__main__":
    unittest.main()
