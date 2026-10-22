from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from balance_suite.parameters import default_manifest
from balance_suite.provenance import ENCODED_SOURCE_COMMIT
from sylvia_ruleset.builder import (
    CANONICAL_ROOTS,
    CONFORMANCE_SETTING_ID,
    INDEXED_REFERENCE_ROOTS,
    PACKAGE_ID,
    _complete_package_record,
    _verify_source_commit,
    build_draft,
)

ROOT = Path(__file__).parents[1]
HOST_COMPATIBILITY = {
    "host_contract": 1,
    "runtime_format": 1,
    "requires": [
        "shot_effects.v1",
        "spatial_clearance.v1",
        "spatial_mutation.v1",
        "spatial_path_moving_entity.v1",
        "tactical_grid.v1",
    ],
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generated_hashes(draft: Path) -> dict[str, str]:
    paths = [
        path
        for path in draft.rglob("*")
        if "output" not in path.relative_to(draft).parts
    ]
    return {
        path.relative_to(draft).as_posix(): _sha(path)
        for path in paths
        if path.is_file()
    }


def _canonical_hashes() -> dict[str, str]:
    paths: list[Path] = []
    for directory in CANONICAL_ROOTS:
        paths.extend((ROOT / directory).rglob("*.md"))
    return {
        path.relative_to(ROOT).as_posix(): _sha(path)
        for path in sorted(paths)
    }


class SylviaCanonicalReferenceBuildTests(unittest.TestCase):
    def test_build_packages_indexed_character_references_without_mutating_canon(self) -> None:
        source_hashes_before = {
            path.relative_to(ROOT).as_posix(): _sha(path)
            for directory in INDEXED_REFERENCE_ROOTS
            for path in sorted((ROOT / directory).rglob("*.md"))
        }
        source_commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory() as temporary:
            draft = Path(temporary) / "ruleset-draft"
            build_draft(ROOT, source_commit=source_commit, draft_path=draft)

            files_root = draft / "files"
            source_index = json.loads(
                (files_root / "provenance" / "source-index.json").read_text()
            )
            canonical_paths = {
                entry["path"]
                for entry in source_index["entries"]
                if Path(entry["path"]).parts[0] in INDEXED_REFERENCE_ROOTS
            }
            packaged_paths = {
                path.relative_to(files_root).as_posix()
                for path in (files_root / "references" / "canon").rglob("*.md")
            }
            self.assertEqual(
                {f"references/canon/{path}" for path in canonical_paths},
                packaged_paths,
            )
            self.assertFalse(
                any(path.startswith("references/canon/Testing/") for path in packaged_paths)
            )
            for source_path in canonical_paths:
                self.assertEqual(
                    (ROOT / source_path).read_bytes(),
                    (files_root / "references" / "canon" / source_path).read_bytes(),
                )

            guide_path = "references/character-authoring.md"
            guide = (files_root / guide_path).read_text(encoding="utf-8")
            required_targets = {
                "references/canon/Character/LEVEL.md",
                "references/canon/Character/SPECIALTY.md",
                "references/canon/Character/CLASS-SKILL INDEPENDENCE.md",
                "references/canon/Character/CHECK.md",
                "references/canon/Hubs/APTITUDES.md",
                "references/canon/Hubs/SKILLS.md",
                "references/canon/Combat/HP.md",
                "references/canon/Combat/RES.md",
                "references/canon/Combat/AP.md",
                "references/canon/Equipment/INVENTORY.md",
                "references/canon/Equipment/READY.md",
                "references/canon/Equipment/QUICK ACCESS.md",
                "references/canon/Equipment/STOWED.md",
            }
            self.assertTrue(required_targets <= packaged_paths)
            guide_targets = set(
                re.findall(r"`(references/canon/[^`]+\.md)`", guide)
            )
            self.assertTrue(required_targets <= guide_targets)
            self.assertTrue(guide_targets <= packaged_paths)

            runtime = json.loads(
                (files_root / "runtime" / "ruleset.yaml").read_text()
            )
            self.assertEqual(
                guide_path,
                runtime["definitions"]["authoring_defaults"][
                    "character_authoring_reference"
                ],
            )
            self.assertIn(
                guide_path,
                (files_root / "references" / "campaign-authoring.md").read_text(),
            )

            payloads = {
                path.relative_to(files_root).as_posix(): path.read_bytes()
                for path in files_root.rglob("*")
                if path.is_file()
            }
            descriptor = json.loads((draft / "package.json").read_text())
            record = _complete_package_record(descriptor, payloads)
            self.assertTrue(
                packaged_paths | {guide_path}
                <= {item["path"] for item in record["files"]}
            )
            declarations = json.loads(
                (files_root / "content" / "content-declarations.json").read_text()
            )
            artifacts = set(declarations["inclusions"][0]["links"]["artifacts"])
            self.assertTrue(packaged_paths | {guide_path} <= artifacts)

        self.assertEqual(
            source_hashes_before,
            {
                path.relative_to(ROOT).as_posix(): _sha(path)
                for directory in INDEXED_REFERENCE_ROOTS
                for path in sorted((ROOT / directory).rglob("*.md"))
            },
        )


class SylviaRulesetBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.draft = Path(cls._temporary.name) / "ruleset-draft"
        cls.source_commit = ENCODED_SOURCE_COMMIT
        build_draft(ROOT, source_commit=cls.source_commit, draft_path=cls.draft)
        cls.manifest = json.loads((cls.draft / "package.json").read_text())
        cls.setting_draft = cls.draft / "dependencies" / "conformance-setting"
        cls.setting_manifest = json.loads(
            (cls.setting_draft / "package.json").read_text()
        )
        cls.runtime = json.loads(
            (cls.draft / "files" / "runtime" / "ruleset.yaml").read_text()
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_build_is_byte_deterministic_and_does_not_modify_canon(self) -> None:
        canon_before = _canonical_hashes()
        first = _generated_hashes(self.draft)
        build_draft(ROOT, source_commit=self.source_commit, draft_path=self.draft)
        self.assertEqual(first, _generated_hashes(self.draft))
        self.assertEqual(canon_before, _canonical_hashes())

    def test_committed_draft_matches_deterministic_builder_output(self) -> None:
        self.assertEqual(
            _generated_hashes(self.draft),
            _generated_hashes(ROOT / "ruleset-draft"),
        )

    def test_format4_descriptor_has_closed_tools_and_exact_state_references(self) -> None:
        self.assertEqual(
            {"format": 1, "state_namespace": PACKAGE_ID},
            self.runtime["sylvia_gm_runtime"],
        )
        self.assertNotIn("meta", self.runtime)
        tools = {item["name"]: item for item in self.manifest["tools"]}
        self.assertEqual(24, len(tools))
        self.assertEqual(HOST_COMPATIBILITY, self.manifest["compatibility"])
        self.assertEqual(HOST_COMPATIBILITY, self.setting_manifest["compatibility"])
        for tool in tools.values():
            schema = tool["input_schema"]
            self.assertEqual("object", schema["type"])
            self.assertIs(False, schema["additionalProperties"])
            self.assertNotIn("active_side", schema["properties"])
        for name in (
            "aim",
            "fire_burst",
            "fire_full_auto",
            "fire_single",
            "fpv_fragmentation_strike",
            "intrude_network",
            "move_actor",
            "operate_remote_function",
            "autodrone_fire",
            "resolve_cqc_displacement",
            "resolve_trigger_crossing",
        ):
            property_name = (
                "attacker"
                if name in {"aim", "fire_burst", "fire_full_auto", "fire_single"}
                else "actor"
            )
            self.assertEqual(
                "entity",
                tools[name]["input_schema"]["properties"][property_name][
                    "x-sylvia-gm-reference"
                ],
            )
        self.assertEqual(
            [{"namespace": PACKAGE_ID, "key": "state.active_side", "equals": "allies"}],
            tools["end_allies_turn"]["state_predicates"],
        )

    def test_validation_only_setting_has_exact_ruleset_snapshot(self) -> None:
        ruleset_files_root = self.draft / "files"
        ruleset_payloads = {
            path.relative_to(ruleset_files_root).as_posix(): path.read_bytes()
            for path in ruleset_files_root.rglob("*")
            if path.is_file()
        }
        ruleset_record = _complete_package_record(self.manifest, ruleset_payloads)
        files_root = self.setting_draft / "files"
        setting_payloads = {
            path.relative_to(files_root).as_posix(): path.read_bytes()
            for path in files_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual("primary", self.manifest["role"])
        self.assertEqual("ruleset", self.manifest["content_kind"])
        self.assertEqual([], self.manifest["dependencies"])
        self.assertEqual("dependency", self.setting_manifest["role"])
        self.assertEqual("campaign_setting", self.setting_manifest["content_kind"])
        self.assertEqual(CONFORMANCE_SETTING_ID, self.setting_manifest["id"])
        self.assertEqual(
            "validation_only",
            json.loads(setting_payloads["conformance/setting.json"])["purpose"],
        )
        self.assertEqual(
            [
                {
                    "namespace": "ruleset_snapshot",
                    "kind": "ruleset_snapshot",
                    "id": PACKAGE_ID,
                    "version": self.manifest["version"],
                    "content_sha256": ruleset_record["content_sha256"],
                }
            ],
            self.setting_manifest["dependencies"],
        )

    def test_runtime_guards_actor_side_and_uses_only_host_spatial_queries(self) -> None:
        runtime_tools = self.runtime["tools"]
        for name, tool in runtime_tools.items():
            self.assertTrue(tool["metadata"]["source_provenance"])
            if name not in {"end_allies_turn", "end_hostiles_turn"}:
                hidden = next(
                    item for item in tool["parameters"] if item["name"] == "active_side"
                )
                self.assertEqual("state.active_side", hidden["from_state"])
                effects = iter(tool["effects"])
                first = next(effects)
                while "set" in first and first["set"]["field"].startswith("_card_"):
                    first = next(effects)
                self.assertIn(".side == active_side", first["condition"])
        encoded_fire = json.dumps(runtime_tools["fire_single"], sort_keys=True)
        self.assertIn('"operation": "ray"', encoded_fire)
        self.assertIn('"operation": "exposure"', encoded_fire)
        self.assertIn("_single_route_target_reached", encoded_fire)
        self.assertIn("definitions.parameters.check.difficulty.standard", encoded_fire)
        self.assertIn("attacker.weapon.range_breakpoints_m", encoded_fire)
        self.assertIn("attacker.weapon.range_modifiers", encoded_fire)
        self.assertIn('"spatial_mutation"', encoded_fire)
        self.assertIn('"kind": "geometry"', encoded_fire)
        self.assertIn('"value": "_exposure.occluders"', encoded_fire)
        self.assertIn('"expected_revision": "{_shot_snapshot.geometry_revision}"', encoded_fire)
        self.assertIn("_cover_hp_after", encoded_fire)
        self.assertNotIn("supercover", encoded_fire.casefold())
        fire_schema = next(
            item for item in self.manifest["tools"] if item["name"] == "fire_single"
        )["input_schema"]
        self.assertNotIn("open_difficulty", fire_schema["properties"])
        self.assertNotIn("range_modifier", fire_schema["properties"])
        map_surface = next(
            item for item in self.manifest["ui_surfaces"] if item["id"] == "operations.map"
        )
        fire_action = next(item for item in map_surface["actions"] if item["id"] == "fire")
        self.assertNotIn("open_difficulty", fire_action["params"])
        self.assertNotIn("range_modifier", fire_action["params"])
        encoded_move = json.dumps(runtime_tools["move_actor"], sort_keys=True)
        self.assertIn('"operation": "path"', encoded_move)
        self.assertIn('"include_entities": true', encoded_move)
        self.assertIn('"moving_entity_id": "{_actor_id}"', encoded_move)
        self.assertIn('"max_steps": 256', encoded_move)
        self.assertIn('actor.movement_m_per_ap', encoded_move)
        self.assertNotIn(" / actor.movement_m_per_ap", encoded_move)
        self.assertIn('"spatial_mutation"', encoded_move)
        self.assertIn('"target": {"id": "{_actor_id}", "kind": "entity"}', encoded_move)
        self.assertIn('"expected_revision": "{_move_snapshot.geometry_revision}"', encoded_move)
        self.assertIn("{_actor_id}.position_cell", encoded_move)
        self.assertIn("{_actor_id}.center", encoded_move)
        self.assertIn("{_actor_id}.muzzle", encoded_move)

        encoded_fpv = json.dumps(
            runtime_tools["fpv_fragmentation_strike"], sort_keys=True
        )
        self.assertIn("actor.skills.drone_operations + actor.aptitudes.precision", encoded_fpv)
        self.assertIn('"operation": "snapshot"', encoded_fpv)
        self.assertIn('"operation": "area"', encoded_fpv)
        self.assertIn('"spatial_mutation"', encoded_fpv)
        self.assertIn('"id": "{_drone_id}", "kind": "entity"', encoded_fpv)
        self.assertIn("definitions.parameters.equipment.frag_grenade.damage", encoded_fpv)
        self.assertIn("definitions.parameters.equipment.fpv_fragmentation_payload.attack_ap", encoded_fpv)

        encoded_autodrone = json.dumps(
            runtime_tools["autodrone_fire"], sort_keys=True
        )
        self.assertIn("actor.standing_order.target == _target_id", encoded_autodrone)
        self.assertIn("actor.sensor_confirmed_targets", encoded_autodrone)
        self.assertIn("definitions.parameters.equipment.autodrone_light_weapon.targeting_bonus", encoded_autodrone)
        self.assertIn('"operation": "ray"', encoded_autodrone)
        self.assertIn('"spatial_mutation"', encoded_autodrone)

        encoded_door = json.dumps(
            runtime_tools["operate_remote_function"], sort_keys=True
        )
        self.assertIn('"spatial_mutation"', encoded_door)
        self.assertIn(
            '"target": {"id": "security-door", "kind": "geometry"}',
            encoded_door,
        )
        self.assertIn(
            '"expected_revision": "{_door_snapshot.geometry_revision}"',
            encoded_door,
        )
        self.assertIn('"state": "open"', encoded_door)

    def test_character_schema_requires_closed_runtime_stats(self) -> None:
        schema = self.manifest["participant_artifacts"][0]["schema"]
        skills = schema["properties"]["skills"]
        aptitudes = schema["properties"]["aptitudes"]
        expected_skills = {
            "athletics",
            "cqc",
            "drone_operations",
            "engineering",
            "grenades",
            "influence",
            "investigation",
            "local_hacking",
            "marksmanship",
            "medicine",
            "networking",
            "recon",
            "social_read",
            "stealth",
            "tactics",
            "vehicles",
        }
        expected_aptitudes = {"acuity", "force", "nerve", "precision", "presence", "reason"}
        self.assertEqual(expected_skills, set(skills["required"]))
        self.assertEqual(expected_skills, set(skills["properties"]))
        self.assertIs(False, skills["additionalProperties"])
        self.assertEqual(expected_aptitudes, set(aptitudes["required"]))
        self.assertEqual(expected_aptitudes, set(aptitudes["properties"]))
        self.assertIs(False, aptitudes["additionalProperties"])
        resolve_check = next(
            item for item in self.manifest["tools"] if item["name"] == "resolve_check"
        )
        self.assertEqual(
            expected_skills,
            set(resolve_check["input_schema"]["properties"]["skill"]["enum"]),
        )
        specialty_ids = schema["properties"]["specialties"]["items"]["enum"]
        self.assertEqual(364, len(specialty_ids))
        self.assertEqual(364, len(set(specialty_ids)))
        self.assertIn("specialty.networker.entry.map_the_links", specialty_ids)
        self.assertNotIn("NETWORKER", specialty_ids)

    def test_cqc_compiles_displacing_and_non_displacing_maneuvers(self) -> None:
        manifest_tool = next(
            item for item in self.manifest["tools"] if item["name"] == "resolve_cqc"
        )
        self.assertEqual(
            ["TRIP", "DISARM", "BODY_CONTROL", "WEAPON_CONTROL", "ESCAPE"],
            manifest_tool["input_schema"]["properties"]["maneuver"]["enum"],
        )
        self.assertEqual(
            ["PRIMARY_WEAPON"],
            manifest_tool["input_schema"]["properties"]["held_item"]["enum"],
        )
        self.assertNotIn(
            "held_item", manifest_tool["input_schema"]["required"]
        )
        encoded = json.dumps(self.runtime["tools"]["resolve_cqc"], sort_keys=True)
        self.assertIn('"operation": "path"', encoded)
        self.assertIn("_reach.cost <= 1.5", encoded)
        self.assertIn("actor.aptitudes.precision", encoded)
        self.assertIn("target.aptitudes.precision", encoded)
        self.assertIn("actor.aptitudes.force", encoded)
        self.assertIn("target.aptitudes.force", encoded)
        self.assertIn("'MISSING_PREREQUISITE'", encoded)
        self.assertIn("'OUT_OF_REACH_OR_NO_AP'", encoded)
        self.assertIn('"call": "td_drop_held_weapon"', encoded)
        self.assertIn('"actor": "{_target_id}"', encoded)
        self.assertIn("{_target_id}.control.controller", encoded)
        self.assertIn("{_target_id}.control.focus", encoded)
        self.assertIn("{_actor_id}.control.target", encoded)
        self.assertNotIn("'SHOVE'", encoded)
        self.assertNotIn("'THROW'", encoded)

        displacement_manifest = next(
            item
            for item in self.manifest["tools"]
            if item["name"] == "resolve_cqc_displacement"
        )
        self.assertEqual(
            ["SHOVE", "THROW"],
            displacement_manifest["input_schema"]["properties"]["maneuver"]["enum"],
        )
        self.assertIn("destination", displacement_manifest["input_schema"]["required"])
        displacement = json.dumps(
            self.runtime["tools"]["resolve_cqc_displacement"], sort_keys=True
        )
        self.assertIn('"include_entities": true', displacement)
        self.assertIn('"moving_entity_id": "{_target_id}"', displacement)
        self.assertIn('"spatial_mutation"', displacement)
        self.assertIn(
            '"target": {"id": "{_target_id}", "kind": "entity"}',
            displacement,
        )
        self.assertIn("definitions.parameters.cqc.shove_distance_m", displacement)
        self.assertIn("definitions.parameters.cqc.throw_distance_m", displacement)
        self.assertIn("{_target_id}.position_cell", displacement)
        self.assertIn("{_target_id}.center", displacement)
        self.assertIn("{_target_id}.muzzle", displacement)
        self.assertIn("{_target_id}.stance", displacement)

        coverage = json.loads(
            (self.draft / "files" / "conformance" / "coverage.json").read_text()
        )
        cqc = next(item for item in coverage["mechanics"] if item["id"] == "cqc")
        self.assertEqual("partial", cqc["status"])
        self.assertIn("TRIP", cqc["conformance"])
        self.assertIn("DISARM", cqc["conformance"])
        self.assertIn("BODY CONTROL", cqc["conformance"])
        self.assertIn("WEAPON CONTROL", cqc["conformance"])
        self.assertIn("ESCAPE", cqc["conformance"])
        self.assertIn("SHOVE", cqc["conformance"])
        self.assertIn("THROW", cqc["conformance"])

    def test_weapon_control_blocks_only_declared_weapon_actions(self) -> None:
        for name in (
            "aim",
            "fire_single",
            "fire_burst",
            "fire_full_auto",
            "prepare_overwatch",
            "establish_suppression",
        ):
            encoded = json.dumps(self.runtime["tools"][name], sort_keys=True)
            reference = (
                "attacker"
                if name in {"aim", "fire_single", "fire_burst", "fire_full_auto"}
                else "actor"
            )
            self.assertIn(f"{reference}.weapon.controlled_by == null", encoded)
            self.assertIn(f"{reference}.weapon.location == 'READY'", encoded)

        encoded_check = json.dumps(self.runtime["tools"]["resolve_check"], sort_keys=True)
        encoded_network = json.dumps(
            self.runtime["tools"]["intrude_network"], sort_keys=True
        )
        self.assertNotIn("weapon.controlled_by", encoded_check)
        self.assertNotIn("weapon.controlled_by", encoded_network)

    def test_firearm_state_preserves_chambers_and_physical_magazines(self) -> None:
        firing = json.dumps(self.runtime["tools"]["fire_single"], sort_keys=True)
        cycling = json.dumps(self.runtime["tools"]["cycle_weapon"], sort_keys=True)
        reloading = json.dumps(self.runtime["tools"]["reload_weapon"], sort_keys=True)
        suppression = json.dumps(
            self.runtime["tools"]["establish_suppression"], sort_keys=True
        )
        self.assertIn("{_attacker_id}.weapon.chamber", firing)
        self.assertIn("{_attacker_id}.weapon.magazine.rounds", firing)
        self.assertIn("{_attacker_id}.weapon.needs_cycle", firing)
        self.assertIn("actor.weapon.cycle_ap", cycling)
        self.assertIn("{_actor_id}.weapon.magazine.rounds", cycling)
        self.assertIn('"list_pop"', reloading)
        self.assertIn('"list_push"', reloading)
        self.assertIn("actor.weapon.retained_magazines", reloading)
        self.assertIn("state.ground_objects.magazine_{_drop_index}", reloading)
        self.assertIn("actor.weapon.feed == 'DETACHABLE_MAGAZINE'", reloading)
        self.assertIn("actor.weapon.magazine.rounds + 1 >= rounds", suppression)
        self.assertIn("_pre_suppression_magazine_rounds < rounds", suppression)
        self.assertIn("{_actor_id}.weapon.chamber", suppression)

        reload_manifest = next(
            item for item in self.manifest["tools"] if item["name"] == "reload_weapon"
        )
        self.assertEqual(
            ["RETAIN", "DROP"],
            reload_manifest["input_schema"]["properties"]["disposition"]["enum"],
        )
        self.assertNotIn(
            "ground_object_sequence", reload_manifest["input_schema"]["properties"]
        )

    def test_multi_round_fire_uses_one_roll_and_fresh_geometry_per_projectile(self) -> None:
        manifest_tools = {item["name"]: item for item in self.manifest["tools"]}
        self.assertIn("fire_burst", manifest_tools)
        self.assertIn("fire_full_auto", manifest_tools)
        self.assertNotIn(
            "rounds", manifest_tools["fire_burst"]["input_schema"]["properties"]
        )
        self.assertEqual(
            (3, 20),
            (
                manifest_tools["fire_full_auto"]["input_schema"]["properties"][
                    "rounds"
                ]["minimum"],
                manifest_tools["fire_full_auto"]["input_schema"]["properties"][
                    "rounds"
                ]["maximum"],
            ),
        )

        for name, mode in (("fire_burst", "BURST"), ("fire_full_auto", "FULL_AUTO")):
            tool = self.runtime["tools"][name]
            encoded = json.dumps(tool, sort_keys=True)
            self.assertEqual(1, encoded.count('"dice": {"expression": "d20"}'))
            self.assertIn(f"'{mode}' in attacker.weapon.fire_modes", encoded)
            self.assertIn("_connection_opportunities", encoded)
            self.assertIn("_target_connections", encoded)
            self.assertIn("_blocked_connections", encoded)
            self.assertIn("_projectile_slots", encoded)
            self.assertIn('"for_each"', encoded)
            self.assertIn('"operation": "snapshot"', encoded)
            self.assertIn('"operation": "ray"', encoded)
            self.assertIn('"spatial_mutation"', encoded)
            self.assertIn(
                '"value": "_projectile_snapshot.geometry_revision"',
                encoded,
            )
            self.assertIn("_projectile_route_geometry_revision", encoded)
            self.assertIn("attacker.weapon.current_recoil", encoded)
            self.assertIn("{_attacker_id}.weapon.magazine.rounds", encoded)

        coverage = json.loads(
            (self.draft / "files" / "conformance" / "coverage.json").read_text()
        )
        mechanic = next(
            item
            for item in coverage["mechanics"]
            if item["id"] == "multi-round-sequential-geometry"
        )
        self.assertEqual("implemented", mechanic["status"])
        self.assertIn("fire_burst", mechanic["conformance"])
        self.assertIn("fire_full_auto", mechanic["conformance"])

    def test_trigger_crossing_pauses_resolves_and_resumes_real_movement(self) -> None:
        encoded = json.dumps(
            self.runtime["tools"]["resolve_trigger_crossing"], sort_keys=True
        )
        self.assertIn('"operation": "path"', encoded)
        self.assertIn('"operation": "crossed_zones"', encoded)
        self.assertIn("_trigger_point", encoded)
        self.assertIn("_trigger_aim_center", encoded)
        self.assertIn("_trigger_point[0] + 0.000001", encoded)
        self.assertIn("_trigger_pause_mutation", encoded)
        self.assertIn("first_effect == 'OVERWATCH'", encoded)
        self.assertIn("first_effect == 'SUPPRESSION'", encoded)
        self.assertIn("state.prepared.overwatch.shots", encoded)
        self.assertIn("state.prepared.suppression.capacity", encoded)
        self.assertIn("_trigger_resume_snapshot", encoded)
        self.assertIn("'RESUMED'", encoded)
        self.assertIn("'INTERRUPTED'", encoded)
        self.assertGreaterEqual(encoded.count('"dice": {"expression": "d20"}'), 4)

    def test_every_projectile_surface_routes_ordered_material_intersections(self) -> None:
        expected = {
            "fire_single": "_single_route",
            "fire_burst": "_projectile_route",
            "fire_full_auto": "_projectile_route",
            "fpv_fragmentation_strike": "_fragment_route",
            "autodrone_fire": "_drone_route",
        }
        for tool_name, prefix in expected.items():
            encoded = json.dumps(self.runtime["tools"][tool_name], sort_keys=True)
            self.assertIn(f"{prefix}_intersection", encoded)
            self.assertIn(f"{prefix}_continues", encoded)
            self.assertIn(f"{prefix}_target_reached", encoded)
            self.assertIn("'STOP'", encoded)
            self.assertIn("'REDUCE'", encoded)
            self.assertIn("'PENETRATE'", encoded)
            self.assertIn("material_resistance", encoded)
            self.assertIn("spatial_mutation", encoded)

    def test_conformance_objective_requires_both_tactical_and_network_state(self) -> None:
        firing = json.dumps(self.runtime["tools"]["fire_single"], sort_keys=True)
        operating = json.dumps(
            self.runtime["tools"]["operate_remote_function"], sort_keys=True
        )
        self.assertIn("state.mission.hostile_neutralized", firing)
        self.assertIn("security_door_open == true", firing)
        self.assertIn("hostile_alpha.downed == true", operating)
        self.assertIn("state.mission.status", operating)

        scenario = json.loads(
            (self.draft / "files" / "conformance" / "scenario.json").read_text()
        )
        state = scenario["initial_state"][f"{PACKAGE_ID}.state"]
        self.assertEqual("ACTIVE", state["mission"]["status"])
        self.assertEqual(
            {"neutralize-hostile-alpha", "open-security-door"},
            {item["id"] for item in state["objectives"]},
        )

    def test_each_fire_mode_replaces_stale_attack_telemetry(self) -> None:
        single = json.dumps(self.runtime["tools"]["fire_single"]["effects"])
        burst = json.dumps(self.runtime["tools"]["fire_burst"]["effects"])
        full_auto = json.dumps(self.runtime["tools"]["fire_full_auto"]["effects"])

        for encoded in (single, burst, full_auto):
            self.assertIn('"field": "state.last_attack", "value": {}', encoded)
        self.assertIn('"field": "state.last_attack.mode", "value": "\'SEMI_AUTO\'"', single)
        self.assertIn('"field": "state.last_attack.rounds_fired", "value": 1', single)

    def test_multi_round_fire_conserves_chamber_and_magazine_ammunition(self) -> None:
        for tool_name, rounds in (("fire_burst", "3"), ("fire_full_auto", "rounds")):
            encoded = json.dumps(self.runtime["tools"][tool_name]["effects"])
            self.assertIn(
                f'"value": "_pre_fire_magazine - {rounds}"',
                encoded,
            )
            self.assertIn(
                f'"condition": "_pre_fire_magazine >= {rounds}"',
                encoded,
            )

    def test_specialty_runtime_applies_declared_operations_field_by_field(self) -> None:
        encoded = json.dumps(
            self.runtime["tools"]["exercise_specialty_counterfactual"]["effects"]
        )
        self.assertIn("_specialty_branch.mechanical_operations", encoded)
        self.assertIn("_specialty_rule.branch_fixtures[effect_branch]", encoded)
        self.assertNotIn("node_id + '|' + effect_branch", encoded)
        self.assertNotIn("specialty_branch_fixtures", self.runtime["definitions"])
        self.assertIn(
            "state.last_specialty_test.result.{_specialty_operation.field}",
            encoded,
        )
        self.assertNotIn(
            '"field": "state.last_specialty_test.result", "value": "_specialty_rule.candidate"',
            encoded,
        )
        self.assertNotIn("_specialty_rule.candidate", encoded)
        self.assertIn("_specialty_operation.amount", encoded)
        self.assertNotIn(
            "state.last_specialty_test.mechanical_operations_executed + [_specialty_operation]",
            encoded,
        )
        self.assertIn(
            '"field": "state.last_specialty_test.mechanical_operations_executed", "value": "_specialty_operations"',
            encoded,
        )
        self.assertIn("MISSING_PREREQUISITE", encoded)
        self.assertIn("negative_prerequisite_fixture", encoded)
        self.assertNotIn("OFF_ROLE_NODE_PRESENT", encoded)
        self.assertIn("role == 'OFF_ROLE'", encoded)
        self.assertIn("_specialty_actor_has_node == true", encoded)

    def test_specialty_runtime_exposes_only_approved_exclusive_branches(self) -> None:
        tool = next(
            item
            for item in self.manifest["tools"]
            if item["name"] == "exercise_specialty_counterfactual"
        )
        branch_schema = tool["input_schema"]["properties"]["effect_branch"]
        fixtures = {
            f"{node_id}|{branch_id}": fixture
            for node_id, record in self.runtime["definitions"][
                "specialty_counterfactuals"
            ].items()
            for branch_id, fixture in record["branch_fixtures"].items()
        }
        self.assertEqual(
            sorted({key.rsplit("|", 1)[1] for key in fixtures}),
            branch_schema["enum"],
        )
        self.assertEqual("DEFAULT", branch_schema["default"])
        self.assertTrue(
            {
                "LARGER_OPENING",
                "REDUCED_COLLATERAL",
                "PASSAGE",
                "FIRING_APERTURE",
                "EXTRA_AP",
                "REDUCED_MOVEMENT",
                "HP_RESTORATION",
                "INJURY_RECOVERY",
                "FASTER",
                "MORE_EFFECTIVE",
                "ACCESS",
                "BURDEN",
                "FRAGILE",
                "ONE_USE",
                "SENSOR_ORIENTATION",
                "MOUNTING",
                "STABILITY",
                "POWER_ROUTING",
                "OPERATING_MODE",
                "OBSTACLE",
                "HAZARD",
                "EXTREME_RANGE_DIFFICULTY",
                "EXPOSED_AREA_DIFFICULTY",
                "CALLED_SHOT_DIFFICULTY",
                "HIT_QUALITY",
                "TARGET_PENETRATION",
                "MATERIAL_PENETRATION",
            }.issubset(branch_schema["enum"])
        )
        perfect_prefix = "specialty.sniper.tier_5.perfect_solution|"
        self.assertIs(
            True,
            fixtures[f"{perfect_prefix}MATERIAL_PENETRATION"][
                "prerequisite_contract"
            ]["material_window_applies"],
        )
        self.assertNotIn(
            "material_window_applies",
            fixtures[f"{perfect_prefix}HIT_QUALITY"]["prerequisite_contract"],
        )
        emplace_prefix = "specialty.field_engineer.tier_3.emplace|"
        emplace = {
            key.removeprefix(emplace_prefix): value
            for key, value in fixtures.items()
            if key.startswith(emplace_prefix)
        }
        self.assertEqual(
            {"DEFAULT", "EFFECTIVENESS", "DURABILITY"}, set(emplace)
        )
        self.assertEqual("EFFECTIVENESS", emplace["DEFAULT"]["selected_branch"])
        for branch in ("EFFECTIVENESS", "DURABILITY"):
            quantitative_fields = {
                operation["field"]
                for operation in emplace[branch]["mechanical_operations"]
                if operation.get("parameter_id")
            }
            self.assertEqual(1, len(quantitative_fields))

        overdrive_prefix = "specialty.field_engineer.tier_4.overdrive|"
        overdrive = {
            key.removeprefix(overdrive_prefix): value
            for key, value in fixtures.items()
            if key.startswith(overdrive_prefix)
        }
        self.assertEqual(17, len(overdrive))
        self.assertEqual(
            "MOVEMENT_WEAR", overdrive["DEFAULT"]["selected_branch"]
        )
        for branch, fixture in overdrive.items():
            if branch == "DEFAULT":
                continue
            quantitative_fields = {
                operation["field"]
                for operation in fixture["mechanical_operations"]
                if operation.get("parameter_id")
            }
            self.assertEqual(2, len(quantitative_fields))

        suppression_prefix = "specialty.gunner.tier_1.suppression_discipline|"
        suppression = {
            key.removeprefix(suppression_prefix): value
            for key, value in fixtures.items()
            if key.startswith(suppression_prefix)
        }
        self.assertEqual(
            {"DEFAULT", "AMMUNITION", "FIRING_TIME"}, set(suppression)
        )
        self.assertEqual("AMMUNITION", suppression["DEFAULT"]["selected_branch"])
        for branch in ("AMMUNITION", "FIRING_TIME"):
            self.assertEqual(
                1,
                sum(
                    operation.get("field")
                    in {"ammunition_discount", "ap_discount"}
                    for operation in suppression[branch]["mechanical_operations"]
                ),
            )

    def test_hacking_and_firearms_spend_the_same_actor_ap_namespace(self) -> None:
        hacking = json.dumps(self.runtime["tools"]["intrude_network"]["effects"])
        firing = json.dumps(self.runtime["tools"]["fire_single"]["effects"])
        self.assertIn('{_actor_id}.ap', hacking)
        self.assertIn('{_attacker_id}.ap', firing)
        self.assertNotIn("network_ap_pool", hacking)
        self.assertEqual(
            "state.network.nodes.lobby_terminal.control",
            next(
                item
                for item in self.runtime["tools"]["intrude_network"]["parameters"]
                if item["name"] == "lobby_control"
            )["from_state"],
        )
        self.assertIn("_prerequisites_valid == true", hacking)
        self.assertIn(
            "'specialty.networker.entry.map_the_links' in actor.specialties",
            hacking,
        )
        self.assertIn(
            "'specialty.networker.tier_1.pivot' in actor.specialties",
            hacking,
        )
        self.assertIn(
            "definitions.parameters.specialty.networker.pivot.ap_discount",
            hacking,
        )
        self.assertIn(
            "'specialty.networker.tier_2.foothold' in actor.specialties",
            hacking,
        )
        self.assertIn(
            "definitions.parameters.specialty.networker.foothold.ap_discount",
            hacking,
        )
        self.assertIn(
            "action == 'FOOTHOLD' and node_id != 'lobby_terminal' and _node_control == 'compromised'",
            hacking,
        )
        hidden = {
            item["name"]: item["from_state"]
            for item in self.runtime["tools"]["intrude_network"]["parameters"]
            if "from_state" in item
        }
        self.assertEqual(
            "state.network_access.security_controller.privilege",
            hidden["security_privilege"],
        )
        self.assertEqual(
            "state.network_access.security_controller.foothold",
            hidden["security_foothold"],
        )

    def test_network_actions_replace_stale_result_telemetry(self) -> None:
        encoded = json.dumps(self.runtime["tools"]["intrude_network"]["effects"])
        self.assertIn('"field": "state.last_network", "value": {}', encoded)

        operate = json.dumps(
            self.runtime["tools"]["operate_remote_function"]["effects"]
        )
        self.assertIn('"field": "state.last_network", "value": {}', operate)
        self.assertIn('"field": "state.last_network.action", "value": "\'OPERATE\'"', operate)
        self.assertIn('"field": "state.last_network.node", "value": "node_id"', operate)
        self.assertIn(
            '"field": "state.last_network.remote_function", "value": "remote_function"',
            operate,
        )
        self.assertIn('"field": "state.last_network.success", "value": "false"', operate)
        self.assertIn('"field": "state.last_network.success", "value": "true"', operate)

        def walk(effects: list[dict[str, object]]) -> list[dict[str, object]]:
            found: list[dict[str, object]] = []
            for effect in effects:
                found.append(effect)
                for branch in ("on_true", "on_false"):
                    nested = effect.get(branch)
                    if isinstance(nested, list):
                        found.extend(walk(nested))
            return found

        revision_effects = [
            effect
            for effect in walk(self.runtime["tools"]["intrude_network"]["effects"])
            if "increment" in effect
            and isinstance(effect["increment"], dict)
            and effect["increment"].get("field") == "state.network.revision"
        ]
        self.assertEqual(1, len(revision_effects))
        self.assertEqual("_intrusion_total >= _network_difficulty", revision_effects[0]["condition"])

    def test_source_commit_must_exist_and_match_canonical_sources(self) -> None:
        with self.assertRaises((RuntimeError, ValueError)):
            build_draft(ROOT, source_commit="0000000000000000000000000000000000000000")
        with self.assertRaises((RuntimeError, ValueError)):
            build_draft(
                ROOT,
                source_commit="3947ac1018568203f3d59d145c9e6114095bc0b1",
            )

    def test_source_commit_rejects_authority_file_deleted_after_claimed_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Test"],
                check=True,
            )
            canonical = repository / "Character" / "OLD.md"
            canonical.parent.mkdir(parents=True)
            canonical.write_text("# Old authority\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "--quiet", "-m", "authority"],
                check=True,
            )
            claimed_commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            canonical.unlink()
            subprocess.run(["git", "-C", str(repository), "add", "-u"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "--quiet", "-m", "delete authority"],
                check=True,
            )

            with self.assertRaisesRegex(RuntimeError, "does not contain"):
                _verify_source_commit(repository, claimed_commit)

    def test_parameter_artifact_contains_only_authorized_manifest_values(self) -> None:
        artifact = json.loads(
            (
                self.draft
                / "files"
                / "provenance"
                / "parameter-manifest.json"
            ).read_text()
        )
        expected = default_manifest().seed_set()
        self.assertEqual(expected.hash, artifact["parameter_set_sha256"])
        self.assertEqual(expected.values, artifact["values"])
        self.assertEqual(
            set(expected.values),
            {
                item["parameter_id"]
                for item in artifact["manifest"]["parameters"]
            },
        )

    def test_coverage_accounts_for_every_specialty_node_and_open_gap(self) -> None:
        coverage = json.loads(
            (
                self.draft / "files" / "conformance" / "coverage.json"
            ).read_text()
        )
        self.assertEqual(26, coverage["specialty_summary"]["specialties"])
        self.assertEqual(364, coverage["specialty_summary"]["nodes"])
        self.assertEqual(364, len(coverage["specialty_nodes"]))
        self.assertEqual(
            360,
            coverage["specialty_summary"]["package_status_counts"][
                "test_subject_implemented"
            ],
        )
        self.assertNotIn(
            "gm_judgment",
            coverage["specialty_summary"]["package_status_counts"],
        )
        self.assertNotIn(
            "unresolved_design",
            coverage["specialty_summary"]["package_status_counts"],
        )
        self.assertNotIn(
            "structural_finding",
            coverage["specialty_summary"]["package_status_counts"],
        )
        self.assertEqual(
            0,
            sum(
                item["code"] == "UNIMPLEMENTABLE SPEC GAP"
                and item.get("subtype")
                == "SPECIALTY_OBJECTIVE_RUNTIME_INTEGRATION_GAP"
                for item in coverage["findings"]
            ),
        )
        self.assertNotIn(
            "test_subject_partial",
            coverage["specialty_summary"]["package_status_counts"],
        )
        self.assertEqual(364, coverage["specialty_summary"]["executable_counterfactuals"])
        self.assertEqual(0, coverage["specialty_summary"]["unexecutable_nodes"])
        self.assertEqual(0, coverage["specialty_summary"]["rejected_parameter_bindings"])
        self.assertEqual(0, len(coverage["rejected_parameter_bindings"]))
        self.assertEqual(0, coverage["specialty_summary"]["deferred_parameter_bindings"])
        self.assertEqual(0, len(coverage["deferred_parameter_bindings"]))
        self.assertNotIn(
            "MISSING_ENGINE_CAPABILITY",
            {item["code"] for item in coverage["findings"]},
        )
        self.assertNotIn(
            "HOST_UI_GAP",
            {item["code"] for item in coverage["findings"]},
        )
        statuses = {item["id"]: item["status"] for item in coverage["mechanics"]}
        self.assertEqual("implemented", statuses["entity-aware-path"])
        self.assertEqual("implemented", statuses["dynamic-spatial-state"])
        self.assertEqual("implemented", statuses["overwatch-suppression-triggering"])
        host_gap = next(
            item
            for item in coverage["findings"]
            if item["code"] == "SYLVIA_RAY_GRID_INTERSECTION_NONTERMINATION"
        )
        self.assertEqual("open_host_follow_up", host_gap["status"])
        self.assertIn("one micrometre", host_gap["package_mitigation"])
        foothold = next(
            item
            for item in coverage["specialty_nodes"]
            if item["id"] == "specialty.networker.tier_2.foothold"
        )
        self.assertEqual("authored_runtime_hook", foothold["package_status"])
        self.assertEqual(
            set(),
            {
                item["code"]
                for item in coverage["findings"]
                if item.get("node_id") == foothold["id"]
            },
        )

        specialty_tool = next(
            item
            for item in self.manifest["tools"]
            if item["name"] == "exercise_specialty_counterfactual"
        )
        self.assertEqual(
            364,
            len(
                specialty_tool["input_schema"]["properties"]["node_id"]["enum"]
            ),
        )
        self.assertEqual(
            364,
            len(self.runtime["definitions"]["specialty_counterfactuals"]),
        )
        fixture = json.loads(
            (
                self.draft
                / "files"
                / "conformance"
                / "specialty-counterfactuals.json"
            ).read_text()
        )
        self.assertEqual(364, fixture["node_count"])
        self.assertEqual(364, len(fixture["records"]))
        encoded_specialty = json.dumps(
            self.runtime["tools"]["exercise_specialty_counterfactual"],
            sort_keys=True,
        )
        self.assertIn("definitions.specialty_counterfactuals", encoded_specialty)
        self.assertIn("_specialty_rule.source", encoded_specialty)
        self.assertIn("benchmark_level == actor.level", encoded_specialty)
        self.assertIn("actor.class_id == _specialty_rule.class_id", encoded_specialty)
        self.assertIn("node_id in actor.specialties", encoded_specialty)
        self.assertIn("'ACTOR_MISSING_NODE'", encoded_specialty)
        self.assertNotIn("'OFF_ROLE_NODE_PRESENT'", encoded_specialty)
        self.assertIn("'CONTROL'", encoded_specialty)
        self.assertIn("'MISSING_PREREQUISITE'", encoded_specialty)
        actor_missing = [
            item
            for item in self.runtime["tools"]["exercise_specialty_counterfactual"]["effects"][0]["on_true"]
            if item.get("set", {}).get("field")
            == "state.last_specialty_test.status"
            and item.get("set", {}).get("value") == "'ACTOR_MISSING_NODE'"
        ]
        self.assertEqual(1, len(actor_missing))
        self.assertNotIn("role != 'OFF_ROLE'", actor_missing[0]["condition"])
        self.assertEqual(
            "HACKER",
            self.runtime["definitions"]["specialty_counterfactuals"][
                "specialty.networker.tier_2.foothold"
            ]["class_id"],
        )
        boundary_parameter = next(
            item
            for item in self.runtime["tools"][
                "exercise_specialty_counterfactual"
            ]["parameters"]
            if item["name"] == "boundary_mode"
        )
        self.assertFalse(boundary_parameter["required"])
        self.assertEqual("SATISFIED", boundary_parameter["default"])
        hasty = self.runtime["definitions"]["specialty_counterfactuals"][
            "specialty.snapshot.tier_4.hasty_full_solution"
        ]
        self.assertEqual(
            {"GUARD", "TERMINATION", "INVARIANT"},
            set(hasty["boundary_fixtures"]),
        )
        self.assertEqual(
            "TERMINATED", hasty["boundary_fixtures"]["TERMINATION"]["status"]
        )
        self.assertEqual(
            "BOUNDARY_NOT_AUTHORED",
            hasty["boundary_fixtures"]["GUARD"]["status"],
        )
        self.assertEqual(
            "BOUNDARY_NOT_AUTHORED",
            hasty["boundary_fixtures"]["INVARIANT"]["status"],
        )
        self.assertNotIn('"dice"', encoded_specialty)

    def test_player_network_surface_excludes_private_mechanical_fields(self) -> None:
        scenario = json.loads(
            (
                self.draft / "files" / "conformance" / "scenario.json"
            ).read_text()
        )
        state = scenario["initial_state"][f"{PACKAGE_ID}.state"]
        public_node_fields = {
            "label",
            "kind",
            "group_id",
            "status",
            "access",
            "control",
            "detection",
            "functions",
            "physical_binding",
            "visible",
        }
        for node in state["network"]["nodes"].values():
            self.assertLessEqual(set(node), public_node_fields)
        self.assertEqual(0, state["network_access"]["lobby_terminal"]["privilege"])

    def test_content_declaration_excludes_only_formal_specialty_gaps(self) -> None:
        declarations = json.loads(
            (
                self.draft
                / "files"
                / "content"
                / "content-declarations.json"
            ).read_text()
        )
        self.assertEqual("NOT_COMPLETE", declarations["scope"]["claim_status"])
        self.assertEqual(0, len(declarations["exclusions"]))
        self.assertEqual(
            declarations["exclusions"],
            sorted(declarations["exclusions"], key=lambda item: item["id"]),
        )
        self.assertTrue(
            all(
                item["reason"] == "unsupported"
                and item["classifications"] == ["unsupported"]
                and item["within_declared_scope"] is True
                for item in declarations["exclusions"]
            )
        )


if __name__ == "__main__":
    unittest.main()
