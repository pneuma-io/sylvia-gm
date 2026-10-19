"""Run the TD player-control changes with Sylvia's Python environment.

These checks compile the current TD tools and use the real Sylvia package runtime. They do not
rebuild or qualify the unrelated specialty corpus or read existing generated campaign artifacts.
"""

from copy import deepcopy
import hashlib
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balance_suite.parameters import default_manifest
from sylvia_ruleset import builder

try:
    from sylvia_gm.application.package_runtime import (
        materialize_package_mechanic, materialize_player_action_checker,
        materialize_player_action_previewer,
    )
    from sylvia_gm.domain.authority import MechanicContext, thaw_json_value
    from sylvia_gm.domain.package_library import ToolDeclaration, manifest_from_record
except ModuleNotFoundError as error:
    raise unittest.SkipTest("Run these integration checks with Sylvia GM's Python environment") from error


class PlayerControlRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters = default_manifest().seed_set()
        runtime = builder._runtime(cls.parameters.values, {})
        # The selected mechanics have no specialty dependency. Omit that unrelated tool rather
        # than manufacturing specialty executions merely to populate its required enum.
        runtime["tools"].pop("exercise_specialty_counterfactual")
        cls.runtime_bytes = builder._canonical_json(runtime)
        cls.specs = builder._tool_specs([], [])

    def setUp(self):
        self.state = builder._scenario(
            "0" * 40, self.parameters.hash, self.parameters.values,
        )["initial_state"]
        self.sequence = 0

    def execute(self, tool_name, parameters):
        spec = self.specs[tool_name]
        mechanic = materialize_package_mechanic(
            package_id=builder.PACKAGE_ID, package_version=builder.PACKAGE_VERSION,
            package_sha256=hashlib.sha256(self.runtime_bytes).hexdigest(),
            declaration=ToolDeclaration(
                name=tool_name, capability=spec["capability"],
                input_schema=builder._schema(spec["parameters"]), state_predicates=(),
                turn_statuses=("open",), writers=("dm",),
            ),
            runtime_bytes=self.runtime_bytes,
        )
        self.sequence += 1
        result = mechanic(MechanicContext(
            campaign_id="td-player-controls", turn_id="turn",
            action_id=f"action-{self.sequence}", state=self.state, parameters=parameters,
        ))
        for mutation in result.mutations:
            self.state[f"{mutation.namespace}.{mutation.key}"] = thaw_json_value(mutation.value)
        return result.result

    def check(self, tool_name, parameters):
        """Read current eligibility through the host without executing the TD mechanic."""
        spec = self.specs[tool_name]
        checker = materialize_player_action_checker(
            package_id=builder.PACKAGE_ID,
            declaration=ToolDeclaration(
                name=tool_name, capability=spec["capability"],
                input_schema=builder._schema(spec["parameters"]), state_predicates=(),
                turn_statuses=("open",), writers=("dm",),
            ), runtime_bytes=self.runtime_bytes,
        )
        before = deepcopy(self.state)
        reason = checker(parameters, self.state, {})
        self.assertEqual(before, self.state)
        return reason

    def preview(self, tool_name, parameters):
        """Resolve one player-visible estimate without executing the TD mechanic."""
        spec = self.specs[tool_name]
        previewer = materialize_player_action_previewer(
            package_id=builder.PACKAGE_ID,
            declaration=ToolDeclaration(
                name=tool_name, capability=spec["capability"],
                input_schema=builder._schema(spec["parameters"]), state_predicates=(),
                turn_statuses=("open",), writers=("dm",),
            ), runtime_bytes=self.runtime_bytes,
        )
        before = deepcopy(self.state)
        result = previewer(parameters, self.state, {})
        self.assertEqual(before, self.state)
        return result

    def test_movement_does_not_require_a_preexisting_network_motion_map(self):
        state = self.state[f"{builder.PACKAGE_ID}.state"]
        state.pop("network_motion", None)
        self.execute("move_actor", {"actor": "vesper", "destination": [3, 2]})
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        state = self.state[f"{builder.PACKAGE_ID}.state"]
        self.assertEqual(actor["position_cell"], [3, 2])
        self.assertEqual(actor["ap"], 5)
        self.assertEqual(state["network_motion"], {"vesper": 1})
        self.execute("move_actor", {"actor": "vesper", "destination": [2, 2]})
        self.assertEqual(self.state[f"{builder.PACKAGE_ID}.state"]["network_motion"], {"vesper": 2})

    def test_stance_changes_geometry_and_preserves_height_during_movement(self):
        for stance, height, ap_per_metre in (
            ("STANDING", 1.8, 1), ("CROUCHED", 1.15, 2), ("PRONE", 0.45, 4),
        ):
            with self.subTest(stance=stance):
                self.setUp()
                args = {"actor": "vesper", "stance": stance}
                if stance != "STANDING":
                    self.assertIsNone(self.check("change_stance", args))
                    changed = self.execute("change_stance", args)
                    self.assertEqual("Stance", changed["result_card"]["title"])
                move = {"actor": "vesper", "destination": [3, 2]}
                estimate = self.preview("move_actor", move)
                self.assertEqual(ap_per_metre, estimate["preview"]["summary"][2]["value"])
                self.execute("move_actor", move)
                actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
                scene = self.state[f"{builder.PACKAGE_ID}.state"]["spatial_scene"]
                body = next(item for item in scene["entities"] if item["id"] == "vesper")
                self.assertEqual(stance, actor["stance"])
                self.assertEqual(height, body["height"])
                self.assertEqual(stance.lower(), body["metadata"]["player"]["stance"])
                self.assertEqual([3, 2], actor["position_cell"])
                self.assertAlmostEqual(height / 2, actor["center"][2])
                self.assertLess(actor["muzzle"][2], height)
                self.assertEqual(6 - (stance != "STANDING") - ap_per_metre, actor["ap"])

    def test_stance_preserves_authored_size_and_mobility_and_rejects_missing_body(self):
        from sylvia_gm.application.package_runtime import PackageRuntimeError

        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        actor["movement_m_per_ap"] = 1.5
        scene = self.state[f"{builder.PACKAGE_ID}.state"]["spatial_scene"]
        body = next(item for item in scene["entities"] if item["id"] == "vesper")
        body["height"] = 2.4
        self.execute("change_stance", {"actor": "vesper", "stance": "CROUCHED"})
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        self.assertEqual(0.75, actor["movement_m_per_ap"])
        scene = self.state[f"{builder.PACKAGE_ID}.state"]["spatial_scene"]
        body = next(item for item in scene["entities"] if item["id"] == "vesper")
        self.assertAlmostEqual(2.4 / 1.8 * 1.15, body["height"])
        scene["entities"] = [item for item in scene["entities"] if item["id"] != "vesper"]
        before = deepcopy(self.state)
        with self.assertRaises(PackageRuntimeError):
            self.execute("change_stance", {"actor": "vesper", "stance": "PRONE"})
        self.assertEqual(before, self.state)

    def test_stance_changes_firing_exposure_and_can_return_to_standing(self):
        fire = {"attacker": "vesper", "target": "hostile_alpha",
                "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"}
        standing = self.preview("fire_single", fire)["map_preview"]["exposure"]["fraction"]
        self.assertGreater(standing, 0)
        self.state[f"{builder.PACKAGE_ID}.state"]["active_side"] = "hostiles"
        self.execute("change_stance", {"actor": "hostile_alpha", "stance": "PRONE"})
        self.state[f"{builder.PACKAGE_ID}.state"]["active_side"] = "allies"
        fire["aim_point"] = [9.5, 2.5, 0.225]
        self.assertEqual(0, self.preview("fire_single", fire)["map_preview"]["exposure"]["fraction"])
        self.state[f"{builder.PACKAGE_ID}.state"]["active_side"] = "hostiles"
        self.execute("change_stance", {"actor": "hostile_alpha", "stance": "STANDING"})
        target = self.state[f"{builder.PACKAGE_ID}.entities"]["hostile_alpha"]
        self.assertEqual(4, target["ap"])
        self.assertEqual(1, target["movement_m_per_ap"])
        self.assertEqual(0.9, target["center"][2])
        self.state[f"{builder.PACKAGE_ID}.state"]["active_side"] = "allies"
        fire["aim_point"] = [9.5, 2.5, 0.9]
        self.assertEqual(standing, self.preview("fire_single", fire)["map_preview"]["exposure"]["fraction"])

    def test_stance_rejects_same_posture_no_ap_and_other_team(self):
        args = {"actor": "vesper", "stance": "STANDING"}
        self.assertIsNotNone(self.check("change_stance", args))
        before = deepcopy(self.state[f"{builder.PACKAGE_ID}.entities"])
        self.execute("change_stance", args)
        self.assertEqual(before, self.state[f"{builder.PACKAGE_ID}.entities"])
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        actor["ap"] = 0
        args["stance"] = "CROUCHED"
        self.assertIsNotNone(self.check("change_stance", args))
        self.execute("change_stance", args)
        self.assertEqual("STANDING", self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]["stance"])
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        actor["ap"] = 6
        self.state[f"{builder.PACKAGE_ID}.state"]["active_side"] = "hostiles"
        self.assertIsNotNone(self.check("change_stance", args))

    def test_forced_prone_updates_body_and_preserves_metadata(self):
        for tool, maneuver in (("resolve_cqc", "TRIP"), ("resolve_cqc_displacement", "THROW")):
            with self.subTest(maneuver=maneuver):
                self.setUp()
                actors = self.state[f"{builder.PACKAGE_ID}.entities"]
                actors["vesper"]["skills"]["cqc"] = 100
                scene = self.state[f"{builder.PACKAGE_ID}.state"]["spatial_scene"]
                body = next(item for item in scene["entities"] if item["id"] == "hex")
                body["metadata"]["private_note"] = "preserve privately"
                body["metadata"]["player"]["color"] = "blue"
                params = {"actor": "vesper", "target": "hex", "maneuver": maneuver}
                if maneuver == "THROW":
                    params["destination"] = [3, 3]
                result = self.execute(tool, params)
                state = self.state[f"{builder.PACKAGE_ID}.state"]
                self.assertTrue(state["last_cqc"]["success"])
                actor = self.state[f"{builder.PACKAGE_ID}.entities"]["hex"]
                body = next(item for item in state["spatial_scene"]["entities"] if item["id"] == "hex")
                self.assertEqual("PRONE", actor["stance"])
                self.assertEqual(0.45, body["height"])
                self.assertEqual("prone", body["metadata"]["player"]["stance"])
                self.assertEqual("blue", body["metadata"]["player"]["color"])
                self.assertEqual("preserve privately", body["metadata"]["private_note"])
                self.assertNotIn("preserve privately", str(result["context"]))
                self.assertEqual(6, actor["ap"])
                self.assertEqual(0.25, actor["movement_m_per_ap"])
                self.assertEqual(0.225, actor["center"][2])
                self.assertEqual(body["position"][:2], actor["center"][:2])
                self.assertGreater(state["spatial_scene"]["map"]["revision"], scene["map"]["revision"])

    def test_readiness_tracks_spent_ap_chamber_and_weapon_cycle(self):
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        move = {"actor": "vesper", "destination": [3, 2]}
        self.assertIsNone(self.check("move_actor", move))
        actor["ap"] = 0
        self.assertIsNotNone(self.check("move_actor", move))
        actor["ap"] = actor["max_ap"]
        fire = {"attacker": "vesper", "target": "hostile_alpha",
                "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"}
        self.assertIsNone(self.check("fire_single", fire))

        actor["weapon"]["chamber"] = "EMPTY"
        self.assertIsNotNone(self.check("fire_single", fire))
        cycle = {"actor": "vesper"}
        actor["weapon"]["cycle_ap"] = 1
        actor["weapon"]["needs_cycle"] = False
        self.assertIsNotNone(self.check("cycle_weapon", cycle))
        actor["weapon"]["needs_cycle"] = True
        self.assertIsNone(self.check("cycle_weapon", cycle))
        self.execute("cycle_weapon", cycle)
        self.assertIsNotNone(self.check("cycle_weapon", cycle))
        self.assertIsNone(self.check("fire_single", fire))

    def test_shot_effects_cover_every_expended_round_at_explicit_cadence(self):
        for tool, rounds in (("fire_single", 1), ("fire_burst", 3), ("fire_full_auto", 5)):
            with self.subTest(tool=tool):
                self.setUp()
                actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
                actor["weapon"]["cyclic_rate_rpm"] = 600
                parameters = {"attacker": "vesper", "target": "hostile_alpha",
                              "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"}
                if tool == "fire_full_auto":
                    parameters["rounds"] = rounds
                fired = self.execute(tool, parameters)
                effects = fired["shot_effects"]
                self.assertFalse(effects["unavailable"])
                self.assertEqual(rounds, len(effects["shots"]))
                self.assertEqual([index * 100 for index in range(rounds)],
                                 [shot["delay_ms"] for shot in effects["shots"]])
                self.assertTrue(all(shot["map_id"] == "td-conformance-floor"
                                    for shot in effects["shots"]))
                self.assertNotIn("_fx_contact", fired["context"])
                actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
                actor["ap"] = 0
                rejected = self.execute(tool, parameters)
                self.assertNotIn("shot_effects", rejected)

    def test_missing_cadence_does_not_invent_a_rate_or_undo_the_attack(self):
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        actor["weapon"]["cyclic_rate_rpm"] = None
        fired = self.execute("fire_burst", {
            "attacker": "vesper", "target": "hostile_alpha", "aim_point": [9.5, 2.5, 0.9],
            "critical_effect": "DEVASTATING_HIT",
        })
        self.assertEqual(3, fired["context"]["_card_attack_rounds"])
        self.assertTrue(fired["shot_effects"]["unavailable"])
        self.assertEqual([], list(fired["shot_effects"]["shots"]))

    def test_movement_readiness_uses_the_real_bounded_route_and_its_ap_cost(self):
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        blocked = {"actor": "vesper", "destination": [6, 2]}
        self.assertEqual(
            "A movement route to this destination could not be found.",
            self.check("move_actor", blocked),
        )
        self.assertEqual([2, 2], actor["position_cell"])

        longer_route = {"actor": "vesper", "destination": [7, 2]}
        actor["ap"] = 1
        self.assertEqual(
            "This actor does not have enough action points for the movement route.",
            self.check("move_actor", longer_route),
        )
        self.assertEqual([2, 2], actor["position_cell"])

        actor["ap"] = 8
        self.assertIsNone(self.check("move_actor", longer_route))
        moved = self.execute("move_actor", longer_route)
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
        self.assertEqual(8, moved["context"]["_card_move_ap"])
        self.assertEqual(0, actor["ap"])
        self.assertEqual([7, 2], actor["position_cell"])

    def test_spatial_previews_reuse_movement_cost_and_each_firing_solution(self):
        move = {"actor": "vesper", "destination": [3, 2]}
        estimate = self.preview("move_actor", move)
        self.assertEqual([2.5, 2.5, 0], estimate["map_preview"]["path"]["points"][0])
        self.assertEqual([3.5, 2.5, 0], estimate["map_preview"]["path"]["points"][-1])
        self.assertEqual("Vesper", estimate["preview"]["summary"][0]["value"])
        estimated_ap = estimate["preview"]["summary"][2]["value"]
        moved = self.execute("move_actor", move)
        self.assertEqual("Vesper", moved["result_card"]["summary"][0]["value"])
        self.assertEqual([2, 2], moved["context"]["_card_move_origin"])
        move_details = {
            row["label"]: row["value"] for row in moved["result_card"]["details"]
        }
        self.assertEqual([2, 2], move_details["Origin"])
        self.assertEqual([3, 2], move_details["Destination"])
        self.assertEqual(
            [3, 2], self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]["position_cell"]
        )
        self.assertEqual(moved["context"]["_card_move_ap"], estimated_ap)

        base = {
            "attacker": "vesper",
            "target": "hostile_alpha",
            "aim_point": [9.5, 2.5, 0.9],
            "critical_effect": "DEVASTATING_HIT",
        }
        for tool_name in ("fire_single", "fire_burst", "fire_full_auto"):
            with self.subTest(tool=tool_name):
                parameters = {**base, **({"rounds": 5} if tool_name == "fire_full_auto" else {})}
                estimate = self.preview(tool_name, parameters)
                self.assertEqual("Vesper", estimate["preview"]["summary"][0]["value"])
                self.assertEqual("Hostile Alpha", estimate["preview"]["summary"][1]["value"])
                preview = estimate["map_preview"]
                self.assertEqual(
                    self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]["muzzle"],
                    preview["ray"]["origin"],
                )
                self.assertGreater(preview["exposure"]["fraction"], 0)
                self.assertLess(preview["exposure"]["fraction"], 1)

        fired = self.execute("fire_single", base)
        fire_details = {
            row["label"]: row["value"] for row in fired["result_card"]["details"]
        }
        self.assertEqual("Vesper", fire_details["Attacker"])
        self.assertEqual("Hostile Alpha", fire_details["Target"])

    def test_campaign_map_identity_drives_routes_mutations_and_fired_rounds(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        scene = self.state[state_key]["spatial_scene"]
        scene["map"]["id"] = "authored-harbor"
        for group in ("geometry", "entities", "zones", "terrain"):
            for item in scene[group]:
                item["map_id"] = scene["map"]["id"]
        movement = {"actor": "vesper", "destination": [3, 2]}
        estimate = self.preview("move_actor", movement)
        self.assertEqual([3.5, 2.5, 0], estimate["map_preview"]["path"]["points"][-1])
        self.assertIsNone(self.check("move_actor", movement))
        self.execute("move_actor", movement)
        scene = self.state[state_key]["spatial_scene"]
        moved = next(item for item in scene["entities"] if item["id"] == "vesper")
        self.assertEqual([3.5, 2.5, 0], moved["position"])
        self.assertEqual("authored-harbor", moved["map_id"])
        fire = {"attacker": "vesper", "target": "hostile_alpha",
                "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"}
        self.assertIn("ray", self.preview("fire_burst", fire)["map_preview"])
        fired = self.execute("fire_burst", fire)
        self.assertEqual(3, len(fired["shot_effects"]["shots"]))
        self.assertTrue(all(shot["map_id"] == "authored-harbor"
                            for shot in fired["shot_effects"]["shots"]))
        self.assertEqual("authored-harbor", self.state[state_key]["spatial_scene"]["map"]["id"])

    def establish_network(self, actor_id="hex", *, through_security=False):
        """Enter the authored test interface and traverse actual links with reliable test skill."""
        actor = self.state[f"{builder.PACKAGE_ID}.entities"][actor_id]
        actor["skills"]["networking"] = 100
        actor["ap"] = 30
        self.execute("intrude_network", {"actor": actor_id, "node_id": "lobby_terminal", "action": "GET_IN"})
        self.execute("pivot_network", {"actor": actor_id, "node_id": "camera_gateway",
                     "route_step": ["lobby_terminal", "camera_gateway", "terminal_gateway"]})
        if through_security:
            self.execute("pivot_network", {"actor": actor_id, "node_id": "security_controller",
                         "route_step": ["camera_gateway", "security_controller", "gateway_security"]})
            self.execute("intrude_network", {"actor": actor_id, "node_id": "security_controller", "action": "ESCALATE"})

    def test_network_readiness_preserves_discover_foothold_and_real_costs(self):
        for action in ("DISCOVER", "FOOTHOLD", "ESCALATE"):
            with self.subTest(action=action):
                self.setUp()
                self.establish_network()
                parameters = {"actor": "hex", "node_id": "camera_gateway", "action": action}
                self.assertIsNone(self.check("intrude_network", parameters))
                before_ap = self.state[f"{builder.PACKAGE_ID}.entities"]["hex"]["ap"]
                self.execute("intrude_network", parameters)
                self.assertLess(self.state[f"{builder.PACKAGE_ID}.entities"]["hex"]["ap"], before_ap)
                self.state[f"{builder.PACKAGE_ID}.entities"]["hex"]["ap"] = 0
                self.assertIsNotNone(self.check("intrude_network", parameters))

    def test_network_actions_without_access_explain_the_missing_session(self):
        for tool, parameters in (
            ("intrude_network", {"actor": "hex", "node_id": "lobby_terminal", "action": "FOOTHOLD"}),
            ("pivot_network", {"actor": "hex", "node_id": "camera_gateway",
                               "route_step": ["lobby_terminal", "camera_gateway", "terminal_gateway"]}),
        ):
            with self.subTest(tool=tool):
                self.setUp()
                world = self.state[f"{builder.PACKAGE_ID}.state"]
                world["network"]["nodes"]["camera_gateway"]["visible"] = True
                world["network"]["links"]["terminal_gateway"]["visible"] = True
                actor_before = deepcopy(self.state[f"{builder.PACKAGE_ID}.entities"]["hex"])
                sessions_before = deepcopy(world["network_sessions"])
                self.assertEqual("This actor has no live session on this device.",
                                 self.check(tool, parameters))
                self.execute(tool, parameters)
                self.assertEqual(actor_before, self.state[f"{builder.PACKAGE_ID}.entities"]["hex"])
                self.assertEqual(sessions_before,
                                 self.state[f"{builder.PACKAGE_ID}.state"]["network_sessions"])

    def test_network_route_preview_uses_current_device_labels_in_path_order(self):
        self.establish_network(through_security=True)
        args = {"actor": "hex", "node_id": "security_controller",
                "remote_function": "open_security_door"}
        card = self.preview("operate_remote_function", args)["preview"]
        labels = [row["value"] for row in card["summary"] if row["label"] not in {"Access", "AP cost"}]
        self.assertEqual(["Lobby terminal", "Camera gateway", "Security controller"], labels)
        state = self.state[f"{builder.PACKAGE_ID}.state"]
        state["network"]["nodes"]["camera_gateway"]["label"] = "Renamed gateway"
        card = self.preview("operate_remote_function", args)["preview"]
        labels = [row["value"] for row in card["summary"] if row["label"] not in {"Access", "AP cost"}]
        self.assertEqual(["Lobby terminal", "Renamed gateway", "Security controller"], labels)

    def test_fire_modes_do_not_own_campaign_mission_or_require_fixture_physical_state(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        for name in ("fire_single", "fire_burst", "fire_full_auto"):
            with self.subTest(name=name):
                self.setUp()
                world = self.state[state_key]
                world.pop("physical")
                world["mission"] = {"status": "ACTIVE", "authored_goal": "evacuate"}
                world["objectives"] = []
                self.state[f"{builder.PACKAGE_ID}.entities"]["hostile_alpha"]["downed"] = True
                parameters = {"attacker": "vesper", "target": "hostile_alpha",
                              "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"}
                if name == "fire_full_auto":
                    parameters["rounds"] = 5
                result = self.execute(name, parameters)
                self.assertGreater(result["context"]["_card_attack_rounds"], 0)
                self.assertEqual({"status": "ACTIVE", "authored_goal": "evacuate"}, self.state[state_key]["mission"])
                self.assertEqual([], self.state[state_key]["objectives"])

    def test_prepared_fire_uses_authored_zones_and_rejects_missing_zones_without_spending(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        for tool, prepared, original in (
            ("prepare_overwatch", "overwatch", "overwatch-lane"),
            ("establish_suppression", "suppression", "suppression-lane"),
        ):
            with self.subTest(tool=tool):
                self.setUp()
                world = self.state[state_key]
                zone = next(item for item in world["spatial_scene"]["zones"] if item["id"] == original)
                zone["id"] = "harbor-approach"
                parameters = {"actor": "vesper", "zone_id": zone["id"]}
                if prepared == "suppression":
                    parameters["rounds"] = 5
                self.assertIsNone(self.check(tool, parameters))
                self.execute(tool, parameters)
                saved = deepcopy(self.state[state_key]["prepared"][prepared])
                self.assertEqual(zone["id"], saved["zone_id"])
                self.assertTrue(saved["active"])
                actor_before = deepcopy(self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"])
                self.execute(tool, {**parameters, "zone_id": "missing-zone"})
                self.assertEqual(actor_before, self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"])
                self.assertEqual(saved, self.state[state_key]["prepared"][prepared])

    def test_suppression_expires_on_its_owners_next_team_turn_without_refund(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        entity_key = f"{builder.PACKAGE_ID}.entities"
        self.execute("establish_suppression", {
            "actor": "vesper", "zone_id": "suppression-lane", "rounds": 10,
        })
        weapon = deepcopy(self.state[entity_key]["vesper"]["weapon"])
        self.execute("end_allies_turn", {})
        self.assertTrue(self.state[state_key]["prepared"]["suppression"]["active"])
        self.execute("end_hostiles_turn", {})
        prepared = self.state[state_key]["prepared"]["suppression"]
        self.assertFalse(prepared["active"])
        self.assertEqual(0, prepared["capacity"])
        self.assertEqual(weapon, self.state[entity_key]["vesper"]["weapon"])

    def test_suppression_ends_only_after_its_owners_successful_incompatible_action(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        entity_key = f"{builder.PACKAGE_ID}.entities"
        cases = (
            ("move_actor", {"actor": "vesper", "destination": [3, 2]}),
            ("reload_weapon", {"actor": "vesper", "disposition": "RETAIN"}),
            ("recover_recoil", {"actor": "vesper", "ap": 1}),
            *[(tool, {"attacker": "vesper", "target": "hostile_alpha",
                      "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEEP_HIT", **extra})
              for tool, extra in (("fire_single", {}), ("fire_burst", {}),
                                  ("fire_full_auto", {"rounds": 5}))],
        )
        for tool, args in cases:
            for rejected in (False, True):
                with self.subTest(tool=tool, rejected=rejected):
                    self.setUp()
                    self.execute("establish_suppression", {
                        "actor": "vesper", "zone_id": "suppression-lane", "rounds": 10,
                    })
                    self.state[state_key]["prepared"]["overwatch"].update(owner="hex", active=True)
                    if rejected:
                        self.state[entity_key]["vesper"]["ap"] = 0
                    before = deepcopy(self.state[entity_key])
                    prepared = deepcopy(self.state[state_key]["prepared"])
                    self.execute(tool, args)
                    current = self.state[state_key]["prepared"]
                    if rejected:
                        self.assertEqual(before, self.state[entity_key])
                        self.assertEqual(prepared, current)
                    else:
                        self.assertLess(self.state[entity_key]["vesper"]["ap"], before["vesper"]["ap"])
                        self.assertFalse(current["suppression"]["active"])
                        self.assertEqual(0, current["suppression"]["capacity"])
                        self.assertEqual(prepared["overwatch"], current["overwatch"])

    def test_stationary_or_other_actor_movement_keeps_suppression(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        self.execute("establish_suppression", {
            "actor": "vesper", "zone_id": "suppression-lane", "rounds": 10,
        })
        prepared = deepcopy(self.state[state_key]["prepared"])
        self.execute("move_actor", {"actor": "vesper", "destination": [2, 2]})
        self.assertEqual(prepared, self.state[state_key]["prepared"])
        self.execute("move_actor", {"actor": "hex", "destination": [3, 3]})
        self.assertEqual(prepared, self.state[state_key]["prepared"])

    def test_prepared_crossing_reports_unaffordable_route_without_spending(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        self.execute("prepare_overwatch", {"actor": "vesper", "zone_id": "overwatch-lane"})
        self.execute("establish_suppression", {
            "actor": "hex", "zone_id": "suppression-lane", "rounds": 10,
        })
        self.execute("end_allies_turn", {})
        before = deepcopy(self.state)
        result = self.execute("resolve_trigger_crossing", {
            "actor": "hostile_alpha", "overwatcher": "vesper", "suppressor": "hex",
            "destination": [4, 2], "first_effect": "OVERWATCH",
            "overwatch_critical_effect": "DEEP_HIT",
            "suppression_critical_effect": "DEVASTATING_HIT",
        })
        self.assertEqual("INSUFFICIENT_AP", self.state[state_key]["last_trigger"]["status"])
        self.assertEqual(8, self.state[state_key]["last_trigger"]["required_ap"])
        self.assertEqual(6, self.state[state_key]["last_trigger"]["available_ap"])
        self.assertEqual("Not enough AP for this movement", result["result_card"]["summary"][0]["value"])
        before[state_key]["last_trigger"] = self.state[state_key]["last_trigger"]
        self.assertEqual(before, self.state)

    def test_prepared_crossing_uses_initial_ap_and_cannot_repeat_spent_reactions(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        entity_key = f"{builder.PACKAGE_ID}.entities"
        self.execute("prepare_overwatch", {"actor": "vesper", "zone_id": "overwatch-lane"})
        self.execute("establish_suppression", {
            "actor": "hex", "zone_id": "suppression-lane", "rounds": 10,
        })
        self.execute("end_allies_turn", {})
        self.state[entity_key]["hostile_alpha"]["ap"] = 3
        args = {
            "actor": "hostile_alpha", "overwatcher": "vesper", "suppressor": "hex",
            "destination": [7, 0], "first_effect": "OVERWATCH",
            "overwatch_critical_effect": "DEEP_HIT",
            "suppression_critical_effect": "DEVASTATING_HIT",
        }
        self.execute("resolve_trigger_crossing", args)
        trigger = self.state[state_key]["last_trigger"]
        self.assertIn(trigger["status"], ("RESUMED", "INTERRUPTED"))
        self.assertEqual(3, trigger["available_ap"])
        self.assertEqual("RESOLVED", trigger["overwatch"]["status"])
        self.assertEqual("RESOLVED", trigger["suppression"]["status"])
        before = deepcopy(self.state)
        self.execute("resolve_trigger_crossing", args)
        self.assertEqual("NO_ACTIVE_TRIGGER", self.state[state_key]["last_trigger"]["status"])
        before[state_key]["last_trigger"] = self.state[state_key]["last_trigger"]
        self.assertEqual(before, self.state)

    def test_prepared_crossing_reports_blocked_destination_without_spending(self):
        state_key = f"{builder.PACKAGE_ID}.state"
        self.execute("prepare_overwatch", {"actor": "vesper", "zone_id": "overwatch-lane"})
        self.execute("end_allies_turn", {})
        before = deepcopy(self.state)
        result = self.execute("resolve_trigger_crossing", {
            "actor": "hostile_alpha", "overwatcher": "vesper", "suppressor": "hex",
            "destination": [6, 2], "first_effect": "OVERWATCH",
            "overwatch_critical_effect": "DEEP_HIT",
            "suppression_critical_effect": "DEVASTATING_HIT",
        })
        self.assertEqual("NO_PATH", self.state[state_key]["last_trigger"]["status"])
        self.assertEqual("No valid movement path", result["result_card"]["summary"][0]["value"])
        before[state_key]["last_trigger"] = self.state[state_key]["last_trigger"]
        self.assertEqual(before, self.state)

    def test_network_costs_come_from_authored_actions_not_ambient_test_discounts(self):
        self.establish_network()
        package = self.state[f"{builder.PACKAGE_ID}.state"]
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["hex"]
        actor["intrusion_ap_discount"] = 100
        actor["ap"] = 1
        rule = package["network_intrusions"]["camera_gateway"]["actions"]["FOOTHOLD"]
        rule["ap"] = 2
        parameters = {"actor": "hex", "node_id": "camera_gateway", "action": "FOOTHOLD"}
        self.assertIsNotNone(self.check("intrude_network", parameters))
        rule["ap"] = 1
        self.assertIsNone(self.check("intrude_network", parameters))
        self.execute("intrude_network", parameters)
        self.assertEqual(0, self.state[f"{builder.PACKAGE_ID}.entities"]["hex"]["ap"])
        foothold = self.state[f"{builder.PACKAGE_ID}.state"]["network_footholds"]["hex"]["nodes"]["camera_gateway"]
        self.assertTrue(foothold["active"])

    def test_move_then_rejection_cannot_reuse_previous_card_values(self):
        parameters = {"actor": "vesper", "destination": [3, 2]}
        moved = self.execute("move_actor", parameters)
        card = deepcopy(moved["result_card"])
        self.assertIsNotNone(card)
        self.assertEqual("MOVED", moved["context"]["_card_move_status"])
        self.assertEqual("Moved", card["summary"][1]["value"])
        self.assertGreater(moved["context"]["_card_move_ap"], 0)
        self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]["ap"] = 0
        rejected = self.execute("move_actor", {**parameters, "destination": [4, 2]})
        self.assertEqual("NO_AP_OR_INACTIVE", rejected["context"]["_card_move_status"])
        self.assertIsNone(rejected["context"]["_card_move_ap"])
        self.assertIsNone(rejected["context"]["_card_move_distance"])
        self.assertIsNotNone(rejected["result_card"])
        self.assertNotIn("AP spent", [row.get("label") for row in rejected["result_card"]["summary"]])
        self.assertEqual(card, moved["result_card"])

    def test_firing_then_rejection_cannot_reuse_a_previous_roll(self):
        for tool in ("fire_single", "fire_burst", "fire_full_auto"):
            with self.subTest(tool=tool):
                self.setUp()
                parameters = {
                    "attacker": "vesper", "target": "hostile_alpha",
                    "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT",
                }
                if tool == "fire_full_auto":
                    parameters["rounds"] = 5
                fired = self.execute(tool, parameters)
                card = deepcopy(fired["result_card"])
                self.assertIsNotNone(card)
                self.assertIsInstance(fired["context"]["_card_attack_roll"], int)
                self.state[f"{builder.PACKAGE_ID}.entities"]["vesper"]["ap"] = 0
                rejected = self.execute(tool, parameters)
                self.assertEqual("FIRE_REJECTED", rejected["context"]["_card_attack_outcome"])
                self.assertIsNone(rejected["context"]["_card_attack_roll"])
                self.assertIsNone(rejected["context"]["_card_attack_total"])
                self.assertEqual(0, rejected["context"]["_card_attack_rounds"])
                self.assertIsNotNone(rejected["result_card"])
                self.assertEqual("Unable to fire", rejected["result_card"]["summary"][0]["value"])
                self.assertEqual(card, fired["result_card"])

    def test_team_handoff_changes_owner_and_preserves_independent_ap(self):
        package_state = f"{builder.PACKAGE_ID}.state"
        entities = f"{builder.PACKAGE_ID}.entities"
        allied_ids = list(self.state[package_state]["eligible_actor_ids"])
        self.assertEqual("player", self.state[package_state]["decision_owner"])
        self.execute("move_actor", {"actor": "vesper", "destination": [3, 2]})
        self.assertLess(self.state[entities]["vesper"]["ap"], self.state[entities]["hex"]["ap"])
        self.execute("end_allies_turn", {})
        self.assertEqual("gm", self.state[package_state]["decision_owner"])
        self.assertEqual(["hostile_alpha"], self.state[package_state]["eligible_actor_ids"])
        self.assertTrue(all(self.state[entities][actor]["ap"] == 0 for actor in allied_ids))
        self.execute("end_hostiles_turn", {})
        self.assertEqual("player", self.state[package_state]["decision_owner"])
        self.assertEqual(allied_ids, self.state[package_state]["eligible_actor_ids"])
        for actor in allied_ids:
            self.assertEqual(self.state[entities][actor]["max_ap"], self.state[entities][actor]["ap"])
        self.assertEqual(0, self.state[entities]["hostile_alpha"]["ap"])

    def test_authored_roster_preserves_actor_pools_and_rejects_partial_handoff(self):
        from sylvia_gm.application.package_runtime import PackageRuntimeError

        package_key = f"{builder.PACKAGE_ID}.state"
        entities_key = f"{builder.PACKAGE_ID}.entities"
        template = self.state[entities_key]["vesper"]
        actors = {
            identity: {**deepcopy(template), "id": identity, "side": side,
                       "max_ap": pool, "ap": 2, "active": active, "downed": downed}
            for identity, side, pool, active, downed in (
                ("courier", "allies", 4, True, False),
                ("medic", "allies", 5, True, False),
                ("yard-guard", "hostiles", 3, True, False),
                ("casualty", "allies", 6, True, True),
                ("offline-drone", "allies", 2, False, False),
            )
        }
        self.state[entities_key] = actors
        self.state[package_key]["turn_roster"] = list(actors)
        self.state[package_key]["eligible_actor_ids"] = ["courier", "medic"]
        outcome = self.execute("end_allies_turn", {})
        self.assertEqual(["yard-guard"], self.state[package_key]["eligible_actor_ids"])
        self.assertEqual(3, self.state[entities_key]["yard-guard"]["ap"])
        self.assertNotIn("yard-guard", str(outcome["context"]))
        self.execute("end_hostiles_turn", {})
        self.assertEqual(["courier", "medic"], self.state[package_key]["eligible_actor_ids"])
        self.assertEqual({"courier": 4, "medic": 5, "yard-guard": 0,
                          "casualty": 0, "offline-drone": 0},
                         {identity: actor["ap"] for identity, actor in self.state[entities_key].items()})
        self.state[package_key]["turn_roster"].insert(2, "missing-actor")
        before = deepcopy(self.state)
        with self.assertRaises(PackageRuntimeError):
            self.execute("end_allies_turn", {})
        self.assertEqual(before, self.state)

    def test_remote_door_retraction_updates_physical_clearance_and_shot_contact(self):
        from sylvia_gm.spatial import SpatialEngine

        package_state = self.state[f"{builder.PACKAGE_ID}.state"]
        geometry = {item["id"]: item for item in package_state["spatial_scene"]["geometry"]}
        self.assertTrue(geometry["partial-cover"]["occupies_cells"])
        self.assertFalse(geometry["full-wall"]["occupies_cells"])
        self.assertFalse(geometry["security-door"]["occupies_cells"])
        query = {
            "kind": "ray", "map_id": "td-conformance-floor", "origin": [9, 4.5, 1],
            "destination": [11, 4.5, 1], "include": ["geometry"], "max_intersections": 8,
        }
        path = {
            "kind": "path", "map_id": "td-conformance-floor", "start": [9, 4],
            "goal": [11, 4], "allow_diagonal": False, "corner_policy": "forbid_cutting",
            "include_entities": False, "max_steps": 256,
        }
        closed = SpatialEngine(package_state["spatial_scene"])
        self.assertEqual("security-door", closed.query(query).as_payload()["intersections"][0]["id"])

        self.assertGreater(closed.query(path).as_payload()["cost"], 2)
        actor_id = "participant-admitted-hex"
        entities = self.state[f"{builder.PACKAGE_ID}.entities"]
        entities[actor_id] = entities.pop("hex")
        entities[actor_id]["id"] = actor_id
        package_state["eligible_actor_ids"] = [
            actor_id if value == "hex" else value for value in package_state["eligible_actor_ids"]
        ]
        package_state["network_entries"][actor_id] = package_state["network_entries"].pop("hex")
        package_state["network_motion"][actor_id] = package_state["network_motion"].pop("hex")
        self.establish_network(actor_id, through_security=True)
        self.execute("operate_remote_function", {
            "actor": actor_id, "node_id": "security_controller",
            "remote_function": "open_security_door",
        })
        self.assertTrue(self.state[f"{builder.PACKAGE_ID}.state"]["last_network"]["success"])
        current = self.state[f"{builder.PACKAGE_ID}.state"]["spatial_scene"]
        opened = SpatialEngine(current)
        self.assertEqual([], opened.query(query).as_payload()["intersections"])
        self.assertEqual(2, opened.query(path).as_payload()["cost"])
        self.assertEqual("security-door", closed.query(query).as_payload()["intersections"][0]["id"])

    def test_drone_actions_use_authored_operator_device_and_target_identities(self):
        encoded = json.dumps(self.state)
        for old, new in (("hex", "harbor-operator"), ("fpv_alpha", "courier-drone"),
                         ("autodrone_alpha", "sentry-drone"), ("drone_target", "harbor-target")):
            encoded = encoded.replace(old, new)
        self.state = json.loads(encoded)
        self.state[f"{builder.PACKAGE_ID}.entities"]["courier-drone"]["name"] = "Courier Survey Unit"
        before = deepcopy(self.state)
        parameters = {"actor": "harbor-operator", "drone": "courier-drone",
                      "target": "harbor-target", "landing_point": [31.5, 30.5, 0]}
        self.assertIsNone(self.check("fpv_fragmentation_strike", parameters))
        self.assertIsNotNone(self.check("fpv_fragmentation_strike", {**parameters, "actor": "vesper"}))
        self.execute("fpv_fragmentation_strike", parameters)
        entities = self.state[f"{builder.PACKAGE_ID}.entities"]
        self.assertFalse(entities["courier-drone"]["active"])
        wreck = next(item for item in self.state[f"{builder.PACKAGE_ID}.state"]["spatial_scene"]["entities"]
                     if item["id"] == "courier-drone")
        self.assertEqual("Courier Survey Unit", wreck["metadata"]["player"]["label"])
        self.assertEqual("destroyed", wreck["metadata"]["player"]["state"])
        self.assertLess(entities["harbor-operator"]["ap"], before[f"{builder.PACKAGE_ID}.entities"]["harbor-operator"]["ap"])
        self.state = before
        self.assertIsNone(self.check("autodrone_fire", {
            "actor": "sentry-drone", "target": "harbor-target", "aim_point": [31.5, 30.5, 0.9],
        }))
        rounds = self.state[f"{builder.PACKAGE_ID}.entities"]["sentry-drone"]["weapon"]["magazine"]["rounds"]
        self.execute("autodrone_fire", {
            "actor": "sentry-drone", "target": "harbor-target", "aim_point": [31.5, 30.5, 0.9],
        })
        self.assertEqual(rounds - 1, self.state[f"{builder.PACKAGE_ID}.entities"]["sentry-drone"]["weapon"]["magazine"]["rounds"])

    def test_fpv_strike_resolves_at_aim_height_and_map_edge_without_extending_its_radius(self):
        for offset, height, target_offset, reaches_target in (
            (0, 0.9, 0, True),
            (24, 0, 0, True),
            (0, 0.9, 26, False),
        ):
            with self.subTest(offset=offset, height=height, target_offset=target_offset):
                self.setUp()
                entities = self.state[f"{builder.PACKAGE_ID}.entities"]
                scene = self.state[f"{builder.PACKAGE_ID}.state"]["spatial_scene"]
                # Guaranteed placement isolates the blast boundary from scatter randomness.
                entities["hex"]["skills"]["drone_operations"] = 100
                for identity, shift in (("fpv_alpha", offset), ("drone_target", offset + target_offset)):
                    entity = entities[identity]
                    for field in ("position_cell", "position_point", "center", "muzzle"):
                        if field in entity:
                            entity[field][0] += shift
                    body = next(item for item in scene["entities"] if item["id"] == identity)
                    body["position"][0] += shift
                initial_hp = entities["drone_target"]["hp"]
                initial_res = entities["drone_target"]["res"]
                initial_ap = entities["hex"]["ap"]
                parameters = {
                    "actor": "hex", "drone": "fpv_alpha", "target": "drone_target",
                    "landing_point": [31.5 + offset, 30.5, height],
                }
                self.assertIsNone(self.check("fpv_fragmentation_strike", parameters))
                self.execute("fpv_fragmentation_strike", parameters)
                entities = self.state[f"{builder.PACKAGE_ID}.entities"]
                result = self.state[f"{builder.PACKAGE_ID}.state"]["last_drone_attack"]
                self.assertEqual("DETONATED", result["status"])
                self.assertEqual(reaches_target, result["target_reached"])
                self.assertFalse(entities["fpv_alpha"]["active"])
                self.assertIsNone(entities["fpv_alpha"]["payload"])
                cost = self.parameters.values["equipment.fpv_fragmentation_payload.attack_ap"]
                self.assertEqual(initial_ap - cost, entities["hex"]["ap"])
                self.assertIsNotNone(self.check("fpv_fragmentation_strike", parameters))
                if reaches_target:
                    self.assertLess(entities["drone_target"]["hp"] + entities["drone_target"]["res"],
                                    initial_hp + initial_res)
                else:
                    self.assertEqual(initial_hp, entities["drone_target"]["hp"])
                    self.assertEqual(initial_res, entities["drone_target"]["res"])

    def test_host_admits_party_surface_declarations_and_current_tool_policies(self):
        specialties = builder._conformance_character()["artifact"]["specialties"]
        # The temporary enum permits descriptor construction; the unrelated specialty tool is
        # removed before admission and is never executed or claimed verified by this fixture.
        descriptor = builder._manifest(
            "1" * 64, specialties, ["not-executed"],
        )
        descriptor["tools"] = [tool for tool in descriptor["tools"]
                               if tool["name"] != "exercise_specialty_counterfactual"]
        files = {"runtime/ruleset.yaml": self.runtime_bytes,
                 "content/content-declarations.json": b"{}"}
        record = builder._complete_package_record(descriptor, files)
        admitted = manifest_from_record(record, format_version=4)
        self.assertEqual(builder.PACKAGE_ID, admitted.package_id)
        self.assertEqual([], next(surface for surface in descriptor["ui_surfaces"]
                                 if surface["renderer"] == "party")["actions"])
        party = next(surface for surface in descriptor["ui_surfaces"]
                     if surface["renderer"] == "unit_list")
        self.assertEqual(["change_stance"] * 3 + ["cycle_weapon", "reload_weapon", "recover_recoil", "end_allies_turn"],
                         [action["intent"] for action in party["actions"]])
        self.assertEqual(["STANDING", "CROUCHED", "PRONE"],
                         [action["params"]["stance"] for action in party["actions"][:3]])
        self.assertEqual("automatic", builder._tool_metadata("end_allies_turn", True)
                         ["player_controls"]["response"])
        network = next(surface for surface in descriptor["ui_surfaces"]
                       if surface["renderer"] == "network_graph")
        for action in network["actions"]:
            self.assertNotIn("actor", action["params"])
            self.assertEqual("actor", action["bindings"]["actor"])

    def test_status_panel_exposes_public_turn_status_without_hidden_scene_or_network(self):
        from sylvia_gm.application.tactical_grid import resolve_surface_source

        specialties = builder._conformance_character()["artifact"]["specialties"]
        descriptor = builder._manifest("1" * 64, specialties, ["not-executed"])
        source = next(surface["source"] for surface in descriptor["ui_surfaces"]
                      if surface["id"] == "operations.status")
        before = deepcopy(self.state)
        status = resolve_surface_source(source, state=self.state, participants=())
        self.assertEqual({"Active side": "allies", "Decision owner": "player",
                          "Mission status": "ACTIVE"}, status)
        self.assertEqual(before, self.state)
        # Hidden entities, network nodes and later private state must not enter this surface.
        self.state[f"{builder.PACKAGE_ID}.state"]["future_private_note"] = "unrevealed"
        self.execute("end_allies_turn", {})
        self.assertEqual({"Active side": "hostiles", "Decision owner": "gm",
                          "Mission status": "ACTIVE"},
                         resolve_surface_source(source, state=self.state, participants=()))

    def test_safe_recovery_changes_only_res_and_requires_an_explicit_maximum(self):
        actor = self.state[f"{builder.PACKAGE_ID}.entities"]["hex"]
        actor.update({"res": 2, "max_res": 10, "hp": 3, "ap": 1})
        before = deepcopy(actor)
        args = {"actor": "hex", "amount": 20, "situation": "UNSAFE_OR_INSUFFICIENT_TIME"}
        self.execute("recover_between_encounters", args)
        self.assertEqual(before, self.state[f"{builder.PACKAGE_ID}.entities"]["hex"])
        args["situation"] = "SECURED_WITH_TIME_AND_SUPPORT"
        self.execute("recover_between_encounters", args)
        self.assertEqual({**before, "res": 10}, self.state[f"{builder.PACKAGE_ID}.entities"]["hex"])
        self.assertNotIn("player_controls", builder._tool_metadata("recover_between_encounters", False))
        self.setUp()
        before = deepcopy(self.state[f"{builder.PACKAGE_ID}.entities"]["hex"])
        self.execute("recover_between_encounters", args)
        self.assertEqual(before, self.state[f"{builder.PACKAGE_ID}.entities"]["hex"])


if __name__ == "__main__":
    unittest.main()
