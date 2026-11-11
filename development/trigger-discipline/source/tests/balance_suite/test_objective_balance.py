from __future__ import annotations

from dataclasses import replace
import unittest

from balance_suite.engine import EventEngine
from balance_suite.fixtures import benchmark_actor
from balance_suite.geometry import GridMap
from balance_suite.model import Position, Side, SimulationState
from balance_suite.objective_tactical import (
    OBJECTIVE_SPAWNS,
    _build_state,
    _extract_action,
    _record_move,
    _side_metrics,
)
from balance_suite.optimizer import objective_automaticity_failure, optimize_objective
from balance_suite.parameters import default_manifest
from balance_suite.runner import build_baseline
from balance_suite.tactical import default_team_spec, paired_mirrored_team_scenarios, run_team_scenario


class ObjectiveBalanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parameters = default_manifest().seed_set()

    def _movement_fixture(self) -> tuple[EventEngine, GridMap, object, dict[str, object]]:
        grid = GridMap("OBJECTIVE-MOVEMENT-TEST", 30, 20)
        breacher = benchmark_actor("BREACHER", 10, Side.A, Position(2, 10), self.parameters, actor_suffix="1", weapon_id="shotgun")
        defender = benchmark_actor("RIFLEMAN", 10, Side.B, Position(12, 10), self.parameters, actor_suffix="1", weapon_id="service_rifle")
        state = SimulationState("OBJECTIVE-MOVEMENT-TEST", "1.0.0", {breacher.actor_id: breacher, defender.actor_id: defender})
        metrics: dict[str, object] = {
            "by_side": {"A": _side_metrics(), "B": _side_metrics()},
            "by_actor": {
                actor.actor_id: {"side": actor.side.value, "ap_spent": 0, "ap_by_action": {}, "movement_m": 0.0}
                for actor in state.actors.values()
            },
        }
        return EventEngine(state, self.parameters, 17, True), grid, breacher, metrics

    def test_breacher_close_move_requires_known_enemy_and_genuine_closing(self) -> None:
        engine, grid, breacher, metrics = self._movement_fixture()
        self.assertTrue(
            _record_move(
                engine,
                grid,
                breacher,
                Position(12, 10),
                metrics,
                known_enemy=Position(12, 10),
                policy_id="POLICY_PRESSURE",
            )
        )
        closing = engine.log[-1].to_dict()
        self.assertTrue(closing["metrics"]["closing_on_known_enemy"])
        self.assertEqual(closing["procedure_stages"][0]["rate_m_per_ap"], 6.0)

        engine, grid, breacher, metrics = self._movement_fixture()
        self.assertTrue(
            _record_move(
                engine,
                grid,
                breacher,
                Position(2, 16),
                metrics,
                known_enemy=Position(12, 10),
                policy_id="POLICY_FLANK",
            )
        )
        lateral = engine.log[-1].to_dict()
        self.assertFalse(lateral["metrics"]["closing_on_known_enemy"])
        self.assertEqual(lateral["procedure_stages"][0]["rate_m_per_ap"], 4.0)

    def test_extraction_does_not_silently_reassign_a_declared_combat_actor(self) -> None:
        spec = default_team_spec("TEAM-EXTRACT-4V4-V1")
        state, grid, policies, attacker = _build_state(spec, self.parameters)
        casualty_id = state.objective["casualty_id"]
        role_ids = {
            actor_id
            for actor_id, policy_id in policies.items()
            if state.actors[actor_id].side is attacker
            and actor_id != casualty_id
            and policy_id in {"POLICY_SUPPORT", "POLICY_OBJECTIVE"}
        }
        self.assertTrue(role_ids)
        for actor_id in role_ids:
            state.actors[actor_id].downed = True
        metrics = {
            "by_side": {"A": _side_metrics(), "B": _side_metrics()},
            "by_actor": {
                actor.actor_id: {"side": actor.side.value, "ap_spent": 0, "ap_by_action": {}, "movement_m": 0.0}
                for actor in state.actors.values()
            },
        }
        engine = EventEngine(state, self.parameters, 23, True)
        self.assertFalse(_extract_action(engine, grid, attacker, policies, metrics))
        self.assertIsNone(state.objective["carrier_id"])

    def test_breach_fixture_assigns_durable_actor_to_objective_and_spends_full_team_turns(self) -> None:
        spec = replace(default_team_spec("TEAM-BREACH-4V4-V1"), max_team_turns=4)
        result = run_team_scenario(spec, self.parameters, 19, capture_events=True)
        objective_actor_id = next(
            actor_id
            for actor_id, policy_id in result["policies"].items()
            if policy_id == "POLICY_OBJECTIVE" and result["metrics"]["by_actor"][actor_id]["side"] == result["attacker_side"]
        )
        self.assertEqual(result["metrics"]["by_actor"][objective_actor_id]["class_id"], "BULWARK")
        support_defender = next(
            actor_id
            for actor_id, policy_id in result["policies"].items()
            if policy_id == "POLICY_SUPPORT" and result["metrics"]["by_actor"][actor_id]["side"] != result["attacker_side"]
        )
        self.assertGreater(result["metrics"]["by_actor"][support_defender]["ap_spent"], spec.max_team_turns * 2)
        pressure_events = [
            event
            for event in result["events"]
            if event["choices"].get("policy_id") == "POLICY_PRESSURE"
        ]
        self.assertIn("BREACH", {event["action"] for event in pressure_events})

    def test_breach_objective_consumes_tactical_breaching_entry(self) -> None:
        base = replace(
            default_team_spec("TEAM-BREACH-4V4-V1"),
            max_team_turns=4,
        )
        candidate = run_team_scenario(
            replace(
                base,
                specialty_nodes_a=(
                    ("specialty.breaker.entry.tactical_breaching",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            19,
            capture_events=True,
        )
        effects = candidate["metrics"]["specialty_effects"]
        self.assertTrue(effects)
        self.assertEqual(
            {"specialty.breaker.entry.tactical_breaching"},
            {effect["node_id"] for effect in effects},
        )
        self.assertTrue(
            all(effect["before"] > effect["after"] for effect in effects)
        )

    def test_control_objective_consumes_gunner_sustained_fire_entry(self) -> None:
        base = replace(
            default_team_spec("TEAM-CONTROL-4V4-V1", 1),
            weapons_a=("lmg", "service_rifle", "smg", "pistol"),
            policies_a=(
                "POLICY_VOLUME",
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_FLANK",
                "POLICY_SUPPORT",
            ),
            specialty_nodes_a=(("specialty.gunner.entry.sustained_fire_discipline",), (), (), ()),
        )
        result = run_team_scenario(
            base,
            self.parameters,
            17442464949847382173,
            capture_events=True,
        )
        effects = result["metrics"]["specialty_effects"]
        self.assertTrue(effects)
        self.assertEqual(
            {"specialty.gunner.entry.sustained_fire_discipline"},
            {effect["node_id"] for effect in effects},
        )
        self.assertTrue(
            all(effect["field"] == "recoil_after" for effect in effects)
        )

    def test_control_objective_consumes_anchor_strongpoint_entry(self) -> None:
        base = replace(
            default_team_spec("TEAM-CONTROL-4V4-V1", 1),
            policies_a=(
                "POLICY_STRONGPOINT",
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_FLANK",
                "POLICY_SUPPORT",
            ),
            specialty_nodes_a=(("specialty.anchor.entry.establish_strongpoint",), (), (), ()),
        )
        result = run_team_scenario(
            base,
            self.parameters,
            14494819236818216554,
            capture_events=True,
        )
        effects = result["metrics"]["specialty_effects"]
        self.assertTrue(effects)
        self.assertEqual(
            {"specialty.anchor.entry.establish_strongpoint"},
            {effect["node_id"] for effect in effects},
        )
        setups = [
            stage
            for event in result["events"]
            if event["action"] == "ESTABLISH_STRONGPOINT"
            for stage in event["procedure_stages"]
            if stage["stage"] == "STRONGPOINT"
        ]
        self.assertTrue(setups)
        self.assertTrue(all(not stage["creates_cover"] for stage in setups))

    def test_control_objective_consumes_operator_reload_entry(self) -> None:
        base = replace(
            default_team_spec("TEAM-CONTROL-4V4-V1", 1),
            classes_a=("RIFLEMAN", "RIFLEMAN", "GHOST", "CORPSMAN"),
            weapons_a=("manual_precision_rifle", "service_rifle", "smg", "pistol"),
            policies_a=(
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_FLANK",
                "POLICY_SUPPORT",
            ),
            initial_magazine_rounds_a=(0, None, None, None),
            specialty_nodes_a=(("specialty.operator.entry.weapon_manipulation",), (), (), ()),
        )
        result = run_team_scenario(
            base,
            self.parameters,
            3298323367096250025,
            capture_events=True,
        )
        effects = result["metrics"]["specialty_effects"]
        self.assertEqual(1, len(effects))
        self.assertEqual("RELOAD", effects[0]["action"])
        self.assertEqual((2, 1), (effects[0]["before"], effects[0]["after"]))

    def test_control_objective_consumes_sentinel_prepared_sightline_entry(self) -> None:
        base = replace(
            default_team_spec("TEAM-CONTROL-4V4-V1", 1),
            classes_a=("SHARPSHOOTER", "RIFLEMAN", "GHOST", "CORPSMAN"),
            weapons_a=("precision_rifle", "service_rifle", "smg", "pistol"),
            policies_a=(
                "POLICY_PREPARED_FIRE",
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_FLANK",
                "POLICY_SUPPORT",
            ),
            specialty_nodes_a=(("specialty.sentinel.entry.prepared_sightline",), (), (), ()),
        )
        result = run_team_scenario(
            base,
            self.parameters,
            9663061392819288042,
            capture_events=True,
        )
        effects = result["metrics"]["specialty_effects"]
        self.assertTrue(effects)
        self.assertTrue(
            all(effect["action"] == "OVERWATCH+AIM" for effect in effects)
        )
        self.assertTrue(
            all((effect["before"], effect["after"]) == (3, 2) for effect in effects)
        )

    def test_control_objective_consumes_snapshot_quick_solution_entry(self) -> None:
        base = replace(
            default_team_spec("TEAM-CONTROL-4V4-V1", 1),
            classes_a=("SHARPSHOOTER", "RIFLEMAN", "GHOST", "CORPSMAN"),
            weapons_a=("precision_rifle", "service_rifle", "smg", "pistol"),
            policies_a=(
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_FLANK",
                "POLICY_SUPPORT",
            ),
            specialty_nodes_a=(("specialty.snapshot.entry.quick_solution",), (), (), ()),
        )
        result = run_team_scenario(
            base,
            self.parameters,
            11813868576170050864,
            capture_events=True,
        )
        effects = result["metrics"]["specialty_effects"]
        self.assertTrue(effects)
        self.assertTrue(all(effect["action"] == "AIM" for effect in effects))
        self.assertTrue(
            all((effect["before"], effect["after"]) == (1, 0) for effect in effects)
        )
        self.assertNotIn("deadlock", result["terminal_reason"])

    def test_breach_objective_consumes_shock_assault_momentum_entry(self) -> None:
        base = replace(
            default_team_spec("TEAM-BREACH-4V4-V1", 1),
            weapons_a=("service_rifle", "service_rifle", "service_rifle", "pistol"),
            policies_a=(
                "POLICY_ASSAULT_MOMENTUM",
                "POLICY_FIRE_DISCIPLINE",
                "POLICY_OBJECTIVE",
                "POLICY_SUPPORT",
            ),
            specialty_nodes_a=(("specialty.shock.entry.assault_momentum",), (), (), ()),
        )
        result = run_team_scenario(
            base,
            self.parameters,
            875581880393899565,
            capture_events=True,
        )
        effects = result["metrics"]["specialty_effects"]
        self.assertEqual(1, len(effects))
        self.assertEqual("specialty.shock.entry.assault_momentum", effects[0]["node_id"])
        self.assertEqual("MOVE", effects[0]["action"])
        self.assertEqual((1, 0), (effects[0]["before"], effects[0]["after"]))
        suppression = next(
            event
            for event in result["events"]
            if event["actor_id"] == effects[0]["actor_id"]
            and event["action"] == "SUPPRESS"
        )
        commitment = next(
            stage
            for stage in suppression["procedure_stages"]
            if stage["stage"] == "SUPPRESSION COMMITMENT"
        )
        self.assertTrue(commitment["target_position_occupied"])

    def test_social_objective_consumes_diplomat_handler_and_icon_entries(self) -> None:
        cases = (
            (
                "SPECIALTY-DIPLOMAT-ENTRY",
                "specialty.diplomat.entry.negotiating_position",
                15845939401592425111,
                "counteroffers_after_rejection",
            ),
            (
                "SPECIALTY-HANDLER-ENTRY",
                "specialty.handler.entry.work_a_contact",
                15712802823890502160,
                "relationship_leverage",
            ),
            (
                "SPECIALTY-ICON-ENTRY",
                "specialty.icon.entry.recognition",
                6069951642820727038,
                "setup_ap",
            ),
        )
        for fixture_id, node_id, seed, expected_field in cases:
            with self.subTest(fixture_id=fixture_id):
                base = replace(
                    default_team_spec("TEAM-SOCIAL-4V4-V1", 1),
                    objective_fixture_id=fixture_id,
                )
                control = run_team_scenario(
                    base,
                    self.parameters,
                    seed,
                    capture_events=True,
                )
                candidate = run_team_scenario(
                    replace(
                        base,
                        specialty_nodes_a=((node_id,), (), (), ()),
                    ),
                    self.parameters,
                    seed,
                    capture_events=True,
                )
                self.assertFalse(control["metrics"]["specialty_effects"])
                effects = candidate["metrics"]["specialty_effects"]
                self.assertTrue(effects)
                self.assertEqual({node_id}, {effect["node_id"] for effect in effects})
                self.assertIn(expected_field, {effect["field"] for effect in effects})
                self.assertEqual(1, candidate["event_count"])
                self.assertFalse(candidate["metrics"]["invariant_failures"])

    def test_recon_objective_consumes_autodrone_and_fpv_entries(self) -> None:
        base = default_team_spec("TEAM-RECON-HACK-4V4-V1", 1)
        cases = (
            (
                "SPECIALTY-AUTODRONES-ENTRY",
                "specialty.autodrones.entry.tactical_autonomy",
                11,
                {"conditional_responses"},
                "ASSIGN STANDING ORDER",
                1,
                1,
            ),
            (
                "SPECIALTY-FPV_DRONES-ENTRY",
                "specialty.fpv_drones.entry.manual_pilot",
                13,
                {"difficulty", "movement_m_per_ap"},
                "MANUAL PILOT",
                2,
                1,
            ),
        )
        for (
            fixture_id,
            node_id,
            seed,
            expected_fields,
            action,
            control_ap,
            candidate_ap,
        ) in cases:
            with self.subTest(fixture_id=fixture_id):
                fixture = replace(
                    base,
                    classes_a=("TECH", *base.classes_a[1:]),
                    objective_fixture_id=fixture_id,
                )
                control = run_team_scenario(
                    fixture,
                    self.parameters,
                    seed,
                    capture_events=True,
                )
                candidate = run_team_scenario(
                    replace(
                        fixture,
                        specialty_nodes_a=((node_id,), (), (), ()),
                    ),
                    self.parameters,
                    seed,
                    capture_events=True,
                )
                self.assertFalse(control["metrics"]["specialty_effects"])
                effects = candidate["metrics"]["specialty_effects"]
                self.assertEqual({node_id}, {effect["node_id"] for effect in effects})
                self.assertEqual(expected_fields, {effect["field"] for effect in effects})
                self.assertEqual(
                    control_ap,
                    control["metrics"]["by_side"]["A"]["ap_by_action"][action],
                )
                self.assertEqual(
                    candidate_ap,
                    candidate["metrics"]["by_side"]["A"]["ap_by_action"][action],
                )
                self.assertFalse(candidate["metrics"]["unsupported_events"])
                self.assertFalse(candidate["metrics"]["invariant_failures"])

    def test_field_engineer_reconfigures_deployed_relay_for_one_less_ap(self) -> None:
        base = default_team_spec("TEAM-RECON-HACK-4V4-V1", 1)
        fixture = replace(
            base,
            classes_a=("TECH", *base.classes_a[1:]),
            objective_fixture_id="SPECIALTY-FIELD_ENGINEER-ENTRY",
        )
        control = run_team_scenario(
            fixture,
            self.parameters,
            19,
            capture_events=True,
        )
        candidate = run_team_scenario(
            replace(
                fixture,
                specialty_nodes_a=(
                    ("specialty.field_engineer.entry.field_work",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            19,
            capture_events=True,
        )

        self.assertEqual(
            3,
            control["metrics"]["by_side"]["A"]["ap_by_action"][
                "RECONFIGURE RELAY"
            ],
        )
        self.assertEqual(
            2,
            candidate["metrics"]["by_side"]["A"]["ap_by_action"][
                "RECONFIGURE RELAY"
            ],
        )
        self.assertEqual(
            [
                {
                    "actor_id": "A_NOVA_1",
                    "node_id": "specialty.field_engineer.entry.field_work",
                    "action": "RECONFIGURE RELAY",
                    "field": "ap_cost",
                    "before": 3,
                    "after": 2,
                }
            ],
            candidate["metrics"]["specialty_effects"],
        )
        stage = next(
            event
            for event in candidate["events"]
            if event["action"] == "RECONFIGURE RELAY"
        )["procedure_stages"][0]
        self.assertEqual("NORTH", stage["orientation_before"])
        self.assertEqual("EAST", stage["orientation_after"])
        self.assertEqual(stage["capabilities_before"], stage["capabilities_after"])
        self.assertTrue(stage["already_deployed"])
        self.assertFalse(stage["ordinary_deployment"])
        self.assertTrue(stage["tools_available"])
        self.assertTrue(stage["physical_access"])
        self.assertTrue(stage["compatible_equipment"])
        self.assertFalse(candidate["metrics"]["unsupported_events"])
        self.assertFalse(candidate["metrics"]["invariant_failures"])

    def test_extract_objective_consumes_resuscitator_and_trauma_entries(self) -> None:
        base = default_team_spec("TEAM-EXTRACT-4V4-V1", 1)

        resuscitation_fixture = replace(
            base,
            objective_fixture_id="SPECIALTY-RESUSCITATOR-ENTRY",
        )
        resuscitation_control = run_team_scenario(
            resuscitation_fixture,
            self.parameters,
            1,
            capture_events=True,
        )
        resuscitation_candidate = run_team_scenario(
            replace(
                resuscitation_fixture,
                specialty_nodes_a=(
                    ("specialty.resuscitator.entry.resuscitation",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            1,
            capture_events=True,
        )
        self.assertIn("window expired", resuscitation_control["terminal_reason"])
        self.assertIn("extracted stabilized casualty", resuscitation_candidate["terminal_reason"])
        self.assertEqual(
            2,
            resuscitation_control["metrics"]["by_side"]["A"]["ap_by_action"][
                "RESUSCITATION"
            ],
        )
        self.assertEqual(
            5,
            resuscitation_candidate["metrics"]["by_side"]["A"]["ap_by_action"][
                "RESUSCITATION"
            ],
        )
        self.assertEqual(
            [
                {
                    "actor_id": "A_SOLACE_1",
                    "node_id": "specialty.resuscitator.entry.resuscitation",
                    "action": "RESUSCITATION",
                    "field": "reversible_window_held",
                    "before": False,
                    "after": True,
                }
            ],
            resuscitation_candidate["metrics"]["specialty_effects"],
        )

        trauma_fixture = replace(
            base,
            objective_fixture_id="SPECIALTY-TRAUMA-ENTRY",
        )
        trauma_control = run_team_scenario(
            trauma_fixture,
            self.parameters,
            37,
            capture_events=True,
        )
        trauma_candidate = run_team_scenario(
            replace(
                trauma_fixture,
                specialty_nodes_a=(
                    ("specialty.trauma.entry.trauma_care",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            37,
            capture_events=True,
        )
        self.assertFalse(trauma_control["metrics"]["specialty_effects"])
        self.assertEqual(
            {"injury_recovery_steps"},
            {
                effect["field"]
                for effect in trauma_candidate["metrics"]["specialty_effects"]
            },
        )
        self.assertEqual(
            1,
            trauma_candidate["metrics"]["by_side"]["A"][
                "injury_recovery_steps"
            ],
        )
        self.assertFalse(trauma_candidate["metrics"]["unsupported_events"])
        self.assertFalse(trauma_candidate["metrics"]["invariant_failures"])

    def test_field_medic_restores_one_res_once_without_erasing_forearm_injury(self) -> None:
        fixture = replace(
            default_team_spec("TEAM-EXTRACT-4V4-V1", 1),
            objective_fixture_id="SPECIALTY-FIELD_MEDIC-ENTRY",
            max_team_turns=2,
        )
        control = run_team_scenario(
            fixture,
            self.parameters,
            1,
            capture_events=True,
        )
        candidate = run_team_scenario(
            replace(
                fixture,
                specialty_nodes_a=(
                    ("specialty.field_medic.entry.combat_medicine",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            1,
            capture_events=True,
        )

        self.assertFalse(control["metrics"]["specialty_effects"])
        self.assertEqual(
            [
                {
                    "actor_id": "A_SOLACE_1",
                    "node_id": "specialty.field_medic.entry.combat_medicine",
                    "action": "COMBAT MEDICINE",
                    "field": "res",
                    "before": 4,
                    "after": 5,
                }
            ],
            candidate["metrics"]["specialty_effects"],
        )
        treatment = next(
            event for event in candidate["events"] if event["action"] == "COMBAT MEDICINE"
        )["procedure_stages"][0]
        self.assertEqual("INJURY_REPAIRABLE_FOREARM_TRAUMA_V1", treatment["injury_id"])
        self.assertEqual(treatment["injury_before"], treatment["injury_after"])
        self.assertEqual(treatment["hp_before"], treatment["hp_after"])
        self.assertEqual(1, treatment["res_restored"])
        self.assertTrue(treatment["distinct_treatment_consumed"])
        self.assertEqual(
            1,
            sum(
                event["action"] == "COMBAT MEDICINE"
                for event in candidate["events"]
            ),
        )
        self.assertFalse(candidate["metrics"]["unsupported_events"])
        self.assertFalse(candidate["metrics"]["invariant_failures"])

    def test_control_objective_consumes_physical_entry_fixtures(self) -> None:
        base = default_team_spec("TEAM-CONTROL-4V4-V1", 1)
        cases = (
            (
                "ARSENAL",
                "RIFLEMAN",
                "specialty.arsenal.entry.fighting_load",
                41,
                "CONFIGURE FIGHTING LOAD",
                {"additional_accessible_firearms"},
            ),
            (
                "FLANKER",
                "GHOST",
                "specialty.flanker.entry.angle_advantage",
                43,
                "ANGLE ADVANTAGE FIRE",
                {"attack_difficulty"},
            ),
            (
                "GUARDIAN",
                "BULWARK",
                "specialty.guardian.entry.interpose",
                47,
                "INTERPOSE TRIGGER",
                {"prepared_before_threat", "prepared_movement_m"},
            ),
            (
                "INFILTRATOR",
                "GHOST",
                "specialty.infiltrator.entry.approach_plan",
                53,
                "FOLLOW APPROACH",
                {"stealth_difficulty"},
            ),
            (
                "SHIELD_BEARER",
                "BULWARK",
                "specialty.shield_bearer.entry.shield_training",
                59,
                "DEPLOY SHIELD",
                {"effective_burden", "effective_handling"},
            ),
        )
        for specialty, class_id, node_id, seed, action, expected_fields in cases:
            with self.subTest(specialty=specialty):
                fixture = replace(
                    base,
                    classes_a=(class_id, *base.classes_a[1:]),
                    objective_fixture_id=f"SPECIALTY-{specialty}-ENTRY",
                    max_team_turns=2,
                )
                control = run_team_scenario(
                    fixture,
                    self.parameters,
                    seed,
                    capture_events=True,
                )
                candidate = run_team_scenario(
                    replace(
                        fixture,
                        specialty_nodes_a=((node_id,), (), (), ()),
                    ),
                    self.parameters,
                    seed,
                    capture_events=True,
                )
                self.assertFalse(control["metrics"]["specialty_effects"])
                effects = candidate["metrics"]["specialty_effects"]
                self.assertEqual({node_id}, {effect["node_id"] for effect in effects})
                self.assertEqual(expected_fields, {effect["field"] for effect in effects})
                entry = next(
                    event for event in candidate["events"] if event["action"] == action
                )
                self.assertTrue(entry["procedure_stages"])
                self.assertFalse(candidate["metrics"]["unsupported_events"])
                self.assertFalse(candidate["metrics"]["invariant_failures"])

                if specialty == "ARSENAL":
                    physical = entry["procedure_stages"][0]
                    self.assertTrue(all(item["physically_carried"] for item in physical["firearms"]))
                    self.assertFalse(physical["burden_ignored"])
                elif specialty == "FLANKER":
                    geometry = entry["procedure_stages"][0]
                    self.assertTrue(geometry["reference_line_protected"])
                    self.assertFalse(geometry["attack_line_protected"])
                    self.assertTrue(geometry["valid_firing_line"])
                elif specialty == "GUARDIAN":
                    interpose = entry["procedure_stages"][0]
                    self.assertTrue(interpose["prepared_before_threat"])
                    self.assertTrue(interpose["physically_interposed"])
                elif specialty == "INFILTRATOR":
                    approach = entry["procedure_stages"][0]
                    self.assertTrue(approach["observed_threat_ids"])
                    self.assertEqual(
                        approach["route_revision"],
                        approach["assessed_route_revision"],
                    )
                elif specialty == "SHIELD_BEARER":
                    shield = entry["procedure_stages"][0]
                    self.assertEqual(16, shield["cover_hp"])
                    self.assertEqual(4, shield["material_resistance"])
                    self.assertEqual(4, shield["physical_burden"])
                    self.assertEqual(4, shield["physical_handling"])

    def test_sniper_deep_aim_costs_one_extra_ap_and_binds_the_solution(self) -> None:
        base = default_team_spec("TEAM-CONTROL-4V4-V1", 1)
        fixture = replace(
            base,
            classes_a=("SHARPSHOOTER", *base.classes_a[1:]),
            weapons_a=("manual_precision_rifle", *base.weapons_a[1:]),
            objective_fixture_id="SPECIALTY-SNIPER-ENTRY",
        )
        candidate_spec = replace(
            fixture,
            specialty_nodes_a=(
                ("specialty.sniper.entry.deep_aim",),
                (),
                (),
                (),
            ),
        )
        control = run_team_scenario(
            fixture,
            self.parameters,
            23,
            capture_events=True,
        )
        candidate = run_team_scenario(
            candidate_spec,
            self.parameters,
            23,
            capture_events=True,
        )

        self.assertEqual(
            1,
            control["metrics"]["by_side"]["A"]["ap_by_action"][
                "BUILD FIRING SOLUTION"
            ],
        )
        self.assertEqual(
            2,
            candidate["metrics"]["by_side"]["A"]["ap_by_action"][
                "BUILD FIRING SOLUTION"
            ],
        )
        effects = candidate["metrics"]["specialty_effects"]
        self.assertEqual(
            {"additional_ap", "attack_difficulty"},
            {effect["field"] for effect in effects},
        )
        difficulty = next(
            effect for effect in effects if effect["field"] == "attack_difficulty"
        )
        self.assertEqual((14, 12), (difficulty["before"], difficulty["after"]))
        stage = next(
            event
            for event in candidate["events"]
            if event["action"] == "BUILD FIRING SOLUTION"
        )["procedure_stages"][0]
        self.assertEqual(1, stage["ordinary_aim_ap"])
        self.assertEqual(1, stage["deep_aim_additional_ap"])
        self.assertTrue(stage["solution_active"])
        self.assertEqual(stage["firing_position"], stage["bound_firing_position"])
        self.assertEqual(stage["target_id"], stage["bound_target_id"])

        moved = run_team_scenario(
            replace(
                candidate_spec,
                objective_fixture_id="SPECIALTY-SNIPER-ENTRY-MOVED",
            ),
            self.parameters,
            23,
            capture_events=True,
        )
        moved_stage = next(
            event
            for event in moved["events"]
            if event["action"] == "BUILD FIRING SOLUTION"
        )["procedure_stages"][0]
        self.assertTrue(moved_stage["material_change"])
        self.assertFalse(moved_stage["solution_active"])
        self.assertFalse(candidate["metrics"]["unsupported_events"])
        self.assertFalse(candidate["metrics"]["invariant_failures"])

    def test_evader_break_contact_requires_every_observer_to_lose_los(self) -> None:
        base = default_team_spec("TEAM-CONTROL-4V4-V1", 1)
        fixture = replace(
            base,
            classes_a=("GHOST", *base.classes_a[1:]),
            objective_fixture_id="SPECIALTY-EVADER-ENTRY",
        )
        candidate_spec = replace(
            fixture,
            specialty_nodes_a=(
                ("specialty.evader.entry.break_contact",),
                (),
                (),
                (),
            ),
        )
        control = run_team_scenario(
            fixture,
            self.parameters,
            29,
            capture_events=True,
        )
        candidate = run_team_scenario(
            candidate_spec,
            self.parameters,
            29,
            capture_events=True,
        )

        self.assertEqual(
            2,
            control["metrics"]["by_side"]["A"]["ap_by_action"][
                "BREAK CONTACT"
            ],
        )
        self.assertEqual(
            1,
            candidate["metrics"]["by_side"]["A"]["ap_by_action"][
                "BREAK CONTACT"
            ],
        )
        self.assertEqual(
            {"ap_cost", "stealth_difficulty"},
            {
                effect["field"]
                for effect in candidate["metrics"]["specialty_effects"]
            },
        )
        event = next(
            event for event in candidate["events"] if event["action"] == "BREAK CONTACT"
        )
        stage = event["procedure_stages"][0]
        self.assertEqual(1, stage["movement_ap"])
        self.assertEqual(1, stage["stealth_ap"])
        self.assertEqual((14, 12), (stage["difficulty_before"], stage["difficulty_after"]))
        self.assertTrue(all(stage["observer_los_before"].values()))
        self.assertFalse(any(stage["observer_los_after"].values()))
        self.assertTrue(stage["every_exact_observer_lost_los"])

        observed = run_team_scenario(
            replace(
                candidate_spec,
                objective_fixture_id="SPECIALTY-EVADER-ENTRY-OBSERVED",
            ),
            self.parameters,
            29,
            capture_events=True,
        )
        self.assertNotIn(
            "BREAK CONTACT",
            observed["metrics"]["by_side"]["A"]["ap_by_action"],
        )
        self.assertFalse(observed["metrics"]["specialty_effects"])
        self.assertIn(
            "one or more exact observers retain LOS",
            {item["reason"] for item in observed["metrics"]["unsupported_events"]},
        )
        self.assertFalse(candidate["metrics"]["unsupported_events"])
        self.assertFalse(candidate["metrics"]["invariant_failures"])

    def test_breach_objective_consumes_enforcer_weapon_retention(self) -> None:
        fixture = replace(
            default_team_spec("TEAM-BREACH-4V4-V1", 1),
            objective_fixture_id="SPECIALTY-ENFORCER-ENTRY",
            max_team_turns=2,
        )
        control = run_team_scenario(
            fixture,
            self.parameters,
            10,
            capture_events=True,
        )
        candidate = run_team_scenario(
            replace(
                fixture,
                specialty_nodes_a=(
                    ("specialty.enforcer.entry.weapon_retention",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            10,
            capture_events=True,
        )
        control_attempt = next(
            event
            for event in control["events"]
            if event["action"] == "DISARM HELD FIREARM"
        )["procedure_stages"][0]
        candidate_attempt = next(
            event
            for event in candidate["events"]
            if event["action"] == "DISARM HELD FIREARM"
        )["procedure_stages"][0]
        self.assertTrue(control_attempt["check"]["success"])
        self.assertTrue(control_attempt["weapon_left_control"])
        self.assertFalse(candidate_attempt["check"]["success"])
        self.assertFalse(candidate_attempt["weapon_left_control"])
        self.assertFalse(candidate_attempt["weapon_transferred_to_attacker"])
        self.assertEqual(
            [
                {
                    "actor_id": "A_RIOT_1",
                    "node_id": "specialty.enforcer.entry.weapon_retention",
                    "action": "RESIST DISARM",
                    "field": "incoming_disarm_difficulty",
                    "before": 15,
                    "after": 17,
                }
            ],
            candidate["metrics"]["specialty_effects"],
        )
        self.assertFalse(candidate["metrics"]["unsupported_events"])
        self.assertFalse(candidate["metrics"]["invariant_failures"])

    def test_reference_objectives_are_not_automatic_under_common_smoke_seeds(self) -> None:
        for scenario_id in ("TEAM-EXTRACT-4V4-V1", "TEAM-BREACH-4V4-V1"):
            with self.subTest(scenario_id=scenario_id):
                result = paired_mirrored_team_scenarios(
                    default_team_spec(scenario_id),
                    self.parameters,
                    20260825,
                    30,
                    capture_replay=False,
                )
                self.assertFalse(objective_automaticity_failure(result["subject_outcome_share"]))

    def test_recon_objective_consumes_exact_networker_node_identity(self) -> None:
        base = default_team_spec("TEAM-RECON-HACK-4V4-V1")
        control = run_team_scenario(base, self.parameters, 1, capture_events=True)
        candidate = run_team_scenario(
            replace(
                base,
                specialty_nodes_a=(
                    ("specialty.networker.entry.map_the_links",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            1,
            capture_events=True,
        )
        self.assertEqual(
            1,
            control["metrics"]["by_side"]["A"]["network_nodes_discovered"],
        )
        self.assertEqual(
            3,
            candidate["metrics"]["by_side"]["A"]["network_nodes_discovered"],
        )

    def test_recon_objective_consumes_exact_local_hacker_node_identity(self) -> None:
        base = default_team_spec("TEAM-RECON-HACK-4V4-V1")
        control = run_team_scenario(base, self.parameters, 7, capture_events=True)
        candidate = run_team_scenario(
            replace(
                base,
                specialty_nodes_a=(
                    ("specialty.local_hacker.entry.service_access",),
                    (),
                    (),
                    (),
                ),
            ),
            self.parameters,
            7,
            capture_events=True,
        )
        self.assertEqual(
            13,
            control["metrics"]["by_side"]["A"]["hacking_actions"],
        )
        self.assertEqual(
            6,
            candidate["metrics"]["by_side"]["A"]["hacking_actions"],
        )

    def test_objective_automaticity_requires_confident_tail_evidence(self) -> None:
        self.assertTrue(objective_automaticity_failure({"low": 0.96, "high": 1.0}))
        self.assertTrue(objective_automaticity_failure({"low": 0.0, "high": 0.04}))
        self.assertFalse(objective_automaticity_failure({"low": 0.0, "high": 0.7}))
        self.assertFalse(objective_automaticity_failure({"low": 0.08, "high": 0.2}))

    def test_objective_optimizer_retains_manifest_and_replay_provenance(self) -> None:
        result = optimize_objective(
            self.parameters,
            "TEAM-BREACH-4V4-V1",
            ["class.breacher.close_move_delta_m_per_ap"],
            max_candidates=10,
            master_seed=20260825,
            pairs=1,
        )
        self.assertEqual(result["non_manifest_mutation_count"], 0)
        self.assertEqual(result["qualification"], "SMOKE_NON_QUALIFYING")
        self.assertEqual(result["review_band"]["meaning"], "automaticity review, not a 50/50 target")
        self.assertTrue(set(result["best"]["updates"]).issubset(self.parameters.manifest.specs))
        self.assertIsNotNone(result["best"]["sample_replay"])
        self.assertEqual(self.parameters.manifest.load_set(result["best_parameter_values"]).hash, result["best"]["parameter_hash"])

    def test_baseline_can_reuse_a_validated_qualified_objective(self) -> None:
        override = paired_mirrored_team_scenarios(
            default_team_spec("TEAM-EXTRACT-4V4-V1"),
            self.parameters,
            29,
            1,
        )
        result = build_baseline(
            self.parameters,
            master_seed=29,
            runs=1,
            progression_runs=1,
            team_runs=1,
            objective_overrides={"TEAM-EXTRACT-4V4-V1": override},
        )
        reused = result["mixed_team_objectives"]["objective_scenarios"]["TEAM-EXTRACT-4V4-V1"]
        self.assertEqual(reused["sample_replay"]["event_log_hash"], override["sample_replay"]["event_log_hash"])
        self.assertEqual(result["run_configuration"]["objective_override_ids"], ["TEAM-EXTRACT-4V4-V1"])

        invalid = {**override, "parameter_set_hash": "not-the-current-parameter-hash"}
        with self.assertRaisesRegex(ValueError, "parameter hash mismatch"):
            build_baseline(
                self.parameters,
                master_seed=29,
                runs=1,
                progression_runs=1,
                team_runs=1,
                objective_overrides={"TEAM-EXTRACT-4V4-V1": invalid},
            )

    def test_objective_spawn_fixtures_are_explicit_and_passable(self) -> None:
        for scenario_id, placement in OBJECTIVE_SPAWNS.items():
            grid = _build_state(default_team_spec(scenario_id), self.parameters)[1]
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(len(placement["attacker"]), 4)
                self.assertEqual(len(placement["defender"]), 4)
                self.assertEqual(len(set(placement["attacker"] + placement["defender"])), 8)
                self.assertTrue(all(grid.in_bounds(position) and not grid.occupied((round(position.x), round(position.y))) for position in placement["attacker"] + placement["defender"]))


if __name__ == "__main__":
    unittest.main()
