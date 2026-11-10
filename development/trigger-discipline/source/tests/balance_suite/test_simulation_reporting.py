from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from balance_suite.artifacts import read_json
from balance_suite.cli import main
from balance_suite.optimizer import optimize
from balance_suite.parameters import ParameterError, default_manifest
from balance_suite.policies import KnownTarget, PolicyObservation, choose_action
from balance_suite.model import KnowledgeState, Position, Stance
from balance_suite.probes import exact_check_curves
from balance_suite.provenance import protected_fingerprint
from balance_suite.replay import replay_artifact
from balance_suite.reports import render_html
from balance_suite.rng import RNGManager, derive_seed
from balance_suite.runner import build_baseline, write_baseline_artifacts
from balance_suite.scenario_runtime import EXACT_RESULT_KEYS, execute_scenario
from balance_suite.scenarios import scenario_registry
from balance_suite.simulation import DuelSpec, paired_mirrored_duels, run_duel
from balance_suite.tactical import TeamScenarioSpec, paired_mirrored_team_scenarios, run_team_scenario


class SimulationAndReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = default_manifest()
        cls.parameters = cls.manifest.seed_set()

    def test_derived_seed_is_stable(self) -> None:
        self.assertEqual(derive_seed(42, "attack", 1), derive_seed(42, "attack", 1))
        self.assertNotEqual(derive_seed(42, "attack", 1), derive_seed(42, "attack", 2))

    def test_rng_substreams_are_isolated(self) -> None:
        first = RNGManager(42)
        a1 = first.stream("A").d20("test")
        first.stream("B").d20("unrelated")
        a2 = first.stream("A").d20("test2")
        second = RNGManager(42)
        self.assertEqual((a1, a2), (second.stream("A").d20("test"), second.stream("A").d20("test2")))

    def test_duel_repeats_semantically(self) -> None:
        spec = DuelSpec(level=1)
        first = run_duel(spec, self.parameters, 99, capture_events=True)
        second = run_duel(spec, self.parameters, 99, capture_events=True)
        self.assertEqual(first["event_log_hash"], second["event_log_hash"])
        self.assertEqual(first["final_state_hash"], second["final_state_hash"])

    def test_recorded_replay_matches(self) -> None:
        artifact = run_duel(DuelSpec(level=1), self.parameters, 88, capture_events=True)
        self.assertTrue(replay_artifact(artifact, self.manifest)["replay_match"])

    def test_team_control_runs_end_to_end_and_replays(self) -> None:
        artifact = run_team_scenario(TeamScenarioSpec(level=1, max_team_turns=8), self.parameters, 88, capture_events=True)
        self.assertIn("held objective continuously", artifact["terminal_reason"])
        self.assertGreater(artifact["event_count"], 0)
        self.assertFalse(artifact["metrics"]["invariant_failures"])
        self.assertTrue(replay_artifact(artifact, self.manifest)["replay_match"])

    def test_symmetric_team_control_pair_is_neutral(self) -> None:
        result = paired_mirrored_team_scenarios(TeamScenarioSpec(level=1, max_team_turns=8), self.parameters, 88, 1, capture_replay=False)
        self.assertEqual(result["subject_outcome_share"]["estimate"], 0.5)
        self.assertTrue(result["mirror_neutral"])

    def test_every_declared_scenario_has_a_dispatch_path(self) -> None:
        dispatched = set(EXACT_RESULT_KEYS) | {
            "MIRROR-DUEL-V1",
            "SPECIALTY-COUNTERFACTUAL-V1",
            "TEAM-CONTROL-4V4-V1",
            "TEAM-EXTRACT-4V4-V1",
            "TEAM-BREACH-4V4-V1",
            "TEAM-RECON-HACK-4V4-V1",
            "DRONE-ARMED-V1",
        }
        self.assertEqual(set(scenario_registry()), dispatched)

    def test_exact_scenario_execution_replays_by_semantic_hash(self) -> None:
        execution = execute_scenario(
            "FIRE-HANDLING-TIGHT-V1",
            self.parameters,
            master_seed=19,
            runs=1,
            level=1,
        )
        self.assertEqual(execution["result"]["status"], "IMPLEMENTED_WITH_PARAMETERS")
        record = execution["scenario_record"]
        self.assertTrue(record["fixture_ids"])
        self.assertTrue(record["exact_enumeration"])
        self.assertEqual(record["input_state"]["map_fixture_id"], "MAP_TIGHT_20")
        self.assertEqual(
            "result.measurements.exact_case_records",
            record["input_state"]["serialized_from"],
        )
        self.assertGreater(record["input_state"]["exact_case_count"], 1)
        for case in record["input_state"]["exact_case_records"]:
            self.assertTrue(case["case_id"])
            self.assertTrue(case["serialized_case_hash"])
            self.assertTrue(case["result_locator"])
            self.assertEqual(2, len(case["input_state"]["actors"]))
            self.assertEqual(
                record["map_id"], case["input_state"]["map_fixture_id"]
            )
            self.assertIn("subsystem_state", case["input_state"])
            self.assertIn("team_turn", case["input_state"])
        first_case = record["input_state"]["exact_case_records"][0]
        for actor in first_case["input_state"]["actors"].values():
            self.assertTrue(actor["build_id"])
            self.assertIn("current", actor["hp"])
            self.assertIn("current", actor["res"])
            self.assertIn("current", actor["ap"])
            self.assertIn("ammunition", actor["equipment"])
            self.assertEqual("STANDING", actor["posture"])
            self.assertTrue(actor["target_knowledge"])
            self.assertGreaterEqual(actor["position"]["x"], 0)
            self.assertLess(actor["position"]["x"], record["input_state"]["map_geometry"]["width"])
            self.assertGreaterEqual(actor["position"]["y"], 0)
            self.assertLess(actor["position"]["y"], record["input_state"]["map_geometry"]["height"])
        self.assertEqual(
            "result.measurements",
            record["input_state"]["probe_state_locator"],
        )
        self.assertEqual(record["raw_metrics_locator"], "result.measurements")
        self.assertTrue(replay_artifact(execution, self.manifest)["replay_match"])

    def test_cover_destruction_cases_use_the_real_street_cover_geometry(self) -> None:
        execution = execute_scenario(
            "COVER-DESTRUCTION-V1",
            self.parameters,
            master_seed=19,
            runs=1,
            level=1,
        )
        cases = execution["scenario_record"]["input_state"]["exact_case_records"]
        self.assertEqual(42, len(cases))
        for case in cases:
            state = case["input_state"]["subsystem_state"]
            self.assertEqual("STREET_BARRIER_1", state["cover_id"])
            self.assertIn((20, 7), state["cover_cells"])
            actors = case["input_state"]["actors"]
            self.assertEqual({"x": 5, "y": 10}, actors["A_PROBE_1"]["position"])
            self.assertEqual({"x": 55, "y": 10}, actors["B_PROBE_1"]["position"])

    def test_partial_cover_cases_cover_every_required_posture_and_exposure(self) -> None:
        execution = execute_scenario(
            "COVER-PARTIAL-V1",
            self.parameters,
            master_seed=19,
            runs=1,
            level=1,
        )
        cases = execution["scenario_record"]["input_state"]["exact_case_records"]
        combinations = {
            (
                case["input_state"]["actors"]["B_PROBE_1"]["posture"],
                case["input_state"]["subsystem_state"][
                    "geometry_verified_exposure"
                ],
            )
            for case in cases
        }
        self.assertEqual(
            {
                (posture, exposure)
                for posture in ("STANDING", "CROUCHED", "PRONE")
                for exposure in ("full", "half", "quarter")
            },
            combinations,
        )
        self.assertEqual(9 * 13, len(cases))
        for case in cases:
            state = case["input_state"]["subsystem_state"]
            self.assertEqual("STREET_BARRIER_1", state["cover_id"])
            self.assertEqual(
                {"x": 25, "y": 10},
                case["input_state"]["actors"]["B_PROBE_1"]["position"],
            )

    def test_every_exact_scenario_publishes_resolvable_source_inputs(self) -> None:
        def resolve_locator(execution: dict[str, object], locator: str) -> object:
            current: object = execution["result"]
            for component in locator.removeprefix("result.").split("."):
                match = re.fullmatch(r"([^\[]+)(?:\[(\d+)\])?", component)
                self.assertIsNotNone(match)
                assert match is not None and isinstance(current, dict)
                current = current[match.group(1)]
                if match.group(2) is not None:
                    assert isinstance(current, list)
                    current = current[int(match.group(2))]
            return current

        for scenario_id in EXACT_RESULT_KEYS:
            with self.subTest(scenario_id=scenario_id):
                execution = execute_scenario(
                    scenario_id,
                    self.parameters,
                    master_seed=19,
                    runs=1,
                    level=1,
                )
                record = execution["scenario_record"]
                cases = record["input_state"]["exact_case_records"]
                self.assertTrue(cases)
                for case in cases:
                    self.assertEqual(
                        record["map_id"],
                        case["input_state"]["map_fixture_id"],
                    )
                    self.assertEqual(2, len(case["input_state"]["actors"]))
                    self.assertIsNotNone(
                        resolve_locator(execution, case["result_locator"])
                    )

    def test_exact_specialty_scenario_records_actual_designated_build(self) -> None:
        execution = execute_scenario(
            "SPECIALTY-COUNTERFACTUAL-V1",
            self.parameters,
            master_seed=19,
            runs=1,
            level=10,
            class_a="RIFLEMAN",
            specialty="GUNNER",
        )
        actors = execution["scenario_record"]["input_state"][
            "requested_actor_selection"
        ]
        subject = next(actor for actor in actors.values() if actor["side"] == "A")
        control = next(actor for actor in actors.values() if actor["side"] == "B")
        self.assertIn("TD-BUILD-GUNNER-L10-", subject["build_id"])
        self.assertEqual(10, len(subject["specialty_nodes"]))
        self.assertEqual([], control["specialty_nodes"])
        self.assertIn("BASELINE-V1", control["build_id"])

    def test_previously_blocked_team_scenario_executes_and_replays(self) -> None:
        execution = execute_scenario(
            "TEAM-EXTRACT-4V4-V1",
            self.parameters,
            master_seed=19,
            runs=1,
            level=1,
        )
        self.assertEqual(execution["execution_kind"], "MONTE_CARLO")
        self.assertEqual(execution["result"]["scenario_id"], "TEAM-EXTRACT-4V4-V1")
        record = execution["scenario_record"]
        self.assertFalse(record["exact_enumeration"])
        self.assertEqual(len(record["filters"]["classes_a"]), 4)
        self.assertEqual(record["raw_event_log_locator"], "result.sample_replay.events")
        self.assertTrue(record["sample_event_log_hash"])
        self.assertEqual(
            execution["result"]["sample_replay"]["initial_state"],
            record["input_state"]["scenario_state"],
        )
        width = record["input_state"]["map_geometry"]["width"]
        height = record["input_state"]["map_geometry"]["height"]
        for actor in record["input_state"]["scenario_state"]["actors"].values():
            self.assertGreaterEqual(actor["position"]["x"], 0)
            self.assertLess(actor["position"]["x"], width)
            self.assertGreaterEqual(actor["position"]["y"], 0)
            self.assertLess(actor["position"]["y"], height)
        self.assertTrue(replay_artifact(execution, self.manifest)["replay_match"])

    def test_long_range_fire_uses_the_1000m_map_fixture(self) -> None:
        definition = scenario_registry()["FIRE-SINGLE-RANGE-V1"]
        self.assertEqual(definition.map_id, "MAP_LONG_1000")

    def test_all_mixed_team_objectives_execute_and_replay(self) -> None:
        for scenario_id in ("TEAM-EXTRACT-4V4-V1", "TEAM-BREACH-4V4-V1", "TEAM-RECON-HACK-4V4-V1"):
            with self.subTest(scenario_id=scenario_id):
                execution = execute_scenario(scenario_id, self.parameters, master_seed=31, runs=1, level=10)
                self.assertEqual(execution["execution_kind"], "MONTE_CARLO")
                self.assertTrue(replay_artifact(execution, self.manifest)["replay_match"])

    def test_symmetric_paired_mirror_is_neutral(self) -> None:
        result = paired_mirrored_duels(DuelSpec(level=1), self.parameters, 5, 20, capture_replay=False)
        self.assertAlmostEqual(result["subject_outcome_share"]["estimate"], 0.5)
        self.assertTrue(result["mirror_neutral"])

    def test_level_one_training_control_uses_approved_fixture_and_beats_talent(self) -> None:
        result = exact_check_curves(self.parameters)
        standard = [row for row in result["rows"] if row["level"] == 1 and row["difficulty_id"] == "standard"]
        training = next(row for row in standard if row["control"] == "TRAINING")
        talent = next(row for row in standard if row["control"] == "TALENT")

        self.assertEqual((training["skill"], training["aptitude"]), (5, 0))
        self.assertEqual((talent["skill"], talent["aptitude"]), (2, 3))
        self.assertGreaterEqual(training["success_rate"], talent["success_rate"])
        self.assertNotIn("TRAINING_TALENT_CONFLICT", {finding["code"] for finding in result["findings"]})

    def test_policy_cannot_read_hidden_enemy_position(self) -> None:
        def observation(hidden_position: Position) -> PolicyObservation:
            return PolicyObservation("A", Position(0,0), Stance.STANDING, 6, 10, 6, "service_rifle", 30, 0, ("SEMI AUTO",), (KnownTarget("B", KnowledgeState.APPROXIMATE, None, None, True, False),), Position(10,0), metadata={"hidden_position_not_exposed": hidden_position})
        first = choose_action("POLICY_DIRECT", observation(Position(50,50)))
        second = choose_action("POLICY_DIRECT", observation(Position(5,5)))
        self.assertEqual(first, second)

    def test_all_tactical_policies_make_distinct_declared_choices(self) -> None:
        target = KnownTarget("B", KnowledgeState.CONFIRMED, Position(60, 0), 60.0, True, False)
        base = dict(
            actor_id="A",
            position=Position(0, 0),
            stance=Stance.STANDING,
            ap=6,
            hp=10,
            res=6,
            weapon_id="service_rifle",
            ammunition=30,
            recoil=0,
            fire_modes=("SEMI AUTO", "3-round BURST", "FULL AUTO"),
            known_targets=(target,),
            objective_position=Position(30, 0),
            behind_cover=False,
        )
        metadata = {
            "aim_ap": 0,
            "flank_complete": False,
            "at_objective": False,
            "cover_destination": Position(5, 5),
            "inside_close_envelope": False,
            "suppression_active": False,
            "can_overwatch": True,
            "can_treat": False,
            "can_hack": False,
            "can_deploy": True,
            "can_cqc": False,
            "needs_cycle": False,
            "needs_reload": False,
            "can_reload": True,
        }
        decisions = {
            policy: choose_action(policy, PolicyObservation(**base, metadata=metadata))
            for policy in (
                "POLICY_DIRECT",
                "POLICY_COVER_FIRST",
                "POLICY_PRESSURE",
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_VOLUME",
                "POLICY_FLANK",
                "POLICY_OBJECTIVE",
                "POLICY_SUPPORT",
            )
        }
        self.assertEqual(decisions["POLICY_DIRECT"].action, "FIRE")
        self.assertEqual(decisions["POLICY_COVER_FIRST"].action, "MOVE_COVER")
        self.assertEqual(decisions["POLICY_PRESSURE"].action, "MOVE_OBJECTIVE")
        self.assertEqual(decisions["POLICY_FIRE_DISCIPLINE"].action, "AIM")
        self.assertEqual(decisions["POLICY_VOLUME"].mode, "FULL AUTO")
        self.assertEqual(decisions["POLICY_FLANK"].action, "FLANK")
        self.assertEqual(decisions["POLICY_OBJECTIVE"].action, "MOVE_OBJECTIVE")
        self.assertEqual(decisions["POLICY_SUPPORT"].action, "DEPLOY")

    def test_optimizer_rejects_locked_parameter(self) -> None:
        with self.assertRaises(ParameterError):
            optimize(self.parameters, ["fixture.ap_seconds"])

    def test_optimizer_only_returns_manifest_updates(self) -> None:
        result = optimize(self.parameters, ["hit_quality.clean_margin"], 20)
        self.assertTrue(set(result["best"]["updates"]).issubset(self.manifest.specs))
        loaded = self.manifest.load_set(result["best_parameter_values"])
        self.assertEqual(loaded.hash, result["best"]["parameter_hash"])
        self.assertEqual(result["non_manifest_mutation_count"], 0)

    def test_optimizer_does_not_infer_general_dominance_from_one_family(self) -> None:
        result = optimize(self.parameters, ["hit_quality.clean_margin"], 20)
        self.assertNotIn("GENERAL DOMINANCE", {finding["code"] for finding in result["findings"]})

    def test_optimizer_does_not_write_canonical_sources(self) -> None:
        before = protected_fingerprint()
        optimize(self.parameters, ["hit_quality.clean_margin"], 20)
        self.assertEqual(before, protected_fingerprint())

    def test_all_cli_workflows_preserve_protected_sources(self) -> None:
        before = protected_fingerprint()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_dir = root / "baseline"
            duel = root / "duel.json"
            team = root / "team.json"
            commands = [
                ["validate"],
                ["coverage", "--specialty", "ANCHOR"],
                ["exact", "--family", "check_curves"],
                ["simulate", "--scenario", "MIRROR-DUEL-V1", "--runs", "1", "--level", "1", "--output", str(duel)],
                ["simulate", "--scenario", "TEAM-CONTROL-4V4-V1", "--runs", "1", "--level", "1", "--output", str(team)],
                ["simulate", "--scenario", "TEAM-EXTRACT-4V4-V1", "--runs", "1", "--level", "1"],
                ["optimize", "--parameter", "hit_quality.clean_margin", "--max-candidates", "20"],
                ["baseline", "--runs", "1", "--progression-runs", "1", "--team-runs", "1", "--output", str(baseline_dir)],
            ]
            for command in commands:
                with self.subTest(command=command), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(command), 0)
                    self.assertEqual(before, protected_fingerprint())
            followups = [
                ["replay", "--artifact", str(baseline_dir / "replays" / "sample-duel.json")],
                ["replay", "--artifact", str(baseline_dir / "replays" / "sample-team-control.json")],
                ["regress", "--accepted", str(baseline_dir / "baseline.json"), "--current", str(baseline_dir / "baseline.json")],
                ["report", "--input", str(baseline_dir / "baseline.json"), "--output", str(root / "report.html")],
            ]
            for command in followups:
                with self.subTest(command=command), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(command), 0)
                    self.assertEqual(before, protected_fingerprint())

    def test_baseline_artifacts_and_html_generate(self) -> None:
        before = protected_fingerprint()
        result = build_baseline(self.parameters, master_seed=7, runs=4, progression_runs=1)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            paths = write_baseline_artifacts(output, result)
            render_html(result, output / "report.html")
            self.assertTrue(Path(paths["baseline"]).is_file())
            self.assertTrue((output / "report.html").is_file())
            report_html = (output / "report.html").read_text(encoding="utf-8")
            self.assertIn("All 26 specialties", report_html)
            self.assertIn("Armor survivability distributions", report_html)
            self.assertIn("Firing-mode, recoil and ammunition tradeoffs", report_html)
            self.assertIn("Structural specialty counterfactual measurements", report_html)
            self.assertIn("They do not claim objective completion or generic-dominance measurement", report_html)
            self.assertIn("Specialty objective execution", report_html)
            self.assertTrue((output / "specialty-objectives.csv").is_file())
            self.assertIn("OVERWATCH crossing", report_html)
            self.assertIn("<meta name=\"viewport\"", report_html)
            linked_artifacts = re.findall(r'href="([^"#]+)"', report_html)
            self.assertGreaterEqual(len(linked_artifacts), 10)
            self.assertTrue(all((output / href).is_file() for href in linked_artifacts))
            reloaded = read_json(Path(paths["baseline"]))
            self.assertEqual(reloaded["result_id"], result["result_id"])
            render_html(reloaded, output / "report-reloaded.html")
            self.assertEqual((output / "report.html").read_bytes(), (output / "report-reloaded.html").read_bytes())
            self.assertEqual(self.manifest.load_set(read_json(Path(paths["parameters"]))).hash, self.parameters.hash)
        self.assertEqual(before, protected_fingerprint())

    def test_parallel_baseline_matches_serial_simulation_outputs(self) -> None:
        serial = build_baseline(
            self.parameters,
            master_seed=73,
            runs=3,
            progression_runs=3,
            team_runs=3,
            workers=1,
        )
        parallel = build_baseline(
            self.parameters,
            master_seed=73,
            runs=3,
            progression_runs=3,
            team_runs=3,
            workers=2,
        )
        self.assertEqual(serial["monte_carlo"], parallel["monte_carlo"])
        self.assertEqual(serial["progression"], parallel["progression"])
        self.assertEqual(
            serial["mixed_team_objectives"],
            parallel["mixed_team_objectives"],
        )
        self.assertEqual(2, parallel["run_configuration"]["worker_processes"])

    def test_smoke_run_is_clearly_non_qualifying(self) -> None:
        result = paired_mirrored_duels(DuelSpec(level=1), self.parameters, 5, 4, capture_replay=False)
        self.assertEqual(result["qualification"], "SMOKE_NON_QUALIFYING")


if __name__ == "__main__":
    unittest.main()
