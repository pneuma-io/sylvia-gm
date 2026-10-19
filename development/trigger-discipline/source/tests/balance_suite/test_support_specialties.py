from __future__ import annotations

import hashlib
import unittest

from balance_suite.fixtures import build_map, network_site_nodes
from balance_suite.model import ConditionState, DetectionState, KnowledgeState, LifeThreateningCondition, Position
from balance_suite.parameters import default_manifest
from balance_suite.provenance import source_drift
from balance_suite.scenarios import scenario_registry, validate_scenarios
from balance_suite.specialty_contracts import SPECIALTY_RULE_CONTRACTS
from balance_suite.specialty_audit import REJECTED_SPECIALTY_PARAMETER_ROWS
from balance_suite.specialty_manifest import (
    DEFERRED_SPECIALTY_PARAMETER_IDS,
    REJECTED_SPECIALTY_PARAMETER_IDS,
    parameter_id as specialty_parameter_id,
)
from balance_suite.specialties import (
    execute_specialty_counterfactual,
    parse_all_specialties,
    specialty_coverage,
)
from balance_suite.support import PoweredDevice, PersistentInjury, apply_salvage, casualty_movement, degrade_knowledge, discover_neighbors, grenade_scatter_distance, grenade_scatter_landing, intrusion_action, lose_exact_observation, medical_action, repairable_forearm_injury, reroute_power_budget, resolve_social_attempt, share_knowledge, valid_network_route


class SupportAndSpecialtyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parameters = default_manifest().seed_set()
        cls.coverage = specialty_coverage(cls.parameters)

    def test_confirmed_becomes_last_known_when_observation_breaks(self) -> None:
        self.assertEqual(lose_exact_observation(KnowledgeState.CONFIRMED), KnowledgeState.LAST_KNOWN)

    def test_team_sharing_cannot_increase_precision(self) -> None:
        self.assertEqual(share_knowledge(KnowledgeState.APPROXIMATE, KnowledgeState.CONFIRMED), KnowledgeState.APPROXIMATE)

    def test_suspicious_requires_fictional_reason_to_return_unaware(self) -> None:
        self.assertEqual(degrade_knowledge(KnowledgeState.SUSPICIOUS, bounded_area_still_credible=False, presence_still_relevant=True), KnowledgeState.SUSPICIOUS)
        self.assertEqual(degrade_knowledge(KnowledgeState.SUSPICIOUS, bounded_area_still_credible=False, presence_still_relevant=False), KnowledgeState.UNAWARE)

    def test_stabilize_changes_only_active_condition(self) -> None:
        condition = LifeThreateningCondition("BLEED", "bleed", ConditionState.ACTIVE, 6, "hemorrhage", 18)
        result = medical_action(condition, "STABILIZE", roll=20, skill=4, aptitude=1, parameters=self.parameters, corpsman=False, capable_intervention=True)
        self.assertTrue(result.success)
        self.assertEqual(condition.state, ConditionState.STABILIZED)

    def test_resuscitation_requires_reversible_window(self) -> None:
        condition = LifeThreateningCondition("AIRWAY", "airway", ConditionState.FAILED, 12, "airway", 24)
        result = medical_action(condition, "RESUSCITATION", roll=20, skill=8, aptitude=3, parameters=self.parameters, corpsman=True, capable_intervention=True, within_window=False)
        self.assertFalse(result.eligible)
        self.assertEqual(condition.state, ConditionState.FAILED)

    def test_network_route_respects_real_links(self) -> None:
        nodes = network_site_nodes()
        self.assertEqual(valid_network_route(nodes, "NET40-A", "NET40-C"), ["NET40-A", "NET40-B", "NET40-C"])
        nodes["NET40-B"].links.remove("NET40-C")
        nodes["NET40-C"].links.remove("NET40-B")
        self.assertIsNone(valid_network_route(nodes, "NET40-A", "NET40-C"))

    def test_map_the_links_only_reveals_neighbors(self) -> None:
        nodes = network_site_nodes()
        nodes["NET40-A"].compromised = True
        self.assertEqual(discover_neighbors(nodes, "NET40-A"), {"NET40-B"})
        self.assertFalse(nodes["NET40-C"].discovered)

    def test_social_impossible_never_succeeds(self) -> None:
        result = resolve_social_attempt(plausible=False, relevant_fact=True, leverage="decisive", roll=20, skill=8, aptitude=3, difficulty=10, parameters=self.parameters, influencer=True)
        self.assertFalse(result.success)

    def test_relevant_leverage_improves_plausible_attempt(self) -> None:
        control = resolve_social_attempt(plausible=True, relevant_fact=False, leverage=None, roll=10, skill=4, aptitude=1, difficulty=18, parameters=self.parameters, influencer=False)
        leveraged = resolve_social_attempt(plausible=True, relevant_fact=True, leverage="strong", roll=10, skill=4, aptitude=1, difficulty=18, parameters=self.parameters, influencer=False)
        self.assertLess(leveraged.difficulty, control.difficulty)

    def test_all_26_specialties_have_14_nodes(self) -> None:
        self.assertEqual(self.coverage["specialty_count"], 26)
        self.assertEqual(self.coverage["node_count"], 364)
        self.assertTrue(all(item["node_count"] == 14 for item in self.coverage["specialties"].values()))

    def test_approved_replacements_leave_no_explicit_unresolved_nodes(self) -> None:
        self.assertEqual(self.coverage["coverage_counts"].get("UNRESOLVED_SOURCE", 0), 0)

    def test_structural_counterfactuals_have_real_delta(self) -> None:
        self.assertGreater(len(self.coverage["counterfactuals"]), 0)
        self.assertTrue(all(item["delta_detected"] for item in self.coverage["counterfactuals"]))

    def test_specialty_counterfactual_executes_same_character_present_vs_absent(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.networker.tier_2.foothold"
        )
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=10,
        )
        self.assertEqual("EXECUTED", result["status"])
        self.assertEqual(
            result["control_character"]["character_id"],
            result["candidate_character"]["character_id"],
        )
        self.assertNotIn(
            definition["node_id"],
            result["control_character"]["specialty_nodes"],
        )
        self.assertIn(
            definition["node_id"],
            result["candidate_character"]["specialty_nodes"],
        )
        self.assertTrue(result["prerequisites_satisfied"])
        self.assertTrue(result["node_effect_applied"])
        self.assertEqual(definition["changed_fields"], result["measured_fields"])
        self.assertEqual(
            definition["mechanical_operations"],
            result["mechanical_operations_executed"],
        )
        self.assertNotEqual(result["control_result"], result["candidate_result"])

    def test_off_role_counterfactual_keeps_the_node_but_does_not_apply_it(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.networker.tier_2.foothold"
        )
        result = execute_specialty_counterfactual(
            definition,
            role="OFF_ROLE",
            level=10,
        )
        self.assertIn(
            definition["node_id"],
            result["candidate_character"]["specialty_nodes"],
        )
        self.assertTrue(result["prerequisites_satisfied"])
        self.assertFalse(result["failed_prerequisites"])
        self.assertFalse(result["role_relevant"])
        self.assertFalse(result["node_effect_applied"])
        self.assertEqual([], result["mechanical_operations_executed"])
        self.assertEqual(result["control_result"], result["candidate_result"])
        self.assertEqual([], result["measured_fields"])

    def test_counterfactuals_execute_declared_field_operations(self) -> None:
        for definition in self.coverage["counterfactuals"]:
            level = definition["available_benchmark_levels"][0]
            result = execute_specialty_counterfactual(
                definition,
                role="PRIMARY",
                level=level,
            )
            self.assertEqual(
                definition["changed_fields"],
                [item["field"] for item in result["mechanical_operations_executed"]],
            )
            self.assertEqual(definition["changed_fields"], result["measured_fields"])

    def test_counterfactual_candidates_are_derived_from_operations(self) -> None:
        for definition in self.coverage["counterfactuals"]:
            self.assertNotIn("candidate", definition)
            self.assertNotIn(
                "expected_before",
                {
                    field
                    for operation in definition["mechanical_operations"]
                    for field in operation
                },
            )

    def test_missing_specialty_prerequisite_blocks_the_node_delta(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.networker.tier_2.foothold"
        )
        field = definition["prerequisite_fields"][0]
        expected = definition["prerequisite_contract"][field]
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=10,
            prerequisite_overrides={field: not expected},
        )
        self.assertEqual("MISSING_PREREQUISITE", result["status"])
        self.assertEqual([field], result["failed_prerequisites"])
        self.assertFalse(result["node_effect_applied"])
        self.assertEqual([], result["mechanical_operations_executed"])
        self.assertEqual(result["control_result"], result["candidate_result"])

    def test_all_declared_limitations_and_negative_prerequisites_are_checked(self) -> None:
        self.assertTrue(
            all(item["limitations_preserved"] for item in self.coverage["scenario_matrix"])
        )
        self.assertTrue(self.coverage["negative_prerequisite_matrix"])
        for item in self.coverage["negative_prerequisite_matrix"]:
            self.assertEqual("MISSING_PREREQUISITE", item["status"])
            self.assertFalse(item["node_effect_applied"])
            self.assertEqual(item["control_result"], item["candidate_result"])

        self.assertEqual(
            sum(
                len(item["prerequisite_contract"])
                for item in self.coverage["counterfactuals"]
            ),
            len(self.coverage["negative_prerequisite_matrix"]),
        )
        self.assertEqual(
            sum(
                len(item["authored_constraint_fields"])
                for item in self.coverage["counterfactuals"]
            ),
            len(self.coverage["limitation_violation_matrix"]),
        )
        for item in self.coverage["limitation_violation_matrix"]:
            expected_status = {
                "GUARD": "GUARD_VIOLATION",
                "TERMINATION": "TERMINATED",
                "INVARIANT": "INVARIANT_VIOLATION",
            }[item["enforcement"]]
            self.assertEqual(expected_status, item["status"])
            self.assertFalse(item["limitations_preserved"])
            if item["enforcement"] == "GUARD":
                self.assertFalse(item["node_effect_applied"])
                self.assertEqual(item["control_result"], item["candidate_result"])
            elif item["enforcement"] == "TERMINATION":
                self.assertFalse(item["node_effect_applied"])
                self.assertTrue(item["effect_established_before_termination"])
                self.assertTrue(item["mechanical_operations_executed"])
            else:
                self.assertTrue(item["node_effect_applied"])
            failed = [
                result
                for result in item["limitation_results"]
                if not result["passed"]
            ]
            self.assertEqual([item["violated_field"]], [result["field"] for result in failed])

    def test_every_counterfactual_carries_its_exact_authored_rule_contract(self) -> None:
        self.assertEqual(364, len(SPECIALTY_RULE_CONTRACTS))
        self.assertTrue(
            all(
                row["prerequisites"] or row["constraints"] or row["effects"]
                for row in SPECIALTY_RULE_CONTRACTS.values()
            )
        )
        by_node_id = {
            node["node"]["node_id"]: node["node"]
            for specialty in self.coverage["specialties"].values()
            for node in specialty["nodes"]
        }
        parsed_by_node_id = {node.node_id: node for node in parse_all_specialties()}
        for definition in self.coverage["counterfactuals"]:
            contract = definition["authored_rule_contract"]
            node = by_node_id[definition["node_id"]]
            parsed = parsed_by_node_id[definition["node_id"]]
            self.assertEqual(node["source_excerpt"], contract["source_excerpt"])
            self.assertEqual(node["source_path"], contract["source_path"])
            self.assertEqual(node["source_sha256"], contract["source_sha256"])
            self.assertEqual(
                hashlib.sha256(parsed.canonical_span.encode("utf-8")).hexdigest(),
                contract["source_span_sha256"],
            )
            self.assertTrue(contract["effect_clause"])
            for clause in (
                contract["activation_clauses"]
                + contract["constraint_clauses"]
                + contract["effect_clauses"]
                + contract["accounted_clauses"]
            ):
                self.assertIn(clause["source_clause"], parsed.canonical_span)
            self.assertTrue(
                contract["activation_clauses"]
                or contract["constraint_clauses"]
                or contract["effect_clauses"]
                or definition["mechanical_operations"]
            )

    def test_inline_entry_headings_preserve_exact_canonical_spans(self) -> None:
        by_id = {node.node_id: node for node in parse_all_specialties()}
        expected = {
            "specialty.field_medic.entry.combat_medicine": "COMBAT MEDICINE",
            "specialty.local_hacker.entry.service_access": "SERVICE ACCESS",
            "specialty.resuscitator.entry.resuscitation": "RESUSCITATION",
        }
        for node_id, heading in expected.items():
            node = by_id[node_id]
            self.assertTrue(node.canonical_span.startswith(f"> **ENTRY — {heading}:**"))
            self.assertTrue(node.source_excerpt.startswith(f"{heading}: "))
            self.assertNotIn(f"{heading}::", node.source_excerpt)

    def test_networker_pivot_compiles_its_real_route_requirements(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.networker.tier_1.pivot"
        )
        clauses = " ".join(
            item["source_clause"]
            for item in definition["authored_rule_contract"]["activation_clauses"]
        ).lower()
        self.assertIn("immediately connected discovered node", clauses)
        self.assertIn("node already controlled", clauses)
        self.assertIn("link must actually exist and permit traffic", clauses)
        self.assertGreaterEqual(len(definition["prerequisite_contract"]), 2)

    def test_explicit_contracts_preserve_alternatives_without_heuristic_ands(self) -> None:
        rapid = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.arsenal.tier_4.rapid_changeover"
        )
        self.assertEqual(
            {"transitioned_to_another_fighting_load_firearm"},
            {
                item["field"]
                for item in rapid["authored_rule_contract"]["activation_clauses"]
            },
        )
        guaranteed = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.diplomat.tier_4.guaranteed_terms"
        )
        self.assertEqual(
            {"credible_collateral_attached"},
            {
                item["field"]
                for item in guaranteed["authored_rule_contract"]["activation_clauses"]
            },
        )

    def test_autonomous_recovery_requires_failure_and_a_predefined_fallback(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"]
            == "specialty.autodrones.tier_4.autonomous_recovery"
        )
        fields = set(definition["prerequisite_contract"])
        self.assertEqual(
            {"current_order_impossible", "predefined_secondary_order_available"},
            fields,
        )
        matrix = [
            item
            for item in self.coverage["negative_prerequisite_matrix"]
            if item["node_id"] == definition["node_id"]
        ]
        self.assertEqual(fields, {item["failed_field"] for item in matrix})
        self.assertTrue(all(not item["node_effect_applied"] for item in matrix))

    def test_authored_boundaries_have_explicit_mechanical_enforcement(self) -> None:
        for definition in self.coverage["counterfactuals"]:
            for field in definition["authored_constraint_fields"]:
                self.assertIn(field, definition["control"])
                assertions = [
                    item
                    for item in definition["limitation_contract"]
                    if item.get("field") == field
                ]
                self.assertEqual(1, len(assertions))
                self.assertEqual("field_equals", assertions[0]["assertion"])
                self.assertIn(
                    assertions[0]["enforcement"],
                    {"GUARD", "TERMINATION", "INVARIANT"},
                )

        recovery = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"]
            == "specialty.autodrones.tier_4.autonomous_recovery"
        )
        field = recovery["authored_constraint_fields"][0]
        result = execute_specialty_counterfactual(
            recovery,
            role="PRIMARY",
            level=20,
            limitation_overrides={field: False},
        )
        enforcement = next(
            item["enforcement"]
            for item in recovery["limitation_contract"]
            if item.get("field") == field
        )
        self.assertEqual(
            {
                "GUARD": "GUARD_VIOLATION",
                "TERMINATION": "TERMINATED",
                "INVARIANT": "INVARIANT_VIOLATION",
            }[enforcement],
            result["status"],
        )
        self.assertFalse(result["limitations_preserved"])

    def test_braced_position_encodes_inside_and_displacement_boundaries(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.anchor.tier_1.braced_position"
        )
        self.assertEqual(True, definition["prerequisite_contract"]["inside_strongpoint"])
        self.assertEqual(
            False,
            definition["control"]["displaced_beyond_position"],
        )
        self.assertIn(
            "displaced_beyond_position", definition["authored_constraint_fields"]
        )
        constraints = " ".join(
            item["source_clause"]
            for item in definition["authored_rule_contract"]["constraint_clauses"]
        ).lower()
        self.assertIn("protection is lost", constraints)

    def test_authored_guard_blocks_the_effect_before_operations(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.anchor.tier_1.settle_in"
        )
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=5,
            limitation_overrides={"terrain_supports_stance_and_geometry": False},
        )
        self.assertEqual("GUARD_VIOLATION", result["status"])
        self.assertEqual(
            ["terrain_supports_stance_and_geometry"], result["failed_guards"]
        )
        self.assertFalse(result["node_effect_applied"])
        self.assertEqual([], result["mechanical_operations_executed"])
        self.assertEqual(result["control_result"], result["candidate_result"])

    def test_authored_termination_establishes_then_removes_the_effect(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.anchor.tier_1.braced_position"
        )
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=5,
            limitation_overrides={"displaced_beyond_position": True},
        )
        self.assertEqual("TERMINATED", result["status"])
        self.assertTrue(result["effect_established_before_termination"])
        self.assertEqual(
            2, result["established_result"]["opponent_difficulty_offset"]
        )
        self.assertEqual(
            result["control_result"]["opponent_difficulty_offset"],
            result["candidate_result"]["opponent_difficulty_offset"],
        )
        self.assertFalse(result["node_effect_applied"])

    def test_authored_invariant_detects_an_invalid_post_effect_mutation(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.anchor.entry.establish_strongpoint"
        )
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=1,
            limitation_overrides={"creates_cover": True},
        )
        self.assertEqual("INVARIANT_VIOLATION", result["status"])
        self.assertTrue(result["node_effect_applied"])
        self.assertTrue(result["candidate_result"]["strongpoint"])
        self.assertFalse(result["limitations_preserved"])

    def test_effect_clauses_are_operations_not_activation_prerequisites(self) -> None:
        expected_effects = {
            "specialty.evader.tier_3.evasive_break": {
                "prep_action_with_limited_movement_reserved"
            },
            "specialty.shield_bearer.tier_5.walking_bastion": {
                "moving_brace_active",
                "controlled_facing_or_giving_ground_collapses_moving_brace",
            },
            "specialty.shock.tier_4.rolling_assault": {
                "assault_window_affects_additional_nearby_allies",
                "successful_momentum_chain_refreshes_allied_movement_benefit",
            },
            "specialty.snapshot.tier_4.hasty_full_solution": {
                "larger_ordinary_aim_attack_benefit"
            },
        }
        definitions = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }
        for node_id, fields in expected_effects.items():
            definition = definitions[node_id]
            self.assertTrue(fields.issubset(definition["authored_effect_fields"]))
            self.assertTrue(fields.isdisjoint(definition["prerequisite_fields"]))
            result = execute_specialty_counterfactual(
                definition,
                role="PRIMARY",
                level=definition["available_benchmark_levels"][0],
            )
            self.assertTrue(fields.issubset(result["measured_fields"]))

        hasty = definitions["specialty.snapshot.tier_4.hasty_full_solution"]
        self.assertNotIn(
            "quick_solution_first_shot_against_target",
            hasty["prerequisite_fields"],
        )
        self.assertIn(
            "subsequent_shot_against_target",
            hasty["authored_termination_fields"],
        )

    def test_every_authored_constraint_has_an_executable_enforcement_class(self) -> None:
        constraints = [
            constraint
            for contract in SPECIALTY_RULE_CONTRACTS.values()
            for constraint in contract["constraints"]
        ]
        self.assertEqual(
            {"GUARD", "TERMINATION", "INVARIANT"},
            {constraint["enforcement"] for constraint in constraints},
        )
        self.assertFalse(
            any(
                constraint["enforcement"] == "UNCLASSIFIED"
                for constraint in constraints
            )
        )
        self.assertEqual(
            len(self.coverage["guard_violation_matrix"]),
            sum(
                len(item["authored_guard_fields"])
                for item in self.coverage["counterfactuals"]
            ),
        )
        self.assertEqual(
            len(self.coverage["termination_matrix"]),
            sum(
                len(item["authored_termination_fields"])
                for item in self.coverage["counterfactuals"]
            ),
        )
        self.assertEqual(
            len(self.coverage["invariant_mutation_matrix"]),
            sum(
                len(item["authored_invariant_fields"])
                for item in self.coverage["counterfactuals"]
            ),
        )

    def test_power_budget_counterfactual_executes_transfer_and_conservation(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.field_engineer.tier_2.power_budget"
        )
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=15,
        )
        self.assertEqual(2, result["candidate_result"]["action_ap"])
        self.assertEqual(900, result["candidate_result"]["donor_runtime_s"])
        self.assertEqual(1500, result["candidate_result"]["recipient_runtime_s"])
        self.assertEqual(
            2400,
            result["candidate_result"]["donor_runtime_s"]
            + result["candidate_result"]["recipient_runtime_s"],
        )
        self.assertFalse(result["candidate_result"]["output_bonus"])
        self.assertTrue(result["limitations_preserved"])

    def test_salvage_counterfactual_executes_application_count(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.trauma.tier_4.salvage"
        )
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=20,
        )
        self.assertEqual(
            "MODERATE_HAND_IMPAIRMENT",
            result["candidate_result"]["persistent_state"],
        )
        self.assertEqual(2, result["candidate_result"]["persistent_difficulty"])
        self.assertEqual(1, result["candidate_result"]["salvage_applications"])
        self.assertEqual(0, result["candidate_result"]["immediate_hp_restored"])
        self.assertIn(
            {"operation": "add", "field": "salvage_applications", "amount": 1},
            result["mechanical_operations_executed"],
        )

    def test_specialty_counterfactual_rejects_an_unavailable_level(self) -> None:
        definition = next(
            item
            for item in self.coverage["counterfactuals"]
            if item["node_id"] == "specialty.networker.tier_2.foothold"
        )
        with self.assertRaises(ValueError):
            execute_specialty_counterfactual(
                definition,
                role="PRIMARY",
                level=1,
            )

    def test_entry_and_keystone_excerpts_include_their_rule_text(self) -> None:
        breaker = self.coverage["specialties"]["BREAKER"]["nodes"]
        entry = next(item for item in breaker if item["node"]["tier"] == "ENTRY")
        keystone = next(item for item in breaker if item["node"]["tier"] == "TIER 5")
        self.assertIn("costs less AP/time", entry["node"]["source_excerpt"])
        self.assertIn("dramatically less tactical time", keystone["node"]["source_excerpt"])

    def test_executable_nodes_have_primary_secondary_off_role_and_level_scenarios(self) -> None:
        expected_roles = {"PRIMARY", "SECONDARY", "OFF_ROLE"}
        self.assertTrue(self.coverage["scenario_matrix"])
        for item in self.coverage["counterfactuals"]:
            self.assertEqual(set(item["scenario_roles"]), expected_roles)
            self.assertTrue(item["available_benchmark_levels"])
            self.assertTrue(item["scenario_roles"]["PRIMARY"]["node_effect_applied"])
            self.assertFalse(item["scenario_roles"]["OFF_ROLE"]["node_effect_applied"])
            self.assertNotEqual(
                item["scenario_roles"]["PRIMARY"]["scenario_id"],
                item["scenario_roles"]["SECONDARY"]["scenario_id"],
            )
            self.assertTrue(
                item["scenario_roles"]["PRIMARY"]["mechanic_scope_matches"]
            )
            self.assertFalse(
                item["scenario_roles"]["OFF_ROLE"]["mechanic_scope_matches"]
            )

        primary = next(
            item
            for item in self.coverage["scenario_matrix"]
            if item["role"] == "PRIMARY"
        )
        fixture = primary["scenario_fixture"]
        self.assertEqual(
            fixture["actor"]["specialty_nodes"],
            [primary["node_id"]],
        )
        self.assertIn("ap", fixture["actor"])
        self.assertIn("equipment_id", fixture["actor"])
        self.assertIn("position", fixture["actor"])
        self.assertIn("knowledge_state", fixture["actor"])
        self.assertEqual(
            fixture["prerequisite_state"],
            primary["prerequisite_fixture"],
        )
        self.assertTrue(primary["effect_measurements"])
        self.assertIsNone(primary["generic_dominance_detected"])
        self.assertEqual(
            "NOT_CLAIMED_STRUCTURAL_ONLY",
            primary["generic_dominance_evaluation"],
        )
        execution = primary["scenario_execution"]
        self.assertEqual(
            "DETERMINISTIC_SPECIALTY_COUNTERFACTUAL",
            execution["execution_kind"],
        )
        self.assertTrue(execution["structural_counterfactual"])
        self.assertIsNone(execution["objective_completed"])
        self.assertEqual(
            "NOT_CLAIMED_STRUCTURAL_ONLY",
            execution["objective_evaluation_status"],
        )
        self.assertTrue(execution["events"])
        self.assertTrue(execution["terminal_state_hash"])
        width = fixture["map_geometry"]["width"]
        height = fixture["map_geometry"]["height"]
        x, y = fixture["actor"]["position"]
        self.assertGreaterEqual(x, 0)
        self.assertLess(x, width)
        self.assertGreaterEqual(y, 0)
        self.assertLess(y, height)

    def test_designated_specialty_builds_execute_all_objective_roles(self) -> None:
        matrix = self.coverage["objective_scenario_matrix"]
        runtime_integrated = {
            "ANCHOR",
            "ARSENAL",
            "AUTODRONES",
            "BREAKER",
            "DIPLOMAT",
            "ENFORCER",
            "EVADER",
            "FIELD ENGINEER",
            "FIELD MEDIC",
            "FPV DRONES",
            "FLANKER",
            "GUNNER",
            "GUARDIAN",
            "HANDLER",
            "ICON",
            "INFILTRATOR",
            "LOCAL HACKER",
            "NETWORKER",
            "OPERATOR",
            "RESUSCITATOR",
            "SENTINEL",
            "SHOCK",
            "SHIELD BEARER",
            "SNAPSHOT",
            "SNIPER",
            "TRAUMA",
        }
        expected_runtime_gaps = set(self.coverage["specialties"]) - runtime_integrated
        self.assertEqual(26 * 5 * 3, len(matrix))
        self.assertEqual(
            "OBJECTIVE_EXECUTED_WITH_FULL_RUNTIME_INTEGRATION",
            self.coverage["specialty_scenario_evidence_status"],
        )
        self.assertEqual(
            "ACTUAL_OFF_ROLE_OBJECTIVE_AND_RESOURCE_METRICS",
            self.coverage["generic_dominance_evaluation"],
        )
        for item in matrix:
            self.assertEqual(
                "DETERMINISTIC_SPECIALTY_OBJECTIVE",
                item["execution_kind"],
            )
            self.assertTrue(item["build_id"])
            self.assertTrue(item["build_node_ids"])
            self.assertTrue(item["control_metrics"]["terminal_reason"])
            self.assertTrue(item["candidate_metrics"]["terminal_reason"])
            self.assertNotEqual(
                "NOT_CAPTURED", item["control_metrics"]["event_log_hash"]
            )
            self.assertNotEqual(
                "NOT_CAPTURED", item["candidate_metrics"]["event_log_hash"]
            )
            self.assertTrue(item["events"])
            self.assertTrue(item["terminal_state_hash"])
            if item["role"] in {"PRIMARY", "SECONDARY"}:
                self.assertTrue(item["structural_contribution_detected"])
                self.assertEqual(
                    item["specialty"] in runtime_integrated,
                    item["objective_runtime_effect_detected"],
                )
                self.assertEqual(
                    item["objective_runtime_effect_detected"],
                    item["contribution_detected"],
                )
                if item["specialty"] in runtime_integrated:
                    self.assertTrue(item["runtime_specialty_effects"])
                self.assertGreater(
                    item["candidate_metrics"][
                        "structurally_executed_specialty_nodes"
                    ],
                    0,
                )
            else:
                self.assertFalse(item["contribution_detected"])
                self.assertFalse(item["generic_dominance_detected"])

        integration_gaps = [
            finding
            for finding in self.coverage["findings"]
            if finding.get("subtype")
            == "SPECIALTY_OBJECTIVE_RUNTIME_INTEGRATION_GAP"
        ]
        self.assertEqual(0, len(integration_gaps))
        self.assertEqual(
            expected_runtime_gaps,
            {finding["specialty"] for finding in integration_gaps},
        )

        gunner_l10 = self.coverage["designated_builds"]["GUNNER"]["10"]
        self.assertEqual(10, len(gunner_l10["node_ids"]))
        self.assertIn("TD-BUILD-GUNNER-L10-", gunner_l10["build_id"])

    def test_grilled_nodes_leave_no_ambiguous_source_classifications(self) -> None:
        self.assertEqual(self.coverage["coverage_counts"].get("AMBIGUOUS_SOURCE", 0), 0)

    def test_repaired_shield_permission_perk_is_executable(self) -> None:
        codes = {finding["code"] for finding in self.coverage["findings"]}
        self.assertNotIn("PERMISSION PERK", codes)
        self.assertNotIn("DEAD NODE", codes)
        shield = self.coverage["specialties"]["SHIELD BEARER"]["nodes"]
        training = next(item for item in shield if item["node"]["name"] == "SHIELD TRAINING")
        self.assertEqual(training["state"], "IMPLEMENTED_WITH_PARAMETERS")

    def test_networker_foothold_has_an_executable_cost_advantage(self) -> None:
        foothold = next(
            item
            for item in self.coverage["specialties"]["NETWORKER"]["nodes"]
            if item["node"]["name"] == "FOOTHOLD"
        )
        self.assertEqual("IMPLEMENTED_WITH_PARAMETERS", foothold["state"])
        self.assertEqual(
            ("specialty.networker.foothold.ap_discount",),
            foothold["parameter_ids"],
        )
        self.assertIsNotNone(foothold["counterfactual_id"])
        self.assertEqual((), foothold["unsupported_dependencies"])
        self.assertEqual((), foothold["findings"])

    def test_all_unexecutable_specialty_nodes_are_formal_spec_gaps(self) -> None:
        missing = [
            node
            for specialty in self.coverage["specialties"].values()
            for node in specialty["nodes"]
            if node["state"] == "MISSING_DEPENDENCY"
        ]
        self.assertEqual([], missing)

    def test_all_executable_specialty_nodes_have_counterfactuals(self) -> None:
        uncovered = [
            node
            for specialty in self.coverage["specialties"].values()
            for node in specialty["nodes"]
            if node["counterfactual_id"] is None
        ]
        self.assertEqual(364, len(self.coverage["counterfactuals"]))
        self.assertEqual([], uncovered)

    def test_rejected_keyword_bindings_are_authorized_but_never_executed(self) -> None:
        self.assertEqual(0, len(REJECTED_SPECIALTY_PARAMETER_ROWS))
        expected_ids = {
            specialty_parameter_id(*row)
            for row in REJECTED_SPECIALTY_PARAMETER_ROWS
        }
        self.assertEqual(expected_ids, REJECTED_SPECIALTY_PARAMETER_IDS)
        self.assertTrue(expected_ids.issubset(self.parameters.manifest.specs))
        executed_parameter_ids = {
            parameter_id
            for counterfactual in self.coverage["counterfactuals"]
            for parameter_id in counterfactual["parameter_ids"]
        }
        executed_parameter_ids.update(
            operation["parameter_id"]
            for counterfactual in self.coverage["counterfactuals"]
            for operation in counterfactual["mechanical_operations"]
            if "parameter_id" in operation
        )
        self.assertTrue(expected_ids.isdisjoint(executed_parameter_ids))
        self.assertEqual(0, len(DEFERRED_SPECIALTY_PARAMETER_IDS))
        self.assertTrue(
            DEFERRED_SPECIALTY_PARAMETER_IDS.isdisjoint(executed_parameter_ids)
        )
        self.assertEqual(
            expected_ids,
            {
                item["parameter_id"]
                for item in self.coverage["rejected_parameter_bindings"]
            },
        )
        self.assertTrue(
            all(
                item["execution_policy"] == "FAIL_CLOSED"
                for item in self.coverage["rejected_parameter_bindings"]
            )
        )
        self.assertEqual(
            DEFERRED_SPECIALTY_PARAMETER_IDS,
            {
                item["parameter_id"]
                for item in self.coverage["deferred_parameter_bindings"]
            },
        )

    def test_approved_specialty_representations_do_not_double_count(self) -> None:
        by_node = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }
        expected_single_parameters = {
            "specialty.evader.tier_2.extended_reposition": (
                "specialty.evader.extended_reposition.movement_m_per_ap_bonus",
            ),
            "specialty.evader.tier_4.ghost_walk": (
                "specialty.evader.ghost_walk.movement_m_per_ap_bonus",
            ),
            "specialty.fpv_drones.entry.manual_pilot": (
                "specialty.fpv_drones.manual_pilot.difficulty_offset",
                "specialty.fpv_drones.manual_pilot.movement_m_per_ap_bonus",
            ),
            "specialty.gunner.tier_1.recoil_rhythm": (
                "specialty.gunner.recoil_rhythm.recoil_reduction",
            ),
        }
        for node_id, parameter_ids in expected_single_parameters.items():
            self.assertEqual(list(parameter_ids), by_node[node_id]["parameter_ids"])

        emplace = by_node["specialty.field_engineer.tier_3.emplace"]
        self.assertEqual(
            ["DURABILITY", "EFFECTIVENESS"], emplace["branch_options"]
        )
        for branch in emplace["branch_options"]:
            result = execute_specialty_counterfactual(
                emplace, role="PRIMARY", level=10, branch_id=branch
            )
            candidate = result["candidate_result"]
            self.assertEqual(
                1,
                sum(
                    bool(candidate[field])
                    for field in (
                        "difficulty_offset",
                        "durability_or_restoration_bonus",
                    )
                ),
            )
            self.assertTrue(candidate["relocation_slowdown_honored"])

        suppression = by_node[
            "specialty.gunner.tier_1.suppression_discipline"
        ]
        self.assertEqual(
            ["AMMUNITION", "FIRING_TIME"], suppression["branch_options"]
        )
        for branch in suppression["branch_options"]:
            result = execute_specialty_counterfactual(
                suppression, role="PRIMARY", level=5, branch_id=branch
            )
            candidate = result["candidate_result"]
            self.assertEqual(
                1,
                sum(
                    bool(candidate[field])
                    for field in ("ammunition_discount", "ap_discount")
                ),
            )
        self.assertEqual(61, len(self.coverage["branch_selection_matrix"]))
        with self.assertRaises(ValueError):
            execute_specialty_counterfactual(
                suppression,
                role="PRIMARY",
                level=5,
                branch_id="BOTH",
            )

    def test_approved_numeric_defaults_are_executable_and_audited(self) -> None:
        approved_parameter_ids = {
            "specialty.anchor.establish_strongpoint.action_ap",
            "specialty.breaker.firing_port.ap_discount",
            "specialty.breaker.wide_breach.resource_cost",
            "specialty.diplomat.terms_on_paper.leverage_steps",
            "specialty.enforcer.weapon_frame.difficulty_offset",
            "specialty.field_medic.functional_splint.penalty_reduction",
            "specialty.field_medic.pain_control.duration_team_turns",
            "specialty.field_medic.pain_control.penalty_reduction",
            "specialty.field_medic.patch_and_move.movement_penalty_reduction_m_per_ap",
            "specialty.flanker.double_envelopment.difficulty_offset",
            "specialty.flanker.shadow_the_cover.prepared_movement_m",
            "specialty.fpv_drones.last_second_correction.action_ap",
            "specialty.fpv_drones.last_second_correction.failure_margin_window",
            "specialty.fpv_drones.manual_pilot.difficulty_offset",
            "specialty.fpv_drones.mark_and_pass.duration_team_turns",
            "specialty.gunner.long_string.diminishing_returns_step_bonus",
            "specialty.guardian.interpose.prepared_movement_m",
            "specialty.handler.introduction.difficulty_offset",
            "specialty.infiltrator.approach_plan.difficulty_offset",
            "specialty.infiltrator.close_pass.penalty_reduction",
            "specialty.infiltrator.gear_control.penalty_reduction",
            "specialty.infiltrator.leave_it_clean.evidence_steps_reduction",
            "specialty.infiltrator.quiet_hands.penalty_reduction",
            "specialty.infiltrator.silent_removal.noise_steps_reduction",
            "specialty.infiltrator.work_in_the_shadows.penalty_reduction",
            "specialty.operator.cycle_on_the_return.ap_discount",
            "specialty.operator.one_handed_manipulation.one_handed_surcharge_reduction_ap",
            "specialty.resuscitator.not_yet.difficulty_offset",
            "specialty.resuscitator.refuse_deterioration.deterioration_steps",
            "specialty.resuscitator.restore_consciousness.time_steps_reduction",
            "specialty.shock.break_the_rhythm.opponent_action_cost_ap",
            "specialty.snapshot.hasty_full_solution.aim_steps",
            "specialty.snapshot.instant_solution.developed_aim_steps",
            "specialty.sniper.deep_aim.difficulty_offset",
        }
        executed_parameter_ids = {
            parameter_id
            for counterfactual in self.coverage["counterfactuals"]
            for parameter_id in counterfactual["parameter_ids"]
        }
        self.assertTrue(approved_parameter_ids.issubset(executed_parameter_ids))
        self.assertTrue(
            approved_parameter_ids.isdisjoint(REJECTED_SPECIALTY_PARAMETER_IDS)
        )
        finding_nodes = {
            item["node_id"]
            for item in self.coverage["findings"]
            if "node_id" in item
        }
        counterfactuals = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }
        for parameter_id in approved_parameter_ids:
            specialty, node_slug, field = parameter_id.split(".")[1:]
            node_id_fragment = f"specialty.{specialty}."
            matches = [
                item
                for node_id, item in counterfactuals.items()
                if node_id.startswith(node_id_fragment)
                and parameter_id in item["parameter_ids"]
            ]
            self.assertEqual(1, len(matches), parameter_id)
            self.assertIn(field, matches[0]["changed_fields"])
            self.assertNotIn(matches[0]["node_id"], finding_nodes)

    def test_approved_alternative_branches_never_stack(self) -> None:
        by_node = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }
        simple_alternatives = {
            "specialty.breaker.tier_2.controlled_breach": (
                "area_or_range_m_bonus",
                "collateral_damage_reduction",
            ),
            "specialty.field_engineer.tier_4.hot_repair": (
                "repair_effectiveness_penalty_steps",
                "risk_steps",
            ),
            "specialty.field_medic.tier_3.battlefield_recovery": (
                "res_bonus",
                "short_term_survivability_bonus",
            ),
            "specialty.handler.tier_2.favor_ledger": (
                "leverage_steps",
                "persistence_steps",
            ),
            "specialty.resuscitator.tier_4.bridge_to_trauma": (
                "difficulty_offset",
                "ap_discount",
            ),
            "specialty.shield_bearer.tier_3.impact_control": (
                "failure_changes_position",
                "failure_changes_facing",
            ),
            "specialty.trauma.entry.trauma_care": (
                "hp_restoration_bonus",
                "injury_recovery_steps",
            ),
            "specialty.trauma.tier_4.recovery_plan": (
                "time_steps_reduction",
                "injury_recovery_steps",
            ),
        }
        finding_nodes = {
            item["node_id"]
            for item in self.coverage["findings"]
            if "node_id" in item
        }
        for node_id, fields in simple_alternatives.items():
            definition = by_node[node_id]
            for branch in definition["branch_options"]:
                result = execute_specialty_counterfactual(
                    definition,
                    role="PRIMARY",
                    level=definition["available_benchmark_levels"][0],
                    branch_id=branch,
                )
                self.assertEqual("EXECUTED", result["status"])
                self.assertEqual(
                    1,
                    sum(
                        result["candidate_result"][field]
                        != result["control_result"][field]
                        for field in fields
                    ),
                    f"{node_id}:{branch}",
                )
            self.assertNotIn(node_id, finding_nodes)

        no_fixed_entry = by_node["specialty.breaker.tier_5.no_fixed_entry"]
        for branch in no_fixed_entry["branch_options"]:
            result = execute_specialty_counterfactual(
                no_fixed_entry,
                role="PRIMARY",
                level=20,
                branch_id=branch,
            )
            self.assertEqual(branch, result["candidate_result"]["breach_shape"])

        evasion = by_node["specialty.fpv_drones.tier_4.evasive_piloting"]
        for branch in evasion["branch_options"]:
            result = execute_specialty_counterfactual(
                evasion,
                role="PRIMARY",
                level=15,
                branch_id=branch,
            )
            self.assertGreater(
                result["candidate_result"]["opponent_difficulty_offset"], 0
            )
            self.assertEqual(
                1,
                sum(
                    bool(result["candidate_result"][field])
                    for field in ("action_ap", "movement_m_per_ap_penalty")
                ),
            )

        overdrive = by_node["specialty.field_engineer.tier_4.overdrive"]
        output_fields = (
            "movement_m_per_ap_bonus",
            "sensor_steps",
            "operation_rate_steps",
            "power_output_steps",
        )
        cost_fields = (
            "wear_steps",
            "damage_cost",
            "heat_steps",
            "power_depletion_steps",
        )
        self.assertEqual(16, len(overdrive["branch_options"]))
        for branch in overdrive["branch_options"]:
            result = execute_specialty_counterfactual(
                overdrive,
                role="PRIMARY",
                level=15,
                branch_id=branch,
            )
            self.assertEqual(
                1,
                sum(bool(result["candidate_result"][field]) for field in output_fields),
            )
            self.assertEqual(
                1,
                sum(bool(result["candidate_result"][field]) for field in cost_fields),
            )

    def test_one_handed_manipulation_reduces_cost_without_granting_permission(self) -> None:
        definition = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }["specialty.operator.tier_2.one_handed_manipulation"]
        result = execute_specialty_counterfactual(
            definition,
            role="PRIMARY",
            level=10,
        )
        self.assertEqual("EXECUTED", result["status"])
        self.assertEqual(2, result["control_result"]["effective_surcharge_ap"])
        self.assertEqual(1, result["candidate_result"]["effective_surcharge_ap"])
        self.assertEqual(
            1,
            result["candidate_result"]["one_handed_surcharge_reduction_ap"],
        )
        self.assertFalse(result["candidate_result"]["ability_creates_baseline_permission"])
        self.assertFalse(
            result["candidate_result"]["final_cost_below_normal_two_handed_cost"]
        )
        self.assertFalse(
            result["candidate_result"]["literal_two_hand_operation_without_workaround"]
        )

    def test_final_non_hacking_specialty_boundaries_execute(self) -> None:
        by_node = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }
        finding_nodes = {
            item["node_id"]
            for item in self.coverage["findings"]
            if "node_id" in item
        }
        resolved_node_ids = {
            "specialty.arsenal.tier_4.role_package",
            "specialty.diplomat.tier_5.grand_bargain",
            "specialty.field_engineer.tier_1.patch_job",
            "specialty.field_engineer.tier_2.field_modification",
            "specialty.field_engineer.tier_3.technical_obstacle",
            "specialty.field_engineer.tier_4.redundant_system",
            "specialty.handler.tier_3.protect_the_source",
            "specialty.sniper.tier_5.perfect_solution",
        }
        self.assertTrue(resolved_node_ids.isdisjoint(finding_nodes))
        self.assertTrue(
            all(
                item["node_id"].startswith(
                    ("specialty.local_hacker.", "specialty.networker.")
                )
                for item in self.coverage["findings"]
                if item.get("node_id")
            )
        )

        role_package = by_node["specialty.arsenal.tier_4.role_package"]
        self.assertEqual(["ACCESS", "BURDEN"], role_package["branch_options"])
        for branch in role_package["branch_options"]:
            result = execute_specialty_counterfactual(
                role_package, role="PRIMARY", level=15, branch_id=branch
            )["candidate_result"]
            self.assertEqual(branch, result["package_benefit"])
            self.assertEqual(
                1,
                sum(bool(result[field]) for field in ("ap_discount", "burden_reduction")),
            )

        grand_bargain = execute_specialty_counterfactual(
            by_node["specialty.diplomat.tier_5.grand_bargain"],
            role="PRIMARY",
            level=20,
        )["candidate_result"]
        self.assertEqual(1, grand_bargain["additional_term_setup_ap"])
        self.assertTrue(grand_bargain["linked_terms_share_negotiation"])

        patch_job = by_node["specialty.field_engineer.tier_1.patch_job"]
        self.assertEqual(["FRAGILE", "ONE_USE"], patch_job["branch_options"])
        for branch in patch_job["branch_options"]:
            result = execute_specialty_counterfactual(
                patch_job, role="PRIMARY", level=5, branch_id=branch
            )["candidate_result"]
            self.assertEqual(1, result["ap_discount"])
            self.assertTrue(result["named_function_restored"])
            self.assertEqual(branch, result["patch_liability"])
            self.assertEqual(
                1,
                sum(bool(result[field]) for field in ("patch_durability", "remaining_uses")),
            )

        field_modification = by_node[
            "specialty.field_engineer.tier_2.field_modification"
        ]
        self.assertEqual(5, len(field_modification["branch_options"]))
        for branch in field_modification["branch_options"]:
            result = execute_specialty_counterfactual(
                field_modification, role="PRIMARY", level=10, branch_id=branch
            )["candidate_result"]
            self.assertEqual(branch, result["selected_characteristic"])
            self.assertTrue(result["tradeoff_declared"])
            self.assertTrue(result["temporary_physical_modification"])

        technical_obstacle = by_node[
            "specialty.field_engineer.tier_3.technical_obstacle"
        ]
        obstacle = execute_specialty_counterfactual(
            technical_obstacle, role="PRIMARY", level=10, branch_id="OBSTACLE"
        )["candidate_result"]
        hazard = execute_specialty_counterfactual(
            technical_obstacle, role="PRIMARY", level=10, branch_id="HAZARD"
        )["candidate_result"]
        self.assertEqual(1, obstacle["movement_cost_ap"])
        self.assertFalse(obstacle["named_existing_hazard_payload"])
        self.assertEqual(0, hazard["movement_cost_ap"])
        self.assertTrue(hazard["named_existing_hazard_payload"])
        self.assertTrue(obstacle["placed_hardware"] and hazard["placed_hardware"])

        redundant = execute_specialty_counterfactual(
            by_node["specialty.field_engineer.tier_4.redundant_system"],
            role="PRIMARY",
            level=15,
        )["candidate_result"]
        self.assertTrue(redundant["named_function_selected"])
        self.assertFalse(redundant["first_component_failure_disables_function"])
        self.assertEqual(2, redundant["backup_effectiveness_penalty_steps"])

        protected = execute_specialty_counterfactual(
            by_node["specialty.handler.tier_3.protect_the_source"],
            role="PRIMARY",
            level=10,
        )["candidate_result"]
        self.assertEqual(1, protected["ap_discount"])
        self.assertEqual(1, protected["count_bonus"])
        self.assertTrue(
            protected["additional_directly_connected_contact_avoids_chain_exposure"]
        )

        perfect = by_node["specialty.sniper.tier_5.perfect_solution"]
        solution_fields = (
            "extreme_range_difficulty_reduction_steps",
            "exposed_area_difficulty_reduction_steps",
            "called_shot_difficulty_reduction_steps",
            "hit_quality_steps",
            "target_pen_bonus",
            "material_pen_bonus",
        )
        self.assertEqual(6, len(perfect["branch_options"]))
        for branch in perfect["branch_options"]:
            result = execute_specialty_counterfactual(
                perfect, role="PRIMARY", level=20, branch_id=branch
            )["candidate_result"]
            self.assertEqual(1, result["action_ap"])
            self.assertEqual(2, result["solution_steps"])
            self.assertEqual(2, sum(result[field] for field in solution_fields))
            self.assertEqual(1, sum(bool(result[field]) for field in solution_fields))
        material_rejected = execute_specialty_counterfactual(
            perfect,
            role="PRIMARY",
            level=20,
            branch_id="MATERIAL_PENETRATION",
            prerequisite_overrides={"material_window_applies": False},
        )
        self.assertEqual("MISSING_PREREQUISITE", material_rejected["status"])
        self.assertFalse(material_rejected["node_effect_applied"])

    def test_canonical_structural_procedures_need_no_optimizer_values(self) -> None:
        expected_fields = {
            "specialty.autodrones.tier_3.local_response": {
                "count_bonus",
                "condition_response_pair_stored",
            },
            "specialty.diplomat.tier_4.multi_party_accord": {
                "common_terms_one_resolution",
            },
            "specialty.guardian.tier_4.no_opening": {
                "interpose_ends_after_use",
            },
            "specialty.resuscitator.tier_2.sustain_life": {
                "stabilization_retained_through_ordinary_handling",
            },
            "specialty.resuscitator.tier_4.move_the_critical": {
                "intervention_retained_during_ordinary_transport",
            },
            "specialty.shield_bearer.tier_5.walking_bastion": {
                "moving_brace_active",
                "controlled_facing_or_giving_ground_collapses_moving_brace",
            },
            "specialty.shock.tier_4.rolling_assault": {
                "assault_window_affects_additional_nearby_allies",
                "successful_momentum_chain_refreshes_allied_movement_benefit",
            },
            "specialty.shock.tier_5.break_the_line": {
                "assault_chain_active",
                "next_advance_efficiency_refreshed",
                "next_disruption_efficiency_refreshed",
            },
        }
        counterfactuals = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }
        finding_nodes = {
            item["node_id"]
            for item in self.coverage["findings"]
            if "node_id" in item
        }
        for node_id, fields in expected_fields.items():
            definition = counterfactuals[node_id]
            self.assertTrue(fields.issubset(definition["changed_fields"]))
            self.assertNotIn(node_id, finding_nodes)
            if node_id in {
                "specialty.guardian.tier_4.no_opening",
                "specialty.resuscitator.tier_2.sustain_life",
                "specialty.resuscitator.tier_4.move_the_critical",
                "specialty.shock.tier_5.break_the_line",
            }:
                self.assertEqual([], definition["parameter_ids"])

    def test_approved_hacking_boundaries_are_fully_executable(self) -> None:
        self.assertEqual(
            [],
            [
                finding
                for finding in self.coverage["findings"]
                if finding.get("node_id")
            ],
        )
        by_node = {
            item["node_id"]: item for item in self.coverage["counterfactuals"]
        }

        quiet_console = execute_specialty_counterfactual(
            by_node["specialty.local_hacker.tier_1.quiet_console"],
            role="PRIMARY",
            level=5,
        )["candidate_result"]
        self.assertEqual("LOWER", quiet_console["visibility_change_direction"])
        self.assertEqual(1, quiet_console["visibility_band_shift"])
        self.assertFalse(quiet_console["action_cannot_be_detected"])
        self.assertFalse(quiet_console["physical_evidence_eliminated"])

        snapshot = execute_specialty_counterfactual(
            by_node["specialty.local_hacker.tier_2.snapshot"],
            role="PRIMARY",
            level=5,
        )["candidate_result"]
        self.assertTrue(snapshot["configuration_snapshot"])
        self.assertEqual(1, snapshot["capture_action_count"])
        self.assertEqual(1, snapshot["comparison_ap_discount"])
        self.assertEqual(1, snapshot["restore_ap_discount"])
        self.assertTrue(snapshot["capture_limited_to_readable_fields"])
        self.assertTrue(snapshot["restore_limited_to_supported_writable_fields"])

        hard_lockout = by_node["specialty.local_hacker.tier_4.hard_lockout"]
        self.assertEqual(
            ["INVALIDATE_SESSIONS", "REVOKE_ACCESS_METHOD"],
            hard_lockout["branch_options"],
        )
        invalidated = execute_specialty_counterfactual(
            hard_lockout,
            role="PRIMARY",
            level=15,
            branch_id="INVALIDATE_SESSIONS",
        )["candidate_result"]
        revoked = execute_specialty_counterfactual(
            hard_lockout,
            role="PRIMARY",
            level=15,
            branch_id="REVOKE_ACCESS_METHOD",
        )["candidate_result"]
        self.assertTrue(invalidated["active_remote_sessions_invalidated"])
        self.assertFalse(invalidated["selected_access_method_revoked"])
        self.assertFalse(revoked["active_remote_sessions_invalidated"])
        self.assertTrue(revoked["selected_access_method_revoked"])
        rejected_revoke = execute_specialty_counterfactual(
            hard_lockout,
            role="PRIMARY",
            level=15,
            branch_id="REVOKE_ACCESS_METHOD",
            prerequisite_overrides={
                "selected_remote_access_method_currently_valid": False,
            },
        )
        self.assertEqual("MISSING_PREREQUISITE", rejected_revoke["status"])

        root = execute_specialty_counterfactual(
            by_node["specialty.local_hacker.tier_5.root_on_the_box"],
            role="PRIMARY",
            level=20,
        )["candidate_result"]
        self.assertTrue(root["persistent_override_slot"])
        self.assertTrue(root["scripted_routine_slot"])
        self.assertFalse(root["replacing_one_slot_replaces_other"])

        quiet_enumeration = execute_specialty_counterfactual(
            by_node["specialty.networker.tier_1.quiet_enumeration"],
            role="PRIMARY",
            level=5,
        )["candidate_result"]
        self.assertEqual("LOWER", quiet_enumeration["visibility_change_direction"])
        self.assertFalse(quiet_enumeration["undetectable"])

        credential_sweep = execute_specialty_counterfactual(
            by_node["specialty.networker.tier_1.credential_sweep"],
            role="PRIMARY",
            level=5,
        )["candidate_result"]
        self.assertEqual(1, credential_sweep["action_ap"])
        self.assertTrue(credential_sweep["plausible_candidate_set_identified"])
        self.assertFalse(credential_sweep["credential_tested"])
        self.assertFalse(credential_sweep["credential_validity_proved"])

        gateway = execute_specialty_counterfactual(
            by_node["specialty.networker.tier_3.gateway_pivot"],
            role="PRIMARY",
            level=10,
        )["candidate_result"]
        self.assertTrue(gateway["far_side_links_revealed"])
        self.assertTrue(gateway["immediate_neighboring_nodes_revealed"])
        self.assertFalse(gateway["hidden_beyond_entry_topology_revealed"])
        self.assertEqual(1, gateway["first_further_discovery_ap_discount"])

        parallel = execute_specialty_counterfactual(
            by_node["specialty.networker.tier_3.parallel_probe"],
            role="PRIMARY",
            level=10,
        )["candidate_result"]
        self.assertEqual(1, parallel["additional_directly_reachable_targets"])
        self.assertEqual("RAISE", parallel["visibility_change_direction"])
        self.assertEqual(1, parallel["visibility_band_shift"])
        self.assertFalse(parallel["automatic_detection"])

        route_persistence = execute_specialty_counterfactual(
            by_node["specialty.networker.tier_3.route_persistence"],
            role="PRIMARY",
            level=10,
        )["candidate_result"]
        self.assertEqual(1, route_persistence["ap_discount"])
        self.assertTrue(route_persistence["one_downstream_connection_switched"])
        self.assertTrue(route_persistence["alternate_reaches_same_compromised_node"])
        self.assertTrue(route_persistence["required_session_or_foothold_valid"])

        cascade = by_node["specialty.networker.tier_4.cascade_access"]
        self.assertEqual(["EASIER", "FASTER"], cascade["branch_options"])
        for branch in cascade["branch_options"]:
            result = execute_specialty_counterfactual(
                cascade,
                role="PRIMARY",
                level=15,
                branch_id=branch,
            )["candidate_result"]
            self.assertEqual(
                1,
                sum(bool(result[field]) for field in ("ap_discount", "difficulty_offset")),
            )
            self.assertFalse(result["cascade_benefits_stack"])

        reroute = execute_specialty_counterfactual(
            by_node["specialty.networker.tier_4.reroute"],
            role="PRIMARY",
            level=15,
        )["candidate_result"]
        self.assertTrue(reroute["connection_chain_recalculated"])
        self.assertTrue(reroute["replacement_uses_active_known_links"])
        self.assertFalse(reroute["unreached_sessions_retained"])
        self.assertFalse(reroute["surviving_footholds_invalidated"])

        topology = execute_specialty_counterfactual(
            by_node["specialty.networker.tier_5.own_the_topology"],
            role="PRIMARY",
            level=20,
        )["candidate_result"]
        self.assertTrue(topology["topology_knowledge_persists"])
        self.assertTrue(topology["current_reachability_recalculated"])
        self.assertTrue(topology["reachability_from_surviving_control_or_foothold"])
        self.assertFalse(topology["topology_knowledge_cannot_become_outdated"])

    def test_power_budget_conserves_connected_reference_runtime(self) -> None:
        result = reroute_power_budget(
            PoweredDevice("donor", 1200),
            PoweredDevice("recipient", 1200),
            connected=True,
            compatible=True,
            parameters=self.parameters,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.ap_spent, 2)
        self.assertEqual(result.transfer_s, 300)
        self.assertEqual((result.donor.runtime_s, result.recipient.runtime_s), (900, 1500))
        self.assertEqual(result.donor.runtime_s + result.recipient.runtime_s, 2400)
        self.assertEqual(result.recipient_runtime_if_disconnected_s, 1200)
        self.assertFalse(result.output_bonus)

    def test_power_budget_rejects_missing_physical_compatibility(self) -> None:
        result = reroute_power_budget(
            PoweredDevice("donor", 1200),
            PoweredDevice("recipient", 1200),
            connected=False,
            compatible=True,
            parameters=self.parameters,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.ap_spent, 0)
        self.assertEqual((result.donor.runtime_s, result.recipient.runtime_s), (1200, 1200))

    def test_salvage_reduces_only_the_authored_persistent_consequence_once(self) -> None:
        injury = repairable_forearm_injury(self.parameters)
        result = apply_salvage(
            injury,
            successful_definitive_care=True,
            parameters=self.parameters,
        )
        self.assertTrue(result.applied)
        self.assertEqual(result.injury.persistent_state, "MODERATE_HAND_IMPAIRMENT")
        self.assertEqual(result.injury.difficulty_offset, 2)
        self.assertEqual(result.immediate_hp_restored, 0)
        repeated = apply_salvage(
            result.injury,
            successful_definitive_care=True,
            parameters=self.parameters,
        )
        self.assertFalse(repeated.applied)
        self.assertEqual(repeated.injury, result.injury)

    def test_salvage_cannot_repair_irreparable_anatomy(self) -> None:
        injury = PersistentInjury(
            "DESTROYED_HAND",
            "IRREPARABLE_HAND_LOSS",
            6,
            irreparable=True,
        )
        result = apply_salvage(
            injury,
            successful_definitive_care=True,
            parameters=self.parameters,
        )
        self.assertFalse(result.applied)
        self.assertEqual(result.injury, injury)

    def test_salvage_cannot_invent_an_unauthored_lower_state(self) -> None:
        injury = PersistentInjury("UNTRACKED_INJURY", "SEVERE", 4)
        result = apply_salvage(
            injury,
            successful_definitive_care=True,
            parameters=self.parameters,
        )
        self.assertFalse(result.applied)
        self.assertEqual(result.injury, injury)

    def test_casualty_modes_preserve_rate_and_hand_order(self) -> None:
        one = casualty_movement("DRAG", current_movement_m_per_ap=4.0, parameters=self.parameters, drag_hands=1)
        two = casualty_movement("DRAG", current_movement_m_per_ap=4.0, parameters=self.parameters, drag_hands=2)
        carry = casualty_movement("CARRY", current_movement_m_per_ap=4.0, parameters=self.parameters)
        self.assertLess(one.movement_m_per_ap, two.movement_m_per_ap)
        self.assertLess(two.movement_m_per_ap, carry.movement_m_per_ap)
        self.assertTrue(one.trailing_space)
        self.assertFalse(carry.trailing_space)

    def test_grenade_scatter_uses_failure_bands_and_geometry(self) -> None:
        grid = build_map("MAP_TIGHT_20", self.parameters)
        self.assertEqual([grenade_scatter_distance(margin, self.parameters) for margin in (1, 5, 10, 15)], [1, 2, 4, 6])
        landing = grenade_scatter_landing(Position(10, 10), failure_margin=15, direction_index=0, grid=grid, parameters=self.parameters)
        self.assertTrue(grid.in_bounds(landing))
        self.assertFalse(grid.occupied((round(landing.x), round(landing.y))))

    def test_intrusion_retries_and_detection_are_stateful(self) -> None:
        nodes = network_site_nodes()
        result = intrusion_action(
            nodes,
            "NET40-A",
            "GET IN",
            roll=1,
            skill=4,
            aptitude=1,
            parameters=self.parameters,
            valid_route=True,
            hacker=True,
            local_hacker=True,
            stay_quiet_roll=1,
        )
        self.assertFalse(result.success)
        self.assertTrue(result.retry_available)
        self.assertEqual(nodes["NET40-A"].detection_state, DetectionState.SUSPICIOUS)

    def test_hacking_visibility_modifies_only_observed_stay_quiet_checks(self) -> None:
        standard_nodes = network_site_nodes()
        standard = intrusion_action(
            standard_nodes,
            "NET40-A",
            "GET IN",
            roll=20,
            skill=4,
            aptitude=1,
            parameters=self.parameters,
            valid_route=True,
            stay_quiet_roll=7,
        )
        self.assertEqual("STANDARD", standard.visibility_band)
        self.assertEqual(14, standard.stay_quiet_difficulty)
        self.assertEqual(DetectionState.SUSPICIOUS, standard_nodes["NET40-A"].detection_state)

        quiet_nodes = network_site_nodes()
        quiet = intrusion_action(
            quiet_nodes,
            "NET40-A",
            "GET IN",
            roll=20,
            skill=4,
            aptitude=1,
            parameters=self.parameters,
            valid_route=True,
            local_hacker=True,
            stay_quiet_roll=7,
        )
        self.assertEqual("LOW", quiet.visibility_band)
        self.assertEqual(12, quiet.stay_quiet_difficulty)
        self.assertEqual(DetectionState.UNDETECTED, quiet_nodes["NET40-A"].detection_state)

        unmonitored_nodes = network_site_nodes()
        unmonitored_nodes["NET40-A"].monitoring_difficulty = None
        unmonitored = intrusion_action(
            unmonitored_nodes,
            "NET40-A",
            "GET IN",
            roll=20,
            skill=4,
            aptitude=1,
            parameters=self.parameters,
            valid_route=True,
            base_visibility="HIGH",
            stay_quiet_roll=1,
        )
        self.assertEqual("HIGH", unmonitored.visibility_band)
        self.assertIsNone(unmonitored.stay_quiet_difficulty)
        self.assertEqual(DetectionState.UNDETECTED, unmonitored_nodes["NET40-A"].detection_state)

    def test_parallel_probe_raises_visibility_without_automatic_detection(self) -> None:
        nodes = network_site_nodes()
        nodes["NET40-A"].compromised = True
        result = intrusion_action(
            nodes,
            "NET40-A",
            "DISCOVER",
            roll=20,
            skill=4,
            aptitude=1,
            parameters=self.parameters,
            valid_route=True,
            networker=True,
            parallel_probe=True,
        )
        self.assertEqual("STANDARD", result.visibility_band)
        self.assertEqual(DetectionState.UNDETECTED, nodes["NET40-A"].detection_state)

    def test_authorized_remote_function_is_automatic_but_costs_operational_ap(self) -> None:
        nodes = network_site_nodes()
        nodes["NET40-C"].compromised = True
        nodes["NET40-C"].privilege = nodes["NET40-C"].required_privilege
        result = intrusion_action(nodes, "NET40-C", "OPERATE", roll=1, skill=0, aptitude=0, parameters=self.parameters, valid_route=True, authorized=True)
        self.assertTrue(result.success)
        self.assertIsNone(result.roll)
        self.assertEqual(result.ap_spent, self.parameters.values["hacking.authorized_function_ap"])

    def test_networker_foothold_discount_requires_a_remotely_reached_node(self) -> None:
        local_nodes = network_site_nodes()
        local_nodes["NET40-A"].compromised = True
        local_nodes["NET40-A"].privilege = 1
        local = intrusion_action(
            local_nodes,
            "NET40-A",
            "FOOTHOLD",
            roll=20,
            skill=6,
            aptitude=1,
            parameters=self.parameters,
            valid_route=True,
            hacker=True,
            networker=True,
        )

        remote_nodes = network_site_nodes()
        remote_nodes["NET40-B"].compromised = True
        remote_nodes["NET40-B"].privilege = 1
        remote = intrusion_action(
            remote_nodes,
            "NET40-B",
            "FOOTHOLD",
            roll=20,
            skill=6,
            aptitude=1,
            parameters=self.parameters,
            valid_route=True,
            hacker=True,
            networker=True,
        )

        self.assertTrue(local.success)
        self.assertTrue(remote.success)
        self.assertEqual(2, local.ap_spent)
        self.assertEqual(1, remote.ap_spent)

    def test_source_drift_is_clean_at_encoded_checkpoint(self) -> None:
        self.assertEqual(source_drift(), [])

    def test_all_required_scenario_families_are_registered(self) -> None:
        self.assertEqual(len(scenario_registry()), 35)
        self.assertEqual(validate_scenarios(), [])


if __name__ == "__main__":
    unittest.main()
